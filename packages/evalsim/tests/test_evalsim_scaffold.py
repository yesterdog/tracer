# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-01: scaffold of tracer-evalsim (contract docs/phase4/api-surface-batch-0b.md sections 1
to 3), in the style of ``packages/evalcore/tests/test_phase3_scaffold.py``.

These tests read repository files, build the wheel and start fresh interpreters; they open no
socket. The package imports ``websockets`` and (optional extra) ``aiortc`` and nothing else
beyond the standard library and the two workspace packages below it.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata as md
import json
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
from packaging.version import Version


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
PACKAGES = ROOT / "packages"
EVALSIM = PACKAGES / "evalsim"
SRC = EVALSIM / "src" / "tracer_evalsim"
EVALSCHEMA = PACKAGES / "evalschema"
POLICY = ROOT / "docs" / "security" / "license-policy.toml"

SPDX_HEAD = [
    "# SPDX-License-Identifier: Apache-2.0",
    "# Copyright 2026 The Tracer contributors",
]
FORBIDDEN_PARTS = {"tests", "test", "fixtures", "__pycache__"}

#: The declared ranges (SCOPE Decisions D3, amended E-b: websockets and aiortc only).
DEPENDENCIES = (
    "tracer-evalschema==0.0.1",
    "tracer-evalcore==0.0.1",
    "websockets>=15.0,<18",
)
EXTRA_NAME = "webrtc"
EXTRA_DEPENDENCIES = ("aiortc>=1.15.0,<2",)

CORE_CLOSURE = {
    "annotated-types",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "typing-extensions",
    "typing-inspection",
}
WEBRTC_CLOSURE = {
    "aioice",
    "aiortc",
    "av",
    "cffi",
    "cryptography",
    "dnspython",
    "google-crc32c",
    "ifaddr",
    "pycparser",
    "pyee",
    "pylibsrtp",
    "pyopenssl",
}
#: Never in a tracer wheel, never in uv.lock (ADR 0026 rules 1 and 3; SCOPE gate item 9).
NEVER_LOCKED = (
    "pipecat-ai",
    "protobuf",
    "num2words",
    "soxr",
    "soundfile",
    "onnxruntime",
    "numba",
    "llvmlite",
    "sentencex",
    "loudness",
    "sarvamai",
    "opencv-python-headless",
    "tracer-samplebot",
)

ALLOWED_IMPORTS = {
    "websockets",
    "aiortc",
    "tracer_evalschema",
    "tracer_evalcore",
    "tracer_evalsim",
}
#: Top-level modules that may appear in sys.modules after importing every module of the package.
ALLOWED_LOADED = {
    "tracer_evalschema",
    "tracer_evalcore",
    "tracer_evalsim",
    "websockets",
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "typing_extensions",
    "typing_inspection",
    "rfc8785",
    "cython_runtime",
}
#: The extra is optional: importing the package without using WebRTC loads none of its closure.
EXTRA_MODULES = {
    "aiortc",
    "av",
    "aioice",
    "pylibsrtp",
    "pyee",
    "OpenSSL",
    "cryptography",
    "cffi",
    "google_crc32c",
    "ifaddr",
    "dns",
}
FORBIDDEN_LOADED = {
    "httpx",
    "httpcore",
    "h11",
    "anyio",
    "requests",
    "urllib3",
    "aiohttp",
    "sarvamai",
    "sqlalchemy",
    "alembic",
    "pg8000",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "tracer_evalproviders",
    "tracer_evaldb",
    "tracer_cli",
    "pytest",
    "jsonschema",
    "hypothesis",
    "yaml",
    "google",
    "pipecat",
}
#: Only tracer-evalsim may import these (ADR 0026 rule 2, invariant I2).
SIM_ONLY_IMPORTS = {"websockets", "aiortc", "av", "aioice", "pylibsrtp", "pyee"}
#: Imported by no tracer package at all (ADR 0026 rules 1 and 3).
NEVER_IMPORTED = {"pipecat", "google", "protobuf"}
OTHER_PACKAGES = ("evalschema", "evaldb", "evalcore", "evalproviders", "cli")


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _req_key(text: str) -> tuple[str, str, SpecifierSet]:
    """Parsed form of a requirement: wheel metadata reorders ``>=15.0,<18`` to ``<18,>=15.0``."""
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
before = {n.split(".")[0] for n in sys.modules}
pkg = importlib.import_module("tracer_evalsim")
for m in pkgutil.walk_packages(pkg.__path__, "tracer_evalsim."):
    if m.name.rsplit(".", 1)[-1] != "__main__":
        importlib.import_module(m.name)
