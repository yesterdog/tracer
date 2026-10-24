# Phase 2 batch A contract: scaffold, licences, CI, engine, column types (P2-01 to P2-05)

Author: qa, 2026-10-24. Source: `docs/phase2/SCOPE.md` (section 1.2, Decisions D2), `docs/security/P2-02-licenses.md`, the lead's instructions of 2026-10-24. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or HANDOFF. Every statement below is pinned by a test named in section 7.

## 0. Where the tests are, and how to run them

IMPORTANT (found while writing them): a directory `packages/evaldb/` without a `pyproject.toml` breaks `uv run --locked` and `uv lock --check` for the whole workspace (`members = ["packages/*"]`: "Workspace member ... is missing a pyproject.toml"). So the tests are staged at

`docs/phase2/evidence/red/batch-a/staging/tests/`

(the last path component is `tests` so the `**/tests/**` ruff rule `S101` applies; `ruff`, `ruff format`, `mypy --strict` and the header check pass on them there). The dev's first act in P2-01 is `git mv docs/phase2/evidence/red/batch-a/staging/tests packages/evaldb/tests` together with the scaffold and the relock. Nothing in the tests depends on the location: the repository root is found by walking up to `uv.lock`, and the CI test derives the path it passes to pytest from `__file__`. There is no `__init__.py` in the directory on purpose: `packages/evalschema/tests` is the package `tests`, and a second package of that name would clash. Test module names are unique (`test_evaldb_*`); `conftest.py` is the only `conftest`. Shared helpers live in `conftest.py` fixtures, not in sibling imports.

Commands (after the move and the root `pyproject.toml` changes of section 8):

* default (SQLite side, what every Python leg runs): `uv run --locked --all-groups pytest` (testpaths cover both packages)
* Postgres side: `TRACER_TEST_POSTGRES_URL=... TRACER_TEST_POSTGRES_SQL_ASCII_URL=... uv run --locked --all-groups pytest -m postgres packages/evaldb/tests`

Test-only environment variables (names are part of the contract):

| Variable | Meaning |
|---|---|
| `TRACER_TEST_POSTGRES_URL` | `postgresql+pg8000://postgres:<password>@127.0.0.1:<port>/tracer_test`. The database `tracer_test` has `ALTER DATABASE ... SET timezone TO 'Asia/Kolkata'`. |
| `TRACER_TEST_POSTGRES_SQL_ASCII_URL` | the same server, database `tracer_ascii`, created `ENCODING 'SQL_ASCII' ... TEMPLATE template0`. |

`conftest.py` registers the marker `postgres` itself (so the directory runs alone under `--strict-markers`), parametrizes `store` as `[sqlite]` and `[postgres]` (the second carries the marker) and raises `pytest.UsageError` (exit code 4) when a `postgres`-marked test is selected and `TRACER_TEST_POSTGRES_URL` is unset. It never skips. Fixtures: `evaldb` and `sa` (the modules, loaded lazily so each test fails on its own before the package exists), `store` / `sqlite_store` / `postgres_store` (a handle with `.engine`, `.backend`, `.url`, `.metadata`, `.table(name, *columns)` which creates a uniquely named STRICT table and drops it at teardown, `.sql_type(table, column)`, `.collation(table, column)`), and `postgres_sql_ascii_url`.

## 1. P2-01: scaffold (`test_evaldb_scaffold.py`)

1.1 Layout. `packages/evaldb/{pyproject.toml,README.md,LICENSE,NOTICE}` (`LICENSE` byte-identical to `packages/evalschema/LICENSE`), `src/tracer_evaldb/`, `tests/`. Every `.py` under `src/` starts with the two SPDX header lines. `src/tracer_evaldb/py.typed` exists.

