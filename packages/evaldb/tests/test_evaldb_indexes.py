# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch E, P2-20: the indexes of the access patterns of phases 3 to 7.

Contract: docs/phase2/api-surface-batch-e.md sections 2 and 3, ADR 0019 Decision 15 and invariant
I20. The indexes are made by the new Alembic revision 0002 (revision 0001 is frozen text) and by
``tracer_evaldb.tables`` (so ``metadata.create_all`` builds the same schema). A plan is asserted on
facts only (which index a plan names, whether a table is read without an index, whether a separate
sort step exists), never on plan text, which differs between SQLite and Postgres versions.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
from evaldb_index_support import (  # type: ignore[import-not-found,unused-ignore]
    ALL_INDEX_NAMES,
    ANY_INDEX,
    NEW_INDEXES,
    OLD_INDEXES,
    PRIMARY_KEY,
    VERSIONED,
    Plan,
    capture_statements,
    db_indexes,
    explain,
    key_prefix_covered,
    metadata_indexes,
    scans_of,
)
from evaldb_migration_support import MigStore, populate  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    load_run_bundle_doc,
    run_bundle_doc,
    store_bundle,
)
from evaldb_support import FIXED_INSTANT, Rows  # type: ignore[import-not-found,unused-ignore]

mig = _mig_support.mig
sqlite_mig = _mig_support.sqlite_mig
postgres_mig = _mig_support.postgres_mig
migrate = _mig_support.migrate


def _uuid7(ms: int, rng: random.Random) -> uuid.UUID:
    """A UUIDv7 at this millisecond (48-bit time, version 7, variant 10, random rest)."""
    value = (ms << 80) | (0x7 << 76) | (rng.getrandbits(12) << 64) | (0b10 << 62)
    value |= rng.getrandbits(62)
    return uuid.UUID(int=value)


def _fresh(ms: int = 1_800_000_000_000) -> uuid.UUID:
    return _uuid7(ms, random.Random(ms))  # noqa: S311  (test data)


SHA = bytes(range(32))
NOW = FIXED_INSTANT
LATER = NOW + dt.timedelta(days=1)


def _migrated(mig: MigStore, migrate: ModuleType) -> sa.Engine:
    engine: sa.Engine = mig.engine
    migrate.upgrade(engine)
    return engine


# ================================================================================ the index set


