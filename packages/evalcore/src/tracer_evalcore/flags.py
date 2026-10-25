# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Flag interface, confidence and threshold (P3-17; ADR 0023 rules 9 and 10).

The confidence is an uncalibrated disagreement strength, not a probability (BL-184). A span is a
flag iff ``edit_distance >= flag_min_confidence * key_length`` in exact arithmetic; the rounded
display value never decides. The threshold has no default anywhere in this module: the owner sets
it (BL-172). Flags are raised behind the ``Signal`` interface; the only Phase 3 signal is
``stt_disagreement``.

Levenshtein distance (contract ambiguity 8): exact, bit-parallel (Myers 1999 in the form of
Hyyro 2003) over Python integers, so a span of a few thousand tokens costs a few thousand
big-integer steps instead of a quadratic table. Each side is capped at ``LEVENSHTEIN_MAX_CHARS``
code points, a technical limit with a typed error and not a result.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Protocol, runtime_checkable

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.spans import Span
from tracer_evalcore.tokens import TokenSequence

__all__ = [
    "FLAGS_FORMAT",
    "FLAGS_JSON_SCHEMA",
    "LEVENSHTEIN_MAX_CHARS",
    "SIGNAL_NAME",
    "STT_DISAGREEMENT",
    "Confidence",
    "FlagInputError",
    "LevenshteinTooLargeError",
    "Signal",
    "SpanFlag",
    "SttDisagreementSignal",
    "ThresholdError",
    "flag_spans",
    "format_threshold",
    "levenshtein",
    "near_chunk_boundary",
    "parse_threshold",
]

SIGNAL_NAME = "stt_disagreement"
FLAGS_FORMAT = "tracer.flags/1"

LEVENSHTEIN_MAX_CHARS = 200_000
"""Largest key, in code points, on either side of one distance."""

_THRESHOLD_DIGITS = 18
_SIGNAL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")

# The JSON Schema of ADR 0023, section "The tracer.flags/1 format", key for key
# (test_adr0023_flags_format_matches_json_schema compares the two).
FLAGS_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "tracer.flags/1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "format",
        "signal",
        "normaliser",
        "stt",
        "flag_min_confidence",
        "alignment",
        "tokens",
        "spans",
    ],
    "properties": {
        "format": {"type": "string", "enum": ["tracer.flags/1"]},
        "signal": {"type": "string", "enum": ["stt_disagreement"]},
        "normaliser": {"type": "string", "pattern": "^[a-z]{2,3}-[A-Z][a-z]{3}/[1-9][0-9]{0,8}$"},
        "stt": {
            "type": "object",
            "additionalProperties": False,
            "required": ["provider", "model", "model_source"],
            "properties": {
                "provider": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
                "model": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
                "model_source": {"type": "string", "enum": ["requested"]},
            },
        },
        "flag_min_confidence": {"type": "string", "pattern": "^(?:0|1|0\\.[0-9]{0,17}[1-9])$"},
        "alignment": {
            "type": "object",
            "additionalProperties": False,
            "required": ["unit", "hits", "substitutions", "deletions", "insertions"],
            "properties": {
                "unit": {"type": "string", "enum": ["word"]},
                "hits": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                "substitutions": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                "deletions": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                "insertions": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
            },
        },
        "tokens": {
            "type": "object",
            "additionalProperties": False,
            "required": ["export", "stt", "flagged_export"],
            "properties": {
                "export": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
                "stt": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
                "flagged_export": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
            },
        },
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "turn_seq",
                    "start",
                    "end",
                    "export_tokens",
                    "stt_token_start",
                    "stt_token_end",
                    "substitutions",
                    "deletions",
                    "insertions",
                    "edit_distance",
                    "key_length",
                    "confidence",
                    "flagged",
                    "near_chunk_boundary",
                ],
                "properties": {
                    "turn_seq": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "start": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "end": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "export_tokens": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "stt_token_start": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 9007199254740991,
                    },
                    "stt_token_end": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "substitutions": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "deletions": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "insertions": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                    "edit_distance": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
                    "key_length": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
                    "confidence": {"type": "string", "pattern": "^(?:0|1|0\\.[0-9]{0,3}[1-9])$"},
                    "flagged": {"type": "boolean"},
                    "near_chunk_boundary": {"type": "boolean"},
                    "audio_start_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 9007199254740991,
                    },
                    "audio_end_ms": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                },
                "dependentRequired": {
                    "audio_start_ms": ["audio_end_ms"],
                    "audio_end_ms": ["audio_start_ms"],
                },
            },
        },
    },
}


class FlagInputError(InputRefusedError):
    """A span or a sequence does not fit the call. Messages hold counts and indices only."""


