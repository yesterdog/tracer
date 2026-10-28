# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared checker for the Phase 4 audit documents (SCOPE P4-32 and P4-J26).

``docs/security/P4-32-audit.md`` and ``docs/security/P4-J-audit.md`` list their findings in a
table whose rows start with an id (``A-F1``, ``J-F1``, ``N-1`` ...). The checker reads the
document, ``docs/BACKLOG.md`` and the AST of ``packages/*/tests/test_*.py`` and refuses a document
that has drifted from the code: every finding has an id, a severity and a status (``fixed in
<commit>``, ``open``, ``deferred`` or ``accepted residual``); a fixed finding cites a test that is
defined exactly once; any other finding cites a BACKLOG row that exists; the BACKLOG status agrees
with the audit status; an open P0 or P1 is named in the GO / NO-GO section; the verdict, the
required sections and the required words are present; and the text holds no secret, no private
path, no language outside the supported nine and no file stem. Files are read only; nothing is
imported from the product. The two test modules run the checker on the real documents, on a
synthetic one and on mutants of both, so that the checker has teeth.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _root()
BACKLOG = ROOT / "docs" / "BACKLOG.md"

ROW = re.compile(r"^\| ([A-Z][A-Za-z]*-[A-Za-z]*\d+) \|")
TEST_NAME = re.compile(r"`(test_\w+)`")
BL_ID = re.compile(r"BL-\d{3}")
FIXED = re.compile(r"^fixed in ([0-9a-f]{7,40})\b")
#: a hex-looking token of 8 or more characters with both a digit and a letter: a file stem or a
#: hash; a commit id of 7 characters in a "fixed in" cell is outside this rule by its length
HEX_TOKEN = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{8,}\b")
SEVERITIES = ("P0", "P1", "P2", "P3")
ID_COLUMN, SEVERITY_COLUMN, STATUS_COLUMN, TRACKED_COLUMN = 0, 2, 3, 5
CELLS = 6
LINE_CAP = 600

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bsk_[A-Za-z0-9]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\b[A-Z_]*(?:API_KEY|TOKEN|SECRET)\s*=\s*[^\s`'\"]{6,}"),
)
PRIVATE_PATH_NEEDLES = (
    "/Users/",
    "/home/",
    "/private/",
    "/var/folders",
    "/tmp/",  # noqa: S108 - a needle to refuse, not a path used
    "~/",
    "claude-502",
    "-Users-",
    "pytest-of-",
    "fixtures/private",
    "tracer-runs",
    "purge-request",
    "Desktop",
    "C:\\",
)
#: languages that are not among the nine supported ones (English, Hindi, Marathi, Telugu, Tamil,
#: Kannada, Malayalam, Punjabi in Gurmukhi, Gujarati)
OUT_OF_SCOPE_LANGUAGES = (
    "bengali",
    "bangla",
    "odia",
    "oriya",
    "urdu",
    "assamese",
    "nepali",
    "sanskrit",
    "konkani",
    "kashmiri",
    "sindhi",
    "maithili",
    "dogri",
    "santali",
    "bodo",
    "manipuri",
)


@dataclass(frozen=True)
class AuditSpec:
    """What one audit document must contain."""

    path: Path
    ids: tuple[str, ...]
    verdict_heading: str
    verdict_words: tuple[str, ...]
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    must_cite_tests: tuple[tuple[str, tuple[str, ...]], ...] = ()
    p0_line: str = "P0:** none"


def defined_tests() -> Counter[str]:
    names: Counter[str] = Counter()
    for path in sorted(ROOT.glob("packages/*/tests/test_*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names[node.name] += 1
    return names


def backlog_statuses(text: str) -> dict[str, str]:
    """``{BL id: status cell}`` of every BACKLOG row (the status is the last cell)."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\| (BL-\d{3}) \|", line)
        if match is not None:
            found[match.group(1)] = line.rstrip().rstrip("|").rsplit("|", 1)[-1].strip()
    return found


def rows_of(text: str) -> list[tuple[str, list[str], str]]:
    """``(id, cells, raw line)`` of every finding row; the cells are stripped."""
    found = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match is not None:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            found.append((match.group(1), cells, line))
    return found


def section(text: str, title: str) -> str | None:
    """The text of the ``## ...`` section whose heading contains ``title``."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## ") and title in line:
            end = next(
                (i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), None
            )
            return "\n".join(lines[index : end if end is not None else len(lines)])
    return None


