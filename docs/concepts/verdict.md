# The run verdict

A run's verdict answers one question: did the agent meet the requirements of its evaluator? It
is a `RunVerdict` record. Decisions: [ADR 0013](../adr/0013-verdict.md); per-turn metrics:
[ADR 0010](../adr/0010-metric-value-model.md) Decisions 3 and 3a.

## The headline: "x/y required checks passed"

- The rubric (`Rubric`) marks some metric bindings as **required**; each required binding is one
  check. Every rubric has at least one, and every required check needs a pass rule.
- Each check has one outcome: `passed`, `failed`, `not_applicable` or `errored`, taken from its
  `MetricResult` (scored and passed, scored and not passed, not applicable, error).
- `render_headline()` returns exactly `"{passed}/{required_total} required checks passed"`. The
  denominator `required_total` counts **every** required check, including those that were not
  applicable or errored. A run where 3 of 5 required checks passed and 2 could not run reads
  "3/5 required checks passed", never "3/3".
- The counts add up: `required_total = passed + failed + not_applicable + errored`, and each count
  equals the tally of the checks.

## Status

| Status | When |
| --- | --- |
| `pass` | every required check passed |
| `fail` | at least one required check failed |
| `inconclusive` | none failed, and at least one was not applicable or errored |

Missing telemetry therefore lowers confidence (`inconclusive`), never turns a run into a pass
or a fail. A stored status that contradicts the counts is rejected.

## Weighted score, always with its coverage

Weights live in the rubric, are positive decimals, and never change the headline or the status.
A weight needs a pass rule.

- `weighted_score = Σ(w · passed) / Σ(w)` over the weighted checks that were **scored**, with
  `passed` as 1 or 0. Not-applicable and errored weighted checks are left out and listed in
  `weighted_excluded`.
- `weight_coverage = Σ(w of scored weighted checks) / Σ(w of all weighted checks)`: how much of
  the rubric's weight the score describes.
- The two are stored together. If nothing weighted was scored, both are absent; a score of 0
  never stands for "nothing measured".
- **Never show the score without the coverage.** The one rendering helper is
  `render_weighted_score(verdict)`: `"weighted score 1.00 (weight coverage 0.80)"`.

Worked example (golden): weights 5, 3 and 2; the weight-5 and weight-3 checks passed, the
weight-2 check was not applicable. Score `(5 + 3) / 8 = 1`, coverage `8 / 10 = 0.8`.

## Per-turn share, always with its turn coverage

A metric aggregated as `per_turn_share` (for example "at least 80% of the agent's turns are
polite") is still **one** check. Its share is `passed turns / (passed + failed turns)`; turns
that were not applicable or whose judge verdict was undetermined are excluded from the
denominator and counted separately. The share lives in `MetricResult.per_turn`, never in
`value`. `passed` is the definition's range rule applied to the share, and in the weighted score
the check contributes `w · passed`, never `w · share`. The one rendering helper is
`render_per_turn_share(result)`: `"turn share 0.75 (turn coverage 8/10 turns)"`.

## Tests

`test_counts_sum_to_required_total`, `test_status_pass_fail_inconclusive_table`,
`test_inconsistent_status_rejected`, `test_headline_unchanged_by_weights`,
`test_weighted_score_5_3_2_with_na_is_1_coverage_0_8`, `test_weight_coverage_required_with_score`,
`test_weight_coverage_is_scored_over_total_weight`, `test_render_weighted_score_shows_coverage`,
`test_no_renderer_shows_weighted_score_without_coverage`, `test_rubric_weight_requires_pass_rule`,
`test_weighted_score_uses_per_turn_passed_not_share`, `test_render_per_turn_share_shows_turn_coverage`,
`test_no_renderer_shows_per_turn_share_without_turn_coverage`.
