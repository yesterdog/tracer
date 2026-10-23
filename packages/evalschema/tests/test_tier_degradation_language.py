# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""T-DEG, part 3 (P1-19): the language paths, the observed tier, the export of every bundle, and the
bundle-level check of results (B15).

- Language (ADR 0004 Decisions 4, 6 and 7): the same evaluator on bundles whose transcript is
  tagged, untagged, declared by the user (imports only), tagged with a language outside the metrics'
  lists, romanized, or mixed.
- Observed tier (ADR 0004 Decision 2a, R1 and R2): each bundle's stored tier equals the tier its
  records show; an over-claim and an under-claim are rejected.
- Export: each bundle loads inside an internal ExportBundle.
- The last section holds three tests that are RED BY DESIGN: the bundle does not yet check a stored
  result against the run's inputs (finding B15, deferred by the architect "to the T-DEG work"). They
  fail with "DID NOT RAISE" today. See docs/phase1/api-surface-tdeg.md section 5.

All test names here are PROPOSED by qa (none is in SCOPE).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import new_id, schema_accepts
from ._support_b7 import export_doc
from ._support_b13 import (
    COLUMNS,
    TdegWorld,
    bundle_of,
    cached_world,
    rebuild_verdict,
)

DEPENDENT = (
    "goal_completion",
    "agent_audio_clarity",
    "agent_politeness",
    "turn_politeness_share",
    "script_consistency",
)
INDEPENDENT = (
    "p95_latency",
    "tool_call_success",
    "stage_ttfb_p95",
    "silence_ratio",
    "turn_length_share",
    "agent_input_tokens",
    "agent_turn_count",
)


def _results(world: TdegWorld) -> dict[str, Any]:
    bundle = bundle_of(world)
    by_id = {str(r.metric.id): r for r in bundle.results}
    return {str(d.key): by_id[str(d.id)] for d in bundle.snapshot.metric_definitions}


def _reasons(world: TdegWorld) -> dict[str, tuple[str, str | None, str | None]]:
    return {k: (r.status, r.na_reason, r.language_source) for k, r in _results(world).items()}


# ------------------------------------------------------------------- an untagged import


@pytest.mark.parametrize("column", ["T1", "T2"])
def test_deg_untagged_import_is_language_not_observed(column: str) -> None:
    """ADR 0004 Decision 6c to 6e, P-59 (proposed name): a telemetry or observer transcript with no
    language tag gives language_not_observed for every language-dependent metric whose inputs exist;
    the configured languages are not read. The language-independent metrics are scored, with no
    language provenance. Metrics whose inputs are missing keep their missing_input reason."""
    world = cached_world(column, language="untagged")
    got = _reasons(world)
    for key in ("goal_completion", "agent_audio_clarity", "agent_politeness", "script_consistency"):
        assert got[key] == ("not_applicable", "language_not_observed", None), key
    assert got["turn_politeness_share"] == ("not_applicable", "language_not_observed", None)
    for key in ("p95_latency", "tool_call_success", "stage_ttfb_p95", "silence_ratio"):
        assert got[key][0] == "scored", key
        assert got[key][2] is None
    assert got["turn_length_share"][0] == "scored"
    assert got["agent_wer"] == ("not_applicable", "missing_input", None)
    # the per-turn language-dependent result records that every turn was not applicable
    per_turn = _results(world)["turn_politeness_share"].per_turn
    assert per_turn is not None
    assert (per_turn.counted_turns, per_turn.total_turns, per_turn.share) == (0, 4, None)
    assert per_turn.counts.not_applicable == 4
    # nothing was assumed: no tag was written anywhere
    bundle = bundle_of(world)
    assert all(t.language is None for t in bundle.turns)
    assert all(t.detected_language is None for t in bundle.transcripts)
    # and the required goal-completion check could not run, so the verdict is inconclusive
    assert bundle.verdict.status == "inconclusive"
    assert bundle.verdict.not_applicable >= 2


# ------------------------------------------------------------------ a declared language


