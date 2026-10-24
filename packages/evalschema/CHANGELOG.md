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
version. The Phase 1 gate (staff-reviewer) passed with conditions at commit `538c286` (`534c346`
before the history rewrite of 2026-10-24); see `docs/phase1/SCOPE.md` section 7.

First version. Everything below is new, so there is no change class and no migration.

### Package

- Pydantic v2 models, Python 3.11 or later (required CI legs 3.11 to 3.14 on hosted Linux; a
  non-blocking 3.15 leg, which has run on a 3.15 release candidate). Runtime dependencies:
  `pydantic>=2.11,<3` and `rfc8785>=0.1.4,<0.2` only, bounded ranges tested at their lower bound
  and at the locked versions (see "Development notes"). Licence Apache-2.0.
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
- A run bundle re-checks every stored result against its run: its last rule (`[applicability]`)
  re-runs `results.applicability` with the snapshot's definition over the transcripts and turns
  the result read and rejects a difference in the status class, `na_reason`, `missing_kinds`,
  `language_source` or `declared_language_transcript_ids`; a run that lists `agent_transcript`
  and holds turns needs an agent turn (ADR 0004 Decision 9). The optional
  `MetricResult.transcript_ids` (added during 0.0.1 development; additive, so `SCHEMA_VERSION`
  stays and the lock was regenerated) names the transcripts a result read; it is required when a
  bundle holds several transcripts and the metric reads transcripts or is language dependent.
  Producers that wrote such bundles before must add it, and must store each result as
  `applicability` decides it.
- Phase 1 gate fixes (added during 0.0.1 development; `SCHEMA_VERSION` stays `0.0.1`; the JSON
  Schema files of `Transcript`, `MetricResult`, `RunBundle`, `ExportBundle` and the lock were
  regenerated). The rules tighten what loads, so documents that relied on the looser behaviour
  are rejected; producers must fix such data, nothing is converted:
  - A run bundle rejects a `scored` result of a metric that reads transcripts when the result
    read no turn (no agent turn for `agent_transcript`), at the field `status` (T-B13).
  - A run bundle recomputes `per_turn.total_turns` and `per_turn.counts.not_applicable` of a
    stored per-turn summary from the turns the result read and rejects a difference (T-B16);
    `passed`, `failed` and `undetermined` are not recomputed. Both rules are off when a
    transcript or turn is declared withheld, so a run bundle whose turns were replaced by a
    sealed `transcript_text` must declare them withheld.
  - A `not_applicable` result with `missing_input` or `agent_channel_unknown` carries no
    `per_turn` summary (model and JSON Schema).
  - `LanguageDeclaration.declared_by` is optional (default `None`; additive: it leaves the
    schema's `required` list and may be `null`). A `shared_export` or `benchmark` export rejects
    a transcript whose declaration has one (`[declared_by]`); internal exports keep it.
- Security hardening from the P1-21 audit (added during 0.0.1 development; code-only rules, so
  no schema file, lock, golden fixture or field document changed and `SCHEMA_VERSION` stays).
  They tighten rules, so documents that relied on the looser behaviour are rejected; producers
  must fix such data, nothing is converted:
  - `KeyRef.key_id` and `key_version` reject credential-shaped values (a known key prefix, any
    `=`, a high-entropy segment split at `/` and `:`) with error type `key_ref_credential` and
    no quoted value; `KeyRef`, `ContentDigest` and every model that embeds them or a `DekWrap`
    hide their input in validation errors.
  - A key for `hmac-sha256-jcs/v1` has at least 32 bytes in `compute_digest` and
    `verify_digest`; a shorter key raises the new `errors.WeakDigestKeyError` (a `ValueError`
    and a `DigestKeyUnavailable`).
  - `DigestKeyUnavailable` no longer names the key id.
  - A sealed payload's digest key must have a `key_id` other than the KEK's, whatever the
    versions (the same `key_id` with another `key_version` was accepted before).
  - `ProviderConfigRef.config_ref` rejects a high-entropy name part after any scheme.
  - Messages outside hiding models quote at most 64 code points of a rejected value, then
    `...`.
- Importing a Pipecat Evals scenario (fixture F5) that carries per-turn expectations loses
  information: the event assertions of a scripted scenario (`response` with `eval` or
  `text_contains`, `within_ms` budgets, `absent`, `llm_marker` checks, `user_transcription`
  text) have no place in an Evaluator, which holds a script, expected tool calls, an expected
  outcome and metric bindings. They are recorded as non-blocking gaps in the gap register (no
  MVP-1 metric reads them), so an imported scenario runs its script and its declared
  expectations but **cannot reproduce the verdict Pipecat Evals gives for that scenario**.
- Limitations and non-guarantees: `SCHEMA_POLICY.md`.

### Development notes

Changes made while 0.0.1 was being built. None changes the version, because nothing was
released.

- **Year formatting on Linux.** `UtcDatetime` serialized instants with `strftime("%Y-...")`.
  glibc does not zero-pad `%Y` for years below 1000, so on Linux an instant in year 1 was
  written as `1-01-01T...` and failed the type's own pattern (macOS pads, which hid the bug
  locally; the hosted Linux CI legs 3.11 to 3.13 caught it). Fixed in two steps: the shipped
  serializer formats the year explicitly with four digits (`d15410f`), and the test-side
  converters and helpers that used the same `strftime` pattern do the same (`7c2fe48`). The
  output on macOS is unchanged byte for byte, and no schema file or lock changed. Hosted run
  40043294681 at `7c2fe48` passed every job on Linux, 3.11 to 3.15.
- **Runtime dependency ranges.** The package declares `pydantic>=2.11,<3` and `rfc8785>=0.1.4,<0.2` (it was pinned exactly during development). The suite passes on pydantic 2.11.0, the oldest version of the range (a CI leg runs it), and on the locked 2.14.1; pydantic 2.10.x is excluded because it renders a different character in one generated schema description. `pipecat-ai` 1.13.0 (`pydantic>=2.10.6,<3`) installs next to it. Each release below 3 is expected to keep working but is verified only on the versions CI runs.

### Not in this release

- No database tables, so no Alembic migrations (the relational store is the separate package
  `tracer-evaldb`).
- No export builder (exports are validated, not built), no redaction, no retention enforcement,
  no production key management (sealing is defined; the reference implementation is test-only).
- No upgrade step (`upgrade()`), because no earlier version exists.
