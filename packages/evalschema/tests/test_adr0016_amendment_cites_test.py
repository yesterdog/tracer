# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Gate condition C1 (decision log "PHASE 3 GATE"): the ADR 0016 Decision 1 amendment (P3-00d) and
the ADR 0004 invariants I41 and I42 cite only tests that exist, and none of them still says that
a test is pending after it landed. Machine-read, in the style of ``test_adr0023_rules_are_tested``
and ``test_adr0024_rules_are_tested``, with no ``PENDING`` allowance: every test they cite is
defined today. Contract: docs/phase3/api-surface-batch-j.md section 1.

Scope of the reading: the Decision 1 amendment paragraph and invariant I17 of
docs/adr/0016-roles-caller-origin-privacy.md, invariants I41 and I42 of
docs/adr/0004-requires-and-not-applicable.md, and (for the stale wording only) every line of those
two files. Other citations of the two ADRs are not read here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
ADR16 = ROOT / "docs" / "adr" / "0016-roles-caller-origin-privacy.md"
ADR04 = ROOT / "docs" / "adr" / "0004-requires-and-not-applicable.md"
PACKAGES = ROOT / "packages"

AMENDMENT_MARKER = "**What the agent's outputs are"
BINDING = "test_metrics_evaluate_agent_outputs_only"
SELF = "test_adr0016_amendment_cites_test"

#: Wording that says a cited test is not there yet. A line that holds one of these is allowed only
#: while it cites at least one test that no file defines (a genuinely pending test).
STALE = re.compile(
    r"pending\s+until"
    r"|until\s+[^.;)\n]*\blands?\b"
    r"|\(Phase\s+\d+,\s*P3-\d+[^)]*;\s*pending\b"
    r"|\bnot\s+(?:yet\s+)?(?:written|defined|implemented)\b"
    r"|\bnot\s+written\s+yet\b"
    r"|\byet\s+to\s+be\s+written\b"
    r"|\bto\s+be\s+written\b"
    r"|(?-i:\bPENDING\b)",
    re.IGNORECASE,
)


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted(PACKAGES.glob("*/tests/test_*.py")):
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


def _line_with(text: str, start: str) -> str:
    """The one line that starts (after blanks and a bullet) with ``start``; the item is one line."""
    found = [line for line in text.splitlines() if re.match(rf"\s*-?\s*{re.escape(start)}", line)]
    assert len(found) == 1, (start, len(found))
    return found[0]


def _amendment(text: str) -> str:
    found = [line for line in text.splitlines() if AMENDMENT_MARKER in line]
    assert len(found) == 1, f"the Decision 1 amendment paragraph is missing ({len(found)} lines)"
    return found[0]


def _invariant(text: str, label: str) -> str:
    return _line_with(text, f"{label}. ")


def citation_problems(amendment: str, i17: str, i41: str, i42: str, defined: set[str]) -> list[str]:
    """Every reason why the four items do not cite existing tests (no pending allowance)."""
    problems: list[str] = []
    items = {
        "ADR 0016 Decision 1 amendment": (amendment, {BINDING, SELF}),
        "ADR 0016 I17": (i17, {BINDING, SELF}),
        "ADR 0004 I41": (
            i41,
            {
                "test_stt_failure_is_missing_input_with_warning_and_nonzero_exit",
                "test_sarvam_partial_chunk_failure_yields_no_transcript",
            },
        ),
        "ADR 0004 I42": (
            i42,
            {
                "test_empty_chunk_makes_no_turn_and_is_counted",
                "test_detected_language_set_only_when_all_chunks_agree",
            },
        ),
    }
    for label, (body, required) in items.items():
        cited = _cited(body)
        if not cited:
            problems.append(f"{label} cites no test")
        for name in sorted(required - cited):
            problems.append(f"{label} does not cite {name}")
        for name in sorted(cited - defined):
            problems.append(f"{label} cites {name}, which no packages/*/tests file defines")
        for match in STALE.finditer(body):
            problems.append(f"{label} still says {match.group(0)!r}")
    return problems


def stale_line_problems(text: str, defined: set[str], name: str) -> list[str]:
    """Lines of a whole document that cite tests, all of which exist, and still say 'pending'."""
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        cited = _cited(line)
        if STALE.search(line) and cited and not (cited - defined):
            problems.append(f"{name} line {number}: pending wording, every cited test exists")
    return problems


# ------------------------------------------------------------------------ the real documents


