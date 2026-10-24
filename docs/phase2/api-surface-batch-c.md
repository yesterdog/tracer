# Phase 2 batch C contract: repository, exports, re-import, immutability (P2-12a, P2-12b, P2-12c, P2-13, P2-16)

Author: qa, 2026-10-24. Source: `docs/phase2/SCOPE.md` (section 1.4, Decisions D4), ADR 0019 (Decisions 12 to 18, revision 35), ADR 0020 (Decisions 5 and 7), ADR 0022 (Decisions 1 to 13, revision 35), the batch A and B contracts (`api-surface-batch-a.md`, `api-surface-batch-b.md`), the implemented tables and codecs of `packages/evaldb/src/tracer_evaldb` and the decision log (entries "PHASE 2"). qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or HANDOFF. Every statement below is pinned by a test named in section 9, or is marked (unpinned).

## 0. Where the tests are and how to run them

`packages/evaldb/tests/` (the batch A and B directory), seven new test files, one new helper module, and no change to an earlier file:

| File | Holds |
|---|---|
| `evaldb_repo_support.py` | helpers, no test: the Phase 1 bundle builders behind small functions, `renew_ids` (a bundle document with new IDs for chosen records), `sized_bundle_doc`, a statement recorder, a fault injector, a pause hook, whole-database dumps |
| `test_evaldb_definitions.py` | P2-12a, plus `store_project`, `store_dataset_item`, the loaders, the API shape |
| `test_evaldb_run_bundle.py` | P2-12b (write unit, load, rescoring, ownership, tiers, huge call, statement count, incomplete loads, N18) |
| `test_evaldb_export.py` | P2-12c |
| `test_evaldb_reimport.py` | P2-13 |
| `test_evaldb_triggers.py` | P2-16 |
| `test_evaldb_error_mapping.py` | ADR 0019 Decision 18 (A12): the SQLSTATE hook, the typed constraint error, the hygiene of both |
| `test_evaldb_adr0022.py` | `test_adr0022_policy_rules_are_tested` and the existence of every test the SCOPE rows of the batch name |

Commands are those of batches A and B: `uv run --locked --all-groups pytest` runs the SQLite side; `TRACER_TEST_POSTGRES_URL=... TRACER_TEST_POSTGRES_SQL_ASCII_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests` runs the Postgres side. No new environment variable and no change to `conftest.py`, the root `pyproject.toml` or CI. Every test that touches a database uses the `db` fixture of batch B (the `store` fixture, `[sqlite]` and `[postgres]`, with every table created by `metadata.create_all`); the `[postgres]` case carries the marker and is never skipped. The tests reach the repository only through `tracer_evaldb` (the names are re-exports; `test_repository_functions_and_signatures` pins that they are the objects of `tracer_evaldb.repository`).

Inputs: the Phase 1 T-DEG bundles (T0, T0m, T1, T2, each simulated and human, `_support_b13`), the golden RunBundle, ExportBundle, Project, DatasetItem, ConfigSnapshot and definition fixtures, and bundle documents built with `_support_b7` (a fresh run, snapshot and project for every call). Every round trip asserts model equality, a byte-identical `model_dump_json()` and that the own digests verify (`verify_own_digest`: the snapshot, the definitions it embeds, the catalogs and the profiles of a bundle).

## 1. Modules and names (pinned)

```
packages/evaldb/src/tracer_evaldb/
  repository.py   the 15 functions of section 2, the 4 result types of section 2.2 and translate_integrity_error
  triggers.py     the immutability triggers (section 6)
  errors.py       the errors of batches A and B, plus the 8 of section 7
  engine.py       create_store_engine (unchanged signature) plus the SQLSTATE hook (section 7.1)
  __init__.py     re-exports every public name above; imports repository and triggers so that
                  `import tracer_evaldb` alone fills metadata and attaches the trigger DDL
```

`tracer_evaldb.__all__` gains: the 15 functions, `StoreResult`, `MissingMember`, `LoadedRunBundle`, `ExternalRefMatch`, the 8 errors, and `triggers`. `translate_integrity_error` is public in `tracer_evaldb.repository` only. The import rules of batch A still hold (standard library, `sqlalchemy`, `alembic`, `tracer_evalschema`, `tracer_evaldb`; no `pydantic`, `hashlib`, `hmac`, `rfc8785`); hashing goes through `tracer_evalschema.digest`.

## 2. The repository API

### 2.1 Session, connection and transaction handling

* Every function takes the `sqlalchemy.engine.Engine` as its first positional argument (`engine`). It must be the engine of `create_store_engine` (the hook, `hide_parameters`, the SQLite pragmas and the real `BEGIN` live there; behaviour with any other engine is unpinned). A `Connection`, a URL string, a `URL` object or `None` is a `TypeError` raised before any statement (`test_repository_functions_require_an_engine`). There is no module-level engine, no session object and no cache.
* Every function opens exactly one transaction with `engine.begin()`: committed when the function returns, rolled back when any exception leaves it. No function commits partway (`test_run_bundle_store_is_atomic`); the use of a savepoint or of a second transaction is unpinned and not wanted. A caller that needs several writes to be atomic uses the write unit (`store_run_bundle`, `store_export`); there is no multi-call transaction in batch C.
* An exception that is not a database error (for example the fault injected by the tests) leaves the function unchanged and unwrapped, after the rollback. A database `IntegrityError` raised by a write is re-raised as `StoreConstraintViolation` (section 7.2); any other database error is not translated.
* The writers validate before they send an `INSERT`: each takes its argument, serialises it with `model_dump_json()`, and loads that text again with `loader.load_model` (`load_bundle` for bundles), so an instance built or changed without validation (`model_copy`, `model_construct`) cannot be stored (`test_store_functions_revalidate_instances_built_without_validation`, `test_invalid_run_bundle_never_stored`). Every integer the codecs refuse (`StoreValueOutOfRange`), the project check and the internal-snapshot rule are decided before the first `INSERT`, `UPDATE` or `DELETE`; the tests record the statements (`Statements.writes`) and require none.
* The identifier arguments are typed IDs of the right class or a `uuid.UUID`; a `str`, `bytes`, `int`, `None` or the ID of another entity kind is a `TypeError`; a `uuid.UUID` that is not version 7 is a `ValueError` whose message contains "version 7" (the typed ID refuses it). Reading functions never raise for a well-formed ID that names nothing except where section 2.3 says `RecordNotStored`.

