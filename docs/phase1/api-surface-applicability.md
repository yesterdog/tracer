# Bundle applicability: the `[applicability]` rule and `MetricResult.transcript_ids` (batch 23, finding B15, decision D1)

Author: qa, 2026-10-24. Task: close the divergence D1 of the T-DEG work (`api-surface-tdeg.md` section 9: a stored result is never checked against the run it belongs to). Decided by the owner's auto-approval rule (decision log, "AUTO-APPROVED T-DEG decisions"): **D1** (add an `[applicability]` rule to `check_run_bundle` that re-runs `results.applicability` per result), **T-A3** (optional `MetricResult.transcript_ids`), **T-A4** (`agent_transcript` available implies an agent turn), and the documentation items T-A1 (`usage` means usage of the agent under test), T-A2 (no interruptions input kind yet), T-A5 and T-A6 (no change). Sources of truth: ADR 0004 (Decisions 2, 2a, 3 to 6, 6d, 6e, 6g, I3), ADR 0010 (Decision 3, 3a), `api-surface.md` sections 22 and 25 (`check_run_bundle`, `results.applicability`, B5, B15, B17, B22), `api-surface-tdeg.md` (the worlds the tests reuse).

New files only. Nothing existing was edited; the edits that existing tests and fixtures need are listed as named change requests in section 9 and carried as a patch in the evidence.

| File | Role |
|---|---|
| `packages/evalschema/tests/test_bundle_applicability.py` | the tests; 72 functions, 450 items |
| `packages/evalschema/tests/_support_b15.py` | helpers: the consistent worlds, the transplant, the two-transcript world, error documents (no tests) |
| `docs/phase1/api-surface-applicability.md` | this file |
| `docs/phase1/evidence/red/batch23-applicability/` | red logs and oracle logs on Python 3.11 to 3.14 (`red-applicability.py*.log`, `oracle-green.py*.log`), the T-DEG files with the oracle (`tdeg-oracle.py*.log`, `tdeg-d1-tests-red.log`), the mutants (`mutants-b23.txt`), the fallout of the rule on the existing suite (`fallout-*.log`), the gates, two reference patches (`oracle-src.patch`, `suggested-existing-edits.patch`) |

The four red T-DEG tests (`test_deg_bundle_rejects_a_scored_result_for_a_missing_input`, `..._rejects_not_applicable_for_an_input_the_run_has`, `..._rejects_a_wrong_not_applicable_reason`, `..._rejects_a_scored_result_on_an_untagged_transcript`, all in `test_tier_degradation_language.py`) are not edited. They turn green with the rule of this contract (section 7).

## 1. What is added, in one paragraph

`check_run_bundle` gets a last rule. For every `MetricResult` of the bundle it takes the metric definition from the bundle's own snapshot, finds the turns and transcripts the result read, calls `results.applicability` over them, and rejects with `ValueError` tagged `[applicability]` any difference between what is stored and what the re-run says in the status class (scored versus not applicable), `na_reason`, `missing_kinds` or `language_source` (and, **P**, `declared_language_transcript_ids`). A result with status `error` is exempt from the status comparison. `MetricResult` gets one optional additive field, `transcript_ids`, so that a bundle with several transcripts says which one each result read. The rule also checks that a run that lists `agent_transcript` has an agent turn (T-A4).

## 2. The field `MetricResult.transcript_ids`

```
transcript_ids: list[TranscriptId] | None = None
```

- Optional; absent and `null` are the same (digest domain, ADR 0006 Decision 5). The dump of a result without it writes `"transcript_ids": null`.
- Meaning: the transcripts the result read (ADR 0004 Decision 6d: the turns of the transcripts that supply its transcript-type inputs, and, for a metric that reads no turn, the transcript whose tag supplies the language).
- When present: at least one element (**P**, T-B2), no element twice (field error at `transcript_ids`), every element a `TranscriptId` (an artifact, turn or run ID, a malformed string, a number or `null` element is a field error), the order is kept as written and is not sorted (**P**, T-B3).
- A rejected value is never echoed in an error (the bundle hides its input, B21).
- References: each element resolves inside the run bundle (`[unresolved]`, naming the dangling ID) unless the ID is declared withheld (25.6.3). The check is against the records of the same run bundle only: a transcript that only another run bundle of an export holds is `[unresolved]`.
- Presence: the field is on any result, also on one of a metric that reads no transcript (**P**, T-B1).
- Content: the field is part of the result record's content wherever results are digested: the digest domain differs with the ids (order matters, arrays keep their order), `null` equals absent, and the seam (`check_bundle_consistency`) sees two contents for one result ID that differ in it (`ConflictingRecordError`).
- JSON Schema: an optional member (not in `required`), `null` or a non-empty array of transcript IDs (the ID pattern), `uniqueItems`. The three generated files that hold a result (`MetricResult`, `RunBundle`, `ExportBundle`) and `schema.lock` change.
- Models and schema agree: the property goes into `UNIQUE_ITEMS_PROPERTIES` (a set of property names) in `_support_b8.py`.
- Status: additive; no presence rule depends on it (`_result_conditions` needs no row, `PRESENCE_RULES` pins do not change; the oracle runs with them unchanged).

