# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Gate condition C3 (decision log "PHASE 3 GATE", item 8): the Postgres parity test of the cli
store (``test_pipeline_bundle_stores_on_both_backends[postgres]``, P3-21) runs in the hosted
Postgres jobs. Both ``-m postgres`` invocations of ``.github/workflows/ci.yml`` (one per Postgres
version: pg15 and pg18 in ``test-postgres``, pg18 again in the non-blocking ``test-postgres-py315``)
name ``packages/cli/tests`` after ``packages/evaldb/tests``. Contract:
docs/phase3/api-surface-batch-j.md section 2.

These tests read ``ci.yml`` and the root ``pyproject.toml``, and run one ``pytest --collect-only``
in a subprocess (it opens no socket and needs no database). They are in the style of
``test_ci_*`` of ``test_phase3_ci.py`` and ``test_evaldb_ci.py``.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
yaml: Any = importlib.import_module("yaml")  # pyyaml, test group; untyped, so loaded dynamically

POSTGRES_JOBS = ("test-postgres", "test-postgres-py315")
EVALDB = "packages/evaldb/tests"
CLI_DIR = "packages/cli/tests"
CLI_FILE = "packages/cli/tests/test_recorded_store.py"
PARITY_ID = "test_pipeline_bundle_stores_on_both_backends[postgres]"
MARKER_LINE = (
    "postgres: needs a Postgres server (TRACER_TEST_POSTGRES_URL); deselected by default, "
    "selected in the Postgres CI job with -m postgres"
)
ADDOPTS = (
    "-ra --strict-markers --strict-config "
    "-m 'not private_fixtures and not local_only and not postgres'"
)


def _workflow(text: str | None = None) -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8") if text is None else text)
    assert isinstance(data, dict)
    return data


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert name in jobs, f"job {name!r} is missing from ci.yml (jobs: {sorted(jobs)})"
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def postgres_invocations(job: dict[str, Any]) -> list[tuple[str, list[str], list[str]]]:
    """Each ``pytest`` command line of the job's steps as (marker, paths, other options).

    A line counts when it holds the word ``pytest``; the tokens before it (``uv run ...``) are
    skipped, ``-m`` takes the marker expression, a token that does not start with ``-`` is a path
    and every other option is returned in the third list.
    """
    found: list[tuple[str, list[str], list[str]]] = []
    for step in job["steps"]:
        for line in str(step.get("run", "")).splitlines():
            if "pytest" not in line:
                continue
            tokens = shlex.split(line, comments=True)
            if "pytest" not in tokens:
                continue
            rest = tokens[tokens.index("pytest") + 1 :]
            marker = ""
            paths: list[str] = []
            others: list[str] = []
            i = 0
            while i < len(rest):
                if rest[i] == "-m" and i + 1 < len(rest):
                    marker = rest[i + 1]
                    i += 2
                    continue
                (others if rest[i].startswith("-") else paths).append(rest[i])
                i += 1
            found.append((marker, paths, others))
    return found


def ci_problems(workflow: dict[str, Any]) -> list[str]:
    """Why the Postgres jobs of this workflow would not run the cli store parity test."""
    problems: list[str] = []
    for name in POSTGRES_JOBS:
        job = _job(workflow, name)
        runs = [r for r in postgres_invocations(job) if r[0] == "postgres"]
        if len(runs) != 1:
            problems.append(f"{name}: {len(runs)} pytest invocations with -m postgres, not 1")
            continue
        _, paths, others = runs[0]
        if paths[:1] != [EVALDB]:
            problems.append(f"{name}: the first path is not {EVALDB}: {paths}")
        if CLI_DIR not in paths and CLI_FILE not in paths:
            problems.append(f"{name}: neither {CLI_DIR} nor {CLI_FILE} is a path of -m postgres")
        extra = [p for p in paths if p not in (EVALDB, CLI_DIR, CLI_FILE)]
        if extra:
            problems.append(f"{name}: unknown paths {extra}")
        if CLI_DIR in paths and CLI_FILE in paths:
            problems.append(f"{name}: the cli tests are named twice")
        if others:
            problems.append(f"{name}: options besides -m: {others}")
        for step in job["steps"]:
            run = str(step.get("run", ""))
            if "pytest" in run and re.search(r"\|\|\s*(true|:)|;\s*(true|exit 0)", run):
                problems.append(f"{name}: the pytest step swallows its exit code")
    if _job(workflow, "test-postgres").get("continue-on-error"):
        problems.append("test-postgres is not allowed to continue on error")
    return problems


