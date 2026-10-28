# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018
"""BL-338 (batch G): two more rows of the T-DEG matrix of a simulated run (P4-29, ADR 0027 rule 16).

Contract: docs/phase4/api-surface-batch-g.md section 8. The ten cases of ``test_tdeg_sim`` are
untouched; these are the eleventh and twelfth: the caller's brain fails at its first request (an
established call of zero caller turns) and after one answered turn. Like every case they give the
same result keys, the same metric definitions, the same stored documents, each valid against the
same JSON Schema, and a bundle that passes ``check_run_bundle``; only statuses, values and reasons
differ; a missing input is ``not_applicable`` with its kinds and never 0, false or a failure; a timing
value without an edge is the ``error`` ``timing_edge_missing``. The exit code is 2 in both, and in the
second it is the end reason alone that makes it so (no result is an error).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli_p4e_support import LINE_0  # type: ignore[import-not-found,unused-ignore]
from cli_p4f_support import (  # type: ignore[import-not-found,unused-ignore]
    AGREEMENT_KEYS,
    RESULT_KEYS,
    TIMING_KEYS,
    CaseOutcome,
    describe,
    run_case,
)
from cli_p4g_support import ChatSim, llm_args, llm_files  # type: ignore[import-not-found,unused-ignore]

NA_BOTH = ("not_applicable", ("missing_input", ("transcript", "agent_transcript")))
NA_EXPORT = ("not_applicable", ("missing_input", ("agent_transcript",)))
NA_TURN_TIMING = ("not_applicable", ("missing_input", ("turn_timing",)))

#: exit code, end reason, inputs, transcript sources, usage stages, the four timing results and the
#: three agreement results, per new case (derived by hand like the ten of ``test_tdeg_sim``)
EXPECT: dict[str, dict[str, Any]] = {
    "brain_failed_first": {
        # the call is up and the brain fails before the caller says a word: no caller line, the
        # hang-up is at once so the agent is never heard, nothing was paid. No Turn has a start
        # and an end, so ``turn_timing`` is not an available input (ADR 0027 rule 8) and the four
        # timing results are ``not_applicable`` for it, never 0. No result is an error, so the
        # exit code 2 is the end reason's alone (BL-338: this call used to exit 0)
        "code": 2, "end": "brain_failed", "established": True,
        "inputs": ["audio", "agent_audio"],
        "sources": [], "warnings": [], "usage": [],
        "timing": [NA_TURN_TIMING] * 4,
        "agreement": [NA_BOTH] * 3,
    },
    "brain_failed_later": {
        # line 0 is paid for (the brain request and the TTS), sent at 0 to 80 (ends at edge 100),
        # the agent speaks 400 to 580 (first audio 400, gap 300, no overlap, one answered turn),
        # the segment is transcribed in the call (one STT request), then the second brain request
        # fails at 880. Everything that exists is scored; the exit code is 2 from the end reason
        "code": 2, "end": "brain_failed", "established": True,
        "inputs": ["audio", "agent_audio", "transcript", "turn_timing"],
        "sources": ["script", "our_stt"], "warnings": [],
        "usage": ["sim_caller_llm", "sim_caller_tts", "stt"],
        "timing": [("scored", 400), ("scored", 300), ("scored", 0), ("scored", 1)],
        "agreement": [NA_EXPORT] * 3,
    },
}  # fmt: skip


def run_new_case(name: str, root: Path) -> CaseOutcome:
    from cli_p4e_support import real_env, run_sim  # type: ignore[import-not-found,unused-ignore]

    files = llm_files(root)
    good = 0 if name == "brain_failed_first" else 1
    sim = ChatSim("500", good=good, chat=[LINE_0] if good else ())
    run = run_sim(files, args=llm_args(files), environ=real_env(), transport=sim)
    return CaseOutcome(name, run, files, sim)


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, CaseOutcome]:
    root = tmp_path_factory.mktemp("tdeg-brain")
    cases = {name: run_new_case(name, root / name) for name in EXPECT}
    cases["no_export"] = run_case("no_export", root / "no_export")
    return cases


def summary(case: CaseOutcome) -> dict[str, Any]:
    bundle = case.bundle
    results = case.results
    return {
        "code": case.run.code,
        "end": case.outcome["end_reason"],
        "established": case.outcome["call_established"],
        "inputs": list(bundle.run.available_inputs),
        "sources": [t.source for t in bundle.transcripts],
        "warnings": [w.code for w in bundle.run.ingest_warnings],
        "usage": [u.stage for u in bundle.usage_records],
        "timing": [describe(results[k]) for k in TIMING_KEYS],
        "agreement": [describe(results[k]) for k in AGREEMENT_KEYS],
    }


@pytest.mark.parametrize("name", list(EXPECT))
def test_tdeg_sim_brain_failed_every_case_is_exactly_what_adr_0027_rule_16_says(
    outcomes: dict[str, CaseOutcome], name: str
) -> None:
    got = summary(outcomes[name])
    for field, want in EXPECT[name].items():
        assert got[field] == want, (name, field)


def test_tdeg_sim_brain_failed_schema_identical_to_the_other_cases(
    outcomes: dict[str, CaseOutcome],
) -> None:
    """The same result keys in the same order, the same metric definitions, the same metadata
    documents, valid against the same schemas, a bundle that passes ``check_run_bundle``, no
    verdict anywhere, and a ``not_applicable`` or ``error`` result that is typed like the others."""
    import jsonschema  # type: ignore[import-untyped,unused-ignore]

    from tracer_evalcore import simoutcome, simprofile, simtiming
    from tracer_evalschema.bundle import check_run_bundle

    def definitions_of(case: CaseOutcome) -> dict[str, dict[str, Any]]:
        return {
            d.key: {
                k: v
                for k, v in d.model_dump(mode="json").items()
                if k not in ("id", "content_digest")
            }
            for d in case.bundle.snapshot.metric_definitions
        }

    baseline = definitions_of(outcomes["no_export"])
    for name in EXPECT:
        case = outcomes[name]
        bundle = case.bundle
        check_run_bundle(bundle)
        assert case.outcome["end_reason"] == "brain_failed", name
        assert tuple(case.results) == RESULT_KEYS, name
        assert definitions_of(case) == baseline, name
        assert len(bundle.results) == 7, name
        assert bundle.run.mode == "simulated", name
        assert sorted(bundle.run.metadata) == ["tracer.sim_outcome", "tracer.timing"], name
        assert sorted(bundle.snapshot.metadata) == ["tracer.sim_profile"], name
        jsonschema.validate(
            bundle.run.metadata["tracer.sim_outcome"], simoutcome.SIM_OUTCOME_JSON_SCHEMA
        )
        jsonschema.validate(
            bundle.snapshot.metadata["tracer.sim_profile"], simprofile.SIM_PROFILE_JSON_SCHEMA
        )
        jsonschema.validate(bundle.run.metadata["tracer.timing"], simtiming.TIMING_JSON_SCHEMA)
        assert case.outcome["outcome"] == "not_evaluated", name
        for key, result in case.results.items():
            assert result.status in ("scored", "not_applicable", "error"), (name, key)
            assert result.passed is None, (name, key)
            assert not isinstance(result.value, bool), (name, key)
            if result.status == "not_applicable":
                assert result.value is None and result.error is None, (name, key)
                assert result.na_reason == "missing_input" and result.missing_kinds, (name, key)
            elif result.status == "error":
                assert result.value is None and result.na_reason is None, (name, key)
                assert result.error.code == "timing_edge_missing", (name, key)
            else:
                assert result.value is not None, (name, key)


@pytest.mark.parametrize("name", list(EXPECT))
def test_tdeg_sim_brain_failed_has_no_error_result_so_the_exit_code_is_the_end_reasons(
    outcomes: dict[str, CaseOutcome], name: str
) -> None:
    case = outcomes[name]
    assert all(r.status != "error" for r in case.bundle.results), name
    assert case.run.code == 2
    assert case.run.value("end reason") == "brain_failed"


def test_tdeg_sim_brain_failed_first_is_an_established_call_not_a_setup_failure(
    outcomes: dict[str, CaseOutcome],
) -> None:
    """A failed brain is not a failed connection: the recording exists (the call was up), the
    attempt is stored, and the exit code is 2 (not 5, which means the call was not established)."""
    case = outcomes["brain_failed_first"]
    assert case.outcome["call_established"] is True
    assert case.outcome["connected_ms"] is not None
    assert case.bundle.artifacts
    assert case.run.code == 2
    assert case.run.value("result") == "call established"
    assert case.run.value("recording") != "none (the call was not established)"
    assert case.outcome["counts"]["caller_turns_started"] == 0
