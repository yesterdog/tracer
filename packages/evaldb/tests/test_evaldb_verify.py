# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch E, P2-26: ``verify_database(engine)``, the admin check of a store.

Contract: docs/phase2/api-surface-batch-e.md section 6. It re-validates every ``doc`` through the
models, recomputes ``doc_sha256``, every projected column and the side-table rows, and reports the
kind and the ID of what is wrong, never a value. A difference between the stored text of a document
and the canonical ``model_dump_json()`` text that leaves the JSON value unchanged (white space,
indentation) is informational, not an error (decision log, batch B).

A store is damaged here the way a raw SQL session could: the immutability triggers of one table are
dropped for the duration of a statement and created again (the same DDL the package uses).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import decimal
import importlib
import json
import uuid
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_migration_support import MigStore, populate, raw_dump  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import new_id, recording  # type: ignore[import-not-found,unused-ignore]
from evaldb_support import Db, load_golden  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import ids as ev_ids

db = _support.db
codecs = importlib.import_module("tracer_evaldb.codecs")
mig = _mig_support.mig
migrate = _mig_support.migrate

MARKER = "VERIFY-ECHO-MARKER-7c1d"
PROBLEMS = (
    "doc_invalid",
    "digest_mismatch",
    "doc_sha256_mismatch",
    "projection_mismatch",
    "side_rows_mismatch",
)
NOTES = ("doc_text_not_canonical", "bundle_member_absent")
VERSIONED = (
    "agent_target",
    "persona",
    "test_profile",
    "evaluator",
    "metric_definition",
    "metric_binding",
    "rubric",
    "pricing_catalog",
    "dataset",
)
DIGESTED = (*VERSIONED, "config_snapshot")
ENTITY_TABLE_NAMES = (
    *VERSIONED,
    "project",
    "config_snapshot",
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
    "dataset_item",
    "run_bundle",
    "export_bundle",
)
WRITE = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE", "VACUUM")


# ----------------------------------------------------------------------------------- helpers


def _t(db: Db, table: str) -> sa.Table:
    found: sa.Table = db.metadata.tables[table]
    return found


@contextlib.contextmanager
def _triggers_off(db: Db, table: str) -> Iterator[None]:
    """The way a raw session gets round the immutability trigger: drop it, edit, create it again."""
    dialect = db.engine.dialect.name
    with db.engine.begin() as conn:
        for statement in db.evaldb.triggers.drop_statements(table, dialect):
            conn.exec_driver_sql(statement)
    try:
        yield
    finally:
        with db.engine.begin() as conn:
            for statement in db.evaldb.triggers.create_statements(table, dialect):
                conn.exec_driver_sql(statement)


def _where(table: sa.Table, row: dict[str, Any]) -> Any:
    return sa.and_(*[table.c[c.name] == row[c.name] for c in table.primary_key.columns])


def _update(db: Db, table: str, row: dict[str, Any], **values: Any) -> None:
    t = _t(db, table)
    with _triggers_off(db, table), db.engine.begin() as conn:
        conn.execute(sa.update(t).where(_where(t, row)).values(**values))


def _first(db: Db, table: str) -> dict[str, Any]:
    t = _t(db, table)
    with db.engine.connect() as conn:
        found = (
            conn.execute(sa.select(t).order_by(*t.primary_key.columns).limit(1)).mappings().one()
        )
        conn.rollback()
    return dict(found)


def _wire(db: Db, table: str, row: dict[str, Any]) -> str:
    """The wire ID (``prj_...``) of the row, taken from the model the row decodes to."""
    if table == "run_bundle":
        return str(ev_ids.RunBundleId(row["id"]))
    return str(codecs.decode(table, row).id)


def _version(row: dict[str, Any]) -> int | None:
    version = row.get("version")
    return None if version is None else int(version)


def _everything(report: Any) -> str:
    """All the text a report could leak a value through."""
    parts = [repr(report), str(report)]
    for finding in (*report.findings, *report.informational):
        parts += [repr(finding), str(finding), *map(str, dataclasses.astuple(finding))]
    return "\n".join(parts)


