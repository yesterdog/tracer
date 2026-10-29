# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-05: turn-taking of the simulated caller (ADR 0026 rule 10 and invariants I5, I6; ADR 0028
rule 9; contract docs/phase4/api-surface-batch-a.md section 3).

Time is an integer number of nanoseconds (``ms()`` writes milliseconds); the tests read no clock.
The modules are loaded lazily, so before they exist every test fails on its own with
``ModuleNotFoundError``. The reference decision lives in ``evalcore_batcha_support`` and is
written from the contract's rule, not from the implementation.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
import struct
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]
from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    EVALCORE_SRC,
    PATHS,
    active,
    callstate_mod,
    drive,
    frame_sequences,
    ms,
    pcm,
    recorder_mod,
    ref_turn_decision,
    silent,
    turntaking_mod,
)

POLICY_KEYS = ("caller_speaks_first", "wait_for_agent_silence_ms", "max_silence_ms")

# Deterministic hypothesis profile (as Phase 1 to 3): derandomised, no deadline.
HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def policy(first: bool = False, wait: int = 800, maximum: int = 5000) -> Any:
    return turntaking_mod().TurnTakingPolicy(
        caller_speaks_first=first, wait_for_agent_silence_ms=wait, max_silence_ms=maximum
    )


def taker(first: bool = False, wait: int = 800, maximum: int = 5000) -> Any:
    return turntaking_mod().TurnTaker(policy(first, wait, maximum))


def shape(decision: Any) -> tuple[str, int | None, str | None]:
    return (decision.kind, decision.at_ns, decision.cause)


def feed(tk: Any, frames: list[tuple[int, bytes]]) -> None:
    for t_ns, payload in frames:
        tk.agent_frame(t_ns, payload)


def every_20ms(start_ms: int, stop_ms: int, payload: bytes) -> list[tuple[int, bytes]]:
    return [(ms(t), payload) for t in range(start_ms, stop_ms + 1, 20)]


# ----------------------------------------------------------------------------------------------
# The SCOPE tests of P4-05
# ----------------------------------------------------------------------------------------------


def test_agent_activity_is_any_nonzero_sample_no_threshold() -> None:
    tt = turntaking_mod()
    is_active = tt.agent_frame_is_active
    # all-zero frames of several sizes are silent
    for n in (1, 2, 80, 160, 320, 960):
        assert is_active(silent(n)) is False, n
    # one non-zero sample anywhere makes the frame active, however small
    for value in (1, -1, 2, -2, 3, 7, 15, 31, 100, 255, 256, 257, 1000, 32767, -32768):
        for position in (0, 1, 79, 158, 159):
            samples = [0] * 160
            samples[position] = value
            assert is_active(pcm(samples)) is True, (value, position)
    # the high byte alone and the low byte alone both count (a sample is two bytes)
    assert is_active(b"\x00\x01") is True  # 256
    assert is_active(b"\x01\x00") is True  # 1
    assert is_active(b"\x00\x80") is True  # -32768
    assert is_active(b"\x00\x00\x00\x00") is False
    # a frame that is nearly silent is as active as a loud one: no energy threshold
    quiet = pcm([1, 0, -1, 0] * 40)
    loud = pcm([20000, -20000] * 80)
    assert is_active(quiet) is True
    assert is_active(loud) is True
    # the TurnTaker reports the same value for every frame it is given
    tk = taker()
    tk.begin_wait(0)
    assert tk.agent_frame(ms(1), silent()) is False
    assert tk.agent_frame(ms(2), quiet) is True
    # decoded G.711: mu-law 0xFF and 0x7F are zero (silent); 0xFE is +8 (active, no threshold);
    # A-law has no zero at all: 0xD5 decodes to +8, so an A-law frame is always active
    rec = recorder_mod()
    assert is_active(rec.decode_to_pcm16("mulaw", b"\xff" * 160)) is False
    assert is_active(rec.decode_to_pcm16("mulaw", b"\x7f" * 160)) is False
    assert is_active(rec.decode_to_pcm16("mulaw", b"\xff" * 159 + b"\xfe")) is True
    assert is_active(rec.decode_to_pcm16("alaw", b"\xd5" * 160)) is True
    # refusals: a frame is a non-empty even number of bytes
    for bad in (b"", b"\x00", b"\x00\x00\x00", bytearray(b"\x00\x00"), "ab", None, [0, 0], 5):
        with pytest.raises(tt.TurnTakingError):
            is_active(bad)
    assert issubclass(tt.TurnTakingError, ValueError)


