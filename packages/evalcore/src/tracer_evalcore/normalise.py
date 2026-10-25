# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Comparison normaliser registry and the Marathi normaliser ``mr-Deva/1`` (P3-13; ADR 0025).

A normaliser produces a comparison key for a piece of text. It never produces replacement text:
the stored text and every offset refer to the original. The steps for one token are, in this
order: drop the ignorable characters, fold the Devanagari digits U+0966 to U+096F to ASCII, apply
full Unicode case folding, apply NFC (ADR 0025 Decision 2). A change that can alter, for some
input, a key, a token boundary, an offset or a refusal is a new id and never an edit of an existing
one (ADR 0025 Decision 12); ``mr-Deva/1`` stays in the registry for ever.

Every rule of ``mr-Deva/1`` is ``needs_native_review`` (BL-169): nothing here is an Indic quality
claim.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from tracer_evalcore.errors import InputRefusedError

__all__ = [
    "IMPLEMENTED_RULES",
    "NORMALISER_ID",
    "SUPPORTED_NORMALISERS",
    "LoneSurrogateError",
    "NormaliserError",
    "NormaliserInfo",
    "NormaliserSpec",
    "UnknownNormaliserError",
    "check_text",
    "comparison_key",
    "get_normaliser",
    "normaliser_info",
]

NORMALISER_ID = "mr-Deva/1"
SUPPORTED_NORMALISERS: tuple[str, ...] = (NORMALISER_ID,)
IMPLEMENTED_RULES: frozenset[str] = frozenset(f"R{n:02d}" for n in range(1, 19))

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

# ADR 0025 Decision 7, class I: dropped from the key, never split a token (R04, R05, R06).
_MR_DEVA_1_IGNORABLE = frozenset(
    chr(c)
    for c in (
        0x00AD,
        0x061C,
        0x200C,
        0x200D,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        0x2060,
        *range(0x2066, 0x206A),
        0xFEFF,
    )
)
_MR_DEVA_1_JOINERS = frozenset({"‌", "‍"})

# One translation table does both the drop (None) and the digit fold (R03); only U+0966-U+096F
# are folded, other decimal digits are not (R14).
_MR_DEVA_1_TABLE: dict[int, int | None] = {
    **{ord(c): None for c in _MR_DEVA_1_IGNORABLE},
    **{0x0966 + n: 0x0030 + n for n in range(10)},
}


class NormaliserError(InputRefusedError):
    """A normaliser refused its arguments."""


class UnknownNormaliserError(NormaliserError):
    """The id names no normaliser this package ships. It is never mapped to the nearest one."""


class LoneSurrogateError(NormaliserError):
    """The text holds a lone surrogate (R17). The message holds the index, never the text."""


@dataclass(frozen=True)
class NormaliserInfo:
    """The id and the Unicode data version recorded next to it in every result (Decision 12)."""

    id: str
    unicode_version: str


@dataclass(frozen=True)
class NormaliserSpec:
    """What the tokeniser needs from a normaliser: the character sets and the key function."""

    id: str
    ignorable: frozenset[str]
    joiners: frozenset[str]
    key: Callable[[str], str]


def _mr_deva_1_key(text: str) -> str:
    dropped_and_folded = text.translate(_MR_DEVA_1_TABLE)
    return unicodedata.normalize("NFC", dropped_and_folded.casefold())


_REGISTRY: dict[str, NormaliserSpec] = {
    NORMALISER_ID: NormaliserSpec(
        id=NORMALISER_ID,
        ignorable=_MR_DEVA_1_IGNORABLE,
        joiners=_MR_DEVA_1_JOINERS,
        key=_mr_deva_1_key,
    )
}


def get_normaliser(normaliser: str) -> NormaliserSpec:
    """The registry entry for ``normaliser``; there is no default and no nearest match."""
    if not isinstance(normaliser, str):
        raise TypeError("normaliser must be a str")
    spec = _REGISTRY.get(normaliser)
    if spec is None:
        raise UnknownNormaliserError(
            f"unknown normaliser id (length {len(normaliser)}); supported: "
            f"{', '.join(SUPPORTED_NORMALISERS)}"
        )
    return spec


def normaliser_info(normaliser: str) -> NormaliserInfo:
    spec = get_normaliser(normaliser)
    return NormaliserInfo(id=spec.id, unicode_version=unicodedata.unidata_version)


def check_text(text: str) -> None:
    """Refuse a non-``str`` (TypeError) and a lone surrogate (R17, ``LoneSurrogateError``)."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    found = _LONE_SURROGATE.search(text)
    if found is not None:
        raise LoneSurrogateError(
            f"text holds a lone surrogate at code point index {found.start()}; "
            "replace lone surrogates before comparing (ADR 0017)"
        )


def comparison_key(text: str, normaliser: str) -> str:
    """The comparison key of ``text`` under ``normaliser`` (ADR 0025 Decision 2)."""
    spec = get_normaliser(normaliser)
    check_text(text)
    return spec.key(text)
