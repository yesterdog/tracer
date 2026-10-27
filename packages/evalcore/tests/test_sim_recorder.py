# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-06: the stereo recorder (ADR 0027 rules 3 and 4 and invariant I2; ADR 0026 rule 2; contract
docs/phase4/api-surface-batch-a.md section 4). The G.711 test of this task is in
``test_sim_g711.py``.

Frames are placed on one timeline of integer nanoseconds. The expected WAV is built by a reference
in this file (plain lists of samples), not by the recorder's own code. The module is loaded lazily,
so before it exists every test fails on its own with ``ModuleNotFoundError``.
"""

from __future__ import annotations

import ast
import hashlib
import struct
from collections.abc import Sequence
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    EVALCORE_SRC,
    T0,
    active,
    fmt_pcm,
    g711_mod,
    log_frame,
    ms,
    pcm,
    read_wav,
    recorder_mod,
    sample_at,
    silent,
    unpcm,
)

# Deterministic hypothesis profile (as Phase 1 to 3): derandomised, no deadline.
HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def build(
    caller: Sequence[Any],
    agent: Sequence[Any],
    *,
    rate: int = 8000,
    codec: str = "pcm_s16le",
    origin: int = T0,
) -> Any:
    rec = recorder_mod()
    return rec.build_recording(rec.RecorderFormat(rate, codec), origin, caller, agent)


def ref_channels(
    rate: int,
    origin: int,
    caller: list[tuple[int, list[int]]],
    agent: list[tuple[int, list[int]]],
) -> tuple[list[int], list[int], list[int], list[int], int]:
    """(left, right, caller starts, agent starts, caller overlap) of two logs of (t_ns, samples).

    Caller: exact send instant (the later frame overwrites an overlap and the overlap is counted).
    Agent: at the later of the arrival sample and the end of the previous agent frame.
    """
    left: dict[int, int] = {}
    covered: set[int] = set()
    overlap = 0
    starts_c = []
    for t, samples in caller:
        start = sample_at(t, origin, rate)
        starts_c.append(start)
        for i, value in enumerate(samples):
            if start + i in covered:
                overlap += 1
            covered.add(start + i)
            left[start + i] = value
    right: dict[int, int] = {}
    starts_a = []
    free = 0
    for t, samples in agent:
        start = max(sample_at(t, origin, rate), free)
        starts_a.append(start)
        for i, value in enumerate(samples):
            right[start + i] = value
        free = start + len(samples)
    n = max([*(k + 1 for k in left), *(k + 1 for k in right), 0])
    return (
        [left.get(i, 0) for i in range(n)],
        [right.get(i, 0) for i in range(n)],
        starts_c,
        starts_a,
        overlap,
    )


# ----------------------------------------------------------------------------------------------
# The SCOPE tests of P4-06
# ----------------------------------------------------------------------------------------------


def test_recorder_is_deterministic_from_frame_log() -> None:
    def frames() -> tuple[list[Any], list[Any]]:
        caller = [log_frame(T0 + ms(20 * k), pcm([k + 1] * 160)) for k in range(6)]
        agent = [
            log_frame(T0 + ms(35), pcm([-7] * 160)),
            log_frame(T0 + ms(35), pcm([9] * 160)),
            log_frame(T0 + ms(300) + 123, pcm([3, -3] * 80)),
        ]
        return caller, agent

    first = build(*frames())
    second = build(*frames())  # fresh objects, same log
    assert first.wav == second.wav
    assert first == second
    assert hashlib.sha256(first.wav).hexdigest() == hashlib.sha256(second.wav).hexdigest()
    # nothing between calls changes the answer (no hidden state, no clock, no randomness)
    other = build([log_frame(T0 + ms(5), pcm([1] * 160))], [])
    assert other.wav != first.wav
    assert build(*frames()).wav == first.wav
    # a tuple log gives the same bytes as a list
    caller, agent = frames()
    assert build(tuple(caller), tuple(agent)).wav == first.wav
    # the golden digest of this log: any change of layout, placement or rounding changes it
    assert (
        hashlib.sha256(first.wav).hexdigest()
        == "c49fbdb9e7edbac75bd29fbe35115cd3ec303aebd4b77d618e30ab0303bfdc3b"
    )
    # and a tiny log whose whole file is written out by hand: caller [1, -2], agent [3]
    tiny = build([log_frame(T0, pcm([1, -2]))], [log_frame(T0, pcm([3]))])
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + 8, b"WAVE", b"fmt ", 16, 1, 2, 8000, 32000, 4, 16, b"data", 8,
    )  # fmt: skip
    assert tiny.wav == header + struct.pack("<4h", 1, 3, -2, 0)
    assert (tiny.n_samples, tiny.sample_rate_hz) == (2, 8000)


@given(
    caller=st.lists(
        st.tuples(st.integers(min_value=0, max_value=400_000_000), st.integers(1, 200)),
        max_size=6,
    ),
    agent=st.lists(
        st.tuples(st.integers(min_value=0, max_value=400_000_000), st.integers(1, 200)),
        max_size=6,
    ),
    rate=st.sampled_from([8000, 16000, 24000, 44100, 48000]),
    value=st.integers(min_value=-32768, max_value=32767),
    shift=st.integers(min_value=0, max_value=10**12),
)
@HSETTINGS
def test_recorder_matches_the_reference_for_random_frame_logs(
    caller: list[tuple[int, int]],
    agent: list[tuple[int, int]],
    rate: int,
    value: int,
    shift: int,
) -> None:
    """The whole recording (both channels, every start sample, the overlap count) equals a
    reference built from lists, for sorted random logs at several rates; and is deterministic."""
    origin = T0 + shift  # a monotonic clock has an arbitrary zero
    caller_log = sorted(caller)
    agent_log = sorted(agent)
    caller_frames = [
        (origin + t, [(value + k) % 30000 + 1] * n) for k, (t, n) in enumerate(caller_log)
    ]
    agent_frames = [(origin + t, [-(k + 1)] * n) for k, (t, n) in enumerate(agent_log)]
    expected = ref_channels(rate, origin, caller_frames, agent_frames)
    got = build(
        [log_frame(t, pcm(s), rate=rate) for t, s in caller_frames],
        [log_frame(t, pcm(s), rate=rate) for t, s in agent_frames],
        rate=rate,
        origin=origin,
    )
    wav = read_wav(got.wav)
    assert (wav["rate"], wav["channels"], wav["bits"]) == (rate, 2, 16)
    assert wav["left"] == expected[0]
    assert wav["right"] == expected[1]
    assert wav["n"] == got.n_samples == len(expected[0])
    assert list(got.caller_start_samples) == expected[2]
    assert list(got.agent_start_samples) == expected[3]
    assert got.caller_overlap_samples == expected[4]


def test_recorder_channel_0_is_exactly_what_was_sent() -> None:
    rate = 8000
    sent = [
        (T0 + ms(0), [11] * 160),
        (T0 + ms(20), [-12] * 160),
        (T0 + ms(40) + 62_500, [13] * 160),  # 0.5 sample late: floors to sample 320
        (T0 + ms(100), [14] * 80),  # a gap of silence before it
    ]
    # an agent that talks all the time, over the caller: channel 0 does not change
    agent = [log_frame(T0 + ms(10 * k), pcm([777] * 160)) for k in range(12)]
    caller_only = build([log_frame(t, pcm(s)) for t, s in sent], [])
    with_agent = build([log_frame(t, pcm(s)) for t, s in sent], agent)
    left_only = read_wav(caller_only.wav)["left"]
    left_with = read_wav(with_agent.wav)["left"]
    n_only = len(left_only)
    assert left_with[:n_only] == left_only
    assert not any(left_with[n_only:])
    # the expected channel 0: the sent samples at floor(elapsed * rate), zeros elsewhere
    expected = [0] * (sample_at(T0 + ms(100), T0, rate) + 80)
    for t, samples in sent:
        start = sample_at(t, T0, rate)
        expected[start : start + len(samples)] = samples
    assert left_only == expected
    assert caller_only.caller_start_samples == (0, 160, 320, 800)
    assert caller_only.caller_overlap_samples == 0
    assert not any(read_wav(caller_only.wav)["right"]), "no agent frame: channel 1 is silent"
    # the sent PCM comes back byte for byte
    data = read_wav(caller_only.wav)["data"]
    assert data[0:4] == struct.pack("<hh", 11, 0)
    assert data[4 * 160 : 4 * 160 + 4] == struct.pack("<hh", -12, 0)
    # instants are floored to a sample exactly (8000 Hz: a sample is 125,000 ns)
    for t_ns, sample in ((0, 0), (124_999, 0), (125_000, 1), (125_001, 1), (249_999, 1)):
        got = build([log_frame(T0 + t_ns, pcm([5]))], [])
        assert got.caller_start_samples == (sample,), t_ns
    # and at 44.1 kHz (22,676 ns is the first instant of sample 1)
    for t_ns, sample in ((22_675, 0), (22_676, 1), (1_000_000_000, 44_100)):
        got = build([log_frame(T0 + t_ns, pcm([5]), rate=44100)], [], rate=44100)
        assert got.caller_start_samples == (sample,), t_ns
    # the origin is the connection instant: moving every instant and the origin together changes
    # nothing
    shift = 987_654_321_000
    moved = build(
        [log_frame(t + shift, pcm(s)) for t, s in sent],
        [log_frame(f.t_ns + shift, f.payload) for f in agent],
        origin=T0 + shift,
    )
    assert moved.wav == with_agent.wav
    # on a mu-law wire, channel 0 is the DECODED wire bytes (what was sent, as a listener hears it)
    wire = bytes([0xFF, 0xFE, 0x7E, 0x00, 0x80] * 32)
    ulaw = build([log_frame(T0, wire, codec="mulaw")], [], codec="mulaw")
    decoded = unpcm(g711_mod().decode_mulaw(wire))
    assert read_wav(ulaw.wav)["left"] == decoded
    assert decoded[:5] == [0, 8, -8, -32124, 32124]


def test_recorder_burst_arrival_uses_playback_model() -> None:
    # five 20 ms frames arrive in one burst at 100 ms; a sixth arrives while the burst still plays
    # (150 ms, the burst ends at 200 ms); a seventh arrives long after (400 ms)
    burst = [log_frame(T0 + ms(100), pcm([k] * 160)) for k in range(1, 6)]
    inside = log_frame(T0 + ms(150), pcm([6] * 160))
    late = log_frame(T0 + ms(400), pcm([7] * 160))
    got = build([], [*burst, inside, late])
    wav = read_wav(got.wav)
    assert got.agent_start_samples == (800, 960, 1120, 1280, 1440, 1600, 3200)
    right = wav["right"]
    for k in range(1, 8):
        start = got.agent_start_samples[k - 1]
        assert right[start : start + 160] == [k] * 160, k
    # every sample of every frame is there (no frame was overwritten) and the rest is silence
    assert sum(1 for v in right if v != 0) == 7 * 160
    assert not any(right[:800])
    assert not any(right[1760:3200])
    assert wav["n"] == got.n_samples == 3360
    assert not any(wav["left"])
    # a frame arriving exactly when the previous one ends is placed at its arrival
    exact = build([], [log_frame(T0, pcm([1] * 160)), log_frame(T0 + ms(20), pcm([2] * 160))])
    assert exact.agent_start_samples == (0, 160)
    # one nanosecond before the end: pushed to the end of the previous frame
    early = build([], [log_frame(T0, pcm([1] * 160)), log_frame(T0 + ms(20) - 1, pcm([2] * 160))])
    assert early.agent_start_samples == (0, 160)
    # arrival order is play order even for equal instants
    tie = build([], [log_frame(T0, pcm([1] * 4)), log_frame(T0, pcm([2] * 4))])
    assert read_wav(tie.wav)["right"] == [1, 1, 1, 1, 2, 2, 2, 2]
    # the caller's channel is placed by send time, never by the playback model: a caller burst of
    # equal instants overlaps and is counted, it is not queued
    twice = build([log_frame(T0, pcm([1] * 4)), log_frame(T0, pcm([2] * 4))], [])
    assert twice.caller_start_samples == (0, 0)
    assert read_wav(twice.wav)["left"] == [2, 2, 2, 2]
    assert twice.caller_overlap_samples == 4


@given(
    frames=st.lists(
        st.tuples(st.integers(min_value=0, max_value=300_000_000), st.integers(1, 120)),
        min_size=1,
        max_size=10,
    )
)
@HSETTINGS
def test_agent_frames_play_back_to_back_and_never_overlap(frames: list[tuple[int, int]]) -> None:
    """Property: each agent start is the later of the arrival sample and the previous end, starts
    never decrease, no two frames share a sample, and the frames' samples all survive."""
    log = sorted(frames)
    got = build([], [log_frame(T0 + t, pcm([k + 1] * n)) for k, (t, n) in enumerate(log)])
    free = 0
    for (t, n), start in zip(log, got.agent_start_samples, strict=True):
        assert start == max(sample_at(T0 + t, T0, 8000), free)
        free = start + n
    right = read_wav(got.wav)["right"]
    assert sum(1 for v in right if v != 0) == sum(n for _, n in log)
    assert list(got.agent_start_samples) == sorted(got.agent_start_samples)


