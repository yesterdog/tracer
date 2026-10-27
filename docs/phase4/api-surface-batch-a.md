# Phase 4 batch A contract: the pure core (P4-04 call state machine, P4-05 turn-taking, P4-06 recorder and G.711, P4-07 timing)

Author: qa, 2026-10-27. Source: `docs/phase4/SCOPE.md` (rows P4-04 to P4-07, sections B, C, D, the amendments D-a to D-d and E-a/E-b, findings F5, F6, F10), ADRs 0026 (rules 2, 8 to 10, 15, 17, 24, invariants I3, I5, I6), 0027 (rules 1, 3, 4, 10, 11 and the `tracer.timing/1` format; the `turn_taking` part of `tracer.sim_profile/1`; the cross-field rules of `tracer.sim_outcome/1`), 0028 (rules 9 and 10), the batch 0b contract and the P4-03 modules (`simtypes`, `_engine_errors`) which this batch reuses and never duplicates, the Phase 3 pure-core modules and tests as models, and the lead's instructions of 2026-10-27. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE or earlier test except the named spec change S-A of section 9 (pending-set deletions). Every statement below is pinned by a test named in section 8, unless it says "not pinned".

## 0. Where the tests are, and how to move them

IMPORTANT (Phase 2 lesson, twice): a directory under `packages/` without a `pyproject.toml` breaks `uv run --locked` and `uv lock --check` for the whole workspace. So nothing was created under `packages/`. The tests are staged at

```text
docs/phase4/evidence/red/batch-a/staging/tests/evalcore/   -> packages/evalcore/tests/    (8 files)
(nothing is staged for any other package; no data file; no conftest.py; no __init__.py)
```

Seven of the eight staged files are new. One REPLACES an existing file: `evalcore_phase4_adr_support.py` (named spec change S-A, section 9: 26 deletions of 20 names from four pending sets, nothing else). Test module names are unique across all test directories. The tests find the repository root by walking up to `uv.lock`. All are ASCII, pass `ruff check`, `ruff format --check` and `mypy --strict` at their final path (they were formatted there in the oracle), open no socket and read no key, no private fixture and no clock. The new `tracer_evalcore` modules are imported lazily (`importlib`), so before they exist each test fails on its own with `ModuleNotFoundError` and the directory still collects.

The dev's first acts, in this order (the oracle's modules in section 11 are a working starting point, not a substitute for the dev's own):

```text
1. git mv docs/phase4/evidence/red/batch-a/staging/tests/evalcore/*.py packages/evalcore/tests/   (replaces evalcore_phase4_adr_support.py)
2. write the five modules of section 1 in packages/evalcore/src/tracer_evalcore/
3. run the commands below
```

Commands (after the move and the modules):

```text
everything:    uv run --locked --all-groups pytest
batch A:       uv run --locked --all-groups pytest packages/evalcore/tests/test_sim_callstate.py \
                 packages/evalcore/tests/test_sim_turntaking.py packages/evalcore/tests/test_sim_g711.py \
                 packages/evalcore/tests/test_sim_recorder.py packages/evalcore/tests/test_sim_timing.py \
                 packages/evalcore/tests/test_sim_core_conventions.py
ADR readers:   uv run --locked --all-groups pytest packages/evalcore/tests -k "adr0004 or adr0023_phase4 or adr0024_phase4 or adr0026 or adr0027 or adr0028 or adr0029 or phase4"
```

## 1. Modules and public names (all new, in `tracer-evalcore`, pure: stdlib, `tracer_evalschema` and `tracer_evalcore` only)

| Module | Task | Public names (the `__all__`, in the order ruff keeps: SCREAMING_CASE, CamelCase, then lower case) |
|---|---|---|
| `callstate` | P4-04 | `CALL_EVENTS`, `PRECONNECT_END_REASONS`, `REQUESTABLE_END_REASONS`, `TRANSITIONS`, `CallMachine`, `CallStateError`, `Transition`, `effective_max_duration_ms` |
| `turntaking` | P4-05 | `SPEAK_CAUSES`, `TURN_DECISION_KINDS`, `TurnDecision`, `TurnTaker`, `TurnTakingError`, `TurnTakingPolicy`, `agent_frame_is_active` |
| `g711` | P4-06 | `ALAW_DECODE_TABLE`, `MULAW_DECODE_TABLE`, `decode_alaw`, `decode_mulaw` |
| `recorder` | P4-06 | `MAX_RECORDING_SECONDS`, `RECORDER_CODECS`, `LogFrame`, `RecorderFormat`, `RecorderFrameError`, `Recording`, `build_recording`, `decode_to_pcm16` |
| `simtiming` | P4-07 | `TIMING_EDGE_MISSING`, `TIMING_FORMAT`, `TIMING_JSON_SCHEMA`, `TIMING_METADATA_KEY`, `TIMING_METRICS`, `CallerTurn`, `SelfTimes`, `TimingDocumentError`, `TimingValue`, `extract_timing`, `nearest_rank_median`, `timing_value`, `validate_timing_document` |

Names that are reused, never duplicated: `tracer_evalcore.simtypes` (`CALL_STATES`, `END_REASONS`, `DIALECTS`, `FRAME_MS == 20`), `tracer_evalcore._engine_errors.FactError` (the base of `RecorderFrameError`), `tracer_evalcore.errors.InputRefusedError` (the base of the other four errors). Each module has an `__all__` with exactly its public top-level names (`test_sim_core_modules_follow_package_conventions`); private helpers start with an underscore (`simtiming` may import `_check` from `recorder`; not pinned).

Rules common to the five modules (all pinned by `test_sim_core_conventions.py`):

* the two SPDX header lines, a module docstring, `from __future__ import annotations`, ASCII only;
* no import of `time`, `datetime`, `random`, `secrets`, `uuid`, `os`, `sys`, `signal`, `threading`, `multiprocessing`, `subprocess`, `asyncio`, `socket`, `ssl`, `select`, `selectors`, `tempfile`, `shutil`, `logging`, `pathlib`, `io`, `ctypes`, `inspect`, `importlib` (ADR 0026 rule 2: the core takes its clock and its randomness injected); and no use of a name or attribute that reads a clock or an entropy source (`monotonic`, `monotonic_ns`, `time_ns`, `perf_counter`, `perf_counter_ns`, `process_time`, `utcnow`, `urandom`, `getrandbits`, `token_bytes`, `token_hex`, `uuid4`, `uuid7`, `sleep`, `environ`, `getenv`, `.now()`, `.today()`, `__import__`, `import_module`). The existing guards also hold (`test_evalcore_runtime_imports_whitelist`, `test_evalcore_imports_no_network_module`, `test_evalcore_imports_no_asyncio_network`);
* every instant is an `int` number of NANOSECONDS read by the caller from its own injected monotonic clock (`clock_ns()` of the runner, P4-20/P4-21); no module reads a clock. `ms(n)` in the tests is `n * 1_000_000`. Section 10, ambiguity 2 explains why ns and not ms;
* every refusal is `tracer_evalcore.errors.InputRefusedError` (a `ValueError`), with `.code` the stable reason and `str(error) == error.code`, lower case words and underscores, never a value, a payload or a text (`RecorderFrameError` is the exception: it is a `FactError`, section 4.2);
* a call that raises changes nothing on the object it was called on (pinned for `CallMachine` and `TurnTaker`).

