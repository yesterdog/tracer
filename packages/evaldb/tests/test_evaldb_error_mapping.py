# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C, error mapping (ADR 0019 Decision 18, revision 35 A12; batch A ambiguity 3).

Contract: docs/phase2/api-surface-batch-c.md section 7. pg8000 reports every server error,
constraint violations included, as ``ProgrammingError``. ``create_store_engine`` translates a
SQLSTATE of class 23 (``23001``, ``23502``, ``23503``, ``23505``, ``23514`` and the rest of the
class) into ``sqlalchemy.exc.IntegrityError`` through a ``handle_error`` hook, so both backends
raise the same class (SQLite does natively). The repository turns an ``IntegrityError`` of a
write into a ``StoreConstraintViolation`` that names the kind and, when the database says so, the
table and the constraint, and never a value. The engine hides bound parameters.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    dump,
    load_run_bundle_doc,
    renew_ids,
    run_bundle_doc,
    store_bundle,
)
from evaldb_support import Db, Raw, violation_kind  # type: ignore[import-not-found,unused-ignore]

db = _support.db

CLASS_23 = ("23000", "23001", "23502", "23503", "23505", "23514", "23P01")
OTHER = (
    "00000",
    "22001",
    "22P02",
    "25P02",
    "40001",
    "40P01",
    "42601",
    "42P01",
    "55P03",
    "57014",
    "08006",
    "",
)
MARKER = "MARKER-PARAMETER-3a7d"


class _Pg8000Like(Exception):
    pass


def test_sqlstate_helpers() -> None:
    engine = importlib.import_module("tracer_evaldb.engine")
    assert engine.INTEGRITY_SQLSTATE_CLASS == "23"
    for code in CLASS_23:
        assert engine.is_integrity_sqlstate(code) is True, code
    for code in OTHER:
        assert engine.is_integrity_sqlstate(code) is False, code
    for not_a_code in (None, 23505, b"23505", "2350", "235055", " 23505"):
        assert engine.is_integrity_sqlstate(not_a_code) is False, not_a_code
    assert engine.sqlstate_of(_Pg8000Like({"S": "ERROR", "C": "23505", "M": "x"})) == "23505"
    for args in (("plain message",), ({"M": "no code"},), ({"C": 23505},), ()):
        assert engine.sqlstate_of(_Pg8000Like(*args)) is None, args


# --------------------------------------------------------------------- the hook, on both backends


def _violate(db: Db, kind: str, marker: str = "x") -> sa.exc.DBAPIError:
    """Provoke one kind of constraint violation with raw rows through the typed columns. The
    failing row carries ``marker`` in its ``doc`` (and in ``schema_version`` for a check)."""
    try:
        with db.engine.begin() as conn:
            if kind == "check":
                db.rows.insert(conn, "project", schema_version="0.0.2", doc=marker)
            elif kind == "unique":
                first = db.rows.insert(conn, "project", doc=marker)
                db.rows.insert_exact(conn, "project", {**first})
            elif kind == "not_null":
                values = db.rows.values("project", doc=marker)
                del values["name"]  # no value: NULL for a NOT NULL column
                conn.execute(sa.insert(db.metadata.tables["project"]), values)
            elif kind == "foreign_key":
                values = db.rows.values("artifact", doc=marker)  # its run_id names no run
                conn.execute(sa.insert(db.metadata.tables["artifact"]), values)
            else:  # restrict: a snapshot that a run still references
                run = db.rows.insert(conn, "run", doc=marker)
                snapshot = db.metadata.tables["config_snapshot"]
                conn.execute(sa.delete(snapshot).where(snapshot.c.id == run["config_snapshot_id"]))
    except sa.exc.DBAPIError as error:
        return error
    raise AssertionError(f"the database accepted the {kind} violation")


@pytest.mark.parametrize("kind", ["check", "unique", "not_null", "foreign_key", "restrict"])
def test_constraint_violations_are_integrity_errors_on_both_backends(db: Db, kind: str) -> None:
    error = _violate(db, kind)
    assert isinstance(error, sa.exc.IntegrityError), (type(error), str(error.orig)[:200])
    assert error.orig is not None  # the driver's error is kept
    expected = "foreign_key" if kind == "restrict" else kind
    assert violation_kind(error) == expected
    if db.backend == "postgres":
        engine = importlib.import_module("tracer_evaldb.engine")
        code = engine.sqlstate_of(error.orig)
        assert code is not None
        assert engine.is_integrity_sqlstate(code)
        # RESTRICT is 23503 on Postgres 15 and 23001 on Postgres 18 (15.19 and 18.6 observed)
        assert (
            code
            in {
                "check": ("23514",),
                "unique": ("23505",),
                "not_null": ("23502",),
                "foreign_key": ("23503",),
                "restrict": ("23503", "23001"),
            }[kind]
        )


@pytest.mark.parametrize("kind", ["check", "unique", "not_null", "foreign_key", "restrict"])
def test_translated_integrity_errors_carry_no_row_content(db: Db, kind: str) -> None:
    """Postgres quotes the failing row in the error detail ("Failing row contains (...)"), which
    would put document text into exceptions and logs. The translated error keeps the SQLSTATE, the
    message, the table, the column and the constraint, and drops the detail."""
    error = _violate(db, kind, marker=MARKER)
    assert isinstance(error, sa.exc.IntegrityError)
    assert error.orig is not None
    shown = [str(error), repr(error), str(error.orig), repr(error.orig), repr(error.orig.args)]
    for text in shown:
        assert MARKER not in text
    if db.backend == "postgres":
        fields = error.orig.args[0]
        assert isinstance(fields, dict)
        assert {"C", "M"} <= set(fields)
        assert set(fields) <= {"S", "V", "C", "M", "s", "t", "c", "d", "n"}
        assert "t" in fields  # the table is still named
        for text in (str(error), repr(error)):
            assert "Failing row" not in text
            assert "DETAIL" not in text


