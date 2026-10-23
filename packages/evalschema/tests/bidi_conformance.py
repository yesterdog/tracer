# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Harness: compare a UAX #9 reference implementation with the Unicode conformance files.

Test support only (no tests here, nothing imported by the package). The reference is
provided by ``load_uba``: the in-repo clean-room ``support.uax9`` (P1-04e). It exposes
``get_display`` and ``get_base_level`` and returns
the display string, not levels or indices, so the comparison is made on the display string:

* BidiTest.txt gives bidi *classes*. Every class is replaced by a distinct code point of that
  class that is not Bidi_Mirrored and not a bracket (pools below), so the expected display string
  can be built from the expected visual order and compared character for character. Distinct code
  points make a swap of two characters of one class visible; only the classes with fewer
  characters than a line needs (for example ``S``, ``B``, the explicit controls) can repeat a code
  point.
* BidiCharacterTest.txt gives code points. Paired brackets and other Bidi_Mirrored characters
  are shown mirrored by the reference (rule L4, which the file declares out of scope), so the
  expected string applies the reference's own mirror of a character at odd resolved levels.
  Everything else is compared exactly.
* Characters removed by rule X9 (marked ``x`` in the files) are dropped from the reference output
  before comparing; the reference keeps them in its output.
* The paragraph embedding level (field 2 of BidiCharacterTest.txt) is checked through
  ``bidi.get_base_level`` for the auto-direction lines.