## 2. P4-04: the call state machine (`callstate`)

2.1 Closed lists. `CALL_EVENTS` (9, in this order): `connect_started`, `connected`, `connect_failed`, `caller_line_started`, `caller_line_finished`, `agent_active`, `agent_silent`, `end_requested`, `hangup_complete`. States and end reasons are `simtypes.CALL_STATES` (8) and `simtypes.END_REASONS` (11), unchanged. `REQUESTABLE_END_REASONS` = the 11 end reasons minus `setup_failed` (10, in `END_REASONS` order). `PRECONNECT_END_REASONS == ("kill_switch",)`.

2.2 The table. `TRANSITIONS` is a read-only `Mapping[(state, event), state | None]` with exactly 72 keys (`types.MappingProxyType`; item assignment and deletion raise `TypeError`). `None` is a refused cell: the event cannot happen in that state (a runner defect). The grid (one row per state, one cell per event in the order of `CALL_EVENTS`, `-` = refused):

```text
state             cs         cn        cf      ls              lf              aa              as              er      hc
preflight         connecting -         -       -               -               -               -               ending  -
connecting        -          connected ending  -               -               -               -               ending  -
connected         -          -         -       caller_speaking -               agent_speaking  connected       ending  -
caller_speaking   -          -         -       -               awaiting_agent  caller_speaking caller_speaking ending  -
awaiting_agent    -          -         -       caller_speaking -               agent_speaking  awaiting_agent  ending  -
agent_speaking    -          -         -       caller_speaking -               agent_speaking  awaiting_agent  ending  -
ending            ending     ending    ending  ending          ending          ending          ending          ending  ended
ended             ended      ended     ended   ended           ended           ended           ended           ended   ended
(cs connect_started, cn connected, cf connect_failed, ls caller_line_started, lf caller_line_finished,
 aa agent_active, as agent_silent, er end_requested, hc hangup_complete)
```

Reading: a barge-in is `caller_line_started` in `agent_speaking`; the agent talking over the caller is `agent_active` in `caller_speaking` (no state change); a late frame or a late line event while `ending` is ignored (no error); in `ended` every event is ignored; everything else not in the grid is refused.

2.3 `CallMachine()`: starts in `preflight` with `end_reason None` and `call_established False`. Properties `state`, `end_reason`, `call_established`. `apply(event, reason=None) -> Transition(source, event, target, changed)` (frozen dataclass; `changed` is `target != source`). Rules, checked in this order, each a `CallStateError` (code = message):

1. `event` not in `CALL_EVENTS`: `unknown_event`;
2. `event == "end_requested"` and `reason` not in `REQUESTABLE_END_REASONS` (this includes `setup_failed`, `None`, a non-str): `end_reason_invalid`; any other event with a `reason` that is not `None`: `reason_not_allowed`;
3. the cell is refused: `event_not_allowed_in_state`;
4. `end_requested` in `preflight` or `connecting` with a reason outside `PRECONNECT_END_REASONS`: `end_reason_not_allowed_before_connect` (so a call that was not established ends with `setup_failed` or `kill_switch`, the cross-field rule of ADR 0027 `tracer.sim_outcome/1`; a watchdog or any other limit that fires while connecting is reported by the runner as `connect_failed`).

On success: when the target is `ending` and the source is neither `ending` nor `ended`, `end_reason` becomes the request's reason (`setup_failed` for `connect_failed`); it never changes afterwards (exactly one end reason; the first wins). `call_established` becomes `True` on `connected` from `connecting` and never goes back. The state becomes the target. A refused or invalid call changes nothing. Properties pinned: no state is entered after `ended`; no event after `ended` changes state, reason or flag; `end_requested` with `kill_switch` then `hangup_complete` reaches `ended` from every state in at most two events; `setup_failed` implies `call_established False` and a not-established ended call has `setup_failed` or `kill_switch`.

2.4 `effective_max_duration_ms(evaluator_max_duration_ms: int, max_call_seconds: int | None) -> int`: the smaller of the two in milliseconds (`min(evaluator, cap * 1000)`); an absent cap (legal only with fake engines, ADR 0026 rule 15) gives the evaluator's value. `evaluator_max_duration_ms` is an `int` (not `bool`) in `1..14_400_000` (the schema's `MAX_DURATION_MS`), else `CallStateError("evaluator_max_duration_invalid")`; `max_call_seconds` an `int` (not `bool`) in `1..14_400` (the sim profile schema), else `CallStateError("max_call_seconds_invalid")`. Pure: the watchdog's grace (ADR 0026 rule 8) is P4-20's.

## 3. P4-05: turn-taking (`turntaking`)

3.1 `agent_frame_is_active(pcm: bytes) -> bool`: `True` iff the 16-bit little-endian frame holds any non-zero sample (no threshold, no model, no energy). `pcm` must be `bytes` (not `bytearray`), non-empty, of even length, else `TurnTakingError("frame_invalid")`. G.711 frames are decoded first (`recorder.decode_to_pcm16`): mu-law `0xFF` and `0x7F` decode to 0 (silent), `0xFE` to +8 (active); A-law has no zero (`0xD5` decodes to +8), so an A-law frame is always active (no dialect uses A-law in Phase 4; section 10, ambiguity 10).

3.2 `TurnTakingPolicy(caller_speaks_first: bool, wait_for_agent_silence_ms: int, max_silence_ms: int)`: a frozen dataclass, all three REQUIRED, no default, no factory, and no module of the package gives any of the three names a default or a `DEFAULT_*` constant (ADR 0026 rule 10; an AST scan of every module). Validation, codes as messages: `caller_speaks_first` a real `bool` (`caller_speaks_first_invalid`); the two integers `int` (not `bool`) in `1..9_007_199_254_740_991` (`wait_for_agent_silence_ms_invalid`, `max_silence_ms_invalid`); `max_silence_ms >= wait_for_agent_silence_ms` (`max_silence_below_wait`; equal is valid). `TurnTakingPolicy.from_profile(section)` reads the `turn_taking` object of `tracer.sim_profile/1`: a `Mapping` with exactly the three keys; a missing key is `<key>_missing`, an unknown key `turn_taking_unknown_key`, a non-mapping `turn_taking_not_an_object`, a bad value the policy's code (only the `<key>_missing` codes and the refusal of the last two are pinned; their exact codes are not).

3.3 `TurnTaker(policy)` (a non-policy is refused; the code `policy_invalid` is not pinned). One taker serves a whole call; each time the simulator waits for the agent is one WAIT. Pinned codes are `time_invalid`, `barge_in_on_first_line`, `brain_pending`, `no_speak_decision` and those of 3.2; for the other refusals below (`wait_open`, `no_wait`, `brain_already_pending`, `brain_not_pending`, `barge_in_after_ms_invalid`) the refusal is pinned and the code is not. Methods (instants in ns; `now_ns` must be an `int` >= 0 and not before the last instant the taker took, else `TurnTakingError("time_invalid")`; equal instants are fine):