def test_recorder_refuses_rate_mismatch_never_resamples() -> None:
    rec = recorder_mod()
    from tracer_evalcore._engine_errors import FactError

    ok = log_frame(T0, pcm([1] * 160))
    # a frame at another rate in either log is refused, with the facts and nothing else
    for rate in (16000, 7999, 8001, 48000, 44100):
        wrong = log_frame(T0 + ms(40), pcm([2] * 160), rate=rate)
        with pytest.raises(rec.RecorderFrameError) as error:
            build([ok, log_frame(T0 + ms(20), ok.payload), wrong], [])
        assert (error.value.index, error.value.kind, error.value.reason) == (
            2,
            "sample_rate",
            "caller",
        )
        with pytest.raises(rec.RecorderFrameError) as error:
            build([], [ok, wrong])
        assert (error.value.index, error.value.kind, error.value.reason) == (
            1,
            "sample_rate",
            "agent",
        )
    assert str(error.value) == "RecorderFrameError: request 1, transport sample_rate, reason agent"
    assert isinstance(error.value, FactError)
    assert isinstance(error.value, ValueError)
    # no payload, no sample value, no instant in the message
    canary = log_frame(T0, bytes([0xA5, 0x5A]) * 80, rate=16000)
    with pytest.raises(rec.RecorderFrameError) as error:
        build([canary], [])
    text = str(error.value) + repr(error.value) + repr(error.value.__dict__)
    assert "165" not in text
    assert "90" not in text
    assert "xa5" not in text.lower()
    assert "x5a" not in text.lower()
    # a second channel, or another codec on the wire, is refused as well
    stereo = log_frame(T0, pcm([1] * 320), channels=2)
    with pytest.raises(rec.RecorderFrameError) as error:
        build([stereo], [])
    assert error.value.kind == "channels"
    with pytest.raises(rec.RecorderFrameError) as error:
        build([], [log_frame(T0, b"\xff" * 160, codec="mulaw")])
    assert error.value.kind == "codec"
    with pytest.raises(rec.RecorderFrameError) as error:
        build([], [log_frame(T0, b"\xff" * 160, codec="alaw")], codec="mulaw")
    assert error.value.kind == "codec"
    # the format itself is the dialect's: 8,000 to 48,000 Hz and a known wire codec
    for bad_rate in (7999, 48001, 0, True, 8000.0):
        with pytest.raises(ValueError, match="sample_rate_hz"):
            rec.RecorderFormat(bad_rate, "pcm_s16le")
    with pytest.raises(ValueError, match="codec"):
        rec.RecorderFormat(8000, "opus")
    # nothing in the module can resample: no such function or class exists
    tree = ast.parse((EVALCORE_SRC / "recorder.py").read_text("utf-8"))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef)
    }
    assert not [n for n in names if "resampl" in n.lower() or "interpol" in n.lower()]
    assert not [n for n in dir(rec) if "resampl" in n.lower()]


