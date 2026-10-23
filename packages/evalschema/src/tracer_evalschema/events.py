# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Tool calls, stage spans, evidence references and ingest warnings (ADR 0002, 0003, 0012;
P1-10c).

- ``ToolCallEvent`` (``tcl``): a tool call of the agent, with the raw arguments and result kept
  verbatim. ``arguments`` (the parsed JSON object) is present only when ``arguments_complete`` is
  true, and ``arguments_complete`` is never true for a raw payload that is not a whole JSON object
  (a truncated payload never yields it). ``source`` is the closed set ``agent_export``,
  ``agent_telemetry``, ``observer`` (O2). ``external_call_id`` is the provider's opaque
  identifier, stored verbatim (ADR 0001 I12). It hides its input in validation errors (N31).
- ``StageSpan`` (``spn``): the timing of one pipeline stage, pointing to the raw span by
  ``span_ref`` (an ``ExternalRef``).
- ``EvidenceRef{kind, ref_id, start_ms?, end_ms?}``: from a result to a turn, exchange, tool
  call, span or a range of an artifact; ``ref_id`` is an ID of the kind ``kind`` names.
- ``IngestWarning{code, path?, raw?, message}``: what an importer could not map faithfully. It
  hides its input (its ``raw`` holds source values verbatim).
- ``check_tool_call_seqs(events)`` and ``check_evidence_resolves(refs, records)``: the rules that
  need several records of one bundle (N22), raising ``ValueError``.

No message of a model that hides its input quotes a rejected value (ADR 0007 Decision 8 (c),
N42).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Annotated, Any, ClassVar, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    Strict,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._constraints import ClosedSet, echoed
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import EntityBase
from tracer_evalschema.errors import TracerSchemaError
from tracer_evalschema.ids import (
    ArtifactId,
    ExchangeId,
    ExternalRef,
    StageSpanId,
    ToolCallEventId,
    TurnId,
    TypedId,
    wire_pattern,
)
from tracer_evalschema.ijson import MAX_SAFE_INTEGER, IJsonObject
from tracer_evalschema.loader import loads_strict
from tracer_evalschema.primitives import Ms, UtcDatetime
from tracer_evalschema.text import MachineKey, VerbatimText
from tracer_evalschema.transcript import TimelineOrigin

__all__ = [
    "EVIDENCE_KINDS",
    "STAGES",
    "TOOL_CALL_SOURCES",
    "TOOL_CALL_STATUSES",
    "EvidenceId",
    "EvidenceRef",
    "IngestWarning",
    "StageSpan",
    "ToolCallEvent",
    "check_evidence_resolves",
    "check_tool_call_seqs",
]

TOOL_CALL_STATUSES: Final = ("completed", "cancelled", "unknown")
TOOL_CALL_SOURCES: Final = ("agent_export", "agent_telemetry", "observer")
STAGES: Final = (
    "stt",
    "llm",
    "tts",
    "judge",
    "sim_caller_llm",
    "sim_caller_tts",
    "realtime",
    "other",
)
EVIDENCE_KINDS: Final = ("turn", "exchange", "tool_call", "span", "artifact_range")

_EVIDENCE_ID_TYPES: Final[dict[str, type[TypedId]]] = {
    "turn": TurnId,
    "exchange": ExchangeId,
    "tool_call": ToolCallEventId,
    "span": StageSpanId,
    "artifact_range": ArtifactId,
}

Index = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]


# ------------------------------------------------------------------------------ tool calls


def _is_whole_json_object(raw: str) -> bool:
    """Whether ``raw`` parses (strictly: no duplicate keys, no NaN) as one JSON object."""
    try:
        parsed = loads_strict(raw)
    except (ValueError, TracerSchemaError):
        return False
    return isinstance(parsed, dict)


