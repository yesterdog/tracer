# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04d step 2: display_safe() and its marker table (ADR 0017 Decision 3).

Assumed public API (docs/phase1/api-surface.md) in ``tracer_evalschema.text``: ``display_safe``,
``MARKER_TABLE`` (a read-only mapping from each of U+202A-U+202E and U+2066-U+2069 to an ASCII
marker) and ``marker_table_json()``; the same table ships as ``data/bidi_markers.json``.

The visual order is computed by the UBA reference through ``bidi_conformance.uba_visual``, the
single seam, which asks ``bidi_conformance.load_uba()``. Since P1-04e (ADR 0017 Decision 7,
revision 7) the default reference is the in-repo clean-room UAX #9 implementation
(``tests/support/uax9``), which passes both Unicode 16.0.0 conformance files completely;
python-bidi is only selected by setting ``TRACER_UBA_IMPL=python-bidi``, for comparison. These
tests therefore no longer avoid any input family and are no longer provisional.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from . import bidi_conformance as bc
from ._support import DATA_DIR, api

FSI = "\u2068"
PDI = "\u2069"
CONTROLS = [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]


def _text() -> Any:
    return api("text")


def _marker(cp: int) -> str:
    marker = _text().MARKER_TABLE[chr(cp)]
    assert isinstance(marker, str)
    return marker


def test_display_safe_account_number() -> None:
    text = _text()
    stored = "1234\u202e9876"
    out = text.display_safe(stored)
    assert out == FSI + "1234" + _marker(0x202E) + "9876" + PDI
    assert "\u202e" not in out, "the override itself is never emitted"
    visual = bc.uba_visual(out)
    shown = visual.replace(FSI, "").replace(PDI, "")
    assert shown.startswith("1234"), visual
    assert "9876" in visual
    assert "6789" not in visual
    # control: the unprotected string is exactly the attack (it displays 1234 then 6789), so the
    # reference is able to tell a safe string from an unsafe one on this input
    assert "6789" in bc.uba_visual(stored)
    assert "9876" not in bc.uba_visual(stored)
    # the same holds when the account number sits inside surrounding right-to-left content
    context = f"\u05d0\u05d1 {out} \u05d2\u05d3"
    assert "9876" in bc.uba_visual(context)
    assert "6789" not in bc.uba_visual(context)


def _isolate_span(visual: str) -> str:
    """Text between the outermost isolate characters of a display string.

    In a right-to-left context the isolate's FSI is shown to the right of its content and the PDI
    to the left (visual order), so the span is taken between the first and last isolate character
    whichever way round they appear.
    """
    positions = [i for i, ch in enumerate(visual) if ch in (FSI, PDI)]
    assert len(positions) >= 2, visual
    return visual[min(positions) + 1 : max(positions)]


@pytest.mark.parametrize("raw", ["x\u2069\u202ey"], ids=["x<U+2069><U+202E>y"])
def test_display_safe_pdi_breakout(raw: str) -> None:
    text = _text()
    out = text.display_safe(raw)
    inner = FSI + "x" + _marker(0x2069) + _marker(0x202E) + "y" + PDI
    assert out == inner
    assert out.count(PDI) == 1, "only the closing PDI is a real PDI"
    assert out.endswith(PDI)
    assert out.count(FSI) == 1
    assert out.startswith(FSI)
    assert "\u202e" not in out
    content = out[1:-1]
    # the isolate content stays one contiguous block, and the surrounding text keeps its own
    # direction and order, in right-to-left and left-to-right contexts alike
    for before, after in (
        ("\u05d0\u05d1 ", " \u05d2\u05d3"),
        ("\u0627\u0628 ", " \u062c\u062f"),
        ("abc ", " def"),
    ):
        visual = bc.uba_visual(before + out + after)
        assert _isolate_span(visual) == content, (before, visual)
        safe_outside = bc.uba_visual(before + FSI + "x" + PDI + after)
        assert visual.replace(content, "x") == safe_outside, (
            "the context is displayed as for plain x"
        )
    # control: wrapping without neutralizing the PDI lets the content escape the isolate and
    # reorders the text after it ("def" is displayed as "fed")
    naive = FSI + raw + PDI
    escaped = bc.uba_visual("abc " + naive + " def")
    assert "def" not in escaped
    assert _isolate_span(bc.uba_visual("\u05d0\u05d1 " + naive + " \u05d2\u05d3")) != content


def test_display_safe_marks_isolates() -> None:
    text = _text()
    for cp in CONTROLS:
        out = text.display_safe(f"a{chr(cp)}b")
        assert out == FSI + "a" + _marker(cp) + "b" + PDI, f"U+{cp:04X}"
        inner = out[1:-1]
        assert not any(chr(c) in inner for c in CONTROLS), "no raw control survives inside"
    every = "".join(chr(cp) for cp in CONTROLS)
    out = text.display_safe(every)
    assert out == FSI + "".join(_marker(cp) for cp in CONTROLS) + PDI
    assert out[0] == FSI
    assert out[-1] == PDI
    assert not any(chr(c) in out[1:-1] for c in CONTROLS)
    # repeated and adjacent controls are each replaced
    assert text.display_safe("\u2067\u2067") == FSI + _marker(0x2067) * 2 + PDI


def test_display_safe_lrm_rlm_unchanged() -> None:
    text = _text()
    for sample in (
        "a\u200eb\u200fc\u061cd",
        "\u200e",
        "\u200f\u05d0\u05d1\u200f",
        "\u061c\u0627\u0628",
        "x \u200e y",
    ):
        assert text.display_safe(sample) == FSI + sample + PDI
    # ZWJ and ZWNJ are preserved too (ADR 0017 Decision 6)
    for sample in ("\u0915\u094d\u200d\u0937", "\u0915\u094d\u200c\u0937"):
        assert text.display_safe(sample) == FSI + sample + PDI