class ThresholdError(InputRefusedError):
    """The threshold is not a finite number in [0, 1] with at most 18 fractional digits."""


class LevenshteinTooLargeError(InputRefusedError):
    """A key is longer than ``LEVENSHTEIN_MAX_CHARS``. The message holds the sizes."""


def levenshtein(a: str, b: str) -> int:
    """Unit-cost edit distance over code points, no normalisation (exact, bit-parallel)."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise TypeError("levenshtein compares two str")
    if len(a) > LEVENSHTEIN_MAX_CHARS or len(b) > LEVENSHTEIN_MAX_CHARS:
        raise LevenshteinTooLargeError(
            f"keys of {len(a)} and {len(b)} code points; each may hold at most "
            f"{LEVENSHTEIN_MAX_CHARS}"
        )
    if len(a) < len(b):
        a, b = b, a
    pattern, text = a, b
    if not text:
        return len(pattern)
    m = len(pattern)
    mask = (1 << m) - 1
    last = 1 << (m - 1)
    occurrences: dict[str, int] = {}
    for position, char in enumerate(pattern):
        occurrences[char] = occurrences.get(char, 0) | (1 << position)
    vertical_plus, vertical_minus = mask, 0
    score = m
    for char in text:
        eq = occurrences.get(char, 0)
        xv = eq | vertical_minus
        xh = (((eq & vertical_plus) + vertical_plus) ^ vertical_plus) | eq
        horizontal_plus = (vertical_minus | ~(xh | vertical_plus)) & mask
        horizontal_minus = vertical_plus & xh
        if horizontal_plus & last:
            score += 1
        elif horizontal_minus & last:
            score -= 1
        horizontal_plus = ((horizontal_plus << 1) | 1) & mask
        horizontal_minus = (horizontal_minus << 1) & mask
        vertical_plus = (horizontal_minus | ~(xv | horizontal_plus)) & mask
        vertical_minus = horizontal_plus & xv
    return score


def _check_count(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int")
    return value


@dataclass(frozen=True, slots=True)
class Confidence:
    """``edit_distance / key_length``, kept as the two integers; the decimal is for display."""

    edit_distance: int
    key_length: int

    def __post_init__(self) -> None:
        _check_count(self.edit_distance, "edit_distance")
        _check_count(self.key_length, "key_length")
        if self.key_length < 1:
            raise ValueError("key_length must be at least 1")
        if not 0 <= self.edit_distance <= self.key_length:
            raise ValueError("edit_distance must be between 0 and key_length")

    @property
    def display(self) -> str:
        """The fraction rounded half to even to 4 fractional digits, in canonical form."""
        quotient, remainder = divmod(self.edit_distance * 10_000, self.key_length)
        twice = 2 * remainder
        if twice > self.key_length or (twice == self.key_length and quotient % 2 == 1):
            quotient += 1
        return _canonical(quotient, 4)

    def meets(self, threshold: Decimal) -> bool:
        """Exactly ``edit_distance / key_length >= threshold``; never uses the display value."""
        if not isinstance(threshold, Decimal):
            raise TypeError("threshold must be a Decimal")
        if not threshold.is_finite():
            raise ThresholdError("threshold must be finite")
        return Fraction(self.edit_distance, self.key_length) >= Fraction(threshold)


def _canonical(units: int, digits: int) -> str:
    """``units / 10**digits`` (0 <= value <= 1) with no sign, exponent or trailing zero."""
    if units == 0:
        return "0"
    if units == 10**digits:
        return "1"
    return "0." + str(units).zfill(digits).rstrip("0")


@runtime_checkable
class Signal(Protocol):
    """A source of flags: a machine-key ``name`` and a confidence per span."""

    @property
    def name(self) -> str: ...

    def confidence(self, span: Span, export: TokenSequence, stt: TokenSequence) -> Confidence: ...


def _check_span_ranges(span: Span, export: TokenSequence, stt: TokenSequence) -> None:
    if not (
        0 <= span.export_token_start <= span.export_token_end <= len(export.tokens)
        and 0 <= span.stt_token_start <= span.stt_token_end <= len(stt.tokens)
    ):
        raise FlagInputError(
            f"span ranges [{span.export_token_start}, {span.export_token_end}) and "
            f"[{span.stt_token_start}, {span.stt_token_end}) do not fit sequences of "
            f"{len(export.tokens)} and {len(stt.tokens)} tokens"
        )


class SttDisagreementSignal:
    """Rule 9: the Levenshtein distance of the joined keys of the span over the longer key."""

    name = SIGNAL_NAME

    def confidence(self, span: Span, export: TokenSequence, stt: TokenSequence) -> Confidence:
        _check_span_ranges(span, export, stt)
        export_key = " ".join(
            t.key for t in export.tokens[span.export_token_start : span.export_token_end]
        )
        stt_key = " ".join(t.key for t in stt.tokens[span.stt_token_start : span.stt_token_end])
        return Confidence(
            edit_distance=levenshtein(export_key, stt_key),
            key_length=max(len(export_key), len(stt_key), 1),
        )


STT_DISAGREEMENT = SttDisagreementSignal()


def _fractional_digits(value: Decimal) -> int:
    _, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int) or exponent >= 0:
        return 0
    trailing = 0
    for digit in reversed(digits):
        if digit != 0 or trailing == -exponent:
            break
        trailing += 1
    if trailing == len(digits):
        return 0
    return -exponent - trailing


def _check_threshold(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ThresholdError("threshold must be a finite number")
    if value < 0 or value > 1:
        raise ThresholdError("threshold must be in [0, 1]")
    if _fractional_digits(value) > _THRESHOLD_DIGITS:
        raise ThresholdError(
            f"threshold has more than {_THRESHOLD_DIGITS} fractional digits; "
            "the record cannot write it"
        )
    return Decimal(0) if value == 0 else value


def parse_threshold(value: int | float | Decimal) -> Decimal:
    """The decimal of the shortest round-trip spelling of ``value`` (``Decimal(repr(0.1))``)."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise TypeError("threshold must be an int, a float or a Decimal")
    if isinstance(value, float):
        return _check_threshold(Decimal(repr(value)))
    if isinstance(value, int):
        return _check_threshold(Decimal(value))
    return _check_threshold(value)


