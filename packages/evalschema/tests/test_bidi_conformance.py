# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04d step 1: the clean-room UBA reference against the Unicode conformance files.

ADR 0017 Decision 7: every line of BidiTest.txt and BidiCharacterTest.txt, isolates U+2066-U+2069
and embeddings/overrides U+202A-U+202E included, no allowlist of failures. The files are vendored
under tests/fixtures/unicode/ with provenance.json. No extra dependency group is needed.

A failure of the first two tests is a defect of the reference, not of the harness (the harness is
checked by the negative control below); it triggers task P1-04e.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

from . import bidi_conformance as bc
from ._support import FIXTURES, load_json

UNICODE_DIR = FIXTURES / "unicode"


def _provenance() -> dict[str, Any]:
    data = load_json(UNICODE_DIR / "provenance.json")
    assert isinstance(data, dict)
    return data


def _file_entry(name: str) -> dict[str, Any]:
    return next(f for f in _provenance()["files"] if f["name"] == name)


def test_bidi_reference_passes_biditest() -> None:
    result = bc.conformance("BidiTest.txt")
    assert result.data_lines > 0
    assert result.failed_lines == 0, result.summary()


def test_bidi_reference_passes_bidicharactertest() -> None:
    result = bc.conformance("BidiCharacterTest.txt")
    assert result.data_lines > 0
    assert result.failed_lines == 0, result.summary()


def test_bidi_reference_conformance_covers_isolates_and_overrides() -> None:
    """The run executed every data line, in particular every line that uses an isolate
    (LRI RLI FSI PDI, U+2066-U+2069) or an embedding/override (LRE RLE LRO RLO PDF,
    U+202A-U+202E). Independent of pass or fail: counts come from a second, regex-based reader."""
    expected = bc.count_lines_by_method_b(
        UNICODE_DIR / "BidiTest.txt", UNICODE_DIR / "BidiCharacterTest.txt"
    )
    first = bc.conformance("BidiTest.txt")
    second = bc.conformance("BidiCharacterTest.txt")
    assert first.unparsed_lines == 0
    assert second.unparsed_lines == 0
    assert (
        first.data_lines == expected["biditest_lines"] == _file_entry("BidiTest.txt")["data_lines"]
    )
    assert second.data_lines == expected["bidichartest_lines"]
    assert second.data_lines == _file_entry("BidiCharacterTest.txt")["data_lines"]
    assert first.isolate_lines == expected["biditest_isolate_lines"] > 100_000
    assert first.override_lines == expected["biditest_override_lines"] > 100_000
    assert second.isolate_lines == expected["bidichartest_isolate_lines"] > 0
    assert second.override_lines == expected["bidichartest_override_lines"] > 0
    # every variant (paragraph direction) of every line was run
    assert first.variants >= first.data_lines
    assert second.variants == second.data_lines


def test_unicode_conformance_files_match_provenance() -> None:
    """Proposed name. The vendored files are the ones provenance.json describes."""
    provenance = _provenance()
    assert {f["name"] for f in provenance["files"]} == {"BidiTest.txt", "BidiCharacterTest.txt"}
    for entry in provenance["files"]:
        path = UNICODE_DIR / entry["name"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], entry["name"]
        assert len(raw) == entry["bytes"]
        first_line = raw.decode("utf-8").splitlines()[0]
        assert first_line == entry["header_line"] == f"# {path.stem}-{entry['unicode_version']}.txt"
        assert entry["url"] == (
            f"https://www.unicode.org/Public/{entry['unicode_version']}/ucd/{entry['name']}"
        )
        assert entry["retrieved_at"] == "2026-10-22"
        assert entry["unicode_18_0_0_file"]["data_and_directive_lines_identical_to_16_0_0"] is True
    licence = provenance["license"]
    text = (UNICODE_DIR / licence["file"]).read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == licence["sha256"]
    assert licence["spdx"] == "Unicode-3.0"
    assert "UNICODE LICENSE V3" in text
    assert licence["url"].startswith("https://www.unicode.org/")


def test_reference_unicode_version_matches_vendored_files() -> None:
    """ADR 0017: the conformance files are the Unicode version the reference implements,
    recorded in the clean-room implementation's vendored data provenance."""
    provenance = _provenance()
    versions = {f["unicode_version"] for f in provenance["files"]}
    data = load_json(UNICODE_DIR.parent.parent / "support" / "uax9" / "data" / "provenance.json")
    assert isinstance(data, dict)
    assert versions == {f["unicode_version"] for f in data["files"]}
    (version,) = versions
    implemented = bc.load_uba().VERSION
    assert implemented == "uax9-clean-room/unicode-" + version
    assert data["specification"]["version"] == "Unicode " + version


def test_harness_detects_a_broken_reference() -> None:
    """Proposed name. Negative control: a reference that does no reordering must fail massively
    on both files, so a pass of the real reference is not a vacuous pass."""

    class NoReordering:
        VERSION = "0"

        @staticmethod
        def get_display(text: str, base_dir: str | None = None) -> str:
            return text

        @staticmethod
        def get_base_level(text: str) -> int:
            return 0

    char = bc.run_bidicharactertest(UNICODE_DIR / "BidiCharacterTest.txt", NoReordering)
    assert char.failed_lines > 40_000, char.summary()
    assert char.reasons["visual-order"] > 0
    assert char.reasons["paragraph-level"] > 0
    cls = bc.run_biditest(UNICODE_DIR / "BidiTest.txt", NoReordering)
    assert cls.failed_lines > 200_000, cls.summary()
    assert cls.failed_isolate_lines > 0
    assert cls.failed_override_lines > 0

    class Crashing:
        @staticmethod
        def get_display(text: str, base_dir: str | None = None) -> str:
            raise RuntimeError("boom")

        @staticmethod
        def get_base_level(text: str) -> int:
            raise RuntimeError("boom")

    crashed = bc.run_bidicharactertest(UNICODE_DIR / "BidiCharacterTest.txt", Crashing)
    assert crashed.failed_lines == crashed.data_lines, "a crash counts as a failure"


def test_pool_characters_have_their_class() -> None:
    """Proposed name. The per-class code point pools of the harness really have that Bidi_Class
    on this interpreter (all assigned since Unicode 6.0) and are neither mirrored nor brackets."""
    for cls, pool in bc.CLASS_POOLS.items():
        assert pool, cls
        for cp in pool:
            ch = chr(cp)
            assert unicodedata.bidirectional(ch) == cls, f"U+{cp:04X} is not {cls}"
            assert not unicodedata.mirrored(ch), f"U+{cp:04X} is mirrored"
        assert len(set(pool)) == len(pool)
    for cls, cp in bc.EXPLICIT.items():
        assert unicodedata.bidirectional(chr(cp)) == cls
    assert len(bc.CLASS_POOLS) == 14, "14 non-explicit Bidi_Class values"
    assert len(bc.EXPLICIT) == 9, "9 explicit formatting values, 23 in total"
