# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``smallwebrtc/1`` as the call runner sees it (ADR 0026 rule 7; batch F contract section 3.4).

WebRTC encodes the audio itself, so this dialect has no message format of its own: a message is one
frame of 16-bit little-endian mono PCM in either direction, there is no opening and no closing
message, and the frame the simulator sends is 20 ms at the dialect rate. The media connection that
carries the frames is ``tracer_evalsim.webrtc.open_webrtc``.
"""

from __future__ import annotations

from typing import ClassVar

from tracer_evalsim.dialects.base import Decoded, Dialect
from tracer_evalsim.webrtc import DIALECT_ID, SPEC_RETRIEVED, SPEC_SOURCES

__all__ = ["SmallWebRtcDialect"]


class SmallWebRtcDialect(Dialect):
    dialect_id: ClassVar[str] = DIALECT_ID
    wire_codec: ClassVar[str] = "pcm_s16le"
    spec_sources: ClassVar[tuple[str, ...]] = SPEC_SOURCES
    spec_retrieved: ClassVar[str] = SPEC_RETRIEVED

    def __init__(self, *, sample_rate_hz: int) -> None:
        if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int):
            raise ValueError("sample_rate_hz is not an integer")
        if not 8000 <= sample_rate_hz <= 48000 or sample_rate_hz % 50:
            raise ValueError("sample_rate_hz must be 8000..48000 and a multiple of 50")
        self.sample_rate_hz = sample_rate_hz

    def opening_messages(self) -> tuple[bytes | str, ...]:
        return ()

    def encode_audio(self, frame: bytes, *, stream_ms: int) -> bytes:
        self._check_frame(frame)
        return frame

    def closing_messages(self) -> tuple[bytes | str, ...]:
        return ()

    def decode(self, message: bytes | str) -> Decoded | None:
        if not isinstance(message, bytes) or len(message) < 2 or len(message) % 2:
            return None
        return Decoded(kind="audio", audio=message)