def test_access_pattern_indexes_exist(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """The set of CREATE INDEX indexes is exactly the 13 of batches A to D and the 25 of revision
    0002, with these key columns in this order, none unique or partial or descending among the new
    ones. The same facts hold in ``metadata``, in the schema ``metadata.create_all`` builds and in
    the schema Alembic builds."""
    declared = metadata_indexes(evaldb.metadata)
    assert set(declared) == ALL_INDEX_NAMES, sorted(set(declared) ^ ALL_INDEX_NAMES)
    for name, (table, columns) in NEW_INDEXES.items():
        info = declared[name]
        assert (info.table, info.columns) == (table, columns), name
        assert not info.unique, name
        assert not info.partial, name
        assert not any(info.descending), name
        assert name == f"ix_{table}_{'_'.join(columns)}", name  # the naming rule
    for name, (table, columns, unique, partial) in OLD_INDEXES.items():
        info = declared[name]
        assert (info.table, info.columns, info.unique, info.partial) == (
            table,
            columns,
            unique,
            partial,
        ), name
    assert len(NEW_INDEXES) == 25
    assert len(OLD_INDEXES) == 13

    migrated = db_indexes(_migrated(mig, migrate))
    assert set(migrated) == ALL_INDEX_NAMES, sorted(set(migrated) ^ ALL_INDEX_NAMES)
    assert migrated == declared  # name, table, columns in order, unique, partial, direction

    reference = mig.sibling()
    evaldb.metadata.create_all(reference.engine)
    created = db_indexes(reference.engine)
    assert created == declared


def test_every_foreign_key_is_led_by_a_key_or_an_index(evaldb: ModuleType) -> None:
    """Deleting a parent row looks up its children by the foreign key columns; without a leading
    key or index that is a scan of the child table per deleted row (SQLite shows twelve scans for
    one DELETE FROM run). Every foreign key's columns lead the primary key, a UNIQUE constraint or
    a CREATE INDEX index of the child table."""
    metadata: sa.MetaData = evaldb.metadata
    by_table: dict[str, set[tuple[str, ...]]] = {}
    for info in metadata_indexes(metadata).values():
        by_table.setdefault(info.table, set()).add(info.columns)
    checked = 0
    for table in metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            columns = tuple(fk.parent.name for fk in constraint.elements)
            assert key_prefix_covered(table, columns, by_table.get(table.name, set())), (
                table.name,
                columns,
            )
            checked += 1
    assert checked == 23  # ADR 0020 Decision 3


def test_no_index_duplicates_a_key_or_another_index(evaldb: ModuleType) -> None:
    """A plain index whose columns lead the primary key, a UNIQUE constraint or another plain index
    of its table is dead weight on every write. (Partial and unique indexes are exempt: their
    meaning differs.)"""
    metadata: sa.MetaData = evaldb.metadata
    plain = {n: i for n, i in metadata_indexes(metadata).items() if not i.unique and not i.partial}
    for name, info in plain.items():
        table = metadata.tables[info.table]
        keys = [tuple(c.name for c in table.primary_key.columns)]
        keys += [
            tuple(c.name for c in u.columns)
            for u in table.constraints
            if isinstance(u, sa.UniqueConstraint)
        ]
        for key in keys:
            assert key[: len(info.columns)] != info.columns, f"{name} duplicates a key"
        for other_name, other in plain.items():
            if other_name != name and other.table == info.table:
                assert other.columns[: len(info.columns)] != info.columns, (
                    f"{name} duplicates {other_name}"
                )


# ============================================================================= the access patterns


@dataclass(frozen=True)
class Pattern:
    """One access pattern of ADR 0019 Decision 15: a statement, the table it reads and the index
    that serves it. ``index`` is a name, PRIMARY_KEY (the table's key serves it) or ANY_INDEX;
    ``sqlite_index`` overrides it on SQLite (an unnamed UNIQUE-constraint index). ``ordered``
    asserts that the order comes from the index (no separate sort step)."""

    name: str
    table: str
    build: Any  # Callable[[sa.MetaData], sa.Select[Any]]
    index: str
    sqlite_index: str | None = None
    ordered: bool = False
    alternatives: tuple[str, ...] = ()  # Postgres only: other indexes that serve it equally


def _t(m: sa.MetaData, name: str) -> sa.Table:
    return m.tables[name]


def _by(table: str, *pairs: tuple[str, Any]) -> Any:
    def build(m: sa.MetaData) -> sa.Select[Any]:
        t = _t(m, table)
        return sa.select(t.c[pairs[0][0]]).where(*[t.c[c] == v for c, v in pairs])

    return build


def _keyset(m: sa.MetaData) -> sa.Select[Any]:
    run = _t(m, "run")
    return (
        sa.select(run.c.id)
        .where(run.c.project_id == _fresh(), run.c.id > _fresh(1_800_000_001_000))
        .order_by(run.c.id)
        .limit(50)
    )


def _keyset_desc(m: sa.MetaData) -> sa.Select[Any]:
    run = _t(m, "run")
    return (
        sa.select(run.c.id)
        .where(run.c.project_id == _fresh(), run.c.id < _fresh(1_800_000_001_000))
        .order_by(run.c.id.desc())
        .limit(50)
    )


def _members_of_definition(m: sa.MetaData) -> sa.Select[Any]:
    t = _t(m, "snapshot_member")
    return sa.select(t.c.snapshot_id).where(
        t.c.kind == "evaluator", t.c.def_id == _fresh(), t.c.def_version == 2
    )


def _started_between(m: sa.MetaData) -> sa.Select[Any]:
    run = _t(m, "run")
    return sa.select(run.c.id).where(run.c.started_at >= NOW, run.c.started_at < LATER)


def _expired(table: str, column: str) -> Any:
    def build(m: sa.MetaData) -> sa.Select[Any]:
        t = _t(m, table)
        return sa.select(t.c.id).where(t.c[column] < NOW)

    return build


def _metric_prefix(*columns: str) -> Any:
    values: dict[str, Any] = {"metric_id": _fresh(), "metric_version": 1, "status": "passed"}

    def build(m: sa.MetaData) -> sa.Select[Any]:
        t = _t(m, "metric_result")
        return sa.select(t.c.id).where(*[t.c[c] == values[c] for c in columns])

    return build


def _latest(table: str) -> Any:
    def build(m: sa.MetaData) -> sa.Select[Any]:
        t = _t(m, table)
        return sa.select(sa.func.max(t.c.version)).where(t.c.id == _fresh())

    return build


def _newest_first(table: str) -> Any:
    def build(m: sa.MetaData) -> sa.Select[Any]:
        t = _t(m, table)
        return (
            sa.select(t.c.version).where(t.c.id == _fresh()).order_by(t.c.version.desc()).limit(1)
        )

    return build


def _snapshot_digest(m: sa.MetaData) -> sa.Select[Any]:
    t = _t(m, "config_snapshot")
    return sa.select(t.c.id).where(
        t.c.content_digest_scheme == "sha256-jcs-v1", t.c.content_digest_value == SHA
    )


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "runs-by-project-keyset-ascending",
        "run",
        _keyset,
        "ix_run_project_id_id",
        ordered=True,
        alternatives=("run_pkey",),
    ),
    Pattern(
        "runs-by-project-keyset-descending",
        "run",
        _keyset_desc,
        "ix_run_project_id_id",
        ordered=True,
        alternatives=("run_pkey",),
    ),
    Pattern(
        "runs-by-agent-target-version",
        "run",
        _by("run", ("agent_target_id", _fresh()), ("agent_target_version", 3)),
        "ix_run_agent_target_id_agent_target_version",
    ),
    Pattern(
        "runs-by-agent-target-only",
        "run",
        _by("run", ("agent_target_id", _fresh())),
        "ix_run_agent_target_id_agent_target_version",
    ),
    Pattern("runs-by-mode", "run", _by("run", ("mode", "recorded")), "ix_run_mode"),
    Pattern(
        "runs-by-observed-tier", "run", _by("run", ("observed_tier", "T1")), "ix_run_observed_tier"
    ),
    Pattern("runs-started-between", "run", _started_between, "ix_run_started_at"),
    Pattern(
        "runs-by-snapshot",
        "run",
        _by("run", ("config_snapshot_id", _fresh())),
        "ix_run_config_snapshot_id",
    ),
    Pattern(
        "snapshot-members-of-a-definition",
        "snapshot_member",
        _members_of_definition,
        "ix_snapshot_member_kind_def_id_def_version",
    ),
    Pattern(
        "artifacts-by-run", "artifact", _by("artifact", ("run_id", _fresh())), "ix_artifact_run_id"
    ),
    Pattern(
        "artifacts-by-sha256", "artifact", _by("artifact", ("sha256", SHA)), "ix_artifact_sha256"
    ),
    Pattern(
        "transcripts-by-run",
        "transcript",
        _by("transcript", ("run_id", _fresh())),
        "ix_transcript_run_id",
    ),
    Pattern(
        "turns-by-transcript-and-seq",
        "turn",
        _by("turn", ("transcript_id", _fresh()), ("seq", 4)),
        "uq_turn_transcript_seq",
        sqlite_index=ANY_INDEX,
    ),
    Pattern(
        "turns-of-a-transcript-in-order",
        "turn",
        lambda m: (
            sa.select(_t(m, "turn").c.id)
            .where(_t(m, "turn").c.transcript_id == _fresh())
            .order_by(_t(m, "turn").c.seq)
        ),
        "uq_turn_transcript_seq",
        sqlite_index=ANY_INDEX,
        ordered=True,
    ),
    Pattern("turns-by-run", "turn", _by("turn", ("run_id", _fresh())), "ix_turn_run_id"),
    Pattern(
        "exchanges-by-run", "exchange", _by("exchange", ("run_id", _fresh())), "ix_exchange_run_id"
    ),
    Pattern(
        "tool-calls-by-run",
        "tool_call_event",
        _by("tool_call_event", ("run_id", _fresh())),
        "ix_tool_call_event_run_id",
    ),
    Pattern(
        "spans-by-run",
        "stage_span",
        _by("stage_span", ("run_id", _fresh())),
        "ix_stage_span_run_id",
    ),
    Pattern(
        "results-by-run",
        "metric_result",
        _by("metric_result", ("run_id", _fresh())),
        "ix_metric_result_run_id",
    ),
    Pattern(
        "results-by-metric",
        "metric_result",
        _metric_prefix("metric_id"),
        "ix_metric_result_metric_id_metric_version_status",
    ),
    Pattern(
        "results-by-metric-version",
        "metric_result",
        _metric_prefix("metric_id", "metric_version"),
        "ix_metric_result_metric_id_metric_version_status",
    ),
    Pattern(
        "results-by-metric-version-status",
        "metric_result",
        _metric_prefix("metric_id", "metric_version", "status"),
        "ix_metric_result_metric_id_metric_version_status",
    ),
    Pattern(
        "verdicts-by-status",
        "run_verdict",
        _by("run_verdict", ("status", "failed")),
        "ix_run_verdict_status",
    ),
    Pattern(
        "verdicts-by-run",
        "run_verdict",
        _by("run_verdict", ("run_id", _fresh())),
        "ix_run_verdict_run_id",
    ),
    Pattern(
        "usage-by-run",
        "usage_record",
        _by("usage_record", ("run_id", _fresh())),
        "ix_usage_record_run_id",
    ),
    Pattern(
        "cost-records-by-run",
        "cost_record",
        _by("cost_record", ("run_id", _fresh())),
        "ix_cost_record_run_id",
    ),
    Pattern(
        "cost-summaries-by-run",
        "cost_summary",
        _by("cost_summary", ("run_id", _fresh())),
        "ix_cost_summary_run_id",
    ),
    Pattern(
        "snapshot-by-digest",
        "config_snapshot",
        _snapshot_digest,
        "ix_config_snapshot_content_digest_value",
    ),
    Pattern(
        "metric-definition-by-key",
        "metric_definition",
        _by("metric_definition", ("key", "wer")),
        "ix_metric_definition_key",
    ),
    Pattern(
        "dataset-members-by-item",
        "dataset_member",
        _by("dataset_member", ("item_id", _fresh())),
        "ix_dataset_member_item_id",
    ),
    Pattern(
        "dataset-items-by-artifact",
        "dataset_item",
        _by("dataset_item", ("artifact_id", _fresh())),
        "ix_dataset_item_artifact_id",
    ),
    Pattern(
        "dataset-items-by-transcript",
        "dataset_item",
        _by("dataset_item", ("transcript_id", _fresh())),
        "ix_dataset_item_transcript_id",
    ),
    Pattern(
        "run-bundle-by-run",
        "run_bundle",
        _by("run_bundle", ("run_id", _fresh())),
        "ix_run_bundle_run_id",
    ),
    Pattern(
        "bundles-holding-a-record",
        "run_bundle_member",
        _by("run_bundle_member", ("kind", "turn"), ("record_id", _fresh())),
        "ix_run_bundle_member_kind_record_id",
    ),
    Pattern(
        "run-by-external-ref",
        "external_ref_index",
        _by("external_ref_index", ("entity_kind", "run"), ("key_sha256", SHA)),
        "ux_external_ref_index_run_key",
    ),
    Pattern(
        "child-by-external-ref-within-run",
        "external_ref_index",
        _by(
            "external_ref_index", ("run_id", _fresh()), ("entity_kind", "span"), ("key_sha256", SHA)
        ),
        "ix_external_ref_index_run_id_entity_kind_key_sha256",
    ),
    Pattern(
        "snapshots-using-a-profile",
        "snapshot_profile_ref",
        _by("snapshot_profile_ref", ("profile_id", _fresh()), ("profile_version", 1)),
        "ix_snapshot_profile_ref_profile_id_profile_version",
    ),
    Pattern(
        "exports-of-a-run",
        "export_bundle_run",
        _by("export_bundle_run", ("run_id", _fresh())),
        "ix_export_bundle_run_run_id",
    ),
    Pattern(
        "exports-of-an-item",
        "export_bundle_item",
        _by("export_bundle_item", ("item_id", _fresh())),
        "ix_export_bundle_item_item_id",
    ),
    Pattern(
        "exports-of-a-profile",
        "export_bundle_profile",
        _by("export_bundle_profile", ("profile_id", _fresh())),
        "ix_export_bundle_profile_profile_id",
    ),
    Pattern(
        "artifacts-expired",
        "artifact",
        _expired("artifact", "retain_until"),
        "ix_artifact_retain_until",
    ),
    Pattern(
        "transcripts-expired",
        "transcript",
        _expired("transcript", "retain_until"),
        "ix_transcript_retain_until",
    ),
    Pattern(
        "dataset-items-expired",
        "dataset_item",
        _expired("dataset_item", "retain_until"),
        "ix_dataset_item_retain_until",
    ),
    Pattern(
        "runs-expired", "run", _expired("run", "caller_retain_until"), "ix_run_caller_retain_until"
    ),
    *(Pattern(f"latest-version-of-{t}", t, _latest(t), PRIMARY_KEY) for t in VERSIONED),
    *(
        Pattern(f"newest-version-first-of-{t}", t, _newest_first(t), PRIMARY_KEY, ordered=True)
        for t in VERSIONED
    ),
)

