# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-36 follow-up (batch 24): extra F8 tests found by the mutation check of the REAL converter
(``tests/support/mappings/voicera_transcript.py``) and the tests for the six dev ambiguities
N1 to N6 (decision log "AUTO-APPROVED dev F8 ambiguities N1-N6") that had no test:

- N1 a blank line 1 or line 2 (warned with the line as ``raw``, the rest still mapped);
- N2 an early end of file (a file that ends before line 2 gets no delimiter warning);
- N3 a call id with a lone surrogate (not stored, no reference, ``text_not_valid_unicode`` at
  ``f8:1/call_id`` without ``raw``);
- N4 a line 2 that is neither the delimiter nor a message (only ``delimiter_not_found``);
- N5 timestamps with non-ASCII digits or outside the UTC range (``timestamp_not_parseable``);
- N6 a bad declaration, privacy marker or ``created_at`` raises ``ValidationError`` even when the
  text is empty.

Survivors of the mutation check that are test gaps (partial relaxations of the extended ISO
form, offset minutes, the offset sign, fraction padding, an ``absent`` provenance row for a
field the converter fills) are pinned here. The existing ``test_mapping_f8.py`` is not edited.
The input is built by hand from invented values; the owner's real call file is not read here.
All non-ASCII characters are written as escapes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from tracer_evalschema.base import EntityBase

from ._support_b3 import language_declaration, privacy_human
from ._support_b6 import check_records_valid, mapping
from ._support_b14 import (
    check_no_fabrication_f8,
    check_warnings_quote_sources_f8,
    run_f8,
    twin_text,
)

UUID_TEXT = "5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11"
HEADER = f"call_id={UUID_TEXT}\n---\n"
STAMP = "2026-10-24T05:31:07.412+00:00"
LONE = chr(0xD800)


def message(speaker: str = "user", text: str = "hello", stamp: str = STAMP) -> str:
    return f"[{stamp}] {speaker}: {text}\n"


def codes(result: Any) -> list[str]:
    return [w.code for w in result.warnings]


def module() -> Any:
    return mapping("voicera_transcript")


def one_stamp(stamp: str) -> Any:
    """Run a one-message file whose only variable part is the timestamp."""
    result = run_f8(HEADER + message("user", "hello", stamp))
    assert [t.text for t in result.turns] == ["hello"], "the turn is mapped whatever the stamp"
    return result


def instant_text(result: Any) -> str | None:
    wall = result.turns[0].wall_time
    return None if wall is None else wall.at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# ======================================================================= N1: blank line 1 or 2

BLANK_LINES = ["", " ", "\t", "  \t ", chr(0xA0), chr(0x2003)]


@pytest.mark.parametrize("blank", BLANK_LINES, ids=[ascii(b) for b in BLANK_LINES])
def test_f8_n1_blank_line_1_is_a_missing_header(blank: str) -> None:
    """N1: a blank first line is not a ``call_id`` header. One ``header_not_recognized`` at
    ``f8:1`` with the line itself as ``raw`` (so the white space is reported as sent); no call
    reference; line 2 is still read as the delimiter and the messages are still mapped."""
    text = blank + "\n---\n" + message("user", "one") + message("assistant", "two")
    result = run_f8(text)
    assert codes(result) == ["header_not_recognized"]
    warning = result.warnings[0]
    assert (warning.path, warning.raw) == ("f8:1", blank)
    assert [t.text for t in result.turns] == ["one", "two"]
    assert [t.seq for t in result.turns] == [0, 1]
    assert len(result.transcripts) == 1
    assert result.transcripts[0].provider_call_ref is None
    assert result.external_refs == ()
    assert "f8:2" in result.consumed
    check_records_valid(result)
    check_no_fabrication_f8(result, text, module())
    check_warnings_quote_sources_f8(result, text)


