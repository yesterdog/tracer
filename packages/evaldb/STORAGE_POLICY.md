# Storage policy: tracer-evaldb 0.0.1

## 1. What this document is

This file states the storage rules of `tracer-evaldb` 0.0.1, and every sentence of sections 1 to 9
cites a test that checks it, is marked as not verified, or only points to another file
(`test_every_statement_of_the_storage_policy_is_tested_or_marked_unverified`).

Section 10 repeats, word for word and in order, every sentence that is marked as not verified
(`test_the_register_of_unverified_statements_matches_the_marked_sentences`).

See `packages/evaldb/README.md`, `packages/evaldb/CHANGELOG.md` and `docs/concepts/storage.md`.

See `docs/adr/0019-relational-storage.md`, `docs/adr/0020-deletion-and-retention.md`,
`docs/adr/0021-store-schema-version-and-migrations.md` and
`docs/adr/0022-write-unit-rescoring-reimport.md`.

The package stores the entities of `tracer-evalschema` 0.0.1 in SQLite or Postgres, and a record
that the models reject is never stored by its functions (`test_invalid_run_bundle_never_stored`).

A cited test shows its statement on SQLite in the default selection and on Postgres 15 and 18
in the CI legs marked `postgres` (`test_ci_postgres_job_matrix_and_pinned_image`,
`test_ci_sqlite_runs_on_every_python_leg`).

That each cited test really supports the sentence that cites it is checked by a reviewer and not
by a test (unverified).

## 2. The parity table

Both backends store the same value the same way, and this parity table is compared with the DDL
type that SQLAlchemy writes on each dialect, with every column of the 37 tables and with what the
database under test reports (`test_storage_policy_parity_table_matches_column_types`).

| Logical type | Postgres | SQLite (STRICT) |
|---|---|---|
| `UuidCol` | `uuid` | `BLOB` |
| `InstantCol` | `timestamptz` | `TEXT` |
| `DecimalCol` | `numeric` | `TEXT` |
| `DocCol` | `text` | `TEXT` |
| `Sha256Col` | `bytea` | `BLOB` |
| `KeyCol` | `varchar COLLATE "C"` | `TEXT COLLATE "BINARY"` |
| `BigIntCol` | `bigint` | `INTEGER` |
| `BoolCol` | `boolean` | `INTEGER` |

See `docs/adr/0019-relational-storage.md` for the Python value, the CHECK constraints and the
tests of each logical type.

- The Postgres type of `KeyCol` is `varchar COLLATE "C"` without a length, which behaves as
  `text`, and both backends compare its values by code point, so case variants and the NFC and
  NFD forms of a text stay distinct and sort alike (`test_key_column_order_parity`,
  `test_case_distinct_keys_distinct_on_both_backends`, `test_nfc_nfd_distinct_on_both_backends`).
- A UUID is a native `uuid` on Postgres and 16 bytes on SQLite, and sorts in UUIDv7 time order on
  both (`test_uuid_column_native_on_postgres_blob16_on_sqlite`,
  `test_uuid_column_sorts_in_v7_time_order`).
- An instant is stored in UTC on both backends, also when the Postgres server runs in another time
  zone (`test_instant_round_trip_under_non_utc_server_timezone`).
- A decimal round-trips exactly, and SQLite keeps it as text (`test_decimal_round_trip_exact`,
  `test_sqlite_decimal_stored_as_text`).
- An absent boolean is NULL on both backends and never false (`test_bool_tristate_null_never_false`).
- Indic text is stored and read back byte for byte, with no normalization by a driver or by the
  database (`test_indic_text_round_trip_byte_exact`, `test_indic_text_no_normalization_by_driver`).
- Every SQLite table is STRICT and WITHOUT ROWID (`test_sqlite_tables_are_strict_without_rowid`).

## 3. The database is not a validation boundary

The DB is not a validation boundary, the repository is
(`test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`).

- A raw INSERT of a row whose document the models reject, or whose `doc_sha256` does not match its
  document, is accepted by the database, because its constraints check the shape of single columns
  and not the schema
  (`test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`).
