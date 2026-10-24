# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The 37 tables of the store: 26 entity tables (ADR 0019 Table 1) and 11 side tables (Table 2).

Importing this module fills ``tracer_evaldb.metadata``. Column classes follow the rules under
Table 1 and Table 2 of the ADR; the closed sets are read from the JSON Schema files shipped with
``tracer_evalschema`` so that a CHECK can never drift from the enum it mirrors. The foreign keys
are exactly those of ADR 0020 Decision 3 and every one is ``CASCADE`` or ``RESTRICT``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources
from types import MappingProxyType
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import SchemaItem
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import TypeEngine

from . import columns
from .schema import metadata

__all__ = ["ENTITY_TABLES", "SIDE_TABLES"]

# ----------------------------------------------------------------------------- column classes

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
_INSTANT_COLUMNS = frozenset({"created_at", "started_at", "occurred_at", "deleted_at"})
_SHA256_COLUMNS = frozenset({"doc_sha256", "sha256", "content_digest_value", "key_sha256"})


def _column_class(table: str, name: str) -> type[TypeEngine[Any]]:
    if name in ("doc", "counts"):
        return columns.DocCol
    if name in _SHA256_COLUMNS:
        return columns.Sha256Col
    if name in ("schema_version", "evaldb_version"):
        return columns.KeyCol
    if name == "passed":  # a boolean on a result, a count on a verdict
        return columns.BoolCol if table == "metric_result" else columns.BigIntCol
    if name.endswith(("human_subject", "delete_on_expiry")):
        return columns.BoolCol
    # turn_ref holds a Turn ID (api-surface-batch-b A3)
    if name == "id" or name.endswith("_id") or name in ("audit_uuid", "turn_ref"):
        return columns.UuidCol
    if name in _BIGINT_COLUMNS or name.endswith("_version"):
        return columns.BigIntCol
    if name in _INSTANT_COLUMNS or name.endswith("retain_until"):
        return columns.InstantCol
    if name in ("amount", "total"):
        return columns.DecimalCol
    return columns.KeyCol


# ------------------------------------------------------------------------------ nullability

PRIVACY = (
    "human_subject",
    "human_subject_reason",
    "consent_status",
    "retain_until",
    "delete_on_expiry",
    "policy_ref",
)
_CALLER_PRIVACY = tuple(f"caller_{name}" for name in PRIVACY)

# Every column is NOT NULL except these: the projections of optional model fields. The marker of
# an artifact or transcript is required, so its human_subject is NOT NULL there (ADR 0019).
_NULLABLE: Mapping[str, frozenset[str]] = {
    "evaluator": frozenset({"test_profile_id", "test_profile_version"}),
    "config_snapshot": frozenset({"pricing_catalog_id", "pricing_catalog_version"}),
    "run": frozenset({"started_at", *_CALLER_PRIVACY}),
    "artifact": frozenset({"turn_ref", *PRIVACY[1:]}),
    "transcript": frozenset({"detected_language", *PRIVACY[1:]}),
    "turn": frozenset({"start_ms"}),
    "metric_result": frozenset({"na_reason", "passed"}),
    "usage_record": frozenset({"occurred_at"}),
    "cost_record": frozenset({"amount", "basis", "currency", "na_reason"}),
    "cost_summary": frozenset({"total", "currency"}),
    "dataset_item": frozenset({"artifact_id", "transcript_id", "source_run_id", *PRIVACY}),
    "run_bundle_member": frozenset({"record_version"}),
}


def _make_columns(table: str, names: tuple[str, ...]) -> list[sa.Column[Any]]:
    nullable = _NULLABLE.get(table, frozenset())
    result: list[sa.Column[Any]] = []
    for name in names:
        kind = _column_class(table, name)
        extra: dict[str, Any] = {"autoincrement": False} if kind is columns.BigIntCol else {}
        result.append(sa.Column(name, kind(), nullable=name in nullable, **extra))
    return result


# --------------------------------------------------------------------------------- closed sets


