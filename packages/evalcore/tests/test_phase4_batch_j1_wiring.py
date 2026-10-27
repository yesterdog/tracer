# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch J part 1 inventory (tasks P4-J01 to P4-J16): every SCOPE name of those rows is defined by
exactly one test, none is left on a pending set, the contract lists them, and no test module of the
batch imports a batch J product module at module level (so that a missing module fails one test,
not the collection).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

CONTRACT = sup.ROOT / "docs" / "phase4" / "api-surface-batch-j1.md"
COST_TEST = "test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost"
#: the test modules of the batch (names relative to ``packages/<package>/tests``)
BATCH_FILES = (
    "evalcore/tests/evalcore_p4j_support.py",
    "evalcore/tests/test_adr0030_documents_match_json_schemas.py",
    "evalcore/tests/test_adr0030_rules_are_tested.py",
    "evalcore/tests/test_golden_set.py",
    "evalcore/tests/test_judge_dossier.py",
    "evalcore/tests/test_judge_engine_boundary.py",
    "evalcore/tests/test_judge_output.py",
    "evalcore/tests/test_judge_prompt.py",
    "evalcore/tests/test_judge_records.py",
    "evalcore/tests/test_judge_window.py",
    "evalcore/tests/test_phase4_batch_j1_wiring.py",
    "evalproviders/tests/evalproviders_p4j_support.py",
    "evalproviders/tests/test_judge_exchange_replay_fake.py",
    "evalproviders/tests/test_judge_hosts.py",
    "evalproviders/tests/test_judge_profiles.py",
    "evalproviders/tests/test_openai_compat_client.py",
    "evalsim/tests/evalsim_support_openai_server.py",
    "evalsim/tests/test_evalsim_judge_loopback.py",
)
PRODUCT_MODULES = (
    "tracer_evalcore.golden",
    "tracer_evalcore.judge",
    "tracer_evalcore.judgelabels",
    "tracer_evalcore.judgeoutput",
    "tracer_evalcore.judgeprompt",
    "tracer_evalcore.judgerecords",
    "tracer_evalcore.judgewindow",
    "tracer_evalproviders.judgeexchange",
    "tracer_evalproviders.judgehosts",
    "tracer_evalproviders.judgeprofiles",
    "tracer_evalproviders.openai_compat",
)


def part1_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in sup.scope_section_a().splitlines():
        match = re.match(r"\| (P4-J\d\d) \|", line)
        if match and int(match.group(1)[-2:]) <= 16:
            rows[match.group(1)] = line
    return rows


def part1_names() -> set[str]:
    names: set[str] = set()
    for line in part1_rows().values():
        names |= sup.cited(line)
    return names


def definitions_by_name() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(sup.PACKAGES.glob("*/tests/test_*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                counts[node.name] = counts.get(node.name, 0) + 1
    return counts


def test_the_scope_rows_of_part_1_exist_and_name_the_cost_test() -> None:
    rows = part1_rows()
    assert sorted(rows) == [f"P4-J{n:02d}" for n in range(1, 17)]
    assert COST_TEST in sup.cited(rows["P4-J12"]), "the A6 test is named in the P4-J12 row"
    assert len(part1_names()) >= 58


def test_every_scope_name_of_part_1_is_defined_by_exactly_one_test() -> None:
    counts = definitions_by_name()
    problems = [
        f"{name}: {counts.get(name, 0)} definitions"
        for name in sorted(part1_names())
        if counts.get(name, 0) != 1
    ]
    assert problems == [], "\n".join(problems)


def test_no_scope_name_of_part_1_is_left_on_a_pending_set() -> None:
    pending = set().union(*sup.PENDING.values()) | set(sup.PENDING_OUTSIDE_RULES)
    assert not pending & part1_names(), sorted(pending & part1_names())


def test_the_contract_lists_every_name_of_part_1() -> None:
    text = CONTRACT.read_text("utf-8")
    assert text.startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->")
    missing = [name for name in sorted(part1_names()) if f"`{name}`" not in text]
    assert missing == [], missing
    for heading in (
        "## 0. Where the tests are, and how to move them",
        "## 5. `tracer_evalcore.judgeoutput`",
        "## 14. Named spec changes",
        "## 17. Ambiguities, with recommendations",
    ):
        assert heading in text, heading


def test_no_batch_j_test_module_imports_a_batch_j_product_module_at_module_level() -> None:
    """A module-level import of a module that does not exist yet would fail the collection of the
    whole file; the tests import lazily (``importlib``) instead."""
    for relative in BATCH_FILES:
        path = sup.PACKAGES / relative
        tree = ast.parse(path.read_text("utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            for name in names:
                assert name not in PRODUCT_MODULES, f"{relative}: module-level import of {name}"


def test_the_batch_files_have_an_spdx_header_and_are_ascii() -> None:
    for relative in BATCH_FILES:
        text = (sup.PACKAGES / relative).read_text("utf-8")
        assert text.startswith(
            "# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 The Tracer contributors"
        ), relative
        assert text.isascii(), f"{relative}: Devanagari is written as \\u escapes"


def test_the_golden_fixture_is_in_the_evalcore_fixtures() -> None:
    fixture = sup.PACKAGES / "evalcore" / "tests" / "fixtures" / "golden_mr.json"
    assert fixture.is_file()
    assert fixture.read_bytes().isascii()
    assert Path(fixture).stat().st_size < 200_000
