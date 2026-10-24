# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared fixtures for the tracer-evaldb tests (Phase 2 batch A).

Contract: docs/phase2/api-surface-batch-a.md.

Nothing heavy is imported at module level: ``tracer_evaldb`` and ``sqlalchemy`` are loaded by the
``evaldb`` and ``sa`` fixtures, so that before the package exists each test fails on its own with
``ModuleNotFoundError`` instead of the whole directory failing to collect.

The ``store`` fixture is parametrized ``[sqlite]`` and ``[postgres]``. The ``[postgres]`` case
carries the marker ``postgres`` (deselected by the repository ``addopts``, selected in the
Postgres CI job). It is never skipped: with a postgres-marked test selected and
``TRACER_TEST_POSTGRES_URL`` unset, collection ends with a usage error.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

POSTGRES_URL_ENV = "TRACER_TEST_POSTGRES_URL"
POSTGRES_SQL_ASCII_URL_ENV = "TRACER_TEST_POSTGRES_SQL_ASCII_URL"


def pytest_configure(config: pytest.Config) -> None:
    # Also registered in the root pyproject.toml (required change, contract section 7); declared
    # here too so that this directory runs on its own with --strict-markers.
    config.addinivalue_line(
        "markers",
        "postgres: needs a Postgres server (TRACER_TEST_POSTGRES_URL); deselected by default, "
        "selected in the Postgres CI job with -m postgres",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Runs after the -m deselection: a selected postgres test without a URL is a usage error."""
    if os.environ.get(POSTGRES_URL_ENV):
        return
    if any(item.get_closest_marker("postgres") is not None for item in items):
        raise pytest.UsageError(
            f"tests marked 'postgres' are selected but {POSTGRES_URL_ENV} is not set; set it or "
            "deselect them with -m 'not postgres' (see docs/phase2/api-surface-batch-a.md)"
        )


@pytest.fixture(scope="session")
def evaldb() -> ModuleType:
    return importlib.import_module("tracer_evaldb")


@pytest.fixture(scope="session")
def sa() -> Any:
    return importlib.import_module("sqlalchemy")


class StoreHandle:
    """A store engine plus helpers to create scratch tables with unique names."""

    def __init__(self, sa: Any, evaldb: ModuleType, backend: str, url: str) -> None:
        self.sa = sa
        self.backend = backend
        self.url = url
        self.engine: Any = evaldb.create_store_engine(url)
        self.metadata: Any = sa.MetaData()

    def table(self, name: str, *items: Any) -> Any:
        """Create (and register for dropping) a table named ``<name>_<hex>``; STRICT on SQLite."""
        table = self.sa.Table(
            f"{name}_{uuid.uuid4().hex[:12]}", self.metadata, *items, sqlite_strict=True
        )
        table.create(self.engine)
        return table

    def sql_type(self, table: Any, column: str) -> str:
        """The declared type of a column as the database reports it (SQLite upper case)."""
        sa = self.sa
        with self.engine.connect() as conn:
            if self.backend == "sqlite":
                rows = conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")').all()
                return str(next(r[2] for r in rows if r[1] == column)).upper()
            found = conn.execute(
                sa.text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = :t AND a.attname = :c AND a.attnum > 0"
                ),
                {"t": table.name, "c": column},
            ).scalar_one()
            return str(found)

    def collation(self, table: Any, column: str) -> str | None:
        """Postgres: the explicit collation of a column (None when the default applies)."""
        assert self.backend == "postgres"
        with self.engine.connect() as conn:
            found = conn.execute(
                self.sa.text(
                    "SELECT collation_name FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table.name, "c": column},
            ).scalar_one()
            return None if found is None else str(found)

    def close(self) -> None:
        try:
            self.metadata.drop_all(self.engine)
        finally:
            self.engine.dispose()


def _postgres_url() -> str:
    url = os.environ.get(POSTGRES_URL_ENV)
    assert url, f"{POSTGRES_URL_ENV} is not set"  # collection already refused this case
    return url


@pytest.fixture
def sqlite_store(sa: Any, evaldb: ModuleType, tmp_path: Path) -> Iterator[StoreHandle]:
    handle = StoreHandle(sa, evaldb, "sqlite", f"sqlite:///{tmp_path / 'store.db'}")
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def postgres_store(sa: Any, evaldb: ModuleType) -> Iterator[StoreHandle]:
    """For tests that carry ``@pytest.mark.postgres`` themselves."""
    handle = StoreHandle(sa, evaldb, "postgres", _postgres_url())
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def store(request: pytest.FixtureRequest, sa: Any, evaldb: ModuleType) -> Iterator[StoreHandle]:
    if request.param == "sqlite":
        tmp_path: Path = request.getfixturevalue("tmp_path")
        handle = StoreHandle(sa, evaldb, "sqlite", f"sqlite:///{tmp_path / 'store.db'}")
    else:
        handle = StoreHandle(sa, evaldb, "postgres", _postgres_url())
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def postgres_sql_ascii_url() -> str:
    url = os.environ.get(POSTGRES_SQL_ASCII_URL_ENV)
    assert url, f"{POSTGRES_SQL_ASCII_URL_ENV} is not set (the CI job creates that database)"
    return url
