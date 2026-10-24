# Phase 2 batch B contract: tables and codecs (P2-06 to P2-11b)

Author: qa, 2026-10-24. Source: `docs/phase2/SCOPE.md` (sections 1.3 and 1.8), ADR 0019 (Tables 1, 2, 3 and 5, Decisions 2 to 12, Ambiguities 1 to 15), ADR 0020 (Decisions 1 to 3, 6, 7), ADR 0021 (rule 1), ADR 0022 (Decisions 4, 5, 10, 11), the batch A contract `docs/phase2/api-surface-batch-a.md` and its code, and the lead's instructions of 2026-10-24. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or HANDOFF (the one edit outside the tests is the doc-only correction of the lower-bound CI text in the batch A contract). Every statement below is pinned by a test named in section 8, or is marked (unpinned).

## 0. Where the tests are and how to run them

`packages/evaldb/tests/` (the batch A directory), four new files and no change to a batch A file:

| File | Holds |
|---|---|
| `evaldb_support.py` | helpers, no test: ADR table reader, column rules, closed sets, a minimal-row factory, the reference projector, a test-only mini repository, the `db` fixture |
| `test_evaldb_tables.py` | P2-06 to P2-10 and the ADR table tests |
| `test_evaldb_codecs.py` | P2-11a and P2-11b |
| `test_evaldb_bigint.py` | `BigIntCol` (the eighth column type of ADR 0019 Table 3) |

`evaldb_support.py` is imported with `from evaldb_support import ...` (the directory has no `__init__.py`, so pytest puts it on `sys.path`); mypy cannot see sibling modules there, so the import lines carry `# type: ignore[import-not-found,unused-ignore]` and the names are `Any` for mypy. The fixtures `db`, `sqlite_db`, `codecs` and `tables` are bound in each test module as module variables (`db = _support.db`).

Commands are those of batch A: `uv run --locked --all-groups pytest` runs the SQLite side; `TRACER_TEST_POSTGRES_URL=... TRACER_TEST_POSTGRES_SQL_ASCII_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests` runs the Postgres side. No new environment variable.

The `db` fixture is the `store` fixture of batch A (`[sqlite]` and `[postgres]`, the second marked `postgres`, never skipped) with every table of `tracer_evaldb.metadata` created by `metadata.create_all`. SQLite: a new database file per test. Postgres: the schema is created once per session (after a `drop_all` of stale tables), emptied by one `TRUNCATE ... CASCADE` before each test, and dropped by a `config.add_cleanup` at the end of the session. Tests never UPDATE a row (batch D and E add triggers that refuse it) and never DELETE from `deletion_audit` through the fixture. Tests that need no database (signatures, the reference projection, the error hygiene of a pure `decode_*`, the DDL text) carry no `store` parameter and run once.

The tests read, never write: the ADR files (`docs/adr/0019-relational-storage.md`, `0020-deletion-and-retention.md`), the shipped schemas (`packages/evalschema/src/tracer_evalschema/schemas/*.schema.json`), the golden fixtures (`packages/evalschema/tests/fixtures/golden`), the frozen 0.0.1 fixtures (`.../frozen/0.0.1`, with `digests.json`), the adversarial fixtures and registry (`.../adversarial`), and, through `importlib`, the Phase 1 helpers `tests._support_b13` (the T-DEG bundles) and `tests._support_b12` (`expand`, the credential placeholders). The evalschema package directory is appended to `sys.path` for that import.

## 1. Module and file layout (pinned)

```
packages/evaldb/src/tracer_evaldb/
  columns.py      the seven types of batch A, plus BigIntCol (section 2)
  errors.py       the errors of batch A, plus StoreIntegrityError and StoreValueOutOfRange (section 5)
  schema.py       metadata: the one sqlalchemy.MetaData (already there)
  tables.py       the 37 Table objects (section 3)
  codecs.py       EncodedEntity, encode_<table>, decode_<table>, encode, decode (section 4)
  __init__.py     imports tables (so that `import tracer_evaldb` fills metadata) and re-exports
```

Names:

* `tracer_evaldb.metadata` is `tracer_evaldb.schema.metadata`. After `import tracer_evaldb` alone, in a fresh interpreter, it holds exactly 37 tables: the 26 entity tables of ADR 0019 Table 1 and the 11 side tables of Table 2. `alembic_version` is not in it (ADR 0019 Ambiguity 9; Alembic creates it).
* `tracer_evaldb.tables` (a module, imported by `__init__`) has one module attribute per table, named as the table (`tables.turn`, `tables.run_bundle_member`), each the object `metadata.tables[name]`, and no other `sqlalchemy.Table` attribute. It also has `ENTITY_TABLES: Mapping[str, Table]` (the class name of the entity, as in the registry of `tracer_evalschema`, to its table: 26 entries) and `SIDE_TABLES: Mapping[str, Table]` (table name to table: 11 entries).
* `tracer_evaldb.BigIntCol` joins the seven classes of batch A in `columns.__all__` and `tracer_evaldb.__all__`; `StoreIntegrityError` and `StoreValueOutOfRange` join the errors there. `tracer_evaldb.codecs` is imported as a submodule (not re-exported).
* Import rules of batch A still hold for every new module: only the standard library, `sqlalchemy`, `alembic`, `tracer_evalschema` and `tracer_evaldb` may be imported. In particular `codecs.py` must not import `pydantic` (type its arguments with the `tracer_evalschema` classes or `Any`; found while building the oracle) and, by ADR 0019 Decision 1, none of `hashlib`, `hmac`, `rfc8785` (all hashing is `tracer_evalschema.digest.jcs_bytes` and `sha256_hex`; pinned by `test_evaldb_hashes_only_through_the_digest_module`).

## 2. `BigIntCol` (`test_evaldb_bigint.py`)

A SQLAlchemy type like the other seven. Python value: an `int` (exactly; `bool` is refused) in the signed 64-bit range, `-2**63 <= v <= 2**63 - 1`; read back as `int`. Postgres `bigint`; SQLite `INTEGER` (STRICT-legal). `None` is NULL. Anything else (`2**63`, `-2**63 - 1`, `True`, `1.0`, `"1"`, `b"1"`, `Decimal(1)`, a list) raises `ValueError` at bind time (surfacing as `sqlalchemy.exc.StatementError` whose `.orig` is the `ValueError`), with a fixed message that does not hold the value. It is not a subclass of `Float`, `Numeric` or `DateTime`, adds no CHECK of its own (a column that needs `>= 1` or `>= 0` gets it from the table), and orders numerically on both backends. Tests: `test_bigint_column_round_trips_the_signed_64_bit_range`, `test_bigint_column_refuses_values_beyond_64_bits_and_other_types` (12 inputs), `test_bigint_column_orders_numerically`, `test_bigint_column_is_not_a_float_or_a_numeric`, `test_bigint_column_has_no_check_of_its_own`.

