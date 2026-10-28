# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018
"""P4-29: the T-DEG matrix of a simulated run (the project's core rule, "never break if absent").

Ten cases of one configuration (a two-line scripted call over ``pipecat-protobuf/1`` against the
in-process fake agent, on the virtual clock, no socket): setup failed, an agent that never speaks,
a comfort-noise agent, no export, an export, a failed speech-to-text, a kill switch in the middle of
the call, ``max_duration``, ``max_silence`` and a spend cap. Every case gives the same result keys
and the same stored documents; only statuses, values and reasons differ; a missing input is
``not_applicable`` with its missing kinds and never a value, ``0`` or a failure (ADR 0004); a timing
value whose edge is missing is the ``error`` ``timing_edge_missing`` and never ``0`` (ADR 0027 rule
11, BL-292); and nothing is ever ``passed`` or ``failed``.

Contract: docs/phase4/api-surface-batch-f.md section 5. ADR 0027 rule 16, ADR 0026 rules 9, 17, 19.
The case helpers are in ``cli_p4f_support``. Numbers are derived by hand in the docstrings (virtual
clock: a 7-character fake line is 14 frames, 0 to 280 ms; the fake agent speaks 10 frames from 400).
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import pytest

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    LINE_0,
    run_sim,
    write_inputs,
)
from cli_p4f_support import (  # type: ignore[import-not-found,unused-ignore]
    AGREEMENT_KEYS,
    CASES,
    RESULT_KEYS,
    TIMING_KEYS,
    CaseOutcome,
    FakeSignals,
    describe,
    results_by_key,
    run_case,
)

NA_TIMING = ("not_applicable", ("missing_input", ("audio", "turn_timing")))
NA_BOTH = ("not_applicable", ("missing_input", ("transcript", "agent_transcript")))
NA_EXPORT = ("not_applicable", ("missing_input", ("agent_transcript",)))
NA_TRANSCRIPT = ("not_applicable", ("missing_input", ("transcript",)))
EDGE = ("error", "timing_edge_missing")
INPUTS_FULL = ["audio", "agent_audio", "transcript", "turn_timing"]


def timing(first: Any, gap: Any, overlap: Any, answered: Any) -> list[Any]:
    return [first, gap, overlap, answered]


def scored(*values: Any) -> list[Any]:
    return [("scored", v) for v in values]


#: Per case: exit code, end reason, established, inputs, transcript sources, warning codes, usage
#: stages, the four timing results and the three agreement results (what ADR 0027 rule 16 says).
EXPECT: dict[str, dict[str, Any]] = {
    "setup_failed": {
        "code": 5, "end": "setup_failed", "established": False, "inputs": [],
        "sources": [], "warnings": [], "usage": [],
        "timing": timing(NA_TIMING, NA_TIMING, NA_TIMING, NA_TIMING),
        "agreement": [NA_BOTH] * 3,
    },
    "agent_silent": {
        # nothing was heard: the two values that need an agent edge are errors, not 0; the overlap
        # and the count are measured (0 ms overlapped, 0 turns answered)
        "code": 2, "end": "max_silence", "established": True,
        "inputs": ["audio", "agent_audio", "turn_timing"],
        "sources": ["script"], "warnings": [], "usage": [],
        "timing": timing(EDGE, EDGE, ("scored", 0), ("scored", 0)),
        "agreement": [NA_BOTH] * 3,
    },
    "comfort_noise": {
        # activity is any non-zero sample (ADR 0026 rule 10): a comfort-noise agent is active from
        # the first frame, so first audio 0 and gap 0 are measured, 14 frames (280 ms) of the line
        # overlap it, and the call ends on max_silence, never a hang
        "code": 0, "end": "max_silence", "established": True, "inputs": INPUTS_FULL,
        "sources": ["script", "our_stt"], "warnings": [], "usage": [],
        "timing": timing(*scored(0, 0, 280, 1)),
        "agreement": [NA_EXPORT] * 3,
    },
    "no_export": {
        # first audio 400; line 0 ends at edge 280 and the reply starts at 400 (gap 120); the
        # frame at 1160 is the first active one at or after the second edge (gap 0); median of
        # [0, 120] by nearest rank is 0
        "code": 0, "end": "script_complete", "established": True, "inputs": INPUTS_FULL,
        "sources": ["script", "our_stt"], "warnings": [], "usage": [],
        "timing": timing(*scored(400, 0, 60, 2)),
        "agreement": [NA_EXPORT] * 3,
    },
    "export_present": {
        "code": 0, "end": "script_complete", "established": True,
        "inputs": ["audio", "agent_audio", "transcript", "agent_transcript", "turn_timing"],
        "sources": ["script", "our_stt", "agent_export"], "warnings": [], "usage": [],
        "timing": timing(*scored(400, 0, 60, 2)),
        "agreement": scored(0.0, 0, 0.0),
    },
    "stt_failure": {
        # no our_stt transcript: the agreement needs "transcript" and says so; the timing, which
        # needs no transcript, is still scored; exit 3
        "code": 3, "end": "script_complete", "established": True,
        "inputs": ["audio", "agent_audio", "agent_transcript", "turn_timing"],
        "sources": ["script", "agent_export"], "warnings": ["stt_failed"], "usage": [],
        "timing": timing(*scored(400, 0, 60, 2)),
        "agreement": [NA_TRANSCRIPT] * 3,
    },
    "kill_switch": {
        "code": 0, "end": "kill_switch", "established": True, "inputs": INPUTS_FULL,
        "sources": ["script", "our_stt"], "warnings": [],
        "usage": ["sim_caller_tts", "sim_caller_tts", "stt"],
        "timing": timing(*scored(400, 300, 0, 1)),
        "agreement": [NA_EXPORT] * 3,
    },
    "max_duration": {
        "code": 0, "end": "max_duration", "established": True, "inputs": INPUTS_FULL,
        "sources": ["script", "our_stt"], "warnings": [], "usage": [],
        "timing": timing(*scored(300, 20, 0, 1)),
        "agreement": [NA_EXPORT] * 3,
    },
    "max_silence": {
        "code": 0, "end": "max_silence", "established": True, "inputs": INPUTS_FULL,
        "sources": ["script", "our_stt"], "warnings": [], "usage": [],
        "timing": timing(*scored(300, 20, 0, 1)),
        "agreement": [NA_EXPORT] * 3,
    },
    "spend_cap": {
        "code": 0, "end": "spend_cap", "established": True, "inputs": INPUTS_FULL,
        "sources": ["script", "our_stt"], "warnings": [],
        "usage": ["sim_caller_llm", "sim_caller_tts", "stt"],
        "timing": timing(*scored(400, 300, 0, 1)),
        "agreement": [NA_EXPORT] * 3,
    },
}  # fmt: skip


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, CaseOutcome]:
    root = tmp_path_factory.mktemp("tdeg")
    return {name: run_case(name, root / name) for name in CASES}


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


def test_the_matrix_has_the_ten_cases_of_the_scope_row() -> None:
    assert tuple(EXPECT) == CASES
    assert len(CASES) == 10


@pytest.mark.parametrize("name", CASES)
def test_every_case_is_exactly_what_adr_0027_rule_16_says(
    outcomes: dict[str, CaseOutcome], name: str
) -> None:
    """The whole row of one case in one comparison: a mismatch names the case and the field."""
    got = summary(outcomes[name])
    want = EXPECT[name]
    for field in want:
        assert got[field] == want[field], (name, field)


def test_tdeg_sim_setup_failed_all_not_applicable_exit_5(
    outcomes: dict[str, CaseOutcome],
) -> None:
    """ADR 0026 rule 19, ADR 0027 rule 16: a call that was never established is stored (the attempt
    happened), exit 5, every result ``not_applicable`` with its missing kinds, no recording, no
    transcript and no turn, and the report and the summary say so."""
    case = outcomes["setup_failed"]
    run = case.run
    assert run.code == 5
    assert run.err == ""
    assert run.value("result") == "call not established"
    assert run.value("end reason") == "setup_failed"
    assert run.value("recording") == "none (the call was not established)"
    assert run.value("first agent audio") == "not measured (missing input audio, turn_timing)"
    assert run.value("agreement") == "not scored (missing input transcript, agent_transcript)"
    bundle = case.bundle
    assert len(bundle.results) == 7
    for result in bundle.results:
        assert result.status == "not_applicable"
        assert result.na_reason == "missing_input"
        assert result.value is None
        assert result.passed is None
        assert result.error is None
        assert result.missing_kinds
    assert bundle.artifacts == []
    assert bundle.transcripts == []
    assert bundle.turns == []
    assert bundle.run.available_inputs == []
    assert case.outcome["call_established"] is False
    assert case.outcome["connected_ms"] is None
    assert case.outcome["counts"]["frames_sent"] == 0
    assert case.timing["first_agent_audio_ms"] is None
    assert case.timing["overlap_ms"] is None
    assert run.names() == [
        "call.lock",
        "report-20261027T120000Z.html",
        "sim_call_log.jsonl",
        "tracer.sqlite",
    ]
    assert "no recording: the call was not established" in run.report()


def test_tdeg_sim_a_kill_switch_before_the_connection_is_a_call_that_never_happened(
    tmp_path: Path,
) -> None:
    """The other way to end a call before it exists (ADR 0026 rules 9 and 17): a stop requested before
    the connection is made. The connector is never called, the end reason is ``kill_switch``, nothing
    was established (exit 5) and every result is ``not_applicable`` for its missing inputs, exactly as
    for a refused connection."""
    files = write_inputs(tmp_path)
    connected: list[str] = []

    async def connect(request: Any) -> Any:
        connected.append("connect")
        raise AssertionError

    class Early(FakeSignals):  # type: ignore[misc,unused-ignore]
        def install(self, on_signal: Any) -> Any:
            on_signal()  # the signal arrived before the call loop ran
            return super().install(on_signal)

    run = run_sim(files, target=True, world_kw={"signals": Early(), "connect": connect})
    bundle = run.bundle()
    assert connected == []
    assert run.code == 5
    assert run.value("end reason") == "kill_switch"
    assert run.value("result") == "call not established"
    assert bundle.run.metadata["tracer.sim_outcome"]["end_reason"] == "kill_switch"
    assert bundle.artifacts == [] and bundle.transcripts == [] and bundle.turns == []
    for result in bundle.results:
        assert (result.status, result.na_reason) == ("not_applicable", "missing_input")
        assert result.value is None


def test_tdeg_sim_agent_silent_timing_not_measured(outcomes: dict[str, CaseOutcome]) -> None:
    """SCOPE P4-29 (D-a, BL-292), ADR 0027 rule 11: an agent that never speaks has no agent edge, so
    the values that need one have no value and are ``error`` ``timing_edge_missing`` (never 0,
    never false, never a ``not_applicable``, which schema 0.0.1 cannot store here); the summary
    says "not measured" and names the edge; the exit code is 2."""
    case = outcomes["agent_silent"]
    results = case.results
    for key in ("sim_first_agent_audio_ms", "sim_agent_response_gap_median_ms"):
        result = results[key]
        assert result.status == "error", key
        assert result.error.code == "timing_edge_missing", key
        assert result.value is None, key
        assert result.passed is None, key
        assert result.na_reason is None, key
        assert result.error.message, key
    assert case.timing["first_agent_audio_ms"] is None
    assert case.timing["gap_median_ms"] is None
    assert case.timing["gap_n"] == 0
    assert case.run.code == 2
    assert case.run.value("end reason") == "max_silence"
    assert case.run.value("first agent audio") == (
        "not measured (missing edge: first active agent frame)"
    )
    assert case.run.value("agent segments") == "0"
    # no agent speech: no our_stt transcript and no speech-to-text request, so nothing failed
    assert [t.source for t in case.bundle.transcripts] == ["script"]
    assert case.bundle.run.ingest_warnings == []


def test_tdeg_sim_no_export_agreement_missing_input(outcomes: dict[str, CaseOutcome]) -> None:
    """Without an export the agreement results are ``missing_input`` ``agent_transcript`` (never 0
    and never a failure); the timing is scored; the run is complete (exit 0)."""
    case = outcomes["no_export"]
    for key in AGREEMENT_KEYS:
        result = case.results[key]
        assert (result.status, result.na_reason) == ("not_applicable", "missing_input"), key
        assert list(result.missing_kinds) == ["agent_transcript"], key
        assert result.value is None
        assert result.passed is None
    assert "agent_transcript" not in case.bundle.run.available_inputs
    assert all(t.source != "agent_export" for t in case.bundle.transcripts)
    assert case.run.code == 0
    assert case.run.value("agreement") == "not scored (missing input agent_transcript)"


def test_tdeg_sim_export_present_agreement_scored(outcomes: dict[str, CaseOutcome]) -> None:
    """With an export (declared ``mr``) the per-role agreement is scored: the agent turns of the
    export against the agent channel's speech-to-text; the caller's lines are not compared. The
    two texts are equal here, so the distance is 0.0 over 2 reference tokens and nothing is flagged."""
    case = outcomes["export_present"]
    results = case.results
    assert results["stt_export_agent_agreement_mer"].status == "scored"
    assert results["stt_export_agent_agreement_mer"].value == 0.0
    assert results["stt_export_agent_flag_count"].value == 0
    assert results["stt_export_agent_flagged_token_share"].value == 0.0
    for key in AGREEMENT_KEYS:
        scorer = results[key].scorer
        assert scorer is not None
        assert scorer.version.endswith("+agent-partition.1"), key
    assert "agent_transcript" in case.bundle.run.available_inputs
    export = [t for t in case.bundle.transcripts if t.source == "agent_export"]
    assert len(export) == 1
    roles = [x.role for x in case.bundle.turns if x.transcript_id == export[0].id]
    assert roles == ["agent", "caller", "agent"]  # the caller line is in the export, not compared
    assert case.run.code == 0
    assert case.run.value("agreement distance (MER)").startswith("0.0 (0 of 2;")


def test_tdeg_sim_export_without_agent_turns_agreement_missing_input(tmp_path: Path) -> None:
    """The F6 trap (ADR 0027 rule 8): an export that holds only the caller's lines is not an agent
    record. The transcript is stored, ``agent_transcript`` is NOT listed as an available input, the
    agreement results are ``missing_input`` ``agent_transcript`` (never scored against nothing, never
    0), and the run is complete (exit 0). The counterpart of ``export_present``."""
    files = write_inputs(tmp_path)
    files.export.write_text(
        "call_id=5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11\n---\n"
        f"[2026-10-25T06:00:01.000+00:00] user: {LINE_0}\n",
        "utf-8",
    )
    run = run_sim(files, add=["--agent-transcript", str(files.export), "--declared-language", "mr"])
    bundle = run.bundle()
    assert run.code == 0, (run.out, run.err)
    assert "agent_transcript" not in bundle.run.available_inputs
    assert [t.source for t in bundle.transcripts] == ["script", "our_stt", "agent_export"]
    results = results_by_key(bundle)
    for key in AGREEMENT_KEYS:
        assert describe(results[key]) == NA_EXPORT, key
    assert run.value("agreement") == "not scored (missing input agent_transcript)"


def test_tdeg_sim_stt_failure_missing_input_transcript(outcomes: dict[str, CaseOutcome]) -> None:
    """The speech-to-text of the agent channel failed: no ``our_stt`` transcript, so the agreement
    needs ``transcript`` (and only that: the export is there), a Run ``IngestWarning`` says why
    (the class of the failure, never a value), the timing is unaffected, the exit code is 3."""
    case = outcomes["stt_failure"]
    for key in AGREEMENT_KEYS:
        result = case.results[key]
        assert (result.status, result.na_reason) == ("not_applicable", "missing_input"), key
        assert list(result.missing_kinds) == ["transcript"], key
        assert result.value is None
    (warning,) = case.bundle.run.ingest_warnings
    assert warning.code == "stt_failed"
    assert warning.path == "/stt"
    assert warning.message.startswith("Stt")
    assert not any(t.source == "our_stt" for t in case.bundle.transcripts)
    assert case.run.code == 3
    assert case.run.value("ingest warnings") == "1"
    assert case.run.value("agreement") == "not scored (missing input transcript)"
    assert case.results["sim_first_agent_audio_ms"].value == 400


def test_tdeg_sim_comfort_noise_agent_is_max_silence_not_a_hang(
    outcomes: dict[str, CaseOutcome],
) -> None:
    """ADR 0026 rule 10: an agent that streams comfort noise never looks silent, so the call ends on
    ``max_silence`` with what was recorded. The timing values are measured with the definition of
    activity (any non-zero sample); the outcome names the end reason; the clock shows the wait was
    bounded by ``max_silence_ms`` (3000) from the send instant of the line's last frame (260 ms),
    so the call lasted 3260 ms, during which 164 comfort frames (one every 20 ms, at 0 .. 3260)
    arrived: bounded, not a hang."""
    case = outcomes["comfort_noise"]
    assert case.outcome["end_reason"] == "max_silence"
    assert case.outcome["connected_ms"] == 3260
    assert case.outcome["counts"]["frames_received"] == 164
    assert case.run.code == 0


def test_tdeg_sim_kill_switch_keeps_partial_recording_and_usage(
    outcomes: dict[str, CaseOutcome],
) -> None:
    """ADR 0026 rule 17: the first signal in the middle of the call hangs up gracefully, the call
    ends ``kill_switch``, and the run is stored with what exists: the partial recording (one line
    of 100 ms and the first reply of 200 ms, not the second line), the usage of every request that
    was answered (two TTS requests and the one transcription), the cache entries and the report."""
    case = outcomes["kill_switch"]
    assert case.outcome["end_reason"] == "kill_switch"
    assert case.outcome["counts"]["signals_received"] == 1
    assert case.outcome["counts"]["caller_turns_started"] == 1
    assert case.outcome["counts"]["tts_requests"] == 2
    stages = [u.stage for u in case.bundle.usage_records]
    assert stages == ["sim_caller_tts", "sim_caller_tts", "stt"]
    assert case.sim is not None
    assert len(case.sim.bodies("/text-to-speech")) == 2
    assert case.sim.paths.count("/speech-to-text") == 1
    (wav_path,) = list(case.files.out.glob("recording-*.wav"))
    with wave.open(str(wav_path)) as reader:
        assert (reader.getnchannels(), reader.getframerate()) == (2, 16000)
        data = reader.readframes(reader.getnframes())
        length_ms = reader.getnframes() // 16
    samples = [
        int.from_bytes(data[i : i + 2], "little", signed=True) for i in range(0, len(data), 2)
    ]
    left, right = samples[0::2], samples[1::2]
    assert length_ms >= 600
    assert sum(1 for x in left if x) // 16 == 100  # one line of 5 frames was sent
    assert sum(1 for x in right if x) // 16 == 200  # the first reply (10 frames), heard in full
    assert list(case.files.out.glob("report-*.html"))
    assert len(list((case.files.out / "tts-cache").iterdir())) == 2
    assert case.run.value("tts usage") == "2 requests, 14 characters"
    assert case.run.value("stt usage") == "1 requests, 0.2 audio seconds"
    assert case.run.code == 0


@pytest.mark.parametrize(
    ("name", "end", "cap"),
    [
        ("max_duration", "max_duration", None),
        ("max_silence", "max_silence", None),
        ("spend_cap", "spend_cap", "llm_tokens"),
    ],
)
def test_tdeg_sim_a_cap_or_a_wait_that_ends_the_call_is_an_end_reason_not_a_failure(
    outcomes: dict[str, CaseOutcome], name: str, end: str, cap: str | None
) -> None:
    """``max_duration``, ``max_silence`` and a spend cap are end reasons with what was recorded:
    the run is stored, scored and exit 0; the outcome names the reason and, for a cap, which cap."""
    case = outcomes[name]
    assert case.outcome["end_reason"] == end
    assert case.outcome["cap_reached"] == cap
    assert case.run.code == 0
    assert case.outcome["call_established"] is True
    assert case.run.value("end reason") == end
    if cap is not None:
        assert case.run.value("cap reached") == cap
    else:
        assert "cap reached" not in case.run.out


def test_tdeg_sim_schema_identical_across_cases(outcomes: dict[str, CaseOutcome]) -> None:
    """ADR 0027 rule 16: every case of one configuration gives the same result keys in the same
    order, the same metric definitions (same digest), the same metadata documents, each valid against
    the same JSON Schema, and a bundle that passes ``check_run_bundle``; only statuses, values and
    reasons differ. A result is never ``passed`` or ``failed``; a ``not_applicable`` result has no
    value and its missing kinds; an ``error`` has a code and no value; a timing value with no edge
    in the stored timing document has no value in its result either."""
    import jsonschema  # type: ignore[import-untyped,unused-ignore]

    from tracer_evalcore import simoutcome, simprofile, simtiming
    from tracer_evalschema.bundle import check_run_bundle

    definitions: dict[str, dict[str, Any]] | None = None
    for name in CASES:
        case = outcomes[name]
        bundle = case.bundle
        check_run_bundle(bundle)
        assert tuple(case.results) == RESULT_KEYS, name
        digests = {
            d.key: {
                k: v
                for k, v in d.model_dump(mode="json").items()
                if k not in ("id", "content_digest")
            }
            for d in bundle.snapshot.metric_definitions
        }
        keys = {d.key for d in bundle.snapshot.metric_definitions}
        assert keys == {*RESULT_KEYS, "simulated_call_no_outcome_check"}, name
        if definitions is None:
            definitions = digests
        assert digests == definitions, name
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
                assert result.na_reason == "missing_input", (name, key)
                assert result.missing_kinds, (name, key)
            elif result.status == "error":
                assert result.value is None and result.na_reason is None, (name, key)
                assert result.error.code == "timing_edge_missing", (name, key)
                assert key in TIMING_KEYS[:2], (name, key)
            else:
                assert result.value is not None, (name, key)
        document = case.timing
        for key, field in (
            ("sim_first_agent_audio_ms", "first_agent_audio_ms"),
            ("sim_agent_response_gap_median_ms", "gap_median_ms"),
        ):
            result = case.results[key]
            if document[field] is None:
                assert result.status in ("error", "not_applicable"), (name, key)
                assert result.value is None, (name, key)
            else:
                assert result.status == "scored" and result.value == document[field], (name, key)
    # which record kinds a case lacks, and why: the kinds that differ are exactly these
    kinds = {
        name: {f for f in ("artifacts", "transcripts", "turns", "usage_records")
               if getattr(outcomes[name].bundle, f)}
        for name in CASES
    }  # fmt: skip
    assert kinds["setup_failed"] == set()  # nothing was recorded or heard
    assert "usage_records" in kinds["kill_switch"] and "usage_records" in kinds["spend_cap"]
    for name in ("no_export", "export_present", "max_duration", "max_silence", "comfort_noise"):
        assert kinds[name] == {"artifacts", "transcripts", "turns"}, name
    assert kinds["agent_silent"] == {"artifacts", "transcripts", "turns"}
    for name in CASES:
        assert len(outcomes[name].bundle.results) == 7, name  # fixed by the configuration
