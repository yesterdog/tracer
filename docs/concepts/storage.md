# Storage

The package `tracer-evaldb` keeps the entities of `tracer-evalschema` in a relational database,
SQLite for a single user, the CLI and CI, Postgres for a server. Decisions:
[ADR 0019](../adr/0019-relational-storage.md) (tables and types),
[ADR 0020](../adr/0020-deletion-and-retention.md) (deletion and retention),
[ADR 0021](../adr/0021-store-schema-version-and-migrations.md) (schema version and migrations) and
[ADR 0022](../adr/0022-write-unit-rescoring-reimport.md) (write unit, rescoring, re-import). The
rules, limits and risks, each with the test that checks it or marked as not verified, are in
`packages/evaldb/STORAGE_POLICY.md`.

## What is stored

- One table per entity (26) and eleven side tables (snapshot members, bundle members, export links,
  external-reference keys, the deletion audit and the `tracer_meta` row): 37 tables.
- A run is stored as one whole validated `RunBundle`, in one transaction: either every record of
  it is stored or none is (`test_run_bundle_store_is_atomic`). Records of fact are shared by ID, so
  a rescoring stores only its new results and verdict
  (`test_rescoring_bundle_shares_records_without_duplication`).
- An export bundle is stored as the exact document it is, never split into the internal tables,
  and only with the purpose the caller names (`expected_purpose`, required)
  (`test_export_round_trip_byte_identical`, `test_store_export_requires_expected_purpose`).
- The store is internal data, and internal data is personal data: test profiles, transcripts of
  human callers and references to recordings are stored as they are.

## Documents and projections

- Each row keeps the whole document as text (`doc`) and its SHA-256 (`doc_sha256`), plus the
  columns a query needs (project, run, status, times, keys). The document is the truth; a column
  is a projection of it.
- Every read validates the document again with the strict loader of `tracer-evalschema` and checks
  each projected column against it, so a row edited behind the package's back is refused, naming
  the column and never the value (`test_db_tampered_doc_rejected_on_read`,
  `test_db_projection_tamper_rejected_naming_the_column_not_the_value`).
- Rows are immutable: a trigger refuses every UPDATE. A change of a versioned entity is a new
  version, a change of a record of fact is a new record with a new ID
  (`test_update_rejected_on_every_table`).

## Two backends, one behaviour

- SQLite and Postgres store the same value the same way: UUIDs, UTC instants, exact decimals,
  documents, digests, keys, 64-bit integers and three-valued booleans. The parity table in
  `packages/evaldb/STORAGE_POLICY.md` is compared with the real column types of both backends
  (`test_storage_policy_parity_table_matches_column_types`).
- Text keys compare by code point on both backends; no comparison is language-sensitive and
  Indic text is stored byte for byte, never normalized (`test_key_column_order_parity`,
  `test_indic_text_round_trip_byte_exact`).
- The same tests run on SQLite and, with the `postgres` marker, on Postgres 15 and 18.

## What the database does not do

- The database is not a validation boundary: the repository is. A raw INSERT of a document the
  models reject is accepted by the database; the repository refuses to read it, and
  `verify_database` finds it
  (`test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`). Write to the
  store only through the package's functions.
- The package does not encrypt the database: documents and stored test profiles are readable in a
  SQLite file (`test_the_sqlite_file_holds_documents_in_plain_text`). Use disk encryption and
  operating system permissions; a new SQLite file is created readable by its owner only.
- It does not enforce retention: retention dates are recorded and indexed, and a deletion is
  always an explicit call.
- It does not store provider keys or other secrets, and its errors never echo a stored value
  (`test_store_has_no_secret_columns`, `test_store_errors_hide_input`).

## Deletion and retention

- Seven deletion roots (`delete_run`, `delete_project`, `delete_test_profile`,
  `delete_definition`, `delete_dataset_item`, `delete_export`, `delete_snapshot`) remove what the
  root owns, through foreign key cascades and a few rules in the package, in one transaction
  (`test_delete_run_cascades_every_run_owned_kind`).
- Each deletion writes one content-free, append-only audit row: when, who (a machine-style
  `actor` label, never a person's name), why (`operator_request`, `retention_policy` or
  `data_subject_request`) and how many rows per table, and no ID or content
  (`test_deletion_audit_is_content_free`, `test_deletion_audit_rows_cannot_be_deleted`).
- A bundle that listed a deleted test profile loads as incomplete, and storing the same bundle
  again does not bring the deleted data back unless the caller asks for it with
  `repair_members=True` (`test_storing_the_same_bundle_again_does_not_bring_back_a_deleted_member`).

## Versions and migrations

- One database holds one schema version, recorded in the `tracer_meta` row.
- Opening a store never migrates it: `open_store` refuses a database that is not at the head
  revision, and `upgrade` and `downgrade` are explicit (`test_open_store_never_auto_migrates`).
  A downgrade that would lose rows needs `allow_data_loss=True`.
- Shipped revisions are frozen text and never regenerated; each runs in its own transaction.
