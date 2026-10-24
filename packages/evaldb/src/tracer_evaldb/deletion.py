# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Deletion roots, application-level cascades and the deletion audit (ADR 0020).

Each ``delete_*`` function removes one root and what depends on it in a single transaction with
its audit row. The audit row is content-free: the actor, the reason, the kind of root and the
number of rows removed per kind, never an ID or a value. On Postgres the root and the rows the
cascade reads are locked ``FOR UPDATE`` first; on SQLite the transaction starts ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from tracer_evalschema import ids as ev_ids
from tracer_evalschema.credentials import credential_reason
from tracer_evalschema.text import _check_label

from .engine import BEGIN_OPTION
from .errors import RecordNotStored, SnapshotInUse
from .repository import _engine, _typed_refusals, _uuid_of, translate_integrity_error
from .schema import metadata

__all__ = [
    "DELETION_REASONS",
    "DeletionResult",
    "delete_dataset_item",
    "delete_definition",
    "delete_export",
    "delete_project",
    "delete_run",
    "delete_snapshot",
    "delete_test_profile",
]

DELETION_REASONS = ("operator_request", "retention_policy", "data_subject_request")

# Rows owned by a run (they go with it through ON DELETE CASCADE) and counted one by one.
_RUN_OWNED = (
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
)
RUN_KEYS = ("run", *_RUN_OWNED, "config_snapshot", "dataset_item", "export_bundle")

_DEFINITION_IDS: Mapping[str, type[ev_ids.TypedId]] = {
    "agent_target": ev_ids.AgentTargetId,
    "persona": ev_ids.PersonaId,
    "evaluator": ev_ids.EvaluatorId,
    "metric_definition": ev_ids.MetricDefinitionId,
    "metric_binding": ev_ids.MetricBindingId,
    "rubric": ev_ids.RubricId,
    "pricing_catalog": ev_ids.PricingCatalogId,
    "dataset": ev_ids.DatasetId,
}


@dataclass(frozen=True)
class DeletionResult:
    root_kind: str
    counts: Mapping[str, int]
    audit_uuid: uuid.UUID


