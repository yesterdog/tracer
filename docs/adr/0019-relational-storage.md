# ADR 0019: Relational storage of evalschema 0.0.1

- Status: Accepted (Phase 2 batch 0, task P2-00a). Records the questions Q1, Q2, Q3, Q4, Q7, Q8, Q9, Q18, Q19, Q24, Q25, Q26 and Q30 of `docs/phase2/SCOPE.md` section 3, auto-approved by the lead on the recommended option under the owner's standing rule of 2026-10-23 (decision log "PHASE 2 SCOPE delivered"), and the bounded dependency ranges of SCOPE Decisions D2. The points where the SCOPE was ambiguous were resolved by reading the code of `tracer_evalschema`; each is listed under "Ambiguities resolved" with the recommendation the lead auto-approves. Revision 35 (2026-10-24, docs only): amended for qa's batch B contract (`docs/phase2/api-surface-batch-b.md` section 10, ambiguities A1 to A17 auto-approved by the lead, decision log "PHASE 2 BATCH B (qa) DELIVERED"; SCOPE Decisions D4): `external_ref_index` unique for `run` only and finding P2-F3 (A1); the integer ceiling `2**53 - 1` (A2, P2-F2); `artifact.turn_ref` and `tracer_meta.singleton` types (A3, A4); nullability (A5); the decode error rule (A9); closed-set CHECKs on `deletion_audit` (A10); the class-23 error mapping (A12); the UUID CHECK covers the version nibble only (A17). Revision 36 (2026-10-24, docs only): Decision 18 amended for qa's batch C contract (`docs/phase2/api-surface-batch-c.md` sections 7 and 11, ambiguities A8 and A9 auto-approved by the lead, decision log "PHASE 2 BATCH C (qa) DELIVERED"; SCOPE Decisions D5): the `RESTRICT` SQLSTATE differs between Postgres versions and the hook maps the whole class 23 (A8, correcting the revision 35 sentence), `hide_parameters=True` on both engines and the failing-row detail dropped (A9). Open [Proposed] items: none.
- Date: 2026-10-24 (revision 36: Decision 18 amended)
- Task: P2-00a (implemented by P2-01, P2-04, P2-05 to P2-11b, P2-16, P2-18, P2-20)

## Context
`tracer-evalschema` 0.0.1 is frozen at the Phase 1 gate: 26 entities, 70 value objects (ADR 0001 Decision 8), JSON Schema files and `schema.lock`. Phase 2 adds `tracer-evaldb`, which stores and reloads every entity on SQLite (CLI and CI) and Postgres (server) with the same observable behaviour. The models stay the source of truth: the database must not become a second, weaker definition of the data.

Facts in the code that drive the design (checked in `packages/evalschema/src/tracer_evalschema`):
- `VerbatimText` accepts U+0000 and rejects only lone surrogates (`text.py`); `HumanLabel`, `MachineKey`, `MetricKey`, the language tag types and every closed set reject control characters. Postgres `text` cannot hold U+0000; JSON text written by `model_dump_json()` escapes it as `\u0000`.
- `EntityBase.metadata` is an I-JSON object that may hold any finite float (`1e300`), and floats must come back with the same JSON spelling.
- Only `Run` has `project_id` and only `UsageRecord` has `run_id` (N22). The other records of a run belong to it through its `RunBundle`.
- A `RunBundle` may hold turns whose transcript it declares withheld (`bundle._check_references` resolves a turn's `transcript_id` through the bundle's records or its `withheld` list; `test_bundle_applicability_counts.py` keeps the turns of a withheld transcript).
- `RunBundle` holds `pricing_catalogs` and `test_profiles` (versioned entities) but no `AgentTarget`; `Run.agent_target` is a reference that may not resolve.
- `VersionedEntity` verifies its own `content_digest` on load without a key resolver, so a stored versioned entity (and a `ConfigSnapshot`) with a keyed own digest cannot be validated at this layer (`DigestKeyUnavailable`).
- `Version` (`base.py`) and the `*_ms` fields are unbounded integers in the models (ADR 0002 Decision 2 adds no `maximum`); counts and indexes are bounded by `MAX_SAFE_INTEGER`.
- `loader.loads_strict` rejects duplicate keys and depth over 256; `loader.load_bundle` checks the 256 MiB limit before parsing.

## Decision
1. **Package and API (Q2, Q3).** `tracer-evaldb` (import `tracer_evaldb`, Apache-2.0) maps the entities to SQLAlchemy Core `Table` objects in one `MetaData`, with explicit typed codecs per entity, and exposes a synchronous API. The Postgres driver is pg8000 (extra `[postgres]`). Runtime dependencies are bounded ranges, each tested at its lower bound on a CI leg: `sqlalchemy>=2.1.6,<3`, `alembic>=1.20.1,<2`, `pg8000>=1.31.5,<2`; `tracer-evalschema==0.0.1` stays exact because it is the schema-version pin of the stored data (ADR 0021). evaldb imports evalschema; evalschema never imports evaldb. All hashing goes through the public helpers of `tracer_evalschema.digest` (`jcs_bytes`, `sha256_hex`), so the rule "only `digest` hashes" holds for the stored data too.
2. **Storage shape (Q1, Q25).** Every entity row holds the authoritative document plus projections:
   - `doc` (`DocCol`, text on both backends) is the exact text of `model_dump_json()` of the validated model. It is what the strict loader reads back. No JSON column type is used on either backend.
   - `doc_sha256` (`Sha256Col`) is the SHA-256 of the RFC 8785 (JCS) bytes of the JSON-mode dump (`digest.jcs_bytes`), the content identity of the whole document, including the fields outside the digest domain (`schema_version`, `content_digest`, null members).
   - Projections are columns copied from the model for keys, foreign keys, closed sets, filters and indexes. A projection is never the source of truth: the codec computes it from the model on write and recomputes and compares it on read (Decision 6).
   - Decoding a row: the byte length of `doc` is checked against `loader.DEFAULT_MAX_BUNDLE_BYTES` (256 MiB) before parsing (Q23, ADR 0022); `doc` goes through `loader.load_model` (strict parsing, duplicate keys and depth), then model validation; the recomputed `doc_sha256` and projections must equal the row. Errors (revision 35, A9): a versioned entity or a `ConfigSnapshot` whose own digest does not verify raises `tracer_evalschema.errors.DigestMismatch`, unwrapped; anything else wrong with the text of `doc` (not JSON, a repeated key, depth, any model error, the schema-version errors included) is `StoreIntegrityError(table, column="doc")`; a `doc_sha256` that differs from the validated model's is `StoreIntegrityError(table, column="doc_sha256")`; any other column that differs from what the document says is `StoreIntegrityError(table, column=<that column>)`. A `StoreIntegrityError` names the table, the column and the row's ID and nothing else, and is raised `from None`, so no parser or model message that could quote the document is chained. The storage-only `run_id` of the ten tables of Decision 8 is not in the document and cannot be re-checked there.
3. **One table per entity (Table 1).** The 26 entities of the prefix registry (ADR 0001 Decision 4) map to 26 tables named in snake case. The wire prefix is never stored; the table implies it (ADR 0001 Consequences).
   - **Common columns of the 26 entity tables:** `id` (`UuidCol`); `schema_version` (`KeyCol`, CHECK `= '0.0.1'`); `created_at` (`InstantCol`); `doc`; `doc_sha256`.
   - **Versioned tables** (the nine of ADR 0009 Decision 5) add `version` (`BigIntCol`, CHECK `>= 1`), with primary key `(id, version)`, and `content_digest_scheme` (`KeyCol`, CHECK in the shipped `ContentDigest.scheme` enum) and `content_digest_value` (`Sha256Col`).
   - **Records of fact** have primary key `id`. `config_snapshot` also projects its own `content_digest_scheme` and `content_digest_value` (lookup by digest, P2-12a).
   - `metadata` and `metadata_flags` are never projected.
