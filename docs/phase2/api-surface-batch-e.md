# Phase 2 batch E contract: access-pattern indexes, Indic storage, verification, measurement (P2-20, P2-21, P2-23, P2-26)

Author: qa, 2026-10-24. Source: `docs/phase2/SCOPE.md` (sections 1.7 and 2, Decisions D1 to D5), ADR 0019 (Decisions 15 and 18, Table 2, invariant I20), ADR 0020 (Decisions 3, 7 and 8), ADR 0021 (rules 3, 5, 9 and 14), ADR 0022, the batch A to D contracts, the implemented `packages/evaldb/src/tracer_evaldb`, the Indic storage fixture written by the indic-linguistics agent, and the decision log (entries "PHASE 2"). qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or HANDOFF, and no earlier test (the one change an earlier test needs is a NAMED SPEC CHANGE, section 4.5, delivered as a patch, not applied). Every statement below is pinned by a test named in section 8, or is marked (unpinned).

## 0. Where the tests are and how to run them

`packages/evaldb/tests/` (the directory of batches A to D). Five new test files, two new helper modules, one fixture file that is not qa's:

| File | Holds |
|---|---|
| `evaldb_index_support.py` | helpers, no test: the exact index tables (25 new, 13 old), a catalog reader for the indexes of a database on either backend (`PRAGMA index_list`/`index_xinfo` on SQLite, `pg_index` on Postgres: never the text of `indexdef`), the plan readers (facts only), statement capture |
| `evaldb_measure.py` | the measurement harness of P2-23 (evidence tooling, section 7); not a test module, no `test_` function |
| `test_evaldb_indexes.py` | P2-20 (the index set, the 62 access-pattern plans on each backend, the statements the package sends, foreign key coverage, redundancy, keyset paging) |
| `test_evaldb_revision_0002.py` | revision 0002 (chain, frozen text, effect, stairway, transactions) and the `open_store` tests of an older revision (batch D ambiguity A10) |
| `test_evaldb_indic_storage.py` | P2-21 |
| `test_evaldb_verify.py` | P2-26 |
| `test_evaldb_measure.py` | the promises of the harness (format, refusals, no URL or path in the output) |
| `fixtures/indic_storage.json` | 79 cases written by indic-linguistics (read-only for qa; `needs_native_review` is true for every one) |

Commands are those of batches A to D: `uv run --locked --all-groups pytest` runs the SQLite side; `TRACER_TEST_POSTGRES_URL=... TRACER_TEST_POSTGRES_SQL_ASCII_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests` runs the Postgres side. No new environment variable for the tests, no change to `conftest.py`, the root `pyproject.toml` or CI. The index plan tests and the revision tests use the `mig` fixtures of batch D (an EMPTY database per test, migrated by Alembic: the plans are those of the schema Alembic builds); the Indic and verify tests use the `db` fixture of batch B (`metadata.create_all`).

Counts: 51 test functions, 123 default cases (SQLite side and the tests without a database) and 108 `postgres`-marked cases, 231 in all.

## 1. Modules and names (pinned)

```
packages/evaldb/src/tracer_evaldb/
  tables.py                  the 25 new indexes of section 2 (so metadata.create_all builds them)
  migrations/versions/
    0002_access_pattern_indexes.py   revision "0002", down_revision "0001"      (new)
  verify.py                  verify_database, VerifyReport, VerifyFinding, VERIFY_PROBLEMS, VERIFY_NOTES   (new)
  __init__.py                re-exports: verify_database, VerifyReport, VerifyFinding, VERIFY_PROBLEMS, VERIFY_NOTES
```

`tracer_evaldb.__all__` gains those five names. `verify.py` imports only the standard library, `sqlalchemy`, `tracer_evalschema` and `tracer_evaldb` (the batch A import whitelist). Nothing else in `src` changes, except that `head_revision()` is now `"0002"`.

## 2. The indexes (P2-20)

### 2.1 The set

The CREATE INDEX indexes of the store are exactly these 38 (primary keys, the UNIQUE constraint `uq_turn_transcript_seq` and the key b-trees of WITHOUT ROWID tables are not indexes in this sense). Name rule for a plain index: `ix_<table>_<column>_<column>`; key columns in this order; none of the 25 new ones is unique, partial or descending. `test_access_pattern_indexes_exist` compares name, table, key columns in order, unique, partial and direction, on `metadata`, on the schema `metadata.create_all` builds and on the schema Alembic builds.

