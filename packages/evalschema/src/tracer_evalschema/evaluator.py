# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Evaluator: one test of an agent, with its script, expectations and metrics (ADR 0010 Decision 8).

- ``ScriptedTurn{index, text, language}``: a caller line, at list position ``index`` (0, 1, 2,
  ... without gaps). It is not a ``Turn``.
- ``ExpectedToolCall{name, match, arguments?, order_index}``: ``match`` is ``name_only`` (no
  ``arguments``), ``arguments_exact`` (``arguments`` required, ``{}`` allowed) or
  ``arguments_subset`` (non-empty ``arguments``); ``order_index`` is a rank, and the ranks of an
  evaluator form 0..R.
- ``Evaluator`` (``evl``, versioned): references its persona, an optional test profile and its
  metric bindings by ``{id, version, content_digest?}``.

Profile placeholders ``{{profile.<field>}}`` may stand in the instructions, the expected
outcome, scripted turn text and every string value of expected tool-call arguments (P-16, E7).
They are stored unresolved, unknown fields and malformed placeholders are rejected, and an
evaluator with any placeholder must reference a ``test_profile`` (N8).

The Evaluator's digest domain is the generic one minus ``test_profile.content_digest``
(``evaluator_digest_domain``, N20 decided by the owner): dropping or keying the profile digest in
a shared export leaves the evaluator's digest, and the snapshot's, verifiable.

``Evaluator``, ``ScriptedTurn`` and ``ExpectedToolCall`` hide their input in validation errors and
no message inside them quotes a rejected value (ADR 0007 Decision 8 (b) and (c); O4, N41, N42).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Final

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    Strict,
    WithJsonSchema,
    model_validator,
)

from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import VersionedEntity, json_string_leaves
from tracer_evalschema.digest import digest_domain
from tracer_evalschema.ijson import IJsonObject
from tracer_evalschema.language import SupportedLanguage
from tracer_evalschema.metrics import MetricBindingRef
from tracer_evalschema.persona import PersonaRef
from tracer_evalschema.profile import TestProfileRef, check_profile_text, profile_placeholders
from tracer_evalschema.text import HumanLabel, MachineKey, VerbatimText

__all__ = [
    "MATCH_MODES",
    "MAX_DURATION_MS",
    "Evaluator",
    "ExpectedToolCall",
    "ProfileText",
    "ScriptedTurn",
    "evaluator_digest_domain",
]

MATCH_MODES: Final = ("name_only", "arguments_exact", "arguments_subset")
MAX_DURATION_MS: Final = 14_400_000  # 4 hours (ADR 0002 Decision 2, api-surface 19.4 item 6)

ProfileText = Annotated[VerbatimText, Field(min_length=1), AfterValidator(check_profile_text)]
"""Non-empty free text whose profile placeholders are well formed and name profile fields."""

MaxDurationMs = Annotated[
    int,
    Strict(),
    Field(gt=0, le=MAX_DURATION_MS),
    WithJsonSchema(
        {"type": "integer", "minimum": 0, "exclusiveMinimum": 0, "maximum": MAX_DURATION_MS}
    ),
]
"""1 ms to 4 hours. The ``Ms`` base schema (``minimum: 0``) is kept and only tightened."""

Index = Annotated[int, Strict(), Field(ge=0)]


class ScriptedTurn(HidingModel):
    """A scripted caller line: its list position, its text and its language."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    index: Index
    text: ProfileText
    language: SupportedLanguage


def _argument_placeholders(arguments: dict[str, Any] | None) -> list[str]:
    """The profile fields named in the string values of ``arguments`` (keys are never text)."""
    if arguments is None:
        return []
    return [
        field
        for _path, text in json_string_leaves(arguments)
        for field in profile_placeholders(text)
    ]


def _match_conditions(schema: dict[str, Any]) -> None:
    def when(match: str) -> dict[str, Any]:
        return {"properties": {"match": {"enum": [match]}}, "required": ["match"]}

    schema["allOf"] = [
        {"if": when("name_only"), "then": {"properties": {"arguments": {"type": "null"}}}},
        {
            "if": when("arguments_exact"),
            "then": {"required": ["arguments"], "properties": {"arguments": {"type": "object"}}},
        },
        {
            "if": when("arguments_subset"),
            "then": {
                "required": ["arguments"],
                "properties": {"arguments": {"type": "object", "minProperties": 1}},
            },
        },
    ]


class ExpectedToolCall(HidingModel):
    """A tool call the agent is expected to make (ADR 0010 Decision 8)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        json_schema_extra=_match_conditions,
    )

    name: MachineKey
    match: Annotated[str, ClosedSet(MATCH_MODES)]
    arguments: IJsonObject | None = None
    order_index: Index

    @model_validator(mode="after")
    def _arguments_fit_match(self) -> ExpectedToolCall:
        problem = None
        if self.match == "name_only" and self.arguments is not None:
            problem = "match=name_only compares the tool name only: arguments are forbidden"
        elif self.match == "arguments_exact" and self.arguments is None:
            problem = "match=arguments_exact needs arguments ({} means no arguments)"
        elif self.match == "arguments_subset" and not self.arguments:
            problem = "match=arguments_subset needs a non-empty arguments object"
        if problem is not None:
            raise_problems(
                type(self).__name__, [Problem(("arguments",), "arguments", problem, self.arguments)]
            )
        try:
            _argument_placeholders(self.arguments)
        except ValueError as exc:
            raise_problems(
                type(self).__name__, [Problem(("arguments",), "profile_placeholder", str(exc))]
            )
        return self


