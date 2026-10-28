# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""Phase 4 batch F (P4-26 to P4-30): the guard that the SCOPE names exist, and the repository wiring
the batch needs.

Contract: docs/phase4/api-surface-batch-f.md sections 2, 3 and 8. The rows P4-26 to P4-30 name 19
tests; each is defined under its exact name, once (``test_unit_tests_open_no_socket`` is the
Phase 3 name that every package of the workspace defines: batch F adds the fourth, in
``packages/evalsim/tests``, and the SCOPE row calls it "extended"). None may still sit on a pending
set of an ADR. The default test command is unchanged: the ``integration`` marker is deselected by
the evalsim ``conftest.py`` and selected only by the ``interop`` job, so the root ``addopts`` (and
the Phase 3 tests that pin it) stay as they are.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections import Counter
from typing import Any

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

ROOT = sup.ROOT
PACKAGES = ROOT / "packages"
ROWS = ("P4-26", "P4-27", "P4-28", "P4-29", "P4-30")
EXTENDED = "test_unit_tests_open_no_socket"
ADDOPTS = "-ra --strict-markers --strict-config -m 'not private_fixtures and not local_only and not postgres'"


def scope_names() -> set[str]:
    section = sup.scope_section_a()
    names: set[str] = set()
    for row in ROWS:
        match = re.search(rf"^\| {re.escape(row)} \|.*$", section, re.MULTILINE)
        assert match is not None, row
        names |= sup.cited(match.group(0).split("|")[-3])
    return names


def test_batch_f_scope_rows_name_nineteen_tests() -> None:
    names = scope_names()
    assert len(names) == 19, sorted(names)
    assert EXTENDED in names
    assert "test_tdeg_sim_agent_silent_timing_not_measured" in names  # renamed 2026-10-27 (D-a)
    assert "test_tdeg_sim_agent_silent_timing_not_applicable" not in names


def test_batch_f_defines_exactly_the_scope_names_once() -> None:
    names = scope_names()
    counts: Counter[str] = Counter()
    for path in sorted(PACKAGES.glob("*/tests/test_*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in names:
                counts[node.name] += 1
    # evalcore, evalproviders and cli define the Phase 3 test; evalsim now defines the fourth
    expected = {n: (4 if n == EXTENDED else 1) for n in names}
    assert {n: counts[n] for n in names} == expected
    evalsim = (PACKAGES / "evalsim" / "tests").glob("test_*.py")
    assert any(f"def {EXTENDED}(" in p.read_text("utf-8") for p in evalsim)


def test_batch_f_names_left_the_pending_sets() -> None:
    """The names are defined now, so no ADR pending set may still hold one (named spec change
    S-F: 22 names leave the sets, and the one outside-the-rules name with them)."""
    names = scope_names()
    groups = [*sup.PENDING.values(), sup.PENDING_OUTSIDE_RULES]
    still = {n for n in names if any(n in group for group in groups)}
    assert still == set(), sorted(still)
    assert frozenset() == sup.PENDING_OUTSIDE_RULES


def test_the_default_test_command_is_unchanged() -> None:
    """The ``integration`` marker is kept out of the default run by the evalsim conftest, not by a
    new ``-m`` term: the root addopts, the CI test legs and the Phase 3 tests that pin them stay as
    they were."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    pytest_ini: dict[str, Any] = pyproject["tool"]["pytest"]["ini_options"]
    assert pytest_ini["addopts"] == ADDOPTS
    assert "integration" not in "".join(pytest_ini["markers"])
    assert pytest_ini["testpaths"] == [
        "packages/evalschema/tests",
        "packages/evaldb/tests",
        "packages/evalcore/tests",
        "packages/evalproviders/tests",
        "packages/cli/tests",
        "packages/evalsim/tests",
    ]


def test_the_tool_project_is_still_not_a_workspace_member_or_a_test_path() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert pyproject["tool"]["uv"]["workspace"]["members"] == ["packages/*"]
    paths = pyproject["tool"]["pytest"]["ini_options"]["testpaths"]
    assert not [p for p in paths if p.startswith("tools")]
    for package in PACKAGES.iterdir():
        if package.is_dir():
            assert (package / "pyproject.toml").is_file(), (
                f"{package.name}: a directory with no project"
            )
    assert not (PACKAGES / "samplebot").exists()
