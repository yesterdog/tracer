# Measurement report: noise floor of the mr base case (P3-31)

This is the noise-floor measurement of task P3-31, written from the template `docs/phase3/measurements/TEMPLATE.md`. It holds counts only: it never quotes text of a call, never names a call file, and names the two clips by id only.

## Scope and status

Status: final
Date: 2026-10-26
Task: P3-31

The runs: R1 is the first real run of each clip; R2 repeats R1 with the same inputs; R3 uses a different cut grid, max_chunk_ms 20000 instead of 25000, with search_ms unchanged, because the CLI has no option to shift the chunk boundaries by half a window. R3 is therefore a named deviation from the wording of the scope (SCOPE P3-31, BL-253). The normaliser comparison scores the stored data of R1 again, offline, with the normaliser on and off; the off side is an offline recomputation with a measurement-only identity key registered inside the measurement process, not a product normaliser and not in the product registry (ADR 0025).

R3 changes two things at once. Its windows are 20 s long instead of 25 s, so the speech-to-text side hears a different amount of context in each chunk, and its cuts fall in other places. The cuts moved almost everywhere: on clip-A, 0 of the 10 cuts of R1 coincide with one of the 14 cuts of R3; on clip-B, 2 of the 19 cuts of R1 coincide with one of the 24 cuts of R3. R3 is therefore a perturbation of the whole chunking, not an isolated shift of the boundaries, and its row cannot separate the effect of the cut positions from the effect of the window length.

clip-A was additionally run twice more with the same inputs as R1. All four clip-A runs with the same inputs (R1, R2 and the two extra runs) gave identical summaries and byte-identical tracer.flags/1 documents. The extra runs have no rows below. The two extra runs of clip-A and R2 of clip-B reused the chunk plan of R1 without a dry run of their own; the R3 runs had their own dry runs (BL-261).

Provenance: every cell of this report was printed either by the runs themselves (the command summaries and the call-log summaries printed at run time) or by the measurement script `scripts/measurements/noise_floor.py` (sha256 d0be451eaa8e2976a669a9da880e446a7601d42e0dd76838ef2dd626643422b1, committed in the same change as this report), which opens each store read-only, prints counts only and stops when its recomputation with the normaliser on differs from the stored flags document or when its chunk-edge mark differs from the stored one. The lead re-ran the script on the stores, its output matched the first output, and every cell was checked against those outputs. All eight runs ended with exit 0 and an empty stderr, with zero ingest warnings, and every request was answered on its first attempt (no retry).

## Clips

The clips are named by id only: clip-A and clip-B, in the order of the first real send.

| Clip | Export turns | Export tokens | Audio seconds |
|---|---|---|---|
| clip-A | 18 | 213 | 254.560166666666666667 |
| clip-B | 18 | 378 | 437.2025 |

## Configuration

The settings are those of R1; a run that used another value says so in its own row of the tables below and in a line here. R3 of both clips used max_chunk_ms 20000 (15 chunks for clip-A, 25 chunks for clip-B) and no shift, so the r3_shift_ms row says not_applicable.

| Setting | Value |
|---|---|
| flag_min_confidence | 0.5 |
| normaliser | mr-Deva/1 |
| max_chunk_ms | 25000 |
| search_ms | 5000 |
| stt_model_as_requested | saaras:v4 |
| r3_shift_ms | not_applicable (R3 used max_chunk_ms 20000; the CLI has no shift option) |

## Counts per run

In a cell of the form N of D, N is the numerator and D the denominator, and the decimal in brackets is N divided by D. The agreement distance (MER) is S + D + I of H + S + D + I; the flagged export tokens are counted over the export tokens of the clip, and the export turns with a flag over the export turns of the clip. Chunks answered counts the chunks that got a successful response (an empty transcript included), over the chunks of the plan; attempts counts every request the call log records, retries included.

