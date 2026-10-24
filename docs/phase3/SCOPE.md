# Phase 3 scope: recorded-audio scoring, mr base case

Status: Scoped 2026-10-25, not started

Source: the Phase 3 scoping report of the staff-reviewer (2026-10-25), reproduced faithfully
below. The only edits are the ones listed under Decisions (D1 to D5), third-person wording in the
reviewer's notes, repository-relative paths (`<repo>`), and languages named by their codes (the
in-scope languages other than mr are "the other eight in-scope languages"). Test names are
reproduced exactly.

## Decisions

- **D1. Q1 to Q22 auto-approved.** Every recommendation in section C (Q1 to Q22) is
  AUTO-APPROVED by the lead on the recommended option (owner instruction, 2026-10-23), as logged
  in the decision log entry "PHASE 3 SCOPE DELIVERED" (2026-10-25):
  - Q1 three packages (`tracer-evalcore` pure, `tracer-evalproviders` HTTP, `tracer-cli`);
  - Q2 httpx;
  - Q3 REST plus a deterministic client chunker of at most 25 s, cut at the lowest-RMS 20 ms
    frame (the Batch API is a backlog row);
  - Q4 `language_code=unknown`;
  - Q5 MER as unit `ratio`;
  - Q6 flags in `MetricResult.metadata["tracer.flags"]` (`tracer.flags/1`) plus evidence refs;
  - Q7 confidence = the normalised character edit distance of the span keys, uncalibrated, and
    documented as not a probability;
  - Q8 binding parameters `flag_min_confidence` and `normaliser` with no defaults, plus one
    required rubric item on the token share with the owner's `range{max}` budget (the two values
    are owner decisions, D2);
  - Q9 workload unit = share of export tokens (turns with flags are also shown);
  - Q10 both roles of the agent export are evaluated, with the ADR 0016 amendment;
  - Q11 the normaliser drops ZWJ/ZWNJ in the comparison key only (display kept);
  - Q12 report audio by relative reference by default, embedding opt-in (`--embed-audio`, with a
    warning), one hashed inline seek script under a strict CSP (`default-src 'none'`); relative
    audio playback under `file://` is unverified (manual check in P3-23);
  - Q13 an honest evaluator text, a Persona holding only the declared language, and
    `max_duration_ms` = 14,400,000 (the schema maximum, stated as such);
  - Q14 STT failure is all-or-nothing: `not_applicable` `missing_input[transcript]`, a Run
    `IngestWarning` and a non-zero exit;
  - Q15 `model` always sent explicitly and recorded as requested;
  - Q16 an empty chunk makes no Turn and is counted in the transcript metadata;
  - Q17 a required `--out`, the extended guard (P3-34) and `.gitignore` entries;
  - Q18 chunk size is an adapter parameter with a documented 25 s default (a technical margin,
    not a rubric value);
  - Q19 `occurred_at` = the response `Date` header;
  - Q20 no RunVerdict in Phase 3 (backlog row);
  - Q21 native 24 kHz is sent and verified on the first chunk of R1;
  - Q22 the F8 mapper is promoted into evalcore with a differential test, `tests/support`
    unchanged.

  Each is logged with its rejected options and trade-off (section C), and ADRs 0023 to 0025 and
  the ADR 0016/0004 amendments record them as tasks P3-00a to P3-00d.
- **D2. Owner-only items (P3-29).** These are not auto-approved and nothing is assumed for them:
  (1) the value of `flag_min_confidence`; (2) the review budget (the `range{max}` on the flagged
  token share); (3) Sarvam terms of service 10.5(a): written permission from Sarvam, or explicit
  risk acceptance; (4) the status of the Sarvam 17.5 training opt-out; (5) the consent status to
  record for the callers. They are asked one at a time in chat before the first real send
  (P3-30), and (1) and (2) are pre-registered in the decision log before any real result is seen
  (no tuning on the two clips, BL-170). BACKLOG rows BL-188 (10.5(a)) and BL-189 (17.5) carry
  the two terms gates with status `owner`.
- **D3. Bounded dependency ranges, not exact pins.** Per the owner's O3 decision (as applied to
  `tracer-evalschema` and, in Phase 2 D2, to `tracer-evaldb`), every third-party runtime
  dependency of the three new packages is a bounded range, each tested at its lower bound (CI
  `lowest-direct` leg); for Phase 3 that is `httpx>=0.28.1,<1`. P3-01 may lower a bound only when
  the whole suite passes at that version on the lower-bound leg. `tracer-evalschema==0.0.1` and
  `tracer-evaldb` stay exact workspace pins, as in Phase 2 (the schema-version pin of the data,
  not a library range). The test is `test_phase3_requires_dist_bounded_ranges`. Rejected: exact
  pins (they conflict with O3 and block users on other patch versions); unbounded ranges (an
  untested major version could change behaviour). Trade-off: one more CI leg.
- **D4. Baseline for "evalschema unchanged".** The Phase 3 baseline is the Phase 2 completion
  commit `e942b3b`. P3-01 records its `schema.lock` sha256 and the evalschema source tree hash in
  its evidence, and `test_evalschema_unchanged_since_phase3_baseline` (P3-01, gate item 3)
  compares against those recorded values.
- **D5. No schema change in Phase 3.** `tracer-evalschema` 0.0.1 stays byte-unchanged. Every
  schema gap found in this scope (a requested language on `our_stt`, a typed flag record or
  character-span evidence, a Persona that is optional for recorded runs and a rubric with no
  required item) goes to the 0.0.2 backlog (BL-180, BL-181, BL-182).

---

**What the reviewer read.**
- The plan file; `docs/HANDOFF.md` lines 1-122 only (sections 1-4 and Phase 2 status; lines
  123-265 not read); `docs/BACKLOG.md` (held-off index, sections 6 and 7, BL-130 to BL-178).
- `docs/phase2/SCOPE.md` structure and section 1.2.
- ADR 0016 Decision 1, plus greps of ADR 0005, 0010 and 0018; P1-02 licence review section 8.
- The decision log, 2026-10-24 to 2026-10-25 entries.
- Source: `metrics.py`, `results.py`, `transcript.py`, `events.py` (EvidenceRef), `rubric.py`,
  `snapshot.py` (greps), `usage.py`, `run.py`, `bundle.py` (tier code), `evaluator.py`,
  `persona.py`, `agent.py`, `artifact.py`; `tracer_evaldb.repository` function list.
- Test-support mappers `voicera_transcript.py` (F8) and `sarvam_stt.py` (F3).
- Sarvam captures (`stt_mr_forced`, `stt_hi_timestamps`, `provenance.json`),
  `tools/capture/SARVAM_OBSERVATIONS.md`, `tools/capture/pyproject.toml`; `mr.json` language
  fixtures.

