<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch G: target safety (P4-31) and the failed caller brain (BL-338)

Status: red-first contract from `qa`. Batch G has two parts. (A) The remaining parts of P4-31: the
kill-switch file, the minimum interval and the lock file, one call per invocation, the hardening of
the allowlist read and the DNS check of a named target (ADR 0026 rules 14, 16, 17 and 18). (B) The
auto-approved fix of BL-338 (owner instruction 2026-10-23, decided 2026-10-28): a failure of the
caller's language model during a call is a new end reason, `brain_failed`, run state `error` in the
words of the lead (see ambiguity A1), exit code 2, typed facts only. Rejected: reusing
`transport_error`; keeping exit 0. Batches 0 to F and J are committed; their names are reused as
they are. The oracle (the minimal implementation the tests were proved against) is
`oracle-src.patch` next to the evidence; it is a reference for the dev, not code to copy blindly.

Open decisions that stay open: O1 (Sarvam terms) and O4 (pre-registered values; the owner has
pre-registered 3 s for `min_call_interval_ms` in the first real runs, a value of the sim profile, no
default in code). No test sends a real request: everything uses fakes, a `MockTransport`, a table
resolver and the virtual clock. No unit test opens a socket.

What already existed and is not written again (found by reading the tree at HEAD 5c8c18a): the
allowlist parser, the normalisation, the file-mode and link checks of `read_allowlist` and the
command-level refusal cases (`test_allowlist_*` unit tests in `test_evalsim_fake_agent_and_allowlist`,
the `target not on the allowlist` and `allowlist mode` cases inside the parametrised
`test_refusals_happen_before_any_key_read_or_connect`), the signal path to `kill_switch`
(`test_a_stop_during_a_line_hangs_up_within_one_frame_and_keeps_the_partial_line`,
`test_tdeg_sim_kill_switch_keeps_partial_recording_and_usage`) and `min_call_interval_ms` as a
required value of the sim profile. None of the five SCOPE names of P4-31 existed as a test name; they
are written once each, at the level of the command, with cases the earlier tests do not have.

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-g/staging/tests/<package>/` (no `__init__.py`); the dev
does not edit them. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). The support modules
   (`evalsim_support_g.py`, `cli_p4g_support.py`) are plain modules found through pytest's rootdir
   import mode, as the earlier support modules are.
2. Apply `spec-change.diff` (named spec changes S-G1 to S-G7, section 10) as exact-string edits. The
   hunks under `packages/*/tests/` are the dev's. The hunks under `docs/` are the proposal of
   section 9: the architect applies the ADR and SCOPE text (the ADR tests and the wiring test read
   it), and may word it differently provided the facts the wiring test pins stay (section 10).
3. Create nothing under `packages/` that has no `pyproject.toml`; all new modules go into the existing
   packages (section 1). No dependency is added: `fcntl`, `ipaddress`, `os` and `json` are the
   standard library; `uv.lock` and the licence policy do not change.
4. Run `uv sync --locked --all-groups && uv run --locked pytest -q`.

No marker is new. The tests are in the default selection on every Python.

## 1. Package ownership

| Package | New or changed modules |
|---|---|
| `tracer-evalcore` (pure) | `simtypes` (`END_REASONS` gains `brain_failed` last), `simoutcome` (schema enum, one cross-field rule), `callstate` (docstring only) |
| `tracer-evalsim` (async) | new `guard` (kill switch, call lock), new `addresses` (DNS check); `allowlist.read_allowlist` (open without following), `errors.REFUSAL_CODES` (two codes), `runner` (`kill_switch` poll, `CallerEnd` and `CallResult` fields), `lines` (the mapping of a provider failure) |
| `tracer-evalproviders` | `simcalllog` (the call log accepts the end reason) |
| `tracer-cli` | `simulate` (step 2 additions, the claim, the poll, the printed line, `SimWorld.target_resolver`), `simsummary.exit_code_of`, `simargs.SIM_EXIT_CODES[2]`, `simreport` (one note) |

`tracer_cli.simulate` stays the only importer of `tracer_evalsim` (guard test). No new dependency.

## 2. Names reused from earlier batches (not redefined)

`Clock`, `SystemClock`, `StopFlag`, `run_call`, `RunnerSettings`, `CallerEnd`, `PreparedLine`,
`CallResult`, `LlmLines`, `PresynthesisedLines`, `ConnectionLabels`, `parse_target_url`,
`TargetRefusedError`, `AllowlistError`, `read_allowlist`, `find_entry`, `normalise_target`,
`SimWorld`, `ConnectRequest`, `Refusal`, `exit_code_of`, `sim_summary_lines`, `render_sim_report`,
`END_REASONS`, `CallMachine`, `build_sim_outcome`, `validate_sim_outcome`, `LlmError` and its
subclasses (`tracer_evalcore.llm`), `SimCallLog`, the harness of `cli_p4e_support`, `cli_p4f_support`
and `evalsim_support_runner`.

## 3. `tracer_evalsim.guard`: the kill-switch file and the call lock (ADR 0026 rules 17 and 18)

Constants (pinned by `test_the_kill_switch_file_name_is_a_plain_constant_of_the_package`):
`KILL_SWITCH_FILE_NAME = "kill-switch"`, `CALL_LOCK_FILE_NAME = "call.lock"`,
`CALL_LOCK_FORMAT = "tracer.call_lock/1"`, `MAX_CALL_LOCK_BYTES = 4096`, `GUARD_CODES` (sorted,
closed: `call_in_progress`, `call_lock_clock_inconsistent`, `call_lock_invalid`,
`kill_switch_file_present`, `min_call_interval_not_elapsed`). `GuardError(code)` is an
`EvalsimError` and a `ValueError`; its message is its code; a code that is not a short machine code is
a `ValueError`.

### 3.1 The kill switch

`kill_switch_present(out_dir) -> bool`. True iff anything is named `kill-switch` in the directory:
a file, an empty file, a directory, a dangling link (`os.lstat`; nothing is opened or followed). A
missing entry or a missing directory is False (`FileNotFoundError`, `NotADirectoryError`). Any
other error of the file system is True (never assume: when the file system cannot say 'absent' the call
must stop). The tool never creates, reads or removes the file; the operator creates it (`touch
<out>/kill-switch`) and removes it. A name with another spelling or a suffix is not the switch.

### 3.2 The call lock

`check_call_interval(out_dir, *, now_utc, min_interval_ms) -> None` (step 2; read-only, creates
nothing, an absent directory or file means no earlier call) and `CallLock.claim(out_dir, *, now_utc,
monotonic_ns, min_interval_ms) -> CallLock` (a context manager with `close()`; the claim at the
connect). `now_utc` must be an aware `datetime`, `min_interval_ms` an `int` that is not a `bool` and
not negative, `monotonic_ns` an `int` from 0 to 2**63-1 (a `ValueError` otherwise; a program error,
not a refusal).

The record is exactly one line (`json.dumps(..., sort_keys=True, separators=(",", ":"))` plus `\n`):
`{"format":"tracer.call_lock/1","monotonic_ns":5000000000,"utc":"2026-10-27T12:00:00.123456Z"}`; the
`utc` value has six digits of microseconds and `Z`. A file that is anything else is refused
`call_lock_invalid` and left as it is: not JSON, not an object, other keys, a missing key, a
duplicate key, another `format`, a `monotonic_ns` that is not an integer (a boolean, a float) or is
negative or over 2**63-1, a `utc` that is not that shape or not a date, two lines, not UTF-8, more
than 4,096 bytes, a mode with a group or other bit, not a regular file (a directory), or a link
(`O_NOFOLLOW`; a dangling link too). An empty file is 'no call recorded' (a crash between creation and
write; the record is written before the connect).

Semantics: with a record `last` and `now`: `now < last` and `min_interval_ms > 0` is
`call_lock_clock_inconsistent` (the interval cannot be known); `now - last < min_interval_ms` is
`min_call_interval_not_elapsed` (exact to the microsecond; exactly the interval is enough); with
`min_interval_ms == 0` neither is raised (the record is still validated). Instants are compared as UTC
whatever the zone of `now_utc`; the monotonic instant is recorded, not compared (ambiguity A6).

`claim` opens `call.lock` with `O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC`, mode 0600 whatever the
umask (`fchmod` when the created mode differs), takes an exclusive non-blocking `flock` (an
open-file-description lock; `lockf` would drop the lock when any descriptor of the process is closed)
and refuses `call_in_progress` if it is held, re-reads and judges the record under the lock (the claim
decides, not only the step 2 check), then replaces the record (truncate, write, `fsync`) and returns
holding the lock. A refusal records nothing. `close()` and leaving the `with` block release the lock.
No function in the module sleeps.

## 4. The runner (`tracer_evalsim.runner`)

### 4.1 The kill-switch poll

`run_call(..., kill_switch: Callable[[], bool] | None = None)`. The runner stays free of the file
system: it is given a poll and the command builds it from `kill_switch_present`. The poll is asked
(a) before the connection, (b) in every `_check` (so before every frame, by the sender's `should_stop`,
both before the sleep to the frame's slot and after it), and (c) while the caller waits, on a tick of at
most one frame (`FRAME_NS`) so that nothing but time wakes the loop. A True poll ends the call with
`kill_switch`, the hang-up of the dialect, the partial turn kept; before the connection it is a call
that was not established. The poll does not count as a signal (`signals_received` stays 0). With no poll
(`None`) behaviour is unchanged. A file that appears at an instant T is seen at the first poll at or
after T, so the hang-up is less than one frame later in virtual time and the frame count is
`ceil(T / 20 ms)` (`test_a_switch_seen_mid_line_stops_before_the_next_frame_within_one_frame`).

### 4.2 The failure facts

`CallerEnd` gains `failure_code: str | None = None` and `failure_status: int | None = None` (the last
two fields). `CallResult` gains `failure_http_status: int | None = None` (last field). For
`brain_failed` the runner sets `CallResult.failure_code` to the code and `failure_http_status` to the
status; for every other end reason `failure_http_status` is `None` and `failure_code` is what it was.

## 5. `tracer_evalsim.addresses`: the DNS check (ADR 0026 rule 16, BL-142)

`check_target_addresses(host, port, *, resolver) -> None`, `system_resolver(host, port) -> list[str]`
(`socket.getaddrinfo`, text addresses), `Resolver = Callable[[str, int], Sequence[str]]`.

* A host that is a literal IP address is never looked up: it is the operator's word (an agent on the
  operator's own network, the sample bots on this machine).
* Any other host is looked up once (`resolver(host, port)`); an `OSError`, a `TimeoutError` or an
  empty answer is `TargetRefusedError(code="target_unresolvable")`.
* Every address of the answer is judged: `localhost` needs every address to be loopback; any other
  name needs every address to be global (`ipaddress` `is_global`, after an IPv4-mapped IPv6 address
  is judged as the IPv4 address it carries and a scope suffix `%...` is dropped). A private,
  loopback, link-local, unspecified, shared (100.64.0.0/10) or otherwise non-global address, or an
  answer that is not an address, is `target_address_refused`.
* `errors.REFUSAL_CODES` gains `target_address_refused` and `target_unresolvable` (the tuple stays
  sorted; no code of the new errors holds a host or an address).

The race between this check and the connection is not claimed closed (the judge's precedent, BL-326):
the connection resolves the name again. A BACKLOG row is recommended (ambiguity A11).

## 6. The allowlist read (`tracer_evalsim.allowlist.read_allowlist`)

The file is opened with `O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK` and judged on the descriptor
(`fstat`): a link is `allowlist_not_a_regular_file` (`ELOOP`); a missing file or any other error of
the open is `allowlist_unreadable`; a directory, a FIFO or a device is `allowlist_not_a_regular_file`
(a FIFO is never read, so nothing blocks); a mode with a group or other bit is
`allowlist_mode_too_open`; a size over `MAX_ALLOWLIST_BYTES` (65,536) is `allowlist_too_large`; at
most `MAX + 1` bytes are read, and a file that reads longer than the cap is `allowlist_too_large`
whatever an earlier `lstat` said; a file that is not UTF-8 is `allowlist_unreadable`. There is no
`lstat` before the open: a path swapped between a check and the open is the race this closes. The codes
are unchanged. (`test_a_link_swapped_in_after_the_check_is_still_not_followed`,
`test_a_mode_widened_after_the_check_is_still_refused`,
`test_a_file_grown_after_the_check_is_refused_by_what_was_read`,
`test_a_fifo_is_refused_without_a_blocking_read`.)

## 7. `tracer simulate`

### 7.1 Step 2, in this order (ADR 0026 rule 14)

For a `--target` run: (a) the allowlist (`allowlist_missing` without `--allowlist`; the target parsed
by `parse_target_url` with `ws`/`wss`; the file read; the target found, else `target_not_on_allowlist`);
(b) the addresses of a named target (`check_target_addresses`, resolver from `SimWorld.target_resolver`
or `system_resolver`); (c) the minimum interval (`check_call_interval` with `created_at` as `now_utc`
and the profile's `min_call_interval_ms`); (d) the kill-switch file (`kill_switch_present`,
`kill_switch_file_present`). For a `--agent fake:SCENARIO` run only (d) applies (ambiguity A5). Every
refusal is exit 4, one `error: <code>` line, nothing on stdout, the call trace ends at `allowlist`, no
key is read (the provider key and the bearer variable of the entry), no engine is built, no connection
is made, nothing is created under `--out` (the directory itself is not created). The line is a code
only: never the path of the allowlist or of a lock, a line of either file, a host, a port, an address
or an instant. `--dry-run` returns before step 2 and ignores all of it.

### 7.2 The target is used as allowlisted

A target that matches its entry is connected and stored in the form the entry wrote (the normalised
text: scheme and host in lower case, the default port written out): `ConnectRequest.url` and the stored
`AgentTarget` carry `entry.target`. (Defect found by the probe of this batch: a target written
`WS://127.0.0.1:8765/ws-client` matched its entry after normalisation, then the raw text reached the
`AgentTarget` and the run ended with exit 1, a `ValidationError`, after the call.)
`test_a_target_written_in_another_case_is_used_as_allowlisted`.

### 7.3 The kill switch in the call

`_execute` gives `run_call` `kill_switch=lambda: kill_switch_present(out_dir)`. A file present at
step 2 is a refusal (exit 4). A file that appears after step 2 and before the connection ends a call
that was never made: `kill_switch`, not established, every result `not_applicable`, exit 5, the usage
already paid kept (as for a signal at that moment). A file that appears during the call hangs up within
one frame (section 4.1): the end reason is `kill_switch`, the run is stored with what was recorded (the
partial recording, the usage of every answered request, the report), the exit code is that of any stored
call (0 when every result is scored or not applicable), as for the first signal. No recovery file is
written for a clean kill; if the store then fails, the usual recovery file is written (ADR 0024 rule
16). The file is not removed by the tool, so it stops the next run in that directory at step 2 until
the operator removes it.

### 7.4 The interval and the claim

For a `--target` run, immediately before `run_call` (step 9), `CallLock.claim` records the instants of
the connect attempt (UTC from the command's clock, monotonic from the runner's clock) and holds the
lock until the call is hung up (it is released in a `finally`, also after a crash). A `GuardError` of
the claim is a `Refusal` (exit 4; the usage already paid is kept by the recovery handler of rule 14).
The record is written before the connection is tried, so a refused connection counts as a call
(`test_a_failed_connect_counts_as_a_call`).

### 7.5 One call per invocation

A target run calls the connector at most once (the runner has no reconnect); the call log holds one
`connect` event per invocation. Concurrency is 1 per output directory (`call_in_progress`).

### 7.6 `SimWorld.target_resolver`

`SimWorld` gains `target_resolver: Resolver | None = None` (a seam of tests only; the default is
`system_resolver`). It is used only by a target run, only for a host that is not a literal address,
once per run.

### 7.7 Files and lines

`--out` gains two names: `kill-switch` (made by the operator) and `call.lock` (target runs, mode 0600,
one line). The summary lines of the batch E contract are unchanged, plus section 8.6. Nothing about
either file is printed, reported or stored in the bundle.

## 8. `brain_failed` (BL-338)

### 8.1 The list

`END_REASONS` (and the schema enum of `tracer.sim_outcome/1`, the list of the call log and every test
that lists them) is the old eleven in the old order and `brain_failed` last: twelve.
`brain.BRAIN_END_REASONS` (what a brain DECIDES) is unchanged: a failure is an exception of the
engine, not a decision.

### 8.2 The call machine

`brain_failed` is requestable in every state of an established call (`connected`, `caller_speaking`,
`awaiting_agent`, `agent_speaking`), not before the connection
(`end_reason_not_allowed_before_connect`), and the first end reason wins.

### 8.3 `tracer.sim_outcome/1`

The enum gains `brain_failed`; the cross-field rules gain one: `end_reason == "brain_failed"` needs
`caller_text_origin == "llm"`, else `SimOutcomeError("brain_failed_without_llm_caller")`; the existing
rules apply unchanged (an established call; no `cap_reached`). The facts of the failure are NOT in the
document (the schema is closed and the call log already holds every attempt's status): ambiguity A3.

### 8.4 The line provider (`tracer_evalsim.lines`)

`LlmLines.next` catches `LlmCapReachedError` first (`spend_cap`, `llm_tokens`, unchanged) and then any
other `LlmError`: `CallerEnd("brain_failed", requests=<requests made>, failure_code=<code>,
failure_status=<the error's HTTP status or None>)`. `brain_failure_code(error)` maps by class, in this
order: `LlmAuthError` `llm_auth`; `LlmRequestRejected` `llm_rejected`; `LlmRateLimited`
`llm_rate_limited`; `LlmServerError` `llm_server_error`; `LlmTransportError` (a connection error, a
timeout) `llm_transport`; `LlmResponseInvalid` `llm_response_invalid`; any other `LlmError` (including
a configuration or key error) `llm_error`. `BRAIN_FAILURE_CODES` is that closed sorted tuple. An error
that is not an `LlmError` (a defect) is not caught: it reaches the command (exit 1). A failure of the
caller's text-to-speech in the call stays `transport_error` (ambiguity A4).

### 8.5 The runner

A `CallerEnd` with end reason `brain_failed` ends the call with that reason exactly like the other
brain ends: counts (`brain_requests`, `llm_requests`), the hang-up in the dialect, the turns sent so
far kept (a failure after a turn keeps the turn, the agent's frames and the usage), an established
call (a failure at the first request is an established call of zero caller turns that ends at once).

### 8.6 The command

* Exit code 2 (`exit_code_of`: 5 not established, 3 speech-to-text failed, then 2 for `brain_failed` or
  an `error` result, else 0; the precedence 1, 5, 3, 2, 0 stands). In the common cases no result is an
  error, so the end reason alone makes the code 2.
* One stdout line, after `ingest warnings:` and before `bundle:`:
  `brain failure: <code>` or `brain failure: <code> (HTTP <status>)` (status only when the error has
  one: `llm_response_invalid` has 200). Examples: `llm_auth (HTTP 401)`, `llm_server_error (HTTP 500)`,
  `llm_transport`. Nothing else of the failure is printed; stderr stays empty; the provider's message, a
  body, a header, the key and the authorisation reference reach no stream and no file.
* `SIM_EXIT_CODES[2]` is `a result was not scored, or the caller's language model failed` (also in
  `tracer simulate --help`); `cli.EXIT_CODES` (the Phase 3 table) is unchanged.
* The summary line `end reason: brain_failed`, `result: call established`, `caller turns: 0 started, 0
  completed` (or the real numbers) and the usage lines are the usual ones; the usage of a failed
  request is none.

### 8.7 The report

When the end reason is `brain_failed` the header gets one paragraph, `id="brain-failed-note"`, class
`warning`, with exactly: `The simulated caller's language model failed during the call, so the caller
stopped speaking before it had finished. This is a failure of the test caller, not a result about the
agent under test.` (one line; no word of the claim deny-list). No other report has the element.
`#end-reason` and `data-end-reason` carry `brain_failed` as for any reason.

### 8.8 T-DEG

Two more cases of the matrix of P4-29, in their own file (`test_tdeg_sim_brain_failed`), because the
ten cases and the count 19 of the batch F wiring test are not edited: `brain_failed_first` (the
established call of zero caller turns: no Turn has a start and an end, so `turn_timing` is not an
available input, the four timing results are `not_applicable` `missing_input` `turn_timing`, the
agreement `missing_input` `transcript` and `agent_transcript`, no usage, exit 2) and
`brain_failed_later` (one answered turn: timing 400, 300, 0, 1 scored; usage `sim_caller_llm`,
`sim_caller_tts`, `stt` in the order answered; every result scored or not applicable, exit 2). The same
seven result keys, the same metric definitions, the same stored documents valid against the same
schemas, a bundle that passes `check_run_bundle`, no verdict.

### 8.9 Store and call log

The stored run reads back equal and byte for byte (`store_run`, `load_run`, a second `store_run` inserts
nothing); the call log accepts `brain_failed` as the end reason of its last line, with the counts. Both
backends: the same rows (the Postgres leg of batch E reads the same bundle; not run here).

## 9. The text the architect applies (proposed ADR and SCOPE amendments)

The exact strings are the `docs/` hunks of `spec-change.diff`; this is their meaning, in the order of
the ADRs. Counts of rules and invariants do not change (`SPECS` stays 24 and 10 for ADR 0026, 17 and 10
for ADR 0027), so no label moves.

* **ADR 0026 rule 9** names twelve end reasons, `brain_failed` last, and defines it (an established
  call whose caller brain's provider failed during the call: an HTTP 4xx other than a cap, a 429 or
  a 5xx after the bounded attempts, a transport error, a timeout, a reply not of the documented shape;
  needs an LLM caller; stored with what was recorded; exit 2; typed facts only; a token cap is still
  `spend_cap`). Rejected: `transport_error` for it, exit 0. Tests added to its list.
* **Rule 16** gains: the addresses are checked at step 2 with the system resolver (`target_address_refused`,
  `target_unresolvable`; the race is not claimed closed, BL-326); a target on the list is connected and
  stored as the entry wrote it; the file is opened without following a link and judged on the
  descriptor. Tests added: the entry-fields test and the DNS test.
* **Rule 17** names the file `kill-switch` (the constant), says what counts (any entry, a link not
  followed, a file-system answer other than 'absent' counts), the three checks (step 2: exit 4; before
  the connection: exit 5; before every frame and on a tick while waiting: within one frame), the exit
  code of a stored killed call, and that the tool never removes the file.
* **Rule 18** names the lock file `call.lock` and its format, says that it applies to `--target` runs,
  that the record is written before the connect, the codes, and the exclusive `flock` until the call is
  hung up.
* **Rule 19** extends exit 2: `a result was not scored (status=error) or the caller's language model
  failed (the end reason brain_failed)`.
* **Invariant I3** says twelve end reasons.
* **ADR 0027**: the schema block's `end_reason` enum gains `brain_failed`; the cross-field rules gain
  `brain_failed` implies `caller_text_origin` `llm` and `call_established=true`; rule 16 (T-DEG) gains the
  failed brain and its two cases. **ADR 0029 rule 6** says twelve. **SCOPE** P4-04 lists the twelfth
  reason, P4-29 and P4-31 carry a dated update that points here. **BACKLOG**: BL-338 moves to
  `in-progress` with this batch; the rows recommended by section 13 are opened by the lead.

## 10. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only)

* **S-G1** (`evalcore_phase4_adr_support.py`): the four P4-31 names leave `_P0026`, one leaves
  `_P0028` and two leave `_P0029` because the tests now exist; the three comment headers that became
  empty go with them. `PENDING_OUTSIDE_RULES` and every other pending name are untouched.
* **S-G2** (the end-reason lists, from 11 to 12): `evalcore_batcha_support.py` (`END_REASONS`),
  `test_sim_outcome_doc.py` (the tuple, the docstring, and the body of
  `test_every_end_reason_is_a_valid_record`, which builds `brain_failed` with an LLM caller because a
  scripted caller cannot have it), `test_adr0027_documents_match_json_schemas.py` (the list, the problem
  text, and the mutation `_drop_end_reason`, which now drops the last member), `test_sim_callstate.py`
  (`assert len(seen) == 12` and the docstring), `test_sarvam_sim_calllog.py` (the list). Nothing is
  weakened: every list gains a member and every count moves with it.
* **S-G3** (ADR 0026): rule 9, rule 16 (a sentence and two names), rule 17 (the file, the three checks,
  one test name), rule 18 (the lock, two test names), rule 19 (exit 2 and a test name), invariant I3.
* **S-G4** (ADR 0027): the `end_reason` enum of the schema block, one cross-field bullet, rule 16.
* **S-G5** (ADR 0029): rule 6, `eleven` becomes `twelve`.
* **S-G6** (`docs/phase4/SCOPE.md`): the P4-04 list, a dated update on P4-29 and on P4-31 (no test
  name is added to a SCOPE row: `test_batch_f_scope_rows_name_nineteen_tests` pins the 19 names of the
  rows P4-26 to P4-30).
* **S-G7** (`test_tdeg_sim.py`, `test_tdeg_sim_setup_failed_all_not_applicable_exit_5`): the listing of
  the output directory of a target run whose connection was refused gains `call.lock` (the record is
  written before the connect, section 7.4, so a refused connection counts as a call). Enabling: one
  expected name is added, nothing is removed.

What the wiring test (`test_phase4_batch_g_wiring`) pins after the changes: the twelve names in the four
test files, in order; the twelve in rule 9, in the order of `simtypes`; ``kill-switch`` in rule 17 and
``call.lock`` in rule 18 (read from the constants); `brain_failed` in rule 19; `twelve end reasons` in
ADR 0026 and ADR 0029; `"spend_cap", "brain_failed"]` in the ADR 0027 block; the P4-04 row.

## 11. Test inventory (exact SCOPE names)

P4-31, each defined once: `test_target_not_on_allowlist_refused_before_any_key_or_socket`,
`test_allowlist_mode_and_link_rules`, `test_allowlist_entry_fields_unknown_or_repeated_refused`
(`test_cli_simulate_target_safety.py`), `test_one_call_per_invocation_and_min_interval`
(`test_cli_simulate_interval.py`), `test_kill_switch_file_hangs_up_within_one_frame`
(`test_cli_simulate_kill_switch.py`). The pending names they clear (seven entries over ADRs 0026, 0028
and 0029) are listed in section 10 (S-G1).

Added by qa (not in the SCOPE table; each has a reason in its docstring): the runner and guard unit
tests of the kill switch, the lock, the addresses and the allowlist open
(`test_evalsim_kill_switch`, `test_evalsim_call_lock`, `test_evalsim_target_addresses`,
`test_evalsim_allowlist_open`), the DNS and normalisation tests of the command, and the BL-338 set
(`test_sim_brain_failed_records`, `test_evalsim_brain_failed`, `test_cli_simulate_brain_failed`,
`test_tdeg_sim_brain_failed`), plus the wiring test: 317 test items in 12 files and 2 support
modules. The counts are in `counts.txt`.

## 12. Evidence (under `docs/phase4/evidence/red/batch-g/`)

`red.py3.*.txt` (the tree at HEAD 5c8c18a plus the staged tests plus the spec change, no oracle; 297
failed, 289 passed on each of 3.11 to 3.14),
`red-reasons.py3.13.txt`, `red-tests-that-pass-on-the-current-tree.txt`,
`red-without-spec-change.py3.13.txt`, `oracle.py3.*.txt` (the new files and the changed shared tests),
`full.py3.*.txt` (the whole workspace with the oracle: 15573 passed, 1 skipped, on each of 3.11 to
3.14), `counts.txt`, `mutants.txt` (106 of 109 killed; the 3 survivors are equivalent),
`oracle-src.patch` (14 source files), `spec-change.diff` (12 files). Not run: Postgres parity (no Docker), Python 3.15, the
lowest-direct dependency leg, hosted CI, any real provider.

## 13. Ambiguities, with recommendations (the lead auto-approves)

A1. The brief says `brain_failed` is run state `error`; the 0.0.1 `Run` has no state or status field,
    only the end reason in `tracer.sim_outcome/1`. Recommendation: no new field; the end reason plus
    exit 2 is the 'error' state, and the report says so. Rejected: a Run status (a schema change for
    one reason).
A2. Where the twelfth reason goes. Recommendation: last (the first eleven keep their place, three
    pinned lists change by one append).
A3. Where the typed facts live. Recommendation: stdout only (`brain failure:` line) and the call log's
    per-attempt lines; not in `tracer.sim_outcome/1`, which is closed. Cost: a reader of the store alone
    sees `brain_failed` but not which class. Rejected: a `brain_failure` member of the outcome (an ADR
    0027 schema change).
A4. The same class of defect remains for the caller's TEXT-TO-SPEECH failing in the call (`LlmLines`
    returns `transport_error`, exit 0, no turn), and for a failed in-call transcription (exit 3, already
    non-zero). Recommendation: open a P1 BACKLOG row now; decide at the gate whether TTS failure maps to
    `brain_failed` (the caller could not speak) or a thirteenth reason. Not changed here: the brief
    names the LLM only.
A5. The lock applies to `--target` runs only. Recommendation: keep (a fake agent dials nothing).
    The kill switch applies to all runs.
A6. The interval is compared in UTC; the monotonic instant is recorded but not compared (it is not
    comparable across a reboot). Recommendation: keep; the ADR says 'records the monotonic and UTC
    instants'.
A7. The record is written before the connect, so a refused connection counts as a call. Recommendation:
    keep.
A8. The claim is taken immediately before `run_call`, so a kill switch that appears between step 2 and
    the connection leaves a lock although no connect happened. Recommendation: accept (a cautious
    over-count); the tests do not pin it.
A9. A stop before the connection is exit 4 when it is the file at step 2 (a refusal) and exit 5 when it is
    a signal or a file that appears later (a call that never happened). Recommendation: keep; two
    different facts.
A10. The names `kill-switch` and `call.lock`. Recommendation: keep; they are constants a run book can
    quote.
A11. The DNS check is at step 2, the connection resolves again. Recommendation: BACKLOG row for the
    race (the judge's BL-326 precedent); pin the address into the connection in a later batch.
A12. A target is used as allowlisted (normalised). Recommendation: keep (the defect of section 7.2).
A13. The lock is held until the hang-up, not for the whole invocation (the post-call speech-to-text and
    the store write are not a call). Recommendation: keep.
A14. A killed call exits 0, as the first signal does. Recommendation: keep (a stored, scored call).
A15. `flock` is POSIX. Recommendation: keep; the code already uses `O_NOFOLLOW`; a Windows port is a
    BACKLOG row if one is wanted.
A16. With `min_call_interval_ms` 0 the lock is still validated and written, but a clock that went
    back is not an error. Recommendation: keep.
A17. `127.0.0.2` is a loopback address but not one of the three loopback hosts of rule 16, so plain
    `ws://` to it is `insecure_target`. Recommendation: keep (the ADR's list is the rule).

## 14. Contradictions and findings

C1. ADR 0026 rule 19 defines exit 2 as `a result was not scored`; BL-338 needs a call that scored
    everything to exit 2. Amended (section 9).
C2. ADR 0026 rule 9, I3, ADR 0027's enum, ADR 0029 rule 6 and SCOPE P4-04 fix eleven reasons. Amended.
C3. ADR 0026 rule 16 requires every address of a named target to be checked before the connection;
    nothing in the tree at HEAD 5c8c18a resolves a target host, so DNS rebinding was unguarded in any
    real target run. The oracle adds it (section 5). Severity for the audit agent: P1 before the first
    real run against a named host.
C4. ADR 0026 rule 16 says the allowlist file is not followed through a link; `read_allowlist` made a
    `lstat` and then an `open` that follows links (a swap in between defeats it) and read at most the cap
    without refusing a longer file. Fixed in the oracle (section 6). P2.
C5. Defect (section 7.2): a target in another case matches its entry and then ends the run with exit 1
    after the call. P2 (a paid call is lost to a crash; the usage is kept by the recovery file).
C6. ADR 0026 rules 17 and 18 and the Q17 text of SCOPE are not implemented at HEAD 5c8c18a: no file
    check exists, `min_call_interval_ms` is validated and stored but never enforced, so the 'automated
    dialler' control of rule 18 does not exist. Implemented in the oracle.
C7. The runner's docstring says it reads no file; rule 17 needs a file check per frame. Resolved by an
    injected poll (section 4.1), so the pure-runner property stands.
C8. SCOPE P4-31 says 'hours to be re-estimated by the reviewer'; the lead's brief adds DNS, the open race
    and the URL form, which are the reviewer's to size.
C9. The guard script (`scripts/guard_private_fixtures.py`) has no rule for `call.lock` or `kill-switch`:
    they hold instants and nothing else (no personal data), so none is added. If the owner wants them
    refused when staged, that is a one-line pattern and a test in `test_guard_sim_outputs_and_allowlist`.
