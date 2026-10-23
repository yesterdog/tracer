# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""T-DEG, part 1 (P1-19): the same evaluator at T0, T1 and T2 gives the same schema, and each metric
is scored, or not applicable for the reason ADR 0004 prescribes.

The bundles come from ``_support_b13.deg_world``: one RunBundle per column of the matrix (T0, T0m,
T1, T2; T0m is T0 evidence plus an identified agent channel, not a tier), all built from the same
13 metric definitions and one rubric with four required checks. The expected statuses are the
hand-written ``EXPECTED`` table of ``_support_b13``, which is also the matrix of
``docs/phase1/api-surface-tdeg.md``; nothing is derived from the code under test.

Names. These eight come from docs/phase1/SCOPE.md section 5 (T-DEG), verbatim:
test_deg_schema_identical,
test_deg_same_metric_keys_all_tiers, test_deg_tool_metric_na_at_t0,
test_deg_verdict_inconclusive_when_required_na, test_deg_no_na_with_value0_or_passed_false,
test_deg_agent_cost_not_available_at_t0, test_deg_eval_cost_computed_all_tiers and
test_deg_agent_audio_na_when_channel_unknown (the cost tests are in test_tier_degradation_cost.py).
Every other test name in the three T-DEG files is PROPOSED by qa (not in SCOPE).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import ROOT, api
from ._support_b2 import registered_class, schema_accepts
from ._support_b13 import (
    COLUMNS,
    EXPECTED,
    METRICS,
    REQUIRED_KEYS,
    WEIGHTS,
    Case,
    TdegWorld,
    available_inputs_of,
    bundle_of,
    cached_world,
    metric_keys,
    result_by_key,
)

MATRIX_DOC = ROOT / "docs" / "phase1" / "api-surface-tdeg.md"
KEYS = metric_keys()
NA_REASONS = (
    "missing_input",
    "agent_channel_unknown",
    "language_not_observed",
    "unsupported_language",
    "unsupported_script",
)


def _all_worlds() -> Iterator[TdegWorld]:
    for column in COLUMNS:
        for origin in ("simulated", "human"):
            yield cached_world(column, origin=origin)


def _results(world: TdegWorld) -> dict[str, Any]:
    bundle = bundle_of(world)
    by_id = {str(r.metric.id): r for r in bundle.results}
    return {str(d.key): by_id[str(d.id)] for d in bundle.snapshot.metric_definitions}


# ---------------------------------------------------------------------- the matrix itself


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize("key", KEYS)
def test_deg_matrix_cell_matches_adr_0004(column: str, key: str) -> None:
    """PROPOSED. One cell of the regression matrix: status, reason, missing kinds, value, pass
    decision, share and language source, against the hand-written table."""
    expected = EXPECTED[key][column]
    result = _results(cached_world(column))[key]
    assert result.status == expected.status, (column, key)
    if expected.status == "not_applicable":
        assert result.na_reason == expected.reason, (column, key)
        assert tuple(result.missing_kinds) == expected.kinds, (column, key)
        assert result.value is None
        assert result.passed is None
        assert result.error is None
        assert result.scorer is None
        assert result.judge is None
        return
    assert result.na_reason is None
    assert list(result.missing_kinds) == []
    assert result.language_source == expected.source, (column, key)
    if expected.share is not None:
        assert result.value is None  # a per-turn metric scores through per_turn (P-58)
        assert result.per_turn is not None
        assert result.per_turn.share == expected.share, (column, key)
    else:
        assert result.value == expected.value, (column, key)
        assert type(result.value) is type(expected.value), (column, key)
    assert result.passed is expected.passed, (column, key)


def test_deg_matrix_table_is_complete() -> None:
    """PROPOSED. The expected table has a cell for every metric and every column, and no other."""
    assert set(EXPECTED) == set(KEYS)
    for key in KEYS:
        assert set(EXPECTED[key]) == set(COLUMNS), key


def test_deg_matrix_doc_matches_expected() -> None:
    """PROPOSED. docs/phase1/api-surface-tdeg.md holds the matrix, and its cells equal the
    expected table the tests use (a hand edit of one without the other fails here)."""
    text = MATRIX_DOC.read_text(encoding="utf-8")
    start = text.index("## 3. The regression matrix")
    section = text[start : text.index("\n## 4.", start)]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        match = re.fullmatch(r"\|\s*`([a-z0-9_]+)`\s*\|(.*)\|\s*", line)
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group(2).split("|")]
        assert match.group(1) not in rows, match.group(1)
        rows[match.group(1)] = cells
    assert set(rows) >= set(KEYS), sorted(set(KEYS) - set(rows))
    for key in KEYS:
        cells = rows[key]
        assert len(cells) >= 1 + len(COLUMNS), (key, cells)
        # the first cell is the requires list; then one cell per column
        assert [c.strip("` ") for c in cells[1 : 1 + len(COLUMNS)]] == [
            EXPECTED[key][column].cell() for column in COLUMNS
        ], key


