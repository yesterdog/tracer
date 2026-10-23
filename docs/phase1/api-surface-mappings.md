# Real-sample mappings: pinned converter API, readings, ambiguities, test map (batch 6)

Author: qa, 2026-10-23. Tasks: P1-29 (F1), P1-31 (F3), P1-32 (F4, F7), P1-33 (F6) and the harness parts of P1-35 (gap register, field-provenance tables, no-fabrication test), gate item 13. Sources of truth: ADR 0018 (Decisions 2, 3, 3a, 4, 5, 7, 8, 9), ADR 0011, 0012, 0014, 0002, 0005, the owner decisions (E3, E4, N64, N76, N82), the captures under `packages/evalschema/tests/fixtures/real/` and the Pipecat 1.13.0 source (tag `v1.13.0`, every citation below was fetched on 2026-10-23).

Readings marked **P** are pinned by the tests and are qa's reading where the ADRs are silent. Ambiguities **M1..M25** at the end each carry a recommendation the lead may auto-approve. Nothing here edits an ADR, SCOPE, HANDOFF or api-surface.md.

## 0. Where the converters live and how the tests call them

Test-side code (adapters ship in later phases): `packages/evalschema/tests/support/mappings/`. Files: `__init__.py`, `common.py`, `gap_register.py`, `pipecat_otel.py` (F1), `sarvam_stt.py` (F3), `pipecat_conversation.py` (F4), `pipecat_turn_events.py` (F7), `pipecat_audio.py` (F6). Each starts with the SPDX header. The tests import them lazily (`_support_b6.mapping(name)`), so a missing converter fails the test with `ModuleNotFoundError`. Hygiene (`test_converter_modules_exist_and_are_hygienic`): no network import, no clock (`datetime.now`, `time.time`, `uuid4`), no environment or key file, no private fixture path, no file write.

Every converter takes `created_at` (a UTC string, never "now") and `privacy` (a PrivacyMarker document, applied to every Transcript and Artifact it builds) and never raises on content (ADR 0018, N82). Only a bad caller argument (for example a relative `uri`) raises the model's own error.

## 1. `common.py`

Frozen dataclasses (fields exactly as named):

- `Citation(url: str | None, retrieved_at: str | None, statement: str)`.
- `TraceEntry(record: str, field: str, sources: tuple[str, ...], derivation: str | None, citation: Citation | None = None)`. `record` is `str(entity.id)`, or `"channel_map/" + str(artifact_id)` for a ChannelMap. `field` is an RFC 6901 pointer into `record.model_dump(mode="json", exclude_none=True)`. `sources` are pointers into the input (section 1.2). `derivation` is `None` for a verbatim copy (exactly one source; equal value and type), else a name from section 1.3.
- `GapRow(fixture, source_key, classification, kind, reason, owner)`: `fixture` F1..F8; `source_key` starts `"<fixture>:"`; `classification` `blocking | non-blocking`; `kind` `unmapped | lossy | absent_in_source`; `reason` at least 20 characters of text; `owner` one of schema-architect, dev, indic-linguistics, staff-reviewer, qa, security-licensing.
- `TableRow(target, kind, source, derivation, citation, note)`: `target` is `Class.field.path` with `*` for list positions and one `*` for everything below the open fields `raw` and `arguments`; `kind` is `source | derivation | absent | user_declared | constant`.
- `MappingResult(fixture, records, channel_maps, external_refs, external_ref_sources, warnings, gaps, consumed, trace)` plus read-only properties `transcripts, turns, exchanges, stage_spans, tool_calls, usage_records, artifacts, cost_records` (tuples of the entities of that class, in creation order). `records` holds entities only. `warnings` are `IngestWarning`. `gaps` are the dynamic `GapRow`s a run raises (N76 and the cache cases). `consumed` are pointers read as decision input that are not copied. `external_ref_sources[i]` is the pointer of `external_refs[i].value`.

### 1.1 What must be traced

