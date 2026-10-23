# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Strict JSON loading (ADR 0008 Decision 6).

``loads_strict`` parses standard JSON (RFC 8259, UTF-8) and rejects what ``json.loads`` lets
through silently: a repeated object key (``DuplicateKeyError``, compared after unescaping) and
nesting deeper than ``max_depth`` (``DepthLimitExceeded``, never a ``RecursionError``). The
depth is measured before parsing, so a hostile document never reaches a recursive parser. Syntax
errors, including ``NaN`` and ``Infinity``, are plain ``ValueError``s.

``load_model`` parses strictly and then validates with the model, so the model's typed errors
(for example the schema version errors) come out unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn, TypeVar

from pydantic import BaseModel

from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError

__all__ = ["DEFAULT_MAX_DEPTH", "load_model", "loads_strict"]

DEFAULT_MAX_DEPTH = 256
"""Default limit on nested arrays and objects; a scalar document has depth 0."""

ModelT = TypeVar("ModelT", bound=BaseModel)

# A complete JSON string (skipped as a whole) or one bracket.
_STRING_OR_BRACKET = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', re.DOTALL)


def _check_depth(text: str, max_depth: int) -> None:
    depth = 0
    for token in _STRING_OR_BRACKET.finditer(text):
        char = token.group()
        if char in "[{":
            depth += 1
            if depth > max_depth:
                raise DepthLimitExceeded(
                    f"JSON nesting depth exceeds {max_depth} (at offset {token.start()})"
                )
        elif char in "]}":
            depth -= 1


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a JSON value")


def loads_strict(data: str | bytes, *, max_depth: int = DEFAULT_MAX_DEPTH) -> Any:
    """Parse one JSON document strictly (see the module docstring)."""
    if type(max_depth) is not int or max_depth < 0:
        raise ValueError(f"max_depth must be an int >= 0, got {max_depth!r}")
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    _check_depth(text, max_depth)
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except RecursionError:  # only reachable with a max_depth above what the parser supports
        raise DepthLimitExceeded(
            f"JSON nesting depth is too deep for this interpreter (max_depth={max_depth})"
        ) from None


def load_model(
    model: type[ModelT], data: str | bytes, *, max_depth: int = DEFAULT_MAX_DEPTH
) -> ModelT:
    """Parse ``data`` with ``loads_strict`` and validate it with ``model``."""
    return model.model_validate(loads_strict(data, max_depth=max_depth))
