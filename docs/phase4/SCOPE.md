# Phase 4 scope: the simulated caller (`tracer-evalsim`), mr base case

Status: Scoped 2026-10-27; batch 0 (docs) may start; code (P4-01) starts only after the Phase 3
gate is recorded as passed. Package: `tracer-evalsim` (new), with extensions to
`tracer-evalcore`, `tracer-evalproviders` and `tracer-cli`. Total: about 317 h; telephony 32 h moved to a later stage (Update 2026-10-27 (owner): batch P, P4-40 to P4-43 and P4-G2, is moved out of Phase 4 to a later stage; the earlier text was "about 349 h (P4A, batches 0 to
H: 317 h; P, telephony: 32 h)"). Update 2026-10-28 (owner decision 2026-10-28: judge in Phase 4 as an uncalibrated signal): batch J, the LLM judge (P4-J01 to P4-J32 and P4-JG, 181 h; section A.10 and section J), is added, so the Phase 4 total is about 498 h (317 + 181). No code of this file is built yet. The batch 0 ADRs of P4-00a to
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
| O5 | Telephony (provider, numbers, caller id, tunnel, regulation statement) | batch P (P4-40 to P4-G2) | not needed for Phase 4; re-targeted to the later telephony stage (owner, 2026-10-27; BL-311) |
| O6 | Copyleft in the WebRTC extra (`av`/FFmpeg) | not stated; applies if the bundled FFmpeg is LGPL | decided by the lead 2026-10-27 under the owner rule on copyleft: named exception for the optional `[webrtc]` extra with conditions (`docs/security/P4-02-licenses.md`, decision log) |
| O7 | Retention of simulated runs | not stated | open |
| O8 | New provider keys | asked only if O1 is (c) | open |
| O9 | Languages (mr only) | not stated | open |

- **BACKLOG.** The "Proposed new BACKLOG rows" of section F and the new 0.0.2 rows of D5 are
  filed as BL-269 to BL-287 (`docs/BACKLOG.md`, section 7); BL-015 is re-targeted to a schema
  0.0.2 phase (Q1, D5), and BL-193 carries the F6 extension.
- **Amendment 2026-10-28 (owner decision 2026-10-28: judge in Phase 4 as an uncalibrated signal;
  decision log 2026-10-28 "OWNER DECISION ... LLM JUDGE" and "PHASE 4 BATCH J (LLM JUDGE,
  uncalibrated signal) SCOPE DELIVERED").** The owner moved the LLM judge into Phase 4 ("build
  first, calibrate later"), provider-agnostic through the OpenAI chat-completions specification,
  every verdict labelled uncalibrated, judge-human agreement reported as data and never as
  accuracy, no pass or fail claim of quality. The staff-reviewer scoped it as batch J (read-only,
  2026-10-28): the task table is section A.10, everything else is section J (findings FJ1 to FJ10,
  design Q-J1 to Q-J20, plan, owner questions O-J1 to O-J6, risks, gate additions 15 to 24,
  BACKLOG effects). The text above and the non-goal "The LLM judge and RunVerdict" of section 0
  are kept as history; RunVerdict stays a non-goal (FJ5).
  - **Auto-approved** by the lead on the recommended options (owner rule of 2026-10-23): Q-J1 to
    Q-J19 and the findings FJ1 to FJ10, logged in the decision log with the rejected options;
    Q-J20 (no new package) is in the reviewer's report and follows Q3 (the decision log entry
    lists Q-J1 to Q-J19). The ADR that records them is ADR 0030 (Proposed), staged at
    `docs/phase4/staging/0030-llm-judge-uncalibrated-signal.md` until qa adds its pending set
    (P4-J01), with the proposed amendments of ADRs 0023 (A4), 0024 (A10 to A13) and 0027
    (rule 12a) for P4-J02.
  - **Q1 still holds: no schema change in Phase 4.** The judge fits schema 0.0.1
    (`MetricDefinition.kind=llm_judge`, `JudgeInfo`, `JUDGE_VERDICTS`, `UsageRecord.stage`
    `judge`), with one recorded limitation (FJ4): an `llm_judge` result that requires
    `transcript` is re-checked over every turn of the transcripts it names, so one non-mr agent
    STT turn makes every judge result of the run `not_applicable` `unsupported_language`;
    accepted for Phase 4, with no workaround; the 0.0.2 fix extends BL-193.
  - **Batch order.** J0 (P4-J01, P4-J03 and P4-J04 first) can start now, in parallel with the dev
    of batch E; JA and JB start after batch E is committed; batch F and batch J run in parallel
    after batch E (the reviewer confirmed batch F as essential: the only external check of the
    hand-written wire code, the calibration evidence of gate item 12, the T-DEG matrix of gate
    item 7 and the guards of gate items 2, 4 and 8); P4-J32 waits for P4-26.
  - **Unchanged by batch J:** telephony stays moved to a later stage (BL-311, section A.9);
    concurrency stays held at one caller (BL-276).
  - **Owner questions O-J1 to O-J6** (section J.6) are asked one at a time in chat before
    P4-J28 (the first real judge request), never before the offline work; values (O-J3) are
    pre-registered in the decision log. None is answered yet.

| Question | Subject | Needed before | Status |
|---|---|---|---|
| O-J1 | Judge provider, model and key | P4-J28 (RJ1); gate item 22 | open |
| O-J2 | Data sharing with the judge provider (and Sarvam terms 10.5(a) if O-J1 is Sarvam) | P4-J28 (RJ1); gate item 22 | open |
| O-J3 | Pre-registered values (judge caps, `max_tokens`, temperature or not sent, K, runs) | P4-J28 (RJ1); gate item 22 | open |
| O-J4 | Lift of the BL-177 hold for judge-verdict label files | P4-J28 (RJ1); gate item 22 | open |
| O-J5 | Labellers (who, how many, basis) | P4-J28 (RJ1); gate item 22 | open |
| O-J6 | The real golden set | before P4-J28 (section J.6); gate item 22 names O-J1 to O-J5 only | open |

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
- Update 2026-10-28 (owner decision): the judge is built in Phase 4 as an uncalibrated signal (batch J, section J); the expected-outcome check still stays inert in every run and no RunVerdict is produced (FJ5); calibration stays open (BL-269).

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
  - SIP/PSTN dial-out through the user's own telephony account, as a second gate (P4-G2) after the owner answers. Update 2026-10-27 (owner): moved to a later stage, not part of Phase 4 (BACKLOG BL-311; ADR 0029 stays Proposed).
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
- The LLM judge and RunVerdict. (Update 2026-10-28, owner decision: the LLM judge is in Phase 4 as an uncalibrated signal, batch J, section A.10 and section J; RunVerdict stays a non-goal, FJ5.)
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

No cut list (owner rule): if a task slips, the date moves. PSTN/SIP is sequenced behind owner accounts (P4-G2), not cut. Update 2026-10-27 (owner): PSTN/SIP (batch P) is moved to a later stage by the owner's decision: a recorded deferral with its own BACKLOG row (BL-311), not a cut.

**Package split (Q3):**

| Package | Holds | Dependencies |
|---|---|---|
| `tracer-evalcore` (extended) | `TtsEngine`, `CallerBrain`, `LlmEngine` protocols and error taxonomy; call state machine; turn-taking policy; agent-activity rule; recorder timeline; G.711 decode; timing extraction; prompt builder and output validator; timing definitions; simulated bundle builder | unchanged: evalschema and stdlib, no network import |
| `tracer-evalproviders` (extended) | Sarvam TTS REST, Sarvam chat v2 REST, per-channel STT mapping, TTS cache, telephony REST (P4-G2; moved to a later stage 2026-10-27, BL-311) | unchanged: httpx |
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
| P4-00e | SA | ADR 0029 "Telephony dial-out" (design only; options of Q20; allowlist, rate limits, authorisation record) | Status Proposed until the owner answers O5; then Accepted (P4-40). Update 2026-10-27 (owner): ADR 0029 stays Proposed as a provisional sketch; the later telephony stage's study revisits it, probably rewrites it (BL-311) | `test_adr0029_rules_are_tested` | 5 |
| P4-00f | IL | mr persona and voice validity table (Sarvam bulbul voices x `mr-IN`, recorded as observed facts, never as accent claims); synthetic mr scripted-turn fixtures (invented text, `needs_native_review: true`); the allowed character set for LLM caller lines in mr (Devanagari block, danda, ASCII digits and punctuation) | Fixture `packages/evalcore/tests/fixtures/sim_mr.json` staged | (fixtures) | 5 |
| P4-01 | dev | Scaffold `tracer-evalsim` (SPDX, `py.typed`, ruff and mypy, bounded ranges tested at the lower bound, `lowest-direct` leg for websockets, wheels without tests); extend import whitelists; `tools/samplebot/` as its own uv project (not a workspace member) | evalcore still imports no network module; evalsim is the only importer of `websockets` and `aiortc` | `test_evalsim_runtime_imports_whitelist`, `test_evalcore_imports_no_network_module` (existing, unchanged), `test_evalcore_imports_no_asyncio_network` , `test_phase4_requires_dist_bounded_ranges`, `test_evalsim_wheel_contains_no_tests`, `test_evalschema_unchanged_since_phase4_baseline`, `test_tracer_simulate_help_runs`, `test_samplebot_not_a_workspace_member_and_not_in_uv_lock` | 8 |
| P4-02 | SL | Licence review `docs/security/P4-02-licenses.md`: `websockets`, `protobuf`, the `aiortc` closure (read the licence files actually bundled in the locked `av` wheel); the `tools/samplebot` closure (`num2words` LGPL, `soundfile`/libsndfile, `onnxruntime`, `numba`, `sentencex`, `loudness`, and `soxr` 1.0.0 LGPL-2.1-or-later with libsoxr, added 2026-10-27 (D-d)), recorded as tool-only and never in a tracer wheel; `[runtime.evalsim]` and `[runtime.evalsim.extras_webrtc]` in `license-policy.toml`; `pipecat-ai` added to `[[rejected]]` for runtime with the reason | `check_licenses` ok; every licence read from files; any copyleft runtime item is a named, owner-rule exception (do not dwell on copyleft). Update 2026-10-27 (E-b): `protobuf` is not used (dropped; ADR 0026 rule 3); its review stays in `docs/security/P4-02-licenses.md` as history, and the `[runtime.evalsim]` closure and the overrides carry no `protobuf` entry | `test_license_policy_lists_phase4_closures`, `test_no_lgpl_or_gpl_in_lock` (existing, unchanged unless a named spec change is logged), `test_license_exceptions_exact` (existing; a named spec change if av/FFmpeg needs an exception), `test_samplebot_closure_reviewed_and_tool_only` | 8 |

