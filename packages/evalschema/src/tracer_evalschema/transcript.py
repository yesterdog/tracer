# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Transcripts, turns, segments and exchanges (ADR 0002 Decisions 3 and 4, ADR 0004 Decision 6g,
ADR 0005, ADR 0011, ADR 0017; P1-10b).

- ``Transcript`` (``tsc``): who produced it (``source``), the observed language
  (``detected_language`` and the provider's label ``detected_language_raw``), an optional
  user declaration of the language of an untagged import (``declared_language``, N14), the
  ``timeline_origin`` of its offsets, ``provider_call_ref`` and a required ``privacy`` marker. It
  does not embed its turns (N21).
- ``Turn`` (``turn``): a record of its own that names its transcript (``transcript_id``, N21);
  ``seq``, ``role``, optional timing, ``timing_source``, the pipeline stage (``source``), the text
  with its verified bidi flag, optional ``segments``, the observed language and its raw label.
- ``Segment``, ``WallTime``, ``TimelineOrigin`` and ``LanguageDeclaration``: value objects.
- ``Exchange`` (``exc``): one request/response cycle of a run.
- ``check_transcript(transcript, turns, exchanges=None)``: the rules that need the records of one
  transcript together (``ValueError`` naming the IDs or numbers involved).

``Transcript``, ``Turn`` and ``Segment`` hold text that is personal data on a human call, so they
hide their input in validation errors and no message inside them quotes a rejected value (ADR 0007
Decision 8 (b) and (c), N31, N42). Offsets are measured from the ``timeline_origin``; a
``wall_time`` is an annotation, never an offset (ADR 0002).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Strict, Tag, model_validator

from tracer_evalschema._constraints import ClosedSet, FullMatch
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.artifact import Confidence
from tracer_evalschema.base import EntityBase
from tracer_evalschema.ids import ArtifactId, ExternalRef, TranscriptId
from tracer_evalschema.ijson import MAX_SAFE_INTEGER
from tracer_evalschema.language import ObservedLanguageTag, SupportedLanguageNoScript
from tracer_evalschema.primitives import Ms, UtcDatetime
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.text import (
    MachineKey,
    VerbatimText,
    check_bidi_flag,
    with_computed_bidi_flag,
)

__all__ = [
    "DECLARABLE_SOURCES",
    "EXCHANGE_SOURCES",
    "GRANULARITIES",
    "ROLES",
    "TIMELINE_KINDS",
    "TIMING_SOURCES",
    "TRANSCRIPT_SOURCES",
    "TURN_SOURCES",
    "WALL_TIME_MEANINGS",
    "Exchange",
    "LanguageDeclaration",
    "Segment",
    "TimelineOrigin",
    "Transcript",
    "Turn",
    "WallTime",
    "check_transcript",
]

ROLES: Final = ("caller", "agent", "unknown")
TIMING_SOURCES: Final = ("audio_alignment", "telemetry", "observer", "none")
TURN_SOURCES: Final = (
    "our_stt",
    "agent_stt",
    "agent_llm_output",
    "agent_tts_input",
    "agent_context",
    "agent_log",
    "script",
    "human_reference",
)
TRANSCRIPT_SOURCES: Final = (
    "our_stt",
    "agent_export",
    "agent_telemetry",
    "observer",
    "script",
    "human_reference",
)
DECLARABLE_SOURCES: Final = ("agent_export", "agent_telemetry", "observer", "human_reference")
EXCHANGE_SOURCES: Final = ("audio_alignment", "telemetry", "observer", "inferred_from_turns")
GRANULARITIES: Final = ("word", "phrase", "utterance")
WALL_TIME_MEANINGS: Final = ("start", "end", "logged", "unknown")
TIMELINE_KINDS: Final = ("artifact", "telemetry", "observer")

Index = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]


# ------------------------------------------------------------------------------ value objects


