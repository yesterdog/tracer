# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Hashes of the frozen tracer-evalschema tree (P2-01, gate item 8).

``uv run python packages/evaldb/tests/evalschema_baseline.py`` prints the JSON that P2-01 records
as ``docs/phase2/evidence/evalschema-baseline.json`` (add the key ``commit``: the id of the
rewritten-history tip when the package is scaffolded; this script does not know it).
``test_evalschema_unchanged_since_phase1_gate`` recomputes the same values with these functions.

Source tree hash: SHA-256 of the concatenation, over every regular file below
``packages/evalschema/src/tracer_evalschema`` (path components named ``__pycache__`` and files
ending ``.pyc`` excluded) sorted by POSIX relative path, of ``<relative path>\\0<sha256 hex of the
file>\\n`` (UTF-8).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    """The repository root (found by uv.lock; the tests are staged outside packages/ for now)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
EVALSCHEMA = ROOT / "packages" / "evalschema"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_tree_sha256(source_root: Path) -> str:
    entries: list[str] = []
    for path in source_root.rglob("*"):
        rel = path.relative_to(source_root)
        if not path.is_file() or "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        entries.append(f"{rel.as_posix()}\0{_sha256(path)}\n")
    return hashlib.sha256("".join(sorted(entries)).encode("utf-8")).hexdigest()


def current_values() -> dict[str, str]:
    source = EVALSCHEMA / "src" / "tracer_evalschema"
    return {
        "schema_lock_sha256": _sha256(source / "schemas" / "schema.lock"),
        "source_tree_sha256": source_tree_sha256(source),
        "pyproject_sha256": _sha256(EVALSCHEMA / "pyproject.toml"),
    }


if __name__ == "__main__":
    sys.stdout.write(json.dumps(current_values(), indent=2, sort_keys=True) + "\n")
