# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers shared by the Phase 2 batch D tests (migrations, version policy, deletion, retention).

Contract: docs/phase2/api-surface-batch-d.md. Imported with ``from evaldb_migration_support import
...`` (the tests directory has no ``__init__.py``; mypy cannot see sibling modules, hence the
``type: ignore`` on those imports). Holds no test except the self-tests of the test doubles in
``test_evaldb_migration_doubles.py``.

Contents: the ``mig`` fixtures (an EMPTY database per test: a SQLite file, or a throwaway database
created on the Postgres server of ``TRACER_TEST_POSTGRES_URL`` and dropped afterwards); a catalog
fingerprint of a schema on either backend (tables, columns, constraints, indexes, triggers); the
writers of test-only revisions; the test doubles of a data migration (a byte-exact text rewrite of
``schema_version`` and its variants that must be refused); a populated store; statement faults and
pauses on an engine.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    Boom,
    load_run_bundle_doc,
    store_bundle,
    support,
)
from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    Rows,
    entity_models,
    load_golden,
)
from tracer_evalschema import loader as ev_loader

POSTGRES_URL_ENV = "TRACER_TEST_POSTGRES_URL"
SCHEMA_V1 = "0.0.1"
SCHEMA_V2 = "0.0.2"

# =================================================================== databases for migrations


class MigStore:
    """An empty database (nothing created in it) and the engines opened on it."""

    def __init__(
        self,
        backend: str,
        url: Any,
        cleanup: Callable[[], None],
        make_sibling: Callable[[], MigStore] | None = None,
    ) -> None:
        self.backend = backend
        self._url = url
        self._cleanup = cleanup
        self._make_sibling = make_sibling
        self._siblings: list[MigStore] = []
        self._engines: list[sa.Engine] = []
        self._evaldb = importlib.import_module("tracer_evaldb")

    def sibling(self) -> MigStore:
        """Another empty database of the same kind (the reference schema of a comparison)."""
        assert self._make_sibling is not None
        found = self._make_sibling()
        self._siblings.append(found)
        return found

    def new_engine(self, **kwargs: Any) -> sa.Engine:
        """A new store engine (``create_store_engine``) on this database."""
        engine: sa.Engine = self._evaldb.create_store_engine(self._url, **kwargs)
        self._engines.append(engine)
        return engine

    @property
    def engine(self) -> sa.Engine:
        if not self._engines:
            self.new_engine()
        return self._engines[0]

    def close(self) -> None:
        for engine in self._engines:
            engine.dispose()
        for sibling in self._siblings:
            sibling.close()
        self._cleanup()


@contextlib.contextmanager
def _sqlite_mig(tmp_path: Path) -> Iterator[MigStore]:
    counter = [0]

    def make() -> MigStore:
        counter[0] += 1
        return MigStore("sqlite", f"sqlite:///{tmp_path / f'mig{counter[0]}.db'}", lambda: None)

    handle = MigStore("sqlite", f"sqlite:///{tmp_path / 'mig.db'}", lambda: None, make)
    try:
        yield handle
    finally:
        handle.close()


@contextlib.contextmanager
def _postgres_mig() -> Iterator[MigStore]:
    base = os.environ.get(POSTGRES_URL_ENV)
    assert base, f"{POSTGRES_URL_ENV} is not set"  # collection already refused this case
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT", hide_parameters=True)
    created: list[str] = []

    def make() -> MigStore:
        name = f"tracer_mig_{uuid.uuid4().hex[:16]}"
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created.append(name)
        return MigStore("postgres", sa.engine.make_url(base).set(database=name), lambda: None)

    def drop() -> None:
        try:
            with admin.connect() as conn:
                for name in created:
                    conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            admin.dispose()

    handle = make()
    handle._cleanup = drop
    handle._make_sibling = make
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def mig(request: pytest.FixtureRequest) -> Iterator[MigStore]:
    """An empty database on each backend (the ``[postgres]`` case carries the marker)."""
    if request.param == "sqlite":
        tmp_path: Path = request.getfixturevalue("tmp_path")
        with _sqlite_mig(tmp_path) as handle:
            yield handle
    else:
        with _postgres_mig() as handle:
            yield handle


