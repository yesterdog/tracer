# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-11: the local call log and the pre-send plan of ``tracer score --dry-run`` (contract
docs/phase3/api-surface-batch-b.md section 9; ADR 0024 rules 15 and 16, invariant I8).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MESSAGE_CANARY,
    SRC,
    TEXT_CANARY,
    TWO,
    Rig,
    calllog_mod,
    catalog,
    context,
    dryrun_mod,
    entry,
    error_body,
    hand_plan,
    json_response,
    mapping_mod,
    ok_body,
    secrets_mod,
    small_plan,
)

LINE_KEYS = {
    "at",
    "phase",
    "chunk",
    "attempt",
    "bytes_sent",
    "audio_seconds",
    "status",
    "error_kind",
    "elapsed_ms",
    "request_id",
}
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def _lines(rig: Rig) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = rig.log_lines
    return lines


# ----------------------------------------------------------------------- the log lines


def test_call_log_has_no_text_no_key(tmp_path: Path) -> None:
    plan = small_plan()
    evil_id = f"id\n{TEXT_CANARY} with spaces"
    steps: list[Any] = [
        json_response(
            ok_body(
                f"secret words {TEXT_CANARY}", request_id="ok-1", phrases=[(TEXT_CANARY, 0, 0.5)]
            )
        ),
        json_response(
            error_body(
                "rate_limit_exceeded_error", message=f"{MESSAGE_CANARY} {TEXT_CANARY}", rid="err-1"
            ),
            status=429,
        ),
        httpx.ReadTimeout(f"timeout {CANARY_KEY}"),
        json_response(ok_body(f"more {TEXT_CANARY}", request_id=evil_id)),
        json_response(
            error_body(message=f"{MESSAGE_CANARY}", rid=f"bad id {CANARY_KEY}"), status=400
        ),
    ]
    rig = Rig(tmp_path, steps)
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is not None
    assert outcome.failure.chunk_index == 2
    raw = rig.log.path.read_bytes()
    for canary in (CANARY_KEY, TEXT_CANARY, MESSAGE_CANARY):
        assert canary.encode() not in raw, canary
    text = raw.decode("ascii")
    for word in (
        "secret",
        "hello",
        "words",
        "transcript",
        "message",
        "api-subscription",
        "Authorization",
    ):
        assert word not in text, word
    assert str(tmp_path) not in text
    assert "chunk-" not in text
    assert ".wav" not in text
    assert "with spaces" not in text
    lines = _lines(rig)
    assert len(lines) == 2 * len(rig.transport.requests) == 10
    for line in lines:
        assert set(line) == LINE_KEYS
        assert STAMP.match(line["at"]), line["at"]
        assert line["phase"] in {"before", "after"}
        assert type(line["chunk"]) is int
        assert type(line["attempt"]) is int
        assert type(line["bytes_sent"]) is int
        assert line["bytes_sent"] > 0
        assert re.fullmatch(
            r"0|[1-9][0-9]*|[1-9][0-9]*\.[0-9]{0,17}[1-9]|0\.[0-9]{0,17}[1-9]",
            line["audio_seconds"],
        )
        assert line["request_id"] is None or re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", line["request_id"]
        )
        assert line["error_kind"] is None or re.fullmatch(r"[a-z0-9_]{1,64}", line["error_kind"])
    # each line is one line
    assert text.count("\n") == len(lines)
    assert text.endswith("\n")
    # the facts per attempt, in order
    after = [x for x in lines if x["phase"] == "after"]
    assert [
        (x["chunk"], x["attempt"], x["status"], x["error_kind"], x["request_id"]) for x in after
    ] == [
        (0, 1, 200, None, "ok-1"),
        (1, 1, 429, None, "err-1"),
        (1, 2, None, "read_timeout", None),
        (1, 3, 200, None, None),
        (2, 1, 400, None, None),
    ]


