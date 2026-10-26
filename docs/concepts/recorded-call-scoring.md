# Recorded-call scoring

`tracer score` compares the agent's transcript export of a recorded call with a speech-to-text transcript of the same recording, and points a reviewer at the spans where the two texts disagree, so that the reviewer reads less. This page says what the numbers mean, what they do not mean, and how the command treats call data. The decisions behind it are ADR 0023 (the scoring), ADR 0024 (the provider adapter, the key and the run book) and ADR 0025 (the Marathi comparison normaliser); the links are under "Related".

## What is measured

- Tracer measures the agreement between the agent's transcript export of a call and an independent speech-to-text transcript of the same audio, made by a second engine (Sarvam) or replayed offline from recorded responses.
- Both texts are cut into words by the comparison normaliser `mr-Deva/1` and aligned word by word, each as one whole-call sequence, which gives four counts: H (words that match), S (substitutions), D (export words with no speech-to-text word) and I (speech-to-text words with no export word).
- The headline value is labelled agreement distance (MER): it is the measure of Morris, Maier and Green (2004), the sum S + D + I over the sum H + S + D + I.
- The agreement distance is 0 when the two texts agree word for word, it is never above 1, and it does not change when the two texts swap places.
- Every number is printed with its counts and its denominator: the agreement distance with H, S, D and I, the flagged share of export tokens as a numerator and a denominator, and the export turns that hold a flag out of all export turns.
- A span is a run of neighbouring words where the two texts disagree; it is placed on the export text, and it never crosses a turn boundary.
- The confidence of a span is the character edit distance between the two span keys divided by the length of the longer key, a number from 0 to 1.
- The confidence is uncalibrated: no labelled data has tuned it (BL-184).
- The confidence is not a probability, and Tracer never presents it as one.
- A span is flagged when its confidence is at or above `flag_min_confidence`, a value of the rubric file that has no default: Tracer refuses to run until the file states it.
- For the base case the owner pre-registered `flag_min_confidence` = 0.5 before any real result was seen, and every span, flagged or not, is recorded, so the effect of another threshold can be read later.
- The shipped definitions carry no pass rule; the only pass rule is the review budget that the rubric file states for the flagged share, and the base-case file sets the labelled placeholder 1.0 (no limit), so that rule cannot fail and is not a judgement of the agent.
- Runs are compared by their parameters (threshold, normaliser id, speech-to-text provider and model, scorer version), never by their snapshot digests, because every run gets fresh ids (BL-217).

## Not claimed

- A flag is not a claim that the export holds an error: it says that two machine outputs differ there, and the speech-to-text side may be the wrong one.
- An unflagged span, or a stretch of text with no span at all, is not shown to be right: the two texts agree there, or differ by less than the threshold.
- The export is not a reference or ground truth, and neither is the speech-to-text transcript.
- Two clips (the two calls of the base case) are a demonstration, not a validation (BL-170): their numbers are reported per call with their counts, no threshold is tuned on them, and they support no general claim.
- The agreement scale is the owner's product principle (BL-165): the export is the agent's output under review, never the truth it is measured against.

```text
Tracer reports how far two transcripts agree; it does not measure accuracy, and none of its numbers is a WER, a CER, an error rate, a recall or a precision.
```

## Scope, roles and tier

- Only Marathi (language code mr, Devanagari script) is in scope in this release; any other `--declared-language` is refused before any request (`language_not_supported`, exit 4).
- The language of the export is declared by you with `--declared-language`, because the export carries no language label; Tracer never infers it.
- The speech-to-text request asks the engine to observe the language of each chunk; a chunk observed as another language, or with no observed language, makes the three results `error` and the run is not scored (exit 2), with no value shown (pinned by `test_e2e_nonmr_chunk_is_not_scored_exit_2_with_three_errors`).
- The caller is never evaluated: what is judged is the agent's text.
- The caller lines of the export are the agent's own recognition of the caller's speech, so they are agent output and are compared like the agent's lines.
- The speech-to-text turns have role unknown, because the recording is mono and Tracer does no speaker separation (BL-166).
- The report labels each flag with the role of its export turn, agent or caller.
- A recorded run is at tier T0: the export and the audio are its only evidence, with no agent telemetry.
- The speaker roles are `caller`, `agent` and `unknown`; there is no IVR and no hand-off to a human.

## Cost

- Without a USD price file (`--price-file`) the cost of each speech-to-text request is `not_available` with the reason `no_price_for_model`, and the cost summary has no total.
- A missing cost is never 0: the command prints "cost: not available" with its reason instead.
- Sarvam states its price in INR only; Tracer never converts INR to USD and never derives a USD price.
- The usage of each answered chunk (the audio seconds sent and one request) is recorded whether or not a price is known.
- A replay with `--stt fake:DIR` makes no usage record, so its cost line reads "not available (no_usage)".

## Privacy and consent

