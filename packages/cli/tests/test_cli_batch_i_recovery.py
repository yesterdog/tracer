# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch I, the usage recovery findings of the P3-28 re-audit: R1 and R2.

Contract: docs/phase3/api-surface-batch-i.md sections 1 and 2. The command is run in-process
through ``tracer_cli.cli.run`` over the real Sarvam engine on a scripted ``httpx.MockTransport``
(the transport raises where a real one could), or over an injected engine that follows the
streaming protocol of the contract. Nothing opens a socket, nothing reads the machine's
environment, the only key is the canary and all text is invented.

- R1: the usage of every chunk that was answered is kept as the send goes on, so that an
  exception of the transport or the engine, a ``KeyboardInterrupt``, an ``OSError`` of the call
  log or a Ctrl-C during the alignment still leaves ``usage-recovery-<UTC>.json``; the exception
  is then raised again; the audio of an answered chunk is never sent twice.
- R2: a recovery file that cannot be written is said so (``usage recovery: not written``) in the
  pipeline path, and a failure of the store path writer that is not an ``OSError`` costs nothing.
"""

from __future__ import annotations

import errno
import importlib
import stat
import types
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    SarvamScript,
    cli_mod,
    es,
    load_stored_run,
    replay_mod,
    report_files,
    store_mod,
)
from cli_batchg_support import (  # type: ignore[import-not-found,unused-ignore]
    g_args,
    usage_summary,
)
from cli_batchi_support import (  # type: ignore[import-not-found,unused-ignore]
    CALL_LOG,
    FOUR_CHUNKS,
    RECOVERY_AT,
    REPORT_AT,
    STORE,
    Captured,
    FailingScript,
    FlaggedOffEngine,
    PlainEngine,
    StreamingFake,
    answered_in_call_log,
    call_log,
    four_bodies,
    names,
    new_inputs,
    read_recovery,
    recovery_chunks,
    recovery_request_ids,
    run_capture,
    runs_in_store,
    sarvam_capture,
)

LEAK = "TRANSPORTCANARY"


def _leak_free(result: Captured, out: Path) -> None:
    """Neither the text of the failure nor the key is on any output or in the recovery file."""
    everything = result.out + result.err
    for path in out.iterdir():
        if path.is_file() and path.name != STORE:
            everything += path.read_text("utf-8", errors="replace")
    assert LEAK not in everything
    assert es().KEY_CANARY not in everything


def _seconds(document: dict[str, Any]) -> Decimal:
    return sum(
        (
            Decimal(str(q["count"]))
            for r in document["usage_records"]
            for q in r["quantities"]
            if q["unit"] == "audio_input_seconds"
        ),
        Decimal(),
    )


def _kept(result: Captured, out: Path, *, chunks: list[int], error_class: str) -> dict[str, Any]:
    """The recovery file of a pipeline step failure that answered ``chunks`` before it."""
    document: dict[str, Any] = read_recovery(out)
    assert document["format"] == "tracer.usage_recovery/1"
    assert document["failed_step"] == "pipeline"
    assert document["error_class"] == error_class
    assert document["created_at"] == "2026-10-25T09:00:00.000000Z"
    assert recovery_chunks(document) == chunks
    assert [
        (q["unit"], str(q["count"]))
        for r in document["usage_records"]
        for q in r["quantities"]
        if q["unit"] == "requests"
    ] == [("requests", "1")] * len(chunks)
    assert {r["provider"] for r in document["usage_records"]} == {"sarvam"}
    assert len({r["run_id"] for r in document["usage_records"]}) == 1
    assert document["cost_records"] == []
    assert document["cost_summaries"] == []
    assert document["pricing_catalogs"] == []
    assert stat.S_IMODE((out / RECOVERY_AT).stat().st_mode) == 0o600
    # the file holds what the call log recorded as answered, chunk for chunk and id for id
    answered = answered_in_call_log(out)
    assert [chunk for chunk, _ in answered] == chunks
    assert [rid for _, rid in answered] == recovery_request_ids(document)
    # the announcement is the only thing on stdout
    assert result.out == f"usage recovery: {out / RECOVERY_AT}\n"
    # no run exists: no report, no stored run
    assert report_files(out) == []
    assert runs_in_store(out) == 0
    _leak_free(result, out)
    return document


def _unexpected(result: Captured, error_class: str) -> None:
    assert result.error is None
    assert result.code == 1
    assert result.err == f"error: unexpected failure ({error_class})\n"


# ============================================================ R1: the exception on chunk 2


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError(f"{LEAK} message"),
        ValueError(f"{LEAK} message"),
        OSError(errno.EIO, f"{LEAK} message"),
        ZeroDivisionError(f"{LEAK} message"),
        LookupError(f"{LEAK} message"),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_r1_an_exception_of_the_transport_on_chunk_2_keeps_the_usage_of_chunk_1(
    tmp_path: Path, exc: Exception
) -> None:
    inputs = new_inputs(tmp_path, "a")
    script = FailingScript(1, exc)
    result, _ = sarvam_capture(inputs, script=script)
    _unexpected(result, type(exc).__name__)
    document = _kept(result, inputs.out, chunks=[0], error_class=type(exc).__name__)
    # the audio of the answered chunk was sent once, the failing request was the second one
    assert len(script.requests) == 2
    assert [(line["chunk"], line["phase"]) for line in call_log(inputs.out)] == [
        (0, "before"),
        (0, "after"),
        (1, "before"),
    ]
    first = next(
        line for line in call_log(inputs.out) if line["phase"] == "before" and line["chunk"] == 0
    )
    assert _seconds(document) == Decimal(first["audio_seconds"])
    assert names(inputs.out) == sorted([RECOVERY_AT, STORE, CALL_LOG])


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_r1_the_usage_of_every_answered_chunk_is_kept_whatever_chunk_fails(
    tmp_path: Path, fail_at: int
) -> None:
    inputs = new_inputs(tmp_path, "a")
    script = FailingScript(fail_at, RuntimeError(LEAK), four_bodies())
    result, _ = sarvam_capture(inputs, script=script, options=FOUR_CHUNKS)
    _unexpected(result, "RuntimeError")
    document = _kept(result, inputs.out, chunks=list(range(fail_at)), error_class="RuntimeError")
    assert len(document["usage_records"]) == fail_at
    assert len(script.requests) == fail_at + 1, "no answered chunk was sent again"


def test_r1_an_exception_on_chunk_1_pays_nothing_and_writes_no_recovery_file(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    script = FailingScript(0, RuntimeError(LEAK))
    result, _ = sarvam_capture(inputs, script=script)
    _unexpected(result, "RuntimeError")
    assert result.out == "", "nothing was answered, so there is nothing to recover"
    assert len(script.requests) == 1
    assert names(inputs.out) == sorted([STORE, CALL_LOG])
    assert report_files(inputs.out) == []
    _leak_free(result, inputs.out)


# ============================================================ R1: Ctrl-C


def test_r1_a_ctrl_c_on_chunk_2_keeps_the_usage_of_chunk_1_and_is_raised_again(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    interrupt = KeyboardInterrupt()
    script = FailingScript(1, interrupt)
    result, _ = sarvam_capture(inputs, script=script)
    assert result.error is interrupt, "the Ctrl-C leaves run() and is not turned into an exit code"
    assert result.code is None
    assert result.err == "", "no message is written for a Ctrl-C"
    _kept(result, inputs.out, chunks=[0], error_class="KeyboardInterrupt")
    assert len(script.requests) == 2
    assert names(inputs.out) == sorted([RECOVERY_AT, STORE, CALL_LOG])


def test_r1_a_ctrl_c_on_chunk_1_pays_nothing_and_writes_no_recovery_file(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    interrupt = KeyboardInterrupt()
    script = FailingScript(0, interrupt)
    result, _ = sarvam_capture(inputs, script=script)
    assert result.error is interrupt
    assert result.out == ""
    assert result.err == ""
    assert names(inputs.out) == sorted([STORE, CALL_LOG])


def test_r1_a_ctrl_c_on_the_last_of_four_chunks_keeps_the_three_that_were_answered(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    interrupt = KeyboardInterrupt()
    script = FailingScript(3, interrupt, four_bodies())
    result, _ = sarvam_capture(inputs, script=script, options=FOUR_CHUNKS)
    assert result.error is interrupt
    _kept(result, inputs.out, chunks=[0, 1, 2], error_class="KeyboardInterrupt")
    assert len(script.requests) == 4


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, RuntimeError])
def test_r1_a_failure_during_the_alignment_keeps_the_usage_of_both_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc_type: type[BaseException]
) -> None:
    pipeline = importlib.import_module("tracer_evalcore.pipeline")
    raised = exc_type()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise raised

    monkeypatch.setattr(pipeline, "score_results", boom)
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    if exc_type is KeyboardInterrupt:
        assert result.error is raised
        assert result.err == ""
    else:
        _unexpected(result, "RuntimeError")
    _kept(result, inputs.out, chunks=[0, 1], error_class=exc_type.__name__)
    assert len(script.requests) == 2, "the audio is sent once per chunk, whatever happens later"


def test_r1_the_engine_is_closed_once_after_a_ctrl_c(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    engine = StreamingFake(die_after=1, exc=KeyboardInterrupt())
    result = run_capture(g_args(inputs), engine=engine)
    assert isinstance(result.error, KeyboardInterrupt)
    assert engine.closed == 1
    assert runs_in_store(inputs.out) == 0


# ============================================================ R1: the call log


def _fail_write(monkeypatch: pytest.MonkeyPatch, number: int) -> None:
    """The ``number``-th line the call log is asked to write fails with ``OSError``."""
    calllog = importlib.import_module("tracer_evalproviders.calllog")
    real = calllog.CallLog._write
    seen = {"n": 0}

    def write(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen["n"] += 1
        if seen["n"] == number:
            raise OSError(errno.ENOSPC, f"{LEAK} no space")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(calllog.CallLog, "_write", write)


def test_r1_a_call_log_error_before_chunk_2_keeps_the_usage_of_chunk_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_write(monkeypatch, 3)  # before, after of chunk 1 and then the before line of chunk 2
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    _unexpected(result, "OSError")
    _kept(result, inputs.out, chunks=[0], error_class="OSError")
    assert len(script.requests) == 1, "the line is written before the request, so none was sent"


def test_r1_a_call_log_error_after_chunk_2_was_sent_keeps_only_the_answer_that_was_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reply of chunk 2 arrived but its 'after' line could not be written: the reply is lost
    # before it is read, so the usage of chunk 2 is not known and is not guessed.
    _fail_write(monkeypatch, 4)
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    _unexpected(result, "OSError")
    _kept(result, inputs.out, chunks=[0], error_class="OSError")
    assert len(script.requests) == 2


# ============================================================ R1: the streaming protocol


def test_r1_the_command_hands_an_engine_that_streams_usage_a_callback(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    engine = StreamingFake()
    result = run_capture(g_args(inputs), engine=engine)
    assert result.code == 0, result.err
    (call,) = engine.calls
    assert callable(call[2]), "on_usage is passed by keyword"
    # nothing is counted twice: the stored run has the two records the engine made
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert len(stored.usage_records) == 2
    assert not (inputs.out / RECOVERY_AT).exists()


def test_r1_an_engine_that_streams_usage_and_dies_leaves_the_records_it_handed_out(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    engine = StreamingFake(die_after=1)
    result = run_capture(g_args(inputs), engine=engine)
    _unexpected(result, "RuntimeError")
    document = read_recovery(inputs.out)
    assert document["failed_step"] == "pipeline"
    assert [r["id"] for r in document["usage_records"]] == [str(r.id) for r in engine.handed]
    assert len(engine.handed) == 1
    assert result.out == f"usage recovery: {inputs.out / RECOVERY_AT}\n"
    assert engine.closed == 1
    assert len(engine.calls) == 1, "the engine is asked once"


def test_r1_a_failure_after_the_engine_returned_does_not_count_a_chunk_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = importlib.import_module("tracer_evalcore.pipeline")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(LEAK)

    monkeypatch.setattr(pipeline, "score_results", boom)
    inputs = new_inputs(tmp_path, "a")
    engine = StreamingFake()
    result = run_capture(g_args(inputs), engine=engine)
    _unexpected(result, "RuntimeError")
    document = read_recovery(inputs.out)
    assert len(document["usage_records"]) == 2, "streamed once and returned once, kept once"
    assert [r["id"] for r in document["usage_records"]] == [str(r.id) for r in engine.handed]


def test_r1_an_engine_of_the_old_protocol_is_called_as_before(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    engine = PlainEngine()
    result = run_capture(g_args(inputs), engine=engine)
    assert result.code == 0, result.err
    assert engine.calls == 1
    inputs2 = new_inputs(tmp_path, "b")
    off = FlaggedOffEngine()
    result2 = run_capture(g_args(inputs2), engine=off)
    assert result2.code == 0, result2.err
    assert off.kwargs == [{}], "streams_usage false: no on_usage is passed"


def test_r1_an_engine_of_the_old_protocol_that_fails_has_no_usage_to_keep(tmp_path: Path) -> None:
    class Dies(PlainEngine):  # type: ignore[misc,unused-ignore]
        def transcribe(self, plan: Any, context: Any) -> Any:
            self.calls += 1
            raise RuntimeError(LEAK)

    inputs = new_inputs(tmp_path, "a")
    engine = Dies()
    result = run_capture(g_args(inputs), engine=engine)
    _unexpected(result, "RuntimeError")
    assert result.out == ""
    assert not (inputs.out / RECOVERY_AT).exists()
    assert engine.calls == 1


def test_r1_an_engine_of_the_old_protocol_that_answered_keeps_its_usage_on_a_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = importlib.import_module("tracer_evalcore.pipeline")
    interrupt = KeyboardInterrupt()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise interrupt

    monkeypatch.setattr(pipeline, "score_results", boom)
    inputs = new_inputs(tmp_path, "a")
    engine = PlainEngine()
    result = run_capture(g_args(inputs), engine=engine)
    assert result.error is interrupt
    assert engine.calls == 1
    assert engine.closed == 1
    document = read_recovery(inputs.out)
    assert document["failed_step"] == "pipeline"
    assert document["error_class"] == "KeyboardInterrupt"
    assert len(document["usage_records"]) == 2, "the usage the engine returned is kept"
    assert result.out == f"usage recovery: {inputs.out / RECOVERY_AT}\n"


def test_r1_the_replay_engine_does_not_stream_usage() -> None:
    replay = replay_mod().ReplayStt
    assert getattr(replay, "streams_usage", False) is False


# ============================================================ R1: after the result exists


def test_r1_a_ctrl_c_while_the_store_is_written_keeps_the_usage_and_is_raised_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The answer is in memory and paid for; the store was not written: the usage is kept.
    # No report is written for a Ctrl-C (contract section 1.6).
    interrupt = KeyboardInterrupt()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise interrupt

    monkeypatch.setattr(store_mod(), "store_run", boom)
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    assert result.error is interrupt
    assert result.err == ""
    document = read_recovery(inputs.out)
    assert document["failed_step"] == "store"
    assert document["error_class"] == "KeyboardInterrupt"
    assert len(document["usage_records"]) == 2
    assert len(document["cost_summaries"]) == 1, "the run exists, so its cost records are kept"
    assert result.out == f"usage recovery: {inputs.out / RECOVERY_AT}\n"
    assert report_files(inputs.out) == []
    assert len(script.requests) == 2


# ============================================================ R2: a recovery that cannot be written


def test_r2_a_recovery_file_that_cannot_be_created_is_said_in_the_pipeline_path(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    inputs.out.mkdir(mode=0o700)
    (inputs.out / RECOVERY_AT).write_text("earlier", "ascii")
    script = FailingScript(1, RuntimeError(LEAK))
    result, _ = sarvam_capture(inputs, script=script)
    _unexpected(result, "RuntimeError")
    assert result.out == "usage recovery: not written\n"
    assert (inputs.out / RECOVERY_AT).read_text("ascii") == "earlier"
    assert len(script.requests) == 2


def test_r2_a_writer_that_fails_with_another_error_is_said_and_hides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_writer(monkeypatch, "json")
    inputs = new_inputs(tmp_path, "a")
    script = FailingScript(1, RuntimeError(LEAK))
    result, _ = sarvam_capture(inputs, script=script)
    # the error the user sees is the one that ended the send, not the one of the writer
    _unexpected(result, "RuntimeError")
    assert result.out == "usage recovery: not written\n"
    assert not (inputs.out / RECOVERY_AT).exists(), "no half written file is left"
    assert len(script.requests) == 2


def test_r2_a_ctrl_c_is_still_raised_when_the_recovery_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_writer(monkeypatch, "json")
    inputs = new_inputs(tmp_path, "a")
    interrupt = KeyboardInterrupt()
    result, _ = sarvam_capture(inputs, script=FailingScript(1, interrupt))
    assert result.error is interrupt
    assert result.out == "usage recovery: not written\n"
    assert result.err == ""


def _break_writer(monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    """Make ``tracer_cli.cli._write_recovery`` fail with a ``TypeError`` (not an ``OSError``)."""
    cli = cli_mod()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise TypeError(f"{LEAK} not serialisable")

    if how == "json":
        monkeypatch.setattr(cli, "json", types.SimpleNamespace(dumps=boom))
    else:
        monkeypatch.setattr(cli, "_dump", boom)


def _boom_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"STOREFAILURE {es().KEY_CANARY}")

    monkeypatch.setattr(store_mod(), "store_run", boom)


@pytest.mark.parametrize("patched", ["json", "dump"])
def test_r2_a_store_path_writer_that_fails_with_another_error_still_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patched: str
) -> None:
    _boom_store(monkeypatch)
    _break_writer(monkeypatch, patched)
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    out = inputs.out
    assert result.error is None, "a TypeError of the writer must not leave run()"
    assert result.code == 1
    assert result.err == "error: unexpected failure (RuntimeError)\n"
    assert len(script.requests) == 2
    assert result.value("result") == "scored"
    assert result.value("store") == "not written"
    assert result.value("report") == str(out / REPORT_AT)
    assert result.value("usage recovery") == "not written"
    assert len(report_files(out)) == 1
    assert not (out / RECOVERY_AT).exists()
    assert "STOREFAILURE" not in result.out + result.err
    assert es().KEY_CANARY not in result.out + result.err


def test_r2_a_store_path_writer_that_fails_does_not_stop_the_report_of_an_injected_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    _break_writer(monkeypatch, "json")
    inputs = new_inputs(tmp_path, "a")
    result = run_capture(g_args(inputs))
    assert result.code == 1
    assert result.value("usage recovery") == "not written"
    assert len(result.engine.calls) == 1
    assert len(report_files(inputs.out)) == 1


# ============================================================ the unchanged behaviour (guards)


def test_r1_a_clean_run_leaves_no_recovery_file_and_one_record_per_chunk(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    assert result.code == 0, result.err
    assert result.err == ""
    assert not (inputs.out / RECOVERY_AT).exists()
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert len(stored.usage_records) == 2
    pairs = usage_summary([r.model_dump(mode="json") for r in stored.usage_records])
    assert [unit for unit, _ in pairs].count("requests") == 2
    assert len(script.requests) == 2
