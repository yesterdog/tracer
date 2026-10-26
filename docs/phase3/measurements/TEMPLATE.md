# Measurement report: <title>

This is the empty template of the noise-floor measurement of task P3-31; it holds no measured data. To write a report, copy it to `docs/phase3/measurements/<name>.md`, replace every placeholder in angle brackets, set the status to draft or final, and run `test_measurement_report_has_counts_and_denominators`, which checks every file in this directory. A report holds counts only: it never quotes text of a call, never names a call file, and names the two clips by id only.

## Scope and status

Status: template
Date: <date>
Task: P3-31

The runs: R1 is the first real run of each clip; R2 repeats R1 with the same inputs; R3 shifts the chunk boundaries by half a window; the normaliser comparison scores the stored data of R1 again, offline, with the normaliser on and off. Every value comes from the stored runs and their call logs, never from a recollection.

## Clips

The clips are named by id only: clip-A and clip-B, in the order of the first real send.

| Clip | Export turns | Export tokens | Audio seconds |
|---|---|---|---|
| clip-A | <n> | <n> | <seconds> |
| clip-B | <n> | <n> | <seconds> |

## Configuration

The settings are those of R1; a run that used another value says so in its own row of the tables below and in a line here.

| Setting | Value |
|---|---|
| flag_min_confidence | <value> |
| normaliser | <value> |
| max_chunk_ms | <value> |
| search_ms | <value> |
| stt_model_as_requested | <value> |
| r3_shift_ms | <value> |

## Counts per run

In a cell of the form N of D, N is the numerator and D the denominator, and the decimal in brackets is N divided by D. The agreement distance (MER) is S + D + I of H + S + D + I; the flagged export tokens are counted over the export tokens of the clip, and the export turns with a flag over the export turns of the clip. Chunks answered counts the chunks that got a successful response (an empty transcript included), over the chunks of the plan; attempts counts every request the call log records, retries included.

| Run | Clip | Chunks answered (n of d) | Attempts | Export tokens | STT tokens | H | S | D | I | Agreement distance (MER) (n of d) | Flags | Flagged export tokens (n of d) | Export turns with a flag (n of d) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | clip-A | <n> of <d> | <n> | <n> | <n> | <n> | <n> | <n> | <n> | <n> of <d> (<ratio>) | <n> | <n> of <d> (<ratio>) | <n> of <d> (<ratio>) |
| R2 | clip-A | <n> of <d> | <n> | <n> | <n> | <n> | <n> | <n> | <n> | <n> of <d> (<ratio>) | <n> | <n> of <d> (<ratio>) | <n> of <d> (<ratio>) |
| R3 | clip-A | <n> of <d> | <n> | <n> | <n> | <n> | <n> | <n> | <n> | <n> of <d> (<ratio>) | <n> | <n> of <d> (<ratio>) | <n> of <d> (<ratio>) |
| R1 | clip-B | <n> of <d> | <n> | <n> | <n> | <n> | <n> | <n> | <n> | <n> of <d> (<ratio>) | <n> | <n> of <d> (<ratio>) | <n> of <d> (<ratio>) |
| R2 | clip-B | <n> of <d> | <n> | <n> | <n> | <n> | <n> | <n> | <n> | <n> of <d> (<ratio>) | <n> | <n> of <d> (<ratio>) | <n> of <d> (<ratio>) |
| R3 | clip-B | <n> of <d> | <n> | <n> | <n> | <n> | <n> | <n> | <n> | <n> of <d> (<ratio>) | <n> | <n> of <d> (<ratio>) | <n> of <d> (<ratio>) |

## Usage and cost

The usage comes from the usage records of each run and is reconciled with its call log. The cost is not_available with its reason when no USD price was given, and it is never written as 0. A cell is either not_available with its reason in brackets or a positive amount followed by USD.

| Run | Clip | Requests | Audio seconds | Cost |
|---|---|---|---|---|
| R1 | clip-A | <n> | <seconds> | <cost> |
| R2 | clip-A | <n> | <seconds> | <cost> |
| R3 | clip-A | <n> | <seconds> | <cost> |
| R1 | clip-B | <n> | <seconds> | <cost> |
| R2 | clip-B | <n> | <seconds> | <cost> |
| R3 | clip-B | <n> | <seconds> | <cost> |

## Flags per run

The span ids are the ids the report gives the flags (E and two or more digits), or none. They identify a span within one run only.

| Run | Clip | Flags | Flag span ids |
|---|---|---|---|
| R1 | clip-A | <n> | <ids> |
| R2 | clip-A | <n> | <ids> |
| R3 | clip-A | <n> | <ids> |
| R1 | clip-B | <n> | <ids> |
| R2 | clip-B | <n> | <ids> |
| R3 | clip-B | <n> | <ids> |

## Run-to-run difference

The Jaccard index is the number of export tokens flagged in both runs over the number flagged in either run, written as both of either.
R2 repeats R1 with the same inputs, so its row shows how stable the speech-to-text side is from one run to the next, and R3 shifts the chunk boundaries by half a window, so its row shows how much the chunking moves the flags.
The normaliser row compares the stored data of R1 scored with the normaliser on and off, offline, with nothing sent.
When both runs flag no token, either is 0 and the Jaccard cell is 0 of 0 with no decimal.

| Comparison | Clip | Flagged tokens in both | Flagged tokens in either | Jaccard (n of d) |
|---|---|---|---|---|
| R1 vs R2 | clip-A | <n> | <n> | <n> of <d> (<ratio>) |
| R1 vs R3 | clip-A | <n> | <n> | <n> of <d> (<ratio>) |
| R1 normaliser on vs off | clip-A | <n> | <n> | <n> of <d> (<ratio>) |
| R1 vs R2 | clip-B | <n> | <n> | <n> of <d> (<ratio>) |
| R1 vs R3 | clip-B | <n> | <n> | <n> of <d> (<ratio>) |
| R1 normaliser on vs off | clip-B | <n> | <n> | <n> of <d> (<ratio>) |

## What was NOT measured

- No native speaker has reviewed any flag, any speech-to-text output or the normaliser rules (BL-169).
- Two clips are a demonstration, not a validation: no number here supports a general claim, and no threshold was tuned on them (BL-170).
- No other speech-to-text engine, model or language was run, and the flag threshold was not varied.
- Whether a flag points at a real difference in what was said: that needs a person who listens to the recording.

## Not claimed

A flag is not a claim that the export holds an error, and an unflagged span is not shown to be right.

```text
Tracer reports how far two transcripts agree; it does not measure accuracy, and none of its numbers is a WER, a CER, an error rate, a recall or a precision.
```
