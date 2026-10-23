# Red-first evidence, batch 1 (tasks P1-01..P1-05 tests)

**Update after fixes:** the two repo/data reds are now green. `test_ci_required_test_jobs_sync_bidi_ref_group` passes (test and py3.15 CI jobs now run `uv sync --locked --all-groups` and `uv run --locked --all-groups pytest`). `test_vendored_data_has_verification_note` passes (`supported_currencies.json` `_meta` has sources, retrieved_at, verification). Also added the `local_only` pytest marker (deselected by default) and rewrote the stale `is_x_code` description in `iso4217_active.json`. `p1-01-02-repo-contract.log` and `p1-03-data-files.log` are re-run logs (13 and 16 passed); the other six logs and the red-reason tables below are unchanged from the original run. The two rows for those tests in the tables below are historical.

Generated from the junit XML of the per-group runs saved next to this file (`<group>.log` holds the full pytest output with header). No commits, nothing staged.

Command per group: `.venv/bin/python -m pytest -p no:cacheprovider -v <files>` (Python 3.13.15, python-bidi 0.6.12 installed). The same 194 tests failed identically (before the batch-1 fixes below) on Python 3.11.16 and 3.14.7 (venvs synced with `uv sync --locked --all-groups`).

## Counts per group

| Group | Log | Failed (red) | Passed on arrival |
|---|---|---|---|
| p1-01-02-repo-contract | `p1-01-02-repo-contract.log` | 0 (was 1; re-run after fix: 13 passed) | 13 |
| p1-03-data-files | `p1-03-data-files.log` | 0 (was 1; re-run after fix: 16 passed) | 16 |
| p1-04a-ids | `p1-04a-ids.log` | 20 | 0 |
| p1-04b-primitives | `p1-04b-primitives.log` | 45 | 0 |
| p1-04c-language | `p1-04c-language.log` | 57 | 0 |
| p1-04d-step1-bidi-conformance | `p1-04d-step1-bidi-conformance.log` | 1 | 6 |
| p1-04d-step2-text-and-display-safe | `p1-04d-step2-text-and-display-safe.log` | 33 | 0 |
| p1-05-base-versioning-digest-loader | `p1-05-base-versioning-digest-loader.log` | 36 | 0 |
| **Total** | | **192** (was 194; two repo/data reds fixed) | **35** |

Plus `test_scaffold.py::test_package_importable` (pre-existing placeholder, passes). Collection: `pytest --collect-only` reports 228 tests, no errors, no skips, no xfails, no empty parametrization.

## Why each red test is red

Failure type vocabulary: `ModuleNotFoundError` / `AttributeError` = the implementation the test needs does not exist yet (the intended reason for code tests). `AssertionError` = a repository or data contract that is genuinely unmet today (listed separately below, with the reason).

