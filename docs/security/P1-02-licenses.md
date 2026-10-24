# P1-02 license audit and allowlist (security-licensing)

Date of lookups: 2026-10-22. Auditor: security-licensing. Scope: `~/tracer/uv.lock` (30 package entries, 28 third-party plus the 2 workspace members), plus the test-only dependencies planned in `docs/phase1/SCOPE.md` (P1-02, section 5 tests, Dependencies).

Note 2026-10-22: `python-bidi` was later approved as a test-only exception and then removed again on the owner's approval, because the clean-room UAX #9 reference (`packages/evalschema/tests/support/uax9`) passes 100% of the Unicode conformance files (python-bidi 0.6.12 had failed 1,407 of 490,846 BidiTest lines). `uv.lock` has no GPL/LGPL/AGPL package and the policy has no such exception. The text below is the historical audit and is not rewritten.

Method. Every row below was checked against the PyPI JSON API (`https://pypi.org/pypi/<name>/<version>/json`) AND against the license text or metadata inside a downloaded file (wheel, or sdist when no suitable wheel was picked). Downloads were made into a throwaway directory, treated as untrusted, read with `python -I`, never installed except `python-bidi` into a throwaway venv for a behaviour probe. The SHA-256 of each downloaded file was compared with the PyPI digest and with the hash recorded in `uv.lock` (all match; the one exception is `hatchling`, which is a build backend and is not in `uv.lock`). No known-vulnerability scan was run (not part of P1-02; see recommendation R7). Nothing in this report was guessed; unverified items are marked UNVERIFIED.

## 1. License allowlist policy

| Class | SPDX identifiers | Rule |
|---|---|---|
| Allowed (runtime and dev) | `Apache-2.0`, `MIT`, `MIT-0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `PSF-2.0`, `Unicode-3.0` (data/ICU), `0BSD` | Pass. |
| Dual license (`A OR B`) | e.g. `Apache-2.0 OR BSD-2-Clause` | Pass if at least one alternative is allowed (choice is ours). `AND` requires every term allowed. |
| Test-only exception | `MPL-2.0` | Allowed only for packages in the explicit exception list (section 1.1), only in dev/test groups, never in runtime metadata, never vendored or modified. |
| Blocker | `GPL-*`, `AGPL-*`, `LGPL-*`, `SSPL`, `BUSL`, "non-commercial" and any custom license | Fail CI. LGPL is not on the list: treated as a blocker even test-only, unless the lead grants a written exception. |
| Blocker | missing, empty, ambiguous or unmappable (classifier only, "BSD", "Apache 2.0", "UNKNOWN") | Fail CI until a human reads the actual license file and records an override (section 4, override table) pinned to name, version and license-file SHA-256. |

The metadata is evaluated in this order: PEP 639 `License-Expression`, then short `License` field, then `License ::` classifiers. If these disagree or are empty, the package is "unclear" until an override exists.

### 1.1 Exceptions and overrides proposed (need lead sign-off)

| Id | Package | Version | License | Scope | Basis |
|---|---|---|---|---|---|
| E1 | `pathspec` | 1.1.1 | MPL-2.0 | dev only (transitive of `mypy`) | LICENSE file is Mozilla Public License 2.0. File-level copyleft; used unmodified as a tool, not distributed in the wheel. |
| E2 | `hypothesis` | 6.170.5 | MPL-2.0 | test only | `License-Expression: MPL-2.0`; LICENSE.txt says all code is MPL-2.0 except where noted. Used unmodified, not vendored. |
| O1 | `rfc8785` | 0.1.4 | Apache-2.0 (override) | runtime | Metadata has only the classifier "Apache Software License" and no License field or expression. See section 2.1. |
| O2 | `colorama` | 0.4.6 | BSD-3-Clause (override) | dev (Windows only, pytest) | No License field; classifier "BSD License"; LICENSE.txt is the 3-clause text. |
| O3 | `nodeenv` | 1.11.0 | BSD-3-Clause (override) | dev (pre-commit) | License field is bare "BSD"; LICENSE is the 3-clause text. |
| O4 | `sortedcontainers` | 2.4.0 | Apache-2.0 (override) | test only (hypothesis dep) | License field is "Apache 2.0" (non-SPDX); LICENSE file states "Licensed under the Apache License, Version 2.0". |
| O5 | `distlib` | 0.4.4 | PSF-2.0 | dev | License field "PSF-2.0" is fine; listed because the LICENSE file is the PSF history text (OK). |
| O6 | `python-discovery` | 1.6.4 | MIT (override) | dev | License field contains the MIT text instead of an identifier; classifier MIT. |
| O7 | `pathspec` | 1.1.1 | see E1 | | Classifier only, no expression; LICENSE file is the MPL-2.0 text. |

O5 is informational (no override needed). Overrides O1-O4, O6, O7 must be pinned to the license-file SHA-256 (section 4).

## 2. Dependencies in `uv.lock`

Closure check: `uv export --locked --package tracer-evalschema --no-dev --no-emit-project --no-hashes --format requirements.txt --offline` yields exactly 6 runtime packages: `annotated-types==0.8.0`, `pydantic==2.14.1`, `pydantic-core==2.50.1`, `rfc8785==0.1.4`, `typing-extensions==4.16.0`, `typing-inspection==0.4.4`. Everything else is dev-group only (root `tracer-workspace` dev group). Note: `--no-emit-workspace` on the root yields nothing because the root has no runtime dependencies; the `--package tracer-evalschema` form is required.

Source column: PyPI = `https://pypi.org/pypi/<name>/<version>/json`; file = the license metadata or text file read from the named artifact.

