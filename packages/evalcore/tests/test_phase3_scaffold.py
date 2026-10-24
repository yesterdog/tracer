# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-01: scaffold of the three Phase 3 packages (contract docs/phase3/api-surface-batch-a.md
sections 1 and 2).

``tracer-evalcore`` (``packages/evalcore``), ``tracer-evalproviders`` (``packages/evalproviders``)
and ``tracer-cli`` (``packages/cli``). These tests read repository files, build the wheels and
start fresh interpreters; they open no socket. The per-package import tests of the other two
packages are in their own test directories (``test_evalproviders_scaffold.py``,
``test_cli_scaffold.py``).
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata as md
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass, field
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
PACKAGES = ROOT / "packages"
EVALSCHEMA = PACKAGES / "evalschema"
EVALSCHEMA_SRC = EVALSCHEMA / "src" / "tracer_evalschema"
POLICY = ROOT / "docs" / "security" / "license-policy.toml"
BASELINE = ROOT / "docs" / "phase3" / "evidence" / "evalschema-baseline.json"
BASELINE_PHASE2 = ROOT / "docs" / "phase2" / "evidence" / "evalschema-baseline.json"
BASELINE_TOOL = PACKAGES / "evaldb" / "tests" / "evalschema_baseline.py"
#: The Phase 2 completion commit, the Phase 3 baseline (docs/phase3/SCOPE.md Decisions D4).
BASELINE_COMMIT = "e942b3bd825c92a668a3a1c8061211b76a4d661d"
#: sha256 of the normaliser fixture as IL staged it; P3-01 moves it unchanged.
NORMALISER_SHA256 = "cbdd56b19687761ed4d31c663f97706f5ca7c46b3ad5244e79d38969ee34900c"
STAGED_NORMALISER = ROOT / "docs" / "phase3" / "evidence" / "staging" / "normaliser_mr.json"

SPDX_HEAD = [
    "# SPDX-License-Identifier: Apache-2.0",
    "# Copyright 2026 The Tracer contributors",
]
#: The Python files and wheel members that must never ship.
FORBIDDEN_PARTS = {"tests", "test", "fixtures", "__pycache__"}

CORE_CLOSURE = {
    "annotated-types",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "typing-extensions",
    "typing-inspection",
}
HTTPX_CLOSURE = {"anyio", "certifi", "h11", "httpcore", "httpx", "idna"}
EVALDB_BASE = {"alembic", "mako", "markupsafe", "sqlalchemy"}


@dataclass(frozen=True)
class Pkg:
    key: str  # the directory under packages/ and the key of the licence policy table
    dist: str
    imp: str
    requires: tuple[str, ...]
    lock_closure: frozenset[str]  # third-party and workspace packages reachable in uv.lock
    scripts: dict[str, str] = field(default_factory=dict)

    @property
    def directory(self) -> Path:
        return PACKAGES / self.key

    @property
    def src(self) -> Path:
        return self.directory / "src" / self.imp


EVALCORE = Pkg(
    "evalcore",
    "tracer-evalcore",
    "tracer_evalcore",
    ("tracer-evalschema==0.0.1",),
    frozenset(CORE_CLOSURE | {"tracer-evalschema"}),
)
EVALPROVIDERS = Pkg(
    "evalproviders",
    "tracer-evalproviders",
    "tracer_evalproviders",
    ("tracer-evalschema==0.0.1", "tracer-evalcore==0.0.1", "httpx>=0.28.1,<1"),
    frozenset(CORE_CLOSURE | HTTPX_CLOSURE | {"tracer-evalschema", "tracer-evalcore"}),
)
CLI = Pkg(
    "cli",
    "tracer-cli",
    "tracer_cli",
    (
        "tracer-evalschema==0.0.1",
        "tracer-evalcore==0.0.1",
        "tracer-evalproviders==0.0.1",
        "tracer-evaldb==0.0.1",
    ),
    frozenset(
        CORE_CLOSURE
        | HTTPX_CLOSURE
        | EVALDB_BASE
        | {"tracer-evalschema", "tracer-evalcore", "tracer-evalproviders", "tracer-evaldb"}
    ),
    {"tracer": "tracer_cli.cli:main"},
)
ALL = (EVALCORE, EVALPROVIDERS, CLI)