* `begin_wait(now_ns, *, barge_in_after_ms: int | None = None) -> None`: opens a wait at `now_ns` (`wait_open` if one is open). `barge_in_after_ms` is an `int` (not `bool`) in `0..9_007_199_254_740_991` (`barge_in_after_ms_invalid`) and is refused on the very first wait of a policy with `caller_speaks_first` (`barge_in_on_first_line`: there is no onset to follow). It resets everything the previous wait knew (onset, last active frame, pending flag).
* `agent_frame(now_ns, pcm: bytes) -> bool`: returns the frame's activity (3.1); inside an open wait whose decision is not yet made, an active frame sets the onset (the first one) and the last active instant. A frame outside a wait changes nothing but the clock. A decision that is due at an instant at or before `now_ns` is made BEFORE the frame is considered (so a frame arriving exactly at the silence instant, or at the end instant, does not move the decision).
* `poll(now_ns) -> TurnDecision` (`no_wait` if no wait is open).
* `brain_started(now_ns)`, `brain_finished(now_ns)`: pair the LLM brain's pending state (ADR 0028 rule 9): `no_wait`, `brain_already_pending`, `brain_not_pending`.
* `may_send(now_ns) -> bool`: a query that changes nothing: `True` iff the decision at `now_ns` is `speak` and the brain is not pending.
* `line_started(now_ns) -> None`: the caller starts its line: needs `may_send`, else `TurnTakingError("brain_pending")` (when pending) or `TurnTakingError("no_speak_decision")`; closes the wait.

`TurnDecision(kind, at_ns, cause)` is a frozen dataclass: `TURN_DECISION_KINDS == ("wait", "speak", "end")`, `SPEAK_CAUSES == ("caller_first", "agent_silent", "barge_in")`. `wait` has `at_ns None` and `cause None`; `speak` has the instant the line is DUE and one of the causes; `end` has the end instant and `cause == "max_silence"` (an `END_REASONS` member). The decision, once made, is final for that wait whatever the poll times (`poll` at 60 s later gives the same record).

3.4 The rule of one wait that began at W (policy values W_ms = `wait_for_agent_silence_ms`, M_ms = `max_silence_ms`; E = W + M_ms is the END instant; frames at or after E are not considered):

* first wait and `caller_speaks_first`: `speak` at W, cause `caller_first`, whatever the agent does;
* with `barge_in_after_ms = b`: the ONSET is the first active frame with `W <= arrival < E` (a silent frame, a frame before W, a frame at or after E is not an onset); `speak` at `onset + b` (cause `barge_in`) if that is `<= E`, even if the agent stopped long before; there is no silence rule in this mode;
* otherwise: after an active frame at L with no active frame in `[L, L + W_ms)` (frames at L + W_ms exactly do not prevent it): `speak` at `L + W_ms` (cause `agent_silent`) if that is `<= E`;
* in every case, if no speak instant `<= E` exists, `end` at E (cause `max_silence`). A tie at E goes to `speak`. A dead agent (no frame, or only zero frames) and an agent that never goes silent (comfort noise, every frame active) both end at E: the call never hangs, whatever the agent sends (section 10, ambiguity 1 for the wording of ADR 0026 rule 10);
* the simulator never speaks while the brain is pending (`may_send`, `line_started`), even when the turn is due or the barge-in instant has passed; the decision itself is not delayed.

3.5 Determinism (pinned by a property test against `ref_turn_decision` of the support module): for any recorded frame sequence, any interleaving of polls gives the same final decision as feeding the frames alone.

## 4. P4-06: G.711 and the recorder (`g711`, `recorder`)

4.1 `g711`. `MULAW_DECODE_TABLE` and `ALAW_DECODE_TABLE`: tuples of 256 `int`, the conventional 16-bit scaling of the ITU-T G.711 decision values (mu-law: 14-bit value times 4; A-law: 13-bit value times 8, with the even-bit inversion `^ 0x55`). Anchors (pinned): mu-law `0xFF`, `0x7F` = 0, `0xFE` = 8, `0xFD` = 16, `0xFC` = 24, `0x7E` = -8, `0x80` = 32124, `0x00` = -32124, `0x81` = 31100; A-law `0xD5` = 8, `0x55` = -8, `0xD4` = 24, `0xD7` = 40, `0xC5` = 264, `0xAA` = 32256, `0x2A` = -32256. The whole tables are pinned by two independent derivations in the support module and by SHA-256 (`ad1a1412...a1308a` mu-law, `7c9db214...5e64` A-law, of `",".join(str(v) ...)`). PROVENANCE: the published ITU table was NOT fetched (no network in this task); the pin is "derived twice, pinned", not "verified against the ITU document" (section 10, ambiguity 9). `decode_mulaw(data)` and `decode_alaw(data)` take `bytes` or `bytearray` (anything else is a `TypeError`) and return 16-bit little-endian PCM, two bytes per input byte, byte by byte (no state).

