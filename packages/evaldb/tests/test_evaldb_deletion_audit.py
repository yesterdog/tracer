# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D, P2-17b: the deletion audit row (ADR 0020 Decision 6, Q12 and Q13).

Contract: docs/phase2/api-surface-batch-d.md section 6. One row per deletion, inserted in the
transaction of the deletion: ``audit_uuid`` (a UUIDv7 minted by the store), ``deleted_at`` (the
store's clock), ``actor`` (required, credential-checked, no default), ``reason`` (closed set),
``root_kind`` and ``counts`` (the JCS text of a fixed key set, zeros included). The row holds no
ID, no digest, no text and no metadata of what was deleted.
"""

from __future__ import annotations

import datetime as dt
import json
import unicodedata
import uuid
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_deletion_support import (  # type: ignore[import-not-found,unused-ignore]
    ACTOR,
    COUNT_KEYS,
    PROJECT_KEYS,
    REASON,
    ROOTS,
    RUN_KEYS,
    RootSpec,
    World,
    audit_rows,
    call,
    counts_of,
    full_counts,
)
from evaldb_migration_support import (  # type: ignore[import-not-found,unused-ignore]
    ADVERSARIAL_METADATA,
    fault_on_statement,
    populate,
    raw_dump,
)
from evaldb_repo_support import Boom, recording  # type: ignore[import-not-found,unused-ignore]
from evaldb_support import Db  # type: ignore[import-not-found,unused-ignore]

db = _support.db

COLUMNS = {"audit_uuid", "deleted_at", "actor", "reason", "root_kind", "counts"}
REASONS = ("operator_request", "retention_policy", "data_subject_request")
FUNCTIONS = (
    "delete_run",
    "delete_project",
    "delete_test_profile",
    "delete_definition",
    "delete_dataset_item",
    "delete_export",
    "delete_snapshot",
)


def _specs() -> list[Any]:
    return [pytest.param(spec, id=spec.id) for spec in ROOTS]


# ============================================================================ the row


@pytest.mark.parametrize("spec", _specs())
def test_deletion_audit_counts_per_kind_exact(db: Db, evaldb: ModuleType, spec: RootSpec) -> None:
    world = World(db.engine)
    built = spec.build(world)
    before_call = dt.datetime.now(dt.UTC)
    result = call(
        evaldb, db.engine, spec, built.args, actor="Priya Nair", reason="retention_policy"
    )
    after_call = dt.datetime.now(dt.UTC)
    (row,) = audit_rows(db.engine, db.metadata)
    assert set(row) == COLUMNS
    # audit_uuid: a UUIDv7 made by the store, handed back and never an entity ID
    assert isinstance(row["audit_uuid"], uuid.UUID)
    assert row["audit_uuid"].version == 7
    assert row["audit_uuid"] == result.audit_uuid
    # deleted_at: the store's clock at the commit, in UTC
    assert row["deleted_at"].tzinfo is not None
    assert before_call <= row["deleted_at"] <= after_call
    assert (row["actor"], row["reason"], row["root_kind"]) == (
        "Priya Nair",
        "retention_policy",
        spec.root_kind,
    )
    assert result.root_kind == spec.root_kind
    # counts: JCS text of one object, exactly the key set of the root kind, zeros included
    parsed = counts_of(row)
    assert list(parsed) == sorted(COUNT_KEYS[spec.root_kind])
    assert set(parsed) == set(COUNT_KEYS[spec.root_kind])
    assert row["counts"] == json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert all(type(v) is int and v >= 0 for v in parsed.values())
    assert parsed == full_counts(spec.root_kind, built.expected)
    assert dict(result.counts) == parsed


def test_the_key_sets_of_the_counts_are_those_of_the_adr() -> None:
    assert RUN_KEYS == (
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
    assert ("project", *RUN_KEYS) == PROJECT_KEYS
    assert COUNT_KEYS["test_profile"] == ("test_profile", "export_bundle")
    assert COUNT_KEYS["dataset_item"] == ("dataset_item", "export_bundle")
    assert COUNT_KEYS["export_bundle"] == ("export_bundle",)
    assert COUNT_KEYS["config_snapshot"] == ("config_snapshot",)
    for kind in (
        "agent_target",
        "persona",
        "evaluator",
        "metric_definition",
        "metric_binding",
        "rubric",
        "pricing_catalog",
        "dataset",
    ):
        assert COUNT_KEYS[kind] == (kind,)
    assert len(COUNT_KEYS) == 14  # the fourteen root kinds of the audit table's CHECK


def test_zero_counts_are_written_not_left_out(db: Db, evaldb: ModuleType) -> None:
    """An absent key never stands for zero (ADR 0020 Ambiguity 7)."""
    world = World(db.engine)
    bare = world.run(sizes={})  # a run with no records at all
    empty_project = world.project()
    lonely_profile = world.profile((1,))
    run = evaldb.delete_run(db.engine, bare.id, actor=ACTOR, reason=REASON)
    project = evaldb.delete_project(db.engine, empty_project, actor=ACTOR, reason=REASON)
    profile = evaldb.delete_test_profile(db.engine, lonely_profile, actor=ACTOR, reason=REASON)
    assert dict(run.counts) == {**dict.fromkeys(RUN_KEYS, 0), "run": 1, "config_snapshot": 1}
    assert dict(project.counts) == {**dict.fromkeys(PROJECT_KEYS, 0), "project": 1}
    assert dict(profile.counts) == {"test_profile": 1, "export_bundle": 0}
    rows = audit_rows(db.engine, db.metadata)
    assert sorted(len(counts_of(r)) for r in rows) == [2, 16, 17]
    stored = {r["root_kind"]: counts_of(r) for r in rows}
    assert stored["run"] == dict(run.counts)
    assert stored["project"] == dict(project.counts)
    assert stored["test_profile"] == dict(profile.counts)
    assert len({r["audit_uuid"] for r in rows}) == 3  # one row per deletion, each its own uuid


def test_each_deletion_adds_one_row_and_never_changes_an_earlier_one(
    db: Db, evaldb: ModuleType
) -> None:
    world = World(db.engine)
    first = world.run()
    second = world.run()
    evaldb.delete_run(db.engine, first.id, actor="First Person", reason="operator_request")
    one = audit_rows(db.engine, db.metadata)
    evaldb.delete_run(db.engine, second.id, actor="Second Person", reason="data_subject_request")
    two = audit_rows(db.engine, db.metadata)
    assert len(one) == 1
    assert len(two) == 2
    assert one[0] in two  # the earlier row is byte for byte what it was
    assert {r["actor"] for r in two} == {"First Person", "Second Person"}


def test_the_database_refuses_a_reason_or_root_kind_outside_the_closed_sets(db: Db) -> None:
    """Both closed sets are also CHECKs of the table (ADR 0020 Decision 6, batch B A10), so a row
    written around the API cannot hold a reason or a root kind the API would never write."""
    rows = _support.Rows(db.metadata)
    for column, bad in (
        ("reason", "other"),
        ("reason", "Operator_Request"),
        ("reason", ""),
        ("root_kind", "snapshot"),  # the member kind, not the root kind config_snapshot
        ("root_kind", "RUN"),
        ("root_kind", "test_profile "),
    ):
        with pytest.raises(sa.exc.DBAPIError) as caught, db.engine.begin() as conn:
            rows.insert(conn, "deletion_audit", **{column: bad})
        assert _support.violation_kind(caught.value) == "check", (column, bad)
    for kind in COUNT_KEYS:  # all fourteen root kinds and all three reasons are accepted
        for reason in REASONS:
            with db.engine.begin() as conn:
                rows.insert(conn, "deletion_audit", root_kind=kind, reason=reason)


# ===================================================================== content-free


def _values(engine: sa.Engine) -> list[Any]:
    found: list[Any] = []
    with engine.connect() as conn:
        for (table,) in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            if engine.dialect.name == "sqlite"
            else "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).all():
            if table == "deletion_audit":
                continue
            for row in conn.exec_driver_sql(f'SELECT * FROM "{table}"'):  # noqa: S608
                found.extend(row)
        conn.rollback()
    return found


def _needles(values: list[Any], allowed: set[str]) -> tuple[set[str], set[bytes]]:
    """Every string and byte string the store holds, in every form an ID or a digest can take."""
    texts: set[str] = set()
    blobs: set[bytes] = set()

    def add_bytes(raw: bytes) -> None:
        if len(raw) < 8:
            return
        blobs.add(raw)
        texts.update({raw.hex(), raw.hex().upper()})
        if len(raw) == 16:
            as_uuid = uuid.UUID(bytes=raw)
            texts.update({str(as_uuid), as_uuid.hex, as_uuid.hex.upper(), str(as_uuid).upper()})

    for value in values:
        if isinstance(value, memoryview):
            value = bytes(value)
        if isinstance(value, bytes):
            add_bytes(value)
        elif isinstance(value, uuid.UUID):
            add_bytes(value.bytes)
        elif isinstance(value, str) and len(value) >= 6:
            texts.add(value)
    texts = {t for t in texts if not any(t in token for token in allowed)}
    return texts, blobs


def _haystack(row: dict[str, Any]) -> tuple[str, bytes]:
    text = "\n".join(str(row[c]) for c in ("actor", "reason", "root_kind", "counts"))
    return text, text.encode("utf-8") + row["audit_uuid"].bytes


@pytest.mark.parametrize("spec", _specs())
def test_deletion_audit_is_content_free(db: Db, evaldb: ModuleType, spec: RootSpec) -> None:
    """No ID (wire form, bare UUID, hex, raw 16 bytes), digest, text or metadata value that the
    store held before the deletion appears in the audit row (Q12): every string and byte string
    in every table is a needle."""
    world = World(db.engine)
    for other in ROOTS:
        other.build(world)
    built = spec.build(world)
    allowed = {ACTOR, REASON, spec.root_kind, *REASONS, *COUNT_KEYS[spec.root_kind]}
    texts, blobs = _needles(_values(db.engine), allowed)
    assert len(texts) > 50  # the scan is not vacuous
    assert len(blobs) > 50
    call(evaldb, db.engine, spec, built.args)
    (row,) = audit_rows(db.engine, db.metadata)
    text, raw = _haystack(row)
    assert [n for n in texts if n in text] == []
    assert [n.hex() for n in blobs if n in raw] == []


def test_deletion_audit_holds_no_text_digest_or_metadata_of_real_records(
    db: Db, evaldb: ModuleType
) -> None:
    """The same scan on documents the repository wrote: turn text, the profile's identity fields,
    metadata that spells schema_version, content digests, external references."""
    populated = populate(db.engine, metadata=dict(ADVERSARIAL_METADATA))
    bundle = populated.bundle
    needles: list[str] = [
        str(bundle.run.id),
        str(bundle.run.project_id),
        str(bundle.snapshot.id),
        str(bundle.snapshot.content_digest.value),
        str(populated.ids["profile"].name),
        *(t.text for t in bundle.turns if getattr(t, "text", None)),
        *(ref.value for ref in bundle.run.external_refs),
        *(ref.system for ref in bundle.run.external_refs),
        *(str(v) for v in ADVERSARIAL_METADATA.values() if isinstance(v, str)),
    ]
    needles = [n for n in needles if len(n) >= 4]
    texts, blobs = _needles(_values(db.engine), {ACTOR, REASON, *REASONS, *PROJECT_KEYS, "project"})
    result = evaldb.delete_project(db.engine, bundle.run.project_id, actor=ACTOR, reason=REASON)
    assert result.counts["run"] == 1
    (row,) = [r for r in audit_rows(db.engine, db.metadata) if r["actor"] == ACTOR]
    text, raw = _haystack(row)
    assert [n for n in needles if n in text] == []
    assert [n for n in texts if n in text] == []
    assert [n.hex() for n in blobs if n in raw] == []
    assert set(row) == COLUMNS


# ================================================================== atomic with the deletion


@pytest.mark.parametrize("spec", _specs())
def test_delete_is_atomic_with_audit(db: Db, evaldb: ModuleType, spec: RootSpec) -> None:
    """The audit row and the deletion commit or roll back together: a failing audit INSERT leaves
    every row where it was (nothing deleted), and a deletion that succeeds leaves exactly one."""
    world = World(db.engine)
    for other in ROOTS:
        other.build(world)
    built = spec.build(world)
    state = raw_dump(db.engine)
    with (
        fault_on_statement(db.engine, r"INSERT\s+INTO\s+\"?deletion_audit") as seen,
        pytest.raises(Boom),
    ):
        call(evaldb, db.engine, spec, built.args)
    assert len(seen) == 1
    assert raw_dump(db.engine) == state  # not one row deleted, not one audit row
    assert audit_rows(db.engine, db.metadata) == []
    # the failure left nothing behind that blocks the deletion: it works, with exactly one row
    result = call(evaldb, db.engine, spec, built.args)
    rows = audit_rows(db.engine, db.metadata)
    assert len(rows) == 1
    assert rows[0]["audit_uuid"] == result.audit_uuid
    assert raw_dump(db.engine) != state


def test_the_audit_row_is_written_in_the_deletions_own_transaction(
    db: Db, evaldb: ModuleType
) -> None:
    """One connection, one transaction: nothing of the call is visible to another connection
    before its end, and the audit INSERT is inside it (the engine sees one BEGIN)."""
    world = World(db.engine)
    built = ROOTS[0].build(world)
    with recording(db.engine) as statements:
        call(evaldb, db.engine, ROOTS[0], built.args)
    kinds = [s.lstrip().split(None, 1)[0].upper() for s, _many in statements.items]
    assert kinds.count("BEGIN") <= 1  # SQLite sends one; Postgres none (the driver begins)
    assert "COMMIT" not in kinds  # committed by the connection, not by a statement of ours
    assert "deletion_audit" in statements.inserts
    assert len([s for s in statements.inserts if s == "deletion_audit"]) == 1


# ======================================================== actor and reason: required, checked


@pytest.mark.parametrize("function", FUNCTIONS)
def test_deletion_requires_actor_and_reason(db: Db, evaldb: ModuleType, function: str) -> None:
    """No defaults (Q13): leaving either out is a TypeError before any statement."""
    world = World(db.engine)
    spec = next(s for s in ROOTS if s.function == function)
    built = spec.build(world)
    target = getattr(evaldb, function)
    before = raw_dump(db.engine)
    lead = built.args
    with recording(db.engine) as statements:
        with pytest.raises(TypeError):
            target(db.engine, *lead)
        with pytest.raises(TypeError):
            target(db.engine, *lead, actor=ACTOR)
        with pytest.raises(TypeError):
            target(db.engine, *lead, reason=REASON)
        with pytest.raises(TypeError):
            target(db.engine, *lead, ACTOR, REASON)  # keyword-only
        for bad in (None, 1, b"ops", ["ops"], object()):
            with pytest.raises(TypeError):
                target(db.engine, *lead, actor=bad, reason=REASON)
            with pytest.raises(TypeError):
                target(db.engine, *lead, actor=ACTOR, reason=bad)
        for bad_reason in (
            "",
            "Operator_Request",
            " operator_request",
            "operator_request ",
            "operator request",
            "other",
            "system",
            "retention",
        ):
            with pytest.raises(ValueError, match="reason") as caught:
                target(db.engine, *lead, actor=ACTOR, reason=bad_reason)
            if bad_reason.strip():
                assert repr(bad_reason) not in str(caught.value)
        for bad_actor in (
            "",
            " leading",
            "trailing ",
            "line\nbreak",
            "tab\there",
            "nul\x00byte",
            "bidi\u202eoverride",
            "zero\u200bwidth",
            "para" + chr(0x2029) + "sep",
            "del\x7fchar",
            "c1\x85control",
        ):
            with pytest.raises(ValueError, match="actor") as caught:
                target(db.engine, *lead, actor=bad_actor, reason=REASON)
            assert bad_actor not in str(caught.value) or not bad_actor.strip()
    assert statements.count == 0  # all of that before the first statement
    assert raw_dump(db.engine) == before
    assert audit_rows(db.engine, db.metadata) == []


def test_deletion_accepts_each_reason_and_stores_labels_exactly(db: Db, evaldb: ModuleType) -> None:
    """The actor follows the HumanLabel rules: Indic text, ZWJ and ZWNJ are allowed, and the
    label is stored as given (no trimming, no normalization): NFC and NFD stay different."""
    zwnj, zwj = "\u200c", "\u200d"
    actors = [
        "Priya",
        "\u0911\u092a\u0930\u0947\u091f\u0930"
        + zwnj
        + "\u091f\u0940\u092e",  # Devanagari with a joiner
        "\u0d15\u0d4d" + zwj + "\u0d37",  # Malayalam with ZWJ
        "caf\u00e9 night",  # NFC
        "cafe\u0301 night",  # the same text decomposed
        "x" * 500,
        "user@example.org",
        "Ops (night) #2 - tier-1/3",
    ]
    assert unicodedata.normalize("NFC", actors[3]) != actors[4]
    world = World(db.engine)
    expected: list[tuple[str, str]] = []
    for index, actor in enumerate(actors):
        reason = REASONS[index % 3]
        evaldb.delete_run(db.engine, world.run(sizes={}).id, actor=actor, reason=reason)
        expected.append((actor, reason))
    stored = [(r["actor"], r["reason"]) for r in audit_rows(db.engine, db.metadata)]
    assert sorted(stored) == sorted(expected)  # byte-exact, in both backends' collations


def _credentials() -> list[str]:
    """Shapes of live credentials, assembled from parts (no literal secret in the source)."""
    return [
        "sk" + "-" + "Ab3dE6gH9jK2mN5pQ8sT1vW4",
        "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
        "xox" + "b-" + "123456789012-abcdefghij",
        "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".sig",
        "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6kM9n",  # a bare high-entropy token
        "night shift " + "sk" + "-" + "Zy9xW8vU7tS6rQ5pO4nM3lK2",  # inside a longer label
    ]


@pytest.mark.parametrize("secret", _credentials(), ids=lambda s: f"len{len(s)}")
def test_deletion_actor_rejects_credential_shaped_value(
    db: Db, evaldb: ModuleType, secret: str
) -> None:
    world = World(db.engine)
    spec = ROOTS[0]
    built = spec.build(world)
    before = raw_dump(db.engine)
    with recording(db.engine) as statements, pytest.raises(ValueError, match="actor") as caught:
        call(evaldb, db.engine, spec, built.args, actor=secret)
    message = str(caught.value) + repr(caught.value) + repr(caught.value.args)
    assert secret not in message
    assert all(secret[i : i + 8] not in message for i in range(0, len(secret) - 7, 4))
    assert statements.count == 0
    assert raw_dump(db.engine) == before
    assert audit_rows(db.engine, db.metadata) == []


def test_ordinary_labels_that_resemble_nothing_secret_are_accepted(
    db: Db, evaldb: ModuleType
) -> None:
    world = World(db.engine)
    for index, actor in enumerate(
        (
            "ops-night-shift",
            "Priya R. (QA)",
            "support-team-2026-10-24",
            "a" * 31,
            "0123456789abcdef",
        )
    ):
        result = evaldb.delete_run(
            db.engine, world.run(sizes={}).id, actor=actor, reason=REASONS[index % 3]
        )
        assert result.root_kind == "run"
