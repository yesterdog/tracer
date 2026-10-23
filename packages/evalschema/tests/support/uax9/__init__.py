# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Clean-room, test-only implementation of the Unicode Bidirectional Algorithm (UAX #9).

Why it exists: python-bidi 0.6.12 failed the official conformance files (ADR 0017 fallback plan,
task P1-04e). This package is the replacement reference for the display-safety tests. It is test
support: it lives under ``tests/support`` and is excluded from the wheel and the sdist.

Clean-room statement. The implementation was written only from these sources:

* Unicode Standard Annex #9, "Unicode Bidirectional Algorithm", Unicode 16.0.0, revision 50,
  https://www.unicode.org/reports/tr9/tr9-50.html (fetched 2026-10-22);
* the Unicode Character Database files DerivedBidiClass.txt, BidiBrackets.txt and
  BidiMirroring.txt, version 16.0.0 (vendored in ``data/``, see ``data/provenance.json``);
* the format descriptions in the headers of BidiTest.txt and BidiCharacterTest.txt, and those two
  files as test data (``tests/fixtures/unicode``).

No source code of any other bidi implementation (python-bidi, unicode-bidi, FriBidi, ICU, the
UAX #9 reference code or any other) was read, copied, ported or paraphrased. See
``docs/phase1/uax9-provenance.md``.

Scope: rules P1-P3, X1-X10 (embeddings, overrides, isolates), W1-W7, N0-N2 (paired brackets),
I1-I2, L1-L2, and the mirroring part of L4 for ``get_display``. Rule L3 and shaping are out of
scope. Characters removed by rule X9 are not part of the display string.
"""

from __future__ import annotations

from .algorithm import Resolved, resolve_classes
from .ucd import UNICODE_VERSION, Bracket, bidi_class, bracket, mirror

VERSION = f"uax9-clean-room/unicode-{UNICODE_VERSION}"

__all__ = [
    "VERSION",
    "Resolved",
    "get_base_level",
    "get_display",
    "resolve",
    "resolve_classes",
]


def _base_level(base_dir: str | None) -> int | None:
    if base_dir is None:
        return None
    try:
        return {"L": 0, "R": 1}[base_dir]
    except KeyError:
        raise ValueError(f"base_dir must be 'L', 'R' or None, not {base_dir!r}") from None


def resolve(text: str, base_dir: str | None = None) -> Resolved:
    """Resolved levels and visual order of ``text`` shown on one line."""
    classes = [bidi_class(ord(ch)) for ch in text]
    brackets: list[Bracket | None] = [bracket(ord(ch)) for ch in text]
    return resolve_classes(classes, _base_level(base_dir), brackets)


def get_base_level(text: str, base_dir: str | None = None) -> int:
    """Paragraph embedding level (0 or 1) of the first paragraph of ``text``."""
    return resolve(text, base_dir).paragraph_level


def get_display(text: str, base_dir: str | None = None) -> str:
    """Visual string, left to right: reordered by L2, mirrored at odd levels (L4)."""
    result = resolve(text, base_dir)
    out: list[str] = []
    for i in result.order:
        level = result.levels[i]
        ch = text[i]
        out.append(chr(mirror(ord(ch))) if level is not None and level % 2 else ch)
    return "".join(out)
