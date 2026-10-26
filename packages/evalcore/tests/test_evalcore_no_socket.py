# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-27, the no-network guard: unit tests of every Phase 3 package open no socket.

The guard is the autouse fixture of ``conftest.py`` in each tests directory (evalcore, evalproviders
and cli). This file proves that the evalcore guard is on and covers the ways code reaches a
network, that each of the three Phase 3 packages has such a guard, and that the SCOPE rows of batch
F name only tests that exist. Contract: docs/phase3/api-surface-batch-f.md section 5.
"""

from __future__ import annotations

import ast
import asyncio
import re
import socket
from pathlib import Path

import pytest

from evalcore_batchf_support import ROOT, SCOPE  # type: ignore[import-not-found,unused-ignore]

PACKAGES = ROOT / "packages"
GUARDED = ("evalcore", "evalproviders", "cli")
#: The calls every guard must replace (the names of the attributes).
GUARDED_ATTRIBUTES = {
    ("socket.socket", "connect"),
    ("socket.socket", "connect_ex"),
    ("socket", "create_connection"),
    ("socket", "getaddrinfo"),
    ("asyncio.BaseEventLoop", "create_connection"),
    ("asyncio.BaseEventLoop", "sock_connect"),
}


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
        with pytest.raises(AssertionError, match="network"):
            loop.run_until_complete(loop.sock_connect(socket.socket(), ("127.0.0.1", 9)))
    finally:
        loop.close()


def test_unit_tests_open_no_socket_the_guard_clears_the_key_from_the_environment() -> None:
    import os

    assert "SARVAM_API_KEY" not in os.environ


def _patched(conftest: Path) -> set[tuple[str, str]]:
    """``(owner, attribute)`` of every ``monkeypatch.setattr(owner, "attribute", ...)`` call."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(conftest.read_text("utf-8"), filename=str(conftest))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.add((ast.unparse(node.args[0]), node.args[1].value))
    return found


@pytest.mark.parametrize("package", GUARDED)
def test_unit_tests_open_no_socket_each_phase3_package_has_the_guard(package: str) -> None:
    conftest = PACKAGES / package / "tests" / "conftest.py"
    assert conftest.is_file(), f"{package}: no conftest.py with the no-network guard"
    tree = ast.parse(conftest.read_text("utf-8"))
    fixtures = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(d, ast.Call)
            and ast.unparse(d.func) == "pytest.fixture"
            and any(
                k.arg == "autouse" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in d.keywords
            )
            for d in node.decorator_list
        )
    ]
    assert fixtures, f"{package}: the guard fixture is not autouse"
    assert _patched(conftest) >= GUARDED_ATTRIBUTES, f"{package}: a connection path is not patched"
    # every replacement raises AssertionError (an OSError could be swallowed by a retry loop)
    replacements = {
        node.args[2].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
        and len(node.args) >= 3
        and isinstance(node.args[2], ast.Name)
    }
    assert replacements, package
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for name in sorted(replacements):
        raised = [
            ast.unparse(node.exc.func)
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        ]
        assert raised == ["AssertionError"], (package, name, raised)


# ---------------------------------------------------------------------- the SCOPE rows


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted(PACKAGES.glob("*/tests/test_*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(node.name)
    return names


@pytest.mark.parametrize("task", ["P3-26", "P3-27", "P3-34"])
def test_every_test_named_by_the_scope_rows_of_batch_f_exists(task: str) -> None:
    rows = [
        line for line in SCOPE.read_text("utf-8").splitlines() if line.startswith(f"| {task} |")
    ]
    assert len(rows) == 1
    named = set(re.findall(r"`(test_\w+)`", rows[0]))
    assert len(named) >= 1
    assert named <= _defined_tests(), sorted(named - _defined_tests())
    if task == "P3-27":
        assert len(named) == 8
    if task == "P3-26":
        assert named == {
            "test_e2e_synthetic_mr_exact_outputs",
            "test_e2e_report_and_store_consistent",
        }
