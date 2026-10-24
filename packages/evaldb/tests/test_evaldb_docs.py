# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch F, P2-25: the CHANGELOG, README, concept page and SCHEMA_POLICY pointer of
tracer-evaldb.

Contract: docs/phase2/api-surface-batch-f.md sections 7 to 9. The storage policy itself is read by
``test_evaldb_storage_policy.py``.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

import evaldb_docs_support as docs  # type: ignore[import-not-found,unused-ignore]

POLICY_PATH = "packages/evaldb/STORAGE_POLICY.md"
CHANGELOG_PATH = "packages/evaldb/CHANGELOG.md"
CONCEPT_PATH = "docs/concepts/storage.md"

# sha256 of the white-space-normalized text of packages/evalschema/SCHEMA_POLICY.md as committed
# at the Phase 2 batch E commit (b50c3cc). The file may differ from it by exactly the pointer
# sentence of section 3 "Database" (contract section 9); a later edit of the file for another
# reason is a reviewed change of this constant, with the reviewer's approval.
SCHEMA_POLICY_BASELINE = "c29df495a49fb34a7966d676fb993e5276cbdf928fd1660b1d07ff6184f95e51"


def package_version() -> str:
    data = tomllib.loads(docs.read(docs.EVALDB / "pyproject.toml"))
    found = data["project"]["version"]
    assert isinstance(found, str)
    return found


# ================================================================================ CHANGELOG

CHANGELOG_SECTIONS = ("Package", "Storage", "Migrations", "Findings")
FINDINGS = ("P2-F1", "P2-F2", "P2-F3", "P2-F4")


def changelog_problems(text: str, version: str) -> list[str]:
    problems: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0] != "# Changelog: `tracer-evaldb`":
        problems.append("the first line is not '# Changelog: `tracer-evaldb`'")
    if not text.isascii():
        problems.append("not ASCII")
    entries = docs.headings(text, 2)
    if not entries:
        return [*problems, "no entry"]
    pattern = re.compile(r"(\d+)\.(\d+)\.(\d+) \((unreleased|\d{4}-\d{2}-\d{2})\)")
    parsed = []
    for entry in entries:
        match = pattern.fullmatch(entry)
        if match is None:
            problems.append(f"entry heading {entry!r} is not '<version> (unreleased|<date>)'")
            continue
        parsed.append(tuple(int(match.group(i)) for i in (1, 2, 3)))
    if parsed != sorted(parsed, reverse=True):
        problems.append("entries are not newest first")
    if not entries[0].startswith(f"{version} ("):
        problems.append(f"the newest entry is {entries[0]!r}, the package version is {version}")
    if sum(1 for e in entries if e.startswith(f"{version} (")) != 1:
        problems.append("the version has not exactly one entry")
    body = text.split(f"## {entries[0]}", 1)[1].split("\n## ", 1)[0]
    if docs.headings(body, 3)[: len(CHANGELOG_SECTIONS)] != list(CHANGELOG_SECTIONS):
        problems.append(f"the entry's H3 sections are {docs.headings(body, 3)}")
    for finding in FINDINGS:
        if finding not in body:
            problems.append(f"{finding} is not in the entry")
    if "unreleased" in entries[0] and "Status: not released." not in body:
        problems.append("an unreleased entry says 'Status: not released.'")
    if "STORAGE_POLICY.md" not in body:
        problems.append("the entry does not point to STORAGE_POLICY.md")
    return problems


def test_evaldb_changelog_has_entry_for_version() -> None:
    """P2-25: ``packages/evaldb/CHANGELOG.md`` exists and has an entry for the package version
    (``pyproject.toml`` and the installed distribution agree on it): the newest H2 is
    ``## <version> (unreleased)`` or ``(<date>)``, with the sections Package, Storage, Migrations
    and Findings, the four findings P2-F1 to P2-F4, ``Status: not released.`` while unreleased and
    a pointer to STORAGE_POLICY.md."""
    assert docs.CHANGELOG.is_file(), f"{CHANGELOG_PATH} does not exist yet"
    version = package_version()
    assert installed_version("tracer-evaldb") == version
    assert changelog_problems(docs.read(docs.CHANGELOG), version) == []


