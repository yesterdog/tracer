# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D, P2-14 and P2-15: Alembic inside the package and the migration stairway.

Contract: docs/phase2/api-surface-batch-d.md sections 1 to 4 (ADR 0021 rules 3 to 9, 13 and 14).
Every test uses an EMPTY database (the ``mig`` fixture: a SQLite file, or a throwaway database
created on the Postgres server and dropped afterwards), never the shared schema of the ``db``
fixture. Test-only revisions are written into a temporary directory and added with the
``version_locations`` argument; they are never part of the shipped chain.
"""

from __future__ import annotations

import ast
import configparser
import contextlib
import importlib
import importlib.metadata
import inspect
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_migration_support import (  # type: ignore[import-not-found,unused-ignore]
    MigStore,
    add_column_revision,
    catalog,
    catalog_diff,
    create_all_database,
    expected_trigger_catalog,
    failing_revision,
    fake_data_revision,
    fault_on_statement,
    orphan_row_revision,
    pause_on_statement,
    populate,
    raw_dump,
    revision_in_database,
    row_counts,
    trigger_catalog,
    user_tables,
    wait_reached,
    write_revision,
)
from evaldb_repo_support import Boom, recording  # type: ignore[import-not-found,unused-ignore]

mig = _mig_support.mig
sqlite_mig = _mig_support.sqlite_mig
postgres_mig = _mig_support.postgres_mig
migrate = _mig_support.migrate

ROOT = Path(__file__).resolve().parents[3]
EVALDB = ROOT / "packages" / "evaldb"
SRC = EVALDB / "src" / "tracer_evaldb"
MIGRATIONS = SRC / "migrations"
HEAD = "0001"
WRITE_OR_DDL = re.compile(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE|VACUUM)\b", re.I)
FILE_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.py$")


def _writes(statements: Any) -> list[str]:
    return [s for s, _many in statements.items if WRITE_OR_DDL.match(s)]


def _state(engine: sa.Engine) -> tuple[Any, Any, Any]:
    """Everything a failed or refused operation must leave alone."""
    return (catalog(engine), raw_dump(engine), revision_in_database(engine))


def _migrated(mig: MigStore, migrate: ModuleType) -> sa.Engine:
    engine: sa.Engine = mig.engine
    migrate.upgrade(engine)
    return engine


def _chain(tmp_path: Path, *steps: tuple[str, str, str]) -> Path:
    """A directory of test-only revisions: (revision, down, table) add-column steps."""
    directory = tmp_path / "test_versions"
    for revision, down, table in steps:
        add_column_revision(directory, revision, down, table)
    return directory


# ======================================================================== the shipped chain


def test_revision_chain_is_linear_and_follows_the_naming_convention(migrate: ModuleType) -> None:
    config = migrate.alembic_config()
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD]
    assert migrate.head_revision() == HEAD
    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == [HEAD]
    assert revisions[0].down_revision is None
    assert revisions[0].branch_labels == set()
    files = sorted(
        p.name for p in (MIGRATIONS / "versions").glob("*.py") if p.name != "__init__.py"
    )
    assert len(files) == len(revisions)
    for name in files:
        match = FILE_NAME.match(name)
        assert match, f"{name}: revision files are named NNNN_snake_case_slug.py"
    assert {FILE_NAME.match(n).group(1) for n in files} == {r.revision for r in revisions}  # type: ignore[union-attr]
    assert not any(n.startswith("t0") or "_test" in n for n in files)  # nothing test-only ships


def test_every_shipped_revision_declares_what_a_downgrade_drops(
    migrate: ModuleType, evaldb: ModuleType
) -> None:
    script = ScriptDirectory.from_config(migrate.alembic_config())
    for revision in script.walk_revisions():
        drops = revision.module.DOWNGRADE_DROPS
        assert isinstance(drops, tuple)
        assert all(isinstance(entry, str) for entry in drops)
        assert len(set(drops)) == len(drops)
    first = script.get_revision(HEAD).module.DOWNGRADE_DROPS
    # revision 0001 removes every table with its rows; tracer_meta holds the migration's own row
    assert set(first) == set(evaldb.metadata.tables) - {"tracer_meta"}


def test_revision_0001_is_frozen_text_and_never_reads_the_live_metadata() -> None:
    source = next((MIGRATIONS / "versions").glob("0001_*.py")).read_text("utf-8")
    tree = ast.parse(source)
    allowed = {"__future__", "alembic", "sqlalchemy", "importlib", "tracer_evaldb"}
    live = {"metadata", "tables", "schema", "codecs", "repository"}
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offences += [a.name for a in node.names if a.name.split(".")[0] not in allowed]
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top not in allowed:
                offences.append(node.module or "")
            parts = (node.module or "").split(".")
            if top == "tracer_evaldb" and len(parts) > 1 and parts[1] in live:
                offences.append(node.module or "")
            if node.module == "tracer_evaldb":
                offences += [a.name for a in node.names if a.name in live]
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "tracer_evaldb"
            and node.attr in live
        ):
            offences.append(f"tracer_evaldb.{node.attr}")
    assert not offences, offences
    # a frozen revision carries its own DDL: it names the tables it creates
    assert "tracer_meta" in source
    assert "deletion_audit" in source


def test_revision_template_generates_a_headed_typed_revision(
    migrate: ModuleType, tmp_path: Path
) -> None:
    from alembic import command

    directory = tmp_path / "gen"
    directory.mkdir()
    config = migrate.alembic_config(version_locations=[directory])
    command.revision(
        config,
        message="generated by the packaged template",
        rev_id="t900",
        head="0001",
        version_path=str(directory),
    )
    (path,) = list(directory.glob("*.py"))
    text = path.read_text("utf-8")
    assert text.startswith(
        "# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 The Tracer contributors\n"
    )
    tree = ast.parse(text)
    assigned = {
        t.id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name) and t.id in ("revision", "down_revision")
    }
    assert assigned == {"revision": "t900", "down_revision": "0001"}
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert set(functions) == {"upgrade", "downgrade"}
    assert all(ast.unparse(f.returns) == "None" for f in functions.values())  # type: ignore[arg-type]
    assert "DOWNGRADE_DROPS" in text


def test_importing_the_package_does_not_load_alembic() -> None:
    """Alembic (and Mako, and Pygments through it) is imported inside the functions that need it:
    ``import tracer_evaldb`` and every module outside ``tracer_evaldb.migrations`` load none of it.
    The runtime import whitelist of batch A relies on this (``import alembic`` loads ``pygments``
    where pytest installed it)."""
    code = """
