# Batch 4 (cost, pricing, usage), revisions 16 and 17: red-first summary

Date: 2026-10-23. Source: qa report, counts re-run by the lead on Python 3.13 (1088 collected, 238 failed, 850 passed).

## Counts (identical on Python 3.11, 3.12, 3.13, 3.14)

| Group | Red (repository src) | Oracle green (oracle13) |
|---|---|---|
| Batch 4 files, 226 collected (was 206) | 226 failed | 225 passed, 1 failed |
| test_ids + hide-input seed test + closed-enum test, 30 collected | 9 failed, 21 passed | 27 passed, 3 failed |
| Full suite, 1088 collected (was 1068) | 238 failed, 850 passed | 1082 passed, 6 failed |

- Baseline before this pass: 212 failed of 1068.
- Oracle failures are the gate-bound tests (RunBundle, ExportBundle, SealedPayload, RedactionRecord not present)
  and the three policy-document tests that wait for SCHEMA_POLICY.md (P1-20a).
- `test_all_closed_enums_exact` skips owners that do not exist yet; its new rows are proven by mutants only.

## Why the repository run is red

No usage, pricing or cost module exists; `ids.py` still has the withdrawn `pce` prefix; the three pricing models and
three usage/cost models do not hide their input yet.

## Mutants

144 mutants over oracle13: 141 caught, 3 survive (S03, I19, I20), all equivalent in the oracle (a redundant guard, and
two regexes double-guarded by the canonical-pattern check). The oracle and tests share one author.

## Changed pre-existing tests (spec changes, no assertion removed)

`_support_b4.py` helpers; `test_usage` field set (occurred_at); `test_pricing` (catalog shape, entry shape, digest
tampering, applies_when, non-USD message assertion changed from naming the code to the rule text, N62);
`test_cost` (entry_keys, reasons, one rename); `test_cost_compute` (occurred_at, entry_keys); `test_cost_summary`
(12 exclusion reasons); `test_cost_import` (one extra warning assertion); `test_cost_contract` (float scan scope);
`test_batch4_entities` (PriceEntry is a value object); `test_ids` (pce removed, 26 prefixes, PriceEntry value
object); `test_project_agent` (six new hide-input seeds); `test_metric_definition` (seven closed-enum rows).

## Deferred

Group V (F1 repeated-unit merge, needs the P1-29 mapping; N76 changes it). JSON Schema halves of the by-reason and
note presence rules wait for the N12/N38 ruling. Rules JSON Schema cannot state stay code-only.

## Not verified

Python 3.15, Linux, Windows; the architect's real implementation (none exists yet); a reading shared by the tests and
the oracle.
