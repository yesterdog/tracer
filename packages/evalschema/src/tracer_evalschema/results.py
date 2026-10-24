# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Metric results, judge and scorer provenance, and applicability (ADR 0004 Decisions 3 to 7,
ADR 0010 Decisions 3, 3a, 4a, 5, 5a and 6; P1-11).

- ``MetricResult`` (``res``, a record of fact): the result of one metric definition on one run
  (``metric``, a ``MetricDefinitionRef``; no ``run_id`` and no binding, N22, N33). ``status`` is
  ``scored``, ``not_applicable`` (with ``na_reason``) or ``error`` (with ``error``); a missing
  input is never 0 or false (ADR 0004). A ``per_turn_share`` metric scores through ``per_turn``
  (``PerTurnSummary``, P-58) and has no ``value``. ``transcript_ids`` (optional) names the
  transcripts the result read, for a bundle that holds several (ADR 0004 Decision 9).
- ``ResultError``, ``ScorerInfo`` (P-61), ``JudgeTurnVerdict`` and ``JudgeInfo`` (P-62),
  ``PerTurnSummary``: value objects.
- ``missing_inputs``, ``strongest_na_reason``, ``effective_language``, ``check_language`` and
  ``applicability``: the not-applicable steps, pure functions of the definition and the run's
  records. Configured languages are never an input (P-59).
- ``check_result_against_definition(result, definition)``: the rules that need the definition
  (``ValueError`` naming the result ID). Binding parameters never take part (E6).
- ``render_per_turn_share(result)``: the share always with its turn coverage (P-56).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Any, ClassVar, Final, NamedTuple

from pydantic import (
    AfterValidator,
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

from tracer_evalschema._constraints import ClosedSet, FullMatch
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.artifact import Confidence, agent_audio_available
from tracer_evalschema.base import EntityBase
from tracer_evalschema.events import EvidenceRef
from tracer_evalschema.ids import TranscriptId
from tracer_evalschema.ijson import MAX_SAFE_INTEGER, IJsonObject
from tracer_evalschema.language import allowed_scripts, primary_language, script_subtag
from tracer_evalschema.metrics import (
    LANGUAGE_INDEPENDENT,
    EnumScale,
    InputKind,
    MetricDefinition,
    MetricDefinitionRef,
    PerTurnCounts,
    PerTurnShareAggregation,
    RatingScale,
    per_turn_share,
    rule_passes,
)
from tracer_evalschema.primitives import DecimalString
from tracer_evalschema.provider import ModelRef
from tracer_evalschema.text import MachineKey, VerbatimText

__all__ = [
    "LANGUAGE_SOURCES",
    "NA_REASONS",
    "NA_REASON_PRECEDENCE",
    "RESULT_STATUSES",
    "Applicability",
    "EffectiveLanguage",
    "JudgeInfo",
    "JudgeTurnVerdict",
    "LanguageCheck",
    "MetricResult",
    "PerTurnSummary",
    "ResultError",
    "ScorerInfo",
    "applicability",
    "check_language",
    "check_result_against_definition",
    "effective_language",
    "missing_inputs",
    "render_per_turn_share",
    "strongest_na_reason",
]

RESULT_STATUSES: Final = ("scored", "not_applicable", "error")
NA_REASON_PRECEDENCE: Final = (
    "missing_input",
    "agent_channel_unknown",
    "language_not_observed",
    "unsupported_language",
    "unsupported_script",
)
"""The not-applicable reasons in the order of ADR 0004 Decision 4: the first that applies wins."""
NA_REASONS: Final = NA_REASON_PRECEDENCE
_RUN_LEVEL_NA_REASONS: Final = ("missing_input", "agent_channel_unknown")
"""The reasons decided from the run alone, before any language step or turn is read (ADR 0004
Decisions 3 to 5): a result with one of them has no ``language_source`` and no ``per_turn``."""
LANGUAGE_SOURCES: Final = ("observed", "declared")
JUDGE_VERDICTS: Final = ("yes", "no", "undetermined")
SCORER_KINDS: Final = ("deterministic", "audio", "latency")
JUDGE_UNDETERMINED: Final = "judge_undetermined"

Count = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]

