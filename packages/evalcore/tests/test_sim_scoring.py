# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-22: scoring of a simulated call (ADR 0027 rules 9, 11, 12 and invariants I5 to I7; ADR 0023
amendment A2 and A3 and invariant IA2; SCOPE decisions D-a and D-c; BL-292).

Contract: docs/phase4/api-surface-batch-e.md section 6. Three families are pinned, built by the
product from synthetic calls (the pure builder of ``tracer_evalcore.simbundle``):

* the timing definitions (kind ``latency``, unit ``ms``, ``requires`` ``audio`` and ``turn_timing``,
  no pass rule); a value with a missing edge is an ``error`` result with the code
  ``timing_edge_missing``, never 0, never false and never a valid ``not_applicable``;
* the three agreement definitions of the per-role partition (agent-role export turns against the
  agent-channel speech-to-text turns), whose scorer version differs from the recorded-call one;
* the inert expected-outcome check: required by schema 0.0.1, never run, no result, no verdict.

Every case builds a bundle that ``check_run_bundle`` accepts (the builder calls it).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

import tracer_evalcore
from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    BAAZAR,
    DON,
    GAAV,
    GHAR,
    KAAM,
    MALA,
    NAMASKAR,
    PAANI,
    sentence,
)
from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    AGENT_0,
    AGENT_1,
    LINE_0,
    T0,
    build_bundle,
    export_read,
    log_frame,
    mod,
    ms,
    profile_file_doc,
    results_by_key,
    speech,
    stored_profile,
    timing_document,
)
from tracer_evalschema.bundle import check_run_bundle

TIMING_KEYS = (
    "sim_first_agent_audio_ms",
    "sim_agent_response_gap_median_ms",
    "sim_overlap_ms",
    "sim_barge_in_stop_ms",
    "sim_answered_caller_turns",
)
AGREEMENT_KEYS = (
    "stt_export_agent_agreement_mer",
    "stt_export_agent_flag_count",
    "stt_export_agent_flagged_token_share",
)
RECORDED_KEYS = (
    "stt_export_agreement_mer",
    "stt_export_flag_count",
    "stt_export_flagged_token_share",
)
BARGE_PROFILE = profile_file_doc(barge_ins=[{"turn_index": 1, "barge_in_after_ms": 100}])


def definitions(**options: Any) -> dict[str, Any]:
    built = build_bundle(**options)
    return {d.key: d for d in built.bundle.snapshot.metric_definitions}


def four_gap_timing() -> dict[str, Any]:
    """Four caller lines at 0, 1000, 2000 and 3000 ms (each ends at +200); the agent answers at
    300, 1400, 2500 and 3600 ms: the gaps are 100, 200, 300 and 400 ms."""
    return dict(
        timing_document(turns_ms=(0, 1000, 2000, 3000), agent_starts_ms=(300, 1400, 2500, 3600))
    )


def test_timing_definitions_have_no_pass_rule() -> None:
    """ADR 0027 rule 11 and invariant I6: shipped templates, kind ``latency`` and unit ``ms`` (the
    count is a deterministic ``count``), language independent, no pass rule, builtin refs."""
    defs = definitions(profile=stored_profile(BARGE_PROFILE), barge={1: 100})
    for key in TIMING_KEYS:
        definition = defs[key]
        assert definition.pass_rule is None, key
        assert definition.requires == ["audio", "turn_timing"], key
        assert definition.languages == "language_independent", key
        assert definition.script_sensitive is False, key
        assert definition.aggregation.kind == "per_conversation", key
        assert definition.implementation_ref == f"builtin:{key}", key
        assert definition.parameters == [], key
        assert definition.value_type == "numeric", key
        assert definition.version == 1, key
    for key in TIMING_KEYS[:4]:
        assert defs[key].kind == "latency", key
        assert defs[key].unit == "ms", key
    assert defs["sim_answered_caller_turns"].kind == "deterministic"
    assert defs["sim_answered_caller_turns"].unit == "count"
    for key in AGREEMENT_KEYS:
        assert defs[key].pass_rule is None, key
        assert defs[key].implementation_ref == f"builtin:{key}", key
    units = {defs[k].unit for k in (*TIMING_KEYS, *AGREEMENT_KEYS)}
    assert units <= {"ms", "count", "ratio"}
    assert "wer" not in units
    assert "cer" not in units
    assert mod("simscore").SIM_TIMING_KEYS == TIMING_KEYS
    assert mod("simscore").SIM_AGREEMENT_KEYS == AGREEMENT_KEYS


