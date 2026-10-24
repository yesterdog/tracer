# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C, P2-12c: exports at rest (ADR 0022 Decision 9, ADR 0020 Decision 5).

Contract: docs/phase2/api-surface-batch-c.md. An export is stored as one opaque document plus link
rows; ``expected_purpose`` is keyword-only and required at both entry points. The golden exports
(four purposes and shapes) are stored after their runs, dataset items and test profiles exist as
stub rows (``store_export`` only needs them to be stored); one end-to-end case stores a real run
bundle first and exports it with a sealed recording.
"""

from __future__ import annotations

import inspect
import json
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    counts,
    dump,
    load_run_bundle_doc,
    pause_on_insert,
    recording,
    run_bundle_doc,
    run_in_thread,
    select_all,
    store_bundle,
    support,
    verify_bundle_digests,
    with_marker_metadata,
)
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    GOLDEN,
    Db,
    expected_side_rows,
    golden_valid_files,
    load_entity,
    row_set_equal,
)
from tracer_evalschema import ids as ev_ids
from tracer_evalschema import loader as ev_loader
from tracer_evalschema.bundle import ExportBundle
from tracer_evalschema.errors import BundleSizeError, ConflictingRecordError, TracerSchemaError

db = _support.db

PURPOSES = ("internal", "shared_export", "benchmark")
LINK_TABLES = ("export_bundle_run", "export_bundle_item", "export_bundle_profile")
MARKER = "CONFLICT-MARKER-5be2"
LIMIT = 256 * 1024 * 1024


def _golden_export_cases() -> list[Any]:
    return [pytest.param(p, id=p.stem) for p in golden_valid_files("ExportBundle")]


def _load(path: Path) -> Any:
    return load_entity("ExportBundle", path.read_text("utf-8"))


def _stub_dependencies(db: Db, export: Any, *, skip: str | None = None) -> None:
    """Rows for the runs, dataset items and test profiles an export carries (``store_export``
    needs them stored, nothing else about them). ``skip`` leaves one kind out."""
    links = expected_side_rows("export_bundle", export)
    with db.engine.begin() as conn:
        if skip != "run":
            for row in links["export_bundle_run"]:
                db.rows.insert(conn, "run", id=row["run_id"])
        if skip != "dataset_item":
            for row in links["export_bundle_item"]:
                db.rows.insert(conn, "dataset_item", id=row["item_id"])
        if skip != "test_profile":
            for row in links["export_bundle_profile"]:
                db.rows.insert(conn, "test_profile", id=row["profile_id"], version=1)


def _end_to_end(db: Db, evaldb: ModuleType) -> tuple[Any, Any]:
    """A real stored run bundle (human caller, with a test profile) and an internal export of it
    that carries a really sealed recording. Returns (export, bundle)."""
    b7 = support("_support_b7")
    doc = run_bundle_doc(tier="T1", origin="human", with_profile=True, profile_digest="plain")
    bundle = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, bundle)
    artifact = doc["artifacts"][0]["id"]
    export_doc = b7.export_doc(
        "internal", [doc], sealed_payloads=[b7.sealed_doc("audio", artifact)]
    )
    return b7.load_export(export_doc), bundle


# --------------------------------------------------------------------- the required purpose


def test_store_export_requires_expected_purpose(db: Db, evaldb: ModuleType) -> None:
    export = _load(GOLDEN / "ExportBundle" / "valid" / "minimal.json")
    parameter = inspect.signature(evaldb.store_export).parameters["expected_purpose"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        evaldb.store_export(db.engine, export)
    with pytest.raises(TypeError):
        evaldb.store_export(db.engine, export, "internal")
    for wrong in (None, 1, b"internal", ["internal"]):
        with pytest.raises(TypeError):
            evaldb.store_export(db.engine, export, expected_purpose=wrong)
    for bad in ("", "Internal", "internal ", "shared", "any"):
        with pytest.raises(ValueError, match="purpose"):
            evaldb.store_export(db.engine, export, expected_purpose=bad)
    assert sum(counts(db.engine, db.metadata).values()) == 0
    assert evaldb.store_export(db.engine, export, expected_purpose="internal").created is True


def test_load_export_requires_expected_purpose(db: Db, evaldb: ModuleType) -> None:
    export = _load(GOLDEN / "ExportBundle" / "valid" / "minimal.json")
    evaldb.store_export(db.engine, export, expected_purpose="internal")
    parameter = inspect.signature(evaldb.load_export).parameters["expected_purpose"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        evaldb.load_export(db.engine, export.id)
    with pytest.raises(TypeError):
        evaldb.load_export(db.engine, export.id, "internal")
    for wrong in (None, 1, b"internal"):
        with pytest.raises(TypeError):
            evaldb.load_export(db.engine, export.id, expected_purpose=wrong)
    for bad in ("", "Internal", "shared", "any"):
        with pytest.raises(ValueError, match="purpose"):
            evaldb.load_export(db.engine, export.id, expected_purpose=bad)
    assert evaldb.load_export(db.engine, export.id, expected_purpose="internal") == export


@pytest.mark.parametrize("path", _golden_export_cases())
def test_store_export_purpose_mismatch_rejected(db: Db, evaldb: ModuleType, path: Path) -> None:
    export = _load(path)
    _stub_dependencies(db, export)
    before = dump(db.engine, db.metadata)
    forms: list[Any] = [export, export.model_dump_json(), export.model_dump_json().encode()]
    for other in PURPOSES:
        if other == export.purpose:
            continue
        for form in forms:
            with (
                recording(db.engine) as statements,
                pytest.raises(ValueError, match=r"\[purpose\]"),
            ):
                evaldb.store_export(db.engine, form, expected_purpose=other)
            assert statements.writes == []
    assert dump(db.engine, db.metadata) == before
    # the stored export is read with the purpose it was stored under, and only that one
    evaldb.store_export(db.engine, export, expected_purpose=export.purpose)
    for other in PURPOSES:
        if other != export.purpose:
            with pytest.raises(ValueError, match=r"\[purpose\]"):
                evaldb.load_export(db.engine, export.id, expected_purpose=other)


# ----------------------------------------------------------------------------- round trips


def _canonical_variants(export: Any) -> list[Any]:
    text = export.model_dump_json()
    pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    assert pretty != text
    return [export, text, text.encode("utf-8"), pretty, pretty.encode("utf-8")]


@pytest.mark.parametrize("path", _golden_export_cases())
def test_export_round_trip_byte_identical(db: Db, evaldb: ModuleType, path: Path) -> None:
    export = _load(path)
    original = export.model_dump_json()
    _stub_dependencies(db, export)
    result = evaldb.store_export(db.engine, export, expected_purpose=export.purpose)
    assert result.created is True
    assert result.kind == "export_bundle"
    assert result.entity_id == export.id
    assert dict(result.inserted) == {"export_bundle": 1}
    row = select_all(db.engine, db.metadata, "export_bundle", id=export.id.uuid)[0]
    assert row["doc"] == original  # the canonical text, not the caller's bytes
    assert row["purpose"] == export.purpose
    assert row["byte_length"] == len(original.encode("utf-8"))
    loaded = evaldb.load_export(db.engine, export.id, expected_purpose=export.purpose)
    assert loaded == export
    assert loaded.model_dump_json() == original
    assert loaded.model_dump(mode="json") == export.model_dump(mode="json")
    assert evaldb.load_export(db.engine, export.id.uuid, expected_purpose=export.purpose) == export
    for run_bundle in loaded.run_bundles:
        if export.purpose == "internal":  # shared and benchmark copies drop or key their digests
            verify_bundle_digests(run_bundle)
    # the link rows say which runs, items and profiles the export holds
    expected = expected_side_rows("export_bundle", export)
    for table in LINK_TABLES:
        rows = select_all(db.engine, db.metadata, table, export_id=export.id.uuid)
        assert row_set_equal(rows, expected[table]), table
    # every input form (instance, text, bytes, re-indented text) is the same stored export
    before = dump(db.engine, db.metadata)
    for form in _canonical_variants(export):
        again = evaldb.store_export(db.engine, form, expected_purpose=export.purpose)
        assert again.created is False
        assert dict(again.inserted) == {}
    assert dump(db.engine, db.metadata) == before


@pytest.mark.parametrize(
    "form", range(5), ids=["instance", "text", "bytes", "indented-text", "indented-bytes"]
)
def test_export_doc_is_canonical_whatever_the_input_form(
    db: Db, evaldb: ModuleType, form: int
) -> None:
    """The stored ``doc`` is the ``model_dump_json()`` of the validated export (ADR 0019 Ambiguity
    13), also when the first and only store is of text in another layout."""
    export = _load(GOLDEN / "ExportBundle" / "valid" / "maximal.json")
    _stub_dependencies(db, export)
    given = _canonical_variants(export)[form]
    evaldb.store_export(db.engine, given, expected_purpose="internal")
    row = select_all(db.engine, db.metadata, "export_bundle", id=export.id.uuid)[0]
    assert row["doc"] == export.model_dump_json()
    assert row["byte_length"] == len(row["doc"].encode("utf-8"))
    loaded = evaldb.load_export(db.engine, export.id, expected_purpose="internal")
    assert loaded.model_dump_json() == export.model_dump_json()


def test_export_round_trip_of_a_stored_run_bundle_with_a_sealed_recording(
    db: Db, evaldb: ModuleType
) -> None:
    export, bundle = _end_to_end(db, evaldb)
    assert export.sealed_payloads
    assert export.run_bundles[0].run.id == bundle.run.id
    before_internal = dump(db.engine, db.metadata)
    result = evaldb.store_export(db.engine, export, expected_purpose="internal")
    assert result.created is True
    loaded = evaldb.load_export(db.engine, export.id, expected_purpose="internal")
    assert loaded.model_dump_json() == export.model_dump_json()
    # the stored run bundle is untouched: the export is a copy, not a part of the store
    after = dump(db.engine, db.metadata)
    changed = {t for t in after if after[t] != before_internal[t]}
    assert changed == {"export_bundle", "export_bundle_run"}
    assert evaldb.load_run_bundle(db.engine, bundle.id).bundle == bundle


@pytest.mark.parametrize(
    "path", [p for p in golden_valid_files("ExportBundle") if "maximal" in p.name]
)
def test_export_sealed_payload_ciphertext_unchanged_at_rest(
    db: Db, evaldb: ModuleType, path: Path
) -> None:
    export = _load(path)
    if not export.sealed_payloads:
        export, _bundle = _end_to_end(db, evaldb)
    else:
        _stub_dependencies(db, export)
    given = json.loads(export.model_dump_json())["sealed_payloads"]
    evaldb.store_export(db.engine, export, expected_purpose=export.purpose)
    row = select_all(db.engine, db.metadata, "export_bundle", id=export.id.uuid)[0]
    stored = ev_loader.loads_strict(row["doc"])["sealed_payloads"]
    assert stored == given  # every field of every payload, ciphertext and nonce included
    for payload in given:
        assert payload["ciphertext"] in row["doc"]  # byte for byte in the text at rest
        assert payload["nonce"] in row["doc"]
    loaded = evaldb.load_export(db.engine, export.id, expected_purpose=export.purpose)
    assert [p.ciphertext for p in loaded.sealed_payloads] == [
        p.ciphertext for p in export.sealed_payloads
    ]
    assert [p.model_dump_json() for p in loaded.sealed_payloads] == [
        p.model_dump_json() for p in export.sealed_payloads
    ]


@pytest.mark.parametrize("path", _golden_export_cases())
def test_export_records_not_decomposed_into_internal_tables(
    db: Db, evaldb: ModuleType, path: Path
) -> None:
    export = _load(path)
    _stub_dependencies(db, export)
    before = dump(db.engine, db.metadata)
    evaldb.store_export(db.engine, export, expected_purpose=export.purpose)
    after = dump(db.engine, db.metadata)
    changed = {t for t in after if after[t] != before[t]}
    links = expected_side_rows("export_bundle", export)
    assert changed == {"export_bundle", *(t for t in LINK_TABLES if links[t])}
    # no record of the export (run, snapshot, turn, result, ...) was written to its own table
    exported_ids = {
        record.id.uuid
        for bundle in export.run_bundles
        for record in (
            bundle.run,
            bundle.snapshot,
            *bundle.turns,
            *bundle.results,
            *bundle.artifacts,
            *bundle.transcripts,
            *bundle.pricing_catalogs,
            *bundle.test_profiles,
        )
    }
    for name, table in db.metadata.tables.items():
        if name == "export_bundle" or "id" not in table.c:
            continue
        with db.engine.connect() as conn:
            found = set(conn.execute(sa.select(table.c.id)).scalars())
        # only the stub rows made for the links are there, and they are untouched
        assert not (found & exported_ids) or name in {"run", "test_profile"}, name
    for row in select_all(db.engine, db.metadata, "run"):
        assert row["doc"] == "{}"  # the stub, not the export's copy of the run


# ------------------------------------------------------------------------------ limits and input


def test_export_size_limit_enforced_before_parse(db: Db, evaldb: ModuleType) -> None:
    """256 MiB per document, compared before anything is parsed: a limit-exceeding input that is
    not even JSON is a ``BundleSizeError``, and so is a stored row whose ``byte_length`` exceeds
    it, whatever its document holds."""
    too_big = bytes(LIMIT + 1)  # zero-filled and never read
    with recording(db.engine) as statements, pytest.raises(BundleSizeError):
        evaldb.store_export(db.engine, too_big, expected_purpose="internal")
    assert statements.writes == []
    del too_big
    with db.engine.begin() as conn:
        over = db.rows.insert(conn, "export_bundle", doc="this is not json", byte_length=LIMIT + 1)
        at_limit = db.rows.insert(conn, "export_bundle", doc="this is not json", byte_length=LIMIT)
    with pytest.raises(BundleSizeError):
        evaldb.load_export(db.engine, over["id"], expected_purpose="internal")
    # exactly at the limit it is read, and the garbage is then an integrity error, not a size error
    with pytest.raises(evaldb.StoreIntegrityError):
        evaldb.load_export(db.engine, at_limit["id"], expected_purpose="internal")


def test_store_export_validates_its_input(db: Db, evaldb: ModuleType) -> None:
    export = _load(GOLDEN / "ExportBundle" / "valid" / "minimal.json")
    text = export.model_dump_json()
    bad_inputs = [
        "not json",
        "[]",
        text[:-1],  # truncated
        '{"id": 1, "id": 2}',  # a repeated key
        "[" * 400 + "]" * 400,  # deeper than the loader allows
        text.replace('"purpose":"internal"', '"purpose":"nonsense"'),
    ]
    with recording(db.engine) as statements:
        for bad in bad_inputs:
            with pytest.raises((ValueError, TracerSchemaError)):
                evaldb.store_export(db.engine, bad, expected_purpose="internal")
    assert statements.writes == []
    for wrong_type in (None, 5, {"id": "x"}, export.run_bundles, ["x"]):
        with pytest.raises(TypeError):
            evaldb.store_export(db.engine, wrong_type, expected_purpose="internal")
    run_bundle = load_run_bundle_doc(run_bundle_doc(tier="T0", origin="simulated"))
    with pytest.raises(TypeError):
        evaldb.store_export(db.engine, run_bundle, expected_purpose="internal")
    assert sum(counts(db.engine, db.metadata).values()) == 0


# ------------------------------------------------------------- what the export carries must exist


@pytest.mark.parametrize("missing", ["run", "dataset_item", "test_profile"])
def test_store_export_requires_stored_runs_items_and_profiles(
    db: Db, evaldb: ModuleType, missing: str
) -> None:
    export = _load(GOLDEN / "ExportBundle" / "valid" / "maximal.json")
    links = expected_side_rows("export_bundle", export)
    assert links["export_bundle_run"]
    assert links["export_bundle_item"]
    assert links["export_bundle_profile"]
    _stub_dependencies(db, export, skip=missing)
    before = dump(db.engine, db.metadata)
    with pytest.raises(evaldb.ExportReferenceNotStored) as caught:
        evaldb.store_export(db.engine, export, expected_purpose="internal")
    assert isinstance(caught.value, evaldb.StoreError)
    assert caught.value.kind == missing
    wanted = {
        "run": links["export_bundle_run"][0]["run_id"],
        "dataset_item": links["export_bundle_item"][0]["item_id"],
        "test_profile": links["export_bundle_profile"][0]["profile_id"],
    }[missing]
    assert caught.value.record_id.uuid == wanted
    assert str(caught.value.record_id) in str(caught.value)
    assert dump(db.engine, db.metadata) == before  # no export row, no link row


def test_store_export_requires_the_profile_a_sealed_payload_names(
    db: Db, evaldb: ModuleType
) -> None:
    """A test profile can be in an export only as a sealed payload: its subject must be stored."""
    b7 = support("_support_b7")
    doc = run_bundle_doc(tier="T1", origin="human", with_profile=True, profile_digest="plain")
    bundle = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, bundle)
    profile = doc["snapshot"]["evaluator"]["test_profile"]["id"]
    export = b7.load_export(
        b7.export_doc("internal", [doc], sealed_payloads=[b7.sealed_doc("test_profile", profile)])
    )
    links = expected_side_rows("export_bundle", export)
    assert [str(ev_ids.TestProfileId(r["profile_id"])) for r in links["export_bundle_profile"]] == [
        profile
    ]
    before = dump(db.engine, db.metadata)
    with pytest.raises(evaldb.ExportReferenceNotStored) as caught:
        evaldb.store_export(db.engine, export, expected_purpose="internal")
    assert caught.value.kind == "test_profile"
    assert str(caught.value.record_id) == profile
    assert dump(db.engine, db.metadata) == before
    with db.engine.begin() as conn:
        db.rows.insert(conn, "test_profile", id=ev_ids.TestProfileId.parse(profile).uuid, version=3)
    assert evaldb.store_export(db.engine, export, expected_purpose="internal").created is True


def test_store_export_is_idempotent_and_a_changed_export_is_a_conflict(
    db: Db, evaldb: ModuleType
) -> None:
    export = _load(GOLDEN / "ExportBundle" / "valid" / "maximal.json")
    _stub_dependencies(db, export)
    evaldb.store_export(db.engine, export, expected_purpose="internal")
    before = dump(db.engine, db.metadata)
    with recording(db.engine) as statements:
        again = evaldb.store_export(db.engine, export, expected_purpose="internal")
    assert again.created is False
    assert statements.writes == []
    doc = with_marker_metadata(json.loads(export.model_dump_json()), MARKER)
    other = load_entity("ExportBundle", json.dumps(doc))
    assert other.id == export.id
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_export(db.engine, other, expected_purpose="internal")
    assert MARKER not in str(caught.value)
    assert dump(db.engine, db.metadata) == before


def test_load_export_not_stored(db: Db, evaldb: ModuleType) -> None:
    unknown = ev_ids.ExportBundleId.new()
    with pytest.raises(evaldb.RecordNotStored) as caught:
        evaldb.load_export(db.engine, unknown, expected_purpose="internal")
    assert caught.value.kind == "export_bundle"
    assert str(unknown) in str(caught.value)


# --------------------------------------------------------------------------------- the locks


def test_store_export_locks_the_rows_it_links(db: Db, evaldb: ModuleType) -> None:
    """ADR 0020 Decision 5: before the link rows are inserted, ``store_export`` holds, on every run,
    dataset item and test profile it carries, a lock a deleting transaction conflicts with: on
    Postgres ``FOR KEY SHARE`` (a ``FOR UPDATE`` waits, a ``FOR NO KEY UPDATE`` does not); on
    SQLite the write transaction itself, which serializes writers."""
    export = _load(GOLDEN / "ExportBundle" / "valid" / "maximal.json")
    _stub_dependencies(db, export)
    links = expected_side_rows("export_bundle", export)
    targets = (
        [("run", r["run_id"]) for r in links["export_bundle_run"]]
        + [("dataset_item", r["item_id"]) for r in links["export_bundle_item"]]
        + [("test_profile", r["profile_id"]) for r in links["export_bundle_profile"]]
    )
    with db.engine.begin() as conn:
        bystander = db.rows.insert(conn, "run")["id"]  # a run the export does not carry
    reached, release = threading.Event(), threading.Event()
    with pause_on_insert(db.engine, "export_bundle_run", reached, release):
        thread, box = run_in_thread(
            lambda: evaldb.store_export(db.engine, export, expected_purpose="internal")
        )
        try:
            assert reached.wait(timeout=60), "store_export never reached the link rows"
            if db.backend == "postgres":
                for table, key in targets:
                    with pytest.raises(sa.exc.DBAPIError) as caught, db.engine.begin() as probe:
                        probe.exec_driver_sql(
                            f'SELECT 1 FROM "{table}" WHERE id = %s FOR UPDATE NOWAIT',  # noqa: S608
                            (key,),
                        )
                    assert _sqlstate(caught.value) == "55P03", (table, str(key))
                    with db.engine.begin() as probe:  # a non-key update does not conflict
                        probe.exec_driver_sql(
                            f'SELECT 1 FROM "{table}" WHERE id = %s FOR NO KEY UPDATE NOWAIT',  # noqa: S608
                            (key,),
                        )
                with db.engine.begin() as probe:  # and a row the export does not carry is free
                    probe.exec_driver_sql(
                        'SELECT 1 FROM "run" WHERE id = %s FOR UPDATE NOWAIT', (bystander,)
                    )
            else:
                other = _support_engine(evaldb, db.store.url)
                try:
                    with pytest.raises(sa.exc.OperationalError), other.begin() as probe:
                        probe.exec_driver_sql("INSERT INTO tracer_meta VALUES (1, '0.0.1', 'x')")
                finally:
                    other.dispose()
        finally:
            release.set()
        thread.join(timeout=60)  # inside: the listener is removed only when the writer is done
    assert not thread.is_alive()
    assert not isinstance(box[0], BaseException), box
    assert box[0].created is True


def _sqlstate(error: sa.exc.DBAPIError) -> str | None:
    args = getattr(error.orig, "args", ())
    if args and isinstance(args[0], dict):
        code = args[0].get("C")
        return str(code) if code is not None else None
    return None


def _support_engine(evaldb: ModuleType, url: str) -> sa.Engine:
    return evaldb.create_store_engine(url, busy_timeout_ms=0)  # type: ignore[no-any-return]


def test_golden_exports_are_all_covered() -> None:
    """The four golden exports (one per purpose and shape) are the inputs of this file."""
    names = sorted(p.name for p in golden_valid_files("ExportBundle"))
    assert names == [
        "maximal-benchmark.json",
        "maximal-shared-export.json",
        "maximal.json",
        "minimal.json",
    ]
    assert all(isinstance(_load(p), ExportBundle) for p in golden_valid_files("ExportBundle"))
