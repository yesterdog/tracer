# Phase 3 batch J2 contract: ADR 0016 P-47 and ADR 0004 P-08 citations (finding F2 of batch J)

Author: qa, 2026-10-27. Follows docs/phase3/api-surface-batch-j.md. No schema change, no behaviour change, no source change.

## 1. Finding

Both decided behaviours are already tested under other names in `packages/evalschema/tests`; the two backticked names are historical mentions of tests that were replaced or never written:

| ADR item | Decided behaviour | Cited name (absent) | Tests that bind it (exist) |
|---|---|---|---|
| ADR 0016 P-47 (line 103) | stored marker may be stricter than `derive_human_subject()` with a reason, never looser; manual true always allowed; closed reason set of three | `test_human_subject_marker_contradiction_rejected`, named as "replaced by" | `test_human_subject_looser_marker_rejected`, `test_human_subject_stricter_marker_requires_reason`, `test_human_subject_manual_true_always_allowed`, `test_marker_before_role_evidence_stays_valid_with_reason` (all `test_privacy.py`); also `test_privacy_marker_shape_and_reason_set` |
| ADR 0004 P-08 (line 187) | a scored result whose definition has no pass rule has `passed=None`; true or false is rejected | `test_every_metric_has_pass_rule`, named as the rejected alternative | `test_scored_without_pass_rule_has_passed_none` (`test_results.py`, ADR 0004 I18) |

No gap in packages/evalschema.

## 2. Exact ADR fixes (architect applies; `oracle-docs.patch`)

- ADR 0016 P-47: replace "`test_human_subject_marker_contradiction_rejected` is replaced by" with "The single contradiction test of the proposal is replaced by" (the four binding names stay in backticks).
- ADR 0004 P-08: replace "Had it been decided otherwise: every metric must have a pass rule, and `test_scored_without_pass_rule_has_passed_none` becomes `test_every_metric_has_pass_rule`." with "Test: `test_scored_without_pass_rule_has_passed_none`. Had it been decided otherwise, every metric would need a pass rule and that test would be inverted."
Rule: a name of a replaced or rejected test is not written in backticks.

## 3. Tests

Staged at `docs/phase3/evidence/red/batch-j2/staging/tests/evalschema/test_adr_decided_items_cite_tests.py` -> `packages/evalschema/tests/` (no `__init__.py`). 3 tests: P-47 cites its four tests and only existing names; P-08 cites its test and only existing names; the check has teeth. (The batch J file `test_adr0016_amendment_cites_test.py` was already moved into packages by dev, so this is a separate module.)

Red on the current tree: 2 failed, 1 passed on Python 3.11, 3.12, 3.13, 3.14 (`red.py3.1x.log`; the failures are the two ADR lines above, the teeth test passes). Oracle (the two text fixes in a scratch copy): 3 passed on all four (`oracle.py3.1x.log`); ruff and mypy strict clean. Mutants (`mutants.txt`): 5 of 5 killed (misspelt name, dropped binding test, old name back in P-47; dropped test and old name back in P-08).

## 4. Other cited names with no test (reported, not fixed)

`adr-cited-names-without-a-test.txt`: 49 hits over docs/adr/*.md at 2026-10-27. 29 are not test names (`test_profile`, `test_profiles`, `test_profile_id`, `test_profile_version`: the TestProfile payload type and column names, ADR 0001, 0006, 0015, 0019, 0020, README). The other 20 (including README mirrors) are names of renamed or withdrawn tests; the eight sampled each say "renamed", "withdrawn", "became" or "replaced" (ADR 0005 l.111, 0010 l.124-129, 0011 l.64-66, 0014 l.163-224, 0017 l.27, 0006 l.100). The two of this batch are the only ones that read as if the test existed. A general rule "no backticked name of a replaced test" would need those 18 lines rewritten too; recommendation: P3, do it with the next ADR editing pass, then a whole-ADR reader like `test_adr0023_rules_are_tested` without PENDING.
