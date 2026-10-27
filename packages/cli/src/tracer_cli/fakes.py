# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The fake engines of an offline ``tracer simulate`` run: ``--tts fake`` and ``--stt fake``.

A fake engine makes no request, so it never claims one: neither returns a usage record. Both are
labelled everywhere (provider ``fake``). ``FakeTts`` returns a fixed tone whose length follows the
text; ``ScenarioStt`` returns, in order, the words the fake-agent scenario says its speech steps
speak (a statement of the scenario, never a recognition result).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import ClassVar, Final

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.stt import SttContext, SttOutcome, SttResponseInvalid
from tracer_evalcore.tts import TtsContext, TtsOutcome, TtsRequest
from tracer_evalproviders.sarvam.mapping import map_responses
from tracer_evalproviders.sarvam.models import ChunkResponse, parse_response

__all__ = ["FAKE_PROVIDER", "FAKE_STT_MODEL", "FAKE_TTS_MODEL", "FakeTts", "ScenarioStt"]

FAKE_PROVIDER: Final = "fake"
FAKE_TTS_MODEL: Final = "tone"
FAKE_STT_MODEL: Final = "scenario-text"
_MS_PER_CHARACTER: Final = 40
_FRAME_MS: Final = 20
_LEVEL: Final = 1000
_MULAW_TONE: Final = 0x55


class FakeTts:
    """Deterministic audio: 40 ms per code point of the text, rounded up to whole 20 ms frames,
    a constant non-zero sample (linear16) or byte (mu-law). No usage record."""

    provider: ClassVar[str] = FAKE_PROVIDER
    streams_usage: ClassVar[bool] = False

    def __init__(self) -> None:
        self.requests = 0

    def synthesise(self, request: TtsRequest, context: TtsContext) -> TtsOutcome:
        self.requests += 1
        duration_ms = len(request.text) * _MS_PER_CHARACTER
        frames = -(-duration_ms // _FRAME_MS)
        samples = frames * (request.sample_rate_hz * _FRAME_MS // 1000)
        if request.codec == "linear16":
            audio = _LEVEL.to_bytes(2, "little", signed=True) * samples
        else:
            audio = bytes([_MULAW_TONE]) * samples
        return TtsOutcome(audio=audio, usage=(), failure=None)


class ScenarioStt:
    """``--stt fake`` with ``--agent fake:<scenario>``: the k-th agent segment is the k-th words."""

    provider: ClassVar[str] = FAKE_PROVIDER
    model: ClassVar[str] = FAKE_STT_MODEL
    streams_usage: ClassVar[bool] = False

    def __init__(self, texts: Sequence[str]) -> None:
        self._texts = tuple(texts)
        self._next = 0

    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome:
        answered: list[ChunkResponse] = []
        for chunk in plan.chunks:
            if self._next >= len(self._texts):
                failure = SttResponseInvalid(chunk_index=chunk.index, reason="fake_texts_exhausted")
                return SttOutcome(None, (), (), (), failure)
            body = json.dumps(
                {
                    "request_id": f"fake-{self._next:04d}",
                    "transcript": self._texts[self._next],
                    "language_code": "mr-IN",
                }
            ).encode("utf-8")
            self._next += 1
            answered.append(ChunkResponse(chunk.index, parse_response(body), None, 1))
        outcome = map_responses(plan, answered, model=FAKE_STT_MODEL, context=context)
        if outcome.transcript is None:
            return outcome
        transcript = outcome.transcript.model_copy(update={"provider": FAKE_PROVIDER})
        return SttOutcome(transcript, outcome.turns, (), outcome.warnings, None)