@given(rate=st.integers(min_value=8000, max_value=48000), declared=st.integers(8000, 48000))
@HSETTINGS
def test_a_frame_is_accepted_iff_its_rate_is_the_recorders(rate: int, declared: int) -> None:
    """Property: equal rates record, any other pair is refused; there is no tolerance band."""
    rec = recorder_mod()
    frame = log_frame(T0, pcm([1] * 10), rate=rate)
    if rate == declared:
        assert build([frame], [], rate=declared).sample_rate_hz == declared
    else:
        with pytest.raises(rec.RecorderFrameError):
            build([frame], [], rate=declared)


# ----------------------------------------------------------------------------------------------
# Extra tests (each with its reason)
# ----------------------------------------------------------------------------------------------


def test_wav_layout_is_the_canonical_pcm_header() -> None:
    """The artifact is a canonical 16-bit stereo PCM WAV (ADR 0027 rule 3: container wav, codec
    pcm_s16le, 2 channels, 16 bits, the dialect rate): the bytes any WAV reader expects."""
    for rate in (8000, 16000, 24000, 44100, 48000):
        got = build(
            [log_frame(T0, pcm([1, 2, 3]), rate=rate)],
            [log_frame(T0, pcm([4]), rate=rate)],
            rate=rate,
        )
        wav = read_wav(got.wav)
        assert wav["fmt_size"] == 16
        assert wav["tag"] == 1
        assert (wav["channels"], wav["bits"], wav["align"]) == (2, 16, 4)
        assert wav["rate"] == rate
        assert wav["byte_rate"] == rate * 4
        assert wav["left"] == [1, 2, 3]
        assert wav["right"] == [4, 0, 0]
        assert len(got.wav) == 44 + 4 * 3