@pytest.fixture
def sqlite_mig(tmp_path: Path) -> Iterator[MigStore]:
    with _sqlite_mig(tmp_path) as handle:
        yield handle


@pytest.fixture
def postgres_mig() -> Iterator[MigStore]:
    """For tests that carry ``@pytest.mark.postgres`` themselves."""
    with _postgres_mig() as handle:
        yield handle


@pytest.fixture(scope="session")
def migrate() -> ModuleType:
    return importlib.import_module("tracer_evaldb.migrate")


def create_all_database(mig: MigStore) -> sa.Engine:
    """The tables made by ``metadata.create_all`` (no Alembic): the reference schema."""
    evaldb = importlib.import_module("tracer_evaldb")
    engine = mig.engine
    evaldb.metadata.create_all(engine)
    return engine


# =============================================================== the catalog of a schema


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body at the commas outside parentheses and string literals."""
    parts: list[str] = []
    depth, start, quoted = 0, 0, False
    for index, char in enumerate(body):
        if char == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return [_squash(p) for p in parts if p.strip()]


def _table_signature(sql: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(sorted column and constraint entries, sorted table options) of a SQLite CREATE TABLE.
    An explicit ``COLLATE "BINARY"`` is SQLite's default and is dropped (a table rebuilt by batch
    mode no longer says it)."""
    sql = sql.replace(' COLLATE "BINARY"', "")
    first = sql.index("(")
    last = sql.rindex(")")
    entries = tuple(sorted(_split_top_level(sql[first + 1 : last])))
    options = tuple(sorted(o.strip() for o in sql[last + 1 :].split(",") if o.strip()))
    return entries, options


def sqlite_catalog(engine: sa.Engine) -> dict[str, Any]:
    """Tables (columns, constraints, options), indexes and triggers of a SQLite schema, without
    ``alembic_version`` and the internal tables. Order-insensitive, text-exact otherwise."""
    catalog: dict[str, Any] = {}
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND tbl_name <> 'alembic_version' AND sql IS NOT NULL"
        ).all()
        conn.rollback()
    for kind, name, table, sql in rows:
        key = f"{kind}:{name}"
        catalog[key] = (table, _table_signature(sql)) if kind == "table" else (table, _squash(sql))
    return catalog


def postgres_catalog(engine: sa.Engine) -> dict[str, Any]:
    queries = {
        "columns": (
            "SELECT c.relname || '.' || a.attname, format_type(a.atttypid, a.atttypmod), "
            "a.attnotnull::text, COALESCE((SELECT collname FROM pg_collation WHERE oid = "
            "a.attcollation), ''), COALESCE(pg_get_expr(d.adbin, d.adrelid), '') "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind = 'r' "
            "AND c.relname <> 'alembic_version' AND a.attnum > 0 AND NOT a.attisdropped"
        ),
        "constraints": (
            "SELECT c.conrelid::regclass::text || '.' || c.conname, c.contype::text, "
            "pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "WHERE c.connamespace = current_schema()::regnamespace "
            "AND c.conrelid::regclass::text <> 'alembic_version' "
            "AND c.contype IN ('c', 'f', 'p', 'u')"
        ),
        "indexes": (
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename <> 'alembic_version' AND indexname NOT LIKE '%_pkey'"
        ),
        "triggers": (
            "SELECT c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal"
        ),
        "functions": (
            "SELECT proname, pg_get_functiondef(oid) FROM pg_proc "
            "WHERE pronamespace = current_schema()::regnamespace AND proname LIKE 'tracer_%'"
        ),
    }
    catalog: dict[str, Any] = {}
    with engine.connect() as conn:
        for group, query in queries.items():
            for row in conn.exec_driver_sql(query).all():
                catalog[f"{group}:{row[0]}"] = tuple(_squash(str(v)) for v in row[1:])
        conn.rollback()
    return catalog