@given(
    samples=st.lists(
        st.one_of(st.just(0), st.integers(min_value=-32768, max_value=32767)),
        min_size=1,
        max_size=64,
    )
)
@HSETTINGS
def test_activity_equals_any_nonzero_sample_for_random_frames(samples: list[int]) -> None:
    """The definition itself, compared with a direct decoding of the samples (mostly zeros, so
    the one non-zero sample case is generated often)."""
    got = turntaking_mod().agent_frame_is_active(struct.pack(f"<{len(samples)}h", *samples))
    assert got is any(s != 0 for s in samples)


def test_wait_for_silence_parameter_has_no_default() -> None:
    tt = turntaking_mod()
    cls = tt.TurnTakingPolicy
    # dataclass fields: required, no default, no factory
    fields = {f.name: f for f in dataclasses.fields(cls)}
    # BL-313: the one optional value, ``activity_min_peak``, follows the three required ones; it is
    # the only field with a default and the default is 1 (any non-zero sample)
    assert tuple(fields) == (*POLICY_KEYS, "activity_min_peak")
    for name in POLICY_KEYS:
        field = fields[name]
        assert field.default is dataclasses.MISSING, name
        assert field.default_factory is dataclasses.MISSING, name
    assert fields["activity_min_peak"].default == 1
    assert all(
        p.default is inspect.Parameter.empty
        for name, p in inspect.signature(cls).parameters.items()
        if name in POLICY_KEYS
    )
    full = {"caller_speaks_first": False, "wait_for_agent_silence_ms": 800, "max_silence_ms": 5000}
    assert cls(**full).wait_for_agent_silence_ms == 800
    for key in POLICY_KEYS:
        partial = {k: v for k, v in full.items() if k != key}
        with pytest.raises(TypeError):
            cls(**partial)
        with pytest.raises(tt.TurnTakingError) as error:
            cls.from_profile(partial)
        assert str(error.value) == f"{key}_missing"
        assert error.value.code == f"{key}_missing"
    assert cls.from_profile(full) == cls(**full)
    with pytest.raises(tt.TurnTakingError):
        cls.from_profile({**full, "extra": 1})
    with pytest.raises(tt.TurnTakingError):
        cls.from_profile(None)
    # the ADR's schema requires the three keys and gives no default
    schema = _turn_taking_schema()
    assert tuple(sorted(schema["required"])) == tuple(sorted(POLICY_KEYS))
    assert "default" not in json.dumps(schema)
    assert set(schema["properties"]) == {*POLICY_KEYS, "activity_min_peak"}
    # no module of the package gives any of them a default value (a parameter default, or an
    # assignment of a constant), whatever the spelling
    offences = []
    for path in sorted(EVALCORE_SRC.rglob("*.py")):
        offences += _default_offences(path.read_text("utf-8"), path.name)
    assert not offences, offences
    # the scan has teeth
    bad = [
        "def f(wait_for_agent_silence_ms: int = 800): ...",
        "def f(*, max_silence_ms=5000): ...",
        "def f(caller_speaks_first: bool = False): ...",
        "wait_for_agent_silence_ms = 800",
        "class C:\n    max_silence_ms: int = 5000",
        "DEFAULT_WAIT_FOR_AGENT_SILENCE_MS = 800",
    ]
    for snippet in bad:
        assert _default_offences(snippet, "snippet"), snippet
    good = [
        "def f(wait_for_agent_silence_ms: int, max_silence_ms: int): ...",
        "class C:\n    caller_speaks_first: bool",
        "x = policy.wait_for_agent_silence_ms",
    ]
    for snippet in good:
        assert not _default_offences(snippet, "snippet"), snippet


