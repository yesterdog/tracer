# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch F, P2-25: ``packages/evaldb/STORAGE_POLICY.md``.

Contract: docs/phase2/api-surface-batch-f.md sections 4 to 6. The policy is read by the tests the
way a reader would: its parity table is compared with the real column types of both backends, its
statements are each tested or marked ``(unverified)``, and each statement the owner asked for is
looked for in its own section.

Tests that take ``store`` or ``db`` run on SQLite and, with ``-m postgres``, on Postgres.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

import evaldb_docs_support as docs  # type: ignore[import-not-found,unused-ignore]
import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import new_id, project_doc  # type: ignore[import-not-found,unused-ignore]
from evaldb_support import Db  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema import loader as ev_loader

db = _support.db
sqlite_db = _support.sqlite_db

TITLES = {
    1: "What this document is",
    2: "The parity table",
    3: "The database is not a validation boundary",
    4: "Limits",
    5: "Encryption at rest",
    6: "Digests and the plain profile digest",
    7: "Deletion and audit",
    8: "Migrations and versions",
    9: "Phase 5 risks",
    10: "Statements that are not verified",
}
REGISTER = 10
PARITY_HEADER = ["Logical type", "Postgres", "SQLite (STRICT)"]
PARITY_CLASSES = (
    "UuidCol",
    "InstantCol",
    "DecimalCol",
    "DocCol",
    "Sha256Col",
    "KeyCol",
    "BigIntCol",
    "BoolCol",
)
BOUNDARY_TEST = "test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it"
PLAINTEXT_TEST = "test_the_sqlite_file_holds_documents_in_plain_text"
PARITY_TEST = "test_storage_policy_parity_table_matches_column_types"
ADR_0021 = "docs/adr/0021-store-schema-version-and-migrations.md"


def policy_text() -> str:
    assert docs.STORAGE_POLICY.is_file(), "packages/evaldb/STORAGE_POLICY.md does not exist yet"
    text: str = docs.read(docs.STORAGE_POLICY)
    return text


def sections() -> dict[int, docs.Section]:
    return {s.number: s for s in docs.numbered_sections(policy_text())}


# ======================================================================== structure and rules


def structure_problems(text: str) -> list[str]:
    """The shape the contract pins: title, the ten numbered sections in order, ASCII, no tab, no
    code fence, no abbreviation with periods, and exactly one table (the parity table, in
    section 2)."""
    problems: list[str] = []
    lines = text.splitlines()
    version = _package_version()
    if not lines or lines[0] != f"# Storage policy: tracer-evaldb {version}":
        problems.append("the first line is not '# Storage policy: tracer-evaldb <version>'")
    if not text.isascii():
        problems.append("not ASCII")
    if "\t" in text:
        problems.append("a tab")
    if any(line.startswith("```") for line in lines):
        problems.append("a code fence")
    if any(line != line.rstrip() for line in lines):
        problems.append("trailing white space")
    if not text.endswith("\n") or text.endswith("\n\n"):
        problems.append("not exactly one final newline")
    for abbreviation in docs.ABBREVIATIONS:
        if abbreviation in text:
            problems.append(f"abbreviation {abbreviation!r}")
    try:
        found = docs.numbered_sections(text)
    except AssertionError as error:
        return [*problems, str(error)]
    if [(s.number, s.title) for s in found] != list(TITLES.items()):
        problems.append(f"sections are {[(s.number, s.title) for s in found]}")
    for section in found:
        tables = docs.tables(section.lines)
        if section.number == 2:
            if len(tables) != 1:
                problems.append("section 2 must hold exactly one table")
        elif tables:
            problems.append(f"section {section.number} holds a table")
    return problems


def _package_version() -> str:
    import tomllib

    data = tomllib.loads(docs.read(docs.EVALDB / "pyproject.toml"))
    version = data["project"]["version"]
    assert isinstance(version, str)
    return version


