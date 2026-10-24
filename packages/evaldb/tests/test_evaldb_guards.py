# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch F, P2-24: guard tests of the evaldb security audit.

Contract: docs/phase2/api-surface-batch-f.md sections 1 to 3. Three promises, each a regression
guard on the code that exists:

* no SQL text is built from pieces (``test_no_string_built_sql``): ruff S608 is clean, and an AST
  reader finds every call that hands SQL text to the database; the few sites that are not a plain
  literal are listed here one by one, with their reason, and nothing else is allowed;
* no column of the store holds a secret (``test_store_has_no_secret_columns``);
* an error never contains the text of a document or a bound parameter value
  (``test_store_errors_hide_input``).

The tests that take ``db`` run on SQLite and, with ``-m postgres``, on Postgres.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import importlib
import json
import re
import subprocess
import sys
import traceback
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_guard_support import (  # type: ignore[import-not-found,unused-ignore]
    CONSTRUCT,
    DYNAMIC,
    LITERAL,
    OPAQUE,
    Site,
    scan_source,
)
from evaldb_migration_support import populate  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    load_run_bundle_doc,
    new_id,
    project_doc,
    run_bundle_doc,
    store_project_of,
    with_marker_metadata,
)
from evaldb_support import Db, load_golden  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import errors as ev_errors
from tracer_evalschema import ids as ev_ids


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
EVALDB = ROOT / "packages" / "evaldb"
SRC = EVALDB / "src" / "tracer_evaldb"
VERSIONS = SRC / "migrations" / "versions"

db = _support.db

# ================================================================================ P2-24 SQL text

# Attribute expressions that a revision may interpolate: module constants of literal text in
# ``tracer_evaldb.triggers`` (checked below by reading that module).
TRIGGER_CONSTANTS = frozenset(
    {"triggers.UPDATE_TRIGGER_FUNCTION", "triggers.DELETE_TRIGGER_FUNCTION"}
)

# Every call in src that hands SQL text to the database and is not a plain literal (or a Core
# construct), keyed by (file below src/tracer_evaldb, enclosing function, call, nth such call in
# the function). Value: (kind, interpolated pieces, reason). The set is exact in both directions:
# a new site fails the test until a reviewer adds it here with a reason, and a site that was
# removed fails it until the row goes (the audit table P2-24 is this table).
REVIEWED: dict[tuple[str, str, str, int], tuple[str, tuple[str, ...], str]] = {
    ("engine.py", "_sqlite_engine._configure", "execute", 1): (
        DYNAMIC,
        ("busy_timeout_ms",),
        "PRAGMA busy_timeout takes no bound parameter; busy_timeout_ms is an int >= 0 checked "
        "by create_store_engine before this runs",
    ),
    ("engine.py", "_sqlite_engine._begin", "exec_driver_sql", 0): (
        OPAQUE,
        (),
        "the BEGIN statement comes from an execution option that only deletion.py sets, to "
        "BEGIN IMMEDIATE; default BEGIN",
    ),
    ("migrate.py", "_lost_tables", "exec_driver_sql", 0): (
        DYNAMIC,
        ("table", "where"),
        "identifiers only: table and column come from the DOWNGRADE_DROPS tuple of a revision "
        "file, written in the quoted form; no stored value",
    ),
    ("migrations/helpers.py", "_drop_triggers", "execute", 0): (
        OPAQUE,
        (),
        "statements of triggers.drop_statements, built from table names of the metadata",
    ),
    ("migrations/helpers.py", "_create_triggers", "execute", 0): (
        OPAQUE,
        (),
        "statements of triggers.create_statements, built from table names of the metadata",
    ),
    ("migrations/helpers.py", "_plan", "text", 0): (
        DYNAMIC,
        ("columns", "table"),
        "identifiers only: key column names and entity table names of the metadata; the rows "
        "read are never interpolated",
    ),
    ("migrations/helpers.py", "rewrite_documents", "text", 0): (
        DYNAMIC,
        ("table", "where"),
        "identifiers and named bind parameters only: the UPDATE text holds :doc, :sha, :version "
        "and :k_<column>; every value is bound",
    ),
    ("migrations/helpers.py", "rewrite_documents", "create_check_constraint", 0): (
        DYNAMIC,
        ("new_version",),
        "DDL cannot bind a parameter; new_version is the schema version string chosen by the "
        "author of a data migration revision, never stored data",
    ),
    ("migrations/versions/0001_initial_schema.py", "upgrade", "execute", 0): (
        OPAQUE,
        (),
        "statements of triggers.function_statements, literal text with fixed names",
    ),
    ("migrations/versions/0001_initial_schema.py", "upgrade", "execute", 1): (
        OPAQUE,
        (),
        "a loop over the DDL dict of this revision, which is literal text written out; it reads "
        "as opaque only because the name statement is bound by three loops in this function",
    ),
    ("migrations/versions/0001_initial_schema.py", "upgrade", "execute", 2): (
        OPAQUE,
        (),
        "statements of triggers.create_statements for the tables of this revision",
    ),
    ("migrations/versions/0001_initial_schema.py", "downgrade", "execute", 0): (
        DYNAMIC,
        ("table",),
        "DROP TABLE of a name from the TABLES tuple of this revision",
    ),
    ("migrations/versions/0001_initial_schema.py", "downgrade", "execute", 1): (
        DYNAMIC,
        ("triggers.UPDATE_TRIGGER_FUNCTION",),
        "DROP FUNCTION of a module constant of literal text",
    ),
    ("migrations/versions/0001_initial_schema.py", "downgrade", "execute", 2): (
        DYNAMIC,
        ("triggers.DELETE_TRIGGER_FUNCTION",),
        "DROP FUNCTION of a module constant of literal text",
    ),
    ("migrations/versions/0002_access_pattern_indexes.py", "upgrade", "execute", 0): (
        DYNAMIC,
        ("name", "table", "columns"),
        "CREATE INDEX of the literal tuples _INDEXES of this revision",
    ),
    ("migrations/versions/0002_access_pattern_indexes.py", "downgrade", "execute", 0): (
        DYNAMIC,
        ("name",),
        "DROP INDEX of the literal names of _INDEXES",
    ),
    ("triggers.py", "install", "DDL", 0): (
        OPAQUE,
        (),
        "DDL objects of the statements of function_statements: literal text, fixed names",
    ),
    ("triggers.py", "install", "DDL", 1): (
        OPAQUE,
        (),
        "DDL objects of create_statements: names of the metadata tables, fixed words",
    ),
}
# The sites that are only read as opaque because the reader cannot follow a loop over a call.
# Everything the reader calls DYNAMIC inside a revision file must also draw every piece from a
# literal of that file (or from TRIGGER_CONSTANTS).

