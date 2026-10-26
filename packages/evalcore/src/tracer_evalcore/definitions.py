# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Metric definition templates and the configuration of a recorded run (ADR 0023 rules 3, 4, 17).

A template is a function, not a stored record: the stored definitions are the version-1 records a
configuration builds from the templates and the rubric file. Records are built as JSON-mode
documents, sealed with their content digest and validated by the schema models, so this module
imports no validation library of its own. The review budget and the run parameters come from the
rubric file only; no value here is a default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.flags import ThresholdError, parse_threshold
from tracer_evalcore.normalise import SUPPORTED_NORMALISERS
from tracer_evalcore.registry import (
    COUNT_KEY,
    DEFAULT_REGISTRY,
    MER_KEY,
    SHARE_KEY,
    SHIPPED_KEYS,
    ScorerRegistry,
    check_runnable,
    implementation_ref,
)
from tracer_evalcore.rubricfile import RubricSpec, check_spec
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.agent import AgentTarget
from tracer_evalschema.digest import compute_digest
from tracer_evalschema.evaluator import MAX_DURATION_MS, Evaluator, evaluator_digest_domain
from tracer_evalschema.ids import (
    AgentTargetId,
    ConfigSnapshotId,
    EvaluatorId,
    MetricBindingId,
    MetricDefinitionId,
    PersonaId,
    ProjectId,
    RubricId,
    TypedId,
)
from tracer_evalschema.metrics import MetricBinding, MetricDefinition
from tracer_evalschema.persona import Persona
from tracer_evalschema.project import Project
from tracer_evalschema.provider import ProviderConfigRef
from tracer_evalschema.rubric import Rubric
from tracer_evalschema.snapshot import ConfigSnapshot, compute_snapshot_digest

__all__ = [
    "COUNT_KEY",
    "EVALUATOR_NAME",
    "MER_KEY",
    "PARAMETER_NAMES",
    "PLACEHOLDER_KEY",
    "RECORDED_ENDPOINT_URL",
    "SHARE_KEY",
    "SHIPPED_KEYS",
    "BindingParameterError",
    "RecordedConfiguration",
    "ScoringParameters",
    "build_configuration",
    "check_binding_parameters",
    "parameters_of",
    "recorded_agent_target",
    "recorded_project",
    "shipped_definition",
    "shipped_definitions",
]

PARAMETER_NAMES = ("flag_min_confidence", "normaliser")
PLACEHOLDER_KEY = "recorded_call_no_outcome_check"
EVALUATOR_NAME = "recorded-call agreement scoring"
RECORDED_ENDPOINT_URL = "wss://recorded-call.invalid/"

_UNITS = {MER_KEY: "ratio", COUNT_KEY: "count", SHARE_KEY: "ratio"}
_REQUIRES = ("transcript", "agent_transcript")

_INSTRUCTIONS = (
    "This run scores a recorded call. No call was simulated and no caller was simulated: the "
    "recording and the agent's own transcript export of the same call already exist. The run "
    "measures the agreement between the agent export and our speech-to-text transcript, and "
    "flags the spans of the export where the two disagree, so that a person reads less. "
    "Neither text is treated as the reference. The maximum duration of this record is the "
    "largest the schema allows; it is not the length of the call."
)
_EXPECTED_OUTCOME = (
    "No outcome is evaluated in this run; the recording is scored by agreement only."
)
_PLACEHOLDER_PROMPT = (
    "Placeholder required by schema 0.0.1. No judge is run for a recorded call and no outcome "
    "is evaluated. Expected outcome: {{expected_outcome}}"
)


class BindingParameterError(InputRefusedError):
    """A binding parameter is missing, invalid, unequal across bindings or names no normaliser.

    The message holds the code, the metric key and the parameter name, never a value.
    """

    def __init__(self, code: str, key: str | None = None, parameter: str | None = None) -> None:
        self.code = code
        self.key = key
        self.parameter = parameter
        super().__init__(" ".join(part for part in (code, key, parameter) if part is not None))


@dataclass(frozen=True)
class ScoringParameters:
    flag_min_confidence: Decimal
    normaliser: str


# ------------------------------------------------------------------------ record building


def _instant(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("created_at must be an aware datetime; UTC is never assumed")
    utc = moment.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}"
        f"T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}.{utc.microsecond:06d}Z"
    )


def _base(entity_id: TypedId, created_at: datetime, *, version: int | None = 1) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": str(entity_id),
        "schema_version": SCHEMA_VERSION,
        "created_at": _instant(created_at),
        "metadata": {},
        "metadata_flags": [],
    }
    if version is not None:
        document["version"] = version
    return document


