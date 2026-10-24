# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Measurement harness for P2-23: store and load of a large run bundle on both backends.

Contract: docs/phase2/api-surface-batch-e.md section 7. This is evidence tooling, not a test: it
records numbers and the environment they were taken in, and asserts nothing about speed (no timing
threshold exists anywhere in the repository; the only asserted property of the store is the
statement count of ``test_store_run_bundle_statement_count_is_independent_of_bundle_size``).

Run it from the repository root, SQLite or a throwaway Postgres database (the database must be
EMPTY: the harness refuses otherwise, creates the schema through Alembic, and removes every table
again at the end)::

    uv run --locked python packages/evaldb/tests/evaldb_measure.py --backend sqlite \\
        --out docs/phase2/evidence/measurements/bundle/sqlite-py3.14.json
    TRACER_MEASURE_POSTGRES_URL=postgresql+pg8000://... uv run --locked --all-groups python \\
        packages/evaldb/tests/evaldb_measure.py --backend postgres \\
        --out docs/phase2/evidence/measurements/bundle/postgres-18-py3.14.json

The output is one JSON document (``FORMAT``). It holds no URL, host, user, password or path.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa

FORMAT = "tracer-evaldb-measurement/1"
DEFAULT_SIZES = (4, 400, 4000)
DEFAULT_REPEATS = 5
URL_ENV = "TRACER_MEASURE_POSTGRES_URL"
NOTE = (
    "Evidence only. No threshold is asserted or implied; numbers depend on the machine, the "
    "Python build, the driver and the server."
)


class DatabaseNotEmpty(RuntimeError):
    """The harness creates and drops the whole schema, so it only runs on an empty database."""


def _versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in (
        "tracer-evaldb",
        "tracer-evalschema",
        "sqlalchemy",
        "alembic",
        "pydantic",
        "pg8000",
    ):
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = "not installed"
    return found


def environment(engine: sa.Engine) -> dict[str, Any]:
    """Where the numbers were taken: no host name, user name, path or URL."""
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            server = "SQLite " + str(conn.exec_driver_sql("SELECT sqlite_version()").scalar_one())
        else:
            server = str(conn.exec_driver_sql("SHOW server_version").scalar_one())
        conn.rollback()
    return {
        "backend": "sqlite" if engine.dialect.name == "sqlite" else "postgres",
        "server": server,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "packages": _versions(),
    }


def assert_empty(engine: sa.Engine) -> None:
    names = sa.inspect(engine).get_table_names()
    if names:
        raise DatabaseNotEmpty(f"the database holds {len(names)} table(s); use an empty one")


def _summary(seconds: Sequence[float]) -> dict[str, Any]:
    return {
        "seconds": [round(s, 6) for s in seconds],
        "min": round(min(seconds), 6),
        "median": round(statistics.median(seconds), 6),
        "max": round(max(seconds), 6),
    }


def _bundle(turns: int) -> Any:
    from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
        load_run_bundle_doc,
        sized_bundle_doc,
    )

    return load_run_bundle_doc(sized_bundle_doc(turns, tool_calls=2, usages=2))


def measure_size(engine: sa.Engine, turns: int, repeats: int) -> dict[str, Any]:
    from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
        recording,
        store_project_of,
    )

    evaldb = importlib.import_module("tracer_evaldb")
    store_seconds: list[float] = []
    load_seconds: list[float] = []
    statements: dict[str, int] = {}
    rows: dict[str, int] = {}
    document_bytes = 0
    for _ in range(repeats):
        bundle = _bundle(turns)  # built and validated outside the timed region
        document_bytes = len(bundle.model_dump_json().encode("utf-8"))
        store_project_of(engine, evaldb, bundle)
        with recording(engine) as store_statements:
            started = time.perf_counter()
            result = evaldb.store_run_bundle(engine, bundle)
            store_seconds.append(time.perf_counter() - started)
        with recording(engine) as load_statements:
            started = time.perf_counter()
            loaded = evaldb.load_run_bundle(engine, bundle.id)
            load_seconds.append(time.perf_counter() - started)
        assert loaded.complete
        assert loaded.bundle == bundle
        per_table = Counter(store_statements.inserts)
        statements = {
            "store_total": store_statements.count,
            "store_inserts": len(store_statements.inserts),
            "max_inserts_per_table": max(per_table.values()),
            "load_total": load_statements.count,
        }
        rows = {table: int(n) for table, n in sorted(dict(result.inserted).items())}
        evaldb.delete_run(
            engine, bundle.run.id, actor="measure", reason="operator_request"
        )  # the next repeat starts from the same state
    return {
        "turns": turns,
        "document_bytes": document_bytes,
        "rows_stored": rows,
        "store": _summary(store_seconds),
        "load": _summary(load_seconds),
        "statements": statements,
    }


def measure(
    engine: sa.Engine, sizes: Sequence[int] = DEFAULT_SIZES, repeats: int = DEFAULT_REPEATS
) -> dict[str, Any]:
    """Migrate an empty database, measure each size, remove the schema, return the document."""
    if repeats < 1 or not sizes or min(sizes) < 1:
        raise ValueError("sizes and repeats must be positive")
    assert_empty(engine)
    migrate = importlib.import_module("tracer_evaldb.migrate")
    migrate.upgrade(engine)
    try:
        results = [measure_size(engine, turns, repeats) for turns in sizes]
        env = environment(engine)
    finally:
        migrate.downgrade(engine, "base", allow_data_loss=True)
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    return {
        "format": FORMAT,
        "environment": env,
        "parameters": {"sizes": list(sizes), "repeats": repeats},
        "results": results,
        "note": NOTE,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--backend", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--out", type=Path, required=True, help="the JSON file to write")
    args = parser.parse_args(argv)
    evaldb = importlib.import_module("tracer_evaldb")
    with tempfile.TemporaryDirectory() as scratch:
        if args.backend == "sqlite":
            url = f"sqlite:///{Path(scratch) / 'measure.db'}"
        else:
            url = os.environ.get(URL_ENV, "")
            if not url:
                parser.error(f"{URL_ENV} must name an EMPTY throwaway Postgres database")
        engine = evaldb.create_store_engine(url)
        try:
            document = measure(engine, args.sizes, args.repeats)
        except DatabaseNotEmpty as exc:
            print(str(exc), file=sys.stderr)
            return 2
        finally:
            engine.dispose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
