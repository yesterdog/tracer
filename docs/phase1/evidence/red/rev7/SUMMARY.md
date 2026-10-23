# Revision 7 red evidence: updated and new batch 1 tests (P1-01..P1-05)

Date: 2026-10-22. Written by qa. Nothing under `packages/evalschema/src` was written, no commits made, nothing staged. The earlier logs and `../SUMMARY.md` (revision 6 state) are untouched.

Command per group and Python: `python -m pytest -p no:cacheprovider -v <files>` from the repository root (`pyproject.toml` addopts: `-ra --strict-markers --strict-config -m 'not private_fixtures and not local_only'`). Each log `<group>.py<version>.log` starts with a header (date, command, interpreter, pydantic/pytest/jsonschema versions, `uv.lock` sha256, git status line). Interpreters: 3.11.16, 3.12.14, 3.13.15 (the repository `.venv`) and 3.14.7, all with pydantic 2.14.1, pytest 9.1.1, jsonschema 4.26.0; the 3.11, 3.12 and 3.14 environments are the scratch environments created earlier from `uv.lock` with the same pins.

## Counts per group (identical on all four interpreters)

| Group | Files | Failed | Passed | Logs |
|---|---|---|---|---|
| p1-04a-ids | test_ids.py | 28 | 0 | `p1-04a-ids.py3.11.log`, `.py3.12.log`, `.py3.13.log`, `.py3.14.log` |
| p1-04b-primitives | test_primitives.py | 90 | 0 | `p1-04b-primitives.py3.11.log`, `.py3.12.log`, `.py3.13.log`, `.py3.14.log` |
| p1-04c-language | test_language.py | 63 | 0 | `p1-04c-language.py3.11.log`, `.py3.12.log`, `.py3.13.log`, `.py3.14.log` |
| p1-04d-step2-text-and-display-safe | test_text_types.py, test_display_safe.py | 38 | 0 | `p1-04d-step2-text-and-display-safe.py3.11.log`, `.py3.12.log`, `.py3.13.log`, `.py3.14.log` |
| p1-05-base-versioning-digest-loader | test_entity_base.py, test_versioning.py, test_digest.py, test_loader.py | 81 | 0 | `p1-05-base-versioning-digest-loader.py3.11.log`, `.py3.12.log`, `.py3.13.log`, `.py3.14.log` |
| full-suite | every test file under packages/evalschema/tests | 300 | 118 | `full-suite.py3.11.log`, `.py3.12.log`, `.py3.13.log`, `.py3.14.log` |