# Files in src whose SQL is built only by SQLAlchemy Core: no site at all may appear in them.
CORE_ONLY_FILES = (
    "repository.py",
    "codecs.py",
    "deletion.py",
    "verify.py",
    "versioning.py",
    "columns.py",
    "tables.py",
    "schema.py",
    "errors.py",
    "__init__.py",
)


def _source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _all_sites() -> dict[tuple[str, str, str, int], Site]:
    found: dict[tuple[str, str, str, int], Site] = {}
    for path in _source_files():
        for site in scan_source(path.read_text(encoding="utf-8"), TRIGGER_CONSTANTS):
            if site.kind in (LITERAL, CONSTRUCT):
                continue
            found[(_rel(path), site.qualname, site.sink, site.ordinal)] = site
    return found


def _ruff(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (our own interpreter, fixed arguments)
        [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def test_no_string_built_sql() -> None:
    """P2-24: SQL is built only from bound parameters.

    1. ``ruff check --select S608`` over ``packages/evaldb`` is clean, and the only
       ``noqa: S608`` comments in src are the three of the reviewed identifier-only sites.
    2. An AST reader finds every call in src that hands text to the database (``text``,
       ``exec_driver_sql``, ``execute``, ``executemany``, ``executescript``, ``DDL``, the
       condition of ``create_check_constraint``). The calls that are not a plain literal (or a
       Core construct) are exactly the REVIEWED table: nothing new, nothing stale.
    3. The Core-only modules (repository, codecs, deletion, verify, versioning, columns, tables)
       have no such call at all.
    4. In a revision file every piece of a built statement is drawn from a literal of that file
       (or a constant of ``triggers``): the DDL of a revision is literal text.
    """
    done = _ruff("--select", "S608", "--output-format", "concise", "packages/evaldb")
    assert done.returncode == 0, done.stdout + done.stderr

    # the noqa census: exactly the identifier-only sites
    census: dict[str, list[int]] = {}
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"noqa:[^#]*\bS608\b", line):
                census.setdefault(_rel(path), []).append(number)
    assert {name: len(lines) for name, lines in census.items()} == {
        "migrate.py": 1,
        "migrations/helpers.py": 2,
    }, census

    found = _all_sites()
    actual = {key: (site.kind, site.pieces) for key, site in found.items()}
    expected = {key: (kind, pieces) for key, (kind, pieces, _reason) in REVIEWED.items()}
    new = sorted(set(actual) - set(expected))
    stale = sorted(set(expected) - set(actual))
    assert not new, f"a call hands SQL text to the database that is not a literal: {new}"
    assert not stale, f"REVIEWED lists a call that no longer exists: {stale}"
    assert actual == expected, "a reviewed site changed what it interpolates"
    assert all(reason.strip() for _kind, _pieces, reason in REVIEWED.values())

    core_only = {key for key in found if key[0] in CORE_ONLY_FILES}
    assert core_only == set(), core_only
    for name in CORE_ONLY_FILES:
        assert (SRC / name).is_file(), name

    # the constants a revision may interpolate are literal text in triggers.py
    tree = ast.parse((SRC / "triggers.py").read_text(encoding="utf-8"))
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for expression in TRIGGER_CONSTANTS:
        name = expression.removeprefix("triggers.")
        assert re.fullmatch(r"[a-z_]+", constants.get(name, "")), name

    revisions = sorted(p for p in VERSIONS.glob("*.py") if p.name != "__init__.py")
    assert [p.name for p in revisions] == [
        "0001_initial_schema.py",
        "0002_access_pattern_indexes.py",
    ]
    for path in revisions:
        sites = scan_source(path.read_text(encoding="utf-8"), TRIGGER_CONSTANTS)
        for site in sites:
            if site.kind == DYNAMIC:
                assert site.pieces_literal, (path.name, site)
            assert site.kind != OPAQUE or site.qualname == "upgrade", (path.name, site)


def test_the_sql_reader_tells_literal_text_from_built_text() -> None:
    """The reader of ``test_no_string_built_sql`` has teeth: each way of building a statement from
    pieces is found, and each plain form is left alone."""

    def kinds(source: str) -> list[tuple[str, str]]:
        return [(site.sink, site.kind) for site in scan_source(source)]

    built = {
        "fstring": 'text(f"SELECT * FROM t WHERE a = {x}")',
        "concat": 'text("SELECT * FROM t WHERE a = " + x)',
        "concat_right": 'text(x + " FROM t")',
        "percent": 'text("SELECT * FROM t WHERE a = %s" % x)',
        "format": 'text("SELECT * FROM t WHERE a = {}".format(x))',
        "join": 'text("SELECT " + ", ".join(cols))',
        "via_name": 'q = f"SELECT {x}"\ntext(q)',
        "via_augmented_name": 'q = "SELECT "\nq += x\ntext(q)',
        "driver": 'conn.exec_driver_sql(f"PRAGMA table_info({t})")',
        "cursor": 'cursor.execute("DELETE FROM t WHERE a = " + a)',
        "executemany": 'cursor.executemany(f"INSERT INTO t VALUES ({v})", rows)',
        "ddl": 'DDL("CREATE TABLE " + name)',
        "op": 'op.execute(f"DROP TABLE {t}")',
        "check": 'batch.create_check_constraint("n", f"v = {x}")',
        "nested_function": "def f(x):\n    def g(y):\n        return text(f'{y}')\n    return g",
    }
    for label, source in built.items():
        found = kinds(source)
        assert found, label
        assert all(kind == DYNAMIC for _sink, kind in found), (label, found)

    plain = {
        "literal": 'text("SELECT 1 FROM t WHERE a = :a")',
        "implicit_concatenation": 'text("SELECT 1 " "FROM t")',
        "name_of_literal": 'q = "SELECT 1"\ntext(q)',
        "core_construct": "conn.execute(sa.select(t.c.a))",
        "core_name": "stmt = sa.select(t.c.a)\nconn.execute(stmt)",
        "literal_loop": 'S = ("A", "B")\nfor s in S:\n    op.execute(s)',
        "literal_dict_loop": 'D = {"x": ("A", "B")}\nfor s in D[d]:\n    op.execute(s)',
        "reversed_literal": 'T = ("a", "b")\nfor t in reversed(T):\n    op.execute("DROP")',
        "unrelated_call": "print(f'{x}')",
        "unrelated_method": "obj.run(f'{x}')",
    }
    for label, source in plain.items():
        found = kinds(source)
        assert all(kind in (LITERAL, CONSTRUCT) for _sink, kind in found), (label, found)

    opaque = {
        "parameter": "def f(statement):\n    conn.exec_driver_sql(statement)",
        "call_result_name": "s = options.get('x')\nconn.exec_driver_sql(s)",
        "loop_over_call": "for s in make():\n    op.execute(s)",
        "unbound": "conn.exec_driver_sql(statement)",
        "attribute": "conn.exec_driver_sql(self.sql)",
    }
    for label, source in opaque.items():
        found = kinds(source)
        assert found, label
        assert all(kind == OPAQUE for _sink, kind in found), (label, found)

    # pieces and the literal-draw flag
    (site,) = scan_source('T = ("a",)\nfor t in T:\n    op.execute(f"DROP TABLE {t}")')
    assert (site.kind, site.pieces, site.pieces_literal) == (DYNAMIC, ("t",), True)
    (site,) = scan_source('def f(t):\n    op.execute(f"DROP TABLE {t}")')
    assert (site.kind, site.pieces, site.pieces_literal) == (DYNAMIC, ("t",), False)
    (site,) = scan_source('op.execute(f"DROP FUNCTION {triggers.F}()")', frozenset({"triggers.F"}))
    assert site.pieces_literal is True
    (site,) = scan_source('op.execute(f"DROP FUNCTION {triggers.F}()")')
    assert site.pieces_literal is False
    # the ordinal counts per function and call
    sites = scan_source('def f():\n    text(f"{a}")\n    text(f"{b}")\n    op.execute(f"{c}")')
    assert [(s.qualname, s.sink, s.ordinal) for s in sites] == [
        ("f", "text", 0),
        ("f", "text", 1),
        ("f", "execute", 0),
    ]


def test_ruff_s608_flags_a_string_built_query(tmp_path: Path) -> None:
    """The S608 half of ``test_no_string_built_sql`` has teeth: the same invocation fails on a file
    that builds a query from a value."""
    bad = tmp_path / "bad.py"
    bad.write_text('def q(x):\n    return f"SELECT * FROM t WHERE a = {x}"\n', encoding="ascii")
    done = _ruff("--select", "S608", "--output-format", "concise", str(bad))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S608" in done.stdout
    good = tmp_path / "good.py"
    good.write_text('def q():\n    return "SELECT * FROM t WHERE a = :a"\n', encoding="ascii")
    assert _ruff("--select", "S608", str(good)).returncode == 0


def _captured(engine: sa.Engine) -> Any:
    @contextlib.contextmanager
    def capture() -> Iterator[list[tuple[str, Any]]]:
        seen: list[tuple[str, Any]] = []

        def listener(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
        ) -> None:
            seen.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", listener)
        try:
            yield seen
        finally:
            event.remove(engine, "before_cursor_execute", listener)

    return capture()


def test_sent_statements_hold_no_stored_value(db: Db, evaldb: ModuleType) -> None:
    """The runtime half of the audit: a stored value (a name, a metadata text) reaches the database
    in the parameters of a statement and never in the statement text. Writes, reads, a lookup,
    a verification and a deletion all run with the markers present."""
    name_marker = "SENT-STATEMENT-NAME-4f7a19"
    doc_marker = "SENT-STATEMENT-DOC-4f7a19"
    with _captured(db.engine) as seen:
        populated = populate(db.engine, metadata={"marker": doc_marker})
        project = project_doc(new_id("prj"))
        project["name"] = name_marker
        project["metadata"] = {"marker": doc_marker}
        entity = _support.load_entity("Project", json.dumps(project))
        evaldb.store_project(db.engine, entity)
        evaldb.load_project(db.engine, entity.id)
        evaldb.load_run_bundle(db.engine, populated.bundle.id)
        ref = ev_ids.ExternalRef.model_validate(
            {"system": "pipecat", "kind": "call_sid", "value": name_marker}
        )
        evaldb.find_by_external_ref(db.engine, "run", ref)
        evaldb.verify_database(db.engine)
        evaldb.delete_run(
            db.engine, populated.bundle.run.id, actor="guard", reason="operator_request"
        )
    assert seen, "nothing was captured"
    statements = "\n".join(statement for statement, _ in seen)
    for marker in (name_marker, doc_marker):
        assert marker not in statements
        assert marker.lower() not in statements.lower()
    # positive control: the markers did travel, as parameters
    parameters = repr([params for _, params in seen])
    assert name_marker in parameters
    assert doc_marker in parameters


# ======================================================================== P2-24 secret columns

# A column or table name is refused when one of its words is here (a word is a part between
# underscores, lower case).
SECRET_WORDS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "secret",
        "secrets",
        "token",
        "tokens",
        "credential",
        "credentials",
        "apikey",
        "bearer",
        "cookie",
        "cookies",
        "auth",
        "authorization",
        "private",
        "privkey",
        "kek",
        "dek",
        "hmac",
        "salt",
        "nonce",
        "cipher",
        "ciphertext",
        "plaintext",
        "keyring",
        "keystore",
        "pem",
    }
)
# Word pairs, joined by an underscore in the name, that are refused although each word alone is not.
SECRET_PAIRS = (
    "api_key",
    "access_key",
    "secret_key",
    "signing_key",
    "encryption_key",
    "master_key",
)
# The only columns whose name has the word "key": a metric's own key (an identifier of a
# definition, never key material) and the SHA-256 lookup hash of an external reference.
KEY_WORD_ALLOWED = frozenset({("metric_definition", "key"), ("external_ref_index", "key_sha256")})


