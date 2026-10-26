# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0004 Decision 11 and invariant I43 (Phase 4 batch 0, task P4-00d; SCOPE finding F6).

``test_adr0004_simulated_agent_transcript_rule_cites_test``: Decision 11 (the producer rule for
``agent_transcript`` and ``transcript``) and invariant I43 each cite both
``test_available_inputs_never_lists_agent_transcript_without_agent_record`` (P4-21) and this test;
every other test name they cite is defined in a ``packages/*/tests`` file or is on
``PENDING["0004"]`` (``evalcore_phase4_adr_support``), which holds only names SCOPE section A
names and shrinks to empty by the gate P4-G. In the style of
``test_adr0016_amendment_cites_test.py``. Nothing else of ADR 0004 is read here.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

BINDING = "test_available_inputs_never_lists_agent_transcript_without_agent_record"
SELF = "test_adr0004_simulated_agent_transcript_rule_cites_test"


def _problems(text: str, pending: frozenset[str] | None = None) -> list[str]:
    found: list[str] = sup.check_adr04(
        text,
        defined=sup.defined_tests(),
        pending=sup.PENDING["0004"] if pending is None else pending,
        scope=sup.scope_names(),
    )
    return found


def test_adr0004_simulated_agent_transcript_rule_cites_test() -> None:
    problems = _problems(sup.ADR04.read_text("utf-8"))
    assert problems == [], "\n".join(problems)
    d11, i43 = sup.adr04_items(sup.ADR04.read_text("utf-8"))
    for body in (d11, i43):
        assert {BINDING, SELF} <= sup.cited(body)


def test_decision_11_and_i43_are_single_lines_in_their_own_blocks() -> None:
    text = sup.ADR04.read_text("utf-8")
    d11, i43 = sup.adr04_items(text)
    assert d11.startswith(
        "11. **Which producer makes `agent_transcript` and `transcript` available"
    )
    assert i43.startswith("- I43. ")
    decision = sup.numbered(sup.heading_section(text, "## Decision"), r"(\d+)\. \*\*")
    assert [label for label, _ in decision][-1] == "11"
    invariants = sup.numbered(sup.heading_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants][-1] == "I43"
    labels = [label for label, _ in invariants]
    assert labels.count("I43") == 1


def test_decision_11_states_the_producer_rule_in_the_terms_of_the_scope_finding() -> None:
    d11, i43 = sup.adr04_items(sup.ADR04.read_text("utf-8"))
    for source in ("agent_export", "agent_telemetry", "our_stt", "script"):
        assert f"`{source}`" in d11, source
    assert "BL-193" in d11
    assert re.search(r"never\s+because\s+of\s+`our_stt`", i43)


def test_the_pending_set_of_adr0004_is_the_one_binding_test() -> None:
    assert sup.PENDING["0004"] <= {BINDING}
    assert SELF not in sup.PENDING["0004"]
    assert not sup.PENDING["0004"] & sup.defined_tests()


MUTANTS: dict[str, Callable[[str], str]] = {
    "I43 removed": lambda t: re.sub(r"^- I43\. .*$", "", t, flags=re.MULTILINE),
    "Decision 11 removed": lambda t: re.sub(
        r"^11\. \*\*Which producer.*$", "", t, flags=re.MULTILINE
    ),
    "I43 loses the binding test": lambda t: t.replace(
        f"`{BINDING}`, `{SELF}` (Phase 4, P4-21 and", f"`{SELF}` (Phase 4, P4-21 and"
    ),
    "I43 loses its own test": lambda t: t.replace(
        f", `{SELF}` (Phase 4, P4-21 and", " (Phase 4, P4-21 and"
    ),
    "Decision 11 cites no test": lambda t: re.sub(
        r"^(11\. \*\*Which producer.*)$",
        lambda m: re.sub(r"`(test_\w+)`", r"\1", m.group(1)),
        t,
        flags=re.MULTILINE,
    ),
    "a name no test defines": lambda t: t.replace(
        f"`{SELF}` (Phase 4, P4-00d).", f"`{SELF}` (Phase 4, P4-00d), `test_zz_no_such_test`.", 1
    ),
    "Decision 11 renumbered to 12": lambda t: t.replace(
        "11. **Which producer", "12. **Which producer", 1
    ),
    "I43 renumbered to I44": lambda t: t.replace("- I43. (revision 50", "- I44. (revision 50", 1),
    "I43 no longer says our_stt": lambda t: re.sub(
        r"^(- I43\..*)$", lambda m: m.group(1).replace("our_stt", "ourstt"), t, flags=re.MULTILINE
    ),
}


@pytest.mark.parametrize("name", list(MUTANTS))
def test_adr0004_check_has_teeth(name: str) -> None:
    text = sup.ADR04.read_text("utf-8")
    mutant = MUTANTS[name](text)
    assert mutant != text, f"the mutation {name!r} changed nothing"
    assert _problems(mutant), f"the check accepted the mutant {name!r}"


def test_adr0004_check_refuses_pending_sets_that_do_not_fit() -> None:
    text = sup.ADR04.read_text("utf-8")
    assert _problems(text, frozenset()), "the binding test is not defined yet: it must be pending"
    assert _problems(text, sup.PENDING["0004"] | {"test_zz_invented"})
    assert sup.check_adr04(
        text,
        defined=sup.defined_tests() | {BINDING},
        pending=sup.PENDING["0004"],
        scope=sup.scope_names(),
    ), "a name that landed must leave the pending set"
