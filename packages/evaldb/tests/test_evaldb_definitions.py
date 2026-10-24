# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C, P2-12a: definitions, snapshots, projects and dataset items in the repository.

Contract: docs/phase2/api-surface-batch-c.md. Every test runs on both backends through the ``db``
fixture (batch B: the ``store`` fixture with every table created). The repository is reached
through ``tracer_evaldb`` (the names are re-exported from ``tracer_evaldb.repository``).
Round trips assert model equality, byte-identical ``model_dump_json`` and that the digests verify.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib
import inspect
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    counts,
    dump,
    recording,
    select_all,
)
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    GOLDEN,
    VERSIONED_TABLES,
    Db,
    expected_side_rows,
    golden_doc,
    golden_valid_files,
    load_entity,
    redigest,
    row_set_equal,
    snake,
    verify_own_digest,
    with_metadata,
)
from tracer_evalschema import digest as ev_digest
from tracer_evalschema import ids as ev_ids
from tracer_evalschema.errors import (
    ConflictingRecordError,
    ConflictingVersionError,
    TracerSchemaError,
)

db = _support.db

DEFINITION_ENTITIES = (
    "AgentTarget",
    "Persona",
    "TestProfile",
    "Evaluator",
    "MetricDefinition",
    "MetricBinding",
    "Rubric",
    "PricingCatalog",
    "Dataset",
)
MARKER = "CONFLICT-MARKER-7f3a"


def _definition_cases() -> list[Any]:
    return [
        pytest.param(entity, path, id=f"{entity}-{path.stem}")
        for entity in DEFINITION_ENTITIES
        for path in golden_valid_files(entity)
    ]


def _conflict_variant(entity: str, doc: dict[str, Any]) -> Any:
    """Same ID and version, other content, a digest that verifies."""
    return load_entity(entity, json.dumps(redigest(entity, with_metadata(doc, {"m": MARKER}))))


def _snapshot_with_plain_profile_digest(name: str = "maximal") -> dict[str, Any]:
    """A golden snapshot whose test-profile reference carries a plain digest (the internal rule:
    the golden maximal snapshots carry none). The snapshot digest ignores that field."""
    doc: dict[str, Any] = golden_doc("ConfigSnapshot", name)
    profile = doc["evaluator"]["test_profile"]
    if profile is not None:
        assert profile["content_digest"] is None
        profile["content_digest"] = {"scheme": "sha256-jcs/v1", "value": "ab" * 32, "key_ref": None}
    return doc


def _snapshot(name: str = "maximal") -> Any:
    return load_entity("ConfigSnapshot", json.dumps(_snapshot_with_plain_profile_digest(name)))


def _model_of(path: Path, entity: str) -> Any:
    return load_entity(entity, path.read_text("utf-8"))


def _assert_same(original: Any, loaded: Any) -> None:
    assert loaded == original
    assert loaded.model_dump_json() == original.model_dump_json()
    assert loaded.model_dump(mode="json") == original.model_dump(mode="json")
    verify_own_digest(loaded)


# ------------------------------------------------------------------------- store_definition


@pytest.mark.parametrize(("entity", "path"), _definition_cases())
def test_store_definition_idempotent_same_content(
    db: Db, evaldb: ModuleType, entity: str, path: Path
) -> None:
    table = snake(entity)
    model = _model_of(path, entity)
    first = evaldb.store_definition(db.engine, model)
    assert first.created is True
    assert first.kind == table
    assert first.entity_id == model.id
    assert first.version == model.version
    assert dict(first.inserted)[table] == 1
    before = dump(db.engine, db.metadata)
    again = evaldb.store_definition(db.engine, _model_of(path, entity))  # an equal new instance
    assert again.created is False
    assert dict(again.inserted) == {}
    assert dump(db.engine, db.metadata) == before  # not one byte of the database changed
    rows = select_all(db.engine, db.metadata, table, id=model.id.uuid)
    assert len(rows) == 1
    assert rows[0]["doc"] == model.model_dump_json()
    loaded = evaldb.load_definition(db.engine, table, model.id, model.version)
    _assert_same(model, loaded)
    assert ev_digest.compute_digest(loaded) == ev_digest.compute_digest(model)


