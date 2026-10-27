# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The agent-activity chunk plan and the choice of the STT channel."""

from __future__ import annotations

import sys
from array import array
from dataclasses import dataclass
from typing import Final

from tracer_evalcore.audio.chunker import (
    DEFAULT_MAX_CHUNK_MS,
    DEFAULT_SEARCH_MS,
    PLAN_FORMAT_V2,
    ChannelsRefusedError,
    Chunk,
    ChunkerInputError,
    ChunkerParameterError,
    ChunkPlan,
    chunk_pcm,
)
from tracer_evalcore.errors import InputRefusedError
from tracer_evalschema.artifact import ChannelMap

__all__ = [
    "ACTIVITY_FRAME_MS",
    "ACTIVITY_PLAN_FORMAT",
    "ChannelMapRefusedError",
    "SttChannel",
    "activity_chunk_plan",
    "select_stt_channel",
]

ACTIVITY_FRAME_MS: Final = 20
ACTIVITY_PLAN_FORMAT: Final = PLAN_FORMAT_V2


class ChannelMapRefusedError(InputRefusedError):
    """The channel map does not declare what the speech-to-text needs."""


@dataclass(frozen=True)
class SttChannel:
    index: int
    role: str


def select_stt_channel(channel_map: ChannelMap, *, role: str = "agent") -> SttChannel:
    if not isinstance(channel_map, ChannelMap):
        raise TypeError("channel_map must be a ChannelMap")
    if role not in ("agent", "caller"):
        raise ValueError("role must be 'agent' or 'caller'")
    if channel_map.determination != "declared":
        raise ChannelMapRefusedError("the channel map is not declared")
    found = [c for c in channel_map.channels if c.role == role]
    if len(found) != 1:
        raise ChannelMapRefusedError("the channel map does not declare exactly one such channel")
    return SttChannel(index=found[0].index, role=role)


def _int(name: str, value: object, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ChunkerParameterError(f"{name} must be an int of {minimum} or more")
    return value


def activity_chunk_plan(
    pcm: bytes,
    *,
    sample_rate_hz: int,
    channels: int,
    channel: int,
    wait_for_agent_silence_ms: int,
    max_chunk_ms: int = DEFAULT_MAX_CHUNK_MS,
    search_ms: int = DEFAULT_SEARCH_MS,
) -> ChunkPlan:
    _int("sample_rate_hz", sample_rate_hz, 1)
    _int("channels", channels, 1)
    _int("channel", channel, 0)
    _int("wait_for_agent_silence_ms", wait_for_agent_silence_ms, 1)
    _int("max_chunk_ms", max_chunk_ms, 1)
    _int("search_ms", search_ms, 0)
    if search_ms > max_chunk_ms:
        raise ChunkerParameterError("search_ms must not exceed max_chunk_ms")
    if channels > 2:
        raise ChannelsRefusedError("more than two channels are never accepted")
    if channel >= channels:
        raise ChunkerParameterError("channel is not in the recording")
    if not isinstance(pcm, bytes):
        raise TypeError("pcm must be bytes")
    block = channels * 2
    if len(pcm) % block:
        raise ChunkerInputError("not a whole number of sample frames")
    data = array("h")
    data.frombytes(pcm)
    if sys.byteorder == "big":
        data.byteswap()
    mono = data[channel::channels]
    total = len(mono)
    size = sample_rate_hz * ACTIVITY_FRAME_MS // 1000
    count = -(-total // size) if size else 0
    active = [any(mono[k * size : (k + 1) * size]) for k in range(count)]
    runs: list[list[int]] = []
    for k, flag in enumerate(active):
        if not flag:
            continue
        if runs and (k - runs[-1][1] - 1) * ACTIVITY_FRAME_MS < wait_for_agent_silence_ms:
            runs[-1][1] = k
        else:
            runs.append([k, k])
    chunks: list[Chunk] = []
    for first, last in runs:
        start, end = first * size, min((last + 1) * size, total)
        segment = mono[start:end]
        if sys.byteorder == "big":
            segment = array("h", segment)
            segment.byteswap()
        part = chunk_pcm(
            segment.tobytes(),
            sample_rate_hz=sample_rate_hz,
            channels=1,
            max_chunk_ms=max_chunk_ms,
            search_ms=search_ms,
        )
        for piece in part.chunks:
            chunks.append(
                Chunk(
                    index=len(chunks),
                    start_frame=start + piece.start_frame,
                    end_frame=start + piece.end_frame,
                    sample_rate_hz=sample_rate_hz,
                    channels=1,
                    wav=piece.wav,
                )
            )
    return ChunkPlan(
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        max_chunk_ms=max_chunk_ms,
        search_ms=search_ms,
        total_frames=total,
        chunks=tuple(chunks),
        channel=channel,
        activity_wait_ms=wait_for_agent_silence_ms,
    )