def test_adr0016_amendment_cites_test() -> None:
    text = ADR16.read_text("utf-8")
    defined = _defined_tests()
    amendment = _amendment(text)
    i17 = _invariant(text, "I17")
    # the amendment lives in Decision 1: before Decision 2 and inside "## Decision"
    decision = text.index("\n## Decision")
    assert decision < text.index(AMENDMENT_MARKER) < text.index("\n2. **`Run.caller_origin")
    assert i17.startswith("- I17. "), i17[:20]
    assert {BINDING, SELF} <= defined, "the two tests the ADR names are defined"
    for body in (amendment, i17):
        assert _cited(body) >= {BINDING, SELF}, "both tests are cited"
        assert not (_cited(body) - defined), sorted(_cited(body) - defined)
        assert not STALE.findall(body), STALE.findall(body)
    # the other items are read with the same rules
    problems = citation_problems(
        amendment,
        i17,
        _invariant(ADR04.read_text("utf-8"), "I41"),
        _invariant(ADR04.read_text("utf-8"), "I42"),
        defined,
    )
    assert problems == [], problems
    assert stale_line_problems(text, defined, "ADR 0016") == []


def test_adr0004_invariants_i41_i42_cite_only_existing_tests_and_say_nothing_is_pending() -> None:
    text = ADR04.read_text("utf-8")
    defined = _defined_tests()
    for label, required in (
        (
            "I41",
            {
                "test_stt_failure_is_missing_input_with_warning_and_nonzero_exit",
                "test_sarvam_partial_chunk_failure_yields_no_transcript",
            },
        ),
        (
            "I42",
            {
                "test_empty_chunk_makes_no_turn_and_is_counted",
                "test_detected_language_set_only_when_all_chunks_agree",
            },
        ),
    ):
        body = _invariant(text, label)
        assert _cited(body) == required, (label, sorted(_cited(body)))
        assert required <= defined, (label, sorted(required - defined))
        assert not STALE.findall(body), (label, STALE.findall(body))
        assert "pending" not in body.lower(), label
        assert "lands" not in body.lower(), label
        assert " land)" not in body.lower(), label
    assert stale_line_problems(text, defined, "ADR 0004") == []
    # I41 and I42 keep their revision and decision tags (only the pending clause goes)
    assert "(revision 40, Decision 10a)" in _invariant(text, "I41")
    assert "(revision 40, Decision 10b)" in _invariant(text, "I42")


def test_adr_amendment_citation_check_names_itself() -> None:
    assert SELF in _defined_tests()
    assert BINDING in _defined_tests()


# ------------------------------------------------------------------------ the check has teeth

_GOOD_AMENDMENT = (
    f"   - {AMENDMENT_MARKER} (revision 40).** text. The binding test is `{BINDING}` and "
    f"`{SELF}` checks that this Decision cites it."
)
_GOOD_I17 = f"- I17. text. Tests: `{BINDING}` (Phase 3, P3-18), `{SELF}` (P3-00d)."
_GOOD_I41 = (
    "- I41. text. Tests: `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit`, "
    "`test_sarvam_partial_chunk_failure_yields_no_transcript` (Phase 3, P3-07 and P3-20)."
)
_GOOD_I42 = (
    "- I42. text. Tests: `test_empty_chunk_makes_no_turn_and_is_counted`, "
    "`test_detected_language_set_only_when_all_chunks_agree` (Phase 3, P3-09)."
)
_ALL = {
    BINDING,
    SELF,
    "test_stt_failure_is_missing_input_with_warning_and_nonzero_exit",
    "test_sarvam_partial_chunk_failure_yields_no_transcript",
    "test_empty_chunk_makes_no_turn_and_is_counted",
    "test_detected_language_set_only_when_all_chunks_agree",
}


def test_citation_check_accepts_a_clean_set() -> None:
    assert citation_problems(_GOOD_AMENDMENT, _GOOD_I17, _GOOD_I41, _GOOD_I42, set(_ALL)) == []


def test_citation_check_refuses_the_pending_wording_of_the_gate_finding() -> None:
    bad_i17 = _GOOD_I17.replace("(Phase 3, P3-18)", "(Phase 3, P3-18; pending until it lands)")
    bad_i41 = _GOOD_I41.replace(
        "(Phase 3, P3-07 and P3-20)", "(Phase 3, pending until P3-07 and P3-20 land)"
    )
    bad_i42 = _GOOD_I42.replace("(Phase 3, P3-09)", "(Phase 3, pending until P3-09 lands)")
    problems = citation_problems(_GOOD_AMENDMENT, bad_i17, _GOOD_I41, _GOOD_I42, set(_ALL))
    assert any("I17 still says" in p and "pending" in p for p in problems), problems
    assert any("I17 still says" in p and "until it lands" in p for p in problems), problems
    problems = citation_problems(_GOOD_AMENDMENT, _GOOD_I17, bad_i41, _GOOD_I42, set(_ALL))
    assert any("I41" in p and "still says" in p for p in problems), problems
    problems = citation_problems(_GOOD_AMENDMENT, _GOOD_I17, _GOOD_I41, bad_i42, set(_ALL))
    assert any("I42" in p and "still says" in p for p in problems), problems


