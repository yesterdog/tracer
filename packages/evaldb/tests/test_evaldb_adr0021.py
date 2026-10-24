# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D: the policy-rule tests of ADR 0021 and ADR 0020, the SCOPE names of the batch,
and the error types of the batch.

``test_adr0021_policy_rules_are_tested`` reads
``docs/adr/0021-store-schema-version-and-migrations.md``: every numbered rule of "## Decision" and
every invariant cites at least one test that exists in ``packages/evaldb/tests``. There is no
pending list: every test the ADR cites exists.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from types import ModuleType

import pytest

from evaldb_deletion_support import COUNT_KEYS  # type: ignore[import-not-found,unused-ignore]

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
ADR20 = ROOT / "docs" / "adr" / "0020-deletion-and-retention.md"
ADR21 = ROOT / "docs" / "adr" / "0021-store-schema-version-and-migrations.md"
SCOPE = ROOT / "docs" / "phase2" / "SCOPE.md"

BATCH_D_TASKS = ("P2-14", "P2-15", "P2-17a", "P2-17b", "P2-18", "P2-19")
# Names the ADRs and the batch D contract add to the SCOPE rows.
ADDED = (
    "test_delete_snapshot_refused_while_a_run_references_it",
    "test_adr0021_policy_rules_are_tested",
)


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted(TESTS.glob("test_*.py")):
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


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = re.search(r"\n## ", rest)
    return rest[: end.start()] if end else rest


def _numbered(section: str, pattern: str) -> list[tuple[str, str]]:
    starts = list(re.finditer(rf"^{pattern}", section, re.MULTILINE))
    items = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(section)
        items.append((match.group(1), section[match.start() : end]))
    return items


def test_adr0021_policy_rules_are_tested() -> None:
    text = ADR21.read_text("utf-8")
    defined = _defined_tests()
    rules = _numbered(_section(text, "## Decision"), r"(\d+)\. \*\*")
    assert [int(label) for label, _ in rules] == list(range(1, 16)), "ADR 0021 has 15 rules"
    for label, body in rules:
        cited = _cited(body)
        assert cited, f"rule {label} of ADR 0021 cites no test"
        unknown = cited - defined
        assert not unknown, f"rule {label} cites tests that do not exist: {sorted(unknown)}"
    invariants = _numbered(_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 7)]
    for label, body in invariants:
        cited = _cited(body)
        assert cited, f"invariant {label} of ADR 0021 cites no test"
        assert not (cited - defined), f"invariant {label}: {sorted(cited - defined)}"
    assert "test_adr0021_policy_rules_are_tested" in defined
    # every rule is covered by a test that exercises it, not only by one that names it: the rules
    # that need a database cite one of the migration or version-policy files
    exercised = {
        "test_alembic_head_matches_metadata_exactly",
        "test_migration_stairway_up_down_up",
        "test_fake_data_migration_rewrites_schema_version_and_preserves_digests",
        "test_fake_data_migration_downgrade_restores_bytes_exactly",
        "test_digest_changing_upgrade_of_versioned_entity_refused",
        "test_postgres_migration_is_transactional",
        "test_sqlite_batch_migration_preserves_child_rows",
        "test_downgrade_base_on_populated_db_requires_explicit_data_loss_flag",
    }
    assert exercised <= defined


def test_adr0020_invariants_are_tested() -> None:
    """The same rule for ADR 0020: each of its 14 invariants cites tests that exist."""
    text = ADR20.read_text("utf-8")
    defined = _defined_tests()
    invariants = _numbered(_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 15)]
    for label, body in invariants:
        cited = _cited(body)
        assert cited, f"invariant {label} of ADR 0020 cites no test"
        assert not (cited - defined), f"invariant {label}: {sorted(cited - defined)}"


def test_the_count_key_sets_in_the_tests_are_the_adr_table() -> None:
    """The table of ADR 0020 Decision 6 (``root_kind`` to the keys of ``counts``) is what the tests
    compare the audit rows with."""
    section = _section(ADR20.read_text("utf-8"), "## Decision")
    start = section.index("| `root_kind` | Keys of `counts` |")
    lines = []
    for line in section[start:].splitlines():
        if not line.strip().startswith("|"):
            break  # the table ends where the next rule begins
        lines.append(line)
    rows: dict[str, list[str]] = {}
    for line in lines[2:]:
        left, right = (cell.strip() for cell in line.strip().strip("|").split("|", 1))
        kinds = re.findall(r"`(\w+)`", left)
        keys = re.findall(r"`(\w+)`", right)
        for kind in kinds:
            rows[kind] = keys
    run_keys = rows["run"]
    assert len(run_keys) == 16
    assert rows["project"] == ["project", "run"]  # "`project` and every key of `run`"
    expected: dict[str, tuple[str, ...]] = {}
    for kind, keys in rows.items():
        if kind == "project":
            expected[kind] = ("project", *run_keys)
        elif not keys:  # "the root's own table"
            expected[kind] = (kind,)
        else:
            expected[kind] = tuple(keys)
    assert expected == dict(COUNT_KEYS)


def _scope_rows() -> dict[str, str]:
    rows = {}
    for line in SCOPE.read_text("utf-8").splitlines():
        match = re.match(r"\| (P2-[\w]+) \|", line)
        if match:
            rows[match.group(1)] = line
    return rows


@pytest.mark.parametrize("task", BATCH_D_TASKS)
def test_every_test_named_by_the_scope_rows_of_batch_d_exists(task: str) -> None:
    row = _scope_rows()[task]
    named = _cited(row)
    assert named, task
    missing = named - _defined_tests()
    assert not missing, f"{task}: {sorted(missing)}"


def test_the_names_added_to_the_scope_rows_exist() -> None:
    defined = _defined_tests()
    for name in ADDED:
        assert name in defined, name
    assert "test_cascade_counts_parity_sqlite_postgres" in defined
    assert "test_concurrent_insert_during_run_delete_never_orphans" in defined
    assert "test_postgres_migration_is_transactional" in defined


# ======================================================================== the error types


def test_batch_d_error_types_and_their_parents(evaldb: ModuleType) -> None:
    errors = importlib.import_module("tracer_evaldb.errors")
    base = errors.StoreError
    direct = {
        "StoreNotMigrated": (base,),
        "StoreRevisionUnknown": (base,),
        "StoreSchemaVersionError": (base,),
        "StoreSchemaVersionTooNew": (errors.StoreSchemaVersionError,),
        "StoreSchemaVersionMismatch": (errors.StoreSchemaVersionError,),
        "IrreversibleMigration": (base,),
        "DigestChangingUpgrade": (base,),
        "MixedSchemaVersions": (base,),
        "DowngradeDataLoss": (base,),
        "MigrationIntegrityError": (base,),
        "SnapshotInUse": (base,),
    }
    for name, bases in direct.items():
        cls = getattr(errors, name)
        assert cls.__bases__ == bases, name
        assert getattr(evaldb, name) is cls, name
        assert name in errors.__all__, name
        assert name in evaldb.__all__, name
    # the two schema-version errors are siblings: neither is the other
    assert not issubclass(errors.StoreSchemaVersionTooNew, errors.StoreSchemaVersionMismatch)
    assert not issubclass(errors.StoreSchemaVersionMismatch, errors.StoreSchemaVersionTooNew)
    # and none of them is one of the evalschema errors of the same name (a store error is not a
    # document error)
    from tracer_evalschema import errors as ev_errors

    for name in ("StoreSchemaVersionTooNew", "StoreSchemaVersionMismatch"):
        assert not issubclass(getattr(errors, name), ev_errors.TracerSchemaError)
