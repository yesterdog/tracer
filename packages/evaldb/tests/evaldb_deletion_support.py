# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Stub-built stores for the Phase 2 batch D deletion tests.

Contract: docs/phase2/api-surface-batch-d.md section 5. Imported with ``from evaldb_deletion_support
import ...`` (the tests directory has no ``__init__.py``; mypy cannot see sibling modules, hence the
``type: ignore`` on those imports). Holds no test.

A :class:`World` inserts minimal valid rows with the ``Rows`` factory of batch B (every NOT NULL
column set, every CHECK satisfied): projects, runs with a chosen number of rows in each run-owned
table, snapshots with member rows, dataset items, exports with their link rows, versioned
definitions. A deletion never decodes a document, so stub rows are enough, and the counts the tests
expect are known by construction.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from evaldb_support import Rows  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import ids as ev_ids

# rows per run-owned table; all different, so a count taken from the wrong table is visible
SIZES: Mapping[str, int] = {
    "artifact": 2,
    "transcript": 1,
    "turn": 3,
    "exchange": 2,
    "tool_call_event": 2,
    "stage_span": 4,
    "metric_result": 5,
    "run_verdict": 1,
    "usage_record": 2,
    "cost_record": 3,
    "cost_summary": 1,
    "run_bundle": 2,
}

RUN_KEYS = (
    "run",
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
    "config_snapshot",
    "dataset_item",
    "export_bundle",
)
PROJECT_KEYS = ("project", *RUN_KEYS)
DEFINITION_KINDS = (
    "agent_target",
    "persona",
    "evaluator",
    "metric_definition",
    "metric_binding",
    "rubric",
    "pricing_catalog",
    "dataset",
)
# root kind -> the exact key set of the audit counts (ADR 0020 Decision 6)
COUNT_KEYS: Mapping[str, tuple[str, ...]] = {
    "run": RUN_KEYS,
    "project": PROJECT_KEYS,
    "test_profile": ("test_profile", "export_bundle"),
    **{kind: (kind,) for kind in DEFINITION_KINDS},
    "dataset_item": ("dataset_item", "export_bundle"),
    "export_bundle": ("export_bundle",),
    "config_snapshot": ("config_snapshot",),
}
ACTOR = "ops-night-shift"
REASON = "operator_request"


@dataclass
class RunIds:
    id: uuid.UUID
    project: uuid.UUID
    snapshot: uuid.UUID
    artifacts: list[uuid.UUID] = field(default_factory=list)
    transcripts: list[uuid.UUID] = field(default_factory=list)
    bundles: list[uuid.UUID] = field(default_factory=list)