STALE_PHRASES = [
    "(Phase 3, P3-18; pending until it lands)",
    "(pending until P3-07 and P3-20 land)",
    "pending until P3-09 lands",
    "(not yet written)",
    "the test is not written yet",
    "the test is yet to be written",
    "a test to be written in P3-18",
    "the test is not implemented",
    "PENDING",
]


@pytest.mark.parametrize("phrase", STALE_PHRASES)
def test_citation_check_refuses_every_pending_phrasing(phrase: str) -> None:
    assert STALE.search(phrase), phrase
    bad = _GOOD_I17 + " " + phrase
    problems = citation_problems(_GOOD_AMENDMENT, bad, _GOOD_I41, _GOOD_I42, set(_ALL))
    assert any("still says" in p for p in problems), problems
    bad_amendment = _GOOD_AMENDMENT + " " + phrase
    problems = citation_problems(bad_amendment, _GOOD_I17, _GOOD_I41, _GOOD_I42, set(_ALL))
    assert any("Decision 1 amendment still says" in p for p in problems), problems


@pytest.mark.parametrize(
    "clean",
    [
        "(Phase 3, P3-18)",
        "(P3-00d)",
        "The packages are listed in the pending work of nobody.",
        "a precaution pending evidence",
        "the aligner lands on the minimum",
    ],
)
def test_citation_check_does_not_refuse_ordinary_words(clean: str) -> None:
    problems = citation_problems(
        _GOOD_AMENDMENT, _GOOD_I17 + " " + clean, _GOOD_I41, _GOOD_I42, set(_ALL)
    )
    assert problems == [], problems


def test_citation_check_refuses_a_cited_name_that_no_test_defines() -> None:
    defined = set(_ALL) - {BINDING}
    problems = citation_problems(_GOOD_AMENDMENT, _GOOD_I17, _GOOD_I41, _GOOD_I42, defined)
    assert any("Decision 1 amendment cites" in p and BINDING in p for p in problems), problems
    assert any("I17 cites" in p and BINDING in p for p in problems), problems
    misspelt = _GOOD_I42.replace("is_counted", "is_counted_x")
    problems = citation_problems(_GOOD_AMENDMENT, _GOOD_I17, _GOOD_I41, misspelt, set(_ALL))
    assert any("I42 cites test_empty_chunk_makes_no_turn_and_is_counted_x" in p for p in problems)
    assert any(
        "I42 does not cite test_empty_chunk_makes_no_turn_and_is_counted" in p for p in problems
    )


def test_citation_check_refuses_an_item_that_cites_nothing_or_drops_a_test() -> None:
    no_test = "- I17. text with no test at all."
    problems = citation_problems(_GOOD_AMENDMENT, no_test, _GOOD_I41, _GOOD_I42, set(_ALL))
    assert "ADR 0016 I17 cites no test" in problems
    only_binding = f"- I17. text. Tests: `{BINDING}`."
    problems = citation_problems(_GOOD_AMENDMENT, only_binding, _GOOD_I41, _GOOD_I42, set(_ALL))
    assert f"ADR 0016 I17 does not cite {SELF}" in problems


def test_stale_line_check_allows_a_line_only_while_a_cited_test_is_missing() -> None:
    line = "- I99. x. Tests: `test_exists_now`, `test_not_yet` (pending until P3-99 lands)."
    assert stale_line_problems(line, {"test_exists_now"}, "doc") == []
    problems = stale_line_problems(line, {"test_exists_now", "test_not_yet"}, "doc")
    assert problems == ["doc line 1: pending wording, every cited test exists"]
    # a line that cites no test is not read (it may say "pending" about anything else)
    assert stale_line_problems("- I98. x (pending until it lands).", set(), "doc") == []


def test_line_readers_refuse_a_missing_or_duplicated_item() -> None:
    with pytest.raises(AssertionError):
        _invariant("- I1. a\n- I2. b\n", "I17")
    with pytest.raises(AssertionError):
        _invariant("- I17. a\n- I17. b\n", "I17")
    with pytest.raises(AssertionError):
        _amendment("1. **Role**\n")
    assert _amendment(f"x\n{_GOOD_AMENDMENT}\ny") == _GOOD_AMENDMENT
    assert _invariant(f"- I1. a\n{_GOOD_I17}\n", "I17") == _GOOD_I17
