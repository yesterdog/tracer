# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 (steps 9 to 11): the asyncio call runner against fake agents on a virtual clock.

Contract: docs/phase4/api-surface-batch-e.md section 3. ``run_call`` connects, plays the turn-taking
policy of ADR 0026 rule 10 against the agent's audio, sends the caller's lines through the paced
sender, ends the call with exactly one end reason and hangs up. Every case below runs on the
virtual clock (``ManualClock``) with the deterministic fake agents of P4-19 over an in-memory
connection: no socket, no wall clock, and the same numbers on every run. Instants are written in
milliseconds after the origin of the call (the instant the connection was made). The arithmetic of
the base case, by hand: the caller's first line is 10 frames sent at 0, 20, ... 180; the agent
speaks
10 frames from 400 (the last arrives at 580) and the caller waits for 300 ms of silence, so the
second line starts at exactly 880; the agent speaks 5 frames from 1100 (the last arrives at 1180)
and the call ends at 1180 + 300 = 1480 because the script is complete.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    Abort,
    Comfort,
    Garbage,
    HangUp,
    Send,
    Silence,
    Speak,
    Wait,
)
from evalsim_support_runner import (  # type: ignore[import-not-found,unused-ignore]
    COUNT_KEYS,
    FRAME_NS,
    ORIGIN,
    audio_for,
    core,
    dialect_of,
    lines,
    mod,
    ms,
    play,
    received_frames,
    stalled_pair,
)
from evalsim_support_wire import ref_encode  # type: ignore[import-not-found,unused-ignore]

KINDS = ["protobuf", "twilio"]
BASE_STEPS = [Wait(400), Speak(10), Wait(500), Speak(5)]


def ended(played: Any) -> str:
    reason: str = played.result.end_reason
    return reason


# --------------------------------------------------------------------------------- the base call


@pytest.mark.parametrize("kind", KINDS)
def test_runner_places_a_scripted_call_at_exact_virtual_instants(kind: str) -> None:
    """The base call of the module docstring, on both dialects: the lines start where the policy
    says, the agent's frames are logged at their arrival, the call ends script_complete."""
    dialect = dialect_of(kind)
    played = play(kind, BASE_STEPS, lines(dialect, 10, 10))
    result = played.result
    assert (result.end_reason, result.call_established) == ("script_complete", True)
    caller = played.caller_ms()
    assert caller[:10] == [20 * k for k in range(10)]
    assert caller[10:] == [880 + 20 * k for k in range(10)]
    agent = played.agent_ms()
    assert agent[:10] == [400 + 20 * k for k in range(10)]
    assert agent[10:] == [1100 + 20 * k for k in range(5)]
    assert played.at(result.observed_until_ns) == 1480
    assert result.connect_started_ns == result.connect_ended_ns == ORIGIN
    first, second = result.turns
    assert (first.turn_seq, first.cause) == (0, "caller_first")
    assert (played.at(first.first_frame_ns), played.at(first.last_frame_ns)) == (0, 180)
    assert (first.frames_sent, first.frames_planned, first.complete) == (10, 10, True)
    assert (second.turn_seq, second.cause) == (1, "agent_silent")
    assert (played.at(second.first_frame_ns), played.at(second.last_frame_ns)) == (880, 1060)
    assert second.text == "line 1"
    assert second.barge_in_after_ms is None
    assert result.cap_reached is None
    assert result.watchdog_fired is False
    assert result.close_code is None
    assert result.failure_code is None


@pytest.mark.parametrize("kind", KINDS)
def test_the_frame_logs_are_exactly_what_was_sent_and_received(kind: str) -> None:
    """Channel 0 is what was sent: the payloads the agent received equal the logged caller frames,
    in order, and the logged agent frames are the agent's audio in the wire codec."""
    dialect = dialect_of(kind)
    played = play(kind, BASE_STEPS, lines(dialect, 10, 10))
    sent = [f.payload for f in played.result.caller_frames]
    assert sent == received_frames(played, kind)
    assert b"".join(sent[:10]) == audio_for(dialect, 10)
    assert len(sent) == 20
    for frame in played.result.caller_frames + played.result.agent_frames:
        assert (frame.sample_rate_hz, frame.channels, frame.codec) == (
            dialect.sample_rate_hz,
            1,
            dialect.wire_codec,
        )
    assert all(len(f.payload) == dialect.frame_bytes for f in played.result.caller_frames)
    assert all(f.t_ns >= ORIGIN for f in played.result.agent_frames)


