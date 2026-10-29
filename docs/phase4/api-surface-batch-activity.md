<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch activity: the optional activity threshold `turn_taking.activity_min_peak` (BL-313)

Status: red-first contract from `qa`. One defect found on a real agent (2026-10-29), now a P1 for
testing the owner's agent. The owner's browser agent streams a low noise floor in its pauses (frame
peaks of the order of 100, at most a few hundred, 16-bit samples) and never digital silence, while
its speech peaks at thousands. Tracer's activity rule (ADR 0026 rule 10: agent activity is a
received frame holding any non-zero sample) therefore never sees the agent go silent. With
`turn_taking.caller_speaks_first` false and `wait_for_agent_silence_ms` 1000 the caller waits until
`max_silence_ms` and the call ends `max_silence` with 0 caller turns, so nothing of the agent is
tested.

Auto-approved design (owner instruction 2026-10-23, decision log entry "PROBE RESULT"): an optional
explicit member `turn_taking.activity_min_peak` of the sim profile, an integer from 1 to 32767 in the
unit of an int16 sample magnitude. A received agent frame is ACTIVE iff the maximum absolute value of
its DECODED 16-bit samples is at least `activity_min_peak` (for mu-law the decoded linear value, for
WebRTC the decoded PCM, never the wire byte). Absent, the value is 1, which is exactly the rule of
today (any non-zero sample): no other default, no code default other than 1. The one definition is
used everywhere the activity rule is used: the wait for silence (turn-taking), `first_agent_audio_ms`,
`gap_ms`, `overlap_ms`, `barge_in_stop_ms`, `answered_caller_turns`, the extraction of the agent
segments for the speech-to-text and `max_silence`. Nothing else changes: the recording keeps every
sample, the quantum is unchanged. Rejected: a voice-activity model (it runs a model; owner rule), an
RMS window threshold (a second number and a window nobody measured), a code default other than 1.
Trade-off: the owner must measure the noise floor of the agent and write a number; the claim stays
"observed at the simulator over <dialect>" and the label names the rule applied, never a claim about
speech or silence.

Batches 0 to G2 and J are committed (HEAD 755f714); their names are reused as they are. The oracle
(the minimal implementation the tests were proved against) is `oracle-src.patch` next to the
evidence; it is a reference for the dev, not code to copy blindly. No test sends a real request:
everything uses fakes, an in-memory connection and the virtual clock. No unit test opens a socket.
Another agent works on the end reason `voice_failed` (BL-369, `api-surface-batch-voice.md`): this
batch does not touch its files; the shared files are listed in section 7.

## 0. Where the tests are, and how to move them

The tests are staged under `docs/phase4/evidence/red/batch-activity/staging/tests/{evalcore,evalsim,cli}/`
and are not in `packages/` yet. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). The two support modules
   (`evalsim_support_activity.py`, `cli_p4act_support.py`) are plain modules found through pytest's
   rootdir import mode, as the earlier support modules are.
2. Apply `spec-change.diff` (named spec changes S-A1 to S-A10, section 7) as exact-string edits. The
   hunks under `packages/*/tests/` are the dev's. The hunks under `docs/` and `packages/cli/README.md`
   are the proposal of section 6: the architect applies the ADR and SCOPE text (the ADR tests and the
   wiring test read it) and may word it differently provided the facts the wiring test pins stay.
3. Create nothing under `packages/` that has no `pyproject.toml`; all changes go into existing
   modules (section 1). No dependency is added; `uv.lock` and the licence policy do not change.
4. Run `uv sync --locked --all-groups && uv run --locked pytest -q`.

No marker is new. The tests are in the default selection on every Python (3.11 to 3.14).

## 1. Package ownership

| Package | Changed modules |
|---|---|
| `tracer-evalcore` (pure) | `turntaking` (the range constants, the fourth field of `TurnTakingPolicy`, `activity_min_peak_of`, the keyword `min_peak` of `agent_frame_is_active`, `TurnTaker` reads the policy), `simprofile` (one optional property in the schema), `simtiming` (one optional property in the schema, `extract_timing(activity_min_peak=)`, the validator, `activity_wording`), `audio/activity` and `audio/chunker` (the keyword of `activity_chunk_plan`, the field and the record of `ChunkPlan`) |
| `tracer-evalsim` (async) | `runner` only: `_last_active_before` uses the same predicate with the threshold of the policy. `fakeagent` and the dialects are unchanged. |
| `tracer-cli` | `simulate` (reads the member as the file gave it, passes it to the segment plan and to the timing extractor), `simsummary` (the label), `simreport` (the note and one attribute), `README.md` (the quick-start output) |
| documents | ADR 0026 rule 10; ADR 0027 rules 10 and 13 and its two JSON blocks; `docs/phase4/SCOPE.md` (Q6); `docs/concepts/simulated-calls.md`; `docs/examples/simulated-mr/` |