def test_empty_logs_give_a_valid_empty_recording() -> None:
    """An established call may end with nothing sent and nothing heard: the recording is a valid
    WAV with zero samples (not an error after a paid call, not an invented sample)."""
    got = build([], [])
    assert got.n_samples == 0
    assert got.caller_start_samples == ()
    assert got.agent_start_samples == ()
    wav = read_wav(got.wav)
    assert wav["n"] == 0
    assert len(got.wav) == 44


def test_caller_frames_that_overlap_in_time_are_counted_not_hidden() -> None:
    """A late caller frame can overlap the next one on the timeline (lateness is visible, never
    hidden by a burst, ADR 0026 rule 8). The later frame wins the shared samples and the overlap
    is reported, so a reader can tell the channel is not exact there."""
    a = log_frame(T0, pcm([1] * 160))
    b = log_frame(T0 + ms(12) + 500_000, pcm([2] * 160))  # starts at sample 100
    got = build([a, b], [])
    left = read_wav(got.wav)["left"]
    assert left[:100] == [1] * 100
    assert left[100:260] == [2] * 160
    assert got.caller_overlap_samples == 60
    assert got.caller_start_samples == (0, 100)
    assert build([a, log_frame(T0 + ms(20), pcm([2] * 160))], []).caller_overlap_samples == 0


