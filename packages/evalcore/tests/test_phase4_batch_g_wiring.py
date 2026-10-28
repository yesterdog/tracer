# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch G inventory (task P4-31 and BL-338): every SCOPE name of the row P4-31 is defined by
exactly one test, none is left on a pending set, the contract lists them and the sections its
tests cite, the named spec changes reached the tests and the ADR text they are about, and no test
module of the batch imports a batch G product module at module level (so a missing module fails
one test, not the collection).

Contract: docs/phase4/api-surface-batch-g.md sections 0, 10 and 11.
"""

from __future__ import annotations

import ast
import importlib
import re

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

CONTRACT = sup.ROOT / "docs" / "phase4" / "api-surface-batch-g.md"
NAMES = (
    "test_target_not_on_allowlist_refused_before_any_key_or_socket",
    "test_allowlist_mode_and_link_rules",
    "test_allowlist_entry_fields_unknown_or_repeated_refused",
    "test_one_call_per_invocation_and_min_interval",
    "test_kill_switch_file_hangs_up_within_one_frame",
)
#: the test modules of the batch (names relative to ``packages``)
BATCH_FILES = (
    "cli/tests/cli_p4g_support.py",
    "cli/tests/test_cli_simulate_brain_failed.py",
    "cli/tests/test_cli_simulate_interval.py",
    "cli/tests/test_cli_simulate_kill_switch.py",
    "cli/tests/test_cli_simulate_target_safety.py",
    "cli/tests/test_tdeg_sim_brain_failed.py",
    "evalcore/tests/test_phase4_batch_g_wiring.py",
    "evalcore/tests/test_sim_brain_failed_records.py",
    "evalsim/tests/evalsim_support_g.py",
    "evalsim/tests/test_evalsim_allowlist_open.py",
    "evalsim/tests/test_evalsim_brain_failed.py",
    "evalsim/tests/test_evalsim_call_lock.py",
    "evalsim/tests/test_evalsim_kill_switch.py",
    "evalsim/tests/test_evalsim_target_addresses.py",
)
PRODUCT_MODULES = (
    "tracer_evalsim.guard",
    "tracer_evalsim.addresses",
)
TWELVE = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
)


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


def p431_row() -> str:
    rows = [ln for ln in sup.scope_section_a().splitlines() if ln.startswith("| P4-31 |")]
    assert len(rows) == 1
    row: str = rows[0]
    return row


def test_the_scope_row_of_p4_31_names_the_five_tests() -> None:
    assert sup.cited(p431_row()) == set(NAMES)


def test_every_scope_name_of_p4_31_is_defined_by_exactly_one_test() -> None:
    counts = definitions_by_name()
    problems = [f"{n}: {counts.get(n, 0)} definitions" for n in NAMES if counts.get(n, 0) != 1]
    assert problems == [], "\n".join(problems)


def test_no_scope_name_of_p4_31_is_left_on_a_pending_set() -> None:
    pending = set().union(*sup.PENDING.values()) | set(sup.PENDING_OUTSIDE_RULES)
    assert not pending & set(NAMES), sorted(pending & set(NAMES))


def test_the_contract_lists_every_name_and_the_sections_the_tests_cite() -> None:
    text = CONTRACT.read_text("utf-8")
    assert text.startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->")
    missing = [name for name in NAMES if f"`{name}`" not in text]
    assert missing == [], missing
    headings = {int(m.group(1)) for m in re.finditer(r"(?m)^## (\d+)\. ", text)}
    assert headings == set(range(0, 15)), sorted(headings)
    cited: set[int] = set()
    for relative in BATCH_FILES:
        source = (sup.PACKAGES / relative).read_text("utf-8")
        for match in re.finditer(
            r"api-surface-batch-g\.md\s+sections?\s+(\d+)(?:(?:,|\s+and|\s+to)\s+(\d+))?"
            r"(?:\s+and\s+(\d+))?",
            source,
        ):
            cited |= {int(g) for g in match.groups() if g}
    assert cited, "no test cites the contract"
    assert cited <= headings, sorted(cited - headings)
    for heading in (
        "## 0. Where the tests are, and how to move them",
        "## 9. The text the architect applies (proposed ADR and SCOPE amendments)",
        "## 10. Named spec changes",
        "## 13. Ambiguities, with recommendations",
        "## 14. Contradictions and findings",
    ):
        assert heading in text, heading


def test_no_batch_g_test_module_imports_a_batch_g_product_module_at_module_level() -> None:
    """A module-level import of a module that does not exist yet would fail the collection of the
    whole file; the tests import lazily (``importlib``) instead."""
    for relative in BATCH_FILES:
        tree = ast.parse((sup.PACKAGES / relative).read_text("utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name not in PRODUCT_MODULES, (relative, name)


def test_the_spec_change_reached_the_tests_that_pin_the_end_reasons() -> None:
    """S-G2: every test file that lists the end reasons lists the twelve, in this order."""
    for relative in (
        "evalcore/tests/evalcore_batcha_support.py",
        "evalcore/tests/test_sim_outcome_doc.py",
        "evalcore/tests/test_adr0027_documents_match_json_schemas.py",
        "evalproviders/tests/test_sarvam_sim_calllog.py",
    ):
        source = (sup.PACKAGES / relative).read_text("utf-8")
        found = re.search(r"END_REASONS[^=]*=\s*[\(\[]\s*([^\)\]]*)[\)\]]", source)
        assert found is not None, relative
        assert tuple(re.findall(r'"([a-z_]+)"', found.group(1))) == TWELVE, relative


def test_the_adr_text_says_twelve_and_names_the_two_files() -> None:
    """S-G3 to S-G5: the ADRs agree with the code that the tests pin."""
    adr26 = sup.ADR26.read_text("utf-8")
    rules = dict(sup.numbered(sup.heading_section(adr26, "## Decision"), r"(\d+)\. \*\*"))
    rule9 = rules["9"]
    listed = rule9[rule9.index("exactly the end reasons") : rule9.index("Every call ends")]
    assert tuple(re.findall(r"`([a-z_]+)`", listed)) == TWELVE
    guard = importlib.import_module("tracer_evalsim.guard")
    assert f"`{guard.KILL_SWITCH_FILE_NAME}`" in rules["17"]
    assert f"`{guard.CALL_LOCK_FILE_NAME}`" in rules["18"]
    assert "`brain_failed`" in rules["19"]
    assert "twelve end reasons" in adr26
    assert "eleven end reasons" not in adr26
    assert "twelve end reasons" in sup.ADR29.read_text("utf-8")
    assert '"spend_cap", "brain_failed"]' in sup.ADR27.read_text("utf-8")
    scope = sup.SCOPE4.read_text("utf-8")
    assert "`spend_cap`, `brain_failed` (added 2026-10-28, BL-338)" in scope
