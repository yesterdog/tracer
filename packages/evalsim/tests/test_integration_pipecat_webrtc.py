# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: PT018
"""P4-27: ``smallwebrtc/1`` against real Pipecat 1.14.0 (``tools/samplebot``), on this machine.

BL-310 and ADR 0026 rule 7: the WebRTC media round trip is not tested anywhere else (ICE gathers a
host candidate on every interface, which no unit-test guard can confine), so until this test passes
nothing may claim that WebRTC media works. The decision for batch F (contract section 3.4): the
media path is a LIBRARY feature, ``tracer_evalsim.webrtc.open_webrtc`` plus
``tracer_evalsim.dialects.smallwebrtc.SmallWebRtcDialect``, tested here through the same runner as
the WebSocket dialects; ``tracer simulate`` keeps refusing ``smallwebrtc/1`` with
``dialect_not_runnable`` (batch E ambiguity A6) until a later batch wires the command (BACKLOG
row). The peer is ``scripted_bot`` with ``SmallWebRTCTransport`` and the signalling of ADR 0026
(``POST /api/offer``, ``PATCH /api/offer``); no ICE server is configured, so every packet stays
between addresses of this machine (``evalsim_integration_guard`` enforces it for the test process).

Opus is lossy and the transport has a jitter buffer, so the numbers are looser than the WebSocket
ones and the codec is never part of a claim: the reply is heard in about the right place, as about
the right length, at about the right frequency; the caller is heard at about the right length and
frequency. Run only by the ``interop`` CI job, with ``-m integration``. Contract:
docs/phase4/api-surface-batch-f.md section 3.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from evalsim_support_samplebot import (  # type: ignore[import-not-found,unused-ignore]
    SampleBot,
    check_round_trip,
    place_call,
    sine_pcm,
    write_scenario,
)

pytestmark = pytest.mark.integration

RATE = 48000
END_OF_CALLER_MS = 120
REPLIES = [(200, 440, 400, 4000)]


def test_integration_webrtc_against_pipecat_loopback(tmp_path: Path) -> None:
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES
    )
    with SampleBot.start("smallwebrtc/1", scenario, sample_rate_hz=RATE) as bot:
        assert bot.ready["dialect"] == "smallwebrtc/1"
        assert bot.ready["pipecat"] == "1.14.0"
        assert re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/api/offer", bot.url), bot.url
        placed = asyncio.run(place_call(bot, "webrtc", [sine_pcm(300, 600, 8000, RATE)]))
        summary = bot.wait_summary(1)
    result = placed.result
    check_round_trip(
        placed, summary, kind="webrtc", caller_hz=[300],
        replies=[(d, hz, ms) for d, hz, ms, _ in REPLIES], end_of_caller_ms=END_OF_CALLER_MS,
        hz_rel=0.05, ms_abs=120, gap_upper_ms=500, audio_ms_abs=150,
    )  # fmt: skip
    # the media came back as whole 16-bit mono samples at the rate of the dialect
    assert {f.codec for f in result.agent_frames} == {"pcm_s16le"}
    assert all(len(f.payload) % 2 == 0 for f in result.agent_frames)
    assert summary["inbound_sample_rate"] in (RATE, 16000)  # the bot's pipeline rate, not ours
    # the connection was set up by the signalling of the pinned version and nothing else
    assert result.connect_started_ns is not None and result.connect_ended_ns is not None
    assert result.connect_ended_ns >= result.connect_started_ns
    assert result.failure_code is None


def test_integration_webrtc_the_call_ends_when_the_simulator_hangs_up(tmp_path: Path) -> None:
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES
    )
    with SampleBot.start("smallwebrtc/1", scenario, sample_rate_hz=RATE) as bot:
        placed = asyncio.run(place_call(bot, "webrtc", [sine_pcm(300, 400, 8000, RATE)]))
        summary = bot.wait_summary(1)
    assert placed.result.end_reason == "script_complete"
    assert summary["ended_by"] == "caller_closed"