| Run | Clip | Chunks answered (n of d) | Attempts | Export tokens | STT tokens | H | S | D | I | Agreement distance (MER) (n of d) | Flags | Flagged export tokens (n of d) | Export turns with a flag (n of d) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | clip-A | 11 of 11 | 11 | 213 | 253 | 193 | 19 | 1 | 41 | 61 of 254 (0.24015748031496062) | 10 | 9 of 213 (0.04225352112676056) | 9 of 18 (0.5) |
| R2 | clip-A | 11 of 11 | 11 | 213 | 253 | 193 | 19 | 1 | 41 | 61 of 254 (0.24015748031496062) | 10 | 9 of 213 (0.04225352112676056) | 9 of 18 (0.5) |
| R3 | clip-A | 15 of 15 | 15 | 213 | 253 | 193 | 19 | 1 | 41 | 61 of 254 (0.24015748031496062) | 9 | 8 of 213 (0.03755868544600939) | 8 of 18 (0.4444444444444444) |
| R1 | clip-B | 20 of 20 | 20 | 378 | 452 | 340 | 31 | 7 | 81 | 119 of 459 (0.25925925925925924) | 23 | 18 of 378 (0.047619047619047616) | 13 of 18 (0.7222222222222222) |
| R2 | clip-B | 20 of 20 | 20 | 378 | 452 | 340 | 31 | 7 | 81 | 119 of 459 (0.25925925925925924) | 23 | 18 of 378 (0.047619047619047616) | 13 of 18 (0.7222222222222222) |
| R3 | clip-B | 25 of 25 | 25 | 378 | 446 | 334 | 33 | 11 | 79 | 123 of 457 (0.26914660831509846) | 24 | 26 of 378 (0.06878306878306878) | 13 of 18 (0.7222222222222222) |

## Usage and cost

The usage comes from the usage records of each run and is reconciled with its call log: every call log holds one line before and one line after each request (for example 15 and 15 for R3 of clip-A, 25 and 25 for R3 of clip-B), and every request was answered on its first attempt. The cost is not_available (no_price_for_model) for every run, because no USD price was given, and it is never written as 0. A cell is either not_available with its reason in brackets or a positive amount followed by USD.

| Run | Clip | Requests | Audio seconds | Cost |
|---|---|---|---|---|
| R1 | clip-A | 11 | 254.560166666666666667 | not_available (no_price_for_model) |
| R2 | clip-A | 11 | 254.560166666666666667 | not_available (no_price_for_model) |
| R3 | clip-A | 15 | 254.560166666666666667 | not_available (no_price_for_model) |
| R1 | clip-B | 20 | 437.2025 | not_available (no_price_for_model) |
| R2 | clip-B | 20 | 437.2025 | not_available (no_price_for_model) |
| R3 | clip-B | 25 | 437.2025 | not_available (no_price_for_model) |

## Flags per run

The span ids are the ids the report gives the flags (E and two or more digits), or none. They identify a span within one run only. Each id is E followed by the two-digit index of the span's row among the data spans of that run's report, so E03 is the span with index 3.

| Run | Clip | Flags | Flag span ids |
|---|---|---|---|
| R1 | clip-A | 10 | E00, E03, E06, E07, E09, E10, E11, E13, E15, E16 |
| R2 | clip-A | 10 | E00, E03, E06, E07, E09, E10, E11, E13, E15, E16 |
| R3 | clip-A | 9 | E00, E03, E07, E09, E10, E11, E13, E15, E16 |
| R1 | clip-B | 23 | E00, E02, E03, E04, E06, E07, E08, E09, E10, E13, E14, E16, E18, E19, E20, E21, E22, E24, E25, E26, E31, E34, E35 |
| R2 | clip-B | 23 | E00, E02, E03, E04, E06, E07, E08, E09, E10, E13, E14, E16, E18, E19, E20, E21, E22, E24, E25, E26, E31, E34, E35 |
| R3 | clip-B | 24 | E00, E02, E03, E04, E05, E06, E07, E09, E10, E11, E12, E16, E18, E19, E20, E21, E22, E24, E25, E26, E27, E31, E34, E35 |

Some flags are zero-width: they mark speech-to-text tokens with no export token opposite them (insertions), so they hold no export token. Zero-width flags: clip-A 3 of 10 in R1 and R2, 3 of 9 in R3; clip-B 11 of 23 in R1 and R2, 10 of 24 in R3.

## Run-to-run difference

The Jaccard index is the number of export tokens flagged in both runs over the number flagged in either run, written as both of either.
R2 repeats R1 with the same inputs, so its row shows how stable the speech-to-text side is from one run to the next; R3 does not shift the chunk boundaries by half a window as the scope planned but cuts the same audio on another grid with shorter windows (max_chunk_ms 20000), so its row shows how much that change of the whole chunking moves the flags.
The normaliser row compares the stored data of R1 scored with the normaliser on and off, offline, with nothing sent.
When both runs flag no token, either is 0 and the Jaccard cell is 0 of 0 with no decimal.
The Jaccard index counts export tokens only, so it cannot see the zero-width (insertion) flags listed under Flags per run.

