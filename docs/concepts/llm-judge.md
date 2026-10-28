# LLM judge

`tracer simulate` can ask a language model, once for each golden item, whether the agent's answer to a scripted question agrees with the expected facts. This page says what that judge reports and what is not claimed, how a golden set, the answer window, a provider profile, the owner allowlist and the caps work, how to replay and repeat a judgement, and how a person labels the same answers so that the two can be counted side by side. The decisions behind it are ADR 0030 (the judge) and ADR 0023 (the claim words, amendment A4); the provider facts are in the dossier `docs/security/P4-J-providers.md`; the links are under "Related".

## What the judge reports

- The judge is an uncalibrated signal: it reports what a model said about one answer, and nothing more.
- Each item gets one verdict from a closed set, `agrees`, `disagrees` or `cannot_tell`.
- Each verdict comes with a reason code from a closed set and, in the match mode `all_facts`, a fact status for each expected fact.
- Every count is printed as "n of N items", with its denominator, and the expected facts and answers are never printed.
- No number here is a measure of the agent: the signal is what a model said, and nothing in the output turns it into a figure about the agent.
- The judge runs only inside `tracer simulate` (and `tracer judge` on a stored run) on a simulated call whose caller is scripted, never on a recorded or a real call.
- Golden sets and the judge's templates are for `mr` only.
- A judge request holds the item's question, that item's expected facts or expected answer, and the agent's answer, and nothing else.
- The agent's answer is untrusted data: it is JSON-encoded inside fixed fences, any fence sequence in it is replaced, and it is never an instruction to the model.
- A reply that is not exactly one valid JSON object is never repaired; it gets one regeneration, and a second invalid reply makes the item an error.
- The expected-outcome check stays inert and every output says "outcome not evaluated"; no run verdict is produced.

## Not claimed

This page, the judge lines of the commands, the judge section of the report and the agreement report hold one sentence that names what is not claimed. It occurs once, here, in the block below, and the claim words it names appear nowhere else.

```text
Tracer's judge is an uncalibrated language-model signal: it reports whether a model said that an agent's answer agrees with the expected facts; it does not measure accuracy, correctness, factuality or quality, it is not a pass or fail of the agent, and none of its numbers is a confidence, a probability, an error rate, a recall or a precision.
```

- A verdict is what a model said about one answer; it is not a verdict on the agent.
- The fake judge (`fake:`) reads nothing: it answers the same for every item, whatever the agent said.
- No comparison with human labels is reported on this page, and none is implied by the counts it describes.

## Golden sets

- A golden set is a file in the closed format `tracer.golden_set/1`: a key, the language `mr` and a list of items.
- It is read as strict JSON with closed keys: a repeated key or an unknown key is refused.
- The caps are at most 100 items, 10 expected facts per item, 300 characters per fact and 1,000 characters per expected answer; text is in NFC.
- An item has a match mode: `all_facts` (a list of expected facts, each of which the answer must state) or `equivalent_answer` (one expected answer whose meaning the answer must share).
- An item names a scripted turn, and its question must equal the text of that turn byte for byte, so editing a scripted line means editing the item in the same change.
- A golden set is refused with a language-model caller (`--caller` other than `scripted`), because the question text would not be fixed.
- An item on a turn with a scheduled barge-in is refused, because the caller cuts the agent's answer by design.
- Every refusal happens at step 1 of the order of operations, before any key is read (exit code 4, or 64 for a mistyped option).
- The synthetic set `packages/evalcore/tests/fixtures/golden_mr.json` is for the apparatus only: its items and the planted replies that go with it are invented.
- No native review of that set has happened (BL-169); each item has `needs_native_review` set to true, and a real golden set is the owner's (O-J6).

## The answer window

- The window of an item opens at the end of the caller turn that holds the question.
- It closes at the start of the next caller line that was sent, or at the end of the call for the last line.
- It holds the `our_stt` agent turns that start inside it, in order, joined by one line feed.
- It is never built from the agent's own export and never from the script.
- A turn that starts before the window opens is excluded and counted.
- An empty window, or a question that was never sent, gives the error `answer_window_empty` or `question_not_sent`, and it is never `disagrees`.

## Provider profiles

