# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-27: apparatus calibration. The measured gap equals the injected delay within one frame
quantum plus the stated jitter, over 20 calls per dialect (ADR 0026 rules 8 and 24; SCOPE gate item
12; contract docs/phase4/api-surface-batch-f.md section 3.5).

This is the evidence behind every timing number of Phase 4: a value read at the simulator is more
than a reading of the simulator's own clock only if the apparatus reads a delay it did not make
itself. ``scripted_bot`` waits ``end_of_caller_ms`` after the arrival of the last caller frame and
starts each reply a known ``delay_ms`` later; the simulator measures from the end edge of its line
(the send instant of the last frame plus 20 ms) to the first active agent frame (the timing document
of ADR 0027 rule 10). So the injected value for turn k is ``end_of_caller_ms + delay_k - 20`` and the
error of a reading is ``gap_k - injected_k``. Each call has three lines, so 20 calls are 60
readings per dialect. The rule for the WebSocket dialects: every reading is within the quantum of
that call's timing document plus ``JITTER_MS``. WebRTC adds an unmodelled codec and jitter buffer:
its readings are held to a structural bound only (the reading is never earlier than the injected
delay minus the quantum, and within ``WEBRTC_BOUND_MS`` of it) and are reported. The numbers (counts
and ranges, never audio or text) are written to ``$TRACER_CALIBRATION_OUT/calibration-<dialect>.json``
when that variable names a directory, for the measurement report (P4-36); they are never a claim.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
from pathlib import Path

import pytest

from evalsim_support_samplebot import (  # type: ignore[import-not-found,unused-ignore]
    JITTER_MS,
    KIND_ID,
    RATE,
    SampleBot,
    expected_gap_ms,
    place_call,
    sine_pcm,
    write_scenario,
)

pytestmark = pytest.mark.integration

CALLS = 20
END_OF_CALLER_MS = 120
DELAYS = (100, 250, 400)
WEBRTC_BOUND_MS = 500
OUT_ENV = "TRACER_CALIBRATION_OUT"


@pytest.mark.parametrize("kind", ["protobuf", "twilio", "webrtc"])
def test_integration_calibration_gap_within_quantum(tmp_path: Path, kind: str) -> None:
    rate = RATE[kind]
    scenario = write_scenario(
        tmp_path / "scenario.json",
        end_of_caller_ms=END_OF_CALLER_MS,
        replies=[(delay, 440, 200, 4000) for delay in DELAYS],
    )
    lines = [sine_pcm(300, 300, 8000, rate) for _ in DELAYS]
    injected: list[int] = []
    measured: list[int] = []
    quanta: list[int] = []
    with SampleBot.start(KIND_ID[kind], scenario, sample_rate_hz=rate) as bot:
        for call in range(CALLS):
            placed = asyncio.run(place_call(bot, kind, lines, counter=call + 1))
            assert placed.result.end_reason == "script_complete", (call, placed.result.end_reason)
            turns = placed.timing["turns"]
            assert len(turns) == len(DELAYS), (call, len(turns))
            for k, turn in enumerate(turns):
                assert turn["gap_ms"] is not None, (call, k)
                injected.append(expected_gap_ms(END_OF_CALLER_MS, DELAYS[k]))
                measured.append(turn["gap_ms"])
                quanta.append(placed.timing["quantum_ms"])
    errors = [m - i for m, i in zip(measured, injected, strict=True)]
    assert len(errors) == CALLS * len(DELAYS) == 60
    out = os.environ.get(OUT_ENV)
    if out:
        document = {
            "dialect": KIND_ID[kind], "calls": CALLS, "readings": len(errors),
            "end_of_caller_ms": END_OF_CALLER_MS, "delays_ms": list(DELAYS),
            "jitter_ms": JITTER_MS, "quantum_ms_range": [min(quanta), max(quanta)],
            "error_ms_range": [min(errors), max(errors)],
            "median_abs_error_ms": statistics.median(abs(e) for e in errors),
            "injected_ms": injected, "measured_ms": measured,
        }  # fmt: skip
        Path(out, f"calibration-{kind}.json").write_text(json.dumps(document), encoding="utf-8")
    if kind == "webrtc":
        for error, quantum in zip(errors, quanta, strict=True):
            assert -quantum <= error <= WEBRTC_BOUND_MS, (error, quantum)
        return
    for index, (error, quantum) in enumerate(zip(errors, quanta, strict=True)):
        assert abs(error) <= quantum + JITTER_MS, (kind, index, error, quantum, JITTER_MS)
