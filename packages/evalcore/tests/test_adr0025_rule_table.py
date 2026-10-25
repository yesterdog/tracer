# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0025 machine-read checks: the rule table against the normaliser and the fixture
(``test_adr0025_rule_table_matches_normaliser``) and the invariants against the tests.

The format of the table is defined in ADR 0025, section "Rule table": the table whose header row
is exactly
``| Rule | Code points or category | Effect on key | Effect on display | Fixture cases |``.
"""

from __future__ import annotations

import ast
import re
import unicodedata
from pathlib import Path

from evalcore_batchc_support import ADR25, ROOT, fixture_cases, load_fixture, normalise_mod  # type: ignore[import-not-found,unused-ignore]

HEADER = "| Rule | Code points or category | Effect on key | Effect on display | Fixture cases |"
CODE_POINT = re.compile(r"^U\+([0-9A-F]{4,6})(?:-U\+([0-9A-F]{4,6}))?$")
CATEGORIES = {
    "L", "M", "N", "P", "S", "Z", "C",
    "Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Me", "Nd", "Nl", "No",
    "Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sm", "Sc", "Sk", "So",
    "Zs", "Zl", "Zp", "Cc", "Cf", "Cs", "Co", "Cn",
}  # fmt: skip

# Names cited by the ADR 0025 invariants that tests of a later batch will define. Empty: every
# one is defined by batch C (P3-13 to P3-15).
PENDING: frozenset[str] = frozenset()


def _table() -> list[list[str]]:
    lines = ADR25.read_text("utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == HEADER]
    assert len(starts) == 1, "exactly one table has the documented header row"
    rows = []
    for line in lines[starts[0] + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 5, line
        rows.append(cells)
    return rows


def test_adr0025_rule_table_matches_normaliser() -> None:
    rows = _table()
    ids = [row[0] for row in rows]
    # one row per rule: R plus two digits, unique, ascending
    assert all(re.fullmatch(r"R[0-9]{2}", rule) for rule in ids), ids
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    # the rule ids of the table equal the rule ids implemented by the normaliser
    nm = normalise_mod()
    assert set(ids) == set(nm.IMPLEMENTED_RULES)
    assert ids == [f"R{n:02d}" for n in range(1, 19)]
    cases = fixture_cases()
    case_rules = {case["id"]: set(case["rules"]) for case in cases}
    listed: dict[str, set[str]] = {}
    for rule, code_points, effect_key, effect_display, fixtures in rows:
        # the code point cell: U+XXXX or U+XXXX-U+YYYY, and cat: plus a category, comma separated
        assert code_points, rule
        for item in (part.strip() for part in code_points.split(",")):
            if item.startswith("cat:"):
                # R14 reads "cat:Nd outside U+0966-U+096F": a category and an excluded range
                parts = re.fullmatch(
                    r"cat:([A-Z][a-z]?)(?: outside (U\+[0-9A-F]{4,6}-U\+[0-9A-F]{4,6}))?", item
                )
                assert parts, (rule, item)
                assert parts.group(1) in CATEGORIES, (rule, item)
                continue
            match = CODE_POINT.match(item)
            assert match, (rule, item)
            low = int(match.group(1), 16)
            high = int(match.group(2), 16) if match.group(2) else low
            assert low <= high <= 0x10FFFF, (rule, item)
        # the key effect is a statement; the display effect begins with ``none``
        assert effect_key
        assert effect_display.startswith("none"), (rule, effect_display)
        # the fixture cell: case ids in backticks, or the single value ``-``
        if fixtures == "-":
            listed[rule] = set()
            assert not [c for c, rules in case_rules.items() if rule in rules], rule
            continue
        ids_in_cell = re.findall(r"`([^`]+)`", fixtures)
        assert ids_in_cell, (rule, fixtures)
        assert re.fullmatch(r"(?:`[^`]+`)(?:, `[^`]+`)*", fixtures), (rule, fixtures)
        assert len(set(ids_in_cell)) == len(ids_in_cell), f"{rule} lists a case twice"
        listed[rule] = set(ids_in_cell)
        for case_id in ids_in_cell:
            assert case_id in case_rules, f"{rule} lists a case that does not exist: {case_id}"
            assert rule in case_rules[case_id], f"{case_id} does not name {rule} in its rules"
    # the other way round: every rule a case names is in the table and lists the case
    for case_id, rules in case_rules.items():
        for rule in rules:
            assert rule in listed, f"{case_id} names {rule}, which is not in the table"
            assert case_id in listed[rule], f"{rule} does not list the case {case_id}"
    # every rule with a fixture cell other than ``-`` has at least one case
    for rule, case_ids in listed.items():
        if rule != "R17":
            assert case_ids, rule
    # the single rule without a fixture needs a text JSON cannot hold, and a test covers it
    assert [rule for rule, case_ids in listed.items() if not case_ids] == ["R17"]
    row17 = next(row for row in rows if row[0] == "R17")
    assert "test_normaliser_refuses_lone_surrogate" in row17[2]
    # every case is needs_native_review and the file names the normaliser it belongs to
    assert all(case["needs_native_review"] is True for case in cases)
    assert load_fixture()["normaliser"] == nm.NORMALISER_ID


def test_adr0025_rule_table_code_points_are_real_where_they_name_a_category() -> None:
    """A table row that says ``cat:X`` next to a code point must not contradict Unicode: the
    listed single code points of R05, R06, R08 and R11 have the stated class in this interpreter's
    data, so a typo in the ADR is caught here and not in review."""
    rows = {row[0]: row for row in _table()}
    assert unicodedata.category("\u0933") == "Lo"
    assert "U+0933" in rows["R11"][1]
    assert unicodedata.category("\u200b") == "Cf"
    assert "U+200B" in rows["R08"][1]
    assert unicodedata.category("\xad") == "Cf"
    assert "U+00AD" in rows["R06"][1]
    assert unicodedata.category("\u093c") == "Mn"
    assert "U+093C" in rows["R13"][1]
    assert unicodedata.category("\u0964") == "Po"
    assert "U+0964" in rows["R07"][1]


# ------------------------------------------------------------------ the invariants cite real tests


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted(Path(ROOT / "packages").glob("*/tests/test_*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        names |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        }
    return names


def test_adr0025_invariants_cite_tests_that_exist() -> None:
    text = ADR25.read_text("utf-8")
    start = text.index("## Invariants (testable)") + len("## Invariants (testable)")
    rest = text[start:]
    end = re.search(r"\n## ", rest)
    section = rest[: end.start()] if end else rest
    items = list(re.finditer(r"^- (I\d+)\. ", section, re.MULTILINE))
    assert [m.group(1) for m in items] == [f"I{n}" for n in range(1, 14)]
    defined = _defined_tests()
    for i, match in enumerate(items):
        stop = items[i + 1].start() if i + 1 < len(items) else len(section)
        body = section[match.start() : stop]
        cited = set(re.findall(r"`(test_\w+)`", body))
        assert cited, f"invariant {match.group(1)} of ADR 0025 cites no test"
        assert not (cited - defined - PENDING), (match.group(1), sorted(cited - defined))