| Comparison | Clip | Flagged tokens in both | Flagged tokens in either | Jaccard (n of d) |
|---|---|---|---|---|
| R1 vs R2 | clip-A | 9 | 9 | 9 of 9 (1.0) |
| R1 vs R3 | clip-A | 8 | 9 | 8 of 9 (0.8889) |
| R1 normaliser on vs off | clip-A | 9 | 9 | 9 of 9 (1.0) |
| R1 vs R2 | clip-B | 18 | 18 | 18 of 18 (1.0) |
| R1 vs R3 | clip-B | 15 | 29 | 15 of 29 (0.5172) |
| R1 normaliser on vs off | clip-B | 18 | 18 | 18 of 18 (1.0) |

Reading of the numbers (counts and plain statements only):

- Repeats. The speech-to-text side repeated exactly at fixed inputs on these two clips: R2 matches R1 on both clips, and on clip-A four runs with the same inputs gave the same flags document. All repeats ran on one day, and identical repeats cannot tell deterministic decoding from caching on the provider's side (BL-260).
- Another cut grid. On clip-A, R1 and R3 share 8 of the 9 flagged export tokens: one token is flagged in R1 only, and none in R3 only. On clip-B they share 15 of 29: 14 tokens are flagged in only one of the two runs, 3 in R1 only (18 minus 15) and 11 in R3 only (26 minus 15).
- Flags next to a cut. A recording edge (the first or the last token of the whole recording) is not a cut, so this report counts a span as next to a cut only when it holds the first or the last token of a speech-to-text turn and that token is not a recording edge. On that definition, in R1 3 of the 10 flags of clip-A and 9 of the 23 flags of clip-B sit next to a cut (R3: 3 of 9 and 10 of 24). Every run has 2 recording-edge spans, both flagged. In R1, the spans next to a cut are flagged in 3 of 3 cases on clip-A and 9 of 9 on clip-B (12 of 12 together), against 7 of 14 and 14 of 27 among all other spans of the run (21 of 41 together); in R3, 3 of 6 and 10 of 11 against 6 of 11 and 14 of 25. The near_chunk_boundary mark stored with each span also counts the recording edges (ADR 0023 rule 10, BL-256); with it, R1 gives 5 of 5 against 5 of 12 on clip-A and 11 of 11 against 12 of 25 on clip-B. A stricter per-token variant of the definition differs in one cell only: clip-A R3 has 4 flags next to a cut instead of 3. A flag next to a cut is read with the audio (BL-255).
- Insertions. 3 of the 10 flags of clip-A and 11 of the 23 flags of clip-B in R1 are zero-width insertion flags, which the token Jaccard above does not count.
- The normaliser. Switching it off changed nothing on these two clips: the MER counts and the flags are the same with it on and off. The two clips hold no digits, no letter case and no joiners for the normaliser to fold, so this does not show that the normaliser has no effect; it says nothing about the normaliser in general (BL-254).

## What was NOT measured

- No native speaker has reviewed any flag, any speech-to-text output or the normaliser rules (BL-169).
- Two clips are a demonstration, not a validation: no number here supports a general claim, and no threshold was tuned on them (BL-170).
- No other speech-to-text engine, model or language was run, and the flag threshold was not varied: one model only, saaras:v4 as requested.
- No run on other days or at other times, so a change of the service over time is not measured, and deterministic decoding is not told apart from caching on the provider's side (BL-260).
- An exact half-window shift of the chunk boundaries, or a change of the cut positions with the window length held fixed: R3 changed both, on one alternative grid per clip (BL-253).
- The normaliser on content it would change: the two clips hold no digits, case or joiners (BL-254).
- The cost in USD: no price was given, so the cost is not_available.
- The billing of a retry: no retry happened in any of the eight runs.
- Whether a flag points at a real difference in what was said: that needs a person who listens to the recording. The real report of each run was not checked against the recording by a person. The report's audio playback and click-to-seek were checked by the owner in Chrome and Edge only, which says nothing about any flag (BL-224, closed).

## Not claimed

A flag is not a claim that the export holds an error, and an unflagged span is not shown to be right. The flag confidence is not calibrated. The repeats do not show that the speech-to-text side is deterministic in general. The chunking result covers one alternative grid per clip and no more. The normaliser was not shown to have no effect, only to change nothing on these two clips. A count summed over the two clips is a count, not a statement about calls in general, and nothing here says anything about Marathi speech-to-text quality.

```text
Tracer reports how far two transcripts agree; it does not measure accuracy, and none of its numbers is a WER, a CER, an error rate, a recall or a precision.
```
