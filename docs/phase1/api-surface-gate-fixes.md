# Phase 1 gate fixes: T-B13, T-B16, N19 and the guards for B1, M1, m3 (batch 28)

Author: qa, 2026-10-24. Source: the decision log, "PHASE 1 GATE (staff-reviewer, read-only, first run) VERDICT: FAIL" and the auto-approved decisions after it (B1, M1, M4, M5, m3). qa owns this file; it does not change `api-surface.md`, the ADRs, SCOPE or HANDOFF (the architect merges what it needs).

New files only (no existing test, support module, source file, schema, ADR, SCOPE or HANDOFF was edited):

| File | Role |
|---|---|
| `packages/evalschema/tests/test_utc_datetime_portability.py` | B1: `UtcDatetime` bytes on every platform; no `strftime`/`strptime` in the package, the test helpers, the converters, the tests and `scripts/` (67 items, 18 functions) |
| `packages/evalschema/tests/test_runtime_imports.py` | M1: `test_runtime_imports_whitelist` (SCOPE section 5 name, ADR 0008 I16): AST over every module plus a fresh `python -I` interpreter (42 items, 11 functions) |
| `packages/evalschema/tests/test_bundle_applicability_counts.py` | M4: T-B13 and T-B16 (83 items, 37 functions) |
| `packages/evalschema/tests/test_export_declared_by.py` | M5: N19 (48 items, 25 functions) |
| `packages/evalschema/tests/_support_b17.py` | helpers for the two files above (no tests) |
| `docs/phase1/api-surface-gate-fixes.md` | this contract |
| `docs/phase1/evidence/red/batch28-gate/`, `docs/phase1/evidence/green/batch28-gate/` | logs, mutants, the oracle patch, the suggested edits of existing tests, `tdrift-plan-local.log` |

`_support_b17.py` is the next free name (`_support_b16.py` exists; no `b17` existed).

## 0. The three guard files (they pass today)

### 0.1 `test_utc_datetime_portability.py` (B1)

`primitives._format_instant` now writes `f"{utc.year:04d}-..."`. `strftime("%Y")` writes `1` for year 1 on glibc and `0001` on macOS, so only hosted Linux CI saw the bug. The tests pin hand-written expected strings (never computed with `strftime`) for the years 1, 9, 99, 999, 1000 and 9999: `model_dump(mode="json")`, `model_dump_json`, `TypeAdapter.dump_python/dump_json`, the JSON Schema `pattern` (serialization mode) by `re.fullmatch`, the round trip (equal object and equal bytes), the padded text parses, the glibc text (`1-01-01...`) does not, every other field keeps its zeros, an offset input in years 99, 999, 1000 and 9998, and the last instant of year 9999. The source scans (AST, so comments and docstrings never trip them; a name, an attribute, an import alias, or a string passed to `getattr` is a use):

* the package `src/tracer_evalschema` (the shipped serializer);
* the wider set the lead asked for after hosted CI showed more users: `packages/evalschema/tests/support/mappings/**`, every `tests/_support*.py`, every `tests/test_*.py` except this file (it names the functions in strings), and `scripts/*.py`.

Teeth (scratch copies, logs in `red/batch28-gate/`): mutant M1 (the formatter writes the year unpadded, as glibc does) fails 26 items (every short year in every test; the years 1000 and 9999 stay green as they must); mutant M2 (`strftime` back in `primitives.py` and a `strptime` in `ids.py`; padded on macOS, so only the scan can see it) fails the scan test and names `ids.py:432: strptime` and `primitives.py:126: strftime`.

### 0.2 `test_runtime_imports.py` (M1)

`test_runtime_imports_whitelist`: every module imports only the standard library (`sys.stdlib_module_names`), `pydantic`, `pydantic_core`, `rfc8785` and `tracer_evalschema`; imports at any depth (inside a function, a `try`, `TYPE_CHECKING`) are found; `importlib.import_module`, `__import__` and `import_module` with a literal name are checked, with a computed name only if it is an f-string starting `tracer_evalschema.` (that is `registry.py`). Also in a fresh interpreter (`python -I`, so `PYTHONPATH`, user site and the current directory are out; a baseline interpreter that imported nothing is subtracted): it imports every module of the package, renders the JSON Schema files and validates one instant, then lists `sys.modules`; the only third-party top-level modules are `pydantic`, `pydantic_core`, `annotated_types`, `typing_extensions`, `typing_inspection`, `rfc8785` and the package. `jsonschema`, `pytest`, `hypothesis`, `bidi`, `yaml` are asserted absent one by one. The stdlib-generated module `_sysconfigdata_<platform>` (which `sys.stdlib_module_names` does not list) is excluded by prefix. Self-tests feed both checkers a forbidden import in every form (17 forms must be found, 9 permitted forms must pass; the dynamic check finds `pytest`, `jsonschema`, `hypothesis` imported after the package).

