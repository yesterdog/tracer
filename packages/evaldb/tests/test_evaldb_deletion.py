# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D, P2-17a: the deletion roots, their cascades and their locks (ADR 0020).

Contract: docs/phase2/api-surface-batch-d.md section 5. Most tests build a store from stub rows
(``evaldb_deletion_support.World``: a deletion never decodes a document, and the counts are known
by construction); the tests that read things back through the repository use real bundles. Every
test runs on both backends through the ``db`` fixture; the concurrency tests branch on the backend
(Postgres waits on a row lock, SQLite refuses a second writer) and pin the same invariant: a writer
that races a deletion never leaves an orphan.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import inspect
import json
import threading
import uuid
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_deletion_support import (  # type: ignore[import-not-found,unused-ignore]
    ACTOR,
    COUNT_KEYS,
    DELTAS,
    REASON,
    ROOTS,
    RootSpec,
    World,
    audit_rows,
    call,
    count_where,
    counts_of,
    exists,
    full_counts,
    removed,
    rows_referring,
)
from evaldb_migration_support import (  # type: ignore[import-not-found,unused-ignore]
    fault_on_statement,
    is_delete,
    pause_on_statement,
    populate,
    raw_dump,
    row_counts,
    wait_reached,
    wait_until_blocked,
)
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    RECORD_LISTS,
    Boom,
    load_run_bundle_doc,
    project_doc,
    recording,
    renew_ids,
    run_bundle_doc,
    run_in_thread,
    store_bundle,
    support,
)
from evaldb_support import Db  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import ids as ev_ids

db = _support.db

FUNCTIONS = (
    "delete_run",
    "delete_project",
    "delete_test_profile",
    "delete_definition",
    "delete_dataset_item",
    "delete_export",
    "delete_snapshot",
)
ID_PARAMETER = {
    "delete_run": "run_id",
    "delete_project": "project_id",
    "delete_test_profile": "profile_id",
    "delete_definition": "definition_id",
    "delete_dataset_item": "item_id",
    "delete_export": "export_id",
    "delete_snapshot": "snapshot_id",
}
ID_CLASS = {
    "delete_run": ev_ids.RunId,
    "delete_project": ev_ids.ProjectId,
    "delete_test_profile": ev_ids.TestProfileId,
    "delete_dataset_item": ev_ids.DatasetItemId,
    "delete_export": ev_ids.ExportBundleId,
    "delete_snapshot": ev_ids.ConfigSnapshotId,
}
SPEC = {spec.id: spec for spec in ROOTS}


def _spec_params() -> list[Any]:
    return [pytest.param(spec, id=spec.id) for spec in ROOTS]


def _expected_counts(spec: RootSpec, built: Any) -> dict[str, int]:
    wanted: dict[str, int] = full_counts(spec.root_kind, built.expected)
    return wanted


# ============================================================================== the API


def test_deletion_api_shape(db: Db, evaldb: ModuleType) -> None:
    deletion = importlib.import_module("tracer_evaldb.deletion")
    assert evaldb.deletion is deletion
    for name in (*FUNCTIONS, "DeletionResult"):
        assert getattr(evaldb, name) is getattr(deletion, name), name
        assert name in evaldb.__all__, name
        assert name in deletion.__all__, name
    for name in FUNCTIONS:
        parameters = inspect.signature(getattr(evaldb, name)).parameters
        expected = ["engine", ID_PARAMETER[name], "actor", "reason"]
        if name == "delete_definition":
            expected = ["engine", "kind", "definition_id", "actor", "reason"]
        assert list(parameters) == expected, name
        for keyword in ("actor", "reason"):  # required, keyword-only, no default (Q13)
            assert parameters[keyword].kind is inspect.Parameter.KEYWORD_ONLY, (name, keyword)
            assert parameters[keyword].default is inspect.Parameter.empty, (name, keyword)
        for positional in expected[:-2]:
            assert parameters[positional].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            assert parameters[positional].default is inspect.Parameter.empty
    result_type = deletion.DeletionResult
    assert getattr(result_type, "__dataclass_fields__", None) is not None
    assert [f.name for f in dataclasses.fields(result_type)] == [
        "root_kind",
        "counts",
        "audit_uuid",
    ]
    assert getattr(result_type, "__dataclass_params__").frozen is True  # noqa: B009
    world = World(db.engine)
    built = ROOTS[-1].build(world)  # a snapshot
    result: Any = evaldb.delete_snapshot(db.engine, *built.args, actor=ACTOR, reason=REASON)
    assert type(result) is result_type
    assert result.root_kind == "config_snapshot"
    assert isinstance(result.audit_uuid, uuid.UUID)
    assert result.audit_uuid.version == 7
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.root_kind = "run"
    with pytest.raises(TypeError):
        result.counts["config_snapshot"] = 5  # a read-only mapping