def test_sim_barge_in_stop_is_bound_only_when_the_profile_schedules_a_barge_in() -> None:
    """ADR 0027 Ambiguity 5: the set of result keys is fixed by the configuration."""
    plain = definitions()
    assert "sim_barge_in_stop_ms" not in plain
    assert set(plain) >= {k for k in TIMING_KEYS if k != "sim_barge_in_stop_ms"}
    with_barge = definitions(profile=stored_profile(BARGE_PROFILE), barge={1: 100})
    assert "sim_barge_in_stop_ms" in with_barge


def test_median_is_nearest_rank_with_n_recorded() -> None:
    """ADR 0027 rule 11: the median of the turns' gaps is the value at rank ceil(n / 2) (no mean,
    no interpolation) and N is recorded in the timing document of the result."""
    built = build_bundle(timing=four_gap_timing(), lines=4)
    result = results_by_key(built)["sim_agent_response_gap_median_ms"]
    document = result.metadata["tracer.timing"]
    assert [t["gap_ms"] for t in document["turns"]] == [100, 200, 300, 400]
    assert result.status == "scored"
    assert result.value == 200  # rank 2 of 4; a mean would say 250
    assert document["gap_n"] == 4
    assert document["gap_median_ms"] == 200
    assert document["answered_caller_turns"] == 4
    three = timing_document(turns_ms=(0, 1000, 2000), agent_starts_ms=(300, 1400, 2500))
    odd = results_by_key(build_bundle(timing=three, lines=3))["sim_agent_response_gap_median_ms"]
    assert odd.value == 200
    assert odd.metadata["tracer.timing"]["gap_n"] == 3
    one = results_by_key(
        build_bundle(timing=timing_document(turns_ms=(0,), agent_starts_ms=(300,)), lines=1)
    )
    assert one["sim_agent_response_gap_median_ms"].value == 100
    assert mod("simtiming").nearest_rank_median([400, 100, 300, 200]) == 200
    assert mod("simtiming").nearest_rank_median([]) is None


def test_timing_results_carry_the_run_document_and_the_quantum_and_dialect() -> None:
    built = build_bundle(timing=four_gap_timing(), lines=4)
    run_document = built.bundle.run.metadata["tracer.timing"]
    for key, result in results_by_key(built).items():
        if key not in TIMING_KEYS:
            continue
        assert result.metadata["tracer.timing"] == run_document, key
        assert run_document["dialect"] == "pipecat-protobuf/1"
        assert run_document["quantum_ms"] >= 20
        assert result.scorer.implementation_ref == f"builtin:{key}"
    firsts = results_by_key(built)["sim_first_agent_audio_ms"]
    assert firsts.value == 300
    assert firsts.passed is None


def test_a_timing_value_without_both_edges_is_an_error_with_the_edge_named() -> None:
    """SCOPE D-a, ADR 0027 rule 11, BL-292: the agent never spoke, so the value is absent; the
    result is ``error`` ``timing_edge_missing`` (0.0.1 cannot store a valid ``not_applicable``
    for it while ``audio`` and ``turn_timing`` are available), never 0 and never a pass."""
    silent = timing_document(agent_starts_ms=())
    results = results_by_key(build_bundle(timing=silent))
    first = results["sim_first_agent_audio_ms"]
    assert first.status == "error"
    assert first.value is None
    assert first.passed is None
    assert first.error.code == "timing_edge_missing"
    assert first.error.message == (
        "timing_edge_missing: first_agent_audio_ms has no value; missing edge: first active "
        "agent frame; caller turns 2"
    )
    gap = results["sim_agent_response_gap_median_ms"]
    assert gap.status == "error"
    assert gap.error.code == "timing_edge_missing"
    assert gap.error.message == (
        "timing_edge_missing: gap_median_ms has no value; missing edge: active agent frame "
        "after a caller turn; caller turns 2, gap_n 0"
    )
    # nothing was heard, so nothing overlapped: a measured 0 and a count of 0, not errors
    assert results["sim_overlap_ms"].status == "scored"
    assert results["sim_overlap_ms"].value == 0
    answered = results["sim_answered_caller_turns"]
    assert answered.status == "scored"
    assert answered.value == 0
    for result in results.values():
        assert result.status != "scored" or result.value is not None
        assert result.error is None or result.error.code == "timing_edge_missing"