Teeth: in a scratch copy with its own environment, `text.py` importing `jsonschema` inside a function and `errors.py` importing `pytest` at module level fail the static test (naming `errors.py:151: import of pytest`, `text.py:280: import of jsonschema`), the dynamic test (`pytest`, `_pytest`, `pluggy` ... appear) and the per-module test. Note for the next engineer: `PYTHONPATH` is ignored by `-I`, so a scratch copy needs its own environment (`UV_PROJECT_ENVIRONMENT`) for the dynamic leg to see its source.

### 0.3 T-DRIFT plan-file local check (m3)

`docs/phase1/evidence/green/batch28-gate/tdrift-plan-local.log`: the command line, the pytest line (`1 passed, 54 deselected`) and the sha256 of the plan file the test reads, hash only. The run is on the working tree (the directory is not a git checkout for this tool, so no commit id could be recorded): record the final commit beside the hash when the commit is made.

## 1. T-B13 and T-B16 (M4): what a stored result says about the turns it read

The rule stays the last step of `check_run_bundle`, tag `[applicability]`, `api-surface-applicability.md` sections 3.1 to 3.9 unchanged. Two additions, both inside `_check_applicability`.

### 1.1 Pinned readings

* **P-C1 (T-B13, the turn rule).** For a result whose status is `scored` and whose definition's `requires` names `transcript` or `agent_transcript`, the turns it read (section 3.3: the turns of the transcripts it read, limited to role `agent` when `requires` has `agent_transcript`, else all of them) are not empty. Otherwise it was scored over nothing: `[applicability]`, field `status` (the stored status class differs from what the turns support), message of the existing form (result ID, `results[<i>]`, `status`; no other compared field; no count field; **no input kind**: the message must not contain `agent_transcript`, because the existing T-A4 test `test_applic_a_bundle_without_turns_is_not_judged_by_the_agent_turn_rule` accepts a failure only if its text does not). The first offending result in `results` order is reported.
  The three cases it covers: an empty call (the bundle holds no turn); a transcript with no turn; an `agent_transcript` metric whose `transcript_ids` name only a transcript without an agent turn while another transcript holds them (T-A4, which is global, is satisfied).