@pytest.mark.parametrize("blank", BLANK_LINES, ids=[ascii(b) for b in BLANK_LINES])
def test_f8_n1_blank_line_2_is_a_missing_delimiter(blank: str) -> None:
    """N1: a blank second line is not the ``---`` delimiter. One ``delimiter_not_found`` at
    ``f8:2`` with the line as ``raw``; the header is read, the call reference is kept and the
    messages are mapped; nothing is consumed as a delimiter."""
    text = f"call_id={UUID_TEXT}\n{blank}\n" + message("user", "one")
    result = run_f8(text)
    assert codes(result) == ["delimiter_not_found"]
    warning = result.warnings[0]
    assert (warning.path, warning.raw) == ("f8:2", blank)
    assert [t.text for t in result.turns] == ["one"]
    assert result.transcripts[0].provider_call_ref is not None
    assert result.transcripts[0].provider_call_ref.value == UUID_TEXT
    assert "f8:2" not in result.consumed
    check_records_valid(result)
    check_no_fabrication_f8(result, text, module())
    check_warnings_quote_sources_f8(result, text)


def test_f8_n1_blank_lines_1_and_2_give_both_warnings_in_file_order() -> None:
    """N1: both blank. Both warnings, line 1 first; the messages after them are mapped."""
    result = run_f8("\n\n" + message("assistant", "hi"))
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("header_not_recognized", "f8:1", ""),
        ("delimiter_not_found", "f8:2", ""),
    ]
    assert [t.text for t in result.turns] == ["hi"]


def test_f8_n1_blank_line_1_with_crlf_endings_reports_an_empty_raw() -> None:
    """N1 with CRLF: the CR is a line ending, not text, so the blank line 1 is ``""``."""
    text = "\r\n---\r\n" + message("user", "one").replace("\n", "\r\n")
    result = run_f8(text)
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("header_not_recognized", "f8:1", "")
    ]
    assert [t.text for t in result.turns] == ["one"]


# ===================================================================== N2: early end of file


@pytest.mark.parametrize(
    "text",
    [
        f"call_id={UUID_TEXT}",
        f"call_id={UUID_TEXT}\n",
        f"call_id={UUID_TEXT}\r\n",
    ],
    ids=["header-no-newline", "header-lf", "header-crlf"],
)
def test_f8_n2_file_that_ends_after_the_header_has_no_warning(text: str) -> None:
    """N2: a file with only the header line ends before line 2; nothing is missing that the
    file could have held, so there is NO ``delimiter_not_found``. The Transcript is made with
    its call reference and no Turn."""
    result = run_f8(text)
    assert codes(result) == []
    assert len(result.transcripts) == 1
    assert result.turns == ()
    assert result.transcripts[0].provider_call_ref.value == UUID_TEXT
    assert result.external_ref_sources == ("f8:1/call_id",)
    check_records_valid(result)


def test_f8_n2_file_that_ends_after_the_delimiter_has_no_warning() -> None:
    """N2: header and delimiter, then the end: a Transcript, no Turn, no warning."""
    for text in (HEADER, HEADER.rstrip("\n")):
        result = run_f8(text)
        assert codes(result) == []
        assert len(result.transcripts) == 1
        assert result.turns == ()
        assert "f8:2" in result.consumed


def test_f8_n2_file_that_is_one_message_line_warns_only_about_the_header() -> None:
    """N2: line 1 is a message and the file ends: ``header_not_recognized`` only (no delimiter
    warning, there is no line 2); the message is mapped (M10)."""
    result = run_f8(message("user", "only").rstrip("\n"))
    assert codes(result) == ["header_not_recognized"]
    assert [t.text for t in result.turns] == ["only"]


def test_f8_n2_file_that_is_one_unrecognised_line_makes_nothing_but_the_warning() -> None:
    """N2: a single line that is not a header, with nothing after it: the warning, no records
    (no Turn and no recognised header, so no Transcript)."""
    result = run_f8("hello")
    assert codes(result) == ["header_not_recognized"]
    assert result.records == ()


# ============================================================== N3: lone surrogate in call id

SURROGATE_IDS = {
    "alone": LONE,
    "inside-a-word": "ab" + LONE + "cd",
    "after-a-uuid": UUID_TEXT + LONE,
    "inside-a-uuid": UUID_TEXT[:20] + LONE + UUID_TEXT[21:],
}