| Package | Version | Scope | Declared license (package metadata) | Verified in file | Result |
|---|---|---|---|---|---|
| annotated-types | 0.8.0 | runtime | MIT (License-Expression) | wheel METADATA + `licenses/LICENSE` | allow |
| pydantic | 2.14.1 | runtime | MIT (License-Expression) | wheel METADATA + `licenses/LICENSE` | allow |
| pydantic-core | 2.50.1 | runtime | MIT (License-Expression) | cp313 macOS wheel METADATA + `licenses/LICENSE` (bundled Rust crate licenses NOT audited) | allow |
| rfc8785 | 0.1.4 | runtime | none (classifier "License :: OSI Approved :: Apache Software License" only; License field empty) | wheel `dist-info/LICENSE` and sdist `LICENSE`, byte-identical | allow via O1 (Apache-2.0 text, section 2.1) |
| typing-extensions | 4.16.0 | runtime | PSF-2.0 (License-Expression) | wheel METADATA + `licenses/LICENSE` | allow |
| typing-inspection | 0.4.4 | runtime | MIT (License-Expression) | wheel METADATA + `licenses/LICENSE` | allow |
| ast-serialize | 0.13.1 | dev (mypy) | MIT (License-Expression) | sdist PKG-INFO + LICENSE; bundles Ruff crates (MIT; `ruff_annotate_snippets` MIT/Apache-2.0) | allow |
| cfgv | 3.5.0 | dev (pre-commit) | MIT (License field) | wheel METADATA + LICENSE | allow |
| colorama | 0.4.6 | dev (pytest, Windows) | none; classifier "BSD License" | wheel `licenses/LICENSE.txt` = BSD 3-clause text | allow via O2 |
| distlib | 0.4.4 | dev (virtualenv) | PSF-2.0 (License field) | wheel METADATA + LICENSE.txt (PSF text) | allow |
| filelock | 4.2.4 | dev | MIT (License-Expression) | wheel METADATA + LICENSE | allow |
| identify | 2.6.20 | dev (pre-commit) | MIT (License field) | wheel METADATA + LICENSE (vendored SPDX list data in `identify/vendor/licenses.py`, data only) | allow |
| iniconfig | 2.3.1 | dev (pytest) | MIT (License-Expression) | wheel METADATA + LICENSE | allow |
| librt | 0.16.0 | dev (mypy) | MIT (License-Expression) | cp313 macOS wheel METADATA + LICENSE | allow |
| mypy | 2.4.0 | dev | MIT (License-Expression) | wheel METADATA + LICENSE; also ships typeshed (Apache-2.0 per its LICENSE) | allow |
| mypy-extensions | 1.1.0 | dev (mypy) | wheel METADATA: `License-Expression: MIT`; PyPI JSON shows no expression (discrepancy between JSON and wheel) | wheel METADATA + LICENSE | allow |
| nodeenv | 1.11.0 | dev (pre-commit) | "BSD" (License field), classifier BSD | wheel LICENSE = BSD 3-clause text | allow via O3 |
| packaging | 26.3 | dev (pytest) | Apache-2.0 OR BSD-2-Clause | wheel METADATA + LICENSE, LICENSE.APACHE, LICENSE.BSD | allow |
| pathspec | 1.1.1 | dev (mypy) | none; classifier "Mozilla Public License 2.0 (MPL 2.0)" | wheel `licenses/LICENSE` = MPL-2.0 text | allow only via E1 |
| platformdirs | 4.13.5 | dev | MIT (License-Expression) | wheel METADATA + LICENSE | allow |
| pluggy | 1.7.0 | dev | MIT (License field) | wheel METADATA + LICENSE | allow |
| pre-commit | 4.7.0 | dev | MIT (License field) | wheel METADATA + LICENSE | allow |
| pygments | 2.21.0 | dev (pytest) | BSD-2-Clause (License-Expression) | wheel METADATA + LICENSE | allow |
| pytest | 9.1.1 | dev | MIT (License-Expression) | wheel METADATA + LICENSE | allow |
| python-discovery | 1.6.4 | dev (virtualenv) | License field holds the MIT text; classifier MIT | wheel LICENSE (MIT text) | allow via O6 |
| pyyaml | 6.0.3 | dev (pre-commit) | MIT (License field) | cp313 macOS wheel METADATA + LICENSE (MIT text) | allow |
| ruff | 0.17.1 | dev | MIT (License-Expression) | sdist PKG-INFO + LICENSE; bundled crates MIT / Apache-2.0 | allow |
| virtualenv | 21.14.12 | dev (pre-commit) | MIT (License-Expression) | wheel METADATA + LICENSE + THIRD-PARTY-NOTICES.md | allow |
| tracer-evalschema | 0.0.1 | workspace (published) | Apache-2.0 (`packages/evalschema/pyproject.toml`) | source | allow |
| tracer-workspace | 0.0.1 | workspace (not published) | Apache-2.0 (`pyproject.toml`) | source | allow |
| hatchling (build backend, NOT in uv.lock) | 1.32.4 | build | MIT (License-Expression) | wheel METADATA + LICENSE.txt | allow; see R5 |

