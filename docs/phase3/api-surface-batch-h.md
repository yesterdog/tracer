# Phase 3 batch H contract: the concept page, the CLI README quick start and the measurement report format (P3-31, P3-32)

Author: qa, 2026-10-26. Source: ADR 0023 (rules 1, 2, 14, 16, 19 and Ambiguity 6), ADR 0024 (rules 15 and 16, the run book), the contracts of batches E, F and G, `docs/phase3/SCOPE.md` rows P3-31, P3-32, P3-33 and Decisions D1 to D13, the decision log, and the tree at the end of batch G. qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, script or earlier test. This batch has no product code: it is documents plus tests that read documents. The lead auto-approves the exact names, headings and wording (owner instruction, 2026-10-23).

## 0. Where the tests are, how they run, and the move order

Nothing was created under `packages/` or `scripts/`. The tests are staged at

```text
docs/phase3/evidence/red/batch-h/staging/tests/evalcore/ -> packages/evalcore/tests/ (3 files)
docs/phase3/evidence/red/batch-h/staging/tests/cli/      -> packages/cli/tests/      (1 file)
```

No `__init__.py`, no `conftest.py` (the autouse no-network guard of `packages/cli/tests/conftest.py` covers the README run).

| File | Holds | Functions | Collected |
|---|---|---|---|
| `evalcore/evalcore_batchh_support.py` | helpers, not collected: the deny-list fold, section and statement ("unit") parsing, BL id and test name readers | - | - |
| `evalcore/test_concept_page_recorded_scoring.py` | the concept page `docs/concepts/recorded-call-scoring.md` (section 1) | 22 | 69 |
| `evalcore/test_measurement_reports.py` | every file `docs/phase3/measurements/*.md`, the template included (section 3) | 11 | 62 |
| `cli/test_cli_readme_quick_start.py` | `packages/cli/README.md` (section 2) | 8 | 41 |
| total | | 41 | 172 |

Move order (the dev's or architect's first acts in batch H, all in one commit):

1. `git mv` the four staged files into the two `tests` directories above.
2. Apply `spec-change.diff` (section 5): empty `PENDING` in `packages/evalcore/tests/test_adr0023_recorded_scoring.py`. Without it `test_adr0023_rules_are_tested` fails ("remove from PENDING") the moment the two tests exist; that failure is the intended coupling, not a defect.
3. Write the four documents of sections 1 to 3: `docs/concepts/recorded-call-scoring.md`, a row in `docs/concepts/README.md`, `packages/cli/README.md`, `docs/phase3/measurements/TEMPLATE.md`. `oracle-docs.patch` is one hand-written set that passes every test; use it as a reference for the shape, not as the text (the architect writes the prose).
4. Run `uv run --locked ruff check .`, `ruff format --check .`, `mypy` (the staged files are ruff-format, ruff-check and mypy-strict clean) and the whole workspace.
5. Bookkeeping that no test pins (P3-33, lead): section 6.

Commands: `uv run --locked pytest packages/cli/tests/test_cli_readme_quick_start.py packages/evalcore/tests/test_concept_page_recorded_scoring.py packages/evalcore/tests/test_measurement_reports.py packages/evalcore/tests/test_adr0023_recorded_scoring.py`.

Every checker has teeth: a small passing sample built inside the test module (`GOOD_PAGE` via `build_page`, `filled_report`, `good_readme`; fixtures of the checkers, not the documents) and one mutation per rule that must fail with the matching message. Those teeth tests do not read the documents, so they pass before the documents exist; the tests that read the real documents fail until they are written (section 7).

Statements are matched per unit: one sentence (split after `.`, `!`, `?` followed by white space; so no "e.g." or "i.e." inside a statement), one bullet, or one table row, of the named section; markdown `*` and backticks are ignored; fenced blocks are skipped. A statement lists regular expressions, all of which must match (case-insensitive) in one unit.

## 1. The concept page `docs/concepts/recorded-call-scoring.md` (`test_concept_page_recorded_scoring.py`)

Tests: `test_concept_page_states_no_accuracy_claim` (the name ADR 0023 rule 1 and I1 cite; removed from PENDING), `test_concept_page_has_fixed_headings_and_required_statements`, `test_concept_page_cites_only_existing_rows_tests_and_links`, `test_concept_page_is_linked_from_the_concepts_index`, `test_the_requirement_table_is_well_formed`, and the teeth.