def test_policy_values_are_validated() -> None:
    """Each policy value is refused with its own code when it is not what the profile schema
    says (type, range, the cross-field rule max >= wait), at the boundaries, and the minimum and
    the maximum are accepted: a loose check would let a bad owner value reach a real call."""
    tt = turntaking_mod()

    def make(**override: Any) -> Any:
        values = {
            "caller_speaks_first": True,
            "wait_for_agent_silence_ms": 1,
            "max_silence_ms": 1,
            **override,
        }
        return tt.TurnTakingPolicy(**values)

    assert make().max_silence_ms == 1  # equal values and the minimum are valid
    big = 9_007_199_254_740_991
    assert make(wait_for_agent_silence_ms=big, max_silence_ms=big).max_silence_ms == big
    for bad in (0, -1, True, 1.0, "800", None, big + 1):
        with pytest.raises(tt.TurnTakingError) as error:
            make(wait_for_agent_silence_ms=bad, max_silence_ms=big)
        assert str(error.value) == "wait_for_agent_silence_ms_invalid", bad
        with pytest.raises(tt.TurnTakingError) as error:
            make(max_silence_ms=bad)
        assert str(error.value) == "max_silence_ms_invalid", bad
    with pytest.raises(tt.TurnTakingError) as error:
        make(wait_for_agent_silence_ms=800, max_silence_ms=799)  # max below wait
    assert str(error.value) == "max_silence_below_wait"
    for bad_flag in (0, 1, "yes", None):
        with pytest.raises(tt.TurnTakingError):
            make(caller_speaks_first=bad_flag)
    with pytest.raises(tt.TurnTakingError):
        tt.TurnTaker(None)


def test_barge_in_fires_at_offset_from_agent_onset() -> None:
    tt = turntaking_mod()
    # the agent talks on for a long time: no silence ever comes, the barge-in still fires
    tk = taker()
    tk.begin_wait(ms(10_000), barge_in_after_ms=300)
    feed(tk, [(ms(10_020), silent()), (ms(10_040), silent())])  # zero frames are no onset
    assert tk.poll(ms(10_100)).kind == "wait"
    feed(tk, every_20ms(10_500, 10_780, active()))  # the onset is the frame at 10_500
    assert shape(tk.poll(ms(10_790))) == ("wait", None, None)
    tk.agent_frame(ms(10_800), active())
    assert shape(tk.poll(ms(10_800))) == ("speak", ms(10_800), "barge_in")
    feed(tk, every_20ms(10_820, 12_000, active()))  # later frames change nothing
    assert shape(tk.poll(ms(12_000))) == ("speak", ms(10_800), "barge_in")
    assert shape(tk.poll(ms(99_000))) == ("speak", ms(10_800), "barge_in")
    # offset 0: the line starts at the onset itself
    tk = taker()
    tk.begin_wait(ms(0), barge_in_after_ms=0)
    tk.agent_frame(ms(500), active())
    assert shape(tk.poll(ms(500))) == ("speak", ms(500), "barge_in")
    # the agent stopped long before the offset: the line still starts at onset + offset, not at
    # the silence instant (a barge-in replaces the wait for silence)
    tk = taker()
    tk.begin_wait(ms(10_000), barge_in_after_ms=3_000)
    feed(tk, every_20ms(10_500, 10_600, active()))
    assert shape(tk.poll(ms(13_499))) == ("wait", None, None)
    assert shape(tk.poll(ms(13_500))) == ("speak", ms(13_500), "barge_in")
    # the same frames without a barge-in speak at the silence instant instead
    plain = taker()
    plain.begin_wait(ms(10_000))
    feed(plain, every_20ms(10_500, 10_600, active()))
    assert shape(plain.poll(ms(11_400))) == ("speak", ms(11_400), "agent_silent")
    # the onset is the FIRST active frame since the wait began: not a silent frame, not a later
    # frame, not a frame from before the wait
    tk = taker()
    tk.agent_frame(ms(9_000), active())  # before the wait: ignored
    tk.begin_wait(ms(10_000), barge_in_after_ms=100)
    feed(tk, [(ms(10_100), silent()), (ms(10_200), active()), (ms(10_250), silent())])
    tk.agent_frame(ms(10_600), active())
    assert shape(tk.poll(ms(10_600))) == ("speak", ms(10_300), "barge_in")
    # a frame exactly at the start of the wait counts
    tk = taker()
    tk.begin_wait(ms(10_000), barge_in_after_ms=50)
    tk.agent_frame(ms(10_000), active())
    assert shape(tk.poll(ms(10_050))) == ("speak", ms(10_050), "barge_in")
    # an onset so late that onset + offset passes the end of the wait: the call ends instead
    tk = taker(maximum=5_000)
    tk.begin_wait(ms(10_000), barge_in_after_ms=4_600)
    tk.agent_frame(ms(10_500), active())
    assert shape(tk.poll(ms(14_999))) == ("wait", None, None)
    assert shape(tk.poll(ms(15_000))) == ("end", ms(15_000), "max_silence")
    # exactly at the end: the speak instant wins the tie
    tk = taker(maximum=5_000)
    tk.begin_wait(ms(10_000), barge_in_after_ms=4_500)
    tk.agent_frame(ms(10_500), active())
    assert shape(tk.poll(ms(15_000))) == ("speak", ms(15_000), "barge_in")
    # a barge-in on the very first line of a caller that speaks first has no onset to follow
    first = taker(first=True)
    with pytest.raises(tt.TurnTakingError) as error:
        first.begin_wait(0, barge_in_after_ms=100)
    assert str(error.value) == "barge_in_on_first_line"
    # but a caller that waits for the greeting may barge in on it
    greeting = taker(first=False)
    greeting.begin_wait(0, barge_in_after_ms=100)
    for bad in (-1, True, 1.5, "100"):
        with pytest.raises(tt.TurnTakingError):
            taker().begin_wait(0, barge_in_after_ms=bad)


