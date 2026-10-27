# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 (step 6, the provider half): the speech-to-text engine of a simulated run.

Contract: docs/phase4/api-surface-batch-e.md section 9. ``create_sarvam_stt_for_run`` builds the
Sarvam engine whose request lines go into the ONE call log of the run (ADR 0024 amendment A8,
``sim_call_log.jsonl``), whose key is the role key ``stt`` (ADR 0024 A4), whose model is explicit
(ADR 0026 rule 12: no default) and which does not own the log (the run closes it).
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_support import simlog_mod  # type: ignore[import-not-found,unused-ignore]
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    ScriptedTransport,
    Sleeps,
    Ticker,
    context,
    json_response,
    ok_body,
    small_plan,
)
from tracer_evalcore.stt import SttConfigurationError, SttKeyMissingError


def engine_mod() -> Any:
    return importlib.import_module("tracer_evalproviders.sarvam.engine")


def make(tmp_path: Path, steps: list[Any], **options: Any) -> tuple[Any, Any, Any]:
    transport = ScriptedTransport(steps)
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    engine = engine_mod().create_sarvam_stt_for_run(
        call_log=log,
        model="saaras:v4",
        environ={"SARVAM_API_KEY": CANARY_KEY},
        transport=transport,
        sleep=Sleeps(),
        monotonic=Ticker(),
        **options,
    )
    return engine, log, transport


def lines(log: Any) -> list[dict[str, Any]]:
    return [json.loads(x) for x in log.path.read_text("ascii").splitlines()]


def test_the_run_engine_writes_its_request_lines_into_the_shared_call_log(tmp_path: Path) -> None:
    plan = small_plan(rate=16000, ms=1500, max_chunk_ms=1000)
    engine, log, transport = make(
        tmp_path, [json_response(ok_body("a")), json_response(ok_body("b"))]
    )
    outcome = engine.transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert len(transport.requests) == len(plan.chunks) == 2
    written = lines(log)
    assert [(x["event"], x["stage"], x["index"]) for x in written] == [
        ("before", "stt", 0),
        ("after", "stt", 0),
        ("before", "stt", 1),
        ("after", "stt", 1),
    ]
    assert all(x["audio_seconds"] is not None and x["characters"] is None for x in written)
    assert not (tmp_path / "out" / "sarvam_call_log.jsonl").exists()  # the Phase 3 file is not made
    assert CANARY_KEY not in log.path.read_text("ascii")
    assert {r.stage for r in outcome.usage} == {"stt"}


def test_the_engine_does_not_own_the_log_the_run_closes_it(tmp_path: Path) -> None:
    engine, log, _ = make(tmp_path, [])
    engine.close()
    log.before(stage="tts", index=0, attempt=1, bytes_sent=1, characters=1)  # still open
    assert len(lines(log)) == 1
    log.close()


def test_the_model_has_no_default_and_the_key_comes_first(tmp_path: Path) -> None:
    signature = inspect.signature(engine_mod().create_sarvam_stt_for_run)
    assert signature.parameters["model"].default is inspect.Parameter.empty
    assert signature.parameters["call_log"].default is inspect.Parameter.empty
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in signature.parameters.values())
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    with pytest.raises(SttKeyMissingError) as caught:
        engine_mod().create_sarvam_stt_for_run(call_log=log, model="saaras:v4", environ={})
    assert caught.value.reason == "key_missing"
    assert CANARY_KEY not in repr(caught.value)
    log.close()


def test_an_eight_khz_plan_of_a_simulated_run_is_refused_before_any_request(tmp_path: Path) -> None:
    """ADR 0024 A6 (BL-293): the command refuses the combination earlier still, at step 1."""
    from tracer_evalcore.audio.activity import activity_chunk_plan

    pcm = (b"\x07\x00\x00\x00") * 8000  # one second of stereo, the agent channel active
    plan = activity_chunk_plan(
        pcm, sample_rate_hz=8000, channels=2, channel=0, wait_for_agent_silence_ms=300
    )
    engine, log, transport = make(tmp_path, [])
    with pytest.raises(SttConfigurationError) as caught:
        engine.transcribe(plan, context("mr"))
    assert caught.value.reason == "sample_rate_not_accepted"
    assert transport.requests == []
    assert lines(log) == []