1.2 `pyproject.toml` of the package: build backend `hatchling.build`, `requires = ["hatchling==1.32.4"]`; `name = "tracer-evaldb"`, `requires-python = ">=3.11"`, `license = "Apache-2.0"`, `license-files = ["LICENSE", "NOTICE"]`; wheel `packages = ["src/tracer_evaldb"]`; dependencies exactly (compared as PARSED requirements and specifier sets, because wheel metadata reorders `>=2.1.6,<3` to `<3,>=2.1.6`):

```
tracer-evalschema==0.0.1          (exact: the schema-version pin, not a library range)
sqlalchemy>=2.1.6,<3
alembic>=1.20.1,<2
[project.optional-dependencies] postgres = ["pg8000>=1.31.5,<2"]
```

Every range other than evalschema has exactly the operators `>=` and `<` and the upper bound is the next major of the lower bound. A bound may only be lowered when the suite passes on the lower-bound leg (SCOPE D2). The wheel METADATA has `Provides-Extra: postgres`, `Requires-Python: >=3.11`, and `Requires-Dist` equal (parsed) to the three plus `pg8000<2,>=1.31.5; extra == "postgres"`.

1.3 Wheel (`python -m hatchling build -t wheel`): top-level entries are only `tracer_evaldb/` and the one `*.dist-info/`; it contains `tracer_evaldb/__init__.py`, `py.typed`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/__init__.py` (batch D fills them; the scaffold ships these three so the wheel test is satisfiable now); no member has a path part `tests`, `test` or `fixtures`, no basename `test_*` or `conftest.py`, none containing `evalschema_baseline`; the dist-info carries LICENSE and NOTICE.

1.4 Import rules. Every module under `src/tracer_evaldb` (any depth, including inside functions and `try`) imports only the standard library, `sqlalchemy`, `alembic`, `tracer_evalschema`, `tracer_evaldb`; `importlib.import_module` / `__import__` calls must have a literal name from that set (a computed name is an offence). In a fresh `python -I` interpreter, importing every module except `tracer_evaldb.migrations.*` loads, beyond the standard library, only `sqlalchemy`, `alembic`, `mako`, `markupsafe`, `tracer_evalschema`, `tracer_evaldb`, `pydantic`, `pydantic_core`, `annotated_types`, `typing_extensions`, `typing_inspection`, `rfc8785` and the `cython_runtime` marker; and none of `pg8000`, `scramp`, `dateutil`, `psycopg`, `psycopg2`, `asyncpg`, `pytest`, `jsonschema`, `hypothesis`, `yaml` (pg8000 is reached only through the SQLAlchemy URL).

1.5 evalschema is untouched. (a) No module of `tracer_evalschema` imports `tracer_evaldb`, `sqlalchemy`, `alembic`, `pg8000`, `psycopg`, `psycopg2`, `asyncpg` (AST) and importing all of them in a fresh interpreter loads none. (b) Its runtime closure in `uv.lock` is still the six packages of `[runtime].closure`, its `pyproject.toml` dependencies are still `pydantic>=2.11,<3` and `rfc8785>=0.1.4,<0.2` with no extras. (c) `docs/phase2/evidence/evalschema-baseline.json` exists with exactly the keys `commit` (40 hex), `schema_lock_sha256`, `source_tree_sha256`, `pyproject_sha256` (64 hex); the last three equal what `tests/evalschema_baseline.py` computes now. The dev creates it at scaffold time: `uv run python packages/evaldb/tests/evalschema_baseline.py` prints the three hashes (qa today: `schema_lock_sha256 ee5b2ac3...`, `source_tree_sha256 59d5b637...`, `pyproject_sha256 1a918eef...`, working tree on top of `bfe1ab4`; the dev recomputes them), and the dev adds `commit` = the rewritten-history tip at that time (SCOPE D3). The algorithm is in the docstring of that script (sorted `<relpath>\0<file sha256>\n`, no `__pycache__` or `*.pyc`).

1.6 Repository configuration (extra test `test_evaldb_scaffold_files_and_repo_config`): see section 8 for the exact edits; `uv.lock` holds `tracer-evaldb` with `source = { editable = "packages/evaldb" }` and sqlalchemy, alembic, pg8000. Extra test `test_evaldb_locked_closure_is_the_reviewed_one`: the lock closure of `tracer-evaldb` is the base set {alembic, annotated-types, mako, markupsafe, pydantic, pydantic-core, rfc8785, sqlalchemy, tracer-evalschema, typing-extensions, typing-inspection} and, with extra `postgres`, also {asn1crypto, pg8000, python-dateutil, scramp, six, tzdata}. No greenlet; tzdata is new (P2-02 document, section 2).

## 2. P2-02: licence policy (`test_evaldb_licenses.py`)

Format of `docs/security/license-policy.toml` (the block of P2-02-licenses.md section 6.2, plus `[[rejected]]`):

```toml
[runtime.evaldb]            # sub-table of the existing [runtime]; only these three keys
closure = ["alembic","annotated-types","mako","markupsafe","pydantic","pydantic-core","rfc8785","sqlalchemy","typing-extensions","typing-inspection"]   # sorted
extras_postgres = ["asn1crypto","pg8000","python-dateutil","scramp","six","tzdata"]                                                                        # sorted
requires_dist = ["tracer-evalschema==0.0.1","sqlalchemy>=2.1.6,<3","alembic>=1.20.1,<2","pg8000>=1.31.5,<2; extra == 'postgres'"]