Every non-null scalar leaf of every record's JSON dump has a TraceEntry, except: `/id /schema_version /created_at /metadata /metadata_flags /run_id /transcript_id /privacy /uri /contains_bidi_controls /segments/*/contains_bidi_controls /timeline_origin/ref /artifact_id` (caller-supplied or computed by the framework). The entry's derivation must have a row in the converter's `PROVENANCE_TABLE` with the same target pattern and derivation (P-51). `declared_language` is never filled (no declaration is supplied in these mappings).

### 1.2 Source pointers

F1: RFC 6901 pointer into the OTLP document. F3: into `{"request": <request.json>, "response": <parsed body>, "meta": <response_meta.json>}`. F4: into the list (`/3/content`). F7: into the list of event objects. F6: `wav:bytes`, `wav:riff`, `wav:fmt/format_tag|channels|sample_rate_hz|bits_per_sample|byte_rate|block_align`, `wav:data/length`. A pointer may continue inside a JSON text: `ptr#ptr2` parses the string at `ptr` and continues (chained: `ptr#ptr2#ptr3`). A warning's `path` uses the same forms and its `raw` is the source value as sent (lone surrogates shown as U+FFFD, N82).

### 1.3 Derivation names (closed set; the checker re-computes each one)

`otlp_int_to_int`, `unix_nano_to_utc_us` (truncate toward the earlier microsecond), `ns_interval_to_ms_floor`, `seconds_to_ms_floor` (floor of `Decimal(repr(float))*1000`), `float_to_decimal_string` (canonical, shortest repr), `input_tokens_gross_minus_cache`*, `input_tokens_net_as_reported`*, `span_name_to_stage`, `order_index` (not re-computable; a dedicated test pins it), `parent_span_turn_number`, `documented_stereo_left_user`*, `documented_stereo_right_bot`*, `documented_mono_mixed`*, `wav_pcm_codec`, `wav_container`, `sha256_of_bytes`, `byte_length`, `language_label_to_tag`, `provider_from_endpoint_host`, `lone_surrogate_replaced`, `documented_start_of_turn`* (gives "start"), `documented_conversation_stage`* (gives "agent_context"), `iso_offset_to_utc_us`, `tool_status_from_context_message`* (not re-computable), `merged_one_billable_event`* (sum; citation on the entry, supplied per call), `placeholder_sentinel_ignored`* (an `absent` row), and `constant:<value>` for a value fixed by the mapping (compared as text). Names marked * rest on a published contract: the table row and the trace entry carry a `Citation` with an https URL and a `retrieved_at` date (the merge citation is on the entry only).

Citations used (all fetched from the `v1.13.0` tag, 2026-10-23): `metrics/metrics.py` (LLMTokenUsage: net for Anthropic and Bedrock, gross for OpenAI-compatible; STTUsage), `processors/audio/audio_buffer_processor.py` (stereo: user left, bot right; mono: mixed), `processors/aggregators/llm_response_universal.py` (turn timestamp = turn start; content = the aggregated transcript used in the context; `IN_PROGRESS` and `CANCELLED` tool messages), `evals/conversation.py` and `evals/conversation_observer.py` (F4 format, no timing, no tool results, empty bot turn is not a turn, both sides are context writes), `utils/tracing/service_decorators.py` (`unknown` model fallback, `NOT_GIVEN`), `observers/turn_tracking_observer.py` (turn count increments before it is reported: 1-based).

## 2. F1: `pipecat_otel.map_f1(document, *, run_id, created_at, privacy, one_billable_event=None) -> MappingResult`

`run_id` goes on every UsageRecord. `one_billable_event` is a `Citation` the caller supplies for the N64 merge.