4. **Side tables (Table 2).** Eleven side tables hold ordered membership, lookup keys, the deletion audit and the store's version record. They carry no `doc`, `doc_sha256`, `schema_version` or `created_at`: each row is derived from an entity row that carries them, except `deletion_audit` and `tracer_meta`, whose shape is defined by the store's migrations (ADR 0020, ADR 0021). Alembic's own `alembic_version` table is the only other table in a store database; it is not part of `MetaData` (Ambiguity 9).
5. **Types per backend (Table 3).** Eight logical column types, each with one Postgres type and one SQLite STRICT type. No SQLAlchemy `DateTime`, `JSON`, `Float` or `Numeric` with a scale exists; no float ever stores money or a projected value; `Transcript.language_confidence` and every metadata float stay in `doc`.
   - **UUIDs on SQLite (Q18):** `BLOB` of exactly 16 bytes in RFC 9562 byte order, so byte order is UUIDv7 time order, the same order as Postgres `uuid` comparison. A CHECK on both backends requires version nibble 7 (revision 35, A17: the variant bits are not checked; a variant CHECK would be an additive constraint later).
   - **SQLite tables (Q19):** every table is `STRICT` (SQLAlchemy `sqlite_strict`) and `WITHOUT ROWID`, so a declared `TEXT` column never coerces a decimal string by NUMERIC affinity. SQLite 3.37 or later is required and checked.
   - **Text comparison:** every `KeyCol` is `COLLATE "C"` on Postgres and the default `BINARY` collation on SQLite. Both compare the UTF-8 bytes, which is code point order: case variants, NFC and NFD forms and every Indic sequence (ZWJ, ZWNJ, nukta forms, chillu forms) stay distinct and order the same on both backends. No ordering is language-sensitive and no driver or database normalizes text.
6. **Projection rules.**
   - Only values of types that reject U+0000 are projected: UUIDs, instants, decimals, integers, booleans, digests, closed-set members, `MachineKey`, `MetricKey`, `HumanLabel` and canonical language tags. `VerbatimText` (turn text, segment text, tool arguments, raw labels, `ExternalRef` parts, `CallerExternalId.value`, consent references) is never projected, so no projection meets the Postgres NUL limit (Q24).
   - A projected column carries the name of the model field it copies; a field of an embedded value object is named by joining the path with `_` (`agent_target.version` becomes `agent_target_version`), except the privacy columns of Decision 6a.
   - **6a. Privacy columns.** A `PrivacyMarker` is projected as `human_subject`, `human_subject_reason`, `consent_status` (from `consent.status`), `retain_until`, `delete_on_expiry` and `policy_ref` (from `retention`) on `artifact`, `transcript` and `dataset_item`. On `run` the marker is the one of `caller_external_id`, and its columns carry the prefix `caller_` (`caller_human_subject`, ...), because a Run has no marker of its own. A CHECK states the P-46 presence rule on the columns: when `human_subject` is true, `consent_status` and `delete_on_expiry` are present; when it is false, `human_subject_reason`, `consent_status`, `retain_until`, `delete_on_expiry` and `policy_ref` are all NULL; when the marker is absent (`dataset_item.privacy` and `run.caller_external_id` are optional), all six are NULL.
   - **Tri-state values (Q30).** A projected optional field is a nullable column, and NULL means absent or unknown, never 0 or false. Booleans are nullable; on SQLite an `INTEGER` with CHECK `IN (0, 1)`. A `not_applicable` MetricResult keeps `passed` NULL and a `not_available` CostRecord keeps `amount`, `basis` and `currency` NULL; CHECKs state both (Decision 12).
   - **Integers.** Projected integers are `bigint` (64-bit signed). The models bound counts and indexes by `MAX_SAFE_INTEGER`, but `version` and the `*_ms` fields are unbounded. Every stored document is hashed through `digest.jcs_bytes`, which refuses integers outside the I-JSON range, so the real ceiling of a stored integer is `2**53 - 1` (revision 35, A2): encoding refuses a larger magnitude with `StoreValueOutOfRange(table, column)` (a `StoreError` and a `ValueError`, naming the table and the column, never the number), with `column="doc"` for an integer outside any projected column. `BigIntCol` stays 64-bit, which is wider than any storable value (Ambiguity 5).
7. **References to versioned entities (Q9).** `Run.agent_target`, `MetricBinding.metric`, `MetricResult.metric`, the evaluator's persona and test-profile references, the snapshot's pricing-catalog reference, `snapshot_member` and `snapshot_profile_ref` rows, `dataset_member.item_id` and the versioned members of `run_bundle_member` have no foreign key. They are projected and indexed, and a resolver reports a reference that names no stored row as unresolved (ADR 0009 Consequences). A foreign key would demand rows that a RunBundle does not carry (no `AgentTarget`) and would turn deleting a definition into deleting every run that cites it.
8. **Run ownership (Q4).** Every run-owned table (`artifact`, `transcript`, `turn`, `exchange`, `tool_call_event`, `stage_span`, `metric_result`, `run_verdict`, `usage_record`, `cost_record`, `cost_summary`) has a `run_id` column with a foreign key to `run` that cascades. For `usage_record` it is the native field; for the others it is storage-only, taken from the RunBundle being stored (a fact about the write, not an inference), and it never enters `doc`. A record is owned by exactly one run; storing a record that is already owned by another run is refused (ADR 0022 Decision 5).
9. **Definitions embedded in snapshots (Q8).** Storing a `ConfigSnapshot` also stores each embedded Evaluator, Persona, MetricDefinition, MetricBinding and Rubric in its own table: inserted when absent, compared byte for byte (`doc_sha256`) when present, a typed conflict when different (ADR 0022). The same holds for the `pricing_catalogs` and `test_profiles` of a RunBundle. A snapshot never depends on those rows: its `doc` embeds them, and `snapshot_member` records which `(kind, id, version)` it embeds, in order, for queries such as "runs that used evaluator X".
10. **ExportBundle at rest (Q7).** An export is stored as one opaque document in `export_bundle.doc` (the `model_dump_json()` of the validated export, so sealed ciphertext stays byte-identical), with the projections `purpose` and `byte_length` and the link tables `export_bundle_run`, `export_bundle_item` and `export_bundle_profile`. No record of an export enters the internal tables: export records are transformed copies (digests keyed or dropped, `declared_by` dropped, turns sealed) and collide by ID with internal records. The link tables exist so that deletion reaches every stored copy (ADR 0020).
11. **RunBundle at rest.** `run_bundle.doc` holds only the envelope: `id`, `schema_version`, `created_at`, `metadata` and `metadata_flags`, in the order `model_dump_json()` writes them. The members are rows of `run_bundle_member` (one per record or versioned entity, by list and position) and `run_bundle_withheld` (the `withheld` list, in order). Loading rebuilds the bundle from the envelope and the member rows in member order, validates it as a `RunBundle` and compares its `model_dump_json()` with the bytes that were stored (ADR 0022 Decision 7).
12. **What the database enforces and what the models enforce.** The database is not the validation boundary; the repository is. Every write validates through the models first, and every read validates again.
    - **Enforced by the database** (both backends): primary keys and `(id, version)` uniqueness; foreign-key presence for the edges of ADR 0020; no UPDATE on any table (triggers, Q14, ADR 0020); `version >= 1`; the `schema_version` pin `= '0.0.1'`; the projected closed sets, equal to the `enum` lists of the shipped `schemas/*.schema.json`; `currency = 'USD'`; `cost_record.amount >= 0` and `cost_summary.total >= 0` (Postgres numeric comparison, SQLite `NOT GLOB '-*'`); UUIDv7 version nibble; UUID and digest byte lengths on SQLite; the instant and decimal text shapes on SQLite; `(transcript_id, seq)` uniqueness; the privacy presence rule (Decision 6a); `not_applicable` implies `passed IS NULL`; `not_available` implies `amount`, `basis` and `currency` NULL; `currency` present if and only if the amount is; the paired reference columns present together (`test_profile_id` with `test_profile_version`, `pricing_catalog_id` with `pricing_catalog_version`); `record_version` present exactly for the versioned member kinds of `run_bundle_member`; the closed sets of the side tables (`snapshot_member.kind`, `external_ref_index.entity_kind`, `run_bundle_member.kind`, `run_bundle_withheld.kind`, and, revision 35, A10, `deletion_audit.reason` and `deletion_audit.root_kind` with the sets of ADR 0020 Decisions 6 and 2); the `tracer_meta` singleton; NOT NULL on every column except those of the nullability table under Table 1.
    - **Enforced by the models and the repository only:** digests and HMAC; every cross-record rule of `check_run_bundle` and `check_export_bundle`, including applicability; I-JSON; the text, bidi and credential rules; hiding input in errors; one result per metric per bundle (rescoring shares a run, so this cannot be a database constraint); contiguity of indexes and seqs; agreement between projections and `doc` (checked by the codec on every read and by `verify_database`, P2-26); the language rules (a language column has no CHECK; its value is the canonical tag the model returned).
    - Raw SQL can still INSERT rows the models would reject. STORAGE_POLICY states this, and `verify_database` finds them.