#: Third-party top-level modules the evalcore source may import (nothing else).
CORE_ALLOWED_IMPORTS = {"tracer_evalschema", "tracer_evalcore"}
#: Third-party top-level modules that may appear in sys.modules after importing every module.
CORE_ALLOWED_LOADED = CORE_ALLOWED_IMPORTS | {
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "typing_extensions",
    "typing_inspection",
    "rfc8785",
    "cython_runtime",
}
FORBIDDEN_LOADED = {
    "httpx",
    "httpcore",
    "h11",
    "anyio",
    "requests",
    "urllib3",
    "aiohttp",
    "websockets",
    "sarvamai",
    "sqlalchemy",
    "alembic",
    "pg8000",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "pytest",
    "jsonschema",
    "hypothesis",
    "yaml",
}
#: Imported names that give the pure core a way to open a connection (SCOPE P3-01).
NETWORK_IMPORTS = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "httpx",
    "httpcore",
    "h11",
    "requests",
    "urllib3",
    "aiohttp",
    "websockets",
    "ftplib",
    "smtplib",
    "imaplib",
    "poplib",
    "nntplib",
    "telnetlib",
    "xmlrpc",
    "socketserver",
    "asyncio",
}
#: Modules that must not be loaded because evalcore was imported. ``urllib.parse`` is string
#: handling and not listed (``pathlib`` imports it); ``urllib.request`` is.
NETWORK_LOADED = {
    "socket",
    "ssl",
    "http",
    "httpx",
    "httpcore",
    "urllib.request",
    "urllib.error",
    "ftplib",
    "smtplib",
    "imaplib",
    "poplib",
    "socketserver",
    "xmlrpc",
    "asyncio",
}


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _req_key(text: str) -> tuple[str, str, SpecifierSet]:
    """Parsed form of a requirement: wheel metadata reorders ``>=0.28.1,<1`` to ``<1,>=0.28.1``."""
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
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


_WALK = """
import importlib, json, pkgutil, sys
before = {n for n in sys.modules}
for base in __BASES__:
    pkg = importlib.import_module(base)
    for m in pkgutil.walk_packages(pkg.__path__, base + "."):
        if m.name.rsplit(".", 1)[-1] == "__main__":
            continue
        importlib.import_module(m.name)
print(json.dumps(sorted(n for n in sys.modules if n not in before)))
"""


def _modules_loaded_by(*bases: str) -> set[str]:
    """Names added to ``sys.modules`` (dotted) by importing every module of ``bases``."""
    return set(json.loads(_run_isolated(_WALK.replace("__BASES__", repr(list(bases))))))


def _top_levels(names: set[str]) -> set[str]:
    tops = {n.split(".")[0] for n in names}
    return {n for n in tops if n not in sys.stdlib_module_names and not n.startswith("_")}


def _lock_packages() -> dict[str, dict[str, Any]]:
    lock = _toml(ROOT / "uv.lock")
    return {canonicalize_name(p["name"]): p for p in lock["package"]}


def _closure(root: str) -> set[str]:
    packages = _lock_packages()
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for dep in packages[name].get("dependencies", []):
            todo.append(canonicalize_name(dep["name"]))
    seen.discard(root)
    return seen


def _build_wheel(pkg: Pkg, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("*.whl"))
    if existing:
        return existing[0]
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(out)],
        cwd=pkg.directory,
        check=True,
        capture_output=True,
    )
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


# ---------------------------------------------------------------- the SCOPE tests (P3-01)


def test_evalcore_runtime_imports_whitelist() -> None:
    files = _python_files(EVALCORE.src)
    assert files, f"{EVALCORE.src} holds no module (the package is not scaffolded)"
    offences = []
    for path in files:
        for line, top in _imports_of(path):
            if top not in sys.stdlib_module_names and top not in CORE_ALLOWED_IMPORTS:
                offences.append(f"{path.relative_to(EVALCORE.src)}:{line}: import of {top}")
    assert not offences, offences
    loaded = _top_levels(_modules_loaded_by("tracer_evalcore"))
    # the delta of one fresh interpreter: new top-level names only, stdlib and underscore skipped
    assert "tracer_evalcore" in loaded
    assert loaded <= CORE_ALLOWED_LOADED, sorted(loaded - CORE_ALLOWED_LOADED)
    assert not (loaded & FORBIDDEN_LOADED), sorted(loaded & FORBIDDEN_LOADED)


