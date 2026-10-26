# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The speech-to-text engine boundary (ADR 0024 rules 1, 10 and 11).

A protocol, the typed result of one transcription and the provider-neutral error taxonomy. The
pipeline types the engine and catches its failures through this module, so it never imports a
network package. Errors hold machine facts only: never a provider message, a body, a header, a
key or audio.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.errors import EvalcoreError
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import ArtifactId, RunId
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.transcript import Transcript, Turn
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "SttAuthError",
    "SttConfigurationError",
    "SttContext",
    "SttEngine",
    "SttError",
    "SttKeyMissingError",
    "SttLanguageNotSupportedError",
    "SttOutcome",
    "SttRateLimited",
    "SttRequestRejected",
    "SttResponseInvalid",
    "SttServerError",
    "SttTransportError",
    "UsageSink",
    "UsageStreamingSttEngine",
]


class SttError(EvalcoreError):
    """Base of every speech-to-text failure; the facts are keyword-only attributes."""

    def __init__(
        self,
        *,
        chunk_index: int | None = None,
        status: int | None = None,
        kind: str | None = None,
        code: str | None = None,
        request_id: str | None = None,
        attempts: int | None = None,
        reason: str | None = None,
    ) -> None:
        self.chunk_index = chunk_index
        self.status = status
        self.kind = kind
        self.code = code
        self.request_id = request_id
        self.attempts = attempts
        self.reason = reason
        parts: list[str] = []
        if chunk_index is not None:
            parts.append(f"chunk {chunk_index}")
        if status is not None:
            parts.append(f"HTTP {status}")
        if kind is not None:
            parts.append(f"transport {kind}")
        if code is not None:
            parts.append(f"code {code}")
        if reason is not None:
            parts.append(f"reason {reason}")
        if request_id is not None:
            parts.append(f"request {request_id}")
        if attempts is not None:
            parts.append(f"{attempts} attempt(s)")
        super().__init__(f"{type(self).__name__}: " + ", ".join(parts) if parts else "")

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild, (type(self), dict(self.__dict__)))


def _rebuild(cls: type[SttError], facts: dict[str, Any]) -> SttError:
    return cls(**facts)


class SttAuthError(SttError):
    """HTTP 401 or 403, or the provider's invalid-key code. Never retried."""


class SttRequestRejected(SttError):
    """Any other 4xx except 429. Never retried."""


class SttRateLimited(SttError):
    """429 after the last attempt, or a ``Retry-After`` above the bound."""


class SttServerError(SttError):
    """5xx after the last attempt."""


class SttTransportError(SttError):
    """A connect, read or write error or a timeout after the last attempt."""


class SttResponseInvalid(SttError):
    """A response that is not the documented success shape. Never retried."""


class SttConfigurationError(SttError, ValueError):
    """The run is configured so that no request may be sent."""


class SttKeyMissingError(SttConfigurationError):
    """The API key is missing, empty or unusable."""


class SttLanguageNotSupportedError(SttConfigurationError):
    """The configured language is not one the engine supports."""


@dataclass(frozen=True)
class SttContext:
    """What an engine needs besides the audio.

    ``created_at`` stamps every record the engine builds; the engine reads no clock for it.
    """

    run_id: RunId
    audio_artifact_id: ArtifactId
    created_at: datetime
    privacy: PrivacyMarker
    configured_language: str


@dataclass(frozen=True)
class SttOutcome:
    """A transcript or a failure, never both (all or nothing, ADR 0024 rule 11).

    ``usage`` holds one record for every chunk that was answered, also when the run failed.
    """

    transcript: Transcript | None
    turns: tuple[Turn, ...]
    usage: tuple[UsageRecord, ...]
    warnings: tuple[IngestWarning, ...]
    failure: SttError | None

    def __post_init__(self) -> None:
        if (self.transcript is None) == (self.failure is None):
            raise ValueError("an outcome holds a transcript or a failure, exactly one")
        if self.failure is not None and (self.turns or self.warnings):
            raise ValueError("a failed outcome holds no turn and no warning")


@runtime_checkable
class SttEngine(Protocol):
    """Chunks in, a typed outcome out."""

    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome: ...


UsageSink = Callable[[UsageRecord], None]


@runtime_checkable
class UsageStreamingSttEngine(Protocol):
    """An engine that hands out the usage of each answered chunk as it arrives.

    A runtime check sees the names only; the caller also reads the value of ``streams_usage``.
    """

    streams_usage: bool

    def transcribe(
        self, plan: ChunkPlan, context: SttContext, *, on_usage: UsageSink | None = None
    ) -> SttOutcome: ...
