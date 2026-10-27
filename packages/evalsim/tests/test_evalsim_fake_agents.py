# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-19: the no-network fake agents (ADR 0026 rule 23: "deterministic fake agents, pure Python,
exist for each dialect with injected reply delays, silence, comfort noise, hang-up, garbage frames
and bursts") and the virtual clock they run on. Open no socket.

The fake agents and the clock are test support (``evalsim_support_agents``,
``evalsim_support_clock``,
``evalsim_support_wire``); this file is their acceptance. A fake agent that was not deterministic
would make every timing test flaky, so determinism is checked three ways: the same script gives
byte-identical messages at identical virtual instants; the source of the support modules reads no
wall clock and no unseeded randomness; and each step type does exactly what its name says.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    Abort,
    Clear,
    Comfort,
    FakeAgent,
    Garbage,
    HangUp,
    Interruption,
    Mark,
    ProtobufAgentCodec,
    Send,
    Silence,
    Speak,
    Text,
    TwilioAgentCodec,
    Wait,
    noise,
)
from evalsim_support_clock import START_NS, ManualClock, run_for, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire, ref_decode  # type: ignore[import-not-found,unused-ignore]

MS = 1_000_000
HERE = Path(__file__).resolve().parent
SUPPORT = ("evalsim_support_agents.py", "evalsim_support_clock.py", "evalsim_support_wire.py")

SCRIPT = (
    Wait(60),
    Speak(3),
    Silence(2),
    Comfort(2),
    Garbage(40, seed=3),
    Clear(),
    Mark("m1"),
    Interruption(),
    Text("hello"),
    Speak(2, burst=True),
    HangUp(1001),
)


def play(codec: Any, steps: tuple[Any, ...]) -> FakeAgent:
    clock = ManualClock()
    client, remote = InMemoryWire.pair()
    agent = FakeAgent(codec, steps)
    got: list[Any] = []

    async def main() -> None:
        task = asyncio.ensure_future(agent.run(remote, clock))
        while (message := await client.recv()) is not None:
            got.append(message)
        await task

    run_virtual(clock, main)
    assert [m for _, m in agent.sent_log] == got
    return agent


def _dialect_accepts_what_the_fake_agent_says(codec: Any) -> None:
    """P4-19 acceptance: a fake agent exists for each dialect and what it sends is accepted by
    that dialect: speech, silence and comfort noise are audio, a hang-up request is an event of
    the dialect, garbage and foreign text are ignored and counted."""
    if isinstance(codec, ProtobufAgentCodec):
        dialect = mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000)
    else:
        dialect = mod("dialects.twilio").TwilioMediaStreamDialect(counter=1)
        dialect.opening_messages()
    reader = mod("dialects.base").InboundReader(dialect, ManualClock())
    agent = play(codec, SCRIPT)
    kinds = [getattr(reader.feed(message), "kind", None) for _, message in agent.sent_log]
    clear_kind = "interruption" if isinstance(codec, ProtobufAgentCodec) else "clear"
    expected = ["audio"] * 7 + [
        None,
        clear_kind,
        "message" if isinstance(codec, ProtobufAgentCodec) else "mark",
    ]
    expected += ["interruption" if isinstance(codec, ProtobufAgentCodec) else "clear"]
    expected += ["text" if isinstance(codec, ProtobufAgentCodec) else None]
    expected += ["audio"] * 2
    assert kinds == expected
    assert reader.stats.ignored == expected.count(None)