import importlib, pkgutil, sys
import tracer_evaldb
names = [
    m.name
    for m in pkgutil.walk_packages(tracer_evaldb.__path__, "tracer_evaldb.")
    if ".migrations" not in m.name
]
for name in names:
    importlib.import_module(name)
print(sorted(m for m in ("alembic", "mako", "markupsafe", "pygments") if m in sys.modules))
print(len(names))
"""
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    loaded, count = done.stdout.split("\n")[:2]
    assert loaded == "[]", loaded
    assert int(count) >= 11  # the modules were really imported


# ==================================================================== configuration, no URL


def test_alembic_ini_holds_no_database_url() -> None:
    path = EVALDB / "alembic.ini"
    assert path.is_file()
    text = path.read_text("utf-8")
    parser = configparser.RawConfigParser()
    parser.read_string(text)
    assert parser.has_section("alembic")
    options = {k: v for s in parser.sections() for k, v in parser.items(s)}
    assert "sqlalchemy.url" not in options
    assert "url" not in options
    location = parser.get("alembic", "script_location")
    resolved = Path(location.replace("%(here)s", str(path.parent)))
    assert resolved.resolve() == MIGRATIONS.resolve()
    assert (resolved / "env.py").is_file()
    for value in options.values():
        assert "://" not in value
        assert "@" not in value
    for token in ("password", "passwd", "secret", "token"):
        assert token not in text.lower()


def test_migrations_package_holds_no_database_url_and_no_credential() -> None:
    pattern = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/'\"]*:[^\s/'\"]*@")
    offences = []
    for path in (*MIGRATIONS.rglob("*.py"), MIGRATIONS / "script.py.mako", SRC / "migrate.py"):
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if pattern.search(line):
                offences.append(f"{path.name}:{number}")
    assert not offences, offences


# =========================================================================== the wheel


def _wheel(directory: Path) -> Path:
    directory.mkdir()
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(directory)],
        cwd=EVALDB,
        check=True,
        capture_output=True,
    )
    (wheel,) = list(directory.glob("*.whl"))
    return wheel


def test_migrations_ship_in_wheel(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        extracted = tmp_path / "unpacked"
        archive.extractall(extracted)
    required = {
        "tracer_evaldb/migrations/__init__.py",
        "tracer_evaldb/migrations/env.py",
        "tracer_evaldb/migrations/script.py.mako",
        "tracer_evaldb/migrations/helpers.py",
        "tracer_evaldb/migrations/versions/__init__.py",
        "tracer_evaldb/migrate.py",
        "tracer_evaldb/versioning.py",
        "tracer_evaldb/deletion.py",
    }
    assert required <= names, sorted(required - names)
    versions = sorted(
        n for n in names if n.startswith("tracer_evaldb/migrations/versions/") and n.endswith(".py")
    )
    revision_files = [n for n in versions if not n.endswith("__init__.py")]
    assert [Path(n).name[:4] for n in revision_files] == [HEAD]
    assert not [n for n in names if re.search(r"/t\d{4}_|_test\.py", n)]
    # the packaged files alone can migrate an empty database (no source checkout on the path)
    database = tmp_path / "from_wheel.db"
    code = f"""