@pytest.mark.parametrize("column", ["T1", "T2"])
def test_deg_declared_language_path(column: str) -> None:
    """ADR 0004 Decision 6g, N14 (proposed name): the user declared mr-IN for an untagged import.
    Metrics that are not script-sensitive are scored in that language and say so
    (language_source=declared, the transcript listed); the script-sensitive metric never uses it
    (language_not_observed, no source, no ids); the language-independent ones are untouched; and no
    observed field was filled from the declaration."""
    world = cached_world(column, language="declared")
    bundle = bundle_of(world)
    results = _results(world)
    (transcript,) = bundle.transcripts
    assert transcript.declared_language is not None
    assert transcript.detected_language is None
    assert transcript.detected_language_raw is None
    assert all(t.language is None and t.language_raw is None for t in bundle.turns)
    for key in (
        "goal_completion",
        "agent_audio_clarity",
        "agent_politeness",
        "turn_politeness_share",
    ):
        result = results[key]
        assert result.status == "scored", key
        assert result.language_source == "declared", key
        assert list(result.declared_language_transcript_ids) == [transcript.id], key
    sensitive = results["script_consistency"]
    assert sensitive.status == "not_applicable"
    assert sensitive.na_reason == "language_not_observed"
    assert sensitive.language_source is None
    assert list(sensitive.declared_language_transcript_ids) == []
    for key in INDEPENDENT:
        assert results[key].language_source is None, key
        assert list(results[key].declared_language_transcript_ids) == [], key
    # the verdict is as at the tagged tier: the declaration lifted the language barrier
    tagged = bundle_of(cached_world(column))
    assert (bundle.verdict.passed, bundle.verdict.status) == (
        tagged.verdict.passed,
        tagged.verdict.status,
    )


def test_deg_declaration_is_refused_on_our_own_stt_transcript() -> None:
    """ADR 0004 Decision 6g (proposed name): at T0 the transcript is ours (our_stt), and our own
    producer records the tag, so a user declaration on it does not load."""
    world = cached_world("T0")
    doc = world.copy()
    doc["transcripts"][0].pop("detected_language", None)
    doc["transcripts"][0]["declared_language"] = {
        "language": "mr-IN",
        "source": "user_declared",
        "declared_by": "cli",
        "declared_at": "2026-10-23T08:00:00.000000Z",
    }
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)
    with pytest.raises(AssertionError):
        cached_world("T0", language="declared")


# ----------------------------------------------------------------- unsupported language


@pytest.mark.parametrize("column", ["T0", "T1", "T2"])
def test_deg_unsupported_language_path(column: str) -> None:
    """ADR 0004 Decision 6e step 2, I14 (proposed name): an observed tag outside the metrics' lists
    (ta-IN here; every metric covers en, hi and mr) gives unsupported_language, with the observed
    language_source, for the language-dependent metrics whose inputs exist; earlier reasons are not
    overridden (T0 keeps agent_channel_unknown and missing_input); the independent metrics score."""
    world = cached_world(column, language="unsupported_language")
    got = _reasons(world)
    for key in ("goal_completion", "script_consistency"):
        assert got[key] == ("not_applicable", "unsupported_language", "observed"), (column, key)
    if column == "T0":
        assert got["agent_audio_clarity"] == ("not_applicable", "agent_channel_unknown", None)
        assert got["agent_politeness"] == ("not_applicable", "missing_input", None)
    else:
        for key in ("agent_audio_clarity", "agent_politeness", "turn_politeness_share"):
            assert got[key] == ("not_applicable", "unsupported_language", "observed"), key
    for key in ("p95_latency", "silence_ratio"):
        assert got[key][0] == "scored"
    result = _results(world)["goal_completion"]
    assert result.value is None
    assert result.passed is None
    assert list(result.missing_kinds) == []
    # a required check that cannot apply: the verdict is inconclusive, not a fail
    verdict = bundle_of(world).verdict
    assert verdict.failed == 0
    assert verdict.status == "inconclusive"


