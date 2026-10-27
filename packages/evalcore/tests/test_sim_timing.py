# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-07: timing extraction into ``tracer.timing/1`` (ADR 0027 rules 10 and 11 and invariant I5;
SCOPE decision D-a; contract docs/phase4/api-surface-batch-a.md section 5).

Every instant is an integer of nanoseconds of the simulator's monotonic clock, far from zero
(``T0``) so that a value taken from an absolute instant instead of the connection origin shows.
The expected documents are written by hand for the synthetic calls, and by an independent
reference (``ref_timing`` below, loops over lists) for the random ones. The module is loaded
lazily, so before it exists every test fails on its own with ``ModuleNotFoundError``.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    T0,
    active,
    log_frame,
    ms,
    pcm,
    recorder_mod,
    ref_alaw,
    ref_mulaw,
    silent,
    simtiming_mod,
)

DIALECTS = ("pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1")
SELF_KEYS = ("reply_ms", "wait_ms", "stt_ms", "brain_ms", "tts_ms")

# Deterministic hypothesis profile (as Phase 1 to 3): derandomised, no deadline.
HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def self_times(**values: int | None) -> Any:
    merged: dict[str, int | None] = dict.fromkeys(SELF_KEYS)
    merged.update(values)
    return simtiming_mod().SelfTimes(**merged)


def turn(
    seq: int,
    first_ms: int,
    frames: int = 10,
    *,
    barge: int | None = None,
    own: Any = None,
    first_extra_ns: int = 0,
) -> Any:
    """A caller turn sent from ``first_ms`` after the origin: ``frames`` frames 20 ms apart."""
    first = T0 + ms(first_ms) + first_extra_ns
    return simtiming_mod().CallerTurn(
        turn_seq=seq,
        first_frame_ns=first,
        last_frame_ns=first + ms(20 * (frames - 1)),
        barge_in_after_ms=barge,
        self_times=own if own is not None else self_times(),
    )


def burst(start_ns: int, count: int, payload: bytes, step_ms: int = 20) -> list[Any]:
    """``count`` agent frames of ``payload`` arriving ``step_ms`` apart from ``start_ns``."""
    return [log_frame(start_ns + ms(step_ms * k), payload) for k in range(count)]


def extract(
    turns: list[Any],
    agent: list[Any],
    *,
    dialect: str = "pipecat-protobuf/1",
    rate: int = 8000,
    codec: str = "pcm_s16le",
    wait_ms: int = 800,
    started: int | None = T0 - ms(300),
    ended: int | None = T0,
    established: bool = True,
    until: int | None = None,
) -> dict[str, Any]:
    rec = recorder_mod()
    last = max(
        [T0, *(t.last_frame_ns + ms(20) for t in turns), *(f.t_ns for f in agent)],
    )
    document: dict[str, Any] = simtiming_mod().extract_timing(
        dialect=dialect,
        fmt=rec.RecorderFormat(rate, codec),
        wait_for_agent_silence_ms=wait_ms,
        connect_started_ns=started,
        connect_ended_ns=ended,
        established=established,
        observed_until_ns=last + ms(10_000) if until is None else until,
        caller_turns=turns,
        agent_frames=agent,
    )
    return document


def by_seq(document: dict[str, Any], seq: int) -> dict[str, Any]:
    (found,) = [t for t in document["turns"] if t["turn_seq"] == seq]
    turn_record: dict[str, Any] = found
    return turn_record


# ----------------------------------------------------------------------------------------------
# The SCOPE tests of P4-07
# ----------------------------------------------------------------------------------------------


def test_timing_first_agent_audio_exact_on_synthetic_log() -> None:
    # silent frames first (zero samples are not audio), then the first active frame at
    # 1234.567 ms after the origin: the value is the floor, from the origin, not from T0 or the
    # start of the connection attempt
    agent = [
        log_frame(T0 + ms(0), silent()),
        log_frame(T0 + ms(20), silent()),
        log_frame(T0 + ms(40), silent()),
        log_frame(T0 + 1_234_567_000, active()),
        log_frame(T0 + 1_254_567_000, active()),
    ]
    document = extract([], agent)
    assert document["first_agent_audio_ms"] == 1234
    assert document["setup_ms"] == 300  # from the start of the attempt to the connection
    # floor, to the nanosecond
    for offset_ns, expected in (
        (0, 0),
        (999_999, 0),
        (1_000_000, 1),
        (1_000_001, 1),
        (1_999_999, 1),
        (2_000_000, 2),
        (10_000_000_000_001, 10_000_000),
    ):
        got = extract([], [log_frame(T0 + offset_ns, active())])
        assert got["first_agent_audio_ms"] == expected, offset_ns
    # an active frame at the origin is a measured 0, not a missing edge
    assert extract([], [log_frame(T0, active(amplitude=1))])["first_agent_audio_ms"] == 0
    # the first active frame counts even while the caller is speaking (it is the first sample)
    during = extract([turn(0, 1000)], [log_frame(T0 + ms(1050), active())])
    assert during["first_agent_audio_ms"] == 1050
    # a frame holding any non-zero sample is audio: one LSB in the last sample is enough
    quiet = pcm([0] * 159 + [1])
    assert extract([], [log_frame(T0 + ms(70), quiet)])["first_agent_audio_ms"] == 70
    # on a mu-law wire: 0xFF and 0x7F are zero (silent), 0xFE (+8) is audio
    mu = extract(
        [],
        [
            log_frame(T0 + ms(10), b"\xff" * 160, codec="mulaw"),
            log_frame(T0 + ms(30), b"\x7f" * 160, codec="mulaw"),
            log_frame(T0 + ms(50), b"\xff" * 159 + b"\xfe", codec="mulaw"),
        ],
        codec="mulaw",
        dialect="twilio-media-stream/1",
    )
    assert mu["first_agent_audio_ms"] == 50
    # no agent frame at all, or only zero frames: no value (never 0)
    assert extract([], [])["first_agent_audio_ms"] is None
    assert extract([], burst(T0, 5, silent()))["first_agent_audio_ms"] is None
    # a call that was not established has no origin: no value, and no turn
    never = extract([], [], established=False, ended=None)
    assert never["first_agent_audio_ms"] is None


