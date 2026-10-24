# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch C, P2-13: idempotent re-import (P-02) through ``external_ref_index``.

Contract: docs/phase2/api-surface-batch-c.md and ADR 0022 Decisions 10 and 11. The key is the
SHA-256 of the JCS of ``[system, kind, value]``; it is unique for the kind ``run`` only (revision
35, batch B A1); span, transcript and usage-record keys repeat and are looked up within one run.
"""

from __future__ import annotations

import copy
import unicodedata
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    RECORD_LISTS,
    dump,
    load_run_bundle_doc,
    new_id,
    recording,
    renew_ids,
    run_bundle_doc,
    select_all,
    store_bundle,
    store_project_of,
    with_marker_metadata,
)
from evaldb_support import Db, external_ref_key  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import digest as ev_digest
from tracer_evalschema import ids as ev_ids
from tracer_evalschema.errors import ConflictingRecordError

db = _support.db

MARKER = "CONFLICT-MARKER-2d8e"
SYSTEM, KIND, VALUE = "pipecat", "call_sid", "CA-0123456789"


def cp(*points: int) -> str:
    """The string of these code points (so the source stays ASCII)."""
    return "".join(map(chr, points))


def _ref(value: str = VALUE, system: str = SYSTEM, kind: str = KIND) -> dict[str, str]:
    return {"system": system, "kind": kind, "value": value}


def _external(ref: dict[str, str]) -> Any:
    return ev_ids.ExternalRef.model_validate(ref)


def _bundle(refs: list[dict[str, str]], **kwargs: Any) -> Any:
    kwargs.setdefault("tier", "T0")
    kwargs.setdefault("origin", "simulated")
    doc = run_bundle_doc(**kwargs)
    doc["run"]["external_refs"] = copy.deepcopy(refs)
    return load_run_bundle_doc(doc)


def _doc_with_refs(refs: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("tier", "T1")
    kwargs.setdefault("origin", "simulated")
    doc: dict[str, Any] = run_bundle_doc(**kwargs)
    doc["run"]["external_refs"] = copy.deepcopy(refs)
    return doc


def _independent_key(ref: dict[str, str]) -> bytes:
    return bytes.fromhex(
        ev_digest.sha256_hex(ev_digest.jcs_bytes([ref["system"], ref["kind"], ref["value"]]))
    )


# ------------------------------------------------------------------------------ finding a run


def test_reimport_finds_existing_run_by_external_ref(db: Db, evaldb: ModuleType) -> None:
    refs = [_ref(), _ref("ZZ-77", system="telephony", kind="leg_id")]
    bundle = _bundle(refs)
    assert evaldb.find_by_external_ref(db.engine, "run", _external(refs[0])) == ()  # nothing yet
    store_bundle(db.engine, evaldb, bundle)
    for ref in refs:
        found = evaldb.find_by_external_ref(db.engine, "run", _external(ref))
        assert len(found) == 1
        match = found[0]
        assert match.entity_kind == "run"
        assert match.entity_id == bundle.run.id
        assert match.run_id == bundle.run.id
        assert isinstance(match, evaldb.ExternalRefMatch)
    assert evaldb.find_by_external_ref(db.engine, "run", _external(_ref("CA-0123456780"))) == ()
    assert evaldb.find_by_external_ref(db.engine, "run", _external(_ref(system="other"))) == ()
    # the importer's loop: look up before minting, and the same import is then a no-op
    again = evaldb.store_run_bundle(db.engine, bundle)
    assert again.created is False


def test_find_by_external_ref_arguments(db: Db, evaldb: ModuleType) -> None:
    ref = _external(_ref())
    run_id = ev_ids.RunId.new()
    for kind in ("run",):
        with pytest.raises(ValueError, match="run_id"):  # a run is found by its reference alone
            evaldb.find_by_external_ref(db.engine, kind, ref, run_id=run_id)
    for kind in ("span", "transcript", "usage_record"):
        with pytest.raises(ValueError, match="run_id"):  # the others are looked up within a run
            evaldb.find_by_external_ref(db.engine, kind, ref)
        assert evaldb.find_by_external_ref(db.engine, kind, ref, run_id=run_id) == ()
        assert evaldb.find_by_external_ref(db.engine, kind, ref, run_id=run_id.uuid) == ()
        with pytest.raises(TypeError):
            evaldb.find_by_external_ref(db.engine, kind, ref, run_id=str(run_id))
    for bad_kind in ("artifact", "Run", "", None, "metric_result"):
        with pytest.raises(ValueError, match="entity_kind"):
            evaldb.find_by_external_ref(db.engine, bad_kind, ref)
    for bad_ref in (_ref(), "pipecat", None, ("a", "b", "c")):
        with pytest.raises(TypeError):
            evaldb.find_by_external_ref(db.engine, "run", bad_ref)


# ------------------------------------------------------------------------------- uniqueness


def test_external_ref_unique_per_entity_kind(db: Db, evaldb: ModuleType) -> None:
    """Unique for the kind ``run``: a second run with the same reference is refused. The other
    kinds are lookups: spans, transcripts and usage records with one reference are all stored."""
    first = _bundle([_ref()])
    store_bundle(db.engine, evaldb, first)
    second = _bundle([_ref()])
    store_project_of(db.engine, evaldb, second)
    before = dump(db.engine, db.metadata)
    with pytest.raises(evaldb.ReimportConflict):
        evaldb.store_run_bundle(db.engine, second)
    assert dump(db.engine, db.metadata) == before
    # the golden maximal bundle holds two spans of one run with one span_ref (finding P2-F3): stored
    t1 = load_run_bundle_doc(run_bundle_doc(tier="T1", origin="simulated"))
    assert len(t1.spans) == 2
    assert t1.spans[0].span_ref == t1.spans[1].span_ref
    store_bundle(db.engine, evaldb, t1)
    rows = select_all(db.engine, db.metadata, "external_ref_index", entity_kind="span")
    assert {r["entity_id"] for r in rows} == {s.id.uuid for s in t1.spans}
    assert len({r["key_sha256"] for r in rows}) == 1
    # the same span reference in another run is stored too
    other_doc = renew_ids(
        run_bundle_doc(tier="T1", origin="simulated"), lists=["spans"], run=True, bundle=True
    )
    other_doc["spans"][0]["span_ref"] = copy.deepcopy(t1.spans[0].span_ref.model_dump(mode="json"))
    other = load_run_bundle_doc(other_doc)
    store_project_of(db.engine, evaldb, other)
    assert evaldb.store_run_bundle(db.engine, other).created is True
    runs = {
        r["run_id"]
        for r in select_all(db.engine, db.metadata, "external_ref_index", entity_kind="span")
    }
    assert runs == {t1.run.id.uuid, other.run.id.uuid}


def test_external_ref_key_is_hash_of_jcs_tuple(db: Db, evaldb: ModuleType) -> None:
    long_value = "v" * 1_000_000
    refs = [
        _ref(),
        _ref(long_value, system="s", kind="k"),
        _ref("a\u0000b", system="nul", kind="nul"),
        _ref(cp(0x915, 0x93C), system="indic", kind="nukta"),
    ]
    bundle = _bundle([*refs, _ref()])  # the first reference listed twice
    store_bundle(db.engine, evaldb, bundle)
    rows = select_all(db.engine, db.metadata, "external_ref_index", entity_kind="run")
    assert len(rows) == len(refs)  # one row per distinct reference
    keys = {r["key_sha256"] for r in rows}
    assert keys == {_independent_key(r) for r in refs}
    assert keys == {external_ref_key(r["system"], r["kind"], r["value"]) for r in refs}
    for row in rows:
        assert type(row["key_sha256"]) is bytes
        assert len(row["key_sha256"]) == 32
        assert row["entity_id"] == bundle.run.id.uuid
        assert row["run_id"] == bundle.run.id.uuid
    # the key is the tuple [system, kind, value] under JCS: not a join of the three, and the
    # position of each part matters
    plain = _independent_key(_ref())
    assert plain not in {
        bytes.fromhex(ev_digest.sha256_hex(f"{SYSTEM}:{KIND}:{VALUE}".encode())),
        bytes.fromhex(ev_digest.sha256_hex(f"{SYSTEM}{KIND}{VALUE}".encode())),
        bytes.fromhex(ev_digest.sha256_hex((VALUE).encode())),
        _independent_key(_ref(system=KIND, kind=SYSTEM)),
    }
    assert (
        evaldb.find_by_external_ref(db.engine, "run", _external(refs[1]))[0].entity_id
        == bundle.run.id
    )
    assert (
        evaldb.find_by_external_ref(db.engine, "run", _external(refs[2]))[0].entity_id
        == bundle.run.id
    )


def _variants() -> list[tuple[str, dict[str, str]]]:
    nfc = cp(0x63, 0x61, 0x66, 0xE9)
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    return [
        ("base", _ref()),
        ("lower-case", _ref(VALUE.lower())),
        ("leading-space", _ref(" " + VALUE)),
        ("trailing-space", _ref(VALUE + " ")),
        ("trailing-newline", _ref(VALUE + "\n")),
        ("no-break-space", _ref(VALUE + chr(0xA0))),
        ("nul-inside", _ref("CA-0123\u00006789")),
        ("nul-absent", _ref("CA-01236789")),
        ("nfc", _ref(nfc)),
        ("nfd", _ref(nfd)),
        ("nukta-precomposed", _ref(cp(0x958))),
        ("nukta-decomposed", _ref(cp(0x915, 0x93C))),
        ("zwj", _ref(cp(0x915, 0x94D, 0x200D, 0x937))),
        ("zwnj", _ref(cp(0x915, 0x94D, 0x200C, 0x937))),
        ("no-joiner", _ref(cp(0x915, 0x94D, 0x937))),
        ("system-case", _ref(system=SYSTEM.upper())),
        ("kind-case", _ref(kind=KIND.upper())),
        ("system-and-kind-swapped", _ref(system=KIND, kind=SYSTEM)),
        ("empty-value", _ref("")),
        ("empty-system", _ref(system="")),
    ]


def test_external_ref_lookup_is_exact(db: Db, evaldb: ModuleType) -> None:
    """Case, white space, NFC and NFD, joiners, U+0000 and the position of each part are all
    distinct: every variant is its own run, and a lookup finds that run and no other."""
    stored: dict[str, Any] = {}
    for name, ref in _variants():
        bundle = _bundle([ref])
        store_bundle(db.engine, evaldb, bundle)
        stored[name] = bundle
    assert len(stored) == len(_variants())
    for name, ref in _variants():
        found = evaldb.find_by_external_ref(db.engine, "run", _external(ref))
        assert [m.entity_id for m in found] == [stored[name].run.id], name
    # a reference that equals none of them after any normalization finds nothing
    for ref in (_ref("CA-0123456789" + cp(0x200B)), _ref("ca-0123456789 "), _ref(VALUE[:-1])):
        assert evaldb.find_by_external_ref(db.engine, "run", _external(ref)) == ()
    # a value of 1 MB costs one 32-byte key and is found like any other
    huge = _ref("x" * 1_000_000, system="bulk", kind="blob")
    bundle = _bundle([huge])
    store_bundle(db.engine, evaldb, bundle)
    found = evaldb.find_by_external_ref(db.engine, "run", _external(huge))
    assert [m.entity_id for m in found] == [bundle.run.id]
    near = _ref("x" * 999_999 + "y", system="bulk", kind="blob")
    assert evaldb.find_by_external_ref(db.engine, "run", _external(near)) == ()
    index = db.metadata.tables["external_ref_index"]
    with db.engine.connect() as conn:
        longest = conn.execute(
            sa.select(sa.func.max(sa.func.length(index.c.key_sha256)))
        ).scalar_one()
    assert longest == 32


# --------------------------------------------------------------------------------- conflicts


def test_reimport_conflicting_content_raises_reimport_conflict(db: Db, evaldb: ModuleType) -> None:
    doc = _doc_with_refs([_ref(), _ref("ZZ-77", system="telephony", kind="leg_id")])
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    before = dump(db.engine, db.metadata)

    def refused(candidate: Any) -> Any:
        store_project_of(db.engine, evaldb, candidate)
        snapshot = dump(db.engine, db.metadata)
        with recording(db.engine) as statements, pytest.raises(evaldb.ReimportConflict) as caught:
            evaldb.store_run_bundle(db.engine, candidate)
        assert statements.writes == []  # refused before any write
        assert dump(db.engine, db.metadata) == snapshot
        return caught.value

    # a. the importer minted a new ID for the same external call
    renewed = load_run_bundle_doc(renew_ids(doc, lists=["results"], run=True, verdict=True))
    error = refused(renewed)
    assert error.entity_kind == "run"
    assert error.stored_id == first.run.id
    assert str(first.run.id) in str(error)
    assert "run" in str(error)
    assert VALUE not in str(error)
    assert "ZZ-77" not in str(error)  # the reference is never echoed
    assert isinstance(error, ConflictingRecordError)  # a caller that catches the general class
    # b. the same ID and the same reference, with other content: the specific diagnosis comes first
    changed = copy.deepcopy(doc)
    changed["run"] = with_marker_metadata(changed["run"], MARKER)
    error = refused(load_run_bundle_doc(renew_ids(changed)))
    assert error.stored_id == first.run.id
    assert MARKER not in str(error)
    # c. only the second of two references matches a stored run
    partial = renew_ids(doc, lists=["results"], run=True, verdict=True)
    partial["run"]["external_refs"] = [_ref("never-seen"), _ref("ZZ-77", "telephony", "leg_id")]
    error = refused(load_run_bundle_doc(partial))
    assert error.stored_id == first.run.id
    # d. the same ID with other content and no matching reference is the plain record conflict
    plain = copy.deepcopy(doc)
    plain["run"]["external_refs"] = [_ref("a-different-call")]
    plain["run"] = with_marker_metadata(plain["run"], MARKER)
    candidate = load_run_bundle_doc(renew_ids(plain))
    store_project_of(db.engine, evaldb, candidate)
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_run_bundle(db.engine, candidate)
    assert not isinstance(caught.value, evaldb.ReimportConflict)
    assert MARKER not in str(caught.value)
    # e. the identical import is not a conflict (the same ID, the same content)
    assert evaldb.store_run_bundle(db.engine, first).created is False
    assert dump(db.engine, db.metadata) == before


def test_reimport_conflict_is_a_conflicting_record_error_checked_first(
    db: Db, evaldb: ModuleType
) -> None:
    assert issubclass(evaldb.ReimportConflict, ConflictingRecordError)
    doc = _doc_with_refs([_ref()])
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    # the same run ID and reference with a changed record AND a changed run: the run is reported
    changed = renew_ids(doc)
    changed["run"] = with_marker_metadata(changed["run"], MARKER)
    changed["turns"][0] = with_marker_metadata(changed["turns"][0], MARKER)
    store_project_of(db.engine, evaldb, load_run_bundle_doc(changed))
    with pytest.raises(ConflictingRecordError) as caught:
        evaldb.store_run_bundle(db.engine, load_run_bundle_doc(changed))
    assert isinstance(caught.value, evaldb.ReimportConflict)


# ------------------------------------------------------------------- child references repeat


def test_child_provider_call_refs_not_unique(db: Db, evaldb: ModuleType) -> None:
    """One provider call may yield several transcripts or usage records, and a span reference
    repeats within a run: none of these keys is unique, and each is looked up within one run."""
    shared = _ref("call-77", system="provider", kind="call_id")
    doc = _doc_with_refs([])
    doc["transcripts"][0]["provider_call_ref"] = copy.deepcopy(shared)
    usage_template = next(u for u in doc["usage_records"] if u["domain"] == "evaluation")
    extra = [{**copy.deepcopy(usage_template), "id": new_id("use")} for _ in range(2)]
    for record in [usage_template, *extra]:
        record["provider_call_ref"] = copy.deepcopy(shared)
    doc["usage_records"] = [*doc["usage_records"], *extra]
    # cost records and the summary stay valid: the extra usage is simply not costed
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    usage_ids = sorted(u.id.uuid for u in first.usage_records if u.provider_call_ref is not None)
    assert len(usage_ids) == 3
    found = evaldb.find_by_external_ref(
        db.engine, "usage_record", _external(shared), run_id=first.run.id
    )
    assert [m.entity_id.uuid for m in found] == usage_ids  # all three, ordered by ID
    assert all(m.run_id == first.run.id and m.entity_kind == "usage_record" for m in found)
    found = evaldb.find_by_external_ref(
        db.engine, "transcript", _external(shared), run_id=first.run.id
    )
    assert [m.entity_id for m in found] == [first.transcripts[0].id]
    span_ref = first.spans[0].span_ref.model_dump(mode="json")
    found = evaldb.find_by_external_ref(db.engine, "span", _external(span_ref), run_id=first.run.id)
    assert [m.entity_id.uuid for m in found] == sorted(s.id.uuid for s in first.spans)
    assert evaldb.find_by_external_ref(db.engine, "run", _external(shared)) == ()  # kinds apart
    # a second run with the same provider call reference: stored, and the lookups stay apart
    second_doc = renew_ids(doc, lists=RECORD_LISTS, run=True, verdict=True)
    second = load_run_bundle_doc(second_doc)
    store_project_of(db.engine, evaldb, second)
    assert evaldb.store_run_bundle(db.engine, second).created is True
    for bundle in (first, second):
        in_run = evaldb.find_by_external_ref(
            db.engine, "usage_record", _external(shared), run_id=bundle.run.id
        )
        assert sorted(m.entity_id.uuid for m in in_run) == sorted(
            u.id.uuid for u in bundle.usage_records if u.provider_call_ref is not None
        )
    assert {u.id for u in first.usage_records}.isdisjoint({u.id for u in second.usage_records})
    # the database allows the repeats: no unique violation on the shared key
    index = db.metadata.tables["external_ref_index"]
    with db.engine.connect() as conn:
        repeated = conn.execute(
            sa.select(index.c.key_sha256, sa.func.count())
            .where(index.c.entity_kind == "usage_record")
            .group_by(index.c.key_sha256)
        ).all()
    assert repeated
    assert repeated[0][1] == 6
