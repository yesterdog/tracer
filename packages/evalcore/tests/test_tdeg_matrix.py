# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-27, the tier-degradation matrix of the recorded-call scoring case.

The case has one observed tier, T0 (SCOPE F7: an agent export has no timing, and the recording is
only audio). What degrades is the set of inputs, not the tier: the same configuration (one
``RecordedConfiguration``) is run over six input situations and the result schema must be
identical, with ``not_applicable`` (never a failure, never a zero) for every metric whose
``requires[]`` is missing, ``not_applicable`` ``language_not_observed`` for an export with no
declared language, and a scorer ``error`` (never a score) for a speech-to-text chunk in another
language (ADR 0023 rule 14, Decisions D7). Contract: docs/phase3/api-surface-batch-f.md section 4.

The scenario is the small hand-computed base call of the batch D tests (invented words, the fake
engine). The synthetic Marathi pair runs end to end in ``packages/cli/tests``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import pytest

from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    COUNT,
    CREATED_AT,
    KAAM,
    MER,
    NAMASKAR,
    SHARE,
    TEST_ONLY_REVIEW_BUDGET,
    FakeEngine,
    configuration,
    export_text,
    mask_ids,
    mod,
    results_by_key,
    run_pipeline,
    scripts,
    sentence,
)
from tracer_evalschema.bundle import RunBundle, check_run_bundle


@dataclass(frozen=True)
class Expect:
    status: str
    na_reason: str | None
    missing_kinds: list[str]
    available: list[str]
    language_source: str | None
    error_code: str | None
    outcome: str
    exit_code: int


FULL = ["audio", "transcript", "agent_transcript"]
NO_AUDIO_INPUTS = ["agent_transcript"]
NO_AGENT_INPUTS = ["audio", "transcript"]
EXPECTED = {
    "audio_and_export": Expect("scored", None, [], FULL, "declared", None, "scored", 0),
    "no_audio": Expect(
        "not_applicable",
        "missing_input",
        ["transcript"],
        NO_AUDIO_INPUTS,
        None,
        None,
        "not_scored",
        2,
    ),
    "no_export": Expect(
        "not_applicable",
        "missing_input",
        ["agent_transcript"],
        NO_AGENT_INPUTS,
        None,
        None,
        "not_scored",
        2,
    ),
    "no_declaration": Expect(
        "not_applicable", "language_not_observed", [], FULL, None, None, "not_scored", 2
    ),
    "non_mr_chunk": Expect(
        "error", None, [], FULL, "declared", "stt_unsupported_language", "not_scored", 2
    ),
    "export_without_agent_turn": Expect(
        "not_applicable",
        "missing_input",
        ["agent_transcript"],
        NO_AGENT_INPUTS,
        None,
        None,
        "not_scored",
        2,
    ),
}


def _kwargs(case: str) -> dict[str, Any]:
    """The arguments of ``run_pipeline`` for a case (a fresh engine every time)."""
    caller_only = export_text([("user", sentence(NAMASKAR)), ("user", sentence(KAAM))])
    return {
        "audio_and_export": {},
        "no_audio": {"audio": None},
        "no_export": {"export": None},
        "no_declaration": {"export_language": None},
        "non_mr_chunk": {"engine": FakeEngine(chunk_scripts=scripts(languages=("mr", "hi")))},
        "export_without_agent_turn": {"export": caller_only},
    }[case]


def _run(tmp_path: Any, case: str, config: Any = None, **shared: Any) -> Any:
    return run_pipeline(tmp_path, config=config, **shared, **_kwargs(case))


