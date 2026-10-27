# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch B tests (contract docs/phase4/api-surface-batch-b.md).

Nothing of the batch B modules is imported at module level: each is loaded lazily by the
accessors below, so that before the code exists every test fails on its own with
``ModuleNotFoundError`` and the directory still collects.

No test reaches a network: requests go to ``ScriptedTransport`` (an ``httpx.MockTransport`` of the
Phase 3 support file) and the key is the canary ``CANARY_KEY``, built from parts, never a real key.
The Marathi texts are invented, short and spelled with escapes so that this file stays ASCII.

The one place that knows what a TTS reply holds is ``tts_reply`` (and ``wav_bytes``): the
container of the decoded audio is NOT verified against Sarvam (ADR 0024 A2), the tests assume the
reviewer's reading "a base64 WAV file" for ``linear16`` and keep that assumption in this one
function and in ``test_sarvam_tts_container.py``.
"""

from __future__ import annotations

import base64
import importlib
import json
import struct
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    UTC_NOW,
    ScriptedTransport,
    Sleeps,
    Ticker,
    WallClock,
    json_response,
    pcm,
)
from tracer_evalschema.ids import RunId

__all__ = [
    "CANARY_KEY",
    "DATE",
    "LONG_MR",
    "MR_LINE",
    "SECOND_MR_LINE",
    "UTC_NOW",
    "Kit",
    "LlmRig",
    "SimRun",
    "TtsRig",
    "chat_reply",
    "llm_kit",
    "llm_request",
    "tts_kit",
    "tts_reply",
    "tts_request",
    "tts_voice",
    "wav_bytes",
]

DATE = "Fri, 23 Oct 2026 13:08:26 GMT"
#: invented Marathi lines (a greeting; "I need help"), for byte-count and verbatim tests
MR_LINE = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930"
SECOND_MR_LINE = "\u092e\u0932\u093e \u092e\u0926\u0924 \u0939\u0935\u0940 \u0906\u0939\u0947"
LONG_MR = "\u0905" * 2500


# ---------------------------------------------------------------------------- lazy modules


def core_tts() -> ModuleType:
    return importlib.import_module("tracer_evalcore.tts")


def core_llm() -> ModuleType:
    return importlib.import_module("tracer_evalcore.llm")


def tts_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.tts")


def chat_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.chat")


def models_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.models")


def client_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.client")


def simlog_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.simcalllog")


def cache_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.ttscache")


def secrets_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.secrets")


def cost_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.cost")


def activity_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.audio.activity")


# ---------------------------------------------------------------------------- requests


def tts_voice(**over: Any) -> Any:
    fields: dict[str, Any] = {
        "provider": "sarvam",
        "model": "bulbul:v3",
        "speaker": "tracerspeaker",
        "language_code": "mr-IN",
        "pace": "1",
    }
    fields.update(over)
    return core_tts().TtsVoice(**fields)


def tts_request(
    index: int = 0,
    text: str = MR_LINE,
    *,
    rate: int = 16000,
    codec: str = "linear16",
    voice: Any = None,
) -> Any:
    return core_tts().TtsRequest(
        index=index,
        text=text,
        voice=tts_voice() if voice is None else voice,
        sample_rate_hz=rate,
        codec=codec,
    )


def llm_request(
    index: int = 0,
    *,
    model: str = "sarvam-105b",
    max_tokens: int = 64,
    seed: int | None = None,
    effort: str | None = None,
    messages: Sequence[tuple[str, str]] = (("system", "be brief"), ("user", "hello")),
) -> Any:
    core = core_llm()
    return core.LlmRequest(
        index=index,
        messages=tuple(core.LlmMessage(role=r, content=c) for r, c in messages),
        model=model,
        max_tokens=max_tokens,
        seed=seed,
        reasoning_effort=effort,
    )


def tts_context() -> Any:
    return core_tts().TtsContext(run_id=RunId.new(), created_at=UTC_NOW)


def llm_context() -> Any:
    return core_llm().LlmContext(run_id=RunId.new(), created_at=UTC_NOW)


# ---------------------------------------------------------------------------- replies


def wav_bytes(
    samples: bytes, rate: int, *, channels: int = 1, bits: int = 16, fmt: int = 1
) -> bytes:
    """A canonical 44-byte-header WAV file (``fmt`` 1 is PCM)."""
    block = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(samples),
        b"WAVE",
        b"fmt ",
        16,
        fmt,
        channels,
        rate,
        rate * block,
        block,
        bits,
        b"data",
        len(samples),
    )
    return header + samples


def tts_samples(frames: int = 320, seed: int = 7) -> bytes:
    """The raw 16-bit samples that ``tts_reply`` wraps (what ``TtsOutcome.audio`` must equal)."""
    data: bytes = pcm(frames, seed=seed)
    return data


def tts_reply(
    request_id: str = "tts-req-1",
    *,
    rate: int = 16000,
    frames: int = 320,
    seed: int = 7,
    extra: dict[str, Any] | None = None,
    date: str | None = DATE,
    audio: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """A 200 TTS reply holding one base64 audio (the WAV container assumption lives here)."""
    payload = wav_bytes(tts_samples(frames, seed), rate) if audio is None else audio
    body: dict[str, Any] = {
        "request_id": request_id,
        "audios": [base64.b64encode(payload).decode("ascii")],
    }
    body.update(extra or {})
    response: httpx.Response = json_response(body, date=date, headers=headers)
    return response


def chat_reply(
    content: str = SECOND_MR_LINE,
    *,
    reply_id: str = "chat-req-1",
    prompt: int = 11,
    completion: int = 5,
    cached: int | None = None,
    extra: dict[str, Any] | None = None,
    date: str | None = DATE,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    body: dict[str, Any] = {
        "id": reply_id,
        "object": "chat.completion",
        "model": "echoed-model-name",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }
    body.update(extra or {})
    response: httpx.Response = json_response(body, date=date, headers=headers)
    return response


# ---------------------------------------------------------------------------- rigs


class _Rig:
    """A scripted transport, fake sleep and clocks, a call log, and the engine under test."""

    log: Any
    engine: Any

    def __init__(self, tmp_path: Path, steps: Sequence[Any]) -> None:
        self.transport = ScriptedTransport(list(steps))
        self.sleeps = Sleeps()
        self.ticker = Ticker()
        self.out = tmp_path / "out"
        self.log = simlog_mod().SimCallLog.open(self.out, clock=WallClock())
        self.key = secrets_mod().ApiKey(CANARY_KEY)

    def _finalise(self) -> None:
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


class TtsRig(_Rig):
    def __init__(
        self,
        tmp_path: Path,
        steps: Sequence[Any],
        *,
        cap: int | None = None,
        cache: Any = None,
    ) -> None:
        super().__init__(tmp_path, steps)
        self.engine = tts_mod().SarvamTts(
            self.key,
            call_log=self.log,
            cap_characters=cap,
            cache=cache,
            transport=self.transport,
            sleep=self.sleeps,
            monotonic=self.ticker,
        )
        self._finalise()

    def call(
        self,
        request: Any = None,
        *,
        on_usage: Callable[[Any], None] | None = None,
        context: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {} if on_usage is None else {"on_usage": on_usage}
        return self.engine.synthesise(
            tts_request() if request is None else request,
            tts_context() if context is None else context,
            **kwargs,
        )


class LlmRig(_Rig):
    def __init__(self, tmp_path: Path, steps: Sequence[Any], *, cap: int | None = None) -> None:
        super().__init__(tmp_path, steps)
        self.engine = chat_mod().SarvamLlm(
            self.key,
            call_log=self.log,
            cap_tokens=cap,
            transport=self.transport,
            sleep=self.sleeps,
            monotonic=self.ticker,
        )
        self._finalise()

    def call(
        self,
        request: Any = None,
        *,
        on_usage: Callable[[Any], None] | None = None,
        context: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {} if on_usage is None else {"on_usage": on_usage}
        return self.engine.complete(
            llm_request() if request is None else request,
            llm_context() if context is None else context,
            **kwargs,
        )


class SimRun:
    """The three Sarvam engines of one simulated call over ONE shared call log (A8): TTS, chat and
    STT, each over its own scripted transport, with fake sleep and clocks."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        tts: Sequence[Any] = (),
        llm: Sequence[Any] = (),
        stt: Sequence[Any] = (),
        tts_cap: int | None = None,
        llm_cap: int | None = None,
        cache: Any = None,
    ) -> None:
        self.out = tmp_path / "out"
        self.sleeps = Sleeps()
        self.ticker = Ticker()
        self.tts_transport = ScriptedTransport(list(tts))
        self.llm_transport = ScriptedTransport(list(llm))
        self.stt_transport = ScriptedTransport(list(stt))
        self.log = simlog_mod().SimCallLog.open(self.out, clock=WallClock())
        key = secrets_mod().ApiKey(CANARY_KEY)
        common: dict[str, Any] = {
            "call_log": self.log,
            "sleep": self.sleeps,
            "monotonic": self.ticker,
        }
        self.tts = tts_mod().SarvamTts(
            key, cap_characters=tts_cap, cache=cache, transport=self.tts_transport, **common
        )
        self.llm = chat_mod().SarvamLlm(
            key, cap_tokens=llm_cap, transport=self.llm_transport, **common
        )
        self.stt_client = client_mod().SarvamClient(
            key,
            call_log=self.log.for_stt(),
            transport=self.stt_transport,
            sleep=self.sleeps,
            monotonic=self.ticker,
        )
        self.stt = importlib.import_module("tracer_evalproviders.sarvam.engine").SarvamStt(
            self.stt_client
        )
        weakref.finalize(self, self.log.close)
        weakref.finalize(self, self.tts.close)
        weakref.finalize(self, self.llm.close)
        weakref.finalize(self, self.stt_client.close)

    @property
    def log_lines(self) -> list[dict[str, Any]]:
        text = self.log.path.read_text("ascii")
        return [json.loads(line) for line in text.splitlines()]

    @property
    def log_text(self) -> str:
        text: str = self.log.path.read_text("ascii")
        return text


