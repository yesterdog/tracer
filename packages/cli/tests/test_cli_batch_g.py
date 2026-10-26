# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch G, the command-level fixes of the P3-28 security audit: F2, F4, F5, F7, F8 and F9.

Contract: docs/phase3/api-surface-batch-g.md. The command is run in-process through
``tracer_cli.cli.run`` with an injected ``Environment`` (batch E), over the real Sarvam engine on a
scripted ``httpx.MockTransport`` or over an injected fake engine. Nothing opens a socket, nothing
reads the machine's environment, the only key is the canary and all text is invented.

- F2: nothing that can fail on the output side is left for after a paid send, and a failure after
  the send keeps the paid usage (the store, or a recovery file) and writes the report.
- F4: ``--max-chunks`` (required for ``--stt sarvam``, ceiling 200), ``--max-chunk-ms`` <= 30000.
- F5: ``--consent-reference``, ``--consent-recorded-at`` and the consent gate of ``--stt sarvam``.
- F7: the output directory and the call log are private and never opened through a link.
- F8: ``Environment.environ`` and ``EngineRequest.environ`` are not in a repr.
- F9: the env file warning reaches stderr, and its refusals reach the exit code.
"""

from __future__ import annotations

import dataclasses
import io
import os
import stat
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    DENY_LIST,
    SarvamScript,
    base_bodies,
    es,
    load_stored_run,
    price_file_text,
    replay_mod,
    report_files,
    run_cli,
    sarvam_body,
    store_mod,
    write_replay,
)
from cli_batchg_support import (  # type: ignore[import-not-found,unused-ignore]
    RECORDED_AT_UTC,
    RECOVERY_AT,
    REFERENCE,
    REFERENCE_CANARY,
    REPORT_AT,
    ROOT,
    STORE,
    PlantingScript,
    ProbeEngine,
    call_log_lines,
    cli_mod,
    g_args,
    key_environ,
    new_inputs,
    read_recovery,
    report_doc,
    runs_in_store,
    sarvam_run,
    stored_privacy_markers,
    usage_of_stored,
    usage_summary,
    within,
    words,
)

needs_file_modes = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores file modes"
)


def _single_line_error(result: Any, prefix: str) -> None:
    assert result.err.startswith(f"error: {prefix}"), result.err
    assert result.err.count("\n") == 1, result.err
    assert result.out == ""


def _nothing_happened(result: Any, inputs: Any, script: SarvamScript | None = None) -> None:
    """No engine was built or called, no request was sent, nothing was created."""
    assert result.factory_calls == 0
    assert result.out == ""
    assert not inputs.out.exists()
    if script is not None:
        assert script.requests == []


def _control(tmp_path: Path) -> None:
    """The same command with valid values runs: the options exist, so a 64 below is about the
    value and not about an option the parser does not know."""
    inputs = new_inputs(tmp_path, "control")
    result = run_cli(g_args(inputs))
    assert result.code == 0, result.err
    assert result.err == ""


def _usage_error(result: Any, option: str) -> None:
    assert result.code == 64, result.err
    assert result.err.startswith("usage: "), result.err
    assert "unrecognized" not in result.err, result.err
    assert option in result.err.split("tracer: error: ", 1)[1], result.err


def _usage_seconds(pairs: list[tuple[str, str]]) -> Decimal:
    return sum(
        (Decimal(count) for unit, count in pairs if unit == "audio_input_seconds"), Decimal()
    )


# ============================================================================== F4: the ceilings


def test_audit_f4_the_constants_and_the_new_options_are_pinned() -> None:
    cli = cli_mod()
    assert cli.MAX_SARVAM_CHUNKS == 200
    assert cli.MAX_SARVAM_CHUNK_MS == 30_000
    assert cli.MAX_CONSENT_REFERENCE_CHARS == 200
    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    score = action.choices["score"]
    by_flag = {flag: a for a in score._actions for flag in a.option_strings}
    for flag, metavar in (
        ("--max-chunks", "N"),
        ("--consent-reference", "TEXT"),
        ("--consent-recorded-at", "INSTANT"),
    ):
        assert by_flag[flag].required is False, flag
        assert by_flag[flag].default is None, flag
        assert by_flag[flag].metavar == metavar, flag
    usage = score.format_usage()
    for shown in (
        "[--max-chunks N]",
        "[--consent-reference TEXT]",
        "[--consent-recorded-at INSTANT]",
    ):
        assert shown in usage, shown
    assert "200" in by_flag["--max-chunks"].help
    assert "--stt sarvam" in by_flag["--max-chunks"].help
    for flag in ("--max-chunks", "--consent-reference", "--consent-recorded-at"):
        assert DENY_LIST.findall(by_flag[flag].help) == [], flag


def test_audit_f4_sarvam_needs_an_explicit_ceiling(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunks": None}))
    _usage_error(result, "--max-chunks")
    _nothing_happened(result, inputs)
    # a ceiling is also needed with an engine that sends through the real transport
    script = SarvamScript()
    inputs2 = new_inputs(tmp_path, "b")
    second = run_cli(
        g_args(inputs2, options={"--max-chunks": None}),
        production=True,
        transport=script.transport(),
        environ=key_environ(),
    )
    assert second.code == 64
    assert script.requests == []
    assert not inputs2.out.exists()


def test_audit_f4_a_replay_does_not_need_a_ceiling_and_obeys_one_if_given(tmp_path: Path) -> None:
    replay = write_replay(tmp_path / "replay", base_bodies())
    inputs = new_inputs(tmp_path, "a")
    options = {"--stt": f"fake:{replay}", "--max-chunks": None, "--consent": "unknown"}
    options["--consent-reference"] = None
    options["--consent-recorded-at"] = None
    plain = run_cli(g_args(inputs, options=options), production=True)
    assert plain.code == 0, plain.err
    inputs2 = new_inputs(tmp_path, "b")
    capped = run_cli(g_args(inputs2, options={**options, "--max-chunks": "2"}), production=True)
    assert capped.code == 0, capped.err
    inputs3 = new_inputs(tmp_path, "c")
    over = run_cli(g_args(inputs3, options={**options, "--max-chunks": "1"}), production=True)
    assert over.code == 4
    _single_line_error(over, "max_chunks_exceeded")
    assert not inputs3.out.exists()
    # the replay ceiling is not the sarvam ceiling
    inputs4 = new_inputs(tmp_path, "d")
    high = run_cli(g_args(inputs4, options={**options, "--max-chunks": "100000"}), production=True)
    assert high.code == 0, high.err


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc", "", "1e3", "0x10"])
def test_audit_f4_the_ceiling_is_a_positive_whole_number(tmp_path: Path, value: str) -> None:
    _control(tmp_path)
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunks": value}))
    _usage_error(result, "--max-chunks")
    _nothing_happened(result, inputs)


def test_audit_f4_a_sarvam_ceiling_above_200_is_refused(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunks": "201"}))
    assert result.code == 64
    assert "--max-chunks" in result.err
    _nothing_happened(result, inputs)
    inputs2 = new_inputs(tmp_path, "b")
    edge = run_cli(g_args(inputs2, options={"--max-chunks": "200"}))
    assert edge.code == 0, edge.err
    assert edge.factory_calls == 1
    # the dry run applies the same ceiling to its option value
    inputs3 = new_inputs(tmp_path, "c")
    dry = run_cli(g_args(inputs3, options={"--max-chunks": "201"}, extra=["--dry-run"]))
    assert dry.code == 64
    assert not inputs3.out.exists()


def test_audit_f4_a_chunk_window_above_30000_ms_is_refused_for_sarvam_only(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunk-ms": "30001"}))
    assert result.code == 64, result.err
    assert "--max-chunk-ms" in result.err.split("tracer: error: ", 1)[1]
    _nothing_happened(result, inputs)
    inputs2 = new_inputs(tmp_path, "b")
    dry = run_cli(g_args(inputs2, options={"--max-chunk-ms": "30001"}, extra=["--dry-run"]))
    assert dry.code == 64
    inputs3 = new_inputs(tmp_path, "c")
    edge = run_cli(g_args(inputs3, options={"--max-chunk-ms": "30000"}))
    assert edge.code in (0, 2), edge.err
    assert edge.factory_calls == 1
    assert edge.err == ""
    # a replay sends nothing, so the window is the user's to choose
    one = write_replay(
        tmp_path / "one",
        [sarvam_body(" ".join(es().STT_TEXTS), request_id="20261025_replay-0000")],
    )
    inputs4 = new_inputs(tmp_path, "d")
    replay = run_cli(
        g_args(
            inputs4,
            options={
                "--max-chunk-ms": "60000",
                "--search-ms": "5000",
                "--stt": f"fake:{one}",
                "--consent": "unknown",
                "--consent-reference": None,
                "--consent-recorded-at": None,
                "--max-chunks": None,
            },
        ),
        production=True,
    )
    assert replay.code in (0, 2), replay.err
    assert "max-chunk-ms" not in replay.err


def test_audit_f4_a_plan_over_the_ceiling_is_refused_before_any_engine_or_request(
    tmp_path: Path,
) -> None:
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, script=script, options={"--max-chunks": "1"})
    assert result.code == 4, result.err
    _single_line_error(result, "max_chunks_exceeded")
    assert "2" in result.err
    assert "1" in result.err
    assert script.requests == []
    assert not inputs.out.exists()
    assert es().KEY_CANARY not in result.err
    # the same with an injected engine: the factory is never called, the engine never asked
    inputs2 = new_inputs(tmp_path, "b")
    injected = run_cli(g_args(inputs2, options={"--max-chunks": "1"}))
    assert injected.code == 4
    _nothing_happened(injected, inputs2)
    assert injected.engine.calls == []
    # exactly the plan size is allowed
    inputs3 = new_inputs(tmp_path, "c")
    exact, script3 = sarvam_run(inputs3, options={"--max-chunks": "2"})
    assert exact.code == 0, exact.err
    assert len(script3.requests) == 2


def test_audit_f4_the_dry_run_prints_the_cap_check(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunks": "5"}, extra=["--dry-run"]))
    assert result.code == 0, result.err
    assert result.err == ""
    assert result.lines == [
        "chunks: 2",
        "audio seconds: 3",
        "calls: 2",
        "cost: not available (no USD price supplied)",
        "max attempts per chunk: 4",
        "max chunks: 5 (the plan has 2: within the cap)",
        "dry run: nothing was sent",
    ]
    assert result.factory_calls == 0
    assert not inputs.out.exists()
    # exactly the plan size is within the cap
    inputs2 = new_inputs(tmp_path, "b")
    exact = run_cli(g_args(inputs2, options={"--max-chunks": "2"}, extra=["--dry-run"]))
    assert exact.lines[5] == "max chunks: 2 (the plan has 2: within the cap)"


def test_audit_f4_the_dry_run_without_a_ceiling_says_so_and_still_works(tmp_path: Path) -> None:
    # the dry run is how a user learns the number of chunks, so it does not need the ceiling
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunks": None}, extra=["--dry-run"]))
    assert result.code == 0, result.err
    assert result.lines[0] == "chunks: 2"
    assert result.lines[5:] == ["max chunks: not set", "dry run: nothing was sent"]
    assert not inputs.out.exists()


def test_audit_f4_the_dry_run_refuses_a_plan_over_the_ceiling(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--max-chunks": "1"}, extra=["--dry-run"]))
    assert result.code == 4
    _single_line_error(result, "max_chunks_exceeded")
    _nothing_happened(result, inputs)
    # also when the engine is a replay (a ceiling that is given is always obeyed)
    inputs2 = new_inputs(tmp_path, "b")
    replay = run_cli(
        g_args(
            inputs2,
            options={"--stt": "fake:/x/y", "--max-chunks": "1", "--consent": "unknown"},
            extra=["--dry-run"],
        )
    )
    assert replay.code == 4
    _single_line_error(replay, "max_chunks_exceeded")
    _nothing_happened(replay, inputs2)


# ================================================================================ F5: the consent


def test_audit_f5_obtained_consent_needs_a_reference_and_an_instant(tmp_path: Path) -> None:
    _control(tmp_path)
    cases = [
        {"--consent-reference": None},
        {"--consent-recorded-at": None},
        {"--consent-reference": None, "--consent-recorded-at": None},
    ]
    for index, missing in enumerate(cases):
        for dry in (False, True):
            inputs = new_inputs(tmp_path, f"c{index}{int(dry)}")
            extra = ["--dry-run"] if dry else []
            result = run_cli(g_args(inputs, options=missing, extra=extra))
            assert result.code == 64, (missing, result.err)
            assert result.err.startswith("usage: ")
            message = result.err.split("tracer: error: ", 1)[1]
            assert "--consent-reference" in message
            assert "--consent-recorded-at" in message
            assert "unrecognized" not in message
            _nothing_happened(result, inputs)
    # the same for a replay: the record of an obtained consent is complete or it is refused
    replay = write_replay(tmp_path / "r", base_bodies())
    inputs = new_inputs(tmp_path, "replay")
    got = run_cli(
        g_args(
            inputs,
            options={"--stt": f"fake:{replay}", "--consent-reference": None, "--max-chunks": None},
        ),
        production=True,
    )
    assert got.code == 64
    assert "unrecognized" not in got.err
    assert not inputs.out.exists()


@pytest.mark.parametrize("status", ["unknown", "not_obtained"])
def test_audit_f5_other_statuses_never_need_a_reference(tmp_path: Path, status: str) -> None:
    replay = write_replay(tmp_path / "r", base_bodies())
    inputs = new_inputs(tmp_path, "a")
    options = {
        "--stt": f"fake:{replay}",
        "--consent": status,
        "--consent-reference": None,
        "--consent-recorded-at": None,
        "--max-chunks": None,
    }
    result = run_cli(g_args(inputs, options=options), production=True)
    assert result.code == 0, result.err
    markers = _markers(inputs, result)
    assert len(markers) == 3
    for marker in markers:
        assert marker.consent.status == status
        assert marker.consent.reference is None
        assert marker.consent.recorded_at is None


def _markers(inputs: Any, result: Any) -> list[Any]:
    return list(stored_privacy_markers(inputs.out, result.value("bundle")))


def test_audit_f5_the_reference_and_the_instant_are_stored_on_every_privacy_marker(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs))
    assert result.code == 0, result.err
    markers = _markers(inputs, result)
    assert len(markers) == 3, "the recording and both transcripts"
    expected = RECORDED_AT_UTC
    for marker in markers:
        assert marker.consent.status == "obtained"
        assert marker.consent.reference == REFERENCE
        assert marker.consent.recorded_at == expected
    # through the real engine the record is the same
    inputs2 = new_inputs(tmp_path, "b")
    real, _ = sarvam_run(inputs2)
    assert real.code == 0, real.err
    for marker in _markers(inputs2, real):
        assert marker.consent.reference == REFERENCE
        assert marker.consent.recorded_at == expected


def test_audit_f5_the_reference_is_kept_verbatim_with_spaces_and_non_ascii(
    tmp_path: Path,
) -> None:
    text = "\u092e\u0930\u093e\u0920\u0940 consent, call 7: ok (v2)"
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--consent-reference": text}))
    assert result.code == 0, result.err
    for marker in _markers(inputs, result):
        assert marker.consent.reference == text
    # the longest reference is 200 characters, the shortest one
    for index, value in enumerate(("x", "r" * 200)):
        folder = new_inputs(tmp_path, f"len{index}")
        got = run_cli(g_args(folder, options={"--consent-reference": value}))
        assert got.code == 0, got.err
        assert _markers(folder, got)[0].consent.reference == value


def test_audit_f5_a_reference_given_with_another_status_is_stored_as_given(
    tmp_path: Path,
) -> None:
    replay = write_replay(tmp_path / "r", base_bodies())
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(
        g_args(
            inputs,
            options={
                "--stt": f"fake:{replay}",
                "--consent": "not_obtained",
                "--max-chunks": None,
            },
        ),
        production=True,
    )
    assert result.code == 0, result.err
    for marker in _markers(inputs, result):
        assert marker.consent.status == "not_obtained"
        assert marker.consent.reference == REFERENCE


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "r" * 201,
        "a\x00b",
        "a\nb",
        "a\tb",
        "a\x7fb",
        "a\x85b",
        "a\u202eb",
        "a\u2066b",
        "a\u200db",
        "a\u200cb",
        "a\u2028b",
        "a\u2029b",
        "a\ufeffb",
    ],
)
def test_audit_f5_the_reference_is_bounded_and_display_safe(tmp_path: Path, value: str) -> None:
    _control(tmp_path)
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--consent-reference": value}))
    _usage_error(result, "--consent-reference")
    assert "rrrrrrrrrr" not in result.err, "the value is not echoed"
    _nothing_happened(result, inputs)
    assert all(ord(c) < 128 and (c == "\n" or c >= " ") for c in result.err)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-20",
        "2026-10-20 10:00:00Z",
        "2026-10-20T10:00:00",
        "yesterday",
        "",
        "20261020T100000Z",
    ],
)
def test_audit_f5_the_instant_is_an_rfc3339_instant(tmp_path: Path, value: str) -> None:
    _control(tmp_path)
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options={"--consent-recorded-at": value}))
    _usage_error(result, "--consent-recorded-at")
    _nothing_happened(result, inputs)


def test_audit_f5_a_consent_cannot_be_recorded_after_the_run(tmp_path: Path) -> None:
    run_at = es().CREATED_AT
    assert run_at == datetime(2026, 10, 25, 9, 0, 0, tzinfo=UTC)
    inputs = new_inputs(tmp_path, "a")
    late = run_cli(g_args(inputs, options={"--consent-recorded-at": "2026-10-25T09:00:01Z"}))
    assert late.code == 4, late.err
    _single_line_error(late, "consent_recorded_at_after_run")
    _nothing_happened(late, inputs)
    # the same instant, and the same instant written in another zone, are not after the run
    inputs2 = new_inputs(tmp_path, "b")
    equal = run_cli(g_args(inputs2, options={"--consent-recorded-at": "2026-10-25T14:30:00+05:30"}))
    assert equal.code == 0, equal.err


def test_audit_f5_the_reference_is_never_printed_or_reported_only_stored(tmp_path: Path) -> None:
    reference = f"owner statement {REFERENCE_CANARY}"
    inputs = new_inputs(tmp_path, "a")
    real, script = sarvam_run(inputs, options={"--consent-reference": reference})
    assert real.code == 0, real.err
    assert REFERENCE_CANARY not in real.out + real.err
    assert REFERENCE_CANARY not in report_files(inputs.out)[0].read_text("utf-8")
    assert REFERENCE_CANARY not in (inputs.out / "sarvam_call_log.jsonl").read_text("ascii")
    for request in script.requests:
        assert REFERENCE_CANARY.encode() not in request.content
        assert REFERENCE_CANARY not in str(request.headers)
    # not vacuous: the stored record holds it
    assert _markers(inputs, real)[0].consent.reference == reference
    # the dry run does not echo it either
    inputs2 = new_inputs(tmp_path, "b")
    dry = run_cli(g_args(inputs2, options={"--consent-reference": reference}, extra=["--dry-run"]))
    assert dry.code == 0
    assert REFERENCE_CANARY not in dry.out + dry.err


@pytest.mark.parametrize("status", ["unknown", "not_obtained"])
def test_audit_f5_sarvam_is_refused_unless_consent_is_obtained(tmp_path: Path, status: str) -> None:
    options = {"--consent": status, "--consent-reference": None, "--consent-recorded-at": None}
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, script=script, options=options)
    assert result.code == 4, result.err
    _single_line_error(result, "consent_not_obtained")
    assert "--consent obtained" in result.err
    assert script.requests == []
    assert not inputs.out.exists()
    # also for an injected engine, and also for the dry run (it is a plan error)
    inputs2 = new_inputs(tmp_path, "b")
    injected = run_cli(g_args(inputs2, options=options))
    assert injected.code == 4
    _single_line_error(injected, "consent_not_obtained")
    _nothing_happened(injected, inputs2)
    inputs3 = new_inputs(tmp_path, "c")
    dry = run_cli(g_args(inputs3, options=options, extra=["--dry-run"]))
    assert dry.code == 4
    _single_line_error(dry, "consent_not_obtained")
    _nothing_happened(dry, inputs3)


def test_audit_f5_the_gate_is_decided_before_any_file_is_read(tmp_path: Path) -> None:
    _control(tmp_path)
    options = {
        "--consent": "unknown",
        "--consent-reference": None,
        "--consent-recorded-at": None,
        "--transcript": str(tmp_path / "no-such-export.txt"),
        "--rubric": str(tmp_path / "no-such-rubric.json"),
    }
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs, options=options))
    assert result.code == 4
    _single_line_error(result, "consent_not_obtained")
    # a usage error comes first, then the gate
    missing_ceiling = run_cli(g_args(inputs, options={**options, "--max-chunks": None}))
    _usage_error(missing_ceiling, "--max-chunks")


def test_audit_f5_a_replay_is_never_gated_by_consent(tmp_path: Path) -> None:
    replay = write_replay(tmp_path / "r", base_bodies())
    for index, status in enumerate(("unknown", "not_obtained")):
        inputs = new_inputs(tmp_path, f"c{index}")
        options = {
            "--stt": f"fake:{replay}",
            "--consent": status,
            "--consent-reference": None,
            "--consent-recorded-at": None,
            "--max-chunks": None,
        }
        real = run_cli(g_args(inputs, options=options), production=True)
        assert real.code == 0, real.err
        inputs2 = new_inputs(tmp_path, f"d{index}")
        dry = run_cli(g_args(inputs2, options=options, extra=["--dry-run"]), production=True)
        assert dry.code == 0, dry.err
        assert dry.lines[5] == "max chunks: not set"


# ===================================================================== F2: before the paid send


@pytest.mark.parametrize("dry", [False, True])
def test_audit_f2_a_question_mark_in_the_output_path_is_refused_before_anything(
    tmp_path: Path, dry: bool
) -> None:
    extra = ["--dry-run"] if dry else []
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    leaf = tmp_path / "a" / "out?x"
    result, _ = sarvam_run(inputs, script=script, options={"--out": str(leaf)}, extra=extra)
    assert result.code == 4, result.err
    _single_line_error(result, "output_path_invalid")
    assert script.requests == []
    assert sorted(p.name for p in (tmp_path / "a").iterdir()) == ["in"]
    # a question mark in a parent directory: the parents are not created either
    inputs2 = new_inputs(tmp_path, "b")
    nested = tmp_path / "b" / "q?" / "out"
    injected = run_cli(g_args(inputs2, options={"--out": str(nested)}, extra=extra))
    assert injected.code == 4
    _single_line_error(injected, "output_path_invalid")
    assert injected.factory_calls == 0
    assert sorted(p.name for p in (tmp_path / "b").iterdir()) == ["in"]


@needs_file_modes
def test_audit_f2_an_output_directory_that_cannot_be_written_is_refused_before_the_engine(
    tmp_path: Path,
) -> None:
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    inputs.out.mkdir()
    inputs.out.chmod(0o500)
    try:
        result, _ = sarvam_run(inputs, script=script)
        injected = run_cli(g_args(inputs))
        names = sorted(p.name for p in inputs.out.iterdir())
    finally:
        inputs.out.chmod(0o700)
    assert result.code == 4, result.err
    _single_line_error(result, "output_unavailable")
    assert script.requests == []
    assert injected.code == 4
    _single_line_error(injected, "output_unavailable")
    assert injected.factory_calls == 0
    assert injected.engine.calls == []
    assert names == [], "the command wrote nothing into the directory"


@needs_file_modes
def test_audit_f2_a_new_output_directory_under_an_unwritable_parent_is_refused(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        script = SarvamScript()
        result, _ = sarvam_run(inputs, script=script, options={"--out": str(parent / "out")})
        exists = (parent / "out").exists()
    finally:
        parent.chmod(0o700)
    assert result.code == 4, result.err
    _single_line_error(result, "output_unavailable")
    assert script.requests == []
    assert not exists


def test_audit_f2_a_store_file_that_cannot_be_opened_is_refused_before_the_engine(
    tmp_path: Path,
) -> None:
    # tracer.sqlite is a directory
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "dir")
    (inputs.out / STORE).mkdir(parents=True)
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 4, result.err
    _single_line_error(result, "output_unavailable")
    assert script.requests == []
    assert report_files(inputs.out) == []
    # a store file that is plainly not usable is found by the probe: the engine is not even built
    folder_in_the_way = new_inputs(tmp_path, "dir2")
    (folder_in_the_way.out / STORE).mkdir(parents=True)
    probed = run_cli(g_args(folder_in_the_way))
    assert probed.code == 4
    assert probed.factory_calls == 0
    # tracer.sqlite is not a database: refused, the file is not touched, nothing is sent
    script2 = SarvamScript()
    inputs2 = new_inputs(tmp_path, "garbage")
    inputs2.out.mkdir()
    junk = b"this is not a database file " * 40
    (inputs2.out / STORE).write_bytes(junk)
    result2, _ = sarvam_run(inputs2, script=script2)
    assert result2.code == 4, result2.err
    _single_line_error(result2, "output_unavailable")
    assert script2.requests == []
    assert (inputs2.out / STORE).read_bytes() == junk
    assert report_files(inputs2.out) == []
    # the same with an injected engine: transcribe is never called
    inputs3 = new_inputs(tmp_path, "garbage2")
    inputs3.out.mkdir()
    (inputs3.out / STORE).write_bytes(junk)
    injected = run_cli(g_args(inputs3))
    assert injected.code == 4
    assert injected.engine.calls == []


@needs_file_modes
def test_audit_f2_a_read_only_store_file_is_refused_before_the_engine(tmp_path: Path) -> None:
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    inputs.out.mkdir()
    store = inputs.out / STORE
    store.write_bytes(b"")
    store.chmod(0o400)
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 4, result.err
    _single_line_error(result, "output_unavailable")
    assert script.requests == []
    inputs2 = new_inputs(tmp_path, "b")
    inputs2.out.mkdir()
    (inputs2.out / STORE).write_bytes(b"")
    (inputs2.out / STORE).chmod(0o400)
    probed = run_cli(g_args(inputs2))
    assert probed.code == 4
    assert probed.factory_calls == 0


def test_audit_f2_an_existing_report_name_is_refused_before_the_engine(tmp_path: Path) -> None:
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    inputs.out.mkdir()
    (inputs.out / REPORT_AT).write_text("an earlier report", "ascii")
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 4, result.err
    _single_line_error(result, "report_exists")
    assert script.requests == []
    assert (inputs.out / REPORT_AT).read_text("ascii") == "an earlier report"
    assert sorted(p.name for p in inputs.out.iterdir()) == [REPORT_AT]
    # a dangling link under the report name counts as existing
    inputs2 = new_inputs(tmp_path, "b")
    inputs2.out.mkdir()
    (inputs2.out / REPORT_AT).symlink_to(tmp_path / "nowhere.html")
    injected = run_cli(g_args(inputs2))
    assert injected.code == 4
    assert injected.factory_calls == 0
    assert not (tmp_path / "nowhere.html").exists()


def test_audit_f2_the_store_is_open_and_the_directory_exists_before_the_engine_is_called(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    probe = ProbeEngine(inputs.out)
    previous = os.umask(0o000)
    try:
        result = run_cli(g_args(inputs), engine=probe)
    finally:
        os.umask(previous)
    assert result.code == 0, result.err
    (preflight,) = probe.named("preflight")
    (transcribe,) = probe.named("transcribe")
    # the plan is known and nothing the command writes exists yet when the engine is asked to
    # check it ...
    assert preflight == ("preflight", 2, False, False)
    # ... and the output directory (private) and a migrated store exist when the send begins
    assert transcribe == ("transcribe", 2, True, True, 0o700)
    assert probe.closed == 1
    assert [e[0] for e in probe.events] == ["preflight", "transcribe"]


def test_audit_f2_an_engine_refusal_before_the_send_leaves_nothing_behind(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    probe = ProbeEngine(inputs.out, refuse_in_preflight=True)
    result = run_cli(g_args(inputs), engine=probe)
    assert result.code == 4, result.err
    assert "probe_refused" in result.err
    assert result.err.count("\n") == 1
    assert result.out == ""
    assert probe.named("transcribe") == []
    assert probe.closed == 1
    assert not inputs.out.exists()


@pytest.mark.parametrize(
    ("count", "refused"),
    [(10_000, False), (10_001, True), (12_000, True)],
)
def test_audit_f2_the_export_is_checked_against_the_alignment_cap_before_the_send(
    tmp_path: Path, count: int, refused: bool
) -> None:
    support = es()
    export = support.export_text([("assistant", words(count))])
    inputs = new_inputs(tmp_path, "a", export=export)
    script = SarvamScript()
    result, _ = sarvam_run(inputs, script=script)
    if refused:
        assert result.code == 4, result.err
        _single_line_error(result, "export_tokens_above_alignment_cap")
        assert str(count) in result.err
        assert "10000" in result.err
        assert script.requests == []
        assert not inputs.out.exists()
        # an injected engine is not built either, and the dry run refuses the same way
        inputs2 = new_inputs(tmp_path, "b", export=export)
        injected = run_cli(g_args(inputs2))
        assert injected.code == 4
        _nothing_happened(injected, inputs2)
        inputs3 = new_inputs(tmp_path, "c", export=export)
        dry = run_cli(g_args(inputs3, extra=["--dry-run"]))
        assert dry.code == 4
        _single_line_error(dry, "export_tokens_above_alignment_cap")
        _nothing_happened(dry, inputs3)
    else:
        assert result.code in (0, 2), result.err
        assert len(script.requests) == 2


def test_audit_f2_the_cap_counts_both_roles_and_tokens_not_whitespace_words(
    tmp_path: Path,
) -> None:
    support = es()
    both = support.export_text(
        [("assistant", words(6_000)), ("user", words(6_000, "v")), ("assistant", "x")]
    )
    inputs = new_inputs(tmp_path, "a", export=both)
    result = run_cli(g_args(inputs))
    assert result.code == 4, result.err
    _single_line_error(result, "export_tokens_above_alignment_cap")
    # no space at all: one whitespace word, ten thousand and one tokens
    glued = support.export_text([("assistant", ",".join(f"w{i}" for i in range(10_001)))])
    inputs2 = new_inputs(tmp_path, "b", export=glued)
    second = run_cli(g_args(inputs2))
    assert second.code == 4, second.err
    _single_line_error(second, "export_tokens_above_alignment_cap")
    assert "10001" in second.err


def test_audit_f2_a_replay_is_not_refused_for_the_size_of_the_export(tmp_path: Path) -> None:
    # nothing is paid for with --stt fake:, so the call is scored as far as it can be
    replay = write_replay(tmp_path / "r", base_bodies())
    export = es().export_text([("assistant", words(12_000)), ("user", "x")])
    inputs = new_inputs(tmp_path, "a", export=export)
    result = run_cli(
        g_args(
            inputs,
            options={
                "--stt": f"fake:{replay}",
                "--consent": "unknown",
                "--consent-reference": None,
                "--consent-recorded-at": None,
                "--max-chunks": None,
            },
        ),
        production=True,
    )
    assert result.code == 2, result.err
    assert result.value("not scored") == "input too large to compare"
    assert len(report_files(inputs.out)) == 1
    assert runs_in_store(inputs.out) == 1


def test_audit_f2_a_speech_side_over_the_cap_after_the_send_is_stored_and_reported(
    tmp_path: Path,
) -> None:
    # the speech-to-text side cannot be known before the send: the paid answer is kept, the call
    # is not scored, and nothing is sent again
    bodies = [
        sarvam_body(words(10_001), request_id="20261025_big-0000"),
        base_bodies()[1],
    ]
    script = SarvamScript(bodies)
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 2, result.err
    assert result.value("not scored") == "input too large to compare"
    assert len(script.requests) == 2
    assert len(call_log_lines(inputs.out)) == 4
    assert result.value("stt usage") == "2 requests, 3 audio seconds"
    assert len(report_files(inputs.out)) == 1
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert len(stored.usage_records) == 2


# ----------------------------------------------------------------- failures after the paid send


def _boom_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("STOREFAILURE " + es().KEY_CANARY)

    monkeypatch.setattr(store_mod(), "store_run", boom)


def test_audit_f2_a_store_failure_after_the_send_keeps_the_usage_and_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    inputs = new_inputs(tmp_path, "a")
    result, script = sarvam_run(inputs)
    out = inputs.out
    assert result.code == 1
    assert result.err == "error: unexpected failure (RuntimeError)\n"
    # the audio was sent once per chunk and never again
    assert len(script.requests) == 2
    assert [line["phase"] for line in call_log_lines(out)] == ["before", "after", "before", "after"]
    # the result block is printed and says what was and was not written
    assert result.value("result") == "scored"
    assert result.value("stt usage") == "2 requests, 3 audio seconds"
    assert result.value("store") == "not written"
    assert result.value("report") == str(out / REPORT_AT)
    assert result.value("usage recovery") == str(out / RECOVERY_AT)
    # the report is written from the run that exists in memory
    doc = report_doc(out)
    assert doc.by_id("usage-requests").text().strip() == "2"
    assert doc.by_id("status").attrs["data-outcome"] == "scored"
    assert stat.S_IMODE((out / REPORT_AT).stat().st_mode) == 0o600
    # the recovery file holds the paid usage
    recovery = read_recovery(out)
    assert recovery["format"] == "tracer.usage_recovery/1"
    assert recovery["failed_step"] == "store"
    assert recovery["error_class"] == "RuntimeError"
    assert recovery["created_at"] == "2026-10-25T09:00:00.000000Z"
    pairs = usage_summary(recovery["usage_records"])
    assert [unit for unit, _ in pairs].count("requests") == 2
    assert all(count == "1" for unit, count in pairs if unit == "requests")
    assert _usage_seconds(pairs) == Decimal(3)
    from tracer_evalschema.usage import UsageRecord

    records = [UsageRecord.model_validate(r) for r in recovery["usage_records"]]
    assert {r.provider for r in records} == {"sarvam"}
    assert {r.model for r in records} == {"saaras:v4"}
    assert len({r.run_id for r in records}) == 1
    assert stat.S_IMODE((out / RECOVERY_AT).stat().st_mode) == 0o600
    assert runs_in_store(out) == 0
    # nothing leaks: not the failure text, not the key
    everything = result.out + result.err + (out / RECOVERY_AT).read_text("ascii")
    assert "STOREFAILURE" not in everything
    assert es().KEY_CANARY not in everything
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [REPORT_AT, RECOVERY_AT, STORE, "sarvam_call_log.jsonl"]
    )


def test_audit_f2_the_recovery_file_is_what_a_clean_run_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    price = tmp_path / "price.json"
    price.write_text(price_file_text(), "utf-8")
    options = {"--price-file": str(price)}
    clean_inputs = new_inputs(tmp_path, "clean")
    clean, _ = sarvam_run(clean_inputs, options=options)
    assert clean.code == 0, clean.err
    stored = load_stored_run(clean_inputs.out, clean.value("bundle"))
    _boom_store(monkeypatch)
    inputs = new_inputs(tmp_path, "failed")
    failed, _ = sarvam_run(inputs, options=options)
    assert failed.code == 1
    recovery = read_recovery(inputs.out)
    assert usage_summary(recovery["usage_records"]) == usage_of_stored(
        clean_inputs.out, clean.value("bundle")
    )
    # the cost of the paid chunks is kept too, as the schema records
    from tracer_evalschema.cost import CostRecord, CostSummary

    records = [CostRecord.model_validate(r) for r in recovery["cost_records"]]
    summaries = [CostSummary.model_validate(s) for s in recovery["cost_summaries"]]
    assert len(records) == len(stored.cost_records) == 2
    assert len(summaries) == 1
    assert summaries[0].total == stored.cost_summaries[0].total
    assert sorted(str(r.amount) for r in records) == sorted(
        str(r.amount) for r in stored.cost_records
    )
    assert len(recovery["pricing_catalogs"]) == 1
    assert failed.value("cost") == clean.value("cost")


def test_audit_f2_a_report_failure_after_the_send_keeps_the_store(tmp_path: Path) -> None:
    inputs = new_inputs(tmp_path, "a")
    planted = inputs.out / REPORT_AT
    script = PlantingScript(planted)
    result, _ = sarvam_run(inputs, script=script)
    out = inputs.out
    assert result.code == 1
    assert result.err == "error: unexpected failure (FileExistsError)\n"
    assert len(script.requests) == 2
    assert planted.read_text("ascii") == "planted", "an existing file is never overwritten"
    assert result.value("report") == "not written"
    assert result.value("store") == str(out / STORE)
    stored = load_stored_run(out, result.value("bundle"))
    assert len(stored.usage_records) == 2
    assert not (out / RECOVERY_AT).exists(), "the store already holds the usage"
    assert "usage recovery" not in result.out


def test_audit_f2_when_the_store_and_the_report_both_fail_the_usage_is_still_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    inputs = new_inputs(tmp_path, "a")
    planted = inputs.out / REPORT_AT
    script = PlantingScript(planted)
    result, _ = sarvam_run(inputs, script=script)
    out = inputs.out
    assert result.code == 1
    assert result.err.startswith("error: unexpected failure (")
    assert len(script.requests) == 2
    assert result.value("store") == "not written"
    assert result.value("report") == "not written"
    assert result.value("usage recovery") == str(out / RECOVERY_AT)
    assert len(read_recovery(out)["usage_records"]) == 2
    assert planted.read_text("ascii") == "planted"


def test_audit_f2_a_failure_inside_the_pipeline_after_the_send_keeps_the_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("COSTFAILURE")

    monkeypatch.setattr(cli_mod(), "compute_stt_cost", boom)
    inputs = new_inputs(tmp_path, "a")
    result, script = sarvam_run(inputs)
    out = inputs.out
    assert result.code == 1
    assert result.err == "error: unexpected failure (RuntimeError)\n"
    assert len(script.requests) == 2
    assert result.out == f"usage recovery: {out / RECOVERY_AT}\n"
    recovery = read_recovery(out)
    assert recovery["failed_step"] == "pipeline"
    assert recovery["error_class"] == "RuntimeError"
    assert len(recovery["usage_records"]) == 2
    assert recovery["cost_records"] == []
    assert recovery["cost_summaries"] == []
    assert recovery["pricing_catalogs"] == []
    assert report_files(out) == []
    assert runs_in_store(out) == 0
    assert "COSTFAILURE" not in result.out + result.err + (out / RECOVERY_AT).read_text("ascii")


def test_audit_f2_a_failure_after_an_injected_engine_answered_never_asks_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs))
    assert result.code == 1
    assert len(result.engine.calls) == 1, "the engine is asked once, whatever happens later"
    assert result.factory_calls == 1
    assert len(read_recovery(inputs.out)["usage_records"]) == 2


def test_audit_f2_the_usage_of_the_chunks_that_were_paid_is_kept_when_a_later_chunk_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    script = SarvamScript([base_bodies()[0], 400])
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 1
    assert len(script.requests) == 2
    assert result.value("result") == "not scored"
    pairs = usage_summary(read_recovery(inputs.out)["usage_records"])
    assert [unit for unit, _ in pairs].count("requests") == 1, "the answered chunk only"


def test_audit_f2_a_recovery_file_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    inputs = new_inputs(tmp_path, "a")
    inputs.out.mkdir()
    (inputs.out / RECOVERY_AT).write_text("earlier", "ascii")
    result, script = sarvam_run(inputs)
    assert result.code == 1
    assert len(script.requests) == 2
    assert (inputs.out / RECOVERY_AT).read_text("ascii") == "earlier"
    assert result.value("usage recovery") == "not written"


def test_audit_f2_nothing_paid_means_no_recovery_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _boom_store(monkeypatch)
    script = SarvamScript([401])
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 1
    assert len(script.requests) == 1, "an authentication failure is not retried"
    assert result.value("usage recovery") == "not written"
    assert result.value("store") == "not written"
    assert not (inputs.out / RECOVERY_AT).exists()
    assert len(report_files(inputs.out)) == 1, "the report of the failed call is still written"


def _ignored(path: str) -> bool:
    git = "git"
    proc = subprocess.run(  # noqa: S603
        [git, "check-ignore", "--no-index", "-q", "--", path], cwd=ROOT, check=False
    )
    assert proc.returncode in (0, 1), proc
    return proc.returncode == 0


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("out/usage-recovery-20261025T090000Z.json", True),
        ("deep/er/usage-recovery-20261231T235959Z.json", True),
        ("usage-recovery.json", False),
        ("usage-recovery-notes.md", False),
        ("docs/usage-recovery-20261025T090000Z.md", False),
    ],
)
def test_audit_f2_gitignore_lists_the_recovery_file(path: str, ignored: bool) -> None:
    assert _ignored(path) is ignored


def test_audit_f2_a_missing_key_still_leaves_no_directory_behind(tmp_path: Path) -> None:
    # the factory (the key) comes before the store: the order of the batch B and E guarantee
    script = SarvamScript()
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, script=script, environ={})
    assert result.code == 4, result.err
    assert "key_missing" in result.err
    assert result.out == ""
    assert script.requests == []
    assert not inputs.out.exists()


def test_audit_f2_the_store_connection_is_released_once_whatever_happens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[int] = []
    real_open = store_mod().open_sqlite_store

    def spying_open(out_dir: Any) -> Any:
        engine = real_open(out_dir)
        original = engine.dispose

        def dispose(*args: Any, **kwargs: Any) -> Any:
            disposed.append(1)
            return original(*args, **kwargs)

        engine.dispose = dispose
        return engine

    monkeypatch.setattr(store_mod(), "open_sqlite_store", spying_open)
    inputs = new_inputs(tmp_path, "ok")
    assert run_cli(g_args(inputs)).code == 0
    assert disposed == [1]
    # after a failure in the pipeline, and after a failure to store
    disposed.clear()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(store_mod(), "store_run", boom)
    inputs2 = new_inputs(tmp_path, "store")
    assert run_cli(g_args(inputs2)).code == 1
    assert disposed == [1]
    disposed.clear()
    monkeypatch.setattr(cli_mod(), "compute_stt_cost", boom)
    inputs3 = new_inputs(tmp_path, "pipeline")
    assert run_cli(g_args(inputs3)).code == 1
    assert disposed == [1]


def test_audit_f9_only_the_env_file_warning_is_forwarded_to_stderr(tmp_path: Path) -> None:
    import warnings

    def factory(request: Any) -> Any:
        warnings.warn("something else entirely", UserWarning, stacklevel=1)
        return es().FakeEngine()

    inputs = new_inputs(tmp_path, "a")
    cli = cli_mod()
    stdout, stderr = io.StringIO(), io.StringIO()
    environment = cli.Environment(
        stdout=stdout,
        stderr=stderr,
        environ={},
        clock=lambda: es().CREATED_AT,
        sleep=lambda seconds: None,
        engine_factory=factory,
    )
    code = cli.run(g_args(inputs), environment)
    assert code == 0, stderr.getvalue()
    assert stderr.getvalue() == ""


def test_audit_f2_the_replay_engine_checks_its_directory_against_the_plan_in_preflight(
    tmp_path: Path,
) -> None:
    from tracer_evalcore.audio.chunker import chunk_pcm
    from tracer_evalcore.stt import SttConfigurationError

    plan = chunk_pcm(
        b"\x00\x00" * 24000, sample_rate_hz=8000, channels=1, max_chunk_ms=2000, search_ms=1000
    )
    assert len(plan.chunks) == 2
    replay = replay_mod()
    good = write_replay(tmp_path / "good", base_bodies())
    engine = replay.ReplayStt(good)
    engine.preflight(plan)
    stale = write_replay(tmp_path / "stale", [*base_bodies(), base_bodies()[0]])
    with pytest.raises(SttConfigurationError) as raised:
        replay.ReplayStt(stale).preflight(plan)
    assert raised.value.reason == "replay_extra_file"
    # a file that is missing is not a stale directory: it is a failed transcription, later
    short = write_replay(tmp_path / "short", base_bodies()[:1])
    replay.ReplayStt(short).preflight(plan)
    # the directory vanished after the engine was built
    gone = write_replay(tmp_path / "gone", base_bodies())
    built = replay.ReplayStt(gone)
    for child in gone.iterdir():
        child.unlink()
    gone.rmdir()
    with pytest.raises(SttConfigurationError) as vanished:
        built.preflight(plan)
    assert vanished.value.reason == "replay_directory_unreadable"


# ==================================================================== F7: private, no links


def test_audit_f7_the_directory_the_command_creates_is_private_and_so_are_its_files(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    previous = os.umask(0o000)
    try:
        result, _ = sarvam_run(inputs)
    finally:
        os.umask(previous)
    assert result.code == 0, result.err
    assert stat.S_IMODE(inputs.out.stat().st_mode) == 0o700
    for name in (STORE, "sarvam_call_log.jsonl", REPORT_AT):
        assert stat.S_IMODE((inputs.out / name).stat().st_mode) == 0o600, name


def test_audit_f7_a_linked_call_log_is_refused_by_the_command_before_any_request(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    inputs.out.mkdir()
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep me", "ascii")
    (inputs.out / "sarvam_call_log.jsonl").symlink_to(target)
    script = SarvamScript()
    result, _ = sarvam_run(inputs, script=script)
    assert result.code == 4, result.err
    _single_line_error(result, "output_unavailable")
    assert script.requests == []
    assert target.read_text("ascii") == "keep me"
    assert not (inputs.out / STORE).exists()
    assert report_files(inputs.out) == []


# ============================================================================ F8: not in a repr


def test_audit_f8_environ_is_not_in_the_repr_of_the_environment_or_the_request(
    tmp_path: Path,
) -> None:
    cli = cli_mod()
    key = es().KEY_CANARY
    environment = cli.Environment(
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        environ={"SARVAM_API_KEY": key},
        clock=lambda: es().CREATED_AT,
        sleep=lambda seconds: None,
    )
    request = cli.EngineRequest(
        stt="sarvam",
        out_dir=tmp_path / "out",
        model="saaras:v4",
        environ={"SARVAM_API_KEY": key},
        env_file=None,
        clock=lambda: es().CREATED_AT,
        sleep=lambda seconds: None,
        transport=None,
    )
    for item in (environment, request):
        for text in (repr(item), str(item), f"{item}", format(item)):
            assert key not in text
            assert "SARVAM_API_KEY" not in text
            assert "environ=" not in text
        assert item.environ["SARVAM_API_KEY"] == key, "the field itself is unchanged"
    # the repr still shows the other fields (it is not emptied)
    assert "stt='sarvam'" in repr(request)
    assert "model='saaras:v4'" in repr(request)
    assert "stdout=" in repr(environment)
    for cls in (cli.Environment, cli.EngineRequest):
        fields = {f.name: f for f in dataclasses.fields(cls)}
        assert fields["environ"].repr is False
        assert all(f.repr for name, f in fields.items() if name != "environ")


def test_audit_f8_the_request_the_command_builds_has_no_environ_in_its_repr(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_cli(g_args(inputs), environ=key_environ())
    assert result.code == 0, result.err
    (request,) = result.requests
    assert request.environ == key_environ()
    assert es().KEY_CANARY not in repr(request)


# ================================================================================ F9: env file


def _env_file(folder: Path, mode: int = 0o600, body: bytes | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "secret-keys.env"
    path.write_bytes(body if body is not None else f"SARVAM_API_KEY={es().KEY_CANARY}\n".encode())
    path.chmod(mode)
    return path


def test_audit_f9_a_readable_env_file_earns_exactly_one_non_secret_warning_on_stderr(
    tmp_path: Path,
) -> None:
    env_file = _env_file(tmp_path / "k", 0o644)
    inputs = new_inputs(tmp_path, "a")
    result, script = sarvam_run(inputs, options={"--env-file": str(env_file)}, environ={})
    assert result.code == 0, result.err
    assert result.err == (
        "warning: the env file can be read by other users (mode 0o644); run chmod 600 on it\n"
    )
    assert {r.headers["api-subscription-key"] for r in script.requests} == {es().KEY_CANARY}
    for text in (result.out, result.err):
        assert es().KEY_CANARY not in text
        assert "secret-keys" not in text
    assert str(tmp_path) not in result.err
    assert DENY_LIST.findall(result.err) == []


def test_audit_f9_an_owner_only_env_file_earns_no_warning(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path / "k", 0o600)
    inputs = new_inputs(tmp_path, "a")
    result, _ = sarvam_run(inputs, options={"--env-file": str(env_file)}, environ={})
    assert result.code == 0, result.err
    assert result.err == ""


@pytest.mark.parametrize("kind", ["too_large", "directory", "fifo", "device"])
def test_audit_f9_an_env_file_that_is_too_large_or_not_a_regular_file_is_refused(
    tmp_path: Path, kind: str
) -> None:
    folder = tmp_path / "k"
    folder.mkdir()
    fifo: Path | None = None
    if kind == "too_large":
        body = f"SARVAM_API_KEY={es().KEY_CANARY}\n".encode() + b"#" * 70_000
        path = _env_file(folder, 0o600, body)
        reason = "env_file_too_large"
    elif kind == "directory":
        path = folder / "dir.env"
        path.mkdir()
        reason = "env_file_not_regular"
    elif kind == "fifo":
        path = folder / "pipe.env"
        os.mkfifo(path, 0o600)
        fifo = path
        reason = "env_file_not_regular"
    else:
        path = Path(os.devnull)
        reason = "env_file_not_regular"
    inputs = new_inputs(tmp_path, "a")
    script = SarvamScript()
    try:
        result, _ = within(
            10.0,
            lambda: sarvam_run(
                inputs, script=script, options={"--env-file": str(path)}, environ={}
            ),
        )
    finally:
        if fifo is not None:
            try:
                descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                pass
            else:
                os.close(descriptor)
    assert result.code == 4, result.err
    _single_line_error(result, "")
    assert reason in result.err
    assert str(path) not in result.err
    assert script.requests == []
    assert not inputs.out.exists()


# ==================================================================================== end to end


def test_audit_a_normal_real_send_still_works_and_asks_for_an_uncompressed_reply(
    tmp_path: Path,
) -> None:
    inputs = new_inputs(tmp_path, "a")
    result, script = sarvam_run(inputs)
    assert result.code == 0, result.err
    assert result.err == ""
    assert result.value("result") == "scored"
    assert len(script.requests) == 2
    for request in script.requests:
        assert request.headers.get_list("accept-encoding") == ["identity"]
        assert request.headers["api-subscription-key"] == es().KEY_CANARY
    assert sorted(p.name for p in inputs.out.iterdir()) == sorted(
        [STORE, REPORT_AT, "sarvam_call_log.jsonl"]
    )
    assert runs_in_store(inputs.out) == 1
    assert RECOVERY_AT not in {p.name for p in inputs.out.iterdir()}
