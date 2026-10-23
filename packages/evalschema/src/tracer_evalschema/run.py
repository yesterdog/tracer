# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Run: one execution of an evaluator against an agent, or one imported recording (ADR 0004
Decisions 2 and 2a, ADR 0009 Decision 5, ADR 0012 Decision 6, ADR 0016 Decisions 2 and 6; P1-12).

- ``Run`` (``run``, a record of fact): its fields and record rules are stated in its class
  docstring, which is also its JSON Schema ``description`` (finding A29). R2, the tier computed
  from the records' provenance, is a bundle check (P1-14).

The Run and ``CallerExternalId`` hide their input in validation errors (a caller identifier may be
a phone number) and no message inside them quotes a rejected value (ADR 0007 Decision 8).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, ClassVar, Final

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.agent import AgentTargetRef
from tracer_evalschema.artifact import ChannelMap, agent_audio_available
from tracer_evalschema.base import EntityBase
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import ConfigSnapshotId, ExternalRef, ProjectId
from tracer_evalschema.metrics import InputKind
from tracer_evalschema.primitives import UtcDatetime
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.text import VerbatimText

__all__ = [
    "CALLER_ORIGINS",
    "OBSERVED_TIERS",
    "RUN_MODES",
    "CallerExternalId",
    "Run",
]

RUN_MODES: Final = ("simulated", "recorded")
CALLER_ORIGINS: Final = ("simulated", "human")
OBSERVED_TIERS: Final = ("T0", "T1", "T2")


def _unique_kinds(what: str) -> Callable[[list[str]], list[str]]:
    def check(kinds: list[str]) -> list[str]:
        seen: set[str] = set()
        for position, kind in enumerate(kinds):
            if kind in seen:
                if values_hidden():
                    raise ValueError(f"{what} lists one input kind twice (again at {position})")
                raise ValueError(f"{what} lists {kind!r} twice")
            seen.add(kind)
        return kinds

    return check


class CallerExternalId(HidingModel):
    """The caller's transport identifier (a SIP or telephony ``user_id``, possibly a phone
    number), verbatim, with its own privacy marker (P-43, ADR 0016 Decision 6). The only place
    such an identifier may live; it never enters a snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    value: Annotated[VerbatimText, Field(min_length=1)]
    privacy: PrivacyMarker


InputKinds = Annotated[list[InputKind], Field(json_schema_extra={"uniqueItems": True})]


class Run(HidingModel, EntityBase):
    """A run (prefix ``run``; a record of fact, ADR 0009 Decision 5).

    It names the project, the agent target, and the configuration snapshot it ran with. ``mode``
    and ``caller_origin`` are both required, and a ``simulated`` run never has a ``human`` caller.
    ``started_at`` is the start of the run and never the ingest time; absent when not known.
    ``caller_external_id`` is the caller's transport identifier with its own privacy marker.
    ``detected_capabilities`` (what the run detected) and ``available_inputs`` (what evidence it
    holds) are separate lists. ``agent_audio`` in ``available_inputs`` needs a channel map that
    identifies the agent's channel (ADR 0004 Decision 5). ``observed_tier`` is closed and required
    and is for display and filtering only; ``spans`` in ``available_inputs`` with
    ``observed_tier=T0`` is rejected, because stage spans come only from agent telemetry or an
    observer (rule R1, O3a; ``detected_capabilities`` is not read for it).
    """

    id_prefix: ClassVar[str] = "run"

    project_id: ProjectId
    agent_target: AgentTargetRef
    config_snapshot_id: ConfigSnapshotId
    mode: Annotated[str, ClosedSet(RUN_MODES)]
    caller_origin: Annotated[str, ClosedSet(CALLER_ORIGINS)]
    started_at: UtcDatetime | None = None
    caller_external_id: CallerExternalId | None = None
    detected_capabilities: Annotated[
        InputKinds, AfterValidator(_unique_kinds("detected_capabilities"))
    ] = Field(default_factory=list)
    channel_maps: list[ChannelMap] = Field(default_factory=list)
    available_inputs: Annotated[InputKinds, AfterValidator(_unique_kinds("available_inputs"))] = (
        Field(default_factory=list)
    )
    observed_tier: Annotated[str, ClosedSet(OBSERVED_TIERS)]
    external_refs: list[ExternalRef] = Field(default_factory=list)
    ingest_warnings: list[IngestWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> Run:
        problems: list[Problem] = []
        if self.mode == "simulated" and self.caller_origin == "human":
            problems.append(
                Problem(
                    ("caller_origin",),
                    "caller_origin",
                    "a simulated run is driven by our runner, so its caller_origin is simulated",
                )
            )
        if "agent_audio" in self.available_inputs and not agent_audio_available(self.channel_maps):
            problems.append(
                Problem(
                    ("available_inputs",),
                    "agent_audio",
                    "agent_audio is available only when a channel map identifies the agent's "
                    "channel (ADR 0004 Decision 5)",
                )
            )
        if "spans" in self.available_inputs and self.observed_tier == "T0":
            problems.append(
                Problem(
                    ("observed_tier",),
                    "observed_tier",
                    "a run whose available_inputs hold spans is at least T1: stage spans come only "
                    "from agent telemetry or an observer (ADR 0004 Decision 2a, rule R1)",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self