def test_store_definition_returns_the_rows_it_wrote_for_a_dataset(
    db: Db, evaldb: ModuleType
) -> None:
    dataset = _model_of(GOLDEN / "Dataset" / "valid" / "maximal.json", "Dataset")
    result = evaldb.store_definition(db.engine, dataset)
    members = select_all(db.engine, db.metadata, "dataset_member", dataset_id=dataset.id.uuid)
    assert row_set_equal(members, expected_side_rows("dataset", dataset)["dataset_member"])
    assert len(members) == len(dataset.item_ids) > 0
    assert dict(result.inserted) == {"dataset": 1}  # side rows are not counted
    evaldb.store_definition(db.engine, dataset)
    assert len(select_all(db.engine, db.metadata, "dataset_member")) == len(dataset.item_ids)


@pytest.mark.parametrize(("entity", "path"), _definition_cases())
def test_store_definition_conflicting_content_rejected(
    db: Db, evaldb: ModuleType, entity: str, path: Path
) -> None:
    table = snake(entity)
    stored = _model_of(path, entity)
    evaldb.store_definition(db.engine, stored)
    other = _conflict_variant(entity, golden_doc(entity, path.stem))
    assert (other.id, other.version) == (stored.id, stored.version)
    assert other.model_dump_json() != stored.model_dump_json()
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingVersionError) as caught:
        evaldb.store_definition(db.engine, other)
    text = str(caught.value)
    assert str(stored.id) in text  # names the entity
    assert MARKER not in text  # and none of its content
    assert dump(db.engine, db.metadata) == before
    assert evaldb.load_definition(db.engine, table, stored.id, stored.version) == stored


def test_store_definition_conflict_is_found_by_document_not_by_digest(
    db: Db, evaldb: ModuleType
) -> None:
    """The comparison is the stored ``doc_sha256``: two Evaluators that differ only in the digest
    on their test-profile reference have the same own digest (N20) and are still different."""
    doc = golden_doc("Evaluator", "maximal")
    stored = load_entity("Evaluator", json.dumps(doc))
    twin_doc = copy.deepcopy(doc)
    twin_doc["test_profile"]["content_digest"] = {
        "scheme": "sha256-jcs/v1",
        "value": "cd" * 32,
        "key_ref": None,
    }
    twin = load_entity("Evaluator", json.dumps(twin_doc))
    assert twin.content_digest == stored.content_digest
    assert twin.model_dump_json() != stored.model_dump_json()
    evaldb.store_definition(db.engine, stored)
    with pytest.raises(ConflictingVersionError):
        evaldb.store_definition(db.engine, twin)
    assert evaldb.load_definition(db.engine, "evaluator", stored.id, stored.version) == stored


def test_store_definition_other_version_of_the_same_id_is_not_a_conflict(
    db: Db, evaldb: ModuleType
) -> None:
    doc = golden_doc("Persona", "maximal")
    v1 = load_entity("Persona", json.dumps(doc))
    v2 = load_entity(
        "Persona", json.dumps(redigest("Persona", {**doc, "version": doc["version"] + 1}))
    )
    assert evaldb.store_definition(db.engine, v1).created is True
    assert evaldb.store_definition(db.engine, v2).created is True
    assert len(select_all(db.engine, db.metadata, "persona", id=v1.id.uuid)) == 2


def test_store_definition_takes_the_nine_versioned_entities_only(
    db: Db, evaldb: ModuleType
) -> None:
    project = load_entity("Project", (GOLDEN / "Project" / "valid" / "minimal.json").read_text())
    snapshot = _snapshot("minimal")
    for wrong in (project, snapshot, "persona", None, {"id": 1}):
        with pytest.raises(TypeError):
            evaldb.store_definition(db.engine, wrong)
    assert sum(counts(db.engine, db.metadata).values()) == 0


