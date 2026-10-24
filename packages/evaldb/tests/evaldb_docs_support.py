# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers of the Phase 2 batch F documentation tests (P2-25): a reader of the storage policy.

Contract: docs/phase2/api-surface-batch-f.md sections 4 to 7. This module is imported by
``test_evaldb_storage_policy.py`` and ``test_evaldb_docs.py`` (``from evaldb_docs_support import
...``; the tests directory has no ``__init__.py``). It holds no test.

It reads Markdown the plain way the documents are written: ATX headings, pipe tables, list items
that start at the left margin with ``- `` or ``N. ``, wrapped lines, backticks. It knows nothing
else about Markdown and the documents must not need more.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
EVALDB = ROOT / "packages" / "evaldb"
EVALSCHEMA = ROOT / "packages" / "evalschema"
STORAGE_POLICY = EVALDB / "STORAGE_POLICY.md"
CHANGELOG = EVALDB / "CHANGELOG.md"
README = EVALDB / "README.md"
CONCEPT = ROOT / "docs" / "concepts" / "storage.md"
CONCEPTS_INDEX = ROOT / "docs" / "concepts" / "README.md"
SCHEMA_POLICY = EVALSCHEMA / "SCHEMA_POLICY.md"
TEST_DIRS = (EVALDB / "tests", EVALSCHEMA / "tests")

MARKER = "(unverified)"
ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.")


# --------------------------------------------------------------------------- headings, sections


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    lines: tuple[str, ...]  # the lines below the heading, up to the next H2


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def headings(text: str, level: int) -> list[str]:
    """The text after ``#`` * level and one space, of every heading of exactly that level."""
    prefix = "#" * level + " "
    return [line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix)]


def numbered_sections(text: str) -> list[Section]:
    """The H2 sections ``## N. Title`` in order. A line before the first H2 is not in any."""
    found: list[Section] = []
    current: tuple[int, str] | None = None
    body: list[str] = []
    for line in text.splitlines():
        match = re.fullmatch(r"## (\d+)\. (.+)", line)
        if match is not None:
            if current is not None:
                found.append(Section(current[0], current[1], tuple(body)))
            current = (int(match.group(1)), match.group(2))
            body = []
        elif line.startswith("## "):
            raise AssertionError(f"an H2 heading without a number: {line!r}")
        elif current is not None:
            body.append(line)
    if current is not None:
        found.append(Section(current[0], current[1], tuple(body)))
    return found


def section_text(section: Section) -> str:
    return "\n".join(section.lines)


def flat(text: str) -> str:
    """Lower case, white space collapsed to single spaces."""
    return re.sub(r"\s+", " ", text).strip().lower()


def ticks(text: str) -> list[str]:
    """Every backticked token of a text, in order."""
    return re.findall(r"`([^`]*)`", text)


# ------------------------------------------------------------------------------------- tables


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def tables(lines: tuple[str, ...] | list[str]) -> list[list[list[str]]]:
    """Every pipe table of the lines: a list of rows (header first, separator dropped)."""
    found: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in lines:
        if line.startswith("|"):
            current.append(split_row(line))
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return [[table[0], *table[2:]] for table in found if len(table) >= 2]


# ------------------------------------------------------------------------------- statements


def blocks(lines: tuple[str, ...] | list[str]) -> list[str]:
    """The paragraphs and list items of a section as single lines of text (list marker removed,
    wrapped lines joined with one space). Headings, blank lines and table rows are not blocks."""
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(" ".join(part.strip() for part in current).strip())
            current.clear()

    for line in lines:
        if not line.strip() or line.startswith(("|", "#")):
            flush()
        elif re.match(r"(- |\d+\. )", line):
            flush()
            current.append(re.sub(r"^(- |\d+\. )", "", line))
        else:
            current.append(line)  # a continuation line, or the first line of a paragraph
    flush()
    return out


_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`(\"*])")


def sentences(block: str) -> list[str]:
    """A block split into sentences: after ``.``, ``!`` or ``?`` followed by white space and a
    capital letter, a backtick, an opening bracket, a quote or an asterisk. The documents do not
    use abbreviations with periods (``ABBREVIATIONS`` is a test)."""
    return [part.strip() for part in _SPLIT.split(block) if part.strip()]


def statements(lines: tuple[str, ...] | list[str]) -> Iterator[str]:
    for block in blocks(lines):
        yield from sentences(block)


def citations(sentence: str) -> list[str]:
    """The backticked tokens of a sentence that are test names (``test_`` and word characters)."""
    return [t for t in ticks(sentence) if re.fullmatch(r"test_\w+", t)]


def is_unverified(sentence: str) -> bool:
    return MARKER in sentence


def is_pointer(sentence: str) -> bool:
    """``See `path`, `path`.``: a sentence that only points. Every backticked token that is not a
    test name is a path that exists (below the repository root, an optional ``#anchor`` ignored),
    and there is at least one. A test name in it is allowed here and is then a fault of the
    sentence rule (a pointer carries no citation)."""
    if not sentence.startswith("See "):
        return False
    paths = [t for t in ticks(sentence) if not re.fullmatch(r"test_\w+", t)]
    return bool(paths) and all((ROOT / p.split("#", 1)[0]).exists() for p in paths)


def normalized(sentence: str) -> str:
    """For comparing a register entry with the sentence it repeats: white space collapsed."""
    return re.sub(r"\s+", " ", sentence).strip()


# --------------------------------------------------------------------------------- test names


def collected_test_names() -> set[str]:
    """Every ``test_*`` function (module level or in a class) of the test directories of both
    packages, found by parsing the files, not by importing them."""
    names: set[str] = set()
    for directory in TEST_DIRS:
        for path in sorted(directory.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ) and node.name.startswith("test_"):
                    names.add(node.name)
    return names
