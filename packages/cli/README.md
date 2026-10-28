# tracer-cli

The `tracer` command line, version 0.0.1 (not released). `tracer score` compares the agent's transcript export of a recorded call with a speech-to-text transcript of its audio, prints the agreement distance and the flags with their counts, and writes a store and a self-contained HTML report. What the numbers mean and do not mean is on the concept page [recorded-call scoring](../../docs/concepts/recorded-call-scoring.md). Only Marathi (`mr`) is in scope in this release. Changes are in [CHANGELOG.md](CHANGELOG.md).

Three more commands are in this release. `tracer simulate` places one call from a scripted synthetic caller to an agent, records both sides and scores the recording; what it reports and does not claim is on the concept page [simulated calls](../../docs/concepts/simulated-calls.md), and the package behind it is [tracer-evalsim](../evalsim/README.md). With `--golden`, `--judge-config` and `--judge` it also asks a language model, once per golden item, whether the agent's answer agrees with the expected facts, as an uncalibrated signal; `tracer judge` repeats that on a stored run and `tracer labels` runs the human label loop; all of it is on the concept page [LLM judge](../../docs/concepts/llm-judge.md). Each has an offline quick start below, and the example inputs are in [docs/examples/simulated-mr](../../docs/examples/simulated-mr/README.md).

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

`--out` then holds `tracer.sqlite` (the store) and one `report-<UTC>.html`. Open the report in Chrome or Edge to see each flag beside the speech-to-text text; the report refers to the recording by a relative path, so keep both where they are. Audio playback and click-to-seek were checked by the owner in Chrome and Edge on 2026-10-26 on the real report of the first real send; supported browsers: Chrome and Edge only; other browsers are out of scope for now; the embedded-audio mode and browser versions are not tested, and no automated (headless) browser test exists; the player starts a few seconds before the flagged spot because it jumps to the start of the matching speech-to-text phrase (BL-224, closed). The review budget 1.0 is a labelled placeholder of the example rubric file (no limit), not a recommendation, and `flag_min_confidence` 0.5 is the value the owner pre-registered; neither has a default in code. The cost reads "not available" because a replay makes no usage record; it is never 0.

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

## Simulate quick start (offline, no key, no network)

Run these commands from the repository root, with the workspace installed (`uv sync --locked --all-groups`). They place one simulated call with a fake agent, so no socket is opened, nothing is sent and no key is read: the agent is read from a file (`--agent fake:FILE`), the caller's voice is a tone (`--tts fake`), the caller's lines are the scripted ones (`--caller scripted`) and the agent's words come from the same file (`--stt fake`). The inputs are the invented files of `docs/examples/simulated-mr`. The work directory is created outside the repository with `mktemp -d`, because the store, the recording and the report hold call text and audio. Each command exits 0.

```sh
WORK="$(mktemp -d)"
uv run --locked tracer simulate \
  --agent fake:docs/examples/simulated-mr/fake-agent.json \
  --evaluator docs/examples/simulated-mr/evaluator.json \
  --persona docs/examples/simulated-mr/persona.json \
  --sim-profile docs/examples/simulated-mr/sim-profile.json \
  --tts fake \
  --caller scripted \
  --stt fake \
  --authorisation-reference "offline quick start" \
  --out "$WORK/out"
```

`--authorisation-reference` is required for every run that is not a dry run, offline ones included: it is a text of 1 to 200 characters that names who authorised the call; only its SHA-256 is kept in the run's outcome record and the text is never stored or printed. The run takes about twenty seconds because the caller's frames are paced in real time. The timings are read from the simulator's own clock, so the instants and the ids differ from run to run (a name in angle brackets below stands for any text on that line). `agreement` is not scored because no `--agent-transcript` export was given. `outcome: not evaluated` is fixed: no command here says whether the agent did what the test asked.

Expected output of `tracer simulate`, exit code 0:

```text
result: call established
end reason: script_complete
outcome: not evaluated
dialect: pipecat-protobuf/1
caller audio: synthetic (generated speech, not a person)
caller turns: 10 started, 10 completed
agent segments: 10
first agent audio: <ms> ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)
gap after the caller's line (median): <ms> ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, N 10)
overlap: 0 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)
answered caller turns: 10 (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)
agreement: not scored (missing input agent_transcript)
tts usage: none
llm usage: none
stt usage: none
cost: not available (no_usage)
ingest warnings: 0
bundle: <bundle_id>
store: <out>/tracer.sqlite
recording: <out>/recording-<utc_time>.wav
report: <out>/report-<utc_time>.html
```

`--out` then holds `tracer.sqlite` (the store), `recording-<UTC>.wav` (the caller on channel 0 and the agent on channel 1), `report-<UTC>.html` and `sim_call_log.jsonl` (counts and instants, no text). Every timing is labelled with what it is and "observed at the simulator over" the dialect; none of it is a statement about the agent's own processing. A real call needs more: a target file and an allowlist entry, an owner-written simulator profile, a cap for each real engine and the run book of ADR 0026; `--dry-run` prints the plan first and sends nothing.

## Judge quick start (offline, no key, no network)

Run these commands from the repository root, as above. They place the same simulated call with a golden set and a judge engine that is a fake (`--judge fake:agrees`), then re-judge the stored run twice and walk the label loop with a stand-in for a human labeller. Nothing is sent and no key is read. The fake judge reads nothing: it answers `agrees` for every item whatever the agent said, so the output shows the shape of the judge lines, not a judgement of anything. The golden set is the invented one of `docs/examples/simulated-mr` (not reviewed by a native speaker, BL-169). Each command exits 0, and the instants and ids differ from run to run.