class WallTime(BaseModel):
    """A wall-clock instant and what it marks (ADR 0002 Decision 4). Never an offset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    at: UtcDatetime
    meaning: Annotated[str, ClosedSet(WALL_TIME_MEANINGS)]


def _ref_member(value: Any) -> str | None:
    """N39: the member of ``TimelineOrigin.ref`` is chosen by the JSON type of the input (a
    string is our artifact ID, an object an ``ExternalRef``), not by the order of the union."""
    if isinstance(value, (str, ArtifactId)):
        return "artifact_id"
    if isinstance(value, (Mapping, ExternalRef)):
        return "external_ref"
    return None


TimelineRef = Annotated[
    Annotated[ExternalRef, Tag("external_ref")] | Annotated[ArtifactId, Tag("artifact_id")],
    Discriminator(
        _ref_member,
        custom_error_type="timeline_ref",
        custom_error_message="ref is an art_ ID (a string) or an ExternalRef object",
    ),
]


class TimelineOrigin(BaseModel):
    """What offset 0 means (ADR 0002 Decision 3): sample 0 of an artifact (``kind=artifact``,
    ``ref`` an ``art_`` ID), or the start of a telemetry trace or an observer session (``ref`` an
    ``ExternalRef`` of the source, or none)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(TIMELINE_KINDS)]
    ref: TimelineRef | None = None
    started_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _ref_fits_kind(self) -> TimelineOrigin:
        problem = None
        if self.kind == "artifact" and not isinstance(self.ref, ArtifactId):
            problem = "kind=artifact needs ref, the art_ ID of the artifact whose sample 0 is 0 ms"
        elif self.kind != "artifact" and isinstance(self.ref, ArtifactId):
            problem = f"kind={self.kind} takes an ExternalRef of the source (or no ref), not an ID"
        if problem is not None:
            raise_problems(type(self).__name__, [Problem(("ref",), "timeline_origin", problem)])
        return self


class LanguageDeclaration(BaseModel):
    """A user's declaration of the language of an untagged import (N14, ADR 0004 Decision 6g).

    Never an observation: it fills no observed field, script-sensitive metrics never use it, and
    every result that uses it says so (``language_source=declared``).

    ``language``, ``source`` and ``declared_at`` are required. ``declared_by`` (who declared it, a
    machine key without ``@``) is optional (N19, ADR 0004 Decision 6g): ``None`` (absent and
    ``null`` are the same) says nothing about the declarer, and a present value is validated as
    before. A shared or benchmark ``ExportBundle`` holds no ``declared_by`` (``[declared_by]``,
    ``bundle.check_export_bundle``); an internal export, a bare ``RunBundle`` and a bare
    ``Transcript`` may keep it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    language: SupportedLanguageNoScript
    source: Annotated[str, ClosedSet(("user_declared",))]
    declared_by: (
        Annotated[
            str,
            FullMatch(
                "^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$",
                "declared_by",
                "{value} is not a machine key without '@' ({pattern})",
            ),
        ]
        | None
    ) = None
    declared_at: UtcDatetime


def _bounds_problem(start: int | None, end: int | None) -> Problem | None:
    if start is not None and end is not None and end < start:
        return Problem(("end_ms",), "time_order", "end_ms must not be before start_ms")
    return None


class Segment(HidingModel):
    """A part of a turn with its own timing (P-28): a word, a phrase (a Sarvam phrase timestamp)
    or an utterance. The text is stored verbatim and its bidi flag is computed when omitted and
    verified when supplied (ADR 0017 Decision 2)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    granularity: Annotated[str, ClosedSet(GRANULARITIES)]
    start_ms: Ms | None = None
    end_ms: Ms | None = None
    text: VerbatimText
    confidence: Confidence | None = None
    # Never used as a default: the before-validator supplies the computed flag.
    contains_bidi_controls: Annotated[bool, Strict()] = False

    @model_validator(mode="before")
    @classmethod
    def _fill_flag(cls, data: Any) -> Any:
        return with_computed_bidi_flag(data)

    @model_validator(mode="after")
    def _rules(self) -> Segment:
        problems = [p for p in (_bounds_problem(self.start_ms, self.end_ms),) if p is not None]
        problems.extend(_bidi_problem(self.text, self.contains_bidi_controls))
        raise_problems(type(self).__name__, problems)
        return self


def _bidi_problem(text: str, flag: bool) -> list[Problem]:
    try:
        check_bidi_flag(text, flag)
    except ValueError as exc:
        return [Problem(("contains_bidi_controls",), "bidi_flag", str(exc))]
    return []


# ------------------------------------------------------------------------------------ Turn


