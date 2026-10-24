# Phase 2 batch G contract: fixes decided after the P2-24 security audit (E5, E6, E10, E14, E16, E17)

Author: qa, 2026-10-24. Source: `docs/security/P2-24-audit.md` (findings E5, E6, E10, E13, E14, E16, E17), the decision log entry "P2-24 SECURITY AUDIT DELIVERED" (auto-approved lead decisions 1, 2, 4, 6 and 8), the implemented `packages/evaldb/src/tracer_evaldb` at HEAD b50c3cc plus the uncommitted batch F work. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or HANDOFF, and no earlier test (the one change an earlier test needs is a NAMED SPEC CHANGE, section 7, delivered as a patch, not applied). Every statement below is pinned by a test named in section 8, or is marked (unpinned).

## 0. Where the tests are and how to run them

| File | Holds |
|---|---|
| `packages/evaldb/tests/test_evaldb_audit_fixes.py` | the 29 test functions of section 8 |
| `packages/evaldb/tests/test_evaldb_repair_members.py` | section 10 (P1 privacy: A6 revoked), 13 test functions, 61 cases |
| `packages/evaldb/tests/evaldb_trigger_pin.py` | helper, no test: serialization and hashing of the trigger statements; `python evaldb_trigger_pin.py` prints the current values, `--write` rewrites the data file |
| `packages/evaldb/tests/fixtures/trigger_ddl_sha256.json` | the committed hashes (E17), written from the current `triggers.py` |

Commands are those of batches A to F. The SQLite side and the tests without a database run in the default selection; the Postgres side with `TRACER_TEST_POSTGRES_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests/test_evaldb_audit_fixes.py`. The `db` fixture of batch B (`metadata.create_all`, one schema per Postgres session) is used; no new environment variable, no change to `conftest.py`, the root `pyproject.toml` or CI.

Counts of test_evaldb_audit_fixes.py: 29 test functions, 176 cases: 103 in the default selection (SQLite and no database) and 73 `postgres`-marked.

## 1. Names (pinned)

```
tracer_evaldb.errors.UnsafeEngine        class UnsafeEngine(StoreConfigurationError)
tracer_evaldb.UnsafeEngine               re-exported; in tracer_evaldb.__all__ and tracer_evaldb.errors.__all__
```

Nothing else is added to the public API. The actor rule and the file mode are behaviour, not names.

## 2. E5 and E6: no failing-row detail and no bound value in an exception chain

