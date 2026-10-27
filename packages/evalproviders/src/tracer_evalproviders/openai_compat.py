# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The generic chat-completions client of the judge (ADR 0030 rules 12, 16, 17, 18; ADR 0024
amendments A10 and A13).

Tracer's own thin client on ``httpx`` (the ``openai`` SDK is not a dependency): the request is the
subset one profile lists (``judgeprofiles``), the URL is the exact URL of one validated allowlist
entry, the key travels where the profile says and only to that entry, and the reply is read as one
exact shape. Timeouts, bounded attempts, bounded ``Retry-After`` and the error taxonomy are those of
ADR 0024 rules 9 and 10 (``JsonPoster``), with the ``Judge*`` errors. The existing ``SarvamLlm`` is
unchanged. Cost is never computed here: ``usage.cost`` ("Cost in credits") is read nowhere.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

import httpx

from tracer_evalcore import judge as core
from tracer_evalcore.judge import JudgeContext, JudgeOutcome, JudgeRequest
from tracer_evalcore.judgerecords import build_exchange_document
from tracer_evalcore.stt import UsageSink
from tracer_evalproviders import judgeprofiles
from tracer_evalproviders.calllog import safe_request_id
from tracer_evalproviders.sarvam._post import JsonPoster, Reply, read_document
from tracer_evalproviders.sarvam.client import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    RETRY_AFTER_MAX_SECONDS,
    build_http_client,
)
from tracer_evalproviders.secrets import ApiKey
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import UsageRecordId
from tracer_evalschema.usage import UsageRecord

if TYPE_CHECKING:
    from tracer_evalproviders.judgeexchange import ExchangeStore
    from tracer_evalproviders.judgehosts import BoundEndpoint

__all__ = [
    "BACKOFF_SECONDS",
    "JUDGE_MAX_RESPONSE_BYTES",
    "MAX_ATTEMPTS",
    "RETRY_AFTER_MAX_SECONDS",
    "ChatReply",
    "OpenAICompatJudge",
    "build_judge_http_client",
    "create_judge_engine",
    "parse_chat_response",
]

JUDGE_MAX_RESPONSE_BYTES: Final = 65_536
_MAX_SAFE: Final = 2**53 - 1
_ERROR_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")


def build_judge_http_client(transport: httpx.BaseTransport | None) -> httpx.Client:
    """The one HTTP client of the judge: no redirect, no environment, TLS verified (the builder of
    every Sarvam adapter)."""
    return build_http_client(transport)


@dataclass(frozen=True)
class ChatReply:
    """The members of a chat-completions reply that the judge reads."""

    id: str
    model: str
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int | None
    reasoning_tokens: int | None
    system_fingerprint: str | None


