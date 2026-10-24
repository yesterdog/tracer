# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch E: Alembic revision 0002 (the access-pattern indexes of P2-20) and the
``open_store`` tests of an older revision (batch D ambiguity A10).

Contract: docs/phase2/api-surface-batch-e.md section 4. Revision 0001 is frozen DDL text and is
never regenerated: new indexes are a new revision. Revision 0002 is the first real revision above
the first, so it is also the first time the stairway and ``open_store`` have an older shipped
revision to stand on.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
from evaldb_index_support import NEW_INDEXES, db_indexes  # type: ignore[import-not-found,unused-ignore]
from evaldb_migration_support import (  # type: ignore[import-not-found,unused-ignore]
    MigStore,
    catalog,
    catalog_diff,
    fault_on_statement,
    populate,
    raw_dump,
    revision_in_database,
    trigger_catalog,
)
from evaldb_repo_support import Boom, recording  # type: ignore[import-not-found,unused-ignore]

mig = _mig_support.mig
migrate = _mig_support.migrate

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = ROOT / "packages" / "evaldb" / "src" / "tracer_evaldb" / "migrations" / "versions"
FILE = VERSIONS / "0002_access_pattern_indexes.py"
WRITE_OR_DDL = re.compile(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE|VACUUM)\b", re.I)


def _at_first(mig: MigStore, migrate: ModuleType) -> sa.Engine:
    engine: sa.Engine = mig.engine
    migrate.upgrade(engine, "0001")
    return engine


# =================================================================================== the chain


def test_revision_0002_follows_0001_and_is_the_head(migrate: ModuleType) -> None:
    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_heads() == ["0002"]
    assert migrate.head_revision() == "0002"
    revision = script.get_revision("0002")
    assert revision.down_revision == "0001"
    assert revision.branch_labels == set()
    assert FILE.is_file()
    assert revision.module.DOWNGRADE_DROPS == ()  # an index holds no row and no value
    assert script.get_revision("0001").down_revision is None


def test_revision_0001_is_unchanged_by_the_new_indexes() -> None:
    """Frozen text: revision 0001 names none of the 25 new indexes (a regenerated 0001 would)."""
    text = (VERSIONS / "0001_initial_schema.py").read_text("utf-8")
    assert [name for name in NEW_INDEXES if name in text] == []


