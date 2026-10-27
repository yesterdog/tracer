# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``pipecat-protobuf/1``: the Pipecat development runner's ``/ws-client`` (ADR 0026 rule 5)."""

from __future__ import annotations

from typing import ClassVar

from tracer_evalsim.dialects.base import Decoded, Dialect
from tracer_evalsim.dialects.protobuf_wire import (
    AudioRawFrame,
    InterruptionFrame,
    MessageFrame,
    ProtobufDecodeError,
    TextFrame,
    TranscriptionFrame,
    decode_frame,
    encode_frame,
)
from tracer_evalsim.errors import TransportError

__all__ = ["PipecatProtobufDialect"]


class PipecatProtobufDialect(Dialect):
    dialect_id: ClassVar[str] = "pipecat-protobuf/1"
    wire_codec: ClassVar[str] = "pcm_s16le"
    spec_sources: ClassVar[tuple[str, ...]] = (
        "https://raw.githubusercontent.com/pipecat-ai/pipecat/v1.14.0/src/pipecat/frames/frames.proto",
        "https://raw.githubusercontent.com/pipecat-ai/pipecat/v1.14.0/src/pipecat/serializers/protobuf.py",
    )
    spec_retrieved: ClassVar[str] = "2026-10-27"

    def __init__(self, *, sample_rate_hz: int) -> None:
        if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int):
            raise ValueError("sample_rate_hz is not an integer")
        if not 8000 <= sample_rate_hz <= 48000 or sample_rate_hz % 50:
            raise ValueError("sample_rate_hz must be 8000..48000 and a multiple of 50")
        self.sample_rate_hz = sample_rate_hz

    def opening_messages(self) -> tuple[bytes | str, ...]:
        return ()

    def closing_messages(self) -> tuple[bytes | str, ...]:
        return ()

    def encode_audio(self, frame: bytes, *, stream_ms: int) -> bytes:
        self._check_frame(frame)
        return encode_frame(
            AudioRawFrame(audio=frame, sample_rate=self.sample_rate_hz, num_channels=1)
        )

    def decode(self, message: bytes | str) -> Decoded | None:
        if not isinstance(message, bytes):
            return None
        try:
            frame = decode_frame(message)
        except ProtobufDecodeError:
            return None
        if isinstance(frame, AudioRawFrame):
            if frame.sample_rate != self.sample_rate_hz or frame.num_channels != 1:
                raise TransportError(code="audio_format_mismatch")
            if len(frame.audio) % 2:
                raise TransportError(code="audio_frame_invalid")
            return Decoded(kind="audio", audio=frame.audio)
        if isinstance(frame, InterruptionFrame):
            return Decoded(kind="interruption")
        if isinstance(frame, TextFrame):
            return Decoded(kind="text")
        if isinstance(frame, TranscriptionFrame):
            return Decoded(kind="transcription")
        if isinstance(frame, MessageFrame):
            return Decoded(kind="message")
        return None
