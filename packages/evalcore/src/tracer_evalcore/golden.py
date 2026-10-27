# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The golden set: the document ``tracer.golden_set/1`` and its loader (ADR 0030 rules 1 to 3).

A golden set is a strict JSON file with closed keys and a version member: at most 100 items, each
bound to one scripted turn, with either 1 to 10 expected facts (mode ``all_facts``) or one expected
answer (mode ``equivalent_answer``). Every text is NFC and inside the character table of the
Marathi caller (``mr-caller-charset/1``) on one line. A refusal is a ``GoldenSetError`` whose
message is its stable code and never a value. Binding the set to the evaluator is a second step
(``bind_golden_set``), taken at step 1 of the command, before any key is read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore.callerline import check_caller_reply
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.simspec import SimEvaluatorSpec
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "GOLDEN_SET_FORMAT",
    "GOLDEN_SET_JSON_SCHEMA",
    "MATCH_MODES",
    "MAX_EXPECTED_ANSWER_CHARS",
    "MAX_FACTS_PER_ITEM",
    "MAX_FACT_CHARS",
    "MAX_GOLDEN_ITEMS",
    "GoldenItem",
    "GoldenSet",
    "GoldenSetError",
    "bind_golden_set",
    "parse_golden_set",
]

GOLDEN_SET_FORMAT: Final = "tracer.golden_set/1"
MATCH_MODES: Final = ("all_facts", "equivalent_answer")
MAX_GOLDEN_ITEMS: Final = 100
MAX_FACTS_PER_ITEM: Final = 10
MAX_FACT_CHARS: Final = 300
MAX_EXPECTED_ANSWER_CHARS: Final = 1000
MAX_QUESTION_CHARS: Final = 2500
_MAX_SAFE: Final = 9007199254740991
_SNAKE: Final = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_TOP_KEYS: Final = frozenset({"format", "key", "language", "items"})
_COMMON_KEYS: Final = frozenset(
    {"id", "scripted_turn_index", "question", "match_mode", "needs_native_review"}
)
_ITEM_KEYS: Final = {
    "all_facts": _COMMON_KEYS | {"expected_facts"},
    "equivalent_answer": _COMMON_KEYS | {"expected_answer"},
}
_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.golden_set/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "key",
    "language",
    "items"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.golden_set/1"
      ]
    },
    "key": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
      "minLength": 1,
      "maxLength": 64
    },
    "language": {
      "type": "string",
      "enum": [
        "mr"
      ]
    },
    "items": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {
        "oneOf": [
          {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "id",
              "scripted_turn_index",
              "question",
              "needs_native_review",
              "match_mode",
              "expected_facts"
            ],
            "properties": {
              "id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
                "minLength": 1,
                "maxLength": 40
              },
              "scripted_turn_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9007199254740991
              },
              "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2500
              },
              "needs_native_review": {
                "type": "boolean"
              },
              "match_mode": {
                "type": "string",
                "enum": [
                  "all_facts"
                ]
              },
              "expected_facts": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 300
                }
              }
            }
          },
          {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "id",
              "scripted_turn_index",
              "question",
              "needs_native_review",
              "match_mode",
              "expected_answer"
            ],
            "properties": {
              "id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
                "minLength": 1,
                "maxLength": 40
              },
              "scripted_turn_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9007199254740991
              },
              "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2500
              },
              "needs_native_review": {
                "type": "boolean"
              },
              "match_mode": {
                "type": "string",
                "enum": [
                  "equivalent_answer"
                ]
              },
              "expected_answer": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000
              }
            }
          }
        ]
      }
    }
  }
}"""
GOLDEN_SET_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_SCHEMA_TEXT)
_TEXT_CODES: Final = {
    "empty": "golden_text_empty",
    "multiple_lines": "golden_text_multiple_lines",
    "not_nfc": "golden_text_not_nfc",
    "no_script_letter": "golden_text_no_script_letter",
}


class GoldenSetError(InputRefusedError):
    """A golden set was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GoldenItem:
    id: str
    scripted_turn_index: int
    question: str
    match_mode: str
    expected_facts: tuple[str, ...]
    expected_answer: str | None
    needs_native_review: bool


@dataclass(frozen=True)
class GoldenSet:
    key: str
    language: str
    items: tuple[GoldenItem, ...]
    sha256: str


def _is_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SAFE