Result: 0 GPL/AGPL/LGPL. 1 package needs the MPL-2.0 test-only exception today (pathspec, through mypy). 1 runtime package (rfc8785) needs a metadata override. No yanked release among these (PyPI `yanked=False` for all queried versions).

### 2.1 rfc8785 0.1.4 (runtime) in detail

- PyPI/wheel METADATA: no `License` field, no `License-Expression`, one classifier `License :: OSI Approved :: Apache Software License`. A metadata-only scanner therefore sees only a classifier.
- File read: wheel `rfc8785-0.1.4.dist-info/LICENSE` (10174 bytes) and sdist `rfc8785-0.1.4/LICENSE` are byte-identical, SHA-256 `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594`. The repo `main` LICENSE at `https://github.com/trailofbits/rfc8785.py` has the same SHA-256.
- Exact text found: begins "Apache License / Version 2.0, January 2004 / http://www.apache.org/licenses/ / TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" and ends at "END OF TERMS AND CONDITIONS". `diff -w -B` against `https://www.apache.org/licenses/LICENSE-2.0.txt` shows the only difference is that the "APPENDIX: How to apply the Apache License" boilerplate is absent. The terms are the unmodified Apache License 2.0. The file names no copyright holder; metadata author is "Trail of Bits <opensource@trailofbits.com>" (sdist `pyproject.toml` also says `license = { file = "LICENSE" }`).
- No NOTICE file in the wheel, sdist, or repo main (HTTP 404 for `NOTICE`), so Apache 2.0 section 4(d) imposes no NOTICE propagation. Our NOTICE should still list it (R6).
- Runtime dependencies: none (`dependencies = []`); `Requires-Dist` entries are all under extras.
- Conclusion: Apache-2.0. SCOPE's "believed Apache-2.0, unverified" is now verified.
- UNVERIFIED: runtime behaviour on CPython 3.14/3.15 (pure Python, `Requires-Python >=3.8`, the wheel is `py3-none-any`); that is covered by the CI matrix, not by this audit.

## 3. Test-only dependencies planned in SCOPE.md

Resolved in a throwaway uv project (`requires-python >=3.11`, versions pinned as below); that lock is in the scratchpad, not in the repo. Versions are the latest on PyPI at 2026-10-22.

| Package | Recommended pin | License (metadata -> file) | Verified at | Python 3.11-3.15 wheels | Go/no-go |
|---|---|---|---|---|---|
| cryptography | `50.0.2` (uploaded 2026-09-30) | `Apache-2.0 OR BSD-3-Clause` (License-Expression) -> sdist and abi3 wheel carry `LICENSE`, `LICENSE.APACHE`, `LICENSE.BSD` | PyPI JSON; wheel `cryptography-50.0.2-cp311-abi3-manylinux_2_28_x86_64.whl` and sdist | abi3 wheels from cp311, also cp314/cp315 wheels; no system library needed on Linux/macOS wheels. Wheel statically bundles OpenSSL; no separate OpenSSL license file is shipped in the dist-info (only the three above); bundled-OpenSSL license is UNVERIFIED in-wheel | GO |
| ... transitive `cffi` | `2.1.1` | `MIT-0` (License-Expression); LICENSE text states "MIT No Attribution" | PyPI JSON; cp313 macOS wheel | cp314 and cp315 wheels present | GO (add `MIT-0` to allowlist) |
| ... transitive `pycparser` | `3.12` | `BSD-3-Clause` | PyPI JSON; wheel | py3-none-any | GO |
| jsonschema | `4.26.0` (2026-01-07) | `MIT` (License-Expression) -> `licenses/COPYING` MIT text | PyPI JSON; wheel | py3-none-any, `Requires-Python >=3.10` | GO. Do not add the `[format]` extra (pulls more packages, not audited). |
| ... transitive `attrs` | `26.1.0` | MIT | PyPI JSON; wheel | any | GO |
| ... transitive `jsonschema-specifications` | `2025.9.1` | MIT (COPYING) | PyPI JSON; wheel | any | GO |
| ... transitive `referencing` | `0.37.0` | MIT (COPYING) | PyPI JSON; wheel | any | GO |
| ... transitive `rpds-py` | `2026.9.1` | MIT (LICENSE) | PyPI JSON; cp313 macOS wheel | cp314 and cp315 wheels present | GO |
| hypothesis | `6.170.5` (2026-10-20, 2 days old at audit; releases are very frequent) | `MPL-2.0` (License-Expression) -> `licenses/LICENSE.txt`: "All code in this repository except where explicitly noted otherwise is released under the Mozilla Public License v 2.0", full MPL text follows | PyPI JSON; cp313 macOS wheel | wheels for cp314 and cp315 present, `Requires-Python >=3.11` | CONDITIONAL GO: only with exception E2 signed off; use unmodified, never vendor or copy its code into the package |
| ... transitive `sortedcontainers` | `2.4.0` | License field "Apache 2.0" (non-SPDX) + classifier; LICENSE text "Licensed under the Apache License, Version 2.0" | PyPI JSON; wheel | any | GO via override O4 |
| PyYAML | `6.0.3` (already in `uv.lock` via pre-commit; 6.0.3 is also the latest on PyPI) | `MIT` (License field) -> wheel `licenses/LICENSE` MIT text | PyPI JSON; cp313 macOS wheel | cp314 wheels present (18); NO cp315 wheel in the lock/PyPI listing, so the optional non-blocking 3.15 job builds from sdist (already true today through pre-commit; build success UNVERIFIED) | GO. Use `yaml.safe_load` only (ruff S506 is already selected). |

