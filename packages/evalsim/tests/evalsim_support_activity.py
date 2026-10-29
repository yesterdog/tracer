# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""Shared helpers of the batch activity evalsim tests (BL-313, the threshold ``activity_min_peak``).
Support module, not collected. Contract: docs/phase4/api-surface-batch-activity.md section 5.

A noise-floor agent: it sends a frame every 20 ms whose largest absolute sample is exactly the peak
the step names, so the pauses of the agent carry noise and its speech is louder. This is the shape
of the real agent of the field case (a low noise floor in every pause, never digital silence). The
agent is built with the independent reference of the wire (``ref_encode``) and the independent
G.711 table below (``ref_mulaw``, a copy of the one in ``evalcore_batcha_support``: the evalsim tests
run alone in some jobs, so they import nothing from the evalcore tests): never with the code under test. The call
runs on the virtual clock over an in-memory connection: no socket, no wall clock.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from dataclasses import dataclass, field
from typing import Any

from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import core, dialect_of, lines, mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire, ref_encode  # type: ignore[import-not-found,unused-ignore]

MS = 1_000_000
FRAME_NS = 20 * MS
ORIGIN = START_NS
KINDS = ["protobuf", "twilio", "webrtc"]


def ref_mulaw(code: int) -> int:
    """Segment form of the ITU-T G.711 mu-law decoder (14-bit value times 4).

    Code bits after inversion: sign (1 = negative), 3 segment bits, 4 mantissa bits. The
    magnitude of segment ``s``, mantissa ``m`` is ``4 * (((2 * m + 33) << s) - 33)``.
    """
    inverted = (~code) & 0xFF
    sign = inverted >> 7
    segment = (inverted >> 4) & 7
    mantissa = inverted & 15
    magnitude = 4 * (((2 * mantissa + 33) << segment) - 33)
    return -magnitude if sign else magnitude


def dialect_for(kind: str) -> Any:
    """The dialect of a kind: the two of the earlier tests and ``smallwebrtc/1`` at 48 kHz, whose
    messages are plain 16-bit PCM frames (the decoded audio the media stack hands over)."""
    if kind == "webrtc":
        return mod("dialects.smallwebrtc").SmallWebRtcDialect(sample_rate_hz=48000)
    return dialect_of(kind)


@dataclass(frozen=True)
class Wait:
    ms: int


@dataclass(frozen=True)
class Frames:
    """``count`` frames of 20 ms. ``peak`` is the largest absolute sample the simulator should see
    (for mu-law the nearest G.711 code, see ``decoded_peak``); ``code`` overrides the mu-law byte."""

    count: int
    peak: int
    code: int | None = None


@dataclass(frozen=True)
class Hang:
    code: int = 1000


def mulaw_code_for(value: int) -> int:
    """The G.711 byte whose decoded value is nearest to ``value`` (the first on a tie)."""
    return min(range(256), key=lambda c: (abs(ref_mulaw(c) - value), c))


def decoded_peak(kind: str, peak: int, code: int | None = None) -> int:
    """The peak the simulator sees for ``Frames(peak, code)``: after decoding, never the wire byte."""
    if kind in ("protobuf", "webrtc"):
        return peak
    chosen = mulaw_code_for(peak) if code is None else code
    return int(abs(ref_mulaw(chosen)))