# ---------------------------------------------------------------- the same metric keys


def test_deg_same_metric_keys_all_tiers() -> None:
    """ADR 0004 I8: every tier has a result for every metric of the snapshot, once, in the order of
    the snapshot; a missing input never drops the metric from the results."""
    seen: list[list[str]] = []
    for world in _all_worlds():
        bundle = bundle_of(world)
        definitions = bundle.snapshot.metric_definitions
        assert [str(d.key) for d in definitions] == KEYS
        results = bundle.results
        assert [str(r.metric.id) for r in results] == [str(d.id) for d in definitions]
        assert len({str(r.metric.id) for r in results}) == len(results)
        seen.append([str(d.key) for d in definitions])
        assert len(bundle.results) == len(METRICS) == 13
    assert all(keys == seen[0] for keys in seen)


def test_deg_every_tier_binds_the_same_definitions() -> None:
    """PROPOSED. The metric definitions (keys, requires, languages, script flag, aggregation) of
    the snapshot do not depend on the tier."""
    shapes = []
    for column in COLUMNS:
        definitions = bundle_of(cached_world(column)).snapshot.metric_definitions
        shapes.append(
            [
                (
                    str(d.key),
                    tuple(d.requires),
                    d.languages if isinstance(d.languages, str) else tuple(d.languages),
                    d.script_sensitive,
                    d.aggregation.kind,
                )
                for d in definitions
            ]
        )
    assert all(shape == shapes[0] for shape in shapes)
    by_key = {m.key: m for m in METRICS}
    for key, requires, languages, sensitive, kind in shapes[0]:
        assert requires == by_key[key].requires
        assert languages == by_key[key].languages
        assert sensitive == by_key[key].script_sensitive
        assert kind == by_key[key].aggregation


def test_deg_rubric_covers_the_requires_combinations() -> None:
    """PROPOSED. Guard of the fixture itself: the 13 definitions cover audio, agent_audio,
    transcript, agent_transcript, turn_timing, tool_calls, spans, usage and reference_text, both
    language forms, a script-sensitive metric, a per-turn metric of each language form and an
    informational metric. A weaker fixture would let a rule drift unnoticed."""
    needs = {kind for m in METRICS for kind in m.requires}
    assert needs >= {
        "audio",
        "agent_audio",
        "transcript",
        "agent_transcript",
        "turn_timing",
        "tool_calls",
        "tool_results",
        "spans",
        "usage",
        "reference_text",
    }
    assert any(m.languages == "language_independent" for m in METRICS)
    assert any(m.languages != "language_independent" for m in METRICS)
    assert any(m.script_sensitive for m in METRICS)
    per_turn = [m for m in METRICS if m.aggregation == "per_turn_share"]
    assert {m.languages == "language_independent" for m in per_turn} == {True, False}
    assert any(m.kind == "llm_judge" for m in METRICS)
    assert len(REQUIRED_KEYS) == 4


# -------------------------------------------------------------------- the same schema shape


def _field_aliases(model: Any) -> set[str]:
    return {info.alias or name for name, info in model.model_fields.items()}


def _key_sets(value: Any, model: Any, found: dict[str, set[frozenset[str]]]) -> None:
    """Collect, by model name, the key set of every record dumped from ``model`` instances."""
    if isinstance(value, dict):
        found.setdefault(model.__name__, set()).add(frozenset(value))


LISTS = {
    "artifacts": "Artifact",
    "transcripts": "Transcript",
    "turns": "Turn",
    "exchanges": "Exchange",
    "tool_calls": "ToolCallEvent",
    "spans": "StageSpan",
    "results": "MetricResult",
    "usage_records": "UsageRecord",
    "cost_records": "CostRecord",
    "cost_summaries": "CostSummary",
}


