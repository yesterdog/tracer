# Capability tiers and missing data

The framework must work with any agent, from a black box to a fully instrumented pipeline, and
produce the same kind of records at every level. Decisions:
[ADR 0004](../adr/0004-requires-and-not-applicable.md),
[ADR 0014](../adr/0014-money-usage-cost-pricing.md).

## The core rule

Consume telemetry if it is present; never break if it is absent; never assume or extrapolate.
A value that cannot be known is recorded as unknown, with a reason, and never as 0 or false.

## Tiers T0, T1, T2

| Tier | What the run holds | Typical source |
| --- | --- | --- |
| `T0` | no telemetry: recordings, transcripts or agent exports only | a black-box agent, a recording of a real call |
| `T1` | telemetry evidence: agent telemetry transcripts or tool calls, stage spans, telemetry-reported agent usage | OpenTelemetry spans, Langfuse traces |
| `T2` | observer evidence: records written by an observer inside the agent's pipeline | a pipeline observer |

`Run.observed_tier` is the highest tier whose evidence the run holds. It is a **display and filter
summary only**: no metric, verdict, cost or privacy rule reads it. Two contradiction checks keep
it honest: on a run, `spans` among the available inputs together with `T0` is rejected (R1); over a
run bundle, the stored tier must equal the tier computed from the records' provenance fields (R2),
unless records were deliberately withheld and declared in `RunBundle.withheld`.

What a metric can use is decided per **input kind** (`audio`, `agent_audio`, `transcript`,
`turn_timing`, `tool_calls`, `spans`, `usage` and others) from `Run.available_inputs`, which is
derived from the evidence, not from the tier.

## `not_applicable`: a metric that could not run

A metric declares its inputs in `MetricDefinition.requires`. When the run lacks one, the result
is `not_applicable`, with `value` and `passed` both `None` and a reason:

| `na_reason` | Meaning |
| --- | --- |
| `missing_input` | inputs are missing; `missing_kinds` lists them |
| `agent_channel_unknown` | a recording exists but no channel map identifies the agent's channel |
| `language_not_observed` | no producer recorded the language of the text |
| `unsupported_language` | the observed language is not one the metric supports |
| `unsupported_script` | a script-sensitive metric met text in a non-native script |

When several apply, the first in this order is reported. A not-applicable result is never scored
as 0 or as a failure, and a required check that is not applicable makes the verdict
`inconclusive`. `error` is different: the inputs existed but scoring failed.

## `not_available`: a cost that cannot be known

A `CostRecord` is `available` only when it is **reported** (by the provider or a telemetry
platform) or **computed** from known usage of the billable event and a known price with known
billing rules. Otherwise it is `not_available` with a reason (`usage_not_reported`, `no_usage`,
`no_price_for_model`, `unit_unpriced`, `billing_rules_missing`, `ambiguous_price`,
`unsupported_currency`, `currency_not_reported`, `amount_unparseable`, `currency_conflict`). A
not-available record has no amount and no basis, and a summary's total lists what it excludes.
For a black-box agent the LLM token cost is `not_available` (`usage_not_reported`); it is never
estimated from the recording.

## Tests

`test_agent_channel_unknown_only_when_agent_audio_alone_is_missing`, `test_check_observed_tier_function`,
`test_run_rule_r1_and_bundle_rule_r2_agree`, `test_an_under_claim_is_never_excused`,
`test_no_price_not_available_never_zero`, `test_not_available_cost_has_no_basis`,
`test_per_turn_share_not_applicable_when_no_turn_applies`.