# --------------------------------------------------------------------------- latest_version


@pytest.mark.parametrize("entity", DEFINITION_ENTITIES)
def test_latest_version_lookup(db: Db, evaldb: ModuleType, entity: str) -> None:
    kind = snake(entity)
    doc = golden_doc(entity, "minimal")
    unknown = ev_ids.id_type(doc["id"].split("_", 1)[0]).new()
    assert evaldb.latest_version(db.engine, kind, unknown) is None
    assert evaldb.latest_version(db.engine, kind, unknown.uuid) is None
    versions = {}
    for number in (1, 3, 2):  # stored out of order: the answer is the maximum
        versions[number] = load_entity(
            entity, json.dumps(redigest(entity, {**doc, "version": number}))
        )
    other_id = ev_ids.id_type(doc["id"].split("_", 1)[0]).new()
    other = load_entity(entity, json.dumps(redigest(entity, {**doc, "id": str(other_id)})))
    expected = []
    for number in (1, 3, 2):
        evaldb.store_definition(db.engine, versions[number])
        expected.append(evaldb.latest_version(db.engine, kind, versions[number].id))
    assert expected == [1, 3, 3]
    evaldb.store_definition(db.engine, other)
    assert evaldb.latest_version(db.engine, kind, versions[1].id) == 3
    assert evaldb.latest_version(db.engine, kind, versions[1].id.uuid) == 3
    assert evaldb.latest_version(db.engine, kind, other.id) == 1
    # a version of a rubric is not a version of anything else with the same bytes
    for other_kind in VERSIONED_TABLES:
        if other_kind != kind:
            assert evaldb.latest_version(db.engine, other_kind, versions[1].id.uuid) is None


def test_latest_version_arguments(db: Db, evaldb: ModuleType) -> None:
    persona = load_entity("Persona", json.dumps(golden_doc("Persona", "minimal")))
    rubric = load_entity("Rubric", json.dumps(golden_doc("Rubric", "minimal")))
    evaldb.store_definition(db.engine, persona)
    assert evaldb.latest_version(db.engine, "persona", persona.id) == 1
    with pytest.raises(TypeError):
        evaldb.latest_version(db.engine, "persona", rubric.id)  # another kind of ID
    for wire in (str(persona.id), persona.id.uuid.hex, 7, None):
        with pytest.raises(TypeError):
            evaldb.latest_version(db.engine, "persona", wire)
    for kind in ("project", "run", "Persona", "", "persona ", None, 3):
        with pytest.raises(ValueError, match="kind"):
            evaldb.latest_version(db.engine, kind, persona.id)


# ----------------------------------------------------------------------------- store_snapshot


@pytest.mark.parametrize("name", ["minimal", "maximal", "maximal-keyed-catalog"])
def test_store_snapshot_extracts_embedded_definitions(
    db: Db, evaldb: ModuleType, name: str
) -> None:
    snapshot = _snapshot(name)
    result = evaldb.store_snapshot(db.engine, snapshot)
    assert result.created is True
    assert result.kind == "config_snapshot"
    inserted = dict(result.inserted)
    assert inserted["config_snapshot"] == 1
    assert inserted["persona"] == inserted["rubric"] == inserted["evaluator"] == 1
    assert inserted["metric_definition"] == len(snapshot.metric_definitions) > 0
    assert inserted["metric_binding"] == len(snapshot.metric_bindings) > 0
    assert set(inserted) == {
        "config_snapshot",
        "persona",
        "rubric",
        "evaluator",
        "metric_definition",
        "metric_binding",
    }
    # each embedded definition is in its own table, equal to the embedded one, byte for byte
    embedded = [
        ("persona", snapshot.persona),
        ("rubric", snapshot.rubric),
        ("evaluator", snapshot.evaluator),
        *[("metric_definition", d) for d in snapshot.metric_definitions],
        *[("metric_binding", b) for b in snapshot.metric_bindings],
    ]
    for table, definition in embedded:
        loaded = evaldb.load_definition(db.engine, table, definition.id, definition.version)
        _assert_same(definition, loaded)
        assert evaldb.latest_version(db.engine, table, definition.id) == definition.version
    # the member rows say what the snapshot embeds, in order
    expected = expected_side_rows("config_snapshot", snapshot)
    for side in ("snapshot_member", "snapshot_profile_ref"):
        rows = select_all(db.engine, db.metadata, side, snapshot_id=snapshot.id.uuid)
        assert row_set_equal(rows, expected[side]), side
    assert len(expected["snapshot_profile_ref"]) == (name != "minimal")
    # the snapshot itself comes back equal, with its own digest verifying
    _assert_same(snapshot, evaldb.load_snapshot(db.engine, snapshot.id))
    _assert_same(snapshot, evaldb.load_snapshot(db.engine, snapshot.id.uuid))
    # idempotent: a second store writes nothing
    before = dump(db.engine, db.metadata)
    with recording(db.engine) as statements:
        again = evaldb.store_snapshot(db.engine, _snapshot(name))
    assert again.created is False
    assert dict(again.inserted) == {}
    assert statements.writes == []
    assert dump(db.engine, db.metadata) == before