A. An `sqlalchemy.exc.IntegrityError` that leaves an Engine of `create_store_engine` untranslated (a caller's own statement) on SQLite and on Postgres, for a unique, not-null, foreign key, check, RESTRICT and the two immutability-trigger refusals (UPDATE of any table, DELETE of `deletion_audit`):

* the formatted traceback (`traceback.format_exception`, which prints every `__cause__` and `__context__` link), `str`, `repr` and `args` of every link, the attributes of every link, hold none of: the document text, a text column value, an ID of the failing row, the text "Failing row" (any case). Links are everything reachable through `__cause__`, `__context__` (the `__suppress_context__` flag is ignored on purpose: a handler may walk the attribute) and `orig`.
* the attribute `params` of every link that has one holds none of the bound values (text, IDs). `None`, `()` and an empty mapping all satisfy this.
* `error.statement` is still the statement text (a `str`), `error.orig` is still the driver error and is a link of the chain.
* every driver error in the chain whose first argument is a dict (Postgres) has the fields of the existing scrub rule only (`S V C M s t c d n`) and still has `C` and `M`. On Postgres that includes the pg8000 `DatabaseError` that `pg8000.dbapi` raised its `ProgrammingError` while handling (it is the `__context__` of `orig`, found by the oracle run: the audit named the `__cause__` only). On SQLite the driver error is the native `sqlite3.IntegrityError` with string arguments.

B. `tracer_evaldb.repository.translate_integrity_error(error)` still maps the five kinds, and the `StoreConstraintViolation` it returns leaks nothing through its chain or `params`. The repository's `StoreConstraintViolation` for the turn unique refusal (batch C scenario) holds, for a reader that walks `__context__` ignoring the suppress flag, no turn text, no turn ID and no run bundle ID, in any link, in `params` too.

C. How the dev gets there is not pinned. The oracle: the Postgres hook overwrites `args` of every link of `original` (walking `__cause__`/`__context__`) with the scrubbed fields and builds the new `IntegrityError` with `params=None`; SQLite gets a `handle_error` hook that rebuilds an `sqlite3.IntegrityError` as `IntegrityError(statement, None, original, hide_parameters=True)`. Clearing `__context__` also satisfies the tests, and so would any scrub of `params`.

D. Out of scope here (unpinned): `params` of errors that are not `IntegrityError` (OperationalError, ProgrammingError of class 22, StatementError), E7.

## 3. E14: `deletion_audit.actor`

The actor of every `delete_*` function must match, in full, `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` (`re.fullmatch`; so "alice\n" is refused), in addition to the existing checks (type `str`, non-empty, label rule of `_check_label`, `credential_reason`). Refusal is the existing error: `ValueError` whose message contains the word `actor`, raised before any statement, `__cause__` None, the value (and any 8-character window of it) not in `str`, `repr` or `args` of the error; nothing is written (`raw_dump` equal, `deletion_audit` empty). The message text beyond the word `actor` is not pinned.

Refused (each on all 7 `delete_*` functions, that is the 14 root kinds of `ROOTS`; see the correction note below): e-mail addresses, the empty string, 129 and 201 characters, a leading `-`, `.`, `_` or `:`, a space (so "Priya Nair"), non-ASCII (accented, Devanagari, NFD), a ZWNJ or ZWJ, a full-width letter, an Arabic-Indic digit, a trailing newline or carriage return, and `/ # ( ) + = ~`. Still refused by the credential check although they match the label rule (so the two checks are both needed): six credential shapes assembled from parts. Accepted and stored byte for byte: `ops-night-shift`, `svc.tracer-ci`, `operator:alice-1`, `service_account:tracer-ci`, one character, 128 characters, `Z` plus 127 hyphens, and the others in `LABELS_ACCEPTED`. Not pinned: a database CHECK on the column (the rule is an API rule; a CHECK would need a revision 0003).

Correction (2026-10-25, recorded at the Phase 2 gate P2-G, `bee9d97`; BACKLOG BL-202): this section first said "all 8 functions". `tracer_evaldb.deletion` has seven deletion functions (`delete_run`, `delete_project`, `delete_test_profile`, `delete_definition`, `delete_dataset_item`, `delete_export`, `delete_snapshot`); `delete_definition` covers eight kinds, so the tests run over 14 root kinds. The tests were always parametrized over the 14 root kinds of the seven functions; only the count in the text was wrong. Section 5 already says "the seven `delete_*`".

## 4. E16: the mode of a new SQLite file

* `create_store_engine("sqlite:///<path>")` (also `sa.URL.create("sqlite", database=...)`, a relative path, a directory name with a space) for a path that does not exist creates the file with mode `0o600` (`stat.S_IMODE(os.stat(path).st_mode)`), under process umasks `0o000`, `0o002`, `0o022` and `0o077`. The file exists and has that mode when `create_store_engine` returns, and still has it after `metadata.create_all` and a `store_project`.
* An existing file keeps its mode, empty or a database with data (`0o644 0o640 0o664 0o666 0o600 0o620`), including a file the package made and the owner then changed.
* `sqlite://` and `sqlite:///:memory:` make no file (the working directory stays empty) and the engine works.
* A path in a missing directory is still `StoreConnectionError` with no path in the traceback, and nothing is created.
* POSIX only. The repository CI is Linux and macOS; there is no Windows skip because nothing in CI is Windows. A `-wal`, `-shm` or `-journal` file is not pinned (SQLite gives them the mode of the database file).
* Not pinned: how the file is created (the oracle opens it with `os.open(O_CREAT | O_EXCL, 0o600)` before SQLite connects, which has no window of a wider mode; a `chmod` after the first connect also passes the tests).

## 5. E10: the engine guard

`UnsafeEngine(StoreConfigurationError)`; the message is fixed, names the rule (`hide_parameters=True`) and holds nothing of the URL (no path, user, password, host, database name; checked on the formatted traceback). It is raised, before any statement and before any connection is checked out (`checkout` event count 0), by every public function of `tracer_evaldb` whose first parameter is `engine`, except `open_store`: the seven `store_*`, `load_*`, `latest_version`, `find_snapshot_by_digest`, `find_by_external_ref`, the seven `delete_*` and `verify_database` (23 functions; `test_the_engine_guard_test_covers_every_public_function_that_takes_an_engine` fails when a new one appears). An `Engine` from `sa.create_engine(url)` has `hide_parameters` false and is refused. An Engine from `create_store_engine` and an engine derived from it with `execution_options(...)` (`OptionEngine`, which has `hide_parameters` true) are accepted. A `Connection` or any other object is still `TypeError` (unchanged, and still the first check).

## 6. E17: the trigger DDL pin

`tests/fixtures/trigger_ddl_sha256.json` holds, for the 37 tables of revision 0001's `TABLES` (listed in the file and compared with the module), five SHA-256 values: `sqlite.create`, `sqlite.drop`, `postgresql.functions`, `postgresql.create`, `postgresql.drop`. Serialization (also written in the file): each statement as UTF-8 followed by one NUL byte, in the order revision 0001 runs them, SHA-256, lower-case hex. A change of one character in `triggers.create_statements`, `function_statements` or `drop_statements` fails `test_trigger_ddl_text_matches_the_committed_hashes` and prints the current values; the update procedure (a new revision and a decision-log entry first; then `--write`) is in the docstring of the test module. `drop_statements` is pinned although the audit named only create and functions: the data migrations (`migrations/helpers.py`) call it and share the risk (ambiguity A5). The pin is green on the committed tree by design (a guard, not a red test); the mutants of the evidence prove it fails.

Shipped revision files (note added 2026-10-25 at the Phase 2 gate P2-G, minor m1; BACKLOG BL-066). Shipped revision files are pinned by SHA-256 in `tests/fixtures/revision_files_sha256.json` (test `test_shipped_revision_files_are_pinned_by_sha256`). An existing pin never changes. A fix to a shipped revision is a new revision. The pin of a new revision is added with `evaldb_revision_pin.py --write`.

## 7. NAMED SPEC CHANGE: four earlier tests accept actors the new rule refuses

`docs/phase2/evidence/red/batch-g/spec-change-actor-labels.patch` (applies with `git apply`; not applied; to be applied by dev or staff-reviewer together with the actor rule) changes `packages/evaldb/tests/test_evaldb_deletion_audit.py` only:

* `test_deletion_audit_counts_per_kind_exact` (14 cases): actor "Priya Nair" -> "priya-nair".
* `test_each_deletion_adds_one_row_and_never_changes_an_earlier_one`: "First Person" / "Second Person" -> "first.person" / "second.person".
* `test_deletion_accepts_each_reason_and_stores_labels_exactly`: its list had names with spaces, Devanagari and Malayalam with ZWNJ/ZWJ, NFC/NFD text, 500 characters, an e-mail address and punctuation; the batch D contract said the actor follows the HumanLabel rules. E14 supersedes that, so the list becomes machine labels and the NFC/NFD assertion goes (the refusals of those shapes are in the batch G tests). The `unicodedata` import goes with it.
* `test_ordinary_labels_that_resemble_nothing_secret_are_accepted`: "Priya R. (QA)" -> "priya.r-qa".

Removed assertion (recorded 2026-10-25 at the Phase 2 gate P2-G, minor m4; BACKLOG BL-204). The patch removes exactly one assertion, in `test_deletion_accepts_each_reason_and_stores_labels_exactly`:

```
assert unicodedata.normalize("NFC", actors[3]) != actors[4]
```

It was a precondition on the old fixture list, not a check of the store: it proved that the fourth label (a composed accented word) and the fifth (the same word decomposed) were different strings, so that "stored exactly" would catch a store that normalizes labels. Under the E14 rule both inputs are refused (non-ASCII), so the assertion has no subject any more; for the ASCII labels that are now accepted, the composed and decomposed forms are the same string. The refusal of those inputs is pinned in `test_evaldb_audit_fixes.py` by `test_actor_outside_the_label_rule_is_refused_by_every_deletion` (the NFD, composed accented, Devanagari, ZWJ and ZWNJ entries of `PATTERN_REJECTED`), and exact storage of accepted labels by `test_actor_labels_that_follow_the_rule_are_accepted_and_stored_exactly`. Nothing that the store guarantees lost a test. The `unicodedata` import went with the assertion.

With the patch applied to the oracle, `test_evaldb_deletion_audit.py` and `test_evaldb_deletion.py` pass (155 cases, SQLite). Without it, 17 cases of the oracle run fail for exactly that reason (observed). The documents that describe the actor (`docs/phase2/api-surface-batch-d.md`, the ADR 0020 Decision 6 text, STORAGE_POLICY drafts) need the same sentence; that is the architect's change.

## 8. Test inventory

| Test | Finding | Default / postgres cases | Red on the committed tree |
|---|---|---|---|
| `test_the_leak_detector_sees_a_planted_value` | all | 1 / 0 | no (self-test of the detector) |
| `test_untranslated_integrity_error_chain_text_holds_no_row_detail` | E5 | 7 / 7 | Postgres: 5 of 7 (the two trigger refusals carry no row); SQLite already clean |
| `test_untranslated_integrity_error_params_hold_no_bound_value` | E6 | 7 / 7 | all 14 |
| `test_driver_error_args_along_the_chain_are_scrubbed` | E5 | 7 / 7 | Postgres: all 7; SQLite clean |
| `test_translated_store_error_chain_holds_no_value_even_ignoring_suppress` | E5, E6 | 7 / 7 | no (guard: `translate_integrity_error` called outside an `except` block has no context; the raised path is the next test) |
| `test_repository_store_error_chain_holds_no_stored_text_or_id` | E6 | 1 / 1 | both |
| `test_actor_fixtures_match_the_stated_rule` | E14 | 1 / 0 | no (self-test of the fixture lists) |
| `test_actor_outside_the_label_rule_is_refused_by_every_deletion` | E14 | 14 / 14 | all 28 |
| `test_actor_credential_check_still_applies_to_values_that_match_the_label_rule` | E14 | 12 / 12 | no (regression guard of the existing check) |
| `test_actor_labels_that_follow_the_rule_are_accepted_and_stored_exactly` | E14 | 1 / 1 | no (guard against an over-strict rule) |
| `test_a_service_account_label_is_accepted_by_every_deletion` | E14 | 14 / 14 | no (guard) |
| `test_a_new_sqlite_file_is_created_with_mode_0600` | E16 | 4 / 0 | 3 of 4 (umask 0o077 already gives 0600) |
| `test_a_new_sqlite_file_in_a_directory_with_a_space_and_by_url_object` | E16 | 1 / 0 | yes |
| `test_a_new_sqlite_file_by_relative_path_has_mode_0600` | E16 | 1 / 0 | yes |
| `test_an_existing_empty_file_keeps_its_mode` | E16 | 6 / 0 | no (guard) |
| `test_an_existing_database_keeps_its_mode_and_its_data` | E16 | 1 / 0 | no (guard) |
| `test_reopening_a_file_the_package_made_does_not_change_a_mode_the_owner_set` | E16 | 1 / 0 | yes (the first assertion) |
| `test_in_memory_databases_make_no_file` | E16 | 2 / 0 | no (guard) |
| `test_a_missing_directory_still_fails_as_before_and_makes_nothing` | E16 | 1 / 0 | no (guard) |
| `test_the_engine_guard_test_covers_every_public_function_that_takes_an_engine` | E10 | 1 / 0 | no (inventory) |
| `test_unsafe_engine_error_type` | E10 | 1 / 0 | yes |
| `test_store_functions_refuse_an_engine_that_does_not_hide_parameters` | E10 | 1 / 1 | yes |
| `test_store_functions_accept_the_engine_of_create_store_engine` | E10 | 1 / 1 | no (guard) |
| `test_a_non_engine_is_still_a_type_error` | E10 | 1 / 1 | no (guard) |
| `test_trigger_ddl_text_matches_the_committed_hashes` | E17 | 1 / 0 | no (pin; fails on a change) |
| `test_the_pin_file_has_the_documented_shape_and_the_tables_of_revision_0001` | E17 | 1 / 0 | no |
| `test_the_trigger_statements_are_what_the_pin_serialization_says` | E17 | 1 / 0 | no |
| `test_the_pin_detects_a_one_character_change_in_each_group` | E17 | 5 / 0 | no (self-test) |
| `test_the_pin_covers_each_dialect_and_every_table_of_revision_0001` | E17 | 1 / 0 | no |

(The red column is as observed; the exact counts per Python version and database are in `docs/phase2/evidence/red/batch-g/`.)

## 9. Ambiguities, with recommendations (the lead auto-approves)

* **A1. The typed error keeps the SQLAlchemy error as `__context__`.** `raise StoreConstraintViolation from None` hides it from the traceback only. The tests accept either a scrubbed context (the oracle) or `__context__ = None`. Recommendation: scrub at the source (the hook) as the oracle does, and additionally set `__context__ = None` on the typed errors in `_writing`, so that no SQLAlchemy object hangs on a typed error at all.
* **A2. Message of the actor refusal.** The tests pin the word `actor` and no echo. Recommendation: `"actor must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}"` (the rule text is not a secret), kept apart from the credential message.
* **A3. Engine provenance.** Decision 6 says "hide_parameters"; the audit's E10 also proposed marking engines in `engine.info`. The tests accept an engine from `sa.create_engine(url, hide_parameters=True)` neither way (unpinned). Recommendation: only `hide_parameters`, as decided; do not add a marker (a marker would refuse the OptionEngine copies that `verify_database` itself makes).
* **A4. `open_store`, `migrate.upgrade/downgrade/current_revision`.** They have the same `isinstance(Engine)` check. The tests pin the guard for the repository, deletion and verify functions only (the lead's list) and exclude `open_store` explicitly in the inventory test. Recommendation: add the guard to `open_store` too (it opens the store for use); leave the migration functions alone: migrations run DDL, and several earlier migration tests build plain engines. Decide before dev starts, because an `open_store` guard makes the inventory test fail until `ENGINE_GUARD_NOT_COVERED` loses `open_store` (a one-line, qa-owned change).
* **A5. `drop_statements` is in the pin.** Pinned because `migrations/helpers.py` rewrites rows between a drop and a re-create of the triggers. Recommendation: keep; if the lead prefers only create and functions, delete the two `*.drop` keys and `GROUPS` entries (helper and file).
* **A6. Hash-file location and format.** `tests/fixtures/trigger_ddl_sha256.json` (the sdist already ships `tests/fixtures/`). Recommendation: keep; the data is JSON (no SPDX header, like `indic_storage.json`).
* **A7. Syntax-level mutation of `triggers.py` does not touch databases already migrated.** The pin protects new databases only; real freezing of 0001 is finding P2-F6 (needs a qa-approved change of the 0001-unchanged guard). Nothing to decide now.
* **A8. SQLite file modes of `file:` URIs and `?mode=` query strings.** Not pinned (`create_store_engine` accepts `sqlite:///path` only in tests). Recommendation: apply the 0600 rule when the path can be determined from `URL.database`, and skip `file:` URIs as the oracle does.
* **A9. The credential check order.** Pattern first or credential first is not pinned; both orders refuse. The hash of an E-mail-shaped credential is not an issue (both refuse, neither echoes).
* **A10. umask handling in tests.** The mode tests set the process umask with `os.umask` and restore it in a `finally`. They are not safe under threads (pytest-xdist uses processes, so they are safe there). Recommendation: keep.
* **A11. E13** (the immutability refusal is kind `foreign_key` on Postgres and `other` on SQLite): stays as a documented low finding, per decision 7; no test pins or changes it. The kind of the two trigger refusals is deliberately not asserted by the batch G tests except that `translate_integrity_error` returns a `StoreConstraintViolation`.

## 10. P1 privacy: `store_run_bundle` never brings back a deleted member (batch C ambiguity A6 revoked)

Source: the lead's decision of 2026-10-24 (decision log entry "DECISION REVERSAL (BL-134...)"). Tests: `packages/evaldb/tests/test_evaldb_repair_members.py` (the function count is in section 0; 31 default and 30 `postgres` cases).

Signature (pinned): `store_run_bundle(engine, bundle, *, repair_members: bool = False) -> StoreResult`. `repair_members` is keyword-only, defaults to `False`, and anything that is not exactly a `bool` (`1`, `0`, `None`, `"yes"`, `""`, `[]`, `(True,)`, `1.0`, an object) is a `TypeError` raised before any statement, with nothing written; a positional third argument is a `TypeError`. No other store function gets the parameter.

Rule. A member is a record the run depends on that `delete_test_profile` or `delete_definition` can remove: a test profile, a pricing catalog, and the five definitions embedded in the snapshot (persona, rubric, evaluator, metric definition, metric binding). When the run row of the arriving bundle is stored:

* with `repair_members=False` (the default) no absent member is inserted. The same bundle stored again is a no-op for it: `created` is `False`, `inserted` is `{}`, no INSERT, UPDATE or DELETE is sent, and `raw_dump` of the database is equal before and after. The deleted record stays absent; for a test profile no string of the profile (name, date of birth, account number) is in any row of any table (the personal-data case).
* `load_run_bundle` keeps raising `IncompleteRunBundle` for a missing profile or catalog (`missing` names the kind); a deleted definition is embedded in the snapshot document, so the bundle still loads whole and the row stays absent. `verify_database` keeps the informational note `bundle_member_absent` (location `test_profile`) and no finding.
* a different bundle of the same stored run (a rescoring: new results and verdict) is stored (`created` True, its new rows inserted) and does not bring the deleted profile back.
* a bundle stored twice with nothing deleted is a no-op as before, with or without `repair_members`.
* with `repair_members=True` the absent members the stored run lists are inserted: `inserted` is exactly `{<table>: 1}` for one deleted member (or one entry per deleted member), `created` stays `False` for an already stored bundle, `load_run_bundle(...).bundle` equals the bundle, `verify_database` has no finding and no note, and no `deletion_audit` row is written (audit rows are content-free; the repair is an explicit data-restoring act, to be documented as such in STORAGE_POLICY). On a store that lost nothing it changes nothing.
* a run that is not stored is a new import, as before and with either value: after `delete_run` the same bundle is inserted whole, a profile deleted earlier included, `created` True.

Oracle: after `_settle`, if the run is stored and `repair_members` is false, drop from the rows to insert every record of the member tables that a stored `run_bundle_member` (kinds `pricing_catalog`, `test_profile`) of this run, or the stored snapshot's `snapshot_member` / `snapshot_profile_ref` rows, list. Listing is what tells a deleted record from a new one: the audit holds no ID.

Named spec change (not applied): `docs/phase2/evidence/red/batch-g/spec-change-a6-revoked.patch`. No earlier test pinned the old behaviour (A6 was unpinned; the oracle run of the whole suite confirms it); the only earlier test that fails is `test_repository_functions_and_signatures` in `test_evaldb_definitions.py`, which pins the exact parameter list of `store_run_bundle`: the patch adds `("repair_members", "KEYWORD_ONLY")` and the default `False`, weakening nothing. The patch also replaces the A6 paragraph of `api-surface-batch-c.md`.

Ambiguities, with recommendations:

* **B1. `store_snapshot` and `store_definition` called directly.** They insert a record the caller names, including a deleted one; the rule above is about `store_run_bundle`. A snapshot stored by itself that embeds a deleted persona brings the persona back. Recommendation: leave (the call names the record, and a snapshot is not tied to a stored run); document it next to `repair_members` in STORAGE_POLICY. Unpinned.
* **B2. A member that is new to a stored run.** A rescoring bundle may list a catalog or profile that no stored bundle or snapshot of the run lists; it is inserted (it is new, not deleted). Pinned only through the rescoring test, whose members are all listed. Unpinned beyond that.
* **B3. Records of fact deleted by `delete_run`.** Out of scope: `delete_run` removes the run row, so the next store is a new import.
* **B4. Partial repair.** `repair_members=True` repairs all absent listed members at once; there is no per-kind choice. Recommendation: keep it one flag.
* **B5. Export bundles.** A deleted export bundle is not a member; storing it is `store_export`. Unchanged.