def statement_problems(text: str, known_tests: set[str]) -> list[str]:
    """The sentence rule of the contract (section 5), over sections 1 to 9, and the register
    (section 10)."""
    problems: list[str] = []
    by_number = {s.number: s for s in docs.numbered_sections(text)}
    marked: list[str] = []
    for number in range(1, REGISTER):
        section = by_number.get(number)
        if section is None:
            problems.append(f"section {number} is missing")
            continue
        for block in docs.blocks(section.lines):
            if not block.endswith((".", ".)", ".**", ".`")):
                problems.append(f"section {number}: a statement without a final period: {block!r}")
        for sentence in docs.statements(section.lines):
            where = f"section {number}: {sentence[:70]!r}"
            if sentence.replace("**", "").strip(" .") == docs.MARKER:
                problems.append(f"{where}: the marker is a sentence of its own")
                continue
            tests = docs.citations(sentence)
            unverified = docs.is_unverified(sentence)
            if docs.is_pointer(sentence):
                if tests or unverified:
                    problems.append(f"{where}: a pointer sentence carries a citation or marker")
                continue
            if tests and unverified:
                problems.append(f"{where}: cited and marked unverified")
            if not tests and not unverified:
                problems.append(f"{where}: neither a test citation nor the marker")
            for name in tests:
                if name not in known_tests:
                    problems.append(f"{where}: {name} is not a test")
            if unverified:
                marked.append(docs.normalized(sentence))
    register = by_number.get(REGISTER)
    if register is None:
        return [*problems, "section 10 is missing"]
    entries = [docs.normalized(b) for b in docs.blocks(register.lines)]
    if marked:
        if entries != marked:
            problems.append(f"the register is {entries!r}, the marked sentences are {marked!r}")
        for block in docs.blocks(register.lines):
            if len(docs.sentences(block)) != 1:
                problems.append(f"a register entry is not one sentence: {block!r}")
    elif entries != ["None."]:
        problems.append(f"no sentence is marked, so section 10 is exactly 'None.', not {entries!r}")
    return problems


# ----------------------------------------------------------------- the rules have teeth

MINI_BODY = {
    1: "See `packages/evaldb/README.md`.\n\nA tested statement (`test_alpha`).",
    2: "A table follows (`test_alpha`).\n\n| a | b |\n| --- | --- |\n| 1 | 2 |",
    3: (
        "- First item (`test_alpha`).\n- Second item that continues\n"
        "  on a second line (`test_beta`)."
    ),
    4: "A limit that nothing checks (unverified).",
    5: "A statement (`test_alpha`, `test_beta`).",
    6: "A statement (`test_alpha`).",
    7: "A statement (`test_alpha`).",
    8: "A statement (`test_alpha`).",
    9: "A statement (`test_alpha`).",
    10: "- A limit that nothing checks (unverified).",
}


def mini_policy(**changes: str) -> str:
    body = {**MINI_BODY, **{int(k.removeprefix("s")): v for k, v in changes.items()}}
    parts = ["# Storage policy: tracer-evaldb 0.0.1", ""]
    for number, title in TITLES.items():
        parts += [f"## {number}. {title}", "", body[number], ""]
    return "\n".join(parts)


