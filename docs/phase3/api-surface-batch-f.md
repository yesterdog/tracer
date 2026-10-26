# Phase 3 batch F contract: end to end, tier degradation, no-network guard, repository guard (P3-25 data, P3-26, P3-27, P3-34)

Author: qa, 2026-10-26. Source: `docs/phase3/SCOPE.md` (A.6, Decisions D1 to D11, F7, F12, Q17), ADR 0023 (rules 1, 11 to 17, invariants I1 to I10), ADR 0024 (rules 15 to 17, invariant I8), ADR 0004 (Decisions 6, 9, 10), the contracts of batches A to E (batch E as updated on 2026-10-26: `--stt` required, STT text beside each flag), the synthetic Marathi pair staged by the Indic agent (`docs/phase3/evidence/red/batch-f/staging/data/`), the implemented packages (`tracer-evalcore`, `tracer-evalproviders`, `tracer-cli` scaffold and `store.py`), `scripts/guard_private_fixtures.py` and its Phase 1 test, `.gitignore`, and the decision log (the PHASE 3 entries). qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow, script or earlier test. The data directory is the one exception the lead allowed: qa edited `expected_results.json`, `chunk_plan.json` and `README.md` there (section 2.3). Every statement below is pinned by a test named in section 9, unless it says "not pinned".

