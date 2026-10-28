# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Line providers for the call runner: the caller brain as the runner sees it (ADR 0028).

``PresynthesisedLines`` serves the scripted and the replay caller: every line was synthesised
before the call (ADR 0026 rule 14 step 7), so the call makes no request. ``LlmLines`` serves the
LLM caller: each turn asks the brain for a line and synthesises it during the call; the engines are
synchronous, so both run through ``run_blocking`` (a thread by default) and never stall the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from tracer_evalcore.brain import CallerBrain
from tracer_evalcore.llm import (
    LlmAuthError,
    LlmCapReachedError,
    LlmError,
    LlmRateLimited,
    LlmRequestRejected,
    LlmResponseInvalid,
    LlmServerError,
    LlmTransportError,
)
from tracer_evalcore.tts import TtsCapReachedError, TtsError, TtsOutcome
from tracer_evalsim.clock import Clock
from tracer_evalsim.runner import CallerEnd, PreparedLine
from tracer_evalsim.sender import split_frames

__all__ = [
    "BRAIN_FAILURE_CODES",
    "LlmLines",
    "PresynthesisedLines",
    "SynthesisedLine",
    "brain_failure_code",
    "run_in_thread",
]

_MS: Final = 1_000_000
T = TypeVar("T")

#: the facts a failed brain leaves (BL-338): the class of the provider's error, as a stable code
BRAIN_FAILURE_CODES: Final = (
    "llm_auth",
    "llm_error",
    "llm_rate_limited",
    "llm_rejected",
    "llm_response_invalid",
    "llm_server_error",
    "llm_transport",
)
_BY_CLASS: Final = (
    (LlmAuthError, "llm_auth"),
    (LlmRequestRejected, "llm_rejected"),
    (LlmRateLimited, "llm_rate_limited"),
    (LlmServerError, "llm_server_error"),
    (LlmTransportError, "llm_transport"),
    (LlmResponseInvalid, "llm_response_invalid"),
)


def brain_failure_code(error: LlmError) -> str:
    """The code of a provider failure: its class, never its message."""
    for cls, code in _BY_CLASS:
        if isinstance(error, cls):
            return code
    return "llm_error"


async def run_in_thread(function: Callable[[], T]) -> T:
    """The default ``run_blocking``: the blocking engine call runs in a worker thread."""
    return await asyncio.to_thread(function)


@dataclass(frozen=True)
class SynthesisedLine:
    text: str
    audio: bytes


class PresynthesisedLines:
    """Lines whose audio exists before the call; ``final`` is what the brain says after them."""

    needs_agent_text = False

    def __init__(
        self,
        mode: str,
        entries: Sequence[SynthesisedLine],
        final: CallerEnd,
        *,
        sample_rate_hz: int,
        wire_codec: str,
    ) -> None:
        self.mode = mode
        self._frames = [
            (e.text, tuple(split_frames(e.audio, sample_rate_hz=sample_rate_hz, codec=wire_codec)))
            for e in entries
        ]
        self._final = final

    async def next(self, turn_index: int, agent_text: str | None) -> PreparedLine | CallerEnd:
        if turn_index < len(self._frames):
            text, frames = self._frames[turn_index]
            return PreparedLine(text=text, frames=frames)
        return self._final


class LlmLines:
    """The LLM caller: brain then synthesis, per turn, during the call."""

    mode = "llm"
    needs_agent_text = True

    def __init__(
        self,
        brain: CallerBrain,
        synthesise: Callable[[str, int], TtsOutcome],
        *,
        clock: Clock,
        sample_rate_hz: int,
        wire_codec: str,
        run_blocking: Callable[[Callable[[], Any]], Awaitable[Any]] | None = None,
        requests_of: Callable[[], int] | None = None,
    ) -> None:
        self._brain = brain
        self._synthesise = synthesise
        self._clock = clock
        self._rate = sample_rate_hz
        self._codec = wire_codec
        self._run: Callable[[Callable[[], Any]], Awaitable[Any]] = run_blocking or run_in_thread
        self._requests_of = requests_of or (lambda: 0)

    async def next(self, turn_index: int, agent_text: str | None) -> PreparedLine | CallerEnd:
        before = self._requests_of()
        started = self._clock.monotonic_ns()
        try:
            decision = await self._run(lambda: self._brain.next_line(turn_index, agent_text))
        except LlmCapReachedError:
            return CallerEnd(
                "spend_cap", cap_reached="llm_tokens", requests=self._requests_of() - before
            )
        except LlmError as error:
            return CallerEnd(
                "brain_failed",
                requests=self._requests_of() - before,
                failure_code=brain_failure_code(error),
                failure_status=error.status,
            )
        brain_ms = (self._clock.monotonic_ns() - started) // _MS
        requests = self._requests_of() - before
        if decision.line is None:
            assert decision.end_reason is not None  # noqa: S101
            cap = "llm_tokens" if decision.end_reason == "spend_cap" else None
            return CallerEnd(
                decision.end_reason,
                invalid_outputs=decision.invalid_outputs,
                regenerations=decision.regenerations,
                cap_reached=cap,
                requests=requests,
            )
        started = self._clock.monotonic_ns()
        line = decision.line
        try:
            outcome: TtsOutcome = await self._run(lambda: self._synthesise(line, turn_index))
        except TtsError:
            return CallerEnd("transport_error", requests=requests)
        tts_ms = (self._clock.monotonic_ns() - started) // _MS
        if outcome.failure is not None:
            if isinstance(outcome.failure, TtsCapReachedError):
                return CallerEnd(
                    "spend_cap",
                    invalid_outputs=decision.invalid_outputs,
                    regenerations=decision.regenerations,
                    cap_reached="tts_chars",
                    requests=requests,
                )
            return CallerEnd(
                "transport_error",
                invalid_outputs=decision.invalid_outputs,
                regenerations=decision.regenerations,
                requests=requests,
            )
        assert outcome.audio is not None  # noqa: S101
        frames = tuple(split_frames(outcome.audio, sample_rate_hz=self._rate, codec=self._codec))
        return PreparedLine(
            text=line,
            frames=frames,
            brain_ms=brain_ms,
            tts_ms=tts_ms,
            invalid_outputs=decision.invalid_outputs,
            regenerations=decision.regenerations,
            requests=requests,
        )
