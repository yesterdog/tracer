# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-17: the dialect ``twilio-media-stream/1`` (ADR 0026 rule 6; contract
``docs/phase4/api-surface-batch-d.md`` sections 3 and 5). Open no socket.

The message set is the one ADR 0026 records from Twilio's Media Streams pages and the Pipecat
Twilio serializer at tag ``v1.14.0`` (read 2026-10-27): ``connected``, ``start``, ``media``,
``stop`` from Tracer as the telephony side; ``media``, ``clear`` and ``mark`` from the agent.
Nothing here pins what the ADR marks "not verified": in particular no test claims that Twilio
sends 160 bytes per ``media`` message (20 ms is Tracer's own pacing choice).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

import pytest

from evalsim_support_agents import FakeAgent, Mark, TwilioAgentCodec, Wait  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

MS = 1_000_000
ACCOUNT, STREAM, CALL = "tracer-sim-account-1", "tracer-sim-stream-1", "tracer-sim-call-1"


def dialect(counter: int = 1) -> Any:
    return mod("dialects.twilio").TwilioMediaStreamDialect(counter=counter)


def labels(**kwargs: Any) -> Any:
    base: dict[str, Any] = {
        "auth_token_env": None,
        "origin": None,
        "bot_holds_telephony_credentials": False,
    }
    return mod("ws").ConnectionLabels(**{**base, **kwargs})


CONNECTED = {"event": "connected", "protocol": "Call", "version": "1.0.0"}
START = {
    "event": "start",
    "sequenceNumber": "1",
    "streamSid": STREAM,
    "start": {
        "accountSid": ACCOUNT,
        "streamSid": STREAM,
        "callSid": CALL,
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"tracer_synthetic": "true"},
    },
}


def media(seq: int, chunk: int, timestamp: int, frame: bytes) -> dict[str, Any]:
    return {
        "event": "media",
        "sequenceNumber": str(seq),
        "streamSid": STREAM,
        "media": {
            "track": "inbound",
            "chunk": str(chunk),
            "timestamp": str(timestamp),
            "payload": base64.b64encode(frame).decode("ascii"),
        },
    }


def test_twilio_dialect_message_sequence_exact() -> None:
    d = dialect()
    assert (d.dialect_id, d.wire_codec, d.sample_rate_hz, d.frame_bytes) == (
        "twilio-media-stream/1",
        "mulaw",
        8000,
        160,
    )
    opening = d.opening_messages()
    assert len(opening) == 2
    frames = [bytes([0x10 + i]) * 160 for i in range(3)]
    # stream_ms is the time since the start of the stream, set by the runner (gaps between lines
    # are real time, so it is not frame number times 20)
    sent = [d.encode_audio(f, stream_ms=ms) for f, ms in zip(frames, (0, 20, 5100), strict=True)]
    closing = d.closing_messages()
    assert len(closing) == 1
    wire_messages = [*opening, *sent, *closing]
    # every message is a WebSocket TEXT message holding one JSON object
    assert all(isinstance(m, str) for m in wire_messages)
    parsed = [json.loads(m) for m in wire_messages]
    assert parsed == [
        CONNECTED,
        START,
        media(2, 1, 0, frames[0]),
        media(3, 2, 20, frames[1]),
        media(4, 3, 5100, frames[2]),
        {
            "event": "stop",
            "sequenceNumber": "5",
            "streamSid": STREAM,
            "stop": {"accountSid": ACCOUNT, "callSid": CALL},
        },
    ]
    # sequence numbers, chunk numbers and timestamps are strings (as Twilio's pages show them),
    # the sample rate and channel count are numbers
    assert isinstance(parsed[1]["sequenceNumber"], str)
    assert isinstance(parsed[2]["media"]["chunk"], str)
    assert isinstance(parsed[2]["media"]["timestamp"], str)
    assert parsed[1]["start"]["mediaFormat"]["sampleRate"] == 8000
    assert parsed[1]["start"]["mediaFormat"]["channels"] == 1
    # no `mark` and no `dtmf` is ever sent by Tracer
    assert {m["event"] for m in parsed} == {"connected", "start", "media", "stop"}


