# tracer-cli

The `tracer` command line, version 0.0.1 (not released). `tracer score` compares the agent's transcript export of a recorded call with a speech-to-text transcript of its audio, prints the agreement distance and the flags with their counts, and writes a store and a self-contained HTML report. What the numbers mean and do not mean is on the concept page [recorded-call scoring](../../docs/concepts/recorded-call-scoring.md). Only Marathi (`mr`) is in scope in this release. Changes are in [CHANGELOG.md](CHANGELOG.md).

## Quick start (offline, no key, no network)

Run these commands from the repository root, with the workspace installed (`uv sync --locked --all-groups`). They score the invented Marathi pair of `docs/examples/synthetic-mr` against scripted speech-to-text replies, so nothing is sent and no key is read. The work directory is created outside the repository with `mktemp -d`, because the store and the report hold call text.

```sh
WORK="$(mktemp -d)"
uv run --locked python -I docs/examples/synthetic-mr/make_wav.py "$WORK/synthetic_mr.wav"
mkdir -p "$WORK/replay"
cp docs/examples/synthetic-mr/stt_chunk_00.json "$WORK/replay/chunk-0000.json"
cp docs/examples/synthetic-mr/stt_chunk_01.json "$WORK/replay/chunk-0001.json"
cp docs/examples/synthetic-mr/stt_chunk_02.json "$WORK/replay/chunk-0002.json"
uv run --locked tracer score "$WORK/synthetic_mr.wav" \
  --transcript docs/examples/synthetic-mr/export_synthetic_mr.txt \
  --rubric docs/examples/rubric-mr.example.json \
  --declared-language mr \
  --caller-origin simulated \
  --consent unknown \
  --retention keep \
  --out "$WORK/out" \
  --stt "fake:$WORK/replay"
```

The replay engine reads one file per chunk, named `chunk-NNNN.json` (four digits, from `chunk-0000.json`); the example files have other names, so they are copied. `make_wav.py` writes the 68-second synthetic recording (tones and silences, no speech) and prints its path. The synthetic pair is invented text; it is plumbing data and says nothing about Marathi quality (see its [README](../../docs/examples/synthetic-mr/README.md)).

Expected output of `tracer score`, exit code 0 (a name in angle brackets differs from run to run):

```text
result: scored
language declared by user: mr
stt provider: replay
stt model: recorded-responses (as requested)
normaliser: mr-Deva/1
agreement distance (MER): 0.10655737704918032 (13 of 122; H 109, S 9, D 2, I 2)
tokens: 120 in the export, 120 in the stt transcript
flags: 8 (confidence at or above 0.5)
flagged share of export tokens: 9 of 120 (0.075)
export turns with a flag: 7 of 22
review budget: 1.0 (no limit)
stt usage: none
cost: not available (no_usage)
ingest warnings: 0
bundle: <bundle_id>
store: <out>/tracer.sqlite
report: <out>/report-<utc_time>.html
```

`--out` then holds `tracer.sqlite` (the store) and one `report-<UTC>.html`. Open the report in a browser to see each flag beside the speech-to-text text; the report refers to the recording by a relative path, so keep both where they are. The review budget 1.0 is a labelled placeholder of the example rubric file (no limit), not a recommendation, and `flag_min_confidence` 0.5 is the value the owner pre-registered; neither has a default in code. The cost reads "not available" because a replay makes no usage record; it is never 0.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | scored |
| 1 | unexpected failure (also a failure after the send; when a chunk was answered, the paid usage is kept in `usage-recovery-<UTC>.json` in `--out`, or stdout says `usage recovery: not written`) |
| 2 | not scored (for example a speech-to-text chunk in another language) |
| 3 | speech-to-text failed (no transcript: any failed chunk fails the whole transcript) |
| 4 | input refused (before any request is sent) |
| 64 | usage (a missing or invalid option) |

`tracer score --help` prints the same list.

## Privacy and a real send

- The audio leaves your machine only with `--stt sarvam`, and then only to Sarvam.
- The key `SARVAM_API_KEY` is read from the environment or from `--env-file` (keep that file at mode 0600; a looser mode prints a warning), and it is never on the command line.
- With `fake:` a replay sends nothing and reads no key.
- The report and the store hold call text and must stay out of git.
- Put `--out` outside the repository.
- When the command creates `--out`, the leaf directory is 0700 and the parents follow the umask; an existing `--out` that group or other users can reach earns one warning on stderr.
- Every privacy and language option (`--declared-language`, `--caller-origin`, `--consent`, `--retention`, `--out`, `--stt`) is required and none has a default.
- `--stt sarvam` also needs `--consent obtained` with `--consent-reference` and `--consent-recorded-at`, and a `--max-chunks` ceiling; the reference is stored as given and never printed or reported.
- `--embed-audio` puts the call audio into the report file; it is opt-in, and the file must then not be shared.
- Retention is recorded, not enforced: delete `--out` by hand when it ends.
- Before a real send, follow the Run book for a real send of ADR 0024: [run book](../../docs/adr/0024-provider-adapters-byok-sarvam-rest.md), starting with a `--dry-run` that uses exactly the arguments of the send. Do not press Ctrl-C during a send: since batch I it keeps the usage of the answered chunks in the recovery file, but the run then has no store row and no report.
- What the numbers mean and do not mean, and the known limits: [concept page](../../docs/concepts/recorded-call-scoring.md).