class World:
    """Stub rows in a store, with the ids of what was inserted."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine
        self.evaldb = importlib.import_module("tracer_evaldb")
        self.metadata: sa.MetaData = self.evaldb.metadata
        self.rows = Rows(self.metadata)

    def _insert(self, table: str, **values: Any) -> dict[str, Any]:
        with self.engine.begin() as conn:
            inserted: dict[str, Any] = self.rows.insert(conn, table, **values)
        return inserted

    def project(self) -> uuid.UUID:
        found: uuid.UUID = self._insert("project")["id"]
        return found

    def snapshot(self, members: int = 2) -> uuid.UUID:
        """A snapshot with member rows (and a profile reference row)."""
        snapshot_id: uuid.UUID = self._insert("config_snapshot")["id"]
        kinds = ("evaluator", "persona", "rubric", "metric_definition", "metric_binding")
        for ordinal in range(members):
            self._insert(
                "snapshot_member",
                snapshot_id=snapshot_id,
                kind=kinds[ordinal % len(kinds)],
                ordinal=ordinal,
                def_id=ev_ids.uuid7(),
            )
        self._insert("snapshot_profile_ref", snapshot_id=snapshot_id, profile_id=ev_ids.uuid7())
        return snapshot_id

    def run(
        self,
        project: uuid.UUID | None = None,
        snapshot: uuid.UUID | None = None,
        sizes: Mapping[str, int] = SIZES,
    ) -> RunIds:
        project = project if project is not None else self.project()
        snapshot = snapshot if snapshot is not None else self.snapshot()
        run_id: uuid.UUID = self._insert("run", project_id=project, config_snapshot_id=snapshot)[
            "id"
        ]
        found = RunIds(run_id, project, snapshot)
        with self.engine.begin() as conn:
            for table, count in sizes.items():
                for _ in range(count):
                    values = self.rows.insert(conn, table, run_id=run_id)
                    if table == "artifact":
                        found.artifacts.append(values["id"])
                    elif table == "transcript":
                        found.transcripts.append(values["id"])
                    elif table == "run_bundle":
                        found.bundles.append(values["id"])
            for ordinal, bundle in enumerate(found.bundles):
                self.rows.insert(
                    conn,
                    "run_bundle_member",
                    bundle_id=bundle,
                    kind="run",
                    ordinal=ordinal,
                    record_id=run_id,
                )
            for kind in ("run", "span", "transcript"):
                self.rows.insert(
                    conn,
                    "external_ref_index",
                    entity_kind=kind,
                    entity_id=run_id if kind == "run" else ev_ids.uuid7(),
                    key_sha256=os.urandom(32),
                    run_id=run_id,
                )
        return found

    def item(
        self,
        *,
        artifact: uuid.UUID | None = None,
        transcript: uuid.UUID | None = None,
        source_run: uuid.UUID | None = None,
    ) -> uuid.UUID:
        found: uuid.UUID = self._insert(
            "dataset_item", artifact_id=artifact, transcript_id=transcript, source_run_id=source_run
        )["id"]
        return found

    def export(
        self,
        *,
        runs: Iterable[uuid.UUID] = (),
        items: Iterable[uuid.UUID] = (),
        profiles: Iterable[uuid.UUID] = (),
    ) -> uuid.UUID:
        export_id: uuid.UUID = self._insert("export_bundle")["id"]
        for table, column, values in (
            ("export_bundle_run", "run_id", runs),
            ("export_bundle_item", "item_id", items),
            ("export_bundle_profile", "profile_id", profiles),
        ):
            for value in values:
                self._insert(table, export_id=export_id, **{column: value})
        return export_id

    def profile(self, versions: Sequence[int] = (1, 2, 3)) -> uuid.UUID:
        profile_id = ev_ids.uuid7()
        for version in versions:
            self._insert("test_profile", id=profile_id, version=version)
        return profile_id

    def definition(self, kind: str, versions: Sequence[int] = (1, 2, 3)) -> uuid.UUID:
        definition_id = ev_ids.uuid7()
        for version in versions:
            self._insert(kind, id=definition_id, version=version)
        if kind == "dataset":
            for version in versions:
                for ordinal in range(2):
                    self._insert(
                        "dataset_member",
                        dataset_id=definition_id,
                        dataset_version=version,
                        ordinal=ordinal,
                        item_id=ev_ids.uuid7(),
                    )
        return definition_id

    def dataset_listing(self, item_ids: Sequence[uuid.UUID]) -> uuid.UUID:
        dataset_id = ev_ids.uuid7()
        self._insert("dataset", id=dataset_id, version=1)
        for ordinal, item in enumerate(item_ids):
            self._insert(
                "dataset_member",
                dataset_id=dataset_id,
                dataset_version=1,
                ordinal=ordinal,
                item_id=item,
            )
        return dataset_id


# ---------------------------------------------------------------------------------- reading

# (table, column) pairs through which a row can refer to a run, an export, a snapshot ...
REFERENCES: Mapping[str, tuple[tuple[str, str], ...]] = {
    "run": (
        ("run", "id"),
        ("artifact", "run_id"),
        ("transcript", "run_id"),
        ("turn", "run_id"),
        ("exchange", "run_id"),
        ("tool_call_event", "run_id"),
        ("stage_span", "run_id"),
        ("metric_result", "run_id"),
        ("run_verdict", "run_id"),
        ("usage_record", "run_id"),
        ("cost_record", "run_id"),
        ("cost_summary", "run_id"),
        ("run_bundle", "run_id"),
        ("external_ref_index", "run_id"),
        ("export_bundle_run", "run_id"),
        ("dataset_item", "source_run_id"),
    ),
    "snapshot": (
        ("config_snapshot", "id"),
        ("snapshot_member", "snapshot_id"),
        ("snapshot_profile_ref", "snapshot_id"),
        ("run", "config_snapshot_id"),
    ),
    "export": (
        ("export_bundle", "id"),
        ("export_bundle_run", "export_id"),
        ("export_bundle_item", "export_id"),
        ("export_bundle_profile", "export_id"),
    ),
}


def rows_referring(
    engine: sa.Engine, metadata: sa.MetaData, kind: str, value: uuid.UUID
) -> list[str]:
    """``table.column`` of every row that holds this id in a column of ``REFERENCES[kind]``."""
    found: list[str] = []
    with engine.connect() as conn:
        for table, column in REFERENCES[kind]:
            t = metadata.tables[table]
            n = conn.execute(
                sa.select(sa.func.count()).select_from(t).where(t.c[column] == value)
            ).scalar_one()
            if n:
                found.append(f"{table}.{column}")
        conn.rollback()
    return found


def count_where(engine: sa.Engine, metadata: sa.MetaData, table: str, **where: Any) -> int:
    t = metadata.tables[table]
    clause = sa.and_(sa.true(), *[t.c[k] == v for k, v in where.items()])
    with engine.connect() as conn:
        found = conn.execute(sa.select(sa.func.count()).select_from(t).where(clause)).scalar_one()
        conn.rollback()
    return int(found)


def audit_rows(engine: sa.Engine, metadata: sa.MetaData) -> list[dict[str, Any]]:
    t = metadata.tables["deletion_audit"]
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sa.select(t).order_by(t.c.audit_uuid)).mappings()]
        conn.rollback()
    return rows


def counts_of(row: Mapping[str, Any]) -> dict[str, Any]:
    parsed = json.loads(row["counts"])
    assert isinstance(parsed, dict)
    return parsed


def expected_run_counts(run: RunIds, *, snapshot: int, items: int, exports: int) -> dict[str, int]:
    """The audit counts of ``delete_run`` for a run built with ``SIZES``."""
    return {
        "run": 1,
        **SIZES,
        "config_snapshot": snapshot,
        "dataset_item": items,
        "export_bundle": exports,
    }


def merge_counts(*parts: Mapping[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for part in parts:
        for key, value in part.items():
            merged[key] = merged.get(key, 0) + value
    return merged


# ================================================================= the roots, one world each


@dataclass
class Built:
    """What a root test needs: the arguments after the engine, the counts the audit must hold, the
    rows that must be gone (table, column, value) and the rows that must stay."""

    args: tuple[Any, ...]
    expected: dict[str, int]
    gone: list[tuple[str, str, uuid.UUID]]
    kept: list[tuple[str, str, uuid.UUID]]
    unknown: tuple[Any, ...]
    not_stored_kind: str


@dataclass(frozen=True)
class RootSpec:
    id: str
    root_kind: str
    function: str
    build: Any  # Callable[[World], Built]


def _gone_run(run: RunIds) -> list[tuple[str, str, uuid.UUID]]:
    return [
        (t, c, run.id) for t, c in REFERENCES["run"] if (t, c) != ("dataset_item", "source_run_id")
    ]


def _build_run(world: World) -> Built:
    r = world.run()
    r2 = world.run(project=r.project, sizes={"artifact": 1, "turn": 1, "run_bundle": 1})
    bystander = world.run()
    item_a = world.item(artifact=r.artifacts[0])
    item_b = world.item(transcript=r.transcripts[0])
    item_c = world.item(source_run=r.id)  # provenance only: stays
    item_d = world.item(artifact=r2.artifacts[0])  # another run's payload: stays
    item_e = world.item()
    e1 = world.export(runs=[r.id])
    e2 = world.export(runs=[r.id, r2.id])
    e3 = world.export(items=[item_a])
    e4 = world.export(items=[item_c])
    e5 = world.export(runs=[r2.id])
    e6 = world.export(items=[item_d, item_e])
    return Built(
        args=(r.id,),
        expected=expected_run_counts(r, snapshot=1, items=2, exports=3),
        gone=[
            *_gone_run(r),
            ("config_snapshot", "id", r.snapshot),
            ("snapshot_member", "snapshot_id", r.snapshot),
            ("dataset_item", "id", item_a),
            ("dataset_item", "id", item_b),
            ("export_bundle", "id", e1),
            ("export_bundle", "id", e2),
            ("export_bundle", "id", e3),
            ("export_bundle_run", "export_id", e1),
            ("export_bundle_run", "export_id", e2),
            ("export_bundle_item", "export_id", e3),
        ],
        kept=[
            ("run", "id", r2.id),
            ("run", "id", bystander.id),
            ("project", "id", r.project),
            ("config_snapshot", "id", r2.snapshot),
            ("dataset_item", "id", item_c),
            ("dataset_item", "id", item_d),
            ("dataset_item", "id", item_e),
            ("export_bundle", "id", e4),
            ("export_bundle", "id", e5),
            ("export_bundle", "id", e6),
            ("export_bundle_run", "export_id", e5),
        ],
        unknown=(ev_ids.uuid7(),),
        not_stored_kind="run",
    )


def _build_project(world: World) -> Built:
    project = world.project()
    r1 = world.run(project=project)
    r2 = world.run(project=project)
    other = world.run()  # another project
    item = world.item(artifact=r1.artifacts[0])
    export_a = world.export(runs=[r2.id])
    export_b = world.export(items=[item])
    export_keep = world.export(runs=[other.id])
    expected = merge_counts(
        expected_run_counts(r1, snapshot=1, items=1, exports=1),
        expected_run_counts(r2, snapshot=1, items=0, exports=1),
        {"project": 1},
    )
    return Built(
        args=(project,),
        expected=expected,
        gone=[
            ("project", "id", project),
            *_gone_run(r1),
            *_gone_run(r2),
            ("config_snapshot", "id", r1.snapshot),
            ("config_snapshot", "id", r2.snapshot),
            ("dataset_item", "id", item),
            ("export_bundle", "id", export_a),
            ("export_bundle", "id", export_b),
        ],
        kept=[
            ("run", "id", other.id),
            ("project", "id", other.project),
            ("config_snapshot", "id", other.snapshot),
            ("export_bundle", "id", export_keep),
        ],
        unknown=(ev_ids.uuid7(),),
        not_stored_kind="project",
    )


def _build_test_profile(world: World) -> Built:
    profile = world.profile((1, 2, 3))
    other = world.profile((1,))
    e1 = world.export(profiles=[profile])
    e2 = world.export(profiles=[profile, other])
    e3 = world.export(profiles=[other])
    snapshot = world.snapshot()
    return Built(
        args=(profile,),
        expected={"test_profile": 3, "export_bundle": 2},
        gone=[
            ("test_profile", "id", profile),
            ("export_bundle", "id", e1),
            ("export_bundle", "id", e2),
            ("export_bundle_profile", "export_id", e1),
            ("export_bundle_profile", "export_id", e2),
        ],
        kept=[
            ("test_profile", "id", other),
            ("export_bundle", "id", e3),
            ("config_snapshot", "id", snapshot),
            ("snapshot_profile_ref", "snapshot_id", snapshot),
        ],
        unknown=(ev_ids.uuid7(),),
        not_stored_kind="test_profile",
    )


def _build_definition(kind: str) -> Any:
    def build(world: World) -> Built:
        target = world.definition(kind, (1, 2, 3))
        other = world.definition(kind, (1,))
        gone: list[tuple[str, str, uuid.UUID]] = [(kind, "id", target)]
        if kind == "dataset":
            gone.append(("dataset_member", "dataset_id", target))
        return Built(
            args=(kind, target),
            expected={kind: 3},
            gone=gone,
            kept=[(kind, "id", other)],
            unknown=(kind, ev_ids.uuid7()),
            not_stored_kind=kind,
        )

    return build


def _build_dataset_item(world: World) -> Built:
    item = world.item()
    other = world.item()
    e1 = world.export(items=[item])
    e2 = world.export(items=[item, other])
    e3 = world.export(items=[other])
    listing = world.dataset_listing([item, other])
    return Built(
        args=(item,),
        expected={"dataset_item": 1, "export_bundle": 2},
        gone=[
            ("dataset_item", "id", item),
            ("export_bundle", "id", e1),
            ("export_bundle", "id", e2),
            ("export_bundle_item", "export_id", e1),
            ("export_bundle_item", "export_id", e2),
        ],
        kept=[
            ("dataset_item", "id", other),
            ("export_bundle", "id", e3),
            ("dataset", "id", listing),
            ("dataset_member", "dataset_id", listing),
        ],
        unknown=(ev_ids.uuid7(),),
        not_stored_kind="dataset_item",
    )


def _build_export(world: World) -> Built:
    r = world.run()
    item = world.item()
    profile = world.profile((1,))
    export = world.export(runs=[r.id], items=[item], profiles=[profile])
    other = world.export(runs=[r.id])
    return Built(
        args=(export,),
        expected={"export_bundle": 1},
        gone=[
            ("export_bundle", "id", export),
            ("export_bundle_run", "export_id", export),
            ("export_bundle_item", "export_id", export),
            ("export_bundle_profile", "export_id", export),
        ],
        kept=[
            ("run", "id", r.id),
            ("dataset_item", "id", item),
            ("test_profile", "id", profile),
            ("export_bundle", "id", other),
        ],
        unknown=(ev_ids.uuid7(),),
        not_stored_kind="export_bundle",
    )


def _build_snapshot(world: World) -> Built:
    snapshot = world.snapshot(members=3)
    used = world.run()
    return Built(
        args=(snapshot,),
        expected={"config_snapshot": 1},
        gone=[
            ("config_snapshot", "id", snapshot),
            ("snapshot_member", "snapshot_id", snapshot),
            ("snapshot_profile_ref", "snapshot_id", snapshot),
        ],
        kept=[("config_snapshot", "id", used.snapshot), ("run", "id", used.id)],
        unknown=(ev_ids.uuid7(),),
        not_stored_kind="config_snapshot",
    )


ROOTS: tuple[RootSpec, ...] = (
    RootSpec("run", "run", "delete_run", _build_run),
    RootSpec("project", "project", "delete_project", _build_project),
    RootSpec("test_profile", "test_profile", "delete_test_profile", _build_test_profile),
    *(
        RootSpec(kind, kind, "delete_definition", _build_definition(kind))
        for kind in DEFINITION_KINDS
    ),
    RootSpec("dataset_item", "dataset_item", "delete_dataset_item", _build_dataset_item),
    RootSpec("export_bundle", "export_bundle", "delete_export", _build_export),
    RootSpec("config_snapshot", "config_snapshot", "delete_snapshot", _build_snapshot),
)
ROOT_IDS = [spec.id for spec in ROOTS]


def call(
    evaldb: Any,
    engine: sa.Engine,
    spec: RootSpec,
    args: Sequence[Any],
    *,
    actor: str = ACTOR,
    reason: str = REASON,
) -> Any:
    return getattr(evaldb, spec.function)(engine, *args, actor=actor, reason=reason)


def full_counts(root_kind: str, expected: Mapping[str, int]) -> dict[str, int]:
    """The expected audit counts with every key of the root kind (zeros included)."""
    keys = COUNT_KEYS[root_kind]
    assert set(expected) <= set(keys), set(expected) - set(keys)
    return {key: expected.get(key, 0) for key in keys}


def exists(engine: sa.Engine, metadata: sa.MetaData, table: str, column: str, value: Any) -> bool:
    return count_where(engine, metadata, table, **{column: value}) > 0


# The rows each root removes from the whole store, by table, for the world of its builder:
# entity tables and side tables alike (the audit counts only the entity tables), and one audit row
# added. These literals are the parity table: both backends must produce exactly them.
_RUN_DELTA = {
    "run": 1,
    "artifact": 2,
    "transcript": 1,
    "turn": 3,
    "exchange": 2,
    "tool_call_event": 2,
    "stage_span": 4,
    "metric_result": 5,
    "run_verdict": 1,
    "usage_record": 2,
    "cost_record": 3,
    "cost_summary": 1,
    "run_bundle": 2,
    "config_snapshot": 1,
    "dataset_item": 2,
    "export_bundle": 3,
    "snapshot_member": 2,
    "snapshot_profile_ref": 1,
    "run_bundle_member": 2,
    "external_ref_index": 3,
    "export_bundle_run": 3,
    "export_bundle_item": 1,
    "deletion_audit": -1,
}
DELTAS: Mapping[str, Mapping[str, int]] = {
    "run": _RUN_DELTA,
    "project": {
        "project": 1,
        "run": 2,
        "artifact": 4,
        "transcript": 2,
        "turn": 6,
        "exchange": 4,
        "tool_call_event": 4,
        "stage_span": 8,
        "metric_result": 10,
        "run_verdict": 2,
        "usage_record": 4,
        "cost_record": 6,
        "cost_summary": 2,
        "run_bundle": 4,
        "config_snapshot": 2,
        "dataset_item": 1,
        "export_bundle": 2,
        "snapshot_member": 4,
        "snapshot_profile_ref": 2,
        "run_bundle_member": 4,
        "external_ref_index": 6,
        "export_bundle_run": 1,
        "export_bundle_item": 1,
        "deletion_audit": -1,
    },
    "test_profile": {
        "test_profile": 3,
        "export_bundle": 2,
        "export_bundle_profile": 3,
        "deletion_audit": -1,
    },
    **{kind: {kind: 3, "deletion_audit": -1} for kind in DEFINITION_KINDS if kind != "dataset"},
    "dataset": {"dataset": 3, "dataset_member": 6, "deletion_audit": -1},
    "dataset_item": {
        "dataset_item": 1,
        "export_bundle": 2,
        "export_bundle_item": 3,
        "deletion_audit": -1,
    },
    "export_bundle": {
        "export_bundle": 1,
        "export_bundle_run": 1,
        "export_bundle_item": 1,
        "export_bundle_profile": 1,
        "deletion_audit": -1,
    },
    "config_snapshot": {
        "config_snapshot": 1,
        "snapshot_member": 3,
        "snapshot_profile_ref": 1,
        "deletion_audit": -1,
    },
}


def removed(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    """Rows removed per table (negative: added), tables without a change left out."""
    return {
        table: before[table] - after.get(table, 0)
        for table in before
        if before[table] != after.get(table, 0)
    }
