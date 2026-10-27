# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-16, P4-17: the inbound reader of both WebSocket dialects (ADR 0026 rule 22: every inbound
message has a size cap, every call a cap on inbound messages per second; contract
``docs/phase4/api-surface-batch-d.md`` section 3). Open no socket.

``InboundReader.feed`` is the pure core (cap check, rate window, decode, arrival stamp); ``run``
reads a ``WireConnection`` until the peer closes. The caps are named constants of
``tracer_evalsim.limits`` ("technical bounds, not measured values") and are stated in the package
CHANGELOG.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    Abort,
    FakeAgent,
    HangUp,
    ProtobufAgentCodec,
    Speak,
    TwilioAgentCodec,
)
from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import ROOT, mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

NS = 1_000_000_000


def make_protobuf() -> Any:
    return mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000)


def make_twilio() -> Any:
    d = mod("dialects.twilio").TwilioMediaStreamDialect(counter=1)
    d.opening_messages()
    return d


DIALECTS: list[tuple[str, Callable[[], Any], Callable[[], Any]]] = [
    ("pipecat-protobuf/1", make_protobuf, lambda: ProtobufAgentCodec(16000)),
    ("twilio-media-stream/1", make_twilio, lambda: TwilioAgentCodec()),
]
PARAM = pytest.mark.parametrize(
    ("dialect_id", "make", "codec"), DIALECTS, ids=[d[0] for d in DIALECTS]
)


def reader(make: Callable[[], Any], clock: Any, **caps: int) -> Any:
    return mod("dialects.base").InboundReader(make(), clock, **caps)


def test_inbound_end_reasons_are_end_reasons_of_the_state_machine() -> None:
    base = mod("dialects.base")
    simtypes = __import__("tracer_evalcore.simtypes", fromlist=["END_REASONS"])
    clean = base.InboundEnd(clean=True, close_code=1000)
    dropped = base.InboundEnd(clean=False, close_code=None)
    assert clean.end_reason == "agent_hangup"
    assert dropped.end_reason == "transport_error"
    errors = mod("errors")
    for cls in (errors.SetupFailedError, errors.TransportError):
        assert cls.END_REASON in simtypes.END_REASONS
    assert {clean.end_reason, dropped.end_reason} <= set(simtypes.END_REASONS)


def test_inbound_caps_are_named_constants_and_stated_in_changelog() -> None:
    limits = mod("limits")
    for name in ("MAX_INBOUND_MESSAGE_BYTES", "MAX_INBOUND_MESSAGES_PER_SECOND"):
        value = getattr(limits, name)
        assert isinstance(value, int) and not isinstance(value, bool)  # noqa: PT018 - one fact in two clauses
        assert value > 0
    # one 20 ms frame at 48 kHz in 16-bit PCM is 1,920 bytes: the cap is well above one frame and
    # a hostile message cannot be larger than a few seconds of audio
    assert 1920 * 4 <= limits.MAX_INBOUND_MESSAGE_BYTES <= 1_048_576
    # 50 frames a second is real time; a buffered reply may be sent faster, a flood far faster
    assert 50 * 4 <= limits.MAX_INBOUND_MESSAGES_PER_SECOND <= 100_000
    changelog = (ROOT / "packages" / "evalsim" / "CHANGELOG.md").read_text(encoding="utf-8")
    for name in ("MAX_INBOUND_MESSAGE_BYTES", "MAX_INBOUND_MESSAGES_PER_SECOND"):
        match = re.search(re.escape(name) + r"[^\n]*?([0-9][0-9_,]*)", changelog)
        assert match, f"{name} is not stated in the CHANGELOG"
        assert int(re.sub(r"[_,]", "", match.group(1))) == getattr(limits, name)
    assert limits.FRAME_NS == 20_000_000
    assert limits.NS_PER_S == NS
    assert limits.WATCHDOG_GRACE_NS == NS