[[rejected]]                # name, spdx, reason (non-empty); at least psycopg and psycopg-binary
name = "psycopg"
spdx = "LGPL-3.0-only"
reason = "..."
```

plus two `[[overrides]]` (scope `runtime`, `license_file = "licenses/LICENSE"`, a 64-hex `license_file_sha256`, non-empty `reason`, `version` equal to the locked one): `pg8000` (`BSD-3-Clause`) and `python-dateutil` (`Apache-2.0 AND BSD-3-Clause`). The existing `[runtime]` keys are unchanged.

Pinned by test: the block's `closure` equals the lock's base closure minus `tracer-evalschema`; `extras_postgres` equals the lock closure with the extra minus the base; the `[runtime]` closure is a subset; the allowlist has no GPL-family term; no locked package starts with `psycopg`, none of `python-bidi`; the licence/classifier metadata of every installed package in the evaldb closure (extra included) matches no GPL/LGPL/AGPL; the licence files of markupsafe, asn1crypto, python-dateutil and six are present in the installed distributions and read as a licence text. `test_license_exceptions_exact` (Phase 1) stays unchanged.

`scripts/check_licenses.py` (extra test `test_check_licenses_covers_both_packages`): exit 0 and `0 failed` on the real policy; exit 1 naming `tracer-evaldb` and the package when `[runtime.evaldb].closure` gains an entry the lock does not have; exit 1 naming `tzdata` when it is dropped from `extras_postgres`; `--wheel <dir> --artifacts-only` on the built evaldb wheel prints `1 ok, 0 failed` (its `Requires-Dist` is compared with `[runtime.evaldb].requires_dist` as normalised requirements, and `Provides-Extra` must be exactly `postgres` for this wheel; the evalschema wheel keeps "no extras"); exit 1 with `Requires-Dist` in the message when the policy lists a looser range than the wheel. The reference change to the script is in `oracle-src.patch` (about 70 lines: policy fields, `Lock.closure(..., extras)`, `evaldb_findings`, per-package `requires_dist`).

## 3. P2-03: CI (`test_evaldb_ci.py`)

All statements are about `.github/workflows/ci.yml`; every `uses:` stays SHA-pinned (Phase 1 test), workflow `permissions` stay `contents: read`, no job has `services:`.

3.1 Job `test-postgres` (required: no `continue-on-error`): `name` is exactly `test-postgres (py${{ matrix.python-version }}, pg${{ matrix.postgres }})`, `runs-on: ubuntu-latest`, `strategy.fail-fast: false`, `strategy.matrix` has only `include` with four entries, each with exactly the keys `python-version` (string), `postgres` (string) and `image`: (3.11,15), (3.11,18), (3.14,15), (3.14,18). `image` matches `^postgres:<major>(\.<minor>)?@sha256:<64 hex>$` with `<major>` equal to the entry's `postgres`; one digest per major. The setup-uv step has `python-version: ${{ matrix.python-version }}` and `version: ${{ env.UV_VERSION }}`. The steps contain `uv sync --locked --all-groups`, one run step that uses `${{ matrix.image }}` in `docker run` with `-p 127.0.0.1:...` (loopback only), waits with `pg_isready`, creates the databases (`ENCODING 'SQL_ASCII'` ... `TEMPLATE template0`) and sets `Asia/Kolkata` as the default time zone of the test database, and exports `TRACER_TEST_POSTGRES_URL` and `TRACER_TEST_POSTGRES_SQL_ASCII_URL` (`postgresql+pg8000://`) through `$GITHUB_ENV`; and the test step `uv run --locked --all-groups pytest -m postgres packages/evaldb/tests`. Any `postgres:<digit>...` reference anywhere in the file carries `@sha256:` (or is `postgres:${{`).