def _turn_conditions(schema: dict[str, Any]) -> None:
    """``timing_source=none`` means no offsets (ADR 0011 I3), as JSON Schema ``if``/``then``."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"timing_source": {"enum": ["none"]}},
                "required": ["timing_source"],
            },
            "then": {"properties": {"start_ms": {"type": "null"}, "end_ms": {"type": "null"}}},
            "else": {
                "anyOf": [
                    {"required": ["start_ms"], "properties": {"start_ms": {"type": "integer"}}},
                    {"required": ["end_ms"], "properties": {"end_ms": {"type": "integer"}}},
                ]
            },
        }
    ]


class Turn(HidingModel, EntityBase):
    """One turn of a transcript (prefix ``turn``; ADR 0011 Decisions 1 to 3).

    ``seq`` orders the turns of its transcript and is unique there (``check_transcript``).
    Timing is optional: ``timing_source`` is ``none`` if and only if the turn has neither
    ``start_ms`` nor ``end_ms`` (a ``wall_time`` is not an offset). ``end_ms >= start_ms``, and a
    segment's bounds lie inside the turn's. ``language`` is any well-formed observed tag and
    ``language_raw`` the provider's label, verbatim. ``interrupted`` is true, false or unknown
    (``None``). There is no confidence on a turn (P-29).
    """

    model_config = ConfigDict(json_schema_extra=_turn_conditions)

    id_prefix: ClassVar[str] = "turn"

    transcript_id: TranscriptId
    seq: Index
    role: Annotated[str, ClosedSet(ROLES)]
    start_ms: Ms | None = None
    end_ms: Ms | None = None
    wall_time: WallTime | None = None
    timing_source: Annotated[str, ClosedSet(TIMING_SOURCES)]
    source: Annotated[str, ClosedSet(TURN_SOURCES)]
    text: VerbatimText
    segments: list[Segment] | None = None
    language: ObservedLanguageTag | None = None
    language_raw: VerbatimText | None = None
    interrupted: Annotated[bool, Strict()] | None = None
    exchange_index: Index | None = None
    # Never used as a default: the before-validator supplies the computed flag.
    contains_bidi_controls: Annotated[bool, Strict()] = False

    @model_validator(mode="before")
    @classmethod
    def _fill_flag(cls, data: Any) -> Any:
        return with_computed_bidi_flag(data)

    def _outside(self, segment: Segment) -> bool:
        """Whether a bound of ``segment`` lies before the turn's start or after its end (P-28)."""
        for bound in (segment.start_ms, segment.end_ms):
            if bound is None:
                continue
            if self.start_ms is not None and bound < self.start_ms:
                return True
            if self.end_ms is not None and bound > self.end_ms:
                return True
        return False

    @model_validator(mode="after")
    def _rules(self) -> Turn:
        problems: list[Problem] = []
        timed = self.start_ms is not None or self.end_ms is not None
        if timed and self.timing_source == "none":
            problems.append(
                Problem(
                    ("timing_source",),
                    "timing_source",
                    "timing_source=none means the turn has no start_ms and no end_ms",
                )
            )
        if not timed and self.timing_source != "none":
            problems.append(
                Problem(
                    ("timing_source",),
                    "timing_source",
                    "a turn without start_ms and end_ms has timing_source=none (a wall_time is not "
                    "an offset)",
                )
            )
        order = _bounds_problem(self.start_ms, self.end_ms)
        if order is not None:
            problems.append(order)
        if any(self._outside(segment) for segment in self.segments or ()):
            problems.append(
                Problem(
                    ("segments",),
                    "segment_bounds",
                    "a segment's bounds lie inside the turn's start_ms and end_ms",
                )
            )
        problems.extend(_bidi_problem(self.text, self.contains_bidi_controls))
        raise_problems(type(self).__name__, problems)
        return self


# -------------------------------------------------------------------------------- Exchange


class Exchange(EntityBase):
    """One request/response cycle of a run (prefix ``exc``; ADR 0011 Decision 4). Pipecat's
    ``turn_number`` maps to ``index``, never to ``Turn.seq``. ``was_interrupted`` is true, false or
    unknown (``None``)."""

    id_prefix: ClassVar[str] = "exc"

    index: Index
    started_at: UtcDatetime | None = None
    duration_ms: Ms | None = None
    was_interrupted: Annotated[bool, Strict()] | None = None
    source: Annotated[str, ClosedSet(EXCHANGE_SOURCES)]