def _tuples(findings: Any) -> list[tuple[Any, ...]]:
    return [(f.kind, f.id, f.version, f.problem, f.location) for f in findings]


@pytest.fixture
def filled(db: Db) -> Any:
    """A store with every entity table filled (through the repository) and a dataset with items."""
    filled = populate(db.engine)
    dataset = _support.golden_doc("Dataset", "minimal")
    dataset["id"] = new_id("dts")
    dataset["item_ids"] = [str(load_golden("DatasetItem", "minimal").id)]
    with_items = _support.load_entity("Dataset", json.dumps(_support.redigest("Dataset", dataset)))
    db.evaldb.store_definition(db.engine, with_items)
    filled.ids["dataset_with_items"] = with_items
    return filled


def _firsts(db: Db) -> dict[str, dict[str, Any]]:
    return {table: _first(db, table) for table in ENTITY_TABLE_NAMES}


# ============================================================================== types and basics


def test_verify_database_api_and_types(db: Db, evaldb: ModuleType) -> None:
    assert callable(evaldb.verify_database)
    assert set(evaldb.VERIFY_PROBLEMS) == set(PROBLEMS)
    assert set(evaldb.VERIFY_NOTES) == set(NOTES)
    names = [f.name for f in dataclasses.fields(evaldb.VerifyFinding)]
    assert names == ["kind", "id", "version", "problem", "location"]
    names = [f.name for f in dataclasses.fields(evaldb.VerifyReport)]
    assert names == ["findings", "informational", "checked"]
    report = evaldb.verify_database(db.engine)
    assert isinstance(report, evaldb.VerifyReport)
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.findings = ()
    for bad in (None, "sqlite://", db.engine.url):
        with pytest.raises(TypeError):
            evaldb.verify_database(bad)
    with db.engine.connect() as connection, pytest.raises(TypeError):
        evaldb.verify_database(connection)


def test_verify_database_of_an_empty_store_reports_nothing_and_counts_zero(
    db: Db, evaldb: ModuleType
) -> None:
    report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    assert report.informational == ()
    assert report.ok is True
    assert report.checked == dict.fromkeys(ENTITY_TABLE_NAMES, 0)


# ======================================================================== the clean store