def test_deg_schema_identical() -> None:
    """ADR 0004 I8, SCOPE T-DEG: the bundles of all tiers have the same entity types with the same
    fields. Every record of every list, scored or not applicable, available or not available,
    present or empty, has exactly the fields of its model; the bundle has the same members; and the
    JSON Schema of the RunBundle accepts the document of every column."""
    members: list[set[str]] = []
    for world in _all_worlds():
        bundle = bundle_of(world)
        dumped = bundle.model_dump(mode="json", by_alias=True)
        members.append(set(dumped))
        assert set(dumped) == _field_aliases(type(bundle))
        for name, class_name in LISTS.items():
            model = registered_class(class_name)
            for record in dumped[name]:
                assert set(record) == _field_aliases(model), (world.column, name)
        assert set(dumped["run"]) == _field_aliases(registered_class("Run"))
        assert set(dumped["verdict"]) == _field_aliases(registered_class("RunVerdict"))
        assert set(dumped["snapshot"]) == _field_aliases(registered_class("ConfigSnapshot"))
    assert all(m == members[0] for m in members)


@pytest.mark.parametrize("column", COLUMNS)
def test_deg_every_bundle_validates_against_the_json_schema(column: str) -> None:
    """PROPOSED. The JSON Schema generated from the model accepts the document of each column (the
    schema, not only pydantic, allows a not_applicable result without value and a not_available
    cost without amount)."""
    world = cached_world(column)
    model = api("bundle").RunBundle
    assert schema_accepts(model, world.doc)
    # and the validated dump (every optional member an explicit null) too
    dumped = bundle_of(world).model_dump(mode="json", by_alias=True)
    assert schema_accepts(model, dumped)


@pytest.mark.parametrize("column", COLUMNS)
def test_deg_bundle_json_round_trip_is_stable(column: str) -> None:
    """PROPOSED. A bundle written as JSON and read back is equal (no tier loses or invents a
    field in a round trip)."""
    bundle = bundle_of(cached_world(column))
    text = bundle.model_dump_json(by_alias=True)
    again = type(bundle).model_validate_json(text)
    assert again == bundle
    assert again.model_dump_json(by_alias=True) == text


def test_deg_entity_types_differ_only_by_the_records_a_tier_does_not_hold() -> None:
    """PROPOSED. T0 holds no tool call and no span; T1 and T2 hold both. Every other record kind is
    present at every tier (the same entity types), so a reader never meets a tier-specific type."""
    present: dict[str, set[str]] = {}
    for column in COLUMNS:
        bundle = bundle_of(cached_world(column))
        present[column] = {name for name in LISTS if getattr(bundle, name)}
    assert present["T1"] == present["T2"] == set(LISTS)
    assert present["T0"] == present["T0m"] == set(LISTS) - {"tool_calls", "spans"}
    # the verdict and the cost summaries exist at every tier too
    for column in COLUMNS:
        bundle = bundle_of(cached_world(column))
        assert bundle.verdict is not None
        assert {s.domain for s in bundle.cost_summaries} == {"evaluation", "agent_under_test"}


# ----------------------------------------------------------- tool metrics, channel, evidence


def test_deg_tool_metric_na_at_t0() -> None:
    """ADR 0004 I7: a tool-call metric is not applicable at T0 with missing_input and the exact
    missing kinds, never scored, failed or 0; at T1 and T2 it is scored."""
    for column in ("T0", "T0m"):
        result = _results(cached_world(column))["tool_call_success"]
        assert result.status == "not_applicable"
        assert result.na_reason == "missing_input"
        assert list(result.missing_kinds) == ["tool_calls", "tool_results"]
        assert result.value is None
        assert result.passed is None
    for column in ("T1", "T2"):
        result = _results(cached_world(column))["tool_call_success"]
        assert result.status == "scored"
        assert result.value is True
        assert result.passed is True
        assert list(result.missing_kinds) == []
    # and the helper that decides it, on the runs of the bundles
    results_api = api("results")
    definition = next(
        d
        for d in bundle_of(cached_world("T0")).snapshot.metric_definitions
        if d.key == "tool_call_success"
    )
    assert results_api.missing_inputs(definition, bundle_of(cached_world("T0")).run) == [
        "tool_calls",
        "tool_results",
    ]
    assert results_api.missing_inputs(definition, bundle_of(cached_world("T1")).run) == []