def test_a_barge_in_the_agent_never_stopped_for_is_not_measured() -> None:
    profile = stored_profile(BARGE_PROFILE)
    quiet_agent = timing_document(barge={1: 100})  # the agent was silent when the barge-in began
    stop = results_by_key(build_bundle(timing=quiet_agent, profile=profile, barge={1: 100}))[
        "sim_barge_in_stop_ms"
    ]
    assert stop.status == "error"
    assert stop.error.code == "timing_edge_missing"
    assert "end of the interrupted agent activity" in stop.error.message
    # an agent talking over the barge-in, then stopping: a value (end of the run minus the start)
    talking = timing_document(turns_ms=(0, 1000), agent_runs=[(300, 5), (900, 10)], barge={1: 100})
    measured = results_by_key(build_bundle(timing=talking, profile=profile, barge={1: 100}))[
        "sim_barge_in_stop_ms"
    ]
    assert measured.status == "scored"
    assert measured.value == 100
    assert measured.metadata["tracer.timing"]["barge_in_n"] == 1


def test_an_unestablished_call_has_every_result_not_applicable_with_its_missing_input() -> None:
    """ADR 0027 rule 16: same keys as every other case; nothing is 0 or false or a failure."""
    results = results_by_key(build_bundle(established=False))
    assert (
        set(results)
        == set(results_by_key(build_bundle()))
        == {k for k in (*TIMING_KEYS, *AGREEMENT_KEYS) if k != "sim_barge_in_stop_ms"}
    )
    for key in (*TIMING_KEYS[:3], "sim_answered_caller_turns"):
        if key in results:
            assert results[key].status == "not_applicable", key
            assert results[key].na_reason == "missing_input", key
            assert results[key].missing_kinds == ["audio", "turn_timing"], key
            assert results[key].value is None, key
    for key in AGREEMENT_KEYS:
        assert results[key].status == "not_applicable"
        assert results[key].missing_kinds == ["transcript", "agent_transcript"]


def test_a_call_with_no_export_has_agreement_missing_the_agent_transcript_only() -> None:
    results = results_by_key(build_bundle())
    for key in AGREEMENT_KEYS:
        assert results[key].status == "not_applicable"
        assert results[key].na_reason == "missing_input"
        assert results[key].missing_kinds == ["agent_transcript"]
        assert results[key].value is None


def test_a_failed_transcription_makes_agreement_miss_the_transcript() -> None:
    results = results_by_key(build_bundle(export=export_read(), stt_failed=True))
    for key in AGREEMENT_KEYS:
        assert results[key].status == "not_applicable"
        assert results[key].missing_kinds == ["transcript"]


def test_agreement_partition_compares_agent_role_only() -> None:
    """ADR 0023 amendment A2 and invariant IA2: the agent-role export turns against the
    agent-channel speech-to-text turns. The export's caller lines are not compared: here they say
    something the agent channel never carries, and the result still says the agent lines agree."""
    lines = [
        ("assistant", AGENT_0),
        ("user", sentence(BAAZAR, BAAZAR, BAAZAR, BAAZAR, BAAZAR)),
        ("assistant", AGENT_1),
        ("user", sentence(DON, DON, DON)),
    ]
    built = build_bundle(export=export_read(lines))
    results = results_by_key(built)
    mer = results["stt_export_agent_agreement_mer"]
    assert mer.status == "scored"
    assert mer.value == 0.0
    assert results["stt_export_agent_flag_count"].value == 0
    assert results["stt_export_agent_flagged_token_share"].value == 0.0
    document = mer.metadata["tracer.flags"]
    agent_tokens = len(AGENT_0.split()) + len(AGENT_1.split())
    assert document["tokens"]["export"] == agent_tokens
    assert document["alignment"]["deletions"] == 0
    assert document["alignment"]["insertions"] == 0
    agent_seqs = {t.seq for t in built.export.turns if t.role == "agent"}
    assert {span["turn_seq"] for span in document["spans"]} <= agent_seqs
    # the same export where the AGENT lines differ is not in agreement
    other = [
        ("assistant", sentence(GHAR, KAAM)),
        ("user", LINE_0),
        ("assistant", sentence(MALA, PAANI)),
    ]
    differing = results_by_key(build_bundle(export=export_read(other)))
    assert differing["stt_export_agent_agreement_mer"].value > 0
    assert differing["stt_export_agent_flag_count"].value >= 1
    for key in AGREEMENT_KEYS:
        assert differing[key].scorer.implementation_ref == f"builtin:{key}"


