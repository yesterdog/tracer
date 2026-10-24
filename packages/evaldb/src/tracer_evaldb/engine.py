# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``create_store_engine``: SQLAlchemy engines configured the way the store needs them.

Every failure is raised ``from None`` with a fixed message: the database URL (password, user,
host, database name, file path) never reaches an exception, a repr or a log record of this code.

Both engines hide bound parameters in errors (ADR 0019 Decision 18). pg8000 reports every server
error, constraint violations included, as a ``DatabaseError`` (``IntegrityError`` for 23505 only);
the Postgres engine raises every SQLSTATE of class 23 as ``sqlalchemy.exc.IntegrityError`` (what
SQLite raises natively) and drops the fields of the server's error that quote the failing row, in
the whole chain of exceptions. Both engines raise their integrity errors without bound parameters
(``params`` is ``None``). A new SQLite file is created with mode 0600.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import URL, Connection, Engine, ExceptionContext, make_url

from .errors import (
    StoreConfigurationError,
    StoreConnectionError,
    UnsupportedServerEncoding,
    UnsupportedSqliteVersion,
)

__all__ = [
    "BEGIN_OPTION",
    "INTEGRITY_SQLSTATE_CLASS",
    "create_store_engine",
    "is_integrity_sqlstate",
    "sqlstate_of",
]

INTEGRITY_SQLSTATE_CLASS = "23"
# Execution option naming the statement that opens a SQLite transaction ("BEGIN IMMEDIATE").
BEGIN_OPTION = "tracer_sqlite_begin"
# The fields of a Postgres error that name no row value: severity, SQLSTATE, message, schema, table,
# column, data type, constraint. The detail ("Failing row contains ...", "Key (id)=(...)"), hint,
# context and source position are dropped.
_SAFE_ERROR_FIELDS = ("S", "V", "C", "M", "s", "t", "c", "d", "n")

# STRICT tables (3.37) are part of the storage design.
_SQLITE_MINIMUM = (3, 37, 0)


def create_store_engine(url: str | URL, *, busy_timeout_ms: int = 5000) -> Engine:
    """Create an engine for a SQLite file (``sqlite:///path``) or Postgres (``postgresql+pg8000://``).

    The engine connects once before it is returned. Anything else is refused with
    ``StoreConfigurationError``.
    """
    if type(busy_timeout_ms) is not int or busy_timeout_ms < 0:
        raise StoreConfigurationError("busy_timeout_ms must be an integer of 0 or more")
    try:
        parsed = url if isinstance(url, URL) else make_url(url)
        backend, driver = parsed.get_backend_name(), parsed.get_driver_name()
    except Exception:
        raise StoreConfigurationError("the database URL could not be parsed") from None
    if (backend, driver) == ("sqlite", "pysqlite"):
        return _sqlite_engine(parsed, busy_timeout_ms)
    if (backend, driver) == ("postgresql", "pg8000"):
        return _postgres_engine(parsed)
    raise StoreConfigurationError(
        "unsupported database URL: use sqlite:///<path> or postgresql+pg8000://..."
    )


def sqlstate_of(exception: BaseException) -> str | None:
    """The SQLSTATE (field ``C``) of a pg8000 server error, else ``None``."""
    args = getattr(exception, "args", ())
    if args and isinstance(args[0], dict):
        code = args[0].get("C")
        if isinstance(code, str):
            return code
    return None


def is_integrity_sqlstate(code: object) -> bool:
    """True for exactly a five-character SQLSTATE of class 23 (integrity constraint violation)."""
    return (
        isinstance(code, str)
        and len(code) == 5
        and code.isascii()
        and code.startswith(INTEGRITY_SQLSTATE_CLASS)
    )


def _scrub_chain(error: BaseException) -> None:
    """Keep only the safe fields in every server error reachable from ``error``.

    pg8000 raises its typed error while handling the raw ``DatabaseError`` of the server, so the
    raw error (with the detail "Failing row contains ...") is the ``__context__`` of ``orig``.
    """
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        link = pending.pop()
        if id(link) in seen:
            continue
        seen.add(id(link))
        if link.args and isinstance(link.args[0], dict):
            fields = link.args[0]
            link.args = ({name: fields[name] for name in _SAFE_ERROR_FIELDS if name in fields},)
        pending.extend(next_ for next_ in (link.__cause__, link.__context__) if next_ is not None)


def _translate_integrity(context: ExceptionContext) -> Exception | None:
    original = context.original_exception
    if original is None or not is_integrity_sqlstate(sqlstate_of(original)):
        return None
    _scrub_chain(original)
    # params=None: the bound values are the stored text; ``hide_parameters`` alone hides them from
    # the message only, not from the attribute.
    return sa.exc.IntegrityError(context.statement, None, original, hide_parameters=True)


def _drop_integrity_params(context: ExceptionContext) -> Exception | None:
    original = context.original_exception
    if not isinstance(original, sqlite3.IntegrityError):
        return None
    return sa.exc.IntegrityError(context.statement, None, original, hide_parameters=True)


def _create_private_file(url: URL) -> None:
    """Create a new database file readable by its owner only (``0600`` under any umask that allows
    it). A file that exists is not touched; ``:memory:`` and ``file:`` URIs make no file."""
    path = url.database
    if not path or path == ":memory:" or path.startswith("file:"):
        return
    # an existing file, or one that cannot be created: the first connection reports it
    with contextlib.suppress(OSError):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def _probe(engine: Engine) -> Connection:
    try:
        return engine.connect()
    except Exception:
        engine.dispose()
        raise StoreConnectionError("could not connect to the database") from None


def _sqlite_engine(url: URL, busy_timeout_ms: int) -> Engine:
    if sqlite3.sqlite_version_info < _SQLITE_MINIMUM:
        raise UnsupportedSqliteVersion(
            f"SQLite {sqlite3.sqlite_version} is too old; 3.37 or newer is required"
        )
    _create_private_file(url)
    engine = sa.create_engine(url, hide_parameters=True)
    event.listen(engine, "handle_error", _drop_integrity_params)

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        # The sqlite3 module's implicit transaction handling differs between Python versions
        # (and rolls back nothing for DDL on 3.11). With isolation_level=None the module never
        # opens a transaction itself; the "begin" event below always does.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        # A deletion asks for BEGIN IMMEDIATE: it takes the write lock before it reads anything.
        statement = connection.get_execution_options().get(BEGIN_OPTION, "BEGIN")
        connection.exec_driver_sql(statement)

    _probe(engine).close()
    return engine


def _postgres_engine(url: URL) -> Engine:
    try:
        engine = sa.create_engine(url, hide_parameters=True)
    except Exception:  # the pg8000 extra is not installed
        raise StoreConfigurationError("the Postgres driver (pg8000) is not available") from None

    event.listen(engine, "handle_error", _translate_integrity)

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        # The database default time zone is not ours to rely on; instants are exchanged in UTC.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET TIME ZONE 'UTC'")
        finally:
            cursor.close()
        dbapi_connection.commit()

    connection = _probe(engine)
    try:
        encoding = connection.exec_driver_sql("SHOW server_encoding").scalar_one()
    except Exception:
        engine.dispose()
        raise StoreConnectionError("could not connect to the database") from None
    finally:
        connection.close()
    if encoding != "UTF8":
        engine.dispose()
        raise UnsupportedServerEncoding(
            f"the database server_encoding is {encoding}; UTF8 is required"
        )
    return engine