## 3. The rule

### 3.1 Where it runs and how it fails

- It is the **last** rule of `check_run_bundle` (after R2, the tier): `[duplicate]`, `[snapshot]`, `[membership]`, references, `[exchange]`, `[transcript]`, `[tool_call_seq]`, `[rescoring]`, `[result]`, `[verdict]`, `[cost]`, `[summary]`, `[marker]`, `[withheld]`, `[tier]`, then `[applicability]` (**P**, T-B11). A bundle that is wrong in two ways reports the earlier rule. Reason: every existing test that aims at one of those tags keeps passing; the rule is the only one that reads the language tags and the roles of the turns together with the results.
- It is a pure function of the bundle (no state, no clock, no network, nothing logged or printed); idempotent; it never changes the bundle.
- Tag `[applicability]`. The message is built from IDs and field names. It names the result **ID** and its **position** as `results[<index>]` (0-based, the position in `RunBundle.results`), and names a compared field that differs, spelled exactly `status`, `na_reason`, `missing_kinds`, `language_source` or `declared_language_transcript_ids` (as a whole word). With one difference it names that field and none of the other four. It quotes no text: no turn text, judge or scorer record, tag value or reason value (**P** for "names no other field"; the decision says "which result position and which field differs, quoting no text").
- Two other messages are pinned (section 3.6 and 3.8). Everything else about the wording is free.

### 3.2 Which transcripts a result read

Per result, with `held` the transcripts in `RunBundle.transcripts`:

1. `transcript_ids` present: those transcripts that are held (an ID that resolves through a `withheld` declaration is not held and not read; section 3.7).
2. absent and `len(held) == 1`: that transcript.
3. absent and `len(held) == 0`: none.
4. absent and `len(held) > 1`: the field is **required** when the metric needs transcripts, that is when its definition's `requires` includes `transcript` or `agent_transcript`, **or** the metric is language dependent (`languages` is a list, not `language_independent`). The check is on the definition, not on the outcome: `agent_wer` at T1 is `missing_input` (no reference text) and still needs the field (**P**, T-B6). The error is `[applicability]` and contains, verbatim, `transcript_ids required when the bundle holds several transcripts`, the result ID and `results[<index>]`. For a metric that does not need transcripts, no transcript is read and nothing is required.

### 3.3 Which turns a result read

The turns of the transcripts of 3.2 (matched by `Turn.transcript_id`), limited by the role the input kind names (ADR 0004 Decision 6d): if `requires` includes `agent_transcript`, the turns with `role == agent`; else if it includes `transcript`, all those turns; else none (an audio-only metric reads no turn and takes the language from the transcripts of 3.2).

### 3.4 The expected outcome (per-conversation metrics)

`results.applicability(definition, run, turns=<3.3>, transcripts=<3.2>)` returns `(na_reason, missing_kinds, language_source, declared_language_transcript_ids)`. Nothing else is read: not the tier label, not the configured languages, not a declaration beyond ADR 0004 Decision 6g.

### 3.5 The comparison

For a result whose status is `scored` or `not_applicable`, in this order (the first difference is reported):

| Field | Stored | Expected |
|---|---|---|
| `status` | `not_applicable` or not | the re-run has an `na_reason` or not |
| `na_reason` | `result.na_reason` | the re-run's |
| `missing_kinds` | the list, in order | the re-run's list, in the order of `requires` (**P**, T-B8) |
| `language_source` | `result.language_source` | the re-run's |
| `declared_language_transcript_ids` (**P**, T-B7) | the sorted list | the re-run's (sorted by string form) |

### 3.6 Results with status `error` (**P** for the second bullet, T-B9)

