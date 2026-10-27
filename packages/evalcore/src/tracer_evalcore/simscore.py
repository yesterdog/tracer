# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Scoring of a simulated call: the configuration, the definitions and the results (P4-22).

Three families, none with a pass rule, all shipped as templates (ADR 0027 rule 11, ADR 0023
amendment A2):

* the timing definitions (kind ``latency``, unit ``ms``, ``requires`` ``audio`` and
  ``turn_timing``, language independent) and ``sim_answered_caller_turns`` (kind ``deterministic``,
  unit ``count``); a value whose edges are missing is an ``error`` result with the code
  ``timing_edge_missing``, never 0 and never a valid ``not_applicable`` (BL-292);
* the three agreement definitions of the per-role partition: the agent-role turns of the agent
  export against the agent-channel ``our_stt`` turns (a new scorer version);
* the inert expected-outcome check: required by schema 0.0.1, never run, no verdict (ADR 0027 rule
  12).

Pure: no clock, no I/O, no log. Every record is stamped with the ``created_at`` it is given.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, cast

import tracer_evalcore
from tracer_evalcore.definitions import (
    PARAMETER_NAMES,
    ScoringParameters,
    _base,
    _ref,
    _sealed,
    check_binding_parameters,
)
from tracer_evalcore.flagsdoc import FLAGS_METADATA_KEY
from tracer_evalcore.judgerecords import (
    JUDGE_KEY_PREFIX,
    JUDGE_PROFILE_METADATA_KEY,
    JudgeSetup,
    judge_binding_params,
    judge_definitions,
)
from tracer_evalcore.registry import (
    COUNT_KEY,
    MER_KEY,
    SHARE_KEY,
    ScorerEntry,
    ScorerRegistry,
    implementation_ref,
    scorer_info,
)
from tracer_evalcore.scoring import _analyse, _Analysis, _Failure
from tracer_evalcore.simspec import SimEvaluatorSpec, SimPersonaSpec, SimTestProfileSpec
from tracer_evalcore.simtiming import TIMING_EDGE_MISSING, TIMING_METADATA_KEY, timing_value
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.base import compute_metadata_flags
from tracer_evalschema.evaluator import Evaluator
from tracer_evalschema.ids import (
    ArtifactId,
    ConfigSnapshotId,
    EvaluatorId,
    MetricBindingId,
    MetricDefinitionId,
    MetricResultId,
    PersonaId,
    RubricId,
    TestProfileId,
)
from tracer_evalschema.metrics import MetricBinding, MetricDefinition, MetricDefinitionRef
from tracer_evalschema.persona import Persona
from tracer_evalschema.profile import TestProfile
from tracer_evalschema.results import MetricResult, ResultError, applicability
from tracer_evalschema.rubric import Rubric
from tracer_evalschema.run import Run
from tracer_evalschema.snapshot import ConfigSnapshot, compute_snapshot_digest
from tracer_evalschema.transcript import Transcript, Turn

__all__ = [
    "PARTITION_VERSION_SUFFIX",
    "SIM_AGREEMENT_KEYS",
    "SIM_BARGE_IN_KEY",
    "SIM_EVALUATOR_NAME",
    "SIM_PLACEHOLDER_KEY",
    "SIM_REGISTRY",
    "SIM_SHIPPED_KEYS",
    "SIM_TIMING_KEYS",
    "SimulatedConfiguration",
    "build_simulated_configuration",
    "score_simulated_results",
    "sim_definition",
]

SIM_TIMING_KEYS: Final = (
    "sim_first_agent_audio_ms",
    "sim_agent_response_gap_median_ms",
    "sim_overlap_ms",
    "sim_barge_in_stop_ms",
    "sim_answered_caller_turns",
)
SIM_BARGE_IN_KEY: Final = "sim_barge_in_stop_ms"
SIM_AGREEMENT_KEYS: Final = (
    "stt_export_agent_agreement_mer",
    "stt_export_agent_flag_count",
    "stt_export_agent_flagged_token_share",
)
SIM_SHIPPED_KEYS: Final = (*SIM_TIMING_KEYS, *SIM_AGREEMENT_KEYS)
SIM_PLACEHOLDER_KEY: Final = "simulated_call_no_outcome_check"
SIM_EVALUATOR_NAME: Final = "simulated call evaluation"
PARTITION_VERSION_SUFFIX: Final = "+agent-partition.1"
_PARTITION_OF: Final = dict(zip(SIM_AGREEMENT_KEYS, (MER_KEY, COUNT_KEY, SHARE_KEY), strict=True))
_AGREEMENT_UNITS: Final = {
    SIM_AGREEMENT_KEYS[0]: "ratio",
    SIM_AGREEMENT_KEYS[1]: "count",
    SIM_AGREEMENT_KEYS[2]: "ratio",
}
_PLACEHOLDER_PROMPT: Final = (
    "Placeholder required by schema 0.0.1. No judge is run for a simulated call and no outcome "
    "is evaluated. Expected outcome: {{expected_outcome}}"
)