def status_kind(status: str) -> str:
    lowered = status.lower()
    if FIXED.match(lowered):
        return "fixed"
    for kind in ("open", "deferred", "accepted residual"):
        if lowered == kind or lowered.startswith(kind + " ") or lowered.startswith(kind + ":"):
            return kind
    return "unknown"


def problems(spec: AuditSpec, text: str, tests: Counter[str], backlog: dict[str, str]) -> list[str]:
    """Every way ``text`` breaks the rules of the module docstring; empty when it holds."""
    found: list[str] = []
    rows = rows_of(text)
    ids = [r[0] for r in rows]
    if ids != list(spec.ids):
        found.append(f"ids are {ids}, not {list(spec.ids)} once each in order")
    verdict = section(text, spec.verdict_heading.removeprefix("## "))
    for finding, cells, raw in rows:
        if len(cells) != CELLS:
            found.append(f"{finding} has {len(cells)} cells, not {CELLS}")
            continue
        severity = cells[SEVERITY_COLUMN]
        status = cells[STATUS_COLUMN]
        kind = status_kind(status)
        if severity not in SEVERITIES:
            found.append(f"{finding} has an unknown severity {severity!r}")
        if kind == "unknown":
            found.append(f"{finding} has an unknown status {status!r}")
        cited_tests = TEST_NAME.findall(raw)
        cited_bl = BL_ID.findall(raw)
        if not cited_tests and not cited_bl:
            found.append(f"{finding} cites no test and no BACKLOG row")
        for name in cited_tests:
            if tests[name] == 0:
                found.append(f"{finding} cites an undefined test {name}")
            elif tests[name] > 1:
                found.append(f"{finding} cites an ambiguous test {name} (defined {tests[name]}x)")
        for bl in cited_bl:
            if bl not in backlog:
                found.append(f"{finding} cites a BACKLOG row that does not exist: {bl}")
        if kind == "fixed" and not any(tests[n] == 1 for n in cited_tests):
            found.append(f"{finding} is fixed but cites no existing test")
        if kind in ("open", "deferred", "accepted residual") and not cited_bl:
            found.append(f"{finding} is {kind} but cites no BACKLOG row")
        primary = BL_ID.findall(cells[TRACKED_COLUMN])
        if primary and primary[0] in backlog:
            closed = backlog[primary[0]].lower().startswith("closed")
            if kind == "fixed" and not closed:
                found.append(f"{finding} is fixed but {primary[0]} is not closed in the BACKLOG")
            if kind in ("open", "deferred", "accepted residual") and closed:
                found.append(f"{finding} is {kind} but {primary[0]} is closed in the BACKLOG")
        if kind == "open" and severity in ("P0", "P1"):
            named = verdict is not None and (
                finding in verdict or any(bl in verdict for bl in cited_bl)
            )
            if not named:
                found.append(f"{finding} is an open {severity} but the verdict does not name it")
    for finding, names in spec.must_cite_tests:
        raw = next((r[2] for r in rows if r[0] == finding), "")
        for name in names:
            if name not in TEST_NAME.findall(raw):
                found.append(f"{finding} does not cite {name}")
    for bl in sorted(set(BL_ID.findall(text)) - set(backlog)):
        if not any(bl in p for p in found):
            found.append(f"the audit cites a BACKLOG row that does not exist: {bl}")
    if spec.verdict_heading not in text:
        found.append("the verdict heading is missing")
    elif verdict is not None:
        for word in spec.verdict_words:
            if word not in verdict:
                found.append(f"the verdict does not hold {word!r}")
    if spec.p0_line not in text:
        found.append("the P0 line is missing")
    for title, words in spec.sections:
        body = section(text, title)
        if body is None:
            found.append(f"the {title!r} section is missing")
            continue
        for word in words:
            if word not in body:
                found.append(f"the {title!r} section does not hold {word!r}")
    found.extend(content_problems(text))
    return found