def test_a_caller_role_turn_of_our_speech_to_text_is_not_compared_either() -> None:
    """The partition is on BOTH sides: a turn of the role ``caller`` among the turns of our
    speech-to-text (only a hand-built turn can be one) is left out of the comparison."""
    built = build_bundle(export=export_read())
    bundle = built.bundle
    stt_turns = [t for t in bundle.turns if t.source == "our_stt"]
    export_turns = [t for t in bundle.turns if t.role == "agent" and t.source != "our_stt"]
    assert stt_turns
    stray = stt_turns[0].model_copy(update={"role": "caller", "seq": 99, "text": "kitchen sink"})
    transcripts = {t.id: t for t in bundle.transcripts}
    stt_transcript = transcripts[stt_turns[0].transcript_id]
    export_transcript = transcripts[export_turns[0].transcript_id]
    results = mod("simscore").score_simulated_results(
        configuration=built.configuration,
        run=bundle.run,
        timing=bundle.run.metadata["tracer.timing"],
        export=export_transcript,
        export_turns=export_turns,
        stt=stt_transcript,
        stt_turns=[*stt_turns, stray],
        transcripts=bundle.transcripts,
        audio_artifact_id=built.artifact_id,
        created_at=bundle.run.created_at,
    )
    keys = {d.id: d.key for d in built.bundle.snapshot.metric_definitions}
    by_key = {keys[r.metric.id]: r for r in results}
    assert by_key["stt_export_agent_agreement_mer"].status == "scored"
    assert by_key["stt_export_agent_agreement_mer"].value == 0.0
    assert by_key["stt_export_agent_flag_count"].value == 0


def test_the_agreement_definitions_carry_their_units() -> None:
    """The mer and the flagged share are ratios, the flag count is a count (no unit mix-up)."""
    units = {
        d.key: d.unit
        for d in build_bundle(export=export_read()).bundle.snapshot.metric_definitions
        if d.key in AGREEMENT_KEYS
    }
    assert units == {
        "stt_export_agent_agreement_mer": "ratio",
        "stt_export_agent_flag_count": "count",
        "stt_export_agent_flagged_token_share": "ratio",
    }


def test_partition_changes_scorer_version() -> None:
    """ADR 0023 amendment A2: a partitioned result can never be read as a whole-call result. The
    keys differ from the recorded keys and the scorer version differs from the recorded one."""
    built = build_bundle(export=export_read())
    results = results_by_key(built)
    base = tracer_evalcore.__version__
    recorded = mod("registry").DEFAULT_REGISTRY
    assert set(RECORDED_KEYS).isdisjoint(results)
    for key, recorded_key in zip(AGREEMENT_KEYS, RECORDED_KEYS, strict=True):
        version = results[key].scorer.version
        assert version != base, key
        assert version == base + "+agent-partition.1", key
        assert version != recorded.get(f"builtin:{recorded_key}").version, key
    for key in TIMING_KEYS[:3]:
        assert results[key].scorer.version == base
    registry = mod("simscore").SIM_REGISTRY
    assert set(registry.refs()) == {f"builtin:{k}" for k in (*TIMING_KEYS, *AGREEMENT_KEYS)}
    assert set(registry.refs()).isdisjoint(recorded.refs())
    assert mod("simscore").PARTITION_VERSION_SUFFIX == "+agent-partition.1"


def test_a_non_mr_speech_to_text_turn_is_not_applicable_never_an_error() -> None:
    """SCOPE D-c, ADR 0023 amendment A3: in a simulated run the agent-channel turns are read, so a
    turn in another language is ``not_applicable`` ``unsupported_language`` (a recorded run says
    ``error``); an unobserved language is ``language_not_observed``."""
    other = results_by_key(build_bundle(export=export_read(), stt_language="en-IN"))
    for key in AGREEMENT_KEYS:
        assert other[key].status == "not_applicable", key
        assert other[key].na_reason == "unsupported_language", key
        assert other[key].error is None, key
    unobserved = results_by_key(build_bundle(export=export_read(), stt_language=None))
    for key in AGREEMENT_KEYS:
        assert unobserved[key].status == "not_applicable", key
        assert unobserved[key].na_reason == "language_not_observed", key