- Exempt from the status comparison, with one exception: when the re-run says `missing_input` or `agent_channel_unknown` (the run-level reasons, decided before any language step) an `error` is rejected, field `status`. ADR 0004 I3: an error is for a metric whose inputs existed.
- `language_source` and `declared_language_transcript_ids` of an `error` result are compared with the re-run (they are required on an error of a language-dependent metric). An `error` result where the re-run says a language reason (`unsupported_language`, ...) is accepted when its provenance matches; an `error` of an untagged transcript that says `observed` is rejected. `na_reason` and `missing_kinds` do not exist on an error and are not compared.

### 3.7 Per-turn metrics (`aggregation.kind == per_turn_share`) (**P**, T-B5)

The language steps run on each read turn on its own (ADR 0004 Decision 6e, ADR 0010 Decision 3): `results.applicability(definition, run, turns=[turn], transcripts=<3.2>)` for each turn of 3.3. Then:

1. If any turn's answer is a run-level reason (`missing_input` or `agent_channel_unknown`), that answer is the expected one (kinds as returned, no source).
2. Else if any turn is applicable, the expected status is scored (no `na_reason`).
3. Else (every turn not applicable) the expected `na_reason` is the strongest of the turns' reasons by `NA_REASON_PRECEDENCE`; `missing_kinds` is empty.
4. `language_source`: `declared` if any turn used a declaration, else `observed` if any turn had an effective language (also a turn that was then not applicable), else `None`. `declared_language_transcript_ids`: the sorted union.
5. The counts in `per_turn` are not compared (T-B15, T-B16).

A per-turn metric that reads no turn at all (an empty call) is not pinned (T-B4).

### 3.8 Withheld records (B22: skip only what cannot be re-run) (**P** for the scope, T-B10)

- If the bundle declares a withheld record of kind `transcript` or `turn`, the language steps cannot be re-run. The run-level step still is: the expected run-level answer comes from `results.applicability` over the run alone. A result whose run-level answer is `missing_input` or `agent_channel_unknown` must be `not_applicable` with that reason and those kinds; any other result must not carry a run-level reason. A language outcome (scored, a language reason, a source) is accepted as stored.
- Withheld kinds `artifact`, `exchange`, `tool_call`, `span`, `usage_record`, `cost_record` take nothing away from the rule. A withheld **result** is not there to check; the other results are still checked (the verdict is not recomputed when it names the withheld result: the existing B22 rule).
- `transcript_ids` that name a withheld transcript resolve (`[unresolved]` does not fire); the transcript is not held, so it is not read.
- T-A4 (section 3.9) is skipped when any record of kind `turn` is declared withheld.

### 3.9 T-A4: `agent_transcript` implies an agent turn

When the run's `available_inputs` has `agent_transcript`, the bundle holds at least one turn, and no record of kind `turn` is declared withheld: at least one turn of the bundle (of any transcript) has `role == agent`; otherwise `[applicability]`. The message names the run ID and the input kind `agent_transcript`, and no text. A bundle with no turn (an empty call, the text left out) is not judged by this rule. Only `agent_transcript` is checked; `transcript` implies a turn is not pinned (T-B13).

## 4. Interactions

- **Rescoring (B5).** A rescoring is a new `RunBundle` (new bundle, result and verdict IDs). Its results are judged against the run exactly as the first bundle's: a rescored answer is accepted, a rescored result that the run cannot support is rejected. The rule keeps no memory of an earlier bundle.
- **Tiers.** The 18 consistent worlds built from `_support_b13` (T0, T0 with an empty STT pass, T0 with a reported agent cost, T0m, T1 with and without spans, T1 and T2 for both caller origins, the language scenarios: untagged, declared at T1 and T2, `ta-IN`, `hi-Latn`, one foreign agent turn, untagged with one foreign turn) load unchanged, alone and inside an internal `ExportBundle`. The 165 passing T-DEG tests stay green (169 pass with the oracle).
- **`ExportBundle`.** Each embedded run bundle runs the rule on load; the error is located at `run_bundles.<i>`.
- **Hiding (B21).** The bundle hides its input; no message holds a value of the document.
- **Order of data.** The rule reads the models; it never reads text.

## 5. Why the tests are shaped this way

