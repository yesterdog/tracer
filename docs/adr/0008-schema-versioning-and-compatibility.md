# ADR 0008: Schema versioning and compatibility

- Status: Accepted (user decisions Q5, Q6, OQ1; revision 3: per-version frozen fixtures and the upgrade path accepted by the user). P-22 decided by the user in revision 5, accepted as recommended. Revision 7: how typed errors surface through pydantic, and the input rules for `schema_version` (batch 1 item 19), fixed by schema-architect. Open [Proposed] items: none.
- Date: 2026-10-22 (revision 5: P-22 folded into Decision 4)
- Task: P1-00a (implemented by P1-05, P1-15, P1-16, P1-20a)

## Context
`tracer-evalschema` is the source of truth. JSON Schema, OpenAPI, TypeScript types and DB tables are generated from it. Files written by one version will be read by others: CI artifacts, exports, benchmark bundles. Silently dropping fields that a reader does not understand corrupts data without anyone noticing. Generated artifacts must be reproducible so that reviewers see real changes only.

## Decision
1. **One version string.** `SCHEMA_VERSION` is a semver string. The first release is `0.0.1`. The package version equals `SCHEMA_VERSION`. Every entity carries `schema_version`; value objects inherit it from their entity (ADR 0001).
2. **Reader rules.**
   - A file with a newer version raises `SchemaVersionTooNew`. Fields are never silently dropped.
   - While the version is `0.0.x`, the reader requires an **exact match**. An older file raises `SchemaVersionMismatch`.
   - A malformed version (`"1"`, `"v0.0.1"`) raises `SchemaVersionInvalid`.
   - All errors are typed exceptions carrying the found and expected versions.
   - **`schema_version` is required on input** (no default). The only valid form is `MAJOR.MINOR.PATCH` with non-negative integers without leading zeros, compared numerically (`0.0.10` is newer than `0.0.2`). Pre-release and build suffixes, whitespace and non-strings are `SchemaVersionInvalid` (revision 7).
   - **Typed errors are not wrapped by pydantic (revision 7).** Pydantic v2 turns only `ValueError`, `AssertionError` and `PydanticCustomError` raised in validators into `ValidationError`. Every typed error of this package that derives from `TracerSchemaError` and not from `ValueError` (the version errors, the digest errors, `NonIJsonValueError`, the loader and bundle errors) therefore propagates as itself out of `model_validate`, `model_validate_json`, `TypeAdapter.validate_*` and the strict loader, including from nested models. Consequences: callers catch the typed class; the first typed error aborts validation and carries no pydantic `loc`, so each typed error names the offending value (and the path where known) in its message. `ConfusablePrefixError` and `DataFileError` are also `ValueError`s; they are raised by `validate_new_prefix()` and the data loaders, never inside model validation.
3. **Bundles are single-version.** Every entity in a RunBundle or ExportBundle has the same `schema_version`. A mixed bundle is rejected.
4. **Change classes** during 0.0.x: every release bumps the patch and is treated as incompatible. Additive changes (new optional field, new enum member, new digest scheme, new ID prefix) and breaking changes (removing, renaming, retyping, tightening) are still classified in the CHANGELOG, and a breaking change needs a migration note. **The rule from 0.1.0 on (P-22):** between 0.1.0 and 1.0.0 a minor bump is breaking and a patch is additive; from 1.0.0 a reader accepts the same major with minor <= its own (missing optional fields become `None`), rejects a newer minor with `SchemaVersionTooNew`, and rejects another major unless it is upgraded through `upgrade()`.
5. **Frozen fixtures, per version (accepted in revision 3).**
   - Every released version `V` has a directory `tests/fixtures/frozen/<V>/` holding its golden fixtures. Once `V` is released, the directory is immutable: its file hashes are recorded and checked.
   - `test_frozen_fixtures_still_parse` asserts: (a) the fixtures of the current `SCHEMA_VERSION` parse with the strict loader; (b) for every older version without a registered upgrade step, each fixture raises `SchemaVersionMismatch` (not another error, and never a silent parse); (c) for every older version with a registered step, `upgrade(doc, from=V)` produces a document that parses under the current version and equals the stored upgraded twin in `tests/fixtures/frozen/<V>/upgraded/<current>/`.
   - `upgrade()` is a chain of pure, registered steps `V -> V+1`. A step is required for every release from 0.1.0 on. During 0.0.x a step is optional; without one, older files stay unreadable, which (b) checks explicitly.
   - The test iterates over versions in its body, not through an empty `parametrize`, so it never produces a skip when there are no older versions (gate item 2).
6. **Strict loading.** The loader rejects duplicate JSON object keys and enforces a nesting-depth limit with a typed error, never a `RecursionError`. Unknown fields are forbidden (`extra="forbid"`).
7. **JSON Schema export.** Draft 2020-12, one file per entity plus the bundle, `$id = urn:tracer:evalschema:<version>:<entity>`. Output is byte-identical across runs. Files ship inside the wheel. `schema.lock` records the SHA-256 of each file, tied to `SCHEMA_VERSION`. Changing a schema file without bumping the version fails CI.
8. **Validation and serialization schemas agree.** Pydantic's validation-mode and serialization-mode schemas must be identical. Pydantic and JSON Schema must both reject every invalid fixture. A divergence fails CI unless it is listed in `KNOWN_SCHEMA_GAPS` with an owner (only non-blocking mapping gaps may be listed, gate item 2).
9. **Generated artifacts are never edited by hand.** Field docs and schemas are regenerated in CI, and any diff fails the build.
10. **Runtime dependencies:** stdlib, pydantic (and its dependencies) and `rfc8785` only.

