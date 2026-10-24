# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch E, P2-23: the measurement harness for the 4,000-turn bundle.

Contract: docs/phase2/api-surface-batch-e.md section 7. The measurements themselves are evidence,
not assertions: no test here (or anywhere) compares a duration with a number. These tests pin only
what the harness promises: the document format, that it measures the real store and load, that the
statement counts it records do not grow with the bundle, that it refuses a database that is not
empty and leaves an empty one empty, and that its output holds no URL, path or secret.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import evaldb_measure as measure_module  # type: ignore[import-not-found,unused-ignore]
import evaldb_migration_support as _mig_support  # type: ignore[import-not-found,unused-ignore]
from evaldb_migration_support import MigStore, user_tables  # type: ignore[import-not-found,unused-ignore]

mig = _mig_support.mig
sqlite_mig = _mig_support.sqlite_mig
postgres_mig = _mig_support.postgres_mig

HARNESS = Path(measure_module.__file__)
TOP = {"format", "environment", "parameters", "results", "note"}
ENV = {
    "backend",
    "server",
    "python",
    "python_implementation",
    "system",
    "system_release",
    "machine",
    "cpu_count",
    "packages",
}
RESULT = {"turns", "document_bytes", "rows_stored", "store", "load", "statements"}
TIMING = {"seconds", "min", "median", "max"}
STATEMENTS = {"store_total", "store_inserts", "max_inserts_per_table", "load_total"}


def _check_document(document: dict[str, Any], sizes: list[int], repeats: int, backend: str) -> None:
    assert set(document) == TOP
    assert document["format"] == "tracer-evaldb-measurement/1"
    assert document["parameters"] == {"sizes": sizes, "repeats": repeats}
    assert set(document["environment"]) == ENV
    assert document["environment"]["backend"] == backend
    assert "tracer-evaldb" in document["environment"]["packages"]
    assert "threshold" in document["note"]
    assert [r["turns"] for r in document["results"]] == sizes
    for result in document["results"]:
        assert set(result) == RESULT
        for key in ("store", "load"):
            timing = result[key]
            assert set(timing) == TIMING
            assert len(timing["seconds"]) == repeats
            assert all(isinstance(s, float) and s >= 0 for s in timing["seconds"])
            assert timing["min"] <= timing["median"] <= timing["max"]  # a summary, not a threshold
        assert set(result["statements"]) == STATEMENTS
        assert all(isinstance(v, int) and v > 0 for v in result["statements"].values())
        assert result["document_bytes"] > result["turns"]
        assert result["rows_stored"]["turn"] == result["turns"]  # the real bundle was stored


def test_measurement_harness_records_store_and_load_in_the_documented_format(
    sqlite_mig: MigStore, tmp_path: Path
) -> None:
    document = measure_module.measure(sqlite_mig.engine, sizes=[4, 40], repeats=2)
    _check_document(document, [4, 40], 2, "sqlite")
    # the one asserted property of the store: its statement count does not grow with the bundle
    small, large = (r["statements"] for r in document["results"])
    assert small == large
    assert large["max_inserts_per_table"] == 1
    # the harness left the database empty, schema and alembic_version included
    assert user_tables(sqlite_mig.engine) == []
    text = json.dumps(document)
    assert str(tmp_path) not in text
    assert "://" not in text
    assert "sqlite" in text
    assert document["environment"]["server"].startswith("SQLite ")


def test_measurement_harness_refuses_a_database_that_is_not_empty(sqlite_mig: MigStore) -> None:
    with sqlite_mig.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE precious (x INTEGER)")
        conn.exec_driver_sql("INSERT INTO precious VALUES (1)")
    with pytest.raises(measure_module.DatabaseNotEmpty):
        measure_module.measure(sqlite_mig.engine, sizes=[4], repeats=1)
    with sqlite_mig.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT x FROM precious").all() == [(1,)]  # untouched
        conn.rollback()
    assert user_tables(sqlite_mig.engine) == ["precious"]


@pytest.mark.parametrize(
    "bad", [{"sizes": [], "repeats": 1}, {"sizes": [0], "repeats": 1}, {"sizes": [4], "repeats": 0}]
)
def test_measurement_harness_refuses_nonsense_parameters(
    sqlite_mig: MigStore, bad: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="positive"):
        measure_module.measure(sqlite_mig.engine, **bad)
    assert user_tables(sqlite_mig.engine) == []


def test_measurement_command_line_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "sqlite.json"
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(HARNESS),
            "--backend",
            "sqlite",
            "--sizes",
            "4",
            "--repeats",
            "1",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    _check_document(json.loads(out.read_text("utf-8")), [4], 1, "sqlite")
    assert str(tmp_path) not in out.read_text("utf-8")


def test_measurement_command_line_needs_a_postgres_url_and_never_prints_it(
    tmp_path: Path,
) -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(HARNESS), "--backend", "postgres", "--out", str(tmp_path / "x.json")],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 2
    assert "TRACER_MEASURE_POSTGRES_URL" in done.stderr
    assert not (tmp_path / "x.json").exists()


@pytest.mark.postgres
def test_measurement_harness_runs_on_postgres(postgres_mig: MigStore) -> None:
    document = measure_module.measure(postgres_mig.engine, sizes=[4, 40], repeats=2)
    _check_document(document, [4, 40], 2, "postgres")
    small, large = (r["statements"] for r in document["results"])
    assert small == large
    assert large["max_inserts_per_table"] == 1
    assert user_tables(postgres_mig.engine) == []
    assert "://" not in json.dumps(document)
    assert document["environment"]["packages"]["pg8000"] != "not installed"


def test_measurement_harness_is_not_collected_as_a_test_module() -> None:
    """It is evidence tooling: its name does not start with ``test_`` and it defines no test."""
    assert not HARNESS.name.startswith("test_")
    assert not [n for n in dir(measure_module) if n.startswith("test_")]