def test_deg_agent_audio_na_when_channel_unknown() -> None:
    """ADR 0004 Decision 5 and 5a (N23), I9: at T0 the recording exists (audio) and nobody
    established which channel is the agent. A metric that needs agent_audio alone is not applicable
    with agent_channel_unknown and no missing kinds; with one more missing kind it is missing_input
    with all of them; once a channel map identifies the agent (T0m) it is scored."""
    t0 = _results(cached_world("T0"))
    clarity = t0["agent_audio_clarity"]
    assert clarity.status == "not_applicable"
    assert clarity.na_reason == "agent_channel_unknown"
    assert list(clarity.missing_kinds) == []
    assert clarity.value is None
    assert clarity.passed is None
    assert clarity.language_source is None  # the language steps did not run
    assert list(clarity.declared_language_transcript_ids) == []
    wer = t0["agent_wer"]
    assert wer.na_reason == "missing_input"
    assert list(wer.missing_kinds) == ["agent_audio", "reference_text"]
    run = bundle_of(cached_world("T0")).run
    assert "audio" in run.available_inputs
    assert "agent_audio" not in run.available_inputs
    assert run.channel_maps == []
    # once a channel map identifies exactly one agent channel the same metric is scored
    assert _results(cached_world("T0m"))["agent_audio_clarity"].status == "scored"
    assert "agent_audio" in bundle_of(cached_world("T0m")).run.available_inputs


@pytest.mark.parametrize("role", ["unknown", "mixed"])
def test_deg_channel_map_without_an_agent_channel_is_still_unknown(role: str) -> None:
    """PROPOSED (ADR 0004 Decision 5a, first example): a map whose channels are all ``unknown`` or
    ``mixed`` does not identify the agent, so the answer is the same as with no map."""
    world = cached_world("T0")
    doc = world.copy()
    artifact_id = doc["artifacts"][0]["id"]
    doc["run"]["channel_maps"] = [
        {
            "artifact_id": artifact_id,
            "channels": [{"index": 0, "role": role}],
            "determination": "declared",
            "method": "recorder_layout",
        }
    ]
    bundle = api("bundle").RunBundle.model_validate(doc)
    definition = next(
        d for d in bundle.snapshot.metric_definitions if d.key == "agent_audio_clarity"
    )
    found = api("results").applicability(
        definition, bundle.run, turns=[], transcripts=bundle.transcripts
    )
    assert found.na_reason == "agent_channel_unknown"
    assert found.missing_kinds == []


def test_deg_no_recording_is_missing_input_not_channel_unknown() -> None:
    """PROPOSED (ADR 0004 Decision 5a, second and third examples): without a recording there is no
    channel to identify, so agent_audio alone is missing_input with that kind."""
    bundle = bundle_of(cached_world("T0"))
    run_doc = bundle.run.model_dump(mode="json", by_alias=True)
    run_doc["available_inputs"] = ["transcript", "turn_timing"]
    run = registered_class("Run").model_validate(run_doc)
    by_key = {d.key: d for d in bundle.snapshot.metric_definitions}
    found = api("results").applicability(
        by_key["agent_audio_clarity"], run, turns=[], transcripts=bundle.transcripts
    )
    assert (found.na_reason, found.missing_kinds) == ("missing_input", ["agent_audio"])
    found = api("results").applicability(
        by_key["silence_ratio"], run, turns=[], transcripts=bundle.transcripts
    )
    assert (found.na_reason, found.missing_kinds) == ("missing_input", ["audio"])


def test_deg_available_inputs_follow_the_evidence_not_the_tier_label() -> None:
    """ADR 0004 Decision 2, I15 (proposed name): a T1 run without spans does not list ``spans`` and
    its span metric is not applicable; a T0 run with an identified agent channel scores an
    agent-audio metric. The label never decides."""
    no_spans = cached_world("T1", spans=False)
    bundle = bundle_of(no_spans)
    assert bundle.run.observed_tier == "T1"
    assert "spans" not in bundle.run.available_inputs
    assert bundle.spans == []
    stage = _results(no_spans)["stage_ttfb_p95"]
    assert (stage.status, stage.na_reason, list(stage.missing_kinds)) == (
        "not_applicable",
        "missing_input",
        ["spans"],
    )
    # everything else of T1 is unchanged
    for key in KEYS:
        if key != "stage_ttfb_p95":
            assert _results(no_spans)[key].status == _results(cached_world("T1"))[key].status
    assert bundle_of(cached_world("T0m")).run.observed_tier == "T0"
    assert _results(cached_world("T0m"))["agent_audio_clarity"].status == "scored"


def test_deg_applicability_never_reads_observed_tier() -> None:
    """ADR 0004 Decision 2a: ``observed_tier`` is display only. Changing the stored label of a run
    changes no applicability answer for any metric (the function is pure over available_inputs and
    the channel maps)."""
    results_api = api("results")
    for column in COLUMNS:
        world = cached_world(column)
        bundle = bundle_of(world)
        for label in ("T0", "T1", "T2"):
            relabelled = bundle.run.model_copy(update={"observed_tier": label})
            for definition in bundle.snapshot.metric_definitions:
                turns = [
                    t
                    for t in bundle.turns
                    if "agent_transcript" in definition.requires and t.role == "agent"
                ]
                plain = results_api.applicability(
                    definition, bundle.run, turns=turns, transcripts=bundle.transcripts
                )
                other = results_api.applicability(
                    definition, relabelled, turns=turns, transcripts=bundle.transcripts
                )
                assert plain == other, (column, label, definition.key)