**Fetched on 2026-10-25.**
- Sarvam STT REST docs and Batch STT docs.
- The Sarvam pricing page.
- The Sarvam terms of service (v2.0, effective 2026-07-29).
- PyPI JSON for `httpx` and `sarvamai`.

**Not done.** The reviewer did not run the suite, `git status` or `gh`, and did not open the
private folder, `.env.local` or the keys notes. The reviewer wrote nothing to the repository.

---

## Findings that change the brief (read first)

**F1. Sarvam REST STT cannot take the two clips whole (verified in the docs).**
- The REST page says it is "For quick responses under 30 seconds" and points longer audio to the
  Batch API. The Batch API takes files "up to 2 hours" and 20 files per job, and supports
  diarization.
- The clips are 254.6 s and 437.2 s, so they must be cut into chunks or sent through Batch.
- What happens over 30 s (rejected or cut short) is **unverified**. The 2026-10-23 captures never
  sent more than 12 s.
- The Batch page does not list REST paths for the upload and start steps (only
  `POST /speech-to-text/job/v1`, `GET .../{job_id}/status` and `POST .../download-files`).
- The `sarvamai` SDK has `license: null` and no licence classifier on PyPI.
- Recommendation (Q3): REST with deterministic chunks of 25 s or less, cut on the client.

**F2. Language: with a forced `mr-IN` code, the STT transcript can never be scored.
Recommendation: send `unknown`.**
- A forced `language_code` is echoed back, not detected (captures and the F3 mapper agree).
- So an `our_stt` transcript made with a forced code has no observed language.
- It also cannot carry a declaration: `DECLARABLE_SOURCES` excludes `our_stt`
  (`transcript.py:89`).
- A metric with `languages=["mr"]` that reads it is therefore always `language_not_observed`
  (`results.check_language`).
- Smallest fix with no schema change: send `language_code=unknown`. Sarvam then observes the
  language per chunk, and `language_probability` is recorded.
- The export carries a user-declared `mr` (from a required CLI flag), so the results have
  `language_source=declared` and the definitions must be `script_sensitive=false`.
- Consequence: if any chunk is detected as another language, the whole result is
  `unsupported_language`. That is not-applicable, never a failure.
- The 0.0.2 fix (a requested language on `our_stt`) becomes a backlog row (Q4).

**F3. A recorded human call can only be represented with three workarounds.**
- `Run.config_snapshot_id` is required.
- `ConfigSnapshot` requires an `Evaluator`, a `Persona` (docstring: "the simulated caller") and a
  `Rubric`.
- `validate_rubric` runs inside the snapshot. It needs at least one required item, and a required
  item needs a pass rule.
- `Evaluator.instructions` and `expected_outcome` must be non-empty.

Smallest option with no schema change:
1. An honest evaluator text that describes the recorded-scoring task.
2. A `Persona` that holds only the user-declared call language.
3. **The owner chooses a review budget**: `range{max}` on the flagged-token-share metric, as the
   one required rubric item.

The third item is an owner number. Without it the run cannot be built. The reviewer does not
invent one (Q8, Q13). The real gap goes to the 0.0.2 backlog: a Persona that is optional for
recorded runs, and a rubric with no required item.

**F4. Flag spans, signal and confidence have no typed field in 0.0.1.**
- `EvidenceRef` is `{kind, ref_id, start_ms?, end_ms?}` with `extra="forbid"`: no character span,
  no confidence, no signal.
- What it can hold: one evidence ref per flagged export turn, and artifact refs with ms ranges.
- Recommended home for the rest: `MetricResult.metadata["tracer.flags"]`, a versioned JSON shape
  `tracer.flags/1` owned and validated by the new package.
- It holds only integers, decimal strings and short machine keys. No text and no ID strings, so
  `metadata_flags` stays empty.
- This is representable but untyped. Typing it is a 0.0.2 backlog row (Q6).

**F5. Units for "agreement distance": `ratio` fits, `wer` and `cer` must not be used.**
- `UNITS` = `ms`, `count`, `ratio [0,1]`, `percent`, `wer`, `cer`.
- ADR 0010 Decision 1 defines `wer` and `cer` as error rates ("may exceed 1"), which presume a
  reference transcript. BL-165 forbids that claim.
- The value is the Match Error Rate, MER = (S+D+I)/(H+S+D+I) (Morris, Maier and Green 2004). It
  is symmetric when the two texts swap and always lies in [0,1], so `unit="ratio"` fits.
- Flag count uses `count`; workload share uses `ratio`.

**F6. Roles conflict with ADR 0016 Decision 1.**
- ADR 0016 Decision 1 says "metrics evaluate only `agent` turns".
- The export's `user` lines are the agent's own STT output for the caller's speech, and these are
  probably the agent's commonest transcript errors.
- Recommendation (Q10): amend ADR 0016 to say that a turn of an agent-produced record
  (`source=agent_*`) is an agent output whatever its speaker role. The caller is still never
  evaluated.
- The BL-145 test name becomes `test_metrics_evaluate_agent_outputs_only`; record the rename in
  BL-145.

**F7. Run tier is T0 (verified in code).** `bundle._record_rank` ranks `agent_export` transcripts
and `timing_source` `none` or `audio_alignment` as T0, so `observed_tier=T0`. `available_inputs`
= `audio`, `transcript`, `agent_transcript`.

**F8. Cost will be `not_available` unless the owner supplies a USD price.**
- Sarvam prices in INR only: ₹0.49 per minute pay-as-you-go for real-time and batch STT (pricing
  page, retrieved 2026-10-25). Schema 0.0.1 is USD-only.
- Rounding and minimum charge per request are not stated, which is `billing_rules_missing`.
- Unverified: if each request is rounded up to a minute, about 28 chunks cost about ₹13.7,
  against about ₹5.6 billed by the second.

**F9. Sarvam terms of service (v2.0, 2026-07-29). This is not legal advice.**
- **10.5(a)** forbids using "any content, data, output ... received or derived from the
  Offerings, to directly or indirectly develop, train, test, fine-tune or otherwise improve any
  machine learning algorithm or artificial intelligence system ... except as expressly permitted
  by us in writing".
  - Using Sarvam's transcript to judge a voice agent's transcript plausibly reads as "test ...
    any artificial intelligence system" on a literal reading.
  - The reviewer **cannot determine** whether it is barred. The clause names its own exit:
    written permission from Sarvam.
- **10.2** is about audio Output (TTS, dubbing). STT returns text and the input audio is the
  owner's, so 10.2 does not appear to apply. That reading is unverified.
- **17.5**: Sarvam may train on Inputs "subject to your consent, which you may decline or
  withdraw". Real helpline audio could become training data unless the owner declines; how to
  decline is unverified.