`tracer_cli.simulate` stays the only importer of `tracer_evalsim`. No new dependency, no new module,
no new command line option, no new file in `--out`.

## 2. The option

The member is `turn_taking.activity_min_peak` of the sim profile file (`tracer.sim_profile_file/1`)
and of the stored sim profile (`tracer.sim_profile/1`); it is also an optional key of the timing
document (`tracer.timing/1`) and of the chunk plan (`tracer.chunk_plan/2`).

| Property | Value |
|---|---|
| Type | integer: a Python `int` that is not a `bool`; `100.0`, `1e2`, `true`, `"100"`, `null`, a list are refused |
| Range | 1 to 32767, both inclusive |
| Unit | the magnitude of a 16-bit sample after decoding (a mu-law byte is decoded to its G.711 value; the most negative sample, -32768, has the magnitude 32768) |
| Absent | the value is 1: a frame is active iff it holds any non-zero sample, exactly the earlier rule |
| Default in code | 1 and no other: `TurnTakingPolicy.activity_min_peak` defaults to 1; the three owner values still have none |
| Active frame | the maximum over the frame of the absolute value of its decoded samples is at least the value |

Where the member is read, and the stable code of a bad value (a code, never the value):

| Where | Call | Refusal |
|---|---|---|
| the profile file | `parse_sim_profile_file` (the `turn_taking` object is checked against the JSON Schema of the ADR) | `SimProfileError` `sim_profile_file_invalid_turn_taking` (the code of every fault of that object, an unknown key included) |
| the policy | `TurnTakingPolicy(..., activity_min_peak=)` and `TurnTakingPolicy.from_profile` | `TurnTakingError` `activity_min_peak_invalid` |
| the stored profile | `validate_sim_profile` | `SimProfileError` `schema:/turn_taking/activity_min_peak` |
| the predicate | `agent_frame_is_active(pcm, min_peak=)` | `TurnTakingError` `activity_min_peak_invalid` (a bad frame is still `frame_invalid`, checked first) |
| the timing extractor and validator | `extract_timing(activity_min_peak=)`, `validate_timing_document` | `TimingDocumentError` `activity_min_peak_invalid` (a present key must hold a value: `None` in the document is refused; `None` as the argument means "not given") |
| the segment plan | `activity_chunk_plan(activity_min_peak=)` | `ChunkerParameterError` |
| the command | step 1 (before any key, any engine, any connection) | exit 4, one line `error: sim_profile_file_invalid_turn_taking`, nothing on stdout, no directory |

Reading a stored profile or a timing document WITHOUT the member means 1 (`activity_min_peak_of`,
section 3). A run stored before this batch stays valid and is read as 1.

## 3. The pure functions

All in `tracer-evalcore`; the loaders of the tests are lazy.

3.1 `turntaking` constants: `ACTIVITY_MIN_PEAK_MIN = 1`, `ACTIVITY_MIN_PEAK_MAX = 32767`,
`ACTIVITY_MIN_PEAK_DEFAULT = 1`.

3.2 `TurnTakingPolicy` gains a FOURTH field `activity_min_peak: int = 1` after `max_silence_ms`
(the three owner values keep no default, positional order unchanged). Validation in `__post_init__`
after the existing checks: `type(v) is int` and `1 <= v <= 32767`, else `activity_min_peak_invalid`.
`from_profile(section)` accepts the three required keys and the optional member (any other key is
still `turn_taking_unknown_key`, a missing required key is still `<key>_missing`); an absent member
gives 1.

3.3 `activity_min_peak_of(section) -> int`: the value of the member of a `turn_taking` object or of a
timing document, or 1 when the member is missing; a present member is validated (as 3.2); a non-mapping
is a `TurnTakingError`.

3.4 `agent_frame_is_active(pcm, *, min_peak=1) -> bool`: after the frame checks of the earlier batch
(`bytes`, non-empty, even length, else `frame_invalid`) and the check of `min_peak`, True iff the
largest absolute value of the little-endian 16-bit samples is at least `min_peak`. `min_peak=1` is
`any(pcm)`. A peak, not an energy: 160 samples of 699 are inactive at 700, one sample of 700 among
zeros is active.

3.5 `TurnTaker.agent_frame` uses `policy.activity_min_peak`; onset, last activity, the wait for
silence, the barge-in offset and `max_silence` all read the one predicate.

