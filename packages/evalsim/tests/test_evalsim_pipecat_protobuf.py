# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-16: the dialect ``pipecat-protobuf/1`` over an in-memory wire pair (ADR 0026 rules 5 and 22;
contract ``docs/phase4/api-surface-batch-d.md`` sections 3 and 4). Open no socket: the dialect
is exercised with the fake agents of ``evalsim_support_agents`` and the virtual clock.

What the agent sends is built by the independent reference encoder of ``evalsim_support_wire``;
what the client sends is decoded by it. Nothing here pins an item that ADR 0026 marks "not
verified" (the sample format of the proto, the sample rate a deployed bot expects).
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    FakeAgent,
    HangUp,
    Interruption,
    ProtobufAgentCodec,
    Send,
    Speak,
    Text,
    Wait,
)
from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire, ref_decode, ref_encode  # type: ignore[import-not-found,unused-ignore]

MS = 1_000_000
RATE = 16000


def dialect(rate: int = RATE) -> Any:
    return mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=rate)


def reader_for(d: Any, clock: Any, **caps: int) -> Any:
    return mod("dialects.base").InboundReader(d, clock, **caps)


def pcm(level: int, rate: int = RATE) -> bytes:
    return struct.pack("<h", level) * (rate * 20 // 1000)


def test_protobuf_dialect_roundtrip_in_memory() -> None:
    d = dialect()
    clock = ManualClock()
    client, remote = InMemoryWire.pair()
    sent = [pcm(100 * (i + 1)) for i in range(3)]
    steps = (
        Wait(40),
        Speak(3, level=1000),
        Text("hello"),
        Interruption(),
        Send(ref_encode("transcription", {"id": 3, "text": "t", "user_id": "u", "timestamp": "z"})),
        Send(ref_encode("message", {"data": "{}"})),
        HangUp(1000),
    )
    agent = FakeAgent(ProtobufAgentCodec(RATE), steps)
    events: list[Any] = []
    reader = reader_for(d, clock)

    async def main() -> Any:
        task = asyncio.ensure_future(agent.run(remote, clock))
        for index, frame in enumerate(sent):
            await client.send(d.encode_audio(frame, stream_ms=index * 20))
        end = await reader.run(client, events.append)
        await task
        return end

    end = run_virtual(clock, main)

    # client -> agent: three binary messages, each exactly the Frame{audio} the proto describes
    assert len(agent.received_log) == 3
    for (at, message), frame in zip(agent.received_log, sent, strict=True):
        assert at == 0
        assert isinstance(message, bytes)
        assert message == ref_encode(
            "audio", {"audio": frame, "sample_rate": RATE, "num_channels": 1}
        )
        member, fields = ref_decode(message) or ("", {})
        assert member == "audio"
        assert fields == {
            "id": 0,
            "name": "",
            "audio": frame,
            "sample_rate": RATE,
            "num_channels": 1,
            "pts": None,
        }
    # agent -> client: three audio events 20 ms apart from the 40 ms wait, then the other frames,
    # stamped with the injected clock; the non-audio events carry no payload at all
    got = [(e.kind, e.arrival_ns - START_NS, e.audio) for e in events]
    assert got == [
        ("audio", 40 * MS, pcm(1000)),
        ("audio", 60 * MS, pcm(1000)),
        ("audio", 80 * MS, pcm(1000)),
        ("text", 100 * MS, None),
        ("interruption", 100 * MS, None),
        ("transcription", 100 * MS, None),
        ("message", 100 * MS, None),
    ]
    stats = reader.stats
    assert (stats.messages, stats.audio_frames, stats.audio_bytes, stats.ignored) == (
        7,
        3,
        3 * len(pcm(1000)),
        0,
    )
    assert end.close_code == 1000
    assert end.clean is True


def test_protobuf_dialect_unknown_message_ignored_and_counted() -> None:
    d = dialect()
    clock = ManualClock()
    reader = reader_for(d, clock)
    good = ref_encode("audio", {"audio": pcm(7), "sample_rate": RATE, "num_channels": 1})
    ignored = [
        b"",  # a Frame with no member
        bytes.fromhex("4a02aabb"),  # an unknown top-level field (9)
        '{"type": "x"}',  # a WebSocket text message: not this dialect
        b"\xff" * 12,  # not a protobuf message at all
        bytes.fromhex("0a041a02fffe"),  # a text frame whose string is not UTF-8
        bytes.fromhex("0b"),  # a group
        # a text message whose characters, taken as bytes, WOULD be a valid empty text frame
        "\n\x00",
    ]
    seen: list[Any] = []
    for message in [*ignored[:3], good, *ignored[3:]]:
        seen.append(reader.feed(message))
    assert [e is None for e in seen] == [True, True, True, False, True, True, True, True]
    assert seen[3].kind == "audio"
    assert seen[3].audio == pcm(7)
    stats = reader.stats
    assert stats.ignored == len(ignored)
    assert stats.messages == len(ignored) + 1
    assert stats.audio_frames == 1
    assert stats.audio_bytes == len(pcm(7))
    # an ignored message is never interpreted: it produces no kind count
    assert stats.by_kind == {"audio": 1}


def test_protobuf_dialect_audio_encoding_is_the_exact_frame_bytes() -> None:
    d = dialect()
    frame = pcm(-2)
    message = d.encode_audio(frame, stream_ms=12345)
    assert isinstance(message, bytes)
    # tag 12, length, then audio (1a), sample_rate 16000 (20 80 7d) and num_channels 1 (28 01);
    # no id, no name, no pts: proto3 omits defaults and Tracer sets none
    assert message == bytes.fromhex("12") + _audio_member(frame)
    assert d.encode_audio(frame, stream_ms=0) == message  # the stream clock is not on the wire


def _audio_member(frame: bytes) -> bytes:
    inner = (
        bytes([0x1A])
        + _varint(len(frame))
        + frame
        + bytes.fromhex("20807d")
        + bytes.fromhex("2801")
    )
    return _varint(len(inner)) + inner


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def test_protobuf_dialect_has_no_opening_or_closing_message() -> None:
    d = dialect()
    assert d.opening_messages() == ()
    assert d.closing_messages() == ()
    assert d.dialect_id == "pipecat-protobuf/1"
    assert d.wire_codec == "pcm_s16le"
    assert d.sample_rate_hz == RATE
    assert d.frame_bytes == 640


@pytest.mark.parametrize("rate", [8000, 16000, 24000, 48000])
def test_protobuf_dialect_frame_is_twenty_ms_of_pcm(rate: int) -> None:
    d = dialect(rate)
    assert d.frame_bytes == rate * 20 // 1000 * 2
    ok = d.encode_audio(bytes(d.frame_bytes), stream_ms=0)
    assert isinstance(ok, bytes)
    for wrong in (
        b"",
        bytes(d.frame_bytes - 1),
        bytes(d.frame_bytes + 1),
        bytes(d.frame_bytes * 2),
    ):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            d.encode_audio(wrong, stream_ms=0)
    with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
        d.encode_audio(bytearray(d.frame_bytes), stream_ms=0)


@pytest.mark.parametrize("rate", [0, 7999, 48001, 16001, True, 16000.0, "16000", None, -16000])
def test_protobuf_dialect_requires_a_valid_sample_rate(rate: Any) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
        mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=rate)