- **8.2**: processing and storage happen in India. **15.2**: retention is at Sarvam's
  discretion.
- These are owner gates before the first real send (P3-29).

**F10. Sarvam word timing and diarization (verified in the docs).**
- REST timestamps are chunk or phrase level, never word level. "Speaker diarization is not
  supported on the REST API".
- BL-176 can be resolved with this docs citation.
- Text alignment does not need either.

**F11. httpx 0.28.1 is the latest release, uploaded 2024-12-06.** Licence BSD-3-Clause;
dependencies `anyio`, `certifi` (MPL-2.0), `httpcore==1.*`, `idna`. Not much activity since
then; it goes into a backlog watch row.

---

## 0. Objective, non-goals, done

**Objective.**
- A thin command line (`tracer score`) takes one mono or stereo 16-bit PCM WAV plus the agent's
  VoicEra-format export of the same call (mr, Devanagari).
- It transcribes the audio with Sarvam STT under the user's key (BYOK).
- It normalises both texts with a versioned mr comparison normaliser and aligns them by
  word-level edit distance.
- It flags the export spans that disagree. Each flag has a turn sequence number and a code-point
  span of the export, the signal `stt_disagreement` and an uncalibrated confidence.
- Per call it reports:
  - MER agreement with its H/S/D/I counts;
  - the number of flags;
  - the flagged share of export tokens (the review workload);
  - usage, and cost or `not_available`.
- It stores the run in a `tracer-evaldb` SQLite store and writes one self-contained HTML report.
- No accuracy, error-rate, recall or precision claim anywhere.

**Non-goals (phase boundaries, each gets a BL row when deferred).**
- The other eight in-scope languages: BL-158 to BL-163.
- Further signals: second STT, LLM judge, number and entity checks (BL-173 to BL-175).
- Detection evaluation against known mistakes (BL-178).
- Diarization and speaker attribution (BL-166); per-turn audio alignment of the export
  (BL-167).
- The Sarvam Batch API; MinIO/S3 upload; RunVerdict for recorded scoring.
- The API, UI and full CLI (Phase 7); shared exports, so BL-140 redaction stays open;
  resampling; character-level agreement; schema changes (0.0.2).

**Done means.**
- Every gate item in section D passes on hosted CI (3.11 to 3.14 required, 3.15 non-blocking).
- Every named test exists with red-first evidence under `docs/phase3/evidence/red/<batch>/`.
- `tracer-evalschema` is byte-unchanged (schema.lock sha256 and source tree hash equal the
  Phase 3 baseline, Decisions D4).
- The owner-gated real runs are logged.

No cut list (owner rule): if a task slips, the date moves.

**Package split (Q1).**

| Package | Holds | Dependencies |
|---|---|---|
| `tracer-evalcore` (`tracer_evalcore`) | WAV and VoicEra readers, chunker, normaliser, tokenizer, alignment, flags, metric definitions and registry, result mapping, pipeline with an `SttEngine` Protocol | `tracer-evalschema==0.0.1` and stdlib only; no network import |
| `tracer-evalproviders` (`tracer_evalproviders`) | Sarvam adapter | `httpx>=0.28.1,<1`, evalcore, evalschema |
| `tracer-cli` (`tracer_cli`) | `tracer` console script, HTML report renderer (stdlib `html`), store wiring | evalcore, evalproviders, `tracer-evaldb` |

---

## A. Task table

- Owners: SA (schema-architect), dev, qa, IL (indic-linguistics), SL (security-licensing), lead.
- qa writes every named test red first. Hours include qa red-test time.
- Tier: every scoring test runs under T0, the only tier this case has. The degradation matrix is
  P3-27.
- "Fake Sarvam" means an `httpx.MockTransport` that replays the 2026-10-23 captures or scripted
  bodies. No socket ever opens (P3-27 guard).

### A.0 ADRs and licences (batch 0)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-00a | SA | ADR 0023 "Recorded-call agreement scoring, mr base case" (F2 to F6, Q4 to Q10, Q13, Q14, Q16, Q20) | Accepted; each decision has rejected options and the trade-off. It defines `tracer.flags/1` (fields, units, code-point half-open spans), MER, the confidence formula, the parameters with no defaults, the no-claim wording list, and the not-applicable/error rules. | `test_adr0023_rules_are_tested` (every numbered rule cites an existing test), `test_adr0023_flags_format_matches_json_schema` | 8 |
| P3-00b | SA | ADR 0024 "Provider adapters, BYOK at run time, Sarvam REST" (Q2, Q3, Q15, Q18, Q19, Q21) | Covers chunking, retries, error taxonomy, redaction, call log, the usage/cost mapping, the no-network rule and docs retrieval dates. | `test_adr0024_rules_are_tested` | 6 |
| P3-00c | IL+SA | ADR 0025 "Comparison normaliser `mr-Deva/1`" (Q11) | Rule table with Unicode code points; version-bump rule; offsets kept; every rule `needs_native_review` (BL-169). | `test_adr0025_rule_table_matches_normaliser` | 5 |
| P3-00d | SA | Amend ADR 0016 Decision 1 (F6) and ADR 0004 (the empty-chunk rule, the STT-failure rule); bump the index revision | Amendments logged in the decision log. | `test_adr0016_amendment_cites_test` | 2 |
| P3-01 | dev | Scaffold the three packages: workspace members, SPDX, `py.typed`, ruff/mypy, bounded ranges each tested at the lower bound, console script `tracer`, CI testpaths, `lowest-direct` leg for httpx | Wheels hold no tests; import whitelists hold. | `test_evalcore_runtime_imports_whitelist`, `test_evalcore_imports_no_network_module` (no socket, ssl, http, urllib, httpx), `test_evalproviders_runtime_imports_whitelist`, `test_cli_runtime_imports_whitelist`, `test_evalschema_does_not_import_phase3_packages`, `test_phase3_requires_dist_bounded_ranges`, `test_phase3_wheels_contain_no_tests`, `test_evalschema_unchanged_since_phase3_baseline`, `test_tracer_console_script_runs_help` | 8 |
| P3-02 | SL | Licence review of the httpx closure (httpx, httpcore, h11, anyio, idna, certifi, and sniffio/typing-extensions if present). `[runtime.evalproviders]` and `[runtime.cli]` in `license-policy.toml`. Record `sarvamai` (licence null) and `requests` as rejected. | `check_licenses` ok; every licence read from the files; certifi MPL-2.0 recorded and accepted (owner: do not dwell on copyleft). | `test_license_policy_lists_phase3_closures`, `test_no_lgpl_or_gpl_in_lock` (existing, unchanged), `test_license_exceptions_exact` (existing) | 5 |