def _fail_inside_a_connection(db: Db) -> None:
    with db.engine.connect() as conn:
        conn.execute(sa.select(sa.literal(1)))
        raise ZeroDivisionError


def test_errors_of_other_classes_are_not_integrity_errors(db: Db) -> None:
    for statement in (
        "SELEC 1",
        "SELECT * FROM no_such_table_here",
        "SELECT no_such_column FROM project",
    ):
        with pytest.raises(sa.exc.DBAPIError) as caught, db.engine.connect() as conn:
            conn.exec_driver_sql(statement)
        assert not isinstance(caught.value, sa.exc.IntegrityError), statement
    # an exception that is not a database error passes through untouched
    with pytest.raises(ZeroDivisionError):
        _fail_inside_a_connection(db)


def test_engine_hides_bound_parameters_in_errors(db: Db) -> None:
    """ADR 0019 Decision 18: the engine is created with ``hide_parameters=True`` and
    ``echo=False``: a failing statement's error text holds no bound value."""
    assert db.engine.hide_parameters is True
    assert not db.engine.echo
    with pytest.raises(sa.exc.DBAPIError) as caught, db.engine.begin() as conn:
        db.rows.insert(conn, "project", schema_version=Raw(MARKER))
    for text in (str(caught.value), repr(caught.value)):
        assert MARKER not in text
    with pytest.raises(sa.exc.DBAPIError) as other, db.engine.begin() as conn:
        conn.execute(
            sa.text("SELECT * FROM no_such_table_here WHERE name = :v").bindparams(v=MARKER)
        )
    assert MARKER not in str(other.value)


# ---------------------------------------------------------------- the repository's typed error


def test_translate_integrity_error_names_the_kind_and_no_value(db: Db, evaldb: ModuleType) -> None:
    repository = importlib.import_module("tracer_evaldb.repository")
    for kind, table in (
        ("check", "project"),
        ("unique", "project"),
        ("not_null", "project"),
        ("foreign_key", "artifact"),
    ):
        error = _violate(db, kind)
        assert isinstance(error, sa.exc.IntegrityError)
        translated = repository.translate_integrity_error(error)
        assert isinstance(translated, evaldb.StoreConstraintViolation)
        assert isinstance(translated, evaldb.StoreError)
        assert translated.kind == kind
        if db.backend == "postgres":
            assert translated.table == table, kind  # Postgres names the table of every kind
        else:
            assert translated.table in (None, table), kind
        shown = str(translated) + repr(translated) + repr(translated.args)
        assert kind.replace("_", " ") in shown or kind in shown
        assert MARKER not in shown


def test_the_repository_raises_a_typed_error_when_the_database_refuses_a_write(
    db: Db, evaldb: ModuleType
) -> None:
    """A rescoring that lists a *different* turn at a position ((transcript, seq)) that a stored
    turn already holds is valid for the models; the unique constraint refuses it, and the
    repository reports it as a ``StoreConstraintViolation`` of kind ``unique`` on ``turn``,
    with nothing written and no driver error in the chain."""
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    second = load_run_bundle_doc(renew_ids(doc, lists=["turns", "results"], verdict=True))
    assert {t.seq for t in second.turns} == {t.seq for t in first.turns}
    assert {t.transcript_id for t in second.turns} == {t.transcript_id for t in first.turns}
    before = dump(db.engine, db.metadata)
    with pytest.raises(evaldb.StoreConstraintViolation) as caught:
        evaldb.store_run_bundle(db.engine, second)
    error = caught.value
    assert error.kind == "unique"
    assert error.table in (None, "turn") if db.backend == "sqlite" else error.table == "turn"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    shown = str(error) + repr(error)
    for turn in first.turns:
        assert turn.text not in shown
        assert str(turn.id) not in shown
    assert dump(db.engine, db.metadata) == before  # rolled back, the new bundle row included
    assert evaldb.load_run_bundle(db.engine, first.id).bundle == first


def test_error_types_and_their_parents(evaldb: ModuleType) -> None:
    """The typed errors of batch C and what they derive from (api-surface-batch-c.md section 7)."""
    from tracer_evalschema.errors import ConflictingRecordError

    store_error = evaldb.StoreError
    expected_parents = {
        "ReimportConflict": (ConflictingRecordError,),
        "RecordOwnershipConflict": (store_error,),
        "ProjectNotStored": (store_error,),
        "InternalSnapshotRefused": (store_error, ValueError),
        "RecordNotStored": (store_error,),
        "IncompleteRunBundle": (store_error,),
        "ExportReferenceNotStored": (store_error,),
        "StoreConstraintViolation": (store_error,),
    }
    for name, parents in expected_parents.items():
        cls: Any = getattr(evaldb, name)
        assert cls.__bases__ == parents, name
        assert name in evaldb.__all__
        assert getattr(importlib.import_module("tracer_evaldb.errors"), name) is cls
    assert not issubclass(evaldb.ReimportConflict, store_error)
    assert not issubclass(evaldb.RecordOwnershipConflict, ConflictingRecordError)
    assert not issubclass(evaldb.IncompleteRunBundle, ConflictingRecordError)