def test_revision_0002_is_frozen_text_and_never_reads_the_live_metadata() -> None:
    """The index DDL is written out in the revision: the module imports nothing of the live table
    definitions (tables, schema, codecs, repository, deletion) and spells every index name."""
    text = FILE.read_text("utf-8")
    tree = ast.parse(text)
    live = {"tables", "schema", "codecs", "repository", "deletion", "columns", "engine"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            parts = module.split(".")
            assert not (parts[0] == "tracer_evaldb" and set(parts[1:]) & live), module
            if module == "tracer_evaldb":
                assert not {a.name for a in node.names} & (live | {"metadata"}), module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[-1] not in live | {"metadata"}, alias.name
        if isinstance(node, ast.Name):
            assert node.id not in ("metadata", "ENTITY_TABLES", "SIDE_TABLES"), node.id
    missing = [name for name in NEW_INDEXES if f'"{name}"' not in text and f"'{name}'" not in text]
    assert missing == [], missing
    assert text.startswith(
        "# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 The Tracer contributors\n"
    )


# ===================================================================== what the revision does


def test_revision_0002_adds_exactly_the_access_pattern_indexes(
    mig: MigStore, migrate: ModuleType
) -> None:
    engine = _at_first(mig, migrate)
    before = catalog(engine)
    triggers = trigger_catalog(engine)
    assert not set(NEW_INDEXES) & set(db_indexes(engine))
    migrate.upgrade(engine)
    assert revision_in_database(engine) == "0002"
    diff = catalog_diff(before, catalog(engine))
    assert all(d.startswith("only in second: ") for d in diff), diff[:5]
    assert {d.split(":", 2)[-1] for d in diff} == set(NEW_INDEXES), diff[:5]
    assert set(NEW_INDEXES) <= set(db_indexes(engine))
    assert trigger_catalog(engine) == triggers


def test_revision_0002_keeps_every_row_and_the_store_readable(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """A store filled at revision 0001 is upgraded in place: every row of every table is equal
    afterwards, the repository reads the bundle, and ``open_store`` accepts the database."""
    engine = _at_first(mig, migrate)
    filled = populate(engine)
    rows = raw_dump(engine)
    empty = [t.name for t in evaldb.tables.ENTITY_TABLES.values() if not rows[t.name]]
    assert empty == []  # every one of the 26 entity tables holds rows
    migrate.upgrade(engine)
    assert raw_dump(engine) == rows
    loaded = evaldb.load_run_bundle(engine, filled.bundle.id)
    assert loaded.complete
    assert loaded.bundle == filled.bundle
    assert evaldb.open_store(engine).revision == "0002"


def test_revision_0002_downgrade_restores_revision_0001_exactly(
    mig: MigStore, migrate: ModuleType
) -> None:
    """The downgrade drops the 25 indexes and nothing else, on a filled store, without the
    data-loss flag (it destroys no row), leaving the very catalog and rows of 0001."""
    engine = _at_first(mig, migrate)
    populate(engine)
    before_catalog, before_rows, before_triggers = (
        catalog(engine),
        raw_dump(engine),
        trigger_catalog(engine),
    )
    migrate.upgrade(engine)
    assert revision_in_database(engine) == "0002"  # the upgrade did move it
    assert set(NEW_INDEXES) <= set(db_indexes(engine))
    migrate.downgrade(engine, "0001")  # allow_data_loss is not needed
    assert revision_in_database(engine) == "0001"
    assert catalog_diff(before_catalog, catalog(engine)) == []
    assert raw_dump(engine) == before_rows
    assert trigger_catalog(engine) == before_triggers
    assert not set(NEW_INDEXES) & set(db_indexes(engine))


@pytest.mark.parametrize("filled", [False, True], ids=["empty", "filled"])
def test_revision_0002_stairway_up_down_up(
    mig: MigStore, migrate: ModuleType, filled: bool
) -> None:
    """ADR 0021 rule 14 for the first real revision: up, down and up again leave the same schema
    and the same rows, on an empty and on a filled store."""
    engine = _at_first(mig, migrate)
    if filled:
        populate(engine)
    migrate.upgrade(engine)
    up = (catalog(engine), raw_dump(engine))
    migrate.downgrade(engine, "0001")
    migrate.upgrade(engine)
    assert catalog_diff(up[0], catalog(engine)) == []
    assert raw_dump(engine) == up[1]
    # all the way down and up again, with the flag the filled case needs
    migrate.downgrade(engine, "base", allow_data_loss=True)
    assert revision_in_database(engine) is None
    migrate.upgrade(engine)
    assert catalog_diff(up[0], catalog(engine)) == []
    assert revision_in_database(engine) == "0002"


def test_downgrade_through_0002_to_base_still_asks_for_the_flag_on_a_filled_store(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = mig.engine
    migrate.upgrade(engine)
    populate(engine)
    before = (catalog(engine), raw_dump(engine))
    with pytest.raises(evaldb.DowngradeDataLoss):
        migrate.downgrade(engine, "base")
    assert (catalog(engine), raw_dump(engine)) == before  # refused whole, before the first step
    assert revision_in_database(engine) == "0002"


def test_revision_0002_is_one_transaction(mig: MigStore, migrate: ModuleType) -> None:
    """A fault in the middle of the index statements leaves revision 0001 with none of them; the
    same for a downgrade; the retry then works."""
    engine = _at_first(mig, migrate)
    before = (catalog(engine), raw_dump(engine))
    with fault_on_statement(engine, r"CREATE INDEX", nth=12) as seen, pytest.raises(Boom):
        migrate.upgrade(engine)
    assert len(seen) == 12
    assert revision_in_database(engine) == "0001"
    assert (catalog(engine), raw_dump(engine)) == before
    assert not set(NEW_INDEXES) & set(db_indexes(engine))
    migrate.upgrade(engine)
    after = catalog(engine)
    with fault_on_statement(engine, r"DROP INDEX", nth=12) as seen, pytest.raises(Boom):
        migrate.downgrade(engine, "0001")
    assert len(seen) == 12
    assert revision_in_database(engine) == "0002"
    assert catalog_diff(after, catalog(engine)) == []
    assert set(NEW_INDEXES) <= set(db_indexes(engine))


def test_revision_0002_upgrade_over_a_current_database_sends_no_write(
    mig: MigStore, migrate: ModuleType
) -> None:
    engine = mig.engine
    migrate.upgrade(engine)
    assert revision_in_database(engine) == "0002"
    with recording(engine) as statements:
        migrate.upgrade(engine)
    assert [s for s in statements.writes if WRITE_OR_DDL.match(s)] == []
    assert [s for s, _ in statements.items if WRITE_OR_DDL.match(s)] == []


# ================================================================ open_store at an older revision


def test_open_store_at_an_older_shipped_revision_is_not_migrated(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """Case 3 of ``open_store`` (batch D A10): a shipped revision below the head is
    ``StoreNotMigrated(revision, head)``, distinct from the never-migrated case 1 and from an
    unknown revision, and the message says to run the upgrade."""
    engine = _at_first(mig, migrate)
    errors = importlib.import_module("tracer_evaldb.errors")
    with pytest.raises(errors.StoreNotMigrated) as caught:
        evaldb.open_store(engine)
    assert caught.value.revision == "0001"
    assert caught.value.head == "0002"
    assert "upgrade" in str(caught.value)
    assert not isinstance(caught.value, errors.StoreRevisionUnknown)
    empty = mig.sibling().engine
    with pytest.raises(errors.StoreNotMigrated) as never:
        evaldb.open_store(empty)
    assert never.value.revision is None  # case 1 stays distinguishable from case 3
    assert never.value.head == "0002"


def test_open_store_at_an_older_revision_changes_nothing_and_never_migrates(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = _at_first(mig, migrate)
    populate(engine)
    before = (catalog(engine), raw_dump(engine), revision_in_database(engine))
    with recording(engine) as statements:
        for _ in range(3):
            with pytest.raises(evaldb.StoreNotMigrated):
                evaldb.open_store(engine)
    assert [s for s, _ in statements.items if WRITE_OR_DDL.match(s)] == []
    assert (catalog(engine), raw_dump(engine), revision_in_database(engine)) == before
    assert not set(NEW_INDEXES) & set(db_indexes(engine))


def test_open_store_checks_the_revision_before_the_schema_version(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """At revision 0001 the tracer_meta row is current (0.0.1), so only the revision can refuse:
    the error is StoreNotMigrated, not a schema-version error and not a success."""
    engine = _at_first(mig, migrate)
    with engine.connect() as conn:
        meta = conn.exec_driver_sql("SELECT schema_version FROM tracer_meta").scalar_one()
        conn.rollback()
    assert meta == "0.0.1"
    with pytest.raises(evaldb.StoreNotMigrated):
        evaldb.open_store(engine)


def test_open_store_after_the_upgrade_returns_the_head(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = _at_first(mig, migrate)
    migrate.upgrade(engine)
    info = evaldb.open_store(engine)
    assert (info.revision, info.schema_version) == ("0002", "0.0.1")
    migrate.downgrade(engine, "0001")
    with pytest.raises(evaldb.StoreNotMigrated):
        evaldb.open_store(engine)