@given(
    onset=st.integers(min_value=0, max_value=4_000),
    offset=st.integers(min_value=0, max_value=6_000),
    run=st.integers(min_value=0, max_value=60),
)
@HSETTINGS
def test_barge_in_instant_is_onset_plus_offset_or_the_end(
    onset: int, offset: int, run: int
) -> None:
    """For any onset, offset and agent run length: speak at onset + offset when that is within
    the wait budget (inclusive), else end at the budget; the agent's later frames never move it."""
    maximum = 5_000
    tk = taker(maximum=maximum)
    tk.begin_wait(ms(100), barge_in_after_ms=offset)
    start = 100 + onset
    for k in range(run + 1):
        t = start + 20 * k
        if t >= 100 + maximum:
            break
        tk.agent_frame(ms(t), active())
    decision = tk.poll(ms(100 + 20_000))
    if start < 100 + maximum and start + offset <= 100 + maximum:
        assert shape(decision) == ("speak", ms(start + offset), "barge_in")
    else:
        assert shape(decision) == ("end", ms(100 + maximum), "max_silence")


def test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang() -> None:
    cs = callstate_mod()
    maximum = 5_000
    # the agent streams a non-zero frame every 20 ms for ever (comfort noise, one LSB)
    noise = pcm([1] * 160)
    tk = taker(wait=800, maximum=maximum)
    begin = ms(2_000)
    tk.begin_wait(begin)
    now = begin
    ticks = 0
    decision = tk.poll(now)
    while decision.kind == "wait":
        now += ms(20)
        tk.agent_frame(now, noise)
        decision = tk.poll(now)
        ticks += 1
        assert ticks <= 10 * maximum // 20, "the wait did not end: a hang"
    assert shape(decision) == ("end", begin + ms(maximum), "max_silence")
    assert now == begin + ms(maximum)
    assert ticks == maximum // 20
    # polling late changes nothing
    assert shape(tk.poll(now + ms(60_000))) == ("end", begin + ms(maximum), "max_silence")
    # the cause is the machine's end reason: the call ends, with one reason, from any state
    for state in ("connected", "awaiting_agent", "agent_speaking"):
        machine = drive(cs.CallMachine(), PATHS[state])
        machine.apply("end_requested", decision.cause)
        machine.apply("hangup_complete")
        assert (machine.state, machine.end_reason) == ("ended", "max_silence")
    # a dead agent (no frame at all, or only zero frames) ends at the same instant
    for payloads in ([], [silent()] * 40):
        dead = taker(maximum=maximum)
        dead.begin_wait(begin)
        for k, payload in enumerate(payloads):
            dead.agent_frame(begin + ms(20 * (k + 1)), payload)
        assert shape(dead.poll(begin + ms(maximum - 1))) == ("wait", None, None)
        assert shape(dead.poll(begin + ms(maximum))) == ("end", begin + ms(maximum), "max_silence")
    # the budget restarts with the next wait
    tk = taker(wait=800, maximum=maximum)
    tk.begin_wait(ms(0))
    tk.agent_frame(ms(100), noise)
    assert shape(tk.poll(ms(900))) == ("speak", ms(900), "agent_silent")
    tk.line_started(ms(900))
    tk.begin_wait(ms(7_000))
    assert shape(tk.poll(ms(11_999))) == ("wait", None, None)
    assert shape(tk.poll(ms(12_000))) == ("end", ms(12_000), "max_silence")
    # a caller that speaks first is not held up by an agent that never goes silent
    first = taker(first=True)
    first.begin_wait(ms(0))
    first.agent_frame(ms(10), noise)
    assert shape(first.poll(ms(10))) == ("speak", 0, "caller_first")


