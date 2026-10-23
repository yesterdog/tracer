# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-18 (batch 18): the registry of adversarial cases and the proof that each one is tested.

``fixtures/adversarial/registry.json`` has one entry for every adversarial case: A-01 to A-44 of
SCOPE section 5 (A-33 to A-44 are the sealing cases the architect numbered from the S-03 to S-14 of
``test_sealing_adversarial.py``) and the extra cases X-01 and up. This file proves that

- every A-number has an entry, and the entries are well formed (an expectation of ``rejected``,
  ``preserved`` or ``flagged``, a place, a threat in one sentence for the security-relevant ones);
- every covering test of every entry exists (pytest collects it), is not marked or written to skip,
  runs in a child pytest process and passes; a case with a fixture directory also passes once for
  each of its documents;
- every fixture directory belongs to an entry and its expectation is one of its fixtures'.

The checks are functions in ``_support_b12`` and the mutation tests below feed them damaged
registries (an entry dropped, a test that does not exist, a skipped test, a failing test, a missing
fixture) to show that each damage is found. The child run takes the time of the covering tests
once for the whole module.

The test named by A-26 (``test_policy_documents_low_entropy_digest_limitation``) was red until
SCHEMA_POLICY.md existed (P1-20a), and so was the case A-26: the registry reports a gate-bound test
as the open item it is, case by case, and turns green when the document is written.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from . import _support_b12 as b12
from ._support import ROOT

SCOPE = ROOT / "docs" / "phase1" / "SCOPE.md"


def _registry() -> dict[str, Any]:
    return b12.read_registry()


def _entries() -> list[Any]:
    return [pytest.param(e, id=e["id"]) for e in _registry()["cases"]]


def _existing_files() -> list[str]:
    """The files the registry names that exist (a missing one is reported by its own case)."""
    files = {b12.node_file(n) for e in _registry()["cases"] for n in e["covering_tests"]}
    return sorted(f for f in files if (ROOT / f).is_file())


@pytest.fixture(scope="module")
def collected() -> set[str]:
    """The node ids pytest collects from every file the registry names."""
    return b12.collect_node_ids(_existing_files())


@pytest.fixture(scope="module")
def skips() -> dict[str, dict[str, str]]:
    return {f: b12.skip_reasons(ROOT / f) for f in _existing_files()}


@pytest.fixture(scope="module")
def results(collected: set[str]) -> b12.RunResult:
    """One child pytest run of every covering test that exists (a function node id runs all its
    parametrizations; the generic fixture test runs once per document)."""
    nodes = sorted(
        {
            n
            for e in _registry()["cases"]
            for n in e["covering_tests"]
            if any(b12.node_matches(n, c) for c in collected)
        }
    )
    assert os.environ.get(b12.CHILD_ENV) is None, "the registry test must not run itself"
    return b12.run_nodes(nodes)


# ============================================================================== the registry


def test_registry_file_has_the_documented_shape_and_canonical_bytes() -> None:
    """Every entry has exactly the members {id, title, source, expectation, where, covering_tests,
    fixture, security_relevant, threat}; the ids are unique and ordered; the expectation is one of
    the three; a security-relevant case has a one-sentence threat and no other has one; the file is
    ASCII and exactly ``json.dumps(..., indent=2)`` of its content, so a diff shows only changes."""
    assert b12.shape_problems(_registry()) == []
    raw = b12.REGISTRY_PATH.read_bytes()
    assert raw.decode("ascii") == json.dumps(json.loads(raw), indent=2, ensure_ascii=True) + "\n"


def test_every_numbered_case_has_an_entry() -> None:
    """A-01 to A-44 each have exactly one entry and are defined in SCOPE; nothing else is numbered
    A; the extra cases are X-01 and up without a gap. The architect gives the X cases A-numbers
    when SCOPE is next revised."""
    scope = SCOPE.read_text(encoding="utf-8")
    assert b12.numbering_problems(_registry(), scope) == []
    ids = [e["id"] for e in _registry()["cases"]]
    assert ids[:44] == list(b12.A_NUMBERS)


def test_fixture_directories_and_entries_agree() -> None:
    """Each directory of the adversarial tree is the fixture of the X case of its name; each
    entry's fixture exists; each X case's expectation is that of at least one of its documents."""
    assert b12.fixture_problems(_registry()) == []
    assert {d.name for d in b12.case_directories()} == {
        e["id"] for e in _registry()["cases"] if e["id"].startswith("X-") and e["fixture"]
    }