def _assert_case(run: Any, case: str) -> None:
    want = EXPECTED[case]
    bundle = run.bundle
    assert bundle.run.observed_tier == "T0"
    assert bundle.run.mode == "recorded"
    assert bundle.run.available_inputs == want.available
    assert bundle.verdict is None  # no RunVerdict in Phase 3 (BL-186)
    assert len(bundle.results) == 3
    for result in bundle.results:
        assert result.status == want.status
        assert result.na_reason == want.na_reason
        assert result.missing_kinds == want.missing_kinds
        assert result.language_source == want.language_source
        assert (result.error.code if result.error else None) == want.error_code
        if want.status != "scored":
            # never a zero, never a pass or a fail, never a score
            assert result.value is None
            assert result.passed is None
            assert result.scorer is None or want.status == "error"
            assert result.evidence_refs == []
            assert result.metadata == {}
    assert run.result.outcome == want.outcome
    assert run.result.exit_code == want.exit_code
    check_run_bundle(bundle)
    assert RunBundle.model_validate_json(bundle.model_dump_json()) == bundle


def test_tdeg_audio_and_export_scored(tmp_path: Any) -> None:
    run = _run(tmp_path, "audio_and_export")
    _assert_case(run, "audio_and_export")
    results = results_by_key(run.bundle)
    assert results[MER].value == 3 / 12
    assert results[COUNT].value == 2
    assert results[SHARE].value == 1 / 11
    assert results[SHARE].passed is (TEST_ONLY_REVIEW_BUDGET >= 1 / 11)
    assert results[MER].passed is None
    assert results[COUNT].passed is None
    for result in run.bundle.results:
        assert result.scorer is not None
        assert result.language_source == "declared"
        assert len(result.transcript_ids or []) == 2
        assert result.error is None
        assert result.na_reason is None
    # the engine was called once: the recording exists
    assert len(run.engine.calls) == 1


def test_tdeg_no_audio_missing_input_transcript(tmp_path: Any) -> None:
    run = _run(tmp_path, "no_audio")
    _assert_case(run, "no_audio")
    assert run.engine.calls == []  # nothing to transcribe, nothing sent
    assert run.bundle.artifacts == []
    assert run.bundle.usage_records == []
    assert [t.source for t in run.bundle.transcripts] == ["agent_export"]
    for result in run.bundle.results:
        assert result.transcript_ids == [run.bundle.transcripts[0].id]


def test_tdeg_no_export_missing_input_agent_transcript(tmp_path: Any) -> None:
    run = _run(tmp_path, "no_export")
    _assert_case(run, "no_export")
    assert [t.source for t in run.bundle.transcripts] == ["our_stt"]
    assert len(run.engine.calls) == 1  # the recording is transcribed: that input does exist
    assert len(run.bundle.usage_records) == 2  # and its usage is recorded, never dropped


def test_tdeg_export_without_agent_turn_missing_input_agent_transcript(tmp_path: Any) -> None:
    run = _run(tmp_path, "export_without_agent_turn")
    _assert_case(run, "export_without_agent_turn")
    # T-A4: an export with no assistant line is read (its caller turns are in the bundle) but the
    # input kind agent_transcript is not available, so nothing is scored
    assert [t.role for t in run.bundle.turns if t.source.startswith("agent_")] == ["caller"] * 2
    assert "agent_transcript" not in run.bundle.run.available_inputs


def test_tdeg_export_without_declaration_language_not_observed(tmp_path: Any) -> None:
    run = _run(tmp_path, "no_declaration")
    _assert_case(run, "no_declaration")
    export = next(t for t in run.bundle.transcripts if t.source == "agent_export")
    assert export.declared_language is None
    assert export.detected_language is None
    for result in run.bundle.results:
        assert result.declared_language_transcript_ids == []
    # the speech-to-text turns do carry an observed language (mr); it is not borrowed for the
    # export: a language is never assumed (rule 14, F12)
    assert {t.language for t in run.bundle.turns if t.source == "our_stt"} == {"mr"}


def test_tdeg_non_mr_chunk_is_not_scored(tmp_path: Any) -> None:
    run = _run(tmp_path, "non_mr_chunk")
    _assert_case(run, "non_mr_chunk")
    for result in run.bundle.results:
        assert result.error is not None
        assert (
            result.error.message == "1 of 2 stt turns carry a language other than the covered one"
        )
        assert result.status != "not_applicable"  # rule 14 as amended by Decisions D7 (F12)
        assert result.status != "scored"
    # not an agent failure and not a zero: no flag, no count and no share exist for this call
    assert not any("tracer.flags" in r.metadata for r in run.bundle.results)
    assert run.result.stt_failure is None  # the transcription itself worked


