# Measurement report: noise floor of the mr base case (P3-31)

This is the noise-floor measurement of task P3-31, written from the template `docs/phase3/measurements/TEMPLATE.md`. It holds counts only: it never quotes text of a call, never names a call file, and names the two clips by id only.

## Scope and status

Status: final
Date: 2026-10-26
Task: P3-31

The runs: R1 is the first real run of each clip; R2 repeats R1 with the same inputs; R3 uses a different cut grid, max_chunk_ms 20000 instead of 25000, with search_ms unchanged, because the CLI has no option to shift the chunk boundaries by half a window. R3 is therefore a named deviation from the wording of the scope (SCOPE P3-31, BL-253): it shows how much the flags move when the cuts fall elsewhere, not the effect of an exact half-window shift. The normaliser comparison scores the stored data of R1 again, offline, with the normaliser on and off; the off side is an offline recomputation with a measurement-only identity key registered inside the measurement process, not a product normaliser and not in the product registry (ADR 0025). Two checks were made on it: the recomputation with the normaliser on gives the stored counts again, and a probe with a degenerate key showed that the key is used.

clip-A was additionally run twice more with the same inputs as R1. All four clip-A runs with the same inputs (R1, R2 and the two extra runs) gave identical summaries and byte-identical tracer.flags/1 documents. The extra runs have no rows below.

Every value is copied from the decision log entries the lead recorded from the stored runs and their call logs (2026-10-26, P3-30 FIRST REAL SEND DONE, P3-30/P3-31 RESULTS and P3-31 MEASUREMENTS COMPLETE), never from a recollection. The decision log does not record some cells directly; they are named under Counts per run and Usage and cost and are to be checked against the stores (BL-252). All eight runs ended with exit 0 and an empty stderr, with zero ingest warnings, and every request was answered on its first attempt (no retry).

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

Cells checked against the run-time outputs: the chunks answered and the attempts of R3 of both clips and of R2 of clip-B are the chunk count of the run, each confirmed by that run's call log (every request answered on its first attempt); R2 of clip-B repeats the R1 values and was recorded as identical to R1; H, S, D and I of R3 of clip-A were printed by the run itself (H 193, S 19, D 1, I 41). BL-252 asks for the scripts that recompute them from the stores.

| Run | Clip | Chunks answered (n of d) | Attempts | Export tokens | STT tokens | H | S | D | I | Agreement distance (MER) (n of d) | Flags | Flagged export tokens (n of d) | Export turns with a flag (n of d) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | clip-A | 11 of 11 | 11 | 213 | 253 | 193 | 19 | 1 | 41 | 61 of 254 (0.24015748031496062) | 10 | 9 of 213 (0.04225352112676056) | 9 of 18 (0.5) |
| R2 | clip-A | 11 of 11 | 11 | 213 | 253 | 193 | 19 | 1 | 41 | 61 of 254 (0.24015748031496062) | 10 | 9 of 213 (0.04225352112676056) | 9 of 18 (0.5) |
| R3 | clip-A | 15 of 15 | 15 | 213 | 253 | 193 | 19 | 1 | 41 | 61 of 254 (0.24015748031496062) | 9 | 8 of 213 (0.03755868544600939) | 8 of 18 (0.4444444444444444) |
| R1 | clip-B | 20 of 20 | 20 | 378 | 452 | 340 | 31 | 7 | 81 | 119 of 459 (0.25925925925925924) | 23 | 18 of 378 (0.047619047619047616) | 13 of 18 (0.7222222222222222) |
| R2 | clip-B | 20 of 20 | 20 | 378 | 452 | 340 | 31 | 7 | 81 | 119 of 459 (0.25925925925925924) | 23 | 18 of 378 (0.047619047619047616) | 13 of 18 (0.7222222222222222) |
| R3 | clip-B | 25 of 25 | 25 | 378 | 446 | 334 | 33 | 11 | 79 | 123 of 457 (0.26914660831509846) | 24 | 26 of 378 (0.06878306878306878) | 13 of 18 (0.7222222222222222) |