# ------------------------------------------------------------------------ the real workflow


def test_ci_postgres_legs_run_the_cli_store_parity_test() -> None:
    workflow = _workflow()
    assert ci_problems(workflow) == []
    # both Postgres versions are covered: pg15 and pg18 of the blocking job, pg18 of the other
    include = _job(workflow, "test-postgres")["strategy"]["matrix"]["include"]
    assert {str(c["postgres"]) for c in include} == {"15", "18"}
    assert {
        str(c["postgres"])
        for c in _job(workflow, "test-postgres-py315")["strategy"]["matrix"]["include"]
    } == {"18"}
    for name in POSTGRES_JOBS:
        (invocation,) = [
            r for r in postgres_invocations(_job(workflow, name)) if r[0] == "postgres"
        ]
        assert invocation[1][0] == EVALDB
        assert CLI_DIR in invocation[1] or CLI_FILE in invocation[1]


def test_ci_postgres_command_lines_are_the_reviewed_ones() -> None:
    """The text (contract section 2.1): the evaldb path stays first, as the evaldb tests
    ``test_ci_postgres_job_matrix_and_pinned_image`` (a regex and a count) require, and the cli
    tests follow as the directory or as the one explicit file."""
    text = WORKFLOW.read_text(encoding="utf-8")
    prefix = "- run: uv run --locked --all-groups pytest -m postgres packages/evaldb/tests"
    lines = [ln.strip() for ln in text.splitlines() if "pytest -m postgres" in ln]
    assert len(lines) == 2, lines
    for line in lines:
        assert line in (f"{prefix} {CLI_DIR}", f"{prefix} {CLI_FILE}"), line
    assert text.count("pytest -m postgres") == 2


def test_ci_postgres_url_variable_is_the_one_the_cli_fixture_reads() -> None:
    source = (ROOT / "packages" / "cli" / "tests" / "cli_batchd_support.py").read_text("utf-8")
    tree = ast.parse(source)
    values = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "POSTGRES_URL_ENV" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert values == ["TRACER_TEST_POSTGRES_URL"]
    workflow = _workflow()
    for name in POSTGRES_JOBS:
        run = "\n".join(str(s.get("run", "")) for s in _job(workflow, name)["steps"])
        assert "TRACER_TEST_POSTGRES_URL=" in run, name
        assert run.index("TRACER_TEST_POSTGRES_URL=") < run.index("pytest -m postgres"), name


def test_ci_postgres_marker_configuration_is_unchanged() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ini = pyproject["tool"]["pytest"]["ini_options"]
    assert ini["addopts"] == ADDOPTS
    assert MARKER_LINE in ini["markers"]
    assert sum(1 for m in ini["markers"] if str(m).startswith("postgres:")) == 1
    # the marker is registered strictly, so a typo in a mark cannot pass silently
    assert "--strict-markers" in ini["addopts"]
    assert "postgres" in re.search(r"-m\s+'([^']*)'", ini["addopts"]).group(1)  # type: ignore[union-attr]


def test_cli_store_parity_test_is_marked_postgres_in_the_source() -> None:
    path = ROOT / "packages" / "cli" / "tests" / "test_recorded_store.py"
    tree = ast.parse(path.read_text("utf-8"))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    parity = functions["test_pipeline_bundle_stores_on_both_backends"]
    assert [a.arg for a in parity.args.args] == ["backend", "tmp_path"], "the test uses the fixture"
    fixture = functions["backend"]
    # the fixture has a "postgres" parameter whose marks is pytest.mark.postgres
    params = [
        call
        for dec in fixture.decorator_list
        if isinstance(dec, ast.Call)
        for call in ast.walk(dec)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "param"
    ]
    marked = {}
    for call in params:
        first = call.args[0]
        assert isinstance(first, ast.Constant)
        marks = [kw.value for kw in call.keywords if kw.arg == "marks"]
        marked[first.value] = [ast.unparse(m) for m in marks]
    assert marked == {"sqlite": [], "postgres": ["pytest.mark.postgres"]}


