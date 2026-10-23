# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-25, part 3: ``resolve_price()`` and ``compute_cost()`` (ADR 0014 Decisions 13, 14 and 15,
P-35, P-36, P-37, P-39; hypothesis for the exactness properties).

Assumed public API (docs/phase1/api-surface.md section 23.1), ``tracer_evalschema.cost``:

* ``PriceFacts`` (``NamedTuple``: ``context_tokens``, ``batch``, ``region``, each None when not
  known) and ``PriceResolution`` (``NamedTuple``: ``entry``, ``na_reason``);
* ``resolve_price(catalog, *, provider, model, unit, occurred_at=None, run_started_at=None,
  facts=PriceFacts())`` (revision 15, N43 and detail D9: the price date of the one usage that is
  priced is its ``occurred_at``, else ``run_started_at``, else unknown);
* ``compute_cost(usages, catalog, *, domain, record_id, created_at, context, run_started_at=None,
  facts=PriceFacts()) -> CostRecord``, with a required ``decimal.Context``; the argument
  ``usage_times`` is withdrawn (a ``TypeError``): each usage record carries its own
  ``occurred_at``. The pricing snapshot names the entries used by ``entry_key`` (N61).

Both are pure: ``Decimal`` only, no clock, no I/O, no global state. Names are SCOPE section 5 names
unless the docstring says "Proposed name". A failure of these tests is about the cost module only.
"""

from __future__ import annotations

import decimal
from fractions import Fraction
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ._support import api
from ._support_b2 import schema_accepts
from ._support_b4 import (
    billing,
    catalog_of,
    compute,
    cost_class,
    cost_fn,
    make_catalog,
    make_usage,
    qty,
    usage_for,
    usd,
    when,
)

JAN = "2026-01-01T00:00:00.000000Z"
JUN = "2026-06-01T00:00:00.000000Z"
TOKENS = 1_000_000


def _facts(**kw: Any) -> Any:
    return cost_class("PriceFacts")(**kw)


def _resolve(catalog: Any, **over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4.1",
        "unit": "input_tokens",
    }
    kwargs.update(over)
    return cost_fn("resolve_price")(catalog, **kwargs)


def _two_prices() -> Any:
    """Input tokens of gpt-4.1: 1 USD per 1M until June 2026, 2 USD per 1M from then on."""
    return catalog_of(
        {"price": usd("1"), "effective_from": JAN, "effective_to": JUN},
        {"price": usd("2"), "effective_from": JUN},
    )


def _assert_na(record: Any, reason: str) -> None:
    """A not-available record: the reason, and nothing that could read as a cost."""
    assert record.status == "not_available"
    assert record.na_reason == reason
    assert record.amount is None
    assert record.basis is None
    assert record.pricing_snapshot is None
    assert record.reported_by is None
    assert record.rounding is None
    assert record.reported_raw is None


def _assert_amount(record: Any, amount: str) -> None:
    assert record.status == "available"
    assert record.basis == "computed"
    assert record.amount.amount == amount
    assert record.amount.currency == "USD"
    assert record.na_reason is None
    assert record.reported_by is None
    assert record.unpriced_units == []
    assert record.reported_raw is None


# ---------------------------------------------------------------------------- exactness


def test_compute_exact() -> None:
    """ADR 0014 I12 (golden): 1,234,567 input tokens at 0.15 USD per 1M tokens cost exactly
    "0.18518505" USD. The result is a computed, available record in the evaluation domain with the
    given ID and time, the usage record's ID, a pricing snapshot naming the catalog (ID, version,
    content digest) and the entry used, no rounding record, and amount text in canonical form."""
    catalog = make_catalog()
    usage = usage_for("input_tokens", 1_234_567)
    record_id = api("ids").CostRecordId.new()
    record = compute([usage], catalog, record_id=record_id)
    _assert_amount(record, "0.18518505")
    assert isinstance(record, cost_class("CostRecord"))
    assert record.id == record_id
    assert record.domain == "evaluation"
    assert record.created_at == when("2026-10-23T00:00:00Z")
    assert record.usage_record_ids == [usage.id]
    assert record.rounding is None
    snapshot = record.pricing_snapshot
    assert snapshot.catalog_id == catalog.id
    assert snapshot.version == catalog.version
    assert snapshot.content_digest == catalog.content_digest
    assert snapshot.entry_keys == [catalog.entries[0].entry_key]
    cost_fn("check_pricing_snapshot")(snapshot, catalog.model_dump(mode="json"))
    assert schema_accepts(type(record), record.model_dump(mode="json"))
    assert type(record).model_validate(record.model_dump(mode="json")) == record


def test_compute_multi_unit_and_multi_usage_sum() -> None:
    """Proposed name (ADR 0014 Decisions 9 and 15): the buckets are disjoint and priced one by
    one, so cache reads, cache writes, uncached input and output tokens each use their own entry,
    and the cost of a record is the exact sum over its quantities and over its usage records.
    1,000,000 input at 2.5, 2,000,000 cache-read at 0.25 and 500,000 output at 10 USD per 1M cost
    2.5 + 0.5 + 5 = 8 USD; a second usage record of 1,000,000 input tokens adds 2.5."""
    catalog = catalog_of(
        {"unit": "input_tokens", "price": usd("2.5")},
        {"unit": "cache_read_input_tokens", "price": usd("0.25")},
        {"unit": "cache_write_input_tokens", "price": usd("3.125")},
        {"unit": "output_tokens", "price": usd("10")},
    )
    first = make_usage(
        quantities=[
            qty("input_tokens", TOKENS),
            qty("cache_read_input_tokens", 2 * TOKENS),
            qty("output_tokens", TOKENS // 2),
        ]
    )
    record = compute([first], catalog)
    _assert_amount(record, "8")
    assert len(record.pricing_snapshot.entry_keys) == 3
    second = usage_for("input_tokens", TOKENS)
    both = compute([first, second], catalog)
    _assert_amount(both, "10.5")
    used = both.pricing_snapshot.entry_keys
    assert len(used) == len(set(used)) == 3, "an entry used twice is named once (P reading, N51)"
    assert both.usage_record_ids == [first.id, second.id]
    written = compute([usage_for("cache_write_input_tokens", TOKENS)], catalog)
    _assert_amount(written, "3.125")


def test_billing_increment_and_minimum_applied() -> None:
    """ADR 0014 I11, Decision 15 (golden): per billable event the quantity is rounded up to the
    billing increment, then multiplied by ``price / unit_quantity``, then the minimum charge is
    applied (the larger of the two, not added). At 0.0072 USD per 60 seconds with a 15-second
    increment and a 0.005 USD minimum: 40 s bills as 45 s and costs 0.0054; a 3 s call bills as
    15 s, costs 0.0018 and is lifted to the minimum 0.005; 15 s exactly stays 15 s; 16 s bills as
    30 s; 600 s costs 0.072. Without a minimum the 3 s call costs 0.0018; without an increment and
    a minimum it costs 3 s at face value, 0.00036. Fractional seconds and a fractional increment
    work the same way."""
    seconds = "audio_input_seconds"

    def entry(increment: Any, minimum: Any) -> dict[str, Any]:
        return {
            "unit": seconds,
            "unit_quantity": "60",
            "price": usd("0.0072"),
            "billing": billing(increment, minimum),
        }

    def cost(entry_over: dict[str, Any], count: str) -> Any:
        return compute([usage_for(seconds, count, stage="stt")], catalog_of(entry_over))

    full = entry("15", usd("0.005"))
    for count, expected in (
        ("40", "0.0054"),
        ("3", "0.005"),
        ("15", "0.005"),
        ("16", "0.005"),
        ("46", "0.0072"),
        ("600", "0.072"),
        ("15.5", "0.005"),
    ):
        _assert_amount(cost(full, count), expected)
    no_minimum = entry("15", "none")
    for count, expected in (
        ("3", "0.0018"),
        ("15", "0.0018"),
        ("16", "0.0036"),
        ("15.5", "0.0036"),
    ):
        _assert_amount(cost(no_minimum, count), expected)
    _assert_amount(cost(entry("none", "none"), "3"), "0.00036")
    _assert_amount(cost(entry("none", usd("0.005")), "3"), "0.005")
    _assert_amount(cost(entry("none", usd("0.0001")), "3"), "0.00036")
    _assert_amount(cost(entry("0.5", "none"), "3.2"), "0.00042")
    _assert_amount(cost(entry("30", usd("0.001")), "100"), "0.0144")


def test_billing_increment_per_billable_event() -> None:
    """Proposed name (ADR 0014 Decision 15: "computation per billable event"): increment and
    minimum apply to each usage record on its own, not to the sum of several. Two usage records of
    10 s with a 30 s increment bill 30 s each (60 s, not 30 s), and two 3 s calls with a 0.005
    USD minimum cost 0.005 each. P reading, section 23.3 N55."""
    seconds = "audio_input_seconds"
    entry = {
        "unit": seconds,
        "unit_quantity": "60",
        "price": usd("0.0072"),
        "billing": billing("30", "none"),
    }
    pair = [usage_for(seconds, "10", stage="stt"), usage_for(seconds, "10", stage="stt")]
    _assert_amount(compute(pair, catalog_of(entry)), "0.0072")
    minimum = {**entry, "billing": billing("15", usd("0.005"))}
    short = [usage_for(seconds, "3", stage="stt"), usage_for(seconds, "3", stage="stt")]
    _assert_amount(compute(short, catalog_of(minimum)), "0.01")


def test_compute_scale_overflow_rounds_half_even_and_records() -> None:
    """ADR 0014 Decision 15, I24, P-35: a result beyond scale 18 is rounded half-even at scale 18
    and the record says so with ``rounding = {scale: 18, mode: half_even, exact: false}``. 1 USD
    per 3 requests: one request is 0.333... and becomes "0.333333333333333333", two requests are
    0.666... and become "0.666666666666666667" (rounded, not truncated). The ties: 2.5e-18 rounds
    to 2e-18 and 1.5e-18 to 2e-18 (half-even, where half-up would give 3e-18 for the first).
    Rounding happens once, on the record's amount: two usage records of one request each total
    0.666666666666666667, not 2 x 0.333333333333333333 (P reading, section 23.3 N56). A result
    that is exact is not marked as rounded."""
    third = catalog_of({"unit": "requests", "unit_quantity": "3", "price": usd("1")})
    one = compute([usage_for("requests", 1)], third)
    assert one.amount.amount == "0.333333333333333333"
    assert one.rounding is not None
    assert (one.rounding.scale, one.rounding.mode, one.rounding.exact) == (18, "half_even", False)
    two = compute([usage_for("requests", 2)], third)
    assert two.amount.amount == "0.666666666666666667"
    assert two.rounding is not None
    assert two.rounding.exact is False
    split = compute([usage_for("requests", 1), usage_for("requests", 1)], third)
    assert split.amount.amount == "0.666666666666666667"
    assert split.rounding is not None
    exact = compute([usage_for("requests", 3)], third)
    _assert_amount(exact, "1")
    assert exact.rounding is None

    def tie(price: str) -> Any:
        catalog = catalog_of({"unit": "requests", "unit_quantity": "10", "price": usd(price)})
        return compute([usage_for("requests", 1)], catalog)

    for price, expected in (
        ("0.000000000000000025", "0.000000000000000002"),
        ("0.000000000000000015", "0.000000000000000002"),
        ("0.000000000000000035", "0.000000000000000004"),
        ("0.000000000000000045", "0.000000000000000004"),
    ):
        rounded = tie(price)
        assert rounded.amount.amount == expected, price
        assert rounded.rounding is not None
        assert rounded.rounding.exact is False
    quarter = compute(
        [usage_for("requests", 1)],
        catalog_of({"unit": "requests", "unit_quantity": "4", "price": usd("1")}),
    )
    _assert_amount(quarter, "0.25")
    assert quarter.rounding is None
    eighteen = compute(
        [usage_for("requests", 1)],
        catalog_of(
            {"unit": "requests", "unit_quantity": "1", "price": usd("0.000000000000000001")}
        ),
    )
    _assert_amount(eighteen, "0.000000000000000001")
    assert eighteen.rounding is None


def test_compute_matches_independent_decimal_property() -> None:
    """Proposed name (hypothesis, ADR 0014 I12, I21): for random counts, prices of up to eight
    fractional digits and unit quantities that divide a power of ten, the amount equals an
    independent exact rational computation, with no rounding record; for any unit quantity from 1
    to 999 the amount equals the exact rational rounded half-even at scale 18, and ``rounding`` is
    present exactly when that rounding changed the value."""

    def canonical(numerator: int, scale: int) -> str:
        integer, fraction = divmod(numerator, 10**scale)
        text = str(integer) if scale == 0 else f"{integer}.{fraction:0{scale}d}".rstrip("0")
        return text[:-1] if text.endswith(".") else text

    @settings(derandomize=True, max_examples=60, deadline=None)
    @given(
        st.integers(min_value=1, max_value=10**12),
        st.integers(min_value=0, max_value=10**9),
        st.integers(min_value=0, max_value=8),
        st.sampled_from(["1", "10", "1000", "1000000"]),
    )
    def exact(count: int, price_units: int, price_scale: int, unit_quantity: str) -> None:
        price = canonical(price_units, price_scale)
        catalog = catalog_of({"unit_quantity": unit_quantity, "price": usd(price)})
        record = compute([usage_for("input_tokens", count)], catalog)
        expected = Fraction(count) * Fraction(price_units, 10**price_scale) / int(unit_quantity)
        scaled = expected * 10**18
        assert scaled.denominator == 1
        assert record.amount.amount == canonical(int(scaled), 18)
        assert record.rounding is None

    @settings(derandomize=True, max_examples=60, deadline=None)
    @given(
        st.integers(min_value=1, max_value=10_000),
        st.integers(min_value=1, max_value=10**9),
        st.integers(min_value=0, max_value=8),
        st.integers(min_value=1, max_value=999),
    )
    def rounded(count: int, price_units: int, price_scale: int, unit_quantity: int) -> None:
        price = canonical(price_units, price_scale)
        catalog = catalog_of(
            {"unit": "requests", "unit_quantity": str(unit_quantity), "price": usd(price)}
        )
        record = compute([usage_for("requests", count)], catalog)
        scaled = Fraction(count) * Fraction(price_units, 10**price_scale) / unit_quantity * 10**18
        assert record.amount.amount == canonical(round(scaled), 18)
        assert (record.rounding is None) == (scaled.denominator == 1)
        if record.rounding is not None:
            assert (record.rounding.scale, record.rounding.mode, record.rounding.exact) == (
                18,
                "half_even",
                False,
            )

    exact()
    rounded()


def test_compute_is_pure_and_needs_an_explicit_context() -> None:
    """Proposed name (ADR 0014 Decision 15: pure, ``Decimal`` only, an explicit
    ``decimal.Context``, no I/O): the context is a required keyword (omitting it is a
    ``TypeError``); the result does not depend on the thread's current decimal context (a context
    with 3 digits of precision, round-down and traps set around the call changes nothing), and the
    inputs are not changed."""
    catalog = make_catalog()
    usage = usage_for("input_tokens", 1_234_567)
    compute_cost = cost_fn("compute_cost")
    with pytest.raises(TypeError):
        compute_cost(
            [usage],
            catalog,
            domain="evaluation",
            record_id=api("ids").CostRecordId.new(),
            created_at=when("2026-10-23T00:00:00Z"),
            run_started_at=when("2026-10-20T00:00:00Z"),
        )
    before = (usage.model_dump(mode="json"), catalog.model_dump(mode="json"))
    reference = compute([usage], catalog).amount.amount
    third = catalog_of({"unit": "requests", "unit_quantity": "3", "price": usd("1")})
    hostile = decimal.Context(
        prec=3,
        rounding=decimal.ROUND_DOWN,
        traps=[decimal.Inexact, decimal.Rounded, decimal.InvalidOperation, decimal.Overflow],
    )
    with decimal.localcontext(hostile):
        inside = compute([usage], catalog).amount.amount
        rounded = compute([usage_for("requests", 2)], third).amount.amount
    assert reference == inside == "0.18518505"
    assert rounded == "0.666666666666666667"
    assert (usage.model_dump(mode="json"), catalog.model_dump(mode="json")) == before
    again = compute([usage], catalog, context=decimal.Context(prec=200))
    assert again.amount.amount == reference


# ---------------------------------------------------------------------- price date (P-36)


def test_price_resolution_uses_usage_time() -> None:
    """ADR 0014 Decision 15, I24, P-36; revision 15, N43: the price is the one effective at the
    usage event's time (``UsageRecord.occurred_at``) when the source reports it, and
    ``Run.started_at`` is not consulted then. With 1 USD per 1M
    until 2026-06-01 and 2 USD per 1M from then: a usage in July costs 2 although the run started
    in January, a usage in February costs 1 although the run started in August. Ranges are
    half-open: exactly at 2026-06-01T00:00:00Z the new price applies, one microsecond earlier the
    old one; before the first ``effective_from`` there is no price. Two usage records at different
    times are priced at their own times."""
    catalog = _two_prices()
    old, new = catalog.entries
    january, august = when("2026-01-15T00:00:00Z"), when("2026-08-15T00:00:00Z")

    def cost(at: str, started: Any = None) -> Any:
        usage = usage_for("input_tokens", TOKENS, occurred_at=when(at))
        return compute([usage], catalog, run_started_at=started)

    july = cost("2026-07-15T00:00:00Z", january)
    _assert_amount(july, "2")
    assert july.pricing_snapshot.entry_keys == [new.entry_key]
    february = cost("2026-02-15T00:00:00Z", august)
    _assert_amount(february, "1")
    assert february.pricing_snapshot.entry_keys == [old.entry_key]
    _assert_amount(cost("2026-06-01T00:00:00Z", january), "2")
    _assert_amount(cost("2026-05-31T23:59:59.999999Z", august), "1")
    _assert_amount(cost("2026-01-01T00:00:00Z"), "1")
    _assert_na(cost("2025-12-31T23:59:59.999999Z", august), "no_price_for_model")
    direct = _resolve(catalog, occurred_at=when("2026-07-15T00:00:00Z"), run_started_at=january)
    assert direct.entry is not None
    assert direct.entry.entry_key == new.entry_key
    assert direct.na_reason is None
    first = usage_for("input_tokens", TOKENS, occurred_at=when("2026-02-01T00:00:00Z"))
    second = usage_for("input_tokens", TOKENS, occurred_at=when("2026-07-01T00:00:00Z"))
    both = compute([first, second], catalog, run_started_at=None)
    _assert_amount(both, "3")
    assert sorted(both.pricing_snapshot.entry_keys) == sorted([old.entry_key, new.entry_key])
    # N43: the argument ``usage_times`` is withdrawn, the time is a field of the usage record
    with pytest.raises(TypeError):
        compute([first], catalog, usage_times={first.id: when("2026-07-01T00:00:00Z")})
    with pytest.raises(TypeError):
        _resolve(catalog, usage_time=when("2026-07-15T00:00:00Z"))


def test_price_resolution_falls_back_to_run_started_at() -> None:
    """ADR 0014 Decision 15, I28, P-36: when the source gives no time for a usage, the price date
    is ``Run.started_at`` (passed as ``run_started_at``). A run that started in February uses the
    old price, one that started in August the new price, and in one call a usage with its own time
    and a usage without one are each priced by their own rule."""
    catalog = _two_prices()
    usage = usage_for("input_tokens", TOKENS)
    _assert_amount(compute([usage], catalog, run_started_at=when("2026-02-01T00:00:00Z")), "1")
    _assert_amount(compute([usage], catalog, run_started_at=when("2026-08-01T00:00:00Z")), "2")
    _assert_amount(compute([usage], catalog, run_started_at=when("2026-06-01T00:00:00Z")), "2")
    timed = usage_for("input_tokens", TOKENS, occurred_at=when("2026-02-01T00:00:00Z"))
    untimed = usage_for("input_tokens", TOKENS)
    mixed = compute([timed, untimed], catalog, run_started_at=when("2026-08-01T00:00:00Z"))
    _assert_amount(mixed, "3")
    direct = _resolve(catalog, run_started_at=when("2026-02-01T00:00:00Z"))
    assert direct.entry is not None
    assert direct.entry.price.amount == "1"


def test_price_resolution_without_any_date_is_ambiguous() -> None:
    """ADR 0014 Decision 15, I28, P-36: with neither a usage time nor ``Run.started_at`` the price
    date is unknown and the result is not_available with ``ambiguous_price``, never "now": the
    catalog's open-ended entry is effective today and is still not used, and a catalog with a
    single open-ended entry behaves the same. One usage without any date makes the whole record
    not_available."""
    for catalog in (_two_prices(), make_catalog()):
        usage = usage_for("input_tokens", TOKENS)
        _assert_na(compute([usage], catalog, run_started_at=None), "ambiguous_price")
        direct = _resolve(catalog)
        assert direct.entry is None
        assert direct.na_reason == "ambiguous_price"
    catalog = _two_prices()
    dated = usage_for("input_tokens", TOKENS, occurred_at=when("2026-07-01T00:00:00Z"))
    undated = usage_for("input_tokens", TOKENS)
    record = compute([dated, undated], catalog, run_started_at=None)
    _assert_na(record, "ambiguous_price")
    assert record.usage_record_ids == [dated.id, undated.id]


# ------------------------------------------------------------------- applies_when (P-37)


def test_unevaluable_applies_when_ambiguous() -> None:
    """ADR 0014 Decision 11, I24, P-37: a condition the resolver cannot evaluate from the facts it
    was given makes the price ambiguous (``ambiguous_price``), it is never guessed. Tiered prices:
    one entry for ``context_tokens_le`` 200000 at 1 USD and one for ``context_tokens_gt`` 200000 at
    2 USD per 1M. Without ``context_tokens`` the result is ambiguous_price; with 150000 and 200000
    the first tier; with 200001 the second. A ``batch`` entry needs the batch fact and a ``region``
    entry the region (compared exactly): unknown gives ambiguous_price, a known fact that does not
    match gives no_price_for_model, a match gives the entry. An entry without conditions needs no
    facts. This test is listed under P1-24 in SCOPE but needs the resolver of P1-25 (section
    23.3 N46)."""
    low = {"price": usd("1"), "applies_when": [{"kind": "context_tokens_le", "value": 200000}]}
    high = {"price": usd("2"), "applies_when": [{"kind": "context_tokens_gt", "value": 200000}]}
    tiers = catalog_of(low, high)
    lower, upper = tiers.entries
    usage = usage_for("input_tokens", TOKENS)
    unknown = compute([usage], tiers, facts=_facts())
    _assert_na(unknown, "ambiguous_price")
    default = compute([usage], tiers)
    _assert_na(default, "ambiguous_price")
    only_unrelated = compute([usage], tiers, facts=_facts(batch=False, region="us"))
    _assert_na(only_unrelated, "ambiguous_price")
    direct = _resolve(tiers, run_started_at=when("2026-02-01T00:00:00Z"))
    assert direct.entry is None
    assert direct.na_reason == "ambiguous_price"
    for tokens, entry, amount in (
        (150_000, lower, "1"),
        (200_000, lower, "1"),
        (200_001, upper, "2"),
        (0, lower, "1"),
    ):
        record = compute([usage], tiers, facts=_facts(context_tokens=tokens))
        _assert_amount(record, amount)
        assert record.pricing_snapshot.entry_keys == [entry.entry_key], tokens
    batch_only = catalog_of({"applies_when": [{"kind": "batch", "value": True}]})
    _assert_na(compute([usage], batch_only), "ambiguous_price")
    _assert_na(compute([usage], batch_only, facts=_facts(batch=False)), "no_price_for_model")
    _assert_amount(compute([usage], batch_only, facts=_facts(batch=True)), "0.15")
    regional = catalog_of({"applies_when": [{"kind": "region", "value": "us"}]})
    _assert_na(compute([usage], regional), "ambiguous_price")
    _assert_na(compute([usage], regional, facts=_facts(region="eu")), "no_price_for_model")
    _assert_na(compute([usage], regional, facts=_facts(region="US")), "no_price_for_model")
    _assert_amount(compute([usage], regional, facts=_facts(region="us")), "0.15")
    plain = make_catalog()
    _assert_amount(compute([usage], plain, facts=_facts()), "0.15")
    facts = _facts()
    assert (facts.context_tokens, facts.batch, facts.region) == (None, None, None)


# --------------------------------------------------------------------- not available


def test_no_price_not_available_never_zero() -> None:
    """ADR 0014 I13, A-14: no matching price gives a not_available record with
    ``no_price_for_model``, never an amount of 0: no ``amount``, no ``basis``, no snapshot, and
    the usage record is still listed. The same holds when the catalog prices only other models,
    other providers, or the model only at a time outside every range. By contrast an explicit
    price of 0 is a known price and gives an available record with amount "0"."""
    catalog = catalog_of(
        {"model": "gpt-4.1-mini"},
        {"provider": "anthropic"},
        {"effective_from": "2026-12-01T00:00:00.000000Z"},
    )
    usage = usage_for("input_tokens", TOKENS)
    record = compute([usage], catalog)
    _assert_na(record, "no_price_for_model")
    assert record.usage_record_ids == [usage.id]
    assert record.unpriced_units == []
    assert record.model_dump(mode="json")["amount"] is None
    other = compute([make_usage(model="llama-3-70b")], make_catalog())
    _assert_na(other, "no_price_for_model")
    free = compute([usage], catalog_of({"price": usd("0")}))
    _assert_amount(free, "0")
    assert free.amount is not None
    assert type(record).model_validate(record.model_dump(mode="json")) == record
    direct = _resolve(catalog, run_started_at=when("2026-02-01T00:00:00Z"), model="llama-3")
    assert direct.entry is None
    assert direct.na_reason == "no_price_for_model"


def test_usage_without_model_is_not_available_never_zero() -> None:
    """M3 (owner, auto-approved; ADR 0014 Decisions 13 and 15): a usage record whose source reported
    no model (``model`` None) cannot be matched to any price entry, so the cost is not_available
    with ``no_price_for_model``: never an amount of 0, no basis, no snapshot, the usage record still
    listed. This holds whatever the catalog holds (an entry for every model of the provider, an
    entry for the same unit, a price of 0), for a call dated by ``occurred_at`` or by the run start,
    for several units and several records, in both domains. The same usage with a model named still
    prices, so the reason is the missing model and nothing else; and the result is a valid record
    that survives a JSON round trip."""
    rich = catalog_of(
        {"model": "gpt-4.1", "price": usd("0")},
        {"model": "gpt-4.1-mini"},
        {"unit": "output_tokens", "model": "gpt-4.1"},
        {"provider": "kokoro", "model": "k-1", "unit": "input_characters"},
    )
    for catalog in (make_catalog(), rich, catalog_of({"price": usd("0")})):
        usage = make_usage(model=None, quantities=[qty("input_tokens", TOKENS)])
        assert usage.model is None
        record = compute([usage], catalog)
        _assert_no_cost(record, "no_price_for_model")
        assert record.usage_record_ids == [usage.id]
        assert record.unpriced_units == []
        assert record.model_dump(mode="json")["amount"] is None
        assert type(record).model_validate(record.model_dump(mode="json")) == record
        assert schema_accepts(type(record), record.model_dump(mode="json"))
    undated = make_usage(model=None, occurred_at="2026-02-01T00:00:00Z")
    _assert_no_cost(compute([undated], make_catalog()), "no_price_for_model")
    _assert_no_cost(compute([undated], make_catalog(), run_started_at=None), "no_price_for_model")
    many = make_usage(
        model=None,
        quantities=[qty("input_tokens", TOKENS), qty("output_tokens", 1000), qty("requests", 1)],
    )
    _assert_no_cost(compute([many], make_catalog()), "no_price_for_model")
    twice = compute(
        [make_usage(model=None), make_usage(model=None, provider="anthropic")], make_catalog()
    )
    _assert_no_cost(twice, "no_price_for_model")
    assert len(twice.usage_record_ids) == 2
    agent = make_usage(
        model=None,
        domain="agent_under_test",
        source="telemetry",
        stage="tts",
        quantities=[qty("input_characters", 11)],
    )
    _assert_no_cost(
        compute([agent], catalog_of({"unit": "input_characters"}), domain="agent_under_test"),
        "no_price_for_model",
    )
    named = make_usage(model="gpt-4.1", quantities=[qty("input_tokens", TOKENS)])
    _assert_amount(compute([named], make_catalog()), "0.15")
    unnamed = make_usage(model=None, quantities=[qty("input_tokens", TOKENS)])
    assert unnamed.model_dump(mode="json")["model"] is None


def test_mixed_model_and_modelless_usage_is_never_a_partial_sum() -> None:
    """M3: one record of a cost has a usage with a model (priced) and one without (cannot be
    priced). The cost is not_available with no amount; the priced part is never reported as a
    partial sum (the rule of I15). The reason of a mixed set is ``unit_unpriced`` with
    ``unpriced_units == ["input_tokens"]`` (A1/AMB-1, auto-approved): the units that have at
    least one unpriced quantity; the amount stays absent."""
    priced = make_usage(model="gpt-4.1", quantities=[qty("input_tokens", TOKENS)])
    unpriced = make_usage(model=None, quantities=[qty("input_tokens", TOKENS)])
    for order in ([priced, unpriced], [unpriced, priced]):
        record = compute(order, make_catalog())
        assert record.status == "not_available"
        assert record.na_reason in {"no_price_for_model", "unit_unpriced"}
        # A1/AMB-1 (pinned): the mixed set follows the partial-units rule, and unpriced_units
        # names the units with at least one unpriced quantity.
        assert record.na_reason == "unit_unpriced"
        assert record.unpriced_units == ["input_tokens"]
        assert record.amount is None
        assert record.model_dump(mode="json")["amount"] is None
        assert record.basis is None
        assert record.pricing_snapshot is None
        assert sorted(map(str, record.usage_record_ids)) == sorted(
            map(str, [priced.id, unpriced.id])
        )


def test_partial_units_not_available() -> None:
    """ADR 0014 Decision 13, I15: if any unit of the usage is unpriced the whole record is
    not_available with ``unit_unpriced`` and lists ``unpriced_units``; the priced part is never
    reported as a partial sum. Input tokens are priced, output tokens and cache reads are not:
    no amount, both units listed."""
    catalog = catalog_of({"unit": "input_tokens"})
    usage = make_usage(
        quantities=[
            qty("input_tokens", TOKENS),
            qty("output_tokens", 1000),
            qty("cache_read_input_tokens", 5000),
        ]
    )
    record = compute([usage], catalog)
    _assert_na(record, "unit_unpriced")
    assert sorted(record.unpriced_units) == ["cache_read_input_tokens", "output_tokens"]
    assert record.usage_record_ids == [usage.id]
    one = compute(
        [make_usage(quantities=[qty("input_tokens", TOKENS), qty("output_tokens", 7)])], catalog
    )
    _assert_na(one, "unit_unpriced")
    assert one.unpriced_units == ["output_tokens"]
    priced = compute([make_usage(quantities=[qty("input_tokens", TOKENS)])], catalog)
    _assert_amount(priced, "0.15")
    mixed = compute([usage_for("input_tokens", TOKENS), usage_for("output_tokens", 5)], catalog)
    _assert_na(mixed, "unit_unpriced")
    assert mixed.unpriced_units == ["output_tokens"]


def test_missing_billing_rules_not_available() -> None:
    """ADR 0014 Decision 13, I10: a price whose billing rules are unknown cannot give a computed
    cost, because increments and minimums change the result. The record is not_available (no
    amount, no basis, no snapshot), also when other units of the same usage are priced with full
    rules: a partial sum is never reported. The ``unpriced_units`` list stays empty, because the
    unit has a price."""
    catalog = catalog_of(
        {"unit": "input_tokens"},
        {"unit": "output_tokens", "billing": "unknown"},
    )
    usage = make_usage(quantities=[qty("input_tokens", TOKENS), qty("output_tokens", 1000)])
    record = compute([usage], catalog)
    _assert_na(record, "billing_rules_missing")
    assert record.unpriced_units == []
    assert record.usage_record_ids == [usage.id]
    assert record.amount is None
    allowed = compute([usage_for("input_tokens", TOKENS)], catalog)
    _assert_amount(allowed, "0.15")


def test_billing_unknown_gives_billing_rules_missing() -> None:
    """ADR 0014 Decision 11, Decision 13, I10: a matching entry with ``billing: "unknown"`` gives
    exactly ``billing_rules_missing`` (not no_price_for_model, not unit_unpriced), for catalog and
    user-supplied entries; the explicit rules "none"/"none" are known and give an amount."""
    for source in ("catalog", "user_supplied"):
        catalog = catalog_of({"billing": "unknown", "price_source": source})
        record = compute([usage_for("input_tokens", TOKENS)], catalog)
        _assert_na(record, "billing_rules_missing")
    known = catalog_of({"billing": billing("none", "none")})
    _assert_amount(compute([usage_for("input_tokens", TOKENS)], known), "0.15")


def test_ambiguous_match_not_available() -> None:
    """ADR 0014 Decision 15, I18: more than one matching entry gives not_available with
    ``ambiguous_price``, never the first match or the cheapest. Three ways to match twice:
    the model name of one entry is an alias of another entry (the key differs, so the catalog
    is valid), an alias is shared by two entries, and an unconditional entry together with a
    ``batch`` entry when the batch fact is true."""
    usage = usage_for("input_tokens", TOKENS)
    alias_clash = catalog_of(
        {"model": "gpt-4.1", "price": usd("1")},
        {"model": "gpt-4.1-mini", "aliases": ["gpt-4.1"], "price": usd("2")},
    )
    record = compute([usage], alias_clash)
    _assert_na(record, "ambiguous_price")
    assert record.usage_record_ids == [usage.id]
    shared = catalog_of(
        {"model": "model-a", "aliases": ["gpt-4.1"], "price": usd("1")},
        {"model": "model-b", "aliases": ["gpt-4.1"], "price": usd("2")},
    )
    _assert_na(compute([usage], shared), "ambiguous_price")
    discount = catalog_of(
        {"price": usd("1")},
        {"price": usd("0.5"), "applies_when": [{"kind": "batch", "value": True}]},
    )
    _assert_na(compute([usage], discount, facts=_facts(batch=True)), "ambiguous_price")
    _assert_amount(compute([usage], discount, facts=_facts(batch=False)), "1")
    direct = _resolve(alias_clash, run_started_at=when("2026-02-01T00:00:00Z"))
    assert direct.entry is None
    assert direct.na_reason == "ambiguous_price"


def test_exact_model_match_no_fuzzy_matching() -> None:
    """Proposed name (ADR 0014 Decision 11 and rejected alternative "fuzzy model matching"): price
    resolution matches provider, model (or an explicit alias), unit and date exactly. A prefix,
    a longer name, another case, another provider spelling or a substring does not match
    ("a wrong price is worse than no price"); an explicit alias does."""
    catalog = catalog_of({"model": "gpt-4.1", "aliases": ["gpt-4.1-2025-04-14"]})
    for model, matches in (
        ("gpt-4.1", True),
        ("gpt-4.1-2025-04-14", True),
        ("gpt-4.1-2025-04-15", False),
        ("gpt-4.1-mini", False),
        ("gpt-4", False),
        ("gpt-4.1.1", False),
        ("GPT-4.1", False),
    ):
        record = compute(
            [make_usage(model=model, quantities=[qty("input_tokens", TOKENS)])], catalog
        )
        if matches:
            _assert_amount(record, "0.15")
        else:
            _assert_na(record, "no_price_for_model")
    other_provider = compute([make_usage(provider="OpenAI")], catalog)
    _assert_na(other_provider, "no_price_for_model")
    prefix = compute([make_usage(provider="open")], catalog)
    _assert_na(prefix, "no_price_for_model")


# ------------------------------------------------------------------------ domain rules


def test_agent_cost_not_available_without_reported_usage() -> None:
    """ADR 0014 Decision 14, I19: agent-under-test costs are computed only from usage reported by
    the provider or the telemetry. With no reported usage the cost is not_available with
    ``usage_not_reported``, no usage record listed, no amount: the catalog may hold a price for the
    agent's model, but a price alone is not usage. The evaluation domain with no usage is
    ``no_usage``."""
    catalog = make_catalog()
    record = compute([], catalog, domain="agent_under_test")
    _assert_na(record, "usage_not_reported")
    assert record.usage_record_ids == []
    assert record.domain == "agent_under_test"
    evaluation = compute([], catalog, domain="evaluation")
    _assert_na(evaluation, "no_usage")
    assert evaluation.usage_record_ids == []


def test_recording_duration_not_accepted_as_agent_stt_usage() -> None:
    """ADR 0014 Decision 14, I19: a recording's duration is never accepted as the agent's STT
    usage (the provider may bill differently). A measured audio-seconds usage of the
    agent-under-test domain cannot be built (P-39), and a measured evaluation-domain usage of the
    same recording cannot be passed as the agent's cost either: a usage record of another domain
    is a ``ValueError`` ("domain"), so no agent cost is computed from a duration."""
    catalog = catalog_of(
        {
            "unit": "audio_input_seconds",
            "unit_quantity": "60",
            "price": usd("0.0072"),
            "billing": billing("15", usd("0.005")),
        }
    )
    with pytest.raises(ValidationError):
        make_usage(
            domain="agent_under_test",
            source="measured",
            stage="stt",
            quantities=[qty("audio_input_seconds", "63.5")],
        )
    measured = make_usage(
        domain="evaluation",
        source="measured",
        stage="stt",
        quantities=[qty("audio_input_seconds", "63.5")],
    )
    with pytest.raises(ValueError, match=r"(?i)domain"):
        compute([measured], catalog, domain="agent_under_test")
    own = compute([measured], catalog, domain="evaluation")
    _assert_amount(own, "0.009")


def test_black_box_llm_tokens_not_available() -> None:
    """ADR 0014 Decision 14, I19, T0: LLM tokens of a black-box agent are not reported, so the
    agent's cost is not_available with ``usage_not_reported``, never estimated from the transcript
    or the recording: an agent-domain computation without usage is not_available even when the
    evaluation domain has usage of its own for the same call (our judge tokens are not the
    agent's)."""
    catalog = make_catalog()
    ours = usage_for("input_tokens", TOKENS, domain="evaluation", stage="judge")
    assert ours.domain == "evaluation"
    record = compute([], catalog, domain="agent_under_test")
    _assert_na(record, "usage_not_reported")
    mine = compute([ours], catalog, domain="evaluation")
    assert mine.status == "available"
    with pytest.raises(ValueError, match=r"(?i)domain"):
        compute([ours], catalog, domain="agent_under_test")


def test_agent_domain_computed_from_telemetry_usage() -> None:
    """Proposed name (ADR 0014 Decision 14): agent-under-test costs are computed from
    telemetry-reported or provider-reported usage; both sources give an available computed record
    in the agent domain."""
    catalog = make_catalog()
    for source in ("telemetry", "provider_reported"):
        usage = usage_for(
            "input_tokens", TOKENS, domain="agent_under_test", source=source, stage="llm"
        )
        record = compute([usage], catalog, domain="agent_under_test")
        _assert_amount(record, "0.15")
        assert record.domain == "agent_under_test"


def test_compute_cost_domain_mismatch_rejected() -> None:
    """Proposed name (ADR 0014 Decision 12: a cost record has one ``domain``): every usage record
    must be of the record's domain; an evaluation usage under the agent domain, and the reverse, is
    a ``ValueError`` that says "domain". P reading, section 23.3 N57."""
    catalog = make_catalog()
    agent = usage_for(
        "input_tokens", TOKENS, domain="agent_under_test", source="telemetry", stage="llm"
    )
    ours = usage_for("input_tokens", TOKENS)
    for usages, domain in (
        ([ours], "agent_under_test"),
        ([agent], "evaluation"),
        ([ours, agent], "evaluation"),
    ):
        with pytest.raises(ValueError, match=r"(?i)domain"):
            compute(usages, catalog, domain=domain)


def test_user_supplied_price_is_computed_with_price_source() -> None:
    """ADR 0014 Decision 14, I20: a user-supplied price yields ``basis=computed``, never a new
    basis; ``price_source=user_supplied`` stays on the entry that the pricing snapshot names (and
    the entry's provenance note is kept), so a reader can tell it from a catalog price. The cost
    record has no ``price_source`` field of its own."""
    note = "converted at 83.10 INR per USD on 2026-10-01"
    catalog = catalog_of(
        {
            "price_source": "user_supplied",
            "price": usd("0.2"),
            "provenance": {
                "kind": "user_supplied",
                "retrieved_at": "2026-10-01T00:00:00.000000Z",
                "note": note,
            },
        }
    )
    record = compute([usage_for("input_tokens", TOKENS)], catalog)
    _assert_amount(record, "0.2")
    assert record.basis == "computed"
    (entry_key,) = record.pricing_snapshot.entry_keys
    named = {e.entry_key: e for e in catalog.entries}[entry_key]
    assert named.price_source == "user_supplied"
    assert named.provenance.note == note
    assert "price_source" not in type(record).model_fields
    assert "price_source" not in record.model_dump(mode="json")
    catalog_price = compute([usage_for("input_tokens", TOKENS)], make_catalog())
    assert catalog_price.basis == "computed"
    assert {e.entry_key: e for e in make_catalog().entries}.keys() != {entry_key}


def test_explicit_zero_price_gives_available_zero() -> None:
    """Proposed name (ADR 0014 Decision 11: an explicit price of 0 is a known price): a free tier
    priced at 0 gives an available computed record with amount "0" and a snapshot naming the
    entry, which is not the same as no price (no amount)."""
    catalog = catalog_of({"price": usd("0")})
    record = compute([usage_for("input_tokens", TOKENS)], catalog)
    _assert_amount(record, "0")
    assert record.pricing_snapshot.entry_keys == [catalog.entries[0].entry_key]
    missing = compute([usage_for("input_tokens", TOKENS)], catalog_of({"model": "other"}))
    _assert_na(missing, "no_price_for_model")


def test_resolve_price_result_shape() -> None:
    """Proposed name (section 23.1): ``resolve_price`` returns a ``PriceResolution`` with exactly
    one of ``entry`` (a ``PriceEntry`` of the catalog) and ``na_reason`` (``no_price_for_model`` or
    ``ambiguous_price``) set; the unit is matched exactly, so an entry for another unit is not a
    match."""
    catalog = make_catalog()
    hit = _resolve(catalog, run_started_at=when("2026-02-01T00:00:00Z"))
    assert hit.entry is not None
    assert hit.na_reason is None
    assert hit.entry == catalog.entries[0]
    assert hit.entry.entry_key == catalog.entries[0].entry_key
    miss = _resolve(catalog, run_started_at=when("2026-02-01T00:00:00Z"), unit="output_tokens")
    assert miss.entry is None
    assert miss.na_reason == "no_price_for_model"
    assert set(type(hit)._fields) == {"entry", "na_reason"}
    undated = _resolve(catalog)
    assert undated.entry is None
    assert undated.na_reason == "ambiguous_price"


# ------------------------------------------------------------- N54, N45 (b), N63, N49


def _assert_no_cost(record: Any, reason: str) -> None:
    """Not available with the reason, no amount (never "0") and no pricing snapshot."""
    _assert_na(record, reason)
    assert record.model_dump(mode="json")["amount"] is None
    assert record.pricing_snapshot is None


def test_not_available_reason_precedence() -> None:
    """N54 (owner, accepted; ADR 0014 Decision 13a, detail D11; api-surface 23.6 group F item 1):
    when causes mix, ``compute_cost`` resolves every quantity (one usage record and one unit) on
    its own and gives the first reason that applies, only that one: no usage (``no_usage`` for
    the evaluation domain, ``usage_not_reported`` for the agent, whatever the catalog), then
    ``no_price_for_model`` (every quantity unpriced, also a model priced only for another unit),
    then ``billing_rules_missing`` (some quantity has an entry with unknown billing), then
    ``unit_unpriced`` (some but not all unpriced; only the unpriced units are listed), then
    ``ambiguous_price``. Each case is one record."""
    ambiguous_output = (
        {"unit": "output_tokens", "model": "gpt-4.1", "price": usd("1")},
        {
            "unit": "output_tokens",
            "model": "gpt-4.1-mini",
            "aliases": ["gpt-4.1"],
            "price": usd("2"),
        },
    )
    both = make_usage(quantities=[qty("input_tokens", 10), qty("output_tokens", 10)])
    only_output = make_usage(quantities=[qty("output_tokens", 10)])
    only_input = make_usage(quantities=[qty("input_tokens", 10)])
    # every quantity unpriced
    none_priced = compute([both], catalog_of({"unit": "cache_read_input_tokens"}))
    _assert_no_cost(none_priced, "no_price_for_model")
    assert none_priced.unpriced_units == []
    other_unit = compute([only_output], catalog_of({"unit": "input_tokens"}))
    _assert_no_cost(other_unit, "no_price_for_model")
    assert other_unit.unpriced_units == []
    # unknown billing outranks an unpriced unit and an ambiguous unit
    unknown_and_unpriced = compute(
        [both], catalog_of({"unit": "input_tokens", "billing": "unknown"})
    )
    _assert_no_cost(unknown_and_unpriced, "billing_rules_missing")
    assert unknown_and_unpriced.unpriced_units == []
    unknown_and_ambiguous = compute(
        [both], catalog_of({"unit": "input_tokens", "billing": "unknown"}, *ambiguous_output)
    )
    _assert_no_cost(unknown_and_ambiguous, "billing_rules_missing")
    assert unknown_and_ambiguous.unpriced_units == []
    # an unpriced unit outranks an ambiguous one and lists only the unpriced unit
    unpriced_and_ambiguous = compute([both], catalog_of(*ambiguous_output))
    _assert_no_cost(unpriced_and_ambiguous, "unit_unpriced")
    assert unpriced_and_ambiguous.unpriced_units == ["input_tokens"]
    # a priced unit plus an ambiguous unit, and only ambiguous units
    priced_and_ambiguous = compute([both], catalog_of({"unit": "input_tokens"}, *ambiguous_output))
    _assert_no_cost(priced_and_ambiguous, "ambiguous_price")
    assert priced_and_ambiguous.unpriced_units == []
    _assert_no_cost(compute([only_output], catalog_of(*ambiguous_output)), "ambiguous_price")
    # no usage whatever the catalog
    for catalog in (
        catalog_of(*ambiguous_output),
        make_catalog(),
        catalog_of({"billing": "unknown"}),
    ):
        _assert_no_cost(compute([], catalog, domain="evaluation"), "no_usage")
        _assert_no_cost(compute([], catalog, domain="agent_under_test"), "usage_not_reported")
    # the order does not depend on the order of the quantities or of the usage records
    swapped = make_usage(quantities=[qty("output_tokens", 10), qty("input_tokens", 10)])
    _assert_no_cost(
        compute([swapped], catalog_of({"unit": "input_tokens", "billing": "unknown"})),
        "billing_rules_missing",
    )
    _assert_no_cost(
        compute([only_input, only_output], catalog_of(*ambiguous_output)), "unit_unpriced"
    )


def test_zero_quantity_costs_zero_without_minimum() -> None:
    """N45 (b), N63 (owner; ADR 0014 Decision 15; api-surface 23.6 groups N and U): a quantity of
    exactly 0 contributes exactly 0: neither the increment nor the minimum applies to it, because
    nothing was consumed. It is still resolved like any quantity (N63): it needs a usable price
    with known billing rules and its entry is named in ``pricing_snapshot.entry_keys``. (i)
    ``audio_input_seconds: "0"`` priced with a 15 s increment and a 0.01 USD minimum plus
    ``requests: 1`` priced without a minimum cost exactly the ``requests`` amount, both keys
    named. (ii) ``input_tokens: 1000`` plus ``cache_read_input_tokens: 0`` with a minimum on the
    cache entry cost exactly the input amount, both keys named. (iii) A usage of zero quantities
    only, every unit priced with ``BillingRules``, is available, computed, amount "0", with no
    ``rounding`` and both keys named. Not available, with no amount and no snapshot (never "0"):
    (iv) a zero quantity whose unit has no entry next to a priced unit (``unit_unpriced``, the
    unit listed); (v) a usage of only such a zero quantity for a model priced only for other
    units (``no_price_for_model``); (vi) a zero quantity whose entry has unknown billing
    (``billing_rules_missing``); (vii) a zero quantity matched by two entries, or by an entry
    whose condition needs an unknown fact (``ambiguous_price``); (viii) zero quantities only,
    with neither ``occurred_at`` nor ``run_started_at`` (``ambiguous_price``)."""
    seconds = "audio_input_seconds"
    timed = {
        "unit": seconds,
        "unit_quantity": "60",
        "price": usd("0.0072"),
        "billing": billing("15", usd("0.01")),
        "entry_key": "k-seconds",
    }
    request = {
        "unit": "requests",
        "unit_quantity": "1",
        "price": usd("0.002"),
        "entry_key": "k-req",
    }
    mixed = make_usage(quantities=[qty(seconds, "0"), qty("requests", 1)], stage="stt")
    record = compute([mixed], catalog_of(timed, request))
    _assert_amount(record, "0.002")
    assert sorted(record.pricing_snapshot.entry_keys) == ["k-req", "k-seconds"]
    assert record.rounding is None
    token = {"unit": "input_tokens", "entry_key": "k-in"}
    cache = {
        "unit": "cache_read_input_tokens",
        "price": usd("0.03"),
        "billing": billing("none", usd("0.01")),
        "entry_key": "k-cache",
    }
    usage = make_usage(quantities=[qty("input_tokens", 1000), qty("cache_read_input_tokens", 0)])
    cached = compute([usage], catalog_of(token, cache))
    _assert_amount(cached, "0.00015")
    assert sorted(cached.pricing_snapshot.entry_keys) == ["k-cache", "k-in"]
    nothing = make_usage(quantities=[qty("input_tokens", 0), qty("output_tokens", 0)])
    out = {"unit": "output_tokens", "billing": billing("10", usd("0.5")), "entry_key": "k-out"}
    zero = compute([nothing], catalog_of({**token, "billing": billing("10", usd("0.5"))}, out))
    _assert_amount(zero, "0")
    assert zero.rounding is None
    assert sorted(zero.pricing_snapshot.entry_keys) == ["k-in", "k-out"]
    zero_second = make_usage(quantities=[qty(seconds, "0")], stage="stt")
    assert compute([zero_second], catalog_of(timed)).amount.amount == "0"
    # (iv) to (viii): a zero quantity is resolved like any quantity
    unit_unpriced = compute([usage], catalog_of(token))
    _assert_no_cost(unit_unpriced, "unit_unpriced")
    assert unit_unpriced.unpriced_units == ["cache_read_input_tokens"]
    only_cache = make_usage(quantities=[qty("cache_read_input_tokens", 0)])
    _assert_no_cost(compute([only_cache], catalog_of(token, request)), "no_price_for_model")
    unknown = {**timed, "billing": "unknown"}
    with_seconds = make_usage(
        quantities=[qty("input_tokens", 1000), qty(seconds, "0")], stage="stt"
    )
    _clash = (
        {**cache, "model": "gpt-4.1", "entry_key": "k-c1"},
        {**cache, "model": "gpt-4.1-mini", "aliases": ["gpt-4.1"], "entry_key": "k-c2"},
    )
    _assert_no_cost(compute([with_seconds], catalog_of(token, unknown)), "billing_rules_missing")
    _assert_no_cost(compute([usage], catalog_of(token, *_clash)), "ambiguous_price")
    conditional = {**cache, "applies_when": [{"kind": "batch", "value": True}]}
    _assert_no_cost(compute([usage], catalog_of(token, conditional)), "ambiguous_price")
    _assert_amount(
        compute([usage], catalog_of(token, conditional), facts=_facts(batch=True)), "0.00015"
    )
    undated = compute([nothing], catalog_of(token, out), run_started_at=None)
    _assert_no_cost(undated, "ambiguous_price")


def test_applies_when_conditions_all_must_hold() -> None:
    """N49 remainder (c) (ADR 0014 Decision 11; api-surface 23.6 group P item 2): the conditions of
    an entry combine as all-must-hold. With one entry ``batch: true`` and ``region: "in"``,
    through ``resolve_price``: facts ``batch=False, region=None`` remove the entry (a known false
    wins over an unknown), so with no other candidate the result is ``no_price_for_model`` and
    with an unconditional second candidate it is that entry; ``batch=None, region="in"`` is
    ``ambiguous_price``; ``batch=True, region="in"`` is the entry; ``batch=True, region="us"``
    removes it; ``batch=True, region=None`` is ``ambiguous_price``. A band
    ``context_tokens_gt: 128000`` plus ``context_tokens_le: 1000000`` matches 128001 and 1000000
    and not 128000 or 1000001."""
    when_both = [{"kind": "batch", "value": True}, {"kind": "region", "value": "in"}]
    alone = catalog_of({"applies_when": when_both, "entry_key": "k-cond", "price": usd("1")})
    fallback = catalog_of(
        {"applies_when": when_both, "entry_key": "k-cond", "price": usd("1")},
        {"entry_key": "k-plain", "price": usd("9")},
    )
    date = when("2026-02-01T00:00:00Z")

    def resolve(catalog: Any, **facts: Any) -> Any:
        return _resolve(catalog, run_started_at=date, facts=_facts(**facts))

    removed = resolve(alone, batch=False, region=None)
    assert (removed.entry, removed.na_reason) == (None, "no_price_for_model")
    chosen = resolve(fallback, batch=False, region=None)
    assert chosen.entry is not None
    assert chosen.entry.entry_key == "k-plain"
    unknown_batch = resolve(alone, batch=None, region="in")
    assert (unknown_batch.entry, unknown_batch.na_reason) == (None, "ambiguous_price")
    hit = resolve(alone, batch=True, region="in")
    assert hit.entry is not None
    assert hit.entry.entry_key == "k-cond"
    wrong_region = resolve(alone, batch=True, region="us")
    assert (wrong_region.entry, wrong_region.na_reason) == (None, "no_price_for_model")
    unknown_region = resolve(alone, batch=True, region=None)
    assert (unknown_region.entry, unknown_region.na_reason) == (None, "ambiguous_price")
    both_false = resolve(alone, batch=False, region="us")
    assert (both_false.entry, both_false.na_reason) == (None, "no_price_for_model")
    # a known false wins over an unknown whichever condition is listed first
    flipped = catalog_of(
        {"applies_when": list(reversed(when_both)), "entry_key": "k-flip", "price": usd("1")}
    )
    for catalog in (alone, flipped):
        for facts in ({"batch": None, "region": "us"}, {"batch": False, "region": None}):
            result = resolve(catalog, **facts)
            assert (result.entry, result.na_reason) == (None, "no_price_for_model"), facts
        for facts in ({"batch": None, "region": "in"}, {"batch": True, "region": None}):
            result = resolve(catalog, **facts)
            assert (result.entry, result.na_reason) == (None, "ambiguous_price"), facts
    band = catalog_of(
        {
            "entry_key": "k-band",
            "applies_when": [
                {"kind": "context_tokens_gt", "value": 128000},
                {"kind": "context_tokens_le", "value": 1000000},
            ],
        }
    )
    for tokens, matches in (
        (128001, True),
        (1000000, True),
        (500000, True),
        (128000, False),
        (1000001, False),
        (0, False),
    ):
        result = resolve(band, context_tokens=tokens)
        if matches:
            assert result.entry is not None, tokens
            assert result.entry.entry_key == "k-band"
        else:
            assert (result.entry, result.na_reason) == (None, "no_price_for_model"), tokens
    unknown_tokens = resolve(band)
    assert (unknown_tokens.entry, unknown_tokens.na_reason) == (None, "ambiguous_price")
