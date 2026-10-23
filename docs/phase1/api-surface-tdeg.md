# Tier degradation (T-DEG): the same evaluator at T0, T1 and T2, with the regression matrix (batch 21, P1-19)

Author: qa, 2026-10-24. Task: P1-19 (T-DEG bundles: same evaluator at T0, T1, T2 including verdict counts and both cost domains). Sources of truth: ADR 0004 (Decisions 2, 2a, 3, 4, 5, 5a, 6, 6e, 6g, 7, I1 to I10), ADR 0010 (Decision 3, 3a), ADR 0013 (Decisions 3 to 8), ADR 0014 (Decisions 12 to 16, I22), ADR 0003, `docs/phase1/api-surface.md` (sections 22.1, 22.12, 25.6, B15), `docs/phase1/SCOPE.md` section 5 (T-DEG) and gate items 6 and 18, and the owner's decisions in memory (never assume; missing input is `not_applicable`; missing cost is `not_available`; consume telemetry if present, never break if absent).

New files only. Nothing existing was edited.

| File | Role |
|---|---|
| `packages/evalschema/tests/test_tier_degradation.py` | matrix, keys, schema shape, tool and channel metrics, verdict (SCOPE names); 98 items, 30 test functions |
| `packages/evalschema/tests/test_tier_degradation_cost.py` | both cost domains at every tier; 30 items, 16 functions |
| `packages/evalschema/tests/test_tier_degradation_language.py` | language paths, observed tier (R1, R2), internal export, and the bundle-level applicability check (red by design); 41 items, 22 functions |
| `packages/evalschema/tests/_support_b13.py` | the fixture builder and the reference evaluator (`deg_world`), the hand-written expected table (`EXPECTED`) |
| `docs/phase1/api-surface-tdeg.md` | this file; `test_deg_matrix_doc_matches_expected` keeps section 3 equal to `EXPECTED` |
| `docs/phase1/evidence/red-or-green/batch21-tdeg/` | logs on Python 3.11 to 3.14, the mutant list |

## 1. How the bundles are built

One configuration (13 metric definitions, one rubric, one evaluator, one pricing catalog) and four RunBundles, each a complete document that loads through `RunBundle` with all its checks. The documents are synthetic and shaped like the real captures (a Pipecat stereo recording, OpenTelemetry stage spans, observer turns, a Sarvam-style STT pass); no private fixture, key or network is used.

| Column | Evidence the bundle holds | `available_inputs` | Roles of the turns |
|---|---|---|---|
| `T0` (black box) | a mono recording that mixes both speakers; our STT transcript (`our_stt`, `audio_alignment` timing, 8 turns); no channel map; no tool call, span or agent usage | `audio, transcript, turn_timing` | all `unknown` (nobody separated the speakers) |
| `T0m` (not a tier) | the T0 evidence plus a declared stereo channel map (agent on channel 1), so the agent channel and the agent turns are known | `audio, agent_audio, transcript, agent_transcript, turn_timing` | `caller` / `agent` |
| `T1` (telemetry) | stereo recording with a declared map; telemetry transcript (`agent_telemetry`, `telemetry` timing); 2 tool calls with results; 3 stage spans; agent LLM usage (`telemetry`) | T0m plus `tool_calls, tool_results, spans, usage` | `caller` / `agent` |
| `T2` (Pipecat observer) | as T1, from an observer: `observer` transcript, timing, tool calls, exchanges and interruptions (`Turn.interrupted` known); also TTS characters usage | same as T1 | `caller` / `agent` |

`T0m` shows that availability follows the evidence and not the label (ADR 0004 Decision 2, I15): it is T0 by `observed_tier` (R2) and still scores the agent-audio metric. At T1 and T2 `interrupted` is known; at T0 it is `None`, never `false`.

