# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D, P2-18: retention columns only (ADR 0020 Decision 8, ADR 0019 Decision 6a).

Contract: docs/phase2/api-surface-batch-d.md section 7. ``artifact``, ``transcript`` and
``dataset_item`` carry the six privacy columns of their ``PrivacyMarker``; ``run`` carries them with
the prefix ``caller_`` (the marker of ``caller_external_id``). NULL means absent: it is never read
as false and never as "keep forever". Each retention date has a partial index on its non-NULL
values, so that a later sweeper can find expired rows. Nothing in the package enforces retention.
"""

from __future__ import annotations

import ast
import copy
import importlib
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_migration_support import MigStore  # type: ignore[import-not-found,unused-ignore]
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    Db,
    golden_doc,
    golden_valid_files,
    load_entity,
    violation_kind,
)

db = _support.db
mig = _mig_support.mig
migrate = _mig_support.migrate

SRC = Path(__file__).resolve().parents[1] / "src" / "tracer_evaldb"
PRIVACY = (
    "human_subject",
    "human_subject_reason",
    "consent_status",
    "retain_until",
    "delete_on_expiry",
    "policy_ref",
)
# table -> (column prefix, entity class name)
HOLDERS = {
    "artifact": ("", "Artifact"),
    "transcript": ("", "Transcript"),
    "dataset_item": ("", "DatasetItem"),
    "run": ("caller_", "Run"),
}
RETAIN_INDEXES = {
    "artifact": ("ix_artifact_retain_until", "retain_until"),
    "transcript": ("ix_transcript_retain_until", "retain_until"),
    "dataset_item": ("ix_dataset_item_retain_until", "retain_until"),
    "run": ("ix_run_caller_retain_until", "caller_retain_until"),
}


def _marker(entity: str, model: Any) -> Any:
    if entity == "Run":
        external = model.caller_external_id
        return None if external is None else external.privacy
    return model.privacy


def _expected(marker: Any, prefix: str) -> dict[str, Any]:
    retention = None if marker is None else marker.retention
    consent = None if marker is None else marker.consent
    values = {
        "human_subject": None if marker is None else marker.human_subject,
        "human_subject_reason": None if marker is None else marker.human_subject_reason,
        "consent_status": None if consent is None else consent.status,
        "retain_until": None if retention is None else retention.retain_until,
        "delete_on_expiry": None if retention is None else retention.delete_on_expiry,
        "policy_ref": None if retention is None else retention.policy_ref,
    }
    return {prefix + name: value for name, value in values.items()}


def _set_marker(entity: str, doc: dict[str, Any], marker: dict[str, Any] | None) -> dict[str, Any]:
    edited = copy.deepcopy(doc)
    if entity == "Run":
        external = edited["caller_external_id"]
        assert external is not None
        external["privacy"] = marker
    else:
        edited["privacy"] = marker
    return edited


def _cases(entity: str) -> list[Any]:
    """Every golden fixture of the entity, and edits of its marker that leave parts absent."""
    cases: list[Any] = []
    for path in golden_valid_files(entity):
        cases.append(pytest.param(load_entity(entity, path.read_text("utf-8")), id=path.stem))
    base = golden_doc(entity, "maximal")
    marker = _marker_of_doc(entity, base)
    if marker is not None and marker["human_subject"]:
        edits = {
            "retention-without-date-or-policy": {
                **marker,
                "retention": {"retain_until": None, "policy_ref": None, "delete_on_expiry": False},
            },
            "retention-delete-on-expiry-true": {
                **marker,
                "retention": {
                    "retain_until": "2031-02-03T04:05:06.000007Z",
                    "policy_ref": None,
                    "delete_on_expiry": True,
                },
            },
            "consent-without-reference": {
                **marker,
                "consent": {"status": "obtained", "reference": None, "recorded_at": None},
            },
        }
        for name, edited in edits.items():
            model = load_entity(entity, json.dumps(_set_marker(entity, base, edited)))
            cases.append(pytest.param(model, id=name))
    return cases


def _marker_of_doc(entity: str, doc: dict[str, Any]) -> dict[str, Any] | None:
    if entity == "Run":
        external = doc.get("caller_external_id")
        return None if external is None else external["privacy"]
    return doc.get("privacy")


def _wipe(db: Db) -> None:
    """Delete every row of every table (children first), so the next case starts empty."""
    with db.engine.begin() as conn:
        for table in reversed(db.metadata.sorted_tables):
            conn.execute(sa.delete(table))


def _write_and_read(db: Db, model: Any, table: str) -> dict[str, Any]:
    codecs = importlib.import_module("tracer_evaldb.codecs")
    repo = db.repo(codecs)
    with db.engine.begin() as conn:
        repo.write(conn, model)
    t = db.metadata.tables[table]
    with db.engine.connect() as conn:
        row = dict(conn.execute(sa.select(t).where(t.c.id == model.id.uuid)).mappings().one())
        conn.rollback()
    return row


# ================================================================ mirror, absent is NULL


@pytest.mark.parametrize("table", HOLDERS)
def test_retention_columns_mirror_privacy_marker(db: Db, table: str) -> None:
    """The six columns of every stored row equal the fields of the model's marker (computed here
    from the model, not by the codecs), for every golden fixture and for markers with parts left
    out."""
    prefix, entity = HOLDERS[table]
    cases = _cases(entity)
    assert len(cases) >= 4
    for case in cases:
        model = case.values[0]
        row = _write_and_read(db, model, table)
        wanted = _expected(_marker(entity, model), prefix)
        got = {name: row[name] for name in wanted}
        assert got == wanted, (table, case.id)
        for name, value in wanted.items():  # same types, not merely equal (False is not 0)
            assert type(got[name]) is type(value), (table, case.id, name)
        _wipe(db)  # a fresh store for the next case


@pytest.mark.parametrize("table", HOLDERS)
def test_retention_absent_is_null_never_false(db: Db, table: str) -> None:
    """A marker without retention, a retention without a date or a policy, and a row without a
    marker at all leave NULL in the columns that have nothing to say: never false, zero, an empty
    string or an epoch."""
    prefix, entity = HOLDERS[table]
    columns = [prefix + name for name in PRIVACY]
    t = db.metadata.tables[table]
    # (1) no marker at all (only the entities whose marker is optional)
    if entity in ("DatasetItem", "Run"):
        name = "minimal"
        row = _write_and_read(db, load_entity(entity, json.dumps(golden_doc(entity, name))), table)
        assert [row[c] for c in columns] == [None] * 6, (table, name)
        with db.engine.begin() as conn:
            nulls = conn.execute(
                sa.select(*[t.c[c].is_(None) for c in columns]).where(t.c.id == row["id"])
            ).one()
        assert all(nulls), tuple(nulls)
        _wipe(db)
    # (2) a human-subject marker whose retention names no date and no policy
    base = golden_doc(entity, "maximal")
    marker = _marker_of_doc(entity, base)
    assert marker is not None
    assert marker["human_subject"] is True
    bare = {
        **marker,
        "retention": {"retain_until": None, "policy_ref": None, "delete_on_expiry": False},
    }
    model = load_entity(entity, json.dumps(_set_marker(entity, base, bare)))
    row = _write_and_read(db, model, table)
    assert row[prefix + "retain_until"] is None
    assert row[prefix + "policy_ref"] is None
    assert row[prefix + "delete_on_expiry"] is False  # stated, and false: that is not absence
    assert row[prefix + "human_subject"] is True
    with db.engine.begin() as conn:
        nulls = conn.execute(
            sa.select(
                t.c[prefix + "retain_until"].is_(None), t.c[prefix + "policy_ref"].is_(None)
            ).where(t.c.id == row["id"])
        ).one()
    assert tuple(nulls) == (True, True)
    _wipe(db)
    # (3) a marker that is not human-subject: everything but the flag is NULL
    synthetic = {
        "human_subject": False,
        "human_subject_reason": None,
        "consent": None,
        "retention": None,
    }
    if entity != "Run":  # a human caller's run cannot carry a false marker
        model = load_entity(entity, json.dumps(_set_marker(entity, base, synthetic)))
        row = _write_and_read(db, model, table)
        assert row[prefix + "human_subject"] is False
        assert [row[prefix + n] for n in PRIVACY[1:]] == [None] * 5


@pytest.mark.parametrize("table", HOLDERS)
def test_the_database_refuses_false_standing_for_absent(db: Db, table: str) -> None:
    """The CHECK of ADR 0019 Decision 6a: consent and retention present iff human_subject is true;
    a row without a marker has all six NULL. A stub value cannot stand for an absent one."""
    prefix = HOLDERS[table][0]
    rows = _support.Rows(db.metadata)
    cases: dict[str, dict[str, Any]] = {
        "no marker but a stated retention": {
            prefix + "human_subject": None,
            prefix + "delete_on_expiry": False,
        },
        "no marker but a consent": {
            prefix + "human_subject": None,
            prefix + "consent_status": "unknown",
        },
        "false marker with a retention date": {
            prefix + "human_subject": False,
            prefix + "retain_until": _support.FIXED_INSTANT,
        },
        "false marker with delete_on_expiry": {
            prefix + "human_subject": False,
            prefix + "delete_on_expiry": False,
        },
        "human subject without delete_on_expiry": {
            prefix + "human_subject": True,
            prefix + "consent_status": "obtained",
            prefix + "delete_on_expiry": None,
        },
        "human subject without consent": {
            prefix + "human_subject": True,
            prefix + "delete_on_expiry": False,
            prefix + "consent_status": None,
        },
    }
    for label, overrides in cases.items():
        with pytest.raises(sa.exc.DBAPIError) as caught, db.engine.begin() as conn:
            rows.insert(conn, table, **overrides)
        # the marker of an artifact or a transcript is required: no marker is a NOT NULL there
        required = table in ("artifact", "transcript")
        wanted = (
            "not_null"
            if required and overrides.get(prefix + "human_subject", 1) is None
            else "check"
        )
        assert violation_kind(caught.value) == wanted, label
        rows = _support.Rows(db.metadata)
    valid = {
        prefix + "human_subject": True,
        prefix + "consent_status": "obtained",
        prefix + "delete_on_expiry": False,
    }
    with db.engine.begin() as conn:  # the same rule accepts a complete human-subject marker
        _support.Rows(db.metadata).insert(conn, table, **valid)


def test_privacy_columns_exist_on_exactly_the_four_tables(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    for name, table in metadata.tables.items():
        privacy_like = {
            c.name
            for c in table.columns
            if re.search(
                r"human_subject|consent_status|retain_until|delete_on_expiry|policy_ref", c.name
            )
        }
        if name in HOLDERS:
            prefix = HOLDERS[name][0]
            assert privacy_like == {prefix + p for p in PRIVACY}, name
        else:
            assert privacy_like == set(), name
    # retain_until is an instant; the flags are booleans; absent is allowed on the optional ones
    for name, (prefix, _entity) in HOLDERS.items():
        table = metadata.tables[name]
        assert type(table.c[prefix + "retain_until"].type).__name__ == "InstantCol"
        assert type(table.c[prefix + "delete_on_expiry"].type).__name__ == "BoolCol"
        assert type(table.c[prefix + "human_subject"].type).__name__ == "BoolCol"
        assert table.c[prefix + "retain_until"].nullable is True
        assert table.c[prefix + "delete_on_expiry"].nullable is True
        assert table.c[prefix + "policy_ref"].nullable is True
    assert metadata.tables["artifact"].c.human_subject.nullable is False
    assert metadata.tables["transcript"].c.human_subject.nullable is False
    assert metadata.tables["dataset_item"].c.human_subject.nullable is True
    assert metadata.tables["run"].c.caller_human_subject.nullable is True


# ====================================================================== the partial indexes


def _index_sql(engine: sa.Engine, name: str) -> str:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            found = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
            ).scalar_one_or_none()
        else:
            found = conn.exec_driver_sql(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = %s",
                (name,),
            ).scalar_one_or_none()
        conn.rollback()
    assert found is not None, f"no index {name}"
    return " ".join(str(found).split())


def _check_indexes(engine: sa.Engine) -> None:
    for table, (name, column) in RETAIN_INDEXES.items():
        sql = _index_sql(engine, name)
        assert re.search(rf"\b{table}\b", sql), sql
        assert "UNIQUE" not in sql.upper(), sql
        assert re.search(rf"\(\s*{column}\s*\)", sql), sql  # only that column
        assert re.search(rf"WHERE \(?{column} IS NOT NULL\)?$", sql), (
            sql
        )  # only the non-NULL values


def test_retain_until_index_exists(db: Db, evaldb: ModuleType) -> None:
    """Metadata, the create_all schema and (below) the Alembic schema all have the four partial
    indexes, and the planner can use them for a range query on the date."""
    for table, (name, column) in RETAIN_INDEXES.items():
        found = [i for i in evaldb.metadata.tables[table].indexes if i.name == name]
        assert len(found) == 1, name
        (index,) = found
        assert [c.name for c in index.columns] == [column]
        assert not index.unique
        for dialect in ("sqlite", "postgresql"):
            where = index.dialect_options[dialect]["where"]
            assert str(where) == f"{column} IS NOT NULL", (name, dialect)
    _check_indexes(db.engine)
    # a plan for "what has expired" reads the index (Postgres: with sequential scans discouraged,
    # on a table with no statistics; SQLite: always)
    for table, (name, column) in RETAIN_INDEXES.items():
        t = db.metadata.tables[table]
        query = sa.select(t.c.id).where(t.c[column] < _support.FIXED_INSTANT)
        compiled = str(query.compile(db.engine, compile_kwargs={"literal_binds": False}))
        assert column in compiled
        with db.engine.connect() as conn:
            if db.backend == "sqlite":
                plan = conn.exec_driver_sql(
                    f"EXPLAIN QUERY PLAN SELECT id FROM {table} WHERE {column} < '2026-10-23'"  # noqa: S608
                ).all()
                text = " ".join(str(r) for r in plan)
            else:
                conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
                plan = conn.exec_driver_sql(
                    f"EXPLAIN SELECT id FROM {table} WHERE {column} < '2026-10-23'"  # noqa: S608
                ).all()
                text = " ".join(str(r[0]) for r in plan)
            conn.rollback()
        assert name in text, (table, text)


def test_migrated_database_has_the_retain_until_indexes(mig: MigStore, migrate: ModuleType) -> None:
    migrate.upgrade(mig.engine)
    _check_indexes(mig.engine)


# ================================================================== no enforcement code


def test_no_retention_enforcement_code_exists() -> None:
    """The owner's rule: data only. Only the table definitions, the codecs that project the marker
    and the frozen revisions name the retention columns; nothing reads ``delete_on_expiry`` or
    ``retain_until`` to act, and nothing is named like a sweeper or scheduled job."""
    allowed = {"tables.py", "codecs.py"}
    offences: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        text = path.read_text("utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            for name in names:
                if re.search(r"(?i)sweep|purge|expire|expiry_job|retention_job|scheduler", name):
                    offences.append(f"{relative}: {name}")
        reads = re.findall(r"\b(?:delete_on_expiry|retain_until)\b", text)
        if reads and path.name not in allowed and "versions" not in relative.parts:
            offences.append(f"{relative}: names a retention column")
        for module in ("sched", "apscheduler", "celery", "schedule"):
            if re.search(rf"^\s*(?:import|from)\s+{module}\b", text, re.MULTILINE):
                offences.append(f"{relative}: imports {module}")
    assert offences == [], offences