Digests read by qa on 2026-10-24 with `docker pull` (these are the manifest-list digests, valid for every platform; `postgres:15` was 15.19 and `postgres:18` was 18.6; Docker Hub moves tags, so the dev re-reads them in P2-03 and records the date):

```
postgres:15@sha256:baa22e6286bf832cb147b8bb5aa9d8bc999846d69c21d11bf63f58fbeed1fa48
postgres:18@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6
```

3.2 Job `test-postgres-py315`: `continue-on-error: true`, `name` exactly `test-postgres (py3.15, pg18, non-blocking)`, one matrix entry (3.15, 18) whose image is one of the pg18 images above, the same steps, one `pytest -m postgres packages/evaldb/tests`.

3.3 Job `test-oldest-evaldb-deps` (required): `name` exactly `test (lowest-direct evaldb deps, py3.11, pg15)`, setup-uv `python-version: "3.11"`, its run text contains `uv run --resolution lowest-direct` (full `--resolution lowest` fails on an old transitive dependency, P2-02 document 6.1), `pytest -m "not private_fixtures and not local_only" packages/evaldb/tests` (SQLite and Postgres cases together; `--deselect` of lock-comparing tests may follow), the pinned Postgres 15 image of 3.1 (`@sha256:<the pg15 digest>`), `TRACER_TEST_POSTGRES_URL` and `::add-mask::`.

3.4 Passwords (`test_ci_postgres_password_not_literal`; the checker also runs on bad and good samples): no literal after `POSTGRES_PASSWORD=` / `POSTGRES_PASSWORD:` (it must start with `$`), no `scheme://user:literal@host`, no `env:` value for a key matching password/passwd/secret/token/api-key that is not a `$` reference, no `trust` authentication (`POSTGRES_HOST_AUTH_METHOD` or the word), no `services:`. In each of the three Postgres jobs the password comes from `openssl rand -hex 24`, then `::add-mask::` is printed, and only afterwards is anything written to `$GITHUB_ENV`.

3.5 SQLite on every leg (`test_ci_sqlite_runs_on_every_python_leg`): see section 8 (testpaths, addopts, marker); the `test` and `test-py315` jobs run exactly `uv run --locked --all-groups pytest` (no path, no `-m`), `test.strategy.matrix.python-version` stays 3.11 to 3.14; the `lint` job runs the bare `ruff check` and `mypy`; the `licenses` job runs `uv build --package tracer-evaldb`.

3.6 `test_postgres_marker_without_url_is_usage_error`: `pytest -m postgres <tests dir>` without the URL exits 4, names `TRACER_TEST_POSTGRES_URL`, prints no pass, fail or skip; the default command line (`--collect-only -q`, root addopts) lists `[sqlite]` and no `[postgres]`; with a URL set (never contacted at collection) `-m postgres` lists `[postgres]` ids, no `[sqlite]`, and `test_postgres_session_timezone_is_utc`.

