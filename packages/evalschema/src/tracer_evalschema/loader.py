# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Strict JSON loading (ADR 0008 Decision 6).

``loads_strict`` parses standard JSON (RFC 8259, UTF-8) and rejects what ``json.loads`` lets
through silently: a repeated object key (``DuplicateKeyError``, compared after unescaping) and
nesting deeper than ``max_depth`` (``DepthLimitExceeded``, never a ``RecursionError``). The
depth is measured before parsing, so a hostile document never reaches a recursive parser. Syntax
errors, including ``NaN`` and ``Infinity``, are plain ``ValueError``s.

``load_model`` parses strictly and then validates with the model, so the model's typed errors
(for example the schema version errors) come out unchanged. It has no size limit.

``load_bundle`` loads a ``RunBundle`` or an ``ExportBundle`` with a size limit (S-N94, B12): the
length of the UTF-8 bytes (a ``str`` is measured encoded) is compared with ``max_bytes`` before
anything is parsed, and a larger input raises ``BundleSizeError`` naming the two sizes and no
content. Everything else is ``load_model``.
"""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn, TypeVar

from pydantic import BaseModel

from tracer_evalschema.errors import BundleSizeError, DepthLimitExceeded, DuplicateKeyError

__all__ = [
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_DEPTH",
    "load_bundle",
    "load_model",
    "loads_strict",
]

DEFAULT_MAX_DEPTH = 256
"""Default limit on nested arrays and objects; a scalar document has depth 0."""

DEFAULT_MAX_BUNDLE_BYTES = 256 * 1024 * 1024
"""Default limit on the serialized size of one bundle, in UTF-8 bytes (256 MiB, B12). A 4 MiB
sealed recording is about 5.6 MiB of JSON; hours of audio belong in an object store, referenced by
``Artifact.uri``, not in a bundle."""

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


def load_bundle(
    model: type[ModelT],
    data: str | bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> ModelT:
    """Load a ``RunBundle`` or an ``ExportBundle`` from ``data`` with a size limit (S-N94).

    ``TypeError`` for any other ``model`` or for ``data`` that is not ``str`` or ``bytes``, before
    the data is read. ``max_bytes`` is an ``int`` (not a ``bool``) ``>= 0``. Input longer than
    ``max_bytes`` UTF-8 bytes raises ``BundleSizeError`` before it is parsed (also input that is not
    JSON); input of exactly ``max_bytes`` loads. Then ``load_model``: strict parsing and the model's
    own errors, unwrapped.
    """
    # Imported here: the bundle module imports most of the package, the loader only the errors.
    from tracer_evalschema.bundle import ExportBundle, RunBundle

    if model is not RunBundle and model is not ExportBundle:
        raise TypeError("load_bundle loads a RunBundle or an ExportBundle only")
    if not isinstance(data, (str, bytes)):
        raise TypeError(f"data must be str or bytes, got {type(data).__name__}")
    if type(max_bytes) is not int:
        raise TypeError(f"max_bytes must be an int, got {type(max_bytes).__name__}")
    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")
    # A lone surrogate in a str has no UTF-8 form; measured as its 3-byte surrogatepass encoding,
    # it is then rejected or kept by the parser and the models as in load_model.
    size = len(data) if isinstance(data, bytes) else len(data.encode("utf-8", "surrogatepass"))
    if size > max_bytes:
        raise BundleSizeError(
            f"the bundle is {size} bytes of UTF-8, over the limit of {max_bytes} bytes"
        )
    return load_model(model, data, max_depth=max_depth)