- The repository refuses to load such a row with `StoreIntegrityError`
  (`test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`).
- Every write of the repository loads its argument again through the strict Phase 1 loader and
  checks every record before the first INSERT (`test_store_functions_revalidate_instances_built_without_validation`,
  `test_invalid_run_bundle_never_stored`).
- Every read validates the stored document again and compares each projected column with it,
  naming the column and never the value (`test_db_read_uses_strict_loader`,
  `test_db_tampered_doc_rejected_on_read`,
  `test_db_projection_tamper_rejected_naming_the_column_not_the_value`).
- The immutability triggers refuse every UPDATE of a stored row on all 37 tables and every DELETE
  of a row of `deletion_audit`, and they do not stop an INSERT (`test_update_rejected_on_every_table`,
  `test_deletion_audit_rows_cannot_be_deleted`,
  `test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`).
- A raw DELETE of any other row is not refused by a trigger
  (`test_delete_is_allowed_on_every_table_except_deletion_audit`).
- `verify_database` is the admin check for rows that bypassed the repository: it validates every
  document again, recomputes `doc_sha256`, the projections and the side-table rows, and reports the
  kind and the ID of a damaged row, never a value (`test_verify_database_finds_tampered_doc`,
  `test_verify_database_finds_projection_mismatch_without_echo`,
  `test_verify_database_never_logs_or_raises_content`).
- The Phase 1 label fields `Project.name`, `policy_ref` and `ToolCallEvent.name` accept a text
  that has the shape of a credential, so the store can hold one; this is finding P2-F5 for schema
  0.0.2 (unverified).

## 4. Limits

- A stored integer lies within plus or minus 2**53 - 1 (9007199254740991), the range the JCS
  digest accepts, and a bundle with a larger value is refused with `StoreValueOutOfRange` before
  any write (`test_store_run_bundle_refuses_an_integer_it_cannot_store_before_any_write`,
  `test_encode_refuses_values_beyond_64_bits_with_a_typed_error`).
- The column type `BigIntCol` holds the signed 64-bit range on both backends, so the 2**53 - 1
  ceiling is a rule of the package and not of the database
  (`test_bigint_column_round_trips_the_signed_64_bit_range`).
- SQLite needs library version 3.37 or newer, and `create_store_engine` refuses an older library
  before it connects (`test_sqlite_version_at_least_3_37`).
- Postgres 15 and Postgres 18 are the tested servers (`test_ci_postgres_job_matrix_and_pinned_image`).
- A Postgres database must have the UTF8 server encoding, and the engine refuses any other
  (`test_postgres_non_utf8_database_refused`).
- U+0000 is held only inside a document, as its JSON escape, and a projected text column refuses it
  (`test_db_nul_in_verbatim_text_round_trips`, `test_key_column_rejects_nul_and_lone_surrogates`).
- Instants from the year 0001 to the year 9999 round-trip on both backends
  (`test_instant_round_trip_edges`).
- An export document is at most 256 MiB, the default limit of the strict loader, and a larger one
  is refused before it is parsed (`test_export_size_limit_enforced_before_parse`).
- The stored `doc` text need not equal the canonical `model_dump_json()` text byte for byte, and
  a difference of formatting only is the informational note `doc_text_not_canonical` of
  `verify_database`, not a finding (`test_verify_database_formatting_only_difference_is_informational`).
- Both engines of `create_store_engine` hide bound parameters in errors
  (`hide_parameters=True`), and the URL is never echoed in an error or a log
  (`test_engine_hides_bound_parameters_in_errors`, `test_database_url_never_echoed_in_errors_or_logs`).
- No error of the store, no formatted traceback of it and no log record of `sqlalchemy.engine` at
  INFO holds document text or a parameter value (`test_store_errors_hide_input`).
- On Postgres the engine raises every error of SQLSTATE class 23 as
  `sqlalchemy.exc.IntegrityError` and removes the failing row from it and from every driver error
  along its exception chain (`test_translated_integrity_errors_carry_no_row_content`,
  `test_untranslated_integrity_error_chain_text_holds_no_row_detail`,
  `test_driver_error_args_along_the_chain_are_scrubbed`).