@pytest.mark.parametrize("name", FUNCTIONS)
def test_deletion_arguments_are_checked_before_any_statement(
    db: Db, evaldb: ModuleType, name: str
) -> None:
    function = getattr(evaldb, name)
    world = World(db.engine)
    for spec in ROOTS:
        spec.build(world)
    is_definition = name == "delete_definition"
    lead: tuple[Any, ...] = ("persona",) if is_definition else ()
    valid_id = ev_ids.uuid7()
    other_class = ev_ids.ProjectId if name != "delete_project" else ev_ids.RunId
    before = raw_dump(db.engine)
    with recording(db.engine) as statements:
        with pytest.raises(TypeError):
            function(None, *lead, valid_id, actor=ACTOR, reason=REASON)
        with pytest.raises(TypeError):
            function("sqlite://", *lead, valid_id, actor=ACTOR, reason=REASON)
        with db.engine.connect() as connection, pytest.raises(TypeError):
            function(connection, *lead, valid_id, actor=ACTOR, reason=REASON)
        for bad in (str(valid_id), valid_id.bytes, valid_id.int, None, [valid_id]):
            with pytest.raises(TypeError):
                function(db.engine, *lead, bad, actor=ACTOR, reason=REASON)
        with pytest.raises(TypeError):  # the ID of another kind of entity
            function(db.engine, *lead, other_class(ev_ids.uuid7()), actor=ACTOR, reason=REASON)
        with pytest.raises(ValueError, match="version 7"):
            function(db.engine, *lead, uuid.uuid4(), actor=ACTOR, reason=REASON)
        if is_definition:
            for bad_kind in (
                "Persona",
                "persona ",
                "",
                "run",
                "project",
                "tracer_meta",
                b"persona",
            ):
                with pytest.raises(ValueError, match="kind"):
                    function(db.engine, bad_kind, valid_id, actor=ACTOR, reason=REASON)
    assert statements.count == 0
    assert raw_dump(db.engine) == before


def test_delete_definition_refuses_test_profile_and_names_the_right_root(
    db: Db, evaldb: ModuleType
) -> None:
    """ADR 0020 Ambiguity 8: a test profile goes through delete_test_profile, the root that also
    deletes the exports carrying it."""
    world = World(db.engine)
    profile = world.profile()
    before = raw_dump(db.engine)
    with (
        recording(db.engine) as statements,
        pytest.raises(ValueError, match="delete_test_profile"),
    ):
        evaldb.delete_definition(db.engine, "test_profile", profile, actor=ACTOR, reason=REASON)
    assert statements.count == 0
    assert raw_dump(db.engine) == before


def test_ids_are_accepted_typed_or_as_bare_version_7_uuids(db: Db, evaldb: ModuleType) -> None:
    world = World(db.engine)
    first, second = world.run(), world.run()
    typed = evaldb.delete_run(db.engine, ev_ids.RunId(first.id), actor=ACTOR, reason=REASON)
    bare = evaldb.delete_run(db.engine, second.id, actor=ACTOR, reason=REASON)
    assert typed.counts["run"] == bare.counts["run"] == 1


# ===================================================================== unknown roots, typed


@pytest.mark.parametrize("spec", _spec_params())
def test_delete_unknown_root_is_typed_error_and_writes_no_audit(
    db: Db, evaldb: ModuleType, spec: RootSpec
) -> None:
    world = World(db.engine)
    for other in ROOTS:  # something is stored, of every kind; none of it may be touched
        other.build(world)
    built = spec.build(world)
    # an id that names a row of another kind of entity is just as unknown as a new one
    stranger = world.project() if spec.function == "delete_run" else world.run().id
    strange = (
        (built.not_stored_kind, stranger) if spec.function == "delete_definition" else (stranger,)
    )
    before = raw_dump(db.engine)
    unknown_id = built.unknown[-1]
    for args in (built.unknown, strange):
        with (
            recording(db.engine) as statements,
            pytest.raises(evaldb.RecordNotStored) as caught,
        ):
            call(evaldb, db.engine, spec, args, actor="Unknown-Root-Actor")
        error = caught.value
        assert error.kind == built.not_stored_kind
        assert error.version is None
        assert isinstance(error, evaldb.StoreError)
        assert "Unknown-Root-Actor" not in str(error)
        assert statements.writes == []  # raised before anything was deleted or audited
        assert raw_dump(db.engine) == before
        assert audit_rows(db.engine, db.metadata) == []
    with pytest.raises(evaldb.RecordNotStored) as caught:
        call(evaldb, db.engine, spec, built.unknown)
    assert str(caught.value.record_id.uuid) == str(unknown_id)


# ====================================================================== the cascades


def test_delete_run_cascades_every_run_owned_kind(db: Db, evaldb: ModuleType) -> None:
    spec = SPEC["run"]
    world = World(db.engine)
    built = spec.build(world)
    before = row_counts(db.engine)
    assert all(before[t] > 0 for t in COUNT_KEYS["run"]), before
    result = call(evaldb, db.engine, spec, built.args)
    assert result.root_kind == "run"
    assert dict(result.counts) == _expected_counts(spec, built)
    after = row_counts(db.engine)
    # not one row more or fewer than the world says, in any table (side tables included)
    assert removed(before, after) == dict(DELTAS["run"])
    for table, column, value in built.gone:
        assert not exists(db.engine, db.metadata, table, column, value), (table, column)
    for table, column, value in built.kept:
        assert exists(db.engine, db.metadata, table, column, value), (table, column)
    # the one reference that remains is the provenance of the item that was kept (O7)
    run_id = built.args[0]
    assert rows_referring(db.engine, db.metadata, "run", run_id) == ["dataset_item.source_run_id"]


