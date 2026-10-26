# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The language-model engine boundary (ADR 0026 rule 4, ADR 0024 amendment A3, ADR 0028).

The shape of the speech-to-text boundary. A request is fully explicit: a model, ``max_tokens``,
a seed (or ``None``) and a reasoning effort (or ``None``) have no default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from tracer_evalcore._engine_errors import FactError
from tracer_evalcore.stt import UsageSink
from tracer_evalschema.ids import RunId
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "LLM_ROLES",
    "LlmAuthError",
    "LlmCapReachedError",
    "LlmConfigurationError",
    "LlmContext",
    "LlmEngine",
    "LlmError",
    "LlmKeyMissingError",
    "LlmMessage",
    "LlmOutcome",
    "LlmRateLimited",
    "LlmRequest",
    "LlmRequestRejected",
    "LlmResponseInvalid",
    "LlmServerError",
    "LlmTransportError",
    "UsageSink",
    "UsageStreamingLlmEngine",
    "complete_once",
]

LLM_ROLES: Final = ("system", "user", "assistant")
_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}")
_EFFORT: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
_MAX_SAFE: Final = 2**53 - 1


class LlmError(FactError):
    """Base of every language-model failure."""


class LlmAuthError(LlmError):
    """HTTP 401 or 403, or the provider's invalid-key code. Never retried."""


class LlmRequestRejected(LlmError):
    """Any other 4xx except 429. Never retried."""


class LlmRateLimited(LlmError):
    """429 after the last attempt."""


class LlmServerError(LlmError):
    """5xx after the last attempt."""


class LlmTransportError(LlmError):
    """A connect, read or write error or a timeout after the last attempt."""


class LlmResponseInvalid(LlmError):
    """A response that is not the documented success shape. Never retried."""


class LlmCapReachedError(LlmError):
    """The request would cross a spend cap; it was not sent."""


class LlmConfigurationError(LlmError, ValueError):
    """The run is configured so that no request may be sent."""


class LlmKeyMissingError(LlmConfigurationError):
    """The API key is missing, empty or unusable."""


@dataclass(frozen=True)
class LlmMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in LLM_ROLES:
            raise ValueError("role: not an accepted value")
        if type(self.content) is not str or not self.content:
            raise ValueError("content: empty")


@dataclass(frozen=True)
class LlmRequest:
    """One request; no field has a default (ADR 0026 rule 12, ADR 0028 rule 5)."""

    index: int
    messages: tuple[LlmMessage, ...]
    model: str
    max_tokens: int
    seed: int | None
    reasoning_effort: str | None

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("index: not a non-negative integer")
        if type(self.messages) is not tuple or not self.messages:
            raise ValueError("messages: not a non-empty tuple")
        if not all(isinstance(m, LlmMessage) for m in self.messages):
            raise TypeError("messages: not LlmMessage records")
        if type(self.model) is not str or not _NAME.fullmatch(self.model):
            raise ValueError("model: not an accepted value")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= _MAX_SAFE:
            raise ValueError("max_tokens: outside 1 to 2**53 - 1")
        if self.seed is not None and (
            type(self.seed) is not int or not 0 <= self.seed <= _MAX_SAFE
        ):
            raise ValueError("seed: not a non-negative integer or None")
        if self.reasoning_effort is not None and (
            type(self.reasoning_effort) is not str or not _EFFORT.fullmatch(self.reasoning_effort)
        ):
            raise ValueError("reasoning_effort: not an accepted value or None")


@dataclass(frozen=True)
class LlmContext:
    run_id: RunId
    created_at: datetime


@dataclass(frozen=True)
class LlmOutcome:
    """A reply text or a failure, never both; ``usage`` holds one record per answered request."""

    text: str | None
    usage: tuple[UsageRecord, ...]
    failure: LlmError | None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.failure is None):
            raise ValueError("an outcome holds a text or a failure, exactly one")
        for record in self.usage:
            if not isinstance(record, UsageRecord):
                raise TypeError("usage: not a UsageRecord")
            if record.stage != "sim_caller_llm" or record.domain != "evaluation":
                raise ValueError("usage: not a sim_caller_llm record of the evaluation domain")


@runtime_checkable
class LlmEngine(Protocol):
    """One request in, a typed outcome out."""

    def complete(self, request: LlmRequest, context: LlmContext) -> LlmOutcome: ...


@runtime_checkable
class UsageStreamingLlmEngine(Protocol):
    """An engine that hands out the usage of each answered request before it returns."""

    streams_usage: bool

    def complete(
        self, request: LlmRequest, context: LlmContext, *, on_usage: UsageSink | None = None
    ) -> LlmOutcome: ...


def complete_once(
    engine: LlmEngine | UsageStreamingLlmEngine,
    request: LlmRequest,
    context: LlmContext,
    *,
    on_usage: UsageSink,
) -> LlmOutcome:
    """Send one request; its usage reaches ``on_usage`` before this function returns."""
    if isinstance(engine, UsageStreamingLlmEngine) and engine.streams_usage is True:
        return engine.complete(request, context, on_usage=on_usage)
    outcome = engine.complete(request, context)
    for record in outcome.usage:
        on_usage(record)
    return outcome
