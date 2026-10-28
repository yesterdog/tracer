# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-27: the separate CI job of the interop tests (ADR 0026 rule 23; contract
docs/phase4/api-surface-batch-f.md section 3), in the style of ``test_phase4_ci.py``.

The tests that run against ``tools/samplebot`` (real Pipecat 1.14.0, loopback only, no key) are not
part of the normal test legs: they need the tool's own environment (its own lock) and a second
process. They carry the marker ``integration``, which the evalsim ``conftest.py`` deselects unless
the run selects it with ``-m integration``. This file reads ``.github/workflows/ci.yml`` (no
process is started) and shows that the job is the only place that selects the marker, that it syncs
both environments from their locks, that it holds no secret, and that every other job still runs
the bare command and never mentions the tool. ``interop_problems`` is the reviewed shape; the second
half of the file shows it refuses each weakening (the check has teeth).
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

JOB = "interop"
JOB_NAME = "interop (scripted_bot on loopback, py3.13)"
SYNC_ROOT = "uv sync --locked --all-groups"
SYNC_TOOL = "uv sync --locked --project tools/samplebot"
RUN_TESTS = "uv run --locked --all-groups pytest -m integration packages/evalsim/tests"
USES_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")


def _code(text: str) -> str:
    return "\n".join(re.sub(r"(^|\s)#.*", "", line) for line in text.splitlines())


def _workflow(text: str | None = None) -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8") if text is None else text)
    assert isinstance(data, dict)
    return data


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def _uses(job: dict[str, Any]) -> list[str]:
    return [str(step["uses"]) for step in job["steps"] if "uses" in step]