import sys
sys.path.insert(0, {str(extracted)!r})
import tracer_evaldb, tracer_evaldb.migrate as m
assert tracer_evaldb.__file__.startswith({str(extracted)!r}), tracer_evaldb.__file__
engine = tracer_evaldb.create_store_engine("sqlite:///" + {str(database)!r})
m.upgrade(engine)
assert m.current_revision(engine) == m.head_revision() == {HEAD!r}
info = tracer_evaldb.open_store(engine)
print("ok", info.revision, info.schema_version)
"""
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == f"ok {HEAD} 0.0.1"


# ================================================================ the programmatic API


def test_programmatic_upgrade_api(mig: MigStore, migrate: ModuleType, evaldb: ModuleType) -> None:
    signature = inspect.signature
    up = signature(migrate.upgrade).parameters
    assert list(up) == ["engine", "revision", "version_locations"]
    assert up["revision"].default == "head"
    assert up["version_locations"].kind is inspect.Parameter.KEYWORD_ONLY
    assert up["version_locations"].default == ()
    down = signature(migrate.downgrade).parameters
    assert list(down) == ["engine", "revision", "allow_data_loss", "version_locations"]
    assert down["revision"].default is inspect.Parameter.empty  # explicit: no default target
    assert down["allow_data_loss"].kind is inspect.Parameter.KEYWORD_ONLY
    assert down["allow_data_loss"].default is False
    assert list(signature(migrate.current_revision).parameters) == ["engine"]
    assert list(signature(migrate.head_revision).parameters) == []
    config_params = signature(migrate.alembic_config).parameters
    assert list(config_params) == ["engine", "version_locations"]
    assert config_params["engine"].default is None

    engine = mig.engine
    assert migrate.current_revision(engine) is None
    assert user_tables(engine) == []
    # there is nothing to downgrade in a database that was never migrated: not an error, no write
    with recording(engine) as statements:
        migrate.downgrade(engine, "base")
    assert _writes(statements) == []
    assert user_tables(engine) == []
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == migrate.head_revision() == HEAD
    assert revision_in_database(engine) == HEAD
    assert user_tables(engine) == sorted(evaldb.metadata.tables)
    # a second upgrade is a no-op: not one write or DDL statement
    with recording(engine) as statements:
        migrate.upgrade(engine)
        migrate.upgrade(engine, "head")
        migrate.upgrade(engine, HEAD)
    assert _writes(statements) == []
    # the engine is the caller's: still usable, never disposed or closed by the call (a dispose
    # replaces the pool of the engine)
    pool = engine.pool
    migrate.upgrade(engine, "head")
    assert engine.pool is pool
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT 1").scalar_one() == 1
    migrate.downgrade(engine, "base")
    assert migrate.current_revision(engine) is None
    assert user_tables(engine) == []
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == HEAD


def test_migrate_arguments_are_checked_before_any_statement(
    mig: MigStore, migrate: ModuleType
) -> None:
    engine = mig.engine
    with recording(engine) as statements:
        for bad in (None, "sqlite://", engine.url, "head"):
            with pytest.raises(TypeError):
                migrate.upgrade(bad)
            with pytest.raises(TypeError):
                migrate.downgrade(bad, "base")
            with pytest.raises(TypeError):
                migrate.current_revision(bad)
        with engine.connect() as conn, pytest.raises(TypeError):
            migrate.upgrade(conn)
        for bad_revision in (None, 1, b"head"):
            with pytest.raises(TypeError):
                migrate.upgrade(engine, bad_revision)
            with pytest.raises(TypeError):
                migrate.downgrade(engine, bad_revision)
        with pytest.raises(TypeError):
            migrate.downgrade(engine)
        with pytest.raises(TypeError):
            migrate.downgrade(engine, "base", True)
        for bad_flag in (None, 1, "yes"):
            with pytest.raises(TypeError):
                migrate.downgrade(engine, "base", allow_data_loss=bad_flag)
        with pytest.raises(ValueError, match="revision"):
            migrate.upgrade(engine, "")
        with pytest.raises(ValueError, match="revision"):
            migrate.upgrade(engine, "no_such_revision")
    assert _writes(statements) == []
    assert user_tables(engine) == []


def test_alembic_config_carries_the_engine_and_no_url(
    mig: MigStore, migrate: ModuleType, tmp_path: Path
) -> None:
    engine = mig.engine
    config = migrate.alembic_config(engine)
    assert config.attributes["engine"] is engine
    assert config.get_main_option("sqlalchemy.url") is None
    assert "engine" not in migrate.alembic_config().attributes
    script = ScriptDirectory.from_config(config)
    assert script.dir == str(MIGRATIONS) or Path(script.dir).resolve() == MIGRATIONS.resolve()
    extra = tmp_path / "extra"
    add_column_revision(extra, "t100", HEAD, "project")
    wider = ScriptDirectory.from_config(migrate.alembic_config(version_locations=[extra]))
    assert sorted(wider.get_heads()) == ["t100"]
    # the shipped chain is unchanged by a call that adds a directory
    assert ScriptDirectory.from_config(migrate.alembic_config()).get_heads() == [HEAD]


def test_alembic_head_matches_metadata_exactly(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """The migrated schema is the schema ``metadata.create_all`` builds: every table, column,
    constraint (CHECK literals included), index (partial ones too), trigger and function, and
    Alembic's own comparison is empty."""
    engine = _migrated(mig, migrate)
    reference = create_all_database(mig.sibling())
    differences = catalog_diff(catalog(reference), catalog(engine))
    assert differences == [], differences[:5]
    assert trigger_catalog(engine) == trigger_catalog(reference)

    def include(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
        return not (type_ == "table" and name == "alembic_version")

    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": include,
            },
        )
        diff = compare_metadata(context, evaldb.metadata)
        conn.rollback()
    assert diff == [], [str(d)[:200] for d in diff[:5]]


