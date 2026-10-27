# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-25: the store of a simulated run (ADR 0027 rule 17 and invariant I9; ADR 0007 Decision 5; the
Phase 3 batch D store tests and the batch I failure tests as models).

Contract: docs/phase4/api-surface-batch-e.md section 12. The bundle the command builds round-trips
through the SQLite store byte for byte, a second run joins the same store, and the same bundle
stores on Postgres with the same rows (``postgres`` marker, loopback, ``TRACER_TEST_POSTGRES_URL``).
A failure of the store write after the call keeps the report, the recording and the usage.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import tracer_evaldb
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    NOW,
    STAMP,
    SarvamSim,
    mod,
    real_args,
    real_env,
    real_files,
    run_sim,
    write_inputs,
)

POSTGRES_URL_ENV = "TRACER_TEST_POSTGRES_URL"


class Captured:
    """What the command handed to ``store_run`` (the records it built)."""

    def __init__(self) -> None:
        self.project: Any = None
        self.target: Any = None
        self.bundle: Any = None


def capture(monkeypatch: pytest.MonkeyPatch) -> Captured:
    store = mod("store")
    original = store.store_run
    seen = Captured()

    def spy(engine: Any, *, project: Any, agent_target: Any, bundle: Any) -> Any:
        seen.project, seen.target, seen.bundle = project, agent_target, bundle
        return original(engine, project=project, agent_target=agent_target, bundle=bundle)

    monkeypatch.setattr(store, "store_run", spy)
    return seen


def test_simulated_run_round_trips_through_store_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0027 invariant I9: the bundle read back from the store is the bundle that was stored, in
    value and in bytes; storing it again changes nothing; the file is private."""
    seen = capture(monkeypatch)
    files = real_files(tmp_path)
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert run.code == 0, (run.out, run.err)
    store = mod("store")
    path = files.out / "tracer.sqlite"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    engine = store.open_sqlite_store(files.out)
    try:
        loaded = store.load_run(engine, seen.bundle.id)
        assert loaded == seen.bundle
        assert loaded.model_dump_json() == seen.bundle.model_dump_json()
        assert loaded.model_dump_json().encode("utf-8") == seen.bundle.model_dump_json().encode(
            "utf-8"
        )
        again = store.store_run(
            engine, project=seen.project, agent_target=seen.target, bundle=seen.bundle
        )
        assert again.created is False
        assert sum(again.inserted.values()) == 0
    finally:
        engine.dispose()
    assert seen.bundle.run.mode == "simulated"
    assert seen.bundle.usage_records


def test_a_second_run_joins_the_store_of_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh identifiers per run (ADR 0023 rule 18): two runs of one call are two bundles in one
    store; neither overwrites the other."""
    seen = capture(monkeypatch)
    files = write_inputs(tmp_path)
    first = run_sim(files)
    one = seen.bundle
    second = run_sim(files, instant=NOW + timedelta(seconds=2))
    assert first.code == second.code == 0
    assert one.id != seen.bundle.id
    assert one.run.id != seen.bundle.run.id
    store = mod("store")
    engine = store.open_sqlite_store(files.out)
    try:
        assert store.load_run(engine, one.id) == one
        assert store.load_run(engine, seen.bundle.id) == seen.bundle
    finally:
        engine.dispose()
    assert sorted(p.name for p in files.out.glob("report-*.html")) == [
        f"report-{STAMP}.html",
        "report-20261027T120002Z.html",
    ]


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "sqlite":
        engine = mod("store").open_sqlite_store(tmp_path / "backend")
        try:
            yield engine
        finally:
            engine.dispose()
        return
    url = os.environ.get(POSTGRES_URL_ENV)
    assert url, f"{POSTGRES_URL_ENV} is not set"
    engine = tracer_evaldb.create_store_engine(url)
    tracer_evaldb.metadata.drop_all(engine)
    tracer_evaldb.metadata.create_all(engine)
    try:
        yield engine
    finally:
        tracer_evaldb.metadata.drop_all(engine)
        engine.dispose()