@PARAM
def test_inbound_reader_defaults_to_the_named_caps(dialect_id: str, make: Any, codec: Any) -> None:
    limits = mod("limits")
    r = reader(make, ManualClock())
    over = b"\x00" * (limits.MAX_INBOUND_MESSAGE_BYTES + 1)
    with pytest.raises(mod("errors").TransportError) as raised:
        r.feed(over if dialect_id.startswith("pipecat") else over.decode("ascii"))
    assert raised.value.code == "message_too_large"
    assert raised.value.END_REASON == "transport_error"


@PARAM
def test_inbound_reader_message_cap_is_exact_and_counts_bytes(
    dialect_id: str, make: Any, codec: Any
) -> None:
    cap = 1000
    r = reader(make, ManualClock(), max_message_bytes=cap)
    errors = mod("errors")
    # exactly the cap: accepted (here: ignored as unknown, which is still not an error)
    assert r.feed(b"\xff" * cap if dialect_id.startswith("pipecat") else "x" * cap) is None
    with pytest.raises(errors.TransportError) as raised:
        r.feed(b"\xff" * (cap + 1) if dialect_id.startswith("pipecat") else "x" * (cap + 1))
    assert raised.value.code == "message_too_large"
    # a text message is measured in UTF-8 bytes, not characters: 334 three-byte characters
    # (1,002 bytes) are 334 characters but over the cap
    r2 = reader(make, ManualClock(), max_message_bytes=cap)
    with pytest.raises(errors.TransportError):
        r2.feed("\u092e" * 334)
    assert r2.feed("\u092e" * 333) is None  # 999 bytes


@PARAM
def test_inbound_reader_rate_cap_is_a_sliding_one_second_window(
    dialect_id: str, make: Any, codec: Any
) -> None:
    errors = mod("errors")
    cap = mod("limits").MAX_INBOUND_MESSAGES_PER_SECOND
    audio = codec().audio(1 if dialect_id.startswith("pipecat") else 0x55)

    clock = ManualClock()
    r = reader(make, clock)
    for _ in range(cap):
        assert r.feed(audio) is not None
    with pytest.raises(errors.TransportError) as raised:
        r.feed(audio)  # the cap + 1st message inside one second
    assert raised.value.code == "inbound_rate_exceeded"
    assert raised.value.END_REASON == "transport_error"

    # one nanosecond short of a full second after the first message: still inside the window
    clock = ManualClock()
    r = reader(make, clock)
    for _ in range(cap):
        r.feed(audio)
    clock.advance_to(START_NS + NS - 1)
    with pytest.raises(errors.TransportError):
        r.feed(audio)

    # exactly one second later the first batch has left the window: a full batch fits again,
    # and the next one over the cap does not
    clock = ManualClock()
    r = reader(make, clock)
    for _ in range(cap):
        r.feed(audio)
    clock.advance_to(START_NS + NS)
    for _ in range(cap):
        assert r.feed(audio) is not None
    with pytest.raises(errors.TransportError):
        r.feed(audio)


@PARAM
def test_inbound_reader_garbage_counts_toward_the_rate_cap(
    dialect_id: str, make: Any, codec: Any
) -> None:
    r = reader(make, ManualClock(), max_messages_per_second=5)
    junk: Any = b"\xff" * 12 if dialect_id.startswith("pipecat") else "not json"
    for _ in range(5):
        assert r.feed(junk) is None
    with pytest.raises(mod("errors").TransportError) as raised:
        r.feed(junk)
    assert raised.value.code == "inbound_rate_exceeded"
    assert r.stats.ignored == 5


@PARAM
def test_inbound_reader_after_close_interprets_nothing_and_never_raises(
    dialect_id: str, make: Any, codec: Any
) -> None:
    clock = ManualClock()
    audio = codec().audio(1 if dialect_id.startswith("pipecat") else 0x55)
    r = reader(make, clock)
    assert r.feed(audio) is not None
    r.mark_closed()
    huge: Any = b"\x00" * 10_000_000 if dialect_id.startswith("pipecat") else "x" * 10_000_000
    assert r.feed(audio) is None
    assert r.feed(huge) is None  # frames after the close are not even size-checked
    stats = r.stats
    assert stats.after_close == 2
    assert stats.messages == 1
    assert stats.audio_frames == 1
    assert stats.ignored == 0


