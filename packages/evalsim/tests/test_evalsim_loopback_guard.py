# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Guard tests of the named conftest change S-D1: the ``loopback`` marker of the tracer-evalsim
tests (ADR 0026 rule 23, ADR 0024 rule 17; contract ``docs/phase4/api-surface-batch-d.md``
section 1).

The change: tests that carry the ``loopback`` marker, and only those, may reach 127.0.0.1, ::1 and
``localhost`` (to servers they start themselves); every other test keeps the full guard of
Phase 3, loopback included. These tests prove the exemption is narrow and cannot be widened by
accident. They never send a packet: each attempt at a forbidden destination runs with the real
socket function replaced by a spy (so even a broken guard would only call the spy), and the
destinations are documentation addresses (RFC 5737) that no router carries.
"""

from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import evalsim_loopback_guard as guard  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import ROOT  # type: ignore[import-not-found,unused-ignore]

HERE = Path(__file__).resolve().parent
#: the only test modules that may carry the marker (a new one is a decision, not an accident)
LOOPBACK_MODULES = {
    "test_evalsim_hostile_loopback.py",
    "test_evalsim_judge_loopback.py",  # Phase 4 batch J spec change S-J1
    "test_evalsim_loopback_guard.py",
    "test_evalsim_runner_loopback.py",  # Phase 4 batch E spec change S-E1
    "test_evalsim_webrtc.py",
    "test_evalsim_websocket_loopback.py",
}
FORBIDDEN_V4 = ["192.0.2.1", "198.51.100.7", "203.0.113.9", "10.255.255.254", "169.254.0.1"]
FORBIDDEN_V6 = ["2001:db8::1", "::ffff:198.51.100.7", "fe80::1"]


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        raise OSError("spy: no real network call may be made")


class AsyncSpy(Spy):
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        raise OSError("spy: no real network call may be made")


def spy_on_real_functions(monkeypatch: pytest.MonkeyPatch) -> dict[str, Spy]:
    spies = {
        "REAL_CONNECT": Spy(),
        "REAL_CONNECT_EX": Spy(),
        "REAL_BIND": Spy(),
        "REAL_CREATE_CONNECTION": Spy(),
        "REAL_LOOP_CREATE_CONNECTION": AsyncSpy(),
        "REAL_LOOP_CREATE_SERVER": AsyncSpy(),
        "REAL_LOOP_SOCK_CONNECT": AsyncSpy(),
    }
    for name, spy in spies.items():
        monkeypatch.setattr(guard, name, spy)
    return spies


@pytest.mark.loopback
def test_loopback_marked_test_reaches_its_own_server_and_only_that() -> None:
    async def main() -> bytes:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(await reader.readexactly(4))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"ping")
        data = await reader.readexactly(4)
        writer.close()
        server.close()
        await server.wait_closed()
        return data

    assert asyncio.run(main()) == b"ping"
    with socket.socket() as sock:  # a plain blocking socket works too
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        client = socket.create_connection(sock.getsockname())
        client.close()
    assert socket.getaddrinfo("localhost", 80)  # localhost resolves, to loopback only
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("::1", 80)


@pytest.mark.loopback
@pytest.mark.parametrize(
    "host", FORBIDDEN_V4 + FORBIDDEN_V6 + ["example.com", "localhost.example.com", "8.8.8.8", "::"]
)
def test_loopback_marked_test_cannot_connect_anywhere_else(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spies = spy_on_real_functions(monkeypatch)
    resolver = Spy()
    monkeypatch.setattr(guard, "REAL_GETADDRINFO", resolver)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as sock:
        with pytest.raises(AssertionError):
            sock.connect((host, 80))
        with pytest.raises(AssertionError):
            sock.connect_ex((host, 80))
    with pytest.raises(AssertionError):
        socket.create_connection((host, 80), timeout=0.2)
    with pytest.raises(AssertionError):
        socket.getaddrinfo(host, 80)

    async def asyncio_paths() -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(AssertionError):
            await loop.create_connection(asyncio.Protocol, host, 80)
        with pytest.raises(AssertionError):
            await asyncio.open_connection(host, 80)
        with socket.socket(family) as raw:
            raw.setblocking(False)
            with pytest.raises(AssertionError):
                await loop.sock_connect(raw, (host, 80))

    asyncio.run(asyncio_paths())
    assert all(not spy.calls for spy in spies.values()), {
        k: v.calls for k, v in spies.items() if v.calls
    }
    assert resolver.calls == []  # not even a DNS query for a name that is not loopback


@pytest.mark.loopback
def test_loopback_marked_test_cannot_use_a_name_that_resolves_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hosts file that points ``localhost`` at another address does not open the network."""
    spies = spy_on_real_functions(monkeypatch)

    def evil(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", port))]

    monkeypatch.setattr(guard, "REAL_GETADDRINFO", evil)
    with pytest.raises(AssertionError):
        socket.getaddrinfo("localhost", 80)
    with pytest.raises(AssertionError):
        socket.create_connection(("localhost", 80), timeout=0.2)
    with socket.socket() as sock, pytest.raises(AssertionError):
        sock.connect(("localhost", 80))
    assert all(not spy.calls for spy in spies.values())


@pytest.mark.loopback
@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "192.0.2.1", "localhost", "example.com"])  # noqa: S104 - a refusal test: the guard must refuse this address
def test_loopback_marked_test_can_bind_only_a_literal_loopback_address(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spies = spy_on_real_functions(monkeypatch)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as sock, pytest.raises(AssertionError):
        sock.bind((host, 0))

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(AssertionError):
            await loop.create_server(asyncio.Protocol, host or None, 0)

    asyncio.run(serve())
    assert not spies["REAL_BIND"].calls
    assert not spies["REAL_LOOP_CREATE_SERVER"].calls


@pytest.mark.loopback
def test_loopback_marked_test_cannot_listen_on_every_interface_by_omission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spies = spy_on_real_functions(monkeypatch)

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(AssertionError):
            await loop.create_server(asyncio.Protocol, None, 0)  # no host: all interfaces
        with pytest.raises(AssertionError):
            await loop.create_server(asyncio.Protocol, ["127.0.0.1", "0.0.0.0"], 0)  # noqa: S104 - a refusal test: the guard must refuse this address

    asyncio.run(serve())
    assert not spies["REAL_LOOP_CREATE_SERVER"].calls


@pytest.mark.loopback
def test_loopback_marked_test_cannot_use_udp_or_unix_sockets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spies = spy_on_real_functions(monkeypatch)

    async def paths() -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(AssertionError):
            await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
            )
        with pytest.raises(AssertionError):
            await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, remote_addr=("127.0.0.1", 9)
            )
        with pytest.raises(AssertionError):
            await loop.create_unix_connection(asyncio.Protocol, str(tmp_path / "s"))
        with pytest.raises(AssertionError):
            await loop.create_unix_server(asyncio.Protocol, str(tmp_path / "s"))

    asyncio.run(paths())
    with socket.socket(socket.AF_UNIX) as sock:
        with pytest.raises(AssertionError):
            sock.connect(str(tmp_path / "s"))
        with pytest.raises(AssertionError):
            sock.bind(str(tmp_path / "t"))
    assert all(not spy.calls for spy in spies.values())


