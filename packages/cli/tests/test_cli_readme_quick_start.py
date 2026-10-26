# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""SCOPE P3-32: the quick start of ``packages/cli/README.md`` is parseable and runs offline.

The README must show the exact offline command on the synthetic Marathi pair of
``docs/examples/synthetic-mr``. Three layers, so the text cannot drift from the code:

1. Static (``readme_problems``): the fenced shell block of the section "Quick start (offline, no
   key, no network)" is parsed. It may hold only the five command forms of ``STEP_FORMS``; every
   ``--option`` of ``tracer score`` exists in the real argparse parser (``tracer_cli.cli.
   build_parser``, nothing is run), every required option is there, the real parser accepts the
   argument list, the replay files are named ``chunk-0000.json`` to ``chunk-0002.json`` and sit in
   the directory ``--stt fake:`` names, every path under the work directory is below ``$WORK`` (a
   directory outside the repository), every repository path exists, and nothing in the block can
   send data (no ``--stt sarvam``, no key, no ``--env-file``, no network tool).
2. The README names every exit code of ``tracer_cli.cli.EXIT_CODES`` with its meaning, and no
   other code, and has the privacy statements (what leaves the machine, what stays out of git).
3. ``test_readme_quick_start_runs_offline`` runs the block, step by step, in-process and offline,
   and checks the "Expected output" block of the README against the real output, line by line.

The checker has teeth: a hand-built passing README and one mutation per rule.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
README = ROOT / "packages" / "cli" / "README.md"
EXAMPLES = Path("docs") / "examples" / "synthetic-mr"
MAKE_WAV = (EXAMPLES / "make_wav.py").as_posix()
EXPORT = (EXAMPLES / "export_synthetic_mr.txt").as_posix()
RUBRIC = "docs/examples/rubric-mr.example.json"
CONCEPT_LINK = "../../docs/concepts/recorded-call-scoring.md"
RUN_BOOK_LINK = "../../docs/adr/0024-provider-adapters-byok-sarvam-rest.md"
QUICK_START = "Quick start (offline, no key, no network)"
HEADINGS = [QUICK_START, "Exit codes", "Privacy and a real send"]
WORK_LINE = 'WORK="$(mktemp -d)"'
WORK_TOKEN = "WORK=$(mktemp -d)"  # noqa: S105 (a shell assignment, not a secret)
FAKE_FILES = [f"chunk-{i:04d}.json" for i in range(3)]
STEP_FORMS = (
    "WORK=$(mktemp -d); [uv run] python -I make_wav.py OUT; mkdir -p DIR; cp SRC DST; "
    "[uv run] tracer score ..."
)
FORBIDDEN_IN_BLOCK = (
    r"--stt\s+sarvam",
    r"SARVAM_API_KEY",
    r"--env-file",
    r"\bcurl\b",
    r"\bwget\b",
    r"https?://",
    r"--embed-audio",
)
EXPECTED_LINES = (
    "result: scored",
    "agreement distance (MER): ",
    "flags: ",
    "flagged share of export tokens: ",
)


def cli_module() -> Any:
    import importlib

    return importlib.import_module("tracer_cli.cli")


# ------------------------------------------------------------------------------ the text


def sections(text: str) -> dict[str, str]:
    """``{title of a '## ' section: body}``; a line inside a fenced block is never a heading."""
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    in_fence = False
    for line in text.splitlines(keepends=True):
        if line.startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else re.fullmatch(r"## (.+?)\s*", line.rstrip("\n"))
        if heading is not None:
            current = found.setdefault(heading.group(1), [])
        elif current is not None:
            current.append(line)
    return {title: "".join(lines) for title, lines in found.items()}


def fenced(body: str, info: str) -> list[str]:
    return re.findall(rf"^```{info}[ \t]*\n(.*?)^```[ \t]*$", body, re.M | re.S)


def commands(block: str) -> list[str]:
    """The commands of a shell block: continuation lines joined, comments and blank lines gone."""
    joined = re.sub(r"\\\n\s*", " ", block)
    return [
        ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]


def tokens_of(command: str) -> list[str]:
    return shlex.split(command, posix=True)


def strip_runner(tokens: list[str]) -> list[str]:
    """Drop a leading ``uv run`` and its options."""
    if tokens[:2] == ["uv", "run"]:
        rest = tokens[2:]
        while rest and rest[0].startswith("--"):
            rest = rest[1:]
        return rest
    return tokens