@given(
    offset_ns=st.integers(min_value=0, max_value=3_600_000_000_000),
    origin_shift=st.integers(min_value=0, max_value=10**12),
    setup_ns=st.integers(min_value=0, max_value=60_000_000_000),
)
@HSETTINGS
def test_first_agent_audio_and_setup_are_floors_of_differences(
    offset_ns: int, origin_shift: int, setup_ns: int
) -> None:
    """Property: both values are ``floor(difference / 1 ms)`` of two instants, whatever the
    absolute clock reading (a monotonic clock has an arbitrary zero)."""
    origin = T0 + origin_shift
    document = extract(
        [],
        [log_frame(origin + offset_ns, active())],
        ended=origin,
        started=origin - setup_ns,
    )
    assert document["first_agent_audio_ms"] == offset_ns // 1_000_000
    assert document["setup_ms"] == setup_ns // 1_000_000


def test_timing_gap_measured_from_last_sent_frame() -> None:
    # turn 0: ten frames from 1000 ms; the last is sent at 1180 ms and ends at 1200 ms (its edge).
    # The agent's first active frame after the edge arrives at 1500 ms: the gap is 300 ms. A gap
    # taken from the first frame would be 500 ms; from the last frame's send instant, 320 ms.
    agent = [
        log_frame(T0 + ms(500), active()),  # before the turn: not an answer to it
        log_frame(T0 + ms(1100), silent()),  # zero samples: not audio
        log_frame(T0 + ms(1500), active()),
    ]
    document = extract([turn(0, 1000, 10)], agent)
    assert by_seq(document, 0)["gap_ms"] == 300
    assert document["gap_median_ms"] == 300
    assert document["gap_n"] == 1
    assert document["answered_caller_turns"] == 1
    # a different number of frames moves the edge: 3 frames from 1000 ms end at 1060 ms
    document = extract([turn(0, 1000, 3)], agent)
    assert by_seq(document, 0)["gap_ms"] == 440
    # one frame: the edge is the send instant plus one frame (20 ms)
    document = extract([turn(0, 1000, 1)], [log_frame(T0 + ms(1020), active())])
    assert by_seq(document, 0)["gap_ms"] == 0  # an active frame exactly at the edge is the answer
    document = extract([turn(0, 1000, 1)], [log_frame(T0 + ms(1020) - 1, active())])
    assert by_seq(document, 0)["gap_ms"] is None  # one ns before the edge: still the caller's line
    assert by_seq(document, 0)["overlap_ms"] == 20
    # the gap floors a fraction of a millisecond
    document = extract([turn(0, 1000, 1)], [log_frame(T0 + ms(1020) + 1_999_999, active())])
    assert by_seq(document, 0)["gap_ms"] == 1
    # several turns: each has its own gap; the median is the nearest rank (rank ceil(n / 2))
    agent = [
        log_frame(T0 + ms(1500), active()),  # turn 0 edge 1200: gap 300
        log_frame(T0 + ms(3400), active()),  # turn 1 edge 3100 (3000 + 5*20): gap 300
        log_frame(T0 + ms(5500), active()),  # turn 2 edge 5100 (5000 + 5*20): gap 400
        log_frame(T0 + ms(5600), active()),
    ]
    turns = [turn(0, 1000, 10), turn(1, 3000, 5), turn(2, 5000, 5)]
    document = extract(turns, agent)
    assert [by_seq(document, k)["gap_ms"] for k in range(3)] == [300, 300, 400]
    assert document["gap_median_ms"] == 300
    assert document["gap_n"] == 3
    assert document["answered_caller_turns"] == 3
    # an unanswered turn has no gap (not 0) and is not counted
    document = extract([turn(0, 1000, 10), turn(1, 3000, 5)], [log_frame(T0 + ms(1500), active())])
    # turn 1 has no active frame at or after its edge (3100 ms) except none at all after 1500
    assert by_seq(document, 1)["gap_ms"] is None
    assert document["answered_caller_turns"] == 1
    assert document["gap_n"] == 1
    # the origin is the connection instant, but a gap is a difference of two instants: it does not
    # change when the connection happened
    moved = extract(
        [turn(0, 1000, 10)],
        [log_frame(T0 + ms(1500), active())],
        started=T0 - ms(9_000),
    )
    assert by_seq(moved, 0)["gap_ms"] == 300


