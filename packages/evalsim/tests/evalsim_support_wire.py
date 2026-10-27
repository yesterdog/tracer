# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Wire-level helpers of the tracer-evalsim tests. Support module, not collected.

Three things, none of which imports a tracer module, ``google.protobuf`` or Pipecat:

* ``PROTO`` and ``ONEOF``: the message set of ``frames.proto`` at tag ``v1.14.0`` of
  ``pipecat-ai/pipecat`` as ADR 0026 (section "Dialect specifications", ``pipecat-protobuf/1``)
  records it. ``test_protobuf_codec_field_table_matches_adr_proto_facts`` compares this table with
  the ADR text, so a typo here cannot hide a wrong golden vector.
* ``ref_encode`` and ``ref_decode``: a SECOND, deliberately different implementation of the
  protobuf wire format (an independent encoder, written from the public wire-format description
  of varints, tags and length-delimited fields, driven by the table above). The golden vectors in
  ``test_evalsim_protobuf_codec.py`` are hand-computed hex; this reference reproduces them (a
  teeth test), and the fake agents use it to build what an agent sends.
* ``InMemoryWire``: a pair of ``WireConnection`` objects (``send``, ``recv``, ``close``,
  ``close_info``) joined by queues, so a dialect can be exercised with no socket at all.
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any

#: message name -> ((field name, proto type, number), ...)  (proto3; ``optional`` marks presence)
PROTO: dict[str, tuple[tuple[str, str, int], ...]] = {
    "TextFrame": (("id", "uint64", 1), ("name", "string", 2), ("text", "string", 3)),
    "AudioRawFrame": (
        ("id", "uint64", 1),
        ("name", "string", 2),
        ("audio", "bytes", 3),
        ("sample_rate", "uint32", 4),
        ("num_channels", "uint32", 5),
        ("pts", "optional uint64", 6),
    ),
    "TranscriptionFrame": (
        ("id", "uint64", 1),
        ("name", "string", 2),
        ("text", "string", 3),
        ("user_id", "string", 4),
        ("timestamp", "string", 5),
    ),
    "MessageFrame": (("data", "string", 1),),
    "InterruptionFrame": (("id", "uint64", 1), ("name", "string", 2)),
}
#: the oneof of the top-level ``Frame``: member name -> (field number, message name)
ONEOF: dict[str, tuple[int, str]] = {
    "text": (1, "TextFrame"),
    "audio": (2, "AudioRawFrame"),
    "transcription": (3, "TranscriptionFrame"),
    "message": (4, "MessageFrame"),
    "interruption": (5, "InterruptionFrame"),
}


def varint(value: int) -> bytes:
    """Base-128 little-endian varint of a non-negative integer."""
    assert value >= 0
    groups = []
    while True:
        groups.append(value & 0x7F)
        value >>= 7
        if not value:
            break
    return bytes(g | 0x80 for g in groups[:-1]) + bytes([groups[-1]])


def tag(number: int, wire_type: int) -> bytes:
    return varint((number << 3) | wire_type)


def ref_encode_message(name: str, values: dict[str, Any]) -> bytes:
    """Encode one payload message from its field values (proto3: defaults are omitted)."""
    out = b""
    for field, ptype, number in PROTO[name]:
        value = values.get(field)
        if ptype == "optional uint64":
            if value is not None:
                out += tag(number, 0) + varint(value)
        elif ptype in ("uint64", "uint32"):
            if value:
                out += tag(number, 0) + varint(value)
        elif ptype == "string":
            if value:
                raw = value.encode("utf-8")
                out += tag(number, 2) + varint(len(raw)) + raw
        elif ptype == "bytes":
            if value:
                out += tag(number, 2) + varint(len(value)) + bytes(value)
        else:  # pragma: no cover - the table above is closed
            raise AssertionError(ptype)
    return out