def test_evalcore_imports_no_network_module() -> None:
    offences = []
    for path in _python_files(EVALCORE.src):
        for line, top in _imports_of(path):
            if top in NETWORK_IMPORTS:
                offences.append(f"{path.relative_to(EVALCORE.src)}:{line}: import of {top}")
    assert not offences, offences
    # nothing network-capable is loaded by evalcore itself (the evalschema modules it builds on
    # are measured separately and subtracted)
    base = _modules_loaded_by("tracer_evalschema")
    both = _modules_loaded_by("tracer_evalschema", "tracer_evalcore")
    added = both - base
    assert any(n.startswith("tracer_evalcore") for n in added)
    hit = {n for n in added if n in NETWORK_LOADED or n.split(".")[0] in {"socket", "ssl", "http"}}
    assert not hit, sorted(hit)


def test_evalschema_does_not_import_phase3_packages() -> None:
    banned = {
        "tracer_evalcore",
        "tracer_evalproviders",
        "tracer_cli",
        "tracer_evaldb",
        "httpx",
        "httpcore",
        "sqlalchemy",
        "alembic",
    }
    offences = []
    for path in _python_files(EVALSCHEMA_SRC):
        for line, top in _imports_of(path):
            if top in banned:
                offences.append(f"{path.relative_to(EVALSCHEMA_SRC)}:{line}: import of {top}")
    assert not offences, offences
    loaded = {n.split(".")[0] for n in _modules_loaded_by("tracer_evalschema")}
    assert not (loaded & banned), sorted(loaded & banned)
    # the schema package declares nothing new either
    project = _toml(EVALSCHEMA / "pyproject.toml")["project"]
    assert project["dependencies"] == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    assert not project.get("optional-dependencies")
    assert not any(
        canonicalize_name(d["name"]) in {"httpx", "tracer-evalcore"}
        for d in _lock_packages()["tracer-evalschema"].get("dependencies", [])
    )


@pytest.mark.parametrize("pkg", ALL, ids=lambda p: p.key)
def test_phase3_requires_dist_bounded_ranges(pkg: Pkg, tmp_path: Path) -> None:
    project = _toml(pkg.directory / "pyproject.toml")["project"]
    assert project["name"] == pkg.dist
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "Apache-2.0"
    assert not project.get("optional-dependencies"), "no extras in Phase 3"
    assert sorted(_req_key(d) for d in project["dependencies"]) == sorted(
        _req_key(d) for d in pkg.requires
    )
    for text in project["dependencies"]:
        req = Requirement(text)
        name = canonicalize_name(req.name)
        if name.startswith("tracer-"):
            # a workspace package: the exact version pin of the data and the code that go together
            assert req.specifier == SpecifierSet("==0.0.1"), text
            continue
        # every third-party range is >=X,<next major of X (SCOPE Decisions D3), tested at X
        ops = sorted((s.operator, s.version) for s in req.specifier)
        assert [o for o, _ in ops] == ["<", ">="], f"{text}: not a bounded range"
        lower, upper = dict(ops)[">="], dict(ops)["<"]
        assert re.fullmatch(r"\d+(\.\d+)*", lower), text
        assert upper == str(int(lower.split(".")[0]) + 1), f"{text}: upper bound is not next major"
    # the licence policy lists the same requirements (compared as parsed requirements)
    policy = _toml(POLICY)["runtime"][pkg.key]
    assert sorted(_req_key(d) for d in policy["requires_dist"]) == sorted(
        _req_key(d) for d in pkg.requires
    )
    # the built wheel declares the same, parsed (the build backend reorders specifiers)
    with zipfile.ZipFile(_build_wheel(pkg, tmp_path)) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = BytesParser().parsebytes(zf.read(meta_name))
    assert meta["Name"] == pkg.dist
    assert meta["Version"] == "0.0.1"
    assert meta["Requires-Python"] == ">=3.11"
    assert not meta.get_all("Provides-Extra")
    assert sorted(_req_key(r) for r in meta.get_all("Requires-Dist") or []) == sorted(
        _req_key(d) for d in pkg.requires
    )
    # the only third-party range of Phase 3 is httpx, and it is the one the licence review read
    httpx = [
        d for d in project["dependencies"] if canonicalize_name(Requirement(d).name) == "httpx"
    ]
    assert httpx == (["httpx>=0.28.1,<1"] if pkg is EVALPROVIDERS else [])


