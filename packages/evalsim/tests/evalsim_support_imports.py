# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Small helpers shared by the tracer-evalsim batch D tests. Support module, not collected.

``mod`` imports a module of ``tracer_evalsim`` lazily, so that before the module exists each test
fails on its own with ``ModuleNotFoundError`` and the directory still collects (the style of the
batch 0b tests).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any


def mod(name: str) -> Any:
    return importlib.import_module(f"tracer_evalsim.{name}")


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = repo_root()
SRC = ROOT / "packages" / "evalsim" / "src" / "tracer_evalsim"
ADR26 = ROOT / "docs" / "adr" / "0026-simulated-caller-architecture.md"


def adr_dialect_section(dialect_id: str) -> str:
    """The text of one subsection of "## Dialect specifications" in ADR 0026."""
    text = ADR26.read_text(encoding="utf-8")
    start = text.index("## Dialect specifications")
    end = text.index("\n## ", start + 5)
    body = text[start:end]
    marker = f"### `{dialect_id}`"
    assert marker in body, dialect_id
    after = body[body.index(marker) + len(marker) :]
    nxt = after.find("\n### ")
    return after if nxt < 0 else after[:nxt]


def leaks(obj: Any, secret: str, *, _depth: int = 0, _seen: set[int] | None = None) -> bool:
    """True if ``secret`` is in ``obj``: strings, bytes, containers and the attributes of objects
    that belong to ``tracer_evalsim`` (objects of other modules are not entered: the websockets
    library holds the request it sent, which is its business)."""
    seen = _seen if _seen is not None else set()
    if id(obj) in seen or _depth > 8:
        return False
    seen.add(id(obj))
    if isinstance(obj, str):
        return secret in obj
    if isinstance(obj, bytes | bytearray):
        return secret.encode() in bytes(obj)
    if isinstance(obj, dict):
        return any(
            leaks(k, secret, _depth=_depth + 1, _seen=seen)
            or leaks(v, secret, _depth=_depth + 1, _seen=seen)
            for k, v in obj.items()
        )
    if isinstance(obj, list | tuple | set | frozenset):
        return any(leaks(v, secret, _depth=_depth + 1, _seen=seen) for v in obj)
    if isinstance(obj, BaseException):
        return leaks(
            (obj.args, getattr(obj, "__dict__", {}), repr(obj), str(obj)),
            secret,
            _depth=_depth + 1,
            _seen=seen,
        )
    if type(obj).__module__.startswith("tracer_evalsim") or type(obj).__module__ == "builtins":
        attrs: dict[str, Any] = {}
        if hasattr(obj, "__dict__"):
            attrs.update(vars(obj))
        for klass in type(obj).__mro__:
            for slot in getattr(klass, "__slots__", ()):
                if hasattr(obj, slot):
                    attrs[slot] = getattr(obj, slot)
        return leaks(attrs, secret, _depth=_depth + 1, _seen=seen)
    return False


def imported_top_levels(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found