### 2.2 Signatures and results

```python
def store_project(engine, project) -> StoreResult
def store_definition(engine, definition) -> StoreResult      # one of the nine versioned entities, else TypeError
def store_snapshot(engine, snapshot) -> StoreResult
def store_dataset_item(engine, item) -> StoreResult
def store_run_bundle(engine, bundle) -> StoreResult           # a RunBundle instance, else TypeError
def store_export(engine, export, *, expected_purpose) -> StoreResult   # ExportBundle | str | bytes

def load_project(engine, project_id) -> Project
def load_definition(engine, kind, definition_id, version) -> <the entity>   # kind: one of the nine table names
def load_snapshot(engine, snapshot_id) -> ConfigSnapshot
def load_dataset_item(engine, item_id) -> DatasetItem
def load_run_bundle(engine, bundle_id, *, allow_incomplete=False) -> LoadedRunBundle
def load_export(engine, export_id, *, expected_purpose) -> ExportBundle

def latest_version(engine, kind, definition_id) -> int | None
def find_snapshot_by_digest(engine, content_digest) -> ConfigSnapshot | None   # a ContentDigest, else TypeError
def find_by_external_ref(engine, entity_kind, ref, *, run_id=None) -> tuple[ExternalRefMatch, ...]
```

`test_repository_functions_and_signatures` pins the parameter names, kinds and defaults exactly as written (`expected_purpose` keyword-only with no default; `allow_incomplete` keyword-only, default `False`; `run_id` keyword-only, default `None`; every other parameter positional-or-keyword with no default).

```python
@dataclass(frozen=True)
class StoreResult:
    kind: str
    entity_id: TypedId
    version: int | None
    created: bool
    inserted: Mapping[str, int]


@dataclass(frozen=True)
class MissingMember:
    kind: str
    record_id: TypedId
    record_version: int | None


@dataclass(frozen=True)
class LoadedRunBundle:
    bundle_id: TypedId
    complete: bool
    missing: tuple[MissingMember, ...]
    bundle: RunBundle | None
    records: Mapping[str, tuple[EntityBase, ...]]


@dataclass(frozen=True)
class ExternalRefMatch:
    entity_kind: str
    entity_id: TypedId
    run_id: RunId
```

* `StoreResult.kind` is the entity table written (`persona`, `config_snapshot`, `run_bundle`, `export_bundle`, `project`, `dataset_item`, ...); `entity_id` is `model.id`; `version` is the version of a versioned entity, else `None`; `created` is true when the root row (the entity, the snapshot, the bundle, the export) was inserted by this call; `inserted` maps an entity table to the number of new entity rows this call inserted (a table with 0 is absent; side-table rows are not counted).
* `MissingMember.kind` is a `run_bundle_member` kind (`run`, `snapshot`, `pricing_catalog`, `test_profile`, `artifact`, `transcript`, `turn`, `exchange`, `tool_call`, `span`, `result`, `verdict`, `usage_record`, `cost_record`, `cost_summary`), `record_id` the typed ID of that kind, `record_version` the version for `pricing_catalog` and `test_profile` only.
* `LoadedRunBundle.missing` is in member order (the kind order above, then the ordinal); `bundle` is `None` unless `complete`; `records` maps a kind to the records that remain, in member order.
* `ExternalRefMatch.entity_kind` is `run`, `span`, `transcript` or `usage_record`; `entity_id` the `RunId`, `StageSpanId`, `TranscriptId` or `UsageRecordId`; `run_id` the run that owns the record (for a run, its own ID).

`test_result_types_are_frozen_dataclasses` pins the four classes (fields, order, frozen). A complete load has `records == {}` (the bundle holds the records); an incomplete load has one entry for each kind that still has at least one record, and none for a kind with none (`test_run_bundle_load_reports_incomplete_after_member_deletion`).

### 2.3 Behaviour by function

**`store_definition`, `store_project`, `store_dataset_item`** (`test_store_definition_*`, `test_store_project_round_trip_and_conflict`, `test_store_dataset_item_round_trip_and_conflict`). Insert when absent; when the key is present compare `doc_sha256` (the whole stored document, not the content digest): equal is a no-op (`created=False`, `inserted={}`, not one `INSERT`, `UPDATE` or `DELETE` is sent, the database is byte-identical), different is `tracer_evalschema.errors.ConflictingVersionError` for a versioned entity (key `(id, version)`) and `ConflictingRecordError` for a Project or DatasetItem. The error message names the kind, the ID and (versioned) the version and holds no content. Another version of the same ID is not a conflict. A Dataset also writes its `dataset_member` rows. A DatasetItem naming an artifact, transcript or run that is not stored is accepted (a synthetic item; ADR 0020 Q11).

**`store_snapshot`** (`test_store_snapshot_*`). First `snapshot.check_internal_snapshot`: a profile reference with an absent or keyed digest is `InternalSnapshotRefused` (section 7.3), before any `INSERT`. Then, in one transaction, the embedded persona, rubric, evaluator, metric definitions and metric bindings are inserted when absent and compared by `doc_sha256` when present (different: `ConflictingVersionError`, and then nothing at all is written, the snapshot included), then the snapshot (different content under the same ID: `ConflictingRecordError`, not the version error) with its `snapshot_member` and `snapshot_profile_ref` rows. A definition that is already stored and equal is shared and not inserted again. The golden `ConfigSnapshot/maximal*.json` files carry a test-profile reference with no digest, so they are refused as internal data; the tests give them a plain digest first (that field is outside the snapshot digest domain).

**`latest_version(engine, kind, definition_id)`**: the maximum stored `version` of that ID in that table, or `None`. `kind` outside the nine versioned table names (`project`, `run`, `Persona`, `persona ` and the like) is a `ValueError` whose message contains "kind". Another table with the same UUID does not count.

**`find_snapshot_by_digest`**: the snapshot whose projected `content_digest_scheme` and `content_digest_value` equal the given digest, decoded and verified like `load_snapshot`, else `None`. The scheme is part of the key (the same value under the keyed scheme finds nothing).

**`load_*`** raise `RecordNotStored(kind, record_id[, version])` (section 7.3) for a well-formed ID that names no stored row (`load_definition` includes the version); they decode through the batch B decoders, so a tampered row raises what those raise.

