# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Sarvam request and response models (ADR 0024 rules 3, 6 and 8)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, NoReturn

from tracer_evalcore.stt import SttLanguageNotSupportedError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "API_BASE_URL",
    "CHAT_PATH",
    "CHAT_URL",
    "DEFAULT_MODEL",
    "FORM_FIELD_NAMES",
    "LLM_MAX_REPLY_BYTES",
    "MAX_RESPONSE_BYTES",
    "STT_PATH",
    "STT_URL",
    "TTS_LANGUAGE",
    "TTS_MAX_AUDIO_SECONDS",
    "TTS_MAX_TEXT_CHARS",
    "TTS_PATH",
    "TTS_URL",
    "ChunkResponse",
    "SarvamPhrase",
    "SarvamRequest",
    "SarvamResponseShapeError",
    "SarvamSttResponse",
    "language_code_for",
    "parse_response",
    "tts_max_audio_bytes",
    "tts_max_response_bytes",
]

API_BASE_URL: Final = "https://api.sarvam.ai"
STT_PATH: Final = "/speech-to-text"
STT_URL: Final = API_BASE_URL + STT_PATH
DEFAULT_MODEL: Final = "saaras:v4"
TTS_PATH: Final = "/text-to-speech"
TTS_URL: Final = API_BASE_URL + TTS_PATH
CHAT_PATH: Final = "/v2/chat/completions"
CHAT_URL: Final = API_BASE_URL + CHAT_PATH
TTS_LANGUAGE: Final = "mr-IN"
TTS_MAX_TEXT_CHARS: Final = 2500
TTS_MAX_AUDIO_SECONDS: Final = 600
LLM_MAX_REPLY_BYTES: Final = 65_536
FORM_FIELD_NAMES: Final = ("file", "language_code", "model", "with_timestamps")
MAX_RESPONSE_BYTES: Final = 1_048_576

_UNKNOWN: Final = "unknown"
# Bounds on a number's size: a decimal such as 1E-999999999 is finite but cannot be used.
_MAX_FRACTION_DIGITS: Final = 100
_MAX_ADJUSTED_EXPONENT: Final = 9


def language_code_for(configured: str) -> str:
    """The ``language_code`` form value for a configured language.

    Marathi is sent as ``unknown`` so that the engine observes the language; every other value is
    refused (BL-162).
    """
    if configured != "mr":
        raise SttLanguageNotSupportedError(reason="language_not_supported")
    return _UNKNOWN


@dataclass(frozen=True)
class SarvamRequest:
    model: str

    def text_fields(self, configured_language: str) -> dict[str, str]:
        return {
            "language_code": language_code_for(configured_language),
            "model": self.model,
            "with_timestamps": "true",
        }


class SarvamResponseShapeError(ValueError):
    """A 200 body that is not the documented shape; ``reason`` is a short key, never content."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SarvamPhrase:
    text: str = field(repr=False)
    start_seconds: Decimal
    end_seconds: Decimal


@dataclass(frozen=True)
class SarvamSttResponse:
    request_id: str
    transcript: str = field(repr=False)
    language_code: str | None
    language_probability: Decimal | None
    phrases: tuple[SarvamPhrase, ...] | None


@dataclass(frozen=True)
class ChunkResponse:
    chunk_index: int
    response: SarvamSttResponse
    responded_at: datetime | None
    attempts: int


def _no_constant(name: str) -> NoReturn:
    raise ValueError(name)


def _has_lone_surrogate(value: Any) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if isinstance(value, dict):
        return any(_has_lone_surrogate(k) or _has_lone_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_lone_surrogate(item) for item in value)
    return False


def _read_document(body: bytes) -> Any:
    text = body.decode("utf-8")
    loads_strict(text)  # duplicates, NaN and Infinity, depth
    # Numbers are read from their JSON text as Decimal, never through a float.
    document = json.loads(text, parse_float=Decimal, parse_int=Decimal, parse_constant=_no_constant)
    if _has_lone_surrogate(document):
        raise ValueError("lone surrogate")
    return document


def _try_read(body: bytes) -> tuple[bool, Any]:
    try:
        return True, _read_document(body)
    except Exception:
        return False, None


def _bounded(value: Any) -> Decimal | None:
    """A finite, usable decimal (JSON numbers only), or ``None``."""
    if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite():
        return None
    exponent = value.as_tuple().exponent
    if (
        not isinstance(exponent, int)
        or exponent < -_MAX_FRACTION_DIGITS
        or value.adjusted() > _MAX_ADJUSTED_EXPONENT
    ):
        return None
    return value


def parse_response(body: bytes) -> SarvamSttResponse:
    """Parse a 200 body; any failure is one ``SarvamResponseShapeError``."""
    readable, document = _try_read(body)
    if not readable:
        raise SarvamResponseShapeError("not_json")
    if not isinstance(document, dict):
        raise SarvamResponseShapeError("not_object")
    request_id = document.get("request_id")
    if not isinstance(request_id, str):
        raise SarvamResponseShapeError("request_id_invalid")
    transcript = document.get("transcript")
    if not isinstance(transcript, str):
        raise SarvamResponseShapeError("transcript_invalid")
    language = document.get("language_code")
    if language is not None and not isinstance(language, str):
        raise SarvamResponseShapeError("language_code_invalid")
    probability: Decimal | None = None
    if "language_probability" in document:
        probability = _bounded(document["language_probability"])
        if probability is None or not Decimal(0) <= probability <= Decimal(1):
            raise SarvamResponseShapeError("language_probability_invalid")
    phrases: tuple[SarvamPhrase, ...] | None = None
    if "timestamps" in document:
        phrases = _phrases(document["timestamps"])
    return SarvamSttResponse(request_id, transcript, language, probability, phrases)


def _phrases(stamps: Any) -> tuple[SarvamPhrase, ...]:
    if not isinstance(stamps, dict):
        raise SarvamResponseShapeError("timestamps_invalid")
    words = stamps.get("words")
    starts = stamps.get("start_time_seconds")
    ends = stamps.get("end_time_seconds")
    if not (isinstance(words, list) and isinstance(starts, list) and isinstance(ends, list)):
        raise SarvamResponseShapeError("timestamps_invalid")
    if not len(words) == len(starts) == len(ends):
        raise SarvamResponseShapeError("timestamps_invalid")
    phrases: list[SarvamPhrase] = []
    for word, raw_start, raw_end in zip(words, starts, ends, strict=True):
        start = _bounded(raw_start)
        end = _bounded(raw_end)
        if not isinstance(word, str) or start is None or end is None:
            raise SarvamResponseShapeError("timestamps_invalid")
        if start < 0 or end < start:
            raise SarvamResponseShapeError("timestamps_invalid")
        phrases.append(SarvamPhrase(word, start, end))
    return tuple(phrases)


def tts_max_audio_bytes(sample_rate_hz: int, codec: str) -> int:
    """The most decoded audio bytes a reply may hold: 600 s of the requested rate."""
    if codec == "linear16":
        width = 2
    elif codec == "mulaw":
        width = 1
    else:
        raise ValueError("codec: not an accepted value")
    return sample_rate_hz * width * TTS_MAX_AUDIO_SECONDS


def tts_max_response_bytes(sample_rate_hz: int, codec: str) -> int:
    """The most JSON body bytes read: the base64 length of the audio cap plus 4,096."""
    return 4 * -(-tts_max_audio_bytes(sample_rate_hz, codec) // 3) + 4096