def test_delete_run_removes_orphaned_snapshot_only(db: Db, evaldb: ModuleType) -> None:
    world = World(db.engine)
    shared = world.snapshot(members=2)
    first, second = world.run(snapshot=shared), world.run(snapshot=shared)
    third = world.run()
    unreferenced = world.snapshot(members=1)
    persona = world.definition("persona", (1,))  # a definition extracted from some snapshot

    def snapshot_rows(snapshot: uuid.UUID) -> tuple[int, int, int]:
        return (
            count_where(db.engine, db.metadata, "config_snapshot", id=snapshot),
            count_where(db.engine, db.metadata, "snapshot_member", snapshot_id=snapshot),
            count_where(db.engine, db.metadata, "snapshot_profile_ref", snapshot_id=snapshot),
        )

    one = evaldb.delete_run(db.engine, first.id, actor=ACTOR, reason=REASON)
    assert one.counts["config_snapshot"] == 0  # another run still uses it
    assert snapshot_rows(shared) == (1, 2, 1)
    two = evaldb.delete_run(db.engine, second.id, actor=ACTOR, reason=REASON)
    assert two.counts["config_snapshot"] == 1  # the last user is gone: the snapshot goes
    assert snapshot_rows(shared) == (0, 0, 0)
    three = evaldb.delete_run(db.engine, third.id, actor=ACTOR, reason=REASON)
    assert three.counts["config_snapshot"] == 1
    assert snapshot_rows(third.snapshot) == (0, 0, 0)
    # only the run's own snapshot is ever considered: nobody's snapshot, and the definitions
    # extracted from snapshots, stay
    assert snapshot_rows(unreferenced) == (1, 1, 1)
    assert exists(db.engine, db.metadata, "persona", "id", persona)
    for result in (one, two, three):
        assert set(result.counts) == set(COUNT_KEYS["run"])


def test_delete_run_removes_dataset_items_and_dataset_reports_unresolved(
    db: Db, evaldb: ModuleType
) -> None:
    world = World(db.engine)
    run = world.run()
    other = world.run()
    by_artifact = world.item(artifact=run.artifacts[0])
    by_transcript = world.item(transcript=run.transcripts[0])
    by_both = world.item(artifact=run.artifacts[1], transcript=run.transcripts[0])
    provenance = world.item(source_run=run.id)
    foreign = world.item(artifact=other.artifacts[0], source_run=run.id)
    listing = world.dataset_listing([by_artifact, provenance, by_transcript, foreign, by_both])
    dataset_before = {t: raw_dump(db.engine)[t] for t in ("dataset", "dataset_member")}
    result = evaldb.delete_run(db.engine, run.id, actor=ACTOR, reason=REASON)
    assert result.counts["dataset_item"] == 3
    for gone in (by_artifact, by_transcript, by_both):
        assert not exists(db.engine, db.metadata, "dataset_item", "id", gone)
        with pytest.raises(evaldb.RecordNotStored):
            evaldb.load_dataset_item(db.engine, gone)
    # an item whose only link is source_run_id (O7), and one that holds another run's payload, stay
    for kept in (provenance, foreign):
        assert exists(db.engine, db.metadata, "dataset_item", "id", kept)
    # the dataset is an immutable version: byte for byte the same, and it now lists three members
    # that resolve to nothing (a reader reports them; none is rebuilt or edited)
    assert {t: raw_dump(db.engine)[t] for t in ("dataset", "dataset_member")} == dataset_before
    members = [
        uuid_of
        for uuid_of in (by_artifact, provenance, by_transcript, foreign, by_both)
        if not exists(db.engine, db.metadata, "dataset_item", "id", uuid_of)
    ]
    assert members == [by_artifact, by_transcript, by_both]
    assert count_where(db.engine, db.metadata, "dataset_member", dataset_id=listing) == 5


def test_delete_run_removes_stored_exports_containing_it(db: Db, evaldb: ModuleType) -> None:
    world = World(db.engine)
    run, other = world.run(), world.run()
    item = world.item(artifact=run.artifacts[0])
    by_run = world.export(runs=[run.id])
    with_other_run = world.export(runs=[run.id, other.id])
    by_item = world.export(items=[item])
    mixed = world.export(runs=[other.id], items=[item])
    unrelated = world.export(runs=[other.id])
    result = evaldb.delete_run(db.engine, run.id, actor=ACTOR, reason=REASON)
    assert result.counts["export_bundle"] == 4
    for export in (by_run, with_other_run, by_item, mixed):
        assert not exists(db.engine, db.metadata, "export_bundle", "id", export)
        for link in ("export_bundle_run", "export_bundle_item", "export_bundle_profile"):
            assert not exists(db.engine, db.metadata, link, "export_id", export), link
    # an export that carries only another run is not this run's business
    assert exists(db.engine, db.metadata, "export_bundle", "id", unrelated)
    assert exists(db.engine, db.metadata, "export_bundle_run", "export_id", unrelated)


def test_delete_run_of_a_real_stored_bundle_keeps_the_definitions_of_its_snapshot(
    db: Db, evaldb: ModuleType
) -> None:
    """The same cascade on documents the repository wrote: the bundle, its export and its
    snapshot go; the definitions that were extracted from the snapshot stay and still load."""
    populated = populate(db.engine)
    bundle = populated.bundle
    before = row_counts(db.engine)
    result = evaldb.delete_run(db.engine, bundle.run.id, actor=ACTOR, reason=REASON)
    assert result.counts["config_snapshot"] == 1
    assert result.counts["export_bundle"] == 1
    assert result.counts["run_bundle"] == 1
    after = row_counts(db.engine)
    assert after["run"] == after["artifact"] == after["metric_result"] == 0
    assert after["export_bundle"] == after["run_bundle"] == after["config_snapshot"] == 0
    assert after["persona"] == before["persona"] > 0
    assert after["project"] == before["project"] == 1
    assert after["agent_target"] == before["agent_target"] == 1
    with pytest.raises(evaldb.RecordNotStored):
        evaldb.load_run_bundle(db.engine, bundle.id)
    with pytest.raises(evaldb.RecordNotStored):
        evaldb.load_export(db.engine, populated.export.id, expected_purpose="internal")
    persona = bundle.snapshot.persona
    assert evaldb.load_definition(db.engine, "persona", persona.id, persona.version) == persona
    assert evaldb.find_snapshot_by_digest(db.engine, bundle.snapshot.content_digest) is None