def _contiguous_indexes(turns: list[ScriptedTurn]) -> list[ScriptedTurn]:
    for position, turn in enumerate(turns):
        if turn.index != position:
            raise ValueError(
                f"scripted turn indexes run 0, 1, 2, ... in list order; the turn at position "
                f"{position} has another index"
            )
    return turns


def _contiguous_ranks(calls: list[ExpectedToolCall]) -> list[ExpectedToolCall]:
    ranks = {call.order_index for call in calls}
    if ranks and ranks != set(range(max(ranks) + 1)):
        raise ValueError("expected tool call ranks (order_index) form 0..R without gaps")
    return calls


def _unique_bindings(references: list[MetricBindingRef]) -> list[MetricBindingRef]:
    seen = set()
    for position, reference in enumerate(references):
        if reference.id in seen:
            if values_hidden():
                raise ValueError(f"a binding is listed twice (again at position {position})")
            raise ValueError(f"binding {reference.id} is listed twice")
        seen.add(reference.id)
    return references


def evaluator_digest_domain(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The digest domain of an Evaluator JSON document (ADR 0006 Decision 5, N20).

    The generic domain (``digest.digest_domain``) minus the one path
    ``test_profile.content_digest``; a ``content_digest`` key anywhere else, for example inside
    user ``metadata``, stays. The input is not changed.
    """
    domain = digest_domain(doc)
    profile = domain.get("test_profile")
    if isinstance(profile, dict):
        domain["test_profile"] = {k: v for k, v in profile.items() if k != "content_digest"}
    return domain


class Evaluator(HidingModel, VersionedEntity):
    """One test of an agent (prefix ``evl``; ADR 0010 Decision 8, ADR 0013 Decision 2).

    Its ``content_digest`` is computed over ``evaluator_digest_domain`` (N20).
    """

    id_prefix: ClassVar[str] = "evl"

    name: HumanLabel
    language: SupportedLanguage
    instructions: ProfileText
    expected_outcome: ProfileText
    persona: PersonaRef
    test_profile: TestProfileRef | None = None
    metric_bindings: Annotated[
        list[MetricBindingRef], Field(min_length=1), AfterValidator(_unique_bindings)
    ]
    scripted_turns: Annotated[list[ScriptedTurn], AfterValidator(_contiguous_indexes)] = Field(
        default_factory=list
    )
    expected_tool_calls: Annotated[list[ExpectedToolCall], AfterValidator(_contiguous_ranks)] = (
        Field(default_factory=list)
    )
    max_duration_ms: MaxDurationMs

    def digest_subject(self) -> dict[str, Any]:
        """The Evaluator digest domain: the generic one minus ``test_profile.content_digest``."""
        return evaluator_digest_domain(self.model_dump(mode="json", by_alias=True))

    def profile_placeholder_fields_used(self) -> list[str]:
        """The profile fields named by placeholders anywhere in the evaluator, in order."""
        texts = [self.instructions, self.expected_outcome, *(t.text for t in self.scripted_turns)]
        fields = [field for text in texts for field in profile_placeholders(text)]
        for call in self.expected_tool_calls:
            fields.extend(_argument_placeholders(call.arguments))
        return fields

    @model_validator(mode="after")
    def _placeholders_need_a_profile(self) -> Evaluator:
        if self.test_profile is None and self.profile_placeholder_fields_used():
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("test_profile",),
                        "test_profile_required",
                        "the evaluator holds profile placeholders, so it must reference a "
                        "test_profile to fill them",
                    )
                ],
            )
        return self
