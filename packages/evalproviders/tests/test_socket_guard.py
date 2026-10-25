# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0024 rule 17 and invariant I8: unit tests open no socket.

The guard itself is ``conftest.py`` (autouse). This file proves that it is on, that it holds for
the ways an HTTP client reaches a network, and that no source or test file builds an HTTP client
that could bypass the injected transport.
"""

from __future__ import annotations

import ast
import asyncio
import socket
from pathlib import Path

import pytest

from evalproviders_support import SRC  # type: ignore[import-not-found,unused-ignore]

TESTS = Path(__file__).resolve().parent


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
    finally:
        loop.close()


def _calls(path: Path, names: set[str]) -> list[tuple[int, str, set[str]]]:
    """Calls of ``httpx.<name>(...)`` (any of ``names``) with their keyword names."""
    found = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"), filename=str(path))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if isinstance(func.value, ast.Name) and func.value.id == "httpx" and func.attr in names:
                found.append((node.lineno, func.attr, {k.arg for k in node.keywords if k.arg}))
    return found


def test_unit_tests_open_no_socket_source_has_one_client_with_an_injected_transport() -> None:
    clients = []
    shortcuts = {"get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}
    for path in sorted(SRC.rglob("*.py")):
        clients += [(path, *c) for c in _calls(path, {"Client", "AsyncClient"})]
        assert not _calls(path, shortcuts), f"{path}: an httpx shortcut builds its own client"
    assert len(clients) == 1, clients
    path, _line, name, keywords = clients[0]
    assert path.relative_to(SRC).as_posix() == "sarvam/client.py"
    assert name == "Client"
    assert "transport" in keywords
    assert "base_url" not in keywords
    assert "verify" not in keywords
    assert "follow_redirects" in keywords


def test_unit_tests_open_no_socket_tests_build_no_client_without_a_transport() -> None:
    for path in [*sorted(TESTS.glob("test_*.py")), TESTS / "evalproviders_support.py"]:
        for line, name, keywords in _calls(path, {"Client", "AsyncClient", "HTTPTransport"}):
            assert name == "Client", f"{path.name}:{line}"
            assert "transport" in keywords, f"{path.name}:{line}"
        assert not _calls(
            path, {"get", "post", "put", "patch", "delete", "head", "options", "stream"}
        ), path.name
