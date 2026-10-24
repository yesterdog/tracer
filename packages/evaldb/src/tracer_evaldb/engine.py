# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``create_store_engine``: SQLAlchemy engines configured the way the store needs them.

Every failure is raised ``from None`` with a fixed message: the database URL (password, user,
host, database name, file path) never reaches an exception, a repr or a log record of this code.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import URL, Connection, Engine, make_url

from .errors import (
    StoreConfigurationError,
    StoreConnectionError,
    UnsupportedServerEncoding,
    UnsupportedSqliteVersion,
)

__all__ = ["create_store_engine"]

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
    engine = sa.create_engine(url)

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
        connection.exec_driver_sql("BEGIN")

    _probe(engine).close()
    return engine


def _postgres_engine(url: URL) -> Engine:
    try:
        engine = sa.create_engine(url)
    except Exception:  # the pg8000 extra is not installed
        raise StoreConfigurationError("the Postgres driver (pg8000) is not available") from None

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
