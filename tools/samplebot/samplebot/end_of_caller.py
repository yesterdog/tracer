# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A timer on the ARRIVAL of ACTIVE caller frames: the caller has finished when no active audio
frame has arrived for ``end_of_caller_ms``. A frame is active if it holds any non-zero byte (the
definition of ADR 0026 rule 10: no threshold, no energy, no voice-activity model). WebRTC streams
digital silence between lines; the WebSocket dialects send nothing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pipecat.frames.frames import CancelFrame, EndFrame, Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class EndOfCallerTimer(FrameProcessor):
    def __init__(self, *, end_of_caller_ms: int, on_end_of_caller: Callable[[], None]) -> None:
        super().__init__()
        self._seconds = end_of_caller_ms / 1000
        self._on_end = on_end_of_caller
        self._handle: asyncio.TimerHandle | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and any(frame.audio):
            self._rearm()
        elif isinstance(frame, EndFrame | CancelFrame):
            self._disarm()
        await self.push_frame(frame, direction)

    def _disarm(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _rearm(self) -> None:
        self._disarm()
        self._handle = asyncio.get_running_loop().call_later(self._seconds, self._fire)

    def _fire(self) -> None:
        self._handle = None
        self._on_end()
