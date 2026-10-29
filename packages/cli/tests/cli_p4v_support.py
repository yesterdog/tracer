# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the batch voice cli tests (BL-369, the end reason ``voice_failed``). Support
module, not collected. Contract: docs/phase4/api-surface-batch-voice.md.

It builds on ``cli_p4e_support`` (the in-process run of ``tracer simulate``, the virtual clock, the
scripted Sarvam and the canary key) and ``cli_p4g_support`` (the LLM-caller inputs). Nothing here
opens a socket, reads a key from the machine or calls a provider: the provider is an
``httpx.MockTransport`` and a failure of it is a scripted response or a scripted ``httpx``
exception. Secrets are canaries assembled from parts.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

import httpx

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import SarvamSim  # type: ignore[import-not-found,unused-ignore]
from cli_p4g_support import PROVIDER_MESSAGE_CANARY  # type: ignore[import-not-found,unused-ignore]

__all__ = ["FAILURES", "VoiceSim"]

#: failure kind of the scripted text-to-speech -> (code on the line, status on the line or None)
FAILURES: tuple[tuple[str, str, str | None], ...] = (
    ("401", "tts_auth", "HTTP 401"),
    ("403", "tts_auth", "HTTP 403"),
    ("400", "tts_rejected", "HTTP 400"),
    ("422", "tts_rejected", "HTTP 422"),
    ("429", "tts_rate_limited", "HTTP 429"),
    ("500", "tts_server_error", "HTTP 500"),
    ("503", "tts_server_error", "HTTP 503"),
    ("transport", "tts_transport", None),
    ("timeout", "tts_transport", None),
    ("bad_json", "tts_response_invalid", "HTTP 200"),
    ("no_audios", "tts_response_invalid", "HTTP 200"),
    ("headerless", "tts_response_invalid", "HTTP 200"),
)


class VoiceSim(SarvamSim):  # type: ignore[misc,unused-ignore]
    """The scripted Sarvam of the batch E tests whose text-to-speech endpoint answers its first
    ``good`` requests normally and then fails in the way ``failure`` says. A failure applies to
    every attempt of the client, retries included. Every request is kept in ``requests`` (a raised
    ``httpx`` error is recorded too). The chat and transcription endpoints are the normal ones."""

    def __init__(
        self,
        failure: str,
        *,
        good: int = 0,
        chat: Sequence[str] = (),
        stt: Sequence[str] | None = None,
    ) -> None:
        assert failure in {f[0] for f in FAILURES}, failure
        kwargs: dict[str, Any] = {"chat": chat}
        if stt is not None:
            kwargs["stt"] = stt
        super().__init__(**kwargs)
        self.failure = failure
        self.good = good
        self.answered = 0
        self.tts_attempts = 0

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/text-to-speech" or self.answered < self.good:
            if request.url.path == "/text-to-speech":
                self.answered += 1
            response: httpx.Response = super()._handle(request)
            return response
        self.requests.append(request)
        self.paths.append(request.url.path)
        self.tts_attempts += 1
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
        if self.failure == "no_audios":
            return httpx.Response(200, headers=headers, json={"request_id": "tts-x", "audios": []})
        if self.failure == "headerless":
            raw = b"\x00\x00\x00\x00" + (1000).to_bytes(2, "little") * 320
            return httpx.Response(
                200,
                headers=headers,
                json={"request_id": "tts-x", "audios": [base64.b64encode(raw).decode("ascii")]},
            )
        return httpx.Response(int(self.failure), headers=headers, json=body)
