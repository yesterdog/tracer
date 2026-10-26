# Changelog: `tracer-evalcore`

Every release lists what the pure scoring core gained or changed. The records it builds are
records of `tracer-evalschema` (the exact schema version is a dependency pin). The decisions behind
each entry are in the ADRs named: `docs/adr/0023-recorded-call-agreement-scoring.md` (agreement
scoring), `docs/adr/0024-provider-adapters-byok-sarvam-rest.md` (the STT boundary and the
chunker) and `docs/adr/0025-marathi-comparison-normaliser.md` (the normaliser). A change that can
alter a key, a token boundary, an offset, an alignment, a confidence or a flag changes the scorer
version, and a normaliser change also needs a new normaliser id.

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. Phase 3 batches A
to G are in: the readers, the chunker, the scoring core, the shipped metric definitions, the
rubric file, the result mapping and the pipeline. No real recording has been scored yet (the first
real send, P3-30, is owner-gated).

First version. Everything below is new.

### Package

- Pure core: the standard library and `tracer-evalschema==0.0.1` only. It imports no network
  module (`socket`, `ssl`, `http`, `urllib`, `httpx`), so no part of it can open a connection.
- Typed errors under `EvalcoreError`; every refusal of an input is an `InputRefusedError` (also a
  `ValueError`) whose message holds sizes, positions and codes, never the input text.

### Readers

- WAV reader (`tracer_evalcore.importers.wav`, standard library `wave`): 16-bit PCM only; non-PCM, 8- and 24-bit, the extensible format tag 0xFFFE, zero channels, truncated files
  and a header frame count beyond the file are refused with typed errors. The RIFF size field is
  ignored; zero frames are valid. `DEFAULT_MAX_WAV_BYTES` is 1 GiB (1,073,741,824 bytes), checked
  before reading, and no read buffer is sized from the cap.
- VoicEra export reader (`tracer_evalcore.importers.voicera`), the Phase 1 F8 mapping promoted:
  `assistant` maps to `agent` and `user` to `caller`, text kept verbatim in `seq` order, no language
  or timing set, a declared language only from the caller's argument. A warning that drops content
  is a refusal unless `accept_warnings=True`. One deviation from F8: a `call_id` with a control or
  bidi character is always refused (`call_id_unsafe_characters`), even with `accept_warnings=True`,
  and is never echoed.

### Audio

- Deterministic chunker (`tracer_evalcore.audio`): windows of at most `max_chunk_ms`, cut at the
  lowest-energy whole 20 ms frame inside the last `search_ms` (ties to the latest frame); no
  candidate frame is a `ChunkerParameterError`, never a silent fixed cut. Chunks cover every sample
  once and are byte-identical for the same input. Stereo is refused unless
  `accept_provider_downmix=True`, which keeps the channels unchanged; three or more channels are
  always refused. The plan is the integer-only document `tracer.chunk_plan/1`.
- `exact_seconds(frames, rate)` returns a `Decimal` only when the quotient terminates, else `None`;
  it is never rounded and never a float.

### Scoring

- Comparison normaliser `mr-Deva/1` (Marathi, Devanagari; ADR 0025): NFC, not NFKC; Devanagari
  digits folded and ZWJ/ZWNJ dropped in the comparison key only; offsets are code points of the
  stored text; lone surrogates refused. The normaliser id is a required argument everywhere. Every
  rule is marked `needs_native_review`: no native speaker has reviewed it yet.
- Tokens with half-open code-point offsets into the original text, a key per token.
- Word alignment with the cost pair (edits, deletions plus insertions), so the H, S, D and I counts
  are unique and MER is symmetric; the backtrace prefers match, substitution, deletion, insertion.
  `ALIGN_MAX_TOKENS` is 10,000 tokens per side (a byte table of about 100 MB and about 8 s on
  Python 3.14 measured on one machine; the P3-28 audit extrapolates about 17 s for the worst case,
  so 8 s is not an upper bound, BL-199), checked before any allocation; above it `AlignmentTooLargeError`.
  Changing the cap changes the scorer version. Two empty sides are `UndefinedMerError`, never 0 or 1.