PromptSha256 = Annotated[
    str, FullMatch("^[0-9a-f]{64}$", "sha256_hex", "{value} is not 64 lower-case hex digits")
]


# ------------------------------------------------------------------------------ value objects


class ResultError(BaseModel):
    """Why a metric could not be computed although its inputs existed (ADR 0004 I3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: MachineKey
    message: Annotated[VerbatimText, Field(min_length=1)]


class ScorerInfo(BaseModel):
    """The non-judge scorer that ran (P-61): ``implementation_ref`` equals the definition's, and
    ``version`` is stored verbatim and compared for equality only (never parsed or ordered)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    implementation_ref: MachineKey
    version: MachineKey


ReportedModel = Annotated[VerbatimText, Field(min_length=1)]
"""The model string a provider returned for a call (P-62): verbatim, never filled from
``model_ref``; bidi controls are kept and detected by ``contains_bidi_controls``."""


class JudgeTurnVerdict(BaseModel):
    """The judge's answer for one turn (``turn_seq`` names a ``Turn.seq``). ``p_yes`` is present
    only if the judge produced it (ADR 0010 Decision 6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_seq: Count
    verdict: Annotated[str, ClosedSet(JUDGE_VERDICTS)]
    p_yes: Confidence | None = None
    reported_model: ReportedModel | None = None


class JudgeInfo(BaseModel):
    """The provenance of a judge's verdict (ADR 0010 Decision 6): the model asked for, the hash of
    the prompt, the call parameters, the per-turn answers, and what the judge and the provider
    reported. Nothing is synthesized: an absent ``confidence`` or ``p_yes`` stays absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_ref: ModelRef
    prompt_sha256: PromptSha256
    params: IJsonObject
    per_turn: list[JudgeTurnVerdict] = Field(default_factory=list)
    confidence: Confidence | None = None
    reported_model: ReportedModel | None = None


class PerTurnSummary(BaseModel):
    """The per-turn outcome of a ``per_turn_share`` metric (P-58): the counts, the turn coverage
    (``counted_turns`` of ``total_turns``) and the share of counted turns that passed.

    Checked on load: ``counted_turns = passed + failed``, ``total_turns`` is the sum of the four
    counts (at least 1), and ``share`` equals ``metrics.per_turn_share(counts)``: present if and
    only if a turn was counted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    share: DecimalString | None = None
    counts: PerTurnCounts
    counted_turns: Count
    total_turns: Annotated[int, Strict(), Field(ge=1, le=MAX_SAFE_INTEGER)]

    @model_validator(mode="after")
    def _consistent_with_counts(self) -> PerTurnSummary:
        counts = self.counts
        problems: list[Problem] = []
        if self.counted_turns != counts.passed + counts.failed:
            problems.append(
                Problem(
                    ("counted_turns",),
                    "per_turn_coverage",
                    "counted_turns must equal counts.passed + counts.failed",
                )
            )
        total = counts.passed + counts.failed + counts.not_applicable + counts.undetermined
        if self.total_turns != total:
            problems.append(
                Problem(
                    ("total_turns",), "per_turn_coverage", "total_turns must equal the four counts"
                )
            )
        expected = per_turn_share(counts)
        if self.share != expected:
            message = (
                "share must be absent when no turn was counted"
                if expected is None
                else "share must equal passed / (passed + failed) of the counts (half-even at "
                "scale 18)"
            )
            problems.append(Problem(("share",), "per_turn_share", message))
        raise_problems(type(self).__name__, problems)
        return self


# ------------------------------------------------------------------------------ the value


def _result_value(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise PydanticCustomError(
                "result_value", "an integer value must lie within +-(2**53 - 1) (I-JSON)"
            )
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise PydanticCustomError("result_value", "a numeric value is finite (no NaN or inf)")
        return value
    if type(value) is str:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise PydanticCustomError("result_value", "a text value holds a lone surrogate")
        return value
    raise PydanticCustomError(
        "result_value", "a value is a boolean, a finite number, a text or null"
    )


class _ResultValueRule:
    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(_result_value)

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "anyOf": [
                {"type": "boolean"},
                {"type": "integer", "minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER},
                {"type": "number"},
                {"type": "string"},
                {"type": "null"},
            ]
        }


ResultValue = Annotated[Any, _ResultValueRule()]
"""A scored value: a boolean, an integer within the I-JSON range, a finite number or a text (an
enum option); ``None`` when not scored or for a ``per_turn_share`` metric."""


def _unique_sorted_ids(ids: list[TranscriptId]) -> list[TranscriptId]:
    texts = [str(i) for i in ids]
    if len(set(texts)) != len(texts):
        raise ValueError("declared_language_transcript_ids lists one transcript twice")
    if texts != sorted(texts):
        raise ValueError("declared_language_transcript_ids are sorted by their string form")
    return ids


def _unique_read_ids(ids: list[TranscriptId]) -> list[TranscriptId]:
    """``transcript_ids`` names each transcript once; the order is the producer's and is kept."""
    texts = [str(i) for i in ids]
    if len(set(texts)) != len(texts):
        raise ValueError("transcript_ids lists one transcript twice")
    return ids