def _enum_of(node: Any) -> tuple[str, ...]:
    """The ``enum`` list of a schema node, or of the first member of its ``anyOf``."""
    if "anyOf" in node:
        node = node["anyOf"][0]
    return tuple(node["enum"])


def _schema(file: str) -> Any:
    path = resources.files("tracer_evalschema") / "schemas" / f"{file}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _property_sets() -> dict[tuple[str, str], tuple[str, ...]]:
    sets: dict[tuple[str, str], tuple[str, ...]] = {}

    def prop(table: str, file: str, name: str, column: str | None = None) -> None:
        sets[table, column or name] = _enum_of(_schema(file)["properties"][name])

    transports = (
        _enum_of(_schema("AgentTarget")["$defs"][endpoint]["properties"]["transport"])[0]
        for endpoint in ("WebSocketEndpoint", "WebRtcEndpoint", "SipEndpoint")
    )
    sets["agent_target", "transport"] = tuple(transports)
    prop("metric_definition", "MetricDefinition", "kind")
    prop("metric_definition", "MetricDefinition", "value_type")
    for name in ("mode", "caller_origin", "observed_tier"):
        prop("run", "Run", name)
    prop("transcript", "Transcript", "source")
    prop("tool_call_event", "ToolCallEvent", "source")
    prop("usage_record", "UsageRecord", "source")
    prop("usage_record", "UsageRecord", "domain")
    prop("turn", "Turn", "role")
    prop("stage_span", "StageSpan", "stage")
    prop("metric_result", "MetricResult", "status")
    prop("metric_result", "MetricResult", "na_reason")
    prop("run_verdict", "RunVerdict", "status")
    for name in ("status", "basis", "na_reason"):
        prop("cost_record", "CostRecord", name)
    currency = _enum_of(_schema("CostRecord")["$defs"]["Money"]["properties"]["currency"])
    sets["cost_record", "currency"] = currency
    sets["cost_summary", "currency"] = currency
    prop("cost_summary", "CostSummary", "completeness")
    prop("export_bundle", "ExportBundle", "purpose")
    artifact = _schema("Artifact")["$defs"]
    consent = _enum_of(artifact["ConsentRecord"]["properties"]["status"])
    reason = _enum_of(artifact["PrivacyMarker"]["properties"]["human_subject_reason"])
    for table, prefix in (
        ("artifact", ""),
        ("transcript", ""),
        ("dataset_item", ""),
        ("run", "caller_"),
    ):
        sets[table, f"{prefix}consent_status"] = consent
        sets[table, f"{prefix}human_subject_reason"] = reason
    scheme = _enum_of(_schema("Persona")["$defs"]["ContentDigest"]["properties"]["scheme"])
    for table in (*(t for _, t in _VERSIONED_NAMES), "config_snapshot"):
        sets[table, "content_digest_scheme"] = scheme
    return sets


# -------------------------------------------------------------------------------------- checks


class _PerDialect(ColumnElement[bool]):
    """A boolean SQL fragment that is written once for SQLite and once for Postgres."""

    inherit_cache = False
    type = sa.Boolean()
    _traverse_internals: list[Any] = []  # noqa: RUF012

    def __init__(self, sqlite: str, postgresql: str) -> None:
        self.sqlite = sqlite
        self.postgresql = postgresql


@compiles(_PerDialect, "sqlite")
def _per_dialect_sqlite(element: _PerDialect, compiler: SQLCompiler, **kw: Any) -> str:
    return element.sqlite


@compiles(_PerDialect, "postgresql")
def _per_dialect_postgresql(element: _PerDialect, compiler: SQLCompiler, **kw: Any) -> str:
    return element.postgresql


def _col(name: str) -> ColumnElement[Any]:
    return sa.column(name)


def _check(expression: ColumnElement[bool], name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=name)


def _closed_set(column: str, members: tuple[str, ...]) -> sa.CheckConstraint:
    return _check(_col(column).in_(members), f"{column}_closed")


