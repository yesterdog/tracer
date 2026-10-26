# Changelog: `tracer-evalproviders`

Every release lists what the provider adapters gained or changed. The rules every adapter follows
(injected transport, a fixed endpoint, the key read at run time and redacted, the call log, no
network in tests) are in `docs/adr/0024-provider-adapters-byok-sarvam-rest.md`. The engine boundary
and the error taxonomy live in `tracer-evalcore` (`tracer_evalcore.stt`).

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. The Sarvam adapter
is implemented (Phase 3 batch B) and hardened after the security audit P3-28 (batch G); it is
used by `tracer score --stt sarvam`. Nothing here has made a real request yet: the first real
send (P3-30) is owner-gated and follows the run book of ADR 0024.

First version. Everything below is new.

### Package

- HTTP through `httpx>=0.28.1,<1` (lower bound tested in CI) with an injectable transport;
  `tracer-evalcore` and `tracer-evalschema` are exact workspace pins. No vendor SDK.

### Sarvam speech-to-text (REST)

- Fixed endpoint `https://api.sarvam.ai/speech-to-text`: no URL override, no redirects followed,
  TLS always verified. One request in flight per run.
- The `httpx.Client` is built with `trust_env=False` (batch G, audit F3): it reads no proxy
  variable (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, any case), no certificate
  variable (`SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`) and no
  `.netrc`, and still verifies TLS against the bundled roots. This reverses the earlier trade-off:
  a machine that needs a proxy or extra CA files cannot make a real send yet (BL-237).
- Every request, every retry included, sends exactly one `Accept-Encoding: identity` header
  (batch G, audit F10); a compressed reply is still decoded and still bounded by the 1 MiB cap.
- Each chunk is one request with exactly four form fields: the WAV chunk, `language_code=unknown`,
  the model (always sent; `saaras:v4` is the default the docs state) and `with_timestamps=true`.
  A configured language other than mr is refused before any request.
- Bring your own key: `SARVAM_API_KEY` from the environment, or from `--env-file`, which is then
  the only source and is read for that one variable. The key sits in a redacting wrapper and is in
  no exception, log, call-log line, record, store or report.
- The env file (batch G, audit F9) is opened read-only and non-blocking and judged by `fstat`:
  anything but a regular file is refused (`env_file_not_regular`; a FIFO cannot hang the command),
  a file over `ENV_FILE_MAX_BYTES` = 65,536 bytes is refused (`env_file_too_large`; at most one
  byte over the cap is read), and a symbolic link to a regular file is followed. A file with any
  group or other permission bit earns exactly one `EnvFileModeWarning` (the mode only, never a
  line of the file); a warning never refuses the run. Errors carry the reason only, no path.
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
- Call-log hardening (batch G, audit F7): `CallLog.open` creates a missing output directory with
  mode 0700 whatever the umask, and the log is opened with `O_NOFOLLOW`, so a symbolic link in its
  place (also a dangling one) is refused before anything is written; an existing log with a looser
  mode is tightened to 0600. POSIX only. Only the leaf directory is forced to 0700; parents it
  creates follow the umask.
- Usage streaming (batch I, re-audit R1): `SarvamStt.streams_usage` is true and `transcribe` takes
  a keyword-only `on_usage`; the usage record of each answered chunk is handed to it before the
  next request is built, once per chunk (a retried chunk once), and the outcome carries the same
  records with the same ids. An exception that is not an `SttError` (or a `KeyboardInterrupt`)
  leaves `transcribe` unwrapped, and an exception of `on_usage` stops the send.
- The dry-run plan (`tracer_evalproviders.dryrun`) reads no key, opens no log and no connection and
  writes no file; it prints a cost only when the user supplied a USD price.

### Findings

- Sarvam rounds phrase times to 2 decimals, while a phrase past the exact end of its chunk drops
  that chunk's timing with a warning; such warnings are counted in the first real run.
- Behaviour that only a real request shows (audio over 30 s, 24 kHz acceptance, rate limits,
  billing rounding, the billing of retried or failed attempts) is not testable offline (BL-208,
  BL-228).
- Two batch G tests read private httpx attributes pinned at httpx 0.28.1 (BL-238).
