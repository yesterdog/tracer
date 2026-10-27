# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-16, P4-17: hostile and broken servers on the loopback interface (ADR 0026 rule 22: "a hostile
agent cannot exhaust the simulator"; ADR 0027 rule 4; contract
``docs/phase4/api-surface-batch-d.md``
sections 3 and 5).

Huge frames, a slow drip, the wrong dialect, close storms, binary garbage, frames after the close
and a dropped connection. Every test carries the ``loopback`` marker (named conftest change S-D1);
the servers are the test's own, on 127.0.0.1. No test here waits more than a few seconds, and none
depends on wall-clock timing for its verdict beyond "it ends".
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from collections.abc import Awaitable
from typing import Any

import pytest

from evalsim_support_agents import noise  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_servers import ServerWire, raw_server, wait_until, ws_server  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import ref_encode  # type: ignore[import-not-found,unused-ignore]

pytestmark = pytest.mark.loopback

RATE = 16000


def protobuf() -> Any:
    return mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=RATE)


def twilio() -> Any:
    d = mod("dialects.twilio").TwilioMediaStreamDialect(counter=1)
    d.opening_messages()
    return d


def entry_for(dialect: Any) -> Any:
    credentials = False if dialect.dialect_id.startswith("twilio") else None
    return mod("ws").ConnectionLabels(
        auth_token_env=None, origin=None, bot_holds_telephony_credentials=credentials
    )


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def connect(url: str, dialect: Any, **kwargs: Any) -> Any:
    ws = mod("ws")
    return await ws.open_websocket(
        ws.WebSocketTarget(url=url, labels=entry_for(dialect)),
        dialect,
        token=None,
        open_timeout_s=kwargs.pop("open_timeout_s", 3.0),
        close_timeout_s=kwargs.pop("close_timeout_s", 1.0),
        **kwargs,
    )


def reader(dialect: Any, **caps: int) -> Any:
    return mod("dialects.base").InboundReader(dialect, mod("clock").SystemClock(), **caps)


