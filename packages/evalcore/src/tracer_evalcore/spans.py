# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Disagreement spans on the export text (P3-16; ADR 0023 rule 8).

A span is a maximal run of consecutive non-match ops, split where its export tokens belong to
different export turns. A substitution or a deletion goes with its export token. An insertion goes
with the nearest export token before it in the run, or, when there is none, with the first export
token after it. A run of insertions only becomes one zero-width span at its gap. Spans lie on the
export text only; the STT side is addressed by token indices. The audio range of a span is read
from the segments that hold its STT tokens and is absent unless every one of them has a complete
timing (never extrapolated, never zero).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tracer_evalcore.align import Alignment, AlignOp, OpKind
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.tokens import TokenSequence

__all__ = ["Span", "SpanInputError", "extract_spans"]


class SpanInputError(InputRefusedError):
    """The alignment does not belong to the two token sequences. Messages hold counts only."""


@dataclass(frozen=True, slots=True)
class Span:
    """A disagreement on the export. Offsets are code points of the turn's own text."""

    turn_index: int
    turn_seq: int
    start: int
    end: int
    export_token_start: int
    export_token_end: int
    stt_token_start: int
    stt_token_end: int
    substitutions: int
    deletions: int
    insertions: int
    audio_start_ms: int | None
    audio_end_ms: int | None

    @property
    def export_tokens(self) -> int:
        return self.substitutions + self.deletions


def _audio_range(stt: TokenSequence, first: int, last: int) -> tuple[int | None, int | None]:
    """The range of the segments holding ``stt.tokens[first:last]``, or ``(None, None)``."""
    if first >= last:
        return None, None
    segments = []
    for token in stt.tokens[first:last]:
        if token.segment_index is None:
            return None, None
        segment = stt.turns[token.turn_index].segments[token.segment_index]
        if segment.start_ms is None or segment.end_ms is None:
            return None, None
        segments.append(segment)
    start, end = segments[0].start_ms, segments[-1].end_ms
    if start is None or end is None or start > end:
        return None, None
    return start, end


def _validate(alignment: Alignment, export: TokenSequence, stt: TokenSequence) -> None:
    if alignment.export_tokens != len(export.tokens) or alignment.stt_tokens != len(stt.tokens):
        raise SpanInputError(
            f"alignment covers {alignment.export_tokens} export and {alignment.stt_tokens} STT "
            f"tokens but the sequences hold {len(export.tokens)} and {len(stt.tokens)}"
        )
    e = s = 0
    for position, op in enumerate(alignment.ops):
        has_export = op.kind is not OpKind.INSERTION
        has_stt = op.kind is not OpKind.DELETION
        if (op.export_index is not None) != has_export or (op.stt_index is not None) != has_stt:
            raise SpanInputError(f"op {position} has indices that do not fit its kind")
        if has_export:
            if op.export_index != e:
                raise SpanInputError(f"op {position} does not walk the export tokens in order")
            e += 1
        if has_stt:
            if op.stt_index != s:
                raise SpanInputError(f"op {position} does not walk the STT tokens in order")
            s += 1


def extract_spans(
    alignment: Alignment, export: TokenSequence, stt: TokenSequence
) -> tuple[Span, ...]:
    """The disagreement spans of ``alignment``, in op order."""
    _validate(alignment, export, stt)
    ops = alignment.ops
    spans: list[Span] = []
    export_before = stt_before = 0
    k = 0
    while k < len(ops):
        if ops[k].kind is OpKind.MATCH:
            export_before += 1
            stt_before += 1
            k += 1
            continue
        end = k
        while end < len(ops) and ops[end].kind is not OpKind.MATCH:
            end += 1
        run = ops[k:end]
        if all(op.export_index is None for op in run):
            spans.append(_gap_span(export, stt, export_before, stt_before, len(run)))
            stt_before += len(run)
        else:
            spans.extend(_run_spans(run, export, stt, stt_before))
            export_before += sum(1 for op in run if op.export_index is not None)
            stt_before += sum(1 for op in run if op.stt_index is not None)
        k = end
    return tuple(spans)


def _gap_span(
    export: TokenSequence, stt: TokenSequence, export_before: int, stt_before: int, count: int
) -> Span:
    if not export.tokens:
        raise SpanInputError("an insertion has no export token to anchor to")
    if export_before > 0:
        anchor = export.tokens[export_before - 1]
        where = anchor.end
    else:
        anchor = export.tokens[0]
        where = anchor.start
    first, last = stt_before, stt_before + count
    audio_start, audio_end = _audio_range(stt, first, last)
    return Span(
        turn_index=anchor.turn_index,
        turn_seq=anchor.turn_seq,
        start=where,
        end=where,
        export_token_start=export_before,
        export_token_end=export_before,
        stt_token_start=first,
        stt_token_end=last,
        substitutions=0,
        deletions=0,
        insertions=count,
        audio_start_ms=audio_start,
        audio_end_ms=audio_end,
    )


def _run_spans(
    run: Sequence[AlignOp], export: TokenSequence, stt: TokenSequence, stt_before: int
) -> list[Span]:
    """Split one run that holds export tokens into one span per export turn."""
    tokens = export.tokens
    owners: list[int | None] = []
    current: int | None = None
    for op in run:
        if op.export_index is not None:
            current = tokens[op.export_index].turn_index
        owners.append(current)
    leading = next(owner for owner in owners if owner is not None)
    turn_of = [leading if owner is None else owner for owner in owners]

    spans: list[Span] = []
    begin = 0
    stt_cursor = stt_before
    for index in range(1, len(run) + 1):
        if index < len(run) and turn_of[index] == turn_of[begin]:
            continue
        members = run[begin:index]
        indices = [op.export_index for op in members if op.export_index is not None]
        first_token, last_token = tokens[indices[0]], tokens[indices[-1]]
        stt_count = sum(1 for op in members if op.stt_index is not None)
        audio_start, audio_end = _audio_range(stt, stt_cursor, stt_cursor + stt_count)
        spans.append(
            Span(
                turn_index=first_token.turn_index,
                turn_seq=first_token.turn_seq,
                start=first_token.start,
                end=last_token.end,
                export_token_start=indices[0],
                export_token_end=indices[-1] + 1,
                stt_token_start=stt_cursor,
                stt_token_end=stt_cursor + stt_count,
                substitutions=sum(1 for op in members if op.kind is OpKind.SUBSTITUTION),
                deletions=sum(1 for op in members if op.kind is OpKind.DELETION),
                insertions=sum(1 for op in members if op.kind is OpKind.INSERTION),
                audio_start_ms=audio_start,
                audio_end_ms=audio_end,
            )
        )
        stt_cursor += stt_count
        begin = index
    return spans
