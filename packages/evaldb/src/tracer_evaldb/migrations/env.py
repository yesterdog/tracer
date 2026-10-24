# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Alembic environment.

The engine is the caller's (``config.attributes["engine"]``, set by ``tracer_evaldb.migrate``) or,
on the command line, one made from ``-x url=<database URL>``. Nothing is read from ``alembic.ini``.
Every revision runs in its own transaction, DDL included. On SQLite foreign keys are off while
the revisions run (batch rebuilds would otherwise cascade-delete child rows), ``PRAGMA
foreign_key_check`` runs inside each revision's transaction, and they are on again afterwards.
"""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy.engine import Connection, Engine

from tracer_evaldb import create_store_engine, metadata
from tracer_evaldb.errors import MigrationIntegrityError, StoreConfigurationError

config = context.config


def _engine() -> tuple[Engine, bool]:
    """The engine and whether this module made it (and so disposes of it)."""
    given = config.attributes.get("engine")
    if given is not None:
        return given, False
    url = context.get_x_argument(as_dictionary=True).get("url")
    if not url:
        raise StoreConfigurationError("no engine given: pass -x url=<database URL>")
    return create_store_engine(url), True


def _include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "table" and name == "alembic_version")


def _check_foreign_keys(ctx: Any, step: Any, heads: Any, run_args: Any) -> None:
    rows = ctx.connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if rows:
        raise MigrationIntegrityError([str(row[0]) for row in rows])


def _migrate(connection: Connection) -> None:
    sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=True,
        transactional_ddl=True,
        transaction_per_migration=True,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        on_version_apply=_check_foreign_keys if sqlite else None,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        _migrate(connection)
        return
    # The pragma is a no-op inside a transaction, so it goes through the DBAPI connection before
    # SQLAlchemy sends the first statement.
    raw: Any = connection.connection.dbapi_connection
    raw.execute("PRAGMA foreign_keys=OFF")
    try:
        _migrate(connection)
    finally:
        if connection.in_transaction():
            connection.rollback()
        raw.execute("PRAGMA foreign_keys=ON")


def run() -> None:
    engine, owned = _engine()
    try:
        with engine.connect() as connection:
            _run(connection)
    finally:
        if owned:
            engine.dispose()


run()
