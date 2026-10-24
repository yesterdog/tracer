# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch B, tables (P2-06 to P2-10): the 37 tables of ADR 0019.

Contract: docs/phase2/api-surface-batch-b.md. The ADR tables are read from the ADR files
(``evaldb_support.adr_table*``); the shipped JSON Schemas give the closed sets. Tests that touch
a database use the ``db`` fixture (batch A's ``store`` fixture, ``[sqlite]`` and ``[postgres]``,
with every table created from ``tracer_evaldb.metadata``).
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import uuid
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    CALLER_PRIVACY_COLUMNS,
    CLOSED_CONTEXT,
    COMMON_COLUMNS,
    FIXED_INSTANT,
    PRIVACY_COLUMNS,
    RUN_OWNED_TABLES,
    SIDE_TABLES,
    VERSIONED_COLUMNS,
    VERSIONED_TABLES,
    Db,
    Raw,
    adr_fk_graph,
    adr_table1,
    adr_table2,
    adr_table3,
    adr_table5,
    closed_sets,
    ddl,
    dialect_of,
    in_sets,
    is_nullable,
    logical_type,
    new_uuid,
    not_members,
    snake,
    violation_kind,
)
from tracer_evalschema import registry as ev_registry

# fixtures of evaldb_support (bound as module variables so that pytest finds them)
db = _support.db
sqlite_db = _support.sqlite_db
tables = _support.tables

ENTITY_ROWS = adr_table1()
ENTITY_TABLES = [row.table for row in ENTITY_ROWS]
ALL_TABLES = [*ENTITY_TABLES, *SIDE_TABLES]
RECORD_OF_FACT_TABLES = [t for t in ENTITY_TABLES if t not in VERSIONED_TABLES]
DBAPI_ERROR = sa.exc.DBAPIError


def _expect_violation(db: Db, violation: str, table: str, /, **values: Any) -> None:
    """Inserting the row is refused by the database with a violation of this kind."""
    with pytest.raises(DBAPI_ERROR) as caught, db.begin() as conn:
        db.rows.insert(conn, table, **values)
    assert violation_kind(caught.value) == violation, (table, values, str(caught.value.orig))


def _accept(db: Db, table: str, /, **values: Any) -> dict[str, Any]:
    with db.begin() as conn:
        inserted: dict[str, Any] = db.rows.insert(conn, table, **values)
    return inserted


def _count(db: Db, table: str, **where: Any) -> int:
    t = db.metadata.tables[table]
    clause = sa.and_(sa.true(), *[t.c[k] == v for k, v in where.items()])
    with db.engine.connect() as conn:
        return int(
            conn.execute(sa.select(sa.func.count()).select_from(t).where(clause)).scalar_one()
        )


# ---------------------------------------------------------------- the ADR tables and MetaData