def _message(kind: str, step: Frames) -> tuple[bytes | str, bytes]:
    """The wire message of one frame and its payload as the recording will hold it."""
    if kind in ("protobuf", "webrtc"):
        # quiet samples, then the loudest one (negative), then a half-level one: the peak is in
        # the middle of the frame, so a rule that reads the first sample or the mean fails
        half = 160 if kind == "protobuf" else 480
        samples = [1] * half + [-step.peak, step.peak // 2] + [1] * (half - 2)
        pcm = struct.pack(f"<{2 * half}h", *samples)
        if kind == "webrtc":
            return pcm, pcm
        message = ref_encode("audio", {"audio": pcm, "sample_rate": 16000, "num_channels": 1})
        return message, pcm
    code = mulaw_code_for(step.peak) if step.code is None else step.code
    payload = bytes([code]) * 160
    body = {
        "event": "media",
        "streamSid": "tracer-sim-stream-1",
        "media": {"payload": base64.b64encode(payload).decode("ascii")},
    }
    return json.dumps(body, separators=(",", ":")), payload


@dataclass
class Played:
    result: Any
    clock: ManualClock
    sent: list[tuple[int, bytes]] = field(default_factory=list)  # (ms after origin, payload)

    def at(self, instant_ns: int) -> int:
        return int((instant_ns - ORIGIN) // MS)

    def first_ms(self, turn: int) -> int:
        return self.at(self.result.turns[turn].first_frame_ns)


async def _drain(wire: Any) -> None:
    while (await wire.recv()) is not None:
        pass


async def _agent(
    kind: str, steps: tuple[Any, ...], wire: Any, clock: ManualClock, sent: list[Any]
) -> None:
    reader = asyncio.ensure_future(_drain(wire))
    try:
        for step in steps:
            if isinstance(step, Wait):
                await clock.sleep_until_ns(clock.monotonic_ns() + step.ms * MS)
            elif isinstance(step, Frames):
                start = clock.monotonic_ns()
                for index in range(step.count):
                    message, payload = _message(kind, step)
                    sent.append((int((clock.monotonic_ns() - ORIGIN) // MS), payload))
                    await wire.send(message)
                    await clock.sleep_until_ns(start + (index + 1) * FRAME_NS)
            elif isinstance(step, Hang):
                await wire.close(step.code)
                return
    finally:
        if not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def lines_of(kind: str, *frame_counts: int, texts: list[str] | None = None) -> Any:
    """A scripted line provider with one pre-synthesised line per entry (``script_complete`` after)."""
    return lines(dialect_for(kind), *frame_counts, texts=texts)


def play(
    kind: str,
    steps: list[Any],
    line_provider: Any,
    *,
    peak: int | None = None,
    caller_first: bool = True,
    wait_ms: int = 300,
    max_silence_ms: int = 3000,
    max_duration_ms: int = 60_000,
    barge_ins: dict[int, int] | None = None,
) -> Played:
    """One virtual call against the noise-floor agent ``steps``. ``peak=None`` leaves the member
    out of the policy (the default of the contract, 1)."""
    runner = mod("runner")
    dialect = dialect_for(kind)
    clock = ManualClock()
    extra: dict[str, Any] = {} if peak is None else {"activity_min_peak": peak}
    policy = core("turntaking").TurnTakingPolicy(
        caller_speaks_first=caller_first,
        wait_for_agent_silence_ms=wait_ms,
        max_silence_ms=max_silence_ms,
        **extra,
    )
    settings = runner.RunnerSettings(
        dialect=dialect,
        policy=policy,
        max_duration_ms=max_duration_ms,
        barge_ins=barge_ins or {},
    )
    sent: list[tuple[int, bytes]] = []
    tasks: list[asyncio.Future[Any]] = []

    async def connect() -> Any:
        client, remote = InMemoryWire.pair()
        tasks.append(asyncio.ensure_future(_agent(kind, tuple(steps), remote, clock, sent)))
        return client

    async def main() -> Any:
        try:
            return await runner.run_call(
                settings,
                clock=clock,
                connect=connect,
                lines=line_provider,
                stop=runner.StopFlag(),
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    result = run_virtual(clock, main)
    return Played(result, clock, sent)


#: the call of the field case: noise in every pause, speech (peak 4000) at 500 to 680 and at 1500
#: to 1680 ms, then noise to the end of the scenario (9,700 ms)
def field_steps() -> list[Any]:
    return [
        Frames(25, 250),
        Frames(10, 4000),
        Frames(40, 250),
        Frames(10, 4000),
        Frames(400, 250),
    ]
