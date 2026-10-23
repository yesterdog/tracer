# Red-first evidence, batch 2 revision 8 (P1-06..P1-09 and Rubric, qa)

Produced after the revision 8/9 test update (escalations E5-E7, P-59, P-60 and N15 accepted by the owner). Each `<group>.py<ver>.log` here holds the full pytest output with header (date, command, interpreter, versions, uv.lock sha256, git status). No commits, nothing staged. Interpreters: Python 3.11.16, 3.12.14, 3.13.15 (`.venv`), 3.14.7; pydantic 2.14.1, pytest 9.1.1, jsonschema 4.26.0. The earlier evidence in `../batch2/` is untouched.

Outcome per test is identical on 3.11, 3.12, 3.13 and 3.14 (compared test by test from the junit XML).

## Counts
| Group (test file) | Tests | Failed (red) | Passed on arrival |
|---|---|---|---|
| p1-06-project-agent | 30 | 30 | 0 |
| p1-06-09-batch2-entities | 46 | 46 | 0 |
| p1-07-persona-profile | 19 | 19 | 0 |
| p1-08-metric-definition | 44 | 44 | 0 |
| p1-08-rubric | 15 | 15 | 0 |
| p1-09-evaluator | 18 | 18 | 0 |
| gate-ids | 28 | 3 | 25 |

Full tree: 590 tests collected, no collection errors, no skips, no xfails, no empty parametrization. Full suite on each interpreter: 176 failed and 414 passed. The 414 are the batch 1 tests, unchanged. The 176 failed are all 172 tests of the six batch 2 files, the 3 gate-bound `test_ids.py` tests (`test_every_entity_has_registered_prefix`, `test_value_objects_have_no_entity_fields`, `test_opaque_id_fields_are_exactly_the_adr_allowlist`) and `test_versioned_entity_set_matches_adr` (in `test_versioning.py`, so counted in the full suite only). `test_policy_documents_low_entropy_digest_limitation` stays red until P1-20a.

Failure types: `ModuleNotFoundError` means the module the test needs is not written yet (the intended reason, 156 tests); `AssertionError` or `AttributeError` with "is not implemented yet" or a missing attribute means the same for a class or constant found by name; the few others are listed with their reason. No test fails because of its own code (no NameError, TypeError or import error in a test file).

