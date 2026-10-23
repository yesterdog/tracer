# The cost model

Users compare voice stacks by quality and by cost. A cost in this schema is either known or
explicitly not available; it is never guessed. Decisions:
[ADR 0014](../adr/0014-money-usage-cost-pricing.md). Field reference:
[UsageRecord](../schema/UsageRecord.md), [PricingCatalog](../schema/PricingCatalog.md),
[CostRecord](../schema/CostRecord.md), [CostSummary](../schema/CostSummary.md).

## Money

`Money{amount, currency}`. The amount is a canonical decimal string (never a float or a JSON
number); the currency is exactly `USD` in 0.0.1. There is no currency conversion. Prices in
catalogs and user-supplied prices must be USD.

## Two domains

Every usage and cost record belongs to one domain: `evaluation` (what our evaluation spent:
judge calls, our speech-to-text, the simulated caller) or `agent_under_test` (what the agent
spent). Summaries are per domain.

## Reported or computed, otherwise not available

- **`reported`**: a provider or telemetry platform reported the amount in USD.
- **`computed`**: computed by the pure function `cost.compute_cost` from known usage of the
  billable event (`UsageRecord`) and a known price whose billing rules are known (increment and
  minimum, each a value or `"none"`), with exact decimal arithmetic.
- Anything else is **`not_available`**, with a reason and no amount. There is no `estimated`
  basis: an agent's speech-to-text cost is never derived from the recording's length, and a
  black-box agent's LLM tokens are `usage_not_reported`. Partly priced usage is not available as
  a whole; a partial sum is never reported.
- A reported amount in another currency, without a currency or unparseable is kept verbatim in
  `reported_raw` and marked not available; it is never converted.

## Summaries

`CostSummary` has one USD `total`, present only when at least one record is included (a summary
with nothing included has no total, never `"0"`), the lists of included and excluded records,
and `completeness` (`complete`, `partial`, `none`). Each usage record counts in at most one
included cost record; an available reported cost is preferred over a computed one for the same
usage. A reported cost without usage records cannot be matched with a computed record for the
same call, so that double count is not detected: importers must link usage whenever the source
identifies it (`SCHEMA_POLICY.md` section 10).

## Prices

A `PricingCatalog` version lists `PriceEntry` value objects with a stable `entry_key`, an
effective range `[effective_from, effective_to)`, required billing rules, optional typed
conditions (`applies_when`) and provenance. A computed cost names the catalog version, its digest
and the entry keys it used. The price date is the usage's `occurred_at`, else the run's
`started_at`, else unknown (`ambiguous_price`), never "now".

## Tests

`test_compute_exact`, `test_billing_increment_and_minimum_applied`, `test_no_price_not_available_never_zero`,
`test_basis_enum_exact`, `test_not_available_cost_has_no_basis`, `test_cost_summary_total_absent_when_none_included`,
`test_available_reported_preferred_over_computed_duplicate`, `test_reported_non_usd_cost_not_available_raw_preserved`,
`test_price_resolution_without_any_date_is_ambiguous`, `test_policy_documents_reported_cost_without_usage_limitation`.