## Rejected alternatives
- **Tolerant reader (ignore unknown fields).** It silently loses data written by newer producers, which is the exact failure the user ruled out (Q5).
- **Accept older 0.0.x files with defaults.** Before 0.1 there is no compatibility promise. Partial acceptance would create untested upgrade paths. Exact match keeps behaviour predictable until a policy with migrations exists.
- **One shared frozen-fixture directory that is edited on every release.** Editing frozen fixtures removes the evidence that old files were ever readable; the test would always pass and prove nothing.
- **Frozen fixtures that must parse directly under every later version.** That contradicts exact-match reading during 0.0.x; the test would have to be weakened or skipped. Explicit mismatch-or-upgrade keeps it strict.
- **Date-based or integer schema versions.** Change classes cannot be expressed. Semver is what downstream tools expect.
- **`$id` as an https URL.** No domain is owned (OQ1). A URL that does not resolve, or that someone else later controls, is worse than a URN. A URL alias can be added later.
- **Generate schemas at runtime only.** Consumers in other languages need static files, and reproducibility needs locked bytes.
- **Per-entity independent versions.** Entities cross-reference each other, so independent versions create a compatibility matrix nobody tests.

## Consequences
- During 0.0.x, every release makes all earlier files unreadable without an upgrade step. That is intended for a pre-release, and the frozen-fixture test makes it visible.
- From 0.1.0 every release must ship its upgrade step and upgraded twins.
- The `$id` URN uses the unregistered namespace identifier `tracer`. RFC 8141 expects registered NIDs, so the URN is a stable identifier but not a formally registered one. Accepted under OQ1.

## Invariants (testable)
- I1. Required EntityBase fields are present on every entity. Test: `test_entity_base_required_fields`.
- I2. Extra fields are rejected. Test: `test_extra_field_forbidden`.
- I3. Newer version -> `SchemaVersionTooNew`. Test: `test_schema_version_too_new`. Adversarial: A-10.
- I4. Older version during 0.0.x -> `SchemaVersionMismatch`. Test: `test_schema_version_mismatch_older`.
- I5. Malformed version -> `SchemaVersionInvalid`. Test: `test_schema_version_invalid["1","v0.0.1"]`.
- I6. Version errors carry the found and expected versions. Test: `test_version_errors_carry_found_and_expected`.
- I7. Mixed-version bundles are rejected. Test: `test_bundle_mixed_versions_rejected`.
- I8. Duplicate keys are rejected. Test: `test_strict_loader_duplicate_keys`. Adversarial: A-08.
- I9. Depth 10k raises a typed error, not `RecursionError`. Test: `test_strict_loader_depth_10k_no_recursionerror`. Adversarial: A-06.
- I10. Schema export is byte-identical across two runs. Test: `test_jsonschema_byte_identical_two_runs`.
- I11. `schema.lock` matches `SCHEMA_VERSION`, and a changed schema without a version bump fails. Tests: `test_schema_lock_matches_version`, `test_schema_change_without_version_bump_fails` (negative control).
- I12. All `$id` values are URNs. Test: `test_schema_ids_are_urns`.
- I13. Validation schema equals serialization schema. Test: `test_validation_eq_serialization_schema`.
- I14. Invalid fixtures are rejected by both validators; valid ones round-trip. Tests: `test_invalid_rejected_by_both`, `test_roundtrip_valid`, `test_bundle_roundtrip_model_json_schema_model`.
- I15. Every property is covered by a valid fixture. Test: `test_every_property_covered_by_valid_fixture`.
- I16. Runtime imports and dependency licenses. Tests: `test_runtime_imports_whitelist`, `test_runtime_deps_license_allowlist`, `test_test_only_deps_absent_from_runtime_metadata`.
- I17. Frozen fixtures: current version parses; older versions mismatch or upgrade (Decision 5). Test: `test_frozen_fixtures_still_parse`.
- I18. Released frozen-fixture directories are unchanged. Test: `test_frozen_fixture_dirs_immutable`.
- I19. The package version equals `SCHEMA_VERSION`. Test: `test_package_version_equals_schema_version`.
- I20. The CHANGELOG has an entry for `SCHEMA_VERSION`. Test: `test_changelog_has_entry_for_schema_version`.

## Decided items (formerly [Proposed]; all decided by the user in revision 5)
- **P-22. Compatibility after 0.0.x. Decided by the user (revision 5): accepted as recommended.** Decision: between 0.1.0 and 1.0.0 a minor bump is breaking and a patch is additive; from 1.0.0 a reader accepts the same major with minor <= its own (missing optional fields become `None`), rejects a newer minor with `SchemaVersionTooNew`, and rejects another major unless it is upgraded through `upgrade()`. Reason: SCOPE defines only 0.0.x; without a stated rule, the first 0.1.0 release would have to invent one under time pressure, and the frozen-fixture test (c) needs to know which versions must upgrade. Had it been decided otherwise: the chosen rule replaces this one, and only reader-rule tests for 0.1.0+ change; nothing in 0.0.1 changes.