### 1.1 Headings (exact, in this order, no others)

Line 1 is `# Recorded-call scoring`. The second-level headings are exactly: `## What is measured`, `## Not claimed`, `## Scope, roles and tier`, `## Cost`, `## Privacy and consent`, `## Run book`, `## Known limits`, `## Related`.

### 1.2 The wording deny-list (ADR 0023 rule 1)

Whole words, case-insensitive, after NFKC folding, removal of every format character (zero-width joiners, soft hyphen, bidi marks) and collapse of white space: `accuracy`, `WER`, `CER`, `error rate` (also `error-rate`, `error_rate`), `correct`, `recall`, `precision`. They may occur only in the one verbatim sentence

```text
Tracer reports how far two transcripts agree; it does not measure accuracy, and none of its numbers is a WER, a CER, an error rate, a recall or a precision.
```

which must occur exactly once on the page, as the whole content of the one fenced block (`text`) of the section "Not claimed". No other fenced block may exist on the page. So "Match Error Rate" (the expansion of MER) is forbidden, and so is "correct" anywhere else: write "right", "verified", "confirmed". The page also holds no format or control character (a tab and a newline are fine).

### 1.3 Required statements (each in the named section; see `REQUIREMENTS` in the test for the exact regexes)

| Key | Section | The statement says (one unit) |
|---|---|---|
| agreement_definition | What is measured | the agreement between the agent's (transcript) export and an independent speech-to-text (or STT) transcript of the same audio (or recording, call) |
| mer_label | What is measured | the label `agreement distance (MER)` |
| mer_citation | What is measured | `Morris, Maier and Green (2004)` |
| counts_with_denominators | What is measured | every number is printed with counts and a denominator |
| threshold_has_no_default | What is measured | `flag_min_confidence` has no default |
| confidence_uncalibrated | What is measured | the confidence is uncalibrated (or not calibrated) |
| confidence_not_a_probability | What is measured | the confidence is not a probability |
| a_flag_is_not_an_error | Not claimed | a flag is not a claim of an error, mistake or wrong text |
| unflagged_is_not_vouched_for | Not claimed | an unflagged span is not shown right, verified or confirmed |
| export_is_not_a_reference | Not claimed | the export is not a reference or ground truth |
| two_clips_are_a_demonstration | Not claimed | two clips (or calls) are a demonstration, citing BL-170 |
| marathi_only | Scope, roles and tier | only Marathi, code `mr` |
| declared_language | Scope, roles and tier | the language is declared, with `--declared-language` |
| caller_never_evaluated | Scope, roles and tier | the caller is never evaluated (judged, scored) |
| caller_lines_are_agent_output | Scope, roles and tier | the export's caller lines are the agent's own recognition of the caller (ADR 0023 rule 2) |
| stt_turns_have_role_unknown | Scope, roles and tier | the speech-to-text turns have role unknown |
| flags_are_labelled_with_the_role | Scope, roles and tier | the report labels each flag with a role |
| tier_t0 | Scope, roles and tier | the run is at tier T0 |
| cost_not_available | Cost | cost is `not_available` without a USD price |
| cost_never_zero | Cost | a missing cost is never 0 (or zero) |
| audio_leaves_only_with_stt_sarvam | Privacy and consent | the audio goes to Sarvam only with `--stt sarvam` |
| replay_sends_nothing | Privacy and consent | with `fake:` a replay sends nothing (no network) |
| report_and_store_hold_text | Privacy and consent | the report and the store hold call text and must stay out of git |
| call_log_holds_no_text | Privacy and consent | the call log holds no text (counts, ids and timings only) |
| out_is_outside_the_repository | Privacy and consent | `--out` goes outside the repository |
| consent_record | Privacy and consent | the consent record, given with `--consent-reference` |
| retention_recorded_not_enforced | Privacy and consent | retention is recorded and not enforced, citing BL-234 |
| embedding_audio_is_opt_in | Privacy and consent | `--embed-audio` is opt-in |
| run_book_pointer | Run book | "Run book for a real send" of ADR 0024 |
| dry_run_first | Run book | a dry run first |
| max_chunks_ceiling | Run book | the `--max-chunks` ceiling refuses a plan |
| offline_quick_start_pointer | Run book | `packages/cli/README.md` for the offline quick start |
| number_words_against_digits | Known limits | number words against digits are not folded, BL-192 |
| two_letter_threshold | Known limits | a two-letter word and the threshold |
| split_word_below_threshold | Known limits | a split word stays below the threshold |
| noise_floor | Known limits | the noise floor is unmeasured (not measured) until P3-31, OR the sentence names `docs/phase3/measurements/` |
| native_review_missing | Known limits | no native review, BL-169 |

