# Changelog: `tracer-evalcore`

Every release lists what the pure scoring core gained or changed. The records it builds are
records of `tracer-evalschema` (the exact schema version is a dependency pin). The decisions behind
each entry are in the ADRs named: `docs/adr/0023-recorded-call-agreement-scoring.md` (agreement
scoring), `docs/adr/0024-provider-adapters-byok-sarvam-rest.md` (the STT boundary and the
chunker) and `docs/adr/0025-marathi-comparison-normaliser.md` (the normaliser). A change that can
alter a key, a token boundary, an offset, an alignment, a confidence or a flag changes the scorer
version, and a normaliser change also needs a new normaliser id.

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. Work in progress
(Phase 3): the result mapping, the pipeline and the shipped metric definitions are not written yet.

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

### Findings

- The results do not yet map `AlignmentTooLargeError` and `LevenshteinTooLargeError` to scorer
  `error` codes; the result mapping (Phase 3 batch D) does.
- The alignment cap was chosen from a measurement on one machine; it is revisited after the first
  real run (`docs/BACKLOG.md`).