def catalog(engine: sa.Engine) -> dict[str, Any]:
    return sqlite_catalog(engine) if engine.dialect.name == "sqlite" else postgres_catalog(engine)


def catalog_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    """Readable differences (object names only for a one-sided object; both values for a changed
    one). Empty when the catalogs are equal."""
    found = [f"only in first: {k}" for k in sorted(set(left) - set(right))]
    found += [f"only in second: {k}" for k in sorted(set(right) - set(left))]
    found += [
        f"differs: {k}: {left[k]!r} != {right[k]!r}"
        for k in sorted(set(left) & set(right))
        if left[k] != right[k]
    ]
    return found


def user_tables(engine: sa.Engine) -> list[str]:
    """Names of the tables in the database, ``alembic_version`` excluded."""
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            rows = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).all()
        else:
            rows = conn.exec_driver_sql(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            ).all()
        conn.rollback()
    return sorted(r[0] for r in rows if r[0] != "alembic_version")


def trigger_catalog(engine: sa.Engine) -> dict[str, tuple[str, str]]:
    """trigger name -> (table, 'update' | 'delete') for the user triggers of the schema; on
    Postgres the function and the exact row/before/event bits are checked here."""
    found: dict[str, tuple[str, str]] = {}
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            for name, table, sql in conn.exec_driver_sql(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
            ).all():
                match = re.match(r"CREATE TRIGGER \"?\w+\"? BEFORE (UPDATE|DELETE) ON ", sql)
                assert match, name
                assert "FOR EACH ROW" in sql, name
                found[name] = (table, match.group(1).lower())
        else:
            for name, table, bits, function in conn.exec_driver_sql(
                "SELECT t.tgname, c.relname, t.tgtype, p.proname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE NOT t.tgisinternal"
            ).all():
                assert (bits, function) in (
                    (19, "tracer_reject_update"),
                    (11, "tracer_reject_delete"),
                ), (name, bits, function)
                found[name] = (table, "update" if bits == 19 else "delete")
        conn.rollback()
    return found


def expected_trigger_catalog(engine: sa.Engine) -> dict[str, tuple[str, str]]:
    """The 37 update triggers and the one delete trigger of ``deletion_audit`` (ADR 0020 D7)."""
    evaldb = importlib.import_module("tracer_evaldb")
    expected = {f"trg_{name}_no_update": (name, "update") for name in evaldb.metadata.tables}
    expected["trg_deletion_audit_no_delete"] = ("deletion_audit", "delete")
    return expected


def raw_dump(engine: sa.Engine) -> dict[str, list[str]]:
    """Every row of every table (``SELECT *``, so columns added by a test revision are in), as
    sorted ``repr`` strings: equal dumps are equal databases."""
    dump: dict[str, list[str]] = {}
    with engine.connect() as conn:
        for table in user_tables(engine):
            result = conn.exec_driver_sql(f'SELECT * FROM "{table}"')  # noqa: S608
            names = list(result.keys())
            dump[table] = sorted(
                repr([(n, _plain(v)) for n, v in zip(names, row, strict=True)]) for row in result
            )
        conn.rollback()
    return dump


def _plain(value: Any) -> Any:
    return bytes(value) if isinstance(value, memoryview) else value


def row_counts(engine: sa.Engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in user_tables(engine):
            counts[table] = int(
                conn.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar_one()  # noqa: S608
            )
        conn.rollback()
    return counts


def revision_in_database(engine: sa.Engine) -> str | None:
    """The row of ``alembic_version`` read with plain SQL (not through the package)."""
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            present = conn.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name = 'alembic_version'"
            ).scalar_one()
        else:
            present = conn.exec_driver_sql(
                "SELECT count(*) FROM pg_tables WHERE tablename = 'alembic_version' "
                "AND schemaname = current_schema()"
            ).scalar_one()
        found = None
        if present:
            rows = conn.exec_driver_sql("SELECT version_num FROM alembic_version").all()
            assert len(rows) <= 1
            found = rows[0][0] if rows else None
        conn.rollback()
    return found if found is None else str(found)