The "Not claimed" statements are prose or bullets outside the fence, and use no deny-listed word.

### 1.4 References

- Every `BL-nnn` on the page exists as a row of `docs/BACKLOG.md`; the page cites at least BL-165, BL-169, BL-170, BL-192 and BL-234.
- Every backticked `test_...` name on the page is a test function under `packages/*/tests`; the page names `test_concept_page_states_no_accuracy_claim`.
- Every relative Markdown link resolves from `docs/concepts/`; the section `## Related` links (as Markdown links, whatever else links them elsewhere) `../adr/0023-recorded-call-agreement-scoring.md`, `../adr/0024-provider-adapters-byok-sarvam-rest.md`, `../adr/0025-marathi-comparison-normaliser.md`, `../../packages/cli/README.md` and `../examples/synthetic-mr/README.md`.
- `docs/concepts/README.md` has a table row that starts `| [recorded-call-scoring.md](recorded-call-scoring.md) | ` followed by text.
- T-DRIFT (existing `test_tdrift`) scans the page: it names no excluded language, tag or mixed-language word.

## 2. The CLI README (`test_cli_readme_quick_start.py`)

Tests: `test_readme_quick_start_is_parseable_and_matches_the_parser`, `test_readme_names_every_exit_code_of_the_command`, `test_readme_quick_start_runs_offline` (the SCOPE P3-32 name), and the teeth. `packages/cli/README.md` must not say "scaffold only".

### 2.1 Layout

Line 1 is `# tracer-cli`. It has the second-level sections, in this order (others may sit between): `## Quick start (offline, no key, no network)`, `## Exit codes`, `## Privacy and a real send`. Relative links resolve from `packages/cli/`.

### 2.2 The quick start

