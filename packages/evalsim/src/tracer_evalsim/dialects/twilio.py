# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``twilio-media-stream/1``: a Twilio-compatible media stream to the runner's ``/ws`` (rule 6)."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, ClassVar

from tracer_evalsim.dialects.base import Decoded, Dialect

__all__ = ["TwilioMediaStreamDialect"]


class TwilioMediaStreamDialect(Dialect):
    dialect_id: ClassVar[str] = "twilio-media-stream/1"
    wire_codec: ClassVar[str] = "mulaw"
    spec_sources: ClassVar[tuple[str, ...]] = (
        "https://www.twilio.com/docs/voice/media-streams/websocket-messages",
        "https://www.twilio.com/docs/voice/media-streams",
        "https://raw.githubusercontent.com/pipecat-ai/pipecat/v1.14.0/src/pipecat/serializers/twilio.py",
    )
    spec_retrieved: ClassVar[str] = "2026-10-27"

    def __init__(self, *, counter: int) -> None:
        valid = isinstance(counter, int) and not isinstance(counter, bool)
        if not valid or not 1 <= counter <= 999_999_999:
            raise ValueError("counter must be an integer from 1 to 999999999")
        self.sample_rate_hz = 8000
        self.account_sid = f"tracer-sim-account-{counter}"
        self.stream_sid = f"tracer-sim-stream-{counter}"
        self.call_sid = f"tracer-sim-call-{counter}"
        self._state = "new"
        self._sequence = 0
        self._chunk = 0

    def _dump(self, body: dict[str, Any]) -> str:
        return json.dumps(body, separators=(",", ":"))

    def _next(self) -> str:
        self._sequence += 1
        return str(self._sequence)

    def opening_messages(self) -> tuple[bytes | str, ...]:
        if self._state != "new":
            raise RuntimeError("the stream is already open")
        self._state = "open"
        connected = self._dump({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        start = self._dump(
            {
                "event": "start",
                "sequenceNumber": self._next(),
                "start": {
                    "accountSid": self.account_sid,
                    "streamSid": self.stream_sid,
                    "callSid": self.call_sid,
                    "tracks": ["inbound"],
                    "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                    "customParameters": {"tracer_synthetic": "true"},
                },
                "streamSid": self.stream_sid,
            }
        )
        return (connected, start)

    def encode_audio(self, frame: bytes, *, stream_ms: int) -> str:
        if self._state != "open":
            raise RuntimeError("the stream is not open")
        self._check_frame(frame)
        self._chunk += 1
        return self._dump(
            {
                "event": "media",
                "sequenceNumber": self._next(),
                "media": {
                    "track": "inbound",
                    "chunk": str(self._chunk),
                    "timestamp": str(stream_ms),
                    "payload": base64.b64encode(frame).decode("ascii"),
                },
                "streamSid": self.stream_sid,
            }
        )

    def closing_messages(self) -> tuple[bytes | str, ...]:
        if self._state != "open":
            raise RuntimeError("the stream is not open")
        self._state = "closed"
        return (
            self._dump(
                {
                    "event": "stop",
                    "sequenceNumber": self._next(),
                    "stop": {"accountSid": self.account_sid, "callSid": self.call_sid},
                    "streamSid": self.stream_sid,
                }
            ),
        )

    def decode(self, message: bytes | str) -> Decoded | None:
        if not isinstance(message, str):
            return None
        try:
            body = json.loads(message)
        except Exception:  # any parser failure (a RecursionError included) is an ignored message
            return None
        if not isinstance(body, dict):
            return None
        event = body.get("event")
        if event == "media":
            media = body.get("media")
            payload = media.get("payload") if isinstance(media, dict) else None
            if not isinstance(payload, str):
                return None
            try:
                return Decoded(kind="audio", audio=base64.b64decode(payload, validate=True))
            except (binascii.Error, ValueError):
                return None
        if event == "clear":
            return Decoded(kind="clear")
        if event == "mark":
            mark = body.get("mark")
            if isinstance(mark, dict) and isinstance(mark.get("name"), str):
                return Decoded(kind="mark")
        return None
