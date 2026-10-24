# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers for revisions (ADR 0021 rules 7 and 9 to 12).

``batch_alter_table`` keeps ``STRICT``, ``WITHOUT ROWID`` and the immutability triggers across a
SQLite table rebuild. ``rewriting`` opens a trigger-free window inside the revision's transaction.
``rewrite_documents`` moves every stored document from one schema version to the next, refusing
before any write what cannot be reversed byte for byte or would change a content digest.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from typing import Any

import sqlalchemy as sa
from alembic import op
from alembic.operations.base import BatchOperations

from tracer_evaldb import tables, triggers
from tracer_evaldb.errors import DigestChangingUpgrade, IrreversibleMigration, MixedSchemaVersions
from tracer_evalschema import digest as ev_digest
from tracer_evalschema.errors import DigestMismatch

__all__ = ["batch_alter_table", "rewrite_documents", "rewriting"]

PIN = "schema_version_pinned"
_VERSIONED = frozenset(
    {
        "agent_target",
        "persona",
        "test_profile",
        "evaluator",
        "metric_definition",
        "metric_binding",
        "rubric",
        "pricing_catalog",
        "dataset",
    }
)
_DIGESTED = _VERSIONED | {"config_snapshot"}


def _dialect() -> str:
    return op.get_bind().dialect.name


def _drop_triggers(table: str) -> None:
    for statement in triggers.drop_statements(table, _dialect()):
        op.execute(statement)


def _create_triggers(table: str) -> None:
    for statement in triggers.create_statements(table, _dialect()):
        op.execute(statement)


@contextlib.contextmanager
def batch_alter_table(table_name: str, **kwargs: Any) -> Iterator[BatchOperations]:
    """``op.batch_alter_table`` that keeps ``STRICT`` and ``WITHOUT ROWID`` on SQLite and creates
    the table's immutability triggers again when the block ends (a rebuild drops them).

    On Postgres the table is altered in place.
    """
    sqlite = _dialect() == "sqlite"
    if sqlite:
        kwargs.setdefault("table_kwargs", {"sqlite_strict": True, "sqlite_with_rowid": False})
    with op.batch_alter_table(table_name, **kwargs) as batch:
        yield batch
    if sqlite:
        _drop_triggers(table_name)
        _create_triggers(table_name)


@contextlib.contextmanager
def rewriting(*table_names: str) -> Iterator[None]:
    """Drop the immutability triggers of these tables, let the caller rewrite rows, and create the
    triggers again. An exception leaves the triggers to the revision's rollback."""
    for table in table_names:
        _drop_triggers(table)
    yield
    for table in table_names:
        _create_triggers(table)


def _plan(
    old_version: str,
    new_version: str,
    forward: Callable[[str, str], str],
    backward: Callable[[str, str], str],
    validate: Callable[[str, str], None],
) -> list[tuple[str, dict[str, Any], str, bytes]]:
    """Every row's new text, checked in full before anything is written."""
    bind = op.get_bind()
    plan: list[tuple[str, dict[str, Any], str, bytes]] = []
    for entity in tables.ENTITY_TABLES.values():
        table = entity.name
        keys = ["id", "version"] if table in _VERSIONED else ["id"]
        columns = ", ".join(f'"{key}"' for key in keys)
        select = f'SELECT {columns}, schema_version, doc FROM "{table}"'  # noqa: S608
        for row in bind.execute(sa.text(select)).mappings().all():
            if row["schema_version"] != old_version:
                raise MixedSchemaVersions(table)
            text = row["doc"]
            new_text = forward(table, text)
            if backward(table, new_text) != text:
                raise IrreversibleMigration(table)
            try:
                parsed = json.loads(new_text)
            except ValueError:
                raise MixedSchemaVersions(table) from None
            if not isinstance(parsed, dict) or parsed.get("schema_version") != new_version:
                raise MixedSchemaVersions(table)
            if table in _DIGESTED and parsed.get("content_digest") != (
                json.loads(text).get("content_digest")
            ):
                raise DigestChangingUpgrade(table)
            try:
                validate(table, new_text)
            except DigestMismatch:
                raise DigestChangingUpgrade(table) from None
            sha = bytes.fromhex(ev_digest.sha256_hex(ev_digest.jcs_bytes(parsed)))
            plan.append((table, {key: row[key] for key in keys}, new_text, sha))
    return plan


def rewrite_documents(
    old_version: str,
    new_version: str,
    *,
    forward: Callable[[str, str], str],
    backward: Callable[[str, str], str],
    validate: Callable[[str, str], None],
) -> None:
    """Move every ``doc`` of the 26 entity tables from ``old_version`` to ``new_version``.

    ``forward`` and ``backward`` are pure text functions (the upgrade step and its inverse);
    ``validate`` stands for the models of the new version and raises to refuse. Only ``doc``,
    ``doc_sha256``, ``schema_version`` and ``tracer_meta.schema_version`` are written; a revision
    that changes any other projection writes it itself inside ``rewriting``. Call it from a
    revision's ``upgrade`` or ``downgrade``.
    """
    plan = _plan(old_version, new_version, forward, backward, validate)
    bind = op.get_bind()
    pinned = [entity.name for entity in tables.ENTITY_TABLES.values()] + ["tracer_meta"]
    for table in pinned:
        with batch_alter_table(table) as batch:
            batch.drop_constraint(PIN, type_="check")
    with rewriting(*pinned):
        for table, key, new_text, sha in plan:
            where = " AND ".join(f'"{name}" = :k_{name}' for name in key)
            update = (
                f'UPDATE "{table}" SET doc = :doc, doc_sha256 = :sha, '  # noqa: S608
                f"schema_version = :version WHERE {where}"
            )
            params = {"doc": new_text, "sha": sha, "version": new_version}
            bind.execute(sa.text(update), params | {f"k_{name}": v for name, v in key.items()})
        bind.execute(
            sa.text("UPDATE tracer_meta SET schema_version = :version"), {"version": new_version}
        )
    for table in pinned:
        with batch_alter_table(table) as batch:
            batch.create_check_constraint(PIN, f"schema_version = '{new_version}'")