def _non_negative(column: str) -> sa.CheckConstraint:
    # Decimals are text on SQLite, where only the sign of the text can be tested.
    expression = _PerDialect(f"{column} NOT GLOB '-*'", f"{column} >= 0")
    return _check(expression, f"{column}_non_negative")


def _both_or_neither(first: str, second: str, name: str) -> sa.CheckConstraint:
    a, b = _col(first), _col(second)
    return _check(
        sa.or_(sa.and_(a.is_(None), b.is_(None)), sa.and_(a.is_not(None), b.is_not(None))), name
    )


def _privacy_check(prefix: str) -> sa.CheckConstraint:
    """ADR 0019 Decision 6a: the presence rule of the privacy marker, on its six columns."""
    subject, *rest = (_col(f"{prefix}{name}") for name in PRIVACY)
    consent, delete_on_expiry = rest[1], rest[3]
    all_null = sa.and_(*(column.is_(None) for column in rest))
    return _check(
        sa.or_(
            sa.and_(subject.is_(None), all_null),
            sa.and_(subject.is_(True), consent.is_not(None), delete_on_expiry.is_not(None)),
            sa.and_(subject.is_(False), all_null),
        ),
        f"{prefix}privacy_projection",
    )


# --------------------------------------------------------------------------- entity tables

# (entity class name, table name); the nine versioned entities of ADR 0009 Decision 5 first.
_VERSIONED_NAMES = (
    ("AgentTarget", "agent_target"),
    ("Persona", "persona"),
    ("TestProfile", "test_profile"),
    ("Evaluator", "evaluator"),
    ("MetricDefinition", "metric_definition"),
    ("MetricBinding", "metric_binding"),
    ("Rubric", "rubric"),
    ("PricingCatalog", "pricing_catalog"),
    ("Dataset", "dataset"),
)
_VERSIONED = frozenset(table for _, table in _VERSIONED_NAMES)
_DIGEST_COLUMNS = ("content_digest_scheme", "content_digest_value")

# table -> (entity class name, projected columns); Table 1 of ADR 0019
_ENTITIES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "agent_target": ("AgentTarget", ("transport",)),
    "persona": ("Persona", ("language",)),
    "test_profile": ("TestProfile", ()),
    "evaluator": (
        "Evaluator",
        ("persona_id", "persona_version", "test_profile_id", "test_profile_version"),
    ),
    "metric_definition": ("MetricDefinition", ("key", "kind", "value_type")),
    "metric_binding": ("MetricBinding", ("evaluator_id", "metric_id", "metric_version")),
    "rubric": ("Rubric", ()),
    "pricing_catalog": ("PricingCatalog", ()),
    "dataset": ("Dataset", ()),
    "project": ("Project", ("name",)),
    "config_snapshot": (
        "ConfigSnapshot",
        (
            *_DIGEST_COLUMNS,
            "evaluator_id",
            "evaluator_version",
            "rubric_id",
            "rubric_version",
            "pricing_catalog_id",
            "pricing_catalog_version",
        ),
    ),
    "run": (
        "Run",
        (
            "project_id",
            "config_snapshot_id",
            "agent_target_id",
            "agent_target_version",
            "mode",
            "caller_origin",
            "observed_tier",
            "started_at",
            *_CALLER_PRIVACY,
        ),
    ),
    "artifact": ("Artifact", ("run_id", "sha256", "turn_ref", *PRIVACY)),
    "transcript": ("Transcript", ("run_id", "source", "detected_language", *PRIVACY)),
    "turn": ("Turn", ("run_id", "transcript_id", "seq", "role", "start_ms")),
    "exchange": ("Exchange", ("run_id", "index")),
    "tool_call_event": ("ToolCallEvent", ("run_id", "source")),
    "stage_span": ("StageSpan", ("run_id", "stage")),
    "metric_result": (
        "MetricResult",
        ("run_id", "metric_id", "metric_version", "status", "na_reason", "passed"),
    ),
    "run_verdict": (
        "RunVerdict",
        ("run_id", "status", "required_total", "passed", "failed", "not_applicable", "errored"),
    ),
    "usage_record": ("UsageRecord", ("run_id", "domain", "source", "occurred_at")),
    "cost_record": (
        "CostRecord",
        ("run_id", "status", "basis", "amount", "currency", "na_reason"),
    ),
    "cost_summary": ("CostSummary", ("run_id", "completeness", "total", "currency")),
    "dataset_item": (
        "DatasetItem",
        ("artifact_id", "transcript_id", "source_run_id", "language", *PRIVACY),
    ),
    "run_bundle": ("RunBundle", ("run_id",)),
    "export_bundle": ("ExportBundle", ("purpose", "byte_length")),
}

