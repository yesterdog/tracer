# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 4 batch E (P4-21 to P4-25): the repository wiring the command needs, and the guard that the
SCOPE names exist.

Contract: docs/phase4/api-surface-batch-e.md section 13. ``tracer-cli`` declares ``tracer-evalsim``
with its first import (ambiguity 1 of the batch 0b contract): the dependency, the licence policy
block of the CLI, the lock and the closure follow; the console script is unchanged; the import
stays inside the command module.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

ROOT = sup.ROOT
PACKAGES = ROOT / "packages"
ROWS = ("P4-21", "P4-22", "P4-23", "P4-24", "P4-25")
PHASE3_NAME = "test_key_never_in_store_and_report_outputs"


def toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text("utf-8"))


def test_the_cli_declares_the_simulator_with_the_command() -> None:
    project = toml(PACKAGES / "cli" / "pyproject.toml")["project"]
    assert project["dependencies"] == [
        "tracer-evalschema==0.0.1",
        "tracer-evalcore==0.0.1",
        "tracer-evalproviders==0.0.1",
        "tracer-evaldb==0.0.1",
        "tracer-evalsim==0.0.1",
    ]
    assert project["scripts"] == {"tracer": "tracer_cli.cli:main"}
    assert (
        "optional-dependencies" not in project
    )  # the WebRTC extra is the simulator's, not the CLI's


def test_the_licence_policy_block_of_the_cli_lists_the_simulator_closure() -> None:
    policy = toml(ROOT / "docs" / "security" / "license-policy.toml")["runtime"]["cli"]
    assert "tracer-evalsim==0.0.1" in policy["requires_dist"]
    assert "websockets" in policy["closure"]
    assert policy["closure"] == sorted(policy["closure"])
    evalsim = toml(ROOT / "docs" / "security" / "license-policy.toml")["runtime"]["evalsim"]
    assert set(evalsim["closure"]) <= set(policy["closure"]) | {
        n for n in evalsim["closure"] if n.startswith("tracer-")
    }


def test_the_lock_gives_the_cli_the_simulator_and_nothing_pulls_the_webrtc_extra() -> None:
    lock = toml(ROOT / "uv.lock")
    cli = next(p for p in lock["package"] if p["name"] == "tracer-cli")
    dependencies = {d["name"] for d in cli["dependencies"]}
    assert "tracer-evalsim" in dependencies
    assert not [d for d in cli["dependencies"] if d["name"] == "tracer-evalsim" and d.get("extra")]
    assert "optional-dependencies" not in cli or "webrtc" not in cli.get(
        "optional-dependencies", {}
    )
    for name in ("tracer-evalcore", "tracer-evalproviders", "tracer-evaldb", "tracer-evalschema"):
        pkg = next(p for p in lock["package"] if p["name"] == name)
        assert "tracer-evalsim" not in {d["name"] for d in pkg.get("dependencies", [])}


def test_only_the_cli_command_module_imports_the_simulator() -> None:
    importers: set[str] = set()
    for path in sorted(PACKAGES.glob("*/src/*/*.py")):
        if path.parent.name == "tracer_evalsim":
            continue  # the package itself
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "tracer_evalsim" for n in names):
                importers.add(f"{path.parent.name}/{path.name}")
    assert importers == {"tracer_cli/simulate.py"}, importers


def test_batch_e_defines_exactly_the_scope_names_once() -> None:
    """SCOPE rows P4-21 to P4-25 name the tests of this batch; each exists under its exact name,
    once (``test_key_never_in_store_and_report_outputs`` is the Phase 3 test the row extends; it
    exists
    twice already, the store and the report channel, and stays so)."""
    section = sup.scope_section_a()
    names: set[str] = set()
    for row in ROWS:
        match = re.search(rf"^\| {re.escape(row)} \|.*$", section, re.MULTILINE)
        assert match is not None, row
        names |= sup.cited(match.group(0).split("|")[-3])
    assert len(names) == 23, sorted(names)
    counts: Counter[str] = Counter()
    for path in sorted(PACKAGES.glob("*/tests/test_*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in names:
                counts[node.name] += 1
    # the Phase 3 test has two channels already (the store one in test_recorded_store.py and the
    # report one in test_cli_report.py); batch E adds no third definition, it extends the report one
    expected = {n: (2 if n == PHASE3_NAME else 1) for n in names}
    assert {n: counts[n] for n in names} == expected
    assert PHASE3_NAME in names
    original = (PACKAGES / "cli" / "tests" / "test_cli_report.py").read_text("utf-8")
    assert f"def {PHASE3_NAME}(" in original


def test_batch_e_names_left_the_pending_sets() -> None:
    """The names are defined now, so no ADR pending set may still hold one."""
    section = sup.scope_section_a()
    names: set[str] = set()
    for row in ROWS:
        match = re.search(rf"^\| {re.escape(row)} \|.*$", section, re.MULTILINE)
        assert match is not None
        names |= sup.cited(match.group(0).split("|")[-3])
    groups = [*sup.PENDING.values(), sup.PENDING_OUTSIDE_RULES]
    still = {n for n in names if any(n in group for group in groups)}
    assert still == set(), sorted(still)
