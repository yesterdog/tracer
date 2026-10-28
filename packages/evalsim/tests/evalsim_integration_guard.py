# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: SIM102
"""The guard of the ``integration`` marker of the tracer-evalsim tests (batch F, named conftest
change S-F1; contract ``docs/phase4/api-surface-batch-f.md`` section 3). Support module, not
collected; ``conftest.py`` imports it.

The interop tests (P4-27) talk to a REAL Pipecat bot that a fixture starts as a child process on
the same machine. A test that carries the ``integration`` marker, and only such a test, runs with
``install_integration_guard``: the TCP paths are exactly the loopback-only paths of
``evalsim_loopback_guard`` (a connection only to ``localhost`` or a literal loopback address, a bind
only to a literal loopback address, an ``AssertionError`` for anything else), and the datagram
paths, which the loopback guard refuses outright, are opened for ONE purpose: WebRTC media between
two processes of this machine. ICE gathers a host candidate on every interface of the machine, so
the two peers do not talk over 127.0.0.1 but over the machine's own addresses (BL-310). The rule is
therefore not "loopback" for UDP but "this machine": a datagram may be bound to, connected to or
sent to a literal address that is loopback or that the machine itself owns, and to nothing else.

* an address is the machine's own if it is a loopback address, or if a UDP socket can be bound to
  it right now (the bind of a UDP socket to an address that is not assigned to an interface of the
  machine fails; the probe sends nothing and is made with the real ``bind``);
* a wildcard address (``0.0.0.0``, ``::``), a multicast or broadcast address, a name and every
  address that is not the machine's own are refused with ``AssertionError``, so no packet can leave
  the machine, and no STUN or TURN server (which is never an address of the machine) can be reached;
* ``getaddrinfo`` may resolve a literal address of the machine (asyncio resolves the local address
  of a datagram endpoint through it); any other name is refused as in the loopback guard;
* Unix sockets stay refused, and an unmarked test keeps the full guard (``conftest.py``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, NoReturn

import pytest

import evalsim_loopback_guard as loopback  # type: ignore[import-not-found,unused-ignore]

MARKER = "integration"
MESSAGE = loopback.MESSAGE

# The real functions, taken when this module is imported (before any test patches the socket).
REAL_SENDTO = socket.socket.sendto
REAL_LOOP_CREATE_DATAGRAM_ENDPOINT = asyncio.BaseEventLoop.create_datagram_endpoint


def _refuse(*args: Any, **kwargs: Any) -> NoReturn:
    raise AssertionError(MESSAGE)


def is_machine_address(text: Any) -> bool:
    """True for a literal address that is loopback or that this machine owns (see the module
    docstring); never for a name, a wildcard, a multicast address or anything else."""
    if not isinstance(text, str):
        return False
    if loopback.is_loopback_ip(text):
        return True
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if (
        address.is_unspecified
        or address.is_multicast
        or address == ipaddress.ip_address("255.255.255.255")
    ):
        return False
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_DGRAM)
    try:
        loopback.REAL_BIND(probe, (text, 0))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _is_udp(sock: socket.socket) -> bool:
    return sock.family in (socket.AF_INET, socket.AF_INET6) and sock.type == socket.SOCK_DGRAM


def _host_of(address: Any) -> Any:
    if not isinstance(address, tuple) or len(address) < 2:
        _refuse()
    return address[0]


def _connect(self: socket.socket, address: Any) -> Any:
    if _is_udp(self):
        if not is_machine_address(_host_of(address)):
            _refuse()
        return loopback.REAL_CONNECT(self, address)
    return loopback._connect(self, address)


def _connect_ex(self: socket.socket, address: Any) -> Any:
    if _is_udp(self):
        if not is_machine_address(_host_of(address)):
            _refuse()
        return loopback.REAL_CONNECT_EX(self, address)
    return loopback._connect_ex(self, address)


def _bind(self: socket.socket, address: Any) -> Any:
    if _is_udp(self):
        if not is_machine_address(_host_of(address)):
            _refuse()
        return loopback.REAL_BIND(self, address)
    return loopback._bind(self, address)


def _sendto(self: socket.socket, *args: Any) -> Any:
    if not args or not _is_udp(self) or not is_machine_address(_host_of(args[-1])):
        _refuse()
    return REAL_SENDTO(self, *args)


def _getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(host, str) and not loopback.is_loopback_ip(host) and host.lower() != "localhost":
        if is_machine_address(host):  # a literal address of this machine: no lookup happens
            return loopback.REAL_GETADDRINFO(host, port, *args, **kwargs)
    return loopback._getaddrinfo(host, port, *args, **kwargs)


async def _loop_create_datagram_endpoint(
    self: Any, protocol_factory: Any, local_addr: Any = None, remote_addr: Any = None, **kwargs: Any
) -> Any:
    for address in (local_addr, remote_addr):
        if address is not None and not is_machine_address(_host_of(address)):
            _refuse()
    sock = kwargs.get("sock")
    if local_addr is None and remote_addr is None:
        if sock is None or not _is_udp(sock):
            _refuse()
    return await REAL_LOOP_CREATE_DATAGRAM_ENDPOINT(
        self, protocol_factory, local_addr, remote_addr, **kwargs
    )


def install_integration_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loopback-only guard for TCP plus the machine-only guard for datagrams."""
    loopback.install_loopback_only(monkeypatch)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket.socket, "bind", _bind)
    monkeypatch.setattr(socket.socket, "sendto", _sendto)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(
        asyncio.BaseEventLoop, "create_datagram_endpoint", _loop_create_datagram_endpoint
    )