### A.1 Ingest (batch A)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-03 | dev | WAV reader (stdlib `wave`), producing an `Artifact` (absolute `file:///` URI, size, sha256, `AudioFormat{wav, pcm_s16le, rate, channels, 16}`, privacy marker from explicit inputs). Duration = frames/rate as an exact Decimal. | Rejects non-PCM, non-16-bit, 0 channels, truncated files and a header frame count beyond the file, each with a typed error. Never reads beyond a size cap. | `test_wav_reader_reads_mono_24k_16bit_exact_duration`, `test_wav_reader_rejects_non_pcm_and_8_or_24_bit`, `test_wav_reader_rejects_truncated_and_lying_header`, `test_wav_reader_rejects_oversize_before_reading`, `test_artifact_uri_is_absolute_file_uri`, `test_artifact_privacy_marker_requires_explicit_consent_input` | 5 |
| P3-04 | dev | Shipped VoicEra export reader (`tracer_evalcore.importers.voicera`), promoted from F8 with identical mapping; `tests/support` left unchanged. Any warning that drops content (unknown speaker, unparsed line, empty input) is a refusal unless `accept_warnings=True` is passed. | Outputs equal F8's on every public F8 case. | `test_voicera_reader_equals_f8_mapper_on_public_cases` (differential), `test_voicera_reader_refuses_dropped_lines_by_default`, `test_voicera_reader_keeps_text_verbatim_and_seq_order`, `test_voicera_reader_maps_assistant_agent_user_caller`, `test_voicera_reader_never_sets_language_or_timing`, `test_voicera_reader_declared_language_only_from_argument`, `test_voicera_reader_rejects_bidi_in_call_id_safely` (adversarial) | 6 |
| P3-05 | dev | Deterministic chunker: windows of at most `max_chunk_ms`; each cut at the lowest-RMS 20 ms frame within the last `search_ms` (pure Python, `array('h')`); chunks as in-memory WAV; chunk plan recorded in `Transcript.metadata`. | Same input gives byte-identical chunks; chunks cover every sample exactly once. | `test_chunker_is_deterministic`, `test_chunker_covers_every_sample_once`, `test_chunker_never_exceeds_max_chunk_ms`, `test_chunker_cuts_at_lowest_energy_frame`, `test_chunker_short_audio_is_one_chunk`, `test_chunker_stereo_is_not_downmixed_silently` (refused unless an explicit downmix is requested; Sarvam merges channels per the docs) | 6 |

### A.2 Sarvam adapter (batch B)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-06 | dev | Request and response models; the mr-only language map (send `unknown`; record any returned tag through `normalize_language_label`); configured language other than mr refused (BL-162); explicit `model` field always sent | Tested against the 2026-10-23 captures. | `test_sarvam_request_sends_unknown_language_and_explicit_model`, `test_sarvam_request_never_sends_forced_code_in_base_case`, `test_sarvam_response_models_parse_all_captures`, `test_sarvam_configured_language_other_than_mr_refused`, `test_sarvam_response_rejects_wrong_types_and_oversize_body` (adversarial) | 5 |
| P3-07 | dev | HTTP client: httpx with an injected transport; connect/read timeouts; bounded retries on 429, 5xx and transport errors, with deterministic backoff and a bounded `Retry-After`; no retry on 4xx; error taxonomy from `err_*` captures; sequential calls; response-size cap; fixed `https://api.sarvam.ai` (no URL override; tests inject a transport, not a URL); all-or-nothing transcript | | `test_sarvam_retries_429_and_503_then_succeeds`, `test_sarvam_gives_up_after_bounded_retries`, `test_sarvam_does_not_retry_400_403`, `test_sarvam_error_taxonomy_matches_captures`, `test_sarvam_honours_bounded_retry_after`, `test_sarvam_partial_chunk_failure_yields_no_transcript`, `test_sarvam_base_url_not_overridable` | 7 |
| P3-08 | dev+SL | Key handling: `SARVAM_API_KEY` from the environment or `--env-file` (reads only that variable); a redacting key wrapper; the key never appears in exceptions, `repr`, logs (httpx logger included), the call log, records, the store or the report | Canary key test over every output channel. | `test_key_never_in_exception_str_repr_or_chain`, `test_key_never_in_logs_at_debug`, `test_key_never_in_call_log_store_or_report`, `test_env_file_reads_only_named_variable`, `test_missing_key_refuses_before_any_request` | 5 |
| P3-09 | dev | Mapping to records. Transcript (`our_stt`, provider `sarvam`, model as requested, `timeline_origin` artifact). One Turn per non-empty chunk (role `unknown`, source `our_stt`, language observed per chunk, `language_probability` in `Turn.metadata`, phrase Segments offset by the chunk start, timing `audio_alignment` from the phrase times, F3 floor rule). Empty chunks give no Turn (Q16). One UsageRecord per chunk (`evaluation`, `stt`, `measured`, `audio_input_seconds` decimal, `requests` 1, `provider_call_ref` request_id, `occurred_at` from the HTTP `Date` header, `raw` null). | | `test_stt_transcript_maps_one_turn_per_nonempty_chunk`, `test_empty_chunk_makes_no_turn_and_is_counted`, `test_stt_turn_language_is_observed_from_unknown_request`, `test_stt_segments_offset_by_chunk_start`, `test_usage_record_per_chunk_measured_seconds_exact`, `test_usage_raw_never_holds_transcript_text`, `test_detected_language_set_only_when_all_chunks_agree` | 6 |
| P3-10 | dev | Cost through the evalschema pricing functions: no catalog gives `not_available`; an optional user USD catalog file (with `note`) gives `computed`; INR is never converted; CostSummary | | `test_cost_not_available_without_usd_catalog`, `test_cost_computed_with_user_usd_catalog`, `test_cost_never_zero_when_unpriced`, `test_inr_price_never_converted` | 4 |
| P3-11 | dev | Local call log (JSONL, git-ignored location; timestamp, chunk index, bytes, audio seconds, status, elapsed, request_id; no text, no key) and pre-send estimate `tracer score --dry-run` (chunks, audio seconds, calls; cost only from a user-supplied price) | | `test_call_log_has_no_text_no_key`, `test_dry_run_sends_nothing_and_prints_plan`, `test_call_log_written_before_and_after_each_send` | 4 |