- **The transplant matrix** is the main engine. The T-DEG worlds hold, for every metric, the result the reference evaluator produced at each tier and for each language scenario; the matrix is hand-written (`api-surface-tdeg.md` section 3). For every ordered pair of the 18 worlds and every one of the 13 metrics, the result the donor stored replaces the target's (under the target's IDs, verdict rebuilt). Whether that bundle must be rejected, and which fields differ, is decided by comparing the two stored documents, not by the library: 306 pairs, 13 metrics each. A pair where nothing differs must load (a guard); a pair that differs must be rejected `[applicability]`, naming only fields on which they differ.
- **Targeted forgeries** pin each field alone (status class both ways, `na_reason`, `missing_kinds` as subset, superset and swap, `language_source` both ways, the declared list), the error-result rules, and the per-turn aggregation.
- **A two-transcript world** (`multi_world`) is built from the T1 world plus a second transcript of our own STT (untagged, `ta-IN` or declared) and shows that the ids choose what the language is read from.
- **Guards** (pass today, must stay green): every consistent world loads and `check_run_bundle` accepts it twice, the lists of metrics that need the field cover the 13, an error result where the inputs exist is accepted, the T0 bundle that does not list `agent_transcript` loads.

## 6. Test map

Counts are items collected. R = fails today and passes with the oracle; G = guard, passes today and with the oracle.