### 3.1 Unicode bidirectional algorithm reference

| Candidate | Version | License (verified) | Maintenance | UAX #9 isolates (U+2066-U+2069) and overrides | System library / CI risk | Go/no-go |
|---|---|---|---|---|---|---|
| PyICU | 2.16.2 (2026-03-20) | `License: MIT` (OSAF-style MIT text in `LICENSE`, sdist) ; classifier is only "License :: OSI Approved". ICU itself is Unicode license (UNVERIFIED here; not shipped by PyICU) | Maintained (release in 2026) | Exposes ICU `UBiDi` (`setPara`, `getLevels`, `writeReordered`, `getVisualMap`, reordering options; verified by reading `bidi.cpp`). Whether isolates and overrides are honoured depends on the ICU version installed; ICU support for UBA isolates is from my background knowledge, UNVERIFIED in this session | Sdist only (0 wheels on PyPI). Needs C++ compiler, `pkg-config` and ICU headers (`setup.py` calls `icu-config`/`pkg-config icu-i18n`; README: `apt-get install pkg-config libicu-dev` on Debian, `brew install pkg-config icu4c` plus env vars on macOS). On this dev machine `pkg-config` is absent and no icu4c is under `/opt/homebrew/opt`, so `uv sync` would fail locally. The Unicode version is whatever the runner's ICU ships, not what `uv.lock` pins (reproducibility gap). Python 3.15 build UNVERIFIED | CONDITIONAL GO, see recommendation below |
| python-bidi | 0.6.12 (2026-10-20) | LGPL: classifier "GNU Library or Lesser General Public License (LGPL)"; `COPYING` (GPL-3.0 text) and `COPYING.LESSER` (LGPL-3.0 text) shipped in wheel and sdist; no License field | Actively maintained, wheels for cp310-cp315, Rust crate `unicode-bidi` under the hood | Probe in a throwaway venv: reorders correctly inside an RLI...PDI isolate and keeps the control characters in its output. Not conformance tested | None (wheels) | NO-GO: LGPL-3.0 fails the policy (SCOPE: "python-bidi only if license clears"; it does not). Not used even for tests. Do not copy its code either. |
| unicode-bidi-py | 0.1.0 (2023-10-13) | MIT / Apache-2.0 files, no metadata license | Abandoned stub; single `get_display` function; no wheels for cp313 or later | n/a | Would need a Rust toolchain on 3.13+ | NO-GO |
| icu4py | 1.2.0 (2026-10-20) | MIT (+ `LICENSE-ICU` = Unicode License V3) | Maintained | Modules in the wheel are `breakers`, `locale`, `messageformat`, `_version`; NO bidi module | Wheels bundle ICU 78.3 (no system library) | NO-GO for bidi (no API). Noted as a possible future route if it adds bidi. |
| pyfribidi 0.12.0 / pybidi 1.0 | | GPL / LGPL | Unmaintained since 2019 | | | NO-GO (license) |
| Pure-Python permissive package | none found | | | | | PyPI search is unavailable; I tried about 25 plausible names (`bidi`, `uax9`, `unicode-bidi`, `pyuba`, ...), all 404 or unrelated. Not exhaustive. |

Recommendation (decision for the lead, because SCOPE says "PyICU preferred" and the tests P1-04d must not be skipped):
1. Keep the UBA reference test-only and out of runtime metadata.
2. Do not make PyICU a default group member. Put it in a separate non-default group (for example `bidi-ref = ["PyICU==2.16.2"]`) and install it only in one dedicated CI job (`apt-get install -y pkg-config libicu-dev g++`, then `uv sync --locked --group bidi-ref`) so the main 3.11-3.14 matrix and developer laptops do not need ICU. Having PyICU in `uv.lock` does not build it unless the group is synced.
3. Because tests named `test_display_safe_account_number`, `test_display_safe_pdi_breakout` and `test_display_safe_marks_isolates` must run in the required matrix ("under the UBA reference"), the required legs need a reference that needs no system library. Recommended: an in-repo, test-only, pure-Python UBA reference written from the UAX #9 text by the SA, checked in CI against the Unicode `BidiCharacterTest.txt` and `BidiTest.txt` (both returned HTTP 200 from `https://www.unicode.org/Public/UCD/latest/ucd/`; they carry Unicode's terms of use; vendor them with provenance and a license note in NOTICE), and cross-checked against PyICU in the dedicated job. Original Apache-2.0 code; must not be derived from the LGPL/GPL implementations above (python-bidi, FriBidi) or from any copy-pasted code, which is the licensing risk to review at P1-21.
4. If the lead instead insists on PyICU for the required matrix, CI needs the apt step on every leg, the local-setup doc must say so, and the pin must be recorded together with the ICU version printed by the job (`icu.ICU_VERSION`, `icu.UNICODE_VERSION`) so results are traceable.