def score_parser() -> Any:
    import argparse

    parser = cli_module().build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["score"]
    raise AssertionError("no score subcommand")


def option_names(parser: Any) -> set[str]:
    return {s for a in parser._actions for s in a.option_strings}


def required_options(parser: Any) -> set[str]:
    return {a.option_strings[0] for a in parser._actions if a.required and a.option_strings}


# ------------------------------------------------------------------------------ the checker


def readme_problems(text: str, root: Path = ROOT) -> list[str]:
    """Every way ``text`` breaks the rules of the module docstring; empty when it holds.

    Paths of the repository are looked up below ``root``, the links from ``root/packages/cli``.
    """
    cli = cli_module()
    found: list[str] = []
    if re.search(r"scaffold only", text, re.I):
        found.append("still says 'scaffold only'")
    if not text.startswith("# tracer-cli\n"):
        found.append("the first line is not '# tracer-cli'")
    body = sections(text)
    titles = list(body)
    positions = [titles.index(h) if h in titles else -1 for h in HEADINGS]
    if -1 in positions or positions != sorted(positions):
        found.append(f"the sections {HEADINGS} are not all there in that order (found {titles})")
        return found
    found += quick_start_problems(body[QUICK_START], root)
    found += exit_code_problems(body["Exit codes"], cli.EXIT_CODES)
    found += privacy_problems(body["Privacy and a real send"])
    if "repository root" not in body[QUICK_START]:
        found.append("the quick start does not say the commands run from the repository root")
    if not re.search(r"chunk-NNNN\.json[^\n]*four digits", body[QUICK_START]):
        found.append("the quick start does not say replay files are chunk-NNNN.json (four digits)")
    for target in re.findall(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)", text):
        if (
            not re.match(r"[a-z]+:", target)
            and not (root / "packages/cli" / target.split("#")[0]).resolve().exists()
        ):
            found.append(f"a link does not resolve: {target}")
    return found


def quick_start_problems(body: str, root: Path) -> list[str]:
    found: list[str] = []
    shells = fenced(body, "(?:sh|bash|shell|console)")
    if len(shells) != 1:
        return [f"the quick start has {len(shells)} fenced shell blocks, not one"]
    block = shells[0]
    for pattern in FORBIDDEN_IN_BLOCK:
        if re.search(pattern, block):
            found.append(f"the shell block holds {pattern!r}: a quick start sends nothing")
    steps = commands(block)
    if not steps or tokens_of(steps[0]) != [WORK_TOKEN]:
        return [*found, f"the first command is not {WORK_LINE}"]
    wavs: list[str] = []
    copies: list[tuple[str, str]] = []
    made: list[str] = []
    scores: list[list[str]] = []
    for step in steps[1:]:
        tokens = strip_runner(tokens_of(step))
        if tokens[:2] == ["python", "-I"] or tokens[:2] == ["python3", "-I"]:
            if len(tokens) != 4 or tokens[2] != MAKE_WAV or not tokens[3].startswith("$WORK/"):
                found.append(f"a make_wav command is not 'python -I {MAKE_WAV} $WORK/NAME': {step}")
            else:
                wavs.append(tokens[3])
        elif tokens[:2] == ["mkdir", "-p"] and len(tokens) == 3 and tokens[2].startswith("$WORK/"):
            made.append(tokens[2])
        elif tokens[:1] == ["cp"] and len(tokens) == 3 and tokens[2].startswith("$WORK/"):
            copies.append((tokens[1], tokens[2]))
        elif tokens[:2] == ["tracer", "score"]:
            scores.append(tokens[2:])
        else:
            found.append(f"a command is none of the forms [{STEP_FORMS}]: {step}")
    if len(wavs) != 1 or len(scores) != 1:
        return [
            *found,
            f"expected one make_wav and one score command, got {len(wavs)} and {len(scores)}",
        ]
    if not (root / MAKE_WAV).is_file():
        found.append(f"{MAKE_WAV} does not exist")
    found += score_problems(scores[0], wavs[0], copies, made, root)
    return found