def test_twilio_dialect_stream_state_is_enforced() -> None:
    d = dialect()
    frame = bytes(160)
    with pytest.raises(RuntimeError):
        d.encode_audio(frame, stream_ms=0)  # media before start
    with pytest.raises(RuntimeError):
        d.closing_messages()  # stop before start
    d.opening_messages()
    with pytest.raises(RuntimeError):
        d.opening_messages()  # a second start
    d.encode_audio(frame, stream_ms=0)
    d.closing_messages()
    with pytest.raises(RuntimeError):
        d.encode_audio(frame, stream_ms=20)  # media after stop
    with pytest.raises(RuntimeError):
        d.closing_messages()  # a second stop


def test_twilio_dialect_audio_frame_is_exactly_twenty_ms_of_mulaw() -> None:
    d = dialect()
    d.opening_messages()
    for wrong in (b"", bytes(159), bytes(161), bytes(320)):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            d.encode_audio(wrong, stream_ms=0)
    d.encode_audio(bytes(160), stream_ms=0)


def test_twilio_dialect_ids_are_synthetic_and_marked() -> None:
    d = dialect(7)
    start = json.loads(d.opening_messages()[1])
    ids = {
        start["streamSid"],
        start["start"]["streamSid"],
        start["start"]["callSid"],
        start["start"]["accountSid"],
    }
    assert ids == {"tracer-sim-stream-7", "tracer-sim-call-7", "tracer-sim-account-7"}
    for value in ids:
        # the fixed prefix and a run-local counter, never the shape of a real provider SID
        assert re.fullmatch(r"tracer-sim-(?:account|stream|call)-[0-9]{1,9}", value)
        assert not re.fullmatch(r"[A-Za-z]{2}[0-9a-fA-F]{32}", value)
        assert not re.search(r"[0-9a-fA-F]{32}", value)
    assert (d.account_sid, d.stream_sid, d.call_sid) == (
        "tracer-sim-account-7",
        "tracer-sim-stream-7",
        "tracer-sim-call-7",
    )
    # a marker for the bot's operator, and no number of any kind
    assert start["start"]["customParameters"] == {"tracer_synthetic": "true"}
    # the same counter gives the same ids, another counter other ones
    again = json.loads(dialect(7).opening_messages()[1])
    assert again == start
    other = json.loads(dialect(8).opening_messages()[1])
    assert other["start"]["callSid"] == "tracer-sim-call-8"
    # every later message carries the stream id too
    media_message = json.loads(d.encode_audio(bytes(160), stream_ms=0))
    assert media_message["streamSid"] == "tracer-sim-stream-7"


@pytest.mark.parametrize("counter", [0, -1, True, "1", 1.0, None, 10**9])
def test_twilio_dialect_counter_must_be_a_positive_integer(counter: Any) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
        mod("dialects.twilio").TwilioMediaStreamDialect(counter=counter)


def test_twilio_dialect_counter_has_no_default() -> None:
    with pytest.raises(TypeError):
        mod("dialects.twilio").TwilioMediaStreamDialect()


def test_twilio_dialect_agent_clear_message_is_barge_in_evidence() -> None:
    clock = ManualClock()
    d = dialect()
    d.opening_messages()
    rd = mod("dialects.base").InboundReader(d, clock)
    c = TwilioAgentCodec()
    assert rd.feed(c.audio(0x55)) is not None
    clock.advance_to(START_NS + 40 * MS)
    clear = rd.feed(c.clear())
    assert clear is not None
    assert (clear.kind, clear.arrival_ns, clear.audio) == ("clear", START_NS + 40 * MS, None)
    clock.advance_to(START_NS + 1_000 * MS)
    rd.feed(c.clear())
    stats = rd.stats
    # the arrival instants of every clear, in order: the agent's own statement that it stopped
    assert stats.clear_arrival_ns == (START_NS + 40 * MS, START_NS + 1_000 * MS)
    assert stats.by_kind == {"audio": 1, "clear": 2}
    assert stats.ignored == 0