| Test (all `test_applic_...`) | Items | R/G | What it pins |
|---|---|---|---|
| `field_is_optional_and_defaults_to_none` | 1 | R | field exists, default `None`, not required |
| `field_keeps_ids_in_the_given_order_and_round_trips` | 1 | R | order kept (P), JSON round trip |
| `field_none_is_null_in_json_and_absent_without_nulls` | 1 | R | dump |
| `field_rejects_values_that_are_not_a_list_of_transcript_ids` | 9 | R | other id kinds, malformed, numbers, null element, string, object |
| `field_rejects_a_transcript_listed_twice` | 1 | R | no duplicates |
| `field_rejects_an_empty_list` | 1 | R | (P) at least one |
| `json_schema_has_an_optional_nullable_array_of_transcript_ids` | 7 | R | schema accepts and rejects, not required |
| `the_shipped_schema_files_list_the_field` | 1 | R | regenerated files |
| `ids_are_part_of_the_result_record_content` | 1 | R | digest domain |
| `the_seam_sees_two_contents_for_one_result_id` | 1 | R | `ConflictingRecordError` |
| `ids_must_resolve_inside_the_bundle` | 1 | R | `[unresolved]` names the ID |
| `a_withheld_transcript_resolves` | 1 | R | resolves through the declaration |
| `ids_do_not_resolve_across_the_run_bundles_of_an_export` | 1 | R | per run bundle |
| `a_rejected_id_value_is_not_echoed` | 1 | R | hiding |
| `a_duplicate_id_in_a_bundle_is_rejected` | 1 | R | |
| `consistent_bundles_load_and_pass_the_function` | 18 | G | the worlds load; function pure |
| `consistent_bundles_load_in_an_internal_export` | 18 | G | in an export |
| `the_results_of_a_world_have_no_transcript_ids_unless_given` | 1 | G | the T-DEG worlds derive |
| `naming_the_only_transcript_equals_deriving_it` | 5 | R | explicit equals derived |
| `a_result_stored_for_another_run_is_rejected_unless_it_agrees` | 306 | 290 R, 16 G | the transplant matrix |
| `status_class_scored_for_not_applicable` | 1 | R | |
| `status_class_not_applicable_for_scored` | 1 | R | |
| `na_reason_only` | 1 | R | |
| `na_reason_agent_channel_unknown_is_not_missing_input` | 1 | R | |
| `missing_kinds_only` | 6 | R | subset, other subset, superset, swap, extra, wrong one |
| `missing_kinds_order_follows_requires` | 1 | R | (P) |
| `language_source_only_declared_to_observed` | 1 | R | |
| `language_not_observed_keeps_source_none_when_no_turn_has_a_language` | 1 | R | |
| `language_not_observed_keeps_the_source_of_the_turns_that_had_a_language` | 1 | R | N29 |
| `unsupported_language_is_not_language_not_observed` | 1 | R | |
| `declared_ids_name_the_transcripts_that_were_used` | 1 | R | (P) fifth field |
| `error_result_where_the_input_is_missing_is_rejected` | 1 | R | |
| `error_result_where_the_inputs_exist_is_accepted` | 5 | G | |
| `error_result_is_exempt_from_the_status_class_also_for_a_language_reason` | 1 | G | (P) |
| `error_per_turn_result_is_accepted_where_the_metric_could_run` | 1 | R | |
| `error_result_still_has_its_language_source_checked` | 1 | R | (P) |
| `per_turn_all_turns_not_applicable_takes_the_strongest_reason` | 1 | R | (P) |
| `per_turn_scored_when_one_turn_counts` | 1 | R | |
| `per_turn_run_level_reason_comes_first` | 1 | R | |
| `two_transcripts_with_ids_on_every_result_load` | 1 | R | |
| `the_metric_lists_cover_the_thirteen` | 1 | G | helper self-check |
| `ids_are_required_with_several_transcripts_for_metrics_that_read_them` | 13 | R (8 required, 5 not) | T-A3 |
| `ids_given_on_a_metric_that_does_not_need_them_are_accepted` | 1 | R | (P) |
| `one_transcript_is_derived_zero_is_none` | 1 | G | |
| `the_ids_choose_which_transcript_the_language_is_read_from` | 1 | R | 10 cases |
| `the_ids_choose_the_transcript_of_an_audio_metric_too` | 1 | R | Decision 6d |
| `a_second_transcript_outside_the_lists_is_read_when_named` | 1 | R | |
| `a_declared_second_transcript_makes_the_source_declared` | 1 | R | Decision 6g |
| `the_run_level_reasons_do_not_depend_on_which_transcript_is_named` | 1 | R | |
| `ids_inside_an_export_round_trip_and_validate_against_the_schema` | 1 | R | |
| `agent_transcript_available_needs_an_agent_turn` | 1 | R | T-A4 |
| `agent_transcript_with_one_agent_turn_is_enough` | 1 | G | |
| `the_rule_needs_agent_transcript_to_be_listed` | 1 | G | |
| `a_bundle_without_turns_is_not_judged_by_the_agent_turn_rule` | 1 | G | |
| `withheld_turns_excuse_the_agent_turn_rule` | 1 | G | (P) |
| `a_withheld_transcript_skips_the_language_steps_only` | 1 | R | B22 |
| `a_withheld_transcript_still_rejects_na_for_an_input_the_run_has` | 1 | R | |
| `withheld_turns_skip_the_language_steps_only` | 1 | R | |
| `the_transcript_metrics_read_all_turns_the_agent_ones_only_the_agent_turns` | 1 | R | Decision 6d roles |
| `a_withheld_result_leaves_the_other_results_checked` | 1 | R | |
| `a_withheld_artifact_does_not_block_the_run_level_steps` | 1 | R | |
| `a_rescoring_is_a_new_bundle_and_is_judged_on_its_own` | 1 | R | B5 |
| `message_names_the_position_the_result_and_the_field_and_quotes_no_text` | 1 | R | |
| `the_position_is_the_one_of_the_forged_result` | 1 | R | |
| `is_the_last_rule_to_run` | 1 | R | (P) order |
| `the_function_judges_a_bundle_that_was_not_validated` | 1 | R | |
| `a_forged_bundle_in_an_export_is_rejected_at_the_run_bundle` | 1 | R | |
| `prompt_injection_in_the_turns_changes_nothing` | 1 | R | adversarial |
| `a_huge_call_validates_and_a_forgery_in_it_is_still_found` | 1 | R | 2,000 turns |
| `nothing_is_printed_or_logged` | 1 | R | secrets in logs |
| `the_documents_are_not_modified_by_the_check` | 1 | G | |
| `compared_fields_are_the_five_of_the_contract` | 1 | G | helper self-check |

(P) marks a reading of mine where the decisions are silent; section 8 lists each with a recommendation.

## 7. Validation

Numbers are filled in the evidence directory and summarised here (2026-10-24).

