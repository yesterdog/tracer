# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-02: licence policy for the three Phase 3 closures (contract
docs/phase3/api-surface-batch-a.md section 6; review docs/security/P3-02-licenses.md).

The policy block format is the one of ``[runtime.evaldb]``: ``closure`` (sorted, workspace
packages left out) and ``requires_dist``. certifi (MPL-2.0) is accepted by name as a pinned
``[[exceptions]]`` entry; ``sarvamai`` and ``requests`` are recorded as rejected.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import re
import subprocess
import sys
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
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
PACKAGES = ROOT / "packages"
GPL_FAMILY = re.compile(r"(?i)\b(?:a|l)?gpl\b|general public licen[cs]e")

REQUIRES = {
    "evalcore": ["tracer-evalschema==0.0.1"],
    "evalproviders": ["tracer-evalschema==0.0.1", "tracer-evalcore==0.0.1", "httpx>=0.28.1,<1"],
    "cli": [
        "tracer-evalschema==0.0.1",
        "tracer-evalcore==0.0.1",
        "tracer-evalproviders==0.0.1",
        "tracer-evaldb==0.0.1",
    ],
}
DIST = {"evalcore": "tracer-evalcore", "evalproviders": "tracer-evalproviders", "cli": "tracer-cli"}
CORE = ["annotated-types", "pydantic", "pydantic-core", "rfc8785", "typing-extensions"]
CLOSURES = {
    "evalcore": sorted([*CORE, "typing-inspection"]),
    "evalproviders": sorted(
        [*CORE, "typing-inspection", "anyio", "certifi", "h11", "httpcore", "httpx", "idna"]
    ),
    "cli": sorted(
        [
            *CORE,
            "typing-inspection",
            "anyio",
            "certifi",
            "h11",
            "httpcore",
            "httpx",
            "idna",
            "alembic",
            "mako",
            "markupsafe",
            "sqlalchemy",
        ]
    ),
}
#: What the licence file of each httpx-closure package must read like (read from the installed
#: distribution, never assumed from the declared metadata).
LICENCE_TEXT = {
    "httpx": r"(?i)redistribution and use",
    "httpcore": r"(?i)redistribution and use",
    "idna": r"(?i)redistribution and use",
    "h11": r"(?i)permission is hereby granted",
    "anyio": r"(?i)permission is hereby granted",
    "certifi": r"(?i)mozilla public license",
}


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _lock() -> dict[str, dict[str, Any]]:
    return {canonicalize_name(p["name"]): p for p in _toml(ROOT / "uv.lock")["package"]}


def _reachable(root: str) -> set[str]:
    packages = _lock()
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [canonicalize_name(d["name"]) for d in packages[name].get("dependencies", [])]
    seen.discard(root)
    return seen


def _key(text: str) -> tuple[str, str, Any]:
    req = Requirement(text)
    return (canonicalize_name(req.name), str(req.marker or ""), req.specifier)


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _licence_file(name: str) -> tuple[str, bytes]:
    dist = md.distribution(name)
    files = [f for f in (dist.files or []) if "LICENSE" in f.name.upper()]
    assert files, f"{name}: the installed distribution carries no licence file"
    return str(files[0]), files[0].read_binary()


# --------------------------------------------------------------------------- the SCOPE test


def test_license_policy_lists_phase3_closures() -> None:
    policy = _toml(POLICY)
    runtime = policy["runtime"]
    workspace = {
        canonicalize_name(p["name"]) for p in _lock().values() if "editable" in p["source"]
    }
    for key, closure in CLOSURES.items():
        block = runtime[key]
        assert set(block) == {"closure", "requires_dist"}, key
        assert block["closure"] == closure, key
        assert block["closure"] == sorted(block["closure"]), "the block is kept sorted"
        # the block agrees with the lock: nothing unreviewed and nothing missing
        assert _reachable(DIST[key]) - workspace == set(block["closure"]), key
        assert sorted(_key(d) for d in block["requires_dist"]) == sorted(
            _key(d) for d in REQUIRES[key]
        ), key
    # the evalschema and evaldb blocks are untouched
    assert runtime["requires_dist"] == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    assert set(runtime["evaldb"]) == {"closure", "extras_postgres", "requires_dist"}
    assert set(runtime["closure"]) <= set(runtime["evalcore"]["closure"])
    assert set(runtime["evalproviders"]["closure"]) <= set(runtime["cli"]["closure"])

    # certifi (MPL-2.0, file-level copyleft) is accepted by name, pinned, read from its file
    exceptions = {e["name"]: e for e in policy["exceptions"]}
    assert set(exceptions) == {"hypothesis", "pathspec", "certifi"}
    certifi = exceptions["certifi"]
    assert certifi["spdx"] == "MPL-2.0"
    assert certifi["version"] == _lock()["certifi"]["version"] == md.version("certifi")
    assert certifi["groups"] == ["dev"]
    assert certifi["scope"] == "runtime"
    assert certifi["license_file"] == "licenses/LICENSE"
    assert re.fullmatch(r"[0-9a-f]{64}", certifi["license_file_sha256"])
    _, body = _licence_file("certifi")
    assert certifi["license_file_sha256"] == hashlib.sha256(body).hexdigest()
    assert "project owner" in certifi["decided_by"]
    assert certifi["reason"].strip()
    assert "certifi" not in runtime["evalproviders"]["requires_dist"]
    # the packages are cleared from their own metadata: no override is needed for them
    overrides = {o["name"] for o in policy["overrides"]}
    assert overrides.isdisjoint({"httpx", "httpcore", "h11", "anyio", "idna", "certifi"})
    # the two refused candidates are recorded with their reason
    rejected = {r["name"]: r for r in policy["rejected"]}
    assert {"sarvamai", "requests", "psycopg", "psycopg-binary"} <= set(rejected)
    for name in ("sarvamai", "requests"):
        assert rejected[name]["spdx"].strip()
        assert rejected[name]["reason"].strip()
        assert name not in _lock()
    # the allowlist did not grow a copyleft term, and MPL stays outside it
    assert not any(GPL_FAMILY.search(s) for s in policy["allowlist"]["spdx"])
    assert "MPL-2.0" not in policy["allowlist"]["spdx"]