### A.3 Normaliser, alignment, flags (batch C, pure, the critical path)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-12 | IL | Normaliser spec and fixtures for mr only, reusing `mr.json` mr-001 to mr-010. Rules: NFC (not NFKC); Devanagari digits U+0966-U+096F folded to ASCII in the comparison key only; ZWJ/ZWNJ dropped in the key only (mr-006, eyelash ra still unconfirmed, BL-132); nukta through NFC; punctuation (Unicode P*, danda U+0964/U+0965) and white space separate tokens; Latin case-folded; ळ/ल never merged (mr-007/008); no folding of chandrabindu/anusvara or of number words against digits | Fixture file `packages/evalcore/tests/fixtures/normaliser_mr.json`, every case `needs_native_review: true`. | (fixtures used by P3-13) | 6 |
| P3-13 | dev | Normaliser `mr-Deva/1` (id recorded in every result) | | `test_normaliser_id_is_versioned_and_recorded`, `test_normaliser_mr_fixture_cases`, `test_normaliser_never_merges_lla_and_la`, `test_normaliser_folds_devanagari_digits_in_key_only`, `test_normaliser_drops_joiners_in_key_only_display_kept`, `test_normaliser_is_nfc_not_nfkc`, `test_normaliser_rule_change_requires_new_id` | 5 |
| P3-14 | dev | Tokenizer over the original text with code-point offsets (normalise per token, so offsets survive) | | `test_tokens_carry_code_point_offsets_into_original_text`, `test_token_edges_never_split_a_grapheme_cluster` (virama, ZWJ, nukta next to punctuation), `test_latin_tokens_inside_marathi_kept_as_tokens` (mr-009), `test_numbers_tokenised_whole` | 4 |
| P3-15 | dev | Word-level Levenshtein alignment with fixed tie-break (match > substitution > deletion > insertion); op list; H/S/D/I; MER; input-size cap gives a typed error; performance measured, no threshold | | `test_alignment_identical_texts_all_matches`, `test_alignment_single_substitution_insertion_deletion`, `test_alignment_tie_break_is_deterministic`, `test_alignment_is_symmetric_in_mer`, `test_mer_in_unit_interval_and_exact_counts`, `test_alignment_mixed_script_latin_tokens`, `test_alignment_numbers_digits_vs_native_digits`, `test_alignment_empty_side_cases`, `test_alignment_size_cap_typed_error`, `test_alignment_measurement_2000_tokens` (records the time) | 7 |
| P3-16 | dev | Span extraction: maximal runs of non-match ops become export spans (turn seq, half-open code points); spans split at turn boundaries; an insertion is anchored as a zero-width span at its gap; STT side span and approximate ms from Segments | | `test_spans_are_maximal_non_match_runs`, `test_spans_never_cross_turn_boundaries`, `test_insertion_flag_is_zero_width_at_gap`, `test_span_maps_back_to_exact_export_substring`, `test_span_audio_range_from_stt_segments_or_absent`, `test_spans_only_on_export_never_on_stt` | 5 |
| P3-17 | dev | Flag interface (`Signal` Protocol, name `stt_disagreement`). Confidence = normalised character edit distance between the normalised keys of the export and STT spans, in [0,1]; 1.0 for pure insertion or deletion; documented as an uncalibrated disagreement strength, not a probability. Threshold `flag_min_confidence` (no default). Flag if confidence >= threshold. `near_chunk_boundary` attribute. | | `test_flag_threshold_has_no_default`, `test_flag_kept_iff_confidence_at_or_above_threshold`, `test_confidence_in_unit_interval_and_deterministic`, `test_confidence_is_one_for_pure_insertion_or_deletion`, `test_signal_interface_accepts_second_signal_stub` (interface only, BL-173), `test_near_chunk_boundary_marked` | 5 |

### A.4 Results, pipeline, storage (batch D)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-18 | SA+dev | Three shipped definition templates, kind deterministic, `languages=["mr"]`, `script_sensitive=false`, `requires=["transcript","agent_transcript"]`, per-conversation, **no pass rule**: `stt_export_agreement_mer` (numeric/ratio), `stt_export_flag_count` (numeric/count), `stt_export_flagged_token_share` (numeric/ratio). Parameters `flag_min_confidence` (number) and `normaliser` (string), neither with a default. Scorer registry (BL-144) with `implementation_ref` `builtin:...`; version = distribution version. The rubric file builds owner definition versions; the owner's review budget is `range{max}` on the share. | | `test_shipped_definitions_have_no_pass_rule`, `test_agreement_unit_is_ratio_never_wer_or_cer`, `test_definitions_requires_transcript_and_agent_transcript`, `test_unknown_scorer_refuses_before_run`, `test_rubric_file_without_required_pass_rule_refused`, `test_missing_binding_param_refuses_before_any_send`, `test_metrics_evaluate_agent_outputs_only` (closes BL-145, renamed) | 6 |
| P3-19 | dev | Result mapping. Scored results carry ScorerInfo, `transcript_ids=[export, stt]`, `language_source=declared` with `declared_language_transcript_ids`, and evidence refs (distinct flagged export turns by seq, plus artifact ms ranges). `metadata["tracer.flags"]` follows `tracer.flags/1` (only ints, decimal strings, machine keys) and holds H/S/D/I, normaliser id, signal, provider and model, threshold and per-flag records. Zero export tokens gives `error` with code `no_tokens`. | | `test_results_pass_check_result_against_definition`, `test_flags_metadata_validates_against_tracer_flags_1`, `test_flags_metadata_has_no_text_and_no_metadata_flags`, `test_evidence_refs_resolve_in_bundle`, `test_transcript_ids_required_with_two_transcripts`, `test_language_source_declared_from_export`, `test_empty_export_is_error_no_tokens_not_zero` | 6 |
| P3-20 | dev | Pipeline (evalcore): read, STT through the Protocol, normalise, align, flag, build the RunBundle (Run `recorded`, `caller_origin` explicit, `observed_tier` T0, `available_inputs` exact, `started_at` absent), then `check_run_bundle` | | `test_pipeline_bundle_passes_check_run_bundle`, `test_pipeline_observed_tier_is_t0`, `test_pipeline_started_at_absent_never_ingest_time`, `test_pipeline_is_deterministic_given_same_stt`, `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit` | 7 |
| P3-21 | dev | Store via evaldb (project, definitions, snapshot, run bundle) in `--out`; reload equal; SQLite file 0600 | | `test_run_round_trips_through_store_byte_identical`, `test_store_file_is_0600`, `test_pipeline_bundle_stores_on_both_backends` (`[postgres]` marker) | 4 |

