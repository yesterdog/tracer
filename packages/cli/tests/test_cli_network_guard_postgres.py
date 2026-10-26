# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch J3 (gate condition C3): the no-network guard of the cli tests lets a test that carries
the ``postgres`` marker reach the loopback interface and nothing else; unmarked tests keep the full
guard (loopback included). Contract: docs/phase3/api-surface-batch-j3.md.

These tests run in the default suite (they are not marked ``postgres`` and need no server): the
helper ``cli_network_guard`` is tested directly against a listener the test opens itself, and the
real ``conftest.py`` is tested in a subprocess pytest run over an inline test file whose tests
carry the marker.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "uv.lock").is_file())
REFUSED = "network"
NOT_LOOPBACK = [
    ("192.0.2.1", 9),
    ("203.0.113.5", 80),
    ("10.0.0.1", 5432),
    ("8.8.8.8", 53),
    ("0.0.0.0", 9),  # noqa: S104
    ("", 9),
    ("example.invalid", 9),
    ("api.sarvam.ai", 443),
    ("localhost.example.com", 9),
    ("evil-localhost", 9),
    ("notlocalhost", 9),
    ("127.0.0.1.example.com", 9),
    ("2001:db8::1", 9),
    ("::", 9),
]


def guard() -> ModuleType:
    return importlib.import_module("cli_network_guard")


@contextmanager
def loopback_only() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as mp:
        guard().install_loopback_only(mp)
        yield


@contextmanager
def listener() -> Iterator[socket.socket]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        server.settimeout(5)
        yield server
    finally:
        server.close()


# ------------------------------------------------------------ the helper, called directly


def test_loopback_guard_lets_the_four_socket_paths_reach_a_loopback_listener() -> None:
    with loopback_only(), listener() as server:
        port = server.getsockname()[1]
        for host in ("127.0.0.1", "localhost"):
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as client:
                client.settimeout(5)
                if host == "127.0.0.1":
                    client.connect((host, port))
                    server.accept()[0].close()
            with closing(socket.create_connection((host, port), timeout=5)):
                pass
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as client:
            assert client.connect_ex(("127.0.0.1", port)) == 0
        infos = socket.getaddrinfo("127.0.0.1", port, type=socket.SOCK_STREAM)
        assert infos
        assert socket.getaddrinfo("localhost", port, type=socket.SOCK_STREAM)
        assert socket.getaddrinfo("::1", port, type=socket.SOCK_STREAM)


@pytest.mark.parametrize(("host", "port"), NOT_LOOPBACK)
def test_loopback_guard_refuses_every_other_address_before_any_lookup(host: str, port: int) -> None:
    with loopback_only():
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as one:
            with pytest.raises(AssertionError, match=REFUSED):
                one.connect((host, port))
            with pytest.raises(AssertionError, match=REFUSED):
                one.connect_ex((host, port))
        with pytest.raises(AssertionError, match=REFUSED):
            socket.create_connection((host, port), timeout=1)
        with pytest.raises(AssertionError, match=REFUSED):
            socket.getaddrinfo(host, port)


def test_loopback_guard_refuses_non_tcp_shapes(tmp_path: Path) -> None:
    with loopback_only():
        unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with closing(unix), pytest.raises(AssertionError, match=REFUSED):
            unix.connect(str(tmp_path / "db.sock"))
        with pytest.raises(AssertionError, match=REFUSED):
            socket.getaddrinfo(None, 80)
        with pytest.raises(AssertionError, match=REFUSED):
            socket.create_connection("127.0.0.1")  # type: ignore[arg-type]
        with closing(socket.socket()) as short, pytest.raises(AssertionError, match=REFUSED):
            short.connect(("127.0.0.1",))


def test_loopback_guard_refuses_localhost_when_it_resolves_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = guard()

    def elsewhere(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", port))]

    monkeypatch.setattr(module, "REAL_GETADDRINFO", elsewhere)
    with loopback_only():
        with (
            closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as one,
            pytest.raises(AssertionError, match=REFUSED),
        ):
            one.connect(("localhost", 9))
        with pytest.raises(AssertionError, match=REFUSED):
            socket.create_connection(("localhost", 9), timeout=1)
        with pytest.raises(AssertionError, match=REFUSED):
            socket.getaddrinfo("localhost", 9)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("127.0.0.1", True),
        ("127.5.5.5", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("127.0.0.1%lo0", True),
        ("0.0.0.0", False),  # noqa: S104
        ("::", False),
        ("10.0.0.1", False),
        ("192.168.1.1", False),
        ("8.8.8.8", False),
        ("::ffff:8.8.8.8", False),
        ("localhost", False),
        ("127.0.0.1.example.com", False),
        ("", False),
        ("1270.0.1", False),
    ],
)
def test_is_loopback_ip_reads_literals_only(text: str, expected: bool) -> None:
    assert guard().is_loopback_ip(text) is expected


# ------------------------------------------------------------ the real conftest, in a subprocess

