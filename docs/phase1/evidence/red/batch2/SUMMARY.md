# Red-first evidence, batch 2 (tasks P1-06..P1-09 tests, plus Rubric)

Generated from the junit XML of the per-group runs saved next to this file (`<group>.py<ver>.log` holds the full pytest output with header: date, command, versions, uv.lock sha256, git status). No commits, nothing staged. Interpreters: Python 3.11.16, 3.12.14, 3.13.15 (`.venv`), 3.14.7; pydantic 2.14.1, pytest 9.1.1, jsonschema 4.26.0.

Failure type vocabulary: `ModuleNotFoundError` / `AttributeError` = the implementation the test needs does not exist yet (the intended reason). `AssertionError` / `Failed` = a contract that is genuinely unmet today (listed with its reason).

## Counts per group (identical on 3.11, 3.12, 3.13, 3.14)

| Group | Test file | Tests | Failed (red) | Passed on arrival |
|---|---|---|---|---|
| p1-06-project-agent | `test_project_agent.py` | 17 | 17 | 0 |
| p1-06-09-batch2-entities | `test_batch2_entities.py` | 42 | 42 | 0 |
| p1-07-persona-profile | `test_persona_profile.py` | 13 | 13 | 0 |
| p1-08-metric-definition | `test_metric_definition.py` | 29 | 29 | 0 |
| p1-08-rubric | `test_rubric.py` | 9 | 9 | 0 |
| p1-09-evaluator | `test_evaluator.py` | 10 | 10 | 0 |
| **Total** | | **120** | **120** | **0** |

Collection: `pytest --collect-only` over the whole test tree reports 538 tests, no errors, no skips, no xfails, no empty parametrization. Full suite on each interpreter: 124 failed (the 120 new tests plus the 4 batch 1 gate-bound tests) and 414 passed (the batch 1 tests, unchanged).

## Why each red test is red