4.2 `recorder`. `RECORDER_CODECS == ("pcm_s16le", "mulaw", "alaw")`; `MAX_RECORDING_SECONDS == 14_400` (the Evaluator's four hours; read from the module attribute at call time so that the boundary is testable). `RecorderFormat(sample_rate_hz, codec)`: frozen, rate an `int` (not `bool`) in `8000..48000`, codec in `RECORDER_CODECS`, else `ValueError` whose message starts with the field name (`sample_rate_hz: ...`, `codec: ...`). `LogFrame(t_ns, payload, sample_rate_hz, channels, codec)`: frozen, no default; `t_ns` an `int` (not `bool`) >= 0, `payload` `bytes`, rate and channels positive `int`, codec in `RECORDER_CODECS`, else `ValueError` naming the field. `decode_to_pcm16(codec, payload)`: PCM as is (odd length is `ValueError("payload: ...")`), `mulaw`/`alaw` through the tables, any other codec `ValueError("codec: ...")`.

4.3 `build_recording(fmt, origin_ns, caller, agent) -> Recording` (`origin_ns` an `int` >= 0 else `ValueError("origin_ns: ...")`; `caller` and `agent` are sequences of `LogFrame`, the two frame logs, instants ABSOLUTE ns of the same clock). Placement, all in samples of the dialect rate: a frame's arrival or send sample is `floor((t_ns - origin_ns) * rate / 1_000_000_000)` in exact integer arithmetic. CALLER frame: placed at its send sample exactly (channel 0 is what was sent, decoded when the wire is G.711). If two caller frames overlap on the timeline the later one overwrites the shared samples and `caller_overlap_samples` counts them (lateness is visible, never hidden by a burst; section 10, ambiguity 7). AGENT frame, in log order: placed at `max(arrival sample, end of the previous agent frame)` (the playback model: a burst plays back to back; a frame arriving after the previous one ended is placed at its arrival; silence between is zeros). The WAV has `n_samples = max(end of channel 0, end of channel 1)`; both channels are zero-padded to it. Output: `Recording(wav, sample_rate_hz, n_samples, caller_start_samples, agent_start_samples, caller_overlap_samples)` (frozen; the two start tuples hold the placement sample of each frame in log order). The WAV is the canonical 44-byte-header PCM file: `RIFF`, `36 + 4n`, `WAVE`, `fmt ` 16, tag 1, 2 channels, rate, byte rate `4 * rate`, block align 4, 16 bits, `data`, `4n`, then `n` pairs of little-endian int16, channel 0 (caller) left, channel 1 (agent) right. Empty logs give a valid 44-byte file with 0 samples. The same logs give the same bytes (a golden SHA-256 is pinned for one log), and moving every instant and the origin together changes nothing.

4.4 Refusals, each a `RecorderFrameError(FactError, ValueError)` with `index` (position in its log), `kind` and `reason` (`"caller"` or `"agent"`), message `RecorderFrameError: request <index>, transport <kind>, reason <log>` and nothing else (no payload, no sample, no instant). Kinds, checked per frame in this order: `before_origin` (`t_ns < origin_ns`), `order` (an instant earlier than the previous frame's), `sample_rate` (frame rate != format rate: NEVER resampled), `channels` (!= 1), `codec` (!= format codec), `payload_size` (empty, or an odd number of bytes for `pcm_s16le`), `too_long` (the frame would end after `MAX_RECORDING_SECONDS * rate` samples; checked before any buffer is allocated). Equal instants are valid. Nothing in the module can resample (no function or class name with `resampl` or `interpol`). A refusal returns no recording.

## 5. P4-07: timing extraction (`simtiming`)

5.1 `extract_timing(*, dialect, fmt, wait_for_agent_silence_ms, connect_started_ns, connect_ended_ns, established, observed_until_ns, caller_turns, agent_frames) -> dict` builds the `tracer.timing/1` document of ADR 0027 and validates it before returning. `dialect` in `simtypes.DIALECTS` (else `TimingDocumentError("dialect_invalid")`); `fmt` a `RecorderFormat`; `wait_for_agent_silence_ms` the policy's value (an `int` in `1..2**53-1`; it separates runs of agent activity for the barge-in stop); `connect_started_ns`/`connect_ended_ns` the instants the attempt started and completed or failed (`None` when not measured; an end without a start is refused; reversed instants are refused); `established` a real `bool` (the call was connected; `connect_ended_ns` is then the ORIGIN, the instant of sample 0); `observed_until_ns` the instant the observation ended (not before the last frame or the last turn edge); `caller_turns` a sequence of `CallerTurn(turn_seq, first_frame_ns, last_frame_ns, barge_in_after_ms, self_times)` (sent instants of the first and last frame of each line, in send order, no overlap with the previous turn's edge, `turn_seq` strictly increasing; that each names a Turn of the run's `script` transcript is the bundle builder's, not pinned here; `SelfTimes(reply_ms, wait_ms, stt_ms, brain_ms, tts_ms)`, each a non-negative `int` or `None`); `agent_frames` a sequence of `LogFrame` (the agent frame log, arrival instants). A call that was not established must have no turn and no frame (`frames_without_call`). Frames are checked like the recorder's (4.4) against the origin, in the agent role.

5.2 The values (all `int` or `None`, never `float`; `ms` = floor of the difference in ns divided by 1,000,000; an agent frame is ACTIVE when its decoded samples hold any non-zero one, 3.1, and is dated by its ARRIVAL instant, not its playback position):

* `setup_ms` = `connect_ended - connect_started`, `None` when either is `None` (a failed attempt with both instants has a value);
* `first_agent_audio_ms` = first active frame arrival minus the origin (`None` without an active frame or a connection; a frame at the origin is 0, a measured value);
* per turn, `gap_ms` = the arrival of the first active frame at or after the turn's EDGE (`last_frame_ns + 20 ms`) minus the edge (`None` if there is none; not bounded by the next turn); `overlap_ms` = the sum of the durations of the active frames whose arrival is in `[first_frame_ns, edge)`, floored once per turn (a frame's duration is `samples * 10**9 // rate` ns); `barge_in_after_ms` copied; `barge_in_stop_ms` (only for a turn with `barge_in_after_ms`): let `t0` be the turn's first frame instant and a RUN a maximal chain of active frames whose consecutive arrivals differ by less than `wait_for_agent_silence_ms`; the value is the end (arrival + duration) of the last frame of the run that holds the last active frame arriving at or before `t0`, minus `t0`, floored, and `None` if no active frame arrived at or before `t0`, or the last such frame is `wait_for_agent_silence_ms` or more before `t0` (the agent was already silent), or the run's last frame arrived less than `wait_for_agent_silence_ms` before `observed_until_ns` (the run may not have ended), or the difference is not positive (nothing was interrupted); `self` = the turn's `SelfTimes` verbatim;
* `overlap_ms` (top) = the sum of the turns' (`None` iff the call was not established; an established call with a silent agent has 0, a measured value); `answered_caller_turns` = `gap_n` = the number of turns with a `gap_ms`; `gap_median_ms` = the nearest-rank median of those gaps (`None` iff `gap_n == 0`); `barge_in_n` and `barge_in_stop_median_ms` likewise over the non-null stops;
* `quantum_ms` = `max(20, ceil(longest agent frame received, silent ones included, in ms))`, exact arithmetic (a call without agent frames, or not established, has 20); `caller_frame_ms` = 20; `clock` = `"simulator_monotonic"`; `dialect` as given; `format` = `"tracer.timing/1"`.

The simulator's own times (`self`) are never inside a gap: a gap ends at the agent's first sample, whatever the simulator does afterwards (pinned with calls whose simulator is 5 s slower and whose `self` values are astronomical).

5.3 The document: exactly the keys and bounds of the ADR 0027 block (14 required top-level keys, closed objects, every integer in `0..2**53-1`, `quantum_ms >= 20`). `TIMING_JSON_SCHEMA` equals the ADR's block (`test_shipped_timing_schema_equals_adr0027_block`; the two other documents' schemas are shipped by the tasks that build them, section 10, ambiguity 5). `validate_timing_document(document)` is written by hand over that schema (`jsonschema` is not a runtime dependency): an `int` is a Python `int` (so `True` and `1.5` are refused; the `9.0` case, which JSON Schema accepts, is not pinned), and the cross-field rules of the ADR are refusals with a stable code: keys exactly the schema's; turns sorted by `turn_seq` with no repeat; `answered_caller_turns == gap_n ==` the count of non-null `gap_ms`; `gap_median_ms` is `None` iff `gap_n == 0` and otherwise the nearest-rank median; `barge_in_n` and `barge_in_stop_median_ms` likewise; `barge_in_stop_ms` is `None` whenever `barge_in_after_ms` is; `overlap_ms` is the sum of the turns' `overlap_ms`, or `None` (and it may be `None` only when the document has no turn). A violation raises `TimingDocumentError` (`.code` = message; the codes themselves are not pinned, only that the message is a lower-case code with no value). `TIMING_METADATA_KEY == "tracer.timing"`, `TIMING_FORMAT == "tracer.timing/1"`.

5.4 `nearest_rank_median(values) -> int | None`: the value at rank `ceil(n / 2)` of the sorted values (1-based), `None` for none. `timing_value(document, metric) -> TimingValue(value, error_code, message)` with `TIMING_METRICS` mapping the metric key to its document field: `sim_first_agent_audio_ms` to `first_agent_audio_ms`, `sim_agent_response_gap_median_ms` to `gap_median_ms` (N = `gap_n`), `sim_overlap_ms` to `overlap_ms`, `sim_barge_in_stop_ms` to `barge_in_stop_median_ms` (N = `barge_in_n`), `sim_answered_caller_turns` to `answered_caller_turns` (a count, never missing). A present value is `TimingValue(value, None, None)`. An absent one is `TimingValue(None, "timing_edge_missing", message)` (SCOPE D-a, BL-292: the result is an `error`, never 0, never false, never a valid `not_applicable`; P4-22 turns it into the `MetricResult`), with the messages EXACTLY:

