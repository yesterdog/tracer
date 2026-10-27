# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The dialect interface, the wire connection protocol and the inbound reader (ADR 0026 rules 5, 22)."""  # noqa: E501

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol

from tracer_evalsim.clock import Clock
from tracer_evalsim.errors import TransportError
from tracer_evalsim.limits import (
    FRAME_NS,
    MAX_INBOUND_MESSAGE_BYTES,
    MAX_INBOUND_MESSAGES_PER_SECOND,
    NS_PER_S,
)

__all__ = [
    "AGENT_EVENT_KINDS",
    "AgentEvent",
    "CloseInfo",
    "Decoded",
    "Dialect",
    "InboundEnd",
    "InboundReader",
    "InboundStats",
    "WireConnection",
    "hang_up",
]

AGENT_EVENT_KINDS = ("audio", "clear", "mark", "interruption", "text", "transcription", "message")


@dataclass(frozen=True)
class CloseInfo:
    code: int | None
    clean: bool


class WireConnection(Protocol):
    async def send(self, message: bytes | str) -> None: ...

    async def recv(self) -> bytes | str | None: ...

    async def close(self, code: int = 1000) -> None: ...

    @property
    def close_info(self) -> CloseInfo | None: ...


@dataclass(frozen=True)
class Decoded:
    kind: str
    audio: bytes | None = None


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    arrival_ns: int
    audio: bytes | None = None


class Dialect(ABC):
    dialect_id: ClassVar[str]
    wire_codec: ClassVar[str]
    spec_sources: ClassVar[tuple[str, ...]]
    spec_retrieved: ClassVar[str]
    sample_rate_hz: int

    @property
    def frame_bytes(self) -> int:
        width = 2 if self.wire_codec == "pcm_s16le" else 1
        return self.sample_rate_hz * FRAME_NS // NS_PER_S * width

    @abstractmethod
    def opening_messages(self) -> tuple[bytes | str, ...]: ...

    @abstractmethod
    def encode_audio(self, frame: bytes, *, stream_ms: int) -> bytes | str: ...

    @abstractmethod
    def closing_messages(self) -> tuple[bytes | str, ...]: ...

    @abstractmethod
    def decode(self, message: bytes | str) -> Decoded | None: ...

    def _check_frame(self, frame: bytes) -> None:
        if not isinstance(frame, bytes) or len(frame) != self.frame_bytes:
            raise ValueError("an audio frame must be exactly 20 ms of wire audio")


@dataclass(frozen=True)
class InboundStats:
    messages: int
    audio_frames: int
    audio_bytes: int
    ignored: int
    after_close: int
    by_kind: dict[str, int]
    clear_arrival_ns: tuple[int, ...]


@dataclass(frozen=True)
class InboundEnd:
    clean: bool
    close_code: int | None

    @property
    def end_reason(self) -> str:
        return "agent_hangup" if self.clean else "transport_error"


class InboundReader:
    def __init__(
        self,
        dialect: Dialect,
        clock: Clock,
        *,
        max_message_bytes: int = MAX_INBOUND_MESSAGE_BYTES,
        max_messages_per_second: int = MAX_INBOUND_MESSAGES_PER_SECOND,
    ) -> None:
        self._dialect = dialect
        self._clock = clock
        self._max_bytes = max_message_bytes
        self._max_rate = max_messages_per_second
        self._window: deque[int] = deque()
        self._closed = False
        self._messages = 0
        self._audio_frames = 0
        self._audio_bytes = 0
        self._ignored = 0
        self._after_close = 0
        self._by_kind: dict[str, int] = {}
        self._clears: list[int] = []

    @property
    def stats(self) -> InboundStats:
        return InboundStats(
            messages=self._messages,
            audio_frames=self._audio_frames,
            audio_bytes=self._audio_bytes,
            ignored=self._ignored,
            after_close=self._after_close,
            by_kind=dict(self._by_kind),
            clear_arrival_ns=tuple(self._clears),
        )

    def mark_closed(self) -> None:
        self._closed = True

    def feed(self, message: bytes | str) -> AgentEvent | None:
        if self._closed:
            self._after_close += 1
            return None
        size = len(message) if isinstance(message, bytes) else len(message.encode("utf-8"))
        if size > self._max_bytes:
            raise TransportError(code="message_too_large")
        now = self._clock.monotonic_ns()
        while self._window and self._window[0] <= now - NS_PER_S:
            self._window.popleft()
        if len(self._window) >= self._max_rate:
            raise TransportError(code="inbound_rate_exceeded")
        self._window.append(now)
        self._messages += 1
        decoded = self._dialect.decode(message)
        if decoded is None:
            self._ignored += 1
            return None
        self._by_kind[decoded.kind] = self._by_kind.get(decoded.kind, 0) + 1
        if decoded.kind == "audio":
            self._audio_frames += 1
            self._audio_bytes += len(decoded.audio or b"")
        elif decoded.kind == "clear":
            self._clears.append(now)
        return AgentEvent(kind=decoded.kind, arrival_ns=now, audio=decoded.audio)

    async def run(self, conn: WireConnection, on_event: Callable[[AgentEvent], None]) -> InboundEnd:
        while True:
            message = await conn.recv()
            if message is None:
                self.mark_closed()
                info = conn.close_info
                if info is None:
                    return InboundEnd(clean=False, close_code=None)
                return InboundEnd(clean=info.clean, close_code=info.code)
            event = self.feed(message)
            if event is not None:
                on_event(event)


async def hang_up(conn: WireConnection, dialect: Dialect, *, close_code: int = 1000) -> None:
    """End the call: the dialect's closing messages, then close the socket (never wait for a reply).

    A peer that is already gone is not an error: the close is still attempted.
    """
    try:
        for message in dialect.closing_messages():
            await conn.send(message)
    except TransportError:
        pass
    await conn.close(close_code)