# A machine label, not a person's name: the audit row is content-free and kept for good.
_ACTOR_RULE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _check_actor_and_reason(actor: object, reason: object) -> tuple[str, str]:
    if not isinstance(actor, str):
        raise TypeError("actor must be a str")
    if not isinstance(reason, str):
        raise TypeError("reason must be a str")
    if reason not in DELETION_REASONS:
        raise ValueError(f"reason must be one of: {', '.join(DELETION_REASONS)}")
    if not actor:
        raise ValueError("actor must not be empty")
    try:
        _check_label(actor)
    except ValueError:
        raise ValueError("actor is not a valid label: no control, separator or invisible") from None
    if _ACTOR_RULE.fullmatch(actor) is None:
        raise ValueError("actor must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
    flagged = credential_reason(actor)
    if flagged is not None:
        raise ValueError(f"actor looks like a credential (rule: {flagged})")
    return actor, reason


def _table(name: str) -> sa.Table:
    return metadata.tables[name]


def _locked(connection: Connection, select: sa.Select[Any]) -> sa.Select[Any]:
    return select.with_for_update() if connection.dialect.name == "postgresql" else select


def _count(connection: Connection, table: sa.Table, where: Any) -> int:
    return int(
        connection.execute(sa.select(sa.func.count()).select_from(table).where(where)).scalar_one()
    )


class _Deletion:
    """One deletion in progress: the counts of rows removed per kind."""

    def __init__(self, connection: Connection, keys: Sequence[str]) -> None:
        self.connection = connection
        self.counts = dict.fromkeys(keys, 0)
        self.audit_uuid = ev_ids.uuid7()

    def lock_ids(self, table: sa.Table, where: Any) -> list[Any]:
        """Select (and on Postgres lock) the IDs of the rows matching ``where``."""
        select = _locked(self.connection, sa.select(table.c.id).where(where))
        return list(self.connection.execute(select).scalars())

    def delete(self, table: sa.Table, where: Any) -> None:
        self.connection.execute(sa.delete(table).where(where))

    def result(self, kind: str) -> DeletionResult:
        return DeletionResult(kind, MappingProxyType(dict(self.counts)), self.audit_uuid)


@contextlib.contextmanager
def _transaction(
    engine: Engine, actor: str, reason: str, kind: str, keys: Sequence[str]
) -> Iterator[_Deletion]:
    """The deletion's transaction; on a clean exit the audit row is written inside it."""
    try:
        options = {BEGIN_OPTION: "BEGIN IMMEDIATE"}
        with engine.connect().execution_options(**options) as connection, connection.begin():
            deletion = _Deletion(connection, keys)
            yield deletion
            connection.execute(
                sa.insert(_table("deletion_audit")).values(
                    audit_uuid=deletion.audit_uuid,
                    deleted_at=dt.datetime.now(dt.UTC),
                    actor=actor,
                    reason=reason,
                    root_kind=kind,
                    counts=json.dumps(deletion.counts, sort_keys=True, separators=(",", ":")),
                )
            )
    except sa.exc.IntegrityError as error:
        raise translate_integrity_error(error) from None


def _require(found: Sequence[Any], kind: str, record_id: ev_ids.TypedId) -> None:
    if not found:
        raise RecordNotStored(kind, record_id)


def _delete_exports(deletion: _Deletion, export_ids: set[Any]) -> int:
    if export_ids:
        export = _table("export_bundle")
        deletion.lock_ids(export, export.c.id.in_(export_ids))
        deletion.delete(export, export.c.id.in_(export_ids))
    return len(export_ids)


def _cascade_run(deletion: _Deletion, run_uuid: uuid.UUID, snapshot_uuid: Any) -> None:
    """Delete one (already locked) run with everything ADR 0020 Decision 4 attaches to it."""
    connection = deletion.connection
    run, item = _table("run"), _table("dataset_item")
    artifact, transcript = _table("artifact"), _table("transcript")
    link_run, link_item = _table("export_bundle_run"), _table("export_bundle_item")

    artifact_ids = list(
        connection.execute(sa.select(artifact.c.id).where(artifact.c.run_id == run_uuid)).scalars()
    )
    transcript_ids = list(
        connection.execute(
            sa.select(transcript.c.id).where(transcript.c.run_id == run_uuid)
        ).scalars()
    )
    holds = [
        column.in_(ids)
        for column, ids in (
            (item.c.artifact_id, artifact_ids),
            (item.c.transcript_id, transcript_ids),
        )
        if ids
    ]
    item_ids = deletion.lock_ids(item, sa.or_(*holds)) if holds else []

    export_ids = set(
        connection.execute(
            sa.select(link_run.c.export_id).where(link_run.c.run_id == run_uuid)
        ).scalars()
    )
    if item_ids:
        export_ids |= set(
            connection.execute(
                sa.select(link_item.c.export_id).where(link_item.c.item_id.in_(item_ids))
            ).scalars()
        )
    exports = _delete_exports(deletion, export_ids)
    if item_ids:
        deletion.delete(item, item.c.id.in_(item_ids))

    for name in _RUN_OWNED:
        owned = _table(name)
        deletion.counts[name] += _count(connection, owned, owned.c.run_id == run_uuid)
    deletion.delete(run, run.c.id == run_uuid)
    deletion.counts["run"] += 1
    deletion.counts["dataset_item"] += len(item_ids)
    deletion.counts["export_bundle"] += exports

    if _count(connection, run, run.c.config_snapshot_id == snapshot_uuid) == 0:
        snapshot = _table("config_snapshot")
        deletion.delete(snapshot, snapshot.c.id == snapshot_uuid)
        deletion.counts["config_snapshot"] += 1


@_typed_refusals
def delete_run(engine: Engine, run_id: Any, *, actor: str, reason: str) -> DeletionResult:
    """Delete a run, its run-owned rows, the dataset items and exports that carry its content and
    its snapshot when no other run uses it."""
    _engine(engine)
    run_uuid = _uuid_of(run_id, ev_ids.RunId)
    actor, reason = _check_actor_and_reason(actor, reason)
    run, snapshot = _table("run"), _table("config_snapshot")
    with _transaction(engine, actor, reason, "run", RUN_KEYS) as deletion:
        found = deletion.connection.execute(
            _locked(
                deletion.connection,
                sa.select(run.c.config_snapshot_id).where(run.c.id == run_uuid),
            )
        ).first()
        if found is None:
            raise RecordNotStored("run", ev_ids.RunId(run_uuid))
        deletion.lock_ids(snapshot, snapshot.c.id == found[0])
        _cascade_run(deletion, run_uuid, found[0])
    return deletion.result("run")


@_typed_refusals
def delete_project(engine: Engine, project_id: Any, *, actor: str, reason: str) -> DeletionResult:
    """Delete a project and, with the cascades of ``delete_run``, every run of it."""
    _engine(engine)
    project_uuid = _uuid_of(project_id, ev_ids.ProjectId)
    actor, reason = _check_actor_and_reason(actor, reason)
    project, run, snapshot = _table("project"), _table("run"), _table("config_snapshot")
    with _transaction(engine, actor, reason, "project", ("project", *RUN_KEYS)) as deletion:
        connection = deletion.connection
        _require(
            deletion.lock_ids(project, project.c.id == project_uuid),
            "project",
            ev_ids.ProjectId(project_uuid),
        )
        runs = connection.execute(
            _locked(
                connection,
                sa.select(run.c.id, run.c.config_snapshot_id)
                .where(run.c.project_id == project_uuid)
                .order_by(run.c.id),
            )
        ).all()
        # Every snapshot is locked before the first delete, not one run at a time.
        wanted = {row[1] for row in runs}
        if wanted:
            deletion.lock_ids(snapshot, snapshot.c.id.in_(wanted))
        for run_uuid, snapshot_uuid in runs:
            _cascade_run(deletion, run_uuid, snapshot_uuid)
        deletion.delete(project, project.c.id == project_uuid)
        deletion.counts["project"] = 1
    return deletion.result("project")


@_typed_refusals
def delete_test_profile(
    engine: Engine, profile_id: Any, *, actor: str, reason: str
) -> DeletionResult:
    """Delete every version of a test profile and the exports that carry it."""
    _engine(engine)
    profile_uuid = _uuid_of(profile_id, ev_ids.TestProfileId)
    actor, reason = _check_actor_and_reason(actor, reason)
    profile, link = _table("test_profile"), _table("export_bundle_profile")
    keys = ("test_profile", "export_bundle")
    with _transaction(engine, actor, reason, "test_profile", keys) as deletion:
        rows = deletion.lock_ids(profile, profile.c.id == profile_uuid)
        _require(rows, "test_profile", ev_ids.TestProfileId(profile_uuid))
        export_ids = set(
            deletion.connection.execute(
                sa.select(link.c.export_id).where(link.c.profile_id == profile_uuid)
            ).scalars()
        )
        deletion.counts["export_bundle"] = _delete_exports(deletion, export_ids)
        deletion.delete(profile, profile.c.id == profile_uuid)
        deletion.counts["test_profile"] = len(rows)
    return deletion.result("test_profile")


@_typed_refusals
def delete_definition(
    engine: Engine, kind: str, definition_id: Any, *, actor: str, reason: str
) -> DeletionResult:
    """Delete every version of one definition (``kind`` names its table)."""
    _engine(engine)
    if kind == "test_profile":
        raise ValueError("a test profile is deleted by delete_test_profile")
    if not isinstance(kind, str) or kind not in _DEFINITION_IDS:
        raise ValueError(f"kind must be one of: {', '.join(_DEFINITION_IDS)}")
    id_type = _DEFINITION_IDS[kind]
    definition_uuid = _uuid_of(definition_id, id_type)
    actor, reason = _check_actor_and_reason(actor, reason)
    table = _table(kind)
    with _transaction(engine, actor, reason, kind, (kind,)) as deletion:
        rows = deletion.lock_ids(table, table.c.id == definition_uuid)
        _require(rows, kind, id_type(definition_uuid))
        deletion.delete(table, table.c.id == definition_uuid)
        deletion.counts[kind] = len(rows)
    return deletion.result(kind)


@_typed_refusals
def delete_dataset_item(engine: Engine, item_id: Any, *, actor: str, reason: str) -> DeletionResult:
    """Delete a dataset item and the exports that embed it."""
    _engine(engine)
    item_uuid = _uuid_of(item_id, ev_ids.DatasetItemId)
    actor, reason = _check_actor_and_reason(actor, reason)
    item, link = _table("dataset_item"), _table("export_bundle_item")
    keys = ("dataset_item", "export_bundle")
    with _transaction(engine, actor, reason, "dataset_item", keys) as deletion:
        _require(
            deletion.lock_ids(item, item.c.id == item_uuid),
            "dataset_item",
            ev_ids.DatasetItemId(item_uuid),
        )
        export_ids = set(
            deletion.connection.execute(
                sa.select(link.c.export_id).where(link.c.item_id == item_uuid)
            ).scalars()
        )
        deletion.counts["export_bundle"] = _delete_exports(deletion, export_ids)
        deletion.delete(item, item.c.id == item_uuid)
        deletion.counts["dataset_item"] = 1
    return deletion.result("dataset_item")


@_typed_refusals
def delete_export(engine: Engine, export_id: Any, *, actor: str, reason: str) -> DeletionResult:
    """Delete an export and its link rows; what it carried stays."""
    _engine(engine)
    export_uuid = _uuid_of(export_id, ev_ids.ExportBundleId)
    actor, reason = _check_actor_and_reason(actor, reason)
    export = _table("export_bundle")
    with _transaction(engine, actor, reason, "export_bundle", ("export_bundle",)) as deletion:
        _require(
            deletion.lock_ids(export, export.c.id == export_uuid),
            "export_bundle",
            ev_ids.ExportBundleId(export_uuid),
        )
        deletion.delete(export, export.c.id == export_uuid)
        deletion.counts["export_bundle"] = 1
    return deletion.result("export_bundle")


@_typed_refusals
def delete_snapshot(engine: Engine, snapshot_id: Any, *, actor: str, reason: str) -> DeletionResult:
    """Delete a snapshot that no run references (``SnapshotInUse`` otherwise)."""
    _engine(engine)
    snapshot_uuid = _uuid_of(snapshot_id, ev_ids.ConfigSnapshotId)
    actor, reason = _check_actor_and_reason(actor, reason)
    snapshot, run = _table("config_snapshot"), _table("run")
    with _transaction(engine, actor, reason, "config_snapshot", ("config_snapshot",)) as deletion:
        typed = ev_ids.ConfigSnapshotId(snapshot_uuid)
        rows = deletion.lock_ids(snapshot, snapshot.c.id == snapshot_uuid)
        _require(rows, "config_snapshot", typed)
        if _count(deletion.connection, run, run.c.config_snapshot_id == snapshot_uuid):
            raise SnapshotInUse(typed)
        deletion.delete(snapshot, snapshot.c.id == snapshot_uuid)
        deletion.counts["config_snapshot"] = 1
    return deletion.result("config_snapshot")
