# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C: the policy-rule test of ADR 0022 and the SCOPE names of the batch.

``test_adr0022_policy_rules_are_tested`` reads ``docs/adr/0022-write-unit-rescoring-reimport.md``:
every numbered rule of "## Decision" and every invariant cites at least one test that exists in
``packages/evaldb/tests``. A cited name that exists nowhere must be on ``PENDING`` (tests of a later
batch the ADR already cites); anything else is a misspelt or deleted test.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
ADR22 = ROOT / "docs" / "adr" / "0022-write-unit-rescoring-reimport.md"
SCOPE = ROOT / "docs" / "phase2" / "SCOPE.md"

# Named by ADR 0022 and written in a later batch: P2-24 (batch F, security audit of evaldb).
PENDING = frozenset({"test_store_errors_hide_input"})

# The batch C rows of SCOPE section 1.4.
BATCH_C_TASKS = ("P2-12a", "P2-12b", "P2-12c", "P2-13", "P2-16")
# Names the ADRs and the batch C contract add to the SCOPE rows (api-surface-batch-c.md section 9).
ADDED_BY_ADR = (
    "test_store_same_run_bundle_twice_is_a_no_op",
    "test_record_owned_by_another_run_rejected",
    "test_deletion_audit_rows_cannot_be_deleted",
)


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        names |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        }
    return names


def _cited(text: str) -> set[str]:
    return set(re.findall(r"`(test_\w+)`", text))


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = re.search(r"\n## ", rest)
    return rest[: end.start()] if end else rest


def _numbered(section: str, pattern: str) -> list[tuple[str, str]]:
    """(label, text) of each item that starts a line with ``pattern``, up to the next one."""
    starts = list(re.finditer(rf"^{pattern}", section, re.MULTILINE))
    items = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(section)
        items.append((match.group(1), section[match.start() : end]))
    return items


def test_adr0022_policy_rules_are_tested() -> None:
    text = ADR22.read_text("utf-8")
    defined = _defined_tests()
    rules = _numbered(_section(text, "## Decision"), r"(\d+)\. \*\*")
    assert [int(label) for label, _ in rules] == list(range(1, 14)), "ADR 0022 has 13 rules"
    for label, body in rules:
        cited = _cited(body)
        assert cited, f"rule {label} of ADR 0022 cites no test"
        assert cited & defined, f"rule {label} cites no test that exists: {sorted(cited)}"
        unknown = cited - defined - PENDING
        assert not unknown, f"rule {label} cites tests that do not exist: {sorted(unknown)}"
    invariants = _numbered(_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 7)]
    for label, body in invariants:
        cited = _cited(body)
        assert cited & defined, f"invariant {label} of ADR 0022 cites no test that exists"
        assert not (cited - defined - PENDING), f"invariant {label}: {sorted(cited - defined)}"
    # the rule that the ADR checks itself
    assert "test_adr0022_policy_rules_are_tested" in defined
    # the tests the ambiguities propose (the lead adds them to the SCOPE rows)
    for name in ADDED_BY_ADR:
        assert name in defined, name


def _scope_rows() -> dict[str, str]:
    rows = {}
    for line in SCOPE.read_text("utf-8").splitlines():
        match = re.match(r"\| (P2-[\w]+) \|", line)
        if match:
            rows[match.group(1)] = line
    return rows


@pytest.mark.parametrize("task", BATCH_C_TASKS)
def test_every_test_named_by_the_scope_rows_of_batch_c_exists(task: str) -> None:
    row = _scope_rows()[task]
    named = _cited(row)
    assert named, task
    missing = named - _defined_tests()
    assert not missing, f"{task}: {sorted(missing)}"
