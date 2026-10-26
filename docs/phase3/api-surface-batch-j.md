# Phase 3 batch J contract: the gate conditions C1, C3, C7 and C12

Author: qa, 2026-10-26. Source: the decision log entry "PHASE 3 GATE" (staff-reviewer ab2a7b7921ec16456, HEAD 7b929ed; conditions C1, C3, C7, C12, all accepted by the lead), the tree of 2026-10-26, `.github/workflows/ci.yml`, `.gitleaks.toml`, `packages/cli/src/tracer_cli/report.py`, ADR 0004, ADR 0016. qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, workflow, script or earlier test except the named spec change of section 5. The lead auto-approves the exact names, text and wording (owner instruction, 2026-10-23); where the contract chose, the rejected option is in section 7.

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/` or `scripts/`, and nothing under them was edited. The tests are staged at

```text
docs/phase3/evidence/red/batch-j/staging/tests/cli/        -> packages/cli/tests/        (1 file)
docs/phase3/evidence/red/batch-j/staging/tests/evalcore/   -> packages/evalcore/tests/   (4 new files, 2 replaced files)
docs/phase3/evidence/red/batch-j/staging/tests/evalschema/ -> packages/evalschema/tests/ (1 file)
```

No `__init__.py`, no `conftest.py`. The dev's first act in batch J:

```text
git mv docs/phase3/evidence/red/batch-j/staging/tests/cli/*.py packages/cli/tests/
git mv docs/phase3/evidence/red/batch-j/staging/tests/evalcore/*.py packages/evalcore/tests/
git mv docs/phase3/evidence/red/batch-j/staging/tests/evalschema/*.py packages/evalschema/tests/
```

Two of the files in `evalcore/` replace an existing file of the same name (the named spec change of section 5); `git mv` onto them needs `-f`.

| File | Gate condition | Functions | Collected |
|---|---|---|---|
| `evalschema/test_adr0016_amendment_cites_test.py` | C1 | 11 | 23 |
| `evalcore/test_phase3_ci_postgres_cli.py` | C3 | 12 | 22 |
| `cli/test_cli_report_chunk_edge_note.py` | C7, C7b | 8 | 8 |
| `evalcore/test_gitleaks_canary_allowlist.py` | C12(a) | 4 | 4 |
| `evalcore/test_jsonschema_hard_import.py` | C12(b) | 4 | 4 |
| `evalcore/test_flags_document.py`, `evalcore/test_recorded_scoring.py` | C12(b), spec change | 0 new | 0 new |
| total new | | 39 | 61 |

Run: `uv run --locked pytest -q packages/cli/tests/test_cli_report_chunk_edge_note.py packages/evalcore/tests/test_phase3_ci_postgres_cli.py packages/evalcore/tests/test_gitleaks_canary_allowlist.py packages/evalcore/tests/test_jsonschema_hard_import.py packages/evalschema/tests/test_adr0016_amendment_cites_test.py packages/evalcore/tests/test_flags_document.py packages/evalcore/tests/test_recorded_scoring.py`.

All text is invented, the only key-shaped strings are the canary shape and decoys built from parts at run time (see section 4.1, finding F1), nothing opens a socket (the only subprocess is `pytest --collect-only`, which needs no database), nothing reads the machine's environment except an unset of `TRACER_TEST_POSTGRES_URL` for that subprocess.

## 1. C1: ADR 0016 Decision 1 amendment, I17, and ADR 0004 I41 and I42 cite existing tests

### 1.1 What the tests read

`test_adr0016_amendment_cites_test` (the name P3-00d and ADR 0016 Decision 1 and I17 give) and `test_adr0004_invariants_i41_i42_cite_only_existing_tests_and_say_nothing_is_pending` read the two ADRs as text, collect the names in backticks (`` `test_\w+` ``), and an AST scan of `packages/*/tests/test_*.py` gives the set of defined test functions. There is no `PENDING` allowance. They assert:

1. The Decision 1 amendment paragraph (the one line of ADR 0016 that holds `**What the agent's outputs are`, between `## Decision` and item 2) cites `test_metrics_evaluate_agent_outputs_only` and `test_adr0016_amendment_cites_test`, and both exist.
2. I17 (the line that starts `- I17. `) cites the same two names, and both exist.
3. I41 cites exactly `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit` and `test_sarvam_partial_chunk_failure_yields_no_transcript`; I42 cites exactly `test_empty_chunk_makes_no_turn_and_is_counted` and `test_detected_language_set_only_when_all_chunks_agree`; all four exist today.
4. None of these four items matches the stale-wording pattern `STALE` of the test file (below), and, for I41 and I42, the lowercased text holds neither `pending`, nor `lands`, nor ` land)`.
5. In the whole of ADR 0004 and of ADR 0016: no line that cites at least one test, all of whose cited tests exist, matches `STALE`.
6. I41 and I42 keep their `(revision 40, Decision 10a)` and `(revision 40, Decision 10b)` tags.

