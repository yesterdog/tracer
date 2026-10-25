# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-13: the comparison normaliser ``mr-Deva/1`` (ADR 0025; contract
docs/phase3/api-surface-batch-c.md section 2).

The expected values come from the ADR text and from the fixture ``normaliser_mr.json`` (94 cases
by the indic-linguistics agent; every rule is ``needs_native_review``: nothing here is an Indic
quality claim). All text is invented; non-ASCII characters are escapes.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib
import inspect
import itertools
import json
import re
import unicodedata
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batchc_support import (  # type: ignore[import-not-found,unused-ignore]
    ADR25,
    IGNORABLE,
    NORMALISER,
    fixture_cases,
    load_fixture,
    mixed_text,
    normalise_mod,
    tokens_mod,
)

ID_PATTERN = re.compile(r"^[a-z]{2,3}-[A-Z][a-z]{3}/[1-9][0-9]{0,8}$")


# Deterministic hypothesis profile (as Phase 1 and 2): derandomised, no shrinking, no deadline.
HSETTINGS = settings(
    max_examples=80,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def _key(text: str, normaliser: str = NORMALISER) -> str:
    result = normalise_mod().comparison_key(text, normaliser)
    assert isinstance(result, str)
    return result


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(t.key, t.start, t.end) for t in tokens_mod().tokenise(text, NORMALISER)]


# ------------------------------------------------------------------ id (I1)


def test_normaliser_id_is_versioned_and_recorded() -> None:
    nm = normalise_mod()
    assert nm.NORMALISER_ID == "mr-Deva/1"
    assert ID_PATTERN.fullmatch(nm.NORMALISER_ID)
    assert nm.SUPPORTED_NORMALISERS == ("mr-Deva/1",)
    # the fixture names the same id, so the file and the code cannot drift apart silently
    assert load_fixture()["normaliser"] == nm.NORMALISER_ID
    # the version recorded next to the id (ADR 0025 Decision 12): the interpreter's Unicode data
    info = nm.normaliser_info(nm.NORMALISER_ID)
    assert (info.id, info.unicode_version) == ("mr-Deva/1", unicodedata.unidata_version)
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.id = "mr-Deva/2"
    # an id the package does not ship is refused, never mapped to the nearest one
    for bad in ("mr-Deva/2", "mr-Deva/0", "mr-deva/1", "hi-Deva/1", "mr-Deva", "", "mr-Deva/01"):
        with pytest.raises(nm.UnknownNormaliserError):
            nm.normaliser_info(bad)
        with pytest.raises(nm.UnknownNormaliserError):
            nm.comparison_key("abc", bad)
        with pytest.raises(nm.UnknownNormaliserError):
            tokens_mod().tokenise("abc", bad)
    for not_text in (None, 1, b"abc", ["a"]):
        with pytest.raises(TypeError):
            nm.comparison_key(not_text, "mr-Deva/1")
    for not_a_string in (None, 1, b"mr-Deva/1", ("mr-Deva/1",)):
        with pytest.raises(TypeError):
            nm.normaliser_info(not_a_string)
    # the id is a required argument: the binding parameter has no default (ADR 0023 rule 4)
    for function in (nm.comparison_key, tokens_mod().tokenise, nm.normaliser_info):
        parameters = inspect.signature(function).parameters
        assert parameters["normaliser"].default is inspect.Parameter.empty, function


def test_normaliser_error_types_are_value_errors_with_a_common_base() -> None:
    nm = normalise_mod()
    errors = importlib.import_module("tracer_evalcore.errors")
    assert issubclass(nm.NormaliserError, errors.InputRefusedError)
    assert issubclass(nm.NormaliserError, ValueError)
    assert issubclass(nm.UnknownNormaliserError, nm.NormaliserError)
    assert issubclass(nm.LoneSurrogateError, nm.NormaliserError)
    assert nm.UnknownNormaliserError is not nm.LoneSurrogateError


