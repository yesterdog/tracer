# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The pipeline of a recorded call: read, transcribe, score and build the run bundle (P3-20).

Everything that varies is injected: the engine, the instant that stamps every record, the scorer
registry and the cost builder. The module reads no clock, no environment and no file, and prints
and logs nothing. Ids are fresh UUIDv7 values per call, so two runs of one call agree only after
their ids are masked (ADR 0023 rule 18). A failed transcription is the one engine failure that is
recorded; every other failure stops the run before a bundle exists.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from tracer_evalcore.audio.chunker import DEFAULT_MAX_CHUNK_MS, DEFAULT_SEARCH_MS, chunk_wav
from tracer_evalcore.definitions import RecordedConfiguration, parameters_of
from tracer_evalcore.importers.voicera import read_voicera_export
from tracer_evalcore.importers.wav import WavAudio
from tracer_evalcore.registry import DEFAULT_REGISTRY, ScorerRegistry, check_runnable
from tracer_evalcore.scoring import score_results
from tracer_evalcore.stt import SttContext, SttEngine, SttError
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.agent import AgentTarget, AgentTargetRef
from tracer_evalschema.bundle import RunBundle, check_run_bundle
from tracer_evalschema.cost import CostRecord, CostSummary
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import (
    AgentTargetId,
    ArtifactId,
    ConfigSnapshotId,
    ProjectId,
    RunBundleId,
    RunId,
)
from tracer_evalschema.pricing import PricingCatalog
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy
from tracer_evalschema.project import Project
from tracer_evalschema.run import Run
from tracer_evalschema.transcript import LanguageDeclaration, Transcript, Turn
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "EXIT_NOT_SCORED",
    "EXIT_SCORED",
    "EXIT_STT_FAILED",
    "CostBuilder",
    "CostParts",
    "PipelineResult",
    "score_recorded_call",
]

EXIT_SCORED: Final = 0
EXIT_NOT_SCORED: Final = 2
EXIT_STT_FAILED: Final = 3

_CALLER_ORIGINS = ("human", "simulated")


@dataclass(frozen=True)
class CostParts:
    """The cost records of a bundle, built by the caller from the usage the engine reported."""

    catalogs: tuple[PricingCatalog, ...]
    records: tuple[CostRecord, ...]
    summaries: tuple[CostSummary, ...]


CostBuilder = Callable[[Sequence[UsageRecord]], CostParts]


@dataclass(frozen=True)
class PipelineResult:
    bundle: RunBundle
    outcome: str
    stt_failure: SttError | None

    @property
    def exit_code(self) -> int:
        if self.outcome == "scored":
            return EXIT_SCORED
        if self.outcome == "stt_failed":
            return EXIT_STT_FAILED
        return EXIT_NOT_SCORED