def test_the_changelog_rule_names_each_fault() -> None:
    good = (
        "# Changelog: `tracer-evaldb`\n\n## 0.0.2 (2026-11-01)\n\n### Package\n\n### Storage\n\n"
        "### Migrations\n\n### Findings\n\nP2-F1 P2-F2 P2-F3 P2-F4 STORAGE_POLICY.md\n\n"
        "## 0.0.1 (unreleased)\n\nStatus: not released.\n"
    )
    assert changelog_problems(good, "0.0.2") == []
    assert any("package version" in p for p in changelog_problems(good, "0.0.3"))
    faults = {
        "title": good.replace("# Changelog", "# Log", 1),
        "order": good.replace("0.0.1 (unreleased)", "0.0.3 (unreleased)"),
        "heading": good.replace("(2026-11-01)", "(soon)"),
        "section": good.replace("### Storage", "### Stored"),
        "finding": good.replace("P2-F3", "P2-F9"),
        "pointer": good.replace("STORAGE_POLICY.md", "the policy"),
        "ascii": good + "caf\u00e9\n",
    }
    for label, text in faults.items():
        assert changelog_problems(text, "0.0.2"), label
    unreleased = good.replace("## 0.0.2 (2026-11-01)", "## 0.0.2 (unreleased)")
    assert any("not released" in p for p in changelog_problems(unreleased, "0.0.2"))
    assert changelog_problems("", "0.0.2")


# ================================================================================== README

README_SECTIONS = ("Install", "Quick start", "Storage policy", "Security and privacy", "Status")
ALLOWED_IMPORTS = {
    "tracer_evaldb",
    "tracer_evalschema",
    "sqlalchemy",
    "json",
    "pathlib",
    "tempfile",
    "uuid",
    "datetime",
    "os",
}


def readme_sections(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:]
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return found


def python_block(lines: list[str]) -> str:
    """The first fenced block that starts with three backticks and the word python."""
    inside = False
    code: list[str] = []
    for line in lines:
        if not inside and line.strip() == "```python":
            inside = True
        elif inside and line.strip() == "```":
            return "\n".join(code) + "\n"
        elif inside:
            code.append(line)
    raise AssertionError("no ```python block")


def test_evaldb_readme_has_the_documented_sections() -> None:
    """P2-25: the README keeps its earlier content and has the H2 sections Install, Quick start,
    Storage policy, Security and privacy and Status. It names the three documents (backticked
    repository paths that exist), says the store is not encrypted by tracer and that its data is
    personal data, that ``expected_purpose`` is required at both export entry points, and
    states the version and that it is unreleased."""
    text = docs.read(docs.README)
    assert text.isascii()
    found = readme_sections(text)
    missing = [name for name in README_SECTIONS if name not in found]
    assert missing == [], missing
    ticked = set(docs.ticks(text))
    for path in (POLICY_PATH, CHANGELOG_PATH, CONCEPT_PATH):
        assert path in ticked, path
        assert (docs.ROOT / path).is_file(), path
    install = docs.flat("\n".join(found["Install"]))
    assert "pip install" in install
    assert "tracer-evaldb[postgres]" in install
    assert "3.37" in install
    security = docs.flat("\n".join(found["Security and privacy"]))
    for fragment in ("not encrypt", "personal data", "expected_purpose", "never echo"):
        assert fragment in security, fragment
    policy = "\n".join(found["Storage policy"])
    assert POLICY_PATH in policy
    status = docs.flat("\n".join(found["Status"]))
    assert package_version() in status
    assert "unreleased" in status
    # the sections of the earlier README are still there (batches A to E)
    for earlier in ("Repository", "Migrations", "Access-pattern indexes and verification"):
        assert any(name.startswith(earlier) for name in found), earlier