## 4. Proposed CI license scan

Tool: a small stdlib-only script `scripts/check_licenses.py` (SPDX header required, because `scripts/check_headers.py` enforces it), not a new third-party tool. Reason: the two things we need (rfc8785 empty metadata, MPL exceptions pinned per package) need an override table keyed by file hash; `pip-licenses 5.5.5` (MIT; depends on `prettytable`) was looked up but its handling of `License-Expression`, `--allow-only` matching and per-package exceptions was not verified, so I do not rely on it.

Behaviour:
1. Input: `uv export --locked --no-hashes --no-emit-workspace --format requirements.txt --offline` (all groups; `--package tracer-evalschema --no-dev --no-emit-project` for the runtime set), so the scan covers exactly what is locked, including dev packages. Run inside `uv run --locked` so `importlib.metadata` reads the synced environment.
2. For each distribution: read `License-Expression`; else short `License`; else classifiers. Evaluate SPDX `OR`/`AND`. Map aliases ("MIT License", "BSD", "Apache 2.0"). Anything not mapped to the allowlist is "unclear".
3. Exceptions and overrides in `docs/security/license-policy.toml` (new file), one entry per package: `name`, `version`, `spdx`, `scope` (`runtime`, `dev`, `test`), `license_file`, `license_file_sha256`, `reason`. An override applies only if the installed dist's license file hash matches; a version bump with a different hash re-triggers a failure.
4. Fail (exit 1, one line per offence, package, version, declared license): any GPL/AGPL/LGPL/unknown license; any MPL-2.0 package not in the exception list or present in the runtime closure; any override with a hash mismatch; any installed distribution that is not in the lock; any stale policy entry.
5. Output the full table as a CI artifact (`license-report.md`) so P1-21 can attach it.

Exact changes for dev (I did not make them):

a. New files: `scripts/check_licenses.py`, `docs/security/license-policy.toml` (allowlist, E1, E2, O1-O4, O6 with SHA-256 values; the rfc8785 LICENSE SHA-256 is `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594`).

b. `~/tracer/pyproject.toml` dependency groups:
```
[dependency-groups]
test = [
    "cryptography==50.0.2",
    "jsonschema==4.26.0",
    "hypothesis==6.170.5",
    "pyyaml==6.0.3",
]
dev = [
    "tracer-evalschema",
    { include-group = "test" },
    "pytest==9.1.1",
    "ruff==0.17.1",
    "mypy==2.4.0",
    "pre-commit==4.7.0",
]
# Only if the lead picks PyICU; NOT part of default groups; needs system ICU.
bidi-ref = ["PyICU==2.16.2"]
```
Then `uv lock`. Expected new lock entries: attrs 26.1.0, cffi 2.1.1, cryptography 50.0.2, hypothesis 6.170.5, jsonschema 4.26.0, jsonschema-specifications 2025.9.1, pycparser 3.12, referencing 0.37.0, rpds-py 2026.9.1, sortedcontainers 2.4.0 (resolved in my throwaway project; the real resolution must be re-checked after `uv lock`). Nothing may be added to `[project] dependencies` in `packages/evalschema/pyproject.toml`; it must stay `pydantic==2.14.1` and `rfc8785==0.1.4`.

c. `~/tracer/.github/workflows/ci.yml`: add to the `lint` job (or a new `licenses` job, same SHA-pinned actions) after `uv sync --locked`:
```
- run: uv run --locked python scripts/check_licenses.py
- run: uv build --package tracer-evalschema --wheel --out-dir dist && uv run --locked python scripts/check_licenses.py --wheel dist
```
Make the runtime-metadata tests (section 5) run in the normal `pytest` step. If PyICU is chosen, add a separate job with the apt step described above.

d. Set `[tool.uv] default-groups = ["dev"]` explicitly so a non-default `bidi-ref` group is never synced by plain `uv sync --locked`.

## 5. Runtime-metadata check design

Both tests live in `packages/evalschema/tests/` (class C) and use only the stdlib plus `packaging` (already installed via pytest).

