# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch C tests (normaliser, tokens, alignment, spans, flags).

Contract: docs/phase3/api-surface-batch-c.md. Nothing here imports ``tracer_evalcore`` at module
level: each loader imports lazily, so that before the modules exist every test fails on its own
with ``ModuleNotFoundError`` and the directory still collects. The reference implementations
below (a unit-cost edit distance, a brute-force enumeration of every alignment, a reference
grapheme-safe tokeniser oracle for property tests) share no code with ``tracer_evalcore``.
All text is invented; non-ASCII characters are written as escapes so that this file is ASCII.
"""

from __future__ import annotations

import importlib
import itertools
import json
import re
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from hypothesis import strategies as st

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
FIXTURE = TESTS / "fixtures" / "normaliser_mr.json"
ADR23 = ROOT / "docs" / "adr" / "0023-recorded-call-agreement-scoring.md"
ADR25 = ROOT / "docs" / "adr" / "0025-marathi-comparison-normaliser.md"
SCOPE = ROOT / "docs" / "phase3" / "SCOPE.md"

NORMALISER = "mr-Deva/1"


def normalise_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.normalise")


def tokens_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.tokens")


def align_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.align")


def spans_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.spans")


def flags_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.flags")


def load_fixture() -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text("utf-8"))
    assert isinstance(data, dict)
    return data


def fixture_cases() -> list[dict[str, Any]]:
    cases = load_fixture()["cases"]
    assert isinstance(cases, list)
    return cases


# ------------------------------------------------------------------ character classes (ADR 0025)

IGNORABLE = frozenset(
    {0x00AD, 0x061C, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF}
    | set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x206A))
)
JOINERS = frozenset({0x200C, 0x200D})


def char_class(char: str) -> str:
    """'I' ignorable, 'T' token character, 'S' separator (ADR 0025 Decision 7)."""
    if ord(char) in IGNORABLE:
        return "I"
    category = unicodedata.category(char)
    if category[0] in "LM" or category == "Nd":
        return "T"
    return "S"


def is_letter_or_digit(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] == "L" or category == "Nd"


# ------------------------------------------------------------------ text strategies

# Pieces that exercise every class. Escapes only. No surrogate is in the pool.
POOL = [
    "a",
    "B",
    "z",
    "K",
    "\u00df",
    "\u0130",
    "\u212a",
    "\ufb01",
    "e\u0301",
    "\u00e9",
    "0",
    "7",
    "\u0966",
    "\u096f",
    "\u0967",
    "\uff11",
    "\u0ae7",
    "\u0915",
    "\u0916",
    "\u0933",
    "\u0932",
    "\u0921",
    "\u093c",
    "\u094d",
    "\u093e",
    "\u0902",
    "\u0901",
    "\u0903",
    "\u0958",
    "\u200d",
    "\u200c",
    "\u200b",
    "\u200e",
    "\u202e",
    "\u2066",
    "\u00ad",
    "\u2060",
    "\ufeff",
    "\u061c",
    " ",
    "\t",
    "\n",
    "\x00",
    "\x85",
    ",",
    ".",
    "\u0964",
    "\u0965",
    "\u0970",
    "-",
    "_",
    "'",
    "\u20b9",
    "%",
    "\u2460",
    "\U0001f600",
    "\U00010400",
    "\ue000",
    "\u2028",
]

mixed_text = st.lists(st.sampled_from(POOL), max_size=24).map("".join)

# Devanagari clusters (each starts with a letter or a digit) for the grapheme test.
CLUSTERS = [
    "\u0915",
    "\u0915\u093e",
    "\u0915\u094d\u0937",
    "\u0915\u094d\u0937\u093f",
    "\u091c\u093c",
    "\u091c\u093c\u0942",
    "\u0930\u094d\u200d\u092f",
    "\u0915\u094d\u200c\u0937",
    "\u0916\u0902",
    "\u0916\u0901",
    "\u0916\u0903",
    "\u0905",
    "\u0906",
    "\u0967",
    "\u0968\u0969",
    "\u0924\u094d\u0930\u094d\u200d",
    "\u0915\u200d",
]
SEPARATORS = [" ", ", ", "\u0964", "\u0965 ", "\u200b", "-", "\n", "\u0970", "\t", "\x00"]


@st.composite
def cluster_texts(draw: st.DrawFn) -> tuple[str, list[tuple[int, int]]]:
    """(text, expected token spans) built from clusters and separators; a token is a maximal
    run of consecutive clusters with no separator between them."""
    units = draw(st.lists(st.tuples(st.sampled_from(CLUSTERS), st.booleans()), max_size=10))
    leading = draw(st.sampled_from(["", " ", ", ", "\u0964"]))
    text = leading
    spans: list[tuple[int, int]] = []
    open_start: int | None = None
    for cluster, then_sep in units:
        if open_start is None:
            open_start = len(text)
        text += cluster
        if then_sep:
            spans.append((open_start, len(text)))
            open_start = None
            text += draw(st.sampled_from(SEPARATORS))
    if open_start is not None:
        spans.append((open_start, len(text)))
    return text, spans


# ------------------------------------------------------------------ reference edit distance


def levenshtein_ref(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Unit-cost Levenshtein distance, two rows."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


# ------------------------------------------------------------------ brute-force alignment

Op = tuple[str, int | None, int | None]
RANK = {"match": 0, "substitution": 1, "deletion": 2, "insertion": 3}


def all_alignments(export: Sequence[str], stt: Sequence[str]) -> Iterator[list[Op]]:
    """Every alignment (sequence of ops) of the two key lists, in an arbitrary order."""

    def walk(i: int, j: int) -> Iterator[list[Op]]:
        if i == len(export) and j == len(stt):
            yield []
            return
        if i < len(export) and j < len(stt):
            kind = "match" if export[i] == stt[j] else "substitution"
            for rest in walk(i + 1, j + 1):
                yield [(kind, i, j), *rest]
        if i < len(export):
            for rest in walk(i + 1, j):
                yield [("deletion", i, None), *rest]
        if j < len(stt):
            for rest in walk(i, j + 1):
                yield [("insertion", None, j), *rest]

    yield from walk(0, 0)


def pair_cost(ops: Sequence[Op]) -> tuple[int, int]:
    edits = sum(1 for kind, _, _ in ops if kind != "match")
    indel = sum(1 for kind, _, _ in ops if kind in ("deletion", "insertion"))
    return edits, indel


def reference_ops(export: Sequence[str], stt: Sequence[str]) -> list[Op]:
    """The ADR 0023 rule 6 answer by exhaustion: among the alignments of minimal cost pair
    (edits, deletions plus insertions) the one whose reversed op list is smallest when kinds are
    ranked match < substitution < deletion < insertion (what a backtrace from the end that
    prefers a match, then a substitution, then a deletion, then an insertion produces)."""
    best = min(all_alignments(export, stt), key=lambda ops: (pair_cost(ops), _reversed_rank(ops)))
    return best


def _reversed_rank(ops: Sequence[Op]) -> list[int]:
    return [RANK[kind] for kind, _, _ in reversed(ops)]


def minimal_alignments(export: Sequence[str], stt: Sequence[str]) -> list[list[Op]]:
    everything = list(all_alignments(export, stt))
    low = min(pair_cost(ops) for ops in everything)
    return [ops for ops in everything if pair_cost(ops) == low]


def tally(ops: Sequence[Op]) -> tuple[int, int, int, int]:
    """(H, S, D, I)."""
    return tuple(  # type: ignore[return-value]
        sum(1 for kind, _, _ in ops if kind == name)
        for name in ("match", "substitution", "deletion", "insertion")
    )


def all_sequences(alphabet: str, max_len: int) -> Iterator[tuple[str, ...]]:
    for length in range(max_len + 1):
        yield from itertools.product(alphabet, repeat=length)


# ------------------------------------------------------------------ building token sequences


def make_turns(texts: Sequence[str], *, first_seq: int = 0, step: int = 1) -> list[Any]:
    tk = tokens_mod()
    return [
        tk.TextTurn(seq=first_seq + i * step, text=text, segments=())
        for i, text in enumerate(texts)
    ]


def sequence(texts: Sequence[str], *, first_seq: int = 0, step: int = 1) -> Any:
    tk = tokens_mod()
    return tk.tokenise_turns(make_turns(texts, first_seq=first_seq, step=step), NORMALISER)


def make_alignment(kinds: Sequence[str]) -> Any:
    """An Alignment built by hand from a string of op letters, so that span tests do not depend
    on the aligner: m match, s substitution, d deletion, i insertion. Indices are consecutive."""
    al = align_mod()
    names = {
        "m": al.OpKind.MATCH,
        "s": al.OpKind.SUBSTITUTION,
        "d": al.OpKind.DELETION,
        "i": al.OpKind.INSERTION,
    }
    ops = []
    e = s = 0
    for letter in kinds:
        kind = names[letter]
        if letter in "msd":
            ei: int | None = e
            e += 1
        else:
            ei = None
        if letter in "msi":
            si: int | None = s
            s += 1
        else:
            si = None
        ops.append(al.AlignOp(kind=kind, export_index=ei, stt_index=si))
    return al.Alignment(
        ops=tuple(ops),
        hits=kinds.count("m"),
        substitutions=kinds.count("s"),
        deletions=kinds.count("d"),
        insertions=kinds.count("i"),
    )


def words(count: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


ESCAPE_FREE = re.compile(r"^[\x00-\x7f]*$")