3.6 `simtiming.extract_timing(..., activity_min_peak: int | None = None)` (keyword-only, default
`None` = the profile gave none): active frames are the frames whose DECODED samples (`decode_to_pcm16`
of the frame's codec: PCM as is, mu-law, A-law) satisfy 3.4; `first_agent_audio_ms`, every `gap_ms`,
`overlap_ms`, the answered count and `barge_in_stop_ms` (the run of active frames closer than the wait
that the line interrupted) are measured over these frames only. `quantum_ms` is still the larger of
the caller frame and the longest agent frame received, active or not. The document has the key
`activity_min_peak` iff the argument is not `None` (also for a call that was not established).

3.7 `simtiming.activity_wording(min_peak) -> str`: `"any non-zero sample"` for 1, else
`"peak >= N"`; a bad value is `TimingDocumentError("activity_min_peak_invalid")`. This is the closed
wording of every label; it states the rule applied.

3.8 `validate_timing_document` accepts the key set of the earlier batches and the same set plus
`activity_min_peak` (any other key set is `document_keys`); a present member must satisfy 3.1.

3.9 `audio.activity.activity_chunk_plan(..., activity_min_peak: int | None = None)` (keyword-only,
default `None`): a 20 ms frame of the agent channel is active iff its largest absolute sample is at
least the peak (1 when `None`); runs, joins by the wait and chunking are unchanged.
`ChunkPlan.activity_min_peak` is the argument; `as_metadata()` of a v2 plan holds
`"activity_min_peak"` iff it is not `None`. The format stays `tracer.chunk_plan/2` (an optional key
is not a new format, ambiguity A5).

## 4. The records and the labels

4.1 The stored sim profile: `build_sim_profile` copies the `turn_taking` object of the file, so the
member is in the stored profile iff the file gave it, with the value as given (also an explicit 1).
`validate_sim_profile` takes it as optional with the range of section 2. The snapshot digest follows
the stored bytes: it does not change for a file without the member.

4.2 The timing document: the optional key of 3.6, placed after `caller_frame_ms`.

4.3 The chunk plan of the `our_stt` transcript (`metadata["tracer.chunk_plan"]`): the optional key of
3.9.

4.4 The labels. The claim does not change: "observed at the simulator over <dialect>". The printed
line and the report row of a measured value carry the rule after the quantum, only when the timing
document has the member:

```
first agent audio: 500 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: peak >= 2)
gap after the caller's line (median): 2440 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: peak >= 2, N 1)
overlap: 0 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: peak >= 2)
answered caller turns: 1 (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: peak >= 2)
```

With the member 1 the words are `activity: any non-zero sample`. A value that could not be measured
because an edge is missing names the rule too:
`first agent audio: not measured (missing edge: first active agent frame, activity: peak >= 32767)`;
a value that is not measured for a missing input (`not measured (missing input turn_timing)`) is
unchanged. In the report: the paragraph `timing-note` ends with ` activity: <wording>.` (before it
the sentence of the earlier batches, unchanged); each row of the class `timing` is the printed text;
each row of the class `timing-record` has the attribute `data-activity-min-peak="N"`. Without the
member nothing of this is written: the strings and the attributes are the ones of the earlier batches.

## 5. The runner

`run_call` reads the threshold from `RunnerSettings.policy` (no new field of `RunnerSettings`, a
test pins the field list). Every use of the activity rule reads it: the taker through
`TurnTaker.agent_frame` (the wait for silence, the barge-in onset), and the runner's own
`_last_active_before` (the `reply_ms` and `wait_ms` of the simulator's own times). The frame the
predicate sees is the DECODED 16-bit PCM of the frame (`decode_to_pcm16`); the dialects are
unchanged: pipecat-protobuf (16-bit PCM), twilio (mu-law decoded to its G.711 value) and
smallwebrtc/1 (the PCM frames the media stack hands over). The recording is not gated: every received
frame stays in `agent_frames` byte for byte. With the member absent or 1 the runner behaves exactly as
before; with a threshold above the noise of an agent and below its speech, the caller sees the pauses
of the agent: by hand, speech at 500 to 680 ms and a wait of 300 ms give the first line at 980 ms.
A frame below the threshold counts as silence even if it is speech (documented consequence): a
quiet agent is not heard by a threshold above its speech and the call ends `max_silence`.

## 6. The text the architect applies (proposed ADR, SCOPE and document amendments)

PROPOSED TEXT only: this batch does not edit `docs/adr`. The exact-string edits are in
`spec-change.diff`; the architect applies them and may word them differently provided the facts that
`test_the_adr_text_defines_the_threshold_where_the_rule_is_stated` and its neighbours pin stay. Each
change below shows the words around it (eight words of context on each side); the unchanged text of
the rule is not repeated.

### 6.1 ADR 0026 rule 10 (S-A1)

Change 1. Was:

```text
(Q6; P4-05).** Agent activity is a received frame holding any non-zero sample: no energy threshold and no voice-activity model runs in Tracer. The agent is silent once no active frame
```

Now:

```text
(Q6; P4-05).** Agent activity is a received frame whose decoded 16-bit samples reach a peak (amended 2026-10-29, BL-313): the frame is active iff the maximum absolute value of its samples is at least `turn_taking.activity_min_peak`, an optional integer of 1 to 32,767 of the sim profile, in the unit of an int16 sample (for mu-law the decoded G.711 value, for WebRTC the decoded PCM, never the wire byte). Absent, the value is 1, which is exactly the earlier rule (a frame holding any non-zero sample): Tracer has no other default and chooses no number, the owner writes it after measuring the agent's own noise (a measured peak, not a model). The reason: a real browser agent streams a low noise floor in its pauses (frame peaks of the order of 100 and at most a few hundred) and never digital silence, while its speech peaks at thousands, so under the earlier rule the caller never saw the agent go silent, waited until `max_silence_ms` and the call ended `max_silence` with no caller turn. The one definition is used everywhere the rule is used: the wait for silence, `first_agent_audio_ms`, `gap_ms`, `overlap_ms`, `barge_in_stop_ms`, the answered caller turns, the extraction of the agent segments for speech-to-text and the end reason `max_silence`; nothing else changes (the recording keeps every sample and the quantum is unchanged). No energy window and no voice-activity model runs in Tracer. A frame below the threshold counts as silence even when it is speech: that is the consequence of the number the owner writes. When the sim profile gives the value, it is recorded in the stored sim profile and in the timing document, and every timing label names it as the rule applied (`activity: peak >= N`, or `activity: any non-zero sample` for 1), never as a statement that the audio is or is not speech; a profile without it has no such record and a reader takes the missing value as 1. The agent is silent once no active frame
```

Change 2. Was:

```text
values are owner values, O4); `caller_speaks_first` is required too. A scripted turn may carry `barge_in_after_ms`: the caller
```

Now:

```text
values are owner values, O4); `caller_speaks_first` is required too; `activity_min_peak` is the one optional value of the object. A scripted turn may carry `barge_in_after_ms`: the caller
```

Change 3. Was:

```text
producing the next line. An agent that streams comfort noise never looks silent, so such a call ends on `max_silence` (or
```

Now:

```text
producing the next line. An agent that streams noise at or above the threshold never looks silent (without the member, any noise does), so such a call ends on `max_silence` (or
```

Change 4. Was:

```text
(it runs a model; owner rule); an energy threshold (a number nobody measured). Trade-off: comfort-noise agents cannot be driven turn by turn in Phase 4. Tests: `test_agent_activity_is_any_nonzero_sample_no_threshold`, `test_wait_for_silence_parameter_has_no_default`, `test_barge_in_fires_at_offset_from_agent_onset`, `test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang`, `test_simulator_never_speaks_while_brain_pending`.
```

Now:

```text
(it runs a model; owner rule); an energy or RMS window over several frames (a second number and a window nobody measured); a default in code other than 1 (a number nobody measured). Trade-off: the owner must measure the noise floor of the agent and write a number; a number set below the noise leaves the call ending `max_silence`, and a number set above the agent's speech makes the agent look silent for ever; a file without the number behaves as before. Tests: `test_a_frame_is_active_iff_its_largest_absolute_sample_reaches_the_threshold`, `test_with_a_threshold_above_the_noise_the_caller_sees_the_pause_and_the_call_completes`, `test_a_noise_floor_agent_completes_the_call_with_a_threshold_above_the_noise`, `test_agent_activity_is_any_nonzero_sample_no_threshold`, `test_wait_for_silence_parameter_has_no_default`, `test_barge_in_fires_at_offset_from_agent_onset`, `test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang`, `test_simulator_never_speaks_while_brain_pending`.
```

### 6.2 ADR 0027 rule 10 (S-A2)

Change 1. Was:

```text
is not observed. Agent activity is a frame holding any non-zero sample (ADR 0026 rule 10), dated by its arrival instant (not its playback
```

Now:

```text
is not observed. Agent activity is a frame whose decoded samples reach `activity_min_peak` in absolute value (ADR 0026 rule 10; 1, which is any non-zero sample, when the profile gives none), dated by its arrival instant (not its playback
```

Change 2. Was:

```text
buffering and is not the agent's internal latency. Rejected: a voice-activity threshold for the agent's onset (ADR 0026 rule 10);
```

Now:

```text
buffering and is not the agent's internal latency. When the sim profile gives `activity_min_peak`, the document records it as the optional integer `activity_min_peak` (1 to 32,767, amended 2026-10-29, BL-313) and every label of a value names it as the rule applied (`activity: peak >= N`, or `activity: any non-zero sample` for 1); when the profile gives none, the document has no such key and a reader takes it as 1, as a document of an earlier run is read. Rejected: a voice-activity model for the agent's onset (ADR 0026 rule 10);
```

Change 3. Was:

```text
includes the transport. Tests: `test_timing_first_agent_audio_exact_on_synthetic_log`, `test_timing_gap_measured_from_last_sent_frame`, `test_simulator_self_latency_excluded_from_agent_gap`, `test_timing_values_carry_quantum_and_dialect`, `test_timing_value_without_both_edges_is_error_never_zero`.
```

Now:

```text
includes the transport. Tests: `test_timing_first_agent_audio_exact_on_synthetic_log`, `test_timing_gap_measured_from_last_sent_frame`, `test_simulator_self_latency_excluded_from_agent_gap`, `test_timing_values_carry_quantum_and_dialect`, `test_timing_value_without_both_edges_is_error_never_zero`, `test_the_values_are_measured_by_the_threshold_and_not_by_the_noise`, `test_the_document_echoes_the_member_exactly_when_it_was_given`.
```

### 6.3 ADR 0027 rule 13 (S-A3)

Change 1. Was:

```text
on the wire, the turn-taking values (`caller_speaks_first`, `wait_for_agent_silence_ms`, `max_silence_ms`), the scheduled barge-ins, the caller brain (mode; for
```

Now:

```text
on the wire, the turn-taking values (`caller_speaks_first`, `wait_for_agent_silence_ms`, `max_silence_ms` and, when the input file gave it, `activity_min_peak`), the scheduled barge-ins, the caller brain (mode; for
```

Change 2. Was:

```text
the input file and has no default in code. Rejected: storing the whole voice table (entries of
```

Now:

```text
the input file and has no default in code, except `turn_taking.activity_min_peak`, which is optional (amended 2026-10-29, BL-313): absent means 1, and the stored profile then has no such key, so a run without it keeps the bytes it had. Rejected: storing the whole voice table (entries of
```

Change 3. Was:

```text
and with the connection labels. Tests: `test_prompt_template_hash_recorded_in_sim_profile`, `test_wait_for_silence_parameter_has_no_default`, `test_adr0027_documents_match_json_schemas`.
```

Now:

```text
and with the connection labels. Tests: `test_prompt_template_hash_recorded_in_sim_profile`, `test_wait_for_silence_parameter_has_no_default`, `test_adr0027_documents_match_json_schemas`, `test_the_stored_profile_records_the_member_exactly_when_the_file_gave_it`.
```

### 6.4 The sim profile JSON block and its cross-field list (S-A4)

In the `turn_taking` object of the `tracer.sim_profile/1` block (the `required` list is unchanged),
and one bullet in the list that follows the block:

```diff
-        "max_silence_ms": {"type": "integer", "minimum": 1, "maximum": 9007199254740991}
+        "max_silence_ms": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
+        "activity_min_peak": {"type": "integer", "minimum": 1, "maximum": 32767}
+- `turn_taking.activity_min_peak` is optional (BL-313): it is the only key of the document that may be absent, it is present iff the input file gave it, and a missing member reads as 1 (any non-zero sample). It is in the unit of an int16 sample magnitude.
+    "activity_min_peak": {"type": "integer", "minimum": 1, "maximum": 32767},
+- `activity_min_peak` is optional (BL-313): present iff the sim profile gave it, with the same value, and a missing member reads as 1; it does not change `quantum_ms`.
```

(the changes of rules 10 and 13, shown in 6.2 and 6.3, are left out of this diff.)

### 6.5 The timing JSON block and its cross-field list (S-A5)

One property after `caller_frame_ms` (the `required` list is unchanged) and one bullet, shown in
6.4. The shipped schemas (`SIM_PROFILE_JSON_SCHEMA`, `TIMING_JSON_SCHEMA`) are equal to these blocks
when parsed, which `test_shipped_sim_profile_schema_equals_adr0027_block`,
`test_shipped_timing_schema_equals_adr0027_block` and
`test_the_two_json_blocks_of_adr_0027_carry_the_optional_member` check.

### 6.6 Phase 4 scope, Q6 (S-A6), the concept page (S-A7), the example (S-A8)

```diff
+  - Update 2026-10-29 (BL-313, a field result on a real browser agent whose pauses carry a noise floor): the optional sim-profile value `turn_taking.activity_min_peak` (1 to 32767, in int16 sample units) makes a frame active only when its largest decoded sample reaches it; absent means 1, which is the rule above, so nothing earlier changes (ADR 0026 rule 10, ADR 0027 rules 10 and 13).
+- An agent that streams a steady noise floor never sends a frame of zeros, so the caller would never see it pause. The optional `activity_min_peak` (a whole number from 1 to 32767) in the `turn_taking` object of the sim profile file changes the rule for the whole call: a frame then counts only when the largest absolute value of its decoded 16-bit samples is at least that number. Without it the rule above holds. When the file gives it, the number is stored with the run, and every timing label says it (`activity: peak >= N`, or `activity: any non-zero sample` for 1). The number is the rule applied, nothing more: a frame below it counts as silence even when it is speech, and Tracer does not choose the number.
-    "max_silence_ms": 3000
+    "max_silence_ms": 3000,
+    "activity_min_peak": 1
-| `sim-profile.json` | `tracer.sim_profile_file/1`: the dialect `pipecat-protobuf/1`, the audio format, the silence values, the fake voice and the fake speech-to-text; every value is written here because the command has no default |
+| `sim-profile.json` | `tracer.sim_profile_file/1`: the dialect `pipecat-protobuf/1`, the audio format, the silence values (and `activity_min_peak`, the one optional value: 1 is the rule of any non-zero sample, written here so that the label shows it), the fake voice and the fake speech-to-text; every value is written here because the command has no default |
-first agent audio: <ms> ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)
-gap after the caller's line (median): <ms> ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, N 10)
-overlap: 0 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)
-answered caller turns: 10 (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)
+first agent audio: <ms> ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: any non-zero sample)
+gap after the caller's line (median): <ms> ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: any non-zero sample, N 10)
+overlap: 0 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: any non-zero sample)
+answered caller turns: 10 (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, activity: any non-zero sample)
```

## 7. Named spec changes

Exact-string edits against the CURRENT tree, in `spec-change.diff` (a `git diff` of a mirror of the
tree at HEAD 755f714; 9 files). Nothing is weakened: S-A9 and S-A10 name the one exception each and
keep every other assertion; a guard that objects is amended, never skipped.

| Id | File | Edit |
|---|---|---|
| S-A1 | `docs/adr/0026-simulated-caller-architecture.md` | rule 10: four changes (section 6.1) |
| S-A2 | `docs/adr/0027-simulated-run-records.md` | rule 10: three changes (section 6.2) |
| S-A3 | `docs/adr/0027-simulated-run-records.md` | rule 13: three changes (section 6.3) |
| S-A4 | `docs/adr/0027-simulated-run-records.md` | the `tracer.sim_profile/1` JSON block (one property) and its cross-field list (one bullet) |
| S-A5 | `docs/adr/0027-simulated-run-records.md` | the `tracer.timing/1` JSON block (one property) and its cross-field list (one bullet) |
| S-A6 | `docs/phase4/SCOPE.md` | Q6: one update line |
| S-A7 | `docs/concepts/simulated-calls.md` | "What is measured": one bullet; the fixed "Not claimed" sentence is untouched |
| S-A8 | `docs/examples/simulated-mr/sim-profile.json`, `docs/examples/simulated-mr/README.md`, `packages/cli/README.md` | the example writes `"activity_min_peak": 1`; one clause in the file table; the four timing lines of the quick-start output carry `, activity: any non-zero sample` |
| S-A9 | `packages/evalcore/tests/test_sim_turntaking.py` | `test_wait_for_silence_parameter_has_no_default`: the policy has the three owner fields without default and a fourth field `activity_min_peak` whose default is 1; the ADR schema has the three required keys and four properties |
| S-A10 | `packages/evalcore/tests/test_adr0027_documents_match_json_schemas.py` | `test_an_optional_looking_key_is_not_optional`: every key is required except the two named `activity_min_peak` (`OPTIONAL_KEYS`), and the test checks both are present |

The whole workspace was run against the oracle WITHOUT the spec change first (16,360 passed, 5
failed). The five objections:

| Test that objected | Reason | Resolution |
|---|---|---|
| `test_shipped_sim_profile_schema_equals_adr0027_block` and `test_shipped_timing_schema_equals_adr0027_block` | the shipped schemas gain the property, the ADR block must too | S-A4, S-A5 |
| `test_wait_for_silence_parameter_has_no_default` | the policy gains a field | S-A9 |
| `test_sim_core_modules_import_no_clock_or_randomness` | an oracle draft imported `sys` in `turntaking` (one of the five pure modules) | the oracle reads the samples with `struct`; no spec change (a constraint on the implementation: `turntaking.py` and `simtiming.py` import no `sys`, `os`, `time`, `random`) |
| `test_guard_has_no_false_positive_on_this_repository` | an untracked file of the working tree (`docs/phase4/runbooks/RJ1.md`, finding `tracer judge allowlist`) objects on the CURRENT tree too, with or without this batch | not this batch; the mirrors leave that file out; a finding of its own (A9) |

`test_an_optional_looking_key_is_not_optional` did not fail in that run only because the ADR block was
not yet changed (it reads the ADR); with S-A4 and S-A5 applied it is the guard that objects, hence
S-A10.

## 8. Test inventory

New test functions, by file (the number of test items after parametrization is in `counts.txt`):

| File | Functions | What it pins |
|---|---|---|
| `evalcore/tests/test_sim_activity_threshold.py` | 24 | the profile file (range, type, absent, the closed object, the three required keys), the stored profile (given or absent, bad value pointer, old document valid, schema), the policy (fourth field, default 1, range, `from_profile`, `activity_min_peak_of`), the predicate (peak rule, boundaries, negative and most negative samples, one loud sample, default = old rule, errors, a property test, decoded mu-law and not the wire byte), the decision (noise never starts activity, barge-in from the first active frame, a frame at the threshold, a property test against `ref_turn_decision`) |
| `evalcore/tests/test_sim_timing_activity_threshold.py` | 24 | the values measured by the threshold and not the noise (first audio, gap, overlap, barge-in stop, counts), speech below the threshold is silence, boundary equal to the noise, quantum unchanged, mu-law and A-law decoded, the echo only when given, not-established calls, bad values, signature, validator, shipped schema, old documents read as 1, the closed wording, T-DEG keys for the three dialects, a property test against an independent reference, the agent segments (bursts only above the noise, plan record, bad values, default unchanged) |
| `evalsim/tests/test_evalsim_activity_threshold.py` | 15 | the runner on the virtual clock for pipecat-protobuf, twilio and smallwebrtc: the field problem without the member or with 1, the comfort-noise scenario unchanged by 1, a threshold above the noise (the caller sees the pause, speaks at 980, the script completes at 1980), the wait after a line, boundaries, the whole-frame peak, barge-in from the first active frame, the own times, mu-law decoded (372 and 7932, a negative code), the recording keeps every sample, hang-up and `max_duration` unaffected, a threshold above the whole signal, `RunnerSettings` fields unchanged |
| `cli/tests/test_cli_simulate_activity_min_peak.py` | 16 | the field call end to end (`script_complete`, 2 segments, 500 / 2440 / 0 / 1 ms, 4080 ms connected, the words of the segments), the same call without the member (`max_silence` at 3000 ms), 1 says so, the three records, the old shape without the member, the labels (printed and report, rows, attributes), twilio, refusals at step 1 with one code, boundaries, no command line option (usage error 64), the dry run unchanged, the help |
| `cli/tests/test_tdeg_sim_activity_min_peak.py` | 5 | T-DEG: the result keys and stored key sets with the member, a threshold above every signal (edges missing are errors, never 0), a call with no caller turn (not applicable, never 0), a comfort-noise agent behind the threshold has the shape of the silent-agent case, both runnable dialects |
| `evalcore/tests/test_phase4_batch_activity_wiring.py` | 10 | the inventory (names defined once, none pending, the contract and its headings, no module-level import of a Tracer module), the ADR text facts, the two JSON blocks equal the shipped schemas, SCOPE, concept page, example profile, quick-start output, the spec change reached the two guard tests |

Support modules: `evalsim/tests/evalsim_support_activity.py` (a noise-floor agent built with the
independent reference encoder and the independent G.711 table; the virtual-clock call) and
`cli/tests/cli_p4act_support.py` (the field call against the in-process fake agent).

Tests of the batch that already pass on the current tree (regression and negative guards, and the
inventory checks that need no new code) are listed with their reasons in
`red-tests-that-pass-on-the-current-tree.txt`.

## 9. The guarantee for a profile without the member

A profile file with no `activity_min_peak` behaves and records exactly as before this batch: the
stored profile, the timing document, the chunk plan, every printed line and the report are the bytes
of the earlier batches. This is checked by the existing tests, unchanged: `test_e2e_sim_synthetic_mr`
(the pinned `WAV_SHA256`, `REPORT_MASKED_SHA256`, `BUNDLE_MASKED_SHA256`, the whole `TIMING_DOCUMENT`,
the printed lines with the note `observed at the simulator over pipecat-protobuf/1, quantum 20 ms`),
`test_cli_simulate_m1`, `test_cli_simulate_report`, `test_tdeg_sim`, `test_sim_timing`,
`test_sim_turntaking`, `test_activity_chunk_plan`, and by the new tests that assert the absence of the
key and of the word `activity` in every output. The guarantee has one exception chosen with the
design: the example profile of the repository writes the member (S-A8), so the quick-start output
changes by the words `, activity: any non-zero sample`.

The rule is "the member is recorded and named iff the file gave it" (ambiguity A1). Reading is
independent of it: a missing member is 1 (`activity_min_peak_of`).

## 10. The command

10.1 The value lives in the sim profile JSON file only, like `wait_for_agent_silence_ms`; there is no
new option (the existing pattern has none for a profile value). A test pins that no option of
`tracer simulate` contains `activity`, `peak`, `threshold` or `noise`, that `--activity-min-peak` is a
usage error (exit 64, nothing on stdout), that the help does not mention it, and that `--dry-run`
prints the same plan with and without the member (it prints no turn-taking value).

10.2 Step 1 reads the member with the rest of the file. A bad value is refused there, before any key,
engine, connection or directory, with `sim_profile_file_invalid_turn_taking` (section 2).

10.3 `_Inputs` keeps the member as the file gave it (`None` when absent); the `TurnTakingPolicy` of the
run carries the effective value. The extractor and the segment plan receive the value as given, so the
documents record it iff the file gave it.

10.4 The labels are those of section 4.4. The wording is a statement of the rule, not of the audio:
no label says that a frame is speech, noise or silence.

10.5 Exit codes are unchanged. The field call with the member exits 0; a call whose threshold is above
all the agent's audio ends `max_silence` with two timing edges missing and exits 2, as the silent agent
does (T-DEG `agent_silent`). A call with no caller turn has no `turn_timing` input, so its four values
are `not_applicable` (missing input `turn_timing`), never 0; that is the field problem itself and it
is not changed by the member.

## 11. Ambiguities, with recommendations

A1. **"Always written" or "written when given".** The brief asks both that the stored profile and the
timing document always carry the value (1 when not given) and that the pinned outputs of the existing
e2e and M1 tests do not move. They cannot both hold: the pinned `TIMING_DOCUMENT`, the printed notes,
`REPORT_MASKED_SHA256` and `BUNDLE_MASKED_SHA256` contain exactly the bytes that an always-written
member would change. Recommendation (built and proved here): write, echo and name the member iff the
file gave it (an explicit 1 included); a reader takes a missing member as 1. The statement "as used" is
then exact (1 is the rule when nothing is said), no stored run changes, and the byte guarantee of
section 9 holds. The alternative was built and measured on the oracle (two changed lines: the profile
always written, the value always passed to the extractor and the plan): the whole workspace then has
SIX existing tests that fail, all pinned outputs, `test_m1_offline_run_prints_exactly_these_lines`,
`test_m1_a_silent_agent_is_exit_two_with_the_edge_named`,
`test_sim_report_timing_shows_quantum_and_dialect`, `test_e2e_sim_synthetic_mr_exact_outputs`,
`test_e2e_judge_synthetic_mr_exact_outputs` and `test_tdeg_sim_agent_silent_timing_not_measured`, and
every old-shaped output carries the `activity:` words. If the lead prefers it, the change is local:
`build_sim_profile` and `_Inputs` always carry the value (1 when the file gave none), the two label
functions drop their "in the document" condition, and the pins of those six tests move (with the
hashes of the e2e tests).

A2. **The policy dataclass gets a fourth field.** Alternative: a separate argument to `TurnTaker` and
`RunnerSettings`. Recommendation: the field, because the `turn_taking` object of the profile is the
one place the value lives and `from_profile` already reads it; the price is S-A9 (the test of the
three owner values keeps every assertion for them).

A3. **An optional key in documents whose ADR invariant is "every key is required".** The invariant
says absence is never a default. The member is the one key where absence is defined (1) because every
run stored before this batch lacks it, so making it required would invalidate the stored runs.
Recommendation: optional in the schemas of the sim profile and of the timing document, named in one
place per document (S-A4, S-A5, S-A10), present only when the file gave it.

A4. **Which labels carry the rule.** The brief says every timing label. A value that is not measured
for a missing input has no rule to name; a value that is not measured because an edge is missing
depends on the rule, so it names it (`missing edge: ..., activity: peak >= N`). Recommendation: as in
section 4.4.

A5. **The chunk plan records the member without a new format.** `tracer.chunk_plan/2` gains an optional
integer; the plan of a run without the member is byte-identical. Alternative `tracer.chunk_plan/3`:
rejected, an optional key is not a new format and no reader branches on it.

A6. **The sample bot's own timer.** `tools/samplebot/samplebot/end_of_caller.py` (the sample agent's
end-of-caller timer) has its own copy of the "any non-zero byte" rule for the caller's frames. It is a
different tool that decides when the BOT hangs up, not when Tracer's caller speaks; it is not changed.
Follow-up for the architect if the bot is ever run against a noisy caller.

A7. **WebRTC.** `smallwebrtc/1` is not runnable by the command (`dialect_not_runnable`); the runner
tests drive it through an in-memory connection of plain PCM frames, which is what the media stack hands
over. The rule is the same (decoded PCM).

A8. **No cross-field check of the number against the speech.** Tracer cannot know the level of an
agent's speech, so a number above it is valid and the call ends `max_silence` (the documented
consequence, pinned by tests). A later warning (for example "no frame reached the threshold") would be
a new finding, not part of this batch.

A9. **A finding outside the batch.** `docs/phase4/runbooks/RJ1.md` is untracked in the working tree and
the guard test `test_guard_has_no_false_positive_on_this_repository` objects to it (finding `tracer
judge allowlist`) with or without this batch. The whole-workspace runs of this batch use a mirror
without that file.

A10. **Documents not edited here.** The owner's R1 runbook values (`docs/phase4/runbooks/R1.md`) are
registered owner values; the owner adds `activity_min_peak` to the profile of the real-agent run after
measuring the agent's noise. The three package changelogs and `docs/BACKLOG.md` (close BL-313) are the
dev's and the lead's.