after = {n.split(".")[0] for n in sys.modules}
print(json.dumps(sorted(after - before)))
"""


def _loaded_top_levels() -> set[str]:
    loaded = set(json.loads(_run_isolated(_WALK)))
    return {n for n in loaded if n not in sys.stdlib_module_names and not n.startswith("_")}


def _lock_packages() -> dict[str, dict[str, Any]]:
    lock = _toml(ROOT / "uv.lock")
    return {canonicalize_name(p["name"]): p for p in lock["package"]}


def _build_wheel(out: Path) -> Path:
    assert (EVALSIM / "pyproject.toml").is_file(), "packages/evalsim is not scaffolded"
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("*.whl"))
    if existing:
        return existing[0]
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(out)],
        cwd=EVALSIM,
        check=True,
        capture_output=True,
    )
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


# ---------------------------------------------------------------- the SCOPE tests (P4-01)


def test_evalsim_runtime_imports_whitelist() -> None:
    files = _python_files(SRC)
    assert files, f"{SRC} holds no module (the package is not scaffolded)"
    offences = []
    for path in files:
        for line, top in _imports_of(path):
            if top not in sys.stdlib_module_names and top not in ALLOWED_IMPORTS:
                offences.append(f"{path.relative_to(SRC)}:{line}: import of {top}")
    assert not offences, offences

    # a fresh interpreter: the package loads websockets-free stdlib-plus-workspace code only, and
    # nothing of the optional [webrtc] closure (an import of aiortc is lazy, inside a function)
    loaded = _loaded_top_levels()
    assert "tracer_evalsim" in loaded
    assert loaded <= ALLOWED_LOADED, sorted(loaded - ALLOWED_LOADED)
    assert not (loaded & FORBIDDEN_LOADED), sorted(loaded & FORBIDDEN_LOADED)
    assert not (loaded & EXTRA_MODULES), sorted(loaded & EXTRA_MODULES)

    # evalsim is the only importer of websockets and aiortc (and of their closure); nobody
    # imports Pipecat or Google protobuf
    offences = []
    for key in OTHER_PACKAGES:
        for path in _python_files(PACKAGES / key / "src"):
            for line, top in _imports_of(path):
                if top in SIM_ONLY_IMPORTS or top in NEVER_IMPORTED:
                    offences.append(f"{key}/{path.name}:{line}: import of {top}")
    for path in files:
        for line, top in _imports_of(path):
            if top in NEVER_IMPORTED:
                offences.append(f"evalsim/{path.name}:{line}: import of {top}")
    assert not offences, offences

    # the websockets API the dialects and the fake agents are written against exists in the
    # installed (and, on the lower-bound leg, the lowest) version
    out = _run_isolated(
        "import websockets.asyncio.client as c, websockets.asyncio.server as s;"
        "print(callable(c.connect), callable(s.serve))"
    )
    assert out.split() == ["True", "True"]


def test_phase4_requires_dist_bounded_ranges(tmp_path: Path) -> None:
    project = _toml(EVALSIM / "pyproject.toml")["project"]
    assert project["name"] == "tracer-evalsim"
    assert project["version"] == "0.0.1"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "Apache-2.0"
    assert sorted(_req_key(d) for d in project["dependencies"]) == sorted(
        _req_key(d) for d in DEPENDENCIES
    )
    assert project["optional-dependencies"] == {EXTRA_NAME: list(EXTRA_DEPENDENCIES)}
    # no protobuf, Pipecat or HTTP stack anywhere in the declared metadata (E-b, ADR 0026 rule 3)
    declared = " ".join([*project["dependencies"], *project["optional-dependencies"][EXTRA_NAME]])
    for banned in ("protobuf", "pipecat", "httpx", "requests", "aiohttp", "sarvamai"):
        assert banned not in declared.lower(), banned
    for text in [*project["dependencies"], *EXTRA_DEPENDENCIES]:
        req = Requirement(text)
        name = canonicalize_name(req.name)
        if name.startswith("tracer-"):
            # a workspace package: the exact version pin of the data and the code that go together
            assert req.specifier == SpecifierSet("==0.0.1"), text
            continue
        # a third-party range is exactly >=lower,<upper with the upper above the lower
        ops = sorted((s.operator, s.version) for s in req.specifier)
        assert [o for o, _ in ops] == ["<", ">="], f"{text}: not a bounded range"
        lower, upper = Version(dict(ops)[">="]), Version(dict(ops)["<"])
        assert upper > lower, text
        assert upper.release[0] > lower.release[0], f"{text}: the upper bound is not a major"

    # the licence policy lists the same requirements, the extra with its marker
    policy = _toml(POLICY)["runtime"]["evalsim"]
    wanted = [*DEPENDENCIES, f"{EXTRA_DEPENDENCIES[0]}; extra == '{EXTRA_NAME}'"]
    assert sorted(_req_key(d) for d in policy["requires_dist"]) == sorted(
        _req_key(d) for d in wanted
    )

    # the built wheel declares the same, parsed (the build backend reorders specifiers)
    with zipfile.ZipFile(_build_wheel(tmp_path)) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = BytesParser().parsebytes(zf.read(meta_name))
    assert meta["Name"] == "tracer-evalsim"
    assert meta["Version"] == "0.0.1"
    assert meta["Requires-Python"] == ">=3.11"
    assert meta.get_all("Provides-Extra") == [EXTRA_NAME]
    assert sorted(_req_key(r) for r in meta.get_all("Requires-Dist") or []) == sorted(
        _req_key(d) for d in wanted
    )

    # the lower bound is what the lower-bound leg installs; the installed versions satisfy the
    # ranges in every leg (the extra is in the dev group, so it is installed in every test run)
    for text in (DEPENDENCIES[2], EXTRA_DEPENDENCIES[0]):
        req = Requirement(text)
        assert Version(md.version(req.name)) in req.specifier, text


def test_evalsim_wheel_contains_no_tests(tmp_path: Path) -> None:
    with zipfile.ZipFile(_build_wheel(tmp_path)) as zf:
        names = zf.namelist()
    dist_info = {n.split("/")[0] for n in names if ".dist-info/" in n}
    assert len(dist_info) == 1
    info = next(iter(dist_info))
    assert {n.split("/")[0] for n in names} == {"tracer_evalsim", info}, sorted(
        {n.split("/")[0] for n in names}
    )
    required = {"tracer_evalsim/__init__.py", "tracer_evalsim/py.typed", "tracer_evalsim/errors.py"}
    assert required <= set(names), sorted(required - set(names))
    for name in names:
        parts = name.split("/")
        base = parts[-1]
        assert not (FORBIDDEN_PARTS & set(parts)), name
        assert not base.startswith("test_"), name
        assert base != "conftest.py", name
        assert not base.endswith(".pyc"), name
        assert "baseline" not in base, name
        assert "samplebot" not in name.lower(), name
        assert "pipecat" not in name.lower(), name
    assert any(n.startswith(f"{info}/") and "LICENSE" in n for n in names)
    assert any(n.startswith(f"{info}/") and "NOTICE" in n for n in names)
    # no console script: the command line is tracer-cli's
    entry_points = f"{info}/entry_points.txt"
    if entry_points in names:
        with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as zf:
            assert "console_scripts" not in zf.read(entry_points).decode()
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as zf:
        for name in names:
            if name.endswith(".py"):
                head = zf.read(name).decode("utf-8").splitlines()[:2]
                assert head == SPDX_HEAD, name


# ------------------------------------------------- added by qa (not in the SCOPE table)


def test_phase4_scaffold_files_and_repo_config() -> None:
    licence = (EVALSCHEMA / "LICENSE").read_bytes()
    notice = (EVALSCHEMA / "NOTICE").read_bytes()
    for name in ("LICENSE", "NOTICE", "README.md", "pyproject.toml"):
        assert (EVALSIM / name).is_file(), name
    assert (EVALSIM / "LICENSE").read_bytes() == licence
    assert (EVALSIM / "NOTICE").read_bytes() == notice
    readme = (EVALSIM / "README.md").read_text(encoding="utf-8")
    assert readme.strip()
    # the optional extra is stated with its licence caveat (P4-02 section 7, condition 3)
    for needle in ("webrtc", "FFmpeg", "docs/security/P4-02-licenses.md"):
        assert needle in readme, needle
    assert (SRC / "py.typed").is_file()
    assert (SRC / "__init__.py").is_file()
    for path in _python_files(EVALSIM / "src"):
        assert path.read_text(encoding="utf-8").splitlines()[:2] == SPDX_HEAD, path
    data = _toml(EVALSIM / "pyproject.toml")
    assert data["build-system"] == {
        "requires": ["hatchling==1.32.4"],
        "build-backend": "hatchling.build",
    }
    project = data["project"]
    assert project["license-files"] == ["LICENSE", "NOTICE"]
    assert project["readme"] == "README.md"
    assert "scripts" not in project
    assert "Typing :: Typed" in project["classifiers"]
    for minor in ("3.11", "3.12", "3.13", "3.14"):
        assert f"Programming Language :: Python :: {minor}" in project["classifiers"]
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/tracer_evalsim"]
    module = importlib.import_module("tracer_evalsim")
    assert module.__version__ == "0.0.1" == md.version("tracer-evalsim")
    errors = importlib.import_module("tracer_evalsim.errors")
    core_errors = importlib.import_module("tracer_evalcore.errors")
    assert issubclass(errors.EvalsimError, core_errors.EvalcoreError)

    root = _toml(ROOT / "pyproject.toml")
    assert "packages/*" in root["tool"]["uv"]["workspace"]["members"]
    assert "tools/samplebot" not in " ".join(root["tool"]["uv"]["workspace"]["members"])
    dev = root["dependency-groups"]["dev"]
    assert "tracer-evalsim[webrtc]" in dev, "the extra is installed so its closure is scanned"
    assert root["tool"]["uv"]["sources"]["tracer-evalsim"] == {"workspace": True}
    # the compiled packages of the extra are never built from source on a clean machine (P4-02
    # review section 5): wheels exist for 3.11 to 3.15, and a missing one must fail loudly
    assert {"av", "pylibsrtp", "google-crc32c"} <= set(root["tool"]["uv"]["no-build-package"])
    dirs = ["packages/evalsim/src", "packages/evalsim/tests"]
    assert set(dirs) <= set(root["tool"]["ruff"]["src"])
    assert set(dirs) <= set(root["tool"]["mypy"]["files"])
    assert "packages/evalsim/src" in root["tool"]["mypy"]["mypy_path"].split(":")
    assert root["tool"]["pytest"]["ini_options"]["testpaths"] == [
        "packages/evalschema/tests",
        "packages/evaldb/tests",
        "packages/evalcore/tests",
        "packages/evalproviders/tests",
        "packages/cli/tests",
        "packages/evalsim/tests",
    ]
    lock = _lock_packages()
    assert "tracer-evalsim" in lock, "uv.lock does not hold tracer-evalsim"
    assert lock["tracer-evalsim"]["source"] == {"editable": "packages/evalsim"}
    assert lock["tracer-evalsim"]["metadata"]["provides-extras"] == [EXTRA_NAME]


def test_phase4_locked_closures_are_the_reviewed_ones() -> None:
    """P4-02 review sections 2 and 3 (docs/security/P4-02-licenses.md): base and extra closures."""
    lock = _lock_packages()
    assert "tracer-evalsim" in lock, "uv.lock does not hold tracer-evalsim"

    def reach(extras: tuple[str, ...]) -> set[str]:
        seen: set[str] = set()
        todo = ["tracer-evalsim"]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            package = lock[name]
            todo += [canonicalize_name(d["name"]) for d in package.get("dependencies", [])]
            if name == "tracer-evalsim":
                for extra in extras:
                    optional = package.get("optional-dependencies", {}).get(extra, [])
                    todo += [canonicalize_name(d["name"]) for d in optional]
        seen.discard("tracer-evalsim")
        return seen

    base = reach(())
    assert base == CORE_CLOSURE | {"tracer-evalschema", "tracer-evalcore", "websockets"}
    with_extra = reach((EXTRA_NAME,))
    assert with_extra - base == WEBRTC_CLOSURE
    for name in NEVER_LOCKED:
        assert name not in lock, f"{name} must stay out of uv.lock"
    # evalcore, evalproviders and the command line do not reach the new closure
    for dist in ("tracer-evalcore", "tracer-evalproviders", "tracer-cli", "tracer-evaldb"):
        todo = [dist]
        seen: set[str] = set()
        while todo:
            name = todo.pop()
            if name not in seen:
                seen.add(name)
                todo += [canonicalize_name(d["name"]) for d in lock[name].get("dependencies", [])]
        assert "websockets" not in seen, dist
        assert "tracer-evalsim" not in seen, dist


@pytest.mark.parametrize("module", ["aiortc", "websockets"])
def test_phase4_dev_environment_has_the_extra(module: str) -> None:
    """The test environments install ``tracer-evalsim[webrtc]`` (dev group), so a missing
    extra cannot hide a broken lower bound."""
    try:
        installed = md.version(module)
    except md.PackageNotFoundError:
        pytest.fail(f"{module} is not installed: add tracer-evalsim[webrtc] to the dev group")
    assert installed