13. **Parity (Table 4).** SQLite and Postgres give the same observable results for every row of Table 4; each row names its test.
14. **Value objects (Table 5).** Each of the 70 value objects of ADR 0001 Decision 8 is classified as `doc only`, `projected as <columns>` or `side table <name>`. The list was derived from `registry.value_object_classes()` and a walk of every entity's fields, not from memory.
15. **Indexes (P2-20).** Every listed access pattern is served by an index on both backends: runs by `project_id` ordered by `id` (UUIDv7 keyset), by `(agent_target_id, agent_target_version)`, `mode`, `observed_tier`, `started_at` and `config_snapshot_id`; `snapshot_member` by `(kind, def_id, def_version)`; `snapshot_profile_ref` by `(profile_id, profile_version)`; `config_snapshot` by `content_digest_value`; artifacts by `run_id` and by `sha256`; transcripts by `run_id`; turns by `(transcript_id, seq)` (the unique constraint) and by `run_id`; results by `run_id` and by `(metric_id, metric_version, status)`; verdicts by `status`; usage, cost records and cost summaries by `run_id`; spans, tool calls and exchanges by `run_id`; definitions by `(id, version DESC)`; metric definitions by `key`; `dataset_member` by `item_id`; `run_bundle` by `run_id`; `run_bundle_member` by `(kind, record_id)`; the three export link tables by their second column; `external_ref_index` as in ADR 0022; and a partial index on `retain_until` (`caller_retain_until` on `run`) where it is not NULL (ADR 0020).
16. **No text search in Phase 2 (Q24).** No full-text or trigram index and no projection of free text. A Phase 7 search design must decide how text holding U+0000 is indexed on Postgres.
17. **Definitions are not owned by a project (Q26).** No ownership is invented: the definition tables have no `project_id`. Finding **P2-F1** proposes an additive `project_id` on definitions for 0.0.2, decided before Phase 7.
18. **Errors and logs hide input.** Both engines, SQLite and Postgres, are created by `create_store_engine` with `hide_parameters=True` and `echo=False` (revision 36, batch C A9: batch A did not set `hide_parameters`; test `test_engine_hides_bound_parameters_in_errors`). A database refusal of a repository write is re-raised as the typed store error `StoreConstraintViolation(kind, table, constraint)`, which names the kind, the table and the constraint and never a bound value, a document fragment or the database URL (`create_store_engine` never echoes the URL, P2-04; tests `test_translate_integrity_error_names_the_kind_and_no_value`, `test_the_repository_raises_a_typed_error_when_the_database_refuses_a_write`).
    - **Error mapping (revision 35, A12; corrected in revision 36, batch C A8).** pg8000 raises every server error as `ProgrammingError`, constraint violations included. `create_store_engine` therefore registers a `handle_error` hook on the Postgres engine that turns any driver error whose SQLSTATE is in class 23 (integrity constraint violation: `23000`, `23001`, `23502`, `23503`, `23505`, `23514`, `23P01`) into `sqlalchemy.exc.IntegrityError`; errors of every other class, and exceptions that are not database errors, are left as they are. The hook maps the whole class, not a list of codes, because the code of one violation differs between server versions: a delete refused by `ON DELETE RESTRICT` is `23503` (foreign_key_violation) on Postgres 15.19 and `23001` (restrict_violation) on Postgres 18.6. The revision 35 text said that pg8000 reports `23001` for `RESTRICT`; that holds on 18.6 only. The immutability triggers raise `23001` on Postgres (ADR 0020 Decision 7). SQLite needs no hook: its driver raises `IntegrityError` natively. The repository maps an `IntegrityError` to the kinds check (`23514`), unique (`23505`), foreign key (`23503` and `23001`) and not null (`23502`) on Postgres, and by the constraint message prefix on SQLite. Tests: `test_sqlstate_helpers`, `test_constraint_violations_are_integrity_errors_on_both_backends` (accepts `23503` or `23001` for `RESTRICT`), `test_errors_of_other_classes_are_not_integrity_errors`.
    - **No failing-row detail (revision 36, batch C A9).** Postgres puts the failing row into the detail field of an integrity error ("Failing row contains (...)", "Key (id)=(...)"), which would carry document text into exceptions and logs. The hook builds the error it passes on from the driver's error with only the severity, SQLSTATE, message, schema, table, column, data type and constraint fields, and drops the detail, hint and context, so `str`, `repr` and `args` of the translated error hold no value. Test: `test_translated_integrity_errors_carry_no_row_content`.

### Table 1: entity tables
Machine-read by `test_adr0019_entity_table_matches_metadata`: one row per entity; the Table and Key columns are exact, and the Projected columns cell lists every column of the table besides the common columns of Decision 3.