def test_tdeg_missing_input_is_decided_before_the_language(tmp_path: Any) -> None:
    """ADR 0004 precedence: with no recording and no declaration the reason is the missing input."""
    run = run_pipeline(tmp_path, audio=None, export_language=None)
    for result in run.bundle.results:
        assert result.status == "not_applicable"
        assert result.na_reason == "missing_input"
        assert result.missing_kinds == ["transcript"]
        assert result.language_source is None
    check_run_bundle(run.bundle)


def _shape_by_kind(bundle: Any) -> dict[str, set[tuple[str, ...]]]:
    dumped = bundle.model_dump(mode="json")
    shapes: dict[str, set[tuple[str, ...]]] = {}
    for kind, value in dumped.items():
        if isinstance(value, list):
            shapes[kind] = {tuple(sorted(item)) for item in value if isinstance(item, dict)}
        elif isinstance(value, dict):
            shapes[kind] = {tuple(sorted(value))}
        else:
            shapes[kind] = set()
    return shapes


def test_tdeg_schema_identical_across_cases(tmp_path: Any) -> None:
    config = configuration()
    runs = {case: _run(tmp_path, case, config) for case in EXPECTED}
    base = runs["audio_and_export"].bundle
    all_shapes: dict[str, set[tuple[str, ...]]] = {}
    for case, run in runs.items():
        bundle = run.bundle
        # one configuration: the same snapshot, the same metric references in the same order
        assert bundle.snapshot == base.snapshot, case
        assert bundle.run.config_snapshot_id == base.run.config_snapshot_id, case
        assert [r.metric for r in bundle.results] == [r.metric for r in base.results], case
        assert list(results_by_key(bundle)) == [MER, COUNT, SHARE], case
        assert bundle.run.observed_tier == "T0", case
        assert {r.status for r in bundle.results} == {EXPECTED[case].status}, case
        assert {r.status for r in bundle.results} <= {"scored", "not_applicable", "error"}
        # the records round trip through their own JSON, so the schema holds on read
        again = RunBundle.model_validate_json(bundle.model_dump_json())
        assert again.model_dump(mode="json") == bundle.model_dump(mode="json"), case
        check_run_bundle(bundle)
        for kind, shapes in _shape_by_kind(bundle).items():
            all_shapes.setdefault(kind, set()).update(shapes)
    # one JSON member set per record kind over all six situations
    for kind, shapes in all_shapes.items():
        assert len(shapes) <= 1, (kind, shapes)
    # the six situations really differ in what they hold (the matrix is not six copies)
    assert len({tuple(r.bundle.run.available_inputs) for r in runs.values()}) == 3
    assert (
        len({(r.bundle.results[0].status, r.bundle.results[0].na_reason) for r in runs.values()})
        == 4
    )
    # a metric that cannot be computed is never a zero: no result of any case holds 0 or False
    for case, run in runs.items():
        for result in run.bundle.results:
            if result.status != "scored":
                assert result.value is None, case
                assert result.passed is None, case
    dumped = json.dumps([r.bundle.model_dump(mode="json") for r in runs.values()])
    assert not re.search(r'"value": (?:0|0\.0|false)[,}]', dumped)


@pytest.mark.parametrize("case", list(EXPECTED))
def test_tdeg_every_case_is_deterministic_for_the_same_inputs(tmp_path: Any, case: str) -> None:
    """Two runs of one situation are identical once the fresh ids are masked (batch D rule)."""
    definitions = mod("definitions")
    config = configuration()
    project = definitions.recorded_project(name="tdeg project", created_at=CREATED_AT)
    target = definitions.recorded_agent_target(name="tdeg agent", created_at=CREATED_AT)
    first = _run(tmp_path, case, config, project=project, agent_target=target)
    second = _run(tmp_path, case, config, project=project, agent_target=target)
    assert mask_ids(first.bundle.model_dump_json()) == mask_ids(second.bundle.model_dump_json())