New in revision 0002 (25):

| Table | Index | Key columns | Serves (ADR 0019 Decision 15) |
|---|---|---|---|
| run | `ix_run_project_id_id` | `project_id, id` | runs by project ordered by id (UUIDv7 keyset), both directions; the cascade of `delete_project` |
| run | `ix_run_agent_target_id_agent_target_version` | `agent_target_id, agent_target_version` | runs by `(agent_target_id, version)` and by the id alone |
| run | `ix_run_mode` | `mode` | runs by `mode` |
| run | `ix_run_observed_tier` | `observed_tier` | runs by `observed_tier` |
| run | `ix_run_started_at` | `started_at` | runs by `started_at` (range) |
| run | `ix_run_config_snapshot_id` | `config_snapshot_id` | runs by snapshot; the RESTRICT check of `delete_snapshot` |
| artifact | `ix_artifact_run_id` | `run_id` | artifacts by run; the cascade |
| artifact | `ix_artifact_sha256` | `sha256` | artifacts by `sha256` |
| transcript | `ix_transcript_run_id` | `run_id` | transcripts by run; the cascade |
| turn | `ix_turn_run_id` | `run_id` | turns by run; the cascade (turns by `(transcript_id, seq)` is the existing unique constraint) |
| exchange | `ix_exchange_run_id` | `run_id` | exchanges by run; the cascade |
| tool_call_event | `ix_tool_call_event_run_id` | `run_id` | tool calls by run; the cascade |
| stage_span | `ix_stage_span_run_id` | `run_id` | spans by run; the cascade |
| metric_result | `ix_metric_result_run_id` | `run_id` | results by run; the cascade |
| metric_result | `ix_metric_result_metric_id_metric_version_status` | `metric_id, metric_version, status` | results by `(metric_id, metric_version, status)` and by its leading prefixes |
| run_verdict | `ix_run_verdict_run_id` | `run_id` | the cascade (see ambiguity A3) |
| run_verdict | `ix_run_verdict_status` | `status` | verdicts by status |
| usage_record | `ix_usage_record_run_id` | `run_id` | usage by run; the cascade |
| cost_record | `ix_cost_record_run_id` | `run_id` | cost records by run; the cascade |
| cost_summary | `ix_cost_summary_run_id` | `run_id` | cost summaries by run; the cascade |
| run_bundle | `ix_run_bundle_run_id` | `run_id` | bundles by run; the cascade |
| config_snapshot | `ix_config_snapshot_content_digest_value` | `content_digest_value` | snapshot by digest (`find_snapshot_by_digest`, which also filters on the scheme) |
| metric_definition | `ix_metric_definition_key` | `key` | metric definitions by `key` (`key` is a reserved word: see 2.3) |
| dataset_item | `ix_dataset_item_artifact_id` | `artifact_id` | the dataset items of a run's artifacts (`delete_run`; ambiguity A3) |
| dataset_item | `ix_dataset_item_transcript_id` | `transcript_id` | the dataset items of a run's transcripts (`delete_run`; ambiguity A3) |

Existing since revision 0001 (13), unchanged: `ix_artifact_retain_until`, `ix_transcript_retain_until`, `ix_dataset_item_retain_until`, `ix_run_caller_retain_until` (partial, where not NULL), `ix_snapshot_member_kind_def_id_def_version`, `ix_snapshot_profile_ref_profile_id_profile_version`, `ux_external_ref_index_run_key` (unique, partial: entity kind `run`), `ix_external_ref_index_run_id_entity_kind_key_sha256`, `ix_dataset_member_item_id`, `ix_run_bundle_member_kind_record_id`, `ix_export_bundle_run_run_id`, `ix_export_bundle_item_item_id`, `ix_export_bundle_profile_profile_id` (their columns are the table `OLD_INDEXES` of `evaldb_index_support.py`).

Definitions by `(id, version DESC)`: **no new index** (ambiguity A1). The primary key `(id, version)` is read backward on both backends; `latest-version-of-<table>` and `newest-version-first-of-<table>` (nine tables each) assert the key serves them, with no sort.

### 2.2 Three more rules, each a test