def format_threshold(threshold: Decimal) -> str:
    """The canonical string of the record: ``^(?:0|1|0\\.[0-9]{0,17}[1-9])$``."""
    if not isinstance(threshold, Decimal):
        raise TypeError("threshold must be a Decimal")
    checked = _check_threshold(threshold)
    units = Fraction(checked) * 10**_THRESHOLD_DIGITS
    return _canonical(int(units), _THRESHOLD_DIGITS)


@dataclass(frozen=True, slots=True)
class SpanFlag:
    """One span with its confidence and whether it is a flag. Holds no text."""

    span: Span
    signal: str
    edit_distance: int
    key_length: int
    confidence: str
    flagged: bool
    near_chunk_boundary: bool


def near_chunk_boundary(span: Span, stt: TokenSequence) -> bool:
    """Rule 10, by token position only: does the span touch the cut between two STT turns?

    With STT tokens: true iff they include the first or last token of an STT turn. Without
    (a pure deletion): true iff its gap lies strictly between two STT turns; the two edges of the
    recording are not a cut.
    """
    tokens = stt.tokens
    start, end = span.stt_token_start, span.stt_token_end
    if not 0 <= start <= end <= len(tokens):
        raise FlagInputError(
            f"STT token range [{start}, {end}) does not fit a sequence of {len(tokens)} tokens"
        )
    if start == end:
        return 0 < start < len(tokens) and tokens[start - 1].turn_index != tokens[start].turn_index
    for index in range(start, end):
        turn = tokens[index].turn_index
        if index == 0 or tokens[index - 1].turn_index != turn:
            return True
        if index == len(tokens) - 1 or tokens[index + 1].turn_index != turn:
            return True
    return False


def _check_signal(signal: object) -> str:
    if not isinstance(signal, Signal):
        raise TypeError("signal must have a name and a confidence(span, export, stt) method")
    name = signal.name
    if not isinstance(name, str):
        raise TypeError("signal name must be a str")
    if _SIGNAL_NAME.fullmatch(name) is None:
        raise ValueError("signal name must match ^[a-z][a-z0-9_]{0,63}$")
    return name


def flag_spans(
    spans: Sequence[Span],
    export: TokenSequence,
    stt: TokenSequence,
    *,
    signal: Signal,
    flag_min_confidence: int | float | Decimal,
) -> tuple[SpanFlag, ...]:
    """One ``SpanFlag`` per span, flagged or not, in span order.

    The signal and the threshold are checked before any span is scored; neither has a default.
    """
    name = _check_signal(signal)
    threshold = parse_threshold(flag_min_confidence)
    flags: list[SpanFlag] = []
    for span in spans:
        confidence = signal.confidence(span, export, stt)
        if not isinstance(confidence, Confidence):
            raise TypeError("a signal must return a Confidence")
        flags.append(
            SpanFlag(
                span=span,
                signal=name,
                edit_distance=confidence.edit_distance,
                key_length=confidence.key_length,
                confidence=confidence.display,
                flagged=confidence.meets(threshold),
                near_chunk_boundary=near_chunk_boundary(span, stt),
            )
        )
    return tuple(flags)
