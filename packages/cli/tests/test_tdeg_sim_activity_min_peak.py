# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""BL-313, T-DEG: a simulated run with the activity threshold set has the same record keys as one
without it (the project's core rule, "never break if absent"; contract
docs/phase4/api-surface-batch-activity.md section 10).

Whatever the threshold does to the values (it moves activity edges, so a timing value may gain or
lose its edge), the result keys, the stored documents and their key sets are those of the earlier
batches; a timing value without both edges is the ``error`` ``timing_edge_missing`` and never 0; a
missing input is ``not_applicable``; nothing is ``passed`` or ``failed``. The one new key is the
optional ``activity_min_peak`` of the sim profile, of the timing document and of the chunk plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli_p4act_support import field_run  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import profile_doc, run_sim, write_inputs  # type: ignore[import-not-found,unused-ignore]
from cli_p4f_support import (  # type: ignore[import-not-found,unused-ignore]
    AGREEMENT_KEYS,
    RESULT_KEYS,
    TIMING_KEYS,
    describe,
    results_by_key,
    run_case,
)

NOTE = "observed at the simulator over pipecat-protobuf/1, quantum 20 ms"
EDGE = ("error", "timing_edge_missing")
NA_EXPORT = ("not_applicable", ("missing_input", ("agent_transcript",)))
NA_BOTH = ("not_applicable", ("missing_input", ("transcript", "agent_transcript")))


def shape(run: Any) -> dict[str, Any]:
    bundle = run.bundle()
    return {
        "results": tuple(results_by_key(bundle)),
        "statuses": {k: describe(v)[0] for k, v in results_by_key(bundle).items()},
        "timing_keys": set(bundle.run.metadata["tracer.timing"]),
        "turn_keys": [set(t) for t in bundle.run.metadata["tracer.timing"]["turns"]],
        "profile_keys": set(bundle.snapshot.metadata["tracer.sim_profile"]),
        "turn_taking_keys": set(bundle.snapshot.metadata["tracer.sim_profile"]["turn_taking"]),
        "outcome_keys": set(bundle.run.metadata["tracer.sim_outcome"]),
    }


def test_the_field_call_with_a_threshold_gives_the_result_keys_of_every_other_case(
    tmp_path: Path,
) -> None:
    run = field_run(tmp_path, 2)
    assert run.code == 0, (run.out, run.err)
    results = results_by_key(run.bundle())
    assert tuple(results) == RESULT_KEYS
    assert [describe(results[k]) for k in TIMING_KEYS] == [
        ("scored", 500),
        ("scored", 2440),
        ("scored", 0),
        ("scored", 1),
    ]
    assert [describe(results[k]) for k in AGREEMENT_KEYS] == [NA_EXPORT] * 3
    assert not any(r.status in ("passed", "failed") for r in results.values())
    assert run.bundle().verdict is None


def test_a_threshold_above_every_signal_loses_the_timing_edges_and_never_scores_them_0(
    tmp_path: Path,
) -> None:
    """Nothing reaches 32767 (the speech is at 1000), so no activity edge exists. The caller speaks
    first, so there is a turn: the first audio and the gap are the error ``timing_edge_missing``
    (and say which rule was applied); the overlap and the count are measured zeros; the agreement
    is not applicable. The call ends ``max_silence``."""
    run = field_run(tmp_path, 32767, first=True)
    assert run.value("end reason") == "max_silence"
    rule = "activity: peak >= 32767"
    assert run.value("first agent audio") == (
        f"not measured (missing edge: first active agent frame, {rule})"
    )
    assert run.value("gap after the caller's line (median)") == (
        f"not measured (missing edge: active agent frame after a caller turn, {rule})"
    )
    assert run.value("overlap") == f"0 ms ({NOTE}, {rule})"
    results = results_by_key(run.bundle())
    assert tuple(results) == RESULT_KEYS
    assert [describe(results[k]) for k in TIMING_KEYS] == [
        EDGE,
        EDGE,
        ("scored", 0),
        ("scored", 0),
    ]
    assert [describe(results[k]) for k in AGREEMENT_KEYS] == [NA_BOTH] * 3
    assert run.bundle().run.metadata["tracer.timing"]["activity_min_peak"] == 32767