def _sealed(model: Any, document: dict[str, Any]) -> Any:
    subject = evaluator_digest_domain(document) if model is Evaluator else document
    digest = compute_digest(subject).model_dump(mode="json", exclude_none=True)
    return model.model_validate({**document, "content_digest": digest})


def _ref(record: Any) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "version": record.version,
        "content_digest": record.content_digest.model_dump(mode="json", exclude_none=True),
    }


def _template_document(
    key: str, created_at: datetime, pass_rule: Mapping[str, Any] | None
) -> dict[str, Any]:
    document = _base(MetricDefinitionId.new(), created_at)
    document.update(
        {
            "key": key,
            "kind": "deterministic",
            "value_type": "numeric",
            "unit": _UNITS[key],
            "requires": list(_REQUIRES),
            "languages": ["mr"],
            "script_sensitive": False,
            "aggregation": {"kind": "per_conversation"},
            "pass_rule": None if pass_rule is None else dict(pass_rule),
            "parameters": [
                {"name": "flag_min_confidence", "value_type": "number"},
                {"name": "normaliser", "value_type": "string"},
            ],
            "implementation_ref": implementation_ref(key),
        }
    )
    return document


def shipped_definition(key: str, *, created_at: datetime) -> MetricDefinition:
    """A fresh version-1 definition of one shipped metric, with no pass rule (rule 3)."""
    if key not in SHIPPED_KEYS:
        raise ValueError("not a shipped metric key")
    built: MetricDefinition = _sealed(MetricDefinition, _template_document(key, created_at, None))
    return built


def shipped_definitions(*, created_at: datetime) -> tuple[MetricDefinition, ...]:
    return tuple(shipped_definition(key, created_at=created_at) for key in SHIPPED_KEYS)