- Only SQLSTATE class 23 is scrubbed: an error of another class raised by a caller's own statement,
  such as a failed cast of class 22, can quote a value in its message (unverified).
- The store functions refuse an engine that does not hide parameters with `UnsafeEngine` before
  any statement, and accept the engine of `create_store_engine` and engines derived from it
  (`test_store_functions_refuse_an_engine_that_does_not_hide_parameters`,
  `test_store_functions_accept_the_engine_of_create_store_engine`).
- `open_store` and the functions of `tracer_evaldb.migrate` do not have that guard (unverified).
- Do not enable DEBUG logging of `sqlalchemy.engine` or `sqlalchemy.pool` where the rows are
  personal data, because at DEBUG SQLAlchemy logs result rows and the pool logs the host of a
  failed connection (unverified).

## 5. Encryption at rest

Tracer does not encrypt the database at rest: a SQLite file holds every stored document, name and
metadata text as readable bytes (`test_the_sqlite_file_holds_documents_in_plain_text`).

The store holds internal data, and internal data is personal data: a stored `TestProfile`, with a
person's name, date of birth and account number, is readable in clear in the file
(`test_the_sqlite_file_holds_documents_in_plain_text`).

`create_store_engine` has no parameter for a key, a password or a cipher, so the package has no
encryption to switch on (`test_the_sqlite_file_holds_documents_in_plain_text`).

- Protect the database with disk or volume encryption and with operating system permissions, and
  treat every copy and backup of it as the data itself (unverified).
- A new SQLite database file is created with mode 0600, readable and writable by its owner only,
  whatever the process umask (`test_a_new_sqlite_file_is_created_with_mode_0600`,
  `test_a_new_sqlite_file_by_relative_path_has_mode_0600`).
- An existing file keeps its mode, also one that the package created and the owner changed
  (`test_an_existing_empty_file_keeps_its_mode`, `test_an_existing_database_keeps_its_mode_and_its_data`,
  `test_reopening_a_file_the_package_made_does_not_change_a_mode_the_owner_set`).
- The mode of the `-wal`, `-shm` and `-journal` files that SQLite creates next to the database is
  not checked by a test (unverified).
- On Postgres the storage of the server, its disks, backups and replicas belongs to the operator,
  and nothing in the package or its CI can see whether it is encrypted (unverified).
- The sealed payload of an export is stored unchanged, so its ciphertext stays sealed at rest
  (`test_export_sealed_payload_ciphertext_unchanged_at_rest`).

## 6. Digests and the plain profile digest

ADR 0006 Decision 6 holds in the store: a plain SHA-256 digest of a low-entropy test profile can
be recomputed by brute-forcing likely values, so the plain profile digest is itself personal data
(`test_a26_a_plain_digest_of_a_low_entropy_profile_can_be_recomputed_by_guessing`,
`test_policy_documents_low_entropy_digest_limitation`).

The store keeps internal data, and an internal snapshot carries the plain profile digest, so a
snapshot whose profile reference has an absent or keyed digest is refused with
`InternalSnapshotRefused` (`test_store_snapshot_refuses_absent_or_keyed_profile_digest`,
`test_store_run_bundle_refuses_absent_or_keyed_profile_digest`).

The plain digest is held in the snapshot document, and it stays there after `delete_test_profile`,
because a snapshot keeps its profile reference and reports it as unresolved
(`test_delete_test_profile_removes_all_versions_snapshots_report_unresolved`).

A snapshot that no run references is removed with its member rows by `delete_snapshot`, and with
it the plain digest it holds (`test_delete_snapshot_removes_an_unreferenced_snapshot_with_its_member_rows`).

A shared export drops the profile digest or replaces it with a keyed HMAC digest, so a shared file
never carries the plain digest (`test_shared_export_drops_or_keys_profile_digest`).

## 7. Deletion and audit