def test_twilio_dialect_clear_is_barge_in_evidence_only_in_this_dialect() -> None:
    """The protobuf dialect's interruption frame is an event, not a `clear` (rule 6)."""
    clock = ManualClock()
    rd = mod("dialects.base").InboundReader(
        mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000), clock
    )
    from evalsim_support_agents import ProtobufAgentCodec

    event = rd.feed(ProtobufAgentCodec(16000).interruption())
    assert event is not None
    assert event.kind == "interruption"
    assert rd.stats.clear_arrival_ns == ()


def test_twilio_dialect_decodes_agent_media_and_ignores_what_it_does_not_know() -> None:
    clock = ManualClock()
    d = dialect()
    d.opening_messages()
    rd = mod("dialects.base").InboundReader(d, clock)
    audio = bytes(range(160))
    good = json.dumps(
        {
            "event": "media",
            "streamSid": STREAM,
            "media": {"payload": base64.b64encode(audio).decode()},
        }
    )
    event = rd.feed(good)
    assert event is not None
    assert (event.kind, event.audio) == ("audio", audio)  # base64 decoded, kept as mu-law bytes
    # the payload of an agent's media message may be any size (Twilio: "can be of any size")
    big = json.dumps(
        {"event": "media", "media": {"payload": base64.b64encode(bytes(4000)).decode()}}
    )
    big_event = rd.feed(big)
    assert big_event is not None
    assert big_event.audio == bytes(4000)
    ignored: list[bytes | str] = [
        b"\x00\x01binary",  # a binary message is not this dialect
        "not json",
        "[1, 2]",  # JSON but not an object
        json.dumps({"event": "media", "media": {"payload": "not base64!!"}}),
        # characters outside the base64 alphabet are not skipped: the payload is refused whole
        json.dumps({"event": "media", "media": {"payload": "AAAA!!!!"}}),
        # a BINARY message is not this dialect even when its bytes are valid JSON
        json.dumps({"event": "clear", "streamSid": STREAM}).encode(),
        json.dumps({"event": "media", "media": {}}),
        json.dumps({"event": "media"}),
        json.dumps({"event": "mark", "mark": {}}),  # a mark without a name
        json.dumps(
            {"event": "connected", "protocol": "Call", "version": "1.0.0"}
        ),  # not agent-to-Twilio
        json.dumps({"event": "start", "start": {}}),
        json.dumps({"event": "stop", "stop": {}}),
        json.dumps({"event": "dtmf", "dtmf": {"digit": "1"}}),
        json.dumps({"event": 5}),
        json.dumps({"no_event": True}),
    ]
    for message in ignored:
        assert rd.feed(message) is None, message
    stats = rd.stats
    assert stats.ignored == len(ignored)
    assert stats.by_kind == {"audio": 2}