def test_evaldb_readme_quick_start_runs(tmp_path: Path) -> None:
    """The ```python block of the Quick start section runs as written, in an empty directory and
    with a clean interpreter (``-I``): it imports only ``tracer_evaldb``, ``tracer_evalschema``,
    ``sqlalchemy`` and the standard library modules of ``ALLOWED_IMPORTS``, writes a SQLite
    database file below the directory, and leaves a store that ``open_store`` accepts, that
    holds at least one row and that ``verify_database`` finds clean. It needs no key and no
    network."""
    code = python_block(readme_sections(docs.read(docs.README))["Quick start"])
    imported = set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", code, re.MULTILINE))
    assert imported, "the quick start imports nothing"
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS
    assert "tracer_evaldb" in imported
    assert re.search(r"sqlite:///", code)
    assert not re.search(r"https?://|password|api[_-]?key|getenv|environ", code, re.IGNORECASE)
    done = subprocess.run(  # noqa: S603  (our own interpreter, the README's own code)
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    databases = [p for p in files if p.suffix in {".db", ".sqlite", ".sqlite3"}]
    assert len(databases) == 1, files
    check = (
        "import sys, sqlalchemy as sa, tracer_evaldb as e\n"
        "engine = e.create_store_engine('sqlite:///' + sys.argv[1])\n"
        "info = e.open_store(engine)\n"
        "assert info.revision == e.migrate.head_revision(), info\n"
        "with engine.connect() as c:\n"
        "    rows = sum(c.execute(sa.text(f'SELECT count(*) FROM \"{t}\"')).scalar_one()\n"
        "               for t in ('project', 'run', 'persona', 'agent_target', 'dataset_item'))\n"
        "assert rows >= 1, rows\n"
        "report = e.verify_database(engine)\n"
        "assert report.ok and not report.findings, report\n"
    )
    again = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", check, str(databases[0])],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert again.returncode == 0, again.stdout + again.stderr


def test_the_readme_python_block_finder_has_teeth() -> None:
    text = "## A\n\n```text\nno\n```\n\n```python\nprint(1)\n```\n"
    assert python_block(readme_sections(text)["A"]) == "print(1)\n"
    with pytest.raises(AssertionError):
        python_block(["```text", "x", "```"])


# ============================================================================ concept page

CONCEPT_SECTIONS = (
    "What is stored",
    "Documents and projections",
    "Two backends, one behaviour",
    "What the database does not do",
    "Deletion and retention",
    "Versions and migrations",
)
CONCEPT_ADRS = (
    "0019-relational-storage.md",
    "0020-deletion-and-retention.md",
    "0021-store-schema-version-and-migrations.md",
    "0022-write-unit-rescoring-reimport.md",
)


def test_concept_page_storage_exists_and_is_linked() -> None:
    """P2-25: ``docs/concepts/storage.md`` exists in the style of the other concept pages: H1
    ``# Storage``, the six H2 sections of ``CONCEPT_SECTIONS``, relative links to the four ADRs
    that resolve, a pointer to STORAGE_POLICY.md, at least three citations of tests that exist, and
    the two sentences 'not a validation boundary' and 'not encrypted'. ``docs/concepts/README.md``
    lists it in its table."""
    assert docs.CONCEPT.is_file(), f"{CONCEPT_PATH} does not exist yet"
    text = docs.read(docs.CONCEPT)
    assert text.isascii()
    assert text.splitlines()[0] == "# Storage"
    assert docs.headings(text, 2) == list(CONCEPT_SECTIONS)
    links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text)
    for name in CONCEPT_ADRS:
        assert f"../adr/{name}" in links, name
    for target in links:
        if "://" not in target:
            assert (docs.CONCEPT.parent / target).resolve().exists(), target
    assert "STORAGE_POLICY.md" in text
    cited = {t for t in docs.ticks(text) if re.fullmatch(r"test_\w+", t)}
    assert len(cited) >= 3
    assert cited <= docs.collected_test_names(), cited - docs.collected_test_names()
    flat = docs.flat(text)
    assert "not a validation boundary" in flat
    assert "not encrypt" in flat
    index = docs.read(docs.CONCEPTS_INDEX)
    assert "| [storage.md](storage.md) |" in index
    for earlier in ("verdict.md", "versioning.md", "mappings-and-gaps.md"):
        assert f"[{earlier}]({earlier})" in index  # the earlier rows are still there