def test_simulator_never_speaks_while_brain_pending() -> None:
    tt = turntaking_mod()
    # scripted case: no brain pending, the line starts at the decision
    tk = taker()
    tk.begin_wait(ms(0))
    feed(tk, every_20ms(100, 200, active()))
    assert tk.may_send(ms(999)) is False  # the agent is not yet silent for the wait
    assert tk.may_send(ms(1_000)) is True
    # LLM case: the decision is due at 1_000, the brain starts then and takes 3 s
    tk = taker()
    tk.begin_wait(ms(0))
    feed(tk, every_20ms(100, 200, active()))
    assert shape(tk.poll(ms(1_000))) == ("speak", ms(1_000), "agent_silent")
    tk.brain_started(ms(1_000))
    assert tk.may_send(ms(1_000)) is False
    assert tk.may_send(ms(2_500)) is False
    assert shape(tk.poll(ms(2_500))) == ("speak", ms(1_000), "agent_silent")  # the turn is due...
    with pytest.raises(tt.TurnTakingError) as error:  # ...but the caller may not speak yet
        tk.line_started(ms(2_500))
    assert str(error.value) == "brain_pending"
    tk.brain_finished(ms(4_000))
    assert tk.may_send(ms(4_000)) is True
    tk.line_started(ms(4_000))
    with pytest.raises(tt.TurnTakingError):  # a line is started once per wait
        tk.line_started(ms(4_020))
    # the brain pending from the start of the call: even a caller that speaks first waits for it
    first = taker(first=True)
    first.begin_wait(ms(0))
    first.brain_started(ms(0))
    assert shape(first.poll(ms(0))) == ("speak", 0, "caller_first")
    assert first.may_send(ms(500)) is False
    with pytest.raises(tt.TurnTakingError):
        first.line_started(ms(500))
    first.brain_finished(ms(700))
    first.line_started(ms(700))
    # a barge-in instant that passes while the brain is pending is not a licence to speak either
    barge = taker()
    barge.begin_wait(ms(0), barge_in_after_ms=100)
    barge.brain_started(ms(0))
    barge.agent_frame(ms(50), active())
    assert shape(barge.poll(ms(150))) == ("speak", ms(150), "barge_in")
    assert barge.may_send(ms(150)) is False
    barge.brain_finished(ms(300))
    assert barge.may_send(ms(300)) is True
    # never before a speak decision: a wait, or the end of the call, is not a turn
    early = taker()
    early.begin_wait(ms(0))
    with pytest.raises(tt.TurnTakingError) as error:
        early.line_started(ms(10))
    assert str(error.value) == "no_speak_decision"
    ended = taker(maximum=1_000)
    ended.begin_wait(ms(0))
    with pytest.raises(tt.TurnTakingError):
        ended.line_started(ms(2_000))
    # the brain calls are paired
    pair = taker()
    pair.begin_wait(ms(0))
    with pytest.raises(tt.TurnTakingError):
        pair.brain_finished(ms(1))
    pair.brain_started(ms(1))
    with pytest.raises(tt.TurnTakingError):
        pair.brain_started(ms(2))
    pair.brain_finished(ms(3))
    with pytest.raises(tt.TurnTakingError):
        taker().brain_started(0)  # no wait is open


@given(
    ops=st.lists(
        st.tuples(
            st.sampled_from(["frame", "poll", "start", "finish", "line"]),
            st.integers(min_value=0, max_value=700),
            st.booleans(),
        ),
        max_size=40,
    )
)
@HSETTINGS
def test_a_line_never_starts_while_the_brain_is_pending(
    ops: list[tuple[str, int, bool]],
) -> None:
    """Property: over any interleaving of frames, polls and brain calls, ``line_started``
    succeeds only when the brain is not pending and a speak decision is due; and ``may_send`` is
    never true while the brain is pending."""
    tt = turntaking_mod()
    tk = taker(wait=300, maximum=3_000)
    tk.begin_wait(0)
    now = 0
    pending = False
    for op, step, flag in ops:
        now += ms(step)
        try:
            if op == "frame":
                tk.agent_frame(now, active() if flag else silent())
            elif op == "poll":
                tk.poll(now)
            elif op == "start":
                tk.brain_started(now)
                pending = True
            elif op == "finish":
                tk.brain_finished(now)
                pending = False
            else:
                tk.line_started(now)
                assert not pending, "a line started while the brain was pending"
                break
        except tt.TurnTakingError:
            continue
        if pending:
            assert tk.may_send(now) is False