- The judge speaks to a provider only through one profile of a closed set: `anthropic_compat/1`, `gemini_compat/1`, `xai/1`, `openrouter/1`, `sarvam_v2/1`, `openai/1` and `local_loopback/1`.
- All seven profiles are `unverified` in the provider dossier (`docs/security/P4-J-providers.md`), which gives a source and a retrieval date for each fact it has read.
- A fact the dossier marks "not verified" stays not verified here, and this page repeats no provider fact.
- An unverified profile is refused for a real run until its first request, made by the owner under `--judge-first-request-gate`, is logged.
- The terms and data-use page of each provider is not fetched; it is an owner gate (O-J2), never an assumption.
- `anthropic_compat/1` never sends `response_format` or `seed`.
- `openrouter/1` always names one provider slug without fallback and sends `data_collection: "deny"`, which is a request, not a record of what any provider does with the data.
- A Sarvam judge needs its own owner decision, because the terms (clause 10.5(a), as read on 2026-10-27) forbid using Sarvam output to test any artificial intelligence system (BL-188).
- `local_loopback/1` reaches a literal loopback address only and is never a real provider.
- The engines `fake:SCENARIO` and `replay:DIRECTORY` send nothing and read no key.

## Owner allowlist, key and caps

- The judge allowlist is a file only the owner writes: git-ignored, mode 0600, not a link; an unknown or repeated field refuses it.
- Each entry is an exact `https://host[:port]/path` followed by `profile=` and `key_env=`; there is no userinfo, query or fragment.
- `http` is allowed only for a literal loopback address.
- The client follows no redirects, reads no proxy environment and verifies TLS.
- A DNS answer that is private, loopback or link-local is refused for a public entry.
- The key is read, by the variable name that `key_env` gives, only after the entry matches, and it is bound to that host and never sent elsewhere.
- The key is never written to any output: not an exception, a log at any level, an exchange file, the store, the report, the recovery file, a labels file or the agreement report.
- A real engine requires `--max-judge-requests` and `--max-judge-tokens`, with no default; every attempt counts, and a missing cap is refused at step 4.
- It also requires `max_tokens` for each request in the judge configuration, `tracer.judge_config/1`, in which every key is required and `null` means not sent.
- A regeneration counts as a request against the cap, and when a cap is reached no further request is sent and the remaining items are errors.
- The token cap is checked as the reported usage plus the next request's `max_tokens`, so the prompt tokens of one request can cross it (BL-295).
- `--judge-authorisation-reference` is required for a real provider, as `--authorisation-reference` is required for a call.
- The command prints the reference as `judge authorisation reference: sha256 <hex>`; the hash is printed and is not stored in any record, so keep it in your own record (BL-363).
- The text of the reference is never written, printed or stored.
- A DNS rebinding race between the client's check and the connect is not claimed closed (BL-326).

## Replay and repeats

- Each attempt of a real engine writes an exchange file `<out>/judge/<run>/item-<id>-<attempt>.json`, mode 0600 and git-ignored; it holds the request and the raw reply and stays on your machine.
- `--judge replay:DIRECTORY` answers from those files and reproduces the stored verdicts byte for byte.
- `--judge fake:SCENARIO` takes one of `agrees`, `disagrees`, `cannot_tell`, `invalid_once`, `invalid`, `fenced` or `auth_failure`; the same request gives the same reply and nothing is sent.
- `tracer judge --out DIR --bundle ID --golden FILE --judge-config FILE --judge ENGINE` re-judges a stored run; it needs the golden set for the scripted turn of each item and the configuration for the request settings.
- `--repeats K` takes K from 1 to 100 and defaults to 1.
- Each repeat is stored as one rescoring bundle of the same run, sharing the call's records and adding its own judge results.
- It is refused when the stored run holds no judge definitions.
- It is also refused when the model of the engine differs from the `judge_model_ref` of the definitions.
- The line "judge changed from the stored run: n of N items" measures the judge on fixed answers, not the agent.
- Nothing is claimed about whether a provider gives the same reply twice; a seed is recorded as sent, with no guarantee.

## Labeller guide

A labeller answers the same questions as the judge, without seeing what the judge said, so that the two can be counted side by side.

