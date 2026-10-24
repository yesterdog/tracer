# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0008 invariant I16 / SCOPE section 5: the runtime imports of ``tracer_evalschema``.

``test_runtime_imports_whitelist`` (the SCOPE name). Every module under
``packages/evalschema/src/tracer_evalschema`` imports only the standard library, ``pydantic`` (and
its dependency ``pydantic_core``), ``rfc8785`` and the package itself. Two independent checks:

* **Static (AST):** every ``import`` and ``from ... import`` in every module, at any depth
  (inside a function, a ``try``, an ``if TYPE_CHECKING`` block), and every ``importlib`` or
  ``__import__`` call, names an allowed top-level module. A dynamic import is allowed only when it
  can be shown to stay inside the package (a literal name, or an f-string that starts with
  ``tracer_evalschema.``).
* **Dynamic (a fresh interpreter):** ``python -I`` imports every module of the package, renders
  the JSON Schema files and builds a model, then lists ``sys.modules``. Compared with a baseline
  interpreter that imported nothing, the only third-party top-level modules that appear are
  ``pydantic``, ``pydantic_core``, ``annotated_types``, ``typing_extensions``, ``typing_inspection``
  (pydantic's own dependencies) and ``rfc8785``, plus the package. No test-only dependency
  (``jsonschema``, ``pytest``, ``hypothesis``, the clean-room bidi reference) is ever loaded.

The two checkers are given teeth by self-tests that feed them a forbidden import in every form.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ._support import PACKAGE_DIR

SRC = PACKAGE_DIR / "src" / "tracer_evalschema"
PACKAGE = "tracer_evalschema"

#: Allowed top-level modules in source, besides the standard library: the package, pydantic, its
#: compiled core and rfc8785 (ADR 0008 I16).
SOURCE_THIRD_PARTY: frozenset[str] = frozenset({PACKAGE, "pydantic", "pydantic_core", "rfc8785"})

#: Third-party top-level modules that may appear in ``sys.modules`` of the fresh interpreter:
#: the source set plus the dependencies pydantic 2.14 itself imports.
RUNTIME_THIRD_PARTY: frozenset[str] = frozenset(
    {
        PACKAGE,
        "pydantic",
        "pydantic_core",
        "annotated_types",
        "typing_extensions",
        "typing_inspection",
        "rfc8785",
    }
)

#: Modules that must never be loaded by the runtime package (all are test-only or dev-only).
TEST_ONLY_MODULES: tuple[str, ...] = ("jsonschema", "pytest", "hypothesis", "bidi", "yaml")

STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)

#: Modules the standard library generates for itself that ``sys.stdlib_module_names`` does not list
#: (``sysconfig`` loads ``_sysconfigdata__<platform>``). Matched by prefix, never by a name a
#: dependency could use.
STDLIB_GENERATED_PREFIXES: tuple[str, ...] = ("_sysconfigdata_",)


# ------------------------------------------------------------------------------ static check


def _is_package_fstring(node: ast.expr) -> bool:
    """An f-string whose literal head is ``tracer_evalschema.``: it stays inside the package."""
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return False
    head = node.values[0]
    return (
        isinstance(head, ast.Constant)
        and isinstance(head.value, str)
        and head.value.startswith(PACKAGE + ".")
    )


def _call_name(node: ast.Call) -> str | None:
    function = node.func
    if isinstance(function, ast.Name) and function.id in {"__import__", "import_module"}:
        return function.id
    if isinstance(function, ast.Attribute) and function.attr in {"__import__", "import_module"}:
        return function.attr
    return None


