# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers shared by the Phase 2 batch B tests (tables and codecs).

Contract: docs/phase2/api-surface-batch-b.md. This module is imported by the batch B test modules
(``from evaldb_support import ...``; the tests directory has no ``__init__.py``, so pytest puts it
on ``sys.path``; mypy cannot see sibling modules there, hence the ``type: ignore`` on those
imports). It holds no test.

Contents, in order: repository paths and fixtures; the machine reading of the ADR tables; the
rules that give every column its logical type and its nullability; the closed sets read from the
shipped schemas; a minimal-row factory for the table tests; a reference projector (the expected
value of every projected column, computed from the model without using the codecs); a test-only
mini repository that writes and reads whole entities and bundles through the codecs.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import re
import sys
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from tracer_evalschema import base as ev_base
from tracer_evalschema import digest as ev_digest
from tracer_evalschema import evaluator as ev_evaluator
from tracer_evalschema import ids as ev_ids
from tracer_evalschema import loader as ev_loader
from tracer_evalschema import registry as ev_registry
from tracer_evalschema import snapshot as ev_snapshot

# --------------------------------------------------------------------------------- paths


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
ADR_DIR = ROOT / "docs" / "adr"
ADR19 = ADR_DIR / "0019-relational-storage.md"
ADR20 = ADR_DIR / "0020-deletion-and-retention.md"
EVALSCHEMA = ROOT / "packages" / "evalschema"
FIXTURES = EVALSCHEMA / "tests" / "fixtures"
GOLDEN = FIXTURES / "golden"
FROZEN = FIXTURES / "frozen" / "0.0.1"
ADVERSARIAL = FIXTURES / "adversarial"
SCHEMAS = EVALSCHEMA / "src" / "tracer_evalschema" / "schemas"

SCHEMA_VERSION = "0.0.1"
INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)

# ------------------------------------------------------------------------ the 26 + 11 tables
# The entity -> table map is read from ADR 0019 Table 1 (see ``adr_table1``); these groupings are
# the facts of ADR 0009 Decision 5 and ADR 0019 Decisions 3 and 8, checked against the ADR by the
# tests.

