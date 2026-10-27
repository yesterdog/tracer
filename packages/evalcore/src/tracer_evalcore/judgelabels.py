# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The human labels file ``tracer.judge_labels/1`` (ADR 0030 rule 22).

Part 1 ships the format, the closed schema and the validator; the commands ``tracer labels packet``,
``check`` and ``agreement`` are task P4-J21. A label has a verdict (``null`` for an item not
labelled: shown separately and never counted as a disagreement) and the basis the human judged on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from tracer_evalcore._schemalite import first_violation
from tracer_evalcore.errors import InputRefusedError

__all__ = [
    "JUDGE_LABELS_FORMAT",
    "JUDGE_LABELS_JSON_SCHEMA",
    "LABEL_BASES",
    "JudgeLabelsError",
    "validate_judge_labels",
]

JUDGE_LABELS_FORMAT: Final = "tracer.judge_labels/1"
LABEL_BASES: Final = ("audio", "stt_text", "both")
_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_labels/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "bundle_id",
    "labeller",
    "labels"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_labels/1"
      ]
    },
    "bundle_id": {
      "type": "string",
      "pattern": "^rbnd_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "labeller": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9_-]{0,63}$"
    },
    "labels": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "item_id",
          "verdict",
          "basis"
        ],
        "properties": {
          "item_id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
            "minLength": 1,
            "maxLength": 40
          },
          "verdict": {
            "type": [
              "string",
              "null"
            ],
            "enum": [
              "agrees",
              "disagrees",
              "cannot_tell",
              null
            ]
          },
          "basis": {
            "type": "string",
            "enum": [
              "audio",
              "stt_text",
              "both"
            ]
          }
        }
      }
    }
  }
}"""
JUDGE_LABELS_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_SCHEMA_TEXT)


class JudgeLabelsError(InputRefusedError):
    """A labels document was refused. ``code`` is the stable reason (the message); ``pointer`` names
    the place of the first fault, never a value."""

    def __init__(self, code: str, pointer: str = "/") -> None:
        self.code = code
        self.pointer = pointer
        super().__init__(code)


def validate_judge_labels(document: Mapping[str, Any]) -> None:
    """The schema, then: no item id twice in a file."""
    found = first_violation(JUDGE_LABELS_JSON_SCHEMA, document)
    if found is not None:
        raise JudgeLabelsError("judge_labels_invalid", found)
    ids = [label["item_id"] for label in document["labels"]]
    if len(set(ids)) != len(ids):
        raise JudgeLabelsError("judge_labels_invalid", "/labels")