A line passes only if every paragraph-direction variant of that line passes.
"""

from __future__ import annotations

import importlib
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "unicode"


def _r(a: int, b: int) -> list[int]:
    return list(range(a, b + 1))


# One distinct, non-mirrored, non-bracket code point per position, per Bidi_Class. Verified
# against DerivedBidiClass-16.0.0.txt when written, and again against the running interpreter's
# unicodedata by test_pool_characters_have_their_class (all are assigned since Unicode 6.0).
CLASS_POOLS: dict[str, list[int]] = {
    "L": _r(0x41, 0x5A) + _r(0x61, 0x7A) + _r(0x3B1, 0x3C9) + _r(0x430, 0x44F),
    "R": _r(0x5D0, 0x5EA) + _r(0x7CA, 0x7EA) + _r(0x800, 0x815) + _r(0x840, 0x858),
    "AL": _r(0x620, 0x63F) + _r(0x641, 0x64A) + _r(0x712, 0x72F) + _r(0x780, 0x7A5),
    "EN": _r(0x30, 0x39) + _r(0x6F0, 0x6F9) + _r(0x2080, 0x2089) + _r(0xFF10, 0xFF19),
    "ES": [0x2B, 0x2D, 0x207A, 0x207B, 0x208A, 0x208B, 0x2212],
    "ET": _r(0x23, 0x25)
    + _r(0xA2, 0xA5)
    + _r(0xB0, 0xB1)
    + _r(0x2030, 0x2034)
    + _r(0x20A0, 0x20B5),
    "AN": [*_r(0x660, 0x669), 0x66B, 0x66C],
    "CS": [0x2C, 0x2E, 0x2F, 0x3A, 0xA0, 0x60C, 0x202F, 0x2044],
    "NSM": _r(0x300, 0x362),
    "BN": [0xAD, 0x200B, 0x200C, 0x200D, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF],
    "B": [0x2029],
    "S": [0x09, 0x0B, 0x1F],
    "WS": [0x20, 0x0C, 0x1680, *_r(0x2000, 0x200A), 0x2028, 0x205F, 0x3000],
    "ON": [
        *[0x21, 0x22, 0x26, 0x27, 0x2A, 0x3B, 0x3D, 0x3F, 0x40, 0x5C, 0x5E, 0x5F, 0x60, 0x7C],
        *[0x7E, 0xA1, 0xA7, 0xA9, 0xAC, 0xAE, 0xB6, 0xB7, 0xBF, 0xD7, 0xF7],
        *_r(0x2190, 0x21FF),
    ],
}
EXPLICIT: dict[str, int] = {
    "LRE": 0x202A,
    "RLE": 0x202B,
    "PDF": 0x202C,
    "LRO": 0x202D,
    "RLO": 0x202E,
    "LRI": 0x2066,
    "RLI": 0x2067,
    "FSI": 0x2068,
    "PDI": 0x2069,
}
REMOVED_BY_X9 = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "BN"})
ISOLATE_CLASSES = frozenset({"LRI", "RLI", "FSI", "PDI"})
OVERRIDE_CLASSES = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF"})
ISOLATE_CODE_POINTS = frozenset(range(0x2066, 0x206A))
OVERRIDE_CODE_POINTS = frozenset(range(0x202A, 0x202F))
MAX_EXAMPLES = 15


@dataclass
class Failure:
    line_no: int
    detail: str


@dataclass
class ConformanceResult:
    file: str
    data_lines: int = 0
    variants: int = 0
    failed_lines: int = 0
    failed_variants: int = 0
    unparsed_lines: int = 0
    isolate_lines: int = 0
    override_lines: int = 0
    failed_isolate_lines: int = 0
    failed_override_lines: int = 0
    reasons: Counter[str] = field(default_factory=Counter)
    examples: list[Failure] = field(default_factory=list)

    @property
    def passed_lines(self) -> int:
        return self.data_lines - self.failed_lines

    def summary(self) -> str:
        top = ", ".join(f"{k}: {v}" for k, v in self.reasons.most_common(6)) or "none"
        sample = "; ".join(f"line {f.line_no}: {f.detail}" for f in self.examples[:5])
        return (
            f"{self.file}: {self.passed_lines}/{self.data_lines} data lines pass "
            f"({self.failed_lines} fail, {self.failed_variants}/{self.variants} paragraph-level "
            f"variants fail, {self.unparsed_lines} unparsed); isolate lines "
            f"{self.isolate_lines - self.failed_isolate_lines}/{self.isolate_lines} pass; "
            f"embedding/override lines "
            f"{self.override_lines - self.failed_override_lines}/{self.override_lines} pass; "
            f"failure kinds [{top}]; first failures [{sample}]"
        )


def load_uba() -> Any:
    """The UBA implementation under test and used by the display tests (the single seam)."""
    return importlib.import_module(".support.uax9", __package__)


def _base_dir(direction: str) -> str | None:
    return {"L": "L", "R": "R", "auto": None}[direction]


def _record(res: ConformanceResult, line_no: int, kind: str, detail: str) -> None:
    res.reasons[kind] += 1
    if len(res.examples) < MAX_EXAMPLES:
        res.examples.append(Failure(line_no, f"{kind}: {detail}"))


def _display(ref: Any, text: str, direction: str) -> str:
    out = ref.get_display(text, base_dir=_base_dir(direction))
    assert isinstance(out, str)
    return out


# --------------------------------------------------------------------------- BidiTest.txt


def iter_biditest(path: Path) -> Iterator[tuple[int, list[str], list[str], list[str], int]]:
    """Yield (line_no, levels, reorder, classes, bitset) for each data line."""
    levels: list[str] = []
    reorder: list[str] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@Levels:"):
            levels = line.split(":", 1)[1].split()
            continue
        if line.startswith("@Reorder:"):
            reorder = line.split(":", 1)[1].split()
            continue
        if line.startswith("@"):
            continue
        body, bitset = line.split(";")
        yield line_no, levels, reorder, body.split(), int(bitset.strip(), 16)


def run_biditest(path: Path, ref: Any | None = None) -> ConformanceResult:
    ref = ref or load_uba()
    res = ConformanceResult(file=path.name)
    # direction bit -> paragraph direction passed to the reference
    directions = ((1, "auto"), (2, "L"), (4, "R"))
    class_of: dict[str, str] = {}
    for cls, pool in CLASS_POOLS.items():
        for cp in pool:
            class_of[chr(cp)] = cls
    for cls, cp in EXPLICIT.items():
        class_of[chr(cp)] = cls
    for line_no, levels, reorder, classes, bitset in iter_biditest(path):
        res.data_lines += 1
        is_iso = any(c in ISOLATE_CLASSES for c in classes)
        is_ovr = any(c in OVERRIDE_CLASSES for c in classes)
        res.isolate_lines += is_iso
        res.override_lines += is_ovr
        try:
            chars = _materialize(classes)
        except KeyError as exc:
            res.unparsed_lines += 1
            res.failed_lines += 1
            _record(res, line_no, "unknown-class", str(exc))
            continue
        text = "".join(chars)
        expected = "".join(chars[int(i)] for i in reorder)
        line_failed = False
        for bit, direction in directions:
            if not bitset & bit:
                continue
            res.variants += 1
            try:
                shown = _display(ref, text, direction)
            except Exception as exc:
                res.failed_variants += 1
                line_failed = True
                _record(res, line_no, "exception", f"{type(exc).__name__} on {classes} {direction}")
                continue
            removed = {
                chars[i] for i, level in enumerate(levels) if level == "x" and i < len(chars)
            }
            actual = "".join(ch for ch in shown if ch not in removed)
            if actual != expected:
                res.failed_variants += 1
                line_failed = True
                _record(
                    res,
                    line_no,
                    "visual-order",
                    f"{' '.join(classes)} dir={direction} expected {_ids(expected, chars)} "
                    f"got {_ids(actual, chars)}",
                )
        if line_failed:
            res.failed_lines += 1
            res.failed_isolate_lines += is_iso
            res.failed_override_lines += is_ovr
    return res


def _materialize(classes: list[str]) -> list[str]:
    """Distinct code points per position, per class (repeats only when a pool is exhausted)."""
    used: Counter[str] = Counter()
    out: list[str] = []
    for cls in classes:
        if cls in EXPLICIT:
            out.append(chr(EXPLICIT[cls]))
            continue
        pool = CLASS_POOLS[cls]
        out.append(chr(pool[used[cls] % len(pool)]))
        used[cls] += 1
    return out


def _ids(text: str, chars: list[str]) -> str:
    return (
        "[" + " ".join(str(chars.index(c)) if c in chars else f"U+{ord(c):04X}" for c in text) + "]"
    )


# ------------------------------------------------------------------- BidiCharacterTest.txt


def iter_bidicharactertest(
    path: Path,
) -> Iterator[tuple[int, list[int], int, int, list[str], list[int]]]:
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f0, f1, f2, f3, f4 = (p.strip() for p in line.split(";"))
        yield (
            line_no,
            [int(x, 16) for x in f0.split()],
            int(f1),
            int(f2),
            f3.split(),
            [int(x) for x in f4.split()],
        )


def mirror_map(ref: Any, code_points: set[int]) -> dict[str, str]:
    """The reference's own L4 mirror of each mirrored character (probed in an RTL context)."""
    import unicodedata

    out: dict[str, str] = {}
    for cp in code_points:
        ch = chr(cp)
        if unicodedata.mirrored(ch):
            shown = ref.get_display("\u05d0" + ch, base_dir="R")
            out[ch] = shown[0]
    return out


