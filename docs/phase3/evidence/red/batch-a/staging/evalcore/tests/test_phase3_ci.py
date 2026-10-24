# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-01: CI for the three Phase 3 packages (contract docs/phase3/api-surface-batch-a.md
section 7).

These tests read ``.github/workflows/ci.yml``; they start no process. The lower-bound statement
follows the licence review (docs/security/P3-02-licenses.md section 8): the only third-party
range of Phase 3 is ``httpx>=0.28.1,<1``, its lowest-direct resolution is the highest one
(0.28.1 is the newest stable release), so a job named for httpx would add no coverage and none
exists; the Phase 3 test directories run on a lowest-direct leg of their own, which lowers every
direct dependency of the workspace together.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
yaml: Any = importlib.import_module("yaml")  # pyyaml, test group; untyped, so loaded dynamically

JOB = "test-oldest-phase3-deps"
JOB_NAME = "test (lowest-direct phase 3 deps, py3.11)"
TEST_DIRS = "packages/evalcore/tests packages/evalproviders/tests packages/cli/tests"
PHASE3_DISTS = ("tracer-evalcore", "tracer-evalproviders", "tracer-cli")
USES_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")


def _code(text: str) -> str:
    """The workflow text without YAML comments (a comment may name httpx or a secret)."""
    return "\n".join(re.sub(r"(^|\s)#.*", "", line) for line in text.splitlines())


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _job(name: str) -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert name in jobs, f"job {name!r} is missing from ci.yml (jobs: {sorted(jobs)})"
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step["run"]) for step in job["steps"] if "run" in step)


def _uses(job: dict[str, Any]) -> list[str]:
    return [str(step["uses"]) for step in job["steps"] if "uses" in step]


def test_ci_phase3_wheels_are_built_and_checked() -> None:
    text = _run_text(_job("licenses"))
    builds = [
        text.find(f"uv build --package {dist}")
        for dist in ("tracer-evalschema", "tracer-evaldb", *PHASE3_DISTS)
    ]
    assert all(i >= 0 for i in builds), builds
    check = text.find("scripts/check_licenses.py --wheel dist --artifacts-only")
    assert check > max(builds), "the artifact check runs after every wheel is built"
    # the environment scan of the licence job runs with every group (the new packages are in dev)
    assert "uv run --locked --all-groups python scripts/check_licenses.py\n" in text + "\n"


def test_ci_phase3_test_legs_run_the_new_directories() -> None:
    # the existing legs run the bare command, so the testpaths of the root pyproject decide
    for name in ("test", "test-py315"):
        job = _job(name)
        steps = [str(s["run"]) for s in job["steps"] if "run" in s and "pytest" in str(s["run"])]
        assert steps == ["uv run --locked --all-groups pytest"], name
    assert _job("test")["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13", "3.14"]
    assert not _job("test").get("continue-on-error")
    assert _job("test-py315")["continue-on-error"] is True
    # lint covers the new paths through the root configuration (ruff src, mypy files)
    lint = _run_text(_job("lint"))
    assert "uv run --locked ruff check" in lint
    assert "uv run --locked ruff format --check" in lint
    assert "uv run --locked mypy" in lint
    assert "uv run --locked python scripts/check_headers.py" in lint


def test_ci_lower_bound_leg_for_phase3_ranges() -> None:
    """Gate item 1 and SCOPE Decisions D3: the Phase 3 ranges are tested at their lower bound."""
    job = _job(JOB)
    assert job["name"] == JOB_NAME
    assert job["runs-on"] == "ubuntu-latest"
    assert not job.get("continue-on-error"), "the lower-bound leg is required"
    assert "services" not in job
    assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
    uv_steps = [s for s in job["steps"] if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert len(uv_steps) == 1
    assert uv_steps[0]["with"]["python-version"] == "3.11"
    assert uv_steps[0]["with"]["version"] == "${{ env.UV_VERSION }}"
    runs = [str(s["run"]) for s in job["steps"] if "run" in s]
    # two steps, as the evaldb leg: the throwaway checkout is re-resolved, then every uv call
    # stays --locked
    assert runs[0] == "uv lock --resolution lowest-direct"
    assert len(runs) == 2
    test = runs[1]
    assert test.startswith("uv run --locked --resolution lowest-direct --all-groups pytest ")
    assert TEST_DIRS in test
    assert "--resolution lowest " not in test + " ", "full 'lowest' fails on an old transitive dep"
    # the tests that compare the installed versions or the closure with the committed lock cannot
    # hold after the deliberate downgrade (they run in every other leg)
    assert "--deselect packages/evalcore/tests/test_phase3_licenses.py" in test
    assert (
        "--deselect packages/evalcore/tests/"
        "test_phase3_scaffold.py::test_phase3_locked_closures_are_the_reviewed_ones"
    ) in test
    # no database server, no container, no key
    text = _run_text(job)
    for forbidden in ("docker", "POSTGRES", "TRACER_TEST_POSTGRES_URL", "SARVAM", "secrets."):
        assert forbidden not in text, forbidden
    # an existing lower-bound leg is untouched
    old = _job("test-oldest-evaldb-deps")
    assert old["name"] == "test (lowest-direct evaldb deps, py3.11, pg15)"


def test_ci_has_no_httpx_specific_job() -> None:
    """The lowest-direct resolution of httpx>=0.28.1,<1 is 0.28.1, the highest one too: a job
    for httpx would be a tautology, so none exists (P3-02 section 8). The range is covered by the
    lower-bound leg of the Phase 3 directories above."""
    workflow = _workflow()
    for key, job in workflow["jobs"].items():
        assert "httpx" not in key.lower(), key
        assert "httpx" not in str(job.get("name", "")).lower(), key
        for step in job["steps"]:
            assert "httpx" not in str(step.get("name", "")).lower(), (key, step)
            assert "httpx" not in str(step.get("run", "")).lower(), (key, step)
            assert "httpx" not in str(step.get("with", "")).lower(), (key, step)
    # and nothing pins an httpx version by hand
    assert not re.search(r"httpx\s*[=<>~]", _code(WORKFLOW.read_text(encoding="utf-8")))


def test_ci_phase3_actions_pinned_and_no_new_image() -> None:
    workflow = _workflow()
    every = [u for job in workflow["jobs"].values() for u in _uses(job)]
    assert every
    for use in every:
        assert USES_RE.match(use), f"{use} is not pinned by commit SHA"
    # the new leg adds no action that the main test leg does not already use
    assert set(_uses(_job(JOB))) <= set(_uses(_job("test")))
    # a Phase 3 leg needs no container: every image in the file is a pinned Postgres image
    text = _code(WORKFLOW.read_text(encoding="utf-8"))
    for ref in re.findall(r"\bdocker run\b[^\n]*", text):
        assert "postgres" in ref or "${{ matrix.image }}" in ref, ref
    for ref in re.findall(r"\bpostgres:[0-9][^\s\"']*", text):
        assert "@sha256:" in ref or ref.startswith("postgres:${{"), ref
    assert workflow["permissions"] == {"contents": "read"}
    # no key reaches the workflow: the unit tests of Phase 3 replay recorded responses offline
    assert "secrets." not in text
    assert "SARVAM" not in text
    assert "api-subscription-key" not in text
