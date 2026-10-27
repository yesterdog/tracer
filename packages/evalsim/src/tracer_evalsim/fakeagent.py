# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The offline fake agent of ``tracer simulate --agent fake:<scenario>`` (P4-19, M1).

A scenario is a JSON file ``tracer.fake_agent/1`` with a closed list of timed steps: waits, speech,
digital silence, comfort noise, garbage, a hang-up or a dropped connection. The agent is in-process
(no socket, no network): it speaks the wire dialect of the run to the simulator over an in-memory
connection, on the injected clock, so the same scenario and the same clock give the same messages
at the same instants. It reacts to nothing the caller says. The ``text`` of a speech step is the
words the scenario author says the agent speaks; ``--stt fake`` returns them, in order, for the
agent segments (it is a statement of the scenario, never a recognition result).
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import struct
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict
from tracer_evalsim.clock import Clock
from tracer_evalsim.dialects.base import CloseInfo, Dialect, WireConnection
from tracer_evalsim.dialects.protobuf_wire import AudioRawFrame, encode_frame
from tracer_evalsim.errors import TransportError
from tracer_evalsim.limits import FRAME_NS

__all__ = [
    "FAKE_AGENT_FORMAT",
    "MAX_FAKE_AGENT_STEPS",
    "FakeAgentError",
    "FakeAgentScenario",
    "Step",
    "connect_fake_agent",
    "parse_fake_agent",
    "speech_texts",
]

FAKE_AGENT_FORMAT: Final = "tracer.fake_agent/1"
MAX_FAKE_AGENT_STEPS: Final = 1000
_MAX_FRAMES: Final = 100_000
_MULAW_SILENCE: Final = 0xFF
_MULAW_COMFORT: Final = 0xFE