```text
sim_first_agent_audio_ms:          timing_edge_missing: first_agent_audio_ms has no value; missing edge: first active agent frame; caller turns <T>
sim_agent_response_gap_median_ms:  timing_edge_missing: gap_median_ms has no value; missing edge: active agent frame after a caller turn; caller turns <T>, gap_n <N>
sim_overlap_ms:                    timing_edge_missing: overlap_ms has no value; missing edge: established call; caller turns <T>
sim_barge_in_stop_ms:              timing_edge_missing: barge_in_stop_median_ms has no value; missing edge: end of the interrupted agent activity; caller turns <T>, barge_in_n <N>
```

(`<T>` the number of turns in the document.) An unknown metric key is `TimingDocumentError("metric_unknown")`. `TIMING_EDGE_MISSING == "timing_edge_missing"`.

## 6. Clock and randomness injection points (ADR 0026 rule 2)

| Who reads time | Where | What the core receives |
|---|---|---|
| the runner (P4-20/P4-21), once per event | `clock_ns()` injected (`time.monotonic_ns` in production, a fake in tests) | an `int` ns per frame sent, frame received, connect start/end, call end, brain/STT/TTS step |
| nobody | `CallMachine`, `TurnTaker`, `build_recording`, `extract_timing` | they compute from arguments only; the AST scan forbids every clock and entropy source |

Randomness: none in this batch (no jitter, no sampling, no ids). Wall-clock `started_at` of the Run is the runner's (ADR 0027 rule 2). Nothing here sleeps.

## 7. Documents (ADR 0027)

* `tracer.timing/1`: shipped by P4-07 (section 5); the document has exactly the ADR's keys; a pre-existing document test (`test_adr0027_documents_match_json_schemas`) is unchanged and the comparison with the shipped schema is the NEW test `test_shipped_timing_schema_equals_adr0027_block` (an addition, not a weakening).
* `tracer.sim_profile/1`, `tracer.sim_outcome/1`: NOT shipped in batch A (section 10, ambiguity 5). Batch A pins the parts they hand to it: `TurnTakingPolicy.from_profile` reads the `turn_taking` object of the sim profile; the state machine's end reasons and the rule that a not-established call ends with `setup_failed` or `kill_switch` are the cross-field rules of the outcome document.

## 8. Test inventory

8.1 SCOPE names (EXACTLY these 20, each defined once; `test_batch_a_defines_exactly_the_scope_names_once` reads the SCOPE rows and the definitions):

| Task | Name | File |
|---|---|---|
| P4-04 | `test_state_machine_transition_table_exact` | `test_sim_callstate.py` |
| P4-04 | `test_max_duration_is_min_of_evaluator_and_cap` | `test_sim_callstate.py` |
| P4-04 | `test_kill_switch_reaches_ended_from_every_state` | `test_sim_callstate.py` |
| P4-04 | `test_no_event_after_ended` | `test_sim_callstate.py` |
| P4-04 | `test_setup_failure_is_an_end_reason_not_an_exception` | `test_sim_callstate.py` |
| P4-05 | `test_agent_activity_is_any_nonzero_sample_no_threshold` | `test_sim_turntaking.py` |
| P4-05 | `test_wait_for_silence_parameter_has_no_default` | `test_sim_turntaking.py` |
| P4-05 | `test_barge_in_fires_at_offset_from_agent_onset` | `test_sim_turntaking.py` |
| P4-05 | `test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang` | `test_sim_turntaking.py` |
| P4-05 | `test_simulator_never_speaks_while_brain_pending` | `test_sim_turntaking.py` |
| P4-06 | `test_recorder_is_deterministic_from_frame_log` | `test_sim_recorder.py` |
| P4-06 | `test_recorder_channel_0_is_exactly_what_was_sent` | `test_sim_recorder.py` |
| P4-06 | `test_recorder_burst_arrival_uses_playback_model` | `test_sim_recorder.py` |
| P4-06 | `test_g711_mulaw_alaw_decode_matches_itu_table` | `test_sim_g711.py` |
| P4-06 | `test_recorder_refuses_rate_mismatch_never_resamples` | `test_sim_recorder.py` |
| P4-07 | `test_timing_first_agent_audio_exact_on_synthetic_log` | `test_sim_timing.py` |
| P4-07 | `test_timing_gap_measured_from_last_sent_frame` | `test_sim_timing.py` |
| P4-07 | `test_simulator_self_latency_excluded_from_agent_gap` | `test_sim_timing.py` |
| P4-07 | `test_timing_values_carry_quantum_and_dialect` | `test_sim_timing.py` |
| P4-07 | `test_timing_value_without_both_edges_is_error_never_zero` (renamed 2026-10-27, D-a) | `test_sim_timing.py` |

8.2 Added by qa (not in the SCOPE table; each has its reason in its docstring): 

47 extra tests, 67 in all (parametrised cases: none, every test is one item), grouped by file:

* `test_sim_callstate.py` (15 tests, 10 extra):
  * `test_max_duration_property_is_the_smaller_of_the_two`: The result never exceeds either limit and equals one of them (min, not sum or product).
  * `test_kill_switch_reaches_ended_after_any_legal_prefix`: Whatever legal events came first, a kill switch then a hang-up ends the call in at most two events, with one end reason, and the established flag is untouched by them.
  * `test_no_state_is_entered_after_ended`: After the first ``ended`` every later event leaves state, reason and flag as they were.
  * `test_every_end_reason_is_reachable_with_its_own_value`: The eleven end reasons of ADR 0026 rule 9 are all reachable, each recorded as itself (a table that merged two reasons, or lost one, would pass the grid test alone).
  * `test_the_first_end_reason_wins`: Exactly one end reason: later requests (a watchdog after a signal) change nothing.
  * `test_late_events_while_ending_are_ignored_but_a_refused_event_changes_nothing`: Frames and line events still arrive while the call hangs up: ignored, not errors. An event that cannot happen (a runner defect) raises and leaves the machine exactly as it was.
  * `test_established_only_after_connected_and_only_once_connecting`: ``call_established`` is the fact the outcome document records: true from ``connected`` on, never for a call that failed to connect, never turned back to false.
  * `test_arguments_are_validated_and_only_the_machine_error_is_raised`: Junk in, ``CallStateError`` out: never a ``KeyError`` or a ``TypeError`` from the table.
  * `test_machine_follows_the_reference_model_on_random_event_sequences`: Model-based: the machine and a reference (the grid plus the stated reason rules) agree on state, end reason, established flag and refusals after every event; and at the end the invariants of ADR 0026 rule 9 and ADR 0027 (outcome cross-field rules) hold.
  * `test_the_table_text_in_the_grid_has_one_row_per_state_and_nine_cells`: Guards the test's own oracle: a typo in the grid would make two wrong things agree.