The `full-suite` row covers the groups this revision did not touch (repository contract, data files, bidi conformance, the P1-04e clean-room UAX #9 tests): their 118 tests pass on arrival, unchanged. Collection: `pytest --collect-only` reports 418 tests, no errors, no skips, no xfails, no empty parametrization (`-W error` too).

## New tests (revision 7): why each is red

Failure type vocabulary: `ModuleNotFoundError` means the implementation module the test needs does not exist yet (the intended reason for every code test); the tests import lazily, so collection never breaks. Parameters are collapsed (count in the second column).

| Test | Cases | Failure type | One-line reason |
|---|---|---|---|
| `test_ids.py::test_from_external_signature_is_id_type_then_keyword_only_parts` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_ids.py::test_from_external_returns_a_pair_of_typed_id_and_external_ref_for_every_kind` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_ids.py::test_from_external_is_pure` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_ids.py::test_new_prefix_syntax_is_two_to_six_lowercase_letters` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_ids.py::test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_ids.py::test_new_prefix_rejects_every_permutation_of_every_registered_prefix` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| `test_ids.py::test_validate_new_prefix_flags_exactly_the_five_frozen_pairs` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_ids.py::test_opaque_id_fields_are_exactly_the_adr_allowlist` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.registry' (module not written yet) |
| `test_primitives.py::test_utc_rejects_excess_precision_numbers_and_dates` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_decimal_pattern_is_the_architects_pattern` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_decimal_pydantic_and_json_schema_accept_the_same_set` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_closed_value_sets_emit_enum_not_const_in_both_schema_modes` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_no_const_in_generated_schemas` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.registry' (module not written yet) |
| `test_primitives.py::test_money_display_rounds_half_even_with_exact_digits` | 36 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_money_display_default_places_and_argument_checks` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_money_add_returns_canonical_amounts` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_language.py::test_case_only_difference_gives_no_warning` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| `test_language.py::test_types_do_not_trim_labels` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| `test_language.py::test_codemix_message_applies_to_the_eight_non_english_languages_with_region_EN` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| `test_language.py::test_en_EN_and_other_pairs_get_the_ordinary_messages` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| `test_language.py::test_observed_grandfathered_is_a_whole_tag_case_insensitive_match` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| `test_language.py::test_observed_accepts_up_to_three_extlang_subtags` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.language' (module not written yet) |
| `test_text_types.py::test_human_label_rejects_variation_selectors_and_tag_characters` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| `test_text_types.py::test_human_label_rejects_every_default_ignorable_of_unicode_16` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| `test_text_types.py::test_human_label_rejects_del_separators_empty_and_edge_whitespace` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| `test_text_types.py::test_bidi_flagged_text_is_the_reusable_verified_flag_object` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| `test_display_safe.py::test_marker_table_spelling_and_shipped_file` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.text' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_rule_golden` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_known_credential_prefix_patterns` | 17 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_high_entropy_boundaries` | 9 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_scope_is_string_leaves_of_metadata_only` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_pointer_escaping_and_utf16_order` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flag_one_flag_per_path_prefix_reason_wins` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.ids' (module not written yet) |
| `test_entity_base.py::test_metadata_flags_supplied_list_must_equal_the_computed_list` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.base' (module not written yet) |
| `test_entity_base.py::test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| `test_entity_base.py::test_schema_version_errors_propagate_unwrapped_through_every_entry_point` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| `test_entity_base.py::test_digest_errors_propagate_unwrapped_through_every_entry_point` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| `test_entity_base.py::test_loader_typed_errors_are_not_wrapped_either` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.errors' (module not written yet) |
| `test_digest.py::test_digest_domain_removes_content_digest_and_schema_version_at_top_level_only` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_digest_domain_removes_nulls_at_every_object_depth_and_keeps_array_nulls` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_digest_domain_of_a_model_equals_that_of_its_json_dump` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_digest_domain_does_not_modify_its_input` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_canonical_bytes_are_the_jcs_of_the_domain` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_verify_digest_returns_none_or_raises` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_verify_digest_settles_the_key_and_the_scheme_before_hashing_the_content` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_content_digest_scheme_is_an_enum_in_both_schema_modes` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |
| `test_digest.py::test_key_ref_key_id_is_an_opaque_string_stored_verbatim` | 1 | ModuleNotFoundError | No module named 'tracer_evalschema.digest' (module not written yet) |

## Changed tests (revision 7)

| Test | Cases | Failure type | What changed / one-line reason |
|---|---|---|---|
| `test_ids.py::test_value_objects_have_no_entity_fields` | 1 | ModuleNotFoundError | api-surface 15a item 22 / N1: allows exactly the four reference fields; value-object name list 36 -> 42 (revision 6/7 additions); red because No module named 'tracer_evalschema.registry' (module not written yet) |
| `test_primitives.py::test_decimal_canonical` | 7 | ModuleNotFoundError | api-surface 15a item 5 / E1: non-canonical spellings are rejected with the canonical form in the message; `3` accepted; new parameter `0.150`; red because No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_money_may_be_negative_but_stays_canonical` | 1 | ModuleNotFoundError | api-surface 15a item 5: `2.50` is rejected (canonical `2.5` named), no longer normalized; red because No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_decimal_rejects` | 7 | ModuleNotFoundError | new parameter `-0.0`; red because No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_primitives.py::test_decimal_rejects_other_malformed_strings` | 1 | ModuleNotFoundError | eight more non-canonical lexemes (`01`, `.5`, `0.`, ...); red because No module named 'tracer_evalschema.primitives' (module not written yet) |
| `test_language.py::test_supported_region_outside_vendored_set_rejected` | 1 | ModuleNotFoundError | new case `en-EN` (ordinary region message; 15a item 11); red because No module named 'tracer_evalschema.language' (module not written yet) |

Also edited without a behaviour change: the module docstring of `test_display_safe.py` (the display tests go through `bidi_conformance.uba_visual` -> `load_uba()`, whose default is the clean-room UAX #9 reference `tests/support/uax9`; they were never python-bidi specific in code) and the docstring of `test_human_label_rejects_default_ignorables`. Not touched: `test_bidi_conformance.py`, `test_repo_contract.py` and the other python-bidi pins (project owner has not approved removal).

## Tests whose result is the same as before (red or green)

Every other test of the five groups keeps its revision 6 red reason (`ModuleNotFoundError` for a missing `tracer_evalschema.<module>`, plus the two `AssertionError`/`AttributeError` cases `test_observed_validation_does_not_read_registry` (the child interpreter cannot import `tracer_evalschema.language`) and `test_package_version_equals_schema_version` (no `SCHEMA_VERSION` yet)). The three gate-bound walks (`test_every_entity_has_registered_prefix`, `test_value_objects_have_no_entity_fields`, `test_versioned_entity_set_matches_adr`) and the new `test_opaque_id_fields_are_exactly_the_adr_allowlist` and `test_no_const_in_generated_schemas` stay red until every entity and value object exists (the gate), but they are red today only for the missing `registry` module.

## Validation against a throwaway correct implementation and mutants

A throwaway implementation of the revision 7 contract (outside the repository, not committed; it also carries stub entities and value objects so the gate-bound walks can run) passes every changed and new test on 3.11, 3.12, 3.13 and 3.14 (`300 passed` for the nine test modules that touch it; the only failures in that tree are tests that need the repository layout, `test_repo_contract.py`, `test_uax9_packaging.py` and `test_data_files.py::test_language_data_scripts_run_offline_checks`). Each of the 161 mutants below, applied alone, makes at least one of the changed or new tests fail; no mutant survived.

| Mutant | Caught by (first tests) |
|---|---|
| P01: DecimalString rewrites non-canonical input (rev 6 behaviour: 1.50 -> 1.5) | `test_decimal_canonical`, `test_decimal_pydantic_and_json_schema_accept_the_same_set`, `test_decimal_rejects` ... |
| P02: non-canonical message names the input but not the canonical form | `test_decimal_canonical`, `test_money_may_be_negative_but_stays_canonical` |
| P03: non-canonical message names the canonical form but not the input | `test_decimal_canonical` |
| P04: JSON Schema pattern is the rev-6 spelling (same set, different text) | `test_decimal_pattern_is_the_architects_pattern` |
| P05: JSON Schema pattern accepts trailing zeros (the old known gap) | `test_decimal_json_schema_is_string_with_canonical_pattern`, `test_decimal_pattern_is_the_architects_pattern`, `test_decimal_pydantic_and_json_schema_accept_the_same_set` |
| P06: pattern scale 19 instead of 18 (schema only) | `test_decimal_json_schema_is_string_with_canonical_pattern`, `test_decimal_pattern_is_the_architects_pattern`, `test_decimal_pydantic_and_json_schema_accept_the_same_set` |
| P07: validator scale limit 19 (pydantic accepts what the pattern rejects) | `test_decimal_pydantic_and_json_schema_accept_the_same_set`, `test_decimal_rejects_other_malformed_strings`, `test_decimal_scale_limit_is_18` |
| P08: negative zero accepted | `test_decimal_pydantic_and_json_schema_accept_the_same_set`, `test_decimal_rejects`, `test_money_may_be_negative_but_stays_canonical` |
| P09: canonical regex allows a leading plus | `test_decimal_pydantic_and_json_schema_accept_the_same_set`, `test_decimal_rejects`, `test_decimal_rejects_other_malformed_strings` |
| P10: UtcDatetime truncates >6 fractional digits | `test_utc_rejects_excess_precision_numbers_and_dates` |
| P11: UtcDatetime rounds nothing but allows up to 9 digits only (rejects 10+) | `test_utc_rejects_excess_precision_numbers_and_dates` |
| P12: UtcDatetime accepts int/float as epoch seconds | `test_utc_rejects_excess_precision_numbers_and_dates` |
| P13: UtcDatetime accepts bool | `test_utc_rejects_excess_precision_numbers_and_dates` |
| P14: UtcDatetime accepts date objects as midnight UTC | `test_utc_rejects_excess_precision_numbers_and_dates` |
| P15: UtcDatetime accepts numeric strings as epoch | `test_utc_rejects_excess_precision_numbers_and_dates` |
| P16: UtcDatetime accepts leap seconds (clamped to :59) | `test_utc_rejects_leap_second` |
| P17: display rounds half up | `test_money_display_rounds_half_even_with_exact_digits` |
| P18: display uses the default 28-digit context | `test_money_display_rounds_half_even_with_exact_digits` |
| P19: display strips trailing zeros (not exactly `places` digits) | `test_display_rounding_does_not_mutate_amount`, `test_money_display_rounds_half_even_with_exact_digits` |
| P20: display may print an exponent | `test_display_rounding_does_not_mutate_amount`, `test_money_display_rounds_half_even_with_exact_digits` |
| P21: display accepts negative places | `test_money_display_default_places_and_argument_checks` |
| P22: display accepts float places | `test_money_display_default_places_and_argument_checks` |
| P23: Money addition returns a non-canonical amount | `test_money_add_exact`, `test_money_add_returns_canonical_amounts` |
| P24: Money addition limited to the 28-digit context | `test_money_add_exact` |
| P25: SupportedCurrency emits const for a single code (plain Literal) | `test_closed_value_sets_emit_enum_not_const_in_both_schema_modes`, `test_no_const_in_generated_schemas`, `test_supported_currency_set_is_data_driven` |
| P26: SupportedCurrency enum only in validation mode | `test_closed_value_sets_emit_enum_not_const_in_both_schema_modes`, `test_no_const_in_generated_schemas` |
| P27: enum built from a hard-coded list, not the codes tuple | `test_closed_value_sets_emit_enum_not_const_in_both_schema_modes`, `test_supported_currency_set_is_data_driven` |
| P28: validator accepts lowercase currency | `test_closed_value_sets_emit_enum_not_const_in_both_schema_modes`, `test_money_rejects_non_usd` |
| I01: new prefix: one-edit neighbours accepted | `test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration`, `test_new_prefixes_not_confusable`, `test_validate_new_prefix_flags_exactly_the_five_frozen_pairs` |
| I02: new prefix: anagram rule missing | `test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration`, `test_new_prefix_rejects_every_permutation_of_every_registered_prefix`, `test_new_prefixes_not_confusable` ... |
| I03: new prefix: length up to 8 allowed | `test_new_prefix_syntax_is_two_to_six_lowercase_letters` |
| I04: new prefix: one-letter prefixes allowed | `test_new_prefix_syntax_is_two_to_six_lowercase_letters` |
| I05: new prefix: syntax is [a-z]+ (the rev-6 oracle) | `test_new_prefix_syntax_is_two_to_six_lowercase_letters` |
| I06: new prefix: distance 2 also rejected (stricter than the rule) | `test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration`, `test_new_prefix_syntax_is_two_to_six_lowercase_letters`, `test_new_prefixes_not_confusable` ... |
| I07: new prefix: uppercase tolerated (lower()ed) | `test_new_prefix_syntax_is_two_to_six_lowercase_letters` |
| I08: new prefix: trailing newline tolerated (re.match with $) | `test_new_prefix_syntax_is_two_to_six_lowercase_letters` |
| I09: new prefix: duplicate not reported as already registered | `test_validate_new_prefix_flags_exactly_the_five_frozen_pairs` |
| I10: new prefix: deletion/insertion not counted (substitution only) | `test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration`, `test_new_prefixes_not_confusable` |
| I11: from_external memoizes by (system, kind, value) | `test_from_external_is_pure`, `test_from_external_mints_v7_and_records_external_ref` |
| I12: from_external records a lookup table in a module dict | `test_from_external_is_pure` |
| I13: from_external writes a file | `test_from_external_is_pure` |
| I14: from_external: system is positional | `test_from_external_signature_is_id_type_then_keyword_only_parts` |
| I15: from_external normalizes (strips) the external value | `test_from_external_is_pure`, `test_from_external_mints_v7_and_records_external_ref` |
| I16: from_external returns the ref only | `test_from_external_is_pure`, `test_from_external_mints_v7_and_records_external_ref`, `test_from_external_returns_a_pair_of_typed_id_and_external_ref_for_every_kind` ... |
| I17: from_external ignores id_type (always RunId) | `test_from_external_returns_a_pair_of_typed_id_and_external_ref_for_every_kind`, `test_from_external_signature_is_id_type_then_keyword_only_parts` |
| L01: case-only change warns language_label_normalized | `test_case_only_difference_gives_no_warning` |
| L02: observed type trims surrounding white space | `test_observed_rejects_other_malformed_forms`, `test_types_do_not_trim_labels` |
| L03: supported type trims surrounding white space | `test_types_do_not_trim_labels` |
| L04: code-mix message for any primary subtag with region EN (even unsupported ones) | `test_en_EN_and_other_pairs_get_the_ordinary_messages` |
| L05: code-mix message also for en-EN | `test_en_EN_and_other_pairs_get_the_ordinary_messages`, `test_supported_region_outside_vendored_set_rejected` |
| L06: region message wins over the code-mix message | `test_codemix_message_applies_to_the_eight_non_english_languages_with_region_EN`, `test_hi_en_rejected_with_codemix_message` |
| L07: code-mix only when the region was written in lower case | `test_codemix_message_applies_to_the_eight_non_english_languages_with_region_EN`, `test_hi_en_rejected_with_codemix_message` |
| L08: grandfathered compared case-sensitively (lowercase table only) | `test_observed_grandfathered_is_a_whole_tag_case_insensitive_match`, `test_observed_rejects_all_26_grandfathered_tags` |
| L09: grandfathered matched as a prefix (rejects zh-min-nan-TW) | `test_observed_grandfathered_is_a_whole_tag_case_insensitive_match`, `test_observed_rejects_all_26_grandfathered_tags` |
| L10: extlang limited to two | `test_observed_accepts_up_to_three_extlang_subtags` |
| L11: extlang unlimited | `test_observed_accepts_up_to_three_extlang_subtags` |
| L12: grandfathered rejected only when written all lower case or all upper case | `test_observed_grandfathered_is_a_whole_tag_case_insensitive_match` |
| T01: HumanLabel allows variation selectors U+FE00-FE0F | `test_human_label_rejects_every_default_ignorable_of_unicode_16`, `test_human_label_rejects_variation_selectors_and_tag_characters` |
| T02: HumanLabel allows supplementary variation selectors U+E0100-E01EF | `test_human_label_rejects_every_default_ignorable_of_unicode_16`, `test_human_label_rejects_variation_selectors_and_tag_characters` |
| T03: HumanLabel allows Mongolian free variation selectors | `test_human_label_rejects_every_default_ignorable_of_unicode_16`, `test_human_label_rejects_variation_selectors_and_tag_characters` |
| T04: HumanLabel allows tag characters | `test_human_label_rejects_default_ignorables`, `test_human_label_rejects_every_default_ignorable_of_unicode_16`, `test_human_label_rejects_variation_selectors_and_tag_characters` |
| T05: HumanLabel allows U+E0001 only | `test_human_label_rejects_default_ignorables`, `test_human_label_rejects_every_default_ignorable_of_unicode_16`, `test_human_label_rejects_variation_selectors_and_tag_characters` |
| T06: HumanLabel allows DEL | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T07: HumanLabel allows U+2028 | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T08: HumanLabel allows U+2029 | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T09: HumanLabel allows the empty label | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T10: HumanLabel checks leading white space only | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T11: HumanLabel checks trailing white space only | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T12: HumanLabel checks only ASCII space at the edges | `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T13: HumanLabel rejects ALL white space (even inside) | `test_human_label_accepts_zwj_zwnj`, `test_human_label_rejects`, `test_human_label_rejects_del_separators_empty_and_edge_whitespace` ... |
| T14: HumanLabel rejects every Cf character (not just default-ignorable) | `test_human_label_rejects_every_default_ignorable_of_unicode_16` |
| T15: HumanLabel misses the unassigned DI block U+E0080-E0FFF | `test_human_label_rejects_every_default_ignorable_of_unicode_16`, `test_human_label_rejects_variation_selectors_and_tag_characters` |
| T16: HumanLabel misses U+FFF0-FFF8 / U+2065 | `test_human_label_rejects_every_default_ignorable_of_unicode_16` |
| T17: HumanLabel trims instead of rejecting | `test_human_label_rejects`, `test_human_label_rejects_del_separators_empty_and_edge_whitespace` |
| T18: marker spelling without brackets | `test_marker_table_json_matches_function`, `test_marker_table_spelling_and_shipped_file` |
| T19: marker for U+202E is [RLO] but U+2067 is [RLE] | `test_display_safe_markers_ascii_and_inert`, `test_marker_table_json_matches_function`, `test_marker_table_spelling_and_shipped_file` |
| T20: shipped bidi_markers.json carries a _meta | `test_marker_table_json_matches_function`, `test_marker_table_spelling_and_shipped_file` |
| T21: shipped bidi_markers.json differs from the function | `test_marker_table_json_matches_function`, `test_marker_table_spelling_and_shipped_file` |
| T22: BidiFlaggedText does not verify a supplied flag | `test_bidi_flag_forgery_rejected`, `test_bidi_flagged_text_is_the_reusable_verified_flag_object` |
| T23: BidiFlaggedText verifies only the true->false direction | `test_bidi_flag_forgery_rejected`, `test_bidi_flagged_text_is_the_reusable_verified_flag_object` |
| T24: display_safe keeps one control raw (U+2067) | `test_display_safe_markers_ascii_and_inert`, `test_display_safe_marks_isolates`, `test_marker_table_json_matches_function` ... |
| B01: pointer path not escaped | `test_metadata_flag_pointer_escaping_and_utf16_order`, `test_metadata_flag_rule_golden` |
| B02: pointer escape order wrong (/ before ~) | `test_metadata_flag_pointer_escaping_and_utf16_order`, `test_metadata_flag_rule_golden` |
| B03: flags sorted by code point, not UTF-16 | `test_metadata_flag_pointer_escaping_and_utf16_order`, `test_metadata_flag_rule_golden` |
| B04: flags sorted by key tuple, not path string | `test_metadata_flag_pointer_escaping_and_utf16_order`, `test_metadata_flag_rule_golden` |
| B05: flags in document order (unsorted) | `test_metadata_flag_pointer_escaping_and_utf16_order`, `test_metadata_flag_rule_golden`, `test_metadata_flags_supplied_list_must_equal_the_computed_list` |
| B06: high-entropy length threshold 31 | `test_metadata_flag_high_entropy_boundaries` |
| B07: high-entropy length threshold 33 | `test_metadata_flag_high_entropy_boundaries`, `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run` |
| B08: high-entropy distinct threshold 15 | `test_metadata_flag_high_entropy_boundaries` |
| B09: high-entropy distinct threshold 17 | `test_metadata_flag_high_entropy_boundaries` |
| B10: high-entropy does not need an upper-case letter | `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run`, `test_metadata_flag_rule_golden` |
| B11: high-entropy does not need a digit | `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run` |
| B12: high-entropy does not need a lower-case letter | `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run` |
| B13: run class lacks '='  | `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run` |
| B14: run class lacks '-' and '_' | `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run` |
| B15: boundary is \b (underscore is a word character) | `test_metadata_flag_known_credential_prefix_patterns` |
| B16: no boundary at all (matches inside words) | `test_metadata_flag_known_credential_prefix_patterns` |
| B17: prefix must start the string (no mid-string match) | `test_metadata_flag_known_credential_prefix_patterns`, `test_metadata_flag_one_flag_per_path_prefix_reason_wins` |
| B18: reason: entropy wins over prefix | `test_metadata_flag_known_credential_prefix_patterns`, `test_metadata_flag_one_flag_per_path_prefix_reason_wins`, `test_metadata_flag_pointer_escaping_and_utf16_order` ... |
| B19: sk- minimum length 16 | `test_metadata_flag_known_credential_prefix_patterns` |
| B20: AKIA tail accepts lowercase | `test_metadata_flag_known_credential_prefix_patterns` |
| B21: ASIA missing | `test_metadata_flag_known_credential_prefix_patterns`, `test_metadata_flag_rule_golden` |
| B22: JWT needs a signature | `test_metadata_flag_known_credential_prefix_patterns` |
| B23: PEM pattern only matches 'BEGIN PRIVATE KEY' | `test_metadata_flag_known_credential_prefix_patterns` |
| B24: github_pat_ missing | `test_metadata_flag_known_credential_prefix_patterns`, `test_metadata_flag_rule_golden` |
| B25: xox type letters [bap] only | `test_metadata_flag_known_credential_prefix_patterns` |
| B26: gh prefix letters [pousr] -> p only | `test_metadata_flag_known_credential_prefix_patterns` |
| B27: glpat- missing | `test_metadata_flag_known_credential_prefix_patterns`, `test_metadata_flag_rule_golden` |
| B28: hf_ minimum 24 | `test_metadata_flag_known_credential_prefix_patterns` |
| B29: AIza tail 30 | `test_metadata_flag_known_credential_prefix_patterns` |
| B30: sk_live_ minimum 12 | `test_metadata_flag_known_credential_prefix_patterns` |
| B31: rk_ missing | `test_metadata_flag_known_credential_prefix_patterns`, `test_metadata_flag_rule_golden` |
| B32: dict keys are flagged too | `test_metadata_flag_rule_golden`, `test_metadata_flag_scope_is_string_leaves_of_metadata_only` |
| B33: array elements are not walked | `test_metadata_flag_pointer_escaping_and_utf16_order`, `test_metadata_flag_rule_golden`, `test_metadata_flag_scope_is_string_leaves_of_metadata_only` |
| B34: supplied flags compared as a set (order ignored) | `test_metadata_flags_supplied_list_must_equal_the_computed_list` |
| B35: supplied flags only checked for being a subset | `test_metadata_flags_supplied_list_must_equal_the_computed_list`, `test_metadata_flags_verified_on_load` |
| B36: supplied flags with extra reasons accepted (paths only compared) | `test_metadata_flags_supplied_list_must_equal_the_computed_list` |
| B37: metadata_flags always recomputed (a forged list is silently replaced) | `test_metadata_flags_supplied_list_must_equal_the_computed_list`, `test_metadata_flags_verified_on_load` |
| B38: non-metadata fields are flagged too (name) | `test_metadata_flag_scope_is_string_leaves_of_metadata_only` |
| B39: schema version checked only after field validation | `test_schema_version_errors_propagate_unwrapped_through_every_entry_point` |
| B40: typed version errors derive from ValueError (pydantic wraps them) | `test_schema_version_errors_propagate_unwrapped_through_every_entry_point`, `test_schema_version_too_new`, `test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps` |
| B41: UnknownDigestScheme derives from ValueError | `test_digest_errors_propagate_unwrapped_through_every_entry_point`, `test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps` |
| B42: DigestMismatch derives from AssertionError | `test_digest_errors_propagate_unwrapped_through_every_entry_point`, `test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps` |
| B43: BundleError derives from ValueError | `test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps` |
| B44: ConfusablePrefixError loses the ValueError base | `test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps` |
| B45: dict-valued flag: path uses '.' separators | `test_metadata_flag_high_entropy_boundaries`, `test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run`, `test_metadata_flag_known_credential_prefix_patterns` ... |
| D01: schema_version removed at every depth | `test_digest_domain_removes_content_digest_and_schema_version_at_top_level_only` |
| D02: content_digest removed at every depth | `test_digest_domain_removes_content_digest_and_schema_version_at_top_level_only` |
| D03: nulls removed at the top level only | `test_canonical_bytes_are_the_jcs_of_the_domain`, `test_digest_domain_excludes_schema_version_and_nulls`, `test_digest_domain_of_a_model_equals_that_of_its_json_dump` ... |
| D04: nulls removed in lists as well | `test_canonical_bytes_are_the_jcs_of_the_domain`, `test_digest_domain_of_a_model_equals_that_of_its_json_dump`, `test_digest_domain_removes_nulls_at_every_object_depth_and_keeps_array_nulls` |
| D05: nulls not removed inside lists of objects | `test_digest_domain_of_a_model_equals_that_of_its_json_dump`, `test_digest_domain_removes_nulls_at_every_object_depth_and_keeps_array_nulls` |
| D06: pydantic exclude_none instead of the JSON rule (nulls in dict values kept) | `test_canonical_bytes_are_the_jcs_of_the_domain`, `test_digest_domain_excludes_schema_version_and_nulls`, `test_digest_domain_of_a_model_equals_that_of_its_json_dump` ... |
| D07: empty containers dropped | `test_digest_domain_excludes_schema_version_and_nulls`, `test_digest_domain_of_a_versioned_entity`, `test_digest_domain_removes_nulls_at_every_object_depth_and_keeps_array_nulls` ... |
| D08: digest_domain mutates its input mapping | `test_digest_domain_does_not_modify_its_input`, `test_digest_domain_of_a_model_equals_that_of_its_json_dump`, `test_digest_domain_of_a_versioned_entity` ... |
| D09: verify_digest returns True on a match | `test_digest_plain_sha256_jcs_golden`, `test_hmac_digest_golden`, `test_keyed_digest_unverifiable_without_key_raises` ... |
| D10: verify_digest returns False on a mismatch | `test_digest_domain_of_a_versioned_entity`, `test_hmac_digest_golden`, `test_keyed_digest_unverifiable_without_key_raises` ... |
| D11: verify_digest hashes the content before settling the key | `test_verify_digest_settles_the_key_and_the_scheme_before_hashing_the_content` |
| D12: verify_digest: resolver exceptions pass through | `test_verify_digest_returns_none_or_raises` |
| D13: verify_digest: unknown scheme check after hashing | `test_verify_digest_settles_the_key_and_the_scheme_before_hashing_the_content` |
| D14: verify_digest asks the resolver for plain digests too | `test_verify_digest_settles_the_key_and_the_scheme_before_hashing_the_content` |
| D15: ContentDigest.scheme schema is a plain string (no enum) | `test_content_digest_scheme_is_an_enum_in_both_schema_modes` |
| D16: ContentDigest.scheme schema uses const for the first scheme only (serialization) | `test_content_digest_scheme_is_an_enum_in_both_schema_modes` |
| D17: ContentDigest.scheme enum in validation mode only | `test_content_digest_scheme_is_an_enum_in_both_schema_modes` |
| D18: ContentDigest.scheme enum lists a third scheme | `test_content_digest_scheme_is_an_enum_in_both_schema_modes` |
| D19: KeyRef.key_id is a typed-ID pattern | `test_content_digest_scheme_registered`, `test_digest_key_ref_must_be_absent_for_v1`, `test_hmac_digest_golden` ... |
| D20: canonical_bytes is not JCS (json.dumps sort_keys) | `test_canonical_bytes_are_the_jcs_of_the_domain`, `test_digest_plain_sha256_jcs_golden`, `test_digest_rejects_non_ijson_values` ... |
| G01: TestProfileRef gains schema_version | `test_value_objects_have_no_entity_fields` |
| G02: a value object gains created_at | `test_value_objects_have_no_entity_fields` |
| G03: a value object gains metadata | `test_value_objects_have_no_entity_fields` |
| G04: a value object gains its own id | `test_value_objects_have_no_entity_fields` |
| G05: aad_fields lacks schema_version (the fourth reference field is absent) | `test_value_objects_have_no_entity_fields` |
| G06: TestProfileRef.id is an untyped string | `test_value_objects_have_no_entity_fields` |
| G07: PricingCatalogRef.id typed with the wrong kind | `test_value_objects_have_no_entity_fields` |
| G08: a new opaque *_id field appears (RunBundle.trace_id: str) | `test_every_id_reference_field_has_registered_prefix`, `test_opaque_id_fields_are_exactly_the_adr_allowlist` |
| G09: ToolCallEvent.external_call_id becomes a typed ID | `test_opaque_id_fields_are_exactly_the_adr_allowlist` |
| G10: aad subject_id is an opaque string | `test_every_id_reference_field_has_registered_prefix`, `test_opaque_id_fields_are_exactly_the_adr_allowlist` |
| G11: VersionedRef.id loses its typed leaves | `test_value_objects_have_no_entity_fields` |
| G12: the 3 allowed value-object names are missing (VersionedRef removed from names) | `test_value_objects_have_no_entity_fields` |
| I18: validate_new_prefix ignores its existing= argument | `test_new_prefix_syntax_is_two_to_six_lowercase_letters`, `test_new_prefixes_not_confusable`, `test_validate_new_prefix_flags_exactly_the_five_frozen_pairs` |
| I19: validate_new_prefix returns True instead of None | `test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration`, `test_new_prefix_syntax_is_two_to_six_lowercase_letters`, `test_new_prefixes_not_confusable` |
| I20: validate_new_prefix checks the values of existing instead of the keys | `test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration`, `test_new_prefix_rejects_every_permutation_of_every_registered_prefix`, `test_new_prefixes_not_confusable` ... |
