# ADR 0012: Tool calls, stage spans, evidence, artifacts and the channel map

- Status: Accepted (SCOPE.md "Entity specs", P1-06, P1-10a, P1-10c; revision 3: ToolCallEvent timeline origin and value-object classification accepted by the user). P-05, P-31, P-32 decided by the user in revision 5, accepted as recommended; revision 5 user change: speaker roles are caller, agent, unknown. Revision 11: `bits_per_sample` optional (N27, default applied), non-empty channel maps, field types fixed (N28), run-level rules checked per bundle (N22). Revision 12: the owner decided `ToolCallEvent.source` (O2, closed set) and the `Artifact.uri` schemes (O5, ADR 0007 Decision 6c), option A both. Revision 13: O8 (loopback hosts in `http`/`https` artifact URIs) decided by the owner, option A, in ADR 0007 Decision 6d. Revision 17: N69 (the `path` and `raw` of importer warnings) decided, auto-approved under the owner's standing rule (Decision 7). Open [Proposed] items: none.
- Date: 2026-10-22 (revision 5: decided proposals folded into Decisions 1, 2 and 5)
- Task: P1-00b (implemented by P1-06, P1-10a, P1-10c)

## Context
T1/T2 sources provide tool calls, per-stage spans, usage and audio. Each has ways it can mislead us. Telemetry truncates long attribute values, so a truncated JSON argument string that is re-parsed or "repaired" becomes fabricated data. Cancelled tool calls may or may not appear in a conversation log (not verified for Pipecat). A stereo recording's channel assignment may be declared by the producer (Pipecat AudioBufferProcessor: left = user, right = bot) or inferred by us. Without knowing which channel is the agent, agent-audio metrics are meaningless.

## Decision
1. **`ToolCallEvent`** (entity `tcl`): `{name, arguments_raw?, arguments?, arguments_complete, result_raw?, result_complete?, status, seq, offset_ms?, timeline_origin?, source, external_call_id?}`:
   - `arguments_raw` is the exact source string. `arguments` (a parsed JSON object) is present only when `arguments_complete=True` and parsing succeeded.
   - `arguments_complete` is required and is never inferred as true from a payload marked or detected as truncated.
   - `status ∈ {completed, cancelled, unknown}`. A call with no result is valid (`result_raw=None`). `result_complete` is present only with `result_raw`. **A call that ran and returned an error (P-32):** `result_error: bool | None`, where `None` means the source did not report it; `status` keeps meaning only "did the call finish".
   - `seq` is unique per run. `offset_ms` requires `timeline_origin` (ADR 0002). (Revision 11, N22: a ToolCallEvent names no run, so uniqueness is checked over the events of one RunBundle by `check_tool_call_seqs(events)`.)
   - `name` is a `MachineKey`. `external_call_id` is a source identifier stored as data (ADR 0001), typed `VerbatimText` and stored verbatim (revision 11, N28).
   - **Truncation, exactly (revision 11, N28).** `arguments_complete=true` with an `arguments_raw` that is not one whole JSON object is rejected: parse failure is the only truncation the schema can detect, and `arguments` is defined as a parsed object. A complete source payload that is legitimately not a JSON object is a mapping gap (ADR 0018) recorded with an IngestWarning, never a guessed flag, and tool metrics treat it like an incomplete payload (Consequences).
   - `source` is required and is the closed set `agent_export | agent_telemetry | observer` (revision 12, O2 decided by the owner, option A; meanings in ADR 0003 Decision 1). An event whose producer an importer cannot state is a mapping gap (ADR 0018), not a guessed member.
