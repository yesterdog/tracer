# P2-02 licence review of the tracer-evaldb runtime closure (security-licensing)

Date of lookups: 2026-10-24. Scope: the planned runtime closure of `tracer-evaldb` (`docs/phase2/SCOPE.md`, P2-01 and P2-02, section 2 "External", Decisions D2), plus the test and infrastructure items named in the task. Nothing in the repository was changed except this file.

## 1. Method

- Closure resolved in throwaway projects under `<scratch>` (`uv init --bare`, `requires-python = ">=3.11"`, `uv add --no-sync "sqlalchemy>=2.1.6,<3" "alembic>=1.20.1,<2" "pg8000>=1.31.5,<2"`). The repository `pyproject.toml` and `uv.lock` were not touched. Two resolutions were run: default (highest) and `--resolution lowest-direct`. Both give the same 11 packages and versions (section 2).
- Each wheel was downloaded from PyPI into its own directory under `<scratch>`, unzipped, and read with `METADATA` (`License-Expression`, `License`, classifiers, `Requires-Dist`) and the licence file in `*.dist-info`. Files were treated as untrusted data and never executed or imported. SHA-256 of each licence file is recorded in section 4.
- The wheel was chosen per package: pure-Python wheels where they exist; `sqlalchemy` and `markupsafe` were read from the cp313 macOS arm64 wheels (the licence metadata does not differ per platform tag in these projects, but the Linux and Windows wheels were not each opened: UNVERIFIED for those tags).
- The classification used the repository's own `scripts/check_licenses.py` (`classify_expression`) on the declared strings, to find which packages need an override.

## 2. Resolved closure (Python >=3.11, both resolutions)

| Package | Version | Pulled in by | Group |
|---|---|---|---|
| sqlalchemy | 2.1.6 | evaldb (direct) | base |
| alembic | 1.20.1 | evaldb (direct) | base |
| mako | 1.4.3 | alembic | base |
| markupsafe | 3.0.4 | mako | base |
| typing-extensions | 4.16.0 | sqlalchemy, alembic | base (already in the evalschema closure) |
| pg8000 | 1.31.5 | evaldb extra `[postgres]` | extra postgres |
| scramp | 1.4.18 | pg8000 | extra postgres |
| asn1crypto | 1.5.1 | scramp | extra postgres |
| python-dateutil | 3.0.0 | pg8000 | extra postgres |
| six | 1.17.0 | python-dateutil | extra postgres |
| tzdata | 2026.5 | python-dateutil 3.0.0 | extra postgres |

Two differences from the SCOPE list, both read from the lock and the wheel metadata:

1. **greenlet is not in the closure.** `sqlalchemy 2.1.6` lists `greenlet>=1` only under `Provides-Extra: asyncio` (`Requires-Dist: greenlet>=1; extra == "asyncio"`). The base `Requires-Dist` of SQLAlchemy 2.1.6 is `typing-extensions>=4.6.0` only, with `Requires-Python: >=3.11`. The resolved lock contains no greenlet on Python 3.14 (first resolution) or on the universal `>=3.11` resolution. A sync API (SCOPE Q about pg8000) never needs the `asyncio` extra. Not tested: Python 3.15 (see section 7).
2. **tzdata is new.** `python-dateutil 3.0.0` has an unconditional `Requires-Dist: tzdata`. The SCOPE list (P2-02, section 2) does not name it. `pg8000 1.31.5` only requires `python-dateutil>=2.8.2`, so the resolver is free to pick a 2.x dateutil (no tzdata) on a user's machine; the locked CI closure has tzdata. `alembic 1.20.1` has `tzdata` only under the extra `tz`, which we do not request. `tomli` (alembic, `python_version < "3.10"`) is not installed on `>=3.11`.

The packages `SQLAlchemy` and `Mako` appear in their metadata with upper-case names; the policy normalises names.

## 3. Verdict per package

Ship-in-wheel column: none of these is vendored in, copied into, or shipped inside the `tracer-evaldb` wheel; the wheel only lists them in `Requires-Dist`. (The wheel contents are checked separately by `test_evaldb_wheel_contains_migrations_and_no_tests` and the `--wheel` licence step. That the built wheel ships no third-party code is a plan statement, not yet verified here because the wheel does not exist.)