PATTERN_IDS = [p.name for p in PATTERNS]


def test_the_pattern_table_covers_every_index_and_every_versioned_table() -> None:
    """The plan tests below name every index of the store at least once, so an index nobody can
    use, or a pattern with no index, shows here."""
    named = {p.index for p in PATTERNS if p.index.startswith(("ix_", "ux_"))}
    assert named == ALL_INDEX_NAMES, sorted(named ^ ALL_INDEX_NAMES)
    assert len(set(PATTERN_IDS)) == len(PATTERNS)
    assert {p.table for p in PATTERNS if p.index == PRIMARY_KEY} == set(VERSIONED)


def _check_plan(engine: sa.Engine, evaldb: ModuleType, pattern: Pattern) -> None:
    metadata: sa.MetaData = evaldb.metadata
    plan: Plan = explain(engine, pattern.build(metadata), ordered=pattern.ordered)
    sqlite = engine.dialect.name == "sqlite"
    assert not plan.scans & set(metadata.tables), (pattern.name, plan.scans, plan.text)
    wanted = (pattern.sqlite_index or pattern.index) if sqlite else pattern.index
    if wanted == PRIMARY_KEY:
        if sqlite:
            assert plan.primary_key, (pattern.name, plan.text)
        else:
            assert f"{pattern.table}_pkey" in plan.indexes, (pattern.name, plan.indexes)
    elif wanted == ANY_INDEX:
        assert plan.indexes or plan.primary_key, (pattern.name, plan.text)
    else:
        accepted = {wanted} | (set(pattern.alternatives) if not sqlite else set())
        assert plan.indexes & accepted, (pattern.name, plan.indexes, plan.text)
    if pattern.ordered:
        assert not plan.sort, (pattern.name, "a separate sort step", plan.text)