def score_recorded_call(
    *,
    audio: WavAudio | None,
    export_text: str | None,
    engine: SttEngine | None,
    configuration: RecordedConfiguration,
    project: Project,
    agent_target: AgentTarget,
    configured_language: str,
    export_declared_language: str | None,
    caller_origin: str,
    consent: ConsentRecord,
    retention: RetentionPolicy,
    created_at: datetime,
    registry: ScorerRegistry = DEFAULT_REGISTRY,
    max_chunk_ms: int = DEFAULT_MAX_CHUNK_MS,
    search_ms: int = DEFAULT_SEARCH_MS,
    accept_provider_downmix: bool = False,
    accept_export_warnings: bool = False,
    cost_builder: CostBuilder | None = None,
) -> PipelineResult:
    """Score one recorded call. The steps run in the order that stops a bad run before any send."""
    if caller_origin not in _CALLER_ORIGINS:
        raise ValueError("caller_origin must be human or simulated")
    snapshot = configuration.snapshot
    if configured_language != snapshot.evaluator.language:
        raise ValueError("configured_language is not the language the configuration was built for")
    if audio is not None and engine is None:
        raise ValueError("an engine is required when there is audio")
    parameters_of(snapshot)
    check_runnable(snapshot.metric_definitions, registry)

    privacy = PrivacyMarker(human_subject=True, consent=consent, retention=retention)
    export_transcript: Transcript | None = None
    export_turns: tuple[Turn, ...] = ()
    warnings: list[IngestWarning] = []
    if export_text is not None:
        declaration = (
            None
            if export_declared_language is None
            else LanguageDeclaration(
                language=export_declared_language, source="user_declared", declared_at=created_at
            )
        )
        read = read_voicera_export(
            export_text,
            created_at=created_at,
            privacy=privacy,
            declared_language=declaration,
            accept_warnings=accept_export_warnings,
        )
        export_transcript, export_turns = read.transcript, read.turns
        warnings.extend(read.warnings)

    plan = (
        None
        if audio is None
        else chunk_wav(
            audio,
            max_chunk_ms=max_chunk_ms,
            search_ms=search_ms,
            accept_provider_downmix=accept_provider_downmix,
        )
    )

    run_id = RunId.new()
    stt_transcript: Transcript | None = None
    stt_turns: tuple[Turn, ...] = ()
    usage: tuple[UsageRecord, ...] = ()
    failure: SttError | None = None
    if audio is not None and plan is not None and engine is not None:
        outcome = engine.transcribe(
            plan,
            SttContext(
                run_id=run_id,
                audio_artifact_id=cast(ArtifactId, audio.artifact.id),
                created_at=created_at,
                privacy=privacy,
                configured_language=configured_language,
            ),
        )
        usage = outcome.usage
        if outcome.failure is not None:
            failure = outcome.failure
            warnings.append(
                IngestWarning(
                    code="stt_failed",
                    path="/stt",
                    message=str(failure) or type(failure).__name__,
                )
            )
        else:
            stt_transcript, stt_turns = outcome.transcript, outcome.turns
            warnings.extend(outcome.warnings)

    available = []
    if audio is not None:
        available.append("audio")
    if stt_transcript is not None:
        available.append("transcript")
    if any(turn.role == "agent" for turn in export_turns):
        available.append("agent_transcript")
    run = Run(
        id=run_id,
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        project_id=cast(ProjectId, project.id),
        agent_target=AgentTargetRef(
            id=cast(AgentTargetId, agent_target.id),
            version=agent_target.version,
            content_digest=agent_target.content_digest,
        ),
        config_snapshot_id=cast(ConfigSnapshotId, snapshot.id),
        mode="recorded",
        caller_origin=caller_origin,
        available_inputs=available,
        observed_tier="T0",
        ingest_warnings=warnings,
    )
    results = score_results(
        configuration=configuration,
        run=run,
        export=export_transcript,
        export_turns=export_turns,
        stt=stt_transcript,
        stt_turns=stt_turns,
        audio_artifact_id=None if audio is None else cast(ArtifactId, audio.artifact.id),
        created_at=created_at,
        registry=registry,
    )
    costs = cost_builder(usage) if cost_builder is not None and audio is not None else None
    bundle = RunBundle(
        id=RunBundleId.new(),
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        run=run,
        snapshot=snapshot,
        artifacts=[] if audio is None else [audio.artifact],
        transcripts=[t for t in (export_transcript, stt_transcript) if t is not None],
        turns=[*export_turns, *stt_turns],
        results=list(results),
        usage_records=list(usage),
        pricing_catalogs=[] if costs is None else list(costs.catalogs),
        cost_records=[] if costs is None else list(costs.records),
        cost_summaries=[] if costs is None else list(costs.summaries),
    )
    check_run_bundle(bundle)
    if failure is not None:
        label = "stt_failed"
    elif all(result.status == "scored" for result in results):
        label = "scored"
    else:
        label = "not_scored"
    return PipelineResult(bundle=bundle, outcome=label, stt_failure=failure)