def test_call_log_bytes_audio_seconds_and_chunk_match_the_request(tmp_path: Path) -> None:
    plan = small_plan()
    rig = Rig(tmp_path, [json_response(ok_body(request_id=f"r{i}")) for i in range(3)])
    rig.engine().transcribe(plan, context("mr"))
    lines = _lines(rig)
    for index, request in enumerate(rig.transport.requests):
        pair = [x for x in lines if x["chunk"] == index]
        assert [x["phase"] for x in pair] == ["before", "after"]
        for line in pair:
            assert line["bytes_sent"] == len(request.content), "the bytes of the request body"
            chunk = plan.chunks[index]
            assert (
                line["audio_seconds"]
                == mapping_mod().usage_audio_seconds(chunk.frames, chunk.sample_rate_hz)[0]
            ), "the usage rule: exact or rounded half-even to 18 digits"
            assert line["attempt"] == 1
        assert pair[1]["request_id"] == f"r{index}"


def test_call_log_written_before_and_after_each_send(tmp_path: Path) -> None:
    seen: list[list[dict[str, Any]]] = []
    holder: dict[str, Rig] = {}

    def observe(status: int) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            # while the request is in flight, the log already holds its "before" line, on disk
            text = holder["rig"].log.path.read_text("ascii")
            seen.append([json.loads(line) for line in text.splitlines()])
            if status == 200:
                return json_response(  # type: ignore[no-any-return,unused-ignore]
                    ok_body(request_id=f"in-flight-{len(seen)}")
                )
            return json_response(error_body(), status=status)  # type: ignore[no-any-return,unused-ignore]

        return handler

    rig = Rig(tmp_path, [observe(503), httpx.ConnectError("x"), observe(200)])
    holder["rig"] = rig
    chunk = small_plan().chunks[1]
    rig.client.send_chunk(chunk)
    assert len(seen) == 2
    assert [(x["phase"], x["attempt"]) for x in seen[0]] == [("before", 1)]
    assert [(x["phase"], x["attempt"]) for x in seen[1]] == [
        ("before", 1),
        ("after", 1),
        ("before", 2),
        ("after", 2),
        ("before", 3),
    ]
    assert len(rig.transport.requests) == 3
    final = _lines(rig)
    assert [(x["phase"], x["attempt"], x["chunk"]) for x in final] == [
        ("before", 1, 1),
        ("after", 1, 1),
        ("before", 2, 1),
        ("after", 2, 1),
        ("before", 3, 1),
        ("after", 3, 1),
    ]
    befores = [x for x in final if x["phase"] == "before"]
    for line in befores:
        assert line["status"] is None
        assert line["error_kind"] is None
        assert line["elapsed_ms"] is None
        assert line["request_id"] is None
    assert [x["error_kind"] for x in final if x["phase"] == "after"] == [
        None,
        "connect_error",
        None,
    ]
    assert [x["status"] for x in final if x["phase"] == "after"] == [503, None, 200]


def test_call_log_a_crash_mid_request_leaves_the_before_line(tmp_path: Path) -> None:
    class Crash(BaseException):
        pass

    rig = Rig(tmp_path, [json_response(ok_body()), Crash()])
    plan = small_plan()
    rig.client.send_chunk(plan.chunks[0])
    with pytest.raises(Crash):
        rig.client.send_chunk(plan.chunks[1])
    final = _lines(rig)
    assert [(x["phase"], x["chunk"]) for x in final] == [
        ("before", 0),
        ("after", 0),
        ("before", 1),
    ], "a billable call that never answered still has its trace"


def test_call_log_elapsed_milliseconds_and_timestamps_come_from_the_injected_clocks(
    tmp_path: Path,
) -> None:
    rig = Rig(
        tmp_path,
        [json_response(error_body(), status=503), httpx.ReadTimeout("x"), json_response(ok_body())],
    )
    rig.client.send_chunk(small_plan().chunks[0])
    final = _lines(rig)
    afters = [x for x in final if x["phase"] == "after"]
    assert [x["elapsed_ms"] for x in afters] == [250, 250, 250], "floor((after - before) * 1000)"
    assert rig.ticker.calls == 6, "the monotonic clock is read twice per attempt"
    stamps = [x["at"] for x in final]
    assert stamps == [f"2026-10-25T09:00:0{i}.123456Z" for i in range(1, 7)], "wall clock, UTC"