`test_runtime_deps_license_allowlist`:
1. Build the runtime closure from installed metadata: start at distribution `tracer-evalschema`, read `Requires-Dist`, drop entries whose marker needs an `extra` or does not evaluate true for the current environment (`packaging.requirements.Requirement.marker.evaluate({"extra": ""})`), recurse by normalised name. Expected closure, from the lock: `{annotated-types, pydantic, pydantic-core, rfc8785, typing-extensions, typing-inspection}`; the test asserts the closure equals this frozen set so any new runtime dependency fails and needs a conscious edit.
2. For each member, classify the license using the order in section 1 (expression, then short field, then classifiers) with the shared helper in `scripts/check_licenses.py` (import it by path or move the helper under `tests/`).
3. Pass only if every member maps to the allowlist, or has a policy override whose recorded license-file SHA-256 equals the hash of the file found in that distribution (`dist.files`, `dist.read_text`). Only `rfc8785` may rely on an override in the runtime closure; assert no MPL-2.0 package appears.
4. Also assert every `Requires-Dist` of `tracer-evalschema` is pinned with `==`.

`test_test_only_deps_absent_from_runtime_metadata`:
1. Forbidden set (normalised names): `cryptography, jsonschema, hypothesis, pyyaml, pyicu, python-bidi` plus their transitive closure computed from the `test` group (`attrs, cffi, pycparser, jsonschema-specifications, referencing, rpds-py, sortedcontainers`), minus packages that are legitimately runtime (`typing-extensions`).
2. Assert the runtime closure from step 1 above does not intersect the forbidden set.
3. Assert `tracer-evalschema` metadata has no `Provides-Extra` and no extra-marked `Requires-Dist` pointing at the forbidden set.
4. Same assertions on a built wheel (CI step above): open `dist/*.whl`, parse `*.dist-info/METADATA`, so the check is on shipped metadata, not only the editable install. The wheel's `Requires-Dist` must be exactly `pydantic==2.14.1` and `rfc8785==0.1.4`.
5. Cross-check against `uv.lock`: parse the lock (`tomllib`), take the `tracer-evalschema` package entry's `dependencies`, and assert it equals `{pydantic, rfc8785}` (the lock currently shows exactly these two).

## 6. Findings and blockers

| # | Severity | Item | Recommended fix |
|---|---|---|---|
| F1 | Blocker (decision) for the bidi reference | `python-bidi` 0.6.12 is LGPL-3.0, fails the allowlist. PyICU has no wheels and needs system ICU, absent on this machine and not pinned by `uv.lock`; 3.15 build UNVERIFIED | Lead decision per section 3.1: in-repo pure-Python reference validated by Unicode conformance files, PyICU in a separate non-default group and CI job. Do not use python-bidi. |
| F2 | Needs sign-off | `pathspec` 1.1.1 (MPL-2.0, dev, via mypy) and `hypothesis` 6.170.5 (MPL-2.0, test) | Record exceptions E1 and E2 in `docs/security/license-policy.toml`; the gate "test-only exceptions listed" is then satisfied. |
| F3 | Non-blocker | `rfc8785` metadata has no license field; text verified Apache-2.0 | Override O1 pinned to LICENSE SHA-256; mention in NOTICE (R6). |
| F4 | Non-blocker | Several dev packages have non-SPDX or classifier-only metadata (colorama, nodeenv, sortedcontainers, python-discovery, pathspec, distlib); `mypy-extensions` shows a JSON-vs-wheel metadata discrepancy | Overrides O2-O4, O6, O7 with hashes; the scanner reads the wheel metadata, not the PyPI JSON. |
| F5 | Non-blocker | Test-only dependencies are not yet in `pyproject.toml` or `uv.lock`; P1-02 acceptance (runtime metadata lists only pydantic and rfc8785; every test-only license recorded) is therefore recorded here but not enforced in the repo | Apply section 4b. |
| F6 | Non-blocker | `hatchling==1.32.4` (build backend) is pinned in `packages/evalschema/pyproject.toml` but is not in `uv.lock`; build-time tool, MIT | Accept, or pin via `uv build --build-constraints`; listed for P1-21. |
| F7 | Non-blocker | `pyyaml` has no cp315 wheel; hypothesis was released 2 days before the audit | The 3.15 leg is non-blocking. Optionally set `[tool.uv] exclude-newer` to a date so brand-new releases are not picked up silently. |

Other recommendations: R5 pin the build backend; R6 list rfc8785 (Apache-2.0) and the bundled-license packages (typeshed, Ruff crates) in NOTICE only if we redistribute them (we do not today: only pydantic and rfc8785 are runtime and nothing is vendored); R7 run a vulnerability scan (for example `uvx pip-audit` against the exported requirements) at P1-21; this audit did not do it; R8 `check_headers.py` already requires SPDX headers on .py/.yml/.toml/.sh files; LICENSE (Apache-2.0 text) and NOTICE exist at the repo root and under `packages/evalschema/`.

## 7. Gate recommendation

NON-BLOCKER for the current lock (28 locked third-party packages: 0 GPL/AGPL/LGPL; runtime set is permissive; the two MPL-2.0 test/dev packages need the listed exception); BLOCKER for adding `python-bidi`, and a pending lead decision on the bidi reference before P1-04d starts.

## 8. Third-party data terms (P1-21 C2, i4)

Retrieved 2026-10-24 with curl from the official pages. This section records facts and short quotes only. It is not legal advice. "Allowed" means the terms we found permit what we do; "unclear" means we found no clear grant.