| Entity | Table | Key | Projected columns | Side tables | FKs (on delete) |
|---|---|---|---|---|---|
| AgentTarget | `agent_target` | `(id, version)` | `transport` | none | none |
| Persona | `persona` | `(id, version)` | `language` | none | none |
| TestProfile | `test_profile` | `(id, version)` | none (no profile field is projected) | none | none |
| Evaluator | `evaluator` | `(id, version)` | `persona_id`, `persona_version`, `test_profile_id`, `test_profile_version` | none | none (references, Decision 7) |
| MetricDefinition | `metric_definition` | `(id, version)` | `key`, `kind`, `value_type` | none | none |
| MetricBinding | `metric_binding` | `(id, version)` | `evaluator_id`, `metric_id`, `metric_version` | none | none (references) |
| Rubric | `rubric` | `(id, version)` | none | none | none |
| PricingCatalog | `pricing_catalog` | `(id, version)` | none (price entries stay in `doc`; cost resolution reads the whole catalog) | none | none |
| Dataset | `dataset` | `(id, version)` | none | `dataset_member` | none |
| Project | `project` | `id` | `name` | none | none |
| ConfigSnapshot | `config_snapshot` | `id` | `content_digest_scheme`, `content_digest_value`, `evaluator_id`, `evaluator_version`, `rubric_id`, `rubric_version`, `pricing_catalog_id`, `pricing_catalog_version` | `snapshot_member`, `snapshot_profile_ref` | none to definitions |
| Run | `run` | `id` | `project_id`, `config_snapshot_id`, `agent_target_id`, `agent_target_version`, `mode`, `caller_origin`, `observed_tier`, `started_at`, `caller_human_subject`, `caller_human_subject_reason`, `caller_consent_status`, `caller_retain_until`, `caller_delete_on_expiry`, `caller_policy_ref` | `external_ref_index` | `project_id` CASCADE; `config_snapshot_id` RESTRICT |
| Artifact | `artifact` | `id` | `run_id`, `sha256`, `turn_ref`, `human_subject`, `human_subject_reason`, `consent_status`, `retain_until`, `delete_on_expiry`, `policy_ref` | none | `run_id` CASCADE |
| Transcript | `transcript` | `id` | `run_id`, `source`, `detected_language`, `human_subject`, `human_subject_reason`, `consent_status`, `retain_until`, `delete_on_expiry`, `policy_ref` | none | `run_id` CASCADE |
| Turn | `turn` | `id` | `run_id`, `transcript_id`, `seq`, `role`, `start_ms` | none | `run_id` CASCADE; UNIQUE `(transcript_id, seq)`; no FK on `transcript_id` (Ambiguity 1) |
| Exchange | `exchange` | `id` | `run_id`, `index` | none | `run_id` CASCADE |
| ToolCallEvent | `tool_call_event` | `id` | `run_id`, `source` | none | `run_id` CASCADE |
| StageSpan | `stage_span` | `id` | `run_id`, `stage` | none | `run_id` CASCADE |
| MetricResult | `metric_result` | `id` | `run_id`, `metric_id`, `metric_version`, `status`, `na_reason`, `passed` | none | `run_id` CASCADE |
| RunVerdict | `run_verdict` | `id` | `run_id`, `status`, `required_total`, `passed`, `failed`, `not_applicable`, `errored` | none | `run_id` CASCADE |
| UsageRecord | `usage_record` | `id` | `run_id`, `domain`, `source`, `occurred_at` | none | `run_id` CASCADE (native field) |
| CostRecord | `cost_record` | `id` | `run_id`, `status`, `basis`, `amount`, `currency`, `na_reason` | none | `run_id` CASCADE |
| CostSummary | `cost_summary` | `id` | `run_id`, `completeness`, `total`, `currency` | none | `run_id` CASCADE |
| DatasetItem | `dataset_item` | `id` | `artifact_id`, `transcript_id`, `source_run_id`, `language`, `human_subject`, `human_subject_reason`, `consent_status`, `retain_until`, `delete_on_expiry`, `policy_ref` | none | none on the three references; application-level cascade (ADR 0020) |
| RunBundle | `run_bundle` | `id` | `run_id` | `run_bundle_member`, `run_bundle_withheld` | `run_id` CASCADE |
| ExportBundle | `export_bundle` | `id` | `purpose`, `byte_length` | `export_bundle_run`, `export_bundle_item`, `export_bundle_profile` | none; application-level cascades (ADR 0020) |

Column types: IDs, every `*_id` projection and `artifact.turn_ref` (a Turn ID; revision 35, A3) are `UuidCol`; `version` and every `*_version`, `seq`, `index`, `start_ms`, `byte_length` and the five verdict counts are `BigIntCol`; `created_at`, `started_at`, `occurred_at` and `retain_until` are `InstantCol`; `amount` and `total` are `DecimalCol`; `sha256` and `content_digest_value` are `Sha256Col`; `passed` on `metric_result`, `human_subject` and `delete_on_expiry` (with their `caller_` forms) are `BoolCol`; every other projected column is `KeyCol`. Closed-set CHECKs: `transport`, `kind`, `value_type`, `mode`, `caller_origin`, `observed_tier`, `source` (transcript, tool call, usage), `role`, `stage`, `status` (result, verdict, cost record), `na_reason` (result, cost record), `domain`, `basis`, `completeness`, `currency`, `purpose`, `consent_status`, `human_subject_reason` and `content_digest_scheme`, each equal to the `enum` of the shipped schema file.

Nullability (revision 35, A5; contract `docs/phase2/api-surface-batch-b.md` section 3.2): every column is NOT NULL except the columns below, which are exactly the projections of optional model fields. `human_subject` is NOT NULL on `artifact` and `transcript`, whose marker is required, and nullable on `dataset_item` and `run`, whose marker is optional (absent marker: all six privacy columns NULL, Decision 6a).

| Table | Nullable columns |
|---|---|
| `evaluator` | `test_profile_id`, `test_profile_version` |
| `config_snapshot` | `pricing_catalog_id`, `pricing_catalog_version` |
| `run` | `started_at`, the six `caller_` privacy columns |
| `artifact` | `turn_ref`, the privacy columns except `human_subject` |
| `transcript` | `detected_language`, the privacy columns except `human_subject` |
| `turn` | `start_ms` |
| `metric_result` | `na_reason`, `passed` |
| `usage_record` | `occurred_at` |
| `cost_record` | `amount`, `basis`, `currency`, `na_reason` |
| `cost_summary` | `total`, `currency` |
| `dataset_item` | `artifact_id`, `transcript_id`, `source_run_id`, the six privacy columns |
| `run_bundle_member` | `record_version` |

### Table 2: side tables
Machine-read by `test_adr0019_entity_table_matches_metadata` together with Table 1 (37 tables in `MetaData`, no others).

| Table | Columns | Key | Constraints and indexes | Holds |
|---|---|---|---|---|
| `snapshot_member` | `snapshot_id`, `kind`, `ordinal`, `def_id`, `def_version` | `(snapshot_id, kind, ordinal)` | `kind` IN (`evaluator`, `persona`, `rubric`, `metric_definition`, `metric_binding`); `ordinal >= 0`; `def_version >= 1`; index `(kind, def_id, def_version)` | the definitions a snapshot embeds, in list order (`ordinal` 0 for the three single members) |
| `snapshot_profile_ref` | `snapshot_id`, `profile_id`, `profile_version` | `(snapshot_id)` | `profile_version >= 1`; index `(profile_id, profile_version)` | the snapshot's `evaluator.test_profile` reference (id and version only, never the digest) |
| `external_ref_index` | `entity_kind`, `entity_id`, `key_sha256`, `run_id` | `(entity_kind, entity_id, key_sha256)` | `entity_kind` IN (`run`, `span`, `transcript`, `usage_record`); unique `(entity_kind, key_sha256)` where `entity_kind` = `run` only (revision 35, A1; `span`, `transcript` and `usage_record` keys repeat and are looked up per run); index `(run_id, entity_kind, key_sha256)` | P-02 re-import keys (ADR 0022) |
| `dataset_member` | `dataset_id`, `dataset_version`, `ordinal`, `item_id` | `(dataset_id, dataset_version, ordinal)` | `ordinal >= 0`; index `item_id`; no FK on `item_id` | `Dataset.item_ids` in order |
| `run_bundle_member` | `bundle_id`, `kind`, `ordinal`, `record_id`, `record_version` | `(bundle_id, kind, ordinal)` | `kind` IN (`run`, `snapshot`, `pricing_catalog`, `test_profile`, `artifact`, `transcript`, `turn`, `exchange`, `tool_call`, `span`, `result`, `verdict`, `usage_record`, `cost_record`, `cost_summary`); `record_version` NOT NULL exactly when `kind` IN (`pricing_catalog`, `test_profile`); index `(kind, record_id)` | every member of a RunBundle, by list and position |
| `run_bundle_withheld` | `bundle_id`, `ordinal`, `kind`, `record_id` | `(bundle_id, ordinal)` | `kind` IN the shipped `WithheldRecord.kind` enum | `RunBundle.withheld` in order |
| `export_bundle_run` | `export_id`, `run_id` | `(export_id, run_id)` | index `run_id`; no FK on `run_id` | the runs whose bundles an export embeds |
| `export_bundle_item` | `export_id`, `item_id` | `(export_id, item_id)` | index `item_id`; no FK on `item_id` | the dataset items an export embeds |
| `export_bundle_profile` | `export_id`, `profile_id` | `(export_id, profile_id)` | index `profile_id`; no FK on `profile_id` | the test profiles an export carries, in the clear (`RunBundle.test_profiles`) or sealed (`aad_fields.subject_id` of a `test_profile` payload) |
| `deletion_audit` | `audit_uuid`, `deleted_at`, `actor`, `reason`, `root_kind`, `counts` | `(audit_uuid)` | ADR 0020 Decision 6; `reason` IN the three reasons of ADR 0020 Decision 6 and `root_kind` IN the fourteen root kinds of ADR 0020 Decision 2 (revision 35, A10) | one row per deletion, no content |
| `tracer_meta` | `singleton`, `schema_version`, `evaldb_version` | `(singleton)` | `singleton = 1`; `schema_version = '0.0.1'` | the store's schema version (ADR 0021) |

