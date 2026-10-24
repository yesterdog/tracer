# Phase 2 batch B (P2-06 to P2-11b): red-first evidence

Date: 2026-10-24. Tests: `packages/evaldb/tests/test_evaldb_tables.py`, `test_evaldb_codecs.py`, `test_evaldb_bigint.py`, helper `evaldb_support.py`. Contract: `docs/phase2/api-surface-batch-b.md`.

## Counts

| Run | Tree | Selection | Result |
|---|---|---|---|
| `red.py3.11.log` ... `red.py3.14.log` | the repository (batch A code, no `tables.py`, `codecs.py`, `BigIntCol`, `StoreIntegrityError`) | the three batch B files, default selection (SQLite side and the tests without a database) | 605 selected: 104 failed, 501 errors, 0 passed, 0 skipped, on each of 3.11, 3.12, 3.13 and 3.14; 414 deselected |
| `red-postgres15.py3.11.log`, `red-postgres18.py3.14.log` | the same, with a throwaway Postgres 15.19 and 18.6 | the three files, `-m postgres` | 414 selected: 15 failed, 399 errors, 0 passed |
| `oracle-sqlite.py3.11.log` ... `.py3.15.log` | scratch copy with the oracle (`oracle-src.patch`) | `packages/evaldb/tests` (batch A and B), default selection | 649 passed (44 of batch A, 605 of batch B), 430 deselected, on each of 3.11, 3.12, 3.13, 3.14 and 3.15 |
| `oracle-postgres15.py3.11.log`, `.py3.14.log`, `oracle-postgres18.py3.11.log`, `.py3.14.log` | the same, Postgres 15.19 and 18.6 | `-m postgres` | 430 passed each (16 of batch A, 414 of batch B), 649 deselected |
| `mutants.txt` | oracle with one edit each | whole directory, SQLite side on 3.14 (four Postgres-only mutants on Postgres 18.6) | 116 mutants, 115 caught, 1 equivalent survivor (CM24, explained in the file) |

1019 test cases in 75 test functions; 605 by default, 414 postgres-marked. The repository's existing suite (`packages/evalschema/tests` and the batch A tests, with the three new files ignored) was run unchanged on the shared environment: 8832 passed, 22 deselected, in 174 s.

## Why each red test fails for the intended reason

`pytest --junitxml` on 3.13 groups the 605 failures by message: 500 setup errors `ModuleNotFoundError: No module named 'tracer_evaldb.codecs'` (every codec test), 1 setup error for `tracer_evaldb.tables`, 75 `KeyError: '<table>'` (the table is not in `metadata.tables`), 17 `AttributeError: module 'tracer_evaldb' has no attribute 'BigIntCol'`, and 12 assertion failures that state the missing structure (`assert 0 == 37` tables, `assert 0 == 23` foreign keys, an empty set against the ADR table, `sqlalchemy.exc.NoSuchTableError: agent_target`). No test passes on the batch A tree: the tests that could pass vacuously on an empty `MetaData` assert `len(metadata.tables) == 37` first (the first red run found five such passes; they were fixed and the logs regenerated).

## Method

* Oracle: a scratch copy of the repository under the scratchpad, with a hand-written `tables.py` and `codecs.py` and small edits of `columns.py` (adds `BigIntCol` and the SQLite shape CHECKs of `InstantCol` and `DecimalCol`), `errors.py` and `__init__.py`. The diff against the batch A source is `oracle-src.patch`. It is a reference, not the implementation.
* Postgres: `docker run` of `postgres:15@sha256:baa22e62...` and `postgres:18@sha256:edb0c08d...` (the digests of the batch A CI contract), bound to 127.0.0.1 on a random port, password from `openssl rand -hex 24` held only in the shell that runs pytest and scrubbed from the log; database `tracer_test` with `Asia/Kolkata` as its time zone and database `tracer_ascii` (SQL_ASCII); the container is removed after the run. No container named `voicera_oss_*` was touched.
* Environments: one `UV_PROJECT_ENVIRONMENT` directory per Python version and role (`red`, `oracle`) under the scratchpad; the shared `.venv` was not used or changed.
* Mutants: a Python runner applies one text edit to a pristine copy of the oracle source, runs the directory, restores the pristine files and parses the failures; four worker copies of the oracle run in parallel.
* Paths: `<repo>`, `<scratchpad>`, `~` and `<user>` replace the local paths and the user name in every log.

## Findings found while building the oracle (details in the contract, section 10)

* A1: the golden `RunBundle/maximal` holds two spans with one `span_ref`, so `external_ref_index` cannot be unique for `span` (ADR 0019 Table 2 and ADR 0022 Decision 10 say it is). The tests implement the recommendation (unique for `run` only); the mutant TM33 is the ADR text and is caught by the golden round trip.
* A2: JCS refuses integers above 2**53 - 1, so no document with a larger number can be hashed; the codec refuses them with `StoreValueOutOfRange` (before the 64-bit limit matters).
* A11: the batch A import whitelist forbids `pydantic` in `tracer_evaldb`.
* A12: pg8000 reports `ON DELETE RESTRICT` as SQLSTATE 23001.
