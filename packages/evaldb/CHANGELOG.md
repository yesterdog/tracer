# Changelog: `tracer-evaldb`

Every release lists what the store, its migrations and its policies gained or changed. The data it
holds is the data of `tracer-evalschema` (the exact schema version is a dependency pin), and the
rules of the storage are in `STORAGE_POLICY.md` next to this file. A change that needs a new
database revision names that revision under "Migrations"; a shipped revision is never edited.

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI.

First version. Everything below is new.

### Package

- Relational storage of `tracer-evalschema` 0.0.1 on SQLite (library 3.37 or newer) and
  Postgres (15 and 18 tested, through pg8000 with the extra `tracer-evaldb[postgres]`), with
  SQLAlchemy Core and Alembic. Runtime dependencies are bounded ranges, each tested at its lower
  bound in CI: `sqlalchemy>=2.1.6,<3`, `alembic>=1.20.1,<2` and, for the extra,
  `pg8000>=1.31.5,<2`; `tracer-evalschema==0.0.1` is exact because it is the schema version of the
  stored data.
- `create_store_engine(url)`: SQLite files and `postgresql+pg8000://` URLs only; foreign keys on,
  real transactions, UTC sessions, UTF8 only, bound parameters hidden in errors, the URL never
  echoed. A new SQLite file is created with mode 0600.
- The column types `UuidCol`, `InstantCol`, `DecimalCol`, `DocCol`, `Sha256Col`, `KeyCol`,
  `BigIntCol` and `BoolCol`, the same values the same way on both backends.
- The repository (`store_*`, `load_*`, the lookups and typed errors), the seven deletion roots,
  `open_store`, `tracer_evaldb.migrate` and `verify_database`. The store functions refuse an
  engine that does not hide bound parameters (`UnsafeEngine`).

### Storage

- 37 tables (26 entity tables and 11 side tables), each row holding its whole document as text
  plus the projected columns that queries need; every read validates the document again.
- Immutability triggers: no UPDATE of any stored row, no DELETE of a `deletion_audit` row.
- Deletion with a content-free, append-only audit row; the `actor` is a machine-style label and
  the `reason` one of three values. Retention dates are recorded and indexed, not enforced.
- `store_run_bundle` never brings back a member that a deletion removed when the run is stored;
  `repair_members=True` is the explicit way to restore it.
- The rules, limits and risks of the storage, with the test behind each statement, are in
  `STORAGE_POLICY.md`. The database is not encrypted by the package.

### Migrations

- Revision `0001`: the 37 tables, the immutability triggers and the `tracer_meta` row.
- Revision `0002`: 25 access-pattern indexes, no table or row change.
- Each revision runs in one transaction; a downgrade that would lose rows needs
  `allow_data_loss=True`; opening a store never migrates it.

### Findings

- P2-F1: definitions are not owned by a project; an additive `project_id` on definitions is
  proposed for schema 0.0.2.
- P2-F2: `version` and the `*_ms` fields should be bounded by 2**53 - 1 in schema 0.0.2; in 0.0.1
  the store refuses a larger value with a typed error.
- P2-F3: the `span_ref` values of one run may repeat; schema 0.0.2 should either require them to
  be distinct or say that they need not be.
- P2-F4: storing a 4,000-turn bundle on Postgres through pg8000 took 5.6 to 5.7 s against 0.28 s
  on SQLite, with equal statement counts; a Phase 5 performance risk.
- P2-F5: the schema label fields `Project.name`, `policy_ref` and `ToolCallEvent.name` accept
  credential-shaped text; for schema 0.0.2.
- P2-F6: revision `0001` takes its trigger statements from the live `triggers` module; the text is
  pinned by hash, and a real freeze is still to do.