def secret_name_findings(table: str, column: str | None) -> list[str]:
    """Why a table or column name is refused, as short reasons (empty when it is fine)."""
    name = table if column is None else column
    words = name.lower().split("_")
    reasons = [f"word {w}" for w in words if w in SECRET_WORDS]
    reasons += [f"pair {p}" for p in SECRET_PAIRS if p in name.lower()]
    if "key" in words and (table, column) not in KEY_WORD_ALLOWED:
        reasons.append("word key")
    return reasons


def _catalog_columns(db: Db) -> set[tuple[str, str]]:
    """(table, column) of every column the database itself reports, on either backend."""
    found: set[tuple[str, str]] = set()
    with db.engine.connect() as conn:
        if db.backend == "sqlite":
            tables = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).all()
            for (table,) in tables:
                for row in conn.exec_driver_sql(f'PRAGMA table_info("{table}")').all():
                    found.add((str(table), str(row[1])))
        else:
            rows = conn.exec_driver_sql(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema()"
            ).all()
            found = {(str(t), str(c)) for t, c in rows}
        conn.rollback()
    return found


def test_store_has_no_secret_columns(db: Db, evaldb: ModuleType) -> None:
    """P2-24: the store keeps no key, password, token or credential in a column. Every table and
    column name of the metadata, and of the schema the database reports, passes a word denylist
    (the word ``key`` only for the two allowed columns). The BYOK promise holds at rest: a
    provider configuration is a reference in a document, never key material."""
    refused = []
    for table in evaldb.metadata.sorted_tables:
        for reason in secret_name_findings(table.name, None):
            refused.append((table.name, None, reason))
        for column in table.columns:
            for reason in secret_name_findings(table.name, column.name):
                refused.append((table.name, column.name, reason))
    assert refused == []
    # the allowed columns exist (the allowance is not stale) and are what they say
    metadata_columns = {
        (t.name, c.name) for t in evaldb.metadata.tables.values() for c in t.columns
    }
    assert metadata_columns >= KEY_WORD_ALLOWED
    assert type(evaldb.metadata.tables["metric_definition"].c["key"].type).__name__ == "KeyCol"
    assert type(evaldb.metadata.tables["external_ref_index"].c["key_sha256"].type).__name__ == (
        "Sha256Col"
    )
    # the database holds the same columns as the metadata (no extra column the metadata hides),
    # plus the alembic version table when a migration ran (not here: create_all)
    reported = _catalog_columns(db)
    assert reported == metadata_columns
    assert [(t, c) for t, c in sorted(reported) if secret_name_findings(t, c)] == []
    # no table name is a secret word either, in the database
    assert [t for t, _c in reported if secret_name_findings(t, None)] == []


