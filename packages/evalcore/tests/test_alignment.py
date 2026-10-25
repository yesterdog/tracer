# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-15: word-level alignment, the cost pair, the tie-break and the MER (ADR 0023 rules 6 and 7;
contract docs/phase3/api-surface-batch-c.md section 4). The expected op lists come from hand
computation and from ``reference_ops`` (exhaustion over every alignment), not from the aligner."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import signal
import time
import tracemalloc
from collections.abc import Iterator
from fractions import Fraction
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batchc_support import (  # type: ignore[import-not-found,unused-ignore]
    align_mod,
    all_sequences,
    levenshtein_ref,
    minimal_alignments,
    pair_cost,
    reference_ops,
    sequence,
    tally,
)

# Deterministic hypothesis profile (as Phase 1 and 2): derandomised, no shrinking, no deadline.
HSETTINGS = settings(
    max_examples=80,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


@pytest.fixture(autouse=True)
def _must_finish() -> Iterator[None]:
    """A hang must fail the test, not the CI job."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def alarm(signum: int, frame: Any) -> None:
        raise TimeoutError("the aligner did not finish in 120 s")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(120)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _ops(alignment: Any) -> list[tuple[str, int | None, int | None]]:
    return [(str(op.kind), op.export_index, op.stt_index) for op in alignment.ops]


def _counts(alignment: Any) -> tuple[int, int, int, int]:
    return (alignment.hits, alignment.substitutions, alignment.deletions, alignment.insertions)


def _align(export: list[str], stt: list[str]) -> Any:
    return align_mod().align_keys(export, stt)


# ------------------------------------------------------------------ shapes


def test_alignment_types_are_frozen_and_pinned() -> None:
    al = align_mod()
    assert [m.name for m in al.OpKind] == ["MATCH", "SUBSTITUTION", "DELETION", "INSERTION"]
    assert [m.value for m in al.OpKind] == ["match", "substitution", "deletion", "insertion"]
    assert al.OpKind.MATCH == "match", "a str enum: it serialises as its value"
    assert [f.name for f in dataclasses.fields(al.AlignOp)] == ["kind", "export_index", "stt_index"]
    assert [f.name for f in dataclasses.fields(al.Alignment)] == [
        "ops",
        "hits",
        "substitutions",
        "deletions",
        "insertions",
    ]
    result = _align(["a"], ["a"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.hits = 5
    assert isinstance(result.ops, tuple)
    assert (
        inspect.signature(al.align_keys).parameters["max_tokens"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert isinstance(al.ALIGN_MAX_TOKENS, int)
    assert not isinstance(al.ALIGN_MAX_TOKENS, bool)
    # the cap admits the measured size of P3-15 and the real calls (a few hundred words)
    assert al.ALIGN_MAX_TOKENS >= 2000
    assert inspect.signature(al.align_keys).parameters["max_tokens"].default == al.ALIGN_MAX_TOKENS


def test_alignment_counts_must_match_the_ops() -> None:
    al = align_mod()
    op = al.AlignOp(kind=al.OpKind.MATCH, export_index=0, stt_index=0)
    al.Alignment(ops=(op,), hits=1, substitutions=0, deletions=0, insertions=0)
    for counts in ((0, 0, 0, 0), (2, 0, 0, 0), (1, 1, 0, 0), (0, 1, 0, 0)):
        with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
            al.Alignment(
                ops=(op,),
                hits=counts[0],
                substitutions=counts[1],
                deletions=counts[2],
                insertions=counts[3],
            )
    with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
        al.Alignment(ops=(op,), hits=-1, substitutions=2, deletions=0, insertions=0)


# ------------------------------------------------------------------ the simple cases


def test_alignment_identical_texts_all_matches() -> None:
    alignment = _align(["a", "b", "c"], ["a", "b", "c"])
    assert _ops(alignment) == [("match", 0, 0), ("match", 1, 1), ("match", 2, 2)]
    assert _counts(alignment) == (3, 0, 0, 0)
    assert alignment.mer == 0.0
    assert isinstance(alignment.mer, float)
    # through the tokenizer, with case, punctuation, joiners and digit forms ignored by the keys
    export = sequence(
        ["\u0928\u092e\u0938\u094d\u0915\u093e\u0930, \u092e\u0940 \u0967\u0968 WhatsApp"]
    )
    stt = sequence(["\u0928\u092e\u0938\u094d\u0915\u093e\u0930 \u092e\u0940 12 whatsapp"])
    through = align_mod().align(export, stt)
    assert _counts(through) == (4, 0, 0, 0)
    assert through.mer == 0.0


def test_alignment_single_substitution_insertion_deletion() -> None:
    sub = _align(["a", "b", "c"], ["a", "x", "c"])
    assert _ops(sub) == [("match", 0, 0), ("substitution", 1, 1), ("match", 2, 2)]
    assert _counts(sub) == (2, 1, 0, 0)
    assert sub.mer == 1 / 3
    deletion = _align(["a", "b", "c"], ["a", "c"])
    assert _ops(deletion) == [("match", 0, 0), ("deletion", 1, None), ("match", 2, 1)]
    assert _counts(deletion) == (2, 0, 1, 0)
    assert deletion.mer == 1 / 3
    insertion = _align(["a", "c"], ["a", "b", "c"])
    assert _ops(insertion) == [("match", 0, 0), ("insertion", None, 1), ("match", 1, 2)]
    assert _counts(insertion) == (2, 0, 0, 1)
    assert insertion.mer == 1 / 3
    # a deletion is an EXPORT token with no STT token; an insertion an STT token with no
    # export token
    only_export = _align(["a"], [])
    assert _ops(only_export) == [("deletion", 0, None)]
    only_stt = _align([], ["a"])
    assert _ops(only_stt) == [("insertion", None, 0)]
    # a substitution has two different keys; a match two equal keys
    for op in sub.ops:
        pair = ("a", "b", "c")[op.export_index], ("a", "x", "c")[op.stt_index]
        assert (pair[0] == pair[1]) == (str(op.kind) == "match")


# ------------------------------------------------------------------ the cost pair and the tie-break


def test_alignment_tie_break_is_deterministic() -> None:
    # (edits, deletions + insertions): two substitutions beat deletion + insertion, so the counts
    # are unique. A plain edit distance ties these (both 2 edits) and would let the tie-break
    # choose between H=0,S=2 and H=1,D=1,I=1, changing the MER from 1 to 2/3.
    swapped = _align(["a", "b"], ["b", "c"])
    assert _counts(swapped) == (0, 2, 0, 0)
    assert swapped.mer == 1.0
    assert _ops(swapped) == [("substitution", 0, 0), ("substitution", 1, 1)]
    # a pair on which plain edit distance (every edit costs 1, same tie-break) gives other counts:
    # it finds H=2, D=1, I=2 (MER 3/5); the cost pair finds the unique H=1, S=2, I=1 (MER 3/4)
    pair = _align(["a", "b", "a"], ["b", "c", "a", "b"])
    assert _counts(pair) == (1, 2, 0, 1)
    assert pair.mer == 3 / 4
    assert _ops(pair) == [
        ("substitution", 0, 0),
        ("substitution", 1, 1),
        ("match", 2, 2),
        ("insertion", None, 3),
    ]
    # among alignments of minimal cost the backtrace from the end prefers a match: the LAST a
    assert _ops(_align(["a", "b", "a"], ["a"])) == [
        ("deletion", 0, None),
        ("deletion", 1, None),
        ("match", 2, 0),
    ]
    assert _ops(_align(["a"], ["a", "b", "a"])) == [
        ("insertion", None, 0),
        ("insertion", None, 1),
        ("match", 0, 2),
    ]
    assert _ops(_align(["a", "a"], ["a"])) == [("deletion", 0, None), ("match", 1, 0)]
    assert _ops(_align(["a"], ["a", "a"])) == [("insertion", None, 0), ("match", 0, 1)]
    # then a substitution before a deletion
    assert _ops(_align(["a", "b"], ["c"])) == [("deletion", 0, None), ("substitution", 1, 0)]
    assert _ops(_align(["c"], ["a", "b"])) == [("insertion", None, 0), ("substitution", 0, 1)]
    # then a deletion before an insertion (a cell reachable by both): found by exhaustion below
    # repeated calls and fresh lists give the identical op list
    first = _ops(_align(["a", "b", "a", "b"], ["b", "a", "b", "a"]))
    for _ in range(3):
        assert _ops(_align(["a", "b", "a", "b"], ["b", "a", "b", "a"])) == first


def test_alignment_equals_exhaustive_reference_on_every_small_pair() -> None:
    """Every pair of key lists over {a, b} up to length 4 and over {a, b, c} up to length 3: the
    op list equals the exhaustive reference (minimal cost pair, then the backtrace preference)
    and all minimal alignments share H, S, D and I (the uniqueness claim of ADR 0023 rule 6)."""
    al = align_mod()
    pairs = 0
    for alphabet, limit in (("ab", 4), ("abc", 3)):
        sequences = list(all_sequences(alphabet, limit))
        for export in sequences:
            for stt in sequences:
                result = al.align_keys(list(export), list(stt))
                expected = reference_ops(export, stt)
                assert _ops(result) == expected, (export, stt)
                counts = {tally(ops) for ops in minimal_alignments(export, stt)}
                assert counts == {_counts(result)}, (export, stt)
                assert pair_cost(expected)[0] == levenshtein_ref(export, stt)
                pairs += 1
    assert pairs == 31 * 31 + 40 * 40


_keys = st.lists(st.sampled_from(["a", "b", "c", "d"]), max_size=6)


@HSETTINGS
@given(_keys, _keys)
def test_alignment_ops_are_a_consistent_walk_over_both_sequences(
    export: list[str], stt: list[str]
) -> None:
    result = _align(export, stt)
    export_seen = [op.export_index for op in result.ops if op.export_index is not None]
    stt_seen = [op.stt_index for op in result.ops if op.stt_index is not None]
    assert export_seen == list(range(len(export)))
    assert stt_seen == list(range(len(stt)))
    for op in result.ops:
        kind = str(op.kind)
        assert (op.export_index is None) == (kind == "insertion")
        assert (op.stt_index is None) == (kind == "deletion")
        if kind == "match":
            assert export[op.export_index] == stt[op.stt_index]
        if kind == "substitution":
            assert export[op.export_index] != stt[op.stt_index]
    h, s, d, i = _counts(result)
    assert h + s + d == len(export)
    assert h + s + i == len(stt)
    assert s + d + i == levenshtein_ref(export, stt), "minimal edits"
    assert _ops(result) == reference_ops(export, stt)


# ------------------------------------------------------------------ MER


@HSETTINGS
@given(_keys, _keys)
def test_alignment_is_symmetric_in_mer(export: list[str], stt: list[str]) -> None:
    forward, backward = _align(export, stt), _align(stt, export)
    h, s, d, i = _counts(forward)
    assert _counts(backward) == (h, s, i, d), "swapping the texts swaps D and I and nothing else"
    if h + s + d + i:
        assert forward.mer == backward.mer


def test_alignment_is_symmetric_in_mer_on_the_documented_pairs() -> None:
    for export, stt in (
        (["a", "b"], ["b", "c"]),
        (["a", "b", "a"], ["a"]),
        (["x"], ["y", "z", "x", "w"]),
    ):
        assert _align(export, stt).mer == _align(stt, export).mer


@HSETTINGS
@given(_keys, _keys)
def test_mer_in_unit_interval_and_exact_counts(export: list[str], stt: list[str]) -> None:
    result = _align(export, stt)
    h, s, d, i = _counts(result)
    if h + s + d + i == 0:
        with pytest.raises(align_mod().UndefinedMerError):
            _ = result.mer
        return
    assert 0.0 <= result.mer <= 1.0
    # the JSON number nearest to the exact fraction (correctly rounded integer division)
    assert result.mer == (s + d + i) / (h + s + d + i)
    assert result.mer == float(Fraction(s + d + i, h + s + d + i))
    assert (result.mer == 0.0) == (export == stt)
    assert (result.mer == 1.0) == (h == 0)


def test_mer_counts_are_exact_on_a_hand_computed_pair() -> None:
    # export  a b c d e f   stt  a x c e f g : b->x substitution, d deleted, g inserted
    result = _align(list("abcdef"), list("axcefg"))
    assert _counts(result) == (4, 1, 1, 1)
    assert result.mer == 3 / 7
    assert result.export_tokens == 6
    assert result.stt_tokens == 6
    assert result.edits == 3


# ------------------------------------------------------------------ mixed scripts and digits


def test_alignment_mixed_script_latin_tokens() -> None:
    al = align_mod()
    mr = "\u092e\u0940 WhatsApp \u0935\u093e\u092a\u0930\u0924\u094b"
    same = al.align(
        sequence([mr]), sequence(["\u092e\u0940 whatsapp \u0935\u093e\u092a\u0930\u0924\u094b"])
    )
    assert _counts(same) == (3, 0, 0, 0)
    # no transliteration: the Devanagari spelling of a Latin word is a substitution
    other = al.align(
        sequence([mr]),
        sequence(
            [
                "\u092e\u0940 "
                "\u0935\u094d\u0939\u093e\u091f\u094d\u0938\u0905\u0945\u092a "
                "\u0935\u093e\u092a\u0930\u0924\u094b"
            ]
        ),
    )
    assert _counts(other) == (2, 1, 0, 0)
    # a Latin word glued to a Marathi suffix is a different token from the spaced spelling
    glued = al.align(sequence(["WhatsApp\u0935\u0930"]), sequence(["WhatsApp \u0935\u0930"]))
    assert _counts(glued) == (0, 1, 0, 1)
    # two minimal alignments (substitution then insertion, or the reverse) have the same counts;
    # the backtrace from the end prefers the substitution, so it sits last
    assert [str(op.kind) for op in glued.ops] == ["insertion", "substitution"]


def test_alignment_numbers_digits_vs_native_digits() -> None:
    al = align_mod()
    native = sequence(["\u0967\u0968\u0969\u096a \u0915\u0930"])
    ascii_ = sequence(["1234 \u0915\u0930"])
    assert _counts(al.align(native, ascii_)) == (2, 0, 0, 0), "folded in the key"
    # grouping separators split a number: 1,00,000 is three tokens and never aligns with 100000
    grouped = al.align(sequence(["1,00,000"]), sequence(["100000"]))
    assert _counts(grouped) == (0, 1, 2, 0)
    # a number word is not a number
    assert _counts(al.align(sequence(["\u090f\u0915"]), sequence(["1"]))) == (0, 1, 0, 0)
    # other decimal digits are not folded
    assert _counts(al.align(sequence(["\u0ae7"]), sequence(["1"]))) == (0, 1, 0, 0)


# ------------------------------------------------------------------ empty sides


def test_alignment_empty_side_cases() -> None:
    al = align_mod()
    both = _align([], [])
    assert both.ops == ()
    assert _counts(both) == (0, 0, 0, 0)
    with pytest.raises(al.UndefinedMerError) as info:
        _ = both.mer
    assert isinstance(info.value, importlib.import_module("tracer_evalcore.errors").EvalcoreError)
    assert not isinstance(info.value, ValueError), "not an input the caller can fix"
    only_export = _align(["a", "b"], [])
    assert _counts(only_export) == (0, 0, 2, 0)
    assert only_export.mer == 1.0
    only_stt = _align([], ["a", "b", "c"])
    assert _counts(only_stt) == (0, 0, 0, 3)
    assert only_stt.mer == 1.0
    # through token sequences: an empty text and a text of punctuation only have no token
    empty = sequence([""])
    punct = sequence([", . \u0964"])
    full = sequence(["aa bb"])
    assert _counts(al.align(empty, punct)) == (0, 0, 0, 0)
    assert _counts(al.align(full, empty)) == (0, 0, 2, 0)
    assert _counts(al.align(empty, full)) == (0, 0, 0, 2)
    # the scorer, not the aligner, refuses empty sides (ADR 0023 rule 15): no error, no zero here
    assert al.align(full, empty).mer == 1.0


# ------------------------------------------------------------------ size cap


def test_alignment_size_cap_typed_error() -> None:
    al = align_mod()
    errors = importlib.import_module("tracer_evalcore.errors")
    assert issubclass(al.AlignmentTooLargeError, errors.InputRefusedError)
    assert issubclass(al.AlignmentTooLargeError, ValueError)
    # a small cap through the keyword: exactly the cap is accepted, one more on either side is not
    keys = [f"k{i}" for i in range(5)]
    assert al.align_keys(keys, keys, max_tokens=5).mer == 0.0
    for export, stt in (([*keys, "x"], keys), (keys, [*keys, "x"]), ([*keys, "x"], [*keys, "y"])):
        with pytest.raises(al.AlignmentTooLargeError) as info:
            al.align_keys(export, stt, max_tokens=5)
        error = info.value
        assert (error.export_tokens, error.stt_tokens, error.max_tokens) == (
            len(export),
            len(stt),
            5,
        )
        assert "k0" not in str(error), "the message holds sizes, never the keys"
        assert "6" in str(error)
        assert "5" in str(error)
    # the default cap: one more than the cap on one side with a single token on the other
    cap = al.ALIGN_MAX_TOKENS
    for export, stt in (
        (["a"] * (cap + 1), ["a"]),
        (["a"], ["a"] * (cap + 1)),
        ([], ["a"] * (cap + 1)),
    ):
        with pytest.raises(al.AlignmentTooLargeError):
            al.align_keys(export, stt)
    # refused before any table is built: both sides over the cap, tiny memory peak, quick
    big = ["a"] * (cap + 1)
    other = list(big)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        with pytest.raises(al.AlignmentTooLargeError):
            al.align_keys(big, other)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024
    assert time.perf_counter() - started < 5
    # never a partial alignment: the error is the only result; the cap parameter is validated
    for bad in (True, 2.0, "5", None):
        with pytest.raises(TypeError):
            al.align_keys(["a"], ["a"], max_tokens=bad)
    for bad_value in (0, -1):
        with pytest.raises(ValueError) as refused:  # noqa: PT011 (message deliberately not pinned)
            al.align_keys([], [], max_tokens=bad_value)
        assert not isinstance(refused.value, al.AlignmentTooLargeError)
    assert al.align_keys([], [], max_tokens=1).ops == ()


def test_alignment_compares_keys_exactly() -> None:
    """Case folding and every other normalisation belong to the normaliser, not to the aligner."""
    result = _align(["A", "e\u0301"], ["a", "\u00e9"])
    assert _counts(result) == (0, 2, 0, 0)


def test_alignment_refuses_non_sequences_of_str() -> None:
    al = align_mod()
    for bad in ("abc", b"abc", 5, None, [1, 2], ["a", None]):
        with pytest.raises(TypeError):
            al.align_keys(bad, ["a"])
        with pytest.raises(TypeError):
            al.align_keys(["a"], bad)
    assert _counts(al.align_keys(("a", "b"), ("a", "b"))) == (2, 0, 0, 0), "tuples are sequences"


# ------------------------------------------------------------------ measurement


def test_alignment_measurement_2000_tokens(record_property: Any) -> None:
    """P3-15: performance measured, no threshold. The test records the time for the report and
    checks the counts, which are exact by construction (unique keys)."""
    n = 2000
    export = [f"w{i}" for i in range(n)]
    stt: list[str] = []
    for i, key in enumerate(export):
        if i % 40 == 7:  # deleted from the STT side: 50 deletions
            continue
        stt.append(f"x{i}" if i % 10 == 3 else key)  # 200 substitutions
        if i % 100 == 55:
            stt.append(f"ins{i}")  # 20 insertions
    started = time.perf_counter()
    result = _align(export, stt)
    elapsed = time.perf_counter() - started
    record_property("align_2000_tokens_seconds", f"{elapsed:.3f}")
    print(f"align 2000 x {len(stt)} tokens: {elapsed:.3f} s")
    assert _counts(result) == (1750, 200, 50, 20)
    assert result.mer == 270 / 2020
    assert 0 < elapsed < 120
