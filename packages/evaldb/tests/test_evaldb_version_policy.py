# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D, P2-19: the store schema version and the data-migration mechanism.

Contract: docs/phase2/api-surface-batch-d.md sections 2 and 3 (ADR 0021 rules 1, 3, 9 to 12 and 15,
ADR 0006 Decision 5). The "0.0.2" of these tests is fake: a test-only revision whose upgrade step
rewrites ``schema_version`` in every document at the text level (``fake_functions``), with the
installed 0.0.1 models standing in for "the models of the new version". Digests are unchanged
because ``schema_version`` is outside the digest domain. Nothing here is shipped or registered in
the package's chain.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_migration_support import (  # type: ignore[import-not-found,unused-ignore]
    ADVERSARIAL_METADATA,
    DIGESTED_TABLES,
    SCHEMA_V1,
    SCHEMA_V2,
    MigStore,
    ValidateBoom,
    catalog,
    create_all_database,
    expected_trigger_catalog,
    fake_data_revision,
    fault_on_statement,
    pause_on_statement,
    populate,
    raw_dump,
    revision_in_database,
    row_counts,
    table_of_entity,
    trigger_catalog,
    user_tables,
    wait_reached,
)
from evaldb_repo_support import Boom, recording  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema import digest as ev_digest
from tracer_evalschema import loader as ev_loader

mig = _mig_support.mig
postgres_mig = _mig_support.postgres_mig
migrate = _mig_support.migrate

HEAD = "0002"
WRITE_OR_DDL = re.compile(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE|VACUUM)\b", re.I)
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


def _writes(statements: Any) -> list[str]:
    return [s for s, _many in statements.items if WRITE_OR_DDL.match(s)]


def _state(engine: sa.Engine) -> tuple[Any, Any, Any]:
    return (catalog(engine), raw_dump(engine), revision_in_database(engine))


def _entity_tables(evaldb: ModuleType) -> list[str]:
    return sorted(table.name for table in evaldb.tables.ENTITY_TABLES.values())


def _force_meta(engine: sa.Engine, stored: str | None) -> None:
    """Replace ``tracer_meta`` by a copy without CHECKs holding ``stored`` (``None``: no row): what
    a store written by another evaldb release would look like to this one."""
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE tracer_meta")
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql(
                "CREATE TABLE tracer_meta (singleton INTEGER NOT NULL, schema_version TEXT NOT "
                "NULL, evaldb_version TEXT NOT NULL, PRIMARY KEY (singleton)) STRICT"
            )
        else:
            conn.exec_driver_sql(
                "CREATE TABLE tracer_meta (singleton bigint NOT NULL, schema_version text NOT "
                "NULL, evaldb_version text NOT NULL, PRIMARY KEY (singleton))"
            )
        if stored is not None:
            conn.execute(
                sa.text("INSERT INTO tracer_meta VALUES (1, :v, :e)"), {"v": stored, "e": "9.9.9"}
            )