def test_the_secret_name_denylist_has_teeth() -> None:
    """The denylist of ``test_store_has_no_secret_columns`` refuses what it should and passes the
    real names that contain a near miss."""
    for column in (
        "password",
        "db_password",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "access_key_id",
        "token",
        "auth_token",
        "bearer",
        "credentials",
        "private_key",
        "kek",
        "hmac_key",
        "key_value",
        "key",
        "ciphertext",
        "Password",
    ):
        assert secret_name_findings("run", column), column
    for column in (
        "sha256",
        "doc_sha256",
        "content_digest_value",
        "actor",
        "purpose",
        "policy_ref",
        "human_subject",
        "turn_ref",
        "authored",  # a word that merely starts with a denylist word
        "tokenizer",
    ):
        assert secret_name_findings("run", column) == [], column
    # the word key is excepted for two columns of their own tables, and nowhere else
    assert secret_name_findings("run", "key")
    assert secret_name_findings("run", "key_sha256")
    assert secret_name_findings("metric_definition", "key_sha256")
    assert secret_name_findings("metric_definition", "key") == []
    assert secret_name_findings("external_ref_index", "key_sha256") == []
    assert secret_name_findings("secret_store", None)


# ===================================================================== P2-24 errors hide input

DOC_MARKER = "HIDE-INPUT-DOC-5b2e91"
NAME_MARKER = "HIDE-INPUT-NAME-5b2e91"
EDIT_MARKER = "HIDE-INPUT-EDIT-5b2e91"
NUMBER = 2**53 + 977  # a number that must not appear in the message of an out-of-range refusal
HUGE = 2**70 + 977
MARKERS = (DOC_MARKER, NAME_MARKER, EDIT_MARKER, str(NUMBER), str(HUGE))


