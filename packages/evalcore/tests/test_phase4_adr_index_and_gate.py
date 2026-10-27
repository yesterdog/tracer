# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The ADR index (``docs/adr/README.md`` revision 50) agrees with the ADRs it announces, and the
gate check that every Phase 4 pending set is empty (Phase 4 batch 0).

``test_phase4_adr_pending_sets_are_empty_at_the_gate`` is the P4-G gate item 13 and 2 in code. It
is opt-in (``TRACER_PHASE4_GATE=1``) because the pending sets are not empty while Phase 4 is being
built; the lead runs it at the gate, and the dev deletes the skip in the commit that empties the
last set (when every set is empty it passes either way, so the skip is then dead code).
"""

from __future__ import annotations

import os
import re

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

STATUS = {
    "0026": "Accepted",
    "0027": "Accepted",
    "0028": "Accepted",
    "0029": "Proposed",
    "0030": "Accepted",
}


def _changes(revision: int = 50) -> str:
    section: str = sup.heading_section(
        sup.README.read_text("utf-8"), f"## Changes in revision {revision}"
    )
    return section


def _bullet(adr: str) -> str:
    """ADRs 0026 to 0029 are announced in revision 50, ADR 0030 in revision 52 (batch J)."""
    lines = [
        ln
        for ln in _changes(52 if adr == "0030" else 50).splitlines()
        if f"**New ADR {adr}**" in ln
    ]
    assert len(lines) == 1, adr
    return lines[0]


@pytest.mark.parametrize("adr", ["0026", "0027", "0028", "0029", "0030"])
def test_the_index_announces_the_rule_and_invariant_counts_the_adr_has(adr: str) -> None:
    spec = sup.SPECS[adr]
    rules, invariants = sup.items_of(spec, spec.path.read_text("utf-8"))
    match = re.search(r"(\d+) numbered rules and invariants I1 to I(\d+)", _bullet(adr))
    assert match is not None, adr
    assert (int(match.group(1)), int(match.group(2))) == (len(rules), len(invariants))
    assert f"`test_adr{adr}_rules_are_tested`" in _bullet(adr)


@pytest.mark.parametrize("adr", ["0026", "0027", "0028", "0029", "0030"])
def test_the_index_row_and_the_adr_agree_on_status(adr: str) -> None:
    rows = [ln for ln in sup.README.read_text("utf-8").splitlines() if ln.startswith(f"| [{adr}]")]
    assert len(rows) == 1, adr
    cells = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cells[2] == STATUS[adr], (adr, cells[2])
    status = [
        ln
        for ln in sup.SPECS[adr].path.read_text("utf-8").splitlines()
        if ln.startswith("- Status:")
    ]
    assert len(status) == 1
    assert status[0].startswith(f"- Status: {STATUS[adr]}")


def test_the_index_announces_the_three_amendments_with_their_labels() -> None:
    changes = _changes()
    assert re.search(r"ADR 0024 amended\*\*.*rules A1 to A9 and invariants IA1 to IA4", changes)
    assert re.search(r"ADR 0023 amended\*\*.*rules A1 to A3 and invariants IA1 and IA2", changes)
    assert re.search(r"ADR 0004 Decision 11 added\*\*.*invariant I43", changes)
    assert index_header_problems(sup.README.read_text("utf-8")) == []


def index_header_problems(text: str) -> list[str]:
    """Named spec change (revision 51): the header may move on; revision 50 must stay announced."""
    problems: list[str] = []
    header = next((ln for ln in text.splitlines() if ln.startswith("Revision ")), "")
    match = re.match(r"Revision (\d+)\b", header)
    if match is None or int(match.group(1)) < 50:
        problems.append(f"the index header is not 'Revision N' with N >= 50: {header[:30]!r}")
    if len(re.findall(r"^## Changes in revision 50[ \t]*$", text, re.MULTILINE)) != 1:
        problems.append("the section 'Changes in revision 50' is not there exactly once")
    return problems


def test_the_index_header_check_has_teeth() -> None:
    text = sup.README.read_text("utf-8")
    assert index_header_problems(text) == []
    # revision 52 fix: replacing "Revision 5" by "Revision 49" reads "Revision 492" at any revision
    # from 52 to 59, which the check accepts; the mutant now replaces the whole number of the header
    assert index_header_problems(
        re.sub(r"^Revision \d+", "Revision 49", text, count=1, flags=re.MULTILINE)
    )
    assert index_header_problems(
        text.replace("## Changes in revision 50", "## Changes in revision 5O", 1)
    )
    assert index_header_problems(text.replace("Revision ", "Rev. ", 1))
    assert index_header_problems(text.replace("Revision 5", "Revision 99", 1)) == []


def test_the_scope_names_the_batch_zero_tests_of_this_batch() -> None:
    names = sup.scope_names()
    for name in (
        "test_adr0026_rules_are_tested",
        "test_adr0027_rules_are_tested",
        "test_adr0027_documents_match_json_schemas",
        "test_adr0028_rules_are_tested",
        "test_adr0029_rules_are_tested",
        "test_adr0024_rules_are_tested",
        "test_adr0023_rules_are_tested",
        "test_adr0004_simulated_agent_transcript_rule_cites_test",
    ):
        assert name in names, name
        assert name in sup.defined_tests(), name


def test_every_pending_name_is_a_valid_identifier_and_in_at_most_one_set_of_a_kind() -> None:
    seen: dict[str, str] = {}
    for key, names in sup.PENDING.items():
        for name in names:
            assert re.fullmatch(r"test_[a-z0-9_]+", name), name
            seen.setdefault(name, key)
    assert sup.PENDING_OUTSIDE_RULES.isdisjoint(seen)


def test_the_gate_check_reports_every_non_empty_set() -> None:
    problems = sup.gate_problems()
    assert len(problems) == sum(1 for v in sup.PENDING.values() if v) + bool(
        sup.PENDING_OUTSIDE_RULES
    )


def test_phase4_adr_pending_sets_are_empty_at_the_gate() -> None:
    problems = sup.gate_problems()
    if not problems:
        return
    if os.environ.get("TRACER_PHASE4_GATE") != "1":
        pytest.skip(
            "opt-in gate check (TRACER_PHASE4_GATE=1); pending sets shrink as Phase 4 lands"
        )
    assert problems == [], "\n".join(problems)
