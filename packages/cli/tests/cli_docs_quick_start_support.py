# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared checker and runner of the two offline quick starts of ``packages/cli/README.md``
(P4-37: ``tracer simulate`` with ``fake:`` engines; P4-J30: the judged run with ``--judge fake:``).

Three layers, so the README text cannot drift from the code:

1. ``static_problems``: the section's one fenced shell block is parsed. Every step is one of the
   forms in ``STEP_FORMS``; every ``tracer`` command is parsed by the real argparse parser; the
   engines are the ``fake`` ones; nothing in the block can send data (no real engine, no key, no
   ``--env-file``, no ``--target``, no allowlist, no URL, no network tool); every repository path
   exists; every path of the work directory is below ``$WORK``.
2. ``run_quick_start``: the commands are run, exactly as written, by ``sh`` in a work directory of
   the test, with the real ``tracer`` entry point of the installed environment (a one-line ``uv``
   shim removes ``uv run --locked`` and nothing else), a clean environment (no key, no proxy) and
   a ``sitecustomize`` that makes every outbound socket call raise. The exit code of every step is
   recorded.
3. ``output_problems``: the "Expected output" blocks of the section, in order, against the real
   standard output (a name in angle brackets stands for any text on that line).

Nothing here imports a Tracer package except ``tracer_cli.cli`` for its parser.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
README = ROOT / "packages" / "cli" / "README.md"
EXAMPLES = "docs/examples/simulated-mr"
SIM_TITLE = "Simulate quick start (offline, no key, no network)"
JUDGE_TITLE = "Judge quick start (offline, no key, no network)"
EXIT_TITLE = "Exit codes of tracer simulate, tracer judge and tracer labels"
WORK_LINE = 'WORK="$(mktemp -d)"'

STEP_FORMS = (
    'WORK="$(mktemp -d)"; [uv run --locked] tracer simulate|judge|labels ... [> "$WORK/FILE"]; '
    "[uv run --locked] python -I docs/examples/simulated-mr/fill_labels.py ...; "
    'BUNDLE="$(sed -n \'s/^bundle: //p\' "$WORK/FILE")"; grep -E \'PATTERN\' "$WORK/FILE"'
)

FORBIDDEN_IN_BLOCK = (
    r"--tts\s+sarvam",
    r"--stt\s+sarvam",
    r"--caller\s+sarvam",
    r"--judge\s+(?!fake:|replay:)\S",
    r"SARVAM_API_KEY",
    r"--env-file",
    r"--target\b",
    r"--allowlist\b",
    r"--judge-allowlist\b",
    r"--dry-run\b",
    r"--max-judge",
    r"--judge-first-request-gate",
    r"\bcurl\b",
    r"\bwget\b",
    r"https?://",
    r"wss?://",
    r"--embed-audio",
)
SIM_FORBIDDEN_EXTRA = (r"--golden\b", r"--judge\b", r"--judge-config\b")

REQUIRED_SIM_OPTIONS = {
    "--agent", "--evaluator", "--persona", "--sim-profile", "--tts", "--caller", "--stt",
    "--authorisation-reference", "--out",
}  # fmt: skip

SITECUSTOMIZE = textwrap.dedent(
    '''\
    """Every outbound socket call raises: a quick start that tries the network fails."""
    import socket


    def _refuse(*args, **kwargs):
        raise OSError("quick start guard: the network is closed")


    socket.socket.connect = _refuse
    socket.socket.connect_ex = _refuse
    socket.create_connection = _refuse
    socket.getaddrinfo = _refuse
    '''
)
UV_SHIM = """#!/bin/sh
# stands in for `uv run --locked X ...`: runs X from the environment of the test, nothing else
[ "$1" = run ] || exit 97
shift
while [ "${1#--}" != "$1" ]; do shift; done
exec "$@"
"""
MKTEMP_SHIM = """#!/bin/sh
# `mktemp -d` that honours $TMPDIR on every platform (the macOS one does not); nothing else changes
if [ "$#" -eq 1 ] && [ "$1" = -d ]; then exec /usr/bin/mktemp -d "$TMPDIR/tmp.XXXXXXXXXX"; fi
exec /usr/bin/mktemp "$@"
"""
ENV_KEEP = ("LANG", "LC_ALL")


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
    """The steps of a shell block: continuation lines joined, comments and blank lines gone."""
    joined = re.sub(r"\\\n\s*", " ", block)
    return [
        ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]


