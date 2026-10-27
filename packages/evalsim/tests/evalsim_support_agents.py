# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Deterministic fake agents for the tracer-evalsim tests (P4-19). Support module, not collected.

A fake agent is a list of steps run against any ``WireConnection`` (``send``, ``recv``, ``close``)
and any ``Clock`` (``monotonic_ns``, ``async sleep_until_ns``). With the ``ManualClock`` of
``evalsim_support_clock`` the same steps give byte-identical sent messages at identical virtual
instants on every run; with the real ``SystemClock`` the same steps serve the loopback tests.

Steps: reply delays (``Wait``), speech (``Speak``, paced at real time or as a ``burst``), comfort
noise (``Comfort``: frames whose samples are tiny but not zero, so the agent never looks silent),
digital silence (``Silence``: all-zero frames), a hang-up (``HangUp`` with a close code or
``Abort`` with no close frame), garbage (``Garbage`` and raw ``Send``), and the dialect messages a
real agent may send (``Clear``, ``Mark``, ``Interruption``, ``Text``).

What an agent sends is built with the INDEPENDENT reference encoder of ``evalsim_support_wire``
and with plain ``json``/``base64`` here: never with the dialect code under test. The only source
of pseudo-randomness is ``noise()``, a ``random.Random`` created with an explicit seed; nothing
reads the wall clock, ``os.urandom``, ``secrets`` or ``uuid``
(``test_fake_agents_are_deterministic``
checks the source for that).
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

from evalsim_support_wire import ref_encode as _ref_encode  # type: ignore[import-not-found,unused-ignore]

FRAME_NS = 20_000_000


def ref_encode(member: str, fields: dict[str, Any]) -> bytes:
    data: bytes = _ref_encode(member, fields)
    return data


class _Clock(Protocol):
    def monotonic_ns(self) -> int: ...

    async def sleep_until_ns(self, deadline_ns: int) -> None: ...


class _Wire(Protocol):
    async def send(self, message: bytes | str) -> None: ...

    async def recv(self) -> bytes | str | None: ...

    async def close(self, code: int = 1000) -> None: ...


@dataclass(frozen=True)
class Wait:
    ms: int


@dataclass(frozen=True)
class Speak:
    frames: int
    level: int = 1000
    burst: bool = False


@dataclass(frozen=True)
class Comfort:
    frames: int


@dataclass(frozen=True)
class Silence:
    frames: int


@dataclass(frozen=True)
class Garbage:
    size: int
    seed: int = 1


@dataclass(frozen=True)
class Send:
    message: bytes | str


@dataclass(frozen=True)
class Clear:
    pass


@dataclass(frozen=True)
class Mark:
    name: str


@dataclass(frozen=True)
class Interruption:
    pass


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class HangUp:
    code: int = 1000


@dataclass(frozen=True)
class Abort:
    pass


Step = (
    Wait
    | Speak
    | Comfort
    | Silence
    | Garbage
    | Send
    | Clear
    | Mark
    | Interruption
    | Text
    | HangUp
    | Abort
)


def noise(seed: int, size: int) -> bytes:
    rng = random.Random(seed)  # explicit seed: the only randomness in this module  # noqa: S311 - a seeded test signal, not a secret
    return bytes(rng.randrange(256) for _ in range(size))


class ProtobufAgentCodec:
    """What a Pipecat bot behind ``/ws-client`` sends (16-bit PCM, mono)."""

    dialect_id = "pipecat-protobuf/1"

    def __init__(self, sample_rate_hz: int = 16000) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_samples = sample_rate_hz * 20 // 1000

    def audio(self, level: int) -> bytes | str:
        pcm = struct.pack("<h", level) * self.frame_samples
        return ref_encode(
            "audio",
            {"audio": pcm, "sample_rate": self.sample_rate_hz, "num_channels": 1},
        )

    def clear(self) -> bytes | str:
        return ref_encode("interruption", {})

    def interruption(self) -> bytes | str:
        return ref_encode("interruption", {})

    def mark(self, name: str) -> bytes | str:
        return ref_encode("message", {"data": json.dumps({"mark": name})})

    def text(self, text: str) -> bytes | str:
        return ref_encode("text", {"text": text})