def test_simulated_bundle_stores_on_both_backends(
    backend: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0027 rule 17: the simulated bundle stores with the same rows on SQLite and Postgres and
    reads back identical. The SQLite parametrisation always runs; the Postgres one carries the
    ``postgres`` marker (loopback, ``TRACER_TEST_POSTGRES_URL``) and is selected in its CI job."""
    seen = capture(monkeypatch)
    files = real_files(tmp_path / "run")
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert run.code == 0
    store = mod("store")
    stored = store.store_run(
        backend, project=seen.project, agent_target=seen.target, bundle=seen.bundle
    )
    # the reference rows are those of a SQLite store made from the same records
    reference = store.open_sqlite_store(tmp_path / "reference")
    try:
        expected = store.store_run(
            reference, project=seen.project, agent_target=seen.target, bundle=seen.bundle
        )
    finally:
        reference.dispose()
    assert stored.created is True
    assert dict(stored.inserted) == dict(expected.inserted)
    assert stored.inserted.get("usage_record", 0) == len(seen.bundle.usage_records) == 4
    loaded = store.load_run(backend, seen.bundle.id)
    assert loaded == seen.bundle
    assert loaded.model_dump_json() == seen.bundle.model_dump_json()


def test_a_failed_store_write_keeps_the_report_the_recording_and_the_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase 3 batch G rule for the paid send, applied to the call: the call happened and was
    paid for, so a store that cannot be written does not lose the report, the recording or the
    usage (exit 1, a recovery file, the lines say what was not written)."""
    store = mod("store")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "store_run", broken)
    files = real_files(tmp_path)
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert run.code == 1
    assert run.err == "error: unexpected failure (RuntimeError)\n"
    assert run.value("store") == "not written"
    assert run.value("report").endswith(f"report-{STAMP}.html")
    assert run.value("recording").endswith(f"recording-{STAMP}.wav")
    (recovery,) = list(files.out.glob("usage-recovery-*.json"))
    assert run.out.splitlines()[-1] == f"usage recovery: {recovery}"
    import json

    document = json.loads(recovery.read_text("ascii"))
    assert document["failed_step"] == "store"
    assert document["error_class"] == "RuntimeError"
    assert len(document["usage_records"]) == 4
    assert document["cost_records"]
    assert "disk full" not in recovery.read_text("ascii") + run.out + run.err
    assert (files.out / f"report-{STAMP}.html").is_file()


def test_a_failed_store_write_of_a_free_call_says_there_is_no_usage_to_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mod("store"), "store_run", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    run = run_sim(write_inputs(tmp_path))
    assert run.code == 1
    assert run.value("store") == "not written"
    assert run.out.splitlines()[-1] == "usage recovery: not written"


def test_a_store_file_that_is_a_link_is_refused_before_anything_is_sent(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    files.out.mkdir(mode=0o700)
    os.symlink(tmp_path / "elsewhere.sqlite", files.out / "tracer.sqlite")
    sim = SarvamSim()
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=sim)
    assert run.code == 4
    assert "store_file_is_a_link" in run.err
    assert sim.requests == []
    assert run.out == ""


def test_the_stored_documents_are_the_adr_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read back from the store: the sim profile in the snapshot, the outcome and the timing on the
    Run, all valid against the shipped schemas and free of text (metadata flags empty)."""
    seen = capture(monkeypatch)
    files = write_inputs(tmp_path)
    run = run_sim(files)
    assert run.code == 0
    bundle = run.bundle()
    core = mod("cli")  # noqa: F841
    from tracer_evalcore.simoutcome import SIM_OUTCOME_METADATA_KEY, validate_sim_outcome
    from tracer_evalcore.simprofile import SIM_PROFILE_METADATA_KEY, validate_sim_profile
    from tracer_evalcore.simtiming import TIMING_METADATA_KEY, validate_timing_document

    validate_sim_profile(bundle.snapshot.metadata[SIM_PROFILE_METADATA_KEY], scripted_turn_count=2)
    validate_sim_outcome(bundle.run.metadata[SIM_OUTCOME_METADATA_KEY], brain_mode="scripted")
    validate_timing_document(bundle.run.metadata[TIMING_METADATA_KEY])
    assert bundle.run.metadata_flags == []
    assert bundle.snapshot.metadata_flags == []
    assert bundle == seen.bundle