def strip_runner(tokens: list[str]) -> list[str]:
    """Drop a leading ``uv run`` and its options."""
    if tokens[:2] == ["uv", "run"]:
        rest = tokens[2:]
        while rest and rest[0].startswith("--"):
            rest = rest[1:]
        return rest
    return tokens


def cli_module() -> Any:
    import importlib

    return importlib.import_module("tracer_cli.cli")


def subparser(name: str, *path: str) -> Any:
    import argparse

    parser = cli_module().build_parser()
    for step in (name, *path):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction) and step in action.choices:
                parser = action.choices[step]
                break
        else:
            raise AssertionError(f"no subcommand {step}")
    return parser


def option_names(parser: Any) -> set[str]:
    return {s for a in parser._actions for s in a.option_strings}


def required_options(parser: Any) -> set[str]:
    return {a.option_strings[0] for a in parser._actions if a.required and a.option_strings}


# ------------------------------------------------------------------------------ static layer


def _substitute(token: str) -> str:
    return token.replace("${WORK}", "/work").replace("$WORK", "/work").replace("$BUNDLE", "rbnd_x")


def static_problems(text: str, title: str, *, judged: bool, root: Path = ROOT) -> list[str]:
    """Every way the section ``title`` of ``text`` breaks the rules of the module docstring."""
    found: list[str] = []
    body = sections(text).get(title)
    if body is None:
        return [f"packages/cli/README.md has no section {title!r}"]
    shells = fenced(body, "(?:sh|bash|shell|console)")
    blocks = fenced(body, "text")
    if len(shells) != 1:
        return [f"the quick start has {len(shells)} fenced shell blocks, not one"]
    if len(blocks) != 1:
        found.append(f"the quick start has {len(blocks)} expected-output blocks, not one")
    block = shells[0]
    for pattern in FORBIDDEN_IN_BLOCK + (() if judged else SIM_FORBIDDEN_EXTRA):
        if re.search(pattern, block):
            found.append(f"the shell block holds {pattern!r}: an offline quick start sends nothing")
    if "repository root" not in body:
        found.append("the quick start does not say the commands run from the repository root")
    if not re.search(r"each command exits 0|every command exits 0|exit code 0", body, re.I):
        found.append("the quick start does not say each command exits 0")
    if not re.search(r"\bnothing is sent\b|\bsends nothing\b", body, re.I):
        found.append("the quick start does not say that nothing is sent")
    if not re.search(r"differ from run to run|vary from run to run", body, re.I):
        found.append("the quick start does not say that instants and ids differ from run to run")
    steps = commands(block)
    if not steps or shlex.split(steps[0]) != ["WORK=$(mktemp -d)"]:
        return [*found, f"the first command is not {WORK_LINE}"]
    ran = 0
    for step in steps[1:]:
        found += _step_problems(step, judged=judged, root=root)
        if re.match(r"(?:uv run (?:--\S+ )*)?tracer (?:simulate|judge|labels)\b", step):
            ran += 1
    if ran == 0:
        found.append("the block runs no tracer command")
    simulates = [s for s in steps if re.search(r"\btracer simulate\b", s)]
    if len(simulates) != 1:
        found.append(f"the block has {len(simulates)} simulate commands, not one")
    if judged:
        for needed in (
            "tracer judge",
            "tracer labels packet",
            "tracer labels check",
            "tracer labels agreement",
        ):
            if not any(needed in s for s in steps):
                found.append(f"the judge quick start has no {needed!r} step")
        if not any(re.search(r"--repeats\s+[2-9]", s) for s in steps):
            found.append("the judge quick start repeats the judgement fewer than 2 times")
    for target in re.findall(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)", body):
        if (
            not re.match(r"[a-z]+:", target)
            and not (root / "packages/cli" / target.split("#")[0]).resolve().exists()
        ):
            found.append(f"a link does not resolve: {target}")
    return found


_REDIRECT = re.compile(r'\s>\s("?\$WORK/[A-Za-z0-9_.-]+"?)\s*$')
_GREP = re.compile(r"""^grep -E '[^']+' "\$WORK/[A-Za-z0-9_.-]+"$""")
_BUNDLE = re.compile(r"""^BUNDLE="\$\(sed -n 's/\^bundle: //p' "\$WORK/[A-Za-z0-9_.-]+"\)"$""")


