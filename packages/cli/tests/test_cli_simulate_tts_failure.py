# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A text-to-speech failure before the call is a typed failure, not "unexpected failure".

Found by the first real Sarvam call (2026-10-28): a reply the adapter refused ended as
``error: unexpected failure (TtsResponseInvalid)``, with the closed reason key hidden. Contract
docs/phase4/api-surface-batch-e.md section 10.2: a TTS cap hit is the refusal
``spend_cap_reached_before_the_call (tts_chars)`` (exit 4); any other TTS failure before the call is
exit 1. The line now names the error class and the closed reason key (and the HTTP status when
there is one), never provider text and never a key, and the usage of earlier answered requests is
kept in the recovery file.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    SarvamSim,
    real_args,
    real_env,
    real_files,
    run_sim,
)


class SecondReplyHeaderless(SarvamSim):  # type: ignore[misc,unused-ignore]
    """The first TTS reply is good; the second is headerless PCM (the shape seen on the wire)."""

    def _handle(self, request: httpx.Request) -> httpx.Response:
        good: httpx.Response = super()._handle(request)
        if request.url.path != "/text-to-speech" or len(self.bodies("/text-to-speech")) < 2:
            return good
        raw = b"\x00\x00\x00\x00" + (1000).to_bytes(2, "little") * 320
        return httpx.Response(
            200,
            headers=good.headers,
            json={"request_id": "tts-x", "audios": [base64.b64encode(raw).decode("ascii")]},
        )


def test_a_refused_tts_reply_before_the_call_names_the_class_and_the_reason(
    tmp_path: Path,
) -> None:
    files = real_files(tmp_path)
    sim = SecondReplyHeaderless()
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=sim)
    assert run.code == 1
    assert run.err == (
        "error: text-to-speech failed before the call (TtsResponseInvalid: audio_not_recognised)\n"
    )
    assert "connect" not in run.trace
    assert not list(files.out.glob("report-*"))
    assert KEY_CANARY not in run.out + run.err


def test_a_refused_tts_reply_keeps_the_usage_of_the_earlier_request(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    run = run_sim(
        files, args=real_args(files), environ=real_env(), transport=SecondReplyHeaderless()
    )
    (path,) = sorted(files.out.glob("usage-recovery-*.json"))
    document: dict[str, Any] = json.loads(path.read_text("ascii"))
    assert len(document["usage_records"]) == 1
    assert document["failed_step"] == "presynthesis"
    assert document["error_class"] == "TtsResponseInvalid"
    assert run.out == f"usage recovery: {path}\n"
    assert KEY_CANARY not in path.read_text("ascii")


def test_a_tts_provider_error_names_the_class_and_the_status(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    run = run_sim(
        files, args=real_args(files), environ=real_env(), transport=SarvamSim(tts_status=503)
    )
    assert run.code == 1
    assert run.err == ("error: text-to-speech failed before the call (TtsServerError: HTTP 503)\n")
