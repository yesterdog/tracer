# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P2-02: licence policy for the tracer-evaldb closure (contract section 2).

The policy block format is the one in docs/security/P2-02-licenses.md section 6.2. The existing
Phase 1 test ``test_license_exceptions_exact`` stays as it is and is not repeated here.
"""

from __future__ import annotations

import importlib.metadata as md
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.utils import canonicalize_name


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
POLICY = ROOT / "docs" / "security" / "license-policy.toml"
SCRIPT = ROOT / "scripts" / "check_licenses.py"
EVALDB = ROOT / "packages" / "evaldb"

EVALDB_CLOSURE = [
    "alembic",
    "annotated-types",
    "mako",
    "markupsafe",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "sqlalchemy",
    "typing-extensions",
    "typing-inspection",
]
EVALDB_EXTRAS_POSTGRES = ["asn1crypto", "pg8000", "python-dateutil", "scramp", "six", "tzdata"]
GPL_FAMILY = re.compile(r"(?i)\b(?:a|l)?gpl\b|general public licen[cs]e")


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _lock() -> dict[str, dict[str, Any]]:
    return {canonicalize_name(p["name"]): p for p in _toml(ROOT / "uv.lock")["package"]}


def _reachable(root: str, extras: tuple[str, ...] = ()) -> set[str]:
    packages = _lock()
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        entry = packages[name]
        todo += [canonicalize_name(d["name"]) for d in entry.get("dependencies", [])]
        if name == root:
            for extra in extras:
                optional = entry.get("optional-dependencies", {}).get(extra, [])
                todo += [canonicalize_name(d["name"]) for d in optional]
    seen.discard(root)
    return seen


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_license_policy_lists_evaldb_closure() -> None:
    policy = _toml(POLICY)
    block = policy["runtime"]["evaldb"]
    assert set(block) == {"closure", "extras_postgres", "requires_dist"}
    assert block["closure"] == EVALDB_CLOSURE
    assert block["extras_postgres"] == EVALDB_EXTRAS_POSTGRES
    # the block agrees with the lock: nothing unreviewed, nothing missing
    base = _reachable("tracer-evaldb") - {"tracer-evalschema"}
    with_extra = _reachable("tracer-evaldb", ("postgres",)) - {"tracer-evalschema"}
    assert base == set(block["closure"])
    assert with_extra - base == set(block["extras_postgres"])
    # the existing evalschema block is untouched and stays a sub-set of the evaldb one
    assert policy["runtime"]["requires_dist"] == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    assert set(policy["runtime"]["closure"]) <= set(block["closure"])
    # the two packages whose metadata is not an SPDX expression are cleared by pinned overrides
    overrides = {o["name"]: o for o in policy["overrides"]}
    versions = {name: _lock()[name]["version"] for name in ("pg8000", "python-dateutil")}
    for name, spdx in (
        ("pg8000", "BSD-3-Clause"),
        ("python-dateutil", "Apache-2.0 AND BSD-3-Clause"),
    ):
        override = overrides[name]
        assert override["version"] == versions[name]
        assert override["spdx"] == spdx
        assert override["scope"] == "runtime"
        assert override["license_file"] == "licenses/LICENSE"
        assert re.fullmatch(r"[0-9a-f]{64}", override["license_file_sha256"])
        assert override["reason"].strip()
    # psycopg stays rejected (LGPL-3.0-only) and is recorded as such
    rejected = {r["name"]: r for r in policy["rejected"]}
    assert rejected["psycopg"]["spdx"] == "LGPL-3.0-only"
    assert rejected["psycopg"]["reason"].strip()
    assert {"psycopg", "psycopg-binary"} <= set(rejected)
    assert not any(n.startswith("psycopg") for n in _lock())
    # the allowlist did not grow a copyleft term
    assert not any(GPL_FAMILY.search(s) for s in policy["allowlist"]["spdx"])


def test_no_lgpl_or_gpl_in_lock() -> None:
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert not GPL_FAMILY.search(lock_text)
    locked = _lock()
    assert {"sqlalchemy", "alembic", "pg8000"} <= set(locked), "the evaldb closure is not locked"
    banned = {
        "psycopg",
        "psycopg-binary",
        "psycopg-c",
        "psycopg2",
        "psycopg2-binary",
        "python-bidi",
    }
    assert banned.isdisjoint(locked)
    # the installed metadata of the whole evaldb closure (extra included) is not copyleft
    names = _reachable("tracer-evaldb", ("postgres",))
    for name in sorted(names - {"tracer-evalschema"}):
        meta = md.metadata(name)
        fields = [
            *meta.get_all("License-Expression", []),
            *meta.get_all("License", []),
            *meta.get_all("Classifier", []),
        ]
        assert not any(GPL_FAMILY.search(f) for f in fields), f"{name}: {fields}"


def test_check_licenses_covers_both_packages(tmp_path: Path) -> None:
    """The script of Phase 1 scans two published packages now (acceptance of P2-02)."""
    done = _run_script()
    assert done.returncode == 0, done.stdout + done.stderr
    assert " 0 failed" in done.stdout
    text = POLICY.read_text(encoding="utf-8")

    def tampered(old: str, new: str, after: str) -> Path:
        start = text.index(after)
        assert old in text[start:], old
        cut = start + text[start:].index(old)
        path = tmp_path / f"policy-{len(list(tmp_path.iterdir()))}.toml"
        path.write_text(text[:cut] + new + text[cut + len(old) :], encoding="utf-8")
        return path

    extra_in_closure = tampered(
        "closure = [", 'closure = [\n    "zzz-not-in-the-lock",', "[runtime.evaldb]"
    )
    done = _run_script("--policy", str(extra_in_closure))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "tracer-evaldb" in done.stderr
    assert "zzz-not-in-the-lock" in done.stderr

    missing_extra = tampered('    "tzdata",\n', "", "extras_postgres")
    done = _run_script("--policy", str(missing_extra))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "tzdata" in done.stderr

    # the built wheel is checked against its own Requires-Dist and extra
    dist = tmp_path / "dist"
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(dist)],
        cwd=EVALDB,
        check=True,
        capture_output=True,
    )
    done = _run_script("--wheel", str(dist), "--artifacts-only")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 ok, 0 failed" in done.stdout
    loose = tampered('"sqlalchemy>=2.1.6,<3"', '"sqlalchemy>=2.1.6"', "[runtime.evaldb]")
    done = _run_script("--wheel", str(dist), "--artifacts-only", "--policy", str(loose))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "Requires-Dist" in done.stderr


@pytest.mark.parametrize("name", ["markupsafe", "asn1crypto", "python-dateutil", "six"])
def test_licence_files_of_unassumed_packages_are_read(name: str) -> None:
    """The licence of these four is read from the distribution's own file, not assumed."""
    dist = md.distribution(name)
    files = [f for f in (dist.files or []) if "LICENSE" in f.name.upper()]
    assert files, f"{name}: the installed distribution carries no licence file"
    body = files[0].read_text() or ""
    assert re.search(r"(?i)permission|redistribution|apache license", body)