* `test_sim_turntaking.py` (17 tests, 12 extra):
  * `test_activity_equals_any_nonzero_sample_for_random_frames`: The definition itself, compared with a direct decoding of the samples (mostly zeros, so the one non-zero sample case is generated often).
  * `test_policy_values_are_validated`: Each policy value is refused with its own code when it is not what the profile schema says (type, range, the cross-field rule max >= wait), at the boundaries, and the minimum and the maximum are accepted: a loose check would let a bad owner value reach a real call.
  * `test_barge_in_instant_is_onset_plus_offset_or_the_end`: For any onset, offset and agent run length: speak at onset + offset when that is within the wait budget (inclusive), else end at the budget; the agent's later frames never move it.
  * `test_a_line_never_starts_while_the_brain_is_pending`: Property: over any interleaving of frames, polls and brain calls, ``line_started`` succeeds only when the brain is not pending and a speak decision is due; and ``may_send`` is never true while the brain is pending.
  * `test_silence_rule_speaks_wait_ms_after_the_last_active_frame`: The silence rule of ADR 0026 rule 10 at its boundaries, to the nanosecond: silent once no active frame has arrived for ``wait_for_agent_silence_ms`` (inclusive).
  * `test_the_first_line_of_a_caller_that_speaks_first_does_not_wait`: ``caller_speaks_first`` (ADR 0026 Ambiguity 2): the first line is due at once, later lines wait for the agent as usual; with the flag off the first line waits for the greeting.
  * `test_state_of_one_wait_does_not_leak_into_the_next`: A new wait forgets the onset, the last active frame, the barge-in and the brain flag of the previous one.
  * `test_the_onset_of_one_wait_is_not_the_onset_of_the_next`: Each wait finds its own onset: a barge-in of the second wait follows the second onset, not the first wait's (a stale onset would make the line due at once).
  * `test_time_never_goes_backwards_and_inputs_are_checked`: The taker reads no clock: its instants come from the runner, so an instant that goes backwards or is not an integer is a runner defect and is refused, not smoothed over.
  * `test_a_refused_call_changes_nothing`: The runner drives the taker in a loop: a refused call (an event that should not happen) must not move its clock, latch a decision or flip a flag, or the refusal itself would change the call (the contract: a call that raises changes nothing).
  * `test_decision_matches_the_reference_on_random_recorded_frame_sequences`: Deterministic on recorded frame sequences: whatever the polls in between, the final decision equals the reference over the same frames, and equals what a second taker, fed the same frames without any intermediate poll, decides.
  * `test_decisions_are_plain_values`: The decision record: fixed kinds and causes, frozen, hashable, equal by value.
* `test_sim_g711.py` (3 tests, 2 extra):
  * `test_g711_tables_have_the_structure_of_the_standard`: Properties of the standard that hold for a correct table whatever its derivation: the sign bit is the top bit and flips the sign; there are eight segments of sixteen steps, the step doubling from segment to segment; mu-law has two zeros and A-law none; everything fits in 16 bits and the full scales are 32124 and 32256.
  * `test_g711_decode_is_bytewise_and_length_doubling`: A frame decodes byte by byte (no state between bytes, no resampling, no smoothing).
