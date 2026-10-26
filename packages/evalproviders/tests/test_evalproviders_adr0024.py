# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 3 batch B: the policy-rule test of ADR 0024 and the SCOPE names of the batch.

``test_adr0024_rules_are_tested`` reads ``docs/adr/0024-provider-adapters-byok-sarvam-rest.md``
(the format of ``test_adr0021_*`` and ``test_adr0022_*`` of tracer-evaldb): every numbered rule of
"## Decision" and every invariant cites at least one test, and at least one of the cited tests
exists in a ``packages/*/tests`` directory. A cited name that exists nowhere must be on
``PENDING``, and ``PENDING`` may hold only names that section A of ``docs/phase3/SCOPE.md`` names
(the task table of the later batches). A name that has landed must leave ``PENDING`` (a tightening
edit of this file, made by the batch that adds the test), so the set shrinks to empty by the gate
P3-G.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "uv.lock").is_file())
ADR24 = ROOT / "docs" / "adr" / "0024-provider-adapters-byok-sarvam-rest.md"
SCOPE = ROOT / "docs" / "phase3" / "SCOPE.md"

# Cited by ADR 0024, named by SCOPE section A, and written by a later batch:
#   test_stt_failure_is_missing_input_with_warning_and_nonzero_exit  P3-20 (batch D)
#   test_cli_fake_stt_runs_offline                                   P3-22 (batch E)
#   test_guard_rejects_staged_report_store_and_call_log              P3-34 (batch F)
PENDING: frozenset[str] = frozenset(
    {
        "test_cli_fake_stt_runs_offline",
        "test_guard_rejects_staged_report_store_and_call_log",
    }
)

BATCH_B_TASKS = ("P3-06", "P3-07", "P3-08", "P3-09", "P3-10", "P3-11")
# Names the batch B contract adds to the SCOPE rows (api-surface-batch-b.md section 12).
ADDED_BY_CONTRACT = ("test_unit_tests_open_no_socket",)


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "packages").glob("*/tests/test_*.py")):
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


def _scope_section_a() -> str:
    text = SCOPE.read_text("utf-8")
    return text[text.index("## A. Task table") : text.index("## B. Dependency graph")]


def test_adr0024_rules_are_tested() -> None:
    text = ADR24.read_text("utf-8")
    defined = _defined_tests()
    rules = _numbered(_section(text, "## Decision"), r"(\d+)\. \*\*")
    assert [int(label) for label, _ in rules] == list(range(1, 18)), "ADR 0024 has 17 rules"
    for label, body in rules:
        cited = _cited(body)
        assert cited, f"rule {label} of ADR 0024 cites no test"
        assert cited & defined, f"rule {label} cites no test that exists: {sorted(cited)}"
        unknown = cited - defined - PENDING
        assert not unknown, f"rule {label} cites tests that do not exist: {sorted(unknown)}"
    invariants = _numbered(_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 10)]
    for label, body in invariants:
        cited = _cited(body)
        assert cited & defined, f"invariant {label} of ADR 0024 cites no test that exists"
        unknown = cited - defined - PENDING
        assert not unknown, f"invariant {label}: {sorted(unknown)}"
    # the pending names are the ones the SCOPE task table gives to later batches, and no more
    scope_names = _cited(_scope_section_a())
    assert scope_names >= PENDING, sorted(PENDING - scope_names)
    cited_anywhere = _cited(text)
    assert cited_anywhere >= PENDING, "a pending name that the ADR no longer cites is stale"
    # a name that has landed leaves PENDING (the set shrinks to empty by the gate)
    landed = PENDING & defined
    assert not landed, f"these tests exist now; remove them from PENDING: {sorted(landed)}"
    # the rule that the ADR checks itself
    assert "test_adr0024_rules_are_tested" in defined
    for name in ADDED_BY_CONTRACT:
        assert name in defined, name


def test_adr0024_cites_only_names_that_follow_the_test_naming_rule() -> None:
    text = ADR24.read_text("utf-8")
    for name in _cited(text):
        assert re.fullmatch(r"test_[a-z0-9_]+", name), name


def _scope_rows() -> dict[str, str]:
    rows = {}
    for line in SCOPE.read_text("utf-8").splitlines():
        match = re.match(r"\| (P3-[\w]+) \|", line)
        if match:
            rows[match.group(1)] = line
    return rows


@pytest.mark.parametrize("task", BATCH_B_TASKS)
def test_every_test_named_by_the_scope_rows_of_batch_b_exists(task: str) -> None:
    row = _scope_rows()[task]
    named = _cited(row)
    assert named, task
    missing = named - _defined_tests()
    assert not missing, f"{task}: {sorted(missing)}"


def test_the_scope_rows_of_batch_b_name_the_expected_counts() -> None:
    expected = {"P3-06": 5, "P3-07": 7, "P3-08": 5, "P3-09": 9, "P3-10": 4, "P3-11": 3}
    rows = _scope_rows()
    assert {t: len(_cited(rows[t])) for t in BATCH_B_TASKS} == expected
