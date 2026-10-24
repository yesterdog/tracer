# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helper of the shipped-revision-file pin (gate P2-G finding m1). Holds no test.

``docs/STORAGE_POLICY`` says a shipped Alembic revision is never regenerated. Nothing but this pin
stops an edit of ``0001_initial_schema.py`` made together with ``tables.py`` (the pair would stay
consistent with each other and every test would pass). ``tests/test_evaldb_revision_files.py``
compares the SHA-256 of the bytes on disk of every revision file in the ``versions`` directory with
the committed file ``tests/fixtures/revision_files_sha256.json`` (revision id -> hash).

Revision id: the string literal assigned to ``revision`` at module level of the file.

Run ``uv run python packages/evaldb/tests/evaldb_revision_pin.py`` to print the current state and
``... --write`` to add the pin of a revision that has none. ``--write`` never changes an existing
pin: if a pinned file differs it prints the difference and exits 1 without writing anything.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIN_FILE = HERE / "fixtures" / "revision_files_sha256.json"
VERSIONS = HERE.parent / "src" / "tracer_evaldb" / "migrations" / "versions"


def revision_files(directory: Path) -> list[Path]:
    """Every revision file of a versions directory (not ``__init__.py``, not caches)."""
    return sorted(p for p in directory.glob("*.py") if p.is_file() and p.name != "__init__.py")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def revision_id(path: Path) -> str:
    tree = ast.parse(path.read_bytes())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "revision"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ValueError(f"{path.name}: no module-level revision = '<id>'")


def current(directory: Path) -> dict[str, tuple[str, str]]:
    """Revision id -> (file name, sha256) of the files on disk."""
    found: dict[str, tuple[str, str]] = {}
    for path in revision_files(directory):
        rid = revision_id(path)
        if rid in found:
            raise ValueError(f"revision id {rid} is in two files")
        found[rid] = (path.name, file_digest(path))
    return found


def read_pin(pin_file: Path = PIN_FILE) -> dict[str, dict[str, str]]:
    loaded = json.loads(pin_file.read_text("utf-8"))
    assert isinstance(loaded, dict)
    return {"files": dict(loaded["files"]), "sha256": dict(loaded["sha256"])}


def problems(directory: Path, pin: Mapping[str, Mapping[str, str]]) -> list[str]:
    """What differs between the files on disk and the pin (empty when they agree)."""
    found = current(directory)
    out: list[str] = []
    for rid, (name, digest) in found.items():
        if rid not in pin["sha256"]:
            out.append(f"revision {rid} ({name}) has no pin")
        elif pin["sha256"][rid] != digest:
            out.append(f"revision {rid} ({name}) differs from its pin: {digest}")
        elif pin["files"].get(rid) != name:
            out.append(f"revision {rid} is pinned as file {pin['files'].get(rid)}, found {name}")
    for rid in pin["sha256"]:
        if rid not in found:
            out.append(f"pinned revision {rid} ({pin['files'].get(rid)}) is missing")
    return out


def pin_document(files: Mapping[str, str], sha256: Mapping[str, str]) -> dict[str, object]:
    return {
        "format": 1,
        "algorithm": "sha256",
        "serialization": "the bytes of the revision file on disk, SHA-256 (lower-case hex)",
        "files": dict(sorted(files.items())),
        "sha256": dict(sorted(sha256.items())),
    }


def write_missing(directory: Path = VERSIONS, pin_file: Path = PIN_FILE) -> int:
    """Add the missing pins; refuse (return 1, write nothing) if an existing one differs."""
    pin = read_pin(pin_file) if pin_file.is_file() else {"files": {}, "sha256": {}}
    found = current(directory)
    changed = [
        f"revision {rid} ({name}) differs from its pin: an existing pin never changes"
        for rid, (name, digest) in found.items()
        if rid in pin["sha256"] and pin["sha256"][rid] != digest
    ]
    if changed:
        print("\n".join(changed))
        return 1
    files, sha = dict(pin["files"]), dict(pin["sha256"])
    added = []
    for rid, (name, digest) in found.items():
        if rid not in sha:
            files[rid], sha[rid] = name, digest
            added.append(rid)
    if added:
        text = json.dumps(pin_document(files, sha), indent=2, ensure_ascii=True) + "\n"
        pin_file.write_text(text, "utf-8")
    print(f"added {added}" if added else "nothing to add")
    return 0


def main(argv: Sequence[str]) -> int:
    if "--write" in argv:
        return write_missing()
    found = current(VERSIONS)
    files = {rid: name for rid, (name, _) in found.items()}
    sha = {rid: digest for rid, (_, digest) in found.items()}
    print(json.dumps(pin_document(files, sha), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