def test_store_snapshot_reuses_definitions_that_are_already_stored(
    db: Db, evaldb: ModuleType
) -> None:
    snapshot = _snapshot("maximal")
    evaldb.store_definition(db.engine, snapshot.persona)
    evaldb.store_definition(db.engine, snapshot.metric_definitions[0])
    result = evaldb.store_snapshot(db.engine, snapshot)
    inserted = dict(result.inserted)
    assert "persona" not in inserted  # present and equal: shared, not inserted again
    assert inserted["metric_definition"] == len(snapshot.metric_definitions) - 1
    assert len(select_all(db.engine, db.metadata, "persona")) == 1
    # two snapshots that embed the same definition share its row
    other_doc = copy.deepcopy(_snapshot_with_plain_profile_digest("maximal"))
    other_doc["id"] = str(ev_ids.ConfigSnapshotId.new())
    other_doc = _redigest_snapshot(other_doc)
    other = load_entity("ConfigSnapshot", json.dumps(other_doc))
    second = evaldb.store_snapshot(db.engine, other)
    assert dict(second.inserted) == {"config_snapshot": 1}
    assert len(select_all(db.engine, db.metadata, "persona")) == 1


def _redigest_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    redigested: dict[str, Any] = redigest("ConfigSnapshot", doc)
    return redigested


@pytest.mark.parametrize(
    "which",
    ["persona", "rubric", "evaluator", "metric_definition", "metric_binding"],
)
def test_store_snapshot_conflicting_embedded_definition_rejected(
    db: Db, evaldb: ModuleType, which: str
) -> None:
    snapshot = _snapshot("maximal")
    embedded = snapshot.model_dump(mode="json")
    docs = {
        "persona": ("Persona", embedded["persona"]),
        "rubric": ("Rubric", embedded["rubric"]),
        "evaluator": ("Evaluator", embedded["evaluator"]),
        "metric_definition": ("MetricDefinition", embedded["metric_definitions"][0]),
        "metric_binding": ("MetricBinding", embedded["metric_bindings"][0]),
    }
    entity, doc = docs[which]
    conflicting = _conflict_variant(entity, doc)
    evaldb.store_definition(db.engine, conflicting)
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingVersionError) as caught:
        evaldb.store_snapshot(db.engine, snapshot)
    assert MARKER not in str(caught.value)
    # all or nothing: no snapshot, no other definition, no member row; the stored one is untouched
    assert dump(db.engine, db.metadata) == before
    assert select_all(db.engine, db.metadata, "config_snapshot") == []


