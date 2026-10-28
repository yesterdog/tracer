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

Status: not released. No version tag exists and nothing is published to PyPI. The first real
sends through the command happened on 2026-10-26 under the owner's key, following the run book of
ADR 0024 (P3-30 and P3-31; counts only, in the measurement report
`docs/phase3/measurements/2026-10-26-noise-floor.md`).

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
- Batch I (the re-audit of batch G, findings R1 to R6): the usage of the chunks answered so far is
  kept whatever ends the send, a Ctrl-C (`KeyboardInterrupt`), a transport exception or a call-log
  `OSError` included: an engine that sets `streams_usage` gets an `on_usage` callback, and the
  command catches `BaseException` around the pipeline and the store write, writes the recovery
  file when it holds usage and raises the same exception again (a Ctrl-C leaves the command with
  no exit code of its own and no report). A recovery file that cannot be created is said with
  `usage recovery: not written`. An existing `--out` with group or other permission bits earns
  one stderr warning with its mode. A link in place of `tracer.sqlite` is refused
  (`output_unavailable (store_file_is_a_link)`, exit 4). Every instant option is parsed by one
  strict pattern (no hour 24, no minute or second 60, no offset minute 60) on every Python version.
- Exit codes 0 scored, 1 unexpected failure, 2 not scored, 3 speech-to-text failed, 4 input refused,
  64 usage; `tracer score --help` lists them.

### Output

- A summary on stdout with every number and its counts: the agreement distance (MER) with H, S, D
  and I, the flag count, the flagged share of export tokens and the export turns with a flag, each
  as a numerator and a denominator; the cost as `not available` with its reason, never 0; the
  language "declared by user".
- The store `tracer.sqlite` in `--out` through `tracer-evaldb` (file mode 0600; when the command
  creates `--out`, the leaf directory is 0700 and the parents follow the umask). It holds the call text.
- One self-contained HTML report `report-<UTC>.html` in `--out`: one hashed style and one hashed
  seek script under a Content-Security-Policy that allows nothing else, no external reference,
  every piece of untrusted text through `display_safe` and HTML escaping, each flag with the
  role of its export turn and the speech-to-text text beside it. The recording is referenced by a
  relative path; `--embed-audio` (opt-in) puts the audio into the file, which then holds personal
  data. The report holds the call text. The same bundle gives the same bytes.

### `tracer simulate` (Phase 4)

- `tracer simulate` places one simulated call from a scripted synthetic caller (or, with an owner-valued profile, a language-model caller) to an agent, records both sides on a stereo recording and scores it. `--target FILE` with `--allowlist FILE` reaches an agent (owner-gated, with caps and the run book of ADR 0026); `--agent fake:FILE` runs the offline fake agent of `tracer-evalsim` and opens no socket. `--tts` is `sarvam` or `fake`; `--caller` is `scripted`, `sarvam` (a language-model caller) or `fake:DIRECTORY` (replayed lines); `--stt` is `sarvam`, `fake` (the words of the fake agent) or `fake:DIRECTORY` (recorded bodies). Every value that decides the call or its spend is an option or a file entry and none has a default.
- Order of operations pinned by a trace: inputs, the target allowlist, the authorisation reference, the caps, the output probe, the keys and call logs, the call, scoring, the store and the report; a refusal comes before any key is read. `--authorisation-reference` is required for every run that is not a dry run; its SHA-256 is stored in the run's outcome record and its text never. `--dry-run` prints the plan and sends nothing.
- Output: the lines of the call (result, end reason, "outcome: not evaluated", dialect, the labelled timings each "observed at the simulator over" the dialect with its quantum, the answered caller turns, the agreement with the agent's own export when `--agent-transcript` is given), the usage lines and `cost: not available` without usage; the store, the recording (`recording-<UTC>.wav`, channel 0 the caller and channel 1 the agent), the report and `sim_call_log.jsonl`.
- Exit codes: 0, 1, 2, 3, 4 and 64 as for `tracer score`, and the new exit code 5, call not established (the run is still stored and every result is not applicable); when several apply the first of 1, 5, 3, 2, 0 wins. `tracer simulate --help` lists them.

### The judge (Phase 4 batch J)

- `tracer simulate --golden FILE --judge-config FILE --judge ENGINE` also asks a judge, once per golden item after scoring and before the store, whether the agent's answer agrees with the expected facts, as an uncalibrated signal (ADR 0030). `--judge` takes a profile name (`openrouter/1` and the others, owner-gated: `--judge-allowlist`, `--max-judge-requests`, `--max-judge-tokens`, `--judge-authorisation-reference`, and `--judge-first-request-gate` for a profile that is not verified), `fake:SCENARIO` (a deterministic engine that sends nothing) or `replay:DIRECTORY` (the stored exchange files).
- Output: the judge lines ("judge: uncalibrated signal", the counts "n of N items", one line per item with the verdict, the reason code and the fact statuses, the judge usage) and a report section titled "Uncalibrated judge signal"; expected facts are never printed; a judge result of status `error` gives exit code 2, a verdict `disagrees` gives 0.
- The judge authorisation reference is printed as `judge authorisation reference: sha256 <hex>` and is not stored in any record (BL-363); keep the hash with your own record.
- `tracer judge --out DIR --bundle ID --golden FILE --judge-config FILE --judge ENGINE [--repeats K]` re-judges a stored judged run and stores one rescoring bundle per repeat (K from 1 to 100); it prints how many items changed from the stored run. It is refused when the run holds no judge definitions or the model differs.
- `tracer labels packet|check|agreement`: `packet` writes a blind packet (never the judge's verdict), `check` validates a labels file and copies it content-addressed (mode 0600) under `<out>/labels/`, and `agreement` prints counts of the judge against the labels, with unlabelled items shown apart and no rate. The line `labelled: n of N items` of `check` counts the labels the file lists, not the items of the run (BL-350).

### Documentation (P4-37, P4-J30)

- The README has the sections "Simulate quick start (offline, no key, no network)" and "Judge quick start (offline, no key, no network)" and the exit codes of the three new commands; both quick starts are run by the test suite (`test_sim_readme_quick_start_runs_offline`, `test_judge_readme_quick_start_runs_offline`) with the real command, offline, and their expected output is compared line by line. The inputs are the invented files of `docs/examples/simulated-mr`.
- The concept pages `docs/concepts/simulated-calls.md` and `docs/concepts/llm-judge.md` say what the commands report and, in a fenced sentence each, what is not claimed. A change of a printed label or of the report wording is a change in the README quick starts, the end-to-end tests and the report tests (BL-226).

### Findings

- No test plays the report's audio or runs its seek script. The relative audio reference under
  `file://` and click-to-seek were checked by the owner in Chrome and Edge on 2026-10-26 on the
  real report of the first real send; supported browsers: Chrome and Edge only; other browsers are
  out of scope for now; the embedded-audio mode and browser versions are not tested; the player
  starts a few seconds before the flagged spot because it jumps to the start of the matching
  speech-to-text phrase (BL-224, closed). There is no golden report file yet, only determinism
  (BL-222).
- Retention is recorded, not enforced: no command deletes `--out` (BL-234, BL-240).
- A link at `--out` itself and links at SQLite's side files are not checked (BL-246); the dry run
  does not require `--max-chunks` (BL-247).
- POSIX only (file modes, `O_NOFOLLOW`); a machine that needs an HTTP proxy cannot make a real send
  (BL-237).