def test_delete_project_cascades_runs(db: Db, evaldb: ModuleType) -> None:
    spec = SPEC["project"]
    world = World(db.engine)
    built = spec.build(world)
    before = row_counts(db.engine)
    result = call(evaldb, db.engine, spec, built.args)
    assert result.root_kind == "project"
    assert dict(result.counts) == _expected_counts(spec, built)
    assert removed(before, row_counts(db.engine)) == dict(DELTAS["project"])
    for table, column, value in built.gone:
        assert not exists(db.engine, db.metadata, table, column, value), (table, column)
    for table, column, value in built.kept:
        assert exists(db.engine, db.metadata, table, column, value), (table, column)


def test_delete_project_applies_the_run_cascades_to_every_run_first(
    db: Db, evaldb: ModuleType
) -> None:
    """ADR 0020 Ambiguity 1: a foreign-key cascade runs no application code, so a project deletion
    that only deleted the project row would leave snapshots, items and exports behind."""
    world = World(db.engine)
    project = world.project()
    shared = world.snapshot()
    first = world.run(project=project, snapshot=shared)
    second = world.run(project=project, snapshot=shared)
    outsider = world.run(snapshot=shared)  # a run of another project that uses the same snapshot
    item = world.item(transcript=second.transcripts[0])
    export = world.export(items=[item])
    result = evaldb.delete_project(db.engine, project, actor=ACTOR, reason=REASON)
    assert result.counts["run"] == 2
    assert result.counts["config_snapshot"] == 0  # the outsider still uses it
    assert exists(db.engine, db.metadata, "config_snapshot", "id", shared)
    assert not exists(db.engine, db.metadata, "dataset_item", "id", item)
    assert not exists(db.engine, db.metadata, "export_bundle", "id", export)
    assert first.id != second.id
    assert exists(db.engine, db.metadata, "run", "id", outsider.id)
    # and the snapshot goes with its last user
    last = evaldb.delete_run(db.engine, outsider.id, actor=ACTOR, reason=REASON)
    assert last.counts["config_snapshot"] == 1


def test_delete_test_profile_removes_all_versions_snapshots_report_unresolved(
    db: Db, evaldb: ModuleType
) -> None:
    populated = populate(db.engine)
    bundle, profile = populated.bundle, populated.ids["profile"]
    second = profile.model_dump(mode="json")
    second["version"] = 2
    second = _support.redigest("TestProfile", second)
    evaldb.store_definition(db.engine, _support.load_entity("TestProfile", json.dumps(second)))
    world = World(db.engine)
    carrying = world.export(profiles=[profile.id.uuid])
    other_profile = world.profile((1,))
    keeping = world.export(profiles=[other_profile])
    assert evaldb.latest_version(db.engine, "test_profile", profile.id) == 2
    reference = bundle.snapshot.evaluator.test_profile
    assert reference is not None
    assert reference.id == profile.id

    result = evaldb.delete_test_profile(db.engine, profile.id, actor=ACTOR, reason=REASON)

    # the real export of the run carries the profile in the clear (the bundle lists it), and the
    # stub export links it too: both go
    assert dict(result.counts) == {"test_profile": 2, "export_bundle": 2}
    with pytest.raises(evaldb.RecordNotStored):
        evaldb.load_export(db.engine, populated.export.id, expected_purpose="internal")
    assert evaldb.latest_version(db.engine, "test_profile", profile.id) is None
    for version in (1, 2):
        with pytest.raises(evaldb.RecordNotStored):
            evaldb.load_definition(db.engine, "test_profile", profile.id, version)
    assert not exists(db.engine, db.metadata, "export_bundle", "id", carrying)
    assert exists(db.engine, db.metadata, "export_bundle", "id", keeping)
    assert exists(db.engine, db.metadata, "test_profile", "id", other_profile)
    # the snapshot keeps its reference and stays valid: it reports the profile as unresolved
    loaded = evaldb.load_snapshot(db.engine, bundle.snapshot.id)
    assert loaded == bundle.snapshot
    _support.verify_own_digest(loaded)
    assert (
        count_where(
            db.engine,
            db.metadata,
            "snapshot_profile_ref",
            snapshot_id=bundle.snapshot.id.uuid,
            profile_id=profile.id.uuid,
        )
        == 1
    )
    # the run bundle that listed the profile no longer loads whole, and says what is missing
    with pytest.raises(evaldb.IncompleteRunBundle) as incomplete:
        evaldb.load_run_bundle(db.engine, bundle.id)
    assert [(m.kind, m.record_id, m.record_version) for m in incomplete.value.missing] == [
        ("test_profile", profile.id, 1)
    ]
    partial = evaldb.load_run_bundle(db.engine, bundle.id, allow_incomplete=True)
    assert partial.complete is False
    assert partial.bundle is None
    # everything else of the run is untouched
    assert exists(db.engine, db.metadata, "run", "id", bundle.run.id.uuid)
    assert count_where(db.engine, db.metadata, "turn", run_id=bundle.run.id.uuid) == len(
        bundle.turns
    )