ADR 0019 Table 3 also lists CHECKs that batch A did not pin for two older types: `InstantCol` on SQLite (length 27 and the GLOB of `YYYY-MM-DDTHH:MM:SS.ffffffZ`) and `DecimalCol` on SQLite (the GLOB of the decimal text). Batch B pins them on the real tables (`test_sqlite_instant_and_decimal_shape_checks`; ambiguity A6). The mechanism is the dev's: a type-bound CHECK on the two types (the oracle does this and emits `TRUE` for Postgres) or a table-level CHECK.

## 3. The 37 tables

### 3.1 What the tests read from the ADR

`evaldb_support.adr_table*` parse the markdown tables; a change of an ADR table is therefore a change of the tests (escalate to staff-reviewer). The parsers take:

| Source | What is read | Pinned by |
|---|---|---|
| ADR 0019 Table 1 | per row: entity (backticks optional), `` `table` ``, key `` `(id, version)` `` or `` `id` ``, the Projected columns cell (every backticked token, or none when the cell starts with `none`), the Side tables cell, the FK cell: every `` `col` CASCADE|RESTRICT `` pair and every ``UNIQUE `(a, b)` `` | `test_adr0019_entity_table_matches_metadata`, `test_metadata_has_one_table_per_entity` |
| ADR 0019 Table 2 | per row: table, the Columns cell (set), the Key cell (ordered), the Constraints cell: every ``index `...` `` token (ordered column tuple), and the first ``` `col` IN (...) ``` list of `snapshot_member.kind`, `external_ref_index.entity_kind`, `run_bundle_member.kind` | the same, `test_side_table_indexes_and_keys_match_adr_table2`, `test_closed_set_checks_equal_shipped_schema_enums` |
| ADR 0019 Table 3 | per row: the logical type, the first backticked token of the Postgres and of the SQLite cell (`uuid`/`BLOB`, `timestamptz`/`TEXT`, `numeric`/`TEXT`, `text`/`TEXT`, `bytea`/`BLOB`, `text COLLATE "C"`/`TEXT`, `bigint`/`INTEGER`, `boolean`/`INTEGER`) | `test_adr0019_table3_matches_column_classes` |
| ADR 0019 Table 5 | per row: the value object, the classification (must start with `doc only`, `projected as` or `side table`), every backticked token of a `projected as` row (`table.column`, or a column or table name that exists) and of a `side table` row (a table that exists), and the `Totals:` sentence (54, 14, 2) | `test_adr0019_every_value_object_classified` |
| ADR 0020 Decision 3 | the 23 foreign-key rows: child, columns, parent, parent columns, on delete | `test_adr0020_cascade_graph_matches_metadata`, `test_fk_graph_matches_adr0020` |

The 70 value objects are `registry.value_object_classes()`; the 26 entities `registry.entity_classes()`.

### 3.2 Columns, types, nullability