def content_problems(text: str) -> list[str]:
    """Secrets, private paths, languages outside the supported nine, file stems, long lines."""
    found: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            found.append(f"a secret-shaped string matches {pattern.pattern[:30]!r}")
    for needle in PRIVATE_PATH_NEEDLES:
        if needle in text:
            found.append(f"a private path fragment: {needle}")
    lowered = text.lower()
    for language in OUT_OF_SCOPE_LANGUAGES:
        if re.search(rf"\b{language}\b", lowered):
            found.append(f"a language outside the supported nine: {language}")
    for token in HEX_TOKEN.findall(text):
        found.append(f"a hex token outside a commit cell: {token}")
    for number, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000:
            found.append(f"line {number} is {len(line)} characters long")
    if len(text.splitlines()) > LINE_CAP:
        found.append(f"the document has more than {LINE_CAP} lines")
    return found


# ------------------------------------------------------------------------------ teeth


def synthetic(spec: AuditSpec) -> tuple[str, Counter[str], dict[str, str]]:
    """A small document that satisfies ``spec``, with its fake tests and BACKLOG."""
    tests: Counter[str] = Counter({"test_fixed_one": 1, "test_fixed_two": 1})
    backlog = {
        "BL-901": "closed: fixed in abcdef1",
        "BL-902": "open",
        "BL-903": "accepted residual (documented)",
    }
    for word in (*spec.verdict_words, *(w for _, words in spec.sections for w in words)):
        for bl in BL_ID.findall(word):
            backlog.setdefault(bl, "open")
    rows = []
    for index, finding in enumerate(spec.ids):
        slot = index % 4
        if slot == 0:
            rows.append(f"| {finding} | f | P2 | fixed in abcdef1 | x | `test_fixed_one`; BL-901 |")
        elif slot == 1:
            rows.append(f"| {finding} | f | P3 | deferred | x | BL-902 |")
        elif slot == 2:
            rows.append(f"| {finding} | f | P2 | accepted residual | x | BL-903 |")
        else:
            rows.append(f"| {finding} | f | P1 | open | x | BL-902 |")
    for finding, names in spec.must_cite_tests:
        for index, row in enumerate(rows):
            if row.startswith(f"| {finding} |"):
                extra = "; ".join(f"`{n}`" for n in names)
                cells = row.strip().strip("|").split("|")
                cells[TRACKED_COLUMN] = f" {extra}; BL-901 "
                cells[STATUS_COLUMN] = " fixed in abcdef1 "
                cells[SEVERITY_COLUMN] = " P2 "
                rows[index] = "|" + "|".join(cells) + "|"
                for name in names:
                    tests[name] = 1
    open_p1 = [r.split("|")[1].strip() for r in rows if "| P1 | open |" in r]
    lines = [
        "# audit",
        spec.verdict_heading,
        f"- **{spec.p0_line}.",
        "- " + " ".join(spec.verdict_words) + " " + " ".join(open_p1),
        "## Findings",
        "| id | finding | severity | status | fix | pinned by / tracked in |",
        *rows,
    ]
    for title, words in spec.sections:
        lines.extend([f"## {title}", "- " + " ".join(words)])
    return "\n".join(lines), tests, backlog