| Package | Version tested | SPDX (from the files) | Class | Source of the finding | Verdict |
|---|---|---|---|---|---|
| sqlalchemy | 2.1.6 | MIT | permissive | METADATA `License-Expression: MIT`; `licenses/LICENSE` is the MIT text ("Copyright 2005-2026 SQLAlchemy authors"), no Apache/GPL text inside; `License-File: AUTHORS` is not a licence | go |
| alembic | 1.20.1 | MIT | permissive | METADATA `License-Expression: MIT`; LICENSE is MIT text ("Copyright 2009-2026 Michael Bayer") | go |
| mako | 1.4.3 | MIT | permissive | METADATA `License-Expression: MIT`; LICENSE is MIT text | go |
| markupsafe | 3.0.4 | BSD-3-Clause | permissive | METADATA `License-Expression: BSD-3-Clause`; `licenses/LICENSE.txt` is the 3-clause text ("Copyright 2010 Pallets"); contains a C speedups source file `_speedups.c` covered by the same licence | go |
| typing-extensions | 4.16.0 | PSF-2.0 | permissive | METADATA `License-Expression: PSF-2.0`; LICENSE is the PSF text. Same version and licence as already recorded in `[runtime]` for evalschema (P1-02) | go |
| pg8000 | 1.31.5 | BSD-3-Clause | permissive | METADATA has `License: BSD 3-Clause License` (not an SPDX identifier; the repository checker returns "unparseable" for it) plus classifier "BSD License"; `licenses/LICENSE` is the 3-clause text ("Copyright Mathieu Fenniak and Contributors to pg8000") | go, through an override (section 5) |
| scramp | 1.4.18 | MIT-0 | permissive | METADATA `License-Expression: MIT-0`; LICENSE begins "MIT No Attribution" and has the MIT grant without the notice condition | go |
| asn1crypto | 1.5.1 | MIT | permissive | METADATA `License: MIT`, classifier MIT License; LICENSE is the MIT text ("Copyright (c) 2015-2022 Will Bond") | go |
| python-dateutil | 3.0.0 | Apache-2.0 AND BSD-3-Clause | permissive (both) | METADATA `License: Dual License` (not SPDX; unparseable by the checker), classifiers BSD and Apache. LICENSE holds the Apache-2.0 notice ("applies to all contributions after 2017-12-01 ... and re-licensed code") followed by a BSD-3-Clause text, and ends "The above BSD License Applies to all code, even that also covered by Apache 2.0." So BSD-3-Clause covers all code and Apache-2.0 covers part. Recorded with the conjunction (the stricter reading; both terms are on the allowlist). Not a choice between the two: the file does not give a free choice | go, through an override (section 5) |
| six | 1.17.0 | MIT | permissive | METADATA `License: MIT`, classifier MIT License; LICENSE is the MIT text ("Copyright (c) 2010-2024 Benjamin Peterson") | go |
| tzdata | 2026.5 | Apache-2.0 | permissive | METADATA `License: Apache-2.0`; `licenses/LICENSE` and `licenses/licenses/LICENSE_APACHE` are Apache-2.0 (the second is byte-identical in hash to the Apache text already pinned for `trove-classifiers` in the policy: `c71d239d...`). The package ships IANA time zone data files (`zoneinfo/*`); the data files read (`leapseconds`, `zone1970.tab`, `iso3166.tab`) state they are in the public domain. Not every data file was read: UNVERIFIED for the rest | go |

No GPL, LGPL or AGPL package is in the closure. No copyleft exception is needed.

### Rejected, re-verified: psycopg

`psycopg 3.3.6` and `psycopg-binary 3.3.6` on PyPI JSON (fetched 2026-10-24) both declare `license_expression = LGPL-3.0-only`. Still rejected. SQLAlchemy 2.1.6 offers it as extras `postgresql` and `postgresql-psycopg`, which we do not request. The wheel file itself was not downloaded and read this time, so this is the PyPI JSON evidence only (the file read would confirm it; UNVERIFIED at file level).

### Not used

`pytest-postgresql` is not used (not examined). `psycopg2`, `asyncpg`, `greenlet`, `aiosqlite` and every other SQLAlchemy extra are not requested.

## 4. Licence file hashes (SHA-256, from the wheels read)

| Package | File in the wheel | SHA-256 |
|---|---|---|
| sqlalchemy 2.1.6 | `licenses/LICENSE` | `e862bb5b904fb5513c3e14288d7a25a12eb0ecc7847a0a965d310e9a955e7cb9` |
| alembic 1.20.1 | `licenses/LICENSE` | `6e68d94a03a0e3e327ddf3e86d1ebedc14eece3922022618fc246a36296fd0cc` |
| mako 1.4.3 | `licenses/LICENSE` | `8c1d171bc5d341ad9cd33d5e2c39b96a82c7bd49b3ae89a470c37e5822232054` |
| markupsafe 3.0.4 | `licenses/LICENSE.txt` | `489a8e1108509ed98a37bb983e11e0f7e1d31f0bd8f99a79c8448e7ff37d07ea` |
| typing-extensions 4.16.0 | `licenses/LICENSE` | `3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf` |
| pg8000 1.31.5 | `licenses/LICENSE` | `f63e001d98a1bff7a5ed37d87e3ecee239979b00b9176acc8cea8338f3bd0523` |
| scramp 1.4.18 | `licenses/LICENSE` | `2429a638bd9df049495c9f153dc504c4c5c9fda5803d9f8843e21baf5d2b00a3` |
| asn1crypto 1.5.1 | `LICENSE` (in `*.dist-info`, not `licenses/`) | `29c3425e5da53ab842273e5f2f2f068ce2e35df4260f7fa156aa1ac6bed02433` |
| python-dateutil 3.0.0 | `licenses/LICENSE` | `ba00f51a0d92823b5a1cde27d8b5b9d2321e67ed8da9bc163eff96d5e17e577e` |
| six 1.17.0 | `LICENSE` (in `*.dist-info`) | `4375ba20e2b9c6c4e7cad2940a628fd90e95cc3d50ee92aae755715d8ba1fbd0` |
| tzdata 2026.5 | `licenses/LICENSE` | `df58f69ea88683035d720b5375bcef0cf5aeaa98a1d098d572e0992c378deb46` |
| tzdata 2026.5 | `licenses/licenses/LICENSE_APACHE` | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |

