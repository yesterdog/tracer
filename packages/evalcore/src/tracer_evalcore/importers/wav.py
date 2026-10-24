# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Reader of 16-bit PCM WAV recordings into an ``Artifact`` and the samples (P3-03).

Only plain PCM (format tag 1) with 16-bit samples is read. Everything else is refused with a typed
error whose message holds sizes and format numbers, never a byte of the file. The size is taken
from the open descriptor before any byte is read, and the buffer is sized by the file, never by
the cap or by a size the header claims.
"""

from __future__ import annotations

import hashlib
import os
import stat
import struct
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tracer_evalcore.audio.seconds import exact_seconds
from tracer_evalcore.errors import InputRefusedError
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.artifact import Artifact, AudioFormat
from tracer_evalschema.ids import ArtifactId
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy

__all__ = [
    "DEFAULT_MAX_WAV_BYTES",
    "WavAudio",
    "WavError",
    "WavMalformedError",
    "WavTooLargeError",
    "WavTruncatedError",
    "WavUnsupportedFormatError",
    "read_wav",
]

DEFAULT_MAX_WAV_BYTES = 1_073_741_824
"""1 GiB: a technical bound (four hours of 24 kHz mono 16-bit audio is about 691 MB)."""

_PCM_TAG = 1
_BITS = 16
_FMT_MIN = 16
_CHUNK_HEADER = struct.Struct("<4sI")
_FMT_BODY = struct.Struct("<HHIIHH")


class WavError(InputRefusedError):
    """A WAV file was refused."""


class WavMalformedError(WavError):
    """Not a WAV file this reader can parse (structure, not a regular file)."""


class WavTruncatedError(WavError):
    """The file holds fewer bytes than its own header says, or ends inside a chunk or a frame."""


class WavUnsupportedFormatError(WavError):
    """A WAV file that is not plain 16-bit PCM."""


class WavTooLargeError(WavError):
    """The file is larger than the cap."""


@dataclass(frozen=True)
class WavAudio:
    artifact: Artifact
    frames: int
    sample_rate_hz: int
    channels: int
    duration_seconds: Decimal | None
    pcm: bytes = field(repr=False)


def _check_arguments(max_bytes: object, consent: object, retention: object) -> None:
    if type(max_bytes) is not int:
        raise TypeError("max_bytes must be an int")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be above zero")
    if type(consent) is not ConsentRecord:
        raise TypeError("consent must be a ConsentRecord")
    if type(retention) is not RetentionPolicy:
        raise TypeError("retention must be a RetentionPolicy")


def _load(path: str, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    # Non-blocking so that opening a FIFO never waits for a writer; the type is checked next.
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise WavMalformedError("not a regular file")
        size = info.st_size
        if size > max_bytes:
            raise WavTooLargeError(f"file of {size} bytes is larger than the cap of {max_bytes}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(size + 1)
    finally:
        os.close(fd)
    if len(data) != size:
        raise WavMalformedError("file changed while it was read")
    return data


def _parse_fmt(body: memoryview) -> tuple[int, int]:
    """Validate a ``fmt `` chunk body; returns ``(channels, sample_rate_hz)``."""
    tag, channels, rate, _byte_rate, block, bits = _FMT_BODY.unpack_from(body)
    if tag != _PCM_TAG:
        raise WavUnsupportedFormatError(f"format tag {tag} is not plain PCM (1)")
    if bits != _BITS:
        raise WavUnsupportedFormatError(f"{bits} bits per sample; only 16 is read")
    if channels < 1:
        raise WavUnsupportedFormatError("zero channels")
    if rate < 1:
        raise WavUnsupportedFormatError("sample rate of zero")
    if block != channels * (_BITS // 8):
        raise WavMalformedError(f"block alignment {block} contradicts {channels} channel(s)")
    return channels, rate


def _parse(data: bytes) -> tuple[int, int, bytes]:
    """Returns ``(channels, sample_rate_hz, pcm)``."""
    total = len(data)
    if total < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavMalformedError(f"not a RIFF/WAVE file ({total} bytes)")
    view = memoryview(data)
    position = 12
    fmt: tuple[int, int] | None = None
    while position + _CHUNK_HEADER.size <= total:
        chunk_id, size = _CHUNK_HEADER.unpack_from(view, position)
        body = position + _CHUNK_HEADER.size
        if body + size > total:
            raise WavTruncatedError(
                f"chunk at offset {position} declares {size} bytes, "
                f"the file holds {total - body} after its header"
            )
        if chunk_id == b"fmt " and fmt is None:
            if size < _FMT_MIN:
                raise WavMalformedError(f"fmt chunk of {size} bytes is shorter than {_FMT_MIN}")
            fmt = _parse_fmt(view[body : body + size])
        elif chunk_id == b"data":
            if fmt is None:
                raise WavMalformedError("data chunk before the fmt chunk")
            channels, rate = fmt
            if size % (channels * (_BITS // 8)) != 0:
                raise WavTruncatedError(
                    f"data size {size} is not a whole number of frames of {channels} channel(s)"
                )
            return channels, rate, bytes(view[body : body + size])
        position = body + size + (size & 1)
    if position < total:
        raise WavTruncatedError(f"file ends inside a chunk header at offset {position}")
    raise WavMalformedError("no fmt chunk" if fmt is None else "no data chunk")


def read_wav(
    path: str | os.PathLike[str],
    *,
    created_at: str | datetime,
    consent: ConsentRecord,
    retention: RetentionPolicy,
    max_bytes: int = DEFAULT_MAX_WAV_BYTES,
) -> WavAudio:
    """Read a 16-bit PCM WAV file into an artifact record and its samples.

    The artifact's marker is human-subject data with the consent and retention given; nothing is
    inferred or coerced. ``created_at`` is the creation time of the record (never the clock).
    Operating-system errors (a missing file) are raised as they are.
    """
    _check_arguments(max_bytes, consent, retention)
    resolved = Path(path).resolve(strict=True)
    data = _load(os.fspath(resolved), max_bytes)
    channels, rate, pcm = _parse(data)
    frames = len(pcm) // (channels * (_BITS // 8))
    fields: dict[str, Any] = {
        "id": ArtifactId.new(),
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "uri": resolved.as_uri(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "audio_format": AudioFormat(
            container="wav",
            codec="pcm_s16le",
            sample_rate_hz=rate,
            channels=channels,
            bits_per_sample=_BITS,
        ),
        "privacy": PrivacyMarker(human_subject=True, consent=consent, retention=retention),
    }
    artifact = Artifact(**fields)
    return WavAudio(
        artifact=artifact,
        frames=frames,
        sample_rate_hz=rate,
        channels=channels,
        duration_seconds=exact_seconds(frames, rate),
        pcm=pcm,
    )
