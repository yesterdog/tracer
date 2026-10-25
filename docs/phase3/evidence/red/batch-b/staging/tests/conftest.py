# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The no-network guard of the tracer-evalproviders tests (ADR 0024 rule 17).

Every test runs with ``socket.connect`` and its relatives replaced by a function that raises an
``AssertionError`` (not an ``OSError``: an adapter that retries or translates transport errors
must not swallow it). A test that reaches the network therefore fails; the Sarvam tests use an
``httpx.MockTransport`` and never rely on the guard.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


async def _refuse_async(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", _refuse_async)
    monkeypatch.setattr(asyncio.BaseEventLoop, "sock_connect", _refuse_async)
    # no test reads a key from the machine's environment, and one test cannot leak a key to the next
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
