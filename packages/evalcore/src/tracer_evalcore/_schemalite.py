# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A minimal JSON Schema checker for the closed documents of Phase 4 (no runtime ``jsonschema``).

It reads the subset the three documents use: ``type`` (a name or a list), ``enum``, ``pattern``,
``minimum``, ``maximum``, ``required``, ``additionalProperties: false``, ``properties``,
``items`` and ``oneOf``. An integer is a Python ``int`` that is not a ``bool``. The result is the
JSON pointer of the first violation (property names and array positions only, never a value).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["first_violation"]


def _is_type(name: str, value: Any) -> bool:
    if name == "object":
        return isinstance(value, Mapping)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return type(value) is str
    if name == "integer":
        return type(value) is int
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
    if type(value) is str and "pattern" in schema and re.search(schema["pattern"], value) is None:
        return here
    if type(value) is int:
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
    if isinstance(value, list) and "items" in schema:
        for position, item in enumerate(value):
            found = first_violation(schema["items"], item, f"{pointer}/{position}")
            if found is not None:
                return found
    return None