def ref_encode(member: str, values: dict[str, Any]) -> bytes:
    """Encode a top-level ``Frame`` with one oneof member set (even when it is all defaults)."""
    number, message = ONEOF[member]
    body = ref_encode_message(message, values)
    return tag(number, 2) + varint(len(body)) + body


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise ValueError("bad varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _fields(data: bytes) -> list[tuple[int, int, Any]]:
    """(number, wire type, value) of every field; value is an int or bytes."""
    pos = 0
    found: list[tuple[int, int, Any]] = []
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wire = key >> 3, key & 7
        value: Any
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 2:
            size, pos = _read_varint(data, pos)
            if pos + size > len(data):
                raise ValueError("length overrun")
            value, pos = data[pos : pos + size], pos + size
        elif wire == 1:
            value, pos = data[pos : pos + 8], pos + 8
        elif wire == 5:
            value, pos = data[pos : pos + 4], pos + 4
        else:
            raise ValueError("unsupported wire type")
        found.append((number, wire, value))
    return found


def ref_decode(data: bytes) -> tuple[str, dict[str, Any]] | None:
    """(oneof member, field values with proto3 defaults filled) or None when no member is set."""
    chosen: tuple[str, bytes] | None = None
    by_number = {number: (member, message) for member, (number, message) in ONEOF.items()}
    for number, wire, value in _fields(data):
        if number in by_number and wire == 2:
            chosen = (by_number[number][0], value)
    if chosen is None:
        return None
    member, body = chosen
    message = ONEOF[member][1]
    values: dict[str, Any] = {}
    for field, ptype, _ in PROTO[message]:
        values[field] = {
            "uint64": 0,
            "uint32": 0,
            "string": "",
            "bytes": b"",
            "optional uint64": None,
        }[ptype]
    numbers = {number: (field, ptype) for field, ptype, number in PROTO[message]}
    for number, _wire, value in _fields(body):
        if number in numbers:
            field, ptype = numbers[number]
            values[field] = value.decode("utf-8") if ptype == "string" else value
    return member, values


@dataclass(frozen=True)
class CloseInfo:
    code: int | None
    clean: bool


class InMemoryWire:
    """One end of an in-memory ``WireConnection`` pair. Use ``InMemoryWire.pair()``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.peer: InMemoryWire | None = None
        self._inbox: asyncio.Queue[bytes | str | None] = asyncio.Queue()
        self._closed = False
        self._close_info: CloseInfo | None = None
        #: everything this end sent, in order (the tests read it)
        self.sent: list[bytes | str] = []
        #: the order of calls: ("send", message) and ("close", code)
        self.calls: list[tuple[str, Any]] = []

    @staticmethod
    def pair() -> tuple[InMemoryWire, InMemoryWire]:
        a, b = InMemoryWire("a"), InMemoryWire("b")
        a.peer, b.peer = b, a
        return a, b

    @property
    def close_info(self) -> CloseInfo | None:
        return self._close_info

    async def send(self, message: bytes | str) -> None:
        self.calls.append(("send", message))
        if self._closed or self._close_info is not None:
            # the error a real connection raises when the peer is gone (a product class, lazily)
            raise importlib.import_module("tracer_evalsim.errors").TransportError(
                code="connection_closed"
            )
        assert self.peer is not None
        self.sent.append(message)
        self.peer._inbox.put_nowait(message)

    async def recv(self) -> bytes | str | None:
        item = await self._inbox.get()
        if item is None:
            self._inbox.put_nowait(None)
        return item

    async def close(self, code: int = 1000) -> None:
        self.calls.append(("close", code))
        if self._closed:
            return
        self._closed = True
        self._close_info = CloseInfo(code=code, clean=True)
        assert self.peer is not None
        self.peer._close_info = CloseInfo(code=code, clean=True)
        self.peer._inbox.put_nowait(None)
        self._inbox.put_nowait(None)

    def abort(self) -> None:
        """The peer vanished without a close frame."""
        assert self.peer is not None
        self.peer._close_info = CloseInfo(code=1006, clean=False)
        self.peer._inbox.put_nowait(None)