# -------------------------------------------------------------- added by qa (contract section 6)


def test_phase3_closures_are_not_copyleft_and_licence_files_are_read() -> None:
    for key in CLOSURES:
        for name in CLOSURES[key]:
            meta = md.metadata(name)
            fields = [
                *meta.get_all("License-Expression", []),
                *meta.get_all("License", []),
                *meta.get_all("Classifier", []),
            ]
            assert not any(GPL_FAMILY.search(f) for f in fields), f"{name}: {fields}"
    for name, pattern in LICENCE_TEXT.items():
        path, body = _licence_file(name)
        assert body.strip(), f"{name}: empty licence file {path}"
        assert re.search(pattern, body.decode("utf-8", errors="replace")), f"{name}: {path}"


def test_check_licenses_covers_phase3_packages(tmp_path: Path) -> None:
    """The script of Phase 1 and 2 scans five published packages now (acceptance of P3-02)."""
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

    def fails(path: Path, *needles: str) -> None:
        result = _run_script("--policy", str(path))
        assert result.returncode == 1, result.stdout + result.stderr
        for needle in needles:
            assert needle in result.stderr, (needle, result.stderr)

    fails(
        tampered("closure = [", 'closure = [\n    "zzz-not-in-the-lock",', "[runtime.evalcore]"),
        "tracer-evalcore",
        "zzz-not-in-the-lock",
    )
    fails(
        tampered('    "httpx",\n', "", "[runtime.evalproviders]"),
        "tracer-evalproviders",
        "httpx",
    )
    fails(
        tampered('    "sqlalchemy",\n', "", "[runtime.cli]"),
        "tracer-cli",
        "sqlalchemy",
    )
    # without its exception entry certifi is a licence failure that names the package
    stripped = re.sub(
        r'\[\[exceptions\]\]\nname = "certifi".*?(?=\n\[\[|\Z)', "", text, count=1, flags=re.DOTALL
    )
    assert stripped != text
    without = tmp_path / "policy-without-certifi.toml"
    without.write_text(stripped, encoding="utf-8")
    fails(without, "certifi")

    # the built wheels are checked against their own Requires-Dist and extras
    dist = tmp_path / "dist"
    for key in CLOSURES:
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(dist)],
            cwd=PACKAGES / key,
            check=True,
            capture_output=True,
        )
    done = _run_script("--wheel", str(dist), "--artifacts-only")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "3 ok, 0 failed" in done.stdout
    loose = tampered('"httpx>=0.28.1,<1"', '"httpx>=0.28.1"', "[runtime.evalproviders]")
    done = _run_script("--wheel", str(dist), "--artifacts-only", "--policy", str(loose))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "Requires-Dist" in done.stderr
    assert "tracer_evalproviders" in done.stderr


@pytest.mark.parametrize("key", sorted(CLOSURES))
def test_phase3_wheels_do_not_name_certifi(key: str, tmp_path: Path) -> None:
    """certifi is a dependency of httpx and httpcore only: no tracer wheel lists or ships it."""
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)],
        cwd=PACKAGES / key,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta = BytesParser().parsebytes(zf.read(meta_name))
    requires = {canonicalize_name(Requirement(r).name) for r in meta.get_all("Requires-Dist") or []}
    assert "certifi" not in requires
    assert not any(n.split("/")[0] in {"certifi", "httpx", "httpcore"} for n in names)