# ----------------------------------------------------------------------------------------------
# Extra tests (each with its reason)
# ----------------------------------------------------------------------------------------------


def test_silence_rule_speaks_wait_ms_after_the_last_active_frame() -> None:
    """The silence rule of ADR 0026 rule 10 at its boundaries, to the nanosecond: silent once
    no active frame has arrived for ``wait_for_agent_silence_ms`` (inclusive)."""
    tk = taker(wait=800)
    tk.begin_wait(0)
    feed(tk, [(ms(100), active()), (ms(120), active()), (ms(140), active())])
    assert shape(tk.poll(ms(940) - 1)) == ("wait", None, None)
    assert shape(tk.poll(ms(940))) == ("speak", ms(940), "agent_silent")
    # zero frames after the last active one do not move it, and an active frame later does not
    # reopen a decision already due
    tk = taker(wait=800)
    tk.begin_wait(0)
    feed(tk, [(ms(100), active()), (ms(500), silent()), (ms(899), active())])
    assert shape(tk.poll(ms(899))) == ("wait", None, None)  # gap 799 ms: not silent
    assert shape(tk.poll(ms(1_699))) == ("speak", ms(1_699), "agent_silent")
    # a gap of exactly the wait: the decision is due at the old frame + wait, and the frame
    # arriving at that very instant does not move it
    tk = taker(wait=800)
    tk.begin_wait(0)
    tk.agent_frame(ms(100), active())
    tk.agent_frame(ms(900), active())
    assert shape(tk.poll(ms(900))) == ("speak", ms(900), "agent_silent")
    assert shape(tk.poll(ms(50_000))) == ("speak", ms(900), "agent_silent")
    # one nanosecond short of the wait: the later frame keeps the floor
    tk = taker(wait=800)
    tk.begin_wait(0)
    tk.agent_frame(ms(100), active())
    tk.agent_frame(ms(900) - 1, active())
    assert shape(tk.poll(ms(900)))[0] == "wait"
    # the agent never replies: the end instant, and the wait budget equal to the wait value
    tk = taker(wait=1_000, maximum=1_000)
    tk.begin_wait(0)
    assert shape(tk.poll(ms(1_000))) == ("end", ms(1_000), "max_silence")
    tk = taker(wait=1_000, maximum=1_000)
    tk.begin_wait(0)
    tk.agent_frame(0, active())
    assert shape(tk.poll(ms(1_000))) == ("speak", ms(1_000), "agent_silent")  # speak wins the tie
    # a frame at the end instant is not considered: the end is decided first
    tk = taker(wait=100, maximum=1_000)
    tk.begin_wait(0)
    tk.agent_frame(ms(1_000), active())
    assert shape(tk.poll(ms(1_000))) == ("end", ms(1_000), "max_silence")


def test_the_first_line_of_a_caller_that_speaks_first_does_not_wait() -> None:
    """``caller_speaks_first`` (ADR 0026 Ambiguity 2): the first line is due at once, later lines
    wait for the agent as usual; with the flag off the first line waits for the greeting."""
    tk = taker(first=True)
    tk.begin_wait(ms(50))
    assert shape(tk.poll(ms(50))) == ("speak", ms(50), "caller_first")
    assert tk.may_send(ms(50)) is True
    tk.line_started(ms(60))
    tk.begin_wait(ms(500))
    assert shape(tk.poll(ms(500))) == ("wait", None, None)  # the second line waits
    tk.agent_frame(ms(600), active())
    assert shape(tk.poll(ms(1_400))) == ("speak", ms(1_400), "agent_silent")
    waiting = taker(first=False)
    waiting.begin_wait(ms(50))
    assert shape(waiting.poll(ms(50))) == ("wait", None, None)


def test_state_of_one_wait_does_not_leak_into_the_next() -> None:
    """A new wait forgets the onset, the last active frame, the barge-in and the brain flag of
    the previous one."""
    tt = turntaking_mod()
    tk = taker(wait=300, maximum=2_000)
    tk.begin_wait(ms(0), barge_in_after_ms=10)
    tk.brain_started(ms(0))
    tk.agent_frame(ms(5), active())
    tk.brain_finished(ms(20))
    tk.line_started(ms(20))
    with pytest.raises(tt.TurnTakingError):
        tk.poll(ms(30))  # no wait is open between a line and the next wait
    assert tk.agent_frame(ms(40), active()) is True  # frames while the caller speaks: activity only
    tk.begin_wait(ms(100))  # no barge-in now, no pending brain, no old frames
    assert shape(tk.poll(ms(100))) == ("wait", None, None)
    assert tk.may_send(ms(100)) is False
    tk.agent_frame(ms(150), active())
    assert shape(tk.poll(ms(450))) == ("speak", ms(450), "agent_silent")
    tk.brain_started(ms(450))  # allowed again: the flag was reset
    with pytest.raises(tt.TurnTakingError):
        tk.begin_wait(ms(500))  # a wait is still open


