# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""I-JSON values (RFC 7493): JSON that every reader decodes the same way and JCS can digest.

- ``IJsonValue``: ``null``, a boolean, a string, an integer within +-(2**53 - 1), a finite
  number, or an array or object of such values (object keys are strings).
- ``IJsonObject``: an I-JSON object.

Values are stored unchanged (a copy with the same content). Rejected: NaN and infinities,
integers outside the I-JSON range, lone surrogates in strings or keys, and every Python value
that is not JSON (sets, bytes, tuples, other objects). The messages give the JSON Pointer of the
offending member, never its value. In the JSON Schema an I-JSON value is unconstrained (``{}``)
and an object is ``{"type": "object"}``; JSON text cannot hold NaN, so the remaining limits are
code-only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

__all__ = ["MAX_SAFE_INTEGER", "IJsonObject", "IJsonValue", "check_ijson"]

MAX_SAFE_INTEGER = 2**53 - 1
_SURROGATE = re.compile("[\ud800-\udfff]")


def _pointer(path: str, key: str | int) -> str:
    token = str(key).replace("~", "~0").replace("/", "~1")
    return f"{path}/{token}"


def _reject(path: str, why: str) -> PydanticCustomError:
    where = path or "/"
    return PydanticCustomError(
        "ijson", "not an I-JSON value at {where}: {why}", {"where": where, "why": why}
    )


def _copy(value: Any, path: str) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise _reject(path, "integer outside +-(2**53 - 1)")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _reject(path, "NaN and infinities are not JSON numbers")
        return value
    if type(value) is str:
        if _SURROGATE.search(value) is not None:
            raise _reject(path, "a string holds a lone surrogate")
        return value
    if type(value) is list:
        return [_copy(item, _pointer(path, index)) for index, item in enumerate(value)]
    if type(value) is dict:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or _SURROGATE.search(key) is not None:
                raise _reject(path, "an object key must be a string without lone surrogates")
            copied[key] = _copy(item, _pointer(path, key))
        return copied
    raise _reject(path, f"a Python {type(value).__name__} is not a JSON value")


def check_ijson(value: Any) -> Any:
    """Return a copy of ``value`` if it is an I-JSON value; raise ``PydanticCustomError``."""
    return _copy(value, "")


def _check_object(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise PydanticCustomError("ijson_object", "a JSON object is required here")
    copied: dict[str, Any] = _copy(value, "")
    return copied


@dataclass(frozen=True)
class _IJsonRule:
    object_only: bool = False

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        check = _check_object if self.object_only else check_ijson
        return core_schema.no_info_plain_validator_function(check)

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "object"} if self.object_only else {}


IJsonValue = Annotated[Any, _IJsonRule()]
"""Any I-JSON value, stored unchanged."""

IJsonObject = Annotated[dict[str, Any], _IJsonRule(object_only=True)]
"""An I-JSON object, stored unchanged."""