def test_twilio_dialect_mark_optional_never_awaited() -> None:
    """Marks are accepted and counted when they arrive, never required and never waited for."""

    def run(steps: tuple[Any, ...]) -> tuple[int, list[Any], list[str], list[int]]:
        clock = ManualClock()
        d = dialect()
        client, remote = InMemoryWire.pair()
        agent = FakeAgent(TwilioAgentCodec(), steps)
        rd = mod("dialects.base").InboundReader(d, clock)
        events: list[Any] = []
        sender_cls = mod("sender").PacedSender
        box: dict[str, int] = {}

        async def main() -> int:
            for message in d.opening_messages():
                await client.send(message)
            start = clock.monotonic_ns()
            box["t0"] = start

            async def send_frame(frame: bytes) -> None:
                await client.send(
                    d.encode_audio(frame, stream_ms=(clock.monotonic_ns() - start) // MS)
                )

            agent_task = asyncio.ensure_future(agent.run(remote, clock))
            reader_task = asyncio.ensure_future(rd.run(client, events.append))
            sender = sender_cls(clock, send_frame)
            await sender.send_line([bytes([i]) * 160 for i in range(5)])
            end: int = clock.monotonic_ns() - start
            await mod("dialects.base").hang_up(client, d)
            await asyncio.gather(agent_task, reader_task)
            return end

        elapsed = run_virtual(clock, main)
        kinds = [json.loads(m)["event"] for m in client.sent]
        return elapsed, events, kinds, list(clock.deadlines)

    quiet_elapsed, quiet_events, quiet_kinds, _ = run(())
    marked_elapsed, marked_events, marked_kinds, _ = run((Mark("before"), Wait(30), Mark("during")))
    # the call took exactly 4 frame times either way: no wait for a mark that never came, none
    # for marks that did come
    assert quiet_elapsed == marked_elapsed == 4 * 20 * MS
    assert [e.kind for e in quiet_events] == []
    assert [e.kind for e in marked_events] == ["mark", "mark"]
    # Tracer sent the same messages either way and never a mark, never an echo of one
    expected = ["connected", "start", "media", "media", "media", "media", "media", "stop"]
    assert quiet_kinds == marked_kinds == expected


def test_twilio_dialect_marks_are_counted_in_the_stats_with_no_payload() -> None:
    clock = ManualClock()
    d = dialect()
    d.opening_messages()
    rd = mod("dialects.base").InboundReader(d, clock)
    event = rd.feed(TwilioAgentCodec().mark("SECRET-MARK-NAME"))
    assert event is not None
    assert (event.kind, event.audio) == ("mark", None)
    assert "SECRET-MARK-NAME" not in repr(event)
    assert "SECRET-MARK-NAME" not in repr(rd.stats)
    assert rd.stats.by_kind == {"mark": 1}


def test_hang_up_sends_the_closing_messages_then_closes_in_memory() -> None:
    clock = ManualClock()
    d = dialect()
    client, remote = InMemoryWire.pair()
    agent = FakeAgent(TwilioAgentCodec(), ())  # an agent that ignores stop, as Pipecat does

    async def main() -> None:
        for message in d.opening_messages():
            await client.send(message)
        task = asyncio.ensure_future(agent.run(remote, clock))
        await client.send(d.encode_audio(bytes(160), stream_ms=0))
        await mod("dialects.base").hang_up(client, d)
        await asyncio.gather(task, return_exceptions=True)

    run_virtual(clock, main)
    ops = [op for op, _ in client.calls]
    assert ops == ["send", "send", "send", "send", "close"]
    stop = json.loads(client.calls[3][1])
    assert stop["event"] == "stop"
    assert client.calls[4] == ("close", 1000)
    assert clock.deadlines == []  # no waiting for the agent's reaction to the stop


def test_hang_up_with_the_protobuf_dialect_only_closes() -> None:
    clock = ManualClock()
    d = mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000)
    client, _remote = InMemoryWire.pair()

    async def main() -> None:
        await mod("dialects.base").hang_up(client, d, close_code=1001)

    run_virtual(clock, main)
    assert client.calls == [("close", 1001)]


def test_hang_up_when_the_peer_is_already_gone_still_closes_and_does_not_raise() -> None:
    clock = ManualClock()
    d = dialect()
    client, remote = InMemoryWire.pair()

    async def main() -> None:
        for message in d.opening_messages():
            await client.send(message)
        await remote.close(1000)  # the agent hung up first
        await mod("dialects.base").hang_up(client, d)

    run_virtual(clock, main)
    ops = [op for op, _ in client.calls]
    assert ops[-2:] == ["send", "close"]  # the stop was attempted (and failed), the close happened


# ---------------------------------------------------------------- the two credential refusals