* **`test_every_foreign_key_is_led_by_a_key_or_an_index`.** Deleting a parent looks up its children by the foreign key columns. SQLite's plan of one `DELETE FROM run` shows 12 scans of child tables today. Each of the 23 foreign keys of ADR 0020 Decision 3 has its columns as a leading prefix of the child's primary key, a UNIQUE constraint or a CREATE INDEX index.
* **`test_no_index_duplicates_a_key_or_another_index`.** A plain index whose columns lead the primary key, a UNIQUE constraint or another plain index of its table is refused (partial and unique indexes are exempt). Keeps the next author from adding `(run_id)` next to `(run_id, x)`.
* **`test_the_pattern_table_covers_every_index_and_every_versioned_table`.** The 62 patterns name each of the 38 indexes at least once.

### 2.3 DDL equality (a trap, found while building the oracle)

Revision 0002 is frozen DDL text and must produce the very schema `metadata.create_all` produces. `key` is a reserved word and SQLAlchemy writes `ON metric_definition ("key")`; a revision that writes `(key)` is semantically the same index on both backends, but the schema text differs on SQLite and `test_alembic_head_matches_metadata_exactly` (batch D) fails. Write the quotes.

## 3. How index use is asserted

A plan is reduced to facts: the set of named indexes it uses, whether it searched the table's own key (SQLite), the tables it reads without an index search, and whether it has a separate sort step. No plan text is compared (SQLite and Postgres spell plans differently from version to version).

* **SQLite** (`test_sqlite_query_plans_use_indexes`, one case per pattern): `EXPLAIN QUERY PLAN` of the Core statement exactly as the package would send it (bind processors applied, taken from `before_cursor_execute`). The named index is in the plan (`USING [COVERING] INDEX <name>`) or, for the key-served patterns, `USING PRIMARY KEY`; no `SCAN <store table>`; for the `ordered` patterns no `USE TEMP B-TREE FOR ORDER BY`.
* **Postgres** (`test_postgres_plans_can_use_indexes`, marked `postgres`, one case per pattern): `EXPLAIN (FORMAT JSON)` with `SET LOCAL enable_seqscan = off`, read from the node tree: `Index Name` values, `Node Type` `Seq Scan` (with its `Relation Name`) and `Sort`. The test says "can use": the tables are empty and have no statistics, so which of two usable indexes wins is the planner's; the keyset patterns therefore accept `run_pkey` as an alternative to `ix_run_project_id_id` (both deliver the order). For the `ordered` patterns bitmap scans are also discouraged (`enable_bitmapscan = off`), because a bitmap scan plus a sort says nothing about whether the index can deliver the order.
* **The statements the package sends** (`test_repository_and_deletion_statements_are_served_by_indexes`, both backends): a scenario stores two bundles, reads, looks up and runs every deletion root; every captured SELECT or DELETE with a WHERE clause is explained with the parameters it was sent with; none reads a store table without an index (`tracer_meta` and `alembic_version` are exempt). This is what exposed the dataset item indexes. On SQLite the plan of a DELETE also shows the scans of its foreign key actions; on Postgres the foreign key test carries that.
* **Keyset paging** (`test_keyset_pagination_runs_by_project_is_time_ordered`): 23 runs of one project and 7 of another, UUIDv7 ids 7 ms apart, inserted in shuffled order. `WHERE project_id = :p AND id > :after ORDER BY id LIMIT 5` returns the project's runs in creation order (id bytes and the 48-bit time prefix both ascending), once each, none of the other project, and a run created between two pages is returned once and not skipped; the mirror `id < :before ORDER BY id DESC` returns the newest five; the plan of a page has no sort and no scan. The statement shape is the pinned access pattern; there is no list API in Phase 2.

## 4. Revision 0002 and the stairway

### 4.1 The revision

`0002_access_pattern_indexes.py`: `revision = "0002"`, `down_revision = "0001"`, `branch_labels = None`, SPDX header, `DOWNGRADE_DROPS = ()` (an index holds no row and no value, so a downgrade through it needs no `allow_data_loss`). `upgrade` creates the 25 indexes of 2.1 with `CREATE INDEX` text, `downgrade` drops them. Revision 0001 is never regenerated: `test_revision_0001_is_unchanged_by_the_new_indexes` finds none of the 25 names in its text. Revision 0002 is frozen text like 0001: it imports none of `tables`, `schema`, `codecs`, `repository`, `deletion`, `columns`, `engine`, names no `metadata`, and spells every index name as a string literal (`test_revision_0002_is_frozen_text_and_never_reads_the_live_metadata`). One transaction, DDL included, on both backends: a fault at the 12th `CREATE INDEX` (or 12th `DROP INDEX` of the downgrade) leaves revision 0001 (or 0002) with none of the change (`test_revision_0002_is_one_transaction`).