@pytest.mark.parametrize("pkg", ALL, ids=lambda p: p.key)
def test_phase3_wheels_contain_no_tests(pkg: Pkg, tmp_path: Path) -> None:
    with zipfile.ZipFile(_build_wheel(pkg, tmp_path)) as zf:
        names = zf.namelist()
        dist_info = {n.split("/")[0] for n in names if ".dist-info/" in n}
        assert len(dist_info) == 1
        info = next(iter(dist_info))
        entry_points = (
            zf.read(f"{info}/entry_points.txt").decode()
            if f"{info}/entry_points.txt" in names
            else ""
        )
    assert {n.split("/")[0] for n in names} == {pkg.imp, info}, sorted(
        {n.split("/")[0] for n in names}
    )
    required = {f"{pkg.imp}/__init__.py", f"{pkg.imp}/py.typed"}
    assert required <= set(names), sorted(required - set(names))
    for name in names:
        parts = name.split("/")
        base = parts[-1]
        assert not (FORBIDDEN_PARTS & set(parts)), name
        assert not base.startswith("test_"), name
        assert base != "conftest.py", name
        assert not base.endswith(".pyc"), name
        assert "baseline" not in base, name
    licence_members = {n for n in names if n.startswith(f"{info}/") and "LICENSE" in n}
    notice_members = {n for n in names if n.startswith(f"{info}/") and "NOTICE" in n}
    assert licence_members
    assert notice_members
    # the console script is declared by the cli wheel only
    declared: dict[str, dict[str, str]] = {}
    section = ""
    for raw in entry_points.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            declared[section] = {}
        else:
            key, _, value = line.partition("=")
            declared[section][key.strip()] = value.strip()
    assert declared == ({"console_scripts": pkg.scripts} if pkg.scripts else {})
    if pkg is EVALCORE:
        modules = {
            "errors.py",
            "audio/__init__.py",
            "audio/seconds.py",
            "audio/chunker.py",
            "importers/__init__.py",
            "importers/wav.py",
            "importers/voicera.py",
        }
        assert {f"{pkg.imp}/{m}" for m in modules} <= set(names)
    if pkg is CLI:
        assert f"{pkg.imp}/cli.py" in names


