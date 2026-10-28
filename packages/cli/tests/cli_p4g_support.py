# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch G cli tests (P4-31 target safety, BL-338 ``brain_failed``).
Support module, not collected. Contract: docs/phase4/api-surface-batch-g.md.

It builds on ``cli_p4e_support`` (the in-process run of ``tracer simulate``, the virtual clock, the
fake agent, the scripted Sarvam and the canary key) and ``cli_p4f_support``. Nothing here opens a
socket, reads a key from the machine or calls a provider: the provider is an ``httpx.MockTransport``
and a failure of it is a scripted response or a scripted ``httpx`` exception. Secrets are canaries
assembled from parts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    Files,
    SarvamSim,
    VirtualClock,
    fake_connect,
    llm_caller,
    profile_doc,
    real_args,
    write_inputs,
)

__all__ = [
    "KILL_SWITCH_NAME",
    "LOCK_NAME",
    "PROVIDER_MESSAGE_CANARY",
    "ChatSim",
    "kill_at",
    "llm_args",
    "llm_files",
]

#: The two file names of the guard (contract section 3.1); the unit tests pin the constants.
KILL_SWITCH_NAME = "kill-switch"
LOCK_NAME = "call.lock"

#: Text a provider might put in an error body; it must reach no output.
PROVIDER_MESSAGE_CANARY = "PROVIDERSAID" + "-" + "".join(["q9", "z4", "k2"])

FAILURES = (
    "401",
    "403",
    "400",
    "429",
    "500",
    "503",
    "transport",
    "timeout",
    "bad_json",
    "no_choices",
)


class ChatSim(SarvamSim):  # type: ignore[misc,unused-ignore]
    """The scripted Sarvam of the batch E tests whose chat endpoint answers its first ``good``
    requests normally (the scripted ``chat`` lines) and then fails in the way ``failure`` says.
    A failure applies to every attempt of the client, retries included. Every request is kept in
    ``requests`` (a raised ``httpx`` error is recorded too)."""

    def __init__(
        self,
        failure: str,
        *,
        good: int = 0,
        chat: Sequence[str] = (),
        stt: Sequence[str] | None = None,
    ) -> None:
        assert failure in FAILURES, failure
        kwargs: dict[str, Any] = {"chat": chat}
        if stt is not None:
            kwargs["stt"] = stt
        super().__init__(**kwargs)
        self.failure = failure
        self.good = good
        self.answered = 0
        self.chat_attempts = 0

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v2/chat/completions" or self.answered < self.good:
            if request.url.path == "/v2/chat/completions":
                self.answered += 1
            response: httpx.Response = super()._handle(request)
            return response
        self.requests.append(request)
        self.paths.append(request.url.path)
        self.chat_attempts += 1
        headers = {"content-type": "application/json", "x-provider-note": PROVIDER_MESSAGE_CANARY}
        body = {
            "error": {
                "message": f"{PROVIDER_MESSAGE_CANARY} key {KEY_CANARY} is not accepted",
                "code": "invalid_api_key",
            }
        }
        if self.failure == "transport":
            raise httpx.ConnectError(PROVIDER_MESSAGE_CANARY, request=request)
        if self.failure == "timeout":
            raise httpx.ReadTimeout(PROVIDER_MESSAGE_CANARY, request=request)
        if self.failure == "bad_json":
            return httpx.Response(200, headers=headers, content=PROVIDER_MESSAGE_CANARY.encode())
        if self.failure == "no_choices":
            return httpx.Response(200, headers=headers, json={"id": "chat-x", "choices": []})
        return httpx.Response(int(self.failure), headers=headers, json=body)


def llm_files(root: Path, *, interval_ms: int = 0, **changes: Any) -> Files:
    """Inputs of a run with the real engines and the LLM caller (``max_turns`` 3)."""
    return write_inputs(
        root,
        sim_profile=profile_doc(
            tts="sarvam", stt="sarvam", caller=llm_caller(), min_call_interval_ms=interval_ms,
            **changes,
        ),
    )  # fmt: skip


def llm_args(files: Files, *, target: bool = False, **caps: str) -> list[str]:
    args = real_args(files, target=target)
    args[args.index("--caller") + 1] = "sarvam"
    values = {"--max-llm-tokens": "500", **caps}
    for option, value in values.items():
        if option in args:
            args[args.index(option) + 1] = value
        else:
            args += [option, value]
    return list(args)


def kill_at(
    clock: VirtualClock, files: Files, at_ms: int, created: list[int]
) -> Callable[[Any], Any]:
    """A connector that answers with the in-process fake agent of the scenario and, ``at_ms`` after
    the connection, creates the kill-switch file in the output directory (as an operator would).
    The virtual instant at which it did is appended to ``created``."""
    tasks: list[Any] = []

    async def connect(request: Any) -> Any:
        async def make() -> None:
            await clock.sleep_until_ns(clock.monotonic_ns() + at_ms * 1_000_000)
            created.append(clock.monotonic_ns())
            (files.out / KILL_SWITCH_NAME).write_bytes(b"")

        tasks.append(asyncio.ensure_future(make()))
        return await fake_connect(files, clock)(request)

    return connect
