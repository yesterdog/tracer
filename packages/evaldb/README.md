# tracer-evaldb

Relational storage of `tracer-evalschema` 0.0.1 entities on SQLite and Postgres (SQLAlchemy Core and
Alembic). Status: 0.0.1, unreleased (see "Status"). The design is in ADRs 0019 to 0022
(`docs/adr/` in the repository), the rules of the storage in `packages/evaldb/STORAGE_POLICY.md`,
the changes in `packages/evaldb/CHANGELOG.md` and an overview in `docs/concepts/storage.md`.

## Install

- SQLite: `pip install tracer-evaldb`. The SQLite library of the Python interpreter must be
  version 3.37 or newer (STRICT tables); `create_store_engine` refuses an older one.
- Postgres: `pip install "tracer-evaldb[postgres]"` installs pg8000, a pure Python driver with a
  permissive licence; the URL is `postgresql+pg8000://...`. Postgres 15 and 18 are tested, and the
  database must use the UTF8 encoding.
- Runtime dependencies are bounded ranges (`sqlalchemy>=2.1.6,<3`, `alembic>=1.20.1,<2`,
  `pg8000>=1.31.5,<2`), each tested at its lower bound; `tracer-evalschema==0.0.1` is exact, because
  it is the schema version of the stored data.

## Quick start

Create a SQLite store in the current directory, migrate it explicitly, store and read back a
project, and check the store. It needs no key and no network.

```python
import json

import tracer_evaldb as evaldb
from tracer_evalschema import loader
from tracer_evalschema.project import Project

engine = evaldb.create_store_engine("sqlite:///tracer-store.db")
evaldb.migrate.upgrade(engine)  # explicit: opening a store never migrates it
print(evaldb.open_store(engine))

document = {
    "id": "prj_01a168a1-da00-7394-a377-74bf798c7e70",
    "schema_version": "0.0.1",
    "created_at": "2026-10-23T12:00:00.000000Z",
    "metadata": {},
    "metadata_flags": [],
    "name": "Example project",
    "provider_configs": [],
}
project = loader.load_model(Project, json.dumps(document))
print(evaldb.store_project(engine, project))
assert evaldb.load_project(engine, project.id) == project

report = evaldb.verify_database(engine)
assert report.ok and not report.findings, report
print(report.checked["project"], "project row checked")
```

A run is stored as a whole validated `RunBundle` with `store_run_bundle`, after its project
(the store never creates a project for a bundle).

`create_store_engine(url)` returns an engine configured the way the store needs it (foreign keys on,
real transactions, UTC session time zone, UTF8 only) and never echoes the URL in an error. The
column types `UuidCol`, `InstantCol`, `DecimalCol`, `DocCol`, `Sha256Col`, `KeyCol`, `BigIntCol` and
`BoolCol` store the same values the same way on both backends.

`tracer_evaldb.metadata` holds the 37 tables (26 entity tables and 11 side tables, module
`tracer_evaldb.tables`). `tracer_evaldb.codecs` has `encode_<table>` and `decode_<table>` for each
entity table, the generic `encode` and `decode`, and `decode_run_bundle`: a model becomes the rows
to insert, and a row is checked against its own document on the way back
(`StoreIntegrityError`, `StoreValueOutOfRange`).

## Repository (`tracer_evaldb.repository`, re-exported from `tracer_evaldb`)

Every function takes the engine of `create_store_engine` first and runs in one transaction (a
`Connection` is a `TypeError`). Writers load their argument again through the Phase 1 loaders, check
every record before the first `INSERT`, and write one multi-row `INSERT` per table.

- Writers, each returning a `StoreResult`: `store_project`, `store_definition` (the nine versioned
  entities), `store_snapshot` (with the definitions it embeds), `store_dataset_item`,
  `store_run_bundle(engine, bundle, *, repair_members=False)` (the write unit: records of fact are
  shared by ID, a rescoring inserts only its new rows; when the run is stored, a member that a
  deletion removed is not inserted again unless `repair_members=True`, an explicit act that
  restores deleted data) and `store_export(engine, export, *, expected_purpose)`.
- Readers: `load_project`, `load_definition`, `load_snapshot`, `load_dataset_item`,
  `load_run_bundle(engine, bundle_id, *, allow_incomplete=False)` (a `LoadedRunBundle`; deleted
  members are an `IncompleteRunBundle` or, when allowed, a list of `MissingMember`) and
  `load_export(engine, export_id, *, expected_purpose)`.
- Lookups: `latest_version`, `find_snapshot_by_digest` and
  `find_by_external_ref(engine, entity_kind, ref, *, run_id=None)` (returns `ExternalRefMatch`
  values; the key is the SHA-256 of the JCS of `[system, kind, value]`).
- Errors: `ReimportConflict` (a `ConflictingRecordError`), `RecordOwnershipConflict`,
  `ProjectNotStored`, `InternalSnapshotRefused`, `RecordNotStored`, `IncompleteRunBundle`,
  `ExportReferenceNotStored` and `StoreConstraintViolation` (from
  `repository.translate_integrity_error`). None holds a value, a document fragment or an external
  reference. Every store function refuses, with `UnsafeEngine`, an engine that does not hide bound
  parameters (an engine of `sqlalchemy.create_engine` without `hide_parameters=True`).

