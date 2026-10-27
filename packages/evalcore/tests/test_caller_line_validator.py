# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-13: the caller output validator (ADR 0028 rules 6 and 7; the table mr-caller-charset/1 of
docs/phase4/evidence/red/batch-0/staging/data/sim_mr.json; contract
docs/phase4/api-surface-batch-c.md section 3).

The validator refuses and never repairs. It is a pure function in ``tracer_evalcore.callerline``.
The tests read the allowed set from the fixture table itself (``reference_char_allowed``), so
the code cannot drift from the table without a failure, and they walk every code point of the
Basic Multilingual Plane. All text is invented; non-ASCII is written as escapes.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any

import pytest

import evalcore_phase4_batchc_support as sup  # type: ignore[import-not-found,unused-ignore]

CAP = 300


def check(reply: Any, *, max_line_chars: Any = CAP, language: Any = "mr") -> Any:
    module = sup.mod("callerline")
    return module.check_caller_reply(reply, language=language, max_line_chars=max_line_chars)


def refused(reply: str, code: str, **kwargs: Any) -> None:
    result = check(reply, **kwargs)
    assert result.kind == "refused", (ascii(reply)[:60], result)
    assert result.line is None
    assert result.refusal == code, (ascii(reply)[:60], result.refusal)


def accepted(reply: str, **kwargs: Any) -> Any:
    result = check(reply, **kwargs)
    assert result.kind == "line", (ascii(reply)[:60], result)
    assert result.refusal is None
    return result


# -------------------------------------------------------------------------- the cap (rule 6)