def test_the_statement_rule_accepts_a_correct_policy_and_names_each_fault() -> None:
    known = {"test_alpha", "test_beta"}
    assert statement_problems(mini_policy(), known) == []
    assert statement_problems(mini_policy(s10="None."), known) != []  # a marked sentence unlisted

    def only(change: str, text: str, fragment: str) -> None:
        problems = statement_problems(mini_policy(**{change: text}), known)
        assert any(fragment in p for p in problems), (change, text, problems)

    only("s5", "A statement with nothing behind it.", "neither a test citation nor the marker")
    only("s5", "A statement (`test_missing`).", "is not a test")
    only("s5", "A statement (`test_alpha`) (unverified).", "cited and marked unverified")
    only("s5", "A statement (`test_alpha`). (unverified)", "the marker is a sentence of its own")
    only("s5", "A statement (`test_alpha`). Another with none.", "neither a test citation")
    only("s5", "A statement (`test_alpha`)", "without a final period")
    only("s5", "See `packages/evaldb/no-such-file.md`.", "neither a test citation")
    only("s5", "See `packages/evaldb/README.md` (`test_alpha`).", "pointer sentence carries")
    only("s5", "- A (`test_alpha`).\n- B wrapped\n  but never cited.", "neither a test citation")
    only("s10", "- Something else entirely (unverified).", "the register is")
    only("s10", "- A limit that nothing checks (unverified). Another (unverified).", "register")
    only("s4", "A limit (unverified).\n\nAnother limit (unverified).", "the register is")
    problems = statement_problems(mini_policy(s4="A limit (`test_alpha`).", s10="None."), known)
    assert problems == []  # no marked sentence: the register is exactly "None."
    assert any(
        "no sentence is marked" in p
        for p in statement_problems(mini_policy(s4="A limit (`test_alpha`)."), known)
    )
    # a section may not be missing
    broken = mini_policy().replace("## 6. Digests and the plain profile digest", "## 6. Other")
    assert structure_problems(broken)


def test_the_structure_rule_names_each_fault() -> None:
    version = _package_version()
    good = mini_policy().replace("0.0.1", version)
    assert structure_problems(good) == []
    cases = {
        "title": good.replace("# Storage policy", "# Storage"),
        "ascii": good + "café\n",
        "tab": good.replace("A statement", "A\tstatement", 1),
        "fence": good + "```\n```\n",
        "abbreviation": good.replace("A statement", "A statement e.g. this", 1),
        "table_outside_2": good.replace(
            "A statement (`test_alpha`).\n\n## 7.", "| a |\n| - |\n| 1 |\n\n## 7."
        ),
        "no_table_in_2": good.replace("| 1 | 2 |", "").replace("| a | b |\n| --- | --- |", ""),
        "two_final_newlines": good + "\n",
        "no_final_newline": good.rstrip("\n"),
        "trailing_space": good.replace(
            "A limit that nothing checks (unverified).\n",
            "A limit that nothing checks (unverified). \n",
            1,
        ),
        "section_order": good.replace("## 4. Limits", "## 4. Limits "),
    }
    for label, text in cases.items():
        assert structure_problems(text), label


# ============================================================================= the real policy


def test_storage_policy_exists_with_the_pinned_structure() -> None:
    """The file, its title, the ten sections, ASCII, one table."""
    assert structure_problems(policy_text()) == []


def test_every_statement_of_the_storage_policy_is_tested_or_marked_unverified() -> None:
    """P2-25: 'every statement is tested or marked (unverified)' (contract section 5). Each
    sentence of sections 1 to 9 cites a test that exists (a backticked ``test_...`` name) or
    carries the marker ``(unverified)``, never both; a sentence that only points (``See `path`.``)
    needs neither; section 10 repeats every marked sentence, in order."""
    problems = statement_problems(policy_text(), docs.collected_test_names())
    assert problems == []


def test_the_storage_policy_cites_a_test_for_each_statement_the_owner_asked_for() -> None:
    """Four citations are pinned by name (contract section 5.3): the parity table sentence, the
    sentence that says the database is not a validation boundary, and the two sentences that say
    the database is not encrypted and that its internal data is personal data."""
    found = sections()

    def citing(number: int, fragment: str) -> list[list[str]]:
        return [
            docs.citations(s)
            for s in docs.statements(found[number].lines)
            if fragment in docs.flat(s)
        ]

    assert any(PARITY_TEST in c for c in citing(2, "parity")), "section 2, a sentence on 'parity'"
    assert any(BOUNDARY_TEST in c for c in citing(3, "validation boundary"))
    assert any(PLAINTEXT_TEST in c for c in citing(5, "not encrypt"))
    assert any(PLAINTEXT_TEST in c for c in citing(5, "personal data"))


# ----------------------------------------------------------------------------- section 2


