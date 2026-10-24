# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P2-04: ``create_store_engine`` (contract docs/phase2/api-surface-batch-a.md section 4).

SQLite cases run everywhere; the ``postgres`` cases need ``TRACER_TEST_POSTGRES_URL`` and are
selected with ``-m postgres``. Tests are not skipped on any Python version: the rollback test is
the one that matters on 3.11 (legacy transaction control of the sqlite3 module).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import sqlite3
import traceback
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _insert(sa: Any, table: Any, **values: Any) -> Any:
    return sa.insert(table).values(**values)


def test_create_store_engine_signature_and_error_hierarchy(evaldb: ModuleType, sa: Any) -> None:
    signature = inspect.signature(evaldb.create_store_engine)
    params = list(signature.parameters.values())
    assert [p.name for p in params] == ["url", "busy_timeout_ms"]
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[1].default == 5000
    assert evaldb.create_store_engine.__module__ == "tracer_evaldb.engine"
    errors = importlib.import_module("tracer_evaldb.errors")
    assert issubclass(errors.StoreError, Exception)
    for name in (
        "StoreConfigurationError",
        "StoreConnectionError",
        "UnsupportedSqliteVersion",
        "UnsupportedServerEncoding",
    ):
        cls = getattr(errors, name)
        assert issubclass(cls, errors.StoreError), name
        assert getattr(evaldb, name) is cls, f"{name} is re-exported from tracer_evaldb"
    assert evaldb.StoreError is errors.StoreError


def test_sqlite_connections_enforce_foreign_keys(sqlite_store: Any, sa: Any) -> None:
    engine = sqlite_store.engine
    connections = [engine.connect() for _ in range(4)]
    try:
        raw_ids = {id(c.connection.driver_connection) for c in connections}
        assert len(raw_ids) == 4, "the test must hold four distinct connections"
        for conn in connections:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        for conn in connections:
            conn.close()
    engine.dispose()  # a connection created after a dispose is configured as well
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    raw = engine.raw_connection()
    try:
        assert raw.driver_connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        raw.close()
    from_factory = importlib.import_module("tracer_evaldb").create_store_engine(sqlite_store.url)
    try:
        with from_factory.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        from_factory.dispose()


def test_sqlite_orphan_insert_rejected(sqlite_store: Any, sa: Any) -> None:
    parent = sqlite_store.table("parent", sa.Column("id", sa.Integer, primary_key=True))
    child = sqlite_store.table(
        "child",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey(parent.c.id, ondelete="CASCADE")),
    )
    engine = sqlite_store.engine
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(_insert(sa, child, id=1, parent_id=999))
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(child)).scalar_one() == 0
    with engine.begin() as conn:
        conn.execute(_insert(sa, parent, id=1))
        conn.execute(_insert(sa, child, id=1, parent_id=1))
    with engine.begin() as conn:  # ON DELETE CASCADE only works when the pragma is on
        conn.execute(sa.delete(parent))
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(child)).scalar_one() == 0