def test_caller_output_length_cap_required() -> None:
    """ADR 0028 rule 6 and I3: ``max_line_chars`` is a required value with no default, the same
    in the function and in every brain; a value outside 1 to 2,500 is refused; the cap counts
    code points of the line after the white space is removed, and a line of exactly the cap is
    accepted while one more is refused."""
    module = sup.mod("callerline")
    parameters = inspect.signature(module.check_caller_reply).parameters
    assert list(parameters) == ["reply", "language", "max_line_chars"]
    for name in ("language", "max_line_chars"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty, name
    with pytest.raises(TypeError):
        module.check_caller_reply(sup.LINE_A, language="mr")
    for bad in (0, -1, 2501, 2**53, True, 1.5, "300", None):
        with pytest.raises(ValueError, match=r"\S"):
            check(sup.LINE_A, max_line_chars=bad)
    letter = "\u0915"
    accepted(letter * CAP)
    refused(letter * (CAP + 1), "too_long")
    accepted(letter, max_line_chars=1)
    refused(letter * 2, "too_long", max_line_chars=1)
    accepted(letter * 2500, max_line_chars=2500)
    refused(letter * 2501, "too_long", max_line_chars=2500)
    # the count is of the line after the white space is removed, in code points
    accepted("  " + letter * 5 + " \n", max_line_chars=5)
    accepted("\u0915\u094d\u0937" * 2, max_line_chars=6)
    refused("\u0915\u094d\u0937" * 2, "too_long", max_line_chars=5)
    # a huge reply is refused by length, quickly and without quoting
    refused(letter * 1_000_000, "too_long")


def test_module_constants_are_the_ones_the_contract_and_the_fixture_state() -> None:
    """Contract section 2: the charset id equals the fixture's, the languages and refusal codes are
    closed lists, and `__all__` follows the ruff order."""
    module = sup.mod("callerline")
    assert module.CHARSET_ID == "mr-caller-charset/1" == sup.charset_rules()["id"]
    assert module.ALLOWED_LANGUAGES == ("mr",)
    assert module.REFUSAL_CODES == (
        "empty",
        "multiple_lines",
        "too_long",
        "not_nfc",
        "character_not_allowed",
        "no_script_letter",
    )
    assert module.__all__ == sorted(module.__all__, key=sup.all_order)
    assert set(module.__all__) >= {
        "ALLOWED_LANGUAGES",
        "CHARSET_ID",
        "REFUSAL_CODES",
        "ReplyCheck",
        "check_caller_reply",
    }
    assert sup.SENTINEL == sup.mod("simtypes").END_SENTINEL


# ----------------------------------------------------------- the table, code point by code point


def test_validator_agrees_with_the_fixture_table_on_every_bmp_code_point_and_astral_samples() -> (
    None
):
    """V01 to V09 as one oracle: for every code point of the BMP and a stride of the other
    planes, a line "letter + char + letter" gets the verdict the table gives (deny wins; V07
    categories; NFC; one line)."""
    module = sup.mod("callerline")
    codes = list(range(0x10000)) + list(range(0x10000, 0x110000, 29)) + [0x10FFFF, 0x1F600]
    wrong: list[str] = []
    for code in codes:
        line = "\u0915" + chr(code) + "\u0915"
        expected = sup.reference_verdict(line, CAP)
        got = module.check_caller_reply(line, language="mr", max_line_chars=CAP)
        if (got.kind, got.line) != expected:
            wrong.append(f"U+{code:04X}: {got.kind} != {expected[0]}")
    assert wrong == [], wrong[:20]


def test_the_reference_oracle_itself_has_teeth() -> None:
    """The oracle is the fixture table, so it must refuse what the table denies and allow what
    it allows: guards against a fixture that silently loses a rule."""
    assert sup.reference_char_allowed("\u0915")
    assert sup.reference_char_allowed("7")
    assert sup.reference_char_allowed(" ")
    assert sup.reference_char_allowed("\u0964")
    for char in ("a", "Z", "\u0966", "\u0970", "\u200d", "\u200c", "\u202e", "\x00", "\ue000", "<"):
        assert not sup.reference_char_allowed(char), ascii(char)
    assert sup.reference_verdict("END_CALL", CAP) == ("end_call", None)
    assert sup.reference_verdict("5521", CAP)[0] == "refused"  # V09


# ----------------------------------------------------------------------- the rules, by name


@pytest.mark.parametrize("char", [*"abcxyzABCXYZ", "\uff21", "\u0430", "\u03b1"])
def test_latin_letters_and_lookalikes_are_refused_v05(char: str) -> None:
    """V05: a Latin letter (and a fullwidth, Cyrillic or Greek lookalike, which no rule allows)
    anywhere in the line refuses it."""
    refused(sup.LINE_A + char, "character_not_allowed")
    refused(char + sup.LINE_A, "character_not_allowed")


def test_every_latin_letter_is_refused_v05() -> None:
    """V05 by name, for each of the 52 letters, so that no letter is skipped by a range typo."""
    for code in [*range(0x41, 0x5B), *range(0x61, 0x7B)]:
        refused(sup.LINE_A + " " + chr(code), "character_not_allowed")


def test_native_digits_and_the_abbreviation_sign_are_refused_v02() -> None:
    """V02: the caller writes ASCII digits; U+0966 to U+0970 are refused even though V01
    allows the block."""
    for code in range(0x0966, 0x0971):
        refused(sup.LINE_A + chr(code), "character_not_allowed")
    accepted(sup.LINE_C)  # ASCII digits are fine (V03)


def test_ascii_punctuation_is_the_listed_set_only_v04() -> None:
    """V04: exactly the listed ASCII punctuation is allowed; every other printable ASCII symbol is
    refused (markup, backticks, braces, slashes)."""
    allowed = " !\"'(),-.:;?"
    for char in allowed:
        accepted(sup.LINE_A + char + sup.LINE_B)
    for code in range(0x21, 0x7F):
        char = chr(code)
        if char.isalnum() or char in allowed:
            continue
        refused(sup.LINE_A + char + sup.LINE_B, "character_not_allowed")


def test_invisible_characters_and_joiners_are_refused_v06_even_zwj_and_zwnj() -> None:
    """V06 wins over V01 and over ADR 0025: the caller's line has no ZWJ or ZWNJ (the agent's
    text keeps them, ADR 0028 rule 4)."""
    invisible = [0x00AD, *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x206A)]
    invisible.append(0xFEFF)
    for code in invisible:
        refused(sup.LINE_A + chr(code) + sup.LINE_B, "character_not_allowed")
    refused("\u0915\u094d\u200d\u0937", "character_not_allowed")
    refused("\u0915\u200c\u0937", "character_not_allowed")


def test_controls_format_private_and_unassigned_are_refused_v07() -> None:
    """V07: controls, format, private-use, unassigned and surrogate code points are refused (stable
    code points only, so the test holds on Python 3.11 to 3.14)."""
    samples = [
        "\x00",
        "\x01",
        "\x07",
        "\x1b",
        "\x7f",
        "\x80",
        "\x9f",
        "\t",
        "\r",
        "\ue000",
        "\uf8ff",
        "\U000f0000",
        "\u0378",
        "\ufffe",
        "\uffff",
        "\U0010ffff",
        "\ud800",
        "\udfff",
    ]
    for char in samples:
        result = check(sup.LINE_A + char + sup.LINE_B)
        assert result.kind == "refused", ascii(char)
        assert result.refusal == "character_not_allowed", ascii(char)