Hashes were taken from the PyPI wheels that `pip download` fetched; they were not compared with the `uv.lock` hashes of a repository lock (no repository lock includes these packages yet). The dev must re-check them against the real `uv.lock` when the lock is generated. The checker reads the licence file from the installed distribution, so the platform wheel the CI installs must have the same file bytes (true for pure-Python wheels; sqlalchemy and markupsafe were read from the cp313 macOS wheel, so the Linux CI hash is UNVERIFIED; if the checker pins them differently, add an override-free path: both have `License-Expression` and need no pin).

## 5. Overrides needed (exact and version-pinned)

Only two packages have metadata that the checker cannot parse. All others pass on `License-Expression` or a bare SPDX `License` field.

## 6. Recommended wheel metadata and policy block

### 6.1 `requires_dist` of the evaldb wheel

```
tracer-evalschema==0.0.1
sqlalchemy>=2.1.6,<3
alembic>=1.20.1,<2
pg8000>=1.31.5,<2 ; extra == 'postgres'
```

With `[project.optional-dependencies] postgres = ["pg8000>=1.31.5,<2"]` and `[project] dependencies = ["tracer-evalschema==0.0.1", "sqlalchemy>=2.1.6,<3", "alembic>=1.20.1,<2"]`. (Wheel METADATA writes the extra as `Requires-Dist: pg8000<2,>=1.31.5; extra == "postgres"`, and orders/normalises specifiers; the test must compare parsed `Requirement` objects, not strings.)

How the lower bounds were chosen: each is the highest release on PyPI on 2026-10-24 (SQLAlchemy 2.1.6, Alembic 1.20.1, pg8000 1.31.5), i.e. the values proposed in SCOPE D2. I did not run the suite, so I have no evidence that any lower version works; the lead lowers a bound only when the whole suite passes at the lower version, per D2. Facts that constrain a lower bound: the plan needs `sqlite_strict` (added in SQLAlchemy 2.0.37 per SCOPE P2-10, not re-verified by me), and alembic 1.20.1 itself requires only `SQLAlchemy>=2.0`. Upper bounds are `<3`, `<2`, `<2`, the next major. Licence of other versions inside the range: only the exact versions in section 3 were read. A later release can change a licence; the locked-closure check and the exact-version override pins catch that on every lock change.

The lower-bound CI leg must use `uv sync --resolution lowest-direct` (or an equivalent that lowers only the direct requirements). Full `--resolution lowest` was tried in the scratch project and fails: it picks an ancient transitive (`myghtyutils 0.5`, a very old Mako-era dependency) whose build fails. That leg then reads the same 11-package closure (the `lowest-direct` run resolved identically to the highest run).

### 6.2 `[runtime.evaldb]` for `docs/security/license-policy.toml`

The checker (`scripts/check_licenses.py`) does not read this section yet, and the existing `[runtime]` table has `closure` and `requires_dist` keys that are still valid next to a sub-table. The dev extends the checker (P2-02 deliverable). The `extras_postgres` list is separate so the base-closure test stays tight.

