# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The whole-ADR reader for Phase 4 (Phase 4 batch 0; in the spirit of BL-265).

``test_no_adr_from_0026_cites_a_test_that_neither_exists_nor_is_pending``: every ADR from 0026
onward (read whole, not only its rules), the Phase 4 amendment sections of ADRs 0023 and 0024 and
Decision 11 and I43 of ADR 0004 cite in backticks only names that a ``packages/*/tests`` file
defines or that are on a pending set of ``evalcore_phase4_adr_support``. A name written WITHOUT
backticks is invisible to the other readers, so a bare ``test_*`` name is refused too, except a
name that SCOPE Decisions D-a renamed and the ADR keeps as history (``RENAMED``).

The section "Dialect specifications" of ADR 0026 is completed by P4-16 to P4-18: a name there
needs only to be one that SCOPE section A names. The wording of that section is not read.
"""

from __future__ import annotations

import re

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]


def _problems(
    reads: list[sup.Reading] | None = None,
    *,
    defined: set[str] | None = None,
    pending: dict[str, frozenset[str]] | None = None,
    outside: frozenset[str] | None = None,
    scope_text: str | None = None,
) -> list[str]:
    found: list[str] = sup.whole_adr_problems(
        sup.readings() if reads is None else reads,
        defined=sup.defined_tests() if defined is None else defined,
        pending=sup.PENDING if pending is None else pending,
        outside=sup.PENDING_OUTSIDE_RULES if outside is None else outside,
        scope=sup.scope_names(),
        scope_text=sup.SCOPE4.read_text("utf-8") if scope_text is None else scope_text,
    )
    return found


def test_no_adr_from_0026_cites_a_test_that_neither_exists_nor_is_pending() -> None:
    problems = _problems()
    assert problems == [], "\n".join(problems)


def test_the_outside_rules_pending_set_fits_the_scope() -> None:
    problems = sup.pending_outside_problems(
        sup.readings(),
        defined=sup.defined_tests(),
        outside=sup.PENDING_OUTSIDE_RULES,
        scope=sup.scope_names(),
    )
    assert problems == [], "\n".join(problems)


def test_the_reader_covers_every_adr_from_0026_and_the_three_amendments() -> None:
    labels = [r.label for r in sup.readings()]
    for adr in (
        "ADR 0026",
        "ADR 0027",
        "ADR 0028",
        "ADR 0029",
        "ADR 0030",
        "ADR 0024A",
        "ADR 0023A",
        "ADR 0004",
    ):
        assert adr in labels, adr
    numbers = [sup.adr_number(p) for p in sup.later_adr_paths()]
    assert numbers == sorted(numbers)
    assert {26, 27, 28, 29, 30} <= set(numbers)
    assert all(n >= 26 for n in numbers)


def test_a_later_adr_is_read_too_and_has_no_pending_set() -> None:
    """An ADR 0031 would be read whole; it can cite only existing tests (no registry key). Batch J
    spec change: this was ADR 0030 until ADR 0030 got its own pending set."""
    text = "# ADR 0031: x\n\n## Decision\n1. **a.** `test_zz_not_there`.\n"
    reads = [sup.Reading("ADR 0031", text, "1. **a.** `test_zz_not_there`.")]
    assert _problems(reads), "an ADR from 0030 cites an undefined name"
    defined = sup.defined_tests() | {"test_zz_not_there"}
    assert _problems(reads, defined=defined) == []


def test_bare_test_names_are_refused_except_recorded_renames() -> None:
    for path in sup.later_adr_paths():
        text = path.read_text("utf-8")
        bare = set(re.findall(r"(?<![`\w])(test_[a-z0-9_]+)", text))
        assert bare <= set(sup.RENAMED), (
            f"{path.name}: names written without backticks: {sorted(bare)}"
        )


# ------------------------------------------------------------------------------- teeth


def _read(label: str) -> sup.Reading:
    return next(r for r in sup.readings() if r.label == label)


@pytest.mark.parametrize(
    "label", ["ADR 0026", "ADR 0027", "ADR 0028", "ADR 0029", "ADR 0024A", "ADR 0023A", "ADR 0004"]
)
def test_an_invented_name_anywhere_in_the_text_is_refused(label: str) -> None:
    read = _read(label)
    inside = sup.Reading(read.label, read.full + "\nSee `test_zz_invented_name`.", read.in_items)
    problems = _problems([inside])
    assert any("test_zz_invented_name" in p for p in problems), label


def test_a_pending_name_of_one_document_does_not_excuse_another_typo() -> None:
    read = _read("ADR 0028")
    typo = sup.Reading(
        read.label,
        read.full.replace(
            "test_scripted_mode_makes_no_llm_request", "test_scripted_mode_makes_no_llm_requests"
        ),
        read.in_items,
    )
    assert typo.full != read.full
    assert _problems([typo])


def test_emptied_pending_sets_make_the_reader_fail_until_every_test_exists() -> None:
    empty = {k: frozenset[str]() for k in sup.PENDING}
    assert _problems(pending=empty, outside=frozenset())


def test_the_dialect_section_needs_only_a_scope_name() -> None:
    read = _read("ADR 0026")
    scope_only = next(
        iter(sorted(sup.scope_names() - sup.defined_tests() - set().union(*sup.PENDING.values()))),
        None,
    )
    assert scope_only is not None
    lenient = sup.Reading(
        read.label,
        read.full + f"\n`{scope_only}`",
        read.in_items,
        read.lenient + f"\n`{scope_only}`",
    )
    assert not any(scope_only in p for p in _problems([lenient]))
    strict = sup.Reading(
        read.label,
        read.full + "\n`test_zz_not_in_scope`",
        read.in_items,
        read.lenient + "\n`test_zz_not_in_scope`",
    )
    assert any("test_zz_not_in_scope" in p for p in _problems([strict]))


def test_a_renamed_name_is_history_outside_the_rules_and_a_defect_inside_them() -> None:
    old = next(iter(sup.RENAMED))
    new = sup.RENAMED[old]
    scope = sup.SCOPE4.read_text("utf-8")
    read = _read("ADR 0027")
    history = sup.Reading(read.label, read.full + f"\nWas `{old}`.", read.in_items)
    assert not any(old in p for p in _problems([history], scope_text=scope + f"\n{old} -> `{new}`"))
    assert any(old in p for p in _problems([history], scope_text="no rename recorded"))
    in_rule = sup.Reading(
        read.label, read.full + f"\n- I99. `{old}`.", read.in_items + f"\n- I99. `{old}`."
    )
    assert any(old in p for p in _problems([in_rule], scope_text=scope + f"\n{old} -> `{new}`"))


def test_the_outside_rules_set_is_checked_against_the_scope() -> None:
    reads = sup.readings()
    defined = sup.defined_tests()
    scope = sup.scope_names()
    assert sup.pending_outside_problems(
        reads, defined=defined, outside=frozenset({"test_zz_x"}), scope=scope
    )
    # Phase 4 batch F spec change S-F8: the set is empty now, so a name that landed is shown by the
    # one that was pending (defined, yet still on the set)
    landed = "test_tdeg_sim_agent_silent_timing_not_measured"
    assert landed in defined
    assert sup.pending_outside_problems(
        reads,
        defined=defined | sup.PENDING_OUTSIDE_RULES,
        outside=sup.PENDING_OUTSIDE_RULES | {landed},
        scope=scope,
    )
    # Phase 4 docs batch spec change S-D1: the set of ADR 0026 is empty now; ADR 0030 still has
    # names pending (P4-J29 and P4-J32), cited inside its rules
    inside = next(iter(sorted(sup.PENDING["0030"])))
    assert sup.pending_outside_problems(
        reads, defined=defined, outside=frozenset({inside}), scope=scope
    )