def test_simulator_self_latency_excluded_from_agent_gap() -> None:
    # Two calls with the same agent behaviour: the agent answers each caller line 300 ms (turn 0)
    # and 400 ms (turn 1) after the line ends. In the second call the simulator is slow (a brain
    # and a TTS that take seconds) so its second line starts much later. The agent gaps are the
    # same; the simulator's own times are recorded separately and never inside a gap.
    def call(slow_ms: int, own: Any) -> dict[str, Any]:
        second = 2600 + slow_ms  # the agent's reply to line 1 ends near 2000; silence 800 ms
        turns = [
            turn(0, 1000, 10, own=self_times(reply_ms=None)),
            turn(1, second, 5, own=own),
        ]
        edge1 = second + 100
        agent = [
            log_frame(T0 + ms(1500), active()),  # reply to line 0: gap 300
            log_frame(T0 + ms(1520), active()),
            log_frame(T0 + ms(edge1 + 400), active()),  # reply to line 1: gap 400
        ]
        return extract(turns, agent)

    fast = call(0, self_times(reply_ms=1100, wait_ms=800, stt_ms=0, brain_ms=0, tts_ms=0))
    slow = call(
        5000, self_times(reply_ms=6100, wait_ms=800, stt_ms=900, brain_ms=2500, tts_ms=1900)
    )
    for document in (fast, slow):
        assert by_seq(document, 0)["gap_ms"] == 300
        assert by_seq(document, 1)["gap_ms"] == 400
        assert document["gap_median_ms"] == 300
        assert document["gap_n"] == 2
    # the simulator's own times are in the document, verbatim, per turn, and are separate fields
    assert by_seq(slow, 1)["self"] == {
        "reply_ms": 6100,
        "wait_ms": 800,
        "stt_ms": 900,
        "brain_ms": 2500,
        "tts_ms": 1900,
    }
    assert by_seq(fast, 0)["self"] == dict.fromkeys(SELF_KEYS)
    # an extreme self time changes no agent value
    huge = call(5000, self_times(reply_ms=10**12, brain_ms=10**12))
    assert by_seq(huge, 1)["gap_ms"] == 400
    assert huge["gap_median_ms"] == 300
    plain = call(5000, self_times())
    assert [by_seq(plain, k)["gap_ms"] for k in (0, 1)] == [300, 400]
    # the gap ends at the agent's first sample, before the simulator decides to speak: the agent
    # answered 300 ms after the line and the simulator needed 4 s more: still 300
    late_speaker = extract(
        [turn(0, 1000, 10), turn(1, 7000, 5)],
        [log_frame(T0 + ms(1500), active()), log_frame(T0 + ms(1700), silent())],
    )
    assert by_seq(late_speaker, 0)["gap_ms"] == 300
    # self values are validated: non-negative integers or None
    st_mod = simtiming_mod()
    for key in SELF_KEYS:
        for bad in (-1, 1.5, True, "5"):
            with pytest.raises(st_mod.TimingDocumentError):
                st_mod.SelfTimes(**{**dict.fromkeys(SELF_KEYS), key: bad})


def test_timing_values_carry_quantum_and_dialect() -> None:
    # the dialect is the one the call used, for each of the three
    for dialect in DIALECTS:
        document = extract([], [], dialect=dialect)
        assert document["dialect"] == dialect
        assert document["format"] == "tracer.timing/1"
        assert document["clock"] == "simulator_monotonic"
        assert document["caller_frame_ms"] == 20
    with pytest.raises(simtiming_mod().TimingDocumentError):
        extract([], [], dialect="pipecat-protobuf/2")
    with pytest.raises(simtiming_mod().TimingDocumentError):
        extract([], [], dialect="")
    # the quantum is the larger of the caller frame (20 ms) and the longest agent frame received,
    # rounded UP to a whole millisecond (the position of a sample inside a frame is not observed)
    cases = [
        ([], 20),  # no agent frame: the caller frame
        ([silent(160)], 20),  # 20 ms
        ([active(80)], 20),  # 10 ms: the caller frame is larger
        ([active(320)], 40),  # 40 ms
        ([active(172)], 22),  # 21.5 ms rounds up
        ([active(161)], 21),  # 20.125 ms rounds up
        ([active(160), silent(480), active(80)], 60),  # the longest frame, silent or not
        ([silent(1)], 20),
    ]
    for payloads, quantum in cases:
        agent = [log_frame(T0 + ms(50 * k), p) for k, p in enumerate(payloads)]
        assert extract([], agent)["quantum_ms"] == quantum, quantum
    # the quantum is in milliseconds of the dialect's rate: mu-law at 8 kHz is 1 byte = 1 sample
    mu = extract(
        [],
        [log_frame(T0, b"\xfe" * 330, codec="mulaw")],
        codec="mulaw",
        dialect="twilio-media-stream/1",
    )
    assert mu["quantum_ms"] == 42  # 41.25 ms
    wide = extract([], [log_frame(T0, active(960), rate=48000)], rate=48000)
    assert wide["quantum_ms"] == 20  # 20 ms again
    short = extract([], [log_frame(T0, active(480), rate=48000)], rate=48000)
    assert short["quantum_ms"] == 20  # 10 ms: not below the caller frame
    # a call that was never established still states them
    never = extract([], [], established=False, ended=None, dialect="smallwebrtc/1")
    assert (never["quantum_ms"], never["dialect"], never["caller_frame_ms"]) == (
        20,
        "smallwebrtc/1",
        20,
    )
    # the document is JSON of integers, strings and null only, and validates against the schema
    document = extract(
        [turn(0, 1000, 10, barge=100, own=self_times(reply_ms=5))],
        burst(T0 + ms(1000), 20, active()),
    )
    text = json.dumps(document)
    assert json.loads(text) == document
    assert not re.search(r"\d\.\d", text.replace("tracer.timing/1", "").replace("/1", ""))
    Draft202012Validator(simtiming_mod().TIMING_JSON_SCHEMA).validate(document)
    simtiming_mod().validate_timing_document(document)