# ------------------------------------------------------------------ the fixture (I2)


@pytest.mark.parametrize("case", fixture_cases(), ids=lambda case: str(case["id"]))
def test_normaliser_mr_fixture_cases(case: dict[str, Any]) -> None:
    text = case["text"]
    expected = [(t["key"], t["start"], t["end"]) for t in case["expected_tokens"]]
    tokens = tokens_mod().tokenise(text, NORMALISER)
    assert [(t.key, t.start, t.end) for t in tokens] == expected, case["purpose"]
    for token in tokens:
        piece = text[token.start : token.end]
        assert piece, "a token slice is never empty"
        assert token.key, "a key is never empty"
        # the Decision 2 steps on the slice give the key
        assert _key(piece) == token.key
    # the offsets are in order and do not overlap
    for before, after in itertools.pairwise(tokens):
        assert before.end <= after.start
    assert case["needs_native_review"] is True


def test_normaliser_fixture_has_all_cases_and_the_rules_are_exercised() -> None:
    cases = fixture_cases()
    assert len(cases) == 94
    ids = [case["id"] for case in cases]
    assert len(set(ids)) == len(ids)
    rules = {rule for case in cases for rule in case["rules"]}
    nm = normalise_mod()
    # every implemented rule except R17 (JSON cannot hold a lone surrogate) has a fixture case
    assert rules == set(nm.IMPLEMENTED_RULES) - {"R17"}
    assert len(nm.IMPLEMENTED_RULES) == 18


# ------------------------------------------------------------------ R11, R13


def test_normaliser_never_merges_lla_and_la() -> None:
    la, lla, nukta = "\u0932", "\u0933", "\u093c"
    keys = {_key(la), _key(lla), _key(lla + nukta)}
    assert len(keys) == 3, "LA, LLA and LLA + nukta keep three keys (R11)"
    assert _key(lla + nukta) == "\u0934", "NFC composes LLA + nukta into U+0934"
    # the pair of the reference calls: kaal (time) against kaal (a different word)
    assert _key("\u0915\u093e\u0933") != _key("\u0915\u093e\u0932")
    assert _tokens("\u0915\u093e\u0933 \u0915\u093e\u0932") == [
        ("\u0915\u093e\u0933", 0, 3),
        ("\u0915\u093e\u0932", 4, 7),
    ]
    # R13: the nukta is never stripped, R12: the nasal marks are not folded or dropped
    assert _key("\u091c\u093c\u0930\u0942\u0930") != _key("\u091c\u0930\u0942\u0930")
    assert (
        len({_key("\u0915\u0902"), _key("\u0915\u0901"), _key("\u0915\u0903"), _key("\u0915")}) == 4
    )
    # R18: no vowel-length folding
    assert _key("\u0907") != _key("\u0908")


# ------------------------------------------------------------------ R03, R14


def test_normaliser_folds_devanagari_digits_in_key_only() -> None:
    native = "".join(chr(0x0966 + n) for n in range(10))
    assert native == "\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f"
    assert _key(native) == "0123456789"
    tokens = tokens_mod().tokenise(native, NORMALISER)
    assert [(t.key, t.start, t.end) for t in tokens] == [("0123456789", 0, 10)]
    # display is the original: the offsets address the unchanged text
    assert native[tokens[0].start : tokens[0].end] == native
    # mixed forms are one token with one key
    assert _tokens("1\u0968 3") == [("12", 0, 2), ("3", 3, 4)]
    # R14: other decimal digits are token characters and are not folded
    assert _key("\u0ae7") == "\u0ae7"  # Gujarati digit one
    assert _key("\uff11") == "\uff11"  # fullwidth digit one
    assert _key("\u0ae7") != _key("1")
    # R14: a number word is not folded against digits
    assert _key("\u090f\u0915") != _key("\u0967")


# ------------------------------------------------------------------ R04


