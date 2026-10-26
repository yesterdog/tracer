# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""SCOPE P3-28: every finding of the written security audit cites a test or a BACKLOG row.

``docs/security/P3-28-audit.md`` lists the findings F1 to F16 in a table. This test reads that
file, ``docs/BACKLOG.md`` and the AST of ``packages/*/tests/test_*.py`` and checks that the audit
cannot drift from the code: every cited test exists (no PENDING allowance), every cited BACKLOG
row exists, a fixed or closed finding is pinned by a test, a deferred one by a BACKLOG row, and a
closed one names its commit. The parser has teeth: it is run on synthetic and on mutated audits.
The checks read files only; nothing is imported from the product.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _root()
AUDIT = ROOT / "docs" / "security" / "P3-28-audit.md"
BACKLOG = ROOT / "docs" / "BACKLOG.md"
ROW = re.compile(r"^\| (F\d+) \|")
TEST_NAME = re.compile(r"`(test_\w+)`")
BL_ID = re.compile(r"BL-\d{3}")
HEX_TOKEN = re.compile(r"\b[0-9a-f]{8}\b")
COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
VERDICT = "## Verdict for the first real send (P3-30): CONDITIONAL GO"
EXPECTED_IDS = [f"F{n}" for n in range(1, 17)]
STATUS_COLUMN = 3


def defined_tests() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("packages/*/tests/test_*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(node.name)
    return names


def backlog_ids(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"^\| (BL-\d{3}) \|", text, flags=re.MULTILINE)}


def rows_of(text: str) -> list[tuple[str, list[str], str]]:
    """``(id, cells, raw line)`` of every finding row; the cells are stripped."""
    found = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match is not None:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            found.append((match.group(1), cells, line))
    return found