The reference evaluator is a harness: it decides each result with `results.applicability` (the code under test, called once per turn for per-turn metrics), and scores with recorded constants (a judge answer is a constant, as in a recorded judge response). The expected statuses are **not** derived from the library: they are the hand-written table of section 3 (`EXPECTED`), and the weighted scores of section 4 are recomputed in the tests with exact fractions.

Run origin: every test of the matrix also runs for a human caller (`caller_origin=human`, every payload human-subject) and a simulated caller. At T0 the roles are `unknown`, so the privacy derivation is true even for a simulated caller (ADR 0016 Decision 5, O1); that is conservative and expected.

### The 13 metrics

| Key | Kind | `requires` | `languages` | script | Aggregation | Required | Weight |
|---|---|---|---|---|---|---|---|
| `goal_completion` | llm_judge | transcript | en, hi, mr | no | conversation | yes (the expected-outcome check) | 4 |
| `p95_latency` | latency | turn_timing | language_independent | no | conversation | yes | 5 |
| `tool_call_success` | deterministic | tool_calls, tool_results | language_independent | no | conversation | yes | 3 |
| `stage_ttfb_p95` | latency | spans | language_independent | no | conversation | no | 2 |
| `silence_ratio` | audio | audio | language_independent | no | conversation | no | |
| `agent_audio_clarity` | audio | agent_audio | en, hi, mr | no | conversation | no | 1 |
| `agent_politeness` | llm_judge | agent_transcript | en, hi, mr | no | conversation | yes | 2 |
| `agent_wer` | audio | agent_audio, reference_text | en, hi, mr | yes | conversation | no | |
| `turn_politeness_share` | llm_judge | agent_transcript | en, hi, mr | no | per_turn_share | no | 1 |
| `turn_length_share` | deterministic | agent_transcript | language_independent | no | per_turn_share | no | |
| `agent_input_tokens` | deterministic | usage | language_independent | no | conversation (informational, no pass rule) | no | |
| `agent_turn_count` | deterministic | agent_transcript | language_independent | no | conversation (informational) | no | |
| `script_consistency` | deterministic | transcript | en, hi, mr | yes | conversation | no | |

Total weight 18. Required checks: `goal_completion`, `p95_latency`, `tool_call_success`, `agent_politeness` (four).

## 2. Readings pinned by the tests (qa's readings where the ADRs are silent are marked **R**)

1. A language-independent metric has no language step (ADR 0004 Decision 6b): it is scored with `language_source=None` even for an untagged or foreign transcript.
2. Which turns a metric reads follows its input kind (Decision 6d): `agent_transcript` reads the `agent` turns, `transcript` reads all turns, an audio-only metric reads no turn and takes the language from the transcript's own tag. **R:** the harness passes the run's only transcript; a run with several transcripts needs a rule for which one supplies an input (ambiguity T-A3).
3. A per-turn metric applies the language steps to each turn on its own; a turn that is not applicable for a language reason leaves the denominator (P-26); with no counted turn the result is `not_applicable` with the strongest turn reason and a `per_turn` summary (ADR 0010 Decision 3).
4. `usage` in `available_inputs` means usage of the agent under test (**R**, ambiguity T-A1). The bundle always holds our own evaluation usage; it is not an input of an agent metric.
5. `agent_transcript` is available only where the roles are known (**R**, ambiguity T-A4): at T0 the roles are `unknown`, so the agent-transcript metrics are `missing_input`.
6. The inconclusive share of a tier is `(not_applicable + errored) / required_total` of its verdict (no library function computes it; ambiguity T-A5).

## 3. The regression matrix (metric by column; expected status and reason)

Cell grammar: `scored <value> passed <true|false|none>` (a per-turn metric shows `share <share>`), `NA missing_input [kinds]`, `NA agent_channel_unknown`. The cells are kept equal to `_support_b13.EXPECTED` by `test_deg_matrix_doc_matches_expected`; each cell is a test (`test_deg_matrix_cell_matches_adr_0004`, 52 items).