### 4.2 What it does

`test_revision_0002_adds_exactly_the_access_pattern_indexes`: the catalog of a database at 0001 and after the upgrade differ by exactly the 25 index entries (nothing changed, no trigger changed). `test_revision_0002_keeps_every_row_and_the_store_readable`: a store filled at 0001 (all 26 entity tables) has every row equal after the upgrade and loads its bundle. `test_revision_0002_downgrade_restores_revision_0001_exactly`: catalog, rows and triggers equal to 0001, no flag. `test_revision_0002_stairway_up_down_up` (empty and filled): ADR 0021 rule 14 for the first real revision, ending with `base` and up again. `test_downgrade_through_0002_to_base_still_asks_for_the_flag_on_a_filled_store`. `test_revision_0002_upgrade_over_a_current_database_sends_no_write`.

### 4.3 `open_store` at an older revision (batch D A10)

With two shipped revisions the case 3 of `open_store` exists: a shipped revision below the head is `StoreNotMigrated(revision="0001", head="0002")`, whose message says to run the upgrade, distinct from case 1 (`revision is None`) and from `StoreRevisionUnknown`. It sends no write or DDL statement, changes nothing, never migrates, and is checked before the schema version (at 0001 `tracer_meta` is current, so only the revision can refuse). After the upgrade `StoreInfo.revision == "0002"`. Tests: `test_open_store_at_an_older_shipped_revision_is_not_migrated`, `test_open_store_at_an_older_revision_changes_nothing_and_never_migrates`, `test_open_store_checks_the_revision_before_the_schema_version`, `test_open_store_after_the_upgrade_returns_the_head`.

### 4.4 `upgrade` over a `create_all` schema (batch D A11)

Unchanged and still unpinned beyond "fails and changes nothing".

### 4.5 NAMED SPEC CHANGE REQUEST to staff-reviewer: the head literal of two batch D files

A new shipped revision changes the head, and 51 SQLite cases (and their Postgres twins) of two batch D files pin it as `"0001"`: `test_evaldb_migrations.py` (`HEAD = "0001"` and four literals) and `test_evaldb_version_policy.py` (`HEAD = "0001"` and one literal). They fail for that reason alone: the test-only revisions they write chain off `HEAD`, and a second head appears. One Postgres-only test needs the same change for a different reason, found only by the Postgres run: `test_postgres_migration_is_transactional` faults the downgrade to `base` at the fifth `DROP TABLE` and expects the database to stay at `HEAD`; with two revisions the downgrade first undoes 0002 (its own committed transaction) and the fault then rolls back the undoing of 0001, so the database stands at `"0001"`, which is the better statement of "one transaction per revision". qa does not edit an earlier test. The change is the patch `docs/phase2/evidence/red/batch-e/spec-change-head-0002.patch` (25 changed lines in 2 files): `HEAD = "0002"`; the chain test expects `["0002", "0001"]`; the revision whose `DOWNGRADE_DROPS` is checked and the template's `head=` become `"0001"` and `HEAD`; the packaged-files list expects both revision files; the faulted downgrade expects `"0001"`. It is the same intent (a linear chain, one head, a template that follows the head, one transaction per revision), with no assertion removed or weakened. With the patch applied, the oracle passes all 1471 batch A to D SQLite cases and the Postgres side, plus the batch E cases. The patch is applied by dev in the commit that adds revision 0002.

## 5. Indic storage (P2-21)

The cases: `fixtures/indic_storage.json`, `{"cases": [{id, language, script, text, purpose, needs_native_review}]}`, 79 cases, ASCII file (escapes only, so no tool can have normalized it), every case `needs_native_review: true`, the nine languages plus `und`, ZWJ and ZWNJ, 2 cases with U+0000, 2 of 2,000 code points, and 10 groups of texts that are canonically equivalent (same NFC) but differ in code points. They are storage fixtures; nothing here is a WER reference and no test claims a text is correct Indic.

Two placements, because the models restrict where a text may sit: **a project name** (`project.name`, a projected KeyCol, a label: ADR 0017 refuses controls, bidi controls, line and paragraph separators, a BOM and edge white space; ZWJ and ZWNJ are allowed) holds 70 of the 79 (`test_indic_label_rule_refuses_exactly_the_pinned_cases` pins the other 9 by id, and that each has such a mark or edge white space); **a turn text** (inside `doc`, a `DocCol`) holds all 79.

