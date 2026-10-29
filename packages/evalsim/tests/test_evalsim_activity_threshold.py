# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-313: the call runner with the optional activity threshold (ADR 0026 rule 10; contract
docs/phase4/api-surface-batch-activity.md section 5).

The field case: the real agent streams a low noise floor in its pauses (never digital silence) and
speech above it. With the rule of the earlier batches (any non-zero sample) the caller never sees
the agent go silent, waits until ``max_silence_ms`` and the call ends ``max_silence`` with no caller
turn. With ``turn_taking.activity_min_peak`` above the noise the runner measures silence by the
threshold. The runner reads the threshold from the policy; the recording keeps every sample.

Every call runs on the virtual clock over an in-memory connection (``evalsim_support_activity``),
with the pipecat-protobuf (16 kHz PCM) and the twilio (8 kHz mu-law) wire. The instants are derived
by hand in the comments: a frame is sent every 20 ms from the connection, the caller speaks at
``last active frame + wait`` (300 ms here).
"""

from __future__ import annotations

import dataclasses
import importlib
from typing import Any

import pytest

from evalsim_support_activity import (  # type: ignore[import-not-found,unused-ignore]
    KINDS,
    Frames,
    Hang,
    decoded_peak,
    field_steps,
    lines_of,
    play,
    ref_mulaw,
)
from evalsim_support_runner import mod  # type: ignore[import-not-found,unused-ignore]


def ended(played: Any) -> str:
    reason: str = played.result.end_reason
    return reason


def agent_ms(played: Any) -> list[int]:
    return [played.at(f.t_ns) for f in played.result.agent_frames]


def decode_peak(frame: Any) -> int:
    decode_to_pcm16 = importlib.import_module("tracer_evalcore.recorder").decode_to_pcm16
    pcm = decode_to_pcm16(frame.codec, frame.payload)
    values = [int.from_bytes(pcm[i : i + 2], "little", signed=True) for i in range(0, len(pcm), 2)]
    return max(abs(v) for v in values)


# -------------------------------------------------------------------------- the field problem


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("peak", [None, 1])
def test_without_a_threshold_a_noise_floor_agent_never_looks_silent(
    kind: str, peak: int | None
) -> None:
    """The regression guard and the field case in one: with the member absent (or 1) every noise
    frame is active, the caller waits for a silence that never comes and the call ends
    ``max_silence`` at the wait budget (3000 ms) with no caller turn."""
    played = play(kind, field_steps(), lines_of(kind, 10, 10), peak=peak, caller_first=False)
    assert ended(played) == "max_silence"
    assert played.at(played.result.observed_until_ns) == 3000
    assert played.result.turns == ()
    assert played.result.counts["caller_turns_started"] == 0
    assert played.result.counts["frames_sent"] == 0


@pytest.mark.parametrize("kind", KINDS)
def test_the_comfort_noise_scenario_of_the_earlier_batches_is_unchanged_by_a_threshold_of_1(
    kind: str,
) -> None:
    """The comfort-noise agent (a value of 1 on PCM, the byte 0xFE on mu-law) is active at the
    threshold 1 exactly as before: ``max_silence`` at 180 + 1000 ms after the first line."""
    code = 0xFE if kind == "twilio" else None
    steps = [Frames(400, 1, code)]
    for peak in (None, 1):
        played = play(
            kind, steps, lines_of(kind, 10, 10), peak=peak, caller_first=True, max_silence_ms=1000
        )
        assert ended(played) == "max_silence"
        assert played.at(played.result.observed_until_ns) == 180 + 1000
        assert len(played.result.turns) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_threshold_above_the_comfort_noise_makes_the_caller_see_silence_and_speak(
    kind: str,
) -> None:
    """Comfort noise at the smallest level, speech at 4000. Threshold = the decoded comfort level
    plus one: the noise is ignored. The agent speaks at 500 to 680 and 1500 to 1680; the caller
    (waits for the agent first) speaks at 680 + 300 = 980, the agent speaks again, and the call ends
    at 1680 + 300 = 1980 with the script complete."""
    code = 0xFE if kind == "twilio" else None
    floor = decoded_peak(kind, 1, code)
    steps = [
        Frames(25, 1, code),
        Frames(10, 4000),
        Frames(40, 1, code),
        Frames(10, 4000),
        Frames(400, 1, code),
    ]
    played = play(kind, steps, lines_of(kind, 10), peak=floor + 1, caller_first=False)
    assert ended(played) == "script_complete"
    assert played.first_ms(0) == 980
    assert played.at(played.result.observed_until_ns) == 1980
    # at the decoded level itself the noise is activity again: nothing changes from the old rule
    again = play(kind, steps, lines_of(kind, 10), peak=floor, caller_first=False)
    assert ended(again) == "max_silence"
    assert again.result.turns == ()


@pytest.mark.parametrize("kind", KINDS)
def test_with_a_threshold_above_the_noise_the_caller_sees_the_pause_and_the_call_completes(
    kind: str,
) -> None:
    """The noise floor (peak 250) is below 1000, the speech (4000) above. Hand derivation: the
    agent speaks at 500 to 680 (frames at 500, 520, ..., 680), so the caller speaks at 680 + 300 =
    980. Its line is 10 frames (980 to 1160). The agent speaks again at 1500 to 1680; the caller has
    no more lines, so the call ends at 1680 + 300 = 1980 as ``script_complete``."""
    played = play(kind, field_steps(), lines_of(kind, 10), peak=1000, caller_first=False)
    assert ended(played) == "script_complete"
    (turn,) = played.result.turns
    assert played.first_ms(0) == 980
    assert turn.cause == "agent_silent"
    assert turn.frames_sent == turn.frames_planned == 10
    assert played.at(turn.last_frame_ns) == 1160
    assert played.at(played.result.observed_until_ns) == 1980
    # the simulator's own times are measured from the last ACTIVE frame, not from the noise: the
    # noise frame at 960 would give 20, the speech frame at 680 gives 300
    assert turn.self_times.reply_ms == 300
    assert turn.self_times.wait_ms == 300
    counts = played.result.counts
    assert (counts["caller_turns_started"], counts["caller_turns_completed"]) == (1, 1)


@pytest.mark.parametrize("kind", KINDS)
def test_the_wait_after_a_line_is_ended_by_the_next_burst_of_speech_and_never_by_the_noise(
    kind: str,
) -> None:
    """Two lines. The first starts at 980 (derived above) and ends at 1160. The second starts after
    the second burst (1680 + 300 = 1980) and ends at 2160. The agent has only noise left, which is
    below the threshold, so no activity comes: the wait ends on the budget, 2160 + 3000 = 5160."""
    played = play(kind, field_steps(), lines_of(kind, 10, 10), peak=1000, caller_first=False)
    assert [played.first_ms(i) for i in range(len(played.result.turns))] == [980, 1980]
    assert [t.cause for t in played.result.turns] == ["agent_silent", "agent_silent"]
    assert ended(played) == "max_silence"
    assert played.at(played.result.observed_until_ns) == 2160 + 3000
    assert played.result.counts["caller_turns_completed"] == 2


# ----------------------------------------------------------------------------- the boundaries


@pytest.mark.parametrize("kind", KINDS)
def test_a_frame_exactly_at_the_threshold_is_activity_and_one_below_is_silence(kind: str) -> None:
    """The quiet agent speaks at peak 500 (at 500 to 680). With the threshold at the decoded peak
    the caller hears it and speaks at 980; one above the decoded peak, the agent is silent."""
    steps = [Frames(25, 200), Frames(10, 500), Frames(400, 200)]
    seen = decoded_peak(kind, 500)
    heard = play(kind, steps, lines_of(kind, 10), peak=seen, caller_first=True)
    # caller first: line 0 at 0 to 180; the wait begins at 180; the agent's last active frame is
    # 680, so the (empty) script ends the call at 680 + 300
    assert ended(heard) == "script_complete"
    assert heard.at(heard.result.observed_until_ns) == 980
    silent = play(kind, steps, lines_of(kind, 10), peak=seen + 1, caller_first=True)
    # speech below the threshold counts as silence (documented): no activity ever arrives, so the
    # wait budget ends the call: 180 + 3000
    assert ended(silent) == "max_silence"
    assert silent.at(silent.result.observed_until_ns) == 180 + 3000
    assert len(silent.result.turns) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_the_threshold_applies_to_the_whole_frame_peak_not_to_its_first_sample(kind: str) -> None:
    """A protobuf frame of the agent holds 160 quiet samples (value 1) before its loudest one (a
    negative peak in the middle); a mu-law frame is constant. Either way the frame is active iff
    its peak reaches the threshold."""
    steps = [Frames(25, 100), Frames(10, 3000), Frames(400, 100)]
    top = decoded_peak(kind, 3000)
    played = play(kind, steps, lines_of(kind, 10), peak=top, caller_first=True)
    assert ended(played) == "script_complete"
    assert played.at(played.result.observed_until_ns) == 980
    below = play(kind, steps, lines_of(kind, 10), peak=top + 1, caller_first=True)
    assert ended(below) == "max_silence"


# ----------------------------------------------------------------------------------- barge-in


@pytest.mark.parametrize("kind", KINDS)
def test_a_barge_in_counts_from_the_first_active_frame_not_from_the_noise(kind: str) -> None:
    """Two lines, the second with ``barge_in_after_ms`` 100. The agent's noise runs from 0, its
    speech from 600 to 1580. With the threshold the onset is 600, so the barge-in line starts at
    700; the next wait ends 300 ms after the last active frame (1580): 1880."""
    steps = [Frames(30, 250), Frames(50, 4000), Frames(100, 250)]
    gated = play(
        kind, steps, lines_of(kind, 10, 10), peak=1000, barge_ins={1: 100}, caller_first=True
    )
    assert ended(gated) == "script_complete"
    first, second = gated.result.turns
    assert gated.at(first.first_frame_ns) == 0
    assert gated.at(second.first_frame_ns) == 700
    assert (second.cause, second.barge_in_after_ms) == ("barge_in", 100)
    assert gated.at(gated.result.observed_until_ns) == 1880
    assert gated.result.counts["barge_ins_fired"] == 1
    # without the member the noise is the onset: the line starts about 100 ms after the wait began
    plain = play(kind, steps, lines_of(kind, 10, 10), barge_ins={1: 100}, caller_first=True)
    assert 280 <= plain.first_ms(1) <= 300
    assert plain.result.turns[1].cause == "barge_in"


@pytest.mark.parametrize("kind", KINDS)
def test_the_own_times_of_a_barge_in_line_follow_the_contract(kind: str) -> None:
    """The frame at 700 is itself active (speech), so the reply time of the barge-in line is 0, and
    its wait is the scheduled offset, 100 ms (ADR 0026 rule 10)."""
    steps = [Frames(30, 250), Frames(50, 4000), Frames(100, 250)]
    played = play(kind, steps, lines_of(kind, 10, 10), peak=1000, barge_ins={1: 100})
    own = played.result.turns[1].self_times
    assert (own.reply_ms, own.wait_ms) == (0, 100)


# ------------------------------------------------------------------------------ mu-law wire


def test_the_mulaw_wire_is_compared_by_its_decoded_value() -> None:
    """Twilio dialect, raw G.711 bytes: noise 0xE0 (wire value 224, decoded 372) and speech 0xA0
    (wire value 160, decoded 7932). The threshold 1000 lies above the wire value of the speech byte
    and between the two decoded values: the speech is heard and the noise is not, which only a
    comparison of the decoded values does."""
    assert (ref_mulaw(0xE0), ref_mulaw(0xA0)) == (372, 7932)
    steps = [
        Frames(25, 0, 0xE0),
        Frames(10, 0, 0xA0),
        Frames(40, 0, 0xE0),
        Frames(10, 0, 0xA0),
        Frames(400, 0, 0xE0),
    ]
    played = play("twilio", steps, lines_of("twilio", 10), peak=1000, caller_first=False)
    assert ended(played) == "script_complete"
    assert played.first_ms(0) == 980
    assert played.at(played.result.observed_until_ns) == 1980
    at_noise = play("twilio", steps, lines_of("twilio", 10), peak=372, caller_first=False)
    assert ended(at_noise) == "max_silence"  # 372 is the decoded noise: active again
    above_noise = play("twilio", steps, lines_of("twilio", 10), peak=373, caller_first=False)
    assert ended(above_noise) == "script_complete"
    at_speech = play("twilio", steps, lines_of("twilio", 10), peak=7932, caller_first=False)
    assert ended(at_speech) == "script_complete"
    above_speech = play("twilio", steps, lines_of("twilio", 10), peak=7933, caller_first=False)
    assert ended(above_speech) == "max_silence"


def test_a_negative_mulaw_code_counts_by_its_magnitude() -> None:
    """0x60 decodes to -372: the sign does not matter."""
    assert ref_mulaw(0x60) == -372
    steps = [Frames(25, 0, 0x60), Frames(10, 0, 0xA0), Frames(400, 0, 0x60)]
    played = play("twilio", steps, lines_of("twilio", 10), peak=373, caller_first=True)
    assert ended(played) == "script_complete"
    assert played.at(played.result.observed_until_ns) == 980
    noisy = play("twilio", steps, lines_of("twilio", 10), peak=372, caller_first=True)
    assert ended(noisy) == "max_silence"


# ----------------------------------------------------------------------------- the recording


@pytest.mark.parametrize("kind", KINDS)
def test_the_recording_keeps_every_sample_the_threshold_only_decides_activity(kind: str) -> None:
    """The log of the agent frames holds every frame that arrived, noise included, byte for byte:
    nothing is gated, dropped or rewritten."""
    played = play(kind, field_steps(), lines_of(kind, 10), peak=1000, caller_first=False)
    frames = played.result.agent_frames
    payloads = [f.payload for f in frames]
    assert len(frames) == played.result.counts["frames_received"]
    assert payloads == [p for _, p in played.sent[: len(payloads)]]
    assert len(frames) >= 99  # one frame every 20 ms to 1980 ms
    quiet = [f for f in frames if decode_peak(f) <= 1000]
    loud = [f for f in frames if decode_peak(f) > 1000]
    assert len(quiet) > len(loud) > 0
    assert decode_peak(frames[0]) == decoded_peak(kind, 250)


# ------------------------------------------------------------------------------ other reasons


@pytest.mark.parametrize("kind", KINDS)
def test_a_hang_up_and_the_maximum_duration_do_not_depend_on_the_threshold(kind: str) -> None:
    hung = play(kind, [Frames(10, 4000), Hang(1001)], lines_of(kind, 10, 10), peak=1000)
    assert ended(hung) == "agent_hangup"
    assert hung.result.close_code == 1001
    long = play(
        kind,
        [Frames(400, 250)],
        lines_of(kind, 10, 10),
        peak=1000,
        max_duration_ms=500,
        max_silence_ms=60_000,
        caller_first=False,
    )
    assert ended(long) == "max_duration"
    assert long.at(long.result.observed_until_ns) == 500


@pytest.mark.parametrize("kind", KINDS)
def test_a_threshold_above_the_whole_signal_ends_on_max_silence_with_the_recording_kept(
    kind: str,
) -> None:
    """The agent is never louder than 250: with 1000 the caller (waiting first) hears nothing and
    the call ends on the wait budget; every frame is still recorded."""
    played = play(
        kind,
        [Frames(400, 250)],
        lines_of(kind, 10),
        peak=1000,
        caller_first=False,
        max_silence_ms=2000,
    )
    assert ended(played) == "max_silence"
    assert played.at(played.result.observed_until_ns) == 2000
    assert played.result.turns == ()
    assert len(played.result.agent_frames) >= 99


def test_the_runner_takes_the_threshold_from_the_policy_and_has_no_setting_of_its_own() -> None:
    runner = mod("runner")
    names = [f.name for f in dataclasses.fields(runner.RunnerSettings)]
    assert names == ["dialect", "policy", "max_duration_ms", "barge_ins", "max_agent_audio_bytes"]