@pytest.mark.parametrize(
    "kind", ["evaluator", "persona", "rubric", "metric_definition", "metric_binding"]
)
def test_delete_definition_leaves_snapshots_valid(db: Db, evaldb: ModuleType, kind: str) -> None:
    """The definitions a snapshot embeds are safe to delete: the snapshot embeds them (the owner's
    decision), so it loads, verifies and is found by its digest afterwards."""
    bundle = load_run_bundle_doc(run_bundle_doc(tier="T1", origin="simulated"))
    store_bundle(db.engine, evaldb, bundle)
    snapshot = bundle.snapshot
    embedded = {
        "evaluator": [snapshot.evaluator],
        "persona": [snapshot.persona],
        "rubric": [snapshot.rubric],
        "metric_definition": list(snapshot.metric_definitions),
        "metric_binding": list(snapshot.metric_bindings),
    }[kind]
    members_before = raw_dump(db.engine)["snapshot_member"]
    before_rows = row_counts(db.engine)[kind]
    assert before_rows == len(embedded)
    deleted = 0
    for definition in embedded:
        result = evaldb.delete_definition(
            db.engine, kind, definition.id, actor=ACTOR, reason=REASON
        )
        assert dict(result.counts) == {kind: 1}
        deleted += 1
        with pytest.raises(evaldb.RecordNotStored):
            evaldb.load_definition(db.engine, kind, definition.id, definition.version)
    assert row_counts(db.engine)[kind] == before_rows - deleted == 0
    loaded = evaldb.load_snapshot(db.engine, snapshot.id)
    assert loaded == snapshot
    assert loaded.model_dump_json() == snapshot.model_dump_json()
    _support.verify_own_digest(loaded)
    assert evaldb.find_snapshot_by_digest(db.engine, snapshot.content_digest) == snapshot
    assert raw_dump(db.engine)["snapshot_member"] == members_before
    assert evaldb.load_run_bundle(db.engine, bundle.id).complete is True


def test_delete_definition_removes_every_version_and_only_that_id(
    db: Db, evaldb: ModuleType
) -> None:
    world = World(db.engine)
    target = world.definition("rubric", (1, 2, 3, 4))
    sibling = world.definition("rubric", (1, 2))
    same_id_elsewhere = target  # a persona with the same UUID is another entity: it stays
    world._insert("persona", id=same_id_elsewhere, version=1)
    result = evaldb.delete_definition(db.engine, "rubric", target, actor=ACTOR, reason=REASON)
    assert dict(result.counts) == {"rubric": 4}
    assert count_where(db.engine, db.metadata, "rubric", id=target) == 0
    assert count_where(db.engine, db.metadata, "rubric", id=sibling) == 2
    assert count_where(db.engine, db.metadata, "persona", id=same_id_elsewhere) == 1


def test_delete_pricing_catalog_makes_the_run_bundle_incomplete(db: Db, evaldb: ModuleType) -> None:
    """ADR 0020 Decision 4e: run bundles that listed a deleted catalog load as incomplete."""
    bundle = load_run_bundle_doc(run_bundle_doc(tier="T1", origin="simulated"))
    store_bundle(db.engine, evaldb, bundle)
    (catalog,) = bundle.pricing_catalogs
    result = evaldb.delete_definition(
        db.engine, "pricing_catalog", catalog.id, actor=ACTOR, reason=REASON
    )
    assert dict(result.counts) == {"pricing_catalog": 1}
    with pytest.raises(evaldb.IncompleteRunBundle) as caught:
        evaldb.load_run_bundle(db.engine, bundle.id)
    assert [m.kind for m in caught.value.missing] == ["pricing_catalog"]
    assert evaldb.load_snapshot(db.engine, bundle.snapshot.id) == bundle.snapshot


def test_delete_dataset_item_removes_the_exports_that_embed_it(db: Db, evaldb: ModuleType) -> None:
    spec = SPEC["dataset_item"]
    world = World(db.engine)
    built = spec.build(world)
    before = row_counts(db.engine)
    result = call(evaldb, db.engine, spec, built.args)
    assert dict(result.counts) == _expected_counts(spec, built)
    assert removed(before, row_counts(db.engine)) == dict(DELTAS["dataset_item"])
    for table, column, value in built.gone:
        assert not exists(db.engine, db.metadata, table, column, value)
    for table, column, value in built.kept:  # the dataset that listed the item keeps its version
        assert exists(db.engine, db.metadata, table, column, value)


def test_delete_export_leaves_what_it_carried(db: Db, evaldb: ModuleType) -> None:
    spec = SPEC["export_bundle"]
    world = World(db.engine)
    built = spec.build(world)
    result = call(evaldb, db.engine, spec, built.args)
    assert dict(result.counts) == {"export_bundle": 1}
    for table, column, value in built.gone:
        assert not exists(db.engine, db.metadata, table, column, value)
    for table, column, value in built.kept:
        assert exists(db.engine, db.metadata, table, column, value)