## 4. P2-04: `create_store_engine` (`test_evaldb_engine.py`)

Modules: `tracer_evaldb.engine` (function), `tracer_evaldb.errors` (types); all re-exported from `tracer_evaldb`.

```python
def create_store_engine(url: str | sqlalchemy.engine.URL, *, busy_timeout_ms: int = 5000) -> sqlalchemy.engine.Engine
```

Parameter names and kinds are tested (`url` positional-or-keyword, `busy_timeout_ms` keyword-only, default 5000). Errors, all subclasses of `StoreError(Exception)`:

| Type | When | Message |
|---|---|---|
| `StoreConfigurationError` | URL cannot be parsed (including a bad port); backend/driver pair is not `sqlite`+`pysqlite` or `postgresql`+`pg8000` (so `mysql+...`, and plain `postgresql://` which defaults to psycopg2, are refused); `busy_timeout_ms` is not an `int` >= 0 (`bool`, float, str, None refused) | fixed text, no input |
| `StoreConnectionError` | the connection probe failed (unreachable, authentication, missing directory) | fixed text, no input |
| `UnsupportedSqliteVersion` | `sqlite3.sqlite_version_info < (3, 37, 0)` read at call time, before any connection (no file is created) | names the found version and 3.37 |
| `UnsupportedServerEncoding` | Postgres `server_encoding != 'UTF8'` | names the found encoding and UTF8 |

The function connects once (the probe) before returning, for both backends. Errors are raised `from None`: `__cause__ is None` and the context is suppressed. No error text, argument, repr or formatted traceback, and no log record created by tracer code, contains the password, user, host or database of the URL, nor the SQLite path (`test_database_url_never_echoed_in_errors_or_logs`, six scenarios per backend, including a refused URL object and a wrong password against the real server). SQLAlchemy's own `sqlalchemy.pool` DEBUG records do print the host on a failed connect; the test leaves the `sqlalchemy` logger at its default level and the contract does not change SQLAlchemy logging.

SQLite behaviour: `PRAGMA foreign_keys` is 1 on every connection (four simultaneous, after `dispose()`, a raw DBAPI connection, a second engine); an orphan insert raises `sqlalchemy.exc.IntegrityError` and `ON DELETE CASCADE` works; `PRAGMA busy_timeout` equals `busy_timeout_ms`; transactions are real on every Python: `driver_connection.in_transaction` is true after the first statement of `engine.begin()` (a BEGIN is emitted on the SQLAlchemy `begin` event with `isolation_level=None` on the DBAPI connection), DML rolls back, a failing statement undoes earlier ones, DDL rolls back, SAVEPOINT rollback keeps outer work, a commit is visible to a second engine.

Postgres behaviour: every pooled connection reports `SHOW TimeZone` = `UTC` while the database default is `Asia/Kolkata` (so `'2026-01-01 00:00:00+05:30'::timestamptz::text` is `2025-12-31 18:30:00+00`); a SQL_ASCII database is refused.

## 5. P2-05: column types (`test_evaldb_columns.py`)

Module `tracer_evaldb.columns`, all names re-exported from `tracer_evaldb`, and `tracer_evaldb.metadata` (a `sqlalchemy.MetaData`, empty until batch B). Each is a SQLAlchemy type usable as `Column("v", UuidCol())`; none subclasses `DateTime`, `Date`, `Time`, `Interval` or `Float`. A value the type does not accept raises `ValueError` at bind (surfacing as `sqlalchemy.exc.StatementError` whose `.orig` is the `ValueError`), including a wrong Python type: nothing is coerced. `None` always means NULL. Tables in the tests are created with `sqlite_strict=True`, so every SQLite DDL type name must be STRICT-legal.

