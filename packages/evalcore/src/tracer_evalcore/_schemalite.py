# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A minimal JSON Schema checker for the closed documents of Phase 4 (no runtime ``jsonschema``).

It reads the subset the stored documents use: ``type`` (a name or a list), ``enum``, ``pattern``
(a ``$`` means the end of the text: a trailing line feed does not match), ``minimum``, ``maximum``,
``minLength``, ``maxLength``, ``minItems``, ``maxItems``, ``required``, ``additionalProperties:
false``, ``properties``, ``items`` and ``oneOf``. An integer is a Python ``int`` that is not a
``bool``; a number is an ``int`` or a ``float`` that is not a ``bool``. The result is the JSON
pointer of the first violation (property names and array positions only, never a value).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["first_violation"]


def _end_of_text(pattern: str) -> str:
    """A ``$`` of a schema pattern is the end of the text (Python's also matches before a final
    line feed, which a JSON Schema pattern does not)."""
    return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def _is_type(name: str, value: Any) -> bool:
    if name == "object":
        return isinstance(value, Mapping)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return type(value) is str
    if name == "integer":
        return type(value) is int
    if name == "number":
        return type(value) in (int, float)
    if name == "boolean":
        return type(value) is bool
    if name == "null":
        return value is None
    return False


def first_violation(schema: Mapping[str, Any], value: Any, pointer: str = "") -> str | None:
    """The pointer of the first thing in ``value`` that ``schema`` refuses, or ``None``."""
    here = pointer or "/"
    kinds = schema.get("type")
    if kinds is not None:
        names: Sequence[str] = [kinds] if isinstance(kinds, str) else kinds
        if not any(_is_type(name, value) for name in names):
            return here
    if "enum" in schema and not any(
        value == member and type(value) is type(member) for member in schema["enum"]
    ):
        return here
    if type(value) is str:
        if "pattern" in schema and re.search(_end_of_text(schema["pattern"]), value) is None:
            return here
        if "minLength" in schema and len(value) < schema["minLength"]:
            return here
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return here
    if type(value) in (int, float):
        if "minimum" in schema and value < schema["minimum"]:
            return here
        if "maximum" in schema and value > schema["maximum"]:
            return here
    if "oneOf" in schema:
        matching = sum(1 for option in schema["oneOf"] if first_violation(option, value) is None)
        if matching != 1:
            return here
    if isinstance(value, Mapping):
        for name in schema.get("required", ()):
            if name not in value:
                return f"{pointer}/{name}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    return f"{pointer}/{name}"
        for name, sub in properties.items():
            if name in value:
                found = first_violation(sub, value[name], f"{pointer}/{name}")
                if found is not None:
                    return found
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return here
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return here
    if isinstance(value, list) and "items" in schema:
        for position, item in enumerate(value):
            found = first_violation(schema["items"], item, f"{pointer}/{position}")
            if found is not None:
                return found
    return None
