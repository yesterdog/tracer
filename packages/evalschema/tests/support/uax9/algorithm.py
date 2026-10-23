# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Rules P1-P3, X1-X10, W1-W7, N0-N2, I1-I2 and L1-L2 of UAX #9, on lists of Bidi_Class values.

Written from the UAX #9 text only (see the package docstring). Rule numbers in the comments refer
to that text. Characters removed by rule X9 are not physically removed but are skipped by every
later rule; their resolved level is ``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .ucd import Bracket

MAX_DEPTH = 125  # BD2
BRACKET_STACK_SIZE = 63  # BD16

_EMBEDDING_INITIATORS = frozenset({"LRE", "RLE", "LRO", "RLO"})
_ISOLATE_INITIATORS = frozenset({"LRI", "RLI", "FSI"})
_REMOVED_BY_X9 = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "BN"})
_NI = frozenset({"B", "S", "WS", "ON", "LRI", "RLI", "FSI", "PDI"})
_ISOLATE_FORMATTING = frozenset({"LRI", "RLI", "FSI", "PDI"})
_L1_TRAILING = frozenset({"WS", "LRI", "RLI", "FSI", "PDI"})
_STRONG_FOR_BRACKETS = {"L": "L", "R": "R", "EN": "R", "AN": "R"}
_DIRECTION_FOR_NI = {"L": "L", "R": "R", "EN": "R", "AN": "R"}


@dataclass(frozen=True)
class Resolved:
    """What the Unicode conformance files compare.

    ``paragraph_level`` is that of the first paragraph; ``levels`` has one entry per input
    character (``None`` for characters removed by X9); ``order`` lists the input indices of the
    remaining characters from visual left to right.
    """

    paragraph_level: int
    levels: list[int | None]
    order: list[int]


def resolve_classes(
    classes: Sequence[str],
    base_level: int | None = None,
    brackets: Sequence[Bracket | None] | None = None,
) -> Resolved:
    """Run the algorithm on a line that is displayed unwrapped.

    ``base_level`` is 0 or 1 for a higher-level protocol override (HL1), or ``None`` for rules
    P2 and P3. ``brackets`` gives the paired-bracket data of each character (``None`` for a
    character that is not a bracket); without it rule N0 finds no pairs.
    """
    if brackets is not None and len(brackets) != len(classes):
        raise ValueError("brackets and classes differ in length")
    levels: list[int | None] = []
    order: list[int] = []
    first_level = base_level if base_level is not None else 0
    n = len(classes)
    start = 0
    while start < n:
        end = start
        while end < n and classes[end] != "B":  # P1: the separator stays with its paragraph
            end += 1
        end = min(end + 1, n)
        part_brackets = None if brackets is None else brackets[start:end]
        para, part_levels, part_order = _resolve_paragraph(
            classes[start:end], base_level, part_brackets
        )
        if start == 0:
            first_level = para
        levels.extend(part_levels)
        order.extend(start + i for i in part_order)
        start = end
    return Resolved(first_level, levels, order)


def _first_strong_level(types: Sequence[str], start: int, end: int, match: list[int]) -> int:
    """P2 and P3 on ``types[start:end]``: 1 if the first L/AL/R skipping isolates is R/AL."""
    i = start
    while i < end:
        t = types[i]
        if t == "L":
            return 0
        if t in ("R", "AL"):
            return 1
        if t in _ISOLATE_INITIATORS:
            i = match[i] if match[i] >= 0 else end
        i += 1
    return 0


def _match_isolates(types: Sequence[str]) -> list[int]:
    """BD9: for each isolate initiator the index of its matching PDI and vice versa, else -1."""
    match = [-1] * len(types)
    open_initiators: list[int] = []
    for i, t in enumerate(types):
        if t in _ISOLATE_INITIATORS:
            open_initiators.append(i)
        elif t == "PDI" and open_initiators:
            j = open_initiators.pop()
            match[j] = i
            match[i] = j
    return match


def _resolve_paragraph(
    original: Sequence[str],
    base_level: int | None,
    brackets: Sequence[Bracket | None] | None,
) -> tuple[int, list[int | None], list[int]]:
    n = len(original)
    match = _match_isolates(original)
    para = _first_strong_level(original, 0, n, match) if base_level is None else base_level

    types, levels = _explicit_levels(original, para, match)  # X1-X8

    kept = [i for i in range(n) if original[i] not in _REMOVED_BY_X9]  # X9
    for seq, sos, eos in _isolating_run_sequences(original, levels, kept, match, para):  # X10
        _resolve_sequence(seq, sos, eos, types, levels, original, brackets)

    # L1 uses the original types. Removed characters are skipped, as if absent from the line.
    result: list[int | None] = [None] * n
    for i in kept:
        result[i] = levels[i]
    trailing = True
    for i in reversed(kept):
        t = original[i]
        if t in ("S", "B"):
            result[i] = para
            trailing = True
        elif t in _L1_TRAILING:
            if trailing:
                result[i] = para
        else:
            trailing = False
    return para, result, _reorder(result, kept)


