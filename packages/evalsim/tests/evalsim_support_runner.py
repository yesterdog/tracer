# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch E evalsim tests (the call runner, P4-21). Support module, not
collected. Contract: docs/phase4/api-surface-batch-e.md section 3.

A call is played against a deterministic fake agent (``evalsim_support_agents``) over an in-memory
connection (``evalsim_support_wire``) on the virtual clock (``evalsim_support_clock``): no socket,
no wall clock, the same numbers on every run and every Python version. The virtual call starts at
``START_NS`` (the instant the connection is made when the connector takes no time), so every
expected instant below is written in milliseconds after the origin of the recording.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    FakeAgent,
    ProtobufAgentCodec,
    TwilioAgentCodec,
)
from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

MS = 1_000_000
FRAME_NS = 20 * MS
ORIGIN = START_NS


def ms(value: int) -> int:
    return value * MS


def mod(name: str) -> Any:
    return importlib.import_module(f"tracer_evalsim.{name}")


def core(name: str) -> Any:
    return importlib.import_module(f"tracer_evalcore.{name}")


def dialect_of(kind: str) -> Any:
    if kind == "protobuf":
        return mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000)
    return mod("dialects.twilio").TwilioMediaStreamDialect(counter=1)


def codec_of(kind: str) -> Any:
    return ProtobufAgentCodec(16000) if kind == "protobuf" else TwilioAgentCodec()