- The seven deletion roots are `delete_run`, `delete_project`, `delete_test_profile`,
  `delete_definition`, `delete_dataset_item`, `delete_export` and `delete_snapshot`, and each runs
  in one transaction with its audit row (`test_deletion_api_shape`, `test_delete_is_atomic_with_audit`).
- `delete_run` deletes the run and every record it owns through the foreign key cascade, its
  snapshot when no other run uses it, the dataset items made from its artifacts or transcripts and
  the stored exports that contain it (`test_delete_run_cascades_every_run_owned_kind`,
  `test_delete_run_removes_orphaned_snapshot_only`,
  `test_delete_run_removes_dataset_items_and_dataset_reports_unresolved`,
  `test_delete_run_removes_stored_exports_containing_it`).
- `delete_project` applies the run cascade to every run of the project first
  (`test_delete_project_applies_the_run_cascades_to_every_run_first`).
- `delete_test_profile` deletes every version of the profile and the stored exports that carry it
  (`test_delete_test_profile_removes_all_versions_snapshots_report_unresolved`).
- `delete_definition` deletes every version of one definition, refuses a test profile and leaves
  the snapshots that embed the definition valid (`test_delete_definition_removes_every_version_and_only_that_id`,
  `test_delete_definition_refuses_test_profile_and_names_the_right_root`,
  `test_delete_definition_leaves_snapshots_valid`).
- `delete_dataset_item` also deletes the exports that embed the item, and `delete_export` leaves
  what the export carried (`test_delete_dataset_item_removes_the_exports_that_embed_it`,
  `test_delete_export_leaves_what_it_carried`).
- `delete_snapshot` is refused with `SnapshotInUse` while a run references the snapshot
  (`test_delete_snapshot_refused_while_a_run_references_it`).
- A run bundle that lists a deleted test profile or pricing catalog loads as `IncompleteRunBundle`,
  or with `allow_incomplete=True` as the remaining records and the missing members
  (`test_run_bundle_load_reports_incomplete_after_member_deletion`,
  `test_delete_pricing_catalog_makes_the_run_bundle_incomplete`).
- A deletion that fails changes nothing and writes no audit row
  (`test_a_failed_deletion_changes_nothing`).
- Every deletion writes one content-free row of `deletion_audit`: the time, the `actor`, the
  `reason`, the root kind and the number of rows per table, and no ID, digest, text or metadata of
  a deleted record (`test_deletion_audit_is_content_free`,
  `test_deletion_audit_holds_no_text_digest_or_metadata_of_real_records`).
- The `actor` and the `reason` are required keyword arguments without defaults, and the `reason`
  is one of `operator_request`, `retention_policy` and `data_subject_request`
  (`test_deletion_requires_actor_and_reason`,
  `test_the_database_refuses_a_reason_or_root_kind_outside_the_closed_sets`).
- The `actor` is a machine-style label that matches `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`, so a
  name with a space, an e-mail address, a non-ASCII text or a longer value is refused before any
  statement, with an error that does not echo it
  (`test_actor_outside_the_label_rule_is_refused_by_every_deletion`,
  `test_actor_labels_that_follow_the_rule_are_accepted_and_stored_exactly`).
- An actor that matches the label rule but has the shape of a credential is still refused
  (`test_actor_credential_check_still_applies_to_values_that_match_the_label_rule`).
- The audit is append-only: a trigger refuses every UPDATE and every DELETE of an audit row
  (`test_deletion_audit_rows_cannot_be_deleted`, `test_update_rejected_on_every_table`).
- When the run of a bundle is stored, `store_run_bundle` never inserts a member that a deletion
  removed, so a deleted test profile or definition stays deleted when the same bundle or a
  rescoring of the run is stored again
  (`test_storing_the_same_bundle_again_does_not_bring_back_a_deleted_member`,
  `test_a_deleted_test_profile_leaves_no_personal_data_after_a_second_store`,
  `test_a_rescoring_of_a_stored_run_does_not_bring_back_the_deleted_member`).