def _chain(error: BaseException) -> list[BaseException]:
    """The exceptions Python itself shows with this one: the cause, and the context unless it is
    suppressed (``raise ... from None``)."""
    found: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and all(current is not seen for seen in found):
        found.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    return found


def _strings(value: Any, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, bytes | bytearray):
        return [bytes(value).decode("utf-8", "replace")]
    if isinstance(value, dict):
        return [
            s for k, v in value.items() for s in (*_strings(k, depth + 1), *_strings(v, depth + 1))
        ]
    if isinstance(value, list | tuple | set | frozenset):
        return [s for v in value for s in _strings(v, depth + 1)]
    if isinstance(value, int | float):
        return [str(value)]
    return [repr(value)]


def surface(error: BaseException) -> str:
    """All the text an error can show: its message, its representation, its arguments, its notes,
    the attributes of a store error, the original driver error of a database error, and the
    traceback text. SQLAlchemy keeps the bound parameters of a failed statement in the attribute
    ``params`` of its own errors (hidden from their text by ``hide_parameters``); that attribute is
    not read, and no store error carries a SQLAlchemy error in the chain Python shows."""
    parts: list[str] = []
    for item in _chain(error):
        parts += [str(item), repr(item), repr(item.args), *getattr(item, "__notes__", [])]
        if isinstance(item, sa.exc.SQLAlchemyError):
            orig = getattr(item, "orig", None)
            if orig is not None:
                parts += [str(orig), repr(orig), repr(orig.args)]
        else:
            parts += _strings(vars(item))
    parts.append("".join(traceback.format_exception(error)))
    return "\n".join(parts)


