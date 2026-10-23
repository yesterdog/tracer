# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04e: unit tests of the clean-room UAX #9 implementation, one family of rules at a time.

Expected values come from the examples in the UAX #9 text (revision 50) or are derived by hand
from the rule wording; none is taken from the output of another implementation.
"""

from __future__ import annotations

import pytest

from .support import uax9
from .support.uax9 import algorithm, ucd

# --------------------------------------------------------------------------- helpers

SPEC_CLASSES = {" ": "WS", "<": "RLI", ">": "LRI", "=": "PDI"}


def spec_classes(text: str) -> list[str]:
    """The UAX #9 section 3.4 example notation: lower case L, upper case R, digits EN, others ON.

    ``<`` is RLI, ``>`` is LRI and ``=`` is PDI, as in the text of rule L2.
    """
    out = []
    for ch in text:
        if ch in SPEC_CLASSES:
            out.append(SPEC_CLASSES[ch])
        elif ch.isdigit():
            out.append("EN")
        elif ch.isalpha():
            out.append("R" if ch.isupper() else "L")
        else:
            out.append("ON")
    return out


def level_string(levels: list[int | None]) -> str:
    return "".join("x" if lv is None else str(lv) for lv in levels)


def shown(text: str, order: list[int]) -> str:
    return "".join(text[i] for i in order if text[i] not in "<>=")


def weak(types: str, sos: str = "R") -> list[str]:
    ts = types.split()
    algorithm._resolve_weak(ts, sos)
    return ts


def neutral(types: str, sos: str = "L", eos: str = "L", level: int = 0) -> list[str]:
    ts = types.split()
    algorithm._resolve_neutrals(ts, sos, eos, level)
    return ts


# --------------------------------------------------------------------------- P1 - P3, HL1


def test_uax9_p2_p3_paragraph_level() -> None:
    assert uax9.resolve_classes(["ON", "L"]).paragraph_level == 0
    assert uax9.resolve_classes(["ON", "R"]).paragraph_level == 1
    assert uax9.resolve_classes(["EN", "AL"]).paragraph_level == 1
    assert uax9.resolve_classes(["EN", "AN", "WS"]).paragraph_level == 0, "no strong: level 0"
    assert uax9.resolve_classes([]).paragraph_level == 0


def test_uax9_p2_skips_isolates_but_not_embeddings() -> None:
    assert uax9.resolve_classes(["RLI", "R", "PDI", "L"]).paragraph_level == 0
    assert uax9.resolve_classes(["RLI", "R", "L"]).paragraph_level == 0, "no matching PDI"
    assert uax9.resolve_classes(["LRI", "L", "PDI", "R"]).paragraph_level == 1
    assert uax9.resolve_classes(["LRE", "R"]).paragraph_level == 1
    assert uax9.resolve_classes(["PDI", "R"]).paragraph_level == 1, (
        "unmatched PDI is not an isolate"
    )


def test_uax9_hl1_overrides_paragraph_level() -> None:
    forced = uax9.resolve_classes(["L"], base_level=1)
    assert forced.paragraph_level == 1
    assert forced.levels == [2]
    assert uax9.resolve_classes(["R"], base_level=0).levels == [1]


def test_uax9_p1_splits_paragraphs_and_keeps_separator_with_previous() -> None:
    got = uax9.resolve_classes(["R", "B", "L"])
    assert got.paragraph_level == 1, "the level of the first paragraph"
    assert got.levels == [1, 1, 0], "the second paragraph has its own level"
    assert got.order == [1, 0, 2]


# --------------------------------------------------------------------------- X1 - X8


def test_uax9_x2_x5_embedding_and_override_levels() -> None:
    got = uax9.resolve_classes(["L", "RLE", "L", "LRE", "L", "PDF", "L", "PDF", "L"], 0)
    # L at level 0, then RLE -> 1 (L goes up to 2), LRE -> 2 (L stays 2), PDF back to 1 (2), 0
    assert got.levels == [0, None, 2, None, 2, None, 2, None, 0]


def test_uax9_x4_x5_overrides_change_the_type() -> None:
    got = uax9.resolve_classes(["RLO", "L", "EN", "PDF", "EN"], 0)
    assert got.levels == [None, 1, 1, None, 2], "overridden L and EN are R at level 1"
    got = uax9.resolve_classes(["LRO", "R", "AN", "PDF", "R"], 1)
    assert got.levels == [None, 2, 2, None, 1], "overridden R and AN are L at level 2"


def test_uax9_x2_depth_limit_and_overflow() -> None:
    # 63 valid RLE reach level 125 (max_depth); the 7 further ones overflow and change nothing
    got = uax9.resolve_classes(["RLE"] * 70 + ["L"], 0)
    assert got.levels == [None] * 70 + [126], "I2 can reach max_depth + 1"
    # a PDF first closes the overflow embeddings, and only then valid ones
    got = uax9.resolve_classes(["RLE"] * 70 + ["PDF"] * 7 + ["L"], 0)
    assert got.levels[-1] == 126
    got = uax9.resolve_classes(["RLE"] * 70 + ["PDF"] * 8 + ["L"], 0)
    assert got.levels[-1] == 124, "the eighth PDF closes a valid embedding: level 123, L -> 124"
    # 62 RLE/LRE pairs reach level 124; one more LRE would need 126 and overflows
    got = uax9.resolve_classes(["RLE", "LRE"] * 62 + ["LRE", "L"], 0)
    assert got.levels[-1] == 124


def test_uax9_x5a_isolate_levels_and_matching() -> None:
    got = uax9.resolve_classes(["L", "RLI", "L", "PDI", "L"], 0)
    # RLI and PDI keep the outer level; the content is at level 1 (L -> 2)
    assert got.levels == [0, 0, 2, 0, 0]
    got = uax9.resolve_classes(["R", "LRI", "R", "PDI", "R"], 1)
    assert got.levels == [1, 1, 3, 1, 1]


def test_uax9_x5c_fsi_takes_direction_of_its_content() -> None:
    assert uax9.resolve_classes(["FSI", "R", "PDI"], 0).levels == [0, 1, 0]
    assert uax9.resolve_classes(["FSI", "L", "PDI"], 0).levels == [0, 2, 0]
    # the first strong inside nested isolates is ignored; no strong: LRI-like
    assert uax9.resolve_classes(["FSI", "RLI", "R", "PDI", "L", "PDI"], 0).levels[4] == 2
    assert uax9.resolve_classes(["FSI", "ON", "PDI"], 1).levels == [1, 2, 1], "no strong: LRI-like"


def test_uax9_x5_isolate_initiator_under_override_takes_override_type() -> None:
    got = uax9.resolve_classes(["RLO", "LRI", "L", "PDI", "PDF", "R"], 0)
    # the LRI is R inside the RLO (level 1), its content is at level 2
    assert got.levels == [None, 1, 2, 1, None, 1]


def test_uax9_x6a_pdi_closes_embeddings_inside_the_isolate() -> None:
    got = uax9.resolve_classes(["LRI", "RLE", "L", "PDI", "L"], 0)
    assert got.levels == [0, None, 4, 0, 0], "after the PDI the RLE is closed"


def test_uax9_x6a_x7_unmatched_pdi_and_pdf_are_ignored() -> None:
    assert uax9.resolve_classes(["PDI", "R", "PDF", "R"], 0).levels == [0, 1, None, 1]
    got = uax9.resolve_classes(["RLI", "PDF", "R", "PDI"], 0)
    assert got.levels == [0, None, 1, 0], "a PDF cannot close the embedding outside its isolate"


def test_uax9_x7_pdf_inside_overflow_isolate_is_ignored() -> None:
    depth = ["RLI"] * 64  # RLI 1..63 valid (levels up to 125); the 64th overflows
    got = uax9.resolve_classes([*depth, "PDF", "L", "PDI"], 0)
    assert got.levels[65] == 126


def test_uax9_x8_paragraph_separator_gets_paragraph_level() -> None:
    got = uax9.resolve_classes(["RLE", "R", "B"], 0)
    assert got.levels == [None, 1, 0]


# --------------------------------------------------------------------------- X9, X10


def test_uax9_x9_removed_characters_have_no_level() -> None:
    got = uax9.resolve_classes(["LRE", "RLE", "PDF", "PDF", "BN", "L", "BN", "RLO", "PDF"], 0)
    assert got.levels == [None] * 5 + [0] + [None] * 3
    assert got.order == [5]


def test_uax9_x9_isolate_formatting_characters_are_not_removed() -> None:
    got = uax9.resolve_classes(["LRI", "RLI", "FSI", "PDI"], 0)
    assert all(level is not None for level in got.levels)


def test_uax9_bd13_isolating_run_sequences_example_2() -> None:
    # text1 RLI text2 PDI RLI text3 PDI text4, paragraph level 0 (UAX #9, BD13)
    classes = ["L", "RLI", "L", "PDI", "RLI", "L", "PDI", "L"]
    match = algorithm._match_isolates(classes)
    _, levels = algorithm._explicit_levels(classes, 0, match)
    assert levels == [0, 0, 1, 0, 0, 1, 0, 0]
    seqs = algorithm._isolating_run_sequences(classes, levels, list(range(8)), match, 0)
    assert sorted(s[0] for s in seqs) == [[0, 1, 3, 4, 6, 7], [2], [5]]


def test_uax9_bd13_isolating_run_sequences_example_3() -> None:
    # text1 RLI text2 LRI text3 RLE text4 PDF text5 PDI text6 PDI text7
    classes = ["L", "RLI", "L", "LRI", "L", "RLE", "L", "PDF", "L", "PDI", "L", "PDI", "L"]
    match = algorithm._match_isolates(classes)
    _, levels = algorithm._explicit_levels(classes, 0, match)
    kept = [i for i, c in enumerate(classes) if c not in ("RLE", "PDF")]
    seqs = algorithm._isolating_run_sequences(classes, levels, kept, match, 0)
    assert sorted(s[0] for s in seqs) == [
        [0, 1, 11, 12],
        [2, 3, 9, 10],
        [4],
        [6],
        [8],
    ]


def test_uax9_x10_sos_and_eos_example_3() -> None:
    # text1 RLE text2 LRI text3 RLE text4 PDI text5 PDF text6 (UAX #9, X10 example 3)
    classes = ["L", "RLE", "L", "LRI", "L", "RLE", "L", "PDI", "L", "PDF", "L"]
    match = algorithm._match_isolates(classes)
    _, levels = algorithm._explicit_levels(classes, 0, match)
    kept = [i for i, c in enumerate(classes) if c not in ("RLE", "PDF")]
    seqs = algorithm._isolating_run_sequences(classes, levels, kept, match, 0)
    table = {(tuple(s), sos, eos) for s, sos, eos in seqs}
    assert table == {
        ((0,), "L", "R"),
        ((2, 3, 7, 8), "R", "R"),
        ((4,), "L", "R"),
        ((6,), "R", "R"),
        ((10,), "R", "L"),
    }


# --------------------------------------------------------------------------- W1 - W7


def test_uax9_w1_nsm_takes_previous_type() -> None:
    assert weak("AL NSM NSM") == ["R", "R", "R"], "AL NSM NSM -> AL AL AL, then W3 makes them R"
    assert weak("NSM", sos="R") == ["R"]
    assert weak("NSM", sos="L") == ["L"]
    assert weak("LRI NSM") == ["LRI", "ON"]
    assert weak("PDI NSM") == ["PDI", "ON"]
    assert weak("RLI NSM NSM") == ["RLI", "ON", "ON"]


def test_uax9_w2_w3_european_number_after_arabic_letter() -> None:
    assert weak("AL EN") == ["R", "AN"]
    assert weak("AL ON EN") == ["R", "ON", "AN"]
    assert weak("ON EN", sos="R") == ["ON", "EN"], "sos NI EN"
    assert weak("R ON EN") == ["R", "ON", "EN"]
    assert weak("AL R EN") == ["R", "R", "EN"], "the nearest strong type is R"


def test_uax9_w4_single_separator_between_numbers() -> None:
    assert weak("EN ES EN") == ["EN", "EN", "EN"]
    assert weak("EN CS EN") == ["EN", "EN", "EN"]
    assert weak("AN CS AN") == ["AN", "AN", "AN"]
    assert weak("AN ES AN") == ["AN", "ON", "AN"], "ES joins only European numbers"
    assert weak("EN CS AN") == ["EN", "ON", "AN"]
    assert weak("EN ES ES EN") == ["EN", "ON", "ON", "EN"], "only a single separator"


def test_uax9_w5_terminators_next_to_european_numbers() -> None:
    assert weak("ET ET EN") == ["EN", "EN", "EN"]
    assert weak("EN ET ET") == ["EN", "EN", "EN"]
    assert weak("AN ET EN") == ["AN", "EN", "EN"]
    assert weak("ET AN") == ["ON", "AN"]


def test_uax9_w5_terminator_next_to_number_made_by_w4() -> None:
    assert weak("EN CS EN ET") == ["EN", "EN", "EN", "EN"]


def test_uax9_w6_remaining_separators_and_terminators_are_neutral() -> None:
    assert weak("AN ET") == ["AN", "ON"]
    assert weak("R ES EN") == ["R", "ON", "EN"]
    assert weak("CS") == ["ON"]
    assert weak("ES ET CS") == ["ON", "ON", "ON"]


def test_uax9_w7_european_number_after_l_is_l() -> None:
    assert weak("L ON EN") == ["L", "ON", "L"]
    assert weak("R ON EN") == ["R", "ON", "EN"]
    assert weak("EN", sos="L") == ["L"]
    assert weak("EN", sos="R") == ["EN"]
    assert weak("L R EN") == ["L", "R", "EN"]


# --------------------------------------------------------------------------- N1, N2


def test_uax9_n1_table_from_the_specification() -> None:
    assert neutral("L ON L") == ["L", "L", "L"]
    assert neutral("R ON R") == ["R", "R", "R"]
    assert neutral("R ON AN") == ["R", "R", "AN"]
    assert neutral("R ON EN") == ["R", "R", "EN"]
    assert neutral("AN ON R") == ["AN", "R", "R"]
    assert neutral("AN ON AN") == ["AN", "R", "AN"]
    assert neutral("AN ON EN") == ["AN", "R", "EN"]
    assert neutral("EN ON R") == ["EN", "R", "R"]
    assert neutral("EN ON AN") == ["EN", "R", "AN"]
    assert neutral("EN ON EN") == ["EN", "R", "EN"]


def test_uax9_n1_all_neutral_kinds_and_isolates_are_ni() -> None:
    kinds = ["B", "S", "WS", "ON", "LRI", "RLI", "FSI", "PDI"]
    assert neutral("R " + " ".join(kinds) + " R") == ["R"] + ["R"] * 8 + ["R"]
    assert neutral("L " + " ".join(kinds) + " L") == ["L"] + ["L"] * 8 + ["L"]


def test_uax9_n2_remaining_neutrals_take_the_embedding_direction() -> None:
    assert neutral("L ON R", level=0) == ["L", "L", "R"]
    assert neutral("L ON R", level=1) == ["L", "R", "R"]
    assert neutral("R WS L", level=0) == ["R", "L", "L"]


def test_uax9_n1_n2_use_sos_and_eos_at_the_boundaries() -> None:
    assert neutral("ON", sos="R", eos="L", level=0) == ["L"], "sos R, eos L: embedding direction"
    assert neutral("ON", sos="R", eos="L", level=1) == ["R"]
    assert neutral("ON R", sos="R", eos="L") == ["R", "R"], "sos NI R -> R"
    assert neutral("ON L", sos="R", eos="R") == ["L", "L"], "sos NI L, e = L"
    assert neutral("L ON", sos="L", eos="L", level=1) == ["L", "L"], "L NI eos(L)"
    assert neutral("R ON", sos="L", eos="L", level=0) == ["R", "L"], "R NI eos(L): e"


# --------------------------------------------------------------------------- BD16, N0


def _pairs(text: str) -> list[tuple[int, int]]:
    """Bracket pairs of ``text``; letters are L, brackets ON."""
    brackets = [ucd.bracket(ord(ch)) for ch in text]
    ts = ["ON" if b is not None else "L" for b in brackets]
    return algorithm._find_bracket_pairs(ts, list(range(len(text))), brackets)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a)b(c", []),
        ("a(b]c", []),
        ("a(b)c", [(1, 3)]),
        ("a(b[c)d]", [(1, 5)]),
        ("a(b]c)d", [(1, 5)]),
        ("a(b)c)d", [(1, 3)]),
        ("a(b(c)d", [(3, 5)]),
        ("a(b(c)d)", [(1, 7), (3, 5)]),
        ("a(b{c}d)", [(1, 7), (3, 5)]),
    ],
)
def test_uax9_bd16_examples_from_the_specification(
    text: str, expected: list[tuple[int, int]]
) -> None:
    assert _pairs(text) == expected


def test_uax9_bd16_canonically_equivalent_angle_brackets_pair() -> None:
    assert _pairs("\u2329x\u3009") == [(0, 2)]
    assert _pairs("\u3008x\u232a") == [(0, 2)]
    assert _pairs("\u2329x\u232a") == [(0, 2)]
    assert _pairs("\u3008x\u3009") == [(0, 2)]
    assert _pairs("\u3008x\u232b") == []


def test_uax9_bd16_stack_has_63_elements() -> None:
    assert _pairs("(" * 63 + ")") == [(62, 63)]
    assert _pairs("(" * 64 + ")") == [], "no room: no pairs at all for the sequence"
    assert _pairs("()" + "(" * 64) == [], "stopping returns an empty list, even after a pair"


def test_uax9_bd14_bd15_a_bracket_must_currently_be_on() -> None:
    brackets = [ucd.bracket(ord(ch)) for ch in "(a)"]
    assert algorithm._find_bracket_pairs(["R", "L", "R"], [0, 1, 2], brackets) == []


def _n0(chars: str, types: str, sos: str = "L", level: int = 0) -> list[str]:
    ts = types.split()
    brackets = [ucd.bracket(ord(ch)) for ch in chars]
    algorithm._resolve_brackets(ts, list(ts), list(range(len(ts))), sos, level, brackets)
    return ts


def test_uax9_n0b_strong_type_matching_embedding_direction() -> None:
    assert _n0("x[y]z", "R ON L ON R") == ["R", "L", "L", "L", "R"]
    assert _n0("x[yz]", "R ON R L ON") == ["R", "L", "R", "L", "L"]
    assert _n0("x[ y]", "R ON ON L ON") == ["R", "L", "ON", "L", "L"]
    assert _n0("x[y]z", "L ON R ON L", level=1) == ["L", "R", "R", "R", "L"]


def test_uax9_n0c1_opposite_direction_with_established_context() -> None:
    assert _n0("x[y]z", "R ON R ON L") == ["R", "R", "R", "R", "L"]
    assert _n0("[y]", "ON R ON", sos="R") == ["R", "R", "R"], "sos supplies the context"


def test_uax9_n0c2_opposite_direction_without_context() -> None:
    assert _n0("x[y]z", "L ON R ON R") == ["L", "L", "R", "L", "R"]
    assert _n0("x[y]z", "L ON R ON L") == ["L", "L", "R", "L", "L"]
    assert _n0("[y]", "ON R ON", sos="L") == ["L", "R", "L"]


def test_uax9_n0_numbers_count_as_r() -> None:
    assert _n0("x(y)", "R ON EN ON") == ["R", "R", "EN", "R"]
    assert _n0("x(y)", "L ON AN ON") == ["L", "L", "AN", "L"]


def test_uax9_n0d_no_strong_type_inside_leaves_brackets_unset() -> None:
    assert _n0("x( )z", "L ON ON ON R") == ["L", "ON", "ON", "ON", "R"]
    assert _n0("x()z", "L ON ON R") == ["L", "ON", "ON", "R"]


def test_uax9_n0_pairs_are_resolved_in_order_of_opening_bracket() -> None:
    # UAX #9 N0 example 1 (RTL): AB(CD[&ef]!)gh -> both pairs become R
    types = ["R", "R", "ON", "R", "R", "ON", "ON", "L", "L", "ON", "ON", "ON", "L", "L"]
    chars = "ab(cd[&ef]!)gh"
    ts = list(types)
    brackets = [ucd.bracket(ord(ch)) for ch in chars]
    algorithm._resolve_brackets(ts, list(ts), list(range(len(ts))), "R", 1, brackets)
    assert [ts[i] for i in (2, 5, 9, 11)] == ["R", "R", "R", "R"]


def test_uax9_n0_nsm_after_a_resolved_bracket_follows_it() -> None:
    ts = ["ON", "R", "ON", "ON", "ON"]
    original = ["ON", "R", "ON", "NSM", "NSM"]
    brackets = [ucd.bracket(ord(ch)) for ch in "(a)bc"]
    algorithm._resolve_brackets(ts, original, [0, 1, 2, 3, 4], "R", 0, brackets)
    assert ts == ["R", "R", "R", "R", "R"]
    # an unresolved pair does not touch the following marks
    ts = ["ON", "ON", "ON", "ON"]
    original = ["ON", "ON", "ON", "NSM"]
    brackets = [ucd.bracket(ord("(")), None, ucd.bracket(ord(")")), None]
    algorithm._resolve_brackets(ts, original, [0, 1, 2, 3], "R", 0, brackets)
    assert ts == ["ON", "ON", "ON", "ON"]


def test_uax9_n0_through_the_full_algorithm_spec_example_3() -> None:
    # "ARABIC book(s)" in an RTL paragraph displays as "book(s) CIBARA": brackets resolve to L
    text = "\u05d0\u05d1\u05d2 book(s)"
    assert uax9.get_display(text, "R") == "book(s) \u05d2\u05d1\u05d0"
    levels = uax9.resolve(text, "R").levels
    assert levels == [1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2]


# --------------------------------------------------------------------------- I1, I2


def test_uax9_i1_i2_resolved_levels() -> None:
    # Table 5 of the specification (EN needs a preceding R so that W7 leaves it alone)
    assert [uax9.resolve_classes([c], 0).levels[0] for c in ("L", "R", "AN")] == [0, 1, 2]
    assert uax9.resolve_classes(["R", "EN"], 0).levels == [1, 2]
    assert [uax9.resolve_classes([c], 1).levels[0] for c in ("L", "R", "AN", "EN")] == [2, 1, 2, 2]


# --------------------------------------------------------------------------- L1, L2


def test_uax9_l1_segment_and_paragraph_separators_and_preceding_white_space() -> None:
    got = uax9.resolve_classes(["R", "WS", "S", "R", "WS", "B"], 0)
    assert got.levels == [1, 0, 0, 1, 0, 0]
    got = uax9.resolve_classes(["R", "RLI", "WS", "S"], 0)
    assert got.levels == [1, 0, 0, 0], "isolate formatting characters count like white space"


def test_uax9_l1_trailing_white_space_and_isolates_at_the_end_of_the_line() -> None:
    assert uax9.resolve_classes(["R", "WS", "WS"], 0).levels == [1, 0, 0]
    assert uax9.resolve_classes(["R", "WS", "RLI", "PDI"], 0).levels == [1, 0, 0, 0]
    assert uax9.resolve_classes(["R", "WS", "R"], 0).levels == [1, 1, 1], "not trailing"


def test_uax9_l1_ignores_removed_characters_between_white_space_and_the_end() -> None:
    # the bug class that python-bidi 0.6.12 had: explicit formatting characters (removed by X9)
    # sitting between trailing white space and the separator or the end of the line
    assert uax9.resolve_classes(["R", "WS", "LRE", "S"], 0).levels == [1, 0, None, 0]
    assert uax9.resolve_classes(["R", "WS", "RLE", "WS"], 0).levels == [1, 0, None, 0]
    assert uax9.resolve_classes(["R", "WS", "PDF"], 0).levels == [1, 0, None]
    assert uax9.resolve_classes(["R", "WS", "BN", "WS", "BN"], 0).levels == [1, 0, None, 0, None]
    assert uax9.resolve_classes(["R", "WS", "RLO", "S", "PDF"], 0).levels == [1, 0, None, 0, None]
    assert uax9.resolve_classes(["RLE", "R", "WS", "PDF", "B"], 0).levels == [None, 1, 0, None, 0]


def test_uax9_l1_trailing_tab_after_an_explicit_embedding() -> None:
    # trailing white space inside an embedding is reset to the paragraph level, so it is shown at
    # the visual end of an LTR paragraph, not reordered with the embedded R text
    got = uax9.resolve_classes(["L", "RLE", "R", "WS", "S", "PDF"], 0)
    assert got.levels == [0, None, 1, 0, 0, None]
    assert got.order == [0, 2, 3, 4]


def test_uax9_l1_uses_original_types() -> None:
    # white space under an override is R for W/N purposes but still white space for L1
    got = uax9.resolve_classes(["RLO", "L", "WS", "PDF"], 0)
    assert got.levels == [None, 1, 0, None]


def test_uax9_l2_examples_from_the_specification() -> None:
    cases = [
        ("car means CAR.", 0, "00000000001110", "car means RAC."),
        ("<car MEANS CAR.=", 0, "0222111111111110", ".RAC SNAEM car"),
        (
            "he said \u201c<car MEANS CAR=.\u201d \u201c<IT DOES=,\u201d she agreed.",
            0,
            "000000000022211111111110000001111111000000000000000",
            "he said \u201cRAC SNAEM car.\u201d \u201cSEOD TI,\u201d she agreed.",
        ),
        (
            "DID YOU SAY \u2019>he said \u201c<car MEANS CAR=\u201d=\u2018?",
            1,
            "111111111111112222222222444333333333322111",
            "?\u2018he said \u201cRAC SNAEM car\u201d\u2019 YAS UOY DID",
        ),
    ]
    for text, base, levels, display in cases:
        got = uax9.resolve_classes(spec_classes(text), base)
        assert level_string(got.levels) == levels, text
        assert shown(text, got.order) == display, text


# --------------------------------------------------------------------------- text API


def test_uax9_get_base_level() -> None:
    assert uax9.get_base_level("abc") == 0
    assert uax9.get_base_level("\u05d0abc") == 1
    assert uax9.get_base_level("\u0627") == 1
    assert uax9.get_base_level("123") == 0
    assert uax9.get_base_level("") == 0
    assert uax9.get_base_level("abc", "R") == 1
    with pytest.raises(ValueError, match="base_dir"):
        uax9.get_base_level("abc", "X")


def test_uax9_get_display_mirrors_at_odd_levels_only() -> None:
    assert uax9.get_display("a(b)") == "a(b)"
    assert uax9.get_display("\u05d0(\u05d1)") == "(\u05d1)\u05d0"
    # not paired brackets, but Bidi_Mirrored: '<' is at level 1 and shown as '>', '>' is at level 0
    assert uax9.get_display("\u05d0<\u05d1>", "L") == "\u05d1>\u05d0>"


def test_uax9_get_display_omits_x9_removed_characters() -> None:
    assert uax9.get_display("a\u202bb\u202c") == "ab"
    assert uax9.get_display("a\u200bb") == "ab"
    assert uax9.get_display("a\u2066b\u2069") == "a\u2066b\u2069", "isolates are kept"


def test_uax9_account_number_override_attack() -> None:
    # 1234<RLO>9876 is displayed as 1234 6789 (ADR 0017)
    assert uax9.get_display("1234\u202e9876") == "12346789"
    assert uax9.get_display("abc \u202edef") == "abc fed"


def test_uax9_isolate_contains_its_content() -> None:
    text = "abc \u2068\u05d0\u05d1\u2069 def"
    assert uax9.get_display(text) == "abc \u2068\u05d1\u05d0\u2069 def"
    # an unclosed embedding would reverse what follows; the isolate does not leak
    leaky = "abc \u202b\u05d0\u05d1 def"
    assert uax9.get_display(leaky) != "abc \u05d1\u05d0 def"
