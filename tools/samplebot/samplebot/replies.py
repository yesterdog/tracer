# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The replies of ``scripted_bot`` are pure sines of the scenario (no file, no model).
Standard library only."""

from __future__ import annotations

import math
import struct

__all__ = ["tone_pcm_s16le"]


def tone_pcm_s16le(*, hz: int, ms: int, level: int, sample_rate_hz: int) -> bytes:
    """``sample_rate_hz * ms / 1000`` samples of ``level * sin(2 pi hz n / rate)``, 16-bit LE."""
    count = sample_rate_hz * ms // 1000
    samples = [round(level * math.sin(2 * math.pi * hz * n / sample_rate_hz)) for n in range(count)]
    return struct.pack(f"<{count}h", *samples)