The pattern (case-insensitive, except `PENDING`): `pending until`; `until ... land(s)` on one line; `(Phase N, P3-NN ... ; pending`; `not (yet) written|defined|implemented`; `not written yet`; `yet to be written`; `to be written`; the capitals `PENDING`. A word such as "pending evidence" (P-57) or "lands on" does not match. The check has teeth: 20 collected tests feed synthetic items, each stale phrasing, a misspelt name, a dropped name, a missing item, and a duplicated item.

### 1.2 What the architect changes (exact wording constraints; the tests fail today for these lines)

| File, line | Today | Required |
|---|---|---|
| `docs/adr/0016-roles-caller-origin-privacy.md` I17 (line 96) | `... Tests: \`test_metrics_evaluate_agent_outputs_only\` (Phase 3, P3-18; pending until it lands), \`test_adr0016_amendment_cites_test\` (P3-00d).` | the clause `; pending until it lands` is removed: `(Phase 3, P3-18)`. No other wording about waiting. |
| `docs/adr/0004-requires-and-not-applicable.md` I41 (line 181) | `... (Phase 3, pending until P3-07 and P3-20 land).` | `(Phase 3, P3-07 and P3-20)` |
| `docs/adr/0004-requires-and-not-applicable.md` I42 (line 182) | `... (Phase 3, pending until P3-09 lands).` | `(Phase 3, P3-09)` |
| ADR 0016 Decision 1 amendment paragraph (line 12) | already cites both names and no stale wording | unchanged; if the architect edits it, keep both names in backticks on that one line (a line break inside the paragraph would break the reader). |

Free for the architect: any wording that is not in the `STALE` pattern (for example "landed in P3-18", which contains no `until`; "added by P3-18" is also fine). Do not write `PENDING`, `pending`, `not yet`, `to be written` next to a test name. Bump the ADR revision and the index as P3-00d did.

## 2. C3: the cli store parity test runs in both hosted Postgres legs

### 2.1 What the tests read

`.github/workflows/ci.yml` (YAML) and the root `pyproject.toml`, in the style of `test_ci_*` of `test_phase3_ci.py` and `test_evaldb_ci.py`. For each of the jobs `test-postgres` (matrix pg15 and pg18) and `test-postgres-py315` (pg18, non-blocking):

1. exactly one `pytest` command line with `-m postgres`;
2. its first path is `packages/evaldb/tests` (the existing tests `test_ci_postgres_job_matrix_and_pinned_image` require the regex `pytest -m postgres packages/evaldb/tests\b` and a count of 1 of the string `pytest -m postgres packages/evaldb/tests`; the order keeps them true);
3. `packages/cli/tests` or the explicit `packages/cli/tests/test_recorded_store.py` is also a path, and no other path, no other option, no `|| true`; the cli tests are not named twice;
4. the blocking job has no `continue-on-error`;
5. both lines are exactly `- run: uv run --locked --all-groups pytest -m postgres packages/evaldb/tests packages/cli/tests` (or with the explicit file), and the file mentions `pytest -m postgres` exactly twice;
6. the URL variable the cli fixture reads (`POSTGRES_URL_ENV` of `cli_batchd_support.py`, read by AST) is `TRACER_TEST_POSTGRES_URL`, and each Postgres job writes it before it runs pytest;
7. the root `pyproject.toml` is unchanged: `addopts` is exactly `-ra --strict-markers --strict-config -m 'not private_fixtures and not local_only and not postgres'`, the one `postgres:` marker line is exactly the text of today, and `--strict-markers` is on;
8. by AST, `test_recorded_store.py` has the `backend` fixture with the params `sqlite` (no marks) and `postgres` (`marks=pytest.mark.postgres`), and `test_pipeline_bundle_stores_on_both_backends` takes that fixture;
9. by a `pytest --collect-only -q -m postgres` subprocess, for the directory and for the file, `test_recorded_store.py::test_pipeline_bundle_stores_on_both_backends[postgres]` is selected and `[sqlite]` is not.