Every entity table has `id`, `schema_version`, `created_at`, `doc`, `doc_sha256`; the nine versioned tables add `version`, `content_digest_scheme`, `content_digest_value` and are keyed `(id, version)`; the other 17 are keyed `id`. The projected columns are exactly the Table 1 cells (listed in section 4.4 as the codec's projection). The column set of each table equals common + versioned + projected, no more and no less, with no repeats.

The class of each column (`type(column.type) is tracer_evaldb.<Class>`, exact) follows ADR 0019 "Column types" and is written as `logical_type(table, column)` in `evaldb_support.py`:

* `DocCol`: `doc`, `deletion_audit.counts`. `Sha256Col`: `doc_sha256`, `sha256`, `content_digest_value`, `key_sha256`.
* `BoolCol`: `metric_result.passed`, and `human_subject` and `delete_on_expiry` with their `caller_` forms.
* `UuidCol`: `id`, every `*_id`, `audit_uuid`, and `artifact.turn_ref` (ambiguity A3).
* `BigIntCol`: `version`, every `*_version`, `seq`, `index`, `start_ms`, `byte_length`, `ordinal`, `tracer_meta.singleton` (ambiguity A4), and the five `run_verdict` counts `required_total`, `passed`, `failed`, `not_applicable`, `errored`.
* `InstantCol`: `created_at`, `started_at`, `occurred_at`, `deleted_at`, `retain_until` and `caller_retain_until`. `DecimalCol`: `amount`, `total`.
* `KeyCol`: every other column, including `schema_version`, `evaldb_version`, `policy_ref`, `language`, `detected_language`, `name`, `key`, `actor`, `reason`, `root_kind`, `kind`, `entity_kind`.

Per backend (`test_column_types_per_backend`, by reflection of the created schema): Postgres `UUID`, `TIMESTAMP` with time zone, `NUMERIC` without precision or scale, `TEXT` (a `KeyCol` may reflect as `VARCHAR`; no length limit, collation `C` exactly on the `KeyCol` columns and none on `DocCol`), `BYTEA`, `BIGINT`, `BOOLEAN`; SQLite `BLOB`, `TEXT`, `TEXT`, `TEXT`, `BLOB`, `TEXT`, `INTEGER`, `INTEGER`. No column has a default or a server default. No column of any table is a `DateTime`, `JSON`, `Float`, `Numeric`, `Date`, `Time` or `Interval`.

Nullability (`test_every_column_has_the_logical_type_and_nullability_of_the_adr`; ambiguity A5): every column is NOT NULL except

| Table | Nullable columns |
|---|---|
| `evaluator` | `test_profile_id`, `test_profile_version` |
| `config_snapshot` | `pricing_catalog_id`, `pricing_catalog_version` |
| `run` | `started_at`, all six `caller_` privacy columns |
| `artifact` | `turn_ref`, and the privacy columns except `human_subject` |
| `transcript` | `detected_language`, and the privacy columns except `human_subject` |
| `turn` | `start_ms` |
| `metric_result` | `na_reason`, `passed` |
| `usage_record` | `occurred_at` |
| `cost_record` | `amount`, `basis`, `currency`, `na_reason` |
| `cost_summary` | `total`, `currency` |
| `dataset_item` | `artifact_id`, `transcript_id`, `source_run_id`, all six privacy columns |
| `run_bundle_member` | `record_version` |

### 3.3 Constraints, all enforced by the database on both backends

Pinned by behaviour (raw inserts through the typed columns; a violation is `sqlalchemy.exc.DBAPIError`, and the test also reads the kind: check, unique, foreign key or not null, from the SQLSTATE on Postgres (`23514`, `23505`, `23503` or `23001` for RESTRICT, `23502`) and from the message on SQLite; each refusal test pairs with an accepted control row so that it fails for the intended constraint):

1. **Keys** (`test_versioned_tables_pk_is_id_version`, `test_record_of_fact_tables_pk_is_id`): as 3.2. A second version of an id is accepted, a repeated `(id, version)` or `id` is a unique violation, `2**63 - 1` is an accepted version.
2. **Version** (`test_version_check_ge_1`): `version >= 1` on the nine tables (0, -1 and `-2**63` refused); `snapshot_member.def_version >= 1` and `snapshot_profile_ref.profile_version >= 1`; `snapshot_member.ordinal >= 0` and `dataset_member.ordinal >= 0`.
3. **Schema version** (`test_schema_version_check_pinned_to_0_0_1`): on all 26 entity tables and on `tracer_meta.schema_version`: only the exact text `0.0.1` (the DDL of both backends contains `schema_version = '0.0.1'` and no other `'0.0.x'` literal; `0.0.2`, `0.0.0`, `0.1.0`, `1.0.0`, `0.0.1 `, ` 0.0.1`, `0.0.01`, the empty text and `0.0` are refused).
4. **Closed sets** (`test_closed_set_checks_equal_shipped_schema_enums`, `test_closed_set_check_accepts_members_and_refuses_others[table.column]`, 46 columns): each is one `CheckConstraint` whose compiled SQL on both backends contains `<column> IN ('a', 'b', ...)` with exactly the members of the shipped schema, in any order: `agent_target.transport` (the three endpoint kinds `ws`, `webrtc`, `sip`), `metric_definition.kind` and `value_type`, `run.mode`, `caller_origin`, `observed_tier`, `transcript.source`, `tool_call_event.source`, `usage_record.source` and `domain`, `turn.role`, `stage_span.stage`, `metric_result.status` and `na_reason`, `run_verdict.status`, `cost_record.status`, `basis`, `na_reason`, `currency`, `cost_summary.completeness` and `currency`, `export_bundle.purpose`, `consent_status` and `human_subject_reason` on `artifact`, `transcript`, `dataset_item` and (as `caller_consent_status`, `caller_human_subject_reason`) `run`, and `content_digest_scheme` on the nine versioned tables and `config_snapshot`; and on side tables `snapshot_member.kind`, `external_ref_index.entity_kind`, `run_bundle_member.kind` (the sets of Table 2), `run_bundle_withheld.kind` (the shipped `WithheldRecord.kind` enum), `deletion_audit.reason` and `root_kind` (ADR 0020 Decisions 6 and 2; ambiguity A10). On an entity table the column has exactly one such list; a side-table column may have further lists that are subsets (`run_bundle_member.kind` also appears in the `record_version` rule). No other column of the 26 entity tables has a string-set CHECK (a language column has none). Every member is accepted and each of a few near-misses (empty, member plus a space, upper case, a prefix form, an unrelated word) is a check violation. The `metric_result.passed`-style rule columns are not closed sets.
5. **Privacy** (`test_privacy_projection_check[artifact|transcript|dataset_item|run]`; `run` uses the `caller_` prefix): `human_subject` true requires `consent_status` and `delete_on_expiry` (`retain_until`, `policy_ref` and `human_subject_reason` may be NULL or set); `human_subject` false requires the other five to be NULL (including `delete_on_expiry`); an absent marker (`human_subject` NULL, only on `dataset_item` and `run`) requires all six NULL; on `artifact` and `transcript` a NULL `human_subject` is a NOT NULL violation.
6. **Cost** (`test_currency_check_is_usd_only`, `test_cost_amount_non_negative_check_both_backends`, `test_not_available_cost_has_no_amount_basis_or_currency`, `test_currency_present_if_and_only_if_amount_present`): currency is `USD` only on both tables (the DDL list is exactly `('USD')`; `usd`, `EUR`, `INR`, `US`, `USDD`, empty, padded refused); `amount >= 0` and `total >= 0`: Postgres numeric comparison, SQLite `NOT GLOB '-*'` (both appear in the DDL of the respective dialect); accepted: `0`, `1`, `1.5`, `0.000000000000000001` and a 30-integer-digit, 18-fraction-digit value, stored with their digits and scale; refused: the negative forms; `status = not_available` implies `amount`, `basis` and `currency` NULL (each alone is refused); `currency` is present exactly when `amount` (`total`) is.
7. **Results** (`test_not_applicable_result_requires_null_passed`): `status = not_applicable` implies `passed IS NULL` (true and false are refused); `scored` and `error` rows accept true, false and NULL.
8. **Paired references** (`test_paired_reference_columns_present_together`): `evaluator.test_profile_id` with `test_profile_version`, `config_snapshot.pricing_catalog_id` with `pricing_catalog_version`: both NULL or both set.
9. **Bundle members** (`test_run_bundle_member_record_version_exactly_for_versioned_kinds`): `record_version` is NOT NULL exactly when `kind` is `pricing_catalog` or `test_profile`.
10. **Meta** (`test_tracer_meta_is_one_row`): the key `singleton` equals 1, so a second row is a unique violation and 0 or 2 is a check violation.
11. **Shapes on SQLite** (`test_sqlite_instant_and_decimal_shape_checks`, SQLite only): every `InstantCol` column refuses text that is not the 27-character `YYYY-MM-DDTHH:MM:SS.ffffffZ` (empty, a date only, no fraction, a space for the `T`, lower-case `z`, an offset, 5 or 7 fraction digits, a letter in a field, 27 arbitrary letters, a leading space) and accepts `0001-01-01T00:00:00.000000Z` and `9999-12-31T23:59:59.999999Z`; the `amount` and `total` columns refuse decimal text that is empty, padded, signed `+`, an exponent, letters, a bare or leading or double `.`, `--1`, `NaN`, `Infinity`, `0x10`, `1,5` and accept `0`, `1`, `10`, `1.5`, 18 fraction digits and 30 integer digits. Leading zeros and trailing fraction zeros are unspecified (A6).

Foreign keys (`test_fk_graph_matches_adr0020`, `test_adr0020_cascade_graph_matches_metadata`, `test_no_fk_uses_set_null_or_set_default`, `test_foreign_key_actions_behave_as_adr0020_states`): the foreign keys of `metadata` are exactly the 23 rows of ADR 0020 Decision 3 (child, columns, parent, parent columns, on delete); every one is `CASCADE` or `RESTRICT` (nothing else, not `SET NULL`, `SET DEFAULT`, `NO ACTION`, no `ON UPDATE`, no deferral; the DDL of both backends contains neither phrase). For each of the 23, behaviour: delete the parent row and see the child removed (CASCADE) or the delete refused with a foreign-key violation (RESTRICT, only `run.config_snapshot_id`), and a child row whose parent does not exist is refused. `test_run_owned_tables_have_run_id_cascade`: the 11 run-owned tables have a NOT NULL `UuidCol` `run_id` with a CASCADE foreign key to `run.id`, and deleting a run removes its rows from all 11 tables, from `external_ref_index`, from `run_bundle` and, through the bundle, from `run_bundle_member` and `run_bundle_withheld`, leaving another run's rows.

No foreign key on any reference to a versioned entity or an unresolved edge (`test_definition_references_have_no_foreign_key`): the persona, test-profile, evaluator, metric, pricing-catalog, rubric and agent-target references of `evaluator`, `metric_binding`, `config_snapshot`, `run`, `metric_result`; `turn.transcript_id` (ADR 0019 Ambiguity 1); `artifact.turn_ref`; the three references of `dataset_item`; `snapshot_member.def_id`, `snapshot_profile_ref.profile_id`, `dataset_member.item_id`, `run_bundle_member.record_id`, `run_bundle_withheld.record_id` and the second column of the three export link tables. Rows naming nothing that is stored are accepted. `test_turn_seq_unique_per_transcript`: exactly one unique constraint or unique index on `turn`, over `(transcript_id, seq)`; two turns of one transcript with one `seq` are a unique violation, the same `seq` in another transcript is accepted, and no transcript row is needed.

### 3.4 Side tables (Table 2)

Columns (as a set), key (ordered) and the listed indexes (ordered column tuple, `Index` objects in `table.indexes`; extra indexes are allowed, entity-table indexes are batch F) are those of Table 2: `snapshot_member` (key `(snapshot_id, kind, ordinal)`, index `(kind, def_id, def_version)`), `snapshot_profile_ref` (`(snapshot_id)`, index `(profile_id, profile_version)`), `external_ref_index` (`(entity_kind, entity_id, key_sha256)`, index `(run_id, entity_kind, key_sha256)`), `dataset_member` (`(dataset_id, dataset_version, ordinal)`, index `item_id`; a composite foreign key to `dataset(id, version)` CASCADE, so deleting one dataset version removes its members and leaves another version's), `run_bundle_member` (`(bundle_id, kind, ordinal)`, index `(kind, record_id)`), `run_bundle_withheld` (`(bundle_id, ordinal)`), `export_bundle_run`, `export_bundle_item`, `export_bundle_profile` (key `(export_id, <second column>)`, index on the second column), `deletion_audit` (key `(audit_uuid)`; a repeated key is a unique violation), `tracer_meta`. Every column is NOT NULL except `run_bundle_member.record_version`. Each key refuses a repeated row (`test_side_table_primary_keys_refuse_duplicates`).

`external_ref_index` unique rule (**ambiguity A1, the one blocking finding**): ADR 0019 Table 2 and ADR 0022 Decision 10 say the key is unique for `run` and for `span`. The golden `RunBundle/valid/maximal.json` (Phase 1, frozen) holds two spans with the same `span_ref` (`otel`, `span_id`, `00f067aa0ba902b7`), so a store with a unique span key could not store a valid bundle, the same argument as ADR 0019 Ambiguity 1. The tests implement the recommendation: the unique partial index is on `(entity_kind, key_sha256)` where `entity_kind` is `run` only (the `WHERE` of both dialects names `'run'` and none of `'span'`, `'transcript'`, `'usage_record'`); `span`, `transcript` and `usage_record` keys repeat (a lookup scoped by `run_id`, the index `(run_id, entity_kind, key_sha256)`); one record still repeats a key only once (the primary key). Tests: `test_external_ref_index_unique_only_for_run`, `test_side_table_indexes_and_keys_match_adr_table2`. If the lead keeps the ADR text, three lines change (`entity_kind = 'run'` to the pair, the span cases of the first test) and the golden RunBundle round-trip cases cannot pass until Phase 1 changes (a 0.0.2 finding); see A1.

### 3.5 SQLite

Every one of the 37 tables is `STRICT` and `WITHOUT ROWID` (`test_sqlite_tables_are_strict_without_rowid`: the dialect options `sqlite_strict=True`, `sqlite_with_rowid=False` on every table, and `PRAGMA table_list` of the created database lists exactly the 37 tables, each with `wr = 1` and `strict = 1`; Alembic's table is not created by `create_all`). On Postgres the DDL contains neither word (`test_ddl_compiles_on_both_backends`, which also compiles every `CreateTable` and `CreateIndex` for both dialects and finds none of `JSON`, `JSONB`, `DATETIME`, `FLOAT`, `REAL`, `DOUBLE`, `TIMESTAMP WITHOUT` in either).

### 3.6 The guard for SQLAlchemy private names

Batch A's column types rely on `Column`'s `_type_bound` argument and on `_traverse_internals`. `test_to_metadata_copy_has_exactly_one_check_per_type_bound_column` (decision log, batch A item 5): all tables are copied with `Table.to_metadata` into a new `MetaData` (two generations, because Alembic's batch mode copies copies); for every column of class `UuidCol`, `Sha256Col` or `BoolCol` the original and both copies hold exactly one `CheckConstraint` named `<column>_uuid_v7`, `<column>_sha256_len` or `<column>_bool` and the DDL of each dialect contains `CONSTRAINT <name> ` exactly once; and for every table the set of DDL lines (a copy may reorder the constraints) of both copies equals the original's, on SQLite and on Postgres. The column count checked equals the number of such columns in the metadata (every one of them).

## 4. Codecs (`tracer_evaldb.codecs`)

### 4.1 Types

```python
@dataclasses.dataclass(frozen=True)
class EncodedEntity:
    table: str  # the entity table
    row: Mapping[str, object]  # column -> Python value, every column of the table
    side_rows: Mapping[str, Sequence[Mapping[str, object]]]  # side table -> rows
```

The values in `row` and in the side rows are the Python values of the column types (section 3.2): `uuid.UUID` (the `.uuid` of the typed ID, never the wire string), an aware UTC `datetime`, `decimal.Decimal`, `str` for text, `bytes` (exactly) for digests (the 32 bytes behind the hex text), `int`, `bool`, `None` for NULL. They can be passed unchanged to `connection.execute(sa.insert(table), row)`. `side_rows` has a key for every side table the entity kind can fill, with an empty sequence when there is nothing: `config_snapshot`: `snapshot_member`, `snapshot_profile_ref`; `dataset`: `dataset_member`; `run`, `stage_span`, `transcript`, `usage_record`: `external_ref_index`; `run_bundle`: `run_bundle_member`, `run_bundle_withheld`; `export_bundle`: `export_bundle_run`, `export_bundle_item`, `export_bundle_profile`; every other entity: no key. Row order inside a sequence is unspecified; the tests compare as multisets (except that the member ordinals say the order).

### 4.2 Functions

For each of the 26 tables (name as in `tracer_evaldb.tables`):

```python
def encode_<table>(model: <Entity>) -> EncodedEntity                        # the 16 other tables
def encode_<table>(model: <Entity>, *, run_id: uuid.UUID) -> EncodedEntity  # the 10 run-owned tables with a storage-only run_id
def decode_<table>(row: Mapping[str, object]) -> <Entity>
def decode_run_bundle(row, *, members, withheld, resolve) -> RunBundle      # see 4.5
def encode(model, *, run_id: uuid.UUID | None = None) -> EncodedEntity       # dispatch on type(model); decode(table: str, row) -> model
```

The ten tables with the keyword-only, required, default-less `run_id` are `artifact`, `transcript`, `turn`, `exchange`, `tool_call_event`, `stage_span`, `metric_result`, `run_verdict`, `cost_record`, `cost_summary` (ADR 0019 Decision 8: taken from the bundle being stored, never in `doc`). `encode_usage_record` takes no `run_id` (its `run_id` is the native field), and `encode_run`, `encode_project`, every versioned table, `encode_config_snapshot`, `encode_dataset_item`, `encode_run_bundle` and `encode_export_bundle` take none; passing one is a `TypeError`, and omitting a required one is a `TypeError`. A model of the wrong class is a `TypeError`, as is a `run_id` that is not a `uuid.UUID`. `decode_<table>(row)` takes the full row of the table as `SELECT *` returns it through the typed columns (a mapping with every column name). `test_codec_functions_and_signatures` pins the names, parameter names and kinds, the defaults, `__all__`, the fields of `EncodedEntity`, and the signatures of `encode` and `decode`; `test_storage_only_run_id_is_required_and_native_run_id_is_not_accepted`; `test_generic_encode_and_decode_dispatch_on_the_model`.

### 4.3 Encode

The row of an entity table is:

* `id`, `schema_version`, `created_at` from the model; `doc` the exact text of `model.model_dump_json()` (for `run_bundle` the envelope, section 4.5); `doc_sha256` the 32 bytes of the SHA-256 of `digest.jcs_bytes(model.model_dump(mode="json"))` (the same dump, restricted to the envelope for a bundle), so it covers the whole document including `schema_version`, `content_digest` and null members;
* on the nine versioned tables `version`, `content_digest_scheme`, `content_digest_value` (the model's own digest; the 32 bytes behind the hex text), and the same two digest columns on `config_snapshot`;
* the projected columns of 4.4.

`metadata` and `metadata_flags` are never projected. The doc keeps key order, white space (none), number spelling (`1e+300`, `1152921504606846976.0`), the escape `\u0000` for U+0000 and every Unicode form byte for byte; no column holds a raw U+0000 and no projection holds free text. Pinned by `test_encode_matches_the_reference_projection_for_every_golden_valid_fixture` (all 138 golden valid files: the column set equals the table's, every value equals the reference projector's with equal type, the side rows equal the reference, and `decode(encode(m)) == m` with identical JSON), `test_encoded_values_have_the_python_types_of_the_column_types`, `test_db_projection_equals_model_fields` (hypothesis over the golden documents: a fresh typed ID, an instant of year 1 to 9999, random I-JSON metadata with the flags the model computes, a random version, `seq` or `index`; the digest recomputed with the right domain; 40 derandomized examples per entity and backend; each example is written, read, decoded and rolled back), `test_db_doc_column_is_text_not_json`, `test_db_nul_in_verbatim_text_round_trips`, `test_db_metadata_large_float_round_trips`.

**Integer ceiling (ambiguity A2).** A projected integer outside the signed 64-bit range is refused before any write with `StoreValueOutOfRange` naming the table and the column, never the value (ADR 0019 Ambiguity 5). In fact the ceiling is lower: `digest.jcs_bytes` refuses an integer outside plus or minus `2**53 - 1`, so no document that holds one can get a `doc_sha256`, and a versioned entity or snapshot with one cannot even be built (its own digest fails). The codec therefore raises `StoreValueOutOfRange` for a projected integer above `2**53 - 1` in absolute value (the column named), and `StoreValueOutOfRange(table, "doc")` for an integer elsewhere in the document that cannot be hashed (for example `Turn.end_ms = 2**53`); it never lets `NonIJsonValueError` out. `2**53 - 1` is stored and read back on both backends. `test_encode_refuses_values_beyond_64_bits_with_a_typed_error` (values `2**63`, `2**64`, `10**30`, `2**63 - 1`, `2**53` in `Turn.start_ms`; `end_ms = 2**53`; the maximum; a versioned entity at `2**53 - 1`; and the Phase 1 fact that a version of `2**53` cannot be digested).

### 4.4 Projections (ADR 0019 Table 1, exactly)

Common columns are in 4.3. Projected columns, with the model field they copy (`ref.id` is the `.uuid` of the typed ID):

| Table | Projected columns |
|---|---|
| `agent_target` | `transport` (the endpoint's `transport`) |
| `persona` | `language` |
| `test_profile`, `rubric`, `pricing_catalog`, `dataset` | none |
| `evaluator` | `persona_id`, `persona_version`, `test_profile_id`, `test_profile_version` (NULL both when no profile) |
| `metric_definition` | `key`, `kind`, `value_type` |
| `metric_binding` | `evaluator_id`, `metric_id`, `metric_version` (the `metric` reference) |
| `project` | `name` |
| `config_snapshot` | `content_digest_scheme`, `content_digest_value`, `evaluator_id`, `evaluator_version`, `rubric_id`, `rubric_version`, `pricing_catalog_id`, `pricing_catalog_version` (NULL both when none) |
| `run` | `project_id`, `config_snapshot_id`, `agent_target_id`, `agent_target_version`, `mode`, `caller_origin`, `observed_tier`, `started_at`, and from `caller_external_id.privacy`: `caller_human_subject`, `caller_human_subject_reason`, `caller_consent_status`, `caller_retain_until`, `caller_delete_on_expiry`, `caller_policy_ref` (all NULL when there is no `caller_external_id`; its `value` is never projected) |
| `artifact` | `run_id`, `sha256` (32 bytes), `turn_ref`, and from `privacy`: `human_subject`, `human_subject_reason`, `consent_status` (`consent.status`), `retain_until`, `delete_on_expiry`, `policy_ref` (`retention.*`; NULL when the marker has no consent or retention) |
| `transcript` | `run_id`, `source`, `detected_language`, the six privacy columns |
| `turn` | `run_id`, `transcript_id`, `seq`, `role`, `start_ms` |
| `exchange` | `run_id`, `index` |
| `tool_call_event` | `run_id`, `source` |
| `stage_span` | `run_id`, `stage` |
| `metric_result` | `run_id`, `metric_id`, `metric_version`, `status`, `na_reason`, `passed` (NULL stays NULL, never false) |
| `run_verdict` | `run_id`, `status`, `required_total`, `passed`, `failed`, `not_applicable`, `errored` |
| `usage_record` | `run_id` (the native field), `domain`, `source`, `occurred_at` |
| `cost_record` | `run_id`, `status`, `basis`, `amount`, `currency`, `na_reason` (`amount` and `currency` from the `Money`; all NULL for a not-available cost) |
| `cost_summary` | `run_id`, `completeness`, `total`, `currency` |
| `dataset_item` | `artifact_id`, `transcript_id`, `source_run_id`, `language`, the six privacy columns (all NULL when the item has no marker) |
| `run_bundle` | `run_id` (the bundle's run) |
| `export_bundle` | `purpose`, `byte_length` (UTF-8 byte length of `doc`) |

Side rows: `snapshot_member(snapshot_id, kind, ordinal, def_id, def_version)` for the evaluator, persona and rubric (ordinal 0) and each metric definition and binding in list order (ordinals from 0), `snapshot_profile_ref(snapshot_id, profile_id, profile_version)` only when the evaluator names a profile; `dataset_member(dataset_id, dataset_version, ordinal, item_id)` for `item_ids` in order; `external_ref_index(entity_kind, entity_id, key_sha256, run_id)`: one row per distinct reference of the record (`run`: each of `external_refs`, `run_id` the run's own; `span`: `span_ref`; `transcript`: `provider_call_ref` when present; `usage_record`: `provider_call_ref` when present, `run_id` the native one), `key_sha256` the 32 bytes of SHA-256 of `jcs_bytes([system, kind, value])` (so case, white space, NFC and NFD forms and U+0000 are distinct, and a value of any length costs one key); `export_bundle_run(export_id, run_id)` for the distinct runs of `run_bundles`, `export_bundle_item(export_id, item_id)` for `dataset_items`, `export_bundle_profile(export_id, profile_id)` for the distinct test profiles of the run bundles in the clear together with the subject of every sealed payload of type `test_profile`.

### 4.5 RunBundle and ExportBundle

`encode_run_bundle(bundle)`: `doc` is `bundle.model_dump_json(include={"id", "schema_version", "created_at", "metadata", "metadata_flags"})` (the envelope, in the order `model_dump_json` writes them), `doc_sha256` the JCS hash of the same restricted JSON dump, `run_id` the bundle's run. `run_bundle_member` has one row per member with `kind` in `run`, `snapshot`, `pricing_catalog`, `test_profile`, `artifact`, `transcript`, `turn`, `exchange`, `tool_call`, `span`, `result`, `verdict`, `usage_record`, `cost_record`, `cost_summary` (the lists `pricing_catalogs`, `test_profiles`, `artifacts`, `transcripts`, `turns`, `exchanges`, `tool_calls`, `spans`, `results`, `usage_records`, `cost_records`, `cost_summaries`; the single `run`, `snapshot` and `verdict`), `ordinal` the 0-based position inside its list, `record_id` the `.uuid`, `record_version` the version for the two versioned kinds and NULL for the others; `run_bundle_withheld` one row per `withheld` entry (`ordinal`, `kind`, `record_id`) in order.

`decode_run_bundle(row, *, members, withheld, resolve)`: all three keyword-only and required; `members` and `withheld` are the rows of the two side tables in any order; `resolve(kind, record_id, record_version)` returns the decoded record (a model) of a member; the function orders the members by `ordinal` inside each kind and the withheld entries by `ordinal`, builds the `RunBundle`, validates it (every bundle rule) and re-checks `id`, `schema_version`, `created_at`, `doc_sha256` and `run_id` against the row, naming the column on a difference. A member that `resolve` cannot give is batch C's concern (incomplete reconstruction, ADR 0022 Decision 8); the contract here is only the happy path and the order. `test_run_bundle_doc_holds_only_the_envelope`, `test_run_bundle_decode_rebuilds_in_member_order_whatever_the_row_order`, `test_tdeg_run_bundles_round_trip_through_the_codecs` (T0, T0m, T1, T2, each simulated and human: equal, identical JSON, the turn, result and withheld order kept, digests verifying).

`encode_export_bundle(export)`: `doc` is `export.model_dump_json()` (sealed ciphertext byte-identical), `byte_length` its UTF-8 length. `decode_export_bundle(row)` takes no `expected_purpose` (the entry points of batch C do; ambiguity A8): it loads the document with `loader.load_bundle(ExportBundle, doc)` and re-checks `purpose` and `byte_length` against the row. No record of the export is written to an internal table (`test_export_bundle_link_rows_cover_clear_and_sealed_test_profiles` pins the three link tables and that nothing else is filled).

### 4.6 Decode, integrity, hygiene

`decode_<table>(row)` does, in this order: the document is parsed with `tracer_evalschema.loader` (strict: duplicate keys compared after unescaping and depth over 256 are refused) and validated by the model (`load_bundle` for an export); then `doc_sha256` of the validated model is compared with the row's; then every other column (common, digest and projected, in that order) is recomputed from the model and compared with the row (equal value and equal type; decimals by digits and scale; instants as instants). Errors:

| Situation | Error |
|---|---|
| a versioned entity (the nine tables) or a `ConfigSnapshot` whose own digest does not verify (the changed document is inside the digest domain) | `tracer_evalschema.errors.DigestMismatch`, unwrapped (its message holds the ID and the scheme only) |
| anything else wrong with the text of `doc`: not JSON, a constant `NaN`, a repeated key, depth over 256, not an object, a model error of any kind (an unknown member, a closed set, the schema version) | `StoreIntegrityError(table, column="doc")` |
| `doc_sha256` of the validated model differs from the row (record of fact edited; versioned entity edited with a consistent digest) | `StoreIntegrityError(table, column="doc_sha256")` |
| any other column differs from what the document says (`id`, `created_at`, `version`, `content_digest_*`, a projection, and `usage_record.run_id`; `run_id` of the ten storage-only tables cannot be checked and is not) | `StoreIntegrityError(table, column=<that column>)` |

`StoreIntegrityError` (a `StoreError`) has the attributes `table: str`, `column: str | None`, `entity_id: str | None` (the dashed text of the row's `id`) and a message that holds the table name, the column name and the id and nothing else. It is raised `from None` (`__cause__` is None and the context is suppressed), so no traceback shows a parser or model message that would quote the document. No error of the codecs, in `str`, `repr`, `args` or the formatted traceback, contains any text of the document or of a tampered value (`test_db_errors_never_echo_document_content`: ten failure shapes on a turn whose text, metadata and role carry markers; `test_db_projection_tamper_rejected_naming_the_column_not_the_value`). `test_db_read_uses_strict_loader` (ten document shapes), `test_db_tampered_doc_rejected_on_read` (25 entities), `test_db_versioned_doc_edited_with_a_consistent_digest_is_caught_by_doc_sha256`.

`StoreValueOutOfRange(StoreError, ValueError)` has `table` and `column` and a message that names them and holds no number.

## 5. Errors added to `errors.py`

| Type | Base | Raised by | Attributes |
|---|---|---|---|
| `StoreIntegrityError` | `StoreError` | `decode_*` (4.6) | `table`, `column`, `entity_id` |
| `StoreValueOutOfRange` | `StoreError`, `ValueError` | `encode_*` (4.3) | `table`, `column` |

## 6. Round trips (what "equal" means)

`test_db_round_trip_every_golden_valid_fixture` (138 files x 2 backends), `test_db_round_trip_frozen_0_0_1_fixtures` (52 files), `test_db_digest_preserved_on_round_trip` (52 files; the entity digest of `digests.json`, the row's `content_digest_value`, and the own digest verified over its own domain, which for the Evaluator and the snapshot is not the generic one), `test_db_adversarial_preserved_fixtures_round_trip` (the 13 preserved or flagged adversarial fixtures of the registry, credential placeholders expanded at run time), `test_db_flagged_and_injected_text_is_stored_verbatim`: the entity is written through the mini repository (the row and its side rows; for a bundle every member in foreign-key order), read back and decoded; the test asserts `decoded == model`, `decoded.model_dump_json() == model.model_dump_json()` (and equal to the dump of a fresh load of the file), `model_dump(mode="json")` equal, the stored `doc` byte-equal to the model's text, the stored `doc_sha256` equal to the recomputed one, and for an entity with a digest `verify_digest` over its own domain passes and the projected digest equals it. T-DEG bundles: see 4.5. Counts of the golden files per entity are in the SCOPE gate item 3; the gate's "T-DEG tables set" test is batch C.

## 7. What batch B does not test (later batches)

Entity-table indexes and the `retain_until` partial indexes (P2-18, P2-20), immutability triggers (P2-16), Alembic (`alembic_version`, `compare_metadata`, batch mode, P2-14, P2-15), the repository (P2-12a to P2-13: `store_*`, `load_*`, atomicity, deduplication, the external-ref lookup), deletion (P2-17a, P2-17b), `verify_database`, `tracer_meta` content and `open_store` (P2-19), Indic fixtures (P2-21), `test_adr0021_policy_rules_are_tested` and `test_adr0022_policy_rules_are_tested` (they need every cited test to exist), and `test_storage_policy_parity_table_matches_column_types` (P2-25; Table 3 is read here only for the type names).

## 8. Test inventory

Counts per file are in section 9. SCOPE names (all present under their exact names): P2-06 `test_versioned_tables_pk_is_id_version`, `test_version_check_ge_1`; P2-07 `test_record_of_fact_tables_pk_is_id`, `test_definition_references_have_no_foreign_key`; P2-08 `test_turn_seq_unique_per_transcript`, `test_run_owned_tables_have_run_id_cascade`; P2-09 `test_currency_check_is_usd_only`, `test_cost_amount_non_negative_check_both_backends`; P2-10 `test_metadata_has_one_table_per_entity`, `test_schema_version_check_pinned_to_0_0_1`, `test_closed_set_checks_equal_shipped_schema_enums`, `test_privacy_projection_check`, `test_no_fk_uses_set_null_or_set_default`, `test_fk_graph_matches_adr0020`, `test_sqlite_tables_are_strict_without_rowid`; P2-11a `test_db_round_trip_every_golden_valid_fixture`, `test_db_round_trip_frozen_0_0_1_fixtures`, `test_db_digest_preserved_on_round_trip`, `test_db_doc_column_is_text_not_json`; P2-11b `test_db_projection_equals_model_fields`, `test_db_nul_in_verbatim_text_round_trips`, `test_db_metadata_large_float_round_trips`, `test_db_read_uses_strict_loader`, `test_db_tampered_doc_rejected_on_read`, `test_db_adversarial_preserved_fixtures_round_trip`, `test_db_errors_never_echo_document_content`; ADR tests `test_adr0019_entity_table_matches_metadata`, `test_adr0019_every_value_object_classified`, `test_adr0020_cascade_graph_matches_metadata`.

Added by qa (each makes a contract item testable): in `test_evaldb_tables.py`: `test_tables_module_exposes_every_table`, `test_adr0019_table3_matches_column_classes`, `test_every_column_has_the_logical_type_and_nullability_of_the_adr`, `test_column_types_per_backend`, `test_foreign_key_actions_behave_as_adr0020_states`, `test_closed_set_check_accepts_members_and_refuses_others` (46 parametrizations), `test_not_applicable_result_requires_null_passed`, `test_not_available_cost_has_no_amount_basis_or_currency`, `test_currency_present_if_and_only_if_amount_present`, `test_paired_reference_columns_present_together`, `test_run_bundle_member_record_version_exactly_for_versioned_kinds`, `test_tracer_meta_is_one_row`, `test_side_table_indexes_and_keys_match_adr_table2`, `test_external_ref_index_unique_only_for_run`, `test_side_table_primary_keys_refuse_duplicates`, `test_dataset_member_cascades_with_its_dataset_version`, `test_adr_side_table_columns_are_not_null_except_record_version`, `test_sqlite_instant_and_decimal_shape_checks`, `test_ddl_compiles_on_both_backends`, `test_to_metadata_copy_has_exactly_one_check_per_type_bound_column` (the guard), `test_alembic_version_is_not_part_of_metadata`; in `test_evaldb_codecs.py`: `test_codec_functions_and_signatures`, `test_storage_only_run_id_is_required_and_native_run_id_is_not_accepted`, `test_generic_encode_and_decode_dispatch_on_the_model`, `test_encode_matches_the_reference_projection_for_every_golden_valid_fixture`, `test_encoded_values_have_the_python_types_of_the_column_types`, `test_db_versioned_doc_edited_with_a_consistent_digest_is_caught_by_doc_sha256`, `test_db_projection_tamper_rejected_naming_the_column_not_the_value`, `test_adversarial_registry_classification_covers_every_case`, `test_db_flagged_and_injected_text_is_stored_verbatim`, `test_encode_refuses_values_beyond_64_bits_with_a_typed_error`, `test_tdeg_run_bundles_round_trip_through_the_codecs`, `test_run_bundle_doc_holds_only_the_envelope`, `test_run_bundle_decode_rebuilds_in_member_order_whatever_the_row_order`, `test_export_bundle_link_rows_cover_clear_and_sealed_test_profiles`, `test_evaldb_hashes_only_through_the_digest_module`, `test_run_owned_and_versioned_groupings_match_the_adr`; in `test_evaldb_bigint.py`: section 2.

## 9. Evidence

`docs/phase2/evidence/red/batch-b/`: `SUMMARY.md` (commands, counts, environment, how each failure was classified), `red.py3.11.log` to `red.py3.14.log` (the batch B tests on the tree of batch A: 605 selected by default, 104 failed and 501 errors, none passed, none skipped), `red-postgres15.py3.11.log` and `red-postgres18.py3.14.log` (the 414 postgres-marked cases: 15 failed and 399 errors), `oracle-sqlite.py3.11.log` to `.py3.15.log` (batch A and B against the scratch oracle: 649 passed, 430 deselected on each of the five versions), `oracle-postgres15.py3.11.log`, `oracle-postgres15.py3.14.log`, `oracle-postgres18.py3.11.log`, `oracle-postgres18.py3.14.log` (430 postgres-marked cases passed on each), `oracle-src.patch` (the oracle: `tables.py`, `codecs.py`, and edits of `columns.py`, `errors.py`, `__init__.py`; qa's reference, the dev owns the real code and may differ), and `mutants.txt` (116 mutants of the oracle: 115 caught, 1 equivalent survivor).

Totals: 1019 collected test cases in 75 test functions (157 in `test_evaldb_tables.py`, 832 in `test_evaldb_codecs.py`, 30 in `test_evaldb_bigint.py`); 605 by default (SQLite side and the tests without a database) and 414 postgres-marked. Postgres 15.19 and 18.6 (images pinned by digest, as in the batch A CI).

## 10. Ambiguities, with recommendations (the lead auto-approves; qa implemented the recommendation in the tests)

A1 (blocking for the ADR text). `external_ref_index` uniqueness for `span`. See 3.4. Recommendation: unique for `run` only; amend ADR 0019 Table 2 and Decision 10 and ADR 0022 Decision 10 and read SCOPE P2-13 `test_external_ref_unique_per_entity_kind` as "unique for the run kind; the other kinds are lookups"; file finding **P2-F3** (0.0.2): a rule that the `span_ref` values of one run are distinct (or an explicit statement that they need not be). Rejected: changing the golden fixture (frozen), a global unique with the first span winning (the store would refuse a valid bundle).

A2. The integer ceiling is `2**53 - 1`, not `2**63 - 1`. See 4.3. Recommendation: keep `BigIntCol` as decided (it is wider than any storable value, which costs nothing) and amend ADR 0019 Ambiguity 5 and finding P2-F2 to say that JCS already limits every stored number to the I-JSON range, so `StoreValueOutOfRange` fires at `2**53` in practice and the "beyond 64 bits" branch is only reachable for a column that is not hashed (none today).

A3. `artifact.turn_ref` is a `UuidCol`. The ADR paragraph says "IDs and every `*_id` projection are `UuidCol`" and "every other projected column is `KeyCol`"; `turn_ref` holds a Turn ID and cannot be text. Recommendation: `UuidCol`; amend the paragraph to list it.

A4. `tracer_meta.singleton` is a `BigIntCol` with the CHECK `= 1`. The paragraph under Table 2 says "every other column `KeyCol`", which cannot hold the integer the CHECK compares. Recommendation: `BigIntCol`.

A5. Nullability is not in the ADR tables. Recommendation: the list in 3.2 (NOT NULL unless the model field is optional; `human_subject` NOT NULL on `artifact` and `transcript`, where the marker is required, nullable on `dataset_item` and `run`, where it is optional). The reading of "paired" and "marker absent" in ADR 0019 Decision 6a and Decision 12 follows.

A6. Table 3's CHECK column for `InstantCol` and `DecimalCol` was not pinned in batch A. Batch B pins the shape on SQLite (`test_sqlite_instant_and_decimal_shape_checks`). The decimal shape is coarse on purpose (digits, at most one `.` inside, an optional leading `-`; leading zeros and trailing fraction zeros unspecified) because the canonical-decimal rule lives in the models. Recommendation: as pinned; the dev chooses the mechanism.

A7. `decode_run_bundle(row, *, members, withheld, resolve)` is the codec for a bundle because a bundle row alone holds only the envelope. Recommendation: as pinned; batch C's `load_run_bundle` supplies `resolve` and handles a missing member before calling it.

A8. `decode_export_bundle(row)` takes no `expected_purpose`; the entry points `store_export` and `load_export` (batch C, SCHEMA_POLICY section 11) do. Recommendation: as pinned, so that a tampered `purpose` column is reported as that column and not as a document error.

A9. Which errors come out of `decode`: `DigestMismatch` unwrapped for the nine versioned tables and the snapshot (SCOPE: "versioned: `DigestMismatch`"), `StoreIntegrityError(column)` for everything else including the schema-version errors of the model. Recommendation: as pinned; the `ConfigSnapshot` is digested, so it follows the versioned entities.

A10. `deletion_audit.reason` and `root_kind` are closed sets in the database. ADR 0020 Decision 6 says "the closed set"; Table 2 only says "ADR 0020 Decision 6". Recommendation: CHECKs with the sets of ADR 0020 Decisions 6 and 2 (three reasons, fourteen root kinds); tested.

A11. The import whitelist of batch A forbids `pydantic` in `tracer_evaldb`; the codecs type with `tracer_evalschema` classes or `Any`. Recommendation: as pinned (a finding for the dev, not a change).

A12. pg8000 reports `ON DELETE RESTRICT` as SQLSTATE `23001`, not `23503`. Recommendation for batch C's class-23 translation hook: map the whole class 23 (`23001`, `23502`, `23503`, `23505`, `23514`) to `IntegrityError`; the batch B helper `violation_kind` already reads all five.

A13. The Postgres schema is created once per pytest session and truncated before each test. Recommendation: as built; a test that must see a fresh schema (batch D) uses its own database or `drop_all` and `create_all`.

A14. The frozen `digests.json` records the generic entity digest (`compute_digest(model)`), which for the Evaluator and the `ConfigSnapshot` is not their own `content_digest` (their domains drop the profile reference digest, ADR 0006 Decision 5). `test_db_digest_preserved_on_round_trip` compares the generic digest for every file and the own digest, over its own domain, for the digested ones. Recommendation: as built; no Phase 1 change.

A15. `Table.to_metadata` may reorder the constraints of a table. The guard compares DDL up to line order. Recommendation: as built.

A16. Hypothesis runs with `derandomize=True` and 40 examples per entity so that the suite is reproducible in CI; a failing example is reported with its seed-free reproduction. Recommendation: as built.

A17. ADR 0019 Decision 5 and Table 3 say the UUID CHECK requires the version nibble 7 and the variant `10xx`; batch A (ambiguity 6, auto-approved) pinned the version nibble only, and the oracle and the tests follow batch A. Recommendation: amend the ADR text to "version nibble 7" (a variant check can be added later as an additive CHECK; no test depends on it).

A18. The oracle's `decode_*` compare `doc_sha256` before the other columns, and the common columns before the projections; the tests tamper one column at a time, so the order is unpinned. Recommendation: as built.

A19. Not run: GitHub-hosted CI, and Postgres on Python 3.15. Run locally against the oracle: Python 3.11, 3.12, 3.13, 3.14 and 3.15 (SQLite side, 649 passed on each; 3.15 is non-blocking in CI), and Python 3.11 and 3.14 with Postgres 15.19 and 18.6 (430 postgres-marked cases passed on each).