def _check_text(text: str, limit: int, too_long: str) -> None:
    if len(text) > limit:
        raise GoldenSetError(too_long)
    if "\n" in text or "\r" in text:
        raise GoldenSetError("golden_text_multiple_lines")
    checked = check_caller_reply(text, language="mr", max_line_chars=2500)
    if checked.kind == "line":
        return
    raise GoldenSetError(
        _TEXT_CODES.get(checked.refusal or "", "golden_text_character_not_allowed")
    )


def _item(raw: object) -> GoldenItem:
    if not isinstance(raw, Mapping):
        raise GoldenSetError("golden_item_shape")
    mode = raw.get("match_mode")
    if mode not in MATCH_MODES:
        raise GoldenSetError(
            "golden_item_shape" if "match_mode" not in raw else "golden_item_invalid_match_mode"
        )
    if set(raw) != _ITEM_KEYS[mode]:
        raise GoldenSetError("golden_item_shape")
    item_id = raw["id"]
    if type(item_id) is not str or len(item_id) > 40 or _SNAKE.fullmatch(item_id) is None:
        raise GoldenSetError("golden_item_invalid_id")
    index = raw["scripted_turn_index"]
    if not _is_int(index):
        raise GoldenSetError("golden_item_invalid_turn_index")
    question = raw["question"]
    if type(question) is not str or not 1 <= len(question) <= MAX_QUESTION_CHARS:
        raise GoldenSetError("golden_item_invalid_question")
    if type(raw["needs_native_review"]) is not bool:
        raise GoldenSetError("golden_item_shape")
    _check_text(question, MAX_QUESTION_CHARS, "golden_item_invalid_question")
    facts: tuple[str, ...] = ()
    answer: str | None = None
    if mode == "all_facts":
        listed = raw["expected_facts"]
        if not isinstance(listed, list) or not 1 <= len(listed) <= MAX_FACTS_PER_ITEM:
            raise GoldenSetError("golden_facts_count")
        if any(type(fact) is not str for fact in listed):
            raise GoldenSetError("golden_item_shape")
        for fact in listed:
            _check_text(fact, MAX_FACT_CHARS, "golden_fact_too_long")
        facts = tuple(listed)
    else:
        answer = raw["expected_answer"]
        if type(answer) is not str:
            raise GoldenSetError("golden_item_shape")
        _check_text(answer, MAX_EXPECTED_ANSWER_CHARS, "golden_answer_too_long")
    return GoldenItem(item_id, index, question, mode, facts, answer, raw["needs_native_review"])


def parse_golden_set(text: str) -> GoldenSet:
    """The golden set of ``text``; ``GoldenSetError`` on the first fault."""
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError, RecursionError):
        raise GoldenSetError("golden_not_json") from None
    if not isinstance(document, Mapping):
        raise GoldenSetError("golden_shape")
    if document.get("format") != GOLDEN_SET_FORMAT:
        raise GoldenSetError("golden_format")
    for key in sorted(_TOP_KEYS):
        if key not in document:
            raise GoldenSetError(f"golden_missing_{key}")
    if set(document) - _TOP_KEYS:
        raise GoldenSetError("golden_unknown_key")
    if document["language"] != "mr":
        raise GoldenSetError("golden_language_not_supported")
    key = document["key"]
    if type(key) is not str or len(key) > 64 or _SNAKE.fullmatch(key) is None:
        raise GoldenSetError("golden_invalid_key")
    raw_items = document["items"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_GOLDEN_ITEMS:
        raise GoldenSetError("golden_items_count")
    items = tuple(_item(raw) for raw in raw_items)
    if len({item.id for item in items}) != len(items):
        raise GoldenSetError("golden_item_repeated_id")
    return GoldenSet(
        key=key,
        language="mr",
        items=items,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def bind_golden_set(
    golden: GoldenSet,
    evaluator: SimEvaluatorSpec,
    *,
    caller_mode: str,
    barge_in_turns: Collection[int] = (),
) -> None:
    """Rule 3: only the scripted caller; each question is the scripted line, byte for byte; no
    item on a turn with a scheduled barge-in. ``GoldenSetError`` on the first fault."""
    if caller_mode != "scripted":
        raise GoldenSetError("golden_caller_not_scripted")
    turns = evaluator.scripted_turns
    for item in golden.items:
        index = item.scripted_turn_index
        if index >= len(turns):
            raise GoldenSetError("golden_turn_missing")
        if item.question != turns[index].text:
            raise GoldenSetError("golden_question_mismatch")
        if index in barge_in_turns:
            raise GoldenSetError("golden_turn_barge_in")
