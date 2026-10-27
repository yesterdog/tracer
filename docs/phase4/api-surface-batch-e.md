<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch E: the call runner, `tracer simulate` and the simulated run's bundle, report and store (P4-21 to P4-25)

Status: red-first contract from `qa`. Milestone M1: `tracer simulate` runs OFFLINE with fake engines
(a fake agent, a fake TTS tone, a scenario STT) and writes a store, a recording, a report and a call
log. Batches 0, A, B, C and D are committed; their names are reused as they are (never duplicated).
Every test in this batch is written against this file. The oracle (the minimal implementation the tests
were proved against) is `oracle-src.patch` next to the evidence; it is a reference for the dev, not
the dev's code to copy blindly.

Open decisions that stay open: O1 (Sarvam terms) and O4 (pre-registered values). No test sends a real
request: everything uses fakes, a `MockTransport` and the loopback WebSocket of the tests.

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-e/staging/tests/<package>/`; no `__init__.py`; none of
them is edited by the dev. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). The support modules
   (`evalcore_p4e_support.py`, `evalsim_support_runner.py`, `cli_p4e_support.py`) are plain modules
   found through pytest's rootdir import mode, as the Phase 3 and batch D support modules are.
2. Apply `spec-change.diff` (named spec changes S-E to S-E5, section 14) as exact-string edits.
3. Create nothing under `packages/` that has no `pyproject.toml`. All new modules go into the four
   existing packages (section 1).
4. Apply the wiring of section 13 (CLI dependency, licence policy, lock) and run
   `uv lock` offline-capable (`uv lock --offline` works; the lock gains exactly the `tracer-cli ->
   tracer-evalsim` edge).
5. Run `uv sync --locked --all-groups && uv run --locked pytest -q`.

Marker use: `test_evalsim_runner_loopback.py` carries `pytest.mark.loopback` (batch D, S-D1);
`test_simulated_bundle_stores_on_both_backends[postgres]` carries `postgres` (deselected by default;
needs `TRACER_TEST_POSTGRES_URL`).

## 1. Package ownership

| Package | New or changed modules |
|---|---|
| `tracer-evalcore` (pure, no async, no network) | `simspec`, `simprofile`, `simoutcome`, `simscore`, `simbundle`, `sttparts`, `_schemalite` (a ~80-line hand validator of the JSON Schema subset the two stored documents use; no new dependency); `stt.py` gains `SttCapReachedError` |
| `tracer-evalsim` (async; one runtime dependency `websockets`) | `runner`, `lines`, `fakeagent`, `allowlist`; `limits` gains `MAX_AGENT_AUDIO_BYTES = 268_435_456`, `CONNECT_TIMEOUT_S = 10.0`, `CLOSE_TIMEOUT_S = 5.0` |
| `tracer-evalproviders` | `sarvam.engine.create_sarvam_stt_for_run` |
| `tracer-cli` | `simargs` (stdlib only), `simulate` (the ONLY importer of `tracer_evalsim`, imported lazily from `cli.run` so `tracer score` and `--help` never load it), `simsummary`, `simreport`, `fakes`; `cli.py` changes (section 10.4) |

`EXIT_CODES` (the Phase 3 table) is unchanged. `SIM_EXIT_CODES` (in `simargs`) is that table plus `5`.

## 2. Names that are reused from earlier batches (not redefined)

`Clock`/`SystemClock` (`tracer_evalsim.clock`), `RunnerSettings`'s peers `TurnTakingPolicy`,
`Watchdog`, `InboundReader`, `Dialect`, `WireConnection`, `ConnectionLabels`, `BearerToken`,
`connect_ws` (batch D); `SimCallLog`, `TtsCache`, `SarvamClient`, `load_role_key`, the TTS engine and
`ReplayBrain`/`CallerBrain` (batches A to C); `check_run_bundle`, `MetricResult`, `Run`, `RunBundle`
(Phase 1 to 3); `UsageStreamingSttEngine`, `chunk plans` (Phase 3). The oracle imports them by their
existing names.

## 3. `tracer_evalsim.runner`, `.lines`, `.fakeagent`, `.allowlist` (P4-21 step 10, 11)

### 3.1 `run_call`

```python
async def run_call(
    settings: RunnerSettings, *, clock: Clock,
    connect: Callable[[], Awaitable[WireConnection]],
    lines: LineProvider, stop: StopFlag, segments: SegmentReader | None = None,
) -> CallResult
```

`StopFlag` (`request()`, `subscribe(listener)`; a second request after the first is a no-op for the
runner, the command counts requests). `PreparedLine` (frames, text, spoken-text origin), `CallerEnd(reason,
requests=...)`, `LineProvider.next(turn_index, agent_text) -> PreparedLine | CallerEnd`,
`RunnerSettings(...)`, `CallTurnRecord` (per caller line: first/last frame ns, `complete`, `self_times`),
`CallResult` (`end_reason`, `close_code`, `turns`, `agent_frames`, `observed_until_ns`, `connected_ns`,
`cap_reached`, `established`). Field-level shapes are exactly those read by
`test_evalsim_runner.py` (it is the specification; the oracle is a working reading of it).

Semantics pinned by the tests:

* The brain/line provider is asked AFTER the wait ends (never while a previous line is playing).
  A scripted run whose agent never answers the last line therefore ends `max_silence`, not
  `script_complete` (ambiguity A4).
* `CallResult.close_code` is set only for an agent-initiated Close; a send into a connection after the
  agent's Close frame is `agent_hangup`; a transport error caused by the watchdog is `max_duration`.
* The total agent audio is capped at `MAX_AGENT_AUDIO_BYTES`; hitting it ends the call with the batch D
  code `message_too_large` (A5).
* Self times: `wait_ms` is the silence offset or the barge-in offset; `reply_ms` is the first frame
  minus the last active agent arrival; `stt_ms`, `brain_ms`, `tts_ms` are set only in LLM mode
  (`None` otherwise).
* Connect and close are bounded by `CONNECT_TIMEOUT_S` and `CLOSE_TIMEOUT_S`; a failed connection
  skips the call loop and everything after `hang_up` that needs a call (`end reason` is
  `setup_failed`; the exit code is 5).

### 3.2 `lines`

`PresynthesisedLines(lines)` (scripted/replay: audio made before the call) and `LlmLines(...)` (the
brain mode; transcribes each finished agent segment in-call; takes `run_blocking`, default
`run_in_thread`, so blocking engines never stall the loop). Both implement `LineProvider`.

### 3.3 `fakeagent` (the offline agent; `--agent fake:SCENARIO`)

`FAKE_AGENT_FORMAT = "tracer.fake_agent/1"`, `MAX_FAKE_AGENT_STEPS = 1000`,
`parse_fake_agent(text) -> FakeAgentScenario` (strict JSON, closed keys; refusal `FakeAgentError`, a
stable code, never a value), `speech_texts(scenario) -> tuple[str, ...]`,
`connect_fake_agent(scenario, dialect, clock) -> Callable[[], Awaitable[WireConnection]]` (no socket).
Steps (a list under `steps`): `wait_ms`, `speak{frames,level,text}`, `silence`, `comfort`,
`garbage{size,seed}`, `hang_up`, `abort`. The fake plays on the injected clock, encodes frames for the
dialect (`pipecat-protobuf/1` or `twilio-media-stream/1`), and a level that would encode to the mu-law
zero byte is refused or lifted so speech is always active.

### 3.4 `allowlist`

`MAX_ALLOWLIST_BYTES = 65_536`; `AllowlistError`; `AllowlistEntry(target, bot_holds_telephony_credentials,
auth_token_env, origin)`; `normalise_target(str)`, `parse_allowlist(text)`, `read_allowlist(path)`,
`find_entry(entries, target)`. One entry per line: `<url> [bot_holds_telephony_credentials=true|false]
[auth_token_env=NAME] [origin=ORIGIN]`, `#` comments. Only what step 2 needs is pinned here; the
min-interval lock and the kill-switch file are batch G (P4-31) (A3).