`tracer_evaldb.triggers` attaches the immutability triggers to the metadata: `metadata.create_all`
creates a `BEFORE UPDATE` trigger on each of the 37 tables and a `BEFORE DELETE` trigger on
`deletion_audit` (on Postgres the two trigger functions first; they raise SQLSTATE `23001`). Both
engines hide bound parameters in errors, and the Postgres engine raises every SQLSTATE of class 23
as `sqlalchemy.exc.IntegrityError`, without the failing row.

## Migrations, version policy, deletion and retention (batch D)

- `tracer_evaldb.migrate`: `upgrade(engine, revision="head")`, `downgrade(engine, revision, *,
  allow_data_loss=False)`, `current_revision`, `head_revision` and `alembic_config`. Revision 0001
  creates the 37 tables, the immutability triggers and the one `tracer_meta` row. Each revision runs
  in one transaction, DDL included. A downgrade that would destroy rows is refused whole
  (`DowngradeDataLoss`) unless `allow_data_loss=True`. Command line:
  `alembic -c packages/evaldb/alembic.ini -x url=<database URL> upgrade head` (`alembic.ini` holds
  no URL).
- `open_store(engine) -> StoreInfo`: read-only; never migrates. Raises `StoreNotMigrated`,
  `StoreRevisionUnknown`, `StoreSchemaVersionTooNew` or `StoreSchemaVersionMismatch`.
- `tracer_evaldb.migrations.helpers` (for revisions): `batch_alter_table`, `rewriting` and
  `rewrite_documents`; errors `IrreversibleMigration`, `DigestChangingUpgrade`,
  `MixedSchemaVersions`, `MigrationIntegrityError`.
- Deletion, each in one transaction with a content-free audit row (`actor` and `reason` required;
  the actor is a machine-style label such as `ops-night-shift` or `service_account:tracer-ci`,
  matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`; reasons `operator_request`, `retention_policy`,
  `data_subject_request`): `delete_run`,
  `delete_project`, `delete_test_profile`, `delete_definition(engine, kind, id)`,
  `delete_dataset_item`, `delete_export`, `delete_snapshot` (`SnapshotInUse` while a run uses it).
  Each returns a `DeletionResult(root_kind, counts, audit_uuid)`.
- Retention: the privacy columns have partial indexes on the retention date
  (`ix_artifact_retain_until`, `ix_transcript_retain_until`, `ix_dataset_item_retain_until`,
  `ix_run_caller_retain_until`). Nothing enforces retention; a later phase does.

## Access-pattern indexes and verification (batch E)

- Revision 0002 (`0002_access_pattern_indexes`) adds 25 plain indexes (no table or row changes):
  `run` by `(project_id, id)`, by agent target, `mode`, `observed_tier`, `started_at` and snapshot;
  `run_id` on every child table of a run (so a cascade never scans a child table); artifacts by
  `sha256`; results by `(metric_id, metric_version, status)`; snapshots by digest; metric
  definitions by `key`; dataset items by artifact and transcript. Definitions by `(id, version)`
  are served by the primary key. Its downgrade loses no data. `open_store` on a database still at
  revision 0001 raises `StoreNotMigrated`.
- `verify_database(engine) -> VerifyReport` is a read-only admin check (SELECT only, REPEATABLE
  READ on Postgres, no migration needed). It re-validates every `doc`, recomputes `doc_sha256`,
  every projected column and the side-table rows, and reports `VerifyFinding(kind, id, version,
  problem, location)` in `findings` (`VERIFY_PROBLEMS`: `doc_invalid`, `digest_mismatch`,
  `doc_sha256_mismatch`, `projection_mismatch`, `side_rows_mismatch`) and `informational`
  (`VERIFY_NOTES`: `doc_text_not_canonical`, `bundle_member_absent`), plus `checked` row counts
  per entity table. A damaged row is a finding, never an exception, and no finding holds a value.

## Storage policy

`packages/evaldb/STORAGE_POLICY.md` holds the rules of the storage: the parity table of the column
types on both backends, why the database is not a validation boundary (the repository is), the
limits, encryption at rest, the plain profile digest, deletion and audit, migrations, and the risks
left for Phase 5. Every statement in it cites the test that checks it or is marked as not verified,
and its last section lists the statements that are not verified.

## Security and privacy

- The package does not encrypt the database. The store holds internal data, which is personal
  data: a stored test profile (name, date of birth, account number) and every document are
  readable in the file. Use disk or volume encryption and operating system permissions, and treat
  each copy and backup as the data itself. A new SQLite file is created with mode 0600; an
  existing file keeps its mode.
- Errors never echo a stored value or the database URL: both engines hide bound parameters, the
  Postgres engine removes the failing row from integrity errors, and no store error holds a value.
  Do not enable DEBUG logging of `sqlalchemy.engine` or `sqlalchemy.pool` where rows are personal
  data; at DEBUG SQLAlchemy logs result rows.
- `expected_purpose` is required, with no default, at both export entry points (`store_export`
  and `load_export`); `None` is a `TypeError`.
- No provider key or other secret is stored: the schema holds references only, and no table has
  a column for one.
- A remote Postgres needs TLS with certificate and host name verification (the guarantee of
  `sslmode=verify-full`); `create_store_engine` has no TLS option yet, so use the store on a
  loopback or otherwise protected connection until Phase 5 adds it.

## Status

Version 0.0.1, unreleased: no tag exists and nothing is published. Phase 2 (the storage) is
implemented and awaits its gate review; see `packages/evaldb/CHANGELOG.md`.
