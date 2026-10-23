# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04e: the vendored Unicode data of the clean-room UAX #9 implementation."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .support.uax9 import compat, ucd

DATA = ucd.DATA_DIR


def _provenance() -> dict[str, Any]:
    data = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_uax9_data_files_match_provenance() -> None:
    provenance = _provenance()
    assert {f["name"] for f in provenance["files"]} == {
        "DerivedBidiClass.txt",
        "BidiBrackets.txt",
        "BidiMirroring.txt",
    }
    for entry in provenance["files"]:
        raw = (DATA / entry["name"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], entry["name"]
        assert len(raw) == entry["bytes"]
        first = raw.decode("utf-8").splitlines()[0]
        assert first == entry["header_line"] == f"# {Path(entry['name']).stem}-16.0.0.txt"
        assert entry["unicode_version"] == ucd.UNICODE_VERSION == "16.0.0"
        assert entry["url"].startswith("https://www.unicode.org/Public/16.0.0/ucd/")
    licence = provenance["license"]
    text = (DATA / licence["file"]).read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == licence["sha256"]
    assert licence["spdx"] == "Unicode-3.0"
    assert "UNICODE LICENSE V3" in text


def test_uax9_data_matches_the_version_of_the_conformance_files() -> None:
    fixtures = json.loads(
        (Path(__file__).parent / "fixtures" / "unicode" / "provenance.json").read_text("utf-8")
    )
    assert {f["unicode_version"] for f in fixtures["files"]} == {ucd.UNICODE_VERSION}
    derived = next(f for f in _provenance()["files"] if f["name"] == "DerivedBidiClass.txt")
    assert derived["sha256"] == fixtures["class_pool_verification"]["sha256"]


def test_uax9_bidi_class_values() -> None:
    expected = {
        0x41: "L",
        0x5D0: "R",
        0x627: "AL",
        0x30: "EN",
        0x2B: "ES",
        0x24: "ET",
        0x660: "AN",
        0x2C: "CS",
        0x300: "NSM",
        0xAD: "BN",
        0x2029: "B",
        0x09: "S",
        0x20: "WS",
        0x21: "ON",
        0x202A: "LRE",
        0x202D: "LRO",
        0x202B: "RLE",
        0x202E: "RLO",
        0x202C: "PDF",
        0x2066: "LRI",
        0x2067: "RLI",
        0x2068: "FSI",
        0x2069: "PDI",
        0x200E: "L",
        0x200F: "R",
        0x61C: "AL",
        0x915: "L",  # Devanagari
    }
    for cp, cls in expected.items():
        assert ucd.bidi_class(cp) == cls, hex(cp)


def test_uax9_unassigned_code_points_use_the_block_defaults() -> None:
    assert ucd.bidi_class(0x5FF) == "R", "Hebrew block, unassigned"
    assert ucd.bidi_class(0x7BF) == "AL", "Thaana/Arabic range, unassigned"
    assert ucd.bidi_class(0x20CF) == "ET", "currency symbols block, unassigned"
    assert ucd.bidi_class(0x10FFFF) == "BN", "noncharacter"
    assert ucd.bidi_class(0xFFFE) == "BN"
    assert ucd.bidi_class(0xE0001) == "BN", "language tag"
    assert ucd.bidi_class(0x0378) == "L", "unassigned elsewhere: L"
    assert ucd.bidi_class(0x1EFFF) == "R"


def test_uax9_every_code_point_has_a_class() -> None:
    assert len(ucd._class_table()) == 0x110000
    assert {ucd.bidi_class(cp) for cp in range(0x110000)} == set(ucd.BIDI_CLASSES)


# Bidi_Class changes between Unicode 15.1 (Python 3.13) and 16.0 (this data): found by this test
# and confirmed against the 16.0.0 DerivedBidiClass.txt lines of these code points.
CHANGED_IN_16_0 = frozenset({0x1171E, 0x1D6C1, 0x1D6FB, 0x1D735, 0x1D76F, 0x1D7A9})


# Bidi_Class changes of code points assigned in 16.0.0 when the interpreter ships a NEWER Unicode.
# Each entry needs a Unicode release-note / DerivedBidiClass diff justification. Python 3.15
# (Unicode 17.0.0, 3.15.0rc2) shows no such change: every difference there is a code point that is
# unassigned (Cn) in the 16.0.0 data, so this set is empty and must stay empty without evidence.
CHANGED_IN_NEWER_UNICODE: frozenset[int] = frozenset()


def _interpreter_differences() -> compat.Differences:
    return compat.differences(
        ucd.bidi_class,
        lambda cp: unicodedata.bidirectional(chr(cp)),
        lambda cp: unicodedata.category(chr(cp)),
    )


def _verdict(version: str, found: compat.Differences) -> list[int]:
    return compat.verdict(
        version,
        found,
        changed_in_16_0=CHANGED_IN_16_0,
        changed_in_newer=CHANGED_IN_NEWER_UNICODE,
    )


def test_uax9_classes_agree_with_the_interpreters_unicodedata() -> None:
    """Characters assigned both here (16.0.0) and in the running Python keep the same class.

    Equal Unicode version (Python 3.14): no difference at all. Older Python: only the code points
    whose class changed in 16.0. Newer Python (3.15 ships 17.0.0): code points unassigned in the
    16.0.0 data are not comparable; every code point assigned in 16.0.0 must still agree unless it
    is in the reviewed CHANGED_IN_NEWER_UNICODE list.
    """
    found = _interpreter_differences()
    bad = _verdict(unicodedata.unidata_version, found)
    assert not bad, [hex(cp) for cp in bad[:10]]
    if compat.version_tuple(unicodedata.unidata_version) > compat.version_tuple(
        ucd.UNICODE_VERSION
    ):
        assert found.changed == CHANGED_IN_NEWER_UNICODE, "an unreviewed class change was allowed"


def test_uax9_assigned_set_comes_from_the_vendored_data() -> None:
    assigned = compat.assigned_in_data()
    assert {0x41, 0x5D0, 0x627, 0x1171E} <= assigned
    assert 0x10ED0 not in assigned, "assigned in Unicode 17.0.0, Cn in 16.0.0"
    assert 0x1ACF not in assigned
    assert 0xFFFE not in assigned, "noncharacter"
    assert 0x378 not in assigned, "unassigned"
    assert 0xD800 not in assigned


def test_uax9_every_data_line_carries_a_general_category() -> None:
    """The assigned set is parsed from comments, so no data line may be silently skipped."""
    lines = [
        ln
        for ln in (DATA / "DerivedBidiClass.txt").read_text(encoding="utf-8").splitlines()
        if ln and not ln.startswith("#")
    ]
    assert lines
    assert all(compat.LINE.match(ln) for ln in lines)


def test_uax9_assigned_set_equals_the_interpreters_when_versions_are_equal() -> None:
    if unicodedata.unidata_version != ucd.UNICODE_VERSION:
        return
    expected = {
        cp
        for cp in range(0x110000)
        if not 0xD800 <= cp <= 0xDFFF and unicodedata.category(chr(cp)) != "Cn"
    }
    assert compat.assigned_in_data() - set(range(0xD800, 0xE000)) == expected


def test_uax9_newer_interpreter_branch_rejects_a_real_mismatch() -> None:
    """Inject a wrong class for a code point assigned in 16.0.0 and pretend Unicode 17.0.0."""

    def mutated(cp: int) -> str:
        return "R" if cp == 0x41 else ucd.bidi_class(cp)

    found = compat.differences(
        mutated,
        ucd.bidi_class,
        lambda cp: "Lu" if cp == 0x41 else "Lo",
    )
    assert found.changed == frozenset({0x41})
    assert found.newly_assigned == frozenset()
    assert _verdict("17.0.0", found) == [0x41]
    assert _verdict("16.0.0", found) == [0x41]
    assert _verdict("99.0.0", found) == [0x41]


def test_uax9_newer_interpreter_branch_tolerates_only_newly_assigned() -> None:
    def mutated(cp: int) -> str:
        return "L" if cp == 0x10ED0 else ucd.bidi_class(cp)

    found = compat.differences(
        mutated, lambda cp: ucd.bidi_class(cp) if cp != 0x10ED0 else "ON", lambda cp: "Lo"
    )
    assert found.newly_assigned == frozenset({0x10ED0})
    assert found.changed == frozenset()
    assert _verdict("17.0.0", found) == []
    assert _verdict("16.0.0", found) == [0x10ED0], "equal version stays at full strength"
    assert _verdict("15.1.0", found) == [0x10ED0], "older interpreter has no such allowance"


def test_uax9_older_interpreter_branch_rejects_an_unlisted_change() -> None:
    found = compat.Differences(frozenset({0x41, 0x1171E}), frozenset())
    assert _verdict("15.1.0", found) == [0x41]
    assert _verdict("16.0.0", found) == [0x41, 0x1171E]


def test_uax9_reviewed_newer_allowlist_is_honoured_only_for_assigned_code_points() -> None:
    found = compat.Differences(frozenset({0x41}), frozenset({0x10ED0}))
    ok = compat.verdict(
        "17.0.0", found, changed_in_16_0=frozenset(), changed_in_newer=frozenset({0x41})
    )
    assert ok == []
    assert _verdict("17.0.0", found) == [0x41]


def test_uax9_bidi_brackets() -> None:
    assert ucd.bracket(ord("(")) == ucd.Bracket(True, ord(")"))
    assert ucd.bracket(ord(")")) == ucd.Bracket(False, ord(")"))
    assert ucd.bracket(ord("[")) == ucd.Bracket(True, ord("]"))
    assert ucd.bracket(ord("a")) is None
    assert ucd.bracket(ord("<")) is None, "not a paired bracket, only mirrored"
    # canonical equivalents U+2329/U+232A = U+3008/U+3009
    assert ucd.bracket(0x2329) == ucd.Bracket(True, 0x3009)
    assert ucd.bracket(0x3008) == ucd.Bracket(True, 0x3009)
    assert ucd.bracket(0x232A) == ucd.Bracket(False, 0x3009)
    assert ucd.bracket(0x3009) == ucd.Bracket(False, 0x3009)


def test_uax9_every_bracket_line_is_parsed() -> None:
    lines = [
        ln
        for ln in (DATA / "BidiBrackets.txt").read_text(encoding="utf-8").splitlines()
        if ln and not ln.startswith("#")
    ]
    assert len(lines) == len(ucd._bracket_table())
    for ln in lines:
        assert re.fullmatch(r"[0-9A-F]{4,6}; [0-9A-F]{4,6}; [oc] # .*", ln), ln


def test_uax9_mirroring() -> None:
    assert ucd.mirror(ord("(")) == ord(")")
    assert ucd.mirror(ord("<")) == ord(">")
    assert ucd.mirror(ord("a")) == ord("a")
    assert ucd.mirror(0xBB) == 0xAB


def test_uax9_mirroring_is_involutive_where_defined() -> None:
    table = ucd._mirror_table()
    assert len(table) > 300
    for a, b in table.items():
        assert table.get(b) == a, hex(a)