### 3.5 Loopback

`test_evalsim_runner_loopback.py` (marker `loopback`): the same runner against the batch D loopback
WebSocket server; S-E1 adds the file to `LOOPBACK_MODULES` of the loopback guard.

## 4. `tracer_evalcore.simspec`: the four input files

Strict JSON, a `format` member, a closed key set, no default for any value, a refusal that is a stable
code (`SimSpecError(code)`, `str(error) == error.code`) and never a value.

| Parser | `format` | Keys |
|---|---|---|
| `parse_sim_evaluator` | `tracer.sim_evaluator/1` | `name`, `language`, `instructions`, `expected_outcome`, `scripted_turns` (`[{index, text, language}]`, contiguous from 0), `max_duration_ms` (1..14_400_000) |
| `parse_sim_persona` | `tracer.sim_persona/1` | `key`, `language`, `accent` (string or null) |
| `parse_sim_test_profile` | `tracer.sim_test_profile/1` | `name`, `dob`, `account_number` |
| `parse_sim_target` | `tracer.sim_target/1` | `name`, `url` |

`resolve_profile_text(text, profile)` replaces each `{{profile.<key>}}` once; unknown placeholder or a
missing profile is a refusal. Result types: `SimEvaluatorSpec`, `ScriptedTurnSpec`, `SimPersonaSpec`,
`SimTestProfileSpec`, `SimTargetSpec`. The language must be `mr` for a run (other codes are refused at
step 1, `language_not_supported`).