2. **`StageSpan`** (entity `spn`): `{stage, provider?, model?, start?: UtcDatetime, duration_ms?, ttfb_ms?, exchange_index?, span_ref}`: durations are non-negative ints (ADR 0002). `span_ref` points to the raw span (trace/span ID) for evidence; it is a required `ExternalRef` (Decision 6; revision 11, N28). A latency value that the source does not provide stays absent; it is never derived from unrelated attributes. **`stage` (P-05):** the closed `UsageRecord.stage` set, plus `stage_raw: MachineKey` holding the source label whenever `other` is used, so per-stage latency and per-stage cost join on one key.
3. **`EvidenceRef`** (value object) points from a result to evidence inside the same bundle: `{kind: turn|exchange|tool_call|span|artifact_range, ref_id, start_ms?, end_ms?}`. `ref_id` is a typed ID of the matching kind. It must resolve within the bundle. For `artifact_range`, offsets are from sample 0 of the artifact.
4. **`Artifact`** (entity `art`): `{uri, sha256, size, audio_format?, turn_ref?, privacy: PrivacyMarker}`: `sha256` is 64 lowercase hex characters. `AudioFormat{container, codec, sample_rate_hz, channels, bits_per_sample?}` (value object). **`bits_per_sample` is optional (revision 11, N27; default applied, the owner did not object and may revisit):** a lossy codec (Opus, MP3) has no bit depth, so a producer omits it when the source states none, rather than inventing one; no rule ties it to a codec, because `codec` is an open value. The other four fields are required. `privacy` is required on every artifact (ADR 0016). URIs follow ADR 0007 Decisions 6 to 6c (revision 12, O5 decided by the owner, option A): schemes `http`, `https`, `s3`, `gs`, `file`; `file:` only as `file:///` plus an absolute path; no fragment; `file:` URIs rejected in `shared_export` and `benchmark` exports.
5. **`ChannelMap`** (value object on `Run.channel_maps[]`): `{artifact_id, channels[{index, role: ChannelRole}], determination: declared|inferred, method, confidence?}`. `ChannelRole` is `caller | agent | unknown | mixed` (P-31):
   - `declared` (the producer's documented layout): `confidence` is forbidden, and `method` names the declaration (for example `pipecat_audio_buffer_processor_stereo`).
   - `inferred` (our analysis): `method` and `confidence` are both required.
   - At most one channel has `role=agent`. Every `index` is less than the artifact's `AudioFormat.channels`, and indexes are unique. The artifact must be audio.
   - **`channels` is non-empty (revision 11).** A map with no channel asserts nothing, and it must not be read as role evidence (ADR 0016 Decision 5: role evidence is the set of roles found).
   - If no channel map gives exactly one agent channel, `agent_audio` is not available (ADR 0004).
   - **A mono channel carrying both speakers is `mixed` (P-31).** It leaves `agent_audio` unavailable unless diarization later adds an inferred map. A channel with role `unknown` or `mixed` counts in the human-subject derivation as stated in ADR 0016 Decision 5.
6. **`ExternalRef{system, kind, value}`** (value object) records foreign identifiers (session IDs, call IDs, trace IDs). It is never used as our ID.
7. **`IngestWarning`** (value object on `Run.ingest_warnings[]`): `{code: MachineKey, path?, raw?, message}` for every value an adapter could not map faithfully. (Revision 11, N28: `path` is `VerbatimText` locating the value in its *source*, whose form differs per mapping, for example a JSON pointer into an OTLP payload or a line number of an F8 file; each mapping's field-provenance table (P-51) states its form. `raw` and `message` are `VerbatimText`, `message` non-empty.) (Revision 17, N69 decided, auto-approved under the owner's standing rule: a warning's `path` is the location of the one source field the warning is about, as the caller passed it, and absent when no location was passed; it is never reconstructed. The cost importer takes `currency_source_path` and `amount_source_path` for this reason (ADR 0014 Decision 12c step 5). `raw` is the source value as sent, and absent when nothing was sent, for example a currency taken from a cited contract.) (Revision 20, N82 decided, auto-approved: a source value that holds a lone surrogate cannot be stored as `VerbatimText`; the importer stores it, and gives it as `raw`, with each lone surrogate replaced by U+FFFD, and records the warning `text_not_valid_unicode` for that field (ADR 0014 Decision 12c step 0; ADR 0017 Decision 1). (Revision 33, N83 decided, auto-approved: a lone surrogate in a passed path is replaced by U+FFFD the same way, with no warning of its own, because a path is a locator, not data; ADR 0014 Decision 12c step 0.) Warning codes stay open (ADR 0003 Decision 2).)

## Rejected alternatives
- **Repair truncated JSON arguments** (close brackets, drop the tail). It invents argument values and makes tool-correctness metrics score fabricated data.
- **Parsed arguments only, no raw string.** Evidence would be lost and parse failures undiagnosable. The raw form is the evidence.
- **A boolean `cancelled` flag.** It cannot express "we don't know whether it completed", which is the true state for some sources.
- **Assume channel 0 = caller, channel 1 = agent.** That is a guess. Producers differ, so it must be declared or measured.
- **Allow several agent channels.** Agent-audio metrics would become ambiguous. A second AI agent is a separate run.
- **Label a mono channel `agent` or `unknown`.** `agent` is false (the caller is on it too) and `unknown` hides that the agent *is* on it; `mixed` says what is known.
- **Confidence on declared maps.** A declaration is not a probabilistic estimate, and a confidence of 1.0 would blur the difference between declared and inferred.
- **ChannelMap as an entity with its own ID.** Nothing references a channel map by ID; it describes one artifact within one run, so identity would add references with no lifecycle.

## Consequences
- Tool-correctness metrics treat `arguments_complete=False` as missing `tool_calls` input for argument checks (not_applicable for argument equality; name/order checks can still run).

## Invariants (testable)
- I1. Truncated arguments give `arguments_complete=False` and `arguments=None`; `arguments` present implies complete. Tests: `test_truncated_args_not_complete_not_parsed`, `test_arguments_present_implies_complete`; Adversarial: A-29; `test_f1_truncated_tool_args_flagged`.
- I2. Cancelled status is representable. Tests: `test_cancelled_status`, `test_f4_cancelled_call_handling`.
- I3. The result is optional; `result_complete` requires `result_raw`. Tests: `test_result_optional`, `test_result_complete_requires_result_raw`, `test_f4_tool_call_without_result`.
- I4. Tool-call `seq` is unique per run. Test: `test_tool_call_seq_unique_per_run`.
- I5. Span durations are non-negative. Tests: `test_stage_span_nonneg`, `test_f1_ttfb_to_stage_span_no_latency_attribute`.
- I6. Evidence refs resolve inside the bundle. Test: `test_evidence_ref_resolves_in_bundle` (C).
- I7. The artifact sha256 is lowercase hex; audio format fields. Tests: `test_artifact_sha256_hex`, `test_audio_format_fields`, `test_f6_audio_format_16bit`.
- I8. Declared maps forbid confidence. Test: `test_channel_map_declared_forbids_confidence`.
- I9. Inferred maps require method and confidence. Tests: `test_channel_map_inferred_requires_method_confidence`; Adversarial: A-21.
- I10. A single agent channel. Tests: `test_channel_map_single_agent_channel`; Adversarial: A-21.
- I11. Channel index is within the channel count; the artifact is audio. Tests: `test_channel_index_within_channels`, `test_channel_map_artifact_must_be_audio`.
- I12. An unknown agent channel excludes `agent_audio`. Test: `test_unknown_agent_channel_excludes_agent_audio`.
- I13. F6 stereo left=user, right=bot maps to a declared ChannelMap. Test: `test_f6_stereo_left_user_right_bot_declared`.
- I14. External refs are never IDs. Test: `test_external_refs_never_ids`.
- I15. AgentTarget keeps declared and detected capabilities separate; declared-stack entries have the shape `{stage, provider, model}`. Tests: `test_declared_vs_detected_capabilities_separate`, `test_declared_stack_entry_shape`.
- I16. IngestWarning shape. Test: `test_ingest_warning_shape`.
- I17. A mono `mixed` channel leaves `agent_audio` unavailable. Test: `test_mono_channel_mixed_excludes_agent_audio`.
- I17a. (revision 11) `AudioFormat.bits_per_sample` is optional; a ChannelMap has at least one channel. Tests: `test_audio_format_fields`, proposed `test_audio_format_bits_per_sample_optional`, proposed `test_channel_map_channels_nonempty`.
- I18. `result_error` is `None` when the source does not report it. Test: `test_result_error_none_when_not_reported`.
- I19. `StageSpan.stage` uses the usage-stage set and keeps `stage_raw` for `other`. Test: `test_all_closed_enums_exact` (set), `test_stage_span_other_keeps_stage_raw`.
- I20. (Revision 12, O2) `ToolCallEvent.source` is exactly `agent_export | agent_telemetry | observer`, rejected otherwise by pydantic and JSON Schema. Tests: `test_all_closed_enums_exact` (row), `test_tool_call_event_source_closed_set` (P1-10c).
- I21. (Revision 12, O5) Artifact URI schemes, `file:` form and fragments: ADR 0007 I13.
- I21a. (Revision 13, O8) Loopback `http`/`https` hosts in `Artifact.uri`: accepted on records, rejected in shared and benchmark exports: ADR 0007 I13a.

## Decided items (formerly [Proposed]; all decided by the user in revision 5)
- **P-31. Mono recordings (`mixed` channel role). Decided by the user (revision 5): accepted as recommended.** Decision: a channel-role set separate from `Role`, equal to Role plus `mixed`; a mono channel carrying both speakers is `mixed`, which leaves `agent_audio` unavailable unless diarization later adds an inferred map. Reason: `Role` has no value for "both speakers", and labelling a mono channel `agent` or `unknown` is either false or loses the fact that the agent *is* on it. `mixed` also counts as human-subject evidence unless caller origin and roles rule a human out. Had it been decided otherwise (use `unknown`): mono channels look identical to undetermined channels, and reports cannot tell "we did not analyse it" from "it carries both speakers". Test (binding): `test_mono_channel_mixed_excludes_agent_audio`.
- **P-32. Tool call that ran and returned an error. Decided by the user (revision 5): accepted as recommended.** Decision: keep `status` as is and add `result_error: bool | None` (`None` = not reported). Reason: error handling is a core tool-use behaviour to evaluate; without the field a scorer must parse `result_raw`, whose format differs per agent, and a parse failure would be indistinguishable from success. Had it been decided otherwise (add `errored` to `status`): `status` mixes "did the call finish" with "did it succeed", and `test_all_closed_enums_exact` changes. Test (binding): `test_result_error_none_when_not_reported`.

## Decided items (revision 12)
- **O2. `ToolCallEvent.source`. Decided by the owner (revision 12): option A, closed `agent_export | agent_telemetry | observer`** (Decision 1; ADR 0003 Decision 1). The batch 3 tests use `agent_telemetry` and stay valid.
- **O5. `Artifact.uri` schemes, `file:` URIs and fragments. Decided by the owner (revision 12): option A** (Decision 4; exact rules in ADR 0007 Decision 6c). Its details were confirmed by the owner in revision 13.

## Decided items (revision 13)
- **O8. Loopback hosts in `http`/`https` artifact URIs. Decided by the owner (revision 13): option A.** Allowed in ordinary records, rejected in `shared_export` and `benchmark` exports (exact rules in ADR 0007 Decision 6d).