def test_twilio_opens_with_connected_and_start_and_ends_with_stop() -> None:
    """ADR 0026 rule 6: ``connected`` then ``start`` before any media, ``stop`` at the hang-up, and
    the stream timestamp of each media message is the stream time in ms."""
    played = play("twilio", BASE_STEPS, lines(dialect_of("twilio"), 10, 10))
    sent = [json.loads(m) for m in played.client.sent]
    events = [body["event"] for body in sent]
    assert events[:2] == ["connected", "start"]
    assert events[-1] == "stop"
    assert events.count("media") == 20
    assert [int(body["sequenceNumber"]) for body in sent[1:]] == list(range(1, len(sent)))
    media = [body["media"] for body in sent if body["event"] == "media"]
    assert [int(x["timestamp"]) for x in media[:3]] == [0, 20, 40]
    assert [int(x["timestamp"]) for x in media[10:12]] == [880, 900]
    assert base64.b64decode(media[0]["payload"]) == b"\x55" * 160


def test_protobuf_has_no_opening_and_no_closing_message() -> None:
    played = play("protobuf", BASE_STEPS, lines(dialect_of("protobuf"), 10, 10))
    assert len(played.agent.received_log) == 20  # only audio
    assert played.client.calls[-1] == ("close", 1000)


def test_the_result_is_plain_data_with_the_documented_counts() -> None:
    played = play("protobuf", BASE_STEPS, lines(dialect_of("protobuf"), 10, 10))
    result = played.result
    assert sorted(result.counts) == sorted(COUNT_KEYS)
    assert dict(result.counts) == {
        "barge_ins_fired": 0,
        "brain_invalid_outputs": 0,
        "brain_regenerations": 0,
        "brain_requests": 0,
        "caller_turns_completed": 2,
        "caller_turns_started": 2,
        "frames_received": 15,
        "frames_sent": 20,
        "inbound_messages_ignored": 0,
        "llm_requests": 0,
        "signals_received": 0,
    }
    assert isinstance(result.caller_frames, tuple)
    assert isinstance(result.agent_frames, tuple)
    assert isinstance(result.turns, tuple)
    with pytest.raises(AttributeError):
        result.end_reason = "other"


def test_the_same_call_gives_the_same_result() -> None:
    """P4-19 and ADR 0026 rule 23: determinism on the virtual clock."""
    dialect = dialect_of("protobuf")
    first = play("protobuf", BASE_STEPS, lines(dialect, 10, 10)).result
    second = play("protobuf", BASE_STEPS, lines(dialect, 10, 10)).result
    assert first == second


def test_the_runner_reads_no_wall_clock_and_no_entropy(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0026 rule 2: the clock is injected. Reading the wall clock or sleeping for real fails
    (the event loop's own monotonic clock is not patched: it is not the runner's)."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the runner read the wall clock")

    for name in ("time", "sleep", "monotonic_ns", "perf_counter", "process_time", "time_ns"):
        monkeypatch.setattr(time, name, boom)
    played = play("protobuf", BASE_STEPS, lines(dialect_of("protobuf"), 10, 10))
    assert ended(played) == "script_complete"