The section says "repository root" (the commands run from there). It holds exactly one fenced shell block (info string `sh`, `bash`, `shell` or `console`) and exactly one fenced `text` block (the expected output). The shell block, after joining `\` continuations and dropping comments and blank lines, is exactly this sequence of commands, and nothing else:

1. `WORK="$(mktemp -d)"` (the work directory is outside the repository);
2. `python -I docs/examples/synthetic-mr/make_wav.py "$WORK/NAME.wav"` (`python3` also accepted);
3. `mkdir -p "$WORK/DIR"`;
4. three `cp docs/examples/synthetic-mr/stt_chunk_0N.json "$WORK/DIR/chunk-000N.json"` for N = 0, 1, 2 (the replay directory naming of batch E: `chunk-NNNN.json`, four digits; the example files are named differently, so the copy is the point);
5. `tracer score ...` with `uv run [options]` allowed before `tracer` (and before `python`).

No loop, no variable other than `WORK`, no other command. The `tracer score` arguments must be accepted by the real parser (`tracer_cli.cli.build_parser().parse_args`, no execution), so every option exists, every required option is present (today: `--transcript`, `--rubric`, `--declared-language`, `--caller-origin`, `--consent`, `--retention`, `--out`, `--stt`, and the positional `AUDIO`), and every value is valid. In addition: AUDIO is the file step 2 writes; `--transcript docs/examples/synthetic-mr/export_synthetic_mr.txt`; `--rubric docs/examples/rubric-mr.example.json`; `--declared-language mr`; `--out` below `$WORK/`; `--stt fake:$WORK/DIR` with DIR created by step 3 and holding exactly the three `chunk-000N.json` copies of `stt_chunk_0N.json`; not `--dry-run`. The block must not contain `--stt sarvam`, `SARVAM_API_KEY`, `--env-file`, `--embed-audio`, `curl`, `wget` or a URL. Every repository path the block names exists.

The expected-output block lists lines of the real stdout of that command, verbatim, except that a name in angle brackets (`<out>`) matches any text. The quick start also says, in one line, that the replay files are named `chunk-NNNN.json` (four digits). It must include a line starting with each of `result: scored`, `agreement distance (MER): `, `flags: `, `flagged share of export tokens: `. `test_readme_quick_start_runs_offline` (the checker is `run_problems`, with teeth: a wrong expected line, a refused command and a missing required line are each reported) runs the five kinds of step in-process and offline (the `python -I` step in a subprocess of the test interpreter, the score step through `tracer_cli.cli.main`), asserts exit code 0, that every expected line is a line of the real output, that `tracer.sqlite` and exactly one `report-<UTC>.html` exist in `--out`, and that nothing was written below `docs/examples`. A wording change in the CLI summary therefore changes two places (the README and the batch E e2e tests, BL-226).

### 2.3 Exit codes

A table with the header line `| Code | Meaning |` and its separator line, whose rows start `| N | meaning |` has exactly one row per key of `tracer_cli.cli.EXIT_CODES` (0, 1, 2, 3, 4, 64), the meaning text of the code appearing (case-insensitive) in the row, and no other code.

### 2.4 Privacy and a real send

Statements (one unit each): the audio leaves the machine only with `--stt sarvam`; `SARVAM_API_KEY` is never on the command line; with `fake:` a replay sends nothing; the report and the store hold call text and stay out of git; `--out` goes outside the repository; every privacy and language option is required and none has a default; the "Run book for a real send" of ADR 0024. Links: `../../docs/concepts/recorded-call-scoring.md` and `../../docs/adr/0024-provider-adapters-byok-sarvam-rest.md`.

## 3. The measurement report (`test_measurement_reports.py`)

Tests: `test_measurement_report_has_counts_and_denominators[<file>]` (one per `docs/phase3/measurements/*.md`; the name ADR 0023 rule 19 and SCOPE P3-31 cite), `test_the_template_exists`, `test_the_template_is_not_a_filled_report`, `test_the_template_tells_the_author_where_a_report_goes`, and the teeth (a filled passing report, 43 mutations, every deny-listed word, the ratio placeholder forms). The checker `report_problems(text, backlog, template=...)` lives in the test module (docs-only form); a later script may copy it.

`docs/phase3/measurements/TEMPLATE.md` is the template (`template=True`); any other file is a filled report (`template=False`: no placeholder left, `Status:` not `template`).

### 3.1 Fixed layout

First line `# Measurement report` with an optional `: title`. Second-level headings, exactly and in order: `## Scope and status`, `## Clips`, `## Configuration`, `## Counts per run`, `## Usage and cost`, `## Flags per run`, `## Run-to-run difference`, `## What was NOT measured`, `## Not claimed`.

- Scope and status: lines `Status: template|draft|final`, `Date: YYYY-MM-DD` (`<date>` in the template), `Task: P3-31`.
- Each of Clips, Configuration, Counts per run, Usage and cost, Flags per run, Run-to-run difference holds a table with exactly these columns (see `TABLES`): Clips `Clip | Export turns | Export tokens | Audio seconds`; Configuration `Setting | Value` with the settings, in order, `flag_min_confidence`, `normaliser`, `max_chunk_ms`, `search_ms`, `stt_model_as_requested`, `r3_shift_ms`; Counts per run `Run | Clip | Chunks answered (n of d) | Attempts | Export tokens | STT tokens | H | S | D | I | Agreement distance (MER) (n of d) | Flags | Flagged export tokens (n of d) | Export turns with a flag (n of d)`; Usage and cost `Run | Clip | Requests | Audio seconds | Cost`; Flags per run `Run | Clip | Flags | Flag span ids`; Run-to-run difference `Comparison | Clip | Flagged tokens in both | Flagged tokens in either | Jaccard (n of d)`.
- Cell grammar (`CELLS`): run `R<n>`; clip `clip-A` or `clip-B`; counts whole numbers; `Audio seconds` a decimal; a ratio cell `N of D` with an optional ` (x)`; `Cost` `not_available (reason)` or a positive amount followed by ` USD` (never `0`, never `false`); `Flag span ids` `none` or `E<nn>, E<nn>`; `Comparison` `R<n> vs R<m>` or `R<n> normaliser on vs off`. In the template a cell may hold placeholders `<name>`; a ratio is `<n> of <d> (<ratio>)`.
- Meaning: R1 the first run, R2 a repeat of R1 with the same inputs (STT run-to-run stability), R3 the same call chunked with the boundaries shifted by half a window (boundary sensitivity, `r3_shift_ms`), and the offline comparison of the stored data scored with the normaliser on and off. The template holds both clips in its Clips table and all three comparisons for both clips; a filled report has at least one comparison row per clip in its Clips table.

### 3.2 Statements

Usage and cost: `not_available`, `never`, `0` in one sentence (the cost rule). Run-to-run difference: a definition of Jaccard (flagged, both, either), what R2 and R3 are (repeat, shift), that the normaliser comparison is offline on stored data. Counts per run: numerator and denominator are explained. Clips: clips are named by id only (`clip-A`, `clip-B`). What was NOT measured: at least three bullets, citing BL-169 and BL-170.

### 3.3 Ratios and consistency

- No percent sign and no word percent. Every decimal in [0, 1] (`0.5`, `1.0`) outside the Configuration row `flag_min_confidence` appears on a line with an `N of D (x)` whose x is that decimal and equals N/D (tolerance half a unit of its last digit, at least 1e-12). `D` is not 0 with a decimal; N is not above D in a ratio cell; a ratio cell of a count column carries its decimal (`Chunks answered` is the exception).
- The MER cell of a row is `S+D+I of H+S+D+I`; the flagged-tokens denominator is the row's export tokens, which equal the Clips table; the turns denominator equals the Clips table's export turns; the flag count equals the number of span ids and the Counts per run value; the Jaccard cell is `both of either` of its own row (both not above either); every Usage and Flags row has a Counts row.
- Every BL id cited exists in `docs/BACKLOG.md`.

### 3.4 What a report may not contain

- Any non-ASCII character (so no Devanagari, no other script, no zero-width character).
- A text excerpt: no fenced block outside "Not claimed", no blockquote line, no double-quoted string of three or more words.
- A call identifier: no eight-character hex run (`(?<![0-9A-Za-z])[0-9a-f]{8}(?![0-9A-Za-z])`, so also after `_`), no file name extension (`.wav .mp3 .flac .txt .json .jsonl .sqlite .sqlite3 .html .htm .csv .log`), no absolute path (a leading `/dir/dir`, `~/`, `C:\`), and no clip name other than `clip-A` and `clip-B`.
- The deny-list words (section 1.2), except in the one fenced block of "Not claimed", which holds exactly the verbatim sentence once.

## 4. Statements the tests do not make

- That a statement is true: the tests check that the page says it and that the cited rows and tests exist; the review gate (P3-G) reads the prose. The deny-list is a floor (ADR 0023 rule 1 trade-off): a paraphrase of an accuracy claim passes.
- That the numbers of a real measurement are right: the checker checks consistency (the MER counts, the denominators, the Jaccard row), not provenance.
- That the report's relative audio plays under `file://` (BL-224, owner's manual check).
- P3-32 also names three CHANGELOGs and licence and NOTICE: no new test here. `packages/evalcore/CHANGELOG.md` exists; `packages/cli` and `packages/evalproviders` have none today. Recommendation: the architect adds them and the lead decides whether a test pins them (not added: nothing in the SCOPE row names one).

## 5. Spec change (named, before any test exists for the names)

`PENDING` in `packages/evalcore/tests/test_adr0023_recorded_scoring.py` becomes empty (`spec-change.diff`): the two names it holds now have tests, and that test asserts `not PENDING & defined`. This is the only edit to an existing test.

## 6. Ambiguities and recommendations (the lead auto-approves; each is implemented as recommended)

1. **Page name.** The lead's task and these tests use `docs/concepts/recorded-call-scoring.md`; ADR 0023 rule 1 and Ambiguity 6 and SCOPE P3-32 say `recorded-scoring.md`. **Recommendation:** the lead's name (it matches the ADR title "Recorded-call agreement scoring" and the CLI strings); the architect fixes the two ADR 0023 mentions and the SCOPE row (one word each, with an ADR index revision) in the batch H commit. To keep the old name instead, change the constants `PAGE` (and the index regex in `test_concept_page_is_linked_from_the_concepts_index`) in `test_concept_page_recorded_scoring.py` and `CONCEPT_LINK` in `test_cli_readme_quick_start.py`.
2. **The call log holds no text.** The task text says the report, store and call log hold call text. ADR 0024 rule 15 and I8, the audit (F-data flow) and batch G say the call log holds counts, ids and timings only. **Recommendation:** the page says the report and store hold call text and the call log does not; the test enforces that wording.
3. **"Fenced Not claimed list" and "exactly one sentence".** ADR 0023 rule 1 allows exactly one verbatim sentence. **Recommendation:** the fenced block of "Not claimed" is that one sentence; the other "not claimed" points (flag is not an error, unflagged is not shown right, export is not a reference, uncalibrated confidence) are prose outside the fence, worded without the seven words (use right, verified, confirmed; "error" alone is allowed, "error rate" is not).
4. **Two-letter threshold and split word have no BACKLOG row.** The page states them as limits (D13 and the synthetic README: D13, D07, D17). **Recommendation:** the lead adds two rows in P3-33 (the retroflex-l against l confusion flags only in words of at most two letters; one inserted space in a word stays below 0.5) and the page may cite them; the tests accept any existing id.
5. **Noise floor.** The page says "unmeasured until P3-31". **Recommendation:** when the first real report lands in `docs/phase3/measurements/`, the page changes that sentence to point at it (the test accepts either form).
6. **The ratio decimals.** The CLI prints MER with 17 digits (`0.10655737704918032 (13 of 122; ...)`). The report format writes `13 of 122 (0.10655737704918032)`; any rounding is accepted if within half a unit of the last digit.
7. **`WORK="$(mktemp -d)"`.** The quick start writes into a fresh directory outside the repository (audit F1, run book); the README does not use `--out` inside the tree. `--caller-origin` and `--consent` values are free as long as the parser accepts them (the synthetic pair is simulated and its consent unknown).
8. **A statement fits one unit.** A requirement can fail because the words sit in two sentences; the test message names the key, and the table in section 1.3 says what a unit must contain.
9. **The measurement checker is test-only.** A future P3-31 runner that writes a report can import nothing from the test module; if wanted, move `report_problems` into `scripts/` then (not done here: no owner for it and a script is a source change).
10. **Run order inside the README run.** The make_wav step costs a second or two of pure Python; accepted.

BACKLOG bookkeeping for P3-33 (lead): close or extend BL-226 (two places for the summary wording, now three), BL-225 (still no native review), add rows for item 4, and record BL-224 as still open.

## 7. Evidence (2026-10-26)

Everything ran in a scratch copy of the repository under the session scratchpad (a new directory of this batch, git-initialised there so that the tests that read git run; one environment per Python through `UV_PROJECT_ENVIRONMENT`, a fresh `PYTHONPYCACHEPREFIX` for every run; the shared `.venv` was not used). The real tree changed only under `docs/phase3/` (the staged tests, this file and the evidence). Logs are in `docs/phase3/evidence/red/batch-h/`, paths scrubbed.

- red on the current tree, Python 3.11, 3.12, 3.13, 3.14 (`red.py3.NN.log`): the four staged files in place, no document written, `PENDING` unchanged: **12 failed, 169 passed** on each Python (181 collected = 172 new + the 9 of `test_adr0023_recorded_scoring.py`). `red-reasons.py3.13.txt` gives the first assertion of each failure: the page, the template and the quick start section do not exist, the README says "scaffold only" and has no exit code table, the concept index has no row, and `test_adr0023_rules_are_tested` demands the `PENDING` edit. 11 of the 12 are new tests; the 12th is the intended coupling of section 5. The 161 other new tests pass before the documents exist because they test the checkers on built-in samples.
- oracle (`oracle.py3.NN.log`, `oracle-docs.patch`, `spec-change.diff`): a hand-written minimal set of the four documents and the `PENDING` edit. **181 passed** on 3.11, 3.12, 3.13 and 3.14 (the 172 new and the 9 of the ADR 0023 test). The oracle documents are small and plain; they are a reference for the shape, not for the prose.
- whole workspace on the oracle tree (`oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`): **12375 passed, 1288 deselected, 32 warnings** on each of the three (Postgres tests not selected). That includes the T-DRIFT, repo-contract and guard tests, which read the new files. Ruff check, ruff format and mypy `--strict` are clean on the four staged files.
- mutants (`mutants.txt`): part 1, **166 mutants of the oracle documents** (each non-blank line deleted once, and replacements of key words): 159 killed, 7 survived, all equivalent (the one-line introductions and five optional lines of the expected output block); part 2, **61 mutants of the checkers** (the deny-list words and their folding, the unit splitting, the fences, the BL, test and link checks, the ratio, stem, path, clip and file-name rules, the consistency rules, the README parser, option and replay rules, the run check): **61 killed, 0 survived**.

Not run: Python 3.15 (non-blocking); GitHub-hosted execution; the `[postgres]` leg (no store code); Windows (the quick start is POSIX shell); a real send. The browser behaviour of the report (BL-224) is not tested.