## 5. `tracer_evalcore.simprofile` and `simoutcome`

### 5.1 The file the user writes: `tracer.sim_profile_file/1`

`parse_sim_profile_file(text) -> SimProfileFile`. Keys: `dialect`, `audio`, `turn_taking`, `barge_ins`,
`caller`, `voices` (keyed by persona key), `stt`, `agreement{flag_min_confidence, normaliser}`,
`min_call_interval_ms`. Every value is required; nothing is defaulted (ADR 0027 rule 1, ADR 0026
rules 10, 11, 15, 18). `SimProfileError(code)`.

### 5.2 The stored document: `tracer.sim_profile/1`

`SIM_PROFILE_FORMAT`, `SIM_PROFILE_METADATA_KEY = "tracer.sim_profile"` (in
`ConfigSnapshot.metadata`), `SIM_PROFILE_JSON_SCHEMA` (equal to the ADR 0027 block),
`build_sim_profile(profile, *, persona_key, connection, caps) -> dict`,
`validate_sim_profile(document, *, scripted_turn_count=None, check_caps=True)`. The hand validator
(`_schemalite`) agrees with `jsonschema` on every structural fault the tests inject; the cross-field
rules the schema cannot state have stable codes (section 5.4).

### 5.3 The outcome: `tracer.sim_outcome/1`

`SIM_OUTCOME_FORMAT`, `SIM_OUTCOME_METADATA_KEY = "tracer.sim_outcome"` (in `Run.metadata`),
`SIM_OUTCOME_JSON_SCHEMA`, `authorisation_reference_sha256(reference) -> str` (SHA-256 of the UTF-8
text), `validate_sim_outcome(document, *, brain_mode=None)`,
`build_sim_outcome(*, dialect, call_established, end_reason, cap_reached, connected_ms,
authorisation_reference_sha256, caller_text_origin, counts, brain_mode=None) -> dict`. The document
says whether the call was established, its one end reason, the cap that ended it, the hash (never the
text) and the counts; its `outcome` is always `not_evaluated`.

### 5.4 Cross-field rules (ADR 0027)

Each is a refusal with a stable code listed in the tests (`test_sim_profile_doc.py`,
`test_sim_outcome_doc.py`): e.g. a not-established call has no `connected_ms` and ends
`setup_failed` or `kill_switch`; a cap end reason needs `cap_reached`; `brain_mode` fixes which
`caller_text_origin` is allowed.

`tracer.timing/1` (`simtiming`, batch C) is stored in `Run.metadata["tracer.timing"]`.

## 6. Scoring (`tracer_evalcore.simscore`, P4-22)

