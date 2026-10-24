# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C, P2-12b: the RunBundle write unit and its reconstruction.

Contract: docs/phase2/api-surface-batch-c.md and ADR 0022 (Decisions 1 to 8 and 12). Inputs: the
Phase 1 T-DEG bundles (T0, T0m, T1, T2, simulated and human), the golden RunBundle fixtures and
bundle documents built with the Phase 1 builders. Every test runs on both backends (``db``).
Round trips assert model equality, byte-identical ``model_dump_json`` and verifying digests.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    LIST_TABLE,
    MEMBER_KINDS,
    RECORD_LISTS,
    Boom,
    counts,
    dump,
    fault_on_insert,
    lists_of,
    load_run_bundle_doc,
    make_bundle,
    make_project,
    recording,
    renew_ids,
    run_bundle_doc,
    select_all,
    sized_bundle_doc,
    store_bundle,
    store_project_of,
    table_set_of,
    verify_bundle_digests,
    with_marker_metadata,
)
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    GOLDEN,
    Db,
    expected_side_rows,
    load_entity,
    redigest,
    row_set_equal,
    tdeg_bundles,
)
from tracer_evalschema import ids as ev_ids
from tracer_evalschema import loader as ev_loader
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.errors import (
    ConflictingRecordError,
    ConflictingVersionError,
    TracerSchemaError,
)

db = _support.db

MARKER = "CONFLICT-MARKER-91c4"


def _golden_bundle(name: str) -> Any:
    return load_entity("RunBundle", (GOLDEN / "RunBundle" / "valid" / f"{name}.json").read_text())


def _reversed(doc: dict[str, Any], *, withheld: bool = False) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    for name in RECORD_LISTS:
        out[name] = list(reversed(out[name]))
    out["pricing_catalogs"] = list(reversed(out["pricing_catalogs"]))
    if withheld:
        out["withheld"] = list(reversed(out["withheld"]))
    return out


def _cases() -> list[Any]:
    """(id, bundle): the 8 T-DEG bundles, the 2 golden bundles, two with every list reversed."""
    cases: list[Any] = [pytest.param(b, id=name) for name, b in tdeg_bundles()]
    cases += [pytest.param(_golden_bundle(n), id=f"golden-{n}") for n in ("minimal", "maximal")]
    reversed_t1 = load_run_bundle_doc(
        _reversed(
            run_bundle_doc(tier="T1", origin="simulated", with_profile=True, profile_digest="plain")
        )
    )
    cases.append(pytest.param(reversed_t1, id="T1-lists-reversed"))
    golden_doc = json.loads((GOLDEN / "RunBundle" / "valid" / "maximal.json").read_text())
    cases.append(
        pytest.param(
            load_entity("RunBundle", json.dumps(_reversed(golden_doc, withheld=True))),
            id="golden-maximal-lists-and-withheld-reversed",
        )
    )
    return cases


def _store_and_load(db: Db, evaldb: ModuleType, bundle: Any) -> tuple[Any, Any]:
    result = store_bundle(db.engine, evaldb, bundle)
    return result, evaldb.load_run_bundle(db.engine, bundle.id)


# ------------------------------------------------------------------------------ round trips


@pytest.mark.parametrize("bundle", _cases())
def test_run_bundle_round_trip_order_preserved(db: Db, evaldb: ModuleType, bundle: Any) -> None:
    result, loaded = _store_and_load(db, evaldb, bundle)
    assert result.created is True
    assert result.kind == "run_bundle"
    assert result.entity_id == bundle.id
    assert loaded.complete is True
    assert loaded.missing == ()
    assert loaded.bundle_id == bundle.id
    assert loaded.bundle is not None
    # every list, in order, withheld included
    assert lists_of(loaded.bundle) == lists_of(bundle)
    for name in RECORD_LISTS:
        assert [r.model_dump_json() for r in getattr(loaded.bundle, name)] == [
            r.model_dump_json() for r in getattr(bundle, name)
        ], name
    assert loaded.bundle.withheld == bundle.withheld
    # the member rows say the same thing
    member_rows = select_all(db.engine, db.metadata, "run_bundle_member", bundle_id=bundle.id.uuid)
    assert row_set_equal(member_rows, expected_side_rows("run_bundle", bundle)["run_bundle_member"])
    withheld_rows = select_all(
        db.engine, db.metadata, "run_bundle_withheld", bundle_id=bundle.id.uuid
    )
    assert row_set_equal(
        withheld_rows, expected_side_rows("run_bundle", bundle)["run_bundle_withheld"]
    )