@pytest.mark.parametrize("pattern", PATTERNS, ids=PATTERN_IDS)
def test_sqlite_query_plans_use_indexes(
    sqlite_mig: MigStore, migrate: ModuleType, evaldb: ModuleType, pattern: Pattern
) -> None:
    """EXPLAIN QUERY PLAN on the schema Alembic builds: the statement is served by the named index
    (or the key), reads no store table without an index and, where the order matters, needs no
    sort."""
    _check_plan(_migrated(sqlite_mig, migrate), evaldb, pattern)


@pytest.mark.postgres
@pytest.mark.parametrize("pattern", PATTERNS, ids=PATTERN_IDS)
def test_postgres_plans_can_use_indexes(
    postgres_mig: MigStore, migrate: ModuleType, evaldb: ModuleType, pattern: Pattern
) -> None:
    """EXPLAIN (FORMAT JSON) with ``enable_seqscan = off`` on the schema Alembic builds: the plan
    names the index (a plan that CAN use it; which index wins on real data is the planner's), has
    no sequential scan of a store table and, where the order matters, no sort node."""
    _check_plan(_migrated(postgres_mig, migrate), evaldb, pattern)


# ======================================================================== the statements sent


def _scenario(engine: sa.Engine, evaldb: ModuleType) -> None:
    """Store, read, look up and delete through every public function that takes a WHERE clause."""
    doc = run_bundle_doc(tier="T1", origin="simulated")
    doc["run"]["external_refs"] = [{"system": "pipecat", "kind": "call_sid", "value": "CA-1"}]
    first = load_run_bundle_doc(doc)
    store_bundle(engine, evaldb, first)
    second = populate(engine)
    other = second.bundle
    evaldb.load_run_bundle(engine, first.id)
    evaldb.load_project(engine, first.run.project_id)
    evaldb.load_snapshot(engine, first.snapshot.id)
    evaldb.load_definition(
        engine, "persona", first.snapshot.persona.id, first.snapshot.persona.version
    )
    evaldb.latest_version(engine, "persona", first.snapshot.persona.id)
    evaldb.find_snapshot_by_digest(engine, first.snapshot.content_digest)
    from tracer_evalschema import ids as ev_ids

    ref = ev_ids.ExternalRef.model_validate(first.run.external_refs[0])
    evaldb.find_by_external_ref(engine, "run", ref)
    for span in first.spans:
        if span.span_ref is not None:
            evaldb.find_by_external_ref(engine, "span", span.span_ref, run_id=first.run.id)
    who = {"actor": "qa", "reason": "operator_request"}
    with pytest.raises(evaldb.SnapshotInUse):
        evaldb.delete_snapshot(engine, first.snapshot.id, **who)
    evaldb.delete_export(engine, second.export.id, **who)
    evaldb.delete_definition(engine, "agent_target", _golden_id("AgentTarget"), **who)
    evaldb.delete_test_profile(engine, second.ids["profile"].id, **who)
    evaldb.delete_run(engine, other.run.id, **who)
    evaldb.delete_project(engine, first.run.project_id, **who)