@pytest.mark.parametrize("value", list(SURROGATE_IDS.values()), ids=list(SURROGATE_IDS))
def test_f8_n3_call_id_with_a_lone_surrogate_is_not_stored(value: str) -> None:
    """N3: an identifier that cannot be stored as UTF-8 is NOT repaired into a different id
    (U+FFFD would be a made-up value for a reference): no ``provider_call_ref``, no external
    reference, one ``text_not_valid_unicode`` at ``f8:1/call_id`` WITHOUT ``raw``. The
    Transcript and the Turns are still made. Any ``call_id_not_uuid`` that goes with it (the
    value is not a UUID) quotes a ``raw`` with no surrogate left."""
    text = f"call_id={value}\n---\n" + message("user", "after")
    result = run_f8(text)
    assert len(result.transcripts) == 1
    assert result.transcripts[0].provider_call_ref is None
    assert result.external_refs == ()
    assert result.external_ref_sources == ()
    unicode_warnings = [w for w in result.warnings if w.code == "text_not_valid_unicode"]
    assert len(unicode_warnings) == 1
    assert (unicode_warnings[0].path, unicode_warnings[0].raw) == ("f8:1/call_id", None)
    assert set(codes(result)) <= {"call_id_not_uuid", "text_not_valid_unicode"}
    for warning in result.warnings:
        for part in (warning.path, warning.raw, warning.message):
            assert part is None or LONE not in part
    assert [t.text for t in result.turns] == ["after"]
    assert [t.seq for t in result.turns] == [0]
    # nothing in any record can hold the surrogate: the dumps encode as UTF-8
    for record in result.records:
        json.dumps(record.model_dump(mode="json")).encode("utf-8")
        assert LONE not in record.model_dump_json()
    check_records_valid(result)


def test_f8_n3_a_clean_call_id_next_to_a_surrogate_in_text_is_still_stored() -> None:
    """N3 boundary: the surrogate is in a message, not in the id: the id is stored and only
    the text warning appears (``f8:3/text``)."""
    result = run_f8(HEADER + message("user", "a" + LONE + "b"))
    assert result.transcripts[0].provider_call_ref.value == UUID_TEXT
    assert [(w.code, w.path) for w in result.warnings] == [("text_not_valid_unicode", "f8:3/text")]


# ================================================ N4: line 2 neither delimiter nor message

LINE2_JUNK = ["hello world", "----", "---x", "  ---", "[not a stamp] nothing", "[x]user: hi"]


@pytest.mark.parametrize("junk", LINE2_JUNK, ids=[ascii(j) for j in LINE2_JUNK])
def test_f8_n4_line_2_that_is_not_a_message_gets_only_delimiter_not_found(junk: str) -> None:
    """N4: a non-delimiter second line that is not a message is a missing delimiter and nothing
    else: ONE ``delimiter_not_found`` (path ``f8:2``, raw the line), NOT also an
    ``unparsed_line`` (the line is not warned twice). It makes no Turn; later messages are
    mapped and numbered from 0. Line 3 onwards keeps its own ``unparsed_line`` rule."""
    text = f"call_id={UUID_TEXT}\n{junk}\n" + message("user", "after")
    result = run_f8(text)
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("delimiter_not_found", "f8:2", junk)
    ]
    assert [(t.seq, t.text) for t in result.turns] == [(0, "after")]
    assert result.transcripts[0].provider_call_ref.value == UUID_TEXT
    check_records_valid(result)
    check_no_fabrication_f8(result, text, module())
    check_warnings_quote_sources_f8(result, text)


def test_f8_n4_the_same_junk_on_line_3_is_unparsed_line() -> None:
    """N4 contrast: the same text on line 3 is an ``unparsed_line`` (the delimiter was found)."""
    result = run_f8(HEADER + "hello world\n" + message("user", "after"))
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("unparsed_line", "f8:3", "hello world")
    ]
    assert [t.text for t in result.turns] == ["after"]


def test_f8_n4_line_2_that_is_a_message_is_mapped_and_warned_once() -> None:
    """N4 contrast (M10): a message on line 2 is mapped, with only ``delimiter_not_found``."""
    result = run_f8(f"call_id={UUID_TEXT}\n" + message("user", "second"))
    assert [(w.code, w.path) for w in result.warnings] == [("delimiter_not_found", "f8:2")]
    assert [t.text for t in result.turns] == ["second"]


# ====================================== N5: non-ASCII digits and timestamps outside the UTC range