@pytest.mark.parametrize("bundle", _cases())
def test_run_bundle_round_trip_byte_identical_json(db: Db, evaldb: ModuleType, bundle: Any) -> None:
    original_json = bundle.model_dump_json()
    _, loaded = _store_and_load(db, evaldb, bundle)
    assert loaded.bundle == bundle
    assert loaded.bundle.model_dump_json() == original_json
    assert loaded.bundle.model_dump(mode="json") == bundle.model_dump(mode="json")
    # and equal to a fresh load of the same text
    fresh = ev_loader.load_bundle(RunBundle, original_json)
    assert loaded.bundle.model_dump_json() == fresh.model_dump_json()
    verify_bundle_digests(loaded.bundle)
    # a bundle id given as a bare UUID loads the same bundle
    again = evaldb.load_run_bundle(db.engine, bundle.id.uuid)
    assert again.bundle.model_dump_json() == original_json


def test_run_bundle_withheld_round_trip(db: Db, evaldb: ModuleType) -> None:
    """The golden maximal bundle declares nine records withheld (they are in no list): a withheld
    record is not a missing member."""
    bundle = _golden_bundle("maximal")
    assert len(bundle.withheld) == 9
    _, loaded = _store_and_load(db, evaldb, bundle)
    assert loaded.complete is True
    assert loaded.missing == ()
    assert loaded.bundle.withheld == bundle.withheld
    assert [w.kind for w in loaded.bundle.withheld] == [w.kind for w in bundle.withheld]
    assert loaded.bundle.model_dump_json() == bundle.model_dump_json()
    rows = select_all(db.engine, db.metadata, "run_bundle_withheld", bundle_id=bundle.id.uuid)
    assert sorted((r["ordinal"], r["kind"], r["record_id"]) for r in rows) == [
        (i, w.kind, w.record_id.uuid) for i, w in enumerate(bundle.withheld)
    ]
    # no record is invented for what was withheld
    for withheld in bundle.withheld:
        for table in LIST_TABLE.values():
            assert select_all(db.engine, db.metadata, table, id=withheld.record_id.uuid) == []