Member kinds use the spellings of `bundle.WITHHOLDABLE_KINDS` (`tool_call`, `span`, `result`) plus `run`, `snapshot`, `pricing_catalog`, `test_profile`, `verdict` and `cost_summary`. Side-table IDs and `audit_uuid` are `UuidCol` (the kind column implies the prefix), ordinals, versions and `tracer_meta.singleton` (CHECK `= 1`; revision 35, A4) `BigIntCol`, `key_sha256` `Sha256Col`, `deleted_at` `InstantCol`, `counts` `DocCol`, every other column `KeyCol`.

### Table 3: logical column types
Machine-read by `test_storage_policy_parity_table_matches_column_types` through STORAGE_POLICY, which copies it (P2-25).

| Logical type | Python value | Postgres | SQLite (STRICT) | CHECK | Tests |
|---|---|---|---|---|---|
| `UuidCol` | `uuid.UUID` (the `.uuid` of a typed ID, no prefix) | `uuid` | `BLOB`, 16 bytes | version nibble 7 (variant bits not checked, A17); SQLite length 16 | `test_uuid_column_native_on_postgres_blob16_on_sqlite`, `test_uuid_v7_check_rejects_other_versions` |
| `InstantCol` | aware `datetime` in UTC | `timestamptz` (session `TimeZone=UTC`) | `TEXT` `YYYY-MM-DDTHH:MM:SS.ffffffZ` (ADR 0002) | SQLite: length 27 and the GLOB of that shape | `test_instant_round_trip_edges`, `test_instant_round_trip_under_non_utc_server_timezone`, `test_sqlite_instant_text_sorts_chronologically`, `test_no_sqlalchemy_datetime_columns` |
| `DecimalCol` | canonical `DecimalString` text | `numeric` without precision or scale | `TEXT` | SQLite: the GLOB of the canonical decimal; sign rules per column | `test_decimal_round_trip_exact`, `test_sqlite_decimal_stored_as_text`, `test_cost_amount_non_negative_check_both_backends` |
| `DocCol` | `str` | `text` | `TEXT` | none | `test_db_doc_column_is_text_not_json` |
| `Sha256Col` | 32 `bytes` | `bytea` | `BLOB` | length 32 | `test_db_digest_preserved_on_round_trip` |
| `KeyCol` | `str` free of U+0000 (key, label, tag, closed-set member) | `text COLLATE "C"` | `TEXT` (`BINARY`) | closed sets as `IN (...)` | `test_key_column_order_parity`, `test_case_distinct_keys_distinct_on_both_backends`, `test_nfc_nfd_distinct_on_both_backends`, `test_closed_set_checks_equal_shipped_schema_enums` |
| `BigIntCol` | `int` in the signed 64-bit range | `bigint` | `INTEGER` | per column (`>= 0`, `>= 1`) | `test_version_check_ge_1` |
| `BoolCol` | `True`, `False` or `None` | `boolean`, nullable | `INTEGER`, nullable | SQLite: `IN (0, 1)` | `test_bool_tristate_null_never_false` |

### Table 4: parity
| Concern | Postgres | SQLite | Same observable result | Test |
|---|---|---|---|---|
| UUID storage and order | native `uuid`, compared by bytes | 16-byte `BLOB`, compared by bytes | equal values, UUIDv7 time order | `test_uuid_column_native_on_postgres_blob16_on_sqlite`, `test_keyset_pagination_runs_by_project_is_time_ordered` |
| Instants | `timestamptz`, session `TimeZone=UTC` | fixed-width UTC text | years 0001 to 9999 round-trip; text order is time order | `test_instant_round_trip_edges`, `test_instant_round_trip_under_non_utc_server_timezone`, `test_sqlite_instant_text_sorts_chronologically` |
| Decimals | unconstrained `numeric` | text | exact round trip (18 fractional digits, 30 integer digits, negative, `0`); no SQL arithmetic, sum or order on amounts on either backend | `test_decimal_round_trip_exact`, `test_sqlite_decimal_stored_as_text` |
| Non-negative amounts | `amount >= 0` | `amount NOT GLOB '-*'` | negative amounts refused | `test_cost_amount_non_negative_check_both_backends` |
| Text comparison | `COLLATE "C"` | `BINARY` | code point order; case and normalization forms distinct | `test_key_column_order_parity`, `test_case_distinct_keys_distinct_on_both_backends`, `test_nfc_nfd_distinct_on_both_backends` |
| U+0000 | `text` cannot hold it | `TEXT` can | `doc` holds the JSON escape; projected types reject it | `test_db_nul_in_verbatim_text_round_trips` |
| Indic text | UTF8 database | UTF-8 | byte-exact, no normalization | `test_indic_text_round_trip_byte_exact`, `test_indic_text_no_normalization_by_driver` |
| Encoding | `server_encoding` must be `UTF8`, else refused | always UTF-8 | one encoding | `test_postgres_non_utf8_database_refused` |
| Booleans | `boolean` NULL | `INTEGER` NULL, `IN (0, 1)` | NULL is never false | `test_bool_tristate_null_never_false` |
| Foreign keys | always on | `PRAGMA foreign_keys=ON` on every connection | orphans refused | `test_sqlite_connections_enforce_foreign_keys`, `test_sqlite_orphan_insert_rejected` |
| Transactions | native | `isolation_level=None` plus an explicit `BEGIN` (Python 3.11 has no `autocommit` attribute) | rollback is real | `test_sqlite_rollback_is_real_on_every_python` |
| Type strictness | typed columns | `STRICT`, `WITHOUT ROWID` | no affinity coercion | `test_sqlite_tables_are_strict_without_rowid` |
| Immutability | `BEFORE UPDATE` trigger calling a function that raises | `BEFORE UPDATE` trigger with `RAISE(ABORT, ...)` | every UPDATE refused | `test_update_rejected_on_every_table` |
| Cascades | `ON DELETE CASCADE` / `RESTRICT` | the same, with foreign keys on | equal counts | `test_cascade_counts_parity_sqlite_postgres` |
| Partial and unique indexes | supported | supported | same constraints, plans use them | `test_postgres_plans_can_use_indexes`, `test_sqlite_query_plans_use_indexes` |
| Minimum versions | 15 | 3.37 | tested on every leg | `test_sqlite_version_at_least_3_37`, `test_ci_postgres_job_matrix_and_pinned_image` |

### Table 5: the 70 value objects
Machine-read by `test_adr0019_every_value_object_classified`: one row per class of `registry.value_object_classes()`; the Classification cell starts with `doc only`, `projected as` or `side table`.