def _next_level(current: int, odd: bool) -> int:
    if odd:
        return current + 1 if current % 2 == 0 else current + 2
    return current + 2 if current % 2 == 0 else current + 1


def _explicit_levels(
    original: Sequence[str], para: int, match: list[int]
) -> tuple[list[str], list[int]]:
    """X1-X8: explicit embedding levels, and the types after directional overrides."""
    n = len(original)
    types = list(original)
    levels = [para] * n
    # entries: (level, override "L"/"R"/None, isolate status)
    stack: list[tuple[int, str | None, bool]] = [(para, None, False)]
    overflow_isolates = 0
    overflow_embeddings = 0
    valid_isolates = 0
    for i in range(n):
        t = original[i]
        level, override, _ = stack[-1]
        if t in ("RLE", "LRE", "RLO", "LRO"):  # X2-X5
            levels[i] = level
            new = _next_level(level, t[0] == "R")
            if new <= MAX_DEPTH and overflow_isolates == 0 and overflow_embeddings == 0:
                stack.append((new, t[0] if t[2] == "O" else None, False))
            elif overflow_isolates == 0:
                overflow_embeddings += 1
        elif t in _ISOLATE_INITIATORS:  # X5a-X5c
            levels[i] = level
            if override is not None:
                types[i] = override
            if t == "FSI":
                end = match[i] if match[i] >= 0 else n
                rtl = _first_strong_level(original, i + 1, end, match) == 1
            else:
                rtl = t == "RLI"
            new = _next_level(level, rtl)
            if new <= MAX_DEPTH and overflow_isolates == 0 and overflow_embeddings == 0:
                valid_isolates += 1
                stack.append((new, None, True))
            else:
                overflow_isolates += 1
        elif t == "PDI":  # X6a
            if overflow_isolates > 0:
                overflow_isolates -= 1
            elif valid_isolates > 0:
                overflow_embeddings = 0
                while not stack[-1][2]:
                    stack.pop()
                stack.pop()
                valid_isolates -= 1
            level, override, _ = stack[-1]
            levels[i] = level
            if override is not None:
                types[i] = override
        elif t == "PDF":  # X7
            levels[i] = level
            if overflow_isolates > 0:
                pass
            elif overflow_embeddings > 0:
                overflow_embeddings -= 1
            elif not stack[-1][2] and len(stack) >= 2:
                stack.pop()
        elif t == "B":  # X8
            levels[i] = para
        elif t == "BN":
            levels[i] = level
        else:  # X6
            levels[i] = level
            if override is not None:
                types[i] = override
    return types, levels


def _isolating_run_sequences(
    original: Sequence[str],
    levels: list[int],
    kept: list[int],
    match: list[int],
    para: int,
) -> list[tuple[list[int], str, str]]:
    """BD13 and X10: each sequence as (indices, sos, eos)."""
    runs: list[list[int]] = []
    for i in kept:
        if runs and levels[runs[-1][-1]] == levels[i]:
            runs[-1].append(i)
        else:
            runs.append([i])
    run_starting_at = {run[0]: run for run in runs}
    position = {i: k for k, i in enumerate(kept)}
    sequences: list[tuple[list[int], str, str]] = []
    for run in runs:
        first = run[0]
        if original[first] == "PDI" and match[first] >= 0:
            continue  # continues the sequence of its isolate initiator
        seq = list(run)
        while original[seq[-1]] in _ISOLATE_INITIATORS and match[seq[-1]] >= 0:
            nxt = run_starting_at.get(match[seq[-1]])
            if nxt is None:
                break
            seq.extend(nxt)
        level = levels[seq[0]]
        k = position[seq[0]]
        before = levels[kept[k - 1]] if k > 0 else para
        last = seq[-1]
        k = position[last]
        if k + 1 < len(kept) and original[last] not in _ISOLATE_INITIATORS:
            after = levels[kept[k + 1]]
        else:
            after = para
        sos = "R" if max(level, before) % 2 else "L"
        eos = "R" if max(levels[last], after) % 2 else "L"
        sequences.append((seq, sos, eos))
    return sequences


def _resolve_sequence(
    seq: list[int],
    sos: str,
    eos: str,
    types: list[str],
    levels: list[int],
    original: Sequence[str],
    brackets: Sequence[Bracket | None] | None,
) -> None:
    ts = [types[i] for i in seq]
    level = levels[seq[0]]
    after_override = list(ts)

    _resolve_weak(ts, sos)
    if brackets is not None:
        _resolve_brackets(ts, after_override, seq, sos, level, brackets)
    _resolve_neutrals(ts, sos, eos, level)

    for k, i in enumerate(seq):  # I1, I2
        t = ts[k]
        if level % 2 == 0:
            levels[i] = level + (1 if t == "R" else 2 if t in ("AN", "EN") else 0)
        else:
            levels[i] = level + (1 if t in ("L", "EN", "AN") else 0)