def test_a_call_with_no_caller_turn_has_no_turn_timing_with_or_without_the_member(
    tmp_path: Path,
) -> None:
    """The field problem itself (the caller never gets to speak): the four values are
    ``not_applicable`` for the missing input ``turn_timing``, never 0, with the member or without."""
    for name, peak in (("plain", "absent"), ("gated", 32767)):
        run = field_run(tmp_path / name, peak)
        results = results_by_key(run.bundle())
        assert tuple(results) == RESULT_KEYS
        assert run.value("caller turns") == "0 started, 0 completed"
        assert [describe(results[k]) for k in TIMING_KEYS] == [
            ("not_applicable", ("missing_input", ("turn_timing",)))
        ] * 4


def test_a_comfort_noise_agent_with_a_threshold_above_the_noise_is_the_silent_agent_case(
    tmp_path: Path,
) -> None:
    """The comfort-noise case of the matrix (T-DEG ``comfort_noise``) with the threshold 2: the
    noise (a value of 1) is not activity, so the run has the shape of ``agent_silent``: the same
    keys, the timing edges missing, no agent segment. The only difference is the new member."""
    comfort = write_inputs(tmp_path / "c")
    comfort.scenario.write_text(
        '{"format": "tracer.fake_agent/1", "steps": [{"comfort": {"frames": 600}}]}', "utf-8"
    )
    document = comfort.sim_profile.read_text("utf-8")
    comfort.sim_profile.write_text(
        document.replace(
            '"max_silence_ms": 3000', '"max_silence_ms": 3000, "activity_min_peak": 2'
        ),
        "utf-8",
    )
    gated = run_sim(comfort)
    silent = run_case("agent_silent", tmp_path / "s")
    assert gated.value("end reason") == "max_silence"
    assert gated.value("agent segments") == "0"
    got, want = shape(gated), shape(silent.run)
    assert got["results"] == want["results"] == RESULT_KEYS
    assert got["statuses"] == want["statuses"]
    assert got["timing_keys"] - want["timing_keys"] == {"activity_min_peak"}
    assert got["turn_taking_keys"] - want["turn_taking_keys"] == {"activity_min_peak"}
    for key in ("turn_keys", "profile_keys", "outcome_keys"):
        assert got[key] == want[key], key
    results = results_by_key(gated.bundle())
    assert [describe(results[k]) for k in TIMING_KEYS] == [EDGE, EDGE, ("scored", 0), ("scored", 0)]
    # the same agent without the member is the old comfort-noise case: active from the first frame
    old = run_case("comfort_noise", tmp_path / "o")
    assert old.timing["first_agent_audio_ms"] == 0
    assert "activity_min_peak" not in old.timing


@pytest.mark.parametrize("dialect", ["pipecat-protobuf/1", "twilio-media-stream/1"])
def test_the_record_keys_are_the_same_with_and_without_the_member_for_each_dialect(
    tmp_path: Path, dialect: str
) -> None:
    """The threshold 9 is above the comfort noise of both wires (a value of 1; the byte 0xFE, which
    decodes to 8) and below the speech. The run with it and a run of the default scenario without
    it have the same result keys and stored key sets; the only new keys are the optional one."""
    with_member = field_run(tmp_path / "with", 9, dialect=dialect)
    assert with_member.code == 0, (with_member.out, with_member.err)
    plain = run_sim(write_inputs(tmp_path / "plain", sim_profile=profile_doc(dialect)))
    got, base = shape(with_member), shape(plain)
    assert got["results"] == base["results"] == RESULT_KEYS
    assert got["statuses"] == base["statuses"]
    assert got["profile_keys"] == base["profile_keys"]
    assert got["outcome_keys"] == base["outcome_keys"]
    assert got["timing_keys"] - base["timing_keys"] == {"activity_min_peak"}
    assert got["turn_taking_keys"] - base["turn_taking_keys"] == {"activity_min_peak"}
    assert base["timing_keys"] - got["timing_keys"] == set()
