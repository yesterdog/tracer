# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Gate condition C12(b): ``jsonschema`` is a hard import of the tests. It is pinned in the root
``test`` dependency group, which the ``dev`` group includes, so a missing package is a broken
environment and must fail loudly, never skip. Three tests used ``pytest.importorskip("jsonschema")``
(test_flags_document.py twice and test_recorded_scoring.py once) and now import it at the top of
the module: a named tightening (spec-change.diff). Contract: docs/phase3/api-surface-batch-j.md
section 4.2.
"""

from __future__ import annotations

import ast
import re
import tomllib
from importlib import metadata
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
PINNED = re.compile(r"jsonschema==(\d+(?:\.\d+)+)")
CONSUMERS = ("test_flags_document.py", "test_recorded_scoring.py")


def _groups() -> dict[str, list[object]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {k: list(v) for k, v in data["dependency-groups"].items()}


def _closure(groups: dict[str, list[object]], name: str) -> list[str]:
    found: list[str] = []
    for item in groups[name]:
        if isinstance(item, dict):
            found += _closure(groups, str(item["include-group"]))
        else:
            found.append(str(item))
    return found


def skips_on_missing_jsonschema(source: str) -> list[str]:
    """Where the source could silently go on without ``jsonschema`` (empty: it cannot)."""
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "importorskip":
                found.append(f"line {node.lineno}: importorskip")
        if isinstance(node, ast.Try):
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            catches = any(
                h.type is not None and re.search(r"ImportError", ast.unparse(h.type))
                for h in node.handlers
            ) or any(
                h.type is not None and "ModuleNotFoundError" in ast.unparse(h.type)
                for h in node.handlers
            )
            if re.search(r"\bjsonschema\b", body) and catches:
                found.append(f"line {node.lineno}: try/except ImportError around jsonschema")
    return found


def top_level_imports_jsonschema(source: str) -> bool:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import) and any(a.name == "jsonschema" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "jsonschema":
            return True
    return False


def test_jsonschema_is_pinned_in_the_root_dev_dependency_group() -> None:
    groups = _groups()
    dev = _closure(groups, "dev")
    pins = [d for d in dev if d.startswith("jsonschema")]
    assert len(pins) == 1, pins
    match = PINNED.fullmatch(pins[0].split("#")[0].strip())
    assert match, f"jsonschema must be pinned with == and no extra: {pins[0]!r}"
    assert "[" not in pins[0], "no extra: its extras are not audited"
    assert any(isinstance(i, dict) and i.get("include-group") == "test" for i in groups["dev"])
    assert [d for d in groups["test"] if str(d).startswith("jsonschema")] == pins
    # the lock and the environment agree with the pin
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "jsonschema"]
    assert locked == [match.group(1)]
    assert metadata.version("jsonschema") == match.group(1)
    # and the runtime packages do not depend on it (a test dependency only)
    for pyproject in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        assert "jsonschema" not in pyproject.read_text(encoding="utf-8"), pyproject.parent.name


def test_no_test_skips_or_guards_on_a_missing_jsonschema() -> None:
    offenders = {}
    for path in sorted((ROOT / "packages").glob("*/tests/*.py")):
        found = skips_on_missing_jsonschema(path.read_text("utf-8"))
        if found:
            offenders[str(path.relative_to(ROOT))] = found
    assert offenders == {}, offenders


def test_the_modules_that_validate_with_jsonschema_import_it_at_the_top() -> None:
    tests = ROOT / "packages" / "evalcore" / "tests"
    for name in CONSUMERS:
        source = (tests / name).read_text("utf-8")
        assert top_level_imports_jsonschema(source), name
        # no function imports it again (a local import would hide a skip)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Import | ast.ImportFrom):
                        module = getattr(inner, "module", None) or ""
                        names = [a.name for a in inner.names]
                        assert "jsonschema" not in [module.split(".")[0], *names], (name, node.name)
    # the modules still use it (the import is not dead)
    for name in CONSUMERS:
        assert "jsonschema." in (tests / name).read_text("utf-8"), name


def test_jsonschema_guard_check_has_teeth() -> None:
    assert skips_on_missing_jsonschema('import pytest\njs = pytest.importorskip("jsonschema")\n')
    assert skips_on_missing_jsonschema('from pytest import importorskip\nimportorskip("x")\n')
    assert skips_on_missing_jsonschema(
        "try:\n    import jsonschema\nexcept ImportError:\n    jsonschema = None\n"
    )
    assert skips_on_missing_jsonschema(
        "try:\n    import jsonschema\nexcept ModuleNotFoundError:\n    pass\n"
    )
    assert skips_on_missing_jsonschema(
        "try:\n    from jsonschema import Draft202012Validator\nexcept (ImportError, ValueError):\n"
        "    pass\n"
    )
    assert skips_on_missing_jsonschema("def f():\n    pytest.importorskip('jsonschema')\n")
    # a plain import, or a try around something else, is fine
    assert skips_on_missing_jsonschema("import jsonschema\n") == []
    assert (
        skips_on_missing_jsonschema("try:\n    import json\nexcept ImportError:\n    pass\n") == []
    )
    assert (
        skips_on_missing_jsonschema(
            "try:\n    jsonschema.validate(1, {})\nexcept ValueError:\n    pass\n"
        )
        == []
    )
    assert top_level_imports_jsonschema("import jsonschema  # type: ignore\n")
    assert top_level_imports_jsonschema("from jsonschema import Draft202012Validator\n")
    assert not top_level_imports_jsonschema("def f():\n    import jsonschema\n")
    assert not top_level_imports_jsonschema("x = 'import jsonschema'\n")
