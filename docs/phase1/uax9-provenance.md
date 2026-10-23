# P1-04e: clean-room UAX #9 reference, provenance and python-bidi removal proposal

Status: implemented, passing. python-bidi was removed on 2026-10-22 on the project owner's approval (the list below is what was done).

## What was built

A pure-Python (stdlib only), test-only implementation of the Unicode Bidirectional Algorithm in
`packages/evalschema/tests/support/uax9/` (`algorithm.py`, `ucd.py`, `__init__.py`, `data/`).
It is not in `src/`, so it is not in the wheel. `packages/evalschema/pyproject.toml` excludes
`/tests/support` and `/tests/test_uax9_*.py` from the sdist (hatchling's default sdist includes
`tests/`); `test_uax9_packaging.py` builds both artifacts and asserts that no path contains `uax9`.

Covered: P1-P3, X1-X10 (embeddings, overrides, isolates LRI/RLI/FSI/PDI, isolating run sequences,
max_depth 125 with overflow counters), W1-W7, N0 (BD16 bracket pairs, 63-element stack,
U+2329/U+232A = U+3008/U+3009), N1-N2, I1-I2, L1 (removed characters are skipped, so explicit
formatting characters between trailing white space and the separator or line end do not break the
sequence), L2, and the mirroring half of L4 for `get_display`. Out of scope: L3, shaping, line
wrapping, HL2-HL6.

API: `resolve_classes(classes, base_level=None, brackets=None) -> Resolved(paragraph_level, levels,
order)` (`levels[i]` is `None` for characters removed by X9), `resolve(text, base_dir)`,
`get_display(text, base_dir)` (removed characters are omitted; mirrored at odd levels),
`get_base_level(text, base_dir)`.

The seam: `tests/bidi_conformance.py` has a new `load_uba()`. `uba_visual`, `conformance()` and
the two runners use it. Default is the clean-room implementation; `TRACER_UBA_IMPL=python-bidi`
selects python-bidi for comparison only. `load_reference()` is unchanged (python-bidi), so
`test_reference_unicode_version_matches_vendored_files` (which pins python-bidi 0.6.12) is unaffected.

## Sources used (clean room)

Only these:

1. Unicode Standard Annex #9, Unicode 16.0.0, revision 50, 2024-09-02,
   https://www.unicode.org/reports/tr9/tr9-50.html, fetched 2026-10-22 with curl (the latest
   published revision that day was 52, for Unicode 18.0.0; revision 50 matches the 16.0.0 data).
   Sections read: BD1-BD16, 3.3 (P, X, W, N, I rules), 3.4 (L1-L4 and its examples).
2. Unicode Character Database 16.0.0 files, vendored unmodified in
   `packages/evalschema/tests/support/uax9/data/` with `provenance.json` (URL, retrieval date,
   bytes, sha256) and `UNICODE-LICENSE-V3.txt` (Unicode License V3, SPDX Unicode-3.0):
   `extracted/DerivedBidiClass.txt` (Bidi_Class, with the `@missing` defaults),
   `BidiBrackets.txt`, `BidiMirroring.txt`. Downloaded with curl into a scratch directory, treated
   as untrusted, sha256 of DerivedBidiClass.txt equals the one already recorded in
   `tests/fixtures/unicode/provenance.json`.
