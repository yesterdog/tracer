# tracer-evaldb

Relational storage of `tracer-evalschema` 0.0.1 entities on SQLite and Postgres (SQLAlchemy Core and
Alembic). Status: 0.0.1, unreleased (Phase 2 in progress). The design is in ADRs 0019 to 0022.

Install the Postgres driver with the extra: `pip install "tracer-evaldb[postgres]"` (pg8000, a pure
Python driver; the URL is `postgresql+pg8000://...`). SQLite needs library version 3.37 or newer.

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
  `store_run_bundle` (the write unit: records of fact are shared by ID, a rescoring inserts only
  its new rows) and `store_export(engine, export, *, expected_purpose)`.
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
  reference.

`tracer_evaldb.triggers` attaches the immutability triggers to the metadata: `metadata.create_all`
creates a `BEFORE UPDATE` trigger on each of the 37 tables and a `BEFORE DELETE` trigger on
`deletion_audit` (on Postgres the two trigger functions first; they raise SQLSTATE `23001`). Both
engines hide bound parameters in errors, and the Postgres engine raises every SQLSTATE of class 23
as `sqlalchemy.exc.IntegrityError`, without the failing row.