| Type | Python value | SQLite | Postgres | Checks and rejections |
|---|---|---|---|---|
| `UuidCol` | `uuid.UUID` (type exactly) | `BLOB`, 16 bytes = `uuid.bytes`, `typeof` `blob` | `uuid` | the type attaches a CHECK: version nibble = 7 (also length 16 on SQLite), NULL passes; v0..v15 except 7, nil, max, uuid1/4/5 are refused by the database (`IntegrityError` on SQLite, a `DBAPIError` on Postgres, see 10.3), a 0, 15, 17, 32 byte blob is refused; `ORDER BY` / `min` follow v7 time order; str, hex, bytes, int are refused at bind |
| `InstantCol` | tz-aware `datetime` (type exactly) in, UTC-aware out (`tzinfo is datetime.UTC`) | `TEXT` `YYYY-MM-DDTHH:MM:SS.ffffffZ`, 27 characters, zero-padded by hand (no `strftime`), sorts chronologically as text | `timestamp with time zone` | years 1 and 9999 and 999/1000 edges, microseconds, offset and `ZoneInfo` inputs (including 1850 local mean time) equal as instants; naive datetime, an offset input that leaves years 1 to 9999 in UTC, str, int, float refused; unaffected by process `TZ=Asia/Kolkata` (SQLite) and by the database default time zone (Postgres) |
| `DecimalCol` | `decimal.Decimal` (type exactly) | `TEXT`, `format(value, 'f')`, `typeof` `text` | unconstrained `numeric` (no precision or scale) | digits and scale kept (`str` equal: 18 fractional digits, 30 integer digits, 60 + 40, negative, `0`, `1.50`); exponent form accepted and equal in value; float, int, str, NaN, sNaN, +-Infinity, negative zero refused |
| `DocCol` | `str` | `TEXT` | `text` (not json/jsonb) | exact text incl. 5 million characters, escaped NUL text, U+2028, astral, NFC and NFD kept; raw U+0000, lone surrogate, bytes, dict, int refused |
| `Sha256Col` | `bytes` (type exactly), 32 bytes; read back as exactly `bytes` | `BLOB` | `bytea` | CHECK length = 32 (database refuses 0, 1, 31, 33 bytes); bytearray, memoryview, hex str, hashlib object, int refused |
| `KeyCol` | `str` | `TEXT` (binary collation) | `COLLATE "C"` (`information_schema.columns.collation_name = 'C'`; base type `text` or `varchar`) | order equals UTF-8 byte order (code point order, not UTF-16 order) on both backends for ORDER BY, comparison ranges, min and max; case, trailing and leading white space, Turkish, Greek, Kelvin variants are distinct; NFC and NFD are distinct and kept byte for byte (also in `DocCol`); U+0000, lone surrogates, bytes, int refused |
| `BoolCol` (not named by SCOPE, see 10.2) | `bool` (type exactly) or `None` | `INTEGER` 0/1 with CHECK `IN (0, 1)` | `boolean` | nullable by default with no default and no server default; omitted and `None` stay NULL, never false; 0, 1, 2, 1.0, "true", "f", bytes refused; raw 2 and -1 refused by the SQLite CHECK |

`test_no_sqlalchemy_datetime_columns`: none of the seven classes is a subclass of `DateTime`, `Date`, `Time`, `Interval`, `Float`; no column of `tracer_evaldb.metadata` (and no `impl`) is an instance of them; an AST scan of every module of the package finds none of the names `DateTime Date Time Interval Float REAL FLOAT DOUBLE DOUBLE_PRECISION`, and `TIMESTAMP` only inside `columns.py`.

## 6. Table counts (for batch B, not tested in batch A)

`tracer_evaldb.metadata` will hold 26 entity tables plus 11 side tables = 37 (ADR 0019 tables 1 to 3 and 5), and the Alembic bookkeeping table `alembic_version` is NOT in the MetaData (it is created by Alembic; `compare_metadata` must ignore it). In batch A `metadata` is empty; a count test now would have to be rewritten in batch B, so batch A pins only the object's existence and type (`test_no_sqlalchemy_datetime_columns`). Batch B owns `test_metadata_has_one_table_per_entity`.