| Metric | requires | T0 | T0m | T1 | T2 |
|---|---|---|---|---|---|
| `goal_completion` | transcript | scored true passed true | scored true passed true | scored true passed true | scored true passed true |
| `p95_latency` | turn_timing | scored 420 passed true | scored 420 passed true | scored 420 passed true | scored 420 passed true |
| `tool_call_success` | tool_calls, tool_results | NA missing_input [tool_calls, tool_results] | NA missing_input [tool_calls, tool_results] | scored true passed true | scored true passed true |
| `stage_ttfb_p95` | spans | NA missing_input [spans] | NA missing_input [spans] | scored 480 passed true | scored 480 passed true |
| `silence_ratio` | audio | scored 0.12 passed true | scored 0.12 passed true | scored 0.12 passed true | scored 0.12 passed true |
| `agent_audio_clarity` | agent_audio | NA agent_channel_unknown | scored 0.74 passed false | scored 0.74 passed false | scored 0.74 passed false |
| `agent_politeness` | agent_transcript | NA missing_input [agent_transcript] | scored true passed true | scored true passed true | scored true passed true |
| `agent_wer` | agent_audio, reference_text | NA missing_input [agent_audio, reference_text] | NA missing_input [reference_text] | NA missing_input [reference_text] | NA missing_input [reference_text] |
| `turn_politeness_share` | agent_transcript | NA missing_input [agent_transcript] | scored share 0.75 passed false | scored share 0.75 passed false | scored share 0.75 passed false |
| `turn_length_share` | agent_transcript | NA missing_input [agent_transcript] | scored share 1 passed true | scored share 1 passed true | scored share 1 passed true |
| `agent_input_tokens` | usage | NA missing_input [usage] | NA missing_input [usage] | scored 500 passed none | scored 640 passed none |
| `agent_turn_count` | agent_transcript | NA missing_input [agent_transcript] | scored 4 passed none | scored 4 passed none | scored 4 passed none |
| `script_consistency` | transcript | scored true passed true | scored true passed true | scored true passed true | scored true passed true |

Reading the matrix:

- T0 has nine not applicable cells, T0m four, T1 and T2 one each (`agent_wer`: no reference text at any tier).
- `agent_audio_clarity` at T0 is the pinned case `agent_channel_unknown` (ADR 0004 Decision 5a, first example): the recording exists, only `agent_audio` is missing, nobody said which channel is the agent. `agent_wer` at T0 is the fourth example: `agent_audio` and `reference_text` are both listed, the channel question is not hidden and the reference is not hidden behind it.
- A not applicable cell never has a value, a pass decision or a zero. The scored values are the recorded constants; `agent_input_tokens` follows the usage of the bundle (500 at T1, 640 at T2).
- T1 and T2 have the same statuses. Nothing in `InputKind` separates observer-grade timing and interruptions from audio-aligned or telemetry timing, so applicability cannot tell T2 from T1 (ambiguity T-A2); the tiers differ only in the provenance fields that R2 reads.

## 4. Verdict per column

| Column | Required checks (m01, m02, m03, m07) | `passed/failed/NA/errored` | Headline | Status | Inconclusive share | Weighted score | `weight_coverage` | Rendered | Excluded weighted checks |
|---|---|---|---|---|---|---|---|---|---|
| T0 | pass, pass, NA, NA | 2/0/2/0 | 2/4 required checks passed | inconclusive | 0.50 | `1` | `0.5` | weighted score 1.00 (weight coverage 0.50) | m03, m04, m06, m07, m09 |
| T0m | pass, pass, NA, pass | 3/0/1/0 | 3/4 required checks passed | inconclusive | 0.25 | `0.846153846153846154` | `0.722222222222222222` | weighted score 0.85 (weight coverage 0.72) | m03, m04 |
| T1 | pass, pass, pass, pass | 4/0/0/0 | 4/4 required checks passed | pass | 0 | `0.888888888888888889` | `1` | weighted score 0.89 (weight coverage 1.00) | none |
| T2 | pass, pass, pass, pass | 4/0/0/0 | 4/4 required checks passed | pass | 0 | `0.888888888888888889` | `1` | weighted score 0.89 (weight coverage 1.00) | none |