def test_nfc_is_required_and_nothing_is_normalised_v08() -> None:
    """V08: U+0958 to U+095F never survive NFC and are refused; a decomposed pair that NFC
    would compose is refused; a pair NFC leaves alone is accepted and returned unchanged."""
    for code in range(0x0958, 0x0960):
        refused("\u0915" + chr(code), "not_nfc")
    refused("\u0928\u093c", "not_nfc")  # NFC composes it to U+0929
    result = accepted("\u0915\u093c")  # U+0958 is excluded from composition, so this is NFC
    assert result.line == "\u0915\u093c"
    result = accepted("\u0929")
    assert result.line == "\u0929"


def test_a_line_needs_a_devanagari_letter_v09() -> None:
    """V09: a line of digits, punctuation, danda, vowel signs or marks alone is refused; the letter
    range is U+0904 to U+0939 with both ends."""
    marks = ("\u0900", "\u0901", "\u0902", "\u0903")  # below U+0904: marks, not letters
    for text in ("5521", "...", "?!", "5521 9000.", "\u0964", "\u0965", "\u093e", "\u094d", *marks):
        refused(text, "no_script_letter")
    accepted("5521 \u0915")
    accepted("\u0904")
    accepted("\u0939")
    refused("\u093a", "no_script_letter")  # one past U+0939


# ------------------------------------------------------------------- the end sentinel (rule 7)


def test_the_sentinel_is_matched_exactly_as_the_whole_reply() -> None:
    """ADR 0028 rule 7: only the exact whole reply is the end; every near miss (case, period,
    quotes, zero-width space, fullwidth, in a sentence, with a second line) is refused."""
    for reply in (
        "END_CALL",
        "  END_CALL  ",
        "END_CALL\n",
        "\n\nEND_CALL\r\n",
        "\tEND_CALL \u00a0",
    ):
        result = check(reply)
        assert result.kind == "end_call", ascii(reply)
        assert result.line is None
        assert result.refusal is None
    cases = {
        "end_call": "character_not_allowed",
        "End_Call": "character_not_allowed",
        "END_CALL.": "character_not_allowed",
        "END-CALL": "character_not_allowed",
        "END_CALL END_CALL": "character_not_allowed",
        '"END_CALL"': "character_not_allowed",
        "\u200bEND_CALL": "character_not_allowed",
        "END_CALL\u200b": "character_not_allowed",
        "\uff25\uff2e\uff24\uff3f\uff23\uff21\uff2c\uff2c": "character_not_allowed",
        sup.LINE_A + " END_CALL": "character_not_allowed",
        "END_CALL " + sup.LINE_A: "character_not_allowed",
        "END_CALL\n" + sup.LINE_A: "multiple_lines",
        "END_CALL\nEND_CALL": "multiple_lines",
        sup.LINE_A + "\nEND_CALL": "multiple_lines",
    }
    for reply, code in cases.items():
        refused(reply, code)


def test_the_sentinel_does_not_depend_on_the_cap() -> None:
    """Rule 7: the sentinel is matched before the length rule, so a small cap cannot make the caller
    unable to hang up."""
    assert check("END_CALL", max_line_chars=1).kind == "end_call"
    assert check("END_CALL", max_line_chars=2).kind == "end_call"


# ------------------------------------------------------- one line, white space, no repair


def test_exactly_one_line_and_surrounding_white_space_is_the_only_thing_removed() -> None:
    """Rule 6: one line only, and `str.strip()` is the only thing removed (interior spaces stay,
    zero-width space is not white space)."""
    refused(sup.LINE_A + "\n" + sup.LINE_B, "multiple_lines")
    refused(sup.LINE_A + "\r\n" + sup.LINE_B, "multiple_lines")
    refused("\n".join(sup.LINES), "multiple_lines")
    result = accepted("\n  " + sup.LINE_B + "  \n\n")
    assert result.line == sup.LINE_B
    # interior spaces are kept as they are
    spaced = "\u0915  \u0916   \u0917"
    assert accepted(spaced).line == spaced
    # white space is str.strip(): no-break space, ideographic space, NEL go; zero-width space stays
    assert accepted("\u00a0" + sup.LINE_A + "\u3000").line == sup.LINE_A
    assert accepted("\x85" + sup.LINE_A).line == sup.LINE_A
    refused(sup.LINE_A + "\u200b", "character_not_allowed")
    refused("\r" + sup.LINE_A.replace("\u0928", "\u0928\r"), "character_not_allowed")