## 7. Test inventory

Files: `test_evaldb_scaffold.py`, `test_evaldb_licenses.py`, `test_evaldb_ci.py`, `test_evaldb_engine.py`, `test_evaldb_columns.py`, `conftest.py`, `evalschema_baseline.py` (script and hash functions). 60 collected: 44 run by default, 16 carry the marker (14 `[postgres]` parametrizations of the `store` tests, plus the two `@pytest.mark.postgres` tests `test_postgres_session_timezone_is_utc` and `test_postgres_non_utf8_database_refused`).

All 32 names of SCOPE section 1.2 exist under their exact names, except that `test_license_exceptions_exact` is the existing Phase 1 test and is not duplicated (31 new). Added by qa (11, not in the SCOPE table; each makes a contract item testable): `test_create_store_engine_signature_and_error_hierarchy`, `test_sqlite_busy_timeout_is_configured`, `test_uuid_column_sorts_in_v7_time_order`, `test_key_column_rejects_nul_and_lone_surrogates`, `test_doc_column_round_trips_text_exactly`, `test_sha256_column_is_32_byte_blob`, `test_ci_lower_bound_leg_for_evaldb_ranges`, `test_evaldb_scaffold_files_and_repo_config`, `test_evaldb_locked_closure_is_the_reviewed_one` (2 parametrizations), `test_check_licenses_covers_both_packages`, `test_licence_files_of_unassumed_packages_are_read` (4 parametrizations).

Two guard tests pass on the Phase 1 tree by nature (`test_evalschema_does_not_import_evaldb`, `test_evalschema_runtime_closure_unchanged_by_evaldb`) and `test_postgres_marker_without_url_is_usage_error` is partly satisfied by qa's conftest; their teeth are the mutants W7/W8/W9 and T1 in `evidence/red/batch-a/mutants.txt`.

## 8. Required changes to the repository (qa did not make them)

Root `pyproject.toml`:

```toml
[dependency-groups]
dev = [ ..., "tracer-evalschema", "tracer-evaldb[postgres]", ... ]    # pg8000 must be installed for the postgres job

[tool.uv.sources]
tracer-evaldb = { workspace = true }

[tool.ruff]
src = ["packages/evalschema/src", "packages/evalschema/tests", "packages/evaldb/src", "packages/evaldb/tests", "scripts"]

[tool.mypy]
files = ["packages/evalschema/src", "packages/evalschema/tests", "packages/evaldb/src", "packages/evaldb/tests", "scripts"]
mypy_path = "packages/evalschema/src:packages/evaldb/src"

[tool.pytest.ini_options]
testpaths = ["packages/evalschema/tests", "packages/evaldb/tests"]
addopts = "-ra --strict-markers --strict-config -m 'not private_fixtures and not local_only and not postgres'"
markers = [ ..., "postgres: needs a Postgres server (TRACER_TEST_POSTGRES_URL); deselected by default, selected in the Postgres CI job with -m postgres" ]
```

Also: `uv lock` (adds tracer-evaldb, sqlalchemy, alembic, mako, markupsafe, pg8000, scramp, asn1crypto, python-dateutil, six, tzdata); `docs/security/license-policy.toml` (section 2); `scripts/check_licenses.py` (section 2); `.github/workflows/ci.yml` (section 3); `docs/phase2/evidence/evalschema-baseline.json` (1.5). The `lint` job's `alembic check` step belongs to batch D.

Reference implementation: `docs/phase2/evidence/red/batch-a/oracle-src.patch` (everything except the tests and `uv.lock`; the lock diff is `oracle-uv.lock.patch`). It is qa's scratch oracle that makes the SQLite and Postgres tests pass; the dev owns the real code and may differ.

## 9. Evidence (`docs/phase2/evidence/red/batch-a/`)

