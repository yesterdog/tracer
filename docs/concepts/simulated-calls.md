# Simulated calls

`tracer simulate` places one call from a synthetic caller to an agent under test, records both sides and scores the recording. This page says what a simulated call reports, what it does not claim, and how the command treats safety, spend and call data. The decisions behind it are ADR 0026 (the simulator), ADR 0027 (the records of a simulated run), ADR 0028 (the caller brain) and ADR 0023 (the claim words, amendment A1); the links are under "Related".

## What a simulated call is

- `tracer simulate` places a call from a scripted synthetic caller to the agent under test: the caller's lines are written in advance in the test file, or, with an owner-valued profile, produced by a language model.
- Only the agent is evaluated; the caller is a simulator and is never evaluated.
- Tracer speaks one named wire dialect per call, such as `pipecat-protobuf/1`, written by hand from public documentation, and it labels every timing with that dialect.
- One call per invocation of the command, at concurrency 1; there is no suite or batch mode (BL-276).
- With `fake:` engines and the fake agent of `tracer-evalsim` the call opens no socket and nothing is sent.

## What is measured

Every value below is a count or a whole number of milliseconds, printed with what it is.

- Each timing is labelled with "observed at the simulator over" the dialect, so a reader can tell where it was seen.
- The first agent audio is the time from the moment the connection is established to the first frame in which the agent sent any non-zero sample; no voice-activity model runs in Tracer.
- The gap after the caller's line is the time from the end of a caller line (its last frame plus one frame) to the first frame with sound that arrives at or after that edge; the report shows the median of the gaps with N, the number of gaps.
- The overlap is the summed duration of agent frames with sound that arrive while a caller line is being sent.
- The answered caller turns are the caller lines that have a gap, that is, the lines after which the agent's sound arrived.
- Each timing is read with a quantum, the larger of the 20 ms caller frame and the longest agent frame received, because the position of a sample inside a frame is not observed.
- The instants come from the simulator's own monotonic clock, not from the agent's clock, and a value whose edge is missing is not measured: it is never printed as 0.
- The agreement between the agent's own export of the call and our speech-to-text of the agent channel is reported only when you supply the export (`--agent-transcript`).

## Not claimed

This page and the output of the command hold one sentence that names what is not claimed. It occurs once, here, in the block below, and the claim words it names appear nowhere else.

```text
Tracer reports what it observed at the simulator over one wire dialect; it does not measure accuracy, the agent's latency or quality, task success, persona realism or determinism, and none of its numbers is a WER, a CER, an error rate, a recall or a precision.
```

- A timing is what the simulator saw on the wire; it is not a statement about the agent's own processing or about its internal work.
- No number compares agents or dialects, and none is aggregated across runs here.
- A single call is one observation: nothing here says what another call would show.
- The caller's lines are the test's, not the agent's; the page does not say whether a persona sounds like a person.
- The outcome is always "outcome not evaluated": no command here says whether the agent did what the test asked.

## Roles, tier and outcome

- The roles in a simulated run are `caller`, `agent` and `unknown`; the speech-to-text turns of the agent channel have role `agent` because the channel map is declared by the simulator.
- The recording is stereo: channel 0 is the caller exactly as sent and channel 1 is the agent as received.
- The run is at tier T0: a script, our speech-to-text and the agent's own export are the evidence, and a metric whose input is missing is `not_applicable`, never 0 or false.
- The outcome is always "outcome not evaluated": the expected-outcome check is inert and no verdict is produced.

## Safety and spend

- A real target must be on your allowlist, and the allowlist is checked before any key is read; a target that is not on it is refused with exit code 4.
- Spend caps are required for every real engine and have no default; a cap reached during the call ends it.
- A file named `kill-switch` in `--out` hangs up the call within one frame.
- A minimum interval between two calls is kept with the lock file `call.lock` in `--out`.
- `--authorisation-reference` is required for every run that is not a dry run; its SHA-256 is stored in the outcome record and its text is never stored.
- `--dry-run` prints the plan and sends nothing.
- The terms of a provider are the owner's decision, answered before the first real call; no code decides them.

## Privacy

- The caller's channel is labelled synthetic caller audio (generated speech, not a person).
- The report and the store hold the call's text and the recording holds its audio, and they stay out of git.
- Put `--out` outside the repository.
- The agent's words are shown escaped in the report and in the output; they are text from the agent, never instructions to the simulator.
- With `fake:` engines no key is read, and the call log holds counts and instants only.

## Offline quick start

- The offline quick start of `tracer simulate` is in `packages/cli/README.md`, with the invented inputs of `docs/examples/simulated-mr`; the test `test_sim_readme_quick_start_runs_offline` runs it as written.
- It is plumbing: the inputs are invented, the agent is a fake, and it says nothing about any agent.
- The instants and the ids differ from run to run, because the instants are read from the simulator's real clock.
- The page is checked by `test_sim_concept_page_states_no_claims`.

## Known limits

- A native review of the scripted lines and of the Marathi text of the examples has not happened (BL-169).
- Telephony, that is dialling a number, is moved to a later stage (BL-311).
- The token cap counts the tokens the provider reports, so the prompt tokens of one request can cross it (BL-295).
- The timing source of the stored caller turns is `audio_alignment`, not a dedicated simulator source (BL-285).
- No verdict is produced for a simulated run (BL-270).
- Judging the agent's answers against expected facts is a separate, uncalibrated signal: see [LLM judge](llm-judge.md).

## Related

- [ADR 0023: recorded-call agreement scoring](../adr/0023-recorded-call-agreement-scoring.md) (amendment A1: the claim words)
- [ADR 0026: simulated caller architecture](../adr/0026-simulated-caller-architecture.md)
- [ADR 0027: simulated run records](../adr/0027-simulated-run-records.md)
- [ADR 0028: caller brain prompt contract](../adr/0028-caller-brain-prompt-contract.md)
- [packages/cli/README.md](../../packages/cli/README.md): the command and its offline quick start
- [packages/evalsim/README.md](../../packages/evalsim/README.md): the package behind the call
- [docs/examples/simulated-mr](../examples/simulated-mr/README.md): the invented inputs
- [recorded-call-scoring.md](recorded-call-scoring.md): `tracer score`, the recorded-call counterpart
- [llm-judge.md](llm-judge.md): the judge