INLINE = """
import asyncio
import socket

import pytest


def _listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(5)
    return server


@pytest.mark.postgres
def test_marked_reaches_loopback():
    server = _listener()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.close()
    plain = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    plain.connect(("127.0.0.1", port))
    plain.close()
    assert socket.getaddrinfo("localhost", port)
    server.close()


@pytest.mark.parametrize(
    "case",
    ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
)
def test_param_mark_selects_the_exemption(case):
    server = _listener()
    port = server.getsockname()[1]
    try:
        if case == "postgres":
            socket.create_connection(("127.0.0.1", port), timeout=5).close()
        else:
            with pytest.raises(AssertionError, match="network"):
                socket.create_connection(("127.0.0.1", port), timeout=5)
    finally:
        server.close()


@pytest.mark.postgres
def test_marked_cannot_reach_elsewhere():
    for host in ("192.0.2.1", "10.0.0.1", "example.invalid", "0.0.0.0"):
        with pytest.raises(AssertionError, match="network"):
            socket.create_connection((host, 5432), timeout=1)
        with pytest.raises(AssertionError, match="network"):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, 5432))
        with pytest.raises(AssertionError, match="network"):
            socket.getaddrinfo(host, 5432)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(AssertionError, match="network"):
            loop.run_until_complete(loop.create_connection(asyncio.Protocol, "127.0.0.1", 9))
        with pytest.raises(AssertionError, match="network"):
            # sock_connect is overridden by the selector loop, so the patch of the base class
            # does not reach it; the socket-level guard does: elsewhere is refused
            loop.run_until_complete(loop.sock_connect(socket.socket(), ("192.0.2.1", 9)))
    finally:
        loop.close()


def test_unmarked_is_fully_guarded_loopback_included():
    server = _listener()
    port = server.getsockname()[1]
    try:
        with pytest.raises(AssertionError, match="network"):
            socket.create_connection(("127.0.0.1", port), timeout=5)
        with pytest.raises(AssertionError, match="network"):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", port))
        with pytest.raises(AssertionError, match="network"):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("127.0.0.1", port))
        with pytest.raises(AssertionError, match="network"):
            socket.getaddrinfo("localhost", port)
        with pytest.raises(AssertionError, match="network"):
            socket.getaddrinfo("127.0.0.1", port)
    finally:
        server.close()


@pytest.mark.local_only
def test_other_marker_gets_no_exemption():
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_the_key_is_still_cleared_for_every_test(monkeypatch):
    import os

    assert "SARVAM_API_KEY" not in os.environ
"""
INI = "[pytest]\nmarkers =\n    postgres: x\n    local_only: x\naddopts = -p no:cacheprovider\n"
#: six functions, one of them with two parameters
INLINE_TESTS = 7


def run_inline(tmp_path: Path, conftest: Path, helper: Path | None) -> tuple[int, str]:
    shutil.copy(conftest, tmp_path / "conftest.py")
    if helper is not None and helper.is_file():
        shutil.copy(helper, tmp_path / "cli_network_guard.py")
    (tmp_path / "test_inline.py").write_text(INLINE, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SARVAM_API_KEY", "TRACER_TEST_POSTGRES_URL") and not k.startswith("PYTEST_")
    }
    env["SARVAM_API_KEY"] = "placeholder"  # the guard of the inline run must clear it
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", "-c", str(tmp_path / "pytest.ini"),
         "--rootdir", str(tmp_path), str(tmp_path / "test_inline.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )  # fmt: skip
    return result.returncode, result.stdout + result.stderr


def test_the_real_conftest_exempts_only_postgres_marked_tests_and_only_loopback(
    tmp_path: Path,
) -> None:
    code, output = run_inline(tmp_path, HERE / "conftest.py", HERE / "cli_network_guard.py")
    assert code == 0, output[-3000:]
    assert re.search(rf"\b{INLINE_TESTS} passed\b", output), output[-500:]


def test_the_inline_run_has_teeth_it_fails_under_a_conftest_that_exempts_everything(
    tmp_path: Path,
) -> None:
    real = (HERE / "conftest.py").read_text("utf-8")
    assert "get_closest_marker" in real
    # every postgres-marked test gets no guard at all (the proposal of the dev before J3)
    bad = real.replace("install_loopback_only(monkeypatch)", "return  # no guard for a marked test")
    assert bad != real
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "bad_conftest.py").write_text(bad, encoding="utf-8")
    code, output = run_inline(folder, folder / "bad_conftest.py", HERE / "cli_network_guard.py")
    assert code != 0
    assert "test_marked_cannot_reach_elsewhere" in output


# ------------------------------------------------------------ the other packages


@pytest.mark.parametrize("package", ["evalcore", "evalproviders"])
def test_no_postgres_marked_test_exists_where_the_guard_stays_total(package: str) -> None:
    tests = ROOT / "packages" / package / "tests"
    for path in sorted(tests.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        marks = [
            ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "postgres"
        ]
        assert marks == [], (path.name, marks)
    conftest = (tests / "conftest.py").read_text("utf-8")
    assert "postgres" not in conftest
    assert "get_closest_marker" not in conftest
