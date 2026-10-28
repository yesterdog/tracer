# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The no-network guard of the tracer-evalsim tests (ADR 0024 rule 17, ADR 0026 rules 1 and 23).

Every test runs with ``socket.connect`` and its relatives replaced by a function that raises an
``AssertionError`` (not an ``OSError``: code that retries or translates transport errors must not
swallow it). A test that reaches the network, the loopback included, therefore fails.

One exception (batch D, named conftest change S-D1): a test that carries the ``loopback`` marker
talks to a fake server that the test starts itself, in its own process, on the loopback
interface. For such a test, and only for such a test, the socket paths and the asyncio connection
paths are replaced by the loopback-only versions of ``evalsim_loopback_guard`` (a destination
that is not ``localhost`` or a literal loopback address is still refused, a server may bind a
loopback address only, UDP and Unix sockets are refused). The marker is registered here (so no
repository file changes) and is NOT deselected by default: these tests have no external
resource, no key and no second process, and they run in every leg of the normal test job. The
marker ``integration`` of ADR 0026 rule 23 (tools/samplebot, a separate job) is a different thing.
Unmarked tests keep the full guard, loopback included.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

import evalsim_integration_guard as integration_guard  # type: ignore[import-not-found,unused-ignore]
from evalsim_loopback_guard import MARKER, install_loopback_only  # type: ignore[import-not-found,unused-ignore]

INTEGRATION_MARKER = integration_guard.MARKER


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}: opens sockets to a fake server started by the test itself on 127.0.0.1 or ::1 "
        "(no key, no external host); run by default, see docs/phase4/api-surface-batch-d.md",
    )
    config.addinivalue_line(
        "markers",
        f"{INTEGRATION_MARKER}: runs against the sample bot of tools/samplebot on this machine "
        "(loopback only, no key); deselected unless selected with -m integration (the interop CI "
        "job), see docs/phase4/api-surface-batch-f.md",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Batch F, named change S-F1: the interop tests are DESELECTED (never skipped) unless the run's
    marker expression names the marker. Nothing else decides, so no test can skip itself."""
    expression = config.getoption("markexpr", default="") or ""
    if INTEGRATION_MARKER in expression:
        return
    kept = [i for i in items if i.get_closest_marker(INTEGRATION_MARKER) is None]
    dropped = [i for i in items if i.get_closest_marker(INTEGRATION_MARKER) is not None]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


async def _refuse_async(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker(MARKER) is not None:
        install_loopback_only(monkeypatch)
    elif request.node.get_closest_marker(INTEGRATION_MARKER) is not None:
        integration_guard.install_integration_guard(monkeypatch)
    else:
        monkeypatch.setattr(socket.socket, "connect", _refuse)
        monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
        monkeypatch.setattr(socket, "create_connection", _refuse)
        monkeypatch.setattr(socket, "getaddrinfo", _refuse)
        monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", _refuse_async)
        monkeypatch.setattr(asyncio.BaseEventLoop, "sock_connect", _refuse_async)
        # batch F: an unmarked test starts no server and opens no datagram path either
        monkeypatch.setattr(asyncio.BaseEventLoop, "create_server", _refuse_async)
        monkeypatch.setattr(asyncio.BaseEventLoop, "create_datagram_endpoint", _refuse_async)
        monkeypatch.setattr(socket.socket, "sendto", _refuse)
    # no test reads a key from the machine's environment
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
