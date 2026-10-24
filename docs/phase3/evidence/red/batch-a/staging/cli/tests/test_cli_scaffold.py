# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-01: import rules and the console script of tracer-cli (contract
docs/phase3/api-surface-batch-a.md sections 2.3 and 2.4).

The command line imports the standard library and the four workspace packages; it never imports
httpx, SQLAlchemy or any other third-party package directly (providers and the store are reached
through ``tracer_evalproviders`` and ``tracer_evaldb``).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
SRC = ROOT / "packages" / "cli" / "src" / "tracer_cli"

ALLOWED_IMPORTS = {
    "tracer_evalschema",
    "tracer_evalcore",
    "tracer_evalproviders",
    "tracer_evaldb",
    "tracer_cli",
}
#: Third-party top-level modules that may appear in sys.modules after importing every module:
#: the httpx closure, the evaldb base closure (no Postgres driver) and the evalschema closure.
ALLOWED_LOADED = ALLOWED_IMPORTS | {
    "httpx",
    "httpcore",
    "h11",
    "anyio",
    "idna",
    "certifi",
    "sniffio",
    "sqlalchemy",
    "alembic",
    "mako",
    "markupsafe",
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "typing_extensions",
    "typing_inspection",
    "rfc8785",
    "cython_runtime",
}
FORBIDDEN_LOADED = {
    "pg8000",
    "scramp",
    "dateutil",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "requests",
    "urllib3",
    "aiohttp",
    "websockets",
    "sarvamai",
    "pytest",
    "jsonschema",
    "hypothesis",
    "yaml",
}
DENY_LIST = re.compile(r"(?i)\b(?:accuracy|wer|cer|error rate|correct|recall|precision)\b")


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
pkg = importlib.import_module("tracer_cli")
for m in pkgutil.walk_packages(pkg.__path__, "tracer_cli."):
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


def test_cli_runtime_imports_whitelist() -> None:
    files = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, f"{SRC} holds no module (the package is not scaffolded)"
    offences = []
    for path in files:
        for line, top in _imports_of(path):
            if top not in sys.stdlib_module_names and top not in ALLOWED_IMPORTS:
                offences.append(f"{path.relative_to(SRC)}:{line}: import of {top}")
    assert not offences, offences
    loaded = _loaded_top_levels()
    assert "tracer_cli" in loaded
    assert loaded <= ALLOWED_LOADED, sorted(loaded - ALLOWED_LOADED)
    assert not (loaded & FORBIDDEN_LOADED), sorted(loaded & FORBIDDEN_LOADED)


def test_tracer_console_script_runs_help(capsys: pytest.CaptureFixture[str]) -> None:
    # the installed script, next to the interpreter of this environment
    script = shutil.which("tracer", path=str(Path(sys.executable).parent))
    assert script is not None, "the console script `tracer` is not installed in this environment"
    done = subprocess.run(  # noqa: S603
        [script, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("usage: tracer"), done.stdout[:200]
    assert done.stderr == ""
    assert "Traceback" not in done.stdout
    # Tracer-authored help never claims accuracy (ADR 0023 rule 1)
    assert not DENY_LIST.search(done.stdout), DENY_LIST.findall(done.stdout)

    # the entry point is tracer_cli.cli:main, a function of one optional argument
    cli = importlib.import_module("tracer_cli.cli")
    params = inspect.signature(cli.main).parameters
    assert list(params) == ["argv"]
    assert params["argv"].default is None
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    printed = capsys.readouterr()
    assert printed.out.startswith("usage: tracer")
    assert printed.err == ""