SIM_REGISTRY: Final = ScorerRegistry(
    [
        *(
            ScorerEntry(key, implementation_ref(key), tracer_evalcore.__version__)
            for key in SIM_TIMING_KEYS
        ),
        *(
            ScorerEntry(
                key, implementation_ref(key), tracer_evalcore.__version__ + PARTITION_VERSION_SUFFIX
            )
            for key in SIM_AGREEMENT_KEYS
        ),
    ]
)


def sim_definition(key: str, *, created_at: datetime, flag_params: bool = True) -> MetricDefinition:
    """A fresh version-1 definition of one shipped simulated-run metric, with no pass rule."""
    if key not in SIM_SHIPPED_KEYS:
        raise ValueError("not a shipped simulated-run metric key")
    document = _base(MetricDefinitionId.new(), created_at)
    document.update(
        {
            "key": key,
            "aggregation": {"kind": "per_conversation"},
            "pass_rule": None,
            "script_sensitive": False,
            "implementation_ref": implementation_ref(key),
            "value_type": "numeric",
        }
    )
    if key in SIM_TIMING_KEYS:
        counts = key == "sim_answered_caller_turns"
        document.update(
            {
                "kind": "deterministic" if counts else "latency",
                "unit": "count" if counts else "ms",
                "requires": ["audio", "turn_timing"],
                "languages": "language_independent",
                "parameters": [],
            }
        )
    else:
        document.update(
            {
                "kind": "deterministic",
                "unit": _AGREEMENT_UNITS[key],
                "requires": ["transcript", "agent_transcript"],
                "languages": ["mr"],
                "parameters": [
                    {"name": "flag_min_confidence", "value_type": "number"},
                    {"name": "normaliser", "value_type": "string"},
                ],
            }
        )
    built: MetricDefinition = _sealed(MetricDefinition, document)
    return built


def _inert_check(created_at: datetime) -> MetricDefinition:
    document = _base(MetricDefinitionId.new(), created_at)
    document.update(
        {
            "key": SIM_PLACEHOLDER_KEY,
            "kind": "llm_judge",
            "value_type": "boolean",
            "requires": ["transcript"],
            "languages": "language_independent",
            "script_sensitive": False,
            "aggregation": {"kind": "per_conversation"},
            "pass_rule": {"kind": "equals_true"},
            "parameters": [],
            "prompt_template": _PLACEHOLDER_PROMPT,
            "prompt_variables": ["expected_outcome"],
            "judge_model_ref": {"provider": "none", "model": "not_run"},
        }
    )
    built: MetricDefinition = _sealed(MetricDefinition, document)
    return built


@dataclass(frozen=True)
class SimulatedConfiguration:
    """A sealed snapshot of a simulated-run configuration, and the test profile it refers to."""

    snapshot: ConfigSnapshot
    test_profile: TestProfile | None
    parameters: ScoringParameters
    judge: JudgeSetup | None = None

    def definition(self, key: str) -> MetricDefinition:
        for definition in self.snapshot.metric_definitions:
            if definition.key == key:
                return definition
        raise KeyError(key)

    def metric_keys(self) -> tuple[str, ...]:
        """The shipped simulated-run metrics: not the inert check and not a judge definition."""
        return tuple(
            d.key
            for d in self.snapshot.metric_definitions
            if d.key != SIM_PLACEHOLDER_KEY and not d.key.startswith(JUDGE_KEY_PREFIX)
        )

    def judge_keys(self) -> tuple[str, ...]:
        """The keys of the judge definitions (one per golden item), in golden order."""
        return tuple(
            d.key for d in self.snapshot.metric_definitions if d.key.startswith(JUDGE_KEY_PREFIX)
        )