* **P-C2 (what the turn rule does not judge).** `not_applicable` and `error` results claim nothing and are not judged by P-C1. A withheld record of kind `transcript` or `turn` switches P-C1 and P-C3 off for the whole bundle (the same gate as section 3.8: the language steps cannot be re-run, and neither can the turns a result read); a withheld `artifact`, `exchange`, `tool_call`, `span`, `usage_record`, `cost_record` or `result` takes nothing away. P-C1 applies after the run-level answer: a result whose run-level answer (`missing_input`, `agent_channel_unknown`) differs is reported by the existing comparison first.
* **P-C3 (T-B16, the counts).** For a `per_turn_share` result that has a `per_turn` summary, whatever its status (`scored`, all-`not_applicable`, or the `judge_undetermined` `error`), the bundle recomputes from the turns the result read (section 3.3, per P-C1's gate):
  * `per_turn.total_turns` equals the number of turns read (field spelled `per_turn.total_turns`);
  * `per_turn.counts.not_applicable` equals the number of those turns for which `results.applicability(definition, run, turns=[turn], transcripts=<read>)` says not applicable (field spelled `per_turn.counts.not_applicable`).
  `passed`, `failed` and `undetermined` are what the scorer or judge answered and are not a function of the turns; they are not compared. Their sum is fixed by the two compared counts (the model already requires the four counts to add up to `total_turns`), so no third field exists to name. The counts are recomputed per metric: in a call with one foreign-language agent turn the language-dependent per-turn metric has one not-applicable turn and the language-independent one has none.
* **P-C4 (message).** Same form as the existing `[applicability]` error: result ID, `results[<i>]` (0-based position in `RunBundle.results`), one field. For a count the field is `per_turn.total_turns` or `per_turn.counts.not_applicable`; none of the five compared field names (`status`, `na_reason`, `missing_kinds`, `language_source`, `declared_language_transcript_ids`) appears in a count message, and no count field in a message about the five. No text, tag value or reason value is quoted.
* **P-C5 (order).** Within one result: the run-level step, then P-C1, then the five compared fields (the existing order), then `per_turn.total_turns`, then `per_turn.counts.not_applicable`. So a result wrong in a compared field and in a count is reported for the compared field (this keeps `test_applic_per_turn_scored_when_one_turn_counts` green), and a result wrong in both counts names `total_turns` only. A per-turn result scored over zero turns is reported by P-C1 (`status`), not by a count.
* **P-C6 (a per-turn metric that reads no turn, T-B4 closed).** With no turn read, the run-level reasons are compared as before; a per-turn result that still carries a `per_turn` summary (an `error` with four undetermined turns, say) is rejected at `per_turn.total_turns`; one without a summary loads. A scored one is P-C1.

### 1.2 Exact source changes for the architect

`oracle-src.patch` (in `evidence/red/batch28-gate/`) is a working reference against the current `bundle.py` and `transcript.py`, not for blind application. In `bundle.py`:

1. Two helpers before `_check_agent_turn`: `_not_applicable_turns(definition, run, turns, transcripts) -> int` (count of turns with `applicability(definition, run, turns=[turn], transcripts=transcripts).na_reason is not None`) and `_compare_counts(position, result, definition, run, turns, transcripts)` (return if `result.per_turn is None`; raise `_applicability_error(position, result, "per_turn.total_turns")` if `total_turns != len(turns)`; then `"per_turn.counts.not_applicable"` if the count differs). The architect may reuse the answers `_per_turn_answer` already computes instead of calling `applicability` a second time per turn (the oracle favours clarity over speed; the 2,000-turn test loads in about a second).
2. In the result loop of `_check_applicability`, with `turns` kept `None` when the run-level answer decides or a `transcript`/`turn` is withheld: after `turns = _turns_read(...)`, before the aggregation branch, `if result.status == "scored" and any(kind in _TRANSCRIPT_KINDS for kind in definition.requires) and not turns: raise _applicability_error(position, result, "status")`; after the existing comparison, `if turns is not None and definition.aggregation.kind == "per_turn_share": _compare_counts(...)`.
3. Docstrings of `_check_applicability` and `check_run_bundle` (the "per-turn counts are not compared (T-B16)" sentence goes; T-B13 and T-B16 are stated).

Documents (the architect's): `api-surface-applicability.md` 3.7 item 5 ("the counts are not compared") and T-B13, T-B15, T-B16 in section 8 (mark decided and implemented), 3.9 and T-B4 (closed by P-C6); ADR 0004 Decision 9 (the two rules); `api-surface.md` 25.4.1 step 9. No schema, lock, field doc or golden fixture changes for this part (the oracle regenerated none; `schema_export --check` is unchanged).

## 2. N19 (M5): `declared_by` in shared and benchmark exports

### 2.1 Pinned readings

* **P-N1 (the field).** `LanguageDeclaration.declared_by` becomes optional: `... | None = None` (absent and `null` are the same, as for every optional field; a present value is validated as before: a machine key without `@`). `language`, `source` and `declared_at` stay required. Reason: "absent (`None`)" cannot be said of a required field, and the same `RunBundle` model sits in every purpose. The alternative (required but nullable) keeps the existing "missing member raises" assertion but makes a producer write `null` and is unlike every other optional member of the schema.
* **P-N2 (the rule).** Tag `[declared_by]`, a `ValueError` of `check_export_bundle` (a `ValidationError` whose text holds the tag when the model loads). It runs for `shared_export` and `benchmark` only, over the `transcripts` of every embedded run bundle: a transcript whose `declared_language` is present and whose `declared_by` is not `None` is rejected. `internal` exports, a bare `RunBundle` and a bare `Transcript` keep the member.
* **P-N3 (message).** Names the run bundle ID, the transcript ID and the position as `transcripts[<j>]` (0-based in `RunBundle.transcripts`); never the value of `declared_by`, never any text. The first offender in order (run bundles, then transcripts) is the one named. The error is located at the export root (like every other export rule), not at `run_bundles.<i>`.
* **P-N4 (nothing else changes).** `detected_language`, `detected_language_raw`, the tags on turns and the rest of the declaration are accepted exactly as before in every purpose; in a shared export the declaration keeps `language`, `source`, `declared_at` and the results keep `language_source = declared` with the declared transcript IDs.
* **P-N5 (order).** Unpinned between `[declared_by]` and the other export tags; the tests build single-fault documents. The oracle checks it in `_check_portable_run_bundle` (with `[profile]`, `[profile_digest]`, `[uri]`).

### 2.2 Exact source changes for the architect

1. `transcript.py`: `declared_by` as in P-N1 (oracle patch). Update the class docstring (the member may be absent).
2. `bundle.py`: in `_check_portable_run_bundle`, a loop over `run_bundle.transcripts` as in the oracle patch; update the `check_export_bundle` docstring (the shared/benchmark list gains `[declared_by]`) and the module docstring.
3. Regenerate: `uv run --locked python -m tracer_evalschema.schema_export --write` (changes `ExportBundle.schema.json`, `RunBundle.schema.json`, `Transcript.schema.json`, `schema.lock`: `declared_by` leaves `required` and becomes `anyOf [string with the pattern, null]` with `default: null`); `uv run --locked python scripts/generate_field_docs.py --write` (`docs/schema/ExportBundle.md`, `RunBundle.md`, `Transcript.md`, `value-objects.md`). `scripts/build_golden_fixtures.py --write` rewrites nothing (the oracle run changed no golden file: every valid fixture already carries `declared_by`, no invalid fixture is about its absence).
4. Documents (the architect's): ADR 0005 Decision 6g / ADR 0004 Decision 6g (declared_by optional, absent in shared and benchmark exports), ADR 0016 (the export rule list), `api-surface.md` 25.3.1 (new row `[declared_by]`), 25.4.2, and mark N19 decided in 19.6, 21.5 and 22.7; `SCHEMA_POLICY.md` and `CHANGELOG.md` (an optional member within the unreleased version; the schemas and lock are regenerated, `SCHEMA_VERSION` stays `0.0.1`). The register of rules the schema cannot say: no row was needed in the oracle run (`test_schema_gaps`, `test_schema_agreement` and the export-rule tests stayed green); if the architect adds a row for the export rule it follows the existing `CO-` rows.

## 3. Existing tests that must change (named spec changes; `suggested-existing-edits.patch`)

I may not edit existing tests. Each change follows from a decision; none weakens a check (each one keeps what it tested and adds the consequence). The patch was applied to the oracle tree, where the whole suite is green.

| # | File and place | Change | Why |
|---|---|---|---|
| SCR-G1 | `test_bundle_applicability.py`, `test_applic_a_result_stored_for_another_run_is_rejected_unless_it_agrees` (16 of its 306 items fail with the oracle: every pair where a donor per-turn summary differs in `total_turns` or `counts.not_applicable`, all involving `T1-mixed`) | the expected verdict also compares the two stored per-turn summaries (`_support_b17.count_differences`); the named fields may be the count fields (`fields_named`); the docstring sentence "a per-turn summary may differ: its counts are not compared" goes | T-B16. This is the case T-B15 of `api-surface-applicability.md` announced ("deliberate work, not a regression") |
| SCR-G2 | `test_bundle_applicability.py`, `test_applic_agent_transcript_with_one_agent_turn_is_enough` | after turning three of four agent turns to `unknown`, the two per-turn results get the summary of one turn (`counts_changes(..., passed=1)`) | the control kept stored summaries of four turns for a call that now has one |
| SCR-G3 | `test_bundle_applicability.py`, `test_applic_a_huge_call_validates_and_a_forgery_in_it_is_still_found` | the two per-turn results get the summary of the 1,004 agent turns | same: 2,000 turns were added, the summaries still said four |
| SCR-G4 | `test_bundle.py`, `test_run_bundle_transcript_rules_run_over_the_bundles_turns` (the `empty` bundle) | `empty["results"] = []` and `empty["verdict"] = None` before `assert load_run(empty).turns == []` | T-B13: an empty call holds no scored transcript result. The test is about the transcript rules over the bundle's turns, not about results |
| SCR-G5 | `test_bundle_tier.py`, `test_sealed_and_redacted_records_count_for_the_tier` | `load_export(no_declaration)` becomes `pytest.raises(ValidationError, match=r"\[applicability\]")`; the `declared` branch below it (turns declared withheld) is unchanged and is the accepted form | T-B13 with sealing: see ambiguity A-C1 |
| SCR-G6 | `test_transcript.py`, `test_transcript_declared_language_shape_and_provenance` | `declared_by` leaves the loop of members whose absence raises (the three others stay) and an assertion that absence loads is added; `None` leaves the list of bad `declared_by` values | N19 (P-N1) |

With SCR-G1 to G6 and the architect's source and regenerations the whole suite is green in the oracle: **8,606 passed**, 6 failed, and the 6 fail only because the scratch copy has no `.git` (`test_mapping_f8.py` x3, `test_repo_contract.py` x3; they pass in the repository). Without the edits the oracle fails 21 existing tests (SCR-G1 to G6), nothing else. The 3 other test files this batch touches are new.

## 4. Test map

R = fails today and passes with the oracle; G = guard (passes today, must pass after).

### 4.1 `test_bundle_applicability_counts.py` (83 items: 42 R, 41 G)

| Test (`test_b13_...` / `test_b16_...`) | Items | R/G | What it pins |
|---|---|---|---|
| `b13_scored_agent_transcript_result_over_a_transcript_without_agent_turn` | 4 | R | P-C1: `agent_politeness`, `agent_turn_count`, `turn_politeness_share`, `turn_length_share` naming a caller-only transcript |
| `b13_scored_transcript_result_over_a_transcript_with_zero_turns` | 3 | R | `goal_completion`, `script_consistency`, `agent_politeness` |
| `b13_an_empty_call_with_scored_transcript_results_is_rejected`, `b13_the_function_reports_an_empty_call_too` | 2 | R | no turn at all; message without the input kind |
| `b13_the_first_offending_result_in_order_is_the_one_named` | 1 | R | order |
| `b13_a_withheld_artifact_takes_nothing_away` | 1 | R | gate |
| `b13_a_second_transcript_does_not_hide_a_rejection_inside_an_export` | 1 | R | seam |
| `b13_the_control_naming_the_transcript_with_agent_turns_loads` | 4 | G | the first, or both, transcripts named |
| `b13_the_control_naming_both_transcripts_loads` | 3 | G | |
| `b13_results_that_claim_nothing_are_not_judged_by_the_turn_rule` | 1 | G | error and not-applicable results in an empty call (P-C2) |
| `b13_withheld_turns_skip_the_turn_rule`, `b13_a_withheld_transcript_skips_the_turn_rule` | 2 | G | B22 gate |
| `b16_total_turns_that_differs_from_the_turns_read_is_rejected` | 8 | R | too many, too few, via undetermined, far too many; both metrics |
| `b16_a_not_applicable_count_that_differs_from_the_turns_is_rejected` | 7 | R | including the language-independent metric in the mixed world |
| `b16_both_counts_wrong_names_total_turns_only` | 2 | R | P-C5 |
| `b16_error_per_turn_result_counts_are_compared_too`, `b16_all_turns_not_applicable_counts_are_compared` | 2 | R | the other two statuses |
| `b16_the_turns_counted_are_those_of_the_transcripts_named`, `b16_the_not_applicable_turns_are_those_of_the_transcripts_named` | 2 | R | `transcript_ids` choose the turns |
| `b16_a_summary_over_zero_turns_is_rejected`, `b16_a_scored_per_turn_result_over_zero_turns_is_reported_as_status` | 2 | R | P-C6, P-C5 |
| `b16_a_withheld_artifact_takes_nothing_away`, `b16_a_withheld_result_leaves_the_other_results_checked` | 2 | R | gate |
| `b16_the_error_is_located_at_the_run_bundle_of_an_export`, `b16_the_function_judges_a_bundle_that_was_not_validated` | 2 | R | seams |
| `b16_a_huge_call_...`, `b16_prompt_injection_...`, `b16_nothing_is_printed_or_logged_...` | 3 | R | 2,000 turns (1,004 agent turns); injection text; no output, document unchanged |
| `b16_consistent_bundles_still_load` | 18 | G | the 18 T-DEG worlds |
| `b16_the_mixed_world_has_a_not_applicable_turn_for_the_language_metric_only`, `b16_the_stored_summary_alone_is_a_valid_result`, `b16_the_two_count_fields_are_the_contract_spelling` | 4 | G | fixture and helper self-checks |
| `b16_the_split_into_passed_failed_and_undetermined_is_not_compared` | 5 | G | P-C3 (ambiguity A-C4) |
| `b16_the_five_compared_fields_are_reported_before_a_count` | 1 | G | P-C5 (the existing rule already rejects it; the test stays green and pins the field named) |
| `b16_a_run_level_not_applicable_result_has_no_counts_to_compare` | 1 | G | |
| `b16_a_withheld_turn_switches_the_counts_off`, `b16_a_withheld_transcript_switches_the_counts_off` | 2 | G | B22 gate |

### 4.2 `test_export_declared_by.py` (48 items: 29 R, 19 G)

Field optional, default, both absent forms, the three other members still required, a present value still validated (7 bad values), round trips, JSON Schema in the shipped `Transcript`, `RunBundle`, `ExportBundle` files; the matrix over the three purposes (absent loads in all three; present loads in `internal`, rejected in `shared_export` and `benchmark`); explicit `null` is absent; the message (position, transcript ID, run bundle ID, no value, no turn text); the function on a relabelled bundle; the offender in the first and in the second transcript, in a later run bundle, the first offender named; run bundle and transcript keep the member; tagged transcripts, `detected_language_raw` and turn tags unaffected; the rest of the declaration survives.

## 5. Validation

* **Red, repository tree** (`red/batch28-gate/red-new-tests.py3.1{1,2,3,4}.log`; each of 3.11, 3.12, 3.13 in its own `UV_PROJECT_ENVIRONMENT`, 3.14 in the shared `.venv`): the two new red files, 131 items: **71 fail, 60 pass** on every version. Failure reasons: `DID NOT RAISE ValidationError` (forgeries load), `Field required` for `declared_by`, `assert None is not None`. No failure is a fixture error.
* **Guards** (`guards-portability-imports.py3.1x.log`): `test_utc_datetime_portability.py` and `test_runtime_imports.py`: **109 passed** on every version.
* **Oracle** (a scratch copy with the reference source, regenerated schemas and the suggested edits; `oracle-green.py3.1x.log`): all new tests green on four versions; the whole suite on 3.14 with the edits: 8,606 passed, the 6 `.git` failures only (`oracle-full-suite-with-edits.py3.14.log`); without the edits: the 21 named failures (`oracle-full-suite-without-edits.py3.14.log`). The oracle and the tests share an author: this shows the tests are satisfiable and consistent, not that the readings are right.
* **Mutants of the oracle** (`mutants-b28-counts.txt`, `mutants-b28-declared-by.txt`): T-B13/T-B16 23 mutants (two of them rerun after their first patterns did not compile), N19 13 mutants, each killed by a named test (the turn rule absent, for non-scored, for one input kind only, ignoring withheld, any turn instead of agent turn, message naming the input kind, wrong field; total or not-applicable count not compared, compared against the bundle's turns or a constant, the wrong aggregation, passed or undetermined compared, scored only, errors skipped, ignoring the withheld gate, before the five fields, not-applicable first, skipping zero turns, ignoring `transcript_ids`; the N19 rule absent, for any declaration, for internal, in the message the value, no position, no transcript ID, no run bundle ID, wrong tag, first or last transcript only, first run bundle only, field required again, pattern dropped). One mutant of my first pass survived (checking only the last transcript) and drove the added test `the_first_transcript_is_found_and_the_first_offender_is_named`.
* **Whole suite, repository tree, Python 3.14** (`green/batch28-gate/full-suite-repo.py3.14.log`): **71 failed, 8,543 passed, 6 deselected**; the 71 are exactly the red tests of this batch (42 in `test_bundle_applicability_counts.py`, 29 in `test_export_declared_by.py`); no existing test fails, and the wider `strftime`/`strptime` scan passes (the dev's replacement is complete: no use remains in the package, the helpers, the converters, the tests or `scripts/`). The wider scan has teeth: `red/batch28-gate/utc-portability-mutant-wider-scan.log` (a scratch copy from before the replacement, plus planted uses in `scripts/` and `tests/support/mappings`) lists the files it flags.
* ruff, ruff format, `mypy --strict`, `check_headers.py`, `guard_private_fixtures.py`, `check_licenses.py`: `green/batch28-gate/gates.log`.

## 6. Ambiguities and readings (each with a recommendation)

* **A-C1. T-B13 and sealed transcript text.** An `ExportBundle` that seals `transcript_text` removes the transcript's turns from the embedded run bundle (the transcript record stays), and `check_run_bundle` runs on that run bundle without seeing the sealed payload. With T-B13 as decided, such a run bundle holds scored results that read no turn: rejected, unless the removed turns are declared `withheld` (which the existing `declared` branch of the test already does). Pinned (SCR-G5): rejected without the declaration. Recommendation: keep it (the run bundle is self-contained, and "the turns were removed" is exactly what `withheld` says); document it in `SCHEMA_POLICY.md` and the sealing section of `api-surface.md`, and make the future export builder declare the turns. Alternative: let `check_export_bundle` excuse a run bundle whose transcript has a `transcript_text` seal; that needs the run bundle check to be deferred, a bigger change.
* **A-C2. Only `scored` is judged by T-B13.** An `error` or `not_applicable` result over nothing is accepted (they claim nothing). Recommendation: keep; an `error` is for a scorer that failed and is not an assertion about the input.
* **A-C3. The withheld gate is per bundle, not per result** (as T-B10): with any `transcript` or `turn` withheld, T-B13 and the counts are off for every result. Recommendation: keep; a withheld ID does not say which transcript it belonged to.
* **A-C4. Which counts are recomputable.** Pinned: `total_turns` and `counts.not_applicable`. `passed`, `failed`, `undetermined` are not (the scorer or judge decides), and `counted_turns` is `passed + failed`. A stricter rule would compare, for a judge result, the tally of `judge.per_turn` verdicts with the counts; not pinned. Recommendation: later, with the judge record format frozen (one guard test, `the_split_into_passed_failed_and_undetermined_is_not_compared`, flips).
* **A-C5. Which field a scored per-turn result over zero turns is reported at.** Pinned: `status` (P-C5). Alternative: `per_turn.total_turns`. Recommendation: keep `status`, the class of the difference is "scored versus nothing to score".
* **A-C6. A run-level not-applicable result with a `per_turn` summary** (for example `missing_input` with a summary of zero counted turns). The model allows it and the bundle does not compare its counts (the run-level answer decides). Not tested. Recommendation: reject a summary on a run-level reason in the model (`per_turn` only for results whose metric ran or whose turns were all not applicable); a separate finding.
* **A-N1. Optional or required-but-nullable.** Pinned: optional with default `None` (P-N1). Recommendation: keep; the required-nullable alternative flips the schema tests only (the `required` list keeps the member).
* **A-N2. Location of the error and the order of tags.** The `[declared_by]` error is at the export root; its place among the portable-run-bundle tags is not pinned (P-N5). Recommendation: with `[profile]`, `[profile_digest]`, `[uri]`.
* **A-N3. Internal exports and bare records keep `declared_by`.** `Run.caller_external_id` is rejected in every purpose; `declared_by` only in `shared_export` and `benchmark` (the decision). Recommendation: as decided; an internal export is the producer's own record.
* **A-N4. A sealed or redacted transcript.** The rule reads the transcript record, which a payload seal or redaction does not remove (they cover text and audio). Not tested. Recommendation: keep; `declared_by` is metadata, not payload.
* **A-X1. Test-side date formatting.** `_support_b13._utc` and other helpers formatted instants with `strftime` (fine for 2026 on every platform, wrong for years below 1000 on glibc). The lead had a dev replace them; the wider scan in `test_utc_datetime_portability.py` keeps the class out (the last run of this pass is in `green/batch28-gate/`).

## 7. Requests, in one list

* Architect: section 1.2, section 2.2 (source and regenerations), the document updates named there, and the six SCR-G edits after the owner's review.
* Staff-reviewer: confirm A-C1 (sealed text), A-C4 and A-N1 readings; the gate decisions M4/M5 are implemented by these tests as pinned above.
* Dev: nothing (the strftime replacement was the dev's; the wider scan reports whether any user remains).
