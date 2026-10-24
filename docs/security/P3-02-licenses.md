# P3-02 licence review of the Phase 3 runtime closures (security-licensing)

Date of lookups: 2026-10-25. Scope: the three new packages named in the decision log entry "PHASE 3 SCOPE DELIVERED" (2026-10-25): `tracer-evalcore` (stdlib and `tracer-evalschema` only), `tracer-evalproviders` (`httpx>=0.28.1,<1`) and `tracer-cli` (depends on evalcore, evalproviders and `tracer-evaldb`). `docs/phase3/` did not exist when this was written, so the decision log is the only source of the package list (SCOPE.md not read; UNVERIFIED against it). Nothing in the repository was changed except this file.

## 1. Method

- Closures resolved in throwaway projects under `<scratch>` (`uv lock`, `requires-python = ">=3.11"`). The repository `pyproject.toml` and `uv.lock` were not touched. Resolutions: (a) `httpx>=0.28.1,<1` alone, highest and `--resolution lowest-direct`; (b) a stand-in for the `tracer-cli` closure with the direct requirements `httpx>=0.28.1,<1`, `pydantic>=2.11,<3`, `rfc8785>=0.1.4,<0.2`, `sqlalchemy>=2.1.6,<3`, `alembic>=1.20.1,<2` and extra `pg8000>=1.31.5,<2`, highest and lowest-direct (workspace members cannot be resolved outside the repository, so their own requirements were written out by hand from the existing policy).
- Each wheel was downloaded from PyPI into its own directory under `<scratch>`, unzipped, and read (`METADATA`: `License-Expression`, `License`, classifiers, `Requires-Dist`; plus the file in `*.dist-info/licenses`). The files were treated as data and never imported or run. The repository's own classifier (`classify_metadata` in `scripts/check_licenses.py`) was run on the seven METADATA files and its verdicts are quoted below.
- Wheel availability: the stand-in cli closure was installed with `uv pip install --no-build --no-cache` into fresh virtual environments on CPython 3.11, 3.14.7 and 3.15.0rc2. All three succeeded with 23 packages and no source build.

## 2. httpx closure (resolved, identical in highest and lowest-direct)

| Package | Version tested | Required by | Python-version marker |
|---|---|---|---|
| httpx | 0.28.1 | evalproviders (direct) | none |
| httpcore | 1.0.9 | httpx (`httpcore==1.*`) | none |
| h11 | 0.16.0 | httpcore (`h11>=0.16`) | none |
| anyio | 4.15.1 | httpx | none |
| idna | 3.20 | httpx, anyio | none |
| certifi | 2026.10.23 | httpx, httpcore | none |
| typing-extensions | 4.16.0 | anyio (`typing_extensions>=4.16.0; python_version < "3.15"`) | Python < 3.15 only (still in the universal lock; on 3.15 pydantic keeps it in the cli closure anyway) |

- `sniffio` is not required: anyio 4.15.1 has no `sniffio` requirement and the lock has none. `exceptiongroup` (anyio, `python_version < "3.11"`) does not apply (our floor is 3.11).
- `lowest-direct` equals the highest resolution because 0.28.1 is the newest stable httpx (PyPI, checked 2026-10-25: latest 0.28.1, uploaded 2024-12-06). PyPI also lists `1.0.dev1` to `1.0.dev6` (the newest 2026-08-31). The bound `<1` excludes them and neither resolution picked one. They are pre-releases of a future major, a watch item.
- The wheel `httpx-0.28.1-py3-none-any.whl` has SHA-256 `d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad`, equal to the value PyPI lists. The transitive lower bounds (anyio, idna, certifi, h11, httpcore) are NOT lowered by `lowest-direct`; lower versions are not tested here (UNVERIFIED).

## 3. Verdict per package (httpx closure)