def leaks(text: str) -> list[str]:
    encoded = text.encode("utf-8", "replace")
    return [m for m in MARKERS if m in text or m.encode() in encoded]


@contextlib.contextmanager
def _triggers_off(db: Db, table: str) -> Iterator[None]:
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


def _violate(db: Db, kind: str) -> sa.exc.DBAPIError:
    """A constraint violation of each kind, with the markers in the row (document and a text
    column) and so in the bound parameters."""
    try:
        with db.engine.begin() as conn:
            if kind == "check":
                db.rows.insert(
                    conn, "project", schema_version="0.0.2", doc=DOC_MARKER, name=NAME_MARKER
                )
            elif kind == "unique":
                first = db.rows.insert(conn, "project", doc=DOC_MARKER, name=NAME_MARKER)
                db.rows.insert_exact(conn, "project", {**first})
            elif kind == "not_null":
                values = db.rows.values("project", doc=DOC_MARKER, name=NAME_MARKER)
                del values["created_at"]
                conn.execute(sa.insert(db.metadata.tables["project"]), values)
            elif kind == "foreign_key":
                values = db.rows.values("artifact", doc=DOC_MARKER)
                conn.execute(sa.insert(db.metadata.tables["artifact"]), values)
            else:  # restrict: a snapshot that a run still references
                run = db.rows.insert(conn, "run", doc=DOC_MARKER)
                snapshot = db.metadata.tables["config_snapshot"]
                conn.execute(sa.delete(snapshot).where(snapshot.c.id == run["config_snapshot_id"]))
    except sa.exc.DBAPIError as error:
        return error
    raise AssertionError(f"the database accepted the {kind} violation")


@dataclasses.dataclass
class Probe:
    """One thing that could leak: its label, the text it shows, the error (None for a report)."""

    label: str
    text: str
    error: BaseException | None = None


def _error_probe(label: str, error: BaseException) -> Probe:
    return Probe(label, surface(error), error)


def _report_probe(label: str, report: Any) -> Probe:
    parts = [repr(report), str(report)]
    for finding in (*report.findings, *report.informational):
        parts += [repr(finding), str(finding), *(str(v) for v in dataclasses.astuple(finding))]
    return Probe(label, "\n".join(parts))


def _probe_constraints(db: Db, evaldb: ModuleType) -> list[Probe]:
    repository = importlib.import_module("tracer_evaldb.repository")
    probes = []
    for kind in ("check", "unique", "not_null", "foreign_key", "restrict"):
        error = _violate(db, kind)
        probes.append(_error_probe(f"database {kind}", error))
        translated = repository.translate_integrity_error(error)
        assert isinstance(translated, evaldb.StoreConstraintViolation)
        probes.append(_error_probe(f"translated {kind}", translated))
    # a value the column type refuses before it reaches the database (beyond 64 bits)
    with pytest.raises(sa.exc.StatementError) as caught, db.engine.begin() as conn:
        db.rows.insert(conn, "snapshot_member", ordinal=HUGE)
    probes.append(_error_probe("bind overflow", caught.value))
    return probes


