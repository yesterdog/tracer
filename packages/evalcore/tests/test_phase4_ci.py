# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-01: CI for tracer-evalsim (contract docs/phase4/api-surface-batch-0b.md section 6), in the
style of ``test_phase3_ci.py`` and ``test_phase3_ci_postgres_cli.py``.

These tests read ``.github/workflows/ci.yml``; they start no process. ``phase4_ci_problems`` is the
reviewed shape of the new lower-bound leg; the second half of the file shows it refuses each
weakening (the check has teeth). The first Phase 4 leg is needed, unlike the one for httpx: the
lowest websockets (15.0) is not the newest (SCOPE Decisions D3: each range is tested at its lower
bound).
"""

from __future__ import annotations

import importlib
import re
import shlex
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

JOB = "test-oldest-phase4-deps"
JOB_NAME = "test (lowest-direct phase 4 deps, py3.11)"
TEST_DIR = "packages/evalsim/tests"
DESELECT = (
    "packages/evalsim/tests/test_evalsim_scaffold.py"
    "::test_phase4_locked_closures_are_the_reviewed_ones"
)
PHASE3_JOB = "test-oldest-phase3-deps"
PHASE3_TEST_DIRS = "packages/evalcore/tests packages/evalproviders/tests packages/cli/tests"
USES_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
WHEELS = ("tracer-evalschema", "tracer-evaldb", "tracer-evalcore", "tracer-evalproviders",
          "tracer-cli", "tracer-evalsim")  # fmt: skip


def _code(text: str) -> str:
    """The workflow text without YAML comments (a comment may name a package)."""
    return "\n".join(re.sub(r"(^|\s)#.*", "", line) for line in text.splitlines())


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


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def _uses(job: dict[str, Any]) -> list[str]:
    return [str(step["uses"]) for step in job["steps"] if "uses" in step]


def phase4_ci_problems(workflow: dict[str, Any]) -> list[str]:
    """Why the lower-bound leg of this workflow would not test the Phase 4 ranges."""
    problems: list[str] = []
    job = _job(workflow, JOB)
    if job.get("name") != JOB_NAME:
        problems.append(f"name is {job.get('name')!r}")
    if job.get("runs-on") != "ubuntu-latest":
        problems.append("not ubuntu-latest")
    if job.get("continue-on-error"):
        problems.append("the lower-bound leg is required: it must not continue on error")
    if "services" in job or "container" in job:
        problems.append("a container or service in a leg that needs none")
    if job.get("permissions", {"contents": "read"}) != {"contents": "read"}:
        problems.append("permissions beyond contents: read")
    setup = [s for s in job["steps"] if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    if len(setup) != 1:
        problems.append(f"{len(setup)} setup-uv steps, not 1")
    else:
        with_ = setup[0].get("with", {})
        if with_.get("python-version") != "3.11":
            problems.append("python-version is not 3.11")
        if with_.get("version") != "${{ env.UV_VERSION }}":
            problems.append("uv version is not the workflow's")
    runs = _runs(job)
    if len(runs) != 2:
        problems.append(f"{len(runs)} run steps, not 2")
        return problems
    if runs[0] != "uv lock --resolution lowest-direct":
        problems.append(f"first step is {runs[0]!r}")
    tokens = shlex.split(runs[1])
    head = ["uv", "run", "--locked", "--resolution", "lowest-direct", "--all-groups", "pytest"]
    if tokens[: len(head)] != head:
        problems.append(f"second step does not start with {' '.join(head)}")
    rest = tokens[len(head) :]
    paths = [t for t in rest if not t.startswith("-") and "::" not in t and "/" in t]
    if paths != [TEST_DIR]:
        problems.append(f"test paths are {paths}, not [{TEST_DIR}]")
    deselected = [rest[i + 1] for i, t in enumerate(rest[:-1]) if t == "--deselect"]
    if deselected != [DESELECT]:
        problems.append(f"deselected {deselected}")
    if "--resolution" in rest or "lowest" in rest:
        problems.append("a second resolution option (full 'lowest' fails on a transitive dep)")
    text = "\n".join(runs)
    if re.search(r"\|\|\s*(true|:)|;\s*(true|exit 0)", text):
        problems.append("the pytest step swallows its exit code")
    for forbidden in ("docker", "POSTGRES", "SARVAM", "secrets.", "-m postgres", "-m integration"):
        if forbidden in text:
            problems.append(f"{forbidden} in a leg that needs none")
    for use in _uses(job):
        if not USES_RE.match(use):
            problems.append(f"{use} is not pinned by commit SHA")
    return problems


# ------------------------------------------------------------------------ the real workflow


def test_ci_phase4_wheel_is_built_and_checked() -> None:
    job = _job(_workflow(), "licenses")
    text = "\n".join(_runs(job))
    builds = [text.find(f"uv build --package {dist} --out-dir dist") for dist in WHEELS]
    assert all(i >= 0 for i in builds), builds
    assert builds == sorted(builds), "the wheels are built in the order of the workspace layers"
    check = text.find("scripts/check_licenses.py --wheel dist --artifacts-only")
    assert check > max(builds), "the artifact check runs after every wheel is built"
    assert "uv run --locked --all-groups python scripts/check_licenses.py\n" in text + "\n"
    assert text.count("uv build --package tracer-evalsim") == 1
    # the sample bots are not built, installed or locked by the main jobs
    for name, other in _workflow()["jobs"].items():
        run_text = "\n".join(_runs(other))
        assert "samplebot" not in run_text, name
        assert "pipecat" not in run_text.lower(), name


def test_ci_lower_bound_leg_for_phase4_ranges() -> None:
    workflow = _workflow()
    assert phase4_ci_problems(workflow) == []
    # an existing lower-bound leg is untouched in name and shape
    assert _job(workflow, "test-oldest-evaldb-deps")["name"] == (
        "test (lowest-direct evaldb deps, py3.11, pg15)"
    )
    phase3 = _job(workflow, PHASE3_JOB)
    assert phase3["name"] == "test (lowest-direct phase 3 deps, py3.11)"
    runs = _runs(phase3)
    assert len(runs) == 2
    assert runs[0] == "uv lock --resolution lowest-direct"
    assert PHASE3_TEST_DIRS in runs[1]
    # the Phase 3 leg runs the evalcore directory, which now holds the Phase 4 licence test: it
    # compares installed versions with the committed lock, so it is deselected there as well
    assert "--deselect packages/evalcore/tests/test_phase3_licenses.py" in runs[1]
    assert "--deselect packages/evalcore/tests/test_phase4_licenses.py" in runs[1]
    assert (
        "--deselect packages/evalcore/tests/"
        "test_phase3_scaffold.py::test_phase3_locked_closures_are_the_reviewed_ones"
    ) in runs[1]
    # the extra is installed by --all-groups through the dev group, so the leg lowers aiortc too
    assert "--all-groups" in runs[1]


def test_ci_phase4_legs_and_policy_are_unchanged_where_they_must_be() -> None:
    workflow = _workflow()
    for name in ("test", "test-py315"):
        job = _job(workflow, name)
        pytest_steps = [r for r in _runs(job) if "pytest" in r]
        assert pytest_steps == ["uv run --locked --all-groups pytest"], name
    assert _job(workflow, "test")["strategy"]["matrix"]["python-version"] == [
        "3.11",
        "3.12",
        "3.13",
        "3.14",
    ]
    assert _job(workflow, "test-py315")["continue-on-error"] is True
    # lint covers the new package through the root configuration
    lint = "\n".join(_runs(_job(workflow, "lint")))
    for line in (
        "uv run --locked ruff check",
        "uv run --locked ruff format --check",
        "uv run --locked mypy",
        "uv run --locked python scripts/check_headers.py",
    ):
        assert line in lint, line
    # every action stays pinned by commit; no key or secret reaches the workflow
    every = [u for job in workflow["jobs"].values() for u in _uses(job)]
    assert every
    for use in every:
        assert USES_RE.match(use), f"{use} is not pinned by commit SHA"
    code = _code(WORKFLOW.read_text(encoding="utf-8"))
    for forbidden in ("secrets.", "SARVAM", "api-subscription-key"):
        assert forbidden not in code, forbidden
    assert workflow["permissions"] == {"contents": "read"}
    # nothing pins a websockets or aiortc version by hand: the ranges live in pyproject
    assert not re.search(r"(websockets|aiortc)\s*[=<>~]", code)
    # the two Postgres jobs still run the evaldb and the cli tests, and not the new package
    for name in ("test-postgres", "test-postgres-py315"):
        text = "\n".join(_runs(_job(workflow, name)))
        assert "pytest -m postgres packages/evaldb/tests packages/cli/tests" in text, name
        assert "evalsim" not in text, name


# ------------------------------------------------------------------------ the check has teeth

_GOOD = """
jobs:
  test-oldest-phase4-deps:
    name: test (lowest-direct phase 4 deps, py3.11)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e
        with:
          version: ${{ env.UV_VERSION }}
          python-version: "3.11"
      - run: uv lock --resolution lowest-direct
      - run: @CMD@