Teeth: 16 collected tests run the same checker over synthetic workflows (the state of the gate finding, one leg without the path, the path first, a wrong marker expression, a missing marker, an extra path, an extra option, an exit swallowed, a comment that names the path, an `echo` that names it, the cli tests twice, a blocking job that continues on error, a missing job).

### 2.2 What the dev changes

`.github/workflows/ci.yml` lines 170 and 210 (the last step of `test-postgres` and of `test-postgres-py315`):

```text
      - run: uv run --locked --all-groups pytest -m postgres packages/evaldb/tests packages/cli/tests
```

Nothing else in `ci.yml`, nothing in `pyproject.toml`. The cli parity test needs no other variable: it reads `TRACER_TEST_POSTGRES_URL`, which both jobs already write; it drops and creates the `evaldb` tables of the `tracer_test` database in its own fixture (as the evaldb tests do), so the two paths run one after the other in one process.

The C3 fallback (a local pg15 and pg18 run at HEAD, logs under `docs/phase3/evidence/green/gate/`) is the lead's; no test pins it.

## 3. C7 and C7b: the flags table explains its "chunk edge" column

### 3.1 The sentence and the markup (the tests pin them exactly)

Under the flags table, inside the section `#flags`, directly after `<table id="flags-table">`:

```html
<p id="chunk-edge-note" class="chunk-edge-note">A flag marked yes under chunk edge may come from where the audio was cut for transcription and not from the two texts. The first and last tokens of the whole recording also count as chunk edges.</p>
```

- Text, byte for byte (ASCII): `A flag marked yes under chunk edge may come from where the audio was cut for transcription and not from the two texts. The first and last tokens of the whole recording also count as chunk edges.` One paragraph, one line of source, no other text.
- Element: `<p>`, attributes `id="chunk-edge-note"` and `class="chunk-edge-note"` and no other (no `data-*`, no `style`, no handler); no child element; not a `ut` element (it holds no untrusted text, so no FSI/PDI wrapper).
- Written through `_e(...)`, the way every text of the report is.
- The children of `section#flags`, in order, are exactly `h2`, `table#flags-table`, `p#chunk-edge-note`.
- The column label stays `chunk edge` and the cell values stay `yes` and `no`.
- Present in every report that has at least one flag row, in the relative-audio and the embedded-audio form. No number, no offset, no time in it, and none of the words of the deny list or `recommend`, `suggest`, `correct`, `accuracy` (it passes `DENY_LIST` and the page-wide checks).
- Not a function of the call: two different runs give the same bytes; no turn text is in it.

Dev's change in `packages/cli/src/tracer_cli/report.py` (the oracle is `oracle-src.patch`; 7 lines):

```python
CHUNK_EDGE_NOTE = (
    "A flag marked yes under chunk edge may come from where the audio was cut for transcription "
    "and not from the two texts. The first and last tokens of the whole recording also count as "
    "chunk edges."
)
...
        content = _el("table", head + _el("tbody", "".join(rows)), id="flags-table") + _el(
            "p", _e(CHUNK_EDGE_NOTE), id="chunk-edge-note", **{"class": "chunk-edge-note"}
        )
```

### 3.2 When there are no flag rows (decision, section 7 item 2)

The sentence is not written: the section keeps `h2` and the one `<p>no span reached the threshold</p>` (pinned by `test_report_with_no_flags_says_so`, which this batch does not touch). There is no column to explain; the text "chunk edge" does not occur in that report. Pinned by `test_report_chunk_edge_note_is_absent_when_there_are_no_flag_rows`.

### 3.3 Nothing else changes in the page

No new script, no new style rule, no new tag, no new attribute name: the CSP hash tests (`test_report_csp_present_and_script_hash_matches`, `test_report_seek_controls_and_script_are_inert`) pass unchanged, and the new test `test_report_chunk_edge_note_adds_no_script_style_or_policy_change` pins that `SEEK_SCRIPT` and `STYLE` are still the hashed text, that the sentence is outside them, and that the policy string is the computed one for both audio forms.

### 3.4 C7b: the recording-edge semantics