# ================================================================== SCHEMA_POLICY pointer


def database_bullet(text: str) -> str:
    start = text.index("- **Database.**")
    end = text.index("\n## ", start)
    return text[start:end]


def test_schema_policy_database_bullet_points_to_the_storage_policy() -> None:
    """P2-25: section 3 'Database' of SCHEMA_POLICY.md gains exactly one sentence, the last of the
    bullet, that contains the backticked path ``packages/evaldb/STORAGE_POLICY.md`` (the path
    occurs nowhere else in the file). Apart from that sentence the file is the text of the batch E
    commit, up to white space (``SCHEMA_POLICY_BASELINE``): the Phase 1 policy tests keep reading
    the same document."""
    text = docs.read(docs.SCHEMA_POLICY)
    assert text.count(POLICY_PATH) == 1
    bullet = database_bullet(text)
    found = [s for s in docs.sentences(re.sub(r"\s+", " ", bullet)) if POLICY_PATH in s]
    assert len(found) == 1
    sentence = found[0]
    assert docs.sentences(re.sub(r"\s+", " ", bullet))[-1] == sentence
    assert POLICY_PATH in docs.ticks(sentence)
    assert sentence.endswith(".")
    assert (docs.ROOT / POLICY_PATH).is_file()
    assert sentence.isascii()  # the file itself has three non-ASCII characters from Phase 1
    flat = re.sub(r"\s+", " ", text)
    without = re.sub(r"\s+", " ", flat.replace(sentence, "", 1)).strip()
    assert hashlib.sha256(without.encode()).hexdigest() == SCHEMA_POLICY_BASELINE, (
        "SCHEMA_POLICY.md differs from the committed text by more than the pointer sentence"
    )


def test_the_schema_policy_baseline_is_the_text_without_a_pointer() -> None:
    """The comparison of the test above has teeth: an unrelated edit, a removed word or an extra
    sentence elsewhere changes the digest, and re-wrapping does not."""
    base = docs.read(docs.SCHEMA_POLICY)
    sentence = "The store is described in `packages/evaldb/STORAGE_POLICY.md`."

    def digest(text: str) -> str:
        flat = re.sub(r"\s+", " ", text)
        return hashlib.sha256(
            re.sub(r"\s+", " ", flat.replace(sentence, "", 1)).strip().encode()
        ).hexdigest()

    plain = base.replace(sentence, "")
    with_pointer = plain.replace("(ADR 0009 Decision 6).", "(ADR 0009 Decision 6). " + sentence, 1)
    if POLICY_PATH not in base:  # before the pointer exists the file is the baseline
        assert digest(plain) == SCHEMA_POLICY_BASELINE
    assert digest(with_pointer) == digest(plain)
    assert digest(plain.replace("Phase 2", "Phase two", 1)) != digest(plain)
    assert digest(plain + "An extra sentence.\n") != digest(plain)
    assert digest(plain.replace("\n- **Database.**", "\n\n- **Database.**")) == digest(plain)


# =================================================================================== ASCII


def test_the_documents_of_this_batch_are_ascii_and_free_of_machine_paths() -> None:
    """Every document the batch adds or edits is ASCII, has no tab and no trailing white space at
    the end of a line, names no home directory and holds no key-shaped string. (SCHEMA_POLICY.md
    holds three non-ASCII characters from Phase 1; only its new sentence is checked, above.)"""
    paths = [
        docs.STORAGE_POLICY,
        docs.CHANGELOG,
        docs.README,
        docs.CONCEPT,
        docs.CONCEPTS_INDEX,
    ]
    for path in paths:
        assert path.is_file(), f"{path.relative_to(docs.ROOT)} does not exist yet"
        text = docs.read(path)
        name = path.relative_to(docs.ROOT).as_posix()
        assert text.isascii(), name
        assert "\t" not in text, name
        assert not [i for i, line in enumerate(text.splitlines(), 1) if line != line.rstrip()], name
        for fragment in ("/Users/", "/home/", "claude-502", "BEGIN PRIVATE", "AKIA"):
            assert fragment not in text, (name, fragment)
