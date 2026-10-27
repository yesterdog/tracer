# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The run bundle of a simulated call (ADR 0027 rules 2, 3, 5, 6, 8, 14, 16; P4-21).

Pure: no clock, no I/O. The caller (the command) passes the facts of the call that the runner and
the recorder produced, the speech-to-text outcome and the optional agent export, and gets back a
bundle that passes ``check_run_bundle``. Rules enforced here, each pinned by a test:

* ``Run`` is ``simulated`` / ``simulated`` / ``T0``; ``started_at`` is the instant the connection
  was established (the simulator's wall clock) and absent for a call that was never established;
  no ``caller_external_id`` and no ``external_refs``;
* one stereo artifact with a declared channel map (caller 0, agent 1, method
  ``tracer_evalsim_stereo/1``); a call that was not established has neither;
* ``available_inputs`` by rule 8 (the F6 trap): ``agent_transcript`` only with an agent-produced
  transcript that has an agent-role turn, never because of the ``our_stt`` turns of role agent;
* the privacy markers are derived once, after the roles are known (ADR 0016 Decision 7a): ``false``
  with no consent and no retention when the role evidence is caller and agent only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, cast

from tracer_evalcore.definitions import _base, _sealed
from tracer_evalcore.pipeline import CostParts
from tracer_evalcore.simoutcome import SIM_OUTCOME_METADATA_KEY
from tracer_evalcore.simscore import SimulatedConfiguration, score_simulated_results
from tracer_evalcore.simtiming import TIMING_METADATA_KEY
from tracer_evalcore.stt import SttOutcome
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.agent import AgentTarget, AgentTargetRef
from tracer_evalschema.artifact import Artifact, AudioFormat, ChannelAssignment, ChannelMap
from tracer_evalschema.bundle import RunBundle, check_run_bundle
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import (
    AgentTargetId,
    ArtifactId,
    ConfigSnapshotId,
    ProjectId,
    RunBundleId,
    RunId,
    TranscriptId,
    TurnId,
)
from tracer_evalschema.metrics import INPUT_KINDS
from tracer_evalschema.privacy import (
    ConsentRecord,
    PrivacyMarker,
    RetentionPolicy,
    derive_human_subject,
)
from tracer_evalschema.project import Project
from tracer_evalschema.run import Run
from tracer_evalschema.transcript import Transcript, Turn
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "CHANNEL_MAP_METHOD",
    "TEXT_ORIGIN_KEY",
    "TURN_COMPLETE_KEY",
    "CallerLineFact",
    "ExportRead",
    "RecordingFacts",
    "SimulatedCall",
    "build_simulated_bundle",
    "provisional_privacy",
    "simulated_agent_target",
    "simulated_project",
]

CHANNEL_MAP_METHOD: Final = "tracer_evalsim_stereo/1"
TEXT_ORIGIN_KEY: Final = "tracer.caller_text_origin"
TURN_COMPLETE_KEY: Final = "tracer.caller_line_complete"
_AGENT_SOURCES: Final = ("agent_export", "agent_telemetry")


@dataclass(frozen=True)
class RecordingFacts:
    """The stereo file the command wrote: its ids and the facts the artifact records."""

    artifact_id: ArtifactId
    uri: str
    sha256: str
    size: int
    sample_rate_hz: int


@dataclass(frozen=True)
class CallerLineFact:
    """One caller line of which at least one frame was sent (times in ms on the artifact
    timeline)."""

    seq: int
    text: str
    language: str
    start_ms: int
    end_ms: int
    complete: bool


@dataclass(frozen=True)
class ExportRead:
    """The agent's own export (the reader's result), supplied by ``--agent-transcript``."""

    transcript: Transcript
    turns: tuple[Turn, ...]
    warnings: tuple[IngestWarning, ...] = ()