```toml
[runtime.evaldb]
# Runtime closure of tracer-evaldb (workspace member tracer-evalschema excluded; its closure is
# the [runtime] table above). Extras are listed separately. Any change must be a conscious edit.
closure = [
    "alembic",
    "annotated-types",
    "mako",
    "markupsafe",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "sqlalchemy",
    "typing-extensions",
    "typing-inspection",
]
extras_postgres = [
    "asn1crypto",
    "pg8000",
    "python-dateutil",
    "scramp",
    "six",
    "tzdata",
]
# Requires-Dist of the built wheel (compare as parsed requirements).
requires_dist = [
    "tracer-evalschema==0.0.1",
    "sqlalchemy>=2.1.6,<3",
    "alembic>=1.20.1,<2",
    "pg8000>=1.31.5,<2; extra == 'postgres'",
]

[[overrides]]
name = "pg8000"
version = "1.31.5"
spdx = "BSD-3-Clause"
scope = "runtime"
license_file = "licenses/LICENSE"
license_file_sha256 = "f63e001d98a1bff7a5ed37d87e3ecee239979b00b9176acc8cea8338f3bd0523"
reason = "License field is the non-SPDX text 'BSD 3-Clause License' and the only classifier is 'BSD License'; LICENSE is the 3-clause text. Optional extra [postgres] of tracer-evaldb."

[[overrides]]
name = "python-dateutil"
version = "3.0.0"
spdx = "Apache-2.0 AND BSD-3-Clause"
scope = "runtime"
license_file = "licenses/LICENSE"
license_file_sha256 = "ba00f51a0d92823b5a1cde27d8b5b9d2321e67ed8da9bc163eff96d5e17e577e"
reason = "License field is 'Dual License' (non-SPDX); LICENSE holds an Apache-2.0 notice for post-2017 code and the BSD-3-Clause text for all code. Transitive of pg8000 (extra [postgres])."
```

Notes for the dev: (a) the `[[overrides]]` blocks go in the existing overrides section, not under `[runtime.evaldb]` (TOML scoping: keep them after the `[runtime.evaldb]` table is closed, i.e. place them with the other `[[overrides]]`); (b) the existing `[runtime]` expected-closure check compares the whole `tracer-evalschema` runtime closure, so the evaldb check must be per package (`--package tracer-evaldb`), as noted in P1-02 section 2; (c) when the lock moves `python-dateutil` to a 2.x release, tzdata drops out of the lock, the override pin fails on the version, and the policy needs a conscious edit, which is the intended behaviour; (d) dev-only packages added by evaldb tests (for example a Postgres client used only by CI, if any) are not in this list and need their own review.

## 7. Other items asked for

| Item | Finding | Evidence |
|---|---|---|
| Postgres server | PostgreSQL licence (permissive, BSD-like). Not distributed by us | `COPYRIGHT` of the `postgres/postgres` repository (fetched 2026-10-24) begins "PostgreSQL Database Management System ... Permission to use, copy, modify, and distribute this software ..."; I read the first 8 lines only. The licence text is not an SPDX id in the project allowlist; the server is not a Python dependency, so the allowlist does not apply |
| Docker image `postgres` | The image scripts in `docker-library/postgres` are MIT ("Copyright (c) 2014, Docker PostgreSQL Authors"). The Debian variant (`16/bookworm`) is built `FROM debian:bookworm-slim` and downloads `gosu`, which is Apache-2.0 (LICENSE header read). The image also carries Debian packages with many component licences (including GPL ones), and the Alpine variant carries Alpine package licences. Those components are not distributed by tracer: CI pulls the image at job time, the repository and wheels ship none of it. Not read: the per-package copyright files inside the image (UNVERIFIED) | The Dockerfile first lines; the image digest chosen in P2-03 does not change this |
| GitHub Actions | `actions/checkout` and `astral-sh/setup-uv` are already used by CI, pinned by SHA. I did not read their licences in Phase 2 (the P1-02 audit does not list them either): UNVERIFIED. Any new action for the Postgres job (SCOPE section 4.2 starts the database in a job step, so possibly none) needs its own review before use | `.github/workflows/ci.yml` |
| Python 3.15 | The CI has a 3.15 leg. I resolved only `>=3.11` universally and on 3.14.7 for the highest run; I did not install on 3.15 or check that markupsafe and sqlalchemy publish 3.15 wheels or build C extensions there. UNVERIFIED | |
| Known-vulnerability scan | Not part of P2-02; not run. P2-24 covers the audit | |
| Verbatim copying | Not applicable here (no source copied). Not examined | |

## 8. Unverified items (summary)

1. Linux and Windows wheel licence files of sqlalchemy and markupsafe (only cp313 macOS arm64 read).
2. psycopg licence at file level (PyPI JSON only).
3. Licence of GitHub Actions in use.
4. Debian and Alpine component licences inside the postgres image.
5. tzdata data files other than the three read.
6. Python 3.15 availability of wheels.
7. Whether `sqlite_strict` exists from SQLAlchemy 2.0.37 (SCOPE statement; not re-verified).
8. Licence files of other versions in the ranges (only the versions in section 3 were read).

## 9. Gate

Non-blocker. All 11 packages in the resolved closure are permissive; two (pg8000, python-dateutil) need version-pinned overrides because their metadata is not SPDX, and tzdata is a closure member the SCOPE list missed. Required before P2-02 closes: add the `[runtime.evaldb]` block and both overrides, and re-check the file hashes against the real `uv.lock` wheels.
