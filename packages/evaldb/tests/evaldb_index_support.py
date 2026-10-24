# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers shared by the Phase 2 batch E index tests (P2-20) and the revision 0002 tests.

Contract: docs/phase2/api-surface-batch-e.md. Imported with ``from evaldb_index_support import ...``
(the tests directory has no ``__init__.py``; mypy cannot see sibling modules, hence the ``type:
ignore`` on those imports). Holds no test.

Contents: the exact index tables (the revision 0002 indexes and the indexes of batches A to D); a
catalog reader for the indexes of a database on either backend (name, table, key columns in order,
unique, partial, descending flags), built from ``PRAGMA`` on SQLite and ``pg_index`` on Postgres
(never from the text of ``indexdef``, whose spelling differs between Postgres versions); the plan
readers (``EXPLAIN QUERY PLAN`` rows and ``EXPLAIN (FORMAT JSON)`` trees); and the statement
capture that lets a plan be taken of exactly the statement the package sent.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event

# ----------------------------------------------------------------------------- the index tables

# Revision 0002: index name -> (table, key columns in order). The name is `ix_<table>_<columns>`.
NEW_INDEXES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "ix_run_project_id_id": ("run", ("project_id", "id")),
    "ix_run_agent_target_id_agent_target_version": (
        "run",
        ("agent_target_id", "agent_target_version"),
    ),
    "ix_run_mode": ("run", ("mode",)),
    "ix_run_observed_tier": ("run", ("observed_tier",)),
    "ix_run_started_at": ("run", ("started_at",)),
    "ix_run_config_snapshot_id": ("run", ("config_snapshot_id",)),
    "ix_artifact_run_id": ("artifact", ("run_id",)),
    "ix_artifact_sha256": ("artifact", ("sha256",)),
    "ix_transcript_run_id": ("transcript", ("run_id",)),
    "ix_turn_run_id": ("turn", ("run_id",)),
    "ix_exchange_run_id": ("exchange", ("run_id",)),
    "ix_tool_call_event_run_id": ("tool_call_event", ("run_id",)),
    "ix_stage_span_run_id": ("stage_span", ("run_id",)),
    "ix_metric_result_run_id": ("metric_result", ("run_id",)),
    "ix_metric_result_metric_id_metric_version_status": (
        "metric_result",
        ("metric_id", "metric_version", "status"),
    ),
    "ix_run_verdict_run_id": ("run_verdict", ("run_id",)),
    "ix_run_verdict_status": ("run_verdict", ("status",)),
    "ix_usage_record_run_id": ("usage_record", ("run_id",)),
    "ix_cost_record_run_id": ("cost_record", ("run_id",)),
    "ix_cost_summary_run_id": ("cost_summary", ("run_id",)),
    "ix_run_bundle_run_id": ("run_bundle", ("run_id",)),
    "ix_config_snapshot_content_digest_value": ("config_snapshot", ("content_digest_value",)),
    "ix_metric_definition_key": ("metric_definition", ("key",)),
    "ix_dataset_item_artifact_id": ("dataset_item", ("artifact_id",)),
    "ix_dataset_item_transcript_id": ("dataset_item", ("transcript_id",)),
}

# Batches A to D (already in the shipped revision 0001): name -> (table, columns, unique, partial)
OLD_INDEXES: Mapping[str, tuple[str, tuple[str, ...], bool, bool]] = {
    "ix_artifact_retain_until": ("artifact", ("retain_until",), False, True),
    "ix_transcript_retain_until": ("transcript", ("retain_until",), False, True),
    "ix_dataset_item_retain_until": ("dataset_item", ("retain_until",), False, True),
    "ix_run_caller_retain_until": ("run", ("caller_retain_until",), False, True),
    "ix_snapshot_member_kind_def_id_def_version": (
        "snapshot_member",
        ("kind", "def_id", "def_version"),
        False,
        False,
    ),
    "ix_snapshot_profile_ref_profile_id_profile_version": (
        "snapshot_profile_ref",
        ("profile_id", "profile_version"),
        False,
        False,
    ),
    "ux_external_ref_index_run_key": (
        "external_ref_index",
        ("entity_kind", "key_sha256"),
        True,
        True,
    ),
    "ix_external_ref_index_run_id_entity_kind_key_sha256": (
        "external_ref_index",
        ("run_id", "entity_kind", "key_sha256"),
        False,
        False,
    ),
    "ix_dataset_member_item_id": ("dataset_member", ("item_id",), False, False),
    "ix_run_bundle_member_kind_record_id": (
        "run_bundle_member",
        ("kind", "record_id"),
        False,
        False,
    ),
    "ix_export_bundle_run_run_id": ("export_bundle_run", ("run_id",), False, False),
    "ix_export_bundle_item_item_id": ("export_bundle_item", ("item_id",), False, False),
    "ix_export_bundle_profile_profile_id": (
        "export_bundle_profile",
        ("profile_id",),
        False,
        False,
    ),
}