### A.5 CLI and report (batch E). Milestone M1: runnable by hand offline after P3-22 with `--stt fake:<dir>`.

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-22 | dev | `tracer score <audio> --transcript <file> --rubric <file> --declared-language mr --caller-origin human\|simulated --consent obtained\|not_obtained\|unknown --retention ... --out <dir> [--stt sarvam\|fake:<dir>] [--dry-run] [--embed-audio]`. Every privacy and language input is required, with no defaults. Prints MER (with counts), flag count, token share with numerator/denominator, usage, cost or `not_available`. Exit codes documented. | | `test_cli_requires_every_privacy_and_language_input`, `test_cli_prints_counts_with_denominators`, `test_cli_output_never_claims_accuracy` (deny-list: accuracy, WER, CER, error rate, correct, recall, precision), `test_cli_fake_stt_runs_offline`, `test_cli_exit_codes`, `test_cli_has_no_export_command` (BL-140 stays open) | 7 |
| P3-23 | dev | Single HTML report. CSP `default-src 'none'`. Inline style. One inline seek script pinned by its sha256 in the CSP (Q12). Audio by relative `src` (URL-encoded) by default; `--embed-audio` opt-in with a warning. Every text through `display_safe()` then `html.escape(quote=True)`, applied per segment around the highlights. Shows the declared language (BL-147), scorer/provider/model, normaliser id, threshold, a histogram of flag confidences, counts, and a cost/usage table. No absolute paths. | | `test_report_has_no_external_requests` (src, href, `url()`, `@import`, `//`), `test_report_csp_present_and_script_hash_matches`, `test_report_escapes_html_and_script_in_transcript`, `test_report_shows_bidi_controls_as_markers`, `test_report_highlights_exact_flag_spans`, `test_report_relative_audio_by_default_embed_opt_in`, `test_report_never_claims_accuracy`, `test_report_shows_language_declared_by_user`, `test_report_has_no_absolute_paths` | 8 |
| P3-24 | qa | Adversarial report and input suite: malicious export lines, a Sarvam body with markup and bidi, a huge line, a NUL character, lone surrogates | | `test_adversarial_inputs_never_break_report_or_store` | 4 |

### A.6 End to end, real sends, audit, docs, gate (batch F)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P3-25 | IL+qa | Synthetic mr pair: invented mr text (no real call content), a VoicEra-format export with planted substitutions, deletions, insertions, Latin tokens and digit forms; a generated WAV (tones or silence); scripted fake Sarvam bodies per chunk; provenance `synthetic_hand_written` | | (fixtures) | 5 |
| P3-26 | qa | Offline end to end through the CLI with the fake | Exact expected MER counts, flag spans, share, report bytes stable. | `test_e2e_synthetic_mr_exact_outputs`, `test_e2e_report_and_store_consistent` | 6 |
| P3-27 | qa | T-DEG matrix (identical record kinds and schema, `not_applicable` never failure) and the no-network guard (conftest patches `socket.connect` for every package) | | `test_tdeg_audio_and_export_scored`, `test_tdeg_no_audio_missing_input_transcript`, `test_tdeg_no_export_missing_input_agent_transcript`, `test_tdeg_export_without_declaration_language_not_observed`, `test_tdeg_non_mr_chunk_unsupported_language`, `test_tdeg_schema_identical_across_cases`, `test_unit_tests_open_no_socket` | 4 |
| P3-28 | SL | Security audit `docs/security/P3-28-audit.md` (keys, logs, call log, store, report, privacy markers, out-dir placement, guard, ToS F9). Must pass before P3-30. | | `test_p3_audit_items_cite_tests` | 8 |
| P3-34 | dev+SL | Extend `scripts/guard_private_fixtures.py` and `.gitignore`: refuse staged report HTML (embedded marker), store files and call logs | | `test_guard_rejects_staged_report_store_and_call_log` | 3 |
| P3-29 | lead | **Owner questions**, one at a time in chat: (1) `flag_min_confidence` value and (2) review budget, pre-registered before any real result is seen; (3) ToS 10.5(a): written Sarvam permission, or explicit risk acceptance; (4) Sarvam 17.5 training opt-out status; (5) consent status to record for the callers | Answers in the decision log. | none | 1 |
| P3-30 | lead (owner-gated) | First real send (R1). Order: dry-run; one chunk (checks that 24 kHz is accepted and that `unknown` detects mr); then both clips. Log before and after; record counts only (chunks, detections per tag, usage, status) in the decision log. Never text. | Closes BL-168. | none (evidence `docs/phase3/evidence/real/` counts only) | 3 |
| P3-31 | lead+qa | Noise-floor measurement, owner-gated: R2 repeats R1 (STT run-to-run stability); R3 shifts the chunking by half a window (boundary sensitivity); offline ablation of the normaliser on vs off from stored data. Report the Jaccard of flagged token sets and the counts. | | `test_measurement_report_has_counts_and_denominators` | 4 |
| P3-32 | SA+dev | Docs: `docs/concepts/recorded-scoring.md`; README quick start runnable on the synthetic pair (`python -I`); three CHANGELOGs; licence and NOTICE | | `test_readme_quick_start_runs_offline`, `test_concept_page_states_no_accuracy_claim` | 6 |
| P3-33 | lead | BACKLOG rows at each deferral and closures; HANDOFF refresh | | none | 2 |
| P3-G | reviewer | Gate | | none | 6 |

**Total: about 204 h** (batch 0: 34, A: 17, B: 31, C: 32, D: 23, E: 19, F: 48).

---

## B. Dependency graph

**Internal.**
- P3-00a/b/c/d come before all code. P3-01 comes before every code task. P3-02 must pass before
  P3-07 lands.
- Critical path: P3-12 → 13 → 14 → 15 → 16 → 17 → 18 → 19 → 20 → 22 → 23 → 26 → (28, 29, 34) →
  30 → 31 → G.
- In parallel with the critical path: {P3-03, 04, 05} with batch C; {P3-06 to 11} after P3-02 and
  P3-05; P3-21 after P3-20; P3-25 any time after P3-12; P3-27 after P3-20; P3-32 after P3-23.
- Milestone M1 (offline, runnable by hand) = P3-22 with the fake STT. Milestone M2 = P3-30.

**External.**

| Dependency | Pinned? | Failure mode |
|---|---|---|
| `tracer-evalschema==0.0.1` | pinned (==) | none expected |
| `tracer-evaldb` (workspace) | pinned (==) | none expected |
| Sarvam STT REST `POST /speech-to-text` | unpinned: no API version; dated by retrieval date 2026-10-25; default model `saaras:v4`, sent explicitly | output drift between runs (record `request_id`, model, date); 400 vs the documented 422 (captured); 429/5xx never observed |
| Sarvam REST over 30 s | unverified | rejection or truncation; Tracer never sends over `max_chunk_ms` |
| 24 kHz acceptance | unverified; the docs say "works best at 16kHz" | rejection is checked on the first chunk of R1, which stops the run |
| Sarvam pricing (INR per minute; rounding unstated) | n/a | cost `not_available` |
| Sarvam terms v2.0 (10.5(a), 17.5) | n/a | owner gate |
| `httpx>=0.28.1,<1` | bounded range, lower bound tested | maintenance (no release since 2024-12) |
| stdlib `wave` | stdlib | `audioop` was removed in Python 3.13, so no stdlib resampling |
| SQLite ≥ 3.37 | as in Phase 2 | — |
| Postgres 15/18 | as in Phase 2; only the one parity test | — |
| MinIO, telephony | not used | — |
| Python 3.11 to 3.14 required, 3.15 non-blocking | | — |