| Package | Version tested | SPDX as the package declares it | Checker verdict | Override needed | Class | Ships in our wheels |
|---|---|---|---|---|---|---|
| httpx | 0.28.1 | `License: BSD-3-Clause` (bare SPDX in the old field) plus classifier BSD | allowed (basis License) | no | permissive | no, dependency only |
| httpcore | 1.0.9 | `License-Expression: BSD-3-Clause` | allowed | no | permissive | no |
| h11 | 0.16.0 | `License: MIT` | allowed (basis License) | no | permissive | no |
| anyio | 4.15.1 | `License-Expression: MIT` | allowed | no | permissive | no |
| idna | 3.20 | `License-Expression: BSD-3-Clause` | allowed | no | permissive | no |
| typing-extensions | 4.16.0 | `License-Expression: PSF-2.0` | allowed (already in `[runtime]`) | no | permissive | no |
| certifi | 2026.10.23 | `License: MPL-2.0` (bare SPDX in the old field), classifier Mozilla Public License 2.0 | mpl (outside the allowlist) | no override (field is parseable); needs an exception (section 4) | weak copyleft (file-level) | no, dependency only |

Licence file evidence (SHA-256 of the file inside the wheel):

| Package | File | SHA-256 | What it is |
|---|---|---|---|
| httpx 0.28.1 | `licenses/LICENSE.md` | `4ec59d544f12b5f539a3a716fd321ac58ccd8030b465221f2c880200cdf28d8d` | BSD 3-clause ("Copyright 2019, Encode OSS Ltd") |
| httpcore 1.0.9 | `licenses/LICENSE.md` | `fdcb59154c74cbaba16a11242f7740bea9f23d6feb5547917d8c5f94a80392a5` | BSD 3-clause ("Copyright 2020, Encode OSS Ltd") |
| h11 0.16.0 | `licenses/LICENSE.txt` | `37db5bb85926db28a427a25867f10b1232003aea1be69ccb851138adb8e6f361` | MIT (Nathaniel J. Smith) |
| anyio 4.15.1 | `licenses/LICENSE` | `5361ac9dc58f2ef5fd2e9b09c68297c17f04950909bbc8023bdb82eacf22c2b0` | MIT (Alex Gronholm) |
| idna 3.20 | `licenses/LICENSE.md` | `1a9a4f0e3d479a27240ddd59a9137a66ab4a0f9dfdc8ca6188cc0bfd85187f04` | BSD 3-clause (Kim Davies) |
| typing-extensions 4.16.0 | `licenses/LICENSE` | `3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf` | PSF (same hash as in P2-02) |
| certifi 2026.10.23 | `licenses/LICENSE` | `e93716da6b9c0d5a4a1df60fe695b370f0695603d21f6f83f053e42cfc10caf7` | Mozilla CA bundle notice stating the source form is under MPL 2.0 (v. 2.0, lines 15 to 17); the same hash was read from certifi 2025.8.3 |

The httpx, httpcore and idna texts were read only in their first lines (copyright and the start of the 3-clause grant); the full conditions were not compared line by line with the BSD-3-Clause template (UNVERIFIED beyond the header). The h11 and anyio files were read to their first lines of the MIT grant only.

## 4. certifi (MPL-2.0): accepted by name, and a checker finding

Decision basis: the project owner said not to dwell on copyleft (CLAUDE.md, licensing standing rule). certifi is accepted by name, as a runtime dependency (of httpx and httpcore) of `tracer-evalproviders` and `tracer-cli`, used unmodified, never vendored, never copied into a tracer wheel. MPL-2.0 is file-level copyleft; it is triggered by distributing modified MPL files, which we do not do. The CA bundle data is Mozilla's; an embedded bundle is the reason the licence applies.

How the checker handles it (read from `scripts/check_licenses.py`, not run against the new lock, because that lock does not exist yet):