* `test_sim_recorder.py` (13 tests, 9 extra):
  * `test_recorder_matches_the_reference_for_random_frame_logs`: The whole recording (both channels, every start sample, the overlap count) equals a reference built from lists, for sorted random logs at several rates; and is deterministic.
  * `test_agent_frames_play_back_to_back_and_never_overlap`: Property: each agent start is the later of the arrival sample and the previous end, starts never decrease, no two frames share a sample, and the frames' samples all survive.
  * `test_a_frame_is_accepted_iff_its_rate_is_the_recorders`: Property: equal rates record, any other pair is refused; there is no tolerance band.
  * `test_wav_layout_is_the_canonical_pcm_header`: The artifact is a canonical 16-bit stereo PCM WAV (ADR 0027 rule 3: container wav, codec pcm_s16le, 2 channels, 16 bits, the dialect rate): the bytes any WAV reader expects.
  * `test_empty_logs_give_a_valid_empty_recording`: An established call may end with nothing sent and nothing heard: the recording is a valid WAV with zero samples (not an error after a paid call, not an invented sample).
  * `test_caller_frames_that_overlap_in_time_are_counted_not_hidden`: A late caller frame can overlap the next one on the timeline (lateness is visible, never hidden by a burst, ADR 0026 rule 8). The later frame wins the shared samples and the overlap is reported, so a reader can tell the channel is not exact there.
  * `test_frames_must_be_in_order_inside_the_timeline_and_non_empty`: Refusals that keep a corrupt log from producing a plausible recording: instants go forward, no frame precedes the origin, a PCM payload is whole samples, none is empty, and the timeline is bounded (the Evaluator's four hours) before any memory is allocated.
  * `test_frame_and_format_records_validate_their_fields`: The records the recorder is built from refuse a field that is not what the contract says (each ``ValueError`` names the field), so a wrong value cannot travel into a WAV.
  * `test_silent_and_active_helpers_are_what_they_say`: Guards the helpers the other tests build frames with (a silent frame that carried a sample would make two wrong things agree).
* `test_sim_timing.py` (14 tests, 9 extra):
  * `test_first_agent_audio_and_setup_are_floors_of_differences`: Property: both values are ``floor(difference / 1 ms)`` of two instants, whatever the absolute clock reading (a monotonic clock has an arbitrary zero).
  * `test_quantum_is_the_larger_of_the_caller_frame_and_the_longest_agent_frame`: Property: ``quantum_ms = max(20, ceil(longest agent frame in ms))`` in exact arithmetic, over frames of any size and the rates a dialect can use (silent frames count: the quantum is about what was received, not about what was audible).
  * `test_overlap_is_the_duration_of_active_agent_frames_arriving_while_the_caller_speaks`: ADR 0027 rule 10: per turn, the summed duration of active agent frames that arrive while a caller line is being sent (from its first frame to its edge, the edge excluded); the total is the sum of the turns; silent frames, frames before the line and frames at the edge count for nothing; the sum is floored once per turn.
  * `test_barge_in_stop_is_the_end_of_the_interrupted_activity`: ADR 0027 rule 10: from the send instant of the turn's first frame to the end of the last active agent frame of the activity it interrupted. A run of activity ends at a pause of ``wait_for_agent_silence_ms``; nothing interrupted, or an unfinished run, gives no value.
  * `test_nearest_rank_median_helper`: The nearest-rank median of ADR 0027 (the value at rank ceil(n / 2) of the sorted values), exhaustively for small lists; no mean, no interpolation, ``None`` for no value.
  * `test_nearest_rank_median_is_a_member_with_half_at_or_below`: Property: the median is one of the values, at least half are at or below it and fewer than half are below it (the definition of the nearest-rank median, with no interpolation).
  * `test_turn_and_call_inputs_are_checked_not_repaired`: Inputs that cannot be a real call are refused: turns out of order or overlapping, frames before the origin or unordered, frames of a call that was not established, and bad instants.
  * `test_extraction_matches_the_reference_on_random_calls`: Property: for random established calls the whole document equals the reference's, and it validates (structure and cross-field rules). Missing edges are None, never 0: a document with no active agent frame has no first-audio, no gap and no barge-in stop.
  * `test_the_validator_accepts_what_the_extractor_builds_and_refuses_each_fault`: Every structural fault is refused by the validator and by the JSON Schema; every cross-field fault by the validator (the schema alone cannot state it). The refusal message is the stable code and never a value.
* `test_sim_core_conventions.py` (5 tests, 5 extra):
  * `test_sim_core_modules_import_no_clock_or_randomness`: ADR 0026 rule 2: the five modules compute from their arguments. No clock, no entropy, no environment, no thread, no file, no socket, by import or by name; and the scan has teeth.
  * `test_sim_core_modules_follow_package_conventions`: The package's file conventions for the new modules: SPDX header, docstring, ASCII, the future import, and an ``__all__`` in ruff's order that lists exactly the public names.
  * `test_shipped_timing_schema_equals_adr0027_block`: ADR 0027: ``tracer-evalcore`` ships the same schema as the ADR's block and the documents test compares the two. This is that comparison for ``tracer.timing/1``, shipped by P4-07 (the two other documents are shipped by the tasks that build them; the docstring's "contract section 6" is the batch 0 contract, this contract's section 7 and ambiguity 5).
  * `test_batch_a_defines_exactly_the_scope_names_once`: SCOPE rows P4-04 to P4-07 name 20 tests; each must exist under its exact name, once (the contract of this batch and the SCOPE table must not drift apart).
  * `test_batch_a_names_left_the_pending_sets`: The four tasks' names are defined now, so no ADR pending set may still hold one (the reader of batch 0 refuses a name that is both defined and pending; this says it by name).


## 9. Named spec change S-A (tightening nothing, weakening nothing: the pending sets shrink)

```text
S-A  packages/evalcore/tests/evalcore_phase4_adr_support.py : the pending sets, deletions only (26 lines, 20 names)
     before: the sets list the 20 names as "cited by an ADR, defined by no test yet"
     after:  the names are defined (section 8.1), so the sets no longer hold them:
       PENDING["0026"] (11): test_kill_switch_reaches_ended_from_every_state, test_max_duration_is_min_of_evaluator_and_cap,
         test_no_event_after_ended, test_setup_failure_is_an_end_reason_not_an_exception, test_state_machine_transition_table_exact,
         test_agent_activity_is_any_nonzero_sample_no_threshold, test_barge_in_fires_at_offset_from_agent_onset,
         test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang, test_simulator_never_speaks_while_brain_pending,
         test_wait_for_silence_parameter_has_no_default, test_recorder_is_deterministic_from_frame_log
       PENDING["0027"] (11): test_wait_for_silence_parameter_has_no_default, test_g711_mulaw_alaw_decode_matches_itu_table,
         test_recorder_burst_arrival_uses_playback_model, test_recorder_channel_0_is_exactly_what_was_sent,
         test_recorder_is_deterministic_from_frame_log, test_recorder_refuses_rate_mismatch_never_resamples,
         test_simulator_self_latency_excluded_from_agent_gap, test_timing_first_agent_audio_exact_on_synthetic_log,
         test_timing_gap_measured_from_last_sent_frame, test_timing_value_without_both_edges_is_error_never_zero,
         test_timing_values_carry_quantum_and_dialect
       PENDING["0028"] (2): test_simulator_never_speaks_while_brain_pending, test_simulator_self_latency_excluded_from_agent_gap
       PENDING["0024A"] (2): test_g711_mulaw_alaw_decode_matches_itu_table, test_recorder_refuses_rate_mismatch_never_resamples
```

New sizes: 0026 57 to 46, 0027 41 to 30, 0028 18 to 16, 0024A 25 to 23; 0029, 0023A, 0004 and the outside-rules set are unchanged. The staged copy of the file has the deletions applied already and nothing else (`spec-change.diff` shows them); a set entry that is also defined by a test is a failure of `check_adr`, so the move and the deletions are one commit. No test was skipped, deleted or weakened.

## 10. Ambiguities, with recommendations (the lead auto-approves)

1. **What `max_silence_ms` measures (ADR 0026 rule 10 against its own comfort-noise sentence and the SCOPE test name).** Rule 10 says the call ends with `max_silence` "once no active frame has arrived for `max_silence_ms` while the simulator is waiting for the agent". Read literally, an agent that streams comfort noise (every frame active) never reaches that condition, yet the same rule and Q6 say such a call "ends on `max_silence`", and the SCOPE names the test `test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang`. **Recommendation (applied in the tests):** `max_silence_ms` is the wait budget: the call ends with `max_silence` once the simulator has waited `max_silence_ms` since its wait began without being able to take its turn (the agent said nothing, or never paused for `wait_for_agent_silence_ms`). Every wait is then bounded by construction; no second parameter is needed. Amend the ADR 0026 rule 10 wording to that. Consequence the owner should know (O4): a reply that starts, or a pause-free monologue that runs, longer than `max_silence_ms` after the wait began ends the call with `max_silence`, so the owner's value must exceed the longest reply expected. Rejected: the literal reading (comfort noise never ends; only `max_duration` bounds it, contradicting the rule's own sentence); a second parameter such as `max_agent_turn_ms` (a new required key, the schema of ADR 0027 is frozen); a threshold on the noise (rule 10 forbids it).
2. **Instants are integer NANOSECONDS, not milliseconds.** ADR 0027 rule 10 defines each value as the difference of two instants of the monotonic clock "floored to the millisecond"; a floor of a difference is not the difference of floors, so the instants need a finer unit than the result. **Recommendation:** every instant crossing the core is an `int` of ns (`time.monotonic_ns()` in the runner, a fake in tests); every document value stays in ms. Rejected: ms integers (the floor of the difference could be off by one), floats (non-deterministic comparisons and forbidden in the documents).
3. **The events and the transition table are this contract's (ADR 0026 rule 9 says the batch A contract pins them).** Choices to confirm: (a) nine events, and a two-step end (`end_requested` or `connect_failed` to `ending`, `hangup_complete` to `ended`), so the hang-up has a state of its own; (b) `connect_failed` is the only way to `setup_failed`, and before the connection exists only `kill_switch` can end the call, which is exactly the cross-field rule of `tracer.sim_outcome/1`; the runner must therefore report any limit that fires while connecting (a connect timeout, a watchdog) as `connect_failed`; (c) late events while `ending` and every event after `ended` are ignored, an event that cannot happen in a live state is a `CallStateError` (a runner defect, not an outcome). Rejected: a direct jump to `ended` (no state for the hang-up, the watchdog and the signals could not be told apart from a finished call); ignoring every impossible event (hides runner defects); accepting any end reason before the connection (would contradict the outcome document's cross-field rule).
4. **The gap is not bounded by the next turn.** ADR 0027 rule 10 says `gap_ms` ends at "the first active agent frame arriving at or after that edge"; the tests follow it literally. A turn the agent never answered, followed by another turn (possible only through a caller-first line or a barge-in), would take the answer to the later turn as its own. **Recommendation:** keep the ADR's wording for batch A; if the owner prefers the bound, it is one line in the extractor and one test. Not done: bounding it now (it would be a reading the ADR does not state).
5. **`tracer.sim_profile/1` and `tracer.sim_outcome/1` are not shipped in batch A; only `tracer.timing/1` is.** ADR 0027 says `tracer-evalcore` ships the schemas and the documents test compares them; the timing document is built by P4-07, the other two by P4-13/P4-21 (the sim profile needs the prompt template hash of ADR 0028, the outcome needs the call counts). **Recommendation:** the task that builds each document ships its schema, hand validator and builder (the `flagsdoc` pattern) and adds to `test_adr0027_documents_match_json_schemas.py` the comparison of the shipped schema with `extract_schemas(...)` (an addition, as batch 0 planned). Batch A already reads the `turn_taking` object of the profile (`TurnTakingPolicy.from_profile`) and pins the end-reason rules of the outcome.
6. **The barge-in stop needs a definition of "the activity it interrupted".** ADR 0027 rule 10 says "the end of the last active agent frame of the activity it interrupted". This contract defines a RUN as a chain of active frames closer than `wait_for_agent_silence_ms` (the agent is silent after that pause, by ADR 0026 rule 10), takes the run holding the last active frame at or before the turn's first frame, and gives no value when nothing was in progress, when the run may not have ended by the end of the observation, or when the difference is not positive (5.2). **Recommendation:** accept; the extractor takes the policy's wait value as an argument. Rejected: a fixed pause (an invented number).
7. **Overlapping caller frames.** ADR 0027 rule 4 places a caller frame "at its exact send instant"; two frames sent less than a frame apart (a late frame followed by an on-time one) then overlap on the timeline. **Recommendation:** the later frame wins the shared samples and the recording reports `caller_overlap_samples`, so channel 0 is exact except where the count says otherwise, and the report (P4-24) can show a non-zero count. Rejected: refusing the log (it would lose the recording of a paid call), summing the samples (clipping and a sound nobody sent), shifting the later frame (breaks the exact send instant).
8. **`timing_value` and the exact `timing_edge_missing` messages are pinned in batch A.** SCOPE P4-07 says the result is an `error` with that code and ADR 0027 says its message names the missing edge and counts; P4-22 builds the `MetricResult`. **Recommendation:** P4-22 calls `timing_value` and copies `error_code` and `message` into `MetricResult.error`; the metric key `sim_barge_in_stop_ms` reads the document field `barge_in_stop_median_ms` (the median over the barge-ins with both edges, ADR 0027 Ambiguity 5). Overlap with a silent agent is a measured 0, not an edge error, and a call that was never established is the only case of an absent overlap.
9. **The G.711 tables were not checked against the published ITU-T G.711 text.** There is no network in this task. The tests pin two independent derivations of the standard's formulas, the anchors the standard is known for, structure properties and a SHA-256 of each table. **Recommendation:** the security/licensing or indic-linguistics agent (or the lead) compares the two digests with ITU-T G.711 Tables 2 and 3 when a network is available and records the result (a BACKLOG row until then); if they differ, the pin and the oracle change together. The 16-bit scaling is the conventional one (mu-law times 4, A-law times 8).
10. **A-law has no zero, so "any non-zero sample" makes every A-law frame active.** `0xD5` decodes to +8. No Phase 4 dialect uses A-law (`wire_codec` is `pcm_s16le`, `mulaw` or `webrtc_negotiated`); the decoder exists because SCOPE P4-06 names it. **Recommendation:** a future dialect with an A-law wire must decide silence explicitly (an owner decision, not a threshold invented in code); a BACKLOG row at that time.
11. **The recorder holds the whole WAV in memory.** `build_recording` returns the file as `bytes`; 14,400 s at 48 kHz stereo is 2.76 GB, the bound the timeline check enforces. With the required `--max-call-seconds` of a Phase 4 run this is small. **Recommendation:** keep for batch A; a streaming writer is a BACKLOG row if a long call is ever allowed. An empty log is a valid 44-byte WAV (an established call with nothing sent and nothing heard is not an error after a paid call).
12. **The turn-taker's method names are the contract's** (`begin_wait`, `agent_frame`, `poll`, `brain_started`, `brain_finished`, `may_send`, `line_started`); the ADRs pin the rules, not the names. A merge of two methods would be a named spec change through the lead. A call that raises changes nothing (pinned), because the runner drives the taker in a loop.
13. **Equivalent mutants.** Six of 196 mutants of the oracle cannot change behaviour and are argued in `mutants.txt` (a cache that is recomputed, a flag already clear when the wait closes, two validations of one rule, one unobservable boundary). They are not weaknesses of the tests, but a dev who removes the redundant code removes nothing the tests need.
14. **Not in this batch, by design:** any asyncio, socket or clock (the runner and sender are batch D); the `MetricResult`s and the scorer (P4-22); the bundle builder, the channel map and the privacy markers (P4-21); the chunk plan over agent activity (P4-10); the call log (P4-12). The tests exercise the pure core only, with hand-built frame logs.