def audio_for(dialect: Any, frames: int, level: int = 1) -> bytes:
    """``frames`` 20 ms frames of a constant non-zero signal in the wire codec of ``dialect``."""
    if dialect.wire_codec == "pcm_s16le":
        return bytes(level.to_bytes(2, "little", signed=True) * (dialect.frame_bytes // 2) * frames)
    return bytes(b"\x55" * dialect.frame_bytes * frames)


def lines(
    dialect: Any,
    *frame_counts: int,
    final: Any = None,
    mode: str = "scripted",
    texts: Sequence[str] | None = None,
) -> Any:
    """A scripted line provider: one pre-synthesised line per entry of ``frame_counts``."""
    runner = mod("runner")
    entries = [
        mod("lines").SynthesisedLine(texts[k] if texts else f"line {k}", audio_for(dialect, count))
        for k, count in enumerate(frame_counts)
    ]
    return mod("lines").PresynthesisedLines(
        mode,
        entries,
        final if final is not None else runner.CallerEnd("script_complete"),
        sample_rate_hz=dialect.sample_rate_hz,
        wire_codec=dialect.wire_codec,
    )


def settings(
    dialect: Any,
    *,
    wait_ms: int = 300,
    max_silence_ms: int = 3000,
    caller_first: bool = True,
    max_duration_ms: int = 60_000,
    barge_ins: dict[int, int] | None = None,
    **extra: Any,
) -> Any:
    policy = core("turntaking").TurnTakingPolicy(
        caller_speaks_first=caller_first,
        wait_for_agent_silence_ms=wait_ms,
        max_silence_ms=max_silence_ms,
    )
    return mod("runner").RunnerSettings(
        dialect=dialect,
        policy=policy,
        max_duration_ms=max_duration_ms,
        barge_ins=barge_ins or {},
        **extra,
    )


@dataclass
class Played:
    result: Any
    agent: FakeAgent
    clock: ManualClock
    client: Any
    events: list[tuple[str, int]] = field(default_factory=list)

    def at(self, instant_ns: int) -> int:
        """Milliseconds after the origin of the call."""
        return int((instant_ns - ORIGIN) // MS)

    def caller_ms(self) -> list[int]:
        return [self.at(f.t_ns) for f in self.result.caller_frames]

    def agent_ms(self) -> list[int]:
        return [self.at(f.t_ns) for f in self.result.agent_frames]


def play(
    kind: str,
    steps: Sequence[Any],
    line_provider: Any,
    *,
    stop_at_ms: int | None = None,
    connect_ms: int = 0,
    connect_error: Exception | None = None,
    segments: Callable[..., Awaitable[Any]] | None = None,
    segments_factory: Callable[[ManualClock], Callable[..., Awaitable[Any]]] | None = None,
    wire_factory: Callable[[], tuple[Any, Any]] | None = None,
    agent_runs: bool = True,
    prestop: bool = False,
    **settings_kw: Any,
) -> Played:
    """Place one virtual call of dialect ``kind`` ("protobuf" or "twilio") against the fake agent
    ``steps``. ``stop_at_ms`` raises the stop flag that long after the connection."""
    runner = mod("runner")
    dialect = dialect_of(kind)
    clock = ManualClock()
    if callable(line_provider) and not hasattr(line_provider, "next"):
        line_provider = line_provider(clock)  # a provider that needs the virtual clock
    if segments_factory is not None:
        segments = segments_factory(clock)
    agent = FakeAgent(codec_of(kind), tuple(steps))
    held: list[Any] = []
    tasks: list[asyncio.Future[Any]] = []
    stop = runner.StopFlag()
    if prestop:
        stop.request()

    async def connect() -> Any:
        if connect_ms:
            await clock.sleep_until_ns(clock.monotonic_ns() + ms(connect_ms))
        if connect_error is not None:
            raise connect_error
        client, remote = (wire_factory or InMemoryWire.pair)()
        held.append(client)
        if agent_runs:
            tasks.append(asyncio.ensure_future(agent.run(remote, clock)))
        return client

    async def stopper() -> None:
        assert stop_at_ms is not None
        await clock.sleep_until_ns(ORIGIN + ms(stop_at_ms))
        stop.request()

    async def main() -> Any:
        if stop_at_ms is not None:
            tasks.append(asyncio.ensure_future(stopper()))
        try:
            return await runner.run_call(
                settings(dialect, **settings_kw),
                clock=clock,
                connect=connect,
                lines=line_provider,
                stop=stop,
                segments=segments,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    result = run_virtual(clock, main)
    return Played(result, agent, clock, held[0] if held else None)


def received_frames(played: Played, kind: str) -> list[bytes]:
    """The caller audio the fake agent received, decoded by an independent reader."""
    import base64
    import json

    from evalsim_support_wire import ref_decode  # type: ignore[import-not-found,unused-ignore]

    out: list[bytes] = []
    for _, message in played.agent.received_log:
        if kind == "protobuf":
            assert isinstance(message, bytes)
            decoded = ref_decode(message)
            assert decoded is not None
            assert decoded[0] == "audio"
            out.append(decoded[1]["audio"])
        else:
            assert isinstance(message, str)
            body = json.loads(message)
            if body["event"] == "media":
                out.append(base64.b64decode(body["media"]["payload"]))
    return out


COUNT_KEYS = (
    "barge_ins_fired",
    "brain_invalid_outputs",
    "brain_regenerations",
    "brain_requests",
    "caller_turns_completed",
    "caller_turns_started",
    "frames_received",
    "frames_sent",
    "inbound_messages_ignored",
    "llm_requests",
    "signals_received",
)


class BlockedSendWire:
    """A client end whose ``send`` never completes until the connection is closed: a stalled
    write."""

    def __init__(self) -> None:
        self._closed = asyncio.Event()
        self._info: Any = None
        self.close_calls: list[int] = []
        self.sends = 0

    @property
    def close_info(self) -> Any:
        return self._info

    async def send(self, message: bytes | str) -> None:
        self.sends += 1
        if self.sends > 1:  # the first message (frame 0) goes through, the next one stalls
            await self._closed.wait()
            raise mod("errors").TransportError(code="connection_closed")

    async def recv(self) -> bytes | str | None:
        await self._closed.wait()
        return None

    async def close(self, code: int = 1000) -> None:
        self.close_calls.append(code)
        self._info = importlib.import_module("tracer_evalsim.dialects.base").CloseInfo(code, True)
        self._closed.set()


def stalled_pair() -> tuple[Any, Any]:
    return BlockedSendWire(), None