def _count(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE:
        return None
    return value


def parse_chat_response(
    body: bytes, *, index: int, attempts: int
) -> ChatReply | core.JudgeResponseInvalid:
    """The reply of a 200 response, or the ``JudgeResponseInvalid`` that names why not."""
    facts: dict[str, Any] = {"index": index, "attempts": attempts, "status": 200}

    def invalid(reason: str, **more: Any) -> core.JudgeResponseInvalid:
        return core.JudgeResponseInvalid(reason=reason, **facts, **more)

    ok, document = read_document(body)
    if not ok:
        return invalid("not_json")
    if not isinstance(document, dict):
        return invalid("not_object")
    reply_id = document.get("id")
    if not isinstance(reply_id, str) or not 1 <= len(reply_id) <= 256:
        return invalid("id_invalid")
    model = document.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 256:
        return invalid("model_invalid")
    choices = document.get("choices")
    if not (isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)):
        return invalid("choices_invalid")
    choice = choices[0]
    error = choice.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        kept = code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None
        if kept is None:
            return invalid("choice_error")
        return invalid("choice_error", code=kept)
    message = choice.get("message")
    if not isinstance(message, dict):
        return invalid("message_invalid")
    content = message.get("content")
    if not isinstance(content, str):
        return invalid("content_invalid")
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not 1 <= len(finish_reason) <= 64:
        return invalid("finish_reason_invalid")
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return invalid("usage_invalid")
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    if prompt is None or completion is None:
        return invalid("usage_invalid")
    cached: int | None = None
    details = usage.get("prompt_tokens_details")
    if details is not None and not isinstance(details, dict):
        return invalid("usage_invalid")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        cached = _count(details["cached_tokens"])
        if cached is None or cached > prompt:
            return invalid("usage_invalid")
    reasoning: int | None = None
    completion_details = usage.get("completion_tokens_details")
    if completion_details is not None and not isinstance(completion_details, dict):
        return invalid("usage_invalid")
    if (
        isinstance(completion_details, dict)
        and completion_details.get("reasoning_tokens") is not None
    ):
        reasoning = _count(completion_details["reasoning_tokens"])
        if reasoning is None:
            return invalid("usage_invalid")
    fingerprint = document.get("system_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or len(fingerprint) > 256):
        return invalid("fingerprint_invalid")
    return ChatReply(
        id=reply_id,
        model=model,
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
        reasoning_tokens=reasoning,
        system_fingerprint=fingerprint,
    )


def _check_cap(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= _MAX_SAFE:
        raise ValueError(f"{name}: must be None or an integer from 1 to 2**53 - 1")
    return value


def _is_loopback_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class OpenAICompatJudge:
    """A usage-streaming judge engine over one profile and one URL."""

    streams_usage = True

    def __init__(
        self,
        api_key: ApiKey | None,
        *,
        profile: judgeprofiles.JudgeProfile,
        url: str,
        call_log: SimCallLog | None,
        max_requests: int | None,
        max_tokens: int | None,
        exchanges: ExchangeStore | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme == "http" and not _is_loopback_literal(parts.hostname or ""):
            raise ValueError("url: plain http is for a literal loopback address only")
        if parts.scheme not in ("http", "https"):
            raise ValueError("url: not an http or https URL")
        if api_key is not None and not isinstance(api_key, ApiKey):
            raise TypeError("api_key must be an ApiKey or None")
        self._profile = profile
        self._max_requests = _check_cap(max_requests, "max_requests")
        self._cap_tokens = _check_cap(max_tokens, "max_tokens")
        self._requests = 0
        self._reported = 0
        self._exchanges = exchanges
        if call_log is None:
            raise ValueError("call_log: required")
        self._poster = JsonPoster(
            api_key,
            url=url,
            stage="judge",
            errors=core,
            prefix="Judge",
            call_log=call_log,
            id_member="id",
            transport=transport,
            sleep=sleep,
            monotonic=monotonic,
            auth_scheme={"bearer": "bearer", "header": "header", "none": "none"}[profile.auth],
            auth_header=profile.auth_header or "api-subscription-key",
        )
        self._key = api_key

    def close(self) -> None:
        self._poster.close()

    def __enter__(self) -> OpenAICompatJudge:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def complete(
        self, request: JudgeRequest, context: JudgeContext, *, on_usage: UsageSink | None = None
    ) -> JudgeOutcome:
        if not isinstance(request, JudgeRequest) or not isinstance(context, JudgeContext):
            raise TypeError("request and context must be records")
        if self._max_requests is not None and self._requests >= self._max_requests:
            return self._refused(request, "judge_requests")
        if (
            self._cap_tokens is not None
            and self._reported + request.params.max_tokens > self._cap_tokens
        ):
            return self._refused(request, "judge_tokens")
        body = judgeprofiles.build_request_body(self._profile, request)
        extra = judgeprofiles.checked_extra_headers(self._profile, request.params.extra_headers)
        self._requests += 1
        reply = self._poster.post(
            index=request.index,
            body=json.dumps(body).encode("utf-8"),
            log_fields={},
            max_bytes=JUDGE_MAX_RESPONSE_BYTES,
            extra_headers=extra,
        )
        if not isinstance(reply, Reply):
            return self._failure(reply)
        parsed = parse_chat_response(reply.body, index=request.index, attempts=reply.attempts)
        if isinstance(parsed, core.JudgeResponseInvalid):
            self._save(request, reply)
            return self._failure(parsed)
        # the usage of an answered request is handed out before the exchange file is written: a
        # file that cannot be written must not lose the record of a paid request
        self._reported += parsed.prompt_tokens + parsed.completion_tokens
        record = self._record(request, context, reply, parsed)
        if on_usage is not None:
            on_usage(record)
        saved = self._save(request, reply)
        return JudgeOutcome(
            text=parsed.content,
            finish_reason=parsed.finish_reason,
            reported_model=parsed.model,
            request_id=reply.request_id,
            exchange_sha256=saved,
            params_sent=judgeprofiles.params_as_sent(self._profile, request),
            usage=(record,),
            failure=None,
        )

    @staticmethod
    def _failure(error: core.JudgeError) -> JudgeOutcome:
        return JudgeOutcome(
            text=None,
            finish_reason=None,
            reported_model=None,
            request_id=None,
            exchange_sha256=None,
            params_sent=None,
            usage=(),
            failure=error,
        )

    def _refused(self, request: JudgeRequest, reason: str) -> JudgeOutcome:
        return self._failure(core.JudgeCapReachedError(index=request.index, reason=reason))

    def _save(self, request: JudgeRequest, reply: Reply) -> str | None:
        if self._exchanges is None:
            return None
        try:
            text = reply.body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        document = build_exchange_document(
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
            response_body=text,
            request_id=reply.request_id,
            status=reply.status,
        )
        return self._exchanges.write(request.item_id, request.attempt, document)

    def _record(
        self, request: JudgeRequest, context: JudgeContext, reply: Reply, parsed: ChatReply
    ) -> UsageRecord:
        cached = parsed.cached_tokens
        quantities: list[dict[str, Any]] = [
            {"unit": "input_tokens", "count": parsed.prompt_tokens - (cached or 0)}
        ]
        if cached is not None:
            quantities.append({"unit": "cache_read_input_tokens", "count": cached})
        quantities += [
            {"unit": "output_tokens", "count": parsed.completion_tokens},
            {"unit": "requests", "count": 1},
        ]
        metadata: dict[str, Any] = {"tracer.request_index": request.index}
        if request.params.seed is not None:
            metadata["tracer.seed"] = request.params.seed
            metadata["tracer.seed_guarantee"] = "none"
        if parsed.reasoning_tokens is not None:
            metadata["tracer.reasoning_tokens"] = parsed.reasoning_tokens
        if parsed.system_fingerprint is not None:
            metadata["tracer.system_fingerprint"] = parsed.system_fingerprint
        document: dict[str, Any] = {
            "id": UsageRecordId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": context.created_at,
            "metadata": metadata,
            "run_id": context.run_id,
            "domain": "evaluation",
            "stage": "judge",
            "provider": self._profile.name,
            "model": request.params.model,
            "quantities": quantities,
            "source": "provider_reported",
            "raw": None,
        }
        call_ref = safe_request_id(parsed.id)
        if call_ref is not None:
            document["provider_call_ref"] = {
                "system": self._profile.name,
                "kind": "request_id",
                "value": call_ref,
            }
        if reply.date is not None:
            document["occurred_at"] = reply.date
        return UsageRecord.model_validate(document)


def create_judge_engine(
    bound: BoundEndpoint,
    *,
    call_log: SimCallLog,
    max_requests: int | None,
    max_tokens: int | None,
    exchanges: ExchangeStore | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> OpenAICompatJudge:
    """The engine of a bound allowlist entry: its URL, its profile, its key; nothing else can be
    chosen here."""
    return OpenAICompatJudge(
        bound.api_key,
        profile=bound.profile,
        url=bound.endpoint.url,
        call_log=call_log,
        max_requests=max_requests,
        max_tokens=max_tokens,
        exchanges=exchanges,
        transport=transport,
        sleep=sleep,
        monotonic=monotonic,
    )