| Group | Test (parameters collapsed) | Count | Failure type | One-line reason |
|---|---|---|---|---|
| p1-01-02-repo-contract | `test_ci_required_test_jobs_sync_bidi_ref_group` | 1 | AssertionError | no step of the required test job syncs the bidi-ref group (uv sync --locked --group bidi-ref, or --all-groups); commands: uv sync --locked / uv run -- |
| p1-03-data-files | `test_vendored_data_has_verification_note` | 1 | AssertionError | supported_currencies.json: _meta.verification missing or too short; supported_currencies.json: _meta.retrieved_at missing or not YYYY-MM-DD; supported |
| p1-04a-ids | `test_id_roundtrip_prefixed` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_rejects_wrong_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_rejects_uppercase` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_rejects_uuid_v4_with_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_rejects_bare_uuid_on_wire` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_rejects_trailing_newline_and_whitespace` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_is_not_a_str_subclass` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_id_uuid_property_is_uuid_UUID` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_from_external_mints_v7_and_records_external_ref` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_uuid7_monotonic_10k_same_ms` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_uuid7_rfc9562_layout` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_uuid7_layout_matches_stdlib_when_available` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_prefix_registry_unique` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_prefix_registry_frozen_matches_adr` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_new_prefixes_not_confusable` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_frozen_registry_confusable_pairs_are_the_known_five` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_jsonschema_pattern_rejects_wrong_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-04a-ids | `test_every_id_reference_field_has_registered_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.registry' (module not written yet) |
| p1-04a-ids | `test_every_entity_has_registered_prefix` (gate-bound, see note) | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.registry' (module not written yet) |
| p1-04a-ids | `test_value_objects_have_no_entity_fields` (gate-bound, see note) | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.registry' (module not written yet) |
| p1-04b-primitives | `test_utc_rejects_naive` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_utc_normalizes_offset_to_Z` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_utc_serialization_canonical_six_digits` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_utc_rejects_leap_second` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_ms_fields_reject_float_and_negative` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_ms_rejects_bool_and_numeric_strings` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_ms_field_names_are_integers_schema` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_rejects_python_float` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_rejects_json_number` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_rejects` | 6 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_rejects_other_malformed_strings` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_scale_limit_is_18` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_canonical` | 6 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_decimal_json_schema_is_string_with_canonical_pattern` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_money_rejects_non_usd` | 10 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_money_rejects_every_non_usd_iso_code` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_money_currency_required_and_strictly_typed` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_supported_currency_set_is_data_driven` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_load_supported_currencies_rejects_code_missing_from_iso4217` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_non_usd_error_names_iso_currency` | 3 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_money_roundtrip_small` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_money_may_be_negative_but_stays_canonical` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_money_add_exact` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04b-primitives | `test_display_rounding_does_not_mutate_amount` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| p1-04c-language | `test_supported_accepts` | 6 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_supported_accepts_every_configured_language_code` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_canonical_case` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_supported_rejects` | 5 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_supported_script_allowed_only_if_native` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_supported_region_outside_vendored_set_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_accepts_regions_outside_vendored_set` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_hi_en_rejected_with_codemix_message` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_hi_en_is_region_EN` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_configured_rejects_hi_Latn` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_accepts_hi_Latn` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_rejects` | 6 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_accepts_special_and_extension_tags` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_accepts_bn_IN` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_accepts_well_formed_unregistered` | 3 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_accepts_private_use_only_and_extlang_forms` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_validation_does_not_read_registry` | 1 | AssertionError | child interpreter failed: ImportError: cannot import name 'language' from 'tracer_evalschema' (~/tracer/packages/evalschema/src/trace |
| p1-04c-language | `test_observed_rejects_malformed` | 5 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_rejects_other_malformed_forms` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_primary_subtag_2_3_letters` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_rejects_grandfathered` | 4 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_observed_rejects_all_26_grandfathered_tags` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_importer_language_name_raw_kept_tag_empty` | 5 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_detected_language_raw_verbatim` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_detected_language_raw_kept_when_tag_unparseable` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_importer_underscore_to_hyphen_with_warning_raw_kept` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_normalize_language_label_is_pure_and_total` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_primary_language_equivalence` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04c-language | `test_supported_no_suppress_script_rewrite` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| p1-04d-step1-bidi-conformance | `test_bidi_reference_passes_biditest` | 1 | AssertionError | BidiTest.txt: 489439/490846 data lines pass (1407 fail, 1435/770241 paragraph-level variants fail, 0 unparsed); isolate lines 273522/274412 pass; embe |
| p1-04d-step2-text-and-display-safe | `test_verbatim_nfc_nfd_distinct` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_verbatim_preserves_zwj_zwnj` | 5 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_verbatim_text_is_stored_unchanged` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_rejects_lone_surrogate` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_bidi_flag_computed` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_bidi_flag_forgery_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_human_label_rejects` | 9 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_human_label_rejects_c1` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_human_label_rejects_default_ignorables` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_human_label_accepts_zwj_zwnj` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_machine_key_ascii_only` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_machine_key_charset_allows_model_names` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_metric_key_lowercase_snake_case` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_account_number` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_pdi_breakout` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_marks_isolates` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_lrm_rlm_unchanged` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_does_not_mutate_input` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_wraps_plain_text_in_fsi_pdi` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_display_safe_markers_ascii_and_inert` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-04d-step2-text-and-display-safe | `test_marker_table_json_matches_function` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_entity_base_required_fields` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_extra_field_forbidden` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_entity_id_must_match_class_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_entity_created_at_is_utc_datetime` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_schema_version_too_new` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_schema_version_mismatch_older` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_schema_version_invalid` | 2 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_schema_version_invalid_other_malformed` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_version_errors_carry_found_and_expected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_package_version_equals_schema_version` | 1 | AttributeError | module 'tracer_evalschema' has no attribute 'SCHEMA_VERSION' |
| p1-05-base-versioning-digest-loader | `test_metadata_flags_verified_on_load` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_version_int_ge_1` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_versioned_content_digest_mismatch_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_ref_requires_version` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_versioned_entity_set_matches_adr` (gate-bound, see note) | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.registry' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_bundle_mixed_versions_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_bundle_rejects_same_id_version_different_digest` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_bundle_rejects_same_record_id_different_content` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_ref_digest_must_match_resolved_entity_in_bundle` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_content_digest_scheme_registered` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_unknown_digest_scheme_rejected` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_digest_plain_sha256_jcs_golden` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_hmac_digest_golden` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_digest_key_ref_must_be_absent_for_v1` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_content_digest_has_no_salt_ref_field` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_hmac_digest_requires_key_ref` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_keyed_digest_unverifiable_without_key_raises` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_digest_verification_single_entry_point` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_digest_domain_excludes_schema_version_and_nulls` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_digest_domain_of_a_versioned_entity` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_digest_rejects_non_ijson_values` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_strict_loader_duplicate_keys` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.loader' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_strict_loader_models_reject_duplicate_keys` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.loader' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_strict_loader_depth_10k_no_recursionerror` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.loader' (module not written yet) |
| p1-05-base-versioning-digest-loader | `test_strict_loader_malformed_json_is_a_plain_error` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.loader' (module not written yet) |