- `classify_metadata` returns class `mpl`, which is not in the allowlist, so certifi FAILS unless an `[[exceptions]]` entry exists for the exact name, version and licence file hash.
- `scope_problems` rejects an exception only when the package is in the `tracer-evalschema` runtime closure (`RUNTIME_PROJECT`), when it is reachable from a group not named in `groups`, or when it is not reachable from any group. certifi is not in the evalschema closure; it is reachable from group `dev` once `tracer-evalproviders` and `tracer-cli` are listed in the `dev` group (as `tracer-evaldb[postgres]` is today). So an exception with `groups = ["dev"]` passes the current scope rules without a checker change, provided those members are in `dev`. The default-group rule is applied only to class `copyleft`, not to `mpl`, which is why the `hypothesis` and `pathspec` exceptions already name `dev`.
- Artifact check (`--wheel`): an exception makes the check fail if the wheel's `Requires-Dist` names the excepted package or contains its `import_names`/`forbidden_files`. The evalproviders and cli wheels must therefore list `httpx`, not `certifi`, and must not set `import_names = ["certifi"]` wrongly; neither is in the blocks below. This is a statement from reading the code; the artifact path was not run.
- The exception is pinned to version `2026.10.23`. certifi releases several times a year, so every bump re-triggers review (intended). The licence file hash was identical at 2025.8.3, so only the version field should need a conscious edit.
- Finding (non-blocker, documentation): the exception field `scope = "runtime"` and `groups = ["dev"]` look contradictory to a reader. The `scope` value is only echoed in the report. The reason text below says so.

## 5. Full runtime closures of the three new packages

All names normalised. The workspace members themselves (`tracer-evalschema`, `tracer-evalcore`, `tracer-evalproviders`, `tracer-evaldb`) are excluded from the lists, as in the existing blocks.

| Package | Own requirements (from the decision log) | Third-party closure |
|---|---|---|
| tracer-evalcore | `tracer-evalschema==0.0.1` (stdlib otherwise) | annotated-types, pydantic, pydantic-core, rfc8785, typing-extensions, typing-inspection (identical to `[runtime]`) |
| tracer-evalproviders | `httpx>=0.28.1,<1` | anyio, certifi, h11, httpcore, httpx, idna, typing-extensions |
| tracer-cli | `tracer-evalcore==0.0.1`, `tracer-evalproviders==0.0.1`, `tracer-evaldb==0.0.1` | union of the above and the evaldb base closure: alembic, annotated-types, anyio, certifi, h11, httpcore, httpx, idna, mako, markupsafe, pydantic, pydantic-core, rfc8785, sqlalchemy, typing-extensions, typing-inspection |

Evidence: in the stand-in cli resolution (highest) the locked set is 22 third-party packages: the 16 above plus the postgres extra group asn1crypto, pg8000, python-dateutil, scramp, six, tzdata. Lowest-direct gave the same 22 package names; versions differed only for pydantic (2.11.0) and pydantic-core (2.33.0). Everything else is unchanged and already reviewed in P1-02 and P2-02.

Unverified or open:

1. Whether `tracer-evalproviders` also depends on `tracer-evalschema`/`tracer-evalcore` (the decision log lists `httpx` only). If it does, add the workspace pin to its `requires_dist` and the six evalschema packages to its closure.
2. Whether `tracer-cli` forwards the evaldb `[postgres]` extra. If it does, add `extras_postgres` as in `[runtime.evaldb]` (six packages) and the pg8000 line to its `requires_dist`. If it does not, the cli wheel must not declare any extra.
3. Whether the cli wheel carries the report HTML template, a script or font. Any bundled third-party file (for example a JavaScript library or icon set) has no review here. The decision log says the report uses "one hashed inline seek script", presumably our own; not verified.
4. The built wheels do not exist yet, so "ships in our wheels: no" is a plan statement; the `--wheel` check will confirm it.

## 6. Rejected, recorded