def _resolve_weak(ts: list[str], sos: str) -> None:
    n = len(ts)
    prev = sos  # W1
    for k in range(n):
        if ts[k] == "NSM":
            ts[k] = "ON" if prev in _ISOLATE_FORMATTING else prev
        prev = ts[k]
    strong = sos  # W2
    for k in range(n):
        t = ts[k]
        if t in ("L", "R", "AL"):
            strong = t
        elif t == "EN" and strong == "AL":
            ts[k] = "AN"
    for k in range(n):  # W3
        if ts[k] == "AL":
            ts[k] = "R"
    for k in range(1, n - 1):  # W4
        t = ts[k]
        if t == "ES" and ts[k - 1] == "EN" and ts[k + 1] == "EN":
            ts[k] = "EN"
        elif t == "CS" and ts[k - 1] == ts[k + 1] and ts[k - 1] in ("EN", "AN"):
            ts[k] = ts[k - 1]
    k = 0  # W5
    while k < n:
        if ts[k] != "ET":
            k += 1
            continue
        end = k
        while end < n and ts[end] == "ET":
            end += 1
        if (k > 0 and ts[k - 1] == "EN") or (end < n and ts[end] == "EN"):
            for j in range(k, end):
                ts[j] = "EN"
        k = end
    for k in range(n):  # W6
        if ts[k] in ("ES", "ET", "CS"):
            ts[k] = "ON"
    strong = sos  # W7
    for k in range(n):
        t = ts[k]
        if t in ("L", "R"):
            strong = t
        elif t == "EN" and strong == "L":
            ts[k] = "L"


def _find_bracket_pairs(
    ts: list[str], seq: list[int], brackets: Sequence[Bracket | None]
) -> list[tuple[int, int]]:
    """BD16 on one isolating run sequence; positions are indices into ``seq``."""
    stack: list[tuple[int, int]] = []  # (key of the closing bracket, position)
    pairs: list[tuple[int, int]] = []
    for pos, i in enumerate(seq):
        b = brackets[i]
        if b is None or ts[pos] != "ON":  # BD14, BD15: the current type must be ON
            continue
        if b.is_open:
            if len(stack) == BRACKET_STACK_SIZE:
                return []
            stack.append((b.key, pos))
        else:
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == b.key:
                    pairs.append((stack[depth][1], pos))
                    del stack[depth:]
                    break
    pairs.sort()
    return pairs


def _resolve_brackets(
    ts: list[str],
    after_override: list[str],
    seq: list[int],
    sos: str,
    level: int,
    brackets: Sequence[Bracket | None],
) -> None:
    """N0."""
    embedding = "R" if level % 2 else "L"
    opposite = "L" if embedding == "R" else "R"
    for open_pos, close_pos in _find_bracket_pairs(ts, seq, brackets):
        found_embedding = found_opposite = False
        for k in range(open_pos + 1, close_pos):
            direction = _STRONG_FOR_BRACKETS.get(ts[k])
            if direction == embedding:
                found_embedding = True
                break
            if direction is not None:
                found_opposite = True
        if found_embedding:
            direction = embedding
        elif found_opposite:
            context = sos
            for k in range(open_pos - 1, -1, -1):
                prior = _STRONG_FOR_BRACKETS.get(ts[k])
                if prior is not None:
                    context = prior
                    break
            direction = opposite if context == opposite else embedding
        else:
            continue
        for pos in (open_pos, close_pos):
            ts[pos] = direction
            k = pos + 1
            while k < len(ts) and after_override[k] == "NSM":
                ts[k] = direction
                k += 1


def _resolve_neutrals(ts: list[str], sos: str, eos: str, level: int) -> None:
    """N1 and N2."""
    embedding = "R" if level % 2 else "L"
    n = len(ts)
    k = 0
    while k < n:
        if ts[k] not in _NI:
            k += 1
            continue
        end = k
        while end < n and ts[end] in _NI:
            end += 1
        before = _DIRECTION_FOR_NI[ts[k - 1]] if k > 0 else sos
        after = _DIRECTION_FOR_NI[ts[end]] if end < n else eos
        direction = before if before == after else embedding
        for j in range(k, end):
            ts[j] = direction
        k = end


def _reorder(levels: list[int | None], kept: list[int]) -> list[int]:
    """L2: reverse, from the highest level down to the lowest odd level, every contiguous run."""
    order = list(kept)
    lv = {i: levels[i] or 0 for i in kept}
    odd = [x for x in lv.values() if x % 2]
    if not odd:
        return order
    n = len(order)
    for threshold in range(max(lv.values()), min(odd) - 1, -1):
        k = 0
        while k < n:
            if lv[order[k]] < threshold:
                k += 1
                continue
            end = k
            while end < n and lv[order[end]] >= threshold:
                end += 1
            order[k:end] = order[k:end][::-1]
            k = end
    return order
