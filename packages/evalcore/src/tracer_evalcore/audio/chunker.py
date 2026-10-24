# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Deterministic chunker of 16-bit PCM audio (P3-05; ADR 0024 rule 7).

Audio longer than ``max_chunk_ms`` is cut into chunks of at most that length. A window of
``W = rate * max_chunk_ms // 1000`` sample frames is cut inside its last ``S = rate * search_ms //
1000`` frames, at the middle of the quietest whole 20 ms frame (``F = rate * 20 // 1000`` sample
frames, counted from the window start). The energy of a frame is the exact integer sum of the
squares of all its samples over all channels; a tie goes to the latest frame. Everything is
integer arithmetic, so the same input gives the same plan and the same bytes on every machine.
"""

from __future__ import annotations

import struct
import sys
from array import array
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tracer_evalcore.audio.seconds import exact_seconds
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.importers.wav import WavAudio

__all__ = [
    "DEFAULT_MAX_CHUNK_MS",
    "DEFAULT_SEARCH_MS",
    "ChannelsRefusedError",
    "Chunk",
    "ChunkPlan",
    "ChunkerError",
    "ChunkerInputError",
    "ChunkerParameterError",
    "chunk_pcm",
    "chunk_wav",
]

DEFAULT_MAX_CHUNK_MS = 25_000
DEFAULT_SEARCH_MS = 5_000
PLAN_FORMAT = "tracer.chunk_plan/1"

_FRAME_MS = 20
_SAMPLE_BYTES = 2
_HEADER = struct.Struct("<4sI4s4sIHHIIHH4sI")


class ChunkerError(InputRefusedError):
    """The chunker refused its parameters or its input."""


class ChunkerParameterError(ChunkerError):
    """A parameter is out of range, or no whole 20 ms frame lies in the search region."""


class ChunkerInputError(ChunkerError):
    """The audio is not a whole number of 16-bit sample frames."""


class ChannelsRefusedError(ChunkerError):
    """More than one channel without the explicit opt-in, or more than two channels."""


@dataclass(frozen=True)
class Chunk:
    index: int
    start_frame: int
    end_frame: int
    sample_rate_hz: int
    channels: int
    wav: bytes = field(repr=False)

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def duration_seconds(self) -> Decimal | None:
        return exact_seconds(self.frames, self.sample_rate_hz)

    @property
    def start_seconds(self) -> Decimal | None:
        return exact_seconds(self.start_frame, self.sample_rate_hz)


@dataclass(frozen=True)
class ChunkPlan:
    sample_rate_hz: int
    channels: int
    max_chunk_ms: int
    search_ms: int
    total_frames: int
    chunks: tuple[Chunk, ...]

    def as_metadata(self) -> dict[str, Any]:
        """The plan as an integers-only JSON document (a ``Transcript.metadata`` value)."""
        return {
            "format": PLAN_FORMAT,
            "max_chunk_ms": self.max_chunk_ms,
            "search_ms": self.search_ms,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "total_frames": self.total_frames,
            "chunks": [
                {"index": c.index, "start_frame": c.start_frame, "end_frame": c.end_frame}
                for c in self.chunks
            ],
        }


def _check_int(name: str, value: object, minimum: int) -> None:
    if type(value) is not int:
        raise ChunkerParameterError(f"{name} must be an int")
    if value < minimum:
        raise ChunkerParameterError(f"{name} must be {minimum} or more")


def _wav_bytes(samples: bytes | memoryview, rate: int, channels: int) -> bytes:
    size = len(samples)
    header = _HEADER.pack(
        b"RIFF",
        36 + size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        rate * channels * _SAMPLE_BYTES,
        channels * _SAMPLE_BYTES,
        8 * _SAMPLE_BYTES,
        b"data",
        size,
    )
    return header + bytes(samples)


def _energies(view: memoryview, first: int, count: int, frame: int, channels: int) -> list[int]:
    """Exact sum of squares of ``count`` consecutive frames starting at sample frame ``first``."""
    width = frame * channels
    samples = array("h")
    samples.frombytes(
        view[first * channels * _SAMPLE_BYTES : (first + count * frame) * channels * _SAMPLE_BYTES]
    )
    if sys.byteorder == "big":
        samples.byteswap()
    return [
        sum(v * v for v in samples[index * width : (index + 1) * width]) for index in range(count)
    ]


def _cut(view: memoryview, start: int, window: int, region: int, frame: int, channels: int) -> int:
    """The cut point (a sample frame index) of the window that begins at ``start``."""
    if frame < 2 or window < frame:
        raise ChunkerParameterError(
            "a cut is needed but the window holds no whole 20 ms frame of at least two samples"
        )
    first = max(0, -(-(window - region) // frame))  # ceil((W - S) / F)
    last = window // frame - 1
    if first > last:
        raise ChunkerParameterError(
            "a cut is needed but no whole 20 ms frame lies in the search region"
        )
    energies = _energies(view, start + first * frame, last - first + 1, frame, channels)
    best = 0
    for offset, energy in enumerate(energies):
        if energy <= energies[best]:  # a tie goes to the latest frame
            best = offset
    return start + (first + best) * frame + frame // 2


def chunk_pcm(
    pcm: bytes,
    *,
    sample_rate_hz: int,
    channels: int,
    max_chunk_ms: int = DEFAULT_MAX_CHUNK_MS,
    search_ms: int = DEFAULT_SEARCH_MS,
    accept_provider_downmix: bool = False,
) -> ChunkPlan:
    """Cut little-endian interleaved 16-bit PCM into chunks of at most ``max_chunk_ms``.

    Two channels are refused unless ``accept_provider_downmix`` is true; the chunks then keep both
    channels untouched (Tracer computes no downmix). Three or more channels are always refused.
    Zero frames give zero chunks.
    """
    _check_int("sample_rate_hz", sample_rate_hz, 1)
    _check_int("channels", channels, 1)
    _check_int("max_chunk_ms", max_chunk_ms, 1)
    _check_int("search_ms", search_ms, 0)
    if search_ms > max_chunk_ms:
        raise ChunkerParameterError("search_ms must not exceed max_chunk_ms")
    if type(accept_provider_downmix) is not bool:
        raise ChunkerParameterError("accept_provider_downmix must be a bool")
    if channels > 2 or (channels == 2 and not accept_provider_downmix):
        raise ChannelsRefusedError(
            f"{channels} channels refused: mono is chunked as it is, two channels need "
            "accept_provider_downmix=True, more are never accepted"
        )
    if not isinstance(pcm, bytes):
        raise TypeError("pcm must be bytes")
    block = channels * _SAMPLE_BYTES
    if len(pcm) % block != 0:
        raise ChunkerInputError(
            f"{len(pcm)} bytes are not a whole number of {channels}-channel 16-bit sample frames"
        )
    total = len(pcm) // block
    window = sample_rate_hz * max_chunk_ms // 1000
    region = sample_rate_hz * search_ms // 1000
    frame = sample_rate_hz * _FRAME_MS // 1000
    view = memoryview(pcm)
    chunks: list[Chunk] = []
    start = 0
    while start < total:
        end = (
            total if total - start <= window else _cut(view, start, window, region, frame, channels)
        )
        chunks.append(
            Chunk(
                index=len(chunks),
                start_frame=start,
                end_frame=end,
                sample_rate_hz=sample_rate_hz,
                channels=channels,
                wav=_wav_bytes(view[start * block : end * block], sample_rate_hz, channels),
            )
        )
        start = end
    return ChunkPlan(
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        max_chunk_ms=max_chunk_ms,
        search_ms=search_ms,
        total_frames=total,
        chunks=tuple(chunks),
    )


def chunk_wav(
    audio: WavAudio,
    *,
    max_chunk_ms: int = DEFAULT_MAX_CHUNK_MS,
    search_ms: int = DEFAULT_SEARCH_MS,
    accept_provider_downmix: bool = False,
) -> ChunkPlan:
    """``chunk_pcm`` over the samples, rate and channels of a ``read_wav`` result."""
    return chunk_pcm(
        audio.pcm,
        sample_rate_hz=audio.sample_rate_hz,
        channels=audio.channels,
        max_chunk_ms=max_chunk_ms,
        search_ms=search_ms,
        accept_provider_downmix=accept_provider_downmix,
    )