@PARAM
def test_inbound_reader_stamps_arrival_with_the_injected_clock(
    dialect_id: str, make: Any, codec: Any
) -> None:
    clock = ManualClock()
    r = reader(make, clock)
    c = codec()
    level = 1 if dialect_id.startswith("pipecat") else 0x55
    stamps = []
    for step in (0, 20_000_000, 20_000_001, 1_000_000_000):
        clock.advance_to(START_NS + step)
        event = r.feed(c.audio(level))
        assert event is not None
        stamps.append(event.arrival_ns)
    assert stamps == [START_NS, START_NS + 20_000_000, START_NS + 20_000_001, START_NS + NS]


@PARAM
def test_inbound_stats_are_a_frozen_snapshot(dialect_id: str, make: Any, codec: Any) -> None:
    r = reader(make, ManualClock())
    c = codec()
    level = 1 if dialect_id.startswith("pipecat") else 0x55
    r.feed(c.audio(level))
    before = r.stats
    r.feed(c.audio(level))
    assert before.audio_frames == 1
    assert r.stats.audio_frames == 2
    with pytest.raises(AttributeError):
        before.ignored = 5
    before.by_kind["audio"] = 99  # a copy: the reader's own count is unchanged
    assert r.stats.by_kind == {"audio": 2}


def _session(
    make: Callable[[], Any], codec: Callable[[], Any], steps: tuple[Any, ...], **caps: int
) -> tuple[list[Any], Any, Any, FakeAgent]:
    clock = ManualClock()
    client, remote = InMemoryWire.pair()
    agent = FakeAgent(codec(), steps)
    events: list[Any] = []
    rd = reader(make, clock, **caps)

    async def main() -> Any:
        task = asyncio.ensure_future(agent.run(remote, clock))
        try:
            return await rd.run(client, events.append)
        finally:
            await asyncio.gather(task, return_exceptions=True)

    return events, run_virtual(clock, main), rd, agent


@PARAM
def test_inbound_run_reports_a_clean_close_with_its_code(
    dialect_id: str, make: Any, codec: Any
) -> None:
    events, end, rd, _ = _session(make, codec, (Speak(2), HangUp(4003)))
    assert [e.kind for e in events] == ["audio", "audio"]
    assert (end.clean, end.close_code) == (True, 4003)  # the runner's code, e.g. a refused token
    # a peer that closes with a Close frame while the call runs is the end reason agent_hangup
    assert end.end_reason == "agent_hangup"
    assert rd.stats.after_close == 0


@PARAM
def test_inbound_run_reports_a_dropped_connection_as_not_clean(
    dialect_id: str, make: Any, codec: Any
) -> None:
    events, end, _, agent = _session(make, codec, (Speak(2), Abort()))
    assert agent.aborted
    assert len(events) == 2
    assert (end.clean, end.close_code) == (False, 1006)
    # a connection that vanished with no Close frame is a transport failure, not a hang-up
    assert end.end_reason == "transport_error"


@PARAM
def test_inbound_run_stops_at_the_rate_cap_after_delivering_what_came_before(
    dialect_id: str, make: Any, codec: Any
) -> None:
    clock = ManualClock()
    client, remote = InMemoryWire.pair()
    agent = FakeAgent(codec(), (Speak(25, burst=True), HangUp()))
    events: list[Any] = []
    rd = reader(make, clock, max_messages_per_second=10)

    async def main() -> None:
        task = asyncio.ensure_future(agent.run(remote, clock))
        try:
            with pytest.raises(mod("errors").TransportError) as raised:
                await rd.run(client, events.append)
            assert raised.value.code == "inbound_rate_exceeded"
        finally:
            await asyncio.gather(task, return_exceptions=True)

    run_virtual(clock, main)
    assert len(events) == 10  # exactly the messages inside the cap were delivered