- The T0 score of `1` is over 9 of 18 weight; a not applicable weighted check is never a 0 (it would give 0.5). The score is never shown without its coverage.
- T0m: `agent_audio_clarity` (weight 1) and `turn_politeness_share` (weight 1, share 0.75 against a pass rule of 0.80) fail, so 11 of 13 scored weight passed.
- A T0 recording whose STT pass produced nothing (`audio` only) cannot run any required check: 0/4, inconclusive, no weighted score and no coverage (never a 0 standing for nothing measured).
- A failed required check wins over not applicable ones: `fail` (ADR 0013 Decision 6), tested with a T0 latency failure.

## 5. Both cost domains

| Domain | T0 | T0 with a reported cost in INR | T1 | T2 |
|---|---|---|---|---|
| `agent_under_test` | `not_available`, `usage_not_reported`, no amount, basis, snapshot or usage record; summary total absent, completeness `none` | usage with no model (`provider_reported`): `no_price_for_model`; reported `12.50 INR`: `unsupported_currency`, `reported_raw` keeps `12.50` and `INR` verbatim; total absent | computed from telemetry usage: 500 in + 120 out tokens = `0.000147` USD, with the pricing snapshot (`gpt41-in`, `gpt41-out`); complete | LLM usage computed `0.000186` USD (640 + 150); TTS characters have no price: `no_price_for_model`, no partial sum; summary total `0.000186`, completeness `partial` |
| `evaluation` (simulated caller) | computed and reported, total `1.65` USD, complete | same | same | same |
| `evaluation` (human caller) | total `1.5` USD (no simulated caller), complete | same | same | same |

- Evaluation cost: a reported judge cost (1.5 USD) plus a computed estimate of the same call (`0.00027`) that is excluded as `duplicate_of_reported` covered by the reported record, plus the simulated caller's 1M input tokens computed at `0.15`. The total is `1.65`, not `1.65027`; no usage record is priced twice among the included records. A forged summary that includes both is rejected.
- The two domains have their own records and summaries; the evaluation total is the same at T0 (no agent cost) and T1 (an agent cost), and a summary that names a record of the other domain is rejected.
- A `not_available` cost never has an amount, basis, reporter, snapshot or rounding; a summary with nothing included has no total; a forged `0` amount or `0` total is rejected.
- Agent usage with `source=measured` is rejected (P-39); the length of the recording is never the agent's usage.

## 6. Language paths (T1 unless stated)

| Bundle | `goal_completion` | `agent_audio_clarity` | `turn_politeness_share` | `script_consistency` (script sensitive) | `p95_latency`, `turn_length_share` |
|---|---|---|---|---|---|
| tagged `hi-IN` (the matrix) | scored, observed | scored, observed | scored, observed | scored, observed | scored, no source |
| untagged | NA `language_not_observed` | NA `language_not_observed` | NA `language_not_observed` (4 of 4 turns NA, no share) | NA `language_not_observed` | scored |
| untagged, declared `mr-IN` (T1, T2 only) | scored, `declared`, transcript listed | scored, `declared` | scored, `declared` | NA `language_not_observed`, no source | scored |
| tagged `ta-IN` (outside the lists) | NA `unsupported_language`, observed | NA `unsupported_language` | NA `unsupported_language` (4 of 4 turns NA) | NA `unsupported_language` | scored |
| tagged `hi-Latn` | scored, observed | scored, observed | scored, observed | NA `unsupported_script`, observed | scored |
| one agent turn `ta-IN` | NA `unsupported_language` | scored | scored, share `0.666666666666666667`, coverage 3 of 4 | NA `unsupported_language` | scored |