def test_twilio_target_with_bot_credentials_refused() -> None:
    """ADR 0026 rule 6: a target whose entry says the bot holds telephony credentials is refused in
    Phase 4, at step 2, before any key is read and before any socket is opened (the guard of
    ``conftest.py`` turns any socket use into an ``AssertionError``, so this test passing proves
    that none was tried)."""
    ws = mod("ws")
    errors = mod("errors")
    risky = labels(bot_holds_telephony_credentials=True)
    with pytest.raises(errors.TargetRefusedError) as raised:
        ws.check_connection_labels("twilio-media-stream/1", risky)
    refusal: Any = raised.value
    assert refusal.code == "bot_holds_telephony_credentials"
    assert refusal.END_REASON is None
    assert isinstance(refusal, ValueError)  # an input refusal (exit 4), not a call failure

    # before any key read: the environment is not even looked at
    class Spy(dict[str, str]):
        reads = 0

        def get(self, key: str, default: Any = None) -> Any:
            Spy.reads += 1
            return super().get(key, default)

        def __getitem__(self, key: str) -> str:
            Spy.reads += 1
            return super().__getitem__(key)

    spy = Spy({"TRACER_TEST_TOKEN": "tok"})
    risky_with_token = labels(
        auth_token_env="TRACER_TEST_TOKEN",  # noqa: S106 - a name, not a secret
        bot_holds_telephony_credentials=True,
    )
    with pytest.raises(errors.TargetRefusedError) as raised2:
        ws.prepare_connection("twilio-media-stream/1", risky_with_token, environ=spy)
    assert raised2.value.code == "bot_holds_telephony_credentials"
    assert Spy.reads == 0

    # and through the connection function, before a socket
    target = ws.WebSocketTarget(url="ws://127.0.0.1:9/ws", labels=risky)
    with pytest.raises(errors.TargetRefusedError) as raised3:
        asyncio.run(
            ws.open_websocket(
                target, dialect(), token=None, open_timeout_s=1.0, close_timeout_s=1.0
            )
        )
    assert raised3.value.code == "bot_holds_telephony_credentials"
    # the operator's `false` is accepted by the check
    assert ws.check_connection_labels("twilio-media-stream/1", labels()) is None


def test_twilio_entry_without_credentials_field_refused() -> None:
    """An entry used with a ``twilio-media-stream/1`` target must carry the field: absent is
    refused (rule 16), the same way: step 2, no key, no socket."""
    ws = mod("ws")
    errors = mod("errors")
    absent = labels(bot_holds_telephony_credentials=None)
    with pytest.raises(errors.TargetRefusedError) as raised:
        ws.check_connection_labels("twilio-media-stream/1", absent)
    assert raised.value.code == "bot_credentials_field_missing"
    with pytest.raises(errors.TargetRefusedError) as raised2:
        ws.prepare_connection("twilio-media-stream/1", absent, environ={})
    assert raised2.value.code == "bot_credentials_field_missing"
    target = ws.WebSocketTarget(url="ws://127.0.0.1:9/ws", labels=absent)
    with pytest.raises(errors.TargetRefusedError) as raised3:
        asyncio.run(
            ws.open_websocket(
                target, dialect(), token=None, open_timeout_s=1.0, close_timeout_s=1.0
            )
        )
    assert raised3.value.code == "bot_credentials_field_missing"


def test_connection_labels_state_the_credentials_field_only_for_the_twilio_dialect() -> None:
    """ADR 0027 `connection`: the boolean is present iff the dialect is twilio-media-stream/1."""
    ws = mod("ws")
    errors = mod("errors")
    for dialect_id in ("pipecat-protobuf/1", "smallwebrtc/1"):
        assert (
            ws.check_connection_labels(dialect_id, labels(bot_holds_telephony_credentials=None))
            is None
        )
        for value in (True, False):
            with pytest.raises(errors.TargetRefusedError) as raised:
                ws.check_connection_labels(
                    dialect_id, labels(bot_holds_telephony_credentials=value)
                )
            assert raised.value.code == "labels_inconsistent"
    with pytest.raises(errors.TargetRefusedError) as raised:
        ws.check_connection_labels("not-a-dialect/1", labels())
    assert raised.value.code == "dialect_unknown"
