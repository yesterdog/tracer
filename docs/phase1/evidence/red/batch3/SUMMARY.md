# Red-first evidence, batch 3 (P1-10a, P1-10b, P1-10c, P1-11, P1-22, P1-12, P1-13 and P1-27 tests, qa)

Produced 2026-10-23. Each `<group>.py<ver>.log` here holds the full pytest output with header (date, command, interpreter and package versions, uv.lock sha256, git status line) for one test file against the REAL repository code. No commits, nothing staged. Interpreters: Python 3.11.16, 3.12.14, 3.13.15 (`.venv`), 3.14.7; pydantic 2.14.1, pytest 9.1.1, jsonschema 4.26.0, hypothesis 6.170.5. Earlier evidence in `../`, `../batch2/`, `../batch2-rev8/`, `../rev7/` is untouched.

Outcome per test, and the failure type of each failing test, are identical on 3.11, 3.12, 3.13 and 3.14 (compared test by test from the junit XML of the runs).

## Counts

| Group (test file) | Log | Tests | Failed (red) | Passed on arrival |
|---|---|---|---|---|
| p1-10a-artifact (`test_artifact.py`) | `p1-10a-artifact.py<ver>.log` | 14 | 14 | 0 |
| p1-10b-transcript (`test_transcript.py`) | `p1-10b-transcript.py<ver>.log` | 24 | 24 | 0 |
| p1-10c-events (`test_events.py`) | `p1-10c-events.py<ver>.log` | 14 | 14 | 0 |
| p1-11-results (`test_results.py`) | `p1-11-results.py<ver>.log` | 38 | 38 | 0 |
| p1-11-results-language (`test_results_language.py`) | `p1-11-results-language.py<ver>.log` | 22 | 22 | 0 |
| p1-12-snapshot (`test_snapshot.py`) | `p1-12-snapshot.py<ver>.log` | 13 | 13 | 0 |
| p1-12-run (`test_run.py`) | `p1-12-run.py<ver>.log` | 7 | 7 | 0 |
| p1-13-dataset (`test_dataset.py`) | `p1-13-dataset.py<ver>.log` | 5 | 5 | 0 |
| p1-22-verdict (`test_verdict.py`) | `p1-22-verdict.py<ver>.log` | 24 | 24 | 0 |
| p1-27-privacy (`test_privacy.py`) | `p1-27-privacy.py<ver>.log` | 18 | 18 | 0 |
| batch3-entities (`test_batch3_entities.py`) | `batch3-entities.py<ver>.log` | 62 | 62 | 0 |
| **Total new** | | **241** | **241** | **0** |
| gate-and-changed-existing (`test_ids.py`, `test_versioning.py`, `test_metric_definition.py`) | `gate-and-changed-existing.py<ver>.log` | 82 | 4 | 78 |
| full-suite (`packages/evalschema/tests`) | `full-suite.py<ver>.log` | 836 | 246 | 590 |

Full tree: 836 tests collected, no collection errors, no skips, no xfails, no empty parametrization. Full suite on each interpreter: 246 failed and 590 passed. The 590 are the batch 1 and batch 2 tests, unchanged by this batch (the rows I added to `test_ids.py` and `test_metric_definition.py` do not change an outcome yet: see below). The 246 failed are the 241 new tests plus the five that were already red before this batch: `test_every_entity_has_registered_prefix`, `test_value_objects_have_no_entity_fields`, `test_opaque_id_fields_are_exactly_the_adr_allowlist` (all in `test_ids.py`), `test_versioned_entity_set_matches_adr` (in `test_versioning.py`) and `test_policy_documents_low_entropy_digest_limitation` (in `test_persona_profile.py`, waits for P1-20a). One of the new 241, `test_policy_documents_free_text_lint_limitation`, also waits for `SCHEMA_POLICY.md` (P1-20a) and is red for that reason even with a perfect implementation.

## Why each red test is red