| Group | Test (parameters collapsed) | Count | Failure type | One-line reason |
|---|---|---|---|---|
| p1-06-project-agent | `test_secret_field_walk` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-project-agent | `test_secret_walk_does_not_flag_usage_units` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-project-agent | `test_provider_ref_shape_and_open_values` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.provider' (module not written yet) |
| p1-06-project-agent | `test_provider_ref_extra_api_key_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.provider' (module not written yet) |
| p1-06-project-agent | `test_rejected_credentials_are_not_echoed_in_error_text` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.provider' (module not written yet) |
| p1-06-project-agent | `test_config_ref_shape` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.provider' (module not written yet) |
| p1-06-project-agent | `test_project_is_a_record_of_fact_entity` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-project-agent | `test_metadata_secret_value_preserved_but_flagged` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-project-agent | `test_endpoint_rejects_credentials_in_url` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_endpoint_url_credential_params_case_and_encoding` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_sip_endpoint_userinfo_rule` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_endpoint_transport_scheme_rules` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_endpoint_discriminated_by_transport` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_agent_target_is_a_versioned_entity` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_agent_target_languages_supported_only` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_declared_vs_detected_capabilities_separate` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-project-agent | `test_declared_stack_entry_shape` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.rubric' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.rubric' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.rubric' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.rubric' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.agent' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_versioned_entity_digest_is_verified` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.rubric' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-06-09-batch2-entities | `test_project_is_not_versioned` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_models_emit_enum_not_const` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.project' (module not written yet) |
| p1-06-09-batch2-entities | `test_batch2_models_are_all_found_by_the_registry_walk` | 1 | AssertionError | AssertionError: assert {'AgentTarget...Binding', ...} <= {'BidiFlagged...Warning', ...}      Extra items in the left set:   'TestProfile'   'MetricDef |
| p1-07-persona-profile | `test_persona_has_no_codemix_field` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-07-persona-profile | `test_persona_language_supported_only` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-07-persona-profile | `test_persona_accent_is_a_human_label` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.persona' (module not written yet) |
| p1-07-persona-profile | `test_test_profile_pii_marked` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_test_profile_has_no_secret_like_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_test_profile_ref_has_no_pii` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_test_profile_ref_shape_and_digest` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_snapshot_embedded_entity_schemas_carry_no_pii_marker` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-07-persona-profile | `test_policy_documents_low_entropy_digest_limitation` | 1 | Failed | Failed: SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of packages/evalschema/SCHEMA_POLICY.md, SCHEMA_POLICY.md, docs/SCHEMA_POLICY.md |
| p1-07-persona-profile | `test_profile_placeholder_fields_are_the_profile_data_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_literal_profile_value_in_free_text_is_flagged_with_path_not_value` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_profile_value_lint_is_total_on_other_models` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-07-persona-profile | `test_every_profile_field_is_usable_as_a_placeholder` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.profile' (module not written yet) |
| p1-08-metric-definition | `test_input_kind_members_exact` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_all_closed_enums_exact` | 1 | AssertionError | MetricDefinition is not implemented yet (class missing from the registry) |
| p1-08-metric-definition | `test_open_value_fields_accept_unseen_provider` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_requires_nonempty_unique` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_pass_rule_range_inclusive_and_exclusive` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_latency_lower_is_better_expressed_with_range_max` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_score_higher_is_better_expressed_with_range_min` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_pass_rule_without_direction_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_range_requires_at_least_one_bound` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_equals_true_rule_boolean` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_one_of_rule_options_subset` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_pass_rule_kind_matches_value_type` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_script_sensitive_required_no_default` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_numeric_units_exclude_currency` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_metric_key_is_a_metric_key` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_scale_matches_value_type` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_llm_judge_requires_prompt_template_and_judge_model_ref` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_deterministic_kinds_require_implementation_ref` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_prompt_template_placeholders_declared_equals_used` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_metric_languages_supported_only` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_aggregation_discriminated_by_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_share_bounds_0_1` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_share_definition_requires_turn_rule` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_share_pass_rule_must_be_range` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_counts_fields_exact` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_share_excludes_na_turns_from_denominator` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_share_not_applicable_when_no_turn_applies` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_per_turn_status_no_counted_turn_cases` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-metric-definition | `test_metric_binding_shape_and_params` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_requires_one_required_metric` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_required_metric_needs_pass_rule` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_required_boolean_check_with_equals_true_ok` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_weight_requires_pass_rule` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_weight_values` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_rejects_duplicate_binding_refs` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_unresolved_references_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_per_turn_share_metric_can_be_required_and_weighted` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.metrics' (module not written yet) |
| p1-08-rubric | `test_rubric_is_versioned_entity` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.rubric' (module not written yet) |
| p1-09-evaluator | `test_evaluator_max_duration_bounds` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_scripted_turn_index_contiguous` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_scripted_turn_language_supported` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_scripted_turn_placeholder_stored_unresolved` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_profile_placeholder_unknown_field_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_expected_tool_call_match_enum` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_bindings_unique` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_evaluator_expected_outcome_required` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_evaluator_references_are_versioned_and_typed` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |
| p1-09-evaluator | `test_evaluator_text_fields_are_verbatim` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.evaluator' (module not written yet) |

## Reading the three non-ModuleNotFoundError reds

- `test_all_closed_enums_exact` and `test_batch2_models_are_all_found_by_the_registry_walk` assert that the batch 2 classes exist in the registry; they fail with an AssertionError that names the missing class (the class is not written yet).
- `test_policy_documents_low_entropy_digest_limitation` fails because `SCHEMA_POLICY.md` does not exist; it is gate-bound to P1-20a and cannot be green after the batch 2 implementation.

## Gate-bound tests after batch 2

Run against the throwaway oracle (all eight batch 2 entities implemented, outside the repo): the full suite gives 5 failed, 533 passed. The 5 are the 4 batch 1 gate-bound tests (`test_every_entity_has_registered_prefix`, `test_value_objects_have_no_entity_fields`, `test_opaque_id_fields_are_exactly_the_adr_allowlist`, `test_versioned_entity_set_matches_adr`; they still need the other 19 entities and the remaining value objects) and `test_policy_documents_low_entropy_digest_limitation` (needs P1-20a). No batch 1 test changed.

## Oracle and mutation check (outside the repository)

The tests were validated against a throwaway oracle implementation kept in the session scratchpad (not in the repo, not a proposal for `src/`). All 120 new tests pass on it except the SCHEMA_POLICY.md test. Then 73 single-change mutants were injected into the oracle; each is caught by at least one new test (73 caught, 0 survived):

- M01 config_ref accepts pasted keys
- M02 config_ref shape unchecked (any text)
- M03 config_ref schema pattern dropped
- M04 ProviderConfigRef ignores extra fields
- M05 errors echo the rejected input
- M06 Project errors echo input (nested key)
- M07 endpoint userinfo allowed
- M08 credential params case-sensitive
- M09 credential params compared undecoded
- M10 sip password allowed
- M11 ws scheme unchecked
- M12 any @ in url rejected (naive)
- M13 substring credential params
- M14 AgentTarget gains detected_capabilities
- M15 transport tag is a const
- M16 sip user part rejected
- M17 declared stack stage open string
- M18 profile dob not x-pii
- M19 unknown placeholder accepted
- M20 lint reports value in path
- M21 TestProfileRef ignores extra
- M22 TestProfileRef gains name
- M23 placeholder fields include id
- M24 lint ignores nested lists
- M25 range ignores exclusivity
- M26 float bound exact decimal
- M27 range accepts no bound
- M28 range min above max accepted
- M29 equals_true coerces truthy
- M30 pass rule kind fit unchecked
- M31 per-turn share counts n/a in denominator
- M32 share rounds half up
- M33 script_sensitive defaults false
- M34 usd is a unit
- M35 requires duplicates allowed
- M36 prompt variables unchecked
- M37 share bounds unchecked
- M38 share pass rule may be any kind
- M39 turn_rule optional
- M40 turn_rule fit unchecked
- M41 unit allowed on non-numeric
- M42 scale unchecked vs value_type
- M43 judge needs no model ref
- M44 deterministic needs no implementation_ref
- M45 one_of options not subset-checked
- M46 one_of duplicates allowed
- M47 PerTurnCounts accepts negative
- M48 binding params not JSON
- M49 binding accepts any ref kind
- M50 uniqueness ignores evaluator
- M51 uniqueness by (id,version)
- M52 requires has no schema minItems
- M53 rubric: required needs no rule
- M54 rubric: weight needs no rule
- M55 rubric: negative weight ok
- M56 rubric: duplicates ok
- M57 rubric: no required item ok
- M58 rubric: unresolved binding skipped
- M59 evaluator: no upper bound
- M60 evaluator: zero duration ok
- M61 evaluator: scripted index unchecked
- M62 evaluator: duplicate binding ok
- M63 evaluator: persona kind unchecked
- M64 instructions placeholders unchecked
- M65 scripted turn language free text
- M66 tool match is open string
- M67 expected_outcome optional
- M68 persona language free text
- M69 accent unrestricted
- M70 project name unrestricted
- M71 aggregation accepts a bare string
- M72 languages unrestricted
- M73 metric key any text
