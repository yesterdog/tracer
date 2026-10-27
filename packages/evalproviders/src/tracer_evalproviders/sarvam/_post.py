# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""One JSON POST with the retry, size and error rules of ADR 0024."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import httpx

from tracer_evalproviders.calllog import safe_request_id
from tracer_evalproviders.sarvam.client import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    RETRY_AFTER_MAX_SECONDS,
    _Attempt,
    _error_facts,
    _http_date,
    _retry_after_seconds,
    _transport_kind,
    build_http_client,
)
from tracer_evalproviders.secrets import ApiKey
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalschema.loader import loads_strict

__all__ = ["JsonPoster", "Reply", "read_document"]

_KEY_HEADER = "api-subscription-key"  # UNVERIFIED for TTS and chat v2: assumed as for STT


def _has_lone_surrogate(value: Any) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if isinstance(value, dict):
        return any(_has_lone_surrogate(k) or _has_lone_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_lone_surrogate(item) for item in value)
    return False


def read_document(body: bytes) -> tuple[bool, Any]:
    """``(True, document)`` or ``(False, None)``: strict JSON, no lone surrogate."""
    try:
        text = body.decode("utf-8")
        loads_strict(text)
        document = json.loads(text)
        if _has_lone_surrogate(document):
            return False, None
    except Exception:
        return False, None
    return True, document


@dataclass(frozen=True)
class Reply:
    body: bytes
    date: Any
    attempts: int


class JsonPoster:
    def __init__(
        self,
        api_key: ApiKey,
        *,
        url: str,
        stage: str,
        errors: ModuleType,
        prefix: str,
        call_log: SimCallLog,
        id_member: str,
        transport: httpx.BaseTransport | None,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> None:
        self._key = api_key
        self._url = url
        self._stage = stage
        self._errors = errors
        self._prefix = prefix
        self._log = call_log
        self._id_member = id_member
        self._sleep = sleep
        self._monotonic = monotonic
        self._http = build_http_client(transport)

    def close(self) -> None:
        self._http.close()

    def _err(self, suffix: str, **facts: Any) -> Any:
        return getattr(self._errors, self._prefix + suffix)(**facts)

    def post(
        self,
        *,
        index: int,
        body: bytes,
        log_fields: dict[str, Any],
        max_bytes: int,
    ) -> Reply | Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result = self._once(index, attempt, body, log_fields, max_bytes)
            classified = self._classify(index, attempt, result)
            if isinstance(classified, Reply):
                return classified
            retry, error = classified
            if not retry or attempt == MAX_ATTEMPTS:
                return error
            wait = BACKOFF_SECONDS[attempt - 1]
            if result.status in (429, 503):
                asked = _retry_after_seconds(result.headers)
                if asked is not None and asked > RETRY_AFTER_MAX_SECONDS:
                    return self._err(
                        "RateLimited",
                        index=index,
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

    def _once(
        self, index: int, attempt: int, body: bytes, log_fields: dict[str, Any], max_bytes: int
    ) -> _Attempt:
        request = self._http.build_request(
            "POST",
            self._url,
            headers={
                _KEY_HEADER: self._key.reveal(),
                "accept-encoding": "identity",
                "content-type": "application/json",
            },
            content=body,
        )
        sent = len(request.read())
        self._log.before(
            stage=self._stage, index=index, attempt=attempt, bytes_sent=sent, **log_fields
        )
        result = _Attempt()
        started = self._monotonic()
        try:
            self._receive(request, result, max_bytes)
        except httpx.DecodingError:
            result.undecodable = True
        except httpx.RequestError as error:
            result.transport_kind = _transport_kind(error)
        finished = self._monotonic()
        if result.status is None:
            request_id = None
        elif result.status == 200:
            request_id = self._success_id(result)
        else:
            request_id = _error_facts(result.headers, result.body)[1]
        self._log.after(
            stage=self._stage,
            index=index,
            attempt=attempt,
            bytes_sent=sent,
            status=result.status,
            error_kind=result.transport_kind,
            elapsed_ms=int((finished - started) * 1000),
            request_id=request_id,
            **log_fields,
        )
        return result

    def _success_id(self, result: _Attempt) -> str | None:
        found = None
        if result.body is not None:
            try:
                document = json.loads(result.body)
            except (ValueError, RecursionError):
                document = None
            if isinstance(document, dict):
                found = safe_request_id(document.get(self._id_member))
        return found if found is not None else safe_request_id(result.headers.get("x-request-id"))

    def _receive(self, request: httpx.Request, result: _Attempt, max_bytes: int) -> None:
        response = self._http.send(request, stream=True)
        try:
            result.status = response.status_code
            result.headers = response.headers
            buffer = bytearray()
            for piece in response.iter_bytes():
                buffer += piece
                if len(buffer) > max_bytes:
                    result.oversize = True
                    return
            result.body = bytes(buffer)
        finally:
            response.close()

    def _classify(self, index: int, attempt: int, result: _Attempt) -> Reply | tuple[bool, Any]:
        facts: dict[str, Any] = {"index": index, "attempts": attempt}
        if result.transport_kind is not None:
            return True, self._err("TransportError", kind=result.transport_kind, **facts)
        if result.undecodable:
            return False, self._err("ResponseInvalid", reason="body_undecodable", **facts)
        status = result.status
        if status is None:
            return True, self._err("TransportError", kind="transport_error", **facts)
        if status == 200:
            media_type = result.headers.get("content-type", "").split(";")[0].strip().lower()
            if media_type != "application/json":
                return False, self._err(
                    "ResponseInvalid", reason="content_type", status=200, **facts
                )
            if result.oversize or result.body is None:
                return False, self._err(
                    "ResponseInvalid", reason="body_too_large", status=200, **facts
                )
            return Reply(result.body, _http_date(result.headers.get("date")), attempt)
        if not 400 <= status < 600:
            return False, self._err(
                "ResponseInvalid", status=status, reason="unexpected_status", **facts
            )
        # UNVERIFIED for TTS and chat v2: the error body shape is assumed as for STT
        code, request_id = _error_facts(result.headers, result.body)
        known: dict[str, Any] = {"status": status, "code": code, "request_id": request_id, **facts}
        if status in (401, 403) or code == "invalid_api_key_error":
            return False, self._err("AuthError", **known)
        if status == 429:
            return True, self._err("RateLimited", **known)
        if status >= 500:
            return True, self._err("ServerError", **known)
        return False, self._err("RequestRejected", **known)