def test_verify_database_clean_store_reports_nothing(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """A store written only through the repository: no finding and no note, every one of the 26
    entity tables counted with its real row count (so no table and no row is skipped), the
    database untouched (no write statement, every row equal), the check usable on a database
    nobody migrated."""
    before = raw_dump(db.engine)
    with recording(db.engine) as statements:
        report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    assert report.informational == ()
    assert report.ok is True
    expected = {}
    with db.engine.connect() as conn:
        for name in ENTITY_TABLE_NAMES:
            expected[name] = conn.execute(
                sa.select(sa.func.count()).select_from(_t(db, name))
            ).scalar_one()
        conn.rollback()
    assert report.checked == expected
    assert all(expected[name] > 0 for name in ENTITY_TABLE_NAMES)  # the fixture fills them all
    assert not [s for s, _ in statements.items if s.lstrip().upper().startswith(WRITE)]
    assert raw_dump(db.engine) == before
    assert evaldb.verify_database(db.engine) == report  # deterministic


def test_verify_database_clean_after_the_repository_deleted_things(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """Deleting through the package leaves a store with no finding. A bundle that lost a member
    (a deleted test profile or catalog) is reported as a note, not as damage."""
    who = {"actor": "qa", "reason": "operator_request"}
    evaldb.delete_test_profile(db.engine, filled.ids["profile"].id, **who)
    report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    bundle_id = str(filled.bundle.id)
    assert _tuples(report.informational) == [
        ("run_bundle", bundle_id, None, "bundle_member_absent", "test_profile")
    ]
    evaldb.delete_run(db.engine, filled.bundle.run.id, **who)
    report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    assert report.informational == ()
    assert report.checked["run"] == 0
    assert report.checked["run_bundle"] == 0


def test_verify_database_works_on_the_schema_alembic_builds(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    migrate.upgrade(mig.engine)
    populate(mig.engine)
    report = evaldb.verify_database(mig.engine)
    assert report.findings == ()
    assert report.informational == ()
    assert sum(report.checked.values()) > 30


# ================================================================ projections, without echo


def test_verify_database_finds_projection_mismatch_without_echo(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """A changed ``created_at`` (a projection every one of the 26 tables has) is found on each
    table, as exactly one finding of that kind and ID; nothing else is reported; text projections
    changed to a marker never reach the report."""
    rows = _firsts(db)
    wire = {table: _wire(db, table, row) for table, row in rows.items()}
    for table, row in rows.items():
        _update(db, table, row, created_at=row["created_at"] + dt.timedelta(days=1))
    # a text projection holding a marker
    project = rows["project"]
    _update(db, "project", project, name=MARKER)
    key = rows["metric_definition"]
    _update(db, "metric_definition", key, key=MARKER)
    report = evaldb.verify_database(db.engine)
    expected = [
        (table, wire[table], _version(rows[table]), "projection_mismatch", "created_at")
        for table in ENTITY_TABLE_NAMES
    ]
    expected += [
        ("project", wire["project"], None, "projection_mismatch", "name"),
        ("metric_definition", wire["metric_definition"], 1, "projection_mismatch", "key"),
    ]
    got = _tuples(report.findings)
    assert sorted(got, key=repr) == sorted(expected, key=repr), set(got) ^ set(expected)
    assert report.informational == ()
    assert report.ok is False
    assert MARKER not in _everything(report)
    # sorted by kind, id, version, problem, location: a stable report
    assert got == sorted(
        got, key=lambda t: (t[0], t[1], -1 if t[2] is None else t[2], t[3], t[4] or "")
    )
    assert all(f.problem in PROBLEMS for f in report.findings)


def _other(table: str, column: str, value: Any) -> Any:
    members = _support.closed_sets().get((table, column))
    if members is not None:
        return next((m for m in members if m != value), None)
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, uuid.UUID):
        return _support.new_uuid()
    if isinstance(value, bytes):
        return value[:-1] + bytes([value[-1] ^ 1])
    if isinstance(value, dt.datetime):
        return value + dt.timedelta(days=1)
    if isinstance(value, str):
        return value + MARKER
    return decimal.Decimal(value) + 1


# Columns the database itself refuses to change on the filled store (a foreign key, the unique
# (transcript_id, seq) or a CHECK that ties the column to another one). Everything else is covered.
# Pinned: a new constraint or a new column shows here.
REFUSED_BY_THE_DATABASE = {
    ("artifact", "human_subject"),
    ("artifact", "run_id"),
    ("cost_record", "run_id"),
    ("cost_record", "status"),
    ("cost_summary", "run_id"),
    ("exchange", "run_id"),
    ("metric_result", "run_id"),
    ("metric_result", "status"),
    ("run", "config_snapshot_id"),
    ("run", "project_id"),
    ("run_bundle", "run_id"),
    ("run_verdict", "run_id"),
    ("stage_span", "run_id"),
    ("tool_call_event", "run_id"),
    ("transcript", "human_subject"),
    ("transcript", "run_id"),
    ("turn", "run_id"),
    ("turn", "seq"),
    ("usage_record", "run_id"),
}


def test_verify_database_reports_every_projected_column_by_name_only(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """Each projected column of the first row of each table, changed alone, is found as one
    ``projection_mismatch`` naming that column (and no other finding); the value never appears."""
    rows = _firsts(db)
    refused: set[tuple[str, str]] = set()
    covered: dict[str, int] = {}
    leaked: list[str] = []
    for table, row in rows.items():
        t = _t(db, table)
        key = {c.name for c in t.primary_key.columns}
        for column in sorted(
            set(row) - {"doc", "doc_sha256", "id", "schema_version", "version"} - key
        ):
            original = row[column]
            if original is None:
                continue
            new = _other(table, column, original)
            if new is None:
                continue
            try:
                _update(db, table, row, **{column: new})
            except sa.exc.DBAPIError:
                refused.add((table, column))
                continue
            try:
                report = evaldb.verify_database(db.engine)
            finally:
                _update(db, table, {**row, column: new}, **{column: original})
            found = [f for f in report.findings if f.kind == table and f.location == column]
            assert [(f.problem, f.version) for f in found] == [
                ("projection_mismatch", _version(row))
            ], (table, column, report.findings)
            assert {f.location for f in report.findings if f.kind == table} == {
                column
            } or column in (
                "content_digest_scheme",
                "content_digest_value",
            ), (table, column, _tuples(report.findings))
            if isinstance(new, str) and MARKER in new and MARKER in _everything(report):
                leaked.append(f"{table}.{column}")
            covered[table] = covered.get(table, 0) + 1
    assert leaked == []
    assert refused == REFUSED_BY_THE_DATABASE, sorted(refused ^ REFUSED_BY_THE_DATABASE)
    assert evaldb.verify_database(db.engine).findings == ()  # every change was undone
    assert set(covered) == set(ENTITY_TABLE_NAMES) - {"exchange"} or "exchange" in covered
    assert sum(covered.values()) >= 80


# ================================================================================ the document


def _edited(row: dict[str, Any]) -> str:
    doc = json.loads(row["doc"])
    doc["metadata"] = {**doc["metadata"], "tampered": MARKER}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def test_verify_database_finds_tampered_doc(db: Db, evaldb: ModuleType, filled: Any) -> None:
    """A document edited in place, ``doc_sha256`` untouched: a versioned entity or a snapshot
    fails its own content digest (``digest_mismatch``), every other record fails ``doc_sha256``
    (``doc_sha256_mismatch``). One finding per table, no value in the report."""
    rows = _firsts(db)
    wire = {table: _wire(db, table, row) for table, row in rows.items()}
    for table, row in rows.items():
        _update(db, table, row, doc=_edited(row))
    report = evaldb.verify_database(db.engine)
    expected = []
    for table in ENTITY_TABLE_NAMES:
        problem, where = (
            ("digest_mismatch", "doc")
            if table in DIGESTED
            else ("doc_sha256_mismatch", "doc_sha256")
        )
        expected.append((table, wire[table], _version(rows[table]), problem, where))
    # an export's byte_length is the size of its canonical document, so an edit shows there too
    expected.append(
        ("export_bundle", wire["export_bundle"], None, "projection_mismatch", "byte_length")
    )
    assert sorted(_tuples(report.findings), key=repr) == sorted(expected, key=repr)
    assert MARKER not in _everything(report)
    assert report.informational == ()


def test_verify_database_finds_a_changed_doc_sha256(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    rows = _firsts(db)
    wire = {table: _wire(db, table, row) for table, row in rows.items()}
    for table, row in rows.items():
        sha = bytes(row["doc_sha256"])
        _update(db, table, row, doc_sha256=sha[:-1] + bytes([sha[-1] ^ 1]))
    report = evaldb.verify_database(db.engine)
    expected = [
        (t, wire[t], _version(rows[t]), "doc_sha256_mismatch", "doc_sha256")
        for t in ENTITY_TABLE_NAMES
    ]
    assert sorted(_tuples(report.findings), key=repr) == sorted(expected, key=repr)


def test_verify_database_finds_a_versioned_doc_edited_with_a_consistent_digest(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """An editor who also recomputes the entity digest passes the model, and is caught by the
    ``doc_sha256`` taken when the row was written."""
    row = _first(db, "agent_target")
    doc = json.loads(row["doc"])
    edited = _support.redigest(
        "AgentTarget", _support.with_metadata(doc, {**doc["metadata"], "edited": "yes"})
    )
    _update(
        db,
        "agent_target",
        row,
        doc=json.dumps(edited, ensure_ascii=False, separators=(",", ":")),
        content_digest_value=bytes.fromhex(edited["content_digest"]["value"]),
    )
    report = evaldb.verify_database(db.engine)
    assert {f.problem for f in report.findings if f.kind == "agent_target"} <= {
        "doc_sha256_mismatch",
        "projection_mismatch",
    }
    assert "doc_sha256_mismatch" in {f.problem for f in report.findings if f.kind == "agent_target"}


STRICT_LOADER_CASES: dict[str, Callable[[str], str]] = {
    "duplicate-key": lambda text: text[:-1] + ',"name":"zzz"}',
    "truncated": lambda text: text[: len(text) // 2],
    "empty": lambda text: "",
    "not-an-object": lambda text: "[]",
    "nan-constant": lambda text: text.replace('"metadata":{', '"metadata":{"n":NaN,', 1),
    "trailing-garbage": lambda text: text + "x",
    "deeply-nested": lambda text: text.replace(
        '"metadata":{', '"metadata":{"deep":' + "[" * 300 + "]" * 300 + ",", 1
    ),
}


@pytest.mark.parametrize("case", sorted(STRICT_LOADER_CASES))
def test_verify_database_finds_a_doc_the_strict_loader_refuses(
    db: Db, evaldb: ModuleType, filled: Any, case: str
) -> None:
    row = _first(db, "project")
    wire = _wire(db, "project", row)
    bad = STRICT_LOADER_CASES[case](row["doc"])
    assert bad != row["doc"]
    _update(db, "project", row, doc=bad)
    report = evaldb.verify_database(db.engine)
    assert _tuples(report.findings) == [("project", wire, None, "doc_invalid", "doc")]
    assert "zzz" not in _everything(report)


def test_verify_database_finds_a_run_bundle_envelope_that_is_not_one(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    row = _first(db, "run_bundle")
    _update(db, "run_bundle", row, doc=row["doc"][: len(row["doc"]) // 2])
    report = evaldb.verify_database(db.engine)
    assert _tuples(report.findings) == [
        ("run_bundle", str(filled.bundle.id), None, "doc_invalid", "doc")
    ]


# ================================================================== formatting is informational


def test_verify_database_formatting_only_difference_is_informational(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """The same JSON value written with indentation (not the canonical ``model_dump_json()`` text)
    still verifies: ``doc_sha256`` covers the value. It is a note, never a finding, one per row,
    and the repository still loads the bundle."""
    rows = _firsts(db)
    wire = {table: _wire(db, table, row) for table, row in rows.items()}
    for table, row in rows.items():
        pretty = json.dumps(json.loads(row["doc"]), ensure_ascii=False, indent=2)
        assert pretty != row["doc"]
        _update(db, table, row, doc=pretty)
    report = evaldb.verify_database(db.engine)
    assert report.findings == ()
    assert report.ok is True
    expected = [
        (t, wire[t], _version(rows[t]), "doc_text_not_canonical", "doc") for t in ENTITY_TABLE_NAMES
    ]
    assert sorted(_tuples(report.informational), key=repr) == sorted(expected, key=repr)
    assert all(f.problem in NOTES for f in report.informational)
    assert evaldb.load_run_bundle(db.engine, filled.bundle.id).complete


# ====================================================================================== side rows


def test_verify_database_finds_side_rows_that_do_not_match_their_document(
    db: Db, evaldb: ModuleType, filled: Any
) -> None:
    """The side tables are projections of a document too: a missing or an extra row is reported
    on the entity that owns it, naming the side table."""
    t_dataset = _t(db, "dataset")
    with db.engine.connect() as conn:
        dataset = dict(
            conn.execute(
                sa.select(t_dataset).where(
                    t_dataset.c.id == filled.ids["dataset_with_items"].id.uuid
                )
            )
            .mappings()
            .one()
        )
        conn.rollback()
    snapshot = _first(db, "config_snapshot")
    run = _first(db, "run")
    export = _first(db, "export_bundle")
    side = db.metadata.tables

    def delete_one(name: str, **where: Any) -> None:
        t = side[name]
        with db.engine.begin() as conn:
            found = conn.execute(
                sa.select(t).where(*[t.c[k] == v for k, v in where.items()])
            ).first()
            assert found is not None, name
            conn.execute(
                sa.delete(t).where(
                    *[t.c[k] == found._mapping[k] for k in t.primary_key.columns.keys()]  # noqa: SIM118
                )
            )

    delete_one("snapshot_member", snapshot_id=snapshot["id"])
    delete_one("snapshot_profile_ref", snapshot_id=snapshot["id"])
    ext = side["external_ref_index"]
    with db.engine.connect() as conn:
        ref_row = dict(
            conn.execute(sa.select(ext).order_by(ext.c.entity_kind)).mappings().first() or {}
        )
        conn.rollback()
    assert ref_row, "the filled store has external references"
    owner_table = {
        "run": "run",
        "span": "stage_span",
        "transcript": "transcript",
        "usage_record": "usage_record",
    }[ref_row["entity_kind"]]
    delete_one(
        "external_ref_index",
        entity_kind=ref_row["entity_kind"],
        entity_id=ref_row["entity_id"],
        key_sha256=ref_row["key_sha256"],
    )
    delete_one("export_bundle_run", export_id=export["id"])
    delete_one("export_bundle_profile", export_id=export["id"])
    with db.engine.connect() as conn:
        members = conn.execute(sa.select(side["dataset_member"])).all()
        conn.rollback()
    assert members, "the maximal dataset lists items"
    delete_one("dataset_member", dataset_id=dataset["id"])
    report = evaldb.verify_database(db.engine)
    wire = {
        "dataset": _wire(db, "dataset", dataset),
        "config_snapshot": _wire(db, "config_snapshot", snapshot),
        "run": _wire(db, "run", run),
        "export_bundle": _wire(db, "export_bundle", export),
    }
    t_owner = _t(db, owner_table)
    with db.engine.connect() as conn:
        owner_row = dict(
            conn.execute(sa.select(t_owner).where(t_owner.c.id == ref_row["entity_id"]))
            .mappings()
            .one()
        )
        conn.rollback()
    owner_wire = _wire(db, owner_table, owner_row)
    expected = [
        ("config_snapshot", wire["config_snapshot"], None, "side_rows_mismatch", "snapshot_member"),
        (
            "config_snapshot",
            wire["config_snapshot"],
            None,
            "side_rows_mismatch",
            "snapshot_profile_ref",
        ),
        (owner_table, owner_wire, None, "side_rows_mismatch", "external_ref_index"),
        ("export_bundle", wire["export_bundle"], None, "side_rows_mismatch", "export_bundle_run"),
        (
            "export_bundle",
            wire["export_bundle"],
            None,
            "side_rows_mismatch",
            "export_bundle_profile",
        ),
        ("dataset", wire["dataset"], _version(dataset), "side_rows_mismatch", "dataset_member"),
    ]
    assert sorted(_tuples(report.findings), key=repr) == sorted(expected, key=repr)
    # an extra row is a mismatch too
    t = side["external_ref_index"]
    with db.engine.begin() as conn:
        conn.execute(
            sa.insert(t).values(
                entity_kind="run",
                entity_id=run["id"],
                key_sha256=bytes(32),
                run_id=run["id"],
            )
        )
    again = evaldb.verify_database(db.engine)
    assert ("run", wire["run"], None, "side_rows_mismatch", "external_ref_index") in _tuples(
        again.findings
    )


# ================================================================================ no content ever


def test_verify_database_never_logs_or_raises_content(
    db: Db, evaldb: ModuleType, filled: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Tampered rows give findings, never exceptions, and nothing about them reaches the logs."""
    rows = _firsts(db)
    for table in ("persona", "turn", "metric_result", "run", "export_bundle"):
        _update(db, table, rows[table], doc=_edited(rows[table]))
    _update(db, "project", rows["project"], name=MARKER)
    with caplog.at_level(0):
        report = evaldb.verify_database(db.engine)
    assert len(report.findings) == 7  # one each, the export two (its byte_length too)
    assert MARKER not in caplog.text
    assert MARKER not in _everything(report)
    for finding in report.findings:
        assert finding.kind in ENTITY_TABLE_NAMES
        assert finding.id.split("_", 1)[0].isalpha()
        assert "_" in finding.id  # a wire ID