def test_evalschema_unchanged_since_phase3_baseline(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("evalschema_baseline", BASELINE_TOOL)
    assert spec is not None
    assert spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(recorded) == {
        "commit",
        "schema_lock_sha256",
        "source_tree_sha256",
        "pyproject_sha256",
    }
    assert recorded["commit"] == BASELINE_COMMIT
    current = tool.current_values()
    for key, value in current.items():
        assert re.fullmatch(r"[0-9a-f]{64}", recorded[key]), key
        assert recorded[key] == value, f"tracer-evalschema changed since the baseline: {key}"
    # Phase 3 starts from the tree that Phase 2 recorded: the values did not move in between
    phase2 = json.loads(BASELINE_PHASE2.read_text(encoding="utf-8"))
    for key in ("schema_lock_sha256", "source_tree_sha256", "pyproject_sha256"):
        assert recorded[key] == phase2[key], key
    # the hash follows content: a copy with one byte more hashes differently
    copy = tmp_path / "probe"
    shutil.copytree(EVALSCHEMA_SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert tool.source_tree_sha256(copy) == recorded["source_tree_sha256"]
    target = copy / "ids.py"
    target.write_bytes(target.read_bytes() + b"\n")
    assert tool.source_tree_sha256(copy) != recorded["source_tree_sha256"]


# ------------------------------------------------- added by qa (not in the SCOPE table)


def test_phase3_scaffold_files_and_repo_config() -> None:
    licence = (EVALSCHEMA / "LICENSE").read_bytes()
    notice = (EVALSCHEMA / "NOTICE").read_bytes()
    for pkg in ALL:
        for name in ("LICENSE", "NOTICE", "README.md", "pyproject.toml"):
            assert (pkg.directory / name).is_file(), f"{pkg.key}/{name}"
        assert (pkg.directory / "LICENSE").read_bytes() == licence, pkg.key
        assert (pkg.directory / "NOTICE").read_bytes() == notice, pkg.key
        assert (pkg.directory / "README.md").read_text(encoding="utf-8").strip(), pkg.key
        assert (pkg.src / "py.typed").is_file(), pkg.key
        assert (pkg.src / "__init__.py").is_file(), pkg.key
        for path in _python_files(pkg.directory / "src"):
            assert path.read_text(encoding="utf-8").splitlines()[:2] == SPDX_HEAD, path
        data = _toml(pkg.directory / "pyproject.toml")
        assert data["build-system"] == {
            "requires": ["hatchling==1.32.4"],
            "build-backend": "hatchling.build",
        }
        project = data["project"]
        assert project["version"] == "0.0.1"
        assert project["license-files"] == ["LICENSE", "NOTICE"]
        assert project["readme"] == "README.md"
        assert "Typing :: Typed" in project["classifiers"]
        for minor in ("3.11", "3.12", "3.13", "3.14"):
            assert f"Programming Language :: Python :: {minor}" in project["classifiers"]
        assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [f"src/{pkg.imp}"]
        scripts = project.get("scripts", {})
        assert scripts == pkg.scripts, pkg.key

    root = _toml(ROOT / "pyproject.toml")
    assert "packages/*" in root["tool"]["uv"]["workspace"]["members"]
    dev = root["dependency-groups"]["dev"]
    for pkg in ALL:
        assert pkg.dist in dev, (
            f"{pkg.dist} is missing from the dev group (certifi must be reachable)"
        )
        assert root["tool"]["uv"]["sources"][pkg.dist] == {"workspace": True}
    assert "tracer-evaldb[postgres]" in dev
    dirs = [f"packages/{p.key}/{kind}" for p in ALL for kind in ("src", "tests")]
    assert set(dirs) <= set(root["tool"]["ruff"]["src"])
    mypy = root["tool"]["mypy"]
    assert set(dirs) <= set(mypy["files"])
    sources = {f"packages/{p.key}/src" for p in (EVALCORE, EVALPROVIDERS, CLI)}
    assert sources <= set(mypy["mypy_path"].split(":"))
    ini = root["tool"]["pytest"]["ini_options"]
    assert ini["testpaths"] == [
        "packages/evalschema/tests",
        "packages/evaldb/tests",
        "packages/evalcore/tests",
        "packages/evalproviders/tests",
        "packages/cli/tests",
    ]
    assert "-m 'not private_fixtures and not local_only and not postgres'" in ini["addopts"]
    lock = _lock_packages()
    for pkg in ALL:
        assert lock[pkg.dist]["source"] == {"editable": f"packages/{pkg.key}"}, pkg.key
    assert tuple(int(x) for x in lock["httpx"]["version"].split(".")[:2]) >= (0, 28)


@pytest.mark.parametrize("pkg", ALL, ids=lambda p: p.key)
def test_phase3_locked_closures_are_the_reviewed_ones(pkg: Pkg) -> None:
    """docs/security/P3-02-licenses.md section 5: sniffio is not required, no extra closure."""
    assert _closure(pkg.dist) == set(pkg.lock_closure)
    assert "sniffio" not in _lock_packages()
    assert "greenlet" not in _lock_packages()
    assert "requests" not in _lock_packages()
    assert "sarvamai" not in _lock_packages()


def test_normaliser_fixture_moved_unchanged() -> None:
    final = EVALCORE.directory / "tests" / "fixtures" / "normaliser_mr.json"
    assert final.is_file(), "P3-01 moves the staged fixture to its final path"
    assert hashlib.sha256(final.read_bytes()).hexdigest() == NORMALISER_SHA256
    assert not STAGED_NORMALISER.exists(), "the staging copy is deleted once moved"
    json.loads(final.read_text(encoding="utf-8"))


def test_evalcore_version_is_the_distribution_version() -> None:
    mod = importlib.import_module("tracer_evalcore")
    assert mod.__version__ == "0.0.1"
    assert md.version("tracer-evalcore") == mod.__version__
    for name in (
        "tracer_evalcore.errors",
        "tracer_evalcore.audio.seconds",
        "tracer_evalcore.audio.chunker",
        "tracer_evalcore.importers.wav",
        "tracer_evalcore.importers.voicera",
    ):
        importlib.import_module(name)
    errors = importlib.import_module("tracer_evalcore.errors")
    assert issubclass(errors.EvalcoreError, Exception)
    assert issubclass(errors.InputRefusedError, errors.EvalcoreError)
    assert issubclass(errors.InputRefusedError, ValueError)