# ------------------------------------------------------------------------------ Transcript


class Transcript(HidingModel, EntityBase):
    """A transcript of a run (prefix ``tsc``; ADR 0011 Decision 1).

    ``source`` names the producer, while each turn's ``source`` names its pipeline stage.
    ``declared_language`` is allowed only on an imported transcript (``agent_export``,
    ``agent_telemetry``, ``observer``, ``human_reference``) that carries no language label
    (``detected_language`` and ``detected_language_raw`` both absent); it is never copied into an
    observed field. ``provider_call_ref`` is the provider's request identifier (N28).
    """

    id_prefix: ClassVar[str] = "tsc"

    source: Annotated[str, ClosedSet(TRANSCRIPT_SOURCES)]
    provider: MachineKey | None = None
    model: MachineKey | None = None
    detected_language: ObservedLanguageTag | None = None
    detected_language_raw: VerbatimText | None = None
    language_confidence: Confidence | None = None
    declared_language: LanguageDeclaration | None = None
    timeline_origin: TimelineOrigin | None = None
    provider_call_ref: ExternalRef | None = None
    privacy: PrivacyMarker

    @model_validator(mode="after")
    def _declaration_only_on_untagged_imports(self) -> Transcript:
        if self.declared_language is None:
            return self
        problem = None
        if self.source not in DECLARABLE_SOURCES:
            problem = (
                "a declared_language belongs to an imported transcript (source "
                f"{', '.join(DECLARABLE_SOURCES)}); our own producers record the tag they observed"
            )
        elif self.detected_language is not None or self.detected_language_raw is not None:
            problem = (
                "a declared_language is allowed only when the transcript has no language label "
                "(detected_language and detected_language_raw both absent)"
            )
        if problem is not None:
            raise_problems(
                type(self).__name__, [Problem(("declared_language",), "declared_language", problem)]
            )
        return self


# --------------------------------------------------------------------- check_transcript


def _has_offsets(turn: Turn) -> bool:
    if turn.start_ms is not None or turn.end_ms is not None:
        return True
    return any(s.start_ms is not None or s.end_ms is not None for s in turn.segments or ())


def check_transcript(
    transcript: Transcript,
    turns: Sequence[Turn],
    exchanges: Sequence[Exchange] | None = None,
) -> None:
    """The rules that need the records of one transcript together (ADR 0002 I11, ADR 0011 I1 and
    I11). ``ValueError`` for:

    - a turn of another transcript (names the turn's transcript);
    - a ``seq`` used twice (names it);
    - offsets (on any turn or segment) without ``timeline_origin``, and a ``timeline_origin``
      without any offset;
    - when ``exchanges`` is given (the exchanges of the same run): an exchange index used twice,
      or a turn whose ``exchange_index`` names no exchange (names the index). ``None`` skips these.
    """
    seen: set[int] = set()
    offsets = False
    for turn in turns:
        if turn.transcript_id != transcript.id:
            raise ValueError(
                f"turn {turn.id} belongs to transcript {turn.transcript_id}, not to transcript "
                f"{transcript.id}"
            )
        if turn.seq in seen:
            raise ValueError(f"seq {turn.seq} is used twice in transcript {transcript.id}")
        seen.add(turn.seq)
        offsets = offsets or _has_offsets(turn)
    if offsets and transcript.timeline_origin is None:
        raise ValueError(
            f"transcript {transcript.id} has turns with offsets but no timeline_origin (offsets "
            "need a stated origin)"
        )
    if not offsets and transcript.timeline_origin is not None:
        raise ValueError(
            f"transcript {transcript.id} has a timeline_origin but no turn or segment has an offset"
        )
    if exchanges is None:
        return
    indexes: set[int] = set()
    for exchange in exchanges:
        if exchange.index in indexes:
            raise ValueError(f"exchange index {exchange.index} is used twice")
        indexes.add(exchange.index)
    for turn in turns:
        if turn.exchange_index is not None and turn.exchange_index not in indexes:
            raise ValueError(
                f"turn {turn.id} names exchange index {turn.exchange_index}, which no exchange has"
            )