def score_problems(
    argv: list[str], wav: str, copies: list[tuple[str, str]], made: list[str], root: Path
) -> list[str]:
    found: list[str] = []
    parser = score_parser()
    names = option_names(parser)
    for token in argv:
        if token.startswith("--") and token not in names:
            found.append(f"{token} is not an option of 'tracer score'")
    present = {t for t in argv if t.startswith("--")}
    for option in sorted(required_options(parser) - present):
        found.append(f"the required option {option} is missing")
    try:
        args = cli_module().build_parser().parse_args(["score", *argv])
    except BaseException as error:  # the parser raises UsageError or SystemExit
        return [*found, f"the real parser refuses the command: {type(error).__name__}: {error}"]
    if args.audio != wav:
        found.append("the recording of 'tracer score' is not the one make_wav writes")
    if args.transcript != EXPORT:
        found.append(f"--transcript is not {EXPORT}")
    if args.rubric != RUBRIC:
        found.append(f"--rubric is not {RUBRIC}")
    if args.declared_language != "mr":
        found.append("--declared-language is not mr")
    if not args.out.startswith("$WORK/"):
        found.append("--out is not below $WORK (a directory outside the repository)")
    if args.dry_run:
        found.append("the quick start is a dry run: it must score")
    if not args.stt.startswith("fake:$WORK/"):
        found.append("--stt is not fake:$WORK/DIR")
        return found
    replay = args.stt[len("fake:") :]
    if replay not in made:
        found.append(f"the replay directory {replay} is not created by 'mkdir -p'")
    want = {
        f"{replay}/{name}": EXAMPLES.joinpath(f"stt_chunk_{i:02d}.json").as_posix()
        for i, name in enumerate(FAKE_FILES)
    }
    got = {dst: src for src, dst in copies}
    if got != want:
        found.append(f"the replay copies are {sorted(got.items())}, not {sorted(want.items())}")
    for src in got.values():
        if not (root / src).is_file():
            found.append(f"{src} does not exist")
    for path in (EXPORT, RUBRIC):
        if not (root / path).is_file():
            found.append(f"{path} does not exist")
    return found


def exit_code_problems(body: str, codes: dict[int, str]) -> list[str]:
    found: list[str] = []
    if not re.search(r"^\| Code \| Meaning \|[ \t]*\n\|[-| :]+\|[ \t]*$", body, re.M):
        found.append("the exit code table has no '| Code | Meaning |' header and separator line")
    rows = {
        int(m.group(1)): m.group(2).strip()
        for m in re.finditer(r"^\| (\d+) \| (.+?) \|?\s*$", body, re.M)
    }
    for code, meaning in codes.items():
        if code not in rows:
            found.append(f"exit code {code} ({meaning}) is not in the table")
        elif meaning.lower() not in rows[code].lower():
            found.append(f"exit code {code} is not described as {meaning!r}")
    for code in sorted(set(rows) - set(codes)):
        found.append(f"the table has exit code {code}, which the command does not use")
    return found


def privacy_problems(body: str) -> list[str]:
    plain = body.replace("`", "").replace("*", "")
    units = [u for u in re.split(r"(?<=[.!?])\s+|\n- |\n\n", plain) if u.strip()]
    found: list[str] = []
    wanted: dict[str, tuple[str, ...]] = {
        "audio leaves only with --stt sarvam": (r"--stt sarvam", r"\baudio\b", r"\bonly\b"),
        "the key is never on the command line": (r"SARVAM_API_KEY", r"never", r"command line"),
        "a replay sends nothing": (r"fake:", r"sends? nothing|nothing is sent"),
        "report and store hold call text and stay out of git": (
            r"\breport\b",
            r"\bstore\b",
            r"\btext\b",
            r"\bgit\b",
        ),
        "--out outside the repository": (r"--out", r"outside", r"repository"),
        "every privacy and language option is required": (
            r"\brequired\b",
            r"no default|none has a default",
        ),
        "the run book of ADR 0024": (r"Run book for a real send", r"ADR 0024"),
    }
    for label, patterns in wanted.items():
        if not any(all(re.search(p, u, re.I) for p in patterns) for u in units):
            found.append(f"privacy: no statement that {label}")
    for target in (CONCEPT_LINK, RUN_BOOK_LINK):
        if f"]({target}" not in body:
            found.append(f"privacy: no link to {target}")
    return found


# ------------------------------------------------------------------------------ the real README


def test_readme_quick_start_is_parseable_and_matches_the_parser() -> None:
    text = README.read_text("utf-8")
    assert readme_problems(text) == []