def test_store_snapshot_conflicting_snapshot_rejected(db: Db, evaldb: ModuleType) -> None:
    snapshot = _snapshot("maximal")
    evaldb.store_snapshot(db.engine, snapshot)
    doc = redigest(
        "ConfigSnapshot", with_metadata(_snapshot_with_plain_profile_digest(), {"m": MARKER})
    )
    other = load_entity("ConfigSnapshot", json.dumps(doc))
    assert other.id == snapshot.id
    assert other.content_digest != snapshot.content_digest
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_snapshot(db.engine, other)
    assert not isinstance(caught.value, ConflictingVersionError)
    assert MARKER not in str(caught.value)
    assert dump(db.engine, db.metadata) == before


def test_store_snapshot_refuses_absent_or_keyed_profile_digest(db: Db, evaldb: ModuleType) -> None:
    """ADR 0019 Ambiguity 10: the store holds internal data, so the snapshot's reference to its
    test profile carries the plain digest (``snapshot.check_internal_snapshot``)."""
    absent = load_entity("ConfigSnapshot", json.dumps(golden_doc("ConfigSnapshot", "maximal")))
    assert absent.evaluator.test_profile.content_digest is None
    keyed_doc = _snapshot_with_plain_profile_digest()
    keyed_doc["evaluator"]["test_profile"]["content_digest"] = {
        "scheme": "hmac-sha256-jcs/v1",
        "value": "ef" * 32,
        "key_ref": {"key_id": "profile-hmac-1", "key_version": "2", "provider_hint": "local-kms"},
    }
    keyed = load_entity("ConfigSnapshot", json.dumps(keyed_doc))
    for refused in (absent, keyed):
        with (
            recording(db.engine) as statements,
            pytest.raises(evaldb.InternalSnapshotRefused) as caught,
        ):
            evaldb.store_snapshot(db.engine, refused)
        error: Any = caught.value
        assert error.snapshot_id == refused.id
        assert str(refused.id) in str(error)
        assert "ef" * 32 not in str(error)  # the rule, never the digest
        assert isinstance(error, evaldb.StoreError)
        assert isinstance(error, ValueError)
        assert statements.writes == []
    assert sum(counts(db.engine, db.metadata).values()) == 0
    # a snapshot with no profile reference has nothing to record, and a plain digest is accepted
    assert evaldb.store_snapshot(db.engine, _snapshot("minimal")).created is True
    assert evaldb.store_snapshot(db.engine, _snapshot("maximal")).created is True


# ---------------------------------------------------------- lookup of a snapshot by digest


def test_snapshot_lookup_by_content_digest(db: Db, evaldb: ModuleType) -> None:
    first, second = _snapshot("minimal"), _snapshot("maximal")
    assert first.content_digest != second.content_digest
    assert evaldb.find_snapshot_by_digest(db.engine, first.content_digest) is None  # nothing yet
    evaldb.store_snapshot(db.engine, first)
    evaldb.store_snapshot(db.engine, second)
    for stored in (first, second):
        found = evaldb.find_snapshot_by_digest(db.engine, stored.content_digest)
        assert found is not None
        _assert_same(stored, found)
    digest_type = type(first.content_digest)
    value = first.content_digest.value
    flipped = value[:-1] + ("0" if value[-1] != "0" else "1")
    near_miss = digest_type(scheme=first.content_digest.scheme, value=flipped)
    assert evaldb.find_snapshot_by_digest(db.engine, near_miss) is None
    # the scheme is part of the key: the same value under the keyed scheme names nothing
    keyed = digest_type.model_validate(
        {
            "scheme": "hmac-sha256-jcs/v1",
            "value": value,
            "key_ref": {"key_id": "k1", "key_version": "1", "provider_hint": "local-kms"},
        }
    )
    assert evaldb.find_snapshot_by_digest(db.engine, keyed) is None
    for wrong in (value, None, {"scheme": "sha256-jcs/v1", "value": value}):
        with pytest.raises(TypeError):
            evaldb.find_snapshot_by_digest(db.engine, wrong)


# ----------------------------------------------------------- projects and dataset items


