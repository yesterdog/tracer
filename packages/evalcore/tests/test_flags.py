# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-17: the flag interface, the confidence and the threshold (ADR 0023 rules 9 and 10; contract
docs/phase3/api-surface-batch-c.md section 6). The confidence is an uncalibrated disagreement
strength, not a probability; these tests pin its definition, its exact comparison with the
threshold (never on the rounded display value) and the Signal interface. All text is invented."""

from __future__ import annotations

import dataclasses
import inspect
import re
import signal as os_signal
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batchc_support import (  # type: ignore[import-not-found,unused-ignore]
    NORMALISER,
    align_mod,
    flags_mod,
    levenshtein_ref,
    make_alignment,
    sequence,
    spans_mod,
    tokens_mod,
)

CONFIDENCE_PATTERN = re.compile(r"^(?:0|1|0\.[0-9]{0,3}[1-9])$")
THRESHOLD_PATTERN = re.compile(r"^(?:0|1|0\.[0-9]{0,17}[1-9])$")


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
    if not hasattr(os_signal, "SIGALRM"):
        yield
        return

    def alarm(signum: int, frame: Any) -> None:
        raise TimeoutError("did not finish in 120 s")

    previous = os_signal.signal(os_signal.SIGALRM, alarm)
    os_signal.alarm(120)
    try:
        yield
    finally:
        os_signal.alarm(0)
        os_signal.signal(os_signal.SIGALRM, previous)


def _run(kinds: str, export: list[str], stt: list[str]) -> tuple[Any, Any, tuple[Any, ...]]:
    e, s = sequence(export), sequence(stt)
    return e, s, spans_mod().extract_spans(make_alignment(kinds), e, s)


# ------------------------------------------------------------------ Levenshtein


def test_levenshtein_is_the_unit_cost_distance_over_code_points() -> None:
    lev = flags_mod().levenshtein
    assert lev("kitten", "sitting") == 3
    assert lev("", "") == 0
    assert lev("", "abc") == 3
    assert lev("abc", "") == 3
    assert lev("abc", "abc") == 0
    assert lev("ab cd", "xb") == 4
    # code points: an astral character is one unit; no normalisation happens inside
    assert lev("\U0001f600", "a") == 1
    assert lev("\u00e9", "e\u0301") == 2
    assert lev("\u0915\u093e\u0933", "\u0915\u093e\u0932") == 1
    for bad in (None, b"a", 1, ["a"]):
        with pytest.raises(TypeError):
            lev(bad, "a")
        with pytest.raises(TypeError):
            lev("a", bad)


_small = st.text(alphabet="abc \u0915\u093e", max_size=12)


@HSETTINGS
@given(_small, _small)
def test_levenshtein_matches_the_reference_and_is_a_metric(a: str, b: str) -> None:
    lev = flags_mod().levenshtein
    assert lev(a, b) == levenshtein_ref(a, b) == lev(b, a)
    assert lev(a, b) <= max(len(a), len(b))
    assert (lev(a, b) == 0) == (a == b)
    assert lev(a, b) >= abs(len(a) - len(b))


def test_levenshtein_is_fast_enough_for_a_span_of_a_few_hundred_tokens() -> None:
    a = " ".join(f"w{i}" for i in range(400))
    b = " ".join(f"v{i}" for i in range(400))
    started = time.perf_counter()
    assert flags_mod().levenshtein(a, b) == 400
    assert time.perf_counter() - started < 60


# ------------------------------------------------------------------ confidence value


def test_confidence_record_and_rounding_half_to_even_to_four_digits() -> None:
    fl = flags_mod()
    assert [f.name for f in dataclasses.fields(fl.Confidence)] == ["edit_distance", "key_length"]
    table = {
        (1, 2): "0.5",
        (1, 3): "0.3333",
        (2, 3): "0.6667",
        (1, 1): "1",
        (0, 5): "0",
        (1, 8): "0.125",
        (5, 16): "0.3125",
        (1, 32): "0.0312",
        (3, 32): "0.0938",
        (1, 100000): "0",
        (99999, 100000): "1",
        (1, 20000): "0",
        (3, 20000): "0.0002",
        (5, 20000): "0.0002",
        (7, 20000): "0.0004",
        (9, 20000): "0.0004",
        (1, 16): "0.0625",
        (4, 4): "1",
        (1, 10): "0.1",
        (1, 1000): "0.001",
        (1, 9999): "0.0001",
    }
    for (ed, kl), shown in table.items():
        confidence = fl.Confidence(ed, kl)
        assert confidence.display == shown, (ed, kl)
        assert CONFIDENCE_PATTERN.fullmatch(confidence.display)
    # the record validates its integers
    for ed, kl in ((-1, 3), (4, 3), (1, 0), (0, 0), (1, -2)):
        with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
            fl.Confidence(ed, kl)
    for bad_ed, bad_kl in ((True, 3), (1.0, 3), (1, 3.0), ("1", 3), (None, 3)):
        with pytest.raises(TypeError):
            fl.Confidence(bad_ed, bad_kl)
    with pytest.raises(dataclasses.FrozenInstanceError):
        fl.Confidence(1, 2).edit_distance = 0


def test_confidence_compares_exactly_never_on_the_rounded_display_value() -> None:
    fl = flags_mod()

    def meets(ed: int, kl: int, text: str) -> bool:
        return bool(fl.Confidence(ed, kl).meets(Decimal(text)))

    # displayed as "1", but 24999 / 25000 is below 1
    assert fl.Confidence(24999, 25000).display == "1"
    assert meets(24999, 25000, "1") is False
    assert meets(24999, 25000, "0.99996") is True
    assert meets(24999, 25000, "0.99997") is False
    # 1/3: displayed 0.3333, exactly 0.3333...
    assert meets(1, 3, "0.3333") is True
    assert meets(1, 3, "0.3334") is False
    assert (
        meets(1, 3, "0.333333333333333333") is True
    )  # 18 digits: a 28-digit float-free comparison
    assert meets(1, 3, "0.333333333333333334") is False
    # equality counts (at or above): 3/10 against 0.3, 7/10 against 0.7, 1/1 against 1
    assert meets(3, 10, "0.3")
    assert meets(7, 10, "0.7")
    assert meets(1, 1, "1")
    assert meets(1, 2, "0.5")
    assert not meets(1, 10, "0.1000000000000001")
    # the extremes of the threshold range
    assert meets(0, 5, "0")
    assert meets(1, 5, "0")
    assert not meets(4, 5, "1")
    assert meets(5, 5, "1")
    with pytest.raises(TypeError):
        fl.Confidence(1, 2).meets(0.5)
    with pytest.raises(TypeError):
        fl.Confidence(1, 2).meets("0.5")


# ------------------------------------------------------------------ the shipped signal


def test_confidence_in_unit_interval_and_deterministic() -> None:
    fl = flags_mod()
    export, stt, spans = _run("smsmsm", ["abcd ef gh ij kl mn"], ["abce ef xh ij zz mn"])
    # hand values: abcd/abce 1/4 ; gh/xh 1/2 ; kl/zz 2/2
    values = [fl.STT_DISAGREEMENT.confidence(span, export, stt) for span in spans]
    assert [(v.edit_distance, v.key_length) for v in values] == [(1, 4), (1, 2), (2, 2)]
    assert [v.display for v in values] == ["0.25", "0.5", "1"]
    # deterministic: the same inputs give the same record, and the inputs are not changed
    again = [fl.STT_DISAGREEMENT.confidence(span, export, stt) for span in spans]
    assert again == values
    # the keys, not the original text: case, digit form and joiners do not count
    export, stt, spans = _run("s", ["WHATSAPP"], ["whatsapq"])
    only = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (only.edit_distance, only.key_length) == (1, 8)
    export, stt, spans = _run("s", ["\u0967\u0968\u0969\u096a"], ["1239"])
    digits = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (digits.edit_distance, digits.key_length) == (1, 4)
    # a multi-token span: the keys are joined by one SPACE ("ab cd" against "xb")
    export, stt, spans = _run("sd", ["ab cd"], ["xb"])
    joined = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (joined.edit_distance, joined.key_length) == (4, 5)
    # a one-letter variant in a long word is low, a short substituted word high (the intent)
    export, stt, spans = _run("s", ["abcdefghijk"], ["abcdefghijx"])
    assert fl.STT_DISAGREEMENT.confidence(spans[0], export, stt).display == "0.0909"
    export, stt, spans = _run("s", ["ab"], ["cd"])
    assert fl.STT_DISAGREEMENT.confidence(spans[0], export, stt).display == "1"
    # the Marathi LLA / LA pair: one letter in three
    export, stt, spans = _run("s", ["\u0915\u093e\u0933"], ["\u0915\u093e\u0932"])
    assert fl.STT_DISAGREEMENT.confidence(spans[0], export, stt).display == "0.3333"


@HSETTINGS
@given(
    st.lists(st.text(alphabet="abcd", min_size=1, max_size=5), min_size=1, max_size=6),
    st.lists(st.text(alphabet="abcd", min_size=1, max_size=5), min_size=1, max_size=6),
)
def test_confidence_follows_adr_rule_9_on_random_spans(
    export_words: list[str], stt_words: list[str]
) -> None:
    fl = flags_mod()
    export, stt = sequence([" ".join(export_words)]), sequence([" ".join(stt_words)])
    alignment = align_mod().align(export, stt)
    spans = spans_mod().extract_spans(alignment, export, stt)
    for span in spans:
        a = " ".join(t.key for t in export.tokens[span.export_token_start : span.export_token_end])
        b = " ".join(t.key for t in stt.tokens[span.stt_token_start : span.stt_token_end])
        value = fl.STT_DISAGREEMENT.confidence(span, export, stt)
        assert value.edit_distance == levenshtein_ref(a, b)
        assert value.key_length == max(len(a), len(b), 1)
        assert 0 <= value.edit_distance <= value.key_length
        assert value.edit_distance >= 1, "a span always differs"
        assert CONFIDENCE_PATTERN.fullmatch(value.display)
        exact = Decimal(value.edit_distance) / Decimal(value.key_length)
        assert abs(Decimal(value.display) - exact) <= Decimal("0.00005")
        assert value == fl.STT_DISAGREEMENT.confidence(span, export, stt)


def test_confidence_is_one_for_pure_insertion_or_deletion() -> None:
    fl = flags_mod()
    # a pure deletion: the STT key is empty, so edit distance = key length
    export, stt, spans = _run("mddm", ["aa bb cc dd"], ["aa dd"])
    value = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (value.edit_distance, value.key_length) == (5, 5)
    assert value.display == "1"
    # a pure insertion: the export key is empty
    export, stt, spans = _run("mim", ["aa bb"], ["aa xyz bb"])
    value = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (value.edit_distance, value.key_length) == (3, 3)
    assert value.display == "1"
    export, stt, spans = _run("miim", ["aa bb"], ["aa xy zzz bb"])
    value = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (value.edit_distance, value.key_length) == (6, 6)
    assert value.display == "1"
    # a one-letter token is enough
    export, stt, spans = _run("md", ["aa b"], ["aa"])
    value = fl.STT_DISAGREEMENT.confidence(spans[0], export, stt)
    assert (value.edit_distance, value.key_length) == (1, 1)


# ------------------------------------------------------------------ the threshold


def test_flag_threshold_has_no_default() -> None:
    fl = flags_mod()
    parameters = inspect.signature(fl.flag_spans).parameters
    for name in ("flag_min_confidence", "signal"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty, name
    export, stt, spans = _run("sm", ["aa bb"], ["xx bb"])
    with pytest.raises(TypeError):
        fl.flag_spans(spans, export, stt, signal=fl.STT_DISAGREEMENT)
    # no module-level default threshold, and no default threshold in any public signature
    public = [n for n in dir(fl) if not n.startswith("_")]
    assert not [
        n
        for n in public
        if re.search(r"default", n, re.I) and re.search(r"threshold|confidence|flag", n, re.I)
    ]
    for name in public:
        obj = getattr(fl, name)
        if inspect.isfunction(obj) and obj.__module__ == fl.__name__:
            for pname, parameter in inspect.signature(obj).parameters.items():
                if "confidence" in pname or "threshold" in pname:
                    assert parameter.default is inspect.Parameter.empty, (name, pname)
    assert not hasattr(fl, "FLAG_MIN_CONFIDENCE")
    assert (
        inspect.signature(fl.parse_threshold).parameters["value"].default is inspect.Parameter.empty
    )


def test_threshold_is_read_as_the_decimal_of_the_shortest_round_trip_spelling() -> None:
    fl = flags_mod()
    assert fl.parse_threshold(0.5) == Decimal("0.5")
    assert fl.parse_threshold(0.1) == Decimal("0.1") != Decimal.from_float(0.1)
    assert fl.parse_threshold(0.3) == Decimal("0.3")
    assert fl.parse_threshold(0.30000000000000004) == Decimal("0.30000000000000004")
    assert fl.parse_threshold(1e-05) == Decimal("0.00001")
    assert fl.parse_threshold(1e-18) == Decimal("0.000000000000000001")
    assert fl.parse_threshold(0) == Decimal(0)
    assert fl.parse_threshold(1) == Decimal(1)
    assert fl.parse_threshold(1.0) == Decimal(1)
    assert fl.parse_threshold(0.0) == Decimal(0)
    assert fl.parse_threshold(Decimal("0.25")) == Decimal("0.25")
    assert isinstance(fl.parse_threshold(0.5), Decimal)
    # a boolean is not a number here; text, None and bytes are not accepted either
    for bad in (True, False, "0.5", None, b"1", [0.5], (0.5,)):
        with pytest.raises(TypeError):
            fl.parse_threshold(bad)
    # out of range, not finite, or not writable in the record (more than 18 fractional digits)
    for value in (
        1.0000001,
        2,
        -1,
        -0.1,
        float("nan"),
        float("inf"),
        float("-inf"),
        1e-19,
        1e-30,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-0.5"),
        Decimal("1.000000000000000001"),
        Decimal("0.1234567890123456789"),
    ):
        with pytest.raises(fl.ThresholdError) as info:
            fl.parse_threshold(value)
        assert isinstance(info.value, ValueError)


def test_threshold_is_written_in_the_canonical_form_of_the_record_schema() -> None:
    fl = flags_mod()
    cases = {
        "0.5": "0.5",
        "0.50": "0.5",
        "1.0": "1",
        "1": "1",
        "0": "0",
        "0.0": "0",
        "0E-5": "0",
        "0.25": "0.25",
        "0.123456789012345678": "0.123456789012345678",
        "1E-18": "0.000000000000000001",
        "0.10": "0.1",
        "1.00": "1",
    }
    for given_text, expected in cases.items():
        assert fl.format_threshold(Decimal(given_text)) == expected, given_text
        assert THRESHOLD_PATTERN.fullmatch(expected)
    for bad in (Decimal("1.5"), Decimal("-0.1"), Decimal("NaN"), Decimal("0.1234567890123456789")):
        with pytest.raises(fl.ThresholdError):
            fl.format_threshold(bad)
    with pytest.raises(TypeError):
        fl.format_threshold(0.5)


@HSETTINGS
@given(st.integers(0, 10**18))
def test_threshold_round_trip_through_the_record_text(units: int) -> None:
    fl = flags_mod()
    value = Decimal(units).scaleb(-18)
    text = fl.format_threshold(value)
    assert THRESHOLD_PATTERN.fullmatch(text)
    assert Decimal(text) == value


# ------------------------------------------------------------------ flag_spans


class _Table:
    """A stub signal: a fixed confidence per span in order, under its own name."""

    def __init__(self, name: str, pairs: list[tuple[int, int]]) -> None:
        self.name = name
        self._pairs = pairs
        self.calls: list[Any] = []

    def confidence(self, span: Any, export: Any, stt: Any) -> Any:
        index = len(self.calls)
        self.calls.append(span)
        return flags_mod().Confidence(*self._pairs[index])


def test_flag_kept_iff_confidence_at_or_above_threshold() -> None:
    fl = flags_mod()
    export, stt, spans = _run(
        "smsmsmsmsm", ["a0 a1 a2 a3 a4 a5 a6 a7 a8 a9"], ["b0 a1 b2 a3 b4 a5 b6 a7 b8 a9"]
    )
    assert len(spans) == 5
    pairs = [(1, 4), (1, 3), (1, 2), (3, 4), (4, 4)]
    thresholds: list[Any] = [
        0,
        0.25,
        0.3333,
        0.34,
        0.5,
        0.75,
        0.76,
        1,
        1.0,
        0.0,
        Decimal("0.333333333333333333"),
    ]
    for threshold in thresholds:
        stub = _Table("stub_signal", pairs)
        flags = fl.flag_spans(spans, export, stt, signal=stub, flag_min_confidence=threshold)
        assert isinstance(flags, tuple), "every span is recorded"
        assert len(flags) == len(spans), "every span is recorded"
        exact = fl.parse_threshold(threshold)
        for flag, span, (ed, kl) in zip(flags, spans, pairs, strict=True):
            assert flag.span is span or flag.span == span
            assert flag.flagged is (Fraction(ed, kl) >= Fraction(exact)), (threshold, ed, kl)
            assert (flag.edit_distance, flag.key_length) == (ed, kl)
            assert flag.confidence == fl.Confidence(ed, kl).display
            assert flag.signal == "stub_signal"
    # the threshold is read as a decimal: 0.75 includes 3/4 exactly, 0.76 does not
    stub = _Table("stub_signal", pairs)
    got = [
        f.flagged for f in fl.flag_spans(spans, export, stt, signal=stub, flag_min_confidence=0.75)
    ]
    assert got == [False, False, False, True, True]
    stub = _Table("stub_signal", pairs)
    got = [
        f.flagged for f in fl.flag_spans(spans, export, stt, signal=stub, flag_min_confidence=0.76)
    ]
    assert got == [False, False, False, False, True]
    # the rounded display value never decides
    stub = _Table("stub_signal", [(24999, 25000)] * 5)
    got = [f.flagged for f in fl.flag_spans(spans, export, stt, signal=stub, flag_min_confidence=1)]
    assert got == [False] * 5
    # invalid thresholds stop before any span is scored
    for bad, kind in (
        (True, TypeError),
        ("0.5", TypeError),
        (1.5, fl.ThresholdError),
        (float("nan"), fl.ThresholdError),
    ):
        stub = _Table("stub_signal", pairs)
        with pytest.raises(kind):
            fl.flag_spans(spans, export, stt, signal=stub, flag_min_confidence=bad)
        assert stub.calls == []
    # no span, no flag
    assert fl.flag_spans((), export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=0.5) == ()


def test_span_flag_record_is_frozen_with_pinned_fields() -> None:
    fl = flags_mod()
    assert [f.name for f in dataclasses.fields(fl.SpanFlag)] == [
        "span",
        "signal",
        "edit_distance",
        "key_length",
        "confidence",
        "flagged",
        "near_chunk_boundary",
    ]
    export, stt, spans = _run("sm", ["aa bb"], ["xx bb"])
    flag = fl.flag_spans(spans, export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=0.5)[0]
    assert (flag.signal, flag.edit_distance, flag.key_length, flag.confidence, flag.flagged) == (
        "stt_disagreement",
        2,
        2,
        "1",
        True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        flag.flagged = False


_threshold_grid = st.sampled_from(
    [Decimal(n) / 20 for n in range(21)] + [Decimal("0.3333"), Decimal("0.0909")]
)


@HSETTINGS
@given(
    st.lists(st.text(alphabet="abcd", min_size=1, max_size=5), min_size=1, max_size=7),
    st.lists(st.text(alphabet="abcd", min_size=1, max_size=5), min_size=1, max_size=7),
    _threshold_grid,
)
def test_flagged_iff_exact_fraction_at_or_above_threshold_on_random_spans(
    export_words: list[str], stt_words: list[str], threshold: Decimal
) -> None:
    fl = flags_mod()
    export, stt = sequence([" ".join(export_words)]), sequence([" ".join(stt_words)])
    alignment = align_mod().align(export, stt)
    spans = spans_mod().extract_spans(alignment, export, stt)
    flags = fl.flag_spans(
        spans, export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=threshold
    )
    assert len(flags) == len(spans)
    for flag in flags:
        assert flag.flagged == (
            Fraction(flag.edit_distance, flag.key_length) >= Fraction(threshold)
        )
        assert flag.signal == "stt_disagreement"
    assert flags == fl.flag_spans(
        spans, export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=threshold
    )


# ------------------------------------------------------------------ the Signal interface


def test_signal_interface_accepts_second_signal_stub() -> None:
    fl = flags_mod()
    assert fl.SIGNAL_NAME == "stt_disagreement" == fl.STT_DISAGREEMENT.name
    assert isinstance(fl.STT_DISAGREEMENT, fl.Signal)
    stub = _Table("numeric_mismatch", [(1, 2), (2, 2)])
    assert isinstance(stub, fl.Signal), "a structural Protocol: no base class needed"
    export, stt, spans = _run("smsm", ["a0 a1 a2 a3"], ["b0 a1 b2 a3"])
    flags = fl.flag_spans(spans, export, stt, signal=stub, flag_min_confidence=0.6)
    assert [(f.signal, f.confidence, f.flagged) for f in flags] == [
        ("numeric_mismatch", "0.5", False),
        ("numeric_mismatch", "1", True),
    ]

    class NotASignal:
        name = "no_confidence_method"

    assert not isinstance(NotASignal(), fl.Signal)
    with pytest.raises(TypeError):
        fl.flag_spans(spans, export, stt, signal=NotASignal(), flag_min_confidence=0.5)
    # a name that is not a machine key is refused; a result that is not a Confidence is refused
    for bad_name in ("", "Bad Name", "UPPER", "a" * 65, "1abc", "x-y", None, 5):
        bad = _Table("placeholder", [(1, 2), (1, 2)])
        bad.name = bad_name  # type: ignore[assignment]
        with pytest.raises((TypeError, ValueError)):
            fl.flag_spans(spans, export, stt, signal=bad, flag_min_confidence=0.5)

    class Wrong:
        name = "wrong_result"

        def confidence(self, span: Any, export: Any, stt: Any) -> Any:
            return (1, 2)

    with pytest.raises(TypeError):
        fl.flag_spans(spans, export, stt, signal=Wrong(), flag_min_confidence=0.5)


# ------------------------------------------------------------------ near_chunk_boundary


def _stt(texts: list[str]) -> Any:
    return sequence(texts)


def _nearness(kinds: str, export_tokens: int, stt_texts: list[str]) -> list[bool]:
    fl = flags_mod()
    export = sequence([" ".join(f"e{i}" for i in range(export_tokens))])
    stt = _stt(stt_texts)
    spans = spans_mod().extract_spans(make_alignment(kinds), export, stt)
    flags = fl.flag_spans(spans, export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=0)
    marked = [fl.near_chunk_boundary(span, stt) for span in spans]
    assert [f.near_chunk_boundary for f in flags] == marked
    return marked


def test_near_chunk_boundary_marked() -> None:
    chunks = ["aa bb cc", "dd ee"]  # chunk 0 holds stt tokens 0..2, chunk 1 holds 3..4
    # a span with STT tokens: true iff it holds the first or the last token of a chunk
    assert _nearness("smmmm", 5, chunks) == [True]  # the first token of chunk 0
    assert _nearness("msmmm", 5, chunks) == [False]  # a middle token
    assert _nearness("mmsmm", 5, chunks) == [True]  # the last token of chunk 0
    assert _nearness("mmmsm", 5, chunks) == [True]  # the first token of chunk 1
    assert _nearness("mmmms", 5, chunks) == [True]  # the last token of chunk 1
    assert _nearness("msssm", 5, chunks) == [True]  # a span over the cut
    assert _nearness("msmsm", 5, chunks) == [False, True]
    assert _nearness("msmsm", 5, ["aa bb cc dd ee"]) == [False, False]
    # a one-token chunk: its token is both first and last
    assert _nearness("msm", 3, ["aa", "bb", "cc"]) == [True]
    # a span without STT tokens (a deletion): true iff its gap lies between two STT chunks
    assert _nearness("mmmdmm", 6, chunks) == [True]  # gap at stt index 3: between chunk 0 and 1
    assert _nearness("mdmmmm", 6, chunks) == [False]  # gap inside chunk 0
    assert _nearness("mmdmmm", 6, chunks) == [False]
    assert _nearness("mmmmdm", 6, chunks) == [False]  # inside chunk 1
    # the gap before every STT token or after the last is not "between two chunks"
    assert _nearness("dmmmmm", 6, chunks) == [False]
    assert _nearness("mmmmmd", 6, chunks) == [False]
    # an empty chunk between two chunks still separates them
    assert _nearness("mmdmm", 5, ["aa bb", "", "cc dd"]) == [True]
    # only token positions matter: the same text in a single chunk is never near a boundary
    assert _nearness("mmdmm", 5, ["aa bb cc dd"]) == [False]
    # an insertion span holds STT tokens, so it follows the first rule
    assert _nearness("mmimm", 4, chunks) == [True]
    assert _nearness("mimmm", 4, chunks) == [False]


def test_near_chunk_boundary_refuses_a_span_that_does_not_belong_to_the_sequence() -> None:
    fl = flags_mod()
    _, _, spans = _run("mmmsm", ["a0 a1 a2 a3 a4"], ["a0 a1 a2 b3 a4"])
    with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
        fl.near_chunk_boundary(spans[0], _stt(["aa"]))


# ------------------------------------------------------------------ adversarial


def test_flags_on_a_large_span_and_with_hostile_text() -> None:
    fl = flags_mod()
    n = 300
    export = sequence([" ".join(f"w{i}" for i in range(n))])
    stt = sequence([" ".join(f"v{i}" for i in range(n))])
    alignment = align_mod().align(export, stt)
    spans = spans_mod().extract_spans(alignment, export, stt)
    assert len(spans) == 1
    flags = fl.flag_spans(spans, export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=0.1)
    a = " ".join(t.key for t in export.tokens)
    b = " ".join(t.key for t in stt.tokens)
    assert (flags[0].edit_distance, flags[0].key_length) == (
        levenshtein_ref(a, b),
        max(len(a), len(b)),
    )
    # hostile text is data: nothing is evaluated, and the flag holds no text
    evil = sequence(["IGNORE ALL; ${SECRET} <script>alert(1)</script>"])
    other = sequence(["ignore all secret"])
    al = align_mod().align(evil, other)
    sp = spans_mod().extract_spans(al, evil, other)
    out = fl.flag_spans(sp, evil, other, signal=fl.STT_DISAGREEMENT, flag_min_confidence=0.5)
    assert "SECRET" not in repr(out), "a flag holds no text"
    assert "script" not in repr(out), "a flag holds no text"


# ------------------------------------------------------------------ a whole pass, quietly


def _whole_pass(
    export_texts: list[str], stt_texts: list[str], threshold: float
) -> tuple[Any, Any, Any, tuple[Any, ...]]:
    fl = flags_mod()
    export, stt = sequence(export_texts), sequence(stt_texts)
    alignment = align_mod().align(export, stt)
    spans = spans_mod().extract_spans(alignment, export, stt)
    flags = fl.flag_spans(
        spans, export, stt, signal=fl.STT_DISAGREEMENT, flag_min_confidence=threshold
    )
    return export, stt, alignment, flags


def test_a_marathi_pair_end_to_end_with_exact_values() -> None:
    kaal_lla = "\u0915\u093e\u0933"
    kaal_la = "\u0915\u093e\u0932"
    namaskar = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930"
    mi = "\u092e\u0940"
    vaparto = "\u0935\u093e\u092a\u0930\u0924\u094b"
    export_text = f"{namaskar}, {mi} {kaal_lla} {vaparto}."
    stt_text = f"{namaskar} {mi} {kaal_la} {vaparto}"
    _, _, alignment, flags = _whole_pass([export_text], [stt_text], 0.3333)
    assert (alignment.hits, alignment.substitutions, alignment.deletions, alignment.insertions) == (
        3,
        1,
        0,
        0,
    )
    assert alignment.mer == 1 / 4
    assert len(flags) == 1
    flag = flags[0]
    span = flag.span
    assert export_text[span.start : span.end] == kaal_lla
    assert (span.turn_seq, span.start, span.end) == (0, 12, 15)
    assert (flag.edit_distance, flag.key_length, flag.confidence) == (1, 3, "0.3333")
    assert flag.flagged is True
    assert flag.near_chunk_boundary is False, "one chunk, a middle token"
    # at 0.3334 the same span is recorded and not flagged
    _, _, _, above = _whole_pass([export_text], [stt_text], 0.3334)
    assert [f.flagged for f in above] == [False]
    assert above[0].span == span


def test_the_pure_functions_log_and_print_nothing(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    marker = "SECRETWORD"
    caplog.set_level(0)
    _whole_pass([f"{marker} aa bb"], [f"{marker.lower()} aa cc"], 0.5)
    refused: list[Callable[[], Any]] = [
        lambda: tokens_mod().tokenise(marker + chr(0xD800), NORMALISER),
        lambda: tokens_mod().tokenise(marker.encode(), NORMALISER),
        lambda: align_mod().align_keys([marker] * 3, [marker], max_tokens=2),
        lambda: align_mod().align_keys(marker, [marker]),
        lambda: sequence([marker, marker], first_seq=0, step=0),
    ]
    for call in refused:
        with pytest.raises((TypeError, ValueError)) as caught:
            call()
        assert marker not in str(caught.value)
    out, err = capfd.readouterr()
    assert caplog.records == []
    assert (out, err) == ("", "")
