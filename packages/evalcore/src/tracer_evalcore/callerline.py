# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The output validator of the caller brain (ADR 0028 rules 6 and 7; table mr-caller-charset/1).

A reply is accepted only if, after removing leading and trailing white space (``str.strip``) and
nothing else, it is exactly the end sentinel, or exactly one line of 1 to ``max_line_chars`` code
points in Unicode NFC, every character of which is in the allowed set of the persona's script,
with at least one letter of that script. The validator refuses; it never repairs, and a refusal
holds a code, never the text.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final

from tracer_evalcore.simtypes import END_SENTINEL, MAX_LINE_CHARS

__all__ = [
    "ALLOWED_LANGUAGES",
    "CHARSET_ID",
    "REFUSAL_CODES",
    "ReplyCheck",
    "check_caller_reply",
    "require_max_line_chars",
]

CHARSET_ID: Final = "mr-caller-charset/1"
ALLOWED_LANGUAGES: Final = ("mr",)
REFUSAL_CODES: Final = (
    "empty",
    "multiple_lines",
    "too_long",
    "not_nfc",
    "character_not_allowed",
    "no_script_letter",
)
_KINDS: Final = ("line", "end_call", "refused")

# V01, V03, V04: allowed; V02, V05, V06: denied; V07: denied categories; deny wins.
_ALLOW: Final = (
    (0x0900, 0x097F),
    (0x0030, 0x0039),
    (0x0020, 0x0022),
    (0x0027, 0x0029),
    (0x002C, 0x002E),
    (0x003A, 0x003B),
    (0x003F, 0x003F),
)
_DENY: Final = (
    (0x0966, 0x0970),
    (0x0041, 0x005A),
    (0x0061, 0x007A),
    (0x00AD, 0x00AD),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2069),
    (0xFEFF, 0xFEFF),
)
_DENIED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_LETTER_FIRST: Final = 0x0904  # V09
_LETTER_LAST: Final = 0x0939


@dataclass(frozen=True)
class ReplyCheck:
    """``line`` (an accepted line), ``end_call`` (the sentinel) or ``refused`` (a code)."""

    kind: str
    line: str | None
    refusal: str | None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError("kind: not an accepted value")
        if self.kind == "line":
            if type(self.line) is not str or not self.line or self.refusal is not None:
                raise ValueError("a line holds its text and no refusal")
        elif self.kind == "end_call":
            if self.line is not None or self.refusal is not None:
                raise ValueError("an end holds neither a line nor a refusal")
        elif self.line is not None or self.refusal not in REFUSAL_CODES:
            raise ValueError("a refusal holds a code and no line")


def require_max_line_chars(value: object) -> int:
    """Return ``value`` when it is an int from 1 to 2,500 (a bool is not), else refuse it."""
    if type(value) is not int or not 1 <= value <= MAX_LINE_CHARS:
        raise ValueError("max_line_chars: not an integer from 1 to 2500")
    return value


def _allowed(char: str) -> bool:
    code = ord(char)
    if unicodedata.category(char) in _DENIED_CATEGORIES:
        return False
    if any(low <= code <= high for low, high in _DENY):
        return False
    return any(low <= code <= high for low, high in _ALLOW)


def _refused(code: str) -> ReplyCheck:
    return ReplyCheck(kind="refused", line=None, refusal=code)


def check_caller_reply(reply: str, *, language: str, max_line_chars: int) -> ReplyCheck:
    """The verdict on one reply of the model or one replayed line."""
    require_max_line_chars(max_line_chars)
    if type(language) is not str or language not in ALLOWED_LANGUAGES:
        raise ValueError("language: no allowed character set")
    if type(reply) is not str:
        raise TypeError("reply: not text")
    stripped = reply.strip()
    if stripped == END_SENTINEL:
        return ReplyCheck(kind="end_call", line=None, refusal=None)
    if not stripped:
        return _refused("empty")
    if "\n" in stripped:
        return _refused("multiple_lines")
    if len(stripped) > max_line_chars:
        return _refused("too_long")
    if not unicodedata.is_normalized("NFC", stripped):
        return _refused("not_nfc")
    if not all(_allowed(char) for char in stripped):
        return _refused("character_not_allowed")
    if not any(_LETTER_FIRST <= ord(char) <= _LETTER_LAST for char in stripped):
        return _refused("no_script_letter")
    return ReplyCheck(kind="line", line=stripped, refusal=None)