def test_deg_run_available_inputs_equal_the_written_evidence() -> None:
    """PROPOSED. The runs of the matrix list exactly the inputs the fixture says each tier holds
    (a guard of the fixture, and of Run rejecting agent_audio without a channel map)."""
    for column in COLUMNS:
        run = bundle_of(cached_world(column)).run
        assert sorted(run.available_inputs) == sorted(available_inputs_of(column)), column
    # agent_audio needs an identifying channel map (Run rule, ADR 0004 Decision 5)
    doc = cached_world("T0").run.model_dump(mode="json", by_alias=True)
    doc["available_inputs"] = ["audio", "agent_audio", "transcript"]
    with pytest.raises(ValidationError):
        registered_class("Run").model_validate(doc)


# -------------------------------------------------------------------------- the verdict


def _outcome(case: Case) -> str:
    if case.status == "not_applicable":
        return "not_applicable"
    return "passed" if case.passed else "failed"


def _expected_verdict(column: str, required: tuple[str, ...] = REQUIRED_KEYS) -> dict[str, int]:
    tally = {"passed": 0, "failed": 0, "not_applicable": 0, "errored": 0}
    for key in KEYS:
        if key in required:
            tally[_outcome(EXPECTED[key][column])] += 1
    return tally


def _weighted(column: str) -> tuple[str, str, list[str]]:
    """The weighted score, its coverage and the excluded keys, from the expected table with exact
    fractions (written independently of ``compute_weighted_score``)."""
    total = scored = earned = Fraction(0)
    excluded: list[str] = []
    for key in KEYS:
        weight = WEIGHTS.get(key)
        if weight is None:
            continue
        w = Fraction(weight)
        total += w
        case = EXPECTED[key][column]
        if case.status == "not_applicable":
            excluded.append(key)
            continue
        scored += w
        if case.passed:
            earned += w
    quantum = Decimal(1).scaleb(-18)

    def text(value: Fraction) -> str:
        decimal = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            quantum, rounding=ROUND_HALF_EVEN
        )
        return format(decimal.normalize(), "f")

    return text(earned / scored), text(scored / total), excluded


@pytest.mark.parametrize("column", COLUMNS)
def test_deg_verdict_counts_and_status_per_tier(column: str) -> None:
    """ADR 0013 Decisions 3 to 6 (proposed name): x/y required checks passed, with the four counts
    and the status, equal the counts of the expected table; the denominator keeps the checks that
    did not run."""
    verdict = bundle_of(cached_world(column)).verdict
    tally = _expected_verdict(column)
    assert verdict.required_total == 4
    assert (verdict.passed, verdict.failed, verdict.not_applicable, verdict.errored) == (
        tally["passed"],
        tally["failed"],
        tally["not_applicable"],
        tally["errored"],
    )
    status = (
        "fail"
        if tally["failed"]
        else ("pass" if tally["passed"] == verdict.required_total else "inconclusive")
    )
    assert verdict.status == status
    headline = api("verdict").render_headline(verdict)
    assert headline == f"{tally['passed']}/4 required checks passed"
    # one check per required metric, in the outcome the result gives
    by_key = _results(cached_world(column))
    outcomes = {c.result_id: c.outcome for c in verdict.checks}
    assert len(verdict.checks) == 4
    for key in REQUIRED_KEYS:
        assert outcomes[by_key[key].id] == _outcome(EXPECTED[key][column]), (column, key)


