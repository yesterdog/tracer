# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch H docs tests (concept page, measurement reports).

Contract: docs/phase3/api-surface-batch-h.md. Nothing here imports a Tracer package: the checks
read Markdown files, ``docs/BACKLOG.md`` and the AST of ``packages/*/tests`` only. The deny-list of
ADR 0023 rule 1 is applied to a folded copy of the text (NFKC, format characters removed, white
space collapsed), so full-width letters, zero-width characters and line breaks cannot hide a word.
"""

from __future__ import annotations

import ast
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
BACKLOG = ROOT / "docs" / "BACKLOG.md"
CONCEPTS = ROOT / "docs" / "concepts"
MEASUREMENTS = ROOT / "docs" / "phase3" / "measurements"

#: ADR 0023 rule 1, verbatim: the one sentence that may name the deny-listed words.
NOT_CLAIMED_SENTENCE = (
    "Tracer reports how far two transcripts agree; it does not measure accuracy, and none of "
    "its numbers is a WER, a CER, an error rate, a recall or a precision."
)

#: ADR 0023 rule 1: whole words, case-insensitive. ``error rate`` also catches a hyphen or an
#: underscore between the two words.
DENY = re.compile(r"\b(?:accuracy|wer|cer|error[\s_-]+rate|correct|recall|precision)\b", re.I)

BL_ID = re.compile(r"\bBL-\d{3}\b")
TEST_NAME = re.compile(r"`(test_\w+)`")
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^(#{1,6}) (.+?)[ \t]*$", re.M)
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)")


def fold(text: str) -> str:
    """NFKC, format characters (zero-width joiners, soft hyphen, bidi marks) removed, white space
    collapsed to single spaces."""
    normal = unicodedata.normalize("NFKC", text)
    kept = "".join(c for c in normal if unicodedata.category(c) != "Cf")
    return re.sub(r"\s+", " ", kept)


def deny_hits(text: str) -> list[str]:
    return [m.group(0) for m in DENY.finditer(fold(text))]


def deny_hits_outside_sentence(text: str) -> tuple[list[str], int]:
    """``(hits outside the verbatim sentence, number of times the sentence occurs)``."""
    folded = fold(text)
    sentence = fold(NOT_CLAIMED_SENTENCE)
    count = folded.count(sentence)
    rest = folded.replace(sentence, " ")
    return [m.group(0) for m in DENY.finditer(rest)], count


def backlog_ids() -> set[str]:
    return set(re.findall(r"^\| (BL-\d{3}) \|", BACKLOG.read_text("utf-8"), flags=re.M))


def defined_tests() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("packages/*/tests/test_*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(node.name)
    return names


def sections(text: str) -> dict[str, str]:
    """``{heading of a '## ' section: its body}`` (the body runs to the next '## ' heading)."""
    found: dict[str, str] = {}
    # a line inside a fenced block is never a heading: look for headings in a masked copy
    masked = FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    matches = [m for m in HEADING.finditer(masked) if len(m.group(1)) == 2]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found[match.group(2)] = text[match.end() : end]
    return found


def h2_headings(text: str) -> list[str]:
    """The '## ' headings in order, ignoring lines inside fenced blocks."""
    without_fences = FENCE.sub("", text)
    return [m.group(2) for m in HEADING.finditer(without_fences) if len(m.group(1)) == 2]


def strip_fences(text: str) -> str:
    return FENCE.sub("", text)


def plain(text: str) -> str:
    """Markdown emphasis and code marks removed."""
    return text.replace("*", "").replace("`", "")


def units(text: str) -> list[str]:
    """Statements: one per sentence of a paragraph, per bullet, per table row.

    Lines of one paragraph are joined; a bullet, a numbered item, a table row or a heading starts
    a new unit; a paragraph is then split after '.', '!' or '?' followed by white space.
    """
    result: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            joined = " ".join(paragraph)
            result.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", joined) if s.strip())
            paragraph.clear()

    for raw in plain(text).splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif re.match(r"(?:[-+] |\d+\. |\||#)", line):
            flush()
            result.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
        else:
            paragraph.append(line)
    flush()
    return result


@dataclass(frozen=True)
class Requirement:
    """A statement a document must make: some unit of ``section`` matches every pattern of at
    least one alternative (case-insensitive ``re.search``)."""

    key: str
    section: str
    alternatives: tuple[tuple[str, ...], ...]


def unit_satisfies(unit: str, alternatives: tuple[tuple[str, ...], ...]) -> bool:
    return any(all(re.search(p, unit, re.I) for p in alt) for alt in alternatives)


def unmet(
    body_by_section: dict[str, str], requirements: tuple[Requirement, ...]
) -> list[Requirement]:
    missing = []
    for req in requirements:
        body = strip_fences(body_by_section.get(req.section, ""))
        if not any(unit_satisfies(u, req.alternatives) for u in units(body)):
            missing.append(req)
    return missing