@pytest.mark.parametrize("name", ["minimal", "maximal"])
def test_store_project_round_trip_and_conflict(db: Db, evaldb: ModuleType, name: str) -> None:
    project = load_entity("Project", (GOLDEN / "Project" / "valid" / f"{name}.json").read_text())
    assert evaldb.store_project(db.engine, project).created is True
    again = evaldb.store_project(db.engine, project)
    assert (again.created, dict(again.inserted)) == (False, {})
    loaded = evaldb.load_project(db.engine, project.id)
    assert loaded == project
    assert loaded.model_dump_json() == project.model_dump_json()
    doc = with_metadata(json.loads(project.model_dump_json()), {"m": MARKER})
    other = load_entity("Project", json.dumps(doc))
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_project(db.engine, other)
    assert MARKER not in str(caught.value)
    assert dump(db.engine, db.metadata) == before


def _item_cases() -> list[Any]:
    return [pytest.param(p, id=p.stem) for p in golden_valid_files("DatasetItem")]


@pytest.mark.parametrize("path", _item_cases())
def test_store_dataset_item_round_trip_and_conflict(db: Db, evaldb: ModuleType, path: Path) -> None:
    item = load_entity("DatasetItem", path.read_text("utf-8"))
    # an item may name an artifact, a transcript or a run that is not stored (a synthetic item)
    assert evaldb.store_dataset_item(db.engine, item).created is True
    again = evaldb.store_dataset_item(db.engine, item)
    assert (again.created, dict(again.inserted)) == (False, {})
    loaded = evaldb.load_dataset_item(db.engine, item.id)
    assert loaded == item
    assert loaded.model_dump_json() == item.model_dump_json()
    doc = with_metadata(json.loads(item.model_dump_json()), {"m": MARKER})
    other = load_entity("DatasetItem", json.dumps(doc))
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_dataset_item(db.engine, other)
    assert MARKER not in str(caught.value)
    assert dump(db.engine, db.metadata) == before
    assert len(select_all(db.engine, db.metadata, "dataset_item")) == 1


def test_loading_something_that_is_not_stored_is_a_typed_error(db: Db, evaldb: ModuleType) -> None:
    persona = load_entity("Persona", json.dumps(golden_doc("Persona", "minimal")))
    project = load_entity("Project", (GOLDEN / "Project" / "valid" / "minimal.json").read_text())
    item = load_entity(
        "DatasetItem", (GOLDEN / "DatasetItem" / "valid" / "minimal.json").read_text()
    )
    snapshot = _snapshot("minimal")
    cases: list[tuple[Callable[[], Any], str]] = [
        (lambda: evaldb.load_definition(db.engine, "persona", persona.id, 1), "persona"),
        (lambda: evaldb.load_project(db.engine, project.id), "project"),
        (lambda: evaldb.load_dataset_item(db.engine, item.id), "dataset_item"),
        (lambda: evaldb.load_snapshot(db.engine, snapshot.id), "config_snapshot"),
    ]
    for call, kind in cases:
        with pytest.raises(evaldb.RecordNotStored) as caught:
            call()
        assert isinstance(caught.value, evaldb.StoreError)
        assert caught.value.kind == kind
    evaldb.store_definition(db.engine, persona)
    with pytest.raises(evaldb.RecordNotStored) as caught:
        evaldb.load_definition(db.engine, "persona", persona.id, 2)  # the version is part of it
    assert caught.value.version == 2
    assert str(persona.id) in str(caught.value)


# ------------------------------------------------------------------------ re-validation


def _tampered(kind: str) -> tuple[str, Any]:
    persona = load_entity("Persona", json.dumps(golden_doc("Persona", "minimal")))
    project = load_entity("Project", (GOLDEN / "Project" / "valid" / "minimal.json").read_text())
    item = load_entity(
        "DatasetItem", (GOLDEN / "DatasetItem" / "valid" / "minimal.json").read_text()
    )
    snapshot = _snapshot("minimal")
    if kind == "definition-digest":
        return "store_definition", persona.model_copy(update={"accent": "changed after validation"})
    if kind == "definition-version":
        return "store_definition", persona.model_copy(update={"schema_version": "0.0.2"})
    if kind == "snapshot-digest":
        return "store_snapshot", snapshot.model_copy(update={"id": ev_ids.ConfigSnapshotId.new()})
    if kind == "project-version":
        return "store_project", project.model_copy(update={"schema_version": "0.0.2"})
    return "store_dataset_item", item.model_copy(update={"schema_version": "0.0.2"})