def test_deg_unsupported_language_per_turn_metric_records_every_turn_as_not_applicable() -> None:
    """ADR 0010 Decision 3 and P-26 (proposed name): when every turn is outside the metric's
    languages, the per-turn metric is not_applicable with that reason, and its summary counts every
    turn as not applicable, with no share (never 0)."""
    result = _results(cached_world("T1", language="unsupported_language"))["turn_politeness_share"]
    assert result.status == "not_applicable"
    assert result.na_reason == "unsupported_language"
    assert result.per_turn is not None
    assert result.per_turn.share is None
    assert result.per_turn.counts.model_dump() == {
        "passed": 0,
        "failed": 0,
        "not_applicable": 4,
        "undetermined": 0,
    }
    assert result.value is None
    assert result.passed is None


# ------------------------------------------------------------------------- unsupported script


@pytest.mark.parametrize("column", ["T0", "T1"])
def test_deg_unsupported_script_path(column: str) -> None:
    """ADR 0004 Decision 7, I11 and I12 (proposed name): an observed romanized tag (hi-Latn) makes
    the script-sensitive metric unsupported_script and leaves the other language-dependent
    metrics, which are not script-sensitive, scored."""
    world = cached_world(column, language="romanized")
    got = _reasons(world)
    assert got["script_consistency"] == ("not_applicable", "unsupported_script", "observed")
    assert got["goal_completion"] == ("scored", None, "observed")
    assert got["p95_latency"][0] == "scored"


# ------------------------------------------------------------------------- mixed turn tags


def test_deg_one_foreign_turn_makes_the_conversation_metric_na_but_not_the_per_turn_one() -> None:
    """ADR 0004 Decision 6e, P-26 (proposed name): one agent turn tagged ta-IN makes a
    per-conversation metric that reads it not_applicable (scoring the other turns would be scoring a
    different input), while the per-turn share excludes that turn from its denominator and says
    so in its turn coverage."""
    world = cached_world("T1", language="mixed")
    results = _results(world)
    assert results["goal_completion"].na_reason == "unsupported_language"
    assert results["agent_politeness"].na_reason == "unsupported_language"
    per_turn = results["turn_politeness_share"]
    assert per_turn.status == "scored"
    summary = per_turn.per_turn
    assert summary is not None
    assert summary.counts.model_dump() == {
        "passed": 2,
        "failed": 1,
        "not_applicable": 1,
        "undetermined": 0,
    }
    assert (summary.counted_turns, summary.total_turns) == (3, 4)
    assert summary.share == "0.666666666666666667"
    assert per_turn.passed is False
    assert per_turn.value is None
    rendered = api("results").render_per_turn_share(per_turn)
    assert rendered == "turn share 0.67 (turn coverage 3/4 turns)"
    # the language-independent per-turn metric reads every agent turn
    assert results["turn_length_share"].per_turn.total_turns == 4


def test_deg_untagged_turns_come_before_a_foreign_turn_in_the_reason() -> None:
    """ADR 0004 Decision 4 and 6e (P-10, P-59; proposed name): one agent turn is tagged ta-IN
    (outside the lists) and the other turns have no language at all. The first step that applies
    is language_not_observed, so a metric that reads all turns says that, not unsupported_language;
    a metric that reads only the agent turns says language_not_observed too (three of its four
    turns are untagged). The per-turn share judges each turn on its own: the foreign turn is
    unsupported_language and the untagged ones language_not_observed, so with no counted turn the
    strongest reason (language_not_observed) is the result's."""
    world = cached_world("T1", language="untagged_foreign")
    got = _reasons(world)
    assert got["goal_completion"] == ("not_applicable", "language_not_observed", "observed")
    assert got["agent_politeness"] == ("not_applicable", "language_not_observed", "observed")
    assert got["script_consistency"] == ("not_applicable", "language_not_observed", "observed")
    per_turn = _results(world)["turn_politeness_share"]
    assert (per_turn.status, per_turn.na_reason) == ("not_applicable", "language_not_observed")
    assert per_turn.per_turn is not None
    assert per_turn.per_turn.counts.not_applicable == 4


