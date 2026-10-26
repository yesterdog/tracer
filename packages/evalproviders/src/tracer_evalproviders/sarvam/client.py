# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Sarvam HTTP client (ADR 0024 rules 2, 3, 5, 8, 9 and 10).

One chunk per request, a fixed endpoint, bounded deterministic retries, a response size cap and a
typed error for every failure. The key travels in one header. Every error is raised outside any
exception handler, so it has neither ``__cause__`` nor ``__context__``: a library error can quote
the request, and the request holds the key.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Final

import httpx

from tracer_evalcore.audio.chunker import Chunk
from tracer_evalcore.stt import (
    SttAuthError,
    SttConfigurationError,
    SttError,
    SttRateLimited,
    SttRequestRejected,
    SttResponseInvalid,
    SttServerError,
    SttTransportError,
)
from tracer_evalproviders.calllog import CallLog, safe_request_id
from tracer_evalproviders.sarvam.mapping import usage_audio_seconds
from tracer_evalproviders.sarvam.models import (
    DEFAULT_MODEL,
    MAX_RESPONSE_BYTES,
    STT_URL,
    ChunkResponse,
    SarvamRequest,
    SarvamResponseShapeError,
    parse_response,
)
from tracer_evalproviders.secrets import ApiKey

__all__ = [
    "BACKOFF_SECONDS",
    "CONNECT_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "POOL_TIMEOUT_SECONDS",
    "READ_TIMEOUT_SECONDS",
    "RETRY_AFTER_MAX_SECONDS",
    "WRITE_TIMEOUT_SECONDS",
    "SarvamClient",
]

MAX_ATTEMPTS: Final = 4
BACKOFF_SECONDS: Final = (1.0, 2.0, 4.0)
RETRY_AFTER_MAX_SECONDS: Final = 30
CONNECT_TIMEOUT_SECONDS: Final = 10.0
POOL_TIMEOUT_SECONDS: Final = 10.0
READ_TIMEOUT_SECONDS: Final = 60.0
WRITE_TIMEOUT_SECONDS: Final = 60.0

# The request always asks Sarvam to observe the language; see ``language_code_for``.
_CONFIGURED_LANGUAGE: Final = "mr"
_KEY_HEADER: Final = "api-subscription-key"
_MODEL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
_ERROR_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_DELAY_SECONDS: Final = re.compile(r"[0-9]+", re.ASCII)
_UNRECOGNISED: Final = "unrecognised"
_ABOVE_BOUND: Final = RETRY_AFTER_MAX_SECONDS + 1
_TRANSPORT_KINDS: Final = (
    (httpx.ConnectTimeout, "connect_timeout"),
    (httpx.ReadTimeout, "read_timeout"),
    (httpx.WriteTimeout, "write_timeout"),
    (httpx.PoolTimeout, "pool_timeout"),
    (httpx.ConnectError, "connect_error"),
    (httpx.ReadError, "read_error"),
    (httpx.WriteError, "write_error"),
)


def _transport_kind(error: httpx.RequestError) -> str:
    for cls, kind in _TRANSPORT_KINDS:
        if isinstance(error, cls):
            return kind
    return "transport_error"


