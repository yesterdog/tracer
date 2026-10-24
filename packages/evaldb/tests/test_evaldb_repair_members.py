# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch G, P1 privacy: ``store_run_bundle`` never brings back a deleted member (the
reversal of batch C ambiguity A6).

Contract: docs/phase2/api-surface-batch-g.md section 10. When the run of a bundle is stored,
members that are absent (a test profile, a pricing catalog or a definition removed by
``delete_test_profile`` or ``delete_definition``) stay absent: ``store_run_bundle`` does not insert
them again, ``load_run_bundle`` keeps reporting them (``IncompleteRunBundle``) and
``verify_database`` keeps the note ``bundle_member_absent``. The keyword-only
``repair_members: bool = False`` is the only way to insert them again; it is an explicit
data-restoring act and writes no audit row (audit rows are content-free). A run that is not
stored is a new import, as before.
"""

from __future__ import annotations

import inspect
import json
import re
from types import ModuleType
from typing import Any

import pytest

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_deletion_support import (  # type: ignore[import-not-found,unused-ignore]
    ACTOR,
    REASON,
    audit_rows,
    count_where,
)
from evaldb_migration_support import raw_dump  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    load_run_bundle_doc,
    recording,
    renew_ids,
    store_bundle,
    support,
)
from evaldb_support import Db  # type: ignore[import-not-found,unused-ignore]

db = _support.db

# (member kind as run_bundle_member names it, table, how to find the record in a bundle)
KINDS = (
    "test_profile",
    "pricing_catalog",
    "persona",
    "rubric",
    "evaluator",
    "metric_definition",
    "metric_binding",
)


def _doc() -> dict[str, Any]:
    """A T1 run of a human caller whose snapshot names a test profile, and the bundle lists it
    (as ``populate`` of batch D does)."""
    built = support("_support_b7").world(
        tier="T1", origin="human", with_profile=True, profile_digest="plain"
    )
    doc: dict[str, Any] = built.doc
    doc["test_profiles"] = [built.parts["profile"].model_dump(mode="json")]
    return doc


BUNDLE_MEMBERS = ("test_profile", "pricing_catalog")


def _bundle() -> Any:
    return load_run_bundle_doc(_doc())


def _record(bundle: Any, kind: str) -> Any:
    if kind == "test_profile":
        return bundle.test_profiles[0]
    if kind == "pricing_catalog":
        return bundle.pricing_catalogs[0]
    found = getattr(bundle.snapshot, kind, None)
    if found is None:
        found = getattr(bundle.snapshot, kind + "s")[0]
    return found


def _delete(evaldb: ModuleType, db: Db, bundle: Any, kind: str) -> Any:
    record = _record(bundle, kind)
    if kind == "test_profile":
        evaldb.delete_test_profile(db.engine, record.id, actor=ACTOR, reason=REASON)
    else:
        evaldb.delete_definition(db.engine, kind, record.id, actor=ACTOR, reason=REASON)
    return record


def _present(db: Db, kind: str, record: Any) -> int:
    return int(count_where(db.engine, db.metadata, kind, id=record.id.uuid))


def _missing_kinds(evaldb: ModuleType, db: Db, bundle: Any) -> list[str]:
    """The kinds ``load_run_bundle`` reports as missing (empty: it loads whole)."""
    try:
        evaldb.load_run_bundle(db.engine, bundle.id)
    except evaldb.IncompleteRunBundle as error:
        return [m.kind for m in error.missing]
    return []


# IDs, digests and instants are references the store keeps by design, not personal data
_NOT_PERSONAL = re.compile(
    r"[a-z]{2,4}_[0-9a-f-]{36}|[0-9a-f]{64}|sha256[-a-z0-9/]*|\d{4}-\d\d-\d\dT[\d:.]+Z|\d+\.\d+\.\d+"
)


def _profile_texts(profile: Any) -> list[str]:
    """Distinctive strings of a test profile (its personal data)."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if len(value) >= 6 and not _NOT_PERSONAL.fullmatch(value):
                found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(profile.model_dump_json()))
    return found


# ======================================================================= the keyword


