# Changelog: `tracer-evalproviders`

Every release lists what the provider adapters gained or changed. The rules every adapter follows
(injected transport, a fixed endpoint, the key read at run time and redacted, the call log, no
network in tests) are in `docs/adr/0024-provider-adapters-byok-sarvam-rest.md`. The engine boundary
and the error taxonomy live in `tracer-evalcore` (`tracer_evalcore.stt`).

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. Work in progress
(Phase 3): the Sarvam adapter is being implemented against the batch B tests; nothing here has
made a real request yet.

First version. Everything below is new.

### Package

- HTTP through `httpx>=0.28.1,<1` (lower bound tested in CI) with an injectable transport;
  `tracer-evalcore` and `tracer-evalschema` are exact workspace pins. No vendor SDK.

### Sarvam speech-to-text (REST)

- Fixed endpoint `https://api.sarvam.ai/speech-to-text`: no URL override, no redirects followed,
  TLS always verified. One request in flight per run.
- Each chunk is one request with exactly four form fields: the WAV chunk, `language_code=unknown`,
  the model (always sent; `saaras:v4` is the default the docs state) and `with_timestamps=true`.
  A configured language other than mr is refused before any request.
- Bring your own key: `SARVAM_API_KEY` from the environment, or from `--env-file`, which is then
  the only source and is read for that one variable. The key sits in a redacting wrapper and is in
  no exception, log, call-log line, record, store or report.
- At most 4 attempts per chunk, waits of 1, 2 and 4 s with no jitter, `Retry-After` honoured up to
  30 s; timeouts 10 s to connect and for the pool, 60 s to read and write; response bodies capped at
  1 MiB. These are technical defaults, not measurements. The first chunk that fails ends the run,
  and no transcript is built from the others.
- One UsageRecord per successful chunk: measured audio seconds rounded half to even to 18
  fractional digits, the rounding stated in `metadata["tracer.audio_seconds_rounding"]`
  (`half_even_18` or `exact`), `requests` 1, `occurred_at` from the response `Date` header.
- Cost is `not_available` without a user USD price file; INR is never converted. A user price file
  must also price the `requests` unit, or every chunk is `unit_unpriced`.
- Call log `sarvam_call_log.jsonl` (mode 0600): one line before each send, flushed, and one after;
  no text, no key, no path.

### Findings

- Sarvam rounds phrase times to 2 decimals, while a phrase past the exact end of its chunk drops
  that chunk's timing with a warning; such warnings are counted in the first real run.
- Behaviour that only a real request shows (audio over 30 s, 24 kHz acceptance, rate limits,
  billing rounding) is not testable offline.
