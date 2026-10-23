# Changelog: `tracer-evalschema`

Every release lists its changes, each classified **additive** (a new optional field, closed-set
member, digest scheme or ID prefix) or **breaking** (a field removed, renamed or retyped, a rule
tightened), and every breaking change carries a migration note (ADR 0008 Decision 4). During
`0.0.x` every release bumps the patch version and readers require an exact version match, so a
file of one `0.0.x` version is not readable by another without an upgrade step. The rules for
`0.1.0` and later are in `SCHEMA_POLICY.md` section 3.

## 0.0.1 (unreleased)

**Status: not released.** No version tag exists, nothing is published to PyPI, and the
availability of the name `tracer-evalschema` there is not verified. Until the release the JSON
Schema files and `schema.lock` are regenerated in place under the same version; at the release
the lock is frozen under `tests/fixtures/frozen/0.0.1/` and any later schema change needs a new
version. The Phase 1 gate (staff-reviewer) has not passed yet; see `docs/phase1/SCOPE.md`
section 7.

First version. Everything below is new, so there is no change class and no migration.

### Package

- Pydantic v2 models, Python 3.11 or later (CI legs 3.11 to 3.14; 3.15 non-blocking and not
  run). Runtime dependencies: `pydantic` 2.14.1 and `rfc8785` 0.1.4 only. Licence Apache-2.0.
- 26 entities, each with `id`, `schema_version`, `created_at`, `metadata` and the computed,
  load-verified `metadata_flags`: Project, AgentTarget, Persona, TestProfile, Evaluator,
  MetricDefinition, MetricBinding, Rubric, ConfigSnapshot, Run, Artifact, Transcript, Turn,
  Exchange, ToolCallEvent, StageSpan, MetricResult, RunVerdict, Dataset, DatasetItem,
  PricingCatalog, UsageRecord, CostRecord, CostSummary, RunBundle, ExportBundle. Nine are
  versioned (Persona, TestProfile, Evaluator, MetricDefinition, MetricBinding, Rubric,
  PricingCatalog, AgentTarget, Dataset).
- 70 value objects (no ID), among them Money, ContentDigest, KeyRef, PrivacyMarker,
  SealedPayload, ChannelMap, Segment, PriceEntry, CostExclusion.
- IDs: UUIDv7 with a type prefix on the wire (`run_<uuid>`), 26 registered prefixes.
- JSON Schema (draft 2020-12): one file per entity under `tracer_evalschema/schemas/`, `$id`
  `urn:tracer:evalschema:0.0.1:<Entity>`, `schema.lock`; generator and drift check
  `python -m tracer_evalschema.schema_export --write | --check`.
- Digest schemes `sha256-jcs/v1` and `hmac-sha256-jcs/v1`; one verification function,
  `digest.verify_digest`.
- Pure helpers for applicability (`not_applicable` reasons), the verdict (`render_headline`,
  `render_weighted_score`, `render_per_turn_share`), human-subject derivation
  (`derive_human_subject`, `export_human_subject`), price resolution and cost computation
  (`resolve_price`, `compute_cost`, `summarize_costs`), reported-cost import
  (`import_reported_cost`), language labels, `display_safe`, and the bundle checks.
- Strict loading: duplicate keys rejected, depth limit, 256 MiB default limit for bundles.

### Decided behaviour worth knowing

- Missing inputs give `not_applicable`, missing costs `not_available`; never 0 or false.
- `Evaluator.expected_tool_calls_exhaustive` (strict boolean or `null`, default `null`): `null`
  is not stated, `true` makes any tool call outside `expected_tool_calls` a failure (with an
  empty list: the agent must call nothing), `false` states that other calls are allowed. It is
  inside the Evaluator's content digest.
- `metadata` on every entity is an I-JSON object (RFC 7493): NaN, infinities, integers outside
  +-(2**53 - 1), lone surrogates and values that are not JSON are rejected (the error names the
  member's JSON Pointer, never its value); finite floats are kept unchanged.
- An export bundle's `purpose` is self-declared by its producer. A consumer that needs a purpose
  passes the keyword-only `expected_purpose` to `bundle.check_export_bundle` or
  `loader.load_bundle` (`None`, the default, accepts any purpose); a different label is a
  `ValueError` tagged `[purpose]`.
- Depth and size: the JSON-text loader limits nesting to 256 levels and bundles to 256 MiB;
  Python objects passed to `model_validate` are not depth-limited (very deep input raises
  `RecursionError`). No length limits on labels, lists or `metadata` in this release.
- Money is USD only; reported costs in other currencies are kept raw and marked not available.
- Nine configured languages, native script only; observed language tags are never rejected.
- Shared and benchmark exports need sealed or redacted human-subject payloads; benchmarks also
  need obtained consent.
- Limitations and non-guarantees: `SCHEMA_POLICY.md`.

### Not in this release

- No database tables, so no Alembic migrations.
- No export builder (exports are validated, not built), no redaction, no retention enforcement,
  no production key management (sealing is defined; the reference implementation is test-only).
- No upgrade step (`upgrade()`), because no earlier version exists.