_SCHEMA_VERSION = "0.0.1"
_CLOSED_SETS = _property_sets()

# the tables whose run_id is a cascading foreign key to run.id (ADR 0020 Decision 3, rows 6 to 17)
_RUN_OWNED = frozenset(
    {
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
        "run_bundle",
    }
)


def _fk(
    child: str, names: list[str], parent: str, parent_names: list[str], ondelete: str = "CASCADE"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        names,
        [f"{parent}.{name}" for name in parent_names],
        ondelete=ondelete,
        name=f"fk_{child}_{'_'.join(names)}",
    )


def _table_specific(table: str) -> list[SchemaItem]:
    """The constraints that belong to one entity table and to no other."""
    if table == "run":
        return [
            _fk("run", ["project_id"], "project", ["id"]),
            _fk("run", ["config_snapshot_id"], "config_snapshot", ["id"], "RESTRICT"),
            _privacy_check("caller_"),
        ]
    if table in ("artifact", "transcript", "dataset_item"):
        return [_privacy_check("")]
    if table == "turn":
        return [sa.UniqueConstraint("transcript_id", "seq", name="uq_turn_transcript_seq")]
    if table == "metric_result":
        status, passed = _col("status"), _col("passed")
        return [_check(sa.or_(status != "not_applicable", passed.is_(None)), "na_has_no_passed")]
    if table == "cost_record":
        status = _col("status")
        empty = sa.and_(*(_col(name).is_(None) for name in ("amount", "basis", "currency")))
        return [
            _non_negative("amount"),
            _check(sa.or_(status != "not_available", empty), "not_available_is_empty"),
            _both_or_neither("amount", "currency", "currency_with_amount"),
        ]
    if table == "cost_summary":
        return [
            _non_negative("total"),
            _both_or_neither("total", "currency", "currency_with_total"),
        ]
    if table == "evaluator":
        return [_both_or_neither("test_profile_id", "test_profile_version", "profile_pair")]
    if table == "config_snapshot":
        return [_both_or_neither("pricing_catalog_id", "pricing_catalog_version", "catalog_pair")]
    return []


def _entity_table(table: str) -> sa.Table:
    projected = _ENTITIES[table][1]
    versioned = table in _VERSIONED
    names = (
        "id",
        *(("version",) if versioned else ()),
        "schema_version",
        "created_at",
        "doc",
        "doc_sha256",
        *(_DIGEST_COLUMNS if versioned else ()),
        *projected,
    )
    items: list[Any] = _make_columns(table, names)
    items.append(sa.PrimaryKeyConstraint(*(("id", "version") if versioned else ("id",))))
    items.append(_check(_col("schema_version") == _SCHEMA_VERSION, "schema_version_pinned"))
    if versioned:
        items.append(_check(_col("version") >= 1, "version_ge_1"))
    for name in names:
        members = _CLOSED_SETS.get((table, name))
        if members is not None:
            items.append(_closed_set(name, members))
    if table in _RUN_OWNED:
        items.append(_fk(table, ["run_id"], "run", ["id"]))
    items.extend(_table_specific(table))
    return sa.Table(table, metadata, *items, sqlite_strict=True, sqlite_with_rowid=False)


ENTITY_TABLES: Mapping[str, sa.Table] = MappingProxyType(
    {entity: _entity_table(table) for table, (entity, _) in _ENTITIES.items()}
)

# ------------------------------------------------------------------------------- side tables

