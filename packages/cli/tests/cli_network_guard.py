# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The loopback-only variant of the no-network guard of the tracer-cli tests (batch J3, gate
condition C3). Support module, not collected; ``conftest.py`` imports it.

A test that carries the ``postgres`` marker talks to the CI database on the loopback interface
(``TRACER_TEST_POSTGRES_URL`` points at 127.0.0.1). For such a test, and only for such a test,
``install_loopback_only`` replaces the four socket paths of the guard by versions that let a
loopback address through and refuse everything else with the same ``AssertionError`` as the full
guard. The rules:

* a host is loopback if it is the name ``localhost`` or a literal IP address that is a loopback
  address (127.0.0.0/8, ::1, ::ffff:127.x); nothing else is looked up: an unknown host name is
  refused before any DNS query is made;
* ``localhost`` is resolved with the real resolver and every address it gives must be a loopback
  address, otherwise it is refused (a hosts file that points ``localhost`` away does not open the
  network);
* only TCP/IP sockets (AF_INET, AF_INET6) with a ``(host, port)`` address are allowed: a Unix
  socket path, an empty host (any interface) and ``0.0.0.0`` are refused;
* the asyncio paths stay refused (the conftest does that, not this module).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, NoReturn

import pytest

MESSAGE = "a unit test tried to use the network (ADR 0024 rule 17)"

# The real functions, taken when this module is imported (before any test patches the socket).
REAL_CONNECT = socket.socket.connect
REAL_CONNECT_EX = socket.socket.connect_ex
REAL_CREATE_CONNECTION = socket.create_connection
REAL_GETADDRINFO = socket.getaddrinfo


def _refuse() -> NoReturn:
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


def install_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the four socket paths of the guard by the loopback-only versions."""
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
