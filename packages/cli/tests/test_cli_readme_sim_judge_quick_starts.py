# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-37 and P4-J30: the two offline quick starts of ``packages/cli/README.md`` run, as written.

* ``test_sim_readme_quick_start_runs_offline``: the section "Simulate quick start (offline, no
  key, no network)" (``tracer simulate`` with a fake agent, fake text-to-speech and fake
  speech-to-text) is parsed and then RUN by ``sh`` with the real ``tracer`` entry point, in a
  clean environment with every outbound socket call refused; every step exits 0; the "Expected
  output" block matches the real standard output, line by line; the store, the recording, the
  report and the call log are in ``--out``; nothing is written into the repository.
* ``test_judge_readme_quick_start_runs_offline``: the same for the section "Judge quick start
  (offline, no key, no network)": the judged run with ``--judge fake:agrees``, ``tracer judge`` with
  two repeats, the label loop (packet, labels check, agreement); the files are private (0600).
* the example inputs (``docs/examples/simulated-mr``) validate against the real parser and agree
  with the fixtures they were made from; the helper that stands in for a labeller works.
* the exit code table of the three commands is the code's (``SIM_EXIT_CODES``).

The checker has teeth: mutations of the README text that must be refused, run-level mutations
(a step that exits non-zero, a socket call under the guard, an expected line that is not in the
output).
"""

# ruff: noqa: E501

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from cli_docs_quick_start_support import (  # type: ignore[import-not-found,unused-ignore]
    EXAMPLES,
    EXIT_TITLE,
    JUDGE_TITLE,
    README,
    ROOT,
    SIM_TITLE,
    QuickStartRun,
    cli_module,
    fenced,
    guard_refuses_the_network,
    output_problems,
    run_problems,
    run_quick_start,
    sections,
    static_problems,
)

EXAMPLE_DIR = ROOT / "docs" / "examples" / "simulated-mr"
FIXTURES = ROOT / "packages" / "evalcore" / "tests" / "fixtures"
EXPECTED_FILES = ("tracer.sqlite", "sim_call_log.jsonl")


def _text() -> str:
    text: str = README.read_text("utf-8")
    return text


@functools.cache
def _run(title: str) -> QuickStartRun:
    body = sections(_text()).get(title, "")
    (block,) = fenced(body, "(?:sh|bash|shell|console)")
    return run_quick_start(block)


def _expected(title: str) -> str:
    (block,) = fenced(sections(_text()).get(title, ""), "text")
    expected: str = block
    return expected


def _out_files(run: QuickStartRun) -> list[str]:
    return [f for f in run.files if f.startswith("out/")]


# ------------------------------------------------------------------------------ the two tests


def test_sim_readme_quick_start_runs_offline() -> None:
    """P4-37: parse, then run, the simulate quick start; exit codes, printed lines and files."""
    text = _text()
    assert sections(text).get(SIM_TITLE), f"packages/cli/README.md has no section {SIM_TITLE!r}"
    assert static_problems(text, SIM_TITLE, judged=False) == []
    run = _run(SIM_TITLE)
    assert run_problems(run) == []
    assert output_problems(run, _expected(SIM_TITLE)) == []
    out = [Path(f).name for f in _out_files(run)]
    assert "tracer.sqlite" in out
    assert "sim_call_log.jsonl" in out
    assert len([f for f in out if re.fullmatch(r"recording-\d{8}T\d{6}Z\.wav", f)]) == 1
    assert len([f for f in out if re.fullmatch(r"report-\d{8}T\d{6}Z\.html", f)]) == 1
    assert not [f for f in out if f.startswith(("packet-", "labels-"))]
    assert run.stderr == ""


def test_judge_readme_quick_start_runs_offline() -> None:
    """P4-J30: parse, then run, the judge quick start with ``--judge fake:``; exit codes, printed
    lines, files and modes."""
    text = _text()
    assert sections(text).get(JUDGE_TITLE), f"packages/cli/README.md has no section {JUDGE_TITLE!r}"
    assert static_problems(text, JUDGE_TITLE, judged=True) == []
    run = _run(JUDGE_TITLE)
    assert run_problems(run) == []
    assert output_problems(run, _expected(JUDGE_TITLE)) == []
    names = [f for f in run.files if f.startswith("out/labels/")]
    assert len([f for f in names if "/packet-rbnd_" in f]) == 1
    assert len([f for f in names if "/labels-" in f]) == 1
    for name in names:
        assert run.modes[name] == 0o600, (name, oct(run.modes[name]))
    assert not [f for f in run.files if f.startswith("out/judge/")], (
        "a fake judge wrote exchange files"
    )
    assert run.stderr == ""
    report = [f for f in run.files if re.fullmatch(r"out/report-\d{8}T\d{6}Z\.html", f)]
    assert len(report) == 1


# ------------------------------------------------------------------------------ the sections


def test_the_readme_keeps_the_phase3_quick_start_and_adds_the_sections_in_order() -> None:
    titles = list(sections(_text()))
    for title in (SIM_TITLE, JUDGE_TITLE, EXIT_TITLE):
        assert title in titles, title
    assert titles.index(SIM_TITLE) < titles.index(JUDGE_TITLE)
    assert titles.index("Quick start (offline, no key, no network)") < titles.index(SIM_TITLE)


def test_the_readme_exit_list_of_the_new_commands_is_the_codes_of_the_code() -> None:
    """The codes are a bullet list, not a table: ``test_r4_the_readme_exit_row_promises_the_recovery_
    only_where_the_code_gives_it`` (batch I) requires exactly one table row ``| 1 |`` in the whole
    README, and that row is the one of ``tracer score``."""
    body = sections(_text()).get(EXIT_TITLE, "")
    rows = {
        int(m.group(1)): m.group(2).strip()
        for m in re.finditer(r"^- `(\d+)`: (.+?)\s*$", body, re.M)
    }
    import importlib

    codes = importlib.import_module("tracer_cli.simargs").SIM_EXIT_CODES
    assert set(rows) == set(codes)
    for code, meaning in codes.items():
        assert meaning.lower() in rows[code].lower(), (code, meaning)
    assert not re.search(r"^\|", body, re.M), "a table row here breaks the batch I README guard"
    assert cli_module().EXIT_CODES  # the table of tracer score is untouched


def test_the_readme_states_the_judge_open_points() -> None:
    """BL-350 (the denominator of ``labels check``) and BL-363 (the reference is printed as a
    SHA-256 and not stored) are stated in the README, where the commands are."""
    body = sections(_text()).get(JUDGE_TITLE, "")
    plain = body.replace("`", "")
    assert re.search(r"labelled:? n of N", plain)
    assert "BL-350" in plain
    assert re.search(r"labels the file lists", plain)
    assert re.search(r"sha256", plain, re.I)
    assert "BL-363" in plain
    assert re.search(r"fake judge (?:reads|answers)", plain)


# ------------------------------------------------------------------------------ the examples


def test_example_inputs_exist_and_the_dry_run_accepts_them(tmp_path: Path) -> None:
    """The tracked example inputs validate: ``tracer simulate --dry-run`` parses every file, binds
    the golden set to the evaluator and prints the plan, sending nothing."""
    for name in (
        "evaluator.json",
        "persona.json",
        "sim-profile.json",
        "fake-agent.json",
        "golden-set.json",
        "judge-config.json",
        "fill_labels.py",
        "README.md",
    ):
        assert (EXAMPLE_DIR / name).is_file(), name
    argv = [
        "simulate",
        "--agent", f"fake:{EXAMPLES}/fake-agent.json",
        "--evaluator", f"{EXAMPLES}/evaluator.json",
        "--persona", f"{EXAMPLES}/persona.json",
        "--sim-profile", f"{EXAMPLES}/sim-profile.json",
        "--tts", "fake", "--caller", "scripted", "--stt", "fake",
        "--golden", f"{EXAMPLES}/golden-set.json",
        "--judge-config", f"{EXAMPLES}/judge-config.json",
        "--judge", "fake:agrees",
        "--out", str(tmp_path / "out"),
        "--dry-run",
    ]  # fmt: skip
    done = subprocess.run(  # noqa: S603
        [str(Path(sys.executable).parent / "tracer"), *argv],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "dry run: nothing was sent" in done.stdout
    assert "judge golden set: mr_apparatus_set, 10 items" in done.stdout
    assert not (tmp_path / "out").exists()


def test_example_inputs_agree_with_the_fixtures_they_come_from() -> None:
    golden = json.loads((FIXTURES / "golden_mr.json").read_text("utf-8"))
    sim = json.loads((FIXTURES / "sim_mr.json").read_text("utf-8"))
    assert json.loads((EXAMPLE_DIR / "golden-set.json").read_text("utf-8")) == golden["golden_set"]
    (scenario,) = [s for s in sim["scenarios"] if s["id"] == golden["bound_to"]["scenario"]]
    evaluator = json.loads((EXAMPLE_DIR / "evaluator.json").read_text("utf-8"))
    assert evaluator["scripted_turns"] == scenario["scripted_turns"]
    agent = json.loads((EXAMPLE_DIR / "fake-agent.json").read_text("utf-8"))
    spoken = [s["speak"]["text"] for s in agent["steps"] if "speak" in s]
    exact = {p["item_id"]: p["text"] for p in golden["plants"] if p["kind"] == "exact_facts"}
    assert spoken == [exact[f"mr_q{n:02d}"] for n in range(10)]
    config = json.loads((EXAMPLE_DIR / "judge-config.json").read_text("utf-8"))
    assert config["profile"] == "fake/1"
    assert config["endpoint"] is None


def test_example_readme_states_provenance_and_the_native_review_gap() -> None:
    text = (EXAMPLE_DIR / "README.md").read_text("utf-8")
    assert re.search(r"\binvented\b|\bsynthetic\b", text)
    assert "BL-169" in text
    assert re.search(r"native", text)
    assert re.search(r"plumbing", text)
    assert "../../concepts/simulated-calls.md" in text
    assert "../../concepts/llm-judge.md" in text
    for target in re.findall(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)", text):
        if not re.match(r"[a-z]+:", target):
            assert (EXAMPLE_DIR / target.split("#")[0]).resolve().exists(), target


def test_fill_labels_helper_writes_a_labels_file_and_leaves_the_last_item_unlabelled(
    tmp_path: Path,
) -> None:
    packet = {
        "labels_template": {
            "format": "tracer.judge_labels/1",
            "bundle_id": "rbnd_x",
            "labeller": "labeller-1",
            "labels": [{"item_id": f"i{n}", "verdict": None, "basis": "audio"} for n in range(3)],
        }
    }
    (tmp_path / "packet.json").write_text(json.dumps(packet), "utf-8")
    script = str(EXAMPLE_DIR / "fill_labels.py")
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            script,
            str(tmp_path / "packet.json"),
            str(tmp_path / "labels.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    labels = json.loads((tmp_path / "labels.json").read_text("utf-8"))
    assert [x["verdict"] for x in labels["labels"]] == ["agrees", "agrees", None]
    bad = subprocess.run(  # noqa: S603
        [sys.executable, "-I", script], capture_output=True, text=True, check=False
    )
    assert bad.returncode == 64


# ------------------------------------------------------------------------------ teeth: the text


def _mutate(title: str, change: Callable[[str], str], *, shell_only: bool = True) -> str:
    """The README with ``change`` applied to the shell block of the section (default) or to the
    whole body of the section."""
    text = _text()
    body = sections(text)[title]
    if not shell_only:
        return text.replace(body, change(body), 1)
    (block,) = fenced(body, "(?:sh|bash|shell|console)")
    return text.replace(body, body.replace(block, change(block), 1), 1)


BOTH = [(SIM_TITLE, False), (JUDGE_TITLE, True)]


@pytest.mark.parametrize(("title", "judged"), BOTH)
def test_the_checker_accepts_the_real_sections(title: str, judged: bool) -> None:
    assert static_problems(_text(), title, judged=judged) == []


SEND_MUTANTS = [
    "--tts sarvam", "--stt sarvam", "--caller sarvam", "--env-file x.env", "--target x.json",
    "--allowlist x.txt", "--judge-allowlist x.txt", "--dry-run", "--max-judge-requests 3",
    "--judge-first-request-gate", "--judge openrouter/1", "--embed-audio",
    "curl http://example.invalid/x", "wget x", "SARVAM_API_KEY=x",
]  # fmt: skip


@pytest.mark.parametrize("inject", SEND_MUTANTS)
@pytest.mark.parametrize(("title", "judged"), BOTH)
def test_the_checker_refuses_anything_that_could_send_data(
    title: str, judged: bool, inject: str
) -> None:
    def change(block: str) -> str:
        return block.replace("--tts fake", f"--tts fake {inject}", 1)

    problems = static_problems(_mutate(title, change), title, judged=judged)
    assert problems, inject


STRUCTURE_MUTANTS: list[tuple[str, Callable[[str], str], str, bool]] = [
    ("first step", lambda b: b.replace('WORK="$(mktemp -d)"', "WORK=/tmp/x", 1), "the first command is not", True),
    ("unknown option", lambda b: b.replace("--tts fake", "--ttsx fake", 1), "not an option", True),
    ("renamed out", lambda b: b.replace("--out", "--output", 1), "not an option", True),
    ("renamed persona", lambda b: b.replace("--persona", "--personna", 1), "not an option", True),
    ("missing input file", lambda b: b.replace(f"{EXAMPLES}/evaluator.json", f"{EXAMPLES}/none.json", 1), "does not exist", True),
    ("out outside work", lambda b: b.replace('--out "$WORK/out"', '--out "out"', 1), "is not below $WORK", True),
    ("replayed caller", lambda b: b.replace("--caller scripted", "--caller fake:x", 1), "--caller is not scripted", True),
    ("no repository root", lambda b: b.replace("repository root", "somewhere", 1), "repository root", False),
    ("no exit statement", lambda b: b.replace("exit code 0", "exit status", 1).replace("Each command exits 0", "Each command exits", 1), "exits 0", False),
    ("no nothing-is-sent", lambda b: re.sub(r"(?i)nothing is sent|sends nothing", "x", b), "nothing is sent", False),
    ("no run-to-run note", lambda b: b.replace("differ from run to run", "x", 1), "differ from run to run", False),
    ("second shell block", lambda b: b + "\n```sh\necho hi\n```\n", "fenced shell blocks", False),
    ("no expected output", lambda b: re.sub(r"```text\n.*?```\n", "", b, count=1, flags=re.S), "expected-output blocks", False),
    ("unknown tracer command", lambda b: b.replace("uv run --locked tracer", "uv run --locked tracerx", 1), "none of the forms", True),
    ("dead link", lambda b: b + "\n[x](../none.md)\n", "link does not resolve", False),
]  # fmt: skip


@pytest.mark.parametrize(("title", "judged"), BOTH)
@pytest.mark.parametrize(
    ("name", "change", "fragment", "shell_only"),
    STRUCTURE_MUTANTS,
    ids=[m[0] for m in STRUCTURE_MUTANTS],
)
def test_the_checker_refuses_structural_mutations(
    title: str,
    judged: bool,
    name: str,
    change: Callable[[str], str],
    fragment: str,
    shell_only: bool,
) -> None:
    problems = static_problems(_mutate(title, change, shell_only=shell_only), title, judged=judged)
    assert any(fragment in p for p in problems), (name, problems)


def test_the_checker_refuses_a_step_outside_the_allowed_forms() -> None:
    def change(block: str) -> str:
        return block.replace('WORK="$(mktemp -d)"\n', 'WORK="$(mktemp -d)"\nrm -rf "$WORK/x"\n', 1)

    problems = static_problems(_mutate(SIM_TITLE, change), SIM_TITLE, judged=False)
    assert any("none of the forms" in p for p in problems), problems


def test_the_checker_refuses_a_judge_option_in_the_simulate_quick_start() -> None:
    def add_judge(block: str) -> str:
        return block.replace("--tts fake", "--tts fake --judge fake:agrees", 1)

    assert static_problems(_mutate(SIM_TITLE, add_judge), SIM_TITLE, judged=False)


def test_the_checker_refuses_a_judge_quick_start_without_a_judge_engine() -> None:
    def drop_judge(block: str) -> str:
        return block.replace("--judge fake:agrees", "", 1)

    assert static_problems(_mutate(JUDGE_TITLE, drop_judge), JUDGE_TITLE, judged=True)


def test_the_checker_refuses_a_judge_quick_start_without_repeats_or_labels() -> None:
    problems = static_problems(
        _mutate(JUDGE_TITLE, lambda b: b.replace("--repeats 2", "--repeats 1", 1)),
        JUDGE_TITLE,
        judged=True,
    )
    assert any("fewer than 2" in p for p in problems), problems
    problems = static_problems(
        _mutate(
            JUDGE_TITLE, lambda b: b.replace("tracer labels agreement", "tracer labels packet", 1)
        ),
        JUDGE_TITLE,
        judged=True,
    )
    assert any("no 'tracer labels agreement' step" in p for p in problems), problems


def test_the_checker_refuses_a_missing_section() -> None:
    problems = static_problems(
        _text().replace(f"## {SIM_TITLE}", "## Other", 1), SIM_TITLE, judged=False
    )
    assert any("has no section" in p for p in problems)


# ------------------------------------------------------------------------------ teeth: the run


def test_the_guard_of_the_runner_refuses_the_network() -> None:
    out = guard_refuses_the_network()
    assert "the network is closed" in out
    assert out.rstrip().endswith("1")


def test_the_runner_reports_a_step_that_exits_non_zero() -> None:
    block = 'WORK="$(mktemp -d)"\nuv run --locked tracer simulate --out "$WORK/out"\n'
    run = run_quick_start(block, timeout=120)
    assert run.codes[0] == 0
    assert run.codes[1] == 64
    assert any("exits 64, not 0" in p for p in run_problems(run))


def test_the_runner_reports_a_change_of_the_repository_tree() -> None:
    block = 'WORK="$(mktemp -d)"\ncp docs/examples/simulated-mr/persona.json docs/examples/simulated-mr/zz-tmp.json\n'
    try:
        run = run_quick_start(block, timeout=120)
    finally:
        (EXAMPLE_DIR / "zz-tmp.json").unlink(missing_ok=True)
    assert any("changed the repository tree" in p for p in run_problems(run))


def test_the_runner_closes_the_network_for_a_step() -> None:
    block = (
        'WORK="$(mktemp -d)"\n'
        "uv run --locked python -c \"import socket; socket.create_connection(('127.0.0.1', 9))\"\n"
    )
    run = run_quick_start(block, timeout=120)
    assert run.codes[1] not in (0, None)
    assert "the network is closed" in run.stderr


def test_the_output_check_reports_a_line_that_is_not_there_or_out_of_order() -> None:
    run = _run(SIM_TITLE)
    expected = _expected(SIM_TITLE)
    assert output_problems(run, expected) == []
    assert output_problems(run, expected + "\nresult: call not established\n")
    assert output_problems(run, expected.replace("call established", "call lost", 1))
    lines = [ln for ln in expected.splitlines() if ln.strip()]
    swapped = "\n".join([lines[1], lines[0], *lines[2:]])
    assert output_problems(run, swapped)
    assert (
        output_problems(run, "") == []
    )  # an empty expectation says nothing; the real test requires lines
    assert len(lines) >= 15