@pytest.mark.parametrize(
    "kind",
    [
        "definition-digest",
        "definition-version",
        "snapshot-digest",
        "project-version",
        "item-version",
    ],
)
def test_store_functions_revalidate_instances_built_without_validation(
    db: Db, evaldb: ModuleType, kind: str
) -> None:
    """An instance that was changed after validation (``model_copy`` does not validate) is checked
    again from its own JSON; nothing reaches the database."""
    function_name, model = _tampered(kind)
    with recording(db.engine) as statements, pytest.raises((ValidationError, TracerSchemaError)):
        getattr(evaldb, function_name)(db.engine, model)
    assert statements.writes == []
    assert sum(counts(db.engine, db.metadata).values()) == 0


# ------------------------------------------------------------------------- API shape


WRITERS = (
    "store_project",
    "store_definition",
    "store_snapshot",
    "store_dataset_item",
    "store_run_bundle",
    "store_export",
)
READERS = (
    "load_project",
    "load_definition",
    "load_snapshot",
    "load_dataset_item",
    "load_run_bundle",
    "load_export",
    "latest_version",
    "find_snapshot_by_digest",
    "find_by_external_ref",
)


def test_repository_functions_and_signatures(evaldb: ModuleType) -> None:
    repository = importlib.import_module("tracer_evaldb.repository")
    expected: dict[str, list[tuple[str, Any]]] = {
        "store_project": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("project", "POSITIONAL_OR_KEYWORD"),
        ],
        "store_definition": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("definition", "POSITIONAL_OR_KEYWORD"),
        ],
        "store_snapshot": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("snapshot", "POSITIONAL_OR_KEYWORD"),
        ],
        "store_dataset_item": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("item", "POSITIONAL_OR_KEYWORD"),
        ],
        "store_run_bundle": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("bundle", "POSITIONAL_OR_KEYWORD"),
            ("repair_members", "KEYWORD_ONLY"),
        ],
        "store_export": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("export", "POSITIONAL_OR_KEYWORD"),
            ("expected_purpose", "KEYWORD_ONLY"),
        ],
        "load_project": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("project_id", "POSITIONAL_OR_KEYWORD"),
        ],
        "load_definition": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("kind", "POSITIONAL_OR_KEYWORD"),
            ("definition_id", "POSITIONAL_OR_KEYWORD"),
            ("version", "POSITIONAL_OR_KEYWORD"),
        ],
        "load_snapshot": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("snapshot_id", "POSITIONAL_OR_KEYWORD"),
        ],
        "load_dataset_item": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("item_id", "POSITIONAL_OR_KEYWORD"),
        ],
        "load_run_bundle": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("bundle_id", "POSITIONAL_OR_KEYWORD"),
            ("allow_incomplete", "KEYWORD_ONLY"),
        ],
        "load_export": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("export_id", "POSITIONAL_OR_KEYWORD"),
            ("expected_purpose", "KEYWORD_ONLY"),
        ],
        "latest_version": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("kind", "POSITIONAL_OR_KEYWORD"),
            ("definition_id", "POSITIONAL_OR_KEYWORD"),
        ],
        "find_snapshot_by_digest": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("content_digest", "POSITIONAL_OR_KEYWORD"),
        ],
        "find_by_external_ref": [
            ("engine", "POSITIONAL_OR_KEYWORD"),
            ("entity_kind", "POSITIONAL_OR_KEYWORD"),
            ("ref", "POSITIONAL_OR_KEYWORD"),
            ("run_id", "KEYWORD_ONLY"),
        ],
    }
    assert set(expected) == {*WRITERS, *READERS}
    for name, parameters in expected.items():
        function = getattr(repository, name)
        assert getattr(evaldb, name) is function, name
        assert name in evaldb.__all__, name
        assert name in repository.__all__, name
        signature = inspect.signature(function)
        got = [(p.name, p.kind.name) for p in signature.parameters.values()]
        assert got == parameters, name
        defaults = {
            p.name: p.default for p in signature.parameters.values() if p.default is not p.empty
        }
        want_defaults = {
            "load_run_bundle": {"allow_incomplete": False},
            "store_run_bundle": {"repair_members": False},
            "find_by_external_ref": {"run_id": None},
        }
        assert defaults == want_defaults.get(name, {}), name
    # the keyword-only purpose has no default (SCHEMA_POLICY section 11)
    for name in ("store_export", "load_export"):
        assert (
            inspect.signature(getattr(repository, name)).parameters["expected_purpose"].default
            is inspect.Parameter.empty
        )