```sh
WORK="$(mktemp -d)"
uv run --locked tracer simulate \
  --agent fake:docs/examples/simulated-mr/fake-agent.json \
  --evaluator docs/examples/simulated-mr/evaluator.json \
  --persona docs/examples/simulated-mr/persona.json \
  --sim-profile docs/examples/simulated-mr/sim-profile.json \
  --tts fake \
  --caller scripted \
  --stt fake \
  --authorisation-reference "offline quick start" \
  --golden docs/examples/simulated-mr/golden-set.json \
  --judge-config docs/examples/simulated-mr/judge-config.json \
  --judge fake:agrees \
  --out "$WORK/out" > "$WORK/simulate.txt"
grep -E '^(result|judge[a-z _]*): ' "$WORK/simulate.txt"
BUNDLE="$(sed -n 's/^bundle: //p' "$WORK/simulate.txt")"
uv run --locked tracer judge --out "$WORK/out" --bundle "$BUNDLE" \
  --golden docs/examples/simulated-mr/golden-set.json \
  --judge-config docs/examples/simulated-mr/judge-config.json \
  --judge fake:agrees --repeats 2 > "$WORK/judge.txt"
grep -E '^(rescoring of|repeats|repeat [0-9]+ of [0-9]+|judge changed)' "$WORK/judge.txt"
uv run --locked tracer labels packet --out "$WORK/out" --bundle "$BUNDLE" \
  --golden docs/examples/simulated-mr/golden-set.json
uv run --locked python -I docs/examples/simulated-mr/fill_labels.py "$WORK/out/labels/packet-$BUNDLE.json" "$WORK/labels.json"
uv run --locked tracer labels check --out "$WORK/out" --labels "$WORK/labels.json"
uv run --locked tracer labels agreement --out "$WORK/out" --bundle "$BUNDLE" > "$WORK/agreement.txt"
grep -E '^(agreement report|items|labeller|judge agrees, human agrees|unlabelled by)' "$WORK/agreement.txt"
```

The first `grep` shows the judge lines of `tracer simulate` (the ten `judge item` lines and the other lines of the run are in `simulate.txt`); the `judge` command stores one rescoring bundle per repeat and prints how many items changed from the stored run (0 here, because the same fake answers again); `fill_labels.py` writes a labels file in which every item but the last is `agrees` and the last stays `null`, which `labels check` reports as unlabelled. In the line `labelled: n of N items` (9 of 10 here), N is the number of labels the file lists, not the number of items of the run: a file that lists fewer labels than the run has items gets a smaller N (BL-350). The judge authorisation reference (`--judge-authorisation-reference`) is needed only for a real provider; it is printed as `judge authorisation reference: sha256 <hex>` and is not stored anywhere, so keep the hash with your own record (BL-363).

Expected output, exit code 0 for each command:

```text
result: call established
judge: uncalibrated signal (golden set mr_apparatus_set, engine fake/1, model fake-judge)
judge agrees: 10 of 10 items
judge disagrees: 0 of 10 items
judge cannot_tell: 0 of 10 items
judge error: 0 of 10 items
judge not applicable: 0 of 10 items
judge usage: none
rescoring of: <bundle_id>
repeats: 2
repeat 1 of 2: <bundle_id>
judge changed from the stored run: 0 of 10 items
repeat 2 of 2: <bundle_id>
judge changed from the stored run: 0 of 10 items
packet: <out>/labels/packet-<bundle_id>.json
items: 10
blind: the judge's verdict is not in the packet
labels: <labels_file>
labels: ok
bundle: <bundle_id>
labeller: labeller-1
labelled: 9 of 10 items
unlabelled: 1 of 10 items
stored: <out>/labels/labels-<hash>.json
agreement report: counts of an uncalibrated judge signal against human labels
items: 10
labeller: labeller-1 (basis audio 9, stt_text 0, both 0)
judge agrees, human agrees: 9 of 10 items
unlabelled by labeller-1: 1 of 10 items (not counted as agreement or disagreement)
```

The packet, the labels file and the exchange files of a real judge are call text and must stay out of git (the guard refuses them). What each judge line means, the golden-set format, the provider profiles, the owner allowlist, the caps and the labeller guide are on the concept page [LLM judge](../../docs/concepts/llm-judge.md).

## Exit codes of tracer simulate, tracer judge and tracer labels

- `0`: the call was established and every result is scored or not applicable
- `1`: unexpected failure
- `2`: a result was not scored, or the caller's language model failed
- `3`: speech-to-text failed
- `4`: input refused
- `5`: call not established
- `64`: usage

When several apply, the first of 1, 5, 3, 2, 0 wins. A judge result with status `error` is a result that was not scored, so it gives 2: an item whose answer window is empty, a judge reply that is invalid twice, a request cap reached, an authentication refusal and a `cannot_tell` verdict (stored as the error `judge_undetermined`). A verdict of `disagrees` is a result, not a failure of the command, so it gives 0. `tracer judge` gives 2 if any judge result of any repeat is an `error`, else 0. A refusal before any request (a missing cap, a file that does not validate, a host that is not on the allowlist) gives 4 with one line `error: <code>` on stderr, and a mistyped option 64. `tracer simulate --help` prints the same list.