def _unique_kinds(kinds: list[str]) -> list[str]:
    if len(set(kinds)) != len(kinds):
        raise ValueError("missing_kinds lists one input kind twice")
    return kinds


# ----------------------------------------------------------------------------- MetricResult

_NULL: Final = {"type": "null"}
_NO_ITEMS: Final = {"maxItems": 0}


def _when_status(*statuses: str) -> dict[str, Any]:
    return {"properties": {"status": {"enum": list(statuses)}}, "required": ["status"]}


def _result_conditions(schema: dict[str, Any]) -> None:
    """The presence rules of ``MetricResult._status_rules`` as JSON Schema ``if``/``then`` keyed
    on the closed sets ``status``, ``na_reason`` and ``language_source`` (N38; api-surface 26).

    An absent member and ``null`` are the same to the model, so "has no X" is ``X: null`` (absent
    passes) and "needs X" is ``required`` plus a non-null type; an empty list and an absent list
    are the same too (``maxItems: 0``). Nothing here is stricter than the model: the rules that
    read another record (the definition, the bundle) stay in code.
    """
    has_value = {"properties": {"value": {"not": _NULL}, "per_turn": _NULL}}
    has_per_turn = {
        "required": ["per_turn"],
        "properties": {"value": _NULL, "per_turn": {"type": "object"}},
    }
    rules: list[dict[str, Any]] = [
        {
            # scored: no na_reason, error or missing_kinds; exactly one of value and per_turn; a
            # scored per_turn has a counted turn.
            "if": _when_status("scored"),
            "then": {
                "properties": {
                    "na_reason": _NULL,
                    "error": _NULL,
                    "missing_kinds": _NO_ITEMS,
                    "per_turn": {"properties": {"counted_turns": {"minimum": 1}}},
                },
                "anyOf": [has_value, has_per_turn],
            },
        },
        {
            # only a scored result has a value or passed (never 0, false or empty).
            "if": _when_status("not_applicable", "error"),
            "then": {"properties": {"value": _NULL, "passed": _NULL}},
        },
        {
            # not_applicable: na_reason required; nothing ran, so no error, scorer or judge; a
            # per_turn has no counted and no undetermined turn; missing_kinds non-empty if and
            # only if na_reason=missing_input.
            "if": _when_status("not_applicable"),
            "then": {
                "required": ["na_reason"],
                "properties": {
                    "na_reason": {"type": "string"},
                    "error": _NULL,
                    "scorer": _NULL,
                    "judge": _NULL,
                    "per_turn": {
                        "properties": {
                            "counted_turns": {"maximum": 0},
                            "counts": {"properties": {"undetermined": {"maximum": 0}}},
                        }
                    },
                },
                "if": {
                    "properties": {"na_reason": {"enum": ["missing_input"]}},
                    "required": ["na_reason"],
                },
                "then": {
                    "required": ["missing_kinds"],
                    "properties": {"missing_kinds": {"minItems": 1}},
                },
                "else": {"properties": {"missing_kinds": _NO_ITEMS}},
            },
        },
        {
            # error: a structured error required; no na_reason or missing_kinds.
            "if": _when_status("error"),
            "then": {
                "required": ["error"],
                "properties": {
                    "error": {"type": "object"},
                    "na_reason": _NULL,
                    "missing_kinds": _NO_ITEMS,
                },
            },
        },
        {
            # an error per_turn: no counted turn, an undetermined turn and
            # error.code=judge_undetermined (P-56).
            "if": {
                "properties": {"status": {"enum": ["error"]}, "per_turn": {"type": "object"}},
                "required": ["status", "per_turn"],
            },
            "then": {
                "required": ["error"],
                "properties": {
                    "per_turn": {
                        "properties": {
                            "counted_turns": {"maximum": 0},
                            "counts": {"properties": {"undetermined": {"minimum": 1}}},
                        }
                    },
                    "error": {
                        "type": "object",
                        "properties": {"code": {"enum": [JUDGE_UNDETERMINED]}},
                    },
                },
            },
        },
        {
            # declared_language_transcript_ids non-empty if and only if language_source=declared.
            "if": {
                "properties": {"language_source": {"enum": ["declared"]}},
                "required": ["language_source"],
            },
            "then": {
                "required": ["declared_language_transcript_ids"],
                "properties": {"declared_language_transcript_ids": {"minItems": 1}},
            },
            "else": {"properties": {"declared_language_transcript_ids": _NO_ITEMS}},
        },
        {
            # missing_input and agent_channel_unknown are decided before the language steps and
            # before any turn is read: no language_source and no per_turn summary (A-C6).
            "if": {
                "properties": {"na_reason": {"enum": list(_RUN_LEVEL_NA_REASONS)}},
                "required": ["na_reason"],
            },
            "then": {"properties": {"language_source": _NULL, "per_turn": _NULL}},
        },
    ]
    schema["allOf"] = copy.deepcopy(rules)  # no node is shared between rules or calls