ARABIC = "".join(chr(0x0660 + int(d)) for d in "20261024")  # 2026 10 24 in Arabic-Indic digits
DEVANAGARI_YEAR = "".join(chr(0x0966 + int(d)) for d in "2026")
FULLWIDTH = {d: chr(0xFF10 + int(d)) for d in "0123456789"}


def _fw(text: str) -> str:
    return "".join(FULLWIDTH.get(c, c) for c in text)


NON_ASCII_STAMPS = {
    "arabic-indic-everything": (
        f"{ARABIC[:4]}-{ARABIC[4:6]}-{ARABIC[6:]}T"
        + "".join(chr(0x0660 + int(d)) for d in "05")
        + ":"
        + "".join(chr(0x0660 + int(d)) for d in "31")
        + ":"
        + "".join(chr(0x0660 + int(d)) for d in "07")
        + "Z"
    ),
    "devanagari-year-only": DEVANAGARI_YEAR + "-10-24T05:31:07Z",
    "fullwidth-everything": _fw("2026-10-24T05:31:07.412+00:00"),
    "fullwidth-hour-only": "2026-10-24T" + FULLWIDTH["0"] + "5:31:07Z",
    "fullwidth-fraction-only": "2026-10-24T05:31:07." + FULLWIDTH["4"] + "12Z",
    "fullwidth-offset-only": "2026-10-24T05:31:07.412+" + FULLWIDTH["0"] + "0:00",
    "arabic-indic-offset-minutes": "2026-10-24T05:31:07Z".replace("Z", "+05:") + chr(0x0663) + "0",
}


@pytest.mark.parametrize("stamp", list(NON_ASCII_STAMPS.values()), ids=list(NON_ASCII_STAMPS))
def test_f8_n5_non_ascii_digits_are_not_an_instant(stamp: str) -> None:
    """N5: only ASCII digits make an ISO-8601 instant (Python's ``int`` would read Arabic-Indic,
    Devanagari or full-width digits, so a ``\\d`` pattern would accept them). Any such digit in
    any part: no ``wall_time``, one ``timestamp_not_parseable`` at ``f8:3/timestamp`` with the
    stamp as ``raw``; the Turn and its text are still mapped."""
    result = one_stamp(stamp)
    assert result.turns[0].wall_time is None
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("timestamp_not_parseable", "f8:3/timestamp", stamp)
    ]
    check_records_valid(result)


def test_f8_n5_a_call_id_with_non_ascii_digits_is_not_uuid_shaped() -> None:
    """N5 for the header: a UUID-shaped id with Arabic-Indic digits is not a bare UUID (8-4-4-4-12
    ASCII hex digits): it is kept and warned ``call_id_not_uuid``."""
    value = "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in UUID_TEXT)
    assert value != UUID_TEXT
    result = run_f8(f"call_id={value}\n---\n" + message())
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("call_id_not_uuid", "f8:1/call_id", value)
    ]
    assert result.transcripts[0].provider_call_ref.value == value


OUT_OF_RANGE = {
    # converting to UTC leaves 0001-01-01 or passes 9999-12-31: no instant exists
    "before-year-1": "0001-01-01T00:00:00+05:30",
    "before-year-1-fraction": "0001-01-01T00:00:00.5+00:01",
    "after-year-9999": "9999-12-31T23:59:59-05:00",
    "after-year-9999-fraction": "9999-12-31T23:59:59.999999-00:01",
}


@pytest.mark.parametrize("stamp", list(OUT_OF_RANGE.values()), ids=list(OUT_OF_RANGE))
def test_f8_n5_an_instant_outside_the_utc_range_is_not_parseable(stamp: str) -> None:
    """N5: a well-formed stamp that cannot be written in UTC inside years 0001 to 9999 is
    ``timestamp_not_parseable`` (and never raises ``OverflowError``): no ``wall_time``, the
    turn is kept, the warning quotes the stamp as sent."""
    result = one_stamp(stamp)
    assert result.turns[0].wall_time is None
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("timestamp_not_parseable", "f8:3/timestamp", stamp)
    ]
    check_records_valid(result)


