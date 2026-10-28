# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-30: the no-network guard of ADR 0024 rule 17, extended to ``tracer-evalsim`` (ADR 0026
rule 23; contract docs/phase4/api-surface-batch-f.md section 3).

``test_unit_tests_open_no_socket`` exists in the three packages of Phase 3 with the same body; this
is the fourth, and it adds what is specific to the simulator: an unmarked test cannot reach even
the loopback interface (only a test that carries the ``loopback`` or the ``integration`` marker
can, and then only the guarded paths), the client library of the wire dialects (``websockets``)
and the asyncio server and datagram paths are covered, the failure is an ``AssertionError`` (an
``OSError`` could be swallowed by a retry), and no test reads the key from the machine.
"""

from __future__ import annotations

import asyncio
import os
import socket

import pytest


def test_unit_tests_open_no_socket() -> None:
    with pytest.raises(AssertionError, match="network"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="network"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("api.sarvam.ai", 443)
    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("localhost", 80)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(AssertionError, match="network"):
            loop.run_until_complete(loop.create_connection(asyncio.Protocol, "127.0.0.1", 9))
        with pytest.raises(AssertionError, match="network"):
            loop.run_until_complete(loop.sock_connect(socket.socket(), ("127.0.0.1", 9)))
    finally:
        loop.close()


def test_unit_tests_open_no_socket_the_client_of_the_dialects_cannot_connect() -> None:
    """The WebSocket client of the simulator goes through ``asyncio`` ``create_connection``: an
    unmarked test that tries it, even to a closed loopback port, fails with the guard's message."""
    from websockets.asyncio.client import connect

    async def attempt() -> None:
        async with connect("ws://127.0.0.1:9/ws-client", open_timeout=1):
            pass  # pragma: no cover

    with pytest.raises(AssertionError, match="network"):
        asyncio.run(attempt())


def test_unit_tests_open_no_socket_a_server_cannot_be_started_without_a_marker() -> None:
    async def serve() -> None:
        loop = asyncio.get_running_loop()
        await loop.create_server(asyncio.Protocol, "127.0.0.1", 0)

    with pytest.raises(AssertionError, match="network"):
        asyncio.run(serve())


def test_unit_tests_open_no_socket_a_datagram_cannot_be_sent_or_opened_without_a_marker() -> None:
    """Datagram sockets (WebRTC media) are reachable only by a test with the ``integration``
    marker, and then only to this machine's own addresses (``evalsim_integration_guard``)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(AssertionError, match="network"):
            sock.connect(("127.0.0.1", 9))
        with pytest.raises(AssertionError, match="network"):
            sock.sendto(b"x", ("127.0.0.1", 9))

    async def open_udp() -> None:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))

    with pytest.raises(AssertionError, match="network"):
        asyncio.run(open_udp())


def test_unit_tests_open_no_socket_the_guard_clears_the_key_from_the_environment() -> None:
    assert "SARVAM_API_KEY" not in os.environ
