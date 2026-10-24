# ADR 0020: Deletion roots, cascade graph, deletion audit and retention data

- Status: Accepted (Phase 2 batch 0, task P2-00b). Records the questions Q10, Q11, Q12, Q13, Q14 and the deletion side of Q16 of `docs/phase2/SCOPE.md` section 3, auto-approved by the lead on the recommended option under the owner's standing rule of 2026-10-23 (decision log "PHASE 2 SCOPE delivered"), and the owner's deletion decision in the decision log (deletion is allowed; `ON DELETE CASCADE` or an equivalent; run-owned facts cascade from the run; definitions embedded in snapshots are safe to delete because snapshots embed them; a minimal deletion audit of what kind, when and who, with no content). The points where the SCOPE was ambiguous are listed under "Ambiguities resolved" with the recommendation the lead auto-approves. Open [Proposed] items: none.
- Date: 2026-10-24
- Task: P2-00b (implemented by P2-10, P2-16, P2-17a, P2-17b, P2-18)

## Context
Stored data includes personal data: recordings and transcripts of human callers, test profiles with identity fields, transport identifiers, and free text that a lint cannot fully check (ADR 0006 Decision 9). The owner allows deletion, including cascades. Records of fact are immutable (ADR 0009): a deletion removes rows and never edits one. A deletion must therefore reach every stored copy of what it deletes (bundle membership, dataset items that point at a run's payloads, stored exports), leave no row that refers to deleted content in a way that changes its meaning, and leave an audit row that proves the deletion without keeping any of the content.

Facts in the code that drive the design: a `Dataset` version lists `item_ids` and a `DatasetItem` names no dataset (N25); `DatasetItem.source_run_id` is provenance only (O7); a `ConfigSnapshot` embeds its definitions and holds only an `{id, version, content_digest}` reference to its test profile (ADR 0006 Decision 1); an internal `ExportBundle` may carry `TestProfile` entities in the clear in `RunBundle.test_profiles`, and every export may carry a sealed `test_profile` payload (ADR 0006 Decision 8, ADR 0015).

## Decision
1. **No `SET NULL` and no `SET DEFAULT`, anywhere.** Either would edit a record of fact. Every foreign key is `CASCADE` or `RESTRICT`, and every edge without a foreign key is handled by an application-level cascade of Decision 4 or reported as unresolved by a reader (ADR 0019 Decision 7).
2. **Deletion roots.** A deletion starts at one root and runs in one transaction with its audit row. Each root function takes the root's ID and the keyword-only, required arguments `actor` and `reason` (no defaults; omitting one is a `TypeError`).

   | Root function | Deletes | `root_kind` |
   |---|---|---|
   | `delete_run(run_id)` | the run, every run-owned record, its bundles and their member rows, its external-ref keys; the application-level cascades of Decision 4a | `run` |
   | `delete_project(project_id)` | the project and every run of it, with the cascades of `delete_run` for each run (Decision 4b) | `project` |
   | `delete_test_profile(profile_id)` | every version of the test profile; the stored exports that carry it (Decision 4c) | `test_profile` |
   | `delete_definition(kind, def_id)` | every version of one definition; `kind` is one of `agent_target`, `persona`, `evaluator`, `metric_definition`, `metric_binding`, `rubric`, `pricing_catalog`, `dataset` (a test profile goes through `delete_test_profile`) | the `kind` |
   | `delete_dataset_item(item_id)` | the item; the stored exports that embed it (Decision 4d) | `dataset_item` |
   | `delete_export(export_id)` | the stored export and its link rows | `export_bundle` |
   | `delete_snapshot(snapshot_id)` | a snapshot that no run references, with its member rows; refused with a typed error while a run references it (`RESTRICT`) | `config_snapshot` |

   An ID that names no stored row is a typed error, raised before anything is deleted, and no audit row is written. A RunBundle, a record of fact other than the run, and a single version of a versioned entity are not roots: records of fact go with their run, and versions with their entity.
3. **The foreign-key graph.** Machine-read by `test_adr0020_cascade_graph_matches_metadata` and `test_fk_graph_matches_adr0020`: these 23 rows are every foreign key in `MetaData`, with its `ondelete`.

   | # | Child table | Column(s) | Parent table (column(s)) | On delete | Why |
   |---|---|---|---|---|---|
   | 1 | `run` | `project_id` | `project` (`id`) | CASCADE | a project's runs are its data; deleting a project deletes them (Decision 4b adds the application-level cascades) |
   | 2 | `run` | `config_snapshot_id` | `config_snapshot` (`id`) | RESTRICT | a snapshot is shared by runs; it is deleted only when no run references it (Q10, Decision 4a) |
   | 3 | `snapshot_member` | `snapshot_id` | `config_snapshot` (`id`) | CASCADE | derived rows of the snapshot document |
   | 4 | `snapshot_profile_ref` | `snapshot_id` | `config_snapshot` (`id`) | CASCADE | derived row of the snapshot document |
   | 5 | `external_ref_index` | `run_id` | `run` (`id`) | CASCADE | re-import keys of the run's records |
   | 6 | `artifact` | `run_id` | `run` (`id`) | CASCADE | run-owned record (storage-only `run_id`, ADR 0019 Decision 8) |
   | 7 | `transcript` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 8 | `turn` | `run_id` | `run` (`id`) | CASCADE | run-owned record (no FK to `transcript`, ADR 0019 Ambiguity 1) |
   | 9 | `exchange` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 10 | `tool_call_event` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 11 | `stage_span` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 12 | `metric_result` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 13 | `run_verdict` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 14 | `usage_record` | `run_id` | `run` (`id`) | CASCADE | run-owned record (native `run_id`) |
   | 15 | `cost_record` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 16 | `cost_summary` | `run_id` | `run` (`id`) | CASCADE | run-owned record |
   | 17 | `run_bundle` | `run_id` | `run` (`id`) | CASCADE | a bundle is the unit of its run |
   | 18 | `run_bundle_member` | `bundle_id` | `run_bundle` (`id`) | CASCADE | derived rows of the bundle |
   | 19 | `run_bundle_withheld` | `bundle_id` | `run_bundle` (`id`) | CASCADE | derived rows of the bundle |
   | 20 | `dataset_member` | `dataset_id`, `dataset_version` | `dataset` (`id`, `version`) | CASCADE | derived rows of the dataset version |
   | 21 | `export_bundle_run` | `export_id` | `export_bundle` (`id`) | CASCADE | link rows of the export |
   | 22 | `export_bundle_item` | `export_id` | `export_bundle` (`id`) | CASCADE | link rows of the export |
   | 23 | `export_bundle_profile` | `export_id` | `export_bundle` (`id`) | CASCADE | link rows of the export |

   Edges with no foreign key, by design: references to versioned entities (ADR 0019 Decision 7); `turn.transcript_id` and `artifact.turn_ref` (a bundle may withhold the target); `dataset_item.artifact_id`, `transcript_id` and `source_run_id` (a synthetic item may reference an artifact that is not stored, Q11); `dataset_member.item_id` (a dataset reports deleted items as unresolved); `run_bundle_member.record_id` (polymorphic; a reader reports a deleted member, Q16); the second column of the three export link tables (the export is an opaque copy, Decision 4); `deletion_audit` and `tracer_meta` (no references).
4. **Application-level cascades.** They run inside the root's transaction, after the root's lock (Decision 5) and before the root row is deleted, and each deleted row is counted in the audit.
   - **4a. `delete_run`.**
     - The dataset items whose `artifact_id` or `transcript_id` names an artifact or transcript of the run are deleted (Q11), each with the stored exports that embed it (Decision 4d). An item whose only link to the run is `source_run_id` is kept: `source_run_id` is provenance, not membership (O7), and the item's payload belongs to another run or to no stored run.
     - The stored exports linked to the run in `export_bundle_run` are deleted.
     - The run row is deleted; the foreign keys of Decision 3 remove its records, bundles, member rows and external-ref keys.
     - The run's snapshot is deleted, with its member rows, when no other run references it (Q10). A snapshot can hold personal data in evaluator free text that the lint cannot see (ADR 0006 Decision 9). The definitions extracted from it into the definition tables (ADR 0019 Decision 9) stay; they are deleted with `delete_definition`.
   - **4b. `delete_project`.** The cascades of 4a run for every run of the project, then the project row is deleted (FK 1 removes the runs).
   - **4c. `delete_test_profile`.** Every version is deleted. The stored exports linked in `export_bundle_profile` are deleted, because an internal export carries the profile in the clear and any export may carry it sealed. Snapshots keep their reference (`snapshot_profile_ref` and the snapshot document) and stay valid; a reader reports the reference as unresolved (ADR 0009 Consequences). Run bundles that listed the profile in `test_profiles` load as incomplete (ADR 0022 Decision 8).
   - **4d. `delete_dataset_item`.** The item is deleted, and the stored exports linked in `export_bundle_item` are deleted. Datasets that list the item keep their versions (immutable, digest over `item_ids`) and report the member as unresolved.
   - **4e. `delete_definition`.** Every version is deleted; for `dataset`, FK 20 removes the member rows. Snapshots that embed the definition stay valid, because they embed it (the owner's decision). Results, bindings and verdict checks that reference it report the reference as unresolved. Run bundles that listed a deleted pricing catalog load as incomplete.
5. **Transaction and locks.** A root runs in one transaction: on Postgres it first takes `SELECT ... FOR UPDATE` on the root row and, for `delete_run` and `delete_project`, on each run row and each run's snapshot row, then reads the dependents; on SQLite the transaction starts with `BEGIN IMMEDIATE`, which serializes writers. Writers that add rows that a deletion must find take the matching lock: `store_run_bundle` relies on the foreign keys (Postgres takes `FOR KEY SHARE` on the project and run rows they reference), and `store_export` requires every run, dataset item and test profile that the export carries to be stored and takes `FOR KEY SHARE` on those rows before inserting its link rows. A writer waiting on a deletion's lock then fails on the missing parent instead of leaving an orphan or an unlinked copy.
6. **The deletion audit row (Q12, Q13).** `deletion_audit` has exactly these columns:
   - `audit_uuid` (`UuidCol`): a UUIDv7 minted by the store, without a prefix, never exported and never shown as an entity ID;
   - `deleted_at` (`InstantCol`): the commit time of the deletion, from the store's clock;
   - `actor` (`KeyCol`): a required label naming who asked, with no default; it follows the `HumanLabel` rules (non-empty, no control, separator or default-ignorable character) and is refused when `credentials.credential_reason` flags it; the error names the rule, never the value;
   - `reason` (`KeyCol`): required, the closed set `operator_request`, `retention_policy`, `data_subject_request`;
   - `root_kind` (`KeyCol`): the closed set of the `root_kind` column of Decision 2;
   - `counts` (`DocCol`): the JCS text of one JSON object from table name to the integer number of entity rows deleted. Its keys are exactly the reach set of the root kind (table below), zeros included, so an absent key never stands for zero. Side-table rows are not counted.

   The row holds no ID, no digest, no text, no metadata and no projection of a deleted record (a UUIDv7 reveals its creation time and links to external logs; a plain digest of a low-entropy profile is content). It is inserted in the root's transaction, so the deletion and its audit commit or roll back together.

   | `root_kind` | Keys of `counts` |
   |---|---|
   | `run` | `run`, `artifact`, `transcript`, `turn`, `exchange`, `tool_call_event`, `stage_span`, `metric_result`, `run_verdict`, `usage_record`, `cost_record`, `cost_summary`, `run_bundle`, `config_snapshot`, `dataset_item`, `export_bundle` |
   | `project` | `project` and every key of `run` |
   | `test_profile` | `test_profile`, `export_bundle` |
   | `agent_target`, `persona`, `evaluator`, `metric_definition`, `metric_binding`, `rubric`, `pricing_catalog`, `dataset` | the root's own table |
   | `dataset_item` | `dataset_item`, `export_bundle` |
   | `export_bundle` | `export_bundle` |
   | `config_snapshot` | `config_snapshot` |
7. **Immutability (Q14).** Every table of ADR 0019 Tables 1 and 2 has a `BEFORE UPDATE` trigger that raises, on both backends, with a message that names the table and no value. `deletion_audit` also has a `BEFORE DELETE` trigger: audit rows are append-only (Ambiguity 6). Alembic's `alembic_version` has no trigger (ADR 0019 Ambiguity 9). Migrations that must rewrite rows follow ADR 0021 Decision 9.
8. **Retention data, not enforcement.** `artifact`, `transcript` and `dataset_item` carry the privacy columns of ADR 0019 Decision 6a, and `run` carries them with the `caller_` prefix. Each `retain_until` column has a partial index on its non-NULL values, so a later sweeper can find expired rows. Phase 2 ships no sweeper, no scheduled job and no code that reads `delete_on_expiry`: the owner's rule is data only. NULL means absent; it is never read as false or as "keep forever".
9. **Parity.** Every root deletes the same rows and writes the same `counts` on SQLite and Postgres.

## Ambiguities resolved
- **Ambiguity 1. `delete_project` and the application-level cascades.** FK 1 cascades runs, but a foreign-key cascade runs no application code, so orphaned snapshots, dataset items and exports would survive. **Recommendation:** `delete_project` runs Decision 4a for each run before deleting the project row (Decision 4b).
- **Ambiguity 2. Which dataset items `delete_run` removes.** **Recommendation:** items whose `artifact_id` or `transcript_id` names a record of the run; `source_run_id` alone does not make an item part of the run (O7). Rejected: also deleting items linked only by `source_run_id` (it deletes data that is not the run's).
- **Ambiguity 3. What `delete_test_profile` leaves behind.** The test name `test_delete_test_profile_removes_all_versions_snapshots_report_unresolved` is read as: all versions removed, snapshots kept and reporting the reference unresolved. Snapshots keep the plain profile digest in their document, the known limitation of ADR 0006 Decision 6 (a low-entropy profile can be guessed offline from it); the store cannot remove it without editing an immutable snapshot. **Recommendation:** keep that reading, delete the exports that carry the profile (Decision 4c), and state the residual digest in STORAGE_POLICY. Rejected: deleting the snapshots (RESTRICT would also force deleting every run that used the profile).
- **Ambiguity 4. Exports beyond runs.** **Recommendation:** `export_bundle_item` and `export_bundle_profile` (ADR 0019 Ambiguity 8), and `store_export` requires the runs, items and profiles it carries to be stored (Decision 5), so a deletion always finds every stored copy. Rejected: storing exports of unknown runs (a deleted run could reappear inside an export stored after its deletion).
- **Ambiguity 5. A snapshot that no run references.** `store_snapshot` can store one, and only `delete_run` deletes snapshots. **Recommendation:** the root `delete_snapshot`, refused while a run references it. Proposed test name for the lead to add to the P2-17a row: `test_delete_snapshot_refused_while_a_run_references_it`.
- **Ambiguity 6. Deleting audit rows.** **Recommendation:** no API deletes an audit row, and a `BEFORE DELETE` trigger refuses raw deletes (Decision 7); the audit holds no content, so keeping it costs no privacy. Proposed test name for the P2-17b row: `test_deletion_audit_rows_cannot_be_deleted`.
- **Ambiguity 7. The shape of `counts`.** **Recommendation:** a fixed key set per root kind with zeros (Decision 6), in JCS text, so `test_deletion_audit_counts_per_kind_exact` compares exact objects.
- **Ambiguity 8. `delete_definition` and test profiles.** **Recommendation:** `delete_definition` refuses `kind="test_profile"` with a `ValueError` naming `delete_test_profile`, because only that root also deletes the exports that carry the profile.

## Rejected alternatives
- **Keeping orphaned snapshots forever (Q10).** Evaluator free text may hold personal data the lint cannot see.
- **RESTRICT from dataset items to run-owned payloads (Q11).** It puts benchmark integrity above the deletion right; a synthetic item may also reference an artifact that is not stored, so a foreign key is impossible.
- **The root ID, or an HMAC of it, in the audit (Q12).** A UUIDv7 reveals its creation time and links to external logs; an HMAC needs key management Phase 2 does not have.
- **A default actor such as `system` (Q13).** It is an assumption about who deleted.
- **Application-only immutability (Q14).** Raw SQL or a future ORM bug would bypass it.
- **`SET NULL` on the references of dataset items or bundle members.** It edits a record of fact.
- **A sweeper that enforces `retain_until`.** Out of scope by the owner's rule (data only).

## Consequences
- After a deletion, datasets, snapshots and run bundles can name rows that no longer exist; readers report them (ADR 0022 Decision 8), never fail silently and never rebuild a different content.
- Deleting a run can delete stored exports that also held other runs; the audit counts them.
- The audit proves that a deletion happened, when, by whom and how much, and nothing about what.

## Invariants (testable)
- I1. No foreign key uses `SET NULL` or `SET DEFAULT`, and the graph equals Decision 3. Tests: `test_no_fk_uses_set_null_or_set_default`, `test_fk_graph_matches_adr0020`, `test_adr0020_cascade_graph_matches_metadata`.
- I2. `delete_run` removes every run-owned kind, the run's bundles, the dataset items that reference its payloads and the stored exports that contain it, and the snapshot only when no other run uses it. Tests: `test_delete_run_cascades_every_run_owned_kind`, `test_delete_run_removes_orphaned_snapshot_only`, `test_delete_run_removes_dataset_items_and_dataset_reports_unresolved`, `test_delete_run_removes_stored_exports_containing_it`.
- I3. `delete_project` cascades its runs. Test: `test_delete_project_cascades_runs`.
- I4. `delete_test_profile` removes all versions; snapshots stay and report the reference unresolved. Test: `test_delete_test_profile_removes_all_versions_snapshots_report_unresolved`.
- I5. `delete_definition` leaves the snapshots that embed the definition valid. Test: `test_delete_definition_leaves_snapshots_valid`.
- I6. Both backends delete the same counts. Test: `test_cascade_counts_parity_sqlite_postgres`.
- I7. An unknown root is a typed error and writes no audit row. Test: `test_delete_unknown_root_is_typed_error_and_writes_no_audit`.
- I8. A concurrent insert during a run deletion never leaves an orphan (Postgres). Test: `test_concurrent_insert_during_run_delete_never_orphans`.
- I9. A deletion and its audit row are atomic. Test: `test_delete_is_atomic_with_audit`.
- I10. The audit row holds no ID, uuid hex, text, digest or metadata value of the deleted records, and its counts are exact. Tests: `test_deletion_audit_is_content_free`, `test_deletion_audit_counts_per_kind_exact`.
- I11. `actor` and `reason` are required, without defaults, and a credential-shaped actor is refused. Tests: `test_deletion_requires_actor_and_reason`, `test_deletion_actor_rejects_credential_shaped_value`.
- I12. No UPDATE succeeds on any table, and the triggers exist after every migration. Tests: `test_update_rejected_on_every_table`, `test_immutability_triggers_exist_after_migrations`.
- I13. Retention columns mirror the privacy marker, absent is NULL, and `retain_until` is indexed. Tests: `test_retention_columns_mirror_privacy_marker`, `test_retention_absent_is_null_never_false`, `test_retain_until_index_exists`.
- I14. A bundle that lost a member loads as incomplete, never as a silent partial. Test: `test_run_bundle_load_reports_incomplete_after_member_deletion`.