- **Exchange** per `turn` span with an integer `turn.number`: `index = turn.number` (1-based, never Turn.seq), `started_at` = span start (microsecond truncation), `duration_ms` from `turn.duration_seconds` (floor ms, P, M6), `was_interrupted` verbatim, `source = telemetry`.
- **StageSpan** per `stt|llm|tts` span: `stage`, `provider = gen_ai.provider.name`, `model = gen_ai.request.model` unless a placeholder (`unknown`, `NOT_GIVEN`; then `None` and warning `placeholder_value_ignored`, P, M3), `start`, `duration_ms` = floor((end-start)/1e6), `ttfb_ms` only from `metrics.ttfb` (floor ms), `exchange_index` from the parent turn span only, `span_ref = ExternalRef(otel, span_id, spanId)`.
- **Transcript 1** (`agent_telemetry`): Turns in span-start order (ties: document order), `seq` 0-based: stt final text as caller / `agent_stt`; tts `text` as agent / `agent_tts_input`. No timing, no `wall_time`, no language (P, M1, M2). Interim stt (`is_final` explicitly false) is skipped with `interim_transcript_skipped` (P, M23). **Transcript 2** (`agent_telemetry`): llm `output` as agent / `agent_llm_output` (P, M25).
- **UsageRecord** (domain `agent_under_test`, source `telemetry`, `occurred_at` = span start, `raw` = the usage attributes as received): llm tokens (cache semantics below); stt `metrics.audio_seconds` as `audio_input_seconds` (P, M5); tts `metrics.character_count` as `input_characters` when the model is named. A span whose model is a placeholder gets no UsageRecord.
- **Cache semantics (P, M4):** provider `openai` is gross: `input_tokens = prompt - cache_read - cache_creation` (only the counts present), `cache_read_input_tokens` and `cache_write_input_tokens` as reported, `output_tokens`. Providers `anthropic` and `aws.bedrock` are net: counts as reported. Any other provider with a cache count: no UsageRecord, warning `cache_semantics_unknown`, one blocking `GapRow`. Cache larger than input, or a count that is not a non-negative integer: no UsageRecord, warning `usage_inconsistent`, one blocking `GapRow`.
- **N64 / N76:** the same usage attribute key twice in one span is "repeated". With a complete `one_billable_event` (url and retrieved_at both non-empty) the counts are summed into one quantity (derivation `merged_one_billable_event`, both pointers as sources, the citation on the entry). Otherwise no UsageRecord for that span, one warning `usage_quantities_not_merged` (path = pointer of the second occurrence's attribute object, raw absent), one blocking `GapRow` (`F1:llm.<key>`). Two spans are two records.
- **ToolCallEvent** only from the `input` JSON of llm spans (never from `tools`, `tool_count`, `tool.*`): one event per distinct tool_call id, `seq` by first appearance, the occurrence that has a result wins, a different name or arguments under the same id keeps the first and warns `tool_call_conflict`. `arguments_raw` verbatim; `arguments` and `arguments_complete=true` only for a whole JSON object, else `false`, `arguments` absent, warning `tool_arguments_incomplete` (path = chained pointer to the string). Status from the tool message (P, M20): none -> `unknown`; `IN_PROGRESS` -> `unknown` + `tool_result_placeholder`; `CANCELLED` -> `cancelled`; JSON object with `type = async_tool` -> `unknown` + `async_tool_payload`; any other content -> `completed` with `result_raw`. `result_complete` and `result_error` stay `None`. `external_call_id` = the id. An unparseable `input` -> `llm_input_unparseable`; a tool message with no call -> `tool_result_without_call`.
- **External refs:** the first `traceId` of each trace (`otel`/`trace_id`) and `conversation.id` (`pipecat`/`conversation_id`).
- Span names outside stt/llm/tts/turn/conversation: warning `unknown_enum_value` (raw = the name). E3: an instant finer than a microsecond, and every ms value cut from a finer number, is truncated and warned `time_precision_reduced` (raw = the source value as sent).

## 3. F3: `sarvam_stt.map_f3(request, body, meta, *, created_at, privacy, audio_artifact_id=None)`

`request` = `request.json`, `body` = `response_body.json` bytes, `meta` = `response_meta.json`.

- Non-200 or `{"error": ...}`: no records; warning `provider_error_response` (raw = `error.code`, path `/response/error/code`); the request id as an external ref; the provider message is not copied. A body that is not JSON, not an object or has a non-text transcript: `body_not_json`.
- Success: one **Transcript** (`our_stt`, provider `sarvam` from the endpoint host, `model` only from the request's `model` field (never the docs default), `provider_call_ref = ExternalRef(sarvam, request_id, id)`, `language_confidence` = `language_probability`). One **Turn** (role `unknown`, `our_stt`, `timing_source none`, text verbatim); none for an empty transcript (warning `empty_transcript`, P, M14).
- **Language (P, M13):** a forced request (`language_code` other than `unknown`/absent) echoes the label: `detected_language(_raw)` stay empty, warning `language_label_is_request_echo`. An empty transcript is no detection evidence. Otherwise the importer trims (`str.strip`), calls `normalize_language_label`, stores the label as received (untrimmed, lone surrogates replaced) in `detected_language_raw`, the tag in `detected_language`, ONE warning per label (`language_label_normalized` or `language_label_unparseable`, raw = the label as received, path `/response/language_code`). A null label stays empty with no warning. A request with `mode` other than `transcribe` adds `label_not_transcript_language`.
- **Segments:** `timestamps` arrays of equal length give phrase Segments (text verbatim, start/end seconds floored to ms, warning `time_precision_reduced` when cut). Offsets need `audio_artifact_id` (then `timeline_origin = {artifact, ref}`); without it the segments keep text only and `timeline_origin_unknown` is warned. Unequal arrays: no segments, `timestamps_arrays_mismatch`. Segment confidence stays `None`.
- No UsageRecord and no cost, ever (the response has no usage, duration or price).
- N82: a lone surrogate in a label or text is replaced by U+FFFD and `text_not_valid_unicode` records it.

## 4. F4: `pipecat_conversation.map_f4(conversation, *, created_at, privacy)`

Transcript `observer`; user -> caller, assistant -> agent, both `agent_context` (P, M8), `seq` 0-based over turns only, no timing. `tool_call` -> ToolCallEvent: name, `arguments` object, `arguments_complete=true`, `arguments_raw` absent (P, M9), no result fields, `status = cancelled` only for `cancelled: true`, else `unknown`; a non-boolean `cancelled` warns `unknown_enum_value`. `source observer`, `seq` over tool calls, 0-based. Unknown role: `unknown_enum_value`; missing content/name or a non-object entry: `malformed_entry`. Empty list: nothing. Registered gaps: `F4:assistant.content` (absent_in_source), `F4:tool_call.@position`.

## 5. F7: `pipecat_turn_events.map_f7(events, *, created_at, privacy)`

Transcript `observer`. `on_user_turn_stopped` / `on_assistant_turn_stopped` give Turns in file order (`seq` 0-based), `agent_context`, text verbatim, `wall_time = {at, meaning: start}` (documented), assistant `interrupted` from the message, user `None`, `exchange_index` always `None` (P, M11), an empty assistant text is kept (P, M10). `on_turn_started/ended` give one Exchange per `turn_number` (`observer`, `duration_ms` floored, `was_interrupted` verbatim, `started_at` absent; a started-only turn has neither duration nor flag). Timestamps: offset converted to UTC; naive -> `naive_timestamp`; text that is not an instant or a number -> `timestamp_not_parseable`; finer than a microsecond -> truncated + `time_precision_reduced`; missing -> nothing. `content: null` -> `turn_text_missing`. Duplicate turn number -> first kept + `duplicate_turn_number`. Unknown event -> `unknown_enum_value`. `harness_observed_at_utc`, `seq`, `source` are never read as data.

## 6. F6: `pipecat_audio.map_f6(wav, *, uri, created_at, privacy)`

Only 16-bit integer PCM WAV is mapped; anything else (float, 8/24-bit, extensible, truncated, empty, not RIFF): no records, warning `wav_format_not_supported`. Artifact: `uri` and `privacy` from the caller, `sha256` and `size` of the bytes, `container wav`, `codec pcm_s16le`, rate, channels and bits from the header. ChannelMap `declared`, no confidence: 2 channels -> `[0 caller, 1 agent]`, method `pipecat_audio_buffer_processor_stereo`; 1 channel -> `[0 mixed]`, method `pipecat_audio_buffer_processor_mono`; more -> Artifact only, warning `channel_layout_not_documented`. Roles are documentation-derived, never inferred from samples.

## 7. Gap register: `gap_register.GAP_REGISTER`, `KNOWN_SCHEMA_GAPS`

`KNOWN_SCHEMA_GAPS` is exactly the non-blocking rows (M16). Keys are unique; wildcard `*` allowed (`fnmatch`). Required by name (tests): `F1:turn.turn.user_bot_latency_seconds`, `F1:stt.language`, `F1:llm.gen_ai.request.top_k`, `F1:tts.metrics.character_count`, `F4:assistant.content` (`absent_in_source`), `F4:tool_call.@position`, `F7:*.@harness_observed_at_utc`, `F3:response.language_code`. Every other source key of every real fixture must be traced, consumed, quoted by a warning, an external ref, or have a row (`test_every_source_field_mapped_or_gapped`). On an F3 error response the `request.*` keys are not required (no transcript is produced). Zero rows may be `blocking`.

## 8. Warning codes (closed; `_support_b6.WARNING_CODES`)

`time_precision_reduced placeholder_value_ignored usage_quantities_not_merged cache_semantics_unknown usage_inconsistent tool_arguments_incomplete tool_call_conflict tool_result_placeholder tool_result_without_call async_tool_payload llm_input_unparseable interim_transcript_skipped unknown_enum_value naive_timestamp timestamp_not_parseable turn_text_missing language_label_normalized language_label_unparseable language_label_is_request_echo label_not_transcript_language empty_transcript timestamps_arrays_mismatch timeline_origin_unknown provider_error_response text_not_valid_unicode wav_format_not_supported channel_layout_not_documented body_not_json malformed_entry duplicate_turn_number`.

## 9. Test map (counts are pytest items; 513 collected, 18 green today)

| File | Items | Tests (SCOPE binding names in bold) |
|---|---|---|
| `test_mapping_f1.py` | 73 | **test_f1_validates, test_f1_turn_number_to_exchange_index, test_f1_stt_transcript_source_agent_stt, test_f1_tts_text_source_agent_tts_input, test_f1_llm_usage_to_usage_record, test_f1_cache_read_bucket_disjoint, test_f1_ttfb_to_stage_span_no_latency_attribute, test_f1_truncated_tool_args_flagged, test_f1_repeated_unit_quantities_merged_only_for_one_billable_event**; extras: turn linking, stt language not an observation, llm output transcript, interim stt, cache semantics per provider (6 cases), unknown-provider and inconsistent cache, placeholder model, NOT_GIVEN, time precision (E3), known values, external refs, tool call (real), tool definitions are not calls, repeated call dedupe, tool status (4 cases), orphan result, unparseable input, N76 per call, unknown span, empty doc, malformed usage, prompt injection, no parent, secret leakage, 1,200-span document, seq order |
| `test_mapping_f3.py` | 166 | **test_f3_phrase_segments, test_f3_null_language_code_is_none, test_f3_no_confidence_stays_null, test_f3_bn_IN_label_normalized_raw_kept**; extras over all 38 captures: validates, no usage/cost, language only as detection, errors (6), label variants (8), lone surrogates (N82), segments without artifact, multi-phrase ms truncation, mismatched arrays, malformed bodies (5), joiners/NFC, bidi, injection, huge transcript, secret |
| `test_mapping_f4_f7.py` | 44 | **test_f4_no_timestamps_timing_none, test_f4_tool_call_without_result, test_f4_cancelled_call_handling, test_f4_assistant_source_agent_context, test_f7_turn_ended_interrupted_to_exchange, test_f7_naive_timestamp_warning_not_crash**; extras: validates, F4 vs F7 empty bot turn, unknown role/malformed, surrogates, F7 turn messages, harness fields, timestamp forms (8), missing text/unknown event, started-only/duplicate, large logs |
| `test_mapping_f6.py` | 25 | **test_f6_stereo_left_user_right_bot_declared, test_f6_audio_format_16bit**; extras: validates, not inferred from samples, mono, 3 channels, 8 kHz, malformed/unsupported audio (9), empty recording, 20 MB, uri |
| `test_mapping_no_fabrication.py` | 205 | **test_no_fabricated_values_all_mappings** (F1x3, F3x38, F4x3, F7x3, F6x3, 8 synthetic), **test_adapter_unknown_enum_value_becomes_warning, test_adapter_malformed_language_label_recorded_as_warning, test_provenance_json_schema_all_fixtures, test_gap_register_zero_blocking, test_known_schema_gaps_have_owners_and_are_nonblocking**; extras: coverage of every source key, determinism, register well-formed and named rows, tables well-formed, contract citations, hashes of the clips, no secret in fixtures, converter hygiene, 12 checker/registry self-tests (green now) |

The 18 tests green today are the 4 `test_provenance_json_schema_all_fixtures`, the clip-hash test, the secret scan and the 12 self-tests of the checkers; every other test fails with `ModuleNotFoundError: No module named 'tests.support.mappings'` except the hygiene test, which fails on the missing modules.

## 10. Deferred (not written here)

- F2 Langfuse (P1-30): `test_f2_cost_details_reported_by_telemetry_platform`, `test_f2_session_id_external_ref`, `test_documented_currency_contract_requires_citation`.
- F5 Pipecat Evals YAML (P1-34): `test_f5_scenario_keys_mapped_or_gapped`, `test_f5_min_score_to_per_turn_share`, `test_f5_measure_range_inclusive`.
- F8 VoicEra (P1-36, private fixture and public twin): all `test_f8_*`, `test_no_private_text_in_committable_files`. Nothing in this batch reads the private fixture.
- Gap-register rows for F2, F5, F8 (they add rows when their mappings exist).

## 11. Ambiguities and recommendations (auto-approvable)

- **M1** F1 Turns carry no timing or wall_time (a stage span marks when the service worked, not when someone spoke). Recommend accept.
- **M2** The stt `language` attribute is "Detected or configured" (Pipecat docstring): not stored as an observed tag; non-blocking gap, owner indic-linguistics. Recommend accept.
- **M3** `unknown` and `NOT_GIVEN` are Pipecat placeholders (cited). Model absent; a span with no named model gives no UsageRecord because `UsageRecord.model` is required (the TTS characters of every capture are lost). Recommend accept now and ask the architect whether `UsageRecord.model` should become optional (a schema change that would keep the usage and price it `no_price_for_model`); if yes the tts rows become records and the gap row disappears.
- **M4** Cache semantics table: gross `{openai}`, net `{anthropic, aws.bedrock}`, anything else unsplittable (no record, blocking gap). Citation: Pipecat `LLMTokenUsage`. The real captures use `openai` through a local OpenAI-compatible server; the contract is the service class's, not the backend's. Recommend accept.
- **M5** stt `metrics.audio_seconds` ("client-measured seconds of audio submitted") and tts `metrics.character_count` are telemetry-reported usage, not a recording duration. Recommend accept.
- **M6** `Exchange.duration_ms` from `turn.duration_seconds` rather than the span interval. Recommend accept.
- **M7** `turn.user_bot_latency_seconds` has no target field; registered non-blocking because latency metrics compute from turn timing (ADR 0004 6f). staff-reviewer must confirm; if an MVP-1 metric consumes Pipecat's value it is blocking and needs an Exchange field.
- **M8** F4 user turns are `agent_context`, not `agent_stt` as ADR 0011 (P-27 example) says: the observer records context writes for both sides and typed RTVI text is a user turn that never passed STT. Recommend accept and amend the ADR 0011 example.
- **M9** F4 tool call: `arguments_raw` absent, `arguments_complete=true`; `cancelled:false` is `unknown` (not completed). Recommend accept.
- **M10** F7 keeps empty assistant turns (text ""), F4 cannot have them. Recommend accept.
- **M11** F7 states no link between a message and a `turn_number`; `Turn.exchange_index` stays `None` (ordering heuristics would misattribute, see en seq 9). Non-blocking gap. Recommend accept.
- **M12** F7 `wall_time.meaning = start` from the aggregator docstring (cited). Recommend accept.
- **M13** F3: a forced language echo is not a detection; silence is not evidence; translate mode stores the spoken-language label with a warning. A forced code has no home in Transcript (registered gap). Recommend accept; optional later field `requested_language`.
- **M14** F3 role `unknown`, one Turn, none for empty transcripts; segment offsets only with a caller-supplied artifact id. Recommend accept.
- **M15** `language_probability` -> `Transcript.language_confidence` (the task text says "confidence null": read as segment/turn confidence). Recommend accept.
- **M16** The gap register lives in `gap_register.py`; `KNOWN_SCHEMA_GAPS` is its non-blocking rows. The P1-16 harness may define its own `KNOWN_SCHEMA_GAPS` for pydantic-vs-JSON-Schema divergences; if so, rename mine. Recommend the lead confirm.
- **M17 (repo action)** `test_f3_bn_IN_label_normalized_raw_kept` (binding name) contains an excluded-language tag: `test_mapping_f3.py` needs one T-DRIFT allowlist entry (ADR 0005 Decision 10, binding-name rule, like `test_results_language.py`). The other batch 6 files assemble tokens from parts. The T-DRIFT test does not exist yet.
- **M18** Warning codes and path forms of section 8 are qa's names. Recommend accept.
- **M19** E3 reading: truncate toward the earlier value; `*_ms` floor; warn only when digits are lost.
- **M20** Tool status rules (placeholder content is not a result; async payload unknown). Recommend accept.
- **M21** F6 supports only 16-bit PCM; mono = one `mixed` channel; more than two channels no map.
- **M22** "Repeated" = the same OTLP attribute key twice in one span; the citation is passed as `one_billable_event`.
- **M23** `is_final=false` is interim and skipped; absent is not interim.
- **M24** The result shape (`consumed`, `external_ref_sources`, trace) is test infrastructure for P-51, not a schema.
- **M25** F1 produces two transcripts (conversation view; llm output view) and orders turns by span start.

## 12. Validation and its limits

Throwaway oracle converters (scratchpad, outside the repo) make all 513 tests pass on Python 3.11 to 3.14 (`oracle-green-*` logs). 126 single-change mutants of the oracle (F1, F3, F4, F7, F6, register, table and hygiene changes) were run; the first run left four survivors (two were no-op mutants, rewritten); two tests were added for the other two; the final run in `mutants.txt` kills 126 of 126. Red logs on 3.11 to 3.14: 495 failed (494 `ModuleNotFoundError`, 1 hygiene), 18 passed. ruff, ruff format, mypy --strict, check_headers, guard_private_fixtures and check_licenses pass for the whole repo.

Not verifiable: the oracle and the tests share one author, so a misreading of an ADR would sit in both; the derivation registry re-uses `normalize_language_label` and `datetime.fromisoformat`; JSON-Schema validity of the records (the P1-16 harness) is not asserted here; Hindi and Sarvam Indic text is `needs_native_review` and is only copied, never compared to a reference; `order_index` and the tool status are pinned by dedicated tests, not by the generic checker.