def _tool_call_conditions(schema: dict[str, Any]) -> None:
    """The presence rules of a tool call as ``if``/``then``: ``arguments`` only with
    ``arguments_complete=true`` (ADR 0012 I1), and ``result_complete`` only together with
    ``result_raw``. The second is keyed on a non-null ``result_complete`` rather than written as
    ``dependentRequired``, because an absent member and ``null`` are the same to the model: a
    ``result_complete: null`` without ``result_raw`` is valid and ``dependentRequired`` would
    reject it."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"arguments_complete": {"enum": [False]}},
                "required": ["arguments_complete"],
            },
            "then": {"properties": {"arguments": {"type": "null"}}},
        },
        {
            "if": {
                "properties": {"result_complete": {"type": "boolean"}},
                "required": ["result_complete"],
            },
            "then": {"required": ["result_raw"], "properties": {"result_raw": {"type": "string"}}},
        },
    ]


class ToolCallEvent(HidingModel, EntityBase):
    """A tool call of the agent (prefix ``tcl``; ADR 0012 Decision 1).

    ``arguments_complete`` is required and never inferred. ``result_complete`` needs
    ``result_raw``. ``timeline_origin`` is present if and only if ``offset_ms`` is. ``seq`` is the
    order of the calls of a run and is unique there (``check_tool_call_seqs``). ``result_error``
    is true, false or unknown (``None``): ``status`` says only whether the call finished. The
    event carries no privacy marker of its own (it takes its run's derived value).
    """

    model_config = ConfigDict(json_schema_extra=_tool_call_conditions)

    id_prefix: ClassVar[str] = "tcl"

    name: MachineKey
    arguments_raw: VerbatimText | None = None
    arguments: IJsonObject | None = None
    arguments_complete: Annotated[bool, Strict()]
    result_raw: VerbatimText | None = None
    result_complete: Annotated[bool, Strict()] | None = None
    status: Annotated[str, ClosedSet(TOOL_CALL_STATUSES)]
    result_error: Annotated[bool, Strict()] | None = None
    seq: Index
    offset_ms: Ms | None = None
    timeline_origin: TimelineOrigin | None = None
    source: Annotated[str, ClosedSet(TOOL_CALL_SOURCES)]
    external_call_id: VerbatimText | None = None

    @model_validator(mode="after")
    def _rules(self) -> ToolCallEvent:
        problems: list[Problem] = []
        if self.arguments is not None and not self.arguments_complete:
            problems.append(
                Problem(
                    ("arguments",),
                    "tool_arguments",
                    "parsed arguments are present only when arguments_complete is true",
                )
            )
        if (
            self.arguments_complete
            and self.arguments_raw is not None
            and not _is_whole_json_object(self.arguments_raw)
        ):
            problems.append(
                Problem(
                    ("arguments_complete",),
                    "tool_arguments",
                    "arguments_complete=true needs arguments_raw to be one whole JSON object (a "
                    "truncated or non-object payload is never complete)",
                )
            )
        if self.result_complete is not None and self.result_raw is None:
            problems.append(
                Problem(
                    ("result_complete",),
                    "tool_result",
                    "result_complete is stated only together with result_raw",
                )
            )
        if (self.timeline_origin is None) != (self.offset_ms is None):
            problems.append(
                Problem(
                    ("timeline_origin",),
                    "timeline_origin",
                    "timeline_origin is present if and only if offset_ms is",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self


# ------------------------------------------------------------------------------- stage spans


def _span_conditions(schema: dict[str, Any]) -> None:
    """``stage_raw`` if and only if ``stage=other`` (P-05), as JSON Schema ``if``/``then``."""
    schema["allOf"] = [
        {
            "if": {"properties": {"stage": {"enum": ["other"]}}, "required": ["stage"]},
            "then": {"required": ["stage_raw"], "properties": {"stage_raw": {"type": "string"}}},
            "else": {"properties": {"stage_raw": {"type": "null"}}},
        }
    ]


class StageSpan(EntityBase):
    """The timing of one pipeline stage (prefix ``spn``; ADR 0012 Decision 2).

    ``stage_raw`` keeps the source's label when, and only when, ``stage`` is ``other`` (P-05).
    Values the source does not give stay absent and are never derived. ``span_ref`` points to the
    raw span or trace (a foreign identifier, ADR 0012 Decision 6).
    """

    model_config = ConfigDict(json_schema_extra=_span_conditions)

    id_prefix: ClassVar[str] = "spn"

    stage: Annotated[str, ClosedSet(STAGES)]
    stage_raw: MachineKey | None = None
    provider: MachineKey | None = None
    model: MachineKey | None = None
    start: UtcDatetime | None = None
    duration_ms: Ms | None = None
    ttfb_ms: Ms | None = None
    exchange_index: Index | None = None
    span_ref: ExternalRef

    @model_validator(mode="after")
    def _stage_raw_iff_other(self) -> StageSpan:
        problem = None
        if self.stage == "other" and self.stage_raw is None:
            problem = "stage=other keeps the source's stage label in stage_raw"
        elif self.stage != "other" and self.stage_raw is not None:
            problem = "stage_raw belongs to stage=other only"
        if problem is not None:
            raise_problems(type(self).__name__, [Problem(("stage_raw",), "stage_raw", problem)])
        return self


# ---------------------------------------------------------------------------- evidence refs

_EVIDENCE_PREFIXES = "(?:" + "|".join(t.prefix for t in _EVIDENCE_ID_TYPES.values()) + ")"
EVIDENCE_ID_PATTERN: Final = wire_pattern(_EVIDENCE_PREFIXES)


def _evidence_id(value: Any) -> TypedId:
    if isinstance(value, TypedId) and type(value) in _EVIDENCE_ID_TYPES.values():
        return value
    if isinstance(value, str):
        kind = next(
            (t for t in _EVIDENCE_ID_TYPES.values() if value.startswith(t.prefix + "_")), None
        )
        if kind is not None:
            try:
                return kind.parse(value)
            except ValueError:
                pass
    if values_hidden() or not isinstance(value, str):
        raise PydanticCustomError(
            "evidence_id",
            "not the ID of a turn, exchange, tool call, span or artifact ({pattern})",
            {"pattern": EVIDENCE_ID_PATTERN},
        )
    raise PydanticCustomError(
        "evidence_id",
        "{value} is not the ID of a turn, exchange, tool call, span or artifact ({pattern})",
        {"value": echoed(value), "pattern": EVIDENCE_ID_PATTERN},
    )


class _EvidenceIdRule:
    """One validator for the union of the five evidence ID kinds (one error, not five)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _evidence_id,
            serialization=core_schema.plain_serializer_function_ser_schema(str, when_used="json"),
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": EVIDENCE_ID_PATTERN}


EvidenceId = Annotated[
    TurnId | ExchangeId | ToolCallEventId | StageSpanId | ArtifactId, _EvidenceIdRule()
]
"""The ID of a turn, exchange, tool call, stage span or artifact (``EvidenceRef.ref_id``)."""


class EvidenceRef(BaseModel):
    """A pointer from a result to its evidence in the same bundle (ADR 0012 Decision 3).

    ``ref_id`` is an ID of the kind ``kind`` names (``turn_``, ``exc_``, ``tcl_``, ``spn_``,
    ``art_``). Offsets are ``Ms`` from sample 0 of the artifact, with ``end_ms >= start_ms``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(EVIDENCE_KINDS)]
    ref_id: EvidenceId
    start_ms: Ms | None = None
    end_ms: Ms | None = None

    @model_validator(mode="after")
    def _rules(self) -> EvidenceRef:
        problems: list[Problem] = []
        if not isinstance(self.ref_id, _EVIDENCE_ID_TYPES[self.kind]):
            problems.append(
                Problem(
                    ("ref_id",),
                    "evidence_kind",
                    f"kind={self.kind} needs an ID with the prefix "
                    f"{_EVIDENCE_ID_TYPES[self.kind].prefix}_",
                )
            )
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            problems.append(
                Problem(("end_ms",), "time_order", "end_ms must not be before start_ms")
            )
        raise_problems(type(self).__name__, problems)
        return self


# ------------------------------------------------------------------------------ warnings


class IngestWarning(HidingModel):
    """Something an importer could not map faithfully (ADR 0012 Decision 7). ``path`` locates the
    value in its source (the form depends on the mapping), ``raw`` is the source value verbatim.
    A ``LanguageLabelWarning`` becomes an ``IngestWarning`` with the same ``code`` and ``raw``."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    code: MachineKey
    path: VerbatimText | None = None
    raw: VerbatimText | None = None
    message: Annotated[VerbatimText, Field(min_length=1)]


# ------------------------------------------------------------------------- bundle-level checks


def check_tool_call_seqs(events: Sequence[ToolCallEvent]) -> None:
    """``seq`` is unique among the tool calls of one run (ADR 0012 I4); the caller passes the
    events of one RunBundle (N22). ``ValueError`` names the repeated ``seq``."""
    seen: dict[int, Any] = {}
    for event in events:
        if event.seq in seen:
            raise ValueError(
                f"tool call seq {event.seq} is used by {seen[event.seq]} and {event.id}"
            )
        seen[event.seq] = event.id


def check_evidence_resolves(refs: Iterable[EvidenceRef], records: Iterable[Any]) -> None:
    """Every reference names the ``id`` of a supplied record (ADR 0012 I6). ``ValueError`` names
    the ``ref_id`` that does not resolve."""
    known: set[tuple[str, uuid.UUID]] = {
        (type(record.id).prefix, record.id.uuid) for record in records
    }
    for ref in refs:
        if (ref.ref_id.prefix, ref.ref_id.uuid) not in known:
            raise ValueError(f"evidence reference {ref.ref_id} names no supplied record")