def canon(cell: str) -> tuple[str, str | None]:
    """(type, collation) of a type text: lower case, quotes removed, white space collapsed, the two
    Postgres spellings of a type unified."""
    text = re.sub(r"\s+", " ", cell.replace('"', "").strip().lower())
    kind, _, collation = text.partition(" collate ")
    kind = {"timestamptz": "timestamp with time zone", "varchar": "character varying"}.get(
        kind, kind
    )
    return kind, (collation or None)


def parity_rows() -> dict[str, tuple[str, str]]:
    (table,) = docs.tables(sections()[2].lines)
    assert table[0] == PARITY_HEADER, table[0]
    rows: dict[str, tuple[str, str]] = {}
    for row in table[1:]:
        assert len(row) == 3, row
        cells = []
        for cell in row:
            assert re.fullmatch(r"`[^`]+`", cell), f"a cell is one backticked token: {cell!r}"
            cells.append(cell.strip("`"))
        assert cells[0] not in rows, f"duplicate row {cells[0]}"
        rows[cells[0]] = (cells[1], cells[2])
    return rows


def type_text(column_class: Any, dialect: Any) -> str:
    return str(column_class().compile(dialect=dialect))


def test_storage_policy_parity_table_matches_column_types(store: Any, evaldb: ModuleType) -> None:
    """P2-25: the parity table of the policy equals the real column types of both backends.

    The table (section 2) has the header ``Logical type | Postgres | SQLite (STRICT)`` and one row
    for each of the eight column types in the pinned order, every cell one backticked token. Each
    cell is compared (a) with the DDL type SQLAlchemy writes for the column type on each dialect,
    (b) with the DDL type of every real column of the 37 tables, and (c) with what the database
    itself reports for a scratch table of that type on the backend under test: the type, and the
    collation (Postgres ``COLLATE "C"``, SQLite ``COLLATE "BINARY"`` in the table definition)."""
    rows = parity_rows()
    assert tuple(rows) == PARITY_CLASSES
    public = {n for n in evaldb.__all__ if n.endswith("Col")}
    assert public == set(PARITY_CLASSES)

    pg = postgresql.dialect()  # type: ignore[no-untyped-call,unused-ignore]
    lite = sqlite.dialect()  # type: ignore[no-untyped-call,unused-ignore]
    for name, (pg_cell, lite_cell) in rows.items():
        column_class = getattr(evaldb, name)
        assert canon(pg_cell) == canon(type_text(column_class, pg)), (name, "postgres DDL")
        assert canon(lite_cell) == canon(type_text(column_class, lite)), (name, "sqlite DDL")

    classes_in_use = set()
    for table in evaldb.metadata.sorted_tables:
        for column in table.columns:
            name = type(column.type).__name__
            assert name in rows, (table.name, column.name, name)
            classes_in_use.add(name)
            assert canon(rows[name][0]) == canon(str(column.type.compile(dialect=pg))), (
                table.name,
                column.name,
            )
            assert canon(rows[name][1]) == canon(str(column.type.compile(dialect=lite))), (
                table.name,
                column.name,
            )
    assert classes_in_use == set(PARITY_CLASSES), "a row of the table that no column uses"

    # what the database reports, on the backend under test
    columns = [sa.Column(f"c_{n.lower()}", getattr(evaldb, n)()) for n in PARITY_CLASSES]
    table = store.table("parity", sa.Column("pk", sa.Integer, primary_key=True), *columns)
    backend = 0 if store.backend == "postgres" else 1
    for name in PARITY_CLASSES:
        expected_type, expected_collation = canon(rows[name][backend])
        live_type, _ = canon(store.sql_type(table, f"c_{name.lower()}"))
        assert live_type == expected_type, (name, store.backend, live_type)
        if store.backend == "postgres":
            live_collation = store.collation(table, f"c_{name.lower()}")
            assert (None if live_collation is None else live_collation.lower()) == (
                expected_collation
            ), (name, live_collation)
        else:
            with store.engine.connect() as conn:
                sql = conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE name = ?", (table.name,)
                ).scalar_one()
            declared = re.search(rf'c_{name.lower()} +(\w+)( +COLLATE +"?(\w+)"?)?', sql)
            assert declared is not None, (name, sql)
            assert (declared.group(3) or "").lower() == (expected_collation or ""), (name, sql)