Counts are in the final report and in the log tails; summary: red on the Phase 1 tree 19 failed, 2 passed, 23 errors, 16 deselected on 3.11, 3.12, 3.13, 3.14 (`red.py3.*.log`); oracle SQLite 44 passed, 16 deselected on all four; oracle Postgres 15 and 18 (throwaway docker containers) 16 passed, 44 deselected on 3.11 and 3.14 each; 59 mutants, 56 caught, 2 equivalent, 1 expected usage-error abort (`mutants.txt`).

## 10. Ambiguities, with recommendations (the lead auto-approves; qa implemented the recommendation in the tests)

1. Where the tests live until P2-01: staged outside `packages/` (section 0). Recommendation: the dev moves them in the P2-01 commit.
2. `BoolCol`: SCOPE P2-05 asks for "booleans as nullable with NULL meaning absent" and names no class. Recommendation: a seventh class `BoolCol` in `columns.py` (pinned).
3. pg8000 reports every server error, including constraint violations (SQLSTATE class 23), as `pg8000.dbapi.ProgrammingError`, so SQLAlchemy raises `ProgrammingError`, not `IntegrityError`, on Postgres (observed on 15.19 and 18.6). Batch A therefore asserts `sqlalchemy.exc.DBAPIError` for constraint failures on both backends and `IntegrityError` only on SQLite. Recommendation: in batch C (typed `ReimportConflict` and friends) translate SQLSTATE class 23 into `IntegrityError` in a `handle_error` hook of `create_store_engine`, and add a both-backends test then. Not done now to keep batch A minimal.
4. Plain `postgresql://` URL: refuse (pinned) rather than silently rewrite to pg8000. Recommendation: refuse; the message names `postgresql+pg8000://` in the docs, not the URL.
5. `sqlite://` (in memory) and relative paths: unspecified; the tests use absolute file paths. Recommendation: file paths only for batch A; decide in-memory support when a fast test fixture needs it.
6. `UuidCol` checks the version nibble only, not the RFC 4122 variant bits (SCOPE says "version nibble"). Recommendation: keep as SCOPE says.
7. `DecimalCol` refuses negative zero (`Decimal('-0')`), int and str, because Postgres `numeric` has no negative zero and nothing may be coerced. Recommendation: pinned as written.
8. The CI shape: matrix as `include` entries carrying the `image`, because one digest cannot serve two majors; the 3.15 leg as a separate job (like `test-py315`). Recommendation: as pinned.
9. Lower-bound leg: `uv run --resolution lowest-direct` also lowers pydantic and rfc8785 (direct dependencies of the workspace's other member). Recommendation: accept (it tests the evalschema ranges too); the leg deselects tests that compare installed versions with the lock.
10. The lower-bound leg starts Postgres 15 (for pg8000) and runs SQLite and Postgres cases together with `-m "not private_fixtures and not local_only"`. Recommendation: as pinned.
11. `TRACER_TEST_POSTGRES_SQL_ASCII_URL` is a second variable, not checked by the collection-time usage error (only the main URL is); a missing one fails `test_postgres_non_utf8_database_refused` with an assertion message. Recommendation: keep, it matches "CI creates a SQL_ASCII database".
12. SQLAlchemy's own pool DEBUG logging prints the host on a failed connect (not controlled by tracer). Recommendation: document in STORAGE_POLICY (P2-25); no code.
13. `tracer-evaldb` package version: `0.0.1` (same as evalschema), assumed by the oracle; the CHANGELOG test of P2-25 decides later.
14. The Phase 1 T-DRIFT scan (in `test_tdrift.py`) reads every committable text file, so any token it treats as an excluded language code in a Phase 2 document fails the Phase 1 suite. qa's first draft of this document tripped it and was reworded; in a scratch copy without `.git` the same scan also flagged `docs/security/P2-02-licenses.md` (line 49), which passes in the real repository today. Recommendation: the lead re-runs the Phase 1 suite after every Phase 2 document is committed.
