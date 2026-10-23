# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""T-DRIFT (ADR 0005 Decision 10, revision 27 text; invariant I22; gate item 18).

No file in the scan scope names an excluded language, its tag or the mixed-language words, unless
it is on the allowlist, and the allowlist is exactly the one the ADR states, holds repository paths
only, and holds no entry that is not needed. This module builds every pattern and every sample
from parts, so it spells no token itself (and is not on the allowlist).

Scan scope: the git-tracked and untracked, non-ignored files below ``packages/evalschema/`` and
``docs/``, and ``.claude/agents/*.md``. ``tools/``, ``scripts/`` and the root files are out.
Nothing under ``packages/evalschema/tests/fixtures/private/`` and no ``.env*`` file is ever read;
binary files (a NUL byte, or not UTF-8) are skipped. A hit is reported as ``path:line: token``
and never as the text of the line.

Matcher: (1) tag syntax, case-sensitive and strict: an excluded primary subtag followed by ``-``
or ``_`` and a second subtag of region or script shape (``[A-Z]{2}``, ``\\d{3}``,
``[A-Z][a-z]{3}``); (2) case-insensitive: an excluded subtag that is a whole quoted string, the
excluded language names, the mixed-language words, the Hindi-English alias and the excluded
Punjabi script tag.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from ._support import ROOT

# ----------------------------------------------------------------------------- the matcher


def _join(*parts: str) -> str:
    return "".join(parts)


_SUBTAGS = _join("(?:b", "n|u", "r|o", "r|a", "s)")
_NAMES = "|".join(
    (
        _join("ben", "gali"),
        _join("u", "rdu"),
        _join("od", "ia"),
        _join("ori", "ya"),
        _join("assam", "ese"),
        _join("shah", "mukhi"),
        _join("hing", "lish"),
    )
)
_MIXED_WORDS = _join("code", "[-_ ]?", "mix")
_ALIAS = _join(r"\bhi", "[-_]", "en", r"\b")
_PUNJABI_SCRIPT = _join(r"\bpa", "[-_]", "Ar", r"ab\b")
_QUOTED = _join("[\"']", _SUBTAGS, "[\"']")

INSENSITIVE = re.compile(
    "|".join((_NAMES, _MIXED_WORDS, _ALIAS, _PUNJABI_SCRIPT, _QUOTED)), re.IGNORECASE
)
STRICT_TAG = re.compile(_join(r"\b", _SUBTAGS, "[-_]", r"(?:[A-Z]{2}|\d{3}|[A-Z][a-z]{3})\b"))


def line_hits(line: str) -> list[str]:
    """Every matched text of one line (both parts of the matcher)."""
    return [m.group(0) for m in INSENSITIVE.finditer(line)] + [
        m.group(0) for m in STRICT_TAG.finditer(line)
    ]


def text_hits(text: str) -> list[tuple[int, str]]:
    return [(n, token) for n, line in enumerate(text.splitlines(), 1) for token in line_hits(line)]


# ----------------------------------------------------------------------------- scope and walk

SCOPE_DIRS = ("packages/evalschema/", "docs/")
AGENT_GLOB = ".claude/agents/*.md"
AGENT_FILES = re.compile(r"^\.claude/agents/[^/]+\.md$")
NEVER_READ = ("packages/evalschema/tests/fixtures/private/",)
SKIPPED_DIR_NAMES = {
    "__pycache__",
    ".venv",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "dist",
    "build",
}

SELF = "packages/evalschema/tests/test_tdrift.py"

# The allowlist: exactly the entries ADR 0005 Decision 10 states
# (``test_tdrift_allowlist_matches_adr``).
# Revision 27 text plus batch 19 (qa): the five clean-room UAX #9 modules (A36, bidi class code
# names, not language tags); without the entries for the two modules whose binding test names no
# longer match the strict pattern, without the synthetic fixture directory (no hit there) and
# without the T-DRIFT test module (it builds its tokens from parts).
ALLOW_FILES: frozenset[str] = frozenset(
    {
        "docs/phase1/SCOPE.md",
        "docs/phase1/api-surface.md",
        "docs/adr/0004-requires-and-not-applicable.md",
        "docs/adr/0005-language.md",
        "docs/adr/0018-real-sample-mapping-policy.md",
        "packages/evalschema/tests/fixtures/languages/negatives/negatives.json",
        "packages/evalschema/tests/fixtures/languages/negatives/README.md",
        "packages/evalschema/tests/test_language.py",
        "packages/evalschema/tests/test_data_files.py",
        "packages/evalschema/tests/test_persona_profile.py",
        "packages/evalschema/tests/test_dataset.py",
        "packages/evalschema/src/tracer_evalschema/language.py",
        "packages/evalschema/tests/bidi_conformance.py",
        "packages/evalschema/tests/support/uax9/algorithm.py",
        "packages/evalschema/tests/support/uax9/ucd.py",
        "packages/evalschema/tests/test_uax9_data.py",
        "packages/evalschema/tests/test_uax9_rules.py",
        SELF,
    }
)
ALLOW_DIRS: frozenset[str] = frozenset(
    {
        "docs/phase1/evidence/",
        "packages/evalschema/tests/fixtures/real/",
        "packages/evalschema/tests/support/uax9/data/",
    }
)
ALLOW_GLOBS: frozenset[str] = frozenset({AGENT_GLOB})
ALLOWLIST: frozenset[str] = ALLOW_FILES | ALLOW_DIRS | ALLOW_GLOBS


def allowed(path: str) -> bool:
    return (
        path in ALLOW_FILES
        or path.startswith(tuple(ALLOW_DIRS))
        or (AGENT_GLOB in ALLOW_GLOBS and bool(AGENT_FILES.match(path)))
    )


def in_scope(path: str) -> bool:
    return path.startswith(SCOPE_DIRS) or bool(AGENT_FILES.match(path))


def never_read(path: str) -> bool:
    return path.startswith(NEVER_READ) or Path(path).name.startswith(".env")


def candidate_paths(root: Path) -> list[str]:
    """Tracked plus untracked, non-ignored files (``git ls-files -co --exclude-standard``); in a
    tree without git (the self-tests' temporary trees) every file below ``root`` except caches."""
    if (root / ".git").exists():
        done = subprocess.run(  # noqa: S603 - fixed executable and arguments, no shell
            [shutil.which("git") or "git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
        return sorted({p for p in done.stdout.decode().split("\0") if p})
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIR_NAMES]
        found += [(Path(current) / f).relative_to(root).as_posix() for f in files]
    return sorted(found)


@dataclass(frozen=True)
class Scan:
    scanned: int
    binary: int
    hits: dict[str, list[tuple[int, str]]]  # path -> [(line, matched text)], allowlisted or not


def scan(root: Path, *, everywhere: bool = False) -> Scan:
    """Scan the scope below ``root``. Hits are returned for every scanned file (the caller decides
    which are allowlisted); with ``everywhere`` the scope is ignored (self-test of the scope)."""
    scanned = binary = 0
    hits: dict[str, list[tuple[int, str]]] = {}
    for rel in candidate_paths(root):
        if never_read(rel) or not (everywhere or in_scope(rel)):
            continue
        full = root / rel
        if not full.is_file() or full.is_symlink():
            continue
        data = full.read_bytes()
        if b"\0" in data:
            binary += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            binary += 1
            continue
        scanned += 1
        found = text_hits(text)
        if found:
            hits[rel] = found
    return Scan(scanned, binary, hits)


def violations(result: Scan) -> list[str]:
    """``path:line: token`` for every hit outside the allowlist (never the text of the line)."""
    return [
        f"{path}:{line}: {token!r}"
        for path, found in sorted(result.hits.items())
        if not allowed(path)
        for line, token in found
    ]


# ----------------------------------------------------------------------------- the real scan


def test_no_codemix_or_excluded_language_tokens() -> None:
    """I22 / gate item 18: no file in the scope, outside the allowlist, matches. The scan reads a
    real tree (not a handful of files), so a walk that finds nothing fails."""
    result = scan(ROOT)
    assert result.scanned > 500, result.scanned
    assert not violations(result), "\n".join(violations(result))


def test_tdrift_scan_covers_the_documents_the_gate_names() -> None:
    """The walk reaches the package source, tests, fixtures, docs and agent files (gate item 18),
    and not ``tools/`` or ``scripts/``."""
    paths = [p for p in candidate_paths(ROOT) if in_scope(p) and not never_read(p)]
    assert any(p.startswith("packages/evalschema/src/tracer_evalschema/") for p in paths)
    assert any(p.startswith("packages/evalschema/tests/fixtures/") for p in paths)
    assert any(p.startswith("packages/evalschema/tests/test_") for p in paths)
    assert any(p.startswith("docs/adr/") for p in paths)
    assert any(p.startswith("docs/concepts/") for p in paths)
    assert any(AGENT_FILES.match(p) for p in paths)
    assert not any(p.startswith(("tools/", "scripts/")) for p in paths)
    assert not any(p.startswith("packages/evalschema/tests/fixtures/private/") for p in paths)


def test_tdrift_this_module_and_the_support_helpers_spell_no_token() -> None:
    """This module builds its tokens from parts. Its only match is the binding test name
    ``test_no_codemix_or_excluded_language_tokens`` (SCOPE section 5), which is why it has an
    allowlist entry. The earlier helper modules of the language fixtures have none."""
    own = Path(__file__).read_text(encoding="utf-8").splitlines()
    found = {(n, t) for n, t in text_hits("\n".join(own))}
    assert found, "the binding test name no longer matches: remove the allowlist entry"
    assert all("test_no_" in own[n - 1] for n, _t in found), sorted(found)
    for name in ("_support_b10.py", "_support_b2.py", "_support_b3.py"):
        path = Path(__file__).with_name(name)
        assert text_hits(path.read_text(encoding="utf-8")) == [], name


# ----------------------------------------------------------------------------- the allowlist


def test_tdrift_allowlist_has_only_repo_paths() -> None:
    """Every entry is a path inside the repository that exists (a directory ends with ``/``, the
    one glob is the agent definitions), and none is absolute, escapes the root or names the plan
    file or the user's home."""
    for entry in ALLOWLIST:
        assert not entry.startswith(("/", "~")), entry
        assert ".." not in entry.split("/"), entry
        assert "hazy-herding-castle" not in entry
        assert "plans" not in entry.split("/"), entry
    for entry in ALLOW_FILES:
        assert (ROOT / entry).is_file(), entry
    for entry in ALLOW_DIRS:
        assert entry.endswith("/")
        assert (ROOT / entry).is_dir(), entry
    assert {".claude/agents/*.md"} == ALLOW_GLOBS
    assert list((ROOT / ".claude/agents").glob("*.md"))
    assert not [e for e in ALLOW_FILES if e.endswith("/") or "*" in e]
    assert not [e for e in ALLOW_DIRS if "*" in e]
    for entry in ALLOW_FILES | ALLOW_DIRS:
        assert in_scope(entry), f"{entry} is outside the scan scope, so it needs no entry"
        assert not never_read(entry), entry


def test_tdrift_allowlist_has_no_unneeded_entry() -> None:
    """ADR 0005 Decision 10, "what qualifies": a file is allowlisted only if it must spell a
    matched token. Every file, directory and glob entry covers at least one hit of the real tree;
    an entry without a hit is removed from the ADR and from this module (the binding test names
    of two modules no longer match the strict pattern, for example)."""
    result = scan(ROOT)
    unneeded = [
        entry
        for entry in sorted(ALLOWLIST)
        if not any(
            path == entry
            or (entry.endswith("/") and path.startswith(entry))
            or (entry == AGENT_GLOB and AGENT_FILES.match(path))
            for path in result.hits
        )
    ]
    assert unneeded == [], unneeded


ADR = ROOT / "docs" / "adr" / "0005-language.md"
ENTRY_PATH = re.compile(r"`((?:docs|packages|\.claude)/[A-Za-z0-9_./*-]+)`")


def adr_decision_10() -> str:
    text = ADR.read_text(encoding="utf-8")
    start = text.index("10. **T-DRIFT allowlist")
    end = text.index("\n## ", start)
    return text[start:end]


def adr_allowlist(decision: str) -> set[str]:
    """The paths the allowlist bullets of Decision 10 name as entries: backticked repository paths
    in the bullets, minus the sentences that say an entry is withdrawn or not needed."""
    entries: set[str] = set()
    for line in decision.splitlines():
        if not line.startswith("    - "):
            continue
        body = re.sub(r"\([^()]*withdrawn[^()]*\)", "", line)
        for sentence in re.split(r"(?<=[.;])\s+", body):
            if re.search(r"\bno entry\b|\bneeds? no\b|\bhas no entry\b", sentence):
                continue
            entries.update(ENTRY_PATH.findall(sentence))
    return entries


def test_tdrift_allowlist_matches_adr() -> None:
    """I22: the allowlist of this module equals the entries of ADR 0005 Decision 10, as the
    bullets of the Decision state them (files, directories, the agent glob). Adding or removing an
    entry amends the Decision and this module together."""
    in_adr = adr_allowlist(adr_decision_10())
    assert in_adr, "no allowlist entries found in Decision 10"
    assert "the T-DRIFT test module itself" in adr_decision_10()
    expected = ALLOWLIST - {SELF}
    assert {e for e in in_adr if e not in expected} == set(), "in the ADR, not in the test"
    assert {e for e in expected if e not in in_adr} == set(), "in the test, not in the ADR"


def test_adr_allowlist_reader_is_not_vacuous() -> None:
    """The ADR reader takes entries from bullets, skips sentences that say 'no entry' and
    withdrawn entries, and reads globs and directories."""
    sample = (
        "10. **T-DRIFT allowlist.** intro `docs/ignored.md`\n"
        "    - `docs/a.md`, `packages/x/y.py` and the directory `docs/phase1/evidence/`\n"
        "    - (P-15) the agents `.claude/agents/*.md`; (Revision 27: the entry for the page "
        "is withdrawn. `docs/concepts/language.md` has no entry.)\n"
        "    - `packages/x/z.py` defines a test. `packages/x/helper.py` needs no entry, because "
        "it assembles tags.\n"
        "    prose line with `docs/prose.md`\n"
    )
    assert adr_allowlist(sample) == {
        "docs/a.md",
        "packages/x/y.py",
        "docs/phase1/evidence/",
        ".claude/agents/*.md",
        "packages/x/z.py",
    }


# ----------------------------------------------------------------------------- self-tests

# Samples are assembled from parts, as the matcher is.
TAG_CASES = [
    _join("b", "n", "-IN"),
    _join("b", "n", "_IN"),
    _join("u", "r", "-419"),
    _join("o", "r", "-Beng"),
    _join("a", "s", "-IN"),
    _join("x ", "b", "n", "-IN", " y"),
]
QUOTED_CASES = [
    _join('"', "b", "n", '"'),
    _join('"', "B", "N", '"'),
    _join("'", "O", "r", "'"),
    _join("'", "u", "r", "'"),
    _join('"', "A", "s", '"'),
]
WORD_CASES = [
    _join("Ben", "gali"),
    _join("URD", "U"),
    _join("O", "dia"),
    _join("ori", "ya"),
    _join("Assam", "ese"),
    _join("Shah", "mukhi"),
    _join("Hing", "lish"),
    _join("code", "-mix"),
    _join("Code", " Mix"),
    _join("code", "mix"),
    _join("code", "_mix"),
    _join("hi", "-en"),
    _join("hi", "_EN"),
    _join("pa", "-Ar", "ab"),
    _join("pa", "_ar", "ab"),
]
CLEAN_CASES = [
    "or-else it works",
    "as-is and as_is",
    "bn is a bare token",
    _join("b", "n", "-in"),
    _join("o", "r", "-key"),
    _join("o", "r", "-der"),
    _join("a", "s", "-needed"),
    _join("u", "r", "-text"),
    "hi-IN and en-US and pa-Guru-IN",
    "in order to; as such",
    "works as it is, or else it fails",
    _join("metrics.as_", "decimal"),
    _join("o", "r", "_none"),
    _join("pa", "-Guru"),
]


@pytest.mark.parametrize("sample", TAG_CASES + QUOTED_CASES + WORD_CASES)
def test_matcher_flags_the_tokens(sample: str) -> None:
    assert line_hits(sample) != [], sample


@pytest.mark.parametrize("sample", CLEAN_CASES)
def test_matcher_spares_ordinary_text(sample: str) -> None:
    assert line_hits(sample) == [], sample


def test_strict_tag_shape_is_case_sensitive_and_needs_a_region_or_script_shape() -> None:
    """The first part is lower case and needs [A-Z]{2}, three digits or Title-case four letters."""
    primary = _join("b", "n")
    assert STRICT_TAG.search(f"{primary}-IN")
    assert STRICT_TAG.search(f"{primary}-419")
    assert STRICT_TAG.search(f"{primary}-Beng")
    assert not STRICT_TAG.search(f"{primary}-in")
    assert not STRICT_TAG.search(f"{primary}-beng")
    assert not STRICT_TAG.search(f"{primary}-INN")
    assert not STRICT_TAG.search(f"{primary.upper()}-IN")
    assert not STRICT_TAG.search(f"x{primary}-IN")


def _write(root: Path, rel: str, text: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def test_scan_flags_injected_tokens_and_spares_the_allowlisted_paths(tmp_path: Path) -> None:
    """Not vacuous: the same banned text is flagged in every scanned location that is not on the
    allowlist (package source, tests, fixtures, docs, an agent file) and spared in each allowlisted
    kind (a listed file, a listed directory, the agent glob); files out
    of scope are not read."""
    banned = _join("b", "n", "-IN") + " " + _join("Ben", "gali")
    flagged = [
        "packages/evalschema/src/tracer_evalschema/mod.py",
        "packages/evalschema/tests/test_new.py",
        "packages/evalschema/tests/fixtures/golden/x.json",
        "packages/evalschema/tests/fixtures/synthetic/x.json",
        "packages/evalschema/tests/test_results_language.py",
        "docs/concepts/language.md",
        "docs/HANDOFF.md",
    ]
    spared = [
        "docs/phase1/SCOPE.md",
        "docs/adr/0005-language.md",
        "docs/phase1/evidence/green/x/run.log",
        "packages/evalschema/tests/fixtures/real/p/response.json",
        "packages/evalschema/tests/support/uax9/data/DerivedBidiClass.txt",
        "packages/evalschema/tests/bidi_conformance.py",
        ".claude/agents/qa.md",
    ]
    out_of_scope = [
        "tools/notes.md",
        "scripts/tool.py",
        "README.md",
        "packages/other/mod.py",
        "packages/evalschema/tests/fixtures/private/call.txt",
        "packages/evalschema/.env.local",
        "docs/.env",
        ".claude/agents/sub/nested.md",
    ]
    for rel in [*flagged, *spared, *out_of_scope]:
        _write(tmp_path, rel, f"line one\n{banned}\n")
    result = scan(tmp_path)
    bad = violations(result)
    assert sorted({v.split(":")[0] for v in bad}) == sorted(flagged)
    assert {r for r in result.hits if allowed(r)} == set(spared)
    assert not [r for r in result.hits if r in out_of_scope]
    assert all(v.split(":")[1] == "2" for v in bad)
    assert all(banned not in v for v in bad), "a hit must never print the line"


def test_scan_skips_binary_files_and_never_reads_private_or_env_files(tmp_path: Path) -> None:
    banned = _join("Ben", "gali")
    _write(tmp_path, "docs/blob.bin", b"\x00" + banned.encode())
    _write(tmp_path, "docs/latin.txt", banned.encode() + b"\xff\xfe")
    _write(tmp_path, "docs/ok.md", "clean\n")
    private = tmp_path / "packages/evalschema/tests/fixtures/private/secret.txt"
    _write(tmp_path, "packages/evalschema/tests/fixtures/private/secret.txt", banned)
    env = tmp_path / "docs/.env.local"
    _write(tmp_path, "docs/.env.local", banned)
    for guarded in (private, env):
        guarded.chmod(0)
    try:
        result = scan(tmp_path)
    finally:
        for guarded in (private, env):
            guarded.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert result.scanned == 1
    assert result.binary == 2
    assert result.hits == {}


def test_scan_reports_the_line_number_of_each_hit(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "x\n\n" + _join("U", "rdu") + " and " + _join("O", "dia") + "\n")
    assert violations(scan(tmp_path)) == [
        f"docs/a.md:3: {_join('U', 'rdu')!r}",
        f"docs/a.md:3: {_join('O', 'dia')!r}",
    ]


# ----------------------------------------------------------------------------- the plan file

PLAN_REVIEWED = ROOT / "docs" / "phase1" / "tdrift-plan-reviewed.json"


def plan_line_digests(text: str) -> list[str]:
    """SHA-256 of every matching line's text, sorted and without repeats (the reviewed file holds
    only these, never the text)."""
    lines = {line for line in text.splitlines() if line_hits(line)}
    return sorted(hashlib.sha256(line.encode("utf-8")).hexdigest() for line in lines)


@pytest.mark.local_only
def test_tdrift_plan_file_local() -> None:
    """ADR 0005 Decision 10, revision 6: the plan lives outside the repository. Selected only
    with ``-m local_only`` (deselected in CI, never skipped): the path comes from
    ``TRACER_PLAN_FILE``; the set of matching lines (by SHA-256 of the line) must equal the set
    reviewed in ``docs/phase1/tdrift-plan-reviewed.json`` (``{"line_sha256": [...]}``)."""
    variable = os.environ.get("TRACER_PLAN_FILE")
    assert variable, "set TRACER_PLAN_FILE to the path of the plan file to run this check"
    plan = Path(variable)
    assert plan.is_file(), "TRACER_PLAN_FILE does not name a file"
    assert PLAN_REVIEWED.is_file(), f"{PLAN_REVIEWED.relative_to(ROOT)} is missing"
    reviewed = json.loads(PLAN_REVIEWED.read_text(encoding="utf-8"))
    assert sorted(reviewed["line_sha256"]) == plan_line_digests(plan.read_text(encoding="utf-8"))


def test_plan_line_digests_are_text_free_and_detect_a_new_line() -> None:
    base = "ordinary line\n" + _join("Ben", "gali") + " is excluded\n"
    digests = plan_line_digests(base)
    assert len(digests) == 1
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests)
    assert plan_line_digests(base + _join("code", "-mix") + " too\n") != digests
    assert plan_line_digests(base + "another clean line\n") == digests


def test_plan_check_is_not_part_of_the_default_run() -> None:
    """The marker is declared in the root configuration, which deselects it by default."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "not local_only" in text
    assert "local_only:" in text


def test_scan_walk_yields_only_files(tmp_path: Path) -> None:
    (tmp_path / "docs" / "dir.md").mkdir(parents=True)
    _write(tmp_path, "docs/real.md", "ok\n")
    names = list(_iter_scanned(tmp_path))
    assert names == ["docs/real.md"]


def _iter_scanned(root: Path) -> Iterator[str]:
    for rel in candidate_paths(root):
        if in_scope(rel) and (root / rel).is_file():
            yield rel