def _rows(engine: sa.Engine, table: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        result = conn.exec_driver_sql(f'SELECT * FROM "{table}"')  # noqa: S608
        names = list(result.keys())
        rows = [dict(zip(names, (_plain(v) for v in row), strict=True)) for row in result]
        conn.rollback()
    return sorted(rows, key=lambda r: repr((r["id"], r.get("version"))) if "id" in r else repr(r))


def _plain(value: Any) -> Any:
    return bytes(value) if isinstance(value, memoryview) else value


def _retag(value: Any, old: str, new: str, *, inside_metadata: bool = False) -> Any:
    """A copy of a parsed document in which every ``schema_version`` member outside a ``metadata``
    object has ``new`` where it had ``old`` (a structural walk, independent of the text scanner)."""
    if isinstance(value, list):
        return [_retag(v, old, new, inside_metadata=inside_metadata) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key == "schema_version" and item == old and not inside_metadata:
            out[key] = new
        else:
            out[key] = _retag(item, old, new, inside_metadata=inside_metadata or key == "metadata")
    return out


def _swap_version_literals(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(f"'{SCHEMA_V2}'", f"'{SCHEMA_V1}'")
    if isinstance(value, tuple):
        return tuple(_swap_version_literals(v) for v in value)
    if isinstance(value, dict):
        return {k: _swap_version_literals(v) for k, v in value.items()}
    return value


def _fake_world(
    mig: MigStore,
    migrate: ModuleType,
    tmp_path: Path,
    mode: str = "plain",
    table: str = "persona",
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[sa.Engine, Path]:
    """A migrated, populated store and a directory with the fake revision ``t001``."""
    engine = mig.engine
    migrate.upgrade(engine)
    populate(engine, metadata=metadata)
    directory = tmp_path / "fake"
    fake_data_revision(directory, "t001", HEAD, mode, table)
    return engine, directory


# ============================================================================ tracer_meta


def test_tracer_meta_records_schema_version(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = mig.engine
    migrate.upgrade(engine)
    assert SCHEMA_VERSION == SCHEMA_V1
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT singleton, schema_version, evaldb_version FROM tracer_meta"
        ).all()
        conn.rollback()
    assert [tuple(r) for r in rows] == [
        (1, SCHEMA_VERSION, importlib.metadata.version("tracer-evaldb"))
    ]
    info = evaldb.open_store(engine)
    assert (info.schema_version, info.evaldb_version) == (SCHEMA_VERSION, rows[0][2])
    t = evaldb.metadata.tables["tracer_meta"]
    rowsf = _support.Rows(evaldb.metadata)
    # one row only: a second singleton, a singleton other than 1 and another version are refused
    for values in (
        {"singleton": 1},
        {"singleton": 2},
        {"singleton": 1, "schema_version": SCHEMA_V2},
    ):
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(sa.insert(t).values(**rowsf.values("tracer_meta", **values)))
    # and the row cannot be edited (the immutability trigger covers tracer_meta too)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.update(t).values(schema_version=SCHEMA_V1))


# ================================================================= open_store, the version


@pytest.mark.parametrize("stored", ["0.0.2", "0.0.10", "0.1.0", "0.1", "1.0.0", "10.0.0"])
def test_open_store_newer_schema_version_raises_too_new(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, stored: str
) -> None:
    engine = mig.engine
    migrate.upgrade(engine)
    _force_meta(engine, stored)
    before = _state(engine)
    errors = importlib.import_module("tracer_evaldb.errors")
    with recording(engine) as statements, pytest.raises(errors.StoreSchemaVersionTooNew) as caught:
        evaldb.open_store(engine)
    assert (caught.value.stored, caught.value.installed) == (stored, SCHEMA_VERSION)
    assert isinstance(caught.value, errors.StoreSchemaVersionError)
    assert isinstance(caught.value, errors.StoreError)
    assert not isinstance(caught.value, errors.StoreSchemaVersionMismatch)
    assert stored in str(caught.value)
    assert SCHEMA_VERSION in str(caught.value)
    assert _writes(statements) == []
    assert _state(engine) == before  # never "repaired"


@pytest.mark.parametrize(
    "stored",
    [
        "0.0.0",
        "0.0",
        "0",
        "banana",
        "0.0.1-rc1",
        "0.0.1 ",
        " 0.0.1",
        "0.0.x",
        "\u0660.\u0660.\u0661",
        "0.0.01x",
    ],
)
def test_open_store_older_schema_version_raises_mismatch(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, stored: str
) -> None:
    """Older, and also anything that is not a dotted number: it cannot be called newer."""
    engine = mig.engine
    migrate.upgrade(engine)
    _force_meta(engine, stored)
    before = _state(engine)
    errors = importlib.import_module("tracer_evaldb.errors")
    with recording(engine) as statements, pytest.raises(errors.StoreSchemaVersionMismatch) as c:
        evaldb.open_store(engine)
    assert (c.value.stored, c.value.installed) == (stored, SCHEMA_VERSION)
    assert isinstance(c.value, errors.StoreSchemaVersionError)
    assert not isinstance(c.value, errors.StoreSchemaVersionTooNew)
    assert _writes(statements) == []
    assert _state(engine) == before


def test_open_store_without_a_tracer_meta_row_is_a_mismatch_not_a_default(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """A missing record is not assumed to mean "current" (never assume)."""
    engine = mig.engine
    migrate.upgrade(engine)
    _force_meta(engine, None)
    with pytest.raises(evaldb.StoreSchemaVersionMismatch) as caught:
        evaldb.open_store(engine)
    assert caught.value.stored is None


def test_open_store_unknown_revision_raises_revision_unknown(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """A database that a newer evaldb migrated (a revision this one does not ship) is refused with
    its own error, before the schema version is read (so an operator sees which side is ahead)."""
    engine = mig.engine
    directory = tmp_path / "newer"
    _mig_support.add_column_revision(directory, "t001", HEAD, "project")
    migrate.upgrade(engine, "t001", version_locations=[directory])
    errors = importlib.import_module("tracer_evaldb.errors")
    with pytest.raises(errors.StoreRevisionUnknown) as caught:
        evaldb.open_store(engine)
    assert caught.value.revision == "t001"
    assert isinstance(caught.value, errors.StoreError)
    assert not isinstance(caught.value, errors.StoreNotMigrated)
    _force_meta(engine, "9.9.9")  # a newer schema version too: the revision is still what is said
    with pytest.raises(errors.StoreRevisionUnknown):
        evaldb.open_store(engine)
    # a revision id that is no one's
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num = 'zzzz'")
    with pytest.raises(errors.StoreRevisionUnknown) as caught:
        evaldb.open_store(engine)
    assert caught.value.revision == "zzzz"


def test_migrate_refuses_a_database_at_an_unknown_revision(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    engine = mig.engine
    directory = tmp_path / "newer"
    _mig_support.add_column_revision(directory, "t001", HEAD, "project")
    migrate.upgrade(engine, "t001", version_locations=[directory])
    before = _state(engine)
    with pytest.raises(evaldb.StoreRevisionUnknown):
        migrate.upgrade(engine)
    with pytest.raises(evaldb.StoreRevisionUnknown):
        migrate.downgrade(engine, "base", allow_data_loss=True)
    assert _state(engine) == before


def test_test_only_revisions_are_never_part_of_the_shipped_chain(migrate: ModuleType) -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert [r.revision for r in script.walk_revisions()] == [HEAD, "0001"]
    src = Path(__file__).resolve().parents[1] / "src" / "tracer_evaldb"
    offences = [
        str(p.relative_to(src))
        for p in src.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and SCHEMA_V2 in p.read_text("utf-8", "replace")
    ]
    assert offences == []  # no 0.0.2 anywhere in what ships


# ================================================================= the fake 0.0.2 migration


def test_fake_data_migration_rewrites_schema_version_and_preserves_digests(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    engine, directory = _fake_world(mig, migrate, tmp_path, metadata=dict(ADVERSARIAL_METADATA))
    names = table_of_entity()
    tables = _entity_tables(evaldb)
    assert len(tables) == 26
    before = {t: _rows(engine, t) for t in tables}
    assert all(before[t] for t in tables), [t for t in tables if not before[t]]  # every doc exists
    catalog_before = catalog(engine)
    meta_before = _rows(engine, "tracer_meta")

    migrate.upgrade(engine, "t001", version_locations=[directory])

    assert revision_in_database(engine) == "t001"
    skipped = {"doc", "doc_sha256", "schema_version"}
    for table in tables:
        after = _rows(engine, table)
        assert len(after) == len(before[table]), table
        for old_row, new_row in zip(before[table], after, strict=True):
            # nothing but the document, its hash and the version column changed: every other
            # projection, the content digest columns and created_at included, is untouched
            assert {k: v for k, v in old_row.items() if k not in skipped} == {
                k: v for k, v in new_row.items() if k not in skipped
            }, table
            assert old_row["schema_version"] == SCHEMA_V1
            assert new_row["schema_version"] == SCHEMA_V2
            parsed_old, parsed_new = json.loads(old_row["doc"]), json.loads(new_row["doc"])
            assert parsed_new == _retag(parsed_old, SCHEMA_V1, SCHEMA_V2), table
            assert parsed_new["schema_version"] == SCHEMA_V2
            assert bytes(new_row["doc_sha256"]) == bytes.fromhex(
                ev_digest.sha256_hex(ev_digest.jcs_bytes(parsed_new))
            ), table
            if table in DIGESTED_TABLES:
                # the content digest is the same value, and it still verifies: the document read
                # at the old version (what "the models of the new version" would accept)
                assert parsed_new["content_digest"] == parsed_old["content_digest"]
                text = json.dumps(_retag(parsed_new, SCHEMA_V2, SCHEMA_V1))
                model = ev_loader.load_model(_support.entity_models()[names[table]], text)
                _support.verify_own_digest(model)
    # user metadata that spells schema_version stays exactly as written
    for table in ("turn", "agent_target", "dataset"):
        for row in _rows(engine, table):
            assert json.loads(row["doc"])["metadata"] == dict(ADVERSARIAL_METADATA), table
    # the store's own record, the CHECK literals, STRICT/WITHOUT ROWID and the triggers
    meta_after = _rows(engine, "tracer_meta")
    assert [r["schema_version"] for r in meta_after] == [SCHEMA_V2]
    assert {k: v for k, v in meta_after[0].items() if k != "schema_version"} == {
        k: v for k, v in meta_before[0].items() if k != "schema_version"
    }
    assert _swap_version_literals(catalog(engine)) == catalog_before
    assert catalog(engine) != catalog_before
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    # the store now refuses the old version and accepts the new one, on every entity table
    for table in tables:
        assert _probe(engine, evaldb, table, SCHEMA_V2) == "ok", table
        assert _probe(engine, evaldb, table, SCHEMA_V1) == "check", table
    # open_store: this evaldb does not ship the revision t001
    with pytest.raises(evaldb.StoreRevisionUnknown):
        evaldb.open_store(engine)


class _VersionRows(_support.Rows):  # type: ignore[misc,unused-ignore]
    """The row factory with one schema version for every row it makes, parents included."""

    def __init__(self, metadata: sa.MetaData, version: str) -> None:
        super().__init__(metadata)
        self.version = version

    def default(self, table: sa.Table, column: sa.Column[Any]) -> Any:
        if column.name == "schema_version":
            return self.version
        return super().default(table, column)


def _probe(engine: sa.Engine, evaldb: ModuleType, table: str, version: str) -> str:
    """Insert a minimal row of this schema version (and its parents) in a transaction that is
    rolled back: "ok", or the kind of constraint that refused it."""
    rows = _VersionRows(evaldb.metadata, version)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                rows.insert(conn, table)
            finally:
                transaction.rollback()
    except sa.exc.DBAPIError as error:
        return str(_support.violation_kind(error))
    return "ok"


def test_fake_data_migration_downgrade_restores_bytes_exactly(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    engine, directory = _fake_world(mig, migrate, tmp_path, metadata=dict(ADVERSARIAL_METADATA))
    state = _state(engine)
    info = evaldb.open_store(engine)
    stored = {}
    with engine.connect() as conn:
        for table in _entity_tables(evaldb):
            stored[table] = [r[0] for r in conn.exec_driver_sql(f'SELECT doc FROM "{table}"')]  # noqa: S608
        conn.rollback()
    migrate.upgrade(engine, "t001", version_locations=[directory])
    assert _state(engine) != state
    migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert _state(engine) == state  # every table, column, byte, constraint and trigger
    assert evaldb.open_store(engine) == info
    with engine.connect() as conn:
        for table, docs in stored.items():
            now = [r[0] for r in conn.exec_driver_sql(f'SELECT doc FROM "{table}"')]  # noqa: S608
            assert sorted(now) == sorted(docs), table  # byte for byte, not merely equal JSON
        conn.rollback()
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    # the repository reads the same bundle back
    populated_bundle = _bundle_id(engine, evaldb)
    loaded = evaldb.load_run_bundle(engine, populated_bundle)
    assert loaded.complete is True
    # and going up again gives the same bytes as the first time
    migrate.upgrade(engine, "t001", version_locations=[directory])
    again = _state(engine)
    migrate.downgrade(engine, HEAD, version_locations=[directory])
    migrate.upgrade(engine, "t001", version_locations=[directory])
    assert _state(engine) == again


def _bundle_id(engine: sa.Engine, evaldb: ModuleType) -> Any:
    ids = importlib.import_module("tracer_evalschema.ids")
    t = evaldb.metadata.tables["run_bundle"]
    with engine.connect() as conn:
        (value,) = conn.execute(sa.select(t.c.id)).scalars().all()
        conn.rollback()
    return ids.RunBundleId(value)


def test_irreversible_data_migration_is_refused_before_any_change(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """A step whose downgrade cannot restore a byte raises IrreversibleMigration before the first
    write (ADR 0021 rule 12)."""
    engine, directory = _fake_world(mig, migrate, tmp_path, "lossy")
    before = _state(engine)
    with recording(engine) as statements, pytest.raises(evaldb.IrreversibleMigration) as caught:
        migrate.upgrade(engine, "t001", version_locations=[directory])
    assert caught.value.table in user_tables(engine)
    assert isinstance(caught.value, evaldb.StoreError)
    assert "schema_version" not in str(caught.value)
    assert "{" not in str(caught.value)
    assert _writes(statements) == []
    assert _state(engine) == before
    assert revision_in_database(engine) == HEAD


# ----------------------------------------------------------------- refused upgrades


def _refused(
    mig: MigStore,
    migrate: ModuleType,
    evaldb: ModuleType,
    tmp_path: Path,
    table: str,
    mode: str,
) -> None:
    engine, directory = _fake_world(mig, migrate, tmp_path, mode, table)
    before = _state(engine)
    with recording(engine) as statements, pytest.raises(evaldb.DigestChangingUpgrade) as caught:
        migrate.upgrade(engine, "t001", version_locations=[directory])
    assert caught.value.table == table
    assert isinstance(caught.value, evaldb.StoreError)
    assert "{" not in str(caught.value)  # the table, never the document
    assert _writes(statements) == []  # refused before the first write
    assert _state(engine) == before
    assert revision_in_database(engine) == HEAD
    assert evaldb.open_store(engine).revision == HEAD


@pytest.mark.parametrize("mode", ["digest_member", "digest_member_lax", "digest_content"])
@pytest.mark.parametrize("table", VERSIONED)
def test_digest_changing_upgrade_of_versioned_entity_refused(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path, table: str, mode: str
) -> None:
    """``digest_member``: the upgraded document carries another ``content_digest``;
    ``digest_content``: its digested content changed and the member did not (only the models of
    the new version can see that); ``digest_member_lax``: the member changes and ``validate``
    accepts everything, so the check of the member itself must refuse it. All would edit an
    immutable ``(id, version)``."""
    _refused(mig, migrate, evaldb, tmp_path, table, mode)


@pytest.mark.parametrize("mode", ["digest_member", "digest_member_lax", "digest_content"])
def test_digest_changing_upgrade_of_config_snapshot_refused(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path, mode: str
) -> None:
    """ADR 0021 Ambiguity 2: the one digested record of fact is covered too (runs cite it)."""
    _refused(mig, migrate, evaldb, tmp_path, "config_snapshot", mode)


def test_a_validation_failure_of_the_new_models_propagates_and_rolls_back(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    engine, directory = _fake_world(mig, migrate, tmp_path, "validate_boom", "rubric")
    before = _state(engine)
    with pytest.raises(ValidateBoom):  # not rewritten into a store error: it is not a digest case
        migrate.upgrade(engine, "t001", version_locations=[directory])
    assert _state(engine) == before


# ------------------------------------------------------------------ mixed versions


def test_mixed_schema_versions_in_one_database_rejected(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """ADR 0021 rule 1. (1) The database refuses a row of another version on every entity table;
    (2) a migration that leaves a document behind is refused, whole; (3) after a real move the
    repository cannot insert an old document."""
    engine = mig.engine
    migrate.upgrade(engine)
    for table in _entity_tables(evaldb):
        assert _probe(engine, evaldb, table, SCHEMA_V1) == "ok", table
        for version in ("0.0.2", "0.0.0", "0.0.1 ", ""):
            assert _probe(engine, evaldb, table, version) == "check", (table, version)
    # (2) the rows of one table are left at the old version: nothing is written
    engine2_dir = tmp_path / "skip"
    populate(engine)
    fake_data_revision(engine2_dir, "t001", HEAD, "skip_one", "persona")
    before = _state(engine)
    with recording(engine) as statements, pytest.raises(evaldb.MixedSchemaVersions) as caught:
        migrate.upgrade(engine, "t001", version_locations=[engine2_dir])
    assert caught.value.table == "persona"
    assert isinstance(caught.value, evaldb.StoreError)
    assert _writes(statements) == []
    assert _state(engine) == before
    # (3) a complete move: all documents at 0.0.2, and an old one cannot get in
    good = tmp_path / "good"
    fake_data_revision(good, "t001", HEAD)
    migrate.upgrade(engine, "t001", version_locations=[good])
    for table in _entity_tables(evaldb):
        with engine.connect() as conn:
            versions = {r[0] for r in conn.exec_driver_sql(f'SELECT schema_version FROM "{table}"')}  # noqa: S608
            docs = {
                json.loads(r[0])["schema_version"]
                for r in conn.exec_driver_sql(f'SELECT doc FROM "{table}"')  # noqa: S608
            }
            conn.rollback()
        assert versions == docs == {SCHEMA_V2}, table
    project = _support.load_golden("Project", "minimal")
    with pytest.raises(evaldb.StoreConstraintViolation) as refused:
        evaldb.store_project(engine, project)
    assert refused.value.kind == "check"
    assert row_counts(engine)["project"] > 0


# -------------------------------------------------------------------------- triggers


def test_migrations_drop_and_recreate_immutability_triggers_inside_the_transaction(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """The rewrite needs the triggers out of the way; a fault after they are dropped must bring them
    back (they went with the transaction), and the committed states never lack one (rule 9)."""
    engine, directory = _fake_world(mig, migrate, tmp_path)
    before = _state(engine)
    with fault_on_statement(engine, r"^\s*UPDATE\b") as seen, pytest.raises(Boom):
        migrate.upgrade(engine, "t001", version_locations=[directory])
    assert seen
    assert "alembic_version" not in seen[0]  # it was the first rewrite of a document
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    assert _state(engine) == before
    # the triggers work again, and again after a complete migration
    t = evaldb.metadata.tables["project"]
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.update(t).values(name=t.c.name))
    migrate.upgrade(engine, "t001", version_locations=[directory])
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.update(t).values(name=t.c.name))
    migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    assert _state(engine) == before


@pytest.mark.parametrize("fail", [False, True], ids=["commits", "fails"])
def test_rewriting_alone_takes_the_triggers_away_and_brings_them_back(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path, fail: bool
) -> None:
    """``helpers.rewriting`` on its own (without a batch alter, which would put triggers back by
    itself): inside the block an UPDATE of the table succeeds; when the block ends, or the revision
    fails and rolls back, all 38 triggers exist and an UPDATE is refused."""
    engine = mig.engine
    migrate.upgrade(engine)
    project = evaldb.metadata.tables["project"]
    with engine.begin() as conn:
        _support.Rows(evaldb.metadata).insert(conn, "project", name="original")
    directory = tmp_path / "rewriting"
    _mig_support.rewriting_revision(directory, "t001", HEAD, fail=fail)
    before = _state(engine)
    if fail:
        with pytest.raises(RuntimeError, match="inside the block"):
            migrate.upgrade(engine, "t001", version_locations=[directory])
        assert _state(engine) == before  # the rewrite went with the transaction
    else:
        migrate.upgrade(engine, "t001", version_locations=[directory])
        with engine.connect() as conn:
            assert conn.execute(sa.select(project.c.name)).scalar_one() == "rewritten"
            conn.rollback()
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.update(project).values(name="refused"))


@pytest.mark.postgres
def test_postgres_other_sessions_never_see_the_trigger_window(
    postgres_mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """While the migrating transaction has dropped the triggers (paused at its first document
    rewrite), another session still sees all 38; after the commit it sees 38 again."""
    import threading

    engine, directory = _fake_world(postgres_mig, migrate, tmp_path)
    monitor = postgres_mig.new_engine()
    reached, release = threading.Event(), threading.Event()
    outcome: list[BaseException] = []

    def run() -> None:
        try:
            migrate.upgrade(engine, "t001", version_locations=[directory])
        except BaseException as exc:
            outcome.append(exc)

    with pause_on_statement(
        engine,
        lambda s: re.match(r'\s*UPDATE\s+"?(?!alembic_version)', s, re.I) is not None,
        reached,
        release,
    ):
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        wait_reached(
            reached, worker, "the migration never reached a document rewrite", outcome, 120
        )
        assert trigger_catalog(monitor) == expected_trigger_catalog(monitor)
        release.set()
        worker.join(timeout=240)
    assert not worker.is_alive()
    assert outcome == []
    assert trigger_catalog(monitor) == expected_trigger_catalog(monitor)
    assert revision_in_database(monitor) == "t001"


# ------------------------------------------------------------------ the doubles themselves


def test_the_data_migration_runs_on_a_database_that_was_never_populated(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    engine = mig.engine
    migrate.upgrade(engine)
    directory = tmp_path / "fake"
    fake_data_revision(directory, "t001", HEAD)
    before = catalog(engine)
    migrate.upgrade(engine, "t001", version_locations=[directory])
    assert _swap_version_literals(catalog(engine)) == before
    with engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT schema_version FROM tracer_meta").scalar_one() == SCHEMA_V2
        )
        conn.rollback()
    migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert catalog(engine) == before


def test_create_all_database_is_not_a_migration_target(mig: MigStore, migrate: ModuleType) -> None:
    """Self-check of the fixture helper used above: a create_all database has no revision."""
    engine = create_all_database(mig)
    assert revision_in_database(engine) is None
    assert row_counts(engine)["tracer_meta"] == 0