- The keyword `repair_members=True` of `store_run_bundle` is the only way to insert those members
  again: it is an explicit act that restores deleted data, and it writes no audit row, because a
  content-free audit row cannot name what it restored (`test_repair_members_inserts_the_deleted_member_again`,
  `test_repair_members_inserts_every_deleted_member_in_one_call`).
- A run that is not stored, also after `delete_run`, is a new import, and its bundle is inserted
  whole (`test_a_run_that_is_not_stored_is_imported_whole_as_before`).
- A direct call of `store_snapshot` or `store_definition` inserts the record it names, a deleted
  one included, so a snapshot stored on its own brings back a deleted definition that it embeds;
  this is a documented limit with no test (unverified).
- Retention is recorded and not enforced: the privacy columns carry the retention date with an
  index, and nothing in the package deletes a record when the date passes
  (`test_retention_columns_mirror_privacy_marker`, `test_retain_until_index_exists`,
  `test_no_retention_enforcement_code_exists`).

## 8. Migrations and versions

See `docs/adr/0021-store-schema-version-and-migrations.md`.

- A store is never migrated by being opened: `open_store` is read-only and raises
  `StoreNotMigrated` for a database below the head revision (`test_open_store_never_auto_migrates`,
  `test_open_store_at_an_older_shipped_revision_is_not_migrated`).
- `upgrade` and `downgrade` of `tracer_evaldb.migrate` are explicit calls, and a downgrade that
  would lose rows is refused whole unless `allow_data_loss` is true
  (`test_programmatic_upgrade_api`, `test_downgrade_base_on_populated_db_requires_explicit_data_loss_flag`,
  `test_a_multi_step_downgrade_is_refused_whole_before_the_first_step`).
- The shipped revisions are `0001` (the 37 tables, the triggers and the `tracer_meta` row) and
  `0002` (25 access-pattern indexes), and each is frozen text that never reads the live metadata
  (`test_revision_0001_is_frozen_text_and_never_reads_the_live_metadata`,
  `test_revision_0002_is_frozen_text_and_never_reads_the_live_metadata`).
- A shipped revision is never regenerated, and a change of the tables comes as a new revision
  (`test_revision_0001_is_unchanged_by_the_new_indexes`, `test_alembic_head_matches_metadata_exactly`).
- The trigger statements that revision `0001` runs come from the live `tracer_evaldb.triggers`
  module, and their text is pinned by hash so that a change fails loudly
  (`test_trigger_ddl_text_matches_the_committed_hashes`).
- Each revision runs in its own transaction, DDL included, so a failed revision leaves the database
  at the previous revision (`test_failed_migration_rolls_back_to_the_previous_revision`,
  `test_postgres_migration_is_transactional`).
- One database holds one schema version, recorded in `tracer_meta`, and `open_store` refuses a
  newer or an older one (`test_tracer_meta_records_schema_version`,
  `test_open_store_newer_schema_version_raises_too_new`,
  `test_open_store_older_schema_version_raises_mismatch`).
- A database at a revision the package does not know is refused with `StoreRevisionUnknown`
  (`test_open_store_unknown_revision_raises_revision_unknown`).
- The repository functions also work on a database that `metadata.create_all` built
  (`test_repository_functions_do_not_require_a_migrated_database`).

## 9. Phase 5 risks

- A store sends the same number of statements for 4, 400 and 4,000 turns, at most one INSERT per
  table, and the measurement harness records times with no timing threshold
  (`test_store_run_bundle_statement_count_independent_of_record_count`,
  `test_measurement_harness_runs_on_postgres`).
- Finding P2-F4: in the P2-23 measurement a bundle of 4,000 turns took a median of 5.6 to 5.7 s to
  store on Postgres 15 and 18 through the pg8000 `executemany`, against 0.28 s on SQLite, and no
  test checks these times (unverified).
- See `docs/phase2/evidence/measurements/bundle`.
- The candidate remedies are batching tuned for `executemany`, `COPY` for the bulk tables, or a
  second driver with a permissive licence, each behind the same parity suite (unverified).
