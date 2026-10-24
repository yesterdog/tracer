# Phase 2 batch F contract: the security guard tests and the storage documents (P2-24 tests, P2-25)

Author: qa, 2026-10-25. Source: `docs/phase2/SCOPE.md` (rows P2-24 and P2-25, Decisions D1 to D6), ADRs 0019 to 0022 (ADR 0019 Table 3 and Decision 18, ADR 0006 Decision 6, ADR 0020, ADR 0021), the batch A to E contracts, the implemented `packages/evaldb/src/tracer_evaldb`, `docs/security/P2-24-audit.md` (findings E1 to E24), `packages/evalschema/SCHEMA_POLICY.md` section 3, and the decision log (entries "PHASE 2"). qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, HANDOFF, `packages/evalschema` or earlier test. The documents named in section 3 do not exist yet (the architect writes them, ADR index and SCOPE are the lead's). Every statement below is pinned by a test named in section 10, or is marked (unpinned).

## 0. Where the tests are and how to run them

`packages/evaldb/tests/` (the directory of batches A to E). Three new test files and two new helper modules; nothing else changes:

| File | Holds |
|---|---|
| `evaldb_guard_support.py` | helper, no test: the reader of the SQL text a source file hands to the database (`scan_source`, `Site`) |
| `evaldb_docs_support.py` | helper, no test: the reader of Markdown (sections, tables, blocks, sentences, citations), the list of test names |
| `test_evaldb_guards.py` | P2-24: `test_no_string_built_sql`, `test_store_has_no_secret_columns`, `test_store_errors_hide_input` and their companions |
| `test_evaldb_storage_policy.py` | P2-25: the checks of `packages/evaldb/STORAGE_POLICY.md` |
| `test_evaldb_docs.py` | P2-25: the checks of `CHANGELOG.md`, `README.md`, the concept page and the SCHEMA_POLICY pointer |

Commands are those of batches A to E: `uv run --locked --all-groups pytest` runs the SQLite side; `TRACER_TEST_POSTGRES_URL=... TRACER_TEST_POSTGRES_SQL_ASCII_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests` the Postgres side. No new environment variable, no change to `conftest.py`, the root `pyproject.toml` or CI. Tests that need a database take the `db` fixture of batch B (all tables from `metadata.create_all`) or `store` of batch A; the document tests need none. `test_no_string_built_sql` and the quick start test start a subprocess (`python -m ruff`, and the interpreter itself); `ruff` is in the dev group of the lock, which every CI leg installs.

Counts: 35 test functions in three files, 35 default cases (the SQLite side and the tests without a database) and 5 `postgres`-marked cases, 40 in all (`test_store_has_no_secret_columns`, `test_sent_statements_hold_no_stored_value`, `test_store_errors_hide_input`, `test_storage_policy_parity_table_matches_column_types` and `test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it` on Postgres).

## 1. What is red and what is green today

* The seven tests of P2-24 are **regression guards of code that exists**: on the current tree they pass on SQLite (guards of the audit findings E1, E3 and the SQL side of E9). The one exception is `test_store_errors_hide_input[postgres]`, which fails today for the right reason: audit finding **E5** (the untranslated `IntegrityError` of Postgres is raised from the driver error that still holds "Failing row contains (...)", so `traceback.format_exception` shows the document text and the name on a raw `check` and a raw `not_null` violation). Batch G (`docs/phase2/api-surface-batch-g.md`) owns the fix of E5 and E6; batch F pins the observable result and nothing about how it is reached. After the fix the same test passes on both backends (proof in section 12: the oracle carries a short patch of `engine.py`).
* The P2-25 tests are **red until the architect writes the documents**: 19 of the 35 default cases fail today, every one because a document or a section of it does not exist (`... does not exist yet`, `KeyError: 'Quick start'`). The other 16 are the "has teeth" tests, the guards and the reader tests; they pass.

## 2. P2-24, the guards (`test_evaldb_guards.py`)

### 2.1 `test_no_string_built_sql`

SCOPE: "ruff S608 plus an AST check that no `text()` takes an f-string or concatenation". Four parts, in one test:

1. `python -m ruff check --isolated --no-cache --select S608 packages/evaldb` exits 0, and the only `noqa: S608` comments in `src` are the three that exist (`migrate.py` once, `migrations/helpers.py` twice).
2. **The reader.** `evaldb_guard_support.scan_source` finds every call in `src` that hands text to the database: the call names `text`, `exec_driver_sql`, `execute`, `executemany`, `executescript`, `DDL` (first argument) and `create_check_constraint` (the condition). It classifies the text: `literal` (a string constant, implicit concatenation included, or a name bound only to literals, or a loop over a literal structure of the module), `dynamic` (an f-string, a `+` or `%` with text, `.format`, a name bound to one), `opaque` (a parameter, a call result, a loop over a call) or a Core construct (a call such as `sa.select(...)`). It follows a name to the assignments of the enclosing functions and the module, not through the control flow (a name bound in three loops of one function is as suspicious as its worst binding). It is a reader of source text, and the test pins what it cannot call literal.
3. **The reviewed table `REVIEWED`.** The sites that are not literal are exactly these 18, each with the interpolated pieces and a reason. A new site fails until a reviewer adds it with a reason (the audit table P2-24 is this table); a site that disappears fails until its row goes.

| File | Function, call | Kind | Pieces | Reason |
|---|---|---|---|---|
| `engine.py` | `_sqlite_engine._configure`, `execute` (2nd) | dynamic | `busy_timeout_ms` | `PRAGMA busy_timeout` takes no bound parameter; the value is an `int >= 0` checked first |
| `engine.py` | `_sqlite_engine._begin`, `exec_driver_sql` | opaque | | the BEGIN text comes from an execution option that only `deletion.py` sets (`BEGIN IMMEDIATE`) |
| `migrate.py` | `_lost_tables`, `exec_driver_sql` | dynamic | `table`, `where` | identifiers from `DOWNGRADE_DROPS` of a revision |
| `migrations/helpers.py` | `_drop_triggers`, `_create_triggers`, `execute` | opaque (2) | | statements of `triggers.*_statements` |
| `migrations/helpers.py` | `_plan`, `text` | dynamic | `columns`, `table` | key column and entity table names of the metadata |
| `migrations/helpers.py` | `rewrite_documents`, `text` | dynamic | `table`, `where` | identifiers and named bind parameters; every value is bound |
| `migrations/helpers.py` | `rewrite_documents`, `create_check_constraint` | dynamic | `new_version` | DDL cannot bind; the version string of a data migration, never stored data |
| `migrations/versions/0001_initial_schema.py` | `upgrade`, `execute` (x3) | opaque | | trigger functions, the literal DDL dict of the dialect, the trigger statements |
| `migrations/versions/0001_initial_schema.py` | `downgrade`, `execute` (x3) | dynamic | `table`; the two trigger function names | names from the `TABLES` tuple and module constants of literal text |
| `migrations/versions/0002_access_pattern_indexes.py` | `upgrade`, `downgrade`, `execute` | dynamic | `name`, `table`, `columns`; `name` | the literal tuple `_INDEXES` |
| `triggers.py` | `install`, `DDL` (x2) | opaque | | literal trigger text with fixed names |

4. **Two more rules.** (a) The modules that only use Core (`repository`, `codecs`, `deletion`, `verify`, `versioning`, `columns`, `tables`, `schema`, `errors`, `__init__`) have no such site at all. (b) In a revision file every piece of a dynamic site is drawn from a literal of that file (a name bound only to literal structures, or a loop over one) or is one of the two module constants `triggers.UPDATE_TRIGGER_FUNCTION` and `triggers.DELETE_TRIGGER_FUNCTION`, which the test reads in `triggers.py` as literal text. This is "the migration revisions use only literal DDL".

Companions with teeth: `test_the_sql_reader_tells_literal_text_from_built_text` (15 built forms found, 10 plain forms left alone, 5 opaque forms, the pieces and the ordinals), `test_ruff_s608_flags_a_string_built_query` (the same ruff invocation exits 1 on a built query), and the runtime half `test_sent_statements_hold_no_stored_value` (a project name and a metadata text reach the database as parameters and are in no statement text; writes, reads, a lookup by external reference, `verify_database` and `delete_run` run with the markers present; the markers are found in the parameters, so the scenario did carry them).

### 2.2 `test_store_has_no_secret_columns`

The words of every table and column name (split at `_`, lower case) of `tracer_evaldb.metadata`, and of the schema the database reports (`PRAGMA table_info` on SQLite, `information_schema.columns` on Postgres, equal to the metadata), are checked against the denylist `SECRET_WORDS` (password, passwd, passphrase, secret, token, credential(s), apikey, bearer, cookie, auth, authorization, private, privkey, kek, dek, hmac, salt, nonce, cipher, ciphertext, plaintext, keyring, keystore, pem) and the pairs `api_key`, `access_key`, `secret_key`, `signing_key`, `encryption_key`, `master_key`. The word `key` is allowed for exactly two columns, `metric_definition.key` (type `KeyCol`, a metric's identifier) and `external_ref_index.key_sha256` (`Sha256Col`, the hash of a reference); both are checked to be of those types. Today the store has 37 tables and 99 distinct column names and none is refused. `test_the_secret_name_denylist_has_teeth` runs the rule on 18 refused and 10 accepted names, and checks that `key` is refused on any other table. (Not pinned: a document never holds key material; that is the Phase 1 credential rules.)

### 2.3 `test_store_errors_hide_input`

"Errors never contain document text or parameter values." Markers: a document marker, a name marker, an edit marker, the number 2**53 + 977 and the number 2**70 + 977. The probes, on SQLite and (with `-m postgres`) Postgres:

* the five kinds of constraint violation (`check`, `unique`, `not_null`, `foreign_key`, `restrict`), raw (the `IntegrityError` the engine raises) and translated by `repository.translate_integrity_error`, with the markers in the row (the document and a text column);
* a value the column type refuses (beyond 64 bits);
* `StoreValueOutOfRange`: a turn whose `end_ms` is 2**53 and 2**53 + 977, with the marker in the metadata of the run and the turns;
* tampered rows, for seven tables (`project`, `agent_target`, `config_snapshot`, `dataset_item`, `export_bundle`, `turn` through `load_run_bundle`, `run_bundle`): a document edited (valid JSON, other content), broken (not JSON, old text kept) and given a field the strict loader refuses, and for `project` a projected column that disagrees with its document; each read back through the repository and through `verify_database`, and put back.

The text surface of every error is searched for every marker: its message, `repr`, `args`, notes, the attributes of a store error, the original driver error of a database error, the whole `traceback.format_exception` text, and the exceptions Python itself shows with it (`__cause__`, and `__context__` unless `raise ... from None` suppressed it). The attribute `params` of SQLAlchemy's own errors is not read (it holds the bound values by design; `hide_parameters` hides it from the text; the repository raises its own errors instead, and the batch G tests read `params`). A report of `verify_database` is searched through `repr`, `str` and every field. Every log record is searched too, with the `sqlalchemy.engine` logger at INFO (the statements are logged, "SQL parameters hidden" in place of the values); the test asserts that the engine logger produced records, so the log half cannot pass on silence. Further assertions: no store error has a SQLAlchemy error in the chain Python shows; a tampered document fails as `StoreIntegrityError`, or as the Phase 1 `DigestMismatch` for the nine versioned entities and the snapshot (their own content digest fails first), both by type. `test_the_error_surface_reader_has_teeth` plants a marker in a message, an attribute (deep, bytes, a number), a cause, a note and a suppressed context.

No plan, message or Postgres wording is compared; every match is on a marker of the test.

## 3. The documents and where they live (all must exist)

| Path (from the repository root) | Pinned by |
|---|---|
| `packages/evaldb/STORAGE_POLICY.md` | `test_evaldb_storage_policy.py` |
| `packages/evaldb/CHANGELOG.md` | `test_evaldb_changelog_has_entry_for_version` |
| `packages/evaldb/README.md` (exists; sections added) | `test_evaldb_readme_has_the_documented_sections`, `test_evaldb_readme_quick_start_runs` |
| `docs/concepts/storage.md`, and a row in `docs/concepts/README.md` | `test_concept_page_storage_exists_and_is_linked` |
| `packages/evalschema/SCHEMA_POLICY.md` (one sentence added) | `test_schema_policy_database_bullet_points_to_the_storage_policy` |

Every document of the batch is ASCII, has no tab and no trailing white space, names no home directory and no key-shaped string (`test_the_documents_of_this_batch_are_ascii_and_free_of_machine_paths`). SCHEMA_POLICY.md already holds three non-ASCII characters from Phase 1; only its new sentence is held to ASCII.

## 4. `STORAGE_POLICY.md`: structure

Line 1 is `# Storage policy: tracer-evaldb <version>` (the version of `packages/evaldb/pyproject.toml`). The ten sections are H2 headings with these numbers and titles, in this order, and no other H2:

1. `## 1. What this document is`
2. `## 2. The parity table`
3. `## 3. The database is not a validation boundary`
4. `## 4. Limits`
5. `## 5. Encryption at rest`
6. `## 6. Digests and the plain profile digest`
7. `## 7. Deletion and audit`
8. `## 8. Migrations and versions`
9. `## 9. Phase 5 risks`
10. `## 10. Statements that are not verified`

The file is ASCII, has no tab, no trailing white space, exactly one final newline, no fenced code block, no abbreviation with periods (`e.g.`, `i.e.`, `etc.`, `vs.`, `cf.`, `approx.`), and exactly one table, in section 2. Lower-level headings are not used. (`test_storage_policy_exists_with_the_pinned_structure`.)

## 5. `STORAGE_POLICY.md`: the statement convention (tested or marked)

SCOPE: "Every statement is tested or marked (unverified)." The convention, pinned by `test_every_statement_of_the_storage_policy_is_tested_or_marked_unverified`:

* **Blocks and sentences.** In sections 1 to 9 a *block* is a paragraph or a list item (`- ` or `N. ` at the left margin; wrapped lines belong to the item). Table rows and blank lines are not blocks. A block ends with a period (`.`, `.)`, `.**` or a closing backtick after it). A block is split into *sentences* after `.`, `!` or `?` followed by white space and a capital letter, a backtick, an opening bracket, a quote or an asterisk. Because of that rule the file does not use abbreviations with periods.
* **A sentence is one of three things**, and is exactly one:
  1. **Tested:** it contains a *citation*, a backticked token that is a test function name (`test_` and word characters, no brackets). Every cited name must be the name of a `test_*` function in `packages/evaldb/tests/*.py` or `packages/evalschema/tests/*.py` (found by parsing the files; a name of the batch G tests counts once they exist). Several citations are allowed.
  2. **Marked:** it contains the exact text `(unverified)` before its final period (bold around it is allowed). A sentence that is only the marker is refused.
  3. **A pointer:** it starts with `See `, and every backticked token in it that is not a test name is a path below the repository root that exists (an `#anchor` is ignored), and there is at least one. A pointer carries neither a citation nor the marker. It is for orientation (`See `docs/adr/0021-store-schema-version-and-migrations.md`.`), never for a claim.
* A sentence that is both cited and marked is refused (a statement is not at once tested and unverified).
* **The register (section 10).** It lists every marked sentence of sections 1 to 9, in order, once each, as bullets, each bullet being that sentence word for word (white space aside) and exactly one sentence. If no sentence is marked, section 10 is exactly the sentence `None.`.
* **Pinned citations (`test_the_storage_policy_cites_a_test_for_each_statement_the_owner_asked_for`).** Four sentences must cite a named test: the sentence of section 2 that mentions "parity" cites `test_storage_policy_parity_table_matches_column_types`; the sentence of section 3 that contains "validation boundary" cites `test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`; the sentence of section 5 that contains "not encrypt" and the one that contains "personal data" cite `test_the_sqlite_file_holds_documents_in_plain_text`.

The rules themselves are tested on synthetic policies (`test_the_statement_rule_accepts_a_correct_policy_and_names_each_fault`, `test_the_structure_rule_names_each_fault`), so a change of the reader cannot weaken them unseen.

## 6. `STORAGE_POLICY.md`: required content, section by section

Each requirement below is a test; none replaces the sentence rule of section 5.

**Section 2, the parity table** (`test_storage_policy_parity_table_matches_column_types`). The only table of the file. Header exactly `| Logical type | Postgres | SQLite (STRICT) |`. Eight data rows in this order: `UuidCol`, `InstantCol`, `DecimalCol`, `DocCol`, `Sha256Col`, `KeyCol`, `BigIntCol`, `BoolCol` (the eight column types that `tracer_evaldb.__all__` exports). Each cell is exactly one backticked token. The type is compared case-insensitively, quotes ignored, after unifying the two Postgres spellings `timestamptz` = `timestamp with time zone` and `varchar` = `character varying`; a collation is the part after `COLLATE`. The cells that the code produces today:

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

Each cell is compared (a) with the DDL type SQLAlchemy writes for the column type on the Postgres and the SQLite dialect, (b) with the DDL type of every real column of the 37 tables (and every class a column uses has a row, and every row has a column), and (c) with what the database reports on the backend under test for a scratch table of that type: `format_type` and the column collation on Postgres, the declared type and `COLLATE` in `sqlite_master` on SQLite. A cell that differs from any of the three fails, so a change of a column type that is not copied into the table fails. See ambiguity A1: ADR 0019 Table 3 says `text COLLATE "C"` for `KeyCol` on Postgres; the column is `varchar COLLATE "C"`.

**Section 3** (`test_storage_policy_states_db_is_not_validation_boundary`): the text (white space and case aside) contains `the db is not a validation boundary, the repository is`, names the raw INSERT (`raw insert` or `raw sql insert`), the triggers and UPDATE, and has `verify_database` in backticks; the sentence with the phrase cites the behaviour test and is not marked. The behaviour is shown by `test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it` (two raw INSERTs, one with a document the models reject and one with a document that loads and a wrong `doc_sha256`: the database accepts both, `load_project` raises `StoreIntegrityError` for both, `verify_database` reports `doc_invalid` and `doc_sha256_mismatch` for kind `project`, counts 2 rows, and the report holds no value).

**Section 4, limits** (`test_storage_policy_limits_state_the_numbers_of_the_code`): the text states the stored integer range (`2**53` or `9007199254740991`), `3.37` (SQLite), `Postgres 15`, `UTF8`, `U+0000`, the years `0001` and `9999`, and the document size as `256 MiB` (computed from `tracer_evalschema.loader.DEFAULT_MAX_BUNDLE_BYTES`, which the test also pins to 256 MiB). Each is a sentence like any other.

**Section 5, encryption** (`test_storage_policy_states_the_database_is_not_encrypted_at_rest`): contains `not encrypt`, `at rest`, `personal data` and `internal data`; a sentence that mentions Postgres is marked (nothing in CI can see a server disk). `test_the_sqlite_file_holds_documents_in_plain_text` shows the claim on SQLite: a project name and document text, and the name, date of birth and account number of the golden maximal `TestProfile`, are readable bytes in the database file; the file starts with the standard header; `create_store_engine` has no parameter whose name contains key, pass, secret, cipher, crypt, token or pragma.

**Section 6, the plain profile digest** (`test_storage_policy_states_the_plain_profile_digest_limitation`): the regular expressions of the Phase 1 test `test_policy_documents_low_entropy_digest_limitation` (low-entropy, brute-force, internal, plain SHA-256 or digest, shared export, drop or omit or absent, keyed or HMAC) hold in this section, which also contains `ADR 0006` and `Decision 6` and `InternalSnapshotRefused` in backticks (the store keeps internal data, so a snapshot whose profile reference has an absent or keyed digest is refused).

**Section 7, deletion and audit** (`test_storage_policy_covers_deletion_and_audit`): in backticks the seven roots `delete_run`, `delete_project`, `delete_test_profile`, `delete_definition`, `delete_dataset_item`, `delete_export`, `delete_snapshot`; `deletion_audit`, `actor`, `reason`, the three reasons `operator_request`, `retention_policy`, `data_subject_request`, and `IncompleteRunBundle`; in the text `append-only`, "no content" or "content-free", `cascade`, "retention is recorded" and `not enforced`.

**Section 8, migration policy pointer** (`test_storage_policy_points_to_the_migration_policy`): a pointer sentence `See `docs/adr/0021-store-schema-version-and-migrations.md`.`; in backticks `open_store`, `upgrade`, `downgrade`, `allow_data_loss`, `StoreNotMigrated` and `tracer_meta`; the text says the store is never migrated by being opened (`never migrat`).

**Section 9, Phase 5 risks** (`test_storage_policy_names_the_phase5_risks`): `P2-F4` and `executemany` (the pg8000 store time of a 4,000-turn bundle, measured in P2-23: 5.6 to 5.7 s median on Postgres, 0.28 s on SQLite, equal statement counts), "no timing threshold" or "no threshold", `concurren` (concurrent writers: batch C A15 and batch D A16), and `in place` (a digest-changing upgrade of a versioned entity cannot be migrated in place, ADR 0021 and SCOPE Q15).

Whole file (`test_the_policy_text_names_no_machine_path_and_no_key`): no `/Users/`, `/home/`, `claude-502`, backslash, `sk-`, `AKIA` or `BEGIN PRIVATE`, and no token-shaped string of 40 or more characters outside backticks.

## 7. `CHANGELOG.md`

`packages/evaldb/CHANGELOG.md` (`test_evaldb_changelog_has_entry_for_version`): line 1 is `# Changelog: `tracer-evaldb``; ASCII; the H2 headings are all `## <MAJOR>.<MINOR>.<PATCH> (unreleased)` or `(<YYYY-MM-DD>)`, newest first; the first is the package version (`pyproject.toml` equals the installed distribution) and no other heading has it. Under that entry the H3 sections are exactly, in order, `### Package`, `### Storage`, `### Migrations`, `### Findings`; `P2-F1`, `P2-F2`, `P2-F3` and `P2-F4` occur in it (more findings may); while unreleased the entry contains `Status: not released.`; it names `STORAGE_POLICY.md`.

## 8. `README.md` and the concept page

**README** (`test_evaldb_readme_has_the_documented_sections`): H2 sections `## Install`, `## Quick start`, `## Storage policy`, `## Security and privacy`, `## Status` (the earlier sections whose titles start with `Repository`, `Migrations` and `Access-pattern indexes and verification` stay). The README names, in backticks, `packages/evaldb/STORAGE_POLICY.md`, `packages/evaldb/CHANGELOG.md` and `docs/concepts/storage.md` (all exist; backticked paths, not links, because the README is the package description on PyPI). Install has `pip install`, `tracer-evaldb[postgres]` and `3.37`. Security and privacy has `not encrypt`, `personal data`, `expected_purpose` and `never echo`. Storage policy names the policy path. Status has the package version and `unreleased`.

**The quick start runs** (`test_evaldb_readme_quick_start_runs`). The first fenced block that starts with ```python in `## Quick start` is executed as written in an empty directory with `python -I -c` (a clean interpreter). It may import only `tracer_evaldb`, `tracer_evalschema`, `sqlalchemy`, `json`, `pathlib`, `tempfile`, `uuid`, `datetime` and `os`; it uses a `sqlite:///` URL; it contains no `http`, `password`, `api key`, `getenv` or `environ`. After it ends with exit code 0 the directory holds exactly one `.db`, `.sqlite` or `.sqlite3` file, `open_store` accepts it at `head_revision()`, it has at least one row in `project`, `run`, `persona`, `agent_target` or `dataset_item`, and `verify_database` finds it clean.

**Concept page** (`test_concept_page_storage_exists_and_is_linked`): `docs/concepts/storage.md`, ASCII, line 1 `# Storage`, the H2 sections exactly `What is stored`, `Documents and projections`, `Two backends, one behaviour`, `What the database does not do`, `Deletion and retention`, `Versions and migrations`; relative links to `../adr/0019-relational-storage.md`, `../adr/0020-deletion-and-retention.md`, `../adr/0021-store-schema-version-and-migrations.md`, `../adr/0022-write-unit-rescoring-reimport.md` (every relative link resolves); `STORAGE_POLICY.md` is named; at least three backticked test names, all existing; the texts "not a validation boundary" and "not encrypt"; and `docs/concepts/README.md` has the row `| [storage.md](storage.md) | ... |` (its earlier rows stay).

## 9. The SCHEMA_POLICY pointer

`packages/evalschema/SCHEMA_POLICY.md` section 3, the bullet `- **Database.**`, gains **one sentence, its last**, that contains the backticked path `packages/evaldb/STORAGE_POLICY.md`; the path occurs nowhere else in the file (`test_schema_policy_database_bullet_points_to_the_storage_policy`). The sentence is ASCII and ends with a period. The rest of the file is the Phase 1 text: the test removes that one sentence, collapses white space, and compares the SHA-256 with `SCHEMA_POLICY_BASELINE = c29df495a49fb34a7966d676fb993e5276cbdf928fd1660b1d07ff6184f95e51`, the digest of the file as committed at `b50c3cc`. Re-wrapping the lines does not matter; a changed word does. The tests that read the policy in Phase 1 (`test_policy_register_table_lists_exactly_the_code_only_rules`, `test_policy_documents_low_entropy_digest_limitation`, and the policy tests of cost, snapshot, export purpose and changelog) read other sections and keep passing: the oracle ran all 10,382 earlier cases with the pointer in place. A pointer that would need a Phase 1 test to change is not allowed; none does. An example sentence that satisfies the test: `The store that creates these tables is `tracer-evaldb`, and its rules are in `packages/evaldb/STORAGE_POLICY.md`.` (ambiguity A5 about the old sentence in the same bullet.) `test_the_schema_policy_baseline_is_the_text_without_a_pointer` shows that the comparison has teeth.

## 10. Test names

`test_evaldb_guards.py`: `test_no_string_built_sql`, `test_the_sql_reader_tells_literal_text_from_built_text`, `test_ruff_s608_flags_a_string_built_query`, `test_sent_statements_hold_no_stored_value`, `test_store_has_no_secret_columns`, `test_the_secret_name_denylist_has_teeth`, `test_store_errors_hide_input`, `test_the_error_surface_reader_has_teeth`.

`test_evaldb_storage_policy.py`: `test_the_statement_rule_accepts_a_correct_policy_and_names_each_fault`, `test_the_structure_rule_names_each_fault`, `test_storage_policy_exists_with_the_pinned_structure`, `test_every_statement_of_the_storage_policy_is_tested_or_marked_unverified`, `test_the_storage_policy_cites_a_test_for_each_statement_the_owner_asked_for`, `test_storage_policy_parity_table_matches_column_types`, `test_the_parity_comparison_has_teeth`, `test_storage_policy_states_db_is_not_validation_boundary`, `test_the_database_accepts_what_the_models_reject_and_verify_database_finds_it`, `test_storage_policy_limits_state_the_numbers_of_the_code`, `test_storage_policy_states_the_database_is_not_encrypted_at_rest`, `test_the_sqlite_file_holds_documents_in_plain_text`, `test_storage_policy_states_the_plain_profile_digest_limitation`, `test_storage_policy_covers_deletion_and_audit`, `test_storage_policy_points_to_the_migration_policy`, `test_storage_policy_names_the_phase5_risks`, `test_the_register_of_unverified_statements_matches_the_marked_sentences`, `test_the_policy_text_names_no_machine_path_and_no_key`.

`test_evaldb_docs.py`: `test_evaldb_changelog_has_entry_for_version`, `test_the_changelog_rule_names_each_fault`, `test_evaldb_readme_has_the_documented_sections`, `test_evaldb_readme_quick_start_runs`, `test_the_readme_python_block_finder_has_teeth`, `test_concept_page_storage_exists_and_is_linked`, `test_schema_policy_database_bullet_points_to_the_storage_policy`, `test_the_schema_policy_baseline_is_the_text_without_a_pointer`, `test_the_documents_of_this_batch_are_ascii_and_free_of_machine_paths`.

SCOPE rows P2-24 and P2-25 name six of these (`test_no_string_built_sql`, `test_store_has_no_secret_columns`, `test_store_errors_hide_input`, `test_storage_policy_parity_table_matches_column_types`, `test_storage_policy_states_db_is_not_validation_boundary`, `test_evaldb_changelog_has_entry_for_version`) and keep their names exactly. `test_evaldb_adr0022.py` already allows the name `test_store_errors_hide_input` as `PENDING`; once this file exists the allowance is stale but harmless (its test only subtracts it).

None of the tests pins pydantic-version-dependent or Postgres-version-dependent text: a type is matched by class, a document by a marker, the Postgres types by the catalog (`format_type`, collation), a plan not at all.

## 11. Ambiguities and findings (auto-approval requested on the recommendations)

* **A1. ADR 0019 Table 3 against the column.** Table 3 says `text COLLATE "C"` for `KeyCol` on Postgres. The column is `VARCHAR COLLATE "C"` (SQLAlchemy writes `sa.Text(collation=...)` as `VARCHAR` on Postgres; the database reports `character varying`); the batch B test accepts either spelling. Recommendation: pin the truth in the policy (`varchar COLLATE "C"`), and amend the ADR cell in the next ADR revision; the behaviour is identical (`varchar` without length is `text` with another name). Rejected: changing the column to `text` (a revision for no behaviour).
* **A2. The parity table has three columns.** ADR 0019 says STORAGE_POLICY "copies" Table 3 (six columns: logical type, Python value, Postgres, SQLite, CHECK, tests). The test compares the types, which is what can be compared with a database; the Python value, the CHECK and the tests columns are pinned by the batch A and B tests. Recommendation: the three-column table of section 6 (a reader of the policy gets the types, and the ADR keeps the rest). Rejected: copying six columns (the CHECK text differs by dialect and is not a type).
* **A3. SQL text: a reviewed table, not an absolute ban.** SCOPE says "no `text()` takes an f-string or concatenation". Today 10 sites in `src` do (identifier-only: table and column names of the metadata or of a revision, one validated integer, DDL that cannot bind) and 8 are opaque. An absolute rule would fail on the current tree and force dev to rewrite frozen revision DDL. Recommendation: the table `REVIEWED` of section 2.1, exact in both directions, with reasons, reviewed by security-licensing for P2-24; the Core-only modules have none, and in the revisions every piece is drawn from a literal. Rejected: an absolute ban (it would have no oracle without a src change), and a ruff-only check (S608 sees only SELECT, INSERT, UPDATE and DELETE text, not `PRAGMA`, `DROP` or `CREATE`).
* **A4. E5 and E6 belong to batch G.** The audit finds that the Postgres scrub is defeated by the chained driver error (E5) and that a store error keeps its `__context__` (E6). Batch G pins both. Batch F's probe reads only the text Python shows and fails on Postgres today (E5), and passes after the fix; it pins nothing about `__context__`. (An earlier draft of this batch pinned `__cause__ is None and __context__ is None` for every store error; it was removed because it duplicates batch G and fixes a design the batch G contract leaves open.) Recommendation: as written.
* **A5. The old sentence of the Database bullet.** It says "No database tables exist in 0.0.1, so there are no migrations yet." which is true of `tracer-evalschema` and now reads as false next to the pointer. The test allows only the added sentence (the file's digest is pinned), so the old sentence stays and the pointer sentence must make the scope clear (`The store that creates these tables is ...`). Recommendation: leave it; rewording it is a Phase 3 edit with a new baseline digest.
* **A6. The baseline digest.** `SCHEMA_POLICY_BASELINE` is a constant in the test. A later edit of SCHEMA_POLICY.md for another reason must update it deliberately, with the reviewer. Recommendation: keep it (the owner asked that the schema package stay unchanged except documentation text agreed by test). Rejected: no digest (the test would not notice an unrelated edit).
* **A7. The pointer exemption and the register copy.** Sentences that start with `See ` and only name existing paths need no citation, and every marked sentence is repeated in section 10. Both are choices of this contract; the first keeps orientation text free of invented tests, the second makes the list of unverified statements complete by construction (SCHEMA_POLICY section 16 is the model). Recommendation: as written.
* **A8. Documents ship in the sdist only.** `STORAGE_POLICY.md` and `CHANGELOG.md` sit next to the README in the package directory; the audit (E18) observed that the sdist already holds `README.md`, `alembic.ini` and `tests/`, so these two join it, and the wheel (the modules and migrations only) does not. No test pins the contents of the evaldb sdist. Recommendation: as written.
* **A9. FINDING (scratch only): the full suite deletes `.git` of a copy.** Running the whole workspace suite inside a copy of the repository left the copy without its `.git` directory (and made six git-based Phase 1 tests fail there); with `.git` made immutable the same run passed all 10,417 cases. I did not find which test removes it and the real repository was not affected (its `.git` is intact). Recommendation: someone should find it before a test run inside a clone is trusted; it is not a defect of this batch.
* **A10. Unpinned.** That every sentence cited for a statement really supports it (the reader checks that the test exists, a reviewer checks the fit); the Postgres server-side storage; whether the quick start should also run on Postgres.

## 12. Evidence

`docs/phase2/evidence/red/batch-f/` (each file under 5,000 lines, paths scrubbed):

* `red.py3.11.log` to `red.py3.14.log`: the batch F tests against the committed tree on SQLite, 19 failed, 16 passed, 5 deselected on each of Python 3.11.16, 3.12.14, 3.13.15 and 3.14.7 (every failure is a document or section that does not exist).
* `red-postgres15.py3.11.log` and `red-postgres18.py3.14.log`: `-m postgres` against Postgres 15 and 18, 2 failed and 3 passed each: `test_store_errors_hide_input` (audit finding E5) and the parity test (the policy is missing).
* `oracle-sqlite.py3.11.log` to `oracle-sqlite.py3.14.log`: the scratch oracle (HEAD, the batch F tests, minimal documents, the E5 patch), 35 passed on each version; `oracle-postgres15.py3.11.log`, `oracle-postgres15.py3.14.log`, `oracle-postgres18.py3.11.log`, `oracle-postgres18.py3.14.log`: 5 passed each; `oracle-full-workspace.py3.14.log`: the whole workspace in the oracle, 10,417 passed (the 10,382 earlier cases and the 35 new), 1,184 deselected.
* `mutants.txt`: 57 mutants (15 of `src`, 42 of the documents), 57 caught. `oracle-src.patch` (seven lines of `engine.py`: scrub `args` of the driver error and cut its chain, which is audit finding E5; batch G owns the real fix) and `oracle-docs.patch` (the minimal documents of the oracle; they satisfy every test and are a starting point for the architect, not a text to copy).

The Postgres passwords were generated per container, never written to a file, and the containers (Postgres 15.19 and 18.6 from the local images, bound to 127.0.0.1, named `qa-batchf-*`) were removed; no other container was touched. The oracle is a throwaway copy (`git archive` of HEAD) outside the repository.