def build_simulated_configuration(
    *,
    evaluator: SimEvaluatorSpec,
    persona: SimPersonaSpec,
    test_profile: SimTestProfileSpec | None,
    sim_profile: Mapping[str, Any],
    flag_min_confidence: int | float,
    normaliser: str,
    created_at: datetime,
    judge: JudgeSetup | None = None,
) -> SimulatedConfiguration:
    """Build and seal the snapshot; ``sim_profile`` is stored in its metadata (digest-covered).

    With a ``judge`` setup (ADR 0030, ADR 0027 rule 12a) the snapshot also embeds one optional
    ``llm_judge`` definition and binding per golden item and the ``tracer.judge_profile/1``
    document; the inert check stays the only required rubric item."""
    from tracer_evalcore.simprofile import SIM_PROFILE_METADATA_KEY

    params = {PARAMETER_NAMES[0]: flag_min_confidence, PARAMETER_NAMES[1]: normaliser}
    parameters = check_binding_parameters(dict.fromkeys((MER_KEY, COUNT_KEY, SHARE_KEY), params))
    keys = [k for k in SIM_TIMING_KEYS if k != SIM_BARGE_IN_KEY or sim_profile["barge_ins"]]
    keys += list(SIM_AGREEMENT_KEYS)
    definitions = [sim_definition(k, created_at=created_at) for k in keys]
    inert = _inert_check(created_at)
    definitions.append(inert)
    judge_defs = [] if judge is None else judge_definitions(judge, created_at=created_at)
    judge_params = (
        {}
        if judge is None
        else dict(
            zip(
                (d.key for d in judge_defs),
                (judge_binding_params(i) for i in judge.golden.items),
                strict=True,
            )
        )
    )
    shown = [*definitions, *judge_defs]

    persona_record: Persona = _sealed(
        Persona,
        {
            **_base(PersonaId.new(), created_at),
            "language": persona.language,
            **({} if persona.accent is None else {"accent": persona.accent}),
        },
    )
    profile_record: TestProfile | None = None
    if test_profile is not None:
        profile_record = _sealed(
            TestProfile,
            {
                **_base(TestProfileId.new(), created_at),
                "name": test_profile.name,
                "dob": test_profile.dob,
                "account_number": test_profile.account_number,
            },
        )
    evaluator_id = EvaluatorId.new()
    bindings: list[MetricBinding] = []
    for definition in shown:
        document = _base(MetricBindingId.new(), created_at)
        bound_params = judge_params.get(definition.key)
        if bound_params is None:
            bound_params = dict(params) if definition.key in SIM_AGREEMENT_KEYS else {}
        document.update(
            {
                "evaluator_id": str(evaluator_id),
                "metric": _ref(definition),
                "params": bound_params,
                "enabled": True,
            }
        )
        bindings.append(_sealed(MetricBinding, document))
    binding_of = {b.metric.id: b for b in bindings}

    evaluator_document = _base(evaluator_id, created_at)
    evaluator_document.update(
        {
            "name": evaluator.name,
            "language": evaluator.language,
            "instructions": evaluator.instructions,
            "expected_outcome": evaluator.expected_outcome,
            "persona": _ref(persona_record),
            "metric_bindings": [_ref(binding) for binding in bindings],
            "scripted_turns": [
                {"index": t.index, "text": t.text, "language": t.language}
                for t in evaluator.scripted_turns
            ],
            "expected_tool_calls": [],
            "max_duration_ms": evaluator.max_duration_ms,
        }
    )
    if profile_record is not None:
        evaluator_document["test_profile"] = _ref(profile_record)
    evaluator_record: Evaluator = _sealed(Evaluator, evaluator_document)

    items = [{"binding": _ref(binding_of[cast(MetricDefinitionId, inert.id)]), "required": True}]
    for definition in [*definitions[:-1], *judge_defs]:
        items.append(
            {
                "binding": _ref(binding_of[cast(MetricDefinitionId, definition.id)]),
                "required": False,
            }
        )
    rubric: Rubric = _sealed(Rubric, {**_base(RubricId.new(), created_at), "items": items})

    document = _base(ConfigSnapshotId.new(), created_at, version=None)
    document["metadata"] = {SIM_PROFILE_METADATA_KEY: dict(sim_profile)}
    if judge is not None:
        document["metadata"][JUDGE_PROFILE_METADATA_KEY] = dict(judge.profile_document)
    document["metadata_flags"] = [
        flag.model_dump() for flag in compute_metadata_flags(document["metadata"])
    ]
    document.update(
        {
            "evaluator": evaluator_record.model_dump(mode="json", by_alias=True),
            "persona": persona_record.model_dump(mode="json", by_alias=True),
            "metric_definitions": [d.model_dump(mode="json", by_alias=True) for d in shown],
            "metric_bindings": [b.model_dump(mode="json", by_alias=True) for b in bindings],
            "rubric": rubric.model_dump(mode="json", by_alias=True),
        }
    )
    digest = compute_snapshot_digest(document).model_dump(mode="json", exclude_none=True)
    snapshot = ConfigSnapshot.model_validate({**document, "content_digest": digest})
    return SimulatedConfiguration(snapshot, profile_record, parameters, judge)


# ------------------------------------------------------------------------------------- scoring


def _common(
    definition: MetricDefinition, transcript_ids: list[Any] | None, created_at: datetime
) -> dict[str, Any]:
    return {
        "id": MetricResultId.new(),
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "metric": MetricDefinitionRef(
            id=cast(MetricDefinitionId, definition.id),
            version=definition.version,
            content_digest=definition.content_digest,
        ),
        "transcript_ids": transcript_ids,
    }