def test_deg_verdict_inconclusive_when_required_na() -> None:
    """ADR 0013 I9, ADR 0004 I10: a required check that is not applicable makes the verdict
    inconclusive, never pass and never fail (T0: two required checks cannot run; T0m: one); with
    every input present (T1, T2) it passes. The inconclusive share per tier is not_applicable over
    required_total: 0.5, 0.25, 0, 0."""
    shares = {}
    for column in COLUMNS:
        verdict = bundle_of(cached_world(column)).verdict
        shares[column] = Fraction(verdict.not_applicable + verdict.errored, verdict.required_total)
    assert shares == {
        "T0": Fraction(1, 2),
        "T0m": Fraction(1, 4),
        "T1": Fraction(0),
        "T2": Fraction(0),
    }
    assert bundle_of(cached_world("T0")).verdict.status == "inconclusive"
    assert bundle_of(cached_world("T0m")).verdict.status == "inconclusive"
    assert bundle_of(cached_world("T1")).verdict.status == "pass"
    assert bundle_of(cached_world("T2")).verdict.status == "pass"
    # the not applicable checks are the ones whose results are not applicable
    t0 = bundle_of(cached_world("T0")).verdict
    na_results = {c.result_id for c in t0.checks if c.outcome == "not_applicable"}
    by_key = _results(cached_world("T0"))
    assert na_results == {by_key["tool_call_success"].id, by_key["agent_politeness"].id}


def test_deg_required_checks_that_are_all_na_give_inconclusive_never_pass() -> None:
    """ADR 0004 Consequences, ADR 0013 Decisions 6 and 8 (proposed name): a T0 recording whose STT
    pass produced nothing (only ``audio``) cannot run any of the four required checks. No check
    passed, none failed: the verdict is inconclusive, the headline is 0/4, and no weighted score is
    stored (never a score of 0 standing for nothing measured). The audio-only metric still
    scores."""
    world = cached_world("T0", stt=False)
    bundle = bundle_of(world)
    assert bundle.run.available_inputs == ["audio"]
    assert bundle.transcripts == []
    assert bundle.turns == []
    verdict = bundle.verdict
    assert (verdict.required_total, verdict.passed, verdict.failed) == (4, 0, 0)
    assert (verdict.not_applicable, verdict.errored) == (4, 0)
    assert verdict.status == "inconclusive"
    assert api("verdict").render_headline(verdict) == "0/4 required checks passed"
    assert verdict.weighted_score is None
    assert verdict.weight_coverage is None
    assert api("verdict").render_weighted_score(verdict) is None
    assert len(verdict.weighted_excluded) == 7  # every weighted check, none counted as 0
    by_key = _results(world)
    assert by_key["goal_completion"].na_reason == "missing_input"
    assert list(by_key["goal_completion"].missing_kinds) == ["transcript"]
    assert list(by_key["p95_latency"].missing_kinds) == ["turn_timing"]
    assert by_key["silence_ratio"].status == "scored"
    # the same rubric with the STT pass present is not inconclusive for that reason
    assert bundle_of(cached_world("T0")).verdict.passed == 2


def test_deg_a_failed_required_check_makes_fail_even_with_na_checks() -> None:
    """ADR 0013 Decision 6 (proposed name): fail wins over inconclusive. A T0 run whose latency
    check fails and whose two other required checks are not applicable is ``fail``."""
    world = cached_world("T0")
    doc = world.copy()
    by_key = result_by_key(world)
    latency = next(r for r in doc["results"] if r["id"] == by_key["p95_latency"]["id"])
    latency["value"] = 950
    latency["passed"] = False
    for check in doc["verdict"]["checks"]:
        if check["result_id"] == latency["id"]:
            check["outcome"] = "failed"
    verdict = doc["verdict"]
    verdict["passed"], verdict["failed"] = 1, 1
    verdict["status"] = "fail"
    # recompute the weighted score of the new outcomes with the library function
    verdict_api = api("verdict")
    bundle = bundle_of(world)
    bindings = {str(b.metric.id): b for b in bundle.snapshot.metric_bindings}
    items = []
    for item in bundle.snapshot.rubric.items:
        if item.weight is None:
            continue
        binding = next(b for b in bundle.snapshot.metric_bindings if b.id == item.binding.id)
        result = next(r for r in doc["results"] if r["metric"]["id"] == str(binding.metric.id))
        if result["status"] == "not_applicable":
            outcome = "not_applicable"
        else:
            outcome = "passed" if result["passed"] else "failed"
        items.append((binding.id, item.weight, outcome))
    assert bindings
    score = verdict_api.compute_weighted_score(items)
    verdict["weighted_score"], verdict["weight_coverage"] = score.score, score.coverage
    loaded = api("bundle").RunBundle.model_validate(doc)
    assert loaded.verdict.status == "fail"
    assert (loaded.verdict.passed, loaded.verdict.failed, loaded.verdict.not_applicable) == (
        1,
        1,
        2,
    )
    # a stored status of inconclusive for this tally is rejected
    doc["verdict"]["status"] = "inconclusive"
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)