@pytest.mark.parametrize("entry", _entries())
def test_covering_tests_exist_are_collected_and_are_not_skipped(
    entry: dict[str, Any], collected: set[str], skips: dict[str, dict[str, str]]
) -> None:
    """For each case: every covering test is a function of an existing file, pytest collects it
    (with the project's default options, so a test deselected by default is not found), and it is
    not marked skip, skipif, xfail, private_fixtures or local_only and does not call
    ``pytest.skip``, ``importorskip`` or ``xfail``."""
    for node in entry["covering_tests"]:
        assert (ROOT / b12.node_file(node)).is_file(), node
        assert any(b12.node_matches(node, c) for c in collected), f"{node} is not collected"
        assert not skips.get(b12.node_file(node), {}).get(b12.node_function(node)), node


@pytest.mark.parametrize("entry", _entries())
def test_covering_tests_run_and_pass(entry: dict[str, Any], results: b12.RunResult) -> None:
    """For each case: every collected test the covering list names (all parametrizations of a
    function) ran in the child pytest process and passed; none was skipped; a case with a fixture
    directory passed once for each of its documents."""
    collected = set(results.passed) | set(results.failed) | set(results.skipped)
    problems = b12.node_problems(entry, collected=collected, skips={}, results=results)
    assert problems == []


def test_the_registry_covers_each_expectation_kind() -> None:
    """The registry is not one-sided: it has rejected, preserved and flagged cases, and at least
    one security-relevant case for each kind."""
    kinds = {e["expectation"] for e in _registry()["cases"]}
    assert kinds == set(b12.EXPECTATIONS)
    for kind in b12.EXPECTATIONS:
        assert any(
            e["expectation"] == kind and e["security_relevant"] for e in _registry()["cases"]
        ), kind


# ======================================================================== the checks have teeth


def _mutant(case: str, **changes: Any) -> dict[str, Any]:
    registry = copy.deepcopy(_registry())
    entry = next(e for e in registry["cases"] if e["id"] == case)
    entry.update(changes)
    return registry


def _all_green(collected: set[str]) -> b12.RunResult:
    return b12.RunResult(passed=set(collected))


def test_mutant_a_dropped_entry_is_found() -> None:
    """Dropping A-17, A-33 or A-44 leaves a number without an entry."""
    scope = SCOPE.read_text(encoding="utf-8")
    for case in ("A-17", "A-33", "A-44"):
        registry = copy.deepcopy(_registry())
        registry["cases"] = [e for e in registry["cases"] if e["id"] != case]
        assert f"{case}: no registry entry" in b12.numbering_problems(registry, scope)
    registry = copy.deepcopy(_registry())
    registry["cases"].append({**registry["cases"][0], "id": "A-45"})
    assert any("outside A-01..A-44" in p for p in b12.numbering_problems(registry, scope))
    registry = copy.deepcopy(_registry())
    registry["cases"] = [e for e in registry["cases"] if e["id"] != "X-02"]
    assert any("gap" in p for p in b12.numbering_problems(registry, scope))


def test_mutant_a_missing_test_is_found(collected: set[str]) -> None:
    """A covering test that does not exist, and one whose function was renamed, are found; a
    parametrization prefix is not mistaken for a longer name."""
    entry = _mutant("A-08", covering_tests=[f"{b12.TESTS_REL}/test_loader.py::test_no_such_test"])
    entry = next(e for e in entry["cases"] if e["id"] == "A-08")
    problems = b12.node_problems(
        entry, collected=collected, skips={}, results=_all_green(collected)
    )
    assert any("is not collected" in p for p in problems)
    near = f"{b12.TESTS_REL}/test_loader.py::test_strict_loader_duplicate_key"
    assert not any(b12.node_matches(near, c) for c in collected)
    assert any(
        b12.node_matches(f"{b12.TESTS_REL}/test_loader.py::test_strict_loader_duplicate_keys", c)
        for c in collected
    )


def test_mutant_a_skipped_or_failing_test_is_found(collected: set[str]) -> None:
    """A test that was skipped when run, one that failed, and one that did not run at all each make
    the case fail, for one parametrization of a function as for the whole function."""
    entry = next(e for e in _registry()["cases"] if e["id"] == "A-13")
    assert entry["fixture"] is None
    node = f"{b12.TESTS_REL}/test_primitives.py::test_decimal_rejects"
    assert node in entry["covering_tests"]
    ran = sorted(c for c in collected if b12.node_matches(node, c))
    assert len(ran) > 1, "the A-13 test is parametrized"
    green = _all_green(collected)
    assert b12.node_problems(entry, collected=collected, skips={}, results=green) == []
    skipped = b12.RunResult(passed=set(collected) - {ran[1]}, skipped={ran[1]: "skipped"})
    failed = b12.RunResult(passed=set(collected) - {ran[0]}, failed={ran[0]: "boom"})
    not_run = b12.RunResult(passed=set(collected) - {ran[0]})
    for result, word in ((skipped, "was skipped"), (failed, "failed"), (not_run, "did not run")):
        problems = b12.node_problems(entry, collected=collected, skips={}, results=result)
        assert [p for p in problems if word in p], (word, problems)


