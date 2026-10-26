# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Facts-only error base of the TTS and LLM engine boundaries (ADR 0026 rule 4).

An error holds machine facts, never a provider message, a body, a header, a key or text: every
fact is validated against a fixed shape, and a value that does not fit is refused without being
quoted.
"""

from __future__ import annotations

import re
from typing import Any, Final

from tracer_evalcore.errors import EvalcoreError

__all__ = ["FactError"]

_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _is_int(value: object) -> bool:
    return type(value) is int


class FactError(EvalcoreError):
    """Base of the engine errors; the facts are keyword-only attributes."""

    def __init__(
        self,
        *,
        index: int | None = None,
        status: int | None = None,
        kind: str | None = None,
        code: str | None = None,
        request_id: str | None = None,
        attempts: int | None = None,
        reason: str | None = None,
    ) -> None:
        if index is not None and not (_is_int(index) and index >= 0):
            raise ValueError("index: not a non-negative integer")
        if status is not None and not (_is_int(status) and 100 <= status <= 599):
            raise ValueError("status: not an HTTP status")
        if attempts is not None and not (_is_int(attempts) and attempts >= 1):
            raise ValueError("attempts: not a positive integer")
        for name, value in (("kind", kind), ("code", code), ("reason", reason)):
            if value is not None and not (type(value) is str and _CODE.fullmatch(value)):
                raise ValueError(f"{name}: not a short machine code")
        if request_id is not None and not (
            type(request_id) is str and _REQUEST_ID.fullmatch(request_id)
        ):
            raise ValueError("request_id: not a request identifier")
        self.index = index
        self.status = status
        self.kind = kind
        self.code = code
        self.request_id = request_id
        self.attempts = attempts
        self.reason = reason
        parts: list[str] = []
        if index is not None:
            parts.append(f"request {index}")
        if status is not None:
            parts.append(f"HTTP {status}")
        if kind is not None:
            parts.append(f"transport {kind}")
        if code is not None:
            parts.append(f"code {code}")
        if reason is not None:
            parts.append(f"reason {reason}")
        if request_id is not None:
            parts.append(f"request id {request_id}")
        if attempts is not None:
            parts.append(f"{attempts} attempt(s)")
        super().__init__(f"{type(self).__name__}: " + ", ".join(parts) if parts else "")

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild, (type(self), dict(self.__dict__)))


def _rebuild(cls: type[FactError], facts: dict[str, Any]) -> FactError:
    return cls(**facts)