def run_bidicharactertest(path: Path, ref: Any | None = None) -> ConformanceResult:
    ref = ref or load_uba()
    res = ConformanceResult(file=path.name)
    cases = list(iter_bidicharactertest(path))
    seen = {cp for _, cps, *_ in cases for cp in cps}
    try:
        mirrors = mirror_map(ref, seen)
    except Exception:
        mirrors = {}
    directions = {0: "L", 1: "R", 2: "auto"}
    for line_no, cps, dir_code, para_level, levels, order in cases:
        res.data_lines += 1
        res.variants += 1
        is_iso = any(cp in ISOLATE_CODE_POINTS for cp in cps)
        is_ovr = any(cp in OVERRIDE_CODE_POINTS for cp in cps)
        res.isolate_lines += is_iso
        res.override_lines += is_ovr
        text = "".join(chr(cp) for cp in cps)
        direction = directions[dir_code]
        problems: list[tuple[str, str]] = []
        try:
            shown = _display(ref, text, direction)
            removed = {chr(cps[i]) for i, level in enumerate(levels) if level == "x"}
            actual = "".join(ch for ch in shown if ch not in removed)
            expected = "".join(
                mirrors.get(chr(cps[i]), chr(cps[i])) if int(levels[i]) % 2 else chr(cps[i])
                for i in order
            )
            if actual != expected:
                problems.append(
                    (
                        "visual-order",
                        f"{' '.join(f'{c:04X}' for c in cps)} dir={direction} expected order "
                        f"{order} got {[hex(ord(c)) for c in actual]}",
                    )
                )
            if direction == "auto":
                got_level = ref.get_base_level(text)
                if got_level != para_level:
                    problems.append(
                        ("paragraph-level", f"expected {para_level} got {got_level} for {text!r}")
                    )
        except Exception as exc:
            problems.append(("exception", f"{type(exc).__name__}: {exc}"))
        if problems:
            res.failed_lines += 1
            res.failed_variants += 1
            res.failed_isolate_lines += is_iso
            res.failed_override_lines += is_ovr
            for kind, detail in problems:
                _record(res, line_no, kind, detail)
    return res