def interop_problems(workflow: dict[str, Any]) -> list[str]:
    """Why the interop job of this workflow would not run the interop tests as designed."""
    problems: list[str] = []
    jobs = workflow["jobs"]
    if JOB not in jobs:
        return [f"no job {JOB}"]
    job = jobs[JOB]
    if job.get("name") != JOB_NAME:
        problems.append(f"name is {job.get('name')!r}")
    if job.get("runs-on") != "ubuntu-latest":
        problems.append("not ubuntu-latest")
    if job.get("continue-on-error"):
        problems.append("the interop job is required: it must not continue on error")
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or not 10 <= timeout <= 45:
        problems.append(
            "timeout-minutes is not an integer from 10 to 45 (a hung bot holds the job)"
        )
    for forbidden in ("services", "container", "strategy", "env", "environment"):
        if forbidden in job:
            problems.append(f"{forbidden} in a job that needs none")
    if job.get("permissions", {"contents": "read"}) != {"contents": "read"}:
        problems.append("permissions beyond contents: read")
    setup = [s for s in job["steps"] if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    if len(setup) != 1:
        problems.append(f"{len(setup)} setup-uv steps, not 1")
    else:
        with_ = setup[0].get("with", {})
        if with_.get("python-version") != "3.13":
            problems.append("python-version is not 3.13")
        if with_.get("version") != "${{ env.UV_VERSION }}":
            problems.append("uv version is not the workflow's")
    checkout = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    if len(checkout) != 1 or checkout[0].get("with", {}).get("persist-credentials") is not False:
        problems.append("checkout must not persist credentials")
    runs = _runs(job)
    if runs != [SYNC_ROOT, SYNC_TOOL, RUN_TESTS]:
        problems.append(f"run steps are {runs}")
        return problems
    tokens = shlex.split(RUN_TESTS)
    if "--deselect" in tokens or "-k" in tokens or "-x" in tokens:
        problems.append("a filter in the interop run")
    text = "\n".join(runs)
    if re.search(r"\|\|\s*(true|:)|;\s*(true|exit 0)", text):
        problems.append("a step swallows its exit code")
    for forbidden in ("docker", "SARVAM", "secrets.", "TWILIO", "api-subscription-key", "sudo"):
        if forbidden in text or forbidden in str(job):
            problems.append(f"{forbidden} in a job that needs none")
    for use in _uses(job):
        if not USES_RE.match(use):
            problems.append(f"{use} is not pinned by commit SHA")
    return problems


# ------------------------------------------------------------------------ the real workflow


def test_ci_has_the_interop_job_of_the_reviewed_shape() -> None:
    assert interop_problems(_workflow()) == []


def test_only_the_interop_job_selects_the_integration_marker_or_names_the_tool() -> None:
    """Every other job keeps the bare command (the marker is deselected by the conftest) and never
    mentions Pipecat or the tool project (the older assertion of ``test_phase4_ci.py`` is narrowed
    to the other jobs by the named spec change S-F3)."""
    workflow = _workflow()
    for name, job in workflow["jobs"].items():
        if name == JOB:
            continue
        run_text = "\n".join(_runs(job))
        assert "samplebot" not in run_text, name
        assert "pipecat" not in run_text.lower(), name
        assert "integration" not in run_text, name
    for name in ("test", "test-py315"):
        pytest_steps = [r for r in _runs(workflow["jobs"][name]) if "pytest" in r]
        assert pytest_steps == ["uv run --locked --all-groups pytest"], name
    code = _code(WORKFLOW.read_text(encoding="utf-8"))
    for forbidden in ("secrets.", "SARVAM", "TWILIO"):
        assert forbidden not in code, forbidden
    assert workflow["permissions"] == {"contents": "read"}


def test_the_interop_job_does_not_build_or_publish_the_tool() -> None:
    """The tool is never built into a wheel, never uploaded and never installed in the root
    environment (``test_samplebot_and_pipecat_are_not_installed_in_the_tracer_environment``)."""
    job = _workflow()["jobs"][JOB]
    text = "\n".join(_runs(job))
    for forbidden in ("uv build", "uv publish", "twine", "pip install", "upload-artifact"):
        assert forbidden not in text
    assert "--project tools/samplebot" in text
    assert not any("upload-artifact" in u for u in _uses(job))


# ------------------------------------------------------------------------ the check has teeth

_GOOD = """
jobs:
  interop:
    name: interop (scripted_bot on loopback, py3.13)
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e
        with:
          version: ${{ env.UV_VERSION }}
          python-version: "3.13"
      - run: uv sync --locked --all-groups
      - run: uv sync --locked --project tools/samplebot
      - run: uv run --locked --all-groups pytest -m integration packages/evalsim/tests
"""


def _variant(old: str, new: str) -> dict[str, Any]:
    assert old in _GOOD, old
    return _workflow(_GOOD.replace(old, new, 1))


def test_the_check_accepts_the_reviewed_shape() -> None:
    assert interop_problems(_workflow(_GOOD)) == []


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        ("interop (scripted_bot on loopback, py3.13)", "interop", "name is"),
        ("runs-on: ubuntu-latest", "runs-on: macos-latest", "ubuntu"),
        ("    timeout-minutes: 30\n", "", "timeout-minutes"),
        ("timeout-minutes: 30", "timeout-minutes: 360", "timeout-minutes"),
        (
            "    timeout-minutes: 30\n",
            "    timeout-minutes: 30\n    continue-on-error: true\n",
            "continue on error",
        ),
        (
            "    timeout-minutes: 30\n",
            "    timeout-minutes: 30\n    services: {pg: {image: x}}\n",
            "services",
        ),
        (
            "    timeout-minutes: 30\n",
            "    timeout-minutes: 30\n    env: {SARVAM_API_KEY: x}\n",
            "env",
        ),
        ("persist-credentials: false", "persist-credentials: true", "credentials"),
        ('python-version: "3.13"', 'python-version: "3.11"', "python-version"),
        (
            "uv sync --locked --project tools/samplebot",
            "uv sync --project tools/samplebot",
            "run steps",
        ),
        (
            "uv sync --locked --project tools/samplebot",
            "uv pip install pipecat-ai==1.14.0",
            "run steps",
        ),
        (
            "pytest -m integration packages/evalsim/tests",
            "pytest packages/evalsim/tests",
            "run steps",
        ),
        ("pytest -m integration packages/evalsim/tests", "pytest -m integration", "run steps"),
        (
            "pytest -m integration packages/evalsim/tests",
            "pytest -m integration packages/evalsim/tests || true",
            "run steps",
        ),
        (
            "pytest -m integration packages/evalsim/tests",
            "pytest -m 'integration or postgres' packages/evalsim/tests",
            "run steps",
        ),
        (
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v4",
            "pinned",
        ),
    ],
)
def test_the_check_refuses_each_weakening(old: str, new: str, fragment: str) -> None:
    problems = interop_problems(_variant(old, new))
    assert problems, (old, new)
    assert any(fragment in p for p in problems), (fragment, problems)