- Disagreement spans on the export only, never across a turn boundary; insertions are zero-width
  at their gap.
- Flags: the confidence is the Levenshtein distance of the two span keys over the longer key
  length, uncalibrated and not a probability, compared exactly with `flag_min_confidence` (no
  default; more than 18 fractional digits is a `ThresholdError`). The Levenshtein distance is exact,
  computed with the Myers/Hyyro bit-parallel method on Python integers;
  `LEVENSHTEIN_MAX_CHARS` is 200,000 code points per side, above which `LevenshteinTooLargeError`
  carries the sizes only. The `tracer.flags/1` JSON Schema is `tracer_evalcore.flags.FLAGS_JSON_SCHEMA`.

### Speech-to-text boundary

- `tracer_evalcore.stt`: the `SttEngine` protocol, the typed result of one transcription and the
  provider-neutral error taxonomy (`SttError` and its subclasses). Errors hold machine facts only:
  never a provider message, a body, a header, a key or audio. The pipeline types the engine through
  this module, so the core never imports a network package.
- Batch I (re-audit R1): `UsageSink` and the runtime protocol `UsageStreamingSttEngine`
  (`streams_usage` and `transcribe(plan, context, *, on_usage=None)`), so an engine can hand out
  each answered chunk's usage as the send goes on; `SttEngine` is unchanged.

### Definitions, rubric file and results (batch D)

- Three shipped definition templates, `stt_export_agreement_mer` (unit `ratio`),
  `stt_export_flag_count` (unit `count`) and `stt_export_flagged_token_share` (unit `ratio`), with
  `requires=["transcript", "agent_transcript"]`, `languages=["mr"]` and no pass rule; each takes
  the parameters `flag_min_confidence` and `normaliser`, neither with a default. The units `wer` and
  `cer` are never used.
- The rubric file `tracer.rubric/1` (`tracer_evalcore.rubricfile`): the one place where the
  threshold, the normaliser and the review budget are written; a file that lacks one is refused
  with a stable code (`rubric_budget_missing` and others) before anything is sent. The optional
  `note` is never stored.
- The configuration of a recorded run (`build_configuration`): fresh version-1 definitions from the
  templates and the rubric file, the inert required judge check that schema 0.0.1 demands (never
  run, never shown as a check of the agent), and the reserved agent endpoint
  `wss://recorded-call.invalid/` (ADR 0023 rules 16 and 17).
- The scorer registry (`tracer_evalcore.registry`): an unknown `implementation_ref` stops the run
  before any send; the scorer version is the distribution version of this package.
- The `tracer.flags/1` document (`tracer_evalcore.flagsdoc`): built and validated, every cross-field
  rule a refusal with one stable code; it holds no text and no identifier.
- The result mapping (`tracer_evalcore.scoring`): the three results of a recorded call share one
  alignment; a failure is an `error` result whose message holds counts and sizes only, with the
  codes `no_tokens` (either side), `alignment_too_large`, `levenshtein_too_large`,
  `stt_language_not_observed`, `stt_unsupported_language` and `stt_provenance_missing`, never a
  value of 0 or 1.
  `export_token_count` (batch G) counts the export tokens the aligner will see, so the command can
  refuse an export over `ALIGN_MAX_TOKENS` before a paid send.
- The pipeline (`tracer_evalcore.pipeline.score_recorded_call`): reads, transcribes through the
  injected engine, scores and builds a run bundle at tier T0 with no start time and no verdict that
  passes `check_run_bundle`. It reads no clock, environment or file and logs nothing; ids are fresh
  UUIDv7 values per call, so two runs of one call agree only after their ids are masked. A failed
  transcription is recorded as `missing_input` with a Run warning.

### Findings

- The concept page `docs/concepts/recorded-call-scoring.md` lists the known limits of the scoring:
  number words against digits (BL-192), the two-letter-word threshold behaviour (BL-243), the split
  word (BL-244), the unmeasured noise floor (P3-31) and the missing native review (BL-169).
- The alignment cap was chosen from a measurement on one machine; it is revisited after the first
  real run (`docs/BACKLOG.md`).
