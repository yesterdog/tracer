# Changelog: `tracer-evalproviders`

Every release lists what the provider adapters gained or changed. The rules every adapter follows
(injected transport, a fixed endpoint, the key read at run time and redacted, the call log, no
network in tests) are in `docs/adr/0024-provider-adapters-byok-sarvam-rest.md`. The engine boundary
and the error taxonomy live in `tracer-evalcore` (`tracer_evalcore.stt`).

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. The Sarvam adapter
is implemented (Phase 3 batch B) and hardened after the security audit P3-28 (batch G); it is
used by `tracer score --stt sarvam`. It made its first real requests on 2026-10-26 under the run
book of ADR 0024: 124 requests in eight runs of two recordings, every one answered on its first
attempt, so no retry happened and retry billing is unverified (BL-228).

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

### Sarvam text-to-speech and chat completions (Phase 4)

- The Sarvam text-to-speech adapter and the Sarvam chat completions v2 adapter behind the `TtsEngine` and `LlmEngine` boundaries of `tracer-evalcore`, with the rules of ADR 0024 (a fixed endpoint, the key read at run time, the usage of each answered request handed out before the next). A content-addressed text-to-speech cache gives byte-identical caller audio on a re-run with no request. The call log of a simulated run holds counts and instants, no text.

### The judge client (Phase 4 batch J)

- A generic chat completions client for the judge (ADR 0030, no vendor SDK, `httpx` only) with a closed set of seven provider profiles: `anthropic_compat/1`, `gemini_compat/1`, `xai/1`, `openrouter/1`, `sarvam_v2/1`, `openai/1` and `local_loopback/1`. Each profile fixes what is sent and read. All seven are `unverified` in the dossier `docs/security/P4-J-providers.md`, which cites a URL and a retrieval date per fact or says "not verified"; an unverified profile is refused for a real run until its first owner-gated request is logged.
- Pinned hosts: the owner's allowlist (a file of mode 0600 that is not a link, an exact `https://host[:port]/path` per entry with `profile=` and `key_env=`; `http` only for a literal loopback address), no redirects followed (`follow_redirects=False`), no proxy environment read (`trust_env=False`), TLS verified, and a private, loopback or link-local DNS answer refused for a public entry. The key is read by its variable name only after the entry matches and is bound to that host. The residual race between the client's DNS check and the connect is not claimed closed (BL-326).
- Exchange files `<out>/judge/<run>/item-<id>-<attempt>.json` (mode 0600, opened without following links, git-ignored and guarded), the `replay:DIRECTORY` engine that reproduces the stored verdicts byte for byte, and the deterministic `fake:SCENARIO` engine that sends nothing. Judge usage is mapped exactly (cached tokens only when reported); cost is `not_available` without a USD catalog and credits are never stored as cost.

### Findings

- Sarvam rounds phrase times to 2 decimals, while a phrase past the exact end of its chunk drops
  that chunk's timing with a warning; such warnings are counted in the first real run.
- Behaviour that only a real request shows (audio over 30 s, 24 kHz acceptance, rate limits,
  billing rounding, the billing of retried or failed attempts) is not testable offline (BL-208,
  BL-228).
- Two batch G tests read private httpx attributes pinned at httpx 0.28.1 (BL-238).