@pytest.mark.parametrize(
    "codec", [ProtobufAgentCodec(16000), TwilioAgentCodec()], ids=["protobuf", "twilio"]
)
def test_fake_agents_are_deterministic(codec: Any) -> None:
    first = play(codec, SCRIPT)
    second = play(codec, SCRIPT)
    assert first.sent_log == second.sent_log  # same bytes at the same virtual nanosecond
    assert first.sent_log  # not vacuous
    times = [at for at, _ in first.sent_log]
    assert times == sorted(times)
    # the schedule the steps promise: a 60 ms wait, 3 speech frames paced at 20 ms (60, 80, 100),
    # 2 digital-silence frames (120, 140), 2 comfort frames (160, 180), then single messages with
    # no delay (garbage, clear, mark, interruption, text at 200), then 2 burst frames at 200
    assert [t // MS for t in times] == [60, 80, 100, 120, 140, 160, 180] + [200] * 7
    # another seed gives other garbage; the same seed the same garbage
    other = play(codec, (Garbage(40, seed=4), HangUp()))
    assert other.sent_log[0][1] != first.sent_log[7][1]
    assert noise(3, 40) == noise(3, 40)
    assert noise(3, 40) != noise(4, 40)
    # and the dialect under test accepts what the agent sends (P4-19: used by every unit test)
    _dialect_accepts_what_the_fake_agent_says(codec)


def test_fake_agent_steps_do_what_their_names_say_protobuf() -> None:
    agent = play(ProtobufAgentCodec(16000), SCRIPT)
    messages = [m for _, m in agent.sent_log]
    audio = [ref_decode(m) for m in messages[:7]]
    assert all(a is not None and a[0] == "audio" for a in audio)
    pcm = [a[1]["audio"] for a in audio if a is not None]
    assert all(len(p) == 640 for p in pcm)
    assert all(struct.unpack("<320h", p) == (1000,) * 320 for p in pcm[:3])  # speech
    assert all(p == bytes(640) for p in pcm[3:5])  # digital silence: every sample zero
    assert all(struct.unpack("<320h", p) == (1,) * 320 for p in pcm[5:7])  # comfort: tiny, not zero
    assert messages[7][:10] == b"\xff" * 10  # garbage is not a protobuf message
    assert len(messages[7]) == 50
    assert ref_decode(messages[8]) == ("interruption", {"id": 0, "name": ""})  # clear
    mark = ref_decode(messages[9])
    assert mark is not None and mark[0] == "message"  # noqa: PT018 - one fact in two clauses
    assert json.loads(mark[1]["data"]) == {"mark": "m1"}
    assert ref_decode(messages[10]) == ("interruption", {"id": 0, "name": ""})
    text = ref_decode(messages[11])
    assert text is not None and text[1]["text"] == "hello"  # noqa: PT018 - one fact in two clauses
    assert [ref_decode(m) is not None for m in messages[12:14]] == [True, True]
    assert agent.sent_log[-1][0] == agent.sent_log[-2][0]  # a burst: both at the same instant


def test_fake_agent_steps_do_what_their_names_say_twilio() -> None:
    agent = play(TwilioAgentCodec("tracer-sim-stream-9"), SCRIPT)
    messages = [m for _, m in agent.sent_log]
    payloads = [base64.b64decode(json.loads(m)["media"]["payload"]) for m in messages[:7]]
    assert all(len(p) == 160 for p in payloads)
    assert all(set(p) == {0x55} for p in payloads[:3])  # speech: non-zero mu-law
    assert all(set(p) == {0xFF} for p in payloads[3:5])  # 0xff decodes to a zero sample
    assert all(set(p) == {0xFE} for p in payloads[5:7])  # 0xfe decodes to a small non-zero sample
    assert json.loads(messages[0])["streamSid"] == "tracer-sim-stream-9"
    assert messages[7][:10] == b"\xff" * 10
    assert json.loads(messages[8]) == {"event": "clear", "streamSid": "tracer-sim-stream-9"}
    assert json.loads(messages[9]) == {
        "event": "mark",
        "mark": {"name": "m1"},
        "streamSid": "tracer-sim-stream-9",
    }
    assert json.loads(messages[10])["event"] == "clear"


def test_fake_agent_records_what_the_client_sent_and_when() -> None:
    clock = ManualClock()
    client, remote = InMemoryWire.pair()
    agent = FakeAgent(ProtobufAgentCodec(16000), (Wait(100), HangUp()))

    async def main() -> None:
        task = asyncio.ensure_future(agent.run(remote, clock))
        await client.send(b"one")
        await clock.sleep_until_ns(clock.monotonic_ns() + 40 * MS)
        await client.send("two")
        await task

    run_virtual(clock, main)
    assert agent.received_log == [(0, b"one"), (40 * MS, "two")]


def test_fake_agent_abort_drops_the_connection_without_a_close_frame() -> None:
    clock = ManualClock()
    client, remote = InMemoryWire.pair()
    agent = FakeAgent(TwilioAgentCodec(), (Speak(1), Abort()))

    async def main() -> Any:
        task = asyncio.ensure_future(agent.run(remote, clock))
        got = []
        while (message := await client.recv()) is not None:
            got.append(message)
        await task
        return got

    got = run_virtual(clock, main)
    assert len(got) == 1
    assert agent.aborted
    assert client.close_info is not None
    assert (client.close_info.code, client.close_info.clean) == (1006, False)


def test_fake_agent_raw_send_step_sends_exactly_the_given_message() -> None:
    agent = play(TwilioAgentCodec(), (Send("any text"), Send(b"\x00\x01"), HangUp()))
    assert [m for _, m in agent.sent_log] == ["any text", b"\x00\x01"]


def test_support_modules_read_no_wall_clock_and_no_unseeded_randomness() -> None:
    for name in SUPPORT:
        tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
        imported = {
            a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for a in n.names
        } | {
            n.module.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module
        }
        assert not imported & {"time", "secrets", "uuid", "datetime", "os", "socket", "ssl"}, name
        for found in ast.walk(tree):
            node: Any = found
            is_random = (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "random"
            )
            if is_random:
                assert node.attr == "Random", f"{name}: random.{node.attr}"
            seeded_call = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Random"
            )
            if seeded_call:
                assert node.args or node.keywords, f"{name}: random.Random() without a seed"


