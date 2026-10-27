# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0030 machine-read checks (Phase 4 batch J, task P4-J01), in the format of
``test_adr0029_rules_are_tested.py``.

``test_adr0030_rules_are_tested``: the 25 numbered rules of "## Decision" and the invariants I1 to
I9 each cite at least one test name in backticks; each cited name is defined in a
``packages/*/tests`` file or is on ``PENDING["0030"]`` (``evalcore_phase4_adr_support``), which
holds only names SCOPE section A.10 names for the tasks of part 2 of batch J (P4-J17 and later)
and shrinks to empty by the gate P4-G. Every rule carries its rejected options and its trade-off
(SCOPE P4-J01). The ADR is Accepted: the owner gates O-J1 to O-J6 are before the first real judge
request (P4-J28), none before the offline work.
"""

from __future__ import annotations

import re

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

ADR30 = sup.ADR_DIR / "0030-llm-judge-uncalibrated-signal.md"
EXPECTED_STATUS = "Accepted"
COST_TEST = "test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost"


def spec() -> sup.AdrSpec:
    """The pinned shape of ADR 0030; read inside each test, so that a missing entry fails that test
    and not the collection of the module."""
    return sup.SPECS["0030"]


def _task_rows() -> dict[str, str]:
    """SCOPE section A.10: the row of each task, by its identifier."""
    rows: dict[str, str] = {}
    for line in sup.scope_section_a().splitlines():
        match = re.match(r"\| (P4-J\d\d) \|", line)
        if match:
            rows[match.group(1)] = line
    return rows


def _names_of(first: int, last: int) -> set[str]:
    rows = _task_rows()
    names: set[str] = set()
    for number in range(first, last + 1):
        names |= sup.cited(rows[f"P4-J{number:02d}"])
    return names


def test_adr0030_rules_are_tested() -> None:
    problems = sup.check_adr_file("0030")
    assert problems == [], "\n".join(problems)


def test_adr0030_has_25_rules_and_9_invariants_with_the_pinned_labels() -> None:
    rules, invariants = sup.items_of(spec(), ADR30.read_text("utf-8"))
    assert [label for label, _ in rules] == [str(i) for i in range(1, 26)], "ADR 0030 has 25 rules"
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 10)]
    assert (len(rules), len(invariants)) == (25, 9)
    assert (spec().rules, spec().invariants, spec().self_test) == (
        25,
        9,
        "test_adr0030_rules_are_tested",
    )


def test_adr0030_last_invariant_cites_its_own_test_and_no_item_swallows_a_heading() -> None:
    text = ADR30.read_text("utf-8")
    assert "`test_adr0030_rules_are_tested`" in sup.heading_section(text, "## Decision")
    rules, invariants = sup.items_of(spec(), text)
    assert sup.cited(invariants[-1][1]) == {"test_adr0030_rules_are_tested"}
    for label, body in rules + invariants:
        assert "\n## " not in body, label
        assert "\n### " not in body, label


def test_adr0030_every_rule_has_rejected_options_and_a_trade_off() -> None:
    """SCOPE P4-J01: numbered rules with rejected options and trade-offs."""
    rules, _ = sup.items_of(spec(), ADR30.read_text("utf-8"))
    for label, body in rules:
        assert "Rejected" in body, f"rule {label} states no rejected option"
        assert "Trade-off:" in body, f"rule {label} states no trade-off"


def test_adr0030_is_accepted_and_the_index_row_agrees() -> None:
    status = [ln for ln in ADR30.read_text("utf-8").splitlines() if ln.startswith("- Status:")]
    assert len(status) == 1
    assert status[0].startswith(f"- Status: {EXPECTED_STATUS}")
    rows = [ln for ln in sup.README.read_text("utf-8").splitlines() if ln.startswith("| [0030]")]
    assert len(rows) == 1
    cells = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cells[2] == EXPECTED_STATUS
    assert "(0030-llm-judge-uncalibrated-signal.md)" in cells[0]
    # the staged copy no longer exists once the ADR is moved to docs/adr
    staged = sup.ROOT / "docs" / "phase4" / "staging" / "0030-llm-judge-uncalibrated-signal.md"
    assert not staged.exists(), "the staged copy moved to docs/adr/ (P4-J01)"


def test_adr0030_index_revision_52_announces_the_adr_and_the_three_amendments() -> None:
    """P4-J01 and P4-J02: the index header is at least revision 52, the section of revision 52
    announces ADR 0030 with its counts and each amendment with its label, and the older sections
    stay (the old header check accepts any revision from 50, so this one pins the bump)."""
    text = sup.README.read_text("utf-8")
    header = next(line for line in text.splitlines() if line.startswith("Revision "))
    match = re.match(r"Revision (\d+), (\d{4}-\d{2}-\d{2})\.", header)
    assert match is not None, header[:40]
    assert int(match.group(1)) >= 52, header[:40]
    assert "ADRs 0026 to 0030" in header
    section = sup.heading_section(text, "## Changes in revision 52")
    assert re.search(r"\*\*New ADR 0030\*\*.*25 numbered rules and invariants I1 to I9", section)
    assert re.search(r"ADR 0023 amended\*\*.*rule A4.*invariants IA1 and IA2", section)
    assert re.search(r"ADR 0024 amended\*\*.*rules A10 to A13.*invariants IA1 to IA4", section)
    assert re.search(r"ADR 0027 rule 12a added\*\*.*17 rules and I1 to I10", section)
    for older in ("## Changes in revision 51", "## Changes in revision 50"):
        assert sup.heading_section(text, older)
    assert len(re.findall(r"^## Changes in revision 52[ \t]*$", text, re.MULTILINE)) == 1


def test_adr0030_rule_18_cites_the_cost_test_of_ambiguity_6() -> None:
    """Ambiguity 6: the cost rule names a test: cost is ``not_available`` without a USD catalog and
    OpenRouter credits are never stored as a cost; SCOPE row P4-J12 names the same test."""
    rules, _ = sup.items_of(spec(), ADR30.read_text("utf-8"))
    rule18 = dict(rules)["18"]
    assert COST_TEST in sup.cited(rule18)
    assert COST_TEST in sup.cited(_task_rows()["P4-J12"])
    assert COST_TEST in sup.defined_tests()


def test_adr0030_pending_names_are_the_scope_names_of_later_tasks_only() -> None:
    pending = sup.PENDING["0030"]
    assert pending, "ADR 0030 cites tests of later tasks; the set empties at the gate"
    assert pending <= sup.scope_names()
    assert not pending & sup.defined_tests()
    assert "test_adr0030_rules_are_tested" not in pending
    # a pending name belongs to a task after P4-J16 (batch J part 2); part 1 names are all defined
    assert pending <= _names_of(17, 32)
    assert not pending & _names_of(1, 16), "a name of P4-J01 to P4-J16 must be defined by now"
    for name in sorted(_names_of(1, 16)):
        assert name in sup.defined_tests(), name


def test_adr0030_amendments_are_applied_to_the_three_earlier_adrs() -> None:
    """P4-J02: A4 of ADR 0023, A10 to A13 of ADR 0024 and rule 12a of ADR 0027 are in the text;
    the Decision-block counts of the three ADRs are unchanged (12a is not a numbered label)."""
    text23 = sup.ADR23.read_text("utf-8")
    text24 = sup.ADR24.read_text("utf-8")
    text27 = sup.ADR27.read_text("utf-8")
    rules23, _ = sup.items_of(sup.SPECS["0023A"], text23)
    rules24, _ = sup.items_of(sup.SPECS["0024A"], text24)
    assert [label for label, _ in rules23] == ["A1", "A2", "A3", "A4"]
    assert [label for label, _ in rules24] == [f"A{i}" for i in range(1, 14)]
    a4 = dict(rules23)["A4"]
    sentence = (
        "Tracer's judge is an uncalibrated language-model signal: it reports whether a model "
        "said that an agent's answer agrees with the expected facts; it does not measure "
        "accuracy, correctness, factuality or quality, it is not a pass or fail of the agent, "
        "and none of its numbers is a confidence, a probability, an error rate, a recall or a "
        "precision."
    )
    assert sentence in a4
    a4_names = sup.cited(a4)
    assert {
        "test_judge_report_never_claims_accuracy_or_quality",
        "test_agreement_report_counts_only_never_rates_or_accuracy",
        "test_judge_concept_page_states_no_claims",
    } <= a4_names
    for label in ("A10", "A11", "A12", "A13"):
        assert "ADR 0030" in dict(rules24)[label], label
    decision27 = sup.heading_section(text27, "## Decision")
    assert len(re.findall(r"^12a\. \*\*", decision27, re.MULTILINE)) == 1
    rules27 = sup.numbered(decision27, r"(\d+)\. \*\*")
    assert [label for label, _ in rules27] == [str(i) for i in range(1, 18)]
    body12 = dict(rules27)["12"]
    assert "12a." in body12, "rule 12a is read as part of rule 12"
    assert {
        "test_expected_outcome_check_stays_inert_with_judge",
        "test_judged_bundle_passes_check_run_bundle",
    } <= sup.cited(body12)
    # the three earlier ADRs keep reading with their own pinned counts
    assert sup.SPECS["0023A"].rules == 4
    assert sup.SPECS["0024A"].rules == 13
    assert sup.check_adr_file("0023A") == []
    assert sup.check_adr_file("0024A") == []
    assert sup.check_adr_file("0027") == []


@pytest.mark.parametrize("name", sup.MUTANT_NAMES)
def test_adr0030_check_has_teeth(name: str) -> None:
    text = ADR30.read_text("utf-8")
    mutant = sup.mutate_each(text, spec())[name]
    assert mutant != text, f"the mutation {name!r} changed nothing"
    problems = sup.check_adr(
        spec(),
        mutant,
        defined=sup.defined_tests(),
        pending=sup.PENDING["0030"],
        scope=sup.scope_names(),
    )
    assert problems, f"the check accepted the mutant {name!r}"


def test_adr0030_check_refuses_pending_sets_that_do_not_fit() -> None:
    text = ADR30.read_text("utf-8")
    defined = sup.defined_tests()
    scope = sup.scope_names()
    pending = sup.PENDING["0030"]
    kw = {"defined": defined, "scope": scope}
    assert sup.check_adr(spec(), text, pending=pending, **kw) == sup.check_adr_file("0030") == []
    assert sup.check_adr(spec(), text, pending=frozenset(), **kw)
    assert sup.check_adr(spec(), text, pending=pending | {"test_zz_invented"}, **kw)
    stale = next(iter(sorted(scope - sup.cited(text) - defined)))
    assert sup.check_adr(spec(), text, pending=pending | {stale}, **kw)
    landed = sorted(pending)[0]
    assert sup.check_adr(spec(), text, pending=pending, defined=defined | {landed}, scope=scope)