3. The headers of `BidiTest.txt` and `BidiCharacterTest.txt` (file formats) and those files as
   test data (qa's vendored copies, Unicode 16.0.0).

Not used, not read: python-bidi (including `.venv/.../site-packages/bidi`), the Rust crate
unicode-bidi, FriBidi, ICU, the UAX #9 reference implementations or any other bidi source code.
The author did not consult them before or during implementation. The only contact with
python-bidi in this task is the optional runtime comparison through `TRACER_UBA_IMPL`.

## Validation

* `BidiCharacterTest.txt`: 91,707 of 91,707 data lines pass (paragraph level, every resolved level,
  and the visual order, compared directly by `test_uax9_conformance.py`).
* `BidiTest.txt`: 490,846 of 490,846 data lines pass, in all 770,241 paragraph-level variants
  (auto, LTR, RTL), levels and order compared directly.
* Through qa's harness (display-string comparison): both files 100 percent, 0 failed variants;
  isolate lines 274,412/274,412 and 39/39; embedding/override lines 305,180/305,180 and 81/81.
* Negative controls: breaking L1 for isolates fails BidiTest; breaking N0 fails BidiCharacterTest.
* Unit tests per rule family (`test_uax9_rules.py`, expectations derived from the UAX #9 text),
  data tests (`test_uax9_data.py`), packaging (`test_uax9_packaging.py`).

Performance (macOS arm64, CPython 3.13.15): direct run of both files 6 s (BidiCharacterTest 1 s,
BidiTest 5 s, about 860,000 resolutions, about 7 microseconds each for short lines); the
qa-harness run of both files 7.4 s. Whole `test_uax9_*` + `test_bidi_conformance` set about 25 s
on 3.11, 3.12, 3.13 and 3.14.

Environments run: macOS arm64 (Darwin 25.5.0) only; CPython 3.11.16, 3.12.14, 3.13.15, 3.14.7.
Not run: CPython 3.15, Linux, x86_64.

Data note: Python 3.13's `unicodedata` (Unicode 15.1) differs from the 16.0.0 file for six code
points (U+1171E, U+1D6C1, U+1D6FB, U+1D735, U+1D76F, U+1D7A9); `test_uax9_data.py` allows exactly
those on older Pythons and requires full agreement on a Python with Unicode 16.0.

## python-bidi removal proposal (NOT done; needs project-owner approval)

Once approved, the following would be removed or changed:

1. `pyproject.toml`: the `bidi-ref` dependency group (`python-bidi==0.6.12`, lines 28-32) and its
   comment; `"python-bidi"` in `[tool.uv] no-build-package`. Then `uv lock` (removes the
   `python-bidi` package and the `bidi-ref` entries from `uv.lock`).
2. `docs/security/license-policy.toml`: the `python-bidi` `[[exceptions]]` entry (name, version,
   license-file sha256, `groups = ["bidi-ref"]`).
3. `scripts/check_licenses.py`: no python-bidi-specific code was found by grep; check once more
   after the lock change (the exception is data-driven by the policy file).
4. `.github/workflows/ci.yml`: the three comments about the opt-in `bidi-ref` group; the
   `--all-groups` flags can stay (they are harmless) or return to `uv sync --locked` for the test
   jobs and the license job.
5. Tests that pin python-bidi (these are existing tests, so qa must approve the edits):
   `test_repo_contract.py` (`test_ci_required_test_jobs_sync_bidi_ref_group`, the forbidden-set
   entry `"python-bidi"` near line 260, the bidi-ref group holder assertions near lines 279-296,
   `test_license_exceptions_exact` expecting `{"hypothesis", "pathspec", "python-bidi"}`),
   `test_bidi_conformance.py::test_reference_unicode_version_matches_vendored_files` (asserts
   python-bidi's version and lock entry) and the `reference` block of
   `tests/fixtures/unicode/provenance.json`.
6. `tests/bidi_conformance.py`: `load_reference()` and the `python-bidi` branch of `load_uba()`,
   `mirror_map`'s doc, and the module docstring.
7. Docs: ADR 0017 (Decision 4 exception text, the fallback-plan result, I16 exceptions list; add
   the P1-04e result), `docs/phase1/SCOPE.md` (license exceptions lists, P1-02/P1-04d/P1-04e rows,
   risk and acceptance items 9 and 16), `docs/phase1/api-surface.md` (items 17, 24, the test-support
   list), `docs/security/P1-02-licenses.md` (python-bidi rows and F1), `docs/adr/README.md` (item 24),
   `docs/phase1/evidence/red/*` (historical, leave as evidence).
8. The license exceptions would become `hypothesis` and `pathspec` only.
