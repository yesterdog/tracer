# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the Phase 1 tests (no tests here).

Implementation modules are imported lazily through ``api()`` so that a module the architect has
not written yet fails the test that needs it (ModuleNotFoundError / AttributeError) and never
breaks collection. The public import paths are listed in docs/phase1/api-surface.md.
"""

from __future__ import annotations

import functools
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from pydantic import TypeAdapter

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]
PACKAGE_DIR = TESTS_DIR.parent
DATA_DIR = PACKAGE_DIR / "src" / "tracer_evalschema" / "data"
FIXTURES = TESTS_DIR / "fixtures"

CURRENT_SCHEMA_VERSION = "0.0.1"


def api(name: str) -> Any:
    """Import ``tracer_evalschema.<name>`` (or the package itself for an empty name)."""
    return importlib.import_module("tracer_evalschema" + (f".{name}" if name else ""))


def adapter(module: str, name: str) -> TypeAdapter[Any]:
    """``pydantic.TypeAdapter`` for ``tracer_evalschema.<module>.<name>``."""
    target: Any = getattr(api(module), name)
    return TypeAdapter(target)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def data_json(name: str) -> Any:
    return load_json(DATA_DIR / name)


def resolve_schema(schema: dict[str, Any], node: Any) -> Any:
    """Follow local ``$ref`` and single-element ``allOf`` wrappers inside a pydantic JSON Schema."""
    seen = 0
    while isinstance(node, dict) and seen < 20:
        seen += 1
        if "$ref" in node:
            target: Any = schema
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        elif "allOf" in node and len(node["allOf"]) == 1 and len(node) <= 2:
            node = node["allOf"][0]
        else:
            break
    return node


# ----------------------------------------------------------------------------- test doubles
# Entity classes for the P1-05 tests. They are defined lazily (the base module may not exist yet)
# and live outside the tracer_evalschema package, so the entity registry (which only counts
# classes defined inside the package) never sees them. They borrow the registered prefixes
# "evl" and "per" for their IDs.


@functools.cache
def widget_classes() -> SimpleNamespace:
    base = api("base")
    entity_base: Any = base.EntityBase
    versioned_base: Any = base.VersionedEntity

    class Widget(entity_base):  # type: ignore[misc]
        id_prefix: ClassVar[str] = "evl"
        name: str
        note: str | None = None

    class VersionedWidget(versioned_base):  # type: ignore[misc]
        id_prefix: ClassVar[str] = "per"
        name: str
        note: str | None = None

    return SimpleNamespace(Widget=Widget, VersionedWidget=VersionedWidget)


def entity_doc(prefix: str = "evl", **over: Any) -> dict[str, Any]:
    """A complete, JSON-mode EntityBase document (every serialized field present)."""
    doc: dict[str, Any] = {
        "id": str(api("ids").id_type(prefix).new()),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "created_at": "2026-10-22T10:00:00.000000Z",
        "metadata": {},
        "metadata_flags": [],
        "name": "widget",
    }
    doc.update(over)
    return doc


def redigest(doc: dict[str, Any]) -> dict[str, Any]:
    """Return ``doc`` with a fresh plain content_digest over its digest domain."""
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    digest = api("digest").compute_digest(body)
    return {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}


def versioned_doc(prefix: str = "per", version: int = 1, **over: Any) -> dict[str, Any]:
    return redigest(entity_doc(prefix, version=version, **over))
