# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The text-to-speech engine boundary (ADR 0026 rule 4, ADR 0024 amendments A1 and A2).

The shape of the speech-to-text boundary: a protocol, typed request and outcome records and a
provider-neutral error taxonomy. The core imports no network package.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from tracer_evalcore._engine_errors import FactError
from tracer_evalcore.stt import UsageSink
from tracer_evalschema.ids import RunId
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "TTS_CODECS",
    "TtsAuthError",
    "TtsCapReachedError",
    "TtsConfigurationError",
    "TtsContext",
    "TtsEngine",
    "TtsError",
    "TtsKeyMissingError",
    "TtsLanguageNotSupportedError",
    "TtsOutcome",
    "TtsRateLimited",
    "TtsRequest",
    "TtsRequestRejected",
    "TtsResponseInvalid",
    "TtsServerError",
    "TtsTextTooLongError",
    "TtsTransportError",
    "TtsVoice",
    "UsageSink",
    "UsageStreamingTtsEngine",
    "synthesise_all",
]

TTS_CODECS: Final = ("linear16", "mulaw")
_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}")
_LANGUAGE: Final = re.compile(r"[a-z]{2,3}-[A-Z]{2}")
_PACE: Final = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{0,5}[1-9])?")


class TtsError(FactError):
    """Base of every text-to-speech failure."""


class TtsAuthError(TtsError):
    """HTTP 401 or 403, or the provider's invalid-key code. Never retried."""


class TtsRequestRejected(TtsError):
    """Any other 4xx except 429. Never retried."""


class TtsRateLimited(TtsError):
    """429 after the last attempt."""


class TtsServerError(TtsError):
    """5xx after the last attempt."""


class TtsTransportError(TtsError):
    """A connect, read or write error or a timeout after the last attempt."""


class TtsResponseInvalid(TtsError):
    """A response that is not the documented success shape. Never retried."""


class TtsCapReachedError(TtsError):
    """The request would cross a spend cap; it was not sent."""


class TtsConfigurationError(TtsError, ValueError):
    """The run is configured so that no request may be sent."""


class TtsKeyMissingError(TtsConfigurationError):
    """The API key is missing, empty or unusable."""


class TtsLanguageNotSupportedError(TtsConfigurationError):
    """The voice's language is not one the engine supports."""


class TtsTextTooLongError(TtsConfigurationError):
    """The text is longer than the engine accepts; it was not sent."""


def _check(pattern: re.Pattern[str], name: str, value: object) -> None:
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f"{name}: not an accepted value")


@dataclass(frozen=True)
class TtsVoice:
    """The voice of a call, exactly as the sim profile states it (ADR 0027 rule 13)."""

    provider: str
    model: str
    speaker: str
    language_code: str
    pace: str

    def __post_init__(self) -> None:
        _check(_NAME, "provider", self.provider)
        _check(_NAME, "model", self.model)
        _check(_NAME, "speaker", self.speaker)
        _check(_LANGUAGE, "language_code", self.language_code)
        _check(_PACE, "pace", self.pace)


@dataclass(frozen=True)
class TtsRequest:
    """One line to synthesise; ``index`` is its position among the lines of the call."""

    index: int
    text: str
    voice: TtsVoice
    sample_rate_hz: int
    codec: str

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("index: not a non-negative integer")
        if type(self.text) is not str or not self.text:
            raise ValueError("text: empty")
        if not isinstance(self.voice, TtsVoice):
            raise TypeError("voice: not a TtsVoice")
        if type(self.sample_rate_hz) is not int or not 8000 <= self.sample_rate_hz <= 48000:
            raise ValueError("sample_rate_hz: outside 8000 to 48000")
        if self.codec not in TTS_CODECS:
            raise ValueError("codec: not an accepted value")


@dataclass(frozen=True)
class TtsContext:
    """What an engine needs besides the request; the engine reads no clock for ``created_at``."""

    run_id: RunId
    created_at: datetime


@dataclass(frozen=True)
class TtsOutcome:
    """Audio or a failure, never both; ``usage`` holds one record per answered request."""

    audio: bytes | None
    usage: tuple[UsageRecord, ...]
    failure: TtsError | None

    def __post_init__(self) -> None:
        if (self.audio is None) == (self.failure is None):
            raise ValueError("an outcome holds audio or a failure, exactly one")
        if self.audio is not None and (type(self.audio) is not bytes or not self.audio):
            raise ValueError("audio: not non-empty bytes")
        for record in self.usage:
            if not isinstance(record, UsageRecord):
                raise TypeError("usage: not a UsageRecord")
            if record.stage != "sim_caller_tts" or record.domain != "evaluation":
                raise ValueError("usage: not a sim_caller_tts record of the evaluation domain")


@runtime_checkable
class TtsEngine(Protocol):
    """One request in, a typed outcome out."""

    def synthesise(self, request: TtsRequest, context: TtsContext) -> TtsOutcome: ...


@runtime_checkable
class UsageStreamingTtsEngine(Protocol):
    """An engine that hands out the usage of each answered request before it returns."""

    streams_usage: bool

    def synthesise(
        self, request: TtsRequest, context: TtsContext, *, on_usage: UsageSink | None = None
    ) -> TtsOutcome: ...


def synthesise_all(
    engine: TtsEngine | UsageStreamingTtsEngine,
    requests: Sequence[TtsRequest],
    context: TtsContext,
    *,
    on_usage: UsageSink,
) -> tuple[TtsOutcome, ...]:
    """Synthesise the requests in order; the usage of request k reaches ``on_usage`` first.

    The next request is not built before the usage of the previous one was handed out (the R1
    lesson, ADR 0024 rule 16 (d)). The run stops after the first failed outcome; that outcome is
    the last one returned.
    """
    outcomes: list[TtsOutcome] = []
    for request in requests:
        if isinstance(engine, UsageStreamingTtsEngine) and engine.streams_usage is True:
            outcome = engine.synthesise(request, context, on_usage=on_usage)
        else:
            outcome = engine.synthesise(request, context)
            for record in outcome.usage:
                on_usage(record)
        outcomes.append(outcome)
        if outcome.failure is not None:
            break
    return tuple(outcomes)