| Item | Source page (retrieved 2026-10-24) | Quote that matters | Conclusion |
|---|---|---|---|
| (a) `iso4217_active.json` | https://www.six-group.com/en/products-services/financial-information/data-standards.html | "make them available online and free of charge" | UNCLEAR |
| (a) | https://www.six-group.com/en/services/legal/terms-and-conditions/terms-of-use.html (SIX website terms) | "may not be reproduced or reused in any way or used for commercial purposes" | UNCLEAR (this is about website content; the XML file itself carries no licence text) |
| (b) `iana_subtag_registry_subset.json` | https://www.iana.org/help/licensing-terms (revised 2021-11-10) | "dedicate any applicable copyright rights that they may own in the Protocol Registries to the public domain" and "subject to the Creative Commons CC0 1.0 dedication" | ALLOWED, no attribution required (we still credit IANA). The Language Subtag Registry is linked from https://www.iana.org/protocols. |
| (c) Sarvam outputs in `tests/fixtures/real/sarvam-2026-10-23/` | https://www.sarvam.ai/terms-of-service (Version 2.0, effective 2026-07-29) | 17.4: "we assign to you our rights in the Output generated for you" (to the extent possible) | Text and JSON outputs: ALLOWED WITH CAVEAT (the assignment is "to the extent possible"; other users may get similar output) |
| (c) audio | same, section 10.2 | "distribute or otherwise commercially exploit any audio Output" "on a standalone basis (for example, as isolated files" "except as expressly permitted by the applicable Product-Specific Terms" | UNCLEAR for audio Output in a public repo. Resolved by not distributing it: the 10 Sarvam clips (the 9 `tts_*.wav` direct outputs and `short_300ms.wav`, a 0.3 s cut of `tts_hi.wav`) are removed from the working tree (the deletion lands with the pending clips commit); `clips-manifest.json` records their sha256, size, duration and how they were made, and the files stay local under the git-ignored `tests/fixtures/private/sarvam-clips/`. They remain in public git history (commit 9223d98); rewriting it is the owner's release-checklist decision. Product-Specific Terms were not found at any linked URL (https://www.sarvam.ai/api-terms returned 404; https://docs.sarvam.ai/ links no terms page). |
| (c) testing | same, section 10.5(a) | "develop, train, test, fine-tune or otherwise improve any machine learning algorithm or artificial intelligence system" | UNCLEAR. The fixtures test Tracer's parsers and mappers, not an AI system, but a later phase that scores STT or other models with these clips should be checked against this clause. |
| (d) Langfuse docs quotes (F2 fixture provenance) | https://raw.githubusercontent.com/langfuse/langfuse-docs/main/LICENSE | "MIT License" "Copyright (c) 2022-2025 Langfuse GmbH" | ALLOWED WITH ATTRIBUTION (MIT: keep the copyright and permission notice; NOTICE names it). The licence file is for the docs repository; the page on langfuse.com is assumed to be the same content, which we did not verify. |
| (d) Pipecat docs quotes (F5 fixture provenance) | https://raw.githubusercontent.com/pipecat-ai/docs/main/LICENSE | "BSD 2-Clause License" "Copyright (c) 2024-2025, Daily" | ALLOWED WITH ATTRIBUTION (BSD-2: keep the copyright notice and conditions; the F5 provenance says no Pipecat text is copied). |
| (e) Kokoro model weights and voices (synthetic audio fixtures `kokoro_*.wav`, Pipecat capture `f6_stereo.wav` files) | https://huggingface.co/hexgrad/Kokoro-82M (model card, front matter `license: apache-2.0`; retrieved 2026-10-24) | "With Apache-licensed weights, Kokoro can be deployed anywhere from production environments to personal projects." and "This is an Apache-licensed model" | ALLOWED (Apache-2.0). The card names no separate voice licence; the 26 voice style vectors of `voices-v1.0.bin` are the voices listed in `VOICES.md` of the same repository, which carries no licence statement of its own (grep for "licen", "apache", "cc by" finds only table column headers). Treated as under the model card licence; a per-voice grant is NOT verified. |
| (e) Kokoro training data | same card, "Training Details" and "Creative Commons Attribution" | "trained exclusively on permissive/non-copyrighted audio data"; CC BY audio used: Koniwa `tnc` (CC BY 3.0, under 1 h) and SIWIS (CC BY 4.0, under 11 h) | Recorded for completeness. The card lists these as attribution for the training set; it states no condition on generated audio. Whether CC BY attribution propagates to synthetic output is NOT verified here and the card does not claim it. |
| (e) ONNX packaging used by the capture | https://github.com/thewh1teagle/kokoro-onnx (README "License": "kokoro-onnx: MIT", "kokoro model: Apache 2.0"; `LICENSE` is "MIT License"); release `model-files-v1.0` notes that `kokoro-v1.0.onnx` is an "optimized f32 version from taylorchu/kokoro-onnx" | `taylorchu/kokoro-onnx` repository licence reported by the GitHub API as MIT | ALLOWED (MIT code, Apache-2.0 model). The release page states no licence for the converted ONNX file itself; it is read as the Apache-2.0 model in another format, which is an inference, not a stated grant. The sha256 of `kokoro-v1.0.onnx` and `voices-v1.0.bin` are recorded in the Pipecat `provenance.json` files. |
| (e) Tooling that produced the synthetic audio | the Pipecat capture `provenance.json` files (`tests/fixtures/real/pipecat-1.13.0/*/provenance.json`): `kokoro-onnx` 0.6.1, `espeakng-loader` 0.2.4, `phonemizer` 3.4.0; https://raw.githubusercontent.com/espeak-ng/espeak-ng/master/COPYING (eSpeak NG, "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007") | provenance: `phonemizer` GPL-3.0, libespeak-ng bundled by `espeakng-loader` GPL-3.0, `soxr` LGPL-2.1-or-later | GPL tools used at capture time only. They are not distributed by tracer: not in `uv.lock`, not among the files of the wheel or sdist (file lists in the P1-21 audit), no code copied. Only generated audio data is committed. Whether the licence of a GPL phonemizer reaches the audio it helped produce is NOT verified; the GPL text does not, on its face, cover program output unless the output contains program text, but this is a reading, not legal advice. |