# --------------------------------------------------- acceptance of the virtual clock itself


def test_manual_clock_jumps_only_to_deadlines_and_wakes_sleepers_in_order() -> None:
    clock = ManualClock()
    order: list[tuple[str, int]] = []

    async def sleeper(name: str, at_ms: int) -> None:
        await clock.sleep_until_ns(START_NS + at_ms * MS)
        order.append((name, clock.monotonic_ns() - START_NS))

    async def main() -> None:
        await asyncio.gather(
            sleeper("c", 300), sleeper("a", 100), sleeper("b", 100), sleeper("d", 0)
        )

    run_virtual(clock, main)
    assert sorted(order, key=lambda x: (x[1], x[0])) == [
        ("d", 0),
        ("a", 100 * MS),
        ("b", 100 * MS),
        ("c", 300 * MS),
    ]
    assert clock.monotonic_ns() == START_NS + 300 * MS  # never past the last deadline
    assert sorted(clock.deadlines) == [START_NS + t * MS for t in (0, 100, 100, 300)]


def test_manual_clock_run_reports_a_stuck_run_instead_of_hanging() -> None:
    clock = ManualClock()

    async def main() -> None:
        await asyncio.Event().wait()

    with pytest.raises(AssertionError, match="stuck"):
        run_virtual(clock, main)


def test_manual_clock_run_for_stops_the_world_at_the_given_instant() -> None:
    clock = ManualClock()
    ticks: list[int] = []

    async def main() -> None:
        while True:
            await clock.sleep_until_ns(clock.monotonic_ns() + 30 * MS)
            ticks.append(clock.monotonic_ns() - START_NS)

    run_for(clock, main, until_ns=START_NS + 100 * MS)
    assert ticks == [30 * MS, 60 * MS, 90 * MS]
    assert clock.monotonic_ns() == START_NS + 100 * MS


def test_in_memory_wire_orders_messages_and_signals_close_to_both_ends() -> None:
    clock = ManualClock()
    a, b = InMemoryWire.pair()

    async def main() -> list[Any]:
        await a.send(b"1")
        await a.send("2")
        await b.send(b"x")
        out = [await b.recv(), await b.recv(), await a.recv()]
        await a.close(1000)
        out += [await b.recv(), await b.recv(), await a.recv()]
        return out

    assert run_virtual(clock, main) == [b"1", "2", b"x", None, None, None]
    assert a.calls[-1] == ("close", 1000)
    assert b.close_info is not None and b.close_info.code == 1000  # noqa: PT018 - one fact in two clauses