def test_cli_store_parity_test_is_selected_by_the_postgres_marker() -> None:
    env = {k: v for k, v in os.environ.items() if k != "TRACER_TEST_POSTGRES_URL"}
    for target in (CLI_DIR, CLI_FILE):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "postgres",
             "-p", "no:cacheprovider", target],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )  # fmt: skip
        selected = [ln for ln in result.stdout.splitlines() if "::" in ln]
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        assert any(ln.endswith(f"test_recorded_store.py::{PARITY_ID}") for ln in selected), selected
        assert not any("[sqlite]" in ln for ln in selected), selected


# ------------------------------------------------------------------------ the check has teeth

_GOOD = """
jobs:
  test-postgres:
    strategy: {matrix: {include: [{postgres: "15"}, {postgres: "18"}]}}
    steps:
      - run: uv sync --locked --all-groups
      - run: @CMD@
  test-postgres-py315:
    continue-on-error: true
    steps:
      - run: @CMD@
"""


COMMAND = "uv run --locked --all-groups pytest -m postgres packages/evaldb/tests packages/cli/tests"
_GOOD = _GOOD.replace("@CMD@", COMMAND)


def _variant(old: str, new: str, count: int = 1) -> dict[str, Any]:
    assert old in _GOOD
    return _workflow(_GOOD.replace(old, new, count))


def test_ci_check_accepts_the_reviewed_shape_and_the_explicit_file() -> None:
    assert ci_problems(_workflow(_GOOD)) == []
    explicit = _GOOD.replace("packages/cli/tests", CLI_FILE)
    assert ci_problems(_workflow(explicit)) == []


def test_ci_check_refuses_the_state_of_the_gate_finding() -> None:
    today = _GOOD.replace(" packages/cli/tests", "")
    problems = ci_problems(_workflow(today))
    assert len(problems) == 2, problems
    assert all("neither packages/cli/tests" in p for p in problems), problems


@pytest.mark.parametrize("job", POSTGRES_JOBS)
def test_ci_check_refuses_a_single_leg_without_the_cli_path(job: str) -> None:
    lines = _GOOD.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"  {job}:"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("  test-")), len(lines)
    )
    bad = "".join(
        ln.replace(" packages/cli/tests", "") if start <= i < end else ln
        for i, ln in enumerate(lines)
    )
    problems = ci_problems(_workflow(bad))
    assert [p.split(":")[0] for p in problems] == [job], problems


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/cli/tests packages/evaldb/tests", "the first path"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         'pytest -m "not postgres" packages/evaldb/tests packages/cli/tests', "0 pytest"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest packages/evaldb/tests packages/cli/tests", "0 pytest"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/evaldb/tests packages/cli/tests packages/evalcore/tests",
         "unknown paths"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/evaldb/tests packages/cli/tests || true", "swallows"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/evaldb/tests packages/cli/tests --deselect x", "options"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/evaldb/tests packages/cli/tests " + CLI_FILE, "named twice"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/evaldb/tests", "neither"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "pytest -m postgres packages/evaldb/tests # packages/cli/tests", "neither"),
        ("pytest -m postgres packages/evaldb/tests packages/cli/tests",
         "echo packages/cli/tests; pytest -m postgres packages/evaldb/tests", "neither"),
    ],
)  # fmt: skip
def test_ci_check_refuses_each_weakening(old: str, new: str, fragment: str) -> None:
    problems = ci_problems(_variant(old, new))
    assert any(fragment in p for p in problems), (fragment, problems)


def test_ci_check_refuses_a_blocking_job_that_continues_on_error() -> None:
    bad = _GOOD.replace("  test-postgres:\n", "  test-postgres:\n    continue-on-error: true\n", 1)
    assert any("continue on error" in p for p in ci_problems(_workflow(bad)))


def test_ci_check_refuses_a_missing_job() -> None:
    bad = _GOOD.replace("test-postgres-py315", "test-postgres-other")
    with pytest.raises(AssertionError, match="test-postgres-py315"):
        ci_problems(_workflow(bad))
