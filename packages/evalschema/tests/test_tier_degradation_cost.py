# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""T-DEG, part 2 (P1-19): the two cost domains at T0, T1 and T2.

ADR 0014 Decisions 12 to 16 and I22: the cost of the agent under test is ``not_available`` at T0
(a black box reports no usage; nothing is estimated from the length of a recording), computed from
telemetry usage at T1 and T2; the cost of our own evaluation (judge, simulated caller) is computed
or reported at every tier, identical across tiers, and never counted twice. The two domains
never mix.

Names from docs/phase1/SCOPE.md (T-DEG): test_deg_agent_cost_not_available_at_t0 and
test_deg_eval_cost_computed_all_tiers. Every other name is PROPOSED by qa.

Expected amounts are written out with ``Decimal`` here from the catalog prices of
``_support_b13._catalog`` (gpt-4.1 input 0.15 USD and output 0.6 USD per 1M tokens), not taken from
``compute_cost``.
"""

from __future__ import annotations

import decimal
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import registered_class
from ._support_b13 import COLUMNS, TdegWorld, bundle_of, cached_world

AGENT = "agent_under_test"
EVALUATION = "evaluation"
PER_MILLION = Decimal(1_000_000)


def _price(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * Decimal("0.15") / PER_MILLION
        + Decimal(output_tokens) * Decimal("0.6") / PER_MILLION
    )


def _records(world: TdegWorld, domain: str) -> list[Any]:
    return [r for r in bundle_of(world).cost_records if r.domain == domain]


def _summary(world: TdegWorld, domain: str) -> Any:
    found = [s for s in bundle_of(world).cost_summaries if s.domain == domain]
    assert len(found) == 1, (world.column, domain)
    return found[0]


def _amount(record: Any) -> Decimal:
    assert record.amount is not None
    assert record.amount.currency == "USD"
    return Decimal(record.amount.amount)


# ------------------------------------------------------------------ the agent, at T0


@pytest.mark.parametrize("origin", ["simulated", "human"])
@pytest.mark.parametrize("column", ["T0", "T0m"])
def test_deg_agent_cost_not_available_at_t0(column: str, origin: str) -> None:
    """ADR 0014 Decision 14, I22; SCOPE gate item 6: a black box reports no usage, so the agent's
    cost is not_available with usage_not_reported. The record has no amount, no basis, no reporter,
    no pricing snapshot, no rounding and no usage record; the summary has no total (not 0) and says
    that nothing is included."""
    world = cached_world(column, origin=origin)
    bundle = bundle_of(world)
    assert [u for u in bundle.usage_records if u.domain == AGENT] == []
    (record,) = _records(world, AGENT)
    assert record.status == "not_available"
    assert record.na_reason == "usage_not_reported"
    assert record.amount is None
    assert record.basis is None
    assert record.reported_by is None
    assert record.pricing_snapshot is None
    assert record.rounding is None
    assert record.reported_raw is None
    assert list(record.usage_record_ids) == []
    assert list(record.unpriced_units) == []
    summary = _summary(world, AGENT)
    assert summary.total is None
    assert list(summary.included) == []
    assert summary.completeness == "none"
    assert [(e.ref, e.reason, list(e.covered_by)) for e in summary.excluded] == [
        (record.id, "usage_not_reported", [])
    ]
    # the bundle's JSON never shows an amount for the agent: no 0, no estimate
    dumped = bundle.model_dump(mode="json", by_alias=True)
    for cost in dumped["cost_records"]:
        if cost["domain"] == AGENT:
            assert cost["amount"] is None
            assert cost["basis"] is None
    for summary_doc in dumped["cost_summaries"]:
        if summary_doc["domain"] == AGENT:
            assert summary_doc["total"] is None


def test_deg_agent_cost_t0_is_not_estimated_from_the_recording() -> None:
    """ADR 0014 Decisions 14 and P-39 (proposed name): the length of the recording is never the
    agent's usage, and usage of the agent that we "measured" does not load. The T0 bundle holds an
    audio artifact and no agent usage; agent usage with source=measured is rejected."""
    world = cached_world("T0")
    bundle = bundle_of(world)
    assert len(bundle.artifacts) == 1
    assert not [u for u in bundle.usage_records if u.domain == AGENT]
    doc = world.copy()
    doc["usage_records"].append(
        {
            **doc["usage_records"][0],
            "id": "use_01a16a5c-0000-7000-8000-000000000001",
            "domain": AGENT,
            "stage": "stt",
            "provider": "agentco",
            "source": "measured",
            "quantities": [{"unit": "audio_input_seconds", "count": "93.5"}],
        }
    )
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)


def test_deg_agent_cost_t0_reported_in_unsupported_currency_keeps_the_raw_values() -> None:
    """ADR 0014 Decisions 1a and 12 to 12c, I22 (proposed name): a black box whose API reports
    usage (no model) and a cost in INR. The cost is not_available, ``reported_raw`` keeps the
    lexeme and the label exactly as sent (``12.50``, not ``12.5``; nothing converted), no amount or
    basis is shown, and the usage that cannot be priced without a model is listed. The run is still
    T0 (provider-reported usage is not telemetry evidence)."""
    world = cached_world("T0", agent_cost="reported_inr")
    bundle = bundle_of(world)
    assert bundle.run.observed_tier == "T0"
    assert (
        api("bundle").evidence_tier(
            [
                bundle.run,
                *bundle.transcripts,
                *bundle.turns,
                *bundle.exchanges,
                *bundle.usage_records,
            ]
        )
        == "T0"
    )
    (usage,) = [u for u in bundle.usage_records if u.domain == AGENT]
    assert usage.source == "provider_reported"
    assert usage.model is None
    assert "usage" in bundle.run.available_inputs  # evidence, not the tier label, decides
    records = _records(world, AGENT)
    by_reason = {r.na_reason: r for r in records}
    assert set(by_reason) == {"no_price_for_model", "unsupported_currency"}
    assert all(r.status == "not_available" for r in records)
    for record in records:
        assert record.amount is None
        assert record.basis is None
        assert record.reported_by is None
        assert record.pricing_snapshot is None
        assert record.rounding is None
    raw = by_reason["unsupported_currency"].reported_raw
    assert raw is not None
    assert raw.model_dump(mode="json", exclude_none=True) == {
        "reported_by": "provider",
        "amount_raw": "12.50",
        "currency_raw": "INR",
        "currency_source": "payload",
    }
    assert by_reason["no_price_for_model"].reported_raw is None
    assert list(by_reason["no_price_for_model"].usage_record_ids) == [usage.id]
    summary = _summary(world, AGENT)
    assert summary.total is None
    assert summary.completeness == "none"
    assert sorted(e.reason for e in summary.excluded) == [
        "no_price_for_model",
        "unsupported_currency",
    ]
    # the informational cost-dependent metric reads the usage that exists
    result = {
        str(d.key): r
        for d in bundle.snapshot.metric_definitions
        for r in bundle.results
        if r.metric.id == d.id
    }
    assert result["agent_input_tokens"].status == "scored"
    assert result["agent_input_tokens"].value == 300


# ------------------------------------------------------------------ the agent, at T1 and T2


@pytest.mark.parametrize("column", ["T1", "T2"])
def test_deg_agent_cost_computed_from_telemetry_usage_at_t1_and_t2(column: str) -> None:
    """ADR 0014 Decisions 13 and 14 (proposed name): with telemetry usage and a catalog price the
    agent's cost is computed, in USD, with the pricing snapshot it used; the amount is the exact
    decimal of tokens times price."""
    world = cached_world(column)
    bundle = bundle_of(world)
    llm_usage = [u for u in bundle.usage_records if u.domain == AGENT and u.stage == "llm"]
    assert len(llm_usage) == 1
    assert llm_usage[0].source == "telemetry"
    tokens = {q.unit: int(q.count) for q in llm_usage[0].quantities}
    expected = _price(tokens["input_tokens"], tokens["output_tokens"])
    computed = [r for r in _records(world, AGENT) if r.status == "available"]
    assert len(computed) == 1
    (record,) = computed
    assert record.basis == "computed"
    assert record.reported_by is None
    assert _amount(record) == expected
    assert expected > 0
    assert record.amount.amount == format(expected.normalize(), "f")
    assert list(record.usage_record_ids) == [llm_usage[0].id]
    snapshot = record.pricing_snapshot
    assert snapshot is not None
    catalog = bundle.pricing_catalogs[0]
    assert (snapshot.catalog_id, snapshot.version) == (catalog.id, catalog.version)
    assert snapshot.content_digest == catalog.content_digest
    assert list(snapshot.entry_keys) == ["gpt41-in", "gpt41-out"]
    assert expected == {"T1": Decimal("0.000147"), "T2": Decimal("0.000186")}[column]


def test_deg_agent_cost_t1_summary_is_complete_and_t2_is_partial() -> None:
    """ADR 0014 Decisions 13a and 16 (proposed name): at T1 the one agent usage is priced, so the
    summary is complete. At T2 the observer also reports TTS characters, for which the catalog has
    no price: that usage's cost is not_available (no_price_for_model, no partial sum), the LLM
    cost is the total, and the summary is partial because the unpriced record is not covered."""
    t1 = _summary(cached_world("T1"), AGENT)
    assert t1.completeness == "complete"
    assert t1.excluded == []
    assert Decimal(t1.total.amount) == Decimal("0.000147")
    t2_world = cached_world("T2")
    t2 = _summary(t2_world, AGENT)
    assert t2.completeness == "partial"
    assert Decimal(t2.total.amount) == Decimal("0.000186")
    unpriced = [r for r in _records(t2_world, AGENT) if r.status == "not_available"]
    assert [r.na_reason for r in unpriced] == ["no_price_for_model"]
    (record,) = unpriced
    assert record.amount is None
    assert [(e.ref, e.reason, list(e.covered_by)) for e in t2.excluded] == [
        (record.id, "no_price_for_model", [])
    ]
    # the total is exactly the priced part; the unpriced characters add nothing, not a guess
    assert t2.total.amount == "0.000186"


def test_deg_agent_cost_differs_by_tier_only_in_availability() -> None:
    """PROPOSED. For the agent domain, T0 has no amount, T1 and T2 have one: the only difference is
    availability (the record type, the summary type and the field set are the same)."""
    totals = {c: _summary(cached_world(c), AGENT).total for c in COLUMNS}
    assert totals["T0"] is None
    assert totals["T0m"] is None
    assert totals["T1"] is not None
    assert totals["T2"] is not None
    for column in COLUMNS:
        for record in _records(cached_world(column), AGENT):
            dumped = record.model_dump(mode="json", by_alias=True)
            assert set(dumped) == set(
                registered_class("CostRecord").model_json_schema()["properties"]
            )


# --------------------------------------------------------------- our own cost, at every tier


@pytest.mark.parametrize("origin", ["simulated", "human"])
@pytest.mark.parametrize("column", COLUMNS)
def test_deg_eval_cost_computed_all_tiers(column: str, origin: str) -> None:
    """ADR 0014 Decision 14, I22, SCOPE T-DEG: our own evaluation cost is computed at every tier.
    The 1M-token simulated-caller call is 0.15 USD from the catalog, in every column; the judge
    call, which the provider reported at 1.5 USD, has a computed estimate of the same call
    (0.00027 USD). A human-caller run has no simulated caller and so no 0.15 record."""
    world = cached_world(column, origin=origin)
    records = _records(world, EVALUATION)
    computed = [r for r in records if r.basis == "computed"]
    assert all(r.status == "available" for r in records)
    amounts = sorted(_amount(r) for r in computed)
    expected = [_price(1000, 200)]
    if origin == "simulated":
        expected.append(_price(1_000_000, 0))
    assert amounts == sorted(expected)
    assert Decimal("0.15") in amounts or origin == "human"
    for record in computed:
        assert record.pricing_snapshot is not None
        assert record.reported_by is None
    reported = [r for r in records if r.basis == "reported"]
    assert [(r.reported_by, _amount(r)) for r in reported] == [("provider", Decimal("1.5"))]
    summary = _summary(world, EVALUATION)
    assert summary.completeness == "complete"
    assert Decimal(summary.total.amount) == (
        Decimal("1.65") if origin == "simulated" else Decimal("1.5")
    )


def test_deg_eval_cost_is_recomputed_exactly_from_the_bundle() -> None:
    """ADR 0014 Decision 15 (proposed name): ``compute_cost`` over the bundle's evaluation usage and
    catalog reproduces each stored computed record exactly, at every tier (pure, no clock, no
    tier input)."""
    cost_api = api("cost")
    context = decimal.Context(prec=120, rounding=decimal.ROUND_HALF_EVEN)
    for column in COLUMNS:
        world = cached_world(column)
        bundle = bundle_of(world)
        usage_by_id = {u.id: u for u in bundle.usage_records}
        catalog = bundle.pricing_catalogs[0]
        for record in bundle.cost_records:
            if record.basis != "computed":
                continue
            again = cost_api.compute_cost(
                [usage_by_id[i] for i in record.usage_record_ids],
                catalog,
                domain=record.domain,
                record_id=record.id,
                created_at=record.created_at,
                run_started_at=bundle.run.started_at,
                context=context,
            )
            assert again == record, (column, record.domain)


def test_deg_eval_cost_identical_across_tiers() -> None:
    """PROPOSED. Our own cost does not depend on the capability tier: the same totals, the same
    completeness and the same multiset of amounts at T0, T0m, T1 and T2."""
    seen = []
    for column in COLUMNS:
        world = cached_world(column)
        summary = _summary(world, EVALUATION)
        amounts = sorted(
            (r.basis, _amount(r)) for r in _records(world, EVALUATION) if r.status == "available"
        )
        seen.append((summary.total.amount, summary.completeness, len(summary.included), amounts))
    assert all(item == seen[0] for item in seen)


# -------------------------------------------------------------- no double counting, no mixing


@pytest.mark.parametrize("column", COLUMNS)
def test_deg_no_double_counting_in_the_evaluation_summary(column: str) -> None:
    """ADR 0014 Decision 16, P-54 (proposed name): the judge call has a reported cost and a
    computed estimate. Only the reported one is included; the estimate is excluded as
    duplicate_of_reported, covered by the reported record. The total is 1.65, not 1.65027, and no
    usage record is priced twice among the included records."""
    world = cached_world(column)
    summary = _summary(world, EVALUATION)
    records = {r.id: r for r in _records(world, EVALUATION)}
    reported = next(r for r in records.values() if r.basis == "reported")
    estimate = next(
        r
        for r in records.values()
        if r.basis == "computed" and list(r.usage_record_ids) == list(reported.usage_record_ids)
    )
    assert [(e.ref, e.reason, list(e.covered_by)) for e in summary.excluded] == [
        (estimate.id, "duplicate_of_reported", [reported.id])
    ]
    assert reported.id in summary.included
    assert estimate.id not in summary.included
    assert Decimal(summary.total.amount) == Decimal("1.65")
    included_usage = [u for i in summary.included for u in records[i].usage_record_ids]
    assert len(included_usage) == len(set(included_usage))
    assert sum(_amount(records[i]) for i in summary.included) == Decimal(summary.total.amount)
    api("cost").check_cost_summary(summary, list(records.values()))


def test_deg_without_the_estimate_the_total_is_the_same() -> None:
    """PROPOSED. Leaving out the duplicate estimate does not change the evaluation total: the
    estimate never counted."""
    with_dup = _summary(cached_world("T1"), EVALUATION)
    without = _summary(cached_world("T1", with_duplicate_eval_cost=False), EVALUATION)
    assert with_dup.total == without.total
    assert without.excluded == []


def test_deg_forged_double_count_is_rejected_by_the_bundle() -> None:
    """ADR 0014 Decision 16b (proposed name): a summary that includes both the reported record and
    the computed estimate of one call (and so counts it twice) does not load."""
    world = cached_world("T1")
    doc = world.copy()
    summary = next(s for s in doc["cost_summaries"] if s["domain"] == EVALUATION)
    estimate_id = summary["excluded"][0]["ref"]
    summary["excluded"] = []
    summary["included"].append(estimate_id)
    summary["total"] = {"amount": "1.65027", "currency": "USD"}
    with pytest.raises(ValidationError) as caught:
        api("bundle").RunBundle.model_validate(doc)
    assert "[summary]" in str(caught.value)


def test_deg_cost_domains_are_never_mixed() -> None:
    """ADR 0014 Decision 14, N57 (proposed name): the two domains have their own records and their
    own summary; the agent's cost is not part of our total (the evaluation total is the same at T0
    with no agent cost and at T1 with one), and a summary that names a record of the other domain
    does not load."""
    for column in COLUMNS:
        world = cached_world(column)
        bundle = bundle_of(world)
        domain_of = {r.id: r.domain for r in bundle.cost_records}
        for summary in bundle.cost_summaries:
            named = [*summary.included, *(e.ref for e in summary.excluded)]
            assert {domain_of[i] for i in named} == {summary.domain}, (column, summary.domain)
        usage_domain = {u.id: u.domain for u in bundle.usage_records}
        for record in bundle.cost_records:
            assert {usage_domain[i] for i in record.usage_record_ids} <= {record.domain}
    assert (
        _summary(cached_world("T0"), EVALUATION).total
        == _summary(cached_world("T1"), EVALUATION).total
    )
    # forge: the agent's record listed in the evaluation summary
    world = cached_world("T1")
    doc = world.copy()
    agent_record = next(r for r in doc["cost_records"] if r["domain"] == AGENT)
    summary = next(s for s in doc["cost_summaries"] if s["domain"] == EVALUATION)
    summary["included"].append(agent_record["id"])
    # the total is made consistent, so that only the domain rule can reject the summary
    summary["total"] = {
        "amount": format(
            (
                Decimal(summary["total"]["amount"]) + Decimal(agent_record["amount"]["amount"])
            ).normalize(),
            "f",
        ),
        "currency": "USD",
    }
    with pytest.raises(ValidationError) as caught:
        api("bundle").RunBundle.model_validate(doc)
    assert "[summary]" in str(caught.value)
    # at the bundle the rejection is "not supplied" (the check reads the records of the summary's
    # domain only); the domain rule itself is in the two cost functions, called with both domains
    bundle = bundle_of(world)
    cost_api = api("cost")
    evaluation = next(s for s in bundle.cost_summaries if s.domain == EVALUATION)
    with pytest.raises(ValueError, match="domain"):
        cost_api.check_cost_summary(evaluation, list(bundle.cost_records))
    agent_records = [r for r in bundle.cost_records if r.domain == AGENT]
    with pytest.raises(ValueError, match="domain"):
        cost_api.summarize_costs(
            agent_records,
            domain=EVALUATION,
            summary_id=api("ids").CostSummaryId.new(),
            created_at=datetime(2026, 10, 23, tzinfo=UTC),
        )


def test_deg_not_available_cost_never_has_an_amount_basis_or_zero() -> None:
    """SCOPE gate item 6, ADR 0014 Decision 12 (proposed name): in every bundle of the matrix a
    not_available cost has no amount, no basis, no reporter, no snapshot and no rounding, and a
    summary with nothing included has no total (never 0). A forged zero amount or a zero total
    does not load."""
    seen = 0
    for column in COLUMNS:
        for origin in ("simulated", "human"):
            bundle = bundle_of(cached_world(column, origin=origin))
            for record in bundle.cost_records:
                if record.status == "not_available":
                    seen += 1
                    assert record.na_reason is not None
                    for name in ("amount", "basis", "reported_by", "pricing_snapshot", "rounding"):
                        assert getattr(record, name) is None, (column, name)
            for summary in bundle.cost_summaries:
                if not summary.included:
                    assert summary.total is None
                    assert summary.completeness == "none"
    assert seen == 6  # T0 and T0m (both origins) and the unpriced TTS usage at T2
    world = cached_world("T0")
    doc = world.copy()
    record = next(r for r in doc["cost_records"] if r["domain"] == AGENT)
    record["amount"] = {"amount": "0", "currency": "USD"}
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)
    record["amount"] = None
    record["basis"] = "computed"
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)
    doc = world.copy()
    summary = next(s for s in doc["cost_summaries"] if s["domain"] == AGENT)
    summary["total"] = {"amount": "0", "currency": "USD"}
    with pytest.raises(ValidationError):
        api("bundle").RunBundle.model_validate(doc)


def test_deg_no_usage_reason_depends_on_the_domain() -> None:
    """ADR 0014 Decision 13a step 1 (proposed name): with no usage record the agent's cost is
    usage_not_reported and our own is no_usage; both are not_available, never 0."""
    cost_api = api("cost")
    ids = api("ids")
    catalog = bundle_of(cached_world("T1")).pricing_catalogs[0]
    context = decimal.Context(prec=60, rounding=decimal.ROUND_HALF_EVEN)
    for domain, reason in ((AGENT, "usage_not_reported"), (EVALUATION, "no_usage")):
        record = cost_api.compute_cost(
            [],
            catalog,
            domain=domain,
            record_id=ids.CostRecordId.new(),
            created_at=datetime(2026, 10, 23, tzinfo=UTC),
            context=context,
        )
        assert (record.status, record.na_reason, record.amount, record.basis) == (
            "not_available",
            reason,
            None,
            None,
        )


def test_deg_forged_summary_total_is_rejected() -> None:
    """ADR 0014 Decision 16b (proposed name): a summary whose total is not the exact sum of the
    included amounts does not load, at every tier (only the total is wrong here)."""
    for column in ("T0", "T1", "T2"):
        world = cached_world(column)
        doc = world.copy()
        summary = next(s for s in doc["cost_summaries"] if s["domain"] == EVALUATION)
        assert summary["total"]["amount"] == "1.65"
        summary["total"] = {"amount": "1.66", "currency": "USD"}
        with pytest.raises(ValidationError) as caught:
            api("bundle").RunBundle.model_validate(doc)
        assert "[summary]" in str(caught.value), column
        assert "total" in str(caught.value), column
