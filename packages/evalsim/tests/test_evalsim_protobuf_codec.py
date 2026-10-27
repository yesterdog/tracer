# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-16: the hand-written protobuf codec of ``pipecat-protobuf/1`` (ADR 0026 rules 1, 3, 5;
contract ``docs/phase4/api-surface-batch-d.md`` section 4).

The golden vectors are HAND-COMPUTED hex (the arithmetic is in the comments), not output of the
code under test and not of ``google.protobuf`` (which no tracer package may import). A second,
differently written reference encoder (``evalsim_support_wire``) must reproduce them (a teeth
test), and a seeded property test compares codec and reference on 400 frames that cross every
varint length boundary. The field numbers come from ``frames.proto`` at tag ``v1.14.0`` as ADR
0026 records them; ``test_protobuf_codec_field_table_matches_adr_proto_facts`` compares the
reference table with the ADR text. Open no socket.
"""

from __future__ import annotations

import random
import re
from typing import Any

import pytest

from evalsim_support_imports import ADR26, adr_dialect_section, mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import ONEOF, PROTO, ref_decode, ref_encode  # type: ignore[import-not-found,unused-ignore]

U64 = 2**64 - 1
U32 = 2**32 - 1


def wire() -> Any:
    return mod("dialects.protobuf_wire")


# (id, class name, constructor kwargs, hex of the serialised top-level Frame)
#
# Wire format used by the arithmetic below: tag = (field_number << 3) | wire_type, wire type 0 is
# a varint and 2 is length-delimited; the top-level Frame is a oneof whose member is field 1 text,
# 2 audio, 3 transcription, 4 message, 5 interruption, written as tag, length, body.
GOLDEN: list[tuple[str, str, dict[str, Any], str]] = [
    # TextFrame: 08 01 (id=1) | 12 01 74 (name "t") | 1a 02 68 69 (text "hi") = 9 bytes = 0x09
    ("text", "TextFrame", {"id": 1, "name": "t", "text": "hi"}, "0a0908011201741a026869"),
    # an empty member is still present on the wire: tag 0a, length 0
    ("text_empty", "TextFrame", {}, "0a00"),
    # AudioRawFrame: 1a 04 01020304 (audio) | 20 80 7d (sample_rate 16000: 16000 = 125*128 + 0,
    # so 0x80 then 0x7d) | 28 01 (num_channels 1) = 6+3+2 = 11 = 0x0b
    (
        "audio",
        "AudioRawFrame",
        {"audio": b"\x01\x02\x03\x04", "sample_rate": 16000, "num_channels": 1},
        "120b1a040102030420807d2801",
    ),
    # id=300: 08 ac 02 (300 = 2*128 + 44 -> 0xac 0x02) | 12 01 61 | 1a 02 ff00 |
    # 20 c0 3e (8000 = 62*128 + 64 -> 0xc0 0x3e) | 28 01 | 30 00 (pts=0 IS written: optional) = 17
    (
        "audio_full",
        "AudioRawFrame",
        {
            "id": 300,
            "name": "a",
            "audio": b"\xff\x00",
            "sample_rate": 8000,
            "num_channels": 1,
            "pts": 0,
        },
        "121108ac02120161" + "1a02ff00" + "20c03e" + "2801" + "3000",
    ),
    # pts = 2**64-1 is ten bytes: nine 0xff and a 0x01; 30 + 10 bytes = 11 = 0x0b
    ("pts_max", "AudioRawFrame", {"pts": U64}, "120b30" + "ff" * 9 + "01"),
    # 48000 = 375*128 + 0 and 375 = 2*128 + 119: 0x80 0xf7 0x02; 20 80 f7 02 | 28 01 = 6
    (
        "rate_48000",
        "AudioRawFrame",
        {"sample_rate": 48000, "num_channels": 1},
        "12062080f7022801",
    ),
    # TranscriptionFrame: 08 07 | 1a 03 e0a4ae (U+092E, three UTF-8 bytes) | 22 02 75 31 (user_id
    # "u1") | 2a 01 74 (timestamp "t") = 2+5+4+3 = 14 = 0x0e
    (
        "transcription",
        "TranscriptionFrame",
        {"id": 7, "text": "\u092e", "user_id": "u1", "timestamp": "t"},
        "1a0e08071a03e0a4ae220275312a0174",
    ),
    # MessageFrame: 0a 07 + the seven characters of {"a":1} = 9 bytes = 0x09
    ("message", "MessageFrame", {"data": '{"a":1}'}, "22090a077b2261223a317d"),
    # InterruptionFrame: 08 05 | 12 01 78 = 5
    ("interruption", "InterruptionFrame", {"id": 5, "name": "x"}, "2a050805120178"),
    ("interruption_empty", "InterruptionFrame", {}, "2a00"),
    # a 200-byte name: 12 c8 01 + 200 bytes = 203 = 0xcb 0x01 as the member length
    ("name_200", "TextFrame", {"name": "x" * 200}, "0acb0112c801" + "78" * 200),
    # 16384 bytes of audio: 1a 80 80 01 (16384 = 1*16384) + 16384 = 16388 = 0x84 0x80 0x01
    (
        "audio_16384",
        "AudioRawFrame",
        {"audio": bytes(16384)},
        "12848001" + "1a808001" + "00" * 16384,
    ),
]


def frame_of(name: str, kwargs: dict[str, Any]) -> Any:
    return getattr(wire(), name)(**kwargs)


@pytest.mark.parametrize(("case", "name", "kwargs", "hexed"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_protobuf_codec_golden_vectors_encode(
    case: str, name: str, kwargs: dict[str, Any], hexed: str
) -> None:
    assert wire().encode_frame(frame_of(name, kwargs)).hex() == hexed


@pytest.mark.parametrize(("case", "name", "kwargs", "hexed"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_protobuf_codec_golden_vectors_decode(
    case: str, name: str, kwargs: dict[str, Any], hexed: str
) -> None:
    decoded = wire().decode_frame(bytes.fromhex(hexed))
    assert decoded == frame_of(name, kwargs)
    assert type(decoded).__name__ == name


@pytest.mark.parametrize(("case", "name", "kwargs", "hexed"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_protobuf_reference_encoder_reproduces_the_golden_vectors(
    case: str, name: str, kwargs: dict[str, Any], hexed: str
) -> None:
    """Teeth for the independent reference: it must give the hand-computed hex, so a wrong golden
    vector or a wrong reference cannot both pass unnoticed."""
    member = next(m for m, (_, message) in ONEOF.items() if message == name)
    assert ref_encode(member, kwargs).hex() == hexed
    decoded = ref_decode(bytes.fromhex(hexed))
    assert decoded is not None
    assert decoded[0] == member


def test_protobuf_codec_proto3_defaults_are_omitted_but_pts_has_presence() -> None:
    w = wire()
    # zero id, empty strings and empty bytes write nothing; pts None writes nothing, pts 0 does
    assert w.encode_frame(w.AudioRawFrame(sample_rate=1)).hex() == "12022001"
    assert w.encode_frame(w.AudioRawFrame(pts=0)).hex() == "12023000"
    assert w.encode_frame(w.AudioRawFrame()).hex() == "1200"
    assert w.decode_frame(bytes.fromhex("12023000")).pts == 0
    assert w.decode_frame(bytes.fromhex("1200")).pts is None


def _random_frames(count: int) -> list[Any]:
    w = wire()
    rng = random.Random(20261027)  # noqa: S311 - a seeded test signal, not a secret
    ints64 = [0, 1, 127, 128, 255, 300, 16383, 16384, 2**21 - 1, 2**21, 2**32, 2**63, U64]
    ints32 = [0, 1, 127, 128, 8000, 16000, 48000, 16383, 16384, 2**31, U32]
    texts = [
        "",
        "a",
        "hi",
        "x" * 127,
        "x" * 128,
        "\u092e\u0930\u093e\u0920\u0940",
        "\U0001f600",
        "\u200d",
    ]
    sizes = [0, 1, 2, 127, 128, 639, 640, 16383, 16384, 20000]
    frames: list[Any] = []
    for _ in range(count):
        kind = rng.choice(
            [
                "TextFrame",
                "AudioRawFrame",
                "TranscriptionFrame",
                "MessageFrame",
                "InterruptionFrame",
            ]
        )
        if kind == "TextFrame":
            frames.append(w.TextFrame(rng.choice(ints64), rng.choice(texts), rng.choice(texts)))
        elif kind == "AudioRawFrame":
            frames.append(
                w.AudioRawFrame(
                    rng.choice(ints64),
                    rng.choice(texts),
                    bytes(rng.randrange(256) for _ in range(rng.choice(sizes))),
                    rng.choice(ints32),
                    rng.choice(ints32),
                    rng.choice([None, *ints64]),
                )
            )
        elif kind == "TranscriptionFrame":
            frames.append(
                w.TranscriptionFrame(
                    rng.choice(ints64),
                    rng.choice(texts),
                    rng.choice(texts),
                    rng.choice(texts),
                    rng.choice(texts),
                )
            )
        elif kind == "MessageFrame":
            frames.append(w.MessageFrame(rng.choice(texts)))
        else:
            frames.append(w.InterruptionFrame(rng.choice(ints64), rng.choice(texts)))
    return frames


def _fields_of(frame: Any) -> dict[str, Any]:
    return {k: getattr(frame, k) for k in vars(frame)}


def test_protobuf_codec_matches_independent_reference_encoder_on_400_frames() -> None:
    w = wire()
    members = {message: member for member, (_, message) in ONEOF.items()}
    for frame in _random_frames(400):
        name = type(frame).__name__
        data = w.encode_frame(frame)
        assert data == ref_encode(members[name], _fields_of(frame)), frame
        assert w.decode_frame(data) == frame
        decoded = ref_decode(data)
        assert decoded is not None
        assert decoded[0] == members[name]
        for field, value in _fields_of(frame).items():
            assert decoded[1][field] == value


BAD_WIRE: list[tuple[str, bytes]] = [
    ("tag_without_length", bytes.fromhex("0a")),
    ("length_past_end", bytes.fromhex("0a0508")),
    ("truncated_tag_varint", bytes.fromhex("80")),
    ("truncated_length_varint", bytes.fromhex("0a80")),
    ("varint_longer_than_ten_bytes", b"\xff" * 11),
    # id varint whose tenth byte is 2: the value would be above 2**64-1
    ("uint64_overflow", bytes.fromhex("0a0b08" + "ff" * 9 + "02")),
    # the same overflow in a field Tracer does not know: skipped, but still not a varint
    ("unknown_field_varint_overflow", bytes.fromhex("0a0b78" + "ff" * 9 + "02")),
    ("start_group", bytes.fromhex("0b")),
    ("end_group", bytes.fromhex("0c")),
    ("wire_type_6", bytes.fromhex("0e")),
    ("wire_type_7", bytes.fromhex("0f")),
    ("field_number_zero", bytes.fromhex("00")),
    # text = ff fe is not UTF-8 (proto3 strings must be)
    ("invalid_utf8", bytes.fromhex("0a041a02fffe")),
    # sample_rate = 2**32 (varint 80 80 80 80 10) does not fit a uint32
    ("uint32_overflow", bytes.fromhex("1206208080808010")),
    # the audio field inside claims four bytes, one remains in the member
    ("nested_length_past_end", bytes.fromhex("12031a0401")),
    ("fixed64_truncated", bytes.fromhex("49010203")),
]


@pytest.mark.parametrize(("case", "data"), BAD_WIRE, ids=[b[0] for b in BAD_WIRE])
def test_protobuf_codec_decode_refuses_malformed_wire(case: str, data: bytes) -> None:
    w = wire()
    with pytest.raises(w.ProtobufDecodeError) as raised:
        w.decode_frame(data)
    error: Any = raised.value
    assert isinstance(error.code, str)
    assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error.code)
    assert isinstance(error, ValueError)
    # facts only: the message never quotes the bytes
    assert data.hex() not in str(raised.value) or len(data) < 2


def test_protobuf_codec_decode_tolerates_order_unknown_fields_and_last_member_wins() -> None:
    w = wire()
    golden_audio = w.AudioRawFrame(audio=b"\x01\x02\x03\x04", sample_rate=16000, num_channels=1)
    # fields in a different order
    assert (
        w.decode_frame(bytes.fromhex("120b" + "2801" + "20807d" + "1a0401020304")) == golden_audio
    )
    # unknown fields of every skippable wire type, inside the member and at the top level:
    #   78 05 (field 15 varint) | a2 01 02 aabb (field 20 bytes) | 4d 01020304 (field 9 fixed32)
    inner = "1a0401020304" + "20807d" + "2801" + "7805" + "a201" + "02aabb"
    data = bytes.fromhex("12" + f"{len(inner) // 2:02x}" + inner + "4d01020304" + "51" + "00" * 8)
    assert w.decode_frame(data) == golden_audio
    # two members on the wire: the last one wins
    text_empty, audio = bytes.fromhex("0a00"), w.encode_frame(golden_audio)
    assert w.decode_frame(text_empty + audio) == golden_audio
    assert w.decode_frame(audio + text_empty) == w.TextFrame()
    # nothing recognised is not an error: it is the "unknown" frame (the caller ignores and counts)
    assert w.decode_frame(b"") == w.UnknownFrame()
    assert w.decode_frame(bytes.fromhex("4a02aabb")) == w.UnknownFrame()
    # a bytearray and a memoryview decode like bytes
    assert w.decode_frame(bytearray(audio)) == golden_audio
    assert w.decode_frame(memoryview(audio)) == golden_audio


def test_protobuf_codec_encode_refuses_values_that_do_not_fit() -> None:
    w = wire()
    bad_frames: list[tuple[str, dict[str, Any]]] = [
        ("TextFrame", {"id": -1}),
        ("TextFrame", {"id": U64 + 1}),
        ("TextFrame", {"id": True}),
        ("TextFrame", {"text": b"x"}),
        ("AudioRawFrame", {"sample_rate": U32 + 1}),
        ("AudioRawFrame", {"num_channels": -1}),
        ("AudioRawFrame", {"pts": U64 + 1}),
        ("AudioRawFrame", {"pts": -1}),
        ("AudioRawFrame", {"audio": "text"}),
        ("MessageFrame", {"data": 5}),
    ]
    for name, kwargs in bad_frames:
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            w.encode_frame(getattr(w, name)(**kwargs))
    with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
        w.encode_frame(w.UnknownFrame())
    with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
        w.encode_frame(object())


def test_protobuf_codec_frames_are_frozen_value_objects() -> None:
    w = wire()
    frame = w.TextFrame(id=1, name="n", text="t")
    assert frame == w.TextFrame(id=1, name="n", text="t")
    assert hash(frame) == hash(w.TextFrame(id=1, name="n", text="t"))
    with pytest.raises(AttributeError):
        frame.id = 2
    assert [f for f in w.__all__ if f.endswith("Frame")] == sorted(
        f for f in w.__all__ if f.endswith("Frame")
    )


def test_protobuf_codec_field_table_matches_adr_proto_facts() -> None:
    """The reference table behind the golden vectors is the message set of ADR 0026, verbatim."""
    section = adr_dialect_section("pipecat-protobuf/1")
    for name, fields in PROTO.items():
        match = re.search(re.escape(name) + r" \{ ([^}]*) \}", section)
        assert match, f"{name} is not in the ADR section"
        parsed = [
            (n, ty, int(num))
            for ty, n, num in re.findall(r"((?:optional )?\w+) (\w+) = (\d+);", match.group(1))
        ]
        assert parsed == [(f, t, n) for f, t, n in fields], name
    oneof = re.search(r"Frame \{ oneof frame \{ ([^}]*) \} \}", section)
    assert oneof
    members = re.findall(r"(\w+) (\w+) = (\d+);", oneof.group(1))
    assert {m: (int(n), t) for t, m, n in members} == {
        member: (number, message) for member, (number, message) in ONEOF.items()
    }
    assert ADR26.is_file()
