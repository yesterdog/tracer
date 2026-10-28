# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""The inner test suite that ``test_evalsim_integration_guard.py`` runs in a fresh pytest process
against the real ``conftest.py`` (support module, not collected: it is data, so that the marker
names in it are not mistaken for uses of the markers by the tests that confine them)."""

INNER = """
import asyncio, socket
import evalsim_integration_guard as ig  # type: ignore[import-not-found,unused-ignore]
import evalsim_loopback_guard as lg  # type: ignore[import-not-found,unused-ignore]
import pytest


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        raise OSError("spy")


class ASpy(Spy):
    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        raise OSError("spy")


@pytest.fixture
def spies(monkeypatch):
    out = {n: Spy() for n in ("REAL_CONNECT", "REAL_CONNECT_EX", "REAL_BIND", "REAL_CREATE_CONNECTION")}
    for n in ("REAL_LOOP_CREATE_CONNECTION", "REAL_LOOP_CREATE_SERVER", "REAL_LOOP_SOCK_CONNECT"):
        out[n] = ASpy()
    for name, spy in out.items():
        monkeypatch.setattr(lg, name, spy)
    monkeypatch.setattr(lg, "REAL_GETADDRINFO", Spy())
    sendto = Spy()
    monkeypatch.setattr(ig, "REAL_SENDTO", sendto)
    datagram = ASpy()
    monkeypatch.setattr(ig, "REAL_LOOP_CREATE_DATAGRAM_ENDPOINT", datagram)
    out["REAL_SENDTO"] = sendto
    out["REAL_DATAGRAM"] = datagram
    return out


@pytest.mark.integration
def test_marked_reaches_its_own_tcp_server():
    async def main():
        async def echo(r, w):
            w.write(await r.readexactly(2)); await w.drain(); w.close()
        s = await asyncio.start_server(echo, "127.0.0.1", 0)
        r, w = await asyncio.open_connection("127.0.0.1", s.sockets[0].getsockname()[1])
        w.write(b"ok"); got = await r.readexactly(2); w.close(); s.close(); await s.wait_closed()
        return got
    assert asyncio.run(main()) == b"ok"


@pytest.mark.integration
def test_marked_reaches_its_own_udp_socket():
    a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        a.bind(("127.0.0.1", 0)); b.bind(("127.0.0.1", 0))
        b.sendto(b"dg", a.getsockname())
        a.settimeout(5)
        assert a.recvfrom(10)[0] == b"dg"
    finally:
        a.close(); b.close()

    async def main():
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        transport.close()
    asyncio.run(main())


@pytest.mark.integration
def test_marked_cannot_reach_elsewhere(spies):
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET, kind) as sock:
            with pytest.raises(AssertionError):
                sock.connect(("198.51.100.7", 3478))
            with pytest.raises(AssertionError):
                sock.connect_ex(("203.0.113.9", 3478))
            with pytest.raises(AssertionError):
                sock.bind(("0.0.0.0", 0))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(AssertionError):
            sock.sendto(b"x", ("198.51.100.7", 3478))
        with pytest.raises(AssertionError):
            sock.sendto(b"x", 0, ("192.0.2.1", 3478))
        with pytest.raises(AssertionError):
            sock.sendto(b"x", ("224.0.0.251", 5353))
        with pytest.raises(AssertionError):
            sock.sendto(b"x", ("255.255.255.255", 5353))
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
        with pytest.raises(AssertionError):
            sock.sendto(b"x", ("2001:db8::1", 3478, 0, 0))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        with pytest.raises(AssertionError):
            sock.connect("/nonexistent/socket")
    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80), timeout=0.2)
    with pytest.raises(AssertionError):
        socket.getaddrinfo("stun.l.google.com", 3478)
    async def main():
        loop = asyncio.get_running_loop()
        with pytest.raises(AssertionError):
            await asyncio.open_connection("203.0.113.9", 80)
        with pytest.raises(AssertionError):
            await loop.create_server(asyncio.Protocol, "0.0.0.0", 0)
        with pytest.raises(AssertionError):
            await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("0.0.0.0", 0))
        with pytest.raises(AssertionError):
            await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, remote_addr=("198.51.100.7", 3478)
            )
        with pytest.raises(AssertionError):
            await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=("stun.example.org", 3478))
    asyncio.run(main())
    # no real function ran, except the bind PROBES of `is_machine_address` (an address with port 0 on
    # a throwaway datagram socket: they send nothing)
    called = {name: spy.calls for name, spy in spies.items() if spy.calls}
    assert set(called) <= {"REAL_BIND"}, called
    assert all(call[1][1] == 0 for call in called.get("REAL_BIND", [])), called


@pytest.mark.integration
def test_the_address_of_the_machine_is_decided_by_a_bind_probe(spies, monkeypatch):
    # a probe that fails: not the machine's address (documentation addresses are nobody's)
    assert not ig.is_machine_address("192.0.2.55")
    assert not ig.is_machine_address("localhost")
    assert not ig.is_machine_address("stun.example.org")
    assert not ig.is_machine_address(None)
    assert not ig.is_machine_address("")
    assert ig.is_machine_address("127.0.0.1") and ig.is_machine_address("::1")
    assert ig.is_machine_address("127.9.9.9")
    # a probe that succeeds: the machine's own address (a spy stands for the bind)
    class OkBind:
        def __init__(self):
            self.calls = []

        def __call__(self, *args, **kwargs):
            self.calls.append(args)

    ok = OkBind()
    monkeypatch.setattr(lg, "REAL_BIND", ok)
    assert ig.is_machine_address("192.0.2.55")
    assert ok.calls and ok.calls[-1][1] == ("192.0.2.55", 0)
    # but never a wildcard, a multicast address or the broadcast address, whatever the probe says
    for text in ("0.0.0.0", "::", "224.0.0.251", "ff02::fb", "255.255.255.255"):
        assert not ig.is_machine_address(text), text


def test_unmarked_is_fully_guarded(spies):
    with socket.socket() as sock:
        with pytest.raises(AssertionError):
            sock.connect(("127.0.0.1", 9))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(AssertionError):
            sock.connect(("127.0.0.1", 9))
        with pytest.raises(AssertionError):
            sock.sendto(b"x", ("127.0.0.1", 9))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("localhost", 80)
    async def main():
        with pytest.raises(AssertionError):
            await asyncio.open_connection("127.0.0.1", 9)
        with pytest.raises(AssertionError):
            await asyncio.get_running_loop().create_datagram_endpoint(
                asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
            )
    asyncio.run(main())
    assert not any(s.calls for s in spies.values())


@pytest.mark.loopback
def test_loopback_marked_keeps_its_own_narrower_guard(spies):
    # the older marker still refuses a datagram endpoint outright
    async def main():
        with pytest.raises(AssertionError):
            await asyncio.get_running_loop().create_datagram_endpoint(
                asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
            )
    asyncio.run(main())
"""