def test_frames_must_be_in_order_inside_the_timeline_and_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusals that keep a corrupt log from producing a plausible recording: instants go
    forward, no frame precedes the origin, a PCM payload is whole samples, none is empty, and the
    timeline is bounded (the Evaluator's four hours) before any memory is allocated."""
    rec = recorder_mod()
    a = log_frame(T0 + ms(40), pcm([1] * 10))
    b = log_frame(T0 + ms(20), pcm([1] * 10))
    with pytest.raises(rec.RecorderFrameError) as error:
        build([a, b], [])
    assert (error.value.index, error.value.kind) == (1, "order")
    with pytest.raises(rec.RecorderFrameError) as error:
        build([log_frame(T0 - 1, pcm([1] * 10))], [])
    assert (error.value.index, error.value.kind) == (0, "before_origin")
    for payload in (b"", b"\x00\x00\x00"):
        with pytest.raises(rec.RecorderFrameError) as error:
            build([], [log_frame(T0, payload)])
        assert error.value.kind == "payload_size"
    assert rec.MAX_RECORDING_SECONDS == 14_400
    with pytest.raises(rec.RecorderFrameError) as error:
        build([log_frame(T0 + 14_401 * 1_000_000_000, pcm([1] * 10))], [])
    assert error.value.kind == "too_long"
    with pytest.raises(rec.RecorderFrameError) as error:
        build([], [log_frame(T0 + 14_401 * 1_000_000_000, pcm([1] * 10))])
    assert (error.value.kind, error.value.reason) == ("too_long", "agent")
    # the limit is the module constant, read at call time (so the boundary can be tested small):
    # a frame ending exactly at the limit is accepted, one sample more is refused
    monkeypatch.setattr(rec, "MAX_RECORDING_SECONDS", 1)
    assert build([], [log_frame(T0, pcm([1] * 8000))]).n_samples == 8000
    with pytest.raises(rec.RecorderFrameError) as error:
        build([], [log_frame(T0, pcm([1] * 8001))])
    assert error.value.kind == "too_long"
    with pytest.raises(rec.RecorderFrameError):
        build([log_frame(T0 + 1_000_000_000, pcm([1]))], [])
    monkeypatch.undo()
    # equal instants are fine (a burst)
    build([], [log_frame(T0, pcm([1] * 4)), log_frame(T0, pcm([1] * 4))])


def test_frame_and_format_records_validate_their_fields() -> None:
    """The records the recorder is built from refuse a field that is not what the contract says
    (each ``ValueError`` names the field), so a wrong value cannot travel into a WAV."""
    rec = recorder_mod()
    assert tuple(rec.RECORDER_CODECS) == ("pcm_s16le", "mulaw", "alaw")
    for kwargs in (
        {"t_ns": -1},
        {"t_ns": 1.5},
        {"t_ns": True},
        {"payload": bytearray(b"ab")},
        {"payload": "ab"},
        {"sample_rate_hz": 0},
        {"channels": 0},
        {"codec": "opus"},
    ):
        values: dict[str, Any] = {
            "t_ns": 0,
            "payload": b"ab",
            "sample_rate_hz": 8000,
            "channels": 1,
            "codec": "pcm_s16le",
            **kwargs,
        }
        with pytest.raises(ValueError, match=next(iter(kwargs))):
            rec.LogFrame(**values)
    for bad in (-1, 1.5, True, None):
        with pytest.raises(ValueError, match="origin_ns"):
            rec.build_recording(fmt_pcm(), bad, [], [])
    # decoded payloads: PCM as is, G.711 through the pure tables, nothing else
    assert rec.decode_to_pcm16("pcm_s16le", pcm([1, -1])) == pcm([1, -1])
    assert rec.decode_to_pcm16("mulaw", b"\xfe") == pcm([8])
    assert rec.decode_to_pcm16("alaw", b"\xd5") == pcm([8])
    with pytest.raises(ValueError, match="codec"):
        rec.decode_to_pcm16("opus", b"ab")
    with pytest.raises(ValueError, match="payload"):
        rec.decode_to_pcm16("pcm_s16le", b"abc")


def test_silent_and_active_helpers_are_what_they_say() -> None:
    """Guards the helpers the other tests build frames with (a silent frame that carried a sample
    would make two wrong things agree)."""
    assert unpcm(silent(3)) == [0, 0, 0]
    assert unpcm(active(3, 5)) == [5, 5, 5]