def audio_frame(level: int = 7) -> bytes:
    data: bytes = ref_encode(
        "audio",
        {
            "audio": struct.pack("<h", level) * (RATE * 20 // 1000),
            "sample_rate": RATE,
            "num_channels": 1,
        },
    )
    return data


async def serve_forever(wire: ServerWire) -> None:
    while await wire.recv() is not None:
        pass


# ------------------------------------------------------------------------------ huge frames


@pytest.mark.parametrize("size_factor", ["cap_plus_one", "ten_mib"])
def test_a_frame_over_the_cap_ends_the_call_with_transport_error(size_factor: str) -> None:
    limits = mod("limits")
    errors = mod("errors")
    size = (
        limits.MAX_INBOUND_MESSAGE_BYTES + 1 if size_factor == "cap_plus_one" else 10 * 1024 * 1024
    )

    async def hostile(wire: ServerWire) -> None:
        await wire.send(b"\xff" * size)
        await serve_forever(wire)

    async def main() -> Any:
        d = protobuf()
        async with ws_server(hostile) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            started = asyncio.get_running_loop().time()
            with pytest.raises(errors.TransportError) as raised:
                await reader(d).run(conn, lambda event: None)
            elapsed = asyncio.get_running_loop().time() - started
            await conn.close()
            return raised.value, elapsed

    err, elapsed = run(main())
    assert err.code == "message_too_large"
    assert err.END_REASON == "transport_error"
    assert err.close_code == 1009  # the connection was closed with "message too big"
    assert elapsed < 5.0


def test_a_message_of_exactly_the_cap_is_accepted() -> None:
    limits = mod("limits")

    async def hostile(wire: ServerWire) -> None:
        await wire.send(b"\xff" * limits.MAX_INBOUND_MESSAGE_BYTES)
        await wire.send(audio_frame())
        await wire.close(1000)

    async def main() -> Any:
        d = protobuf()
        events: list[Any] = []
        async with ws_server(hostile) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            rd = reader(d)
            end = await rd.run(conn, events.append)
            return events, rd.stats, end

    events, stats, end = run(main())
    assert [e.kind for e in events] == ["audio"]  # the cap-size garbage was ignored, not fatal
    assert stats.ignored == 1
    assert end.close_code == 1000


def test_a_text_message_over_the_cap_ends_a_twilio_call_too() -> None:
    limits = mod("limits")
    errors = mod("errors")

    async def hostile(wire: ServerWire) -> None:
        await wire.send("x" * (limits.MAX_INBOUND_MESSAGE_BYTES + 1))
        await serve_forever(wire)

    async def main() -> None:
        d = twilio()
        async with ws_server(hostile) as server:
            conn = await connect(f"{server.url}/ws", d)
            with pytest.raises(errors.TransportError) as raised:
                await reader(d).run(conn, lambda event: None)
            assert raised.value.code == "message_too_large"
            await conn.close()

    run(main())


def test_one_message_split_into_many_fragments_is_capped_as_a_whole() -> None:
    """A fragmented message is checked as it is assembled: 200 fragments of 1,000 bytes are over
    the cap before the last one arrives, so the client does not buffer it all."""
    errors = mod("errors")

    async def hostile(wire: ServerWire) -> None:
        await wire.send_fragmented([b"\xff" * 1000] * 200, gap_s=0)
        await serve_forever(wire)

    async def main() -> None:
        d = protobuf()
        async with ws_server(hostile) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            with pytest.raises(errors.TransportError) as raised:
                await reader(d).run(conn, lambda event: None)
            assert raised.value.code == "message_too_large"
            await conn.close()

    run(main())


def test_a_flood_of_small_messages_ends_the_call_at_the_rate_cap() -> None:
    errors = mod("errors")

    async def flood(wire: ServerWire) -> None:
        try:
            for _ in range(20000):
                await wire.send(b"\xff" * 12)
        except Exception:  # the client hung up on us, which is the expected end
            return

    async def main() -> Any:
        d = protobuf()
        async with ws_server(flood) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            rd = reader(d, max_messages_per_second=200)
            with pytest.raises(errors.TransportError) as raised:
                await rd.run(conn, lambda event: None)
            await conn.close()
            return raised.value, rd.stats

    err, stats = run(main())
    assert err.code == "inbound_rate_exceeded"
    assert stats.messages == 200  # exactly the messages inside the cap were counted
    assert stats.ignored == 200


# ------------------------------------------------------------------------------ slow servers


def test_a_valid_frame_dripped_in_many_fragments_is_one_event() -> None:
    frame = audio_frame(9)

    async def drip(wire: ServerWire) -> None:
        chunks = [frame[i : i + 16] for i in range(0, len(frame), 16)]
        await wire.send_fragmented(chunks, gap_s=0.005)
        await wire.close(1000)

    async def main() -> Any:
        d = protobuf()
        events: list[Any] = []
        async with ws_server(drip) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            await reader(d).run(conn, events.append)
        return events

    events = run(main())
    assert [(e.kind, len(e.audio or b"")) for e in events] == [("audio", 640)]


def test_hanging_up_while_the_server_drips_does_not_hang() -> None:
    """The slow drip that never completes: the watchdog's hang-up (``conn.close()``) returns within
    the close timeout and the reader ends; nothing waits for the message to finish."""

    async def endless(wire: ServerWire) -> None:
        async def parts() -> Any:
            for _ in range(10_000):
                yield b"\xff" * 4
                await asyncio.sleep(0.005)

        try:
            await wire.ws.send(parts())
        except Exception:  # the client closed on us
            return

    async def main() -> float:
        d = protobuf()
        async with ws_server(endless) as server:
            conn = await connect(f"{server.url}/ws-client", d, close_timeout_s=0.5)
            task = asyncio.ensure_future(reader(d).run(conn, lambda event: None))
            await asyncio.sleep(0.2)
            started = asyncio.get_running_loop().time()
            await conn.close()
            await asyncio.wait_for(task, timeout=5)
            return asyncio.get_running_loop().time() - started

    assert run(main()) < 4.0


def test_close_returns_within_the_close_timeout_when_the_server_never_answers() -> None:
    async def deaf(wire: ServerWire) -> None:
        wire.ws.transport.pause_reading()  # the client's close frame is never answered
        await asyncio.sleep(1)

    async def main() -> float:
        d = protobuf()
        async with ws_server(deaf) as server:
            conn = await connect(f"{server.url}/ws-client", d, close_timeout_s=0.3)
            started = asyncio.get_running_loop().time()
            await conn.close()
            elapsed = asyncio.get_running_loop().time() - started
            server.requests.clear()
            return elapsed

    assert run(main()) < 2.5


# ------------------------------------------------------------------------------ wrong dialect


def test_json_text_to_a_protobuf_client_is_ignored_and_counted() -> None:
    async def wrong(wire: ServerWire) -> None:
        for index in range(30):
            await wire.send(
                json.dumps({"label": "rtvi-message", "type": "bot-ready", "id": str(index)})
            )
        await wire.close(1000)

    async def main() -> Any:
        d = protobuf()
        events: list[Any] = []
        async with ws_server(wrong) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            rd = reader(d)
            await rd.run(conn, events.append)
        return events, rd.stats

    events, stats = run(main())
    assert events == []
    assert (stats.messages, stats.ignored, stats.audio_frames) == (30, 30, 0)


def test_protobuf_binary_to_a_twilio_client_is_ignored_and_counted() -> None:
    async def wrong(wire: ServerWire) -> None:
        for _ in range(30):
            await wire.send(audio_frame())
        await wire.close(1000)

    async def main() -> Any:
        d = twilio()
        events: list[Any] = []
        async with ws_server(wrong) as server:
            conn = await connect(f"{server.url}/ws", d)
            rd = reader(d)
            await rd.run(conn, events.append)
        return events, rd.stats

    events, stats = run(main())
    assert events == []
    assert (stats.messages, stats.ignored) == (30, 30)


def test_agent_words_never_reach_a_log_record(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR 0026 rule 22: agent text never reaches a log line (the dialect keeps no text at all)."""
    words = "SECRET-AGENT-WORDS"

    async def chatty(wire: ServerWire) -> None:
        await wire.send(ref_encode("text", {"text": words}))
        await wire.send(ref_encode("transcription", {"text": words, "user_id": words}))
        await wire.send(ref_encode("message", {"data": json.dumps({"x": words})}))
        await wire.send(json.dumps({"event": "mark", "mark": {"name": words}}))
        await wire.send(b"\xff" * 10 + words.encode())
        await wire.close(1000)

    async def main() -> Any:
        d = protobuf()
        events: list[Any] = []
        async with ws_server(chatty) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            rd = reader(d)
            await rd.run(conn, events.append)
        return events, rd.stats

    caplog.set_level(logging.DEBUG)
    events, stats = run(main())
    assert [e.kind for e in events] == ["text", "transcription", "message"]
    assert words not in repr(events) + repr(stats)
    assert words not in caplog.text
    captured = capsys.readouterr()
    assert words not in captured.out + captured.err


# ------------------------------------------------------------------------------ garbage and storms


def test_binary_garbage_is_ignored_and_counted_and_never_an_exception() -> None:
    async def garbage(wire: ServerWire) -> None:
        for seed in range(100):
            await wire.send(b"\xff" * 10 + noise(seed, 1 + (seed * 37) % 1500))
        await wire.close(1000)

    async def main() -> Any:
        d = protobuf()
        events: list[Any] = []
        async with ws_server(garbage) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            rd = reader(d)
            end = await rd.run(conn, events.append)
        return events, rd.stats, end

    events, stats, end = run(main())
    assert events == []
    assert (stats.messages, stats.ignored) == (100, 100)
    assert end.close_code == 1000


def test_a_storm_of_immediate_closes_leaves_nothing_behind() -> None:
    codes = [1000, 1001, 1002, 1008, 1011, 1013, 4000, 4003, 4999, 3000] * 2

    async def closer(wire: ServerWire) -> None:
        index = closer.count  # type: ignore[attr-defined]
        closer.count += 1  # type: ignore[attr-defined]
        await wire.close(codes[index])

    closer.count = 0  # type: ignore[attr-defined]

    async def main() -> list[Any]:
        before = set(asyncio.all_tasks())
        d = protobuf()
        ends = []
        async with ws_server(closer) as server:
            for _ in codes:
                conn = await connect(f"{server.url}/ws-client", d)
                ends.append(await reader(d).run(conn, lambda event: None))
            await wait_until(lambda: server.handled == len(codes))
        await asyncio.sleep(0.05)
        leaked = [
            t for t in asyncio.all_tasks() if t not in before and t is not asyncio.current_task()
        ]
        assert leaked == [], leaked
        return ends

    ends = run(main())
    assert [e.close_code for e in ends] == codes
    assert all(e.clean for e in ends)


def test_frames_written_after_the_close_frame_are_never_delivered() -> None:
    frame = audio_frame(3)

    def ws_frame(payload: bytes) -> bytes:  # an unmasked server-to-client binary frame
        assert 126 <= len(payload) < 65536
        return b"\x82\x7e" + struct.pack(">H", len(payload)) + payload

    close = b"\x88\x02" + struct.pack(">H", 1000)

    async def sneaky(wire: ServerWire) -> None:
        # one TCP write: a frame, the close frame, then two more frames the client must not see
        wire.ws.transport.write(ws_frame(frame) + close + ws_frame(frame) + ws_frame(frame))
        await asyncio.sleep(0.3)

    async def main() -> Any:
        d = protobuf()
        events: list[Any] = []
        async with ws_server(sneaky) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            rd = reader(d)
            end = await asyncio.wait_for(rd.run(conn, events.append), timeout=5)
        return events, rd.stats, end

    events, stats, end = run(main())
    assert [e.kind for e in events] == ["audio"]
    assert end.close_code == 1000
    assert stats.audio_frames == 1


def test_a_dropped_connection_mid_line_stops_the_sender_with_a_transport_error() -> None:
    errors = mod("errors")
    d = protobuf()

    async def vanish(wire: ServerWire) -> None:
        await asyncio.sleep(0.1)
        wire.abort()

    async def main() -> Any:
        async with ws_server(vanish) as server:
            conn = await connect(f"{server.url}/ws-client", d)
            clock = mod("clock").SystemClock()

            async def send_frame(frame: bytes) -> None:
                await conn.send(d.encode_audio(frame, stream_ms=0))

            sender = mod("sender").PacedSender(clock, send_frame)
            with pytest.raises(errors.TransportError) as raised:
                await asyncio.wait_for(sender.send_line([bytes(d.frame_bytes)] * 100), timeout=5)
            return raised.value, sender.log

    err, log = run(main())
    assert err.code in ("connection_closed", "connection_lost", "send_failed")
    assert err.code in errors_codes()
    assert 1 <= len(log) < 100  # some frames went out before the drop; not all
    assert all(r.sent_ns >= r.scheduled_ns for r in log)


def errors_codes() -> tuple[str, ...]:
    return tuple(mod("errors").TRANSPORT_CODES)


def test_a_server_that_closes_during_the_handshake_phase_is_not_a_call() -> None:
    errors = mod("errors")

    async def hang_up_at_once(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")  # take the upgrade request, answer nothing, drop

    async def main() -> Any:
        async with raw_server(hang_up_at_once) as server:
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(server.url + "/ws", protobuf(), open_timeout_s=2.0)
            return raised.value

    err = run(main())
    assert err.code in errors_codes_setup()
    assert err.END_REASON == "setup_failed"


def errors_codes_setup() -> tuple[str, ...]:
    return tuple(mod("errors").SETUP_CODES)
