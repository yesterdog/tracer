# Phase 3 batch I contract: the fixes of the P3-28 re-audit (R1, R2, R3, R5, R6; R4 follows)

Author: qa, 2026-10-26. Source: the decision log entry "PHASE 3 RE-AUDIT OF BATCH G" (the finding text, the evidence and the required behaviour of R1 to R6), ADR 0024 (rules 15 and 16 and the run book), the contracts of batches E and G, and the implemented `tracer-evalcore`, `tracer-evalproviders` and `tracer-cli` at the tree of 2026-10-26 (HEAD 3926c09 plus the dirty working tree). qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow, script or earlier test. Every statement below is pinned by a test named in section 11, unless it says "not pinned". The lead auto-approves the exact names, codes and wording (owner instruction, 2026-10-23); where the contract chose, the rejected option is in section 10.

No schema change. One new optional keyword and one class flag on the Sarvam engine, two new names in `tracer_evalcore.stt` (nothing else there changes), a few new lines and branches in `tracer-cli`. Nothing is added to `tracer_evalschema` or `tracer_evaldb`. The recovery file format stays `tracer.usage_recovery/1` with no new key (section 1.5).

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/` or `scripts/` and nothing under them was edited. The tests are staged at

```text
docs/phase3/evidence/red/batch-i/staging/tests/cli/           -> packages/cli/tests/           (5 files)
docs/phase3/evidence/red/batch-i/staging/tests/evalproviders/ -> packages/evalproviders/tests/ (1 file)
docs/phase3/evidence/red/batch-i/staging/tests/evalcore/      -> packages/evalcore/tests/      (1 file)
```

No `__init__.py`, no `conftest.py` (the autouse no-network guards of batches F and G cover the new modules). The dev's first act in batch I:

```text
git mv docs/phase3/evidence/red/batch-i/staging/tests/cli/*.py packages/cli/tests/
git mv docs/phase3/evidence/red/batch-i/staging/tests/evalproviders/*.py packages/evalproviders/tests/
git mv docs/phase3/evidence/red/batch-i/staging/tests/evalcore/*.py packages/evalcore/tests/
```

There is no spec change to an existing test (section 9). The documents that the new tests pin are listed in section 7.

| File | Holds | Functions | Collected |
|---|---|---|---|
| `cli/cli_batchi_support.py` | helpers, not collected: `run_capture` (also returns an exception that leaves `run`), `sarvam_capture`, `FailingScript`, `StreamingFake`, `PlainEngine`, `FlaggedOffEngine`, call log and recovery readers | - | - |
| `cli/test_cli_batch_i_recovery.py` | R1 and R2 at the level of the command | 24 | 32 |
| `cli/test_cli_batch_i_output.py` | R3 and R6 at the level of the command and the store | 15 | 41 |
| `cli/test_cli_batch_i_instant.py` | R5 (pattern, converter, command line, stored instant) | 10 | 131 |
| `cli/test_cli_batch_i_docs.py` | the documents (R3, R1, R6, R4); reads files only | 6 | 7 |
| `evalproviders/test_sarvam_usage_stream.py` | R1 at the Sarvam engine | 13 | 16 |
| `evalcore/test_stt_usage_streaming.py` | R1 at the engine boundary | 7 | 12 |
| total | | 75 | 239 |

Run: `uv run --locked pytest -q packages/cli/tests/test_cli_batch_i_*.py packages/evalproviders/tests/test_sarvam_usage_stream.py packages/evalcore/tests/test_stt_usage_streaming.py`. The tests that need file modes skip as root (`needs_file_modes`, as in batch G). All text is invented, the only key is the canary of the batch D support file, nothing opens a socket, nothing reads the machine's environment.

## 1. R1: the usage of the chunks answered so far survives any end of the send

### 1.1 The boundary (`tracer_evalcore.stt`)

Two names are added to `__all__` (kept sorted) and nothing else changes; `SttEngine` keeps `transcribe(plan, context)` exactly, so every engine written before batch I stays valid and is called as before:

```python
UsageSink = Callable[[UsageRecord], None]


@runtime_checkable
class UsageStreamingSttEngine(Protocol):
    """An engine that hands out the usage of each answered chunk as it arrives."""

    streams_usage: bool

    def transcribe(
        self, plan: ChunkPlan, context: SttContext, *, on_usage: UsageSink | None = None
    ) -> SttOutcome: ...
```

`isinstance(engine, UsageStreamingSttEngine)` is true only for an object that has the `streams_usage` attribute (a runtime protocol checks names, not values; so the command also reads the value, section 1.3). The module still imports no network package. Pinned: the export, the unchanged `SttEngine` signature, the flag being required, `on_usage` keyword-only with default `None`.

### 1.2 `SarvamStt` (`tracer-evalproviders`)

- Class attribute `streams_usage = True` (a `ClassVar[bool]`).
- `transcribe(self, plan, context, *, on_usage=None)`; `plan` and `context` stay the first two parameters.
- After `send_chunk` returns a chunk's answer (so after the chunk's last attempt, once), the engine builds that chunk's one `UsageRecord` (the same record `map_usage_records` makes for it), appends it to the list it keeps and then calls `on_usage(record)`, before the next chunk's request is built. A retried chunk is handed out once. A chunk that fails (`SttError`) is not handed out.
- The records handed out are the records of the outcome, with the same ids: on success `outcome.usage` is the tuple of the handed-out records (not a second mapping with new ids), on an `SttError` the failed outcome's `usage` is the same tuple. One source of truth, so a recovery file, a stored bundle and the call log reconcile by id.
- Anything else that leaves `send_chunk` (an exception of the transport that is not an `httpx.RequestError`, a `KeyboardInterrupt`, an `OSError` of the call log, a bug) leaves `transcribe` unchanged and unwrapped; the records already handed out stay with the caller. The engine sends no chunk again.
- An exception raised by `on_usage` also leaves `transcribe` unchanged and no further chunk is sent (an observer that cannot keep a record must not let the engine go on paying; section 10, A4).
- A plan without chunks and a language the engine refuses (`language_code_for`, before any request) hand nothing out.
- Without `on_usage` (or `on_usage=None`) the outcome has the same shape as before.

### 1.3 The command (`tracer_cli.cli`)

`_Recorder` keeps, besides `outcome`, the records it was handed. It passes `on_usage=<its own collector>` to `transcribe` only when `getattr(engine, "streams_usage", False) is True`; an engine without the attribute, or with a false value, is called `transcribe(plan, context)` as before and never receives the keyword (`ReplayStt` is unaffected: it makes no usage and has no flag). The recorder's usage is the outcome's `usage` once `transcribe` returned, otherwise the records it was handed; the two are never added (a chunk is not counted twice).

In `_send_and_save`, the `score_recorded_call` call is wrapped in `except BaseException` (it was `except Exception`):

1. if the recorder's usage is not empty, `_write_recovery(failed_step="pipeline", error=<the exception>, usage=<recorder usage>, bundle=None)` is called, and one stdout line follows: `usage recovery: <path>` or, when the file could not be written, `usage recovery: not written` (R2, section 2);
2. the exception is raised again, the same object. A plain exception ends in `run` as before (`error: unexpected failure (<Class>)` on stderr, exit 1). A `KeyboardInterrupt` (and any other `BaseException` that is not an `Exception`) leaves `run`; nothing is written to stderr for it, no exit code is returned, `main()` does not catch it (section 10, A8);
3. with no usage (nothing was answered, or an engine that does not stream failed before it returned) no file is written and nothing is printed on stdout (nothing paid, nothing to recover);
4. the report is written only when the pipeline result exists. After an exception there is no result: no report, no store row, no stored run, and nothing is invented.

Ctrl-C during the alignment is the same path: the engine has returned, the recorder holds its outcome, `score_results` is interrupted, and the file holds the outcome's usage. The engine and the store connection are released as before (`_execute`'s `finally` blocks); the engine is closed once.

### 1.4 The store phase (extension E1, section 10 A3)

`store.store_run(...)` is wrapped as `except Exception` (as before: the failure is kept, the report is still written, exit 1) and, new, `except BaseException`: `_write_recovery(failed_step="store", error=<the exception>, usage=result.bundle.usage_records, bundle=result.bundle)`, the stdout line of 1.3 (`usage recovery: <path>` or `usage recovery: not written`), and the exception is raised again. No report is written for a `KeyboardInterrupt` (the user wants the command to stop; the recovery file is the cheap, fast keep). A Ctrl-C while the report is written needs nothing: the store already holds the usage.

### 1.5 The recovery file (`tracer.usage_recovery/1`), schema additions: none

The format string, the keys, the mode (0600), the exclusive creation (O_EXCL, never overwritten) and the content rules (usage and cost records; class name of the error only; no call text, no key, no path, no message, no reference) are those of batch G section 5. What batch I clarifies, pinned:

- `failed_step` is `"pipeline"` for an end of the send or the alignment and `"store"` for the store phase; no new value.
- `error_class` is `type(error).__name__`, so it may now be `KeyboardInterrupt` or the class of an `OSError`, `ValueError` or any exception the transport raised.
- For `failed_step == "pipeline"` the cost lists are empty (`cost_records`, `cost_summaries`, `pricing_catalogs` are `[]`): no run bundle exists and the cost is derived data (section 10, A2). For `"store"` the cost lists are those of the bundle.
- `usage_records` holds exactly the chunks that were answered: one record per answered chunk, `metadata["tracer.chunk_index"]` the chunk, `provider_call_ref.value` the request id. The answered chunks of the file equal the chunks the call log recorded as answered (an `after` line with status 200): same chunk indexes in the same order, same request ids. A chunk whose reply arrived but could not be logged (the `after` line failed with an `OSError`) is not counted: its reply is not read, and its usage is not guessed (section 10, A1). The `before` line in the call log still shows the attempt.
- With zero answered chunks no recovery file is written.
- Not added: `chunks_answered` / `chunks_planned` keys. They are derivable (the length of `usage_records`, the call log) and the file has no reader in Phase 3 (BL-240); a key without a reader is a promise.

### 1.6 Audio is never sent twice

Pinned on every R1 test over the real engine: after a transport exception, a Ctrl-C or a call log error on chunk k, the transport saw exactly the requests that were made up to and including chunk k (and none at all for the chunk whose `before` line could not be written), the call log has the same lines, and no recovery path asks the engine again (an injected engine is called once).

## 2. R2: a recovery file that cannot be written is said so, and costs nothing

- In the pipeline path, when the usage is not empty and the file could not be created (the name already exists, the directory is not writable, a non-`OSError` of the writer), the stdout line is `usage recovery: not written`. The original exception is the one that ends the command (a plain exception gives `error: unexpected failure (<its class>)` and exit 1, a `KeyboardInterrupt` still leaves `run`): the writer's own failure never replaces it and never leaves `run`.
- `_write_recovery` returns `None` for any `Exception` of its own (it was only `OSError`): the document is built and serialised before the file is created, so no half-written file is left (pinned: the file does not exist after a serialisation failure).
- In the store path a writer that raises a non-`OSError` (pinned with a patched `json` of the `cli` module, and with a patched `_dump`, both a `TypeError`) still writes the report, still prints the summary lines (`store: not written`, `report: <path>`, `usage recovery: not written`) and ends with `error: unexpected failure (<class of the store failure>)`, exit 1.
- With zero usage the pipeline path prints nothing (section 1.3 item 3); the store path keeps printing `usage recovery: not written` for zero usage (batch G, unchanged).

## 3. R3: the mode of `--out`

All of this is at the leaf of the path the user gave. Order: after the output probe, the engine factory, `preflight` and the store open (so after the leaf exists), and before the first send, immediately before the `--embed-audio` warning (so the embed warning, if any, is the second line).

- If the directory has any group or other permission bit (`mode & 0o077 != 0`, also a bare execute bit such as 0701 or 0710), the command writes exactly one line to stderr:

  `warning: the output directory is accessible to group or other users (mode 0755)`

  The mode is `stat.S_IMODE(st_mode) & 0o777` as four octal digits. The line holds the mode only: no path (not the leaf, not a parent), nothing else. The special bits (setgid, sticky) neither warn nor print.
- It never refuses, never changes the mode (no chmod), never changes the exit code: a clean run is still exit 0, a failure after the send is still exit 1 with the warning first and the `error:` line second.
- An existing directory with mode 0700 is silent. A leaf the command creates is 0700 whatever the umask (0000, 0022, 0077; pinned over the injected engine and over the Sarvam engine) and therefore silent.
- The dry run touches no output and writes no warning (pinned with a 0777 directory). A refusal before the store is open (exit 4 for the store file, the report name, the probe) writes no mode warning: the warning is for a run that is about to send, and a second stderr line in an exit-4 refusal would make the single-line refusals of batch G false (section 10, A7).
- Parents that `mkdir(parents=True)` creates follow the umask; only the leaf is forced to 0700 (the call log's `CallLog.open` and `store.open_sqlite_store` both do `mkdir(mode=0o700, parents=True)`; the mode applies to the leaf only). Pinned as truth-in-wording: under umask 022 the parents are 0755, under 027 they are 0750, the leaf 0700. If the parents are ever created 0700 too, change this test and the documents together.
- The documents say "the leaf directory is 0700" and "follow the umask" (section 7); the old sentence "A directory the command creates is readable by you only (mode 0700)" is false for parents and is removed.

## 4. R5: an instant is parsed strictly, the same on every Python

`tracer_cli.cli._INSTANT` (private, pinned by name as the lead asked) becomes strict in the time and the offset; the date stays for the calendar (`fromisoformat`):

```text
[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])
```

- Hour 24 (`T24:00:00Z`, `T24:00:00+05:30`, `T24:00:00.5Z`, `T24:30:00Z`, hour 25 or 99) is refused by the pattern on every version, so by `_instant` (an `argparse.ArgumentTypeError`) and by the command (exit 64, `usage:` block, `--consent-recorded-at` named, nothing created, no factory call). Python 3.14 accepts `T24:00:00Z` and reads it as the start of the next day; 3.11 to 3.13 refuse it. The result-level tests therefore pass on 3.11 to 3.13 even on the current tree and fail on 3.14 (red log); the pattern-level tests fail on all four.
- Out of range in the time or the offset, the pattern refuses too: minute 60, second 60 (a leap second is not accepted; it never was), offset hour 24 or more, offset minute 60 or more. New fact found while writing the tests: `+05:60` and `-05:60` were accepted by the converter on 3.13 (and read as 06:00); the pattern now refuses them (finding R5b, same fix).
- Month 00 or 13, day 00 or 32, Feb 30 are refused by the converter (`fromisoformat`) as before; the pattern leaves the calendar to it (pinned at the converter and the command line, not at the pattern).
- The same converter serves `--retention keep-until:` / `delete-after:` and `--priced-at`: hour 24 is a usage error naming that option too.
- An accepted instant is stored exactly: the UTC instant on the three privacy markers (`consent.recorded_at`, and `retention.retain_until` for `keep-until`) equals the input instant (ten values: Z, offsets from -23:59 to +23:59, fractions, midnight, 23:59:59.999999), aware, offset zero.

## 5. R6: a link in place of `tracer.sqlite`

- `_check_output_location` (called before the engine factory, only for a real run) refuses a link at `<out>/tracer.sqlite` before the existing `lexists` / `is_file` / `access` check: `os.path.islink(store_file)` true (a link to a file, to a directory, dangling) raises `Refusal("output_unavailable (store_file_is_a_link)")`. Exit 4, stderr is exactly `error: output_unavailable (store_file_is_a_link)` and a newline, stdout empty.
- Zero engine calls: the factory is not called (injected: `factory_calls == 0`, `engine.calls == []`; Sarvam: zero requests and no call log, report or recovery file is created in `--out`). The link target is never written: the bytes and the mtime of a real store of an earlier run are unchanged and its directory gets no new file; a target directory stays empty; a dangling target is not created.
- `store.open_sqlite_store` refuses a link too (defence in depth: a link planted between the probe and the open): it raises `OSError` (the message holds the word `link`; recommended `OSError(errno.ELOOP, "the store file is a link")`) before it creates the engine, and the command maps it to exit 4 `output_unavailable (OSError)` as it does today for any exception of the open. Pinned for the three kinds; the target is untouched.
- The dry run probes no output and is unchanged (exit 0 with a link in place; pinned).
- A regular `tracer.sqlite` is still used (a second run into the same directory shares the store; pinned).
- Not covered (section 10, A9): a link at `--out` itself, and links at SQLite's side files (`tracer.sqlite-wal`, `-shm`, `-journal`).

## 6. Messages and codes added

| Where | Text |
|---|---|
| stderr, existing `--out` with group or other bits | `warning: the output directory is accessible to group or other users (mode 0755)` |
| stderr, exit 4 | `error: output_unavailable (store_file_is_a_link)` |
| stdout, pipeline path or store `BaseException` path, file not created | `usage recovery: not written` |
| `OSError` of `store.open_sqlite_store` | message contains `link` |

No new exit code (a Ctrl-C leaves `run` as the exception). `run` returns the same six codes. `EXIT_CODES`, the README exit table and `--help` are unchanged.

## 7. Documents the tests pin (the architect writes them; the wording below is the exact substring)

`test_cli_batch_i_docs.py` reads whitespace-normalised text:

- `docs/concepts/recorded-call-scoring.md`, "Privacy and consent": replace "A directory the command creates is readable by you only (mode 0700), and the store and the call log are created with mode 0600." by a bullet that contains `the leaf directory is 0700`, `follow the umask`, `an existing `--out``, `group or other` and `warning`, and still says the store and the call log are 0600. The oracle line: "When the command creates `--out`, the leaf directory is 0700 and the parent directories it creates follow the umask; an existing `--out` that group or other users can reach earns one warning (its mode only) on stderr and the run goes on. The store and the call log are created with mode 0600."
- `docs/adr/0024-provider-adapters-byok-sarvam-rest.md`: the run book bullet for `--out` ("a directory the command creates is 0700.") becomes a sentence with `the leaf directory is 0700`, `follow the umask`, `warning` and `group or other` (the check is on the run book section only); rule 16 (d) gains `usage recovery: not written`, `KeyboardInterrupt`; somewhere in the ADR `on_usage`, `streams_usage` and `store_file_is_a_link` appear. The sentence "a failed store write, a failed report write or a pipeline failure is exit 1" becomes true for a Ctrl-C only with the added sentence of the oracle patch. The run book line "do NOT press Ctrl-C during the send" (decision log, run book additions) is not in the ADR today; if it is added before this batch, it is removed by it.
- `packages/cli/README.md` exit row 1 keeps "when a chunk was answered, the paid usage is kept in `usage-recovery-<UTC>.json` in `--out`" (the claim of finding R4 is true once R1 and R2 are fixed; the test only guards the row and that it promises no exit code 130). The README needs no other change; a line on the mode warning is optional.

`docs/phase3/api-surface-batch-g.md` section 4 and 5.3 describe the recovery as written "when `transcribe` returned"; batch I supersedes that sentence (this file, section 1). The architect may add a pointer; no test reads it.

## 8. What the tests show on the current tree

Red logs are in section 12. On Python 3.11 to 3.13: 100 of the 239 new tests fail on the current tree and 139 pass; on 3.14, 126 fail. The passing ones are guards on behaviour that must not change (the zero-usage cases, the old engine protocol, the replay, a clean run, the dry runs, a regular store, the leaf mode, the truth-in-wording parents, the README row, the converter and the command line for hour 24 on 3.11 to 3.13). The reasons of the failures are grouped in `red-reasons.py3.13.txt`: a keyword the engine does not take, a name the module does not have, no recovery file, no warning, `store_file_not_writable` instead of `store_file_is_a_link`, an exit 0 that wrote through a link, a pattern that still matches `T24`, a `TypeError` that left `run`.

## 9. Named spec changes to existing tests

None. The whole workspace suite (all packages) was run on the oracle (the minimal implementation of this batch) on 3.11, 3.13 and 3.14 with the new tests added: no existing test fails (section 12). No existing test is edited, skipped or deleted. Checked by reading: the existing tests that pre-create `--out` with the default mode and run to the end do not assert an empty stderr, and the single-line refusal tests (`_single_line_error`) all refuse before the store is open, where the mode warning is not written.

Existing statements made false (documents, not tests): section 7. In code, the `_write_recovery` docstring ("or the file cannot be created") and the `_Recorder` docstring ("keeps its outcome") need the new wording.

## 10. Ambiguities and decisions (the lead auto-approves; each is implemented as recommended in the oracle and pinned as stated)

- A1 (pinned) A reply that arrived but whose `after` call-log line failed with an `OSError` is not counted in the recovery file. The reply is dropped before it is read, so there is no usage record to keep. The alternative (count the request as paid) is a guess and the file would disagree with the call log's answered lines. The `before` line shows the attempt; the lead reconciles with the dashboard (BL-228).
- A2 (pinned) The recovery file written for a failure of the send or the alignment has empty cost lists. Alternative: compute the cost from the usage and the price file in the handler. Rejected: the existing batch G test of a failing cost builder pins `[]` for that step, a second computation inside an exception handler is a second way to fail while keeping the first, and the cost is derivable from the usage and the user's price file.
- A3 (pinned, extension beyond the listed findings) A Ctrl-C while the store is written keeps the usage (`failed_step: "store"`, with the bundle's cost lists), prints the stdout line and raises again, with no report. Rejected: writing the report first (a slow write after the user asked to stop) and doing nothing (the paid usage would be lost, which is what R1 is about). Drop the test `test_r1_a_ctrl_c_while_the_store_is_written_keeps_the_usage_and_is_raised_again` if the lead prefers the narrower scope; nothing else depends on it.
- A4 (pinned) An exception raised by the `on_usage` observer ends `transcribe` with that exception and no chunk after it is sent. Rejected: swallowing it (an observer that cannot keep a record would hide a loss while the engine goes on paying).
- A5 (not pinned) `streams_usage` is read with `is True`, so a truthy non-bool does not opt in. The Protocol cannot check a value at run time; the command does.
- A6 (not pinned) `ReplayStt` gets no `streams_usage` and no `on_usage`; it makes no usage record. Pinned only that it is not a streaming engine.
- A7 (pinned) The mode warning sits after the store is open, not before the engine factory. Rejected: before the factory or in `_prepare`: the single-line exit-4 refusals of batch G with a 0755 `--out` (the garbage store file, the store that cannot be opened) would carry a second stderr line and `_single_line_error` would fail on a default umask. The cost: a refusal that happens earlier does not warn, which is fine because nothing is sent.
- A8 (not pinned) A `KeyboardInterrupt` leaves `run()` and `main()` and ends the process the way Python ends it for an uncaught Ctrl-C (traceback, status 130 on POSIX shells). Alternative: catch it in `main` and return 130 with one line. Recommended: leave it; a traceback of a `KeyboardInterrupt` holds no value (no locals), and the stdout line already says where the usage is. If caught in `main`, `run` must still re-raise (the tests call `run`).
- A9 (not pinned, candidate backlog row, P3) New observation while writing R6: SQLite opens `<store>-wal`, `-shm` and `-journal` next to the store, and `--out` itself may be a link to a directory. Neither is checked by the batch G probe or by R6. Recommendation: a BACKLOG row now; fix with the `O_NOFOLLOW` style check of the side files if the owner wants it before the real send, otherwise accept (the directory is the user's, 0700, outside the repository).
- A10 (pinned) Out-of-range offset minutes (`+05:60`) were accepted before this batch (finding R5b, not in the re-audit list). Fixed with the same pattern.
- A11 (pinned by name) `tracer_cli.cli._INSTANT` and `_instant` are private names; the lead asked for a regex-level pin. If the lead prefers public names, rename in one place and in `test_cli_batch_i_instant.py`.
- A12 (pinned) A leap second (`:60`) stays refused, as RFC 3339 section 5.7 allows but `datetime` cannot hold it.
- A13 (not pinned) The R3 check reads the mode of the leaf at the moment of the first send, after the store open. A directory the command created in this run is 0700 (leaf), so it never warns. A directory whose mode is changed during the run is not seen again.
- A14 (pinned) The `usage recovery: not written` line of the pipeline path is printed only when there is usage to recover; a store path with zero usage keeps printing it (batch G, unchanged). Unifying them would change an existing test (`test_audit_f2_nothing_paid_means_no_recovery_file`).
- A15 (not pinned) The audit table `docs/security/P3-28-audit.md` and `test_p3_audit_items_cite_tests` cover F1 to F16 only. The re-audit findings R1 to R6 are not in a table that test reads; if the lead adds one, the test names of section 11 are the citations.

## 11. Test inventory

`test_cli_batch_i_recovery.py` (R1, R2): a transport exception of five classes on chunk 2 (usage of chunk 1 kept, call log lines, answered equals the call log, no report, no store row, no leak of the message or the key, file mode 0600); the failing chunk 2, 3 and 4 of four chunks; an exception or a Ctrl-C on chunk 1 (nothing paid, no file, empty stdout); a Ctrl-C on chunk 2 (leaves `run`, stderr empty, same object) and on the last of four; the alignment failing with a Ctrl-C and with a `RuntimeError` (both chunks kept, two requests only); the engine closed once after a Ctrl-C; the call log `OSError` before chunk 2 and after chunk 2 was sent; the streaming protocol with an injected engine (callback handed in, nothing counted twice, the records handed out are the file's records, the alignment failing after a streaming engine returned); the old protocol and the false flag (no keyword passed, a plain engine that answered keeps its usage on a Ctrl-C, one that failed has nothing); the replay is not a streaming engine; a Ctrl-C while the store is written; R2: a planted recovery name, a writer that raises `TypeError` in both paths, a Ctrl-C with a failing writer, the store path over the Sarvam engine and over an injected one; a clean run guard.

`test_cli_batch_i_output.py` (R3, R6): ten modes with one warning line each, the Sarvam engine and a replay, 0700 silent, the dry run silent and untouched, warning before the embed warning, warning with exit 1 and the recovery, leaf 0700 under three umasks over two paths, parents follow the umask (two umasks, both engines), the special bits neither warn nor print; R6: three link kinds over an injected engine, over the Sarvam engine, in the dry run and at `open_sqlite_store`; a regular store reused.

`test_cli_batch_i_instant.py` (R5): the pattern refuses 13 hour-24-or-more values and 8 time or offset out-of-range values and accepts ten valid ones; the converter refuses 26 values and returns the input instant (aware, UTC) for ten; the command line refuses hour 24 for `--consent-recorded-at`, `--retention keep-until:` and `delete-after:` and `--priced-at`, the out-of-range values for `--consent-recorded-at`; the stored consent instant and the stored retention instant equal the input for ten values.

`test_cli_batch_i_docs.py`: sections 3, 1, 6 and 4 of the documents (section 7).

`test_sarvam_usage_stream.py`: shape, order (a record before the next request), content and ids, no `on_usage`, a retried chunk once, no chunks, a refused language, an `SttError` on chunk 2, four kinds of exception on chunk 2 (raised unwrapped, same object), a call log `OSError`, an observer that raises.

`test_stt_usage_streaming.py`: exports, unchanged `SttEngine`, old-protocol engine not streaming, flag and keyword make a streaming engine, the flag is required, keyword-only with a `None` default, no network import.

## 12. Evidence (`docs/phase3/evidence/red/batch-i/`)

Counts: new tests red 100 failed / 139 passed on 3.11, 3.12, 3.13 and 126 / 113 on 3.14; oracle 239 passed on all four; whole workspace on the oracle 12614 passed on 3.11, 3.13, 3.14 (no existing test fails); 83 mutants, all killed. The files are `red.py3.11.log` to `red.py3.14.log` (new tests on the current tree), `oracle.py3.11.log` to `oracle.py3.14.log` (new tests on the oracle), `oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log` (every package, with the new tests), `red-reasons.py3.13.txt`, `oracle-src.patch` (the minimal implementation of the code, a reference and not a design), `oracle-docs.patch` (the minimal wording of the pinned sentences), `mutants.txt`.
