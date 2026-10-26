# Phase 4 scope: the simulated caller (`tracer-evalsim`), mr base case

Status: Scoped 2026-10-27; batch 0 (docs) may start; code (P4-01) starts only after the Phase 3
gate is recorded as passed. Package: `tracer-evalsim` (new), with extensions to
`tracer-evalcore`, `tracer-evalproviders` and `tracer-cli`. Total: about 349 h (P4A, batches 0 to
H: 317 h; P, telephony: 32 h). No code of this file is built yet. The batch 0 ADRs of P4-00a to
P4-00e (ADRs 0026 to 0029 and the amendments of ADRs 0004, 0023 and 0024) were written on
2026-10-27, ADR index revision 50 (docs only; their rule tests are qa's next task); P4-00f is
indic-linguistics' fixture task.

Source: the Phase 4 scoping report of the staff-reviewer (read-only, 2026-10-27), logged in the
decision log entry 2026-10-27 "PHASE 4 SCOPE DELIVERED", reproduced faithfully below. The only
edits are formatting: this header block and the section "Decisions and owner questions" below,
repository-relative paths (`<repo>`) instead of absolute ones, ASCII arrows (`->`) instead of
arrow characters, "as Phase 3" instead of a dash in one empty table cell, and the reviewer's
hand-over sentence to the architect dropped from the reviewer's note. Tasks, hours, test
names, acceptance criteria and every "not verified" marker are unchanged. The reviewer's own
notes keep the reviewer's first-person wording ("I").

## Decisions and owner questions

- **Decisions D1 to D5** are the reviewer's records in section F, reproduced there unchanged.
  D1 (Q1 to Q22 auto-approved on the recommended option, owner instruction of 2026-10-23) is
  logged in the decision log entry 2026-10-27 "PHASE 4 SCOPE DELIVERED", with the rejected
  options and trade-offs of section C. The ADRs that record them are ADRs 0026 to 0029 and the
  amendments of ADRs 0004, 0023 and 0024 (P4-00a to P4-00e, ADR index revision 50, 2026-10-27).
  Their "Ambiguities resolved" sections hold readings for the lead to accept; two of them need a
  decision before qa writes red tests: ADR 0027 Ambiguity 1 (a timing value without both edges is
  `error` `timing_edge_missing`, so the P4-29 test test_tdeg_sim_agent_silent_timing_not_applicable
  needs a named spec change; renamed 2026-10-27 (D-a) to `test_tdeg_sim_agent_silent_timing_not_measured`; BL-292) and the dialect message sets that could not be fetched
  (ADR 0026, BL-291).
- **Update 2026-10-27 (lead decisions D-a to D-d; decision log 2026-10-27 "PHASE 4 BATCH 0 ADRs
  DELIVERED", auto-approved on the architect's readings).** The text above is kept as history.
  - **(D-a)** The P4-29 test test_tdeg_sim_agent_silent_timing_not_applicable is renamed to
    `test_tdeg_sim_agent_silent_timing_not_measured` (named change, before and after). A timing
    value with a missing edge (an agent that never speaks) is stored as `error` with the code
    `timing_edge_missing`, because schema 0.0.1 cannot store a valid `not_applicable` for it while
    `audio` and `turn_timing` are available (ADR 0027 rule 11 and Ambiguity 1, BL-292). Never 0 and
    never false: the value is absent and the result is an error with the code. The P4-07 test
    test_no_timing_value_without_both_edges_is_not_applicable is renamed the same way (renamed
    2026-10-27 (D-a)) to `test_timing_value_without_both_edges_is_error_never_zero` (BL-292).
  - **(D-b)** With the LLM caller, each finished agent segment is transcribed DURING the call, because
    the caller brain needs the agent's text before it answers (ADR 0028); scripted and replay modes
    may transcribe the agent channel after the call. P4-13 and P4-21 are amended below; the hours of
    both rows are to be re-estimated by the reviewer.
  - **(D-c)** Simulated runs report an STT turn in a language other than mr as `not_applicable`,
    where recorded runs report `error` (ADR 0023 amendment A3): the simulated agent may legitimately
    speak another language, and the per-role partition handles it. P4-22 is amended below.
  - **(D-d)** Finding F3 is corrected: `soxr` 1.0.0 (LGPL-2.1-or-later, bundles libsoxr; in the
    Pipecat core closure through `soxr~=1.0.0`) is another tool-only LGPL item. F3 and P4-02 are
    amended below.
  - **O6** is decided by the lead on 2026-10-27 under the owner rule on copyleft (section E).
- **Owner questions O1 to O9** (section E) are asked one at a time in chat, before the tasks that
  depend on them (P4-33). Numeric values (O4) are pre-registered in the decision log before any
  real result is seen. None is answered yet:

| Question | Subject | Needed before (as the scope states it) | Status |
|---|---|---|---|
| O1 | Sarvam terms for the simulator (10.5(a), 10.2(8), 17.5) | P4-34 (R1); section B and gate item 11 | open |
| O2 | Agent under test for R1 and R2 | P4-34 (R1); section B and gate item 11 | open |
| O3 | Stack of the `llm_bot` (and a Silero VAD inside the sample agent) | R1 (gate item 11); running the `llm_bot` of P4-26 (b) | open |
| O4 | Pre-registered values (caps, minimum interval, repeats, silence values) | R1 (gate items 6 and 11); P4-36 (repeats) | open |
| O5 | Telephony (provider, numbers, caller id, tunnel, regulation statement) | batch P (P4-40 to P4-G2) | open |
| O6 | Copyleft in the WebRTC extra (`av`/FFmpeg) | not stated; applies if the bundled FFmpeg is LGPL | decided by the lead 2026-10-27 under the owner rule on copyleft: named exception for the optional `[webrtc]` extra with conditions (`docs/security/P4-02-licenses.md`, decision log) |
| O7 | Retention of simulated runs | not stated | open |
| O8 | New provider keys | asked only if O1 is (c) | open |
| O9 | Languages (mr only) | not stated | open |

- **BACKLOG.** The "Proposed new BACKLOG rows" of section F and the new 0.0.2 rows of D5 are
  filed as BL-269 to BL-287 (`docs/BACKLOG.md`, section 7); BL-015 is re-targeted to a schema
  0.0.2 phase (Q1, D5), and BL-193 carries the F6 extension.

## Reviewer's note

Staff-reviewer, read-only, 2026-10-27. Everything marked "not verified" was not checked against a primary source at the pinned version.

## What the reviewer read

**Files read:**
- The plan file.
- `docs/HANDOFF.md`: lines 1-121 and 230-304.
- `docs/phase3/SCOPE.md`: all of it.
- `docs/adr/README.md`: revisions 20-49.
- ADR 0016: Decisions 1-9.
- ADR 0024: all of it.
- `docs/phase1/api-surface-mappings-2.md`: section 1 (the F5 part) and section 3 (F5 Pipecat Evals).
- `docs/security/license-policy.toml`.
- Source:
  - `persona.py`, `agent.py`, `evaluator.py`, `run.py`, `artifact.py`, `privacy.py`
  - `transcript.py` (constants), `usage.py` (constants), `metrics.py` (`INPUT_KINDS`)
  - `snapshot.py` (`ConfigSnapshot`), `bundle.py` (agent_transcript rules)
  - `tracer_evalcore/stt.py`, `pipeline.py` (greps)
- Greps of the BACKLOG (BL-015, BL-029, BL-148) and `docs/concepts`.

**Fetched on 2026-10-27:**
- Pipecat docs pages: simulated-scenarios, scenario-configuration, runner guide, small-webrtc.
- PyPI JSON: `pipecat-ai` 1.14.0, `num2words`, `aiortc`.
- GitHub `main` (not a tag): `src/pipecat/evals/` listing, `client_transport.py`, `serializer.py`, `src/pipecat/serializers/` and `src/pipecat/transports/` listings.
- Sarvam: TTS REST reference, chat completions v2 reference, terms of service v2.0.

**Not done:**
- I did not run the suite, `git status`, `git log` or `gh`.
- I did not open `.env.local`, the private fixtures, `~/tracer-runs`, the keys notes or the Desktop folders. I wrote nothing.
- The Phase 3 gate is not yet recorded as passed (C1 to C4 open, per HANDOFF section 4). Its final commit id is unknown to me.

---

## Findings that change the brief (read first)

**F1. Pipecat already ships a simulator, and it is not a black-box client (verified on GitHub `main` and in the docs, not at a tag).**
- Pipecat Evals (`pipecat eval run`, modules `pipecat/evals/*`) does persona + goal + success simulations and has a judge. Its defaults are local Ollama `gemma4:12b`, Kokoro TTS and Moonshine STT.
- Its harness talks to the bot through `EvalClientTransport`, a subclass of `RTVIClientTransport`. The wire is JSON RTVI envelopes plus Pipecat's own messages: `eval-connect`, `eval-configure`, `eval-context`, `eval-cancel`, `eval-bot-audio`.
- The bot must run Pipecat's `EvalTransport` behind the dev runner's `/ws` route. That is a test-only harness, not the agent's production transport.
- So Pipecat Evals cannot place a T0 call to a deployed Dograh or VoicEra agent. We do not build on it.
- The repository already maps its scenario format (F5 mapper, test support only). Promoting it to an importer is a backlog row, not Phase 4.

**F2. "A Pipecat WebSocket endpoint" is not one protocol (Pipecat runner docs, verified 2026-10-27).**
- The dev runner serves:
  - `/ws`, a telephony media WebSocket. The provider is detected from the first messages (`parse_telephony_websocket()`); the serializers are Twilio, Telnyx, Plivo, Exotel, Vonage and Genesys.
  - `/ws-client`, a plain WebSocket with `ProtobufFrameSerializer` (`pipecat.serializers.protobuf`).
  - `POST /start`, `/client` (WebRTC UI), `/status`, and Daily routes.
- The simulator therefore needs explicit, named wire dialects. Which dialect Dograh and VoicEra deployments expose is **not verified**; that is the adapters phase.
- Update 2026-10-27 (BL-301; source read at tag `v1.14.0`, decision log "BL-291 PARTIALLY CLOSED"; ADR 0026 Context and "Dialect specifications"): `parse_telephony_websocket` detects Twilio, Telnyx, Plivo and Exotel; Vonage has its own runner file and no Genesys detection was seen, so the list of six serializers above is not confirmed. `/ws-client` does not enforce `ProtobufFrameSerializer`: the bot chooses its serializer and sample rate. `/client/` is the prebuilt web UI; WebRTC signalling is `POST` and `PATCH /api/offer`. The text above is kept as history.

**F3. Taking `pipecat-ai` as a runtime dependency would break `test_no_lgpl_or_gpl_in_lock` and the closure policy.**
- `pipecat-ai` 1.14.0 was uploaded 2026-10-24. The repository's captures are 1.13.0, so it moves weekly.
- Its core `requires_dist` includes:
  - `num2words>=0.5.14`, whose PyPI licence is "LGPL".
  - `soundfile`, whose wheels bundle libsndfile (LGPL-2.1; not verified for the wheel actually locked).
  - `onnxruntime`, `numba`, `openai`, `resampy`, `loudness`, `sentencex==1.0.32` (licences of the last three not verified).
  - Update 2026-10-27 (D-d): `soxr` 1.0.0, LGPL-2.1-or-later, bundles libsoxr; in the core closure through `soxr~=1.0.0`; tool-only like the items above.
- The `sarvam` extra pins `sarvamai==0.1.28`, which is already in `[[rejected]]` (no licence declared).
- Recommendation (Q2): a thin protocol client, with no Pipecat in any tracer wheel. Pipecat lives only in an isolated tool project, `tools/samplebot/`, with its own lock, following the precedent of `tools/capture`.

**F4. Sarvam terms v2.0 hit the simulator harder than they hit Phase 3 (fetched 2026-10-27; not legal advice).**
- **10.5(a)** forbids using output "to ... test ... any ... artificial intelligence system" without written permission. The simulator uses Sarvam TTS and LLM output for exactly that.
  - The owner's Phase 3 reading ("we are not testing an AI system") does not carry over.
  - This is a new owner gate.
- **10.2(5)** forbids unauthorised robocalling: "automated dialling systems or artificial ... voice messages to place calls without appropriate human authorisation and consent". This applies to PSTN dial-out.
- **10.2(8)** requires labelling synthetic audio that could be mistaken for human speech.
- **10.3(6)**: agentic workflows need "human oversight, testing, rate-limiting and authorisation".

**F5. The schema can hold a simulated run without a change, with three readings to pin.**
- Already present and usable:
  - `Run.mode` includes `simulated`; `simulated` implies `caller_origin=simulated`.
  - `UsageRecord.stage` already has `sim_caller_llm` and `sim_caller_tts`.
  - `Evaluator` already has `instructions`, `expected_outcome`, `scripted_turns{index,text,language}` and `max_duration_ms` (at most 4 h).
  - `ChannelMap` with `declared` and roles `caller`/`agent`.
  - `TRANSCRIPT_SOURCES` and `TURN_SOURCES` include `script`.
- Gaps:
  - `Persona` holds only `language` and `accent`. BL-015 lists the missing fields (pace, interruption style, emotion, noise, `voice_ref`) and targets "Phase 4 (simulator)".
  - `Run` has no status or end reason.
  - There is no timing source for "the simulator's own clock", and no source for "LLM-generated caller line".
- Recommendation (Q1): no schema change in Phase 4.
  - The simulator configuration goes in a versioned document `tracer.sim_profile/1`, held in `ConfigSnapshot.metadata`, which the digest covers (verified: `digest_domain` keeps `metadata`).
  - The outcome goes in `Run.metadata["tracer.sim_outcome/1"]`.
  - All of it is machine keys and integers, no free text.
  - BL-015 is re-targeted to a schema 0.0.2 phase.

**F6. A trap in 0.0.1: `agent_transcript` selects turns by role, not by source.**
- `bundle._turns_read` keeps `role == "agent"` turns of any source.
- Phase 4 will produce `our_stt` turns with role `agent` (the agent channel of a declared stereo map). A metric requiring `agent_transcript` would then read our own STT as if it were the agent's transcript.
- Rule needed (ADR 0027): a simulated run lists `agent_transcript` in `available_inputs` only when an agent-produced transcript (an export or telemetry) is in the bundle.
- The scorer selects the agent's side by `source`, never by role alone.
- The 0.0.2 fix is BL-193 extended.

**F7. The expected-outcome check cannot be honestly scored in Phase 4.**
- `ConfigSnapshot` requires an `llm_judge` expected-outcome check as a required rubric item. There is no judge (BL-173).
- Recommendation (Q19): the inert check of ADR 0023 rule 17 again, with no RunVerdict. Every output says "outcome not evaluated".
- The judge, with Indic human-label calibration, is its own phase (a BACKLOG row, not a cut).

**F8. The only key is Sarvam, and Sarvam covers all three roles.**
- TTS: `POST https://api.sarvam.ai/text-to-speech`.
  - Fields `text`, `language_code`, `speaker`, `model` (default `bulbul:v3`), `pace`, `speech_sample_rate` (8000 to 48000) and `output_audio_codec` (including `linear16`, `mulaw`, `alaw`, `wav`).
  - At most 2,500 characters for v3. Codes cover all nine in-scope languages.
  - The reply is `{request_id, audios[base64 WAV]}`.
- LLM: `POST https://api.sarvam.ai/v2/chat/completions`.
  - Models `sarvam-105b`, `glm5.3`, `gemma4`, `deepseekv4.1-flash`.
  - `seed` is accepted, but the docs say "Output is not guaranteed to be identical across requests, even at `0`".
  - Usage comes back as `prompt_tokens`/`completion_tokens`.
  - `tools` (functions) are supported.
- Not verified:
  - TTS and LLM pricing and currency (assume INR, so cost is `not_available`).
  - A streaming TTS WebSocket (the TTS page only implies a streaming mode).
  - Whether Sarvam STT accepts 8 kHz telephony audio.
  - Per-model language quality of the LLM.

**F9. WebRTC brings a heavy closure, and the signalling is undocumented.**
- `aiortc` 1.15.0 is BSD-3-Clause. It requires `av` (PyAV, whose wheels bundle FFmpeg; the licence of the bundled build is not verified), `cryptography`, `pyopenssl`, `pylibsrtp`, `aioice`, `google-crc32c` and `pyee`.
- The SmallWebRTC docs page names `SmallWebRTCTransport` and `SmallWebRTCConnection` but no signalling route or body. The `/api/offer` route is not verified, and no Python client counterpart is documented.
- Update 2026-10-27 (BL-291, BL-301; ADR 0026 "Dialect specifications"): the signalling is now read in the source at tag `v1.14.0`: `POST /api/offer` (`sdp`, `type`, `pc_id`, `restart_pc`, `request_data`; answer `sdp`, `type`, `pc_id`) and `PATCH /api/offer` for trickled ICE candidates; `/client/` is the web UI, not signalling. Still not verified: the codec, the sample rate, whether audio needs the client's data channel, and whether aiortc (which gathers all candidates before the offer) needs the PATCH. The text above is kept as history.

**F10. Measuring at the simulator is honest only if both clock edges are ours.**
- What can be measured without a quality claim:
  - Call setup.
  - The time from connect to the first non-zero agent sample received.
  - The gap from the last caller frame sent to the first non-zero agent sample received.
  - The overlap of agent audio with caller audio.
  - The time for agent audio to stop after a scripted barge-in.
- What must be stated with every value:
  - It is "observed at the simulator over <dialect>". It includes the network and both sides' buffering. It is not the agent's internal latency.
  - Dead air that the simulator itself caused (LLM plus TTS time in LLM mode) is measured separately and excluded from agent gaps.

---

## 0. Objective, non-goals, done

**Objective.**
- `tracer simulate` places one simulated call from a persona-driven caller to one agent under test. The caller is either scripted (`Evaluator.scripted_turns`, deterministic audio from a content-addressed TTS cache) or an LLM caller (Sarvam chat v2) with injection hardening.
- Transports:
  - WebSocket, in two named dialects: Pipecat protobuf `/ws-client`, and a telephony media stream compatible with Twilio Media Streams on `/ws`.
  - WebRTC, to Pipecat SmallWebRTC, loopback-proven.
  - SIP/PSTN dial-out through the user's own telephony account, as a second gate (P4-G2) after the owner answers.
- It records two channels: channel 0 is the caller exactly as sent; channel 1 is the agent as received.
- It produces:
  - a `script` transcript of the caller lines, with exact send times;
  - our BYOK STT of the agent channel (Sarvam, role `agent` from the declared map);
  - the agent's own export when the user supplies it (existing VoicEra reader);
  - timing records `tracer.timing/1`;
  - the outcome `tracer.sim_outcome/1`;
  - a RunBundle with `mode=simulated`, `caller_origin=simulated` and `observed_tier=T0`, scored by the Phase 3 path (agreement, per-role partition) plus new timing definitions with no pass rule.
- It stores the run and writes the report.
- Nothing claims accuracy, WER, CER, recall, precision, agent quality, task success or persona realism.

**Non-goals (phase boundaries; each gets a BL row when deferred):**
- The LLM judge and RunVerdict.
- Languages other than mr (BL-158 to BL-163).
- Agreement of caller-role export lines against the script.
- T1/T2 ingest (OTLP, Langfuse, RTVI `bot-output` text).
- Daily and LiveKit transports.
- Suite or batch mode and concurrency above 1.
- Cloned or custom voices.
- A Pipecat Evals scenario importer.
- Dograh and VoicEra adapters.
- The schema 0.0.2 fields (BL-015, BL-180 to BL-182).
- A public tunnel by default.
- STUN/TURN by default.

**Done means:**
- Every gate item in D holds on hosted CI at the gate commit, with the run id written down.
- Every named test exists, with red-first evidence under `docs/phase4/evidence/red/<batch>/`.
- `tracer-evalschema` is byte-unchanged against the Phase 4 baseline (D4).
- The owner-gated runs are logged with counts only.
- The P4 measurement report follows its template.

No cut list (owner rule): if a task slips, the date moves. PSTN/SIP is sequenced behind owner accounts (P4-G2), not cut.

**Package split (Q3):**

| Package | Holds | Dependencies |
|---|---|---|
| `tracer-evalcore` (extended) | `TtsEngine`, `CallerBrain`, `LlmEngine` protocols and error taxonomy; call state machine; turn-taking policy; agent-activity rule; recorder timeline; G.711 decode; timing extraction; prompt builder and output validator; timing definitions; simulated bundle builder | unchanged: evalschema and stdlib, no network import |
| `tracer-evalproviders` (extended) | Sarvam TTS REST, Sarvam chat v2 REST, per-channel STT mapping, TTS cache, telephony REST (P4-G2) | unchanged: httpx |
| `tracer-evalsim` (new, `tracer_evalsim`) | asyncio call runner; wire dialects; paced sender; WebRTC client (extra `[webrtc]`) | `websockets` (BSD-3, bounded range), `protobuf` (BSD-3, only if Q5 keeps the protobuf dialect), `aiortc` (extra only), evalcore, evalschema. Update 2026-10-27 (E-b): `protobuf` is dropped; the protobuf dialect is encoded and decoded by hand (ADR 0026 rules 1 and 3), so the runtime dependencies are `websockets` and `aiortc` (extra) only |
| `tracer-cli` (extended) | `tracer simulate` command, report extension, store wiring | adds evalsim |
| `tools/samplebot/` (not a workspace member, own lock) | Pipecat sample agents for loopback interop and owner-gated runs | `pipecat-ai==1.14.0` exact, plus its closure (reviewed separately, never in a tracer wheel) |

---

## A. Task table

- **Owners:** SA (schema-architect), dev, qa, IL (indic-linguistics), SL (security-licensing), lead, reviewer.
- **qa** writes every named test red first. Hours include qa red-test time.
- **Tier:** every simulated run is T0. The T-DEG matrix is P4-29.
- **Test categories:**
  - Unit tests open no socket; they use injected fakes and in-memory transport pairs.
  - `@pytest.mark.integration` tests are loopback only (127.0.0.1, ::1, localhost), run in a separate CI job, and are never run with a key.
  - Real calls are owner-gated steps, never tests.
- **OWNER-GATED** marks a task that needs an owner answer, account, key, number, spend value or a real call.

### A.0 ADRs, licences, scaffold (batch 0)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-00a | SA | ADR 0026 "Simulated caller: architecture, wire dialects, call state machine, order of operations, limits" (Q2 to Q11, Q15 to Q18) | Accepted; numbered rules with rejected options and trade-offs; each rule cites a test; dialect specs cite public docs with retrieval dates (Twilio Media Streams and the Pipecat protobuf frame format fetched at writing time) | `test_adr0026_rules_are_tested` | 10 |
| P4-00b | SA | ADR 0027 "Simulated-run records" (F5, F6, F10, Q12 to Q14, Q19): stereo artifact and declared `ChannelMap` (method `tracer_evalsim_stereo/1`); `script` transcript for caller lines (`metadata["tracer.caller_text_origin"]` = `scripted` or `llm`); `our_stt` agent-channel transcript with role `agent`; `available_inputs` rules (F6); `tracer.sim_profile/1`, `tracer.sim_outcome/1`, `tracer.timing/1` JSON Schemas; privacy markers (derivation `false` once role evidence exists; no consent and no retention, P-46) | Accepted; three document JSON Schemas machine-checked | `test_adr0027_rules_are_tested`, `test_adr0027_documents_match_json_schemas` | 8 |
| P4-00c | SA+SL | ADR 0028 "Caller brain: prompt contract, injection hardening, scripted and replay modes" (Q8, Q9) | Accepted; lists what enters the prompt (persona, instructions, profile values) and what never does (`expected_outcome`, metric definitions, keys, rubric); fences agent text as data; output validator; caps | `test_adr0028_rules_are_tested` | 6 |
| P4-00d | SA | Amend ADR 0024 (TTS and LLM adapters, several BYOK variables, per-channel STT with declared role, 8 kHz and mulaw input) and ADR 0023 (per-role partition for simulated runs; rule 1 claim list extended with latency words); ADR 0004 (F6 producer rule); index revision bump | Amendments logged | `test_adr0024_rules_are_tested` (extended), `test_adr0023_rules_are_tested` (extended), `test_adr0004_simulated_agent_transcript_rule_cites_test` | 6 |
| P4-00e | SA | ADR 0029 "Telephony dial-out" (design only; options of Q20; allowlist, rate limits, authorisation record) | Status Proposed until the owner answers O5; then Accepted (P4-40) | `test_adr0029_rules_are_tested` | 5 |
| P4-00f | IL | mr persona and voice validity table (Sarvam bulbul voices x `mr-IN`, recorded as observed facts, never as accent claims); synthetic mr scripted-turn fixtures (invented text, `needs_native_review: true`); the allowed character set for LLM caller lines in mr (Devanagari block, danda, ASCII digits and punctuation) | Fixture `packages/evalcore/tests/fixtures/sim_mr.json` staged | (fixtures) | 5 |
| P4-01 | dev | Scaffold `tracer-evalsim` (SPDX, `py.typed`, ruff and mypy, bounded ranges tested at the lower bound, `lowest-direct` leg for websockets, wheels without tests); extend import whitelists; `tools/samplebot/` as its own uv project (not a workspace member) | evalcore still imports no network module; evalsim is the only importer of `websockets` and `aiortc` | `test_evalsim_runtime_imports_whitelist`, `test_evalcore_imports_no_network_module` (existing, unchanged), `test_evalcore_imports_no_asyncio_network` , `test_phase4_requires_dist_bounded_ranges`, `test_evalsim_wheel_contains_no_tests`, `test_evalschema_unchanged_since_phase4_baseline`, `test_tracer_simulate_help_runs`, `test_samplebot_not_a_workspace_member_and_not_in_uv_lock` | 8 |
| P4-02 | SL | Licence review `docs/security/P4-02-licenses.md`: `websockets`, `protobuf`, the `aiortc` closure (read the licence files actually bundled in the locked `av` wheel); the `tools/samplebot` closure (`num2words` LGPL, `soundfile`/libsndfile, `onnxruntime`, `numba`, `sentencex`, `loudness`, and `soxr` 1.0.0 LGPL-2.1-or-later with libsoxr, added 2026-10-27 (D-d)), recorded as tool-only and never in a tracer wheel; `[runtime.evalsim]` and `[runtime.evalsim.extras_webrtc]` in `license-policy.toml`; `pipecat-ai` added to `[[rejected]]` for runtime with the reason | `check_licenses` ok; every licence read from files; any copyleft runtime item is a named, owner-rule exception (do not dwell on copyleft). Update 2026-10-27 (E-b): `protobuf` is not used (dropped; ADR 0026 rule 3); its review stays in `docs/security/P4-02-licenses.md` as history, and the `[runtime.evalsim]` closure and the overrides carry no `protobuf` entry | `test_license_policy_lists_phase4_closures`, `test_no_lgpl_or_gpl_in_lock` (existing, unchanged unless a named spec change is logged), `test_license_exceptions_exact` (existing; a named spec change if av/FFmpeg needs an exception), `test_samplebot_closure_reviewed_and_tool_only` | 8 |

### A.1 Pure core (batch A, evalcore; clock and randomness injected)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-03 | dev | Protocols and taxonomy: `TtsEngine` (text, voice, rate, codec in; PCM out plus a usage record), `LlmEngine` (messages in; text plus usage out), `CallerBrain` (scripted, llm, replay); provider-neutral errors `Tts*`, `Llm*` (facts only, raised outside handlers, no cause and no context, as finding F-B2); `UsageStreaming*` variants (the R1 lesson) | Same shape as the `SttEngine` boundary | `test_tts_llm_errors_hold_facts_only_and_no_chain`, `test_engines_stream_usage_before_next_request`, `test_brain_protocol_accepts_scripted_llm_replay` | 4 |
| P4-04 | dev | Call state machine: states `preflight`, `connecting`, `connected`, `caller_speaking`, `awaiting_agent`, `agent_speaking`, `ending`, `ended`; end reasons `script_complete`, `caller_end_call`, `agent_hangup`, `max_duration`, `max_turns`, `max_silence`, `kill_switch`, `setup_failed`, `transport_error`, `brain_invalid_output`, `spend_cap`; the smallest of `Evaluator.max_duration_ms` and the run cap wins | Pure, table-driven, every transition tested; no state is entered after `ended` | `test_state_machine_transition_table_exact`, `test_max_duration_is_min_of_evaluator_and_cap`, `test_kill_switch_reaches_ended_from_every_state`, `test_no_event_after_ended`, `test_setup_failure_is_an_end_reason_not_an_exception` | 10 |
| P4-05 | dev | Turn-taking: scripted `wait_for_agent_silence_ms` (a sim-profile parameter, required, no code default) and an optional per-turn `barge_in_after_ms`; agent activity = a received frame holding any non-zero sample (no threshold, no model); `max_silence_ms` required | Deterministic on recorded frame sequences | `test_agent_activity_is_any_nonzero_sample_no_threshold`, `test_wait_for_silence_parameter_has_no_default`, `test_barge_in_fires_at_offset_from_agent_onset`, `test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang`, `test_simulator_never_speaks_while_brain_pending` | 8 |
| P4-06 | dev | Recorder: one monotonic timeline; caller frames placed at their exact send time; agent frames placed at max(arrival, end of the previous agent frame) (playback model, documented); stereo 16-bit WAV, caller left (0) and agent right (1), at the dialect rate; pure G.711 mulaw and A-law decode checked against the published ITU table; never resampled | Same frame log gives byte-identical WAV | `test_recorder_is_deterministic_from_frame_log`, `test_recorder_channel_0_is_exactly_what_was_sent`, `test_recorder_burst_arrival_uses_playback_model`, `test_g711_mulaw_alaw_decode_matches_itu_table`, `test_recorder_refuses_rate_mismatch_never_resamples` | 10 |
| P4-07 | dev | Timing extraction into `tracer.timing/1` (integers in ms and frame counts only): connect to first agent sample; per caller turn the gap from the last caller frame sent to the first agent sample after it; overlap ms; agent stop time after barge-in; simulator self-latency (brain plus TTS) recorded separately; quantum = frame size, stated | Each value carries its quantum and its dialect; no aggregate except the defined ones. Update 2026-10-27 (D-a): a timing value that needs a missing edge is absent and its result is `error` with the code `timing_edge_missing` (ADR 0027, BL-292): never 0, never false, never a valid `not_applicable` | `test_timing_first_agent_audio_exact_on_synthetic_log`, `test_timing_gap_measured_from_last_sent_frame`, `test_simulator_self_latency_excluded_from_agent_gap`, `test_timing_values_carry_quantum_and_dialect`, `test_timing_value_without_both_edges_is_error_never_zero` (renamed 2026-10-27 (D-a); was test_no_timing_value_without_both_edges_is_not_applicable) | 8 |

### A.2 Providers (batch B, evalproviders; httpx MockTransport only)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-08 | dev | Sarvam TTS REST adapter: fixed URL, `SARVAM_API_KEY`, explicit `model` and `speaker` always sent (no server default), `language_code=mr-IN` only (others refused, BL-162), `speech_sample_rate` equal to the dialect rate, codec `linear16`, or `mulaw` for telephony; 2,500-character cap checked before send; base64 decoded with a size cap; retries and taxonomy as ADR 0024 rule 9 | Strict reply validation | `test_sarvam_tts_sends_explicit_model_speaker_language_rate`, `test_sarvam_tts_refuses_non_mr_and_over_2500_chars_before_send`, `test_sarvam_tts_response_rejects_wrong_shape_and_oversize`, `test_sarvam_tts_retry_taxonomy_matches_stt_rules`, `test_sarvam_tts_base_url_not_overridable` | 7 |
| P4-09 | dev | Sarvam chat v2 adapter: explicit model (no default, Q10), `max_tokens` required, `seed` sent and recorded but documented as not a determinism guarantee; usage mapped to `input_tokens`/`output_tokens` (`cache_read` split as ADR 0014); `reasoning_effort` explicit for reasoning models; no `tools` except Q9's `end_call` | Reply text cap; usage record per request | `test_sarvam_llm_model_and_max_tokens_required`, `test_sarvam_llm_usage_maps_tokens_exactly`, `test_sarvam_llm_seed_recorded_never_claimed_deterministic`, `test_sarvam_llm_response_rejects_wrong_shape_and_oversize` | 7 |
| P4-10 | dev | Per-channel STT: chunk plan `tracer.chunk_plan/2` cut at the agent-activity segments (each agent reply is a chunk; above 25 s, ADR 0024 rule 7 applies inside it); Turns role from the declared channel map (`agent`), source `our_stt`; 8 kHz accepted only after an owner-gated check (BL row until then; fake engine in tests) | Phase 3 mono behaviour unchanged | `test_stt_agent_channel_turns_have_role_agent_from_declared_map`, `test_activity_chunk_plan_is_deterministic_and_covers_agent_audio`, `test_phase3_mono_mapping_unchanged`, `test_stt_never_runs_on_caller_channel_by_default` | 8 |
| P4-11 | dev | Content-addressed TTS cache under `--out`/`tts-cache/` (key sha256 of provider, model, speaker, language, rate, codec, pace and text; mode 0700/0600; `O_NOFOLLOW`); a cache hit makes no request and no UsageRecord | Re-run spends 0 TTS requests for scripted turns | `test_tts_cache_hit_sends_nothing_and_records_no_usage`, `test_tts_cache_key_covers_every_parameter`, `test_tts_cache_refuses_links_and_is_private` | 4 |
| P4-12 | dev | Usage, cost and call log: UsageRecords (`evaluation`, `sim_caller_tts` `input_characters`+`requests`; `sim_caller_llm` tokens+`requests`; `stt`); cost `not_available` without a user USD catalog; `sim_call_log.jsonl` (counts, ids, timings, end reason; no text, no number, no key) | | `test_sim_usage_records_per_request_with_stages`, `test_sim_cost_not_available_without_usd_catalog`, `test_sim_call_log_has_no_text_no_number_no_key`, `test_sim_call_log_written_before_and_after_each_request` | 5 |

### A.3 Caller brain (batch C)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-13 | dev+SL | Prompt builder and output validator: system text = fixed template + persona language + `instructions` + resolved profile values (synthetic only); agent text inserted only inside a fenced data block, escaped, labelled untrusted; `expected_outcome`, metric text and keys never in any request; output: one line, at most N characters (sim-profile, required), only the IL allowed set for the persona script, at most one regeneration, else end reason `brain_invalid_output`; `end_call` per Q9 | Request bodies built offline and inspected. Update 2026-10-27 (D-b, ADR 0028): the LLM brain receives the text of each finished agent segment, transcribed during the call, before it answers; scripted and replay brains need no agent text during the call. Hours to be re-estimated by the reviewer. | `test_expected_outcome_never_in_any_llm_request`, `test_agent_text_only_inside_fenced_data_block`, `test_caller_output_outside_persona_script_refused_once_then_ends`, `test_caller_output_length_cap_required`, `test_prompt_template_hash_recorded_in_sim_profile` | 8 |
| P4-14 | qa | Adversarial injection suite: a fake agent says "ignore previous instructions", asks for the system prompt or keys, dictates a phone number, sends bidi, markup or 10k-character lines; and a fake LLM that returns injections back | No outcome text, no key canary and no agent-dictated number ever reaches TTS, the call log, stdout or a dial action | `test_injection_from_agent_never_changes_dial_target_or_tools`, `test_injection_cannot_leak_expected_outcome_or_key_canary`, `test_adversarial_agent_text_never_breaks_report_or_store` | 6 |
| P4-15 | dev | Replay brain `--caller fake:<dir>` (`turn-NNNN.json`) and a scripted brain; scripted mode makes zero LLM requests | | `test_scripted_mode_makes_no_llm_request`, `test_replay_brain_missing_file_is_a_refusal_before_connect` | 3 |

### A.4 Transports (batch D, evalsim)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-16 | dev | Dialect `pipecat-protobuf` (Pipecat `/ws-client`), clean-room from public documentation (no Pipecat source copied; the frame message set listed with retrieval date; if not documentable, Q5 falls back and the dialect moves to BL); paced sender | Unit: in-memory pair; integration: P4-27 | `test_protobuf_dialect_roundtrip_in_memory`, `test_protobuf_dialect_unknown_message_ignored_and_counted`, `test_dialect_spec_cites_docs_with_retrieval_date`; added 2026-10-27 (BL-298..301): `test_protobuf_codec_is_hand_written_no_generated_or_pipecat_module` (also asserts no `google.protobuf` import, E-b), `test_notice_carries_pipecat_bsd2_attribution`, `test_bearer_token_from_env_never_stored`, `test_bearer_token_only_in_authorization_header_never_path_or_query`, `test_missing_auth_token_env_refused_before_socket`, `test_origin_header_from_entry_or_absent` (the token and Origin rules apply to both WebSocket dialects; P4-17 reuses them); hours to be re-estimated by the reviewer | 10 |
| P4-17 | dev | Dialect `twilio-media-stream` (JSON `connected`/`start`/`media`/`mark`/`stop`, mulaw 8 kHz), to Pipecat `/ws`; synthetic stream and call ids (never real-looking SIDs) | | `test_twilio_dialect_message_sequence_exact`, `test_twilio_dialect_ids_are_synthetic_and_marked`, `test_twilio_dialect_agent_clear_message_is_barge_in_evidence`; added 2026-10-27 (BL-298..301): `test_twilio_target_with_bot_credentials_refused`, `test_twilio_entry_without_credentials_field_refused`, `test_twilio_call_ends_by_socket_close_after_stop`, `test_twilio_dialect_mark_optional_never_awaited`; hours to be re-estimated by the reviewer | 8 |
| P4-18 | dev | WebRTC client (`[webrtc]` extra, aiortc), signalling per ADR 0026 after verifying the Pipecat route at the pinned tag; no ICE servers by default (host candidates only; an explicit `--ice-server` is required for non-loopback, logged) | Loopback-proven only in Phase 4 | `test_webrtc_no_stun_by_default`, `test_webrtc_signalling_body_matches_pinned_pipecat`, `test_webrtc_extra_absent_gives_clear_refusal` | 14 |
| P4-19 | qa+dev | No-network fake agents (pure Python, `tests/support`): a deterministic agent for each dialect with injected reply delays, silence, comfort noise, hang-up, garbage frames and bursts | Used by every unit and e2e test | (fixtures) `test_fake_agents_are_deterministic` | 8 |
| P4-20 | dev | Real-time pacing (20 ms frames, injected clock), backpressure, a sender that never bursts, and a wall-clock watchdog independent of the state machine | | `test_sender_paces_real_time_with_injected_clock`, `test_watchdog_hangs_up_even_if_state_machine_stalls` | 5 |

### A.5 Orchestration, CLI, report, store (batch E). Milestone M1: runnable offline after P4-23 with `fake:` engines.

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-21 | dev | Simulate pipeline with the pinned order of operations (Q15): (1) parse and validate every input; (2) allowlist check of the target; (3) authorisation and terms gates; (4) required spend caps present; (5) output probe (create nothing); (6) engine factories read keys; (7) pre-synthesise all scripted turns (usage streamed to recovery); (8) open the store; (9) connect or dial; (10) call loop; (11) hang up; (12) post-call STT of the agent channel; (13) scoring; (14) store write; (15) report. A `BaseException` anywhere after step 6 keeps the usage (`usage-recovery-<UTC>.json`). Bundle: Run `simulated`/`simulated`/T0, channel map declared, `available_inputs` exact (F6), `started_at` = connect instant (our clock, stated), `check_run_bundle` | Update 2026-10-27 (D-b, ADR 0028): with `--caller sarvam` (the LLM caller) each finished agent segment is transcribed during the call loop (step 10), because the brain needs the agent's text before it answers; step (12) then holds only what is not yet transcribed. Scripted and replay modes may transcribe the agent channel after the call (step 12). Hours to be re-estimated by the reviewer. | `test_order_of_operations_pinned_by_call_trace`, `test_refusals_happen_before_any_key_read_or_connect`, `test_usage_survives_ctrl_c_mid_call`, `test_simulated_bundle_passes_check_run_bundle`, `test_available_inputs_never_lists_agent_transcript_without_agent_record`, `test_privacy_marker_false_only_with_role_evidence` | 12 |
| P4-22 | SA+dev | Scoring: (a) the Phase 3 agreement scorer with a per-role partition (agent-role export turns against agent-channel `our_stt`; new scorer version; caller-role lines not compared, BL); (b) timing definitions, kind `latency`, unit `ms`, `requires=["audio","turn_timing"]`, `language_independent`, no pass rule: `sim_first_agent_audio_ms`, `sim_agent_response_gap_median_ms` (nearest-rank, N shown), `sim_overlap_ms`, `sim_barge_in_stop_ms`, `sim_answered_caller_turns` (count); (c) the inert expected-outcome check (F7) | Update 2026-10-27 (D-c, ADR 0023 amendment A3): a simulated run reports an STT turn in a language other than mr as `not_applicable`, where a recorded run reports `error`; the simulated agent may legitimately speak another language, and the per-role partition handles it. | `test_timing_definitions_have_no_pass_rule`, `test_median_is_nearest_rank_with_n_recorded`, `test_agreement_partition_compares_agent_role_only`, `test_partition_changes_scorer_version`, `test_outcome_check_inert_and_no_verdict` | 8 |
| P4-23 | dev | `tracer simulate --target <agent file> --allowlist <file> --evaluator ... --persona ... [--profile ...] --sim-profile <file> --tts sarvam\|fake:<dir> --caller scripted\|sarvam\|fake:<dir> --stt sarvam\|fake:<dir> --max-call-seconds N --max-tts-chars N --max-llm-tokens N --max-stt-seconds N --authorisation-reference TEXT --out <dir> [--agent-transcript <file>] [--dry-run]`: every cap required for any real engine, no defaults; exit codes 0, 1, 2, 3, 4, 64 as in Phase 3, plus `5 call not established` (Q16); SIGINT and SIGTERM hang up gracefully, a second signal aborts, usage kept | | `test_simulate_requires_caps_for_real_engines`, `test_simulate_dry_run_prints_plan_and_sends_nothing`, `test_simulate_exit_codes`, `test_simulate_output_never_claims_accuracy_or_quality`, `test_simulate_never_prints_target_number_or_key` | 10 |
| P4-24 | dev | Report: stereo player (relative audio, existing CSP and seek script), timeline of caller and agent intervals, timing table with quantum and dialect, a "synthetic caller audio" label (terms 10.2(8)), "outcome not evaluated", usage and cost; Chrome and Edge only | | `test_sim_report_labels_synthetic_audio`, `test_sim_report_states_outcome_not_evaluated`, `test_sim_report_timing_shows_quantum_and_dialect`, `test_sim_report_never_claims_accuracy_or_quality`, `test_key_never_in_store_and_report_outputs` (extended to three keys) | 8 |
| P4-25 | dev | Store: round trip of the simulated bundle on SQLite, plus Postgres parity (`postgres` marker, loopback) | | `test_simulated_run_round_trips_through_store_byte_identical`, `test_simulated_bundle_stores_on_both_backends` | 4 |

### A.6 Interop, end to end, T-DEG, guards (batch F)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-26 | dev | `tools/samplebot/`: (a) `scripted_bot`, a Pipecat 1.14.0 pipeline with no model (a timer-based end-of-caller detector, pre-recorded replies with known delays, no VAD model) for `/ws-client`, `/ws` (Twilio) and SmallWebRTC; (b) `llm_bot` per owner answer O3 (OWNER-GATED to run) | Own lock; README; never imported by tracer | `test_samplebot_scripted_bot_uses_no_model` (static check of its pipeline); added 2026-10-27 (BL-298..301): `test_samplebot_twilio_runs_without_auto_hang_up_or_credentials` (static check of its configuration, ADR 0026 rule 6); hours to be re-estimated by the reviewer | 12 |
| P4-27 | qa | Integration (loopback, separate CI job, no key): evalsim against `scripted_bot` on each dialect; apparatus calibration: measured gap equals the injected delay within one frame quantum plus stated jitter, over 20 calls each | Calibration numbers go to the measurement report, not to a claim | `test_integration_protobuf_dialect_against_pipecat`, `test_integration_twilio_dialect_against_pipecat`, `test_integration_webrtc_against_pipecat_loopback`, `test_integration_calibration_gap_within_quantum` | 10 |
| P4-28 | qa+IL | Offline e2e: synthetic mr scripted call through the CLI with fake TTS, STT and agent; exact outputs (WAV bytes, transcripts, timing, report bytes stable) | | `test_e2e_sim_synthetic_mr_exact_outputs`, `test_e2e_sim_report_and_store_consistent` | 6 |
| P4-29 | qa | T-DEG matrix for simulated runs: setup failure; agent silent; comfort-noise agent; no export; export present; STT failure; kill switch mid-call; max_duration; max_silence; spend cap hit. Identical record kinds and schema; `not_applicable` never failure or zero | Update 2026-10-27 (D-a): when the agent never speaks, each timing value that needs an agent edge is absent and its result is `error` with the code `timing_edge_missing` (ADR 0027, BL-292): never 0, never false, never a valid `not_applicable` (0.0.1 cannot store one for it) | `test_tdeg_sim_setup_failed_all_not_applicable_exit_5`, `test_tdeg_sim_agent_silent_timing_not_measured` (renamed 2026-10-27 (D-a); was test_tdeg_sim_agent_silent_timing_not_applicable), `test_tdeg_sim_no_export_agreement_missing_input`, `test_tdeg_sim_export_present_agreement_scored`, `test_tdeg_sim_stt_failure_missing_input_transcript`, `test_tdeg_sim_kill_switch_keeps_partial_recording_and_usage`, `test_tdeg_sim_schema_identical_across_cases` | 6 |
| P4-30 | qa+SL | No-network guard extended to evalsim (integration marker loopback-only); canary keys across every output channel (STT, TTS, LLM, telephony: exception, repr, logs at DEBUG, call log, store, report, recovery file, TTS cache names); guard extended to staged `*.wav` under any out dir, `sim_call_log.jsonl`, `tts-cache/`, and allowlist files | | `test_unit_tests_open_no_socket` (extended), `test_integration_tests_loopback_only`, `test_key_canaries_never_in_any_output_channel`, `test_guard_rejects_staged_sim_outputs_and_allowlist` | 5 |

### A.7 Safety and audit (batch G)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-31 | dev+SL | Target safety: the allowlist file (git-ignored, 0600, exact match after normalisation: ws or wss host:port/path, or an E.164 number) is checked before any key read; a target not on it is refused (exit 4); loopback targets still need an allowlist entry; one call per invocation; minimum interval between calls (lock file in `--out`); a kill-switch file checked every frame | | `test_target_not_on_allowlist_refused_before_any_key_or_socket`, `test_allowlist_mode_and_link_rules`, `test_one_call_per_invocation_and_min_interval`, `test_kill_switch_file_hangs_up_within_one_frame`; added 2026-10-27 (BL-298..301): `test_allowlist_entry_fields_unknown_or_repeated_refused` (ADR 0026 rule 16 entry fields); hours to be re-estimated by the reviewer | 6 |
| P4-32 | SL | Security audit `docs/security/P4-32-audit.md`: keys (three providers), prompt injection, allowlist, recordings and caches, terms F4, order of operations, recovery, guard, supply chain (aiortc/av, websockets), DoS by a hostile agent (frame floods, huge messages: caps). Must be GO before P4-34 | No open P0 or P1 | `test_p4_audit_items_cite_tests` | 10 |

### A.8 Owner gates, real runs, measurement, docs (batch H)

| ID | Owner | Task | Acceptance | Tests | h |
|---|---|---|---|---|---|
| P4-33 | lead | **OWNER-GATED.** Owner questions O1 to O9 (section E), asked one at a time in chat; numeric values pre-registered in the decision log before any real result | Answers logged | none | 1 |
| P4-34 | lead | **OWNER-GATED.** First real call R1, by the run book: dry run; scripted mr evaluator; `scripted_bot` (no agent model) on loopback over `/ws-client`; real Sarvam TTS (pre-synthesis) and STT; counts only | Logged before and after; no text | none (counts in the measurement report) | 4 |
| P4-35 | lead | **OWNER-GATED.** R2: LLM caller (Sarvam chat v2) against the O3 agent on loopback; then the WebRTC dialect once | Counts only | none | 3 |
| P4-36 | lead+qa | **OWNER-GATED** (number of repeats, O4). Measurement report `docs/phase4/measurements/<date>-apparatus.md` from `TEMPLATE.md`: apparatus calibration (P4-27), R1 repeated N times (caller WAV byte identity from the cache, agent-side variation, timing spread as counts and ranges), measurement scripts under `scripts/measurements/` with their sha256 | Template checker green | `test_p4_measurement_report_follows_template` | 6 |
| P4-37 | SA+dev | Docs: `docs/concepts/simulated-calls.md` (a "Not claimed" section with one fixed fenced sentence), CLI README offline quick start for `tracer simulate` with `fake:` engines, CHANGELOGs, evalsim README | | `test_sim_readme_quick_start_runs_offline`, `test_sim_concept_page_states_no_claims` | 7 |
| P4-38 | lead | BACKLOG rows at deferral time; HANDOFF refresh | | none | 2 |
| P4-G | reviewer | Gate (P4A: WS and WebRTC) | | none | 8 |

### A.9 Telephony, second gate (batch P; every task OWNER-GATED on O5)

| ID | Owner | Task | Acceptance | Tests | h |
|---|---|---|---|---|---|
| P4-40 | SA | ADR 0029 Accepted for the provider the owner names | | `test_adr0029_rules_are_tested` | 3 |
| P4-41 | dev | Telephony adapter: provider REST dial-out through httpx (fixed host, BYOK credentials, allowlisted `to` only, the owner's caller id), and the media leg through the matching media-stream dialect (P4-17 if Twilio-compatible). An exposed endpoint, if needed, only through an owner-approved tunnel with a one-time token. Or an external SIP user agent (Q20) | Fake provider tests; no real dial in tests | `test_dial_refuses_number_not_on_allowlist_before_any_request`, `test_dial_credentials_never_in_any_output`, `test_dial_hangup_on_every_end_reason`, `test_media_endpoint_requires_one_time_token` | 16 |
| P4-42 | SL | Telephony audit delta (robocall clause 10.2(5), exposure of the media endpoint, numbers in records) | GO before P4-43 | `test_p4_telephony_audit_items_cite_tests` | 6 |
| P4-43 | lead | **OWNER-GATED.** First PSTN call to the owner's allowlisted number only; counts only | | none | 3 |
| P4-G2 | reviewer | Telephony gate | | none | 4 |

**Totals:**

| Batch | Hours |
|---|---|
| 0 | 56 |
| A | 40 |
| B | 31 |
| C | 17 |
| D | 45 |
| E | 42 |
| F | 39 |
| G | 16 |
| H | 31 |
| P4A (batches 0 to H) | **317** |
| P (telephony) | **32** |
| **Phase 4 total** | **about 349 h** |

At the observed pace of 80 to 130 task-hours per day, P4A is about 2.5 to 4 working days, and P4-G2 about 0.5 day more. The owner's answer times are not counted. For comparison: Phase 2 was 273 h and Phase 3 204 h.

---

## B. Plan, batches and dependency graph

**Precondition.** Batch 0 (docs) may start now. P4-01 (code) starts only after the Phase 3 gate is recorded as passed (C1 to C4 closed with commit ids). The baseline commit for D4 is that gate commit.

**Batches and order:**
- **0:** P4-00a, 00b, 00c, 00d, 00e, 00f; P4-01; P4-02.
- **A:** P4-03 to 07 (pure; parallel with B and C after P4-03).
- **B:** P4-08 to 12 (after P4-02 and P4-03).
- **C:** P4-13 to 15 (after P4-03 and P4-00f).
- **D:** P4-16 to 20 (after P4-01 and P4-02; P4-19 first).
- **E:** P4-21 to 25 (after A, B, C and D). **M1** = P4-23 offline.
- **F:** P4-26 to 30. P4-26 may start in batch 0.
- **G:** P4-31 and 32.
- **H:** P4-33, then P4-34, 35, 36, 37, 38, G. **M2** = P4-34.
- **P:** P4-40 to G2, only after O5 is answered. It runs in parallel with H once P4-32 is GO.

**Critical path (about 125 h):** P4-00a -> P4-01 -> P4-04 -> P4-05 -> P4-06 -> P4-19 -> P4-16 -> P4-21 -> P4-23 -> P4-28 -> P4-30 -> P4-32 -> P4-33 -> P4-34 -> P4-36 -> P4-G.

**Parallel work:**
- B and C run beside A.
- P4-17 and P4-18 run beside P4-16.
- P4-26 starts early.
- P4-24, P4-25 and P4-37 run after P4-21.

**Internal dependencies:**
- ADRs come before code. P4-02 must pass before any new dependency lands.
- P4-32 GO comes before any real call. O1 and O2 are answered before P4-34. O5 is answered before batch P.
- Phase 3 artefacts reused unchanged: `SttEngine`, chunker rule 7, normaliser `mr-Deva/1`, `tracer.flags/1`, store, report CSP, guard, recovery file, call-log rules.

**External dependencies:**

| Dependency | Pinned? | Failure mode |
|---|---|---|
| `tracer-evalschema==0.0.1`, `tracer-evaldb` | exact workspace pins | none expected; any gap goes to BACKLOG 0.0.2 |
| `websockets` | bounded range, lower bound tested (D3) | API change; async cancellation edge cases |
| `protobuf` | bounded range; only if the protobuf dialect is kept. Update 2026-10-27 (E-b): not a dependency; the dialect is kept and hand-written (ADR 0026 rules 1 and 3) | the Pipecat frame schema changes; dialect versioned |
| `aiortc` (`[webrtc]` extra) and `av` | bounded range; licence of the bundled FFmpeg **not verified** | wheel availability on 3.14/3.15; licence exception needed |
| `pipecat-ai==1.14.0` | exact, `tools/samplebot` only | weekly releases (1.14.0 uploaded 2026-10-24); deprecations announced for 2.0.0; the interop test is the drift alarm |
| Pipecat routes `/ws`, `/ws-client`, `/start`, protobuf serializer, `parse_telephony_websocket` | docs, retrieved 2026-10-27, **not verified at the 1.14.0 tag** | route or serializer drift; P4-16, 17 and 18 re-verify at the tag |
| SmallWebRTC signalling route and body | **not verified** (not on the docs page) | P4-18 reads the 1.14.0 source; if not stable, WebRTC goes to BL with the evidence |
| Twilio Media Streams JSON format | **not fetched**; P4-00a fetches it with a date | format drift; the dialect is versioned |
| Sarvam TTS REST (`bulbul:v3`, voices, 2,500 characters, rates, codecs) | unpinned API, dated 2026-10-27 | voice catalogue changes; model sent explicitly |
| Sarvam chat v2 (`sarvam-105b`, ...; seed not deterministic) | unpinned, dated 2026-10-27 | model churn; replies vary; scripted mode is the reproducible path |
| Sarvam STT at 8 kHz, TTS streaming, TTS/LLM pricing | **not verified** | 8 kHz checked on the first telephony-dialect real call; cost `not_available` |
| Sarvam terms v2.0, clauses 10.5(a), 10.2(5), 10.2(8), 10.3(6), 17.5 | n/a | owner gates O1 |
| Telephony provider, SIP trunk, numbers, India regulation for automated calls | owner-provided; regulation **not verified** | P4-G2 blocked until O5 |
| STUN/TURN | none by default | non-loopback WebRTC stays explicit and logged |
| Postgres 15/18, SQLite | as Phase 3 | as Phase 3 |
| Python 3.11 to 3.14 required, 3.15 non-blocking | as before | aiortc/av wheels on 3.15 **not verified** |

---

## C. Design questions (recommended option first; the lead auto-approves and logs; owner-only items are in E)

- **Q1 Schema.** No schema change in Phase 4.
  - The simulator configuration is in `tracer.sim_profile/1` inside `ConfigSnapshot.metadata` (digest-covered). The outcome is in `Run.metadata["tracer.sim_outcome/1"]`; timing in `tracer.timing/1`.
  - Rejected: schema 0.0.2 now (a migration and a frozen-version break in the same phase as async transports); persona fields in `Persona.metadata` (identity mixed with rendering).
  - BL-015 is re-targeted to a schema 0.0.2 phase. New BL rows: a `simulator` timing source, an `llm_caller` turn source, Run end reason and status.
- **Q2 Pipecat.** A thin protocol client; Pipecat only in `tools/samplebot` (F3).
  - Rejected: Pipecat as a runtime dependency (LGPL `num2words`, `onnxruntime`, `numba`, the `sarvamai` extra, weekly churn); building on Pipecat Evals (it needs the bot to run `EvalTransport`, so it is not black-box, F1).
  - Trade-off: we maintain the dialect code and must re-verify it at every pinned version.
- **Q3 Package.** A new `tracer-evalsim` for async network transports. Pure logic stays in evalcore; provider HTTP stays in evalproviders.
  - Rejected: inside evalproviders (mixes async sockets into the audited HTTP package); inside evalcore (breaks the no-network rule).
- **Q4 Concurrency.** asyncio in evalsim only; the core stays synchronous and pure with injected clocks.
- **Q5 Wire dialects.** `pipecat-protobuf` (`/ws-client`) and `twilio-media-stream` (`/ws`), each versioned (`/1`) and clean-room from public docs.
  - Rejected: the RTVI `eval-*` protocol (harness-only); every telephony serializer at once (scope).
  - If the protobuf frame format cannot be documented without copying Pipecat's `.proto`, the protobuf dialect goes to BL and Twilio-compatible becomes the only WebSocket dialect.
  - Update 2026-10-27 (BL-298, E-b): the dialect is kept; its format is hand-written from the public `frames.proto` at the cited tag, with no generated code and no `protobuf` dependency (ADR 0026 rules 1, 3 and 5). The fallback applies only if P4-16 cannot verify the message set line by line.
- **Q6 Turn-taking.** Wait-for-agent-silence plus an optional scripted barge-in offset.
  - Agent activity is "any non-zero sample received": no energy threshold and no VAD model, so nothing is invented and no model runs in Tracer.
  - `wait_for_agent_silence_ms` and `max_silence_ms` are required sim-profile values with no code default (the example file is labelled).
  - Rejected: Silero VAD inside Tracer (runs a model; owner rule); an energy threshold (an invented number).
  - Trade-off: an agent that streams comfort noise never looks silent, so the call ends on `max_silence` with timing `not_applicable`. It is named in the outcome, never a hang. (Update 2026-10-27, D-a: a timing value whose agent edge is missing is `error` `timing_edge_missing`, not `not_applicable`; ADR 0027, BL-292.)
- **Q7 Persona to voice.** An explicit table in the sim profile maps a persona to `{provider, model, speaker, pace, rate}`. There is no automatic mapping, and an accent is never inferred from a voice name. IL checks only that the language codes match.
  - Cloned or custom voices are refused in Phase 4; if ever allowed, the marker is `real_person_likeness` (ADR 0016).
- **Q8 LLM caller hardening.** As P4-13:
  - The agent's text is untrusted data in a fenced block.
  - The outcome and metric text are never sent.
  - Output is validated (script, length), with at most one regeneration.
  - The dial target is never derivable from any text.
  - There is no tool except end-call.
- **Q9 End of call by the caller.** A fixed end sentinel line, validated exactly. Tool-calling is not used.
  - Reason: tool support is model-dependent at Sarvam (not verified), and a sentinel is testable offline.
  - Trade-off: a model may say the sentinel inside a sentence; only an exact whole line counts.
- **Q10 Models.** `--caller-model` and the TTS `model`/`speaker` are required, with no code defaults. They are recorded as requested. `seed` is sent and recorded, never claimed.
- **Q11 Reproducibility.**
  - Scripted mode plus the TTS cache gives byte-identical caller audio.
  - LLM mode records every request and reply (synthetic caller lines plus agent text) in the store, and `fake:<dir>` replays them.
  - No determinism claim for the agent side.
- **Q12 Recording.**
  - Stereo, caller on the left, at the dialect rate.
  - A playback-time placement for agent frames.
  - Never resampled: the TTS is asked for the dialect rate.
- **Q13 Transcripts.**
  - Caller lines: a `script` transcript with exact send times (timing `audio_alignment` on the artifact timeline; reading recorded, BL for a simulator source).
  - The agent channel: `our_stt` with role `agent`.
  - The agent export only from `--agent-transcript`.
  - Our STT is never run on the caller channel (we know the text).
- **Q14 Privacy.**
  - Markers are written `true` with `marked_before_role_evidence` before role evidence exists, re-derived `false` at export once the bundle holds the declared map and turns (ADR 0016 Decision 7a).
  - The stored consent is `unknown` (the only truthful value for a simulator).
  - If a human is ever on the line (PSTN to a human-staffed number): refused by the allowlist, and BL for a "human present" mode.
- **Q15 Order of operations.** As P4-21, pinned by a call-trace test.
- **Q16 Exit codes.** Keep 0 to 4 and 64; add `5 call not established`. The run is still stored with all results `not_applicable`.
  - Rejected: reusing exit 3 (it means STT failed).
- **Q17 Limits and kill switch.**
  - Required caps: `--max-call-seconds`, `--max-tts-chars`, `--max-llm-tokens`, `--max-stt-seconds`. The smallest of the caps and `max_duration_ms` wins.
  - SIGINT/SIGTERM hang up gracefully; a second signal aborts. A kill-switch file is checked every frame; a watchdog runs independently.
  - Usage recovery applies at every step after the keys are read.
- **Q18 Rate limits.** One call per invocation; a minimum interval between calls (owner value O4); no suite mode (BL).
- **Q19 Outcome.** The inert expected-outcome check (ADR 0023 rule 17 pattern), no verdict, "outcome not evaluated" everywhere (F7).
- **Q20 Telephony** (ADR 0029). Recommend the provider REST dial-out plus a media-stream WebSocket into evalsim, with the provider the owner already has. It reuses P4-17 and keeps SIP signalling outside our code.
  - Rejected: PJSIP Python bindings (GPL-2 unless a commercial licence, which fails the no-GPL check); `pyVoIP` (GPL-3); an embedded SIP stack written by us (an audit burden).
  - Alternative if the owner has a SIP trunk only: an external `baresip` process (BSD-3, not in the Python lock), bridged by audio files or pipes; more hours (+12 h).
- **Q21 What Phase 4 measures:** call established (yes/no as an outcome), first agent audio ms, per-turn gaps with their median, overlap ms, barge-in stop ms, answered caller turns, and the simulator's own latency (separately). Agreement with the agent export applies only when supplied.
- **Q22 What Phase 4 must not claim:**
  - accuracy, WER, CER, error rate, recall or precision;
  - agent quality or task success;
  - the agent's internal latency;
  - persona realism or accent fidelity;
  - LLM caller determinism;
  - agent-side determinism;
  - any comparison between agents or dialects;
  - any aggregate across runs beyond the descriptive counts in the measurement report;
  - the plan's "WER against scripted turns" (the owner rule wins; BL row).

---

## D. Gate checklist (P4-G; all must hold; evidence = hosted CI logs plus paths)

1. Hosted CI green at the gate commit, run id recorded. The jobs:
   - tests on 3.11 to 3.14;
   - the lowest-direct legs for the new ranges;
   - the separate loopback `integration` job against `tools/samplebot`;
   - licences, guard, pinned gitleaks, pip-audit.
   - Local pinned gitleaks before every push.
2. Every test named in A exists under its exact name, with red-first evidence for every batch under `docs/phase4/evidence/red/`.
3. evalschema byte-unchanged against the Phase 4 baseline (D4). No earlier test weakened, skipped or deleted; any spec change is named, with before and after.
4. No socket in unit tests. Integration tests are loopback only. Key canaries pass for STT, TTS, LLM and telephony across every output channel. Gitleaks and the guard are clean.
5. The no-claim tests are green for the CLI, the report and the concept page. No `wer`/`cer` unit anywhere. Every timing value carries its quantum and its dialect.
6. Every cap and parameter without a default is refused when absent. The owner's values were pre-registered before R1.
7. The T-DEG matrix for simulated runs is green: identical schema, `not_applicable` never a failure or zero, exit 5 on a failed setup.
8. `check_run_bundle` passes for every produced bundle. The store round trip is byte-identical on SQLite, plus the Postgres parity test.
9. Licence review P4-02 is ok. `pipecat-ai` is absent from every tracer wheel and from `uv.lock`. Any copyleft runtime item is a named, recorded exception.
10. Security audit P4-32 has no open P0 or P1, and was finished before P4-34.
11. Owner gates O1 to O4 are answered and logged before R1. R1 and R2 are logged before and after with counts only. No call text, no prompt or reply text, and no number in any committed file.
12. Apparatus calibration is in the measurement report: measured gaps equal the injected delays within the stated quantum on loopback. The report passes its template checker; the scripts are committed with their sha256.
13. ADRs 0026 to 0028 and the amendments to 0023, 0024 and 0004 are Accepted, each rule citing a test. ADR 0029 is Proposed or Accepted. The index revision is bumped.
14. BACKLOG rows filed at deferral, closures carry commit ids and test names; HANDOFF is refreshed.

**P4-G2 (telephony)** adds:
- O5 answered;
- the P4-42 audit is GO;
- the allowlist refusal is tested before any provider request;
- one PSTN call to the owner's allowlisted number only, logged with counts;
- the robocall clause 10.2(5) authorisation is recorded.

---

## E. Risks, and questions for the owner (one at a time in chat; recommendation first)

- **O1 Sarvam terms for the simulator (F4).**
  - Options: (a) ask Sarvam for written permission under 10.5(a); (b) an explicit owner risk acceptance, recorded as the owner's reading, not as permission; (c) no Sarvam TTS/LLM, which needs another provider key.
  - Recommend (a), with (b) as the interim decision for loopback runs against the project's own sample agent.
  - Also record: the 10.2(8) synthetic labelling (built in), and 17.5 training use of the agent audio and prompts sent (both synthetic).
- **O2 Agent under test for R1 and R2.**
  - Options: (a) the project's `scripted_bot` (no model, no agent spend) for R1, and an `llm_bot` for R2; (b) an endpoint the owner names explicitly (never one of the owner's own Docker containers unless he names it); (c) none until the adapters phase.
  - Recommend (a).
- **O3 Stack of the `llm_bot`.**
  - Options: (a) Pipecat with our own small httpx-based Sarvam STT/TTS processors and Pipecat's OpenAI-compatible LLM service pointed at Sarvam chat v2 (OpenAI compatibility **not verified**); (b) `pipecat-ai[sarvam]` with `sarvamai` (no licence declared) in the tool environment only; (c) a scripted bot only.
  - Recommend (a). Also confirm that a local Silero VAD inside the sample agent (not inside Tracer) is acceptable, or require (c).
- **O4 Pre-registered values.**
  - Per-run caps: call seconds, TTS characters, LLM tokens, STT seconds.
  - The minimum interval between calls.
  - The number of repeats for P4-36.
  - The `wait_for_agent_silence_ms` and `max_silence_ms` used in R1.
  - Recommend the owner states each; Tracer supplies none (labelled example file only).
- **O5 Telephony (P4-G2).**
  - Which provider and account (or SIP trunk); the allowlisted numbers (the owner's own only); the caller id.
  - Whether a public tunnel for the media leg is acceptable (and which).
  - The owner's statement on Indian regulation for automated calls (**not verified** by us).
  - Recommend: the provider the owner already uses, plus a provider media stream, plus a one-time-token tunnel during the call only.
- **O6 Copyleft in the WebRTC extra.** If the bundled FFmpeg in `av` is LGPL: (a) accept it as a named exception for the optional extra; (b) drop WebRTC to BL. Recommend (a) (owner rule: do not dwell on copyleft). Decided by the lead 2026-10-27 under the owner rule on copyleft: named exception for the optional `[webrtc]` extra with conditions (see `docs/security/P4-02-licenses.md` and the decision log).
- **O7 Retention of simulated runs.** No human subject, so no retention marker; local stores stay `keep`. Recommend accept; ask only if the owner wants a deletion date.
- **O8 New provider keys.** None needed for P4A. Ask only if O1 is (c).
- **O9 Languages.** Base case mr only; hi and the other seven are BL-158 to BL-163. Recommend mr only, matching Phase 3's normaliser and STT map.

**Risks and how each is measured:**
- **Apparatus error** (pacing, buffering, clock): P4-27 calibration against known injected delays on loopback, reported as counts and ranges.
- **Transport bias:** values are always labelled per dialect; nothing is compared across dialects.
- **Noisy STT of the agent channel:** the agreement scorer's existing flags are not quality; 8 kHz acceptance is checked first.
- **LLM caller non-determinism:** scripted mode is the reproducible path; LLM runs are replayable but not repeatable.
- **Prompt injection:** P4-14 suite and audit P4-32.
- **Hostile agent DoS:** frame and message caps, the watchdog.
- **Spend:** required caps, usage streamed to recovery, dry run.
- **Supply chain:** Pipecat churn (isolated, the interop test as alarm); aiortc/av wheels on 3.15.
- **Scope creep watch:** judge, suites, more dialects, more languages, Pipecat Evals import, telephony beyond one provider. Each is a BL row.

---

## F. Decisions (the D-style records for SCOPE.md)

- **D1.** Q1 to Q22 auto-approved on the recommended option (owner rule of 2026-10-23), logged with the rejected options and trade-offs above.
- **D2.** Owner-only items O1 to O9, asked one at a time before the dependent tasks. Numeric values are pre-registered before any real result.
- **D3.** Bounded dependency ranges, each tested at its lower bound: `websockets`, `protobuf`, `aiortc` (extra). `pipecat-ai` is exact and tool-only. Test: `test_phase4_requires_dist_bounded_ranges`. Update 2026-10-27 (E-b): `websockets` and `aiortc` (extra) only; `protobuf` is dropped, and `test_phase4_requires_dist_bounded_ranges` lists those two.
- **D4.** The baseline for "evalschema unchanged" is the Phase 3 gate commit once recorded. P4-01 records the `schema.lock` sha256 and the source tree hash.
- **D5.** No schema change in Phase 4 (Q1). Gaps go to 0.0.2 rows (BL-015 re-targeted, plus new rows for a simulator timing source, an LLM caller turn source, Run status and end reason, and the F6 source-aware `agent_transcript`).

**Proposed new BACKLOG rows:**
- the LLM judge and calibration phase;
- RunVerdict for simulated runs;
- caller-role export lines against the script (agreement, never WER);
- the Pipecat Evals F5 importer;
- the Daily and LiveKit transports;
- other telephony serializers (Telnyx, Plivo, Exotel, Vonage);
- RTVI and T1 text from the protocol (Phase 5/6);
- suite and concurrency mode;
- cloned voices;
- other languages for simulation;
- TTS streaming;
- STUN/TURN for remote WebRTC;
- a human-present call mode;
- the Pipecat version watch;
- aiortc/av on Python 3.15;
- the plan's "WER against scripted turns" line, superseded by the owner rule.

## What I needed and could not get

- `git status`, `git log`, `gh run list` and the Phase 3 gate commit id.
- The Pipecat 1.14.0 source at the tag for:
  - `pipecat.transports.websocket.client` and `.rtvi_client`;
  - `pipecat.serializers.protobuf` and its frame schema;
  - the SmallWebRTC signalling route and request body;
  - whether the WebSocket output transports pace audio in real time.

  I read the `main` branch only, and only the evals files.
- The Twilio Media Streams documentation.
- Sarvam TTS and LLM pricing, the streaming TTS documentation, 8 kHz STT acceptance and per-model tool support.
- The licence of the FFmpeg bundled in the locked `av` wheel, and the licences of `sentencex` and `loudness`.
- Dograh and VoicEra production transports (the adapters phase).
- Indian rules for automated outbound calls.

## Files that matter for this scope

- `<repo>/packages/evalschema/src/tracer_evalschema/persona.py` (language and accent only)
- `<repo>/packages/evalschema/src/tracer_evalschema/run.py` (`RUN_MODES`, `CALLER_ORIGINS`, no status field)
- `<repo>/packages/evalschema/src/tracer_evalschema/evaluator.py` (`ScriptedTurn`, `MAX_DURATION_MS`)
- `<repo>/packages/evalschema/src/tracer_evalschema/agent.py` (ws, webrtc and sip endpoints)
- `<repo>/packages/evalschema/src/tracer_evalschema/artifact.py` (`ChannelMap`, declared)
- `<repo>/packages/evalschema/src/tracer_evalschema/privacy.py` (`derive_human_subject`, `real_person_likeness`)
- `<repo>/packages/evalschema/src/tracer_evalschema/usage.py` (`sim_caller_llm`, `sim_caller_tts`)
- `<repo>/packages/evalschema/src/tracer_evalschema/bundle.py` (lines 930-940: `agent_transcript` reads by role; F6)
- `<repo>/packages/evalschema/src/tracer_evalschema/snapshot.py` (required expected-outcome check; F7)
- `<repo>/packages/evalcore/src/tracer_evalcore/stt.py` (the boundary pattern to copy)
- `<repo>/docs/adr/0016-roles-caller-origin-privacy.md`
- `<repo>/docs/adr/0024-provider-adapters-byok-sarvam-rest.md`
- `<repo>/docs/phase1/api-surface-mappings-2.md` (F5 Pipecat Evals)
- `<repo>/docs/security/license-policy.toml`
- `<repo>/docs/BACKLOG.md` (BL-015, BL-029, BL-148, BL-158 to BL-163, BL-173, BL-193)