No schema change (SCOPE D5) and no new product module: batch F adds tests, one data directory and a change to the repository guard script and `.gitignore`.

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/` or `scripts/` and nothing under them was edited. The tests are staged at

```text
docs/phase3/evidence/red/batch-f/staging/tests/evalcore/ -> packages/evalcore/tests/   (5 files)
docs/phase3/evidence/red/batch-f/staging/tests/cli/      -> packages/cli/tests/        (5 files)
docs/phase3/evidence/red/batch-f/staging/data/           -> docs/examples/synthetic-mr/ (14 files, section 2)
```

No `__init__.py`. Two `conftest.py` files are part of the batch (the no-network guard, section 5). The dev's first act in batch F, after batch D and E are in (the order is pinned in section 10):

```text
git mv docs/phase3/evidence/red/batch-f/staging/tests/evalcore/*.py packages/evalcore/tests/
git mv docs/phase3/evidence/red/batch-f/staging/tests/cli/*.py packages/cli/tests/
git mv docs/phase3/evidence/red/batch-f/staging/data docs/examples/synthetic-mr
```

and, in the same commit, the PENDING tightenings of section 10, the change of `scripts/guard_private_fixtures.py` (section 6.4) and the `.gitignore` lines (section 6.5). The staging data directory must not exist afterwards (`test_synthetic_data_is_at_its_final_path`).

| File | Holds | Collected |
|---|---|---|
| `evalcore/evalcore_batchf_support.py` | helpers, not collected: data location and loaders, WAV generator, replay directory writer, the independent alignment | - |
| `evalcore/test_synthetic_pair.py` | P3-25 data checks (files, export, bodies, recording, chunk plan on the real chunker, expected values, independent alignment, README) | 17 (15 functions) |
| `evalcore/test_tdeg_matrix.py` | P3-27 tier-degradation matrix | 14 (9) |
| `evalcore/test_evalcore_no_socket.py` | P3-27 no-network guard of evalcore, the three-package check, the SCOPE-name guards of P3-26, P3-27, P3-34 | 8 (4) |
| `evalcore/conftest.py` | the autouse no-network guard of the evalcore tests | - |
| `cli/cli_batchf_support.py` | helpers, not collected: in-process run of the command, a tiny HTML tree, the throwaway guard repository | - |
| `cli/test_e2e_synthetic_mr.py` | P3-26 end to end (base, report, consistency, stability, non-mr, empty chunk, Sarvam path, guard on real outputs) | 8 (8) |
| `cli/test_guard_report_store_call_log.py` | P3-34 guard and `.gitignore` | 69 (16) |
| `cli/test_cli_no_socket.py` | P3-27 no-network guard of the cli tests | 1 (1) |
| `cli/conftest.py` | the autouse no-network guard of the cli tests | - |
| total | | 117 (53 functions) |

`test_unit_tests_open_no_socket` exists in three directories (evalproviders from batch B, evalcore and cli here): the SCOPE name is a family and each package proves its own guard. Nothing of the batch D or E modules is imported at module level: every test loads `tracer_cli.cli` or `.report` lazily (the cli support) or imports the evalcore modules inside the test, so before the code exists each test fails on its own and the directories still collect. The cli support loads `packages/evalcore/tests/evalcore_batchf_support.py` by file path (the batch D precedent). The T-DEG tests import `evalcore_batchd_support` (batch D, already moved). The batch F cli tests import nothing from the batch E test files. All staged files pass `ruff check`, `ruff format --check` and `mypy` (repository configuration), are ASCII only (Devanagari and invisible characters are written as `\u` escapes in the Python files; the data README and the export are UTF-8 text by design) and open no socket, read no clock for a result, read no key from the machine and touch no private fixture.

Commands (after the move): `uv run --locked --all-groups pytest packages/evalcore/tests packages/cli/tests` or the whole workspace. Time: the batch F files take about 11 seconds on one core.

## 1. Test values, the owner's values and the canary

The e2e tests use the product's own example rubric file `docs/examples/rubric-mr.example.json` (threshold 0.5, placeholder budget 1.0 inclusive: the owner's values of SCOPE D10, nothing invented). The T-DEG tests use the batch D test values (`TEST_ONLY_FLAG_MIN_CONFIDENCE` 0.3, `TEST_ONLY_REVIEW_BUDGET` 0.25), which are test values and not defaults. The key is a canary built from parts in the cli support (`KEY_CANARY`), never a real key; the only test that sets `SARVAM_API_KEY` sets it in the injected `Environment.environ`, never in the process environment (the conftest removes it).

## 2. The synthetic pair (P3-25 data) and how it is loaded

### 2.1 Location and loading

```text
FINAL_DATA   = <repo>/docs/examples/synthetic-mr            the dev's destination (ambiguity 1)
STAGING_DATA = <repo>/docs/phase3/evidence/red/batch-f/staging/data
DATA         = FINAL_DATA if it is a directory else STAGING_DATA      (evalcore_batchf_support.py)
```

The fallback exists so that the tests run before the move; `test_synthetic_data_is_at_its_final_path` fails until the move is done and until the staging copy is gone. Every loader reads the file named below from `DATA`; nothing is cached across tests except the generated WAV (a module-scoped fixture) and the command runs of the e2e module (a `World` object that runs each variant lazily, inside the test that first needs it, so that the no-network guard of that test is active).

| File | Holds |
|---|---|
| `export_synthetic_mr.txt` | VoicEra-format export, `call_id=e84b1d52-9c3a-47f0-a6d1-3f5b08c7a924`, 22 turns strictly alternating assistant first, stamps with `+05:30`, no invisible character |
| `stt_chunk_00.json` to `02` | scripted Sarvam 200 bodies of the base variant |
| `stt_nonmr_chunk/` | same, chunk 1 answers `hi-IN` (0.91); request ids differ from the base |
| `stt_empty_chunk/` | same, chunk 1 is `{"request_id", "transcript": "", "language_code": null}` |
| `wav_recipe.json` | the recording (2.2) |
| `chunk_plan.json` | the expected cuts of `chunk_wav` with its defaults, and the expected `tracer.chunk_plan/1` metadata |
| `expected_results.json` | outcome, exit code, counts, MER fraction, spans, evidence and planted checks per variant (`base`, `nonmr_chunk`, `empty_chunk`) |
| `README.md` | the planted-difference table D01 to D19, the verification status and, new, the section "Empty-chunk variant" |

The nine bodies are ASCII JSON (`\uXXXX` escapes), the request ids are unique over the nine, the transcript of a body equals its phrases joined by one space (`test_synthetic_bodies_are_sarvam_shaped_with_unique_request_ids`). The three variants differ from the base only where the README says (`test_synthetic_variants_differ_from_the_base_only_where_the_readme_says`). No audio, store, report or database file is ever data (`test_synthetic_data_files_are_all_present_ascii_json_and_unreviewed`).

### 2.2 The recording: generated by the test, never committed

Mono, 24,000 Hz, 16-bit little-endian PCM, 1,632,000 frames (68.00 s), written with the standard `wave` module by `make_wav(recipe, path)`:

```text
pcm = array("h", zeros(total_frames))
for each segment with kind == "tone":
    for n in segment.start_frame .. segment.end_frame - 1:                 # n is the index in the whole file
        pcm[n] = round(amplitude * sin(2 * pi * frequency_hz * n / sample_rate_hz))
a segment with kind == "silence" stays exact zeros; byteswap on a big-endian machine
```

The recipe's segments tile the file (tone 440 Hz 0 to 9.80 s, silence, tone 523 Hz, **silence 22.00 to 23.00 s**, tone 440, silence, tone 523, **silence 45.00 to 46.00 s**, tone 440, silence, tone 523); only the bold silences matter. The file size is 44 + 2 x 1,632,000 = 3,264,044 bytes. Only the exact zeros decide where the chunker cuts; the tone values are not pinned and no hash of the file is asserted (libm rounding may differ by one on another platform; the sha256 on the author's machine, macOS arm64, Python 3.13, was `5ddb9c004afaccf375c9388efb874dc79fb1c79ecb2a48a0f81382a7a587c73b`, informational only). The real chunker with its defaults (25,000 ms window, 5,000 ms search) cuts it at frames 551,760 and 1,103,520: three chunks of 551,760, 551,760 and 528,480 frames (22.99 s, 22.99 s, 22.02 s), WAV sizes 1,103,564, 1,103,564 and 1,057,004 bytes (`test_synthetic_chunk_plan_matches_the_real_chunker_on_the_generated_wav`, `test_synthetic_generated_wav_has_exact_zero_silences_and_audible_tones`).

### 2.3 The replay directory, and what qa changed in the data

`write_replay_dir(variant, dest)` copies the three bodies of a variant byte for byte to `dest/chunk-0000.json`, `chunk-0001.json`, `chunk-0002.json` (the batch E replay format). The e2e module builds one such directory per variant under a temporary directory and runs `tracer score ... --stt fake:<dir>`.

The lead ran the Indic agent's verification script against the real pipeline: the chunk plan, the base variant and the non-mr variant matched; the empty-chunk variant did not (25 differences in spans E07 to E12 of 13). qa confirmed it and **regenerated that variant's spans from the real pipeline output** (`expected_results.json`, `variants.empty_chunk.spans`, E07 to E14; the totals H 73, S 4, D 43, I 1, MER 48/121, 12 flags, 46 flagged tokens and the evidence lists were right already). The hand prediction put the three matched words of the speech-to-text tail (the words `thik`, `aahe`, `mi` that end chunk 0) at the start of turn 7; the aligner puts them at turn 11 (two) and turn 12 (one). Both placements have the same cost pair (48, 44): it is a tie, and the rule of ADR 0023 (cost pair (edits, deletions+insertions), backtrace from the end preferring match > substitution > deletion > insertion) picks the latest feasible export word for each. The README now documents this with a hand derivation (the stretch of 46 export tokens against 4 speech-to-text tokens, the minimum (43, 42), the occurrences of each word in the stretch, why the backtrace lands on export tokens 63, 64, 68 and 87, and the span offsets read off the export text).

The expectation is not a copy of the output: `independent_summary` in the support module is a second implementation of the rule (cost table, tie-break, backtrace, runs cut at turn boundaries) written without `tracer_evalcore.align` and `.spans`; only the public tokeniser supplies the comparison keys. `test_synthetic_expectations_match_an_independent_alignment` (base and empty-chunk) compares counts and every span with export tokens (turn, offsets, token, substitution and deletion counts) and the number of insertion-only spans; `test_synthetic_empty_chunk_tie_is_real_and_broken_by_the_latest_feasible_word` builds the hand-predicted alignment explicitly and shows its cost equals the chosen one. The standalone script `docs/phase3/evidence/red/batch-f/check_empty_chunk_alignment.py` does the same outside pytest and prints the evidence. Not independent: the tokeniser and the normaliser `mr-Deva/1` (they are what the pipeline uses), and the Marathi spelling (BL-169). `chunk_plan.json`, `expected_results.json` now say `verified_by_running_code: true` and what that covers.

## 3. P3-26: the end to end run through the command

All runs call `tracer_cli.cli.run(argv, Environment)` in-process with the fixed clock 2026-10-26T09:00:00Z (report name `report-20261026T090000Z.html`), `environ` `{}` for a replay, no engine factory (the command builds its own) and no chunking option (the defaults produce the three chunks). `argv` is `score <wav> --transcript <export> --rubric docs/examples/rubric-mr.example.json --declared-language mr --caller-origin human --consent unknown --retention keep --stt fake:<dir> --out <dir>`: every privacy and language input explicit, `--stt` explicit (batch E). Consent `unknown` because the data are invented and nothing is claimed about callers.

### 3.1 The base variant: `test_e2e_synthetic_mr_exact_outputs`

Exit 0, stderr empty, and these stdout lines exactly (`<out>` is the `--out` path as typed; the `bundle:` line is the one line with an id):

```text
result: scored
language declared by user: mr
stt provider: replay
stt model: recorded-responses (as requested)
normaliser: mr-Deva/1
agreement distance (MER): 0.10655737704918032 (13 of 122; H 109, S 9, D 2, I 2)
tokens: 120 in the export, 120 in the stt transcript
flags: 8 (confidence at or above 0.5)
flagged share of export tokens: 9 of 120 (0.075)
export turns with a flag: 7 of 22
review budget: 1.0 (no limit)
stt usage: none
cost: not available (no_usage)
ingest warnings: 0
bundle: <RunBundleId>
store: <out>/tracer.sqlite
report: <out>/report-20261026T090000Z.html
```

`--out` holds exactly `tracer.sqlite` (0600) and the report (0600): a replay writes no call log. The stored run (read back through `tracer_cli.store`): `mode` recorded, `caller_origin` human, `observed_tier` T0, `available_inputs` `[audio, transcript, agent_transcript]`, no `started_at`, no external reference, no ingest warning, no verdict, no usage record; the export transcript holds the call id as `provider_call_ref`, the declaration `mr`/`user_declared`, 22 turns (roles agent, caller alternating, seq 0 to 21); the STT transcript has provider `replay`, model `recorded-responses`, 3 turns with a Marathi tag, `tracer.chunk_plan` equal to `chunk_plan.json`'s `as_metadata` and `tracer.empty_chunks` `{"count": 0, "indexes": []}`. The three results are `scored` in the order MER, count, share, with `language_source` declared, `transcript_ids = [export, stt]`, one shared `tracer.flags/1` document, and values `13/122` (as the Python float quotient), `8` and `0.075` (`passed` true for the share only: the placeholder budget cannot fail). The document: `alignment` {word, 109, 9, 2, 2}, `tokens` {120, 120, 9}, `flag_min_confidence` "0.5", `stt` {replay, recorded-responses, requested}, 10 spans equal to the span table of `expected_results.json`; the flagged ones are (turn, start, end) (4,13,18) (5,11,16) (6,17,19) (10,0,23) (12,12,15) (14,17,19) (14,35,37) (16,21,21) as literals in the test, and the two recorded but not flagged are (7,12,18) and (17,8,11); only spans 7 and 14 (the split word and the cut word) are near a chunk edge. Evidence of every result: the turn references 4, 5, 6, 10, 12, 14, 16, then the artifact ranges (11300, 13800), (14100, 16600), (29590, 32290), (35590, 38290), (41590, 44290), (49180, 51880) ms.

The planted differences D01 to D19 (README) map to spans as the table says: D01, D02, D03, D08, D09, D11, D15, D18, D19 leave no span (punctuation, danda, digit forms both ways, case, ZWNJ, ZWJ); D04, D05, D06, D10, D12, D13, D14, D16 are flagged; D07 and D17 are recorded and below the threshold (`test_synthetic_planted_differences_hold_in_the_expected_base_spans`).

### 3.2 The report: `test_e2e_synthetic_mr_report_shows_the_exact_flags`

Reads the report with the tiny HTML tree of the cli support (ids and classes of the batch E contract 5.5): the marker `<meta name="tracer-report" content="1">` once, before `<style>` and `<script>` (the marker the guard reads); `p#status` `data-outcome` scored; `p#agreement` value, hits, substitutions, deletions, insertions (109, 9, 2, 2); `p#flag-count` 8; `p#flagged-share` 9 over 120; `p#review-budget` `data-no-limit` true; 8 `tr.flag-row` in span order whose turn, role (agent for even turns, caller for odd), offsets, `td.exportside` text and `td.heard` text are literals in the test (for example `500` heard against the number word in turn 4, the substituted `OTP` heard as its Devanagari transliteration in turn 12, `nothing heard` for the two deletions, `not in export` and the inserted word for the insertion); 8 `mark.flag` with the span offsets and one `mark.flag.gap` "[+]"; the histogram bins by integer arithmetic over all 10 spans (spans per bin 0,1,0,1,0,1,0,0,0,7; flagged 0,0,0,0,0,1,0,0,0,7; total 10).

### 3.3 Report and store agree: `test_e2e_report_and_store_consistent`

The report file equals `render_report(stored_bundle, audio=RelativeAudio(relative_audio_src(...)))` byte for byte and its audio reference is `../in/synthetic_mr.wav`; the numbers of stdout, report and store are the same (MER line rebuilt from the stored document, `data-*` attributes, counts, share); every flagged row of the report is a flagged span of the stored document, in order, with its STT token range; the export text of a row is the stored turn text at the span offsets and the heard text is the stored STT turn text between the span's first and last STT token (recomputed with the public tokeniser from the stored turns: a second route to the same text); the 22 export turns of the report equal the stored turn texts; the report holds no record id, no call id, no absolute path and no `file:` reference; the artifact keeps the absolute `file:///` URI and its sha256 equals `p#audio-facts[data-sha256]`.

### 3.4 Stability and variants

- `test_e2e_report_bytes_are_stable_across_runs`: a second run of the base variant into another `--out` gives the same report bytes and the same result lines; the two stores hold different bundle ids.
- `test_e2e_nonmr_chunk_is_not_scored_exit_2_with_three_errors`: exit 2; stdout lines `result: not scored`, `language declared by user: mr`, `not scored: unsupported language detected`, `stt usage: none`, `cost: not available (no_usage)`, `ingest warnings: 0`, `bundle`, `store`, `report`; the three results `error` `stt_unsupported_language` with the message "1 of 3 stt turns carry a language other than the covered one", no value, no pass, no metadata, no evidence; the report outcome `not_scored`, one `li` "not scored: unsupported language detected" and none of the numbers sections; the tag `hi-IN` is not echoed.
- `test_e2e_empty_chunk_makes_no_turn_is_counted_and_is_scored`: exit 0; the lines of the base with MER `48/121` (H 73, S 4, D 43, I 1), `tokens: 120 ... 78`, `flags: 12`, `46 of 120`, `export turns with a flag: 12 of 22`; 2 STT turns; `tracer.empty_chunks` `{"count": 1, "indexes": [1]}`; the 13 spans equal to the regenerated span table; the report shows `thaamb` as heard for the substituted last word of turn 14, with the chunk-edge cell "yes", and eight rows "nothing heard".
- `test_e2e_sarvam_path_over_mock_transport_equals_the_replay_run`: the same call through `--stt sarvam` over an `httpx.MockTransport` that serves the base bodies by chunk file name (three requests, in order, each holding the WAV of the plan: the RIFF size field plus 8 equals the planned bytes; no sleep): same lines as the replay except `stt provider: sarvam`, `stt model: saaras:v4 (as requested)`, `stt usage: 3 requests, 68 audio seconds` and `cost: not available (no_price_for_model)`; `--out` holds the store, the report and `sarvam_call_log.jsonl` (six lines: before and after each chunk, the after lines carry the body request ids); the flags document equals the replay's apart from the `stt` member; three usage records; the canary key is in no file, no stdout, no stderr.
- `test_e2e_every_output_it_writes_is_refused_by_the_repository_guard`: the store, report and call log of a real run, staged by force into a throwaway repository, make the real guard fail with three named lines; renamed copies (`page.html`, `data.bin`, `run.jsonl`) are still caught by what they hold.

## 4. P3-27: the tier-degradation matrix

The case has one observed tier, T0 (SCOPE F7); what degrades is the set of inputs, so the matrix runs the same `RecordedConfiguration` (one snapshot) over six input situations with `score_recorded_call` (the fake engine and the small hand-computed call of batch D; the synthetic pair runs end to end in section 3). Every case: `mode` recorded, `observed_tier` T0, no verdict, three results in the order MER, count, share, `check_run_bundle` passes, the bundle round-trips through its JSON.

| Case (test) | Situation | status | `na_reason` / error code | `missing_kinds` | `available_inputs` | `language_source` | outcome, exit |
|---|---|---|---|---|---|---|---|
| `test_tdeg_audio_and_export_scored` | recording + export, declared `mr` | scored x3 | - | [] | audio, transcript, agent_transcript | declared | scored, 0 |
| `test_tdeg_no_audio_missing_input_transcript` | export only, engine never called | not_applicable x3 | `missing_input` | [transcript] | agent_transcript | absent | not_scored, 2 |
| `test_tdeg_no_export_missing_input_agent_transcript` | recording only (transcribed, usage kept) | not_applicable x3 | `missing_input` | [agent_transcript] | audio, transcript | absent | not_scored, 2 |
| `test_tdeg_export_without_declaration_language_not_observed` | both, no declared language | not_applicable x3 | `language_not_observed` | [] | audio, transcript, agent_transcript | absent, no declared ids | not_scored, 2 |
| `test_tdeg_non_mr_chunk_is_not_scored` | one STT chunk tagged `hi` | error x3 | `stt_unsupported_language` ("1 of 2 stt turns carry a language other than the covered one") | [] | audio, transcript, agent_transcript | declared | not_scored, 2 |
| `test_tdeg_export_without_agent_turn_missing_input_agent_transcript` | export of caller lines only (T-A4) | not_applicable x3 | `missing_input` | [agent_transcript] | audio, transcript | absent | not_scored, 2 |

`test_tdeg_schema_identical_across_cases` runs all six with one configuration and asserts: the same snapshot and metric references in the same order; the same JSON member set for each record kind over all cases; every result status in {scored, not_applicable, error} and the three results of a case agree; a result that is not scored has no value, no pass and no zero anywhere in the serialised bundles (`"value": 0`, `0.0` or `false` never occurs); four distinct (status, reason) pairs and three distinct input sets over the six cases (the matrix is not six copies). `test_tdeg_missing_input_is_decided_before_the_language` pins the ADR 0004 precedence (no recording and no declaration gives `missing_input`, not `language_not_observed`). `test_tdeg_every_case_is_deterministic_for_the_same_inputs` (6) pins identical bundles after the fresh ids are masked (batch D rule), with a shared project and agent target. The non-mr case is an `error`, never `not_applicable` (Decisions D7, F12): a `not_applicable` `unsupported_language` for an STT turn would be rejected by `check_run_bundle`, and the command says "not scored: unsupported language detected", never an agent failure and never a zero.

## 5. P3-27: the no-network guard

`packages/evalcore/tests/conftest.py` and `packages/cli/tests/conftest.py` (same content as `packages/evalproviders/tests/conftest.py`): an autouse function-scoped fixture that replaces `socket.socket.connect`, `socket.socket.connect_ex`, `socket.create_connection`, `socket.getaddrinfo`, `asyncio.BaseEventLoop.create_connection` and `.sock_connect` by functions that raise `AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")` (not an `OSError`: a retry loop or a transport translation must not swallow it) and removes `SARVAM_API_KEY` from the environment. `test_unit_tests_open_no_socket` (evalcore, cli) proves each call raises; `test_unit_tests_open_no_socket_each_phase3_package_has_the_guard[evalcore|evalproviders|cli]` parses the three conftest files and checks the autouse fixture, the six patched calls and the `AssertionError`. Interpretation of "every package" (ambiguity 6): the three Phase 3 packages. The e2e runs execute inside tests (not in module fixtures) so the guard is active; the Sarvam-path test uses `httpx.MockTransport`, never a socket.

## 6. P3-34: the repository guard for the outputs of `tracer score`

### 6.1 What is refused, by what

Three outputs of the command hold personal data (call text, audio location, call id) and are written to a user-chosen `--out`:

| Output | Refused by name (index, HEAD and history) | Refused by content (index only: staged and tracked) |
|---|---|---|
| report | basename `report-YYYYMMDDTHHMMSSZ.html` exactly (`report-[0-9]{8}T[0-9]{6}Z\.html`), any directory | the first 65,536 bytes hold `<meta` `name` `=` `tracer-report` (regex `<meta\s+name\s*=\s*["']tracer-report["']`, case-insensitive) **and** the file is `.html`/`.htm` (any case) or starts, after an optional byte order mark and white space, with `<!DOCTYPE html` or `<html` |
| store | basename ending (case-insensitive) in `.sqlite` or `.sqlite3`, optionally followed by `-wal`, `-shm` or `-journal` (so `tracer.sqlite`, `tracer.sqlite-wal`) | the first 16 bytes are `SQLite format 3` and a NUL byte, whatever the name |
| call log | basename `sarvam_call_log.jsonl`, except the exact path `tools/capture/sarvam_call_log.jsonl` | a `.jsonl` file whose first line holds both `"bytes_sent"` and `"audio_seconds"` (the line shape of `tracer_evalproviders.calllog`), whatever the name, also at the allowed path |

Unchanged: the private fixture prefix `packages/evalschema/tests/fixtures/private/` and env files (`.env`, `.env.*` except `.env.example`) are refused as before, from the index, HEAD and history. A file that is written but not staged or committed is not a finding. Content is read from the index blob (`git cat-file --batch` on `:path`, or equivalent); a path that is only in HEAD or in history is judged by name alone (a renamed report that was committed and removed cannot be found without reading history blobs: not pinned either way).

Why the capture log is allowed: `tools/capture/sarvam_call_log.jsonl` has been tracked since Phase 1 (47 lines of the older capture tool: timestamp, endpoint, case, clip id, language requested, status, elapsed; no text); a name rule without the exception would fail on the real tree on its first run. The exception is one exact path, and a Tracer call log at that path is still refused by its content (`test_guard_keeps_the_capture_tools_own_call_log_that_is_tracked_since_phase_1`).

Why the marker rule needs a document start or an html name: `packages/cli/src/tracer_cli/report.py`, the contracts and the tests contain the marker text as a string; a content rule on every file would refuse the source of the report writer. A renamed report (`.txt`, no extension) still starts as a document and is caught (`test_guard_refuses_a_report_by_the_marker_of_its_head`); a source or markdown file that merely mentions the marker, or an example that quotes it after other text, is not (`test_guard_does_not_refuse_what_is_not_a_report`).

### 6.2 Messages and exit code (`stderr`; stdout stays empty)

```text
tracer report file tracked, staged or in history: <path>
tracer store file tracked, staged or in history: <path>
tracer call log tracked, staged or in history: <path>
private fixture or env file tracked, staged or in history: <path>      (unchanged)
```

One line per (path, kind), each exactly once, sorted by path then kind, `<path>` as git writes it (relative to the repository root, `/` separators, no quoting: paths come from `-z` output, so spaces and non-ASCII names print as they are). Exit code 1 if there is any line, else 0. The guard remains a standalone script of the standard library (`test_guard_script_is_still_a_standalone_stdlib_script`).

### 6.3 Tests

`test_guard_rejects_staged_report_store_and_call_log` (the SCOPE name): in a throwaway repository with a copy of the real script, written-but-unstaged outputs pass; each of `out/report-20261026T090000Z.html`, `out/tracer.sqlite`, `out/sarvam_call_log.jsonl` staged alone gives exit 1 and its own message line; all three give three lines; after a commit the guard still fails (tracked, three lines); after `git rm --cached` and a commit they are still named (history). The other 68 collected cases pin every row of 6.1 by parametrised staged-and-tracked cases and by their negatives (what must not be refused), the capture-log exception, the unchanged private/env rules, sorted unique output and the stdlib-only script. `.gitignore`: `test_gitignore_lists_the_outputs_of_tracer_score` and `test_gitignore_does_not_hide_ordinary_files` (via `git check-ignore --no-index`).

### 6.4 The change to `scripts/guard_private_fixtures.py` (the dev's; a working oracle is in `oracle-guard.patch`)

```text
constants:  HEAD_BYTES = 65536
            REPORT_NAME = re.compile(r"report-[0-9]{8}T[0-9]{6}Z\.html")        # fullmatch on the basename
            REPORT_MARKER = re.compile(rb"<meta\s+name\s*=\s*[\"']tracer-report[\"']", re.IGNORECASE)
            DOCUMENT_START = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*<(?:!doctype\s+html|html)\b", re.IGNORECASE)
            STORE_SUFFIXES = (".sqlite", ".sqlite3");  STORE_SIDECARS = ("-wal", "-shm", "-journal")
            SQLITE_MAGIC = b"SQLite format 3\x00";  CALL_LOG_NAME = "sarvam_call_log.jsonl"
            CALL_LOG_ALLOWED = {"tools/capture/sarvam_call_log.jsonl"}

kind_by_name(path):    basename matches REPORT_NAME            -> report
                       basename.lower() minus a sidecar ends in a store suffix -> store
                       basename == CALL_LOG_NAME and path not in CALL_LOG_ALLOWED -> call log
kind_by_content(path, head):   head startswith SQLITE_MAGIC -> store
                       REPORT_MARKER found in head and (suffix in html/htm or DOCUMENT_START matches head) -> report
                       path ends in .jsonl and the first line has "bytes_sent" and "audio_seconds" -> call log
findings(root):  paths = index + staged + HEAD tree + history (as today);
                 for every path: private/env rule (as today) and kind_by_name;
                 for every path of the index only: kind_by_content on the first HEAD_BYTES of the blob
main():          print "<kind> tracked, staged or in history: <path>" sorted; return 1 if any
```

### 6.5 The `.gitignore` lines

```text
# Output of `tracer score` (report, store, call log): personal data. Never commit (P3-34).
report-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.html
tracer.sqlite
tracer.sqlite-wal
tracer.sqlite-shm
tracer.sqlite-journal
*.sqlite
*.sqlite3
sarvam_call_log.jsonl
!/tools/capture/sarvam_call_log.jsonl
```

Pinned by `git check-ignore --no-index`: the report pattern is narrow (`report.html`, `report-notes.html` are not ignored), the capture log is not ignored (it is tracked), ordinary files under `docs/examples/synthetic-mr/` are not ignored. The line `tracer.sqlite` is redundant with `*.sqlite` (kept for the reader; its removal is an equivalent mutant).

## 7. Findings and ambiguities (the lead auto-approves; each is implemented as recommended in the oracle and pinned as stated)

1. **Where the synthetic pair lives.** Pinned: `docs/examples/synthetic-mr/` (beside `rubric-mr.example.json`; the README quick start of P3-32 runs on it, and both evalcore and cli tests read it). Rejected: `packages/cli/tests/fixtures/` (the quick start would point into a tests directory; the wheel rule "no tests in wheels" is not at stake, but the example is documentation); `packages/evalcore/tests/fixtures/` (same). P3-32 needs a stdlib generator for the recording so the quick start can run; recommendation: `docs/examples/synthetic-mr/make_wav.py` with a differential test against `make_wav` of the support module (not pinned here; the data directory test allows extra files except audio, store, report and database files).
2. **The recording is generated, and only its zeros are pinned.** No hash of the file is asserted (libm rounding), and the file is not committed (3.2 MB of synthetic tones with no value, and a binary the guard would have to know). The chunk plan and the exact zero frames are pinned instead.
3. **The empty-chunk variant is regenerated from the pipeline.** See 2.3. The hand prediction was wrong on the placement of tied words; the aligner rule decides, the README documents the reasoning, an independent implementation reproduces it. Recommendation for the future: any new variant with ties is generated first and checked by the independent aligner before it is written down.
4. **T-DEG uses the small call, not the 68 s recording.** The matrix needs six pipeline runs with controlled inputs; the fake engine and the batch D call do that in 0.2 s. The synthetic pair already runs end to end (3.4) and its non-mr variant is the real-bodies instance of the non-mr case. Rejected: six CLI runs over the long recording (slow, and the CLI cannot express a missing audio or a missing export: `tracer score` requires both).
5. **One tier.** The SCOPE phrase "fake T0, T1 and T2 agents" does not apply to this case (a recording and an export are T0 by `bundle._record_rank`, SCOPE F7); the matrix varies the inputs. The tier-degradation matrix of the general framework (Phase 1 and 2) is not touched.
6. **"Every package" for the no-network guard.** Interpreted as the three Phase 3 packages. `tracer-evalschema` and `tracer-evaldb` are not guarded: the Postgres tests of evaldb need a socket by design (marker `postgres`), and a guard would have to exempt them. Recommendation: a later task adds the guard to evalschema (no socket use expected) and to evaldb with the marker exemption; listed in section 11 as a gate gap.
7. **The guard test lives in `packages/cli/tests`.** `scripts/` has no test directory and the SCOPE-name scans (`_defined_tests`) read `packages/*/tests`. The Phase 1 guard test is in `packages/evalschema/tests/test_repo_contract.py`; adding to that file would touch the package whose source must stay byte-identical (the tests are not part of the hash, but a new file in another package is cleaner). Alternative: evalschema's `test_repo_contract.py`.
8. **Content rules read the index, name rules read everything.** Reading every blob of history would make the guard slow on a long history; the cost of reading the index blobs (about 2,700 files today) is small. A renamed report that was committed and later removed stays undetected by content: the name rules and `.gitignore` are the first line, the content rules the second (the owner's real risk is the staged `git add -A` of an `--out` directory).
9. **The allowed capture log.** One exact path (6.1). Alternative: move the old log, or rename it; the file is a Phase 1 artefact the owner may want to keep as is.
10. **Blanket `*.sqlite` and `*.sqlite3` in `.gitignore`.** Broader than `tracer.sqlite*`, because `--out` names are the user's and a copy of the store is usually renamed; the guard has the same suffix rule. Alternative: only `tracer.sqlite*`. The repository holds no SQLite file today.
11. **The marker is part of the report contract.** Batch E 5.1 already puts `<meta name="tracer-report" content="1">` in the head; batch F adds a test that it appears exactly once, before `<style>` and `<script>` (a change in the report writer that moves or renames it breaks the guard silently otherwise). If the report writer changes the marker, the guard regex changes with it.
12. **Batch F depends on the wording of batch E.** The e2e tests assert the stdout labels and the report ids of the batch E contract (3.1 and 5.5). A wording change there is a change here, in the same commit.
13. **The e2e tests do not import the batch E test files.** The in-process run (`Environment`, `run`) is eleven lines in `cli_batchf_support.py`; importing `cli_batche_support` would couple two batches that are moved at different times. Cost: the duplication.
14. **`test_unit_tests_open_no_socket` in three places.** One name, three proofs. The SCOPE-name guard accepts any definition; each package proves its own conftest.
15. **No golden hash of the report.** SCOPE P3-26 says "report bytes stable"; the tests pin determinism (two runs, same bytes; the report equals `render_report` of the stored run) and every number and cell, not a sha256 of the page, because a hash would pin the style text and layout of the batch E writer before it exists. Recommendation: when the report is final (P3-23 done and P3-32 written), add a golden file with a regeneration script, as the Phase 1 golden fixtures have.
16. **Mutation testing and stale bytecode.** A first mutant run restored files within the same second as the mutation; for same-size edits Python reused the mutated bytecode, so every later result of that run was suspect and was discarded. Found because a mutant of the summary label was "killed" by the chunk-plan test. The final run uses a fresh `PYTHONPYCACHEPREFIX` per mutant. Anyone who reruns mutants over the repository should do the same.
17. **Python 3.15 and Windows.** Not run (non-blocking leg; POSIX file modes and `git` on a POSIX path are assumed, as in batches A to E).

## 8. Coverage against the gate

- Gate 4 (no network in unit tests): the two new conftest files, `test_unit_tests_open_no_socket` (3 places), the three-package check, and the e2e Sarvam path on a mock transport with the canary key searched in every output.
- Gate 7 (T-DEG matrix): section 4.
- Gate 8 (`check_run_bundle` for every produced bundle): every T-DEG case and every stored e2e run loads through the store, which validates on read (the pipeline already called `check_run_bundle`).
- Gate 5 (no accuracy claim): the e2e stdout and report text are asserted line by line and contain none of the words; the deny-list scans themselves are batch E's.
- Gate 11 (no call text in a committed file): the guard of 6, and the data are invented.
- Not tested here: a browser; the hosted CI; Postgres (the e2e store is SQLite); a real send; the guard on the real tree (CI runs it on every push; qa ran the oracle guard on the scratch tree, section 11).

## 9. Test inventory (every SCOPE name, exact)

| Task | Names | File |
|---|---|---|
| P3-25 data | the 15 `test_synthetic_*` functions of section 2 | `test_synthetic_pair.py` |
| P3-26 | `test_e2e_synthetic_mr_exact_outputs`, `test_e2e_report_and_store_consistent` | `test_e2e_synthetic_mr.py` |
| P3-26 extra | `test_e2e_synthetic_mr_report_shows_the_exact_flags`, `test_e2e_report_bytes_are_stable_across_runs`, `test_e2e_nonmr_chunk_is_not_scored_exit_2_with_three_errors`, `test_e2e_empty_chunk_makes_no_turn_is_counted_and_is_scored`, `test_e2e_sarvam_path_over_mock_transport_equals_the_replay_run`, `test_e2e_every_output_it_writes_is_refused_by_the_repository_guard` | same |
| P3-27 | `test_tdeg_audio_and_export_scored`, `test_tdeg_no_audio_missing_input_transcript`, `test_tdeg_no_export_missing_input_agent_transcript`, `test_tdeg_export_without_declaration_language_not_observed`, `test_tdeg_non_mr_chunk_is_not_scored`, `test_tdeg_export_without_agent_turn_missing_input_agent_transcript`, `test_tdeg_schema_identical_across_cases`, `test_unit_tests_open_no_socket` (evalcore, cli) | `test_tdeg_matrix.py`, `test_evalcore_no_socket.py` (evalcore) and `test_cli_no_socket.py` (cli) |
| P3-27 extra | `test_tdeg_missing_input_is_decided_before_the_language`, `test_tdeg_every_case_is_deterministic_for_the_same_inputs` (6), `test_unit_tests_open_no_socket_each_phase3_package_has_the_guard` (3), `test_unit_tests_open_no_socket_the_guard_clears_the_key_from_the_environment` | same |
| P3-34 | `test_guard_rejects_staged_report_store_and_call_log` | `test_guard_report_store_call_log.py` |
| P3-34 extra | by name and by content for each output, their negatives, the capture-log exception, unchanged rules, order, stdlib script, `.gitignore` (2 tests, 20 cases) | same |
| guards | `test_every_test_named_by_the_scope_rows_of_batch_f_exists[P3-26|P3-27|P3-34]` (reads SCOPE and the test sources) | `test_evalcore_no_socket.py` |

## 10. Required changes to existing tests (named spec change; qa did not edit them; tightening only, nothing removed that a test still asserts)

Two existing tests assert that a pending name leaves `PENDING` in the commit that adds its test (`assert not PENDING & defined`), so they go red the moment the batch F files are moved. Apply batch E's change first (the order of the test move is D, E, F); then, in the same commit as the move:

- `packages/evalcore/tests/test_adr0023_recorded_scoring.py`: remove 8 names from `PENDING`: `test_e2e_synthetic_mr_exact_outputs`, `test_tdeg_audio_and_export_scored`, `test_tdeg_export_without_agent_turn_missing_input_agent_transcript`, `test_tdeg_export_without_declaration_language_not_observed`, `test_tdeg_no_audio_missing_input_transcript`, `test_tdeg_no_export_missing_input_agent_transcript`, `test_tdeg_non_mr_chunk_is_not_scored`, `test_tdeg_schema_identical_across_cases`. Two names remain (`test_concept_page_states_no_accuracy_claim`, `test_measurement_report_has_counts_and_denominators`: P3-32 and P3-31).
- `packages/evalproviders/tests/test_evalproviders_adr0024.py`: remove `test_guard_rejects_staged_report_store_and_call_log`; `PENDING` becomes `frozenset()` (also update the comment block above it).

`spec-change.diff` is the reference (against the tree after batch E's change). Before: the sets contain the names; after: they do not. The guards that PENDING is a subset of the SCOPE names and that the ADRs still cite the names keep holding. ADR text edits that this batch makes necessary and qa did not make: ADR 0024 rule 17 ("the test configuration of every package"): say "of the three Phase 3 packages" (ambiguity 6); ADR 0023 invariant text for the e2e test: none.

## 11. Evidence (`docs/phase3/evidence/red/batch-f/`)

Counts (2026-10-26). Everything ran in a scratch copy of the repository under the session scratchpad (one environment per Python through `UV_PROJECT_ENVIRONMENT`; the shared `.venv` was not used and stays on its default Python); the real tree changed only under `docs/phase3/` (the staged tests, this contract, the data directory edits of section 2.3 and the evidence files): nothing was created or edited under `packages/` or `scripts/`. The copy holds no private fixture, no `.env.local` and no `marathi_samples`; nothing opened a network socket (the new conftest guards, the Sarvam path on a mock transport) and the only key is the canary.

- red (`red.py3.11.log` to `red.py3.14.log`, `red-reasons.py3.13.txt`): the 10 staged files moved into `packages/evalcore/tests` and `packages/cli/tests` of a copy that is the tree as it stands (batches B to D implemented, the batch E command and the guard change absent, the data still at the staging path), `--tb=line`: **46 failed, 71 passed** of 117 on 3.11, 3.12, 3.13 and 3.14. The 46: 8 e2e tests (`tracer_cli.cli` has no attribute `Environment`: batch E is not implemented), 37 guard and `.gitignore` tests (the guard exits 0 where it must exit 1; ten paths are not git-ignored) and 1 data test (the staging data is not at `docs/examples/synthetic-mr`). The 71 that pass are honest: the 14 T-DEG tests pin behaviour batch D already implements (they are new names for existing behaviour; they fail the PENDING guards instead, below), the 9 no-network tests (the conftest files move with them), 16 data tests (the data exist) and 32 negatives of the guard. `red-pending-guards.py3.13.log`: with the files moved and without the spec change of section 10, `test_adr0023_rules_are_tested` and `test_adr0024_rules_are_tested` fail ("these tests exist now; remove them from PENDING").
- oracle (`oracle.py3.11.log` to `oracle.py3.14.log`): the batch E oracle (`../batch-e/oracle-src.patch` as updated on 2026-10-26, with its staged tests and its spec change), the batch F guard and `.gitignore` oracle (`oracle-guard.patch`, 171 lines), the data moved to `docs/examples/synthetic-mr/`, and `spec-change.diff`: the batch F files on 3.11, 3.12, 3.13 and 3.14: **117 passed** each. `oracle-guard-on-tree.log`: the oracle guard run on the whole scratch tree (about 2,770 tracked files, including `report.py`, which holds the marker as a string, and the tracked capture log) exits 0.
- whole workspace with the oracle (`oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`): **12,038 passed, 1,288 deselected** on each (11,790 existing, 131 of batch E by difference, 117 new). A first run found one failure, the evidence script `check_empty_chunk_alignment.py` without an SPDX header (`test_spdx_headers_present_on_all_sources`); the header was added and the three runs repeated. The two new conftest guards break no earlier test.
- mutants (`mutants.txt`): **95 mutants, 93 killed, 2 equivalent (with reasons), 0 survived unexplained**: the guard script (30: each rule of 6.1, the allow-list, messages, order, history, index versus staged, private and env rules), `.gitignore` (11), the aligner, normaliser, flag threshold, language gate, tier, available inputs, chunker, mapping, replay engine, summary, report and cli (24), the synthetic data (14), the T-DEG paths (5) and the six conftest guards (11, with the evalproviders one). The second complete run (the first, which reused stale bytecode, was discarded) left four survivors and one mutant that did not apply: a README heading that was also matched by prose elsewhere in the file, a conftest replacement that was checked by text and not by AST, and the missing cli copy of the guard test led to test changes; the aligner tie-break and chunk-edge mutants survive the batch F data by design and are killed by the batch C tests; one mutant text was corrected. The final run is the one in `mutants.txt`.
- the staged files alone: `ruff check`, `ruff format --check` and `mypy` (repository configuration) pass on the 10 files; no non-ASCII byte in a staged file, in this contract, in a patch, in a log or in the check script (the data README and the export are UTF-8 by design).
- the independent check: `check_empty_chunk_alignment.py` and its output `check_empty_chunk_alignment.out.txt` (agrees with `expected_results.json`).

Not run: Python 3.15 (non-blocking); GitHub-hosted execution; Windows; the `[postgres]` leg (the e2e store is SQLite); any real send (owner-gated); a browser. Timing: the batch F files take about 9 to 12 seconds on one core per Python; the whole workspace takes about 5 minutes; no test asserts a time. Logs are scrubbed of home, scratch and session paths and are under 5,000 lines each.