## Why each red test is red
| Test file | Test (parameters collapsed) | Count | Failure type | One-line reason |
|---|---|---|---|---|
| `test_project_agent.py` | `test_secret_field_walk` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_project_agent.py` | `test_secret_walk_does_not_flag_usage_units` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_project_agent.py` | `test_provider_ref_shape_and_open_values` | 1 | ModuleNotFoundError | module 'tracer_evalschema.provider' (not written yet) |
| `test_project_agent.py` | `test_provider_ref_extra_api_key_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.provider' (not written yet) |
| `test_project_agent.py` | `test_rejected_credentials_are_not_echoed_in_error_text` | 1 | ModuleNotFoundError | module 'tracer_evalschema.provider' (not written yet) |
| `test_project_agent.py` | `test_config_ref_shape` | 1 | ModuleNotFoundError | module 'tracer_evalschema.provider' (not written yet) |
| `test_project_agent.py` | `test_project_is_a_record_of_fact_entity` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_project_agent.py` | `test_metadata_secret_value_preserved_but_flagged` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_project_agent.py` | `test_endpoint_rejects_credentials_in_url` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_endpoint_url_credential_params_case_and_encoding` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_sip_endpoint_userinfo_rule` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_endpoint_transport_scheme_rules` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_endpoint_discriminated_by_transport` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_agent_target_is_a_versioned_entity` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_agent_target_languages_supported_only` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_declared_vs_detected_capabilities_separate` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_declared_stack_entry_shape` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_project_provider_configs_unique_per_provider_and_model` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_project_agent.py` | `test_declared_capabilities_unique` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_agent_target_languages_empty_means_undeclared_and_unique` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_declared_stack_stage_is_exact` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_hide_input_set_closed_under_embedding` | 1 | AssertionError | ProviderConfigRef is not implemented yet |
| `test_project_agent.py` | `test_credential_errors_have_no_input_in_any_logged_form` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_endpoint_rejects_wider_credential_names_and_prefixes` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_endpoint_parameter_names_percent_decoded_and_casefolded` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_endpoint_url_syntax_rules` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_sip_uri_credential_rules` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_agent_target_has_no_tier_adapter_or_description_field` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_url_credential_parameter_names_exact` | 1 | AttributeError | module 'tracer_evalschema.credentials' has no attribute 'URL_CREDENTIAL_PARAMETER_NAMES' |
| `test_project_agent.py` | `test_url_credential_parameter_names_hyphen_underscore_equivalent` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 2 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.rubric' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_is_registered_with_its_prefix` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 2 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.rubric' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_roundtrip_and_schema_agree` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 2 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.rubric' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_rejects_unknown_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 2 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.rubric' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_entity_checks_schema_version_and_id_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_versioned_entity_digest_is_verified` | 2 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.rubric' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_versioned_entity_digest_is_verified` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_batch2_entities.py` | `test_project_is_not_versioned` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_models_emit_enum_not_const` | 1 | ModuleNotFoundError | module 'tracer_evalschema.project' (not written yet) |
| `test_batch2_entities.py` | `test_batch2_models_are_all_found_by_the_registry_walk` | 1 | AssertionError | assert {'AgentTarget...Binding', ...} <= {'BidiFlagged...Warning', ...} |
| `test_batch2_entities.py` | `test_typed_reference_narrows_the_id_kind` | 1 | AssertionError | PersonaRef is not implemented yet |
| `test_batch2_entities.py` | `test_typed_reference_narrows_the_id_kind` | 1 | AssertionError | MetricDefinitionRef is not implemented yet |
| `test_batch2_entities.py` | `test_typed_reference_narrows_the_id_kind` | 1 | AssertionError | MetricBindingRef is not implemented yet |
| `test_batch2_entities.py` | `test_reference_digest_is_optional_and_verified_when_present` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_persona_profile.py` | `test_persona_has_no_codemix_field` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_persona_profile.py` | `test_persona_language_supported_only` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_persona_profile.py` | `test_persona_accent_is_a_human_label` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_persona_profile.py` | `test_test_profile_pii_marked` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_test_profile_has_no_secret_like_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_test_profile_ref_has_no_pii` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_test_profile_ref_shape_and_digest` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_snapshot_embedded_entity_schemas_carry_no_pii_marker` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_persona_profile.py` | `test_policy_documents_low_entropy_digest_limitation` | 1 | Failed | SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of packages/evalschema/SCHEMA_POLICY.md, SCHEMA_ |
| `test_persona_profile.py` | `test_profile_placeholder_fields_are_the_profile_data_fields` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_literal_profile_value_in_free_text_is_flagged_with_path_not_value` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_profile_value_lint_is_total_on_other_models` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_every_profile_field_is_usable_as_a_placeholder` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_profile_lint_folding_and_token_bounds` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_profile_lint_folds_unicode_forms` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_profile_lint_paths_order_and_privacy` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_profile_lint_covers_expected_outcome_and_tool_arguments` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_test_profile_errors_do_not_echo_values` | 1 | ModuleNotFoundError | module 'tracer_evalschema.profile' (not written yet) |
| `test_persona_profile.py` | `test_persona_has_only_language_and_accent` | 1 | ModuleNotFoundError | module 'tracer_evalschema.persona' (not written yet) |
| `test_metric_definition.py` | `test_input_kind_members_exact` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_all_closed_enums_exact` | 1 | AssertionError | MetricDefinition is not implemented yet |
| `test_metric_definition.py` | `test_open_value_fields_accept_unseen_provider` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_requires_nonempty_unique` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_pass_rule_range_inclusive_and_exclusive` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_latency_lower_is_better_expressed_with_range_max` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_score_higher_is_better_expressed_with_range_min` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_pass_rule_without_direction_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_range_requires_at_least_one_bound` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_equals_true_rule_boolean` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_one_of_rule_options_subset` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_pass_rule_kind_matches_value_type` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_script_sensitive_required_no_default` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_numeric_units_exclude_currency` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_key_is_a_metric_key` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_scale_matches_value_type` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_llm_judge_requires_prompt_template_and_judge_model_ref` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_deterministic_kinds_require_implementation_ref` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_prompt_template_placeholders_declared_equals_used` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_languages_supported_only` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_aggregation_discriminated_by_kind` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_share_bounds_0_1` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_share_definition_requires_turn_rule` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_share_pass_rule_must_be_range` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_counts_fields_exact` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_share_excludes_na_turns_from_denominator` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_share_not_applicable_when_no_turn_applies` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_per_turn_status_no_counted_turn_cases` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_binding_shape_and_params` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_ref_text_form_is_display_only` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_judge_model_ref_is_a_model_ref_without_config_ref` | 1 | AssertionError | ModelRef is not implemented yet |
| `test_metric_definition.py` | `test_judge_only_fields_forbidden_on_other_kinds` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_implementation_ref_is_namespaced` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_prompt_template_rejects_other_double_braces` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_expected_outcome_variable_definition_shapes` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_conditional_field_rules_are_in_the_json_schema` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_parameter_shape_and_defaults` | 1 | AssertionError | MetricParameter is not implemented yet |
| `test_metric_definition.py` | `test_binding_params_must_be_declared_and_typed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_binding_cannot_change_the_pass_rule` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_binding_params_keys_are_metric_keys` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_scale_and_unit_domains` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_range_bounds_within_value_domain` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_languages_nonempty_distinct_primary` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_metric_languages_accepts_language_independent_value` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_requires_one_required_metric` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_required_metric_needs_pass_rule` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_required_boolean_check_with_equals_true_ok` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_weight_requires_pass_rule` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_weight_values` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_rejects_duplicate_binding_refs` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_unresolved_references_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_per_turn_share_metric_can_be_required_and_weighted` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_is_versioned_entity` | 1 | ModuleNotFoundError | module 'tracer_evalschema.rubric' (not written yet) |
| `test_rubric.py` | `test_rubric_item_shape` | 1 | AssertionError | RubricItem is not implemented yet |
| `test_rubric.py` | `test_rubric_rejects_disabled_binding` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_bound_definition_keys_unique_per_evaluator` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_validate_rubric_rejects_bindings_of_more_than_one_evaluator` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_validate_rubric_checks_binding_params_against_the_definition` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_rubric.py` | `test_rubric_unweighted_items_and_no_required_total` | 1 | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_evaluator.py` | `test_evaluator_max_duration_bounds` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_scripted_turn_index_contiguous` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_scripted_turn_language_supported` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_scripted_turn_placeholder_stored_unresolved` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_profile_placeholder_unknown_field_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_expected_tool_call_match_enum` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_bindings_unique` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_evaluator_expected_outcome_required` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_evaluator_references_are_versioned_and_typed` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_evaluator_text_fields_are_verbatim` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_evaluator_max_duration_schema_is_exact` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_evaluator_requires_at_least_one_binding` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_expected_tool_call_arguments_rules` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_expected_tool_call_order_ranks_contiguous` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_malformed_profile_placeholder_rejected` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_profile_placeholder_requires_test_profile` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_profile_placeholders_in_outcome_and_tool_arguments` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_evaluator.py` | `test_evaluator_references_reject_text_forms` | 1 | ModuleNotFoundError | module 'tracer_evalschema.evaluator' (not written yet) |
| `test_ids.py` | `test_every_entity_has_registered_prefix` | 1 | AssertionError | every registered prefix needs exactly one EntityBase subclass and vice versa; missing ['agt', 'art', 'cst', 'c |
| `test_ids.py` | `test_value_objects_have_no_entity_fields` | 1 | AssertionError | value objects not implemented yet: ['PrivacyMarker', 'ConsentRecord', 'RetentionPolicy', 'SealedPayload', 'Red |
| `test_ids.py` | `test_opaque_id_fields_are_exactly_the_adr_allowlist` | 1 | AssertionError | opaque *_id fields: unexpected [], missing [('Run', 'caller_external_id'), ('ToolCallEvent', 'external_call_id |

## Validation against a throwaway oracle (outside the repo)
A revision 8 oracle (typed references, `ModelRef`, parameters, domains, kind conditions, URL rules with the 25-name set and `-`/`_` folding, placeholder grammar, the folding lint, `hide_input_in_errors`, rubric validation) passes every new and changed test: the full tree gives 585 passed and 5 failed (the 4 gate-bound tests, whose later entities the oracle does not have, and the SCHEMA_POLICY.md test). A second copy with stubs for the later value objects passes `test_value_objects_have_no_entity_fields`. The batch 1 walk `test_ms_field_names_are_integers_schema` passes with `Evaluator.max_duration_ms` present.

Mutation check: 259 injected mutants (`mutants.txt`), all caught. Two candidate mutants were dropped as equivalent (documented at the end of `mutants.txt`). Mutants found 5 weak spots in the first version of the tests, all fixed: stray double braces in a prompt template were masked by the declared-equals-used rule; a rating scale with min equal to max was masked by the new domain rule; the same-definition-id check was masked by the same-key check; the second NFC of the lint fold was untested; a string `test_profile` was not rejected.

## Not verified
CI on GitHub and Linux were not run. Python 3.15 was not run. The oracle is my own reading of the contract, not the architect's implementation: a test that the oracle passes can still pin a reading the architect rejects (listed in api-surface 19.7). The JSON Schema `if`/`then` tests depend on pydantic constructs that were only checked with this oracle.

## Revision 9 group A additions (2026-10-23, qa; appended, nothing above was changed)
Five new tests (the four named by the architect, plus one proposed pair member). Logs are new files: `p1-06-project-agent-rev9a.py<ver>.log`, `p1-08-metric-definition-rev9a.py<ver>.log`, `full-suite-rev9a.py<ver>.log` for 3.11, 3.12, 3.13 and 3.14 (same header format as above; the mutant list is `mutants-group-a-rev9.txt`).

| Group (test file) | Tests | Failed (red) | Passed on arrival |
|---|---|---|---|
| p1-06-project-agent (`test_project_agent.py`) | 33 | 33 | 0 |
| p1-08-metric-definition (`test_metric_definition.py`) | 46 | 46 | 0 |
| Full tree | 595 collected | 181 failed | 414 passed (the batch 1 tests, unchanged) |

The 181 failures are the 176 of the table above plus the 5 new tests. Outcome test by test is identical on all four interpreters (compared from the failed-test lists). No skip, no xfail, no collection error.

| Test file | Test | Failure type | Reason |
|---|---|---|---|
| `test_project_agent.py` | `test_url_credential_pattern_anywhere_rejected` (P1-06) | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_url_high_entropy_segment_accepted` (P1-06) | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_project_agent.py` | `test_url_pattern_fitting_slug_is_rejected_known_false_positive` (P1-06, proposed name) | ModuleNotFoundError | module 'tracer_evalschema.agent' (not written yet) |
| `test_metric_definition.py` | `test_metric_languages_list_or_language_independent` (P1-08) | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |
| `test_metric_definition.py` | `test_language_independent_metric_not_script_sensitive` (P1-08) | ModuleNotFoundError | module 'tracer_evalschema.metrics' (not written yet) |

Oracle: a copy of the revision 8 oracle (`oracle9`, outside the repo) gains the pattern rule with part names, whole-URL strict decoding and the `language_independent` / `script_sensitive` rule. It passes all 5 new tests and the rest of the tree: 590 passed, 5 failed (the same 5 as before: 3 gate-bound `test_ids.py` tests, `test_versioned_entity_set_matches_adr`, and the SCHEMA_POLICY.md test).

Mutation check against `oracle9`: 87 new mutants, all caught. Of the 259 earlier mutants, 257 are caught and 2 (A14, A15) do not apply any more because their code line was replaced; U01-U08 and U26 are their successors. No mutant survives. Four of the new mutants (U25, U27, U28, U29: a scan that is skipped when the URL has a port, parameters, headers or a query) survived the first version of the new test; the placements "path then query", "SIP user part with port, parameters and headers", "SIP host with port and parameters" and "SIP parameter before a header" were added, and all four are now caught. The 339 results of the main run were taken before that addition, which only adds assertions; U25-U29 and the language mutants (L01-L15) were re-run after the final edit.

Not verified: the exact message words (the tests accept `pattern` or `credential`, plus one word per URL part); the percent-decoding of the host (not tested); the oracle is my reading of 6b, not the architect's code; revision 10 (25 names in ADR text) was not read, the tests use the 25-name set of N15 as before.