def test_repository_functions_require_an_engine(db: Db, evaldb: ModuleType) -> None:
    """One transaction per call, opened by the function on an ``Engine``: a connection, a URL or
    anything else is a TypeError before any statement."""
    persona = load_entity("Persona", json.dumps(golden_doc("Persona", "minimal")))
    with db.engine.connect() as connection:
        for wrong in (connection, str(db.engine.url), None, db.engine.url):
            with pytest.raises(TypeError):
                evaldb.store_definition(wrong, persona)
            with pytest.raises(TypeError):
                evaldb.latest_version(wrong, "persona", persona.id)
            with pytest.raises(TypeError):
                evaldb.load_run_bundle(wrong, ev_ids.RunBundleId.new())
    assert sum(counts(db.engine, db.metadata).values()) == 0


def test_id_arguments_are_typed_ids_or_uuids(db: Db, evaldb: ModuleType) -> None:
    bundle_id = ev_ids.RunBundleId.new()
    loaders: list[Callable[[Any], Any]] = [
        lambda value: evaldb.load_run_bundle(db.engine, value),
        lambda value: evaldb.load_project(db.engine, value),
        lambda value: evaldb.load_snapshot(db.engine, value),
        lambda value: evaldb.load_dataset_item(db.engine, value),
        lambda value: evaldb.load_export(db.engine, value, expected_purpose="internal"),
    ]
    for loader_call in loaders:
        for wrong in (str(bundle_id), bundle_id.uuid.hex, 5, None, b"x" * 16):
            with pytest.raises(TypeError):
                loader_call(wrong)
        with pytest.raises(TypeError):  # the ID of another kind of entity
            loader_call(ev_ids.TurnId.new())
    for loader_call in loaders[1:2] + loaders[3:4]:  # project and dataset item
        with pytest.raises(evaldb.RecordNotStored):  # a bare UUIDv7 is accepted
            loader_call(ev_ids.uuid7())
        with pytest.raises(ValueError, match="version 7"):  # every stored ID is a UUIDv7
            loader_call(uuid.uuid4())


def test_result_types_are_frozen_dataclasses(evaldb: ModuleType) -> None:
    expected = {
        "StoreResult": ["kind", "entity_id", "version", "created", "inserted"],
        "MissingMember": ["kind", "record_id", "record_version"],
        "LoadedRunBundle": ["bundle_id", "complete", "missing", "bundle", "records"],
        "ExternalRefMatch": ["entity_kind", "entity_id", "run_id"],
    }
    for name, fields in expected.items():
        cls = getattr(evaldb, name)
        assert dataclasses.is_dataclass(cls), name
        assert [f.name for f in dataclasses.fields(cls)] == fields, name
        assert vars(cls)["__dataclass_params__"].frozen is True, name
        assert name in evaldb.__all__
    result = evaldb.StoreResult("persona", ev_ids.PersonaId.new(), 1, True, {})
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.created = False