# ============================================================ test-only revisions (writers)

_HEADER = '''\
"""Test-only revision (never shipped)."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from tracer_evaldb.migrations import helpers

revision = {revision!r}
down_revision = {down!r}
branch_labels = None
depends_on = None
'''


def write_revision(directory: Path, revision: str, down: str, body: str) -> Path:
    """Write ``<directory>/<revision>_test.py`` with the common header and ``body`` (the
    ``DOWNGRADE_DROPS``, ``upgrade`` and ``downgrade`` definitions)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{revision}_test.py"
    path.write_text(_HEADER.format(revision=revision, down=down) + dedent(body), encoding="utf-8")
    return path


def add_column_revision(
    directory: Path,
    revision: str,
    down: str,
    table: str,
    *,
    column: str = "note",
    drops: bool = True,
) -> Path:
    """A revision that adds a nullable TEXT column with ``helpers.batch_alter_table``; on SQLite it
    asks for ``recreate="always"``, so the table is rebuilt (create, copy, drop, rename)."""
    declared = f'("{table}.{column}",)' if drops else "()"
    return write_revision(
        directory,
        revision,
        down,
        f"""
        DOWNGRADE_DROPS = {declared}


        def _recreate() -> str:
            # SQLite: rebuild the table whatever the change; Postgres alters it in place
            return "always" if op.get_bind().dialect.name == "sqlite" else "auto"


        def upgrade() -> None:
            with helpers.batch_alter_table({table!r}, recreate=_recreate()) as batch:
                batch.add_column(sa.Column({column!r}, sa.Text(), nullable=True))


        def downgrade() -> None:
            with helpers.batch_alter_table({table!r}, recreate=_recreate()) as batch:
                batch.drop_column({column!r})
        """,
    )


def failing_revision(directory: Path, revision: str, down: str) -> Path:
    """Creates a table, inserts a row into it, then raises ``RuntimeError`` (after the DDL)."""
    return write_revision(
        directory,
        revision,
        down,
        """
        DOWNGRADE_DROPS = ()


        def upgrade() -> None:
            op.execute("CREATE TABLE scratch_marker (x INTEGER)")
            op.execute("INSERT INTO scratch_marker VALUES (1)")
            raise RuntimeError("test-only failure after the DDL")


        def downgrade() -> None:
            op.execute("DROP TABLE scratch_marker")
        """,
    )


ORPHAN_RUN = "0191b2c47d3e7a1f8b2c4d5e6f708192"  # a UUID-shaped hex with version nibble 7


def orphan_row_revision(directory: Path, revision: str, down: str) -> Path:
    """Inserts an ``external_ref_index`` row whose run does not exist (a dangling foreign key)."""
    return write_revision(
        directory,
        revision,
        down,
        f"""
        DOWNGRADE_DROPS = ()


        def upgrade() -> None:
            op.execute(
                "INSERT INTO external_ref_index (entity_kind, entity_id, key_sha256, run_id) "
                "VALUES ('run', X'{ORPHAN_RUN}', X'{"ab" * 32}', X'{ORPHAN_RUN}')"
            )


        def downgrade() -> None:
            op.execute("DELETE FROM external_ref_index")
        """,  # noqa: S608
    )


def rewriting_revision(directory: Path, revision: str, down: str, *, fail: bool = False) -> Path:
    """A revision that rewrites a ``project`` row inside ``helpers.rewriting`` only (no batch
    alter, no data-migration helper): the triggers of the table must be out of the way for the
    UPDATE and back afterwards. With ``fail`` it raises inside the block."""
    raising = "raise RuntimeError('test-only failure inside the block')" if fail else "pass"
    return write_revision(
        directory,
        revision,
        down,
        f"""
        DOWNGRADE_DROPS = ()


        def upgrade() -> None:
            with helpers.rewriting("project"):
                op.execute("UPDATE project SET name = 'rewritten'")
                {raising}


        def downgrade() -> None:
            with helpers.rewriting("project"):
                op.execute("UPDATE project SET name = 'original'")
        """,  # noqa: S608
    )


def fake_data_revision(
    directory: Path, revision: str, down: str, mode: str = "plain", table: str = "persona"
) -> Path:
    """The test-only fake 0.0.2 data migration (ADR 0021 rules 10 to 15); ``mode`` and ``table``
    select the double of :func:`fake_functions`."""
    return write_revision(
        directory,
        revision,
        down,
        f"""
        from evaldb_migration_support import fake_functions

        DOWNGRADE_DROPS = ()


        def _accept(_table: str, _text: str) -> None:
            return None


        def upgrade() -> None:
            forward, backward, validate = fake_functions({mode!r}, {table!r})
            helpers.rewrite_documents(
                "0.0.1", "0.0.2", forward=forward, backward=backward, validate=validate
            )


        def downgrade() -> None:
            forward, backward, _validate = fake_functions({mode!r}, {table!r})
            helpers.rewrite_documents(
                "0.0.2", "0.0.1", forward=backward, backward=forward, validate=_accept
            )
        """,
    )


# ====================================================== doubles of a data migration (text level)


@dataclass
class _Frame:
    array: bool
    in_metadata: bool
    expect_key: bool = False
    key: str | None = None


def rewrite_schema_version(text: str, old: str, new: str) -> str:
    """Replace the value of every ``"schema_version": "<old>"`` member that is outside a
    ``metadata`` object, and touch no other byte of the JSON text. A scanner, not a parser: it
    never re-serializes (float spelling, escapes and key order stay exactly as written)."""
    out: list[str] = []
    last = 0
    stack: list[_Frame] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        top = stack[-1] if stack else None
        if char == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            raw = text[i + 1 : j]
            if top is not None and not top.array and top.expect_key:
                top.key, top.expect_key = raw, False
            elif (
                top is not None
                and not top.array
                and top.key == "schema_version"
                and raw == old
                and not top.in_metadata
            ):
                out.append(text[last : i + 1])
                out.append(new)
                last = j
            i = j + 1
            continue
        if char in "{[":
            inside = top is not None and (
                top.in_metadata or (not top.array and top.key == "metadata")
            )
            stack.append(_Frame(array=char == "[", in_metadata=inside, expect_key=char == "{"))
        elif char in "}]":
            stack.pop()
        elif char == "," and top is not None and not top.array:
            top.expect_key = True
        i += 1
    out.append(text[last:])
    return "".join(out)


def _flip_hex(char: str) -> str:
    return format(int(char, 16) ^ 1, "x")


_DIGEST_VALUE = re.compile(r'("content_digest":\{[^{}]*?"value":")([0-9a-f])')
_CREATED_AT = re.compile(r'("created_at":"[0-9TZ:.-]*?)([0-9])(Z")')


def _flip_digest_member(text: str) -> str:
    """Flip the first hex digit of the (first) ``content_digest.value`` member: an involution."""
    return _DIGEST_VALUE.sub(lambda m: m.group(1) + _flip_hex(m.group(2)), text, count=1)


def _flip_created_at(text: str) -> str:
    """Flip the last digit of the microseconds of the (first) ``created_at``: an involution that
    changes the digest domain and leaves the document valid."""
    return _CREATED_AT.sub(lambda m: m.group(1) + str(int(m.group(2)) ^ 1) + m.group(3), text, 1)


def table_of_entity() -> dict[str, str]:
    evaldb = importlib.import_module("tracer_evaldb")
    return {table.name: name for name, table in evaldb.tables.ENTITY_TABLES.items()}


DIGESTED_TABLES = (
    "agent_target",
    "persona",
    "test_profile",
    "evaluator",
    "metric_definition",
    "metric_binding",
    "rubric",
    "pricing_catalog",
    "dataset",
    "config_snapshot",
)

FAKE_MODES = (
    "plain",
    "lossy",
    "skip_one",
    "digest_member",
    "digest_member_lax",
    "digest_content",
    "validate_boom",
)


class ValidateBoom(Exception):
    """Raised by the ``validate_boom`` double; never raised by the package."""


def fake_functions(
    mode: str, table: str
) -> tuple[Callable[[str, str], str], Callable[[str, str], str], Callable[[str, str], None]]:
    """(forward, backward, validate) of the fake 0.0.1 to 0.0.2 migration.

    * plain: rewrite the version at every entity level, byte-exact both ways;
    * lossy: a forward that no backward can undo (IrreversibleMigration);
    * skip_one: the rows of ``table`` are not rewritten (a mixed state would result);
    * digest_member: the ``content_digest`` member of ``table`` changes (reversibly);
    * digest_member_lax: as digest_member, but ``validate`` accepts everything, so only the
      check of the ``content_digest`` member itself can refuse it;
    * digest_content: the digested content of ``table`` changes and the member does not, so only
      the "models of the new version" (``validate``) can see it;
    * validate_boom: ``validate`` raises ``ValidateBoom`` for ``table``.
    """
    names = table_of_entity()
    lax = mode.endswith("_lax")
    mode = mode.removesuffix("_lax")

    def up(_table: str, text: str) -> str:
        return rewrite_schema_version(text, SCHEMA_V1, SCHEMA_V2)

    def down(_table: str, text: str) -> str:
        return rewrite_schema_version(text, SCHEMA_V2, SCHEMA_V1)

    def forward(name: str, text: str) -> str:
        done = up(name, text)
        if mode == "lossy":
            return done.replace("a", "b")
        if mode == "skip_one" and name == table:
            return text
        if mode == "digest_member" and name == table:
            return _flip_digest_member(done)
        if mode == "digest_content" and name == table:
            return _flip_created_at(done)
        return done

    def backward(name: str, text: str) -> str:
        done = down(name, text)
        if mode == "lossy":
            return done.replace("b", "a")
        if mode == "digest_member" and name == table:
            return _flip_digest_member(done)
        if mode == "digest_content" and name == table:
            return _flip_created_at(done)
        return done

    def validate(name: str, text: str) -> None:
        """What the models of the new version would do: load the document (the content digest of
        a versioned entity or a snapshot is verified on load). The installed models read 0.0.1,
        so the version is put back first."""
        if mode == "validate_boom" and name == table:
            raise ValidateBoom
        if lax or name not in DIGESTED_TABLES:
            return
        ev_loader.load_model(entity_models()[names[name]], down(name, text))

    return forward, backward, validate


# =========================================================================== populated stores


@dataclass
class Populated:
    bundle: Any
    bundle_doc: dict[str, Any]
    export: Any
    metadata_used: Mapping[str, Any] | None
    ids: dict[str, Any] = field(default_factory=dict)


def custom_metadata_record(
    record: Mapping[str, Any], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """The record document with this ``metadata`` and the flags the model computes for it."""
    from tracer_evalschema import base as ev_base

    flags = ev_base.compute_metadata_flags(dict(metadata))
    return {
        **json.loads(json.dumps(record)),
        "metadata": dict(metadata),
        "metadata_flags": [f.model_dump(mode="json") for f in flags],
    }


ADVERSARIAL_METADATA: Mapping[str, Any] = {
    "schema_version": SCHEMA_V1,
    "quoted": '"schema_version":"0.0.1"',
    "nested": {"schema_version": SCHEMA_V1, "metadata": {"schema_version": SCHEMA_V1}},
}


def populate(engine: sa.Engine, *, metadata: Mapping[str, Any] | None = None) -> Populated:
    """Fill every entity table through the repository: a T1 run of a human caller with a test
    profile (so every run-owned table, the snapshot, its definitions, the catalog and the profile
    have rows), an agent target, a dataset, a dataset item and an internal export of the run, and
    one deletion audit row. ``metadata``, when given, is put on the turns, the agent target and
    the dataset (user ``metadata`` that mentions ``schema_version`` must survive a migration)."""
    from evaldb_support import redigest, with_metadata  # type: ignore[import-not-found,unused-ignore]

    evaldb = importlib.import_module("tracer_evaldb")
    b7 = support("_support_b7")
    built = b7.world(tier="T1", origin="human", with_profile=True, profile_digest="plain")
    doc = built.doc
    profile = built.parts["profile"]  # the profile the snapshot names; the bundle lists it too
    doc["test_profiles"] = [profile.model_dump(mode="json")]
    if metadata is not None:
        doc["turns"] = [custom_metadata_record(turn, metadata) for turn in doc["turns"]]
    bundle = load_run_bundle_doc(doc)
    store_bundle(engine, evaldb, bundle)
    for entity in ("AgentTarget", "Dataset"):
        golden = load_golden(entity, "minimal")
        if metadata is not None:
            body = redigest(entity, with_metadata(golden.model_dump(mode="json"), dict(metadata)))
            golden = type(golden).model_validate(body)
        evaldb.store_definition(engine, golden)
    evaldb.store_dataset_item(engine, load_golden("DatasetItem", "minimal"))
    export = b7.load_export(b7.export_doc("internal", [doc]))
    evaldb.store_export(engine, export, expected_purpose="internal")
    with engine.begin() as conn:
        Rows(evaldb.metadata).insert(conn, "deletion_audit", actor="populate")
    return Populated(bundle, doc, export, metadata, {"profile": profile})


# ======================================================================= engine instrumentation


@contextlib.contextmanager
def fault_on_statement(engine: sa.Engine, pattern: str, *, nth: int = 1) -> Iterator[list[str]]:
    """Raise ``Boom`` from the ``nth`` statement that matches ``pattern`` (a regex searched in the
    statement text, case-insensitively). Yields the list of statements that matched."""
    compiled = re.compile(pattern, re.IGNORECASE)
    seen: list[str] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if compiled.search(statement):
            seen.append(statement)
            if len(seen) == nth:
                raise Boom

    event.listen(engine, "before_cursor_execute", listener)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", listener)


@contextlib.contextmanager
def pause_on_statement(
    engine: sa.Engine,
    matches: Callable[[str], bool],
    reached: threading.Event,
    release: threading.Event,
) -> Iterator[None]:
    """Block the executing thread at the first statement for which ``matches`` is true (before it
    is sent), until ``release`` is set; ``reached`` is set first."""
    state = {"done": False}

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if not state["done"] and matches(statement):
            state["done"] = True
            reached.set()
            assert release.wait(timeout=60), "the test never released the paused thread"

    event.listen(engine, "before_cursor_execute", listener)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", listener)


def wait_reached(
    reached: threading.Event,
    thread: threading.Thread,
    what: str,
    box: list[Any] | None = None,
    seconds: float = 60.0,
) -> None:
    """Wait until a paused thread has reached its pause; fail at once, with what the thread ended
    with, when it ended first (a thread that raised before the pause would otherwise be waited
    for in vain until the timeout)."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if reached.wait(0.1):
            return
        if not thread.is_alive():
            ended = "" if not box else f": {box[0]!r}"
            raise AssertionError(f"{what} (the thread ended first{ended})")
    raise AssertionError(f"{what} (no pause after {seconds:.0f} seconds)")


def is_delete(statement: str) -> bool:
    return re.match(r"\s*DELETE\b", statement, re.IGNORECASE) is not None


def is_not_begin(statement: str) -> bool:
    return re.match(r"\s*BEGIN\b", statement, re.IGNORECASE) is None


def wait_until_blocked(monitor: sa.Engine, thread: threading.Thread, seconds: float = 20.0) -> bool:
    """Postgres: True once some session waits for a lock (``wait_event_type = 'Lock'``) while
    ``thread`` is still running; False when the thread ended without ever waiting."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False
        with monitor.connect() as conn:
            waiting = conn.exec_driver_sql(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                "AND datname = current_database()"
            ).scalar_one()
            conn.rollback()
        if waiting:
            return True
        time.sleep(0.05)
    return thread.is_alive()