### Reds that are not 'module missing'

- `test_ci_required_test_jobs_sync_bidi_ref_group` (p1-01-02-repo-contract): AssertionError: no step of the required test job syncs the bidi-ref group (uv sync --locked --group bidi-ref, or --all-groups); commands: uv sync --locked / uv run --
- `test_vendored_data_has_verification_note` (p1-03-data-files): AssertionError: supported_currencies.json: _meta.verification missing or too short; supported_currencies.json: _meta.retrieved_at missing or not YYYY-MM-DD; supported
- `test_observed_validation_does_not_read_registry` (p1-04c-language): AssertionError: child interpreter failed: ImportError: cannot import name 'language' from 'tracer_evalschema' (~/tracer/packages/evalschema/src/trace
- `test_bidi_reference_passes_biditest` (p1-04d-step1-bidi-conformance): AssertionError: BidiTest.txt: 489439/490846 data lines pass (1407 fail, 1435/770241 paragraph-level variants fail, 0 unparsed); isolate lines 273522/274412 pass; embe

Why these are the right reason:

- `test_ci_required_test_jobs_sync_bidi_ref_group`: `.github/workflows/ci.yml` runs `uv sync --locked` and `uv run --locked pytest` for the 3.11-3.14 jobs, with no `--group bidi-ref`; ADR 0017 Decision 7 and SCOPE P1-02 require the group to be synced there. Real gap in the scaffold (dev/SL to fix).
- `test_vendored_data_has_verification_note`: `supported_currencies.json` `_meta` has no `sources`, `retrieved_at` or `verification` (P1-03b not finished); the other three data files pass the same check.
- `test_bidi_reference_passes_biditest`: the UBA reference does not pass every line of BidiTest.txt (see below). This is a defect of the reference, demonstrated, and it triggers P1-04e.
- `test_observed_validation_does_not_read_registry`: the child interpreter that audits file opens cannot import `tracer_evalschema.language` yet (the child's Traceback ends in ImportError: cannot import name 'language' from 'tracer_evalschema').
- `test_package_version_equals_schema_version`: `tracer_evalschema.SCHEMA_VERSION` does not exist yet (the package has `__version__` only).

### Gate-bound tests (red until the last entity exists)

`test_every_entity_has_registered_prefix`, `test_value_objects_have_no_entity_fields` and `test_versioned_entity_set_matches_adr` compare the complete set of entities and value objects with the ADRs. They need the `registry` module now and all 27 entities and 36 value objects at the gate, so they cannot go green in the P1-04a/P1-05 batch. They are written as the SCOPE names require; flagged to staff-reviewer.

## Tests that passed on arrival (guards of work already done), with why that is acceptable

These check files and scripts that already exist (P1-01, P1-02, P1-03), the vendored Unicode data, and the harness itself. Each has a negative control or a discriminating assertion (SHA pin checker run on bad input, guard script run in a throwaway repository, no-reordering and crashing fake references run through the conformance harness, identity of vendored files checked by sha256).

- p1-01-02-repo-contract: `test_license_and_notice_present`
- p1-01-02-repo-contract: `test_spdx_headers_present_on_all_sources`
- p1-01-02-repo-contract: `test_ci_matrix_required_311_314_nonblocking_315`
- p1-01-02-repo-contract: `test_actions_pinned_by_sha`
- p1-01-02-repo-contract: `test_uv_lock_committed_and_sync_locked`
- p1-01-02-repo-contract: `test_uv_lock_up_to_date_with_pyproject`
- p1-01-02-repo-contract: `test_ci_uv_version_pinned_exactly`
- p1-01-02-repo-contract: `test_runtime_deps_license_allowlist`
- p1-01-02-repo-contract: `test_test_only_deps_absent_from_runtime_metadata`
- p1-01-02-repo-contract: `test_license_exceptions_exact`
- p1-01-02-repo-contract: `test_private_fixtures_gitignored`
- p1-01-02-repo-contract: `test_private_fixture_guard_fails_when_tracked`
- p1-03-data-files: `test_data_directory_ships_only_known_files`
- p1-03-data-files: `test_languages_exactly_nine`
- p1-03-data-files: `test_excluded_languages_absent[bn]`
- p1-03-data-files: `test_excluded_languages_absent[ur]`
- p1-03-data-files: `test_excluded_languages_absent[or]`
- p1-03-data-files: `test_excluded_languages_absent[as]`
- p1-03-data-files: `test_excluded_languages_absent[pa-Arab]`
- p1-03-data-files: `test_scripts_in_vendored_iso15924`
- p1-03-data-files: `test_vendored_registry_has_file_date`
- p1-03-data-files: `test_languages_native_names_need_native_review`
- p1-03-data-files: `test_iso4217_data_keeps_all_list_one_rows`
- p1-03-data-files: `test_vendored_regions_exactly_in_us_gb`
- p1-03-data-files: `test_supported_currencies_file_is_exactly_usd`
- p1-03-data-files: `test_supported_currencies_exist_in_iso4217`
- p1-03-data-files: `test_language_data_scripts_run_offline_checks`
- p1-04d-step1-bidi-conformance: `test_bidi_reference_passes_bidicharactertest`
- p1-04d-step1-bidi-conformance: `test_bidi_reference_conformance_covers_isolates_and_overrides`
- p1-04d-step1-bidi-conformance: `test_unicode_conformance_files_match_provenance`
- p1-04d-step1-bidi-conformance: `test_reference_unicode_version_matches_vendored_files`
- p1-04d-step1-bidi-conformance: `test_harness_detects_a_broken_reference`
- p1-04d-step1-bidi-conformance: `test_pool_characters_have_their_class`

## Bidi conformance result (P1-04d step 1)

Reference: `python-bidi` 0.6.12 `bidi.get_display` (Rust crate `unicode-bidi` 0.3.18, **Unicode 16.0.0**). Files: Unicode 16.0.0 `BidiTest.txt` and `BidiCharacterTest.txt`, vendored with provenance.

- `BidiCharacterTest.txt`: **91,707 / 91,707 data lines pass** (including 39 isolate lines and 81 embedding/override lines; paragraph embedding level checked for the auto-direction lines).
- `BidiTest.txt`: **489,439 / 490,846 data lines pass; 1,407 lines fail** (1,435 of 770,241 paragraph-level variants). Isolate lines: 273,522 / 274,412 pass (890 fail). Embedding/override lines: 303,828 / 305,180 pass (1,352 fail). All failures are visual-order mismatches; 1,407 of the 1,435 failing variants are explicit-RTL paragraphs and 28 are auto-direction.
- Cause (reproduced by hand): rule L1 is not applied across the explicit formatting characters that X9 retains. `get_display('a\t', base_dir='R')` returns `'\ta'` (the segment separator is reset to the paragraph level) but `get_display('a\u202a\t', base_dir='R')` returns the input unchanged. The same happens for trailing white space and for isolate formatting characters (FSI, LRI, RLI, PDI) that follow LRE/LRO/RLE/RLO/PDF/BN.
- Decision input: ADR 0017 Decision 7 requires every line, with no allowlist. **The criterion is not met, so the fallback (P1-04e, own clean-room UAX #9 implementation) is triggered.** The display_safe tests pass under python-bidi on their own inputs (none has an explicit embedding next to trailing separators) and are provisional until a conforming reference exists; they go through the single seam `tests/bidi_conformance.py::uba_visual`.
- Harness validity: a reference that does not reorder fails 52,001 of 91,707 BidiCharacterTest lines and 261,057 of 490,846 BidiTest lines (identity reference, measured), a crashing reference fails every line (`test_harness_detects_a_broken_reference`); the independent regex line counter agrees with the parser on every count (`test_bidi_reference_conformance_covers_isolates_and_overrides`).
