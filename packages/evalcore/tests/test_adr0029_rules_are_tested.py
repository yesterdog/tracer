# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0029 machine-read checks (Phase 4 batch 0, task P4-00e), in the format of
``test_adr0023_recorded_scoring.py``.

``test_adr0029_rules_are_tested``: the 10 numbered rules of "## Decision" and the invariants I1 to
I4 each cite at least one test name in backticks; each cited name is defined in a
``packages/*/tests`` file or is on ``PENDING["0029"]`` (``evalcore_phase4_adr_support``), which
holds only names SCOPE section A names and shrinks to empty by the gate P4-G.

ADR 0029 is Proposed until owner question O5 (SCOPE section E). ``EXPECTED_STATUS`` pins that: the
day P4-40 makes it Accepted, the test fails until the same commit changes the constant, the index
row and the revision's Changes entry together.
"""

from __future__ import annotations

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

SPEC = sup.SPECS["0029"]

#: P4-40 changes this to "Accepted" with the index revision (a named spec change).
EXPECTED_STATUS = "Proposed"


def test_adr0029_rules_are_tested() -> None:
    problems = sup.check_adr_file("0029")
    assert problems == [], "\n".join(problems)


def test_adr0029_has_10_rules_and_4_invariants_with_the_pinned_labels() -> None:
    rules, invariants = sup.items_of(SPEC, sup.ADR29.read_text("utf-8"))
    assert [label for label, _ in rules] == [str(i) for i in range(1, 11)], "ADR 0029 has 10 rules"
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 5)]
    assert (len(rules), len(invariants)) == (10, 4)


def test_adr0029_last_invariant_cites_its_own_test_and_no_item_swallows_a_heading() -> None:
    text = sup.ADR29.read_text("utf-8")
    assert "`test_adr0029_rules_are_tested`" in sup.heading_section(text, "## Decision")
    rules, invariants = sup.items_of(SPEC, text)
    assert sup.cited(invariants[-1][1]) == {"test_adr0029_rules_are_tested"}
    for label, body in rules + invariants:
        assert "\n## " not in body, label
        assert "\n### " not in body, label


def test_adr0029_is_proposed_until_p4_40() -> None:
    """Pinned on purpose: P4-40 flips this ADR to Accepted for the provider the owner names (O5)
    and, in the same commit, changes ``EXPECTED_STATUS`` below, the index row and the Changes
    entry. A flip in the ADR alone fails this test (named spec change, contract section 5)."""
    lines = [ln for ln in sup.ADR29.read_text("utf-8").splitlines() if ln.startswith("- Status:")]
    assert len(lines) == 1
    assert lines[0].startswith(f"- Status: {EXPECTED_STATUS}")
    row = [ln for ln in sup.README.read_text("utf-8").splitlines() if ln.startswith("| [0029]")]
    assert len(row) == 1
    assert f"| {EXPECTED_STATUS} |" in row[0]
    changes = sup.heading_section(sup.README.read_text("utf-8"), "## Changes in revision 50")
    assert f"**{EXPECTED_STATUS}**" in changes.split("New ADR 0029", 1)[1].split("\n", 1)[0]


def test_adr0029_rules_bind_no_code_while_proposed() -> None:
    text = sup.ADR29.read_text("utf-8")
    decision = sup.heading_section(text, "## Decision")
    assert "While this ADR is Proposed its rules bind no code" in decision


def test_adr0029_pending_names_are_the_scope_names_of_later_tasks_only() -> None:
    pending = sup.PENDING["0029"]
    assert pending, "ADR 0029 cites tests of later tasks; the set empties at the gate"
    assert pending <= sup.scope_names()
    assert not pending & sup.defined_tests()
    assert "test_adr0029_rules_are_tested" not in pending


@pytest.mark.parametrize("name", sup.MUTANT_NAMES)
def test_adr0029_check_has_teeth(name: str) -> None:
    text = sup.ADR29.read_text("utf-8")
    mutant = sup.mutate_each(text, SPEC)[name]
    assert mutant != text, f"the mutation {name!r} changed nothing"
    problems = sup.check_adr(
        SPEC,
        mutant,
        defined=sup.defined_tests(),
        pending=sup.PENDING["0029"],
        scope=sup.scope_names(),
    )
    assert problems, f"the check accepted the mutant {name!r}"


def test_adr0029_check_refuses_pending_sets_that_do_not_fit() -> None:
    text = sup.ADR29.read_text("utf-8")
    defined = sup.defined_tests()
    scope = sup.scope_names()
    pending = sup.PENDING["0029"]
    kw = {"defined": defined, "scope": scope}
    assert sup.check_adr(SPEC, text, pending=pending, **kw) == sup.check_adr_file("0029")
    assert sup.check_adr(SPEC, text, pending=frozenset(), **kw)
    assert sup.check_adr(SPEC, text, pending=pending | {"test_zz_invented"}, **kw)
    stale = next(iter(sorted(scope - sup.cited(text) - defined)))
    assert sup.check_adr(SPEC, text, pending=pending | {stale}, **kw)
    landed = sorted(pending)[0]
    assert sup.check_adr(SPEC, text, pending=pending, defined=defined | {landed}, scope=scope)