At T0 the same bundles keep `agent_channel_unknown` for `agent_audio_clarity` and `missing_input` for the agent-transcript metrics, whatever the language (precedence, ADR 0004 Decision 4). A declaration on an `our_stt` transcript (T0) does not load.

## 7. Observed tier (R1, R2)

Each bundle stores the tier its records show: T0 for both T0 columns, T1, T2 (`evidence_tier`). An over-claim (T0 stored as T1 or T2, T1 as T2) and an under-claim (T2 stored as T1 or T0, a T1 run without spans stored as T0) do not load; a declared withheld record of a provenance kind excuses an over-claim and never an under-claim; `spans` with T0 is rejected on the Run (R1). `applicability` ignores the label: changing `observed_tier` changes no answer.

## 8. Export

Each of the four bundles, for both caller origins, loads inside an internal `ExportBundle`, comes back equal, round-trips through JSON, and is accepted by the generated JSON Schema. The four together load in one export.

## 9. Findings

### Divergence D1 (red by design): a stored result is never checked against the run's inputs (finding B15)

`RunBundle` checks references, results against their definitions (value type, pass rule, scorer), the verdict against the results and the cost summaries against the records, but not a result against the run's `available_inputs`, the channel maps or the turns' language tags. ADR 0004 Decision 3 says that a metric with missing inputs **is** `not_applicable`; the architect deferred the bundle-level re-run to this task (`api-surface.md` B15: "Recommendation: defer with applicability to the T-DEG work (P1-19)"). Four tests forge a result in an otherwise consistent bundle (the verdict is rebuilt) and expect rejection; all four fail with `DID NOT RAISE ValidationError`:

| Test | Forged result | Evidence |
|---|---|---|
| `test_deg_bundle_rejects_a_scored_result_for_a_missing_input` | T0: `tool_call_success` scored `true`, the run has no tool calls | the bundle loads |
| `test_deg_bundle_rejects_not_applicable_for_an_input_the_run_has` | T1: `goal_completion` `missing_input [transcript]`, the run has a transcript | the bundle loads |
| `test_deg_bundle_rejects_a_wrong_not_applicable_reason` | T0: `agent_politeness` `language_not_observed` instead of `missing_input [agent_transcript]` | the bundle loads |
| `test_deg_bundle_rejects_a_scored_result_on_an_untagged_transcript` | T1, untagged: `goal_completion` scored with `language_source=observed` | the bundle loads |