IN_RANGE = {
    "first-instant": ("0001-01-01T00:00:00Z", "0001-01-01T00:00:00.000000Z"),
    "first-instant-by-offset": ("0001-01-01T05:30:00+05:30", "0001-01-01T00:00:00.000000Z"),
    "last-instant": ("9999-12-31T23:59:59.999999Z", "9999-12-31T23:59:59.999999Z"),
    "last-instant-by-offset": ("9999-12-31T18:29:59.999999-05:30", "9999-12-31T23:59:59.999999Z"),
    "three-digit-year": ("0999-06-01T00:00:00Z", "0999-06-01T00:00:00.000000Z"),
}


@pytest.mark.parametrize(("stamp", "expected"), list(IN_RANGE.values()), ids=list(IN_RANGE))
def test_f8_n5_the_edges_of_the_utc_range_are_instants(stamp: str, expected: str) -> None:
    """N5 boundary: the first and last representable UTC instants parse (no warning about the
    stamp) and are stored with the four-digit year and six fractional digits."""
    result = one_stamp(stamp)
    assert instant_text(result) == expected
    assert result.turns[0].wall_time is not None
    assert result.turns[0].wall_time.meaning == "unknown"
    assert "timestamp_not_parseable" not in codes(result)


# ===================================== N6: caller arguments, even on empty text

EMPTY_TEXTS = {
    "empty": "",
    "newline": "\n",
    "white-space": "  \n\t\n \n",
    "header-only": f"call_id={UUID_TEXT}",
    "twin": "twin",
}
BAD_ARGUMENTS: dict[str, dict[str, Any]] = {
    "declaration-script-subtag": {
        "declared_language": {**language_declaration(), "language": "mr-Deva-IN"}
    },
    "declaration-source": {"declared_language": {**language_declaration(), "source": "observed"}},
    "declaration-declared-by-at": {
        "declared_language": {**language_declaration(), "declared_by": "someone@example.com"}
    },
    "declaration-unsupported-language": {
        "declared_language": {**language_declaration(), "language": "xx-YY"}
    },
    "declaration-incomplete": {"declared_language": {"language": "mr-IN"}},
    "declaration-empty": {"declared_language": {}},
    "privacy-incomplete": {"privacy": {"human_subject": True}},
    "privacy-unknown-key": {"privacy": {**privacy_human(), "nonsense": 1}},
    "created-at-text": {"created_at": "not a time"},
    "created-at-naive": {"created_at": "2026-10-24T05:31:07"},
}


@pytest.mark.parametrize("kind", list(BAD_ARGUMENTS))
@pytest.mark.parametrize("which", list(EMPTY_TEXTS))
def test_f8_n6_a_bad_caller_argument_raises_whatever_the_text(which: str, kind: str) -> None:
    """N6: the caller's own arguments (the declaration, the privacy marker, ``created_at``) are
    checked BEFORE the content is read, so a bad one raises the model's ``ValidationError`` even
    when the text is empty (no records would be made), white space only, or a header alone. It
    is never turned into a warning and never repaired."""
    text = twin_text() if which == "twin" else EMPTY_TEXTS[which]
    with pytest.raises(ValidationError):
        run_f8(text, **BAD_ARGUMENTS[kind])


def test_f8_n6_a_valid_declaration_on_empty_text_makes_nothing() -> None:
    """N6 contrast: a valid declaration on an empty file is not a reason for a Transcript; the
    result is exactly what the empty file gives without it (``empty_input``, no records)."""
    plain = run_f8("")
    declared = run_f8("", declared_language=language_declaration())
    assert declared.records == plain.records == ()
    assert codes(declared) == codes(plain) == ["empty_input"]
    assert (declared.warnings[0].path, declared.warnings[0].raw) == (None, None)


# ====================================================== survivors of the mutation check: timestamps

