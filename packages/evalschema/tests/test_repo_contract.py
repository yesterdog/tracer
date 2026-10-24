# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-01 / P1-02 contract tests: license files, CI workflow, lock file, dependency policy, guard.

These read repository files and run the repository's own scripts; they do not import the package
under test. Where a checker is a regex or a walk, it is also run on a deliberately bad sample so a
vacuous pass is impossible.
"""

from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from ._support import PACKAGE_DIR, ROOT

yaml: Any = importlib.import_module("yaml")  # pyyaml, test group; untyped, so loaded dynamically

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
POLICY = ROOT / "docs" / "security" / "license-policy.toml"
REQUIRED_PYTHONS = ["3.11", "3.12", "3.13", "3.14"]
SHA_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
GIT = shutil.which("git") or "/usr/bin/git"
GUARD_SCRIPT = ROOT / "scripts" / "guard_private_fixtures.py"
PRIVATE_PREFIX = "packages/evalschema/tests/fixtures/private/"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _all_uses(workflow: dict[str, Any]) -> list[str]:
    uses: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                uses.append(str(step["uses"]))
    return uses


def unpinned_uses(uses: list[str]) -> list[str]:
    """Remote actions not pinned to a full 40-hex commit SHA (local ``./`` actions are fine)."""
    return [u for u in uses if not u.startswith("./") and not SHA_RE.match(u)]


def _lock_packages() -> dict[str, dict[str, Any]]:
    lock = _toml(ROOT / "uv.lock")
    return {_norm(p["name"]): p for p in lock["package"]}


def _runtime_closure() -> set[str]:
    packages = _lock_packages()
    seen: set[str] = set()
    todo = ["tracer-evalschema"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for dep in packages[name].get("dependencies", []):
            todo.append(_norm(dep["name"]))
    seen.discard("tracer-evalschema")
    return seen


def _jobs_by_python(workflow: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required, nonblocking = [], []
    for job in workflow["jobs"].values():
        matrix = job.get("strategy", {}).get("matrix", {})
        if "python-version" in matrix:
            required.append(job)
        for step in job.get("steps", []):
            if str(step.get("with", {}).get("python-version")) == "3.15":
                nonblocking.append(job)
    return required, nonblocking


# ----------------------------------------------------------------------- license and notice


def test_license_and_notice_present() -> None:
    for base in (ROOT, PACKAGE_DIR):
        text = (base / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in text
        assert "Version 2.0, January 2004" in text
        notice = (base / "NOTICE").read_text(encoding="utf-8")
        assert notice.strip(), f"{base}/NOTICE is empty"
        assert "Tracer" in notice
    assert (ROOT / "LICENSE").read_bytes() == (PACKAGE_DIR / "LICENSE").read_bytes()
    assert (ROOT / "NOTICE").read_bytes() == (PACKAGE_DIR / "NOTICE").read_bytes()
    project = _toml(PACKAGE_DIR / "pyproject.toml")["project"]
    assert project["license"] == "Apache-2.0"
    assert set(project["license-files"]) >= {"LICENSE", "NOTICE"}


def test_spdx_headers_present_on_all_sources() -> None:
    """Gate item 9. Proposed name: not in SCOPE section 5, which only lists the files."""
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "check_headers.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------------------------------- CI


def test_ci_matrix_required_311_314_nonblocking_315() -> None:
    workflow = _workflow()
    required, nonblocking = _jobs_by_python(workflow)
    assert len(required) == 1, "exactly one job carries the required python matrix"
    job = required[0]
    versions = job["strategy"]["matrix"]["python-version"]
    assert versions == REQUIRED_PYTHONS, "versions must be quoted strings, never YAML floats"
    assert all(isinstance(v, str) for v in versions)
    assert "include" not in job["strategy"]["matrix"]
    assert "exclude" not in job["strategy"]["matrix"]
    assert job["strategy"].get("fail-fast") is False
    assert not job.get("continue-on-error"), "required jobs must not be non-blocking"
    assert len(nonblocking) == 1, "exactly one job runs 3.15"
    assert nonblocking[0] is not job
    assert nonblocking[0].get("continue-on-error") is True
    assert "3.15" not in versions


def test_actions_pinned_by_sha() -> None:
    uses = _all_uses(_workflow())
    assert uses, "the workflow uses no actions at all; the pin check would be vacuous"
    assert unpinned_uses(uses) == []
    # negative control: the checker must catch a tag and a short SHA
    assert unpinned_uses(["actions/checkout@v4", "actions/checkout@3d3c42e"]) == [
        "actions/checkout@v4",
        "actions/checkout@3d3c42e",
    ]
    assert unpinned_uses(["actions/checkout@" + "a" * 40, "./local-action"]) == []


def _commands(workflow: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if "run" in step:
                for line in str(step["run"]).splitlines():
                    out.append((name, line.strip()))
    return out


def test_uv_lock_committed_and_sync_locked() -> None:
    """The lock exists, is not ignored, and every uv call in CI is --locked.

    Committed-ness itself cannot be asserted while the repository has no commits (the user has
    not asked for any); "not ignored" is the strongest check available.
    """
    lock = ROOT / "uv.lock"
    assert lock.is_file()
    ignored = subprocess.run(  # noqa: S603
        [GIT, "check-ignore", "-q", "uv.lock"], cwd=ROOT, check=False
    )
    assert ignored.returncode == 1, "uv.lock is git-ignored"
    uv_calls = [(j, c) for j, c in _commands(_workflow()) if re.match(r"uv (sync|run|pip)\b", c)]
    assert uv_calls, "no uv call found in the workflow"
    for job, command in uv_calls:
        assert not command.startswith("uv pip"), f"{job}: {command}"
        assert "--locked" in command, f"{job}: uv call without --locked: {command}"
        assert "--frozen" not in command, f"{job}: --frozen skips the lock check: {command}"


def test_uv_lock_up_to_date_with_pyproject() -> None:
    """Proposed name. The lock matches pyproject (offline, so no network is needed)."""
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to run the test suite"
    proc = subprocess.run(  # noqa: S603
        [uv, "lock", "--check", "--offline"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ci_uv_version_pinned_exactly() -> None:
    """Proposed name (SCOPE section 4: 'pin uv version and action SHAs')."""
    workflow = _workflow()
    pinned = str(workflow["env"]["UV_VERSION"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned)
    required = _toml(ROOT / "pyproject.toml")["tool"]["uv"]["required-version"]
    assert required == f">={pinned}"
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                version = str(step["with"].get("version"))
                assert version == "${{ env.UV_VERSION }}", f"{name}: setup-uv version {version}"


def test_ci_required_test_jobs_sync_locked_groups_without_bidi_ref() -> None:
    """The required 3.11-3.14 jobs sync the locked groups before pytest; no job references the
    removed bidi-ref group (python-bidi was removed on the owner's approval, 2026-10-22)."""
    workflow = _workflow()
    required, _ = _jobs_by_python(workflow)
    assert len(required) == 1
    commands = [str(step["run"]) for step in required[0].get("steps", []) if "run" in step]
    syncs = [c for c in commands if re.search(r"\buv sync\b", c)]
    assert syncs, "no uv sync step in the required test job; commands: " + " | ".join(commands)
    assert all("--locked" in c for c in syncs), f"uv sync without --locked: {syncs}"
    sync_index = commands.index(syncs[0])
    pytest_index = next(i for i, c in enumerate(commands) if "pytest" in c)
    assert sync_index <= pytest_index, "the sync must run before pytest"
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            assert "bidi-ref" not in str(step), f"{name}: step references bidi-ref"


# ------------------------------------------------------------------- dependency and licenses


def test_runtime_deps_license_allowlist() -> None:
    policy = _toml(POLICY)
    project = _toml(PACKAGE_DIR / "pyproject.toml")["project"]
    declared = list(project["dependencies"])
    assert (
        declared
        == policy["runtime"]["requires_dist"]
        == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    )
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+>=\d+(\.\d+)*,<\d+(\.\d+)*", d) for d in declared)
    assert _runtime_closure() == {_norm(n) for n in policy["runtime"]["closure"]}
    allowed = set(policy["allowlist"]["spdx"])
    assert not any(re.search(r"GPL|MPL|SSPL|BUSL|CC-BY-NC", s) for s in allowed)
    scan = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "check_licenses.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert scan.returncode == 0, scan.stdout + scan.stderr
    assert " 0 failed" in scan.stdout


TEST_ONLY = {
    "cryptography",
    "jsonschema",
    "hypothesis",
    "pyyaml",
    "pytest",
    "pathspec",
    "mypy",
    "ruff",
    "pre-commit",
    "hatchling",
}


def test_test_only_deps_absent_from_runtime_metadata() -> None:
    project = _toml(PACKAGE_DIR / "pyproject.toml")["project"]
    names = {_norm(re.split(r"[=<>!~ \[;]", d, maxsplit=1)[0]) for d in project["dependencies"]}
    assert names == {"pydantic", "rfc8785"}
    assert not project.get("optional-dependencies"), "extras would leak test-only dependencies"
    assert not (names & {_norm(n) for n in TEST_ONLY})
    # the lock's view of the package agrees with the metadata
    entry = _lock_packages()["tracer-evalschema"]
    assert {_norm(d["name"]) for d in entry["dependencies"]} == {"pydantic", "rfc8785"}
    assert _runtime_closure().isdisjoint({_norm(n) for n in TEST_ONLY})
    # python-bidi and its bidi-ref group were removed (owner approval, 2026-10-22)
    root = _toml(ROOT / "pyproject.toml")
    groups = root["dependency-groups"]
    assert "bidi-ref" not in groups
    assert "bidi-ref" not in root["tool"]["uv"]["default-groups"]
    assert "python-bidi" not in root["tool"]["uv"].get("no-build-package", [])


def test_license_exceptions_exact() -> None:
    policy = _toml(POLICY)
    exceptions = {e["name"]: e for e in policy["exceptions"]}
    assert set(exceptions) == {"hypothesis", "pathspec"}
    assert exceptions["hypothesis"]["spdx"] == "MPL-2.0"
    assert exceptions["pathspec"]["spdx"] == "MPL-2.0"
    assert set(exceptions["hypothesis"]["groups"]) <= {"dev", "test"}
    assert exceptions["pathspec"]["groups"] == ["dev"]
    locked = _lock_packages()
    for name, entry in exceptions.items():
        assert entry["version"] == locked[name]["version"], f"{name}: exception not pinned to lock"
    # every exception is a named, never-runtime dependency
    assert set(exceptions).isdisjoint(_runtime_closure())


def test_no_gpl_family_package_in_lock() -> None:
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert not re.search(r"(?i)gpl|general public", lock_text)
    assert {"python-bidi", "pyfribidi"}.isdisjoint(_lock_packages())
    policy = _toml(POLICY)
    assert not any(re.search(r"(?i)gpl", e["spdx"]) for e in policy["exceptions"])


# -------------------------------------------------------------------- private fixture guard


def test_private_fixtures_gitignored() -> None:
    assert PRIVATE_PREFIX in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for sample in ("a.wav", "voicera/call.txt", "deep/er/x.json"):
        proc = subprocess.run(  # noqa: S603
            [GIT, "check-ignore", "-q", PRIVATE_PREFIX + sample], cwd=ROOT, check=False
        )
        assert proc.returncode == 0, f"{sample} under private/ is not ignored"
    public = subprocess.run(  # noqa: S603
        [GIT, "check-ignore", "-q", "packages/evalschema/tests/fixtures/public/a.json"],
        cwd=ROOT,
        check=False,
    )
    assert public.returncode == 1, "the ignore rule is broader than the private directory"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [
            GIT,
            "-c",
            "user.name=guard-test",
            "-c",
            "user.email=guard-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_private_fixture_guard_fails_when_tracked(tmp_path: Path) -> None:
    """Runs the real guard in a throwaway repository (the project repository is never touched)."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(GUARD_SCRIPT, repo / "scripts" / "guard_private_fixtures.py")
    _git(repo, "init", "-q")

    def run_guard() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-I", str(repo / "scripts" / "guard_private_fixtures.py")],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )

    assert run_guard().returncode == 0, "clean repository must pass"
    private = repo / PRIVATE_PREFIX
    private.mkdir(parents=True)
    (private / "call.txt").write_text("synthetic placeholder, not real data\n", encoding="utf-8")
    assert run_guard().returncode == 0, "an untracked private file must not trip the guard"
    _git(repo, "add", "-f", PRIVATE_PREFIX + "call.txt")
    staged = run_guard()
    assert staged.returncode == 1, "a staged private file must fail the guard"
    assert "call.txt" in staged.stderr
    _git(repo, "commit", "-q", "-m", "synthetic")
    tracked = run_guard()
    assert tracked.returncode == 1, "a tracked private file must fail the guard"
    _git(repo, "rm", "-q", "--cached", PRIVATE_PREFIX + "call.txt")
    _git(repo, "commit", "-q", "-m", "untrack")
    in_history = run_guard()
    assert in_history.returncode == 1, "a private file that is still in history must fail"