| # | Value object | Classification | Where it occurs and what stays in `doc` |
|---|---|---|---|
| 1 | `AgentTargetRef` | projected as `run.agent_target_id`, `run.agent_target_version` | `Run.agent_target`; `content_digest` stays in `doc` |
| 2 | `AppliesWhenCondition` | doc only | inside `PricingCatalog.entries[]`; cost resolution reads the whole catalog |
| 3 | `AudioFormat` | doc only | `Artifact.audio_format` |
| 4 | `BidiFlaggedText` | doc only | no entity field holds it in 0.0.1; nothing to store beyond `doc` |
| 5 | `BillingRules` | doc only | inside `PricingCatalog.entries[]` |
| 6 | `CallerExternalId` | projected as `run.caller_human_subject`, `run.caller_human_subject_reason`, `run.caller_consent_status`, `run.caller_retain_until`, `run.caller_delete_on_expiry`, `run.caller_policy_ref` | only its `privacy` marker is projected; `value` (the transport identifier) is never projected |
| 7 | `ChannelAssignment` | doc only | inside `Run.channel_maps[]` |
| 8 | `ChannelMap` | doc only | `Run.channel_maps`; its `artifact_id` is checked by the bundle rules, not by an FK |
| 9 | `ConsentRecord` | projected as `consent_status` (`artifact`, `transcript`, `dataset_item`) and `run.caller_consent_status` | `reference` and `recorded_at` stay in `doc` |
| 10 | `ContentDigest` | projected as `content_digest_scheme`, `content_digest_value` | the entity's own digest on the 9 versioned tables and `config_snapshot`; every other digest (references, pricing snapshot, sealed plaintext) stays in `doc` |
| 11 | `CostExclusion` | doc only | `CostSummary.excluded[]` |
| 12 | `CostRounding` | doc only | `CostRecord.rounding` |
| 13 | `DeclaredStackEntry` | doc only | `AgentTarget.declared_stack[]` |
| 14 | `DekWrap` | doc only | inside an `ExportBundle` document only (opaque, Decision 10) |
| 15 | `EnumScale` | doc only | `MetricDefinition.scale` |
| 16 | `EqualsTrueRule` | doc only | `MetricDefinition.pass_rule`, `aggregation.turn_rule` |
| 17 | `EvidenceRef` | doc only | `MetricResult.evidence_refs[]`; resolution is a bundle rule |
| 18 | `ExpectedToolCall` | doc only | `Evaluator.expected_tool_calls[]` |
| 19 | `ExternalRef` | side table `external_ref_index` | `Run.external_refs[]`, `StageSpan.span_ref`, `Transcript.provider_call_ref`, `UsageRecord.provider_call_ref`, as `key_sha256` only (ADR 0022); `TimelineOrigin.ref` stays in `doc` |
| 20 | `IngestWarning` | doc only | `Run.ingest_warnings[]` |
| 21 | `JudgeInfo` | doc only | `MetricResult.judge` |
| 22 | `JudgeTurnVerdict` | doc only | `MetricResult.judge.per_turn[]` |
| 23 | `KeyRef` | doc only | names a key, never holds one; never projected (ADR 0007) |
| 24 | `LanguageDeclaration` | doc only | `Transcript.declared_language` (a change is a new Transcript, ADR 0022) |
| 25 | `LanguageLabelWarning` | doc only | no entity field holds it in 0.0.1 (a helper result) |
| 26 | `LiteralProfileValue` | doc only | no entity field holds it in 0.0.1 (a lint result) |
| 27 | `MetadataFlag` | doc only | `metadata_flags` of every entity; `metadata` is never projected |
| 28 | `MetricBindingRef` | doc only | `Evaluator.metric_bindings[]`, `RubricItem.binding`; a run's bindings are found through `snapshot_member` |
| 29 | `MetricDefinitionRef` | projected as `metric_binding.metric_id`, `metric_binding.metric_version`, `metric_result.metric_id`, `metric_result.metric_version` | `VerdictCheck.metric_ref` stays in `doc` |
| 30 | `MetricParameter` | doc only | `MetricDefinition.parameters[]` |
| 31 | `ModelRef` | doc only | `MetricDefinition.judge_model_ref`, `JudgeInfo.model_ref` |
| 32 | `Money` | projected as `cost_record.amount`, `cost_record.currency`, `cost_summary.total`, `cost_summary.currency` | prices inside `PricingCatalog.entries[]` stay in `doc` |
| 33 | `ObservedLanguage` | doc only | no entity field holds it in 0.0.1 (a helper result) |
| 34 | `OneOfRule` | doc only | `MetricDefinition.pass_rule`, `aggregation.turn_rule` |
| 35 | `PayloadRedaction` | doc only | inside an `ExportBundle` document only |
| 36 | `PerConversationAggregation` | doc only | `MetricDefinition.aggregation` |
| 37 | `PerTurnCounts` | doc only | `MetricResult.per_turn.counts` |
| 38 | `PerTurnShareAggregation` | doc only | `MetricDefinition.aggregation` |
| 39 | `PerTurnSummary` | doc only | `MetricResult.per_turn` |
| 40 | `PersonaRef` | projected as `evaluator.persona_id`, `evaluator.persona_version` | `content_digest` stays in `doc` |
| 41 | `PriceEntry` | doc only | `PricingCatalog.entries[]`; cost resolution reads the whole catalog |
| 42 | `PriceProvenance` | doc only | inside `PricingCatalog.entries[]` |
| 43 | `PricingCatalogRef` | projected as `config_snapshot.pricing_catalog_id`, `config_snapshot.pricing_catalog_version` | `content_digest` stays in `doc` |
| 44 | `PricingSnapshot` | doc only | `CostRecord.pricing_snapshot` |
| 45 | `PrivacyMarker` | projected as `human_subject`, `human_subject_reason`, `consent_status`, `retain_until`, `delete_on_expiry`, `policy_ref` | on `artifact`, `transcript`, `dataset_item`; on `run` with the `caller_` prefix (from `CallerExternalId.privacy`) |
| 46 | `ProvenanceRedaction` | doc only | inside an `ExportBundle` document only |
| 47 | `ProviderConfigRef` | doc only | `Project.provider_configs[]` (references only, never keys) |
| 48 | `RangeRule` | doc only | `MetricDefinition.pass_rule`, `aggregation.turn_rule` |
| 49 | `RatingScale` | doc only | `MetricDefinition.scale` |
| 50 | `RedactionRecord` | doc only | inside an `ExportBundle` document only |
| 51 | `ReportedCostRaw` | doc only | `CostRecord.reported_raw` (verbatim text, may hold any code point) |
| 52 | `ResultError` | doc only | `MetricResult.error` |
| 53 | `RetentionPolicy` | projected as `retain_until`, `delete_on_expiry`, `policy_ref` | with the `caller_` prefix on `run` |
| 54 | `RubricItem` | doc only | `Rubric.items[]` |
| 55 | `ScorerInfo` | doc only | `MetricResult.scorer` |
| 56 | `ScriptedTurn` | doc only | `Evaluator.scripted_turns[]` |
| 57 | `SealedAadFields` | doc only | inside an `ExportBundle` document only |
| 58 | `SealedPayload` | doc only | inside an `ExportBundle` document only; ciphertext byte-identical at rest |
| 59 | `Segment` | doc only | `Turn.segments[]` (free text) |
| 60 | `SipEndpoint` | projected as `agent_target.transport` | the `uri` stays in `doc` |
| 61 | `TestProfileRef` | projected as `evaluator.test_profile_id`, `evaluator.test_profile_version` | the snapshot's copy is the side table `snapshot_profile_ref`; `content_digest` stays in `doc` |
| 62 | `TimelineOrigin` | doc only | `Transcript.timeline_origin`, `ToolCallEvent.timeline_origin` |
| 63 | `UsageQuantity` | doc only | `UsageRecord.quantities[]` |
| 64 | `VerdictCheck` | doc only | `RunVerdict.checks[]` |
| 65 | `VersionedRef` | doc only | the base of the typed references; no field is typed with it directly |
| 66 | `WallTime` | doc only | `Turn.wall_time` |
| 67 | `WebRtcEndpoint` | projected as `agent_target.transport` | the `url` stays in `doc` |
| 68 | `WebSocketEndpoint` | projected as `agent_target.transport` | the `url` stays in `doc` |
| 69 | `WeightedExclusion` | doc only | `RunVerdict.weighted_excluded[]` |
| 70 | `WithheldRecord` | side table `run_bundle_withheld` | `RunBundle.withheld[]`, in order |