def test_normaliser_drops_joiners_in_key_only_display_kept() -> None:
    zwj, zwnj, virama = "\u200d", "\u200c", "\u094d"
    ka, ssa = "\u0915", "\u0937"
    for joiner in (zwj, zwnj):
        text = ka + virama + joiner + ssa
        tokens = tokens_mod().tokenise(text, NORMALISER)
        assert [(t.key, t.start, t.end) for t in tokens] == [(ka + virama + ssa, 0, 4)]
        assert text[tokens[0].start : tokens[0].end] == text, "the span keeps the joiner"
    # a trailing joiner is inside the span; a leading or lone one is outside every token
    assert _tokens(ka + zwj) == [(ka, 0, 2)]
    assert _tokens(zwj + ka) == [(ka, 1, 2)]
    assert _tokens(" " + zwj + " ") == []
    assert _tokens(zwj + zwnj) == []
    # a joiner never splits a token
    assert len(_tokens(ka + zwj + zwj + zwnj + ssa)) == 1
    # the nukta that follows a joiner composes with its base once the joiner is dropped
    assert _key("\u0928" + zwj + "\u093c") == "\u0929"
    # the eyelash-ra sequence is only recorded, not confirmed (BL-132): RA virama ZWJ YA
    assert _key("\u0930\u094d\u200d\u092f") == _key("\u0930\u094d\u092f")


# ------------------------------------------------------------------ R01, R02, R10


def test_normaliser_is_nfc_not_nfkc() -> None:
    # R01: U+0958 to U+095F never survive; they decompose to base + nukta
    for code in range(0x0958, 0x0960):
        decomposed = unicodedata.normalize("NFD", chr(code))
        assert len(decomposed) == 2
        assert _key(chr(code)) == decomposed
        assert not any(0x0958 <= ord(c) <= 0x095F for c in _key(chr(code)))
    # the precomposed and the decomposed spellings of one word have one key
    assert _key("\u095b") == _key("\u091c\u093c") == "\u091c\u093c"
    # NA, RA, LLA + nukta compose to U+0929, U+0931, U+0934
    assert _key("\u0928\u093c") == "\u0929"
    assert _key("\u0930\u093c") == "\u0931"
    assert _key("\u0933\u093c") == "\u0934"
    # a nukta (class 7) sorts before a virama (class 9) that came first
    assert _key("\u0915\u094d\u093c") == "\u0915\u093c\u094d"
    # R02: no NFKC. Fullwidth and circled forms are not folded to ASCII
    assert _key("\uff21") == "\uff41"  # fullwidth A folds to fullwidth a, not to ASCII a
    assert _key("\uff21") != "a"
    assert _tokens("\u2460") == []  # circled digit one is a separator (R09)
    assert _tokens("a\u2460b") == [("a", 0, 1), ("b", 2, 3)]
    # R10: full case folding, not simple folding and not NFKC
    assert _key("\u00df") == "ss"
    assert _key("\u0130") == "i\u0307"
    assert _key("\u212a") == "k"
    assert _key("\ufb01") == "fi"
    assert _key("WhatsApp") == _key("whatsapp") == "whatsapp"
    # the key is NFC, and Latin NFC and NFD spellings have one key
    assert _key("e\u0301") == _key("\u00e9") == "\u00e9"
    assert unicodedata.is_normalized("NFC", _key("\u0958\u0959 e\u0301"))


@HSETTINGS
@given(
    st.lists(
        st.sampled_from(
            [
                "\u0915",
                "\u0916",
                "\u091c",
                "\u0928",
                "\u0930",
                "\u0933",
                "\u093c",
                "\u094d",
                "\u093e",
                "\u0902",
                "\u200d",
                "\u200c",
                "a",
                "e",
                "\u0301",
                "\u0958",
                "\u095b",
                "\u0929",
                "7",
                "\u0967",
            ]
        ),
        min_size=1,
        max_size=8,
    ).map("".join)
)
def test_normaliser_key_is_indifferent_to_nfc_and_nfd_spelling(text: str) -> None:
    assert _key(unicodedata.normalize("NFD", text)) == _key(unicodedata.normalize("NFC", text))
    assert unicodedata.is_normalized("NFC", _key(text))