- The audio leaves your machine only with `--stt sarvam`, and then only to Sarvam, at a fixed endpoint with TLS verified.
- With `--stt fake:DIR` the replay sends nothing and reads no key: it replays recorded responses from files named `chunk-NNNN.json`.
- The key `SARVAM_API_KEY` is read at run time from the environment or from `--env-file`, never from the command line, and it is never stored, printed or logged.
- The report and the store hold call text (both transcripts), so they must stay out of git.
- The call log holds no text: it holds counts, ids and timings only (chunk index, attempt, bytes sent, audio seconds, status, elapsed time and request id).
- Put `--out` outside the repository; the repository's ignore rules and its guard are a second line of defence, not the first.
- When the command creates `--out`, the leaf directory is 0700 and any parent directories it creates follow the umask; an existing `--out` that group or other users can reach earns one warning on stderr (its mode only), and the run goes on without changing the mode. The store and the call log are created with mode 0600.
- The consent record is given with `--consent`, `--consent-reference` and `--consent-recorded-at`, and Tracer stores the statement as given on the privacy marker of the audio and of both transcripts, and never prints, reports, logs or sends the reference.
- Tracer checks that a consent statement was given, not that it is true; for the base case the statement is the owner's, recorded on 2026-10-25 (BL-213).
- `--stt sarvam` is refused unless `--consent obtained`; a replay is never gated.
- A link in place of `tracer.sqlite` in `--out` is refused before any request (`store_file_is_a_link`, exit 4).
- Retention is recorded and not enforced: the `--retention` value is stored on the records, but nothing deletes the store, the report or the call log when the date passes, so you delete `--out` by hand (BL-234).
- Embedding the audio in the report with `--embed-audio` is opt-in: without it the report refers to the recording by a relative path, and with it the report file itself holds the call audio and must not be shared.
- A machine that needs an HTTP proxy or extra CA certificates cannot make a real send in this release, because the client reads no proxy or certificate setting from the environment (BL-237).

## Run book

- Before a real send, follow the Run book for a real send in ADR 0024; each step there is a precondition, and a failed one stops the send.
- Do a dry run first, with `--dry-run` and exactly the arguments of the real send: it reads no key, sends nothing, and prints the chunk plan, the audio seconds, the number of calls and the `max chunks:` line.
- The `--max-chunks` ceiling, required with `--stt sarvam`, refuses a plan with more chunks than you allowed; it never truncates a plan, and it counts chunks, not requests, since a chunk can take up to 4 attempts (BL-239).
- Send the clip with the fewest chunks first, alone; afterwards record counts only, never text, and reconcile the call log with the provider's dashboard (BL-228).
- For the offline quick start on the synthetic pair, which needs no key and no network, see [packages/cli/README.md](../../packages/cli/README.md).

## Known limits

- Number words against digits are not folded: a number spoken as a word in one text and written in digits in the other gets a high confidence and can be flagged (BL-192).
- The confidence is relative to the length of the span, so one differing letter in a two-letter word reaches the threshold of 0.5 (1 of 2) and is flagged, while the same difference in a three-letter word (1 of 3) is not (BL-243).
- A split word, one that the other text cuts in two with a single space (as a chunk boundary can do), has a distance of 1 over the word length plus one, so it stays below the threshold of 0.5 and is not flagged (BL-244).
- The noise floor of the speech-to-text side (how much a repeat run or a shifted chunking changes the flags) was measured once on two clips, with counts only, in `docs/phase3/measurements/2026-10-26-noise-floor.md`; two clips are a demonstration, not a validation (BL-170).
- No native review of the Marathi normaliser rules, of the synthetic pair or of any result has happened (BL-169, BL-225).
- The two texts share no time base, so the comparison is over the whole call, not turn by turn (BL-167).
- The aligner accepts at most 10,000 tokens per side; with `--stt sarvam` a longer export is refused before the send, and memory and time at the caps are unmeasured (BL-230).
- The speech-to-text side can only be counted after the send: above 10,000 tokens it gives the `error` result `alignment_too_large`, and the run is stored and reported as not scored (exit 2); no command-level test drives this case yet (BL-250).
- The report's audio playback and click-to-seek under `file://` have no automated check; the owner checks them by hand in a browser (BL-224).

## Related

- [Offline quick start, exit codes and a real send](../../packages/cli/README.md)
- [The synthetic Marathi pair](../examples/synthetic-mr/README.md)
- [ADR 0023: recorded-call agreement scoring](../adr/0023-recorded-call-agreement-scoring.md)
- [ADR 0024: provider adapters, BYOK, Sarvam REST and the run book](../adr/0024-provider-adapters-byok-sarvam-rest.md)
- [ADR 0025: the Marathi comparison normaliser](../adr/0025-marathi-comparison-normaliser.md)
- [Tiers and missing data](tiers-and-missing-data.md), [cost](cost.md) and [privacy and consent](privacy-and-consent.md)

The statements of this page are checked by `test_concept_page_states_no_accuracy_claim` and the other tests of `packages/evalcore/tests/test_concept_page_recorded_scoring.py`; the tests check that the page says them, and the review gate checks that they are true.
