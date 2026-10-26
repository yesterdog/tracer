# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-27, the no-network guard of the tracer-cli tests (ADR 0024 rule 17).

The guard is the autouse fixture of ``conftest.py`` in this directory. This file proves that it is
on and that the command, which takes an injected ``Environment``, cannot reach the network by
accident: the only transport a test can give it is an ``httpx.MockTransport``. Contract:
docs/phase3/api-surface-batch-f.md section 5.
"""

from __future__ import annotations

import asyncio
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
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(AssertionError, match="network"):
            loop.run_until_complete(loop.create_connection(asyncio.Protocol, "127.0.0.1", 9))
        with pytest.raises(AssertionError, match="network"):
            loop.run_until_complete(loop.sock_connect(socket.socket(), ("127.0.0.1", 9)))
    finally:
        loop.close()
