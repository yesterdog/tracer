# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018
"""BL-313: the optional activity threshold ``turn_taking.activity_min_peak`` of the sim profile
(ADR 0026 rule 10, ADR 0027 rule 13; contract docs/phase4/api-surface-batch-activity.md sections 2
to 4).

A received agent frame is ACTIVE iff the maximum absolute value of its decoded 16-bit samples is at
least ``activity_min_peak`` (an integer 1 to 32767). Absent means 1, which is exactly the rule
before this batch (any non-zero sample): every test of the earlier batches keeps its meaning. This
module pins the pure parts: the profile file and the stored profile, the policy object, the
predicate and the turn-taking decision. Expected values are computed here with plain Python over
lists, from the contract's rule, not from the implementation.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import struct
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    frame_sequences,
    ms,
    pcm,
    recorder_mod,
    ref_mulaw,
    ref_turn_decision,
    turntaking_mod,
)
from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    dumps,
    mod,
    profile_file_doc,
    stored_profile,
)

HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

BASE = {"caller_speaks_first": False, "wait_for_agent_silence_ms": 800, "max_silence_ms": 5000}
#: values that are valid (the closed range 1 to 32767 of int16 magnitudes)
VALID = (1, 2, 3, 36, 100, 300, 624, 1000, 4052, 16384, 32766, 32767)
#: values that are refused, each of a different kind: below, above, wrong type
INVALID = (
    0,
    -1,
    -32768,
    32768,
    65535,
    2**31,
    2**53,
    1.0,
    100.0,
    100.5,
    True,
    False,
    "100",
    None,
    [1],
)
#: raw JSON text of values a Python value cannot spell (float forms, an exponent, a big number)
INVALID_JSON = (
    "0",
    "-5",
    "32768",
    "1.0",
    "100.0",
    "1e2",
    "100.5",
    "true",
    "false",
    "null",
    '"100"',
    "[100]",
    "{}",
)


def policy(**extra: Any) -> Any:
    return turntaking_mod().TurnTakingPolicy(**{**BASE, **extra})


def file_text(**turn_taking: Any) -> str:
    document = profile_file_doc()
    document["turn_taking"] = {
        "caller_speaks_first": True,
        "wait_for_agent_silence_ms": 300,
        "max_silence_ms": 3000,
        **turn_taking,
    }
    text: str = dumps(document)
    return text


def file_text_with_raw(raw: str) -> str:
    """The profile file text with ``activity_min_peak`` spelled exactly as ``raw`` (JSON)."""
    marker = "ACTIVITY_RAW"
    text = file_text(activity_min_peak=marker)
    return text.replace(f'"{marker}"', raw)


def file_code(text: str) -> str:
    simprofile = mod("simprofile")
    with pytest.raises(simprofile.SimProfileError) as caught:
        simprofile.parse_sim_profile_file(text)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def stored_code(document: Any) -> str:
    simprofile = mod("simprofile")
    with pytest.raises(simprofile.SimProfileError) as caught:
        simprofile.validate_sim_profile(document, check_caps=False)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


# ---------------------------------------------------------------------- the profile file


def test_the_profile_file_takes_an_optional_activity_min_peak_from_1_to_32767() -> None:
    simprofile = mod("simprofile")
    for value in VALID:
        parsed = simprofile.parse_sim_profile_file(file_text(activity_min_peak=value))
        assert parsed.turn_taking["activity_min_peak"] == value
        assert type(parsed.turn_taking["activity_min_peak"]) is int
        assert set(parsed.turn_taking) == {
            "caller_speaks_first",
            "wait_for_agent_silence_ms",
            "max_silence_ms",
            "activity_min_peak",
        }
    # absent: the file is valid as before and the parsed section holds exactly the three keys
    absent = simprofile.parse_sim_profile_file(file_text())
    assert set(absent.turn_taking) == set(BASE)


@pytest.mark.parametrize("raw", INVALID_JSON)
def test_the_profile_file_refuses_an_activity_min_peak_that_is_not_an_integer_1_to_32767(
    raw: str,
) -> None:
    """The stable code is the one of every other fault of the object: ``turn_taking``."""
    assert file_code(file_text_with_raw(raw)) == "sim_profile_file_invalid_turn_taking"


def test_the_profile_file_boundaries_are_exact() -> None:
    assert file_code(file_text_with_raw("0")) == "sim_profile_file_invalid_turn_taking"
    assert file_code(file_text_with_raw("32768")) == "sim_profile_file_invalid_turn_taking"
    mod("simprofile").parse_sim_profile_file(file_text_with_raw("1"))
    mod("simprofile").parse_sim_profile_file(file_text_with_raw("32767"))


def test_an_unknown_turn_taking_key_is_still_refused() -> None:
    """The object stays closed: only the optional member is new."""
    assert file_code(file_text(activity_threshold=5)) == "sim_profile_file_invalid_turn_taking"
    assert file_code(file_text(activity_min_peak=5, extra=1)) == (
        "sim_profile_file_invalid_turn_taking"
    )


def test_the_three_required_turn_taking_values_are_still_required() -> None:
    document = profile_file_doc()
    for key in BASE:
        broken = json.loads(dumps(document))
        broken["turn_taking"] = {
            "activity_min_peak": 5,
            **{k: v for k, v in BASE.items() if k != key},
        }
        assert file_code(dumps(broken)) == "sim_profile_file_invalid_turn_taking", key


# ---------------------------------------------------------------------- the stored profile


def test_the_stored_profile_records_the_member_exactly_when_the_file_gave_it() -> None:
    """Given: recorded as used, under ``turn_taking``. Absent: the stored document is the one of
    every earlier run (no new key); a reader takes a missing member as 1 (section 2)."""
    for value in (1, 250, 32767):
        document = profile_file_doc()
        document["turn_taking"]["activity_min_peak"] = value
        stored = stored_profile(document)
        assert stored["turn_taking"]["activity_min_peak"] == value
        assert set(stored["turn_taking"]) == {*BASE, "activity_min_peak"}
    plain = stored_profile(profile_file_doc())
    assert set(plain["turn_taking"]) == set(BASE)


@pytest.mark.parametrize("bad", [0, -1, 32768, 1.0, 100.5, True, "100", None])
def test_the_stored_profile_validator_refuses_a_bad_member_with_its_pointer(bad: Any) -> None:
    document = stored_profile(profile_file_doc())
    document["turn_taking"]["activity_min_peak"] = bad
    assert stored_code(document) == "schema:/turn_taking/activity_min_peak"


def test_an_old_stored_profile_without_the_member_still_validates() -> None:
    """Every run stored before this batch has no member: it stays valid (it means 1)."""
    document = stored_profile(profile_file_doc())
    assert "activity_min_peak" not in document["turn_taking"]
    mod("simprofile").validate_sim_profile(document, check_caps=False)


def test_the_stored_profile_schema_has_the_member_optional_with_the_range() -> None:
    schema = mod("simprofile").SIM_PROFILE_JSON_SCHEMA["properties"]["turn_taking"]
    assert schema["required"] == [
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms",
    ]
    assert list(schema["properties"]) == [
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms",
        "activity_min_peak",
    ]
    assert schema["properties"]["activity_min_peak"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 32767,
    }
    assert schema["additionalProperties"] is False
    assert "default" not in json.dumps(schema)


def test_the_member_changes_the_digest_of_the_stored_profile_only_when_given() -> None:
    """The snapshot digest follows the stored bytes: a run without the member keeps the digest it
    had; a run with it has another one (so the setting is part of what the run was)."""
    plain = json.dumps(stored_profile(profile_file_doc()), sort_keys=True)
    document = profile_file_doc()
    document["turn_taking"]["activity_min_peak"] = 1
    explicit = json.dumps(stored_profile(document), sort_keys=True)
    assert plain != explicit
    assert json.loads(explicit)["turn_taking"]["activity_min_peak"] == 1


# ---------------------------------------------------------------------- the policy object


def test_the_policy_has_the_member_as_its_fourth_field_with_the_one_default_of_1() -> None:
    tt = turntaking_mod()
    cls = tt.TurnTakingPolicy
    fields = {f.name: f for f in dataclasses.fields(cls)}
    assert tuple(fields) == (
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms",
        "activity_min_peak",
    )
    for name in tuple(fields)[:3]:  # the three values of the owner still have no default
        assert fields[name].default is dataclasses.MISSING, name
        assert fields[name].default_factory is dataclasses.MISSING, name
    assert fields["activity_min_peak"].default == 1
    assert fields["activity_min_peak"].default_factory is dataclasses.MISSING
    parameters = inspect.signature(cls).parameters
    assert [n for n, p in parameters.items() if p.default is inspect.Parameter.empty] == [
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms",
    ]
    assert policy().activity_min_peak == 1
    assert policy(activity_min_peak=250).activity_min_peak == 250
    assert policy() == policy(activity_min_peak=1)  # absent is the value 1, nothing else


def test_the_policy_range_is_1_to_32767_and_a_bad_value_has_one_stable_code() -> None:
    tt = turntaking_mod()
    for value in VALID:
        assert policy(activity_min_peak=value).activity_min_peak == value
    for bad in INVALID:
        with pytest.raises(tt.TurnTakingError) as error:
            policy(activity_min_peak=bad)
        assert str(error.value) == "activity_min_peak_invalid", bad
        assert error.value.code == "activity_min_peak_invalid", bad


def test_the_policy_reads_the_profile_section_with_and_without_the_member() -> None:
    tt = turntaking_mod()
    cls = tt.TurnTakingPolicy
    assert cls.from_profile(BASE) == policy()
    assert cls.from_profile(BASE).activity_min_peak == 1
    assert cls.from_profile({**BASE, "activity_min_peak": 700}) == policy(activity_min_peak=700)
    for bad in INVALID:
        with pytest.raises(tt.TurnTakingError) as error:
            cls.from_profile({**BASE, "activity_min_peak": bad})
        assert str(error.value) == "activity_min_peak_invalid", bad
    with pytest.raises(tt.TurnTakingError) as error:
        cls.from_profile({**BASE, "activity_min_peak": 5, "extra": 1})
    assert str(error.value) == "turn_taking_unknown_key"
    for key in BASE:  # the member does not stand in for a missing required key
        partial = {k: v for k, v in BASE.items() if k != key}
        with pytest.raises(tt.TurnTakingError) as error:
            cls.from_profile({**partial, "activity_min_peak": 5})
        assert str(error.value) == f"{key}_missing"


def test_a_reader_takes_a_missing_member_as_1_in_the_section_and_in_the_timing_document() -> None:
    tt = turntaking_mod()
    assert tt.activity_min_peak_of(BASE) == 1
    assert tt.activity_min_peak_of({**BASE, "activity_min_peak": 42}) == 42
    assert tt.activity_min_peak_of({}) == 1
    with pytest.raises(tt.TurnTakingError) as error:
        tt.activity_min_peak_of({"activity_min_peak": 0})
    assert str(error.value) == "activity_min_peak_invalid"
    with pytest.raises(tt.TurnTakingError):
        tt.activity_min_peak_of({"activity_min_peak": True})
    with pytest.raises(tt.TurnTakingError):
        tt.activity_min_peak_of(None)


def test_the_range_constants_are_the_contract() -> None:
    tt = turntaking_mod()
    assert tt.ACTIVITY_MIN_PEAK_MIN == 1
    assert tt.ACTIVITY_MIN_PEAK_MAX == 32767
    assert tt.ACTIVITY_MIN_PEAK_DEFAULT == 1


# ---------------------------------------------------------------------- the predicate


def test_a_frame_is_active_iff_its_largest_absolute_sample_reaches_the_threshold() -> None:
    tt = turntaking_mod()
    is_active = tt.agent_frame_is_active
    frame = pcm([3, -250, 17, 249, 0, -1])  # the peak is 250 (a negative sample)
    assert is_active(frame, min_peak=250) is True
    assert is_active(frame, min_peak=251) is False
    assert is_active(frame, min_peak=249) is True
    assert is_active(frame, min_peak=1) is True
    assert is_active(frame) is True
    # positive and negative peaks alike
    assert is_active(pcm([100]), min_peak=100) is True
    assert is_active(pcm([-100]), min_peak=100) is True
    assert is_active(pcm([99]), min_peak=100) is False
    assert is_active(pcm([-99]), min_peak=100) is False
    # the most negative sample has the magnitude 32768
    assert is_active(pcm([-32768]), min_peak=32767) is True
    assert is_active(pcm([32767]), min_peak=32767) is True
    assert is_active(pcm([-32767]), min_peak=32767) is True
    assert is_active(pcm([-32766]), min_peak=32767) is False
    # one loud sample among zeros is enough; many quiet ones are not (a peak, not an energy)
    assert is_active(pcm([0] * 159 + [700]), min_peak=700) is True
    assert is_active(pcm([699] * 160), min_peak=700) is False
    assert is_active(pcm([5, -5] * 5000), min_peak=6) is False
    # an all-zero frame is silent at every threshold, the default one included
    assert is_active(pcm([0] * 160)) is False
    for value in (1, 2, 32767):
        assert is_active(pcm([0] * 160), min_peak=value) is False


def test_the_default_of_the_predicate_is_the_rule_before_this_batch() -> None:
    tt = turntaking_mod()
    for samples in ([0, 0], [1, 0], [0, -1], [32767, -32768], [0] * 160):
        data = pcm(samples)
        assert tt.agent_frame_is_active(data) is any(samples)
        assert tt.agent_frame_is_active(data, min_peak=1) is any(samples)
    parameter = inspect.signature(tt.agent_frame_is_active).parameters["min_peak"]
    assert parameter.default == 1
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_predicate_refuses_a_bad_threshold_and_keeps_its_frame_rules() -> None:
    tt = turntaking_mod()
    for bad in INVALID:
        with pytest.raises(tt.TurnTakingError) as error:
            tt.agent_frame_is_active(pcm([5]), min_peak=bad)
        assert str(error.value) == "activity_min_peak_invalid", bad
    for bad_frame in (b"", b"\x00", b"\x00\x00\x00", bytearray(b"\x00\x00"), None):
        with pytest.raises(tt.TurnTakingError) as error:
            tt.agent_frame_is_active(bad_frame, min_peak=10)
        assert str(error.value) == "frame_invalid"


@given(
    samples=st.lists(st.integers(min_value=-32768, max_value=32767), min_size=1, max_size=64),
    threshold=st.integers(min_value=1, max_value=32767),
)
@HSETTINGS
def test_activity_equals_the_peak_rule_for_random_frames(
    samples: list[int], threshold: int
) -> None:
    got = turntaking_mod().agent_frame_is_active(
        struct.pack(f"<{len(samples)}h", *samples), min_peak=threshold
    )
    assert got is (max(abs(s) for s in samples) >= threshold)


def test_the_threshold_applies_to_decoded_mulaw_and_not_to_the_wire_byte() -> None:
    """The frame the predicate sees is the decoded 16-bit PCM of the wire codec: for mu-law that is
    the G.711 value, never the byte. 0xE0 has the wire value 224 and decodes to 372; 0xA0 has the
    wire value 160 and decodes to 7932; the byte 0xFE decodes to 8 (small) though its value is 254."""
    tt = turntaking_mod()
    decode = recorder_mod().decode_to_pcm16
    assert (ref_mulaw(0xE0), ref_mulaw(0xA0), ref_mulaw(0xFE)) == (372, 7932, 8)
    quiet_negative = 0x60  # decodes to -372
    assert ref_mulaw(quiet_negative) == -372

    def active_at(code: int, threshold: int) -> bool:
        return bool(
            tt.agent_frame_is_active(decode("mulaw", bytes([code]) * 160), min_peak=threshold)
        )

    assert active_at(0xE0, 372) is True and active_at(0xE0, 373) is False
    assert active_at(quiet_negative, 372) is True and active_at(quiet_negative, 373) is False
    assert active_at(0xA0, 7932) is True and active_at(0xA0, 7933) is False
    assert active_at(0xA0, 1000) is True  # a wire byte of 160 compared with 1000 would be silent
    assert active_at(0xFE, 100) is False  # a wire byte of 254 compared with 100 would be active
    assert active_at(0xFE, 8) is True and active_at(0xFE, 9) is False
    assert active_at(0xFF, 1) is False and active_at(0x7F, 1) is False  # digital zero stays silent
    # one speech-coded sample among digital zeros
    assert tt.agent_frame_is_active(decode("mulaw", b"\xff" * 159 + b"\xa0"), min_peak=7932) is True


# ---------------------------------------------------------------------- the decision


def frame_of(peak: int) -> bytes:
    data: bytes = pcm([peak, 0, -1, 1] * 40)
    return data


def taker(**extra: Any) -> Any:
    return turntaking_mod().TurnTaker(policy(**extra))


def shape(decision: Any) -> tuple[str, int | None, str | None]:
    return (decision.kind, decision.at_ns, decision.cause)


def test_noise_below_the_threshold_never_starts_or_extends_activity() -> None:
    """The field case: noise (peak 250) in every pause, speech (peak 4000). With the default the
    caller never gets a silence; with a threshold of 1000 the speech is the only activity."""
    frames = [(ms(t), frame_of(250)) for t in range(0, 600, 20)]
    frames += [(ms(t), frame_of(4000)) for t in range(600, 800, 20)]
    frames += [(ms(t), frame_of(250)) for t in range(800, 6000, 20)]
    plain = taker()
    plain.begin_wait(0)
    for when, data in frames:
        assert plain.agent_frame(when, data) is True
    assert shape(plain.poll(ms(6000))) == ("end", ms(5000), "max_silence")
    gated = taker(activity_min_peak=1000)
    gated.begin_wait(0)
    results = [gated.agent_frame(when, data) for when, data in frames]
    assert results.count(True) == 10  # the ten speech frames, 600 to 780
    assert shape(gated.poll(ms(6000))) == ("speak", ms(780 + 800), "agent_silent")


def test_the_taker_counts_a_barge_in_from_the_first_active_frame_not_from_the_noise() -> None:
    gated = taker(activity_min_peak=1000)
    gated.begin_wait(0, barge_in_after_ms=300)
    for t in range(0, 600, 20):
        assert gated.agent_frame(ms(t), frame_of(250)) is False
    assert shape(gated.poll(ms(580))) == ("wait", None, None)
    assert gated.agent_frame(ms(600), frame_of(4000)) is True
    assert shape(gated.poll(ms(899))) == ("wait", None, None)
    assert shape(gated.poll(ms(900))) == ("speak", ms(900), "barge_in")


def test_a_frame_exactly_at_the_threshold_is_activity_and_one_below_is_not() -> None:
    at = taker(activity_min_peak=500)
    at.begin_wait(0)
    assert at.agent_frame(ms(10), frame_of(500)) is True
    below = taker(activity_min_peak=501)
    below.begin_wait(0)
    assert below.agent_frame(ms(10), frame_of(500)) is False


@given(
    wait=st.integers(min_value=1, max_value=900),
    extra=st.integers(min_value=0, max_value=1_500),
    barge=st.one_of(st.none(), st.integers(min_value=0, max_value=2_500)),
    threshold=st.integers(min_value=1, max_value=2_000),
    frames=frame_sequences(max_frames=14, max_gap_ms=700, start_ms=50),
    peaks=st.lists(
        st.sampled_from([0, 1, 5, 99, 100, 101, 999, 1000, 1001, 4000]), min_size=14, max_size=14
    ),
)
@HSETTINGS
def test_the_decision_matches_the_reference_for_random_peaks_and_thresholds(
    wait: int,
    extra: int,
    barge: int | None,
    threshold: int,
    frames: list[tuple[int, bool]],
    peaks: list[int],
) -> None:
    """The reference classifies each frame by its peak against the threshold, then applies the
    rule of the earlier batch (``ref_turn_decision``); the taker must agree."""
    begin = ms(10)
    sequence = [(when, peaks[i]) for i, (when, _) in enumerate(frames)]
    expected = ref_turn_decision(
        first=True,
        speaks_first=False,
        wait_ms=wait,
        max_silence_ms=wait + extra,
        begin_ns=begin,
        barge_ms=barge,
        frames=[(when, peak >= threshold) for when, peak in sequence],
    )
    tk = turntaking_mod().TurnTaker(
        turntaking_mod().TurnTakingPolicy(
            caller_speaks_first=False,
            wait_for_agent_silence_ms=wait,
            max_silence_ms=wait + extra,
            activity_min_peak=threshold,
        )
    )
    tk.begin_wait(begin, barge_in_after_ms=barge)
    for when, peak in sequence:
        tk.agent_frame(when, frame_of(peak) if peak else pcm([0] * 160))
    final = tk.poll(max([begin + ms(wait + extra), *(w for w, _ in sequence)]) + ms(1))
    assert shape(final) == expected