PARTIAL_FORMS = {
    # each differs from the extended form in ONE part only; the part is the id
    "basic-date": "20261024T05:31:07.412+00:00",
    "basic-time": "2026-10-24T053107.412+00:00",
    "time-without-seconds-colon": "2026-10-24T05:3107.412+00:00",
    "no-seconds": "2026-10-24T05:31+00:00",
    "offset-without-colon": "2026-10-24T05:31:07.412+0000",
    "offset-hours-only": "2026-10-24T05:31:07.412+00",
    "offset-without-colon-z-free": "2026-10-24T05:31:07+0530",
    "date-only": "2026-10-24",
    "date-with-slashes": "2026/10/24T05:31:07.412+00:00",
    "fraction-without-digits": "2026-10-24T05:31:07.+00:00",
    "two-spaces-as-separator": "2026-10-24  05:31:07.412+00:00",
    "year-with-two-digits": "26-10-24T05:31:07.412+00:00",
    "leading-space": " 2026-10-24T05:31:07.412+00:00",
    "trailing-space": "2026-10-24T05:31:07.412+00:00 ",
    "hour-24": "2026-10-24T24:00:00+00:00",
    "minute-60": "2026-10-24T05:60:07+00:00",
    "second-61": "2026-10-24T05:31:61+00:00",
    "month-13": "2026-13-24T05:31:07+00:00",
    "day-0": "2026-10-00T05:31:07+00:00",
    "offset-with-seconds": "2026-10-24T05:31:07+05:30:00",
}


@pytest.mark.parametrize("stamp", list(PARTIAL_FORMS.values()), ids=list(PARTIAL_FORMS))
def test_f8_a_stamp_that_is_not_the_extended_iso_form_in_every_part_is_not_an_instant(
    stamp: str,
) -> None:
    """The reading is the extended form in every part (``YYYY-MM-DD``, ``T`` or one space,
    ``hh:mm:ss``, an optional ``.digits``, ``Z`` or ``+hh:mm``): relaxing any ONE part (basic
    date, basic time, an offset without its colon or with hours only, a missing second, extra
    white space, a field out of range) gives no ``wall_time`` and ``timestamp_not_parseable``,
    never a guess. (The mutation check found that only the all-basic form was tested.)"""
    result = one_stamp(stamp)
    assert result.turns[0].wall_time is None
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("timestamp_not_parseable", "f8:3/timestamp", stamp)
    ]


OFFSETS = {
    "negative-hours": ("2026-10-24T05:31:07-05:00", "2026-10-24T10:31:07.000000Z"),
    "negative-half-hour": ("2026-10-24T05:31:07-05:30", "2026-10-24T11:01:07.000000Z"),
    "positive-half-hour": ("2026-10-24T05:31:07+05:30", "2026-10-24T00:01:07.000000Z"),
    "positive-minutes-only": ("2026-10-24T05:31:07+00:45", "2026-10-24T04:46:07.000000Z"),
    "negative-minutes-only": ("2026-10-24T05:31:07-00:45", "2026-10-24T06:16:07.000000Z"),
    "largest-positive": ("2026-10-24T05:31:07+23:59", "2026-10-23T05:32:07.000000Z"),
    "largest-negative": ("2026-10-24T05:31:07-23:59", "2026-10-25T05:30:07.000000Z"),
    "negative-zero": ("2026-10-24T05:31:07-00:00", "2026-10-24T05:31:07.000000Z"),
    "z-and-fraction-crossing-midnight": (
        "2026-10-24T23:59:59.9995-00:30",
        "2026-10-25T00:29:59.999500Z",
    ),
}


@pytest.mark.parametrize(("stamp", "expected"), list(OFFSETS.values()), ids=list(OFFSETS))
def test_f8_offsets_are_converted_with_their_sign_without_a_warning(
    stamp: str, expected: str
) -> None:
    """M3: an offset is subtracted with its sign (a ``-`` offset moves the instant later), also
    across a day boundary; the largest offsets accepted are ``+-23:59``."""
    result = one_stamp(stamp)
    assert instant_text(result) == expected
    assert codes(result) == []


@pytest.mark.parametrize(
    "offset", ["+24:00", "-24:00", "+05:60", "-05:60", "+00:99", "+99:00", "+23:60"]
)
def test_f8_an_offset_out_of_range_is_not_an_instant(offset: str) -> None:
    """An offset of a day or more, or with minutes of 60 or more, is not an instant."""
    stamp = "2026-10-24T05:31:07" + offset
    result = one_stamp(stamp)
    assert result.turns[0].wall_time is None
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("timestamp_not_parseable", "f8:3/timestamp", stamp)
    ]