def test_timing_value_without_both_edges_is_error_never_zero() -> None:
    st_mod = simtiming_mod()
    # an agent that never speaks: every value that needs an agent edge is absent
    silent_agent = extract([turn(0, 1000, 10), turn(1, 3000, 5)], burst(T0 + ms(100), 30, silent()))
    assert silent_agent["first_agent_audio_ms"] is None
    assert silent_agent["gap_median_ms"] is None
    assert silent_agent["gap_n"] == 0
    assert silent_agent["answered_caller_turns"] == 0
    assert silent_agent["barge_in_stop_median_ms"] is None
    assert silent_agent["barge_in_n"] == 0
    assert all(t["gap_ms"] is None for t in silent_agent["turns"])
    # ...and each such metric is an ERROR with the code, a message with the missing edge and the
    # counts, and no value: never 0, never a pass or a fail
    first = st_mod.timing_value(silent_agent, "sim_first_agent_audio_ms")
    assert (first.value, first.error_code) == (None, "timing_edge_missing")
    assert first.message == (
        "timing_edge_missing: first_agent_audio_ms has no value; "
        "missing edge: first active agent frame; caller turns 2"
    )
    gap = st_mod.timing_value(silent_agent, "sim_agent_response_gap_median_ms")
    assert (gap.value, gap.error_code) == (None, "timing_edge_missing")
    assert gap.message == (
        "timing_edge_missing: gap_median_ms has no value; "
        "missing edge: active agent frame after a caller turn; caller turns 2, gap_n 0"
    )
    barge = st_mod.timing_value(silent_agent, "sim_barge_in_stop_ms")
    assert (barge.value, barge.error_code) == (None, "timing_edge_missing")
    assert barge.message == (
        "timing_edge_missing: barge_in_stop_median_ms has no value; "
        "missing edge: end of the interrupted agent activity; caller turns 2, barge_in_n 0"
    )
    assert st_mod.TIMING_EDGE_MISSING == "timing_edge_missing"
    # overlap with a silent agent is a measured 0 (there was an established call and nothing
    # overlapped); the count of answered turns is a count, 0 is its value; neither is an error
    overlap = st_mod.timing_value(silent_agent, "sim_overlap_ms")
    assert (overlap.value, overlap.error_code, overlap.message) == (0, None, None)
    answered = st_mod.timing_value(silent_agent, "sim_answered_caller_turns")
    assert (answered.value, answered.error_code, answered.message) == (0, None, None)
    # a call that was never established: overlap has no value either (the only null overlap)
    never = extract([], [], established=False, ended=None, started=None)
    assert never["overlap_ms"] is None
    assert never["setup_ms"] is None
    overlap = st_mod.timing_value(never, "sim_overlap_ms")
    assert overlap.value is None
    assert overlap.error_code == "timing_edge_missing"
    assert overlap.message == (
        "timing_edge_missing: overlap_ms has no value; missing edge: established call; "
        "caller turns 0"
    )
    # a barge-in the agent never stopped for (still talking when the observation ends) has no stop
    talking = extract(
        [turn(0, 1000, 10, barge=100)],
        burst(T0 + ms(900), 60, active()),  # active from 900 ms to 2080 ms
        until=T0 + ms(2100),  # observation ends before the agent was silent for 800 ms
    )
    assert by_seq(talking, 0)["barge_in_after_ms"] == 100
    assert by_seq(talking, 0)["barge_in_stop_ms"] is None
    assert talking["barge_in_n"] == 0
    assert talking["barge_in_stop_median_ms"] is None
    assert st_mod.timing_value(talking, "sim_barge_in_stop_ms").error_code == "timing_edge_missing"
    # a barge-in that the agent did stop for has a value
    stopped = extract(
        [turn(0, 1000, 10, barge=100)],
        burst(T0 + ms(900), 11, active()),  # active 900..1100, its last frame ends at 1120
    )
    assert by_seq(stopped, 0)["barge_in_stop_ms"] == 120
    assert stopped["barge_in_n"] == 1
    assert stopped["barge_in_stop_median_ms"] == 120
    ok = st_mod.timing_value(stopped, "sim_barge_in_stop_ms")
    assert (ok.value, ok.error_code, ok.message) == (120, None, None)
    # the document refuses a fabricated value: a median of 0 with no turn behind it
    forged = copy.deepcopy(silent_agent)
    forged["gap_median_ms"] = 0
    with pytest.raises(st_mod.TimingDocumentError):
        st_mod.validate_timing_document(forged)
    forged = copy.deepcopy(silent_agent)
    forged["barge_in_stop_median_ms"] = 0
    with pytest.raises(st_mod.TimingDocumentError):
        st_mod.validate_timing_document(forged)
    with pytest.raises(st_mod.TimingDocumentError):
        st_mod.timing_value(silent_agent, "sim_wer")


# ----------------------------------------------------------------------------------------------
# Extra tests (each with its reason)
# ----------------------------------------------------------------------------------------------