Failure type vocabulary: `ModuleNotFoundError` = the module the test needs (`tracer_evalschema.privacy`, `.artifact`, `.transcript`, `.events`, `.results`, `.verdict`, `.snapshot`, `.run`, `.dataset`) is not written yet (the intended reason for code tests); `AttributeError` or `AssertionError` with 'is not implemented yet' = a class found by name is missing; any other type is listed with its reason. No test fails because of its own code (no NameError, TypeError or import error in a test file).

| Test file | Test (parameters collapsed) | Count | Failure type | One-line reason |
|---|---|---|---|---|
| `test_artifact.py` | `test_artifact_sha256_hex` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_audio_format_fields` | 1 | AssertionError (class not implemented yet) | AudioFormat is not implemented yet |
| `test_artifact.py` | `test_artifact_uri_rejects_presigned_signature` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_artifact_uri_rejects_wider_credential_names_and_patterns` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_artifact_errors_do_not_echo_uri_credentials` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_channel_role_set_exact` | 1 | AssertionError (class not implemented yet) | ChannelMap is not implemented yet |
| `test_artifact.py` | `test_channel_map_declared_forbids_confidence` | 1 | AssertionError (class not implemented yet) | ChannelMap is not implemented yet |
| `test_artifact.py` | `test_channel_map_inferred_requires_method_confidence` | 1 | AssertionError (class not implemented yet) | ChannelMap is not implemented yet |
| `test_artifact.py` | `test_channel_map_single_agent_channel` | 1 | AssertionError (class not implemented yet) | ChannelMap is not implemented yet |
| `test_artifact.py` | `test_channel_map_indexes_unique` | 1 | AssertionError (class not implemented yet) | ChannelMap is not implemented yet |
| `test_artifact.py` | `test_channel_index_within_channels` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_channel_map_artifact_must_be_audio` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_unknown_agent_channel_excludes_agent_audio` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_artifact.py` | `test_mono_channel_mixed_excludes_agent_audio` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_transcript.py` | `test_turn_seq_required_timing_optional` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_seq_unique_within_transcript` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_end_ge_start` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_segment_end_ge_start` | 1 | AssertionError (class not implemented yet) | Segment is not implemented yet |
| `test_transcript.py` | `test_segment_times_within_turn` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_segment_granularity_phrase` | 1 | AssertionError (class not implemented yet) | Segment is not implemented yet |
| `test_transcript.py` | `test_confidence_in_0_1` | 1 | AssertionError (class not implemented yet) | Segment is not implemented yet |
| `test_transcript.py` | `test_timing_source_none_iff_no_timing` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_has_no_started_at_field` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_source_enum_exact` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_transcript_source_names_producer` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_interrupted_none_distinct_from_false` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_role_enum_exact` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_language_observed_any` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_language_raw_verbatim` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_turn_and_segment_bidi_flag_computed_and_verified` | 1 | AssertionError (class not implemented yet) | Segment is not implemented yet |
| `test_transcript.py` | `test_exchange_shape_and_sources` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_exchange_index_references_existing_exchange` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_timeline_origin_artifact_requires_artifact_ref` | 1 | AssertionError (class not implemented yet) | TimelineOrigin is not implemented yet |
| `test_transcript.py` | `test_timeline_origin_required_iff_offsets_present` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_transcript_shape_and_optional_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_transcript_declared_language_shape_and_provenance` | 1 | AssertionError (class not implemented yet) | LanguageDeclaration is not implemented yet |
| `test_transcript.py` | `test_declared_language_only_on_untagged_imported_transcript` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_transcript.py` | `test_long_transcript_50k_turns_under_2s` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_events.py` | `test_truncated_args_not_complete_not_parsed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_arguments_present_implies_complete` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_cancelled_status` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_result_optional` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_result_complete_requires_result_raw` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_result_error_none_when_not_reported` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_tool_call_seq_unique_per_run` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_tool_call_event_shape` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_stage_span_nonneg` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_stage_span_other_keeps_stage_raw` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_stage_span_shape` | 1 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_events.py` | `test_evidence_ref_resolves_in_bundle` | 1 | AssertionError (class not implemented yet) | EvidenceRef is not implemented yet |
| `test_events.py` | `test_evidence_ref_shape` | 1 | AssertionError (class not implemented yet) | EvidenceRef is not implemented yet |
| `test_events.py` | `test_ingest_warning_shape` | 1 | AssertionError (class not implemented yet) | IngestWarning is not implemented yet |
| `test_results.py` | `test_metric_result_shape_and_enums` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_na_value_and_passed_none` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_na_requires_reason` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_na_missing_kinds_equal_requires_minus_available` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_na_reason_precedence` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_error_structured_no_value` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_judge_undetermined_is_error` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_scored_value_matches_type` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_scored_without_pass_rule_has_passed_none` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_rating_value_within_scale` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_enum_value_in_options` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_metric_value_rejects_nan_inf` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_missing_inputs_helper` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_available_inputs_not_derived_from_tier` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_per_turn_verdict_undetermined` | 1 | AssertionError (class not implemented yet) | JudgeInfo is not implemented yet |
| `test_results.py` | `test_judge_p_yes_absent_when_not_reported` | 1 | AssertionError (class not implemented yet) | JudgeInfo is not implemented yet |
| `test_results.py` | `test_judge_info_shape` | 1 | AssertionError (class not implemented yet) | JudgeInfo is not implemented yet |
| `test_results.py` | `test_judge_reported_model_verbatim_and_optional` | 1 | AssertionError (class not implemented yet) | JudgeInfo is not implemented yet |
| `test_results.py` | `test_judge_per_turn_reported_model_verbatim_and_optional` | 1 | AssertionError (class not implemented yet) | JudgeInfo is not implemented yet |
| `test_results.py` | `test_prompt_injection_text_is_data` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results.py` | `test_per_turn_share_excludes_undetermined_turns_and_counts_them` | 1 | AssertionError (class not implemented yet) | PerTurnSummary is not implemented yet |
| `test_results.py` | `test_per_turn_share_all_undetermined_is_judge_undetermined_error` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_render_per_turn_share_shows_turn_coverage` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_no_renderer_shows_per_turn_share_without_turn_coverage` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_per_turn_share_value_is_none` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_per_turn_share_stored_in_per_turn_summary` | 1 | AssertionError (class not implemented yet) | PerTurnSummary is not implemented yet |
| `test_results.py` | `test_per_turn_share_checked_against_counts` | 1 | AssertionError (class not implemented yet) | PerTurnSummary is not implemented yet |
| `test_results.py` | `test_per_turn_share_mismatch_rejected` | 1 | AssertionError (class not implemented yet) | PerTurnSummary is not implemented yet |
| `test_results.py` | `test_per_turn_coverage_checked_against_counts` | 1 | AssertionError (class not implemented yet) | PerTurnSummary is not implemented yet |
| `test_results.py` | `test_per_turn_share_absent_when_no_turn_counted` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_per_turn_share_pass_from_range_rule` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_passed_uses_definition_pass_rule_not_binding_params` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_check_result_against_definition_checks_the_reference` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_scorer_info_present_iff_non_judge_scored_or_error` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_scorer_info_matches_definition_implementation_ref` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_scorer_version_stored_verbatim` | 1 | AssertionError (class not implemented yet) | ScorerInfo is not implemented yet |
| `test_results.py` | `test_judge_info_present_only_on_scored_or_errored_judge_results` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results.py` | `test_metric_result_evidence_refs_are_evidence_ref_objects` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results_language.py` | `test_language_independent_metric_skips_language_check` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_untagged_input_is_language_not_observed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_und_and_unparseable_labels_are_language_not_observed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_turn_language_takes_precedence_over_transcript_language` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_configured_language_never_used_as_observed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_results_language.py` | `test_per_conversation_any_untagged_turn_is_language_not_observed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_per_turn_share_untagged_turns_not_counted` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_unsupported_language_na_for_observed_bn_IN` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_wer_not_applicable_for_observed_romanized` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_romanized_observed_scored_when_not_script_sensitive` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_declared_language_lifts_language_not_observed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_declared_language_never_treated_as_observed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_declared_language_not_used_for_und_or_unparseable_labels` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_observed_turn_tag_takes_precedence_over_declared_language` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_declared_language_still_gives_unsupported_language` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_declaration_does_not_suppress_unsupported_script` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_script_sensitive_metric_ignores_declared_language` | 1 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_results_language.py` | `test_result_language_source_presence_and_values` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results_language.py` | `test_declared_language_transcript_ids_iff_declared` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results_language.py` | `test_effective_language_steps_table` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results_language.py` | `test_untagged_import_worked_example_6f` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_results_language.py` | `test_untagged_import_with_declaration_worked_example_6h` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_snapshot.py` | `test_snapshot_shape` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_snapshot_pii_walk_zero` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_snapshot_jcs_digest_stable` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_snapshot_forged_digest_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_snapshot_digest_independent_of_profile_digest` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_snapshot_digest_ignores_embedded_schema_version` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_internal_snapshot_profile_digest_plain_required` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_pricing_catalog_ref_digest_verified` | 1 | AssertionError (class not implemented yet) | PricingCatalogRef is not implemented yet |
| `test_snapshot.py` | `test_snapshot_free_text_profile_value_lint_warns` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_policy_documents_free_text_lint_limitation` | 1 | Failed | Failed: SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of packages/evalschema/SCHEMA_POLICY.md, SCHEMA_POLICY.md, docs/SCHEMA_POLICY.md |
| `test_snapshot.py` | `test_snapshot_expected_outcome_is_a_required_equals_true_check` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_definition_refs_digest_optional_and_verified_when_present` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_snapshot.py` | `test_snapshot_validates_the_rubric_against_bindings` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_run.py` | `test_run_shape_and_enums` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_run.py` | `test_run_caller_origin_required` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_run.py` | `test_simulated_mode_implies_simulated_caller` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_run.py` | `test_run_started_at_optional_utc` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_run.py` | `test_external_refs_never_ids` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_run.py` | `test_run_detected_capabilities_separate_from_declared` | 1 | AssertionError (class not implemented yet) | AgentTargetRef is not implemented yet |
| `test_run.py` | `test_run_channel_maps_and_warnings_are_value_objects` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_dataset.py` | `test_dataset_item_no_codemix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_dataset.py` | `test_human_recording_item_requires_privacy_marker` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_dataset.py` | `test_dataset_item_recording_without_run_is_human_subject` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_dataset.py` | `test_dataset_item_language_is_not_a_declaration` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_dataset.py` | `test_dataset_is_a_versioned_entity` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_verdict.py` | `test_weighted_exclusions_are_unique_per_binding` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_outcome_of_result_needs_a_pass_decision` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_verdict.py` | `test_run_verdict_shape_and_enums` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_empty_verdict_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_counts_sum_to_required_total` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_status_pass_fail_inconclusive_table` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_inconsistent_status_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_counts_equal_check_tally` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_headline` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_headline_unchanged_by_weights` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weighted_score_5_3_2_with_na_is_1_coverage_0_8` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weighted_score_excludes_na_and_lists_them` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weighted_score_excludes_errored` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weighted_score_absent_when_all_excluded` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weighted_score_range_0_1` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weight_coverage_required_with_score` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weight_coverage_is_scored_over_total_weight` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_render_weighted_score_shows_coverage` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_no_renderer_shows_weighted_score_without_coverage` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_weighted_score_uses_per_turn_passed_not_share` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_checks_reference_required_bindings_only` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_verdict.py` | `test_check_outcome_matches_metric_result_status` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_verdict.py` | `test_verdict_check_metric_ref_is_typed_and_equals_binding_metric` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_verdict.py` | `test_weighted_score_checked_against_rubric_and_results` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_privacy.py` | `test_consent_enum` | 1 | AssertionError (class not implemented yet) | ConsentRecord is not implemented yet |
| `test_privacy.py` | `test_retain_until_utc` | 1 | AssertionError (class not implemented yet) | RetentionPolicy is not implemented yet |
| `test_privacy.py` | `test_privacy_marker_shape_and_reason_set` | 1 | AssertionError (class not implemented yet) | PrivacyMarker is not implemented yet |
| `test_privacy.py` | `test_consent_retention_required_iff_human_subject` | 1 | AssertionError (class not implemented yet) | PrivacyMarker is not implemented yet |
| `test_privacy.py` | `test_human_subject_derived_from_caller_origin_human` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_human_subject_derived_from_unknown_role` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_human_subject_defaults_true_when_unknown` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_human_subject_false_only_when_simulated_and_roles_known` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_mixed_channel_counts_as_caller_and_agent_evidence` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_simulated_run_with_unknown_role_turn_is_human_subject` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_human_subject_looser_marker_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_human_subject_stricter_marker_requires_reason` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_human_subject_manual_true_always_allowed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_marker_before_role_evidence_stays_valid_with_reason` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_privacy.py` | `test_privacy_marker_required_for_human_caller` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_privacy.py` | `test_privacy_marker_required_on_payload_entities` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_privacy.py` | `test_caller_external_id_requires_privacy_marker` | 1 | AssertionError (class not implemented yet) | CallerExternalId is not implemented yet |
| `test_privacy.py` | `test_user_id_not_in_snapshot_or_export` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 3 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 2 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_is_registered_with_its_prefix` | 2 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 3 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 2 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_roundtrip_and_schema_agree` | 2 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 3 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 2 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_rejects_unknown_fields` | 2 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 3 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 2 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_entity_checks_schema_version_and_id_kind` | 2 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_digested_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_digested_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.snapshot' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 3 | ModuleNotFoundError | module 'tracer_evalschema.transcript' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 2 | ModuleNotFoundError | module 'tracer_evalschema.events' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 1 | ModuleNotFoundError | module 'tracer_evalschema.results' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 1 | ModuleNotFoundError | module 'tracer_evalschema.verdict' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 1 | ModuleNotFoundError | module 'tracer_evalschema.run' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_records_of_fact_are_not_versioned` | 1 | ModuleNotFoundError | module 'tracer_evalschema.dataset' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_models_emit_enum_not_const` | 1 | ModuleNotFoundError | module 'tracer_evalschema.artifact' (not written yet) |
| `test_batch3_entities.py` | `test_batch3_models_are_all_found_by_the_registry_walk` | 1 | AssertionError | AssertionError: assert {'Artifact', ...cResult', ...} <= {'AgentTarget...umScale', ...} |

Of the 241 red tests, 206 fail with `ModuleNotFoundError`, 33 fail with `AssertionError: <Class> is not implemented yet` (the test looks the class up by name in the registry walk, so the module may exist but the class does not), and 2 fail for another reason.

### Reds that are not 'module missing' or 'class missing'

- `test_snapshot::test_policy_documents_free_text_lint_limitation` (test_snapshot.py): Failed: Failed: SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of packages/evalschema/SCHEMA_POLICY.md, SCHEMA_POLICY.md, docs/SCHEMA_POLICY.md
- `test_batch3_entities::test_batch3_models_are_all_found_by_the_registry_walk` (test_batch3_entities.py): AssertionError: AssertionError: assert {'Artifact', ...cResult', ...} <= {'AgentTarget...umScale', ...}

### Why these two reds are the right reason

- `test_policy_documents_free_text_lint_limitation` (`test_snapshot.py`): the only red of another kind; it fails with 'SCHEMA_POLICY.md does not exist yet (P1-20a)'. It reads the policy text exactly like the batch 2 `test_policy_documents_low_entropy_digest_limitation`; it turns green when P1-20a writes the file. It is the only batch 3 test that a correct implementation of the schema alone cannot turn green.

- `test_batch3_models_are_all_found_by_the_registry_walk` (`test_batch3_entities.py`): its first assertion is that the twelve entity class names are among `registry.model_classes()`; none exists yet, so the set comparison fails. Same pattern as the batch 2 test of the same kind.

## Gate-bound and changed existing tests

Run: `gate-and-changed-existing.py<ver>.log` (82 tests of `test_ids.py`, `test_versioning.py`, `test_metric_definition.py`): 4 failed, 78 passed. The four that fail are the gate-bound `test_every_entity_has_registered_prefix`, `test_value_objects_have_no_entity_fields`, `test_opaque_id_fields_are_exactly_the_adr_allowlist` and `test_versioned_entity_set_matches_adr`; they were red before this batch and stay red after it (below). Edits to existing tests (strengthening only, nothing removed): `test_ids.py` (`VALUE_OBJECT_NAMES` gains ScorerInfo, LanguageDeclaration, ChannelAssignment, ResultError, JudgeTurnVerdict, WeightedExclusion; `EXPECTED_TYPED_REFS` and `NARROWED_VERSIONED_REFS` gain AgentTargetRef) and `test_metric_definition.py::_closed_enum_table` (rows LanguageDeclaration.source, MetricResult.language_source, VerdictCheck.outcome, WeightedExclusion.outcome). `test_all_closed_enums_exact` checks a row only when its owner class exists, so it passes on arrival and starts checking the new rows when the models arrive.

### Can the batch 3 entities turn the gate-bound tests green? (checked on the oracle, `oracle-green-full-suite.py<ver>.log`)

No. With the batch 3 entities present the four fail for the reasons below, all of them belonging to later tasks:

| Test | What is left after batch 3 | Owner task |
|---|---|---|
| `test_every_entity_has_registered_prefix` | seven entities missing: `pcat` PricingCatalog, `pce` PriceEntry, `use` UsageRecord, `cst` CostRecord, `csum` CostSummary, `rbnd` RunBundle, `exp` ExportBundle | P1-23, P1-24, P1-25, P1-14 |
| `test_value_objects_have_no_entity_fields` | nine value objects missing: SealedPayload, RedactionRecord, UsageQuantity, BillingRules, PriceProvenance, PricingSnapshot, ReportedCostRaw, CostRounding, CostExclusion; every other name it checks (including the five typed references and the walks) passes | P1-14, P1-25, P1-26 |
| `test_opaque_id_fields_are_exactly_the_adr_allowlist` | the set of opaque `*_id` fields is exactly right (`KeyRef.key_id`, `ToolCallEvent.external_call_id`, `Run.caller_external_id`); only the last assertion fails: `SealedPayload.aad_fields.subject_id` must exist and be a typed ID | P1-26 |
| `test_versioned_entity_set_matches_adr` | only `PricingCatalog` missing from the nine versioned entities (Dataset now present) | P1-24 |

`test_every_id_reference_field_has_registered_prefix` is green now and stays green only if `EvidenceRef.ref_id` is typed as the union of the five ID classes (a base `TypedId` has no `.prefix`).

## Oracle (throwaway, outside the repository)

A correct implementation of the contract of api-surface section 22 was built on the real batch 1 and 2 code in `scratchpad/oracle10` (the files are not in the repository). Logs: `oracle-green-full-suite.py<ver>.log` and `oracle-green-p1-12-snapshot.py<ver>.log`, on 3.11, 3.12, 3.13 and 3.14. Result on every interpreter, full tree (836 tests): **830 passed, 5 failed, 1 deselected**. The 5 failures are the four gate-bound tests above and `test_policy_documents_low_entropy_digest_limitation` (P1-20a); the deselected test is `test_policy_documents_free_text_lint_limitation` (P1-20a). All 240 other batch 3 tests pass, and all 590 older tests keep passing. The oracle's own mistakes found while writing the tests (located errors for cross-field rules, a union serializer for `TimelineOrigin.ref`, `EvidenceRef.ref_id` as a union, keyed digests comparable in `check_pricing_catalog_ref`) are recorded in api-surface section 22.

## Mutants

470 mutants of the oracle (one change each) were run against the 241 batch 3 tests; 442 are caught, 28 are not. `mutants.txt` lists all of them with the first failing test, and for each survivor the reason: it is equivalent given another rule of the oracle, or it changes a reading that the tests deliberately do not pin (listed as an ambiguity in api-surface section 22.3).

