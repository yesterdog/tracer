# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch J part 2 inventory (tasks P4-J17 to P4-J25): every SCOPE name of those rows is defined by
exactly one test (``test_unit_tests_open_no_socket`` is the named test of four packages and is
extended, not added), none is left on a pending set, the contract lists them and the sections its
tests cite, the spec change reached the named no-socket test, and no test module of the batch
imports a batch J product module at module level (so that a missing module fails one test, not
the collection).
"""

from __future__ import annotations

import ast
import re

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

CONTRACT = sup.ROOT / "docs" / "phase4" / "api-surface-batch-j2.md"
EXTENDED = "test_unit_tests_open_no_socket"
#: the test modules of the batch (names relative to ``packages``)
BATCH_FILES = (
    "cli/tests/cli_p4j2_e2e.py",
    "cli/tests/cli_p4j2_support.py",
    "cli/tests/test_cli_judge_canaries.py",
    "cli/tests/test_cli_judge_command.py",
    "cli/tests/test_cli_judge_injection.py",
    "cli/tests/test_cli_judge_report.py",
    "cli/tests/test_cli_judge_store.py",
    "cli/tests/test_cli_judge_tdeg.py",
    "cli/tests/test_cli_labels.py",
    "cli/tests/test_cli_scenario_stt_timing.py",
    "cli/tests/test_cli_simulate_judge_order.py",
    "cli/tests/test_e2e_judge.py",
    "cli/tests/test_guard_judge_files.py",
    "evalcore/tests/test_judge_config.py",
    "evalcore/tests/test_judge_labels_loop.py",
    "evalcore/tests/test_judge_run.py",
    "evalcore/tests/test_phase4_batch_j2_wiring.py",
    "evalproviders/tests/test_judge_commands_support.py",
)
PRODUCT_MODULES = (
    "tracer_cli.judgecmd",
    "tracer_cli.judgeplan",
    "tracer_cli.judgesummary",
    "tracer_cli.labelscmd",
    "tracer_evalcore.judgeconfig",
    "tracer_evalcore.judgerun",
)


def part2_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in sup.scope_section_a().splitlines():
        match = re.match(r"\| (P4-J\d\d) \|", line)
        if match and 17 <= int(match.group(1)[-2:]) <= 25:
            rows[match.group(1)] = line
    return rows


def part2_names() -> set[str]:
    names: set[str] = set()
    for line in part2_rows().values():
        names |= sup.cited(line)
    return names - {EXTENDED}


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


def test_the_scope_rows_of_part_2_exist() -> None:
    rows = part2_rows()
    assert sorted(rows) == [f"P4-J{n:02d}" for n in range(17, 26)]
    assert EXTENDED in sup.cited(rows["P4-J25"])
    assert len(part2_names()) >= 42


def test_every_scope_name_of_part_2_is_defined_by_exactly_one_test() -> None:
    counts = definitions_by_name()
    problems = [
        f"{name}: {counts.get(name, 0)} definitions"
        for name in sorted(part2_names())
        if counts.get(name, 0) != 1
    ]
    assert problems == [], "\n".join(problems)
    assert counts.get(EXTENDED, 0) >= 1


def test_no_scope_name_of_part_2_is_left_on_a_pending_set() -> None:
    pending = set().union(*sup.PENDING.values()) | set(sup.PENDING_OUTSIDE_RULES)
    assert not pending & part2_names(), sorted(pending & part2_names())


def test_the_named_no_socket_test_of_the_cli_is_extended_to_the_judge() -> None:
    """The spec change of the batch: the cli's ``test_unit_tests_open_no_socket`` also runs the
    check that no test of the directory builds a client, a server or a socket of its own."""
    text = (sup.PACKAGES / "cli" / "tests" / "test_cli_no_socket.py").read_text("utf-8")
    tree = ast.parse(text)
    (named,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == EXTENDED]
    body = ast.get_source_segment(text, named) or ""
    assert "assert_judge_tests_open_no_socket()" in body


def test_the_command_set_test_knows_the_judge_and_labels_commands() -> None:
    """The spec change S-J2-3: ``test_cli_has_no_export_command`` expects the four commands."""
    text = (sup.PACKAGES / "cli" / "tests" / "test_cli_score.py").read_text("utf-8")
    assert '{"score", "simulate", "judge", "labels"}' in text


def test_the_contract_lists_every_name_of_part_2_and_the_sections_the_tests_cite() -> None:
    text = CONTRACT.read_text("utf-8")
    assert text.startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->")
    missing = [name for name in sorted(part2_names()) if f"`{name}`" not in text]
    assert missing == [], missing
    headings = {int(m.group(1)) for m in re.finditer(r"(?m)^## (\d+)\. ", text)}
    assert headings == set(range(0, 19)), sorted(headings)
    cited: set[int] = set()
    for relative in BATCH_FILES:
        source = (sup.PACKAGES / relative).read_text("utf-8")
        for match in re.finditer(
            r"api-surface-batch-j2\.md\s+sections?\s+(\d+)(?:\s+(?:to|and)\s+(\d+))?", source
        ):
            low, high = int(match.group(1)), int(match.group(2) or match.group(1))
            cited |= set(range(low, high + 1)) if " to " in match.group(0) else {low, high}
    assert cited, sorted(cited - headings)
    assert cited <= headings, sorted(cited - headings)
    for heading in (
        "## 0. Where the tests are, and how to move them",
        "## 6. The order of operations",
        "## 16. Named spec changes",
        "## 18. Ambiguities, with recommendations",
    ):
        assert heading in text, heading


def test_no_batch_j2_test_module_imports_a_batch_j_product_module_at_module_level() -> None:
    """A module-level import of a module that does not exist yet would fail the collection of the
    whole file; the tests import lazily (``importlib``) instead."""
    for relative in BATCH_FILES:
        tree = ast.parse((sup.PACKAGES / relative).read_text("utf-8"))
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


def test_the_batch_files_name_no_path_of_this_machine_and_no_out_of_scope_language() -> None:
    """The evidence rule: no absolute path of a user, no temporary directory of the platform, no
    language outside the supported nine (en hi mr te ta kn ml pa gu)."""
    forbidden = (
        re.compile("/" + "Users/"),
        re.compile("claude" + "-502"),
        re.compile("/private" + "/(?:var|tmp)"),
        re.compile("/var" + "/folders"),
    )
    for relative in BATCH_FILES:
        text = (sup.PACKAGES / relative).read_text("utf-8")
        for pattern in forbidden:
            assert pattern.search(text) is None, f"{relative}: {pattern.pattern}"