def _step_problems(step: str, *, judged: bool, root: Path) -> list[str]:
    if _GREP.fullmatch(step) or _BUNDLE.fullmatch(step):
        return []
    command = _REDIRECT.sub("", step)
    try:
        tokens = strip_runner(shlex.split(command))
    except ValueError as error:
        return [f"a step does not parse as shell words: {step!r} ({error})"]
    if tokens[:2] == ["python", "-I"]:
        script = f"{EXAMPLES}/fill_labels.py"
        if len(tokens) != 5 or tokens[2] != script:
            return [f"a python step is not 'python -I {script} PACKET LABELS': {step!r}"]
        found = [] if (root / script).is_file() else [f"{script} does not exist"]
        found += [f"{t} is not below $WORK" for t in tokens[3:] if not t.startswith("$WORK/")]
        return found
    if tokens[:1] != ["tracer"] or len(tokens) < 2:
        return [f"a step is none of the forms [{STEP_FORMS}]: {step!r}"]
    if tokens[1] == "labels":
        sub: tuple[str, ...] = ("labels", tokens[2]) if len(tokens) > 2 else ("labels",)
        argv = tokens[1:]
    else:
        sub = (tokens[1],)
        argv = tokens[1:]
    if tokens[1] not in ("simulate", "judge", "labels"):
        return [f"{tokens[1]!r} is not a command of the quick start"]
    problems: list[str] = []
    try:
        parser = subparser(*sub)
    except AssertionError as error:
        return [str(error)]
    names = option_names(parser)
    for token in tokens:
        if token.startswith("--") and token not in names:
            problems.append(f"{token} is not an option of 'tracer {' '.join(sub)}'")
    present = {t for t in tokens if t.startswith("--")}
    for option in sorted(required_options(parser) - present):
        problems.append(f"the required option {option} is missing")
    substituted = [_substitute(t) for t in argv]
    try:
        args = cli_module().build_parser().parse_args(substituted)
    except BaseException as error:  # the parser raises UsageError or SystemExit
        return [*problems, f"the real parser refuses the command: {type(error).__name__}: {error}"]
    if tokens[1] == "simulate":
        problems += _simulate_problems(args, judged=judged, root=root)
    else:
        problems += _judge_labels_problems(args, tokens[1], root=root)
    return problems


def _repo_path(value: str | None, root: Path) -> list[str]:
    if value is None:
        return []
    if value.startswith("/work"):
        return []
    return [] if (root / value).is_file() else [f"{value} does not exist"]


def _simulate_problems(args: Any, *, judged: bool, root: Path) -> list[str]:
    problems: list[str] = []
    if not str(args.agent or "").startswith("fake:"):
        problems.append("--agent is not fake:FILE")
    else:
        problems += _repo_path(args.agent[len("fake:") :], root)
    if args.target is not None:
        problems.append("--target is a real agent")
    if args.tts != "fake":
        problems.append("--tts is not fake")
    if args.caller != "scripted":
        problems.append("--caller is not scripted")
    if args.stt != "fake":
        problems.append("--stt is not fake")
    if args.dry_run:
        problems.append("the quick start is a dry run: it must place the call")
    if not str(args.out).startswith("/work/"):
        problems.append("--out is not below $WORK (a directory outside the repository)")
    for value in (args.evaluator, args.persona, args.sim_profile, args.golden, args.judge_config):
        problems += _repo_path(value, root)
    if judged:
        if not str(args.judge or "").startswith("fake:"):
            problems.append("--judge is not fake:SCENARIO")
        if args.golden is None or args.judge_config is None:
            problems.append("a judged run needs --golden and --judge-config")
    elif args.golden or args.judge or args.judge_config:
        problems.append("the simulate quick start holds a judge option")
    return problems


def _judge_labels_problems(args: Any, command: str, *, root: Path) -> list[str]:
    problems: list[str] = []
    if not str(args.out).startswith("/work/"):
        problems.append("--out is not below $WORK (a directory outside the repository)")
    if command == "judge":
        if not str(args.judge or "").startswith(("fake:", "replay:")):
            problems.append("--judge is not fake: or replay:")
        problems += _repo_path(args.golden, root)
        problems += _repo_path(args.judge_config, root)
    return problems


# ------------------------------------------------------------------------------ run layer


@dataclass(frozen=True)
class QuickStartRun:
    steps: tuple[str, ...]
    codes: tuple[int | None, ...]
    stdout: str
    stderr: str
    work: tuple[str, ...]
    files: tuple[str, ...]
    modes: dict[str, int]
    tree_changed: tuple[str, ...]


