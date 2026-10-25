# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Word alignment with unique counts, and the match error rate (P3-15; ADR 0023 rules 6 and 7).

The two key sequences are aligned by a dynamic program whose cost is the pair (edits, deletions
plus insertions), compared lexicographically and added component by component: a match costs
(0, 0), a substitution (1, 0), a deletion (1, 1) and an insertion (1, 1). The pair is kept as one
integer ``edits * scale + indels`` where ``scale`` exceeds the largest possible number of
deletions plus insertions, so integer order is the lexicographic order. Among alignments of
minimal cost the backtrace from the end prefers, at each step, a match, then a substitution, then
a deletion, then an insertion. A deletion is an export token with no STT token; an insertion is
an STT token with no export token.

Memory (contract ambiguity 9, ``docs/phase3/evidence/green/batch-c/``): the cost rows are kept two
at a time. What the backtrace needs is, for each cell, which step the backtrace takes from it. That
is one of four values, stored as one byte per cell in one ``bytearray`` per row, so the table of
two sequences of n tokens needs about n * n bytes (4 MB for n = 2000). The cap is per side and is
checked before any table is allocated.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from tracer_evalcore.errors import EvalcoreError, InputRefusedError
from tracer_evalcore.tokens import TokenSequence

__all__ = [
    "ALIGN_MAX_TOKENS",
    "AlignOp",
    "Alignment",
    "AlignmentTooLargeError",
    "OpKind",
    "UndefinedMerError",
    "align",
    "align_keys",
]

ALIGN_MAX_TOKENS = 10_000
"""Largest number of tokens on either side. Changing it changes the scorer version (ADR 0023
rule 6). The step table of two sides at the cap is about 100 MB and a pass takes seconds; see the
batch C measurement."""


class OpKind(enum.StrEnum):
    MATCH = "match"
    SUBSTITUTION = "substitution"
    DELETION = "deletion"
    INSERTION = "insertion"


class AlignmentTooLargeError(InputRefusedError):
    """A side holds more tokens than the cap. No partial alignment is ever returned."""

    def __init__(self, export_tokens: int, stt_tokens: int, max_tokens: int) -> None:
        super().__init__(
            f"alignment refused: {export_tokens} export tokens and {stt_tokens} STT tokens; "
            f"each side may hold at most {max_tokens}"
        )
        self.export_tokens = export_tokens
        self.stt_tokens = stt_tokens
        self.max_tokens = max_tokens


class UndefinedMerError(EvalcoreError):
    """The match error rate of two empty sides is undefined. Not an input the caller can fix."""


@dataclass(frozen=True, slots=True)
class AlignOp:
    kind: OpKind
    export_index: int | None
    stt_index: int | None


@dataclass(frozen=True)
class Alignment:
    ops: tuple[AlignOp, ...]
    hits: int
    substitutions: int
    deletions: int
    insertions: int

    def __post_init__(self) -> None:
        tally = dict.fromkeys(OpKind, 0)
        for op in self.ops:
            tally[op.kind] += 1
        counts = (self.hits, self.substitutions, self.deletions, self.insertions)
        if counts != (
            tally[OpKind.MATCH],
            tally[OpKind.SUBSTITUTION],
            tally[OpKind.DELETION],
            tally[OpKind.INSERTION],
        ):
            raise ValueError("the counts of an alignment must equal the tallies of its ops")

    @property
    def export_tokens(self) -> int:
        return self.hits + self.substitutions + self.deletions

    @property
    def stt_tokens(self) -> int:
        return self.hits + self.substitutions + self.insertions

    @property
    def edits(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def mer(self) -> float:
        """``edits / (hits + edits)`` as the correctly rounded quotient of two ints."""
        total = self.hits + self.edits
        if total == 0:
            raise UndefinedMerError("the match error rate of two empty sequences is undefined")
        return self.edits / total


_MATCH, _SUBSTITUTION, _DELETION, _INSERTION = 0, 1, 2, 3
_KINDS = (OpKind.MATCH, OpKind.SUBSTITUTION, OpKind.DELETION, OpKind.INSERTION)


def _check_keys(keys: Sequence[str], name: str) -> None:
    if isinstance(keys, str | bytes | bytearray) or not isinstance(keys, Sequence):
        raise TypeError(f"{name} must be a sequence of str")


def align_keys(
    export_keys: Sequence[str],
    stt_keys: Sequence[str],
    *,
    max_tokens: int = ALIGN_MAX_TOKENS,
) -> Alignment:
    """Align two key sequences; keys are compared for equality only."""
    if type(max_tokens) is not int:
        raise TypeError("max_tokens must be an int")
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    _check_keys(export_keys, "export_keys")
    _check_keys(stt_keys, "stt_keys")
    n, m = len(export_keys), len(stt_keys)
    if n > max_tokens or m > max_tokens:
        raise AlignmentTooLargeError(n, m, max_tokens)
    if not all(isinstance(k, str) for k in export_keys) or not all(
        isinstance(k, str) for k in stt_keys
    ):
        raise TypeError("keys must be str")

    # cost = edits * scale + deletions-plus-insertions; scale > n + m
    scale = n + m + 1
    sub_cost, indel_cost = scale, scale + 1
    previous = [j * indel_cost for j in range(m + 1)]
    steps: list[bytearray] = [bytearray([_INSERTION]) * (m + 1)]
    steps[0][0] = _MATCH
    for i in range(1, n + 1):
        key = export_keys[i - 1]
        row = bytearray(m + 1)
        row[0] = _DELETION
        current = [i * indel_cost]
        left = current[0]
        for j in range(1, m + 1):
            if key == stt_keys[j - 1]:
                best, step = previous[j - 1], _MATCH
            else:
                best, step = previous[j - 1] + sub_cost, _SUBSTITUTION
            up = previous[j] + indel_cost
            if up < best:
                best, step = up, _DELETION
            left += indel_cost
            if left < best:
                best, step = left, _INSERTION
            row[j] = step
            current.append(best)
            left = best
        steps.append(row)
        previous = current

    ops: list[AlignOp] = []
    i, j = n, m
    counts = [0, 0, 0, 0]
    while i > 0 or j > 0:
        step = steps[i][j]
        counts[step] += 1
        if step == _DELETION:
            i -= 1
            ops.append(AlignOp(_KINDS[step], i, None))
        elif step == _INSERTION:
            j -= 1
            ops.append(AlignOp(_KINDS[step], None, j))
        else:
            i -= 1
            j -= 1
            ops.append(AlignOp(_KINDS[step], i, j))
    ops.reverse()
    return Alignment(
        ops=tuple(ops),
        hits=counts[_MATCH],
        substitutions=counts[_SUBSTITUTION],
        deletions=counts[_DELETION],
        insertions=counts[_INSERTION],
    )


def align(
    export: TokenSequence, stt: TokenSequence, *, max_tokens: int = ALIGN_MAX_TOKENS
) -> Alignment:
    """``align_keys`` over the keys of two token sequences."""
    return align_keys(export.keys, stt.keys, max_tokens=max_tokens)
