# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The repository: the write unit, its reconstruction, exports and re-import lookups.

Every function takes the ``Engine`` of ``create_store_engine`` first and opens exactly one
transaction with ``engine.begin()``: committed when the function returns, rolled back when any
exception leaves it. A ``Connection`` (or anything that is not an ``Engine``) is a ``TypeError``
raised before any statement.

A writer works in this order, and nothing is sent to the database before the last step but a
``SELECT`` (ADR 0022):

1. the argument is serialised with ``model_dump_json()`` and loaded again through the Phase 1
   loaders, so an instance built or changed without validation cannot be stored;
2. every record is encoded with the codecs, outside the transaction (so ``StoreValueOutOfRange``
   is decided before the first statement);
3. in the transaction, every record is looked up (one ``SELECT`` per table) and compared with what
   is stored: ownership before content, ``ReimportConflict`` before everything else;
4. the rows that are absent are inserted, parents first, one multi-row ``INSERT`` per table.

Errors name the kind and the ID of a record and never a value, a document fragment or an external
reference.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from tracer_evalschema import digest as ev_digest
from tracer_evalschema import loader
from tracer_evalschema import snapshot as ev_snapshot
from tracer_evalschema.agent import AgentTarget
from tracer_evalschema.base import EntityBase, VersionedEntity
from tracer_evalschema.bundle import (
    EXPORT_PURPOSES,
    ExportBundle,
    RunBundle,
    check_export_bundle,
)
from tracer_evalschema.dataset import Dataset, DatasetItem
from tracer_evalschema.digest import ContentDigest
from tracer_evalschema.errors import (
    BundleSizeError,
    ConflictingRecordError,
    ConflictingVersionError,
)
from tracer_evalschema.evaluator import Evaluator
from tracer_evalschema.ids import (
    AgentTargetId,
    ArtifactId,
    ConfigSnapshotId,
    CostRecordId,
    CostSummaryId,
    DatasetId,
    DatasetItemId,
    EvaluatorId,
    ExchangeId,
    ExportBundleId,
    ExternalRef,
    MetricBindingId,
    MetricDefinitionId,
    MetricResultId,
    PersonaId,
    PricingCatalogId,
    ProjectId,
    RubricId,
    RunBundleId,
    RunId,
    RunVerdictId,
    StageSpanId,
    TestProfileId,
    ToolCallEventId,
    TranscriptId,
    TurnId,
    TypedId,
    UsageRecordId,
)
from tracer_evalschema.metrics import MetricBinding, MetricDefinition
from tracer_evalschema.persona import Persona
from tracer_evalschema.pricing import PricingCatalog
from tracer_evalschema.profile import TestProfile
from tracer_evalschema.project import Project
from tracer_evalschema.rubric import Rubric
from tracer_evalschema.snapshot import ConfigSnapshot

from . import codecs
from .codecs import EncodedEntity
from .errors import (
    ExportReferenceNotStored,
    IncompleteRunBundle,
    InternalSnapshotRefused,
    ProjectNotStored,
    RecordNotStored,
    RecordOwnershipConflict,
    ReimportConflict,
    StoreConstraintViolation,
)
from .schema import metadata

__all__ = [
    "ExternalRefMatch",
    "LoadedRunBundle",
    "MissingMember",
    "StoreResult",
    "find_by_external_ref",
    "find_snapshot_by_digest",
    "latest_version",
    "load_dataset_item",
    "load_definition",
    "load_export",
    "load_project",
    "load_run_bundle",
    "load_snapshot",
    "store_dataset_item",
    "store_definition",
    "store_export",
    "store_project",
    "store_run_bundle",
    "store_snapshot",
    "translate_integrity_error",
]

# ------------------------------------------------------------------------------------ results


@dataclass(frozen=True)
class StoreResult:
    """What a writer did. ``created``: the root row (the entity, the snapshot, the bundle, the
    export) was inserted by this call. ``inserted``: new entity rows per table (side-table rows
    are not counted, a table with none is absent)."""

    kind: str
    entity_id: TypedId
    version: int | None
    created: bool
    inserted: Mapping[str, int]


@dataclass(frozen=True)
class MissingMember:
    """A member of a stored run bundle whose record is gone."""

    kind: str
    record_id: TypedId
    record_version: int | None


@dataclass(frozen=True)
class LoadedRunBundle:
    """A run bundle read back. ``bundle`` is ``None`` unless ``complete``; ``records`` holds, for an
    incomplete load, the records that remain by member kind."""

    bundle_id: TypedId
    complete: bool
    missing: tuple[MissingMember, ...]
    bundle: RunBundle | None
    records: Mapping[str, tuple[EntityBase, ...]]


