# ADR 0027: Simulated-run records

- Status: Accepted (Phase 4 batch 0, task P4-00b). Records the findings F5, F6, F7 and F10 and the questions Q1, Q12 to Q14, Q19 and Q21 of `docs/phase4/SCOPE.md`, auto-approved by the lead on the recommended option under the owner's standing rule of 2026-10-23 (decision log "PHASE 4 SCOPE DELIVERED", 2026-10-27; SCOPE Decisions D1 and D5). Schema 0.0.1 is unchanged: every rule uses the 0.0.1 records as they are, and the gaps are BACKLOG rows for schema 0.0.2 (BL-015, BL-193, BL-285 to BL-287). The points where the SCOPE was ambiguous are listed under "Ambiguities resolved" for the lead. Open for the owner: O7 (retention of simulated runs; the recommendation, no retention marker for a run with no human subject, is applied in rule 14 and changes if the owner asks for a deletion date). Amended 2026-10-27 (lead decision E-a, decision log "ADR 0026 AMENDED"): `tracer.sim_profile/1` gains the required object `connection` (rule 13, the schema and its cross-field rules) for the labels ADR 0026 rules 6 and 16 record; rule and invariant counts are unchanged (17 rules, I1 to I10).
- Date: 2026-10-27
- Task: P4-00b (implemented by P4-06, P4-07, P4-10, P4-12, P4-21, P4-22, P4-24, P4-25, P4-28, P4-29)

## Context
A simulated call (ADR 0026) produces a recording of two sides, the text the simulator spoke, our speech-to-text of the agent's side, and, when the user supplies it, the agent's own export of the call. Schema 0.0.1 can hold all of it without a change (F5): `Run.mode` has `simulated`, which implies `caller_origin=simulated` (ADR 0016 Decision 2); `UsageRecord.stage` has `sim_caller_llm` and `sim_caller_tts`; the `Evaluator` has `instructions`, `expected_outcome`, `scripted_turns` (`index`, `text`, `language`) and `max_duration_ms` (at most 14,400,000); `ChannelMap` has `declared` determinations and the roles `caller` and `agent`; `TRANSCRIPT_SOURCES` and `TURN_SOURCES` have `script`. Three things are missing: `Persona` holds only `language` and `accent`; `Run` has no status or end reason; there is no timing source for the simulator's own clock and no turn source for a caller line an LLM wrote.

Two 0.0.1 facts shape the rules. First (F6): the bundle's applicability re-run selects the turns of an `agent_transcript` input by role, not by source (`bundle._turns_read`, ADR 0004 Decision 6d), and a simulated run produces `our_stt` turns with role `agent` (the agent channel of a declared stereo map), so a metric that requires `agent_transcript` could read our own STT as if it were the agent's transcript. Second (F7): `ConfigSnapshot` requires an enabled `llm_judge` expected-outcome check as a required rubric item, and there is no judge (BL-173, BL-269).

## Decision
Machine-read by `test_adr0027_rules_are_tested`, in the format of ADR 0023: every numbered rule of this section and every invariant cites at least one test name in backticks. A cited name that no test defines yet must be one that the task table of `docs/phase4/SCOPE.md` (section A) names; that set of not-yet-defined names shrinks to empty by the gate (P4-G). `test_adr0027_documents_match_json_schemas` compares the JSON Schema of each of the three sections "The `tracer.sim_profile/1` format", "The `tracer.sim_outcome/1` format" and "The `tracer.timing/1` format" with the one `tracer-evalcore` ships.