def test_flags_document_and_evidence_of_a_scored_agreement() -> None:
    built = build_bundle(export=export_read())
    mer = results_by_key(built)["stt_export_agent_agreement_mer"]
    document = mer.metadata["tracer.flags"]
    assert document["format"] == "tracer.flags/1"
    assert document["normaliser"] == "mr-Deva/1"
    assert document["stt"]["provider"] == "sarvam"
    assert document["stt"]["model"] == "saaras:v4"
    assert str(document["flag_min_confidence"]) == "0.5"
    transcript_ids = {t.id for t in built.bundle.transcripts}
    assert set(mer.transcript_ids) <= transcript_ids
    assert mer.passed is None
    # the document holds no text of the call
    assert all(w not in json.dumps(document) for w in (NAMASKAR, GAAV, PAANI))


def test_outcome_check_inert_and_no_verdict() -> None:
    """ADR 0027 rule 12 and invariant I7, finding F7: the snapshot holds a required expected-outcome
    check that is never run (the judge is recorded as not run), no result names it, and the bundle
    has no verdict. The outcome of the call is ``not_evaluated``."""
    built = build_bundle(export=export_read())
    snapshot = built.bundle.snapshot
    inert = [d for d in snapshot.metric_definitions if d.kind == "llm_judge"]
    assert [d.key for d in inert] == ["simulated_call_no_outcome_check"]
    check = inert[0]
    assert (check.judge_model_ref.provider, check.judge_model_ref.model) == ("none", "not_run")
    required = [item for item in snapshot.rubric.items if item.required]
    binding_ids = {b.id for b in snapshot.metric_bindings if b.metric.id == check.id}
    assert len(required) == 1
    assert required[0].binding.id in binding_ids
    for result in built.bundle.results:
        assert result.metric.id != check.id
        assert result.status != "scored" or result.passed is None
    assert built.bundle.verdict is None
    assert built.bundle.run.metadata["tracer.sim_outcome"]["outcome"] == "not_evaluated"
    # every other definition is optional in the rubric
    assert (
        sum(1 for item in snapshot.rubric.items if not item.required)
        == len(snapshot.rubric.items) - 1
    )


def test_the_snapshot_holds_the_sim_profile_and_its_digest_covers_it() -> None:
    """ADR 0027 rule 1: ``tracer.sim_profile/1`` lives in ``ConfigSnapshot.metadata`` and the
    content digest covers it, so a changed cap is a changed digest (and a doctored one fails)."""
    built = build_bundle()
    snapshot = built.bundle.snapshot
    assert snapshot.metadata["tracer.sim_profile"] == stored_profile()
    assert snapshot.metadata_flags == []
    document = snapshot.model_dump(mode="json", by_alias=True)
    document["metadata"]["tracer.sim_profile"]["limits"]["max_call_seconds"] = 1
    from tracer_evalschema.errors import DigestMismatch
    from tracer_evalschema.snapshot import ConfigSnapshot

    with pytest.raises(DigestMismatch):
        ConfigSnapshot.model_validate(document)
    again = copy.deepcopy(snapshot.model_dump(mode="json", by_alias=True))
    assert ConfigSnapshot.model_validate(again).content_digest == snapshot.content_digest


def test_the_bundle_re_validates_as_written() -> None:
    """A bundle that was built is exactly what ``check_run_bundle`` accepts after a JSON round
    trip (the store does the same)."""
    from tracer_evalschema.bundle import RunBundle

    built = build_bundle(export=export_read())
    restored = RunBundle.model_validate_json(built.bundle.model_dump_json(by_alias=True))
    check_run_bundle(restored)
    assert restored == built.bundle


def test_log_frames_used_by_the_helpers_are_real_recorder_frames() -> None:
    """Guards the helpers the other tests build calls with."""
    frame = log_frame(T0 + ms(5), speech())
    assert frame.sample_rate_hz == 16000
    assert frame.codec == "pcm_s16le"
    assert len(frame.payload) == 640