@pytest.mark.parametrize("column", COLUMNS)
def test_deg_weighted_score_always_has_its_weight_coverage(column: str) -> None:
    """ADR 0013 Decision 8, P-33 (proposed name): the stored weighted score equals the exact
    expected value; the coverage is stored with it and equals scored weight over total weight; the
    excluded weighted checks are exactly the not applicable ones, in rubric order; and the
    rendering shows the score only with its coverage."""
    verdict = bundle_of(cached_world(column)).verdict
    score, coverage, excluded = _weighted(column)
    assert verdict.weighted_score == score
    assert verdict.weight_coverage == coverage
    assert verdict.weight_coverage is not None
    shown = api("verdict").render_weighted_score(verdict)
    assert shown is not None
    assert "weight coverage" in shown
    bundle = bundle_of(cached_world(column))
    keyed = {str(b.id): str(b.metric.id) for b in bundle.snapshot.metric_bindings}
    definition_key = {str(d.id): str(d.key) for d in bundle.snapshot.metric_definitions}
    listed = [definition_key[keyed[str(e.binding_id)]] for e in bundle.verdict.weighted_excluded]
    assert listed == excluded
    assert all(e.outcome == "not_applicable" for e in bundle.verdict.weighted_excluded)


def test_deg_weighted_score_values_per_tier_are_the_golden_numbers() -> None:
    """PROPOSED. The hand-computed numbers of the regression matrix, spelled out: T0 scores 1 over
    a coverage of 0.5 (9 of 18 weight measured); T0m 11/13 over 13/18; T1 and T2 16/18 over 1.
    ``1`` is not a measurement of the whole rubric and is never shown without its coverage."""
    got = {
        c: (
            bundle_of(cached_world(c)).verdict.weighted_score,
            bundle_of(cached_world(c)).verdict.weight_coverage,
        )
        for c in COLUMNS
    }
    assert got == {
        "T0": ("1", "0.5"),
        "T0m": ("0.846153846153846154", "0.722222222222222222"),
        "T1": ("0.888888888888888889", "1"),
        "T2": ("0.888888888888888889", "1"),
    }
    texts = {
        c: api("verdict").render_weighted_score(bundle_of(cached_world(c)).verdict) for c in COLUMNS
    }
    assert texts == {
        "T0": "weighted score 1.00 (weight coverage 0.50)",
        "T0m": "weighted score 0.85 (weight coverage 0.72)",
        "T1": "weighted score 0.89 (weight coverage 1.00)",
        "T2": "weighted score 0.89 (weight coverage 1.00)",
    }


def test_deg_not_applicable_weighted_check_is_never_counted_as_zero() -> None:
    """ADR 0013 Decision 8 (proposed name): the T0 score (1) is not pulled down by the weighted
    checks that did not run (a 0 each would give 9/18 = 0.5); the coverage carries that fact."""
    verdict = bundle_of(cached_world("T0")).verdict
    assert Decimal(verdict.weighted_score or "0") == 1
    assert Decimal(verdict.weight_coverage or "0") == Decimal("0.5")
    assert len(verdict.weighted_excluded) == 5  # m03, m04, m06 (channel), m07, m09


# ------------------------------------------------------- never zero or false for missing


def _na_results_of_all_worlds() -> Iterator[tuple[str, str, Any]]:
    for world in _all_worlds():
        for key, result in _results(world).items():
            if result.status == "not_applicable":
                yield world.column, key, result


def test_deg_no_na_with_value0_or_passed_false() -> None:
    """SCOPE gate item 6, ADR 0004 I1/I2/I6: across every bundle of the matrix (both caller
    origins), a not_applicable result has value None (never 0, false, an empty string or a
    zero-turn share), passed None, a reason from the closed set, missing kinds exactly when the
    reason is missing_input, and its verdict check is not_applicable (never failed)."""
    count = 0
    for column, key, result in _na_results_of_all_worlds():
        count += 1
        assert result.value is None, (column, key)
        assert result.passed is None, (column, key)
        assert result.na_reason in NA_REASONS, (column, key)
        assert bool(result.missing_kinds) == (result.na_reason == "missing_input"), (column, key)
        assert result.error is None
        assert result.per_turn is None or result.per_turn.share is None, (column, key)
    assert count >= 2 * (9 + 4 + 1 + 1)  # the matrix has 15 not applicable cells per origin
    for world in _all_worlds():
        bundle = bundle_of(world)
        na_ids = {r.id for r in bundle.results if r.status == "not_applicable"}
        for check in bundle.verdict.checks:
            if check.result_id in na_ids:
                assert check.outcome == "not_applicable"
            else:
                assert check.outcome != "not_applicable"
        # the JSON of a not applicable result has an explicit null value, never a number
        dumped = bundle.model_dump(mode="json", by_alias=True)
        for result in dumped["results"]:
            if result["status"] == "not_applicable":
                assert result["value"] is None
                assert result["passed"] is None