1. **No schema change; three versioned documents (Q1; F5; SCOPE Decisions D5).** The simulator's configuration is the document `tracer.sim_profile/1`, stored in `ConfigSnapshot.metadata["tracer.sim_profile"]`, which the snapshot's content digest covers (`digest_domain` keeps `metadata`). The call's outcome is `tracer.sim_outcome/1`, stored in `Run.metadata["tracer.sim_outcome"]`. The timing measurements are `tracer.timing/1`, stored in `Run.metadata["tracer.timing"]`, and each timing result carries the same document in `MetricResult.metadata["tracer.timing"]` (the pattern of `tracer.flags/1`, ADR 0023 rule 12). The metadata key carries no version; the document's `format` member does. Every document holds only integers, booleans, `null`, short machine keys, lower-case hexadecimal digests and canonical decimal strings: no text of any line, no number, no path, no URL and no ID string, so `metadata_flags` stays empty. `tracer-evalcore` owns the formats and validates each document against its schema and its cross-field rules before it builds a record; any change of a field, a rule or a meaning is a new format name (`/2`). Rejected: schema 0.0.2 now (a migration and a frozen-version break in the same phase as async transports, BL-015 re-targeted); persona fields in `Persona.metadata` (identity mixed with rendering); the version in the metadata key (a reader would need the version to find the key, and two versions could co-exist in one record). Trade-off: untyped in 0.0.1; readers outside `tracer-evalcore` must validate with the shipped schemas (BL-285 to BL-287). Tests: `test_adr0027_documents_match_json_schemas`, `test_evalschema_unchanged_since_phase4_baseline`.
2. **The Run (F5).** `mode=simulated`, `caller_origin=simulated`, `observed_tier=T0` (a `script`, an `our_stt` and an `agent_export` transcript with timing `audio_alignment` or `none` are T0 evidence, ADR 0004 Decision 2a). `started_at` is the instant the connection was established, read from the simulator's wall clock in UTC; `tracer.sim_outcome/1` states that clock (`started_at_clock`), and a call that was never established has no `started_at`. The Run holds no `caller_external_id` (no caller identifier exists) and no `external_refs` (the synthetic stream identifiers of ADR 0026 rule 6 are not provider references, and a call id on the Run is the re-import key of ADR 0022 Decision 11). `agent_target` names the allowlisted target of ADR 0026 rule 16. Every produced bundle passes `check_run_bundle`. Rejected: the instant the command started as `started_at` (it is not when the call started); external references for synthetic identifiers (they would look like provider records). Trade-off: `started_at` is our clock, not the agent's. Tests: `test_simulated_bundle_passes_check_run_bundle`, `test_e2e_sim_report_and_store_consistent`.
3. **The stereo artifact and its declared channel map.** One Artifact holds the recording: a WAV file, `audio_format{container: wav, codec: pcm_s16le, sample_rate_hz: <the dialect rate>, channels: 2, bits_per_sample: 16}`; channel 0 is the caller exactly as sent and channel 1 the agent as received. The Run carries one `ChannelMap{artifact_id, channels: [{index: 0, role: caller}, {index: 1, role: agent}], determination: declared, method: tracer_evalsim_stereo/1}` with no confidence (a declaration by construction is not a probability, ADR 0012 Decision 5). A call that was never established has no artifact and no channel map. Rejected: two mono artifacts (the shared timeline would be implicit); an inferred map (Tracer wrote both channels, so it knows them). Trade-off: a stereo file is twice the size of the agent side alone. Tests: `test_recorder_channel_0_is_exactly_what_was_sent`, `test_stt_agent_channel_turns_have_role_agent_from_declared_map`, `test_simulated_bundle_passes_check_run_bundle`.
4. **The recorder (Q12; P4-06).** One monotonic timeline whose origin (sample 0) is the instant the connection was established. A caller frame is placed at its exact send instant. An agent frame is placed at the later of its arrival instant and the end of the previous agent frame (the playback model: a burst of frames plays back to back, as a listener would hear it; documented, so the WAV positions of a burst differ from the arrival instants that timing uses, rule 10). Audio is written as 16-bit PCM at the dialect rate; G.711 mu-law and A-law are decoded by a pure table checked against the published ITU-T G.711 table; nothing is ever resampled, and a frame at another rate or channel count is refused (`transport_error`). The same frame log gives a byte-identical WAV. Rejected: placing agent frames at arrival only (bursts would overlap themselves); resampling a mismatched frame (it invents samples, BL-187). Trade-off: the recording shows playback order, not network jitter; the frame log keeps the arrival instants. Tests: `test_recorder_is_deterministic_from_frame_log`, `test_recorder_channel_0_is_exactly_what_was_sent`, `test_recorder_burst_arrival_uses_playback_model`, `test_g711_mulaw_alaw_decode_matches_itu_table`, `test_recorder_refuses_rate_mismatch_never_resamples`.
5. **The caller lines: a `script` transcript (Q13).** One Transcript with `source=script`, no `provider`, no `model`, `timeline_origin{kind: artifact, ref: <the stereo artifact>}` and `metadata["tracer.caller_text_origin"]` equal to `scripted`, `llm` or `replay` (the brain mode, ADR 0028). One Turn per caller line of which at least one frame was sent, in send order: `role=caller`, `source=script`, `text` exactly the text that was synthesised, `timing_source=audio_alignment`, `start_ms` the send instant of its first frame and `end_ms` the send instant of its last frame plus the frame duration, both on the artifact timeline (exact, from our own clock; the reading of `audio_alignment` for a time we produced is recorded here and the dedicated simulator source is BL-285). `Turn.language` is the language code the runner asked the TTS to speak (`mr-IN`): the producer's statement of the language it generated, which ADR 0004 Decision 6c counts for turns "our runner ... generated from a script"; it is not an observation of what the agent heard. A line cut short by the end of the call keeps the frames sent and has `metadata["tracer.caller_line_complete"]=false`; a line never started is not a turn. Our speech-to-text never runs on the caller channel (the text is known). Rejected: an `our_stt` transcript of the caller channel (it would re-measure text we wrote); a new turn source for LLM lines (a schema change, BL-286). Trade-off: `script` turns carry LLM-written lines too, told apart only by the transcript metadata. Tests: `test_e2e_sim_synthetic_mr_exact_outputs`, `test_stt_never_runs_on_caller_channel_by_default`, `test_scripted_mode_makes_no_llm_request`.
6. **The agent side: an `our_stt` transcript with role `agent` (P4-10).** One Transcript with `source=our_stt`, the STT `provider` and `model` as requested, `timeline_origin{kind: artifact, ref: <the stereo artifact>}` and the chunk plan `tracer.chunk_plan/2` in its metadata (ADR 0024 amendment A5): one chunk per agent-activity segment of channel 1. Each Turn has `role=agent`, taken from the declared channel map (never inferred), `source=our_stt`, and otherwise the mapping of ADR 0024 rule 12 (one Turn per non-empty chunk, times offset by the chunk's start on the artifact timeline, the observed language from a request with `language_code=unknown`). The transcript is all or nothing (ADR 0004 Decision 10a). The Phase 3 mono mapping (role `unknown`, `tracer.chunk_plan/1`) is unchanged. Rejected: role `unknown` on the agent channel (the channel is known by construction, so `unknown` would hide a fact); diarization (nothing to separate on a one-speaker channel). Trade-off: the turns have role `agent` but are not the agent's own record, which rules 7 and 8 handle. Tests: `test_stt_agent_channel_turns_have_role_agent_from_declared_map`, `test_activity_chunk_plan_is_deterministic_and_covers_agent_audio`, `test_phase3_mono_mapping_unchanged`, `test_tdeg_sim_stt_failure_missing_input_transcript`.
7. **The agent's own transcript only from `--agent-transcript`.** An agent export enters a simulated run only when the user supplies it with `--agent-transcript <file>` (the existing VoicEra reader: `source=agent_export`, `assistant` lines to role `agent`, `user` lines to role `caller`, the language from the required declaration, ADR 0004 Decision 6g). Tracer never fetches it from the agent and never builds one from the protocol (RTVI or T1 text is BL-275). Rejected: building an agent transcript from our STT (it is not the agent's record, F6). Trade-off: without an export the agreement results are `missing_input`. Tests: `test_tdeg_sim_export_present_agreement_scored`, `test_tdeg_sim_no_export_agreement_missing_input`.
8. **`available_inputs` of a simulated run (F6; ADR 0004 Decision 11).** Derived from the records the bundle holds, exactly: `audio` iff the stereo artifact exists; `agent_audio` iff `audio` and the declared map names exactly one agent channel (always, given the artifact); `transcript` iff an `our_stt` transcript exists (ADR 0011: `transcript` is our STT; the `script` transcript makes no input kind available); `agent_transcript` iff the bundle holds an agent-produced transcript (`source` `agent_export` or `agent_telemetry`) with at least one turn of role `agent`, never because of `our_stt` turns of role `agent`; `turn_timing` iff at least one Turn has `start_ms` and `end_ms`; no other kind (no `usage`, since no usage of the agent under test exists, ADR 0004 Decision 2). The 0.0.1 bundle check cannot tell the producer of an agent turn (it reads by role), so this is a producer rule pinned by tests; the 0.0.2 fix is BL-193 extended (each transcript input kind reads the turns of the sources it names). Rejected: listing `agent_transcript` whenever an agent-role turn exists (F6: our STT would be read as the agent's transcript); dropping the role from the agent-channel turns (rule 6). Trade-off: the rule is the producer's, so a hand-built bundle can still break it and pass the 0.0.1 check (as the Phase 1 T-DEG fixture `T0m` of `docs/phase1/api-surface-tdeg.md` does). Tests: `test_available_inputs_never_lists_agent_transcript_without_agent_record`, `test_adr0004_simulated_agent_transcript_rule_cites_test`, `test_simulated_bundle_passes_check_run_bundle`.
9. **Scorers select the agent's side by source, never by role alone (F6).** A scorer that compares or reads the agent's own text takes the turns of transcripts whose `source` is an agent source; a scorer that reads our STT of the agent channel takes the `our_stt` turns of role `agent`. The agreement scorer for simulated runs is the per-role partition of ADR 0023 amendment A2. Rejected: selecting by role (in a simulated run two different producers write role `agent`). Trade-off: every scorer states the sources it reads. Tests: `test_agreement_partition_compares_agent_role_only`, `test_partition_changes_scorer_version`.
10. **Timing extraction: the `tracer.timing/1` document (F10, Q21; P4-07).** Every value is an integer number of milliseconds (or a count) between two instants of the simulator's own monotonic clock, floored to the millisecond, and is read with the document's `dialect` and `quantum_ms`; `quantum_ms` is the larger of the caller frame duration (20 ms) and the longest agent frame received, rounded up to a whole millisecond, because the position of a sample inside a frame is not observed. Agent activity is a frame holding any non-zero sample (ADR 0026 rule 10), dated by its arrival instant (not its playback position, rule 4). The values: `setup_ms`, from the start of the connection attempt to its establishment; `first_agent_audio_ms`, from establishment to the first active agent frame; for each caller turn, `gap_ms`, from the end edge of the turn (the send instant of its last frame plus 20 ms) to the first active agent frame arriving at or after that edge; `overlap_ms` per turn and in total, the summed duration of active agent frames that arrive while a caller line is being sent; `barge_in_stop_ms` for a turn sent with `barge_in_after_ms`, from the send instant of its first frame to the end of the last active agent frame of the activity it interrupted; `answered_caller_turns`, the caller turns with a `gap_ms`; and, separately, the simulator's own reply time before each caller turn (`self`: the wait, the in-call STT, the brain and the TTS times, each `null` where that step did not run). A value with a missing edge is `null` and is never 0. The simulator's own reply time is never inside an agent gap: a gap ends at the agent's first sample, before the simulator decides to speak. Each value is "observed at the simulator over <dialect>": it includes the network and both sides' buffering and is not the agent's internal latency. Rejected: a voice-activity threshold for the agent's onset (ADR 0026 rule 10); playback positions for timing (they move a burst later than it arrived); 0 for a missing edge (a fabricated measurement). Trade-off: every value is coarse to one frame and includes the transport. Tests: `test_timing_first_agent_audio_exact_on_synthetic_log`, `test_timing_gap_measured_from_last_sent_frame`, `test_simulator_self_latency_excluded_from_agent_gap`, `test_timing_values_carry_quantum_and_dialect`, `test_timing_value_without_both_edges_is_error_never_zero`.
11. **Timing definitions with no pass rule (P4-22 (b)).** Shipped templates, `language_independent`, `script_sensitive=false`, `aggregation=per_conversation`, `requires=["audio", "turn_timing"]`, no pass rule, `implementation_ref` `builtin:<key>`: `sim_first_agent_audio_ms`, `sim_agent_response_gap_median_ms` (the nearest-rank median of the turns' `gap_ms` values, N recorded as `gap_n`), `sim_overlap_ms` (the total) and `sim_barge_in_stop_ms` (the nearest-rank median over the barge-ins with both edges, N recorded), each `kind=latency`, unit `ms`; and `sim_answered_caller_turns`, `kind=deterministic`, unit `count`. `sim_barge_in_stop_ms` is bound only when the sim profile schedules at least one barge-in, so the set of result keys is fixed by the configuration and equal across every case of one configuration. Each scored result carries the run's `tracer.timing/1` document (rule 1). A value whose edges are missing (an agent that never spoke, a barge-in the agent never stopped for) cannot be `not_applicable` in a valid 0.0.1 bundle when `audio` and `turn_timing` are available (the re-run of ADR 0004 Decision 9 would compute an applicable result), so it is `status=error` with `error.code=timing_edge_missing` and a message that names the missing edge and counts, never a value, never 0 and never a pass or fail (the ADR 0023 rule 15 pattern; BL-292). Rejected: a pass rule (a latency target is the owner's to set, never a shipped value); a mean (one long gap would move it; the median with N is stated); 0 for no agent audio. Trade-off: an agent that never spoke shows timing errors, which the report labels "not measured: no agent audio", never as an agent failure. Tests: `test_timing_definitions_have_no_pass_rule`, `test_median_is_nearest_rank_with_n_recorded`, `test_sim_report_timing_shows_quantum_and_dialect`, `test_tdeg_sim_schema_identical_across_cases`.
12. **The outcome document and the inert expected-outcome check (F7, Q19).** `tracer.sim_outcome/1` records whether the call was established, its one end reason (ADR 0026 rule 9), the cap that ended it if any, the clock of `started_at`, the connected duration, the SHA-256 of the authorisation reference (ADR 0026 rule 21), the brain mode and the counts (frames, requests, cache hits, regenerations, ignored messages, signals). Its `outcome` member is always `not_evaluated`. The snapshot's rubric holds the inert check of ADR 0023 rule 17 (named `simulated_call_no_outcome_check`, judge model recorded as not run, never run, no result, no verdict) as its only required item, the agreement and timing definitions as optional items; no `RunVerdict` is produced (BL-270), and the CLI, the report and the concept page say "outcome not evaluated". The `Evaluator` is the user's own (instructions, expected outcome, scripted turns, maximum duration). Rejected: running a judge on the expected outcome (no judge, no calibration, BL-269); an end reason in free text. Trade-off: the snapshot holds a required check that never runs until the judge phase. Tests: `test_outcome_check_inert_and_no_verdict`, `test_sim_report_states_outcome_not_evaluated`, `test_adr0027_documents_match_json_schemas`.
13. **The sim profile in the snapshot (Q7, Q10, Q17).** `tracer.sim_profile/1` records, as used: the dialect, the connection labels of the target's allowlist entry (amended 2026-10-27, lead decision E-a; ADR 0026 rules 6 and 16: the name of the environment variable that held the bearer token or `null`, never its value; the `Origin` header sent or `null`; and, for `twilio-media-stream/1` only, the operator's `bot_holds_telephony_credentials` statement), the audio format on the wire, the turn-taking values (`caller_speaks_first`, `wait_for_agent_silence_ms`, `max_silence_ms`), the scheduled barge-ins, the caller brain (mode; for LLM mode the provider, model, `max_tokens`, `seed`, `reasoning_effort`, `max_line_chars`, `max_turns`, the end sentinel and the prompt template id and SHA-256, ADR 0028), the resolved voice of the run's persona (the input file may map several personas; only the entry used is stored), the STT engine, and the limits (the four caps as given, `null` where an engine is a fake and no cap was given, and the minimum interval). Every value is required in the input file and has no default in code. Rejected: storing the whole voice table (entries of other personas are not part of the run); caps outside the snapshot (a run could not be reproduced under the same limits); leaving the connection labels out (a profile that carries them would be invalid against the schema, and a reader could not tell how the target was reached); putting them in free-text metadata (unvalidated). Trade-off: the snapshot digest changes with any cap and with the connection labels. Tests: `test_prompt_template_hash_recorded_in_sim_profile`, `test_wait_for_silence_parameter_has_no_default`, `test_adr0027_documents_match_json_schemas`.
14. **Privacy markers (Q14; ADR 0016 Decisions 5 and 7a).** The producer builds every marker after the call, when the Run already carries the declared channel map, by `derive_human_subject(run, turns)` over the run's turns: with role evidence of only `caller` and `agent` it is `false`, so the artifact and every transcript carry `human_subject=false` with no consent record and no retention policy (P-46). Where the derivation is `true` (an export with an `unknown` role turn), the marker is `true` with consent `status=unknown` (the only truthful value for a simulator) and a retention policy with `delete_on_expiry=false` (the local store keeps it; O7). A record written before role evidence exists (none in the pinned order; a later incremental writer) is `true` with reason `marked_before_role_evidence`, consent `unknown` and that retention, and is re-derived at export (ADR 0016 Decision 7a). A call to a target where a human may answer is refused by the allowlist (ADR 0026 rule 16), and a human-present mode is BL-281. Rejected: `true` on every simulated payload (it would block benchmarks of synthetic calls for no subject); a consent status of `obtained` (nobody gave it). Trade-off: a simulated run with an agent export that has `unknown` turns becomes human-subject, which is the conservative reading. Tests: `test_privacy_marker_false_only_with_role_evidence`, `test_simulated_bundle_passes_check_run_bundle`.
15. **Usage and cost (P4-12).** Every answered request of every engine makes one UsageRecord with `domain=evaluation` (ADR 0024 amendment A7): `sim_caller_tts` (`input_characters`, `requests`), `sim_caller_llm` (`input_tokens`, `cache_read_input_tokens` where reported, `output_tokens`, `requests`) and `stt` (`audio_input_seconds`, `requests`). A TTS cache hit makes no record. Without a user USD catalog every CostRecord is `not_available` (`no_price_for_model`), never 0. None of these is usage of the agent under test, so `usage` is never an available input (rule 8). Rejected: one record per call (the per-request record is what the provider can bill). Trade-off: many records per call. Tests: `test_sim_usage_records_per_request_with_stages`, `test_sim_cost_not_available_without_usd_catalog`, `test_tts_cache_hit_sends_nothing_and_records_no_usage`.
16. **Degradation: the same record kinds in every case (T-DEG; P4-29).** Every case of one configuration gives the same result keys and record kinds; only statuses, values and reasons differ, and a missing input is never 0, false or a failure (ADR 0004): setup failed (no artifact, no transcript: every result `missing_input`, exit 5); no export (agreement `missing_input` `agent_transcript`); export present (agreement scored); STT failure (no `our_stt` transcript: agreement `missing_input` `transcript`, exit 3, a Run `IngestWarning`); kill switch mid-call (the partial recording and the usage kept, end reason `kill_switch`); `max_duration`, `max_silence` and `spend_cap` (end reasons, with what was recorded); an agent that never spoke (timing `error` `timing_edge_missing`, rule 11). Rejected: dropping results whose inputs are missing (the key set would vary by case). Trade-off: a failed call still stores a run full of not-applicable results. Tests: `test_tdeg_sim_setup_failed_all_not_applicable_exit_5`, `test_tdeg_sim_no_export_agreement_missing_input`, `test_tdeg_sim_export_present_agreement_scored`, `test_tdeg_sim_stt_failure_missing_input_transcript`, `test_tdeg_sim_kill_switch_keeps_partial_recording_and_usage`, `test_tdeg_sim_schema_identical_across_cases`.
17. **The store and the report.** The simulated bundle round-trips through the store byte-identical on SQLite, and stores on Postgres with the same rows (`postgres` marker, loopback). Every record gets a fresh UUIDv7, so a re-run is identical modulo IDs and the timestamps derived from them (ADR 0023 rule 18). The report shows the stereo recording (relative audio path, the existing content security policy and seek script), the caller and agent intervals on one timeline, the timing table with quantum and dialect, the label "synthetic caller audio", "outcome not evaluated", and usage and cost. Rejected: deterministic IDs (two runs of one call would collide). Trade-off: runs are compared by their documents' parameters, never by bytes. Tests: `test_simulated_run_round_trips_through_store_byte_identical`, `test_simulated_bundle_stores_on_both_backends`, `test_sim_report_labels_synthetic_audio`.

## The `tracer.sim_profile/1` format
The document of rules 1 and 13, stored in `ConfigSnapshot.metadata["tracer.sim_profile"]`. The JSON Schema below (draft 2020-12) is the structural part; `tracer-evalcore` ships the same schema and `test_adr0027_documents_match_json_schemas` compares the two. Single-member sets are written as `enum` (ADR 0003 Decision 6). Every integer is at most `2**53 - 1` (I-JSON, ADR 0007 Decision 5).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.sim_profile/1",
  "type": "object",
  "additionalProperties": false,
  "required": ["format", "dialect", "connection", "audio", "turn_taking", "barge_ins", "caller", "voice", "stt", "limits"],
  "properties": {
    "format": {"type": "string", "enum": ["tracer.sim_profile/1"]},
    "dialect": {"type": "string", "enum": ["pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1"]},
    "connection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["auth_token_env", "origin", "bot_holds_telephony_credentials"],
      "properties": {
        "auth_token_env": {"type": ["string", "null"], "pattern": "^[A-Z_][A-Z0-9_]{0,63}$"},
        "origin": {"type": ["string", "null"], "pattern": "^(?:https?|wss?)://[a-z0-9.-]+(?::[0-9]{1,5})?$"},
        "bot_holds_telephony_credentials": {"type": ["boolean", "null"]}
      }
    },
    "audio": {
      "type": "object",
      "additionalProperties": false,
      "required": ["sample_rate_hz", "channels", "frame_ms", "wire_codec"],
      "properties": {
        "sample_rate_hz": {"type": "integer", "minimum": 8000, "maximum": 48000},
        "channels": {"type": "integer", "enum": [1]},
        "frame_ms": {"type": "integer", "enum": [20]},
        "wire_codec": {"type": "string", "enum": ["pcm_s16le", "mulaw", "webrtc_negotiated"]}
      }
    },
    "turn_taking": {
      "type": "object",
      "additionalProperties": false,
      "required": ["caller_speaks_first", "wait_for_agent_silence_ms", "max_silence_ms"],
      "properties": {
        "caller_speaks_first": {"type": "boolean"},
        "wait_for_agent_silence_ms": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
        "max_silence_ms": {"type": "integer", "minimum": 1, "maximum": 9007199254740991}
      }
    },
    "barge_ins": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["turn_index", "barge_in_after_ms"],
        "properties": {
          "turn_index": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
          "barge_in_after_ms": {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
        }
      }
    },
    "caller": {
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["mode"],
          "properties": {
            "mode": {"type": "string", "enum": ["scripted"]}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["mode", "provider", "model", "max_tokens", "seed", "reasoning_effort", "max_line_chars", "max_turns", "max_regenerations", "end_sentinel", "prompt"],
          "properties": {
            "mode": {"type": "string", "enum": ["llm"]},
            "provider": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
            "model": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
            "seed": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
            "reasoning_effort": {"type": ["string", "null"], "pattern": "^[a-z][a-z0-9_]{0,31}$"},
            "max_line_chars": {"type": "integer", "minimum": 1, "maximum": 2500},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
            "max_regenerations": {"type": "integer", "enum": [1]},
            "end_sentinel": {"type": "string", "enum": ["END_CALL"]},
            "prompt": {
              "type": "object",
              "additionalProperties": false,
              "required": ["template_id", "template_sha256", "agent_text_max_chars"],
              "properties": {
                "template_id": {"type": "string", "pattern": "^tracer\\.caller_prompt/[1-9][0-9]{0,8}$"},
                "template_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "agent_text_max_chars": {"type": "integer", "minimum": 1, "maximum": 9007199254740991}
              }
            }
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["mode", "max_line_chars", "max_turns", "end_sentinel"],
          "properties": {
            "mode": {"type": "string", "enum": ["replay"]},
            "max_line_chars": {"type": "integer", "minimum": 1, "maximum": 2500},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
            "end_sentinel": {"type": "string", "enum": ["END_CALL"]}
          }
        }
      ]
    },
    "voice": {
      "type": "object",
      "additionalProperties": false,
      "required": ["provider", "model", "speaker", "language_code", "pace", "sample_rate_hz", "codec"],
      "properties": {
        "provider": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
        "model": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
        "speaker": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
        "language_code": {"type": "string", "enum": ["mr-IN"]},
        "pace": {"type": "string", "pattern": "^(?:0|[1-9][0-9]{0,2})(?:\\.[0-9]{0,5}[1-9])?$"},
        "sample_rate_hz": {"type": "integer", "minimum": 8000, "maximum": 48000},
        "codec": {"type": "string", "enum": ["linear16", "mulaw"]}
      }
    },
    "stt": {
      "type": "object",
      "additionalProperties": false,
      "required": ["provider", "model", "max_chunk_ms"],
      "properties": {
        "provider": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
        "model": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"},
        "max_chunk_ms": {"type": "integer", "minimum": 1, "maximum": 9007199254740991}
      }
    },
    "limits": {
      "type": "object",
      "additionalProperties": false,
      "required": ["max_call_seconds", "max_tts_chars", "max_llm_tokens", "max_stt_seconds", "min_call_interval_ms"],
      "properties": {
        "max_call_seconds": {"type": ["integer", "null"], "minimum": 1, "maximum": 14400},
        "max_tts_chars": {"type": ["integer", "null"], "minimum": 1, "maximum": 9007199254740991},
        "max_llm_tokens": {"type": ["integer", "null"], "minimum": 1, "maximum": 9007199254740991},
        "max_stt_seconds": {"type": ["integer", "null"], "minimum": 1, "maximum": 9007199254740991},
        "min_call_interval_ms": {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
      }
    }
  }
}
```

Cross-field rules that JSON Schema cannot state, checked by the `tracer-evalcore` validator (each a refusal, never a repair):
- `twilio-media-stream/1` needs `audio.sample_rate_hz` 8000 and `audio.wire_codec` `mulaw`; `pipecat-protobuf/1` needs `pcm_s16le`; `smallwebrtc/1` needs `webrtc_negotiated`.
- `voice.sample_rate_hz` equals `audio.sample_rate_hz`; `voice.codec` is `mulaw` iff `audio.wire_codec` is `mulaw`, else `linear16`.
- `turn_taking.max_silence_ms` is at least `turn_taking.wait_for_agent_silence_ms`.
- `barge_ins` holds each `turn_index` at most once, sorted ascending; in scripted mode each `turn_index` names a scripted turn of the evaluator.
- A cap is not `null` when its engine is real: `max_call_seconds` when any of the three engines is not `fake`, `max_tts_chars` when `voice.provider` is not `fake`, `max_llm_tokens` when `caller.mode` is `llm` with a provider other than `fake`, `max_stt_seconds` when `stt.provider` is not `fake`.
- `caller.prompt.template_sha256` is the SHA-256 of the template that `caller.prompt.template_id` names in `tracer-evalcore` (ADR 0028).
- `connection` (added 2026-10-27, lead decision E-a; ADR 0026 rules 6 and 16) copies the allowlist entry of the target: `auth_token_env` is the NAME of the environment variable the bearer token was read from, or `null` when no token was sent (the value is never stored); `origin` is the `Origin` header sent, or `null` when none was sent; `bot_holds_telephony_credentials` is a boolean iff `dialect` is `twilio-media-stream/1`, else `null`, and in Phase 4 it is `false` (ADR 0026 rule 6 refuses `true` before the call).

## The `tracer.sim_outcome/1` format
The document of rules 1 and 12, stored in `Run.metadata["tracer.sim_outcome"]`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.sim_outcome/1",
  "type": "object",
  "additionalProperties": false,
  "required": ["format", "dialect", "outcome", "call_established", "end_reason", "cap_reached", "started_at_clock", "connected_ms", "authorisation_reference_sha256", "caller_text_origin", "counts"],
  "properties": {
    "format": {"type": "string", "enum": ["tracer.sim_outcome/1"]},
    "dialect": {"type": "string", "enum": ["pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1"]},
    "outcome": {"type": "string", "enum": ["not_evaluated"]},
    "call_established": {"type": "boolean"},
    "end_reason": {"type": "string", "enum": ["script_complete", "caller_end_call", "agent_hangup", "max_duration", "max_turns", "max_silence", "kill_switch", "setup_failed", "transport_error", "brain_invalid_output", "spend_cap"]},
    "cap_reached": {"type": ["string", "null"], "enum": ["call_seconds", "tts_chars", "llm_tokens", "stt_seconds", null]},
    "started_at_clock": {"type": "string", "enum": ["simulator_wall_clock"]},
    "connected_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
    "authorisation_reference_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "caller_text_origin": {"type": "string", "enum": ["scripted", "llm", "replay"]},
    "counts": {
      "type": "object",
      "additionalProperties": false,
      "required": ["caller_turns_started", "caller_turns_completed", "agent_segments", "barge_ins_fired", "brain_requests", "brain_invalid_outputs", "brain_regenerations", "tts_requests", "tts_cache_hits", "llm_requests", "stt_requests", "frames_sent", "frames_received", "inbound_messages_ignored", "signals_received"],
      "properties": {
        "caller_turns_started": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "caller_turns_completed": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "agent_segments": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "barge_ins_fired": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "brain_requests": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "brain_invalid_outputs": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "brain_regenerations": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "tts_requests": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "tts_cache_hits": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "llm_requests": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "stt_requests": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "frames_sent": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "frames_received": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "inbound_messages_ignored": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
        "signals_received": {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
      }
    }
  }
}
```

