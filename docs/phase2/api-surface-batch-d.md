# Phase 2 batch D contract: migrations, version policy, deletion, audit, retention (P2-14, P2-15, P2-19, P2-17a, P2-17b, P2-18)

Author: qa, 2026-10-24. Source: `docs/phase2/SCOPE.md` (sections 1.5 and 1.6, Decisions D1 to D5), ADR 0019 (Decisions 6a, 15 and 18, Table 2, Ambiguity 9), ADR 0020 (Decisions 1 to 9, Ambiguities 1 to 8), ADR 0021 (rules 1 to 15, Ambiguities 1 to 5), ADR 0022 (Decision 8), ADR 0006 (Decision 5), the batch A, B and C contracts, the implemented `packages/evaldb/src/tracer_evaldb` and the decision log (entries "PHASE 2"). qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or HANDOFF, and no earlier test. Every statement below is pinned by a test named in section 9, or is marked (unpinned).

## 0. Where the tests are and how to run them

`packages/evaldb/tests/` (the directory of batches A to C), seven new test files, two new helper modules, and no change to an earlier file:

| File | Holds |
|---|---|
| `evaldb_migration_support.py` | helpers, no test: the `mig` fixtures (an EMPTY database per test), the catalog fingerprint of a schema on either backend, the writers of test-only revisions, the doubles of the fake data migration, `populate` (a store with every entity table filled through the repository), statement faults and pauses |
| `evaldb_deletion_support.py` | helpers, no test: `World` (stub rows for projects, runs with chosen row counts, snapshots, items, exports, definitions), the 14 root specs with their expected counts and the literal table of rows each root removes |
| `test_evaldb_migrations.py` | P2-14 and P2-15 (Alembic in the package, `open_store`, the stairway, data loss, SQLite batch mode, transactions) |
| `test_evaldb_version_policy.py` | P2-19 (`tracer_meta`, the version errors, the fake 0.0.2 data migration, refused upgrades, mixed versions, trigger windows) |
| `test_evaldb_deletion.py` | P2-17a (the seven roots, the cascades, the locks, the races, parity) |
| `test_evaldb_deletion_audit.py` | P2-17b (the audit row) |
| `test_evaldb_retention.py` | P2-18 |
| `test_evaldb_adr0021.py` | `test_adr0021_policy_rules_are_tested`, the SCOPE names of the batch, the ADR 0020 invariants and count-key table, the error types |
| `test_evaldb_migration_doubles.py` | the text-level upgrade step of the fake migration, tested by itself |

Commands are those of batches A to C: `uv run --locked --all-groups pytest` runs the SQLite side; `TRACER_TEST_POSTGRES_URL=... TRACER_TEST_POSTGRES_SQL_ASCII_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests` runs the Postgres side. No new environment variable, no change to `conftest.py`, the root `pyproject.toml` or CI. The deletion, audit and retention tests use the `db` fixture of batch B (the `store` fixture with every table created by `metadata.create_all`; Postgres: one shared schema, emptied before each test). The migration tests must not share that schema, so the `mig` fixture is new and independent of `db`: SQLite, a new file; Postgres, a database `tracer_mig_<16 hex>` created on the server of `TRACER_TEST_POSTGRES_URL` (the role needs `CREATEDB`; the CI role is the superuser) and dropped with `DROP DATABASE ... WITH (FORCE)` at teardown. `[postgres]` cases carry the marker and are never skipped; `test_postgres_migration_is_transactional` and `test_postgres_other_sessions_never_see_the_trigger_window` carry it themselves.

Test-only revisions are written into a temporary directory by the tests and added to a call with `version_locations=[directory]` (section 2.4). They are never part of the shipped chain.

## 1. Modules and names (pinned)

```
packages/evaldb/
  alembic.ini                         script_location only; no URL (not in the wheel)
  src/tracer_evaldb/
    migrate.py        upgrade, downgrade, current_revision, head_revision, alembic_config
    versioning.py     open_store, StoreInfo
    deletion.py       delete_run, delete_project, delete_test_profile, delete_definition,
                      delete_dataset_item, delete_export, delete_snapshot, DeletionResult
    errors.py         the errors of batches A to C, plus the 11 of section 8
    tables.py         the 37 tables of batch B, plus 4 partial indexes (section 7)
    engine.py         create_store_engine: unchanged signature; a deletion can ask for BEGIN IMMEDIATE (section 5.4)
    migrations/
      __init__.py     (new: the wheel ships it)
      env.py          the Alembic environment (section 2.2)
      helpers.py      for revisions: batch_alter_table, rewriting, rewrite_documents (section 3)
      script.py.mako  the revision template (section 2.3)
      versions/
        __init__.py
        0001_initial_schema.py
    __init__.py       re-exports: the 7 delete functions, DeletionResult, open_store, StoreInfo, the 11
                      errors; the modules `migrate` and `deletion` are attributes of the package
```

`tracer_evaldb.__all__` gains `DeletionResult`, `StoreInfo`, `delete_dataset_item`, `delete_definition`, `delete_export`, `delete_project`, `delete_run`, `delete_snapshot`, `delete_test_profile`, `deletion`, `migrate`, `open_store` and the 11 errors. `upgrade` and `downgrade` are not re-exported (their names are too general for the package namespace): call `tracer_evaldb.migrate.upgrade`.

**Import rule (finding F1).** `import alembic` loads `mako`, `markupsafe` and, where pytest installed it, `pygments`; the batch A test `test_evaldb_runtime_imports_whitelist` imports every module outside `tracer_evaldb.migrations` and forbids `pygments`. So `migrate.py` and `versioning.py` (and every module but `migrations/*`) import `alembic` inside the functions that need it, and `import tracer_evaldb` loads none of it (`test_importing_the_package_does_not_load_alembic`). Imports stay within the batch A rule (standard library, `sqlalchemy`, `alembic`, `tracer_evalschema`, `tracer_evaldb`).

## 2. Alembic inside the package (P2-14)

### 2.1 The API of `tracer_evaldb.migrate`

```python
def head_revision() -> str                                  # "0001"
def current_revision(engine) -> str | None                  # None: never migrated (no alembic_version, or an empty one)
def alembic_config(engine=None, *, version_locations=()) -> alembic.config.Config
def upgrade(engine, revision="head", *, version_locations=()) -> None
def downgrade(engine, revision, *, allow_data_loss=False, version_locations=()) -> None
```

`test_programmatic_upgrade_api` pins the parameter names, kinds and defaults exactly as written (`revision` of `downgrade` has no default; `version_locations` and `allow_data_loss` are keyword-only). `engine` is the `Engine` of `create_store_engine`: a `Connection`, a URL, a string or `None` is a `TypeError` before any statement, as in batch C; `revision` is a `str` (`TypeError` otherwise) and an empty or unknown one is a `ValueError` containing "revision" (`test_migrate_arguments_are_checked_before_any_statement`); `allow_data_loss` must be a `bool` (`TypeError`). `upgrade` to the revision the database is at sends no write or DDL statement. The caller's engine is never disposed. `alembic_config(engine)` carries the engine in `config.attributes["engine"]` and no URL (`get_main_option("sqlalchemy.url")` is `None`); `alembic_config()` has no `"engine"` attribute.

`downgrade` of a database that was never migrated is a no-op that sends no write or DDL statement (ambiguity A24). A database at a revision that this evaldb does not know (a newer evaldb migrated it, or an extra directory is missing) is refused by `upgrade` and `downgrade` with `StoreRevisionUnknown`, before any change (`test_migrate_refuses_a_database_at_an_unknown_revision`).

### 2.2 The revision chain, the environment and the transactions