def tree_files(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


def clean_environment(bin_dirs: list[str], home: str, tmp: str, guard: str) -> dict[str, str]:
    env = {
        "PATH": os.pathsep.join([*bin_dirs, "/usr/bin", "/bin"]),
        "HOME": home,
        "TMPDIR": tmp,
        "PYTHONPATH": guard,
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
    }
    for key in ENV_KEEP:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def driver_script(steps: list[str]) -> str:
    """One ``sh`` script: each step as written, the exit code of each appended to ``$RC_FILE``."""
    lines = []
    for step in steps:
        lines.append(step)
        lines.append('echo "$?" >> "$RC_FILE"')
    return "\n".join(lines) + "\n"


def run_quick_start(block: str, *, timeout: int = 600) -> QuickStartRun:
    steps = commands(block)
    before_examples = tree_files(ROOT / "docs" / "examples")
    before_top = sorted(p.name for p in ROOT.iterdir())
    with tempfile.TemporaryDirectory(prefix="qs-") as raw:
        base = Path(raw)
        shim = base / "shim"
        guard = base / "guard"
        tmp = base / "tmp"
        home = base / "home"
        for directory in (shim, guard, tmp, home):
            directory.mkdir()
        (guard / "sitecustomize.py").write_text(SITECUSTOMIZE, "utf-8")
        uv = shim / "uv"
        uv.write_text(UV_SHIM, "utf-8")
        uv.chmod(0o755)
        mktemp = shim / "mktemp"
        mktemp.write_text(MKTEMP_SHIM, "utf-8")
        mktemp.chmod(0o755)
        rc_file = base / "rc.txt"
        env = clean_environment(
            [str(shim), str(Path(sys.executable).parent)], str(home), str(tmp), str(guard)
        )
        env["RC_FILE"] = str(rc_file)
        done = subprocess.run(  # noqa: S603 - the commands of the README, offline, in a temp dir
            ["/bin/sh", "-c", driver_script(steps)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        codes: list[int | None] = []
        if rc_file.is_file():
            codes = [int(x) for x in rc_file.read_text("utf-8").split()]
        codes += [None] * (len(steps) - len(codes))
        work = tuple(sorted(p.name for p in tmp.iterdir()))
        files: list[str] = []
        modes: dict[str, int] = {}
        for directory in tmp.iterdir():
            for path in directory.rglob("*"):
                if path.is_file():
                    rel = str(path.relative_to(directory))
                    files.append(rel)
                    modes[rel] = stat.S_IMODE(path.stat().st_mode)
        changed = sorted(
            set(tree_files(ROOT / "docs" / "examples")) ^ set(before_examples)
        ) + sorted({p.name for p in ROOT.iterdir()} ^ set(before_top))
        return QuickStartRun(
            steps=tuple(steps),
            codes=tuple(codes),
            stdout=done.stdout,
            stderr=done.stderr,
            work=work,
            files=tuple(sorted(files)),
            modes=modes,
            tree_changed=tuple(changed),
        )


def guard_refuses_the_network() -> str:
    """Run a one-line Python under the same guard; the output says what happened."""
    with tempfile.TemporaryDirectory(prefix="qs-guard-") as raw:
        guard = Path(raw)
        (guard / "sitecustomize.py").write_text(SITECUSTOMIZE, "utf-8")
        env = clean_environment([str(Path(sys.executable).parent)], raw, raw, raw)
        done = subprocess.run(
            [sys.executable, "-c", "import socket; socket.create_connection(('127.0.0.1', 9))"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return done.stderr + str(done.returncode)


# ------------------------------------------------------------------------------ output layer


def run_problems(run: QuickStartRun, *, expect_zero: bool = True) -> list[str]:
    found: list[str] = []
    for step, code in zip(run.steps, run.codes, strict=True):
        if expect_zero and code != 0:
            found.append(f"the step exits {code}, not 0: {step[:70]!r}")
    if run.tree_changed:
        found.append(f"the quick start changed the repository tree: {list(run.tree_changed)}")
    if len(run.work) != 1:
        found.append(f"{len(run.work)} work directories, not one")
    return found


def output_problems(run: QuickStartRun, expected_block: str) -> list[str]:
    """The lines of ``expected_block``, in order, against the standard output; ``<name>`` stands
    for any text."""
    got = run.stdout.splitlines()
    found: list[str] = []
    at = 0
    for line in (ln for ln in expected_block.splitlines() if ln.strip()):
        pattern = re.escape(line)
        pattern = re.sub(r"<[a-z_ ]+>", ".+", pattern.replace("\\<", "<").replace("\\>", ">"))
        for index in range(at, len(got)):
            if re.fullmatch(pattern, got[index]):
                at = index + 1
                break
        else:
            found.append(f"not in the real output, in order: {line!r}")
    return found