def test_the_runner_modules_import_no_clock_entropy_or_process_state() -> None:
    """The same rule at the source: ``runner`` and ``lines`` take everything injected."""
    import ast
    from pathlib import Path

    root = Path(mod("runner").__file__).parent
    banned = {
        "time",
        "datetime",
        "random",
        "secrets",
        "uuid",
        "os",
        "sys",
        "socket",
        "signal",
        "threading",
        "subprocess",
    }
    for name in ("runner.py", "lines.py"):
        tree = ast.parse((root / name).read_text("utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        assert not imported & banned, (name, imported & banned)


# ------------------------------------------------------------------------------ who speaks first


def test_without_caller_first_the_caller_waits_for_the_greeting() -> None:
    """ADR 0026 Ambiguity 2: the first line waits for the agent's greeting and its silence."""
    played = play(
        "protobuf",
        [Wait(100), Speak(5)],
        lines(dialect_of("protobuf"), 10),
        caller_first=False,
        max_silence_ms=1000,
    )
    # greeting frames arrive 100..180; silence of 300 ms after the last: the line starts at 480
    assert played.caller_ms()[0] == 480
    first = played.result.turns[0]
    assert first.cause == "agent_silent"
    assert first.self_times.wait_ms == 300
    assert first.self_times.reply_ms == 300
    # the agent said nothing after the line: the call ends by the wait budget, not by the script
    assert ended(played) == "max_silence"
    assert played.at(played.result.observed_until_ns) == 660 + 1000


def test_a_script_that_is_done_still_waits_for_the_agent_to_finish() -> None:
    """The brain is asked only when the wait ends: the agent's last reply is heard in full."""
    played = play("protobuf", [Wait(400), Speak(10)], lines(dialect_of("protobuf"), 10))
    assert ended(played) == "script_complete"
    assert played.agent_ms()[-1] == 580  # the whole reply was recorded
    assert played.at(played.result.observed_until_ns) == 880


# ----------------------------------------------------------------------------------- end reasons


def test_a_silent_agent_ends_the_call_by_the_wait_budget() -> None:
    """ADR 0026 rule 10: ``max_silence_ms`` is the wait budget; the call never hangs."""
    played = play(
        "protobuf",
        [Silence(500)],
        lines(dialect_of("protobuf"), 10, 10),
        max_silence_ms=1000,
    )
    result = played.result
    assert ended(played) == "max_silence"
    assert played.at(result.observed_until_ns) == 180 + 1000
    assert len(result.turns) == 1
    assert all(not any(f.payload) for f in result.agent_frames)  # digital silence, recorded


def test_a_comfort_noise_agent_never_looks_silent_and_ends_on_max_silence() -> None:
    """Q6 and ADR 0026 rule 10: every frame is active, so the silence rule never fires; the wait
    budget ends the call. Not a hang."""
    played = play(
        "protobuf",
        [Comfort(400)],
        lines(dialect_of("protobuf"), 10, 10),
        max_silence_ms=1000,
    )
    assert ended(played) == "max_silence"
    assert played.at(played.result.observed_until_ns) == 180 + 1000
    assert len(played.result.turns) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_clean_hang_up_by_the_agent_is_agent_hangup_with_its_close_code(kind: str) -> None:
    played = play(kind, [Wait(100), Speak(5), HangUp(1001)], lines(dialect_of(kind), 10, 10))
    assert ended(played) == "agent_hangup"
    assert played.result.close_code == 1001
    assert played.at(played.result.observed_until_ns) == 200
    assert len(played.result.turns) == 1
    assert played.result.turns[0].complete


def test_a_hang_up_in_the_middle_of_a_line_cuts_the_line() -> None:
    """The line keeps the frames that were sent (3 of 10) and says it is incomplete."""
    played = play("protobuf", [Wait(50), HangUp(1000)], lines(dialect_of("protobuf"), 10))
    assert ended(played) == "agent_hangup"
    (turn,) = played.result.turns
    assert (turn.frames_sent, turn.frames_planned, turn.complete) == (3, 10, False)
    assert played.caller_ms() == [0, 20, 40]
    assert played.result.counts["caller_turns_started"] == 1
    assert played.result.counts["caller_turns_completed"] == 0


def test_a_connection_that_vanishes_is_a_transport_error() -> None:
    """No Close frame: the connection was lost (ADR 0026 rule 9, batch D end-reason mapping)."""
    played = play("protobuf", [Wait(50), Abort()], lines(dialect_of("protobuf"), 10))
    assert ended(played) == "transport_error"
    assert played.result.failure_code == "connection_lost"
    assert played.result.close_code == 1006


def test_the_call_ends_at_the_maximum_duration_and_never_after() -> None:
    """ADR 0026 rule 9: the maximum duration; here in a wait, then in the middle of a line."""
    in_wait = play(
        "protobuf",
        [Silence(100)],
        lines(dialect_of("protobuf"), 10, 10),
        max_duration_ms=500,
        max_silence_ms=60_000,
    )
    assert ended(in_wait) == "max_duration"
    assert in_wait.at(in_wait.result.observed_until_ns) == 500
    in_line = play(
        "protobuf", [Silence(100)], lines(dialect_of("protobuf"), 30), max_duration_ms=100
    )
    assert ended(in_line) == "max_duration"
    assert in_line.caller_ms() == [0, 20, 40, 60, 80]  # the frame at 100 is never sent
    assert in_line.result.turns[0].complete is False
    assert in_line.at(in_line.result.observed_until_ns) == 100


def test_a_stalled_write_is_ended_by_the_watchdog_after_the_grace() -> None:
    """ADR 0026 rule 8, P4-20: a write that never completes blocks the sender, so the loop cannot
    notice the deadline; the independent watchdog hangs up at deadline + 1 s, and the call is
    reported as ``max_duration`` (not as the transport error its own hang-up caused)."""
    played = play(
        "protobuf",
        [],
        lines(dialect_of("protobuf"), 10),
        wire_factory=stalled_pair,
        agent_runs=False,
        max_duration_ms=500,
    )
    result = played.result
    assert result.end_reason == "max_duration"
    assert result.watchdog_fired is True
    assert played.at(result.observed_until_ns) == 500 + 1000
    assert played.client.close_calls
    assert played.client.close_calls[0] == 1000
    (turn,) = result.turns
    assert turn.frames_sent == 1
    assert not turn.complete


# ------------------------------------------------------------------------------- stop and signals


def test_a_stop_during_a_wait_ends_the_call_at_once_with_the_kill_switch() -> None:
    played = play(
        "protobuf",
        [Silence(100)],
        lines(dialect_of("protobuf"), 10, 10),
        stop_at_ms=1000,
        max_silence_ms=60_000,
    )
    assert ended(played) == "kill_switch"
    assert played.at(played.result.observed_until_ns) == 1000
    assert played.result.counts["signals_received"] == 1


def test_a_stop_during_a_line_hangs_up_within_one_frame_and_keeps_the_partial_line() -> None:
    """ADR 0026 rule 17: the first signal hangs up gracefully; the partial recording is kept. The
    stop at 90 ms is seen before the frame at 100 ms, so at most one frame period passes."""
    played = play(
        "protobuf",
        [Silence(100)],
        lines(dialect_of("protobuf"), 30),
        stop_at_ms=90,
        max_silence_ms=60_000,
    )
    assert ended(played) == "kill_switch"
    assert played.caller_ms() == [0, 20, 40, 60, 80]
    assert played.at(played.result.observed_until_ns) - 90 <= FRAME_NS // 1_000_000
    (turn,) = played.result.turns
    assert not turn.complete
    assert turn.frames_sent == 5


def test_a_stop_before_the_connection_never_connects() -> None:
    played = play("protobuf", [], lines(dialect_of("protobuf"), 10), prestop=True, agent_runs=False)
    assert ended(played) == "kill_switch"
    assert played.result.call_established is False
    assert played.client is None  # the connector was never called
    assert played.result.caller_frames == ()
    assert played.result.turns == ()


def test_a_stop_while_connecting_is_seen_as_soon_as_the_call_is_connected() -> None:
    played = play(
        "protobuf",
        [Silence(100)],
        lines(dialect_of("protobuf"), 10),
        connect_ms=50,
        stop_at_ms=10,
    )
    assert ended(played) == "kill_switch"
    assert played.result.call_established is True
    assert played.result.turns == ()
    assert played.result.counts["signals_received"] == 1


# ----------------------------------------------------------------------------------- setup


def test_a_refused_connection_is_a_setup_failure_not_an_exception() -> None:
    """ADR 0026 rule 9 and P4-04: ``setup_failed`` is an outcome; the attempt is timed."""
    error = mod("errors").SetupFailedError(code="connection_refused")
    played = play(
        "protobuf",
        [],
        lines(dialect_of("protobuf"), 10),
        connect_ms=30,
        connect_error=error,
        agent_runs=False,
    )
    result = played.result
    assert (result.end_reason, result.call_established) == ("setup_failed", False)
    assert result.failure_code == "connection_refused"
    assert played.at(result.connect_started_ns) == 0
    assert played.at(result.connect_ended_ns) == 30
    assert result.caller_frames == ()
    assert result.agent_frames == ()
    assert result.turns == ()
    assert all(v == 0 for v in result.counts.values())


def test_the_origin_of_the_recording_is_the_instant_the_connection_was_made() -> None:
    played = play("protobuf", BASE_STEPS, lines(dialect_of("protobuf"), 10, 10), connect_ms=30)
    result = played.result
    assert result.connect_ended_ns - result.connect_started_ns == ms(30)
    assert result.caller_frames[0].t_ns == result.connect_ended_ns  # caller speaks first
    assert played.at(result.observed_until_ns) == 30 + 1480


# --------------------------------------------------------------------------- the brain's end


@pytest.mark.parametrize(
    ("final_kwargs", "reason"),
    [
        ({"end_reason": "caller_end_call"}, "caller_end_call"),
        ({"end_reason": "max_turns"}, "max_turns"),
        (
            {"end_reason": "brain_invalid_output", "invalid_outputs": 2, "regenerations": 1},
            "brain_invalid_output",
        ),
        ({"end_reason": "spend_cap", "cap_reached": "llm_tokens", "requests": 1}, "spend_cap"),
    ],
)
def test_the_end_decision_of_the_brain_is_the_end_reason(
    final_kwargs: dict[str, Any], reason: str
) -> None:
    final = mod("runner").CallerEnd(**final_kwargs)
    played = play("protobuf", BASE_STEPS[:2], lines(dialect_of("protobuf"), 10, final=final))
    assert ended(played) == reason
    assert played.result.cap_reached == final_kwargs.get("cap_reached")
    assert played.result.counts["brain_invalid_outputs"] == final_kwargs.get("invalid_outputs", 0)
    assert played.result.counts["brain_regenerations"] == final_kwargs.get("regenerations", 0)
    assert played.result.counts["llm_requests"] == final_kwargs.get("requests", 0)
    assert played.result.counts["brain_requests"] == (1 if final_kwargs.get("requests") else 0)


# ------------------------------------------------------------------------------------ barge-in


def test_a_scripted_barge_in_starts_the_line_at_the_offset_from_the_agent_onset() -> None:
    """ADR 0026 rule 10: ``barge_in_after_ms`` from the onset of agent activity, even though the
    agent is still talking; the turn records the offset and the counts record the barge-in."""
    played = play(
        "protobuf",
        [Wait(300), Speak(30)],
        lines(dialect_of("protobuf"), 10, 10),
        barge_ins={1: 100},
    )
    second = played.result.turns[1]
    assert second.cause == "barge_in"
    assert second.barge_in_after_ms == 100
    assert played.at(second.first_frame_ns) == 400  # onset 300 + 100
    assert second.self_times.wait_ms == 100
    assert played.result.counts["barge_ins_fired"] == 1
    assert played.result.turns[0].barge_in_after_ms is None
    # the agent never paused for 300 ms before 400, so no silence rule was involved
    assert any(300 <= t < 400 for t in played.agent_ms())


# --------------------------------------------------------------------------------- slow brain


class SlowLines:
    """An LLM-like provider: it needs the agent's text and takes virtual time to answer."""

    mode = "llm"
    needs_agent_text = True

    def __init__(self, clock: Any, *, brain_ms: int = 500, turns: int = 2) -> None:
        self.clock = clock
        self.brain_ms = brain_ms
        self.turns = turns
        self.seen: list[tuple[int, str | None]] = []

    async def next(self, turn_index: int, agent_text: str | None) -> Any:
        runner = mod("runner")
        self.seen.append((turn_index, agent_text))
        await self.clock.sleep_until_ns(self.clock.monotonic_ns() + ms(self.brain_ms))
        if turn_index >= self.turns:
            return runner.CallerEnd("max_turns")
        return runner.PreparedLine(
            text=f"llm line {turn_index}",
            frames=tuple(audio_for(dialect_of("protobuf"), 1) for _ in range(10)),
            brain_ms=self.brain_ms,
            tts_ms=7,
            requests=1,
        )


SLOW_STEPS = [Wait(1000), Speak(10), Wait(1500), Speak(5)]
HEARD = "\u0939\u094b\u092f"


def play_slow(**extra: Any) -> tuple[Any, list[SlowLines], list[Any]]:
    """The LLM-like call: the agent speaks 1000..1180 and 2700..2780 (arrivals)."""
    made: list[SlowLines] = []
    calls: list[Any] = []

    def provider(clock: Any) -> SlowLines:
        made.append(SlowLines(clock))
        return made[0]

    def segments_for(clock: Any) -> Any:
        async def read(caller: Any, agent: Any, origin: int) -> Any:
            calls.append((len(caller), len(agent), origin))
            await clock.sleep_until_ns(clock.monotonic_ns() + ms(50))
            return HEARD

        return read

    played = play(
        "protobuf",
        SLOW_STEPS,
        provider,
        segments_factory=extra.pop("segments_factory", segments_for),
        max_silence_ms=5000,
        **extra,
    )
    return played, made, calls


def test_the_simulator_never_speaks_while_the_brain_is_pending() -> None:
    """ADR 0026 rule 10 and ADR 0028 rule 9. The first line waits for the brain (500 ms) before
    its first frame. The agent's reply ends at 1180, the silence is due at 1480, and the line
    starts only after the in-call transcription (50 ms) and the brain (500 ms): at 2030."""
    played, _, _ = play_slow()
    result = played.result
    first, second, *rest = result.turns
    assert rest == []
    assert played.at(first.first_frame_ns) == 500
    assert first.self_times == core("simtiming").SelfTimes(None, None, None, 500, 7)
    assert played.at(second.first_frame_ns) == 1480 + 50 + 500
    assert second.self_times == core("simtiming").SelfTimes(
        reply_ms=2030 - 1180, wait_ms=300, stt_ms=50, brain_ms=500, tts_ms=7
    )
    assert not any(1480 <= t < 2030 for t in played.caller_ms())
    assert result.counts["brain_requests"] == 2
    assert result.counts["llm_requests"] == 2


def test_the_brain_is_given_the_text_of_each_finished_agent_segment() -> None:
    """ADR 0028 rule 9: nothing for the first turn, the text of the segment that just ended for
    the others; the transcriber is called once per finished segment with the logs so far and the
    origin of the recording; the call ends with ``max_turns`` after the third request."""
    played, (provider,), segments = play_slow()
    assert provider.seen == [(0, None), (1, HEARD), (2, HEARD)]
    assert len(segments) == 2
    assert all(origin == ORIGIN for _, _, origin in segments)
    assert segments[0][1] == 10
    assert segments[1][1] == 15
    assert ended(played) == "max_turns"


@pytest.mark.parametrize(
    ("raises", "reason", "cap", "code"),
    [
        ("cap", "spend_cap", "stt_seconds", None),
        ("server", "transport_error", None, "stt_failed"),
    ],
)
def test_a_transcription_failure_in_the_call_ends_it(
    raises: str, reason: str, cap: str | None, code: str | None
) -> None:
    """ADR 0028 rule 9 and Ambiguity 3: an STT request that fails ends the call with
    ``transport_error`` (the failure itself is the command's to record); a cap is ``spend_cap``."""
    stt = core("stt")

    def segments_for(clock: Any) -> Any:
        async def read(caller: Any, agent: Any, origin: int) -> Any:
            if raises == "cap":
                raise stt.SttCapReachedError(chunk_index=0, reason="stt_seconds")
            raise stt.SttServerError(chunk_index=0, status=503)

        return read

    played, _, _ = play_slow(segments_factory=segments_for)
    assert (ended(played), played.result.cap_reached) == (reason, cap)
    assert played.result.failure_code == code
    assert len(played.result.turns) == 1  # only the first line was sent


# ----------------------------------------------------------------------------------- hostile agents


def test_a_message_over_the_cap_ends_the_call_with_a_transport_error() -> None:
    """ADR 0026 rule 22: a huge message is never buffered."""
    played = play("protobuf", [Wait(50), Send(b"\x00" * 70_000)], lines(dialect_of("protobuf"), 10))
    assert ended(played) == "transport_error"
    assert played.result.failure_code == "message_too_large"
    assert played.result.agent_frames == ()


def test_audio_at_the_wrong_rate_is_refused_never_resampled() -> None:
    """ADR 0027 rule 4: a frame at another rate ends the call; nothing is resampled or recorded."""
    frame = ref_encode(
        "audio", {"audio": b"\x01\x00" * 160, "sample_rate": 8000, "num_channels": 1}
    )
    played = play("protobuf", [Wait(50), Send(frame)], lines(dialect_of("protobuf"), 10))
    assert ended(played) == "transport_error"
    assert played.result.failure_code == "audio_format_mismatch"
    assert played.result.agent_frames == ()


def test_audio_of_an_odd_length_is_refused() -> None:
    odd = ref_encode("audio", {"audio": b"\x01\x00\x01", "sample_rate": 16000, "num_channels": 1})
    played = play("protobuf", [Wait(50), Send(odd)], lines(dialect_of("protobuf"), 10))
    assert played.result.failure_code == "audio_frame_invalid"
    assert ended(played) == "transport_error"


def test_garbage_is_ignored_and_counted_and_the_call_goes_on() -> None:
    """ADR 0026 rule 5: a message the dialect does not know is ignored and counted."""
    steps = [Wait(50), Garbage(100, seed=1), Garbage(10, seed=2), Wait(300), Speak(5)]
    played = play("protobuf", steps, lines(dialect_of("protobuf"), 10))
    assert ended(played) == "script_complete"
    assert played.result.counts["inbound_messages_ignored"] == 2
    assert played.result.counts["frames_received"] == 5


def test_an_audio_message_with_no_audio_is_counted_and_not_recorded() -> None:
    empty = ref_encode("audio", {"audio": b"", "sample_rate": 16000, "num_channels": 1})
    played = play(
        "protobuf", [Wait(50), Send(empty), Wait(300), Speak(5)], lines(dialect_of("protobuf"), 10)
    )
    assert ended(played) == "script_complete"
    assert played.result.counts["inbound_messages_ignored"] == 1
    assert len(played.result.agent_frames) == 5


def test_a_flood_of_frames_ends_the_call_at_the_rate_cap() -> None:
    """ADR 0026 rule 22: the cap on inbound messages per second."""
    played = play(
        "protobuf", [Wait(50), Speak(1500, burst=True)], lines(dialect_of("protobuf"), 10)
    )
    assert ended(played) == "transport_error"
    assert played.result.failure_code == "inbound_rate_exceeded"
    assert len(played.result.agent_frames) <= 1000


def test_the_total_agent_audio_is_bounded() -> None:
    """A slow flood is bounded too: the sum of the agent's audio has a cap of its own."""
    played = play(
        "protobuf",
        [Wait(50), Speak(10)],
        lines(dialect_of("protobuf"), 10),
        max_agent_audio_bytes=2000,
    )
    assert ended(played) == "transport_error"
    assert played.result.failure_code == "message_too_large"
    assert len(played.result.agent_frames) <= 4


def test_the_default_total_cap_is_the_documented_constant() -> None:
    limits = mod("limits")
    assert limits.MAX_AGENT_AUDIO_BYTES == 268_435_456
    assert mod("runner").RunnerSettings.__dataclass_fields__["max_agent_audio_bytes"].default == (
        limits.MAX_AGENT_AUDIO_BYTES
    )
    assert limits.CONNECT_TIMEOUT_S == 10.0
    assert limits.CLOSE_TIMEOUT_S == 5.0


def test_the_total_agent_audio_cap_is_inclusive() -> None:
    """The cap is on the sum of the agent's audio: exactly the cap is allowed, one byte more ends
    the call (the boundary of ``test_the_total_agent_audio_is_bounded``)."""
    dialect = dialect_of("protobuf")
    exact = len(audio_for(dialect, 3))
    allowed = play(
        "protobuf",
        [Wait(50), Speak(3)],
        lines(dialect, 10),
        max_agent_audio_bytes=exact,
    )
    assert allowed.result.failure_code is None
    assert len(allowed.result.agent_frames) == 3
    refused = play(
        "protobuf",
        [Wait(50), Speak(3)],
        lines(dialect, 10),
        max_agent_audio_bytes=exact - 1,
    )
    assert refused.result.failure_code == "message_too_large"
    assert len(refused.result.agent_frames) == 3  # the frame that crossed the cap was received


def test_a_barge_in_timer_alone_decides_the_start_after_a_short_burst() -> None:
    """With two agent frames only (300 and 320 ms) nothing else wakes the loop at 400 ms: the line
    starts at the onset plus the offset, to the millisecond."""
    played = play(
        "protobuf",
        [Wait(300), Speak(2)],
        lines(dialect_of("protobuf"), 10, 10),
        barge_ins={1: 100},
    )
    second = played.result.turns[1]
    assert second.cause == "barge_in"
    assert played.at(second.first_frame_ns) == 400


class FailingReader:
    """A client end whose reader hits a bug of its own (not a transport error)."""

    def __init__(self) -> None:
        self.close_info = None

    async def send(self, message: bytes | str) -> None:
        return None

    async def recv(self) -> bytes | str | None:
        raise RuntimeError("reader bug")

    async def close(self, code: int = 1000) -> None:
        return None


def test_an_unexpected_error_of_the_reader_is_raised_not_turned_into_a_hang_up() -> None:
    """A programming error must not pass for a clean end of the call (``agent_hangup``): it is
    raised to the caller of ``run_call``."""
    with pytest.raises(RuntimeError, match="reader bug"):
        play(
            "protobuf",
            [Wait(50)],
            lines(dialect_of("protobuf"), 10),
            wire_factory=lambda: (FailingReader(), None),
            agent_runs=False,
        )