* `test_indic_text_round_trip_byte_exact`: the 70 names come back equal from the repository, with the same UTF-8 bytes and the same length in code points as the database itself reports (`hex(CAST(name AS BLOB))` and `length` on SQLite, `encode(convert_to(name, 'UTF8'), 'hex')` and `char_length` on Postgres), equal in the raw `doc`, with the `doc_sha256` the codec computes. The 79 turn texts come back equal from a stored and loaded run bundle and from the raw documents (U+0000 is the six characters `\u0000` inside the JSON text, so it survives on Postgres).
* `test_indic_text_no_normalization_by_driver`: a bound text echoed by the server (`SELECT CAST(:x AS TEXT)`) is unchanged for the 77 without U+0000; `count(DISTINCT name)` equals the number of distinct texts; for each of the 10 canonical-equivalence groups a lookup by one spelling returns exactly that spelling; the three case twins, Kelvin sign and K, Angstrom sign and A-ring, sharp s and SS, the ZWJ, ZWNJ and plain conjuncts, the Malayalam chillu spellings and native against ASCII digits are pairwise distinct values.
* `test_indic_text_ordering_is_never_language_sensitive`: `ORDER BY name` is the order of the UTF-8 bytes on both backends (so KeyCol's `COLLATE "C"` is exercised on a Postgres whose database collation is not C), and a range predicate follows it.
* `test_language_tags_stored_canonical_case`: 108 spellings (nine languages, with and without script and region, four letter cases each) of configured tags go through `Persona` and `DatasetItem`, and 113 observed tags (those plus `und`, `xq-IN`, `hi-Latn-IN`, `zxx` and the tag of a language the framework does not configure, which an observation may carry) through `Transcript.detected_language` by the codec: the model returns language lower case, script title case, region upper case (ADR 0005 Decision 1), and the projected column, the document and the model agree; a lookup by a differently cased spelling finds nothing (binary comparison). A persona's digest is computed over the canonical spelling, so a non-canonical input is built by redigesting the canonical document and then respelling the field.
* `test_language_tag_of_a_stored_bundle_transcript_is_canonical`: the same through `store_run_bundle` for five spellings of a Hindi tag.
* `test_indic_storage_fixture_is_well_formed`: the file shape and its coverage (the counts above).

## 6. `verify_database` (P2-26)

### 6.1 Signature and types

```python
def verify_database(engine: Engine) -> VerifyReport

@dataclass(frozen=True)
class VerifyFinding:
    kind: str            # the table: one of the 26 entity tables
    id: str              # the wire ID of the row (prj_..., run_..., rbnd_...), never a value
    version: int | None  # the version of a versioned entity, else None
    problem: str         # one of VERIFY_PROBLEMS (findings) or VERIFY_NOTES (informational)
    location: str | None # a column name, a side-table name, "doc", or a member kind: schema identifiers only

@dataclass(frozen=True)
class VerifyReport:
    findings: tuple[VerifyFinding, ...]       # errors; sorted by (kind, id, version or -1, problem, location or "")
    informational: tuple[VerifyFinding, ...]  # notes; same sort
    checked: dict[str, int]                   # rows examined per entity table, all 26 keys, zeros included
    @property
    def ok(self) -> bool                      # True when findings is empty (notes do not count)

VERIFY_PROBLEMS = ("doc_invalid", "digest_mismatch", "doc_sha256_mismatch", "projection_mismatch", "side_rows_mismatch")
VERIFY_NOTES    = ("doc_text_not_canonical", "bundle_member_absent")
```

`engine` is the engine of `create_store_engine` (`TypeError` for a `Connection`, a URL, a string or `None`). Read-only: no INSERT, UPDATE, DELETE or DDL statement, every row unchanged afterwards. It needs no migration (it works on a `create_all` schema and an Alembic schema alike). It never raises for a damaged row: damage is a finding. Deterministic: two calls on an unchanged store are equal. On Postgres it should read one consistent snapshot (REPEATABLE READ, as `load_run_bundle`): (unpinned).

### 6.2 What it checks, per row, in this order

1. **The document loads.** The strict loader and the model (`tracer_evalschema.loader`). A document the loader or model refuses is `doc_invalid`, location `doc`; a versioned entity or a snapshot whose own content digest does not verify is `digest_mismatch`, location `doc`. When the document does not load, nothing else is computed for that row (the projections come from the model).
2. **`doc_sha256`.** The JCS hash of the parsed document against the column: `doc_sha256_mismatch`, location `doc_sha256`.
3. **Every projected column**, recomputed from the model (including `id`, `schema_version`, `created_at`, `version`, the digest columns and the storage-only `run_id`): one `projection_mismatch` per differing column, location the column name, never its value. (Consequence, pinned: an edited `ExportBundle` document also changes its `byte_length`, the size of its canonical text.)
4. **The side rows** the row's document fills (`snapshot_member`, `snapshot_profile_ref`, `dataset_member`, `external_ref_index`, `export_bundle_run`, `export_bundle_item`, `export_bundle_profile`), compared as multisets with the rows the codec would write: one `side_rows_mismatch` per side table, kind and id of the OWNER (the entity whose document it projects: a span's reference rows belong to the `stage_span`), location the side table.
5. **The note.** If the stored `doc` text differs from the canonical `model_dump_json()` text while the JSON value is unchanged (white space, indentation), the row gets `doc_text_not_canonical`, location `doc`, in `informational` and nothing in `findings` (decision log, batch B: decode does not require byte equality). The repository still loads such a row.