class MetricResult(HidingModel, EntityBase):
    """The result of one metric on one run (prefix ``res``; ADR 0004, ADR 0010 Decision 5).

    Rules on the result alone (each reported at the field it is about):

    - ``scored``: no ``na_reason``, ``error`` or ``missing_kinds``; exactly one of ``value`` and
      ``per_turn``, and a scored ``per_turn`` has a counted turn;
    - only a scored result has a ``value`` or ``passed``;
    - ``not_applicable``: ``na_reason`` required; no ``error``, ``scorer`` or ``judge``;
      ``missing_kinds`` non-empty if and only if ``na_reason=missing_input``; ``per_turn`` only
      when every turn was not applicable (counted 0, undetermined 0);
    - ``error``: ``error`` required; no ``na_reason`` or ``missing_kinds``; ``per_turn`` only with
      no counted turn, an undetermined turn and ``error.code=judge_undetermined`` (P-56);
    - ``declared_language_transcript_ids`` non-empty if and only if ``language_source=declared``;
    - ``language_source`` is absent when the language steps did not run (``missing_input``,
      ``agent_channel_unknown``);
    - ``per_turn`` is absent with those two run-level reasons too: no turn was read, so there are
      no turn counts (A-C6; a ``per_turn`` summary belongs to a result whose metric ran or whose
      read turns were all not applicable).

    ``transcript_ids`` (optional, additive in 0.0.1) names the transcripts the result read (ADR
    0004 Decision 6d): ``None`` (absent and ``null`` are the same) lets a bundle derive them; a
    list has at least one element, names each transcript once and keeps the producer's order. No
    rule of the result alone reads it; ``check_run_bundle`` resolves it (``[unresolved]``), requires
    it when the bundle holds several transcripts and the metric reads transcripts or is language
    dependent, and re-runs ``applicability`` over the transcripts it names (``[applicability]``).
    """

    model_config = ConfigDict(json_schema_extra=_result_conditions)

    id_prefix: ClassVar[str] = "res"

    metric: MetricDefinitionRef
    status: Annotated[str, ClosedSet(RESULT_STATUSES)]
    value: ResultValue
    passed: Annotated[bool, Strict()] | None = None
    na_reason: Annotated[str, ClosedSet(NA_REASONS)] | None = None
    missing_kinds: Annotated[
        list[InputKind],
        AfterValidator(_unique_kinds),
        Field(json_schema_extra={"uniqueItems": True}),
    ] = Field(default_factory=list)
    error: ResultError | None = None
    per_turn: PerTurnSummary | None = None
    scorer: ScorerInfo | None = None
    judge: JudgeInfo | None = None
    language_source: Annotated[str, ClosedSet(LANGUAGE_SOURCES)] | None = None
    declared_language_transcript_ids: Annotated[
        list[TranscriptId],
        AfterValidator(_unique_sorted_ids),
        Field(json_schema_extra={"uniqueItems": True}),
    ] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    transcript_ids: (
        Annotated[
            list[TranscriptId],
            AfterValidator(_unique_read_ids),
            Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _status_rules(self) -> MetricResult:
        problems: list[Problem] = []

        def problem(field: str, message: str) -> None:
            problems.append(Problem((field,), "metric_result", message))

        status = self.status
        if status == "scored":
            for field in ("na_reason", "error"):
                if getattr(self, field) is not None:
                    problem(field, f"a scored result has no {field}")
            if self.missing_kinds:
                problem("missing_kinds", "a scored result has no missing_kinds")
            if (self.value is None) == (self.per_turn is None):
                field = "value" if self.per_turn is None else "per_turn"
                problem(
                    field,
                    "a scored result has a value or, for a per_turn_share metric, a per_turn "
                    "summary; exactly one of them",
                )
            if self.per_turn is not None and self.per_turn.counted_turns == 0:
                problem("per_turn", "a scored per-turn result has at least one counted turn")
        else:
            if self.value is not None:
                problem("value", f"a {status} result has no value (never 0, false or empty)")
            if self.passed is not None:
                problem("passed", f"a {status} result has no passed")
        if status == "not_applicable":
            self._not_applicable_rules(problem)
        if status == "error":
            self._error_rules(problem)
        if bool(self.declared_language_transcript_ids) != (self.language_source == "declared"):
            problem(
                "declared_language_transcript_ids",
                "declared_language_transcript_ids are listed if and only if "
                "language_source=declared",
            )
        if self.na_reason in _RUN_LEVEL_NA_REASONS:
            if self.language_source is not None:
                problem(
                    "language_source",
                    f"na_reason={self.na_reason} is decided before the language steps, so there "
                    "is no language_source",
                )
            if self.per_turn is not None:
                problem(
                    "per_turn",
                    f"na_reason={self.na_reason} is a run-level reason, decided from the run "
                    "before any turn is read, so there is no per_turn summary",
                )
        raise_problems(type(self).__name__, problems)
        return self

    def _not_applicable_rules(self, problem: Any) -> None:
        if self.na_reason is None:
            problem("na_reason", "a not_applicable result needs an na_reason")
        for field in ("error", "scorer", "judge"):
            if getattr(self, field) is not None:
                problem(field, f"a not_applicable result has no {field} (nothing ran)")
        if bool(self.missing_kinds) != (self.na_reason == "missing_input"):
            problem(
                "missing_kinds",
                "missing_kinds are listed (non-empty) if and only if na_reason=missing_input",
            )
        per_turn = self.per_turn
        if per_turn is not None and (
            per_turn.counted_turns != 0 or per_turn.counts.undetermined != 0
        ):
            problem(
                "per_turn",
                "a not_applicable per-turn result has no counted and no undetermined turn (every "
                "turn was not applicable)",
            )

    def _error_rules(self, problem: Any) -> None:
        if self.error is None:
            problem("error", "an error result needs a structured error {code, message}")
        if self.na_reason is not None:
            problem("na_reason", "an error result has no na_reason")
        if self.missing_kinds:
            problem("missing_kinds", "an error result has no missing_kinds")
        per_turn = self.per_turn
        if per_turn is not None and (
            per_turn.counted_turns != 0
            or per_turn.counts.undetermined == 0
            or self.error is None
            or self.error.code != JUDGE_UNDETERMINED
        ):
            problem(
                "per_turn",
                "an error per-turn result has no counted turn, at least one undetermined turn and "
                f"error.code={JUDGE_UNDETERMINED} (P-56)",
            )


# ----------------------------------------------------------------------- not applicable steps


def missing_inputs(definition: MetricDefinition, run: Any) -> list[str]:
    """``definition.requires`` minus ``run.available_inputs``, in the order of ``requires``.

    Pure: it reads nothing else (not the tier, not ``detected_capabilities``, not a declaration;
    ADR 0004 Decision 2).
    """
    available = set(run.available_inputs)
    return [kind for kind in definition.requires if kind not in available]


def strongest_na_reason(reasons: Iterable[str]) -> str:
    """The first reason of ``NA_REASON_PRECEDENCE`` among ``reasons`` (``ValueError`` for none or
    for an unknown reason)."""
    found = list(reasons)
    unknown = [reason for reason in found if reason not in NA_REASON_PRECEDENCE]
    if unknown:
        raise ValueError(f"unknown not-applicable reason(s): {sorted(set(unknown))}")
    for reason in NA_REASON_PRECEDENCE:
        if reason in found:
            return reason
    raise ValueError("no not-applicable reason was given")


class EffectiveLanguage(NamedTuple):
    """The language a metric reads for some input, and where it came from."""

    language: str
    source: str  # observed or declared


class LanguageCheck(NamedTuple):
    """The language steps of ADR 0004 Decision 6e over the turns a metric reads."""

    na_reason: str | None
    language_source: str | None
    declared_language_transcript_ids: list[TranscriptId]


class Applicability(NamedTuple):
    """Whether a metric applies to a run, by the precedence of ADR 0004 Decision 4."""

    na_reason: str | None
    missing_kinds: list[str]
    language_source: str | None
    declared_language_transcript_ids: list[TranscriptId]


def _is_und(tag: str) -> bool:
    return tag.lower() == "und"


def effective_language(
    turn: Any, transcript: Any, definition: MetricDefinition
) -> EffectiveLanguage | None:
    """The language a metric reads for ``turn`` of ``transcript`` (api-surface 21.2, six steps).

    1. the turn's own tag (not ``und``) is observed;
    2. a turn with ``und`` or an unparseable raw label (``language_raw`` without a tag) has none;
    3. the transcript's tag (not ``und``) is observed;
    4. a transcript with ``und`` or an unparseable raw label has none;
    5. the transcript's declaration, for a definition that is not script-sensitive, is declared;
    6. otherwise none.

    ``turn`` may be ``None`` (no turn is read): steps 3 to 6. Configured languages are never read.
    """
    if turn is not None:
        if turn.language is not None:
            return None if _is_und(turn.language) else EffectiveLanguage(turn.language, "observed")
        if turn.language_raw is not None:
            return None
    if transcript.detected_language is not None:
        tag = transcript.detected_language
        return None if _is_und(tag) else EffectiveLanguage(tag, "observed")
    if transcript.detected_language_raw is not None:
        return None
    declaration = transcript.declared_language
    if declaration is not None and not definition.script_sensitive:
        return EffectiveLanguage(declaration.language, "declared")
    return None


def _unsupported_script(tag: str) -> bool:
    script = script_subtag(tag)
    return script is not None and script not in allowed_scripts(primary_language(tag))


def check_language(
    definition: MetricDefinition, turns: Sequence[Any], transcripts: Sequence[Any]
) -> LanguageCheck:
    """The language steps of ADR 0004 Decision 6e on all the given turns together.

    A ``language_independent`` definition has no language step: ``(None, None, [])``. Otherwise
    each turn is read with the transcript its ``transcript_id`` names (``ValueError`` if that
    transcript is not supplied); with no turn, each transcript supplies the language (steps 3 to
    6), and with neither there is no tag, so ``language_not_observed``. The reason is
    ``language_not_observed`` if any input has no effective language, else
    ``unsupported_language`` if any primary language is not covered by ``languages``, else, for a
    script-sensitive definition, ``unsupported_script`` if an observed tag names a script that is
    not a native script of its language. ``language_source`` is ``declared`` if any input used a
    declaration, else ``observed`` if any input had an effective language. For a
    ``per_turn_share`` metric the caller passes one turn at a time.
    """
    if definition.languages == LANGUAGE_INDEPENDENT:
        return LanguageCheck(None, None, [])
    by_id = {transcript.id: transcript for transcript in transcripts}
    found: list[tuple[EffectiveLanguage | None, Any]] = []
    if turns:
        for turn in turns:
            transcript = by_id.get(turn.transcript_id)
            if transcript is None:
                raise ValueError(
                    f"turn {turn.id} names transcript {turn.transcript_id}, which was not supplied"
                )
            found.append((effective_language(turn, transcript, definition), transcript))
    else:
        found = [(effective_language(None, t, definition), t) for t in transcripts]
    effective = [language for language, _transcript in found if language is not None]
    declared_ids = sorted(
        {
            transcript.id
            for language, transcript in found
            if language is not None and language.source == "declared"
        },
        key=str,
    )
    source = None
    if declared_ids:
        source = "declared"
    elif effective:
        source = "observed"
    covered = {primary_language(tag) for tag in definition.languages}
    reason = None
    if not found or len(effective) < len(found):
        reason = "language_not_observed"
    elif any(primary_language(e.language) not in covered for e in effective):
        reason = "unsupported_language"
    elif definition.script_sensitive and any(
        e.source == "observed" and _unsupported_script(e.language) for e in effective
    ):
        reason = "unsupported_script"
    return LanguageCheck(reason, source, list(declared_ids))


def applicability(
    definition: MetricDefinition,
    run: Any,
    *,
    turns: Sequence[Any] = (),
    transcripts: Sequence[Any] = (),
) -> Applicability:
    """Whether ``definition`` applies to ``run`` (ADR 0004 Decisions 3 to 6).

    Missing inputs first: ``missing_input`` with every missing kind in the order of ``requires``,
    except ``agent_channel_unknown`` (no kinds) when ``agent_audio`` is the only missing kind,
    ``audio`` is available and no channel map identifies the agent (N23, worked examples in ADR
    0004 Decision 5a). Then the language steps (``check_language``). A missing-input reason
    carries no language provenance.
    """
    missing = missing_inputs(definition, run)
    if missing:
        if (
            missing == ["agent_audio"]
            and "audio" in run.available_inputs
            and not agent_audio_available(run.channel_maps)
        ):
            return Applicability("agent_channel_unknown", [], None, [])
        return Applicability("missing_input", missing, None, [])
    check = check_language(definition, turns, transcripts)
    return Applicability(
        check.na_reason, [], check.language_source, check.declared_language_transcript_ids
    )


# ---------------------------------------------------------------- result against definition


def _value_fits(definition: MetricDefinition, value: Any) -> bool:
    value_type = definition.value_type
    if value_type == "boolean":
        return type(value) is bool
    if value_type == "numeric":
        return type(value) in (int, float)
    if value_type == "rating":
        scale = definition.scale
        return (
            type(value) is int
            and isinstance(scale, RatingScale)
            and scale.min <= value <= scale.max
        )
    if value_type == "enum":
        scale = definition.scale
        return type(value) is str and isinstance(scale, EnumScale) and value in scale.options
    return False


def check_result_against_definition(result: MetricResult, definition: MetricDefinition) -> None:
    """The rules of a result that need its definition (ADR 0010 Decisions 4a, 5, 5a and 6; ADR
    0004 Decision 6g). ``ValueError`` naming the result ID:

    - the result refers to this definition (same ``id`` and ``version``);
    - a ``per_turn_share`` definition scores through ``per_turn`` and never ``value``; any other
      aggregation has no ``per_turn``;
    - a scored value fits ``value_type`` (no coercion; a rating within the scale, an enum option);
    - ``passed`` is the definition's pass rule applied to the value (to the exact share for
      ``per_turn_share``), and ``None`` without a pass rule;
    - ``scorer`` for scored and errored results of deterministic, audio and latency definitions
      only, with the definition's ``implementation_ref``; ``judge`` required on a scored
      ``llm_judge`` result, optional on an errored one, forbidden otherwise;
    - ``language_source``: none for a language-independent definition, never ``declared`` for a
      script-sensitive one, and, for a list of languages, required for scored and errored results
      and for ``unsupported_language`` and ``unsupported_script``.
    """
    name = f"result {result.id}"
    if (result.metric.id, result.metric.version) != (definition.id, definition.version):
        raise ValueError(
            f"{name} refers to definition {result.metric.id} version {result.metric.version}, "
            f"not to {definition.id} version {definition.version}"
        )
    per_turn_metric = isinstance(definition.aggregation, PerTurnShareAggregation)
    scored = result.status == "scored"
    if per_turn_metric and (result.value is not None or (scored and result.per_turn is None)):
        raise ValueError(
            f"{name}: a per_turn_share definition scores through per_turn, never value (P-58)"
        )
    if not per_turn_metric and result.per_turn is not None:
        raise ValueError(f"{name}: only a per_turn_share definition has a per_turn summary")
    if scored and not per_turn_metric and not _value_fits(definition, result.value):
        raise ValueError(
            f"{name}: the value does not fit value_type {definition.value_type} of the definition"
        )
    _check_passed(name, result, definition, per_turn_metric)
    _check_provenance(name, result, definition)
    _check_language_source(name, result, definition)


def _check_passed(
    name: str, result: MetricResult, definition: MetricDefinition, per_turn_metric: bool
) -> None:
    rule = definition.pass_rule
    if rule is None:
        if result.passed is not None:
            raise ValueError(f"{name}: the definition has no pass rule, so passed is None (P-08)")
        return
    if result.status != "scored":
        return
    measured: Any = result.value
    if per_turn_metric:
        share = result.per_turn.share if result.per_turn is not None else None
        measured = Decimal(share) if share is not None else None
    expected = rule_passes(rule, measured)
    if result.passed is not expected:
        raise ValueError(
            f"{name}: passed must be {expected}, the definition's pass rule applied to the "
            "value (binding parameters never change it, E6)"
        )


def _check_provenance(name: str, result: MetricResult, definition: MetricDefinition) -> None:
    ran = result.status in ("scored", "error")
    if definition.kind in SCORER_KINDS and ran:
        if result.scorer is None:
            raise ValueError(f"{name}: a {definition.kind} result that ran records its scorer")
        if result.scorer.implementation_ref != definition.implementation_ref:
            raise ValueError(
                f"{name}: scorer implementation_ref {result.scorer.implementation_ref} is not the "
                f"definition's implementation_ref {definition.implementation_ref}"
            )
    elif result.scorer is not None:
        raise ValueError(f"{name}: only a deterministic, audio or latency result has a scorer")
    if definition.kind == "llm_judge":
        if result.status == "scored" and result.judge is None:
            raise ValueError(f"{name}: a scored llm_judge result records its judge (JudgeInfo)")
        if result.status == "not_applicable" and result.judge is not None:
            raise ValueError(f"{name}: a not_applicable result has no judge")
    elif result.judge is not None:
        raise ValueError(f"{name}: only an llm_judge result has a judge (JudgeInfo)")


def _check_language_source(name: str, result: MetricResult, definition: MetricDefinition) -> None:
    source = result.language_source
    if definition.languages == LANGUAGE_INDEPENDENT:
        if source is not None:
            raise ValueError(f"{name}: a language_independent definition has no language_source")
        return
    if definition.script_sensitive and source == "declared":
        raise ValueError(
            f"{name}: a script_sensitive definition never uses a declared language "
            "(language_source=declared is not possible)"
        )
    needs_source = result.status in ("scored", "error") or result.na_reason in (
        "unsupported_language",
        "unsupported_script",
    )
    if needs_source and source is None:
        raise ValueError(
            f"{name}: the language steps found a language, so language_source is required"
        )


# ------------------------------------------------------------------------------- rendering


def render_per_turn_share(result: MetricResult) -> str | None:
    """``turn share {share} (turn coverage {counted}/{total} turns)`` with the share at two
    decimals, half-even (display only). ``None`` unless the result is scored and has a share
    (P-56: the share is never shown without its turn coverage)."""
    if result.status != "scored":
        return None
    summary = result.per_turn
    if summary is None or summary.share is None:
        return None
    shown = Decimal(summary.share).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return f"turn share {shown} (turn coverage {summary.counted_turns}/{summary.total_turns} turns)"
