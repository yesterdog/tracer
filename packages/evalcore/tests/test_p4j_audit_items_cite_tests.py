# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""SCOPE P4-J26 (docs/phase4/SCOPE.md section A.10): every finding of the Phase 4 judge audit.

``docs/security/P4-J-audit.md`` lists the findings J-F1 to J-F8 and the one finding added after
the audit (N-1) in one table. This test reads that file, ``docs/BACKLOG.md`` and the
AST of ``packages/*/tests/test_*.py`` and checks that the audit cannot drift from the code: each
row has an id, a severity and a status (fixed in a commit, open, deferred or accepted residual);
a fixed finding cites a test that is defined exactly once, any other a BACKLOG row that exists
and whose status agrees; an open P0 or P1 is named in the GO / NO-GO section; the conditions, the
judge verification list and the unverified items are present; and the text holds no
secret, no private path and no language outside the supported nine. The checker has teeth: it is
run on a synthetic audit and on mutants of the synthetic and of the real one. The checks read
files only; nothing is imported from the product.
"""

from __future__ import annotations

from collections import Counter

from evalcore_phase4_audit_support import (  # type: ignore[import-not-found,unused-ignore]
    BACKLOG,
    ROOT,
    AuditSpec,
    backlog_statuses,
    defined_tests,
    generic_mutants,
    problems,
    rows_of,
    synthetic,
)

AUDIT = ROOT / "docs" / "security" / "P4-J-audit.md"
IDS = (*(f"J-F{n}" for n in range(1, 9)), "N-1")
SPEC = AuditSpec(
    path=AUDIT,
    ids=IDS,
    verdict_heading="## Verdict for the first real judge run (P4-J28): GO with conditions",
    verdict_words=(
        "J-F1",
        "--judge-first-request-gate",
        "data_collection",
        "BL-141",
        "BL-363",
        "BL-266",
        "O-J3",
    ),
    sections=(
        ("Judge verification list", ("allowlist", "HTTP clients", "Exchange files", "injection")),
        ("Not verified", ("OpenRouter", "pip-audit", "gitleaks")),
    ),
    must_cite_tests=(
        (
            "J-F1",
            (
                "test_words_of_adjacent_turns_stay_separate",
                "test_each_turn_is_cleaned_alone_and_the_separator_is_kept",
            ),
        ),
        (
            "J-F5",
            (
                "test_judge_injection_cannot_flip_verdict_without_consistent_fields",
                "test_judge_injection_forged_delimiter_stays_data",
                "test_judge_injection_cannot_store_free_text",
                "test_judge_injection_cannot_change_output_format",
            ),
        ),
    ),
)


def _real() -> tuple[str, Counter[str], dict[str, str]]:
    return AUDIT.read_text("utf-8"), defined_tests(), backlog_statuses(BACKLOG.read_text("utf-8"))


def test_p4j_audit_items_cite_tests() -> None:
    text, tests, backlog = _real()
    assert problems(SPEC, text, tests, backlog) == []
    assert [r[0] for r in rows_of(text)] == list(IDS)


# ------------------------------------------------------------------------------ teeth


def test_the_checker_accepts_a_good_synthetic_audit() -> None:
    text, tests, backlog = synthetic(SPEC)
    assert problems(SPEC, text, tests, backlog) == []


def test_the_synthetic_audit_is_pinned_by_mutants() -> None:
    text, tests, backlog = synthetic(SPEC)
    mutants = generic_mutants(SPEC, text)
    assert len(mutants) >= 20
    for label, edit, expect in mutants:
        mutated = edit(text)
        assert mutated != text, label
        found = problems(SPEC, mutated, tests, backlog)
        assert any(expect in p for p in found), (label, found)


def test_the_real_audit_is_pinned_by_mutants() -> None:
    text, tests, backlog = _real()
    for label, edit, expect in generic_mutants(SPEC, text):
        mutated = edit(text)
        assert mutated != text, label
        found = problems(SPEC, mutated, tests, backlog)
        assert any(expect in p for p in found), (label, found)


def test_the_checker_refuses_a_test_defined_twice_and_a_missing_required_citation() -> None:
    text, tests, backlog = synthetic(SPEC)
    twice = tests.copy()
    twice["test_fixed_one"] = 2
    assert any("ambiguous test" in p for p in problems(SPEC, text, twice, backlog))
    cited = SPEC.must_cite_tests[0][1][0]
    gone = text.replace(f"`{cited}`", "`test_fixed_two`")
    assert any("does not cite" in p for p in problems(SPEC, gone, tests, backlog))
