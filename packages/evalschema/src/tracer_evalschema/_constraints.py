# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Reusable string constraints used as ``Annotated[str, <marker>]`` metadata.

Each marker builds the pydantic validator and the JSON Schema from one value, so pydantic and the
generated schema cannot drift apart:

- ``FullMatch(pattern)``: the whole string must match. The JSON Schema ``pattern`` is the given
  text (anchored ``^...$``); Python matches the same expression with ``re.fullmatch``, which gives
  ``$`` the ECMAScript "end of text" meaning (a trailing newline is never accepted).
- ``ClosedSet(values)``: a closed value set (ADR 0003 Decision 6). The schema is always
  ``{"type": "string", "enum": [...]}``, never ``const``, also for a single member.

Both accept only real ``str`` values (strict: no coercion from numbers, bytes or ``None``).

Inside a model that hides its input (``_hiding.values_hidden()``) neither quotes the rejected
value, in the message or in the error context (ADR 0007 Decision 8 (c), finding N42): the message
names the rule and the expected form only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NoReturn

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._hiding import values_hidden


def _anchored_body(pattern: str) -> str:
    if not (pattern.startswith("^") and pattern.endswith("$")):
        raise ValueError(f"a JSON Schema pattern here must be anchored with ^ and $: {pattern!r}")
    return pattern[1:-1]


@dataclass(frozen=True)
class FullMatch:
    """The whole string matches ``pattern`` (a JSON Schema pattern written ``^...$``).

    ``max_length`` (optional) also bounds the length in code points and is emitted as
    ``maxLength``. With ``echo_value=False`` the error context carries no ``value``, so a message
    for a credential-checked field cannot show the rejected text (ADR 0007 Decision 8 (c)); such
    a message must then not use ``{value}``. Inside a model that hides its input the value is
    never quoted either: ``{value}`` in the message reads "the value" and the context has no
    ``value``.
    """

    pattern: str
    error_type: str = "string_pattern_mismatch"
    message: str = "{value} does not match {pattern}"
    max_length: int | None = None
    echo_value: bool = True
    _regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_regex", re.compile(_anchored_body(self.pattern)))
        if not self.echo_value and "{value}" in self.message:
            raise ValueError("a FullMatch that does not echo its value cannot use {value}")

    def matches(self, value: str) -> bool:
        if self.max_length is not None and len(value) > self.max_length:
            return False
        return self._regex.fullmatch(value) is not None

    @property
    def quiet_message(self) -> str:
        """The message without the rejected value (``{value}`` reads "the value")."""
        return self.message.replace("{value}", "the value")

    def _check(self, value: str) -> str:
        if not self.matches(value):
            context: dict[str, object] = {"pattern": self.pattern}
            message = self.quiet_message
            if self.echo_value and not values_hidden():
                context["value"] = repr(value)
                message = self.message
            raise PydanticCustomError(self.error_type, message, context)
        return value

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            self._check, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema: JsonSchemaValue = {"type": "string", "pattern": self.pattern}
        if self.max_length is not None:
            json_schema["maxLength"] = self.max_length
        return json_schema


def _default_reject(value: str, values: tuple[str, ...]) -> NoReturn:
    if values_hidden():
        raise PydanticCustomError(
            "closed_set", "the value is not one of {allowed}", {"allowed": values}
        )
    raise PydanticCustomError(
        "closed_set", "{value} is not one of {allowed}", {"value": repr(value), "allowed": values}
    )


@dataclass(frozen=True)
class ClosedSet:
    """A closed value set: exactly the strings in ``values``.

    ``reject`` raises the error for a string outside the set; the default is a
    ``PydanticCustomError`` (a ``ValidationError`` for the caller). A set backed by a registry can
    raise a typed error instead (``ContentDigest.scheme`` raises ``UnknownDigestScheme``).
    """

    values: tuple[str, ...]
    reject: Callable[[str, tuple[str, ...]], NoReturn] = _default_reject

    def __post_init__(self) -> None:
        if not self.values or len(set(self.values)) != len(self.values):
            raise ValueError(f"a closed set needs distinct members: {self.values!r}")

    def _check(self, value: str) -> str:
        if value not in self.values:
            self.reject(value, self.values)
        return value

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            self._check, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "enum": list(self.values)}
