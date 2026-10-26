# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-21: the store of a scored run (contract docs/phase3/api-surface-batch-d.md section 11).

``tracer_cli.store`` is the only place of Phase 3 that depends on ``tracer-evaldb`` (SCOPE package
split). The runs come from the pipeline of ``tracer_evalcore`` with the fake engine of the
evalcore batch D tests. ``[postgres]`` is selected only in the Postgres CI job (marker ``postgres``,
variable ``TRACER_TEST_POSTGRES_URL``); no other test here opens a socket.
"""

from __future__ import annotations

import ast
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tracer_evaldb
from cli_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    CLI_SRC,
    POSTGRES_URL_ENV,
    Scored,
    evalcore_support,
    score,
    store_mod,
)


def _open(tmp_path: Path, name: str = "out") -> Any:
    return store_mod().open_sqlite_store(tmp_path / name)


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "sqlite":
        engine = _open(tmp_path)
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


# ------------------------------------------------------------------------ the named tests


def test_run_round_trips_through_store_byte_identical(tmp_path: Path) -> None:
    store = store_mod()
    scored = score(tmp_path)
    engine = _open(tmp_path)
    stored = store.store_run(
        engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
    )
    assert stored.bundle_id == scored.bundle.id
    assert stored.created is True
    loaded = store.load_run(engine, scored.bundle.id)
    assert loaded.model_dump_json() == scored.bundle.model_dump_json()
    assert loaded.model_dump_json().encode("utf-8") == scored.bundle.model_dump_json().encode(
        "utf-8"
    )
    assert loaded == scored.bundle
    assert loaded.verdict is None
    # the records the run needs are stored too: project, agent target, snapshot and definitions
    assert tracer_evaldb.load_project(engine, scored.project.id) == scored.project
    target = tracer_evaldb.load_definition(
        engine, "agent_target", scored.agent_target.id, scored.agent_target.version
    )
    assert target == scored.agent_target
    snapshot = tracer_evaldb.load_snapshot(engine, scored.bundle.snapshot.id)
    assert snapshot.model_dump_json() == scored.bundle.snapshot.model_dump_json()
    for definition in scored.bundle.snapshot.metric_definitions:
        again = tracer_evaldb.load_definition(
            engine, "metric_definition", definition.id, definition.version
        )
        assert again.model_dump_json() == definition.model_dump_json()
    found = tracer_evaldb.find_snapshot_by_digest(engine, scored.bundle.snapshot.content_digest)
    assert found is not None
    assert found.id == scored.bundle.snapshot.id
    # the call text and the Devanagari come back exactly as stored
    texts = {t.text for t in loaded.turns}
    assert texts == {t.text for t in scored.bundle.turns}
    engine.dispose()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_store_file_is_0600(tmp_path: Path) -> None:
    store = store_mod()
    original = os.umask(0)
    try:
        engine = _open(tmp_path, "umask0")
        scored = score(tmp_path)
        store.store_run(
            engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
        )
        engine.dispose()
        os.umask(0o022)
        again = _open(tmp_path, "umask022")
        again.dispose()
    finally:
        os.umask(original)
    assert store.STORE_FILE_NAME == "tracer.sqlite"
    for name in ("umask0", "umask022"):
        path = tmp_path / name / store.STORE_FILE_NAME
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, name
        # whatever else SQLite keeps beside the file (a journal) is private as well
        for sibling in (tmp_path / name).iterdir():
            assert stat.S_IMODE(sibling.stat().st_mode) & 0o077 == 0, sibling.name
    # opening the store again never loosens the mode
    path = tmp_path / "umask0" / store.STORE_FILE_NAME
    _open(tmp_path, "umask0").dispose()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_pipeline_bundle_stores_on_both_backends(backend: Any, tmp_path: Path) -> None:
    store = store_mod()
    scored = score(tmp_path)
    stored = store.store_run(
        backend, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
    )
    assert stored.created is True
    assert stored.bundle_id == scored.bundle.id
    assert stored.inserted  # new rows were written, per table
    assert sum(stored.inserted.values()) >= len(scored.bundle.results) + len(scored.bundle.turns)
    loaded = store.load_run(backend, scored.bundle.id)
    assert loaded.model_dump_json() == scored.bundle.model_dump_json()
    # the same bundle again changes nothing
    again = store.store_run(
        backend, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
    )
    assert again.created is False
    assert sum(again.inserted.values()) == 0
    assert store.load_run(backend, scored.bundle.id) == scored.bundle


# ------------------------------------------------------------------------ the other outcomes


OUTCOMES: dict[str, dict[str, Any]] = {
    "scored": {},
    "no_audio": {"audio": None},
    "no_export": {"export": None},
    "no_declaration": {"export_language": None},
    "only_caller_lines": {"export": "only_caller"},
}


@pytest.mark.parametrize("outcome", sorted(OUTCOMES))
def test_every_outcome_of_the_pipeline_round_trips(tmp_path: Path, outcome: str) -> None:
    support = evalcore_support()
    options = dict(OUTCOMES[outcome])
    if options.get("export") == "only_caller":
        options["export"] = support.export_text([("user", support.sentence(support.NAMASKAR))])
    scored = score(tmp_path, **options)
    store = store_mod()
    engine = _open(tmp_path)
    store.store_run(
        engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
    )
    loaded = store.load_run(engine, scored.bundle.id)
    assert loaded.model_dump_json() == scored.bundle.model_dump_json()
    assert [r.status for r in loaded.results] == [r.status for r in scored.bundle.results]
    engine.dispose()


def test_a_failed_stt_and_an_error_result_round_trip_with_their_warning(tmp_path: Path) -> None:
    from tracer_evalcore.stt import SttServerError

    support = evalcore_support()
    failing = support.FakeEngine(
        fail_at=1,
        failure=SttServerError(chunk_index=1, status=503, code="server_error", attempts=4),
    )
    one = score(tmp_path, engine=failing)
    empty = score(
        tmp_path,
        engine=support.FakeEngine(chunk_scripts=[support.ChunkScript(""), support.ChunkScript("")]),
    )
    store = store_mod()
    engine = _open(tmp_path)
    for scored in (one, empty):
        store.store_run(
            engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
        )
        loaded = store.load_run(engine, scored.bundle.id)
        assert loaded.model_dump_json() == scored.bundle.model_dump_json()
    assert store.load_run(engine, one.bundle.id).run.ingest_warnings[-1].code == "stt_failed"
    assert {r.error.code for r in store.load_run(engine, empty.bundle.id).results} == {"no_tokens"}
    engine.dispose()


def test_two_runs_under_one_configuration_share_one_snapshot(tmp_path: Path) -> None:
    support = evalcore_support()
    store = store_mod()
    config = support.configuration()
    first = score(tmp_path, config=config)
    second = score(tmp_path, config=config, project=first.project, agent_target=first.agent_target)
    engine = _open(tmp_path)
    for scored in (first, second):
        store.store_run(
            engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
        )
    assert first.bundle.snapshot.id == second.bundle.snapshot.id
    found = tracer_evaldb.find_snapshot_by_digest(engine, config.snapshot.content_digest)
    assert found is not None
    assert found.id == config.snapshot.id
    assert store.load_run(engine, first.bundle.id) == first.bundle
    assert store.load_run(engine, second.bundle.id) == second.bundle
    engine.dispose()


def test_rescoring_the_same_call_stores_a_second_run(tmp_path: Path) -> None:
    """Finding F14: the Run carries no external reference of the export, so scoring one call
    again (R2 and R3 of the noise-floor measurement) is not an ADR 0022 re-import conflict."""
    support = evalcore_support()
    store = store_mod()
    config = support.configuration()
    runs = [score(tmp_path, config=config)]
    runs.append(
        score(tmp_path, config=config, project=runs[0].project, agent_target=runs[0].agent_target)
    )
    third = support.FakeEngine(
        chunk_scripts=support.scripts(texts=(support.STT_TEXTS[0], support.STT_TEXTS[0]))
    )
    runs.append(
        score(
            tmp_path,
            config=config,
            project=runs[0].project,
            agent_target=runs[0].agent_target,
            engine=third,
        )
    )
    export_ids = {
        next(t for t in r.bundle.transcripts if t.source == "agent_export").provider_call_ref.value
        for r in runs
    }
    assert export_ids == {"5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11"}
    engine = _open(tmp_path)
    for scored in runs:
        store.store_run(
            engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
        )
    assert len({r.bundle.run.id for r in runs}) == 3
    for scored in runs:
        assert store.load_run(engine, scored.bundle.id) == scored.bundle
    engine.dispose()


def test_store_run_stores_the_project_and_agent_target_itself(tmp_path: Path) -> None:
    store = store_mod()
    scored = score(tmp_path)
    engine = _open(tmp_path)
    with pytest.raises(tracer_evaldb.RecordNotStored):
        tracer_evaldb.load_project(engine, scored.project.id)
    store.store_run(
        engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
    )
    assert tracer_evaldb.load_project(engine, scored.project.id) == scored.project
    # a project with the id of a stored one and other content is refused, not overwritten
    other = scored.project.model_copy(update={"name": "another name"})
    other_run = score(tmp_path, project=other, agent_target=scored.agent_target)
    with pytest.raises(Exception) as caught:  # noqa: PT011 (the evaldb conflict type is evaldb's)
        store.store_run(
            engine, project=other, agent_target=scored.agent_target, bundle=other_run.bundle
        )
    assert "another name" not in str(caught.value)
    engine.dispose()


def test_open_sqlite_store_creates_the_directory_and_migrates_to_head(tmp_path: Path) -> None:
    store = store_mod()
    nested = tmp_path / "a" / "b" / "out"
    engine = store.open_sqlite_store(nested)
    info = tracer_evaldb.open_store(engine)  # raises unless the store is at the current head
    assert info.schema_version == "0.0.1"
    assert (nested / store.STORE_FILE_NAME).is_file()
    engine.dispose()
    # a second open on the same directory works and keeps the data
    scored = score(tmp_path)
    first = store.open_sqlite_store(nested)
    store.store_run(
        first, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
    )
    first.dispose()
    second = store.open_sqlite_store(nested)
    assert store.load_run(second, scored.bundle.id) == scored.bundle
    second.dispose()
    with pytest.raises((TypeError, ValueError)):
        store.open_sqlite_store(None)


def test_the_store_module_is_the_only_phase3_code_that_imports_the_store() -> None:
    importers = set()
    packages = CLI_SRC.parents[2]
    for path in sorted(packages.glob("*/src/**/*.py")):
        package = path.relative_to(packages).parts[0]
        if package in ("evalschema", "evaldb"):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "tracer_evaldb" for n in names):
                importers.add(package + "/" + path.name)
    assert importers == {"cli/store.py"}, importers
    _ = Scored


def test_key_never_in_store_and_report_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Store channel (SCOPE Decisions D9; the report channel is P3-23). The canary key is in the
    environment of the process while a run is scored and stored; it must be in no byte of any
    file under the output directory, in no record of the bundle and in no log record."""
    import logging

    from tracer_evalcore.stt import SttServerError

    caplog.set_level(logging.DEBUG)
    support = evalcore_support()
    canary = support.KEY_CANARY
    monkeypatch.setenv("SARVAM_API_KEY", canary)
    store = store_mod()
    out = tmp_path / "out"
    engine = store.open_sqlite_store(out)
    failing = support.FakeEngine(
        fail_at=1,
        failure=SttServerError(chunk_index=1, status=503, code="server_error", attempts=4),
    )
    runs = [score(tmp_path), score(tmp_path, engine=failing)]
    for scored in runs:
        store.store_run(
            engine, project=scored.project, agent_target=scored.agent_target, bundle=scored.bundle
        )
        loaded = store.load_run(engine, scored.bundle.id)
        assert canary not in loaded.model_dump_json()
        assert canary not in scored.bundle.snapshot.model_dump_json()
    engine.dispose()
    files = [p for p in out.rglob("*") if p.is_file()]
    assert any(p.name == store.STORE_FILE_NAME for p in files)
    for path in files:
        data = path.read_bytes()
        assert canary.encode() not in data, path.name
        assert b"SARVAM_API_KEY" not in data, path.name
    assert canary not in caplog.text
    # the call text is what the store holds: the canary check is not vacuous
    stored = (out / store.STORE_FILE_NAME).read_bytes()
    assert support.NAMASKAR.encode("utf-8") in stored
