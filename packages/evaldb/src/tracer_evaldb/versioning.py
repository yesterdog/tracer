# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``open_store``: the read-only check that a database is at this evaldb's head revision and holds
the installed schema version (ADR 0021 rules 1 and 3). It never migrates."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from tracer_evalschema import SCHEMA_VERSION

from .errors import (
    StoreNotMigrated,
    StoreRevisionUnknown,
    StoreSchemaVersionMismatch,
    StoreSchemaVersionTooNew,
)
from .migrate import alembic_config, current_revision, head_revision, is_known

__all__ = ["StoreInfo", "open_store"]


@dataclass(frozen=True)
class StoreInfo:
    revision: str
    schema_version: str
    evaldb_version: str


def _numbers(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    if not all(part.isascii() and part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def open_store(engine: Engine) -> StoreInfo:
    """Check the database against this evaldb; raise a typed error rather than guess."""
    if not isinstance(engine, Engine):
        raise TypeError("the first argument must be the Engine of create_store_engine")
    from alembic.script import ScriptDirectory

    head = head_revision()
    revision = current_revision(engine)
    if revision is None:
        raise StoreNotMigrated(None, head)
    if not is_known(ScriptDirectory.from_config(alembic_config()), revision):
        raise StoreRevisionUnknown(revision)
    if revision != head:
        raise StoreNotMigrated(revision, head)
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT schema_version, evaldb_version FROM tracer_meta WHERE singleton = 1")
        ).first()
        connection.rollback()
    if row is None:
        raise StoreSchemaVersionMismatch(None, SCHEMA_VERSION, "tracer_meta has no row")
    stored, evaldb_version = str(row[0]), str(row[1])
    if stored != SCHEMA_VERSION:
        found, wanted = _numbers(stored), _numbers(SCHEMA_VERSION)
        if found is not None and wanted is not None and found > wanted:
            raise StoreSchemaVersionTooNew(stored, SCHEMA_VERSION, "the store is newer")
        raise StoreSchemaVersionMismatch(stored, SCHEMA_VERSION, "the store is older or unreadable")
    return StoreInfo(revision, stored, evaldb_version)