@given(
    sizes=st.lists(st.integers(min_value=1, max_value=4000), min_size=0, max_size=6),
    rate=st.sampled_from([8000, 11025, 16000, 22050, 24000, 44100, 48000]),
)
@HSETTINGS
def test_quantum_is_the_larger_of_the_caller_frame_and_the_longest_agent_frame(
    sizes: list[int], rate: int
) -> None:
    """Property: ``quantum_ms = max(20, ceil(longest agent frame in ms))`` in exact arithmetic,
    over frames of any size and the rates a dialect can use (silent frames count: the quantum is
    about what was received, not about what was audible)."""
    agent = [
        log_frame(T0 + ms(50 * k), active(n, 1) if k % 2 else silent(n), rate=rate)
        for k, n in enumerate(sizes)
    ]
    document = extract([], agent, rate=rate)
    longest_ms_ceil = max((-(-n * 1000 // rate) for n in sizes), default=0)
    assert document["quantum_ms"] == max(20, longest_ms_ceil)
    assert document["quantum_ms"] >= document["caller_frame_ms"]


def test_overlap_is_the_duration_of_active_agent_frames_arriving_while_the_caller_speaks() -> None:
    """ADR 0027 rule 10: per turn, the summed duration of active agent frames that arrive while a
    caller line is being sent (from its first frame to its edge, the edge excluded); the total is
    the sum of the turns; silent frames, frames before the line and frames at the edge count for
    nothing; the sum is floored once per turn."""
    agent = [
        log_frame(T0 + ms(990), active()),  # before the line (1000): no
        log_frame(T0 + ms(1000), active()),  # at the first frame: yes (20 ms)
        log_frame(T0 + ms(1100), silent()),  # zero samples: no
        log_frame(T0 + ms(1190), active(80)),  # 10 ms: yes
        log_frame(T0 + ms(1200), active()),  # at the edge: no
    ]
    document = extract([turn(0, 1000, 10)], agent)
    assert by_seq(document, 0)["overlap_ms"] == 30
    assert document["overlap_ms"] == 30
    # sub-millisecond durations are summed and floored once: 3 frames of 10.667 ms = 32 ms
    third = [log_frame(T0 + ms(1000) + 1000 * k, active(85)) for k in range(3)]
    assert 85 * 1_000_000_000 // 8000 == 10_625_000
    assert by_seq(extract([turn(0, 1000, 10)], third), 0)["overlap_ms"] == 31
    # two turns add up
    agent = [log_frame(T0 + ms(1000), active()), log_frame(T0 + ms(3000), active(320))]
    document = extract([turn(0, 1000, 10), turn(1, 3000, 5)], agent)
    assert [by_seq(document, k)["overlap_ms"] for k in (0, 1)] == [20, 40]
    assert document["overlap_ms"] == 60
    # no turn at all: an established call with nothing overlapped is 0, not absent
    assert extract([], [log_frame(T0 + ms(5), active())])["overlap_ms"] == 0


def test_barge_in_stop_is_the_end_of_the_interrupted_activity() -> None:
    """ADR 0027 rule 10: from the send instant of the turn's first frame to the end of the last
    active agent frame of the activity it interrupted. A run of activity ends at a pause of
    ``wait_for_agent_silence_ms``; nothing interrupted, or an unfinished run, gives no value."""
    # the agent talks from 900 to 1500 ms (its last frame ends at 1520); the caller barges in at
    # 1000 ms
    talk = burst(T0 + ms(900), 31, active())
    document = extract([turn(0, 1000, 10, barge=100)], talk)
    assert by_seq(document, 0)["barge_in_stop_ms"] == 520
    # a pause shorter than the silence value keeps one run; a longer pause ends it
    resumed = [*burst(T0 + ms(900), 11, active()), *burst(T0 + ms(1500), 5, active())]
    assert by_seq(extract([turn(0, 1000, 10, barge=100)], resumed), 0)["barge_in_stop_ms"] == 600
    resumed_far = [*burst(T0 + ms(900), 11, active()), *burst(T0 + ms(1920), 5, active())]
    assert (
        by_seq(extract([turn(0, 1000, 10, barge=100)], resumed_far), 0)["barge_in_stop_ms"] == 120
    )
    # a pause of exactly the silence value ends the run (the agent is silent by then); one
    # millisecond less keeps it: the second burst starts 800 ms, or 799 ms, after the first
    # run's last frame (the first run's last frame arrives at 1100 ms)
    exact_pause = [*burst(T0 + ms(900), 11, active()), *burst(T0 + ms(1900), 5, active())]
    edge_doc = extract([turn(0, 1000, 10, barge=100)], exact_pause)
    assert by_seq(edge_doc, 0)["barge_in_stop_ms"] == 120
    short_pause = [*burst(T0 + ms(900), 11, active()), *burst(T0 + ms(1899), 5, active())]
    assert (
        by_seq(extract([turn(0, 1000, 10, barge=100)], short_pause), 0)["barge_in_stop_ms"] == 999
    )
    # the agent had already been silent for the whole silence value: nothing was interrupted
    gone = burst(T0 + ms(100), 5, active())  # last frame arrives at 180
    assert by_seq(extract([turn(0, 1000, 10, barge=100)], gone), 0)["barge_in_stop_ms"] is None
    # the activity ended before the line began (its last frame ends before the first frame)
    ended = burst(T0 + ms(900), 4, active())  # frames 900..960, the last ends at 980
    assert by_seq(extract([turn(0, 1000, 10, barge=100)], ended), 0)["barge_in_stop_ms"] is None
    # an activity that begins after the line started was not interrupted by it
    later = burst(T0 + ms(1100), 5, active())
    assert by_seq(extract([turn(0, 1000, 10, barge=100)], later), 0)["barge_in_stop_ms"] is None
    # a turn without a scheduled barge-in has no stop, whatever the agent does
    assert by_seq(extract([turn(0, 1000, 10)], talk), 0)["barge_in_stop_ms"] is None
    # the median over several barge-ins is the nearest rank, with N
    agent = [*burst(T0 + ms(900), 11, active()), *burst(T0 + ms(3900), 21, active())]
    document = extract([turn(0, 1000, 10, barge=0), turn(1, 4000, 10, barge=5)], agent)
    assert by_seq(document, 0)["barge_in_stop_ms"] == 120
    assert by_seq(document, 1)["barge_in_stop_ms"] == 320
    assert document["barge_in_n"] == 2
    assert document["barge_in_stop_median_ms"] == 120


def test_nearest_rank_median_helper() -> None:
    """The nearest-rank median of ADR 0027 (the value at rank ceil(n / 2) of the sorted values),
    exhaustively for small lists; no mean, no interpolation, ``None`` for no value."""
    median = simtiming_mod().nearest_rank_median
    assert median([]) is None
    assert median([5]) == 5
    assert median([1, 2]) == 1
    assert median([2, 1]) == 1
    assert median([1, 2, 3]) == 2
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2
    assert median([4, 3, 2, 1]) == 2
    assert median([10, 10, 20, 20, 30]) == 20
    for n in range(1, 8):
        values = [7 * k + (k % 3) for k in range(n)]
        for shift in range(n):
            rotated = values[shift:] + values[:shift]
            assert median(rotated) == sorted(values)[-(-n // 2) - 1]


@given(values=st.lists(st.integers(min_value=0, max_value=10**9), min_size=1, max_size=30))
@HSETTINGS
def test_nearest_rank_median_is_a_member_with_half_at_or_below(values: list[int]) -> None:
    """Property: the median is one of the values, at least half are at or below it and fewer
    than half are below it (the definition of the nearest-rank median, with no interpolation)."""
    got = simtiming_mod().nearest_rank_median(values)
    assert got in values
    assert sum(1 for v in values if v <= got) >= (len(values) + 1) // 2
    assert sum(1 for v in values if v < got) < (len(values) + 1) // 2


def test_turn_and_call_inputs_are_checked_not_repaired() -> None:
    """Inputs that cannot be a real call are refused: turns out of order or overlapping, frames
    before the origin or unordered, frames of a call that was not established, and bad instants."""
    st_mod = simtiming_mod()
    rec = recorder_mod()
    one = turn(0, 1000, 5)
    with pytest.raises(st_mod.TimingDocumentError):  # turns not in send order
        extract([turn(1, 3000, 5), turn(0, 1000, 5)], [])
    with pytest.raises(st_mod.TimingDocumentError):  # the second begins before the first ends
        extract([turn(0, 1000, 10), turn(1, 1100, 5)], [])
    with pytest.raises(st_mod.TimingDocumentError):  # a repeated sequence number
        extract([turn(0, 1000, 5), turn(0, 3000, 5)], [])
    with pytest.raises(st_mod.TimingDocumentError):  # a turn before the connection was established
        extract([turn(0, 0, 5, first_extra_ns=-1)], [])
    with pytest.raises(rec.RecorderFrameError) as error:  # an agent frame before the origin
        extract([one], [log_frame(T0 - 1, active())])
    assert (error.value.kind, error.value.reason) == ("before_origin", "agent")
    with pytest.raises(rec.RecorderFrameError):  # unordered agent frames
        extract([], [log_frame(T0 + ms(40), active()), log_frame(T0 + ms(20), active())])
    with pytest.raises(rec.RecorderFrameError):  # another rate: the quantum is never guessed
        extract([], [log_frame(T0, active(), rate=16000)])
    with pytest.raises(st_mod.TimingDocumentError):  # frames of a call that was never established
        extract([], [log_frame(T0, active())], established=False, ended=None)
    with pytest.raises(st_mod.TimingDocumentError):  # established without an instant
        extract([], [], established=True, ended=None)
    with pytest.raises(st_mod.TimingDocumentError):  # the attempt ended before it started
        extract([], [], started=T0, ended=T0 - 1)
    with pytest.raises(st_mod.TimingDocumentError):  # observation ended before the last event
        extract([one], [], until=T0 + ms(1000))
    for bad in (0, -1, True, 0.5, None):
        with pytest.raises(st_mod.TimingDocumentError):
            extract([], [], wait_ms=bad)  # type: ignore[arg-type]
    with pytest.raises(st_mod.TimingDocumentError):
        st_mod.CallerTurn(0, 5, 4, None, self_times())  # last frame before the first
    with pytest.raises(st_mod.TimingDocumentError):
        st_mod.CallerTurn(-1, 4, 5, None, self_times())
    with pytest.raises(st_mod.TimingDocumentError):
        st_mod.CallerTurn(0, 4, 5, -1, self_times())
    # a failed connection attempt still has its setup time (a measured instant), and nothing else
    failed = extract([], [], established=False, ended=T0, started=T0 - ms(1500))
    assert failed["setup_ms"] == 1500
    assert failed["first_agent_audio_ms"] is None
    assert failed["overlap_ms"] is None
    assert failed["turns"] == []
    # an attempt that never completed or failed (killed while connecting) has no setup time
    assert extract([], [], established=False, ended=None, started=T0)["setup_ms"] is None


# ---------------------------------------------------------------------- reference and properties


def ref_timing(
    *,
    dialect: str,
    rate: int,
    codec: str,
    wait_ms: int,
    started: int | None,
    ended: int | None,
    established: bool,
    until: int,
    turns: list[Any],
    agent: list[Any],
) -> dict[str, Any]:
    """The expected document, from loops over plain lists (independent of the implementation)."""
    ns = 1_000_000

    def audible(frame: Any) -> bool:
        if codec == "pcm_s16le":
            return any(frame.payload)
        table = ref_mulaw if codec == "mulaw" else ref_alaw
        return any(table(b) != 0 for b in frame.payload)

    def duration(frame: Any) -> int:
        count = len(frame.payload) // 2 if codec == "pcm_s16le" else len(frame.payload)
        return count * 1_000_000_000 // rate

    setup = (ended - started) // ns if started is not None and ended is not None else None
    doc: dict[str, Any] = {
        "format": "tracer.timing/1",
        "dialect": dialect,
        "clock": "simulator_monotonic",
        "caller_frame_ms": 20,
        "setup_ms": setup,
    }
    if not established:
        doc.update(
            quantum_ms=20,
            first_agent_audio_ms=None,
            overlap_ms=None,
            answered_caller_turns=0,
            gap_median_ms=None,
            gap_n=0,
            barge_in_stop_median_ms=None,
            barge_in_n=0,
            turns=[],
        )
        return doc
    assert ended is not None
    longest = max([duration(f) for f in agent], default=0)
    doc["quantum_ms"] = max(20, -(-longest // ns))
    live = [(f.t_ns, f.t_ns + duration(f)) for f in agent if audible(f)]
    doc["first_agent_audio_ms"] = (live[0][0] - ended) // ns if live else None
    out_turns = []
    for t in turns:
        edge = t.last_frame_ns + 20 * ns
        gap = None
        for arrival, _end in live:
            if arrival >= edge:
                gap = (arrival - edge) // ns
                break
        overlap = sum(e - a for a, e in live if t.first_frame_ns <= a < edge) // ns
        stop = None
        if t.barge_in_after_ms is not None:
            stop = ref_stop(live, t.first_frame_ns, until, wait_ms * ns)
        out_turns.append(
            {
                "turn_seq": t.turn_seq,
                "gap_ms": gap,
                "overlap_ms": overlap,
                "barge_in_after_ms": t.barge_in_after_ms,
                "barge_in_stop_ms": stop,
                "self": {k: getattr(t.self_times, k) for k in SELF_KEYS},
            }
        )
    gaps = sorted(t["gap_ms"] for t in out_turns if t["gap_ms"] is not None)
    stops = sorted(t["barge_in_stop_ms"] for t in out_turns if t["barge_in_stop_ms"] is not None)
    doc.update(
        overlap_ms=sum(t["overlap_ms"] for t in out_turns),
        answered_caller_turns=len(gaps),
        gap_median_ms=gaps[(len(gaps) + 1) // 2 - 1] if gaps else None,
        gap_n=len(gaps),
        barge_in_stop_median_ms=stops[(len(stops) + 1) // 2 - 1] if stops else None,
        barge_in_n=len(stops),
        turns=out_turns,
    )
    return doc


def ref_stop(live: list[tuple[int, int]], t0: int, until: int, silence: int) -> int | None:
    """End of the run of activity (frames closer than ``silence``) that was in progress at t0."""
    runs: list[list[tuple[int, int]]] = []
    for frame in live:
        if runs and frame[0] - runs[-1][-1][0] < silence:
            runs[-1].append(frame)
        else:
            runs.append([frame])
    chosen = None
    last_before = 0
    for run in runs:
        for arrival, _ in run:
            if arrival <= t0:
                chosen = run
                last_before = arrival
    if chosen is None or t0 - last_before >= silence:
        return None
    if until - chosen[-1][0] < silence:
        return None
    stop = chosen[-1][1] - t0
    return stop // 1_000_000 if stop > 0 else None


@st.composite
def calls(draw: st.DrawFn) -> dict[str, Any]:
    wait_ms = draw(st.sampled_from([100, 400, 800]))
    n_turns = draw(st.integers(0, 4))
    cursor = T0 + ms(draw(st.integers(0, 300)))
    turns = []
    for seq in range(n_turns):
        cursor += draw(st.integers(0, 1_500_000_000))
        frames = draw(st.integers(1, 6))
        barge = draw(st.one_of(st.none(), st.integers(0, 3000)))
        own = self_times(
            reply_ms=draw(st.one_of(st.none(), st.integers(0, 10_000))),
            brain_ms=draw(st.one_of(st.none(), st.integers(0, 10_000))),
        )
        first = cursor
        last = first + ms(20 * (frames - 1))
        turns.append(
            simtiming_mod().CallerTurn(
                turn_seq=seq * 2 + draw(st.integers(0, 1)),
                first_frame_ns=first,
                last_frame_ns=last,
                barge_in_after_ms=barge,
                self_times=own,
            )
        )
        cursor = last + ms(20)
    total = max(cursor, T0) - T0 + 6_000_000_000
    n_agent = draw(st.integers(0, 14))
    times = sorted(draw(st.integers(0, total)) for _ in range(n_agent))
    sizes = draw(st.lists(st.sampled_from([80, 160, 172, 320]), min_size=n_agent, max_size=n_agent))
    flags = draw(st.lists(st.booleans(), min_size=n_agent, max_size=n_agent))
    agent = [
        log_frame(T0 + t, active(n, 3) if flag else silent(n))
        for t, n, flag in zip(times, sizes, flags, strict=True)
    ]
    return {
        "turns": turns,
        "agent": agent,
        "wait_ms": wait_ms,
        "setup_ns": draw(st.integers(0, 5_000_000_000)),
        "tail_ns": draw(st.integers(0, 3_000_000_000)),
    }


@given(call=calls())
@HSETTINGS
def test_extraction_matches_the_reference_on_random_calls(call: dict[str, Any]) -> None:
    """Property: for random established calls the whole document equals the reference's, and it
    validates (structure and cross-field rules). Missing edges are None, never 0: a document with
    no active agent frame has no first-audio, no gap and no barge-in stop."""
    last = max(
        [T0, *(t.last_frame_ns + ms(20) for t in call["turns"]), *(f.t_ns for f in call["agent"])]
    )
    until = last + call["tail_ns"]
    kwargs = {
        "wait_ms": call["wait_ms"],
        "started": T0 - call["setup_ns"],
        "ended": T0,
        "until": until,
    }
    got = extract(call["turns"], call["agent"], **kwargs)
    expected = ref_timing(
        dialect="pipecat-protobuf/1",
        rate=8000,
        codec="pcm_s16le",
        wait_ms=call["wait_ms"],
        started=T0 - call["setup_ns"],
        ended=T0,
        established=True,
        until=until,
        turns=call["turns"],
        agent=call["agent"],
    )
    assert got == expected
    Draft202012Validator(simtiming_mod().TIMING_JSON_SCHEMA).validate(got)
    simtiming_mod().validate_timing_document(got)
    if not any(any(f.payload) for f in call["agent"]):
        assert got["first_agent_audio_ms"] is None
        assert got["gap_median_ms"] is None
        assert got["gap_n"] == 0
        assert got["barge_in_stop_median_ms"] is None
        assert all(t["gap_ms"] is None and t["barge_in_stop_ms"] is None for t in got["turns"])
    # the same call twice gives the same document (no hidden state)
    assert extract(call["turns"], call["agent"], **kwargs) == got


def good_document() -> dict[str, Any]:
    agent = [log_frame(T0 + ms(1500), active()), log_frame(T0 + ms(3400), active())]
    return extract(
        [turn(0, 1000, 10, barge=0, own=self_times(reply_ms=3)), turn(1, 3000, 5)], agent
    )


def test_the_validator_accepts_what_the_extractor_builds_and_refuses_each_fault() -> None:
    """Every structural fault is refused by the validator and by the JSON Schema; every
    cross-field fault by the validator (the schema alone cannot state it). The refusal message is
    the stable code and never a value."""
    st_mod = simtiming_mod()
    schema = Draft202012Validator(st_mod.TIMING_JSON_SCHEMA)
    base = good_document()
    schema.validate(base)
    st_mod.validate_timing_document(base)

    def mutate(path: list[Any], value: Any) -> dict[str, Any]:
        doc = copy.deepcopy(base)
        node: Any = doc
        for key in path[:-1]:
            node = node[key]
        if value is KeyError:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return doc

    structural = [
        mutate(["format"], "tracer.timing/2"),
        mutate(["dialect"], "pipecat-protobuf/2"),
        mutate(["clock"], "wall"),
        mutate(["caller_frame_ms"], 10),
        mutate(["quantum_ms"], 0),
        mutate(["quantum_ms"], -1),
        mutate(["quantum_ms"], 2**53),
        mutate(["quantum_ms"], True),
        mutate(["quantum_ms"], "20"),
        mutate(["setup_ms"], -1),
        mutate(["setup_ms"], "300"),
        mutate(["first_agent_audio_ms"], 1.5),
        mutate(["gap_n"], True),
        mutate(["turns", 0, "gap_ms"], -3),
        mutate(["turns", 0, "turn_seq"], -1),
        mutate(["turns", 0, "overlap_ms"], None),
        mutate(["turns", 0, "self"], {"reply_ms": 1}),
        mutate(["turns", 0, "self", "reply_ms"], -1),
        mutate(["turns", 0, "extra"], 1),
        mutate(["extra"], 1),
        mutate(["turns"], KeyError),
        mutate(["gap_median_ms"], KeyError),
        mutate(["turns", 0, "self", "tts_ms"], KeyError),
    ]
    for doc in structural:
        assert list(schema.iter_errors(doc)), doc
        with pytest.raises(st_mod.TimingDocumentError) as error:
            st_mod.validate_timing_document(doc)
        assert re.fullmatch(r"[a-z][a-z0-9_]*", str(error.value)), str(error.value)
        assert error.value.code == str(error.value)
    cross = [
        mutate(["answered_caller_turns"], 0),
        mutate(["gap_n"], 0),
        mutate(["gap_median_ms"], 99999),
        mutate(["gap_median_ms"], None),
        mutate(["barge_in_n"], 1),
        mutate(["barge_in_stop_median_ms"], 5),
        mutate(["overlap_ms"], 12345),
        mutate(["overlap_ms"], None),  # a turn exists, so the call was established
    ]
    # a stop for a turn with no barge-in, with the counts made consistent so that this is the
    # only fault
    lone = mutate(["turns", 1, "barge_in_stop_ms"], 5)
    lone["barge_in_n"] = 1
    lone["barge_in_stop_median_ms"] = 5
    cross.append(lone)
    assert base["turns"][1]["barge_in_after_ms"] is None
    swapped = copy.deepcopy(base)
    swapped["turns"].reverse()
    cross.append(swapped)
    duplicate = copy.deepcopy(base)
    duplicate["turns"][1]["turn_seq"] = duplicate["turns"][0]["turn_seq"]
    cross.append(duplicate)
    for doc in cross:
        assert not list(schema.iter_errors(doc)), "the schema alone accepts this fault"
        with pytest.raises(st_mod.TimingDocumentError):
            st_mod.validate_timing_document(doc)
    junk: Any
    for junk in (None, [], "x", 3):
        with pytest.raises(st_mod.TimingDocumentError):
            st_mod.validate_timing_document(junk)
    assert st_mod.TIMING_METADATA_KEY == "tracer.timing"
    assert st_mod.TIMING_FORMAT == "tracer.timing/1"
    assert issubclass(st_mod.TimingDocumentError, ValueError)