_SNAPSHOT_KINDS = ("evaluator", "persona", "rubric", "metric_definition", "metric_binding")
_BUNDLE_MEMBER_KINDS = (
    "run",
    "snapshot",
    "pricing_catalog",
    "test_profile",
    "artifact",
    "transcript",
    "turn",
    "exchange",
    "tool_call",
    "span",
    "result",
    "verdict",
    "usage_record",
    "cost_record",
    "cost_summary",
)
_VERSIONED_MEMBER_KINDS = ("pricing_catalog", "test_profile")
_WITHHELD_KINDS = _enum_of(_schema("RunBundle")["$defs"]["WithheldRecord"]["properties"]["kind"])
_EXTERNAL_REF_KINDS = ("run", "span", "transcript", "usage_record")
# ADR 0020 Decision 6 (reasons) and Decision 2 (root kinds)
_DELETION_REASONS = ("operator_request", "retention_policy", "data_subject_request")
_ROOT_KINDS = (
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


def _side_table(
    name: str, names: tuple[str, ...], key: tuple[str, ...], *items: SchemaItem
) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        *_make_columns(name, names),
        sa.PrimaryKeyConstraint(*key),
        *items,
        sqlite_strict=True,
        sqlite_with_rowid=False,
    )


def _ix(table: str, *names: str, **kwargs: Any) -> sa.Index:
    return sa.Index(f"ix_{table}_{'_'.join(names)}", *names, **kwargs)


def _export_link(name: str, second: str) -> sa.Table:
    return _side_table(
        name,
        ("export_id", second),
        ("export_id", second),
        _fk(name, ["export_id"], "export_bundle", ["id"]),
        _ix(name, second),
    )


def _build_side_tables() -> dict[str, sa.Table]:
    in_run = _col("entity_kind") == "run"
    versioned_kind = _col("kind").in_(_VERSIONED_MEMBER_KINDS)
    version = _col("record_version")
    tables = [
        _side_table(
            "snapshot_member",
            ("snapshot_id", "kind", "ordinal", "def_id", "def_version"),
            ("snapshot_id", "kind", "ordinal"),
            _fk("snapshot_member", ["snapshot_id"], "config_snapshot", ["id"]),
            _closed_set("kind", _SNAPSHOT_KINDS),
            _check(_col("ordinal") >= 0, "ordinal_ge_0"),
            _check(_col("def_version") >= 1, "def_version_ge_1"),
            _ix("snapshot_member", "kind", "def_id", "def_version"),
        ),
        _side_table(
            "snapshot_profile_ref",
            ("snapshot_id", "profile_id", "profile_version"),
            ("snapshot_id",),
            _fk("snapshot_profile_ref", ["snapshot_id"], "config_snapshot", ["id"]),
            _check(_col("profile_version") >= 1, "profile_version_ge_1"),
            _ix("snapshot_profile_ref", "profile_id", "profile_version"),
        ),
        # Unique for the run kind only (api-surface-batch-b A1): the golden RunBundle holds two
        # spans with one span_ref, so span, transcript and usage_record keys are lookups.
        _side_table(
            "external_ref_index",
            ("entity_kind", "entity_id", "key_sha256", "run_id"),
            ("entity_kind", "entity_id", "key_sha256"),
            _fk("external_ref_index", ["run_id"], "run", ["id"]),
            _closed_set("entity_kind", _EXTERNAL_REF_KINDS),
            sa.Index(
                "ux_external_ref_index_run_key",
                "entity_kind",
                "key_sha256",
                unique=True,
                sqlite_where=in_run,
                postgresql_where=in_run,
            ),
            _ix("external_ref_index", "run_id", "entity_kind", "key_sha256"),
        ),
        _side_table(
            "dataset_member",
            ("dataset_id", "dataset_version", "ordinal", "item_id"),
            ("dataset_id", "dataset_version", "ordinal"),
            _fk(
                "dataset_member",
                ["dataset_id", "dataset_version"],
                "dataset",
                ["id", "version"],
            ),
            _check(_col("ordinal") >= 0, "ordinal_ge_0"),
            _ix("dataset_member", "item_id"),
        ),
        _side_table(
            "run_bundle_member",
            ("bundle_id", "kind", "ordinal", "record_id", "record_version"),
            ("bundle_id", "kind", "ordinal"),
            _fk("run_bundle_member", ["bundle_id"], "run_bundle", ["id"]),
            _closed_set("kind", _BUNDLE_MEMBER_KINDS),
            _check(_col("ordinal") >= 0, "ordinal_ge_0"),
            _check(
                sa.or_(
                    sa.and_(versioned_kind, version.is_not(None)),
                    sa.and_(sa.not_(versioned_kind), version.is_(None)),
                ),
                "record_version_for_versioned_kinds",
            ),
            _ix("run_bundle_member", "kind", "record_id"),
        ),
        _side_table(
            "run_bundle_withheld",
            ("bundle_id", "ordinal", "kind", "record_id"),
            ("bundle_id", "ordinal"),
            _fk("run_bundle_withheld", ["bundle_id"], "run_bundle", ["id"]),
            _closed_set("kind", _WITHHELD_KINDS),
        ),
        _export_link("export_bundle_run", "run_id"),
        _export_link("export_bundle_item", "item_id"),
        _export_link("export_bundle_profile", "profile_id"),
        _side_table(
            "deletion_audit",
            ("audit_uuid", "deleted_at", "actor", "reason", "root_kind", "counts"),
            ("audit_uuid",),
            _closed_set("reason", _DELETION_REASONS),
            _closed_set("root_kind", _ROOT_KINDS),
        ),
        _side_table(
            "tracer_meta",
            ("singleton", "schema_version", "evaldb_version"),
            ("singleton",),
            _check(_col("singleton") == 1, "singleton_is_1"),
            _check(_col("schema_version") == _SCHEMA_VERSION, "schema_version_pinned"),
        ),
    ]
    return {table.name: table for table in tables}