Totals: 54 `doc only`, 14 `projected as`, 2 `side table`.

## Ambiguities resolved
Each point below is one where `docs/phase2/SCOPE.md` was silent or read two ways; the recommendation is the decision of this ADR, for the lead to auto-approve and log.
- **Ambiguity 1. Turn to transcript foreign key.** SCOPE section 1.8 lists "transcript CASCADE" for `turn`. The code allows a RunBundle to hold the turns of a transcript it declares withheld, so a foreign key would refuse a valid bundle. **Recommendation:** no FK on `turn.transcript_id`; turns are owned by the run (`run_id` CASCADE) and the unique `(transcript_id, seq)` stays. Rejected: keeping the FK and refusing such bundles (the store would not store every valid bundle); a deferred FK (Postgres only, no SQLite equivalent; parity broken).
- **Ambiguity 2. "Every table has these common columns".** SCOPE section 1.8 and P2-10 say every table; side tables are derived rows. **Recommendation:** the common columns and the `schema_version` CHECK sit on the 26 entity tables; `tracer_meta.schema_version` carries the pin for the database; side tables carry none (Decision 4). Rejected: copying `schema_version` onto member rows (redundant, and a second place for a data migration to rewrite).
- **Ambiguity 3. Run privacy column names.** **Recommendation:** the prefix `caller_` on `run` (Decision 6a). Rejected: unprefixed names, which read as a marker of the run itself.
- **Ambiguity 4. `human_subject_reason`.** P2-18 lists five privacy columns. **Recommendation:** also project `human_subject_reason`, so that precautionary markers (`marked_before_role_evidence`) can be found and the P-46 CHECK covers the whole marker. Additive, no content.
- **Ambiguity 5. Integers beyond 64 bits.** `version` and `*_ms` are unbounded in the models. **Recommendation:** projected integers are `bigint`; the repository refuses a value it cannot store before any write (`StoreValueOutOfRange`, naming the table and column, never the value); file finding **P2-F2**: bound `version` and the `*_ms` fields by `MAX_SAFE_INTEGER` in 0.0.2 (an I-JSON interoperability bound already used for counts). **Revision 35 (A2, auto-approved):** the real ceiling is `2**53 - 1`, not `2**63 - 1`: `digest.jcs_bytes` refuses any integer outside the I-JSON range, and every stored document is hashed for `doc_sha256`, so `StoreValueOutOfRange` fires above `2**53 - 1` (on the projected column, or `doc` for an integer elsewhere in the document); the 64-bit branch is reachable only for a column that is not hashed, and there is none today. P2-F2 therefore states that a model-valid `*_ms` value of `2**53` or more cannot be stored at all in 0.0.1, and that a `version` of `2**53` or more cannot carry a digest that `digest.compute_digest` can produce (`NonIJsonValueError`), so no such versioned entity validates. Rejected: `numeric` or text columns for these (text order breaks `version DESC` on SQLite; `numeric` keys are slower and differ between backends).
- **Ambiguity 6. Currency column type.** ADR 0014 Decision 7 says `char(3)`; SQLite STRICT has no `char` type and Postgres `char(n)` pads. **Recommendation:** `KeyCol` with CHECK `= 'USD'` on both backends; ADR 0014 Decision 7 is read as "three letters, USD only", which the CHECK states exactly.
- **Ambiguity 7. Versioned members of a RunBundle.** `pricing_catalogs` and `test_profiles` are versioned, so `(kind, record_id, ordinal)` cannot name them. **Recommendation:** `run_bundle_member.record_version`, required exactly for those two kinds (Table 2).
- **Ambiguity 8. Export links beyond runs.** SCOPE names `export_bundle_run` only. An internal export carries test profiles in the clear and every export may carry dataset items; deleting a profile or an item must reach those copies. **Recommendation:** add `export_bundle_item` and `export_bundle_profile` (Table 2; ADR 0020 Decisions 3 and 4). Rejected: one polymorphic link table (a kind column that the cascade queries must filter, for no gain); no links (stored copies outlive the deletion).
- **Ambiguity 9. Alembic's table.** Alembic creates `alembic_version`, which is outside `MetaData`, not STRICT and updated by every migration. **Recommendation:** it is the one table outside Tables 1 and 2; the STRICT, `WITHOUT ROWID` and trigger tests exclude it by name, and `test_metadata_has_one_table_per_entity` compares `MetaData`, which does not contain it.
- **Ambiguity 10. Keyed digests at rest.** SCOPE section 0 says the internal store holds no keyed digests. The models already refuse a keyed own digest without a key resolver (`DigestKeyUnavailable`), and a RunBundle accepts a keyed or absent `TestProfileRef` digest. **Recommendation:** the store is internal data in the sense of ADR 0006 Decision 6, so `store_snapshot` and `store_run_bundle` run `snapshot.check_internal_snapshot` and refuse an absent or keyed profile-reference digest with a typed error; other keyed reference digests are kept in `doc` unverified, as the models accept them. The `content_digest_scheme` CHECK equals the shipped enum (both schemes), and the model makes a keyed own digest impossible to store.
- **Ambiguity 11. Write paths outside a RunBundle.** SCOPE names `store_definition`, `store_snapshot`, `store_run_bundle` and `store_export`. Project and DatasetItem have no path, and `run.project_id` needs the project row. **Recommendation:** `store_project` and `store_dataset_item` (single records, validated by their models; they are not run-owned, so the Q5 argument against per-record writes does not apply); Dataset goes through `store_definition`. `store_run_bundle` refuses a bundle whose project is not stored (typed error); it never creates a project.
- **Ambiguity 12. More CHECKs than SCOPE lists.** **Recommendation:** add the CHECKs of Decision 12 that mirror model rules on projected columns (`not_applicable` implies `passed IS NULL`; `not_available` implies no `amount`, `basis` or `currency`; currency with amount; paired reference columns; `cost_summary.total >= 0`, N81). They cost nothing and close raw-SQL holes for the tri-state rule.
- **Ambiguity 13. Which bytes an export keeps.** **Recommendation:** `export_bundle.doc` is the `model_dump_json()` of the validated export (one canonical text, like every `doc`), not the caller's input bytes; input that was already canonical is therefore kept byte for byte, and sealed ciphertext always is (ADR 0022 Decision 9).
- **Ambiguity 14. One row in `tracer_meta`.** **Recommendation:** a `singleton` key with CHECK `= 1`, so a second row is refused by the key, on both backends.
- **Ambiguity 15. `WITHOUT ROWID` and large documents.** SQLite documents that `WITHOUT ROWID` tables suit rows smaller than about a twentieth of a page; `doc` values are often larger (an export can approach 256 MiB). **Recommendation:** keep Q19 as decided (type strictness is the reason, and the tables have explicit keys); P2-23 records store and load times, and a measured problem is answered by a reviewed migration, never by a weaker table.
- **Ambiguity 16 (revision 35, A1). Re-import uniqueness for spans.** Revision 34 made the key unique for `run` and `span`. The frozen golden `RunBundle/valid/maximal.json` holds two spans of one run with the same `span_ref`, so a unique span key would refuse a valid bundle (the argument of Ambiguity 1). **Decision (auto-approved):** unique for `run` only; `span`, `transcript` and `usage_record` keys repeat and are looked up per run (Table 2, ADR 0022 Decision 10); one record repeats a key only once (the primary key). File finding **P2-F3** (0.0.2): either a rule that the `span_ref` values of one run are distinct, or an explicit statement in the schema that they need not be. Rejected: changing the frozen golden fixture; a unique key where the first span wins (the store would refuse a valid bundle).

