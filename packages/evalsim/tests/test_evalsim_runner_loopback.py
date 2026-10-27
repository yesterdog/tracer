# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 (steps 9 to 11) over a real WebSocket: the call runner against loopback fake agents.

Contract: docs/phase4/api-surface-batch-e.md section 3.5. The same runner as in
``test_evalsim_runner.py``, now on the system clock and a real ``websockets`` connection to a fake
agent that this test starts itself on 127.0.0.1 (the ``loopback`` marker, named spec change S-E1:
this module joins the declared list of ``test_evalsim_loopback_guard.py``). Real time passes (about
a second a test), so only properties that hold under any scheduling are asserted: the order, the end
reason, the counts, and that no frame ever leaves before its schedule.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    FakeAgent,
    HangUp,
    ProtobufAgentCodec,
    Send,
    Speak,
    TwilioAgentCodec,
    Wait,
)
from evalsim_support_runner import (  # type: ignore[import-not-found,unused-ignore]
    FRAME_NS,
    audio_for,
    core,
    dialect_of,
    lines,
    mod,
    settings,
)
from evalsim_support_servers import ServerWire, ws_server  # type: ignore[import-not-found,unused-ignore]

pytestmark = pytest.mark.loopback

KINDS = ["protobuf", "twilio"]


def codec_of(kind: str) -> Any:
    return ProtobufAgentCodec(16000) if kind == "protobuf" else TwilioAgentCodec()


def labels(kind: str) -> Any:
    return mod("ws").ConnectionLabels(None, None, False if kind == "twilio" else None)


async def place(
    kind: str, steps: list[Any], provider: Any, **options: Any
) -> tuple[Any, FakeAgent]:
    agent = FakeAgent(codec_of(kind), tuple(steps))
    dialect = dialect_of(kind)
    clock = mod("clock").SystemClock()

    async def handler(wire: ServerWire) -> None:
        await agent.run(wire, clock)
        await wire.ws.wait_closed()  # the scenario is over; the agent stays on the line

    async with ws_server(handler) as server:
        target = mod("ws").WebSocketTarget(f"{server.url}/ws", labels(kind))

        async def connect() -> Any:
            return await mod("ws").open_websocket(
                target, dialect, token=None, open_timeout_s=5, close_timeout_s=2
            )

        result = await mod("runner").run_call(
            settings(dialect, wait_ms=100, max_silence_ms=2000, max_duration_ms=20_000, **options),
            clock=clock,
            connect=connect,
            lines=provider,
            stop=mod("runner").StopFlag(),
        )
        await asyncio.sleep(0.05)
    return result, agent


@pytest.mark.parametrize("kind", KINDS)
def test_runner_against_a_loopback_fake_agent_over_a_real_websocket(kind: str) -> None:
    """The base call (two short lines, two replies) end to end on both dialects."""
    steps = [Wait(150), Speak(5), Wait(400), Speak(3)]
    result, agent = asyncio.run(place(kind, steps, lines(dialect_of(kind), 5, 5)))
    assert result.end_reason == "script_complete"
    assert result.call_established
    assert [(t.turn_seq, t.complete) for t in result.turns] == [(0, True), (1, True)]
    assert len(result.caller_frames) == 10
    assert len(result.agent_frames) == 8
    assert result.counts["frames_sent"] == 10
    assert result.counts["frames_received"] == 8
    assert result.counts["inbound_messages_ignored"] == 0
    # the agent received what was logged (the Twilio agent also saw connected, start and stop)
    assert (
        len([m for _, m in agent.received_log if "media" in str(m) or isinstance(m, bytes)]) >= 10
    )


@pytest.mark.parametrize("kind", KINDS)
def test_no_frame_ever_leaves_before_its_schedule_over_a_real_socket(kind: str) -> None:
    """ADR 0026 rule 8: frame k of a line leaves no earlier than the line's first frame plus k times
    20 ms, whatever the scheduler does (it may be late, it is never early, and never a burst)."""
    steps = [Wait(150), Speak(3)]
    result, _ = asyncio.run(place(kind, steps, lines(dialect_of(kind), 12)))
    first = result.turns[0]
    sent = [f.t_ns for f in result.caller_frames[: first.frames_sent]]
    assert len(sent) == 12
    for k, instant in enumerate(sent):
        assert instant - sent[0] >= k * FRAME_NS, k


@pytest.mark.parametrize("kind", KINDS)
def test_a_clean_hang_up_over_a_real_socket_is_agent_hangup_with_its_code(kind: str) -> None:
    result, _ = asyncio.run(
        place(kind, [Wait(100), Speak(3), HangUp(1001)], lines(dialect_of(kind), 5, 5))
    )
    assert result.end_reason == "agent_hangup"
    assert result.close_code == 1001


def test_a_message_over_the_library_cap_ends_the_call_over_a_real_socket() -> None:
    """ADR 0026 rule 22 with the real library: ``max_size`` closes with 1009 while the message is
    assembled, and the runner reports a transport error with the close code."""
    steps = [Wait(100), Send(b"\x00" * 100_000)]
    result, _ = asyncio.run(place("protobuf", steps, lines(dialect_of("protobuf"), 5)))
    assert result.end_reason == "transport_error"
    assert result.failure_code == "message_too_large"
    assert result.close_code == 1009


def test_a_refused_connection_over_a_real_socket_is_a_setup_failure() -> None:
    from evalsim_support_servers import closed_port  # type: ignore[import-not-found,unused-ignore]

    async def attempt() -> Any:
        dialect = dialect_of("protobuf")
        target = mod("ws").WebSocketTarget(f"ws://127.0.0.1:{closed_port()}/ws", labels("protobuf"))

        async def connect() -> Any:
            return await mod("ws").open_websocket(
                target, dialect, token=None, open_timeout_s=2, close_timeout_s=1
            )

        return await mod("runner").run_call(
            settings(dialect),
            clock=mod("clock").SystemClock(),
            connect=connect,
            lines=lines(dialect, 5),
            stop=mod("runner").StopFlag(),
        )

    result = asyncio.run(attempt())
    assert (result.end_reason, result.call_established) == ("setup_failed", False)
    assert result.failure_code == "connection_refused"
    assert core("simtypes").END_REASONS  # the closed list the reason belongs to
    assert result.connect_ended_ns >= result.connect_started_ns
    assert audio_for(dialect_of("protobuf"), 1)
