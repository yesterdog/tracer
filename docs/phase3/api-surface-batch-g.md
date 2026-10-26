# Phase 3 batch G contract: the fixes of the P3-28 security audit (F2, F3, F4, F5, F7, F8, F9, F10)

Author: qa, 2026-10-26. Source: the decision log entry "PHASE 3 SECURITY AUDIT P3-28" (the required behaviour of each finding), ADR 0024 (rules 3, 4, 9, 15, 17), ADR 0023, the contracts of batches B, D, E and F, and the implemented `tracer-evalcore`, `tracer-evalproviders` and `tracer-cli` at HEAD 47f2d70 plus the dirty batch F tree. qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow, script or earlier test. Every statement below is pinned by a test named in section 11, unless it says "not pinned". The lead auto-approves the exact names, codes and wording (owner instruction, 2026-10-23); where the contract chose, the rejected option is in section 10.

No schema change (SCOPE D5): `ConsentRecord.reference` and `.recorded_at` already exist. One new public function in `tracer-evalcore`, one new module-level warning class in `tracer-evalproviders`, new options and codes in `tracer-cli`. Nothing is added to `tracer_evalschema` or `tracer_evaldb`.

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/` or `scripts/` and nothing under them was edited. The tests are staged at

```text
docs/phase3/evidence/red/batch-g/staging/tests/cli/          -> packages/cli/tests/          (2 files)
docs/phase3/evidence/red/batch-g/staging/tests/evalproviders/ -> packages/evalproviders/tests/ (1 file)
docs/phase3/evidence/red/batch-g/staging/tests/evalcore/      -> packages/evalcore/tests/      (1 file)
```

No `__init__.py`, no `conftest.py` (the autouse no-network guards of batch F cover the new modules). The dev's first act in batch G:

```text
git mv docs/phase3/evidence/red/batch-g/staging/tests/cli/*.py packages/cli/tests/
git mv docs/phase3/evidence/red/batch-g/staging/tests/evalproviders/*.py packages/evalproviders/tests/
git mv docs/phase3/evidence/red/batch-g/staging/tests/evalcore/*.py packages/evalcore/tests/
```

and, in the same commit, the six named spec changes of section 9 (`spec-change.diff` is the reference) and the `.gitignore` line of section 5.4.

| File | Holds | Functions | Collected |
|---|---|---|---|
| `cli/cli_batchg_support.py` | helpers, not collected: `g_args`, `sarvam_run`, `ProbeEngine`, `PlantingScript`, recovery readers | - | - |
| `cli/test_cli_batch_g.py` | F2, F4, F5, F7, F8, F9 at the level of the command | 56 | 94 |
| `evalproviders/test_sarvam_hardening.py` | F3, F7, F9, F10 at the level of the adapter | 24 | 33 |
| `evalcore/test_scoring_export_token_count.py` | the export token count the command checks before a send (F2) | 7 | 12 |
| total | | 87 | 139 |

Every test builds the command line with `g_args` (cli support): the consent flags, the reference, the instant and the explicit ceiling are always present, so the tests agree with the contract whether or not the spec change of `score_args` (section 9, SC1) is applied. The command is run in-process (`tracer_cli.cli.run` with an injected `Environment`, batch E), over the real Sarvam engine on a scripted `httpx.MockTransport` or over an injected fake engine. Nothing opens a socket, no test reads the machine's environment, a key or a private fixture, and the only key is the canary. The permission tests (`chmod 0o500`, read-only store) are skipped when the process is root (root ignores file modes); the FIFO tests run the call on a thread with a 10 s deadline so that a blocking implementation fails instead of hanging.

Commands (after the move): `uv run --locked --all-groups pytest packages/cli/tests packages/evalproviders/tests packages/evalcore/tests` or the whole workspace.

## 1. What the audit found and what is fixed here

| Finding | Priority | Fix | Section |
|---|---|---|---|
| F2 a failure after the paid send loses the paid result | P2 | everything that can fail on the output side is checked before the send; a failure after the send keeps the usage and writes the report | 3, 4 |
| F3 httpx reads the proxy and certificate environment | P2 | `trust_env=False` | 6.1 |
| F4 no way to send one chunk first, no spend cap | P2 | `--max-chunks` (required for sarvam, ceiling 200) and a 30,000 ms window limit | 2.2 |
| F5 consent reference cannot be recorded; no code gate | P2 | `--consent-reference`, `--consent-recorded-at`, and `--stt sarvam` refused unless consent is obtained | 2.3 |
| F7 call log directory mode and links | P3 | directory 0o700, `O_NOFOLLOW` | 6.2 |
| F8 `environ` in a repr | P3 | `field(repr=False)` | 2.6 |
| F9 `--env-file` unbounded and unchecked | P3 | 64 KiB, regular files only, mode warning | 6.3 |
| F10 compressed replies | P3 | `Accept-Encoding: identity` | 6.1 |

## 2. The command

```text
tracer score AUDIO --transcript FILE --rubric FILE --declared-language CODE
             --caller-origin {human,simulated} --consent {obtained,not_obtained,unknown}
             [--consent-reference TEXT] [--consent-recorded-at INSTANT]
             --retention POLICY --out DIR --stt ENGINE [--dry-run] [--embed-audio]
             [--env-file FILE] [--price-file FILE] [--priced-at INSTANT]
             [--max-chunks N] [--max-chunk-ms MS] [--search-ms MS]
             [--accept-provider-downmix] [--accept-export-warnings]
```

New constants of `tracer_cli.cli` (also in `__all__`): `MAX_SARVAM_CHUNKS = 200`, `MAX_SARVAM_CHUNK_MS = 30_000`, `MAX_CONSENT_REFERENCE_CHARS = 200`, `RECOVERY_FORMAT = "tracer.usage_recovery/1"`. The three new options are optional in the parser (`required=False`, `default=None`), with the metavars above and a help text (the deny-list of batch E 5.7 applies to it). `--max-chunks` help says that it is required with `--stt sarvam` and that sarvam accepts at most 200.

### 2.1 Usage errors (exit 64, the `usage:` block and one `tracer: error: <message>` line, nothing on stdout, nothing created, no engine factory call)

- `--max-chunks` is a positive whole number (decimal digits, no sign, no zero, no fraction, no exponent, no space, at most 9 digits); anything else is a usage error that names `--max-chunks` (and is not the "unrecognized arguments" message).
- `--consent-reference` is 1 to 200 characters, has no leading or trailing white space, and holds no character of Unicode general category `Cc` (controls, also tab and newline), `Cf` (format: the bidi controls, ZWJ, ZWNJ, the byte order mark), `Cs`, `Co`, `Cn`, `Zl` or `Zp`. Spaces inside are fine, and so is any other non-ASCII character (a Devanagari word is a valid reference). The message names `--consent-reference` and never echoes the value.
- `--consent-recorded-at` is an RFC 3339 instant with the rules of every instant of the command (batch E 2.1: `YYYY-MM-DDTHH:MM:SS`, optional fraction, then `Z` or an offset); it is stored in UTC.
- `--consent obtained` needs both `--consent-reference` and `--consent-recorded-at`: if either is missing the message names both options. For `not_obtained` and `unknown` neither is ever required; if given they are stored as given (ambiguity 7).
- With `--stt sarvam` (real run or dry run): `--max-chunk-ms` above 30,000 is refused (message names `--max-chunk-ms`; exactly 30,000 is accepted). `--max-chunks` above 200 is refused (message names `--max-chunks`; exactly 200 is accepted).
- With `--stt sarvam` and no `--dry-run`: `--max-chunks` is required (message names `--max-chunks`). There is no default (ADR 0016 Decision 2; owner rule: no defaults for inputs that decide what leaves the machine). The dry run does not need it (ambiguity 1).
- With `--stt fake:<dir>` none of these limits applies: `--max-chunks` is optional (and obeyed if given, up to any positive value), `--max-chunk-ms` is the user's to choose.

### 2.2 The ceiling (F4)

`--max-chunks N` refuses a plan with more than N chunks: exit 4, one stderr line

```text
error: max_chunks_exceeded: the plan has <n> chunks and --max-chunks is <N>
```

with no engine factory call, no request, nothing created. A plan of exactly N chunks is allowed. The ceiling counts chunks, not requests: a chunk can take up to `MAX_ATTEMPTS` = 4 requests (ambiguity 12, and the open finding F6 on retry billing). The hard ceiling 200 is a technical bound (about 83 minutes of audio at the 25 s default window), not a measured value; raising it takes a source change. Both the real run and the dry run apply the check.

### 2.3 Consent (F5)

`ConsentRecord(status, reference, recorded_at)` is built from the three options and sits on the privacy marker of the artifact and of both transcripts (the three markers of a stored run). Neither value is printed on stdout or stderr (also not by the dry run), shown in the report, written to the call log or sent to the provider; the store is the only place (`test_audit_f5_the_reference_is_never_printed_or_reported_only_stored`, with a canary found in the store).

- `--consent-recorded-at` later than the clock instant of the run is refused: exit 4, `error: consent_recorded_at_after_run: ...`. The same instant, also written in another zone, is allowed. The check is evaluated before the language check.
- **The code gate.** `--stt sarvam` with `--consent` not `obtained` (`not_obtained` or `unknown`) is refused, in a real run and in a dry run: exit 4, one line

```text
error: consent_not_obtained: --stt sarvam sends the audio to the provider and needs --consent obtained
```

with no engine factory call, no request, nothing created. It is decided right after the language check and before any file is read (a missing transcript file does not change the message). A usage error (64) comes first. `--stt fake:<dir>` is never gated: nothing leaves the machine.

### 2.4 Order of operations (replaces batch E 2.2; pinned where a test says so)

```text
 1. parse; the usage errors of 2.1 (64)                                      the engine factory is never called
 2. read the clock once; build ConsentRecord and RetentionPolicy;
    consent_recorded_at after the clock instant -> 4
 3. the language check (4, batch E)
 4. the consent gate of 2.3 (4)                                              before any file is read
 5. the output path: a "?" anywhere in the resolved path -> 4 output_path_invalid;
    an existing path that is not a directory -> 4 (batch E)
 6. read rubric, price file, export, audio; build the configuration;
    read the export as the pipeline will; chunk the audio                    (batch E steps 3 to 5)
 7. plan checks (4): max_chunks_exceeded; for --stt sarvam only, the export
    has more tokens than ALIGN_MAX_TOKENS -> export_tokens_above_alignment_cap
 8. the report name exists -> 4 report_exists; --embed-audio read and hashed (batch E)
 9. --dry-run: print the plan (2.5) and return 0: nothing is written, the output directory is
    not created or probed, no key and no env file are read, no engine factory call
10. (real run) the output location is probed WITHOUT creating anything:
    the nearest existing ancestor of --out must be a directory with write and search permission;
    an existing <out>/tracer.sqlite must be a regular, writable file       -> 4 output_unavailable
11. the engine factory is called (sarvam: the key is read first, from the environment or the
    env file under the rules of 6.3 with the warning of 2.7; then the call log is opened under
    the rules of 6.2). An OSError from the factory -> 4 output_unavailable.   a missing key stops
    here and leaves no directory behind (batch B and E, unchanged)
12. engine.preflight(plan), if the engine has such a method (3): SttConfigurationError -> 4,
    nothing the command writes exists yet
13. the store is opened: the directory is created (mode 0700, parents too), tracer.sqlite is created
    and migrated. Any failure (a directory or a garbage file in place of the store, a read-only
    file, a full disk) -> 4 output_unavailable, before the first request
14. --embed-audio warning (stderr); the pipeline runs; engine.transcribe is the first call that can
    send audio                                                                 <- THE POINT OF NO RETURN
15. store_run; the report; the result block; the exit code (4)
```

The point of the order: every refusal that depends on the inputs, the plan or the output side happens at steps 1 to 13, all before step 14. The store is open and the directory exists when the first byte is sent (`test_audit_f2_the_store_is_open_and_the_directory_exists_before_the_engine_is_called`: the engine sees the directory, 0700, and a store file when `transcribe` is called, and nothing yet when `preflight` is called). `EngineRequest` and the factory contract of batch E are unchanged; the factory is still called exactly once per real run and the engine's `close()` exactly once, whatever happens.

### 2.5 The dry run

The six lines of batch E 3.3 plus one, before the last line:

```text
chunks: <n>
audio seconds: <canonical decimal>[ (rounded half-even to 18 digits)]
calls: <n>
cost: <...>
max attempts per chunk: 4
max chunks: <N> (the plan has <n>: within the cap)       or      max chunks: not set
dry run: nothing was sent
```

A plan over the ceiling, an export over the alignment cap (sarvam) and a consent gate refusal are printed as the same exit-4 refusal lines as a real run (stdout stays empty): "the dry run prints the same refusal as a plan error". The dry run needs no `--max-chunks` for `--stt sarvam` (it is how a user learns the number of chunks) and then prints `max chunks: not set`.

### 2.6 `Environment.environ` and `EngineRequest.environ` (F8)

Both fields are `dataclasses.field(repr=False)`. The field stays (a test reads `request.environ`), only `repr`, `str` and `format` of the dataclass no longer show the mapping, so a canary key in `environ` never appears there (an assertion failure or a log line that prints the object cannot leak it). Every other field is still in the repr.

### 2.7 The env file warning on stderr (F9)

Printed once, before the engine runs, only when the default engine factory read an env file whose mode has any group or other permission bit:

```text
warning: the env file can be read by other users (mode 0o644); run chmod 600 on it
```

(the mode is `oct()` of the permission bits, so `0o640` for a group-readable file; no path, no file name, no key, no variable name). The command collects `tracer_evalproviders.secrets.EnvFileModeWarning` (6.3) raised while the factory runs (`warnings.catch_warnings(record=True)`, `simplefilter("always")`) and writes one `warning: <message>` line per warning to `Environment.stderr`; other warnings are not printed. An owner-only file prints nothing. An injected factory reads no env file, so it prints nothing. The exit code is not changed by the warning.

## 3. The engine preflight hook

An engine may have an optional method `preflight(plan: ChunkPlan) -> None`. If it exists (`getattr`), the command calls it once, after the factory and before the store is opened (step 12), with the chunk plan. It may raise `SttConfigurationError` (exit 4, the stderr line `error: <the error's text>`, `close()` is still called once, `transcribe` is never called). Nothing the command itself writes exists at that moment.

Why it exists: the existing guarantee "`replay_extra_file` is refused before anything is written" (batch E 4.2, pinned by `test_replay_directory_rules`) was true only because the store was opened after the pipeline. With the store opened before the send, the replay engine needs a way to refuse a stale directory earlier. `ReplayStt.preflight(plan)` does that (it raises `replay_directory_unreadable` or `replay_extra_file` exactly as `transcribe` does; `transcribe` keeps its own check). `SarvamStt` needs none: its only pre-request refusal (`language_code_for`) is a constant for the fixed language. Rejected: deleting what the command created when the engine refuses (a deletion in the user's directory on an error path); changing the existing test to allow an empty store and directory (weakens a guarantee). `test_audit_f2_an_engine_refusal_before_the_send_leaves_nothing_behind` pins the hook with a probe engine.

## 4. Failures after the paid send (F2)

Nothing after step 14 re-sends audio: `engine.transcribe` is called once and the Sarvam client's retries belong to the engine. After `transcribe` returned, the command works from what it holds in memory. The command wraps the engine in a thin recorder so that the outcome (usage) of `transcribe` is known even if the pipeline raises later.

| What fails (after the send) | What is kept | Exit | stdout | stderr |
|---|---|---|---|---|
| `store.store_run` raises (store write) | the report is written from the run in memory; a recovery file (5) holds the usage and cost | 1 | the result block, then `bundle: <id>`, `store: not written`, `report: <path>`, `usage recovery: <path>` | `error: unexpected failure (<exception class>)` |
| the report write raises (for example the name appeared: O_EXCL) | the store holds the run and its usage; no recovery file | 1 | the result block, `bundle`, `store: <path>`, `report: not written` | the same line |
| both fail | the recovery file | 1 | `store: not written`, `report: not written`, `usage recovery: <path>` | the same line |
| the pipeline raises after `transcribe` returned (a cost builder, a bundle check) | the recovery file from the recorded outcome (`failed_step` `pipeline`); no store row, no report (no run exists) | 1 | exactly one line: `usage recovery: <path>` | the same line |
| alignment too large on the speech side, a failed chunk, a language gate | not failures: the pipeline returns a bundle; stored and reported as in batch E | 2 or 3 | batch E | batch E |

In the first and third rows, `usage recovery: not written` is printed when there is nothing to keep (no chunk was answered: no recovery file is created for a send that cost nothing) or the file could not be created (it never overwrites). An exception message is never printed or written (it could hold a key or call text): only its class name.

The export side of "alignment too large" is refused before the send (step 7, `--stt sarvam`). The speech side cannot be known before the send; `test_audit_f2_a_speech_side_over_the_cap_after_the_send_is_stored_and_reported` pins that the paid answer is stored, the call is `not scored: input too large to compare` (exit 2) and the report is written.

## 5. The recovery file

### 5.1 Name, mode, writing

`<out>/usage-recovery-YYYYMMDDTHHMMSSZ.json`, the UTC run instant of the report name. Created exclusively (`O_CREAT | O_EXCL`, mode 0600; an existing file is never overwritten or followed), ASCII (`ensure_ascii`), written only after a failed store write or a failed pipeline and only when at least one usage record exists. It holds no call text, no key, no path and no exception message.

### 5.2 Content

```text
{
 "format": "tracer.usage_recovery/1",
 "created_at": "2026-10-25T09:00:00.000000Z",          the run instant, microseconds, Z
 "failed_step": "store" | "pipeline",
 "error_class": "<exception class name>",
 "usage_records": [ <UsageRecord JSON, model_dump(mode="json")>, ... ],   one per answered chunk
 "cost_records": [ <CostRecord JSON> ... ],              the bundle's, [] when no bundle exists
 "cost_summaries": [ <CostSummary JSON> ... ],           the bundle's, [] when no bundle exists
 "pricing_catalogs": [ <PricingCatalog JSON> ... ]       the bundle's, [] when no bundle exists
}
```

Each record validates against its schema class. The usage quantities equal what an un-failed run stores (`test_audit_f2_the_recovery_file_is_what_a_clean_run_stores`, with a price file: the cost records and the summary total equal the clean run's). The usage of a failed chunk run is the answered chunks only (`test_audit_f2_the_usage_of_the_chunks_that_were_paid_is_kept_when_a_later_chunk_fails`).

### 5.3 What it is for

It is a recovery aid, not a second store: the user (or a later `tracer` command, BL candidate) can load the usage into a store or reconcile with the provider dashboard. No command reads it in Phase 3.

### 5.4 `.gitignore`

One line, next to the batch F lines (pinned by `git check-ignore --no-index`; narrow, so notes with a similar name are not hidden):

```text
usage-recovery-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.json
```

The repository guard (P3-34) is not extended: the file holds usage and request ids, not call text (ambiguity 5).

## 6. The adapter (`tracer-evalproviders`)

### 6.1 `SarvamClient` (F3, F10)

- `httpx.Client(..., trust_env=False)`: the client reads no `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` (any case), `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE` or `CURL_CA_BUNDLE`, and `.netrc`. Pinned four ways: with all of these planted (the cert file and directory pointing nowhere) a client with no injected transport is built without error (`trust_env=True` would raise `FileNotFoundError` for the certificate file), `client._http.trust_env is False`, no proxy transport is mounted (`_mounts == {}`, the pool is not a proxy pool), and the default transport still verifies TLS (`CERT_REQUIRED`, `check_hostname`); the source passes the literal `trust_env=False` and `follow_redirects=False` to the only `httpx.Client`. No public parameter is added (the batch B test that no entry point has a parameter named `trust_env`, `proxy`, `verify` and so on stays true). Effect on ADR 0024 rule 3: its trade-off ("a user behind a corporate proxy needs the proxy environment that httpx reads") is reversed (ambiguity 10).
- Every request carries `Accept-Encoding: identity` (exactly one header with exactly that value), also every retry; the key header, the method and the multipart body are unchanged. A server that compresses anyway is still decoded and still bounded by the existing size cap (the existing tests).

### 6.2 `CallLog` (F7)

- `CallLog.open(out_dir)` creates a missing leaf directory with mode 0o700, whatever the umask (parents are created with the default mode); an existing directory keeps its mode (the user's directory is not changed).
- The log is opened with `O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW`, mode 0600: a symbolic link in place of `sarvam_call_log.jsonl`, also a dangling one, is refused with an `OSError` before anything is written through it (the target is neither written, nor created, nor chmodded); the same for the constructor `CallLog(path)`. An existing regular file with a looser mode is still tightened to 0600 (batch B 9.3). In the command the `OSError` from the factory is exit 4, `output_unavailable`, with no request.

### 6.3 `secrets.load_api_key` (F9)

```text
ENV_FILE_MAX_BYTES = 65_536
class EnvFileModeWarning(UserWarning)       message: "the env file can be read by other users (mode 0o644); run chmod 600 on it"
```

With `env_file` the file is opened read-only and non-blocking (so a FIFO cannot block), then `fstat` decides: not a regular file (a directory, a FIFO, a device such as `/dev/null`) -> `SttKeyMissingError(reason="env_file_not_regular")`; more than 65,536 bytes -> `reason="env_file_too_large"` (at most 65,536 + 1 bytes are read; a file of exactly 65,536 is read, a file of 65,537 is refused even if the key is the first line); any other failure to open or read -> `env_file_unreadable` (unchanged). A symbolic link to a regular file is read (the link is followed by `stat`). A mode with any group or other bit (`mode & 0o077`) emits exactly one `EnvFileModeWarning` through `warnings.warn` before the content is parsed; the message holds the mode and nothing else; a warning is never a refusal (the key is still returned, or `key_missing` is still raised). Reading the key from `environ` never warns. Errors keep the batch B rule: the reason key only, no path, no line, no file name.

## 7. `tracer-evalcore`: `scoring.export_token_count`

```text
export_token_count(export_turns: Sequence[Turn], normaliser: str) -> int
    len(tokenise_turns(_text_turns(export_turns, timed=False), normaliser).tokens)
```

Public (in `scoring.__all__`), pure. The number of tokens the aligner will see for the export: every export turn (both roles), `seq` order, the normaliser of the snapshot (`definitions.parameters_of(snapshot).normaliser`, `mr-Deva/1`). It equals `tokens.export` of the stored flags document of the same run, counts both roles, counts tokens and not whitespace words (a line with no space is thousands of tokens), drops ZWJ/ZWNJ inside a word, is independent of the order the turns are given in and is 0 for no token. An unknown normaliser is the existing `UnknownNormaliserError` (a `ValueError`). `ALIGN_MAX_TOKENS` stays 10,000 and the command refuses `tokens > ALIGN_MAX_TOKENS` (10,000 is allowed, 10,001 is not):

```text
error: export_tokens_above_alignment_cap: the export has <t> tokens and the alignment cap is 10000
```

The check applies to `--stt sarvam` only: a replay pays for nothing, and its oversize call is scored as far as it can be (`not scored: input too large to compare`, exit 2, store and report written: `test_audit_f2_a_replay_is_not_refused_for_the_size_of_the_export`).

## 8. Messages and codes added

| Where | Code / text (prefix, `error: ` on stderr, ASCII, one line) | Exit |
|---|---|---|
| usage | `--max-chunks ...`, `--consent-reference ...`, `--consent-recorded-at ...`, `--consent obtained needs --consent-reference and --consent-recorded-at`, `--stt sarvam needs --max-chunks`, `--max-chunks above 200 is refused for --stt sarvam`, `--max-chunk-ms above 30000 is refused for --stt sarvam` | 64 |
| plan | `max_chunks_exceeded`, `export_tokens_above_alignment_cap` | 4 |
| consent | `consent_not_obtained`, `consent_recorded_at_after_run` | 4 |
| output | `output_path_invalid`, `output_unavailable (<reason or exception class>)` (a directory not writable, the store file not writable, the factory or the store raised an OSError or a database error) | 4 |
| key / env file | `env_file_not_regular`, `env_file_too_large` (new reasons of `SttKeyMissingError`, printed like `key_missing`) | 4 |
| after the send | `error: unexpected failure (<class>)` plus the stdout lines of section 4 | 1 |

The usage text of exit 64 is the argparse message of the option; only the option name is pinned. The exit-code table of batch E is unchanged (six codes); no seventh code is added (ambiguity 6).

## 9. Named spec changes to existing tests (qa did not edit them; tightening only; the reference is `spec-change.diff`)

The oracle shows that these and only these existing tests change. With the batch G source in place and no test change, 88 existing tests (57 functions), all in `packages/cli/tests`, fail: the default command of `score_args` is `--stt sarvam --consent unknown` with no ceiling, and the gate and the required ceiling now apply to it. SC1 alone brings the 88 down to 7; SC2 to SC6 are those 7 (two end-to-end tests, one usage test, one records test, one dry-run test and two adversarial cases). With SC1 to SC6 nothing fails in `packages/cli/tests`, `packages/evalproviders/tests` or `packages/evalcore/tests` (the 1,480 existing tests there pass). Each change makes an expectation stricter or states a contract line the code now has; nothing is deleted or skipped.

- **SC1 `packages/cli/tests/cli_batche_support.py`, `score_args` defaults.** Before: `"--consent": "unknown"` and no ceiling. After:

```text
-        "--consent": "unknown",
+        "--consent": "obtained",
+        "--consent-reference": "owner statement 2026-10-25",
+        "--consent-recorded-at": "2026-01-01T00:00:00Z",
         "--retention": "keep",
         "--stt": "sarvam",
+        "--max-chunks": "50",
```

  Why: the default command of every batch E test is `--stt sarvam` with an injected engine; the code gate and the required ceiling now apply to it. The values are tests values (the instant is before every clock the tests use; 50 is far above every plan of the tests). Every test that passes `options` keeps working; `None` removes an option as before.
- **SC2 `packages/cli/tests/cli_batchf_support.py`, `score_argv`.** For `stt == "sarvam"` the consent block becomes `--consent obtained --consent-reference "owner statement 2026-10-25" --consent-recorded-at 2026-10-01T00:00:00Z --max-chunks 3`; for a replay it stays `--consent unknown`. Affects `test_e2e_sarvam_path_over_mock_transport_equals_the_replay_run` and `test_e2e_every_output_it_writes_is_refused_by_the_repository_guard`.
- **SC3 `test_cli_requires_every_privacy_and_language_input`.** Before: `assert f"[{flag}" not in usage` (a substring test, which `[--consent-reference TEXT]` now satisfies by accident for `--consent`). After: `assert re.search(rf"\[{re.escape(flag)}(?![-\w])", usage) is None`. Same intent, exact match.
- **SC4 `test_cli_records_the_privacy_and_language_inputs_as_given`.** The loop that records `--consent not_obtained` used `--stt sarvam` (now refused by the gate). After: it passes `--stt fake:<replay dir>` (the base bodies, `write_replay`) with `production=True`. The first block (`--consent obtained`) is unchanged and passes with the defaults of SC1.
- **SC5 `test_cli_dry_run_sends_nothing_and_prints_the_plan`.** The expected lines gain `"max chunks: 50 (the plan has 2: within the cap)"` before `"dry run: nothing was sent"`, and the comment says the command adds the last three lines.
- **SC6 `test_adversarial_inputs_never_break_report_or_store`, two cases.** `more tokens than the aligner takes` and `a huge line with a hostile tail` allowed `(2,)` (not scored, exit 2) with the default `--stt sarvam`; they are now refused before the send, `(4,)`, which the test body already checks for a refusal (one `error:` line, empty stdout, no directory, no factory call). The coverage of "an oversize call is stored and reported as not scored" moves to the new tests `test_audit_f2_a_replay_is_not_refused_for_the_size_of_the_export` and `test_audit_f2_a_speech_side_over_the_cap_after_the_send_is_stored_and_reported`.

Statements outside tests that this batch makes false, for the lead and the architect (qa edits none of them):

- Batch E contract: 2 (the command line), 2.1 (the `--consent` row says "nothing else (no reference)"), 2.2 (the order of operations: replaced by section 2.4), 2.3 (files created: the recovery file), 3.3 (the dry-run lines), 7 finding 3 (the consent reference is no longer a backlog candidate) and finding 21 (a failed store or report write after a paid transcription is no longer "usage only in the call log"), and 4.1.
- Batch B contract 3.2 (the client builds `httpx.Client` with `trust_env=False`; `Accept-Encoding: identity`), 9.3 (directory mode, `O_NOFOLLOW`) and the section on `load_api_key` (cap, regular file, warning).
- ADR 0024 rule 3 (**the trade-off sentence is reversed**: Tracer no longer reads the proxy or certificate environment; a user behind a proxy cannot use it in Phase 3, an explicit audited setting is a later task), rule 4 (the env file rules of 6.3), rule 15 (the directory is 0700, the log is never opened through a link), rule 16 (the dry run shows the cap line) and, new, a rule or a paragraph for the ceiling and the consent gate. ADR 0023 rule 17 needs nothing.
- `docs/BACKLOG.md`: BL-227 (closed by F2); F6 (retries may double-bill) stays open and now interacts with the ceiling (ambiguity 12); a row for an explicit proxy setting.
- The run book of P3-30: the dry run now shows the cap line; the real command needs `--consent obtained --consent-reference ... --consent-recorded-at ...` and `--max-chunks N`; "unset HTTP_PROXY ..." is now enforced by the code (but still good practice for `curl`).

## 10. Ambiguities and decisions (the lead auto-approves; each is implemented as recommended in the oracle and pinned as stated)

1. **Dry run without `--max-chunks`.** Pinned: allowed for sarvam, prints `max chunks: not set`. The dry run is how a user learns the chunk count before choosing a ceiling, and it sends nothing. Rejected: requiring the option in the dry run too (a user would have to guess N, and the refusal text would then be the way to learn the count). Consequence: a dry run of the exact real arguments minus the option exits 0 where the real run exits 64; the run book says to include the option.
2. **Order: the factory before the store.** The lead's text says "the store is opened and the directory created BEFORE the engine is called". Pinned: the store is opened before the engine can SEND (`transcribe`), and the output location is probed before the factory; the factory (key, call log) comes first, so that a missing key still leaves no directory behind (three existing tests pin that: `test_cli_missing_key_refuses_before_any_request_and_before_the_output_directory`, `test_cli_env_file_is_the_only_key_source` and the env file variants). Rejected: opening the store first and tolerating an empty directory after a missing key (changes three existing tests; the audit asked for protection of the paid send, not for the key check to move). What this loses: a store that cannot be opened for a reason the probe cannot see (a corrupt database) is found after the key was read and the call log created, not before; still before any request.
3. **The preflight hook** (section 3), for the stale replay directory.
4. **The export token check is for `--stt sarvam` only.** Rejected: all engines (two existing adversarial cases and the replay tests would need to change, and a replay pays for nothing). The speech side cannot be checked before the send (section 4).
5. **The recovery file.** A JSON file in `--out`, exit 1, not a new store table or a new exit code. Not covered by the repository guard: it holds usage (counts, seconds, provider request ids) and no call text. Alternative: add `usage-recovery-*.json` to the guard (a change to a file batch F is editing; cheap later). The `.gitignore` line is part of this batch.
6. **Exit code 1 after the send** rather than a seventh code. The exit table is pinned (six codes); the stdout lines carry the difference. Alternative: a new code 5 "failed after the send" (changes `EXIT_CODES` and its test).
7. **A reference given with another status** is stored as given. Alternative: usage error ("a reference needs `obtained`"). Rejected: the schema allows it, "never required" does not mean "forbidden", and a user may record a pointer for a refusal.
8. **A future `--consent-recorded-at` is refused (4).** The record claims a past event; "never assume". The same instant is allowed. Alternative: usage error 64 (the clock is not a parse-time input).
9. **The reference alphabet.** No control, format, separator, surrogate, private-use or unassigned character; any other character, any script, is fine. A ZWJ (a Devanagari conjunct written with one) is refused: the reference is a pointer to a record, not text of the call, and the safest rule is the one with no exception. Alternative: ASCII only (excludes a Marathi note), or `display_safe` markers (a stored value that differs from the input).
10. **`trust_env=False` ends proxy support** in Phase 3 (reverses the trade-off of ADR 0024 rule 3). A corporate proxy user cannot run the command; the alternative, a `--proxy` option, is the one setting that sends the key anywhere (the reason rule 3 rejects a base URL setting). A later task may add an explicit, audited setting.
11. **The probe uses `os.access`** (advisory: root, ACLs and races can differ). It is a first line only; opening the store is the real check and also happens before the send. The permission tests are skipped for root.
12. **The ceiling counts chunks, not requests.** A chunk may take 4 attempts and a retried request may be billed again (finding F6, open). `--max-chunks 1` is the way to send one chunk first: the retry bound of one chunk is 4 requests.
13. **A symlink to a regular env file is read; a symlinked call log is refused.** The first is the common case of a dotfile managed elsewhere, and the key is only read; the second would let a planted link redirect an append of the command's own log.
14. **The warning is a Python warning** (`EnvFileModeWarning`), converted to a stderr line by the command, so a library user sees an ordinary `UserWarning` and the command alone decides the wording. Alternative: a callback argument (`load_api_key(..., warn=...)`), rejected because it changes a public signature that batch B pins.
15. **POSIX only** (modes, `O_NOFOLLOW`, FIFOs); Windows is not supported (the batch A caveat).
16. **Tests use the private attributes of httpx** (`_mounts`, `_transport._pool._ssl_context`) for F3. The lock file pins httpx 0.28.1; the public `trust_env` property and the literal in the source are pinned too, so a version bump that moves the private names breaks only the two tests that read them.

## 11. Test inventory

| Finding | Tests (file), collected cases |
|---|---|
| F2 | 32 at the command: the refusals before the engine (`?` x2, unwritable directory, unwritable parent, store file in the way (directory, garbage, read-only), existing report name, the missing key, probe order, preflight refusal and the replay preflight), the token cap (3 sizes, both roles and tokens, a replay not refused, the speech side after the send), the store connection released, the failures after the send (store, report, both, pipeline, injected engine, failed chunk, nothing paid, never overwritten, clean-run equality) and the `.gitignore` line (5) (`test_cli_batch_g.py`); 12 for `export_token_count` (`test_scoring_export_token_count.py`) |
| F3 | 4 (`test_sarvam_hardening.py`) |
| F4 | 16: constants and options, required, replay, values (7), ceiling 200, window 30,000, plan over the ceiling, dry run (4) (`test_cli_batch_g.py`) |
| F5 | 34: required with obtained (3 x 2), other statuses (2), stored on every marker, verbatim, any status, reference rules (16), instant rules (6), after the run, never printed, the gate (2, order, replay) (`test_cli_batch_g.py`) |
| F7 | 6 at the adapter (`test_sarvam_hardening.py`), 2 at the command (`test_cli_batch_g.py`) |
| F8 | 2 (`test_cli_batch_g.py`) |
| F9 | 22 at the adapter (`test_sarvam_hardening.py`), 7 at the command (`test_cli_batch_g.py`) |
| F10 | 1 at the adapter (`test_sarvam_hardening.py`), 1 end to end (`test_cli_batch_g.py`, with the normal real send) |

Pins that hold on the red tree by design (they guard against a regression by the fix): the negatives of the `.gitignore` test, the already-correct env file cases (a link, an exact-cap file, an owner-only file, a missing file, the environment route), an existing directory keeping its mode, a replay not refused for the export size, a replay with another consent status, and `ALIGN_MAX_TOKENS == 10_000`. Everything else fails on the red tree, mostly with the parser's `unrecognized arguments` for an option that does not exist yet (`red-reasons.py3.13.txt`); that the tests have teeth beyond that is shown by the mutants.

## 11b. The audit-to-test guard (SCOPE P3-28): `test_p3_audit_items_cite_tests`

Staged at `docs/phase3/evidence/red/batch-g/staging/tests/cli/test_p3_audit_items_cite_tests.py` (26 collected cases, 1 SCOPE name plus its teeth). The lead moves it to `packages/cli/tests/` AFTER the batch G tests are moved, because the `test_audit_f*` names the audit cites exist in `packages/*/tests` only then. It reads `docs/security/P3-28-audit.md`, `docs/BACKLOG.md` and the AST of `packages/*/tests/test_*.py` (no product import) and asserts, through one function `problems(text, tests, backlog)`:

- the finding rows (`^\| (F\d+) \|`) are exactly F1 to F16, each once, in order; each row cites at least one test (a backticked `test_\w+`) or one `BL-NNN`; every cited test is defined in `packages/*/tests` (no PENDING allowance); every `BL-NNN` cited anywhere in the audit has a `| BL-NNN |` row in the BACKLOG;
- status (fourth column): `fixed` or `closed` needs at least one existing test, `deferred` needs a BACKLOG id, `closed` names a 7 to 40 hex commit id, and any other status is a problem;
- F1 cites `test_guard_rejects_staged_report_store_and_call_log`; F2, F3, F4, F5, F7, F8, F9 and F10 each cite a `test_audit_f<N>_*` test;
- the file holds `## Verdict for the first real send (P3-30): CONDITIONAL GO` and `P0:** none`, and a `Not verified` section that cites BL-236;
- no 8-character hex token outside the status cell of a closed row (keeps the owner's file stems out).

Teeth, in the test file: a good synthetic audit passes; 16 synthetic mutations (missing or duplicate id, unknown test, unknown BL id, no citation, deferred with a test only, fixed with a row only, closed without a commit, unknown status, no F1 guard test, wrong test family, no verdict, no P0 line, no BL-236, no section, a file stem) and 6 mutations of the real audit text each fail with the expected problem.

Evidence: on the oracle tree (the batch G tests in `packages/*/tests`) the test passes on 3.11, 3.12, 3.13 and 3.14 (26 passed each). On the tree without the batch G tests it fails (1 failed, 25 passed on each) for the right reason: 51 problems, the cited `test_audit_f*` names are undefined and F2 to F5 and F7 to F10 are "fixed but cite no existing test". File-level mutants of the audit and of the BACKLOG, each run against `test_p3_audit_items_cite_tests`: drop the F7 row, rename a cited test, a bad BL id, F6 `deferred` changed to `fixed`, F1 `closed` without its commit, an 8-hex file stem, the verdict changed, the `Not verified` heading without BL-236, and the BL-236 row removed from the BACKLOG: 9 of 9 killed.

## 12. Evidence (`docs/phase3/evidence/red/batch-g/`)

Counts (2026-10-26). Everything ran in a scratch copy of the repository under the session scratchpad (a new directory of this batch; one environment per Python through `UV_PROJECT_ENVIRONMENT`; the shared `.venv` was not used and stays on its default Python); the real tree changed only under `docs/phase3/`: nothing was created or edited under `packages/` or `scripts/`. The copy holds no private fixture, no `.env.local` and no `marathi_samples`; nothing opened a network socket (the autouse guards of batches B and F, the scripted transport) and the only key is the canary. The copy is the working tree of the moment, including the batch F files already moved into `packages/*/tests`.

- red (`red.py3.11.log` to `red.py3.14.log`, `red-reasons.py3.13.txt`): the 4 staged files moved into `packages/*/tests` of a copy that is the tree as it stands, the three directories `packages/cli/tests`, `packages/evalproviders/tests` and `packages/evalcore/tests`, `--tb=line`: **124 failed, 1495 passed, 1 deselected in 73.42s (0:01:13)** on 3.11; 3.12: 124 failed, 1495 passed, 1 deselected in 68.12s (0:01:08); 3.13: 124 failed, 1495 passed, 1 deselected in 69.52s (0:01:09); 3.14: 124 failed, 1495 passed, 1 deselected in 70.05s (0:01:10). Of the 139 new tests, 124 fail and 15 pass by design (section 11); the 1,480 existing tests pass. `red-reasons.py3.13.txt` groups the 124 failures by their first line: about 66 are the parser's `unrecognized arguments` (an option of this batch is not in the parser yet), 10 `cannot import name export_token_count`, 9 no `EnvFileModeWarning`, and the rest are the individual assertions of the adapter fixes (`trust_env`, `Accept-Encoding`, `O_NOFOLLOW`, the directory mode, the regular-file and size rules of the env file, the FIFO that blocks, the repr, `MAX_SARVAM_CHUNKS`).
- oracle (`oracle.py3.11.log` to `oracle.py3.14.log`, `oracle-src.patch`, `spec-change.diff`): a hand-written minimal implementation of this contract (`oracle-src.patch`: 7 files, about 320 added lines; it is ruff-format, ruff-check and mypy clean, and the repository gates `ruff check .`, `ruff format --check .`, `mypy` (321 files), `check_headers.py` and `guard_private_fixtures.py` pass on the tree; the dev owns the real code and may differ wherever the contract does not pin it) with the six spec changes: the same three directories on 3.11, 3.12, 3.13 and 3.14: **1619 passed, 1 deselected, 32 warnings in 72.70s (0:01:12)**; 3.12: 1619 passed, 1 deselected, 32 warnings in 68.20s (0:01:08); 3.13: 1619 passed, 1 deselected, 32 warnings in 69.77s (0:01:09); 3.14: 1619 passed, 1 deselected, 32 warnings in 69.73s (0:01:09) (1,480 existing + 139 new).
- whole workspace with the oracle and the spec changes (`oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`): 3.11: **12177 passed, 1288 deselected, 32 warnings in 569.73s (0:09:29)**; 3.13: 12177 passed, 1288 deselected, 32 warnings in 562.55s (0:09:22); 3.14: 12177 passed, 1288 deselected, 32 warnings in 543.05s (0:09:03). That is the 12,038 of batch F plus the 139 new tests. Without the spec changes and with the batch G source in place, 88 existing tests fail (section 9); with SC1 alone, 7.
- mutants (`mutants.txt`): **132 mutants of the oracle, 131 killed, 1 equivalent (with its reason), 0 survived unexplained**: in the env file rules (cap, exact size, regular file, each mode bit, the message, the reasons), the call log (no-follow, directory mode), the client (`trust_env`, `Accept-Encoding`), `export_token_count`, the parser and the usage checks (each limit and its edge, each character category, the instant, the required combinations), the refusals before the send (consent gate, future consent, `?`, ceiling and its edge, the token cap and its edge), the dry run lines, the order of the steps (preflight, store before the engine, store after the send, the missing-key order), the failure handling after the send (recorder, recovery file name, mode, format, content, exclusive creation, the stdout lines, the exit code, the error line), the repr and the warning forwarding, the replay preflight and the `.gitignore` line. The first run left 5 survivors; each led to a new test (the probe of the store file, the release of the store connection, other warnings not forwarded, the replay preflight directly) or to a corrected mutant. Every mutant ran with a fresh `PYTHONPYCACHEPREFIX`.
- the staged files alone: `ruff check`, `ruff format --check` and `mypy` (repository configuration) pass on the 4 files; no non-ASCII byte in a staged file, in this contract, in a patch or in a log.

Not run: Python 3.15 (non-blocking); GitHub-hosted execution; Windows (POSIX modes, `O_NOFOLLOW` and FIFOs); the `[postgres]` leg (batch G touches no store code); a real send (owner-gated) and so a real TLS handshake, a real proxy and a real compressed reply; a run as root (the permission tests are skipped there). Timing: the three directories take about 70 seconds on one core per Python (the new files about 6 seconds, one of them 10 s on the red tree only, the FIFO that blocks); the whole workspace about 9 minutes with three Pythons running at once. Logs are scrubbed of home, scratch, temporary and session paths and are under 5,000 lines each.