# ------------------------------------------------------------------- the observed tier


def _records_of(bundle: Any) -> list[Any]:
    return [
        bundle.run,
        *bundle.transcripts,
        *bundle.turns,
        *bundle.exchanges,
        *bundle.tool_calls,
        *bundle.spans,
        *bundle.usage_records,
    ]


def test_deg_observed_tier_equals_the_tier_the_records_show() -> None:
    """ADR 0004 Decision 2a, R2: each bundle stores the tier its records' provenance fields show
    (T0 for both T0 columns, T1 for telemetry, T2 for the observer), the function agrees, and the
    check passes on all four."""
    expected = {"T0": "T0", "T0m": "T0", "T1": "T1", "T2": "T2"}
    bundle_api = api("bundle")
    for column in COLUMNS:
        bundle = bundle_of(cached_world(column))
        assert bundle.run.observed_tier == expected[column], column
        assert bundle_api.evidence_tier(_records_of(bundle)) == expected[column], column
        bundle_api.check_observed_tier(bundle.run, _records_of(bundle))


def test_deg_observed_tier_is_not_derived_from_which_records_are_empty() -> None:
    """ADR 0004 Decision 2a (proposed name): a T1 bundle without spans is still T1 (a telemetry
    transcript and telemetry tool calls show it), and a T2 bundle without spans is still T2."""
    for column in ("T1", "T2"):
        bundle = bundle_of(cached_world(column, spans=False))
        assert bundle.spans == []
        assert bundle.run.observed_tier == column
        assert api("bundle").evidence_tier(_records_of(bundle)) == column


@pytest.mark.parametrize(
    ("column", "stored"),
    [("T0", "T1"), ("T0", "T2"), ("T0m", "T1"), ("T1", "T2")],
)
def test_deg_observed_tier_over_claim_is_rejected(column: str, stored: str) -> None:
    """R2 (proposed name): a stored tier above the evidence tier, with no declaration of withheld
    records, does not load; the message names the stored and the computed tier."""
    doc = cached_world(column).copy()
    doc["run"]["observed_tier"] = stored
    with pytest.raises(ValidationError) as caught:
        api("bundle").RunBundle.model_validate(doc)
    text = str(caught.value)
    assert "[tier]" in text
    assert stored in text


@pytest.mark.parametrize(("column", "stored"), [("T1", "T0"), ("T2", "T1"), ("T2", "T0")])
def test_deg_observed_tier_under_claim_is_rejected(column: str, stored: str) -> None:
    """R2 (proposed name): a stored tier below the evidence tier does not load. With spans present
    the Run rejects T0 itself (R1); the T2 cases are R2."""
    doc = cached_world(column).copy()
    doc["run"]["observed_tier"] = stored
    with pytest.raises(ValidationError) as caught:
        api("bundle").RunBundle.model_validate(doc)
    assert "observed_tier" in str(caught.value) or "[tier]" in str(caught.value)


def test_deg_r1_spans_with_t0_are_rejected_on_the_run() -> None:
    """R1 (proposed name): ``spans`` in available_inputs with observed_tier T0 is rejected at the
    Run, whatever else the bundle holds."""
    run_doc = cached_world("T1").copy()["run"]
    run_doc["observed_tier"] = "T0"
    with pytest.raises(ValidationError) as caught:
        api("run").Run.model_validate(run_doc)
    assert any("observed_tier" in [str(p) for p in e["loc"]] for e in caught.value.errors())


def test_deg_a_telemetry_run_without_spans_stored_as_t0_is_rejected_by_r2() -> None:
    """R2 (proposed name): with no span the Run alone cannot see the contradiction; the bundle can,
    from the telemetry transcript and tool calls."""
    doc = cached_world("T1", spans=False).copy()
    assert "spans" not in doc["run"]["available_inputs"]
    api("run").Run.model_validate({**doc["run"], "observed_tier": "T0"})  # the Run accepts it
    doc["run"]["observed_tier"] = "T0"
    with pytest.raises(ValidationError) as caught:
        api("bundle").RunBundle.model_validate(doc)
    assert "[tier]" in str(caught.value)