def test_the_onset_of_one_wait_is_not_the_onset_of_the_next() -> None:
    """Each wait finds its own onset: a barge-in of the second wait follows the second onset,
    not the first wait's (a stale onset would make the line due at once)."""
    tk = taker(wait=300, maximum=5_000)
    tk.begin_wait(ms(0), barge_in_after_ms=10)
    tk.agent_frame(ms(5), active())
    assert shape(tk.poll(ms(15))) == ("speak", ms(15), "barge_in")
    tk.line_started(ms(15))
    tk.begin_wait(ms(100), barge_in_after_ms=10)
    assert shape(tk.poll(ms(100))) == ("wait", None, None)
    assert shape(tk.poll(ms(150))) == ("wait", None, None)
    tk.agent_frame(ms(200), active())
    assert shape(tk.poll(ms(209))) == ("wait", None, None)
    assert shape(tk.poll(ms(210))) == ("speak", ms(210), "barge_in")


def test_time_never_goes_backwards_and_inputs_are_checked() -> None:
    """The taker reads no clock: its instants come from the runner, so an instant that goes
    backwards or is not an integer is a runner defect and is refused, not smoothed over."""
    tt = turntaking_mod()
    tk = taker()
    tk.begin_wait(ms(100))
    tk.agent_frame(ms(200), active())
    with pytest.raises(tt.TurnTakingError) as error:
        tk.poll(ms(199))
    assert str(error.value) == "time_invalid"
    with pytest.raises(tt.TurnTakingError):
        tk.agent_frame(ms(100), active())
    for bad in (-1, True, 1.5, "1", None):
        with pytest.raises(tt.TurnTakingError):
            taker().begin_wait(bad)
    with pytest.raises(tt.TurnTakingError):
        tk.begin_wait(ms(300))  # a wait is open
    with pytest.raises(tt.TurnTakingError):
        taker().poll(0)  # no wait is open
    # equal instants are allowed (a frame and a poll at the same time)
    tk.poll(ms(200))
    tk.agent_frame(ms(200), active())


def test_a_refused_call_changes_nothing() -> None:
    """The runner drives the taker in a loop: a refused call (an event that should not happen)
    must not move its clock, latch a decision or flip a flag, or the refusal itself would change
    the call (the contract: a call that raises changes nothing)."""
    tt = turntaking_mod()
    tk = taker()
    tk.begin_wait(ms(100))
    tk.agent_frame(ms(150), active())
    refused: list[Callable[[], object]] = [
        lambda: tk.begin_wait(ms(900)),  # a wait is open
        lambda: tk.brain_finished(ms(900)),  # nothing is pending
        lambda: tk.line_started(ms(900)),  # the agent has not been silent for 800 ms yet
        lambda: tk.agent_frame(ms(900), b"\x00"),  # not a whole sample
        lambda: tk.agent_frame(ms(900), b""),
    ]
    for call in refused:
        with pytest.raises(tt.TurnTakingError):
            call()
    assert shape(tk.poll(ms(200))) == ("wait", None, None)  # time 900 was never taken
    tk.brain_started(ms(300))
    with pytest.raises(tt.TurnTakingError) as error:
        tk.line_started(ms(5_000))  # the turn is due at 950 but the brain is pending
    assert str(error.value) == "brain_pending"
    assert shape(tk.poll(ms(400))) == ("wait", None, None)  # time 5,000 was never taken either
    with pytest.raises(tt.TurnTakingError):
        tk.brain_started(ms(450))  # already pending
    tk.brain_finished(ms(500))
    assert tk.may_send(ms(949)) is False
    assert tk.may_send(ms(950)) is True
    assert shape(tk.poll(ms(950))) == ("speak", ms(950), "agent_silent")