def test_readme_names_every_exit_code_of_the_command() -> None:
    body = sections(README.read_text("utf-8")).get("Exit codes", "")
    assert exit_code_problems(body, cli_module().EXIT_CODES) == []
    assert set(cli_module().EXIT_CODES) == {0, 1, 2, 3, 4, 64}


def run_problems(
    text: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> list[str]:
    """Run the shell block of ``text`` in-process and offline; the ways it disagrees with the
    expected-output block, the exit code or the files it leaves."""
    body = sections(text).get(QUICK_START, "")
    shells = fenced(body, "(?:sh|bash|shell|console)")
    blocks = fenced(body, "text")
    if len(shells) != 1 or len(blocks) != 1:
        return [
            f"the quick start has {len(shells)} shell and {len(blocks)} text blocks, not 1 and 1"
        ]
    steps = commands(shells[0])
    expected = [ln for ln in blocks[0].splitlines() if ln.strip()]
    found: list[str] = []
    before = tree_files(ROOT / "docs" / "examples")
    monkeypatch.chdir(ROOT)
    for key in ("SARVAM_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    with tempfile.TemporaryDirectory() as work:
        out = run_steps(steps, work, capsys)
        if out.code != 0:
            found.append(f"the command exits {out.code}, not 0")
        lines = out.stdout.splitlines()
        for line in expected:
            pattern = re.escape(line)
            pattern = re.sub(r"<[a-z_ ]+>", ".+", pattern.replace("\\<", "<").replace("\\>", ">"))
            if not any(re.fullmatch(pattern, got) for got in lines):
                found.append(f"not in the real output: {line!r}")
        for needed in EXPECTED_LINES:
            if not any(line.startswith(needed) for line in expected):
                found.append(f"the expected output lacks {needed!r}")
        if out.code == 0:
            files = sorted(p.name for p in Path(out.out_dir).iterdir())
            if "tracer.sqlite" not in files:
                found.append("no tracer.sqlite in --out")
            reports = [f for f in files if re.fullmatch(r"report-\d{8}T\d{6}Z\.html", f)]
            if len(reports) != 1:
                found.append(f"{len(reports)} report files in --out, not one")
    if tree_files(ROOT / "docs" / "examples") != before:
        found.append("the quick start wrote into docs/examples")
    return found


def test_readme_quick_start_runs_offline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    text = README.read_text("utf-8")
    assert sections(text).get(QUICK_START), f"packages/cli/README.md has no section {QUICK_START!r}"
    assert run_problems(text, monkeypatch, capsys) == []


class Outcome:
    def __init__(self, code: int, stdout: str, out_dir: str) -> None:
        self.code, self.stdout, self.out_dir = code, stdout, out_dir


def tree_files(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


def run_steps(steps: list[str], work: str, capsys: pytest.CaptureFixture[str]) -> Outcome:
    code, out_dir, stdout = -1, "", ""
    for step in steps[1:]:
        tokens = [
            t.replace("${WORK}", work).replace("$WORK", work) for t in strip_runner(tokens_of(step))
        ]
        if tokens[0] in ("python", "python3"):
            subprocess.run(  # noqa: S603
                [sys.executable, *tokens[1:]],
                cwd=ROOT,
                check=True,
                env=clean_env(),
                capture_output=True,
            )
        elif tokens[:2] == ["mkdir", "-p"]:
            Path(tokens[2]).mkdir(parents=True)
        elif tokens[0] == "cp":
            shutil.copyfile(tokens[1], tokens[2])
        elif tokens[:2] == ["tracer", "score"]:
            capsys.readouterr()
            code = cli_module().main(tokens[1:])
            stdout = capsys.readouterr().out
            out_dir = tokens[tokens.index("--out") + 1]
        else:
            raise AssertionError(f"unsupported step {step!r}")
    return Outcome(code, stdout, out_dir)


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT")}


# ------------------------------------------------------------------------------ teeth

GOOD_SHELL = f"""\
{WORK_LINE}
python -I {MAKE_WAV} "$WORK/synthetic_mr.wav"
mkdir -p "$WORK/replay"
cp {EXAMPLES.as_posix()}/stt_chunk_00.json "$WORK/replay/chunk-0000.json"
cp {EXAMPLES.as_posix()}/stt_chunk_01.json "$WORK/replay/chunk-0001.json"
cp {EXAMPLES.as_posix()}/stt_chunk_02.json "$WORK/replay/chunk-0002.json"
tracer score "$WORK/synthetic_mr.wav" \\
  --transcript {EXPORT} \\
  --rubric {RUBRIC} \\
  --declared-language mr \\
  --caller-origin simulated \\
  --consent unknown \\
  --retention keep \\
  --out "$WORK/out" \\
  --stt "fake:$WORK/replay"
"""


def good_readme(shell: str = GOOD_SHELL) -> str:
    codes = "\n".join(f"| {c} | {m} |" for c, m in sorted(cli_module().EXIT_CODES.items()))
    return f"""# tracer-cli

The `tracer` command line.

## {QUICK_START}

Run these from the repository root. The work directory is outside the repository.
The replay files are named `chunk-NNNN.json` (four digits).

```sh
{shell}```

Expected output (lines with a name in angle brackets differ from run to run):

```text
result: scored
agreement distance (MER): 0.10655737704918032 (13 of 122; H 109, S 9, D 2, I 2)
flags: 8 (confidence at or above 0.5)
flagged share of export tokens: 9 of 120 (0.075)
store: <out>/tracer.sqlite
```

## Exit codes

| Code | Meaning |
|---|---|
{codes}

## Privacy and a real send

The audio leaves your machine only with --stt sarvam.
SARVAM_API_KEY is read from the environment and is never on the command line.
With fake: a replay sends nothing.
The report and the store hold call text and must stay out of git.
Put --out outside the repository.
Every privacy and language option is required and none has a default.
Follow the Run book for a real send of ADR 0024: [run book]({RUN_BOOK_LINK}).
See [the concept page]({CONCEPT_LINK}).
"""


@pytest.fixture(scope="module")
def fake_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repository skeleton with every file the good README names, so that the checker's own
    tests do not depend on the files the architect writes."""
    root = tmp_path_factory.mktemp("fake-root")
    names = [MAKE_WAV, EXPORT, RUBRIC, "docs/concepts/recorded-call-scoring.md"]
    names += [(EXAMPLES / f"stt_chunk_{i:02d}.json").as_posix() for i in range(3)]
    names += ["docs/adr/0024-provider-adapters-byok-sarvam-rest.md", "packages/cli/README.md"]
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x\n", encoding="utf-8")
    return root


def test_the_checker_accepts_a_good_readme(fake_root: Path) -> None:
    assert readme_problems(good_readme(), fake_root) == []


Mutation = Callable[[str], str]
SHELL_MUTANTS: dict[str, tuple[Mutation, str]] = {
    "unknown option": (
        lambda s: s.replace("--retention keep", "--retention keep --nope 1"),
        "--nope is not an option",
    ),
    "required option missing": (
        lambda s: s.replace("  --consent unknown \\\n", ""),
        "required option --consent",
    ),
    "retention value refused by the parser": (
        lambda s: s.replace("--retention keep", "--retention forever"),
        "real parser refuses",
    ),
    "sarvam engine": (lambda s: s.replace('"fake:$WORK/replay"', "sarvam"), "sends nothing"),
    "key in the block": (
        lambda s: s.replace("tracer score", "SARVAM_API_KEY=x tracer score"),
        "sends nothing",
    ),
    "embed audio": (
        lambda s: s.replace("--retention keep", "--retention keep --embed-audio"),
        "sends nothing",
    ),
    "network tool": (lambda s: s + "curl https://example.com\n", "sends nothing"),
    "replay file misnamed": (
        lambda s: s.replace("chunk-0001.json", "chunk-1.json"),
        "replay copies",
    ),
    "replay file missing": (
        lambda s: s.replace(
            'cp docs/examples/synthetic-mr/stt_chunk_02.json "$WORK/replay/chunk-0002.json"\n', ""
        ),
        "replay copies",
    ),
    "replay source wrong": (
        lambda s: s.replace("stt_chunk_01.json", "stt_chunk_00.json"),
        "replay copies",
    ),
    "replay directory not created": (
        lambda s: s.replace('mkdir -p "$WORK/replay"\n', ""),
        "not created",
    ),
    "out inside the repository": (
        lambda s: s.replace('--out "$WORK/out"', "--out out"),
        "--out is not below $WORK",
    ),
    "dry run": (lambda s: s.replace("--stt", "--dry-run --stt"), "dry run"),
    "wrong transcript": (
        lambda s: s.replace("export_synthetic_mr.txt", "export.txt"),
        "--transcript is not",
    ),
    "wrong language": (
        lambda s: s.replace("--declared-language mr", "--declared-language hi"),
        "--declared-language is not mr",
    ),
    "wav path differs": (
        lambda s: s.replace(
            'tracer score "$WORK/synthetic_mr.wav"', 'tracer score "$WORK/other.wav"'
        ),
        "recording of",
    ),
    "no -I": (lambda s: s.replace("python -I", "python"), "none of the forms"),
    "other command": (lambda s: s + "rm -rf /\n", "none of the forms"),
    "first command is not the work directory": (
        lambda s: s.replace(WORK_LINE, "WORK=/tmp/x"),
        "first command",
    ),
    "script missing": (lambda s: s.replace("make_wav.py", "make_wave.py"), "make_wav command"),
}


@pytest.mark.parametrize("name", sorted(SHELL_MUTANTS))
def test_the_checker_refuses_each_mutated_shell_block(name: str, fake_root: Path) -> None:
    mutate, expected = SHELL_MUTANTS[name]
    assert mutate(GOOD_SHELL) != GOOD_SHELL
    problems = readme_problems(good_readme(mutate(GOOD_SHELL)), fake_root)
    assert any(expected in p for p in problems), (name, problems)


TEXT_MUTANTS: dict[str, tuple[Mutation, str]] = {
    "scaffold only": (lambda t: t + "\nStatus: scaffold only.\n", "scaffold only"),
    "exit code row missing": (
        lambda t: t.replace("| 3 | speech-to-text failed |\n", ""),
        "exit code 3",
    ),
    "exit code described otherwise": (
        lambda t: t.replace("| 4 | input refused |", "| 4 | bad |"),
        "exit code 4",
    ),
    "extra exit code": (
        lambda t: t.replace("| 64 | usage |", "| 64 | usage |\n| 5 | other |"),
        "exit code 5",
    ),
    "no repository root": (lambda t: t.replace("repository root", "top folder"), "repository root"),
    "no privacy statement about git": (
        lambda t: t.replace("must stay out of git", "are private"),
        "report and store",
    ),
    "no key statement": (
        lambda t: t.replace("is never on the command line", "is read"),
        "the key is never",
    ),
    "no run book link": (lambda t: t.replace(RUN_BOOK_LINK, "x.md"), "no link to"),
    "section missing": (lambda t: t.replace("## Exit codes", "## Codes"), "sections"),
    "dead extra link": (lambda t: t + "\n[x](nope.md)\n", "a link does not resolve"),
    "no chunk naming statement": (
        lambda t: t.replace("(four digits)", "(numbered)"),
        "four digits",
    ),
    "no table separator": (
        lambda t: t.replace("| Code | Meaning |\n|---|---|\n", "| Code | Meaning |\n"),
        "header",
    ),
    "no table header": (lambda t: t.replace("| Code | Meaning |", "| a | b |"), "header"),
    "dead link": (lambda t: t.replace(CONCEPT_LINK, "../../docs/concepts/none.md"), "link"),
    "title": (lambda t: t.replace("# tracer-cli", "# cli", 1), "first line"),
}


@pytest.mark.parametrize("name", sorted(TEXT_MUTANTS))
def test_the_checker_refuses_each_mutated_readme(name: str, fake_root: Path) -> None:
    mutate, expected = TEXT_MUTANTS[name]
    text = good_readme()
    assert mutate(text) != text
    problems = readme_problems(mutate(text), fake_root)
    assert any(expected in p for p in problems), (name, problems)


def test_a_second_shell_block_is_refused(fake_root: Path) -> None:
    text = good_readme().replace("Expected output", "```sh\necho hi\n```\n\nExpected output")
    assert any("fenced shell blocks, not one" in p for p in readme_problems(text, fake_root))


def test_the_run_check_accepts_the_good_readme_and_refuses_a_wrong_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    good = good_readme()
    assert run_problems(good, monkeypatch, capsys) == []
    wrong_line = good.replace("flags: 8 (", "flags: 9 (")
    assert any("not in the real output" in p for p in run_problems(wrong_line, monkeypatch, capsys))
    refused = good.replace("--declared-language mr", "--declared-language hi")
    assert any("exits" in p for p in run_problems(refused, monkeypatch, capsys))
    missing = good.replace("flagged share of export tokens: 9 of 120 (0.075)\n", "")
    assert any("lacks" in p for p in run_problems(missing, monkeypatch, capsys))