def test_unmarked_test_keeps_the_full_guard_loopback_included(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spies = spy_on_real_functions(monkeypatch)
    monkeypatch.setattr(guard, "REAL_GETADDRINFO", Spy())
    with socket.socket() as sock:
        with pytest.raises(AssertionError):
            sock.connect(("127.0.0.1", 9))
        with pytest.raises(AssertionError):
            sock.connect_ex(("127.0.0.1", 9))
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
    with pytest.raises(AssertionError):
        socket.getaddrinfo("localhost", 80)
    with pytest.raises(AssertionError):
        socket.getaddrinfo("127.0.0.1", 80)

    async def paths() -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(AssertionError):
            await loop.create_connection(asyncio.Protocol, "127.0.0.1", 9)
        with pytest.raises(AssertionError):
            await asyncio.open_connection("127.0.0.1", 9)
        with socket.socket() as raw:
            raw.setblocking(False)
            with pytest.raises(AssertionError):
                await loop.sock_connect(raw, ("127.0.0.1", 9))

    asyncio.run(paths())
    assert all(not spy.calls for spy in spies.values())


def test_the_marker_is_registered_and_is_not_deselected_by_default(
    request: pytest.FixtureRequest,
) -> None:
    lines = request.config.getini("markers")
    assert any(re.match(r"loopback:\s", line) for line in lines), lines
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    addopts = re.search(r"^addopts\s*=\s*\"([^\"]*)\"", pyproject, re.MULTILINE)
    assert addopts, "addopts not found"
    # the repository's default run deselects private_fixtures, local_only and postgres, and nothing
    # else: loopback tests have no external resource and run in every leg of the normal job
    assert "loopback" not in addopts.group(1)


def test_the_loopback_marker_is_confined_to_the_declared_modules() -> None:
    using = set()
    for path in HERE.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"pytest\.mark\.loopback|pytestmark\s*=.*loopback", text):
            using.add(path.name)
    assert using == LOOPBACK_MODULES, (
        sorted(using - LOOPBACK_MODULES),
        sorted(LOOPBACK_MODULES - using),
    )
    # `integration` (ADR 0026 rule 23: tools/samplebot, a separate job) is a different marker and is
    # not used by these tests
    for path in HERE.glob("test_*.py"):
        if path.name != Path(__file__).name:
            assert "mark.integration" not in path.read_text(encoding="utf-8"), path.name


