# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P2-03: CI for tracer-evaldb (contract docs/phase2/api-surface-batch-a.md section 3).

These tests read ``.github/workflows/ci.yml`` and the root ``pyproject.toml`` and run pytest in a
subprocess. The checkers that scan for literal secrets are also run on deliberately bad samples,
so that a vacuous pass is impossible.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
TESTS = str(Path(__file__).resolve().parent.relative_to(ROOT))
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
yaml: Any = importlib.import_module("yaml")  # pyyaml, test group; untyped, so loaded dynamically

URL_ENV = "TRACER_TEST_POSTGRES_URL"
ASCII_URL_ENV = "TRACER_TEST_POSTGRES_SQL_ASCII_URL"
IMAGE_RE = re.compile(r"^postgres:(?P<major>\d+)(?:\.\d+)?@sha256:(?P<digest>[0-9a-f]{64})$")
PG_NAME = "test-postgres (py${{ matrix.python-version }}, pg${{ matrix.postgres }})"


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    return data


def _job(name: str) -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert name in jobs, f"job {name!r} is missing from ci.yml (jobs: {sorted(jobs)})"
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step["run"]) for step in job["steps"] if "run" in step)


def _combos(job: dict[str, Any]) -> list[dict[str, Any]]:
    include = job["strategy"]["matrix"]["include"]
    assert isinstance(include, list)
    return [dict(entry) for entry in include]


# --------------------------------------------------------------- literal secret detection

_URL_PASSWORD = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:(?P<pw>[^@\s/]+)@")
_PG_PASSWORD = re.compile(r"POSTGRES_PASSWORD\s*[:=]\s*(?P<v>\S+)")
_SECRET_KEY = re.compile(r"(?i)password|passwd|secret|token|api[_-]?key")


def _is_reference(value: str) -> bool:
    return value.strip("'\"").startswith("$")