def _golden_id(entity: str) -> Any:
    from evaldb_support import load_golden  # type: ignore[import-not-found,unused-ignore]

    return load_golden(entity, "minimal").id


def test_repository_and_deletion_statements_are_served_by_indexes(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """Every statement with a WHERE clause that the repository functions and the seven deletion
    roots send is explained, with the parameters it was sent with: none reads a store table
    without an index. (On SQLite the plan of a DELETE also shows the scans its foreign key actions
    make; on Postgres test_every_foreign_key_is_led_by_a_key_or_an_index carries that.)"""
    engine = _migrated(mig, migrate)
    with capture_statements(engine) as statements:
        _scenario(engine, evaldb)
    assert len(statements) > 100  # the scenario really ran
    offences = scans_of(engine, statements, set(evaldb.metadata.tables))
    assert offences == [], offences


# ============================================================================= keyset paging


def _runs(
    engine: sa.Engine, evaldb: ModuleType
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID], Rows]:
    """Runs of two projects with UUIDv7 IDs 1 ms apart, inserted in shuffled order."""
    rng = random.Random(20261024)  # noqa: S311  (test data)
    mine, theirs = _fresh(1_800_000_000_001), _fresh(1_800_000_000_002)
    plan = [(1_800_000_100_000 + 7 * n, mine if n % 4 != 3 else theirs) for n in range(30)]
    ids = [(_uuid7(ms, rng), project) for ms, project in plan]
    shuffled = ids[:]
    rng.shuffle(shuffled)
    rows = Rows(evaldb.metadata)
    with engine.begin() as conn:
        for run_id, project in shuffled:
            rows.insert(conn, "run", id=run_id, project_id=project)
    return mine, theirs, [i for i, p in ids if p == mine], rows