- **Red, repository tree:** 450 items, **383 fail, 67 pass** (the guards) on Python 3.11.16, 3.12.14, 3.13.15 and 3.14.7, each in its own `UV_PROJECT_ENVIRONMENT`, the same count on every version. The failures are for the right reasons: `extra_forbidden` on `transcript_ids` (the field does not exist), `DID NOT RAISE` or "loaded although [fields] differ" (the rule does not exist), a missing property in the JSON Schema or a missing dump member. No test fails for a fixture error.
- **Oracle:** a throwaway implementation in a scratch copy of the repository (the rule and the field as in section 3, schemas regenerated; `oracle-src.patch` is the reference source) turns all **450 green** on all four versions. The oracle and the tests share one author: this shows the tests are satisfiable and consistent, not that the readings are right.
- **The four red T-DEG tests:** all four turn **green** with the oracle (`test_tier_degradation*.py`: 169 pass, 0 fail), so the 165 that pass today stay green.
- **Mutants:** 58 hand-made mutants of the oracle (`mutants-b23.txt`, definitions in `mutants-definitions.py.txt`): **57 killed, 1 survives**. Every kind of forgery has a mutant that a test kills: each compared field not compared (five mutants, plus the set-comparison and the error-handling variants), status both ways, errors fully exempt and not exempt, the withheld scope (language skipped never, always, only for transcripts, only for turns, and run-level leniency), T-A4 (absent, ignoring withheld turns, firing without turns), the requirement and the derivation of the ids (never, for all, for the transcript kinds only, for the language-dependent only, with 1 or 3 transcripts, reading all, the first, the last, none), the turn selection (not limited to the named transcripts, no role filter, transcript metrics reading nothing, audio metrics reading turns), per-turn aggregation (weakest reason, all scorable, any-not-applicable, source rules, one answer for all turns, run-level last), message content (no position, no ID, quotes text, names every field), the rule order (before the verdict, before the tier), the `[unresolved]` check (absent, ignoring withheld), and the field itself (duplicates, empty, sorted, required, default empty, excluded from the dump, any id kind). The survivor `derive-first-of-several` is equivalent: with several transcripts and no ids, the metrics that read transcripts are exactly those the rule requires ids for, so the derived set is never used.
- **Existing suite with the oracle** (`fallout-A-...`, `fallout-B-...`, `fallout-C-...`; Python 3.14, `test_mapping_f8.py` left out, it is dev's concurrent work): the bare oracle over the unchanged tree fails **396** tests (395 existing: 332 in the bundle-family files, because `_support_b7.world()` builds results that contradict their run, and 63 schema, lock and golden drift; plus the one test of the new file that reads the shipped schema file). After the regenerations of 9.2 and SCR-1, **13** existing tests still fail (SCR-2 to SCR-7). With all of section 9.3 the full suite is green: **7,947 passed**, 2 failed, and the 2 are `test_tdrift.py` tests that fail only because the scratch copy lacks `docs/phase1/evidence/` (they pass in the repository).
- ruff, ruff format, mypy `--strict` (158 files), `check_headers.py`, `guard_private_fixtures.py`, `check_licenses.py` pass (`gates.log`).

## 8. Ambiguities and readings (each with a recommendation)

- **T-B1. `transcript_ids` on a metric that reads no transcript.** Pinned: accepted (only the ID resolution applies). Recommendation: accept; the rule does not need to forbid it, and a producer may record every transcript it saw.
- **T-B2. Empty list.** Pinned: rejected (field error). `None` means "derive"; `[]` would mean "read nothing", which no result of a transcript-reading metric can claim. Recommendation: reject, `minItems: 1` in the schema.
- **T-B3. Order.** Pinned: the order is kept, not sorted (`declared_language_transcript_ids` is sorted because it is a set of a derived fact; `transcript_ids` is a producer's statement). Alternative: sort and require sorted for a canonical digest. Recommendation: keep the order; if the architect prefers sorted, one test flips (`field_keeps_ids_in_the_given_order_and_round_trips`, and `ids_...choose...` cases that list `[second, first]`).
- **T-B4. A per-turn metric that reads no turn** (the bundle holds a transcript and no turn, or no agent turn). Not pinned, and not tested beyond "T-A4 does not fire". `applicability` is defined over one turn at a time; with no turn the run-level steps are meaningful and the status class is not (a scored per-turn result needs a counted turn). Recommendation: for a per-turn metric with no read turn, compare only the run-level reasons.
- **T-B5. Per-turn aggregation of `language_source`.** Pinned as in 3.7, which is also what the T-DEG reference evaluator does: `declared` wins over `observed`, a turn that was then not applicable still counts as having had a language, the declared IDs are the union. Recommendation: accept; the alternative (only counted turns) would make the source of an all-not-applicable result `None` and hide the observation that explains the reason.
- **T-B6. The requirement is on the definition, not the outcome.** Literal reading of T-A3. A result that is `missing_input` (no transcript was read) still needs the field with several transcripts. Recommendation: keep it literal; a rule that depends on the stored outcome would be circular.
- **T-B7. `declared_language_transcript_ids` as a fifth compared field.** The decision names four. Without it a forged list (a different transcript than the one whose declaration was used) passes. Recommendation: compare it; five fields, one extra comparison.
- **T-B8. `missing_kinds` order.** Pinned: exactly the order of `requires`, as `applicability` returns it. Recommendation: accept; the alternative (set comparison) accepts a re-ordered list that digests differently.
- **T-B9. Error results.** Pinned as in 3.6 (exempt from the status class except a run-level reason; the language provenance is compared). Recommendation: accept. The one alternative worth considering is to also forbid an `error` where the re-run says a language reason: ADR 0004 I3 says an error is for a metric whose inputs existed, and a language reason says the metric did not apply; the decision says error results are exempt, so it is not pinned.
- **T-B10. Withheld scope.** Pinned as in 3.8: only withheld kinds `transcript` and `turn` switch off the language steps, for the whole bundle (not per result); only held transcripts count for "several". Recommendation: accept; per-result skipping would need to know which transcript each withheld turn belonged to, which a withheld ID does not say.
- **T-B11. Position in `check_run_bundle`.** Pinned: last. The existing suite then keeps its tag expectations. Recommendation: accept. If the rule runs earlier, `test_applic_is_the_last_rule_to_run` flips and more existing tests need edits (fixtures whose results contradict their runs, see section 9).
- **T-B12. Message form.** `results[<index>]`, 0-based, plus the result ID; one field named (the first that differs). Recommendation: accept.
- **T-B13. T-A4 scope.** Only `agent_transcript`; the decision does not ask for `transcript` implies a turn. A result that names a transcript with no agent turn for an `agent_transcript` metric reads zero agent turns and is judged on the transcript's tag alone (a metric can then be `scored` over nothing). Recommendation: add a follow-up rule later (a result of an `agent_transcript` metric names a transcript that has an agent turn, unless turns are withheld); it is not part of D1.
- **T-B14. Schema version.** The field is additive and optional; `SCHEMA_VERSION` stays `0.0.1` while it is unreleased, and the lock is regenerated (`api-surface.md` 26.5.1). After a release this would be a bump.
- **T-B15. The transplant test accepts a bundle whose per-turn counts differ.** It accepts a per-turn result with other counts when the four fields agree. If the architect later adds count checks (T-B16), the guard half of the matrix changes (the donor's counts would be rejected); that is deliberate work, not a regression.
- **T-B16. Per-turn counts against the turns.** `per_turn.counts.not_applicable` and `total_turns` could be compared with the number of read turns for which the re-run says not applicable. Not in D1. Recommendation: later, with the pre-rule guard of T-B4.

## 9. Requests to the architect

### 9.1 Source

`oracle-src.patch` is a working reference (a patch against the current `bundle.py` and `results.py`, not for blind application).

1. `results.py`: add the helper `_unique_read_ids` (rejects a repeated ID, message `transcript_ids lists one transcript twice`) and the field after `evidence_refs`:
   `transcript_ids: Annotated[list[TranscriptId], AfterValidator(_unique_read_ids), Field(min_length=1, json_schema_extra={"uniqueItems": True})] | None = None`. Update the `MetricResult` and module docstrings.
2. `bundle.py`:
   - in `_check_references`, after the declared-language loop: `for transcript_id in result.transcript_ids or (): need(transcript_id, result.id, "transcript_ids")`;
   - a new `_check_applicability(bundle, withheld_kinds)` (section 3), called as the last statement of `check_run_bundle` (after `check_observed_tier`), and its helpers (`_read_turns`, the per-turn aggregator, the message builder);
   - update the docstrings of the module and of `check_run_bundle` (the tag order now ends with `[tier]`, `[applicability]`).
3. `api-surface.md`: 25.3.1 add the `[applicability]` row (words pinned: `transcript_ids required when the bundle holds several transcripts`); 25.4.1 add step 9 and strike "the bundle does not recompute ... applicability" in the "Not rules of the bundle" paragraph; 25.11 and B15 (mark B15 decided: re-run implemented in batch 23); 22.1 add the field to `MetricResult`. The register row `CO-BUNDLE-CROSS-RECORD` needs the new anchor text (the summary must mention the applicability re-run).
4. ADR 0004: a new Decision (the bundle re-runs applicability; the field `transcript_ids`; T-A4) and the T-A1 sentence (`usage` in `available_inputs` is usage of the agent under test; the bundle always holds our own evaluation usage, which is not an input of an agent metric); T-A2 as a stated limitation (no `interruptions` kind until a Phase 3 metric reads `Turn.interrupted`). ADR 0008: one sentence that adding an optional field within an unreleased version regenerates the lock.

### 9.2 Regenerations (commands)

- `uv run --locked python -m tracer_evalschema.schema_export --write`: `ExportBundle.schema.json`, `MetricResult.schema.json`, `RunBundle.schema.json`, `schema.lock`.
- `uv run --locked python scripts/generate_field_docs.py --write`: `docs/schema/ExportBundle.md`, `MetricResult.md`, `RunBundle.md`.
- `uv run --locked python scripts/build_golden_fixtures.py --write`: after the test edits below. In the oracle run this rewrote 55 golden files (the `MetricResult` valid and invalid ones, the `RunBundle`/`ExportBundle` valid and invalid ones) and `frozen/0.0.1/` (`digests.json`, the maximal `ExportBundle`, `MetricResult` (2) and `RunBundle`).

### 9.3 Existing tests and fixtures that change (named spec change requests; `suggested-existing-edits.patch` carries them)

I may not edit existing tests. Each is needed because it builds a bundle whose stored result contradicts its own run, or enumerates the fields of a result.

| # | File and place | Change | Why |
|---|---|---|---|
| SCR-1 | `_support_b7.py`, `world()` | add `"turn_timing"` to `run.available_inputs`; set `detected_language = "hi-IN"` on the transcript when `declared` is false; the declaration becomes `hi-IN` (was `mr-IN`, outside the definitions' `["en", "hi"]`); the latency result gets `language_source` `declared` and `declared_language_transcript_ids = [tsc_id]` when `declared` | the world scores a latency metric that requires `turn_timing` the run does not list, and a language-dependent judge over an untagged transcript; both are exactly what the rule rejects. This one edit, with the regenerations, leaves 13 failures (section 7). |
| SCR-2 | `test_export_rules.py`, `test_benchmark_consent_of_tool_calls_comes_from_the_run_markers` | `only_text` run: `available_inputs` also lists `turn_timing`; `only_audio`: `results, verdict = [], None` instead of filtering evidence references | the run is edited to drop inputs while keeping results that need them |
| SCR-3 | `test_export_rules.py`, `_with_second_records` | set `transcript_ids = [first transcript]` on every result | a second transcript requires the field (T-A3) |
| SCR-4 | `test_results.py`, `test_metric_result_shape_and_enums` | add `"transcript_ids"` to the expected field set | the field |
| SCR-5 | `_support_b8.py`, `UNIQUE_ITEMS_PROPERTIES` | add `"transcript_ids"` | the schema carries `uniqueItems` there |
| SCR-6 | `_support_b8.py`, `CODE_ONLY_RULES`, row `CO-BUNDLE-CROSS-RECORD` | two claims: `("remove", "list_empty")` on `*Bundle` `run.available_inputs`, and `("remove", "to_null")` on `*Bundle` `transcripts.*.detected_language` | the models now reject documents the schema accepts (a run without inputs against its results; an untagged transcript against a scored result); `test_schema_gaps` and `test_schema_agreement` fail without them |
| SCR-7 | `_support_b9_valid.py`, `_result_maximal` | `_scored(transcript_ids=[b2.new_id("tsc")])` | every property needs a valid golden fixture (`property_coverage.py` and `test_golden_fixtures.py`) |
| SCR-8 | `_support_b9_invalid.py` (additive, recommended) | two invalid `MetricResult` fixtures: `transcript-ids-twice`, `transcript-ids-empty` (expected field `transcript_ids`) | golden coverage of the new rejections |

No other test needed a change in the oracle run: the property tests, the closed-enum tables, the `PRESENCE_RULES` pins and the schema-lock tests only need the regenerations of 9.2. The F8 and T-DEG files are untouched and independent.

## 10. Not covered here

- Per-turn counts against the turns (T-B16) and a per-turn metric that reads no turn (T-B4).
- An `agent_transcript` metric naming a transcript without agent turns (T-B13).
- `transcript` implies at least one turn (T-DEG T-A4 text; not part of the decision).
- The reporting-layer inconclusive share (T-A5) and the interruptions input kind (T-A2): documentation only.
- Live providers, other tiers than the four columns, real captures: the worlds are synthetic.