def _env_mappings(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                found.append(value)
            found += _env_mappings(value)
    elif isinstance(node, list):
        for item in node:
            found += _env_mappings(item)
    return found


def literal_secret_problems(text: str) -> list[str]:
    """Reasons why this workflow text holds a literal database password or weakens auth."""
    problems = []
    for match in _PG_PASSWORD.finditer(text):
        if not _is_reference(match.group("v")):
            problems.append("POSTGRES_PASSWORD is not a variable reference")
    for match in _URL_PASSWORD.finditer(text):
        if not _is_reference(match.group("pw")):
            problems.append("URL with a literal password")
    if re.search(r"(?i)POSTGRES_HOST_AUTH_METHOD|\btrust\b", text):
        problems.append("trust authentication")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        document = None  # a shell fragment, not a workflow
    for env in _env_mappings(document):
        for key, value in env.items():
            if _SECRET_KEY.search(str(key)) and not _is_reference(str(value)):
                problems.append(f"env {key} holds a literal")
    return problems


def test_ci_postgres_job_matrix_and_pinned_image() -> None:
    job = _job("test-postgres")
    assert job["name"] == PG_NAME
    assert job["runs-on"] == "ubuntu-latest"
    assert not job.get("continue-on-error"), "the Postgres job is required"
    assert job["strategy"]["fail-fast"] is False
    assert set(job["strategy"]["matrix"]) == {"include"}
    combos = _combos(job)
    assert all(set(c) == {"python-version", "postgres", "image"} for c in combos)
    assert {(c["python-version"], c["postgres"]) for c in combos} == {
        ("3.11", "15"),
        ("3.11", "18"),
        ("3.14", "15"),
        ("3.14", "18"),
    }
    digests: dict[str, set[str]] = {}
    for combo in combos:
        match = IMAGE_RE.match(str(combo["image"]))
        assert match, f"image {combo['image']!r} is not postgres:<major>[.minor]@sha256:<digest>"
        assert match.group("major") == combo["postgres"]
        digests.setdefault(combo["postgres"], set()).add(match.group("digest"))
    assert all(len(found) == 1 for found in digests.values()), "one digest per major version"
    assert set(job.get("permissions", {"contents": "read"})) <= {"contents"}
    assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
    assert "services" not in job

    uv_steps = [s for s in job["steps"] if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert len(uv_steps) == 1
    assert uv_steps[0]["with"]["python-version"] == "${{ matrix.python-version }}"
    assert uv_steps[0]["with"]["version"] == "${{ env.UV_VERSION }}"

    text = _run_text(job)
    assert "uv sync --locked --all-groups" in text
    assert "${{ matrix.image }}" in text
    assert re.search(r"docker run\b", text)
    assert "-p 127.0.0.1:" in text, "the server listens on the loopback interface only"
    assert "pg_isready" in text
    assert re.search(r"ENCODING\s+'SQL_ASCII'", text)
    assert re.search(r"(?i)template\s*=?\s*template0", text)
    assert "Asia/Kolkata" in text
    assert URL_ENV in text
    assert ASCII_URL_ENV in text
    assert "postgresql+pg8000://" in text
    assert re.search(
        r"uv run --locked --all-groups pytest -m postgres packages/evaldb/tests\b", text
    )
    # no other mention of a postgres image anywhere in the file without its digest
    for ref in re.findall(r"\bpostgres:[0-9][^\s\"']*", WORKFLOW.read_text(encoding="utf-8")):
        assert "@sha256:" in ref or ref.startswith("postgres:${{"), ref

    nonblocking = _job("test-postgres-py315")
    assert nonblocking["continue-on-error"] is True
    assert nonblocking["name"] == "test-postgres (py3.15, pg18, non-blocking)"
    only = _combos(nonblocking)
    assert len(only) == 1
    assert (only[0]["python-version"], only[0]["postgres"]) == ("3.15", "18")
    assert only[0]["image"] in {c["image"] for c in combos if c["postgres"] == "18"}
    assert _run_text(nonblocking).count("pytest -m postgres packages/evaldb/tests") == 1


def test_ci_lower_bound_leg_for_evaldb_ranges() -> None:
    """Gate item 11: every evaldb runtime dependency is tested at the lower bound of its range."""
    job = _job("test-oldest-evaldb-deps")
    assert job["name"] == "test (lowest-direct evaldb deps, py3.11, pg15)"
    assert not job.get("continue-on-error")
    uv_steps = [s for s in job["steps"] if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert uv_steps[0]["with"]["python-version"] == "3.11"
    text = _run_text(job)
    assert "--resolution lowest-direct" in text, "full 'lowest' fails on an old transitive dep"
    assert "--resolution lowest " not in text + " "
    assert 'pytest -m "not private_fixtures and not local_only" packages/evaldb/tests' in text
    pg15 = {
        IMAGE_RE.match(str(c["image"]))
        for c in _combos(_job("test-postgres"))
        if c["postgres"] == "15"
    }
    digest15 = next(m for m in pg15 if m is not None).group("digest")
    assert f"@sha256:{digest15}" in text, "the lower-bound leg runs the pinned Postgres 15 image"
    assert URL_ENV in text
    assert "::add-mask::" in text


def test_ci_postgres_password_not_literal() -> None:
    # the checker has teeth
    for bad in (
        "run: docker run -e POSTGRES_PASSWORD=hunter2 postgres",
        "run: echo POSTGRES_PASSWORD: tracer",
        "run: export U=postgresql+pg8000://tracer:tracer@localhost/db",
        "run: docker run -e POSTGRES_HOST_AUTH_METHOD=trust postgres",
        "env:\n  PGPASSWORD: literal-value\n",
    ):
        assert literal_secret_problems(bad), bad
    for good in (
        'run: docker run -e POSTGRES_PASSWORD="$PG_PASSWORD" postgres',
        "run: export U=postgresql+pg8000://tracer:${PG_PASSWORD}@127.0.0.1:5432/db",
        "env:\n  PGPASSWORD: ${{ env.PG_PASSWORD }}\n",
    ):
        assert not literal_secret_problems(good), good

    text = WORKFLOW.read_text(encoding="utf-8")
    assert literal_secret_problems(text) == []
    workflow = _workflow()
    assert all("services" not in job for job in workflow["jobs"].values())
    assert workflow["permissions"] == {"contents": "read"}
    for name in ("test-postgres", "test-postgres-py315", "test-oldest-evaldb-deps"):
        run = _run_text(_job(name))
        assert "openssl rand -hex 24" in run, name
        mask = run.index("::add-mask::")
        assert mask < run.index("GITHUB_ENV"), (
            f"{name}: the password is masked before it is written"
        )
        assert run.index("openssl rand -hex 24") < mask


def test_ci_sqlite_runs_on_every_python_leg() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ini = pyproject["tool"]["pytest"]["ini_options"]
    assert ini["testpaths"] == [  # Phase 3 spec change (P3-01): three more test directories
        "packages/evalschema/tests",
        "packages/evaldb/tests",
        "packages/evalcore/tests",
        "packages/evalproviders/tests",
        "packages/cli/tests",
        "packages/evalsim/tests",  # Phase 4 spec change (P4-01): one more test directory
    ]
    marker_match = re.search(r"-m\s+'([^']*)'", ini["addopts"])
    assert marker_match, ini["addopts"]
    terms = marker_match.group(1).split(" and ")
    assert sorted(terms) == ["not local_only", "not postgres", "not private_fixtures"]
    assert "--strict-markers" in ini["addopts"]
    assert any(str(m).startswith("postgres:") for m in ini["markers"])
    # the existing legs run the bare command, so testpaths and addopts decide what runs
    for name in ("test", "test-py315"):
        job = _job(name)
        pytest_steps = [
            str(s["run"]) for s in job["steps"] if "run" in s and "pytest" in str(s["run"])
        ]
        assert pytest_steps == ["uv run --locked --all-groups pytest"], name
    assert _job("test")["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13", "3.14"]
    # the lint job's commands are the bare ones: pyproject (mypy files, ruff src) names the paths
    lint = _run_text(_job("lint"))
    assert "uv run --locked ruff check" in lint
    assert "uv run --locked mypy" in lint
    # the licence job builds the evaldb wheel and checks it
    licences = _run_text(_job("licenses"))
    assert "uv build --package tracer-evaldb" in licences


def _pytest(
    *args: str,
    env_extra: dict[str, str] | None = None,
    drop: tuple[str, ...] = (URL_ENV, ASCII_URL_ENV),
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in drop and k != "PYTEST_ADDOPTS"}
    env.update(env_extra or {})
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_postgres_marker_without_url_is_usage_error() -> None:
    # selected and no URL: a usage error (exit code 4), never a skip, nothing runs
    done = _pytest("-m", "postgres", TESTS)
    out = done.stdout + done.stderr
    assert done.returncode == 4, out
    assert URL_ENV in out
    assert "skipped" not in out.lower()
    assert " passed" not in out
    assert " failed" not in out
    # the repository default deselects the marker, so the plain command line collects fine
    plain = _pytest("--collect-only", "-q", TESTS)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "[sqlite]" in plain.stdout
    assert "[postgres]" not in plain.stdout
    # with a URL set the marker selects exactly the postgres cases (no connection at collection)
    dummy = "postgresql+pg8000://user:pw@127.0.0.1:1/db"
    selected = _pytest(
        "-m",
        "postgres",
        "--collect-only",
        "-q",
        TESTS,
        env_extra={URL_ENV: dummy},
    )
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert "[postgres]" in selected.stdout
    assert "[sqlite]" not in selected.stdout
    assert "test_postgres_session_timezone_is_utc" in selected.stdout
    assert "skip" not in selected.stdout.lower()
