# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""What the bot heard of the caller, as the ``call_summary`` line it prints (stdlib only)."""

from __future__ import annotations

import struct
from typing import Any

__all__ = ["CallTally", "estimate_hz"]


def estimate_hz(pcm: bytes, sample_rate_hz: int) -> float | None:
    """The frequency of ``pcm`` (16-bit LE mono) from its zero crossings: the sign changes (zeros
    skipped), counted between the first and the last, over the time between them; ``None`` for a
    signal with fewer than two crossings."""
    count = len(pcm) // 2
    at: list[int] = []
    previous = 0
    for index, sample in enumerate(struct.unpack(f"<{count}h", pcm[: count * 2])):
        if sample == 0:
            continue
        sign = 1 if sample > 0 else -1
        if previous and sign != previous:
            at.append(index)
        previous = sign
    if len(at) < 2:
        return None
    return (len(at) - 1) / 2 / ((at[-1] - at[0]) / sample_rate_hz)


class CallTally:
    def __init__(self) -> None:
        self._turns: list[dict[str, Any]] = []
        self._open: list[bytes] | None = None
        self._rate: int | None = None
        self._replies = 0

    def on_audio(self, pcm: bytes, sample_rate_hz: int) -> None:
        if self._open is None:
            self._open = []
        self._open.append(pcm)
        self._rate = sample_rate_hz

    def on_turn_end(self) -> None:
        if self._open is None or self._rate is None:
            return
        data = b"".join(self._open)
        self._turns.append(
            {
                "audio_ms": len(data) // 2 * 1000 // self._rate,
                "frames": len(self._open),
                "hz": estimate_hz(data, self._rate),
            }
        )
        self._open = None

    def on_reply_started(self) -> None:
        self._replies += 1

    def summary(self, *, call: int, dialect: str, ended_by: str) -> dict[str, Any]:
        self.on_turn_end()
        return {
            "event": "call_summary",
            "call": call,
            "dialect": dialect,
            "ended_by": ended_by,
            "caller_turns": list(self._turns),
            "replies_started": self._replies,
            "inbound_frames": sum(t["frames"] for t in self._turns),
            "inbound_sample_rate": self._rate,
        }