def test_catalog_comparison_detects_drift(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """The comparison above has teeth: one dropped index shows in the catalog and in Alembic."""
    engine = _migrated(mig, migrate)
    reference = create_all_database(mig.sibling())
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_artifact_retain_until")
    assert catalog_diff(catalog(reference), catalog(engine)) != []

    def include(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
        return not (type_ == "table" and name == "alembic_version")

    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object": include})
        assert compare_metadata(context, evaldb.metadata) != []
        conn.rollback()


def test_migrated_schema_has_the_immutability_triggers(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """ADR 0020 Decision 7 on the Alembic schema: 37 update triggers and the delete trigger of
    deletion_audit, and nothing else (``alembic_version`` has none)."""
    engine = _migrated(mig, migrate)
    assert len(evaldb.metadata.tables) == 37
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    assert len(trigger_catalog(engine)) == 38


def test_revision_0001_writes_the_tracer_meta_row(mig: MigStore, migrate: ModuleType) -> None:
    engine = _migrated(mig, migrate)
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT singleton, schema_version, evaldb_version FROM tracer_meta"
        ).all()
        conn.rollback()
    assert [tuple(r) for r in rows] == [(1, "0.0.1", importlib.metadata.version("tracer-evaldb"))]
    # downgrading an untouched database needs no flag, and the row goes with the table
    migrate.downgrade(engine, "base")
    assert user_tables(engine) == []


# ======================================================================= open_store


def test_open_store_refuses_unmigrated_database(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = mig.engine
    errors = importlib.import_module("tracer_evaldb.errors")
    # 1. an empty database
    with pytest.raises(errors.StoreNotMigrated) as caught:
        evaldb.open_store(engine)
    assert caught.value.revision is None
    assert caught.value.head == HEAD
    assert issubclass(errors.StoreNotMigrated, errors.StoreError)
    assert "upgrade" in str(caught.value)
    # 2. a database made by metadata.create_all: the same tables, but never migrated
    other = create_all_database(mig.sibling())
    with pytest.raises(errors.StoreNotMigrated) as caught:
        evaldb.open_store(other)
    assert caught.value.revision is None
    # 3. a database migrated and then downgraded to base (alembic_version exists and is empty)
    migrate.upgrade(engine)
    migrate.downgrade(engine, "base")
    assert revision_in_database(engine) is None
    with pytest.raises(errors.StoreNotMigrated):
        evaldb.open_store(engine)
    # 4. at head: the check passes and says what it found
    migrate.upgrade(engine)
    info = evaldb.open_store(engine)
    assert (info.revision, info.schema_version) == (HEAD, "0.0.1")
    assert info.evaldb_version == importlib.metadata.version("tracer-evaldb")
    with pytest.raises(AttributeError):
        info.revision = "x"  # a frozen dataclass
    # the argument is the engine of create_store_engine
    for bad in (None, "sqlite://", engine.url):
        with pytest.raises(TypeError):
            evaldb.open_store(bad)
    with engine.connect() as conn, pytest.raises(TypeError):
        evaldb.open_store(conn)
    assert list(inspect.signature(evaldb.open_store).parameters) == ["engine"]


def test_open_store_errors_never_echo_the_database_url(mig: MigStore, evaldb: ModuleType) -> None:
    engine = mig.engine
    with pytest.raises(evaldb.StoreNotMigrated) as caught:
        evaldb.open_store(engine)
    text = str(caught.value) + repr(caught.value) + repr(caught.value.args)
    url = engine.url
    for secret in (url.password, url.host, url.database, url.username):
        if secret:
            assert str(secret) not in text


def test_open_store_never_auto_migrates(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """open_store reads and refuses; it creates nothing, changes nothing, runs no DDL."""
    engine = mig.engine
    with recording(engine) as statements, pytest.raises(evaldb.StoreNotMigrated):
        evaldb.open_store(engine)
    assert _writes(statements) == []
    assert user_tables(engine) == []
    assert revision_in_database(engine) is None
    # a create_all database stays without alembic_version and without a row in tracer_meta
    other = create_all_database(mig.sibling())
    before = _state(other)
    with recording(other) as statements, pytest.raises(evaldb.StoreNotMigrated):
        evaldb.open_store(other)
    assert _writes(statements) == []
    assert _state(other) == before
    # a migrated database that is then downgraded: refused, and still at base afterwards
    migrate.upgrade(engine)
    migrate.downgrade(engine, "base")
    before = _state(engine)
    with recording(engine) as statements, pytest.raises(evaldb.StoreNotMigrated):
        evaldb.open_store(engine)
    assert _writes(statements) == []
    assert _state(engine) == before
    # even the success path writes nothing
    migrate.upgrade(engine)
    before = _state(engine)
    with recording(engine) as statements:
        evaldb.open_store(engine)
    assert _writes(statements) == []
    assert _state(engine) == before


def test_repository_functions_do_not_require_a_migrated_database(
    mig: MigStore, evaldb: ModuleType
) -> None:
    """open_store is a separate, explicit check: the batch C functions keep working on a
    metadata.create_all database (the suites of batches B and C build theirs that way)."""
    engine = create_all_database(mig)
    assert evaldb.latest_version(engine, "persona", _support.new_uuid()) is None


# ============================================================ the command line (alembic)


def _alembic(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-c", str(EVALDB / "alembic.ini"), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_command_line_upgrades_and_checks_with_x_url(
    tmp_path: Path, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """``alembic -c packages/evaldb/alembic.ini -x url=... upgrade head`` and ``check`` (the lint
    job of the CI), run from any directory; the URL comes from the command line, never the file."""
    database = tmp_path / "cli.db"
    url = f"url=sqlite:///{database}"
    done = _alembic(["-x", url, "upgrade", "head"], tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    engine = evaldb.create_store_engine(f"sqlite:///{database}")
    assert migrate.current_revision(engine) == HEAD
    assert evaldb.open_store(engine).revision == HEAD
    checked = _alembic(["-x", url, "check"], tmp_path)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    # no URL, no engine: refused with a message, not a traceback about a missing option
    bare = _alembic(["current"], tmp_path)
    assert bare.returncode != 0
    assert "url" in (bare.stdout + bare.stderr).lower()
    # drift makes `check` fail
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_transcript_retain_until")
    drifted = _alembic(["-x", url, "check"], tmp_path)
    assert drifted.returncode != 0, drifted.stdout + drifted.stderr
    engine.dispose()


# ====================================================================== the stairway


def _stair_chain(tmp_path: Path) -> tuple[Path, list[str]]:
    """0001, then three test-only revisions: two batch rebuilds (a parent table and a leaf) and the
    fake 0.0.2 data migration."""
    directory = tmp_path / "stairs"
    add_column_revision(directory, "t001", HEAD, "project")
    add_column_revision(directory, "t002", "t001", "run")
    fake_data_revision(directory, "t003", "t002")
    return directory, [HEAD, "t001", "t002", "t003"]


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated"])
@pytest.mark.parametrize(
    "with_test_revisions", [False, True], ids=["shipped", "with-test-revisions"]
)
def test_migration_stairway_up_down_up(
    mig: MigStore,
    migrate: ModuleType,
    tmp_path: Path,
    populated: bool,
    with_test_revisions: bool,
) -> None:
    """Each revision goes up, down and up again with the same schema and the same rows, on an empty
    and on a populated database (ADR 0021 rule 14)."""
    engine = mig.engine
    locations: list[Path] = []
    levels = [HEAD]
    if with_test_revisions:
        directory, levels = _stair_chain(tmp_path)
        locations = [directory]
    migrate.upgrade(engine, HEAD)
    if populated:
        populate(engine)
    states = {HEAD: _state(engine)}
    previous = HEAD
    for revision in levels[1:]:
        migrate.upgrade(engine, revision, version_locations=locations)
        assert migrate.current_revision(engine) == revision
        up_state = _state(engine)
        assert up_state != states[previous]
        migrate.downgrade(engine, previous, version_locations=locations)
        assert migrate.current_revision(engine) == previous
        assert _state(engine) == states[previous], f"down from {revision} is not exact"
        migrate.upgrade(engine, revision, version_locations=locations)
        assert _state(engine) == up_state, f"up to {revision} again is not the same"
        states[revision] = up_state
        previous = revision
    # all the way down and up again: the same schema (rows are gone with the tables)
    schema = catalog(engine) if not with_test_revisions else None
    migrate.downgrade(engine, "base", allow_data_loss=True, version_locations=locations)
    assert migrate.current_revision(engine) is None
    assert user_tables(engine) == []
    migrate.upgrade(engine, previous, version_locations=locations)
    assert migrate.current_revision(engine) == previous
    if schema is not None:
        assert catalog(engine) == schema
    assert sum(n for t, n in row_counts(engine).items() if t != "tracer_meta") == 0


# ============================================================== data loss on downgrade


def test_downgrade_base_on_populated_db_requires_explicit_data_loss_flag(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = _migrated(mig, migrate)
    errors = importlib.import_module("tracer_evaldb.errors")
    # an untouched database: nothing but the migration's own row, so no flag is needed
    migrate.downgrade(engine, "base")
    assert user_tables(engine) == []
    migrate.upgrade(engine)
    # a single audit row is data too
    populate(engine)
    populated = {t for t, n in row_counts(engine).items() if n and t != "tracer_meta"}
    assert populated >= {"run", "artifact", "deletion_audit", "export_bundle", "dataset_item"}
    before = _state(engine)
    for kwargs in ({}, {"allow_data_loss": False}):
        with (
            recording(engine) as statements,
            pytest.raises(errors.DowngradeDataLoss) as caught,
        ):
            migrate.downgrade(engine, "base", **kwargs)
        assert set(caught.value.tables) == populated
        assert list(caught.value.tables) == sorted(caught.value.tables)
        assert issubclass(errors.DowngradeDataLoss, errors.StoreError)
        assert "allow_data_loss" in str(caught.value)
        assert _writes(statements) == []  # refused before the first step
        assert _state(engine) == before
    migrate.downgrade(engine, "base", allow_data_loss=True)
    assert migrate.current_revision(engine) is None
    assert user_tables(engine) == []
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            left = conn.exec_driver_sql(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'tracer_reject_%' "
                "AND pronamespace = current_schema()::regnamespace"
            ).scalar_one()
            conn.rollback()
        assert left == 0


def test_the_only_audit_row_is_enough_to_require_the_flag(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    engine = _migrated(mig, migrate)
    with engine.begin() as conn:
        _support.Rows(evaldb.metadata).insert(conn, "deletion_audit")
    with pytest.raises(evaldb.DowngradeDataLoss) as caught:
        migrate.downgrade(engine, "base")
    assert caught.value.tables == ("deletion_audit",)
    assert revision_in_database(engine) == HEAD


def test_downgrade_that_drops_a_column_with_values_requires_the_flag(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """A revision declares ``DOWNGRADE_DROPS = ("project.note",)``: the downgrade needs the flag
    exactly when some row has a value in that column."""
    directory = _chain(tmp_path, ("t001", HEAD, "project"))
    engine = mig.engine
    migrate.upgrade(engine, "t001", version_locations=[directory])
    project = sa.Table(
        "project",
        sa.MetaData(),
        *[c._copy() for c in evaldb.metadata.tables["project"].columns],
        sa.Column("note", sa.Text(), nullable=True),
    )
    rows = _support.Rows(evaldb.metadata)
    values = rows.values("project")
    with engine.begin() as conn:
        conn.execute(project.insert().values(**values))  # note is NULL
    # every value NULL: dropping the column loses nothing
    with recording(engine) as statements:
        migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert statements.count > 0
    assert migrate.current_revision(engine) == HEAD
    # now with a value
    migrate.upgrade(engine, "t001", version_locations=[directory])
    values = rows.values("project")
    with engine.begin() as conn:
        conn.execute(project.insert().values(**values, note="keep me"))
    before = _state(engine)
    with pytest.raises(evaldb.DowngradeDataLoss) as caught:
        migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert caught.value.tables == ("project",)
    assert _state(engine) == before
    migrate.downgrade(engine, HEAD, allow_data_loss=True, version_locations=[directory])
    assert migrate.current_revision(engine) == HEAD
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(evaldb.metadata.tables["project"])
            ).scalar_one()
            == 2
        )
        conn.rollback()


def test_a_multi_step_downgrade_is_refused_whole_before_the_first_step(
    mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    """The loss is in the older of two steps (``project.note``); the newer step (``run.note``)
    would run first and lose nothing, yet nothing may change: the refusal covers the whole path."""
    directory = _chain(tmp_path, ("t001", HEAD, "project"), ("t002", "t001", "run"))
    engine = mig.engine
    migrate.upgrade(engine, "t002", version_locations=[directory])
    project = sa.Table(
        "project",
        sa.MetaData(),
        *[c._copy() for c in evaldb.metadata.tables["project"].columns],
        sa.Column("note", sa.Text(), nullable=True),
    )
    values = _support.Rows(evaldb.metadata).values("project")
    with engine.begin() as conn:
        conn.execute(project.insert().values(**values, note="keep me"))
    before = _state(engine)
    with recording(engine) as statements, pytest.raises(evaldb.DowngradeDataLoss) as caught:
        migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert caught.value.tables == ("project",)
    assert _writes(statements) == []
    assert _state(engine) == before
    assert revision_in_database(engine) == "t002"
    # asking for the one step that loses nothing is allowed
    migrate.downgrade(engine, "t001", version_locations=[directory])
    assert revision_in_database(engine) == "t001"


# =========================================================== SQLite: batch mode and pragmas


def _table_list(engine: sa.Engine) -> dict[str, tuple[int, int]]:
    """table -> (wr, strict) from PRAGMA table_list (SQLite 3.37 and later)."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_list").all()
        conn.rollback()
    return {r[1]: (r[4], r[5]) for r in rows if r[0] == "main"}


def _foreign_keys_on(engine: sa.Engine) -> int:
    """``PRAGMA foreign_keys`` of four connections held at once, so that every idle connection of
    the pool is seen (a pool hands out its connections in turn); the smallest value."""
    with contextlib.ExitStack() as stack:
        connections = [stack.enter_context(engine.connect()) for _ in range(4)]
        values = [int(c.exec_driver_sql("PRAGMA foreign_keys").scalar_one()) for c in connections]
        for connection in connections:
            connection.rollback()
    return min(values)


def test_sqlite_foreign_keys_are_off_during_a_migration_and_on_after(
    sqlite_mig: MigStore, migrate: ModuleType, tmp_path: Path
) -> None:
    directory = tmp_path / "probe"
    write_revision(
        directory,
        "t001",
        HEAD,
        """
        DOWNGRADE_DROPS = ()


        def upgrade() -> None:
            seen = op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            op.execute("CREATE TABLE fk_probe (seen INTEGER)")
            op.execute(f"INSERT INTO fk_probe VALUES ({seen})")


        def downgrade() -> None:
            op.execute("DROP TABLE fk_probe")
        """,
    )
    engine = sqlite_mig.engine
    assert _foreign_keys_on(engine) == 1
    migrate.upgrade(engine, "t001", version_locations=[directory])
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT seen FROM fk_probe").scalar_one() == 0
        conn.rollback()
    # on again for every connection the pool hands out afterwards, the migrating one included
    for _ in range(3):
        assert _foreign_keys_on(engine) == 1
    other = sqlite_mig.new_engine()
    assert _foreign_keys_on(other) == 1


@pytest.mark.parametrize("parent", ["project", "config_snapshot", "run"])
def test_sqlite_batch_migration_preserves_child_rows(
    sqlite_mig: MigStore, migrate: ModuleType, tmp_path: Path, parent: str
) -> None:
    """Rebuilding a parent table (drop, create, copy, rename) with foreign keys on would cascade
    into its children; with them off during the migration, and a foreign_key_check after, every
    child row survives and no row dangles."""
    engine = sqlite_mig.engine
    migrate.upgrade(engine)
    populate(engine)
    before = row_counts(engine)
    assert before["run"] > 0
    assert before["artifact"] > 0
    assert before["external_ref_index"] > 0
    directory = _chain(tmp_path, ("t001", HEAD, parent))
    migrate.upgrade(engine, "t001", version_locations=[directory])
    assert row_counts(engine) == before
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        conn.rollback()
    assert _foreign_keys_on(engine) == 1
    migrate.downgrade(engine, HEAD, version_locations=[directory])
    assert row_counts(engine) == before
    # the hazard is real: the same rebuild with foreign keys on removes the children
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE project")
    assert row_counts(engine)["run"] == 0


@pytest.mark.parametrize(
    "table", ["project", "run", "turn", "dataset_member", "export_bundle", "deletion_audit"]
)
def test_sqlite_batch_migration_preserves_strict_and_triggers(
    sqlite_mig: MigStore, migrate: ModuleType, tmp_path: Path, table: str
) -> None:
    engine = sqlite_mig.engine
    migrate.upgrade(engine)
    before = catalog(engine)
    flags_before = _table_list(engine)
    assert flags_before[table] == (1, 1)  # WITHOUT ROWID and STRICT
    directory = _chain(tmp_path, ("t001", HEAD, table))
    migrate.upgrade(engine, "t001", version_locations=[directory])
    # the rebuilt table is still STRICT and WITHOUT ROWID
    assert _table_list(engine) == flags_before
    after = catalog(engine)
    # the only change is the column the revision added: constraints, indexes (the partial ones
    # too), foreign keys and the immutability triggers all came back
    changed = catalog_diff(before, after)
    assert len(changed) == 1, changed
    assert changed[0].startswith(f"differs: table:{table}:"), changed
    assert trigger_catalog(engine) == expected_trigger_catalog(engine)
    with engine.connect() as conn:
        foreign = conn.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').all()
        conn.rollback()
    assert len(foreign) == sum(
        len(c.elements)
        for c in importlib.import_module("tracer_evaldb")
        .metadata.tables[table]
        .foreign_key_constraints
    )
    # and they work: a no-op UPDATE of a stored row is still refused
    evaldb = importlib.import_module("tracer_evaldb")
    t = evaldb.metadata.tables[table]
    with engine.begin() as conn:
        _support.Rows(evaldb.metadata).insert(conn, table)
    column = next(c for c in t.columns if not c.primary_key)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.update(t).values({column.name: t.c[column.name]}))
    if table == "deletion_audit":
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(sa.delete(t))


def test_sqlite_foreign_key_check_failure_rolls_the_revision_back(
    sqlite_mig: MigStore, migrate: ModuleType, evaldb: ModuleType, tmp_path: Path
) -> None:
    directory = tmp_path / "orphan"
    orphan_row_revision(directory, "t001", HEAD)
    engine = sqlite_mig.engine
    migrate.upgrade(engine)
    before = _state(engine)
    with pytest.raises(evaldb.MigrationIntegrityError) as caught:
        migrate.upgrade(engine, "t001", version_locations=[directory])
    assert caught.value.tables == ("external_ref_index",)
    assert issubclass(evaldb.MigrationIntegrityError, evaldb.StoreError)
    assert _state(engine) == before
    assert revision_in_database(engine) == HEAD
    assert _foreign_keys_on(engine) == 1  # on again after a failed migration too


# ======================================================== transactions, both backends


def test_failed_migration_rolls_back_to_the_previous_revision(
    mig: MigStore, migrate: ModuleType, tmp_path: Path
) -> None:
    """Each revision runs in its own transaction: t002 fails after its DDL and its INSERT, and the
    database is at t001 exactly (ADR 0021 rule 8; SQLite transacts DDL too, rule 7)."""
    directory = tmp_path / "chain"
    add_column_revision(directory, "t001", HEAD, "project")
    failing_revision(directory, "t002", "t001")
    engine = mig.engine
    migrate.upgrade(engine, "t001", version_locations=[directory])
    before = _state(engine)
    with pytest.raises(RuntimeError, match="test-only failure"):
        migrate.upgrade(engine, "t002", version_locations=[directory])
    assert "scratch_marker" not in user_tables(engine)
    assert revision_in_database(engine) == "t001"
    assert _state(engine) == before
    # from base in one call: 0001 and t001 are committed (one transaction per revision)
    migrate.downgrade(engine, "base", allow_data_loss=True, version_locations=[directory])
    with pytest.raises(RuntimeError):
        migrate.upgrade(engine, "head", version_locations=[directory])
    assert revision_in_database(engine) == "t001"
    assert migrate.current_revision(engine) == "t001"


def test_failed_first_migration_leaves_an_empty_database(
    mig: MigStore, migrate: ModuleType
) -> None:
    """A fault in the middle of revision 0001 (at the first trigger) leaves nothing behind: no
    table, no alembic_version, no trigger function."""
    engine = mig.engine
    with fault_on_statement(engine, r"CREATE TRIGGER") as seen, pytest.raises(Boom):
        migrate.upgrade(engine)
    assert seen
    assert user_tables(engine) == []
    assert revision_in_database(engine) is None
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            left = conn.exec_driver_sql(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'tracer_reject_%' "
                "AND pronamespace = current_schema()::regnamespace"
            ).scalar_one()
            conn.rollback()
        assert left == 0
    migrate.upgrade(engine)  # and the retry works
    assert migrate.current_revision(engine) == HEAD


def test_upgrade_over_an_unmanaged_schema_changes_nothing(
    mig: MigStore, migrate: ModuleType
) -> None:
    """A database built by metadata.create_all has the tables but no alembic_version: revision 0001
    cannot be applied over them, and the attempt leaves the database exactly as it was."""
    engine = create_all_database(mig)
    before = _state(engine)
    with pytest.raises(Exception, match=r".+"):
        migrate.upgrade(engine)
    assert _state(engine) == before
    assert revision_in_database(engine) is None


@pytest.mark.postgres
def test_postgres_migration_is_transactional(
    postgres_mig: MigStore, migrate: ModuleType, evaldb: ModuleType
) -> None:
    """The whole of revision 0001, DDL included, is one transaction on Postgres: a second session
    sees nothing of it until the commit, and a fault in the middle leaves nothing."""
    import threading

    engine = postgres_mig.engine
    monitor = postgres_mig.new_engine()
    reached, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            migrate.upgrade(engine)
        except BaseException as exc:
            errors.append(exc)

    with pause_on_statement(
        engine, lambda s: s.lstrip().upper().startswith("CREATE TRIGGER"), reached, release
    ):
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        wait_reached(reached, worker, "the migration never reached its first trigger", errors)
        # the migrating session has already created the tables and the functions ...
        with monitor.connect() as conn:
            seen = conn.exec_driver_sql(
                "SELECT to_regclass('project'), to_regclass('alembic_version'), "
                "(SELECT count(*) FROM pg_proc WHERE proname LIKE 'tracer_reject_%')"
            ).one()
            conn.rollback()
        assert (seen[0], seen[2]) == (None, 0)  # ... and nobody else can see any of it
        assert seen[1] is None or revision_in_database(monitor) is None
        release.set()
        worker.join(timeout=120)
    assert not worker.is_alive()
    assert errors == []
    assert migrate.current_revision(monitor) == HEAD
    assert sorted(user_tables(monitor)) == sorted(evaldb.metadata.tables)
    # and the reverse direction is one transaction too
    with fault_on_statement(engine, r"DROP TABLE", nth=5) as hit, pytest.raises(Boom):
        migrate.downgrade(engine, "base")
    assert hit
    assert migrate.current_revision(monitor) == HEAD
    assert sorted(user_tables(monitor)) == sorted(evaldb.metadata.tables)
    assert trigger_catalog(monitor) == expected_trigger_catalog(monitor)


# ---------------------------------------------------------------------- self-check of helpers


def test_state_helpers_see_a_difference(mig: MigStore, migrate: ModuleType) -> None:
    """``_state`` has teeth: one extra row and one extra column both change it."""
    engine = _migrated(mig, migrate)
    first = _state(engine)
    with engine.begin() as conn:
        _support.Rows(importlib.import_module("tracer_evaldb").metadata).insert(conn, "project")
    assert _state(engine) != first
    migrate.downgrade(engine, "base", allow_data_loss=True)
    assert _state(engine) != first