## Usage and cost

The usage comes from the usage records of each run and is reconciled with its call log: R1 of clip-A logged 22 lines (11 before and 11 after the requests) and R1 of clip-B 40 lines (20 before and 20 after), all answered on the first attempt. For R3 of both clips and R2 of clip-B the requests and audio seconds are those printed by the run and match its call log: 15 requests and 254.560166666666666667 audio seconds for R3 of clip-A, 20 requests and 437.2025 audio seconds for R2 of clip-B, 25 requests and 437.2025 audio seconds for R3 of clip-B. The cost is not_available (no_price_for_model) for every run, because no USD price was given, and it is never written as 0. A cell is either not_available with its reason in brackets or a positive amount followed by USD.

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

## Run-to-run difference

The Jaccard index is the number of export tokens flagged in both runs over the number flagged in either run, written as both of either.
R2 repeats R1 with the same inputs, so its row shows how stable the speech-to-text side is from one run to the next; R3 does not shift the chunk boundaries by half a window as the scope planned but cuts the same audio on another grid (max_chunk_ms 20000), so its row shows how much moving the cuts moves the flags.
The normaliser row compares the stored data of R1 scored with the normaliser on and off, offline, with nothing sent.
When both runs flag no token, either is 0 and the Jaccard cell is 0 of 0 with no decimal.

| Comparison | Clip | Flagged tokens in both | Flagged tokens in either | Jaccard (n of d) |
|---|---|---|---|---|
| R1 vs R2 | clip-A | 9 | 9 | 9 of 9 (1.0) |
| R1 vs R3 | clip-A | 8 | 9 | 8 of 9 (0.8889) |
| R1 normaliser on vs off | clip-A | 9 | 9 | 9 of 9 (1.0) |
| R1 vs R2 | clip-B | 18 | 18 | 18 of 18 (1.0) |
| R1 vs R3 | clip-B | 15 | 29 | 15 of 29 (0.5172) |
| R1 normaliser on vs off | clip-B | 18 | 18 | 18 of 18 (1.0) |

Reading of the numbers (counts and plain statements only). The speech-to-text side repeated exactly at fixed inputs on these two clips: R2 matches R1 on both clips, and on clip-A four runs with the same inputs gave the same flags document. Moving the cuts changed the flagged set little on clip-A (8 of the 9 tokens flagged in either run are flagged in both) and a lot on clip-B (15 of 29). About half of the flags sit next to a chunk cut: in R1, clip-A has 5 of 17 spans next to a cut, all 5 flagged (5 of the 10 flags), and clip-B has 11 of 36 spans next to a cut, all 11 flagged (11 of the 23 flags); a flag next to a cut is read with the audio (BL-255). The normaliser switch changed nothing on these two clips: the MER counts and the flags are the same with it on and off. The two clips hold no digits, no letter case and no joiners for the normaliser to fold, so this says nothing about the normaliser in general (BL-254).

## What was NOT measured

- No native speaker has reviewed any flag, any speech-to-text output or the normaliser rules (BL-169).
- Two clips are a demonstration, not a validation: no number here supports a general claim, and no threshold was tuned on them (BL-170).
- No other speech-to-text engine, model or language was run, and the flag threshold was not varied: one model only, saaras:v4 as requested.
- No run on other days or at other times, so a change of the service over time is not measured.
- An exact half-window shift of the chunk boundaries: R3 used a different cut grid (BL-253).
- The normaliser on content it would change: the two clips hold no digits, case or joiners (BL-254).
- The cost in USD: no price was given, so the cost is not_available.
- The billing of a retry: no retry happened in any of the eight runs.
- Whether a flag points at a real difference in what was said: that needs a person who listens to the recording. The real report of each run was not checked against the recording by a person.

## Not claimed

A flag is not a claim that the export holds an error, and an unflagged span is not shown to be right.

```text
Tracer reports how far two transcripts agree; it does not measure accuracy, and none of its numbers is a WER, a CER, an error rate, a recall or a precision.
```
