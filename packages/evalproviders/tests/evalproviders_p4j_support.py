# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch J part 1 evalproviders tests (P4-J12 to P4-J15). Support
module, not collected. Contract: docs/phase4/api-surface-batch-j1.md.

Nothing of the batch J modules is imported at module level (the accessors import lazily), so
before the modules exist each test fails on its own with ``ModuleNotFoundError`` and the directory
still collects. No test reaches a network: requests go to ``ScriptedTransport`` (an
``httpx.MockTransport``) and the key is the canary ``CANARY_KEY``, built from parts, never a real
key. The Marathi texts are invented and spelled with escapes so that this file stays ASCII.
"""

from __future__ import annotations

import importlib
import json
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from evalproviders_phase4_support import Kit  # type: ignore[import-not-found,unused-ignore]
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MESSAGE_CANARY,
    TEXT_CANARY,
    UTC_NOW,
    ScriptedTransport,
    Sleeps,
    Ticker,
    WallClock,
    json_response,
)
from tracer_evalschema.ids import RunId

__all__ = [
    "CANARY_KEY",
    "DATE",
    "MESSAGE_CANARY",
    "PROFILE_URLS",
    "TEXT_CANARY",
    "JudgeKit",
    "JudgeRig",
    "context",
    "golden_item",
    "jmod",
    "judge_kit",
    "openai_reply",
    "params",
    "request",
    "verdict_text",
]

DATE = "Fri, 23 Oct 2026 13:08:26 GMT"
LOOPBACK_URL = "http://127.0.0.1:8123/v1/chat/completions"
#: the URL a real run would take from the owner's allowlist, per profile (ADR 0030 profile table)
PROFILE_URLS: dict[str, str] = {
    "anthropic_compat/1": "https://api.anthropic.com/v1/chat/completions",
    "gemini_compat/1": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "xai/1": "https://api.x.ai/v1/chat/completions",
    "openrouter/1": "https://openrouter.ai/api/v1/chat/completions",
    "sarvam_v2/1": "https://api.sarvam.ai/v2/chat/completions",
    "openai/1": "https://api.openai.com/v1/chat/completions",
    "local_loopback/1": LOOPBACK_URL,
}
#: invented Marathi texts: a question, two expected facts, an answer
QUESTION = "\u092a\u094d\u0930\u0936\u094d\u0928 \u090f\u0915?"
FACTS = ("\u0924\u0925\u094d\u092f \u090f\u0915", "\u0924\u0925\u094d\u092f \u0926\u094b\u0928")
ANSWER = "\u0909\u0924\u094d\u0924\u0930 \u090f\u0915"


def jmod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalproviders.{name}")


def core(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalcore.{name}")


def golden_item(mode: str = "all_facts", **changes: Any) -> Any:
    fields: dict[str, Any] = {
        "id": "mr_q00",
        "scripted_turn_index": 0,
        "question": QUESTION,
        "match_mode": mode,
        "expected_facts": FACTS if mode == "all_facts" else (),
        "expected_answer": None if mode == "all_facts" else "\u0909\u0924\u094d\u0924\u0930",
        "needs_native_review": True,
    }
    fields.update(changes)
    return core("golden").GoldenItem(**fields)


def params(**changes: Any) -> Any:
    fields: dict[str, Any] = {
        "model": "judge-model-1",
        "max_tokens": 256,
        "temperature": None,
        "seed": None,
        "reasoning_effort": None,
        "provider_slug": None,
        "extra_headers": (),
    }
    fields.update(changes)
    return core("judge").JudgeParams(**fields)


def request(
    index: int = 0,
    *,
    attempt: int = 1,
    item: Any = None,
    answer: str = ANSWER,
    **param_changes: Any,
) -> Any:
    chosen = item if item is not None else golden_item()
    messages = core("judgeprompt").build_judge_messages(chosen, answer)
    return core("judge").JudgeRequest(
        index=index,
        item_id=chosen.id,
        attempt=attempt,
        match_mode=chosen.match_mode,
        fact_count=len(chosen.expected_facts),
        messages=messages,
        params=params(**param_changes),
    )


def context() -> Any:
    return core("judge").JudgeContext(run_id=RunId.new(), created_at=UTC_NOW)


def verdict_text(item: Any = None) -> str:
    """The reply of a well-behaved judge that agrees; for the two facts of ``golden_item()`` unless
    ``item`` is given (no product module is imported here, so a module may call it at import)."""
    mode = "all_facts" if item is None else item.match_mode
    count = len(FACTS) if item is None else len(item.expected_facts)
    if mode == "all_facts":
        facts = [{"index": i, "status": "stated"} for i in range(count)]
        document = {"verdict": "agrees", "reason_code": "all_facts_stated", "facts": facts}
    else:
        document = {"verdict": "agrees", "reason_code": "meaning_same", "facts": []}
    return json.dumps(document, separators=(",", ":"))


def openai_reply(
    content: str | None = None,
    *,
    reply_id: str = "chatcmpl-req-1",
    model: str = "judge-model-1-echo",
    prompt: int = 120,
    completion: int = 20,
    cached: int | None = None,
    reasoning: int | None = None,
    fingerprint: str | None = None,
    finish_reason: str = "stop",
    extra: dict[str, Any] | None = None,
    usage_extra: dict[str, Any] | None = None,
    date: str | None = DATE,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """A 200 chat-completions reply in the OpenAI shape (what every profile reads)."""
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    usage.update(usage_extra or {})
    body: dict[str, Any] = {
        "id": reply_id,
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": verdict_text() if content is None else content,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    if fingerprint is not None:
        body["system_fingerprint"] = fingerprint
    body.update(extra or {})
    response: httpx.Response = json_response(body, date=date, headers=headers)
    return response


class JudgeRig:
    """A scripted transport, fake sleep and clocks, a call log and the engine under test."""

    def __init__(
        self,
        tmp_path: Path,
        steps: Sequence[Any],
        *,
        profile: str = "local_loopback/1",
        url: str | None = None,
        key: str | None = None,
        max_requests: int | None = None,
        max_tokens: int | None = None,
        exchanges: Any = None,
        extra_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self.transport = ScriptedTransport(list(steps))
        self.sleeps = Sleeps()
        self.ticker = Ticker()
        self.out = tmp_path / "out"
        self.profile = jmod("judgeprofiles").get_profile(profile)
        self.log = jmod("simcalllog").SimCallLog.open(self.out, clock=WallClock())
        chosen_key = (
            key if key is not None else (CANARY_KEY if profile != "local_loopback/1" else None)
        )
        api_key = None if chosen_key is None else jmod("secrets").ApiKey(chosen_key)
        try:
            self.engine = jmod("openai_compat").OpenAICompatJudge(
                api_key,
                profile=self.profile,
                url=url or PROFILE_URLS[profile],
                call_log=self.log,
                max_requests=max_requests,
                max_tokens=max_tokens,
                exchanges=exchanges,
                transport=self.transport,
                sleep=self.sleeps,
                monotonic=self.ticker,
                **dict(extra_kwargs or {}),
            )
        except BaseException:
            self.log.close()
            raise
        weakref.finalize(self, self.log.close)
        weakref.finalize(self, self.engine.close)

    @property
    def log_lines(self) -> list[dict[str, Any]]:
        text = self.log.path.read_text("ascii")
        return [json.loads(line) for line in text.splitlines()]

    @property
    def log_text(self) -> str:
        text: str = self.log.path.read_text("ascii")
        return text

    def complete(
        self,
        req: Any = None,
        *,
        on_usage: Callable[[Any], None] | None = None,
        ctx: Any = None,
    ) -> Any:
        return self.engine.complete(
            req if req is not None else request(),
            ctx if ctx is not None else context(),
            on_usage=on_usage,
        )

    call = complete

    def body(self, position: int = 0) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.transport.requests[position].content)
        return loaded


@dataclass(frozen=True)
class JudgeKit(Kit):  # type: ignore[misc]
    """The judge as a ``Kit`` of the shared taxonomy scenarios of ADR 0024 (the matrix of
    ``evalproviders_phase4_matrix``): the profile ``sarvam_v2/1`` sends the key in the header
    ``api-subscription-key`` to ``https://api.sarvam.ai/v2/chat/completions``, as the scenarios
    expect, and the errors are the ``Judge*`` family. ``name`` is ``llm`` so that the scenarios read
    the text of the outcome and the size cap of a chat reply."""

    def errors(self) -> ModuleType:
        return core("judge")

    def ok(self, number: int = 0) -> httpx.Response:
        return openai_reply(reply_id=f"judge-ok-{number}")

    def rig(self, tmp_path: Path, steps: Sequence[Any]) -> Any:
        return JudgeRig(tmp_path, steps, profile="sarvam_v2/1")

    def request(self, index: int = 5) -> Any:
        return request(index, reasoning_effort="low")

    def call(self, rig: Any, index: int = 5) -> Any:
        return rig.complete(self.request(index))

    def request_id_member(self) -> str:
        return "id"


judge_kit = JudgeKit("llm", "Judge", "judge", "/v2/chat/completions")
