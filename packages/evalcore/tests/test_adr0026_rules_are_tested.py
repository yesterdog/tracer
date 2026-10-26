# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0026 machine-read checks (Phase 4 batch 0, task P4-00a), in the format of
``test_adr0023_recorded_scoring.py``.

``test_adr0026_rules_are_tested``: the 24 numbered rules of "## Decision" and the invariants I1 to
I10 each cite at least one test name in backticks; each cited name is defined in a
``packages/*/tests`` file or is on ``PENDING["0026"]`` (``evalcore_phase4_adr_support``), which
holds only names SCOPE section A names and shrinks to empty by the gate P4-G.

The section "Dialect specifications" is edited by P4-16 to P4-18 and by the architect while the
wire formats are fetched: nothing here reads its wording. The rules and invariants end where that
section begins.
"""

from __future__ import annotations

import re

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

SPEC = sup.SPECS["0026"]


def test_adr0026_rules_are_tested() -> None:
    problems = sup.check_adr_file("0026")
    assert problems == [], "\n".join(problems)


def test_adr0026_has_24_rules_and_10_invariants_with_the_pinned_labels() -> None:
    rules, invariants = sup.items_of(SPEC, sup.ADR26.read_text("utf-8"))
    assert [label for label, _ in rules] == [str(i) for i in range(1, 25)], "ADR 0026 has 24 rules"
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 11)]
    assert len(rules) == 24
    assert len(invariants) == 10


def test_adr0026_i10_cites_its_own_test_and_the_decision_says_it_is_machine_read() -> None:
    text = sup.ADR26.read_text("utf-8")
    decision = sup.heading_section(text, "## Decision")
    assert "`test_adr0026_rules_are_tested`" in decision
    _, invariants = sup.items_of(SPEC, text)
    assert sup.cited(invariants[-1][1]) == {"test_adr0026_rules_are_tested"}


def test_adr0026_rules_end_before_the_dialect_section() -> None:
    """The last rule must not swallow the dialect section, nor the dialect section the rule."""
    text = sup.ADR26.read_text("utf-8")
    rules, _ = sup.items_of(SPEC, text)
    last = rules[-1][1]
    assert "## Dialect specifications" not in last
    assert "### " not in last
    dialect = sup.heading_section(text, sup.DIALECT_HEADING)
    assert not re.search(r"^\d+\. \*\*", dialect, re.MULTILINE), "no numbered rule in the dialects"


def test_adr0026_is_accepted() -> None:
    first = [ln for ln in sup.ADR26.read_text("utf-8").splitlines() if ln.startswith("- Status:")]
    assert len(first) == 1
    assert first[0].startswith("- Status: Accepted")


def test_adr0026_pending_names_are_the_scope_names_of_later_tasks_only() -> None:
    pending = sup.PENDING["0026"]
    assert pending, "ADR 0026 cites tests of later tasks; the set empties at the gate"
    assert pending <= sup.scope_names()
    assert not pending & sup.defined_tests()
    # no name that this very batch defines
    assert "test_adr0026_rules_are_tested" not in pending


@pytest.mark.parametrize("name", sup.MUTANT_NAMES)
def test_adr0026_check_has_teeth(name: str) -> None:
    text = sup.ADR26.read_text("utf-8")
    mutant = sup.mutate_each(text, SPEC)[name]
    assert mutant != text, f"the mutation {name!r} changed nothing"
    problems = sup.check_adr(
        SPEC,
        mutant,
        defined=sup.defined_tests(),
        pending=sup.PENDING["0026"],
        scope=sup.scope_names(),
    )
    assert problems, f"the check accepted the mutant {name!r}"


def test_adr0026_check_refuses_pending_sets_that_do_not_fit() -> None:
    text = sup.ADR26.read_text("utf-8")
    defined = sup.defined_tests()
    scope = sup.scope_names()
    pending = sup.PENDING["0026"]
    kw = {"defined": defined, "scope": scope}
    assert sup.check_adr(SPEC, text, pending=pending, **kw) == sup.check_adr_file("0026")
    # an empty set: every pending name is now an unknown name
    assert sup.check_adr(SPEC, text, pending=frozenset(), **kw)
    # a name SCOPE does not name
    assert sup.check_adr(SPEC, text, pending=pending | {"test_zz_invented"}, **kw)
    # a name the ADR does not cite is stale (even when SCOPE names it)
    stale = next(iter(scope - sup.cited(text) - defined))
    assert sup.check_adr(SPEC, text, pending=pending | {stale}, **kw)
    # a name a test defines must leave the set
    landed = next(iter(sorted(pending)))
    assert sup.check_adr(SPEC, text, pending=pending, defined=defined | {landed}, scope=scope)


def test_the_mutant_names_are_the_mutants() -> None:
    assert tuple(sup.mutate_each(sup.ADR26.read_text("utf-8"), SPEC)) == sup.MUTANT_NAMES