def test_display_safe_does_not_mutate_input() -> None:
    text = _text()
    table_before = dict(text.MARKER_TABLE)
    for original in ("a\u202eb", "plain", "", "\u2066\u2067\u2068\u2069", "x\u2069\u202ey"):
        keep = str(original)
        out = text.display_safe(original)
        assert original == keep
        assert out is not original
        assert isinstance(out, str)
        assert text.display_safe(original) == out, "deterministic"
    assert dict(text.MARKER_TABLE) == table_before
    with pytest.raises(TypeError):
        text.MARKER_TABLE["\u202e"] = "[x]"
    with pytest.raises(TypeError):
        del text.MARKER_TABLE["\u202e"]


def test_display_safe_wraps_plain_text_in_fsi_pdi() -> None:
    """Proposed name. ADR 0017 Decision 3: the whole text is wrapped in FSI ... PDI."""
    text = _text()
    assert text.display_safe("") == FSI + PDI
    assert text.display_safe("plain") == FSI + "plain" + PDI
    assert text.display_safe("\u05d0\u05d1") == FSI + "\u05d0\u05d1" + PDI


def test_display_safe_markers_ascii_and_inert() -> None:
    text = _text()
    table = text.MARKER_TABLE
    assert {ord(k) for k in table} == set(CONTROLS), "one marker per control, no more"
    markers = list(table.values())
    assert len(set(markers)) == 9, "markers are distinct"
    for cp in CONTROLS:
        marker = table[chr(cp)]
        assert marker, cp
        assert marker.isascii(), (cp, marker)
        assert marker.isprintable(), (cp, marker)
        assert not any(chr(c) in marker for c in CONTROLS)
        assert " " not in marker
        assert "\n" not in marker
    # inert: inserting a marker never changes the visual order of the surrounding text
    pairs = [
        ("\u05d0\u05d1", "\u05d2\u05d3"),  # Hebrew / Hebrew
        ("\u0627\u0628", "\u062c\u062f"),  # Arabic / Arabic
        ("\u0915\u093f", "\u0916\u0941"),  # Devanagari / Devanagari
        ("\u05d0\u05d1", "\u0915\u093f"),  # Hebrew / Devanagari
        ("\u0915\u093f", "\u05d0\u05d1"),  # Devanagari / Hebrew
    ]
    for cp in CONTROLS:
        for left, right in pairs:
            reference = bc.uba_visual(FSI + left + right + PDI)
            with_marker = bc.uba_visual(text.display_safe(left + chr(cp) + right))
            stripped = "".join(ch for ch in with_marker if not ch.isascii())
            assert stripped == reference, (f"U+{cp:04X}", left, right, with_marker, reference)


# ADR 0017 Decision 3a (revision 7): the Unicode abbreviations in brackets.
MARKER_SPELLING = {
    "\u202a": "[LRE]",
    "\u202b": "[RLE]",
    "\u202c": "[PDF]",
    "\u202d": "[LRO]",
    "\u202e": "[RLO]",
    "\u2066": "[LRI]",
    "\u2067": "[RLI]",
    "\u2068": "[FSI]",
    "\u2069": "[PDI]",
}


def test_marker_table_spelling_and_shipped_file() -> None:
    """Proposed name. ADR 0017 Decision 3a (revision 7): the marker spellings are fixed so that a
    renderer in another language matches; the table is the read-only ``MARKER_TABLE`` and the
    shipped ``data/bidi_markers.json`` is a generated artifact (exactly ``wrapper`` and
    ``markers``, no ``_meta``, never edited by hand)."""
    text = _text()
    assert dict(text.MARKER_TABLE) == MARKER_SPELLING
    expected = {
        "wrapper": {"open": "U+2068", "close": "U+2069"},
        "markers": {f"U+{ord(ch):04X}": marker for ch, marker in MARKER_SPELLING.items()},
    }
    assert json.loads(text.marker_table_json()) == expected
    shipped_text = (DATA_DIR / "bidi_markers.json").read_text(encoding="utf-8")
    shipped = json.loads(shipped_text)
    assert shipped == expected
    assert set(shipped) == {"wrapper", "markers"}, "a generated file: no _meta"
    assert "_meta" not in shipped_text
    assert shipped_text.isascii(), "markers and keys are ASCII, so the file is too"
    # display_safe uses the table for every control, in that spelling
    for control, marker in MARKER_SPELLING.items():
        assert text.display_safe(control) == FSI + marker + PDI


def test_marker_table_json_matches_function() -> None:
    text = _text()
    exported = json.loads(text.marker_table_json())
    shipped = json.loads((DATA_DIR / "bidi_markers.json").read_text(encoding="utf-8"))
    assert exported == shipped, "the shipped JSON file is the exported table"
    assert exported["wrapper"] == {"open": "U+2068", "close": "U+2069"}
    entries = exported["markers"]
    assert set(entries) == {f"U+{cp:04X}" for cp in CONTROLS}
    for key, marker in entries.items():
        cp = int(key.removeprefix("U+"), 16)
        assert marker == text.MARKER_TABLE[chr(cp)]
        # the function really uses the exported marker, for every entry
        assert text.display_safe(f"a{chr(cp)}b") == FSI + "a" + marker + "b" + PDI
    assert len(set(entries.values())) == 9