### A.1 Pure core (batch A, evalcore; clock and randomness injected)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-03 | dev | Protocols and taxonomy: `TtsEngine` (text, voice, rate, codec in; PCM out plus a usage record), `LlmEngine` (messages in; text plus usage out), `CallerBrain` (scripted, llm, replay); provider-neutral errors `Tts*`, `Llm*` (facts only, raised outside handlers, no cause and no context, as finding F-B2); `UsageStreaming*` variants (the R1 lesson) | Same shape as the `SttEngine` boundary | `test_tts_llm_errors_hold_facts_only_and_no_chain`, `test_engines_stream_usage_before_next_request`, `test_brain_protocol_accepts_scripted_llm_replay` | 4 |
| P4-04 | dev | Call state machine: states `preflight`, `connecting`, `connected`, `caller_speaking`, `awaiting_agent`, `agent_speaking`, `ending`, `ended`; end reasons `script_complete`, `caller_end_call`, `agent_hangup`, `max_duration`, `max_turns`, `max_silence`, `kill_switch`, `setup_failed`, `transport_error`, `brain_invalid_output`, `spend_cap`, `brain_failed` (added 2026-10-28, BL-338); the smallest of `Evaluator.max_duration_ms` and the run cap wins | Pure, table-driven, every transition tested; no state is entered after `ended` | `test_state_machine_transition_table_exact`, `test_max_duration_is_min_of_evaluator_and_cap`, `test_kill_switch_reaches_ended_from_every_state`, `test_no_event_after_ended`, `test_setup_failure_is_an_end_reason_not_an_exception` | 10 |
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
| P4-14 | qa | Adversarial injection suite: a fake agent says "ignore previous instructions", asks for the system prompt or keys, dictates a phone number, sends bidi, markup or 10k-character lines; and a fake LLM that returns injections back | No outcome text, no key canary and no agent-dictated number ever reaches TTS, the call log, stdout or a dial action. Update 2026-10-27 (batch C reading A3, auto-approved): reworded to "no outcome text and no key canary ever reaches TTS, the call log or stdout, and no agent-dictated number reaches a dial action". The caller-line character set allows ASCII digits, so an LLM caller that repeats a number the agent dictated inside a valid Devanagari line speaks it: recorded, not prevented (ADR 0028 Consequences). The brain composes no line itself and the dialer is never touched by any text. The report-and-store clause is tested through the Phase 3 `score` path until a simulated bundle exists (BL-306) | `test_injection_from_agent_never_changes_dial_target_or_tools`, `test_injection_cannot_leak_expected_outcome_or_key_canary`, `test_adversarial_agent_text_never_breaks_report_or_store` | 6 |
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
| P4-29 | qa | T-DEG matrix for simulated runs: setup failure; agent silent; comfort-noise agent; no export; export present; STT failure; kill switch mid-call; max_duration; max_silence; spend cap hit. Identical record kinds and schema; `not_applicable` never failure or zero | Update 2026-10-27 (D-a): when the agent never speaks, each timing value that needs an agent edge is absent and its result is `error` with the code `timing_edge_missing` (ADR 0027, BL-292): never 0, never false, never a valid `not_applicable` (0.0.1 cannot store one for it). Update 2026-10-28 (BL-338, batch G): two more cases, a caller brain that fails at its first request and after one answered turn (`brain_failed`, exit 2); see docs/phase4/api-surface-batch-g.md section 8 | `test_tdeg_sim_setup_failed_all_not_applicable_exit_5`, `test_tdeg_sim_agent_silent_timing_not_measured` (renamed 2026-10-27 (D-a); was test_tdeg_sim_agent_silent_timing_not_applicable), `test_tdeg_sim_no_export_agreement_missing_input`, `test_tdeg_sim_export_present_agreement_scored`, `test_tdeg_sim_stt_failure_missing_input_transcript`, `test_tdeg_sim_kill_switch_keeps_partial_recording_and_usage`, `test_tdeg_sim_schema_identical_across_cases` | 6 |
| P4-30 | qa+SL | No-network guard extended to evalsim (integration marker loopback-only); canary keys across every output channel (STT, TTS, LLM, telephony: exception, repr, logs at DEBUG, call log, store, report, recovery file, TTS cache names); guard extended to staged `*.wav` under any out dir, `sim_call_log.jsonl`, `tts-cache/`, and allowlist files | Update 2026-10-27 (owner): the telephony key canary is deferred with the telephony stage (BL-311); the STT, TTS and LLM canaries stay in Phase 4 | `test_unit_tests_open_no_socket` (extended), `test_integration_tests_loopback_only`, `test_key_canaries_never_in_any_output_channel`, `test_guard_rejects_staged_sim_outputs_and_allowlist` | 5 |

### A.7 Safety and audit (batch G)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-31 | dev+SL | Target safety: the allowlist file (git-ignored, 0600, exact match after normalisation: ws or wss host:port/path, or an E.164 number) is checked before any key read; a target not on it is refused (exit 4); loopback targets still need an allowlist entry; one call per invocation; minimum interval between calls (lock file in `--out`); a kill-switch file checked every frame | Update 2026-10-27 (owner): the allowlist for WebSocket and WebRTC targets stays in Phase 4; the PSTN part (E.164 entries and any dial action) is deferred with the telephony stage (BL-311) | `test_target_not_on_allowlist_refused_before_any_key_or_socket`, `test_allowlist_mode_and_link_rules`, `test_one_call_per_invocation_and_min_interval`, `test_kill_switch_file_hangs_up_within_one_frame`; added 2026-10-27 (BL-298..301): `test_allowlist_entry_fields_unknown_or_repeated_refused` (ADR 0026 rule 16 entry fields); hours to be re-estimated by the reviewer. Update 2026-10-28 (batch G): the kill-switch file is `kill-switch` and the lock file `call.lock` in `--out`; the DNS check of a named target is made at step 2; see docs/phase4/api-surface-batch-g.md | 6 |
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

Update 2026-10-27 (owner; decision log "OWNER DECISION: move telephony to later stage"): batch P (P4-40, P4-41, P4-42, P4-43, P4-G2; 32 h) is moved out of Phase 4 to a later stage. The rows below are kept as history, each marked "moved to a later stage 2026-10-27"; ADR 0029 stays Proposed; O5 is re-targeted to that stage (BACKLOG BL-311).

The owner's reason (decision log "OWNER REASON for deferring telephony"): telephony is convoluted; there are several CPaaS providers, individual SIP servers and trunks, and telcos with their own idiosyncrasies, so it has to be clearly thought through before it is coded. The later stage therefore starts with a design and research study, not code: its first deliverables are a study document and an ADR that map the landscape and decide the architecture. The study writes these as questions with sources to verify, never as facts:
- which provider classes to compare: CPaaS APIs, the owner's own SIP server or trunk, a direct SIP user agent;
- per-telco behaviours to check: codecs, DTMF, caller-id rules, call setup and teardown quirks, the regulation of automated calls, recording and consent;
- how the media reaches Tracer;
- costs;
- a testing strategy that dials no real number.

ADR 0029 is a provisional sketch, not a decision: its recommended design (provider REST dial-out plus a media-stream WebSocket) must be revisited, probably rewritten, by the study. The rows P4-40 to P4-43 and P4-G2 below are placeholders whose scope the study will replace (their hours are kept as history). No telephony code, adapter or spend before the study's ADR is Accepted and the O5 answers exist.

