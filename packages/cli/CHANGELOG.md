# Changelog: `tracer-cli`

Every release lists what the `tracer` command gained or changed. The command wires the other
packages together: `tracer-evalcore` (reading, chunking, scoring, the pipeline),
`tracer-evalproviders` (the Sarvam adapter, the key, the call log), `tracer-evaldb` (the store) and
`tracer-evalschema` (the records), each an exact workspace pin. The decisions behind each entry are
in `docs/adr/0023-recorded-call-agreement-scoring.md` and
`docs/adr/0024-provider-adapters-byok-sarvam-rest.md`; what the numbers mean and do not mean is in
`docs/concepts/recorded-call-scoring.md`. A change of a printed label or of the report wording is
a change in three places: the README quick start, the end-to-end tests and the report tests
(BL-226).

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. No real request has
been sent through the command yet: the first real send (P3-30) is owner-gated and follows the run
book of ADR 0024.

First version. Everything below is new.

### Package

- The console script `tracer` (`tracer_cli.cli:main`) with one command, `tracer score`.
  `run(argv, environment)` is the command with its world injected (streams, process environment,
  clock, sleep, transport, engine factory); `main` is the only place that reads the real
  environment and clock.

### `tracer score`

- Inputs: the recording (16-bit PCM WAV), `--transcript` (the agent's VoicEra-format export),
  `--rubric` (the rubric file `tracer.rubric/1`, which states `flag_min_confidence`, the normaliser
  and the review budget; none has a default), and the required privacy and language inputs
  `--declared-language` (only `mr`), `--caller-origin`, `--consent`, `--retention`, `--out` and
  `--stt`. Nothing among them has a default.
- `--stt sarvam` sends the audio to Sarvam through `tracer-evalproviders`; `--stt fake:<dir>`
  replays recorded Sarvam bodies named `chunk-NNNN.json` (four digits) and sends nothing, reads no
  key and makes no usage record.
- The key is never an option: `SARVAM_API_KEY` is read from the environment or from `--env-file`,
  and a group- or world-readable env file prints one warning line on stderr.
- `--dry-run` reads the inputs, prints the chunk plan, the audio seconds, the number of calls and
  the `max chunks:` line, and reads no key, creates no directory and sends nothing.
- Before any request (batch G, security audit P3-28): `--max-chunks N` is required with
  `--stt sarvam` (at most 200) and refuses a larger plan, never truncating it; `--max-chunk-ms`
  above 30,000 is a usage error with Sarvam; `--stt sarvam` is refused unless `--consent obtained`
  with `--consent-reference` and `--consent-recorded-at` (stored as given on the privacy markers,
  never printed, reported, logged or sent); a `?` in `--out` is refused, the output side is
  probed, the key is read and the call log opened, and the store is opened; an export above
  10,000 tokens is refused with Sarvam.
- After the send nothing re-sends audio: a failure there is exit 1, the report is still written
  when a run exists, and the paid usage of the answered chunks is kept in
  `usage-recovery-<UTC>.json` in `--out` (mode 0600, no call text, no key, no path).
- Exit codes 0 scored, 1 unexpected failure, 2 not scored, 3 speech-to-text failed, 4 input refused,
  64 usage; `tracer score --help` lists them.

### Output

- A summary on stdout with every number and its counts: the agreement distance (MER) with H, S, D
  and I, the flag count, the flagged share of export tokens and the export turns with a flag, each
  as a numerator and a denominator; the cost as `not available` with its reason, never 0; the
  language "declared by user".
- The store `tracer.sqlite` in `--out` through `tracer-evaldb` (file mode 0600; a directory the
  command creates is 0700). It holds the call text.
- One self-contained HTML report `report-<UTC>.html` in `--out`: one hashed style and one hashed
  seek script under a Content-Security-Policy that allows nothing else, no external reference,
  every piece of untrusted text through `display_safe` and HTML escaping, each flag with the
  role of its export turn and the speech-to-text text beside it. The recording is referenced by a
  relative path; `--embed-audio` (opt-in) puts the audio into the file, which then holds personal
  data. The report holds the call text. The same bundle gives the same bytes.

### Findings

- No test plays the report's audio or runs its seek script: the relative audio reference under
  `file://` and click-to-seek need the owner's manual browser check (BL-224); there is no golden
  report file yet, only determinism (BL-222).
- Retention is recorded, not enforced: no command deletes `--out` (BL-234, BL-240).
- POSIX only (file modes, `O_NOFOLLOW`); a machine that needs an HTTP proxy cannot make a real send
  (BL-237).