@dataclass(frozen=True)
class SimulatedCall:
    run_id: RunId
    established: bool
    started_at: datetime | None
    recording: RecordingFacts | None
    caller_lines: tuple[CallerLineFact, ...]
    caller_text_origin: str
    timing: Mapping[str, Any]
    outcome: Mapping[str, Any]
    usage: tuple[UsageRecord, ...]
    warnings: tuple[IngestWarning, ...] = ()


def provisional_privacy() -> PrivacyMarker:
    """The marker the readers and engines are handed before the roles are known; the builder
    replaces it by the derived one (a record written before the role evidence exists is ``true``
    with consent ``unknown``, ADR 0027 rule 14)."""
    return PrivacyMarker(
        human_subject=True,
        consent=ConsentRecord(status="unknown"),
        retention=RetentionPolicy(delete_on_expiry=False),
    )


def simulated_project(
    *, name: str, created_at: datetime, provider_configs: Sequence[Any] = ()
) -> Project:
    return Project(
        id=ProjectId.new(),
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        name=name,
        provider_configs=list(provider_configs),
    )


def simulated_agent_target(
    *, name: str, transport: str, url: str, created_at: datetime
) -> AgentTarget:
    document = _base(AgentTargetId.new(), created_at)
    document.update(
        {
            "name": name,
            "endpoint": {"transport": transport, "url": url},
            "languages": [],
            "declared_capabilities": [],
            "declared_stack": [],
        }
    )
    built: AgentTarget = _sealed(AgentTarget, document)
    return built


def _final_privacy(run: Run, turns: Sequence[Turn]) -> PrivacyMarker:
    if derive_human_subject(run, turns):
        return provisional_privacy()
    return PrivacyMarker(human_subject=False)


def _available_inputs(
    recording: RecordingFacts | None,
    stt: SttOutcome | None,
    export: ExportRead | None,
    turns: Sequence[Turn],
) -> list[str]:
    held: set[str] = set()
    if recording is not None:
        held |= {"audio", "agent_audio"}
    if stt is not None and stt.transcript is not None:
        held.add("transcript")
    if (
        export is not None
        and export.transcript.source in _AGENT_SOURCES
        and any(t.role == "agent" for t in export.turns)
    ):
        held.add("agent_transcript")
    if any(t.start_ms is not None and t.end_ms is not None for t in turns):
        held.add("turn_timing")
    return [kind for kind in INPUT_KINDS if kind in held]


