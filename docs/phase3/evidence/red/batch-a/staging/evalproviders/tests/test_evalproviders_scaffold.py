# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-01: import rules of tracer-evalproviders (contract docs/phase3/api-surface-batch-a.md
section 2.2). The package may import the standard library, ``httpx``, ``tracer_evalschema``,
``tracer_evalcore`` and itself; nothing else, so the HTTP stack is httpx and nothing but httpx,
and the provider adapters never reach the store or the command line.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
SRC = ROOT / "packages" / "evalproviders" / "src" / "tracer_evalproviders"

ALLOWED_IMPORTS = {"httpx", "tracer_evalschema", "tracer_evalcore", "tracer_evalproviders"}
#: Third-party top-level modules that may appear in sys.modules after importing every module:
#: httpx and its closure (P3-02 section 2), the evalschema closure, the workspace packages.
ALLOWED_LOADED = ALLOWED_IMPORTS | {
    "httpcore",
    "h11",
    "anyio",
    "idna",
    "certifi",
    "sniffio",
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "typing_extensions",
    "typing_inspection",
    "rfc8785",
    "cython_runtime",
}
FORBIDDEN_LOADED = {
    "requests",
    "urllib3",
    "aiohttp",
    "websockets",
    "sarvamai",
    "trio",
    "sqlalchemy",
    "alembic",
    "pg8000",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "tracer_evaldb",
    "tracer_cli",
    "pytest",
    "jsonschema",
    "hypothesis",
    "yaml",
}


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


def _loaded_top_levels() -> set[str]:
    code = """
import importlib, json, pkgutil, sys
before = {n.split(".")[0] for n in sys.modules}
pkg = importlib.import_module("tracer_evalproviders")
for m in pkgutil.walk_packages(pkg.__path__, "tracer_evalproviders."):
    if m.name.rsplit(".", 1)[-1] != "__main__":
        importlib.import_module(m.name)
after = {n.split(".")[0] for n in sys.modules}
print(json.dumps(sorted(after - before)))
"""
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    loaded = set(json.loads(done.stdout))
    return {n for n in loaded if n not in sys.stdlib_module_names and not n.startswith("_")}


def test_evalproviders_runtime_imports_whitelist() -> None:
    files = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, f"{SRC} holds no module (the package is not scaffolded)"
    offences = []
    for path in files:
        for line, top in _imports_of(path):
            if top not in sys.stdlib_module_names and top not in ALLOWED_IMPORTS:
                offences.append(f"{path.relative_to(SRC)}:{line}: import of {top}")
    assert not offences, offences
    loaded = _loaded_top_levels()
    assert "tracer_evalproviders" in loaded
    assert loaded <= ALLOWED_LOADED, sorted(loaded - ALLOWED_LOADED)
    assert not (loaded & FORBIDDEN_LOADED), sorted(loaded & FORBIDDEN_LOADED)
