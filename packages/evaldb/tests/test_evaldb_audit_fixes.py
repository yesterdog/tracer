# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch G: the fixes decided after the P2-24 security audit (findings E5, E6, E10, E14,
E16, E17).

Contract: docs/phase2/api-surface-batch-g.md. Audit: docs/security/P2-24-audit.md.

* E5/E6: an ``IntegrityError`` that leaves the Engine untranslated (a caller's own statement) shows
  nothing of the failing row on any link of its exception chain, on SQLite and on Postgres: not in
  ``traceback.format_exception`` (every ``__cause__`` and ``__context__``), not in ``str``, ``repr``
  or ``args`` of a link, not in the driver error's ``args``, and not in the ``params`` attribute
  that holds the bound values. The repository's ``StoreConstraintViolation`` is clean even for a
  reader that walks ``__context__`` and ignores ``__suppress_context__``.
* E14: ``deletion_audit.actor`` must also match ``[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`` in full.
* E16: a new SQLite database file is created with mode 0600 (POSIX; the repository CI is Linux and
  macOS); an existing file keeps its mode; an in-memory database makes no file.
* E10: the store functions refuse an Engine that does not hide bound parameters (``UnsafeEngine``).
* E17: the SHA-256 of the exact text of the trigger statements is pinned in
  ``tests/fixtures/trigger_ddl_sha256.json`` (revision 0001 calls the live ``triggers.py``).

Updating the trigger pin (E17). The test ``test_trigger_ddl_text_matches_the_committed_hashes``
fails when a character of any statement of ``triggers.create_statements``,
``triggers.function_statements`` or ``triggers.drop_statements`` changes. That is not a formality:
a database migrated earlier keeps the old trigger text, and a new database gets the new text from
revision 0001, so the two would differ. Do not just refresh the file. A change of the trigger text
needs (1) a new Alembic revision that replaces the triggers of existing databases and (2) a decision
in the decision log that revision 0001 may create the new text (or a frozen copy of the old text in
0001, which is the open finding P2-F6). Only then run
``uv run python packages/evaldb/tests/evaldb_trigger_pin.py --write`` (it rewrites the file from the
live module and revision 0001's ``TABLES``), read the diff of the file, and commit it with the
change. The failure message prints the current values.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import sqlite3
import stat
import traceback
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
import evaldb_trigger_pin as pin  # type: ignore[import-not-found,unused-ignore]
from evaldb_deletion_support import (  # type: ignore[import-not-found,unused-ignore]
    ROOTS,
    World,
    audit_rows,
    call,
)
from evaldb_migration_support import raw_dump  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    load_run_bundle_doc,
    make_bundle,
    renew_ids,
    run_bundle_doc,
    store_bundle,
)
from evaldb_repo_support import recording as recording_statements
from evaldb_support import Db, load_golden  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import ids as ev_ids

db = _support.db

# ======================================================================== the leak detector

DOC_MARKER = "DOC-MARKER-7e41-\u0915\u094d\u0937"
NAME_MARKER = "NAME-MARKER-91c2-\u0928\u092e\u0938"
RENAMED_MARKER = "RENAMED-MARKER-4b08"
ACTOR_MARKER = "ACTOR-MARKER-2d65"
# the fields of a Postgres error that name no row value (the rule of engine.py, ADR 0019 D18)
SAFE_FIELDS = frozenset(["S", "V", "C", "M", "s", "t", "c", "d", "n"])

KINDS = (
    "check",
    "unique",
    "not_null",
    "foreign_key",
    "restrict",
    "immutable_update",
    "immutable_delete",
)


def _strings(value: object, depth: int = 0) -> list[str]:
    """Every piece of text a value can show: strings, bytes, dict keys and values, items of
    sequences, the text of IDs, and the repr of anything else."""
    if depth > 6:
        return [repr(value)]
    if isinstance(value, str):
        return [value]
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        return [raw.decode("utf-8", "replace"), raw.hex()]
    if isinstance(value, uuid.UUID):
        return [str(value), value.hex, repr(value)]
    if isinstance(value, Mapping):
        return [
            s for k, v in value.items() for s in (*_strings(k, depth + 1), *_strings(v, depth + 1))
        ]
    if isinstance(value, list | tuple | set | frozenset):
        return [s for item in value for s in _strings(item, depth + 1)]
    return [repr(value)]


def _members(error: BaseException) -> list[BaseException]:
    """The error and everything reachable through ``__cause__``, ``__context__`` (the suppress flag
    is ignored: a handler may walk the attribute) and the driver error ``orig``."""
    seen: set[int] = set()
    found: list[BaseException] = []
    stack: list[BaseException] = [error]
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        found.append(item)
        for nxt in (item.__cause__, item.__context__, getattr(item, "orig", None)):
            if isinstance(nxt, BaseException):
                stack.append(nxt)
    return found


def chain_text(error: BaseException) -> str:
    """All the text of the chain except the ``params`` attribute: the formatted traceback with
    every cause and context, and per link ``str``, ``repr``, ``args``, notes and attributes."""
    parts = ["".join(traceback.format_exception(error))]
    for item in _members(error):
        parts += [str(item), repr(item), *_strings(item.args), *getattr(item, "__notes__", [])]
        for name, value in vars(item).items():
            if name != "params":
                parts += _strings(value)
    return "\n".join(parts)


def params_text(error: BaseException) -> str:
    """The text of the ``params`` attribute of every link that has one."""
    parts = []
    for item in _members(error):
        if hasattr(item, "params"):
            parts += _strings(item.params)
    return "\n".join(parts)


def leaks(text: str, needles: Iterable[str]) -> list[str]:
    return [n for n in needles if n and n in text]


def test_the_leak_detector_sees_a_planted_value() -> None:
    """A detector that finds nothing proves nothing: plant a value in each place it must read."""
    needle = "PLANTED-9c1e-\u0915"
    inner = ValueError("inner")
    inner.args = ({"D": f"Failing row contains ({needle})"},)
    outer = sa.exc.IntegrityError(
        "INSERT INTO t VALUES (?)", (needle,), inner, hide_parameters=True
    )
    outer.__cause__ = inner
    assert leaks(chain_text(outer), [needle]) == [needle]
    assert leaks(params_text(outer), [needle]) == [needle]
    context_only = RuntimeError("outer")
    context_only.__context__ = outer
    context_only.__suppress_context__ = True  # the traceback hides it, a handler can still read it
    assert leaks(params_text(context_only), [needle]) == [needle]
    assert leaks(chain_text(sa.exc.IntegrityError("x", None, ValueError("y"))), [needle]) == []


# ============================================================================ E5 and E6


def _provoke(db: Db, kind: str) -> tuple[sa.exc.DBAPIError, list[str]]:
    """One constraint violation of ``kind`` by a raw statement (so the error leaves the Engine
    untranslated by the repository), with the markers and the IDs in the failing row. Returns the
    error and the values that must not appear."""
    tables = db.metadata.tables
    needles = [DOC_MARKER, NAME_MARKER, RENAMED_MARKER, ACTOR_MARKER]
    try:
        with db.engine.begin() as conn:
            if kind == "check":
                values = db.rows.values(
                    "project", schema_version="0.0.2", doc=DOC_MARKER, name=NAME_MARKER
                )
                needles += _strings(values["id"])
                db.rows.insert_exact(conn, "project", values)
            elif kind == "unique":
                first = db.rows.insert(conn, "project", doc=DOC_MARKER, name=NAME_MARKER)
                needles += _strings(first["id"])
                db.rows.insert_exact(conn, "project", {**first})
            elif kind == "not_null":
                values = db.rows.values("project", doc=DOC_MARKER, name=NAME_MARKER)
                needles += _strings(values["id"])
                del values["name"]
                conn.execute(sa.insert(tables["project"]), values)
            elif kind == "foreign_key":
                values = db.rows.values("artifact", doc=DOC_MARKER)
                needles += [*_strings(values["id"]), *_strings(values["run_id"])]
                conn.execute(sa.insert(tables["artifact"]), values)
            elif kind == "restrict":
                run = db.rows.insert(conn, "run", doc=DOC_MARKER)
                needles += [*_strings(run["id"]), *_strings(run["config_snapshot_id"])]
                snapshot = tables["config_snapshot"]
                conn.execute(sa.delete(snapshot).where(snapshot.c.id == run["config_snapshot_id"]))
            elif kind == "immutable_update":
                project = tables["project"]
                first = db.rows.insert(conn, "project", doc=DOC_MARKER, name=NAME_MARKER)
                needles += _strings(first["id"])
                conn.execute(
                    sa.update(project)
                    .where(project.c.id == first["id"])
                    .values(name=RENAMED_MARKER)
                )
            else:  # immutable_delete: the audit is append-only
                audit = tables["deletion_audit"]
                row = db.rows.insert(conn, "deletion_audit", actor=ACTOR_MARKER)
                needles += _strings(row["audit_uuid"])
                conn.execute(sa.delete(audit).where(audit.c.audit_uuid == row["audit_uuid"]))
    except sa.exc.DBAPIError as error:
        return error, needles
    raise AssertionError(f"the database accepted the {kind} violation")


@pytest.mark.parametrize("kind", KINDS)
def test_untranslated_integrity_error_chain_text_holds_no_row_detail(db: Db, kind: str) -> None:
    """E5. On Postgres the original driver error (``__cause__``) carries field ``D``: "Failing row
    contains (...)" and "Key (id)=(...)". The text of every link must be free of it: the formatted
    traceback with all causes and contexts, and ``str``, ``repr`` and ``args`` of every link."""
    error, needles = _provoke(db, kind)
    assert isinstance(error, sa.exc.IntegrityError)
    assert error.orig is not None
    text = chain_text(error)
    assert leaks(text, needles) == []
    assert "failing row" not in text.lower()
    # what is still there: the statement, the driver error, the cause
    assert isinstance(error.statement, str)
    assert error.orig in _members(error)


@pytest.mark.parametrize("kind", KINDS)
def test_untranslated_integrity_error_params_hold_no_bound_value(db: Db, kind: str) -> None:
    """E6. ``hide_parameters=True`` hides the bound values from ``str()`` only; the attribute
    ``params`` of the error (and of every SQLAlchemy error in its chain) must not hold them either,
    on both backends."""
    error, needles = _provoke(db, kind)
    assert isinstance(error, sa.exc.IntegrityError)
    assert leaks(params_text(error), needles) == []


@pytest.mark.parametrize("kind", KINDS)
def test_driver_error_args_along_the_chain_are_scrubbed(db: Db, kind: str) -> None:
    """E5. Postgres: every driver error in the chain (the ``orig`` and the ``__cause__``) has the
    fields of the engine's scrub rule only. SQLite: the driver error is the native one and its
    arguments are one message."""
    error, needles = _provoke(db, kind)
    dict_args = [a for item in _members(error) for a in item.args if isinstance(a, dict)]
    for fields in dict_args:
        assert set(fields) <= SAFE_FIELDS, sorted(set(fields) - SAFE_FIELDS)
        assert leaks("\n".join(_strings(fields)), needles) == []
    if db.backend == "postgres":
        assert dict_args, "the Postgres driver error is expected to carry its fields as a dict"
        assert all({"C", "M"} <= set(f) for f in dict_args)
    else:
        assert isinstance(error.orig, sqlite3.IntegrityError)
        assert dict_args == []
        assert all(isinstance(a, str) for a in error.orig.args)


@pytest.mark.parametrize("kind", KINDS)
def test_translated_store_error_chain_holds_no_value_even_ignoring_suppress(
    db: Db, evaldb: ModuleType, kind: str
) -> None:
    """E5 and E6 for the typed error: ``translate_integrity_error`` still names the kind, and the
    ``StoreConstraintViolation`` it makes leaks nothing through ``__cause__`` or ``__context__``."""
    repository = importlib.import_module("tracer_evaldb.repository")
    error, needles = _provoke(db, kind)
    assert isinstance(error, sa.exc.IntegrityError)
    translated = repository.translate_integrity_error(error)
    assert isinstance(translated, evaldb.StoreConstraintViolation)
    assert translated.kind in {"check", "unique", "not_null", "foreign_key", "other"}
    assert leaks(chain_text(translated) + params_text(translated), needles) == []


def test_repository_store_error_chain_holds_no_stored_text_or_id(
    db: Db, evaldb: ModuleType
) -> None:
    """The repository path of E6. A rescoring that lists a different turn at a stored position is
    refused by the unique constraint and raised as ``StoreConstraintViolation ... from None``. A
    reader that walks ``__context__`` (ignoring ``__suppress_context__``) must find no turn text,
    no ID and no bound value, and no ``params`` holding them."""
    doc = run_bundle_doc(tier="T1", origin="simulated")
    first = load_run_bundle_doc(doc)
    store_bundle(db.engine, evaldb, first)
    second = load_run_bundle_doc(renew_ids(doc, lists=["turns", "results"], verdict=True))
    needles = [str(t.text) for t in second.turns] + [
        s for t in (*first.turns, *second.turns) for s in _strings(t.id)
    ]
    needles += _strings(second.id)
    assert needles
    assert all(len(n) >= 8 for n in needles), "the needles must be distinctive"
    with pytest.raises(evaldb.StoreConstraintViolation) as caught:
        evaldb.store_run_bundle(db.engine, second)
    error = caught.value
    assert error.kind == "unique"
    assert leaks(chain_text(error), needles) == []
    assert leaks(params_text(error), needles) == []
    for item in _members(error):
        for text in (str(item), repr(item)):
            assert leaks(text, needles) == []


# ================================================================================== E14

ACTOR_RULE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")  # matched in full


def _credential_shapes() -> list[str]:
    """Credential shapes that ALSO match the label rule, so only the credential check refuses
    them (assembled from parts: no literal secret in the source)."""
    return [
        "sk" + "-" + "Ab3dE6gH9jK2mN5pQ8sT1vW4",
        "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
        "xox" + "b-" + "123456789012-abcdefghij",
        "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".sig",
        "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6kM9n",
    ]


PATTERN_REJECTED = [
    "alice@example.org",
    "user@example.org",
    "a.b@c.de",
    "",
    "a" * 129,
    "A" + "b" * 200,
    "-leading-hyphen",
    ".leading-dot",
    "_leading_underscore",
    ":leading-colon",
    "Priya Nair",
    "space inside",
    "Priya\u00e9",
    "caf\u00e9",
    "cafe\u0301",
    "\u0911\u092a\u0930\u0947\u091f\u0930",
    "ops\u200cteam",  # a ZWNJ is not in the class
    "ops\u200dteam",  # nor a ZWJ
    "\uff21lice",  # a full-width letter
    "ops\u0663",  # an Arabic-Indic digit
    "alice\n",  # a trailing newline must not slip through a '$' anchor
    "alice\r",
    "ops/night",
    "ops#2",
    "ops(night)",
    "ops+night",
    "ops=night",
    "ops~night",
    "night shift " + "sk" + "-" + "Zy9xW8vU7tS6rQ5pO4nM3lK2",
]
LABELS_ACCEPTED = [
    "ops-night-shift",
    "svc.tracer-ci",
    "operator:alice-1",
    "service_account:tracer-ci",
    "Operator.Alice_1",
    "0",
    "a",
    "A",
    "7-eleven",
    "0123456789abcdef",
    "support-team-2026-10-24",
    "a" * 31,
    "a" * 128,
    "Z" + "-" * 127,
    "a.b.c.d.e",
    "ns:sub:name",
    "x" + "." * 3 + "y",
    "measure",
]
REASONS = ("operator_request", "retention_policy", "data_subject_request")


def test_actor_fixtures_match_the_stated_rule() -> None:
    """The lists below are what the tests say they are."""
    assert [a for a in PATTERN_REJECTED if ACTOR_RULE.fullmatch(a)] == []
    assert [a for a in LABELS_ACCEPTED if not ACTOR_RULE.fullmatch(a)] == []
    assert [a for a in _credential_shapes() if not ACTOR_RULE.fullmatch(a)] == []
    assert "a" * 128 in LABELS_ACCEPTED
    assert "a" * 129 in PATTERN_REJECTED


def _no_echo(actor: str, message: str) -> None:
    if actor.strip():
        assert actor not in message
    assert all(actor[i : i + 8] not in message for i in range(0, max(len(actor) - 7, 0), 4)), (
        "the message echoes part of the value"
    )


@pytest.mark.parametrize("spec", [pytest.param(s, id=s.id) for s in ROOTS])
def test_actor_outside_the_label_rule_is_refused_by_every_deletion(
    db: Db, evaldb: ModuleType, spec: Any
) -> None:
    """E14. E-mail addresses, empty, over 128 characters, a leading punctuation mark, non-ASCII,
    spaces and a trailing newline are a ``ValueError`` naming the actor (the existing error), not
    echoing the value; the check runs before any statement and nothing is written."""
    world = World(db.engine)
    built = spec.build(world)
    before = raw_dump(db.engine)
    with recording_statements(db.engine) as statements:
        for actor in PATTERN_REJECTED:
            with pytest.raises(ValueError, match="actor") as caught:
                call(evaldb, db.engine, spec, built.args, actor=actor)
            message = str(caught.value) + repr(caught.value) + repr(caught.value.args)
            _no_echo(actor, message)
            assert caught.value.__cause__ is None
    assert statements.count == 0
    assert raw_dump(db.engine) == before
    assert audit_rows(db.engine, db.metadata) == []


@pytest.mark.parametrize("secret", _credential_shapes(), ids=lambda s: f"len{len(s)}")
@pytest.mark.parametrize("spec", [pytest.param(s, id=s.id) for s in ROOTS[:2]])
def test_actor_credential_check_still_applies_to_values_that_match_the_label_rule(
    db: Db, evaldb: ModuleType, spec: Any, secret: str
) -> None:
    """E14 'on top of' the credential check: these values pass the label rule, and are still
    refused, without echo, with nothing written."""
    world = World(db.engine)
    built = spec.build(world)
    before = raw_dump(db.engine)
    with (
        recording_statements(db.engine) as statements,
        pytest.raises(ValueError, match="actor") as caught,
    ):
        call(evaldb, db.engine, spec, built.args, actor=secret)
    message = str(caught.value) + repr(caught.value) + repr(caught.value.args)
    _no_echo(secret, message)
    assert statements.count == 0
    assert raw_dump(db.engine) == before
    assert audit_rows(db.engine, db.metadata) == []


def test_actor_labels_that_follow_the_rule_are_accepted_and_stored_exactly(
    db: Db, evaldb: ModuleType
) -> None:
    world = World(db.engine)
    expected = []
    for index, actor in enumerate(LABELS_ACCEPTED):
        reason = REASONS[index % 3]
        evaldb.delete_run(db.engine, world.run(sizes={}).id, actor=actor, reason=reason)
        expected.append((actor, reason))
    stored = [(r["actor"], r["reason"]) for r in audit_rows(db.engine, db.metadata)]
    assert sorted(stored) == sorted(expected)


@pytest.mark.parametrize("spec", [pytest.param(s, id=s.id) for s in ROOTS])
def test_a_service_account_label_is_accepted_by_every_deletion(
    db: Db, evaldb: ModuleType, spec: Any
) -> None:
    world = World(db.engine)
    built = spec.build(world)
    call(evaldb, db.engine, spec, built.args, actor="operator:alice-1")
    (row,) = audit_rows(db.engine, db.metadata)
    assert row["actor"] == "operator:alice-1"
    assert row["root_kind"] == spec.root_kind


# ================================================================================== E16


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@contextlib.contextmanager
def _umask(value: int) -> Iterator[None]:
    old = os.umask(value)
    try:
        yield
    finally:
        os.umask(old)


@pytest.mark.parametrize("umask", [0o000, 0o002, 0o022, 0o077], ids=lambda m: f"umask{m:04o}")
def test_a_new_sqlite_file_is_created_with_mode_0600(
    evaldb: ModuleType, tmp_path: Path, umask: int
) -> None:
    """E16. The file exists, and has mode 0600, as soon as ``create_store_engine`` returns, with
    whatever umask the process has; use of the store does not change it."""
    path = tmp_path / "new.db"
    with _umask(umask):
        engine = evaldb.create_store_engine(f"sqlite:///{path}")
    try:
        assert path.is_file()
        assert _mode(path) == 0o600
        with _umask(umask):
            evaldb.metadata.create_all(engine)
            repository = importlib.import_module("tracer_evaldb.repository")
            repository.store_project(engine, _project())
        assert _mode(path) == 0o600
    finally:
        engine.dispose()


def _project() -> Any:
    return load_golden("Project", "minimal")


def test_a_new_sqlite_file_in_a_directory_with_a_space_and_by_url_object(
    evaldb: ModuleType, tmp_path: Path
) -> None:
    folder = tmp_path / "data dir"
    folder.mkdir()
    path = folder / "x.db"
    with _umask(0o000):
        engine = evaldb.create_store_engine(sa.URL.create("sqlite", database=str(path)))
    engine.dispose()
    assert _mode(path) == 0o600


def test_a_new_sqlite_file_by_relative_path_has_mode_0600(
    evaldb: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with _umask(0o022):
        engine = evaldb.create_store_engine("sqlite:///relative.db")
    engine.dispose()
    assert _mode(tmp_path / "relative.db") == 0o600


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o664, 0o666, 0o600, 0o620])
def test_an_existing_empty_file_keeps_its_mode(
    evaldb: ModuleType, tmp_path: Path, mode: int
) -> None:
    path = tmp_path / "existing.db"
    path.touch()
    os.chmod(path, mode)
    with _umask(0o022):
        engine = evaldb.create_store_engine(f"sqlite:///{path}")
    engine.dispose()
    assert _mode(path) == mode


def test_an_existing_database_keeps_its_mode_and_its_data(
    evaldb: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE kept (v TEXT)")
    connection.execute("INSERT INTO kept VALUES ('still here')")
    connection.commit()
    connection.close()
    os.chmod(path, 0o664)
    engine = evaldb.create_store_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT v FROM kept").scalar_one() == "still here"
            conn.rollback()
    finally:
        engine.dispose()
    assert _mode(path) == 0o664


def test_reopening_a_file_the_package_made_does_not_change_a_mode_the_owner_set(
    evaldb: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "again.db"
    evaldb.create_store_engine(f"sqlite:///{path}").dispose()
    assert _mode(path) == 0o600
    os.chmod(path, 0o640)  # the owner decides to share it with a group
    evaldb.create_store_engine(f"sqlite:///{path}").dispose()
    assert _mode(path) == 0o640


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_in_memory_databases_make_no_file(
    evaldb: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.chdir(tmp_path)
    engine = evaldb.create_store_engine(url)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT 1").scalar_one() == 1
            conn.rollback()
    finally:
        engine.dispose()
    assert sorted(os.listdir(tmp_path)) == []


def test_a_missing_directory_still_fails_as_before_and_makes_nothing(
    evaldb: ModuleType, tmp_path: Path
) -> None:
    target = tmp_path / "no-such-dir" / "x.db"
    with pytest.raises(evaldb.StoreConnectionError) as caught:
        evaldb.create_store_engine(f"sqlite:///{target}")
    assert str(tmp_path) not in "".join(traceback.format_exception(caught.value))
    assert not target.parent.exists()
    assert sorted(os.listdir(tmp_path)) == []


# ================================================================================== E10


def _uuid7() -> uuid.UUID:
    found: uuid.UUID = ev_ids.uuid7()
    return found


def _store_calls(evaldb: ModuleType) -> dict[str, Callable[[Any], Any]]:
    """Every public function of the store that takes the Engine first, with valid arguments, so
    that the refusal cannot be a refusal of an argument. Keys are the function names."""
    bundle = make_bundle()
    project = load_golden("Project", "minimal")
    definition = load_golden("AgentTarget", "minimal")
    item = load_golden("DatasetItem", "minimal")
    snapshot = load_golden("ConfigSnapshot", "minimal")
    export = load_golden("ExportBundle", "minimal")
    purpose = str(export.purpose)
    ref = ev_ids.ExternalRef(system="batch-g", kind="call", value="ref-1")
    calls: dict[str, Callable[[Any], Any]] = {
        "store_project": lambda e: evaldb.store_project(e, project),
        "store_dataset_item": lambda e: evaldb.store_dataset_item(e, item),
        "store_definition": lambda e: evaldb.store_definition(e, definition),
        "store_snapshot": lambda e: evaldb.store_snapshot(e, snapshot),
        "store_run_bundle": lambda e: evaldb.store_run_bundle(e, bundle),
        "store_export": lambda e: evaldb.store_export(e, export, expected_purpose=purpose),
        "load_run_bundle": lambda e: evaldb.load_run_bundle(e, bundle.id),
        "load_project": lambda e: evaldb.load_project(e, _uuid7()),
        "load_definition": lambda e: evaldb.load_definition(e, "agent_target", _uuid7(), 1),
        "load_snapshot": lambda e: evaldb.load_snapshot(e, _uuid7()),
        "load_dataset_item": lambda e: evaldb.load_dataset_item(e, _uuid7()),
        "load_export": lambda e: evaldb.load_export(e, _uuid7(), expected_purpose="internal"),
        "latest_version": lambda e: evaldb.latest_version(e, "agent_target", _uuid7()),
        "find_snapshot_by_digest": lambda e: evaldb.find_snapshot_by_digest(
            e, snapshot.content_digest
        ),
        "find_by_external_ref": lambda e: evaldb.find_by_external_ref(e, "run", ref),
        "delete_run": lambda e: evaldb.delete_run(e, _uuid7(), actor="qa", reason=REASONS[0]),
        "delete_project": lambda e: evaldb.delete_project(
            e, _uuid7(), actor="qa", reason=REASONS[0]
        ),
        "delete_test_profile": lambda e: evaldb.delete_test_profile(
            e, _uuid7(), actor="qa", reason=REASONS[0]
        ),
        "delete_definition": lambda e: evaldb.delete_definition(
            e, "agent_target", _uuid7(), actor="qa", reason=REASONS[0]
        ),
        "delete_dataset_item": lambda e: evaldb.delete_dataset_item(
            e, _uuid7(), actor="qa", reason=REASONS[0]
        ),
        "delete_export": lambda e: evaldb.delete_export(e, _uuid7(), actor="qa", reason=REASONS[0]),
        "delete_snapshot": lambda e: evaldb.delete_snapshot(
            e, _uuid7(), actor="qa", reason=REASONS[0]
        ),
        "verify_database": lambda e: evaldb.verify_database(e),
    }
    return calls


# Functions of the package whose first parameter is the Engine and that this contract does not
# cover (see the contract, section 4 and ambiguity A7).
ENGINE_GUARD_NOT_COVERED = frozenset({"open_store"})


def test_the_engine_guard_test_covers_every_public_function_that_takes_an_engine(
    evaldb: ModuleType,
) -> None:
    import inspect

    takers = set()
    for name in evaldb.__all__:
        member = getattr(evaldb, name)
        if inspect.isfunction(member):
            parameters = list(inspect.signature(member).parameters)
            if parameters and parameters[0] == "engine":
                takers.add(name)
    assert takers - ENGINE_GUARD_NOT_COVERED == set(_store_calls(evaldb)), (
        "a public function takes an Engine and is not in the guard test"
    )


def _plain_engine(url: str) -> sa.Engine:
    engine = sa.create_engine(url)
    assert engine.hide_parameters is False
    return engine


def _url_needles(url: str, folder: Path) -> list[str]:
    parsed = sa.engine.make_url(url)
    found = [str(folder), folder.name, "store.db", url]
    for part in (parsed.password, parsed.username, parsed.host, parsed.database):
        if part and len(part) >= 3:
            found.append(part)
    return found


def test_unsafe_engine_error_type(evaldb: ModuleType) -> None:
    errors = importlib.import_module("tracer_evaldb.errors")
    cls = evaldb.UnsafeEngine
    assert cls.__name__ == "UnsafeEngine"
    assert cls.__bases__ == (evaldb.StoreConfigurationError,)
    assert issubclass(cls, evaldb.StoreError)
    assert not issubclass(cls, TypeError)
    assert errors.UnsafeEngine is cls
    assert "UnsafeEngine" in evaldb.__all__
    assert "UnsafeEngine" in errors.__all__


def test_store_functions_refuse_an_engine_that_does_not_hide_parameters(
    db: Db, evaldb: ModuleType, tmp_path: Path
) -> None:
    """E10. Every store function (repository, deletion, verify) refuses an Engine with
    ``hide_parameters`` false with ``UnsafeEngine`` before it sends a statement or takes a
    connection; the message names the rule and holds nothing of the URL."""
    plain = _plain_engine(db.store.url)
    checkouts: list[int] = []
    sa.event.listen(plain, "checkout", lambda *args: checkouts.append(1))
    try:
        with recording_statements(plain) as statements:
            for function, run in _store_calls(evaldb).items():
                with pytest.raises(evaldb.UnsafeEngine) as caught:
                    run(plain)
                message = "".join(traceback.format_exception(caught.value))
                assert "hide_parameters" in str(caught.value), function
                assert leaks(message, _url_needles(db.store.url, tmp_path)) == [], function
        assert statements.count == 0
        assert checkouts == []
    finally:
        plain.dispose()
    assert raw_dump(db.engine) == {t: [] for t in raw_dump(db.engine)}


def test_store_functions_accept_the_engine_of_create_store_engine(
    db: Db, evaldb: ModuleType
) -> None:
    """E10. The factory's engines (and an engine derived from one with ``execution_options``, which
    is how ``verify_database`` reads on Postgres) are not refused."""
    assert db.engine.hide_parameters is True
    derived = db.engine.execution_options(yield_per=10)
    for engine in (db.engine, derived):
        assert evaldb.verify_database(engine).findings == ()
        with pytest.raises(evaldb.RecordNotStored):
            evaldb.load_project(engine, _uuid7())
        with pytest.raises(evaldb.RecordNotStored):
            evaldb.delete_run(engine, _uuid7(), actor="qa", reason=REASONS[0])
        evaldb.store_project(engine, _project())


def test_a_non_engine_is_still_a_type_error(db: Db, evaldb: ModuleType) -> None:
    """Unchanged: a Connection or anything else is a TypeError (the first check)."""
    with db.engine.connect() as connection:
        with pytest.raises(TypeError):
            evaldb.load_project(connection, _uuid7())
        with pytest.raises(TypeError):
            evaldb.verify_database(connection)
        connection.rollback()
    with pytest.raises(TypeError):
        evaldb.delete_run(object(), _uuid7(), actor="qa", reason=REASONS[0])


# ================================================================================== E17


def _revision_0001() -> Any:
    return importlib.import_module("tracer_evaldb.migrations.versions.0001_initial_schema")


def test_trigger_ddl_text_matches_the_committed_hashes(evaldb: ModuleType) -> None:
    """E17. The SHA-256 of the exact statements of ``triggers.create_statements`` (each table of
    revision 0001, both dialects), ``triggers.function_statements`` (Postgres) and
    ``triggers.drop_statements`` equals the committed value. See the module docstring for the
    update procedure: a change of this text is a migration decision, not a refresh of a file."""
    recorded = pin.read_pin()["sha256"]
    triggers = importlib.import_module("tracer_evaldb.triggers")
    current = pin.current_hashes(triggers, list(_revision_0001().TABLES))
    assert isinstance(recorded, dict)
    changed = sorted(name for name in pin.GROUPS if recorded.get(name) != current[name])
    assert changed == [], (
        f"the text of the trigger statements changed in: {changed}. Revision 0001 builds new "
        "databases from triggers.py, databases migrated earlier keep the old text. Read the "
        "module docstring of test_evaldb_audit_fixes.py before updating "
        "tests/fixtures/trigger_ddl_sha256.json. Current values: " + json.dumps(current, indent=1)
    )


def test_the_pin_file_has_the_documented_shape_and_the_tables_of_revision_0001() -> None:
    document = pin.read_pin()
    assert sorted(document) == ["algorithm", "format", "serialization", "sha256", "tables"]
    assert document["format"] == 1
    assert document["algorithm"] == "sha256"
    assert document["serialization"] == pin.SERIALIZATION
    assert document["tables"] == list(_revision_0001().TABLES)
    assert len(document["tables"]) == 37
    hashes = document["sha256"]
    assert isinstance(hashes, dict)
    assert sorted(hashes) == sorted(pin.GROUPS)
    for name, value in hashes.items():
        assert re.fullmatch(r"[0-9a-f]{64}", value), name
    assert len(set(hashes.values())) == len(hashes)  # no two groups share a hash by accident


def test_the_trigger_statements_are_what_the_pin_serialization_says(evaldb: ModuleType) -> None:
    """The serialization is plain: recompute one group by hand."""
    import hashlib

    triggers = importlib.import_module("tracer_evaldb.triggers")
    functions = triggers.function_statements()
    assert len(functions) == 2
    expected = hashlib.sha256(b"".join(s.encode("utf-8") + b"\x00" for s in functions)).hexdigest()
    assert pin.read_pin()["sha256"]["postgresql.functions"] == expected


def _altered(triggers: ModuleType, group: str) -> SimpleNamespace:
    """The module with one character of the first statement of one group changed."""

    def tweak(statements: list[str]) -> list[str]:
        return [statements[0].replace(" ", "  ", 1), *statements[1:]]

    def create(table: str, dialect: str) -> list[str]:
        found: list[str] = triggers.create_statements(table, dialect)
        return tweak(found) if group == f"{dialect}.create" and table == "agent_target" else found

    def drop(table: str, dialect: str) -> list[str]:
        found: list[str] = triggers.drop_statements(table, dialect)
        return tweak(found) if group == f"{dialect}.drop" and table == "agent_target" else found

    def functions() -> list[str]:
        found = list(triggers.function_statements())
        return tweak(found) if group == "postgresql.functions" else found

    return SimpleNamespace(
        create_statements=create, drop_statements=drop, function_statements=functions
    )


@pytest.mark.parametrize("group", pin.GROUPS)
def test_the_pin_detects_a_one_character_change_in_each_group(group: str) -> None:
    triggers = importlib.import_module("tracer_evaldb.triggers")
    tables = list(_revision_0001().TABLES)
    base = pin.current_hashes(triggers, tables)
    changed = pin.current_hashes(_altered(triggers, group), tables)
    assert [name for name in pin.GROUPS if changed[name] != base[name]] == [group]


def test_the_pin_covers_each_dialect_and_every_table_of_revision_0001() -> None:
    triggers = importlib.import_module("tracer_evaldb.triggers")
    tables = list(_revision_0001().TABLES)
    groups = pin.group_statements(triggers, tables)
    # one update trigger per table, plus the delete trigger of the append-only audit
    for dialect in pin.DIALECTS:
        assert len(groups[f"{dialect}.create"]) == len(tables) + 1
        assert len(groups[f"{dialect}.drop"]) == len(tables) + 1
    assert len(groups["postgresql.functions"]) == 2
    assert any("deletion_audit" in s and "DELETE" in s for s in groups["sqlite.create"])
