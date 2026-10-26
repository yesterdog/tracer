# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""SCOPE P3-31 and ADR 0023 rule 19: the format of a measurement report.

Every file ``docs/phase3/measurements/*.md`` (the template ``TEMPLATE.md`` included) is a
measurement report of the noise-floor measurement (R1, R2 a repeat, R3 the chunking shifted by
half a window, and the offline comparison of the normaliser on and off). The format is fixed so
that a report can be published without a person re-reading it for personal data:

* the nine second-level headings of ``HEADINGS``, in order, and the tables of ``TABLES`` with their
  exact column headers;
* every ratio is written with its numerator and denominator (``N of D``, optionally followed by
  the decimal in parentheses, which must equal N/D); no percent sign; the Jaccard cell repeats the
  two counts of its row; the counts of a row agree with each other and with the clips table;
* clips are named ``clip-A`` or ``clip-B`` and nothing else names a call: no file name, no file
  stem (eight hex digits), no absolute path, no text excerpt (no non-ASCII character at all, no
  fenced block outside 'Not claimed', no blockquote, no long quoted string);
* the deny-list words of ADR 0023 rule 1 appear only in the fenced block of 'Not claimed', in the
  one verbatim sentence;
* cost is ``not_available (reason)`` or a positive USD amount, never 0;
* every BL id cited exists in ``docs/BACKLOG.md``; a report that is not the template has no
  placeholder left.

``report_problems`` is the checker; it has teeth: ``filled_report`` is a hand-built passing report,
and one mutation per rule must fail with the matching problem. The checks read files only.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from evalcore_batchh_support import (  # type: ignore[import-not-found,unused-ignore]
    BL_ID,
    FENCE,
    MEASUREMENTS,
    NOT_CLAIMED_SENTENCE,
    backlog_ids,
    deny_hits_outside_sentence,
    fold,
    h2_headings,
    sections,
    strip_fences,
    units,
)

TEMPLATE = "TEMPLATE.md"
HEADINGS = [
    "Scope and status",
    "Clips",
    "Configuration",
    "Counts per run",
    "Usage and cost",
    "Flags per run",
    "Run-to-run difference",
    "What was NOT measured",
    "Not claimed",
]
TABLES: dict[str, list[str]] = {
    "Clips": ["Clip", "Export turns", "Export tokens", "Audio seconds"],
    "Configuration": ["Setting", "Value"],
    "Counts per run": [
        "Run",
        "Clip",
        "Chunks answered (n of d)",
        "Attempts",
        "Export tokens",
        "STT tokens",
        "H",
        "S",
        "D",
        "I",
        "Agreement distance (MER) (n of d)",
        "Flags",
        "Flagged export tokens (n of d)",
        "Export turns with a flag (n of d)",
    ],
    "Usage and cost": ["Run", "Clip", "Requests", "Audio seconds", "Cost"],
    "Flags per run": ["Run", "Clip", "Flags", "Flag span ids"],
    "Run-to-run difference": [
        "Comparison",
        "Clip",
        "Flagged tokens in both",
        "Flagged tokens in either",
        "Jaccard (n of d)",
    ],
}
SETTINGS = [
    "flag_min_confidence",
    "normaliser",
    "max_chunk_ms",
    "search_ms",
    "stt_model_as_requested",
    "r3_shift_ms",
]
COMPARISONS = ("R1 vs R2", "R1 vs R3", "R1 normaliser on vs off")

PLACEHOLDER = re.compile(r"<[a-z][a-z_ ]*>")
INT = r"\d+"
RATIO = r"\d+ of \d+(?: \(\d+\.\d+\))?"
CELLS: dict[str, str] = {
    "Clip": r"clip-[AB]",
    "Run": r"R\d+",
    "Export turns": INT,
    "Export tokens": INT,
    "Audio seconds": r"\d+(?:\.\d+)?",
    "Chunks answered (n of d)": RATIO,
    "Attempts": INT,
    "STT tokens": INT,
    "H": INT,
    "S": INT,
    "D": INT,
    "I": INT,
    "Agreement distance (MER) (n of d)": RATIO,
    "Flags": INT,
    "Flagged export tokens (n of d)": RATIO,
    "Export turns with a flag (n of d)": RATIO,
    "Requests": INT,
    "Cost": r"not_available \([a-z_]+\)|[1-9]\d*(?:\.\d+)? USD|0\.\d*[1-9]\d* USD",
    "Flag span ids": r"none|E\d{2,}(?:, E\d{2,})*",
    "Comparison": r"R\d+ vs R\d+|R\d+ normaliser on vs off",
    "Flagged tokens in both": INT,
    "Flagged tokens in either": INT,
    "Jaccard (n of d)": RATIO,
}
RATIO_FORM = re.compile(r"(\d+) of (\d+)(?: \((\d+\.\d+)\))?")
DECIMAL = re.compile(r"(?<![\w.])[01]\.\d+(?![\w.])")
STEM = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8}(?![0-9A-Za-z])")
ABSOLUTE = re.compile(r"(?<![\w:/.<>~)-])/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]*)+|~/|\b[A-Za-z]:\\")
CLIPLIKE = re.compile(r"\bclip[-_]\w+|\bclips? \d+\b", re.I)
FILE_EXT = re.compile(r"\.(?:wav|mp3|flac|txt|json|jsonl|sqlite3?|html?|csv|log)\b", re.I)
LONG_QUOTE = re.compile(r'"[^"\n]*\s[^"\n]*\s[^"\n]*"')
STATUS = re.compile(r"^Status: (template|draft|final)$", re.M)
DATE = re.compile(r"^Date: (\d{4}-\d{2}-\d{2}|<date>)$", re.M)


# ------------------------------------------------------------------------------ table helpers


def table(body: str) -> tuple[list[str], list[list[str]]] | None:
    rows = [ln.strip() for ln in strip_fences(body).splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return None
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells[1]):
        return None
    return cells[0], cells[2:]


def is_placeholder(cell: str) -> bool:
    return PLACEHOLDER.search(cell) is not None


def ratio_parts(cell: str) -> tuple[int, int, str | None] | None:
    m = RATIO_FORM.fullmatch(cell)
    return (int(m.group(1)), int(m.group(2)), m.group(3)) if m else None


def rows_of(parsed: dict[str, tuple[list[str], list[list[str]]]], name: str) -> list[list[str]]:
    """The rows of table ``name`` that have the right number of cells (others are reported)."""
    if name not in parsed:
        return []
    return [r for r in parsed[name][1] if len(r) == len(TABLES[name])]


def int_of(cell: str) -> int | None:
    return int(cell) if re.fullmatch(INT, cell) else None


# ------------------------------------------------------------------------------ the checker


def report_problems(text: str, backlog: set[str], *, template: bool) -> list[str]:
    """Every way ``text`` breaks the rules of the module docstring; empty when it holds."""
    found: list[str] = []
    found += global_problems(text, template)
    found += deny_problems(text)
    if h2_headings(text) != HEADINGS:
        found.append(f"the second-level headings are {h2_headings(text)}, not {HEADINGS}")
    first = text.splitlines()[0] if text.strip() else ""
    if not re.fullmatch(r"# Measurement report(?:: .+)?", first):
        found.append("the first line is not '# Measurement report' (with an optional ': title')")
    cited = set(BL_ID.findall(text))
    found += [f"cites a BACKLOG row that does not exist: {b}" for b in sorted(cited - backlog)]
    body = sections(text)
    found += status_problems(body.get("Scope and status", ""), template)
    parsed: dict[str, tuple[list[str], list[list[str]]]] = {}
    for name, header in TABLES.items():
        got = table(body.get(name, ""))
        if got is None:
            found.append(f"{name}: no table")
            continue
        if got[0] != header:
            found.append(f"{name}: the columns are {got[0]}, not {header}")
            continue
        parsed[name] = got
        found += cell_problems(name, got[1], template)
    if "Configuration" in parsed:
        found += configuration_problems(parsed["Configuration"][1])
    found += consistency_problems(parsed, template)
    found += statement_problems(body, template)
    return found


def global_problems(text: str, template: bool) -> list[str]:
    found: list[str] = []
    if any(ord(c) > 0x7F for c in text):
        found.append("a non-ASCII character (no text excerpt, no Devanagari)")
    visible = strip_fences(text)
    if STEM.search(text):
        found.append(f"a file stem or identifier of eight hex digits: {STEM.search(text)}")
    if ABSOLUTE.search(text):
        found.append("an absolute path")
    for m in CLIPLIKE.finditer(visible):
        if m.group(0) not in ("clip-A", "clip-B"):
            found.append(f"a clip named {m.group(0)!r}, only clip-A and clip-B are allowed")
    if FILE_EXT.search(text):
        found.append("a file name extension (a clip is named by id only)")
    if re.search(r"\d\s*%|\bpercent\b|\bper cent\b", text, re.I):
        found.append("a percentage (write the ratio as 'N of D')")
    if re.search(r"^\s*>", visible, re.M):
        found.append("a blockquote (no text excerpt)")
    notice = strip_fences(text)
    if LONG_QUOTE.search(notice):
        found.append("a long quoted string (no text excerpt)")
    if not template and PLACEHOLDER.search(text):
        found.append(f"an unfilled placeholder: {PLACEHOLDER.search(text)}")
    outside_not_claimed = FENCE.sub("", text)
    if len(FENCE.findall(text)) != len(FENCE.findall(sections(text).get("Not claimed", ""))):
        found.append("a fenced block outside the section 'Not claimed'")
    for line in outside_not_claimed.splitlines():
        for decimal in DECIMAL.findall(line):
            if line.lstrip().startswith("| flag_min_confidence |"):
                continue
            forms = [m for m in RATIO_FORM.finditer(line) if m.group(3) == decimal]
            if not forms:
                found.append(f"a decimal without its numerator and denominator: {decimal}")
                continue
            n, d = int(forms[0].group(1)), int(forms[0].group(2))
            if (
                d == 0
                or n > d
                or abs(n / d - float(decimal)) > max(0.5 * 10 ** -len(decimal.split(".")[1]), 1e-12)
            ):
                found.append(f"the decimal {decimal} is not {n} of {d}")
    if re.search(r"<ratio>", text) and not re.search(r"<n> of <d> \(<ratio>\)", text):
        found.append("a <ratio> placeholder without '<n> of <d>'")
    for m in re.finditer(r"<ratio>", text):
        window = text[max(0, m.start() - 20) : m.end() + 1]
        if "<n> of <d> (" not in window:
            found.append("a <ratio> placeholder not in the form '<n> of <d> (<ratio>)'")
    return found


def deny_problems(text: str) -> list[str]:
    found: list[str] = []
    outside, count = deny_hits_outside_sentence(text)
    if outside:
        found.append(f"deny-listed words outside the verbatim sentence: {sorted(set(outside))}")
    if count != 1:
        found.append(f"the verbatim sentence occurs {count} times, not once")
    fences = FENCE.findall(sections(text).get("Not claimed", ""))
    if len(fences) != 1 or fold(fences[0]).strip() != fold(NOT_CLAIMED_SENTENCE):
        found.append("the section 'Not claimed' is not one fenced block of the verbatim sentence")
    return found


def status_problems(body: str, template: bool) -> list[str]:
    found: list[str] = []
    status = STATUS.search(body)
    if status is None:
        found.append("Scope and status: no 'Status: template|draft|final' line")
    elif (status.group(1) == "template") != template:
        found.append("Scope and status: 'template' is the status of TEMPLATE.md only")
    if DATE.search(body) is None:
        found.append("Scope and status: no 'Date: YYYY-MM-DD' line")
    if not re.search(r"^Task: P3-31$", body, re.M):
        found.append("Scope and status: no 'Task: P3-31' line")
    return found


def cell_problems(name: str, rows: list[list[str]], template: bool) -> list[str]:
    found: list[str] = []
    if name == "Configuration":
        return found
    if not rows:
        return [f"{name}: no row"]
    header = TABLES[name]
    for row in rows:
        if len(row) != len(header):
            found.append(f"{name}: a row of {len(row)} cells, not {len(header)}")
            continue
        for column, cell in zip(header, row, strict=True):
            if is_placeholder(cell):
                continue
            if re.fullmatch(CELLS[column], cell) is None:
                found.append(f"{name}: column {column!r} holds {cell!r}")
            if column.endswith("(n of d)") and (p := ratio_parts(cell)) is not None:
                n, d, x = p
                if d == 0 and x is not None:
                    found.append(f"{name}: a decimal on a zero denominator: {cell!r}")
                if d != 0 and n > d:
                    found.append(f"{name}: a numerator above its denominator: {cell!r}")
                if d != 0 and x is None and column != "Chunks answered (n of d)":
                    found.append(f"{name}: a ratio cell without its decimal: {cell!r}")
    return found


def configuration_problems(rows: list[list[str]]) -> list[str]:
    keys = [r[0] for r in rows if r]
    found = [] if keys == SETTINGS else [f"Configuration: the settings are {keys}, not {SETTINGS}"]
    for row in rows:
        if len(row) != 2 or not row[1]:
            found.append(f"Configuration: a row without a value: {row}")
    return found


def consistency_problems(
    parsed: dict[str, tuple[list[str], list[list[str]]]], template: bool
) -> list[str]:
    found: list[str] = []
    clips = {r[0]: r for r in rows_of(parsed, "Clips")}
    counts = {(r[0], r[1]): r for r in rows_of(parsed, "Counts per run")}
    if not template:
        if not clips:
            found.append("Clips: no clip")
        if set(clips) - {"clip-A", "clip-B"}:
            found.append("Clips: only clip-A and clip-B are allowed")
    for (run, clip), row in counts.items():
        if is_placeholder(" ".join(row)):
            continue
        col = dict(zip(TABLES["Counts per run"], row, strict=True))
        mer = ratio_parts(col["Agreement distance (MER) (n of d)"])
        h, s, d, i = (int_of(col[k]) for k in ("H", "S", "D", "I"))
        if mer and None not in (h, s, d, i):
            assert h is not None
            assert s is not None
            assert d is not None
            assert i is not None
            if (mer[0], mer[1]) != (s + d + i, h + s + d + i):
                found.append(
                    f"Counts per run: {run} {clip}: the MER counts are not S+D+I of H+S+D+I"
                )
        tokens = ratio_parts(col["Flagged export tokens (n of d)"])
        if tokens and tokens[1] != int_of(col["Export tokens"]):
            found.append(
                f"Counts per run: {run} {clip}: flagged tokens are not over the export tokens"
            )
        if clip in clips and not is_placeholder(" ".join(clips[clip])):
            if int_of(col["Export tokens"]) != int_of(clips[clip][2]):
                found.append(
                    f"Counts per run: {run} {clip}: export tokens differ from the Clips table"
                )
            turns = ratio_parts(col["Export turns with a flag (n of d)"])
            if turns and turns[1] != int_of(clips[clip][1]):
                found.append(f"Counts per run: {run} {clip}: turns are not over the export turns")
        elif clip not in clips:
            found.append(f"Counts per run: {run} {clip}: the clip is not in the Clips table")
    found += per_run_problems(parsed, counts)
    diff = rows_of(parsed, "Run-to-run difference")
    for row in diff:
        if is_placeholder(" ".join(row)):
            continue
        both, either, jac = int_of(row[2]), int_of(row[3]), ratio_parts(row[4])
        if jac and (jac[0], jac[1]) != (both, either):
            found.append(
                f"Run-to-run difference: {row[0]} {row[1]}: "
                f"the Jaccard cell is not {both} of {either}"
            )
        if either is not None and either > 0 and jac and jac[2] is None:
            found.append(f"Run-to-run difference: {row[0]} {row[1]}: no decimal")
        if both is not None and either is not None and both > either:
            found.append(f"Run-to-run difference: {row[0]} {row[1]}: more in both than in either")
    if template:
        if set(clips) != {"clip-A", "clip-B"}:
            found.append(
                f"Clips: the template holds the rows {sorted(clips)}, not clip-A and clip-B"
            )
        want = {(c, k) for c in ("clip-A", "clip-B") for k in COMPARISONS}
        have = {(r[1], r[0]) for r in diff}
        if want - have:
            found.append(f"Run-to-run difference: the template lacks rows {sorted(want - have)}")
    else:
        compared = {r[1] for r in diff}
        for clip in clips:
            if clip not in compared:
                found.append(f"Run-to-run difference: no comparison for {clip}")
    return found


def per_run_problems(
    parsed: dict[str, tuple[list[str], list[list[str]]]], counts: dict[tuple[str, str], list[str]]
) -> list[str]:
    found: list[str] = []
    for name in ("Usage and cost", "Flags per run"):
        for row in rows_of(parsed, name):
            key = (row[0], row[1])
            if is_placeholder(" ".join(row)):
                continue
            if key not in counts:
                found.append(f"{name}: {key[0]} {key[1]} has no row in Counts per run")
    for row in rows_of(parsed, "Flags per run"):
        key = (row[0], row[1])
        if is_placeholder(" ".join(row)) or key not in counts:
            continue
        ids = [] if row[3] == "none" else row[3].split(", ")
        if int_of(row[2]) != len(ids) or int_of(row[2]) != int_of(counts[key][11]):
            found.append(
                f"Flags per run: {key[0]} {key[1]}: "
                "the flag count disagrees with the ids or Counts per run"
            )
        if len(set(ids)) != len(ids):
            found.append(f"Flags per run: {key[0]} {key[1]}: a repeated span id")
    return found


def statement_problems(body: dict[str, str], template: bool) -> list[str]:
    found: list[str] = []
    checks: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("Usage and cost", "cost is never 0", (r"\bnot_available\b", r"\bnever\b", r"\b0\b|zero")),
        (
            "Run-to-run difference",
            "the Jaccard definition",
            (r"\bJaccard\b", r"flagged", r"\bboth\b", r"\beither\b"),
        ),
        (
            "Run-to-run difference",
            "what R2 and R3 are",
            (r"\bR2\b", r"\brepeat", r"\bR3\b", r"\bshift"),
        ),
        (
            "Run-to-run difference",
            "the ablation is offline",
            (r"normaliser", r"\boffline\b", r"\bstored\b"),
        ),
        (
            "Counts per run",
            "n of d means numerator of denominator",
            (r"\bnumerator\b", r"\bdenominator\b"),
        ),
        ("Clips", "clips are named by id only", (r"\bid\b", r"clip-A", r"clip-B")),
    )
    for section, label, patterns in checks:
        pool = [u for u in units(strip_fences(body.get(section, ""))) if not u.startswith("|")]
        if not any(all(re.search(p, u, re.I) for p in patterns) for u in pool):
            found.append(f"{section}: no statement of {label}")
    section = strip_fences(body.get("What was NOT measured", ""))
    bullets = [ln for ln in section.splitlines() if ln.startswith("- ")]
    if len(bullets) < 3:
        found.append("What was NOT measured: fewer than three bullets")
    joined = " ".join(bullets)
    for bl in ("BL-169", "BL-170"):
        if bl not in joined:
            found.append(f"What was NOT measured: does not cite {bl}")
    return found


# ------------------------------------------------------------------------------ the real files


def _files() -> list[str]:
    return sorted(p.name for p in MEASUREMENTS.glob("*.md"))


def _template() -> str:
    path = MEASUREMENTS / TEMPLATE
    assert path.is_file(), f"docs/phase3/measurements/{TEMPLATE} does not exist"
    text: str = path.read_text("utf-8")
    return text


def test_the_template_exists() -> None:
    assert TEMPLATE in _files(), f"docs/phase3/measurements/{TEMPLATE} does not exist"


@pytest.mark.parametrize("name", _files() or [TEMPLATE])
def test_measurement_report_has_counts_and_denominators(name: str) -> None:
    path = MEASUREMENTS / name
    assert path.is_file(), f"docs/phase3/measurements/{name} does not exist"
    problems = report_problems(path.read_text("utf-8"), backlog_ids(), template=name == TEMPLATE)
    assert problems == []


def test_the_template_tells_the_author_where_a_report_goes() -> None:
    text = _template()
    assert "docs/phase3/measurements/<name>.md" in text


# ------------------------------------------------------------------------------ teeth


def ratio(n: int, d: int) -> str:
    return f"{n} of {d} ({n / d!r})"


def filled_report() -> str:
    def counts(
        run: str,
        clip: str,
        chunks: str,
        hsdi: tuple[int, int, int, int],
        flagged: int,
        turns: int,
        flags: int,
        tokens: int,
        nturns: int,
    ) -> str:
        h, s, d, i = hsdi
        return (
            f"| {run} | {clip} | {chunks} | {chunks.split(' of ')[0]} | {tokens} | {tokens} | "
            f"{h} | {s} | {d} | {i} | "
            f"{ratio(s + d + i, h + s + d + i)} | {flags} | {ratio(flagged, tokens)} | "
            f"{ratio(turns, nturns)} |"
        )

    lines = [
        "# Measurement report: invented numbers for the checker",
        "",
        "## Scope and status",
        "",
        "Status: draft",
        "Date: 2026-01-02",
        "Task: P3-31",
        "",
        "## Clips",
        "",
        "Clips are named by id only: clip-A and clip-B.",
        "",
        "| Clip | Export turns | Export tokens | Audio seconds |",
        "|---|---|---|---|",
        "| clip-A | 22 | 120 | 68.00 |",
        "| clip-B | 10 | 50 | 30.50 |",
        "",
        "## Configuration",
        "",
        "| Setting | Value |",
        "|---|---|",
        "| flag_min_confidence | 0.5 |",
        "| normaliser | mr-Deva/1 |",
        "| max_chunk_ms | 25000 |",
        "| search_ms | 5000 |",
        "| stt_model_as_requested | a model name |",
        "| r3_shift_ms | 12500 |",
        "",
        "## Counts per run",
        "",
        "In a cell 'N of D', N is the numerator and D the denominator.",
        "",
        "| " + " | ".join(TABLES["Counts per run"]) + " |",
        "|" + "---|" * 14,
        counts("R1", "clip-A", "3 of 3", (109, 9, 2, 2), 9, 7, 8, 120, 22),
        counts("R2", "clip-A", "3 of 3", (109, 9, 2, 2), 9, 7, 8, 120, 22),
        counts("R3", "clip-A", "4 of 4", (108, 9, 3, 2), 9, 7, 8, 120, 22),
        counts("R1", "clip-B", "2 of 2", (40, 5, 1, 1), 5, 3, 3, 50, 10),
        "",
        "## Usage and cost",
        "",
        "Cost is not_available when no USD price is given and is never written as 0.",
        "",
        "| Run | Clip | Requests | Audio seconds | Cost |",
        "|---|---|---|---|---|",
        "| R1 | clip-A | 3 | 68.00 | not_available (no_price_for_model) |",
        "| R1 | clip-B | 2 | 30.50 | not_available (no_price_for_model) |",
        "",
        "## Flags per run",
        "",
        "| Run | Clip | Flags | Flag span ids |",
        "|---|---|---|---|",
        "| R1 | clip-A | 8 | E01, E02, E03, E04, E05, E06, E07, E08 |",
        "| R1 | clip-B | 3 | E01, E04, E05 |",
        "",
        "## Run-to-run difference",
        "",
        "Jaccard is the flagged tokens in both runs over the flagged tokens in either run.",
        "R2 repeats R1 and R3 shifts the chunk boundaries by half a window.",
        "The normaliser row compares stored data offline.",
        "",
        "| " + " | ".join(TABLES["Run-to-run difference"]) + " |",
        "|---|---|---|---|---|",
        f"| R1 vs R2 | clip-A | 9 | 9 | {ratio(9, 9)} |",
        f"| R1 vs R3 | clip-A | 7 | 11 | {ratio(7, 11)} |",
        f"| R1 vs R2 | clip-B | 5 | 6 | {ratio(5, 6)} |",
        "",
        "## What was NOT measured",
        "",
        "- Native-speaker review of any flag (BL-169).",
        "- Anything beyond two clips, a demonstration (BL-170).",
        "- Any other engine or language.",
        "",
        "## Not claimed",
        "",
        "```text",
        NOT_CLAIMED_SENTENCE,
        "```",
        "",
    ]
    return "\n".join(lines)


BACKLOG_STUB = {"BL-169", "BL-170"}


def check(text: str, template: bool = False) -> list[str]:
    return report_problems(text, BACKLOG_STUB, template=template)


def test_the_checker_accepts_a_filled_report() -> None:
    assert check(filled_report()) == []


def test_the_template_is_not_a_filled_report() -> None:
    text = _template()
    problems = report_problems(text, backlog_ids(), template=False)
    assert any("unfilled placeholder" in p for p in problems), problems
    assert any("TEMPLATE.md only" in p for p in problems), problems


Mutation = Callable[[str], str]
MUTANTS: dict[str, tuple[Mutation, str]] = {
    "devanagari": (
        lambda t: t.replace("a model name", "\u092e\u093e\u0930\u093e\u0920\u0940"),
        "non-ASCII",
    ),
    "latin transliteration in quotes": (
        lambda t: t.replace("a model name", '"thik aahe mi thaambto"'),
        "long quoted string",
    ),
    "blockquote": (lambda t: t.replace("## Clips\n", "## Clips\n\n> thik aahe\n"), "blockquote"),
    "fenced excerpt": (
        lambda t: t.replace("## Clips\n", "## Clips\n\n```text\nthik aahe\n```\n"),
        "fenced block outside",
    ),
    "file stem": (
        lambda t: t.replace("| clip-A | 22 |", "| clip-A (1dd261a8) | 22 |"),
        "eight hex digits",
    ),
    "stem after underscore": (
        lambda t: t.replace("a model name", "rbnd_01a177bb"),
        "eight hex digits",
    ),
    "file name": (lambda t: t.replace("a model name", "call.wav"), "file name extension"),
    "absolute path": (lambda t: t.replace("a model name", "/Users/someone/calls"), "absolute path"),
    "tilde path": (lambda t: t.replace("a model name", "~/calls"), "absolute path"),
    "third clip": (lambda t: t.replace("clip-B", "clip-C"), "a clip named"),
    "clip named by number": (
        lambda t: t.replace("## Clips\n", "## Clips\n\nThe clip clip-1 is long.\n"),
        "a clip named",
    ),
    "bare decimal": (
        lambda t: t.replace("Any other engine or language.", "Agreement was 0.91 in R1."),
        "decimal without its numerator",
    ),
    "wrong decimal": (lambda t: t.replace("(0.075)", "(0.09)", 1), "is not"),
    "percent": (
        lambda t: t.replace("Any other engine or language.", "About 12% differ."),
        "percentage",
    ),
    "denominator zero with decimal": (
        lambda t: t.replace("| 9 | 9 | 9 of 9 (1.0) |", "| 0 | 0 | 0 of 0 (0.0) |"),
        "zero denominator",
    ),
    "ratio cell without decimal": (
        lambda t: t.replace("9 of 9 (1.0)", "9 of 9"),
        "without its decimal",
    ),
    "jaccard counts differ from the row": (
        lambda t: t.replace("| 7 | 11 | 7 of 11", "| 7 | 12 | 7 of 11"),
        "Jaccard cell",
    ),
    "numerator above denominator": (
        lambda t: t.replace("| 3 of 3 | 3 |", "| 4 of 3 | 3 |", 1),
        "numerator above its denominator",
    ),
    "mer counts differ": (lambda t: t.replace("13 of 122", "12 of 122", 1), "MER counts"),
    "flagged tokens over the wrong denominator": (
        lambda t: t.replace("9 of 120", "9 of 121", 1),
        "flagged tokens are not over",
    ),
    "tokens differ from the clips table": (
        lambda t: t.replace("| clip-A | 22 | 120 |", "| clip-A | 22 | 119 |"),
        "differ from the Clips table",
    ),
    "flag ids disagree with the count": (
        lambda t: t.replace("E07, E08 |", "E07 |"),
        "flag count disagrees",
    ),
    "repeated span id": (lambda t: t.replace("E07, E08", "E07, E07"), "repeated span id"),
    "cost zero": (
        lambda t: t.replace("not_available (no_price_for_model)", "0 USD", 1),
        "column 'Cost'",
    ),
    "cost false": (
        lambda t: t.replace("not_available (no_price_for_model)", "false", 1),
        "column 'Cost'",
    ),
    "heading renamed": (
        lambda t: t.replace("## Usage and cost", "## Cost"),
        "second-level headings",
    ),
    "heading added": (lambda t: t + "\n## Extra\n\ntext\n", "second-level headings"),
    "column renamed": (
        lambda t: t.replace("| H | S | D | I |", "| H | S | D | Ins |"),
        "the columns are",
    ),
    "no table": (
        lambda t: t.replace(
            "| Run | Clip | Requests | Audio seconds | Cost |\n|---|---|---|---|---|\n",
            "| Run | Clip | Requests | Audio seconds | Cost |\n",
        ),
        "Usage and cost: no table",
    ),
    "deny word": (
        lambda t: t.replace("Any other engine or language.", "Any other accuracy figure."),
        "deny-listed",
    ),
    "deny word in a heading": (
        lambda t: t.replace("## Clips", "## Clips and recall"),
        "deny-listed",
    ),
    "sentence missing": (
        lambda t: t.replace(NOT_CLAIMED_SENTENCE, "Nothing is claimed."),
        "occurs 0 times",
    ),
    "status template in a report": (
        lambda t: t.replace("Status: draft", "Status: template"),
        "TEMPLATE.md only",
    ),
    "status missing": (lambda t: t.replace("Status: draft\n", ""), "no 'Status"),
    "placeholder left": (lambda t: t.replace("a model name", "<model>"), "unfilled placeholder"),
    "unknown backlog row": (lambda t: t.replace("(BL-169)", "(BL-999)"), "does not exist"),
    "not measured: BL-170 dropped": (lambda t: t.replace(" (BL-170)", ""), "does not cite BL-170"),
    "not measured: two bullets": (
        lambda t: t.replace("- Any other engine or language.\n", ""),
        "fewer than three",
    ),
    "no comparison for a clip": (
        lambda t: t.replace("| R1 vs R2 | clip-B | 5 | 6 | " + ratio(5, 6) + " |\n", ""),
        "no comparison for clip-B",
    ),
    "missing settings": (lambda t: t.replace("| r3_shift_ms | 12500 |\n", ""), "the settings are"),
    "no cost statement": (
        lambda t: t.replace(
            "Cost is not_available when no USD price is given and is never written as 0.",
            "Cost is listed.",
        ),
        "never 0",
    ),
    "no jaccard definition": (
        lambda t: t.replace(
            "Jaccard is the flagged tokens in both runs over the flagged tokens in either run.",
            "Overlap is shown.",
        ),
        "Jaccard definition",
    ),
    "unknown run in usage": (
        lambda t: t.replace("| R1 | clip-B | 2 | 30.50", "| R7 | clip-B | 2 | 30.50"),
        "has no row in Counts per run",
    ),
}


def test_the_mutations_all_apply() -> None:
    base = filled_report()
    for name, (mutate, _) in MUTANTS.items():
        assert mutate(base) != base, name


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_the_checker_refuses_each_mutated_report(name: str) -> None:
    mutate, expected = MUTANTS[name]
    problems = check(mutate(filled_report()))
    assert any(expected in p for p in problems), (name, problems)


@pytest.mark.parametrize(
    "word",
    [
        "accuracy",
        "WER",
        "CER",
        "error rate",
        "Error  Rate",
        "correct",
        "recall",
        "precision",
        "\uff41ccuracy",
        "acc\u200buracy",
    ],
)
def test_the_checker_refuses_every_deny_listed_word_outside_not_claimed(word: str) -> None:
    text = filled_report().replace("Any other engine or language.", f"Any other {word} figure.")
    problems = check(text)
    assert any("deny-listed" in p or "non-ASCII" in p for p in problems), problems


def test_the_checker_refuses_a_deny_listed_word_beside_the_sentence_in_the_fence() -> None:
    text = filled_report().replace(NOT_CLAIMED_SENTENCE, NOT_CLAIMED_SENTENCE + "\nNo recall.")
    assert any("deny-listed" in p or "not one fenced block" in p for p in check(text))


def test_a_ratio_placeholder_needs_its_numerator_and_denominator() -> None:
    assert not any(
        "<ratio>" in p
        for p in check(
            filled_report().replace("a model name", "<n> of <d> (<ratio>)"), template=True
        )
    )
    bad = filled_report().replace("a model name", "<ratio>")
    assert any("<ratio> placeholder" in p for p in check(bad, template=True))


def test_a_wrapped_verbatim_sentence_is_still_the_sentence() -> None:
    words = NOT_CLAIMED_SENTENCE.split(" ")
    wrapped = "\n".join(" ".join(words[i : i + 6]) for i in range(0, len(words), 6))
    assert check(filled_report().replace(NOT_CLAIMED_SENTENCE, wrapped)) == []
