# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-16: disagreement spans on the export text (ADR 0023 rule 8; contract
docs/phase3/api-surface-batch-c.md section 5). Alignments are built by hand (``make_alignment``)
for the documented cases, so that these tests do not depend on the aligner's tie-break; the
property tests use the real aligner and compare with a reference written here."""

from __future__ import annotations

import dataclasses
import itertools
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batchc_support import (  # type: ignore[import-not-found,unused-ignore]
    NORMALISER,
    align_mod,
    make_alignment,
    sequence,
    spans_mod,
    tokens_mod,
)

# Deterministic hypothesis profile (as Phase 1 and 2): derandomised, no shrinking, no deadline.
HSETTINGS = settings(
    max_examples=80,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def _spans(kinds: str, export: list[str], stt: list[str]) -> list[Any]:
    result = spans_mod().extract_spans(make_alignment(kinds), sequence(export), sequence(stt))
    assert isinstance(result, tuple)
    return list(result)


def _shape(span: Any) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        span.turn_seq,
        span.start,
        span.end,
        span.export_token_start,
        span.export_token_end,
        span.stt_token_start,
        span.stt_token_end,
        span.substitutions + span.deletions,
        span.insertions,
    )


def test_span_is_a_frozen_record_with_pinned_fields() -> None:
    sp = spans_mod()
    assert [f.name for f in dataclasses.fields(sp.Span)] == [
        "turn_index",
        "turn_seq",
        "start",
        "end",
        "export_token_start",
        "export_token_end",
        "stt_token_start",
        "stt_token_end",
        "substitutions",
        "deletions",
        "insertions",
        "audio_start_ms",
        "audio_end_ms",
    ]
    span = _spans("s", ["aa"], ["bb"])[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.start = 3
    assert span.export_tokens == span.substitutions + span.deletions == 1
    assert issubclass(sp.SpanInputError, ValueError)


# ------------------------------------------------------------------ I4: maximal runs


def test_spans_are_maximal_non_match_runs() -> None:
    spans = _spans("mssmmm", ["aa bb cc dd ee ff"], ["aa x1 x2 dd ee ff"])
    assert len(spans) == 1
    span = spans[0]
    assert (span.turn_seq, span.start, span.end) == (0, 3, 8)  # "bb cc"
    assert (span.export_token_start, span.export_token_end) == (1, 3)
    assert (span.stt_token_start, span.stt_token_end) == (1, 3)
    assert (span.substitutions, span.deletions, span.insertions) == (2, 0, 0)
    # a substitution, a deletion and an insertion in one run are ONE span, not three
    mixed = _spans("msdimm", ["aa bb cc dd ee"], ["aa x1 y1 dd ee"])
    assert len(mixed) == 1
    assert (mixed[0].substitutions, mixed[0].deletions, mixed[0].insertions) == (1, 1, 1)
    assert mixed[0].export_tokens == 2
    assert (mixed[0].stt_token_start, mixed[0].stt_token_end) == (1, 3)
    # two runs separated by one match are two spans
    two = _spans("msmsm", ["aa bb cc dd ee"], ["aa x1 cc x2 ee"])
    assert [_shape(s) for s in two] == [
        (0, 3, 5, 1, 2, 1, 2, 1, 0),
        (0, 9, 11, 3, 4, 3, 4, 1, 0),
    ]
    # an alignment with no disagreement has no span; nothing is invented
    assert _spans("mmm", ["aa bb cc"], ["aa bb cc"]) == []
    # a deletion has an empty STT range at its position and no audio range
    gone = _spans("mdm", ["aa bb cc"], ["aa cc"])
    assert [_shape(s) for s in gone] == [(0, 3, 5, 1, 2, 1, 1, 1, 0)]
    assert (gone[0].audio_start_ms, gone[0].audio_end_ms) == (None, None)


def test_spans_never_cross_turn_boundaries() -> None:
    # one non-match run over the last token of turn 0 and the first token of turn 1
    spans = _spans("mssm", ["aa bb", "cc dd"], ["aa x1 x2 dd"])
    assert [_shape(s) for s in spans] == [
        (0, 3, 5, 1, 2, 1, 2, 1, 0),
        (1, 0, 2, 2, 3, 2, 3, 1, 0),
    ]
    assert [s.turn_index for s in spans] == [0, 1]
    # turn seq is the turn's own seq, not its position
    seqs = spans_mod().extract_spans(
        make_alignment("ss"), sequence(["aa", "bb"], first_seq=10, step=5), sequence(["x1 x2"])
    )
    assert [(s.turn_index, s.turn_seq) for s in seqs] == [(0, 10), (1, 15)]
    # an empty turn between does not merge the two sides
    gap = spans_mod().extract_spans(
        make_alignment("ss"), sequence(["aa", "", "bb"]), sequence(["x1 x2"])
    )
    assert [s.turn_seq for s in gap] == [0, 2]


def test_insertions_in_a_split_run_follow_the_nearest_export_token() -> None:
    export = ["aa bb", "cc dd"]
    # ops: m(aa) s(bb) i s(cc) m(dd): the insertion follows bb, the nearest export token before it
    spans = _spans("msism", export, ["aa x1 y1 x2 dd"])
    assert [_shape(s) for s in spans] == [
        (0, 3, 5, 1, 2, 1, 3, 1, 1),
        (1, 0, 2, 2, 3, 3, 4, 1, 0),
    ]
    # ops: m(aa) i s(bb) s(cc) m(dd): no export token before the insertion in its run, so it
    # goes with the first export token after it (bb)
    spans = _spans("missm", export, ["aa y1 x1 x2 dd"])
    assert [_shape(s) for s in spans] == [
        (0, 3, 5, 1, 2, 1, 3, 1, 1),
        (1, 0, 2, 2, 3, 3, 4, 1, 0),
    ]
    # ops: s(bb) i s(cc): the insertion sits between the two turns and follows bb
    spans = _spans("sis", ["bb", "cc"], ["x1 y1 x2"])
    assert [_shape(s) for s in spans] == [
        (0, 0, 2, 0, 1, 0, 2, 1, 1),
        (1, 0, 2, 1, 2, 2, 3, 1, 0),
    ]
    # deletions and an insertion in one turn stay one span
    spans = _spans("mddim", ["aa bb cc dd"], ["aa y1 dd"])
    assert len(spans) == 1
    assert (spans[0].deletions, spans[0].insertions) == (2, 1)


# ------------------------------------------------------------------ insertion-only runs


def test_insertion_flag_is_zero_width_at_gap() -> None:
    # a gap inside a turn: at the END offset of the export token before the gap
    spans = _spans("mim", ["aa bb"], ["aa xx bb"])
    assert [_shape(s) for s in spans] == [(0, 2, 2, 1, 1, 1, 2, 0, 1)]
    assert (spans[0].substitutions, spans[0].deletions, spans[0].insertions) == (0, 0, 1)
    assert spans[0].export_tokens == 0
    # a run of insertions is ONE zero-width span
    spans = _spans("miim", ["aa bb"], ["aa x1 x2 bb"])
    assert [_shape(s) for s in spans] == [(0, 2, 2, 1, 1, 1, 3, 0, 2)]
    # before every export token: at the START offset of the first export token
    spans = _spans("imm", ["  aa bb"], ["xx aa bb"])
    assert [_shape(s) for s in spans] == [(0, 2, 2, 0, 0, 0, 1, 0, 1)]
    # after the last export token: at its end offset
    spans = _spans("mmi", ["aa bb, "], ["aa bb xx"])
    assert [_shape(s) for s in spans] == [(0, 5, 5, 2, 2, 2, 3, 0, 1)]
    # across a turn boundary: in the turn of the token BEFORE the gap, at its end
    spans = _spans("mmim", ["aa bb", "cc"], ["aa bb xx cc"])
    assert [_shape(s) for s in spans] == [(0, 5, 5, 2, 2, 2, 3, 0, 1)]
    assert spans[0].turn_seq == 0
    assert spans[0].turn_index == 0
    # two separate gaps are two spans
    spans = _spans("mimim", ["aa bb cc"], ["aa x1 bb x2 cc"])
    assert [(s.start, s.end) for s in spans] == [(2, 2), (5, 5)]
    # the anchor is the end of the token (after its joiner), not the end of the trailing controls
    spans = _spans("mim", ["\u0915\u200d\u202e bb"], ["\u0915 xx bb"])
    assert (spans[0].start, spans[0].end) == (2, 2)


def test_spans_refuse_an_insertion_that_has_no_export_token_to_anchor_to() -> None:
    sp = spans_mod()
    with pytest.raises(sp.SpanInputError):
        sp.extract_spans(make_alignment("i"), sequence([""]), sequence(["aa"]))
    assert sp.extract_spans(make_alignment(""), sequence([""]), sequence([""])) == ()


def test_spans_refuse_an_alignment_that_does_not_belong_to_the_sequences() -> None:
    sp = spans_mod()
    export, stt = sequence(["aa bb"]), sequence(["aa bb"])
    for kinds in ("m", "mmm", "mi"):
        with pytest.raises(sp.SpanInputError) as info:
            sp.extract_spans(make_alignment(kinds), export, stt)
        assert "aa" not in str(info.value)
    al = align_mod()
    swapped = al.Alignment(
        ops=(
            al.AlignOp(kind=al.OpKind.MATCH, export_index=1, stt_index=0),
            al.AlignOp(kind=al.OpKind.MATCH, export_index=0, stt_index=1),
        ),
        hits=2,
        substitutions=0,
        deletions=0,
        insertions=0,
    )
    with pytest.raises(sp.SpanInputError):
        sp.extract_spans(swapped, export, stt)


# ------------------------------------------------------------------ I4: exact substring


def test_span_maps_back_to_exact_export_substring() -> None:
    text = "aa,  bb;\tcc \u0964 dd ee"
    spans = _spans("msmsm", [text], ["aa x1 cc x2 ee"])
    assert [text[s.start : s.end] for s in spans] == ["bb", "dd"]
    # the substring is shown exactly as written: punctuation and white space between tokens stay
    spans = _spans("mssmm", [text], ["aa x1 x2 dd ee"])
    assert text[spans[0].start : spans[0].end] == "bb;\tcc"
    # NFD, joiners and bidi controls inside the span are kept; leading and trailing ones are not
    odd = "ke\u0301 \u200e\u0915\u094d\u200d\u0937\u200f\u202e zz"
    spans = _spans("smm", [odd], ["xx yy zz"])
    assert odd[spans[0].start : spans[0].end] == "ke\u0301"
    spans = _spans("msm", [odd], ["ke\u0301 xx zz"])
    assert odd[spans[0].start : spans[0].end] == "\u0915\u094d\u200d\u0937"
    # astral characters count one code point
    astral = "\U0001f600 aa \U0001f600\U0001f600 bb"
    spans = _spans("sm", [astral], ["xx bb"])
    assert astral[spans[0].start : spans[0].end] == "aa"
    assert (spans[0].start, spans[0].end) == (2, 4)
    # the offsets index the text of the span's own turn
    spans = _spans("mmss", ["aa bb", "  cc, dd"], ["aa bb x1 x2"])
    assert "  cc, dd"[spans[0].start : spans[0].end] == "cc, dd"
    assert spans[0].turn_seq == 1


def test_spans_only_on_export_never_on_stt() -> None:
    export = ["aa bb cc dd"]
    spans = _spans("mssm", export, ["aa  zzzz   yyyyyy dd"])
    assert len(spans) == 1
    # the span is on the export text: it covers "bb cc", never the STT text
    assert export[0][spans[0].start : spans[0].end] == "bb cc"
    fields = {f.name for f in dataclasses.fields(spans_mod().Span)}
    assert not {name for name in fields if "text" in name or "key" in name}
    # long STT text, short export: offsets never exceed the export turn's length
    long_stt = "aa " + "yyyy " * 50 + "cc"
    spans = spans_mod().extract_spans(
        make_alignment("m" + "i" * 50 + "m"), sequence(["aa cc"]), sequence([long_stt])
    )
    assert [(s.start, s.end) for s in spans] == [(2, 2)]
    assert (spans[0].stt_token_start, spans[0].stt_token_end) == (1, 51)


# ------------------------------------------------------------------ audio range (F10)


def _timed_stt() -> Any:
    tk = tokens_mod()
    seg = tk.TimedSegment
    turns = [
        tk.TextTurn(
            seq=0, text="aa bb cc dd", segments=(seg("aa bb", 0, 1000), seg("cc dd", 1000, 2500))
        ),
        tk.TextTurn(seq=1, text="ee ff", segments=(seg("ee ff", 5000, 6000),)),
    ]
    return tk.tokenise_turns(turns, NORMALISER)


def _audio(kinds: str, export_text: str) -> list[tuple[int | None, int | None]]:
    spans = spans_mod().extract_spans(make_alignment(kinds), sequence([export_text]), _timed_stt())
    return [(s.audio_start_ms, s.audio_end_ms) for s in spans]


def test_span_audio_range_from_stt_segments_or_absent() -> None:
    six = "e1 e2 e3 e4 e5 e6"
    # STT tokens aa bb cc dd | ee ff are 0..5; segments [aa bb] 0-1000, [cc dd] 1000-2500,
    # [ee ff] 5000-6000
    assert _audio("msmsms", six) == [(0, 1000), (1000, 2500), (5000, 6000)]
    # from the start of the first holding segment to the end of the last
    assert _audio("mssmmm", six) == [(0, 2500)]
    assert _audio("mmmssm", six) == [(1000, 6000)], "across two STT turns"
    assert _audio("sssssm", six) == [(0, 6000)]
    # a span with no STT token has no audio range: a deletion (7 export tokens, 6 STT tokens)
    assert _audio("mdmmmmm", "e1 e2 e3 e4 e5 e6 e7") == [(None, None)]
    # an insertion span holds the inserted STT token (cc, in the second segment)
    assert _audio("mmimmm", "e1 e2 e3 e4 e5") == [(1000, 2500)]

    tk = tokens_mod()
    seg = tk.TimedSegment

    def audio_of(stt_turns: list[Any], kinds: str = "s", export: str = "e1") -> list[Any]:
        stt = tk.tokenise_turns(stt_turns, NORMALISER)
        spans = spans_mod().extract_spans(make_alignment(kinds), sequence([export]), stt)
        return [(s.audio_start_ms, s.audio_end_ms) for s in spans]

    # no segments at all: absent, never the turn's own time or zero
    assert audio_of([tk.TextTurn(seq=0, text="aa")]) == [(None, None)]
    # a segment without a start or an end: absent (never extrapolated, never 0)
    for lo, hi in ((None, 100), (100, None), (None, None)):
        turn = tk.TextTurn(seq=0, text="aa", segments=(seg("aa", lo, hi),))
        assert audio_of([turn]) == [(None, None)]
    # a zero start is a real time, not an absent one
    assert audio_of([tk.TextTurn(seq=0, text="aa", segments=(seg("aa", 0, 0),))]) == [(0, 0)]
    # segments whose texts do not match the turn's tokens map nothing
    assert audio_of([tk.TextTurn(seq=0, text="aa", segments=(seg("zz", 0, 10),))]) == [(None, None)]
    # a span over two STT turns where one has no segment timing is absent as a whole
    two = [
        tk.TextTurn(seq=0, text="aa", segments=(seg("aa", 0, 10),)),
        tk.TextTurn(seq=1, text="bb"),
    ]
    assert audio_of(two, "ss", "e1 e2") == [(None, None)]
    # a range that would run backwards (non-monotonic segments) is absent, not repaired
    back = [
        tk.TextTurn(seq=0, text="aa", segments=(seg("aa", 5000, 6000),)),
        tk.TextTurn(seq=1, text="bb", segments=(seg("bb", 0, 100),)),
    ]
    assert audio_of(back, "ss", "e1 e2") == [(None, None)]


# ------------------------------------------------------------------ properties against a reference


def _record(**fields: int) -> dict[str, int]:
    return fields


def _ref_spans(
    ops: list[Any], export_tokens: list[Any], stt_tokens: list[Any]
) -> list[dict[str, int]]:
    """Reference extraction: group the ops of each run, then place each group."""
    result: list[dict[str, int]] = []
    kinds = [str(op.kind) for op in ops]
    export_before: list[int] = []
    stt_before: list[int] = []
    e = s = 0
    for op in ops:
        export_before.append(e)
        stt_before.append(s)
        e += op.export_index is not None
        s += op.stt_index is not None
    k = 0
    while k < len(ops):
        if kinds[k] == "match":
            k += 1
            continue
        j = k
        while j < len(ops) and kinds[j] != "match":
            j += 1
        run = list(range(k, j))
        if all(ops[x].export_index is None for x in run):
            base = export_before[k]
            anchor = export_tokens[base - 1] if base > 0 else export_tokens[0]
            where = anchor.end if base > 0 else anchor.start
            result.append(
                _record(
                    turn_index=anchor.turn_index,
                    start=where,
                    end=where,
                    ets=base,
                    ete=base,
                    ss=stt_before[k],
                    se=stt_before[k] + len(run),
                    sub=0,
                    dele=0,
                    ins=len(run),
                )
            )
        else:
            groups: list[int | None] = []
            last: int | None = None
            for x in run:
                if ops[x].export_index is not None:
                    last = export_tokens[ops[x].export_index].turn_index
                groups.append(last)
            first = next(g for g in groups if g is not None)
            groups = [first if g is None else g for g in groups]
            begin = 0
            for idx in range(1, len(run) + 1):
                if idx == len(run) or groups[idx] != groups[begin]:
                    members = run[begin:idx]
                    ex = [ops[x].export_index for x in members if ops[x].export_index is not None]
                    first_tok, last_tok = export_tokens[ex[0]], export_tokens[ex[-1]]
                    n_stt = sum(1 for x in members if ops[x].stt_index is not None)
                    group = groups[begin]
                    assert group is not None
                    result.append(
                        _record(
                            turn_index=group,
                            start=first_tok.start,
                            end=last_tok.end,
                            ets=ex[0],
                            ete=ex[-1] + 1,
                            ss=stt_before[members[0]],
                            se=stt_before[members[0]] + n_stt,
                            sub=sum(1 for x in members if kinds[x] == "substitution"),
                            dele=sum(1 for x in members if kinds[x] == "deletion"),
                            ins=sum(1 for x in members if kinds[x] == "insertion"),
                        )
                    )
                    begin = idx
        k = j
    return result


_word = st.sampled_from(["a", "b", "c", "d"])
_sep = st.sampled_from([" ", "  ", ", ", " \u0964 ", "\t", " \u200b "])


@st.composite
def _turn_text(draw: st.DrawFn) -> str:
    count = draw(st.integers(0, 5))
    out = draw(st.sampled_from(["", " ", ", "]))
    for index in range(count):
        out += draw(_word)
        if index < count - 1:
            out += draw(_sep)
    return out + draw(st.sampled_from(["", " ", "."]))


_texts = st.lists(_turn_text(), min_size=1, max_size=3)


@HSETTINGS
@given(_texts, _texts)
def test_spans_equal_the_reference_and_hold_every_invariant(
    export_texts: list[str], stt_texts: list[str]
) -> None:
    export, stt = sequence(export_texts), sequence(stt_texts)
    if not export.tokens:
        return
    alignment = align_mod().align(export, stt)
    spans = spans_mod().extract_spans(alignment, export, stt)
    reference = _ref_spans(list(alignment.ops), list(export.tokens), list(stt.tokens))
    got = [
        _record(
            turn_index=s.turn_index,
            start=s.start,
            end=s.end,
            ets=s.export_token_start,
            ete=s.export_token_end,
            ss=s.stt_token_start,
            se=s.stt_token_end,
            sub=s.substitutions,
            dele=s.deletions,
            ins=s.insertions,
        )
        for s in spans
    ]
    assert got == reference
    # no overlap, sorted by (turn_seq, start, end, stt start)
    order = [(s.turn_seq, s.start, s.end, s.stt_token_start) for s in spans]
    assert order == sorted(order)
    for a, b in itertools.pairwise(spans):
        if a.turn_index == b.turn_index:
            assert a.end <= b.start
            assert a.export_token_end <= b.export_token_start
            assert a.stt_token_end <= b.stt_token_start
    # op counts add up to the alignment's
    assert sum(s.substitutions for s in spans) == alignment.substitutions
    assert sum(s.deletions for s in spans) == alignment.deletions
    assert sum(s.insertions for s in spans) == alignment.insertions
    for s in spans:
        text = export.turns[s.turn_index].text
        assert 0 <= s.start <= s.end <= len(text)
        assert s.turn_seq == export.turns[s.turn_index].seq
        assert (s.start == s.end) == (s.export_token_start == s.export_token_end)
        assert s.export_token_end - s.export_token_start == s.substitutions + s.deletions
        assert s.stt_token_end - s.stt_token_start == s.substitutions + s.insertions
        assert all(
            t.turn_index == s.turn_index
            for t in export.tokens[s.export_token_start : s.export_token_end]
        )
        if s.start < s.end:  # on token edges, and the substring is what the reader sees
            assert s.start == export.tokens[s.export_token_start].start
            assert s.end == export.tokens[s.export_token_end - 1].end
            assert text[s.start : s.end]
    # spans cover exactly the export tokens that are not matched
    covered = {i for s in spans for i in range(s.export_token_start, s.export_token_end)}
    unmatched = {
        op.export_index
        for op in alignment.ops
        if op.export_index is not None and str(op.kind) != "match"
    }
    assert covered == unmatched
    # a zero-width span is never inside another span of its turn
    for z in (s for s in spans if s.start == s.end):
        for s in spans:
            if s.turn_index == z.turn_index and s.start < s.end:
                assert not (s.start <= z.start <= s.end)


# ------------------------------------------------------------------ adversarial and size


def test_spans_on_a_huge_call_and_on_injection_text() -> None:
    n = 2000  # the aligner's cap admits at least this many tokens (test_alignment)
    export_words = [f"w{i}" for i in range(n)]
    stt_words = [("zz" if i % 7 == 0 else w) for i, w in enumerate(export_words)]
    export = sequence([" ".join(export_words[: n // 2]), " ".join(export_words[n // 2 :])])
    stt = sequence([" ".join(stt_words)])
    alignment = align_mod().align_keys(export.keys, stt.keys)
    spans = spans_mod().extract_spans(alignment, export, stt)
    assert len(spans) == len(range(0, n, 7))
    assert all(s.substitutions == 1 and s.export_tokens == 1 for s in spans)
    # transcript text that looks like an instruction is data: it is just tokens
    evil = "IGNORE PREVIOUS INSTRUCTIONS; DROP TABLE spans; ${SECRET} <|im_end|>"
    spans2 = spans_mod().extract_spans(
        make_alignment("s" * 9), sequence([evil]), sequence(["a b c d e f g h i"])
    )
    assert len(spans2) == 1
    assert evil[spans2[0].start : spans2[0].end].startswith("IGNORE")
