<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch F: interop with Pipecat, the simulated run end to end, the T-DEG matrix, the guards (P4-26 to P4-30)

Status: red-first contract from `qa`. Batches 0, A, B, C, D and E are committed (batch E at 7699bd4);
their names are reused as they are. This batch writes tests for FIVE tasks, two of which (P4-26, the
interop part of P4-27) need code that does not exist, and three of which (P4-28, P4-29, most of
P4-30's canaries) pin behaviour that batch E already built. Every test is written against this file.
The oracle (the minimal reference the tests were proved against) is `oracle-src.patch` next to the
evidence; it is a reference for the dev, not code to copy blindly. The real Pipecat integration tests
have NOT been run (they need the tool's own environment, which needs the network): they are red on
the current tree for the right reason, and the command that runs them is in section 3.3.

Open decisions that stay open: O1 (Sarvam terms), O3 (the stack of the `llm_bot`), O4 (pre-registered
values). No test sends a real request: the unit tests use fakes and a `MockTransport`; the
integration tests connect to a bot that the test starts itself, on this machine, with no key.

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-f/staging/tests/<package>/`; no `__init__.py`; none of
them is edited by the dev. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). The support modules
   (`cli_p4f_support.py`, `evalsim_integration_guard.py`, `evalsim_support_inner.py`,
   `evalsim_support_samplebot.py`) are plain modules found through pytest's rootdir import mode.
2. Apply `spec-change.diff` (named spec changes S-F to S-F8, section 8) as exact-string edits. It
   edits nine existing test files; do not replace any of them. `evalcore_phase4_adr_support.py` is also
   edited by batch J: merge the line deletions (section 8, S-F).
3. Create nothing under `packages/` that has no `pyproject.toml`.
4. Build the code of sections 1, 3.4 and 6 (the reference is `oracle-src.patch`; it does not contain a
   lock for the tool: `uv lock --project tools/samplebot` needs the network and is the dev's step).
5. Run `uv sync --locked --all-groups && uv run --locked pytest -q` (the default run; the interop tests
   are deselected), then the interop job by hand (section 3.3).

Until step 2 and the code of step 4 exist the four integration modules fail AT COLLECTION with
"'integration' not found in `markers` configuration option" (that is the right reason: the marker is
registered by the conftest of S-F1). Run a red check with `--continue-on-collection-errors`.

Files (18, of which 14 collect tests and 4 are support modules):

| Package | Test files | Support modules |
|---|---|---|
| `evalcore` | `test_samplebot_static.py`, `test_phase4_ci_interop.py`, `test_phase4_batch_f_wiring.py` | none |
| `evalsim` | `test_evalsim_no_socket.py`, `test_evalsim_integration_guard.py`, `test_evalsim_smallwebrtc_dialect.py`, `test_integration_pipecat_protobuf.py`, `test_integration_pipecat_twilio.py`, `test_integration_pipecat_webrtc.py`, `test_integration_calibration.py` | `evalsim_integration_guard.py`, `evalsim_support_inner.py`, `evalsim_support_samplebot.py` |
| `cli` | `test_tdeg_sim.py`, `test_e2e_sim_synthetic_mr.py`, `test_guard_sim_outputs_and_allowlist.py`, `test_key_canaries_all_channels.py` | `cli_p4f_support.py` |

## 1. `tools/samplebot` (P4-26)

A separate uv project (the batch 0b scaffold stays: `pyproject.toml` with `pipecat-ai[websocket,runner,
webrtc]==1.14.0`, `package = false`, README). It is linted by the root `ruff` (it is inside the
repository) and is not in the root `mypy` files. Nothing under `packages/` may import it and it imports
no `tracer_*` package.

### 1.1 Layout (exact names; extra modules are allowed and are held to the same rules)

```
tools/samplebot/
  pyproject.toml  README.md  uv.lock           # its own lock, pipecat-ai pinned 1.14.0, aiortc in the closure
  samplebot/__init__.py
  samplebot/scenario.py        # stdlib only: FORMAT, Reply, Scenario, ScenarioError, parse_scenario
  samplebot/replies.py         # stdlib only: tone_pcm_s16le
  samplebot/summary.py         # stdlib only: estimate_hz, CallTally
  samplebot/end_of_caller.py   # pipecat: class EndOfCallerTimer(FrameProcessor)
  samplebot/scripted_bot.py    # pipecat: the pipeline and the command line
```

`llm_bot` (P4-26 (b)) is NOT built in this batch: it follows owner answer O3, which is open, and is
owner-gated to run (a BACKLOG row). `test_samplebot_llm_bot_waits_for_the_owner_answer_o3` asserts
that `samplebot/llm_bot.py` does not exist and that nothing refers to it; the batch that builds it
replaces that test by a named spec change.

### 1.2 Command line (`python -m samplebot.scripted_bot`, run from `tools/samplebot`)

`--dialect {pipecat-protobuf/1,twilio-media-stream/1,smallwebrtc/1}` `--scenario FILE`
`--sample-rate N` `--host {127.0.0.1,::1,localhost}` `--port N`. Every option is REQUIRED and has NO
default (`--port 0` picks a free port). `--host` has exactly the loopback choices: the bot never
binds a wildcard address and the strings `0.0.0.0` and `::` appear nowhere in the tool. One dialect
per process; one process serves calls one after another (not concurrently). SIGTERM exits 0.

### 1.3 Scenario file `tracer.samplebot_scenario/1`

Strict JSON (duplicate keys, `NaN` and `Infinity` refused), closed keys, no default:

```
{"format": "tracer.samplebot_scenario/1", "end_of_caller_ms": 20..5000,
 "replies": [{"delay_ms": 0..10000, "tone": {"hz": 50..3400, "ms": 20..10000, a multiple of 20, "level": 1..32767}}, ...1 to 64],
 "after_replies": "stay_silent" | "hang_up"}
```

Every number is an `int` (not a `bool`, not a `float`, not a string). `parse_scenario(text) ->
Scenario(end_of_caller_ms, replies: tuple[Reply(delay_ms, hz, ms, level)], after_replies)`; a refusal is
`ScenarioError(code)` with `str(error) == code == error.code` and the code is one of
`scenario_not_json` (not JSON, duplicate key, constant), `scenario_shape` (not an object, a missing or
extra key at any level, wrong `format`, `replies` not a list) and `scenario_value` (right shape, wrong
type or range, including zero or more than 64 replies). The refusal never holds a value.

### 1.4 What the bot prints (stdout: one JSON object per line, `sort_keys`, ASCII; logs go to stderr)

* `ready`, once, when it listens: `{"event": "ready", "dialect": D, "url": U, "pipecat": "1.14.0"}`;
  `U` is `ws://127.0.0.1:PORT/ws-client` (protobuf), `ws://127.0.0.1:PORT/ws` (Twilio-compatible) or
  `http://127.0.0.1:PORT/api/offer` (WebRTC). For the Twilio-compatible dialect it also holds
  `"auto_hang_up": false` and `"credentials": false` (the configuration of rule 6, echoed).
* `twilio_start`, once per Twilio-compatible call: `{"event": "twilio_start", "call": N, "stream_sid":
  S, "call_sid": C, "custom_parameters": {...}}` with the values it took from the caller's `start`
  message (through `parse_telephony_websocket`); never invented by the bot.
* `call_summary`, once per finished call (last thing a call prints), keys EXACTLY `event`, `call`
  (1 for the first call of the process), `dialect`, `ended_by` (`caller_closed` when the other side
  closed first, `bot_hang_up` when the bot did), `caller_turns` (a list of `{audio_ms, frames, hz}`),
  `replies_started`, `inbound_frames`, `inbound_sample_rate` (`null` when no audio arrived).

### 1.5 Behaviour (what the integration tests rely on)

1. A caller frame counts only if it is ACTIVE: it holds any non-zero byte (ADR 0026 rule 10's
   definition of activity: no threshold, no energy, no model). WebRTC streams digital silence between
   lines; the WebSocket dialects send nothing. Frames of silence are not counted in the summary.
2. A caller turn is the active audio between two timer firings. `EndOfCallerTimer` (a Pipecat
   `FrameProcessor` in the `Pipeline`) fires `end_of_caller_ms` after the ARRIVAL of the last active
   caller frame (a timer: `call_later`, `sleep`, ...). It looks at no sample (the static rule refuses
   `rms`, `energy`, `threshold`, `audioop`, `unpack`, `array`, `frombuffer`).
3. Reply `k` answers turn `k` (0-based): its first audio frame is handed to the transport
   `delay_ms` after the timer fired, as one continuous sine of `hz` for `ms`, paced in 20 ms frames.
   Turns beyond the last reply get none. The bot never speaks before the first caller turn ends.
4. `after_replies` `hang_up` closes the connection after the last reply has been played; `stay_silent`
   waits for the caller to close. The Twilio-compatible call ends when the SOCKET closes (Pipecat
   ignores an inbound `stop`), with no request to Twilio.
5. Not pinned: what the bot does if the caller speaks again while a reply is still scheduled.

### 1.6 The three pure modules (tested by value, from the file, with the standard library only)

* `replies.tone_pcm_s16le(*, hz, ms, level, sample_rate_hz) -> bytes`: `rate * ms // 1000` samples,
  16-bit little endian, `round(level * sin(2 pi hz n / rate))` (any formula with sample 0 equal to 0, no
  sample above `level`, the peak within 3 per cent of it, whole 20 ms frames, deterministic bytes, and a
  frequency the zero crossings return within 2 per cent). The test pins no byte.
* `summary.estimate_hz(pcm, rate) -> float | None`: sign changes (zeros skipped) counted between the
  first and the last, over the time between them: `(crossings - 1) / 2 / span`; `None` for fewer than
  two crossings, a flat signal, silence or no sample. Padding does not move it.
* `summary.CallTally`: `on_audio(pcm, rate)` (opens a turn), `on_turn_end()` (closes the open turn),
  `on_reply_started()`, `summary(*, call, dialect, ended_by) -> dict` (the keys of 1.4; a turn that is
  still open is closed and reported; `audio_ms = samples * 1000 // rate`).

### 1.7 Static rules (`test_samplebot_scripted_bot_uses_no_model`, `..._twilio_runs_without_auto_hang_up_or_credentials`)

Read from the source with `ast`; the same two functions refuse each weakening in the tests (the
"teeth" tests). In short: the three pure modules and `__init__` import only the standard library;
`end_of_caller.py` only the standard library and `pipecat`; every other module only the standard
library, `pipecat`, `fastapi`, `uvicorn`, `starlette`, `loguru` and `samplebot`. No import of
`pipecat.services`, `pipecat.audio.vad|turn|filters|mixers`, `pipecat.processors.aggregators|
frameworks`, any model or network library (`openai`, `httpx`, `requests`, `onnxruntime`, `numpy`, ...),
any tracer package; no imported name containing `silero`, `vad`, `smartturn`, `llm`, `stt` or `tts`;
no keyword `vad_analyzer`, `turn_analyzer`, `audio_in_filter`, `llm`, `stt`, `tts`, `api_key`,
`model`; no `https://` literal; no read of the environment (`os.environ`, `getenv`) outside the
standard-library modules; `EndOfCallerTimer` is a `FrameProcessor` taking `end_of_caller_ms` and is in
the arguments of a `Pipeline(...)` call; `--host` is required with loopback choices and no default.
Twilio: `TwilioFrameSerializer(...)` is called with keywords from `{stream_sid, call_sid, params}` only
(no `account_sid`, `auth_token`, `region`, `edge`, no positional argument), the keyword `auto_hang_up`
appears and is always the constant `False`, `parse_telephony_websocket` is used, no name or string
(outside docstrings) holds `account_sid`, `auth_token` or `api.twilio.com`, and no string holds
"twilio" with "token", "sid", "secret" or "account".

### 1.8 Lock, README, lint

`uv.lock` exists, parses, pins `pipecat-ai` 1.14.0, holds `aiortc`, holds
`tracer-samplebot` as a virtual package and no other `tracer-` package (the existing batch 0b tests
also check `num2words`, `soxr` and `soundfile` at the reviewed versions). The README contains: the
scenario format, `python -m samplebot.scripted_bot`, `uv run --project tools/samplebot`,
`pytest -m integration`, `auto_hang_up`, `bot_holds_telephony_credentials=false`, "no model",
"loopback", "ready", `pipecat-ai==1.14.0` and `P4-02-licenses.md`.

## 2. The named tests (SCOPE rows P4-26 to P4-30: 19 names, each defined once)

`test_unit_tests_open_no_socket` is the exception: it already exists in `evalcore`, `evalproviders` and
`cli`; batch F adds the fourth, in `packages/evalsim/tests/test_evalsim_no_socket.py` (the SCOPE row
calls it "extended"). `test_phase4_batch_f_wiring.py` checks the 19 names and that none is on a pending
set.

| Task | Name | File |
|---|---|---|
| P4-26 | `test_samplebot_scripted_bot_uses_no_model` | `evalcore/test_samplebot_static.py` |
| P4-26 | `test_samplebot_twilio_runs_without_auto_hang_up_or_credentials` | same |
| P4-27 | `test_integration_protobuf_dialect_against_pipecat` | `evalsim/test_integration_pipecat_protobuf.py` |
| P4-27 | `test_integration_twilio_dialect_against_pipecat` | `evalsim/test_integration_pipecat_twilio.py` |
| P4-27 | `test_integration_webrtc_against_pipecat_loopback` | `evalsim/test_integration_pipecat_webrtc.py` |
| P4-27 | `test_integration_calibration_gap_within_quantum` (3 dialects) | `evalsim/test_integration_calibration.py` |
| P4-28 | `test_e2e_sim_synthetic_mr_exact_outputs`, `test_e2e_sim_report_and_store_consistent` | `cli/test_e2e_sim_synthetic_mr.py` |
| P4-29 | `test_tdeg_sim_setup_failed_all_not_applicable_exit_5`, `..._agent_silent_timing_not_measured`, `..._no_export_agreement_missing_input`, `..._export_present_agreement_scored`, `..._stt_failure_missing_input_transcript`, `..._kill_switch_keeps_partial_recording_and_usage`, `..._schema_identical_across_cases` | `cli/test_tdeg_sim.py` |
| P4-30 | `test_unit_tests_open_no_socket` (extended) | `evalsim/test_evalsim_no_socket.py` |
| P4-30 | `test_integration_tests_loopback_only` | `evalsim/test_evalsim_integration_guard.py` |
| P4-30 | `test_key_canaries_never_in_any_output_channel` (15 hostile-provider cases; the 5 crash cases are `test_key_canaries_never_in_a_crash_after_a_paid_request`) | `cli/test_key_canaries_all_channels.py` |
| P4-30 | `test_guard_rejects_staged_sim_outputs_and_allowlist` | `cli/test_guard_sim_outputs_and_allowlist.py` |

Every other test has a docstring giving its reason (added by qa). Besides the named tests the T-DEG file
holds two more cases that the SCOPE list does not name and the matrix needs: an export that holds only the
caller's lines (the F6 trap) and a kill switch before the connection.

## 3. The interop tests, the marker and the job (P4-27, P4-30)

### 3.1 The `integration` marker and its guard (named change S-F1, `evalsim/tests/conftest.py`)

* The marker `integration` is registered in the conftest (no `pyproject` change). Tests that carry it
  are DESELECTED (never skipped) unless the run's marker expression names the marker
  (`pytest_collection_modifyitems`); `-m integration` selects them; `-m "not integration"` and a bare
  run keep them out. The root `addopts` is unchanged (so the Phase 3 tests that pin it, and every CI
  leg, are untouched). Decision A2 (section 10).
* The decision on skipping (lead's requirement): there is NO skip anywhere. A missing sample bot, a
  tool environment that was not built, an import error or a silent bot is a FAILURE with a message
  (`SamplebotUnavailable`, an `AssertionError`, naming the command to run). The integration modules
  and the harness hold no `skip`, `skipif`, `xfail`, `importorskip`, `find_spec`, `try/except ImportError`
  (`test_no_integration_module_can_skip_itself`). The only gates are the marker (deselect) and the job.
* The guard (`evalsim_integration_guard.py`): an `integration` test gets the loopback-only paths for
  TCP (as the `loopback` marker) and, for datagrams, the rule "this machine only": a literal address
  that is loopback or that a UDP socket can be bound to right now (a probe that sends nothing).
  Wildcards, multicast, broadcast, names, every other address and Unix sockets are refused with
  `AssertionError`; `getaddrinfo` resolves a literal address of the machine and nothing else. Why not
  plain loopback: ICE gathers a host candidate on every interface, so two WebRTC peers on one machine
  talk over the machine's own addresses (BL-310); with no STUN or TURN server no packet can leave.
  The unmarked guard is hardened too: an unmarked test now also cannot `create_server`, open a
  datagram endpoint or `sendto` (`test_unit_tests_open_no_socket` and its companions).
* The marker is confined to the four integration modules and the test of its guard; each integration
  module has the line `pytestmark = pytest.mark.integration` and no other marker.

### 3.2 The CI job (`.github/workflows/ci.yml`, job id `interop`)

Name `interop (scripted_bot on loopback, py3.13)`, `ubuntu-latest`, `timeout-minutes` 10 to 45 (a hung
bot must not hold the job), not `continue-on-error`, no `services`, `container`, `strategy`, `env`,
no secret, permissions inherited (`contents: read`), actions pinned by SHA, checkout with
`persist-credentials: false`, ONE `setup-uv` with Python 3.13, and exactly these three `run` steps:

```
uv sync --locked --all-groups
uv sync --locked --project tools/samplebot
uv run --locked --all-groups pytest -m integration packages/evalsim/tests
```

No other job mentions the tool, Pipecat or `integration` (named change S-F3 narrows the older
assertion of `test_phase4_ci.py`).

### 3.3 The harness and the command that runs the interop tests

```
uv sync --locked --all-groups
uv sync --locked --project tools/samplebot          # needs the network once; Pipecat is installed ONLY there
uv run --locked --all-groups pytest -m integration packages/evalsim/tests
```

`evalsim_support_samplebot.py` starts the bot as a child process: `uv run --project tools/samplebot
--locked python -m samplebot.scripted_bot <options of 1.2>` with the tool directory as the working
directory, a child environment built from an allow-list (no key, no proxy, no `VIRTUAL_ENV`), `--host
127.0.0.1 --port 0`, reads `ready` (bound `READY_TIMEOUT_S` 120 s) and the summaries
(`SUMMARY_TIMEOUT_S` 20 s) from stdout, stops it with SIGTERM. `TRACER_SAMPLEBOT_COMMAND` (a JSON list
that replaces the part before `--dialect`) exists for qa's own check of the harness with a stand-in
bot; CI leaves it unset. A call is placed with the real `run_call`, `SystemClock`, `PresynthesisedLines`
and the real connection (`open_websocket` or `open_webrtc`); the timing document is built exactly as
`tracer simulate` builds it (`extract_timing`). The caller's lines are sines written by the harness
(independent of the tool and of the product); the mu-law encoder and the frequency estimate are the
harness's own.

### 3.4 WebRTC: the decision (A1)

Read the scope: ADR 0026 rule 7 and BL-310 say the media round trip "is proven only by P4-27", and
the batch E contract (A6) refuses `smallwebrtc/1` in the command (`dialect_not_runnable`) because the
media path does not exist. Decision: the media path is a LIBRARY feature built and proved in this batch,
and the command keeps refusing the dialect until a later batch wires it (a BACKLOG row, "wire
`smallwebrtc/1` into `tracer simulate`", P2). So `test_integration_webrtc_against_pipecat_loopback`
tests the library through the same runner as the WebSocket dialects, and the existing batch E test that
pins the refusal is unchanged. Reason: wiring the command needs the sim-profile file (`audio` block
with `wire_codec` `webrtc_negotiated`), the ICE-server option and the report wording, none of which is
in P4-26..P4-30, whereas the library path is exactly what the integration test must prove.

New code (the reference is `oracle-src.patch`):

* `tracer_evalsim.dialects.smallwebrtc.SmallWebRtcDialect(*, sample_rate_hz)`: a `Dialect` with
  `dialect_id` `smallwebrtc/1`, `wire_codec` `pcm_s16le`, the spec sources and retrieval date of
  `tracer_evalsim.webrtc`; the rate is an `int` from 8000 to 48000 in steps of 50 (`ValueError`
  otherwise, `bool` and `float` included); `frame_bytes` is 20 ms (1920 at 48 kHz); `opening_messages()`
  and `closing_messages()` are `()`; `encode_audio` returns the frame itself (a `ValueError` unless it
  is exactly one 20 ms frame of `bytes`); `decode` returns `Decoded("audio", message)` for a `bytes`
  message of at least one whole 16-bit sample, else `None`.
* `tracer_evalsim.webrtc.open_webrtc(plan, dialect, *, labels, token, open_timeout_s, close_timeout_s)
  -> WireConnection` (a coroutine; the four keyword-only parameters have no default). Order of
  refusals, all before any I/O: the dialect id is not `smallwebrtc/1` -> `TargetRefusedError`
  `dialect_unknown`; the labels are inconsistent for the dialect (`bot_holds_telephony_credentials`
  not `None`) -> `labels_inconsistent`; a token without a variable name -> `labels_inconsistent`; a
  variable name without a token -> `auth_token_missing`; the extra is absent -> `webrtc_extra_missing`
  (message names `tracer-evalsim[webrtc]`). Then: an offer by `POST /api/offer` (the existing
  `HttpSignalling`, no redirect, the token only as a bearer header), the answer set, and the media up
  within `open_timeout_s` (`SetupFailedError` `connect_timeout` otherwise). No ICE server unless the
  plan names one.
* The connection: `send(frame)` hands one 20 ms PCM frame to the outgoing audio track (digital silence
  is sent when none is queued, so the track is continuous); `recv()` returns the next frame of the
  remote audio track as 16-bit mono PCM at the dialect rate (channel 0 of the decoded frame; a frame at
  another rate ends the call as a transport error) and `None` once the peer connection is gone;
  `close()` closes the peer connection and sets `close_info` to `CloseInfo(1000, True)`.
* Imports: `aiortc` and `av` are imported lazily inside functions (the module still imports without
  the extra); `av` is added to the evalsim import whitelist (named change S-F5).
* NOT pinned (ADR 0026 "not verified"): the codec, whether the call needs the client's data channel,
  whether the answer needs the `PATCH /api/offer` candidates. The dev finds out against Pipecat.

### 3.5 Calibration (gate item 12; the evidence behind every timing number)

Per dialect, 20 calls against one bot process; each call has three 300 ms lines of a 300 Hz sine and the
bot has three replies (440 Hz, 200 ms) with delays 100, 250 and 400 ms and `end_of_caller_ms` 120. So
60 readings. The injected value for turn k is `end_of_caller_ms + delay_k - 20` (the bot fires its timer
`end_of_caller_ms` after the arrival of the last active caller frame and starts the reply `delay_k`
later; the end edge of the line is the send instant of the last frame plus one 20 ms frame). The error
of a reading is `gap_k - injected_k`, with `gap_k` from the `tracer.timing/1` document. Rules:

* `protobuf` and `twilio`: EVERY reading satisfies `|error| <= quantum_ms + JITTER_MS`, `quantum_ms`
  from that call's timing document (at least 20) and `JITTER_MS = 50` (a constant of the harness; an
  engineering bound, not a measurement: a change is a named spec change with the numbers that justify it).
* `webrtc`: codec and jitter buffer are not modelled, so the bound is structural only:
  `-quantum_ms <= error <= WEBRTC_BOUND_MS` (500).
* Output: when `TRACER_CALIBRATION_OUT` names a directory the test writes
  `calibration-<kind>.json` (dialect, calls, readings, `end_of_caller_ms`, delays, `jitter_ms`, ranges of
  the quantum and of the error, median absolute error, the injected and measured lists): counts and
  ranges, never audio or text, for the measurement report (P4-36); never a claim.
* What qa observed with its stand-in bot (not Pipecat; two processes of Python on one machine): errors of
  +3 to +9 ms over the WebSocket dialects (quantum 20), +225 to +250 ms over WebRTC (aiortc on both
  sides). These are evidence about the stand-in and the harness, not about Pipecat.

### 3.6 What the dialect tests assert (each reads both sides by independent readers)

Protobuf (rate 16000, `/ws-client`) and Twilio-compatible (rate 8000, `/ws`, mu-law): the bot's
`ready` line (dialect, `pipecat` 1.14.0, the route); two lines (300 Hz 600 ms, 500 Hz 400 ms) and two
replies (440 Hz 400 ms after 200, 880 Hz 200 ms after 300): end reason `script_complete`; both turns
complete; every agent frame at the dialect rate, mono, the right codec; each reply as long as the
scenario says (60 ms) and at the right frequency (3 or 4 per cent, harness estimate); each gap not
before `end_of_caller_ms + delay - 20 - quantum` and not later than that plus 250 ms; no overlap;
`inbound_messages_ignored` 0 (the codec decoded everything Pipecat sent: if it did not, that is the
finding of BL-298/BL-282); `frames_sent` 50; the summary: `ended_by` `caller_closed`, two turns, each
as long as what was sent (40 ms) and at the frequency that was sent (the bot decoded our bytes), two
replies started. Twilio adds the `twilio_start` ids (prefix `tracer-sim-`, `{"tracer_synthetic": "true"}`),
`auto_hang_up` false and no credential in `ready`, and that no `mark` is needed or awaited. Both
WebSocket dialects add: a bot that hangs up after its last reply gives `agent_hangup` with a close code;
sequential calls to one process are independent (protobuf). WebRTC: one line and one reply, tolerances
120 ms, 5 per cent, a gap up to 500 ms late, whole 16-bit samples, `connect_*_ns` set, no failure code,
and the call ends when the simulator hangs up.

## 4. `tracer simulate` end to end, offline (P4-28)

`test_e2e_sim_synthetic_mr_exact_outputs`, `test_e2e_sim_report_and_store_consistent`, and
`test_e2e_sim_twice_gives_the_same_bytes_modulo_ids`. The call: the first four scripted lines and the
four replies of scenario `sim-mr-s1` of `packages/evalcore/tests/fixtures/sim_mr.json` (invented, hand
written, indic-linguistics, needs native review, BL-169: this proves plumbing, not Marathi quality), a
fake TTS tone, the in-process fake agent speaking the replies at 1900, 4900, 6900 and 8700 ms (20, 15, 25
and 10 frames), the fake speech-to-text of the scenario's words, and an agent export with one planted
substitution (the last word of reply 1). The timeline is derived by hand in the docstring of the file
(lines at 0, 2580, 5480 and 7680 ms; gaps 300, 280, 260 and 300; median 280; recording 8900 ms). Pinned:

* stdout (18 lines + 4), the four files and their modes (0700 directory, 0600 files), the call log (two
  lines at the fixed instant);
* the WAV: size 569644, equal to a recording built in the test by an independent writer, and its
  sha256 `3b79ce5d...3212`; the artifact in the store has the same digest and size;
* transcripts and turns (texts, roles, languages, `start_ms`/`end_ms` of the script turns), the
  `tracer.timing/1` document whole, the results in order with their values (1900, 280, 0, 4; MER 0.05,
  0, 0.0), the flags document (19 hits, 1 substitution, one span, confidence 0.2, not flagged), checked
  against an independent word count (20 tokens) and an independent edit alignment;
* the report with identifiers, instants, digests and directories masked (`mask` of `cli_p4f_support`):
  11327 bytes, sha256 `63019eb1...7b85`; the stored bundle as canonical JSON with the same masking,
  sha256 `f7b8d979...2f3`; identical on Python 3.11 to 3.14; and the same on a second run;
* consistency: stdout, the store, the report (read by an HTML parser) and the WAV (read with `wave`)
  agree on every timing value, interval, line of text, count, digest and size; the report is what the
  renderer makes of the stored run.

## 5. The T-DEG matrix of a simulated run (P4-29)

Ten cases of one configuration (two scripted lines, `pipecat-protobuf/1`, virtual clock, no socket).
Table of what each gives (timing results are `first audio, gap, overlap, answered`; agreement results
are `MER, flag count, flagged share`):

| Case | Exit | End reason | Timing | Agreement | Inputs held | Transcripts | Notes |
|---|---|---|---|---|---|---|---|
| `setup_failed` | 5 | `setup_failed` | 4x `not_applicable missing_input [audio, turn_timing]` | 3x `missing_input [transcript, agent_transcript]` | none | none | no recording, a report, usage none |
| `agent_silent` | 2 | `max_silence` | `error timing_edge_missing` x2, then scored 0 and 0 | 3x `missing_input [transcript, agent_transcript]` | audio, agent_audio, turn_timing | script | never 0 for an unmeasured edge |
| `comfort_noise` | 0 | `max_silence` | scored 0, 0, 280, 1 | 3x `missing_input [agent_transcript]` | + transcript | script, our_stt | activity is any non-zero sample (rule 10); one-body replay |
| `no_export` | 0 | `script_complete` | scored 400, 0, 60, 2 | 3x `missing_input [agent_transcript]` | audio, agent_audio, transcript, turn_timing | script, our_stt | |
| `export_present` | 0 | `script_complete` | scored 400, 0, 60, 2 | scored 0.0, 0, 0.0 (scorer version `+agent-partition.1`) | + agent_transcript | script, our_stt, agent_export | |
| `stt_failure` | 3 | `script_complete` | scored 400, 0, 60, 2 | 3x `missing_input [transcript]` | audio, agent_audio, agent_transcript, turn_timing | script, agent_export | `stt_failed` warning at `/stt` |
| `kill_switch` | 0 | `kill_switch` | scored 400, 300, 0, 1 | `missing_input [agent_transcript]` | full | script, our_stt | partial recording, 2 TTS + 1 STT usage kept, cache kept |
| `max_duration` | 0 | `max_duration` | scored 300, 20, 0, 1 | `missing_input [agent_transcript]` | full | script, our_stt | |
| `max_silence` | 0 | `max_silence` | scored 300, 20, 0, 1 | `missing_input [agent_transcript]` | full | script, our_stt | |
| `spend_cap` | 0 | `spend_cap` | scored 400, 300, 0, 1 | `missing_input [agent_transcript]` | full | script, our_stt | `cap_reached` `llm_tokens`; usage llm, tts, stt |

Invariants checked on all ten (`test_tdeg_sim_schema_identical_across_cases`): the bundle passes
`check_run_bundle`; the same seven result keys in the same order; the same metric definitions (every
field but the identifier and its digest); the same metadata members; the stored outcome, profile and
timing documents each valid against their JSON Schema; no `passed`, no boolean, no `failed`; a
`not_applicable` result has no value, `missing_input` and non-empty missing kinds; an `error` result
has no value and the code `timing_edge_missing` and only for the first two timing keys; a timing value
that is `null` in the stored timing document is not a number in its result; the record kinds a case
lacks are exactly those the case cannot have.

## 6. The repository guard and `.gitignore` (P4-30)

`scripts/guard_private_fixtures.py` (standard library only, as before) refuses, with the message
`<kind> tracked, staged or in history: <path>` (sorted by path, once each):

| Kind | By name | By content (staged blob, first 64 KiB) |
|---|---|---|
| `tracer simulated recording` | `recording-<YYYYMMDDTHHMMSSZ>.wav` in any directory | a `.wav` in a directory that also holds an output (a report, a store, a call log, a recovery file or a recording) |
| `tracer call log` | `sim_call_log.jsonl` (and the existing `sarvam_call_log.jsonl` rule) | the existing rule: a `.jsonl` whose first line has `"bytes_sent"` and `"audio_seconds"` (a simulated-run line has both keys) |
| `tracer TTS cache entry` | any path with a directory component `tts-cache` | none |
| `tracer usage recovery file` | `usage-recovery-<UTC>.json` | a JSON object whose `format` is `tracer.usage_recovery/1`, whatever the order of its members (a file longer than the head: its first member); prose and source that merely quote the string are NOT recovery files |
| `tracer target allowlist` | `allowlist`, `allowlist.txt` (any case), `*.allowlist` | every non-blank, non-comment line is an entry (`ws|wss|http|https://... [bot_holds_telephony_credentials=..|auth_token_env=..|origin=..]*`), at least one, and some entry has a field or a URL path ending in `/ws`, `/ws-client` or `/api/offer` |

A regression test runs the real script over the repository itself (`test_guard_has_no_false_positive_on_this_repository`:
the gate `findings`, and the name and content rules over every tracked or untracked-but-not-ignored
file): qa's first version of the recovery rule (a substring search) refused four files of the repository,
among them `docs/phase3/api-surface-batch-g.md`; that is how it was found. The judge batch (J) edits the
same script (`JUDGE_EXCHANGE`): both sets of rules are independent and the diffs merge.

Unchanged: private fixtures, `.env*`, reports, stores, the capture tool's own older call log, and
everything that is not an output (`tests/fixtures/**/*.wav`, `tools/capture/**`, source files named
`allowlist.py` or `ttscache.py`, prose that mentions an entry, a list of links). `.gitignore` lists
`recording-<UTC>.wav`, `sim_call_log.jsonl`, `tts-cache/`, `allowlist.txt` and `*.allowlist` and does not
hide any ordinary file (the negative cases name real tracked files).

## 7. The key canaries (P4-30; the telephony canary is deferred with the telephony stage, BL-311)

`test_key_canaries_never_in_any_output_channel` runs `tracer simulate` over the REAL Sarvam engines
(a scripted `MockTransport`) with the engine of ONE role hostile in one of five ways: an error that
echoes the key and a message of its own (401, 500), a body that is not JSON, a transport exception
whose text holds the key (connect error, read timeout); the other two roles answer normally. 15
cases (3 roles x 5 modes) plus 5 crash cases (an exception, a `RuntimeError` and a `KeyboardInterrupt`
whose message, arguments and attribute hold the key, the bearer token and the authorisation text,
raised while connecting, while storing the run, or while writing the report, after the TTS was
paid; a `KeyboardInterrupt` carries no text). The key canary, the bearer-token canary, the authorisation text and the
role's message canary are in none of: stdout, stderr, the exceptions the engines raised or handed back
(`str`, `repr`, `args`, `__dict__`, notes, the chain, the formatted traceback), the `repr` of the engines,
the seams and the world, the DEBUG log records of every logger, any file under `--out` (call log, store,
report, recording, recovery file, cache) or the cache file names (64 hex characters). Every engine
exception has no cause and no context. Not vacuous: the transport counts its hostile answers, the key is
seen in the provider's request header and nowhere else, and on a good run a text meant to be stored IS
found in the store and the report.

## 8. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only)

* **S-F** (`evalcore_phase4_adr_support.py`): 22 names leave the pending sets (`_P0026` 10, `_P0027` 8,
  `_P0029` 1, `_P0024A` 2, `_P0023A` 1) and `PENDING_OUTSIDE_RULES` becomes empty. Mostly line deletions;
  `_P0027` (and `_P0024A` without batch J) become `frozenset()` (one-line replacements of a block) and
  `PENDING_OUTSIDE_RULES` too. Batch J edits the same file: merge by applying each deletion by exact
  string; never replace the file.
* **S-F1** (`evalsim/tests/conftest.py`): registers `integration`, deselects it unless selected,
  installs `evalsim_integration_guard` for it, and hardens the unmarked guard with `create_server`,
  `create_datagram_endpoint` and `sendto`.
* **S-F2** (`evalcore/tests/test_evalcore_no_socket.py`): `GUARDED` gains `evalsim`.
* **S-F3** (`evalcore/tests/test_phase4_ci.py`): the "no job mentions the tool" assertion skips the job
  `interop`.
* **S-F4** (`evalsim/tests/test_evalsim_loopback_guard.py`): the `integration` marker may be used by the
  four interop modules and the test of its guard, and the inner run of the real conftest also copies the
  integration guard module (the conftest imports it).
* **S-F5** (`evalsim/tests/test_evalsim_scaffold.py`): `av` joins the evalsim import whitelist (lazy).
* **S-F6, S-F7, S-F8** (`evalcore/tests/test_adr0027_rules_are_tested.py`,
  `test_adr0024_phase4_amendment_is_tested.py`, `test_phase4_adrs_cite_only_existing_or_pending_tests.py`):
  the same kind of change as batch E's S-E5. Once the pending set of ADR 0027 (and, without batch J, of the
  ADR 0024 amendment) and the set of names cited outside the rules are empty, five tests that demanded a
  non-empty set fail. They are rewritten so that an empty set is valid and the refusals stay: a cited test
  that is not defined must be pending, a defined one on the set must leave it, an invented name is refused
  (the name used for "landed" is taken from the ADR's own citations, not from the set). With batch J merged
  the ADR 0024 set is not empty (`test_judge_key_canaries_never_in_any_output_channel`) and S-F7 still holds.

## 9. Oracle and evidence (under `docs/phase4/evidence/red/batch-f/`)

* `oracle-src.patch` (about 920 lines): the guard, `.gitignore`, the CI job, `SmallWebRtcDialect` and
  `open_webrtc`, and a STATIC-ONLY skeleton of `tools/samplebot` (the three pure modules are real and tested
  by value; `end_of_caller.py` and `scripted_bot.py` are written to satisfy the static rules and have NEVER
  been run against Pipecat; there is no `uv.lock`). `spec-change.diff` (the named changes, about 300 lines).
  Both were made against the working tree as it was at the last run and apply to it with `patch -p1` (dry
  run, no fuzz).
* `red.py3.11..14.txt` (current tree plus the 18 staged files, no spec change): 74 failed, 155 passed and 4
  collection errors (the four integration modules) on each Python; `red-reasons.py3.13.txt` (one line per
  failure); `red-tests-that-pass-on-the-current-tree.txt` (the 155, grouped, each group with its reason);
  `red-with-spec-change.py3.13.txt` (the spec changes applied, no oracle code: 68 failed, 161 passed, 11
  deselected).
* `oracle.py3.11..14.txt` (oracle code and spec changes applied): 229 passed, 11 deselected (the integration
  tests) on each Python; `flake.txt` (eight more runs: 229 passed each time).
* `full.py3.11..14.txt`: the whole workspace in the oracle state (see `counts.txt`).
* `mutants.txt`: 94 mutants (the guard, `.gitignore`, the CI job, the conftest and the integration guard, the
  dialect and `open_webrtc`, the sample bot, the scoring, timing and bundle code, the exit codes, the leak
  paths): 92 killed, 2 equivalent (analysed in the file).
* `integration-standin.txt`: the 11 interop tests against qa's stand-in bot (Python 3.13; 8 without the
  calibration on 3.11 and 3.14) and the calibration ranges. The stand-in is a 150-line script on the same
  command line and stdout protocol, built on aiortc and on the tracer dialects: it proves the test logic and
  the WebRTC library path, NOT Pipecat.
* `counts.txt`, `staging/tests/**` (the 18 files).

Not run: real Pipecat (no network, no tool lock), Postgres parity, Python 3.15, the lowest-direct legs,
hosted CI.

## 10. Ambiguities, with recommendations (the lead auto-approves)

A1. WebRTC media in this batch. Recommendation: section 3.4 (library path now, command wiring later with a
    BACKLOG row). Rejected: wiring the command now (needs profile, option and report decisions outside
    P4-26..30); a refusal-only placeholder test (BL-310 asks for proof).
A2. How the interop tests are kept out of normal runs. Recommendation: deselect by the conftest, select
    with `-m integration`, never skip (section 3.1). Rejected: a `-m` term in the root `addopts` (it breaks
    four Phase 3 tests that pin the string); an environment-variable skip gate (a missing variable would
    silently pass the job).
A3. `JITTER_MS = 50` and `WEBRTC_BOUND_MS = 500` are engineering bounds. Recommendation: keep; the first
    real run (P4-36) supplies the numbers, and a change is a named spec change. Rejected: a statistical
    rule (a percentile hides the outlier the bound exists to expose).
A4. The bot's end-of-caller timer reads ACTIVE frames (any non-zero byte), not every frame: WebRTC streams
    silence between lines, so a pure arrival timer never fires. Recommendation: keep (same definition as
    ADR 0026 rule 10); the bot is a fixture, Tracer itself is unchanged.
A5. The allowlist-file content rule and the "wav next to an output" rule (section 6) are qa's reading of
    "allowlist files" and "staged `*.wav` under any out dir". Recommendation: keep; tracked fixtures that
    are `.wav` files stay allowed. Rejected: refusing every `.wav` (the repository tracks seven).
A6. `llm_bot` is not built (O3 is open). Recommendation: BACKLOG row; the static test asserts its absence.
A7. FINDING on the current tree (batch E): a failure of the LLM provider in the middle of a call (401, 500,
    a transport error) ends the call with end reason `transport_error` and exit 0 (observed with the
    hostile-provider cases). ADR 0026 rule 9 has no end reason for a failed brain engine, and `transport_error`
    is a wire word. Recommendation: BACKLOG row P2, decide whether the end reason needs a new name (a schema
    change) or the exit code should be 2. Not pinned by any test here.
A8. `--stt fake` (bare) cannot serve a comfort-noise agent: the fake returns the words of the scenario's
    speech steps, a comfort step has none, so the post-call transcription fails (exit 3). The T-DEG case uses a
    one-body replay directory. Recommendation: keep.
A9. A comfort-noise agent gives first audio 0 and gap 0, measured by the definition of activity (rule 10): they
    are numbers about the definition, not about the agent. The summary and the report already say "observed at
    the simulator". Recommendation: keep; the measurement report must say so (P4-36).
A10. `av` joins the import whitelist (S-F5): `av.AudioFrame` is needed to hand audio to aiortc. Recommendation:
    keep (a transitive dependency of the optional extra, imported lazily). Rejected: a string import
    (it evades the scanner).
A11. The unmarked test guard now also refuses servers, datagram endpoints and `sendto` (S-F1). Recommendation:
    keep (no existing unmarked test used them: the oracle's whole workspace run is green).
A12. Real Pipecat behaviours that no document settles and that the tests therefore leave to the first run:
    the size of the frames Pipecat writes (the quantum), the data channel, whether `PATCH /api/offer` is
    needed, whether `FastAPIWebsocketTransport` hands each message on as one frame. The tests tolerate the
    first (the quantum is read from the timing document) and assert the others only through their effects.
A13. `uv.lock` for the tool cannot be made offline. Recommendation: the dev runs `uv lock --project
    tools/samplebot`; the existing batch 0b tests check the reviewed versions once it exists.
A14. Postgres parity, Python 3.15, the lowest-direct legs and the hosted run were not run. Recommendation: the
    lead runs the full legs before the gate (A15 of batch E still holds).
A15. ADR 0026 rule 23 says integration tests connect to `127.0.0.1`, `::1` or `localhost` only. WebRTC media
    between two processes of one machine runs over the machine's own addresses (BL-310), so the guard of
    section 3.1 is "TCP loopback only; datagrams only between addresses of this machine; no STUN or TURN".
    Recommendation: the architect amends rule 23 to say so (the guard is the stricter reading that can be
    enforced).
A16. Batch J (in flight in the working tree) edits `guard_private_fixtures.py`, `.gitignore`,
    `evalcore_phase4_adr_support.py` and `test_evalsim_loopback_guard.py` too, and names
    `test_guard_rejects_staged_judge_exchanges_labels_and_allowlist` (P4-J25) and
    `test_samplebot_golden_scenario_uses_no_model` (P4-J32). `spec-change.diff` and `oracle-src.patch` were
    made against the working tree as it was at the last run (they apply with `patch -p1` and no fuzz);
    qa also ran the 14 files in a copy of that tree with both patches applied (all pass). The allowlist rule
    of section 6 is the one J's "allowlist" test should extend, not replace; the sample bot of section 1 is
    the one P4-J32 extends with a golden scenario.

## 11. BACKLOG rows proposed (the lead files them)

1. Wire `smallwebrtc/1` into `tracer simulate` (sim-profile `webrtc_negotiated`, `--ice-server`, report
   wording); closes the `dialect_not_runnable` refusal of batch E A6 (P2; after the first green interop run).
2. `llm_bot` of P4-26 (b) after owner answer O3 (P2; owner-gated to run).
3. End reason and exit code of a failed LLM provider in the middle of a call (A7) (P2).
4. Calibration numbers of the first real interop run to the measurement report (P4-36) and the review of
   `JITTER_MS` and `WEBRTC_BOUND_MS` (P3).
5. Re-verify the three dialects and the sample bot at every Pipecat release (BL-282 already; the interop job
   is the alarm).