## 3. `store_run_bundle` and `load_run_bundle` (P2-12b)

### 3.1 Write

In this order, all but the last step before the first write (`test_invalid_run_bundle_never_stored`, `test_store_run_bundle_*`):

1. `TypeError` unless `bundle` is a `RunBundle`. Re-validation: `loader.load_bundle(RunBundle, bundle.model_dump_json())` (size limit, strict loader, every model rule, `check_run_bundle`); its errors are not wrapped (`pydantic.ValidationError` or a `TracerSchemaError`).
2. `snapshot.check_internal_snapshot` of the embedded snapshot: `InternalSnapshotRefused`.
3. Encoding of every record with the batch B codecs: `StoreValueOutOfRange(table, column)` (an `end_ms` of `2**53` is `("turn", "doc")`).
4. In the transaction, before any `INSERT`: the run's project is stored, else `ProjectNotStored(project_id)` (the store never creates a project); then the re-import check of ADR 0022 Decision 11 (section 5), which raises `ReimportConflict` before anything is written.
5. Insert-or-verify, parents first: embedded definitions and snapshot, the bundle's pricing catalogs and test profiles (versioned: `ConflictingVersionError` on a different document), the run, then the run-owned records, then the bundle row and its `run_bundle_member` and `run_bundle_withheld` rows. For a record of fact whose ID is stored: **ownership first**, then content. A stored record whose owner (`run_id`) is another run is `RecordOwnershipConflict(kind, record_id)` whether or not the content is equal (for `usage_record` the owner is its native `run_id`); the same owner and a different `doc_sha256` is `ConflictingRecordError` naming the kind and the ID; equal is shared, not inserted again. Ownership is decided before content, so `RecordOwnershipConflict` is what a second run gets for any ID it shares (`test_record_owned_by_another_run_rejected`, one case per record kind).
6. The bundle row: the same ID with the same envelope (`doc_sha256`), the same run and the same member and withheld rows is a no-op; any difference is `ConflictingRecordError` (`test_same_bundle_id_with_other_members_is_a_conflict`).

`test_store_same_run_bundle_twice_is_a_no_op`: the second call returns `created=False`, `inserted={}`, sends no write statement and leaves every table identical; so does a fresh equal instance. A rescoring (a new bundle ID for the same run listing the shared records and its own results and verdict) inserts only the new rows: `inserted` is exactly `{"metric_result": n, "run_verdict": 1, "run_bundle": 1}`, every other table keeps its row count, every run-owned row keeps its owner (`test_rescoring_bundle_shares_records_without_duplication`); a shared record with other content is `ConflictingRecordError` for each of the ten record lists, the run, the verdict and the snapshot, with nothing of the new bundle stored (`test_rescoring_bundle_conflicting_record_rejected`); a stored catalog with other content under the same `(id, version)` is `ConflictingVersionError` (`test_run_bundle_conflicting_pricing_catalog_rejected`). N18 (ADR 0022 Decision 12): a corrected language declaration is a new Transcript, new Turn records naming it and new results in a new bundle of the same run; the old rows stay and `(transcript_id, seq)` never repeats (`test_n18_changed_language_declaration_is_a_new_transcript_in_a_new_bundle`). Two runs may share a snapshot and its definitions (`test_two_runs_share_one_snapshot_and_its_definitions`).

Tiers: storage does not depend on the observed tier. For each of the eight T-DEG bundles the set of tables with new rows after `store_project` and `store_run_bundle` equals `tests.evaldb_repo_support.table_set_of(bundle)`, a function of the bundle's own non-empty member lists (no tier input); T1 and T2 fill the same tables, T0 and T0m fill the same tables, and the tables T1 fills that T0 does not are exactly `tool_call_event`, `stage_span` and `external_ref_index` (the T0 bundles hold no tool call and no span, and the rows for them are not invented) (`test_tdeg_bundles_round_trip_identical_table_set`). Every `not_applicable` result is stored with `passed` NULL (never 0 or false) and a reason, every `not_available` cost with `amount`, `basis` and `currency` NULL and a reason, and a cost summary without a total with `total` and `currency` NULL; the eight T-DEG bundles hold 9, 9, 4, 4, 1, 1, 1 and 1 not-applicable results, a not-available cost in six of them (T0, T0m and T2) and a cost summary without a total in four (T0 and T0m), so the tests are not vacuous.

### 3.2 Statement-count invariant (`test_store_run_bundle_statement_count_independent_of_record_count`)

Method: register a `before_cursor_execute` listener on the engine (`sqlalchemy.event.listen(engine, "before_cursor_execute", ...)`; one event per DBAPI `execute` or `executemany` call, an `executemany` of 4,000 rows is one event), call `store_run_bundle`, count the events. The BEGIN of the SQLite engine and every `SELECT` count. The count must be the same number for `sized_bundle_doc(4, 2, 2)`, `(400, 60, 40)` and `(4000, 600, 400)` (turns, tool calls, usage records each with a reported cost record; the same tables are non-empty in all three), and no table is the target of more than one `INSERT` statement. Consequences for the implementation: one multi-row insert per table (`executemany`; keep `insertmanyvalues` from paging it), one existence `SELECT` per table, constant work per table. The invariant is pinned for up to 10,000 records of each kind (the tests stay below 4,000): a chunked `IN` list above the database's variable limit is allowed to add statements beyond that.

### 3.3 Read

`load_run_bundle(engine, bundle_id, *, allow_incomplete=False)`:

* An ID that names no stored bundle (or whose run was deleted: the bundle goes with its run, foreign key 17) is `RecordNotStored("run_bundle", id)`.
* Every member present: `LoadedRunBundle(complete=True, missing=(), bundle=<the RunBundle>, records={})`; every list in member order (the order the bundle was stored in, which the tests make differ from any key order by storing reversed lists), `withheld` in order, `model_dump_json()` byte-identical to the stored bundle's, the own digests verifying. This holds with `allow_incomplete=True` too.
* A member missing (deleted rows; foreign keys do not protect them): `allow_incomplete=False` raises `IncompleteRunBundle(bundle_id, missing)` (section 7.3) listing every missing `MissingMember` in member order; its message names the bundle ID and the number missing and holds no content (not a turn text, not a tool name). `allow_incomplete=True` returns `complete=False`, `bundle=None`, `missing` as above and `records` as in section 2.2, each record decoded by its own model; a partial set is never a `RunBundle`. The test runs on a bundle stored with its lists in ID order and on one stored with every list reversed (so that member order is not ID order), and deletes a catalog, two turns and a tool call: `missing` and `records` follow the member order in both.
* A withheld record is not a missing member (`test_run_bundle_withheld_round_trip`: the golden maximal bundle has nine and loads complete).