# ---------------------------------------------------------------------------- the kits


@dataclass(frozen=True)
class Kit:
    """What the shared taxonomy scenarios need to drive either adapter."""

    name: str
    prefix: str
    stage: str
    path: str

    def errors(self) -> ModuleType:
        return core_tts() if self.name == "tts" else core_llm()

    def error(self, suffix: str) -> Any:
        return getattr(self.errors(), self.prefix + suffix)

    def ok(self, number: int = 0) -> httpx.Response:
        if self.name == "tts":
            return tts_reply(request_id=f"tts-ok-{number}")
        return chat_reply(reply_id=f"chat-ok-{number}")

    def rig(self, tmp_path: Path, steps: Sequence[Any]) -> Any:
        return TtsRig(tmp_path, steps) if self.name == "tts" else LlmRig(tmp_path, steps)

    def request(self, index: int = 5) -> Any:
        return tts_request(index) if self.name == "tts" else llm_request(index)

    def call(self, rig: Any, index: int = 5) -> Any:
        return rig.call(self.request(index))

    def request_id_member(self) -> str:
        return "request_id" if self.name == "tts" else "id"


tts_kit = Kit("tts", "Tts", "tts", "/text-to-speech")
llm_kit = Kit("llm", "Llm", "llm", "/v2/chat/completions")
