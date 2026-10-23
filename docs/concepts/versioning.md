# Versioning

Two kinds of version exist: the version of the **schema** (one string for the whole package),
and the version of a **versioned entity** (an integer per record). Decisions:
[ADR 0008](../adr/0008-schema-versioning-and-compatibility.md),
[ADR 0009](../adr/0009-entity-versioning.md),
[ADR 0003](../adr/0003-enum-extensibility.md) Decision 4.

## The schema version

- `SCHEMA_VERSION` is semver and equals the package version. The first release is `0.0.1`
  (unreleased). Every entity carries `schema_version`, required on input.
- **Readers never drop data silently.** A newer file raises `SchemaVersionTooNew`. While the
  version is `0.0.x`, an older file raises `SchemaVersionMismatch` (exact match). A malformed
  version raises `SchemaVersionInvalid`. Unknown fields are always rejected.
- Every record in a bundle has the same schema version.
- **During 0.0.x** every release is treated as incompatible. **From 0.1.0** a minor bump is
  breaking and a patch additive until 1.0.0; from 1.0.0 a reader accepts the same major with an
  equal or older minor and needs `upgrade()` for another major.
- The CHANGELOG classifies every change as additive (new optional field, new closed-set member,
  new digest scheme, new ID prefix) or breaking (remove, rename, retype, tighten), with a
  migration note for breaking ones.
- The JSON Schema files carry the version in their `$id`
  (`urn:tracer:evalschema:0.0.1:<Entity>`) and in `schema.lock`. Once a version is released,
  its lock is frozen; changing a schema then requires a version bump.

## Versioned entities

- Persona, TestProfile, Evaluator, MetricDefinition, MetricBinding, Rubric, PricingCatalog,
  AgentTarget and Dataset are versioned: one `id` across edits, `version` an integer from 1,
  and `(id, version)` immutable. Each record carries a `content_digest` (SHA-256 over its RFC 8785
  canonical JSON), verified on load.
- References to them are objects `{id, version, content_digest?}`, never strings. `key@version`
  (`p95_latency@3`) is display text only.
- Every other entity is a record of fact: written once, replaced only by a new record with a new
  ID. A bundle holding two different contents under one ID is rejected.
- A run's `ConfigSnapshot` embeds the evaluator, persona, metric definitions, bindings and rubric
  it used, and references the test profile and pricing catalog, so the run can be reproduced and
  audited.

## Tests

`test_schema_version_too_new`, `test_schema_version_mismatch_older`, `test_package_version_equals_schema_version`,
`test_schema_lock_matches_version`, `test_schema_change_without_version_bump_fails`,
`test_released_version_forbids_schema_changes_under_the_same_version`,
`test_versioned_content_digest_mismatch_rejected`, `test_snapshot_jcs_digest_stable`.