@dataclass(frozen=True)
class ExternalRefMatch:
    """A record found by an external reference and the run that owns it."""

    entity_kind: str
    entity_id: TypedId
    run_id: RunId


# --------------------------------------------------------------------------------- vocabularies

# model class -> (table, ID class): the nine versioned entities of ADR 0009 Decision 5
_DEFINITIONS: Mapping[type[VersionedEntity], tuple[str, type[TypedId]]] = {
    AgentTarget: ("agent_target", AgentTargetId),
    Persona: ("persona", PersonaId),
    TestProfile: ("test_profile", TestProfileId),
    Evaluator: ("evaluator", EvaluatorId),
    MetricDefinition: ("metric_definition", MetricDefinitionId),
    MetricBinding: ("metric_binding", MetricBindingId),
    Rubric: ("rubric", RubricId),
    PricingCatalog: ("pricing_catalog", PricingCatalogId),
    Dataset: ("dataset", DatasetId),
}
_DEFINITION_IDS: Mapping[str, type[TypedId]] = dict(_DEFINITIONS.values())
_VERSIONED = frozenset(_DEFINITION_IDS)

# the run_bundle_member kinds in member order:
# (kind, table, ID class, field of RunBundle, versioned)
_MEMBERS: tuple[tuple[str, str, type[TypedId], str, bool], ...] = (
    ("run", "run", RunId, "run", False),
    ("snapshot", "config_snapshot", ConfigSnapshotId, "snapshot", False),
    ("pricing_catalog", "pricing_catalog", PricingCatalogId, "pricing_catalogs", True),
    ("test_profile", "test_profile", TestProfileId, "test_profiles", True),
    ("artifact", "artifact", ArtifactId, "artifacts", False),
    ("transcript", "transcript", TranscriptId, "transcripts", False),
    ("turn", "turn", TurnId, "turns", False),
    ("exchange", "exchange", ExchangeId, "exchanges", False),
    ("tool_call", "tool_call_event", ToolCallEventId, "tool_calls", False),
    ("span", "stage_span", StageSpanId, "spans", False),
    ("result", "metric_result", MetricResultId, "results", False),
    ("verdict", "run_verdict", RunVerdictId, "verdict", False),
    ("usage_record", "usage_record", UsageRecordId, "usage_records", False),
    ("cost_record", "cost_record", CostRecordId, "cost_records", False),
    ("cost_summary", "cost_summary", CostSummaryId, "cost_summaries", False),
)
_MEMBER_ORDER = {kind: index for index, (kind, *_rest) in enumerate(_MEMBERS)}

# the records of fact that belong to one run (ADR 0022 Decision 5)
_OWNED = frozenset(
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
    }
)
# (RunBundle list field, table), in the order the rows are inserted
_BUNDLE_LISTS = (
    ("artifacts", "artifact"),
    ("transcripts", "transcript"),
    ("turns", "turn"),
    ("exchanges", "exchange"),
    ("tool_calls", "tool_call_event"),
    ("spans", "stage_span"),
    ("results", "metric_result"),
)
_EXTERNAL_IDS: Mapping[str, type[TypedId]] = {
    "run": RunId,
    "span": StageSpanId,
    "transcript": TranscriptId,
    "usage_record": UsageRecordId,
}
_EXPORT_LINKS = (
    ("run", "export_bundle_run", "run_id", "run", RunId),
    ("dataset_item", "export_bundle_item", "item_id", "dataset_item", DatasetItemId),
    ("test_profile", "export_bundle_profile", "profile_id", "test_profile", TestProfileId),
)

# an IN list stays far below the variable limits of SQLite (32,766) and pg8000 (65,535)
_IN_CHUNK = 10_000
_PAIR_CHUNK = 2_000
_EXPORT_LIMIT = loader.DEFAULT_MAX_BUNDLE_BYTES

# ------------------------------------------------------------------------------------ arguments


def _engine(engine: object) -> Engine:
    if not isinstance(engine, Engine):
        raise TypeError("the first argument must be the Engine of create_store_engine")
    return engine


def _uuid_of(value: object, id_type: type[TypedId]) -> uuid.UUID:
    """The UUID of an ID of this class or of a bare ``uuid.UUID`` (which must be version 7)."""
    if isinstance(value, id_type):
        return value.uuid
    if type(value) is uuid.UUID:
        return id_type(value).uuid  # ValueError for a UUID that is not version 7
    raise TypeError(f"expected a {id_type.__name__} or a uuid.UUID")