def test_delete_snapshot_removes_an_unreferenced_snapshot_with_its_member_rows(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = load_run_bundle_doc(run_bundle_doc(tier="T1", origin="simulated"))
    snapshot = bundle.snapshot
    evaldb.store_snapshot(db.engine, snapshot)  # stored by itself: no run uses it
    definitions = row_counts(db.engine)
    assert definitions["snapshot_member"] > 0
    result = evaldb.delete_snapshot(db.engine, snapshot.id, actor=ACTOR, reason=REASON)
    assert dict(result.counts) == {"config_snapshot": 1}
    now = row_counts(db.engine)
    assert now["config_snapshot"] == 0
    assert now["snapshot_member"] == 0
    assert now["persona"] == definitions["persona"] > 0  # the definitions stay
    with pytest.raises(evaldb.RecordNotStored):
        evaldb.load_snapshot(db.engine, snapshot.id)


def test_delete_snapshot_refused_while_a_run_references_it(db: Db, evaldb: ModuleType) -> None:
    """ADR 0020 Ambiguity 5 and Decision 2: RESTRICT, as a typed error, with nothing deleted and no
    audit row. Once the last run is deleted the snapshot goes with it (delete_run), not before."""
    bundle = load_run_bundle_doc(run_bundle_doc(tier="T1", origin="simulated"))
    store_bundle(db.engine, evaldb, bundle)
    snapshot_id = bundle.snapshot.id
    before = raw_dump(db.engine)
    with recording(db.engine) as statements, pytest.raises(evaldb.SnapshotInUse) as caught:
        evaldb.delete_snapshot(db.engine, snapshot_id, actor=ACTOR, reason=REASON)
    assert caught.value.snapshot_id == snapshot_id
    assert isinstance(caught.value, evaldb.StoreError)
    assert str(snapshot_id) in str(caught.value)
    assert ACTOR not in str(caught.value)
    assert statements.writes == []
    assert raw_dump(db.engine) == before
    assert audit_rows(db.engine, db.metadata) == []
    # the database says the same by itself (the foreign key is RESTRICT)
    t = db.metadata.tables["config_snapshot"]
    with pytest.raises(sa.exc.IntegrityError), db.engine.begin() as conn:
        conn.execute(sa.delete(t).where(t.c.id == snapshot_id.uuid))
    evaldb.delete_run(db.engine, bundle.run.id, actor=ACTOR, reason=REASON)
    with pytest.raises(evaldb.RecordNotStored):
        evaldb.delete_snapshot(db.engine, snapshot_id, actor=ACTOR, reason=REASON)


def test_roots_that_are_not_roots_do_not_exist(evaldb: ModuleType) -> None:
    """A RunBundle, a record of fact other than the run, and one version of a versioned entity are
    not roots (ADR 0020 Decision 2): records go with their run, versions with their entity."""
    names = {n for n in dir(evaldb) if n.startswith("delete_")}
    assert names == set(FUNCTIONS)


# ================================================================ parity of the cascade counts


@pytest.mark.parametrize("spec", _spec_params())
def test_cascade_counts_parity_sqlite_postgres(db: Db, evaldb: ModuleType, spec: RootSpec) -> None:
    """Both backends delete the same rows and write the same counts (ADR 0020 Decision 9): the
    literal table of ``DELTAS`` is what each backend must produce, in the entity tables and the
    side tables alike."""
    world = World(db.engine)
    built = spec.build(world)
    before = row_counts(db.engine)
    result = call(evaldb, db.engine, spec, built.args)
    assert dict(result.counts) == _expected_counts(spec, built)
    assert removed(before, row_counts(db.engine)) == dict(DELTAS[spec.id])
    (audit,) = audit_rows(db.engine, db.metadata)
    assert counts_of(audit) == dict(result.counts)
    assert audit["root_kind"] == spec.root_kind


# ======================================================================== locks and races


@contextlib.contextmanager
def _paused_deletion(db: Db, evaldb: ModuleType, spec: RootSpec, args: Any) -> Iterator[list[Any]]:
    """Run the deletion in a thread and hold it before its first DELETE; yields the box that gets
    its result (or exception) once the block has ended and the thread was released and joined."""
    reached, release = threading.Event(), threading.Event()
    with pause_on_statement(db.engine, is_delete, reached, release):
        thread, box = run_in_thread(lambda: call(evaldb, db.engine, spec, args))
        try:
            wait_reached(reached, thread, "the deletion never reached its first DELETE", box)
            yield box
        finally:
            release.set()
            thread.join(timeout=120)
    assert not thread.is_alive()


def _locked_rows(spec: RootSpec, built: Any) -> list[tuple[str, uuid.UUID]]:
    tables = {
        "run": {"run", "config_snapshot"},
        "project": {"project", "run", "config_snapshot"},
        "test_profile": {"test_profile"},
        "dataset_item": {"dataset_item"},
        "export_bundle": {"export_bundle"},
        "config_snapshot": {"config_snapshot"},
    }.get(spec.id, {spec.root_kind})
    return [(t, v) for t, c, v in built.gone if c == "id" and t in tables]


def _free_rows(built: Any) -> list[tuple[str, uuid.UUID]]:
    return [
        (t, v) for t, c, v in built.kept if c == "id" and t in {"run", "project", "config_snapshot"}
    ]


def _sqlstate(error: sa.exc.DBAPIError) -> str | None:
    args = getattr(error.orig, "args", ())
    if args and isinstance(args[0], dict):
        code = args[0].get("C")
        return str(code) if code is not None else None
    return None


@pytest.mark.parametrize("spec", _spec_params())
def test_deletion_takes_its_locks_before_it_deletes(
    db: Db, evaldb: ModuleType, spec: RootSpec
) -> None:
    """ADR 0020 Decision 5, observed from outside while the deletion is paused before its first
    DELETE. Postgres: the root row (and, for a run or project, every run row and each run's
    snapshot row) is locked ``FOR UPDATE``; a row of an unrelated entity is free. SQLite: the
    transaction began with BEGIN IMMEDIATE, so a second writer cannot write."""
    world = World(db.engine)
    built = spec.build(world)
    locked, free = _locked_rows(spec, built), _free_rows(built)
    assert locked
    other = evaldb.create_store_engine(db.store.url, busy_timeout_ms=0)
    try:
        with _paused_deletion(db, evaldb, spec, built.args) as box:
            if db.backend == "postgres":
                for table, key in locked:
                    t = db.metadata.tables[table]
                    probe = (
                        sa.select(t.c.id)
                        .where(t.c.id == key)
                        .with_for_update(key_share=True, nowait=True)
                    )
                    with pytest.raises(sa.exc.DBAPIError) as caught, other.begin() as conn:
                        conn.execute(probe)
                    assert _sqlstate(caught.value) == "55P03", (
                        table,
                        str(key),
                    )  # lock_not_available
                for table, key in free:
                    t = db.metadata.tables[table]
                    probe = sa.select(t.c.id).where(t.c.id == key).with_for_update(nowait=True)
                    with other.begin() as conn:  # an unrelated row is not locked
                        assert conn.execute(probe).all() == [(key,)]
            else:
                meta = db.metadata.tables["tracer_meta"]
                row = {"singleton": 1, "schema_version": "0.0.1", "evaldb_version": "x"}
                with pytest.raises(sa.exc.OperationalError, match="locked"), other.begin() as conn:
                    conn.execute(sa.insert(meta).values(**row))
    finally:
        other.dispose()
    assert not isinstance(box[0], BaseException), box
    assert box[0].root_kind == spec.root_kind


def test_a_deletion_locks_before_it_reads_or_deletes(db: Db, evaldb: ModuleType) -> None:
    """The first statement a deletion sends is BEGIN IMMEDIATE on SQLite (a read transaction that
    later wanted to write could be refused halfway); on Postgres the first statement locks the
    root row."""
    world = World(db.engine)
    built = SPEC["run"].build(world)
    with recording(db.engine) as statements:
        call(evaldb, db.engine, SPEC["run"], built.args)
    first = statements.items[0][0]
    if db.backend == "sqlite":
        assert first.strip().upper().startswith("BEGIN IMMEDIATE"), first
    else:
        assert "FOR UPDATE" in first.upper(), first
        assert '"run"' in first or " run" in first.lower()
    deletes = [s for s, _ in statements.items if s.lstrip().upper().startswith("DELETE")]
    assert deletes  # and the audit row is inserted by the same call
    assert "deletion_audit" in statements.inserts


def _race(
    db: Db,
    evaldb: ModuleType,
    victim: Callable[[sa.Engine], Any],
    writer: Callable[[sa.Engine], Any],
) -> tuple[Any, Any, bool]:
    """Pause ``victim`` at its first DELETE, start ``writer`` on another engine, let the writer
    run into the deletion's locks, then release. Returns (victim result, writer outcome, whether
    the writer was seen waiting for a lock (Postgres) or failed on the lock (SQLite))."""
    other = evaldb.create_store_engine(db.store.url, busy_timeout_ms=200)
    monitor = evaldb.create_store_engine(db.store.url)
    reached, release = threading.Event(), threading.Event()
    blocked = False
    try:
        with pause_on_statement(db.engine, is_delete, reached, release):
            victim_thread, victim_box = run_in_thread(lambda: victim(db.engine))
            wait_reached(
                reached, victim_thread, "the deletion never reached its first DELETE", victim_box
            )
            writer_thread, writer_box = run_in_thread(lambda: writer(other))
            if db.backend == "postgres":
                blocked = wait_until_blocked(monitor, writer_thread)
            else:
                writer_thread.join(timeout=60)
                blocked = not writer_thread.is_alive() and isinstance(
                    writer_box[0], sa.exc.OperationalError
                )
            release.set()
            victim_thread.join(timeout=120)
            writer_thread.join(timeout=120)
        assert not victim_thread.is_alive()
        assert not writer_thread.is_alive()
        return victim_box[0], writer_box[0], blocked
    finally:
        release.set()
        other.dispose()
        monitor.dispose()


def _assert_refused_cleanly(
    db: Db, evaldb: ModuleType, outcome: Any, accepted: tuple[type, ...]
) -> None:
    assert isinstance(outcome, BaseException), f"the racing writer succeeded: {outcome!r}"
    if db.backend == "sqlite":
        # one writer at a time: the second is refused while the deletion holds the write lock
        assert isinstance(outcome, sa.exc.OperationalError), repr(outcome)
        assert "locked" in str(outcome)
    else:
        assert isinstance(outcome, accepted), repr(outcome)
        if isinstance(outcome, evaldb.StoreConstraintViolation):
            assert outcome.kind == "foreign_key"


def _world_for_race(db: Db, evaldb: ModuleType) -> tuple[Any, dict[str, Any]]:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    bundle = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, bundle)
    return bundle, doc