ALL_INDEX_NAMES = frozenset(NEW_INDEXES) | frozenset(OLD_INDEXES)

# The nine versioned tables, whose primary key (id, version) serves "latest version of an id".
VERSIONED = (
    "agent_target",
    "persona",
    "test_profile",
    "evaluator",
    "metric_definition",
    "metric_binding",
    "rubric",
    "pricing_catalog",
    "dataset",
)

# ------------------------------------------------------------------------- the database catalog


@dataclass(frozen=True)
class IndexInfo:
    name: str
    table: str
    columns: tuple[str, ...]  # key columns, in order
    unique: bool
    partial: bool
    descending: tuple[bool, ...]


def _sqlite_indexes(conn: sa.Connection) -> dict[str, IndexInfo]:
    found: dict[str, IndexInfo] = {}
    tables = [
        r[0]
        for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "AND name <> 'alembic_version'"
        )
    ]
    for table in tables:
        for _seq, name, unique, origin, partial in conn.exec_driver_sql(
            f'PRAGMA index_list("{table}")'
        ).all():
            if origin != "c":  # 'pk' and 'u' are the key and the UNIQUE constraints
                continue
            keys = [
                (r[2], bool(r[3]))
                for r in conn.exec_driver_sql(f'PRAGMA index_xinfo("{name}")').all()
                if r[5] == 1
            ]
            found[name] = IndexInfo(
                name,
                table,
                tuple(k[0] for k in keys),
                bool(unique),
                bool(partial),
                tuple(k[1] for k in keys),
            )
    return found


_PG_INDEXES = """
SELECT i.relname, t.relname, ix.indisunique, ix.indpred IS NOT NULL,
  (SELECT array_agg(a.attname::text ORDER BY k.ord)
     FROM unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord)
     JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE k.ord <= ix.indnkeyatts),
  (SELECT array_agg(((ix.indoption[k.ord - 1]::int & 1) = 1) ORDER BY k.ord)
     FROM unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) WHERE k.ord <= ix.indnkeyatts)
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class t ON t.oid = ix.indrelid
WHERE t.relnamespace = current_schema()::regnamespace
  AND t.relname <> 'alembic_version'
  AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = ix.indexrelid
                    AND c.contype IN ('p', 'u'))
"""


def _pg_indexes(conn: sa.Connection) -> dict[str, IndexInfo]:
    found: dict[str, IndexInfo] = {}
    for name, table, unique, partial, columns, desc in conn.exec_driver_sql(_PG_INDEXES).all():
        found[name] = IndexInfo(
            name, table, tuple(columns), bool(unique), bool(partial), tuple(bool(d) for d in desc)
        )
    return found


def db_indexes(engine: sa.Engine) -> dict[str, IndexInfo]:
    """Every index made by CREATE INDEX (key and UNIQUE-constraint indexes excluded)."""
    with engine.connect() as conn:
        found = _sqlite_indexes(conn) if engine.dialect.name == "sqlite" else _pg_indexes(conn)
        conn.rollback()
    return found


def metadata_indexes(metadata: sa.MetaData) -> dict[str, IndexInfo]:
    found: dict[str, IndexInfo] = {}
    for table in metadata.tables.values():
        for index in table.indexes:
            assert index.name is not None
            partial = any(
                index.dialect_options[d]["where"] is not None for d in ("sqlite", "postgresql")
            )
            descending = tuple(
                isinstance(e, sa.sql.elements.UnaryExpression)
                and e.modifier is sa.sql.operators.desc_op
                for e in index.expressions
            )
            found[index.name] = IndexInfo(
                index.name,
                table.name,
                tuple(c.name for c in index.columns),
                bool(index.unique),
                partial,
                descending,
            )
    return found


# ------------------------------------------------------------------------------ the plan readers

ANY_INDEX = "<any index>"
PRIMARY_KEY = "<primary key>"


@dataclass
class Plan:
    """What a plan did, reduced to the facts the tests assert (no plan text is pinned)."""

    indexes: set[str] = field(default_factory=set)  # named indexes used
    primary_key: bool = False  # the table's key b-tree was searched (SQLite)
    scans: set[str] = field(default_factory=set)  # tables read without an index search
    sort: bool = False  # a separate sort step
    text: str = ""


_SQLITE_USING = re.compile(r"USING (?:COVERING )?INDEX (\S+)")
_SQLITE_SCAN = re.compile(r"^SCAN (?:TABLE )?(\w+)")


def sqlite_plan(conn: sa.Connection, statement: str, parameters: Any = ()) -> Plan:
    rows = conn.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
    plan = Plan(text=" | ".join(str(r[3]) for r in rows))
    for row in rows:
        detail = str(row[3])
        used = _SQLITE_USING.search(detail)
        if used:
            plan.indexes.add(used.group(1))
        if "USING PRIMARY KEY" in detail:
            plan.primary_key = True
        scan = _SQLITE_SCAN.match(detail)
        if scan:
            plan.scans.add(scan.group(1))
        if "USE TEMP B-TREE FOR ORDER BY" in detail:
            plan.sort = True
    return plan