`near_chunk_boundary` is unchanged (ADR 0023 rule 10; `test_near_chunk_boundary_marked` is not touched): true for a span that holds the first or the last token of any STT turn, including the first and the last token of the recording; false for a zero-width (pure deletion) edge span. `test_report_chunk_edge_note_names_the_recording_edge_semantics` shows the case on a real report: in the batch D base run the flagged zero-width span at the end of turn 2 holds STT token 10 of 11, the last token of the recording; its row says `yes`; and the sentence states that the first and last tokens of the whole recording count as edges. The README and concept-page statements, and the BACKLOG row for the scorer-version question, are the architect's.

## 4. C12: the canary allowlist and the `jsonschema` import

### 4.1 (a) `.gitleaks.toml` line 55

The canary constants of the tests are `CANARYKEY-<8>-<6>` (`evalcore_batchd_support.py`, `evalproviders_support.py`) and `CANARYKEY-<6>-<6>` (`cli_batchf_support.py`), lowercase letters and digits. The log lines that need the allowlist are the `generic-api-key` matches of batch G, which end with the canary in single quotes: `PI_KEY': 'CANARYKEY-...'`. gitleaks compares the regex with the text of that match (`regexTarget = "match"`), which ends with the closing quote.

Replace the regex (line 55) with exactly:

```text
regexes = ['''['"]CANARYKEY-[a-z0-9]{6,8}-[a-z0-9]{6}['"]\z''']
```

(Go RE2: `\z` is the end of the text; no look-around, no back-reference.) Pinned by `test_gitleaks_canary_allowlist_is_anchored_to_the_canary_shape` (behaviour on 3 accepted wrappings of each of the three canaries and on 16 refused strings, among them `xCANARYKEY-aaaa`, the long tail, a missing quote either side, upper case, a short or long token, a canary followed by a credential-shaped decoy), `test_gitleaks_canary_allowlist_regex_is_the_reviewed_line` (the exact line), `test_gitleaks_canary_allowlist_keeps_its_scope` (rule, `AND`, `match`, the evidence path regex, four allowlists, the default rules still extended), and `test_gitleaks_canary_regex_check_has_teeth` (the old regex, a missing quote, an open tail, a missing end anchor, a regex that refuses the canary, and a look-around are all refused).

Verified with the pinned binary (gitleaks 8.30.1) on scratch copies, log `gitleaks-verification.log`:
- `gitleaks dir` on an rsync copy of `git ls-files` plus `docs/phase3/evidence/` (the batch G logs, which hold the canary echoes): no leaks with the corrected regex;
- `gitleaks git` on the real history (74 commits): no leaks;
- the same tree without the canary allowlist: 4 findings today, all `generic-api-key` canary echoes (the allowlist is still needed);
- a scratch log with `xCANARYKEY-...` and a valid canary: the old regex reports 0 (the hole), the corrected one reports 1 (the x-prefixed string) and allows the valid one.

Finding F1 (found while verifying, fixed in the tests): a first version of the test file held the canary shape and a credential-shaped decoy as literals, and gitleaks reported 4 findings in it (the file is not under the evidence path). The staged file builds every such string from parts at run time. The dev must not "simplify" these into literals, or the hosted `secrets` job fails. For the same reason the batch J evidence logs must not echo an unanchored canary: `gitleaks-verification.log` prints counts only.

### 4.2 (b) `jsonschema` is a hard import

`test_jsonschema_is_pinned_in_the_root_dev_dependency_group`: the `dev` group includes the `test` group (`include-group`), which holds `jsonschema==4.26.0` exactly (a `==` pin, no extra), once; `uv.lock` holds the same version; the installed version equals it; no `packages/*/pyproject.toml` mentions `jsonschema` (a test dependency only). `test_no_test_skips_or_guards_on_a_missing_jsonschema`: no `packages/*/tests/*.py` calls `importorskip` and none wraps a `jsonschema` import in `try/except ImportError`. `test_the_modules_that_validate_with_jsonschema_import_it_at_the_top`: `test_flags_document.py` and `test_recorded_scoring.py` import it at module level and not inside a function, and still use it. `test_jsonschema_guard_check_has_teeth`: synthetic sources (an `importorskip` call, three forms of `try/except ImportError`, a call inside a function, and the clean forms).

The dev changes nothing in `pyproject.toml` or `uv.lock` for this (the pin is already there). The only change is the replacement of the two existing test files by the staged copies (section 5).