SIDE_TABLES: Mapping[str, sa.Table] = MappingProxyType(_build_side_tables())

# One module attribute per table, named as the table.
agent_target = metadata.tables["agent_target"]
persona = metadata.tables["persona"]
test_profile = metadata.tables["test_profile"]
evaluator = metadata.tables["evaluator"]
metric_definition = metadata.tables["metric_definition"]
metric_binding = metadata.tables["metric_binding"]
rubric = metadata.tables["rubric"]
pricing_catalog = metadata.tables["pricing_catalog"]
dataset = metadata.tables["dataset"]
project = metadata.tables["project"]
config_snapshot = metadata.tables["config_snapshot"]
run = metadata.tables["run"]
artifact = metadata.tables["artifact"]
transcript = metadata.tables["transcript"]
turn = metadata.tables["turn"]
exchange = metadata.tables["exchange"]
tool_call_event = metadata.tables["tool_call_event"]
stage_span = metadata.tables["stage_span"]
metric_result = metadata.tables["metric_result"]
run_verdict = metadata.tables["run_verdict"]
usage_record = metadata.tables["usage_record"]
cost_record = metadata.tables["cost_record"]
cost_summary = metadata.tables["cost_summary"]
dataset_item = metadata.tables["dataset_item"]
run_bundle = metadata.tables["run_bundle"]
export_bundle = metadata.tables["export_bundle"]
snapshot_member = metadata.tables["snapshot_member"]
snapshot_profile_ref = metadata.tables["snapshot_profile_ref"]
external_ref_index = metadata.tables["external_ref_index"]
dataset_member = metadata.tables["dataset_member"]
run_bundle_member = metadata.tables["run_bundle_member"]
run_bundle_withheld = metadata.tables["run_bundle_withheld"]
export_bundle_run = metadata.tables["export_bundle_run"]
export_bundle_item = metadata.tables["export_bundle_item"]
export_bundle_profile = metadata.tables["export_bundle_profile"]
deletion_audit = metadata.tables["deletion_audit"]
tracer_meta = metadata.tables["tracer_meta"]
