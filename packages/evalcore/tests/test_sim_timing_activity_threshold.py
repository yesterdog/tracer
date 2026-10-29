# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: PT018, SIM102
"""BL-313: the activity threshold in the timing document and in the agent-segment plan (ADR 0027
rule 10, ADR 0026 rule 10; contract docs/phase4/api-surface-batch-activity.md sections 3 and 4).

``extract_timing`` takes the optional keyword ``activity_min_peak`` and counts a received agent
frame as active iff the maximum absolute value of its DECODED 16-bit samples reaches it. Every
value of the document that depends on activity is then measured by the threshold and not by the
noise: ``first_agent_audio_ms``, ``gap_ms``, ``overlap_ms``, ``barge_in_stop_ms`` and the counts.
The document echoes the value only when the caller gave it. The expected values are written by
hand for the synthetic calls and by an independent reference (plain loops) for the random ones.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import json
import sys
from array import array
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    T0,
    log_frame,
    ms,
    pcm,
    recorder_mod,
    ref_mulaw,
    simtiming_mod,
    turntaking_mod,
)

HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

DIALECTS = ("pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1")
SELF_KEYS = ("reply_ms", "wait_ms", "stt_ms", "brain_ms", "tts_ms")
#: not accepted as a threshold (``None`` is "not given", never a value)
BAD = (0, -1, 32768, 2**31, 1.0, 100.5, True, False, "100", [1])
#: the keys of a document of the earlier batches
OLD_KEYS = {
    "format", "dialect", "clock", "quantum_ms", "caller_frame_ms", "setup_ms",
    "first_agent_audio_ms", "overlap_ms", "answered_caller_turns", "gap_median_ms", "gap_n",
    "barge_in_stop_median_ms", "barge_in_n", "turns",
}  # fmt: skip


def noise(peak: int) -> bytes:
    """A 20 ms frame at 8 kHz whose largest absolute sample is exactly ``peak`` (at least 2)."""
    assert peak >= 2
    data: bytes = pcm([-peak, peak // 2, 1, 0] * 40)
    return data


def zeros() -> bytes:
    data: bytes = pcm([0] * 160)
    return data


def self_times() -> Any:
    return simtiming_mod().SelfTimes(**dict.fromkeys(SELF_KEYS))


def turn(seq: int, first_ms: int, frames: int = 10, *, barge: int | None = None) -> Any:
    first = T0 + ms(first_ms)
    return simtiming_mod().CallerTurn(
        turn_seq=seq,
        first_frame_ns=first,
        last_frame_ns=first + ms(20 * (frames - 1)),
        barge_in_after_ms=barge,
        self_times=self_times(),
    )


def extract(
    turns: list[Any],
    agent: list[Any],
    *,
    peak: Any = "absent",
    dialect: str = "pipecat-protobuf/1",
    codec: str = "pcm_s16le",
    rate: int = 8000,
    wait_ms: int = 300,
    until: int | None = None,
    established: bool = True,
) -> dict[str, Any]:
    last = max([T0, *(t.last_frame_ns + ms(20) for t in turns), *(f.t_ns for f in agent)])
    extra: dict[str, Any] = {} if peak == "absent" else {"activity_min_peak": peak}
    document: dict[str, Any] = simtiming_mod().extract_timing(
        dialect=dialect,
        fmt=recorder_mod().RecorderFormat(rate, codec),
        wait_for_agent_silence_ms=wait_ms,
        connect_started_ns=T0 - ms(300),
        connect_ended_ns=T0,
        established=established,
        observed_until_ns=last + ms(10_000) if until is None else until,
        caller_turns=turns if established else [],
        agent_frames=agent if established else [],
        **extra,
    )
    return document


def the_field_call() -> tuple[list[Any], list[Any]]:
    """The call of the real agent in miniature: noise (peak 250) in every pause, speech (peak 4000)
    at 400 to 580, 1500 to 1680 and 1900 to 2100 ms; two caller turns, the second one a barge-in;
    one frame every 20 ms from 0 to 3000 ms."""
    speech = {*range(400, 600, 20), *range(1500, 1700, 20), *range(1900, 2120, 20)}
    agent = [
        log_frame(T0 + ms(t), noise(4000) if t in speech else noise(250))
        for t in range(0, 3020, 20)
    ]
    return [turn(0, 1000), turn(1, 2000, barge=100)], agent


# ---------------------------------------------------------------------- measured by the threshold


def test_the_values_are_measured_by_the_threshold_and_not_by_the_noise() -> None:
    turns, agent = the_field_call()
    document = extract(turns, agent, peak=1000)
    assert document["first_agent_audio_ms"] == 400
    first, second = document["turns"]
    assert (first["gap_ms"], first["overlap_ms"]) == (300, 0)  # edge 1200, speech at 1500
    assert first["barge_in_stop_ms"] is None and first["barge_in_after_ms"] is None
    # turn 1 sent 2000 to 2180: no active frame at or after its edge 2200; speech 2000 to 2100
    # (6 frames of 20 ms) overlaps it; the activity it interrupted ended with the frame at 2100
    assert second["gap_ms"] is None
    assert second["overlap_ms"] == 120
    assert (second["barge_in_after_ms"], second["barge_in_stop_ms"]) == (100, 120)
    assert document["overlap_ms"] == 120
    assert (document["gap_n"], document["gap_median_ms"], document["answered_caller_turns"]) == (
        1,
        300,
        1,
    )
    assert (document["barge_in_n"], document["barge_in_stop_median_ms"]) == (1, 120)
    assert document["activity_min_peak"] == 1000


def test_without_the_member_the_noise_is_activity_as_before_this_batch() -> None:
    turns, agent = the_field_call()
    document = extract(turns, agent)  # not given: 1, any non-zero sample
    assert "activity_min_peak" not in document
    assert document["first_agent_audio_ms"] == 0  # the noise at the origin
    first, second = document["turns"]
    assert (first["gap_ms"], first["overlap_ms"]) == (0, 200)  # a noise frame at the edge 1200
    # the noise is the activity the line interrupted: it ends with the last frame, at 3000 ms
    assert second["barge_in_stop_ms"] == 3000 + 20 - 2000
    # the same call with the member 1 measures the same numbers
    explicit = extract(turns, agent, peak=1)
    assert explicit["activity_min_peak"] == 1
    assert {k: v for k, v in explicit.items() if k != "activity_min_peak"} == document


def test_a_threshold_equal_to_the_noise_peak_counts_the_noise_and_one_above_does_not() -> None:
    turns, agent = the_field_call()
    at_noise = extract(turns, agent, peak=250)
    assert at_noise["first_agent_audio_ms"] == 0
    above = extract(turns, agent, peak=251)
    assert above["first_agent_audio_ms"] == 400


def test_speech_below_the_threshold_counts_as_silence() -> None:
    """The documented consequence: a quiet agent (peak 500) is not heard by a threshold of 501."""
    agent = [log_frame(T0 + ms(400 + 20 * k), noise(500)) for k in range(10)]
    turns = [turn(0, 100)]
    quiet = extract(turns, agent, peak=501)
    assert quiet["first_agent_audio_ms"] is None
    assert quiet["turns"][0]["gap_ms"] is None
    assert quiet["turns"][0]["overlap_ms"] == 0
    assert (quiet["answered_caller_turns"], quiet["gap_n"], quiet["gap_median_ms"]) == (0, 0, None)
    heard = extract(turns, agent, peak=500)
    assert heard["first_agent_audio_ms"] == 400
    assert heard["turns"][0]["gap_ms"] == 400 - (100 + 180 + 20)  # edge 300 -> 100 ms
    assert heard["answered_caller_turns"] == 1


def test_the_quantum_and_the_other_values_do_not_depend_on_the_threshold() -> None:
    turns, agent = the_field_call()
    base = extract(turns, agent)
    gated = extract(turns, agent, peak=1000)
    for key in ("quantum_ms", "caller_frame_ms", "setup_ms", "dialect", "clock", "format"):
        assert gated[key] == base[key], key
    # a long frame still sets the quantum even when it is below the threshold
    long_quiet = log_frame(T0 + ms(100), noise(10) * 3)  # 60 ms of quiet audio
    wide = extract([], [long_quiet, log_frame(T0 + ms(300), noise(4000))], peak=1000)
    assert wide["quantum_ms"] == 60
    assert wide["first_agent_audio_ms"] == 300


def test_a_frame_holding_one_loud_sample_among_quiet_ones_is_active() -> None:
    one_loud = pcm([3] * 159 + [-2000])
    document = extract([], [log_frame(T0 + ms(70), one_loud)], peak=2000)
    assert document["first_agent_audio_ms"] == 70
    document = extract([], [log_frame(T0 + ms(70), one_loud)], peak=2001)
    assert document["first_agent_audio_ms"] is None


# ---------------------------------------------------------------------- mu-law and the codecs


def test_the_threshold_uses_the_decoded_value_of_a_mulaw_frame() -> None:
    """Twilio dialect: mu-law 0xE0 decodes to 372 (noise), 0xA0 to 7932 (speech), 0x60 to -372."""
    assert (ref_mulaw(0xE0), ref_mulaw(0xA0), ref_mulaw(0x60)) == (372, 7932, -372)
    agent = [log_frame(T0 + ms(20 * k), b"\xe0" * 160, codec="mulaw") for k in range(20)]
    agent += [log_frame(T0 + ms(400 + 20 * k), b"\xa0" * 160, codec="mulaw") for k in range(5)]
    agent += [log_frame(T0 + ms(600), b"\x60" * 160, codec="mulaw")]
    common: dict[str, Any] = {"dialect": "twilio-media-stream/1", "codec": "mulaw"}
    # 1000 is above the wire value of the speech byte 0xA0 (160) and below its decoded value:
    # only a decoded comparison hears the speech and ignores the noise byte 0xE0 (224)
    gated = extract([], sorted(agent, key=lambda f: f.t_ns), peak=1000, **common)
    assert gated["first_agent_audio_ms"] == 400
    assert extract([], agent, peak=372, **common)["first_agent_audio_ms"] == 0
    assert extract([], agent, peak=373, **common)["first_agent_audio_ms"] == 400
    # the negative code has the magnitude 372
    only_negative = [log_frame(T0 + ms(50), b"\x60" * 160, codec="mulaw")]
    assert extract([], only_negative, peak=372, **common)["first_agent_audio_ms"] == 50
    assert extract([], only_negative, peak=373, **common)["first_agent_audio_ms"] is None
    # digital zero stays silent whatever the threshold; the small byte 0xFE decodes to 8
    zero = [log_frame(T0 + ms(50), b"\xff" * 160, codec="mulaw")]
    assert extract([], zero, peak=1, **common)["first_agent_audio_ms"] is None
    small = [log_frame(T0 + ms(50), b"\xfe" * 160, codec="mulaw")]
    assert extract([], small, peak=8, **common)["first_agent_audio_ms"] == 50
    assert extract([], small, peak=9, **common)["first_agent_audio_ms"] is None


def test_an_alaw_frame_is_decoded_too() -> None:
    """The recorder also decodes A-law (no Phase 4 dialect uses it): the rule is the same."""
    frames = [log_frame(T0 + ms(40), b"\xd5" * 160, codec="alaw")]  # decodes to +8
    kwargs: dict[str, Any] = {"codec": "alaw", "dialect": "twilio-media-stream/1"}
    assert extract([], frames, peak=8, **kwargs)["first_agent_audio_ms"] == 40
    assert extract([], frames, peak=9, **kwargs)["first_agent_audio_ms"] is None


# ---------------------------------------------------------------------- the document


def test_the_document_echoes_the_member_exactly_when_it_was_given() -> None:
    turns, agent = the_field_call()
    for value in (1, 2, 250, 1000, 32767):
        document = extract(turns, agent, peak=value)
        assert document["activity_min_peak"] == value
        assert type(document["activity_min_peak"]) is int
        assert set(document) == {*OLD_KEYS, "activity_min_peak"}
        simtiming_mod().validate_timing_document(document)
    plain = extract(turns, agent)
    assert set(plain) == OLD_KEYS
    explicit_none = simtiming_mod().extract_timing(
        dialect="pipecat-protobuf/1",
        fmt=recorder_mod().RecorderFormat(8000, "pcm_s16le"),
        wait_for_agent_silence_ms=300,
        connect_started_ns=T0 - ms(300),
        connect_ended_ns=T0,
        established=True,
        observed_until_ns=T0 + ms(20_000),
        caller_turns=turns,
        agent_frames=agent,
        activity_min_peak=None,
    )
    assert set(explicit_none) == OLD_KEYS  # None is "not given"


def test_a_call_that_was_not_established_still_records_the_setting() -> None:
    document = extract([], [], peak=300, established=False)
    assert document["first_agent_audio_ms"] is None
    assert document["activity_min_peak"] == 300
    simtiming_mod().validate_timing_document(document)
    assert set(extract([], [], established=False)) == OLD_KEYS


@pytest.mark.parametrize("bad", BAD)
def test_extract_timing_refuses_a_bad_threshold_with_one_stable_code(bad: Any) -> None:
    with pytest.raises(simtiming_mod().TimingDocumentError) as error:
        extract([], [], peak=bad)
    assert str(error.value) == "activity_min_peak_invalid"
    assert error.value.code == "activity_min_peak_invalid"


@pytest.mark.parametrize("bad", BAD)
def test_a_bad_threshold_is_the_timing_error_with_frames_and_turns(bad: Any) -> None:
    """The refusal is the extractor's own error, not the predicate's, whatever the input."""
    turns, agent = the_field_call()
    with pytest.raises(simtiming_mod().TimingDocumentError) as error:
        extract(turns, agent, peak=bad)
    assert str(error.value) == "activity_min_peak_invalid"


def test_the_signature_has_the_keyword_with_the_default_none() -> None:
    parameter = inspect.signature(simtiming_mod().extract_timing).parameters["activity_min_peak"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_the_validator_takes_the_member_optional_and_refuses_a_bad_value() -> None:
    timing = simtiming_mod()
    turns, agent = the_field_call()
    document = extract(turns, agent, peak=700)
    timing.validate_timing_document(document)  # with the member
    old = {k: v for k, v in document.items() if k != "activity_min_peak"}
    timing.validate_timing_document(old)  # an earlier document stays valid
    for bad in BAD:
        broken = copy.deepcopy(document)
        broken["activity_min_peak"] = bad
        with pytest.raises(timing.TimingDocumentError) as error:
            timing.validate_timing_document(broken)
        assert str(error.value) == "activity_min_peak_invalid", bad
    broken = copy.deepcopy(document)
    broken["activity_min_peak"] = None
    with pytest.raises(timing.TimingDocumentError) as error:
        timing.validate_timing_document(broken)
    assert str(error.value) == "activity_min_peak_invalid"  # present means a value
    stray = copy.deepcopy(document)
    stray["other"] = 1
    with pytest.raises(timing.TimingDocumentError) as error:
        timing.validate_timing_document(stray)
    assert str(error.value) == "document_keys"
    missing = copy.deepcopy(document)
    del missing["gap_n"]
    with pytest.raises(timing.TimingDocumentError) as error:
        timing.validate_timing_document(missing)
    assert str(error.value) == "document_keys"


def test_the_shipped_timing_schema_has_the_member_optional_with_the_range() -> None:
    schema = simtiming_mod().TIMING_JSON_SCHEMA
    assert "activity_min_peak" not in schema["required"]
    assert schema["properties"]["activity_min_peak"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 32767,
    }
    assert schema["additionalProperties"] is False
    turns, agent = the_field_call()
    validator = Draft202012Validator(dict(schema))
    for peak in ("absent", 1, 1000):
        assert list(validator.iter_errors(extract(turns, agent, peak=peak))) == []
    for bad in (0, 32768, True, 1.5, "5", None):
        document = extract(turns, agent, peak=700)
        document["activity_min_peak"] = bad
        assert list(validator.iter_errors(document)), bad


def test_a_reader_takes_a_document_without_the_member_as_1() -> None:
    turns, agent = the_field_call()
    document = extract(turns, agent)
    assert turntaking_mod().activity_min_peak_of(document) == 1
    assert turntaking_mod().activity_min_peak_of(extract(turns, agent, peak=9)) == 9


def test_the_wording_is_closed() -> None:
    timing = simtiming_mod()
    assert timing.activity_wording(1) == "any non-zero sample"
    assert timing.activity_wording(2) == "peak >= 2"
    assert timing.activity_wording(1000) == "peak >= 1000"
    assert timing.activity_wording(32767) == "peak >= 32767"
    for bad in (0, 32768, True, 1.0, None, "1"):
        with pytest.raises(timing.TimingDocumentError) as error:
            timing.activity_wording(bad)
        assert str(error.value) == "activity_min_peak_invalid"


def test_the_record_keys_are_the_same_with_and_without_the_threshold_for_every_dialect() -> None:
    """T-DEG: the document has the same keys, turn keys and self keys; only the member is added."""
    turns, agent = the_field_call()
    for dialect in DIALECTS:
        plain = extract(turns, agent, dialect=dialect)
        gated = extract(turns, agent, dialect=dialect, peak=1000)
        assert set(gated) - set(plain) == {"activity_min_peak"}
        assert set(plain) == OLD_KEYS
        assert [set(t) for t in gated["turns"]] == [set(t) for t in plain["turns"]]
        assert [set(t["self"]) for t in gated["turns"]] == [set(SELF_KEYS)] * 2
        # the metric fields are present (a missing edge is None, never absent and never 0)
        for key in ("first_agent_audio_ms", "gap_median_ms", "barge_in_stop_median_ms"):
            assert key in gated


def test_the_metrics_of_a_document_do_not_read_the_member() -> None:
    """``timing_value`` reads the value fields; a missing edge is the same error with or without
    the threshold (the message names the edge, never the threshold)."""
    timing = simtiming_mod()
    quiet = [log_frame(T0 + ms(40), noise(300))]
    document = extract([], quiet, peak=500)
    value = timing.timing_value(document, "sim_first_agent_audio_ms")
    assert (value.value, value.error_code) == (None, "timing_edge_missing")
    assert value.message == (
        "timing_edge_missing: first_agent_audio_ms has no value; missing edge: first active agent "
        "frame; caller turns 0"
    )


# ---------------------------------------------------------------------- the reference property


def ref_observables(
    frames: list[tuple[int, int]], turns: list[Any], threshold: int, wait_ms: int, until: int
) -> dict[str, Any]:
    """first audio, per-turn gap, overlap and barge stop from plain loops (frames of 20 ms)."""
    live = [t for t, peak in frames if peak >= threshold]
    first = (live[0] - T0) // 1_000_000 if live else None
    out: list[tuple[Any, ...]] = []
    for t in turns:
        edge = t.last_frame_ns + ms(20)
        later = [a for a in live if a >= edge]
        gap = (later[0] - edge) // 1_000_000 if later else None
        overlap = sum(1 for a in live if t.first_frame_ns <= a < edge) * 20
        stop = None
        if t.barge_in_after_ms is not None:
            silence = ms(wait_ms)
            runs: list[list[int]] = []
            for a in live:
                if runs and a - runs[-1][-1] < silence:
                    runs[-1].append(a)
                else:
                    runs.append([a])
            chosen = None
            before = 0
            for run in runs:
                for a in run:
                    if a <= t.first_frame_ns:
                        chosen, before = run, a
            if chosen is not None and t.first_frame_ns - before < silence:
                if until - chosen[-1] >= silence:
                    delta = chosen[-1] + ms(20) - t.first_frame_ns
                    stop = delta // 1_000_000 if delta > 0 else None
        out.append((t.turn_seq, gap, overlap, stop))
    return {"first": first, "turns": out}


@given(
    threshold=st.integers(min_value=1, max_value=5000),
    wait_ms=st.sampled_from([100, 300, 800]),
    peaks=st.lists(
        st.sampled_from([0, 2, 99, 100, 101, 999, 1000, 1001, 4000]), min_size=1, max_size=60
    ),
    starts=st.lists(st.integers(min_value=0, max_value=1_000), min_size=0, max_size=3),
    barges=st.lists(st.one_of(st.none(), st.integers(0, 500)), min_size=3, max_size=3),
)
@HSETTINGS
def test_the_document_matches_the_reference_for_random_calls_and_thresholds(
    threshold: int, wait_ms: int, peaks: list[int], starts: list[int], barges: list[int | None]
) -> None:
    frames = [(T0 + ms(20 * k), peak) for k, peak in enumerate(peaks)]
    payloads = [log_frame(t, noise(peak) if peak else zeros()) for t, peak in frames]
    cursor = 0
    turns = []
    for seq, gap in enumerate(starts):
        cursor += gap
        turns.append(turn(seq, cursor, 3, barge=barges[seq]))
        cursor += 3 * 20 + 20
    until = T0 + ms(20 * len(peaks)) + ms(5000)
    document = extract(turns, payloads, peak=threshold, wait_ms=wait_ms, until=until)
    expected = ref_observables(frames, turns, threshold, wait_ms, until)
    assert document["first_agent_audio_ms"] == expected["first"]
    got = [
        (t["turn_seq"], t["gap_ms"], t["overlap_ms"], t["barge_in_stop_ms"])
        for t in document["turns"]
    ]
    assert got == expected["turns"]
    # the default is the threshold 1
    assert (
        extract(turns, payloads, wait_ms=wait_ms, until=until)["turns"]
        == extract(turns, payloads, peak=1, wait_ms=wait_ms, until=until)["turns"]
    )


# ---------------------------------------------------------------------- the agent segments


def act() -> Any:
    return importlib.import_module("tracer_evalcore.audio.activity")


def stereo_recording(channel_one: list[int]) -> bytes:
    inter: list[int] = []
    for sample in channel_one:
        inter += [0, sample]
    data = array("h", inter)
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def noisy_agent_channel() -> list[int]:
    """150 frames of 20 ms at 8 kHz: noise (peak 250) except speech (peak 4000) in frames 25 to 34
    and 100 to 104."""
    samples: list[int] = []
    for frame in range(150):
        peak = 4000 if (25 <= frame <= 34 or 100 <= frame <= 104) else 250
        samples += [peak, -peak // 2] + [1] * 158
    return samples


def negative_agent_channel() -> list[int]:
    """The same call with the loudest sample of every frame NEGATIVE (-250 in the pauses, -4000 in
    the speech): the peak is the largest absolute value, not the largest value."""
    samples: list[int] = []
    for frame in range(150):
        peak = 4000 if (25 <= frame <= 34 or 100 <= frame <= 104) else 250
        samples += [-peak, peak // 2] + [1] * 158
    return samples


def plan(peak: Any = "absent", wait: int = 300, channel: list[int] | None = None) -> Any:
    extra: dict[str, Any] = {} if peak == "absent" else {"activity_min_peak": peak}
    return act().activity_chunk_plan(
        stereo_recording(noisy_agent_channel() if channel is None else channel),
        sample_rate_hz=8000,
        channels=2,
        channel=1,
        wait_for_agent_silence_ms=wait,
        **extra,
    )


def test_the_segments_are_the_speech_bursts_when_the_threshold_is_above_the_noise() -> None:
    gated = plan(1000)
    assert [(c.start_frame, c.end_frame) for c in gated.chunks] == [(4000, 5600), (16000, 16800)]
    assert gated.total_frames == 24000
    # the noise is not removed: with the default rule the whole channel is one segment
    plain = plan()
    assert [(c.start_frame, c.end_frame) for c in plain.chunks] == [(0, 24000)]
    assert [(c.start_frame, c.end_frame) for c in plan(250).chunks] == [(0, 24000)]
    assert [(c.start_frame, c.end_frame) for c in plan(251).chunks] == [
        (4000, 5600),
        (16000, 16800),
    ]
    assert [(c.start_frame, c.end_frame) for c in plan(1).chunks] == [(0, 24000)]


def test_a_negative_peak_counts_in_the_plan_as_it_does_in_the_predicate() -> None:
    channel = negative_agent_channel()
    bursts = [(4000, 5600), (16000, 16800)]
    assert [(c.start_frame, c.end_frame) for c in plan(1000, channel=channel).chunks] == bursts
    assert [(c.start_frame, c.end_frame) for c in plan(250, channel=channel).chunks] == [(0, 24000)]
    assert [(c.start_frame, c.end_frame) for c in plan(251, channel=channel).chunks] == bursts
    assert [(c.start_frame, c.end_frame) for c in plan(4000, channel=channel).chunks] == bursts
    assert plan(4001, channel=channel).chunks == ()


def test_a_speech_burst_below_the_threshold_is_not_a_segment() -> None:
    assert plan(4001).chunks == ()
    assert [(c.start_frame, c.end_frame) for c in plan(4000).chunks] == [
        (4000, 5600),
        (16000, 16800),
    ]


def test_the_plan_records_the_member_only_when_it_was_given() -> None:
    plain = plan()
    assert plain.activity_min_peak is None
    assert "activity_min_peak" not in plain.as_metadata()
    gated = plan(1000)
    assert gated.activity_min_peak == 1000
    metadata = gated.as_metadata()
    assert metadata["activity_min_peak"] == 1000
    assert metadata["format"] == act().ACTIVITY_PLAN_FORMAT
    assert metadata["wait_for_agent_silence_ms"] == 300
    assert {
        k: v for k, v in metadata.items() if k != "activity_min_peak"
    } == plain.as_metadata() | {"chunks": metadata["chunks"]}
    explicit = plan(1)
    assert explicit.activity_min_peak == 1
    assert explicit.as_metadata()["activity_min_peak"] == 1
    assert json.loads(json.dumps(metadata)) == metadata  # integers only


@pytest.mark.parametrize("bad", BAD)
def test_the_plan_refuses_a_bad_threshold(bad: Any) -> None:
    chunker = importlib.import_module("tracer_evalcore.audio.chunker")
    with pytest.raises(chunker.ChunkerParameterError):
        plan(bad)


def test_the_default_plan_is_the_one_of_the_earlier_batches() -> None:
    """Same arguments, same plan: with no threshold the call is the call of batch B."""
    one = plan()
    two = plan(wait=300)
    assert [(c.start_frame, c.end_frame, c.wav) for c in one.chunks] == [
        (c.start_frame, c.end_frame, c.wav) for c in two.chunks
    ]
    parameter = inspect.signature(act().activity_chunk_plan).parameters["activity_min_peak"]
    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