def test_deg_withheld_declaration_excuses_an_over_claim_never_an_under_claim() -> None:
    """ADR 0004 Decision 2a, D3 (proposed name): a T1 bundle whose observer records were withheld
    may store T2 when it declares a withheld record of a provenance kind; a T2 bundle may not
    store T1 whatever it declares."""
    doc = cached_world("T1").copy()
    doc["run"]["observed_tier"] = "T2"
    doc["withheld"] = [{"kind": "turn", "record_id": new_id("turn")}]
    assert api("bundle").RunBundle.model_validate(doc).run.observed_tier == "T2"
    doc = cached_world("T2").copy()
    doc["run"]["observed_tier"] = "T1"
    doc["withheld"] = [{"kind": "turn", "record_id": new_id("turn")}]
    with pytest.raises(ValidationError) as caught:
        api("bundle").RunBundle.model_validate(doc)
    assert "[tier]" in str(caught.value)


@pytest.mark.parametrize(("column", "tier"), [("T0", "T0"), ("T1", "T1"), ("T2", "T2")])
def test_deg_each_provenance_field_shows_the_tier_on_its_own(column: str, tier: str) -> None:
    """ADR 0004 Decision 2a, R2 (proposed name): a transcript (without its timeline origin), a turn,
    an exchange and a tool call each show the tier of their closed provenance field alone; so the
    tier of a bundle cannot rest on a single record kind. T0 records show T0."""
    bundle = bundle_of(cached_world(column))
    bundle_api = api("bundle")
    transcript = bundle.transcripts[0].model_copy(update={"timeline_origin": None})
    records: dict[str, list[Any]] = {
        "transcript": [transcript],
        "turn": [bundle.turns[0]],
        "exchange": [bundle.exchanges[0]],
    }
    if bundle.tool_calls:
        call = bundle.tool_calls[0].model_copy(update={"timeline_origin": None})
        records["tool_call"] = [call]
    for kind, items in records.items():
        assert bundle_api.evidence_tier(items) == tier, (column, kind)
    if column != "T0":
        assert bundle_api.evidence_tier([bundle.spans[0]]) == "T1" if bundle.spans else True
        assert set(records) == {"transcript", "turn", "exchange", "tool_call"}


# ----------------------------------------------------------------------------- the export


@pytest.mark.parametrize("origin", ["simulated", "human"])
@pytest.mark.parametrize("column", COLUMNS)
def test_deg_internal_export_of_each_bundle_validates(column: str, origin: str) -> None:
    """P1-19 export check (proposed name): each bundle loads inside an internal ExportBundle, comes
    back unchanged, round-trips through JSON, and the JSON Schema accepts the export."""
    world = cached_world(column, origin=origin)
    doc = export_doc("internal", [world.doc])
    export_class = api("bundle").ExportBundle
    export = export_class.model_validate(doc)
    assert export.purpose == "internal"
    assert export.run_bundles == [bundle_of(world)]
    again = export_class.model_validate_json(export.model_dump_json(by_alias=True))
    assert again == export
    assert schema_accepts(export_class, doc)


def test_deg_one_internal_export_holds_all_tiers_side_by_side() -> None:
    """PROPOSED. The runs of the four columns go into one export (as a comparison across tiers);
    the export loads and keeps the order and the tiers."""
    worlds = [cached_world(c) for c in COLUMNS]
    doc = export_doc("internal", [w.doc for w in worlds])
    export = api("bundle").ExportBundle.model_validate(doc)
    assert [b.run.observed_tier for b in export.run_bundles] == ["T0", "T0", "T1", "T2"]
    assert [b.verdict.status for b in export.run_bundles] == [
        "inconclusive",
        "inconclusive",
        "pass",
        "pass",
    ]


# ---------------------------------------------------------- the bundle checks its results (B15)


