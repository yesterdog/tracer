# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Sarvam text-to-speech adapter (ADR 0024 amendments A1, A2, A7, A9)."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import struct
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from types import TracebackType
from typing import Any, ClassVar, Final

import httpx

from tracer_evalcore import tts as core
from tracer_evalcore.stt import UsageSink
from tracer_evalcore.tts import (
    TtsContext,
    TtsOutcome,
    TtsRequest,
)
from tracer_evalproviders.sarvam._post import JsonPoster, Reply, read_document
from tracer_evalproviders.sarvam.models import (
    TTS_LANGUAGE,
    TTS_MAX_TEXT_CHARS,
    TTS_URL,
    tts_max_audio_bytes,
    tts_max_response_bytes,
)
from tracer_evalproviders.secrets import ApiKey, load_role_key
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalproviders.ttscache import TtsCache, tts_cache_key
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import UsageRecordId
from tracer_evalschema.usage import UsageRecord

__all__ = ["SarvamTts", "create_sarvam_tts"]

PROVIDER: Final = "sarvam"
_MAX_SAFE: Final = 2**53 - 1
_B64: Final = re.compile(rb"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_RIFF_HEAD: Final = 12
_CHUNK = struct.Struct("<4sI")
_FMT = struct.Struct("<HHIIHH")
_STREAMED: Final = 0xFFFFFFFF
#: the codec named in the request body for each domain codec (``linear16`` was seen to return
#: headerless PCM, so the documented ``wav`` is asked for and its container is read)
_WIRE_CODEC: Final = {"linear16": "wav", "mulaw": "mulaw"}


def _check_cap(cap: object) -> int | None:
    if cap is None:
        return None
    if type(cap) is not int or not 0 <= cap <= _MAX_SAFE:
        raise ValueError("cap must be None or an integer from 0 to 2**53 - 1")
    return cap


def _wav_data(raw: bytes, rate: int, codec: str) -> bytes | str:
    """The data of a RIFF/WAVE reply, or the reason key.

    UNVERIFIED: for ``mulaw`` the container is assumed to be a WAV too (format tag 7, 8 bits); for
    ``linear16`` the request asks for ``wav`` (the documented default) because the codec named
    ``linear16`` was observed to return headerless PCM. Anything that is not a well-formed WAV of
    the requested rate, one channel and the expected sample format fails closed.
    """
    if len(raw) < _RIFF_HEAD or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return "audio_not_recognised"
    fmt: tuple[int, ...] | None = None
    data: bytes | None = None
    at = _RIFF_HEAD
    end = len(raw)
    while at < end:
        if end - at < _CHUNK.size:
            return "audio_not_recognised"
        tag, size = _CHUNK.unpack_from(raw, at)
        body = at + _CHUNK.size
        if tag == b"data" and data is None and (size == _STREAMED or (size == 0 and body < end)):
            data = raw[body:]  # a streamed size: the rest of the buffer is the audio
            if len(data) % 2 and codec == "linear16":
                return "audio_not_recognised"
            break
        if size > end - body:
            return "audio_not_recognised"
        if tag == b"fmt ":
            if fmt is not None or size not in (16, 18):
                return "audio_not_recognised"
            fmt = _FMT.unpack_from(raw, body)
        elif tag == b"data":
            if data is not None:
                return "audio_not_recognised"
            data = raw[body : body + size]
        at = min(body + size + (size & 1), end)  # an odd chunk is padded; a missing last pad is ok
    if fmt is None or data is None:
        return "audio_not_recognised"
    tag_id, chans, hz, _byte_rate, _block, bits = fmt
    expected = (1, 16) if codec == "linear16" else (7, 8)
    if (tag_id, bits) != expected or chans != 1 or hz != rate:
        return "audio_format_mismatch"
    if not data:
        return "audio_empty"
    if codec == "linear16" and len(data) % 2:
        return "audio_not_recognised"
    return data


class SarvamTts:
    provider = "sarvam"
    streams_usage: ClassVar[bool] = True

    def __init__(
        self,
        api_key: ApiKey,
        *,
        call_log: SimCallLog,
        cap_characters: int | None,
        cache: TtsCache | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(api_key, ApiKey):
            raise TypeError("api_key must be an ApiKey")
        self._cap = _check_cap(cap_characters)
        self._used = 0
        self._cache = cache
        self._post = JsonPoster(
            api_key,
            url=TTS_URL,
            stage="tts",
            errors=core,
            prefix="Tts",
            call_log=call_log,
            id_member="request_id",
            transport=transport,
            sleep=sleep,
            monotonic=monotonic,
        )

    def close(self) -> None:
        self._post.close()

    def __enter__(self) -> SarvamTts:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def synthesise(
        self, request: TtsRequest, context: TtsContext, *, on_usage: UsageSink | None = None
    ) -> TtsOutcome:
        if not isinstance(request, TtsRequest) or not isinstance(context, TtsContext):
            raise TypeError("request and context must be records")
        voice = request.voice
        if voice.language_code != TTS_LANGUAGE:
            raise core.TtsLanguageNotSupportedError(
                index=request.index, reason="language_not_supported"
            )
        if len(request.text) > TTS_MAX_TEXT_CHARS:
            raise core.TtsTextTooLongError(index=request.index, reason="text_too_long")
        audio_cap = tts_max_audio_bytes(request.sample_rate_hz, request.codec)
        key = None
        if self._cache is not None:
            key = tts_cache_key(
                provider=voice.provider,
                model=voice.model,
                speaker=voice.speaker,
                language_code=voice.language_code,
                sample_rate_hz=request.sample_rate_hz,
                codec=request.codec,
                pace=voice.pace,
                text=request.text,
            )
            hit = self._cache.get(key)
            if hit is not None:
                valid = bool(hit) and len(hit) <= audio_cap
                if request.codec == "linear16" and len(hit) % 2:
                    valid = False
                if not valid:
                    return TtsOutcome(
                        audio=None,
                        usage=(),
                        failure=core.TtsResponseInvalid(
                            index=request.index, reason="cache_entry_invalid"
                        ),
                    )
                return TtsOutcome(audio=hit, usage=(), failure=None)
        if self._cap is not None and self._used + len(request.text) > self._cap:
            return TtsOutcome(
                audio=None,
                usage=(),
                failure=core.TtsCapReachedError(index=request.index, reason="tts_chars"),
            )
        self._used += len(request.text)
        body = json.dumps(
            {
                "text": request.text,
                "language_code": voice.language_code,
                "speaker": voice.speaker,
                "model": voice.model,
                "pace": Decimal(voice.pace),
                "speech_sample_rate": request.sample_rate_hz,
                "output_audio_codec": _WIRE_CODEC[request.codec],
            },
            default=_pace_json_default,
        ).encode("utf-8")
        reply = self._post.post(
            index=request.index,
            body=body,
            log_fields={"characters": len(request.text)},
            max_bytes=tts_max_response_bytes(request.sample_rate_hz, request.codec),
        )
        if not isinstance(reply, Reply):
            return TtsOutcome(audio=None, usage=(), failure=reply)
        parsed = self._parse(request, reply, audio_cap)
        if isinstance(parsed, core.TtsError):
            return TtsOutcome(audio=None, usage=(), failure=parsed)
        audio, request_id = parsed
        if self._cache is not None and key is not None:
            self._cache.put(key, audio)
        record = self._record(request, context, reply, request_id)
        if on_usage is not None:
            on_usage(record)
        return TtsOutcome(audio=audio, usage=(record,), failure=None)

    def _parse(
        self, request: TtsRequest, reply: Reply, audio_cap: int
    ) -> tuple[bytes, str] | core.TtsError:
        facts: dict[str, Any] = {"index": request.index, "attempts": reply.attempts, "status": 200}

        def invalid(reason: str) -> core.TtsError:
            return core.TtsResponseInvalid(reason=reason, **facts)

        ok, document = read_document(reply.body)
        if not ok:
            return invalid("not_json")
        if not isinstance(document, dict):
            return invalid("not_object")
        request_id = document.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
            return invalid("request_id_invalid")
        audios = document.get("audios")
        if not (isinstance(audios, list) and len(audios) == 1 and isinstance(audios[0], str)):
            return invalid("audios_invalid")
        text = audios[0]
        if text == "":
            return invalid("audio_empty")
        raw: bytes | None
        try:
            encoded = text.encode("ascii")
            # the same grammar on every Python: 3.11 accepts excess padding under validate=True
            raw = base64.b64decode(encoded, validate=True) if _B64.fullmatch(encoded) else None
        except (ValueError, binascii.Error):
            raw = None
        if raw is None:
            return invalid("audio_not_base64")
        if not raw:
            return invalid("audio_empty")
        if len(raw) > audio_cap:
            return invalid("audio_too_large")
        data = _wav_data(raw, request.sample_rate_hz, request.codec)
        if isinstance(data, str):
            return invalid(data)
        return data, request_id

    def _record(
        self, request: TtsRequest, context: TtsContext, reply: Reply, request_id: str
    ) -> UsageRecord:
        document: dict[str, Any] = {
            "id": UsageRecordId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": context.created_at,
            "metadata": {
                "tracer.request_index": request.index,
                "tracer.model_basis": "requested_not_echoed",
            },
            "run_id": context.run_id,
            "domain": "evaluation",
            "stage": "sim_caller_tts",
            "provider": PROVIDER,
            "model": request.voice.model,
            "provider_call_ref": {"system": PROVIDER, "kind": "request_id", "value": request_id},
            "quantities": [
                {"unit": "input_characters", "count": len(request.text)},
                {"unit": "requests", "count": 1},
            ],
            "source": "measured",
            "raw": None,
        }
        if reply.date is not None:
            document["occurred_at"] = reply.date
        return UsageRecord.model_validate(document)


def _pace_json_default(value: object) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError("not serialisable")


def create_sarvam_tts(
    *,
    call_log: SimCallLog,
    cap_characters: int | None,
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
    cache: TtsCache | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SarvamTts:
    key = load_role_key("tts", environ=environ, env_file=env_file)
    return SarvamTts(
        key,
        call_log=call_log,
        cap_characters=cap_characters,
        cache=cache,
        transport=transport,
        sleep=sleep,
        monotonic=monotonic,
    )