def test_protobuf_dialect_sample_rate_has_no_default() -> None:
    with pytest.raises(TypeError):
        mod("dialects.protobuf_dialect").PipecatProtobufDialect()


def test_protobuf_dialect_refuses_agent_audio_at_another_rate_or_channel_count() -> None:
    """ADR 0027 rule 4: a frame at another rate or channel count is refused (never resampled)."""
    errors = mod("errors")
    cases = {
        "other_rate": {"audio": pcm(5, 8000), "sample_rate": 8000, "num_channels": 1},
        "stereo": {"audio": pcm(5) * 2, "sample_rate": RATE, "num_channels": 2},
        "no_rate": {"audio": pcm(5), "num_channels": 1},
        "no_channels": {"audio": pcm(5), "sample_rate": RATE},
    }
    for name, fields in cases.items():
        reader = reader_for(dialect(), ManualClock())
        with pytest.raises(errors.TransportError) as raised:
            reader.feed(ref_encode("audio", fields))
        assert raised.value.code == "audio_format_mismatch", name
        assert raised.value.END_REASON == "transport_error"
    # an odd number of bytes cannot be 16-bit PCM
    reader = reader_for(dialect(), ManualClock())
    with pytest.raises(errors.TransportError) as raised:
        reader.feed(
            ref_encode("audio", {"audio": b"\x01\x02\x03", "sample_rate": RATE, "num_channels": 1})
        )
    assert raised.value.code == "audio_frame_invalid"


def test_protobuf_dialect_silent_and_empty_audio_frames_are_audio_events() -> None:
    reader = reader_for(dialect(), ManualClock())
    event = reader.feed(
        ref_encode("audio", {"audio": bytes(640), "sample_rate": RATE, "num_channels": 1})
    )
    assert event is not None
    assert (event.kind, event.audio) == ("audio", bytes(640))  # activity is decided later (P4-05)
    empty = reader.feed(ref_encode("audio", {"audio": b"", "sample_rate": RATE, "num_channels": 1}))
    assert empty is not None
    assert (empty.kind, empty.audio) == ("audio", b"")


def test_protobuf_dialect_text_transcription_and_message_frames_keep_no_text() -> None:
    """Agent text never enters a record from the protocol (ADR 0027 rule 7, BL-275; ADR 0026
    rule 22): the event is a kind and an arrival instant, nothing else."""
    reader = reader_for(dialect(), ManualClock())
    secret = "SECRET-AGENT-WORDS"  # noqa: S105 - a test value, not a secret
    for member, fields, kind in (
        ("text", {"text": secret}, "text"),
        ("transcription", {"text": secret, "user_id": secret}, "transcription"),
        ("message", {"data": secret}, "message"),
        ("interruption", {"name": secret}, "interruption"),
    ):
        event = reader.feed(ref_encode(member, fields))
        assert event is not None
        assert event.kind == kind
        assert event.audio is None
        assert secret not in repr(event)
    assert secret not in repr(reader.stats)