def _definition_table(kind: object) -> str:
    if not isinstance(kind, str) or kind not in _VERSIONED:
        raise ValueError(f"kind must be one of: {', '.join(sorted(_VERSIONED))}")
    return kind


def _version_of(version: object) -> int:
    if type(version) is not int:
        raise TypeError("version must be an int")
    if not 1 <= version <= 2**53 - 1:
        raise ValueError("version must be between 1 and 2**53 - 1")
    return version


def _table(name: str) -> sa.Table:
    return metadata.tables[name]


# ------------------------------------------------------------------------- constraint errors

_SQLITE_KINDS = (
    ("CHECK constraint failed", "check"),
    ("UNIQUE constraint failed", "unique"),
    ("PRIMARY KEY must be unique", "unique"),
    ("FOREIGN KEY constraint failed", "foreign_key"),
    ("NOT NULL constraint failed", "not_null"),
)
_POSTGRES_KINDS = {
    "23514": "check",
    "23505": "unique",
    "23503": "foreign_key",
    "23001": "foreign_key",
    "23502": "not_null",
}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SQLITE_TABLE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.")


def translate_integrity_error(error: sa.exc.IntegrityError) -> StoreConstraintViolation:
    """Map a database refusal to the kind of constraint, the table and the constraint name when the
    database names them (Postgres always; SQLite only the table of a unique or not-null violation
    and the name of a check). A refusal of another kind is ``other``. Never a value."""
    original = error.orig
    fields = original.args[0] if original is not None and original.args else None
    if isinstance(fields, dict):
        code = fields.get("C")
        kind = _POSTGRES_KINDS.get(code, "other") if isinstance(code, str) else "other"
        table, constraint = fields.get("t"), fields.get("n")
        return StoreConstraintViolation(
            kind,
            table if isinstance(table, str) else None,
            constraint if isinstance(constraint, str) else None,
        )
    text = str(original)
    for prefix, kind in _SQLITE_KINDS:
        if text.startswith(prefix):
            detail = text[len(prefix) :].lstrip(": ")
            table_name = _SQLITE_TABLE.match(detail) if kind in ("unique", "not_null") else None
            named = _NAME.fullmatch(detail) if kind == "check" else None
            return StoreConstraintViolation(
                kind,
                table_name.group(1) if table_name else None,
                named.group(0) if named else None,
            )
    return StoreConstraintViolation("other")


@contextlib.contextmanager
def _writing(engine: Engine) -> Iterator[Connection]:
    """One transaction; a database refusal leaves it, after the rollback, as a typed error."""
    try:
        with engine.begin() as connection:
            yield connection
    except sa.exc.IntegrityError as error:
        raise translate_integrity_error(error) from None


# -------------------------------------------------------------------------------- the plan


@dataclass(frozen=True)
class _Item:
    table: str
    model: EntityBase
    encoded: EncodedEntity

    @property
    def key(self) -> tuple[Any, ...]:
        row = self.encoded.row
        return (row["id"], row["version"]) if "version" in row else (row["id"],)


@dataclass(frozen=True)
class _Stored:
    sha: bytes
    owner: uuid.UUID | None