def _http_date(value: str | None) -> datetime | None:
    """An HTTP date as a UTC instant, or ``None`` for anything else (including no zone)."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _retry_after_seconds(headers: httpx.Headers) -> int | None:
    """The delay the server asks for in whole seconds, or ``None`` when absent or unusable.

    An HTTP date is measured against the ``Date`` of the same response, never the local clock.
    """
    value = headers.get("retry-after")
    if value is None:
        return None
    text = value.strip()
    if _DELAY_SECONDS.fullmatch(text):
        digits = text.lstrip("0")
        return int(digits or "0") if len(digits) <= 9 else _ABOVE_BOUND
    moment = _http_date(text)
    sent = _http_date(headers.get("date"))
    if moment is None or sent is None:
        return None
    return max(0, int((moment - sent).total_seconds()))


def _read_json(body: bytes | None) -> object:
    if body is None:
        return None
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        return None


def _error_facts(headers: httpx.Headers, body: bytes | None) -> tuple[str, str | None]:
    """``(code, request_id)`` of an error response; only values of a safe form are kept."""
    code = _UNRECOGNISED
    request_id: str | None = None
    document = _read_json(body)
    error = document.get("error") if isinstance(document, dict) else None
    if isinstance(error, dict):
        raw_code = error.get("code")
        if isinstance(raw_code, str) and _ERROR_CODE.fullmatch(raw_code):
            code = raw_code
        request_id = safe_request_id(error.get("request_id"))
    if request_id is None:
        request_id = safe_request_id(headers.get("x-request-id"))
    return code, request_id


def _success_request_id(headers: httpx.Headers, body: bytes | None) -> str | None:
    document = _read_json(body)
    found = safe_request_id(document.get("request_id")) if isinstance(document, dict) else None
    return found if found is not None else safe_request_id(headers.get("x-request-id"))


@dataclass
class _Attempt:
    """What one request left: a response (status, headers, body) or a transport kind."""

    status: int | None = None
    headers: httpx.Headers = field(default_factory=httpx.Headers)
    body: bytes | None = None
    oversize: bool = False
    undecodable: bool = False
    transport_kind: str | None = None


class SarvamClient:
    """Sends one chunk at a time to the fixed Sarvam speech-to-text endpoint."""

    def __init__(
        self,
        api_key: ApiKey,
        *,
        call_log: CallLog,
        model: str = DEFAULT_MODEL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(api_key, ApiKey):
            raise TypeError("api_key must be an ApiKey")
        if not isinstance(model, str) or not _MODEL.fullmatch(model):
            raise SttConfigurationError(reason="model_invalid")
        self._key = api_key
        self._model = model
        self._log = call_log
        self._sleep = sleep
        self._monotonic = monotonic
        self._http = httpx.Client(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=WRITE_TIMEOUT_SECONDS,
                pool=POOL_TIMEOUT_SECONDS,
            ),
        )

    @property
    def model(self) -> str:
        return self._model

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SarvamClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def send_chunk(self, chunk: Chunk) -> ChunkResponse:
        """The parsed response of ``chunk``; raises the ``SttError`` that ended its attempts."""
        outcome = self._send(chunk)
        if isinstance(outcome, SttError):
            raise outcome
        return outcome

    def _send(self, chunk: Chunk) -> ChunkResponse | SttError:
        seconds, _ = usage_audio_seconds(chunk.frames, chunk.sample_rate_hz)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result = self._once(chunk, attempt, seconds)
            classified = self._classify(chunk, attempt, result)
            if isinstance(classified, ChunkResponse):
                return classified
            retry, error = classified
            if not retry or attempt == MAX_ATTEMPTS:
                return error
            wait = BACKOFF_SECONDS[attempt - 1]
            if result.status in (429, 503):
                asked = _retry_after_seconds(result.headers)
                if asked is not None and asked > RETRY_AFTER_MAX_SECONDS:
                    return SttRateLimited(
                        chunk_index=chunk.index,
                        status=result.status,
                        code=error.code,
                        request_id=error.request_id,
                        attempts=attempt,
                        reason="retry_after_above_bound",
                    )
                if asked is not None:
                    wait = max(wait, float(asked))
            self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _once(self, chunk: Chunk, attempt: int, seconds: str) -> _Attempt:
        request = self._http.build_request(
            "POST",
            STT_URL,
            headers={_KEY_HEADER: self._key.reveal(), "accept-encoding": "identity"},
            data=SarvamRequest(self._model).text_fields(_CONFIGURED_LANGUAGE),
            files={"file": (f"chunk-{chunk.index}.wav", chunk.wav, "audio/wav")},
        )
        sent = len(request.read())
        self._log.before(
            chunk_index=chunk.index, attempt=attempt, bytes_sent=sent, audio_seconds=seconds
        )
        result = _Attempt()
        started = self._monotonic()
        try:
            self._receive(request, result)
        except httpx.DecodingError:
            result.undecodable = True
        except httpx.RequestError as error:
            result.transport_kind = _transport_kind(error)
        finished = self._monotonic()
        if result.status is None:
            request_id = None
        elif result.status == 200:
            request_id = _success_request_id(result.headers, result.body)
        else:
            request_id = _error_facts(result.headers, result.body)[1]
        self._log.after(
            chunk_index=chunk.index,
            attempt=attempt,
            bytes_sent=sent,
            audio_seconds=seconds,
            status=result.status,
            error_kind=result.transport_kind,
            elapsed_ms=int((finished - started) * 1000),
            request_id=request_id,
        )
        return result

    def _receive(self, request: httpx.Request, result: _Attempt) -> None:
        response = self._http.send(request, stream=True)
        try:
            result.status = response.status_code
            result.headers = response.headers
            buffer = bytearray()
            for piece in response.iter_bytes():
                buffer += piece
                if len(buffer) > MAX_RESPONSE_BYTES:
                    result.oversize = True
                    return
            result.body = bytes(buffer)
        finally:
            response.close()

    def _classify(
        self, chunk: Chunk, attempt: int, result: _Attempt
    ) -> ChunkResponse | tuple[bool, SttError]:
        """A parsed response, or ``(retry, error)``."""
        facts: dict[str, Any] = {"chunk_index": chunk.index, "attempts": attempt}
        if result.transport_kind is not None:
            return True, SttTransportError(kind=result.transport_kind, **facts)
        if result.undecodable:
            return False, SttResponseInvalid(reason="body_undecodable", **facts)
        status = result.status
        if status is None:  # a response was neither read nor failed: not reachable
            return True, SttTransportError(kind="transport_error", **facts)
        if status == 200:
            return self._success(chunk, attempt, result)
        if not 400 <= status < 600:
            return False, SttResponseInvalid(status=status, reason="unexpected_status", **facts)
        code, request_id = _error_facts(result.headers, result.body)
        known: dict[str, Any] = {"status": status, "code": code, "request_id": request_id, **facts}
        if status in (401, 403) or code == "invalid_api_key_error":
            return False, SttAuthError(**known)
        if status == 429:
            return True, SttRateLimited(**known)
        if status >= 500:
            return True, SttServerError(**known)
        return False, SttRequestRejected(**known)

    def _success(
        self, chunk: Chunk, attempt: int, result: _Attempt
    ) -> ChunkResponse | tuple[bool, SttError]:
        facts: dict[str, Any] = {"chunk_index": chunk.index, "attempts": attempt, "status": 200}
        media_type = result.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            return False, SttResponseInvalid(reason="content_type", **facts)
        if result.oversize or result.body is None:
            return False, SttResponseInvalid(reason="body_too_large", **facts)
        reason: str | None = None
        parsed = None
        try:
            parsed = parse_response(result.body)
        except SarvamResponseShapeError as shape:
            reason = shape.reason
        if parsed is None:
            return False, SttResponseInvalid(reason=reason, **facts)
        return ChunkResponse(
            chunk_index=chunk.index,
            response=parsed,
            responded_at=_http_date(result.headers.get("date")),
            attempts=attempt,
        )
