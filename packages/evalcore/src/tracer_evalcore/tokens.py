# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Tokenizer with code-point offsets (P3-14; ADR 0023 rule 5, ADR 0025 Decisions 7 and 11).

Tokenisation runs over the original text; only the key of each token is normalised, so offsets
index the text exactly as stored. A token is found in each maximal run of token characters
(letters, marks, decimal digits) and ignorables: it starts at the first letter or decimal digit of
the run, ends after the last token character or joiner, and a run with no letter or digit has no
token. Everything else separates. One pass, linear in the length of the text.

Segment mapping (contract ambiguity 1): ADR 0023 rule 8 gives no mapping from an STT token to the
Segment that holds it. The tokens of a turn are mapped to its segments only when the segments'
token keys, concatenated, equal the turn's token keys exactly; otherwise no token of the turn is
mapped. Batch B must make the Sarvam mapper emit segments whose texts concatenate to the turn text
(up to what the normaliser ignores), or no span gets an audio range.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.normalise import check_text, get_normaliser

__all__ = [
    "TextTurn",
    "TimedSegment",
    "Token",
    "TokenSequence",
    "TurnOrderError",
    "TurnToken",
    "tokenise",
    "tokenise_turns",
]


class TurnOrderError(InputRefusedError):
    """The turns are not in strictly increasing ``seq`` order. They are never sorted."""


@dataclass(frozen=True, slots=True)
class Token:
    """A comparison key and the half-open code-point range of its text in the original."""

    key: str
    start: int
    end: int


def _check_int(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must be zero or more")


@dataclass(frozen=True, slots=True)
class TimedSegment:
    """The text of one STT segment and its bounds in milliseconds, each of which may be absent."""

    text: str
    start_ms: int | None
    end_ms: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("segment text must be a str")
        if self.start_ms is not None:
            _check_int(self.start_ms, "start_ms")
        if self.end_ms is not None:
            _check_int(self.end_ms, "end_ms")
        if self.start_ms is not None and self.end_ms is not None and self.start_ms > self.end_ms:
            raise ValueError("start_ms must not be after end_ms")


@dataclass(frozen=True, slots=True)
class TextTurn:
    """One turn: its ``seq``, its text exactly as stored and, optionally, timed segments."""

    seq: int
    text: str
    segments: tuple[TimedSegment, ...] = ()

    def __post_init__(self) -> None:
        _check_int(self.seq, "seq")
        if not isinstance(self.text, str):
            raise TypeError("turn text must be a str")
        if not isinstance(self.segments, tuple) or not all(
            isinstance(s, TimedSegment) for s in self.segments
        ):
            raise TypeError("segments must be a tuple of TimedSegment")


@dataclass(frozen=True, slots=True)
class TurnToken:
    """A token of a turn sequence: offsets index that turn's own text."""

    turn_index: int
    turn_seq: int
    key: str
    start: int
    end: int
    segment_index: int | None


@dataclass(frozen=True)
class TokenSequence:
    turns: tuple[TextTurn, ...]
    tokens: tuple[TurnToken, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(token.key for token in self.tokens)


def _scan(text: str, ignorable: frozenset[str], joiners: frozenset[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    first: int | None = None
    last = 0
    category = unicodedata.category
    for index, char in enumerate(text):
        if char in ignorable:
            if first is not None and char in joiners:
                last = index + 1
            continue
        kind = category(char)
        if kind[0] == "L" or kind == "Nd":
            if first is None:
                first = index
            last = index + 1
        elif kind[0] == "M":
            if first is not None:
                last = index + 1
        elif first is not None:
            spans.append((first, last))
            first = None
    if first is not None:
        spans.append((first, last))
    return spans


def tokenise(text: str, normaliser: str) -> tuple[Token, ...]:
    """The tokens of ``text``; offsets are code points of ``text`` itself, half-open."""
    spec = get_normaliser(normaliser)
    check_text(text)
    key = spec.key
    return tuple(
        Token(key=key(text[start:end]), start=start, end=end)
        for start, end in _scan(text, spec.ignorable, spec.joiners)
    )


def _segment_indices(
    segments: tuple[TimedSegment, ...], keys: list[str], normaliser: str
) -> list[int | None] | None:
    """The segment of each token, or ``None`` unless the segments' keys equal ``keys``."""
    owner: list[int | None] = []
    segment_keys: list[str] = []
    for index, segment in enumerate(segments):
        found = tokenise(segment.text, normaliser)
        owner.extend([index] * len(found))
        segment_keys.extend(token.key for token in found)
    return owner if segment_keys == keys else None


def tokenise_turns(turns: Sequence[TextTurn], normaliser: str) -> TokenSequence:
    """All tokens of ``turns`` in turn order, with global indices; ``seq`` must increase."""
    get_normaliser(normaliser)
    ordered = tuple(turns)
    for turn in ordered:
        if not isinstance(turn, TextTurn):
            raise TypeError("turns must hold TextTurn records")
    for index in range(1, len(ordered)):
        if ordered[index].seq <= ordered[index - 1].seq:
            raise TurnOrderError(
                f"turns must have strictly increasing seq: turn {index} has seq "
                f"{ordered[index].seq} after {ordered[index - 1].seq}"
            )
    tokens: list[TurnToken] = []
    for turn_index, turn in enumerate(ordered):
        found = tokenise(turn.text, normaliser)
        mapping = (
            _segment_indices(turn.segments, [t.key for t in found], normaliser)
            if turn.segments
            else None
        )
        for position, token in enumerate(found):
            tokens.append(
                TurnToken(
                    turn_index=turn_index,
                    turn_seq=turn.seq,
                    key=token.key,
                    start=token.start,
                    end=token.end,
                    segment_index=None if mapping is None else mapping[position],
                )
            )
    return TokenSequence(turns=ordered, tokens=tuple(tokens))