def test_the_parity_comparison_has_teeth() -> None:
    """``canon`` unifies the spellings and keeps the differences."""
    assert canon("timestamptz") == canon("TIMESTAMP WITH TIME ZONE")
    assert canon('varchar COLLATE "C"') == canon('character varying  collate "c"')
    assert canon("text") != canon('text COLLATE "C"')
    assert canon("text") != canon("varchar")
    assert canon("BLOB") != canon("bytea")
    assert canon('TEXT COLLATE "BINARY"') == ("text", "binary")


# ----------------------------------------------------------------------------- section 3


def test_storage_policy_states_db_is_not_validation_boundary() -> None:
    """P2-25: section 3 says 'the DB is not a validation boundary, the repository is', says what
    follows (a raw INSERT is accepted, the update triggers refuse only edits), and points to the
    admin check ``verify_database``. The sentence is backed by
    ``test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it``."""
    section = sections()[3]
    text = docs.flat(docs.section_text(section))
    assert "the db is not a validation boundary, the repository is" in text
    assert re.search(r"raw (sql )?insert", text), "it names the raw INSERT"
    assert "trigger" in text
    assert re.search(r"\bupdate\b", text)
    assert "`verify_database`" in docs.section_text(section)
    statement = next(
        s
        for s in docs.statements(section.lines)
        if "the db is not a validation boundary, the repository is" in docs.flat(s)
    )
    assert BOUNDARY_TEST in docs.citations(statement)
    assert not docs.is_unverified(statement)


def test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it(
    db: Db, evaldb: ModuleType
) -> None:
    """The claim of section 3, shown: a raw INSERT of a row the models reject is accepted by the
    database (its constraints are checks of shape, not of the schema), the repository then refuses
    to load it, and ``verify_database`` reports it without a value. The update triggers do not
    stop an INSERT."""
    valid = _support.load_entity("Project", json.dumps(project_doc(new_id("prj"))))
    with db.engine.begin() as conn:
        not_a_project = db.rows.insert(conn, "project", doc='{"not": "a project"}', name="raw-a")
        wrong_hash = db.rows.insert(
            conn,
            "project",
            id=valid.id.uuid,
            created_at=valid.created_at,
            doc=valid.model_dump_json(),
            name=valid.name,
        )  # a document that loads, with doc_sha256 left at the helper's zero bytes
    with db.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM project")).scalar_one() == 2
        conn.rollback()
    for row in (not_a_project, wrong_hash):
        with pytest.raises(evaldb.StoreIntegrityError):
            evaldb.load_project(db.engine, row["id"])
    report = evaldb.verify_database(db.engine)
    assert not report.ok
    found = {(f.kind, f.problem) for f in report.findings}
    assert ("project", "doc_invalid") in found
    assert ("project", "doc_sha256_mismatch") in found
    assert report.checked["project"] == 2
    shown = repr(report) + "".join(repr(f) for f in (*report.findings, *report.informational))
    assert "raw-a" not in shown


# ----------------------------------------------------------------------------- section 4

LIMIT_FACTS: dict[str, Callable[[str], bool]] = {
    "stored integers": lambda t: bool(re.search(r"2\*\*53|9007199254740991", t)),
    "SQLite minimum": lambda t: "3.37" in t,
    "Postgres minimum": lambda t: "postgres 15" in t,
    "encoding": lambda t: "utf8" in t,
    "U+0000": lambda t: "u+0000" in t,
    "instant years": lambda t: "0001" in t and "9999" in t,
    "document size": lambda t: f"{ev_loader.DEFAULT_MAX_BUNDLE_BYTES // 2**20} mib" in t,
}