def section(text: str, title: str) -> str | None:
    """The text of the ``## ...`` section whose heading contains ``title``."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## ") and title in line:
            end = next(
                (i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), None
            )
            return "\n".join(lines[index : end if end is not None else len(lines)])
    return None


def problems(text: str, tests: set[str], backlog: set[str]) -> list[str]:
    """Every way ``text`` breaks the rules of the module docstring; empty when it holds."""
    found: list[str] = []
    rows = rows_of(text)
    ids = [r[0] for r in rows]
    if ids != EXPECTED_IDS:
        found.append(f"ids are {ids}, not F1..F16 once each in order")
    for finding, cells, raw in rows:
        cited_tests = TEST_NAME.findall(raw)
        cited_bl = BL_ID.findall(raw)
        if not cited_tests and not cited_bl:
            found.append(f"{finding} cites no test and no BACKLOG row")
        for name in cited_tests:
            if name not in tests:
                found.append(f"{finding} cites an undefined test {name}")
        for bl in cited_bl:
            if bl not in backlog:
                found.append(f"{finding} cites a BACKLOG row that does not exist: {bl}")
        status = cells[STATUS_COLUMN].lower() if len(cells) > STATUS_COLUMN else ""
        if status.startswith(("fixed", "closed")) and not any(n in tests for n in cited_tests):
            found.append(f"{finding} is {status.split()[0]} but cites no existing test")
        if status.startswith("deferred") and not cited_bl:
            found.append(f"{finding} is deferred but cites no BACKLOG row")
        if status.startswith("closed") and COMMIT.search(status) is None:
            found.append(f"{finding} is closed but names no commit id")
        if not status.startswith(("fixed", "closed", "deferred")):
            found.append(f"{finding} has an unknown status {status!r}")
    for bl in sorted(set(BL_ID.findall(text)) - backlog):
        if not any(bl in p for p in found):
            found.append(f"the audit cites a BACKLOG row that does not exist: {bl}")
    by_id = {r[0]: r[2] for r in rows}
    if "test_guard_rejects_staged_report_store_and_call_log" not in TEST_NAME.findall(
        by_id.get("F1", "")
    ):
        found.append("F1 does not cite test_guard_rejects_staged_report_store_and_call_log")
    for number in (2, 3, 4, 5, 7, 8, 9, 10):
        names = TEST_NAME.findall(by_id.get(f"F{number}", ""))
        if not any(n.startswith(f"test_audit_f{number}_") for n in names):
            found.append(f"F{number} cites no test_audit_f{number}_* test")
    if VERDICT not in text:
        found.append("the verdict heading is missing")
    if "P0:** none" not in text:
        found.append("the P0 line is missing")
    unverified = section(text, "Not verified")
    if unverified is None:
        found.append("the 'Not verified' section is missing")
    elif "BL-236" not in unverified:
        found.append("the 'Not verified' section does not cite BL-236")
    outside = text
    for _, cells, _raw in rows:
        if len(cells) > STATUS_COLUMN and cells[STATUS_COLUMN].lower().startswith("closed"):
            outside = outside.replace(cells[STATUS_COLUMN], "")
    for token in HEX_TOKEN.findall(outside):
        found.append(f"an 8-character hex token outside the closed cells: {token}")
    return found


def _real() -> tuple[str, set[str], set[str]]:
    return AUDIT.read_text("utf-8"), defined_tests(), backlog_ids(BACKLOG.read_text("utf-8"))


def test_p3_audit_items_cite_tests() -> None:
    text, tests, backlog = _real()
    assert problems(text, tests, backlog) == []
    assert [r[0] for r in rows_of(text)] == EXPECTED_IDS


# ------------------------------------------------------------------------------ teeth


def _sample() -> tuple[str, set[str], set[str]]:
    rows = [
        "| F1 | a finding | P1 | closed: e8b5747 | a fix | "
        "`test_guard_rejects_staged_report_store_and_call_log`; BL-235 |"
    ]
    for n in range(2, 17):
        if n in (2, 3, 4, 5, 7, 8, 9, 10):
            rows.append(f"| F{n} | f | P2 | fixed in batch G | x | `test_audit_f{n}_one` |")
        else:
            rows.append(f"| F{n} | f | P3 | deferred | x | BL-228 |")
    text = "\n".join(
        [
            "# audit",
            VERDICT,
            "- **P0:** none.",
            "| id | finding | severity | status | fix | pinned by |",
            *rows,
            "## Not verified by this audit (BL-236)",
            "- a thing",
        ]
    )
    tests = {"test_guard_rejects_staged_report_store_and_call_log"} | {
        f"test_audit_f{n}_one" for n in (2, 3, 4, 5, 7, 8, 9, 10)
    }
    return text, tests, {"BL-228", "BL-235", "BL-236"}


def test_the_checker_accepts_a_good_synthetic_audit() -> None:
    text, tests, backlog = _sample()
    assert problems(text, tests, backlog) == []


@pytest.mark.parametrize(
    ("label", "edit", "expect"),
    [
        ("a missing id", lambda t: t.replace("| F7 |", "| F70 |"), "ids are"),
        ("a duplicate id", lambda t: t.replace("| F8 |", "| F7 |"), "ids are"),
        (
            "an unknown test",
            lambda t: t.replace("`test_audit_f4_one`", "`test_audit_f4_two`"),
            "undefined test",
        ),
        ("an unknown BL id", lambda t: t.replace("BL-235", "BL-999"), "does not exist"),
        (
            "no citation",
            lambda t: t.replace(
                "| F6 | f | P3 | deferred | x | BL-228 |", "| F6 | f | P3 | deferred | x | none |"
            ),
            "cites no test and no BACKLOG",
        ),
        (
            "deferred with a test only",
            lambda t: t.replace(
                "| F11 | f | P3 | deferred | x | BL-228 |",
                "| F11 | f | P3 | deferred | x | `test_audit_f2_one` |",
            ),
            "deferred but cites no BACKLOG",
        ),
        (
            "fixed with a row only",
            lambda t: t.replace(
                "| F2 | f | P2 | fixed in batch G | x | `test_audit_f2_one` |",
                "| F2 | f | P2 | fixed in batch G | x | BL-228 |",
            ),
            "no existing test",
        ),
        (
            "closed without a commit",
            lambda t: t.replace("closed: e8b5747", "closed"),
            "names no commit",
        ),
        (
            "an unknown status",
            lambda t: t.replace("| F12 | f | P3 | deferred |", "| F12 | f | P3 | open |"),
            "unknown status",
        ),
        (
            "no F1 guard test",
            lambda t: t.replace("`test_guard_rejects_staged_report_store_and_call_log`; ", ""),
            "F1 does not cite",
        ),
        (
            "a wrong audit test family",
            lambda t: t.replace("`test_audit_f9_one`", "`test_audit_f8_one`"),
            "F9 cites no test_audit_f9_",
        ),
        ("no verdict", lambda t: t.replace(VERDICT, "## Verdict"), "verdict heading"),
        ("no P0 line", lambda t: t.replace("P0:** none", "P0:** one"), "P0 line"),
        (
            "no BL-236 in the section",
            lambda t: t.replace(
                "## Not verified by this audit (BL-236)", "## Not verified by this audit"
            ),
            "BL-236",
        ),
        (
            "no section",
            lambda t: t.replace("## Not verified by this audit (BL-236)", "## Other"),
            "section is missing",
        ),
        ("a file stem", lambda t: t + "\n- the stem a1b2c3d4 of a file", "hex token"),
    ],
)
def test_the_checker_catches(label: str, edit: object, expect: str) -> None:
    text, tests, backlog = _sample()
    mutated = edit(text)  # type: ignore[operator]
    assert mutated != text, label
    found = problems(mutated, tests, backlog)
    assert any(expect in p for p in found), (label, found)


def test_the_checker_catches_a_backlog_row_cited_outside_the_table() -> None:
    text, tests, backlog = _sample()
    found = problems(text, tests, backlog - {"BL-236"})
    assert any("BL-236" in p and "does not exist" in p for p in found), found


def test_the_checker_ignores_a_hex_looking_commit_in_a_closed_cell() -> None:
    text, tests, backlog = _sample()
    ok = text.replace("closed: e8b5747", "closed: e8b57470deadbeef")
    assert problems(ok, tests, backlog) == []


# ----------------------------------------------------------------- mutants of the real audit


@pytest.mark.parametrize(
    ("label", "edit", "expect"),
    [
        (
            "drop a row",
            lambda t: "\n".join(x for x in t.splitlines() if not x.startswith("| F7 |")),
            "ids are",
        ),
        (
            "rename a cited test",
            lambda t: t.replace(
                "test_audit_f3_source_passes_trust_env_false_to_the_http_client",
                "test_audit_f3_renamed",
            ),
            "undefined test",
        ),
        ("a bad BACKLOG id", lambda t: t.replace("BL-228", "BL-998", 1), "does not exist"),
        (
            "a wrong status",
            lambda t: t.replace("| deferred |", "| fixed in batch G |", 1),
            "no existing test",
        ),
        (
            "a closed row without a commit",
            lambda t: t.replace("closed: e8b5747", "closed:", 1),
            "names no commit",
        ),
        ("the verdict changed", lambda t: t.replace("CONDITIONAL GO", "GO"), "verdict heading"),
    ],
)
def test_the_real_audit_is_pinned_by_mutants(label: str, edit: object, expect: str) -> None:
    text, tests, backlog = _real()
    mutated = edit(text)  # type: ignore[operator]
    assert mutated != text, label
    found = problems(mutated, tests, backlog)
    assert any(expect in p for p in found), (label, found)
