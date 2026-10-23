# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Text types and safe display of stored text (ADR 0017).

- ``VerbatimText``: free text stored code point for code point (no normalization, no stripping);
  only lone surrogates are rejected, because they cannot be encoded as UTF-8.
- ``HumanLabel``: names and labels that a person reads and compares (ADR 0017 Decision 4).
- ``MachineKey`` and ``MetricKey``: ASCII keys (ADR 0017 Decision 5, P-49).
- ``contains_bidi_controls`` and ``BidiFlaggedText``: the bidi flag, computed when omitted and
  verified when supplied (Decision 2, P-48).
- ``display_safe``, ``MARKER_TABLE`` and ``marker_table_json``: the one rendering function for
  stored text (Decisions 3 and 3a). ``data/bidi_markers.json`` is generated from
  ``marker_table_json()`` by ``scripts/generate_package_data.py``; it is never edited by hand.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, Strict, model_validator
from pydantic_core import PydanticCustomError

from tracer_evalschema._constraints import FullMatch

__all__ = [
    "BIDI_CONTROLS",
    "MARKER_TABLE",
    "BidiFlaggedText",
    "HumanLabel",
    "MachineKey",
    "MetricKey",
    "VerbatimText",
    "check_bidi_flag",
    "contains_bidi_controls",
    "display_safe",
    "marker_table_json",
    "with_computed_bidi_flag",
]


def _char_class(ranges: tuple[tuple[int, int], ...]) -> re.Pattern[str]:
    parts = "".join(f"{re.escape(chr(low))}-{re.escape(chr(high))}" for low, high in ranges)
    return re.compile(f"[{parts}]")


# ------------------------------------------------------------------------------- VerbatimText

_SURROGATES = _char_class(((0xD800, 0xDFFF),))


def _reject_lone_surrogates(value: str) -> str:
    found = _SURROGATES.search(value)
    if found is not None:
        raise PydanticCustomError(
            "lone_surrogate",
            "text contains the lone surrogate {code_point}, which cannot be encoded as UTF-8",
            {"code_point": f"U+{ord(found.group()):04X}"},
        )
    return value


VerbatimText = Annotated[str, Strict(), AfterValidator(_reject_lone_surrogates)]
"""Free text, stored exactly as received (no NFC/NFD, no strip, no escaping)."""


# --------------------------------------------------------------------------------- HumanLabel

ZWNJ = "‌"
ZWJ = "‍"

# Default_Ignorable_Code_Point of Unicode 16.0.0 (DerivedCoreProperties.txt), assigned and
# reserved code points alike, with ZWNJ U+200C and ZWJ U+200D left out: they select conjunct and
# chillu forms in Indic scripts and are the only default-ignorables a label may contain (ADR 0017
# Decisions 4 and 6). The set includes the bidi controls, the variation selectors and the tag
# characters.
DEFAULT_IGNORABLE_EXCEPT_JOINERS: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200B),
    (0x200E, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)

# Everything a label rejects at any position: general category Cc (C0, DEL, C1), the line and
# paragraph separators, lone surrogates, and the default-ignorables above.
_LABEL_FORBIDDEN = _char_class(
    (
        (0x0000, 0x001F),
        (0x007F, 0x009F),
        (0x2028, 0x2029),
        (0xD800, 0xDFFF),
        *DEFAULT_IGNORABLE_EXCEPT_JOINERS,
    )
)


def _check_label(value: str) -> str:
    found = _LABEL_FORBIDDEN.search(value)
    if found is not None:
        raise PydanticCustomError(
            "human_label_character",
            "a label must not contain {code_point} (control, separator, surrogate or "
            "default-ignorable character; only ZWJ and ZWNJ are allowed)",
            {"code_point": f"U+{ord(found.group()):04X}"},
        )
    if value[0].isspace() or value[-1].isspace():
        raise PydanticCustomError(
            "human_label_edge_whitespace",
            "a label must not begin or end with white space (labels are never trimmed)",
        )
    return value


HumanLabel = Annotated[str, Strict(), Field(min_length=1), AfterValidator(_check_label)]
"""A short, single-line label a person reads (names, accents, display names). Never trimmed."""


# ------------------------------------------------------------------------ MachineKey, MetricKey

MACHINE_KEY_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$"
METRIC_KEY_PATTERN = "^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"

MachineKey = Annotated[
    str,
    FullMatch(MACHINE_KEY_PATTERN, "machine_key", "{value} is not a machine key ({pattern})"),
]
"""ASCII key or external identifier: provider and model names, open enum-like values (P-49)."""