def test_storage_policy_limits_state_the_numbers_of_the_code() -> None:
    """Section 4 names each limit with its number: the stored integer range (2**53 - 1), SQLite
    3.37, Postgres 15, UTF8, the place of U+0000, the years 0001 to 9999 and the size of a document
    (``tracer_evalschema.loader.DEFAULT_MAX_BUNDLE_BYTES``, in MiB). Each limit is a statement that
    is tested or marked like any other."""
    text = docs.flat(docs.section_text(sections()[4]))
    missing = [name for name, present in LIMIT_FACTS.items() if not present(text)]
    assert missing == [], missing
    assert ev_loader.DEFAULT_MAX_BUNDLE_BYTES == 256 * 1024 * 1024  # the number the policy states


# ----------------------------------------------------------------------------- section 5


def test_storage_policy_states_the_database_is_not_encrypted_at_rest() -> None:
    """Section 5: tracer does not encrypt the database, so the stored internal data (which is
    personal data) is as readable as the file or the server volume; Postgres storage is the
    operator's, and that sentence is marked (unverified) because nothing in CI can see a server
    disk. The sentence 'not encrypted' is backed by ``test_the_sqlite_file_holds_documents_in_plain
    _text``."""
    section = sections()[5]
    text = docs.flat(docs.section_text(section))
    for fragment in ("not encrypt", "at rest", "personal data", "internal data"):
        assert fragment in text, fragment
    postgres = [s for s in docs.statements(section.lines) if "postgres" in docs.flat(s)]
    assert postgres
    assert any(docs.is_unverified(s) for s in postgres)
    sealed = [s for s in docs.statements(section.lines) if "not encrypt" in docs.flat(s)]
    assert any(PLAINTEXT_TEST in docs.citations(s) for s in sealed)


def test_the_sqlite_file_holds_documents_in_plain_text(sqlite_db: Db, evaldb: ModuleType) -> None:
    """The claim of section 5, shown on SQLite: a stored name and document are readable as plain
    bytes in the database file, so is the name, birth date and account number of a stored test
    profile (personal data), the file starts with the standard header, and ``create_store_engine``
    has no parameter for a key, a password or a cipher."""
    marker = "PLAINTEXT-AT-REST-6d1f42"
    document = project_doc(new_id("prj"))
    document["name"] = marker
    document["metadata"] = {"note": marker + "-doc"}
    project = _support.load_entity("Project", json.dumps(document))
    evaldb.store_project(sqlite_db.engine, project)
    profile = _support.load_golden("TestProfile", "maximal")  # a person's name, birth date, account
    evaldb.store_definition(sqlite_db.engine, profile)
    sqlite_db.engine.dispose()
    path = Path(sqlite_db.engine.url.database or "")
    data = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        data += wal.read_bytes()
    assert data.startswith(b"SQLite format 3\x00")
    assert marker.encode() in data
    assert (marker + "-doc").encode() in data
    for personal in (profile.name, profile.dob, profile.account_number):
        assert personal.encode("utf-8") in data, "a profile field is readable in the file"
    parameters = inspect.signature(evaldb.create_store_engine).parameters
    forbidden = re.compile(r"key|pass|secret|cipher|crypt|token|pragma", re.IGNORECASE)
    assert [n for n in parameters if forbidden.search(n)] == []


# ----------------------------------------------------------------------------- section 6


def test_storage_policy_states_the_plain_profile_digest_limitation() -> None:
    """Section 6 repeats, for the store, what SCHEMA_POLICY says of ADR 0006 Decision 6: a plain
    SHA-256 digest of a low-entropy profile can be brute-forced; the store keeps internal data,
    which carries the plain digest (a snapshot with an absent or keyed digest is refused:
    ``InternalSnapshotRefused``); a shared export drops or keys it. It cites ADR 0006 Decision 6."""
    section = sections()[6]
    raw = docs.section_text(section)
    text = docs.flat(raw)
    assert re.search(r"low[- ]entropy", text)
    assert re.search(r"brute[- ]?forc", text)
    assert "internal" in text
    assert re.search(r"plain (sha-?256|digest)", text)
    assert re.search(r"shared export", text)
    assert re.search(r"(drop|omit|absent)", text)
    assert re.search(r"(keyed|hmac)", text)
    assert "adr 0006" in text
    assert "decision 6" in text
    assert "`InternalSnapshotRefused`" in raw


