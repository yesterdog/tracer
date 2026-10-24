# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P2-01: scaffold of packages/evaldb (contract docs/phase2/api-surface-batch-a.md section 1).

These tests read repository files, build the wheel and start fresh interpreters. They do not need
the database drivers. The six tests named in docs/phase2/SCOPE.md P2-01 come first; the last two
tests are added by qa to make "ruff and mypy cover the new paths" and the baseline file testable.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
EVALDB = ROOT / "packages" / "evaldb"
EVALDB_SRC = EVALDB / "src" / "tracer_evaldb"
EVALSCHEMA = ROOT / "packages" / "evalschema"
EVALSCHEMA_SRC = EVALSCHEMA / "src" / "tracer_evalschema"
POLICY = ROOT / "docs" / "security" / "license-policy.toml"
BASELINE = ROOT / "docs" / "phase2" / "evidence" / "evalschema-baseline.json"

#: The exact runtime requirements of the evaldb distribution (docs/phase2/SCOPE.md Decisions D2).
EXPECTED_BASE = ["tracer-evalschema==0.0.1", "sqlalchemy>=2.1.6,<3", "alembic>=1.20.1,<2"]
EXPECTED_POSTGRES = ["pg8000>=1.31.5,<2"]

#: Third-party top-level modules the evaldb source may import (nothing else).
ALLOWED_IMPORTS = {"sqlalchemy", "alembic", "tracer_evalschema", "tracer_evaldb"}
#: Third-party top-level modules that may appear in sys.modules after importing every module.
ALLOWED_LOADED = ALLOWED_IMPORTS | {
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "typing_extensions",
    "typing_inspection",
    "rfc8785",
    "mako",
    "markupsafe",
    "cython_runtime",  # a marker module of Cython-compiled extensions, not a package
}
FORBIDDEN_LOADED = {
    "pg8000",
    "scramp",
    "dateutil",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "pytest",
    "jsonschema",
    "hypothesis",
    "yaml",
}


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _req_key(text: str) -> tuple[str, str, SpecifierSet]:
    """Parsed form of a requirement: wheel metadata reorders ``>=2.1.6,<3`` to ``<3,>=2.1.6``."""
    req = Requirement(text)
    assert not req.extras
    return (canonicalize_name(req.name), str(req.marker or ""), req.specifier)


def _imports_of(path: Path) -> list[tuple[int, str]]:
    """Top-level module names imported by a file, at any depth, plus literal dynamic imports.

    A dynamic import with a non-literal name is reported as ``<dynamic>``.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name.split(".")[0]) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in {"import_module", "__import__"}:
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append((node.lineno, arg.value.split(".")[0]))
                else:
                    found.append((node.lineno, "<dynamic>"))
    return found


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _run_isolated(code: str) -> str:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def _loaded_top_levels_after_importing_every_module(package: str) -> set[str]:
    code = f"""
import importlib, json, pkgutil, sys
before = {{n.split(".")[0] for n in sys.modules}}
pkg = importlib.import_module({package!r})
names = [
    m.name for m in pkgutil.walk_packages(pkg.__path__, {package + "."!r})
    if ".migrations" not in m.name
]
for name in names:
    importlib.import_module(name)
after = {{n.split(".")[0] for n in sys.modules}}
print(json.dumps(sorted(after - before)))
"""
    loaded = set(json.loads(_run_isolated(code)))
    return {n for n in loaded if n not in sys.stdlib_module_names and not n.startswith("_")}


def _lock_packages() -> dict[str, dict[str, Any]]:
    lock = _toml(ROOT / "uv.lock")
    return {canonicalize_name(p["name"]): p for p in lock["package"]}


def _closure(root: str, *, extras: tuple[str, ...] = ()) -> set[str]:
    packages = _lock_packages()
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        entry = packages[name]
        for dep in entry.get("dependencies", []):
            todo.append(canonicalize_name(dep["name"]))
        if name == root:
            for extra in extras:
                for dep in entry.get("optional-dependencies", {}).get(extra, []):
                    todo.append(canonicalize_name(dep["name"]))
    seen.discard(root)
    return seen


# ------------------------------------------------------------------ the six SCOPE tests


def test_evaldb_runtime_imports_whitelist() -> None:
    files = _python_files(EVALDB_SRC)
    assert files, f"{EVALDB_SRC} holds no module (the package is not scaffolded)"
    offences = []
    for path in files:
        for line, top in _imports_of(path):
            ok = top in sys.stdlib_module_names or top in ALLOWED_IMPORTS
            if not ok:
                offences.append(f"{path.relative_to(EVALDB_SRC)}:{line}: import of {top}")
    assert not offences, offences
    # pg8000 is reached only through the SQLAlchemy URL; importing the package loads no driver
    loaded = _loaded_top_levels_after_importing_every_module("tracer_evaldb")
    assert loaded <= ALLOWED_LOADED, sorted(loaded - ALLOWED_LOADED)
    assert not (loaded & FORBIDDEN_LOADED), sorted(loaded & FORBIDDEN_LOADED)
    assert "sqlalchemy" in loaded


def test_evalschema_does_not_import_evaldb() -> None:
    banned = {"tracer_evaldb", "sqlalchemy", "alembic", "pg8000", "psycopg", "psycopg2", "asyncpg"}
    offences = []
    for path in _python_files(EVALSCHEMA_SRC):
        for line, top in _imports_of(path):
            if top in banned:
                offences.append(f"{path.relative_to(EVALSCHEMA_SRC)}:{line}: import of {top}")
    assert not offences, offences
    code = f"""
