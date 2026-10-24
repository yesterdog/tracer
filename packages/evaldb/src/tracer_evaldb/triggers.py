# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Immutability triggers (ADR 0020 Decision 7).

Every table of the store has a ``BEFORE UPDATE`` row trigger that raises, so no stored row is ever
edited (a deletion removes rows, it never rewrites one). ``deletion_audit`` also has a ``BEFORE
DELETE`` row trigger: the audit is append-only. ``DELETE`` stays allowed on every other table (the
cascades and the deletion roots need it) and ``TRUNCATE`` is not intercepted.

Both backends raise an ``IntegrityError``: SQLite through ``RAISE(ABORT, ...)``, Postgres through
a function that raises SQLSTATE ``23001`` (class 23, which the engine hook translates). The message
names the table and the word "immutable" and holds no value.

This module is the single source of the DDL: it attaches it to ``metadata`` so that
``metadata.create_all`` builds the triggers (and, on Postgres, the two functions first), and it
returns the same statements to the migrations through ``create_statements``.
"""

from __future__ import annotations

from sqlalchemy import DDL, MetaData, event

from . import tables as _tables  # noqa: F401  (fills ``metadata`` before the DDL is attached)
from .schema import metadata

__all__ = [
    "DELETE_PROTECTED_TABLES",
    "DELETE_TRIGGER_FUNCTION",
    "RESTRICT_SQLSTATE",
    "UPDATE_TRIGGER_FUNCTION",
    "create_statements",
    "delete_trigger_name",
    "function_statements",
    "update_trigger_name",
]

UPDATE_TRIGGER_FUNCTION = "tracer_reject_update"
DELETE_TRIGGER_FUNCTION = "tracer_reject_delete"
DELETE_PROTECTED_TABLES = ("deletion_audit",)
# restrict_violation: class 23, so the engine hook raises it as an IntegrityError
RESTRICT_SQLSTATE = "23001"


def update_trigger_name(table: str) -> str:
    return f"trg_{table}_no_update"


def delete_trigger_name(table: str) -> str:
    return f"trg_{table}_no_delete"


def function_statements() -> tuple[str, str]:
    """The Postgres trigger functions (idempotent: ``CREATE OR REPLACE``)."""

    def function(name: str, verb: str) -> str:
        return (
            f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN RAISE EXCEPTION USING ERRCODE = '{RESTRICT_SQLSTATE}', "
            f"MESSAGE = 'table ' || TG_TABLE_NAME || ' is immutable: {verb} is refused', "
            "TABLE = TG_TABLE_NAME; END $$"
        )

    return (
        function(UPDATE_TRIGGER_FUNCTION, "UPDATE"),
        function(DELETE_TRIGGER_FUNCTION, "DELETE"),
    )


def _operations(table: str) -> list[tuple[str, str, str]]:
    """(trigger name, operation, Postgres function) of a table."""
    found = [(update_trigger_name(table), "UPDATE", UPDATE_TRIGGER_FUNCTION)]
    if table in DELETE_PROTECTED_TABLES:
        found.append((delete_trigger_name(table), "DELETE", DELETE_TRIGGER_FUNCTION))
    return found


def create_statements(table: str, dialect: str) -> list[str]:
    """The ``CREATE TRIGGER`` statements of one table for ``sqlite`` or ``postgresql``."""
    statements = []
    for name, operation, function in _operations(table):
        if dialect == "sqlite":
            statements.append(
                f'CREATE TRIGGER "{name}" BEFORE {operation} ON "{table}" FOR EACH ROW BEGIN '
                f"SELECT RAISE(ABORT, 'table {table} is immutable: {operation} is refused'); END"
            )
        elif dialect == "postgresql":
            statements.append(
                f'CREATE TRIGGER "{name}" BEFORE {operation} ON "{table}" FOR EACH ROW '
                f"EXECUTE FUNCTION {function}()"
            )
        else:
            raise ValueError("dialect must be sqlite or postgresql")
    return statements


def install(target: MetaData) -> None:
    """Attach the trigger DDL to the tables of ``target`` and to ``target`` itself."""
    for statement in function_statements():
        event.listen(target, "before_create", DDL(statement).execute_if(dialect="postgresql"))  # type: ignore[no-untyped-call,unused-ignore]
    for table in target.tables.values():
        for dialect in ("sqlite", "postgresql"):
            for statement in create_statements(table.name, dialect):
                event.listen(table, "after_create", DDL(statement).execute_if(dialect=dialect))  # type: ignore[no-untyped-call,unused-ignore]


install(metadata)