FRACTIONS = {
    "one-digit": ("2026-10-24T05:31:07.4Z", "2026-10-24T05:31:07.400000Z", []),
    "two-digits": ("2026-10-24T05:31:07.41Z", "2026-10-24T05:31:07.410000Z", []),
    "leading-zeros": ("2026-10-24T05:31:07.004Z", "2026-10-24T05:31:07.004000Z", []),
    "five-digits": ("2026-10-24T05:31:07.00001Z", "2026-10-24T05:31:07.000010Z", []),
    "all-zero": ("2026-10-24T05:31:07.000Z", "2026-10-24T05:31:07.000000Z", []),
    "seven-digits-cut-not-rounded": (
        "2026-10-24T05:31:07.1234569Z",
        "2026-10-24T05:31:07.123456Z",
        ["time_precision_reduced"],
    ),
    "seven-zero-digits-still-warn": (
        "2026-10-24T05:31:07.1234560Z",
        "2026-10-24T05:31:07.123456Z",
        ["time_precision_reduced"],
    ),
    "long-fraction": (
        "2026-10-24T05:31:07.999999999999Z",
        "2026-10-24T05:31:07.999999Z",
        ["time_precision_reduced"],
    ),
}


@pytest.mark.parametrize(
    ("stamp", "expected", "expect_codes"), list(FRACTIONS.values()), ids=list(FRACTIONS)
)
def test_f8_fraction_digits_are_padded_on_the_right_and_cut_never_rounded(
    stamp: str, expected: str, expect_codes: list[str]
) -> None:
    """A short fraction is a fraction (``.4`` is 400000 microseconds, not 4); more than six
    digits are cut, never rounded, and warned even when the cut digits are zeros."""
    result = one_stamp(stamp)
    assert instant_text(result) == expected
    assert codes(result) == expect_codes
    if expect_codes:
        assert (result.warnings[0].path, result.warnings[0].raw) == ("f8:3/timestamp", stamp)


# ================================================== survivor: an absent row for a filled field


def test_f8_absent_provenance_rows_name_only_fields_the_converter_leaves_empty() -> None:
    """A table row of kind ``absent`` claims that the file cannot hold the field. The mutation
    check found that nothing compared the rows with the result: a bogus ``absent`` row for a
    field the converter fills (``Turn.text``) survived. For the twin, with and without a
    declaration, every ``absent`` target is ``None`` on every record of its class, and no
    target is listed both as ``absent`` and as mapped (the whole field or a part of it)."""
    rows = list(module().PROVENANCE_TABLE)
    absent = {r.target for r in rows if r.kind == "absent"}
    mapped = {r.target for r in rows if r.kind != "absent"}
    assert absent, "the table has absent rows"
    for target in absent:
        assert target not in mapped, target
        assert not any(m.startswith(target + ".") for m in mapped), target
    for declared in (None, language_declaration()):
        result = run_f8(twin_text(), declared_language=declared)
        assert result.turns, "the twin has turns"
        for target in sorted(absent):
            cls, _, path = target.partition(".")
            field = path.split(".")[0]
            records = result.transcripts if cls == "Transcript" else result.turns
            assert cls in ("Transcript", "Turn"), target
            for record in records:
                if cls == "Transcript" and field == "declared_language":
                    continue
                assert getattr(record, field) is None, target


def test_f8_every_filled_field_has_a_non_absent_provenance_row() -> None:
    """The other direction: each field the converter sets on the twin (not ``None``) has a row
    in the table that is not ``absent``, so the table cannot call a filled field absent or omit
    it. (The entity base fields, privacy marker and the Turn's link to its
    Transcript are the caller's or the framework's, not source fields.)"""
    framework = set(EntityBase.model_fields) | {
        "privacy",
        "transcript_id",
        "contains_bidi_controls",
    }
    rows = list(module().PROVENANCE_TABLE)
    mapped = {
        r.target.split(".")[0] + "." + r.target.split(".")[1]
        for r in rows
        if r.kind != "absent" and "." in r.target
    }
    result = run_f8(twin_text(), declared_language=language_declaration())
    for record in [*result.transcripts, *result.turns]:
        cls = type(record).__name__
        for field in type(record).model_fields:
            if field in framework or getattr(record, field) is None:
                continue
            assert f"{cls}.{field}" in mapped, f"{cls}.{field}"