class FakeAgentError(InputRefusedError):
    """A scenario was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Step:
    kind: str  # wait | speak | silence | comfort | garbage | hang_up | abort
    ms: int = 0
    frames: int = 0
    level: int = 0
    text: str | None = None
    size: int = 0
    seed: int = 0
    code: int = 1000


@dataclass(frozen=True)
class FakeAgentScenario:
    steps: tuple[Step, ...]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def parse_fake_agent(text: str) -> FakeAgentScenario:
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError):
        raise FakeAgentError("fake_agent_not_json") from None
    if (
        not isinstance(document, Mapping)
        or set(document) != {"format", "steps"}
        or document["format"] != FAKE_AGENT_FORMAT
        or type(document["steps"]) is not list
        or len(document["steps"]) > MAX_FAKE_AGENT_STEPS
    ):
        raise FakeAgentError("fake_agent_shape")
    steps: list[Step] = []
    for item in document["steps"]:
        if not isinstance(item, Mapping) or len(item) != 1:
            raise FakeAgentError("fake_agent_step_shape")
        ((kind, body),) = item.items()
        if kind == "wait_ms" and _int(body, 0, 3_600_000):
            steps.append(Step("wait", ms=body))
        elif kind == "speak" and isinstance(body, Mapping):
            extra = set(body) - {"frames", "level", "text"}
            if (
                extra
                or not _int(body.get("frames"), 1, _MAX_FRAMES)
                or not _int(body.get("level"), 1, 32767)
            ):
                raise FakeAgentError("fake_agent_step_invalid")
            words = body.get("text")
            if words is not None and (type(words) is not str or not words):
                raise FakeAgentError("fake_agent_step_invalid")
            steps.append(Step("speak", frames=body["frames"], level=body["level"], text=words))
        elif kind in ("silence", "comfort") and isinstance(body, Mapping):
            if set(body) != {"frames"} or not _int(body["frames"], 1, _MAX_FRAMES):
                raise FakeAgentError("fake_agent_step_invalid")
            steps.append(Step(kind, frames=body["frames"]))
        elif kind == "garbage" and isinstance(body, Mapping):
            if (
                set(body) != {"size", "seed"}
                or not _int(body["size"], 1, 1_000_000)
                or not _int(body["seed"], 0, 2**31)
            ):
                raise FakeAgentError("fake_agent_step_invalid")
            steps.append(Step("garbage", size=body["size"], seed=body["seed"]))
        elif kind == "hang_up" and _int(body, 1000, 4999):
            steps.append(Step("hang_up", code=body))
        elif kind == "abort" and body is True:
            steps.append(Step("abort"))
        else:
            raise FakeAgentError("fake_agent_step_unknown")
    return FakeAgentScenario(tuple(steps))


def speech_texts(scenario: FakeAgentScenario) -> tuple[str, ...]:
    """The words of the speech steps in order; a speech step without text is an error."""
    texts: list[str] = []
    for step in scenario.steps:
        if step.kind == "speak":
            if step.text is None:
                raise FakeAgentError("fake_agent_speech_without_text")
            texts.append(step.text)
    return tuple(texts)


# ------------------------------------------------------------------------------------ audio


def _mulaw_encode(sample: int) -> int:
    """G.711 mu-law byte of a 16-bit sample (the standard compander)."""
    bias, clip = 0x84, 32635
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), clip) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not magnitude & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    encoded = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return encoded if encoded != _MULAW_SILENCE else _MULAW_COMFORT  # never digital silence


def _agent_audio(dialect: Dialect, level: int, kind: str) -> bytes | str:
    """One 20 ms audio message of the agent in the dialect of the run."""
    rate = dialect.sample_rate_hz
    samples = rate * 20 // 1000
    if dialect.wire_codec == "pcm_s16le":
        value = 0 if kind == "silence" else (1 if kind == "comfort" else level)
        return encode_frame(
            AudioRawFrame(
                audio=struct.pack("<h", value) * samples, sample_rate=rate, num_channels=1
            )
        )
    byte = (
        _MULAW_SILENCE
        if kind == "silence"
        else (_MULAW_COMFORT if kind == "comfort" else _mulaw_encode(level))
    )
    payload = base64.b64encode(bytes([byte]) * samples).decode("ascii")
    return json.dumps(
        {"event": "media", "streamSid": "tracer-sim-stream-1", "media": {"payload": payload}},
        separators=(",", ":"),
    )


# ------------------------------------------------------------------------------- connection


class _FakeAgentWire:
    """The simulator's end of an in-memory connection to the fake agent."""

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[bytes | str | None] = asyncio.Queue()
        self._outbox: asyncio.Queue[bytes | str | None] = asyncio.Queue()
        self._closed = False
        self._info: CloseInfo | None = None
        self.task: asyncio.Future[None] | None = None

    @property
    def close_info(self) -> CloseInfo | None:
        return self._info

    async def send(self, message: bytes | str) -> None:
        if self._closed or self._info is not None:
            raise TransportError(code="connection_closed")
        self._outbox.put_nowait(message)

    async def recv(self) -> bytes | str | None:
        item = await self._inbox.get()
        if item is None:
            self._inbox.put_nowait(None)
        return item

    async def close(self, code: int = 1000) -> None:
        if not self._closed:
            self._closed = True
            if self._info is None:
                self._info = CloseInfo(code=code, clean=True)
            self._inbox.put_nowait(None)
            self._outbox.put_nowait(None)
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    # the agent's side
    def agent_send(self, message: bytes | str) -> None:
        if not self._closed and self._info is None:
            self._inbox.put_nowait(message)

    def agent_close(self, code: int) -> None:
        if self._info is None:
            self._info = CloseInfo(code=code, clean=True)
            self._inbox.put_nowait(None)

    def agent_abort(self) -> None:
        if self._info is None:
            self._info = CloseInfo(code=1006, clean=False)
            self._inbox.put_nowait(None)


async def _agent(
    scenario: FakeAgentScenario, dialect: Dialect, clock: Clock, wire: _FakeAgentWire
) -> None:
    async def drain() -> None:
        while (await wire._outbox.get()) is not None:
            pass

    reader = asyncio.ensure_future(drain())
    try:
        for step in scenario.steps:
            if step.kind == "wait":
                await clock.sleep_until_ns(clock.monotonic_ns() + step.ms * 1_000_000)
            elif step.kind in ("speak", "silence", "comfort"):
                start = clock.monotonic_ns()
                for index in range(step.frames):
                    wire.agent_send(_agent_audio(dialect, step.level, step.kind))
                    await clock.sleep_until_ns(start + (index + 1) * FRAME_NS)
            elif step.kind == "garbage":
                rng = random.Random(step.seed)  # noqa: S311 - a seeded test signal, not a secret
                wire.agent_send(b"\xff" * 10 + bytes(rng.randrange(256) for _ in range(step.size)))
            elif step.kind == "hang_up":
                wire.agent_close(step.code)
                return
            elif step.kind == "abort":
                wire.agent_abort()
                return
        await asyncio.Event().wait()  # the scenario is over; the agent stays on the line
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


def connect_fake_agent(
    scenario: FakeAgentScenario, dialect: Dialect, clock: Clock
) -> Callable[[], Awaitable[WireConnection]]:
    """A connector for ``run_call`` that starts the fake agent on connect."""

    async def connect() -> WireConnection:
        wire = _FakeAgentWire()
        wire.task = asyncio.ensure_future(_agent(scenario, dialect, clock, wire))
        return wire

    return connect