- A deletion and a concurrent insert never leave an orphan row
  (`test_concurrent_insert_during_run_delete_never_orphans`,
  `test_concurrent_run_insert_during_project_delete_never_orphans`,
  `test_concurrent_run_insert_during_snapshot_delete_never_orphans`).
- Two concurrent writers of the same new record are not tested, and the loser of that race gets
  `StoreConstraintViolation` with kind `unique` instead of a no-op (unverified).
- SQLite waits up to `busy_timeout_ms`, 5000 ms by default, for another writer
  (`test_sqlite_busy_timeout_is_configured`).
- After that wait the error is the untyped `sqlalchemy.exc.OperationalError` with the message
  database is locked, and a typed busy error is left to Phase 5 (unverified).
- A change of schema 0.0.2 that alters the digest of a versioned entity or of a snapshot cannot be
  migrated in place, and a revision that tries is refused
  (`test_digest_changing_upgrade_of_versioned_entity_refused`,
  `test_digest_changing_upgrade_of_config_snapshot_refused`).
- `create_store_engine` has no TLS option and the tested Postgres servers listen on the loopback
  address only, so before a remote Postgres is used the connection must use TLS with verification
  of the certificate and the host name, the guarantee of `sslmode=verify-full` in libpq
  (unverified).
- Revision `0001` is not fully frozen while its trigger statements come from the live module, and
  a real freeze is finding P2-F6 (unverified).

## 10. Statements that are not verified

- That each cited test really supports the sentence that cites it is checked by a reviewer and not
  by a test (unverified).
- The Phase 1 label fields `Project.name`, `policy_ref` and `ToolCallEvent.name` accept a text
  that has the shape of a credential, so the store can hold one; this is finding P2-F5 for schema
  0.0.2 (unverified).
- Only SQLSTATE class 23 is scrubbed: an error of another class raised by a caller's own statement,
  such as a failed cast of class 22, can quote a value in its message (unverified).
- `open_store` and the functions of `tracer_evaldb.migrate` do not have that guard (unverified).
- Do not enable DEBUG logging of `sqlalchemy.engine` or `sqlalchemy.pool` where the rows are
  personal data, because at DEBUG SQLAlchemy logs result rows and the pool logs the host of a
  failed connection (unverified).
- Protect the database with disk or volume encryption and with operating system permissions, and
  treat every copy and backup of it as the data itself (unverified).
- The mode of the `-wal`, `-shm` and `-journal` files that SQLite creates next to the database is
  not checked by a test (unverified).
- On Postgres the storage of the server, its disks, backups and replicas belongs to the operator,
  and nothing in the package or its CI can see whether it is encrypted (unverified).
- A direct call of `store_snapshot` or `store_definition` inserts the record it names, a deleted
  one included, so a snapshot stored on its own brings back a deleted definition that it embeds;
  this is a documented limit with no test (unverified).
- Finding P2-F4: in the P2-23 measurement a bundle of 4,000 turns took a median of 5.6 to 5.7 s to
  store on Postgres 15 and 18 through the pg8000 `executemany`, against 0.28 s on SQLite, and no
  test checks these times (unverified).
- The candidate remedies are batching tuned for `executemany`, `COPY` for the bulk tables, or a
  second driver with a permissive licence, each behind the same parity suite (unverified).
- Two concurrent writers of the same new record are not tested, and the loser of that race gets
  `StoreConstraintViolation` with kind `unique` instead of a no-op (unverified).
- After that wait the error is the untyped `sqlalchemy.exc.OperationalError` with the message
  database is locked, and a typed busy error is left to Phase 5 (unverified).
- `create_store_engine` has no TLS option and the tested Postgres servers listen on the loopback
  address only, so before a remote Postgres is used the connection must use TLS with verification
  of the certificate and the host name, the guarantee of `sslmode=verify-full` in libpq
  (unverified).
- Revision `0001` is not fully frozen while its trigger statements come from the live module, and
  a real freeze is finding P2-F6 (unverified).