def test_run_bundle_stores_its_embedded_definitions_catalogs_and_profiles(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = _golden_bundle("maximal")
    assert bundle.pricing_catalogs
    assert bundle.test_profiles
    result = store_bundle(db.engine, evaldb, bundle)
    inserted = dict(result.inserted)
    assert inserted["pricing_catalog"] == len(bundle.pricing_catalogs)
    assert inserted["test_profile"] == len(bundle.test_profiles)
    assert inserted["config_snapshot"] == 1
    assert inserted["run"] == 1
    assert inserted["run_bundle"] == 1
    for catalog in bundle.pricing_catalogs:
        found = evaldb.load_definition(db.engine, "pricing_catalog", catalog.id, catalog.version)
        assert found == catalog
    for profile in bundle.test_profiles:
        found = evaldb.load_definition(db.engine, "test_profile", profile.id, profile.version)
        assert found.model_dump_json() == profile.model_dump_json()
    ref = bundle.snapshot.evaluator.test_profile
    assert ref is not None
    rows = select_all(
        db.engine, db.metadata, "snapshot_profile_ref", snapshot_id=bundle.snapshot.id.uuid
    )
    assert [(r["profile_id"], r["profile_version"]) for r in rows] == [(ref.id.uuid, ref.version)]


def test_two_runs_share_one_snapshot_and_its_definitions(db: Db, evaldb: ModuleType) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    other_doc = renew_ids(doc, lists=RECORD_LISTS, verdict=True, run=True)
    second = load_run_bundle_doc(other_doc)
    assert first.run.config_snapshot_id == second.run.config_snapshot_id
    store_bundle(db.engine, evaldb, first)
    before = counts(db.engine, db.metadata)
    result = store_bundle(db.engine, evaldb, second)
    after = counts(db.engine, db.metadata)
    inserted = dict(result.inserted)
    assert "config_snapshot" not in inserted
    assert "persona" not in inserted
    assert "pricing_catalog" not in inserted
    for table in ("config_snapshot", "persona", "rubric", "evaluator", "pricing_catalog"):
        assert after[table] == before[table] == 1, table
    assert after["run"] == 2
    assert after["run_bundle"] == 2
    assert evaldb.load_run_bundle(db.engine, second.id).bundle.model_dump_json() == (
        second.model_dump_json()
    )


# ---------------------------------------------------------------------------------- atomic

ATOMIC_TABLES = [
    "config_snapshot",
    "persona",
    "rubric",
    "evaluator",
    "metric_definition",
    "metric_binding",
    "pricing_catalog",
    "snapshot_member",
    "run",
    "external_ref_index",
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
    "run_bundle_member",
]


@pytest.mark.parametrize("table", ATOMIC_TABLES)
def test_run_bundle_store_is_atomic(db: Db, evaldb: ModuleType, table: str) -> None:
    """A fault at the first INSERT into any table (``turn`` is one of them: after the turns is
    after the records that precede them) leaves the store as it was."""
    bundle = make_bundle(tier="T1", origin="simulated")
    store_project_of(db.engine, evaldb, bundle)
    before = dump(db.engine, db.metadata)
    with fault_on_insert(db.engine, table) as fired, pytest.raises(Boom):
        evaldb.store_run_bundle(db.engine, bundle)
    assert fired, f"the store never inserted into {table}"
    assert dump(db.engine, db.metadata) == before
    # nothing is left behind: the same bundle stores and loads afterwards
    result = evaldb.store_run_bundle(db.engine, bundle)
    assert result.created is True
    assert evaldb.load_run_bundle(db.engine, bundle.id).bundle == bundle


def test_run_bundle_store_is_atomic_after_the_turns_of_a_large_bundle(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = load_run_bundle_doc(sized_bundle_doc(300, 20, 10))
    store_project_of(db.engine, evaldb, bundle)
    before = dump(db.engine, db.metadata)
    with fault_on_insert(db.engine, "exchange") as fired, pytest.raises(Boom):
        evaldb.store_run_bundle(db.engine, bundle)
    assert fired
    assert dump(db.engine, db.metadata) == before


# -------------------------------------------------------------------------------- validity


def _invalid_cases() -> list[Any]:
    def unknown_transcript(b: Any) -> Any:
        turn = b.turns[0].model_copy(update={"transcript_id": ev_ids.TranscriptId.new()})
        return b.model_copy(update={"turns": [turn, *b.turns[1:]]})

    def snapshot_mismatch(b: Any) -> Any:
        run = b.run.model_copy(update={"config_snapshot_id": ev_ids.ConfigSnapshotId.new()})
        return b.model_copy(update={"run": run})

    def digest_tampered(b: Any) -> Any:
        snapshot = b.snapshot.model_copy(update={"id": ev_ids.ConfigSnapshotId.new()})
        return b.model_copy(update={"snapshot": snapshot})

    def conflicting_duplicate(b: Any) -> Any:
        twin = b.turns[0].model_copy(update={"text": "same id, other text"})
        return b.model_copy(update={"turns": [*b.turns, twin]})

    def newer_schema(b: Any) -> Any:
        return b.model_copy(update={"schema_version": "0.0.2"})

    def dropped_result(b: Any) -> Any:
        return b.model_copy(update={"results": b.results[:1]})  # the verdict cites both

    return [
        pytest.param(unknown_transcript, id="turn-names-an-unknown-transcript"),
        pytest.param(snapshot_mismatch, id="run-names-another-snapshot"),
        pytest.param(digest_tampered, id="snapshot-digest-does-not-verify"),
        pytest.param(conflicting_duplicate, id="one-turn-id-two-contents"),
        pytest.param(newer_schema, id="newer-schema-version"),
        pytest.param(dropped_result, id="verdict-cites-a-missing-result"),
    ]


@pytest.mark.parametrize("break_it", _invalid_cases())
def test_invalid_run_bundle_never_stored(
    db: Db, evaldb: ModuleType, break_it: Callable[[Any], Any]
) -> None:
    """The bundle is validated again from its own JSON text (``model_copy`` and
    ``model_construct`` do not validate), before any INSERT."""
    good = make_bundle(tier="T1", origin="simulated")
    store_project_of(db.engine, evaldb, good)
    before = dump(db.engine, db.metadata)
    broken = break_it(good)
    with recording(db.engine) as statements, pytest.raises((ValidationError, TracerSchemaError)):
        evaldb.store_run_bundle(db.engine, broken)
    assert statements.writes == []
    assert dump(db.engine, db.metadata) == before


def test_store_run_bundle_takes_a_run_bundle_only(db: Db, evaldb: ModuleType) -> None:
    bundle = make_bundle(tier="T0", origin="simulated")
    store_project_of(db.engine, evaldb, bundle)
    for wrong in (
        bundle.model_dump(mode="json"),
        bundle.model_dump_json(),
        bundle.run,
        None,
        [bundle],
    ):
        with pytest.raises(TypeError):
            evaldb.store_run_bundle(db.engine, wrong)
    assert sum(counts(db.engine, db.metadata).values()) == counts(db.engine, db.metadata)["project"]


def test_store_run_bundle_refuses_a_bundle_whose_project_is_not_stored(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = make_bundle(tier="T1", origin="simulated")
    with recording(db.engine) as statements, pytest.raises(evaldb.ProjectNotStored) as caught:
        evaldb.store_run_bundle(db.engine, bundle)
    assert isinstance(caught.value, evaldb.StoreError)
    assert caught.value.project_id == bundle.run.project_id
    assert str(bundle.run.project_id) in str(caught.value)
    assert statements.writes == []  # it never creates a project
    assert sum(counts(db.engine, db.metadata).values()) == 0
    # once the project is stored the same bundle goes in
    evaldb.store_project(db.engine, make_project(bundle.run.project_id))
    assert evaldb.store_run_bundle(db.engine, bundle).created is True


@pytest.mark.parametrize("digest", [None, "keyed"])
def test_store_run_bundle_refuses_absent_or_keyed_profile_digest(
    db: Db, evaldb: ModuleType, digest: str | None
) -> None:
    """``snapshot.check_internal_snapshot`` runs before the write (ADR 0019 Ambiguity 10)."""
    bundle = load_run_bundle_doc(
        run_bundle_doc(tier="T1", origin="simulated", with_profile=True, profile_digest=digest)
    )
    store_project_of(db.engine, evaldb, bundle)
    before = dump(db.engine, db.metadata)
    with (
        recording(db.engine) as statements,
        pytest.raises(evaldb.InternalSnapshotRefused) as caught,
    ):
        evaldb.store_run_bundle(db.engine, bundle)
    assert caught.value.snapshot_id == bundle.snapshot.id
    assert statements.writes == []
    assert dump(db.engine, db.metadata) == before


def test_store_run_bundle_refuses_an_integer_it_cannot_store_before_any_write(
    db: Db, evaldb: ModuleType
) -> None:
    """ADR 0019 Ambiguity 5 (A2): a model-valid ``end_ms`` above 2**53 - 1 cannot be hashed, so
    the store raises ``StoreValueOutOfRange`` naming the table and column, before any INSERT."""
    doc = run_bundle_doc(tier="T1", origin="simulated")
    doc["transcripts"][0]["timeline_origin"] = {
        "kind": "artifact",
        "ref": doc["artifacts"][0]["id"],
        "started_at": "2026-10-22T10:00:00.000000Z",
    }
    doc["turns"][0].update({"timing_source": "audio_alignment", "start_ms": 5, "end_ms": 2**53})
    bundle = load_run_bundle_doc(doc)
    store_project_of(db.engine, evaldb, bundle)
    before = dump(db.engine, db.metadata)
    with recording(db.engine) as statements, pytest.raises(evaldb.StoreValueOutOfRange) as caught:
        evaldb.store_run_bundle(db.engine, bundle)
    assert caught.value.table == "turn"
    assert caught.value.column == "doc"
    assert str(2**53) not in str(caught.value)
    assert statements.writes == []
    assert dump(db.engine, db.metadata) == before


# ----------------------------------------------------------------------------- idempotence


@pytest.mark.parametrize("bundle", _cases()[:10])
def test_store_same_run_bundle_twice_is_a_no_op(db: Db, evaldb: ModuleType, bundle: Any) -> None:
    store_bundle(db.engine, evaldb, bundle)
    before = dump(db.engine, db.metadata)
    with recording(db.engine) as statements:
        again = evaldb.store_run_bundle(db.engine, bundle)
    assert again.created is False
    assert dict(again.inserted) == {}
    assert statements.writes == []  # not one INSERT, UPDATE or DELETE
    assert dump(db.engine, db.metadata) == before
    # an equal bundle that is a different instance (a fresh load of the same text) is the same
    twin = ev_loader.load_bundle(RunBundle, bundle.model_dump_json())
    assert evaldb.store_run_bundle(db.engine, twin).created is False
    assert dump(db.engine, db.metadata) == before


def test_same_bundle_id_with_other_members_is_a_conflict(db: Db, evaldb: ModuleType) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    fewer = copy.deepcopy(doc)
    fewer["tool_calls"] = fewer["tool_calls"][:1]  # the same envelope, one member less
    other = load_run_bundle_doc(fewer)
    assert other.id == first.id
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingRecordError):
        evaldb.store_run_bundle(db.engine, other)
    assert dump(db.engine, db.metadata) == before
    changed = copy.deepcopy(doc)
    changed["metadata"] = {"marker": MARKER}
    changed["metadata_flags"] = with_marker_metadata({}, MARKER)["metadata_flags"]
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_run_bundle(db.engine, load_run_bundle_doc(changed))
    assert MARKER not in str(caught.value)
    assert dump(db.engine, db.metadata) == before


# ----------------------------------------------------------------------- rescoring and ownership


def _rescored(doc: dict[str, Any]) -> Any:
    """A rescoring: a new bundle of the same run that lists the shared records and its own results
    and verdict."""
    return load_run_bundle_doc(renew_ids(doc, lists=["results"], verdict=True))


def test_rescoring_bundle_shares_records_without_duplication(db: Db, evaldb: ModuleType) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    second = _rescored(doc)
    assert second.id != first.id
    assert second.run == first.run
    assert [r.id for r in second.results] != [r.id for r in first.results]
    assert second.turns == first.turns
    store_bundle(db.engine, evaldb, first)
    before = counts(db.engine, db.metadata)
    result = evaldb.store_run_bundle(db.engine, second)
    after = counts(db.engine, db.metadata)
    assert dict(result.inserted) == {
        "metric_result": len(second.results),
        "run_verdict": 1,
        "run_bundle": 1,
    }
    expected = dict(before)
    expected["metric_result"] += len(second.results)
    expected["run_verdict"] += 1
    expected["run_bundle"] += 1
    expected["run_bundle_member"] += len(
        expected_side_rows("run_bundle", second)["run_bundle_member"]
    )
    assert after == expected  # every shared record exists once
    for bundle in (first, second):
        loaded = evaldb.load_run_bundle(db.engine, bundle.id)
        assert loaded.complete is True
        assert loaded.bundle.model_dump_json() == bundle.model_dump_json()
    # both bundles list the shared turns; the first still has its own results
    for bundle in (first, second):
        members = select_all(
            db.engine, db.metadata, "run_bundle_member", bundle_id=bundle.id.uuid, kind="turn"
        )
        assert len(members) == len(first.turns)
    # the owner of every run-owned row is still the one run
    for table in LIST_TABLE.values():
        t = db.metadata.tables[table]
        with db.engine.connect() as conn:
            owners = set(conn.execute(sa.select(t.c.run_id)).scalars())
        assert owners == {first.run.id.uuid}, table


def _conflict_cases() -> list[Any]:
    names = [*RECORD_LISTS, "run", "verdict", "snapshot"]
    return [pytest.param(name, id=name) for name in names]


@pytest.mark.parametrize("target", _conflict_cases())
def test_rescoring_bundle_conflicting_record_rejected(
    db: Db, evaldb: ModuleType, target: str
) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    changed = renew_ids(doc)  # a new bundle ID, the same run and records ...
    if target in RECORD_LISTS:
        record = changed[target][0]
        changed[target][0] = with_marker_metadata(record, MARKER)
        table, record_id = LIST_TABLE[target], record["id"]
    elif target == "snapshot":
        record = changed["snapshot"]
        changed["snapshot"] = redigest("ConfigSnapshot", with_marker_metadata(record, MARKER))
        table, record_id = "config_snapshot", record["id"]
    else:
        record = changed[target]
        changed[target] = with_marker_metadata(record, MARKER)
        table = {"run": "run", "verdict": "run_verdict"}[target]
        record_id = record["id"]
    second = load_run_bundle_doc(changed)  # ... but one record has other content
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_run_bundle(db.engine, second)
    assert not isinstance(caught.value, ConflictingVersionError)
    assert not isinstance(caught.value, evaldb.ReimportConflict)  # the run has no reference
    text = str(caught.value)
    assert table in text
    assert record_id in text  # the kind and the ID
    assert MARKER not in text  # and none of the content
    assert dump(db.engine, db.metadata) == before  # not even the new bundle row
    assert evaldb.load_run_bundle(db.engine, first.id).bundle == first


def test_run_bundle_conflicting_pricing_catalog_rejected(db: Db, evaldb: ModuleType) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    bundle = load_run_bundle_doc(doc)
    catalog_doc = doc["pricing_catalogs"][0]
    other = load_entity(
        "PricingCatalog",
        json.dumps(redigest("PricingCatalog", with_marker_metadata(catalog_doc, MARKER))),
    )
    evaldb.store_definition(db.engine, other)
    store_project_of(db.engine, evaldb, bundle)
    before = dump(db.engine, db.metadata)
    with pytest.raises(ConflictingVersionError) as caught:
        evaldb.store_run_bundle(db.engine, bundle)
    assert MARKER not in str(caught.value)
    assert dump(db.engine, db.metadata) == before


OWNERSHIP_TARGETS = [*RECORD_LISTS, "verdict"]


@pytest.mark.parametrize("target", OWNERSHIP_TARGETS)
def test_record_owned_by_another_run_rejected(db: Db, evaldb: ModuleType, target: str) -> None:
    """A record of fact belongs to one run. A second run whose bundle holds a record with the
    ID of a stored record of the first run is refused, whether or not the content is equal
    (the ownership is checked before the content)."""
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    renewed = [name for name in RECORD_LISTS if name != target]
    second_doc = renew_ids(doc, lists=renewed, verdict=target != "verdict", run=True)
    second = load_run_bundle_doc(second_doc)
    assert second.run.id != first.run.id
    shared_id = first.verdict.id if target == "verdict" else getattr(first, target)[0].id
    table = "run_verdict" if target == "verdict" else LIST_TABLE[target]
    store_project_of(db.engine, evaldb, second)
    before = dump(db.engine, db.metadata)
    with pytest.raises(evaldb.RecordOwnershipConflict) as caught:
        evaldb.store_run_bundle(db.engine, second)
    assert isinstance(caught.value, evaldb.StoreError)
    assert caught.value.kind == table
    assert caught.value.record_id == shared_id
    assert table in str(caught.value)
    assert str(shared_id) in str(caught.value)
    assert dump(db.engine, db.metadata) == before
    assert evaldb.load_run_bundle(db.engine, first.id).bundle == first


# ---------------------------------------------------------------------- tiers and tri-state


def test_tdeg_bundles_round_trip_identical_table_set(db: Db, evaldb: ModuleType) -> None:
    """Storage does not depend on the observed tier. Every bundle fills exactly the tables its own
    member lists call for (the same function for every tier), and a tier that holds no tool calls
    or spans writes no row for them: missing evidence stays absent."""
    named = dict(tdeg_bundles())
    assert set(named) == {
        f"{c}-{o}" for c in ("T0", "T0m", "T1", "T2") for o in ("simulated", "human")
    }
    filled: dict[str, set[str]] = {}
    for name, bundle in named.items():
        before = counts(db.engine, db.metadata)
        store_bundle(db.engine, evaldb, bundle)
        loaded = evaldb.load_run_bundle(db.engine, bundle.id)
        assert loaded.bundle.model_dump_json() == bundle.model_dump_json(), name
        after = counts(db.engine, db.metadata)
        filled[name] = {table for table in after if after[table] > before[table]}
        assert filled[name] == table_set_of(bundle), name
    for origin in ("simulated", "human"):
        assert filled[f"T1-{origin}"] == filled[f"T2-{origin}"]
        assert filled[f"T0-{origin}"] == filled[f"T0m-{origin}"]
        assert filled[f"T1-{origin}"] - filled[f"T0-{origin}"] == {
            "tool_call_event",
            "stage_span",
            "external_ref_index",
        }
        assert not filled[f"T0-{origin}"] - filled[f"T1-{origin}"]
    assert filled["T1-simulated"] == filled["T1-human"]
    assert filled["T0-simulated"] == filled["T0-human"]


def test_not_applicable_results_projected_null_never_zero_or_false(
    db: Db, evaldb: ModuleType
) -> None:
    total_na = 0
    for name, bundle in tdeg_bundles():
        store_bundle(db.engine, evaldb, bundle)
        rows = {
            r["id"]: r
            for r in select_all(db.engine, db.metadata, "metric_result", run_id=bundle.run.id.uuid)
        }
        assert len(rows) == len(bundle.results), name
        for result in bundle.results:
            row = rows[result.id.uuid]
            assert row["status"] == result.status
            assert row["na_reason"] == result.na_reason
            if result.status == "not_applicable":
                total_na += 1
                assert row["passed"] is None, (name, str(result.id))  # not 0, not False
                assert row["na_reason"] is not None
            else:
                assert row["passed"] is result.passed or row["passed"] == result.passed
        t = db.metadata.tables["metric_result"]
        with db.engine.connect() as conn:
            bad = conn.execute(
                sa.select(sa.func.count()).where(
                    t.c.status == "not_applicable", t.c.passed.is_not(None)
                )
            ).scalar_one()
        assert bad == 0
        loaded = evaldb.load_run_bundle(db.engine, bundle.id).bundle
        assert [r.passed for r in loaded.results] == [r.passed for r in bundle.results]
    assert total_na >= 9  # the T0 bundles hold nine each: the test is not vacuous


def test_not_available_cost_projected_without_amount_or_basis(db: Db, evaldb: ModuleType) -> None:
    seen = 0
    summaries_without_total = 0
    for name, bundle in tdeg_bundles():
        store_bundle(db.engine, evaldb, bundle)
        rows = {
            r["id"]: r
            for r in select_all(db.engine, db.metadata, "cost_record", run_id=bundle.run.id.uuid)
        }
        for record in bundle.cost_records:
            row = rows[record.id.uuid]
            assert row["status"] == record.status
            if record.status == "not_available":
                seen += 1
                assert row["amount"] is None, name
                assert row["basis"] is None, name
                assert row["currency"] is None, name
                assert row["na_reason"] is not None, name
        sums = {
            r["id"]: r
            for r in select_all(db.engine, db.metadata, "cost_summary", run_id=bundle.run.id.uuid)
        }
        for summary in bundle.cost_summaries:
            if summary.total is None:
                summaries_without_total += 1
                assert sums[summary.id.uuid]["total"] is None
                assert sums[summary.id.uuid]["currency"] is None
        loaded = evaldb.load_run_bundle(db.engine, bundle.id).bundle
        assert loaded.model_dump_json() == bundle.model_dump_json()
    assert seen >= 4
    assert summaries_without_total >= 4  # not vacuous


# ----------------------------------------------------------------- huge calls and statements


def test_huge_call_bundle_round_trip(db: Db, evaldb: ModuleType) -> None:
    """4,000 turns, 600 tool calls, 400 usage records with their costs and one summary: the Phase 1
    huge-call shape (A-05), through the store and back."""
    doc = sized_bundle_doc(4000, 600, 400)
    bundle = load_run_bundle_doc(doc)
    assert len(bundle.turns) == 4000
    assert len(bundle.tool_calls) == 600
    original = bundle.model_dump_json()
    store_bundle(db.engine, evaldb, bundle)
    after = counts(db.engine, db.metadata)
    assert after["turn"] == 4000
    assert after["tool_call_event"] == 600
    assert after["cost_record"] == 400
    loaded = evaldb.load_run_bundle(db.engine, bundle.id)
    assert loaded.complete is True
    assert loaded.bundle is not None
    assert len(loaded.bundle.turns) == 4000
    assert [t.seq for t in loaded.bundle.turns] == list(range(4000))
    assert loaded.bundle.model_dump_json() == original
    assert loaded.bundle == bundle


def test_store_run_bundle_statement_count_independent_of_record_count(
    db: Db, evaldb: ModuleType
) -> None:
    """The number of statements the engine executes during ``store_run_bundle`` (one
    ``before_cursor_execute`` event per statement; an ``executemany`` is one) is the same for a
    bundle of 4 turns and for bundles of 400 and of 4,000 turns, as long as the bundles fill the
    same tables, and no table is the target of more than one INSERT."""
    shapes = [(4, 2, 2), (400, 60, 40), (4000, 600, 400)]
    observed: list[tuple[int, list[str]]] = []
    for turns, calls, usages in shapes:
        bundle = load_run_bundle_doc(sized_bundle_doc(turns, calls, usages))
        store_project_of(db.engine, evaldb, bundle)
        with recording(db.engine) as statements:
            evaldb.store_run_bundle(db.engine, bundle)
        observed.append((statements.count, statements.inserts))
        tables_filled = set(statements.inserts)
        assert {"turn", "tool_call_event", "usage_record", "cost_record"} <= tables_filled
    counts_seen = [count for count, _ in observed]
    assert counts_seen[0] > 0
    assert len(set(counts_seen)) == 1, counts_seen
    for _, inserts in observed:
        assert len(inserts) == len(set(inserts)), "a table was the target of two INSERT statements"
    assert set(observed[0][1]) == set(observed[1][1]) == set(observed[2][1])


# ------------------------------------------------------------------------ incomplete loads


@pytest.mark.parametrize("reverse", [False, True], ids=["natural-order", "lists-reversed"])
def test_run_bundle_load_reports_incomplete_after_member_deletion(
    db: Db, evaldb: ModuleType, reverse: bool
) -> None:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    bundle = load_run_bundle_doc(_reversed(doc) if reverse else doc)
    store_bundle(db.engine, evaldb, bundle)
    catalog = bundle.pricing_catalogs[0]
    gone_turns = [
        bundle.turns[1],
        bundle.turns[3],
    ]  # in list order, which is not ID order if reversed
    turn = gone_turns[0]
    call = bundle.tool_calls[1]
    turn_table, call_table = db.metadata.tables["turn"], db.metadata.tables["tool_call_event"]
    catalog_table = db.metadata.tables["pricing_catalog"]
    with db.engine.begin() as conn:  # ADR 0020: a deletion removes rows, never edits one
        conn.execute(sa.delete(catalog_table).where(catalog_table.c.id == catalog.id.uuid))
        for gone in gone_turns:
            conn.execute(sa.delete(turn_table).where(turn_table.c.id == gone.id.uuid))
        conn.execute(sa.delete(call_table).where(call_table.c.id == call.id.uuid))
    expected_missing = [
        ("pricing_catalog", catalog.id, catalog.version),
        ("turn", gone_turns[0].id, None),
        ("turn", gone_turns[1].id, None),
        ("tool_call", call.id, None),
    ]
    with pytest.raises(evaldb.IncompleteRunBundle) as caught:
        evaldb.load_run_bundle(db.engine, bundle.id)
    assert isinstance(caught.value, evaldb.StoreError)
    assert caught.value.bundle_id == bundle.id
    assert [
        (m.kind, m.record_id, m.record_version) for m in caught.value.missing
    ] == expected_missing
    assert str(bundle.id) in str(caught.value)
    assert turn.text not in str(caught.value)
    assert call.name not in str(caught.value)  # no content
    with pytest.raises(evaldb.IncompleteRunBundle):
        evaldb.load_run_bundle(db.engine, bundle.id, allow_incomplete=False)
    partial = evaldb.load_run_bundle(db.engine, bundle.id, allow_incomplete=True)
    assert partial.complete is False
    assert partial.bundle is None  # a partial set is never returned as a RunBundle
    assert partial.bundle_id == bundle.id
    assert [(m.kind, m.record_id, m.record_version) for m in partial.missing] == expected_missing
    assert all(isinstance(m, evaldb.MissingMember) for m in partial.missing)
    expected_kinds = {
        kind
        for kind, _table, field in MEMBER_KINDS
        if (len(getattr(bundle, field)) if isinstance(getattr(bundle, field), list) else 1)
        - sum(1 for m in partial.missing if m.kind == kind)
        > 0
    }
    assert set(partial.records) == expected_kinds
    assert "pricing_catalog" not in partial.records  # its only record is gone
    assert [t.id for t in partial.records["turn"]] == [
        t.id for t in bundle.turns if t not in gone_turns
    ]
    assert [c.id for c in partial.records["tool_call"]] == [bundle.tool_calls[0].id]
    assert [r.model_dump_json() for r in partial.records["result"]] == [
        r.model_dump_json() for r in bundle.results
    ]
    assert partial.records["run"][0] == bundle.run
    assert partial.records["verdict"][0] == bundle.verdict
    # the rest of the store is untouched, and a complete bundle with allow_incomplete=True is whole
    other = make_bundle(tier="T0", origin="simulated")
    store_bundle(db.engine, evaldb, other)
    whole = evaldb.load_run_bundle(db.engine, other.id, allow_incomplete=True)
    assert whole.complete is True
    assert whole.missing == ()
    assert whole.records == {}
    assert whole.bundle == other


def test_run_bundle_load_reports_a_missing_verdict_and_a_missing_transcript(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = make_bundle(tier="T1", origin="simulated")
    store_bundle(db.engine, evaldb, bundle)
    verdict_table = db.metadata.tables["run_verdict"]
    transcript_table = db.metadata.tables["transcript"]
    with db.engine.begin() as conn:
        conn.execute(sa.delete(verdict_table).where(verdict_table.c.id == bundle.verdict.id.uuid))
        conn.execute(
            sa.delete(transcript_table).where(
                transcript_table.c.id == bundle.transcripts[0].id.uuid
            )
        )
    partial = evaldb.load_run_bundle(db.engine, bundle.id, allow_incomplete=True)
    assert [(m.kind, m.record_id) for m in partial.missing] == [
        ("transcript", bundle.transcripts[0].id),
        ("verdict", bundle.verdict.id),
    ]
    assert "verdict" not in partial.records
    assert "transcript" not in partial.records
    assert len(partial.records["turn"]) == len(bundle.turns)  # the turns stay, whoever names them


def test_load_run_bundle_of_a_deleted_run_or_unknown_id_is_not_stored(
    db: Db, evaldb: ModuleType
) -> None:
    with pytest.raises(evaldb.RecordNotStored) as caught:
        evaldb.load_run_bundle(db.engine, ev_ids.RunBundleId.new())
    assert caught.value.kind == "run_bundle"
    bundle = make_bundle(tier="T0", origin="simulated")
    store_bundle(db.engine, evaldb, bundle)
    run_table = db.metadata.tables["run"]
    with db.engine.begin() as conn:  # the bundle goes with its run (ADR 0020, foreign key 17)
        conn.execute(sa.delete(run_table).where(run_table.c.id == bundle.run.id.uuid))
    with pytest.raises(evaldb.RecordNotStored):
        evaldb.load_run_bundle(db.engine, bundle.id, allow_incomplete=True)


# ---------------------------------------------------------------------------------- N18


def test_n18_changed_language_declaration_is_a_new_transcript_in_a_new_bundle(
    db: Db, evaldb: ModuleType
) -> None:
    """A transcript is never edited: correcting its declaration is a new Transcript with new Turn
    records, the results recomputed over them, in a new RunBundle of the same run. The old
    transcript, its turns and the results that cite them stay (ADR 0022 Decision 12)."""
    doc = run_bundle_doc(tier="T1", origin="simulated", declared=True)
    first = load_run_bundle_doc(doc)
    assert first.transcripts[0].declared_language is not None
    corrected = renew_ids(doc, lists=["transcripts", "turns", "results"], verdict=True)
    corrected["transcripts"][0]["declared_language"]["declared_by"] = "operator-2"
    second = load_run_bundle_doc(corrected)
    assert second.transcripts[0].id != first.transcripts[0].id
    assert second.transcripts[0].declared_language != first.transcripts[0].declared_language
    store_bundle(db.engine, evaldb, first)
    before = counts(db.engine, db.metadata)
    result = evaldb.store_run_bundle(db.engine, second)
    after = counts(db.engine, db.metadata)
    assert dict(result.inserted) == {
        "transcript": 1,
        "turn": len(second.turns),
        "metric_result": len(second.results),
        "run_verdict": 1,
        "run_bundle": 1,
    }
    assert after["transcript"] == before["transcript"] + 1
    assert after["turn"] == before["turn"] + len(second.turns)  # (transcript_id, seq) never repeats
    for bundle in (first, second):
        assert evaldb.load_run_bundle(db.engine, bundle.id).bundle == bundle
    old = evaldb.load_run_bundle(db.engine, first.id).bundle
    assert old.transcripts[0].declared_language == first.transcripts[0].declared_language
