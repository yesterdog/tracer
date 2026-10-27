# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Sarvam chat completions v2 adapter (ADR 0024 amendments A1, A3, A7)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, ClassVar, Final

import httpx

from tracer_evalcore import llm as core
from tracer_evalcore.llm import LlmContext, LlmOutcome, LlmRequest
from tracer_evalcore.stt import UsageSink
from tracer_evalproviders.sarvam._post import JsonPoster, Reply, read_document
from tracer_evalproviders.sarvam.models import CHAT_URL, LLM_MAX_REPLY_BYTES, MAX_RESPONSE_BYTES
from tracer_evalproviders.secrets import ApiKey, load_role_key
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import UsageRecordId
from tracer_evalschema.usage import UsageRecord

__all__ = ["SEED_STATEMENT", "SarvamLlm", "create_sarvam_llm"]

PROVIDER: Final = "sarvam"
SEED_STATEMENT: Final = "replies may differ between requests, even with the same seed"
_MAX_SAFE: Final = 2**53 - 1


def _check_cap(cap: object) -> int | None:
    if cap is None:
        return None
    if type(cap) is not int or not 0 <= cap <= _MAX_SAFE:
        raise ValueError("cap must be None or an integer from 0 to 2**53 - 1")
    return cap


def _count(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE:
        return None
    return value


class SarvamLlm:
    provider = "sarvam"
    streams_usage: ClassVar[bool] = True

    def __init__(
        self,
        api_key: ApiKey,
        *,
        call_log: SimCallLog,
        cap_tokens: int | None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(api_key, ApiKey):
            raise TypeError("api_key must be an ApiKey")
        self._cap = _check_cap(cap_tokens)
        self._reported = 0
        self._post = JsonPoster(
            api_key,
            url=CHAT_URL,
            stage="llm",
            errors=core,
            prefix="Llm",
            call_log=call_log,
            id_member="id",
            transport=transport,
            sleep=sleep,
            monotonic=monotonic,
        )

    def close(self) -> None:
        self._post.close()

    def __enter__(self) -> SarvamLlm:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def complete(
        self, request: LlmRequest, context: LlmContext, *, on_usage: UsageSink | None = None
    ) -> LlmOutcome:
        if not isinstance(request, LlmRequest) or not isinstance(context, LlmContext):
            raise TypeError("request and context must be records")
        if self._cap is not None and self._reported + request.max_tokens > self._cap:
            return LlmOutcome(
                text=None,
                usage=(),
                failure=core.LlmCapReachedError(index=request.index, reason="llm_tokens"),
            )
        # UNVERIFIED: streaming is assumed off by default, so no "stream" member is sent
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_tokens,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        reply = self._post.post(
            index=request.index,
            body=json.dumps(payload).encode("utf-8"),
            log_fields={},
            max_bytes=MAX_RESPONSE_BYTES,
        )
        if not isinstance(reply, Reply):
            return LlmOutcome(text=None, usage=(), failure=reply)
        parsed = self._parse(request, reply)
        if isinstance(parsed, core.LlmError):
            return LlmOutcome(text=None, usage=(), failure=parsed)
        text, reply_id, prompt, completion, cached = parsed
        self._reported += prompt + completion
        record = self._record(request, context, reply, reply_id, prompt, completion, cached)
        if on_usage is not None:
            on_usage(record)
        return LlmOutcome(text=text, usage=(record,), failure=None)

    def _parse(
        self, request: LlmRequest, reply: Reply
    ) -> tuple[str, str, int, int, int | None] | core.LlmError:
        facts: dict[str, Any] = {"index": request.index, "attempts": reply.attempts, "status": 200}

        def invalid(reason: str) -> core.LlmError:
            return core.LlmResponseInvalid(reason=reason, **facts)

        ok, document = read_document(reply.body)
        if not ok:
            return invalid("not_json")
        if not isinstance(document, dict):
            return invalid("not_object")
        reply_id = document.get("id")
        if not isinstance(reply_id, str) or not 1 <= len(reply_id) <= 256:
            return invalid("id_invalid")
        choices = document.get("choices")
        if not (isinstance(choices, list) and len(choices) == 1):
            return invalid("choices_invalid")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return invalid("message_invalid")
        content = message.get("content")
        if not isinstance(content, str):
            return invalid("content_invalid")
        if len(content.encode("utf-8")) > LLM_MAX_REPLY_BYTES:
            return invalid("reply_too_large")
        usage = document.get("usage")
        if not isinstance(usage, dict):
            return invalid("usage_invalid")
        prompt = _count(usage.get("prompt_tokens"))
        completion = _count(usage.get("completion_tokens"))
        if prompt is None or completion is None:
            return invalid("usage_invalid")
        cached: int | None = None
        details = usage.get("prompt_tokens_details")
        # UNVERIFIED: the place of the cached-token count, assumed as prompt_tokens_details
        if isinstance(details, dict) and "cached_tokens" in details:
            cached = _count(details["cached_tokens"])
            if cached is None or cached > prompt:
                return invalid("usage_invalid")
        return content, reply_id, prompt, completion, cached

    def _record(
        self,
        request: LlmRequest,
        context: LlmContext,
        reply: Reply,
        reply_id: str,
        prompt: int,
        completion: int,
        cached: int | None,
    ) -> UsageRecord:
        quantities: list[dict[str, Any]] = [
            {"unit": "input_tokens", "count": prompt - (cached or 0)}
        ]
        if cached is not None:
            quantities.append({"unit": "cache_read_input_tokens", "count": cached})
        quantities += [
            {"unit": "output_tokens", "count": completion},
            {"unit": "requests", "count": 1},
        ]
        metadata: dict[str, Any] = {"tracer.request_index": request.index}
        if request.seed is not None:
            metadata["tracer.seed"] = request.seed
            metadata["tracer.seed_guarantee"] = "none"
        if request.reasoning_effort is not None:
            metadata["tracer.reasoning_effort"] = request.reasoning_effort
        document: dict[str, Any] = {
            "id": UsageRecordId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": context.created_at,
            "metadata": metadata,
            "run_id": context.run_id,
            "domain": "evaluation",
            "stage": "sim_caller_llm",
            "provider": PROVIDER,
            "model": request.model,
            "provider_call_ref": {"system": PROVIDER, "kind": "request_id", "value": reply_id},
            "quantities": quantities,
            "source": "provider_reported",
            "raw": None,
        }
        if reply.date is not None:
            document["occurred_at"] = reply.date
        return UsageRecord.model_validate(document)


def create_sarvam_llm(
    *,
    call_log: SimCallLog,
    cap_tokens: int | None,
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SarvamLlm:
    key = load_role_key("llm", environ=environ, env_file=env_file)
    return SarvamLlm(
        key,
        call_log=call_log,
        cap_tokens=cap_tokens,
        transport=transport,
        sleep=sleep,
        monotonic=monotonic,
    )
