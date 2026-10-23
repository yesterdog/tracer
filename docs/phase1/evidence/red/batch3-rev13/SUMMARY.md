# Batch 3, revision 13: red-first summary

Date: 2026-10-23. Source: qa report, re-verified by the project owner's session (counts re-run on Python 3.13).

## Counts (identical on Python 3.11, 3.12, 3.13, 3.14, compared test by test)

| Run | Collected | Failed | Passed |
|---|---|---|---|
| Before (batch 3 red) | 836 | 246 | 590 |
| Now, repository code | 862 | 274 | 588 |
| Now, qa oracle (policy test deselected) | 861 | 5 | 856 |

- 26 new tests, all red on repository code.
- Two existing tests went from passing to failing because they depend on code that does not exist yet:
  `test_scripted_turn_placeholder_stored_unresolved` (N20 digest exclusion) and
  `test_hide_input_set_closed_under_embedding` (Transcript not implemented).
- No test went from failing to passing.
- On the oracle the 5 failures are the four gate-bound tests and
  `test_policy_documents_low_entropy_digest_limitation` (P1-20a).

## Why the new tests fail

- Module missing or "not implemented yet": the batch 3 tests.
- `DigestMismatch`: `test_evaluator_digest_excludes_test_profile_digest` (N20).
- Missing `hide_input_in_errors`: `test_definition_models_errors_do_not_echo_input`,
  `test_evaluator_errors_do_not_quote_rejected_values`.
- Message quotes the value: the N42 tests on batch 1 and 2 models.

## Existing tests that changed (no assertion removed or weakened)

N26 key rename in `test_pricing_catalog_ref_digest_verified`; `bits_per_sample` optional in
`test_audio_format_fields`; sealing helpers exclude `test_profile.content_digest` (N20);
13 seeds added to `test_hide_input_set_closed_under_embedding`;
`derive_human_subject(human, ())` (O1); two closed-enum rows (`ToolCallEvent.source`,
`Run.observed_tier`); three `test_results.py` inputs corrected for R1 (Runs with spans now
built at T1/T2, every assertion kept); the second half added to
`test_definition_refs_digest_optional_and_verified_when_present` (N34).

## Mutants

Rev 13 set: 174 mutants, 167 caught, 7 survive (3 equivalent, 3 not pinned, 1 unreachable
until Money exists). Old 470-mutant set rerun against the new tree: 445 caught, 21 survivors
are the previously explained ones. Details: `mutants.txt`.

## Deferred (need the bundle or cost modules)

P1-14: bundle observed-tier check, withheld-records declaration, file and loopback URI
rejection in shared and benchmark exports, `source_run_id` export rules, export_human_subject
turns argument, shared-export snapshot digest after the profile digest is dropped.
P1-25: pricing snapshot `content_digest` naming and verification.
T-DRIFT allowlist test (must list `test_results_language.py` and `test_dataset.py`).

## Not verified

The oracle was written by the same agent as the tests, so it shows satisfiability, not an
independent reading. Python 3.15, Linux and a native-speaker review of Indic fixtures were
not run. The tests have not yet run against the architect's implementation.