def _page(
    engine: sa.Engine, evaldb: ModuleType, project: uuid.UUID, after: uuid.UUID | None, size: int
) -> list[uuid.UUID]:
    run = evaldb.metadata.tables["run"]
    query = sa.select(run.c.id).where(run.c.project_id == project)
    if after is not None:
        query = query.where(run.c.id > after)
    with engine.connect() as conn:
        return list(conn.execute(query.order_by(run.c.id).limit(size)).scalars())


def test_keyset_pagination_runs_by_project_is_time_ordered(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """Paging ``WHERE project_id = :p AND id > :after ORDER BY id LIMIT :n`` returns the runs of the
    project in creation order (a UUIDv7 sorts by its time), each exactly once, none of another
    project, whatever the insertion order and the database collation; a run created while paging
    is not skipped and not repeated; the descending page mirrors it."""
    engine = _migrated(mig, migrate)
    mine, _theirs, expected, rows = _runs(engine, evaldb)
    assert expected == sorted(expected, key=lambda u: u.bytes)
    seen: list[uuid.UUID] = []
    after: uuid.UUID | None = None
    pages = 0
    late: uuid.UUID | None = None
    while True:
        page = _page(engine, evaldb, mine, after, 5)
        if not page:
            break
        pages += 1
        assert len(page) <= 5
        seen.extend(page)
        after = page[-1]
        if pages == 2:  # a newer run appears between two pages
            late = _uuid7(1_800_000_900_000, random.Random(1))  # noqa: S311  (test data)
            with engine.begin() as conn:
                rows.insert(conn, "run", id=late, project_id=mine)
    assert late is not None
    assert seen == [*expected, late]
    assert len(set(seen)) == len(seen)
    stamps = [int.from_bytes(u.bytes[:6], "big") for u in seen]
    assert stamps == sorted(stamps)  # time order, not just byte order
    assert pages == 5  # 23 runs of the project plus the late one, 5 per page
    # the other direction, from the newest
    run = evaldb.metadata.tables["run"]
    with engine.connect() as conn:
        newest_first = list(
            conn.execute(
                sa.select(run.c.id)
                .where(run.c.project_id == mine, run.c.id < late)
                .order_by(run.c.id.desc())
                .limit(5)
            ).scalars()
        )
    assert newest_first == list(reversed(expected))[:5]
    # and the plan of a page reads the index in order, with no sort
    plan = explain(
        engine,
        sa.select(run.c.id)
        .where(run.c.project_id == mine, run.c.id > seen[3])
        .order_by(run.c.id)
        .limit(5),
        ordered=True,
    )
    assert not plan.sort, plan.text
    assert not plan.scans & {"run"}, plan.text
    served_by = {"ix_run_project_id_id"}
    if engine.dialect.name != "sqlite":
        served_by.add("run_pkey")  # equally able to deliver the order on an empty table
    assert plan.indexes & served_by, plan.indexes