# ------------------------------------------------------------------ R17


def test_normaliser_refuses_lone_surrogate() -> None:
    nm, tk = normalise_mod(), tokens_mod()
    marker = "SECRETWORD"
    cases = [
        "\ud800",
        "\udfff",
        marker + "\ud800",
        "\udc00" + marker,
        "a\ud800b",
        chr(0xD83D) + chr(0xDE00),  # a UTF-16 pair as two code points: two lone surrogates in str
        "\u0915 \udc80 \u0916",
    ]
    for text in cases:
        with pytest.raises(nm.LoneSurrogateError) as info:
            tk.tokenise(text, NORMALISER)
        assert isinstance(info.value, ValueError)
        assert marker not in str(info.value), "the message never echoes the input"
        assert not re.search(r"ud[89a-f][0-9a-f]{2}", str(info.value).lower())
        with pytest.raises(nm.LoneSurrogateError):
            nm.comparison_key(text, NORMALISER)
        with pytest.raises(nm.LoneSurrogateError):
            tk.tokenise_turns([tk.TextTurn(seq=0, text=text)], NORMALISER)
    # a character outside the BMP is one code point, not two surrogates, and is fine
    assert _tokens("\U0001f600") == []
    assert _tokens("\U00010400") == [("\U00010428", 0, 1)]


@HSETTINGS
@given(
    st.text(
        alphabet=st.characters(min_codepoint=0xD800, max_codepoint=0xDFFF), min_size=1, max_size=3
    ),
    mixed_text,
    mixed_text,
)
def test_normaliser_refuses_a_lone_surrogate_anywhere_in_the_text(
    surrogate: str, before: str, after: str
) -> None:
    nm, tk = normalise_mod(), tokens_mod()
    with pytest.raises(nm.LoneSurrogateError):
        tk.tokenise(before + surrogate + after, NORMALISER)


# ------------------------------------------------------------------ R05, R06


def test_normaliser_bidi_controls_dropped_in_key_only() -> None:
    bidi = [0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)]
    other = [0x00AD, 0x2060, 0xFEFF]
    ka, kha = "\u0915", "\u0916"
    for code in bidi + other:
        control = chr(code)
        text = ka + control + kha
        # inside a token: dropped from the key, does not split, and the span keeps it
        assert _tokens(text) == [(ka + kha, 0, 3)], hex(code)
        # leading and trailing ones are outside the span
        assert _tokens(control + ka) == [(ka, 1, 2)], hex(code)
        assert _tokens(ka + control) == [(ka, 0, 1)], hex(code)
        assert _tokens(control + ka + control + " " + control) == [(ka, 1, 2)], hex(code)
        # a text of ignorables only has no token
        assert _tokens(control * 3) == []
        assert _key(control + ka + control) == ka
    # an override inside a number: one token, the key joins both halves (ADR 0025 Decision 6)
    assert _tokens("1234\u202e9876") == [("12349876", 0, 9)]
    assert _tokens("\u2066\u0915\u2069 \u200e12\u200f") == [("\u0915", 1, 2), ("12", 5, 7)]
    # U+200B is a separator, not an ignorable
    assert _tokens(ka + "\u200b" + kha) == [(ka, 0, 1), (kha, 2, 3)]
    # every ignorable is dropped by comparison_key too
    assert _key("".join(chr(c) for c in sorted(IGNORABLE)) + "a") == "a"


# ------------------------------------------------------------------ I11 (rule change policy)

