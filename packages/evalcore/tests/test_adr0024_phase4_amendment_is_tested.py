# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Phase 4 amendment of ADR 0024 is machine-read (Phase 4 batch 0, task P4-00d).

``test_adr0024_rules_are_tested`` (which stays as it is) pins the Decision block of ADR 0024: 17
rules and 9 invariants, and reads nothing after "## Decision" ends. The amendment is a separate
section, "## Phase 4 amendment: ...", with rules A1 to A13 and invariants IA1 to IA4, so that
the old test is not edited. This module reads that section in the format of the old test: every
rule and every invariant cites at least one test name in backticks; a cited name is defined in a
``packages/*/tests`` file or is on ``PENDING["0024A"]`` (``evalcore_phase4_adr_support``), which
holds only names SCOPE section A names and shrinks to empty by the gate P4-G.
"""

from __future__ import annotations

import re

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

SPEC = sup.SPECS["0024A"]
OLD = sup.AdrSpec("0024", sup.ADR24, 17, 9)


def test_adr0024_phase4_amendment_is_tested() -> None:
    problems = sup.check_adr_file("0024A")
    assert problems == [], "\n".join(problems)


def test_adr0024_phase4_amendment_has_the_pinned_labels() -> None:
    rules, invariants = sup.items_of(SPEC, sup.ADR24.read_text("utf-8"))
    assert [label for label, _ in rules] == [f"A{i}" for i in range(1, 14)]
    assert [label for label, _ in invariants] == [f"IA{i}" for i in range(1, 5)]


def test_adr0024_phase4_amendment_leaves_the_decision_block_counts_alone() -> None:
    """The amendment must not change what ``test_adr0024_rules_are_tested`` pins (not edited)."""
    text = sup.ADR24.read_text("utf-8")
    decision_rules = sup.numbered(sup.heading_section(text, "## Decision"), r"(\d+)\. \*\*")
    assert [label for label, _ in decision_rules] == [str(i) for i in range(1, 18)]
    invariants = sup.numbered(sup.heading_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 10)]
    # no amendment label leaks into the old blocks
    for _, body in decision_rules + invariants:
        assert not re.search(r"^(?:- )?I?A\d+\. ", body, re.MULTILINE)
    # and the old check still passes with this section present
    assert sup.items_of(OLD, text)


def test_adr0024_phase4_amendment_says_where_it_is_machine_read() -> None:
    section = sup.heading_section(sup.ADR24.read_text("utf-8"), sup.AMENDMENT_HEADING, prefix=True)
    intro = section.split("\nA1. ", 1)[0]
    assert "labelled A1 to A13" in intro
    assert "IA1 to IA4" in intro
    assert "`test_adr0024_rules_are_tested`" in intro
    assert "task P4-00d" in intro


def test_adr0024_phase4_amendment_pending_names_are_scope_names_only() -> None:
    pending = sup.PENDING["0024A"]
    # Phase 4 batch F spec change S-F7: the set may be empty (it is, once batch F and batch J have
    # defined the tests the amendment cites)
    assert pending <= sup.scope_names()
    assert not pending & sup.defined_tests()


@pytest.mark.parametrize("name", sup.MUTANT_NAMES)
def test_adr0024_phase4_amendment_check_has_teeth(name: str) -> None:
    text = sup.ADR24.read_text("utf-8")
    mutant = sup.mutate_each(text, SPEC)[name]
    assert mutant != text, f"the mutation {name!r} changed nothing"
    problems = sup.check_adr(
        SPEC,
        mutant,
        defined=sup.defined_tests(),
        pending=sup.PENDING["0024A"],
        scope=sup.scope_names(),
    )
    assert problems, f"the check accepted the mutant {name!r}"


def test_adr0024_phase4_amendment_check_refuses_pending_sets_that_do_not_fit() -> None:
    text = sup.ADR24.read_text("utf-8")
    defined = sup.defined_tests()
    scope = sup.scope_names()
    pending = sup.PENDING["0024A"]
    assert sup.check_adr(SPEC, text, defined=defined, pending=pending, scope=scope) == []
    # Phase 4 batch F spec change S-F7: the set may be empty, so the refusals are kept by naming
    # a cited test that is not defined (it must be pending) and a defined one on the set (it must
    # leave it)
    cited_defined = sorted(sup.cited(text) & defined)[0]
    assert sup.check_adr(
        SPEC, text, defined=defined - {cited_defined}, pending=pending, scope=scope
    )
    assert sup.check_adr(SPEC, text, defined=defined, pending=pending | {"test_zz_x"}, scope=scope)
    assert sup.check_adr(
        SPEC, text, defined=defined, pending=pending | {cited_defined}, scope=scope
    )


def test_adr0024_phase4_amendment_section_is_found_by_its_own_heading_only() -> None:
    text = sup.ADR24.read_text("utf-8")
    assert sup.heading_section(text, sup.AMENDMENT_HEADING, prefix=True)
    with pytest.raises(AssertionError):
        sup.heading_section(
            text + "\n## Phase 4 amendment: again\nx\n", sup.AMENDMENT_HEADING, prefix=True
        )
    with pytest.raises(AssertionError):
        sup.heading_section(
            text.replace("## Phase 4 amendment", "## Phase 5 amendment"),
            sup.AMENDMENT_HEADING,
            prefix=True,
        )
