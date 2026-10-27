# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""G.711 mu-law and A-law decoding to 16-bit linear PCM (ADR 0027 rule 4).

Pure tables: 256 entries each, the conventional 16-bit scaling of the ITU-T G.711 decision values
(mu-law 0xFF and 0x7F decode to 0; A-law has no zero: 0xD5 is +8 and 0x55 is -8). Nothing is
resampled; the output has two bytes (little-endian) per input byte.
"""

from __future__ import annotations

import struct
from typing import Final

__all__ = ["ALAW_DECODE_TABLE", "MULAW_DECODE_TABLE", "decode_alaw", "decode_mulaw"]


def _mulaw(code: int) -> int:
    u = ~code & 0xFF
    t = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
    return 0x84 - t if u & 0x80 else t - 0x84


def _alaw(code: int) -> int:
    a = code ^ 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    t = t + 8 if seg == 0 else (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t


MULAW_DECODE_TABLE: Final = tuple(_mulaw(c) for c in range(256))
ALAW_DECODE_TABLE: Final = tuple(_alaw(c) for c in range(256))
_MU_BYTES: Final = tuple(struct.pack("<h", v) for v in MULAW_DECODE_TABLE)
_A_BYTES: Final = tuple(struct.pack("<h", v) for v in ALAW_DECODE_TABLE)


def decode_mulaw(data: bytes) -> bytes:
    """16-bit little-endian PCM of mu-law bytes."""
    if not isinstance(data, bytes | bytearray):
        raise TypeError("data: not bytes")
    return b"".join([_MU_BYTES[b] for b in data])


def decode_alaw(data: bytes) -> bytes:
    """16-bit little-endian PCM of A-law bytes."""
    if not isinstance(data, bytes | bytearray):
        raise TypeError("data: not bytes")
    return b"".join([_A_BYTES[b] for b in data])