Notes per item:

- (a) The SIX list page also states "SIX is the official Maintenance Agency" for ISO 4217 and the XML (Pblshd 2026-09-17) has no header comment or licence field. The data are factual codes, but we found no written reuse grant. We shipped codes, numeric codes, minor units and English names, de-duplicated across countries.
- (b) The registry file we hashed has File-Date 2026-09-17.
- (c) Kokoro clips (`kokoro_*.wav`) and the generated `silence_2s.wav` are not Sarvam output and stay in the tree. `short_300ms.wav` is derived from the Sarvam clip `tts_hi.wav` (its first 0.3 s), so it is treated as Sarvam audio Output and is one of the 10 clips that are not distributed (manifest `clips-manifest.json` beside the case folders). The Sarvam STT request and response JSON are the transcripts and metadata for invented sentences. The provenance says the fixtures contain no personal data.
- (d) The F2 provenance quotes are one-line attribute table rows and sentences. The OpenTelemetry semantic-conventions registry cited in the same provenance was not checked here.
- (e) Kokoro: licence verified on the model card and the kokoro-onnx README on 2026-10-24 (curl of the raw pages; the HuggingFace page was read as raw README, not rendered). Unverified items are marked in the table: the per-voice licence, any attribution duty arising from CC BY training data, the licence of the converted ONNX file, and any reach of the GPL tooling licences to the audio output. No Kokoro weight, voice file or GPL tool is stored in the repository or its distributions; only the 3 `kokoro_*.wav` clips and the Pipecat capture WAVs are.

Edits made for this section: `_meta.sources[0].license_note` in both data files (strings only), a `terms` note in the Sarvam `provenance.json`, and a third-party data notice in the root and package `NOTICE`.

### 8.1 Proposed replacement for the ISO 4217 data (not implemented)

If the owner prefers a source with a written licence: Unicode CLDR `common/supplemental/supplementalData.xml` carries `<currencyCodes type="AED" numeric="784"/>` (183 entries seen) and the fraction digits, and CLDR English names live in `common/main/en.xml`. The repository https://github.com/unicode-org/cldr says "All other content in this repo is released under UNICODE LICENSE V3" (checked 2026-10-24; the data files, not the LDML specification text). Unicode License v3 requires keeping the copyright and permission notice, so the NOTICE would gain one entry.

What would change: regenerate `iso4217_active.json` from CLDR via `scripts/generate_package_data.py`; `scripts/verify_language_data.py` would compare against CLDR instead of the SIX XML; `_meta.sources` and NOTICE change. Risks: CLDR names differ from SIX names, CLDR may lag the SIX list by months, and it has no fund flag or the `is_x_code` field, so those would need a rule or a second source. Values in the data file would change, which affects the golden and pricing tests. This needs a design decision.

### 8.2 Release checklist (open items)

1. SIX ISO 4217: no reuse licence found. Ask SIX (the contact link on the list page) for written confirmation, or adopt the CLDR replacement in 8.1, before the first public release.
2. Sarvam audio: done for the working tree. The 10 Sarvam clips (9 `tts_*.wav` and `short_300ms.wav`) are not distributed; `tests/fixtures/real/sarvam-2026-10-23/clips-manifest.json` replaces them (sha256, bytes, duration, generation) and the real files are kept locally under the git-ignored `tests/fixtures/private/sarvam-clips/`. Open: the files remain in public git history (commit 9223d98); removing them from history needs a force-push and is the owner's decision. Asking Sarvam for Product-Specific Terms that permit distribution remains optional.
3. Sarvam section 10.5(a): confirm that testing parsers on Output is outside the "test ... any AI system" bar, and re-check before any phase that benchmarks models with these clips.
4. Langfuse: confirm the langfuse.com pages carry the same MIT terms as the docs repository.
5. Kokoro (P1-21 N6, C7): confirm the unverified items of row (e) if the audio is ever redistributed outside tests: per-voice terms, CC BY attribution of the training data, the licence of the converted ONNX file, and the effect of the GPL capture tools on generated audio.
6. Legal review of the above is a one-line reminder only, per the owner.