---

## C. Open design questions (recommendation first; the lead auto-approves and logs; owner-only items are marked)

All of Q1 to Q22 are auto-approved on the recommended option (Decisions D1); the two values in Q8
are owner decisions (Decisions D2).

- **Q1 Package split.** Recommend three packages (evalcore pure / evalproviders HTTP / cli).
  - Rejected: one package (puts HTTP next to the scorers); five packages following the plan
    (evalcore, evalscorers, evalingest, evalproviders, cli: CI and licence overhead with no
    second consumer yet).
  - Backlog row for a later evalscorers split.
- **Q2 HTTP client.** Recommend httpx (BSD-3; `MockTransport` makes offline tests natural; it
  worked on the owner's machine in the capture tool).
  - Rejected: `urllib` (multipart by hand; TLS certificate store on uv's standalone Python on
    macOS unverified); `requests` (no mock transport; same certifi).
- **Q3 Long audio.** Recommend REST plus a deterministic client chunker (≤ 25 s, cut at the
  lowest-energy frame), with a `near_chunk_boundary` mark and the R3 sensitivity measurement.
  - Rejected for now: the Batch API (upload/start REST paths not documented; SDK licence null).
    Backlog row.
- **Q4 Language provenance.** Recommend `language_code=unknown` (F2).
  - Rejected: forced `mr-IN` (always `language_not_observed`); forced code with a
    `language_independent` metric and a user-chosen normaliser (bypasses the P-59 language
    guard); a 0.0.2 `requested_language` field (a schema change). Backlog row.
- **Q5 Agreement measure.** Recommend MER as unit `ratio`.
  - Rejected: Levenshtein divided by the longer length (equally valid, less established);
    WER-like division by the export length (unbounded, needs `wer`, implies a reference).
- **Q6 Where flags live.** Recommend `MetricResult.metadata["tracer.flags"]` of the count result,
  `tracer.flags/1`, plus evidence refs.
  - Rejected: one result per flag (one definition gives one result per run); `per_turn_share`
    (no character spans); a sidecar file (not stored, not reproducible).
  - 0.0.2 backlog row for a typed flag record.
- **Q7 Confidence.** Recommend the normalised character edit distance of the span keys
  (uncalibrated, documented).
  - Rejected: a constant 1.0 (the threshold becomes useless); a "probability" label (an unearned
    claim). Calibration is a backlog row tied to BL-178.
- **Q8 Threshold and rubric.** Recommend binding parameters `flag_min_confidence` and
  `normaliser` with no defaults, plus a required rubric item on the token share with the owner's
  `range{max}` budget. **The two values are owner decisions (P3-29); nothing is assumed.**
- **Q9 Workload unit.** Recommend the share of export tokens. The report also shows turns with
  flags.
  - Rejected: characters (penalise long words); turns (too coarse for "read only the spans").
- **Q10 Roles.** Recommend evaluating both roles of the agent export, with the ADR 0016
  amendment (F6).
  - Rejected: agent lines only (misses the agent's STT errors on caller speech).
- **Q11 Normaliser joiners.** Recommend dropping ZWJ/ZWNJ in the comparison key only, so a
  difference a reader cannot see is not flagged.
  - Rejected: keeping them (flags invisible differences). IL confirms; eyelash ra stays
    unconfirmed (BL-132).
- **Q12 Report audio and script.**
  - Audio: recommend a relative reference by default and embedding as an opt-in. Embedding makes
    a 28 MB file that holds personal data and is easy to send on by mistake.
  - Script: recommend one hashed inline seek script under a strict CSP. Rejected: no JavaScript
    (no seeking to a flag).
  - Whether a relative `<audio src>` plays under `file://` in Chrome, Firefox and Safari is
    **unverified**; P3-23 adds a manual check.
- **Q13 Evaluator and Persona for recorded runs.** Recommend an honest task text, a Persona
  holding only the declared call language, and `max_duration_ms` = 14,400,000 (the schema
  maximum, stated as such). 0.0.2 backlog row.
- **Q14 STT failure.** Recommend all-or-nothing: `not_applicable` `missing_input[transcript]`
  plus a Run `IngestWarning` plus a non-zero exit.
  - Rejected: `error` (the input never existed); a partial transcript (fake deletion flags).
- **Q15 Model.** Recommend always sending `model` explicitly and recording it on the Transcript
  and UsageRecord as requested, with a metadata note that the response does not echo it.
  - Rejected: `null` (the run could then never be priced or attributed to a model).
- **Q16 Empty chunks.** Recommend no Turn; the empty chunk is counted in transcript metadata. A
  non-null language on an empty transcript is not a detection (captures).
- **Q17 Output placement.** Recommend a required `--out` plus the extended guard (P3-34) plus
  `.gitignore` entries.
- **Q18 Chunk size.** Recommend an adapter parameter with a documented 25 s default (a technical
  margin under the docs' 30 s guidance, not a rubric value), recorded in the chunk plan.
- **Q19 `occurred_at`.** Recommend the response `Date` header (the source's own statement);
  absent if there is none.
- **Q20 Verdict.** Recommend no RunVerdict in Phase 3. Backlog row.
- **Q21 Sample rate.** Recommend sending native 24 kHz and verifying on the first chunk of R1.
  - Rejected: a pure-Python resampler (quality risk, untested).
- **Q22 F8 reuse.** Recommend promoting the mapper with a differential test and leaving
  `tests/support` unchanged.

---

## D. Gate checklist (all must hold; evidence = hosted CI logs plus paths)

1. Hosted CI green: every required job and the new jobs (3.11 to 3.14 for the three packages, the
   lowest-direct httpx leg, licences, guard, gitleaks, pip-audit).
2. Every test named in A exists under its exact name. Red-first evidence for every batch is under
   `docs/phase3/evidence/red/`.
3. evalschema byte-unchanged (`test_evalschema_unchanged_since_phase3_baseline`, baseline per
   Decisions D4). No earlier test weakened, skipped or deleted; any spec change is named, with
   before and after.
4. No network in unit tests (`test_unit_tests_open_no_socket`). No key value anywhere: canary
   tests, gitleaks, the guard.
5. `test_report_never_claims_accuracy`, `test_cli_output_never_claims_accuracy`,
   `test_concept_page_states_no_accuracy_claim` green. `wer`/`cer` units never used.
6. `test_flag_threshold_has_no_default` and `test_missing_binding_param_refuses_before_any_send`
   green. The owner's values are recorded in the decision log before R1.
7. T-DEG matrix green: identical schema, `not_applicable` never a failure or zero.
8. `check_run_bundle` passes for every produced bundle. Store round trip byte-identical on
   SQLite, plus the one Postgres parity test.
9. Licence review P3-02 ok, with no GPL or LGPL.
10. Security audit P3-28 has no open P0/P1 finding, and was finished before P3-30.
11. Owner gates P3-29 answered. R1 logged before and after (counts only). No call text or Sarvam
    body in any committed file or log.
12. ADRs 0023 to 0025 and the 0016 amendment Accepted, each rule citing a test. Index revision
    bumped.
13. BACKLOG: new rows filed at deferral time (list below). Closed rows carry a commit id and a
    test name: BL-144, BL-145 (renamed), BL-147 (declared-language part), BL-162 (mr part),
    BL-165, BL-168, BL-170, BL-171, BL-172. BL-176 resolved with the docs citation.
14. HANDOFF refreshed.

**Proposed new BACKLOG rows (Phase 3+ block; filed as BL-179 to BL-192, see `docs/BACKLOG.md`):**
- Batch STT for long audio (BL-179).
- 0.0.2 `requested_language` on `our_stt` transcripts (BL-180).
- 0.0.2 typed flag record or character-span evidence (BL-181).
- 0.0.2 Persona optional for recorded runs, and a rubric with no required item (BL-182).
- Character/grapheme-level agreement (BL-183).
- Confidence calibration (BL-184).
- Object-storage upload (update BL-154) (BL-185).
- RunVerdict for recorded scoring (BL-186).
- 16 kHz resampling (BL-187).
- ToS 10.5(a) clearance (owner) (BL-188).
- Sarvam training opt-out (owner) (BL-189).
- httpx maintenance watch (BL-190).
- evalscorers package split (BL-191).
- Number words vs digits (link to BL-175) (BL-192).

---

## E. Risks and measurement

- **Two clips only.** Results are a demonstration. Report per-call counts with denominators
  (H/S/D/I, flags, flagged and total tokens). Make no aggregate claim. The owner fixes the
  threshold and budget **before** seeing R1 (pre-registration; no tuning on these clips, BL-170).
  The report shows a histogram of flag confidences, never a recommended threshold.
- **STT is not deterministic, and models change with no API version.**
  - R2 measures run-to-run flag stability (Jaccard of flagged tokens, counts).
  - Results record provider, requested model, request ids and the retrieval date.
  - Comparisons across runs are refused or labelled when scorer, provider or model differ
    (BL-147).
- **Chunk-boundary artefacts.** The `near_chunk_boundary` mark plus the R3 shifted-chunking
  measurement.
- **Orthographic noise.** The normaliser on/off ablation, computed offline from the stored R1
  data. Number words vs digits, chandrabindu/anusvara and spelling variants (the captures already
  show them) are known sources of false flags. There is no native review yet (BL-169), so every
  normaliser rule is marked unreviewed.
- **Order differences.** Overlap and barge-in in mono audio, and agent lines that were in the
  export but cut off by an interruption, show up as flags. They are labelled by role in the
  report. Nothing is inferred about the cause.
- **Language.** A non-mr detection on any chunk makes the result `unsupported_language`. R1
  reports detections per tag. If it happens, Q4's alternatives go back to the owner with the
  evidence.
- **Privacy.**
  - Real helpline audio goes to Sarvam (consent given). It is processed in India, Sarvam keeps it
    at its own discretion, and it may be used for training unless the owner opts out (17.5;
    unverified how).
  - The callers' own consent status is an owner fact.
  - Store, report and call log stay local and git-ignored, protected by the guard. The store
    holds the absolute `file:///` path (which contains the local account name).
- **Terms of service 10.5(a).** Whether it bars this use cannot be determined here (F9). Owner
  gate.
- **Cost.** About 11.5 min of audio comes to about ₹5.6, or about ₹13.7 if every request is
  rounded up to a minute (unverified). R2 and R3 multiply that by three. Always `not_available`
  in USD without a user price.
- **Adversarial inputs.** Markup, bidi, NUL or huge lines in exports and in Sarvam bodies; lying
  WAV headers; oversize bodies. Covered by P3-03, P3-06, P3-23, P3-24.
- **Supply chain.** httpx has had no release since 2024-12 (watch row).

## F. Hours by task

See the `h` column in section A. Totals per batch: 0 = 34 h, A = 17 h, B = 31 h, C = 32 h,
D = 23 h, E = 19 h, F = 48 h. **About 204 h.** The critical path alone is about 85 h.

**What the reviewer needed and could not get.**
- HANDOFF lines 123 to 265.
- `git status`, `gh run list` and the current test counts at the Phase 3 baseline commit.
- Whether `anyio` at the locked version still needs `sniffio` (SL reads this in P3-02).
- Browser behaviour of a relative `<audio src>` under `file://` (manual check in P3-23).
- Sarvam's real behaviour on chunks over 30 s and at 24 kHz, and its billing rounding (all
  unverified; only R1 or Sarvam support can answer).
- The owner's answers in P3-29.

**Files that matter for this scope:**
- `<repo>/packages/evalschema/src/tracer_evalschema/metrics.py` (UNITS lines 332-341,
  MetricParameter 483)
- `<repo>/packages/evalschema/src/tracer_evalschema/results.py` (check_language 672-726)
- `<repo>/packages/evalschema/src/tracer_evalschema/transcript.py` (DECLARABLE_SOURCES line 89)
- `<repo>/packages/evalschema/src/tracer_evalschema/events.py` (EvidenceRef 314-345)
- `<repo>/packages/evalschema/src/tracer_evalschema/snapshot.py` (validate_rubric inside the
  snapshot, line 178)
- `<repo>/packages/evalschema/src/tracer_evalschema/rubric.py` (lines 198-202)
- `<repo>/packages/evalschema/src/tracer_evalschema/bundle.py` (tier ranks 505-536)
- `<repo>/packages/evalschema/src/tracer_evalschema/usage.py`
- `<repo>/packages/evalschema/tests/support/mappings/voicera_transcript.py`
- `<repo>/packages/evalschema/tests/support/mappings/sarvam_stt.py`
- `<repo>/tools/capture/SARVAM_OBSERVATIONS.md`
- `<repo>/packages/evalschema/tests/fixtures/languages/mr.json`
- `<repo>/docs/adr/0016-roles-caller-origin-privacy.md` (Decision 1)
- `<repo>/docs/security/P1-02-licenses.md` (section 8)
- `<repo>/docs/BACKLOG.md`
