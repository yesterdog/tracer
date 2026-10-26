# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0028 machine-read checks (Phase 4 batch 0, task P4-00c), in the format of
``test_adr0023_recorded_scoring.py``.

``test_adr0028_rules_are_tested``: the 12 numbered rules of "## Decision" and the invariants I1 to
I7 each cite at least one test name in backticks; each cited name is defined in a
``packages/*/tests`` file or is on ``PENDING["0028"]`` (``evalcore_phase4_adr_support``), which
holds only names SCOPE section A names and shrinks to empty by the gate P4-G.
"""

from __future__ import annotations

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

SPEC = sup.SPECS["0028"]


def test_adr0028_rules_are_tested() -> None:
    problems = sup.check_adr_file("0028")
    assert problems == [], "\n".join(problems)


def test_adr0028_has_12_rules_and_7_invariants_with_the_pinned_labels() -> None:
    rules, invariants = sup.items_of(SPEC, sup.ADR28.read_text("utf-8"))
    assert [label for label, _ in rules] == [str(i) for i in range(1, 13)], "ADR 0028 has 12 rules"
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 8)]
    assert (len(rules), len(invariants)) == (12, 7)


def test_adr0028_last_invariant_cites_its_own_test_and_no_item_swallows_a_heading() -> None:
    text = sup.ADR28.read_text("utf-8")
    assert "`test_adr0028_rules_are_tested`" in sup.heading_section(text, "## Decision")
    rules, invariants = sup.items_of(SPEC, text)
    assert sup.cited(invariants[-1][1]) == {"test_adr0028_rules_are_tested"}
    for label, body in rules + invariants:
        assert "\n## " not in body, label
        assert "\n### " not in body, label


def test_adr0028_is_accepted() -> None:
    lines = [ln for ln in sup.ADR28.read_text("utf-8").splitlines() if ln.startswith("- Status:")]
    assert len(lines) == 1
    assert lines[0].startswith("- Status: Accepted")


def test_adr0028_pending_names_are_the_scope_names_of_later_tasks_only() -> None:
    pending = sup.PENDING["0028"]
    assert pending, "ADR 0028 cites tests of later tasks; the set empties at the gate"
    assert pending <= sup.scope_names()
    assert not pending & sup.defined_tests()
    assert "test_adr0028_rules_are_tested" not in pending


@pytest.mark.parametrize("name", sup.MUTANT_NAMES)
def test_adr0028_check_has_teeth(name: str) -> None:
    text = sup.ADR28.read_text("utf-8")
    mutant = sup.mutate_each(text, SPEC)[name]
    assert mutant != text, f"the mutation {name!r} changed nothing"
    problems = sup.check_adr(
        SPEC,
        mutant,
        defined=sup.defined_tests(),
        pending=sup.PENDING["0028"],
        scope=sup.scope_names(),
    )
    assert problems, f"the check accepted the mutant {name!r}"


def test_adr0028_check_refuses_pending_sets_that_do_not_fit() -> None:
    text = sup.ADR28.read_text("utf-8")
    defined = sup.defined_tests()
    scope = sup.scope_names()
    pending = sup.PENDING["0028"]
    kw = {"defined": defined, "scope": scope}
    assert sup.check_adr(SPEC, text, pending=pending, **kw) == sup.check_adr_file("0028")
    assert sup.check_adr(SPEC, text, pending=frozenset(), **kw)
    assert sup.check_adr(SPEC, text, pending=pending | {"test_zz_invented"}, **kw)
    stale = next(iter(sorted(scope - sup.cited(text) - defined)))
    assert sup.check_adr(SPEC, text, pending=pending | {stale}, **kw)
    landed = sorted(pending)[0]
    assert sup.check_adr(SPEC, text, pending=pending, defined=defined | {landed}, scope=scope)