@pytest.mark.parametrize("writer_kind", ["rescoring", "export"])
def test_concurrent_insert_during_run_delete_never_orphans(
    db: Db, evaldb: ModuleType, writer_kind: str
) -> None:
    """A writer that adds rows for a run while the run is being deleted either waits (Postgres:
    the deletion holds ``FOR UPDATE`` on the run row, and the foreign key or the export's
    ``FOR KEY SHARE`` waits for it) or is refused (SQLite: one writer), and then fails because
    the parent is gone: no row of the run, no export of it and no link to it survives, and the
    audit counts are the rows that were really removed."""
    bundle, doc = _world_for_race(db, evaldb)
    b7 = support("_support_b7")
    rescoring = load_run_bundle_doc(renew_ids(doc, lists=["results"], verdict=True))
    export = b7.load_export(b7.export_doc("internal", [doc]))
    before = row_counts(db.engine)
    run_id = bundle.run.id

    def writer(engine: sa.Engine) -> Any:
        try:
            if writer_kind == "rescoring":
                return evaldb.store_run_bundle(engine, rescoring)
            return evaldb.store_export(engine, export, expected_purpose="internal")
        except BaseException as exc:
            return exc

    victim_result, outcome, blocked = _race(
        db,
        evaldb,
        lambda engine: evaldb.delete_run(engine, run_id, actor=ACTOR, reason=REASON),
        writer,
    )
    assert victim_result.root_kind == "run"
    assert blocked, "the writer was never held back by the deletion's locks"
    accepted = (
        (evaldb.StoreConstraintViolation,)
        if writer_kind == "rescoring"
        else (evaldb.ExportReferenceNotStored,)
    )
    _assert_refused_cleanly(db, evaldb, outcome, accepted)
    assert rows_referring(db.engine, db.metadata, "run", run_id.uuid) == []
    after = row_counts(db.engine)
    for table in (
        "export_bundle",
        "export_bundle_run",
        "metric_result",
        "run_verdict",
        "run_bundle",
    ):
        assert after[table] == 0, table
    # the audit says exactly what went: the writer added nothing the counts could miss
    for key, count in victim_result.counts.items():
        assert before[key] - after[key] == count, key