`run_bundle` rows (their `doc` is the envelope only): the envelope is parsed and its `doc_sha256`, `id`, `schema_version` and `created_at` are checked by themselves (so they are reported even when a member is damaged); when every member listed in `run_bundle_member` is stored and verifies, the whole bundle is assembled and validated as a `RunBundle` (every bundle rule), and `run_id` and any remaining mismatch are reported with the same codes; a member that does not verify is reported under its own table, not again here. A member whose record is **absent** (a deleted test profile or catalog: `delete_test_profile` and `delete_definition` leave bundles that list them, by design) is a note `bundle_member_absent`, location the member kind, not an error; the bundle is then checked at the envelope level only. The text note for a bundle is given only when it assembles.

### 6.3 Tests

`test_verify_database_api_and_types`, `test_verify_database_of_an_empty_store_reports_nothing_and_counts_zero`, **`test_verify_database_clean_store_reports_nothing`** (every entity table filled through the repository: no finding, no note, `checked` equal to the real row counts, no write statement, `raw_dump` unchanged, repeatable), `test_verify_database_clean_after_the_repository_deleted_things`, `test_verify_database_works_on_the_schema_alembic_builds`, **`test_verify_database_finds_projection_mismatch_without_echo`** (a changed `created_at` on the first row of each of the 26 tables, plus text projections holding a marker: exactly those findings, in sorted order, and the marker nowhere in `repr`, `str` or any field of the report), `test_verify_database_reports_every_projected_column_by_name_only` (each projected column of the first row of each table, changed alone and put back: one `projection_mismatch` naming it; the 19 columns a foreign key, the unique `(transcript_id, seq)` or a CHECK refuses to change are pinned by name; at least 80 columns covered), **`test_verify_database_finds_tampered_doc`** (26 tables: `digest_mismatch` for the 10 digested ones, `doc_sha256_mismatch` for the rest, and the export's `byte_length`), `test_verify_database_finds_a_changed_doc_sha256`, `test_verify_database_finds_a_versioned_doc_edited_with_a_consistent_digest`, `test_verify_database_finds_a_doc_the_strict_loader_refuses` (7 cases), `test_verify_database_finds_a_run_bundle_envelope_that_is_not_one`, `test_verify_database_formatting_only_difference_is_informational` (all 26 rows re-indented: no finding, 26 notes, the bundle still loads), `test_verify_database_finds_side_rows_that_do_not_match_their_document` (six side tables, a missing row each, plus an extra row), `test_verify_database_never_logs_or_raises_content`.

A table is damaged as raw SQL would: the table's immutability triggers are dropped with `tracer_evaldb.triggers.drop_statements`, one UPDATE is made, and `create_statements` puts them back.

## 7. The measurement harness (P2-23)

Location: `packages/evaldb/tests/evaldb_measure.py` (not collected: its name does not start with `test_` and it defines no test). Run from the repository root:

```
uv run --locked python packages/evaldb/tests/evaldb_measure.py --backend sqlite --out <file.json>
TRACER_MEASURE_POSTGRES_URL=postgresql+pg8000://... uv run --locked --all-groups python \
    packages/evaldb/tests/evaldb_measure.py --backend postgres --out <file.json>
```

Options: `--sizes N...` (default `4 400 4000` turns), `--repeats N` (default 5). The Postgres URL names an EMPTY throwaway database; the harness refuses (exit code 2, the URL never printed) if it holds any table, migrates it with Alembic, measures, removes every table and `alembic_version` again. SQLite uses a temporary file. A bundle is `sized_bundle_doc(turns, tool_calls=2, usages=2)` of batch C (the very bundle of the statement-count test), built and validated outside the timed region; per repeat the harness times `store_run_bundle` and `load_run_bundle` with `time.perf_counter`, counts statements with `before_cursor_execute`, checks the loaded bundle equals the stored one, and deletes the run.

Output (one JSON document, `sort_keys`, no URL, host, user, password or path):

```
{"format": "tracer-evaldb-measurement/1",
 "environment": {backend, server, python, python_implementation, system, system_release, machine, cpu_count, packages{tracer-evaldb, tracer-evalschema, sqlalchemy, alembic, pydantic, pg8000}},
 "parameters": {"sizes": [...], "repeats": n},
 "results": [{"turns", "document_bytes", "rows_stored": {table: n}, "store": {"seconds": [...], "min", "median", "max"}, "load": {...}, "statements": {"store_total", "store_inserts", "max_inserts_per_table", "load_total"}}],
 "note": "Evidence only. No threshold ..."}
```

Evidence of this run: `docs/phase2/evidence/measurements/bundle/{sqlite,postgres-15,postgres-18}-py3.14.json` (one machine, Python 3.14.7, SQLite 3.53.1, Postgres 15.19 and 18.6 in local Docker, pg8000 over loopback). Medians for 4,000 turns (1.98 MB of document): store 0.28 s on SQLite, 5.7 s on Postgres 15 and 5.6 s on Postgres 18; load 0.27 s, 0.34 s and 0.33 s. The statement counts are equal at 4, 400 and 4,000 turns (SQLite 45 store and 18 load statements, Postgres 44 and 17; 23 INSERT statements, at most one per table). The Postgres store time is not a statement-count problem: it is the cost of pg8000's `executemany` of 4,000 turn rows, the risk SCOPE section 7 names; the remedy it names (a second permissive dialect behind the same parity suite) is a decision for later and this file asserts no number.

Tests (`test_evaldb_measure.py`): the format and keys, `min <= median <= max` (a summary, not a threshold), the statement counts equal between two sizes with at most one INSERT per table, the real bundle stored (`rows_stored["turn"] == turns`), the database left empty, a refusal of a non-empty database that leaves it untouched, refusal of nonsense parameters, the command line on SQLite, the missing-URL error that never prints a URL, no `://` and no temporary path in any output, and the Postgres run (marked). No test compares a duration with a number.

## 8. Test names

`test_evaldb_indexes.py`: `test_access_pattern_indexes_exist`, `test_every_foreign_key_is_led_by_a_key_or_an_index`, `test_no_index_duplicates_a_key_or_another_index`, `test_the_pattern_table_covers_every_index_and_every_versioned_table`, `test_sqlite_query_plans_use_indexes[<pattern>]` and `test_postgres_plans_can_use_indexes[<pattern>]` (62 patterns each), `test_repository_and_deletion_statements_are_served_by_indexes`, `test_keyset_pagination_runs_by_project_is_time_ordered`. SCOPE row P2-20 names four tests: the three of it and the keyset test keep the SCOPE names exactly.

`test_evaldb_revision_0002.py`: `test_revision_0002_follows_0001_and_is_the_head`, `test_revision_0001_is_unchanged_by_the_new_indexes`, `test_revision_0002_is_frozen_text_and_never_reads_the_live_metadata`, `test_revision_0002_adds_exactly_the_access_pattern_indexes`, `test_revision_0002_keeps_every_row_and_the_store_readable`, `test_revision_0002_downgrade_restores_revision_0001_exactly`, `test_revision_0002_stairway_up_down_up[empty|filled]`, `test_downgrade_through_0002_to_base_still_asks_for_the_flag_on_a_filled_store`, `test_revision_0002_is_one_transaction`, `test_revision_0002_upgrade_over_a_current_database_sends_no_write`, and the four `open_store` tests of 4.3.

`test_evaldb_indic_storage.py`: `test_indic_storage_fixture_is_well_formed`, `test_indic_label_rule_refuses_exactly_the_pinned_cases`, `test_indic_text_round_trip_byte_exact`, `test_indic_text_no_normalization_by_driver`, `test_indic_text_ordering_is_never_language_sensitive`, `test_language_tags_stored_canonical_case`, `test_language_tag_of_a_stored_bundle_transcript_is_canonical[...]`.

`test_evaldb_verify.py`: section 6.3. `test_evaldb_measure.py`: section 7.

None of them pins pydantic-version-dependent or Postgres-version-dependent text: a plan is read as facts, a document's canonical text is compared with `model_dump_json()` of the same process, an error is matched by type.

## 9. Findings and ambiguities (auto-approval requested on the recommendations)

* **A1. "Definitions by `(id, version DESC)`" (SCOPE P2-20, ADR 0019 Decision 15).** A descending index on `(id, version)` duplicates the primary key, which both backends read backward. Recommendation: no new index; the nine versioned tables are asserted to be served by the key; amend the wording of SCOPE and Decision 15 to "served by the primary key". Rejected: 9 redundant indexes (write cost, no read gain).
* **A2. `mode` and `observed_tier` as single-column indexes.** The ADR names them separately; each has 2 to 4 values, so the planner will rarely choose them alone. Recommendation: build them as written (the access pattern is "runs by mode", and Phase 3 queries are unknown), and let the P2-23 style measurements of Phase 3 decide whether `(project_id, mode)` is wanted. Rejected: guessing composite shapes now.
* **A3. Indexes beyond the ADR list.** `ix_run_verdict_run_id` and the 11 other `run_id` indexes (12 foreign keys of the cascade; the ADR names 11 of the tables), and `ix_dataset_item_artifact_id` and `ix_dataset_item_transcript_id`, which `delete_run`'s `WHERE artifact_id IN (...) OR transcript_id IN (...)` needs (a SQLite scan of `dataset_item` per run deleted today). Not added: `dataset_item.source_run_id` (provenance, nothing reads it). Recommendation: adopt and amend Decision 15 and SCOPE P2-20.
* **A4. NAMED SPEC CHANGE of two batch D files (section 4.5).** Recommendation: approve the patch.
* **A5. The `key` quoting trap (2.3).** Pinned by an existing test; written into the contract so dev does not meet it as a surprise.
* **A6. `verify_database` field shapes.** Wire ids (not bare UUIDs), `kind` is the table, `location` is a schema identifier, `checked` lists all 26 tables, notes are separate from findings. Recommendation: as written. A bare UUID would be easier to implement (the kind is known) but operators search logs by wire id.
* **A7. A document that does not load gets one finding, nothing more.** Recommendation as written (projections come from the model).
* **A8. A bundle with an absent member is a note.** Because the package's own deletions create it. Recommendation as written. Not pinned: the findings for a damaged `run_bundle_member` row (a deleted turn row changes what the bundle assembles to; the result is a finding, its code is unspecified).
* **A9. Postgres isolation of `verify_database`** (unpinned): REPEATABLE READ recommended.
* **A10. FINDING for the lead (fixture, not a defect).** `fixtures/indic_storage.json` was revised after qa first read it (`en-long-2000` and `hi-long-2000` became valid labels; the file grew). The tests derive what they can from the file and pin by id only the 9 cases the label rule refuses; a later edit of the fixture that changes which cases a label refuses fails `test_indic_label_rule_refuses_exactly_the_pinned_cases` on purpose. Nothing in the file looks wrong. Observation: the two NUL cases and seven other cases can only be stored inside a document, never as a project name, which is the model's rule (ADR 0017).
* **A11. FINDING (Phase 5 risk): pg8000 store of a large bundle** (section 7): 5.6 to 5.7 s for 4,000 turns on Postgres against 0.28 s on SQLite, with equal statement counts. Not a failure of any asserted property; it is the measurement SCOPE section 7 asked for.
* **A12. Unpinned:** the error type of `upgrade` over a `create_all` schema (batch D A11, still open); concurrent writers (batch D A15 and A16, Phase 5 risks).

## 10. Evidence

`docs/phase2/evidence/red/batch-e/`: `red.py3.11.log` to `red.py3.14.log` and `red-postgres15.py3.11.log`, `red-postgres18.py3.14.log` (the batch E tests against the committed tree: no revision 0002, no new index, no `verify_database`), `oracle-sqlite.py3.11.log` to `py3.14.log` and `oracle-postgres{15,18}.py3.{11,14}.log` (the scratch oracle: `oracle-src.patch` is its source, written by qa to prove the tests are satisfiable; it is not a proposal for the implementation's structure), `mutants.txt`, `spec-change-head-0002.patch`. Paths are scrubbed; the Postgres passwords were generated per container, never written to a file in the repository, and the containers were removed.