## 11. Evidence

`docs/phase4/evidence/red/batch-a/`:

```text
red.py3.11.log, red.py3.12.log, red.py3.13.run1.log, red.py3.13.run2.log, red.py3.14.log
    the staged files at their final path in a mirror of the CURRENT tree (the committed tree d940404, environments synced
    from the current lock): the whole packages/evalcore/tests directory, 1,082 tests collected. 63 failed, 1,018 passed,
    1 skipped on every version (3.13 twice, identical; the set of failing names has the same checksum on all five runs).
    The 63 are 63 of the 67 new tests; the 4 new tests that pass on the current tree are guards of the tests' own oracle
    or of the SCOPE names (the grid, the helpers, the SCOPE-names test, the pending-sets test).
red-reasons.py3.13.txt
    the one-line reason of each of the 63 failures: ModuleNotFoundError for callstate (15), turntaking (16), g711 (3),
    recorder (20), simtiming (7), and for the two source scans "callstate.py does not exist" / FileNotFoundError.
    No failure is an import error of a test itself.
red-without-S-A.py3.13.log
    the ADR reader tests on the current tree with the staged tests but the OLD evalcore_phase4_adr_support.py:
    10 failed (the nine ADR pending-set tests report "exists now; remove it from the pending set", plus
    test_shipped_timing_schema_equals_adr0027_block for the missing module): the named spec change S-A is needed.
oracle.py3.11.log .. oracle.py3.14.log
    packages/evalcore/tests on the oracle (the five modules and the staged files): 1,081 passed, 1 skipped on every version
oracle-full-suite.py3.11.log .. oracle-full-suite.py3.14.log
    the whole workspace on the oracle: 13,027 passed, 1 skipped (the opt-in gate test), 1,288 deselected (the
    private_fixtures, local_only and postgres markers) on every version; the earlier suite had 12,960 passed, plus the
    67 new tests. Python 3.15 was not run (non-blocking leg).
static-checks.log
    ruff format --check, ruff check and mypy --strict over the 8 staged files and the 5 oracle modules at their final
    path; no non-ASCII byte.
mutants.txt
    196 mutants of the oracle: 190 caught, 0 survived, 6 equivalent (argued in its header). Two earlier runs led to six
    test changes (listed in the header).
oracle-src.patch
    the oracle's five modules as new files: a working starting point for the dev (every test passes on it), not a
    substitute for the dev's own implementation
spec-change.diff
    the named spec change S-A, before and after (26 deleted lines, nothing added)
```

Method notes. The oracle and the red run were built in two scratch copies of the tree (rsync of the working tree with its `.git`, excluding `.venv`, `.env.local`, `tools/capture`, the private fixtures and any `marathi_samples`), each with its own `UV_PROJECT_ENVIRONMENT` per Python version, synced one at a time with `uv sync --locked --all-groups`. Mutants ran with a fresh `PYTHONPYCACHEPREFIX` each. The logs are scrubbed of home paths, scratch paths, `<session>`, `pytest-of-<user>` and temporary directory names. Not run: Python 3.15, the lowest-direct legs, the Postgres legs, hosted CI. Because batches B, C and D stage their own copy of `evalcore_phase4_adr_support.py` from the same original, the four copies must be MERGED, not copied over one another: S-A is a set of line deletions, so apply `spec-change.diff` (or delete the same 26 lines) and union it with the deletions of the other batches (the lines are independent).