def test_concurrent_run_insert_during_project_delete_never_orphans(
    db: Db, evaldb: ModuleType
) -> None:
    bundle, doc = _world_for_race(db, evaldb)
    new_run = load_run_bundle_doc(renew_ids(doc, lists=list(RECORD_LISTS), verdict=True, run=True))
    assert new_run.run.project_id == bundle.run.project_id
    before = row_counts(db.engine)

    def writer(engine: sa.Engine) -> Any:
        try:
            return evaldb.store_run_bundle(engine, new_run)
        except BaseException as exc:
            return exc

    victim_result, outcome, blocked = _race(
        db,
        evaldb,
        lambda engine: evaldb.delete_project(
            engine, bundle.run.project_id, actor=ACTOR, reason=REASON
        ),
        writer,
    )
    assert blocked
    _assert_refused_cleanly(
        db, evaldb, outcome, (evaldb.StoreConstraintViolation, evaldb.ProjectNotStored)
    )
    after = row_counts(db.engine)
    for table in (
        "project",
        "run",
        "artifact",
        "turn",
        "metric_result",
        "run_bundle",
        "config_snapshot",
    ):
        assert after[table] == 0, table
    for key, count in victim_result.counts.items():
        assert before[key] - after[key] == count, key


def test_concurrent_run_insert_during_snapshot_delete_never_orphans(
    db: Db, evaldb: ModuleType
) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    bundle = load_run_bundle_doc(doc)
    evaldb.store_snapshot(db.engine, bundle.snapshot)  # stored, and no run uses it yet
    evaldb.store_project(
        db.engine,
        _support.load_entity("Project", json.dumps(project_doc(str(bundle.run.project_id)))),
    )

    def writer(engine: sa.Engine) -> Any:
        try:
            return evaldb.store_run_bundle(engine, bundle)
        except BaseException as exc:
            return exc

    victim_result, outcome, blocked = _race(
        db,
        evaldb,
        lambda engine: evaldb.delete_snapshot(
            engine, bundle.snapshot.id, actor=ACTOR, reason=REASON
        ),
        writer,
    )
    assert victim_result.counts == {"config_snapshot": 1}
    assert blocked
    _assert_refused_cleanly(db, evaldb, outcome, (evaldb.StoreConstraintViolation,))
    counts = row_counts(db.engine)
    assert counts["config_snapshot"] == counts["run"] == counts["run_bundle"] == 0
    assert counts["artifact"] == counts["metric_result"] == 0


# ============================================================ faults leave nothing half-deleted


@pytest.mark.parametrize("spec", _spec_params())
def test_a_failed_deletion_changes_nothing(db: Db, evaldb: ModuleType, spec: RootSpec) -> None:
    """One transaction per root: a fault on the first DELETE, in the middle and on the last, leaves
    the store as it was, audit table included (the audit tests add a fault on the audit INSERT)."""
    world = World(db.engine)
    for other in ROOTS:
        other.build(world)
    probe = spec.build(world)
    with recording(db.engine) as statements:
        call(evaldb, db.engine, spec, probe.args)
    total = len([s for s, _many in statements.items if is_delete(s)])
    assert total >= 1
    for nth in sorted({1, max(1, total // 2), total}):
        built = spec.build(World(db.engine))
        state = raw_dump(db.engine)
        with (
            fault_on_statement(db.engine, r"^\s*DELETE\b", nth=nth) as seen,
            pytest.raises(Boom),
        ):
            call(evaldb, db.engine, spec, built.args)
        assert len(seen) == nth
        assert raw_dump(db.engine) == state, (spec.id, nth, total)