VERSIONED_TABLES = (
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
RUN_OWNED_TABLES = (
    "artifact",
    "transcript",
    "turn",
    "exchange",
    "tool_call_event",
    "stage_span",
    "metric_result",
    "run_verdict",
    "usage_record",
    "cost_record",
    "cost_summary",
)
# run_id is storage-only (taken from the bundle) for these; usage_record has a native run_id
STORAGE_RUN_ID_TABLES = tuple(t for t in RUN_OWNED_TABLES if t != "usage_record")
SIDE_TABLES = (
    "snapshot_member",
    "snapshot_profile_ref",
    "external_ref_index",
    "dataset_member",
    "run_bundle_member",
    "run_bundle_withheld",
    "export_bundle_run",
    "export_bundle_item",
    "export_bundle_profile",
    "deletion_audit",
    "tracer_meta",
)
COMMON_COLUMNS = ("id", "schema_version", "created_at", "doc", "doc_sha256")
VERSIONED_COLUMNS = ("version", "content_digest_scheme", "content_digest_value")

# Table 5 / ADR 0019 Decision 6a: the six privacy columns of a marker
PRIVACY_COLUMNS = (
    "human_subject",
    "human_subject_reason",
    "consent_status",
    "retain_until",
    "delete_on_expiry",
    "policy_ref",
)
CALLER_PRIVACY_COLUMNS = tuple(f"caller_{c}" for c in PRIVACY_COLUMNS)

# run_bundle_member kind -> (entity table, RunBundle field, versioned?) in RunBundle field order
BUNDLE_KINDS: tuple[tuple[str, str, str, bool], ...] = (
    ("run", "run", "run", False),
    ("snapshot", "config_snapshot", "snapshot", False),
    ("pricing_catalog", "pricing_catalog", "pricing_catalogs", True),
    ("test_profile", "test_profile", "test_profiles", True),
    ("artifact", "artifact", "artifacts", False),
    ("transcript", "transcript", "transcripts", False),
    ("turn", "turn", "turns", False),
    ("exchange", "exchange", "exchanges", False),
    ("tool_call", "tool_call_event", "tool_calls", False),
    ("span", "stage_span", "spans", False),
    ("result", "metric_result", "results", False),
    ("verdict", "run_verdict", "verdict", False),
    ("usage_record", "usage_record", "usage_records", False),
    ("cost_record", "cost_record", "cost_records", False),
    ("cost_summary", "cost_summary", "cost_summaries", False),
)
BUNDLE_ENVELOPE_FIELDS = ("id", "schema_version", "created_at", "metadata", "metadata_flags")


# ------------------------------------------------------------------------------- ADR reading


def _cells(line: str) -> list[str]:
    parts = line.strip().strip("|").split("|")
    return [part.strip() for part in parts]


def adr_table(path: Path, heading_prefix: str) -> list[list[str]]:
    """The body rows (no header, no separator) of the first markdown table after the heading that
    starts with ``heading_prefix``."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading_prefix))
    rows: list[list[str]] = []
    seen_table = False
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            seen_table = True
            rows.append(_cells(line))
        elif seen_table:
            break
    return rows[2:]  # header and separator


def ticks(cell: str) -> list[str]:
    """Every backticked token of a cell, in order."""
    return re.findall(r"`([^`]*)`", cell)


def column_list(cell: str) -> tuple[str, ...]:
    """A cell that lists columns: the backticked tokens, or none when it starts with ``none``."""
    if cell.startswith("none"):
        return ()
    return tuple(ticks(cell))


@dataclass(frozen=True)
class EntityRow:
    entity: str
    table: str
    key: tuple[str, ...]
    projected: tuple[str, ...]
    side: tuple[str, ...]
    fks: tuple[tuple[str, str], ...]  # (column, ON DELETE action)
    unique: tuple[tuple[str, ...], ...]
    fk_cell: str


def _tuple_of(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.strip("() ").split(","))


def adr_table1() -> list[EntityRow]:
    result: list[EntityRow] = []
    for cells in adr_table(ADR19, "### Table 1"):
        entity, table, key, projected, side, fk = cells
        key_cols = _tuple_of(ticks(key)[0])
        fks = tuple(re.findall(r"`(\w+)` (CASCADE|RESTRICT)", fk))
        unique = tuple(_tuple_of(m) for m in re.findall(r"UNIQUE `(\([^`]*\))`", fk))
        result.append(
            EntityRow(
                entity=ticks(entity)[0] if "`" in entity else entity,
                table=ticks(table)[0],
                key=key_cols,
                projected=column_list(projected),
                side=column_list(side),
                fks=fks,
                unique=unique,
                fk_cell=fk,
            )
        )
    return result


@dataclass(frozen=True)
class SideRow:
    table: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    constraints: str
    holds: str


def adr_table2() -> list[SideRow]:
    result = []
    for cells in adr_table(ADR19, "### Table 2"):
        table, columns, key, constraints, holds = cells
        result.append(
            SideRow(
                table=ticks(table)[0],
                columns=tuple(ticks(columns)),
                key=_tuple_of(ticks(key)[0]),
                constraints=constraints,
                holds=holds,
            )
        )
    return result


def adr_table2_row(table: str) -> SideRow:
    return next(row for row in adr_table2() if row.table == table)


@dataclass(frozen=True)
class FkRow:
    child: str
    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]
    on_delete: str


def adr_fk_graph() -> list[FkRow]:
    """The 23 foreign keys of ADR 0020 Decision 3."""
    result = []
    lines = ADR20.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "| # | Child table |" in line)
    for line in lines[start + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = _cells(stripped)
        child = ticks(cells[1])[0]
        columns = tuple(ticks(cells[2]))
        parent_ticks = ticks(cells[3])
        parent = parent_ticks[0]
        parent_columns = tuple(parent_ticks[1:])
        result.append(FkRow(child, columns, parent, parent_columns, cells[4]))
    return result


def adr_table5() -> list[tuple[str, str, str]]:
    """(value object, classification, where) of the 70 rows of Table 5."""
    rows = []
    for cells in adr_table(ADR19, "### Table 5"):
        rows.append((ticks(cells[1])[0], cells[2], cells[3]))
    return rows


def adr_table3() -> list[tuple[str, str, str]]:
    """(logical type, Postgres cell, SQLite cell) of Table 3."""
    return [(ticks(c[0])[0], c[2], c[3]) for c in adr_table(ADR19, "### Table 3")]


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ---------------------------------------------------------------------- column type rules
# ADR 0019, the paragraph "Column types" under Table 1 and the last paragraph under Table 2, plus
# the readings recorded in api-surface-batch-b.md section 3 (turn_ref, singleton, nullability).

_BIGINT_COLUMNS = frozenset(
    {
        "version",
        "seq",
        "index",
        "start_ms",
        "byte_length",
        "required_total",
        "failed",
        "not_applicable",
        "errored",
        "ordinal",
        "singleton",
    }
)
_KEY_COLUMNS_ENDING_VERSION = frozenset({"schema_version", "evaldb_version"})
_INSTANT_COLUMNS = frozenset({"created_at", "started_at", "occurred_at", "deleted_at"})


def logical_type(table: str, column: str) -> str:
    """The column class (a name exported by ``tracer_evaldb``) of a column."""
    if column in ("doc", "counts"):
        return "DocCol"
    if column in ("doc_sha256", "sha256", "content_digest_value", "key_sha256"):
        return "Sha256Col"
    if column in _KEY_COLUMNS_ENDING_VERSION:
        return "KeyCol"
    if column == "passed":
        return "BoolCol" if table == "metric_result" else "BigIntCol"  # run_verdict: a count
    if column.endswith(("human_subject", "delete_on_expiry")):
        return "BoolCol"
    if column == "id" or column.endswith("_id") or column in ("audit_uuid", "turn_ref"):
        return "UuidCol"
    if column in _BIGINT_COLUMNS or column.endswith("_version"):
        return "BigIntCol"
    if column in _INSTANT_COLUMNS or column.endswith("retain_until"):
        return "InstantCol"
    if column in ("amount", "total"):
        return "DecimalCol"
    return "KeyCol"


def _privacy_nullable(prefix: str, *, marker_optional: bool) -> set[str]:
    names = {f"{prefix}{c}" for c in PRIVACY_COLUMNS[1:]}
    if marker_optional:
        names.add(f"{prefix}human_subject")
    return names


NULLABLE: dict[str, set[str]] = {
    "evaluator": {"test_profile_id", "test_profile_version"},
    "config_snapshot": {"pricing_catalog_id", "pricing_catalog_version"},
    "run": {"started_at"} | _privacy_nullable("caller_", marker_optional=True),
    "artifact": {"turn_ref"} | _privacy_nullable("", marker_optional=False),
    "transcript": {"detected_language"} | _privacy_nullable("", marker_optional=False),
    "turn": {"start_ms"},
    "metric_result": {"na_reason", "passed"},
    "usage_record": {"occurred_at"},
    "cost_record": {"amount", "basis", "currency", "na_reason"},
    "cost_summary": {"total", "currency"},
    "dataset_item": {"artifact_id", "transcript_id", "source_run_id"}
    | _privacy_nullable("", marker_optional=True),
    "run_bundle_member": {"record_version"},
}


def is_nullable(table: str, column: str) -> bool:
    return column in NULLABLE.get(table, set())


# ------------------------------------------------------------------------------- closed sets


def _enum(schema_file: str, *path: str | int) -> tuple[str, ...]:
    node: Any = json.loads((SCHEMAS / schema_file).read_text(encoding="utf-8"))
    for step in path:
        node = node[step]
    values = node["enum"] if isinstance(node, dict) else node
    return tuple(values)


def _prop(schema_file: str, name: str, *, optional: bool = False) -> tuple[str, ...]:
    if optional:
        return _enum(schema_file, "properties", name, "anyOf", 0)
    return _enum(schema_file, "properties", name)


def _closed_sets() -> dict[tuple[str, str], tuple[str, ...]]:
    sets: dict[tuple[str, str], tuple[str, ...]] = {}
    sets[("agent_target", "transport")] = tuple(
        _enum("AgentTarget.schema.json", "$defs", kind, "properties", "transport")[0]
        for kind in ("WebSocketEndpoint", "WebRtcEndpoint", "SipEndpoint")
    )
    sets[("metric_definition", "kind")] = _prop("MetricDefinition.schema.json", "kind")
    sets[("metric_definition", "value_type")] = _prop("MetricDefinition.schema.json", "value_type")
    sets[("run", "mode")] = _prop("Run.schema.json", "mode")
    sets[("run", "caller_origin")] = _prop("Run.schema.json", "caller_origin")
    sets[("run", "observed_tier")] = _prop("Run.schema.json", "observed_tier")
    sets[("transcript", "source")] = _prop("Transcript.schema.json", "source")
    sets[("tool_call_event", "source")] = _prop("ToolCallEvent.schema.json", "source")
    sets[("usage_record", "source")] = _prop("UsageRecord.schema.json", "source")
    sets[("usage_record", "domain")] = _prop("UsageRecord.schema.json", "domain")
    sets[("turn", "role")] = _prop("Turn.schema.json", "role")
    sets[("stage_span", "stage")] = _prop("StageSpan.schema.json", "stage")
    sets[("metric_result", "status")] = _prop("MetricResult.schema.json", "status")
    sets[("metric_result", "na_reason")] = _prop(
        "MetricResult.schema.json", "na_reason", optional=True
    )
    sets[("run_verdict", "status")] = _prop("RunVerdict.schema.json", "status")
    sets[("cost_record", "status")] = _prop("CostRecord.schema.json", "status")
    sets[("cost_record", "basis")] = _prop("CostRecord.schema.json", "basis", optional=True)
    sets[("cost_record", "na_reason")] = _prop("CostRecord.schema.json", "na_reason", optional=True)
    money = _enum("CostRecord.schema.json", "$defs", "Money", "properties", "currency")
    sets[("cost_record", "currency")] = money
    sets[("cost_summary", "currency")] = money
    sets[("cost_summary", "completeness")] = _prop("CostSummary.schema.json", "completeness")
    sets[("export_bundle", "purpose")] = _prop("ExportBundle.schema.json", "purpose")
    consent = _enum("Artifact.schema.json", "$defs", "ConsentRecord", "properties", "status")
    reason = _enum(
        "Artifact.schema.json",
        "$defs",
        "PrivacyMarker",
        "properties",
        "human_subject_reason",
        "anyOf",
        0,
    )
    for table, prefix in (
        ("artifact", ""),
        ("transcript", ""),
        ("dataset_item", ""),
        ("run", "caller_"),
    ):
        sets[(table, f"{prefix}consent_status")] = consent
        sets[(table, f"{prefix}human_subject_reason")] = reason
    scheme = _enum("MetricDefinition.schema.json", "$defs", "ContentDigest", "properties", "scheme")
    for table in (*VERSIONED_TABLES, "config_snapshot"):
        sets[(table, "content_digest_scheme")] = scheme
    # side tables: the sets of ADR 0019 Table 2 (read from the ADR cell) and the shipped enum
    sets[("run_bundle_withheld", "kind")] = _enum(
        "RunBundle.schema.json", "$defs", "WithheldRecord", "properties", "kind"
    )
    for table, column in (
        ("snapshot_member", "kind"),
        ("external_ref_index", "entity_kind"),
        ("run_bundle_member", "kind"),
    ):
        cell = adr_table2_row(table).constraints
        match = re.search(rf"`{column}` IN \(([^)]*)\)", cell)
        assert match is not None, (table, column)
        sets[(table, column)] = tuple(ticks(match.group(1)))
    # ADR 0020 Decision 6 and Decision 2
    sets[("deletion_audit", "reason")] = (
        "operator_request",
        "retention_policy",
        "data_subject_request",
    )
    sets[("deletion_audit", "root_kind")] = (
        "run",
        "project",
        "test_profile",
        "agent_target",
        "persona",
        "evaluator",
        "metric_definition",
        "metric_binding",
        "rubric",
        "pricing_catalog",
        "dataset",
        "dataset_item",
        "export_bundle",
        "config_snapshot",
    )
    return sets


_CLOSED_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}


def closed_sets() -> dict[tuple[str, str], tuple[str, ...]]:
    if not _CLOSED_CACHE:
        _CLOSED_CACHE.update(_closed_sets())
    return _CLOSED_CACHE


# For a closed-set test the other columns of the row must stay consistent with the table's
# cross-column CHECKs; these overrides say how.
CLOSED_CONTEXT: dict[tuple[str, str], dict[str, Any]] = {
    ("cost_record", "basis"): {
        "status": "available",
        "amount": Decimal("1"),
        "currency": "USD",
    },
    ("cost_record", "currency"): {"status": "available", "amount": Decimal("1")},
    ("cost_summary", "currency"): {"total": Decimal("1")},
}
for _table, _prefix in (
    ("artifact", ""),
    ("transcript", ""),
    ("dataset_item", ""),
    ("run", "caller_"),
):
    CLOSED_CONTEXT[(_table, f"{_prefix}consent_status")] = {
        f"{_prefix}human_subject": True,
        f"{_prefix}delete_on_expiry": False,
    }
    CLOSED_CONTEXT[(_table, f"{_prefix}human_subject_reason")] = {
        f"{_prefix}human_subject": True,
        f"{_prefix}consent_status": "obtained",
        f"{_prefix}delete_on_expiry": False,
    }

# the default value of a closed-set column when the test does not set it
CLOSED_DEFAULT: dict[tuple[str, str], str | None] = {
    ("cost_record", "status"): "not_available",
}


def not_members(values: Iterable[str]) -> list[str]:
    """Strings that are not in the set: empty, a trailing space, an upper-case and a prefix form
    of the first member, and a word no set has."""
    members = list(values)
    first = members[0]
    bad = ["", first + " ", first.upper() + "_X", first + "x", "zz_not_a_member"]
    return [b for b in bad if b not in members]


# ---------------------------------------------------------------------------- row factory


class Raw:
    """A column value that bypasses the column type (so a CHECK can be tested with a value the
    type would refuse)."""

    def __init__(self, value: Any) -> None:
        self.value = value


FIXED_INSTANT = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.UTC)


def new_uuid() -> uuid.UUID:
    return ev_ids.uuid7()


def _plain_type(value: object) -> sa.types.TypeEngine[Any]:
    """A type with no checking, so that a ``Raw`` value reaches the database as given."""
    if isinstance(value, str):
        return sa.Text()
    if isinstance(value, bytes):
        return sa.LargeBinary()
    if isinstance(value, bool):
        return sa.Boolean()
    if isinstance(value, int):
        return sa.BigInteger()
    raise TypeError(type(value).__name__)


class Rows:
    """Builds and inserts minimal valid rows (every NOT NULL column set, every CHECK satisfied)
    and the parent rows their foreign keys need. A fresh instance per test."""

    def __init__(self, metadata: sa.MetaData) -> None:
        self.metadata = metadata
        self._inserted: set[tuple[str, tuple[Any, ...]]] = set()

    def default(self, table: sa.Table, column: sa.Column[Any]) -> Any:
        name = column.name
        key = (table.name, name)
        if key in CLOSED_DEFAULT:
            return CLOSED_DEFAULT[key]
        if column.nullable:
            return None
        members = closed_sets().get(key)
        if members is not None:
            return members[0]
        kind = logical_type(table.name, name)
        if kind == "UuidCol":
            return new_uuid()
        if kind == "Sha256Col":
            return bytes(32)
        if kind == "InstantCol":
            return FIXED_INSTANT
        if kind == "DocCol":
            return "{}"
        if kind == "BigIntCol":
            return 1
        if kind == "BoolCol":
            return False
        if kind == "DecimalCol":
            return Decimal("0")
        if name == "schema_version" or name == "evaldb_version":
            return SCHEMA_VERSION
        return "x"

    def values(self, table_name: str, /, **overrides: Any) -> dict[str, Any]:
        table = self.metadata.tables[table_name]
        unknown = set(overrides) - {c.name for c in table.columns}
        assert not unknown, (table_name, unknown)
        return {
            col.name: overrides[col.name] if col.name in overrides else self.default(table, col)
            for col in table.columns
        }

    def _key_of(self, table: sa.Table, values: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(values[c.name] for c in table.primary_key.columns)

    def insert(self, conn: sa.Connection, table_name: str, /, **overrides: Any) -> dict[str, Any]:
        """Insert one row (and the parents it needs); returns the values."""
        values = self.values(table_name, **overrides)
        self.insert_exact(conn, table_name, values)
        return values

    def insert_exact(self, conn: sa.Connection, table_name: str, values: Mapping[str, Any]) -> None:
        """Insert exactly these values (every column), after the parent rows their foreign keys
        need."""
        table = self.metadata.tables[table_name]
        for constraint in table.foreign_key_constraints:
            parent_values = {fk.column.name: values[fk.parent.name] for fk in constraint.elements}
            if any(v is None for v in parent_values.values()):
                continue
            parent = constraint.referred_table
            parent_key = tuple(parent_values.get(c.name) for c in parent.primary_key.columns)
            if (parent.name, parent_key) in self._inserted:
                continue
            self.insert(conn, parent.name, **parent_values)
        self._execute(conn, table, values)
        self._inserted.add((table_name, self._key_of(table, values)))

    def _execute(self, conn: sa.Connection, table: sa.Table, values: Mapping[str, Any]) -> None:
        typed = {k: v for k, v in values.items() if not isinstance(v, Raw)}
        raw = {k: v.value for k, v in values.items() if isinstance(v, Raw)}
        statement = sa.insert(table).values(
            **typed, **{k: sa.literal(v, _plain_type(v)) for k, v in raw.items()}
        )
        conn.execute(statement)

    def new_run(self, conn: sa.Connection, **overrides: Any) -> uuid.UUID:
        values = self.insert(conn, "run", **overrides)
        run_id: uuid.UUID = values["id"]
        return run_id


# ---------------------------------------------------------------------- violation kinds


def violation_kind(exc: BaseException) -> str:
    """check | unique | foreign_key | not_null | other, for either backend. The caller passes a
    ``sqlalchemy.exc.DBAPIError``."""
    original = getattr(exc, "orig", exc)
    code: str | None = None
    if original.args and isinstance(original.args[0], dict):
        code = original.args[0].get("C")  # pg8000: the SQLSTATE
    text = str(original)
    if code == "23514" or "CHECK constraint failed" in text:
        return "check"
    if code == "23505" or "UNIQUE constraint failed" in text or "PRIMARY KEY" in text:
        return "unique"
    if code in ("23503", "23001") or "FOREIGN KEY constraint failed" in text:  # 23001: RESTRICT
        return "foreign_key"
    if code == "23502" or "NOT NULL constraint failed" in text:
        return "not_null"
    return "other"


# ----------------------------------------------------------------------- DDL text reading


def dialect_of(name: str) -> Any:
    """The SQLAlchemy dialect object of ``sqlite`` or ``postgresql``."""
    from sqlalchemy.dialects import postgresql, sqlite

    if name == "sqlite":
        return sqlite.dialect()  # type: ignore[no-untyped-call,unused-ignore]
    return postgresql.dialect()  # type: ignore[no-untyped-call,unused-ignore]


def ddl(table: sa.Table, dialect: str) -> str:
    from sqlalchemy.schema import CreateTable

    return str(CreateTable(table).compile(dialect=dialect_of(dialect)))


def in_sets(ddl_text: str, column: str) -> list[tuple[str, ...]]:
    """The string lists of every ``column IN ('a', 'b', ...)`` in a DDL text."""
    found = []
    for match in re.finditer(rf'"?\b{re.escape(column)}\b"?\s+IN\s*\(([^)]*)\)', ddl_text):
        items = re.findall(r"'((?:[^']|'')*)'", match.group(1))
        if items:
            found.append(tuple(item.replace("''", "'") for item in items))
    return found


# ------------------------------------------------------------------------------- fixtures


def entity_models() -> dict[str, type[BaseModel]]:
    return {cls.__name__: cls for cls in ev_registry.entity_classes().values()}


def golden_valid_files(entity: str) -> list[Path]:
    return sorted((GOLDEN / entity / "valid").glob("*.json"))


def all_golden_valid() -> list[tuple[str, Path]]:
    return [(e, p) for e in sorted(entity_models()) for p in golden_valid_files(e)]


def frozen_files() -> list[tuple[str, Path]]:
    pairs = []
    for path in sorted(FROZEN.glob("*.json")):
        if path.name == "digests.json":
            continue
        pairs.append((path.name.split(".")[0], path))
    return pairs


def load_entity(entity: str, text: str) -> Any:
    return ev_loader.load_model(entity_models()[entity], text)


def load_golden(entity: str, name: str) -> Any:
    return load_entity(entity, (GOLDEN / entity / "valid" / f"{name}.json").read_text("utf-8"))


def golden_doc(entity: str, name: str) -> dict[str, Any]:
    doc = json.loads((GOLDEN / entity / "valid" / f"{name}.json").read_text("utf-8"))
    assert isinstance(doc, dict)
    return doc


def redigest(entity: str, doc: dict[str, Any]) -> dict[str, Any]:
    """The document with its own ``content_digest`` recomputed (a versioned entity or snapshot
    edited by a test must stay valid). The Evaluator and the snapshot have their own digest
    domains (ADR 0006 Decision 5)."""
    if entity == "ConfigSnapshot":
        fresh = ev_snapshot.compute_snapshot_digest(doc)
    elif entity == "Evaluator":
        fresh = ev_digest.compute_digest(ev_evaluator.evaluator_digest_domain(doc))
    else:
        fresh = ev_digest.compute_digest(doc)
    return {**doc, "content_digest": fresh.model_dump(mode="json", exclude_none=True)}


def with_metadata(doc: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """The document with this ``metadata`` and the ``metadata_flags`` the model computes for it
    (the flags are part of the digest domain, so a digest recomputed over the document needs
    them)."""
    flags = ev_base.compute_metadata_flags(metadata)
    return {
        **doc,
        "metadata": metadata,
        "metadata_flags": [f.model_dump(mode="json") for f in flags],
    }


def verify_own_digest(model: Any) -> None:
    """Verify the entity's own ``content_digest`` over its own digest domain (the Evaluator and
    the snapshot have domains of their own, ADR 0006 Decision 5)."""
    dump = model.model_dump(mode="json")
    name = type(model).__name__
    if name == "ConfigSnapshot":
        subject: Any = ev_snapshot.snapshot_digest_domain(dump)
    elif name == "Evaluator":
        subject = ev_evaluator.evaluator_digest_domain(dump)
    else:
        subject = model
    ev_digest.verify_digest(subject, model.content_digest)


def load_support_b13() -> Any:
    """``tests._support_b13`` of the evalschema package (the T-DEG bundle builders)."""
    base = str(EVALSCHEMA)
    if base not in sys.path:
        sys.path.append(base)
    return importlib.import_module("tests._support_b13")


def tdeg_bundles() -> list[tuple[str, Any]]:
    b13 = load_support_b13()
    bundles = []
    for column in b13.COLUMNS:
        for origin in ("simulated", "human"):
            world = b13.cached_world(column, origin=origin)
            bundles.append((f"{column}-{origin}", b13.bundle_of(world)))
    return bundles


def expand_placeholders(text: str) -> str:
    load_support_b13()  # puts the evalschema package directory on sys.path
    support = importlib.import_module("tests._support_b12")
    expanded: str = support.expand(text)
    return expanded


# ------------------------------------------------------------------ reference projector


def doc_sha256_of(dump: Any) -> bytes:
    return bytes.fromhex(ev_digest.sha256_hex(ev_digest.jcs_bytes(dump)))


def _privacy_values(marker: Any, prefix: str) -> dict[str, Any]:
    names = [f"{prefix}{c}" for c in PRIVACY_COLUMNS]
    if marker is None:
        return dict.fromkeys(names)
    consent, retention = marker.consent, marker.retention
    return dict(
        zip(
            names,
            (
                marker.human_subject,
                marker.human_subject_reason,
                None if consent is None else consent.status,
                None if retention is None else retention.retain_until,
                None if retention is None else retention.delete_on_expiry,
                None if retention is None else retention.policy_ref,
            ),
            strict=True,
        )
    )


def _money(amount: Any) -> tuple[Decimal | None, str | None]:
    if amount is None:
        return None, None
    return Decimal(amount.amount), amount.currency


def _ref(ref: Any) -> tuple[uuid.UUID | None, int | None]:
    return (None, None) if ref is None else (ref.id.uuid, ref.version)


def expected_projection(table: str, model: Any, run_id: uuid.UUID | None = None) -> dict[str, Any]:
    """The expected value of every projected column (not the common columns) of ``table`` for
    ``model``, computed from the model fields alone."""
    m = model
    if table == "agent_target":
        return {"transport": m.endpoint.transport}
    if table == "persona":
        return {"language": m.language}
    if table in ("test_profile", "rubric", "pricing_catalog", "dataset"):
        return {}
    if table == "evaluator":
        tp_id, tp_version = _ref(m.test_profile)
        return {
            "persona_id": m.persona.id.uuid,
            "persona_version": m.persona.version,
            "test_profile_id": tp_id,
            "test_profile_version": tp_version,
        }
    if table == "metric_definition":
        return {"key": m.key, "kind": m.kind, "value_type": m.value_type}
    if table == "metric_binding":
        return {
            "evaluator_id": m.evaluator_id.uuid,
            "metric_id": m.metric.id.uuid,
            "metric_version": m.metric.version,
        }
    if table == "project":
        return {"name": m.name}
    if table == "config_snapshot":
        pc_id, pc_version = _ref(m.pricing_catalog)
        return {
            "evaluator_id": m.evaluator.id.uuid,
            "evaluator_version": m.evaluator.version,
            "rubric_id": m.rubric.id.uuid,
            "rubric_version": m.rubric.version,
            "pricing_catalog_id": pc_id,
            "pricing_catalog_version": pc_version,
        }
    if table == "run":
        values = {
            "project_id": m.project_id.uuid,
            "config_snapshot_id": m.config_snapshot_id.uuid,
            "agent_target_id": m.agent_target.id.uuid,
            "agent_target_version": m.agent_target.version,
            "mode": m.mode,
            "caller_origin": m.caller_origin,
            "observed_tier": m.observed_tier,
            "started_at": m.started_at,
        }
        marker = None if m.caller_external_id is None else m.caller_external_id.privacy
        return {**values, **_privacy_values(marker, "caller_")}
    if table == "artifact":
        return {
            "run_id": run_id,
            "sha256": bytes.fromhex(m.sha256),
            "turn_ref": None if m.turn_ref is None else m.turn_ref.uuid,
            **_privacy_values(m.privacy, ""),
        }
    if table == "transcript":
        return {
            "run_id": run_id,
            "source": m.source,
            "detected_language": m.detected_language,
            **_privacy_values(m.privacy, ""),
        }
    if table == "turn":
        return {
            "run_id": run_id,
            "transcript_id": m.transcript_id.uuid,
            "seq": m.seq,
            "role": m.role,
            "start_ms": m.start_ms,
        }
    if table == "exchange":
        return {"run_id": run_id, "index": m.index}
    if table == "tool_call_event":
        return {"run_id": run_id, "source": m.source}
    if table == "stage_span":
        return {"run_id": run_id, "stage": m.stage}
    if table == "metric_result":
        return {
            "run_id": run_id,
            "metric_id": m.metric.id.uuid,
            "metric_version": m.metric.version,
            "status": m.status,
            "na_reason": m.na_reason,
            "passed": m.passed,
        }
    if table == "run_verdict":
        return {
            "run_id": run_id,
            "status": m.status,
            "required_total": m.required_total,
            "passed": m.passed,
            "failed": m.failed,
            "not_applicable": m.not_applicable,
            "errored": m.errored,
        }
    if table == "usage_record":
        return {
            "run_id": m.run_id.uuid,
            "domain": m.domain,
            "source": m.source,
            "occurred_at": m.occurred_at,
        }
    if table == "cost_record":
        amount, currency = _money(m.amount)
        return {
            "run_id": run_id,
            "status": m.status,
            "basis": m.basis,
            "amount": amount,
            "currency": currency,
            "na_reason": m.na_reason,
        }
    if table == "cost_summary":
        total, currency = _money(m.total)
        return {
            "run_id": run_id,
            "completeness": m.completeness,
            "total": total,
            "currency": currency,
        }
    if table == "dataset_item":
        return {
            "artifact_id": None if m.artifact_id is None else m.artifact_id.uuid,
            "transcript_id": None if m.transcript_id is None else m.transcript_id.uuid,
            "source_run_id": None if m.source_run_id is None else m.source_run_id.uuid,
            "language": m.language,
            **_privacy_values(m.privacy, ""),
        }
    if table == "run_bundle":
        return {"run_id": m.run.id.uuid}
    if table == "export_bundle":
        return {"purpose": m.purpose, "byte_length": len(m.model_dump_json().encode("utf-8"))}
    raise AssertionError(table)


def doc_text_and_dump(table: str, model: Any) -> tuple[str, Any]:
    """The expected ``doc`` text and the JSON-mode value its ``doc_sha256`` covers."""
    if table == "run_bundle":
        include = set(BUNDLE_ENVELOPE_FIELDS)
        return model.model_dump_json(include=include), model.model_dump(
            mode="json", include=include
        )
    return model.model_dump_json(), model.model_dump(mode="json")


def expected_row(table: str, model: Any, run_id: uuid.UUID | None = None) -> dict[str, Any]:
    """The full expected row of an entity table."""
    text, dump = doc_text_and_dump(table, model)
    row: dict[str, Any] = {
        "id": model.id.uuid,
        "schema_version": model.schema_version,
        "created_at": model.created_at,
        "doc": text,
        "doc_sha256": doc_sha256_of(dump),
    }
    if table in VERSIONED_TABLES:
        row["version"] = model.version
        row["content_digest_scheme"] = model.content_digest.scheme
        row["content_digest_value"] = bytes.fromhex(model.content_digest.value)
    if table == "config_snapshot":
        row["content_digest_scheme"] = model.content_digest.scheme
        row["content_digest_value"] = bytes.fromhex(model.content_digest.value)
    row.update(expected_projection(table, model, run_id))
    return row


def external_ref_key(system: str, kind: str, value: str) -> bytes:
    return bytes.fromhex(ev_digest.sha256_hex(ev_digest.jcs_bytes([system, kind, value])))


def expected_external_refs(
    table: str, model: Any, run_id: uuid.UUID | None
) -> list[tuple[str, uuid.UUID, bytes, uuid.UUID]]:
    """(entity_kind, entity_id, key_sha256, run_id) rows, one per distinct reference."""
    refs: list[tuple[str, Any]] = []
    if table == "run":
        refs = [("run", r) for r in model.external_refs]
        owner = model.id.uuid
    elif table == "stage_span":
        refs = [("span", model.span_ref)]
        owner = run_id
    elif table == "transcript":
        refs = [] if model.provider_call_ref is None else [("transcript", model.provider_call_ref)]
        owner = run_id
    elif table == "usage_record":
        refs = (
            [] if model.provider_call_ref is None else [("usage_record", model.provider_call_ref)]
        )
        owner = model.run_id.uuid
    else:
        return []
    seen: set[bytes] = set()
    rows = []
    for kind, ref in refs:
        key = external_ref_key(ref.system, ref.kind, ref.value)
        if key in seen:
            continue
        seen.add(key)
        rows.append((kind, model.id.uuid, key, owner))
    return rows


def _member(kind: str, ordinal: int, entity: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "ordinal": ordinal,
        "def_id": entity.id.uuid,
        "def_version": entity.version,
    }


def expected_side_rows(
    table: str, model: Any, run_id: uuid.UUID | None = None
) -> dict[str, list[dict[str, Any]]]:
    """The side-table rows an entity fills, by side table (a key for every side table the entity
    kind can fill, an empty list when there is nothing), computed from the model alone."""
    if table == "config_snapshot":
        sid = model.id.uuid
        members = [
            _member("evaluator", 0, model.evaluator),
            _member("persona", 0, model.persona),
            _member("rubric", 0, model.rubric),
            *[_member("metric_definition", i, d) for i, d in enumerate(model.metric_definitions)],
            *[_member("metric_binding", i, b) for i, b in enumerate(model.metric_bindings)],
        ]
        profile = model.evaluator.test_profile
        return {
            "snapshot_member": [{"snapshot_id": sid, **m} for m in members],
            "snapshot_profile_ref": (
                []
                if profile is None
                else [
                    {
                        "snapshot_id": sid,
                        "profile_id": profile.id.uuid,
                        "profile_version": profile.version,
                    }
                ]
            ),
        }
    if table == "dataset":
        return {
            "dataset_member": [
                {
                    "dataset_id": model.id.uuid,
                    "dataset_version": model.version,
                    "ordinal": i,
                    "item_id": item.uuid,
                }
                for i, item in enumerate(model.item_ids)
            ]
        }
    if table in ("run", "stage_span", "transcript", "usage_record"):
        return {
            "external_ref_index": [
                {"entity_kind": k, "entity_id": e, "key_sha256": key, "run_id": r}
                for k, e, key, r in expected_external_refs(table, model, run_id)
            ]
        }
    if table == "run_bundle":
        bid = model.id.uuid
        members = []
        for kind, _table, field_name, versioned in BUNDLE_KINDS:
            value = getattr(model, field_name)
            records = [] if value is None else value if isinstance(value, list) else [value]
            for ordinal, record in enumerate(records):
                members.append(
                    {
                        "bundle_id": bid,
                        "kind": kind,
                        "ordinal": ordinal,
                        "record_id": record.id.uuid,
                        "record_version": record.version if versioned else None,
                    }
                )
        withheld = [
            {"bundle_id": bid, "ordinal": i, "kind": w.kind, "record_id": w.record_id.uuid}
            for i, w in enumerate(model.withheld)
        ]
        return {"run_bundle_member": members, "run_bundle_withheld": withheld}
    if table == "export_bundle":
        eid = model.id.uuid
        runs = {rb.run.id.uuid for rb in model.run_bundles}
        items = {item.id.uuid for item in model.dataset_items}
        profiles = {p.id.uuid for rb in model.run_bundles for p in rb.test_profiles}
        profiles |= {
            sp.aad_fields.subject_id.uuid
            for sp in model.sealed_payloads
            if sp.aad_fields.payload_type == "test_profile"
        }
        return {
            "export_bundle_run": [{"export_id": eid, "run_id": r} for r in runs],
            "export_bundle_item": [{"export_id": eid, "item_id": i} for i in items],
            "export_bundle_profile": [{"export_id": eid, "profile_id": p} for p in profiles],
        }
    return {}


def strict_equal(left: Any, right: Any) -> bool:
    """Equal values of the same type (so 1 is not True and Decimal('1') is not Decimal('1.0')
    unless the digits agree); datetimes compare as instants."""
    if isinstance(left, dt.datetime) and isinstance(right, dt.datetime):
        return left == right and left.tzinfo is not None and right.tzinfo is not None
    if isinstance(left, Decimal) and isinstance(right, Decimal):
        return format(left, "f") == format(right, "f")
    return type(left) is type(right) and left == right


def rows_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return set(left) == set(right) and all(strict_equal(left[k], right[k]) for k in left)


def row_set_equal(left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]]) -> bool:
    """The same rows, in any order, each equal (strictly)."""
    a, b = list(left), list(right)
    if len(a) != len(b):
        return False
    remaining = list(b)
    for row in a:
        match = next((r for r in remaining if rows_equal(row, r)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


# ------------------------------------------------------------ test-only mini repository


@dataclass
class Written:
    """What ``write_entity`` inserted."""

    table: str
    encoded: Any
    run_id: uuid.UUID | None


@dataclass
class MiniRepo:
    """Writes and reads whole entities through the codecs, with the parent rows foreign keys need.
    Not the repository of batch C: no validation, no dedup, one entity at a time."""

    codecs: Any
    metadata: sa.MetaData
    entity_table: dict[str, str]
    rows: Rows = field(init=False)

    def __post_init__(self) -> None:
        self.rows = Rows(self.metadata)

    def encode(self, model: Any, run_id: uuid.UUID | None = None) -> Any:
        table = self.entity_table[type(model).__name__]
        function: Callable[..., Any] = getattr(self.codecs, f"encode_{table}")
        if table in STORAGE_RUN_ID_TABLES:
            assert run_id is not None, table
            return function(model, run_id=run_id)
        return function(model)

    def write(self, conn: sa.Connection, model: Any, *, run_id: uuid.UUID | None = None) -> Written:
        table = self.entity_table[type(model).__name__]
        if table in STORAGE_RUN_ID_TABLES:
            if run_id is None:
                run_id = self.rows.new_run(conn)
            else:
                self._ensure(conn, "run", run_id)
        if table == "usage_record":
            run_id = model.run_id.uuid
            self._ensure(conn, "run", run_id)
        if table == "run":
            self._ensure(conn, "project", model.project_id.uuid)
            self._ensure(conn, "config_snapshot", model.config_snapshot_id.uuid)
        encoded = self.encode(model, run_id)
        self._insert_encoded(conn, encoded)
        return Written(table, encoded, run_id)

    def _ensure(self, conn: sa.Connection, table: str, key: uuid.UUID) -> None:
        if (table, (key,)) in self.rows._inserted:
            return
        self.rows.insert(conn, table, id=key)

    def _insert_encoded(self, conn: sa.Connection, encoded: Any) -> None:
        conn.execute(sa.insert(self.metadata.tables[encoded.table]), dict(encoded.row))
        self.rows._inserted.add((encoded.table, (encoded.row["id"],)))
        for side, side_rows in encoded.side_rows.items():
            if side_rows:
                conn.execute(sa.insert(self.metadata.tables[side]), [dict(r) for r in side_rows])

    def read_row(self, conn: sa.Connection, table: str, key: Mapping[str, Any]) -> dict[str, Any]:
        t = self.metadata.tables[table]
        clause = sa.and_(*[t.c[name] == value for name, value in key.items()])
        found = conn.execute(sa.select(t).where(clause)).mappings().one()
        return dict(found)

    def read(self, conn: sa.Connection, table: str, key: Mapping[str, Any]) -> Any:
        decode: Callable[..., Any] = getattr(self.codecs, f"decode_{table}")
        return decode(self.read_row(conn, table, key))

    # ---- bundles

    def write_bundle(self, conn: sa.Connection, bundle: Any) -> Written:
        run = bundle.run
        self._ensure(conn, "project", run.project_id.uuid)
        run_uuid: uuid.UUID = run.id.uuid
        self.write(conn, bundle.snapshot)
        self.write(conn, run)
        for catalog in bundle.pricing_catalogs:
            self.write(conn, catalog)
        for profile in bundle.test_profiles:
            self.write(conn, profile)
        for name in (
            "artifacts",
            "transcripts",
            "turns",
            "exchanges",
            "tool_calls",
            "spans",
            "results",
            "cost_records",
            "cost_summaries",
        ):
            for record in getattr(bundle, name):
                self.write(conn, record, run_id=run_uuid)
        if bundle.verdict is not None:
            self.write(conn, bundle.verdict, run_id=run_uuid)
        for usage in bundle.usage_records:
            self.write(conn, usage)
        encoded = self.encode(bundle)
        self._insert_encoded(conn, encoded)
        return Written("run_bundle", encoded, run_uuid)

    def read_bundle(self, conn: sa.Connection, bundle_id: uuid.UUID) -> Any:
        row = self.read_row(conn, "run_bundle", {"id": bundle_id})
        member_t = self.metadata.tables["run_bundle_member"]
        withheld_t = self.metadata.tables["run_bundle_withheld"]
        members = [
            dict(r)
            for r in conn.execute(
                sa.select(member_t).where(member_t.c.bundle_id == bundle_id)
            ).mappings()
        ]
        withheld = [
            dict(r)
            for r in conn.execute(
                sa.select(withheld_t).where(withheld_t.c.bundle_id == bundle_id)
            ).mappings()
        ]
        tables = {kind: table for kind, table, _field, _versioned in BUNDLE_KINDS}

        def resolve(kind: str, record_id: uuid.UUID, record_version: int | None) -> Any:
            key: dict[str, Any] = {"id": record_id}
            if record_version is not None:
                key["version"] = record_version
            return self.read(conn, tables[kind], key)

        members.reverse()  # the decoder must not rely on row order
        withheld.reverse()
        return self.codecs.decode_run_bundle(
            row, members=members, withheld=withheld, resolve=resolve
        )


def table_rows(conn: sa.Connection, metadata: sa.MetaData, name: str) -> list[dict[str, Any]]:
    table = metadata.tables[name]
    return [dict(r) for r in conn.execute(sa.select(table)).mappings()]


def sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(repr(row[k]) for k in sorted(row))


# --------------------------------------------------------------------------------- fixtures

_STATE = {"pg_ready": False}


@dataclass
class Db:
    """A store (one backend) with the whole schema created from ``tracer_evaldb.metadata``."""

    store: Any
    evaldb: ModuleType
    engine: sa.Engine
    backend: str
    metadata: sa.MetaData
    rows: Rows

    def begin(self) -> Any:
        return self.engine.begin()

    def repo(self, codecs: Any) -> MiniRepo:
        tables = importlib.import_module("tracer_evaldb.tables")
        entity_table = {name: table.name for name, table in tables.ENTITY_TABLES.items()}
        return MiniRepo(codecs, self.metadata, entity_table)


def _drop_pg_schema() -> None:
    url = os.environ.get("TRACER_TEST_POSTGRES_URL")
    if url and _STATE["pg_ready"]:
        evaldb = importlib.import_module("tracer_evaldb")
        engine = evaldb.create_store_engine(url)
        try:
            evaldb.metadata.drop_all(engine)
        finally:
            engine.dispose()


@pytest.fixture
def db(store: Any, evaldb: ModuleType, request: pytest.FixtureRequest) -> Db:
    """The ``store`` fixture of batch A with every table of ``tracer_evaldb.metadata`` created.
    SQLite: a new file per test. Postgres: one schema per session (dropped when the session
    ends), emptied before each test."""
    metadata: sa.MetaData = evaldb.metadata
    if store.backend == "postgres":
        if not _STATE["pg_ready"]:
            metadata.drop_all(store.engine)
            metadata.create_all(store.engine)
            _STATE["pg_ready"] = True
            request.config.add_cleanup(_drop_pg_schema)
        else:
            names = ", ".join(f'"{name}"' for name in sorted(metadata.tables))
            with store.engine.begin() as conn:
                conn.exec_driver_sql("TRUNCATE " + names + " CASCADE")
    else:
        metadata.create_all(store.engine)
    return Db(store, evaldb, store.engine, store.backend, metadata, Rows(metadata))


@pytest.fixture
def sqlite_db(sqlite_store: Any, evaldb: ModuleType) -> Db:
    metadata: sa.MetaData = evaldb.metadata
    metadata.create_all(sqlite_store.engine)
    return Db(sqlite_store, evaldb, sqlite_store.engine, "sqlite", metadata, Rows(metadata))


@pytest.fixture(scope="session")
def codecs(evaldb: ModuleType) -> ModuleType:
    return importlib.import_module("tracer_evaldb.codecs")


@pytest.fixture(scope="session")
def tables(evaldb: ModuleType) -> ModuleType:
    return importlib.import_module("tracer_evaldb.tables")
