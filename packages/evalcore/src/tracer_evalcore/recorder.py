# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The stereo recorder (ADR 0027 rules 3 and 4; contract docs/phase4/api-surface-batch-a.md).

One timeline in integer nanoseconds of the caller's injected monotonic clock. A caller frame is
placed at its exact send instant, an agent frame at the later of its arrival and the end of the
previous agent frame (the playback model). G.711 is decoded by pure tables and nothing is ever
resampled: a frame at another rate, channel count or codec is refused. Pure: no clock, no I/O.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from tracer_evalcore._engine_errors import FactError
from tracer_evalcore.g711 import decode_alaw, decode_mulaw

__all__ = [
    "MAX_RECORDING_SECONDS",
    "RECORDER_CODECS",
    "LogFrame",
    "RecorderFormat",
    "RecorderFrameError",
    "Recording",
    "build_recording",
    "decode_to_pcm16",
]

RECORDER_CODECS: Final = ("pcm_s16le", "mulaw", "alaw")
MAX_RECORDING_SECONDS: Final = 14_400
_NS: Final = 1_000_000_000


class RecorderFrameError(FactError, ValueError):
    """A frame was refused. ``index`` is its position in its log, ``kind`` the fact, ``reason``
    the log (``caller`` or ``agent``). The message holds those facts and never the payload."""


@dataclass(frozen=True)
class RecorderFormat:
    """What every frame of the call must be: the dialect's rate and wire codec, mono."""

    sample_rate_hz: int
    codec: str

    def __post_init__(self) -> None:
        if type(self.sample_rate_hz) is not int or not 8000 <= self.sample_rate_hz <= 48000:
            raise ValueError("sample_rate_hz: not 8000 to 48000")
        if self.codec not in RECORDER_CODECS:
            raise ValueError("codec: not a recorder codec")


@dataclass(frozen=True)
class LogFrame:
    """One frame of a log: its instant (absolute integer ns) and what it carried."""

    t_ns: int
    payload: bytes
    sample_rate_hz: int
    channels: int
    codec: str

    def __post_init__(self) -> None:
        if type(self.t_ns) is not int or self.t_ns < 0:
            raise ValueError("t_ns: not a non-negative integer")
        if type(self.payload) is not bytes:
            raise ValueError("payload: not bytes")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz: not a positive integer")
        if type(self.channels) is not int or self.channels <= 0:
            raise ValueError("channels: not a positive integer")
        if self.codec not in RECORDER_CODECS:
            raise ValueError("codec: not a recorder codec")


@dataclass(frozen=True)
class Recording:
    """The stereo WAV and where each frame went (sample indexes on the timeline)."""

    wav: bytes
    sample_rate_hz: int
    n_samples: int
    caller_start_samples: tuple[int, ...]
    agent_start_samples: tuple[int, ...]
    caller_overlap_samples: int


def decode_to_pcm16(codec: str, payload: bytes) -> bytes:
    """16-bit little-endian PCM of a payload of ``codec``."""
    if codec == "pcm_s16le":
        if len(payload) % 2:
            raise ValueError("payload: odd length for 16-bit samples")
        return payload
    if codec == "mulaw":
        return decode_mulaw(payload)
    if codec == "alaw":
        return decode_alaw(payload)
    raise ValueError("codec: not a recorder codec")


def _check(
    fmt: RecorderFormat, frames: Sequence[LogFrame], origin_ns: int, who: str
) -> list[tuple[int, bytes]]:
    """Validate one log; return (start sample, pcm) per frame in log order."""
    out: list[tuple[int, bytes]] = []
    previous = origin_ns
    limit = MAX_RECORDING_SECONDS * fmt.sample_rate_hz
    for index, frame in enumerate(frames):
        for ok, kind in (
            (frame.t_ns >= origin_ns, "before_origin"),
            (frame.t_ns >= previous, "order"),
            (frame.sample_rate_hz == fmt.sample_rate_hz, "sample_rate"),
            (frame.channels == 1, "channels"),
            (frame.codec == fmt.codec, "codec"),
            (len(frame.payload) > 0 and (frame.codec != "pcm_s16le" or len(frame.payload) % 2 == 0),
             "payload_size"),
        ):  # fmt: skip
            if not ok:
                raise RecorderFrameError(index=index, kind=kind, reason=who)
        previous = frame.t_ns
        pcm = decode_to_pcm16(frame.codec, frame.payload)
        start = (frame.t_ns - origin_ns) * fmt.sample_rate_hz // _NS
        if start + len(pcm) // 2 > limit:
            raise RecorderFrameError(index=index, kind="too_long", reason=who)
        out.append((start, pcm))
    return out


def build_recording(
    fmt: RecorderFormat,
    origin_ns: int,
    caller: Sequence[LogFrame],
    agent: Sequence[LogFrame],
) -> Recording:
    """The stereo recording of two frame logs; the same logs give the same bytes."""
    if type(origin_ns) is not int or origin_ns < 0:
        raise ValueError("origin_ns: not a non-negative integer")
    caller_placed = _check(fmt, caller, origin_ns, "caller")
    agent_arrivals = _check(fmt, agent, origin_ns, "agent")
    agent_placed: list[tuple[int, bytes]] = []
    free = 0
    for arrival, pcm in agent_arrivals:
        start = max(arrival, free)
        free = start + len(pcm) // 2
        agent_placed.append((start, pcm))
    caller_end = max((s + len(p) // 2 for s, p in caller_placed), default=0)
    n = max(caller_end, free)
    left = bytearray(2 * n)
    right = bytearray(2 * n)
    overlap = 0
    covered = 0
    for start, pcm in caller_placed:
        count = len(pcm) // 2
        overlap += min(count, max(0, covered - start))
        covered = max(covered, start + count)
        left[2 * start : 2 * (start + count)] = pcm
    for start, pcm in agent_placed:
        right[2 * start : 2 * start + len(pcm)] = pcm
    body = bytearray(4 * n)
    body[0::4] = left[0::2]
    body[1::4] = left[1::2]
    body[2::4] = right[0::2]
    body[3::4] = right[1::2]
    rate = fmt.sample_rate_hz
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + 4 * n, b"WAVE", b"fmt ", 16, 1, 2, rate, rate * 4, 4, 16, b"data", 4 * n,
    )  # fmt: skip
    return Recording(
        wav=header + bytes(body),
        sample_rate_hz=rate,
        n_samples=n,
        caller_start_samples=tuple(s for s, _ in caller_placed),
        agent_start_samples=tuple(s for s, _ in agent_placed),
        caller_overlap_samples=overlap,
    )