def test_sqlite_rollback_is_real_on_every_python(sqlite_store: Any, sa: Any) -> None:
    engine = sqlite_store.engine
    table = sqlite_store.table(
        "rb", sa.Column("pk", sa.Integer, primary_key=True), sa.Column("v", sa.Text)
    )

    def values() -> list[str]:
        with engine.connect() as conn:
            return [r[0] for r in conn.execute(sa.select(table.c.v).order_by(table.c.pk))]

    # a transaction is open from the first statement (the BEGIN event), not from the first write
    with engine.begin() as conn:
        conn.exec_driver_sql("SELECT 1")
        assert conn.connection.driver_connection.in_transaction is True
    with engine.connect() as conn:
        assert conn.connection.driver_connection.in_transaction is False

    # DML rolls back
    def write_then_fail() -> None:
        with engine.begin() as conn:
            conn.execute(_insert(sa, table, v="gone"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_then_fail()
    assert values() == []

    # a failing statement undoes the statements before it
    def duplicate_key() -> None:
        with engine.begin() as conn:
            conn.execute(_insert(sa, table, pk=1, v="first"))
            conn.execute(_insert(sa, table, pk=1, v="duplicate key"))

    with pytest.raises(sa.exc.IntegrityError):
        duplicate_key()
    assert values() == []

    # DDL is transactional on SQLite: legacy mode of the sqlite3 module would have committed it
    scratch = sa.Table(f"ddl_{uuid.uuid4().hex[:12]}", sa.MetaData(), sa.Column("x", sa.Integer))
    with engine.connect() as conn:
        transaction = conn.begin()
        scratch.create(conn)
        assert sa.inspect(conn).has_table(scratch.name)
        transaction.rollback()
    assert not sa.inspect(engine).has_table(scratch.name)

    # SAVEPOINT: the inner rollback keeps the outer work
    with engine.begin() as conn:
        conn.execute(_insert(sa, table, v="outer"))
        savepoint = conn.begin_nested()
        conn.execute(_insert(sa, table, v="inner"))
        savepoint.rollback()
        conn.execute(_insert(sa, table, v="outer2"))
    assert values() == ["outer", "outer2"]

    # commit is durable for another engine on the same file
    other = importlib.import_module("tracer_evaldb").create_store_engine(sqlite_store.url)
    try:
        with other.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 2
    finally:
        other.dispose()


def test_sqlite_version_at_least_3_37(
    evaldb: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert sqlite3.sqlite_version_info >= (3, 37, 0), (
        f"STRICT tables need SQLite 3.37; this interpreter has {sqlite3.sqlite_version}"
    )
    # create_store_engine reads sqlite3.sqlite_version_info when it is called and refuses an old
    # library before it connects (nothing is created), without echoing the URL
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.36.0")
    target = tmp_path / "old-sqlite-marker.db"
    with pytest.raises(evaldb.UnsupportedSqliteVersion) as info:
        evaldb.create_store_engine(f"sqlite:///{target}")
    assert "3.37" in str(info.value)
    assert "3.36" in str(info.value)
    assert str(target) not in str(info.value)
    assert target.name not in str(info.value)
    assert not target.exists()


def test_sqlite_busy_timeout_is_configured(
    evaldb: ModuleType, sqlite_store: Any, tmp_path: Path
) -> None:
    with sqlite_store.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
    engine = evaldb.create_store_engine(f"sqlite:///{tmp_path / 'b.db'}", busy_timeout_ms=1234)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 1234
    finally:
        engine.dispose()
    for bad in (-1, True, 1.5, "10", None):
        with pytest.raises(evaldb.StoreConfigurationError):
            evaldb.create_store_engine(f"sqlite:///{tmp_path / 'c.db'}", busy_timeout_ms=bad)


@pytest.mark.postgres
def test_postgres_session_timezone_is_utc(postgres_store: Any, sa: Any) -> None:
    # the CI database default is Asia/Kolkata, so a connection that did not set the session
    # time zone would not report UTC
    plain = sa.create_engine(postgres_store.url)
    try:
        with plain.connect() as conn:
            assert conn.exec_driver_sql("SHOW TimeZone").scalar_one() == "Asia/Kolkata"
    finally:
        plain.dispose()
    connections = [postgres_store.engine.connect() for _ in range(3)]
    try:
        for conn in connections:
            assert conn.exec_driver_sql("SHOW TimeZone").scalar_one() == "UTC"
            text = conn.exec_driver_sql(
                "SELECT '2026-01-01 00:00:00+05:30'::timestamptz::text"
            ).scalar_one()
            assert text == "2025-12-31 18:30:00+00"
    finally:
        for conn in connections:
            conn.close()


@pytest.mark.postgres
def test_postgres_non_utf8_database_refused(
    evaldb: ModuleType, sa: Any, postgres_sql_ascii_url: str
) -> None:
    plain = sa.create_engine(postgres_sql_ascii_url)
    try:
        with plain.connect() as conn:
            assert conn.exec_driver_sql("SHOW server_encoding").scalar_one() == "SQL_ASCII"
    finally:
        plain.dispose()
    with pytest.raises(evaldb.UnsupportedServerEncoding) as info:
        evaldb.create_store_engine(postgres_sql_ascii_url)
    message = str(info.value)
    assert "SQL_ASCII" in message
    assert "UTF8" in message
    url = sa.engine.make_url(postgres_sql_ascii_url)
    for secret in (url.password, url.host):
        assert secret is None or secret not in message


def _leaks(exc: BaseException, secrets: list[str]) -> list[str]:
    texts = [
        str(exc),
        repr(exc),
        *map(str, exc.args),
        "".join(traceback.format_exception(exc)),
    ]
    assert exc.__cause__ is None, "the original error (which holds the URL) is not chained"
    assert exc.__context__ is None or exc.__suppress_context__
    return [s for s in secrets for t in texts if s and s in t]


def test_database_url_never_echoed_in_errors_or_logs(
    store: Any, evaldb: ModuleType, sa: Any, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    token = uuid.uuid4().hex
    user, password, host, database = f"u{token}", f"p{token}", f"h{token}.example", f"d{token}"
    caplog.set_level(logging.DEBUG)

    scenarios: list[tuple[Any, list[str], str]] = [
        (
            f"mysql+pymysql://{user}:{password}@{host}/{database}",
            [user, password, host, database],
            "StoreConfigurationError",
        ),
        (
            f"postgresql+pg8000://{user}:{password}@{host}:notaport/{database}",
            [user, password, host, database],
            "StoreConfigurationError",
        ),
        (
            f"postgresql://{user}:{password}@{host}/{database}",
            [user, password, host, database],
            "StoreConfigurationError",
        ),
        (
            sa.engine.URL.create("mysql+pymysql", user, password, host, None, database),
            [user, password, host, database],
            "StoreConfigurationError",
        ),
    ]
    if store.backend == "sqlite":
        missing_dir = f"nodir-{token}"
        scenarios.append(
            (
                f"sqlite:///{tmp_path / missing_dir / 'x.db'}",
                [missing_dir],
                "StoreConnectionError",
            )
        )
    else:
        scenarios.append(
            (
                f"postgresql+pg8000://{user}:{password}@127.0.0.1:1/{database}",
                [user, password, database, "127.0.0.1"],
                "StoreConnectionError",
            )
        )
        real = sa.engine.make_url(store.url)
        wrong = real.set(password=password)
        scenarios.append(
            (wrong.render_as_string(hide_password=False), [password], "StoreConnectionError")
        )
    for url, secrets, error_name in scenarios:
        with pytest.raises(getattr(evaldb, error_name)) as info:
            evaldb.create_store_engine(url)
        assert _leaks(info.value, secrets) == [], (error_name, str(info.value))
    assert not [s for _, secrets, _ in scenarios for s in secrets if s in caplog.text], (
        "a refused URL reached the log"
    )

    # a successful creation logs nothing that holds the URL either
    caplog.clear()
    parsed = sa.engine.make_url(store.url)
    engine = evaldb.create_store_engine(store.url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    finally:
        engine.dispose()
    secrets = [str(parsed.password or ""), str(parsed.host or ""), str(parsed.database or "")]
    if store.backend == "postgres":
        secrets = secrets[:2]
    assert not [s for s in secrets if s and s in caplog.text]