def build_simulated_bundle(
    *,
    configuration: SimulatedConfiguration,
    project: Project,
    agent_target: AgentTarget,
    call: SimulatedCall,
    stt: SttOutcome | None,
    export: ExportRead | None,
    stt_warnings: Sequence[IngestWarning] = (),
    costs: CostParts | None,
    created_at: datetime,
) -> RunBundle:
    """The bundle of one simulated call; ``check_run_bundle`` has passed when it returns."""
    snapshot = configuration.snapshot
    recording = call.recording
    if call.established != (recording is not None):
        raise ValueError("an established call has a recording and a failed one has none")
    script_id = TranscriptId.new()
    script_turns: list[Turn] = []
    for line in call.caller_lines:
        script_turns.append(
            Turn.model_validate(
                {
                    "id": TurnId.new(),
                    "schema_version": SCHEMA_VERSION,
                    "created_at": created_at,
                    "metadata": {} if line.complete else {TURN_COMPLETE_KEY: False},
                    "transcript_id": script_id,
                    "seq": line.seq,
                    "role": "caller",
                    "source": "script",
                    "text": line.text,
                    "language": line.language,
                    "start_ms": line.start_ms,
                    "end_ms": line.end_ms,
                    "timing_source": "audio_alignment",
                }
            )
        )
    stt_turns: tuple[Turn, ...] = ()
    stt_transcript: Transcript | None = None
    if stt is not None and stt.transcript is not None:
        stt_transcript, stt_turns = stt.transcript, stt.turns
    export_transcript = None if export is None else export.transcript
    export_turns: tuple[Turn, ...] = () if export is None else export.turns
    all_turns = [*script_turns, *stt_turns, *export_turns]

    available = _available_inputs(recording, stt, export, all_turns)
    channel_maps: list[ChannelMap] = []
    if recording is not None:
        channel_maps.append(
            ChannelMap(
                artifact_id=recording.artifact_id,
                channels=[
                    ChannelAssignment(index=0, role="caller"),
                    ChannelAssignment(index=1, role="agent"),
                ],
                determination="declared",
                method=CHANNEL_MAP_METHOD,
            )
        )
    warnings = [*call.warnings, *stt_warnings]
    if stt is not None:
        warnings.extend(stt.warnings)
    if export is not None:
        warnings.extend(export.warnings)
    metadata: dict[str, Any] = {
        SIM_OUTCOME_METADATA_KEY: dict(call.outcome),
        TIMING_METADATA_KEY: dict(call.timing),
    }
    run = Run(
        id=call.run_id,
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        metadata=metadata,
        project_id=cast(ProjectId, project.id),
        agent_target=AgentTargetRef(
            id=cast(AgentTargetId, agent_target.id),
            version=agent_target.version,
            content_digest=agent_target.content_digest,
        ),
        config_snapshot_id=cast(ConfigSnapshotId, snapshot.id),
        mode="simulated",
        caller_origin="simulated",
        started_at=call.started_at,
        channel_maps=channel_maps,
        available_inputs=available,
        observed_tier="T0",
        ingest_warnings=warnings,
    )
    privacy = _final_privacy(run, all_turns)

    transcripts: list[Transcript] = []
    if script_turns:
        if recording is None:
            raise ValueError("caller lines need the recording they were sent on")
        transcripts.append(
            Transcript.model_validate(
                {
                    "id": script_id,
                    "schema_version": SCHEMA_VERSION,
                    "created_at": created_at,
                    "metadata": {TEXT_ORIGIN_KEY: call.caller_text_origin},
                    "source": "script",
                    "timeline_origin": {"kind": "artifact", "ref": recording.artifact_id},
                    "privacy": privacy,
                }
            )
        )
    for transcript in (stt_transcript, export_transcript):
        if transcript is not None:
            transcripts.append(transcript.model_copy(update={"privacy": privacy}))
    artifacts: list[Artifact] = []
    if recording is not None:
        artifacts.append(
            Artifact(
                id=recording.artifact_id,
                schema_version=SCHEMA_VERSION,
                created_at=created_at,
                uri=recording.uri,
                sha256=recording.sha256,
                size=recording.size,
                audio_format=AudioFormat(
                    container="wav",
                    codec="pcm_s16le",
                    sample_rate_hz=recording.sample_rate_hz,
                    channels=2,
                    bits_per_sample=16,
                ),
                privacy=privacy,
            )
        )
    held_export = next((t for t in transcripts if t.source in _AGENT_SOURCES), None)
    held_stt = next((t for t in transcripts if t.source == "our_stt"), None)
    results = score_simulated_results(
        configuration=configuration,
        run=run,
        timing=call.timing,
        export=held_export,
        export_turns=export_turns,
        stt=held_stt,
        stt_turns=stt_turns,
        transcripts=transcripts,
        audio_artifact_id=None if recording is None else recording.artifact_id,
        created_at=created_at,
    )
    usage = list(call.usage)
    bundle = RunBundle(
        id=RunBundleId.new(),
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        run=run,
        snapshot=snapshot,
        test_profiles=[] if configuration.test_profile is None else [configuration.test_profile],
        artifacts=artifacts,
        transcripts=transcripts,
        turns=all_turns,
        results=list(results),
        usage_records=usage,
        pricing_catalogs=[] if costs is None else list(costs.catalogs),
        cost_records=[] if costs is None else list(costs.records),
        cost_summaries=[] if costs is None else list(costs.summaries),
    )
    check_run_bundle(bundle)
    return bundle