# Pinned digests of ``mr-Deva/1``. A change that can alter, for some input, a key, a token
# boundary, an offset or a refusal is a NEW id (ADR 0025 Decision 12): add ``mr-Deva/2`` here
# with its own fixture and table, and keep this entry. Never edit a value below to make the test
# pass.
PINNED = {
    "mr-Deva/1": {
        "rule_table": "c7cbc90b6e813fccbc3b09f27b7e358c54fe2e93461da77001944332f3d18f0c",
        "fixture": "a721b2dff255341211022b04aadb2c782ed737fb3e8328e47362d52447146a74",
        "behaviour": "3186bd3f0d6ce13d9b211723df01e9705a51415524735e1aa659c217029da14e",
    }
}


def _rule_table_rows() -> list[list[str]]:
    header = (
        "| Rule | Code points or category | Effect on key | Effect on display | Fixture cases |"
    )
    lines = ADR25.read_text("utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def digest_rule_table() -> str:
    return _sha("\n".join("\t".join(row) for row in _rule_table_rows()))


def digest_fixture() -> str:
    canonical = [
        [case["id"], case["text"], case["expected_tokens"], case["rules"]]
        for case in fixture_cases()
    ]
    return _sha(json.dumps(canonical, ensure_ascii=True, sort_keys=True))


def _behaviour_corpus() -> list[str]:
    """Characters whose Unicode data is the same on Python 3.11 to 3.14 (blocks that were fully
    assigned long before Unicode 14), put in four contexts."""
    ranges = [
        range(0x00, 0x180),
        range(0x0900, 0x0980),
        range(0x2000, 0x2070),
        range(0xFF10, 0xFF1A),
        range(0xFF21, 0xFF3B),
        range(0x2460, 0x2474),
        [0xFB01, 0xFEFF],
    ]
    chars = [chr(c) for r in ranges for c in r if not 0xD800 <= c <= 0xDFFF]
    corpus: list[str] = []
    for char in chars:
        corpus.append(char)
        corpus.append("\u0915" + char + "\u0916")
        corpus.append("a " + char)
        corpus.append(char + "\u0967")
    return corpus


def digest_behaviour() -> str:
    tk, nm = tokens_mod(), normalise_mod()
    out = []
    for text in _behaviour_corpus():
        out.append(
            [
                nm.comparison_key(text, NORMALISER),
                [(t.key, t.start, t.end) for t in tk.tokenise(text, NORMALISER)],
            ]
        )
    return _sha(json.dumps(out, ensure_ascii=True))


def test_normaliser_rule_change_requires_new_id() -> None:
    nm = normalise_mod()
    # the registry and the pins move together: a new id needs a new pinned entry
    assert set(PINNED) == set(nm.SUPPORTED_NORMALISERS)
    # an old id is never retired: results made with it must stay reproducible
    assert "mr-Deva/1" in nm.SUPPORTED_NORMALISERS
    pinned = PINNED["mr-Deva/1"]
    assert digest_rule_table() == pinned["rule_table"], (
        "the ADR 0025 rule table changed: that is a new normaliser id"
    )
    assert digest_fixture() == pinned["fixture"], "a fixture case changed: that is a new id"
    assert digest_behaviour() == pinned["behaviour"], (
        "an output of mr-Deva/1 changed: that is a new normaliser id, not an edit in place"
    )


def test_normaliser_digest_pins_are_sensitive_to_the_inputs() -> None:
    """The digests above must change when the thing they pin changes (a pin nobody can trip is
    no pin): mutate a copy of the fixture and of a row and check that the digest moves."""
    base = digest_fixture()
    cases = copy.deepcopy(fixture_cases())
    cases[0]["expected_tokens"][0]["end"] += 1
    canonical = [[c["id"], c["text"], c["expected_tokens"], c["rules"]] for c in cases]
    assert _sha(json.dumps(canonical, ensure_ascii=True, sort_keys=True)) != base
    rows = _rule_table_rows()
    assert len(rows) == 18
    assert _sha("\n".join("\t".join(r) for r in rows[:-1])) != digest_rule_table()