def _probe_out_of_range(db: Db, evaldb: ModuleType) -> list[Probe]:
    doc = run_bundle_doc(tier="T1", origin="simulated")
    doc["transcripts"][0]["timeline_origin"] = {
        "kind": "artifact",
        "ref": doc["artifacts"][0]["id"],
        "started_at": "2026-10-22T10:00:00.000000Z",
    }
    doc["turns"][0].update({"timing_source": "audio_alignment", "start_ms": 5, "end_ms": 2**53})
    doc["turns"] = [with_marker_metadata(turn, DOC_MARKER) for turn in doc["turns"]]
    doc["run"] = with_marker_metadata(doc["run"], DOC_MARKER)
    probes = []
    for label, end in (("end_ms is 2**53", 2**53), ("end_ms is a quotable number", NUMBER)):
        doc["turns"][0]["end_ms"] = end
        bundle = load_run_bundle_doc(doc)
        store_project_of(db.engine, evaldb, bundle)
        with pytest.raises(evaldb.StoreValueOutOfRange) as caught:
            evaldb.store_run_bundle(db.engine, bundle)
        probes.append(_error_probe(label, caught.value))
    return probes


def _tamper_cases(
    db: Db, populated: Any, project: Any, target: Any, item: Any
) -> list[tuple[str, Any, Any]]:
    """(table, primary key filter value, loader) for the rows that get damaged."""
    engine, evaldb = db.engine, db.evaldb
    bundle = populated.bundle
    return [
        ("project", project.id.uuid, lambda: evaldb.load_project(engine, project.id)),
        (
            "agent_target",
            target.id.uuid,
            lambda: evaldb.load_definition(engine, "agent_target", target.id, target.version),
        ),
        (
            "config_snapshot",
            bundle.snapshot.id.uuid,
            lambda: evaldb.load_snapshot(engine, bundle.snapshot.id),
        ),
        ("dataset_item", item.id.uuid, lambda: evaldb.load_dataset_item(engine, item.id)),
        (
            "export_bundle",
            populated.export.id.uuid,
            lambda: evaldb.load_export(engine, populated.export.id, expected_purpose="internal"),
        ),
        ("turn", bundle.turns[0].id.uuid, lambda: evaldb.load_run_bundle(engine, bundle.id)),
        ("run_bundle", bundle.id.uuid, lambda: evaldb.load_run_bundle(engine, bundle.id)),
    ]


def _edit(mode: str, doc: str) -> str:
    if mode == "edited":  # valid JSON, other content: the digest or the stored hash disagrees
        loaded = json.loads(doc)
        loaded["metadata"] = {"marker": EDIT_MARKER}
        return json.dumps(loaded, ensure_ascii=False, separators=(",", ":"))
    if mode == "broken":  # not JSON, holding the old text and a new marker
        return EDIT_MARKER + doc
    loaded = json.loads(doc)  # unknown: a field the strict loader refuses
    loaded["unexpected_" + "field"] = EDIT_MARKER
    return json.dumps(loaded, ensure_ascii=False, separators=(",", ":"))


def _probe_tampered(db: Db, evaldb: ModuleType) -> list[Probe]:
    populated = populate(db.engine, metadata={"marker": DOC_MARKER})
    project_model = _support.load_entity(
        "Project", json.dumps({**project_doc(new_id("prj")), "name": NAME_MARKER})
    )
    evaldb.store_project(db.engine, project_model)
    target = load_golden("AgentTarget", "minimal")
    item = load_golden("DatasetItem", "minimal")
    probes: list[Probe] = []
    for table, key, load in _tamper_cases(db, populated, project_model, target, item):
        t = db.metadata.tables[table]
        with db.engine.connect() as conn:
            row = dict(conn.execute(sa.select(t).where(t.c.id == key)).mappings().first() or {})
            conn.rollback()
        assert row, table
        for mode in ("edited", "broken", "unknown"):
            with _triggers_off(db, table), db.engine.begin() as conn:
                conn.execute(sa.update(t).where(t.c.id == key).values(doc=_edit(mode, row["doc"])))
            try:
                with pytest.raises(Exception) as caught:  # noqa: PT011  (any error: the type is asserted below)
                    load()
                probes.append(_error_probe(f"{table} doc {mode}", caught.value))
                report = evaldb.verify_database(db.engine)
                probes.append(_report_probe(f"verify after {table} doc {mode}", report))
            finally:
                with _triggers_off(db, table), db.engine.begin() as conn:
                    conn.execute(sa.update(t).where(t.c.id == key).values(doc=row["doc"]))
        if table == "project":  # a projected column that disagrees with the document
            with _triggers_off(db, table), db.engine.begin() as conn:
                conn.execute(sa.update(t).where(t.c.id == key).values(name=EDIT_MARKER))
            try:
                with pytest.raises(Exception) as caught:  # noqa: PT011  (any error: the type is asserted below)
                    load()
                probes.append(_error_probe("project projection", caught.value))
                probes.append(
                    _report_probe(
                        "verify after project projection", evaldb.verify_database(db.engine)
                    )
                )
            finally:
                with _triggers_off(db, table), db.engine.begin() as conn:
                    conn.execute(sa.update(t).where(t.c.id == key).values(name=row["name"]))
    return probes