@given(
    first=st.booleans(),
    wait=st.integers(min_value=1, max_value=900),
    extra=st.integers(min_value=0, max_value=1_500),
    barge=st.one_of(st.none(), st.integers(min_value=0, max_value=2_500)),
    frames=frame_sequences(max_frames=14, max_gap_ms=700, start_ms=50),
    poll_gaps=st.lists(st.integers(min_value=0, max_value=900), max_size=12),
)
@HSETTINGS
def test_decision_matches_the_reference_on_random_recorded_frame_sequences(
    first: bool,
    wait: int,
    extra: int,
    barge: int | None,
    frames: list[tuple[int, bool]],
    poll_gaps: list[int],
) -> None:
    """Deterministic on recorded frame sequences: whatever the polls in between, the final
    decision equals the reference over the same frames, and equals what a second taker, fed the
    same frames without any intermediate poll, decides."""
    maximum = wait + extra
    if first and barge is not None:
        barge = None  # refused by the contract (tested elsewhere)
    begin = ms(10)
    expected = ref_turn_decision(
        first=True,
        speaks_first=first,
        wait_ms=wait,
        max_silence_ms=maximum,
        begin_ns=begin,
        barge_ms=barge,
        frames=frames,
    )
    # interleave polls at chosen instants with the frames, in time order
    poll_times = []
    t = 10
    for gap in poll_gaps:
        t += gap
        poll_times.append(ms(t))
    events = sorted(
        [(ft, 0, a) for ft, a in frames] + [(pt, 1, False) for pt in poll_times],
        key=lambda e: (e[0], e[1]),
    )
    tk = taker(first=first, wait=wait, maximum=maximum)
    tk.begin_wait(begin, barge_in_after_ms=barge)
    for when, kind, is_active in events:
        if kind == 0:
            tk.agent_frame(when, active(amplitude=1) if is_active else silent())
        else:
            tk.poll(when)
    final = tk.poll(max([begin + ms(maximum), *(e[0] for e in events)]) + ms(1))
    assert shape(final) == expected
    quiet = taker(first=first, wait=wait, maximum=maximum)
    quiet.begin_wait(begin, barge_in_after_ms=barge)
    for ft, a in frames:
        quiet.agent_frame(ft, active(amplitude=1) if a else silent())
    assert shape(quiet.poll(ms(10 + 10_000))) == expected


def test_decisions_are_plain_values() -> None:
    """The decision record: fixed kinds and causes, frozen, hashable, equal by value."""
    tt = turntaking_mod()
    assert tuple(tt.TURN_DECISION_KINDS) == ("wait", "speak", "end")
    assert tuple(tt.SPEAK_CAUSES) == ("caller_first", "agent_silent", "barge_in")
    a = tt.TurnDecision("speak", 5, "agent_silent")
    assert a == tt.TurnDecision("speak", 5, "agent_silent")
    assert hash(a) == hash(a)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.kind = "end"
    assert [f.name for f in dataclasses.fields(tt.TurnDecision)] == ["kind", "at_ns", "cause"]
    tk = taker()
    tk.begin_wait(0)
    waiting = tk.poll(1)
    assert (waiting.kind, waiting.at_ns, waiting.cause) == ("wait", None, None)


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------


def _turn_taking_schema() -> dict[str, Any]:
    section = sup.heading_section(
        sup.ADR27.read_text("utf-8"), "## The `tracer.sim_profile/1` format"
    )
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1
    schema: dict[str, Any] = json.loads(blocks[0])["properties"]["turn_taking"]
    return schema


def _default_offences(source: str, name: str) -> list[str]:
    """Where ``source`` gives a policy value a default: a parameter default, an assignment of a
    value to the name (module, class or function level), or a ``DEFAULT_...`` constant."""
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            positional = args.posonlyargs + args.args
            with_default = positional[len(positional) - len(args.defaults) :]
            for arg in with_default:
                if arg.arg in POLICY_KEYS:
                    found.append(f"{name}:{node.lineno}: parameter {arg.arg} has a default")
            for kw, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
                if kw.arg in POLICY_KEYS and kw_default is not None:
                    found.append(f"{name}:{node.lineno}: parameter {kw.arg} has a default")
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for target in targets:
            ident = target.id if isinstance(target, ast.Name) else None
            if ident is None:
                continue
            if ident in POLICY_KEYS and value is not None:
                found.append(f"{name}:{line}: {ident} is assigned a value")
            if re.fullmatch(r"_?DEFAULT_\w*(SILENCE|WAIT|SPEAKS_FIRST)\w*", ident):
                found.append(f"{name}:{line}: {ident} looks like a default")
    return found
