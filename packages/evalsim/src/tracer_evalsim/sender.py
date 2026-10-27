# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The paced sender (ADR 0026 rule 8): real-time 20 ms frames, backpressure, never a burst."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from tracer_evalsim.clock import Clock
from tracer_evalsim.limits import FRAME_NS

__all__ = ["FrameRecord", "LineResult", "PacedSender", "split_frames"]


@dataclass(frozen=True)
class FrameRecord:
    line_index: int
    frame_index: int
    scheduled_ns: int
    sent_ns: int
    size: int

    @property
    def lateness_ns(self) -> int:
        return self.sent_ns - self.scheduled_ns


@dataclass(frozen=True)
class LineResult:
    line_index: int
    frames_sent: int
    stopped: bool


def split_frames(audio: bytes, *, sample_rate_hz: int, codec: str) -> list[bytes]:
    if codec not in ("pcm_s16le", "mulaw"):
        raise ValueError("unknown wire codec")
    size = sample_rate_hz * 20 // 1000 * (2 if codec == "pcm_s16le" else 1)
    if size <= 0 or sample_rate_hz % 50:
        raise ValueError("sample rate does not give whole 20 ms frames")
    pad = b"\x00" if codec == "pcm_s16le" else b"\xff"
    frames = [audio[i : i + size] for i in range(0, len(audio), size)]
    if frames and len(frames[-1]) < size:
        frames[-1] = frames[-1] + pad * (size - len(frames[-1]))
    return frames


class PacedSender:
    def __init__(self, clock: Clock, send_frame: Callable[[bytes], Awaitable[None]]) -> None:
        self._clock = clock
        self._send_frame = send_frame
        self._log: list[FrameRecord] = []
        self._lines = 0

    @property
    def log(self) -> tuple[FrameRecord, ...]:
        return tuple(self._log)

    async def send_line(
        self, frames: Sequence[bytes], *, should_stop: Callable[[], bool] | None = None
    ) -> LineResult:
        line = self._lines
        self._lines += 1
        sent = 0
        origin = self._clock.monotonic_ns()
        anchor = origin
        for index, frame in enumerate(frames):
            if should_stop is not None and should_stop():
                return LineResult(line, sent, True)
            target = anchor + index * FRAME_NS
            now = self._clock.monotonic_ns()
            if index > 0 and now >= target + FRAME_NS:
                anchor += now - target
                target = now
            elif now < target:
                await self._clock.sleep_until_ns(target)
                if should_stop is not None and should_stop():
                    return LineResult(line, sent, True)
            sent_ns = self._clock.monotonic_ns()
            await self._send_frame(frame)
            self._log.append(
                FrameRecord(line, index, origin + index * FRAME_NS, sent_ns, len(frame))
            )
            sent += 1
        return LineResult(line, sent, False)