* Keys: `sim_first_agent_audio_ms`, `sim_agent_response_gap_median_ms`, `sim_overlap_ms`,
  `sim_barge_in_stop_ms`, `sim_answered_caller_turns` (kind `latency` or `count`, unit `ms`/`count`,
  `requires=["audio","turn_timing"]`, `language_independent`, no pass rule); the median is
  nearest-rank with N recorded; a value with a missing edge is an `error` result with code
  `timing_edge_missing`, never 0, never false.
* Agreement (per-role partition): `stt_export_agent_agreement_mer`, `stt_export_agent_flag_count`,
  `stt_export_agent_flagged_token_share`; scorer version = `tracer_evalcore.__version__` +
  `"+agent-partition.1"` (`PARTITION_VERSION_SUFFIX`); agent-role export turns against agent-channel
  `our_stt`; caller-role lines are not compared. Without an export: `not_applicable` ("agreement: not
  scored (missing input agent_transcript)"). A turn in a language other than `mr` is `not_applicable`
  for a simulated run (ADR 0023 amendment A3), where a recorded run reports `error`.
* The inert check `simulated_call_no_outcome_check` (`SIM_PLACEHOLDER_KEY`): `not_applicable` with a
  stated reason; never a verdict.
* API: `SIM_REGISTRY`, `SIM_TIMING_KEYS`, `SIM_AGREEMENT_KEYS`, `SIM_SHIPPED_KEYS`,
  `SIM_EVALUATOR_NAME`, `sim_definition(key, *, created_at, flag_params=True)`,
  `SimulatedConfiguration`, `build_simulated_configuration(*, evaluator, persona, test_profile,
  sim_profile, flag_min_confidence, normaliser, created_at)`, `score_simulated_results(*,
  configuration, run, timing, export, export_turns, stt, stt_turns, transcripts, audio_artifact_id,
  created_at, registry=SIM_REGISTRY) -> tuple[MetricResult, ...]`.

## 7. Bundle (`tracer_evalcore.simbundle`, P4-21)

`build_simulated_bundle(*, configuration, project, agent_target, call, stt, export, stt_warnings=(),
costs, created_at) -> RunBundle`; helpers `simulated_project(name=, created_at=)`,
`simulated_agent_target(...)`, `provisional_privacy()`; dataclasses `RecordingFacts`, `CallerLineFact`,
`ExportRead`, `SimulatedCall`; constants `CHANNEL_MAP_METHOD = "tracer_evalsim_stereo/1"`,
`TEXT_ORIGIN_KEY`, `TURN_COMPLETE_KEY`. Rules: Run `mode="simulated"`, `source="simulated"`, tier T0;
channel map declared (caller left, agent right); `available_inputs` exact and never lists
`agent_transcript` without an agent record (F6); `privacy` is false only with role evidence (a hand-built
unknown-role turn is the only way to reach the unknown case, A9); `started_at` is the connect instant
on our clock; the result passes `check_run_bundle`; the usage order is TTS, brain, speech-to-text in the
order answered.

## 8. Speech-to-text in parts (`tracer_evalcore.sttparts`, `stt.SttCapReachedError`)

`CappedStt(inner, *, cap_seconds: int | None)`: exact `Fraction` seconds of a plan before the send; a
plan that would cross the cap is NOT sent (typed `SttCapReachedError(reason="stt_seconds")`, empty
usage); the cap is inclusive; `None` sends everything; a bad cap is `ValueError` (message names
`cap_seconds`); `used_seconds`; passes `preflight`, `close` and `streams_usage` through;
`single_chunk_plan(plan, index)`; `assemble_stt_outcome(parts, *, plan, context)` joins single-chunk
outcomes into the one `our_stt` transcript (language left open when chunks disagree; empty chunks
counted in `tracer.empty_chunks`; refuses missing or failed parts with `ValueError`).
In-call STT reuse is by the exact `(start_frame, end_frame)` of a chunk (A12), so no chunk is ever sent
twice.

## 9. `create_sarvam_stt_for_run` (`tracer_evalproviders.sarvam.engine`)

```python
def create_sarvam_stt_for_run(*, call_log: SimCallLog, model: str,
    environ: Mapping[str, str] | None = None, env_file: str | os.PathLike[str] | None = None,
    transport: httpx.BaseTransport | None = None, sleep=time.sleep, monotonic=time.monotonic) -> SarvamStt
```

Keyword-only; `model` and `call_log` have no default; the key is the role key `stt`
(`load_role_key("stt", ...)`, `SttKeyMissingError(reason="key_missing")` when absent, never echoing the
key); request lines go into `call_log.for_stt()`; the engine does not own the log (the run closes it).

## 10. `tracer simulate` (P4-21, P4-23)

### 10.1 Options

| Option | Rule |
|---|---|
| `--target FILE` XOR `--agent fake:SCENARIO` | exactly one, else usage 64 |
| `--allowlist FILE` | with `--target` only; required for a real run (refusal `allowlist_missing`, exit 4) |
| `--evaluator`, `--persona`, `--sim-profile`, `--out` | required |
| `--profile FILE` | optional (test identity values) |
| `--tts {sarvam,fake}` | required |
| `--caller {scripted,sarvam,fake:DIR}` | required |
| `--stt {sarvam,fake,fake:DIR}` | required; bare `fake` needs `--agent` (usage 64) |
| `--max-call-seconds` (1..14400), `--max-tts-chars`, `--max-llm-tokens`, `--max-stt-seconds` | positive integers; each required when the engine it bounds is real (refusal `cap_missing (--max-...)`, exit 4); `--max-call-seconds` when any engine is real |
| `--authorisation-reference TEXT` | 1 to 200 chars; a shape error is usage 64; absence in a non-dry run is refusal `authorisation_reference_missing` (exit 4); only its SHA-256 is stored |
| `--agent-transcript FILE` + `--declared-language CODE` | go together; only `mr` |
| `--env-file FILE` | the one place the `SARVAM_API_KEY` is read besides the environment |
| `--dry-run` | print the plan, send nothing |

No option has a default for a value that decides a call or its spend. There is no `--embed-audio`
option (the report links the WAV; embedding is a BL row) and no `--caller-model` (the model comes from the
sim profile; A2).

### 10.2 The 15 steps (pinned by call trace; `STEPS`)

`parse_inputs, allowlist, authorisation, caps, output_probe, keys_and_call_log, presynthesis,
open_store, connect, call_loop, hang_up, post_call_stt, scoring, store_write, report`.

* Every refusal happens before any key read or connect (steps 1 to 5 create nothing: step 5 probes the
  output path without creating it).
* Keys are read first (a missing key leaves no directory); then the `SimCallLog` is opened.
* A TTS cap hit in pre-synthesis is refusal `spend_cap_reached_before_the_call (tts_chars)`;
  other TTS failures are exit 1.
* Dialect `smallwebrtc/1` is refused (`dialect_not_runnable`; the media path is unimplemented, A6);
  `--stt sarvam` at 8 kHz is refused at step 1 (`stt_sample_rate_not_accepted`).
* Post-call STT of a plan over `--max-stt-seconds` is a failed transcription (exit 3).
* A connection that fails is exit 5 with end reason `setup_failed` (or `kill_switch`).
* `SIGINT`/`SIGTERM` hang up gracefully; a second signal aborts; usage is kept.
* A `BaseException` after step 6 writes the recovery file, prints `usage recovery: <path|not written>`
  immediately and re-raises (`KeyboardInterrupt` exits the process as Python does).

### 10.3 Exit codes

0 established and every result scored or `not_applicable`; 1 unexpected failure; 2 a result not scored
(status `error`; includes a timing result for an agent that never speaks); 3 speech-to-text failed
(an `stt_failed` warning); 4 input refused; 5 call not established; 64 usage. `exit_code_of(bundle)`:
5 if not established, else 3 if an `stt_failed` warning exists, else 2 if any result has status
`error`, else 0.

### 10.4 `cli.py` changes

`add_simulate_arguments(parser)` and `simulate_usage_problem(args)` come from `simargs`; the
`simulate` subparser gets the options and the exit-code epilog; `run()` validates the cross-option
usage then lazily imports `simulate` and calls `simulate.run_simulate(args, environment)`;
`Environment` gains `sim: Any = None` (a `SimWorld`).

`SimWorld` (all optional seams): `clock`, `run_async`, `connect`, `tts_factory`, `llm_factory`,
`stt_factory`, `signals` (a `SignalSource` with `install(on_signal)`), `trace`, `run_blocking`.
Companion types `EngineSeam(role, call_log, out_dir, environ, env_file, transport, sleep, cap, model,
cache)` and `ConnectRequest(url, dialect, labels, token)`.

### 10.5 Stdout and stderr

Stdout lines (M1, virtual clock, `--out out`, everything fake): `result: call established`,
`end reason: script_complete`, `outcome: not evaluated`, `dialect: pipecat-protobuf/1`,
`caller audio: synthetic (generated speech, not a person)`, `caller turns: 2 started, 2 completed`,
`agent segments: 2`, `first agent audio: 400 ms (observed at the simulator over pipecat-protobuf/1,
quantum 20 ms)`, `gap after the caller's line (median): 0 ms (...)`, `overlap: 60 ms (...)`,
`answered caller turns: 2 (...)`, `agreement: not scored (missing input agent_transcript)`,
`tts usage: none`, `llm usage: none`, `stt usage: none`, `cost: not available (no_usage)`,
`ingest warnings: 0`, `bundle: rbnd_...`, `store: out/tracer.sqlite`,
`recording: out/recording-20261027T120000Z.wav`, `report: out/report-20261027T120000Z.html`.
`test_cli_simulate_m1.py` pins the exact text. Dry run: `dialect: ...`, `caller: scripted (2 lines)`,
`tts: fake, 14 characters to synthesise in 2 requests, 0 cache hits`, `llm: none`,
`stt: fake, planned after the call from the agent audio`, four `max ... : not set|N` lines and
`dry run: nothing was sent`. Stderr: one `error: ...` line (a stable code, never a value, target,
number or key); an unexpected failure is `error: unexpected failure (<ClassName>)`.
The output never claims accuracy or quality (no "accurate", "good", "latency is", ...).

### 10.6 Files written (mode 0600, directory 0700)

`tracer.sqlite`, `recording-<UTC>.wav` (stereo, caller left, agent right, the artifact URI is
`file:///...`), `report-<UTC>.html`, `sim_call_log.jsonl`, `tts-cache/` (real TTS only), and
`usage-recovery-<UTC>.json` when a request was answered and something later failed. No file is
overwritten; an existing store file that is a link is refused (`store_file_is_a_link`, exit 4, nothing sent).

### 10.7 Recovery file: `tracer.usage_recovery/1` (`RECOVERY_FORMAT`, existing Phase 3 writer)

`{format, created_at, failed_step ("call"|"store"), error_class, usage_records, cost_records,
cost_summaries, pricing_catalogs}`; only the class name of the error, never its message. After a store
failure the stdout lines are `store: not written`, the report and recording lines, then
`usage recovery: <path|not written>` as the last line.

### 10.8 Engines and the ledger

`FakeTts` (a 40 ms-per-character tone, provider `fake`, model `tone`) and `ScenarioStt` (the words of
the fake agent, model `scenario-text`) in `tracer_cli.fakes`; real TTS goes through the batch A/B cache
and call log; the LLM caller counts tokens (`_CountingLlm`) against `--max-llm-tokens`; every answered
request is added to the ledger in the order answered (TTS, brain, speech-to-text).

## 11. Report (`tracer_cli.simreport`, `simsummary`, P4-24)

`render_sim_report(bundle, *, audio) -> str` is a single self-contained file (existing CSP and seek
script; the audio is a relative `<audio>` link; Chrome and Edge only). Element ids: `synthetic-label`
("synthetic caller audio", terms 10.2(8)), `outcome-label` ("outcome not evaluated"), `timing` (the
table: quantum and dialect shown), `timing-turns`, `timeline` (caller and agent intervals),
`audio-facts`, `usage-*`, plus the agreement, counts, warnings and snapshot-note sections. All text from
a call is escaped; bidirectional control characters are neutralised; the page never claims accuracy or
quality; the three keys (Sarvam, agent token, authorisation reference) never appear.
`sim_facts(bundle) -> SimFacts`, `sim_summary_lines(bundle) -> list[tuple[str, str]]`,
`stage_usage_text`, `exit_code_of`.

## 12. Store (P4-25)

12.1 The bundle round-trips through SQLite byte for byte (`store_run` then `load_run`); a second run
joins the same store with fresh identifiers; the same bundle stores with the same rows on Postgres.
A failed `store_run` keeps the report, the recording and the usage (exit 1, a recovery file).
12.2 `assert_sim_three_keys_never_in_outputs(out_dir, caplog)` (in `cli_p4e_support`) checks the
Sarvam key, the agent token and the authorisation reference against stdout, stderr, logs and every file
under the output directory; it is also called at the end of the Phase 3 test
`test_key_never_in_store_and_report_outputs` (S-E4).

## 13. Wiring (the dev makes these changes)

* `packages/cli/pyproject.toml`: dependencies gain `"tracer-evalsim==0.0.1"` (after
  `tracer-evaldb==0.0.1`); no extras; the console script is unchanged.
* `docs/security/license-policy.toml`, block `[runtime.cli]`: `requires_dist` gains
  `tracer-evalsim==0.0.1`; `closure` gains `websockets` (sorted).
* `uv.lock`: regenerate (`uv lock`); the only change is the `tracer-cli -> tracer-evalsim` edge.
* CI and `scripts/check_licenses.py`: no new job; the existing closure/licence checks read the
  policy block above. Nothing pulls the `[webrtc]` extra into the CLI.
* Only `tracer_cli/simulate.py` imports `tracer_evalsim` (guard test).

## 14. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only)

* S-E (`evalcore_phase4_adr_support.py`): 35 names leave the `_P...` pending sets because the tests now
  exist (`_P0026` 10, `_P0027` 13, `_P0028` 1, `_P0029` 4, `_P0024A` 1, `_P0023A` 5, `_P0004` 1), and the
  comment headers that became empty. `ADR04_NAMES` and `PENDING_OUTSIDE_RULES` stay untouched. Applied
  by the dev as exact-string edits; no replacement of the file is staged.
* S-E1 (`test_evalsim_loopback_guard.py`): `LOOPBACK_MODULES` gains `test_evalsim_runner_loopback.py`.
* S-E2 (the CLI now depends on `tracer-evalsim`): `test_phase3_licenses.py`, `test_phase3_scaffold.py`,
  `test_phase4_licenses.py` (the "websockets not in a runtime closure" assertion becomes
  `("websockets" in closure) == (key == "cli")`) and `test_evalsim_scaffold.py::
  test_phase4_locked_closures_are_the_reviewed_ones` follow the new dependency.
* S-E3 (`test_cli_scaffold.py`): `ALLOWED_IMPORTS` gains `tracer_evalsim`; `ALLOWED_LOADED` gains
  `websockets`; `websockets` leaves `FORBIDDEN_LOADED`.
* S-E4 (`test_cli_report.py::test_key_never_in_store_and_report_outputs`, report channel): extended to
  the three keys (SCOPE P4-24) by calling `assert_sim_three_keys_never_in_outputs` at its end.
* S-E5 (`test_adr0004_simulated_agent_transcript_rule_cites_test.py::
  test_adr0004_check_refuses_pending_sets_that_do_not_fit`): the binding test is defined now, so an
  empty pending set is valid; the refusals are kept by naming an undefined binding test and a landed
  pending name.

`test_cli_has_no_export_command` is already {score, simulate}: unchanged.

## 15. Test inventory (exact SCOPE names)

P4-21: `test_order_of_operations_pinned_by_call_trace`,
`test_refusals_happen_before_any_key_read_or_connect`, `test_usage_survives_ctrl_c_mid_call`
(`test_cli_simulate_order.py`); `test_simulated_bundle_passes_check_run_bundle`,
`test_available_inputs_never_lists_agent_transcript_without_agent_record`,
`test_privacy_marker_false_only_with_role_evidence` (`test_sim_bundle.py`).
P4-22: `test_timing_definitions_have_no_pass_rule`, `test_median_is_nearest_rank_with_n_recorded`,
`test_agreement_partition_compares_agent_role_only`, `test_partition_changes_scorer_version`,
`test_outcome_check_inert_and_no_verdict` (`test_sim_scoring.py`).
P4-23: `test_simulate_requires_caps_for_real_engines`, `test_simulate_dry_run_prints_plan_and_sends_nothing`,
`test_simulate_exit_codes`, `test_simulate_output_never_claims_accuracy_or_quality`,
`test_simulate_never_prints_target_number_or_key` (`test_cli_simulate_options.py`).
P4-24: `test_sim_report_labels_synthetic_audio`, `test_sim_report_states_outcome_not_evaluated`,
`test_sim_report_timing_shows_quantum_and_dialect`, `test_sim_report_never_claims_accuracy_or_quality`
(`test_cli_simulate_report.py`), `test_key_never_in_store_and_report_outputs` (extended; S-E4).
P4-25: `test_simulated_run_round_trips_through_store_byte_identical`,
`test_simulated_bundle_stores_on_both_backends` (`test_cli_simulate_store.py`).
`test_phase4_batch_e_wiring.py` checks that these 23 names exist exactly once (the Phase 3 name twice,
as before) and have left the pending sets. Every other test has a docstring giving its reason.
Staged: 18 test files and 3 support modules (421 collected test items in the new files, 420 selected by default; the Postgres case is deselected).

## 16. Evidence (under `docs/phase4/evidence/red/batch-e/`)

`red.py3.*.txt` (current tree, per Python; 422 failed, 109 passed), `red-reasons.py3.13.txt`,
`red-tests-that-pass-on-the-current-tree.txt`, `red-without-spec-change.py3.13.txt`, `oracle.py3.*.txt`
(420 passed), `full.py3.*.txt` (14593 passed, 1 skipped), `counts.txt`, `mutants.txt` (76 of 78 killed;
the 2 survivors are equivalent), `oracle-src.patch`, `spec-change.diff`. Not run: Postgres parity (no
Docker here), Python 3.15, the lowest-direct dependency leg, hosted CI.

## 17. Ambiguities, with recommendations (the lead auto-approves)

A1. SCOPE writes `--tts sarvam|fake:<dir>`, `--agent`-less forms and `--stt fake:<dir>`; the offline
    M1 needs a bare `fake` and `--agent fake:SCENARIO`. Recommendation: accept bare `fake` for TTS and
    STT (STT bare only with `--agent`), keep `fake:<dir>` for `--caller` and `--stt`; defer `fake:<dir>`
    TTS (a BL row).
A2. No `--caller-model` option. Recommendation: the model comes from the sim profile (one place for
    every value).
A3. The allowlist, min-interval lock and kill-switch file overlap batch G (P4-31). Recommendation: only
    what step 2 needs is pinned here.
A4. A scripted run whose agent never answers the last line ends `max_silence`. Recommendation: keep
    (the end reason says what was observed).
A5. `message_too_large` is reused for the total agent-audio cap. Recommendation: keep; no new code.
A6. `smallwebrtc/1` is not runnable (media path unimplemented). Recommendation: refuse
    (`dialect_not_runnable`) until a batch implements it.
A7. A close before the first frame is not `setup_failed`. Recommendation: keep (the call was
    established).
A8. `search_ms = min(5000, max_chunk_ms)` for the post-call chunk plan. Recommendation: keep.
A9. A zero-chunk agent produces no `our_stt` transcript; the unknown-role privacy case is reachable only
    by hand-built turns. Recommendation: keep, the test builds the turns by hand.
A10. `--stt sarvam` at 8 kHz is refused at step 1. Recommendation: keep (nothing is sent to learn this).
A11. Timing exit 2 for an agent that never speaks. Recommendation: keep (`timing_edge_missing` is an
    `error` result, not 0).
A12. In-call STT reuse by exact `(start_frame, end_frame)`. Recommendation: keep.
A13. The `run_blocking` and `signals` seams of `SimWorld`, and the `Environment.sim` field.
    Recommendation: keep; tests only, never used by the product path.
A14. The Phase 3 `score` help text is unchanged. Recommendation: unchanged.
A15. Postgres parity was not run here. Recommendation: the lead runs it with
    `TRACER_TEST_POSTGRES_URL` before the gate.