class TwilioAgentCodec:
    """What a Pipecat bot behind ``/ws`` (Twilio serializer) sends: mu-law, 8 kHz, mono JSON."""

    dialect_id = "twilio-media-stream/1"
    sample_rate_hz = 8000
    frame_samples = 160

    def __init__(self, stream_sid: str = "tracer-sim-stream-1") -> None:
        self.stream_sid = stream_sid

    def _json(self, body: dict[str, Any]) -> str:
        return json.dumps({**body, "streamSid": self.stream_sid}, separators=(",", ":"))

    def audio(self, level: int) -> bytes | str:
        # ``level`` is the mu-law byte; 0xFF is digital zero, anything else is a non-zero sample
        payload = base64.b64encode(bytes([level & 0xFF]) * self.frame_samples).decode("ascii")
        return self._json({"event": "media", "media": {"payload": payload}})

    def clear(self) -> bytes | str:
        return self._json({"event": "clear"})

    def interruption(self) -> bytes | str:
        return self._json({"event": "clear"})

    def mark(self, name: str) -> bytes | str:
        return self._json({"event": "mark", "mark": {"name": name}})

    def text(self, text: str) -> bytes | str:
        return json.dumps({"event": "unknown_text", "text": text})


def silent_level(codec: ProtobufAgentCodec | TwilioAgentCodec) -> int:
    return 0xFF if isinstance(codec, TwilioAgentCodec) else 0


def comfort_level(codec: ProtobufAgentCodec | TwilioAgentCodec) -> int:
    # the smallest non-zero sample: a PCM value of 1, or a mu-law byte that decodes to non-zero
    return 0xFE if isinstance(codec, TwilioAgentCodec) else 1


def speech_level(codec: ProtobufAgentCodec | TwilioAgentCodec, level: int) -> int:
    return 0x55 if isinstance(codec, TwilioAgentCodec) else level


@dataclass
class FakeAgent:
    """Runs ``steps`` against a wire; the logs hold (ns since start, message) pairs."""

    codec: ProtobufAgentCodec | TwilioAgentCodec
    steps: tuple[Step, ...]
    sent_log: list[tuple[int, bytes | str]] = field(default_factory=list)
    received_log: list[tuple[int, bytes | str]] = field(default_factory=list)
    aborted: bool = False

    async def _drain(self, wire: _Wire, clock: _Clock, origin: int) -> None:
        while True:
            message = await wire.recv()
            if message is None:
                return
            self.received_log.append((clock.monotonic_ns() - origin, message))

    async def _send(self, wire: _Wire, clock: _Clock, origin: int, message: bytes | str) -> None:
        self.sent_log.append((clock.monotonic_ns() - origin, message))
        await wire.send(message)

    async def _frames(
        self, wire: _Wire, clock: _Clock, origin: int, count: int, make: Any, *, burst: bool
    ) -> None:
        start = clock.monotonic_ns()
        for index in range(count):
            await self._send(wire, clock, origin, make())
            if not burst:
                await clock.sleep_until_ns(start + (index + 1) * FRAME_NS)

    async def run(self, wire: _Wire, clock: _Clock) -> None:
        origin = clock.monotonic_ns()
        reader = asyncio.ensure_future(self._drain(wire, clock, origin))
        codec = self.codec
        try:
            for step in self.steps:
                if isinstance(step, Wait):
                    await clock.sleep_until_ns(clock.monotonic_ns() + step.ms * 1_000_000)
                elif isinstance(step, Speak):
                    level = speech_level(codec, step.level)
                    await self._frames(
                        wire,
                        clock,
                        origin,
                        step.frames,
                        lambda lv=level: codec.audio(lv),
                        burst=step.burst,
                    )
                elif isinstance(step, Comfort):
                    level = comfort_level(codec)
                    await self._frames(
                        wire,
                        clock,
                        origin,
                        step.frames,
                        lambda lv=level: codec.audio(lv),
                        burst=False,
                    )
                elif isinstance(step, Silence):
                    level = silent_level(codec)
                    await self._frames(
                        wire,
                        clock,
                        origin,
                        step.frames,
                        lambda lv=level: codec.audio(lv),
                        burst=False,
                    )
                elif isinstance(step, Garbage):
                    await self._send(
                        wire, clock, origin, b"\xff" * 10 + noise(step.seed, step.size)
                    )
                elif isinstance(step, Send):
                    await self._send(wire, clock, origin, step.message)
                elif isinstance(step, Clear):
                    await self._send(wire, clock, origin, codec.clear())
                elif isinstance(step, Mark):
                    await self._send(wire, clock, origin, codec.mark(step.name))
                elif isinstance(step, Interruption):
                    await self._send(wire, clock, origin, codec.interruption())
                elif isinstance(step, Text):
                    await self._send(wire, clock, origin, codec.text(step.text))
                elif isinstance(step, HangUp):
                    await wire.close(step.code)
                    return
                elif isinstance(step, Abort):
                    self.aborted = True
                    abort = getattr(wire, "abort", None)
                    if abort is not None:
                        abort()
                    return
        finally:
            if not reader.done():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
