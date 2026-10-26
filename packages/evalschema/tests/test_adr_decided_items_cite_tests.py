# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Follow-up of batch J (finding F2): the decided items ADR 0016 P-47 and ADR 0004 P-08 cite the
tests that bind them, every name they cite in backticks exists, and a name of a test that was
replaced or never written is not written in backticks.
Contract: docs/phase3/api-surface-batch-j2.md.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
ADR16 = ROOT / "docs" / "adr" / "0016-roles-caller-origin-privacy.md"
ADR04 = ROOT / "docs" / "adr" / "0004-requires-and-not-applicable.md"

P47_NAMES = {
    "test_human_subject_looser_marker_rejected",
    "test_human_subject_stricter_marker_requires_reason",
    "test_human_subject_manual_true_always_allowed",
    "test_marker_before_role_evidence_stays_valid_with_reason",
}
P08_NAMES = {"test_scored_without_pass_rule_has_passed_none"}


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "packages").glob("*/tests/test_*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        names |= {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test_")
        }
    return names


def _cited(text: str) -> set[str]:
    return set(re.findall(r"`(test_\w+)`", text))


def _item(text: str, start: str) -> str:
    found = [ln for ln in text.splitlines() if ln.startswith(start)]
    assert len(found) == 1, (start, len(found))
    return found[0]


def decided_item_problems(line: str, required: set[str], defined: set[str]) -> list[str]:
    cited = _cited(line)
    problems = [f"does not cite {n}" for n in sorted(required - cited)]
    problems += [f"cites {n}, which no test defines" for n in sorted(cited - defined)]
    return problems


def test_adr0016_p47_cites_the_tests_that_bind_it_and_only_existing_names() -> None:
    line = _item(ADR16.read_text("utf-8"), "- **P-47. ")
    assert decided_item_problems(line, P47_NAMES, _defined_tests()) == []


def test_adr0004_p08_cites_the_test_that_binds_it_and_only_existing_names() -> None:
    line = _item(ADR04.read_text("utf-8"), "- **P-08. ")
    assert decided_item_problems(line, P08_NAMES, _defined_tests()) == []


def test_decided_item_check_has_teeth() -> None:
    defined = P47_NAMES | P08_NAMES
    good = "- **P-47. x.** Tests: " + ", ".join(f"`{n}`" for n in sorted(P47_NAMES)) + "."
    assert decided_item_problems(good, P47_NAMES, defined) == []
    old = good + " `test_human_subject_marker_contradiction_rejected` was replaced."
    assert decided_item_problems(old, P47_NAMES, defined)
    assert decided_item_problems(
        good.replace("`test_human_subject_looser_marker_rejected`, ", ""), P47_NAMES, defined
    )
    assert decided_item_problems("- **P-08. x.**", P08_NAMES, defined)
    assert decided_item_problems(
        "- **P-08. x.** `test_every_metric_has_pass_rule`", P08_NAMES, defined
    )
    assert "test_every_metric_has_pass_rule" not in defined