def test_repair_members_is_a_keyword_only_bool_defaulting_to_false(evaldb: ModuleType) -> None:
    parameter = inspect.signature(evaldb.store_run_bundle).parameters["repair_members"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False
    for name in ("store_project", "store_definition", "store_snapshot", "store_dataset_item"):
        assert "repair_members" not in inspect.signature(getattr(evaldb, name)).parameters


def test_repair_members_must_be_a_bool_and_is_checked_before_anything_is_written(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = _bundle()
    evaldb.store_project(db.engine, _support.load_entity("Project", _project_text(bundle)))
    before = raw_dump(db.engine)
    with recording(db.engine) as statements:
        for bad in (1, 0, None, "yes", "", [], (True,), 1.0, object()):
            with pytest.raises(TypeError):
                evaldb.store_run_bundle(db.engine, bundle, repair_members=bad)
        with pytest.raises(TypeError):
            evaldb.store_run_bundle(db.engine, bundle, True)  # keyword-only
    assert statements.count == 0
    assert raw_dump(db.engine) == before


def _project_text(bundle: Any) -> str:
    from evaldb_repo_support import project_doc  # type: ignore[import-not-found,unused-ignore]

    return json.dumps(project_doc(str(bundle.run.project_id)))


# ================================================================ default: nothing comes back


@pytest.mark.parametrize("kind", KINDS)
def test_storing_the_same_bundle_again_does_not_bring_back_a_deleted_member(
    db: Db, evaldb: ModuleType, kind: str
) -> None:
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    record = _delete(evaldb, db, bundle, kind)
    assert _present(db, kind, record) == 0
    before = raw_dump(db.engine)
    with recording(db.engine) as statements:
        result = evaldb.store_run_bundle(db.engine, bundle)
    assert result.created is False
    assert dict(result.inserted) == {}
    assert statements.writes == []
    assert _present(db, kind, record) == 0
    assert raw_dump(db.engine) == before
    # a profile and a catalog are members of the bundle; a definition is embedded in the snapshot
    assert _missing_kinds(evaldb, db, bundle) == ([kind] if kind in BUNDLE_MEMBERS else [])


@pytest.mark.parametrize("kind", KINDS)
def test_default_and_explicit_false_behave_alike(db: Db, evaldb: ModuleType, kind: str) -> None:
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    record = _delete(evaldb, db, bundle, kind)
    before = raw_dump(db.engine)
    result = evaldb.store_run_bundle(db.engine, bundle, repair_members=False)
    assert dict(result.inserted) == {}
    assert _present(db, kind, record) == 0
    assert raw_dump(db.engine) == before


def test_a_deleted_test_profile_leaves_no_personal_data_after_a_second_store(
    db: Db, evaldb: ModuleType
) -> None:
    """The test profile is personal data: after ``delete_test_profile`` and a store of the same
    bundle, no string of the profile is in any row of any table."""
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    needles = _profile_texts(bundle.test_profiles[0])
    assert needles
    stored = "\n".join(line for rows in raw_dump(db.engine).values() for line in rows)
    assert any(n in stored for n in needles), "the needles are not what the store holds"
    _delete(evaldb, db, bundle, "test_profile")
    evaldb.store_run_bundle(db.engine, bundle)
    evaldb.store_run_bundle(db.engine, bundle, repair_members=False)
    stored = "\n".join(line for rows in raw_dump(db.engine).values() for line in rows)
    assert [n for n in needles if n in stored] == []


def test_a_rescoring_of_a_stored_run_does_not_bring_back_the_deleted_member(
    db: Db, evaldb: ModuleType
) -> None:
    """A different bundle of the same stored run (new results and verdict) is stored; the
    deleted profile that the first bundle listed stays absent; the new rows are inserted."""
    doc = _doc()
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    profile = _delete(evaldb, db, first, "test_profile")
    second = load_run_bundle_doc(renew_ids(doc, lists=["results"], verdict=True))
    result = evaldb.store_run_bundle(db.engine, second)
    assert result.created is True
    assert "test_profile" not in result.inserted
    assert result.inserted.get("metric_result", 0) > 0
    assert _present(db, "test_profile", profile) == 0
    assert _missing_kinds(evaldb, db, second) == ["test_profile"]


def test_storing_a_bundle_twice_without_a_deletion_is_still_a_no_op(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = _bundle()
    first = store_bundle(db.engine, evaldb, bundle)
    assert first.created is True
    before = raw_dump(db.engine)
    for kwargs in ({}, {"repair_members": False}, {"repair_members": True}):
        with recording(db.engine) as statements:
            again = evaldb.store_run_bundle(db.engine, bundle, **kwargs)
        assert again.created is False
        assert dict(again.inserted) == {}
        assert statements.writes == []
        assert raw_dump(db.engine) == before


def test_verify_database_keeps_the_absent_member_note_after_a_second_store(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    _delete(evaldb, db, bundle, "test_profile")
    evaldb.store_run_bundle(db.engine, bundle)
    report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    assert [(f.kind, f.problem, f.location) for f in report.informational] == [
        ("run_bundle", "bundle_member_absent", "test_profile")
    ]


# ============================================================ a fresh run is a new import


def test_a_run_that_is_not_stored_is_imported_whole_as_before(db: Db, evaldb: ModuleType) -> None:
    """After ``delete_run`` the run row is absent, so the same bundle is a new import: everything
    it lists, a profile deleted earlier included, is inserted (the profile belongs to the arriving
    bundle)."""
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    profile = _delete(evaldb, db, bundle, "test_profile")
    evaldb.delete_run(db.engine, bundle.run.id, actor=ACTOR, reason=REASON)
    result = evaldb.store_run_bundle(db.engine, bundle)
    assert result.created is True
    assert result.inserted.get("test_profile") == 1
    assert _present(db, "test_profile", profile) == 1
    assert evaldb.load_run_bundle(db.engine, bundle.id).bundle == bundle


# ====================================================================== repair_members=True


@pytest.mark.parametrize("kind", KINDS)
def test_repair_members_inserts_the_deleted_member_again(
    db: Db, evaldb: ModuleType, kind: str
) -> None:
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    audit_before = audit_rows(db.engine, db.metadata)
    record = _delete(evaldb, db, bundle, kind)
    audit_after_delete = audit_rows(db.engine, db.metadata)
    assert len(audit_after_delete) == len(audit_before) + 1
    result = evaldb.store_run_bundle(db.engine, bundle, repair_members=True)
    assert result.created is False  # the bundle itself was stored
    assert dict(result.inserted) == {kind: 1}
    assert _present(db, kind, record) == 1
    assert evaldb.load_run_bundle(db.engine, bundle.id).bundle == bundle
    report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    assert report.informational == ()
    # no audit row for the repair: audit rows are content-free and say what was deleted
    assert audit_rows(db.engine, db.metadata) == audit_after_delete


def test_repair_members_inserts_every_deleted_member_in_one_call(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    for kind in KINDS:
        _delete(evaldb, db, bundle, kind)
    assert sorted(_missing_kinds(evaldb, db, bundle)) == sorted(BUNDLE_MEMBERS)
    result = evaldb.store_run_bundle(db.engine, bundle, repair_members=True)
    assert dict(result.inserted) == dict.fromkeys(KINDS, 1)
    assert evaldb.load_run_bundle(db.engine, bundle.id).bundle == bundle
    assert evaldb.verify_database(db.engine).informational == ()


def test_repair_members_on_a_store_that_lost_nothing_changes_nothing(
    db: Db, evaldb: ModuleType
) -> None:
    bundle = _bundle()
    store_bundle(db.engine, evaldb, bundle)
    before = raw_dump(db.engine)
    result = evaldb.store_run_bundle(db.engine, bundle, repair_members=True)
    assert (result.created, dict(result.inserted)) == (False, {})
    assert raw_dump(db.engine) == before


def test_repair_members_makes_no_difference_for_a_run_that_is_not_stored(
    db: Db, evaldb: ModuleType
) -> None:
    """For a run that is not stored the flag changes nothing: the same inserts either way."""
    first, second = _bundle(), _bundle()
    evaldb.store_project(db.engine, _support.load_entity("Project", _project_text(first)))
    evaldb.store_project(db.engine, _support.load_entity("Project", _project_text(second)))
    plain = evaldb.store_run_bundle(db.engine, first)
    repaired = evaldb.store_run_bundle(db.engine, second, repair_members=True)
    assert (plain.created, repaired.created) == (True, True)
    assert dict(plain.inserted).keys() == dict(repaired.inserted).keys()
    assert evaldb.load_run_bundle(db.engine, first.id).bundle == first
    assert evaldb.load_run_bundle(db.engine, second.id).bundle == second