## 4. Exports (P2-12c)

`store_export(engine, export, *, expected_purpose)` and `load_export(engine, export_id, *, expected_purpose)`.

* `expected_purpose` is keyword-only with no default. Omitting it, passing it positionally, `None`, a non-`str`, or a `str` that is not `internal`, `shared_export` or `benchmark` is a `TypeError` (type) or `ValueError` (value, message contains "purpose"), before any read or write. `None` is not "any purpose" here, unlike `loader.load_bundle` (`test_store_export_requires_expected_purpose`, `test_load_export_requires_expected_purpose`).
* `store_export` takes an `ExportBundle`, `str` or `bytes` (anything else, a `RunBundle` included, is a `TypeError`). The input goes through `loader.load_bundle(ExportBundle, data, expected_purpose=expected_purpose)`: the 256 MiB limit is compared before anything is parsed (`BundleSizeError`; the test passes `bytes(256 MiB + 1)`, zero-filled and never read), then strict parsing, the model and the purpose check; a different purpose is the `ValueError` `[purpose] expected X, the bundle says Y` of evalschema, unwrapped, for an instance and for text alike, with no write (`test_store_export_purpose_mismatch_rejected`, `test_store_export_validates_its_input`).
* The stored `doc` is `export.model_dump_json()` of the validated export, whatever the input form (instance, text, bytes, re-indented text); `purpose` and `byte_length` (UTF-8 length of `doc`) are projected; sealed ciphertext, nonce and key wrap are byte for byte in the stored text (`test_export_round_trip_byte_identical`, `test_export_sealed_payload_ciphertext_unchanged_at_rest`). Equal re-store is a no-op (no write statement); the same ID with another document is `ConflictingRecordError` (`test_store_export_is_idempotent_and_a_changed_export_is_a_conflict`).
* Every run, dataset item and test profile the export carries (the link rows of the batch B codec: the runs of `run_bundles`, the `dataset_items`, the test profiles in the clear and the subjects of sealed `test_profile` payloads) must be stored: a missing one is `ExportReferenceNotStored(kind, record_id)` with `kind` in `run`, `dataset_item`, `test_profile`, and nothing is written (`test_store_export_requires_stored_runs_items_and_profiles`, `test_store_export_requires_the_profile_a_sealed_payload_names`). "Stored" means a row with that ID exists in `run`, `dataset_item` or `test_profile` (any version); the tests use stub rows for the golden exports.
* Locks (ADR 0020 Decision 5, `test_store_export_locks_the_rows_it_links`): before the link rows are inserted, the transaction holds on each of those rows a lock a deletion conflicts with. Postgres: `SELECT ... FOR KEY SHARE` on exactly those rows (the test pauses the writer at its first link-row `INSERT` and probes from a second connection: `FOR UPDATE NOWAIT` fails with SQLSTATE `55P03`, `FOR NO KEY UPDATE NOWAIT` succeeds, a run the export does not carry is free). SQLite: the write transaction itself serializes writers (a second engine with `busy_timeout_ms=0` cannot write while it runs).
* Nothing of the export enters the internal tables: after `store_export` only `export_bundle` and the non-empty link tables changed (`test_export_records_not_decomposed_into_internal_tables`).
* `load_export`: unknown ID is `RecordNotStored("export_bundle", id)`; the row's `byte_length` is compared with 256 MiB before `doc` is parsed (`BundleSizeError`; exactly 256 MiB is read, so garbage there is a `StoreIntegrityError`); then `codecs.decode_export_bundle` (re-checks `purpose` and `byte_length`), then the purpose check (`[purpose]` `ValueError`) (`test_export_size_limit_enforced_before_parse`).

## 5. Re-import (P2-13)

`external_ref_index` rows are written by the codecs; the repository adds the lookup and the conflict.