# ----------------------------------------------------------------------------- section 7

DELETION_FUNCTIONS = (
    "delete_run",
    "delete_project",
    "delete_test_profile",
    "delete_definition",
    "delete_dataset_item",
    "delete_export",
    "delete_snapshot",
)
DELETION_REASONS = ("operator_request", "retention_policy", "data_subject_request")


def test_storage_policy_covers_deletion_and_audit() -> None:
    """Section 7 names the seven deletion roots, ``deletion_audit`` (append-only, no content,
    ``actor`` and ``reason`` required, the three reasons), the cascade, the incomplete bundles a
    deletion leaves (``IncompleteRunBundle``) and that retention is recorded, not enforced."""
    raw = docs.section_text(sections()[7])
    text = docs.flat(raw)
    ticked = set(docs.ticks(raw))
    for name in (*DELETION_FUNCTIONS, *DELETION_REASONS, "deletion_audit", "actor", "reason"):
        assert name in ticked, name
    assert "`IncompleteRunBundle`" in raw
    assert "append-only" in text
    assert re.search(r"(no|without) (stored )?content|content-free", text)
    assert "cascade" in text
    assert re.search(r"retention (is )?(recorded|only recorded)", text)
    assert "not enforced" in text


# ----------------------------------------------------------------------------- section 8


def test_storage_policy_points_to_the_migration_policy() -> None:
    """Section 8 points to ADR 0021 by path (the pointer sentence is checked to exist), and says
    the store is never migrated by opening it: ``open_store`` refuses, ``upgrade`` and
    ``downgrade`` are explicit, a downgrade that would lose rows needs ``allow_data_loss``."""
    section = sections()[8]
    raw = docs.section_text(section)
    pointers = [s for s in docs.statements(section.lines) if docs.is_pointer(s)]
    assert any(ADR_0021 in docs.ticks(s) for s in pointers), "See `<path of ADR 0021>`."
    ticked = set(docs.ticks(raw))
    for name in ("open_store", "upgrade", "downgrade", "allow_data_loss", "StoreNotMigrated"):
        assert name in ticked, name
    assert re.search(r"never migrat", docs.flat(raw))
    assert "tracer_meta" in ticked


# ----------------------------------------------------------------------------- section 9


def test_storage_policy_names_the_phase5_risks() -> None:
    """Section 9 carries the risks Phase 5 inherits: the pg8000 store time of a large bundle
    (P2-F4, ``executemany``, with no timing threshold), concurrent writers, and the digest-changing
    upgrade that cannot be done in place."""
    raw = docs.section_text(sections()[9])
    text = docs.flat(raw)
    assert "P2-F4" in raw
    assert "executemany" in text
    assert "concurren" in text
    assert "in place" in text
    assert re.search(r"no (timing )?threshold", text)


# ------------------------------------------------------------------------------ the register


def test_the_register_of_unverified_statements_matches_the_marked_sentences() -> None:
    """Section 10 lists every marked sentence of sections 1 to 9, in order, once each (the rule
    is part of ``statement_problems``; this test says it on its own so a failure names it)."""
    found = sections()
    marked = [
        docs.normalized(s)
        for n in range(1, REGISTER)
        for s in docs.statements(found[n].lines)
        if docs.is_unverified(s)
    ]
    entries = [docs.normalized(b) for b in docs.blocks(found[REGISTER].lines)]
    assert entries == (marked or ["None."])


def test_the_policy_text_names_no_machine_path_and_no_key() -> None:
    """The policy is read by users: no home directory, no tool scratch path, no key-shaped value."""
    text = policy_text()
    for fragment in ("/Users/", "/home/", "claude-502", "\\", "sk-", "AKIA", "BEGIN PRIVATE"):
        assert fragment not in text, fragment
    prose = re.sub(r"`[^`]*`", "", text)  # names of tests and paths are long and not keys
    assert not re.search(r"[A-Za-z0-9+/_-]{40,}", prose), "a long token-shaped string"