@pytest.mark.parametrize(
    ("change", "tag"),
    [
        ({"value": 0}, "value"),
        ({"value": False}, "value"),
        ({"value": ""}, "value"),
        ({"passed": False}, "passed"),
        ({"passed": True}, "passed"),
    ],
)
def test_deg_not_applicable_result_with_a_value_or_a_pass_decision_is_rejected(
    change: dict[str, Any], tag: str
) -> None:
    """ADR 0004 Decision 4 and I1 (proposed name): a not_applicable result that carries a value of
    0, false or an empty string, or any pass decision, does not load, in a bundle of every tier."""
    for column in ("T0", "T1"):
        world = cached_world(column)
        by_key = result_by_key(world)
        target_key = "tool_call_success" if column == "T0" else "agent_wer"
        assert by_key[target_key]["status"] == "not_applicable"
        doc = world.copy()
        for result in doc["results"]:
            if result["id"] == by_key[target_key]["id"]:
                result.update(change)
        with pytest.raises(ValidationError) as caught:
            api("bundle").RunBundle.model_validate(doc)
        assert tag in str(caught.value) or "not_applicable" in str(caught.value)


def test_deg_scored_result_is_never_for_a_missing_input_in_the_reference_evaluator() -> None:
    """PROPOSED. The harness itself never scores what the run lacks: for every bundle and metric
    the status follows ``missing_inputs`` (a scored result implies an empty missing list)."""
    results_api = api("results")
    for world in _all_worlds():
        bundle = bundle_of(world)
        by_key = _results(world)
        for definition in bundle.snapshot.metric_definitions:
            missing = results_api.missing_inputs(definition, bundle.run)
            result = by_key[str(definition.key)]
            if missing:
                assert result.status == "not_applicable", (world.column, definition.key)
            if result.status == "scored":
                assert missing == []
            if result.na_reason == "missing_input":
                assert list(result.missing_kinds) == missing
                assert set(result.missing_kinds) == set(definition.requires) - set(
                    bundle.run.available_inputs
                )


# ------------------------------------------------------------------- order of the reasons


def test_deg_reason_precedence_missing_input_before_channel_before_language() -> None:
    """ADR 0004 Decision 4 (P-10, P-59), proposed name. T0 with an untagged transcript: the audio
    metric that is only missing the channel says agent_channel_unknown, the agent-transcript metric
    says missing_input (before any language reason), and the transcript metrics, whose inputs
    exist, say language_not_observed."""
    world = cached_world("T0", language="untagged")
    by_key = _results(world)
    assert by_key["agent_audio_clarity"].na_reason == "agent_channel_unknown"
    assert by_key["agent_politeness"].na_reason == "missing_input"
    assert by_key["turn_politeness_share"].na_reason == "missing_input"
    assert by_key["goal_completion"].na_reason == "language_not_observed"
    assert by_key["script_consistency"].na_reason == "language_not_observed"
    # a language-independent metric has no language step: still scored
    assert by_key["p95_latency"].status == "scored"
    assert by_key["p95_latency"].language_source is None
    # an unsupported language does not change the two earlier reasons
    other = _results(cached_world("T0", language="unsupported_language"))
    assert other["agent_audio_clarity"].na_reason == "agent_channel_unknown"
    assert other["agent_politeness"].na_reason == "missing_input"
    assert other["goal_completion"].na_reason == "unsupported_language"


def test_deg_strongest_reason_over_all_reasons() -> None:
    """PROPOSED. ``strongest_na_reason`` over every reason the matrix produces returns the reason of
    ADR 0004 Decision 4's order, whatever the input order."""
    results_api = api("results")
    order = list(NA_REASONS)
    for first in order:
        for second in order:
            expected = min(first, second, key=order.index)
            assert results_api.strongest_na_reason([first, second]) == expected
            assert results_api.strongest_na_reason([second, first]) == expected


def test_deg_copy_of_a_document_is_independent() -> None:
    """PROPOSED. Guard of the fixture cache: ``TdegWorld.copy`` never shares state, so the tests
    that edit a copy cannot change another test's bundle."""
    world = cached_world("T1")
    first = world.copy()
    first["results"][0]["status"] = "error"
    assert world.doc["results"][0]["status"] != "error"
    assert copy.deepcopy(world.doc) == world.doc