def test_store_errors_hide_input(
    db: Db, evaldb: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    """P2-24: an error never contains the text of a document or the value of a bound parameter.

    Probed: constraint violations of the five kinds (raw, and translated by the repository), a
    value the column type refuses, ``StoreValueOutOfRange`` (the number is not quoted), and
    tampered rows: for seven tables a document edited, broken and given a field the strict loader
    refuses, and a projected column that disagrees with its document, each read back through the
    repository and through ``verify_database``. The text surface of every error (message,
    representation, arguments, notes, the attributes of a store error, the driver's original, the
    whole traceback text, and the exceptions Python itself shows with it) and every log record
    (with the SQLAlchemy engine logger at INFO) is searched for the markers.
    """
    caplog.set_level("DEBUG")
    caplog.set_level("INFO", logger="sqlalchemy.engine")
    probes = _probe_constraints(db, evaldb)
    probes += _probe_out_of_range(db, evaldb)
    probes += _probe_tampered(db, evaldb)
    assert len(probes) >= 5 * 2 + 1 + 2 + 7 * 3 * 2 + 2, len(probes)

    found = {probe.label: leaks(probe.text) for probe in probes}
    assert {label: hits for label, hits in found.items() if hits} == {}

    # every repository refusal is a typed store error whose chain holds no SQLAlchemy error
    for probe in probes:
        if isinstance(probe.error, evaldb.StoreError):
            for item in _chain(probe.error):
                assert not isinstance(item, sa.exc.SQLAlchemyError), probe.label
    tampered = [p for p in probes if " doc " in p.label and p.error is not None]
    assert tampered
    wrong = [
        (p.label, type(p.error).__name__)
        for p in tampered
        if not isinstance(p.error, evaldb.StoreIntegrityError | ev_errors.DigestMismatch)
    ]
    assert wrong == []  # a record of fact fails its hash, a versioned entity its own digest

    logged = "\n".join([r.getMessage() + repr(r.args) + (r.exc_text or "") for r in caplog.records])
    assert leaks(logged) == []
    assert any(r.name.startswith("sqlalchemy.engine") for r in caplog.records), (
        "the engine logger produced nothing: the log half of this test would prove nothing"
    )


def test_the_error_surface_reader_has_teeth() -> None:
    """``surface`` and ``leaks`` find a marker wherever an error could carry one."""

    class Holder(Exception):
        def __init__(self, value: Any) -> None:
            super().__init__("fixed text")
            self.value = value

    assert leaks("nothing here") == []
    assert leaks(surface(ValueError(f"bad {DOC_MARKER}"))) == [DOC_MARKER]
    assert leaks(surface(Holder({"deep": [(NAME_MARKER,)]}))) == [NAME_MARKER]
    assert leaks(surface(Holder(EDIT_MARKER.encode()))) == [EDIT_MARKER]
    assert leaks(surface(Holder(NUMBER))) == [str(NUMBER)]

    def chained(*, suppress: bool) -> RuntimeError:
        try:
            try:
                raise ValueError(DOC_MARKER)
            except ValueError as inner:
                if suppress:
                    raise RuntimeError("outer") from None
                raise RuntimeError("outer") from inner
        except RuntimeError as outer:
            return outer
        raise AssertionError("unreachable")

    assert leaks(surface(chained(suppress=False))) == [DOC_MARKER]
    assert leaks(surface(chained(suppress=True))) == []  # Python does not show a suppressed context
    note = ValueError("x")
    note.add_note(NAME_MARKER)
    assert leaks(surface(note)) == [NAME_MARKER]
    report = type("R", (), {"findings": (), "informational": ()})()
    assert _report_probe("empty", report).text
    assert leaks(_report_probe("empty", report).text) == []