def score_simulated_results(
    *,
    configuration: SimulatedConfiguration,
    run: Run,
    timing: Mapping[str, Any] | None,
    export: Transcript | None,
    export_turns: Sequence[Turn],
    stt: Transcript | None,
    stt_turns: Sequence[Turn],
    transcripts: Sequence[Transcript],
    audio_artifact_id: ArtifactId | None,
    created_at: datetime,
    registry: ScorerRegistry = SIM_REGISTRY,
) -> tuple[MetricResult, ...]:
    """One result for every shipped definition of the snapshot, in the order of ``metric_keys()``.

    ``transcripts`` are all the transcripts of the run (the applicability re-run of the bundle
    reads the agent turns of the export and of the agent-channel speech-to-text together).
    """
    parameters = configuration.parameters
    held_ids = {t.id for t in transcripts}
    all_turns = [t for t in (*export_turns, *stt_turns) if t.transcript_id in held_ids]
    agent_turns = [t for t in all_turns if t.role == "agent"]
    agreement: _Analysis | _Failure | None = None
    results: list[MetricResult] = []
    for key in configuration.metric_keys():
        definition = configuration.definition(key)
        if key in SIM_TIMING_KEYS:
            results.append(
                _timing_result(definition, key, run, timing, transcripts, created_at, registry)
            )
            continue
        read = [
            t for t in transcripts if t.source in ("agent_export", "agent_telemetry", "our_stt")
        ]
        answer = applicability(definition, run, turns=agent_turns, transcripts=read)
        ids = [t.id for t in read] or None
        common = _common(definition, ids, created_at)
        if answer.na_reason is not None:
            results.append(
                MetricResult(
                    **common,
                    status="not_applicable",
                    value=None,
                    na_reason=answer.na_reason,
                    missing_kinds=list(answer.missing_kinds),
                    language_source=answer.language_source,
                    declared_language_transcript_ids=list(answer.declared_language_transcript_ids),
                )
            )
            continue
        if export is None or stt is None:
            raise ValueError("the run lists inputs that were not supplied")
        provenance: dict[str, Any] = {
            "scorer": scorer_info(registry.get(definition.implementation_ref or "")),
            "language_source": answer.language_source,
            "declared_language_transcript_ids": list(answer.declared_language_transcript_ids),
        }
        if agreement is None:
            agreement = _analyse(
                parameters=parameters,
                export_turns=[t for t in export_turns if t.role == "agent"],
                stt=stt,
                stt_turns=[t for t in stt_turns if t.role == "agent"],
                audio_artifact_id=audio_artifact_id,
            )
        if isinstance(agreement, _Failure):
            results.append(
                MetricResult(
                    **common,
                    status="error",
                    value=None,
                    error=ResultError(code=agreement.code, message=agreement.message),
                    **provenance,
                )
            )
            continue
        results.append(
            MetricResult(
                **common,
                status="scored",
                value=agreement.values[_PARTITION_OF[key]],
                passed=None,
                evidence_refs=list(agreement.evidence),
                metadata={FLAGS_METADATA_KEY: agreement.document},
                **provenance,
            )
        )
    return tuple(results)


def _timing_result(
    definition: MetricDefinition,
    key: str,
    run: Run,
    timing: Mapping[str, Any] | None,
    transcripts: Sequence[Transcript],
    created_at: datetime,
    registry: ScorerRegistry,
) -> MetricResult:
    answer = applicability(definition, run, turns=(), transcripts=())
    common = _common(definition, None, created_at)
    if answer.na_reason is not None:
        return MetricResult(
            **common,
            status="not_applicable",
            value=None,
            na_reason=answer.na_reason,
            missing_kinds=list(answer.missing_kinds),
            language_source=answer.language_source,
            declared_language_transcript_ids=list(answer.declared_language_transcript_ids),
        )
    if timing is None:
        raise ValueError("the run lists timing inputs but holds no timing document")
    provenance: dict[str, Any] = {
        "scorer": scorer_info(registry.get(definition.implementation_ref or "")),
        "language_source": answer.language_source,
        "declared_language_transcript_ids": list(answer.declared_language_transcript_ids),
    }
    found = timing_value(timing, key)
    if found.value is None:
        return MetricResult(
            **common,
            status="error",
            value=None,
            error=ResultError(code=TIMING_EDGE_MISSING, message=found.message or ""),
            metadata={TIMING_METADATA_KEY: dict(timing)},
            **provenance,
        )
    return MetricResult(
        **common,
        status="scored",
        value=found.value,
        passed=None,
        metadata={TIMING_METADATA_KEY: dict(timing)},
        **provenance,
    )