* **Naming.** A shipped revision is the file `versions/NNNN_snake_case_slug.py` with `revision = "NNNN"` (four digits, the file prefix), `branch_labels = None`; the chain is linear, `down_revision` of the first is `None`, the single head is `0001` (`test_revision_chain_is_linear_and_follows_the_naming_convention`). A test-only revision has an id that is not four digits (`t001`, `t002`, ...) and the file name `<id>_<slug>.py`; none ships (`test_migrations_ship_in_wheel`, `test_test_only_revisions_are_never_part_of_the_shipped_chain`). Nothing under `src/tracer_evaldb` contains the text `0.0.2`.
* **Revision 0001 is frozen text.** It creates the 37 tables with every CHECK, foreign key and index of `metadata` (so `metadata.create_all` and revision 0001 build the same schema, partial indexes included), the Postgres trigger functions, the 38 triggers from `tracer_evaldb.triggers` (`create_statements` and `function_statements`, the single source of ADR 0020 Decision 7), and inserts the one `tracer_meta` row `(1, '0.0.1', <installed evaldb version>)`. It never imports or reads the live `metadata`, `tables`, `schema`, `codecs` or `repository` (the column types of `tracer_evaldb.columns` and the trigger DDL of `tracer_evaldb.triggers` may be imported: they are the vocabulary of the DDL) (`test_revision_0001_is_frozen_text_and_never_reads_the_live_metadata`): a later change to `tables.py` must not change what revision 0001 does. Its `downgrade` drops every table and, on Postgres, the two functions.
* **`DOWNGRADE_DROPS`.** Every revision module defines `DOWNGRADE_DROPS: tuple[str, ...]`: the tables whose rows its downgrade destroys (`"table"`), and the columns whose values it destroys (`"table.column"`). For `0001` it is every table of `metadata` except `tracer_meta`, whose only row is the migration's own (`test_every_shipped_revision_declares_what_a_downgrade_drops`). Section 2.5 uses it.
* **`alembic.ini`** is `packages/evaldb/alembic.ini`: a section `[alembic]` with `script_location = %(here)s/src/tracer_evaldb/migrations` and nothing else of substance; no `sqlalchemy.url`, no `://`, no `@`, none of the words password, secret, token (`test_alembic_ini_holds_no_database_url`, `test_migrations_package_holds_no_database_url_and_no_credential`). The programmatic API never reads it. It is not part of the wheel.
* **`env.py`.** The engine is `config.attributes["engine"]` when present; on the command line it is `-x url=<URL>` (`alembic -c packages/evaldb/alembic.ini -x url=sqlite:///x.db upgrade head`, run from any directory; `check` exits 0 on a migrated database and non-zero after an index was dropped; with neither engine nor `-x url` it exits non-zero with a message that says "url") (`test_alembic_command_line_upgrades_and_checks_with_x_url`). `render_as_batch=True`, `compare_type=True`, `alembic_version` excluded from the comparison.
* **One transaction per revision, DDL included, on both backends** (`transaction_per_migration=True`; the oracle also sets `transactional_ddl=True`, which no test can tell from the default on SQLite because `create_store_engine` begins a real transaction on every connection and Alembic commits after each step). A revision that fails after its DDL and its INSERT leaves the database at the previous revision with none of it: `test_failed_migration_rolls_back_to_the_previous_revision` (both backends; a three-revision call leaves the first two committed), `test_failed_first_migration_leaves_an_empty_database` (a fault at the first `CREATE TRIGGER` of 0001 leaves no table, no function, no revision), `test_upgrade_over_an_unmanaged_schema_changes_nothing` (a `create_all` database has no `alembic_version`; revision 0001 cannot be applied over its tables, and the attempt changes nothing; the error type is unpinned, ambiguity A11), and on Postgres `test_postgres_migration_is_transactional` (paused at the first `CREATE TRIGGER`, a second session sees no table, no `alembic_version` row and no function; a fault during a downgrade leaves everything).
* **SQLite foreign keys (ADR 0021 rule 7).** `PRAGMA foreign_keys=OFF` is set outside any transaction (it is a no-op inside one: set it on the DBAPI connection before the first statement of the revision's transaction), the revisions run, `PRAGMA foreign_key_check` runs inside each revision's transaction, and any row it reports raises `MigrationIntegrityError(tables)` (sorted table names, no value) and rolls the revision back; then `PRAGMA foreign_keys=ON` again, also after a failed migration and on every connection of the pool (the test holds four connections at once and reads the pragma of each: a pool hands out its connections in turn, so one query can hide a connection that was left off) (`test_sqlite_foreign_keys_are_off_during_a_migration_and_on_after`, `test_sqlite_foreign_key_check_failure_rolls_the_revision_back`). A batch rebuild of a parent (`project`, `config_snapshot`, `run`) keeps every child row (`test_sqlite_batch_migration_preserves_child_rows`, whose control shows that the same rebuild with foreign keys on removes them).

### 2.3 The revision template

`script.py.mako` generates a module that starts with the two SPDX header lines, assigns `revision` and `down_revision`, defines `DOWNGRADE_DROPS`, and defines `upgrade() -> None` and `downgrade() -> None` (`test_revision_template_generates_a_headed_typed_revision`, via `alembic.command.revision`).

### 2.4 How a test-only revision is registered

By the `version_locations` argument of `upgrade`, `downgrade` and `alembic_config`: directories searched in addition to the shipped `versions/`. The shipped chain does not change (`test_alembic_config_carries_the_engine_and_no_url` checks that the default configuration still has the single head `0001`). A database at a test-only revision is, for the shipped chain, at an unknown revision (`StoreRevisionUnknown`). Rejected: monkeypatching the chain (brittle), a test-only environment variable (a second path into production code), and a second `ScriptDirectory` built by each test (the data-loss check and the batch helpers would then not be exercised through the API). Trade-off: a production argument that only tests and extensions use (ambiguity A1).

### 2.5 Data loss on downgrade (ADR 0021 rule 13)

`downgrade` reads `DOWNGRADE_DROPS` of every revision it is about to undo, counts the rows (`SELECT count(*) FROM table`, or `... WHERE column IS NOT NULL` for a column entry) and, if any is above zero and `allow_data_loss` is not `True`, raises `DowngradeDataLoss(tables)` with `.tables` the sorted tuple of table names (no counts, no values), before the first step of the whole path (`test_a_multi_step_downgrade_is_refused_whole_before_the_first_step`). Downgrading an untouched database (only the `tracer_meta` row) needs no flag; one `deletion_audit` row is enough to need it (`test_the_only_audit_row_is_enough_to_require_the_flag`); a column entry counts only when some row has a value in it (`test_downgrade_that_drops_a_column_with_values_requires_the_flag`). `test_downgrade_base_on_populated_db_requires_explicit_data_loss_flag`: the refusal changes nothing (no write or DDL statement, the state is equal), the flag lets it through, and on Postgres the trigger functions are gone afterwards.

### 2.6 The stairway (ADR 0021 rule 14)

`test_migration_stairway_up_down_up` goes, on an empty and on a populated database and on both backends, from 0001 up through three test-only revisions (add a column to `project`, add a column to `run`, the fake data migration) and back: at each level up, down and up again leave the same catalog (tables, columns, constraints with CHECK literals, indexes, triggers; section 3 of `evaldb_migration_support.py`) and every row of every table (`SELECT *`, so the added columns count) equal to what it was; for the shipped chain alone, down to `base` with the flag and up again gives the same schema and no rows.

## 3. The data-migration mechanism (P2-19; ADR 0021 rules 9 to 12)

`tracer_evaldb.migrations.helpers`, imported by revisions:

```python
def batch_alter_table(table_name, **kwargs) -> ContextManager[BatchOperations]
def rewriting(*table_names) -> ContextManager[None]
def rewrite_documents(old_version, new_version, *, forward, backward, validate) -> None
```

* `batch_alter_table` is `op.batch_alter_table` that, on SQLite, passes `table_kwargs={"sqlite_strict": True, "sqlite_with_rowid": False}` (unless the caller gives its own; finding F2) and, when the block ends, drops and creates again the immutability triggers of the table (a rebuild drops them; the update trigger and, for `deletion_audit`, the delete trigger). On Postgres it is `op.batch_alter_table` (the table is altered in place; `recreate` is the caller's business: `"always"` would rebuild it there too). After a rebuild of `project`, `run`, `turn`, `dataset_member`, `export_bundle` or `deletion_audit` the table is still `STRICT` and `WITHOUT ROWID` (`PRAGMA table_list`), the catalog differs only by the added column, all 38 triggers exist and an UPDATE is still refused (`test_sqlite_batch_migration_preserves_strict_and_triggers`).
* `rewriting(*tables)` drops the update triggers of the tables on entry (Postgres: `DROP TRIGGER IF EXISTS ... ON ...`) and creates them on a normal exit, inside the revision's transaction; on an exception the transaction rolls back and the triggers are as they were. Rule 9: `test_migrations_drop_and_recreate_immutability_triggers_inside_the_migration_transaction` (a fault at the first document rewrite leaves all 38 triggers, the documents and the revision unchanged); on Postgres another session sees all 38 triggers while the migrating transaction has dropped them (`test_postgres_other_sessions_never_see_the_trigger_window`).
* `rewrite_documents(old, new, *, forward, backward, validate)` moves every `doc` of the 26 entity tables from schema version `old` to `new` (rules 10 to 12). `forward(table, doc_text) -> doc_text` and `backward` are pure text functions (the "pure upgrade step" and its inverse); `validate(table, doc_text)` stands for "the models of the new version" and raises to refuse. All three are required keyword arguments: a migration that validates nothing would leave a changed digest invisible. In this order, per row, and **before any write**:
  1. `backward(table, forward(table, text)) != text` raises `IrreversibleMigration(table)` (rule 12: a downgrade restores every byte or the migration does not start);
  2. the new text must parse and its top-level `schema_version` must equal `new`, else `MixedSchemaVersions(table)` (rule 1);
  3. for the nine versioned tables and `config_snapshot`: a `content_digest` member other than the old one raises `DigestChangingUpgrade(table)` (rule 11 and ADR 0021 Ambiguity 2);
  4. `validate(table, new_text)`: a `tracer_evalschema.errors.DigestMismatch` (the models of the new version verify the content digest on load) is `DigestChangingUpgrade(table)`; any other exception propagates unchanged.

  Then, in the revision's transaction: the `schema_version_pinned` CHECK of the 26 entity tables and of `tracer_meta` is dropped (SQLite: `helpers.batch_alter_table`), the triggers of those tables are dropped (`rewriting`), `doc`, `doc_sha256` (SHA-256 of the JCS of the parsed new document, as the codecs compute it) and `schema_version` of each row are written, `tracer_meta.schema_version` is set, the triggers come back, and the CHECKs are created again with the new literal. Nothing else is touched: the content digest columns, `created_at` and every other projection stay byte for byte (a general upgrade that changes a projection writes it itself inside `rewriting`, ambiguity A6); `tracer_meta.evaldb_version` is not written by this function (A8).
* **The fake migration** (`fake_data_revision`): `forward` rewrites the value of every `"schema_version": "0.0.1"` member that is outside a `metadata` object to `0.0.2` with a scanner that touches no other byte (it never re-serializes: float spelling, escapes and key order are the original's; `test_rewrite_schema_version_is_exact_and_skips_metadata`, `test_plain_fake_step_is_exact_in_both_directions_on_every_golden_document`), `backward` is its inverse and `validate` loads the document, with the version put back, through the installed models. `test_fake_data_migration_rewrites_schema_version_and_preserves_digests` (on a store whose turns, agent target and dataset carry `metadata` that spells `schema_version`): every entity table's every row is at `0.0.2` in the column and in the document (a structural comparison with the old document), the document equals the old one except for the version members outside `metadata`, `doc_sha256` is the SHA-256 of the JCS of the new document, the content digest columns and members are equal and verify through the models, the CHECK literals read `'0.0.2'` and nothing else in the catalog changed, the 38 triggers exist, a row of the new version is accepted and one of the old version refused on every entity table, `tracer_meta.schema_version` is `0.0.2`, and `open_store` says `StoreRevisionUnknown`. `test_fake_data_migration_downgrade_restores_bytes_exactly`: after the downgrade every table, column, byte, constraint and trigger equals the state before, `open_store` returns the same `StoreInfo`, the repository loads the bundle, and up again gives the same bytes as the first time.
* **Refusals** (all leave the state equal, the revision at `0001` and send no write or DDL statement): `test_irreversible_data_migration_is_refused_before_any_change` (mode `lossy`), `test_digest_changing_upgrade_of_versioned_entity_refused` (nine versioned tables times three modes: `digest_member` changes the member reversibly, `digest_member_lax` changes it while `validate` accepts everything, so the check of the member itself must refuse it, and `digest_content` changes digested content and leaves the member, so only `validate` can see it), `test_digest_changing_upgrade_of_config_snapshot_refused` (the same three modes), `test_a_validation_failure_of_the_new_models_propagates_and_rolls_back`, `test_mixed_schema_versions_in_one_database_rejected` (the database refuses another version on every entity table; a `forward` that leaves the rows of one table behind is `MixedSchemaVersions`; after a complete move the repository cannot insert a 0.0.1 document: `StoreConstraintViolation(kind="check")`).

## 4. `open_store` and the schema version (ADR 0021 rules 1 and 3)

```python
def open_store(engine) -> StoreInfo        # tracer_evaldb.versioning, re-exported
@dataclass(frozen=True)
class StoreInfo: revision: str; schema_version: str; evaldb_version: str
```

`engine` is the engine of `create_store_engine` (`TypeError` otherwise). It is read-only: one read transaction, no INSERT, UPDATE, DELETE or DDL statement, whatever it finds (`test_open_store_never_auto_migrates`). In this order:

1. no revision (no `alembic_version`, or an empty one, a database made by `create_all`, a database downgraded to base): `StoreNotMigrated(revision=None, head="0001")`, whose message says to run `tracer_evaldb.migrate.upgrade` (`test_open_store_refuses_unmigrated_database`);
2. a revision this evaldb does not ship: `StoreRevisionUnknown(revision)`, even when `tracer_meta` also says something else (`test_open_store_unknown_revision_raises_revision_unknown`);
3. a shipped revision that is not the head: `StoreNotMigrated(revision, head)` (with one shipped revision this case and case 1 coincide; ambiguity A10);
4. `tracer_meta.schema_version` against `tracer_evalschema.SCHEMA_VERSION`: equal passes; a dotted-number string greater than the installed one (`0.0.2`, `0.0.10`, `0.1`, `1.0.0`, `10.0.0`) is `StoreSchemaVersionTooNew`; anything else that is not equal (older, `0.0`, `banana`, `0.0.1 `, an Arabic-Indic number) is `StoreSchemaVersionMismatch`; no row at all is `StoreSchemaVersionMismatch(stored=None)`. Both carry `.stored` (`str | None`) and `.installed` and put both in the message (`test_open_store_newer_schema_version_raises_too_new`, `test_open_store_older_schema_version_raises_mismatch`, `test_open_store_without_a_tracer_meta_row_is_a_mismatch_not_a_default`).

`evaldb_version` is returned and never compared (ADR 0021 Ambiguity 4). No message holds the URL, the host, the password or the database name (`test_open_store_errors_never_echo_the_database_url`). The repository functions of batch C do not call `open_store`: they keep working on a `create_all` database (`test_repository_functions_do_not_require_a_migrated_database`).

`tracer_meta` (`test_tracer_meta_records_schema_version`): after revision 0001 one row `(1, "0.0.1", importlib.metadata.version("tracer-evaldb"))`; a second singleton, a singleton other than 1 and another schema version are refused by the database; the row cannot be updated.

## 5. Deletion (P2-17a; ADR 0020)

### 5.1 The functions

```python
def delete_run(engine, run_id, *, actor, reason) -> DeletionResult
def delete_project(engine, project_id, *, actor, reason) -> DeletionResult
def delete_test_profile(engine, profile_id, *, actor, reason) -> DeletionResult      # all versions
def delete_definition(engine, kind, definition_id, *, actor, reason) -> DeletionResult  # all versions
def delete_dataset_item(engine, item_id, *, actor, reason) -> DeletionResult
def delete_export(engine, export_id, *, actor, reason) -> DeletionResult
def delete_snapshot(engine, snapshot_id, *, actor, reason) -> DeletionResult

@dataclass(frozen=True)
class DeletionResult:
    root_kind: str                 # the value of deletion_audit.root_kind
    counts: Mapping[str, int]      # read-only (item assignment is a TypeError); equal to the stored counts
    audit_uuid: uuid.UUID          # a version 7 UUID, the audit_uuid of the row; a bare UUID, not a TypedId
```

`test_deletion_api_shape` pins names, parameter order and kinds (`actor` and `reason` keyword-only without defaults, everything else positional-or-keyword without defaults), the re-exports and the result type. `kind` of `delete_definition` is one of `agent_target`, `persona`, `evaluator`, `metric_definition`, `metric_binding`, `rubric`, `pricing_catalog`, `dataset`; anything else (including `"Persona"`, `"persona "`, `""`, `"run"`, bytes) is a `ValueError` containing "kind"; `"test_profile"` is a `ValueError` containing "delete_test_profile" (ADR 0020 Ambiguity 8; `test_delete_definition_refuses_test_profile_and_names_the_right_root`). Identifier arguments follow batch C: the typed ID of the right class or a bare `uuid.UUID` of version 7; a `str`, `bytes`, `int`, `None`, a list or the ID of another class is a `TypeError`; a UUID that is not version 7 is a `ValueError` containing "version 7"; the engine must be the store engine (`TypeError`). All of that, and the checks of `actor` and `reason` (section 6), happen before the first statement (`test_deletion_arguments_are_checked_before_any_statement`, `test_ids_are_accepted_typed_or_as_bare_version_7_uuids`). There is no other function named `delete_*` (`test_roots_that_are_not_roots_do_not_exist`).

An ID that names no stored row (also an ID that names a row of another kind) is `RecordNotStored(kind, record_id)` with `kind` the table (`run`, `project`, `test_profile`, the definition kind, `dataset_item`, `export_bundle`, `config_snapshot`) and `version=None`; it is raised before anything is deleted, no write statement is sent, no audit row exists, and the message does not hold the actor (`test_delete_unknown_root_is_typed_error_and_writes_no_audit`, 14 roots).

### 5.2 The cascades (ADR 0020 Decision 4)

Every root deletes, in one transaction with its audit row, what the ADR lists; the tests build a store with a known number of rows in every table and compare the rows removed, per table, side tables included, with the literal table `DELTAS` of `evaldb_deletion_support.py` on both backends (`test_cascade_counts_parity_sqlite_postgres`, 14 cases, the SCOPE name: both backends produce exactly the same literals, which is the parity).

* `delete_run`: the run and its twelve kinds of run-owned rows (`test_delete_run_cascades_every_run_owned_kind`); the dataset items whose `artifact_id` or `transcript_id` names an artifact or transcript of the run, each with the exports that carry the item; the exports linked to the run in `export_bundle_run`; the run's snapshot with its member rows, only when no other run uses it (`test_delete_run_removes_orphaned_snapshot_only`: a snapshot nobody uses is never touched by another run's deletion, and the definitions extracted from snapshots stay); an item whose only link is `source_run_id`, and an item that holds another run's payload, stay (the one reference to the run that remains is `dataset_item.source_run_id`); a dataset that listed a deleted item is byte for byte unchanged and now names members that resolve to nothing (`test_delete_run_removes_dataset_items_and_dataset_reports_unresolved`: `load_dataset_item` raises `RecordNotStored` for them); `test_delete_run_removes_stored_exports_containing_it`; on documents the repository wrote, the bundle, its export and its snapshot go, the project, the agent target and the definitions stay and still load (`test_delete_run_of_a_real_stored_bundle_keeps_the_definitions_of_its_snapshot`).
* `delete_project`: the cascades of `delete_run` for every run of the project, then the project; a snapshot that a run of another project still uses is kept and goes with its last user (`test_delete_project_cascades_runs`, `test_delete_project_applies_the_run_cascades_to_every_run_first`).
* `delete_test_profile`: every version; the exports linked in `export_bundle_profile` (the real export of a bundle that lists the profile included); snapshots keep their `snapshot_profile_ref` row and stay valid (`load_snapshot` equals the original and verifies); the run bundle that listed the profile is `IncompleteRunBundle` with exactly the member `("test_profile", id, 1)`, and `allow_incomplete=True` returns `complete=False, bundle=None`; the run's rows are untouched (`test_delete_test_profile_removes_all_versions_snapshots_report_unresolved`).
* `delete_definition`: every version of that id in that table only (a row of another table with the same UUID stays: `test_delete_definition_removes_every_version_and_only_that_id`); for `dataset` the member rows go by foreign key; the snapshots that embed the definition stay valid, load, verify and are found by digest, for each of `evaluator`, `persona`, `rubric`, `metric_definition`, `metric_binding` (`test_delete_definition_leaves_snapshots_valid`); a run bundle that listed a deleted pricing catalog loads as incomplete (`test_delete_pricing_catalog_makes_the_run_bundle_incomplete`).
* `delete_dataset_item`: the item and the exports linked in `export_bundle_item`; datasets keep their versions (`test_delete_dataset_item_removes_the_exports_that_embed_it`).
* `delete_export`: the export and its three kinds of link rows; what it carried stays (`test_delete_export_leaves_what_it_carried`).
* `delete_snapshot`: a snapshot that no run references, with its member and profile-reference rows (`test_delete_snapshot_removes_an_unreferenced_snapshot_with_its_member_rows`); while a run references it, `SnapshotInUse(snapshot_id)` (a `StoreError`; the message holds the wire ID and not the actor), with no write statement, no change and no audit row; the foreign key says the same by itself; after `delete_run` of the last run the snapshot is gone already (`test_delete_snapshot_refused_while_a_run_references_it`).

### 5.3 Atomicity

`test_a_failed_deletion_changes_nothing`: for each of the 14 roots, a fault (an exception raised by the test from the engine) on the first, a middle and the last `DELETE` leaves every row of the store as it was, the audit table included. The audit tests add the fault on the audit `INSERT` (section 6).

### 5.4 Locks and transactions (ADR 0020 Decision 5), observed from outside

The tests pause the deletion in its thread at its first `DELETE` (a `before_cursor_execute` hook blocks it) and look at the database from another engine:

* **Postgres.** The root row and, for `delete_run` and `delete_project`, every run row and every snapshot row of those runs are locked `FOR UPDATE`: `SELECT ... FOR KEY SHARE NOWAIT` on each fails with SQLSTATE `55P03`; the rows of an unrelated run, project or snapshot are free (`FOR UPDATE NOWAIT` succeeds) (`test_deletion_takes_its_locks_before_it_deletes`, 14 roots). The first statement a deletion sends is the `SELECT ... FOR UPDATE` of the root row (`test_a_deletion_locks_before_it_reads_or_deletes`). Locks on dataset items and exports are unpinned (A17).
* **SQLite.** The transaction starts with `BEGIN IMMEDIATE` (the first statement the engine sees, `test_a_deletion_locks_before_it_reads_or_deletes`): with the deletion paused before its first `DELETE`, a second writer on a second engine (`busy_timeout_ms=0`) fails with `sqlalchemy.exc.OperationalError` containing "locked". The mechanism is unpinned (the oracle reads an execution option in the engine's `begin` hook; A19).
* **Races.** `test_concurrent_insert_during_run_delete_never_orphans` (the SCOPE name; cases `rescoring` and `export`; both backends): with `delete_run` paused, `store_run_bundle` of a rescoring bundle for the run, or `store_export` of an export that carries it, runs on another engine. Postgres: the writer is seen waiting for a lock (`pg_stat_activity.wait_event_type = 'Lock'`); after the deletion commits the rescoring fails with `StoreConstraintViolation(kind="foreign_key")` and the export with `ExportReferenceNotStored("run", id)`. SQLite: the writer fails with `OperationalError` "locked". Either way: no row refers to the run, no export and no link row exists, no row of the rescoring bundle exists, and for every audit count `rows before - rows after == count` (the audit says exactly what went). Also `test_concurrent_run_insert_during_project_delete_never_orphans` (a new run of the same project: foreign-key failure or `ProjectNotStored`) and `test_concurrent_run_insert_during_snapshot_delete_never_orphans` (a run that uses the snapshot being deleted).

## 6. The deletion audit row (P2-17b; ADR 0020 Decision 6)

`deletion_audit` has exactly the columns `audit_uuid, deleted_at, actor, reason, root_kind, counts` (`test_deletion_audit_counts_per_kind_exact`, 14 roots):

* `audit_uuid`: a version 7 UUID, equal to `DeletionResult.audit_uuid`; `deleted_at`: timezone-aware, between the Python clock read before the call and the one read after it (the store's clock is `datetime.now(UTC)` of the process, not the server's; A18); `actor`, `reason`, `root_kind` as given, as the root kind of the table below.
* `counts`: the JCS text (`json.dumps(sort_keys=True, separators=(",", ":"))` for integers and ASCII keys) of one object whose keys are exactly the key set of the root kind, zeros included, and whose values are `int` (not `bool`) of at least 0; equal to `DeletionResult.counts`. A row counts entity rows only; side-table rows are not counted.

| `root_kind` | Keys of `counts` |
|---|---|
| `run` | `run`, `artifact`, `transcript`, `turn`, `exchange`, `tool_call_event`, `stage_span`, `metric_result`, `run_verdict`, `usage_record`, `cost_record`, `cost_summary`, `run_bundle`, `config_snapshot`, `dataset_item`, `export_bundle` (16) |
| `project` | `project` and the 16 of `run` (17) |
| `test_profile` | `test_profile`, `export_bundle` |
| `agent_target`, `persona`, `evaluator`, `metric_definition`, `metric_binding`, `rubric`, `pricing_catalog`, `dataset` | the root's own table |
| `dataset_item` | `dataset_item`, `export_bundle` |
| `export_bundle` | `export_bundle` |
| `config_snapshot` | `config_snapshot` |

The table is the one of ADR 0020 Decision 6 (`test_the_count_key_sets_in_the_tests_are_the_adr_table` reads the ADR and compares). `test_zero_counts_are_written_not_left_out`; `test_each_deletion_adds_one_row_and_never_changes_an_earlier_one`.

**Content-free** (`test_deletion_audit_is_content_free`, 14 roots, and `test_deletion_audit_holds_no_text_digest_or_metadata_of_real_records`): every string of six or more characters and every byte string of eight or more bytes that the store held before the deletion, in every table, in every form an ID can take (wire form, hyphenated UUID, 32 hex, upper case, the 16 raw bytes) and a digest (hex, raw), is a needle; none occurs in the actor, reason, root kind or counts text or in the bytes of the row (the needles that are substrings of the allowed tokens are left out). The second test adds the turn text, the profile's name, the external references and the metadata strings of a real bundle.

**Atomic** (`test_delete_is_atomic_with_audit`, 14 roots): a fault on the audit `INSERT` leaves every row where it was and no audit row, and the same deletion then works and writes exactly one; `test_the_audit_row_is_written_in_the_deletions_own_transaction` (one `INSERT` into `deletion_audit`, no `COMMIT` statement, at most one `BEGIN`).

**`actor` and `reason`** (no defaults; `test_deletion_requires_actor_and_reason`, 7 functions): omitting either, passing them positionally, a non-`str` (`None`, `1`, `b"ops"`, a list, an object) is a `TypeError`; a `reason` outside `operator_request`, `retention_policy`, `data_subject_request` (including `""`, a different case, a space, `"other"`, `"system"`) is a `ValueError` containing "reason" that does not echo the value; an `actor` that is not a valid `HumanLabel` (empty, leading or trailing white space, a control character, NUL, DEL, a C1 control, a line or paragraph separator, a bidi override, a zero-width space) is a `ValueError` containing "actor" that does not echo it; zero statements, no audit row. `test_deletion_actor_rejects_credential_shaped_value` (seven shapes assembled from parts: an `sk-` key, an AWS access key id, a GitHub token, a Slack token, a JWT, a bare 40-character high-entropy token, a token inside a longer label): `ValueError` containing "actor", the value and no 8-character slice of it in the message, repr or args, zero statements. `test_deletion_accepts_each_reason_and_stores_labels_exactly`: Indic text with ZWNJ and ZWJ, NFC and NFD of the same label (kept distinct), a 500-character label, `user@example.org` are stored byte for byte on both backends.

## 7. Retention columns (P2-18; ADR 0020 Decision 8, ADR 0019 Decision 6a)

* The six privacy columns exist on `artifact`, `transcript` and `dataset_item` (`human_subject`, `human_subject_reason`, `consent_status`, `retain_until`, `delete_on_expiry`, `policy_ref`) and, with the prefix `caller_`, on `run`, and on no other table (`test_privacy_columns_exist_on_exactly_the_four_tables`). They mirror the model's `PrivacyMarker` for every golden fixture of the four entities and for markers with parts left out, with the same types (`False` is not `0`) (`test_retention_columns_mirror_privacy_marker`).
* Absent is NULL: no marker leaves six NULLs; a retention without a date or a policy leaves `retain_until` and `policy_ref` NULL and `delete_on_expiry` false (stated); a false marker leaves the other five NULL (`test_retention_absent_is_null_never_false`); the database refuses a stub value for an absent one (`test_the_database_refuses_false_standing_for_absent`; for the required marker of an artifact or transcript, no marker is a NOT NULL violation).
* **Partial indexes** `ix_artifact_retain_until`, `ix_transcript_retain_until`, `ix_dataset_item_retain_until` and `ix_run_caller_retain_until`: not unique, on the retention date alone, `WHERE <column> IS NOT NULL`, in `metadata` (both dialect options), in a `create_all` schema, in the Alembic schema (`test_migrated_database_has_the_retain_until_indexes`), and the query plan of a range query on the date names the index (SQLite `EXPLAIN QUERY PLAN`; Postgres with `enable_seqscan = off`) (`test_retain_until_index_exists`). This is a change to `tables.py` (red today).
* **No enforcement** (`test_no_retention_enforcement_code_exists`): outside `tables.py`, `codecs.py` and the frozen revisions no module of `src/tracer_evaldb` names `delete_on_expiry` or `retain_until`; nothing is named like a sweeper, purge or scheduler; no scheduler module is imported.

## 8. Errors and their parents (`test_batch_d_error_types_and_their_parents`)

| Type | Direct base | Raised by | Attributes |
|---|---|---|---|
| `StoreNotMigrated` | `StoreError` | `open_store` | `revision` (`str` or `None`), `head` |
| `StoreRevisionUnknown` | `StoreError` | `open_store`, `upgrade`, `downgrade` | `revision` |
| `StoreSchemaVersionError` | `StoreError` | (base) | `stored` (`str` or `None`), `installed` |
| `StoreSchemaVersionTooNew` | `StoreSchemaVersionError` | `open_store` | as above |
| `StoreSchemaVersionMismatch` | `StoreSchemaVersionError` | `open_store` | as above |
| `IrreversibleMigration` | `StoreError` | `rewrite_documents` | `table` |
| `DigestChangingUpgrade` | `StoreError` | `rewrite_documents` | `table` |
| `MixedSchemaVersions` | `StoreError` | `rewrite_documents` | `table` |
| `DowngradeDataLoss` | `StoreError` | `downgrade` | `tables` (sorted tuple) |
| `MigrationIntegrityError` | `StoreError` | a SQLite migration | `tables` (sorted tuple) |
| `SnapshotInUse` | `StoreError` | `delete_snapshot` | `snapshot_id` |

Deletion also raises the batch C `RecordNotStored`. All are in `errors.__all__` and `tracer_evaldb.__all__`. The two schema-version errors are siblings and are not evalschema errors (A9). No message holds a URL, a value, a document fragment, an actor or a reason; they name tables, kinds, revision ids, version strings and wire IDs.

## 9. Test inventory

Counts (the SQLite side and the tests without a database / the `[postgres]` cases): `test_evaldb_migrations.py` 43 / 24, `test_evaldb_version_policy.py` 61 / 61, `test_evaldb_deletion.py` 91 / 90, `test_evaldb_deletion_audit.py` 64 / 63, `test_evaldb_retention.py` 16 / 14, `test_evaldb_adr0021.py` 11 / 0, `test_evaldb_migration_doubles.py` 141 / 0: 427 / 252, 686 test cases in 111 test functions. Together with batches A to C: 1471 by default (1044 + 427) and 1065 postgres-marked (813 + 252). The existing 9832 tests (the evalschema suite and the evaldb suites of batches A to C, in the count of the lead) are not edited.

SCOPE names (all present under their exact names): P2-14 `test_alembic_head_matches_metadata_exactly`, `test_alembic_ini_holds_no_database_url`, `test_migrations_ship_in_wheel`, `test_programmatic_upgrade_api`, `test_open_store_refuses_unmigrated_database`, `test_open_store_never_auto_migrates`; P2-15 `test_migration_stairway_up_down_up`, `test_downgrade_base_on_populated_db_requires_explicit_data_loss_flag`, `test_sqlite_batch_migration_preserves_child_rows`, `test_sqlite_batch_migration_preserves_strict_and_triggers`, `test_postgres_migration_is_transactional`; P2-19 `test_tracer_meta_records_schema_version`, `test_open_store_newer_schema_version_raises_too_new`, `test_open_store_older_schema_version_raises_mismatch`, `test_fake_data_migration_rewrites_schema_version_and_preserves_digests`, `test_fake_data_migration_downgrade_restores_bytes_exactly`, `test_digest_changing_upgrade_of_versioned_entity_refused`, `test_mixed_schema_versions_in_one_database_rejected`, `test_adr0021_policy_rules_are_tested`; P2-17a `test_delete_run_cascades_every_run_owned_kind`, `test_delete_run_removes_orphaned_snapshot_only`, `test_delete_project_cascades_runs`, `test_delete_test_profile_removes_all_versions_snapshots_report_unresolved`, `test_delete_definition_leaves_snapshots_valid`, `test_delete_run_removes_dataset_items_and_dataset_reports_unresolved`, `test_delete_run_removes_stored_exports_containing_it`, `test_cascade_counts_parity_sqlite_postgres`, `test_delete_unknown_root_is_typed_error_and_writes_no_audit`, `test_concurrent_insert_during_run_delete_never_orphans`, `test_delete_snapshot_refused_while_a_run_references_it`; P2-17b `test_delete_is_atomic_with_audit`, `test_deletion_audit_is_content_free`, `test_deletion_audit_counts_per_kind_exact`, `test_deletion_requires_actor_and_reason`, `test_deletion_actor_rejects_credential_shaped_value`; P2-18 `test_retention_columns_mirror_privacy_marker`, `test_retention_absent_is_null_never_false`, `test_retain_until_index_exists`. `test_every_test_named_by_the_scope_rows_of_batch_d_exists` (one case per row) checks the SCOPE table against the tests.

Names added by this batch (each makes a contract item testable), in `test_evaldb_migrations.py`: `test_revision_chain_is_linear_and_follows_the_naming_convention`, `test_every_shipped_revision_declares_what_a_downgrade_drops`, `test_revision_0001_is_frozen_text_and_never_reads_the_live_metadata`, `test_revision_template_generates_a_headed_typed_revision`, `test_importing_the_package_does_not_load_alembic`, `test_migrations_package_holds_no_database_url_and_no_credential`, `test_migrate_arguments_are_checked_before_any_statement`, `test_alembic_config_carries_the_engine_and_no_url`, `test_catalog_comparison_detects_drift`, `test_migrated_schema_has_the_immutability_triggers` (the batch C test of that name re-run on the Alembic schema, ambiguity A10 of batch C), `test_revision_0001_writes_the_tracer_meta_row`, `test_open_store_errors_never_echo_the_database_url`, `test_repository_functions_do_not_require_a_migrated_database`, `test_alembic_command_line_upgrades_and_checks_with_x_url`, `test_the_only_audit_row_is_enough_to_require_the_flag`, `test_downgrade_that_drops_a_column_with_values_requires_the_flag`, `test_a_multi_step_downgrade_is_refused_whole_before_the_first_step`, `test_sqlite_foreign_keys_are_off_during_a_migration_and_on_after`, `test_sqlite_foreign_key_check_failure_rolls_the_revision_back`, `test_failed_migration_rolls_back_to_the_previous_revision`, `test_failed_first_migration_leaves_an_empty_database`, `test_upgrade_over_an_unmanaged_schema_changes_nothing`, `test_state_helpers_see_a_difference`; in `test_evaldb_version_policy.py`: `test_open_store_without_a_tracer_meta_row_is_a_mismatch_not_a_default`, `test_open_store_unknown_revision_raises_revision_unknown`, `test_migrate_refuses_a_database_at_an_unknown_revision`, `test_test_only_revisions_are_never_part_of_the_shipped_chain`, `test_irreversible_data_migration_is_refused_before_any_change`, `test_digest_changing_upgrade_of_config_snapshot_refused`, `test_a_validation_failure_of_the_new_models_propagates_and_rolls_back`, `test_migrations_drop_and_recreate_immutability_triggers_inside_the_migration_transaction`, `test_rewriting_alone_takes_the_triggers_away_and_brings_them_back`, `test_postgres_other_sessions_never_see_the_trigger_window`, `test_the_data_migration_runs_on_a_database_that_was_never_populated`, `test_create_all_database_is_not_a_migration_target`; in `test_evaldb_deletion.py`: `test_deletion_api_shape`, `test_deletion_arguments_are_checked_before_any_statement`, `test_delete_definition_refuses_test_profile_and_names_the_right_root`, `test_ids_are_accepted_typed_or_as_bare_version_7_uuids`, `test_delete_run_of_a_real_stored_bundle_keeps_the_definitions_of_its_snapshot`, `test_delete_project_applies_the_run_cascades_to_every_run_first`, `test_delete_definition_removes_every_version_and_only_that_id`, `test_delete_pricing_catalog_makes_the_run_bundle_incomplete`, `test_delete_dataset_item_removes_the_exports_that_embed_it`, `test_delete_export_leaves_what_it_carried`, `test_delete_snapshot_removes_an_unreferenced_snapshot_with_its_member_rows`, `test_roots_that_are_not_roots_do_not_exist`, `test_deletion_takes_its_locks_before_it_deletes`, `test_a_deletion_locks_before_it_reads_or_deletes`, `test_concurrent_run_insert_during_project_delete_never_orphans`, `test_concurrent_run_insert_during_snapshot_delete_never_orphans`, `test_a_failed_deletion_changes_nothing`; in `test_evaldb_deletion_audit.py`: `test_the_key_sets_of_the_counts_are_those_of_the_adr`, `test_zero_counts_are_written_not_left_out`, `test_each_deletion_adds_one_row_and_never_changes_an_earlier_one`, `test_the_database_refuses_a_reason_or_root_kind_outside_the_closed_sets`, `test_deletion_audit_holds_no_text_digest_or_metadata_of_real_records`, `test_the_audit_row_is_written_in_the_deletions_own_transaction`, `test_deletion_accepts_each_reason_and_stores_labels_exactly`, `test_ordinary_labels_that_resemble_nothing_secret_are_accepted`; in `test_evaldb_retention.py`: `test_the_database_refuses_false_standing_for_absent`, `test_privacy_columns_exist_on_exactly_the_four_tables`, `test_migrated_database_has_the_retain_until_indexes`, `test_no_retention_enforcement_code_exists`; in `test_evaldb_adr0021.py`: `test_adr0020_invariants_are_tested`, `test_the_count_key_sets_in_the_tests_are_the_adr_table`, `test_the_names_added_to_the_scope_rows_exist`, `test_batch_d_error_types_and_their_parents`; in `test_evaldb_migration_doubles.py`: `test_rewrite_schema_version_touches_the_version_members_and_nothing_else`, `test_plain_fake_step_is_exact_in_both_directions_on_every_golden_document` (one case per golden valid document, 138 of them), `test_the_refused_variants_are_really_bad`, `test_the_validate_double_accepts_the_plain_step_and_sees_a_changed_content`.

`test_adr0021_policy_rules_are_tested` reads ADR 0021: each of the 15 numbered rules of "## Decision" and each of the six invariants cites at least one test that exists (an AST scan of `packages/evaldb/tests/test_*.py`); there is no pending list. `test_adr0020_invariants_are_tested` does the same for the 14 invariants of ADR 0020.


## 10. Evidence (`docs/phase2/evidence/red/batch-d/`)

Logs: `red.py3.11.log` to `red.py3.14.log` (the seven new test files against the committed tree), `red-postgres15.py3.11.log` and `red-postgres18.py3.14.log` (the same, `-m postgres`), `oracle-sqlite.py3.11.log` to `oracle-sqlite.py3.15.log` (the whole `packages/evaldb/tests` directory against the scratch oracle), `oracle-postgres15.py3.11.log`, `.py3.14.log`, `oracle-postgres18.py3.11.log`, `.py3.14.log` (the same, `-m postgres`), `oracle-src.patch` (the oracle: new `migrate.py`, `versioning.py`, `deletion.py`, `migrations/{__init__,helpers}.py`, `0001_initial_schema.py`, `alembic.ini`, and edits of `env.py`, `script.py.mako`, `errors.py`, `engine.py`, `tables.py`, `__init__.py`; it applies to HEAD with `patch -p1` and reproduces the oracle's source exactly; qa's reference, the dev owns the real code and may differ) and `mutants.txt`.

Counts. Red on the committed tree: 427 selected by default, 161 failed, 98 errors and 168 passed, on each of 3.11, 3.12, 3.13 and 3.14; Postgres 15.19 (3.11) and 18.6 (3.14): 252 selected, 154 failed, 84 errors and 14 passed. The errors are all `ModuleNotFoundError: No module named 'tracer_evaldb.migrate'` at the setup of a test that uses the `migrate` fixture; the failures are `AttributeError` for the missing `open_store`, `delete_*`, `DeletionResult` and error names (135), the deletion tests that report "the deletion never reached its first DELETE (the thread ended first: AttributeError ...)" (18, the helper `wait_reached` makes a missing function fail at once instead of after a 60 second timeout), and the structural checks (no `0001_*.py` file, no `alembic.ini`, no `migrations/__init__.py` or `helpers.py` in the package, no `ix_artifact_retain_until`, eight modules where eleven are expected). The 168 passes (14 on Postgres) are guards that hold on a tree without the batch: the 138 golden-document cases and the three other cases of the migration doubles, the SCOPE and ADR name tests (`test_adr0021_policy_rules_are_tested`, `test_adr0020_invariants_are_tested`, `test_every_test_named_by_the_scope_rows_of_batch_d_exists`, `test_the_names_added_to_the_scope_rows_exist`), the count-key tables, `test_no_retention_enforcement_code_exists`, `test_repository_functions_do_not_require_a_migrated_database`, and the retention behaviours that batch B already implements (the six columns, absent is NULL, the CHECKs, the closed sets of the audit table); `test_retain_until_index_exists` and `test_migrated_database_has_the_retain_until_indexes` are red. Oracle: 1471 passed and 1065 deselected on SQLite on each of 3.11, 3.12, 3.13, 3.14 and 3.15 (1044 of batches A to C plus 427); 1065 passed and 1471 deselected on Postgres 15.19 and 18.6, each on 3.11 and 3.14 (813 plus 252). Mutants: 121, 114 caught, 7 equivalent survivors explained in `mutants.txt`; the only change to the tests after the mutant run is the hang detection of the pause helpers (`wait_reached`), which turns a wait that cannot end into an immediate failure and changes no outcome.

Method. Oracle: a scratch copy of the repository under the scratchpad with the hand-written source of `oracle-src.patch` (revision 0001 is generated from the metadata once and kept as frozen DDL text). Postgres: `docker run` of `postgres:15@sha256:baa22e62...` and `postgres:18@sha256:edb0c08d...` (the digests of the batch A CI contract), bound to 127.0.0.1 on a random port with a password from `openssl rand -hex 24` held only in the shell that runs pytest and scrubbed from the logs; database `tracer_test` with `Asia/Kolkata` as its time zone, database `tracer_ascii` (SQL_ASCII), and one `tracer_mig_*` database per migration test, dropped by the test; every container removed after its run. No container that qa did not create was touched. Environments: one `UV_PROJECT_ENVIRONMENT` directory per Python version and role under the scratchpad; the shared `.venv` was not used or changed. Mutants: a Python runner applies one text edit to a pristine copy of the oracle source, runs the batch D files and the scaffold file (SQLite, 3.14, stop at the first failure) or the matching `-k` selection of the postgres-marked tests (Postgres 18.6), and restores the files. Paths: `<repo>`, `<scratchpad>`, `~` and `<user>` replace the local paths and the user name in every log.


## 11. Findings for the implementer (from building the oracle)

* **F1. `import alembic` loads `pygments`** where pytest is installed (through Mako). The batch A whitelist test fails if any module outside `migrations/` imports alembic at import time (section 1). The oracle imports it inside the functions.
* **F2. A batch rebuild of a SQLite table drops its triggers and its `COLLATE "BINARY"`** (not `STRICT` and `WITHOUT ROWID`: with the locked SQLAlchemy 2.1.6 and Alembic 1.20.1 the reflection keeps both, so the `table_kwargs` of the helper are belt and braces and two oracle mutants that remove them are equivalent; this answers the SCOPE item "`sqlite_strict` preserved by Alembic batch mode" for these versions, and the lower-bound leg is the same pair); `op.batch_alter_table(..., recreate="always")` also rebuilds a Postgres table. `add_column` of a nullable column on SQLite is `ALTER TABLE ADD COLUMN` (no rebuild) unless `recreate="always"`. An explicit `COLLATE "BINARY"` is SQLite's default, so the catalog comparison of the tests drops it.
* **F3. `PRAGMA foreign_keys` is a no-op inside a transaction**, and `create_store_engine` begins one at the first statement of a connection: the pragma has to go through the DBAPI connection (`connection.connection.dbapi_connection.execute(...)`) before SQLAlchemy runs anything.
* **F4. Alembic details:** `context.get_x_argument(as_dictionary=True)` (not `config.get_x_argument`); `transactional_ddl=True` is harmless on SQLite but not needed (the engine's own BEGIN makes every revision one transaction); `ScriptDirectory.get_revision(unknown)` raises `CommandError` instead of returning `None`; `on_version_apply` runs inside each revision's transaction (the place for `foreign_key_check`).
* **F5. `delete_project` has to lock every run's snapshot before its first DELETE**, not one run at a time: a first version of the oracle locked a snapshot inside the per-run cascade and `test_deletion_takes_its_locks_before_it_deletes[project]` caught it. A slice bug in the oracle (`run_bundle` missing from the run-owned tables) was caught by the delta table.
* **F6. The populated store of a bundle has no `test_profile` row** unless the `RunBundle` lists the profile (`test_profiles=[...]`); the snapshot only references it. `populate` lists it.
* **F7. `compare_metadata` is empty on a `create_all` database on both backends with `compare_type=True`** but it does not see CHECK constraints, triggers or STRICT, so the tests compare a catalog fingerprint too and prove that both detect a dropped index (`test_catalog_comparison_detects_drift`).

## 12. Ambiguities, with recommendations (the lead auto-approves; qa implemented the recommendation in the tests)

A1. **How a test-only revision is registered.** ADR 0021 rule 15 says "never registered in the package's migration chain" and gives no mechanism. Recommendation: the keyword argument `version_locations` of `upgrade`, `downgrade` and `alembic_config` (section 2.4). Rejected: monkeypatching, an environment variable, a private config builder in the tests. Trade-off: one production argument that only tests and extensions use.

A2. **Where `alembic.ini` lives.** P2-14 says "`alembic.ini` holds no URL" without a path. Recommendation: `packages/evaldb/alembic.ini`, repository only (the CLI and the `alembic check` step of the lint job), not in the wheel; the API never reads it.

A3. **The wheel ships `migrations/__init__.py` and `migrations/helpers.py`.** Batch A required only `env.py`, `script.py.mako` and `versions/__init__.py`. Recommendation: both added (the helpers are imported by revisions at run time).

A4. **Lazy import of alembic** (finding F1). Recommendation: as written; rejected: adding `pygments` to the whitelist of an earlier test (qa may not weaken or edit it; the lead may decide otherwise).

A5. **`DOWNGRADE_DROPS` as the declaration behind the data-loss flag.** ADR 0021 rule 13 says "a downgrade that would drop tables or columns holding rows". Recommendation: each revision declares what it drops and the API counts (section 2.5). Rejected: inspecting the DDL a downgrade will run (not knowable before it runs); a flag per revision without counting (it would refuse an untouched database).

A6. **`rewrite_documents` is text-level and covers `doc`, `doc_sha256` and `schema_version` only.** ADR 0021 rule 10 also lists "the pure evalschema upgrade step" and "projections". Recommendation: the framework owns the order of checks, the transaction, the triggers and the CHECK literals; a real 0.0.2 revision supplies `forward`, `backward` and `validate` (the evalschema upgrade function and the new models) and writes any changed projection itself inside `rewriting(...)`. Rejected for now: a `project` callback (nothing to test it with until a real upgrade exists).

A7. **Byte-exact downgrade is proved before the upgrade** by `backward(forward(x)) == x` on every stored document, so `IrreversibleMigration` can be raised before any change (rule 12). Recommendation: as written.

A8. **`tracer_meta.evaldb_version`.** ADR 0021 Ambiguity 4: "the evaldb version of the last migration, written by migrations only". Pinned: revision 0001 writes the installed version; `rewrite_documents` does not write it. Recommendation: pin more when the first revision after 0001 exists (a data migration run by a newer evaldb should update it).

A9. **The version errors are not subclasses of the evalschema `SchemaVersionTooNew` and `SchemaVersionMismatch`.** A store error is not a document error; one base `StoreSchemaVersionError` (a `StoreError`) joins the two. Rejected: the dual base (two constructors, two message formats).

A10. **"Older revision" cannot be tested with one shipped revision.** `open_store` case 3 (a shipped revision below the head) coincides with case 1. Recommendation: when revision 0002 ships, add a stairway of `open_store` (qa writes it).

A11. **`upgrade` over a schema made by `create_all`.** ADR 0021 is silent. Pinned: the call fails and changes nothing, with any error. Recommendation: a typed refusal (`StoreNotMigrated`-like, naming that tables exist without a revision) in a later batch.

A12. **No `tracer_meta` row** is `StoreSchemaVersionMismatch(stored=None)`: a missing record is not read as "current" (never assume).

A13. **Unparsable stored version** (`banana`, a trailing space, non-ASCII digits) is `Mismatch`, not `TooNew`: it cannot be called newer.

A14. **`DeletionResult.audit_uuid` is returned** (a bare `uuid.UUID`). ADR 0020 says it is never exported or shown as an entity ID; the tests need it to find the row. Recommendation: keep it on the result, a bare UUID without prefix.

A15. **`delete_definition` argument types.** The typed ID class of the kind, or a bare version 7 UUID. Recommendation: as written (mirrors `latest_version`).

A16. **SQLite races are `OperationalError` ("database is locked"), not typed.** One writer at a time is the design; a typed busy error belongs to Phase 5.

A17. **Which rows a deletion locks on Postgres** beyond the root, the runs and their snapshots (dataset items, exports) is unpinned: the tests pin that nothing orphaned can result (the races), not the lock set.

A18. **`deleted_at` is the Python clock** (`datetime.now(UTC)`), so a test can bracket it; the server clock is not used (a container's clock may drift from the host's).

A19. **The SQLite `BEGIN IMMEDIATE` mechanism** is the implementer's (the oracle passes an execution option that the engine's `begin` hook reads). Pinned: the first statement of a deletion on SQLite is `BEGIN IMMEDIATE`, and a second writer is refused while it runs.

A20. **Index names** `ix_<table>_<column>` for the four retention indexes follow `_ix` of batch B.

A21. **ADR 0021 rule 11 for `ConfigSnapshot`** (Ambiguity 2) is implemented through the same `content_digest` member check; a snapshot's digest domain differs (`snapshot_digest_domain`), which `validate` covers.

A22. **`DowngradeDataLoss` names tables, not counts.** Row counts are not content, but a table list is enough for an operator.

A23. **The command line takes `-x url=<URL>`** (the URL may carry a password on a command line; the tests use SQLite files only). Recommendation: document `TRACER_...`-style environment variables later if operators ask; not pinned.

A24. **`downgrade` of a database that was never migrated** (no revision). ADR 0021 is silent. Recommendation: a no-op, as Alembic's own `downgrade base` on an empty database; rejected: an error (an operator's idempotent script would fail on a fresh database).

A25. **`rewrite_documents` requires `validate`.** A migration without the models of the new version would let a changed digest of digested content through unseen (rule 11 says "or a recomputed digest that differs"). Recommendation: required keyword; the member check is kept as a second line that does not depend on `validate` (`digest_member_lax`).

A26. **The pool of the caller's engine** is the caller's: `upgrade` and `downgrade` must not dispose it (`engine.pool` is the same object afterwards).

A27. **T-DRIFT.** This document and the new tests were written without the tokens the Phase 1 T-DRIFT scan bans; the lead re-runs the Phase 1 suite after committing them.
