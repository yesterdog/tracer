# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-27: ``twilio-media-stream/1`` against real Pipecat 1.14.0 (``tools/samplebot``), on loopback.

The only external check of the Twilio-compatible media stream (ADR 0026 rules 5, 6 and 23; BL-299,
BL-291 leftovers: the frame size and the audio sample format). The peer is ``scripted_bot`` on the
runner's ``/ws`` route with ``TwilioFrameSerializer`` and ``auto_hang_up`` off and no credential,
so it never sends a request to Twilio. What is checked: the bot reads ``connected`` then ``start``
(it takes the stream and call identifiers from ``start``, and they are the synthetic, marked ones
the simulator sent), takes 20 ms mu-law frames of 160 bytes as audio at 8000 Hz (the tone it hears
has the frequency that was sent), answers with ``media`` messages the simulator decodes to the tone
the scenario asked for, never needs a ``mark``, and the call ends when the simulator closes the
socket after ``stop`` (Pipecat ignores an inbound ``stop``: only the end of the WebSocket ends the
call). Run only by the ``interop`` CI job, with ``-m integration`` (see the protobuf module for the
command). Contract: docs/phase4/api-surface-batch-f.md section 3.
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

RATE = 8000
END_OF_CALLER_MS = 120
REPLIES = [(200, 440, 400, 4000), (300, 880, 200, 4000)]


def test_integration_twilio_dialect_against_pipecat(tmp_path: Path) -> None:
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES
    )
    with SampleBot.start("twilio-media-stream/1", scenario, sample_rate_hz=RATE) as bot:
        assert bot.ready["dialect"] == "twilio-media-stream/1"
        assert bot.ready["pipecat"] == "1.14.0"
        assert re.fullmatch(r"ws://127\.0\.0\.1:[0-9]{1,5}/ws", bot.url), bot.url
        # ADR 0026 rule 6: the route runs without auto_hang_up and without credentials
        assert bot.ready["auto_hang_up"] is False
        assert bot.ready["credentials"] is False
        lines = [sine_pcm(300, 600, 8000, RATE), sine_pcm(500, 400, 8000, RATE)]
        placed = asyncio.run(place_call(bot, "twilio", lines))
        summary = bot.wait_summary(1)
        starts = bot.events_of("twilio_start", 1)
    result = placed.result
    check_round_trip(
        placed, summary, kind="twilio", caller_hz=[300, 500],
        replies=[(d, hz, ms) for d, hz, ms, _ in REPLIES], end_of_caller_ms=END_OF_CALLER_MS,
        hz_rel=0.04, ms_abs=60, gap_upper_ms=250, audio_ms_abs=40,
    )  # fmt: skip
    assert result.counts["inbound_messages_ignored"] == 0
    assert result.counts["frames_sent"] == 30 + 20
    assert {f.codec for f in result.agent_frames} == {"mulaw"}
    assert summary["inbound_sample_rate"] == RATE
    # the identifiers the bot took from `start` are the synthetic, marked ones (rule 6)
    (start,) = starts
    assert start["stream_sid"].startswith("tracer-sim-")
    assert start["call_sid"].startswith("tracer-sim-")
    assert start["custom_parameters"] == {"tracer_synthetic": "true"}
    # the end of the call is the end of the socket, not the `stop` message (BL-299)
    assert summary["ended_by"] == "caller_closed"


def test_integration_twilio_an_agent_hang_up_is_an_agent_hang_up(tmp_path: Path) -> None:
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES[:1],
        after_replies="hang_up",
    )  # fmt: skip
    with SampleBot.start("twilio-media-stream/1", scenario, sample_rate_hz=RATE) as bot:
        placed = asyncio.run(place_call(bot, "twilio", [sine_pcm(300, 400, 8000, RATE)]))
        summary = bot.wait_summary(1)
    assert placed.result.end_reason == "agent_hangup", placed.result.end_reason
    assert summary["ended_by"] == "bot_hang_up"
    assert placed.reply_ms(0) == pytest.approx(400, abs=60)


def test_integration_twilio_no_mark_is_needed_and_none_is_waited_for(tmp_path: Path) -> None:
    """Pipecat's serializer never writes ``mark``: the call runs to its end with none arriving
    (ADR 0026 rule 6: marks are optional, accepted and counted when they arrive, never awaited)."""
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES[:1]
    )
    with SampleBot.start("twilio-media-stream/1", scenario, sample_rate_hz=RATE) as bot:
        placed = asyncio.run(place_call(bot, "twilio", [sine_pcm(300, 400, 8000, RATE)]))
        bot.wait_summary(1)
    assert placed.result.end_reason == "script_complete"
    assert placed.result.counts["inbound_messages_ignored"] == 0