* `find_by_external_ref(engine, entity_kind, ref, *, run_id=None)`: `ref` is a `tracer_evalschema.ids.ExternalRef` (else `TypeError`); the key is `SHA-256(JCS([system, kind, value]))` (`test_external_ref_key_is_hash_of_jcs_tuple`: not a join of the parts, the order matters, a 1 MB value costs one 32-byte key, a reference listed twice in one run is one row). `entity_kind` outside `run`, `span`, `transcript`, `usage_record` is a `ValueError` containing "entity_kind". For `run` the lookup is global and `run_id` must be `None`; for the other kinds the lookup is within one run and `run_id` is required: a missing `run_id` for them and a given one for `run` is a `ValueError` containing "run_id"; a `str` `run_id` is a `TypeError`. The result is a tuple, possibly empty, ordered by ascending entity UUID (time order), of `ExternalRefMatch`.
* Exactness (`test_external_ref_lookup_is_exact`): case, leading and trailing white space, a non-breaking space, NFC and NFD, precomposed and decomposed nukta, ZWJ, ZWNJ and none, U+0000 inside the value, an empty value, an empty system, and the swap of system and kind are 20 different references; each of 20 runs is found by its own reference and by no other; near misses find nothing.
* Uniqueness (`test_external_ref_unique_per_entity_kind`): a second run (other ID) whose `external_refs` shares one reference with a stored run is `ReimportConflict` (the database's partial unique index for the kind `run` is the backstop). Spans, transcripts and usage records are not unique: the Phase 1 builder's two spans of one run share a `span_ref` and are both stored, a second run may use the same reference, and three usage records with one `provider_call_ref` in one run are all found by the run-scoped lookup, in two runs without mixing (`test_child_provider_call_refs_not_unique`).
* Conflict (`test_reimport_conflicting_content_raises_reimport_conflict`): the check runs before the plain ID comparison and before any write (no `INSERT`, `UPDATE` or `DELETE`). A `run` arrives and some key of its `external_refs` names a stored run whose ID differs, or whose `doc_sha256` differs, from the arriving run: `ReimportConflict(entity_kind="run", stored_id=<RunId>)`, a subclass of `ConflictingRecordError` (section 7.3) whose message holds the kind and the stored ID and neither the reference nor any content. It applies to a new ID for the same external call, to the same ID with changed content, and to a match on only one of several references. The same ID with changed content and no matching reference is the plain `ConflictingRecordError` (and not a `ReimportConflict`); the identical import is not a conflict (`test_reimport_conflict_is_a_conflicting_record_error_checked_first` adds the case with a changed run and a changed turn: the run diagnosis comes first).

## 6. Immutability triggers (P2-16, ADR 0020 Decision 7)

Module `tracer_evaldb.triggers` (pinned names): `update_trigger_name(table) -> "trg_<table>_no_update"`, `delete_trigger_name(table) -> "trg_<table>_no_delete"`, `DELETE_PROTECTED_TABLES = ("deletion_audit",)`, `UPDATE_TRIGGER_FUNCTION = "tracer_reject_update"`, `DELETE_TRIGGER_FUNCTION = "tracer_reject_delete"` (`test_trigger_names_are_pinned`).

* **Which.** One `BEFORE UPDATE` row trigger on each of the 37 tables (`trg_<table>_no_update`) and one `BEFORE DELETE` row trigger on `deletion_audit` (`trg_deletion_audit_no_delete`): 38 triggers and no other user trigger (`test_immutability_triggers_exist_after_migrations`, which reads `sqlite_master` and `pg_trigger`: on Postgres `tgtype` is exactly row + before + update (19) or row + before + delete (11), the function is `tracer_reject_update` or `tracer_reject_delete`; `alembic_version` has none and is not in the metadata).
* **How they are created (batch C).** By `metadata.create_all`: DDL attached to the tables and to the `MetaData` with SQLAlchemy events (`after_create` per table; on Postgres a `before_create` on the `MetaData` first runs `CREATE OR REPLACE FUNCTION` for the two functions), so the `db` fixture of batch B (`create_all`) already has them. `drop_all` removes them with their tables (the Postgres functions stay and are replaced by the next `create_all`; the test drops and recreates the schema and compares the 38 triggers again). Batch D's revision `0001` creates the same objects with the same names from the same DDL (`tracer_evaldb.triggers` is the single source) and re-runs the catalog test on the migrated schema.
* **What they do.** Any `UPDATE` that matches a row raises, whatever the column and even when the new value equals the old (`test_update_rejected_on_every_table`: one attempt per column of each of the 37 tables, one with every column, one selected by primary key; the row is unchanged afterwards). The raised error is `sqlalchemy.exc.IntegrityError` on both backends: SQLite `RAISE(ABORT, ...)` is natively one; the Postgres function raises with SQLSTATE `23001` (class 23, translated by the hook of section 7.1). The database message holds the table name and the word "immutable" and no value (the test updates `doc` to a marker and requires the marker absent). A statement that matches no row is not an error. `DELETE` is allowed on every table but `deletion_audit` (the cascades and the deletion roots of batch E need it): `test_delete_is_allowed_on_every_table_except_deletion_audit`; on `deletion_audit` every `DELETE` raises the same kind of error naming the table (`test_deletion_audit_rows_cannot_be_deleted`) and nothing references it by foreign key. `TRUNCATE` is not intercepted (the batch B fixture relies on it).

## 7. Errors

### 7.1 The SQLSTATE hook (`tracer_evaldb.engine`)

pg8000 raises every server error as `ProgrammingError`, constraint violations included (batch A ambiguity 3). `create_store_engine` for Postgres registers a `handle_error` hook: when the driver error carries a SQLSTATE of class 23 the hook returns `sqlalchemy.exc.IntegrityError(statement, parameters, orig, hide_parameters=True)` (the class 23 codes are `23000`, `23001`, `23502`, `23503`, `23505`, `23514`, `23P01`; `23001` for `RESTRICT` and the trigger, `23502` not null, `23503` foreign key, `23505` unique, `23514` check). Pinned names: `engine.INTEGRITY_SQLSTATE_CLASS == "23"`, `engine.sqlstate_of(exception) -> str | None` (the `C` field of the dict in `args[0]` of a pg8000 error, else `None`), `engine.is_integrity_sqlstate(code) -> bool` (true for exactly a five-character string starting with `23`) (`test_sqlstate_helpers`). Every other class (`22`, `25`, `40`, `42`, `55`, `57`, `08`, an unknown or missing SQLSTATE) is left as it is, and so is an exception that is not a database error. SQLite needs no hook (`sqlite3.IntegrityError` maps natively); `test_constraint_violations_are_integrity_errors_on_both_backends` pins `IntegrityError` for check, unique, not-null, foreign-key and RESTRICT violations on both backends, with the `orig` kept and, on Postgres, the SQLSTATE of each (RESTRICT is `23503` on Postgres 15.19 and `23001` on 18.6; the test accepts both). `DBAPIError` assertions of batches A and B still hold (`IntegrityError` is a subclass).

The translated error carries no row content (`test_translated_integrity_errors_carry_no_row_content`): Postgres puts the failing row in the detail field ("Failing row contains (...)", "Key (id)=(...)"), which would carry document text into exceptions and logs; the hook builds the `orig` it passes on from the driver's error with only the fields `S`, `V`, `C`, `M`, `s`, `t`, `c`, `d`, `n` (severity, SQLSTATE, message, schema, table, column, data type, constraint), so `str`, `repr` and `args` show no value. Both engines are created with `hide_parameters=True` and `echo` off (`test_engine_hides_bound_parameters_in_errors`; ADR 0019 Decision 18, which batch A did not implement).

### 7.2 The repository's constraint error

`translate_integrity_error(error: sqlalchemy.exc.IntegrityError) -> StoreConstraintViolation` maps a database refusal to `kind` in `check`, `unique`, `foreign_key`, `not_null` (SQLSTATE `23514`, `23505`, `23503` and `23001`, `23502` on Postgres; the message prefix "CHECK constraint failed", "UNIQUE constraint failed", "FOREIGN KEY constraint failed" and "NOT NULL constraint failed" on SQLite), `table` (the table the database names: always on Postgres; on SQLite only for unique and not-null, else `None`) and `constraint` (the name when the database gives it, else `None`). Every repository write runs inside a context that applies it to an `IntegrityError` and raises the result `from None` (`__cause__` is `None`, the context is suppressed) after the rollback. Its message holds the kind, the table and the constraint name and no value (`test_translate_integrity_error_names_the_kind_and_no_value`). The case the tests reach without a race (`test_the_repository_raises_a_typed_error_when_the_database_refuses_a_write`): a rescoring bundle lists a different turn (new ID) at a `(transcript_id, seq)` that a stored turn holds; the models accept it, the unique constraint refuses it: `StoreConstraintViolation(kind="unique", table="turn")` (SQLite: table `turn` or `None`), nothing written, the first bundle still loads.

### 7.3 Typed errors and their parents (`test_error_types_and_their_parents`)

| Type | Direct bases | Raised by | Attributes |
|---|---|---|---|
| `ReimportConflict` | `tracer_evalschema.errors.ConflictingRecordError` (not a `StoreError`) | `store_run_bundle` | `entity_kind`, `stored_id` |
| `RecordOwnershipConflict` | `StoreError` | `store_run_bundle` | `kind`, `record_id` |
| `ProjectNotStored` | `StoreError` | `store_run_bundle` | `project_id` |
| `InternalSnapshotRefused` | `StoreError`, `ValueError` | `store_snapshot`, `store_run_bundle` | `snapshot_id` |
| `RecordNotStored` | `StoreError` | every `load_*` | `kind`, `record_id`, `version` |
| `IncompleteRunBundle` | `StoreError` | `load_run_bundle` | `bundle_id`, `missing` |
| `ExportReferenceNotStored` | `StoreError` | `store_export` | `kind`, `record_id` |
| `StoreConstraintViolation` | `StoreError` | any write | `kind`, `table`, `constraint` |

Existing: `StoreError(Exception)` (batch A), `StoreIntegrityError`, `StoreValueOutOfRange` (batch B). Evalschema errors the repository lets through unwrapped: `ConflictingVersionError` and `ConflictingRecordError` (`BundleError`), `BundleSizeError`, `DigestMismatch`, `pydantic.ValidationError`, `ValueError` (`[purpose]`), `TypeError`. `RecordOwnershipConflict` and `IncompleteRunBundle` are not `ConflictingRecordError`s; `ReimportConflict` is one. Every message above holds the kind and the ID (wire form) and never a field value, a document fragment or an external reference.

## 8. Findings for the implementer (from building the oracle)

* **SQLAlchemy `RowMapping` alias.** On a `select(run.c.id, run.c.doc_sha256)`, `"run_id" in row` is true, because `run_id` is the label of `run.id`. A test for "has an owner column" must look at the table (`"run_id" in table.c`), not at the row. (The oracle's first version raised `KeyError` on the `run` table for this reason.)
* **The hook is not optional on Postgres.** Without it `StoreConstraintViolation` can never be built from a Postgres error, because pg8000 does not raise `IntegrityError`.
* **`insertmanyvalues`.** The oracle inserts with `connection.execute(insert(table), list_of_dicts)` and no `RETURNING`; on SQLite and on pg8000 that is one DBAPI call per table, so the statement-count test passes. A `RETURNING` or a dialect option that pages the rows would make the count depend on the record count.
* **Python 3.11.** The SQLite engine's explicit `BEGIN` event is what makes rollback real; the repository must not open a connection that bypasses it.

## 9. Test inventory

Counts (SQLite side and the tests without a database / `[postgres]` cases): `test_evaldb_definitions.py` 109 / 107, `test_evaldb_run_bundle.py` 109 / 109, `test_evaldb_export.py` 33 / 32, `test_evaldb_reimport.py` 8 / 8, `test_evaldb_triggers.py` 114 / 113, `test_evaldb_error_mapping.py` 16 / 14, `test_evaldb_adr0022.py` 6 / 0: 395 / 383, 778 test cases in 90 test functions. Together with batches A and B: 1044 by default (649 + 395) and 813 postgres-marked (430 + 383).

SCOPE names (all present under their exact names): P2-12a `test_store_definition_idempotent_same_content`, `test_store_definition_conflicting_content_rejected`, `test_store_snapshot_extracts_embedded_definitions`, `test_store_snapshot_conflicting_embedded_definition_rejected`, `test_latest_version_lookup`, `test_snapshot_lookup_by_content_digest`; P2-12b `test_run_bundle_round_trip_order_preserved`, `test_run_bundle_round_trip_byte_identical_json`, `test_run_bundle_withheld_round_trip`, `test_run_bundle_store_is_atomic`, `test_invalid_run_bundle_never_stored`, `test_rescoring_bundle_shares_records_without_duplication`, `test_rescoring_bundle_conflicting_record_rejected`, `test_tdeg_bundles_round_trip_identical_table_set`, `test_not_applicable_results_projected_null_never_zero_or_false`, `test_not_available_cost_projected_without_amount_or_basis`, `test_huge_call_bundle_round_trip`, `test_store_run_bundle_statement_count_independent_of_record_count`, `test_run_bundle_load_reports_incomplete_after_member_deletion`; P2-12c `test_store_export_requires_expected_purpose`, `test_load_export_requires_expected_purpose`, `test_store_export_purpose_mismatch_rejected`, `test_export_round_trip_byte_identical`, `test_export_sealed_payload_ciphertext_unchanged_at_rest`, `test_export_records_not_decomposed_into_internal_tables`, `test_export_size_limit_enforced_before_parse`; P2-13 `test_reimport_finds_existing_run_by_external_ref`, `test_external_ref_unique_per_entity_kind`, `test_external_ref_key_is_hash_of_jcs_tuple`, `test_external_ref_lookup_is_exact`, `test_reimport_conflicting_content_raises_reimport_conflict`, `test_child_provider_call_refs_not_unique`; P2-16 `test_update_rejected_on_every_table`, `test_immutability_triggers_exist_after_migrations`; ADR 0022 `test_adr0022_policy_rules_are_tested`.

Names the ADRs add to the SCOPE rows and this batch carries (the lead adds them to the rows): `test_store_same_run_bundle_twice_is_a_no_op` (P2-12b), `test_record_owned_by_another_run_rejected` (P2-12b), `test_deletion_audit_rows_cannot_be_deleted` (P2-16; ADR 0020 proposed it for the P2-17b row, but it tests the `BEFORE DELETE` trigger, which is P2-16, and needs no deletion API). Left to the next batch (deletion roots): `test_delete_snapshot_refused_while_a_run_references_it` (P2-17a, needs `delete_snapshot`; the `RESTRICT` it rests on is already tested in batch B and in `test_constraint_violations_are_integrity_errors_on_both_backends[restrict]`). `test_adr0021_policy_rules_are_tested` belongs to batch D (its rules cite the migration tests).

Added by qa (each makes a contract item testable): in `test_evaldb_definitions.py`: `test_store_definition_returns_the_rows_it_wrote_for_a_dataset`, `test_store_definition_conflict_is_found_by_document_not_by_digest`, `test_store_definition_other_version_of_the_same_id_is_not_a_conflict`, `test_store_definition_takes_the_nine_versioned_entities_only`, `test_latest_version_arguments`, `test_store_snapshot_reuses_definitions_that_are_already_stored`, `test_store_snapshot_conflicting_snapshot_rejected`, `test_store_snapshot_refuses_absent_or_keyed_profile_digest`, `test_store_project_round_trip_and_conflict`, `test_store_dataset_item_round_trip_and_conflict`, `test_loading_something_that_is_not_stored_is_a_typed_error`, `test_store_functions_revalidate_instances_built_without_validation`, `test_repository_functions_and_signatures`, `test_repository_functions_require_an_engine`, `test_id_arguments_are_typed_ids_or_uuids`, `test_result_types_are_frozen_dataclasses`; in `test_evaldb_run_bundle.py`: `test_run_bundle_stores_its_embedded_definitions_catalogs_and_profiles`, `test_two_runs_share_one_snapshot_and_its_definitions`, `test_run_bundle_store_is_atomic_after_the_turns_of_a_large_bundle`, `test_store_run_bundle_takes_a_run_bundle_only`, `test_store_run_bundle_refuses_a_bundle_whose_project_is_not_stored`, `test_store_run_bundle_refuses_absent_or_keyed_profile_digest`, `test_store_run_bundle_refuses_an_integer_it_cannot_store_before_any_write`, `test_same_bundle_id_with_other_members_is_a_conflict`, `test_run_bundle_conflicting_pricing_catalog_rejected`, `test_run_bundle_load_reports_a_missing_verdict_and_a_missing_transcript`, `test_load_run_bundle_of_a_deleted_run_or_unknown_id_is_not_stored`, `test_n18_changed_language_declaration_is_a_new_transcript_in_a_new_bundle`; in `test_evaldb_export.py`: `test_export_doc_is_canonical_whatever_the_input_form`, `test_export_round_trip_of_a_stored_run_bundle_with_a_sealed_recording`, `test_store_export_validates_its_input`, `test_store_export_requires_stored_runs_items_and_profiles`, `test_store_export_requires_the_profile_a_sealed_payload_names`, `test_store_export_is_idempotent_and_a_changed_export_is_a_conflict`, `test_load_export_not_stored`, `test_store_export_locks_the_rows_it_links`, `test_golden_exports_are_all_covered`; in `test_evaldb_reimport.py`: `test_find_by_external_ref_arguments`, `test_reimport_conflict_is_a_conflicting_record_error_checked_first`; in `test_evaldb_triggers.py`: `test_trigger_names_are_pinned`, `test_update_is_rejected_for_a_row_selected_by_key`, `test_delete_is_allowed_on_every_table_except_deletion_audit`, `test_update_and_delete_of_a_missing_row_are_not_errors`; in `test_evaldb_error_mapping.py`: `test_sqlstate_helpers`, `test_constraint_violations_are_integrity_errors_on_both_backends`, `test_translated_integrity_errors_carry_no_row_content`, `test_errors_of_other_classes_are_not_integrity_errors`, `test_engine_hides_bound_parameters_in_errors`, `test_translate_integrity_error_names_the_kind_and_no_value`, `test_the_repository_raises_a_typed_error_when_the_database_refuses_a_write`, `test_error_types_and_their_parents`; in `test_evaldb_adr0022.py`: `test_every_test_named_by_the_scope_rows_of_batch_c_exists`.

`test_adr0022_policy_rules_are_tested` reads ADR 0022: each of the 13 numbered rules of "## Decision" and each of the six invariants cites at least one test that exists (an AST scan of `packages/evaldb/tests/test_*.py`); a cited name that exists nowhere must be on the explicit `PENDING` set (today only `test_store_errors_hide_input`, P2-24, batch F). It also requires the three added names above.

## 10. Evidence (`docs/phase2/evidence/red/batch-c/`)

Logs: `red.py3.11.log` to `red.py3.14.log` (the seven new files against the committed tree), `red-postgres15.py3.11.log`, `red-postgres18.py3.14.log`, `oracle-sqlite.py3.11.log` to `.py3.15.log`, `oracle-postgres15.py3.11.log`, `.py3.14.log`, `oracle-postgres18.py3.11.log`, `.py3.14.log` (the whole directory against the scratch oracle), `oracle-src.patch` (the oracle: `repository.py`, `triggers.py`, and edits of `errors.py`, `engine.py`, `__init__.py`; it applies to HEAD; qa's reference, the dev owns the real code and may differ) and `mutants.txt`.

Counts. Red on the committed tree: 395 selected by default, 344 failed and 51 passed, on each of 3.11, 3.12, 3.13 and 3.14; Postgres 15.19 (3.11) and 18.6 (3.14): 383 selected, 345 failed and 38 passed. The passes are guards that hold on a tree without the repository (the ADR and SCOPE name tests, the delete-allowed guards, and on SQLite the constraint errors that the driver already raises as `IntegrityError`). The failures are `AttributeError` for the missing repository names (the large majority), `ModuleNotFoundError` for `tracer_evaldb.repository` and `tracer_evaldb.triggers`, `DID NOT RAISE IntegrityError` for the missing triggers (75) and the parameter echo of an engine without `hide_parameters` (4). Oracle: 1044 passed and 813 deselected on SQLite on each of 3.11, 3.12, 3.13, 3.14 and 3.15 (649 of batches A and B plus 395); 813 passed and 1044 deselected on Postgres 15.19 and 18.6, each on 3.11 and 3.14 (430 plus 383). Mutants: 95, 91 caught; the four survivors are equivalent or unpinned and explained in `mutants.txt`.

## 11. Ambiguities, with recommendations (the lead auto-approves; qa implemented the recommendation in the tests)

A1. **Home and shape of the API.** ADR 0022 names `store_*`, `load_*`, `latest_version` and a snapshot lookup without signatures; SCOPE names no module. Recommendation: `tracer_evaldb.repository`, `Engine` first, one `engine.begin()` per call, no `Connection` argument (section 2.1), results as in 2.2. Rejected: a `Session` or a repository class holding the engine (state with no gain), accepting a `Connection` (two transaction boundaries to test; a later phase can add it).

A2. **Read functions the SCOPE does not name.** The round trips of definitions, snapshots, projects and items need a reader: `load_definition`, `load_snapshot`, `load_project`, `load_dataset_item`. Recommendation: add them (pinned in 2.2); they decode through the batch B decoders.

A3. **Ownership before content.** ADR 0022 Decision 5 states both rules without an order. Recommendation: ownership first (a record that belongs to another run is refused without comparing its content; the content of another run's records is not touched). Rejected: content first (the answer would depend on whether two runs happen to hold equal bytes).

A4. **`ReimportConflict` is not a `StoreError`.** It subclasses `ConflictingRecordError` as ADR 0022 says. Recommendation: that is all (a caller that catches the evalschema conflict class catches it). Rejected: a second base `StoreError` (two unrelated roots in one class for no use). The other typed errors derive from `StoreError` and are not conflicts of content.

A5. **Golden maximal snapshots are not storable as internal data.** `ConfigSnapshot/maximal.json` and `maximal-keyed-catalog.json` carry a test-profile reference with no digest, so `check_internal_snapshot` refuses them. Recommendation: keep the rule (ADR 0019 Ambiguity 10), note it in STORAGE_POLICY; the tests add a plain digest, which is outside the snapshot digest domain.

A6. **Re-storing a bundle whose member was deleted.** ADR 0022 Decision 5 says an identical bundle is a no-op, and the records are inserted when absent; after a deletion both can apply. Unpinned (no test). Recommendation: the absent records are inserted again (rule 5 per record), the bundle becomes complete; a re-import after a deletion is a deliberate act and the audit holds no ID to refuse it by. The owner may want to decide it differently (a data-subject deletion that a re-import undoes).

A7. **The trigger error class.** ADR 0020 Decision 7 says only "raises". Recommendation: `IntegrityError` on both backends; the Postgres functions raise SQLSTATE `23001` (restrict_violation, class 23, so the hook of section 7.1 makes it one) and the SQLite triggers `RAISE(ABORT, ...)`. Rejected: the default `P0001`, which Postgres users would see as a `ProgrammingError` and SQLite users as an `IntegrityError`.

A8. **RESTRICT is 23503 on Postgres 15 and 23001 on 18.** Batch B's A12 recorded 23001 for 15.19 and 18.6; the oracle shows 15.19 reports `23503` ("violates foreign key constraint") and 18.6 `23001` ("violates RESTRICT setting"). The hook covers the whole class, so nothing changes; the tests accept both. Recommendation: correct the sentence of A12 and ADR 0019 Decision 18 ("which pg8000 reports for ON DELETE RESTRICT" is true on 18 only).

A9. **The detail of a Postgres integrity error quotes the failing row.** Not in the ADRs as a rule, but ADR 0019 Decision 18 requires that errors never hold a document fragment. Recommendation: the hook drops the detail, hint and context (section 7.1); pinned by `test_translated_integrity_errors_carry_no_row_content`. Same for `hide_parameters=True`, which Decision 18 requires and batch A left out (a one-line change in `create_store_engine`; pinned by `test_engine_hides_bound_parameters_in_errors`).

A10. **`test_immutability_triggers_exist_after_migrations` in batch C.** There are no migrations yet. Recommendation: the DDL is attached to `metadata` so `create_all` builds the triggers (section 6); the test reads the catalogs and is re-run by batch D on the Alembic schema (a second schema source, not a second test body). `test_deletion_audit_rows_cannot_be_deleted` is placed here (A11).

A11. **Where the audit-trigger test lives.** ADR 0020 Ambiguity 6 proposed it for the P2-17b row. It tests a trigger only. Recommendation: P2-16 (this batch). `test_delete_snapshot_refused_while_a_run_references_it` needs a deletion root and goes with batch E (the lead's "next batch" for deletion).

A12. **`expected_purpose=None`.** `loader.load_bundle` reads `None` as "any purpose". Recommendation: the store has no such escape hatch: `None` is a `TypeError` at both entry points (SCHEMA_POLICY section 11: required, no default).

A13. **How the 256 MiB limit is tested.** A `max_bytes` parameter would be an API the ADR does not have. Recommendation: no parameter; the tests pass `bytes(256 MiB + 1)` (zero-filled by `calloc`, never read) to `store_export`, and rows with a large `byte_length` and a garbage `doc` to `load_export` (the boundary: exactly 256 MiB is read).

A14. **"Identical table set" for the T-DEG bundles.** Taken literally the eight bundles cannot fill the same tables (T0 has no tool calls or spans). Recommendation: the reading of section 3.1: the tables filled are a function of the bundle's own member lists alone, T1 and T2 agree, T0 and T0m agree, and the difference between T1 and T0 is exactly the tables of the evidence T0 lacks.

A15. **Concurrent writers of the same record.** Two transactions that store the same new record at once: the loser meets the primary key and gets `StoreConstraintViolation(kind="unique")` instead of a clean no-op. ADR 0022 is silent. Unpinned (no test). Recommendation: accept in batch C and list it as a risk for Phase 5 (staging); a retry that re-reads is the remedy if it matters earlier.

A16. **Isolation of `load_run_bundle`.** The load reads the member rows and then the records; on Postgres under READ COMMITTED a deletion between the two statements could produce a member that vanished after being listed. Unpinned (no test). Recommendation: one read transaction at REPEATABLE READ on Postgres (SQLite: the single read transaction). Cheap, and the incomplete-bundle report then describes one moment.

A17. **Bare UUIDs.** Identifier arguments accept a `uuid.UUID` for convenience (importers hold bare UUIDs from `from_external`); a non-v7 UUID is a `ValueError`. Recommendation: as pinned.

A18. **Statement count bound.** Section 3.2 pins the invariant up to 10,000 records per kind. Recommendation: as written; above SQLite's variable limit (32,766) an `IN` list must be chunked, which adds statements and is not tested.

A19. **The T-DRIFT hazard.** The Phase 1 T-DRIFT scan reads every committable text file (batch A ambiguity 14). This document and the new tests were written without the tokens it bans; the lead re-runs the Phase 1 suite after committing them.
