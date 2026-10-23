# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04e: the clean-room UAX #9 implementation against every line of both Unicode files.

Unlike ``test_bidi_conformance`` (which compares display strings through ``bidi_conformance``),
this compares what the files actually specify: the paragraph level, the resolved level of every
character (``x`` for characters removed by X9) and the visual order. No line is skipped and no
failure is allowed. Paragraph directions follow the file headers: BidiTest.txt bitset 1 = auto,
2 = LTR, 4 = RTL; BidiCharacterTest.txt field 1 = 0 LTR, 1 RTL, 2 auto.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from . import bidi_conformance as bc
from .support import uax9
from .support.uax9 import algorithm

UNICODE_DIR = bc.FIXTURE_DIR
BIDITEST_VARIANTS = ((1, None), (2, 0), (4, 1))
CHAR_DIRECTIONS = {0: "L", 1: "R", 2: None}


def _levels(fields: list[str]) -> list[int | None]:
    return [None if f == "x" else int(f) for f in fields]


def _run_bidicharactertest(path: Path) -> tuple[int, list[str]]:
    lines = 0
    failures: list[str] = []
    for line_no, cps, direction, para, levels, order in bc.iter_bidicharactertest(path):
        lines += 1
        got = uax9.resolve("".join(map(chr, cps)), CHAR_DIRECTIONS[direction])
        if (got.paragraph_level, got.levels, got.order) != (para, _levels(levels), order):
            failures.append(f"line {line_no}: {cps} dir {direction}: {got}")
    return lines, failures


def _run_biditest(path: Path) -> tuple[int, int, list[str]]:
    lines = variants = 0
    failures: list[str] = []
    for line_no, levels, reorder, classes, bitset in bc.iter_biditest(path):
        lines += 1
        for bit, base in BIDITEST_VARIANTS:
            if not bitset & bit:
                continue
            variants += 1
            got = uax9.resolve_classes(classes, base)
            if (got.levels, got.order) != (_levels(levels), [int(i) for i in reorder]):
                failures.append(f"line {line_no}: {classes} base {base}: {got}")
    return lines, variants, failures


def test_uax9_passes_every_line_of_bidicharactertest() -> None:
    lines, failures = _run_bidicharactertest(UNICODE_DIR / "BidiCharacterTest.txt")
    assert lines == 91_707
    assert not failures, f"{len(failures)} failing lines, first: {failures[:3]}"


def test_uax9_passes_every_line_of_biditest_in_every_paragraph_direction() -> None:
    lines, variants, failures = _run_biditest(UNICODE_DIR / "BidiTest.txt")
    assert lines == 490_846
    assert variants == 770_241
    assert not failures, f"{len(failures)} failing variants, first: {failures[:3]}"


def test_uax9_conformance_run_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative controls: a broken rule is detected by each file."""
    with monkeypatch.context() as patch:
        patch.setattr(algorithm, "_L1_TRAILING", frozenset({"WS"}))  # L1 without isolates
        _, _, class_failures = _run_biditest(UNICODE_DIR / "BidiTest.txt")
        assert class_failures
    with monkeypatch.context() as patch:
        patch.setattr(algorithm, "_STRONG_FOR_BRACKETS", {})  # N0 finds no strong types
        _, failures = _run_bidicharactertest(UNICODE_DIR / "BidiCharacterTest.txt")
        assert failures


def test_uax9_full_conformance_run_time_is_recorded() -> None:
    """Informational: the full direct run of both files finishes in a reasonable time."""
    start = time.perf_counter()
    _run_bidicharactertest(UNICODE_DIR / "BidiCharacterTest.txt")
    _run_biditest(UNICODE_DIR / "BidiTest.txt")
    elapsed = time.perf_counter() - start
    assert elapsed < 300, elapsed


def test_uba_seam_selects_clean_room_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACER_UBA_IMPL", raising=False)
    assert bc.load_uba() is uax9
    assert bc.uba_visual("1234\u202e9876") == "12346789"


def test_uba_seam_has_no_alternative_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    # the TRACER_UBA_IMPL=python-bidi option was removed (owner approval, 2026-10-22)
    monkeypatch.setenv("TRACER_UBA_IMPL", "python-bidi")
    assert bc.load_uba() is uax9