# ========================================= survivor: the call id is taken verbatim, not trimmed

PADDED_IDS = {
    "leading-spaces": "  abc",
    "trailing-spaces": "abc  ",
    "both-sides": "  abc  ",
    "tab": "\tabc\t",
    "uuid-with-trailing-space": UUID_TEXT + " ",
    "uuid-with-leading-space": " " + UUID_TEXT,
}


@pytest.mark.parametrize("value", list(PADDED_IDS.values()), ids=list(PADDED_IDS))
def test_f8_call_id_is_taken_to_the_end_of_the_line_without_trimming(value: str) -> None:
    """Section 2.2 of the contract: the header value runs to the end of the line verbatim, with
    no trimming. White space around the id stays in the reference and in ``raw``; a UUID with a
    space beside it is therefore not a bare UUID (``call_id_not_uuid``). The mutation check
    found that a stripped value survived the existing tests."""
    result = run_f8(f"call_id={value}\n---\n" + message())
    assert result.transcripts[0].provider_call_ref.value == value
    assert result.external_refs[0].value == value
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("call_id_not_uuid", "f8:1/call_id", value)
    ]


# ================================================== survivors: the line grammar is exact

NOT_EXACT_MESSAGES = {
    "two-spaces-after-bracket": f"[{STAMP}]  user: hello",
    "tab-after-bracket": f"[{STAMP}]\tuser: hello",
    "space-before-colon": f"[{STAMP}] user : hello",
    "space-inside-brackets": f"[ {STAMP}] user: hello",
}


@pytest.mark.parametrize("line", list(NOT_EXACT_MESSAGES.values()), ids=list(NOT_EXACT_MESSAGES))
def test_f8_a_message_line_needs_exactly_one_space_after_the_bracket_and_a_bare_label(
    line: str,
) -> None:
    """Section 2.2: ``[<timestamp>] <speaker>: <text>`` with exactly one space after ``]`` and a
    label without white space. Anything else on line 3 or later is an ``unparsed_line`` (never
    a turn, never repaired). The mutation check found that several spaces after ``]`` were
    accepted without any test noticing."""
    text = HEADER + line + "\n" + message("assistant", "ok")
    result = run_f8(text)
    unparsed = [(w.code, w.path, w.raw) for w in result.warnings if w.code == "unparsed_line"]
    if line == NOT_EXACT_MESSAGES["space-inside-brackets"]:
        # the stamp is any text up to ``]``, so the leading space belongs to the stamp: a turn
        # exists but its stamp is not an instant
        assert unparsed == []
        assert "timestamp_not_parseable" in codes(result)
    else:
        assert unparsed == [("unparsed_line", "f8:3", line)]
        assert [t.text for t in result.turns] == ["ok"]
        assert [t.seq for t in result.turns] == [0]


def test_f8_a_header_shaped_line_after_line_1_is_an_unparsed_line() -> None:
    """The header is line 1 only: a later ``call_id=`` line is ``unparsed_line`` and changes
    neither the reference nor the external references."""
    other = "call_id=ffffffff-ffff-4fff-8fff-ffffffffffff"
    text = HEADER + message("user", "a") + other + "\n" + message("assistant", "b")
    result = run_f8(text)
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [("unparsed_line", "f8:4", other)]
    assert result.transcripts[0].provider_call_ref.value == UUID_TEXT
    assert [r.value for r in result.external_refs] == [UUID_TEXT]
    assert result.external_ref_sources == ("f8:1/call_id",)
    assert [t.text for t in result.turns] == ["a", "b"]


@pytest.mark.parametrize("key", ["Call_Id", "CALL_ID", "call_ID", "call-id", " call_id"])
def test_f8_the_header_key_is_exactly_call_id(key: str) -> None:
    """The header key is the literal ``call_id`` (as the speaker labels are case-sensitive):
    another spelling is not a header, so ``header_not_recognized`` and no call reference."""
    line = f"{key}={UUID_TEXT}"
    result = run_f8(line + "\n---\n" + message())
    assert [(w.code, w.path, w.raw) for w in result.warnings] == [
        ("header_not_recognized", "f8:1", line)
    ]
    assert result.transcripts[0].provider_call_ref is None
    assert result.external_refs == ()