## 5. Named spec change (tightening only): `importorskip("jsonschema")` becomes a plain import

Before/after: `docs/phase3/evidence/red/batch-j/spec-change.diff` (44 lines). In `packages/evalcore/tests/test_flags_document.py` (2 tests: `test_flags_document_structural_faults_are_refused_by_schema_and_validator`, `test_flags_document_cross_field_faults_are_refused_by_the_validator_only`) and `packages/evalcore/tests/test_recorded_scoring.py` (1 test: `test_flags_metadata_validates_against_tracer_flags_1`) the line `jsonschema = pytest.importorskip("jsonschema")` is removed and `import jsonschema  # type: ignore[import-untyped,unused-ignore]` is added at the top of the module. No assertion changes. A missing `jsonschema` now fails at collection instead of skipping 70 collected cases silently. The existing tests are not weakened: the change only removes a way to skip. These are the only edits to existing tests; `find` of `importorskip` over `packages/*/tests` shows no other call (the `_support_b12.py` and `test_adversarial_registry.py` hits are the registry's own detector and its sample text).

## 6. What each side changes

Dev:
1. `git mv` the staged tests (section 0), `-f` over the two spec-changed files.
2. `ci.yml` lines 170 and 210 (section 2.2).
3. `report.py` (section 3.1).
4. `.gitleaks.toml` line 55 (section 4.1); then run `gitleaks dir` and `gitleaks git` with the hosted job's binary.
5. Do not touch `pyproject.toml`, `uv.lock` (the pin exists).

Architect:
1. ADR 0016 I17 and ADR 0004 I41 and I42 (section 1.2); revision numbers and the ADR index.
2. README and concept-page statement for the chunk-edge column (the sentence of section 3.1 is the wording the report shows; the pages may expand it, but must not contradict it); the BACKLOG row for the scorer-version question of `near_chunk_boundary` at recording edges.
3. Findings F2 and F3 below, if the lead agrees.

## 7. Ambiguities and decisions (the lead auto-approves; each is implemented in the oracle as recommended and pinned as stated)

1. **The sentence.** Chosen: the text of section 3.1. Rejected: (a) a tooltip (`title`) on the header cell (not visible on touch screens, not in the text the tests and a screen reader read as the sentence); (b) a longer paragraph with the BL-255 numbers (the numbers are measurement results; the report must not carry them, and they would be call-dependent); (c) renaming the column (a changed label would break `test_report_highlights_exact_flag_spans`'s cell class and the e2e tests). Trade-off: the sentence says "may come from", never "is".
2. **No flag rows: no sentence.** Recommended and pinned. Rejected: always present (it would explain a column that is not on the page). The cost: a reader of a no-flag report does not learn the semantics, which is right because there is nothing to read.
3. **Id and class both `chunk-edge-note`.** Recommended. Rejected: a class from the existing `note` set (no such class exists; the CSS has no rule for it and none is added, so the CSP style hash is untouched).
4. **The quotes in the canary regex.** The corrected regex requires a quote before and after the token, so an unquoted canary (`key=CANARYKEY-...`) in an evidence log would be reported. All current echoes are quoted (pytest assertion reprs). Rejected: allow an unquoted token (then `xCANARYKEY...` followed by a space would again be accepted by prefix games), and a token-only pattern (`\bCANARYKEY-...`, which accepts a long tail after a space).
5. **The token lengths `{6,8}` and `{6}`.** From the three canary constants (8 and 6, 6 and 6, 8 and 6). A new canary of another length must change the regex and `CANARY_SHAPE` together; the test `test_gitleaks_canary_allowlist_is_anchored_to_the_canary_shape` reads every `KEY_CANARY` and `CANARY_KEY` in the tests by AST and fails with the shape, so a new constant cannot slip in.
6. **C3 path: directory, not file.** Recommended: `packages/cli/tests` (a later cli Postgres test is then run without another CI change). The explicit file is accepted by the tests as an alternative. Rejected: a separate third `-m postgres` line (the evaldb tests count the string once per job).
7. **C1 scope.** Only the four named items are read for their citations; the stale wording is read on every line of ADR 0004 and ADR 0016 that cites tests. ADR 0023 and ADR 0024 keep their `PENDING` mechanism (`test_adr0023_rules_are_tested`, `test_adr0024_rules_are_tested`); a grep shows one `pending until` line each in ADR 0023, ADR 0024 and the ADR index, which this batch does not read.
8. **Exact pin of the regex line and of the report sentence.** Both are pinned byte for byte (the lead asked for the exact line; "exact wording yours"). A later wording change is a spec change of the test, to be named.
9. **`jsonschema` import form.** Top of the module, with the same `type: ignore` comment as `test_adr0023_recorded_scoring.py`. Rejected: a fixture (the file's `pytest.mark.parametrize` tests would need it threaded through), `pytest.importorskip` (the thing removed).

### Findings for the lead (not pinned; BACKLOG candidates)

- F1 (fixed above): literal canary and credential-shaped strings in a test file trip the hosted gitleaks job outside the evidence path.
- F2 (P2): ADR 0016 cites `test_human_subject_marker_contradiction_rejected` (item P-47) and ADR 0004 cites `test_every_metric_has_pass_rule` (item P-08); no `packages/*/tests` function has either name. The batch J reader does not read those lines. A reader of all citations in both ADRs would need either the tests or the correction of the names (the real tests may exist under other names; the architect or the schema-architect knows).
- F3 (P3, a note for anyone who reproduces a run): the full suite in a copy of the tree without a `.git` directory fails 31 tests that need `git` (`test_cli_batch_g.py`, `test_guard_report_store_call_log.py`, `test_mapping_f8.py`, `test_repo_contract.py`). The batch J oracle runs were made after `git init` and `git add -A` in the scratch copy; with that they pass.

## 8. What the tests show on the current tree (red) and in the oracle

Red (staged tests over the current tree, with the two spec-changed files): see `red.py3.11.log` to `red.py3.14.log`. 13 failed, 165 passed, 0 errors on each of Python 3.11, 3.12, 3.13, 3.14. The 13 failures, each for the intended reason:

| Test | Why it fails today |
|---|---|
| `test_report_flags_table_is_followed_by_the_chunk_edge_note` | `section#flags` has `h2`, `table` and no `p#chunk-edge-note` |
| `test_report_chunk_edge_note_follows_the_table_in_every_scored_report` | same, in 6 renderings |
| `test_report_chunk_edge_note_holds_no_call_text_and_no_number` | no element with the id (`('chunk-edge-note', 0)`) |
| `test_report_chunk_edge_note_of_a_replay_run_is_the_same_fixed_sentence` | same, replay report |
| `test_report_chunk_edge_note_is_escaped_text_and_passes_the_deny_list` | the markup search finds no `<p id="chunk-edge-note">` |
| `test_report_chunk_edge_note_adds_no_script_style_or_policy_change` | the sentence is not in the page |
| `test_report_chunk_edge_note_names_the_recording_edge_semantics` | no element with the id |
| `test_ci_postgres_legs_run_the_cli_store_parity_test` | both legs run only `packages/evaldb/tests` (2 problems) |
| `test_ci_postgres_command_lines_are_the_reviewed_ones` | lines 170 and 210 lack the cli path |
| `test_gitleaks_canary_allowlist_is_anchored_to_the_canary_shape` | the regex `CANARYKEY-[a-z0-9-]+` accepts the x-prefixed and long-tail strings |
| `test_gitleaks_canary_allowlist_regex_is_the_reviewed_line` | the line is the unanchored one |
| `test_adr0016_amendment_cites_test` | I17 says `pending until it lands` |
| `test_adr0004_invariants_i41_i42_cite_only_existing_tests_and_say_nothing_is_pending` | I41 says `pending until P3-07 and P3-20 land` |

The C12(b) tests pass over the staged (tightened) files; over the original two files they fail (`red-original-jsonschema-tests.py3.13.log`: 2 failed, 2 passed). The regression pins (marker configuration, the source mark of the parity test, the collect-only selection, the absence of the sentence without flag rows, all the teeth tests) pass today by design.

Oracle (`oracle-src.patch`, `oracle-docs.patch`: minimal implementations in a scratch copy that is a git repository): the 7 modules pass, 178 passed on Python 3.11, 3.12, 3.13 and 3.14 (`oracle.py3.1x.log`); ruff check, ruff format --check and mypy strict pass on the package trees. Full workspace suite in the oracle (`oracle-full-suite.py3.1x.log`): 12678 passed, 1288 deselected, 0 failed, on each of the four versions (the Postgres, private-fixture and local-only tests are deselected by the default markers; no Postgres leg was run, so the C3 parity test itself is proven only by the collect-only selection, not by a run against a server).

Mutants (`mutants.txt`): 59 mutants of the oracle (the workflow, the marker configuration, the parity test's mark, the gitleaks regex and scope, the jsonschema pin and guards, the report markup and sentence, the ADR wording and citations), 58 killed, 1 equivalent (the explicit-file alternative of section 2.1 is accepted by design).

## 9. Test inventory

C1, `evalschema/test_adr0016_amendment_cites_test.py`: `test_adr0016_amendment_cites_test`, `test_adr0004_invariants_i41_i42_cite_only_existing_tests_and_say_nothing_is_pending`, `test_adr_amendment_citation_check_names_itself`, `test_citation_check_accepts_a_clean_set`, `test_citation_check_refuses_the_pending_wording_of_the_gate_finding`, `test_citation_check_refuses_every_pending_phrasing` (9), `test_citation_check_does_not_refuse_ordinary_words` (5), `test_citation_check_refuses_a_cited_name_that_no_test_defines`, `test_citation_check_refuses_an_item_that_cites_nothing_or_drops_a_test`, `test_stale_line_check_allows_a_line_only_while_a_cited_test_is_missing`, `test_line_readers_refuse_a_missing_or_duplicated_item`.

C3, `evalcore/test_phase3_ci_postgres_cli.py`: `test_ci_postgres_legs_run_the_cli_store_parity_test`, `test_ci_postgres_command_lines_are_the_reviewed_ones`, `test_ci_postgres_url_variable_is_the_one_the_cli_fixture_reads`, `test_ci_postgres_marker_configuration_is_unchanged`, `test_cli_store_parity_test_is_marked_postgres_in_the_source`, `test_cli_store_parity_test_is_selected_by_the_postgres_marker`, `test_ci_check_accepts_the_reviewed_shape_and_the_explicit_file`, `test_ci_check_refuses_the_state_of_the_gate_finding`, `test_ci_check_refuses_a_single_leg_without_the_cli_path` (2), `test_ci_check_refuses_each_weakening` (10), `test_ci_check_refuses_a_blocking_job_that_continues_on_error`, `test_ci_check_refuses_a_missing_job`.

C7 and C7b, `cli/test_cli_report_chunk_edge_note.py`: `test_report_flags_table_is_followed_by_the_chunk_edge_note`, `test_report_chunk_edge_note_is_absent_when_there_are_no_flag_rows`, `test_report_chunk_edge_note_follows_the_table_in_every_scored_report`, `test_report_chunk_edge_note_holds_no_call_text_and_no_number`, `test_report_chunk_edge_note_of_a_replay_run_is_the_same_fixed_sentence`, `test_report_chunk_edge_note_is_escaped_text_and_passes_the_deny_list`, `test_report_chunk_edge_note_adds_no_script_style_or_policy_change`, `test_report_chunk_edge_note_names_the_recording_edge_semantics`.

C12, `evalcore/test_gitleaks_canary_allowlist.py`: `test_gitleaks_canary_allowlist_is_anchored_to_the_canary_shape`, `test_gitleaks_canary_allowlist_regex_is_the_reviewed_line`, `test_gitleaks_canary_allowlist_keeps_its_scope`, `test_gitleaks_canary_regex_check_has_teeth`. `evalcore/test_jsonschema_hard_import.py`: `test_jsonschema_is_pinned_in_the_root_dev_dependency_group`, `test_no_test_skips_or_guards_on_a_missing_jsonschema`, `test_the_modules_that_validate_with_jsonschema_import_it_at_the_top`, `test_jsonschema_guard_check_has_teeth`.

## 10. Evidence (`docs/phase3/evidence/red/batch-j/`)

`red.py3.11.log` to `red.py3.14.log`, `red-original-jsonschema-tests.py3.13.log`, `oracle.py3.11.log` to `oracle.py3.14.log`, `oracle-full-suite.py3.11.log` to `.py3.14.log`, `oracle-src.patch`, `oracle-docs.patch`, `spec-change.diff`, `mutants.txt`, `gitleaks-verification.log`. Every log was made with its own `UV_PROJECT_ENVIRONMENT` per Python version in a new scratch directory, `--offline`, a fresh `PYTHONPYCACHEPREFIX` per run, and scrubbed of home and temporary paths.
