# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-27: ``pipecat-protobuf/1`` against real Pipecat 1.14.0 (``tools/samplebot``), on loopback.

The only external check of the hand-written protobuf codec (ADR 0026 rules 1, 5 and 23; BL-298,
BL-291): every earlier test of the dialect talks to a fake agent that Tracer wrote from its own
reading of the same documents, so a misreading would pass them all. Here the peer is
``scripted_bot`` (a Pipecat ``/ws-client`` pipeline with no model, a timer on the arrival of frames
and replies with known delays). Run only by the ``interop`` CI job, with ``-m integration``:

    uv sync --locked --project tools/samplebot
    uv run --locked --all-groups pytest -m integration packages/evalsim/tests

What is checked is the audio and the clock, read by independent readers: the 16-bit
little-endian PCM and the rate in both directions (a tone's frequency from zero crossings, on the
bot's side as the bot reports it and on ours), the frame counts, that the wire carried nothing the
five-message set cannot decode (``inbound_messages_ignored`` is 0: if Pipecat sends a frame type
the codec ignores, that is the finding), the end of the call by closing the socket, and that the
gap the simulator reads is not before the bot could have known the line ended. No key, no model,
no non-loopback address. Contract: docs/phase4/api-surface-batch-f.md section 3.
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

RATE = 16000
END_OF_CALLER_MS = 120
REPLIES = [(200, 440, 400, 4000), (300, 880, 200, 4000)]


def test_integration_protobuf_dialect_against_pipecat(tmp_path: Path) -> None:
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES
    )
    with SampleBot.start("pipecat-protobuf/1", scenario, sample_rate_hz=RATE) as bot:
        assert bot.ready["dialect"] == "pipecat-protobuf/1"
        assert bot.ready["pipecat"] == "1.14.0"  # the pin is the version that ran
        assert re.fullmatch(r"ws://127\.0\.0\.1:[0-9]{1,5}/ws-client", bot.url), bot.url
        lines = [sine_pcm(300, 600, 8000, RATE), sine_pcm(500, 400, 8000, RATE)]
        placed = asyncio.run(place_call(bot, "protobuf", lines))
        summary = bot.wait_summary(1)
    result = placed.result
    check_round_trip(
        placed, summary, kind="protobuf", caller_hz=[300, 500],
        replies=[(d, hz, ms) for d, hz, ms, _ in REPLIES], end_of_caller_ms=END_OF_CALLER_MS,
        hz_rel=0.03, ms_abs=60, gap_upper_ms=250, audio_ms_abs=40,
    )  # fmt: skip
    # the codec met nothing it could not decode, and every frame the simulator sent was a frame
    assert result.counts["inbound_messages_ignored"] == 0
    assert result.counts["frames_sent"] == 30 + 20
    assert {f.codec for f in result.agent_frames} == {"pcm_s16le"}
    assert summary["inbound_sample_rate"] == RATE
    # the call ended because the simulator closed the socket, with a code
    assert result.close_code is None or result.close_code == 1000


def test_integration_protobuf_an_agent_hang_up_is_an_agent_hang_up(tmp_path: Path) -> None:
    """The bot closes the WebSocket after its last reply (``after_replies`` ``hang_up``): the runner
    ends the call with ``agent_hangup`` and keeps the close code (ADR 0026 rule 9)."""
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES[:1],
        after_replies="hang_up",
    )  # fmt: skip
    with SampleBot.start("pipecat-protobuf/1", scenario, sample_rate_hz=RATE) as bot:
        placed = asyncio.run(place_call(bot, "protobuf", [sine_pcm(300, 400, 8000, RATE)]))
        summary = bot.wait_summary(1)
    assert placed.result.end_reason == "agent_hangup", placed.result.end_reason
    assert placed.result.close_code is not None
    assert summary["ended_by"] == "bot_hang_up"
    assert placed.timing["answered_caller_turns"] == 1
    assert placed.reply_ms(0) == pytest.approx(400, abs=60)


def test_integration_protobuf_sequential_calls_are_independent(tmp_path: Path) -> None:
    """One bot process serves calls one after another; each call starts the replies again from the
    first (the calibration test relies on it) and gets its own summary, numbered from 1."""
    scenario = write_scenario(
        tmp_path / "scenario.json", end_of_caller_ms=END_OF_CALLER_MS, replies=REPLIES[:1]
    )
    with SampleBot.start("pipecat-protobuf/1", scenario, sample_rate_hz=RATE) as bot:
        first = asyncio.run(place_call(bot, "protobuf", [sine_pcm(300, 300, 8000, RATE)]))
        second = asyncio.run(place_call(bot, "protobuf", [sine_pcm(300, 300, 8000, RATE)]))
        one, two = bot.wait_summary(1), bot.wait_summary(2)
    for placed in (first, second):
        assert placed.timing["answered_caller_turns"] == 1
        assert placed.reply_ms(0) == pytest.approx(400, abs=60)
    assert (one["call"], two["call"]) == (1, 2)
    assert one["replies_started"] == two["replies_started"] == 1
