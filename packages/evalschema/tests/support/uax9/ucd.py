# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Unicode 16.0.0 character data for the UAX #9 reference: Bidi_Class, brackets, mirroring.

Parsed from the vendored Unicode Character Database files in ``data/`` (Unicode License V3, see
``data/provenance.json``). Only the documented file formats are used.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import NamedTuple

UNICODE_VERSION = "16.0.0"
DATA_DIR = Path(__file__).resolve().parent / "data"

BIDI_CLASSES = (
    "L",
    "R",
    "AL",
    "EN",
    "ES",
    "ET",
    "AN",
    "CS",
    "NSM",
    "BN",
    "B",
    "S",
    "WS",
    "ON",
    "LRE",
    "LRO",
    "RLE",
    "RLO",
    "PDF",
    "LRI",
    "RLI",
    "FSI",
    "PDI",
)
_LONG_NAMES = {
    "Left_To_Right": "L",
    "Right_To_Left": "R",
    "Arabic_Letter": "AL",
    "European_Number": "EN",
    "European_Separator": "ES",
    "European_Terminator": "ET",
    "Arabic_Number": "AN",
    "Common_Separator": "CS",
    "Nonspacing_Mark": "NSM",
    "Boundary_Neutral": "BN",
    "Paragraph_Separator": "B",
    "Segment_Separator": "S",
    "White_Space": "WS",
    "Other_Neutral": "ON",
    "Left_To_Right_Embedding": "LRE",
    "Left_To_Right_Override": "LRO",
    "Right_To_Left_Embedding": "RLE",
    "Right_To_Left_Override": "RLO",
    "Pop_Directional_Format": "PDF",
    "Left_To_Right_Isolate": "LRI",
    "Right_To_Left_Isolate": "RLI",
    "First_Strong_Isolate": "FSI",
    "Pop_Directional_Isolate": "PDI",
}
_CLASS_INDEX = {name: i for i, name in enumerate(BIDI_CLASSES)}
_MAX_CP = 0x10FFFF


class Bracket(NamedTuple):
    """Bidi_Paired_Bracket_Type ``is_open`` and the matching key (canonical equivalence applied)."""

    is_open: bool
    key: int


def _data_lines(name: str) -> list[str]:
    return (DATA_DIR / name).read_text(encoding="utf-8").splitlines()


def _range(token: str) -> tuple[int, int]:
    lo, _, hi = token.strip().partition("..")
    return int(lo, 16), int(hi or lo, 16)


@cache
def _class_table() -> bytes:
    table = bytearray([_CLASS_INDEX["L"]]) * (_MAX_CP + 1)
    painted: list[tuple[int, int, int]] = []
    for line in _data_lines("DerivedBidiClass.txt"):
        if line.startswith("# @missing:"):
            span, _, name = line.removeprefix("# @missing:").partition(";")
            lo, hi = _range(span)
            painted.append((lo, hi, _CLASS_INDEX[_LONG_NAMES[name.strip()]]))
    for line in _data_lines("DerivedBidiClass.txt"):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        span, _, name = body.partition(";")
        lo, hi = _range(span)
        painted.append((lo, hi, _CLASS_INDEX[name.strip()]))
    # @missing lines come first, so explicit data lines overwrite them
    for lo, hi, idx in painted:
        table[lo : hi + 1] = bytes([idx]) * (hi - lo + 1)
    return bytes(table)


def bidi_class(cp: int) -> str:
    return BIDI_CLASSES[_class_table()[cp]]


# U+2329/U+232A are canonically equivalent to U+3008/U+3009 (UAX #9 BD16).
_CANONICAL_BRACKET = {0x2329: 0x3008, 0x232A: 0x3009}


@cache
def _bracket_table() -> dict[int, Bracket]:
    out: dict[int, Bracket] = {}
    for line in _data_lines("BidiBrackets.txt"):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        cp_s, paired_s, kind = (f.strip() for f in body.split(";"))
        cp, paired = int(cp_s, 16), int(paired_s, 16)
        if kind == "o":
            out[cp] = Bracket(True, _CANONICAL_BRACKET.get(paired, paired))
        elif kind == "c":
            out[cp] = Bracket(False, _CANONICAL_BRACKET.get(cp, cp))
    return out


def bracket(cp: int) -> Bracket | None:
    return _bracket_table().get(cp)


@cache
def _mirror_table() -> dict[int, int]:
    out: dict[int, int] = {}
    for line in _data_lines("BidiMirroring.txt"):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        a, b = (int(f.strip(), 16) for f in body.split(";"))
        out[a] = b
    return out


def mirror(cp: int) -> int:
    """Bidi_Mirroring_Glyph of ``cp``, or ``cp`` itself if it has none."""
    return _mirror_table().get(cp, cp)