def test_a_refusal_is_never_a_repair_and_an_acceptance_returns_the_input_unchanged() -> None:
    """Rule 6: the validator refuses and never repairs: no stripping of characters, no re-
    normalisation, no truncation."""
    for reply in (*sup.LINES, sup.LINE_A + "\u0915\u093c", sup.LINE_A + " " + "5521"):
        assert accepted(reply).line == reply
    for reply in (
        sup.LINE_A + "a",  # would be fine if the Latin letter were stripped
        sup.LINE_A + "\u200d",  # would be fine if the joiner were stripped
        "\u0958",  # would be fine if it were re-normalised
        sup.LINE_A + "\n" + sup.LINE_B,  # would be fine if truncated at the first line
        "\u0915" * 400,  # would be fine if truncated to the cap
    ):
        result = check(reply)
        assert result.kind == "refused"
        assert result.line is None


def test_the_precedence_of_refusal_codes() -> None:
    """Contract section 2: when several rules fail the first of the listed order is reported, so a
    refusal code is predictable."""
    refused("", "empty")
    refused("  \n\t ", "empty")
    refused(sup.LINE_A + "\n" + "\u0915" * 400, "multiple_lines")
    refused("a" + "\u0915" * 400, "too_long")
    refused("a\u0958", "not_nfc")
    refused("a", "character_not_allowed")


def test_a_refusal_never_quotes_the_refused_text() -> None:
    """ADR 0028 rule 6: the text of a refused reply is never kept; the verdict holds a code."""
    module = sup.mod("callerline")
    for text in (
        sup.CANARY_REFUSED,
        sup.LINE_A + " " + sup.CANARY_REFUSED,
        sup.CANARY_REFUSED + "\n" + sup.LINE_A,
    ):
        result = check(text)
        assert result.kind == "refused"
        assert result.refusal in module.REFUSAL_CODES
        assert sup.CANARY_REFUSED not in repr(result)
        assert sup.CANARY_REFUSED not in str(result)
        assert sup.CANARY_REFUSED not in repr(dataclasses.asdict(result))


def test_the_language_is_mr_only_and_has_no_default() -> None:
    """SCOPE O9 and rule 6: only mr has an allowed set in Phase 4, and the language is never
    assumed."""
    for language in ("hi", "en", "MR", "mr-IN", "", None, 3):
        with pytest.raises(ValueError, match=r"\S"):
            check(sup.LINE_A, language=language)
    accepted(sup.LINE_A, language="mr")


def test_a_reply_that_is_not_text_is_a_type_error_that_does_not_quote_it() -> None:
    """Rule 6 and error hygiene: a non-text reply is a programming error, reported without quoting
    the value."""
    for bad in (None, b"x", 5, ["a"], bytearray(b"x")):
        with pytest.raises(TypeError):
            check(bad)
    with pytest.raises(TypeError) as excinfo:
        check(sup.CANARY_REFUSED.encode("ascii"))
    assert sup.CANARY_REFUSED not in str(excinfo.value)


def test_reply_check_is_a_frozen_record_that_validates_its_own_shape() -> None:
    """The verdict is a closed record: a line has text, an end has nothing, a refusal has a code
    from the list."""
    module = sup.mod("callerline")
    ok = module.ReplyCheck(kind="line", line=sup.LINE_A, refusal=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ok.kind = "end_call"
    module.ReplyCheck(kind="end_call", line=None, refusal=None)
    module.ReplyCheck(kind="refused", line=None, refusal="empty")
    for kwargs in (
        {"kind": "line", "line": None, "refusal": None},
        {"kind": "line", "line": "", "refusal": None},
        {"kind": "line", "line": sup.LINE_A, "refusal": "empty"},
        {"kind": "end_call", "line": sup.LINE_A, "refusal": None},
        {"kind": "end_call", "line": None, "refusal": "empty"},
        {"kind": "refused", "line": sup.LINE_A, "refusal": "empty"},
        {"kind": "refused", "line": None, "refusal": None},
        {"kind": "refused", "line": None, "refusal": "free text"},
        {"kind": "other", "line": None, "refusal": None},
    ):
        with pytest.raises(ValueError, match=r"\S"):
            module.ReplyCheck(**kwargs)


def test_every_scripted_line_of_the_marathi_fixture_passes_the_table() -> None:
    """Guards the P4-00f fixture against the P4-00f table: a scripted line that its own
    character table refuses would be a fixture defect for indic-linguistics, not for the code."""
    scenarios = sup.sim_mr()["scenarios"]
    lines = [turn["text"] for s in scenarios for turn in s["scripted_turns"]]
    assert len(lines) == 28
    for line in lines:
        accepted(line, max_line_chars=sup.mod("simtypes").MAX_LINE_CHARS)