- `sarvamai` 0.1.37 (PyPI JSON read 2026-10-25): `license` is null, `license_expression` is null, and no licence classifier. Declared requirements: `httpx>=0.21.2`, `pydantic>=1.9.2`, `pydantic-core>=2.18.2`, `typing_extensions>=4.0.0`, `websockets>=12.0`. Rejected because the licence is undeclared and `websockets` would widen the closure; the Sarvam adapter uses the documented REST interface through httpx. The wheel itself was not downloaded, so whether it carries a licence file is UNVERIFIED.
- `requests` 2.34.2 (PyPI JSON): `license` is "Apache-2.0" with the Apache classifier, so the licence is not the reason. Rejected because it would add `charset_normalizer`, `urllib3`, `idna` and `certifi` for a second HTTP stack beside the decided `httpx` (decision Q2), with no async or timeout-granularity advantage that the scope needs. This is a design reason from the decision log, not a licence finding; its dependencies' licences were not read (UNVERIFIED).

## 7. Python 3.14 and 3.15 wheel availability

- httpx, httpcore, h11, anyio, idna, certifi and typing-extensions are all published as `py3-none-any` wheels (the files read above), so they cannot lack a 3.14 or 3.15 build. Their `Requires-Python` are `>=3.8`, `>=3.8`, `>=3.8`, `>=3.10`, `>=3.9`, `>=3.7`, `>=3.9`.
- The whole stand-in cli closure (including pydantic-core, markupsafe, sqlalchemy and, with the extra, pg8000 and friends) installed with `--no-build` on 3.11, 3.14.7 and 3.15.0rc2. On 3.15.0rc2 the installed pydantic is 2.14.1 and pydantic-core 2.50.1. This covers the platform of this machine only (macOS arm64). Linux and Windows wheels for markupsafe, pydantic-core and sqlalchemy were NOT checked here (UNVERIFIED); the existing CI matrix covers Linux for the evalschema/evaldb closure already.
- `no-build-package` in the root `pyproject.toml` lists compiled packages only; no new entry is needed since all new packages are pure Python.

## 8. Lowest-direct resolution

`uv lock --resolution lowest-direct` of `httpx>=0.28.1,<1` resolves httpx 0.28.1, httpcore 1.0.9, h11 0.16.0, anyio 4.15.1, idna 3.20, certifi 2026.10.23, typing-extensions 4.16.0: identical to the highest resolution, because 0.28.1 is the lowest allowed and the newest stable release. The lower-bound CI leg therefore adds no httpx-specific coverage beyond the highest leg. It is a tautology until httpx 0.28.2 or 1.0 ships (stated, not hidden). For the cli stand-in, the lowest-direct run is also valid with pydantic 2.11.0/pydantic-core 2.33.0 (a build for 3.15 of that old pydantic-core was not tested).

## 9. Policy blocks for `docs/security/license-policy.toml` (the dev adds them)

The checker today reads `[runtime]` and `[runtime.evaldb]` only. The dev must extend it (as for evaldb in P2-02) to read the three tables below and to compare each package's closure with `--package`. The names of the tables follow the request. The `requires_dist` lists follow the decision log; the workspace pins `==0.0.1` follow the evaldb precedent and are an assumption the dev must confirm against the real `pyproject.toml` files.