def test_mutant_a_marked_skip_is_found(tmp_path: Path) -> None:
    """The static check finds skip, skipif, xfail, the two default-deselected marks, a module-level
    ``pytestmark`` and a call of ``pytest.skip`` or ``importorskip`` in a function body."""
    source = "\n".join(
        [
            "import pytest",
            "@pytest.mark.skip(reason='x')",
            "def test_a() -> None: ...",
            "@pytest.mark.skipif(True, reason='x')",
            "def test_b() -> None: ...",
            "@pytest.mark.xfail",
            "def test_c() -> None: ...",
            "@pytest.mark.private_fixtures",
            "def test_d() -> None: ...",
            "@pytest.mark.local_only",
            "def test_e() -> None: ...",
            "def test_f() -> None:",
            "    pytest.skip('x')",
            "def test_g() -> None:",
            "    pytest.importorskip('nothing')",
            "def test_h() -> None:",
            "    assert True",
            "@pytest.mark.parametrize('x', [1])",
            "def test_i(x: int) -> None: ...",
        ]
    )
    path = tmp_path / "test_sample.py"
    path.write_text(source, encoding="utf-8")
    reasons = b12.skip_reasons(path)
    assert set(reasons) == {"test_a", "test_b", "test_c", "test_d", "test_e", "test_f", "test_g"}
    marked = tmp_path / "test_module_mark.py"
    marked.write_text("import pytest\npytestmark = pytest.mark.skip\ndef test_x() -> None: ...\n")
    assert set(b12.skip_reasons(marked)) == {"test_x"}
    entry = next(e for e in _registry()["cases"] if e["id"] == "A-08")
    node = entry["covering_tests"][0]
    problems = b12.node_problems(
        entry,
        collected={node},
        skips={b12.node_file(node): {b12.node_function(node): "marked skip"}},
        results=b12.RunResult(passed={node}),
    )
    assert any("marked skip" in p for p in problems)


def test_mutant_a_lost_fixture_is_found(collected: set[str]) -> None:
    """A case whose fixture document did not pass, one without the generic fixture test in its
    list, and a directory without an entry are found."""
    entry = next(e for e in _registry()["cases"] if e["id"] == "X-06")
    green = _all_green(collected)
    node = b12.COVERING_CASES_TEST
    documents = b12.fixture_documents("X-06")
    for path in documents:
        green.passed.add(f"{node}[X-06/{path.stem}]")
    assert b12.node_problems(entry, collected=collected, skips={}, results=green) == []
    missing = copy.deepcopy(green)
    missing.passed.discard(f"{node}[X-06/{documents[0].stem}]")
    assert any(
        "did not pass" in p
        for p in b12.node_problems(entry, collected=collected, skips={}, results=missing)
    )
    bare = {**entry, "covering_tests": [t for t in entry["covering_tests"] if t != node]}
    assert any(
        "lists" in p for p in b12.node_problems(bare, collected=collected, skips={}, results=green)
    )
    orphan = copy.deepcopy(_registry())
    orphan["cases"] = [e for e in orphan["cases"] if e["id"] != "X-06"]
    assert any("has no registry entry" in p for p in b12.fixture_problems(orphan))
    wrong = _mutant("X-02", expectation="flagged")
    assert any("no fixture has the entry's expectation" in p for p in b12.fixture_problems(wrong))


def test_mutant_a_malformed_entry_is_found() -> None:
    """Shape damage is found: a missing member, an extra member, an unknown expectation, an empty
    covering list, a threat of two sentences, a threat on a case that is not security relevant, a
    registry test as a covering test, a duplicate id and a wrong order."""
    case = "A-04"
    assert b12.shape_problems(_registry()) == []
    cases = _registry()["cases"]

    def with_entry(change: dict[str, Any] | None, drop: str | None = None) -> list[str]:
        registry = copy.deepcopy(_registry())
        entry = next(e for e in registry["cases"] if e["id"] == case)
        if change:
            entry.update(change)
        if drop:
            del entry[drop]
        return b12.shape_problems(registry)

    assert with_entry(None, drop="where")
    assert with_entry({"extra": 1})
    assert with_entry({"expectation": "accepted"})
    assert with_entry({"covering_tests": []})
    assert with_entry({"threat": "One. Two."})
    assert with_entry({"threat": "no full stop"})
    assert with_entry({"security_relevant": False})
    assert with_entry({"covering_tests": [f"{b12.TESTS_REL}/test_adversarial_registry.py::test_x"]})
    assert with_entry({"fixture": "somewhere/else"})
    registry = copy.deepcopy(_registry())
    registry["cases"].append(copy.deepcopy(cases[0]))
    assert any("twice" in p for p in b12.shape_problems(registry))
    registry = copy.deepcopy(_registry())
    registry["cases"].reverse()
    assert any("ordered" in p for p in b12.shape_problems(registry))
    assert b12.shape_problems({"cases": "x"})