def import_problems(source: str, allowed_third_party: frozenset[str]) -> list[str]:
    """The imports in ``source`` that the whitelist forbids, as ``line: reason`` text (no source
    text is quoted beyond the module name). Relative imports stay inside the package."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                if top not in STDLIB and top not in allowed_third_party:
                    problems.append(f"{node.lineno}: import of {top}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            top = (node.module or "").partition(".")[0]
            if top not in STDLIB and top not in allowed_third_party:
                problems.append(f"{node.lineno}: import from {top or '(no module)'}")
        elif isinstance(node, ast.Call) and _call_name(node) is not None:
            if not node.args:
                problems.append(f"{node.lineno}: dynamic import without a module name")
                continue
            target = node.args[0]
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                top = target.value.partition(".")[0]
                if top not in STDLIB and top not in allowed_third_party:
                    problems.append(f"{node.lineno}: dynamic import of {top}")
            elif not _is_package_fstring(target):
                problems.append(f"{node.lineno}: dynamic import of a computed name")
    return problems


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _module_names() -> list[str]:
    names: list[str] = []
    for path in _modules():
        parts = path.relative_to(SRC.parent).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return sorted(names)


def test_the_scan_covers_the_whole_package() -> None:
    names = _module_names()
    assert PACKAGE in names
    assert f"{PACKAGE}.schema_export" in names
    assert f"{PACKAGE}.bundle" in names
    assert len(names) >= 35
    assert len(_modules()) == len(names)


def test_runtime_imports_whitelist() -> None:
    """Static leg: every module imports only the standard library, pydantic, pydantic_core,
    rfc8785 and the package itself."""
    found = {
        f"{path.relative_to(SRC).as_posix()}:{problem}"
        for path in _modules()
        for problem in import_problems(path.read_text(encoding="utf-8"), SOURCE_THIRD_PARTY)
    }
    assert not found, sorted(found)


def test_every_module_parses_and_has_a_third_party_import_count() -> None:
    """A guard against the scan silently matching nothing: pydantic and rfc8785 are both used."""
    tops: set[str] = set()
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                tops.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops.add(node.module.partition(".")[0])
    assert {"pydantic", "pydantic_core", "rfc8785", PACKAGE} <= tops
    assert (tops - STDLIB) <= SOURCE_THIRD_PARTY


@pytest.mark.parametrize(
    "source",
    [
        "import jsonschema",
        "import jsonschema.validators as v",
        "from jsonschema import validate",
        "from jsonschema.exceptions import ValidationError",
        "import os\nimport requests",
        "def f():\n    import yaml\n    return yaml",
        "try:\n    import ujson\nexcept ImportError:\n    ujson = None",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import numpy",
        "import importlib\nimportlib.import_module('jsonschema')",
        "importlib.import_module('numpy.linalg')",
        "__import__('hypothesis')",
        "from importlib import import_module\nimport_module('pytest')",
        "import importlib\nimportlib.import_module(name)",
        "import importlib\nimportlib.import_module(f'other.{name}')",
        "__import__()",
        "import typing_extensions",
        "import annotated_types",
    ],
)
def test_the_static_check_detects_each_form(source: str) -> None:
    assert import_problems(source, SOURCE_THIRD_PARTY)


@pytest.mark.parametrize(
    "source",
    [
        "import os, sys\nimport os.path\nfrom collections.abc import Mapping",
        "from __future__ import annotations",
        "import pydantic\nfrom pydantic import BaseModel\nfrom pydantic_core import core_schema",
        "import rfc8785\nfrom tracer_evalschema.ids import TypedId",
        "from . import ids\nfrom .ids import TypedId\nfrom .. import x",
        "import importlib\nimportlib.import_module(f'tracer_evalschema.{name}')",
        "import importlib\nimportlib.import_module('json')",
        "def f():\n    import unicodedata\n    return unicodedata",
        "# import jsonschema\n'''import requests'''\nx = 'import numpy'",
    ],
)
def test_the_static_check_allows_each_permitted_form(source: str) -> None:
    assert import_problems(source, SOURCE_THIRD_PARTY) == []


def test_the_problem_text_quotes_no_source() -> None:
    problems = import_problems("import secret_vendor_name_42", SOURCE_THIRD_PARTY)
    assert problems == ["1: import of secret_vendor_name_42"]


# ----------------------------------------------------------------------- fresh interpreter

_BASELINE = (
    "import json, sys\nprint(json.dumps(sorted({n.partition('.')[0] for n in sys.modules})))\n"
)

_PROBE = """
import importlib, json, sys
before = {n.partition('.')[0] for n in sys.modules}
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
extra = sys.argv[2] if len(sys.argv) > 2 else ''
if extra:
    exec(compile(extra, '<extra>', 'exec'))
loaded = {n.partition('.')[0] for n in sys.modules}
print(json.dumps({'before': sorted(before), 'after': sorted(loaded)}))
"""


def _run_isolated(args: list[str]) -> str:
    done = subprocess.run(  # noqa: S603 - fixed interpreter, fixed code, no shell
        [sys.executable, "-I", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def third_party_loaded(extra: str = "") -> set[str]:
    """The third-party top-level modules a fresh ``python -I`` interpreter holds after importing
    every module of the package (and running ``extra``), beyond what a bare interpreter holds and
    beyond the standard library."""
    baseline = set(json.loads(_run_isolated(["-c", _BASELINE])))
    out = _run_isolated(["-c", _PROBE, json.dumps(_module_names()), extra])
    loaded = set(json.loads(out)["after"])
    return {
        name
        for name in loaded - baseline
        if name not in STDLIB and not name.startswith(STDLIB_GENERATED_PREFIXES)
    }


_EXERCISE = """
import tracer_evalschema.schema_export as export
files = export.render_schemas()
assert files
from pydantic import TypeAdapter
import tracer_evalschema.primitives as primitives
adapter = TypeAdapter(primitives.UtcDatetime)
adapter.validate_python('2026-10-24T08:00:00Z')
"""


def test_runtime_imports_whitelist_in_a_fresh_interpreter() -> None:
    """Dynamic leg: importing every module (and rendering the schemas) loads no third-party
    module besides the whitelist."""
    loaded = third_party_loaded(_EXERCISE)
    assert PACKAGE in loaded
    assert {"pydantic", "pydantic_core", "rfc8785"} <= loaded
    unexpected = loaded - RUNTIME_THIRD_PARTY
    assert not unexpected, sorted(unexpected)


@pytest.mark.parametrize("name", TEST_ONLY_MODULES)
def test_no_test_only_module_is_loaded_by_the_runtime(name: str) -> None:
    assert name not in third_party_loaded(_EXERCISE)


def test_the_interpreter_is_isolated() -> None:
    """``-I`` means no user site, no ``PYTHON*`` variables and no current directory on the path."""
    out = _run_isolated(
        ["-c", "import sys; print(sys.flags.isolated, sys.flags.no_user_site, '' in sys.path)"]
    )
    assert out.split() == ["1", "1", "False"]


@pytest.mark.parametrize(
    "extra",
    [
        "import pytest",
        "import jsonschema",
        "import hypothesis",
        "import importlib; importlib.import_module('pytest')",
    ],
)
def test_the_dynamic_check_detects_a_test_only_import(extra: str) -> None:
    """The check has teeth: a forbidden module loaded after the package imports is found."""
    loaded = third_party_loaded(extra)
    assert loaded - RUNTIME_THIRD_PARTY


def test_the_dynamic_check_baseline_is_not_empty_of_the_package() -> None:
    assert third_party_loaded() >= {PACKAGE, "pydantic"}