Cross-field rules, checked by the `tracer-evalcore` validator:
- `end_reason` `setup_failed` implies `call_established=false`; `call_established=false` implies `end_reason` `setup_failed` or `kill_switch` and `connected_ms` `null`; `call_established=true` implies `connected_ms` not `null`.
- `cap_reached` is not `null` iff `end_reason` is `spend_cap`.
- `caller_turns_completed <= caller_turns_started`; `barge_ins_fired <= caller_turns_started`; `brain_regenerations <= brain_invalid_outputs`; `brain_requests` and `llm_requests` are 0 when `caller_text_origin` is `scripted` (ADR 0028).
- `caller_text_origin` equals the brain mode of the snapshot's `tracer.sim_profile/1` and the `script` transcript's `metadata["tracer.caller_text_origin"]`.

## The `tracer.timing/1` format
The document of rules 1, 10 and 11, stored in `Run.metadata["tracer.timing"]` and on each scored timing result.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.timing/1",
  "type": "object",
  "additionalProperties": false,
  "required": ["format", "dialect", "clock", "quantum_ms", "caller_frame_ms", "setup_ms", "first_agent_audio_ms", "overlap_ms", "answered_caller_turns", "gap_median_ms", "gap_n", "barge_in_stop_median_ms", "barge_in_n", "turns"],
  "properties": {
    "format": {"type": "string", "enum": ["tracer.timing/1"]},
    "dialect": {"type": "string", "enum": ["pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1"]},
    "clock": {"type": "string", "enum": ["simulator_monotonic"]},
    "quantum_ms": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
    "caller_frame_ms": {"type": "integer", "enum": [20]},
    "setup_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
    "first_agent_audio_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
    "overlap_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
    "answered_caller_turns": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
    "gap_median_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
    "gap_n": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
    "barge_in_stop_median_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
    "barge_in_n": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
    "turns": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["turn_seq", "gap_ms", "overlap_ms", "barge_in_after_ms", "barge_in_stop_ms", "self"],
        "properties": {
          "turn_seq": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
          "gap_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
          "overlap_ms": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
          "barge_in_after_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
          "barge_in_stop_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
          "self": {
            "type": "object",
            "additionalProperties": false,
            "required": ["reply_ms", "wait_ms", "stt_ms", "brain_ms", "tts_ms"],
            "properties": {
              "reply_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
              "wait_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
              "stt_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
              "brain_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991},
              "tts_ms": {"type": ["integer", "null"], "minimum": 0, "maximum": 9007199254740991}
            }
          }
        }
      }
    }
  }
}
```

Cross-field rules, checked by the `tracer-evalcore` validator:
- `turns` is sorted by `turn_seq`, holds each `turn_seq` once, and each names a Turn of the run's `script` transcript.
- `answered_caller_turns` and `gap_n` equal the number of turns with a non-null `gap_ms`; `gap_median_ms` is `null` iff `gap_n` is 0, and otherwise is the nearest-rank median of those values (the value at rank `ceil(n / 2)` of the sorted values).
- `barge_in_n` equals the number of turns with a non-null `barge_in_stop_ms`; `barge_in_stop_median_ms` is `null` iff `barge_in_n` is 0, and otherwise is their nearest-rank median; `barge_in_stop_ms` is `null` whenever `barge_in_after_ms` is `null`.
- `overlap_ms` is the sum of the turns' `overlap_ms`, or `null` iff the call was never established; `setup_ms` is `null` iff no connection attempt completed or failed with a measured instant; `first_agent_audio_ms` is `null` iff no active agent frame arrived.
- In each `self`, `reply_ms` is `null` for a turn the caller spoke first (no agent reply preceded it); `stt_ms` and `brain_ms` are `null` unless the brain mode is `llm` (ADR 0028); `tts_ms` is `null` when the line came from the cache or was synthesised before the call (scripted mode).
- `quantum_ms` is at least `caller_frame_ms`.

## Ambiguities resolved
For the lead (SCOPE D1 covers the Q items; these are readings the SCOPE leaves open).
- **Ambiguity 1. A missing timing edge cannot be `not_applicable` (new finding, F11 of Phase 4).** P4-07 and P4-29 say a timing value without both edges is not applicable, and the T-DEG test was named test_tdeg_sim_agent_silent_timing_not_applicable (renamed 2026-10-27, D-a, to `test_tdeg_sim_agent_silent_timing_not_measured`). In 0.0.1 the timing definitions require `audio` and `turn_timing`, both available in such a call (the caller's `script` turns are timed), so the re-run of `check_run_bundle` (ADR 0004 Decision 9) computes an applicable result and rejects a stored `not_applicable`; no not-applicable reason of the closed set says "nothing to measure". **Recommendation:** in the document the value is `null` (that is what `test_timing_value_without_both_edges_is_error_never_zero` checks, at document level), and the result is `error` with `error.code=timing_edge_missing` (rule 11), the ADR 0023 rule 15 pattern; the lead renames the P4-29 test by a named spec change of the SCOPE row before qa writes it (proposed: `test_tdeg_sim_agent_silent_timing_not_measured`); the 0.0.2 row is BL-292. Rejected: requiring an input kind the silent call lacks (none exists; making `agent_audio` depend on activity would state that a channel is unknown when it is known).
- **Ambiguity 2. Metadata keys.** The SCOPE writes `Run.metadata["tracer.sim_outcome/1"]`. **Recommendation:** the key without the version and the version in `format` (rule 1), as `tracer.flags` and `tracer.chunk_plan` do.
- **Ambiguity 3. A third brain mode.** The SCOPE names `scripted` and `llm` for `tracer.caller_text_origin`; replay is a third mode (P4-15). **Recommendation:** `replay` as a third value (rule 5), because a replayed line was not written by an LLM in this run.
- **Ambiguity 4. The kind of `sim_answered_caller_turns`.** The SCOPE lists it among the `latency` definitions. **Recommendation:** `kind=deterministic`, unit `count` (rule 11): a count of turns is not a latency.
- **Ambiguity 5. `sim_barge_in_stop_ms` with several or no barge-ins.** **Recommendation:** the nearest-rank median with N for several; bound only when the profile schedules at least one (rule 11), so the key set is fixed by the configuration.
- **Ambiguity 6. Where the timing document lives.** **Recommendation:** on the Run and on each scored timing result (rule 1), so a setup failure still records `setup_ms` and each value is reproducible from its own result.

## Consequences
- A simulated run is a valid 0.0.1 bundle with three machine documents that readers outside `tracer-evalcore` must validate with the shipped schemas; the typed fields are 0.0.2 work (BL-015, BL-285 to BL-287).
- The F6 rule is a producer rule: a hand-built bundle can still list `agent_transcript` from our own STT and pass the 0.0.1 check until BL-193 lands.
- An agent that never speaks yields timing `error` results, not not-applicable ones, until 0.0.2 has a reason for "nothing to measure" (BL-292).
- Every value is a statement about one call over one dialect at one quantum; nothing aggregates across calls in the product.

## Invariants (testable)
- I1. The three documents validate against the schemas of this ADR, hold no text, no number, no path and no ID string (the connection labels of the sim profile are a variable name and an origin, never a token), and leave `metadata_flags` empty. Tests: `test_adr0027_documents_match_json_schemas`, `test_simulated_bundle_passes_check_run_bundle`.
- I2. Channel 0 is exactly what was sent, nothing is resampled, and the same frame log gives the same WAV. Tests: `test_recorder_channel_0_is_exactly_what_was_sent`, `test_recorder_refuses_rate_mismatch_never_resamples`, `test_recorder_is_deterministic_from_frame_log`.
- I3. Agent-channel STT turns have role `agent` from the declared map; our STT never runs on the caller channel. Tests: `test_stt_agent_channel_turns_have_role_agent_from_declared_map`, `test_stt_never_runs_on_caller_channel_by_default`.
- I4. `agent_transcript` is never available without an agent-produced transcript with an agent turn. Tests: `test_available_inputs_never_lists_agent_transcript_without_agent_record`, `test_adr0004_simulated_agent_transcript_rule_cites_test`.
- I5. Every timing value carries its quantum and dialect, a value without both edges is `null` and never 0, and the simulator's own reply time is never inside an agent gap. Tests: `test_timing_values_carry_quantum_and_dialect`, `test_timing_value_without_both_edges_is_error_never_zero`, `test_simulator_self_latency_excluded_from_agent_gap`.
- I6. Timing definitions have no pass rule and the median is nearest-rank with N. Tests: `test_timing_definitions_have_no_pass_rule`, `test_median_is_nearest_rank_with_n_recorded`.
- I7. No verdict and "outcome not evaluated" everywhere. Tests: `test_outcome_check_inert_and_no_verdict`, `test_sim_report_states_outcome_not_evaluated`.
- I8. Markers are `false` only with role evidence; every case of one configuration has the same keys and record kinds. Tests: `test_privacy_marker_false_only_with_role_evidence`, `test_tdeg_sim_schema_identical_across_cases`.
- I9. The simulated bundle round-trips through the store byte-identical and stores on both backends. Tests: `test_simulated_run_round_trips_through_store_byte_identical`, `test_simulated_bundle_stores_on_both_backends`.
- I10. Every numbered rule and every invariant cites a test. Test: `test_adr0027_rules_are_tested`.