def test_call_log_is_private_and_append_only(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "out"
    previous = os.umask(0o000)
    try:
        log = calllog_mod().CallLog.open(out)
    finally:
        os.umask(previous)
    assert log.path == out / "sarvam_call_log.jsonl"
    mode = stat.S_IMODE(log.path.stat().st_mode)
    assert mode == 0o600, oct(mode)
    log.before(chunk_index=0, attempt=1, bytes_sent=10, audio_seconds="1.5")
    log.close()
    log.close()  # closing twice is harmless
    again = calllog_mod().CallLog.open(out)
    again.before(chunk_index=1, attempt=1, bytes_sent=11, audio_seconds="2")
    again.close()
    lines = [json.loads(x) for x in log.path.read_text("ascii").splitlines()]
    assert [x["chunk"] for x in lines] == [0, 1], "an existing log is appended to, never truncated"
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600


def test_call_log_only_ever_names_a_safe_request_id(tmp_path: Path) -> None:
    log = calllog_mod().CallLog.open(tmp_path / "o")
    for number, rid in enumerate(
        [
            "ok-1",
            "20261023_9e4bb02d-dcc0-4fbc-85ed-fca178c17115",
            "a b",
            "x\ny",
            "\u00e9",
            "",
            "x" * 129,
            None,
        ]
    ):
        log.after(
            chunk_index=number,
            attempt=1,
            bytes_sent=1,
            audio_seconds="1",
            status=200,
            error_kind=None,
            elapsed_ms=1,
            request_id=rid,
        )
    log.close()
    ids = [json.loads(x)["request_id"] for x in log.path.read_text("ascii").splitlines()]
    assert ids == [
        "ok-1",
        "20261023_9e4bb02d-dcc0-4fbc-85ed-fca178c17115",
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_call_log_module_imports_only_the_standard_library() -> None:
    tree = ast.parse((SRC / "calllog.py").read_text("utf-8"))
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops |= {a.name.split(".")[0] for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    assert tops <= set(sys.stdlib_module_names), tops - set(sys.stdlib_module_names)


# --------------------------------------------------------------------------- dry run


def test_dry_run_sends_nothing_and_prints_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry run must not do this")

    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.MockTransport, "handle_request", forbidden)
    monkeypatch.setattr(secrets_mod(), "load_api_key", forbidden)
    monkeypatch.setattr(calllog_mod().CallLog, "open", forbidden)
    monkeypatch.setattr(calllog_mod().CallLog, "__init__", forbidden)

    plan = small_plan()  # 2.5 s at 24 kHz: 3 chunks
    dry = dryrun_mod().build_dry_run_plan(plan, model="saaras:v4")
    assert (dry.chunks, dry.calls, dry.total_frames, dry.sample_rate_hz) == (3, 3, 60_000, 24_000)
    assert dry.audio_seconds == Decimal("2.5")
    assert dry.audio_seconds_rounding == "exact"
    assert dry.cost is None, "no USD price supplied: no cost"
    assert dry.render() == (
        "chunks: 3\naudio seconds: 2.5\ncalls: 3\ncost: not available (no USD price supplied)"
    )
    assert list(tmp_path.iterdir()) == [], "a dry run writes no file"
    assert (dry.max_chunk_ms, dry.search_ms) == (plan.max_chunk_ms, plan.search_ms)
    with pytest.raises(dataclasses.FrozenInstanceError):
        dry.chunks = 4
    parameters = set(inspect.signature(dryrun_mod().build_dry_run_plan).parameters)
    assert parameters == {"plan", "model", "catalog", "priced_at"}, "no key, no transport, no path"


def test_dry_run_prints_the_rounded_total_when_the_quotient_does_not_end() -> None:
    plan = hand_plan([(0, 600_000), (600_000, 6_110_401)])  # 254.600041666...
    dry = dryrun_mod().build_dry_run_plan(plan, model="saaras:v4")
    assert dry.chunks == 2
    assert dry.calls == 2
    assert dry.total_frames == 6_110_401
    assert dry.audio_seconds_rounding == "half_even_18"
    assert dry.audio_seconds == Decimal("254.600041666666666667")
    lines = dry.render().splitlines()
    assert lines[1] == "audio seconds: 254.600041666666666667 (rounded half-even to 18 digits)"


def test_dry_run_of_no_audio_prints_zero() -> None:
    dry = dryrun_mod().build_dry_run_plan(hand_plan([]), model="saaras:v4")
    assert (dry.chunks, dry.calls) == (0, 0)
    assert dry.render().splitlines()[:3] == ["chunks: 0", "audio seconds: 0", "calls: 0"]


def test_dry_run_cost_comes_only_from_a_user_supplied_usd_price() -> None:
    plan = hand_plan(TWO)  # 25 s + 12.5 s
    priced_at = datetime(2026, 10, 24, tzinfo=UTC)
    dry = dryrun_mod().build_dry_run_plan(
        plan, model="saaras:v4", catalog=catalog(), priced_at=priced_at
    )
    assert dry.cost is not None
    assert dry.cost.status == "estimated"
    assert dry.cost.amount == "0.00375"
    assert dry.cost.na_reason is None
    assert (
        dry.render().splitlines()[3] == "cost: 0.00375 USD (computed from the supplied USD price)"
    )
    # no price date: not guessed
    undated = dryrun_mod().build_dry_run_plan(plan, model="saaras:v4", catalog=catalog())
    assert undated.cost.status == "not_available"
    assert undated.cost.amount is None
    assert undated.cost.na_reason == "ambiguous_price"
    assert undated.render().splitlines()[3] == "cost: not available (ambiguous_price)"
    # another model, or only audio priced: not available, never a partial amount
    other = dryrun_mod().build_dry_run_plan(
        plan, model="saaras:v3", catalog=catalog(), priced_at=priced_at
    )
    assert other.cost.na_reason == "no_price_for_model"
    audio_only = catalog([entry("audio_input_seconds", "60", "0.006")])
    partial = dryrun_mod().build_dry_run_plan(
        plan, model="saaras:v4", catalog=audio_only, priced_at=priced_at
    )
    assert partial.cost.status == "not_available"
    assert partial.cost.amount is None
    assert partial.cost.na_reason == "unit_unpriced"


def test_dry_run_module_reads_no_key_and_opens_no_connection() -> None:
    tree = ast.parse((SRC / "dryrun.py").read_text("utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "httpx",
        "socket",
        "ssl",
        "os",
        "tracer_evalproviders.secrets",
        "tracer_evalproviders.calllog",
    }
    forbidden |= {"tracer_evalproviders.sarvam.client", "tracer_evalproviders.sarvam.engine"}
    assert not imported & forbidden, imported & forbidden
    assert "SARVAM_API_KEY" not in (SRC / "dryrun.py").read_text("utf-8")


def test_dry_run_leaves_the_module_graph_free_of_the_network_stack() -> None:
    code = (
        "import sys; import tracer_evalproviders.dryrun as d; "
        "print(sorted(m for m in ('httpx','httpcore','h11','ssl','socket') if m in sys.modules))"
    )
    import subprocess

    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "httpx" not in done.stdout
    assert "httpcore" not in done.stdout
    assert "h11" not in done.stdout


def test_call_log_only_ever_names_a_short_machine_key_as_the_error_kind(tmp_path: Path) -> None:
    log = calllog_mod().CallLog.open(tmp_path / "o")
    kinds = ["read_timeout", "connect_error", "a b", "Read_Timeout", "x\ny", "k" * 65, "", None]
    for number, kind in enumerate(kinds):
        log.after(
            chunk_index=number,
            attempt=1,
            bytes_sent=1,
            audio_seconds="1",
            status=None,
            error_kind=kind,
            elapsed_ms=1,
            request_id=None,
        )
    log.close()
    got = [json.loads(x)["error_kind"] for x in log.path.read_text("ascii").splitlines()]
    assert got == ["read_timeout", "connect_error", None, None, None, None, None, None]


def test_call_log_an_existing_file_with_a_looser_mode_is_tightened(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "sarvam_call_log.jsonl"
    existing.write_text("", "ascii")
    existing.chmod(0o666)
    log = calllog_mod().CallLog.open(out)
    log.close()
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600