def _inert_check_document(created_at: datetime) -> dict[str, Any]:
    """The expected-outcome check that schema 0.0.1 demands and that is never run (finding F13)."""
    document = _base(MetricDefinitionId.new(), created_at)
    document.update(
        {
            "key": PLACEHOLDER_KEY,
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
    return document


# ------------------------------------------------------------------- binding parameters


def _threshold(value: object, key: str) -> Decimal:
    name = PARAMETER_NAMES[0]
    if type(value) not in (int, float):
        raise BindingParameterError("binding_param_invalid", key, name)
    try:
        return parse_threshold(value)  # type: ignore[arg-type]
    except (ThresholdError, ValueError):
        raise BindingParameterError("binding_param_invalid", key, name) from None


def check_binding_parameters(
    params_by_key: Mapping[str, Mapping[str, Any]],
) -> ScoringParameters:
    """The values the three bindings carry, or a ``BindingParameterError`` (rule 4).

    Missing values are reported first, then invalid ones, then an unknown normaliser, then
    unequal values.
    """
    for key in SHIPPED_KEYS:
        params = params_by_key.get(key)
        if params is None:
            raise BindingParameterError("binding_param_missing", key)
        for name in PARAMETER_NAMES:
            if params.get(name) is None:
                raise BindingParameterError("binding_param_missing", key, name)
    found: list[ScoringParameters] = []
    for key in SHIPPED_KEYS:
        params = params_by_key[key]
        threshold = _threshold(params[PARAMETER_NAMES[0]], key)
        normaliser = params[PARAMETER_NAMES[1]]
        if not isinstance(normaliser, str):
            raise BindingParameterError("binding_param_invalid", key, PARAMETER_NAMES[1])
        found.append(ScoringParameters(threshold, normaliser))
    for key, item in zip(SHIPPED_KEYS, found, strict=True):
        if item.normaliser not in SUPPORTED_NORMALISERS:
            raise BindingParameterError("normaliser_unknown", key, PARAMETER_NAMES[1])
    if any(item != found[0] for item in found[1:]):
        raise BindingParameterError("binding_param_unequal")
    return found[0]


# ------------------------------------------------------------------------ configuration


@dataclass(frozen=True)
class RecordedConfiguration:
    """A sealed snapshot of a recorded-run configuration; one value serves many runs."""

    snapshot: ConfigSnapshot

    def definition(self, key: str) -> MetricDefinition:
        for definition in self.snapshot.metric_definitions:
            if definition.key == key:
                return definition
        raise KeyError(key)

    def binding(self, key: str) -> MetricBinding:
        definition = self.definition(key)
        for binding in self.snapshot.metric_bindings:
            if binding.metric.id == definition.id:
                return binding
        raise KeyError(key)


def parameters_of(snapshot: ConfigSnapshot) -> ScoringParameters:
    """The run parameters as the snapshot stores them (never from the rubric file)."""
    configuration = RecordedConfiguration(snapshot)
    params: dict[str, Mapping[str, Any]] = {}
    for key in SHIPPED_KEYS:
        try:
            params[key] = configuration.binding(key).params
        except KeyError:
            continue
    return check_binding_parameters(params)


def build_configuration(
    spec: RubricSpec,
    *,
    configured_language: str,
    created_at: datetime,
    registry: ScorerRegistry = DEFAULT_REGISTRY,
) -> RecordedConfiguration:
    """Build and seal the snapshot of one recorded-run configuration (rule 17).

    Order everywhere: MER, count, share, then the inert check. Every call makes fresh ids.
    """
    check_spec(spec)
    params = {
        PARAMETER_NAMES[0]: spec.flag_min_confidence,
        PARAMETER_NAMES[1]: spec.normaliser,
    }
    check_binding_parameters(dict.fromkeys(SHIPPED_KEYS, params))
    rules = {item.metric: item.pass_rule for item in spec.items}
    definitions: list[MetricDefinition] = []
    for key in SHIPPED_KEYS:
        rule = rules.get(key)
        pass_rule = None if rule is None else rule.model_dump(mode="json", exclude_none=True)
        definitions.append(
            _sealed(MetricDefinition, _template_document(key, created_at, pass_rule))
        )
    inert: MetricDefinition = _sealed(MetricDefinition, _inert_check_document(created_at))
    definitions.append(inert)
    check_runnable(definitions, registry)

    persona: Persona = _sealed(
        Persona, {**_base(PersonaId.new(), created_at), "language": configured_language}
    )
    evaluator_id = EvaluatorId.new()
    bindings: list[MetricBinding] = []
    for definition in definitions:
        document = _base(MetricBindingId.new(), created_at)
        document.update(
            {
                "evaluator_id": str(evaluator_id),
                "metric": _ref(definition),
                "params": dict(params) if definition.key in SHIPPED_KEYS else {},
                "enabled": True,
            }
        )
        bindings.append(_sealed(MetricBinding, document))
    binding_of = {b.metric.id: b for b in bindings}

    evaluator_document = _base(evaluator_id, created_at)
    evaluator_document.update(
        {
            "name": EVALUATOR_NAME,
            "language": configured_language,
            "instructions": _INSTRUCTIONS,
            "expected_outcome": _EXPECTED_OUTCOME,
            "persona": _ref(persona),
            "metric_bindings": [_ref(binding) for binding in bindings],
            "scripted_turns": [],
            "expected_tool_calls": [],
            "max_duration_ms": MAX_DURATION_MS,
        }
    )
    evaluator: Evaluator = _sealed(Evaluator, evaluator_document)

    by_key = {definition.key: definition for definition in definitions}
    items = [{"binding": _ref(binding_of[cast(MetricDefinitionId, inert.id)]), "required": True}]
    for item in spec.items:
        binding = binding_of[cast(MetricDefinitionId, by_key[item.metric].id)]
        items.append({"binding": _ref(binding), "required": item.required})
    rubric: Rubric = _sealed(Rubric, {**_base(RubricId.new(), created_at), "items": items})

    document = _base(ConfigSnapshotId.new(), created_at, version=None)
    document.update(
        {
            "evaluator": evaluator.model_dump(mode="json", by_alias=True),
            "persona": persona.model_dump(mode="json", by_alias=True),
            "metric_definitions": [d.model_dump(mode="json", by_alias=True) for d in definitions],
            "metric_bindings": [b.model_dump(mode="json", by_alias=True) for b in bindings],
            "rubric": rubric.model_dump(mode="json", by_alias=True),
        }
    )
    digest = compute_snapshot_digest(document).model_dump(mode="json", exclude_none=True)
    snapshot = ConfigSnapshot.model_validate({**document, "content_digest": digest})
    return RecordedConfiguration(snapshot)


# ----------------------------------------------------------------- project and agent target


def recorded_project(
    *, name: str, created_at: datetime, provider_configs: Sequence[ProviderConfigRef] = ()
) -> Project:
    return Project(
        id=ProjectId.new(),
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        name=name,
        provider_configs=list(provider_configs),
    )


def recorded_agent_target(*, name: str, created_at: datetime) -> AgentTarget:
    """An agent target for a recording: a reserved endpoint that cannot resolve (finding F15)."""
    document = _base(AgentTargetId.new(), created_at)
    document.update(
        {
            "name": name,
            "endpoint": {"transport": "ws", "url": RECORDED_ENDPOINT_URL},
            "languages": [],
            "declared_capabilities": [],
            "declared_stack": [],
        }
    )
    built: AgentTarget = _sealed(AgentTarget, document)
    return built