"""
COMMAND = (
    "uv run --locked --resolution lowest-direct --all-groups pytest packages/evalsim/tests "
    f"--deselect {DESELECT}"
)
_GOOD = _GOOD.replace("@CMD@", COMMAND)


def _variant(old: str, new: str) -> dict[str, Any]:
    assert old in _GOOD, old
    return _workflow(_GOOD.replace(old, new, 1))


def test_ci_check_accepts_the_reviewed_shape() -> None:
    assert phase4_ci_problems(_workflow(_GOOD)) == []


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        ("uv lock --resolution lowest-direct", "uv lock", "first step"),
        ("uv lock --resolution lowest-direct", "uv lock --resolution lowest", "first step"),
        ("--locked --resolution lowest-direct", "--resolution lowest-direct", "second step"),
        ("--resolution lowest-direct --all-groups", "--all-groups", "second step"),
        ("--resolution lowest-direct --all-groups", "--resolution lowest --all-groups", "second"),
        ("--all-groups pytest", "--all-groups --resolution lowest pytest", "second step"),
        ("pytest packages/evalsim/tests", "pytest packages/evalcore/tests", "test paths"),
        ("pytest packages/evalsim/tests", "pytest", "test paths"),
        ("pytest packages/evalsim/tests", "pytest packages/evalsim/tests packages/cli/tests",
         "test paths"),
        (f" --deselect {DESELECT}", "", "deselected"),
        (f" --deselect {DESELECT}", f" --deselect {DESELECT} --deselect packages/x::y",
         "deselected"),
        ('python-version: "3.11"', 'python-version: "3.12"', "python-version"),
        ("version: ${{ env.UV_VERSION }}", 'version: "latest"', "uv version"),
        ("runs-on: ubuntu-latest", "runs-on: macos-latest", "ubuntu"),
        ("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    continue-on-error: true",
         "continue on error"),
        ("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    services: {pg: {image: x}}",
         "service"),
        ("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    permissions: {contents: write}",
         "permissions"),
        ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v4",
         "not pinned"),
        ("name: test (lowest-direct phase 4 deps, py3.11)", "name: test (phase 4)", "name is"),
        (COMMAND, COMMAND + " || true", "swallows"),
        (COMMAND, COMMAND + " -m integration", "-m integration"),
        (COMMAND, "docker run x && " + COMMAND, "docker"),
    ],
)  # fmt: skip
def test_ci_check_refuses_each_weakening(old: str, new: str, fragment: str) -> None:
    problems = phase4_ci_problems(_variant(old, new))
    assert any(fragment in p for p in problems), (fragment, problems)


def test_ci_check_refuses_a_missing_job_and_a_missing_step() -> None:
    with pytest.raises(AssertionError, match=JOB):
        phase4_ci_problems(_workflow(_GOOD.replace(JOB, "test-other")))
    one_step = _GOOD.replace("      - run: uv lock --resolution lowest-direct\n", "")
    assert any("run steps" in p for p in phase4_ci_problems(_workflow(one_step)))