def _forge_scored(result: dict[str, Any], scorer_ref: str | None) -> None:
    result.update(
        {
            "status": "scored",
            "value": True,
            "passed": True,
            "na_reason": None,
            "missing_kinds": [],
            "language_source": None,
            "declared_language_transcript_ids": [],
        }
    )
    if scorer_ref is not None:
        result["scorer"] = {"implementation_ref": scorer_ref, "version": "1.0.0"}


def _result_of(doc: dict[str, Any], world: TdegWorld, key: str) -> dict[str, Any]:
    wanted = next(d for d in world.definitions if d.key == key)
    return next(r for r in doc["results"] if r["metric"]["id"] == str(wanted.id))


def test_deg_bundle_rejects_a_scored_result_for_a_missing_input() -> None:
    """RED BY DESIGN (finding B15, deferred "to the T-DEG work"). ADR 0004 Decision 3: if
    ``requires - available_inputs`` is not empty the metric's result is not_applicable. A T0
    bundle whose run lacks tool_calls and tool_results but stores a scored tool_call_success
    (value true, passed true) contradicts its own run, and every consumer would read "the agent
    called the right tools" from a run that has no tool calls. The bundle must reject it.
    Today the RunBundle loads it: DID NOT RAISE."""
    world = cached_world("T0")
    doc = world.copy()
    _forge_scored(_result_of(doc, world, "tool_call_success"), "builtin:tool_call_success")
    rebuild_verdict(world, doc)  # keep the verdict consistent: the only fault is the result
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)


def test_deg_bundle_rejects_not_applicable_for_an_input_the_run_has() -> None:
    """RED BY DESIGN (B15). The converse: a T1 bundle whose run holds a transcript but stores
    goal_completion as not_applicable / missing_input [transcript] says the run lacks what it has,
    which would hide a metric that could run (and turn a pass into inconclusive). The bundle must
    reject it. Today it loads: DID NOT RAISE."""
    world = cached_world("T1")
    doc = world.copy()
    result = _result_of(doc, world, "goal_completion")
    result.update(
        {
            "status": "not_applicable",
            "value": None,
            "passed": None,
            "na_reason": "missing_input",
            "missing_kinds": ["transcript"],
            "language_source": None,
            "declared_language_transcript_ids": [],
            "judge": None,
        }
    )
    rebuild_verdict(world, doc)
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)


def test_deg_bundle_rejects_a_wrong_not_applicable_reason() -> None:
    """RED BY DESIGN (B15). ADR 0004 Decision 4 (precedence) and 5a: at T0 agent_politeness lacks
    agent_transcript, so its reason is missing_input with that kind. A stored reason of
    language_not_observed names a later step that never ran. The bundle must reject a reason that
    the run's inputs contradict. Today it loads: DID NOT RAISE."""
    world = cached_world("T0")
    doc = world.copy()
    result = _result_of(doc, world, "agent_politeness")
    assert result["na_reason"] == "missing_input"
    result["na_reason"] = "language_not_observed"
    result["missing_kinds"] = []
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)


def test_deg_bundle_rejects_a_scored_result_on_an_untagged_transcript() -> None:
    """RED BY DESIGN (B15, N29 a). ADR 0004 Decision 6e: on an untagged import a
    language-dependent metric is not_applicable / language_not_observed. A bundle that stores
    goal_completion as scored with language_source observed, over a transcript and turns that carry
    no tag, claims an observation nobody made. The bundle must reject it. Today it loads: DID NOT
    RAISE."""
    world = cached_world("T1", language="untagged")
    doc = world.copy()
    result = _result_of(doc, world, "goal_completion")
    assert result["na_reason"] == "language_not_observed"
    result.update(
        {
            "status": "scored",
            "value": True,
            "passed": True,
            "na_reason": None,
            "language_source": "observed",
            "judge": {
                "model_ref": {"provider": "openai", "model": "gpt-4.1"},
                "prompt_sha256": "cd" * 32,
                "params": {"temperature": 0},
                "per_turn": [],
            },
        }
    )
    rebuild_verdict(world, doc)
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)
