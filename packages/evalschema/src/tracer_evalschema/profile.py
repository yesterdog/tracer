# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""TestProfile, its reference, profile placeholders and the literal-value lint (ADR 0006).

- ``TestProfile`` (``tpf``, versioned): the identity a simulated caller presents. Every data
  field is personal data, carries ``x-pii: true`` in the JSON Schema, and is never copied into a
  snapshot (ADR 0006 Decisions 1 and 2). The model hides its input in validation errors
  (ADR 0007 Decision 8 (b)).
- ``TestProfileRef{id, version, content_digest?}``: what a snapshot and an evaluator hold.
- Placeholders (ADR 0006 Decisions 9 and 9a): free text refers to profile values only as
  ``{{profile.<field>}}``. ``profile_placeholders(text)`` returns the fields a text names and
  rejects unknown fields and malformed placeholders; ``profile_placeholder_fields()`` is the
  set of names.
- ``find_literal_profile_values(model, profile)``: the lint seam. It reports where a literal
  profile value stands in a model's JSON dump, by path and field, never by value.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from tracer_evalschema._constraints import echoed
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema.base import VersionedEntity, VersionedRef, json_string_leaves
from tracer_evalschema.ids import TestProfileId
from tracer_evalschema.text import VerbatimText

__all__ = [
    "LiteralProfileValue",
    "ProfileValue",
    "TestProfile",
    "TestProfileRef",
    "find_literal_profile_values",
    "profile_placeholder_fields",
    "profile_placeholders",
]

ProfileValue = Annotated[VerbatimText, Field(min_length=1, json_schema_extra={"x-pii": True})]
"""A profile data value: non-empty text, stored verbatim, marked as personal data."""


class TestProfile(HidingModel, VersionedEntity):
    """The identity a simulated caller presents (prefix ``tpf``). Every data field is PII."""

    model_config = ConfigDict(hide_input_in_errors=True)

    id_prefix: ClassVar[str] = "tpf"

    name: ProfileValue
    dob: ProfileValue
    account_number: ProfileValue


class TestProfileRef(VersionedRef):
    """``{id, version, content_digest?}`` of a ``TestProfile``; it holds no profile value."""

    id: TestProfileId


@functools.cache
def profile_placeholder_fields() -> frozenset[str]:
    """The ``TestProfile`` data fields, the names usable in ``{{profile.<field>}}``."""
    return frozenset(set(TestProfile.model_fields) - set(VersionedEntity.model_fields))


# ------------------------------------------------------------------------------ placeholders

_WELL_FORMED = r"\{\{profile\.([a-z][a-z0-9_]*)\}\}"
# "{{", optional white space, "profile" in any letter case, optional white space, ".": anything
# that starts like a placeholder. ASCII letters only; no normalization is applied.
_LOOKALIKE = r"\{\{\s*[Pp][Rr][Oo][Ff][Ii][Ll][Ee]\s*\."
_PLACEHOLDER_SCAN = re.compile(f"{_WELL_FORMED}|{_LOOKALIKE}")


def profile_placeholders(text: str) -> list[str]:
    """The profile fields named by the placeholders of ``text``, in order.

    Scans left to right without overlap. Raises ``ValueError`` for a placeholder whose field is
    not a profile field and for text that starts like a placeholder but is not exactly
    ``{{profile.<field>}}`` (white space inside, another letter case, an empty or nested field, a
    missing brace). Other ``{{...}}`` text is ordinary. The message names the unknown field as
    typed, except inside a model that hides its input, where it names the rule and the known
    fields only (owner decision on the placeholder message, ADR 0007 Decision 8 (c)): the typed
    name is part of the user's text.
    """
    fields: list[str] = []
    known = profile_placeholder_fields()
    for match in _PLACEHOLDER_SCAN.finditer(text):
        field = match.group(1)
        if field is None:
            raise ValueError(
                "malformed profile placeholder: write {{profile.<field>}} exactly, with a "
                "lowercase field name and no white space, for example {{profile.name}}"
            )
        if field not in known:
            named = "" if values_hidden() else f" {echoed(field)}"
            raise ValueError(
                f"unknown profile placeholder field{named}: a placeholder names a TestProfile "
                f"field ({', '.join(sorted(known))})"
            )
        fields.append(field)
    return fields


def check_profile_text(text: str) -> str:
    """After-validator for free text that may hold profile placeholders (returned unchanged)."""
    profile_placeholders(text)
    return text


# -------------------------------------------------------------------------------------- lint


class LiteralProfileValue(BaseModel):
    """A literal profile value found in free text: where (an RFC 6901 pointer from the document
    root) and which field. The value itself is never part of a hit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: VerbatimText
    field: VerbatimText


_WHITE_SPACE_RUN = re.compile(r"\s+")


def _fold(text: str) -> str:
    """NFC, case fold, NFC, decimal digits to ASCII, white-space runs to one space."""
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())
    folded = "".join(
        str(unicodedata.decimal(char)) if unicodedata.category(char) == "Nd" else char
        for char in folded
    )
    return _WHITE_SPACE_RUN.sub(" ", folded)


def _is_word_character(char: str) -> bool:
    return unicodedata.category(char)[0] in "LMN"


def _bounded_occurrence(text: str, value: str) -> bool:
    """Whether ``value`` occurs in ``text`` with token bounds (ADR 0006 Decision 9a)."""
    check_left = _is_word_character(value[0])
    check_right = _is_word_character(value[-1])
    start = text.find(value)
    while start >= 0:
        end = start + len(value)
        left_ok = not check_left or start == 0 or not _is_word_character(text[start - 1])
        right_ok = not check_right or end == len(text) or not _is_word_character(text[end])
        if left_ok and right_ok:
            return True
        start = text.find(value, start + 1)
    return False


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-be", "surrogatepass")


def find_literal_profile_values(
    model: BaseModel, profile: TestProfile
) -> list[LiteralProfileValue]:
    """Where literal values of ``profile`` stand in the JSON dump of ``model``.

    Every string leaf is compared with every non-empty profile data value after folding both
    (``_fold``); a hit needs token bounds where the value starts or ends with a letter, mark or
    digit. One hit per (path, field), sorted by path and then field in UTF-16 code-unit order.
    Keys, numbers, booleans and nulls are not compared. Nothing is changed.
    """
    values: dict[str, str] = {}
    for field in sorted(profile_placeholder_fields()):
        value: Any = getattr(profile, field, None)
        if isinstance(value, str) and value:
            values[field] = _fold(value)
    hits: set[tuple[str, str]] = set()
    document = model.model_dump(mode="json", by_alias=True)
    for path, text in json_string_leaves(document):
        folded = _fold(text)
        for field, value in values.items():
            if value and _bounded_occurrence(folded, value):
                hits.add((path, field))
    ordered = sorted(hits, key=lambda hit: (_utf16(hit[0]), _utf16(hit[1])))
    return [LiteralProfileValue(path=path, field=field) for path, field in ordered]