def generic_mutants(spec: AuditSpec, text: str) -> list[tuple[str, Callable[[str], str], str]]:
    """Mutations that must each be refused, built from the first matching row of ``text``."""
    rows = rows_of(text)

    fixed_row = next(
        r
        for r in rows
        if status_kind(r[1][STATUS_COLUMN]) == "fixed" and BL_ID.findall(r[1][TRACKED_COLUMN])
    )
    deferred_row = next(r for r in rows if status_kind(r[1][STATUS_COLUMN]) == "deferred")
    open_row = next(
        (
            r
            for r in rows
            if status_kind(r[1][STATUS_COLUMN]) == "open" and r[1][SEVERITY_COLUMN] == "P1"
        ),
        None,
    )
    fixed_test = TEST_NAME.findall(fixed_row[2])[0]
    deferred_line = deferred_row[2]
    fixed_line = fixed_row[2]
    mutants: list[tuple[str, Callable[[str], str], str]] = [
        (
            "drop a row",
            lambda t: "\n".join(x for x in t.splitlines() if x != fixed_line),
            "ids are",
        ),
        (
            "rename a cited test",
            lambda t: t.replace(f"`{fixed_test}`", "`test_renamed_by_a_mutant`"),
            "undefined test",
        ),
        (
            "an unknown BACKLOG id",
            lambda t: t.replace(BL_ID.findall(deferred_line)[0], "BL-998", 1),
            "does not exist",
        ),
        (
            "a deferred row with a fixed status and no test",
            lambda t: t.replace(
                deferred_line,
                deferred_line.replace(deferred_row[1][STATUS_COLUMN], "fixed in 8f780a8", 1),
                1,
            ),
            "fixed but cites no existing test",
        ),
        (
            "a fixed row with no commit",
            lambda t: t.replace(
                fixed_line, fixed_line.replace(fixed_row[1][STATUS_COLUMN], "fixed", 1), 1
            ),
            "unknown status",
        ),
        (
            "an invented status",
            lambda t: t.replace(
                deferred_line,
                deferred_line.replace(deferred_row[1][STATUS_COLUMN], "wontfix", 1),
                1,
            ),
            "unknown status",
        ),
        (
            "an unknown severity",
            lambda t: t.replace(
                fixed_line, fixed_line.replace(fixed_row[1][SEVERITY_COLUMN], "P9", 1), 1
            ),
            "unknown severity",
        ),
        (
            "a fixed row whose BACKLOG row is open",
            lambda t: t.replace(
                fixed_line,
                fixed_line.replace(
                    BL_ID.findall(fixed_row[1][TRACKED_COLUMN])[0], deferred_line_bl(deferred_line)
                ),
                1,
            ),
            "not closed in the BACKLOG",
        ),
        (
            "the verdict heading changed",
            lambda t: t.replace(spec.verdict_heading, "## Verdict"),
            "verdict heading",
        ),
        ("no P0 line", lambda t: t.replace(spec.p0_line, "P0:** one"), "P0 line"),
        ("a secret", lambda t: t + "\n- key sk-or-v1abcdef0123456789\n", "secret-shaped"),
        (
            "a bearer value",
            lambda t: t + "\n- Authorization Bearer abcdefghijklmnop12\n",
            "secret-shaped",
        ),
        ("a private path", lambda t: t + "\n- see /Users/someone/work\n", "private path"),
        ("a home path", lambda t: t + "\n- see ~/notes\n", "private path"),
        ("a language outside the nine", lambda t: t + "\n- Odia samples\n", "language outside"),
        ("a file stem", lambda t: t + "\n- the stem a1b2c3d4 of a file\n", "hex token"),
    ]
    for title, _words in spec.sections:
        mutants.append(
            (
                f"section {title!r} renamed",
                _rename_section(title),
                "section is missing",
            )
        )
    for word in spec.verdict_words[:3]:
        mutants.append(
            (
                f"verdict without {word!r}",
                _without_word(spec.verdict_heading, word),
                "the verdict does not hold",
            )
        )
    if open_row is not None:
        open_line = open_row[2]
        mutants.append(
            (
                "an open P1 the verdict does not name",
                lambda t: _unname(t, spec, open_row[0], BL_ID.findall(open_line)),
                "does not name it",
            )
        )
    return mutants


def _rename_section(title: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return text.replace(f"## {title}", "## Other", 1)

    return edit


def _without_word(heading: str, word: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return _drop_in_verdict(text, heading, word)

    return edit


def deferred_line_bl(line: str) -> str:
    return str(BL_ID.findall(line)[0])


def _drop_in_verdict(text: str, heading: str, word: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    end = len(text) if end == -1 else end
    return text[:start] + text[start:end].replace(word, "") + text[end:]


def _unname(text: str, spec: AuditSpec, finding: str, bl_ids: list[str]) -> str:
    start = text.index(spec.verdict_heading)
    end = text.find("\n## ", start + 1)
    end = len(text) if end == -1 else end
    body = text[start:end].replace(finding, "X")
    for bl in bl_ids:
        body = body.replace(bl, "BL-000")
    return text[:start] + body + text[end:]
