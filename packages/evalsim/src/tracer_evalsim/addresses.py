# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The addresses a target name resolves to (ADR 0026 rule 16, BACKLOG BL-142).

A host name on the allowlist is resolved once per call and every address of the answer is checked
before the connection: a private, loopback, link-local or unspecified address is reachable only
through an entry that names that literal address or ``localhost``, so a public name that answers
with a private address (DNS rebinding) is refused, and ``localhost`` must resolve to loopback
addresses only. A literal address in the entry is the operator's word and is not looked up. The
resolver is injected. The race between this check and the connection is not claimed closed (the
judge's precedent, BL-326). A refusal holds a code only: no host, no address.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from typing import Final

from tracer_evalsim.errors import TargetRefusedError

__all__ = ["Resolver", "check_target_addresses", "system_resolver"]

Resolver = Callable[[str, int], Sequence[str]]
_IP = ipaddress.IPv4Address | ipaddress.IPv6Address
_LOCALHOST: Final = "localhost"


def system_resolver(host: str, port: int) -> list[str]:
    """The addresses the system resolver gives a name (``OSError`` if it cannot)."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _literal(text: str) -> _IP | None:
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None


def _unmapped(address: _IP) -> _IP:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def check_target_addresses(host: str, port: int, *, resolver: Resolver) -> None:
    if _literal(host) is not None:
        return
    try:
        answer = list(resolver(host, port))
    except (OSError, TimeoutError):
        raise TargetRefusedError(code="target_unresolvable") from None
    if not answer:
        raise TargetRefusedError(code="target_unresolvable")
    for text in answer:
        address = _literal(text) if isinstance(text, str) else None
        if address is None:
            raise TargetRefusedError(code="target_address_refused")
        address = _unmapped(address)
        allowed = address.is_loopback if host == _LOCALHOST else address.is_global
        if not allowed:
            raise TargetRefusedError(code="target_address_refused")
