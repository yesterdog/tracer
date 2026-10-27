# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A hand-written protobuf (proto3) codec for the five frames of ``pipecat-protobuf/1``.

Written from the public wire-format description (varints, tags, length-delimited fields) and from
the message names, types and field numbers that ADR 0026 records for ``frames.proto`` at tag
``v1.14.0`` of ``pipecat-ai/pipecat`` (BSD-2-Clause, Copyright (c) 2024-2026, Daily; interface
facts only, no code and no file copied, nothing generated). One binary WebSocket message is one
serialised ``Frame``. Strict where the input is hostile: a truncated or over-long varint, a length
past the end, a group, an invalid UTF-8 string and a 32-bit field above 2**32-1 are refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tracer_evalsim.errors import EvalsimError

__all__ = [
    "AudioRawFrame",
    "Frame",
    "InterruptionFrame",
    "MessageFrame",
    "ProtobufDecodeError",
    "TextFrame",
    "TranscriptionFrame",
    "UnknownFrame",
    "decode_frame",
    "encode_frame",
]

_U32: Final = 2**32 - 1
_U64: Final = 2**64 - 1


class ProtobufDecodeError(EvalsimError, ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(f"ProtobufDecodeError: code {code}")
        self.code = code


@dataclass(frozen=True)
class TextFrame:
    id: int = 0
    name: str = ""
    text: str = ""


@dataclass(frozen=True)
class AudioRawFrame:
    id: int = 0
    name: str = ""
    audio: bytes = b""
    sample_rate: int = 0
    num_channels: int = 0
    pts: int | None = None


@dataclass(frozen=True)
class TranscriptionFrame:
    id: int = 0
    name: str = ""
    text: str = ""
    user_id: str = ""
    timestamp: str = ""


@dataclass(frozen=True)
class MessageFrame:
    data: str = ""


@dataclass(frozen=True)
class InterruptionFrame:
    id: int = 0
    name: str = ""


@dataclass(frozen=True)
class UnknownFrame:
    """A ``Frame`` with no recognised oneof member."""


Frame = (
    TextFrame | AudioRawFrame | TranscriptionFrame | MessageFrame | InterruptionFrame | UnknownFrame
)

# message class -> ((attribute, kind, field number), ...); kinds: u64, u32, str, bytes, opt_u64
_FIELDS: Final = {
    TextFrame: (("id", "u64", 1), ("name", "str", 2), ("text", "str", 3)),
    AudioRawFrame: (
        ("id", "u64", 1),
        ("name", "str", 2),
        ("audio", "bytes", 3),
        ("sample_rate", "u32", 4),
        ("num_channels", "u32", 5),
        ("pts", "opt_u64", 6),
    ),
    TranscriptionFrame: (
        ("id", "u64", 1),
        ("name", "str", 2),
        ("text", "str", 3),
        ("user_id", "str", 4),
        ("timestamp", "str", 5),
    ),
    MessageFrame: (("data", "str", 1),),
    InterruptionFrame: (("id", "u64", 1), ("name", "str", 2)),
}
_MEMBER: Final = {
    TextFrame: 1,
    AudioRawFrame: 2,
    TranscriptionFrame: 3,
    MessageFrame: 4,
    InterruptionFrame: 5,
}
_BY_NUMBER: Final = {number: cls for cls, number in _MEMBER.items()}


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _uint(value: object, limit: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
        raise ValueError(f"{name} is out of range")
    return value


def _encode_message(frame: object) -> bytes:
    out = bytearray()
    for attr, kind, number in _FIELDS[type(frame)]:
        value = getattr(frame, attr)
        if kind in ("u64", "u32"):
            value = _uint(value, _U64 if kind == "u64" else _U32, attr)
            if value:
                out += _varint(number << 3) + _varint(value)
        elif kind == "opt_u64":
            if value is not None:
                out += _varint(number << 3) + _varint(_uint(value, _U64, attr))
        elif kind == "str":
            if not isinstance(value, str):
                raise ValueError(f"{attr} is not a string")
            if value:
                raw = value.encode("utf-8")
                out += _varint((number << 3) | 2) + _varint(len(raw)) + raw
        else:
            if not isinstance(value, bytes | bytearray | memoryview):
                raise ValueError(f"{attr} is not bytes")
            if len(value):
                out += _varint((number << 3) | 2) + _varint(len(value)) + bytes(value)
    return bytes(out)


def encode_frame(frame: Frame) -> bytes:
    """Serialise a ``Frame`` with the given member set (an empty member is still present)."""
    if type(frame) not in _MEMBER:
        raise ValueError("not an encodable frame")
    body = _encode_message(frame)
    return _varint((_MEMBER[type(frame)] << 3) | 2) + _varint(len(body)) + body


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for index in range(10):
        if pos >= len(data):
            raise ProtobufDecodeError("truncated_varint")
        byte = data[pos]
        pos += 1
        if index == 9 and byte > 1:
            raise ProtobufDecodeError("varint_too_long")
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, pos
    raise ProtobufDecodeError("varint_too_long")


def _walk(data: bytes) -> list[tuple[int, int, int | bytes]]:
    pos = 0
    found: list[tuple[int, int, int | bytes]] = []
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ProtobufDecodeError("invalid_tag")
        value: int | bytes
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 2:
            size, pos = _read_varint(data, pos)
            if size > len(data) - pos:
                raise ProtobufDecodeError("length_overrun")
            value, pos = data[pos : pos + size], pos + size
        elif wire == 1 or wire == 5:
            width = 8 if wire == 1 else 4
            if width > len(data) - pos:
                raise ProtobufDecodeError("length_overrun")
            value, pos = data[pos : pos + width], pos + width
        else:
            raise ProtobufDecodeError("unsupported_wire_type")
        found.append((number, wire, value))
    return found


def _decode_message(cls: type, body: bytes) -> object:
    values: dict[str, object] = {}
    spec = {number: (attr, kind) for attr, kind, number in _FIELDS[cls]}
    for number, wire, value in _walk(body):
        if number not in spec:
            continue
        attr, kind = spec[number]
        if kind in ("u64", "u32", "opt_u64"):
            if wire != 0:
                continue
            if not isinstance(value, int):
                raise ProtobufDecodeError("invalid_tag")
            limit = _U32 if kind == "u32" else _U64
            if value > limit:
                raise ProtobufDecodeError("value_out_of_range")
            values[attr] = value
        else:
            if wire != 2:
                continue
            if not isinstance(value, bytes):
                raise ProtobufDecodeError("invalid_tag")
            if kind == "str":
                try:
                    values[attr] = value.decode("utf-8")
                except UnicodeDecodeError:
                    raise ProtobufDecodeError("invalid_utf8") from None
            else:
                values[attr] = value
    return cls(**values)


def decode_frame(data: bytes | bytearray | memoryview) -> Frame:
    """Parse one serialised ``Frame``; the last oneof member on the wire wins."""
    raw = bytes(data)
    chosen: tuple[type, bytes] | None = None
    for number, wire, value in _walk(raw):
        if number in _BY_NUMBER and wire == 2 and isinstance(value, bytes):
            chosen = (_BY_NUMBER[number], value)
    if chosen is None:
        return UnknownFrame()
    return _decode_message(chosen[0], chosen[1])  # type: ignore[return-value]
