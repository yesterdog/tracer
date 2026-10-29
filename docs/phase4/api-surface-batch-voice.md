<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch voice: the failed caller voice (BL-369, P4-32 audit finding N-1)

Status: red-first contract from `qa`. One defect, the last open P1 of Phase 4. A failure of the
caller's text-to-speech (Sarvam speech synthesis) in the middle of a call still ends as the end
reason `transport_error` with exit 0: `LlmLines` maps every `TtsError` during the call to
`CallerEnd("transport_error")`. It is the same defect class as BL-338, which batch G fixed for the
caller's language model with the end reason `brain_failed` (exit 2). A failed caller must never look
like a successful test run, and `transport_error` says the connection to the agent failed, which
hides who failed.

Auto-approved design (owner instruction 2026-10-23, decided 2026-10-28): a NEW end reason
`voice_failed` (the caller's voice synthesis failed after the call was established), run state
`error` in the words of the lead (see ambiguity A1), exit code 2, symmetric to `brain_failed`: the
list goes from twelve to thirteen end reasons, `voice_failed` last. Rejected: generalising
`brain_failed` to `caller_failed` (it would rename a shipped, documented end reason and break the
tests, the report note, the printed line and the call log that name it, and the word `brain` would
then mean two engines); keeping `transport_error` (hides who failed; exit 0 reads as success).
Trade-off: a thirteenth reason to document and one more row in every list that pins the reasons.

Batches 0 to G2 and J are committed (HEAD 755f714); their names are reused as they are. The oracle
(the minimal implementation the tests were proved against) is `oracle-src.patch` next to the
evidence; it is a reference for the dev, not code to copy blindly. No test sends a real request:
everything uses fakes, a `MockTransport` and the virtual clock. No unit test opens a socket.

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-voice/staging/tests/<package>/` (no `__init__.py`);
the dev does not edit them. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). `cli_p4v_support.py` is a plain
   module found through pytest's rootdir import mode, as the earlier support modules are.
2. Apply `spec-change.diff` (named spec changes S-V1 to S-V7, section 9) as exact-string edits. The
   hunks under `packages/*/tests/` are the dev's. The hunks under `docs/` are the proposal of
   section 8: the architect applies the ADR and SCOPE text (the ADR tests and the wiring test read
   it), and may word it differently provided the facts the wiring test pins stay (section 9).
3. Create nothing under `packages/` that has no `pyproject.toml`; all changes go into existing
   modules (section 1). No dependency is added; `uv.lock` and the licence policy do not change.
4. Update the three documents that state the meaning of exit code 2 (section 13, finding F2).
5. Run `uv sync --locked --all-groups && uv run --locked pytest -q`.

No marker is new. The tests are in the default selection on every Python.

## 1. Package ownership

| Package | Changed modules |
|---|---|
| `tracer-evalcore` (pure) | `simtypes` (`END_REASONS` gains `voice_failed` last), `simoutcome` (schema enum, one cross-field rule) |
| `tracer-evalsim` (async) | `lines` (the mapping of a speech-synthesis failure: `VOICE_FAILURE_CODES`, `voice_failure_code`, the two failure paths of `LlmLines.next`); `runner` is unchanged (it ends a call with whatever reason a `CallerEnd` carries and `callstate` accepts) |
| `tracer-evalproviders` | `simcalllog` (the call log accepts the end reason) |
| `tracer-cli` | `simulate` (the printed line), `simsummary.exit_code_of`, `simargs.SIM_EXIT_CODES[2]`, `simreport` (one note), `README.md` (the exit code table) |

`tracer_cli.simulate` stays the only importer of `tracer_evalsim`. No new dependency, no new module,
no new CLI option, no new file in `--out`.

## 2. The thirteenth end reason in the pure records

`simtypes.END_REASONS` (and the schema enum of `tracer.sim_outcome/1`, the closed list of the call
log and every test that lists them) is the old twelve in the old order and `voice_failed` last:
thirteen. `brain.BRAIN_END_REASONS` (what a brain DECIDES) is unchanged: a failure of the voice is an
exception or a failed outcome of the engine, not a decision.

* The call machine: `voice_failed` is requestable in every state of an established call
  (`connected`, `caller_speaking`, `awaiting_agent`, `agent_speaking`), not before the connection
  (`end_reason_not_allowed_before_connect`), and the first end reason wins in both directions with
  `brain_failed` (a call has one failed caller engine at most: the brain is asked first and the
  voice only after it answered). `callstate` needs no change beyond the list it imports.
* `tracer.sim_outcome/1`: the enum gains `voice_failed`; the cross-field rules gain one:
  `end_reason == "voice_failed"` needs `caller_text_origin == "llm"`, else
  `SimOutcomeError("voice_failed_without_llm_caller")` (section 6 says why); the existing rules apply
  unchanged (an established call; no `cap_reached`). The facts of the failure are NOT in the document
  (the schema is closed; same reading as BL-373 for `brain_failed`): ambiguity A3.

Tests: `test_sim_voice_failed_records`.

## 3. The line provider (`tracer_evalsim.lines`)

`LlmLines.next` asks the brain, then synthesises the line through the injected `synthesise(text,
index) -> TtsOutcome`. An engine can fail in two ways and both are covered: it returns an outcome
whose `failure` is a `TtsError`, or it raises one (a configuration error always raises). Both ways
give the same `CallerEnd`:

* `TtsCapReachedError` (returned or raised): `CallerEnd("spend_cap", cap_reached="tts_chars",
  requests=..., invalid_outputs=..., regenerations=...)`, as the returned one already did. The raised
  one used to be `transport_error`; it is a cap and not a failure, so it is now `spend_cap` too.
* any other `TtsError` (returned or raised): `CallerEnd("voice_failed", requests=<brain requests
  made in this call of next>, invalid_outputs=<what the brain reported>, regenerations=<same>,
  failure_code=<code>, failure_status=<the error's HTTP status or None>)`. The raised path used to
  drop `invalid_outputs` and `regenerations`; both paths now keep them, because the brain's work
  happened and is counted.
* an error that is not a `TtsError` (a defect) is not caught: it reaches the command (exit 1).
* a brain that ends the call (`decision.line is None`) never reaches the voice, and a failed brain
  is still `brain_failed` (the brain is asked first, so the two reasons never mix).

### 3.1 The exact class to key table

`voice_failure_code(error)` maps by class, in this order; `VOICE_FAILURE_CODES` is the closed sorted
tuple of the seven keys, mirrored from `brain_failure_code` and the error taxonomy of
`tracer_evalcore.tts` (a subclass takes the key of its parent in the table):

| Error class | Key | HTTP status kept |
|---|---|---|
| `TtsAuthError` | `tts_auth` | yes (401, 403) |
| `TtsRequestRejected` | `tts_rejected` | yes (any other 4xx but 429) |
| `TtsRateLimited` | `tts_rate_limited` | yes (429) |
| `TtsServerError` | `tts_server_error` | yes (5xx) |
| `TtsTransportError` | `tts_transport` | no (a connection error or a timeout has none) |
| `TtsResponseInvalid` | `tts_response_invalid` | 200 when the reply was 200, else none (a cache entry that is not valid has none) |
| `TtsError` | `tts_error` | none unless the error has one |

`TtsError` stands for the base class and for every other subclass, the configuration errors
`TtsKeyMissingError`, `TtsLanguageNotSupportedError` and `TtsTextTooLongError` included.

`TtsCapReachedError` is not in the table: it is `spend_cap` (above). The keys are exactly the
`BRAIN_FAILURE_CODES` pattern with the prefix `tts_` in place of `llm_`; no key is shared between the
two tuples. The wiring test reads this table and runs the code against it
(`test_the_class_to_key_table_of_the_contract_is_the_one_the_code_implements`).

`CallerEnd` and `CallResult` already carry `failure_code` and `failure_status` (batch G section 4.2);
nothing is added.

Tests: `test_evalsim_voice_failed` (15 error cases times two ways, the cap, later lines, defects,
order against the brain, the reachability of section 6).

## 4. The runner

A `CallerEnd` with end reason `voice_failed` ends the call with that reason exactly like
`brain_failed`: counts (`brain_requests`, `llm_requests`: the brain request that preceded the failed
synthesis is counted), the hang-up in the dialect (a `stop` event for Twilio, a close 1000 for
protobuf), the turns sent so far kept (a failure after a turn keeps the turn, the agent's frames and
the usage), an established call (a failure at the first line is an established call of zero caller
turns that ends at once). `CallResult.failure_code` and `failure_http_status` carry the facts; for
every other end reason `failure_http_status` is `None`. The runner needs no change (the test proves
it against the unchanged runner with the changed `simtypes`).

## 5. The command

* Exit code 2: `exit_code_of` returns 2 for `brain_failed` or `voice_failed` (the precedence 1, 5, 3,
  2, 0 stands: a failed speech-to-text of the agent channel, 3, still outranks it). In the common
  cases no result is an error, so the end reason alone makes the code 2.
* One stdout line, after `ingest warnings:` and before `bundle:`:
  `voice failure: <code>` or `voice failure: <code> (HTTP <status>)`, the status only when the error
  has one. Examples: `tts_auth (HTTP 401)`, `tts_server_error (HTTP 500)`, `tts_transport`,
  `tts_response_invalid (HTTP 200)` (the same rule as `brain failure:`, ambiguity A2). Nothing else
  of the failure is printed; stderr stays empty; the provider's message, a body, a header, the key and
  the authorisation reference reach no stream and no file.
* `SIM_EXIT_CODES[2]` is `a result was not scored, or the caller's language model or voice failed`
  (also in `tracer simulate --help` and in the README table, which a test compares); `cli.EXIT_CODES`
  (the Phase 3 table) is unchanged.
* The summary line `end reason: voice_failed`, `result: call established`, `caller turns: <n>
  started, <m> completed`, and the usage lines are the usual ones: `tts usage: none` for a failed
  request (a failed synthesis has no usage), and the brain request that was answered before the
  failure stays in `llm usage` (it was paid).
* The usage of every request answered before the failure is in the stored run, in the order the
  requests were answered; no recovery file is written (the run was stored whole); the recording and
  the report exist; the TTS cache holds only good replies (a failed reply is never cached).
* The call log ends with `{"event": "end", "end_reason": "voice_failed", counts...}`.

Tests: `test_cli_simulate_voice_failed` (12 failure kinds at the first line and after a turn, the
line between `ingest warnings:` and `bundle:`, the canaries on every channel, the exit code table,
the precedence, the regression guards).

## 6. Reachability: which callers can lose their voice

Decided from the code, not assumed. The command builds `LlmLines` only for `--caller sarvam` (the LLM
caller). For the scripted and the replayed caller it synthesises every line BEFORE the call (ADR 0026
rule 14 step 7, `PresynthesisedLines`: a provider that takes no synthesis function and makes no
request in the call); a failure there is the existing refusal before any connection (exit 1, one line
on stderr naming the class and the closed reason, the usage kept in the recovery file; batch E
section 10.2 and `test_cli_simulate_tts_failure`). So `voice_failed` is reachable only with an LLM
caller, and the schema says so: `voice_failed_without_llm_caller` (the twin of
`brain_failed_without_llm_caller`). The runner could call a synthesis mid-call for a scripted caller
only if a provider with a synthesis function were built for it; none is, and this batch does not invent
one. Tests pin the actual reachability: the signature of `PresynthesisedLines` takes no synthesis
function (`test_only_the_llm_line_provider_can_lose_its_voice_during_a_call`), and a scripted run
whose synthesis fails at its second line is exit 1 with no `voice failure:` line and no stored run
(`test_a_scripted_caller_cannot_lose_its_voice_in_the_call`). If a later batch pre-synthesises lazily
(on demand, during the call), this section, the cross-field rule and those two tests change
together; that is a named spec change then.

## 7. The report, T-DEG, the store

* The report: when the end reason is `voice_failed` the header gets one paragraph,
  `id="voice-failed-note"`, class `warning`, with exactly: `The simulated caller's voice synthesis
  failed during the call, so the caller stopped speaking before it had finished. This is a failure of
  the test caller, not a result about the agent under test.` (one line; no word of the claim
  deny-list). No other report has the element, and a `voice_failed` report has no `brain-failed-note`.
  `#end-reason` and `data-end-reason` carry `voice_failed` as for any reason.
* T-DEG (ADR 0027 rule 16): two more cases of the matrix, in their own file
  (`test_tdeg_sim_voice_failed`), because the ten cases, the count 19 of the batch F wiring test and
  the two cases of batch G are not edited: `voice_failed_first` (an established call of zero caller
  turns, the brain's request paid: usage `sim_caller_llm`; no Turn has a start and an end, so
  `turn_timing` is not an available input, the four timing results are `not_applicable`
  `missing_input` `turn_timing`, the agreement `missing_input` `transcript` and `agent_transcript`,
  exit 2) and `voice_failed_later` (one answered turn: timing 400, 300, 0, 1 scored; usage
  `sim_caller_llm`, `sim_caller_tts`, `stt`, `sim_caller_llm` in the order answered; every result
  scored or not applicable, exit 2). The same seven result keys, the same metric definitions, the
  same stored documents valid against the same schemas, a bundle that passes `check_run_bundle`, no
  verdict, never 0 or false for a missing input.
* The store: the stored run reads back equal and byte for byte (`store_run`, `load_run`, a second
  `store_run` inserts nothing); the call log accepts `voice_failed` as the end reason of its last
  line, with the counts. Both backends: the same rows (the Postgres leg reads the same bundle; not
  run here, no Docker).

## 8. The text the architect applies (proposed ADR and SCOPE amendments)

The exact strings are the `docs/` hunks of `spec-change.diff`; this is their meaning, in the order of
the ADRs. Counts of rules and invariants do not change (`SPECS` stays 24 and 10 for ADR 0026, 17 and
10 for ADR 0027), so no label moves. The ADR files in `docs/adr` are NOT edited by this batch.

* ADR 0026 rule 9 names thirteen end reasons, `voice_failed` last, and defines it (an established
  call whose caller's text-to-speech provider failed while the caller was speaking: a 4xx other than a
  cap, a 429 or a 5xx after the bounded attempts, a transport error, a timeout, a reply that is not
  audio, or a configuration error; needs an LLM caller because the scripted and the replayed caller
  speak audio synthesised before the call; stored with what was recorded, the brain's usage
  included; exit 2; typed facts only with the seven keys of section 3.1; a character cap is still
  `spend_cap`). Rejected: `transport_error` for it, exit 0, a caller-wide `caller_failed`. The
  trade-off sentence says a thirteenth reason. Three tests are added to its list.
* ADR 0026 rule 19: exit 2 is `a result was not scored (status=error) or the caller's language model
  or voice failed (the end reason brain_failed or voice_failed)`; one more test name (the exit-code
  table with the voice). Invariant I3 says thirteen end reasons. Rules 16, 17 and 18 do not mention an
  end reason list and are unchanged.
* ADR 0027: the schema block's `end_reason` enum gains `voice_failed`; the cross-field rules gain
  `voice_failed` implies `caller_text_origin` `llm` and `call_established=true`; rule 16 (T-DEG)
  gains the failed voice and its two case tests.
* ADR 0029 rule 6 says thirteen end reasons (the hang-up on every end reason).
* SCOPE: the P4-04 list gains `voice_failed` (added 2026-10-28, BL-369); P4-29 carries a dated update
  that points to section 7. No test name is added to a SCOPE row.
* ADR README: proposed bullet for the next revision (the architect numbers it): `ADR 0026 (BL-369):
  rule 9 names thirteen end reasons, voice_failed last, and defines it; rule 19 extends exit 2 to
  voice_failed; invariant I3 says thirteen. ADR 0027: the end_reason enum gains voice_failed, one
  cross-field rule (voice_failed implies an LLM caller and an established call), rule 16 (T-DEG) names
  the failed voice. ADR 0029 rule 6 says thirteen.`
* BACKLOG: BL-369 moves to `in-progress` with this batch and closes with the fix; the P4-32 audit
  row N-1 changes from `open` to `fixed in <commit>` with the tests of section 10 cited (the lead's
  edit; `test_p4_audit_items_cite_tests` reads that file).

## 9. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only)

* **S-V1** (the end-reason lists, 12 to 13; every list gains a member and every count moves with
  it): `evalcore_batcha_support.py` (`END_REASONS`), `test_sarvam_sim_calllog.py` (the list),
  `test_sim_outcome_doc.py` (the tuple, the docstring, and the body of
  `test_every_end_reason_is_a_valid_record`, which builds `voice_failed` with an LLM caller like
  `brain_failed`), `test_adr0027_documents_match_json_schemas.py` (the list, the problem text, and the
  mutation `_drop_end_reason`, which now drops the last member, `voice_failed`), `test_sim_callstate.py`
  (`assert len(seen) == 13` and the docstring), `test_sim_engine_boundary.py` (`== 13`).
* **S-V2** (`test_sim_brain_failed_records.py`): the two assertions that pin the batch G list
  (`(*ELEVEN, "brain_failed")` and `== 12`, and the schema enum) gain `voice_failed` and 13. The test
  names stay (ADR 0026 cites them).
* **S-V3** (`test_phase4_batch_g_wiring.py`): `TWELVE` becomes `THIRTEEN` with its new member, the
  ADR texts it reads say thirteen, and the ADR 0027 block check reads `"brain_failed",
  "voice_failed"]`. The names stay.
* **S-V4** (`test_cli_simulate_brain_failed.py`): the meaning of exit code 2 now also names the
  voice (`..., or the caller's language model or voice failed`). Enabling: the words of the table
  gain three words.
* **S-V5** (ADR 0026, text only): rule 9 (list, paragraph, trade-off, three tests), rule 19, invariant
  I3. **S-V6** (ADR 0027 and ADR 0029, text only): enum, one cross-field bullet, rule 16, rule 6.
  **S-V7** (SCOPE, text only): the P4-04 list and a dated update on P4-29.

The wiring test (`test_phase4_batch_voice_wiring`) pins after the changes: the thirteen names in the
four test files, in order; the thirteen in rule 9, in the order of `simtypes`; `voice_failed` in
rules 9, 16 and 19; `thirteen end reasons` in ADRs 0026 and 0029 and never `twelve`; the enum tail
in the ADR 0027 block; the P4-04 row; the class to key table of section 3.1 against the code.

## 10. Test inventory

New, with the SCOPE-style names the ADR text cites:
`test_the_end_reasons_are_the_twelve_of_batch_g_and_voice_failed_last`,
`test_a_failed_synthesis_ends_the_caller_with_voice_failed_and_typed_facts`,
`test_a_failure_of_the_voice_at_the_first_line_is_not_a_successful_run`,
`test_the_exit_code_table_says_what_two_means_with_the_voice`,
`test_tdeg_sim_voice_failed_every_case_is_exactly_what_adr_0027_rule_16_says` and
`test_tdeg_sim_voice_failed_schema_identical_to_the_other_cases`.

Files (counts are in `counts.txt`): `evalcore/test_sim_voice_failed_records.py`,
`evalcore/test_phase4_batch_voice_wiring.py`, `evalsim/test_evalsim_voice_failed.py`,
`cli/test_cli_simulate_voice_failed.py`, `cli/test_tdeg_sim_voice_failed.py` and the support module
`cli/cli_p4v_support.py`. Every new test name is defined exactly once in the workspace (a test of the
wiring file checks it); the names of batch G are not reused.

## 11. Evidence (under `docs/phase4/evidence/red/batch-voice/`)

`counts.txt` (the numbers per Python version: 3.11 to 3.14 each give red 103 failed, 438 passed, 1
skipped on the 542 selected items; the oracle 541 passed, 1 skipped; the whole workspace with the
oracle 16347 passed, 1 skipped, 1301 deselected; of the 127 new items 91 are red and 36 are guards
that pass today), `red.py3.*.txt` (the tree at HEAD 755f714 plus the staged tests plus the spec
change, no oracle), `red-reasons.py3.13.txt`, `red-tests-that-pass-on-the-current-tree.txt`,
`red-without-spec-change.py3.13.txt`, `oracle.py3.*.txt`, `full.py3.*.txt` (the whole workspace with
the oracle), `mutants.txt` (68 of 68 killed), `oracle-src.patch` (9 files) and `spec-change.diff`
(13 files). Not run: Postgres parity (no Docker), Python 3.15, the lowest-direct dependency leg,
hosted CI, gitleaks, any real provider.

## 12. Ambiguities, with recommendations (the lead auto-approves)

A1. The brief says `voice_failed` is run state `error`; the 0.0.1 `Run` has no state or status
    field, only the end reason in `tracer.sim_outcome/1`. Recommendation: as for `brain_failed` (batch
    G A1): no new field; the end reason plus exit 2 is the `error` state. Rejected: a Run status.
A2. The printed status. `voice failure:` prints `(HTTP 200)` for `tts_response_invalid` when the reply
    was a 200 that is not audio, exactly as `brain failure:` does for `llm_response_invalid`; the
    refusal line before the call (`_tts_failure_line`) hides a 200. Recommendation: mirror the brain
    line (one rule for both printed failures; the 200 is a fact of the reply, not provider text).
A3. Where the typed facts live. Recommendation: stdout and the call log only, as BL-373 records for
    the brain (the schema is closed); one reopened row covers both keys in a later schema revision.
A4. A raised `TtsCapReachedError` was `transport_error`; the Sarvam adapter returns it in the outcome,
    so the raised path is unreachable with it today, but a fake or a future engine may raise it.
    Recommendation: `spend_cap` on both paths (a cap is never a failure).
A5. Configuration errors (`TtsKeyMissingError`, `TtsLanguageNotSupportedError`,
    `TtsTextTooLongError`) raised in the call map to `voice_failed` `tts_error`, not to a refusal.
    Today the key is read at step 6 and the voice language is the profile's, so they are not
    reachable mid-call with the Sarvam engine; the mapping is for any engine. Recommendation: keep.
A6. The brain request that preceded a failed synthesis is counted and kept (its usage is paid), so
    `voice_failed_first` has the usage `sim_caller_llm` where `brain_failed_first` has none.
    Recommendation: keep (never hide spend).
A7. Both failed-caller reasons are the same defect class with different owners of the key. A later
    batch may add the class to the outcome document for both (BL-373); until then the two printed
    lines are the only place for it.
A8. Lazy pre-synthesis in a future batch would make `voice_failed` reachable for the scripted caller
    (section 6). Recommendation: decide then; the cross-field rule and two tests move together.

## 13. Contradictions and findings

F1. Nothing in the tree contradicts the design. The one behaviour that changes beyond the new reason
    is A4 (a raised cap), which no test or document pinned.
F2. Three places state the meaning of exit code 2 and must change with `simargs.SIM_EXIT_CODES[2]`:
    `packages/cli/README.md` (a test compares the table with the code; the oracle carries the edit),
    `docs/phase4/runbooks/R1.md` line 92 (not touched here: another agent owns it; it says `the
    caller's language model failed (not used in R1)` and should add `or voice`), and ADR 0026 rule 19
    (S-V5). The CHANGELOGs of `tracer-cli`, `tracer-evalsim`, `tracer-evalcore` and
    `tracer-evalproviders` are the dev's to update with the batch.
F3. BACKLOG BL-369 says `generalise brain_failed to caller_failed` was one of two options; this batch
    records the reasons for rejecting it (section 0 and the ADR text).
F4. The mitigation for R1 (the caller audio is synthesised before the call) stays true and untouched;
    the fix is for the gate, as the audit says.
F5. Two sentences of the batch G contract are superseded by this one and are left as history there
    (that file is not edited): section 8.4, `A failure of the caller's text-to-speech in the call
    stays transport_error (ambiguity A4)`, and its ambiguity A4, which asked for this decision.
F6. The T-DEG listing in ADR 0027 rule 16 is the only place that lists the matrix cases; the case
    count of the batch F wiring test (19 names in the rows P4-26 to P4-30) is unchanged because the
    new cases live in their own file and no test name is added to a SCOPE row.
