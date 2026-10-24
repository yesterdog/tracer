# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C, P2-16: immutability triggers on every table (ADR 0020 Decision 7).

Contract: docs/phase2/api-surface-batch-c.md section 6. Every table of the store has a ``BEFORE
UPDATE`` trigger that raises, on both backends, with a message that names the table and no value;
``deletion_audit`` also has a ``BEFORE DELETE`` trigger. ``metadata.create_all`` creates them with
the tables (and, on Postgres, the two trigger functions first). Alembic (batch D) creates the same
objects with the same names; the catalog test below is the one that batch re-runs on the migrated
schema.
"""

from __future__ import annotations

import importlib
import re
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    SIDE_TABLES,
    Db,
    adr_table1,
)

db = _support.db

ALL_TABLES = [*(row.table for row in adr_table1()), *SIDE_TABLES]
MARKER = "MARKER-VALUE-8c1f"
AUDIT = "deletion_audit"


def _row_of(db: Db, table: str) -> dict[str, Any]:
    t = db.metadata.tables[table]
    with db.engine.connect() as conn:
        rows = conn.execute(sa.select(t)).mappings().all()
    assert len(rows) == 1
    return dict(rows[0])


def _message(error: sa.exc.DBAPIError) -> str:
    """The text the database raised (not SQLAlchemy's rendering of the statement)."""
    original = error.orig
    args = getattr(original, "args", ())
    if args and isinstance(args[0], dict):
        return str(args[0].get("M", ""))
    return str(original)


# ------------------------------------------------------------------------------ the triggers


def test_trigger_names_are_pinned(evaldb: ModuleType) -> None:
    triggers = importlib.import_module("tracer_evaldb.triggers")
    assert triggers.update_trigger_name("turn") == "trg_turn_no_update"
    assert triggers.delete_trigger_name("deletion_audit") == "trg_deletion_audit_no_delete"
    assert triggers.DELETE_PROTECTED_TABLES == ("deletion_audit",)
    assert triggers.UPDATE_TRIGGER_FUNCTION == "tracer_reject_update"
    assert triggers.DELETE_TRIGGER_FUNCTION == "tracer_reject_delete"
    assert evaldb.triggers is triggers


@pytest.mark.parametrize("table", ALL_TABLES)
def test_update_rejected_on_every_table(db: Db, table: str) -> None:
    """No UPDATE of any column of any row succeeds, not even one that changes nothing."""
    with db.engine.begin() as conn:
        db.rows.insert(conn, table)
    before = _row_of(db, table)
    t = db.metadata.tables[table]
    for column in t.columns:
        # a no-op update of one column ...
        with pytest.raises(sa.exc.IntegrityError) as caught, db.engine.begin() as conn:
            conn.execute(sa.update(t).values({column.name: t.c[column.name]}))
        message = _message(caught.value)
        assert table in message, (column.name, message)
        assert "immutable" in message, (column.name, message)
        # ... and an update that would change it, with a value that must not be echoed
        if column.name == "doc":
            with pytest.raises(sa.exc.IntegrityError) as caught_doc, db.engine.begin() as conn:
                conn.execute(sa.update(t).values({"doc": MARKER}))
            assert MARKER not in _message(caught_doc.value)
    with pytest.raises(sa.exc.IntegrityError), db.engine.begin() as conn:  # every column at once
        conn.execute(sa.update(t).values({c.name: t.c[c.name] for c in t.columns}))
    assert _row_of(db, table) == before  # unchanged


@pytest.mark.parametrize("table", ALL_TABLES)
def test_update_is_rejected_for_a_row_selected_by_key(db: Db, table: str) -> None:
    with db.engine.begin() as conn:
        db.rows.insert(conn, table)
    t = db.metadata.tables[table]
    key = [c.name for c in t.primary_key.columns]
    before = _row_of(db, table)
    with pytest.raises(sa.exc.IntegrityError), db.engine.begin() as conn:
        conn.execute(
            sa.update(t)
            .where(sa.and_(*[t.c[k] == before[k] for k in key]))
            .values({key[0]: t.c[key[0]]})
        )
    assert _row_of(db, table) == before


@pytest.mark.parametrize("table", [t for t in ALL_TABLES if t != AUDIT])
def test_delete_is_allowed_on_every_table_except_deletion_audit(db: Db, table: str) -> None:
    """Deletion roots (batch E) and the foreign-key cascades remove rows: only the audit is
    append-only."""
    with db.engine.begin() as conn:
        db.rows.insert(conn, table)
    t = db.metadata.tables[table]
    with db.engine.begin() as conn:
        conn.execute(sa.delete(t))
    with db.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one() == 0


def test_deletion_audit_rows_cannot_be_deleted(db: Db) -> None:
    with db.engine.begin() as conn:
        row = db.rows.insert(conn, AUDIT)
    t = db.metadata.tables[AUDIT]
    for statement in (
        sa.delete(t),
        sa.delete(t).where(t.c.audit_uuid == row["audit_uuid"]),
        sa.delete(t).where(sa.true()),
    ):
        with pytest.raises(sa.exc.IntegrityError) as caught, db.engine.begin() as conn:
            conn.execute(statement)
        message = _message(caught.value)
        assert AUDIT in message
        assert "immutable" in message
    assert _row_of(db, AUDIT) == row
    # the row is not reachable by a cascade either: no foreign key points at the audit
    assert not [
        fk
        for table in db.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table is t
    ]


def test_update_and_delete_of_a_missing_row_are_not_errors(db: Db) -> None:
    """Row-level triggers: a statement that touches no row is not refused."""
    t = db.metadata.tables["project"]
    with db.engine.begin() as conn:
        conn.execute(sa.update(t).where(sa.false()).values(name="x"))
        conn.execute(sa.delete(t).where(sa.false()))


# -------------------------------------------------------------------------------- the catalog


def _triggers(db: Db) -> dict[str, tuple[str, str]]:
    """name -> (table, 'BEFORE UPDATE' | 'BEFORE DELETE'), read from the database catalogs."""
    found: dict[str, tuple[str, str]] = {}
    with db.engine.connect() as conn:
        if db.backend == "sqlite":
            for name, table, sql in conn.exec_driver_sql(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
            ):
                match = re.search(r"\bBEFORE\s+(UPDATE|DELETE)\b", sql, re.IGNORECASE)
                assert match is not None, sql
                found[name] = (table, f"BEFORE {match.group(1).upper()}")
        else:
            rows = conn.exec_driver_sql(
                "SELECT t.tgname, c.relname, t.tgtype, p.proname "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE NOT t.tgisinternal AND c.relnamespace = current_schema()::regnamespace"
            )
            for name, table, tgtype, function in rows:
                # tgtype bits: 1 row, 2 before, 4 insert, 8 delete, 16 update, 32 truncate
                row_before = tgtype & 3 == 3
                assert row_before, (name, tgtype)
                assert not tgtype & (4 | 32), (name, tgtype)
                if tgtype & 16:
                    assert function == "tracer_reject_update"
                    assert not tgtype & 8
                    found[name] = (table, "BEFORE UPDATE")
                else:
                    assert tgtype & 8
                    assert function == "tracer_reject_delete"
                    found[name] = (table, "BEFORE DELETE")
    return found


def test_immutability_triggers_exist_after_migrations(db: Db, evaldb: ModuleType) -> None:
    """The 37 update triggers and the one delete trigger, and no other trigger, exist in the
    database that ``create_all`` (batch C) or the migrations (batch D) built."""
    triggers = importlib.import_module("tracer_evaldb.triggers")
    expected = {triggers.update_trigger_name(t): (t, "BEFORE UPDATE") for t in ALL_TABLES}
    expected[triggers.delete_trigger_name(AUDIT)] = (AUDIT, "BEFORE DELETE")
    assert len(expected) == 38
    assert _triggers(db) == expected
    # created again after a drop: the same objects (on Postgres the functions are replaced)
    db.metadata.drop_all(db.engine)
    assert _triggers(db) == {}
    db.metadata.create_all(db.engine)
    assert _triggers(db) == expected
    if db.backend == "postgres":
        with db.engine.connect() as conn:
            functions = {
                r[0]
                for r in conn.exec_driver_sql(
                    "SELECT proname FROM pg_proc WHERE proname LIKE 'tracer\\_reject\\_%'"
                )
            }
        assert functions == {"tracer_reject_update", "tracer_reject_delete"}
