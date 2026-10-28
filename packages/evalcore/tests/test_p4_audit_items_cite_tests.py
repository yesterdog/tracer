# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""SCOPE P4-32 (docs/phase4/SCOPE.md section A.7): every finding of the Phase 4 security audit.

``docs/security/P4-32-audit.md`` lists the findings A-F1 to A-F15 and the three findings added
after the audit (N-1 to N-3) in one table. This test reads that file, ``docs/BACKLOG.md`` and the
AST of ``packages/*/tests/test_*.py`` and checks that the audit cannot drift from the code: each
row has an id, a severity and a status (fixed in a commit, open, deferred or accepted residual);
a fixed finding cites a test that is defined exactly once, any other a BACKLOG row that exists
and whose status agrees; an open P0 or P1 is named in the GO / NO-GO section; the conditions, the
key-path trace, the verified areas and the unverified items are present; and the text holds no
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

AUDIT = ROOT / "docs" / "security" / "P4-32-audit.md"
IDS = (*(f"A-F{n}" for n in range(1, 16)), "N-1", "N-2", "N-3")
SPEC = AuditSpec(
    path=AUDIT,
    ids=IDS,
    verdict_heading="## Verdict for the first real Sarvam run (R1, P4-34): GO with conditions",
    verdict_words=(
        "A-F1",
        "A-F2",
        "BL-369",
        "pre-synthesis",
        "2026-10-28",
        "caps",
        "dry run",
        "allowlist",
    ),
    sections=(
        ("Key-path trace", ("BL-351", "env_file_unreadable", "from None")),
        ("Other areas verified", ("HTTP clients", "Recovery file", "WebSocket client")),
        ("Not verified", ("pip-audit", "gitleaks", "BL-351")),
    ),
    must_cite_tests=(
        (
            "A-F1",
            (
                "test_one_call_per_invocation_and_min_interval",
                "test_kill_switch_file_hangs_up_within_one_frame",
            ),
        ),
        (
            "A-F2",
            ("test_a_failed_brain_request_ends_the_caller_with_brain_failed_and_typed_facts",),
        ),
        ("A-F3", ("test_twilio_decode_treats_any_exception_of_the_parser_as_ignored",)),
        ("A-F5", ("test_the_sarvam_key_variable_is_refused_as_an_agent_token_variable",)),
    ),
)


def _real() -> tuple[str, Counter[str], dict[str, str]]:
    return AUDIT.read_text("utf-8"), defined_tests(), backlog_statuses(BACKLOG.read_text("utf-8"))


def test_p4_audit_items_cite_tests() -> None:
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