| ID | Owner | Task | Acceptance | Tests | h |
|---|---|---|---|---|---|
| P4-40 | SA | ADR 0029 Accepted for the provider the owner names (moved to a later stage 2026-10-27; a placeholder whose scope the telephony study will replace) | | `test_adr0029_rules_are_tested` | 3 |
| P4-41 | dev | Telephony adapter: provider REST dial-out through httpx (fixed host, BYOK credentials, allowlisted `to` only, the owner's caller id), and the media leg through the matching media-stream dialect (P4-17 if Twilio-compatible). An exposed endpoint, if needed, only through an owner-approved tunnel with a one-time token. Or an external SIP user agent (Q20) (moved to a later stage 2026-10-27; a placeholder whose scope the telephony study will replace) | Fake provider tests; no real dial in tests | `test_dial_refuses_number_not_on_allowlist_before_any_request`, `test_dial_credentials_never_in_any_output`, `test_dial_hangup_on_every_end_reason`, `test_media_endpoint_requires_one_time_token` | 16 |
| P4-42 | SL | Telephony audit delta (robocall clause 10.2(5), exposure of the media endpoint, numbers in records) (moved to a later stage 2026-10-27; a placeholder whose scope the telephony study will replace; the Phase 4 audit P4-32 has no telephony item) | GO before P4-43 | `test_p4_telephony_audit_items_cite_tests` | 6 |
| P4-43 | lead | **OWNER-GATED.** First PSTN call to the owner's allowlisted number only; counts only (moved to a later stage 2026-10-27; a placeholder whose scope the telephony study will replace) | | none | 3 |
| P4-G2 | reviewer | Telephony gate (moved to a later stage 2026-10-27; a placeholder whose scope the telephony study will replace) | | none | 4 |

### A.10 LLM judge as an uncalibrated signal (batch J; added 2026-10-28, owner decision)

Added 2026-10-28 (owner decision 2026-10-28: the judge is built in Phase 4 as an uncalibrated signal; decision log 2026-10-28 "OWNER DECISION ... LLM JUDGE" and "PHASE 4 BATCH J (LLM JUDGE, uncalibrated signal) SCOPE DELIVERED"). Source: the staff-reviewer's batch J scope report (read-only, 2026-10-28), reproduced faithfully here and in section J; tasks, hours, acceptance criteria and test names are unchanged. Owner roles as above: SA schema-architect, dev, qa, IL indic-linguistics, SL security-licensing, lead, reviewer. qa writes every named test red first, and the hours include qa red-test time. Unit tests open no socket. Loopback tests carry the `loopback` marker. Real judge calls are owner-gated steps, never tests. Section J holds the findings, the design (Q-J1 to Q-J20), the plan, the owner questions O-J1 to O-J6, the risks and the gate additions 15 to 24.

#### J0: ADRs, dossier, fixtures (docs first)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-J01 | SA+SL | ADR 0030 (rules in section J.7) | Accepted; numbered rules with rejected options and trade-offs; each rule cites a test | `test_adr0030_rules_are_tested` | 10 |
| P4-J02 | SA | Amend ADR 0024 (A10 to A13: the generic chat client, profiles, judge allowlist and key binding, judge usage), ADR 0023 (A4: the judge claim deny-list and its fixed sentence) and ADR 0027 (rule 12a: the snapshot may embed judge definitions; the check stays inert); bump the index revision | Amendments logged; existing rule counts unchanged | `test_adr0024_rules_are_tested` (extended), `test_adr0023_rules_are_tested` (extended), `test_adr0027_rules_are_tested` (extended) | 4 |
| P4-J03 | SL | Provider dossier `docs/security/P4-J-providers.md`. One row per profile fact, each with URL and retrieval date. The terms and data-use page of each provider is recorded as an owner gate, never as an assumption. No new runtime dependency (httpx is reused) | Every profile fact cited, or marked "not verified"; profile status `verified` or `unverified` | `test_judge_provider_dossier_cites_url_and_date_per_fact`, `test_judge_profile_status_matches_dossier` | 6 |
| P4-J04 | IL | Synthetic mr golden fixture `packages/evalcore/tests/fixtures/golden_mr.json`: 10 items bound to the `sim_mr` scripted turns, plus planted agent replies per item. Plants are: exact facts, paraphrase, number words against digits, one fact missing, one contradicted, off-topic, empty. Every item has `needs_native_review: true`; reason-code wording reviewed; labeller instructions in mr and en | Fixture validates; plants listed with intended labels | `test_golden_fixture_mr_valid_and_marked_for_native_review` | 5 |
| P4-J05 | SA+qa | JSON Schemas (draft 2020-12, closed keys) for `tracer.golden_set/1`, `tracer.judge_profile/1` (snapshot metadata), `tracer.judge/1` (result metadata), `tracer.judge_exchange/1` (local file) and `tracer.judge_labels/1` | Machine-checked against shipped constants | `test_adr0030_documents_match_json_schemas` | 4 |

#### JA: pure core (tracer-evalcore; no network import)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-J06 | dev | Golden-set loader. Strict JSON (`loads_strict`; duplicate keys refused), version, caps (at most 100 items, 10 facts per item, 300 characters per fact, 1,000 per expected answer), NFC, IL character table. Each item's `question` must equal `Evaluator.scripted_turns[scripted_turn_index].text` byte for byte. Refused with `--caller` llm, and on a turn with a scheduled barge-in | Refusals happen at step 1 (exit 4 or 64), before any key | `test_golden_set_strict_json_closed_keys_and_version`, `test_golden_item_question_must_equal_scripted_turn_text`, `test_golden_set_refused_with_llm_caller`, `test_golden_set_caps_items_facts_chars`, `test_golden_set_on_barge_in_turn_refused` | 5 |
| P4-J07 | dev | Answer window. The window opens at the end edge of the caller turn holding the item's line (ADR 0027 rule 10 edge) and closes at the start of the next sent caller turn, or the call end. It holds the `our_stt` agent-channel turns whose `start_ms` falls inside, in `seq` order, joined by one U+000A. Turns starting before the edge are excluded and counted. Never the export, never the script | Pure; recorded frame logs give exact windows | `test_answer_window_from_end_of_question_to_next_caller_line`, `test_answer_window_excludes_turns_starting_before_question_end`, `test_answer_window_last_question_ends_at_call_end`, `test_answer_window_reads_our_stt_agent_turns_never_export_or_script` | 6 |
| P4-J08 | dev+SL | Prompt builder. A fixed system template per match mode. The user message is one JSON document `{question, expected_facts or expected_answer, agent_answer}` (JSON-escaped), inside fixed fences, with `agent_answer` labelled untrusted data. Fence sequences in any text are neutralised (cleaning as in ADR 0028 rule 4). Nothing else enters (section J.3, Q-J3) | Request bodies built offline and inspected | `test_judge_request_holds_only_question_expected_and_answer`, `test_judge_request_never_holds_key_instructions_profile_or_other_items`, `test_agent_answer_is_json_encoded_inside_fenced_untrusted_block`, `test_delimiter_sequences_in_agent_text_are_neutralised`, `test_judge_prompt_template_sha_recorded_per_match_mode` | 6 |
| P4-J09 | dev | Output validator. Strict JSON, closed keys `{verdict, reason_code, facts:[{index, status}]}`, values from closed sets. Cross-field rules: `agrees` iff every fact is `stated`; `disagrees` iff any fact is `contradicted` or `absent`; `cannot_tell` iff any fact is `cannot_tell` and none is contradicted. At most 4,096 bytes. Never repaired (a code fence around the JSON is invalid); one regeneration, then `error` `judge_output_invalid` | Table-driven | `test_judge_output_closed_keys_and_values_only`, `test_judge_output_cross_field_consistency_enforced`, `test_judge_output_over_size_cap_refused`, `test_judge_output_never_repaired_one_regeneration_then_error`, `test_judge_reason_code_from_closed_set_only` | 5 |
| P4-J10 | dev+SA | Records. One `llm_judge` definition per item, key `judge_golden_<item_id>`, with: `value_type=enum` and options `[agrees, disagrees]`; no pass rule; `per_conversation`; `requires=["transcript","turn_timing"]`; `languages=["mr"]`; `script_sensitive=false`; the fixed template per mode; `judge_model_ref={profile-label, model}`. Bindings carry the item's text as parameters with no default; they are optional rubric items, and the inert check stays the only required item. Results: `cannot_tell` gives `error` `judge_undetermined`. `JudgeInfo` has `prompt_sha256` (JCS of the messages sent), `params` as sent, and `reported_model` verbatim; no `confidence`, no `p_yes`, empty `per_turn`. `metadata["tracer.judge"]` holds the closed document. EvidenceRef: kind `turn` per window turn; `transcript_ids` names `our_stt` | `check_run_bundle` passes | `test_judge_definitions_are_llm_judge_enum_without_pass_rule`, `test_cannot_tell_is_error_judge_undetermined_never_a_value`, `test_judge_result_carries_judgeinfo_without_confidence_or_p_yes`, `test_judged_bundle_passes_check_run_bundle`, `test_judge_result_evidence_refs_name_answer_window_turns`, `test_expected_outcome_check_stays_inert_with_judge` | 8 |
| P4-J11 | dev | `JudgeEngine` protocol and `Judge*` errors (facts only, raised outside handlers, no cause or context; as `_engine_errors`); usage streamed before the next request | Same shape as `LlmEngine` | `test_judge_errors_hold_facts_only_and_no_chain`, `test_judge_engine_streams_usage_before_next_request` | 3 |

#### JB: providers (tracer-evalproviders; httpx MockTransport only)

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-J12 | dev | Generic chat-completions client. Generalise `JsonPoster` (auth scheme: bearer or a named header; URL from a validated allowlist entry; stage `judge`). Request subset per section J.3 (Q-J1). Response parse: `id`, `model`, `choices` exactly one, `message.content` a string, `finish_reason`, `usage.prompt_tokens`/`completion_tokens`, optional cached and reasoning tokens. Retries and taxonomy as ADR 0024 rules 9 and 10. An OpenRouter choice-level `error` is a typed failure | The existing `SarvamLlm` is unchanged | `test_openai_compat_request_body_subset_exact`, `test_openai_compat_response_parse_rejects_wrong_shape_and_oversize`, `test_openai_compat_usage_maps_prompt_completion_cached_exactly`, `test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost`, `test_openai_compat_retry_taxonomy_matches_adr0024`, `test_openai_compat_choice_level_error_is_typed_failure` | 10 |
| P4-J13 | dev+SL | Provider profiles (closed set; section J.3, Q-J2). Each profile fixes what is sent, omitted or required. An unverified profile is refused for real runs until its first owner-gated request is logged | Profiles cite the dossier | `test_profile_anthropic_compat_never_sends_response_format_or_seed`, `test_profile_temperature_not_sent_unless_configured`, `test_profile_openrouter_sends_pinned_provider_without_fallback_and_data_collection_deny`, `test_profile_reported_model_recorded_verbatim`, `test_profile_sarvam_v2_uses_subscription_key_header`, `test_unknown_profile_refused`, `test_profile_never_sends_unlisted_field`, `test_unverified_profile_refused_for_real_run` | 6 |
| P4-J14 | dev+SL | Pinned hosts (section J.3, Q-J4). Owner allowlist file (git-ignored, 0600, no links). Each entry: exact `https://host[:port]/path`, `profile=`, `key_env=`. https only, except literal loopback with `http`. No userinfo, query or fragment. `follow_redirects=False`, `trust_env=False`, TLS verified. A DNS answer that is private, loopback or link-local is refused for a public entry. The key is read by name only after the entry matches and is bound to that host | Refusal before any key read | `test_judge_host_not_on_allowlist_refused_before_any_key_read`, `test_judge_key_bound_to_its_host_never_sent_elsewhere`, `test_judge_https_only_except_loopback_literal`, `test_judge_client_never_follows_redirects_or_reads_proxy_env`, `test_judge_dns_answer_private_refused_for_public_entry`, `test_judge_allowlist_mode_and_link_rules`, `test_judge_key_env_name_pattern_and_never_value_in_config` | 6 |
| P4-J15 | dev | Exchange files `<out>/judge/<run>/item-<id>-<attempt>.json` (`tracer.judge_exchange/1`: request messages, raw response body, request id, status). 0600, `O_NOFOLLOW`, git-ignored and guarded. `replay:<dir>` engine; `fake:<scenario>` deterministic engine | Replay reproduces the stored results | `test_judge_exchange_files_private_no_follow_and_guarded`, `test_replay_judge_reproduces_stored_verdicts_byte_identical`, `test_fake_judge_is_deterministic_and_sends_nothing` | 5 |
| P4-J16 | qa+dev | Loopback fake OpenAI-compatible server (`tests/support`, stdlib `http.server`, profile behaviours scripted, injection replies) driving the real httpx transport | Loopback marker only | `test_loopback_fake_openai_server_round_trip`, `test_loopback_fake_server_injection_replies_are_refused` | 5 |

#### JC: orchestration, CLI, report, store, labels

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-J17 | dev | `tracer simulate` with `--golden`, `--judge-config`, `--judge-allowlist`, `--judge <profile>\|fake:<s>\|replay:<dir>`, `--max-judge-requests N`, `--max-judge-tokens N`, `--judge-authorisation-reference TEXT`. Order: section J.3 (Q-J6). The judge runs after scoring and before the store. Errors give exit 2 (existing `exit_code_of`, `simsummary.py:62`). Dry run prints the judge plan | Caps required for a real engine, with no defaults | `test_simulate_with_golden_order_of_operations_pinned`, `test_judge_refusals_before_any_key_read_or_connect`, `test_judge_caps_required_for_real_engine_no_defaults`, `test_judge_usage_survives_ctrl_c_mid_judging`, `test_simulate_dry_run_prints_judge_plan_and_sends_nothing`, `test_simulate_judge_errors_exit_2` | 8 |
| P4-J18 | dev | `tracer judge --out <dir> --bundle <id> --judge <engine> [--repeats K]`: one rescoring bundle per repeat (ADR 0022 rule 5), sharing the call's records. Refused when the snapshot has no judge definitions, or when the engine's model differs from `judge_model_ref` | Stability measurement on fixed answers | `test_tracer_judge_rescoring_bundle_shares_records_and_adds_judge_results`, `test_tracer_judge_refuses_run_without_judge_definitions`, `test_tracer_judge_model_must_equal_definition_model_ref`, `test_tracer_judge_repeats_store_one_bundle_each` | 7 |
| P4-J19 | dev | CLI and report. A judge section titled "uncalibrated judge signal"; per item: verdict, reason code, fact statuses and "n of N" counts; "outcome not evaluated" kept; escaped text only; no claim words | | `test_judge_lines_labelled_uncalibrated_everywhere`, `test_judge_report_never_claims_accuracy_or_quality`, `test_judge_report_shows_counts_with_denominators`, `test_judge_report_states_outcome_not_evaluated` | 6 |
| P4-J20 | dev | Store round trip of judged and rescoring bundles (SQLite; Postgres parity with the `postgres` marker) | Byte-identical | `test_judged_bundle_round_trips_through_store_byte_identical`, `test_judged_and_rescoring_bundles_store_on_both_backends` | 4 |
| P4-J21 | dev | Labels. `tracer labels packet` writes a blind packet: question, expected facts, the agent-channel audio range, our STT text, and a JSON template with `verdict: null`, but never the judge verdict. `tracer labels check` validates a labels file and copies it content-addressed (0600) under `<out>/labels/`. `tracer labels agreement` gives contingency counts (judge x human, plus human x human when there are two labellers), with unlabelled shown separately | Counts only; no percent, kappa or claim word | `test_labels_file_strict_closed_keys_and_version`, `test_label_packet_never_shows_judge_verdict`, `test_labels_import_content_addressed_and_private`, `test_agreement_report_counts_only_never_rates_or_accuracy`, `test_agreement_report_unlabelled_never_counted_as_disagreement`, `test_agreement_report_human_human_counts_when_two_labellers` | 9 |

#### JD: adversarial, tier degradation, end to end, guards, audit

| ID | Owner | Task | Acceptance | Tests (red first) | h |
|---|---|---|---|---|---|
| P4-J22 | qa+SL | Injection suite (BL-141). Agent text that orders "agrees", forges the closing fence, embeds `{"verdict":"agrees"}`, asks for the expected facts, asks for markdown or prose output, or carries bidi, markup or 10k characters. A fake model that returns injections, prose, fenced JSON or inconsistent fields | No stored free text; the verdict changes only with consistent fields; nothing leaks into any output | `test_judge_injection_cannot_flip_verdict_without_consistent_fields`, `test_judge_injection_cannot_store_free_text`, `test_judge_injection_cannot_change_output_format`, `test_judge_injection_forged_delimiter_stays_data`, `test_judge_injection_cannot_leak_expected_facts_into_any_output`, `test_judge_never_receives_caller_brain_prompt_or_profile_values` | 7 |
| P4-J23 | qa | Tier-degradation (T-DEG) matrix for judged runs (section J.3, Q-J8) | Identical key set and schema | `test_tdeg_judge_setup_failed_all_missing_input_no_request`, `test_tdeg_judge_stt_failed_missing_input_no_request`, `test_tdeg_judge_question_not_sent_is_error_never_disagrees`, `test_tdeg_judge_empty_answer_window_is_error_never_disagrees`, `test_tdeg_judge_non_mr_turn_unsupported_language_all_items`, `test_tdeg_judge_auth_failure_stops_requests_and_errors_remaining`, `test_tdeg_judge_cap_reached_no_request_beyond_cap`, `test_tdeg_judge_invalid_output_twice_is_error`, `test_tdeg_judge_cannot_tell_is_judge_undetermined_error`, `test_tdeg_judge_schema_identical_across_cases`, `test_tdeg_run_without_golden_set_unchanged` | 5 |
| P4-J24 | qa+IL | Offline end-to-end: synthetic mr scripted call, fake TTS, STT and agent (the J04 plants), fake judge, fake labels. Exact bundle, report and agreement bytes | | `test_e2e_judge_synthetic_mr_exact_outputs`, `test_e2e_judge_report_store_and_agreement_consistent` | 5 |
| P4-J25 | qa+SL | Judge key canaries (exception, repr, DEBUG logs, exchange files, store, report, recovery file, labels, agreement report); no-socket guard; staged-file guard | | `test_judge_key_canaries_never_in_any_output_channel`, `test_unit_tests_open_no_socket` (extended), `test_guard_rejects_staged_judge_exchanges_labels_and_allowlist` | 4 |
| P4-J26 | SL | Audit `docs/security/P4-J-audit.md`: SSRF and key exfiltration, injection, exchange files, terms and data sharing, spend, DoS by huge replies. GO before P4-J28 | No open P0 or P1 | `test_p4j_audit_items_cite_tests` | 6 |

#### JH: owner gates, real runs, measurement, docs

| ID | Owner | Task | Acceptance | Tests | h |
|---|---|---|---|---|---|
| P4-J27 | lead | **OWNER-GATED.** O-J1 to O-J6, one at a time; values pre-registered in the decision log | Logged | none | 1 |
| P4-J28 | lead | **OWNER-GATED.** RJ1: dry run, then the real judge on a stored offline M1 run (synthetic plants; no Sarvam spend; does not wait for O1), then on R1/R2 bundles once they exist; counts only | Logged before and after | none | 3 |
| P4-J29 | lead+qa | **OWNER-GATED** (K, the number of runs). Report `docs/phase4/measurements/<date>-judge-apparatus.md`: judge against planted labels ("fake humans", apparatus only), replay identity, K-repeat stability counts, invalid-output and regeneration counts, usage; human agreement only if the owner's labels exist, and then counts only | Template checker green | `test_p4j_measurement_report_follows_template` | 6 |
| P4-J30 | SA+dev | Docs: `docs/concepts/llm-judge.md` (a "Not claimed" fenced sentence), labeller guide, CLI README offline quick start with `--judge fake:`, CHANGELOGs | | `test_judge_readme_quick_start_runs_offline`, `test_judge_concept_page_states_no_claims` | 6 |
| P4-J31 | lead | BACKLOG rows at deferral; HANDOFF refresh | | none | 2 |
| P4-J32 | dev+IL | `tools/samplebot` golden scenario: pre-recorded replies for the J04 plants (audio made once, owner-gated TTS, or synthetic). Batch F dependency | | `test_samplebot_golden_scenario_uses_no_model` | 4 |
| P4-JG | reviewer | Gate additions inside P4-G (section J.9, items 15 to 24) | | none | 4 |

**Batch J totals:**

| Batch | Hours |
|---|---|
| J0 | 29 |
| JA | 33 |
| JB | 32 |
| JC | 34 |
| JD | 27 |
| JH including J32 and JG | 26 |
| **Batch J** | **181** |

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

Update 2026-10-27 (owner): the Phase 4 total is now about 317 h (P4A = batches 0 to H); the 32 h of batch P are carried by the later telephony stage (BL-311). The 349 h line above is kept as history.

Update 2026-10-28 (owner decision 2026-10-28: judge in Phase 4 as an uncalibrated signal): batch J (section A.10) adds 181 h (J0 29, JA 33, JB 32, JC 34, JD 27, JH including P4-J32 and P4-JG 26), so the Phase 4 total is about 317 + 181 = **498 h**. At 80 to 130 task-hours a day, batch J is about 1.4 to 2.3 working days, excluding the owner's answer time. The 317 h figure above is kept as history.

At the observed pace of 80 to 130 task-hours per day, P4A is about 2.5 to 4 working days, and P4-G2 about 0.5 day more (update 2026-10-27: P4-G2 is no longer part of Phase 4, so Phase 4 is P4A alone). The owner's answer times are not counted. For comparison: Phase 2 was 273 h and Phase 3 204 h.

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
- **P:** P4-40 to G2, only after O5 is answered. It runs in parallel with H once P4-32 is GO. Update 2026-10-27 (owner): batch P is moved out of Phase 4 to a later stage that starts with a design and research study (section A.9, BL-311); Phase 4 is batches 0 to H.
- **J** (added 2026-10-28, owner decision; section A.10 and section J.4): J0 -> JA and JB in parallel -> JC -> JD -> JH. P4-J01, P4-J03 and P4-J04 can start now, in parallel with the dev of batch E; JA and JB start after batch E is committed; batch F and batch J run in parallel after batch E. P4-J32 waits for P4-26. RJ1 (P4-J28) does not wait for O1 or R1: it uses the offline M1 bundle. Phase 4 is batches 0 to H plus J.

**Critical path (about 125 h):** P4-00a -> P4-01 -> P4-04 -> P4-05 -> P4-06 -> P4-19 -> P4-16 -> P4-21 -> P4-23 -> P4-28 -> P4-30 -> P4-32 -> P4-33 -> P4-34 -> P4-36 -> P4-G.

**Parallel work:**
- B and C run beside A.
- P4-17 and P4-18 run beside P4-16.
- P4-26 starts early.
- P4-24, P4-25 and P4-37 run after P4-21.

**Internal dependencies:**
- ADRs come before code. P4-02 must pass before any new dependency lands.
- P4-32 GO comes before any real call. O1 and O2 are answered before P4-34. O5 is answered before batch P. (Update 2026-10-27 (owner): batch P and O5 belong to the later telephony stage, BL-311.)
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
| Telephony provider, SIP trunk, numbers, India regulation for automated calls | owner-provided; regulation **not verified** | P4-G2 blocked until O5 (update 2026-10-27: both moved to the later telephony stage, BL-311) |
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
  - Update 2026-10-27 (owner): this recommendation is a provisional sketch, not a decision. The later telephony stage starts with a design and research study that compares the provider classes and decides the architecture (section A.9, BL-311).
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
4. No socket in unit tests. Integration tests are loopback only. Key canaries pass for STT, TTS, LLM and telephony across every output channel. Gitleaks and the guard are clean. (Update 2026-10-27 (owner): the telephony canary is deferred with the telephony stage; STT, TTS and LLM remain.)
5. The no-claim tests are green for the CLI, the report and the concept page. No `wer`/`cer` unit anywhere. Every timing value carries its quantum and its dialect.
6. Every cap and parameter without a default is refused when absent. The owner's values were pre-registered before R1.
7. The T-DEG matrix for simulated runs is green: identical schema, `not_applicable` never a failure or zero, exit 5 on a failed setup.
8. `check_run_bundle` passes for every produced bundle. The store round trip is byte-identical on SQLite, plus the Postgres parity test.
9. Licence review P4-02 is ok. `pipecat-ai` is absent from every tracer wheel and from `uv.lock`. Any copyleft runtime item is a named, recorded exception.
10. Security audit P4-32 has no open P0 or P1, and was finished before P4-34.
11. Owner gates O1 to O4 are answered and logged before R1. R1 and R2 are logged before and after with counts only. No call text, no prompt or reply text, and no number in any committed file.
12. Apparatus calibration is in the measurement report: measured gaps equal the injected delays within the stated quantum on loopback. The report passes its template checker; the scripts are committed with their sha256.
13. ADRs 0026 to 0028 and the amendments to 0023, 0024 and 0004 are Accepted, each rule citing a test. ADR 0029 is Proposed or Accepted. The index revision is bumped. (Update 2026-10-27 (owner): ADR 0029 stays Proposed, a provisional sketch deferred with the telephony stage. Consequence for the opt-in gate test `test_phase4_adr_pending_sets_are_empty_at_the_gate`: it would require `PENDING['0029']` to be empty, but ADR 0029's pending names belong to the deferred tasks; at gate time qa makes a named spec change (before and after, disclosed) so that `PENDING['0029']` may stay non-empty while ADR 0029 is Proposed and its stage is deferred; every other set must be empty.)
14. BACKLOG rows filed at deferral, closures carry commit ids and test names; HANDOFF is refreshed.

Update 2026-10-28 (owner decision 2026-10-28: judge in Phase 4 as an uncalibrated signal): P4-G also requires the batch J gate additions 15 to 24 of section J.9 (task P4-JG).

Update 2026-10-27 (owner): P4-G no longer includes P4-G2. The P4-G2 items below are deferred with the telephony stage (BL-311), as are the gate references to telephony in items 4 and 13 above.

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
  - Update 2026-10-27 (batch A reading 1, lead decision; ADR 0026 rule 10): `max_silence_ms` is the wait budget. An agent reply that starts later than `max_silence_ms` after the simulator began to wait, or a pause-free reply (a monologue) still running at that point, ends the call with `max_silence`; the owner weighs this when choosing the value.
- **O5 Telephony (P4-G2).** Update 2026-10-27 (owner): not needed for Phase 4; re-targeted to the later telephony stage, after its design and research study (BL-311). The items below are kept as history.
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

---

## J. Batch J: the LLM judge as an uncalibrated signal (added 2026-10-28)

Source: the staff-reviewer's batch J scope report (read-only, 2026-10-28), logged in the decision log entries 2026-10-28 "OWNER DECISION ... LLM JUDGE" and "PHASE 4 BATCH J (LLM JUDGE, uncalibrated signal) SCOPE DELIVERED", reproduced faithfully. The only edits are formatting: the task table is section A.10 (so that the Phase 4 ADR readers see its test names); the report's sections 1 to 10 are J.2 to J.10 here, and its cross-references "section 3.x", "section 7", "section 8" and "section 9" read "section J.3", "section J.7", "section J.8" and "section J.9"; repository-relative paths instead of absolute ones; ASCII arrows (`->`) instead of arrow characters; the BACKLOG ids filed for its rows are added in section J.10. Every "not verified" marker is unchanged. The reviewer's own notes keep the reviewer's first-person wording ("I"). Anything marked "not verified" was not read in a primary source on 2026-10-28.

### J.0 The reviewer's answer on batch F (39 h)

Batch F is essential. P4-G cannot pass without it, and it should not be cut or deferred. Reasons:
1. It is the only external check of the hand-written wire code. Batches A to E test our dialects against fake agents (P4-19) that we wrote from our own reading of the docs; if we misread something, the code and its tests agree with each other and are both wrong. P4-26 and P4-27 are the first runs against real Pipecat 1.14.0. They cover BL-298 (the hand-written protobuf codec), BL-299 (the Twilio close and mark behaviour), the BL-291 leftovers (the audio sample format and the frame size) and BL-310 (WebRTC media: it is proven only by P4-27, so until then nothing may claim WebRTC media works).
2. The P4-27 calibration is the evidence behind every timing number. Gate item 12 (measured gap equals the injected delay within one quantum) is the only thing that makes those numbers more than readings of our own clock.
3. P4-29 is the tier-degradation matrix, the project's core rule ("never break if absent"). Gate item 7 needs it.
4. P4-28 pins exact offline outputs, and P4-30 extends the key canaries and the no-socket guard. Gate items 2, 4 and 8 need them.

Batch J does not depend on F, except the sample-bot golden scenario (P4-J32). So F and J can run in parallel once batch E has landed.

### J.1 Findings that change the brief (read first)

**FJ1. Anthropic's OpenAI-compatible layer ignores structured output.** Verified on 2026-10-28 at platform.claude.com/docs/en/api/openai-sdk (redirected from docs.claude.com):
- `response_format` is "Ignored". The `strict` flag on tools is ignored too: "the tool use JSON is not guaranteed to follow the supplied schema".
- `seed`, `logprobs` and `reasoning_effort` are ignored.
- `temperature`: "On Claude 4.7 and later models ... any value below 1 returns a 400 error, so omit it". The same applies to `top_p`, and setting both returns a 400.
- `n` must be 1. `usage.prompt_tokens_details` is "Always empty", so cached tokens are never reported.
- Forced `tool_choice` returns an error on Claude Opus/Sonnet 5.5.
- The page says the layer "is primarily intended to test and compare model capabilities, and is not considered a long-term or production-ready solution".
- Non-workspace keys need an `anthropic-workspace-id` header.

Consequences: no design may rely on provider-side schema enforcement (Tracer's own validator decides); temperature and seed must be "not sent" options per provider profile.

**FJ2. "OpenAI-compatible" is not one contract.** Each provider differs in which fields it honours or silently ignores, in the model id format, auth headers, error shapes and usage fields. The judge needs named, versioned provider profiles (the pattern of ADR 0026's wire dialects), not one generic client with a free base URL.

**FJ3. OpenRouter's defaults are unsafe for an evaluation tool.** Verified on 2026-10-28 at openrouter.ai/docs (api-reference/overview, features/provider-routing, features/structured-outputs):
- `model` is optional; "If omitted, the user's or payer's default is used". Tracer must always send it.
- `allow_fallbacks` defaults to true and load balancing is on by default.
- `data_collection` defaults to `allow` ("providers which store user data non-transiently and may train on it").
- The response `model` may differ from the request.
- `usage.cost` is "Cost in credits", not money.

Recommendation: the openrouter profile always sends `provider: {order: [<one slug>], allow_fallbacks: false, require_parameters: true, data_collection: "deny"}`, and records the returned model verbatim (`JudgeInfo.reported_model` exists in 0.0.1).

**FJ4. The schema can hold the judge without a change, with one real 0.0.1 limitation.**
- What exists: `MetricDefinition.kind=llm_judge`, which requires `prompt_template`, `prompt_variables` and `judge_model_ref`; `ModelRef{provider, model}` as MachineKeys (they allow `/`, `.` and `:`, so `anthropic/claude-...` and `gemini-...` fit); `MetricResult.judge: JudgeInfo{model_ref, prompt_sha256, params, per_turn[], confidence?, reported_model?}`; `JUDGE_VERDICTS = ("yes","no","undetermined")` and `error.code=judge_undetermined`; `UsageRecord.stage` already includes `judge` (`usage.py:63`); `RunVerdict` (ADR 0013).
- The limitation: an llm_judge result whose `requires` names `transcript` is re-checked by `bundle._turns_read` over every turn of the transcripts the result names (`bundle.py:929-940`), not over the answer turns of one item. In a simulated run, any non-mr agent STT turn anywhere makes every judge result `not_applicable unsupported_language` (ADR 0023 A3).
- Fix: a 0.0.2 row (a per-result turn subset; extends BL-193). Accept it for Phase 4; there is no workaround.

**FJ5. Do not make the inert expected-outcome check real** (the brief listed this as an option; I recommend against).
- `snapshot._is_expected_outcome_check` requires `llm_judge`, `boolean`, `per_conversation`, `equals_true` and a required rubric item (`snapshot.py:131-141, 278-300`).
- A real check therefore produces `passed` true/false and feeds a RunVerdict pass/fail of the agent from an uncalibrated model. That breaks the owner rule of "no pass/fail claim of quality".
- Recommendation: every run keeps the inert placeholder (`simulated_call_no_outcome_check`, `{"provider":"none","model":"not_run"}`, `simscore.py:189`), judged runs included. Golden-item judgements are separate optional rubric items with no pass rule. BL-270 stays open.

**FJ6. A judge cannot be added to an already stored run unless its definitions were in the run's snapshot from the start.**
- `check_run_bundle` requires every result's definition to be embedded in `bundle.snapshot` (`bundle.py:773-790`), and a run has one snapshot.
- A later "rescoring bundle" (ADR 0022 rule 5) can add judge results only if the run was simulated with the golden set and judge configuration.
- So the judge model is fixed at `tracer simulate` time. Re-judging with a different model needs a new run. This shapes the CLI (Q-J15 and Q-J16).

**FJ7. Judge stability must be measured on fixed answers.** Repeating calls varies the agent too. So repeats re-judge one stored run K times (the `tracer judge` rescoring command), not K new calls.

**FJ8. Using Sarvam as the judge hits terms 10.5(a) head-on.** The clause forbids using Sarvam output to "test ... any artificial intelligence system" (fetched 2026-10-27; BL-188). Judging an agent's answers is exactly that. It is also a self-preference risk if the agent's own stack uses Sarvam. The Sarvam profile can exist, but a real Sarvam judge run needs a new owner gate (O-J2).

**FJ9. The Sarvam chat docs disagree with each other.**
- The v1 page (docs.sarvam.ai/api-reference-docs/chat/chat-completions) shows `POST /v1/chat/completions`, models `sarvam-105b` and `sarvam-105b-conversations`, and `seed` as "Beta. Determinism is not guaranteed".
- The v2 page (docs.sarvam.ai/api-reference/chat/chat-completions-v2.md) shows `POST /v2/chat/completions` and four models.
- Both v2 auth headers are listed as "required"; the repository sends only `api-subscription-key` (`_post.py:33`, marked UNVERIFIED).
- v2 `response_format` `json_schema` "guarantees the model generates output matching the supplied JSON Schema". `reasoning_effort` defaults to `max` on glm5.3 and deepseekv4.1-flash and counts against `max_tokens`. 413 is returned above 10,485,760 bytes; 502 `model_call_failed`; 503 `model_overloaded`.

**FJ10. Golden items need a scripted caller in Phase 4.** With the LLM caller the question text is generated, so there is no fixed question to compare against. The answer text comes from our STT of the agent channel. Using the agent export as the answer source needs a shared time base (BL-167) or turn ids, so it goes to the backlog.

### J.2 Objective, non-goals, done

**Objective.** `tracer simulate --golden <file> --judge-config <file> --judge <engine>` runs a scripted Marathi call, maps each golden item to the agent's answer window on our STT of the agent channel, and asks one judge model per item, through a pinned OpenAI chat-completions provider profile, whether the answer agrees with the expected facts.
- Verdicts are `agrees`, `disagrees` or `cannot_tell`, with a reason code from a closed set and per-fact statuses.
- Each is stored as an uncalibrated `llm_judge` result: no pass rule, no confidence, no RunVerdict.
- `tracer judge` re-judges a stored judged run (replay, or repeats with the same model) as a rescoring bundle.
- `tracer labels packet|check|agreement` collects human labels blind to the judge and reports judge-human agreement as counts with denominators.

**Non-goals (each becomes a BACKLOG row at deferral; ids in section J.10):**
- calibration, kappa or confidence intervals as claims;
- ensembles and multi-judge voting, auto-calibration, prompt optimisation;
- rating-scale rubrics;
- making the expected-outcome check real; RunVerdict;
- the judge on Phase 3 recorded calls or real-caller data;
- the judge on flagged spans (BL-174);
- the export as answer source;
- LLM-caller golden runs;
- languages other than mr;
- concurrent judge requests;
- a native Anthropic Messages adapter;
- the xAI Responses API;
- a labelling UI beyond a JSON template.

**Done means:**
- Every gate addition in section J.9 holds on hosted CI.
- Every named test exists with red-first evidence under `docs/phase4/evidence/red/batch-j*/`.
- evalschema is byte-unchanged.
- The owner gates are answered before any real judge call.
- The judge measurement report follows its template.

### J.3 Design (questions Q-J1 to Q-J20, recommended option first; the lead auto-approves and logs)

**Q-J1, the wire subset (verified as listed in section J.5).**
- Request body: `model` (always sent), `messages` (one `system` and one `user`, string content), `max_tokens` or `max_completion_tokens` per profile (always sent, required in the configuration), `temperature` (only if set in the configuration, else not sent), `seed` (only where the profile honours it, recorded as `seed_guarantee: none`), `response_format` (`json_schema` with `strict: true` only on profiles whose docs verify it), `stream: false`, `n` not sent. Never sent: `tools`, `logprobs`, `user`, `store`, `metadata`, `functions`.
- Response: `id`, `model`, exactly one `choices[0].message.content` (a string), `finish_reason` (`length` gives `judge_output_invalid`), `usage.prompt_tokens`/`completion_tokens`, optional `prompt_tokens_details.cached_tokens` (absent means absent, never 0), optional `completion_tokens_details.reasoning_tokens` (to metadata), optional `system_fingerprint` (to metadata).
- Errors: decided on HTTP status, and on `error.code` only when it is a short machine key. Never on message text.
- Rejected: the `openai` Python SDK (its retries, proxy environment handling and redirect behaviour are not ours to audit; it also adds dependencies); generalising `SarvamLlm` in place (that would break ADR 0024 rule 3 and `test_sarvam_base_url_not_overridable`).
- Trade-off: we maintain the profile table and re-verify it on drift.

**Q-J2, provider profiles (closed set, named and versioned `/1`).**

| Profile | Endpoint | Auth | Token field | Temperature, seed, `response_format` | Other |
|---|---|---|---|---|---|
| `anthropic_compat/1` | `https://api.anthropic.com/v1/chat/completions` (verified base URL) | Bearer | `max_tokens` | `response_format` never; `seed` never; temperature not sent by default (400 below 1 on 4.7+) | optional `anthropic-workspace-id` header; prompt-only JSON |
| `gemini_compat/1` | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` | Bearer | not verified | temperature, `max_tokens`, `seed` not listed on the page; "silently ignored" is stated for unlisted parameters (image section); `json_schema` only via the SDK `parse` example | status: **unverified** until the first gated request |
| `xai/1` | `https://api.x.ai/v1/chat/completions` | Bearer | not verified | `json_schema` shown in a Chat Completions example (grok-4.7) | usage and errors not verified; xAI calls the Responses API "the preferred way"; status: **unverified** |
| `openrouter/1` | `https://openrouter.ai/api/v1/chat/completions` | Bearer | `max_tokens` | `seed` integer; `json_schema` with `strict` ("The request will fail" if unsupported) | mandatory `provider` object (FJ3); optional `HTTP-Referer` and `X-OpenRouter-Title` (the owner's values); response model may differ |
| `sarvam_v2/1` | `https://api.sarvam.ai/v2/chat/completions` | `api-subscription-key` | `max_tokens` (includes reasoning) | `json_schema` guaranteed per the docs | `reasoning_effort` explicit; FJ8 terms gate |
| `openai/1` | `https://api.openai.com/v1/chat/completions` | Bearer | `max_completion_tokens` (`max_tokens` "deprecated") | `seed` Beta, "Determinism is not guaranteed" | per the openai-python SDK source |
| `local_loopback/1` | literal loopback only, for the fake server and the user's own local server | | | minimal subset | |

Rejected: a free-form "generic" profile with a user base URL (it sends unknown fields to unknown hosts). Trade-off: a new provider needs a profile row, a dossier row and tests. (Each fact's verification status, fact by fact, is in the profile table of ADR 0030.)

**Q-J3, what the judge sees.** Sees: the question (the exact scripted line), the expected facts or expected answer of that item, and the window text (`our_stt` agent-channel turns). Never sees: keys, URLs or ids; other items, or their answers or verdicts; `Evaluator.instructions` and `expected_outcome`; persona and test-profile values; the caller brain's prompt; metric definitions other than its own template; the agent export; the run's other results. Test: `test_judge_request_never_holds_key_instructions_profile_or_other_items`.

**Q-J4, pinned hosts and keys.**
- The owner-written `judge-allowlist` (0600, git-ignored, guarded, no links; unknown or repeated fields refuse the file) maps an exact URL to a profile and a key variable name.
- The judge configuration may name only an entry. The key variable named for one host is never sent to another.
- Checks at step 2, before any key read: https (loopback literal may use http), no redirects, `trust_env=False`, TLS verified, each DNS answer checked.
- Residual risk: a rebinding race between our resolution check and httpx's own connect. Filed as a BACKLOG row (BL-326); it is not claimed closed.
- Rejected: a base URL in the configuration with no allowlist (SSRF and key exfiltration through one edit); keys in the configuration file.

**Q-J5, the output contract.**
- JSON object `{verdict: agrees|disagrees|cannot_tell, reason_code: <closed set>, facts:[{index, status: stated|contradicted|absent|cannot_tell}]}`. For `equivalent_answer` mode, `facts` is empty.
- Proposed reason codes (IL to review): `all_facts_stated`, `fact_contradicted`, `fact_absent`, `meaning_differs`, `meaning_same`, `answer_off_topic`, `answer_unintelligible`, `insufficient_information`.
- Cross-field rules are enforced; there is no free text and no "reason" prose; 4,096 bytes maximum.
- One regeneration (an identical request), then `error`.
- Rejected: tool-call JSON (strict is ignored on Anthropic, and forced tool use errors on Claude 5.5); repairing fenced JSON (a parser that guesses); storing the model's explanation (unvalidated text in records).
- Note (architect, 2026-10-28; ADR 0030 Ambiguity 2): the cross-field rules of P4-J09 as written are not a partition (one fact `absent` and another `cannot_tell` meets both the `disagrees` and the `cannot_tell` condition); ADR 0030 rule 7 proposes the reading "`cannot_tell` iff no fact is `contradicted` or `absent` and at least one is `cannot_tell`" for the lead.

**Q-J6, order of operations (extends P4-21 Q15).**
1. Parse and validate the target, evaluator, sim profile, golden set (against the evaluator) and judge configuration.
2. Target allowlist and judge allowlist (host, profile, key name).
3. Terms gates: `--judge-authorisation-reference`, stored as SHA-256 only.
4. Every cap present; the judge caps too.
5. Output probe.
6. Engine factories read keys (the judge key by its bound name); call logs open.
7. to 12. As P4-21 (call, hang up, STT).
13. Scoring.
- 13b. Judge: items in golden order, sequential, one request in flight. Usage is streamed to recovery. Auth failure stops further requests. The cap is checked as reported usage plus the next `max_tokens` (BL-295 pattern).
14. Store.
15. Report.

A `BaseException` during 13b writes `usage-recovery` and is raised again (Phase 3 rule 16(d)).

**Q-J7, records.** As P4-J10. Plus:
- `ConfigSnapshot.metadata["tracer.judge_profile"]` holds: profile, host, path, model, params as sent, caps, the key variable name, the golden-set key and SHA-256, the template SHA-256 per mode, and `calibration: "uncalibrated"`.
- This is a stated exception to ADR 0027 rule 1's "no path": the endpoint path is not a file path.
- `MetricResult.metadata["tracer.judge"]` holds: verdict, reason code, fact statuses, window turn seqs, excluded-turn count, attempts, regeneration flag, exchange SHA-256, `calibration: "uncalibrated"`, and `confidence: "none_claimed"`.

**Q-J8, tier degradation.** As the P4-J23 test list:
- setup failed or STT failed: `not_applicable` `missing_input`;
- question not sent, or empty window: `error` (`question_not_sent`, `answer_window_empty`), never `disagrees`;
- a non-mr agent turn: every item `not_applicable` `unsupported_language` (FJ4);
- auth failure, cap reached, invalid output twice: `error` with its code;
- `cannot_tell`: `judge_undetermined`.

The same key set in every case.

**Further questions, Q-J9 to Q-J20.**
- Q-J9: golden sets need the scripted caller (FJ10).
- Q-J10: mr only.
- Q-J11: match modes `all_facts` and `equivalent_answer` only.
- Q-J12: usage stage `judge`, `domain=evaluation`. Cost is `not_available` without a USD catalog. OpenRouter credits are never stored as cost. No currency conversion.
- Q-J13: caps are required with no defaults: `--max-judge-requests` (every attempt and regeneration counts), `--max-judge-tokens`, and `max_tokens` per request in the configuration.
- Q-J14: exchange files are local only. The store holds hashes, never raw replies.
- Q-J15: the judge is integrated in `simulate`. Rejected: judge-only-later (FJ6: the definitions must be in the snapshot).
- Q-J16: `tracer judge` exists for replay and K repeats (FJ7).
- Q-J17: labels are files, not a store table. Rejected: an evaldb side table (a store migration for an unversioned document); labels as `MetricResult`s (`deterministic` or `llm_judge` would misstate who judged).
- Q-J18: blind labelling, and the label records its `basis` (`audio`, `stt_text` or `both`).
- Q-J19: the agreement report gives counts only in Phase 4. Kappa and confidence intervals wait for a pre-registered minimum n (calibration phase).
- Q-J20: no new package. Pure logic goes in evalcore, HTTP in evalproviders, commands in cli.

### J.4 Dependency graph and plan

**Internal dependencies:**
- **Batch E must be committed first.** The work needs: the P4-21 runner and simulated bundle (`simbundle`, `simscore`); the P4-23 CLI and `exit_code_of`; the P4-24 report; the P4-25 store; the ADR 0023 rule 17 / ADR 0027 rule 12 placeholder machinery; the Phase 3 `score` path, which stays untouched (no judge on recorded runs).
- Reused: `JsonPoster`, `secrets.load_role_key` (generalised to a name), `calllog`, recovery file, guard, `loads_strict`, ADR 0022 rescoring.

**Batch order:**
- J0 -> JA and JB in parallel -> JC -> JD -> JH.
- J01, J03 and J04 can start now, in parallel with dev batch E.
- J32 waits for P4-26.
- RJ1 does not wait for O1 or R1: it uses the offline M1 bundle.

**Critical path (about 62 h):** J01 -> J05 -> J07 -> J10 -> J17 -> J18 -> J24 -> J25 -> J26 -> J27 -> J28 -> J29 -> P4-G.

**External dependencies:**

| Dependency | Pinned? | Failure mode |
|---|---|---|
| httpx | existing range | as ADR 0024 |
| Anthropic compat | API unpinned; facts dated 2026-10-28 | silently ignored fields; temperature 400s; "not production-ready" |
| Gemini compat | unpinned, "still in beta" | silent ignoring; token cap may be ignored |
| xAI | unpinned; Chat Completions documented only by an example | usage and error shapes unknown |
| OpenRouter | unpinned | routing and model substitution; data collection defaults |
| Sarvam v2 | unpinned; docs v1 and v2 disagree | auth header; terms 10.5(a) |
| OpenAI | API reference returned 403; verified only via openai-python `main` | |
| Providers' terms and data-use pages | **not fetched**; owner gate | |

**If late:** the date moves. No cut list (owner rule).

### J.5 What is verified, from which source, retrieved 2026-10-28

**Verified:**
- **Anthropic:** platform.claude.com/docs/en/api/openai-sdk, the full field tables (FJ1).
- **Gemini:** ai.google.dev/gemini-api/docs/openai. Base URL, Bearer, `reasoning_effort` values, `parse` structured output, "still in beta", unlisted parameters "silently ignored". The page has internal inconsistencies, flagged by the fetch summary.
- **OpenRouter:** api-reference overview, provider-routing, structured-outputs (FJ3).
- **xAI:** the docs.x.ai structured-outputs guide. Base `https://api.x.ai/v1`, a `chat.completions.create` example with `json_schema`. Odd text in the guide: "SpaceXSI models", quoted as fetched.
- **Sarvam:** the v1 and v2 chat pages (FJ9).
- **OpenAI:** raw.githubusercontent.com/openai/openai-python (`main`, not a tag), `completion_create_params.py` and `completion_usage.py`. `seed` Beta and "Determinism is not guaranteed"; `max_tokens` "deprecated in favor of `max_completion_tokens`"; the usage detail fields. platform.openai.com returned 403.

**Not verified:**
- xAI Chat Completions usage, errors, `seed` and token field.
- Gemini usage fields, and whether temperature, `max_tokens` and `seed` are honoured.
- OpenRouter error codes.
- Every provider's terms, data retention and training use.
- Every provider's Marathi competence.
- The Sarvam v2 auth header requirement.
- Pricing and currencies.
- The rebinding race in httpx.

### J.6 Owner-only questions (one at a time in chat, before P4-J28)

- **O-J1, provider, model and key.** Options: (a) the owner supplies a frontier key and names the provider and model; (b) Sarvam v2 with the existing key; (c) fake only for now. Recommend (a). For OpenRouter: one pinned upstream, fallbacks off, `data_collection: deny`. Also recommend a judge model family different from the agent's LLM (self-preference).
- **O-J2, data sharing.** Sending synthetic simulated-call text, the agent's STT answers and the golden set to that provider. Its terms are recorded by SL with URL and date. Recommend accepting this for simulated runs only; real-caller data never goes to a judge without a new decision (BL-266). If the answer to O-J1 is (b), also decide Sarvam terms 10.5(a) (FJ8).
- **O-J3, pre-registered values.** Judge caps, `max_tokens`, temperature (or "not sent"), K repeats, number of runs for the label loop. Tracer supplies none.
- **O-J4, the BL-177 hold.** Does the 2026-10-28 decision lift BL-177's "no sidecar annotation format" hold for judge-verdict label files (no schema change)? Recommend yes.
- **O-J5, labellers.** Who labels in Marathi, how many (two for human-human counts), and on which basis (audio recommended). Blind to the judge.
- **O-J6, the real golden set.** Recommend: the owner supplies the questions and facts for his own agent's domain (needs native review). IL's synthetic set is for the apparatus only.

### J.7 ADR 0030 proposal: "LLM judge as an uncalibrated signal: golden sets, OpenAI-compatible provider profiles, human labels"

Written 2026-10-28 as a Proposed ADR, staged at `docs/phase4/staging/0030-llm-judge-uncalibrated-signal.md` (it moves to `docs/adr/` with P4-J01, when qa adds its pending set; the whole-ADR reader of the Phase 4 ADR tests has no pending set for an ADR from 0030 yet).

**Rules:** 1. Scope: simulated scripted runs only. 2. `tracer.golden_set/1`. 3. Items bind to scripted turns; the LLM caller is refused. 4. The answer window. 5. What the judge sees and never sees. 6. Prompt: fixed templates, JSON-encoded untrusted data, fences, cleaning. 7. Output contract and validator: never repair, one regeneration. 8. Mapping to records: enum, no pass rule, `judge_undetermined`, JudgeInfo, `tracer.judge/1`. 9. "Uncalibrated" everywhere: no confidence, no pass/fail. 10. The expected-outcome check stays inert; no RunVerdict. 11. Language (mr) and the 0.0.1 coupling. 12. The wire subset. 13. Provider profiles and verification status. 14. Pinned hosts and key binding. 15. BYOK by variable name; canaries. 16. Retries, timeouts, taxonomy. 17. Caps. 18. Usage and cost. 19. Exchange files, replay, reproducibility (no determinism claim). 20. Order of operations and recovery. 21. `tracer judge` rescoring and repeats. 22. Labels, the blind packet, agreement counts. 23. Tier degradation. 24. Data sharing and terms gates. 25. No network in tests; the fake engine and the loopback server.

**Invariants:** I1 to I8, mirroring rules 5, 7, 9, 10, 14, 19, 22 and 23. (Architect, 2026-10-28: the staged ADR adds I9, which cites `test_adr0030_rules_are_tested`, because the Phase 4 ADR format requires the last invariant to cite the ADR's own test; ADR 0030 Ambiguity 5.)

**ADR 0023 A4 deny-list.** Applies to judge CLI lines, the report section, the concept page and the agreement report. It adds to rule 1 and A1: `correct`, `incorrect`, `correctness`, `factual`, `factuality`, `truthful`, `hallucination`, `ground truth`, `calibrated` (whole word), `probability`, `confidence`, `pass`, `fail`, `passed`, `failed`, `score of the agent`.

Fixed sentence for the concept page: "Tracer's judge is an uncalibrated language-model signal: it reports whether a model said that an agent's answer agrees with the expected facts; it does not measure accuracy, correctness, factuality or quality, it is not a pass or fail of the agent, and none of its numbers is a confidence, a probability, an error rate, a recall or a precision."

### J.8 Risks and how each is measured

- **Judge bias** (self-preference, verbosity, Marathi competence): planted-label apparatus counts per plant type; judge and agent model families recorded; no claim.
- **STT noise confounded with judge error:** the label `basis`; human-on-audio against the judge-on-STT-text counts.
- **Non-determinism:** K repeats on fixed answers (counts of verdict changes); replay identity.
- **Silently ignored parameters:** record "sent", never "honoured"; reply byte cap; token cap from reported usage.
- **Routing substitution:** `reported_model` compared with the request, counted.
- **Injection:** P4-J22 suite plus the J26 audit.
- **Key exfiltration and SSRF:** J14 tests plus canaries.
- **Spend:** required caps, dry run, recovery file.
- **Small n:** counts only, no rates.
- **Golden set quality:** native review (BL-169 extended).

### J.9 Gate additions (P4-G must also hold)

15. ADR 0030 and the J02 amendments Accepted, each rule citing an existing test; index revision bumped.
16. The judge injection suite is green. The J26 audit is GO with no open P0 or P1, and was finished before RJ1.
17. No judge request can reach a host outside the owner's allowlist. Keys are bound to their host. https, no redirect, no proxy and TLS tests are green. Judge key canaries are green across every channel.
18. Every judge output is labelled uncalibrated. Deny-list tests are green (CLI, report, concept page, agreement report). The fixed sentence is present.
19. The inert check is present in every run snapshot, judged ones included. No RunVerdict exists.
20. The judge T-DEG matrix is green. Usage stage is `judge`. Cost is `not_available` without USD. No 0 and no `false` for anything missing.
21. Replay reproduces the stored verdicts byte-identically. The offline quick start with `--judge fake:` runs.
22. O-J1 to O-J5 are answered and logged before RJ1. Provider terms are recorded with URL and date. Values were pre-registered. RJ1 is logged with counts only. No call text, prompt or reply in any committed file.
23. The judge measurement report passes its template. Human-agreement numbers appear only from the owner's labels, as counts.
24. evalschema is still byte-unchanged. Every profile used for real is `verified` in the dossier.

### J.10 BACKLOG effects (filed 2026-10-28 in `docs/BACKLOG.md`, section 7)

**Existing rows:**
- BL-269: split. The judge build moves into Phase 4 batch J (in progress; the build part is filed as BL-332). The calibration part stays open (kappa and confidence intervals, pre-registered n, held-out labels).
- BL-270: stays open. The reason becomes "the judge is uncalibrated; the required check stays inert (FJ5)".
- BL-141 (P1): is the blocker for P4-J28. It closes with the P4-J08, J09 and J22 test names and J26 GO.
- BL-174: stays held. A different task (which of two transcripts is right) and accuracy-adjacent.
- BL-177: owner question O-J4.
- BL-193: extended (FJ4: a per-result turn subset).
- BL-142: the judge client is a new fetcher; its J14 controls close the judge part.
- BL-151: add pricing research for judge providers.
- BL-169: extended to golden sets.
- BL-266: applies; no real-caller data to a judge.
- BL-295: the same overshoot note for judge tokens.
- BL-188: extended for a Sarvam judge (FJ8).
- BL-015: unaffected.

**New rows:** the judge on recorded or real calls (BL-314); the export as answer source (BL-315); LLM-caller golden runs (BL-316); schema 0.0.2: a typed judge record and a typed human label (BL-317); ensembles and multi-judge voting (BL-318); auto-calibration (BL-319); prompt optimisation (BL-320); rating rubrics (BL-321); an expected-outcome (whole-call) judge (BL-322); OpenRouter credits (BL-323); a native Anthropic Messages structured-output adapter, if invalid-output counts are high (BL-324); the xAI Responses API (BL-325); the httpx DNS rebinding race (BL-326); concurrent judge requests (BL-327); more match modes (BL-328); other languages (BL-329); a labelling UI (BL-330); the Sarvam v1/v2 docs drift (BL-331); and the build part split from BL-269 (BL-332).

### J.11 What the reviewer could not verify, or did not do

- Everything marked "not verified" in section J.5.
- I did not run the test suite, `git status` or `git log`.
- I did not read the batch E working tree beyond greps of `simscore.py`, `simsummary.py` and `simulate.py`, so the exact batch E API names may differ.
- The decision log is over 256 KB; I read lines 420 to 443 only.

**Files that matter:** `<repo>/docs/phase4/SCOPE.md`; `<repo>/docs/BACKLOG.md` (BL-141, 174, 177, 193, 269, 270); `<repo>/packages/evalschema/src/tracer_evalschema/results.py` (JudgeInfo, `JUDGE_VERDICTS`, `check_result_against_definition`); `metrics.py` (`llm_judge` fields); `snapshot.py` (lines 131-141 and 278-300); `usage.py` (stage `judge`); `bundle.py` (lines 773-790 and 929-940); `provider.py` (ModelRef); `<repo>/packages/evalcore/src/tracer_evalcore/simscore.py` (line 189, the placeholder); `<repo>/packages/cli/src/tracer_cli/simsummary.py` (line 62, `exit_code_of`); `<repo>/packages/evalproviders/src/tracer_evalproviders/sarvam/_post.py` and `sarvam/chat.py`; ADR 0022 (rule 5); ADR 0023 (rules 1 and 17, A1); ADR 0024 (rules 3, 4, 5, 9, 10 and 16); ADR 0027 (rules 1, 12 and 16); `<repo>/packages/evalschema/SCHEMA_POLICY.md` (lines 576-581).
