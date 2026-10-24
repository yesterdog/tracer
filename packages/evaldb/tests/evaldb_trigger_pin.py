# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helper of the Phase 2 batch G trigger-DDL pin (P2-24 finding E17). Holds no test.

Contract: docs/phase2/api-surface-batch-g.md section 5. Revision 0001 calls the live
``triggers.function_statements`` and ``triggers.create_statements`` (it does not carry that text),
so a change of ``triggers.py`` silently changes what a new database gets from revision 0001 while
every database migrated earlier keeps the old text. ``tests/test_evaldb_audit_fixes.py`` compares
the SHA-256 of the text of those statements with the committed file
``tests/fixtures/trigger_ddl_sha256.json``.

Serialization (the one the file records): the statements of a group in the order the revision
runs them, each encoded as UTF-8 and followed by one NUL byte, hashed with SHA-256, lower-case hex.
The groups are ``<dialect>.create`` (``create_statements`` for each table of revision 0001, in its
``TABLES`` order), ``postgresql.functions`` (``function_statements``) and ``<dialect>.drop``
(``drop_statements``, used by the data migrations that rewrite rows).

Run ``uv run python packages/evaldb/tests/evaldb_trigger_pin.py`` to print the current values and
``... --write`` to rewrite the committed file. See the test module for when that is allowed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

PIN_FILE = Path(__file__).resolve().parent / "fixtures" / "trigger_ddl_sha256.json"
SERIALIZATION = (
    "each statement as UTF-8 followed by one NUL byte, in the order the revision runs them, "
    "hashed with SHA-256 (lower-case hex)"
)
DIALECTS = ("sqlite", "postgresql")
GROUPS = (
    "sqlite.create",
    "sqlite.drop",
    "postgresql.functions",
    "postgresql.create",
    "postgresql.drop",
)


def digest(statements: Iterable[str]) -> str:
    hasher = hashlib.sha256()
    for statement in statements:
        hasher.update(statement.encode("utf-8") + b"\x00")
    return hasher.hexdigest()


def group_statements(
    triggers: object,
    tables: Sequence[str],
    function_source: Callable[[], Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """The statements of every group, from a ``triggers`` module, for the given tables."""
    create = getattr(triggers, "create_statements")  # noqa: B009
    drop = getattr(triggers, "drop_statements")  # noqa: B009
    functions = function_source or getattr(triggers, "function_statements")  # noqa: B009
    groups: dict[str, list[str]] = {}
    for dialect in DIALECTS:
        groups[f"{dialect}.create"] = [s for table in tables for s in create(table, dialect)]
        groups[f"{dialect}.drop"] = [s for table in tables for s in drop(table, dialect)]
    groups["postgresql.functions"] = list(functions())
    return groups


def current_hashes(triggers: object, tables: Sequence[str]) -> dict[str, str]:
    groups = group_statements(triggers, tables)
    return {name: digest(groups[name]) for name in GROUPS}


def read_pin() -> Mapping[str, object]:
    loaded = json.loads(PIN_FILE.read_text("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def pin_document(tables: Sequence[str], hashes: Mapping[str, str]) -> dict[str, object]:
    return {
        "format": 1,
        "algorithm": "sha256",
        "serialization": SERIALIZATION,
        "tables": list(tables),
        "sha256": {name: hashes[name] for name in GROUPS},
    }


def main(argv: Sequence[str]) -> int:
    import importlib

    triggers = importlib.import_module("tracer_evaldb.triggers")
    revision = importlib.import_module("tracer_evaldb.migrations.versions.0001_initial_schema")
    tables = list(revision.TABLES)
    document = pin_document(tables, current_hashes(triggers, tables))
    text = json.dumps(document, indent=2, ensure_ascii=True) + "\n"
    if "--write" in argv:
        PIN_FILE.write_text(text, "utf-8")
        print(f"wrote {PIN_FILE.name}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