INNER = """
import asyncio, socket
import evalsim_loopback_guard as guard  # type: ignore[import-not-found,unused-ignore]
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
    sync = ("REAL_CONNECT", "REAL_CONNECT_EX", "REAL_BIND", "REAL_CREATE_CONNECTION")
    out = {n: Spy() for n in sync}
    for n in ("REAL_LOOP_CREATE_CONNECTION", "REAL_LOOP_CREATE_SERVER", "REAL_LOOP_SOCK_CONNECT"):
        out[n] = ASpy()
    for name, spy in out.items():
        monkeypatch.setattr(guard, name, spy)
    monkeypatch.setattr(guard, "REAL_GETADDRINFO", Spy())
    return out


@pytest.mark.loopback
def test_marked_reaches_its_own_server():
    async def main():
        async def echo(r, w):
            w.write(await r.readexactly(2)); await w.drain(); w.close()
        s = await asyncio.start_server(echo, "127.0.0.1", 0)
        r, w = await asyncio.open_connection("127.0.0.1", s.sockets[0].getsockname()[1])
        w.write(b"ok"); got = await r.readexactly(2); w.close(); s.close(); await s.wait_closed()
        return got
    assert asyncio.run(main()) == b"ok"


@pytest.mark.loopback
def test_marked_cannot_reach_elsewhere(spies):
    with socket.socket() as sock:
        with pytest.raises(AssertionError):
            sock.connect(("198.51.100.7", 80))
    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80), timeout=0.2)
    async def main():
        with pytest.raises(AssertionError):
            await asyncio.open_connection("203.0.113.9", 80)
        with pytest.raises(AssertionError):
            await asyncio.get_running_loop().create_server(asyncio.Protocol, "0.0.0.0", 0)
    asyncio.run(main())
    assert not any(s.calls for s in spies.values())


def test_unmarked_is_fully_guarded(spies):
    with socket.socket() as sock:
        with pytest.raises(AssertionError):
            sock.connect(("127.0.0.1", 9))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("localhost", 80)
    async def main():
        with pytest.raises(AssertionError):
            await asyncio.open_connection("127.0.0.1", 9)
    asyncio.run(main())
    assert not any(s.calls for s in spies.values())


class TestClassBased:
    @pytest.mark.loopback
    def test_marker_on_a_method_counts(self):
        socket.getaddrinfo("127.0.0.1", 80)

    def test_method_without_marker_is_guarded(self):
        with pytest.raises(AssertionError):
            socket.getaddrinfo("127.0.0.1", 80)
"""


def test_the_real_conftest_applies_the_marker_exemption_and_nothing_wider(tmp_path: Path) -> None:
    """Run a small test suite in a fresh pytest process against a COPY of the conftest and the
    guard module (the files that ship), with ``--strict-markers`` so that an unregistered marker
    fails the run."""
    (tmp_path / "conftest.py").write_bytes((HERE / "conftest.py").read_bytes())
    (tmp_path / "evalsim_loopback_guard.py").write_bytes(
        (HERE / "evalsim_loopback_guard.py").read_bytes()
    )
    (tmp_path / "test_inner.py").write_text(textwrap.dedent(INNER), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--strict-markers",
            "-q",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            str(tmp_path / "test_inner.py"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert re.search(r"\b5 passed\b", done.stdout), done.stdout


def test_the_conftest_registers_the_marker_and_still_clears_the_key_variable() -> None:
    text = (HERE / "conftest.py").read_text(encoding="utf-8")
    assert "addinivalue_line" in text
    assert 'delenv("SARVAM_API_KEY"' in text
    assert "install_loopback_only" in text
