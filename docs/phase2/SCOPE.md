# Phase 2 scope: tracer-evaldb (relational storage of evalschema 0.0.1)

Status: Scoped 2026-10-24; ADRs 0019-0022 written (batch 0, P2-00a to P2-00d, 2026-10-24; `docs/adr/README.md` revision 34), amended for batch B (revision 35, Decisions D4), batch C (revision 36, Decisions D5) and batch E (revision 37, Decisions D6).

Source: the final scoping report of the staff-reviewer, reproduced faithfully below. The only
edits are the ones listed under Decisions (D2 to D6), third-person wording in the reviewer's notes,
and repository-relative paths (`<repo>`).

## Decisions

- **D1. Q1 to Q30 auto-approved.** Every recommendation in section 3 (Q1 to Q30) is
  AUTO-APPROVED by the lead on the recommended option (owner instruction, 2026-10-23). Each is
  logged in the decision log with its rejected options and trade-off, and ADRs 0019 to 0022
  record them as tasks P2-00a to P2-00d.
- **D2. One departure: bounded tested ranges, not exact pins.** The report pinned the runtime
  dependencies of `tracer-evaldb` exactly. Per the owner's decision for `tracer-evalschema`
  (`pydantic>=2.11,<3`, `rfc8785>=0.1.4,<0.2`), they are bounded ranges instead, each tested at
  its lower bound (CI leg). The proposed ranges are `sqlalchemy>=2.1.6,<3`, `alembic>=1.20.1,<2`
  and `pg8000>=1.31.5,<2`; P2-01 may lower a bound only when the whole suite passes at that
  version on the lower-bound leg. `tracer-evalschema==0.0.1` stays exact: it is the schema-version
  pin of the stored data (ADR 0021), not a library range. The test `test_evaldb_requires_dist_exact`
  becomes `test_evaldb_requires_dist_bounded_ranges`, and every "pinned exactly" statement becomes
  "bounded ranges, each tested at its lower bound (CI leg)". Rejected: exact pins (they conflict
  with the owner's range decision and block users on other patch versions); unbounded ranges
  (an untested major version could change behaviour). Trade-off: one more CI leg.
- **D3. Commit ids after the history rewrite.** The report cited the Phase 1 gate commit as
  `534c346`, its id before the owner-authorised history rewrite of 2026-10-24. In the rewritten
  history the gate commit is `538c286` (pre-rewrite id `534c346`), recorded by commit `bd85da8`
  (`git log --grep='Phase 1 gate commit recorded' --format=%h -1`). The baseline for
  "evalschema unchanged since the gate" (P2-01 `test_evalschema_unchanged_since_phase1_gate`, gate
  item 8, section 0) is the rewritten-history tip at the time P2-01 scaffolds the package, because
  the commit after the gate changed the evalschema runtime dependencies to ranges (D2). P2-01
  records that commit id, the schema.lock sha256 and the source tree hash in its evidence.
- **D4. ADR amendments from qa's batch B (2026-10-24).** The batch B ambiguities A1 to A17 (`docs/phase2/api-surface-batch-b.md` section 10) are auto-approved by the lead on qa's recommendations (decision log "PHASE 2 BATCH B (qa) DELIVERED") and applied to ADRs 0019, 0020 and 0022 (`docs/adr/README.md` revision 35). The one change of a SCOPE reading: `external_ref_index` is unique for the kind `run` only, and `span`, `transcript` and `usage_record` keys repeat and are looked up per run (A1), because the frozen golden `RunBundle/valid/maximal.json` holds two spans of one run with one `span_ref`; finding **P2-F3** (0.0.2) is filed. Rejected: changing the frozen fixture; a unique span key (the store would refuse a valid bundle). Trade-off: a re-imported span is found by a run-scoped lookup, not refused by the database.
- **D5. Batch C decisions (qa's contract, 2026-10-24).** The batch C ambiguities A1 to A19 (`docs/phase2/api-surface-batch-c.md` section 11) are auto-approved by the lead on qa's recommendations (decision log "PHASE 2 BATCH C (qa) DELIVERED") and applied to ADRs 0019, 0020 and 0022 (`docs/adr/README.md` revision 36). In summary:
  - **Engine first, one transaction per call (A1).** The 15 repository functions live in `tracer_evaldb.repository` (re-exported by `tracer_evaldb`). Each takes the `Engine` of `create_store_engine` as its first argument and opens exactly one `engine.begin()` transaction, committed on return and rolled back on any exception; a `Connection`, a URL or `None` is a `TypeError` before any statement. The writers re-validate from the instance's own JSON and send no `INSERT` before every check has passed; for a stored record of fact, ownership is checked before content (A3). The readers `load_project`, `load_definition`, `load_snapshot` and `load_dataset_item` are added (A2). Rejected: a `Session` or a repository object holding the engine (state with no gain); accepting a `Connection` (two transaction boundaries to test; a later phase can add it). Trade-off: no multi-call transaction in Phase 2; atomicity across records comes from the write units (`store_run_bundle`, `store_export`).
  - **SQLSTATE hook (A7, A8, A9).** `create_store_engine` registers a `handle_error` hook on the Postgres engine that turns every driver error of SQLSTATE class 23 into `sqlalchemy.exc.IntegrityError` (pg8000 reports constraint violations as `ProgrammingError`) and drops the detail, hint and context fields, which quote the failing row. The class is mapped whole, not code by code: a delete refused by `ON DELETE RESTRICT` is `23503` on Postgres 15.19 and `23001` on 18.6 (batch B A12 recorded `23001` for both; corrected). The Postgres immutability triggers raise `23001`, so a raw `UPDATE` is an `IntegrityError` on both backends. The repository maps an `IntegrityError` to `StoreConstraintViolation(kind, table, constraint)`. Rejected: a fixed list of codes (it breaks when a server version changes the code); the trigger default `P0001` (a `ProgrammingError` on Postgres but an `IntegrityError` on SQLite). Trade-off: a future class 23 code is translated without review.
  - **`hide_parameters=True` on both engines (A9).** ADR 0019 Decision 18 required it and batch A did not set it; batch C sets it on the SQLite and the Postgres engine, with `echo` off. Rejected: hiding values only in the repository's own errors (a driver error raised outside them would still print bound values). Trade-off: a failing statement's parameters are not visible when debugging.
  - **REPEATABLE READ on `load_run_bundle` (A16; no test pins it).** On Postgres the load reads the member rows and the records in one REPEATABLE READ transaction, so an incomplete-bundle report describes one moment; on SQLite the single read transaction already does. Rejected: READ COMMITTED (a member deleted between two statements could be listed and then missing). Trade-off: none expected for a read-only transaction (not measured).
  - **`expected_purpose=None` is a `TypeError` (A12).** At both export entry points (`store_export`, `load_export`) the store has no "any purpose" form, unlike `loader.load_bundle`, which reads `None` as any purpose (SCHEMA_POLICY section 11: required, no default). Rejected: passing `None` through to the loader. Trade-off: a caller names the purpose even for an internal round trip.
  - **Add-on test names.** `test_store_same_run_bundle_twice_is_a_no_op` and `test_record_owned_by_another_run_rejected` (ADR 0022 Ambiguities 2 and 3) join the P2-12b row; `test_deletion_audit_rows_cannot_be_deleted` (ADR 0020 Ambiguity 6) joins the P2-16 row, not P2-17b, because it tests the `BEFORE DELETE` trigger and needs no deletion API (A11). `test_delete_snapshot_refused_while_a_run_references_it` (ADR 0020 Ambiguity 5) joins the P2-17a row (it needs the root `delete_snapshot`) and `test_adr0021_policy_rules_are_tested` also joins the P2-19 row (its rules cite the migration and version tests); both are written with those batches, not with batch C.
  - Also auto-approved and recorded in the contract: A5 (the golden maximal snapshots carry no profile digest and are refused as internal data), A6 (re-storing a bundle after a member was deleted inserts the absent records again; no test pins it, and the owner may decide otherwise), A13 (no `max_bytes` parameter), A14 (the reading of "identical table set" for the T-DEG bundles), A15 (concurrent writers of one new record are not pinned; a Phase 5 risk), A17 (bare `uuid.UUID` arguments accepted, version 7 only), A18 (the statement-count invariant is pinned up to 10,000 records per kind).
- **D6. Batch E decisions (qa's contract, 2026-10-24).** The lead's batch E covers the SCOPE section 1.7 rows P2-20, P2-21, P2-23 and P2-26 (the section headings keep the report's batch letters; the deletion rows of section 1.6 were done in batch D). The batch E ambiguities A1 to A12 (`docs/phase2/api-surface-batch-e.md` section 9) are auto-approved by the lead on qa's recommendations (decision log "PHASE 2 BATCH E (qa) DELIVERED") and applied to ADRs 0019 and 0021 (`docs/adr/README.md` revision 37). In summary:
  - **Definitions need no extra index (A1).** "Definitions by `(id, version DESC)`" in the P2-20 row is served by the primary key `(id, version)` of each of the nine versioned tables, which both backends read backward with no sort; the row and ADR 0019 Decision 15 now say so. Rejected: nine descending indexes that duplicate the key (write cost, no read gain).
  - **Extra indexes adopted (A3), `mode` and `observed_tier` kept (A2).** Beyond the P2-20 list: `ix_run_verdict_run_id` (the cascade from `run`), and `ix_dataset_item_artifact_id` and `ix_dataset_item_transcript_id`, because `delete_run`'s `WHERE artifact_id IN (...) OR transcript_id IN (...)` would scan `dataset_item` once per deleted run without them. Not added: `dataset_item.source_run_id` (nothing reads by it). `mode` and `observed_tier` keep single-column indexes as listed; the Phase 3 measurements decide whether a composite such as `(project_id, mode)` is wanted. Rejected: guessing composite shapes now. Trade-off: 25 more indexes to maintain on every insert, for index-served reads and deletions.
  - **Revision `0002` (A4, A5).** The 25 new indexes (`ix_<table>_<columns>`, none unique, partial or descending; 13 indexes exist since `0001`, 38 in all) ship as the new hand-written, frozen revision `0002_access_pattern_indexes` (`down_revision` `0001`, one transaction, index names as literals, `key` quoted, no import of a live module, `DOWNGRADE_DROPS = ()`). Revision `0001` stays frozen and is never regenerated. The NAMED SPEC CHANGE of two batch D test files (`HEAD = "0002"`, the chain and packaged-file lists, and `test_postgres_migration_is_transactional` expecting `0001` after a fault while downgrading to `base`, because each revision commits in its own transaction) is approved: no assertion is removed or weakened; dev applies it in the commit that adds `0002`. With two revisions the "older revision" case of `open_store` is tested (ADR 0021 rule 3). Rejected: regenerating `0001` with the indexes (a shipped revision would change under existing databases).
  - **`verify_database` (A6 to A9).** `verify_database(engine) -> VerifyReport(findings, informational, checked, ok)`; each `VerifyFinding(kind, id, version, problem, location)` names the table, the wire id (`run_...`, not a bare UUID, because operators search logs by wire id), the version of a versioned entity and a schema identifier, never a value. Problems (`findings`, they make `ok` false): `doc_invalid`, `digest_mismatch`, `doc_sha256_mismatch`, `projection_mismatch`, `side_rows_mismatch`. Notes (`informational`, `ok` stays true): `doc_text_not_canonical` (the stored text differs from `model_dump_json()` only in formatting) and `bundle_member_absent` (a bundle lists a member that `delete_test_profile` or `delete_definition` removed, which the package's own deletions create by design). A document that does not load gives one finding and nothing more, because the projections come from the model. It is read-only, deterministic, works on a `create_all` and an Alembic schema, and on Postgres reads one REPEATABLE READ snapshot, as `load_run_bundle` does (recommended; no test pins the isolation). Rejected: bare UUIDs; treating an absent bundle member as an error (every deletion of a profile would make the store fail its own check). Trade-off: an operator must read the notes to see incomplete bundles.
  - **Indic storage (A10).** Nine of the 79 fixture texts carry a control character (U+0000 included), a bidi control, a line or paragraph separator, a BOM or edge white space, which the ADR 0017 label rule refuses, so they cannot be a project name and are stored inside documents only; `test_indic_label_rule_refuses_exactly_the_pinned_cases` pins them by id. Nothing in the fixture is wrong.
  - **Finding P2-F4 (A11, Phase 5 performance).** The P2-23 measurement of a 4,000-turn bundle: store median 5.6 s on Postgres 18 and 5.7 s on Postgres 15 through pg8000, against 0.28 s on SQLite; loads 0.27 s (SQLite) and 0.33 to 0.34 s (Postgres); statement counts equal at 4, 400 and 4,000 turns (at most one INSERT per table; SQLite 45 store and 18 load statements, Postgres 44 and 17). The cost is pg8000's `executemany` of the turn rows, the risk section 5 names. Candidate remedies, decided in Phase 5 and each run behind the same parity suite: batching tuned for `executemany`, `COPY` for the bulk tables, or a second permissive-licence driver as another dialect. No timing threshold is asserted; the evidence is `docs/phase2/evidence/measurements/bundle/`.
  - **Still open (A12):** the error type of `upgrade` over a `create_all` schema (batch D A11) and concurrent writers (batch D A15, A16; Phase 5 risks).
  - **Add-on test names.** The batch E tests join the rows P2-20 (index rules, the statements the package sends, revision `0002` and the four `open_store` tests of an older revision), P2-21, P2-23 (the harness, never a duration) and P2-26; the P2-20 task text records A1 and A3, and the pg8000 risk of section 5 points to P2-F4.

## Summary

Phase 2 is `tracer-evaldb`, a new package. It maps the 26 entities to SQLAlchemy Core tables with Alembic migrations, and SQLite and Postgres must behave the same. It is 47 tasks, about 273 hours, and adds four ADRs (0019 to 0022) and 30 design questions, each with a recommendation the lead can auto-approve.

**What the reviewer checked and did not.** The reviewer read HANDOFF, Phase 1 SCOPE (sections 1 to 7 and the gate), SCHEMA_POLICY, ADRs 0001, 0002 and 0009, the decision log, the plan file, `bundle.py`, `run.py`, `project.py`, `transcript.py`, `privacy.py`, `ci.yml`, the root `pyproject.toml` and `license-policy.toml`. The reviewer did not run the suite and did not run `git status`, and did not read the private fixture, `.env.local` or the owner's keys file. Versions and licences below come from PyPI JSON, the PostgreSQL docs and the SQLAlchemy docs, all fetched on 2026-10-24.

Repository facts that drive the design (checked in the code):
- **Ownership.** Only `Run` has `project_id`, and only `UsageRecord` has `run_id` (N22). The other run-owned records name no run; a run's records are the records of its `RunBundle`. The definitions have no project field.
- **Free text.** `VerbatimText` accepts U+0000; it rejects only lone surrogates (`text.py:69`). Turn text and `metadata` strings can therefore hold NUL.
- **What `RunBundle` carries.** It holds `pricing_catalogs` and `test_profiles` but no `AgentTarget`. `Run.agent_target` is a reference that may not resolve.
- **Turn order.** A turn's `seq` is unique per transcript (`Turn` docstring).
- **Rescoring.** A rescoring is a new RunBundle for the same run (B5), so records of fact are shared across bundles.
- **Golden fixtures.** They live under `packages/evalschema/tests/fixtures/golden/<Entity>/valid|invalid/` (449 JSON files).

---

## 0. Objective and non-goals

**Objective.** Add a new workspace package (distribution `tracer-evaldb`, import `tracer_evaldb`, Apache-2.0). It depends on `tracer-evalschema==0.0.1` exactly and does not change it. It stores and reloads every one of the 26 entities, and every value object inside them, on SQLite (CLI/CI mode) and Postgres (server mode). For this phase, "store" means three things:
- an entity goes to rows and comes back equal, with `model_dump_json()` byte-identical and digests still verifying;
- deletion uses `ON DELETE CASCADE` or an equivalent written into an ADR, plus a deletion audit that holds no content;
- the database enforces what it can (keys, FKs, closed sets, immutability, the version pin), and the models still validate everything on write and on read.

**Done means:**
- every gate item in section 4 passes on hosted CI (SQLite on Python 3.11 to 3.14, Postgres 15 and 18 on Python 3.11 and 3.14);
- every test named in section 1 exists under its exact name and has committed red-first evidence under `docs/phase2/evidence/red/`;
- `tracer-evalschema` is byte-unchanged since the evaldb baseline: the rewritten-history tip at the time P2-01 scaffolds the package (see Decisions D3; the Phase 1 gate commit is `538c286`, pre-rewrite id `534c346`).

**Non-goals.** These are phase boundaries, not cuts:
- object storage or MinIO, and uploads (Phase 3);
- streaming or partial ingest of a run (Phase 5 designs staging; Phase 2 writes whole validated bundles);
- the export builder B13/B19 and OpenAPI/TS generation (Phase 7);
- `verify_bundle_digests` B11: the internal store holds no keyed digests and exports are stored as opaque documents; it goes with the key-management work;
- retention enforcement or any sweeper (the owner rule: data only);
- full-text search on transcript text (blocked by NUL in Postgres text, see Q24);
- importing another installation's exports into the internal tables (MVP-2 portability).

Any change to `tracer-evalschema` found necessary in Phase 2 is logged as a 0.0.2 finding. If it blocks, the phase stops and the date moves.

---

## 1. Task table (each task 1 day or less; qa writes the named tests red first, then the owner implements)

Owners: SA = schema-architect, dev, qa, IL = indic-linguistics, SL = security-licensing.

Unless a test says otherwise, every test runs on both backends through a `store` fixture parametrized `[sqlite]` and `[postgres]`. The `[postgres]` case carries the marker `postgres`, which is deselected by default, the same way `private_fixtures` is today, and selected in the Postgres CI job. It is never skipped.

Tier: storage is tier-agnostic. Tests that take a RunBundle are parametrized over the Phase 1 T-DEG bundles (T0, T0m, T1, T2, simulated and human; `_support_b13`).

### 1.1 ADRs (batch 0)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-00a | SA | ADR 0019 "Relational storage of evalschema 0.0.1": document column plus projections; one table per entity (table in section 1.8); type map per backend; parity table; split of what the DB enforces and what the models enforce. Records Q1, Q2, Q4, Q8, Q9, Q18, Q19, Q24, Q25, Q30. | Status Accepted. Each question has rejected options and the trade-off. Every invariant is a testable statement linked to a test below. All 70 value objects of ADR 0001 Decision 8 are classified: "doc only", "projected as <columns>" or "side table <name>". | `test_adr0019_entity_table_matches_metadata` (parses the ADR table and compares it with `MetaData`); `test_adr0019_every_value_object_classified` | 8 |
| P2-00b | SA | ADR 0020 "Deletion roots, cascade graph, deletion audit, retention data" (Q10 to Q14, Q16). | Lists the roots and, for every FK, its `ondelete` and why. Lists the application-level cascades with their reason. States the audit record shape. Rule: no `SET NULL` and no `SET DEFAULT` anywhere, because either would edit a record of fact. | `test_adr0020_cascade_graph_matches_metadata` | 6 |
| P2-00c | SA | ADR 0021 "Store schema version and migration policy": the frozen 0.0.1 schema versus 0.0.2 (Q15, Q28, Q29). | Covers: one schema version per database; the pinned CHECK; `tracer_meta`; no automatic migration; how a data migration is built; refusal of a digest-changing upgrade of a versioned entity; byte-exact reversible downgrade or a typed error. | `test_adr0021_policy_rules_are_tested` (every numbered rule cites an existing test name) | 6 |
| P2-00d | SA | ADR 0022 "Write unit, rescoring, idempotent re-import (P-02), N18, incomplete reconstruction" (Q5, Q6, Q16, Q17, Q22, Q23). | Decisions recorded and logged in the decision log. | `test_adr0022_policy_rules_are_tested` | 5 |

### 1.2 Foundation (batch A)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-01 | dev | Scaffold `packages/evaldb`: workspace member, SPDX headers, `py.typed`, ruff and mypy cover the new paths. Runtime deps as bounded ranges, each tested at its lower bound (CI leg): `tracer-evalschema==0.0.1` (the schema-version pin, Decisions D2), `sqlalchemy>=2.1.6,<3`, `alembic>=1.20.1,<2`; `pg8000>=1.31.5,<2` as extra `[postgres]`. | Wheel ships the migrations and no tests. The evalschema runtime closure and its import whitelist are unchanged. | `test_evaldb_runtime_imports_whitelist`, `test_evalschema_does_not_import_evaldb`, `test_evalschema_runtime_closure_unchanged_by_evaldb`, `test_evaldb_requires_dist_bounded_ranges`, `test_evaldb_wheel_contains_migrations_and_no_tests`, `test_evalschema_unchanged_since_phase1_gate` (schema.lock sha256 and source tree hash equal the values recorded at the evaldb baseline, the rewritten-history tip when P2-01 scaffolds the package; Decisions D3) | 6 |
| P2-02 | SL | Licence review of the new closure: sqlalchemy (MIT), alembic (MIT), mako (MIT), markupsafe, typing-extensions, pg8000 (BSD-3-Clause), scramp (MIT-0), asn1crypto, python-dateutil, six. Add a `[runtime.evaldb]` closure to `license-policy.toml`; extend `scripts/check_licenses.py` to two packages. Record that psycopg was rejected (`LGPL-3.0-only`). | `check_licenses` reports ok. The lock holds no GPL or LGPL package. The licences of markupsafe, asn1crypto, dateutil and six are read from their files, not assumed. | `test_license_policy_lists_evaldb_closure`, `test_no_lgpl_or_gpl_in_lock`, `test_license_exceptions_exact` (existing, unchanged) | 5 |
| P2-03 | dev | CI: the Postgres job (section 4.2); SQLite tests in every existing Python leg; testpaths gain `packages/evaldb/tests`. | Postgres image pinned by digest. No password literal anywhere. The job is required. | `test_ci_postgres_job_matrix_and_pinned_image`, `test_ci_postgres_password_not_literal`, `test_ci_sqlite_runs_on_every_python_leg`, `test_postgres_marker_without_url_is_usage_error` (conftest: with `-m postgres` selected and `TRACER_TEST_POSTGRES_URL` unset, collection fails; it never skips) | 6 |
| P2-04 | dev | `create_store_engine()`. SQLite: `foreign_keys=ON` on every connection; real transactions on 3.11 via `isolation_level=None` plus a BEGIN event (the `autocommit` attribute is 3.12+); `busy_timeout`. Postgres: session `TimeZone=UTC`, refuse a database whose `server_encoding` is not UTF8. The URL is never echoed. | SQLite connections report foreign keys on. An orphan insert fails. Rollback works on 3.11. | `test_sqlite_connections_enforce_foreign_keys`, `test_sqlite_orphan_insert_rejected`, `test_sqlite_rollback_is_real_on_every_python`, `test_sqlite_version_at_least_3_37`, `test_postgres_session_timezone_is_utc`, `test_postgres_non_utf8_database_refused` (CI creates a `SQL_ASCII` database from template0), `test_database_url_never_echoed_in_errors_or_logs` (caplog and exception scan for the password and host) | 7 |
| P2-05 | dev | Column types: `UuidCol` (PG `uuid`, SQLite `BLOB` of 16 bytes); `InstantCol` (PG `timestamptz`, SQLite TEXT `YYYY-MM-DDTHH:MM:SS.ffffffZ`, ADR 0002); `DecimalCol` (PG unconstrained `NUMERIC`, SQLite TEXT, ADR 0014 Decision 7); `DocCol` (TEXT); `Sha256Col` (PG `bytea`, SQLite BLOB, 32 bytes); `KeyCol` (PG `COLLATE "C"`); booleans as nullable with NULL meaning absent (SQLite INTEGER with CHECK IN (0,1)). | Year 0001 and year 9999 survive on both backends (the B1 lesson). No float ever stores money. No SQLAlchemy `DateTime` column exists. | `test_uuid_column_native_on_postgres_blob16_on_sqlite`, `test_uuid_v7_check_rejects_other_versions`, `test_instant_round_trip_edges`, `test_instant_round_trip_under_non_utc_server_timezone` (PG database TimeZone `Asia/Kolkata`), `test_sqlite_instant_text_sorts_chronologically`, `test_decimal_round_trip_exact` (18 fractional digits, 30 integer digits, negative Money, `0`), `test_sqlite_decimal_stored_as_text` (`typeof()='text'`), `test_bool_tristate_null_never_false`, `test_key_column_order_parity`, `test_case_distinct_keys_distinct_on_both_backends`, `test_nfc_nfd_distinct_on_both_backends`, `test_no_sqlalchemy_datetime_columns` | 8 |

### 1.3 Tables and codecs (batch B)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-06 | SA+dev | The 9 versioned tables, primary key `(id, version)`. | Matches the ADR 0019 table. | `test_versioned_tables_pk_is_id_version`, `test_version_check_ge_1` | 6 |
| P2-07 | SA+dev | Tables `project`, `config_snapshot` (+ `snapshot_member`, `snapshot_profile_ref`), `run` (+ `external_ref_index`). | As above. | `test_record_of_fact_tables_pk_is_id`, `test_definition_references_have_no_foreign_key` | 8 |
| P2-08 | SA+dev | Tables `artifact`, `transcript`, `turn`, `exchange`, `tool_call_event`, `stage_span`, each with the storage-only `run_id` (Q4). | Unique `(transcript_id, seq)`. | `test_turn_seq_unique_per_transcript`, `test_run_owned_tables_have_run_id_cascade` | 6 |
| P2-09 | SA+dev | Tables `metric_result`, `run_verdict`, `usage_record`, `cost_record`, `cost_summary`. | CHECK `currency='USD'`. CHECK amount >= 0 for CostRecord (PG numeric comparison; SQLite `NOT GLOB '-*'`). | `test_currency_check_is_usd_only`, `test_cost_amount_non_negative_check_both_backends` | 6 |
| P2-10 | SA+dev | Tables `dataset_item`, `dataset_member`, `run_bundle`, `run_bundle_member`, `run_bundle_withheld`, `export_bundle`, `export_bundle_run`, `deletion_audit`, `tracer_meta`. On every table: `schema_version` CHECK `= '0.0.1'`; CHECKs on the projected closed sets, taken from the shipped `schemas/*.schema.json` `enum` lists (public; no private import); privacy CHECK (consent and retention present iff `human_subject`); SQLite tables `STRICT` (SQLAlchemy `sqlite_strict`, added in 2.0.37) and `WITHOUT ROWID`. | 26 entity tables plus the side tables; no others. | `test_metadata_has_one_table_per_entity`, `test_schema_version_check_pinned_to_0_0_1`, `test_closed_set_checks_equal_shipped_schema_enums`, `test_privacy_projection_check`, `test_no_fk_uses_set_null_or_set_default`, `test_fk_graph_matches_adr0020`, `test_sqlite_tables_are_strict_without_rowid` | 8 |
| P2-11a | dev | Codecs for the versioned entities, Project, ConfigSnapshot and Run. Encode: model to `doc` (exact `model_dump_json()` text), `doc_sha256` (SHA-256 of JCS over the JSON-mode dump) and the projections. Decode: through the strict `loader` (duplicate keys and depth), then model validation; projections re-checked. | Equal and byte-identical round trip; digests verify after load. | `test_db_round_trip_every_golden_valid_fixture`, `test_db_round_trip_frozen_0_0_1_fixtures`, `test_db_digest_preserved_on_round_trip`, `test_db_doc_column_is_text_not_json` (introspection of the PG type) | 8 |
| P2-11b | dev | Codecs for the other 17 entities. | Same. | `test_db_projection_equals_model_fields` (hypothesis over the Phase 1 builders), `test_db_nul_in_verbatim_text_round_trips` (U+0000 in Turn.text and in metadata strings, PG), `test_db_metadata_large_float_round_trips` (`1e300`, `2.0**60`; jsonb would turn `1e300` into a 301-digit integer, which I-JSON then rejects), `test_db_read_uses_strict_loader` (duplicate key injected into `doc` is rejected), `test_db_tampered_doc_rejected_on_read` (versioned: `DigestMismatch`; record of fact: `StoreIntegrityError` from `doc_sha256`), `test_db_adversarial_preserved_fixtures_round_trip` (registry entries classified preserved or flagged), `test_db_errors_never_echo_document_content` | 8 |

### 1.4 Repository (batch C)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-12a | dev | `store_definition`, `store_snapshot` (the embedded definitions are inserted if absent and compared byte for byte if present; Q8), `latest_version`, lookup of a snapshot by digest. | Idempotent for the same content; typed conflict for different content. | `test_store_definition_idempotent_same_content`, `test_store_definition_conflicting_content_rejected`, `test_store_snapshot_extracts_embedded_definitions`, `test_store_snapshot_conflicting_embedded_definition_rejected`, `test_latest_version_lookup`, `test_snapshot_lookup_by_content_digest` | 6 |
| P2-12b | dev | `store_run_bundle` (one transaction; validated by the models first; records deduplicated by ID with content comparison), `load_run_bundle` (rebuilt in member order; result has `complete` and `missing[]`; Q16). | Atomic. The order of every list is kept. Rescoring shares records. | `test_run_bundle_round_trip_order_preserved`, `test_run_bundle_round_trip_byte_identical_json`, `test_run_bundle_withheld_round_trip`, `test_run_bundle_store_is_atomic` (fault injected after the turns), `test_invalid_run_bundle_never_stored`, `test_rescoring_bundle_shares_records_without_duplication`, `test_rescoring_bundle_conflicting_record_rejected`, `test_tdeg_bundles_round_trip_identical_table_set`, `test_not_applicable_results_projected_null_never_zero_or_false`, `test_not_available_cost_projected_without_amount_or_basis`, `test_huge_call_bundle_round_trip` (4,000 turns), `test_store_run_bundle_statement_count_independent_of_record_count`, `test_run_bundle_load_reports_incomplete_after_member_deletion`, `test_store_same_run_bundle_twice_is_a_no_op` (ADR 0022 Ambiguity 2; Decisions D5), `test_record_owned_by_another_run_rejected` (ADR 0022 Ambiguity 3; Decisions D5) | 8 |
| P2-12c | dev | `store_export` and `load_export`. The export is stored as an opaque document plus index rows (Q7). `expected_purpose` is keyword-only and required, with no default (SCHEMA_POLICY section 11 requirement for Phase 2). Size checked before parsing (256 MiB). | Sealed ciphertext byte-identical at rest. No export record enters the internal tables. | `test_store_export_requires_expected_purpose`, `test_load_export_requires_expected_purpose`, `test_store_export_purpose_mismatch_rejected`, `test_export_round_trip_byte_identical`, `test_export_sealed_payload_ciphertext_unchanged_at_rest`, `test_export_records_not_decomposed_into_internal_tables`, `test_export_size_limit_enforced_before_parse` | 5 |
| P2-13 | dev | P-02 re-import: `external_ref_index(entity_kind, key_sha256, entity_id)`, where `key_sha256` = SHA-256 of JCS of `[system, kind, value]`. Unique for Run only; a non-unique index scoped by run for StageSpan and for the provider call refs of Transcript and UsageRecord (Q17 as amended by Decisions D4; `test_external_ref_unique_per_entity_kind` reads: unique for the run kind, the other kinds are lookups). Typed `ReimportConflict`. | Exact lookup (case, white space, NFC/NFD and NUL all distinct); a 1 MB value works. | `test_reimport_finds_existing_run_by_external_ref`, `test_external_ref_unique_per_entity_kind`, `test_external_ref_key_is_hash_of_jcs_tuple`, `test_external_ref_lookup_is_exact`, `test_reimport_conflicting_content_raises_reimport_conflict`, `test_child_provider_call_refs_not_unique` | 5 |
| P2-16 | dev | Immutability triggers: `BEFORE UPDATE` raises, on every entity and side table, on both backends. | Records of fact and `(id, version)` rows can never be updated. | `test_update_rejected_on_every_table`, `test_immutability_triggers_exist_after_migrations`, `test_deletion_audit_rows_cannot_be_deleted` (ADR 0020 Ambiguity 6; Decisions D5) | 5 |

### 1.5 Migrations and version policy (batch D)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-14 | dev | Alembic inside the package (`tracer_evaldb/migrations`); hand-written revision `0001`; `render_as_batch` on SQLite. On SQLite, migrations run with `PRAGMA foreign_keys=OFF` (set outside a transaction) followed by `PRAGMA foreign_key_check`. Programmatic `upgrade(engine)`. `open_store` refuses a database that is not at head and never migrates by itself. | `compare_metadata` is empty at head on both backends. `alembic.ini` holds no URL. | `test_alembic_head_matches_metadata_exactly`, `test_alembic_ini_holds_no_database_url`, `test_migrations_ship_in_wheel`, `test_programmatic_upgrade_api`, `test_open_store_refuses_unmigrated_database`, `test_open_store_never_auto_migrates` | 8 |
| P2-15 | qa | Migration up and down on both backends. | Stairway (each revision up, down, up), on an empty and on a populated database. | `test_migration_stairway_up_down_up`, `test_downgrade_base_on_populated_db_requires_explicit_data_loss_flag`, `test_sqlite_batch_migration_preserves_child_rows`, `test_sqlite_batch_migration_preserves_strict_and_triggers` (test-only revision that adds a column; batch mode recreates the table), `test_postgres_migration_is_transactional` | 7 |
| P2-19 | SA+dev | Version policy mechanism: `tracer_meta(schema_version, evaldb_version)`; typed `StoreSchemaVersionTooNew` and `StoreSchemaVersionMismatch`; a test-only fake "0.0.2" data migration that rewrites `schema_version` in every `doc`. Digests are unchanged because `schema_version` is outside the digest domain (ADR 0006 Decision 5). | Byte-exact downgrade. A digest-changing upgrade of a versioned entity is refused. | `test_tracer_meta_records_schema_version`, `test_open_store_newer_schema_version_raises_too_new`, `test_open_store_older_schema_version_raises_mismatch`, `test_fake_data_migration_rewrites_schema_version_and_preserves_digests`, `test_fake_data_migration_downgrade_restores_bytes_exactly`, `test_digest_changing_upgrade_of_versioned_entity_refused`, `test_mixed_schema_versions_in_one_database_rejected`, `test_adr0021_policy_rules_are_tested` (also in the P2-00c row; written with batch D because its rules cite these tests; Decisions D5) | 8 |

### 1.6 Deletion and retention (batch E)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-17a | dev | Deletion roots: `delete_run`, `delete_project`, `delete_test_profile` (all versions), `delete_definition(kind, id)` (all versions), `delete_dataset_item`, `delete_export`. FK cascades plus the application-level cascades of ADR 0020, in one transaction (PG `SELECT ... FOR UPDATE`; SQLite `BEGIN IMMEDIATE`). | Same counts on both backends. | `test_delete_run_cascades_every_run_owned_kind`, `test_delete_run_removes_orphaned_snapshot_only`, `test_delete_project_cascades_runs`, `test_delete_test_profile_removes_all_versions_snapshots_report_unresolved`, `test_delete_definition_leaves_snapshots_valid`, `test_delete_run_removes_dataset_items_and_dataset_reports_unresolved`, `test_delete_run_removes_stored_exports_containing_it`, `test_cascade_counts_parity_sqlite_postgres`, `test_delete_unknown_root_is_typed_error_and_writes_no_audit`, `test_concurrent_insert_during_run_delete_never_orphans` (PG), `test_delete_snapshot_refused_while_a_run_references_it` (root `delete_snapshot`, ADR 0020 Ambiguity 5; Decisions D5) | 8 |
| P2-17b | dev | `deletion_audit` row: `audit_uuid` (DB-internal UUIDv7, no prefix, never exported), `deleted_at`, `actor` (required label, credential check), `reason` (closed set), `root_kind`, `counts` (kind to integer). No IDs, digests, text or metadata (Q12, Q13). | Atomic with the deletion; holds no content. | `test_delete_is_atomic_with_audit`, `test_deletion_audit_is_content_free` (scans audit rows for every ID, uuid hex, text, digest and metadata value of the deleted records), `test_deletion_audit_counts_per_kind_exact`, `test_deletion_requires_actor_and_reason` (TypeError, no defaults), `test_deletion_actor_rejects_credential_shaped_value` | 5 |
| P2-18 | dev | Retention columns only: `human_subject`, `consent_status`, `retain_until`, `delete_on_expiry`, `policy_ref` on artifact, transcript, dataset_item and run (`caller_external_id`). Partial index on `retain_until`. No enforcement code. | NULL means absent; never false. | `test_retention_columns_mirror_privacy_marker`, `test_retention_absent_is_null_never_false`, `test_retain_until_index_exists` | 4 |

### 1.7 Indexes, Indic text, audits and docs (batch F)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P2-20 | SA+dev | Indexes for the Phase 3 to 7 access patterns: runs by project ordered by id (UUIDv7 keyset); runs by `(agent_target_id, version)`, `mode`, `observed_tier`, `started_at`; runs by snapshot; `snapshot_member` by `(kind, def_id, def_version)` ("runs that used evaluator X"); artifacts by run and by `sha256`; transcripts by run; turns by `(transcript_id, seq)`; results by run and by `(metric_id, metric_version, status)`; verdict by status; usage and cost by run; spans and tool calls by run; snapshot by digest; definitions by `(id, version DESC)` (served by the primary key read backward, no extra index; Decisions D6) and metric definitions by `key`; dataset members; `external_ref_index`; `retain_until`. Also adopted (Decisions D6): `run_verdict.run_id`, and `dataset_item.artifact_id` and `dataset_item.transcript_id` (`delete_run`'s `OR` query would scan `dataset_item` without them). The 25 new indexes ship as the new frozen revision `0002`; revision `0001` stays frozen. | Each listed predicate is served by an index (or, for definitions, the primary key) on both backends. | `test_access_pattern_indexes_exist`, `test_postgres_plans_can_use_indexes` (EXPLAIN with `enable_seqscan=off`), `test_sqlite_query_plans_use_indexes` (EXPLAIN QUERY PLAN), `test_keyset_pagination_runs_by_project_is_time_ordered`; batch E (Decisions D6): `test_every_foreign_key_is_led_by_a_key_or_an_index`, `test_no_index_duplicates_a_key_or_another_index`, `test_the_pattern_table_covers_every_index_and_every_versioned_table`, `test_repository_and_deletion_statements_are_served_by_indexes`, `test_revision_0002_follows_0001_and_is_the_head`, `test_revision_0001_is_unchanged_by_the_new_indexes`, `test_revision_0002_is_frozen_text_and_never_reads_the_live_metadata`, `test_revision_0002_adds_exactly_the_access_pattern_indexes`, `test_revision_0002_keeps_every_row_and_the_store_readable`, `test_revision_0002_downgrade_restores_revision_0001_exactly`, `test_revision_0002_stairway_up_down_up`, `test_downgrade_through_0002_to_base_still_asks_for_the_flag_on_a_filled_store`, `test_revision_0002_is_one_transaction`, `test_revision_0002_upgrade_over_a_current_database_sends_no_write`, `test_open_store_at_an_older_shipped_revision_is_not_migrated`, `test_open_store_at_an_older_revision_changes_nothing_and_never_migrates`, `test_open_store_checks_the_revision_before_the_schema_version`, `test_open_store_after_the_upgrade_returns_the_head` | 8 |
| P2-21 | IL | Indic storage fixtures, all `needs_native_review`, never used as WER references: each of the 9 languages; ZWJ and ZWNJ; precomposed against decomposed nukta; Gurmukhi tippi and addak; Malayalam chillu atomic against a ZWJ sequence; native digits; a mixed-script observed turn; bidi controls. | Byte-exact on both backends. No normalization by driver or DB. Ordering is never language-sensitive. | `test_indic_text_round_trip_byte_exact`, `test_indic_text_no_normalization_by_driver`, `test_language_tags_stored_canonical_case`; batch E (Decisions D6): `test_indic_storage_fixture_is_well_formed`, `test_indic_label_rule_refuses_exactly_the_pinned_cases`, `test_indic_text_ordering_is_never_language_sensitive`, `test_language_tag_of_a_stored_bundle_transcript_is_canonical` | 5 |
| P2-23 | qa | Measurements for the 4,000-turn bundle: store and load time on both backends, saved to evidence. Only statement counts are asserted (P2-12b); no timing threshold is invented. | Numbers and environment recorded. | (evidence only; the harness `packages/evaldb/tests/evaldb_measure.py` is checked, never a duration, by batch E (Decisions D6): `test_measurement_harness_records_store_and_load_in_the_documented_format`, `test_measurement_harness_refuses_a_database_that_is_not_empty`, `test_measurement_harness_refuses_nonsense_parameters`, `test_measurement_command_line_writes_the_file`, `test_measurement_command_line_needs_a_postgres_url_and_never_prints_it`, `test_measurement_harness_runs_on_postgres`, `test_measurement_harness_is_not_collected_as_a_test_module`) | 4 |
| P2-24 | SL | Security audit of evaldb: SQL built only from bound parameters; no secret columns; errors hide input; URL not echoed; deletion audit holds no content; at-rest statement (the DB is not encrypted by tracer; internal data is personal data); `expected_purpose` at every export entry point. | `docs/security/P2-24-audit.md` with zero blockers. | `test_no_string_built_sql` (ruff S608 plus an AST check that no `text()` takes an f-string or concatenation), `test_store_has_no_secret_columns` (column-name denylist walk), `test_store_errors_hide_input` | 8 |
| P2-25 | SA | `packages/evaldb/STORAGE_POLICY.md` (parity table; "the DB is not a validation boundary, the repository is"; the limits), `CHANGELOG.md`, README, a concept page. One pointer sentence in SCHEMA_POLICY section 3 "Database". | Every statement is tested or marked (unverified). | `test_storage_policy_parity_table_matches_column_types`, `test_storage_policy_states_db_is_not_validation_boundary`, `test_evaldb_changelog_has_entry_for_version` | 8 |
| P2-26 | dev | `verify_database(engine)`: re-validates every `doc` through the models, recomputes projections and `doc_sha256`, and reports kind and id only. | Usable as an admin check. | `test_verify_database_clean_store_reports_nothing`, `test_verify_database_finds_projection_mismatch_without_echo`, `test_verify_database_finds_tampered_doc`; batch E (Decisions D6): `test_verify_database_api_and_types`, `test_verify_database_of_an_empty_store_reports_nothing_and_counts_zero`, `test_verify_database_clean_after_the_repository_deleted_things`, `test_verify_database_works_on_the_schema_alembic_builds`, `test_verify_database_reports_every_projected_column_by_name_only`, `test_verify_database_finds_a_changed_doc_sha256`, `test_verify_database_finds_a_versioned_doc_edited_with_a_consistent_digest`, `test_verify_database_finds_a_doc_the_strict_loader_refuses`, `test_verify_database_finds_a_run_bundle_envelope_that_is_not_one`, `test_verify_database_formatting_only_difference_is_informational`, `test_verify_database_finds_side_rows_that_do_not_match_their_document`, `test_verify_database_never_logs_or_raises_content` | 6 |
| P2-Q1..Q6 | qa | The red-first suites for batches A to F (evidence `docs/phase2/evidence/red/<batch>/` with oracle and mutant runs as in Phase 1). | Each suite is red against the tree before implementation, green after. | (all names above) | 8+12+10+8+8+6 = 52 |
| P2-G | staff-reviewer | Phase 2 gate. | Section 4. | n/a | 6 |

**Total: about 273 h (about 34 task-days).** With parallel agents this is about 10 to 12 working days. No cut list.

### 1.8 Entity to table mapping (the core of ADR 0019)

Every table has these common columns:
- `id` (uuid) as PK, or `(id, version)` for versioned tables, plus `version` CHECK >= 1;
- `schema_version` CHECK `='0.0.1'`;
- `created_at` (InstantCol);
- `doc` TEXT, the authoritative exact JSON;
- `doc_sha256`.

Versioned tables also have `content_digest_scheme` and `content_digest_value`. `metadata` is never projected.

The wire prefix is never stored; the column implies it (ADR 0001).

**Versioned entities**

| Entity | Table | Projections and side tables | FKs (on delete) |
|---|---|---|---|
| AgentTarget | `agent_target` | transport | none |
| Persona | `persona` | language | none |
| TestProfile | `test_profile` | none (no PII projected) | none |
| Evaluator | `evaluator` | persona id/version, test_profile id/version | none (references, Q9) |
| MetricDefinition | `metric_definition` | `key` (KeyCol), kind, value_type | none |
| MetricBinding | `metric_binding` | evaluator_id, metric id/version | none |
| Rubric | `rubric` | none | none |
| PricingCatalog | `pricing_catalog` | none (PriceEntry stays in `doc`; cost resolution reads the whole catalog) | none |
| Dataset | `dataset` | side table `dataset_member(dataset_id, dataset_version, ordinal, item_id)` | member to dataset CASCADE; `item_id` has no FK (unresolved members are reported) |

**Records of fact**

| Entity | Table | Projections and side tables | FKs (on delete) |
|---|---|---|---|
| Project | `project` | name | none |
| ConfigSnapshot | `config_snapshot` | digest scheme/value (index), evaluator/rubric/pricing-catalog id+version; side tables `snapshot_member(kind, def_id, def_version, ordinal)` and `snapshot_profile_ref` | members to snapshot CASCADE; none to definitions |
| Run | `run` | project_id, config_snapshot_id, agent_target id/version, mode, caller_origin, observed_tier, started_at, caller_external_id privacy columns | project CASCADE; snapshot RESTRICT |
| Artifact | `artifact` | run_id, sha256, turn_ref, privacy columns | run CASCADE |
| Transcript | `transcript` | run_id, source, detected_language, privacy columns | run CASCADE |
| Turn | `turn` | run_id, transcript_id, seq, role, start_ms (bigint) | run CASCADE; transcript CASCADE; UNIQUE(transcript_id, seq) |
| Exchange | `exchange` | run_id, index | run CASCADE |
| ToolCallEvent | `tool_call_event` | run_id, source | run CASCADE |
| StageSpan | `stage_span` | run_id, stage | run CASCADE |
| MetricResult | `metric_result` | run_id, metric id/version, status, na_reason, passed (NULL allowed) | run CASCADE |
| RunVerdict | `run_verdict` | run_id, status, the five counts | run CASCADE |
| UsageRecord | `usage_record` | run_id (native field), domain, source, occurred_at | run CASCADE |
| CostRecord | `cost_record` | run_id, status, basis, amount (DecimalCol), currency, na_reason | run CASCADE |
| CostSummary | `cost_summary` | run_id, completeness, total, currency | run CASCADE |
| DatasetItem | `dataset_item` | artifact_id, transcript_id, source_run_id, language, privacy columns | none on all three; application-level cascade (Q11) |
| RunBundle | `run_bundle` | run_id; `doc` holds only the envelope (`id`, `schema_version`, `created_at`, `metadata`, `metadata_flags`); side tables `run_bundle_member(kind, record_id, ordinal)` and `run_bundle_withheld(ordinal, kind, record_id)` | run CASCADE; members to bundle CASCADE |
| ExportBundle | `export_bundle` | purpose, byte_length; `doc` is the whole export; side table `export_bundle_run(export_id, run_id)` | link to export CASCADE; no FK to run (application-level cascade from `delete_run`) |

**What the DB enforces:** PK and `(id, version)` uniqueness; FK presence; no UPDATE (triggers); version >= 1; the version pin; projected closed sets; USD; non-negative cost amounts; UUIDv7 version nibble; `(transcript_id, seq)` uniqueness; privacy presence consistency.

**What stays in pydantic and the repository:** digests and HMAC; every bundle cross-record rule and applicability; I-JSON; text, bidi and credential rules; hiding input; per-bundle uniqueness of results (rescoring shares a run, so this cannot be a DB constraint); contiguity of indexes; and agreement between projections and `doc` (checked by the codec and by `verify_database`).

---

## 2. Dependency graph

**Internal:**
- 00a, 00b, 00c and 00d come before the batches they govern.
- 01 comes before everything. 02 runs in parallel with 01. 03 needs 01. 04 and 05 need 01.
- 05 comes before 06 to 10, which come before 11a and 11b. Those come before 12a, then 12b, then 12c. 13 needs 12b. 16 needs 10 and is re-checked by 15.
- 14 needs 10. 15 needs 14 and 16. 19 needs 14 and 11a.
- 17a needs 12b and 00b, then 17b. 18 needs 10.
- 20 needs 12b. 21 can start after 11b. 23 needs 12b. 26 needs 11b.
- 24 and 25 come last, then the gate.

Critical path: 00a, 01, 05, 06 to 10, 11a, 11b, 12b, 14, 15, 17a, 17b, 19, 20, 24, then the gate.

Phase 1 inputs: the gate commit `538c286` (pre-rewrite id `534c346`) (frozen 0.0.1 schema, 26 prefixes, schema.lock, golden and frozen fixtures, T-DEG bundles). The open Phase 1 conditions C4 and C8 and the owner decisions O1 to O5 do not block Phase 2. O3 (exact runtime pins against ranges) applies to evaldb's pins too; the owner decided it for bounded tested ranges (Decisions D2).

**External:**

| Item | Pin | Licence | Failure mode |
|---|---|---|---|
| SQLAlchemy | `>=2.1.6,<3` (2.1.6 is the latest on PyPI) | MIT | 2.1 API changes; Uuid DDL on SQLite (see risks) |
| Alembic | `>=1.20.1,<2` | MIT | batch mode loses STRICT or triggers (tested) |
| Mako, MarkupSafe | locked transitively | MIT (Mako); MarkupSafe licence read at P2-02 | none known |
| pg8000 | `>=1.31.5,<2`, extra `[postgres]` | BSD-3-Clause | slower than psycopg; type conversion edges (tested) |
| scramp | `1.4.18` | MIT-0 (already on the allowlist) | SCRAM auth (exercised in CI with a password) |
| asn1crypto, python-dateutil, six | transitive | to read at P2-02 (unverified) | none known |
| psycopg | rejected | `LGPL-3.0-only` | n/a |
| asyncpg | not used now | Apache-2.0 | possible second dialect later behind the same tests |
| sqlite3 | stdlib | n/a | needs >= 3.37 for STRICT (tested, never skipped) |
| PostgreSQL | 15 (floor; EOL 2027-11-11) and 18 (latest, 18.6); 14 reaches EOL 2026-11-12 and is excluded | n/a | image pinned by digest; digest not yet read |
| testcontainers | not used | Apache-2.0 | would add docker, python-dotenv, urllib3, wrapt and a Docker API dependency |
| MinIO, telephony, provider APIs | none in Phase 2 | n/a | n/a |

---

## 3. Open design questions (recommendation first; the lead auto-approves and logs)

- **Q1. Storage shape.**
  - **Recommended:** an authoritative `doc` TEXT column plus extracted projections.
  - Rejected, fully normalized columns (about 100 tables): order, null and float fidelity are at risk, user `metadata` cannot be normalized, and every additive field becomes DDL.
  - Rejected, PG `jsonb`: it rejects `\u0000` (VerbatimText allows it), reorders keys and rewrites `1e300` without the exponent, which then fails I-JSON on read (PostgreSQL 18 docs, section 8.14).
  - Rejected, PG `json` type: it keeps exact text, but its processing functions still fail on `\u0000`, and the column type would differ between backends.
- **Q2. Mapping layer.**
  - **Recommended:** SQLAlchemy Core `Table`s plus explicit typed codecs.
  - Rejected, ORM declarative classes: they duplicate the models, and the unit of work brings UPDATE semantics we forbid.
  - Rejected, imperative mapping onto pydantic models: these are frozen, hide their input and validate on construction.
- **Q3. API and driver.**
  - **Recommended:** a sync API with pg8000 (permissive, pure Python, works on 3.15).
  - Rejected, psycopg 3: it is LGPL-3.0-only, so it fails the permissive rule.
  - Rejected for now, asyncpg: it forces an async API before Phase 7 has shown it needs one. It can be added later as a second dialect against the same parity suite.
- **Q4. Run ownership for records without `run_id`.**
  - **Recommended:** a storage-only `run_id` column, taken from the bundle being stored (fact, not inference), with the FK cascade.
  - Rejected, ownership through bundle membership only: the cascade becomes indirect, and a record shared by two bundles would need reference counting.
  - Rejected, adding `run_id` to the schema: it contradicts N22 and the frozen 0.0.1 schema.
- **Q5. Write unit.**
  - **Recommended:** the whole validated RunBundle.
  - Rejected, per-record writes: they bypass `check_run_bundle`.
  - Streaming and staging tables are a Phase 5 design input, not built now.
- **Q6. Rescoring bundles.** **Recommended:** records deduplicated by ID with a `doc_sha256` comparison; a difference is `ConflictingRecordError`.
- **Q7. ExportBundle at rest.**
  - **Recommended:** an opaque document plus index and link rows.
  - Rejected, decomposition: export records are transformed copies (digests keyed or dropped, `declared_by` dropped, turns sealed) and collide by ID with internal records.
  - Object storage is Phase 3.
- **Q8. Definitions embedded in snapshots.**
  - **Recommended:** also inserted into the definition tables (insert if absent, verify if present), so "all versions of X" is queryable. A snapshot never depends on those rows.
  - Rejected: keeping them only inside the snapshot document.
- **Q9. References to versioned entities** (`Run.agent_target`, result and binding metric refs, snapshot members).
  - **Recommended:** no FK; an index plus a resolver that reports unresolved references (ADR 0009 consequence). An FK would demand rows that RunBundle does not carry (AgentTarget).
  - Rejected: FK with RESTRICT, and FK with CASCADE.
- **Q10. Snapshot lifetime.**
  - **Recommended:** RESTRICT from `run`; `delete_run` also deletes the snapshot when no other run references it (counted in the audit). Evaluator free text may hold personal data the lint cannot see.
  - Rejected: keeping orphaned snapshots forever.
- **Q11. Dataset items that reference run-owned artifacts or transcripts.**
  - **Recommended:** an application-level cascade in the same transaction. An FK is impossible because a synthetic item may reference an artifact that is not stored. The deletion right wins, and the Dataset then reports unresolved members.
  - Rejected: RESTRICT (benchmark integrity over deletion).
- **Q12. Audit content.**
  - **Recommended:** kind, when, who, reason, per-kind counts. No IDs (a UUIDv7 reveals creation time and links to external logs) and no digests (a plain digest of a low-entropy profile is content).
  - Rejected: storing the root ID, or an HMAC of it (which needs a key).
- **Q13. Actor.**
  - **Recommended:** a required `actor` label, credential-checked, with no default. Reason is a required closed set `{operator_request, retention_policy, data_subject_request}`.
  - Rejected: a default such as "system", because that is an assumption.
- **Q14. Immutability.**
  - **Recommended:** DB triggers on both backends.
  - Rejected: application-only enforcement, which raw SQL or a future ORM bug bypasses.
- **Q15. Schema version.**
  - **Recommended:** one version per database, pinned by CHECK and `tracer_meta`; an explicit `migrate` command and never an automatic one.
  - A move to 0.0.2 is a data migration that calls a pure evalschema upgrade function per entity. A content-preserving upgrade only rewrites `schema_version`, so digests hold.
  - An upgrade that changes the digest of a stored versioned entity is refused, because it would break the immutability of `(id, version)`. Such a change needs its own owner-level decision at that time.
  - Every data migration downgrades byte-exactly or raises a typed `IrreversibleMigration`.
  - Rejected: mixed versions per row, which needs several model versions in one interpreter.
- **Q16. Rebuilding a bundle after a member was deleted.**
  - **Recommended:** `load_run_bundle` returns `complete=False` and `missing[(kind, id)]`, and the partial bundle is only available with `allow_incomplete=True`.
  - Rejected: a silent partial bundle (two contents under one RunBundle ID), and a plain failure (the remaining data becomes unreachable).
- **Q17. Scope of external-ref uniqueness.**
  - **Recommended:** unique per entity kind for Run and StageSpan; non-unique and scoped by run for Transcript and UsageRecord provider call refs (one provider call may yield several records; not assumed otherwise). **Amended by Decisions D4:** unique for Run only; StageSpan keys are also non-unique and scoped by run.
  - Rejected: unique everywhere.
- **Q18. UUIDs on SQLite.**
  - **Recommended:** BLOB(16); its byte order equals v7 time order.
  - Rejected: TEXT(32) or TEXT(36): larger, case variants possible, and `CHAR(32)` (unverified as SQLAlchemy's SQLite DDL for `Uuid`) is not a STRICT type name.
- **Q19. SQLite STRICT and WITHOUT ROWID.**
  - **Recommended:** both. Without STRICT, NUMERIC affinity can coerce decimal text.
  - Cost: needs SQLite >= 3.37, which a test enforces.
- **Q20. Postgres in CI.**
  - **Recommended:** Postgres started in a job step with a generated, masked password (exercises pg8000's SCRAM path; no literal for gitleaks).
  - Rejected: testcontainers (more dependencies, Docker API from Python).
  - Rejected: a service container with `trust` auth (no auth path tested).
- **Q21. Matrix.** **Recommended:** Postgres {15, 18} crossed with Python {3.11, 3.14}, required; 3.15 crossed with Postgres 18, non-blocking; SQLite on every leg.
- **Q22. N18 (changing a language declaration after import).**
  - **Recommended:** a new Transcript record in a new RunBundle; the old record stays, so results that cite it keep their meaning. A `supersedes` link would be an additive 0.0.2 change.
  - Rejected: a versioned declaration, which edits a record of fact.
- **Q23. Storage limits.** **Recommended:** the loader's 256 MiB per document, checked before parsing; no per-field limit until measurement supports a number (P2-23).
- **Q24. Text projections for search.** **Recommended:** none in Phase 2, because Postgres text cannot hold NUL. A Phase 7 full-text design must decide how NUL is handled.
- **Q25. Form of `doc`.** **Recommended:** the exact `model_dump_json()` text, which is what the loader reads; `doc_sha256` is computed over JCS, which is the content identity.
- **Q26. Ownership of definitions by project.** **Recommended:** none invented. File finding **P2-F1**: an additive `project_id` on definitions for 0.0.2, decided before Phase 7.
- **Q27. B11 `verify_bundle_digests`.** **Recommended:** not Phase 2; it belongs to key management (reason in section 0).
- **Q28. SQLite FKs during migrations.** **Recommended:** OFF during the migration, then `foreign_key_check`. Otherwise a batch-mode table recreate can cascade-delete child rows.
- **Q29. Autogenerate.** **Recommended:** used only as a drift check (`compare_metadata` must be empty); migrations are hand-written and reviewed.
- **Q30. Tri-state values.** **Recommended:** nullable columns where NULL means absent or unknown. Nothing is ever coerced to 0 or false. `not_applicable` results keep `passed` NULL; `not_available` costs keep `amount` and `basis` NULL.

---

## 4. Gate checklist and CI changes

### 4.1 Gate (all must hold; evidence is hosted CI logs plus file paths)

1. **Tests:** hosted CI green on every required leg: SQLite on Python 3.11 to 3.14; Postgres 15 and 18 on Python 3.11 and 3.14. The 3.15 legs ran and their results are reported. Zero skips and zero xfails; `postgres`, `private_fixtures` and `local_only` are deselected, never skipped.
2. **Test names:** every test named in section 1 exists under its exact name, with red-first evidence under `docs/phase2/evidence/red/`.
3. **Round trip:** every golden valid fixture and every frozen 0.0.1 fixture round-trips on both backends, equal and byte-identical, with digests verifying.
4. **Tier degradation:** the T-DEG bundles round-trip on both backends. No `not_applicable` result has a projected 0 or false. No `not_available` cost has an amount or a basis.
5. **Migrations:** the stairway passes on both backends. `compare_metadata` is empty at head. Triggers and STRICT survive batch mode. Downgrade with data loss needs the explicit flag.
6. **Cascades:** every cascade test passes and the counts match between backends. The audit holds no content. Deletion is atomic with its audit row.
7. **Version policy:** `tracer_meta` and the version CHECK work. The fake 0.0.2 migration preserves digests and downgrades byte-exactly. A digest-changing upgrade is refused.
8. **Evalschema untouched:** `tracer-evalschema` is unchanged since the evaldb baseline (the rewritten-history tip when P2-01 scaffolds the package; Decisions D3) (schema.lock and source hash). Its runtime closure and import whitelist are unchanged. No evaldb import exists in evalschema.
9. **Lint and types:** `mypy --strict` and `ruff` are clean on `packages/evaldb`.
10. **Licences:** headers present; the licence scan covers both closures; the lock holds no GPL or LGPL package.
11. **Pins:** every runtime dependency is a bounded range, each tested at its lower bound (CI leg); `tracer-evalschema==0.0.1` stays exact; actions and the Postgres image pinned (SHA or digest).
12. **Security:** the P2-24 audit has zero blockers (no string-built SQL, no secret columns, no echo of URL or content, `expected_purpose` required at both export entry points).
13. **ADRs:** 0019 to 0022 are Accepted. Every question Q1 to Q30 is logged with rejected options. P2-F1 is filed.
14. **Docs:** STORAGE_POLICY, CHANGELOG and README are present. The parity table is tested.
15. **Indexes:** the index tests pass on both backends; measurements are recorded (no invented thresholds).
16. **Indic text:** byte-exact on both backends.
17. **Private fixtures:** the private-fixture guard and gitleaks pass. No key value appears in tree, history or CI config.

### 4.2 CI changes (`<repo>/.github/workflows/ci.yml`)

- **New job `test-postgres` (required):**
  - Matrix: python {3.11, 3.14} crossed with postgres {15, 18}, plus a non-blocking 3.15 crossed with 18.
  - A step starts `postgres:<major>@sha256:<digest>` with a password from `openssl rand -hex 24`, masked with `::add-mask::`, written to `$GITHUB_ENV`, and waits for readiness.
  - It creates a test database, a second database with `SQL_ASCII` (for the encoding refusal test) and sets a non-UTC database timezone for the timezone test.
  - It runs `uv run --locked --all-groups pytest -m postgres packages/evaldb/tests`.
  - Permissions stay `contents: read`.
- **Existing `test` and `test-py315` jobs:** run the SQLite suite through the testpaths; `addopts` gains `and not postgres`.
- **`lint`:** mypy `files` and ruff `src` include `packages/evaldb`; add `alembic check` against a fresh SQLite database; `schema_export --check` is unchanged.
- **Lower-bound leg (new, required):** resolves every evaldb runtime dependency at the lower bound of its range (SQLite suite, Python 3.11; plus Postgres 15 for pg8000), in the same way as the evalschema lower-bound leg.
- **`licenses`:** also builds the `tracer-evaldb` wheel and runs `--wheel` on it.
- **`audit`, `secrets`, `guard`:** unchanged; they cover the new lock and paths automatically.

---

## 5. Risks and measurement

- **Fidelity loss at rest.** NUL characters, float formatting, key order, years below 1000, timezone shifts (the PG session TimeZone and old local-mean-time offsets). Measured by the byte-identical round trip over all golden, frozen and adversarial fixtures, the NUL, `1e300`, year 0001/9999 and `Asia/Kolkata` tests, on both backends.
- **Projections drifting from `doc`.** A wrong index or filter would give wrong answers. Measured by the codec check on read, `verify_database`, and the hypothesis property test.
- **SQLite-specific traps:** the FK pragma is per connection; the 3.11 legacy transaction mode; batch mode dropping triggers or STRICT, or cascading during a table recreate. Each has a named test; the pragma test runs on every Python leg.
- **SQLAlchemy and Alembic details unverified by the reviewer:** the default `Uuid` DDL on SQLite; whether batch mode keeps `sqlite_strict`. Both are tested in P2-05 and P2-15. If either fails, the custom type or a re-creation hook is required, not a weaker table.
- **pg8000 performance** on a 4,000-turn bundle (executemany). Measured in P2-23 with the statement-count invariant (result: finding P2-F4, Decisions D6). If the numbers are bad, the remedy is a second permissive dialect behind the same parity suite, never a licence exception.
- **Deletion versus benchmark integrity.** Datasets and bundles become incomplete after a deletion. Mitigated by reporting unresolved members and by typed incomplete reconstruction, never a silent partial.
- **A future 0.0.2 that changes the digests of stored versioned entities** cannot be migrated in place (Q15). This risk is stated now so that schema changes are designed around it.
- **Raw SQL bypassing the repository.** The DB is not a validation boundary. Triggers block UPDATE, but raw INSERTs can still write rows the models would reject. STORAGE_POLICY says so, and `verify_database` detects them.
- **Statistics:** none apply in Phase 2. Timing numbers are recorded with machine, Python, SQLite and Postgres versions and the median of N runs (N stated), and they are not turned into pass thresholds.

### Not verified (to confirm in the named tasks)

| Item | Confirm in |
|---|---|
| SQLAlchemy `Uuid` SQLite DDL | P2-05 |
| `sqlite_strict` preserved by Alembic batch mode | P2-15 |
| The SQLite version shipped with uv's Python builds on every leg | P2-04 |
| Licences of markupsafe, asn1crypto, python-dateutil and six | P2-02 |
| The Postgres image digests | P2-03 |
| pg8000 behaviour with `timestamptz` at year 0001 and with unconstrained `NUMERIC` scale | P2-05 |

**Relevant files:**
- <repo>/docs/HANDOFF.md
- <repo>/docs/phase1/SCOPE.md
- <repo>/packages/evalschema/SCHEMA_POLICY.md
- <repo>/docs/adr/0001-ids.md
- <repo>/docs/adr/0002-time-and-units.md
- <repo>/docs/adr/0009-entity-versioning.md
- <repo>/packages/evalschema/src/tracer_evalschema/bundle.py
- <repo>/packages/evalschema/src/tracer_evalschema/run.py
- <repo>/packages/evalschema/src/tracer_evalschema/project.py
- <repo>/packages/evalschema/src/tracer_evalschema/transcript.py
- <repo>/packages/evalschema/src/tracer_evalschema/privacy.py
- <repo>/packages/evalschema/src/tracer_evalschema/text.py
- <repo>/docs/security/license-policy.toml
- <repo>/.github/workflows/ci.yml
- <repo>/pyproject.toml