MetricKey = Annotated[
    str,
    FullMatch(
        METRIC_KEY_PATTERN, "metric_key", "{value} is not a lowercase snake_case key ({pattern})"
    ),
]
"""Our own metric keys: lowercase snake_case starting with a letter (``wer``, ``p95_latency``)."""


# ---------------------------------------------------------------------------- the bidi flag

BIDI_CONTROLS: tuple[str, ...] = tuple(
    chr(cp) for cp in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
"""The embedding, override and isolate controls U+202A-U+202E and U+2066-U+2069 (P-48)."""

_BIDI_CONTROL = _char_class(((0x202A, 0x202E), (0x2066, 0x2069)))


def contains_bidi_controls(text: str) -> bool:
    """True iff ``text`` holds one of ``BIDI_CONTROLS`` (LRM, RLM and ALM do not count)."""
    return _BIDI_CONTROL.search(text) is not None


def check_bidi_flag(text: str, flag: bool) -> None:
    """Raise a ``ValueError`` (a ``ValidationError`` in a model) if ``flag`` is not the computed
    value for ``text``. A stored flag is never trusted (ADR 0017 Decision 2)."""
    computed = contains_bidi_controls(text)
    if flag is not computed:
        raise ValueError(
            f"contains_bidi_controls is {flag} but the text "
            f"{'contains' if computed else 'does not contain'} a bidi control"
        )


def with_computed_bidi_flag(
    data: Any, *, text_field: str = "text", flag_field: str = "contains_bidi_controls"
) -> Any:
    """Before-validation step: add the computed flag when the input omits it.

    Returns a new mapping and never changes ``data``. Input that is not a mapping, or whose text
    is not a ``str``, is returned unchanged so that field validation reports it.
    """
    if isinstance(data, Mapping) and flag_field not in data:
        text = data.get(text_field)
        if isinstance(text, str):
            return {**data, flag_field: contains_bidi_controls(text)}
    return data


class BidiFlaggedText(BaseModel):
    """Text with its bidi flag: computed when omitted, verified when supplied, always serialized.

    ``Turn`` and ``Segment`` reuse ``with_computed_bidi_flag`` and ``check_bidi_flag``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: VerbatimText
    # The default is never used: the before-validator always supplies the computed value. It only
    # makes the field optional in the JSON Schema, as it is on input.
    contains_bidi_controls: Annotated[bool, Strict()] = False

    @model_validator(mode="before")
    @classmethod
    def _fill_flag(cls, data: Any) -> Any:
        return with_computed_bidi_flag(data)

    @model_validator(mode="after")
    def _verify_flag(self) -> BidiFlaggedText:
        check_bidi_flag(self.text, self.contains_bidi_controls)
        return self


# ------------------------------------------------------------------------------ display_safe

FSI = "⁨"
PDI = "⁩"

MARKER_TABLE: Mapping[str, str] = MappingProxyType(
    {
        "‪": "[LRE]",
        "‫": "[RLE]",
        "‬": "[PDF]",
        "‭": "[LRO]",
        "‮": "[RLO]",
        "⁦": "[LRI]",
        "⁧": "[RLI]",
        "⁨": "[FSI]",
        "⁩": "[PDI]",
    }
)
"""Visible ASCII marker for each bidi control: its Unicode abbreviation in brackets (3a)."""

_MARKER_TRANSLATION = str.maketrans(dict(MARKER_TABLE))


def display_safe(text: str) -> str:
    """Return ``text`` ready to be shown next to other content (ADR 0017 Decision 3).

    The text is wrapped in FSI ... PDI, so it cannot reorder what surrounds it, and every bidi
    control inside it is replaced by its visible marker, so it is both shown and inert (a PDI in
    the text cannot close the outer isolate early). LRM, RLM, ALM, ZWJ and ZWNJ are kept. The
    input is not changed.
    """
    return FSI + text.translate(_MARKER_TRANSLATION) + PDI


def _code_point_label(char: str) -> str:
    return f"U+{ord(char):04X}"


def marker_table_json() -> str:
    """The marker table as JSON text; ``data/bidi_markers.json`` holds exactly this text."""
    document = {
        "wrapper": {"open": _code_point_label(FSI), "close": _code_point_label(PDI)},
        "markers": {_code_point_label(char): marker for char, marker in MARKER_TABLE.items()},
    }
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"