```toml
[runtime.evalcore]
# Runtime closure of tracer-evalcore (docs/security/P3-02-licenses.md). Workspace members excluded.
# The standard library plus tracer-evalschema only; any new third-party dependency is a conscious edit.
closure = [
    "annotated-types",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "typing-extensions",
    "typing-inspection",
]
requires_dist = [
    "tracer-evalschema==0.0.1",
]

[runtime.evalproviders]
# Runtime closure of tracer-evalproviders (docs/security/P3-02-licenses.md). Workspace members excluded.
# httpx and its dependencies only. sniffio is not required by anyio 4.15.1. typing-extensions is
# required by anyio only on Python < 3.15. certifi is MPL-2.0 (see [[exceptions]] below).
closure = [
    "anyio",
    "certifi",
    "h11",
    "httpcore",
    "httpx",
    "idna",
    "typing-extensions",
]
requires_dist = [
    "httpx>=0.28.1,<1",
]

[runtime.cli]
# Runtime closure of tracer-cli (docs/security/P3-02-licenses.md). Workspace members excluded.
# Union of the evalschema, evaldb base and httpx closures. No extra is declared (assumption, see
# section 5, open item 2: add extras_postgres and the pg8000 line if the cli forwards [postgres]).
closure = [
    "alembic",
    "annotated-types",
    "anyio",
    "certifi",
    "h11",
    "httpcore",
    "httpx",
    "idna",
    "mako",
    "markupsafe",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "sqlalchemy",
    "typing-extensions",
    "typing-inspection",
]
requires_dist = [
    "tracer-evalcore==0.0.1",
    "tracer-evalproviders==0.0.1",
    "tracer-evaldb==0.0.1",
]

[[exceptions]]
name = "certifi"
version = "2026.10.23"
spdx = "MPL-2.0"
scope = "runtime"  # dependency of httpx/httpcore in tracer-evalproviders and tracer-cli
groups = ["dev"]   # the checker's reachability group; the 'scope' text is documentary only
license_file = "licenses/LICENSE"
license_file_sha256 = "e93716da6b9c0d5a4a1df60fe695b370f0695603d21f6f83f053e42cfc10caf7"
decided_by = "project owner (standing rule: do not dwell on copyleft; accepted by name 2026-10-25)"
reason = "File-level weak copyleft. Used unmodified as a transitive dependency of httpx and httpcore, never vendored or copied into a tracer wheel and never named in a tracer Requires-Dist."

[[rejected]]
name = "sarvamai"
spdx = "none declared"
reason = "PyPI declares no licence (license and license_expression null, no classifier; 0.1.37). The Sarvam adapter uses the documented REST interface through httpx. See docs/security/P3-02-licenses.md."

[[rejected]]
name = "requests"
spdx = "Apache-2.0"
reason = "Not a licence rejection: a second HTTP stack beside the decided httpx (decision Q2) that adds charset_normalizer and urllib3. See docs/security/P3-02-licenses.md."
```

No `[[overrides]]` are needed for the httpx closure: all six permissive packages are classified `allowed` from their own metadata, and certifi's field parses (the exception, not an override, is the right mechanism). If the checker's `[[rejected]]` handling requires an `spdx` that is a real identifier, the dev should keep the free text; the existing entries use identifiers only, and the loader's behaviour for other text was not tested (UNVERIFIED).

Notes for the dev:

1. The existing evalschema `[runtime]` closure test compares the whole `tracer-evalschema` closure and is unaffected, since the new packages sit above it.
2. The `[[exceptions]]` entry fails with "stale exception" if certifi moves from 2026.10.23, which is intended; update the version and re-read the licence file at the same time.
3. `tracer-evalproviders` and `tracer-cli` must be added to the root `dev` dependency group (as `tracer-evaldb[postgres]` is), otherwise the certifi exception reports "not reachable from any dependency group".
4. Re-check every hash above against the real wheels in `uv.lock` once the lock exists; these hashes come from the wheels `pip download` fetched, not from a repository lock.

## 10. Other items from the brief, not examined

- Known-vulnerability scan: not run (P3-28 audit covers it). No advisory data was read for httpx, httpcore, h11, anyio, idna or certifi.
- Verbatim copying (Dograh, VoicEra): not applicable to this review; no source was compared.
- SPDX file headers: not examined in this task.
- Sarvam Terms (ToS 10.5(a), 17.5) are the owner gate P3-29 recorded in the decision log; they are a contract matter, not a package-licence matter, and were not assessed here.

## 11. Verdicts

| Package | Verdict |
|---|---|
| tracer-evalcore | go: closure equals the existing evalschema closure, all permissive, no new licence review |
| tracer-evalproviders | go with one exception entry: 6 permissive packages; certifi (MPL-2.0) accepted by name; no override needed |
| tracer-cli | go with the same certifi exception: the union closure of 16 third-party packages, all reviewed here or in P1-02/P2-02 |

Gate: non-blocker. Required before P3-02 closes: add the three `[runtime.*]` tables, the certifi exception and the two rejected entries; extend `scripts/check_licenses.py` to read the new tables; confirm open items 1 to 3 in section 5; re-check hashes against the real `uv.lock`.