1. A blind packet is a file of the items a labeller works through: `tracer labels packet --out DIR --bundle ID --golden FILE` writes it to `<out>/labels/`.
2. For each item the packet holds the question, the expected facts, the audio range of the agent's answer and our speech-to-text text, and a labels template.
3. The packet never shows the judge's verdict, reason code or fact statuses, so two judgements of one call give the same packet.
4. A labeller fills in a labels file, `tracer.judge_labels/1`, from the template inside the packet, with a labeller name.
5. Each label has a verdict (`agrees`, `disagrees` or `cannot_tell`) and a basis: `audio`, `stt_text` or `both`, which says what the labeller used.
6. A verdict left `null` is unlabelled.
7. `tracer labels check --out DIR --labels FILE` validates the file and copies it, content-addressed by the SHA-256 of its bytes, under `<out>/labels/` with mode 0600; the same bytes are not stored twice.
8. In the line `labelled: n of N items` of `labels check`, N is the number of labels the file lists, which is not the number of items of the run (BL-350).
9. `tracer labels agreement --out DIR --bundle ID` prints counts only: for each labeller, the judge's row against the human verdict, in "n of N items".
10. An unlabelled item is shown apart and is never counted as agreement or disagreement.
11. There is no percent, no kappa and no interval in the report.
12. At most two labellers are read; with two, the report also counts one labeller against the other.
13. Packets and labels files hold call text and stay out of git; the guard refuses them.

## Reading the output

- Every judge line says that the signal is uncalibrated: the line `judge: uncalibrated signal (golden set KEY, engine PROFILE, model MODEL)` heads the counts.
- The report has a section titled "Uncalibrated judge signal", and the outcome label "Outcome not evaluated" is kept.
- Text from the call is shown escaped only (ADR 0017).
- The expected facts and the expected answers are never printed or put on the report page.
- The judge usage line gives requests and tokens, or none.
- The cost of judge requests is `not_available` without a USD price catalog.
- Credits that a provider reports are never stored as a cost.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the command ended and every judge result is a verdict or not applicable |
| 1 | an unexpected error |
| 2 | at least one judge result is an error |
| 3 | speech-to-text gave no transcript |
| 4 | an input was refused before anything was sent |
| 5 | the call was not established |
| 64 | a usage error |

- A judge result of status `error` gives 2, and so does a `cannot_tell` verdict, which is stored as the error `judge_undetermined`.
- A verdict of `disagrees` is a result and gives 0.
- A refusal before any key is read or any request is sent gives 4, with one line `error: <code>` on the error stream.
- When several apply, the first of 1, 5, 3, 2, 0 wins.

## Offline quick start

- The offline quick start with `--judge fake:agrees` is in `packages/cli/README.md`, with the invented inputs of `docs/examples/simulated-mr`; the test `test_judge_readme_quick_start_runs_offline` runs it as written.
- It is plumbing: the fake judge reads nothing and the inputs are invented.
- The page is checked by `test_judge_concept_page_states_no_claims`.

## Known limits

- No native speaker has done a review of the golden set or of the wording of the reason codes (BL-169).
- A turn of the agent channel in any other language makes every item `not_applicable` with `unsupported_language`, because the 0.0.1 schema checks every turn of the transcript (BL-193).
- No measurement of the judge against human labels is reported here; any such count comes from the owner's labels.
- The owner's decisions O-J1 to O-J6 come before the first real judge request, and this page records no real result.
- Real-caller data never goes to a judge without a new owner decision (BL-266).
- The judge does not run on recorded calls or on flagged spans (BL-174, BL-269).

## Related

- [ADR 0023: recorded-call agreement scoring](../adr/0023-recorded-call-agreement-scoring.md) (amendment A4: the claim words for judge outputs)
- [ADR 0024: provider adapters](../adr/0024-provider-adapters-byok-sarvam-rest.md) (the key, the call log, the run book)
- [ADR 0030: LLM judge as an uncalibrated signal](../adr/0030-llm-judge-uncalibrated-signal.md)
- [P4-J provider dossier](../security/P4-J-providers.md): one row per provider fact, with its source or "not verified"
- [packages/cli/README.md](../../packages/cli/README.md): the commands and the offline quick start
- [docs/examples/simulated-mr](../examples/simulated-mr/README.md): the invented inputs
- [simulated-calls.md](simulated-calls.md): the call that the judge reads
