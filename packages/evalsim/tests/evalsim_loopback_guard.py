# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The loopback-only variant of the no-network guard of the tracer-evalsim tests (batch D,
named conftest change S-D1; contract ``docs/phase4/api-surface-batch-d.md`` section 1).
Support module, not collected; ``conftest.py`` imports it.

The tests of the wire dialects need a real WebSocket (or HTTP) round trip against a fake server
that the test itself starts, in the same process, on the loopback interface. A test that carries
the ``loopback`` marker, and only such a test, runs with ``install_loopback_only``: the socket
paths AND the asyncio connection paths let a loopback destination through and refuse everything
else with the same ``AssertionError`` as the full guard (not an ``OSError``: code that retries or
translates transport errors must not swallow it).

The rules:

* a host is loopback if it is the name ``localhost`` or a literal IP address that is a loopback
  address (127.0.0.0/8, ::1, ::ffff:127.x); nothing else is looked up: an unknown host name is
  refused before any DNS query is made;
* ``localhost`` is resolved with the real resolver and every address it gives must be a loopback
  address, otherwise it is refused (a hosts file that points ``localhost`` away does not open the
  network);
* only TCP/IP sockets (AF_INET, AF_INET6) with a ``(host, port)`` address are allowed: a Unix
  socket path is refused;
* a server may BIND only a literal loopback address: an empty host, ``0.0.0.0`` and ``::`` are
  refused, so a fake server is never reachable from another machine;
* datagram endpoints (UDP) and Unix sockets are refused outright;
* an unmarked test keeps the full guard (``conftest.py``), loopback included.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, NoReturn

import pytest

MARKER = "loopback"
MESSAGE = "a unit test tried to use the network (ADR 0024 rule 17)"

# The real functions, taken when this module is imported (before any test patches the socket).
REAL_CONNECT = socket.socket.connect
REAL_CONNECT_EX = socket.socket.connect_ex
REAL_BIND = socket.socket.bind
REAL_CREATE_CONNECTION = socket.create_connection
REAL_GETADDRINFO = socket.getaddrinfo
REAL_LOOP_CREATE_CONNECTION = asyncio.BaseEventLoop.create_connection
REAL_LOOP_CREATE_SERVER = asyncio.BaseEventLoop.create_server
REAL_LOOP_SOCK_CONNECT = asyncio.BaseEventLoop.sock_connect


def _refuse(*args: Any, **kwargs: Any) -> NoReturn:
    raise AssertionError(MESSAGE)


async def _refuse_async(*args: Any, **kwargs: Any) -> NoReturn:
    raise AssertionError(MESSAGE)


def is_loopback_ip(text: str) -> bool:
    """True for a literal loopback IP address (never for a name)."""
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def check_host(host: Any) -> str:
    """The host as text if it is ``localhost`` or a literal loopback address, else refuse."""
    name = host.decode("ascii", "replace") if isinstance(host, bytes) else host
    if not isinstance(name, str) or not (name.lower() == "localhost" or is_loopback_ip(name)):
        _refuse()
    return name


def _verify_resolution(name: str, port: Any) -> None:
    """``localhost`` must resolve to loopback addresses only."""
    if name.lower() != "localhost":
        return
    for info in REAL_GETADDRINFO(name, port, 0, socket.SOCK_STREAM):
        if not is_loopback_ip(str(info[4][0])):
            _refuse()


def _address_ok(family: int, address: Any) -> None:
    if family not in (socket.AF_INET, socket.AF_INET6):
        _refuse()
    if not isinstance(address, tuple) or len(address) < 2:
        _refuse()
    _verify_resolution(check_host(address[0]), address[1])


def _connect(self: socket.socket, address: Any) -> Any:
    _address_ok(self.family, address)
    return REAL_CONNECT(self, address)


def _connect_ex(self: socket.socket, address: Any) -> Any:
    _address_ok(self.family, address)
    return REAL_CONNECT_EX(self, address)


def _bind(self: socket.socket, address: Any) -> Any:
    # a server binds a literal loopback address only (no name, no wildcard)
    if self.family not in (socket.AF_INET, socket.AF_INET6):
        _refuse()
    if not isinstance(address, tuple) or len(address) < 2 or not is_loopback_ip(str(address[0])):
        _refuse()
    return REAL_BIND(self, address)


def _create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    if not isinstance(address, tuple) or len(address) < 2:
        _refuse()
    _verify_resolution(check_host(address[0]), address[1])
    return REAL_CREATE_CONNECTION(address, *args, **kwargs)


def _getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    name = check_host(host)
    infos = REAL_GETADDRINFO(name, port, *args, **kwargs)
    if any(not is_loopback_ip(str(info[4][0])) for info in infos):
        _refuse()
    return infos


def _peer_ok(sock: Any) -> None:
    """A socket handed to asyncio must be an inet socket (its connect was guarded already)."""
    if getattr(sock, "family", None) not in (socket.AF_INET, socket.AF_INET6):
        _refuse()


async def _loop_create_connection(
    self: Any, protocol_factory: Any, *args: Any, **kwargs: Any
) -> Any:
    host = args[0] if args else kwargs.get("host")
    sock = kwargs.get("sock")
    if host is None:
        if sock is None:
            _refuse()
        _peer_ok(sock)
    else:
        port = args[1] if len(args) > 1 else kwargs.get("port")
        _verify_resolution(check_host(host), port)
    local = kwargs.get("local_addr")
    if local is not None:
        check_host(local[0])
    return await REAL_LOOP_CREATE_CONNECTION(self, protocol_factory, *args, **kwargs)


async def _loop_create_server(self: Any, protocol_factory: Any, *args: Any, **kwargs: Any) -> Any:
    host = args[0] if args else kwargs.get("host")
    if host is None or host == "":
        if kwargs.get("sock") is None:
            _refuse()
        _peer_ok(kwargs["sock"])
    else:
        for one in [host] if isinstance(host, str | bytes) else list(host):
            if not is_loopback_ip(one.decode() if isinstance(one, bytes) else one):
                _refuse()
    return await REAL_LOOP_CREATE_SERVER(self, protocol_factory, *args, **kwargs)


async def _loop_sock_connect(self: Any, sock: Any, address: Any) -> Any:
    _address_ok(sock.family, address)
    return await REAL_LOOP_SOCK_CONNECT(self, sock, address)


def install_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the socket and asyncio paths of the full guard by the loopback-only versions."""
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket.socket, "bind", _bind)
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", _loop_create_connection)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_server", _loop_create_server)
    monkeypatch.setattr(asyncio.BaseEventLoop, "sock_connect", _loop_sock_connect)
    # no UDP and no Unix sockets in a loopback test
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_datagram_endpoint", _refuse_async)
    monkeypatch.setattr(
        asyncio.BaseEventLoop, "create_unix_connection", _refuse_async, raising=False
    )
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_unix_server", _refuse_async, raising=False)