## Rejected alternatives
- **Fully normalized columns, about 100 tables (Q1).** Order, null and float fidelity are at risk, user `metadata` cannot be normalized, and every additive field becomes DDL.
- **Postgres `jsonb` for `doc` (Q1).** It rejects `\u0000`, reorders keys and rewrites `1e300` as a 301-digit integer, which I-JSON then rejects on read (PostgreSQL 18 docs, section 8.14).
- **Postgres `json` for `doc` (Q1).** It keeps exact text, but its functions still fail on `\u0000`, and the column type would differ between backends.
- **ORM declarative classes (Q2).** They duplicate the models, and the unit of work brings UPDATE semantics the store forbids.
- **Imperative mapping onto the pydantic models (Q2).** They are frozen, hide their input and validate on construction.
- **psycopg 3 (Q3).** `LGPL-3.0-only`, against the permissive-licence rule.
- **asyncpg now (Q3).** It forces an async API before Phase 7 shows the need; it can be a second dialect later behind the same parity suite.
- **Ownership through bundle membership only (Q4).** The cascade becomes indirect, and a record shared by two bundles would need reference counting.
- **`run_id` in the schema (Q4).** It contradicts N22 and the frozen 0.0.1 schema.
- **ExportBundle decomposed into the internal tables (Q7).** Export records are transformed copies and collide by ID with internal records.
- **Embedded definitions kept only inside the snapshot (Q8).** "All versions of X" would need a scan of every snapshot document.
- **FK with RESTRICT or CASCADE to versioned entities (Q9).** RESTRICT blocks deleting a definition that a run cites; CASCADE deletes runs with it; both demand rows a RunBundle does not carry.
- **UUIDs as `TEXT(32)` or `TEXT(36)` on SQLite (Q18).** Larger, case variants possible, and `CHAR(32)` (unverified as SQLAlchemy's SQLite DDL for `Uuid`) is not a STRICT type name.
- **Tables without STRICT (Q19).** NUMERIC affinity can coerce decimal text; a typed column could then hold another type.
- **Text projections for search (Q24).** Postgres text cannot hold U+0000, which `VerbatimText` allows.
- **`doc` as JCS text (Q25).** JCS is the content identity (`doc_sha256`), but the loader reads `model_dump_json()` text and the byte-identical round trip is defined on it.
- **Coercing absent values to 0 or false (Q30).** It turns "not applicable" or "not available" into a measurement.
- **Inventing project ownership of definitions (Q26).** Not in the schema; an assumption.

## Consequences
- Every read parses and validates JSON; list queries read projections and decode just the rows they return.
- A raw INSERT can store a row the models would reject; reads and `verify_database` detect it, the triggers stop raw UPDATEs.
- A schema change that adds a projected field needs a migration; a schema change that adds an unprojected field needs a data migration of `doc` only (ADR 0021).
- The store cannot answer amount ordering or sums in SQL; the repository computes in `Decimal` from decoded records.
- Finding P2-F1 (project ownership of definitions), finding P2-F2 (integer bounds) and finding P2-F3 (distinct span references within a run, revision 35) are 0.0.2 inputs; none blocks Phase 2.

## Invariants (testable)
- I1. `MetaData` holds exactly the 26 entity tables of Table 1 and the 11 side tables of Table 2. Tests: `test_metadata_has_one_table_per_entity`, `test_adr0019_entity_table_matches_metadata`.
- I2. Every value object is classified in Table 5. Test: `test_adr0019_every_value_object_classified`.
- I3. Versioned tables are keyed `(id, version)` with `version >= 1`; records of fact are keyed `id`. Tests: `test_versioned_tables_pk_is_id_version`, `test_version_check_ge_1`, `test_record_of_fact_tables_pk_is_id`.
- I4. References to versioned entities have no foreign key. Test: `test_definition_references_have_no_foreign_key`.
- I5. Every run-owned table has `run_id` with a cascading FK to `run`. Test: `test_run_owned_tables_have_run_id_cascade`.
- I6. A turn's `(transcript_id, seq)` is unique. Test: `test_turn_seq_unique_per_transcript`.
- I7. Every entity table pins `schema_version = '0.0.1'`. Test: `test_schema_version_check_pinned_to_0_0_1`.
- I8. Projected closed sets equal the shipped schema enums; `currency` is USD only; cost amounts are non-negative. Tests: `test_closed_set_checks_equal_shipped_schema_enums`, `test_currency_check_is_usd_only`, `test_cost_amount_non_negative_check_both_backends`.
- I9. The privacy columns follow P-46. Tests: `test_privacy_projection_check`, `test_retention_columns_mirror_privacy_marker`, `test_retention_absent_is_null_never_false`.
- I10. Column types per backend are those of Table 3. Tests: `test_uuid_column_native_on_postgres_blob16_on_sqlite`, `test_uuid_v7_check_rejects_other_versions`, `test_instant_round_trip_edges`, `test_instant_round_trip_under_non_utc_server_timezone`, `test_sqlite_instant_text_sorts_chronologically`, `test_decimal_round_trip_exact`, `test_sqlite_decimal_stored_as_text`, `test_bool_tristate_null_never_false`, `test_no_sqlalchemy_datetime_columns`, `test_db_doc_column_is_text_not_json`.
- I11. Text comparison is code point order on both backends and keeps case and normalization forms distinct. Tests: `test_key_column_order_parity`, `test_case_distinct_keys_distinct_on_both_backends`, `test_nfc_nfd_distinct_on_both_backends`, `test_indic_text_round_trip_byte_exact`, `test_indic_text_no_normalization_by_driver`, `test_language_tags_stored_canonical_case`.
- I12. SQLite tables are STRICT and `WITHOUT ROWID`. Test: `test_sqlite_tables_are_strict_without_rowid`.
- I13. Every golden, frozen and adversarial fixture round-trips equal and byte-identical, with digests verifying. Tests: `test_db_round_trip_every_golden_valid_fixture`, `test_db_round_trip_frozen_0_0_1_fixtures`, `test_db_digest_preserved_on_round_trip`, `test_db_adversarial_preserved_fixtures_round_trip`, `test_db_nul_in_verbatim_text_round_trips`, `test_db_metadata_large_float_round_trips`.
- I14. Projections equal the model fields, and reads re-check them. Tests: `test_db_projection_equals_model_fields`, `test_db_read_uses_strict_loader`, `test_db_tampered_doc_rejected_on_read`, `test_verify_database_finds_projection_mismatch_without_echo`.
- I15. Tri-state values project as NULL, never 0 or false. Tests: `test_not_applicable_results_projected_null_never_zero_or_false`, `test_not_available_cost_projected_without_amount_or_basis`, `test_bool_tristate_null_never_false`.
- I16. No UPDATE succeeds on any table. Test: `test_update_rejected_on_every_table`.
- I17. Export records never enter the internal tables. Test: `test_export_records_not_decomposed_into_internal_tables`.
- I18. Embedded definitions are stored and compared. Tests: `test_store_snapshot_extracts_embedded_definitions`, `test_store_snapshot_conflicting_embedded_definition_rejected`.
- I19. Errors never echo a value, a document fragment or the URL. Tests: `test_db_errors_never_echo_document_content`, `test_store_errors_hide_input`, `test_database_url_never_echoed_in_errors_or_logs`.
- I20. Every access pattern of Decision 15 is served by an index on both backends. Tests: `test_access_pattern_indexes_exist`, `test_postgres_plans_can_use_indexes`, `test_sqlite_query_plans_use_indexes`, `test_keyset_pagination_runs_by_project_is_time_ordered`.
- I21. The T-DEG bundles of every tier fill the same table set. Test: `test_tdeg_bundles_round_trip_identical_table_set`.
- I22. evaldb's runtime closure is the bounded ranges of Decision 1; evalschema is unchanged and never imports evaldb. Tests: `test_evaldb_requires_dist_bounded_ranges`, `test_evaldb_runtime_imports_whitelist`, `test_evalschema_does_not_import_evaldb`, `test_evalschema_runtime_closure_unchanged_by_evaldb`, `test_evalschema_unchanged_since_phase1_gate`.