The tests assert only that a `ValidationError` is raised, not a tag or a function name, so the architect keeps the design freedom. Recommendation: add a `[applicability]` rule to `check_run_bundle` that re-runs `applicability` (missing inputs, channel, language steps over the bundle's turns and transcripts) for each result and rejects a stored status, reason, missing kinds or `language_source` that differs. This is the owner's decision to take (it changes the validation of every stored bundle, including future imports); until then these four tests stay red and are listed for the gate.

### Ambiguities (each with a recommendation)

- **T-A1. What `usage` means.** `InputKind.usage` does not say whose usage or which units. The batch 7 fixture `world()` lists `usage` at T0 because it counts our own evaluation usage; that contradicts "agent cost is not_available at T0". The tests read it as usage of the agent under test, and the T0 bundle with provider-reported usage lists it. Recommendation: define `usage` in ADR 0004 or 0014 as agent-under-test usage; a metric that needs a unit (`input_tokens`) still reads the units it finds and reports a missing unit as `error`, not as a score (today nothing says what a metric does with `usage` that lacks its unit).
- **T-A2. T2 cannot be told from T1 by applicability.** No `InputKind` separates frame-accurate turns and interruptions (observer) from audio-aligned or telemetry timing, so an interruption metric would be `scored` at T0 on turns whose `interrupted` is `None`. Recommendation: add an `interruptions` input kind (closed-set change, owner decision, ADR 0003) before any interruption metric is defined; until then no Phase 1 metric may read `Turn.interrupted`.
- **T-A3. Which transcript supplies an input.** A run may hold an `our_stt` and a telemetry transcript; the bundle does not say which supplies `transcript` and `agent_transcript`, and `applicability` takes the transcripts from the caller. The fixture holds one transcript per run. Recommendation: record the transcript IDs a result read (`MetricResult.transcript_ids`, additive) so the B15 rule can re-run exactly.
- **T-A4. `agent_transcript` is listed by the producer.** Unlike `agent_audio`, nothing ties `agent_transcript` in `available_inputs` to turns with `role=agent`. A producer could list it for a run whose turns are all `unknown`. Recommendation: the B15 rule (D1) also checks that `agent_transcript` implies at least one agent turn and that `transcript` implies at least one turn.
- **T-A5. The inconclusive share.** SCOPE and ADR 0013 say "T-DEG reports the inconclusive share per tier", but no function computes it. The tests compute `(not_applicable + errored) / required_total`. Recommendation: leave it to the reporting layer (Phase 2) and keep the formula here as the reference; no change to the schema.
- **T-A6. Wording of SCOPE for the agent cost "with raw preserved".** At a pure T0 there is no raw (no usage, no cost): `usage_not_reported`. The raw values exist when the black box reports a cost in a currency we do not support; that case is tested separately (`test_deg_agent_cost_t0_reported_in_unsupported_currency_keeps_the_raw_values`). Recommendation: keep both readings.

## 10. Test map

SCOPE names (verbatim): `test_deg_schema_identical`, `test_deg_same_metric_keys_all_tiers`, `test_deg_tool_metric_na_at_t0`, `test_deg_verdict_inconclusive_when_required_na`, `test_deg_no_na_with_value0_or_passed_false`, `test_deg_agent_cost_not_available_at_t0`, `test_deg_eval_cost_computed_all_tiers`, `test_deg_agent_audio_na_when_channel_unknown`.

Proposed by qa (every other `test_deg_*` in the three files; not in SCOPE): the matrix cells and the doc check, the fixture guards, JSON Schema acceptance, JSON round trip, entity types, channel maps with only `unknown` or `mixed` channels, no recording, evidence not label, the label never read, verdict counts and weighted scores per tier, all-required-not-applicable, fail over inconclusive, the forged `value`/`passed` on a not applicable result, the reason precedence, the six language scenarios, the observed-tier checks, the export checks, the agent and evaluation cost tests beyond the two SCOPE names, and D1.

Run with `uv run --locked pytest -q packages/evalschema/tests/test_tier_degradation.py packages/evalschema/tests/test_tier_degradation_cost.py packages/evalschema/tests/test_tier_degradation_language.py`. Results by Python version and the mutant table are in `docs/phase1/evidence/red-or-green/batch21-tdeg/`.

## 11. Results (2026-10-24)

- The three files: **169 items, 165 pass and 4 fail** on Python 3.11.16, 3.12.14, 3.13.15 and 3.14.7 (each in its own `UV_PROJECT_ENVIRONMENT`), the same four on every version: the D1 tests, each `Failed: DID NOT RAISE ValidationError`.
- Full suite on each version: 118 failed, 7516 passed, 5 deselected. 114 failures are `test_mapping_f8.py` (the F8 mapping tests, written concurrently by another qa; the converter does not exist yet), 4 are D1. Nothing else fails.
- ruff check, ruff format --check, mypy and mypy --strict (155 files), `check_headers.py`, `guard_private_fixtures.py` and `check_licenses.py` (42 ok) pass.
- 33 mutants of `src` were applied in a scratch copy (not_applicable rules, reason precedence, verdict counts and status, weighted score and coverage, cost-domain rules, R1 and R2, evidence tier): all 33 are killed by these three files. One earlier mutant (`fail` only `if failed > 0 and passed < total`) was equivalent (a failed check implies `passed < total`) and was replaced by one that does change behaviour.
- Logs, mutant definitions and results: `docs/phase1/evidence/red-or-green/batch21-tdeg/`.