import importlib, json, pkgutil, sys
pkg = importlib.import_module("tracer_evalschema")
for m in pkgutil.walk_packages(pkg.__path__, "tracer_evalschema."):
    importlib.import_module(m.name)
print(json.dumps(sorted({{n.split(".")[0] for n in sys.modules}} & set({sorted(banned)!r}))))
"""
    assert json.loads(_run_isolated(code)) == []


def test_evalschema_runtime_closure_unchanged_by_evaldb() -> None:
    policy = _toml(POLICY)["runtime"]
    expected = {
        "annotated-types",
        "pydantic",
        "pydantic-core",
        "rfc8785",
        "typing-extensions",
        "typing-inspection",
    }
    assert set(policy["closure"]) == expected
    assert policy["requires_dist"] == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    assert _closure("tracer-evalschema") == expected
    entry = _lock_packages()["tracer-evalschema"]
    assert {canonicalize_name(d["name"]) for d in entry["dependencies"]} == {"pydantic", "rfc8785"}
    assert not entry.get("optional-dependencies")
    project = _toml(EVALSCHEMA / "pyproject.toml")["project"]
    assert project["dependencies"] == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    assert not project.get("optional-dependencies")


def test_evaldb_requires_dist_bounded_ranges(tmp_path: Path) -> None:
    project = _toml(EVALDB / "pyproject.toml")["project"]
    assert project["name"] == "tracer-evaldb"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "Apache-2.0"
    assert sorted(_req_key(d) for d in project["dependencies"]) == sorted(
        _req_key(d) for d in EXPECTED_BASE
    )
    extras = project["optional-dependencies"]
    assert set(extras) == {"postgres"}
    assert [_req_key(d) for d in extras["postgres"]] == [_req_key(d) for d in EXPECTED_POSTGRES]
    for text in [*project["dependencies"], *extras["postgres"]]:
        req = Requirement(text)
        if canonicalize_name(req.name) == "tracer-evalschema":
            assert req.specifier == SpecifierSet("==0.0.1")  # the schema-version pin stays exact
            continue
        ops = sorted((s.operator, s.version) for s in req.specifier)
        assert [o for o, _ in ops] == ["<", ">="], f"{text}: not a bounded range"
        lower = dict(ops)[">="]
        upper = dict(ops)["<"]
        assert re.fullmatch(r"\d+(\.\d+)*", lower), text
        assert upper == str(int(lower.split(".")[0]) + 1), f"{text}: upper bound is not next major"
    # the licence policy lists the same requirements (compared as parsed requirements)
    policy = _toml(POLICY)["runtime"]["evaldb"]
    wanted = [*EXPECTED_BASE, "pg8000>=1.31.5,<2; extra == 'postgres'"]
    assert sorted(_req_key(d) for d in policy["requires_dist"]) == sorted(
        _req_key(d) for d in wanted
    )
    # the built wheel declares the same, parsed (the build backend reorders specifiers)
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = BytesParser().parsebytes(zf.read(meta_name))
    assert meta.get_all("Provides-Extra") == ["postgres"]
    assert sorted(_req_key(r) for r in meta.get_all("Requires-Dist") or []) == sorted(
        _req_key(d) for d in wanted
    )
    assert meta["Requires-Python"] == ">=3.11"


def _build_wheel(out: Path) -> Path:
    out.mkdir(exist_ok=True)
    existing = sorted(out.glob("*.whl"))
    if existing:
        return existing[0]
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(out)],
        cwd=EVALDB,
        check=True,
        capture_output=True,
    )
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_evaldb_wheel_contains_migrations_and_no_tests(tmp_path: Path) -> None:
    with zipfile.ZipFile(_build_wheel(tmp_path)) as zf:
        names = zf.namelist()
    required = {
        "tracer_evaldb/__init__.py",
        "tracer_evaldb/py.typed",
        "tracer_evaldb/migrations/env.py",
        "tracer_evaldb/migrations/script.py.mako",
        "tracer_evaldb/migrations/versions/__init__.py",
    }
    assert required <= set(names), sorted(required - set(names))
    dist_info = {n.split("/")[0] for n in names if ".dist-info/" in n}
    assert len(dist_info) == 1
    top = {n.split("/")[0] for n in names}
    assert top == {"tracer_evaldb", next(iter(dist_info))}, sorted(top)
    for name in names:
        parts = name.split("/")
        base = parts[-1]
        assert "tests" not in parts, name
        assert "test" not in parts, name
        assert not base.startswith("test_"), name
        assert base != "conftest.py", name
        assert "evalschema_baseline" not in base, name
        assert "fixtures" not in parts, name
    info = next(iter(dist_info))
    licence_members = {n for n in names if n.startswith(f"{info}/") and "LICENSE" in n}
    notice_members = {n for n in names if n.startswith(f"{info}/") and "NOTICE" in n}
    assert licence_members
    assert notice_members


def test_evalschema_unchanged_since_phase1_gate(tmp_path: Path) -> None:
    tool_path = Path(__file__).resolve().parent / "evalschema_baseline.py"
    spec = importlib.util.spec_from_file_location("evalschema_baseline", tool_path)
    assert spec is not None
    assert spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    # the hash function has teeth: it follows content, names and additions, not bytecode caches
    copy = tmp_path / "probe"
    shutil.copytree(EVALSCHEMA_SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert tool.source_tree_sha256(copy) == tool.source_tree_sha256(EVALSCHEMA_SRC)
    (copy / "__pycache__").mkdir()
    (copy / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"x")
    assert tool.source_tree_sha256(copy) == tool.source_tree_sha256(EVALSCHEMA_SRC)
    target = copy / "ids.py"
    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    assert tool.source_tree_sha256(copy) != tool.source_tree_sha256(EVALSCHEMA_SRC)
    target.write_bytes(original)
    target.rename(copy / "ids2.py")
    assert tool.source_tree_sha256(copy) != tool.source_tree_sha256(EVALSCHEMA_SRC)

    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(recorded) == {
        "commit",
        "schema_lock_sha256",
        "source_tree_sha256",
        "pyproject_sha256",
    }
    assert re.fullmatch(r"[0-9a-f]{40}", recorded["commit"])
    current = tool.current_values()
    for key, value in current.items():
        assert re.fullmatch(r"[0-9a-f]{64}", recorded[key]), key
        assert recorded[key] == value, f"tracer-evalschema changed since the baseline: {key}"


# ------------------------------------------------- added by qa (not in the SCOPE table)


def test_evaldb_scaffold_files_and_repo_config() -> None:
    for name in ("LICENSE", "NOTICE", "README.md", "pyproject.toml"):
        assert (EVALDB / name).is_file(), name
    assert (EVALDB / "LICENSE").read_bytes() == (EVALSCHEMA / "LICENSE").read_bytes()
    assert (EVALDB_SRC / "py.typed").is_file()
    for path in _python_files(EVALDB / "src"):
        head = path.read_text(encoding="utf-8").splitlines()[:2]
        assert head == [
            "# SPDX-License-Identifier: Apache-2.0",
            "# Copyright 2026 The Tracer contributors",
        ], path
    build = _toml(EVALDB / "pyproject.toml")
    assert build["build-system"]["build-backend"] == "hatchling.build"
    assert build["build-system"]["requires"] == ["hatchling==1.32.4"]
    assert build["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/tracer_evaldb"]

    root = _toml(ROOT / "pyproject.toml")
    assert "packages/*" in root["tool"]["uv"]["workspace"]["members"]
    assert "tracer-evaldb[postgres]" in root["dependency-groups"]["dev"]
    assert root["tool"]["uv"]["sources"]["tracer-evaldb"] == {"workspace": True}
    ruff_src = set(root["tool"]["ruff"]["src"])
    assert {"packages/evaldb/src", "packages/evaldb/tests"} <= ruff_src
    mypy = root["tool"]["mypy"]
    assert {"packages/evaldb/src", "packages/evaldb/tests"} <= set(mypy["files"])
    assert {"packages/evalschema/src", "packages/evaldb/src"} <= set(mypy["mypy_path"].split(":"))
    lock = _lock_packages()
    assert {"sqlalchemy", "alembic", "pg8000", "tracer-evaldb"} <= set(lock)
    assert lock["tracer-evaldb"]["source"] == {"editable": "packages/evaldb"}


@pytest.mark.parametrize(
    ("extras", "expected"),
    [
        (
            (),
            {
                "alembic",
                "annotated-types",
                "mako",
                "markupsafe",
                "pydantic",
                "pydantic-core",
                "rfc8785",
                "sqlalchemy",
                "tracer-evalschema",
                "typing-extensions",
                "typing-inspection",
            },
        ),
        (
            ("postgres",),
            {
                "alembic",
                "annotated-types",
                "asn1crypto",
                "mako",
                "markupsafe",
                "pg8000",
                "pydantic",
                "pydantic-core",
                "python-dateutil",
                "rfc8785",
                "scramp",
                "six",
                "sqlalchemy",
                "tracer-evalschema",
                "typing-extensions",
                "typing-inspection",
                "tzdata",
            },
        ),
    ],
    ids=["base", "postgres-extra"],
)
def test_evaldb_locked_closure_is_the_reviewed_one(
    extras: tuple[str, ...], expected: set[str]
) -> None:
    """docs/security/P2-02-licenses.md section 2: no greenlet, tzdata is new."""
    assert _closure("tracer-evaldb", extras=extras) == expected