# -------------------------------------------------------------- independent line counting


def count_lines_by_method_b(biditest: Path, bidichartest: Path) -> dict[str, int]:
    """Count data and isolate/override lines with plain regexes (independent of the parser)."""
    iso_re = re.compile(r"\b(?:LRI|RLI|FSI|PDI)\b")
    ovr_re = re.compile(r"\b(?:LRE|RLE|LRO|RLO|PDF)\b")
    counts = Counter[str]()
    for raw in biditest.read_text(encoding="utf-8").splitlines():
        if not raw or raw[0] in "#@":
            continue
        body = raw.split(";", 1)[0]
        counts["biditest_lines"] += 1
        counts["biditest_isolate_lines"] += bool(iso_re.search(body))
        counts["biditest_override_lines"] += bool(ovr_re.search(body))
    cp_iso = re.compile(r"(?:^|\s)206[6-9](?:\s|$)")
    cp_ovr = re.compile(r"(?:^|\s)202[A-E](?:\s|$)")
    for raw in bidichartest.read_text(encoding="utf-8").splitlines():
        if not raw or raw[0] == "#":
            continue
        body = raw.split(";", 1)[0]
        counts["bidichartest_lines"] += 1
        counts["bidichartest_isolate_lines"] += bool(cp_iso.search(body))
        counts["bidichartest_override_lines"] += bool(cp_ovr.search(body))
    return dict(counts)


_RUNNERS: dict[str, Callable[[Path, Any], ConformanceResult]] = {
    "BidiTest.txt": lambda p, r: run_biditest(p, r),
    "BidiCharacterTest.txt": lambda p, r: run_bidicharactertest(p, r),
}
_CACHE: dict[str, ConformanceResult] = {}


def conformance(name: str) -> ConformanceResult:
    """Run (once per process) the reference against one vendored file."""
    if name not in _CACHE:
        ref = load_uba()
        _CACHE[name] = _RUNNERS[name](FIXTURE_DIR / name, ref)
    return _CACHE[name]


def uba_visual(text: str, *, base_dir: str | None = None) -> str:
    """Display string of ``text`` under the UBA reference (the single seam for the display tests).

    Implementation chosen by ``load_uba`` (default: the in-repo clean-room UAX #9).
    """
    ref = load_uba()
    out = ref.get_display(text, base_dir=base_dir)
    assert isinstance(out, str)
    return out