class _Plan:
    """The records to store, by table in insertion order (parents first); a record that is listed
    twice with one ID is one record (the models refuse two contents)."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple[Any, ...], _Item]] = {}

    def add(self, table: str, model: EntityBase, encoded: EncodedEntity) -> _Item:
        item = _Item(table, model, encoded)
        return self.tables.setdefault(table, {}).setdefault(item.key, item)


def _fetch(
    connection: Connection, table: str, keys: Sequence[tuple[Any, ...]]
) -> dict[tuple[Any, ...], _Stored]:
    """The stored hash (and owner) of each key that exists: one statement per chunk of keys."""
    t = _table(table)
    versioned = "version" in t.c
    has_run = "run_id" in t.c
    columns = [t.c.id, t.c.doc_sha256]
    if versioned:
        columns.append(t.c.version)
    if has_run:
        columns.append(t.c.run_id)
    found: dict[tuple[Any, ...], _Stored] = {}
    size = _PAIR_CHUNK if versioned else _IN_CHUNK
    for start in range(0, len(keys), size):
        chunk = keys[start : start + size]
        if versioned:
            where = sa.or_(*(sa.and_(t.c.id == i, t.c.version == v) for i, v in chunk))
        else:
            where = t.c.id.in_([key[0] for key in chunk])
        for row in connection.execute(sa.select(*columns).where(where)):
            key = (row[0], row[2]) if versioned else (row[0],)
            found[key] = _Stored(row[1], row[-1] if has_run else None)
    return found


def _conflict(table: str, item: _Item) -> ConflictingRecordError | ConflictingVersionError:
    model = item.model
    if isinstance(model, VersionedEntity):
        return ConflictingVersionError(
            f"{table} {model.id} version {model.version} is stored with other content"
        )
    return ConflictingRecordError(f"{table} {model.id} is stored with other content")


def _compare(table: str, item: _Item, stored: _Stored) -> None:
    """Ownership first, then content (ADR 0022 Decision 5)."""
    row = item.encoded.row
    if table in _OWNED and stored.owner != row["run_id"]:
        raise RecordOwnershipConflict(table, item.model.id)
    if stored.sha != row["doc_sha256"] or (table == "run_bundle" and stored.owner != row["run_id"]):
        raise _conflict(table, item)


def _settle(connection: Connection, plan: _Plan) -> dict[str, list[_Item]]:
    """Look every record up, check what is stored, and return the records to insert by table.
    Raises before anything is written."""
    fresh: dict[str, list[_Item]] = {}
    for table, items in plan.tables.items():
        stored = _fetch(connection, table, list(items))
        for key, item in items.items():
            found = stored.get(key)
            if found is None:
                fresh.setdefault(table, []).append(item)
            else:
                _compare(table, item, found)
    return fresh


def _insert(connection: Connection, fresh: Mapping[str, Sequence[_Item]]) -> dict[str, int]:
    """One multi-row ``INSERT`` per table: the entity rows in plan order, then the side rows."""
    side: dict[str, list[Mapping[str, object]]] = {}
    for table, items in fresh.items():
        connection.execute(sa.insert(_table(table)), [dict(item.encoded.row) for item in items])
        for item in items:
            for name, rows in item.encoded.side_rows.items():
                side.setdefault(name, []).extend(rows)
    for name, rows in side.items():
        if rows:
            connection.execute(sa.insert(_table(name)), [dict(row) for row in rows])
    return {table: len(items) for table, items in fresh.items()}


def _result(
    root: _Item, fresh: Mapping[str, Sequence[_Item]], inserted: Mapping[str, int]
) -> StoreResult:
    model = root.model
    return StoreResult(
        kind=root.table,
        entity_id=model.id,
        version=model.version if isinstance(model, VersionedEntity) else None,
        created=root.table in fresh and root.key in {i.key for i in fresh[root.table]},
        inserted=MappingProxyType(dict(inserted)),
    )


def _store_plan(engine: Engine, plan: _Plan, root: _Item) -> StoreResult:
    with _writing(engine) as connection:
        fresh = _settle(connection, plan)
        inserted = _insert(connection, fresh)
    return _result(root, fresh, inserted)


def _revalidated(model: Any, cls: type[Any]) -> Any:
    """The model loaded again from its own JSON text (``model_copy`` and ``model_construct`` do
    not validate)."""
    return loader.load_model(cls, model.model_dump_json())


# ----------------------------------------------------------------------- definitions and roots


def _store_root(engine: Engine, table: str, model: EntityBase) -> StoreResult:
    plan = _Plan()
    root = plan.add(table, model, codecs.encode(model))
    return _store_plan(engine, plan, root)


def store_project(engine: Engine, project: Project) -> StoreResult:
    """Insert a project, or do nothing for an equal one; other content under the ID is a
    ``ConflictingRecordError``."""
    _engine(engine)
    if type(project) is not Project:
        raise TypeError("store_project takes a Project")
    return _store_root(engine, "project", _revalidated(project, Project))


def store_dataset_item(engine: Engine, item: DatasetItem) -> StoreResult:
    """Insert a dataset item. It may name an artifact, a transcript or a run that is not stored."""
    _engine(engine)
    if type(item) is not DatasetItem:
        raise TypeError("store_dataset_item takes a DatasetItem")
    return _store_root(engine, "dataset_item", _revalidated(item, DatasetItem))


def store_definition(engine: Engine, definition: VersionedEntity) -> StoreResult:
    """Insert one of the nine versioned entities, or do nothing for an equal one; other content
    under the same ``(id, version)`` is a ``ConflictingVersionError``."""
    _engine(engine)
    entry = _DEFINITIONS.get(type(definition))
    if entry is None:
        raise TypeError("store_definition takes one of the nine versioned entities")
    return _store_root(engine, entry[0], _revalidated(definition, type(definition)))


def _add_snapshot(plan: _Plan, snapshot: ConfigSnapshot) -> _Item:
    """The definitions a snapshot embeds, then the snapshot (the order of the inserts)."""
    embedded: list[tuple[str, EntityBase]] = [
        ("persona", snapshot.persona),
        ("rubric", snapshot.rubric),
        ("evaluator", snapshot.evaluator),
        *(("metric_definition", d) for d in snapshot.metric_definitions),
        *(("metric_binding", b) for b in snapshot.metric_bindings),
    ]
    for table, model in embedded:
        plan.add(table, model, codecs.encode(model))
    return plan.add("config_snapshot", snapshot, codecs.encode(snapshot))


def _internal(snapshot: ConfigSnapshot) -> None:
    try:
        ev_snapshot.check_internal_snapshot(snapshot)
    except ValueError:
        raise InternalSnapshotRefused(snapshot.id) from None


def store_snapshot(engine: Engine, snapshot: ConfigSnapshot) -> StoreResult:
    """Insert a snapshot with the definitions it embeds, in one transaction. A definition that is
    stored and equal is shared; a different one under the same ``(id, version)`` is a
    ``ConflictingVersionError`` and nothing is written."""
    _engine(engine)
    if type(snapshot) is not ConfigSnapshot:
        raise TypeError("store_snapshot takes a ConfigSnapshot")
    valid = _revalidated(snapshot, ConfigSnapshot)
    _internal(valid)
    plan = _Plan()
    root = _add_snapshot(plan, valid)
    return _store_plan(engine, plan, root)


# --------------------------------------------------------------------------------- run bundles


def _check_reimport(connection: Connection, run: _Item) -> None:
    """ADR 0022 Decision 11: a stored run that shares an external reference with the arriving run
    and is another run, or the same run with other content, is a ``ReimportConflict``."""
    keys = [row["key_sha256"] for row in run.encoded.side_rows.get("external_ref_index", ())]
    if not keys:
        return
    index, runs = _table("external_ref_index"), _table("run")
    statement = (
        sa.select(index.c.entity_id, runs.c.doc_sha256)
        .join(runs, runs.c.id == index.c.entity_id)
        .where(index.c.entity_kind == "run", index.c.key_sha256.in_(keys))
        .order_by(index.c.entity_id)
    )
    for stored_id, stored_sha in connection.execute(statement):
        if stored_id != run.encoded.row["id"] or stored_sha != run.encoded.row["doc_sha256"]:
            raise ReimportConflict("run", RunId(stored_id))


def _bundle_plan(bundle: RunBundle) -> tuple[_Plan, _Item, _Item]:
    """Encode every record of a validated bundle. Returns the plan, the run and the bundle."""
    plan = _Plan()
    snapshot = bundle.snapshot
    _add_snapshot(plan, snapshot)
    for catalog in bundle.pricing_catalogs:
        plan.add("pricing_catalog", catalog, codecs.encode(catalog))
    for profile in bundle.test_profiles:
        plan.add("test_profile", profile, codecs.encode(profile))
    run = plan.add("run", bundle.run, codecs.encode(bundle.run))
    run_id = bundle.run.id.uuid
    for field, table in _BUNDLE_LISTS:
        for record in getattr(bundle, field):
            plan.add(table, record, codecs.encode(record, run_id=run_id))
    if bundle.verdict is not None:
        plan.add("run_verdict", bundle.verdict, codecs.encode(bundle.verdict, run_id=run_id))
    for usage in bundle.usage_records:
        plan.add("usage_record", usage, codecs.encode(usage))
    for cost in bundle.cost_records:
        plan.add("cost_record", cost, codecs.encode(cost, run_id=run_id))
    for summary in bundle.cost_summaries:
        plan.add("cost_summary", summary, codecs.encode(summary, run_id=run_id))
    root = plan.add("run_bundle", bundle, codecs.encode_run_bundle(bundle))
    return plan, run, root


def _check_bundle_members(connection: Connection, root: _Item) -> None:
    """A stored bundle with this ID is a no-op only if its members and withheld records are the
    ones arriving."""
    bundle_id = root.encoded.row["id"]
    members, withheld = _table("run_bundle_member"), _table("run_bundle_withheld")
    side = root.encoded.side_rows
    want_members = {
        (r["kind"], r["ordinal"], r["record_id"], r["record_version"])
        for r in side["run_bundle_member"]
    }
    got_members = {
        tuple(row)
        for row in connection.execute(
            sa.select(
                members.c.kind, members.c.ordinal, members.c.record_id, members.c.record_version
            ).where(members.c.bundle_id == bundle_id)
        )
    }
    want_withheld = {(r["ordinal"], r["kind"], r["record_id"]) for r in side["run_bundle_withheld"]}
    got_withheld = {
        tuple(row)
        for row in connection.execute(
            sa.select(withheld.c.ordinal, withheld.c.kind, withheld.c.record_id).where(
                withheld.c.bundle_id == bundle_id
            )
        )
    }
    if got_members != want_members or got_withheld != want_withheld:
        raise _conflict("run_bundle", root)


def store_run_bundle(engine: Engine, bundle: RunBundle) -> StoreResult:
    """Store a run bundle and everything it lists in one transaction (the write unit, ADR 0022).

    Records of fact whose IDs are stored are shared, not inserted again: a rescoring bundle inserts
    only its new rows. The run's project must be stored.
    """
    _engine(engine)
    if type(bundle) is not RunBundle:
        raise TypeError("store_run_bundle takes a RunBundle")
    valid = loader.load_bundle(RunBundle, bundle.model_dump_json())
    _internal(valid.snapshot)
    plan, run, root = _bundle_plan(valid)
    with _writing(engine) as connection:
        projects = _table("project")
        project_id = valid.run.project_id
        if (
            connection.execute(
                sa.select(projects.c.id).where(projects.c.id == project_id.uuid)
            ).first()
            is None
        ):
            raise ProjectNotStored(project_id)
        _check_reimport(connection, run)
        fresh = _settle(connection, plan)
        if "run_bundle" not in fresh:
            _check_bundle_members(connection, root)
        inserted = _insert(connection, fresh)
    return _result(root, fresh, inserted)


def _sorted_members(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: (_MEMBER_ORDER[row["kind"]], row["ordinal"]))


def _read_records(
    connection: Connection, members: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, uuid.UUID, int | None], EntityBase]:
    """The decoded record of every member that is stored, by (kind, ID, version)."""
    found: dict[tuple[str, uuid.UUID, int | None], EntityBase] = {}
    for kind, table, _id_type, _field, versioned in _MEMBERS:
        keys = [(m["record_id"], m["record_version"]) for m in members if m["kind"] == kind]
        if not keys:
            continue
        t = _table(table)
        size = _PAIR_CHUNK if versioned else _IN_CHUNK
        for start in range(0, len(keys), size):
            chunk = keys[start : start + size]
            if versioned:
                where = sa.or_(*(sa.and_(t.c.id == i, t.c.version == v) for i, v in chunk))
            else:
                where = t.c.id.in_([i for i, _ in chunk])
            for row in connection.execute(sa.select(t).where(where)).mappings():
                record = codecs.decode(table, dict(row))
                found[kind, row["id"], row["version"] if versioned else None] = record
    return found


def load_run_bundle(
    engine: Engine, bundle_id: RunBundleId | uuid.UUID, *, allow_incomplete: bool = False
) -> LoadedRunBundle:
    """Read a stored run bundle back, in the order it was stored in.

    A member whose record is gone (deleted rows) is an ``IncompleteRunBundle``; with
    ``allow_incomplete`` it is reported in ``missing`` and the records that remain are returned,
    never a partial ``RunBundle``. One read transaction (REPEATABLE READ on Postgres).
    """
    _engine(engine)
    bundle_uuid = _uuid_of(bundle_id, RunBundleId)
    reader: Engine = engine
    if engine.dialect.name == "postgresql":
        reader = engine.execution_options(isolation_level="REPEATABLE READ")
    with reader.begin() as connection:
        bundles = _table("run_bundle")
        row = (
            connection.execute(sa.select(bundles).where(bundles.c.id == bundle_uuid))
            .mappings()
            .first()
        )
        if row is None:
            raise RecordNotStored("run_bundle", RunBundleId(bundle_uuid))
        member_table, withheld_table = _table("run_bundle_member"), _table("run_bundle_withheld")
        members = _sorted_members(
            [
                dict(r)
                for r in connection.execute(
                    sa.select(member_table).where(member_table.c.bundle_id == bundle_uuid)
                ).mappings()
            ]
        )
        withheld = [
            dict(r)
            for r in connection.execute(
                sa.select(withheld_table).where(withheld_table.c.bundle_id == bundle_uuid)
            ).mappings()
        ]
        records = _read_records(connection, members)
    typed_ids = {kind: id_type for kind, _t, id_type, _f, _v in _MEMBERS}
    missing = tuple(
        MissingMember(m["kind"], typed_ids[m["kind"]](m["record_id"]), m["record_version"])
        for m in members
        if (m["kind"], m["record_id"], m["record_version"]) not in records
    )
    typed_bundle_id = RunBundleId(bundle_uuid)
    if not missing:
        bundle = codecs.decode_run_bundle(
            dict(row),
            members=members,
            withheld=withheld,
            resolve=lambda kind, record_id, version: records[kind, record_id, version],
        )
        return LoadedRunBundle(typed_bundle_id, True, (), bundle, MappingProxyType({}))
    if not allow_incomplete:
        raise IncompleteRunBundle(typed_bundle_id, missing)
    remaining: dict[str, list[EntityBase]] = {}
    for m in members:
        record = records.get((m["kind"], m["record_id"], m["record_version"]))
        if record is not None:
            remaining.setdefault(m["kind"], []).append(record)
    return LoadedRunBundle(
        typed_bundle_id,
        False,
        missing,
        None,
        MappingProxyType({kind: tuple(found) for kind, found in remaining.items()}),
    )


# ----------------------------------------------------------------------------------- readers


def _read_one(
    engine: Engine, table: str, id_type: type[TypedId], record_uuid: uuid.UUID, version: int | None
) -> Any:
    t = _table(table)
    where = [t.c.id == record_uuid]
    if version is not None:
        where.append(t.c.version == version)
    with engine.begin() as connection:
        row = connection.execute(sa.select(t).where(*where)).mappings().first()
    if row is None:
        raise RecordNotStored(table, id_type(record_uuid), version)
    return codecs.decode(table, dict(row))


def load_project(engine: Engine, project_id: ProjectId | uuid.UUID) -> Project:
    _engine(engine)
    found: Project = _read_one(engine, "project", ProjectId, _uuid_of(project_id, ProjectId), None)
    return found


def load_definition(
    engine: Engine, kind: str, definition_id: TypedId | uuid.UUID, version: int
) -> Any:
    """One of the nine versioned entities by table name, ID and version."""
    _engine(engine)
    table = _definition_table(kind)
    id_type = _DEFINITION_IDS[table]
    return _read_one(engine, table, id_type, _uuid_of(definition_id, id_type), _version_of(version))


def load_snapshot(engine: Engine, snapshot_id: ConfigSnapshotId | uuid.UUID) -> ConfigSnapshot:
    _engine(engine)
    snapshot_uuid = _uuid_of(snapshot_id, ConfigSnapshotId)
    found: ConfigSnapshot = _read_one(
        engine, "config_snapshot", ConfigSnapshotId, snapshot_uuid, None
    )
    return found


def load_dataset_item(engine: Engine, item_id: DatasetItemId | uuid.UUID) -> DatasetItem:
    _engine(engine)
    found: DatasetItem = _read_one(
        engine, "dataset_item", DatasetItemId, _uuid_of(item_id, DatasetItemId), None
    )
    return found


def latest_version(engine: Engine, kind: str, definition_id: TypedId | uuid.UUID) -> int | None:
    """The highest stored version of a versioned entity, or ``None``."""
    _engine(engine)
    table = _definition_table(kind)
    record_uuid = _uuid_of(definition_id, _DEFINITION_IDS[table])
    t = _table(table)
    with engine.begin() as connection:
        newest = connection.execute(
            sa.select(sa.func.max(t.c.version)).where(t.c.id == record_uuid)
        ).scalar_one()
    return None if newest is None else int(newest)


def find_snapshot_by_digest(engine: Engine, content_digest: ContentDigest) -> ConfigSnapshot | None:
    """The stored snapshot with this content digest (the scheme is part of the key), or ``None``."""
    _engine(engine)
    if not isinstance(content_digest, ContentDigest):
        raise TypeError("find_snapshot_by_digest takes a ContentDigest")
    t = _table("config_snapshot")
    with engine.begin() as connection:
        row = (
            connection.execute(
                sa.select(t)
                .where(
                    t.c.content_digest_scheme == content_digest.scheme,
                    t.c.content_digest_value == bytes.fromhex(content_digest.value),
                )
                .order_by(t.c.id)
                .limit(1)
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    found: ConfigSnapshot = codecs.decode("config_snapshot", dict(row))
    return found


def find_by_external_ref(
    engine: Engine, entity_kind: str, ref: ExternalRef, *, run_id: RunId | uuid.UUID | None = None
) -> tuple[ExternalRefMatch, ...]:
    """The records of a kind that carry an external reference, ordered by ID (time order).

    A ``run`` is found globally (``run_id`` must be ``None``); a span, transcript or usage record
    within one run (``run_id`` is required).
    """
    _engine(engine)
    if not isinstance(entity_kind, str) or entity_kind not in _EXTERNAL_IDS:
        raise ValueError(f"entity_kind must be one of: {', '.join(_EXTERNAL_IDS)}")
    if not isinstance(ref, ExternalRef):
        raise TypeError("ref must be an ExternalRef")
    if entity_kind == "run":
        if run_id is not None:
            raise ValueError("run_id must be None for the entity_kind run")
        run_uuid = None
    else:
        if run_id is None:
            raise ValueError(f"run_id is required for the entity_kind {entity_kind}")
        run_uuid = _uuid_of(run_id, RunId)
    key = bytes.fromhex(
        ev_digest.sha256_hex(ev_digest.jcs_bytes([ref.system, ref.kind, ref.value]))
    )
    index = _table("external_ref_index")
    where = [index.c.entity_kind == entity_kind, index.c.key_sha256 == key]
    if run_uuid is not None:
        where.append(index.c.run_id == run_uuid)
    with engine.begin() as connection:
        rows = connection.execute(sa.select(index.c.entity_id, index.c.run_id).where(*where)).all()
    id_type = _EXTERNAL_IDS[entity_kind]
    return tuple(
        ExternalRefMatch(entity_kind, id_type(entity), RunId(owner))
        for entity, owner in sorted(rows, key=lambda r: r[0].bytes)
    )


# -------------------------------------------------------------------------------------- exports


def _purpose(expected_purpose: object) -> str:
    if type(expected_purpose) is not str:
        raise TypeError("expected_purpose must be a str")
    if expected_purpose not in EXPORT_PURPOSES:
        raise ValueError(f"expected_purpose must be one of: {', '.join(EXPORT_PURPOSES)}")
    return expected_purpose


def _lock_and_verify(connection: Connection, export: _Item) -> None:
    """Every run, dataset item and test profile the export links must be stored. On Postgres they
    are locked ``FOR KEY SHARE``, so a deletion waits for this transaction (ADR 0020 Decision 5);
    on SQLite the write transaction itself serializes writers."""
    postgres = connection.dialect.name == "postgresql"
    for kind, link, column, table, id_type in _EXPORT_LINKS:
        wanted = [cast("uuid.UUID", row[column]) for row in export.encoded.side_rows[link]]
        if not wanted:
            continue
        t = _table(table)
        statement = sa.select(t.c.id).where(t.c.id.in_(wanted))
        if postgres:
            statement = statement.with_for_update(read=True, key_share=True)
        present = set(connection.execute(statement).scalars())
        for record_uuid in wanted:
            if record_uuid not in present:
                raise ExportReferenceNotStored(kind, id_type(record_uuid))


def store_export(
    engine: Engine, export: ExportBundle | str | bytes, *, expected_purpose: str
) -> StoreResult:
    """Store an export as one opaque document plus link rows (ADR 0022 Decision 9).

    ``expected_purpose`` is required: the export's own label is self-declared. Every run, dataset
    item and test profile it carries must be stored.
    """
    _engine(engine)
    purpose = _purpose(expected_purpose)
    if not isinstance(export, ExportBundle | str | bytes):
        raise TypeError("store_export takes an ExportBundle, str or bytes")
    data = export.model_dump_json() if isinstance(export, ExportBundle) else export
    valid = loader.load_bundle(ExportBundle, data, expected_purpose=purpose)
    plan = _Plan()
    root = plan.add("export_bundle", valid, codecs.encode_export_bundle(valid))
    with _writing(engine) as connection:
        fresh = _settle(connection, plan)
        if fresh:
            _lock_and_verify(connection, root)
        inserted = _insert(connection, fresh)
    return _result(root, fresh, inserted)


def load_export(
    engine: Engine, export_id: ExportBundleId | uuid.UUID, *, expected_purpose: str
) -> ExportBundle:
    """Read a stored export. The row's ``byte_length`` is compared with the 256 MiB limit before
    the document is parsed."""
    _engine(engine)
    purpose = _purpose(expected_purpose)
    export_uuid = _uuid_of(export_id, ExportBundleId)
    t = _table("export_bundle")
    with engine.begin() as connection:
        row = connection.execute(sa.select(t).where(t.c.id == export_uuid)).mappings().first()
    if row is None:
        raise RecordNotStored("export_bundle", ExportBundleId(export_uuid))
    length = row["byte_length"]
    if type(length) is int and length > _EXPORT_LIMIT:
        raise BundleSizeError(
            f"the stored export is {length} bytes of UTF-8, over the limit of {_EXPORT_LIMIT} bytes"
        )
    export: ExportBundle = codecs.decode_export_bundle(dict(row))
    check_export_bundle(export, expected_purpose=purpose)
    return export