def test_metadata_has_one_table_per_entity(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    entity_classes = sorted(c.__name__ for c in ev_registry.entity_classes().values())
    assert len(entity_classes) == 26
    assert sorted(row.entity for row in ENTITY_ROWS) == entity_classes
    for row in ENTITY_ROWS:
        assert row.table == snake(row.entity), row
    assert len(set(ENTITY_TABLES)) == 26
    expected = {*ENTITY_TABLES, *SIDE_TABLES}
    assert len(expected) == 37
    assert set(metadata.tables) == expected
    assert "alembic_version" not in metadata.tables
    assert [row.table for row in adr_table2()] == list(SIDE_TABLES)
    # importing the package alone fills the metadata (a fresh interpreter, no other import)
    code = "import tracer_evaldb; print(len(tracer_evaldb.metadata.tables))"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "37"


def test_tables_module_exposes_every_table(evaldb: ModuleType, tables: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    schema = importlib.import_module("tracer_evaldb.schema")
    assert schema.metadata is metadata
    entity_tables = dict(tables.ENTITY_TABLES)
    assert {name: t.name for name, t in entity_tables.items()} == {
        row.entity: row.table for row in ENTITY_ROWS
    }
    side_tables = dict(tables.SIDE_TABLES)
    assert set(side_tables) == set(SIDE_TABLES)
    for name in ALL_TABLES:
        table = getattr(tables, name)
        assert table is metadata.tables[name], name
    for table in (*entity_tables.values(), *side_tables.values()):
        assert table is metadata.tables[table.name]
    # the whole module namespace of public tables is exactly the 37 names (plus the two maps)
    public = {
        n for n, v in vars(tables).items() if isinstance(v, sa.Table) and not n.startswith("_")
    }
    assert public == set(ALL_TABLES)


def test_adr0019_entity_table_matches_metadata(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    side_listed: set[str] = set()
    for row in ENTITY_ROWS:
        table = metadata.tables[row.table]
        names = [c.name for c in table.columns]
        assert len(names) == len(set(names)), row.table
        versioned = row.key == ("id", "version")
        expected = [*COMMON_COLUMNS, *(VERSIONED_COLUMNS if versioned else ()), *row.projected]
        if row.table == "config_snapshot":
            assert row.projected[:2] == ("content_digest_scheme", "content_digest_value")
        # the Projected columns cell lists every column besides the common columns of Decision 3
        assert set(names) == set(expected), (row.table, set(names) ^ set(expected))
        assert tuple(c.name for c in table.primary_key.columns) == row.key, row.table
        assert (row.table in VERSIONED_TABLES) == versioned
        side_listed.update(row.side)
        # foreign keys: the single-column ones of the cell, exactly (composite: side tables)
        actual_fks = {
            (fk.parent.name, str(constraint.ondelete))
            for constraint in table.foreign_key_constraints
            for fk in constraint.elements
            if len(constraint.elements) == 1
        }
        assert actual_fks == set(row.fks), (row.table, actual_fks, row.fks)
        uniques = {
            tuple(c.name for c in u.columns)
            for u in table.constraints
            if isinstance(u, sa.UniqueConstraint)
        } | {tuple(c.name for c in i.columns) for i in table.indexes if i.unique}
        assert uniques == set(row.unique), (row.table, uniques)
    for row2 in adr_table2():
        table = metadata.tables[row2.table]
        assert {c.name for c in table.columns} == set(row2.columns), row2.table
        assert tuple(c.name for c in table.primary_key.columns) == row2.key, row2.table
    # every side table named by Table 1 exists; deletion_audit and tracer_meta belong to no entity
    assert side_listed == set(SIDE_TABLES) - {"deletion_audit", "tracer_meta"}


def test_adr0019_every_value_object_classified(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    rows = adr_table5()
    names = [name for name, _, _ in rows]
    value_objects = sorted(ev_registry.value_object_classes())
    assert len(value_objects) == 70
    assert sorted(names) == value_objects
    assert len(set(names)) == 70
    counts = {"doc only": 0, "projected as": 0, "side table": 0}
    all_columns = {c.name for t in metadata.tables.values() for c in t.columns}
    for name, classification, where in rows:
        kind = next((k for k in counts if classification.startswith(k)), None)
        assert kind is not None, (name, classification)
        counts[kind] += 1
        assert where.strip(), name
        if kind == "projected as":
            cited = re.findall(r"`([^`]+)`", classification)
            assert cited, name
            for token in cited:
                if "." in token:
                    table, column = token.split(".")
                    assert table in metadata.tables, (name, token)
                    assert column in metadata.tables[table].columns, (name, token)
                else:
                    assert token in all_columns or token in metadata.tables, (name, token)
        if kind == "side table":
            (table_name,) = re.findall(r"`([^`]+)`", classification)
            assert table_name in metadata.tables, (name, table_name)
        if kind == "doc only":
            assert "`" not in classification, (name, classification)
    from evaldb_support import ADR19  # local: only this test reads the totals line

    text = ADR19.read_text(encoding="utf-8")
    match = re.search(r"Totals: (\d+) `doc only`, (\d+) `projected as`, (\d+) `side table`", text)
    assert match is not None
    assert tuple(int(g) for g in match.groups()) == tuple(counts.values())
    assert sum(counts.values()) == 70


def test_adr0019_table3_matches_column_classes(evaldb: ModuleType) -> None:
    expected = {
        "UuidCol": ("uuid", "BLOB"),
        "InstantCol": ("timestamptz", "TEXT"),
        "DecimalCol": ("numeric", "TEXT"),
        "DocCol": ("text", "TEXT"),
        "Sha256Col": ("bytea", "BLOB"),
        "KeyCol": ('text COLLATE "C"', "TEXT"),
        "BigIntCol": ("bigint", "INTEGER"),
        "BoolCol": ("boolean", "INTEGER"),
    }
    parsed = adr_table3()
    assert [name for name, _, _ in parsed] == list(expected)
    for name, pg_cell, sqlite_cell in parsed:
        pg_type, sqlite_type = expected[name]
        assert re.findall(r"`([^`]*)`", pg_cell)[0] == pg_type, name
        assert re.findall(r"`([^`]*)`", sqlite_cell)[0] == sqlite_type, name
        assert isinstance(getattr(evaldb, name), type), name
        assert issubclass(getattr(evaldb, name), sa.types.TypeEngine), name


def _check_fk_graph(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    graph = adr_fk_graph()
    assert len(graph) == 23
    declared = {(r.child, r.columns, r.parent, r.parent_columns, r.on_delete) for r in graph}
    assert len(declared) == 23
    actual = set()
    for table in metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            actual.add(
                (
                    table.name,
                    tuple(constraint.column_keys),
                    constraint.referred_table.name,
                    tuple(e.column.name for e in constraint.elements),
                    str(constraint.ondelete),
                )
            )
    assert actual == declared


def test_adr0020_cascade_graph_matches_metadata(evaldb: ModuleType) -> None:
    _check_fk_graph(evaldb)


# ----------------------------------------------------------------- column types, nullability


def test_every_column_has_the_logical_type_and_nullability_of_the_adr(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    assert len(metadata.tables) == 37
    for name, table in metadata.tables.items():
        for column in table.columns:
            cls = getattr(evaldb, logical_type(name, column.name))
            assert type(column.type) is cls, (name, column.name, type(column.type), cls)
            assert column.nullable == is_nullable(name, column.name), (name, column.name)
            assert column.server_default is None, (name, column.name)
            assert column.default is None, (name, column.name)
    # no SQLAlchemy DateTime, JSON, Float or scaled Numeric type exists in any table
    banned = (sa.DateTime, sa.JSON, sa.Float, sa.Numeric, sa.Date, sa.Time, sa.Interval)
    for table in metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, banned), (table.name, column.name)


def test_column_types_per_backend(db: Db) -> None:
    inspector = sa.inspect(db.engine)
    pg_names = {
        "UuidCol": {"UUID"},
        "InstantCol": {"TIMESTAMP"},
        "DecimalCol": {"NUMERIC"},
        "DocCol": {"TEXT"},
        "Sha256Col": {"BYTEA"},
        "KeyCol": {"TEXT", "VARCHAR"},  # text COLLATE "C" (SQLAlchemy may reflect it as VARCHAR)
        "BigIntCol": {"BIGINT"},
        "BoolCol": {"BOOLEAN"},
    }
    sqlite_names = {
        "UuidCol": "BLOB",
        "InstantCol": "TEXT",
        "DecimalCol": "TEXT",
        "DocCol": "TEXT",
        "Sha256Col": "BLOB",
        "KeyCol": "TEXT",
        "BigIntCol": "INTEGER",
        "BoolCol": "INTEGER",
    }
    for name in ALL_TABLES:
        for column in inspector.get_columns(name):
            kind = logical_type(name, column["name"])
            reflected = column["type"]
            if db.backend == "postgres":
                assert type(reflected).__name__ in pg_names[kind], (name, column["name"])
                if kind == "InstantCol":
                    assert reflected.timezone is True, (name, column["name"])
                if kind == "DecimalCol":
                    assert reflected.precision is None
                    assert reflected.scale is None
            else:
                assert str(reflected).upper() == sqlite_names[kind], (name, column["name"])
    if db.backend == "postgres":
        with db.engine.connect() as conn:
            found = conn.execute(
                sa.text(
                    "SELECT table_name, column_name, character_maximum_length, collation_name "
                    "FROM information_schema.columns WHERE table_schema = current_schema() "
                    "AND data_type IN ('text', 'character varying') AND table_name = ANY(:names)"
                ),
                {"names": ALL_TABLES},
            ).all()
        assert found
        for table_name, column_name, length, collation in found:
            assert length is None, (table_name, column_name)  # no length limit
            expected = "C" if logical_type(table_name, column_name) == "KeyCol" else None
            assert collation == expected, (table_name, column_name, collation)


# -------------------------------------------------------------------------------- keys


def test_versioned_tables_pk_is_id_version(db: Db) -> None:
    assert len(VERSIONED_TABLES) == 9
    for name in VERSIONED_TABLES:
        table = db.metadata.tables[name]
        assert [c.name for c in table.primary_key.columns] == ["id", "version"], name
        key = new_uuid()
        _accept(db, name, id=key, version=1)
        _accept(db, name, id=key, version=2)  # another version of the same id
        _accept(db, name, id=key, version=2**63 - 1)
        _expect_violation(db, "unique", name, id=key, version=2)  # the same (id, version) twice
        assert _count(db, name, id=key) == 3


def test_record_of_fact_tables_pk_is_id(db: Db) -> None:
    assert len(RECORD_OF_FACT_TABLES) == 17
    for name in RECORD_OF_FACT_TABLES:
        table = db.metadata.tables[name]
        assert [c.name for c in table.primary_key.columns] == ["id"], name
        assert "version" not in table.columns, name
        key = new_uuid()
        _accept(db, name, id=key)
        _expect_violation(db, "unique", name, id=key)
        assert _count(db, name, id=key) == 1


def test_version_check_ge_1(db: Db) -> None:
    for name in VERSIONED_TABLES:
        _accept(db, name, version=1)
        for bad in (0, -1, -(2**63)):
            _expect_violation(db, "check", name, version=bad)
    for name in ("snapshot_member", "snapshot_profile_ref"):
        column = "def_version" if name == "snapshot_member" else "profile_version"
        _accept(db, name, **{column: 1})
        for bad in (0, -1):
            _expect_violation(db, "check", name, **{column: bad})
    for name in ("snapshot_member", "dataset_member"):
        _accept(db, name, ordinal=0)
        _expect_violation(db, "check", name, ordinal=-1)


def test_schema_version_check_pinned_to_0_0_1(db: Db) -> None:
    for name in ENTITY_TABLES:
        for dialect in ("sqlite", "postgresql"):
            text = ddl(db.metadata.tables[name], dialect)
            assert re.search(r"schema_version\"?\s*=\s*'0\.0\.1'", text), (name, dialect)
            assert not re.search(r"'0\.0\.(?!1')", text), (name, dialect)
        _accept(db, name, schema_version="0.0.1")
        for bad in ("0.0.2", "0.0.0", "0.1.0", "1.0.0", "0.0.1 ", " 0.0.1", "0.0.01", "", "0.0"):
            _expect_violation(db, "check", name, schema_version=bad)
    # the database-level pin in tracer_meta
    _accept(db, "tracer_meta", singleton=1, schema_version="0.0.1")
    _expect_violation(db, "check", "tracer_meta", singleton=1, schema_version="0.0.2")


# ------------------------------------------------------------------------ foreign keys


def test_no_fk_uses_set_null_or_set_default(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    seen = 0
    for table in metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            seen += 1
            assert str(constraint.ondelete) in ("CASCADE", "RESTRICT"), (
                table.name,
                constraint.ondelete,
            )
            assert constraint.onupdate is None, table.name
            assert constraint.deferrable is None, table.name
            assert constraint.initially is None, table.name
        for dialect in ("sqlite", "postgresql"):
            text = ddl(table, dialect).upper()
            assert "SET NULL" not in text, (table.name, dialect)
            assert "SET DEFAULT" not in text, (table.name, dialect)
    assert seen == 23


def test_fk_graph_matches_adr0020(evaldb: ModuleType) -> None:
    _check_fk_graph(evaldb)


def test_definition_references_have_no_foreign_key(db: Db) -> None:
    no_fk = {
        "evaluator": ["persona_id", "persona_version", "test_profile_id", "test_profile_version"],
        "metric_binding": ["evaluator_id", "metric_id", "metric_version"],
        "config_snapshot": [
            "evaluator_id",
            "evaluator_version",
            "rubric_id",
            "rubric_version",
            "pricing_catalog_id",
            "pricing_catalog_version",
        ],
        "run": ["agent_target_id", "agent_target_version"],
        "metric_result": ["metric_id", "metric_version"],
        "turn": ["transcript_id"],
        "artifact": ["turn_ref"],
        "dataset_item": ["artifact_id", "transcript_id", "source_run_id"],
        "snapshot_member": ["def_id", "def_version"],
        "snapshot_profile_ref": ["profile_id", "profile_version"],
        "dataset_member": ["item_id"],
        "run_bundle_member": ["record_id", "record_version"],
        "run_bundle_withheld": ["record_id"],
        "export_bundle_run": ["run_id"],
        "export_bundle_item": ["item_id"],
        "export_bundle_profile": ["profile_id"],
    }
    for table_name, columns in no_fk.items():
        table = db.metadata.tables[table_name]
        constrained = {c.name for fk in table.foreign_keys for c in fk.constraint.columns}
        assert not constrained & set(columns), (table_name, constrained & set(columns))
    # behaviour: rows naming nothing that is stored are accepted (the resolver reports them)
    _accept(db, "evaluator", test_profile_id=new_uuid(), test_profile_version=1)
    _accept(db, "metric_binding")
    _accept(db, "config_snapshot", pricing_catalog_id=new_uuid(), pricing_catalog_version=1)
    _accept(db, "run")
    _accept(db, "metric_result")
    _accept(db, "turn", transcript_id=new_uuid())
    _accept(db, "artifact", turn_ref=new_uuid())
    _accept(
        db,
        "dataset_item",
        artifact_id=new_uuid(),
        transcript_id=new_uuid(),
        source_run_id=new_uuid(),
    )
    _accept(db, "snapshot_member")
    _accept(db, "snapshot_profile_ref")
    # the dataset_member row needs its dataset (that edge is a foreign key); item_id names nothing
    _accept(db, "dataset_member", item_id=new_uuid())
    _accept(db, "run_bundle_member", record_id=new_uuid())
    _accept(db, "run_bundle_withheld", record_id=new_uuid())
    for name in ("export_bundle_run", "export_bundle_item", "export_bundle_profile"):
        _accept(db, name)  # the second column names nothing stored


def test_run_owned_tables_have_run_id_cascade(db: Db) -> None:
    assert len(RUN_OWNED_TABLES) == 11
    for name in RUN_OWNED_TABLES:
        table = db.metadata.tables[name]
        column = table.columns["run_id"]
        assert column.nullable is False, name
        assert type(column.type) is db.evaldb.UuidCol, name
        (fk,) = [f for f in table.foreign_keys if f.parent.name == "run_id"]
        assert fk.column.table.name == "run", name
        assert fk.column.name == "id", name
        assert str(fk.constraint.ondelete) == "CASCADE", name
    # behaviour: deleting the run removes the rows of every run-owned table, and nothing else
    run_id = _accept(db, "run")["id"]
    other_run = _accept(db, "run")["id"]
    for name in RUN_OWNED_TABLES:
        _accept(db, name, run_id=run_id)
        _accept(db, name, run_id=other_run)
    _accept(db, "external_ref_index", run_id=run_id)
    bundle = _accept(db, "run_bundle", run_id=run_id)["id"]
    _accept(db, "run_bundle_member", bundle_id=bundle)
    _accept(db, "run_bundle_withheld", bundle_id=bundle)
    run_table = db.metadata.tables["run"]
    with db.begin() as conn:
        conn.execute(sa.delete(run_table).where(run_table.c.id == run_id))
    for name in RUN_OWNED_TABLES:
        assert _count(db, name, run_id=run_id) == 0, name
        assert _count(db, name, run_id=other_run) == 1, name
    assert _count(db, "external_ref_index", run_id=run_id) == 0
    assert _count(db, "run_bundle", run_id=run_id) == 0
    assert _count(db, "run_bundle_member", bundle_id=bundle) == 0
    assert _count(db, "run_bundle_withheld", bundle_id=bundle) == 0


def test_foreign_key_actions_behave_as_adr0020_states(db: Db) -> None:
    """Every edge of the graph: delete the parent row and see CASCADE or RESTRICT act."""
    for db_row in adr_fk_graph():
        with db.begin() as conn:
            child_values = db.rows.insert(conn, db_row.child)
        parent = db.metadata.tables[db_row.parent]
        child = db.metadata.tables[db_row.child]
        match = {
            pc: child_values[cc]
            for cc, pc in zip(db_row.columns, db_row.parent_columns, strict=True)
        }
        clause = sa.and_(*[parent.c[k] == v for k, v in match.items()])
        key_clause = sa.and_(
            *[child.c[c.name] == child_values[c.name] for c in child.primary_key.columns]
        )
        if db_row.on_delete == "CASCADE":
            with db.begin() as conn:
                conn.execute(sa.delete(parent).where(clause))
            assert (
                _count(
                    db,
                    db_row.child,
                    **{c.name: child_values[c.name] for c in child.primary_key.columns},
                )
                == 0
            ), db_row
        else:
            assert db_row.on_delete == "RESTRICT"
            with pytest.raises(DBAPI_ERROR) as caught, db.begin() as conn:
                conn.execute(sa.delete(parent).where(clause))
            assert violation_kind(caught.value) == "foreign_key", db_row
            with db.engine.connect() as conn:
                assert (
                    conn.execute(
                        sa.select(sa.func.count()).select_from(child).where(key_clause)
                    ).scalar_one()
                    == 1
                )
        # a child whose parent does not exist is refused
        values = db.rows.values(db_row.child)
        for cc in db_row.columns:
            values[cc] = new_uuid() if logical_type(db_row.child, cc) == "UuidCol" else 1
        with pytest.raises(DBAPI_ERROR) as caught, db.begin() as conn:
            conn.execute(sa.insert(child).values(**values))
        assert violation_kind(caught.value) == "foreign_key", db_row


def test_turn_seq_unique_per_transcript(db: Db) -> None:
    table = db.metadata.tables["turn"]
    unique = {
        tuple(c.name for c in u.columns)
        for u in table.constraints
        if isinstance(u, sa.UniqueConstraint)
    }
    unique |= {tuple(c.name for c in i.columns) for i in table.indexes if i.unique}
    assert unique == {("transcript_id", "seq")}
    transcript, other = new_uuid(), new_uuid()
    run_id = _accept(db, "run")["id"]
    _accept(db, "turn", run_id=run_id, transcript_id=transcript, seq=0)
    _accept(db, "turn", run_id=run_id, transcript_id=transcript, seq=1)
    _accept(db, "turn", run_id=run_id, transcript_id=other, seq=0)  # another transcript
    _expect_violation(db, "unique", "turn", run_id=run_id, transcript_id=transcript, seq=1)
    # no foreign key on transcript_id (ADR 0019 Ambiguity 1): no transcript row was ever inserted
    assert _count(db, "transcript") == 0
    assert _count(db, "turn", transcript_id=transcript) == 2


# ------------------------------------------------------------------------- cost checks


def test_currency_check_is_usd_only(db: Db) -> None:
    assert closed_sets()[("cost_record", "currency")] == ("USD",)
    assert closed_sets()[("cost_summary", "currency")] == ("USD",)
    for table, amount_column, extra in (
        ("cost_record", "amount", {"status": "available"}),
        ("cost_summary", "total", {}),
    ):
        _accept(db, table, **{amount_column: Decimal("1.5"), "currency": "USD", **extra})
        for bad in ("usd", "Usd", "EUR", "INR", "US", "USDD", "", "USD ", " USD"):
            _expect_violation(
                db, "check", table, **{amount_column: Decimal("1.5"), "currency": bad, **extra}
            )
        for dialect in ("sqlite", "postgresql"):
            text = ddl(db.metadata.tables[table], dialect)
            assert in_sets(text, "currency") == [("USD",)], (table, dialect)
        # no amount and no currency is the not-available state
    _accept(db, "cost_record", status="not_available", na_reason="no_usage")
    _accept(db, "cost_summary", completeness="none")


def test_cost_amount_non_negative_check_both_backends(db: Db) -> None:
    good = [
        Decimal("0"),
        Decimal("1"),
        Decimal("1.5"),
        Decimal("0.000000000000000001"),
        Decimal("123456789012345678901234567890.123456789012345678"),
    ]
    bad = [
        Decimal("-1"),
        Decimal("-0.1"),
        Decimal("-0.000000000000000001"),
        Decimal("-123456789012345678901234567890"),
    ]
    for table, column, extra in (
        ("cost_record", "amount", {"status": "available", "basis": "reported"}),
        ("cost_summary", "total", {}),
    ):
        for amount in good:
            row = _accept(db, table, **{column: amount, "currency": "USD", **extra})
            t = db.metadata.tables[table]
            with db.engine.connect() as conn:
                stored = conn.execute(
                    sa.select(t.c[column]).where(t.c.id == row["id"])
                ).scalar_one()
            assert format(stored, "f") == format(amount, "f")  # digits and scale kept
        for amount in bad:
            _expect_violation(db, "check", table, **{column: amount, "currency": "USD", **extra})
    # the sign rule is the database's: it is written per backend (numeric comparison, NOT GLOB)
    for table, column in (("cost_record", "amount"), ("cost_summary", "total")):
        assert "NOT GLOB '-*'" in ddl(db.metadata.tables[table], "sqlite"), table
        assert re.search(rf'"?{column}"?\s*>=\s*0', ddl(db.metadata.tables[table], "postgresql"))


# ------------------------------------------------------------------------- closed sets


def test_closed_set_checks_equal_shipped_schema_enums(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    sets = closed_sets()
    # the columns the ADR names (paragraph under Table 1): 10 digest-scheme columns, 4 privacy
    # prefixes x 2, and the rest; side-table sets come from Table 2 and ADR 0020
    projected_names = {
        "transport", "kind", "value_type", "mode", "caller_origin", "observed_tier", "source",
        "role", "stage", "status", "na_reason", "domain", "basis", "completeness", "currency",
        "purpose", "consent_status", "human_subject_reason", "content_digest_scheme",
    }  # fmt: skip
    for (table, column), members in sets.items():
        assert table in metadata.tables, (
            table,
            column,
        )
        assert column in metadata.tables[table].columns, (
            table,
            column,
        )
        assert len(members) == len(set(members)) > 0
        for dialect in ("sqlite", "postgresql"):
            found = in_sets(ddl(metadata.tables[table], dialect), column)
            assert found, (table, column, dialect)
            full = [f for f in found if set(f) == set(members)]
            assert len(full) == 1, (table, column, dialect, found)
            assert len(full[0]) == len(members), (table, column, dialect, found)
            assert all(set(f) <= set(members) for f in found), (table, column, dialect, found)
            if table in ENTITY_TABLES:
                assert len(found) == 1, (table, column, dialect, found)
    entity_closed = {(t, c) for (t, c) in sets if t in ENTITY_TABLES}
    for table, column in entity_closed:
        assert column in projected_names or column.removeprefix("caller_") in projected_names, (
            table,
            column,
        )
    # no other column of the 26 entity tables has a string-set CHECK (a language column has none)
    for table_name in ENTITY_TABLES:
        for column in metadata.tables[table_name].columns:
            if (table_name, column.name) in sets:
                continue
            for dialect in ("sqlite", "postgresql"):
                found = in_sets(ddl(metadata.tables[table_name], dialect), column.name)
                assert not found, (table_name, column.name, dialect, found)
    # the digest-scheme set is the registered schemes, nothing else
    assert set(sets[("agent_target", "content_digest_scheme")]) == {
        "sha256-jcs/v1",
        "hmac-sha256-jcs/v1",
    }


def _closed_cases() -> list[Any]:
    return [
        pytest.param(table, column, id=f"{table}.{column}")
        for (table, column) in sorted(closed_sets())
    ]


@pytest.mark.parametrize(("table", "column"), _closed_cases())
def test_closed_set_check_accepts_members_and_refuses_others(
    db: Db, table: str, column: str
) -> None:
    members = closed_sets()[(table, column)]
    context = dict(CLOSED_CONTEXT.get((table, column), {}))

    def context_for(value: str) -> dict[str, Any]:
        extra = dict(context)
        if (table, column) == ("run_bundle_member", "kind") and value in (
            "pricing_catalog",
            "test_profile",
        ):
            extra["record_version"] = 1
        if (table, column) == ("external_ref_index", "entity_kind"):
            extra["key_sha256"] = uuid.uuid4().bytes + uuid.uuid4().bytes
        return extra

    with db.begin() as conn:
        for value in members:
            db.rows.insert(conn, table, **{column: value}, **context_for(value))
    for value in not_members(members):
        _expect_violation(db, "check", table, **{column: value}, **context_for(members[0]))


# --------------------------------------------------------------------------- privacy


PRIVACY_TABLES = [("artifact", ""), ("transcript", ""), ("dataset_item", ""), ("run", "caller_")]


@pytest.mark.parametrize(("table", "prefix"), PRIVACY_TABLES)
def test_privacy_projection_check(db: Db, table: str, prefix: str) -> None:
    def cols(**kw: Any) -> dict[str, Any]:
        return {f"{prefix}{k}": v for k, v in kw.items()}

    none_set = {f"{prefix}{c}": None for c in PRIVACY_COLUMNS}
    assert tuple(f"{prefix}{c}" for c in PRIVACY_COLUMNS) == (
        CALLER_PRIVACY_COLUMNS if prefix else tuple(PRIVACY_COLUMNS)
    )
    # accepted: a human-subject marker with consent and retention (the optional ones may be NULL)
    _accept(
        db, table, **cols(human_subject=True, consent_status="obtained", delete_on_expiry=False)
    )
    _accept(
        db, table, **cols(human_subject=True, consent_status="not_obtained", delete_on_expiry=True)
    )
    _accept(
        db,
        table,
        **cols(
            human_subject=True,
            human_subject_reason="manual",
            consent_status="unknown",
            retain_until=FIXED_INSTANT,
            delete_on_expiry=True,
            policy_ref="policy-1",
        ),
    )
    # accepted: a false marker with nothing else
    _accept(db, table, **{**none_set, f"{prefix}human_subject": False})
    # refused: a human-subject marker without consent, or without delete_on_expiry
    _expect_violation(
        db, "check", table, **cols(human_subject=True, consent_status=None, delete_on_expiry=False)
    )
    _expect_violation(
        db,
        "check",
        table,
        **cols(human_subject=True, consent_status="obtained", delete_on_expiry=None),
    )
    _expect_violation(db, "check", table, **cols(human_subject=True))
    # refused: a false marker with any of the five other columns set
    others = {
        "human_subject_reason": "manual",
        "consent_status": "obtained",
        "retain_until": FIXED_INSTANT,
        "delete_on_expiry": False,
        "policy_ref": "p",
    }
    for name, value in others.items():
        _expect_violation(
            db,
            "check",
            table,
            **{**none_set, f"{prefix}human_subject": False, f"{prefix}{name}": value},
        )
    _expect_violation(
        db,
        "check",
        table,
        **{**none_set, f"{prefix}human_subject": False, f"{prefix}delete_on_expiry": True},
    )
    if table in ("dataset_item", "run"):
        # the marker is optional: absent means all six are NULL
        _accept(db, table, **none_set)
        for name, value in others.items():
            _expect_violation(db, "check", table, **{**none_set, f"{prefix}{name}": value})
    else:
        # a payload always has a marker: an absent one is a NOT NULL violation, not a state
        _expect_violation(db, "not_null", table, **none_set)


# ---------------------------------------------------------------- the extra CHECKs (Decision 12)


def test_not_applicable_result_requires_null_passed(db: Db) -> None:
    for value in (True, False):
        _expect_violation(
            db,
            "check",
            "metric_result",
            status="not_applicable",
            na_reason="missing_input",
            passed=value,
        )
    _accept(db, "metric_result", status="not_applicable", na_reason="missing_input", passed=None)
    _accept(db, "metric_result", status="scored", passed=True)
    _accept(db, "metric_result", status="scored", passed=False)
    _accept(db, "metric_result", status="scored", passed=None)
    _accept(db, "metric_result", status="error", passed=None)


def test_not_available_cost_has_no_amount_basis_or_currency(db: Db) -> None:
    base = {"status": "not_available", "na_reason": "no_usage"}
    _accept(db, "cost_record", **base)
    for extra in (
        {"amount": Decimal("1"), "currency": "USD"},
        {"basis": "reported"},
        {"basis": "computed"},
        {"currency": "USD"},
        {"amount": Decimal("0"), "currency": "USD", "basis": "reported"},
    ):
        _expect_violation(db, "check", "cost_record", **base, **extra)
    _accept(
        db, "cost_record", status="available", basis="computed", amount=Decimal("0"), currency="USD"
    )


def test_currency_present_if_and_only_if_amount_present(db: Db) -> None:
    for table, column, extra in (
        ("cost_record", "amount", {"status": "available", "basis": "reported"}),
        ("cost_summary", "total", {}),
    ):
        _accept(db, table, **{column: Decimal("2"), "currency": "USD", **extra})
        _accept(db, table, **{column: None, "currency": None, **extra})
        _expect_violation(db, "check", table, **{column: Decimal("2"), "currency": None, **extra})
        _expect_violation(db, "check", table, **{column: None, "currency": "USD", **extra})


def test_paired_reference_columns_present_together(db: Db) -> None:
    for table, id_column, version_column in (
        ("evaluator", "test_profile_id", "test_profile_version"),
        ("config_snapshot", "pricing_catalog_id", "pricing_catalog_version"),
    ):
        _accept(db, table, **{id_column: new_uuid(), version_column: 1})
        _accept(db, table, **{id_column: None, version_column: None})
        _expect_violation(db, "check", table, **{id_column: new_uuid(), version_column: None})
        _expect_violation(db, "check", table, **{id_column: None, version_column: 1})


def test_run_bundle_member_record_version_exactly_for_versioned_kinds(db: Db) -> None:
    kinds = closed_sets()[("run_bundle_member", "kind")]
    assert len(kinds) == 15
    versioned = {"pricing_catalog", "test_profile"}
    bundle = _accept(db, "run_bundle")["id"]
    for ordinal, kind in enumerate(kinds):
        if kind in versioned:
            _accept(
                db,
                "run_bundle_member",
                bundle_id=bundle,
                kind=kind,
                ordinal=ordinal,
                record_version=3,
            )
            _expect_violation(
                db,
                "check",
                "run_bundle_member",
                bundle_id=bundle,
                kind=kind,
                ordinal=100 + ordinal,
                record_version=None,
            )
        else:
            _accept(
                db,
                "run_bundle_member",
                bundle_id=bundle,
                kind=kind,
                ordinal=ordinal,
                record_version=None,
            )
            _expect_violation(
                db,
                "check",
                "run_bundle_member",
                bundle_id=bundle,
                kind=kind,
                ordinal=100 + ordinal,
                record_version=1,
            )


def test_tracer_meta_is_one_row(db: Db) -> None:
    _accept(db, "tracer_meta", singleton=1, schema_version="0.0.1", evaldb_version="0.0.1")
    _expect_violation(db, "unique", "tracer_meta", singleton=1)
    _expect_violation(db, "check", "tracer_meta", singleton=2)
    _expect_violation(db, "check", "tracer_meta", singleton=0)
    assert _count(db, "tracer_meta") == 1


# ------------------------------------------------------------------------- side tables


def _index_signatures(table: sa.Table) -> set[tuple[str, ...]]:
    return {tuple(c.name for c in i.columns) for i in table.indexes}


def test_side_table_indexes_and_keys_match_adr_table2(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    for row in adr_table2():
        table = metadata.tables[row.table]
        wanted: set[tuple[str, ...]] = set()
        for m in re.finditer(r"\bindex `([^`]*)`", row.constraints):
            wanted.add(tuple(p.strip() for p in m.group(1).strip("()").split(",")))
        actual = _index_signatures(table)
        assert wanted <= actual, (row.table, wanted - actual)
    # the partial unique index of the re-import keys, on both dialects. AMBIGUITY A1 of
    # api-surface-batch-b.md: unique for `run` only (ADR 0019 Table 2 also says `span`, but the
    # golden RunBundle has two spans with one span_ref, so the ADR text must be amended)
    table = metadata.tables["external_ref_index"]
    (index,) = [i for i in table.indexes if i.unique]
    assert tuple(c.name for c in index.columns) == ("entity_kind", "key_sha256")
    for dialect in ("sqlite", "postgresql"):
        where = index.dialect_options[dialect]["where"]
        assert where is not None, dialect
        text = str(
            sa.schema.CreateIndex(index).compile(
                dialect=dialect_of(dialect), compile_kwargs={"literal_binds": True}
            )
        )
        assert "WHERE" in text, (dialect, text)
        assert "'run'" in text, (dialect, text)
        for other in ("'span'", "'transcript'", "'usage_record'"):
            assert other not in text, (dialect, text)


def test_external_ref_index_unique_only_for_run(db: Db) -> None:
    key = bytes(range(32))
    other = bytes(range(1, 33))
    run_id = _accept(db, "run")["id"]
    _accept(db, "external_ref_index", run_id=run_id, entity_kind="run", key_sha256=key)
    _expect_violation(
        db, "unique", "external_ref_index", run_id=run_id, entity_kind="run", key_sha256=key
    )
    _accept(db, "external_ref_index", run_id=run_id, entity_kind="run", key_sha256=other)
    # spans, transcripts and usage records repeat a key (one provider call may yield several
    # records, and the golden RunBundle holds two spans with one span_ref): a lookup, not a rule
    for kind in ("span", "transcript", "usage_record"):
        _accept(db, "external_ref_index", run_id=run_id, entity_kind=kind, key_sha256=key)
        _accept(db, "external_ref_index", run_id=run_id, entity_kind=kind, key_sha256=key)
    assert _count(db, "external_ref_index", key_sha256=key) == 7
    # the same key under run and span are different keys; one record repeats a key only once
    entity = new_uuid()
    _accept(
        db,
        "external_ref_index",
        run_id=run_id,
        entity_kind="span",
        entity_id=entity,
        key_sha256=other,
    )
    _expect_violation(
        db, "unique", "external_ref_index", run_id=run_id, entity_kind="span", entity_id=entity,
        key_sha256=other,
    )  # fmt: skip


def test_side_table_primary_keys_refuse_duplicates(db: Db) -> None:
    snapshot = _accept(db, "config_snapshot")["id"]
    _accept(db, "snapshot_member", snapshot_id=snapshot, kind="evaluator", ordinal=0)
    _expect_violation(
        db, "unique", "snapshot_member", snapshot_id=snapshot, kind="evaluator", ordinal=0
    )
    _accept(db, "snapshot_member", snapshot_id=snapshot, kind="metric_definition", ordinal=0)
    _accept(db, "snapshot_profile_ref", snapshot_id=snapshot)
    _expect_violation(db, "unique", "snapshot_profile_ref", snapshot_id=snapshot)
    dataset = _accept(db, "dataset", version=1)["id"]
    _accept(db, "dataset_member", dataset_id=dataset, dataset_version=1, ordinal=0)
    _expect_violation(
        db, "unique", "dataset_member", dataset_id=dataset, dataset_version=1, ordinal=0
    )
    _accept(db, "dataset_member", dataset_id=dataset, dataset_version=1, ordinal=1)
    # the dataset version must exist: (dataset_id, dataset_version) is a composite foreign key
    values = db.rows.values("dataset_member", dataset_id=dataset, dataset_version=2, ordinal=0)
    with pytest.raises(DBAPI_ERROR) as caught, db.begin() as conn:
        conn.execute(sa.insert(db.metadata.tables["dataset_member"]).values(**values))
    assert violation_kind(caught.value) == "foreign_key"
    bundle = _accept(db, "run_bundle")["id"]
    _accept(db, "run_bundle_withheld", bundle_id=bundle, ordinal=0)
    _expect_violation(db, "unique", "run_bundle_withheld", bundle_id=bundle, ordinal=0)
    export = _accept(db, "export_bundle")["id"]
    item = new_uuid()
    for name, column in (
        ("export_bundle_run", "run_id"),
        ("export_bundle_item", "item_id"),
        ("export_bundle_profile", "profile_id"),
    ):
        _accept(db, name, export_id=export, **{column: item})
        _expect_violation(db, "unique", name, export_id=export, **{column: item})
        _accept(db, name, export_id=export, **{column: new_uuid()})
    audit = _accept(db, "deletion_audit", reason="operator_request", root_kind="run", counts="{}")
    assert audit["actor"] == "x"
    _expect_violation(db, "unique", "deletion_audit", audit_uuid=audit["audit_uuid"])
    for value in ("", "operator", "OPERATOR_REQUEST"):
        _expect_violation(db, "check", "deletion_audit", reason=value, root_kind="run")
    _expect_violation(db, "check", "deletion_audit", reason="operator_request", root_kind="user")


def test_dataset_member_cascades_with_its_dataset_version(db: Db) -> None:
    dataset = _accept(db, "dataset", version=1)["id"]
    _accept(db, "dataset", id=dataset, version=2)
    _accept(db, "dataset_member", dataset_id=dataset, dataset_version=1, ordinal=0)
    _accept(db, "dataset_member", dataset_id=dataset, dataset_version=2, ordinal=0)
    table = db.metadata.tables["dataset"]
    with db.begin() as conn:
        conn.execute(sa.delete(table).where(sa.and_(table.c.id == dataset, table.c.version == 1)))
    assert _count(db, "dataset_member", dataset_id=dataset, dataset_version=1) == 0
    assert _count(db, "dataset_member", dataset_id=dataset, dataset_version=2) == 1


def test_adr_side_table_columns_are_not_null_except_record_version(evaldb: ModuleType) -> None:
    for row in adr_table2():
        for column in evaldb.metadata.tables[row.table].columns:
            assert column.nullable == is_nullable(row.table, column.name), (row.table, column.name)


# ----------------------------------------------------------------------------- sqlite


def test_sqlite_tables_are_strict_without_rowid(sqlite_db: Db) -> None:
    for name in ALL_TABLES:
        options = sqlite_db.metadata.tables[name].dialect_options["sqlite"]
        assert options["strict"] is True, name
        assert options["with_rowid"] is False, name
    with sqlite_db.engine.connect() as conn:
        listed = conn.exec_driver_sql("PRAGMA table_list").all()
    mine = {row[1]: row for row in listed if not row[1].startswith("sqlite_")}
    assert set(mine) == set(ALL_TABLES)  # alembic_version is created by Alembic, not here
    for name, row in mine.items():
        # PRAGMA table_list: schema, name, type, ncol, wr, strict
        assert row[2] == "table", name
        assert row[4] == 1, f"{name} is not WITHOUT ROWID"
        assert row[5] == 1, f"{name} is not STRICT"


def test_sqlite_instant_and_decimal_shape_checks(sqlite_db: Db) -> None:
    good_instants = [
        "0001-01-01T00:00:00.000000Z",
        "9999-12-31T23:59:59.999999Z",
        "2026-10-23T12:00:00.123456Z",
    ]
    bad_instants = [
        "", "2026-10-23", "2026-10-23T12:00:00Z", "2026-10-23 12:00:00.000000Z",
        "2026-10-23T12:00:00.000000z", "2026-10-23T12:00:00.000000+00:00",
        "2026-10-23T12:00:00.00000Z", "2026-10-23T12:00:00.0000000Z", "2026-1x-23T12:00:00.000000Z",
        "abcdefghijklmnopqrstuvwxyz0", " 2026-10-23T12:00:00.000000Z",
    ]  # fmt: skip
    for value in good_instants:
        _accept(sqlite_db, "project", created_at=Raw(value))
    for value in bad_instants:
        _expect_violation(sqlite_db, "check", "project", created_at=Raw(value))
    good_decimals = [
        "0",
        "1",
        "10",
        "1.5",
        "0.000000000000000001",
        "123456789012345678901234567890.123456789012345678",
    ]
    bad_decimals = [
        "",
        " 1",
        "1 ",
        "+1",
        "1e3",
        "1E3",
        "abc",
        "1.",
        ".5",
        "--1",
        "1.2.3",
        "NaN",
        "Infinity",
        "0x10",
        "1,5",
    ]
    for table, column, extra in (
        ("cost_record", "amount", {"status": "available", "basis": "reported", "currency": "USD"}),
        ("cost_summary", "total", {"currency": "USD"}),
    ):
        for value in good_decimals:
            _accept(sqlite_db, table, **{column: Raw(value)}, **extra)
        for value in bad_decimals:
            _expect_violation(sqlite_db, "check", table, **{column: Raw(value)}, **extra)


# --------------------------------------------------------------------------- DDL and the guard


def test_ddl_compiles_on_both_backends(evaldb: ModuleType) -> None:
    metadata: sa.MetaData = evaldb.metadata
    assert len(metadata.tables) == 37
    for table in metadata.tables.values():
        sqlite_text = ddl(table, "sqlite")
        pg_text = ddl(table, "postgresql")
        assert re.search(r"\bSTRICT\b", sqlite_text), table.name
        assert "WITHOUT ROWID" in sqlite_text, table.name
        assert not re.search(r"\bSTRICT\b", pg_text), table.name
        assert "WITHOUT ROWID" not in pg_text, table.name
        for text in (pg_text, sqlite_text):
            found = re.findall(r"\b(JSONB?|DATETIME|FLOAT|REAL|DOUBLE|TIMESTAMP WITHOUT)\b", text)
            assert not found, (table.name, found)
        for index in table.indexes:
            for dialect in ("sqlite", "postgresql"):
                str(sa.schema.CreateIndex(index).compile(dialect=dialect_of(dialect)))


TYPE_BOUND = {"UuidCol": "uuid_v7", "Sha256Col": "sha256_len", "BoolCol": "bool"}


def _lines(text: str) -> list[str]:
    return sorted(line.strip().rstrip(",") for line in text.splitlines() if line.strip())


def _copied(metadata: sa.MetaData, **kwargs: Any) -> sa.MetaData:
    target = sa.MetaData()
    for table in metadata.sorted_tables:
        table.to_metadata(target, **kwargs)
    return target


def test_to_metadata_copy_has_exactly_one_check_per_type_bound_column(evaldb: ModuleType) -> None:
    """Guard for the private SQLAlchemy names the column types rely on (`_type_bound`,
    `_traverse_internals`): copying the tables (Alembic's batch mode and `to_metadata` do) must
    neither drop nor duplicate the CHECK of a UuidCol, Sha256Col or BoolCol column, and the DDL
    must compile on both backends."""
    metadata: sa.MetaData = evaldb.metadata
    first = _copied(metadata)
    generations = [first, _copied(first)]
    checked = 0
    for name, original in metadata.tables.items():
        for dialect in ("sqlite", "postgresql"):
            reference = _lines(ddl(original, dialect))
            for generation in generations:
                # Table.to_metadata may reorder the constraints of a table, never change them
                assert _lines(ddl(generation.tables[name], dialect)) == reference, (name, dialect)
        for column in original.columns:
            suffix = TYPE_BOUND.get(type(column.type).__name__)
            if suffix is None:
                continue
            checked += 1
            check_name = f"{column.name}_{suffix}"
            for table in (original, *(g.tables[name] for g in generations)):
                named = [
                    c
                    for c in table.constraints
                    if isinstance(c, sa.CheckConstraint) and c.name == check_name
                ]
                assert len(named) == 1, (name, column.name, len(named))
                for dialect in ("sqlite", "postgresql"):
                    text = ddl(table, dialect)
                    assert text.count(f"CONSTRAINT {check_name} ") == 1, (name, check_name)
    expected = sum(
        logical_type(name, column.name) in TYPE_BOUND
        for name, table in metadata.tables.items()
        for column in table.columns
    )
    assert checked == expected > 100  # every UuidCol, Sha256Col and BoolCol column


def test_alembic_version_is_not_part_of_metadata(evaldb: ModuleType) -> None:
    assert (
        len(evaldb.metadata.tables) == 37
    )  # a populated metadata, and Alembic's table is not in it
    assert "alembic_version" not in evaldb.metadata.tables
    assert not [t for t in evaldb.metadata.tables if t.startswith(("alembic", "sqlite_"))]