def _walk(node: Mapping[str, Any], plan: Plan) -> None:
    kind = node.get("Node Type", "")
    if "Index Name" in node:
        plan.indexes.add(str(node["Index Name"]))
    if kind == "Seq Scan":
        plan.scans.add(str(node.get("Relation Name", "")))
    if kind == "Sort":
        plan.sort = True
    for child in node.get("Plans", ()):
        _walk(child, plan)


def postgres_plan(
    conn: sa.Connection, statement: str, parameters: Any = (), *, ordered: bool = False
) -> Plan:
    """``EXPLAIN (FORMAT JSON)`` with sequential scans discouraged, read from the node tree. For an
    ``ordered`` statement bitmap scans are discouraged too: a bitmap scan returns rows unordered,
    so on a table with no statistics the planner may prefer it plus a sort, which says nothing
    about whether the index CAN deliver the order."""
    conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
    if ordered:
        conn.exec_driver_sql("SET LOCAL enable_bitmapscan = off")
    raw = conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + statement, parameters).scalar_one()
    tree = json.loads(raw) if isinstance(raw, str) else raw
    plan = Plan(text="")
    _walk(tree[0]["Plan"], plan)
    return plan


def explain(engine: sa.Engine, statement: Any, *, ordered: bool = False) -> Plan:
    """The plan of a Core statement as the package would send it (bind processors applied)."""
    captured: list[tuple[str, Any]] = []

    def grab(conn: Any, cursor: Any, sql: str, parameters: Any, context: Any, many: bool) -> None:
        captured.append((sql, parameters))

    with engine.connect() as conn:
        event.listen(conn, "before_cursor_execute", grab)
        conn.execute(statement).all()
        event.remove(conn, "before_cursor_execute", grab)
        conn.rollback()
    (sql, parameters), *_ = [c for c in captured if READ_OR_DELETE.match(c[0])]
    return plan_of(engine, sql, parameters, ordered=ordered)


def plan_of(engine: sa.Engine, sql: str, parameters: Any, *, ordered: bool = False) -> Plan:
    with engine.connect() as conn:
        plan = (
            sqlite_plan(conn, sql, parameters)
            if engine.dialect.name == "sqlite"
            else postgres_plan(conn, sql, parameters, ordered=ordered)
        )
        conn.rollback()
    return plan


# -------------------------------------------------------------------------- statement capture


@contextlib.contextmanager
def capture_statements(engine: sa.Engine) -> Iterator[list[tuple[str, Any]]]:
    """(SQL, parameters) of every single-row statement the engine executes."""
    items: list[tuple[str, Any]] = []

    def grab(conn: Any, cursor: Any, sql: str, parameters: Any, context: Any, many: bool) -> None:
        if not many:
            items.append((sql, parameters))

    event.listen(engine, "before_cursor_execute", grab)
    try:
        yield items
    finally:
        event.remove(engine, "before_cursor_execute", grab)


READ_OR_DELETE = re.compile(r"\s*(SELECT|DELETE)\b", re.IGNORECASE)
HAS_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)

# Tables that hold at most a handful of rows by construction (never a scan problem).
TINY_TABLES = frozenset({"tracer_meta", "alembic_version"})


def scans_of(
    engine: sa.Engine, statements: list[tuple[str, Any]], store_tables: set[str]
) -> list[tuple[str, set[str]]]:
    """The statements (with a WHERE clause) whose plan reads a store table without an index."""
    seen: set[str] = set()
    offences: list[tuple[str, set[str]]] = []
    for sql, parameters in statements:
        if not READ_OR_DELETE.match(sql) or not HAS_WHERE.search(sql) or sql in seen:
            continue
        seen.add(sql)
        plan = plan_of(engine, sql, parameters)
        bad = (plan.scans & store_tables) - TINY_TABLES
        if bad:
            offences.append((re.sub(r"\s+", " ", sql)[:160], bad))
    return offences


def key_prefix_covered(
    table: sa.Table, columns: tuple[str, ...], indexes: set[tuple[str, ...]]
) -> bool:
    """True when ``columns`` is a leading prefix of the primary key, a UNIQUE constraint or an
    index of ``table`` (``indexes`` holds the key column lists of the CREATE INDEX indexes)."""
    candidates: list[tuple[str, ...]] = [tuple(c.name for c in table.primary_key.columns)]
    candidates += [
        tuple(c.name for c in u.columns)
        for u in table.constraints
        if isinstance(u, sa.UniqueConstraint)
    ]
    candidates += list(indexes)
    return any(candidate[: len(columns)] == columns for candidate in candidates)


Builder = Callable[[sa.MetaData], sa.Select[Any]]
