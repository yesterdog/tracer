# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Usage records: what was consumed in one billable call (ADR 0014 Decisions 8 and 9; P1-23).

- ``UsageQuantity{unit, count}``: one disjoint bucket of usage, with the count type set by the
  unit.
- ``UsageRecord`` (``use``, a record of fact): the usage of one billable call.

The rules of each model are stated in its class docstring, which is also its JSON Schema
``description`` (finding A29).

``UsageRecord`` hides its input in validation errors and no message inside it quotes a rejected
value (N60; ADR 0007 Decision 8). No float is used anywhere in this module (ADR 0014 Decision
15a).
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, Strict, model_validator

from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import EntityBase
from tracer_evalschema.ids import ExternalRef, RunId
from tracer_evalschema.ijson import MAX_SAFE_INTEGER, IJsonObject
from tracer_evalschema.primitives import DecimalString, UtcDatetime
from tracer_evalschema.text import MachineKey

__all__ = [
    "DOMAINS",
    "INTEGER_UNITS",
    "SECONDS_UNITS",
    "USAGE_SOURCES",
    "USAGE_STAGES",
    "USAGE_UNITS",
    "UsageQuantity",
    "UsageRecord",
    "UsageUnit",
]

USAGE_UNITS: Final = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "audio_input_seconds",
    "audio_output_seconds",
    "input_characters",
    "requests",
)
"""The eight disjoint usage buckets (ADR 0014 Decision 9); ``input_tokens`` is uncached input."""

SECONDS_UNITS: Final = ("audio_input_seconds", "audio_output_seconds")
INTEGER_UNITS: Final = tuple(unit for unit in USAGE_UNITS if unit not in SECONDS_UNITS)
DOMAINS: Final = ("evaluation", "agent_under_test")
USAGE_STAGES: Final = (
    "stt",
    "llm",
    "tts",
    "judge",
    "sim_caller_llm",
    "sim_caller_tts",
    "realtime",
    "other",
)
USAGE_SOURCES: Final = ("provider_reported", "telemetry", "measured")

UsageUnit = Annotated[str, ClosedSet(USAGE_UNITS)]
"""One of the eight usage units (an ``enum`` in the JSON Schema)."""


def _not_negative(text: str) -> str:
    # a canonical decimal is negative exactly when it starts with "-" ("-0" is not canonical)
    if text.startswith("-"):
        raise ValueError("a usage quantity is never negative (ADR 0014 Decision 9)")
    return text


Count = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]
Seconds = Annotated[DecimalString, AfterValidator(_not_negative)]


def _quantity_conditions(schema: dict[str, Any]) -> None:
    """The count type by unit (ADR 0014 Decision 9), as JSON Schema ``if``/``then``."""
    schema["allOf"] = [
        {
            "if": {"properties": {"unit": {"enum": list(SECONDS_UNITS)}}, "required": ["unit"]},
            "then": {"properties": {"count": {"type": "string"}}},
            "else": {"properties": {"count": {"type": "integer"}}},
        }
    ]


class UsageQuantity(BaseModel):
    """One usage bucket of a billable call (ADR 0014 Decision 9).

    ``unit`` is one of eight disjoint buckets (``input_tokens`` is uncached input). For the token,
    character and request units ``count`` is a strict integer >= 0; for the two audio-seconds
    units (``audio_input_seconds``, ``audio_output_seconds``) it is a canonical decimal string
    >= 0 (a JSON number is rejected, so seconds are never a float). Zero is known usage of nothing
    (N45 (b)); no quantity is negative (N45 (a)).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_quantity_conditions)

    unit: UsageUnit
    count: Annotated[Count | Seconds, Field(union_mode="left_to_right")]

    @model_validator(mode="after")
    def _count_fits_unit(self) -> UsageQuantity:
        seconds = self.unit in SECONDS_UNITS
        if seconds and type(self.count) is not str:
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("count",),
                        "usage_count",
                        "an audio-seconds count is a canonical decimal string, never a number",
                    )
                ],
            )
        if not seconds and type(self.count) is not int:
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("count",),
                        "usage_count",
                        "a token, character or request count is an integer >= 0, never a text "
                        "or a fraction (it is never rounded)",
                    )
                ],
            )
        return self


def _one_quantity_per_unit(quantities: list[UsageQuantity]) -> list[UsageQuantity]:
    seen: set[str] = set()
    for position, quantity in enumerate(quantities):
        if quantity.unit in seen:
            raise ValueError(
                f"quantities hold one unit twice (again at {position}); the units are disjoint "
                "buckets and the schema never sums them (ADR 0014 Decision 9, N44)"
            )
        seen.add(quantity.unit)
    return quantities


Quantities = Annotated[
    list[UsageQuantity],
    Field(min_length=1),
    AfterValidator(_one_quantity_per_unit),
]


def _usage_conditions(schema: dict[str, Any]) -> None:
    """P-39 as JSON Schema ``if``/``then``: agent-under-test usage is never ``measured``."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"domain": {"enum": ["agent_under_test"]}},
                "required": ["domain"],
            },
            "then": {"properties": {"source": {"enum": ["provider_reported", "telemetry"]}}},
        }
    ]


class UsageRecord(HidingModel, EntityBase):
    """The usage of one billable call (prefix ``use``; a record of fact, ADR 0009 Decision 5).

    ``run_id`` names the run (the only record of fact that does, N22). ``model`` absent or
    ``null`` means the source did not report a model: a placeholder such as ``"unknown"`` is never
    stored as a model, and a usage without a model is never priced (M3). ``occurred_at`` is the
    instant of the billable event as the source states it, never filled with the ingest time, the
    run start or the current time (N43). ``quantities`` is non-empty with at most one quantity per
    unit; the units are disjoint and never summed (N44). ``raw`` is the source payload, an I-JSON
    object kept verbatim and never interpreted. Usage of the ``agent_under_test`` domain is never
    ``source=measured`` (P-39): we never make the agent's billable calls. A correction is a new
    record with a new ID.
    """

    model_config = ConfigDict(json_schema_extra=_usage_conditions)

    id_prefix: ClassVar[str] = "use"

    run_id: RunId
    domain: Annotated[str, ClosedSet(DOMAINS)]
    stage: Annotated[str, ClosedSet(USAGE_STAGES)]
    provider: MachineKey
    model: MachineKey | None = None
    provider_call_ref: ExternalRef | None = None
    occurred_at: UtcDatetime | None = None
    quantities: Quantities
    source: Annotated[str, ClosedSet(USAGE_SOURCES)]
    raw: IJsonObject | None = None

    @model_validator(mode="after")
    def _agent_usage_is_never_measured(self) -> UsageRecord:
        if self.domain == "agent_under_test" and self.source == "measured":
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("source",),
                        "usage_source",
                        "agent_under_test usage is never measured: we do not make the agent's "
                        "billable calls, so its usage is provider_reported or telemetry (P-39)",
                    )
                ],
            )
        return self
