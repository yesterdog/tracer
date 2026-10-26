# Phase 3 batch D contract: definitions, scorer registry, rubric file, result mapping, pipeline, store (P3-18 to P3-21)

Author: qa, 2026-10-25. Source: `docs/phase3/SCOPE.md` (A.4, D1 to D8, F3 to F7), ADR 0023 (rules 1 to 19, invariants I1 to I10, the `tracer.flags/1` section), ADR 0024 (rules 11 to 14), ADR 0004 (Decisions 6, 9, 10), ADR 0016 (I17), the contracts of batches A, B and C and the implemented `packages/evalcore` (importers, audio, normalise, tokens, align, spans, flags, and the `stt` boundary of batch B as it stands in the tree), `tracer_evalschema` (metrics, results, snapshot, rubric, run, bundle, evaluator, persona, events, usage, cost, privacy, digest), `tracer_evaldb.repository`, and the lead's decision of 2026-10-25 on the owner answers (review budget, threshold; see section 12.1). qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test. Every statement below is pinned by a test named in section 13, unless it says "not pinned".

No schema change (SCOPE D5). Every workaround of 0.0.1 is stated as such in section 12 (findings F13 to F16 are new).

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/` and nothing under it was edited. The tests and the one data file are staged at

```text
docs/phase3/evidence/red/batch-d/staging/tests/evalcore/  -> packages/evalcore/tests/   (5 files)
docs/phase3/evidence/red/batch-d/staging/tests/cli/       -> packages/cli/tests/        (2 files)
docs/phase3/evidence/red/batch-d/staging/data/rubric-mr.example.json -> docs/examples/rubric-mr.example.json
```

No `__init__.py`, no `conftest.py`. The dev's first act in batch D:

```text
git mv docs/phase3/evidence/red/batch-d/staging/tests/evalcore/*.py packages/evalcore/tests/
git mv docs/phase3/evidence/red/batch-d/staging/tests/cli/*.py packages/cli/tests/
mkdir -p docs/examples
git mv docs/phase3/evidence/red/batch-d/staging/data/rubric-mr.example.json docs/examples/rubric-mr.example.json
```

The example rubric file is product data (section 4.4): the dev may reword its `note`, but the test pins the facts (threshold 0.5, budget `max` 1.0 inclusive, the words PLACEHOLDER and "no limit" in the note).

| File | Holds | Functions | Collected |
|---|---|---|---|
| `evalcore_batchd_support.py` | helpers (not collected): lazy loaders, the invented Marathi-like words, the synthetic call, `FakeEngine`, `run_pipeline`, `mask_ids`, `base_document` | - | - |
| `test_recorded_definitions.py` | P3-18 (templates, registry, rubric file, parameters, configuration, BL-145, the example rubric), the SCOPE-row guards | 24 | 62 |
| `test_flags_document.py` | P3-19 builder and validator of `tracer.flags/1` | 10 | 77 |
| `test_recorded_scoring.py` | P3-19 result mapping | 26 | 40 |
| `test_recorded_pipeline.py` | P3-20 pipeline | 28 | 28 |
| `cli_batchd_support.py` | helpers of the cli tests (loads the evalcore support file by path) | - | - |
| `test_recorded_store.py` | P3-21 store | 11 | 16 (15 selected, `[postgres]` deselected) |
| total | | 99 | 223 |

Nothing of the batch D modules is imported at module level: each test loads `tracer_evalcore.<module>` or `tracer_cli.store` lazily with `importlib` (`mod("definitions")`), so before the modules exist every test fails on its own with `ModuleNotFoundError` and the directories still collect. The support module imports the existing `tracer_evalcore.stt`, `.audio.chunker` and `.importers.wav` (batches A and B) at module level; those exist. The cli tests load `packages/evalcore/tests/evalcore_batchd_support.py` by file path (the evalschema precedent for the F8 mapper), so the two directories share one fake engine; the file must therefore be moved before the cli tests can run. All seven files pass `ruff check`, `ruff format --check` and `mypy` (repository configuration) as staged; they are ASCII only (Devanagari is written as `\u` escapes). Nothing opens a socket, reads the clock for a result, reads the environment or touches any private fixture. The `[postgres]` case carries the marker `postgres` (deselected by `addopts`, selected in the Postgres CI job with `TRACER_TEST_POSTGRES_URL`); it could not be run here.

Commands (after the move): `uv run --locked --all-groups pytest packages/evalcore/tests packages/cli/tests` or the whole workspace.

The test values `TEST_ONLY_FLAG_MIN_CONFIDENCE = 0.3` and `TEST_ONLY_REVIEW_BUDGET = 0.25` exist in the support module only and are marked as test values. They are not defaults of the product; no product code, example or test of the product reads them.

## 1. Where the code lives, and why

```text
tracer_evalcore/registry.py     scorer registry, shipped keys, UnknownScorerError                 (P3-18)
tracer_evalcore/rubricfile.py   the rubric file tracer.rubric/1, RubricSpec, RubricFileError      (P3-18)
tracer_evalcore/definitions.py  three templates, binding parameters, the recorded configuration,
                                recorded Project and AgentTarget                                   (P3-18)
tracer_evalcore/flagsdoc.py     tracer.flags/1 document builder and validator                     (P3-19)
tracer_evalcore/scoring.py      score_results: the three MetricResult records                     (P3-19)
tracer_evalcore/pipeline.py     score_recorded_call, PipelineResult, exit codes, CostParts        (P3-20)
tracer_cli/store.py             the SQLite store of a scored run through tracer-evaldb            (P3-21)
```

Reasons. (1) Everything except the store is pure logic over `tracer_evalschema` and the batch A to C modules, so it belongs in `tracer-evalcore` (SCOPE package split, A.4 "Pipeline (evalcore)"); the batch A import tests walk every module under `src/` and cover the six new modules with no change (no network module, no `pydantic` import: the modules build entities from JSON-mode documents and `model_validate`). (2) The store needs `tracer-evaldb`, and only `tracer-cli` may depend on it, so P3-21 lives in `tracer_cli.store`; `test_the_store_module_is_the_only_phase3_code_that_imports_the_store` pins that `store.py` is the only file of the three Phase 3 packages that imports `tracer_evaldb`. (3) `rubricfile` is separate from `definitions` so that the parser (the one place that makes a pass rule) is the only module that knows the pass-rule JSON shape (`test_no_code_path_supplies_a_review_budget`). (4) `registry` is separate so that `definitions` and `scoring` share the keys without a cycle. Rejected: one `scoring.py` holding everything (the validator, the mapping and the configuration have different change reasons); a `tracer_evalcore.store` (it would import the store package into the pure core).

Dependency order (no cycles): `registry` <- `rubricfile` <- `definitions` <- `scoring` <- `pipeline`; `flagsdoc` <- `scoring`.

## 2. `registry` (P3-18, BL-144)

```text
MER_KEY    = "stt_export_agreement_mer"
COUNT_KEY  = "stt_export_flag_count"
SHARE_KEY  = "stt_export_flagged_token_share"
SHIPPED_KEYS = (MER_KEY, COUNT_KEY, SHARE_KEY)       # this order everywhere (re-exported by definitions)
implementation_ref(key) -> "builtin:<key>"

@dataclass(frozen=True) class ScorerEntry:  key, implementation_ref, version
class ScorerRegistry:
    def __init__(self, entries: Iterable[ScorerEntry])
    def get(self, ref: str) -> ScorerEntry            # UnknownScorerError if absent
    def __contains__(self, ref) -> bool
    def refs(self) -> tuple[str, ...]
DEFAULT_REGISTRY: ScorerRegistry                      # the three entries, version == tracer_evalcore.__version__
scorer_info(entry) -> ScorerInfo                      # {implementation_ref, version}
check_runnable(definitions, registry=DEFAULT_REGISTRY) -> None   # UnknownScorerError
class UnknownScorerError(InputRefusedError)           # a ValueError; class attribute code = "unknown_scorer"
```

`check_runnable` looks at every definition that is not `llm_judge` (the inert check of section 3.3 names no scorer) and raises `UnknownScorerError` when its `implementation_ref` is absent from the registry. The message holds no ref. The scorer version is the distribution version (ADR 0023 rule 3). ADR 0023 rule 3 also says "or whose version differs": a `MetricDefinition` has no version field for a scorer, so only the unknown ref refuses (ambiguity 3). The registry is injected into `build_configuration` and `score_recorded_call` (default `DEFAULT_REGISTRY`) so that the refusal is testable (`test_unknown_scorer_refuses_before_run`: a registry without the share scorer is refused by `check_runnable`, by `build_configuration` and by the pipeline, and the engine is never called).

## 3. `definitions` (P3-18)

```text
PARAMETER_NAMES = ("flag_min_confidence", "normaliser")
PLACEHOLDER_KEY = "recorded_call_no_outcome_check"
EVALUATOR_NAME  = "recorded-call agreement scoring"
RECORDED_ENDPOINT_URL = "wss://recorded-call.invalid/"

shipped_definition(key, *, created_at) -> MetricDefinition          # ValueError for another key
shipped_definitions(*, created_at) -> tuple[MetricDefinition, ...]  # in SHIPPED_KEYS order

@dataclass(frozen=True) class ScoringParameters:  flag_min_confidence: Decimal; normaliser: str
check_binding_parameters(params_by_key: Mapping[str, Mapping[str, Any]]) -> ScoringParameters
parameters_of(snapshot: ConfigSnapshot) -> ScoringParameters        # reads the three bindings
class BindingParameterError(InputRefusedError):  code, key, parameter

@dataclass(frozen=True) class RecordedConfiguration:  snapshot
    definition(key) -> MetricDefinition        # KeyError if absent
    binding(key) -> MetricBinding
build_configuration(spec: RubricSpec, *, configured_language: str, created_at: datetime,
                    registry: ScorerRegistry = DEFAULT_REGISTRY) -> RecordedConfiguration

recorded_project(*, name, created_at, provider_configs=()) -> Project
recorded_agent_target(*, name, created_at) -> AgentTarget
```

### 3.1 The three templates (rule 3)

Each is a `MetricDefinition` of version 1 with a fresh id and: `kind=deterministic`, `value_type=numeric`, `requires=["transcript", "agent_transcript"]` (this order), `languages=["mr"]`, `script_sensitive=false`, `aggregation={kind: per_conversation}`, **no pass rule**, `implementation_ref=builtin:<key>`, `scale`, `prompt_template`, `judge_model_ref` absent, and `parameters` exactly `flag_min_confidence` (`number`) then `normaliser` (`string`), neither with a default. Units: `stt_export_agreement_mer` `ratio`, `stt_export_flag_count` `count`, `stt_export_flagged_token_share` `ratio`; `wer` and `cer` never occur (also no string constant `"wer"` or `"cer"` in the six modules, by AST). A "template" is a function, not a stored record: the stored definitions are the ones a configuration builds. In a configuration, the share's pass rule is the owner's budget and the other two definitions stay without one (`test_shipped_definitions_have_no_pass_rule`, `test_agreement_unit_is_ratio_never_wer_or_cer`, `test_definitions_requires_transcript_and_agent_transcript`). The task text says the rubric file "builds owner definition versions"; the contract builds fresh version-1 definitions from the templates and the file (ambiguity 2).

### 3.2 Binding parameters (rule 4)

`check_binding_parameters` takes the `params` of the three bindings by key (all three keys required) and returns the values they carry. Refusals are `BindingParameterError` (an `InputRefusedError`, so a `ValueError`) with these `code` values; the message holds the code, the metric key and the parameter name, never a value:

| code | when |
|---|---|
| `binding_param_missing` | a key is absent from the mapping, or a binding lacks a parameter or holds `null` for it |
| `binding_param_invalid` | `flag_min_confidence` is a bool, text, list, NaN, infinite, below 0, above 1 or longer than 18 fractional digits (`parse_threshold` of batch C: JSON number only, read as `Decimal(repr(value))`); `normaliser` is not text |
| `normaliser_unknown` | `normaliser` is text that `tracer_evalcore.normalise.SUPPORTED_NORMALISERS` does not list |
| `binding_param_unequal` | the three bindings do not carry equal values (`1`, `1.0` are equal) |

`parameters_of(snapshot)` applies it to the bindings of the snapshot (by the definition key), so the pipeline reads the parameters from the snapshot it stores, never from the file. `build_configuration` applies the same check to the spec before it builds anything. `test_missing_binding_param_refuses_before_any_send` pins the table, the builder and the pipeline: a snapshot whose count binding lost its threshold is refused and the fake engine records zero calls.

### 3.3 The configuration (rule 17, F3, finding F13)

`build_configuration(spec, ...)` returns a `RecordedConfiguration` whose `snapshot` is a valid `ConfigSnapshot` (digests sealed over documents built from the JSON-mode form; the oracle does it in about 40 lines with `compute_digest`, `evaluator_digest_domain` and `compute_snapshot_digest`, no hook). Order everywhere (definitions, bindings, the evaluator's references): MER, count, share, then the inert check.

- Definitions: the three templates (the share carries `spec`'s budget as `range{max, inclusive}`; the other two none) and the **inert expected-outcome check** (finding F13): `key=recorded_call_no_outcome_check`, `kind=llm_judge`, `value_type=boolean`, `requires=["transcript"]`, `languages="language_independent"`, `script_sensitive=false`, per_conversation, pass rule `equals_true`, a `prompt_template` that uses `{{expected_outcome}}` (and says that no judge is run), `prompt_variables=["expected_outcome"]`, `judge_model_ref={provider: none, model: not_run}`.
- Bindings: one per definition, `enabled=true`, `evaluator_id` the evaluator's. The three scorer bindings carry `params = {flag_min_confidence: <the JSON number of the file, a float stays a float>, normaliser}`; the inert check's `params` is empty.
- Rubric: items in this order: the inert check (`required=true`), then the items of the file in the file's order (`required` as in the file; no weights). Every reference carries the digest of the record it names.
- Evaluator: `name=EVALUATOR_NAME`, `language=configured_language`, `max_duration_ms=14_400_000` (the schema maximum, stated as such), no scripted turn, no expected tool call, no test profile. `instructions` contains (case-insensitive) "recorded call", "no call was simulated", "agreement" and "speech-to-text", and not "accuracy", "simulated caller" or "error rate"; `expected_outcome` contains "no outcome is evaluated".
- Persona: `language=configured_language`, no accent (a `Persona` has no text field, so ADR 0023 rule 17's "its text says it describes no simulated caller" cannot be written; the Evaluator text says it, ambiguity 5).
- `pricing_catalog` absent.

`test_configuration_*` pin all of this. `build_configuration` refuses a spec without the budget (`RubricFileError("rubric_budget_missing")`, section 4.3) and a registry that lacks a scorer (`UnknownScorerError`).

### 3.4 Project and agent target

`recorded_project(name, created_at, provider_configs)` is a `Project` (the CLI passes `provider_config_ref(model)` of batch B). `recorded_agent_target(name, created_at)` is an `AgentTarget` of version 1 with `endpoint={transport: ws, url: wss://recorded-call.invalid/}` (finding F15: 0.0.1 requires an endpoint, a recorded call has none, and `.invalid` is reserved by RFC 6761 so that the placeholder can never resolve), `languages=[]`, `declared_capabilities=[]`, `declared_stack=[]` (nothing is declared, never "all languages").

## 4. The rubric file `tracer.rubric/1` (P3-18)

### 4.1 Format

UTF-8 JSON (the CLI decodes the file, `parse_rubric_file(text: str)` takes text; `bytes` is a `TypeError`), read with `loads_strict` (no duplicate member, no `NaN`, depth bounded, no BOM):

```json
{
  "format": "tracer.rubric/1",
  "note": "free text, optional, never stored",
  "parameters": {"flag_min_confidence": 0.5, "normaliser": "mr-Deva/1"},
  "items": [
    {"metric": "stt_export_flagged_token_share", "required": true,
     "pass_rule": {"kind": "range", "max": 1.0, "inclusive": true}},
    {"metric": "stt_export_agreement_mer", "required": false},
    {"metric": "stt_export_flag_count", "required": false}
  ]
}
```

- Top-level members: `format`, `parameters`, `items` (all required) and `note` (optional text; any other member is `rubric_shape`).
- `parameters`: exactly `flag_min_confidence` (a JSON number, never text or bool, in [0, 1], at most 18 fractional digits after reading it as `Decimal(repr(value))`) and `normaliser` (text naming a shipped normaliser). **No parameter has a default anywhere**: both are explicit members of the file (lead decision, section 12.1).
- `items`: a list of objects with exactly `metric` and `required`, plus `pass_rule` for the share only. `metric` is one of the three shipped keys, each at most once. The share item must be present, `required: true`, with a pass rule of exactly `{kind: "range", max: <number in [0,1]>, inclusive: <bool>}` (no `min`, no other member, `max` a JSON number, not text or bool). The other two items have `required: false` and no pass rule: the review budget is the one owner rule of Phase 3.

### 4.2 Parsed form

```text
RUBRIC_FORMAT = "tracer.rubric/1"
@dataclass(frozen=True) class RubricItemSpec:  metric: str; required: bool; pass_rule: RangeRule | None
@dataclass(frozen=True) class RubricSpec:      flag_min_confidence: int | float; normaliser: str; items: tuple[RubricItemSpec, ...]
parse_rubric_file(text: str) -> RubricSpec
class RubricFileError(InputRefusedError):  code
```

Neither dataclass has a default for any field. The threshold stays the JSON number of the file (`0.5` is a `float`, `1` an `int`); `pass_rule` is the schema's own `RangeRule`.

### 4.3 Refusal codes (`RubricFileError.code`, checked in this order)

| code | when |
|---|---|
| `rubric_not_json` | not text of valid JSON: empty, truncated, duplicate member, `NaN`, nesting 5,000 deep, a byte order mark |
| `rubric_shape` | not an object; a missing, unknown or non-text-`note` member; `parameters` or an item with other members; a threshold that is text, bool or null; a non-text normaliser; `items` not a list; an item not an object; `required` not a bool; a pass rule on an item other than the share |
| `rubric_format_unsupported` | `format` is not exactly `tracer.rubric/1` |
| `rubric_parameter_invalid` | the threshold is outside [0, 1] or has more than 18 digits, or the normaliser is not a shipped one |
| `rubric_metric_unknown` | an item names another metric (also the inert check) |
| `rubric_metric_repeated` | a metric listed twice |
| `rubric_pass_rule_invalid` | the share's pass rule is not a `range` with exactly `kind`, `max`, `inclusive`, `max` a number in [0, 1] |
| `rubric_required_without_pass_rule` | any item with `required: true` and no pass rule |
| `rubric_budget_missing` | no share item that is required and has a pass rule (no item at all, or the share listed with `required: false`). `build_configuration` raises the same code for a hand-made spec without it |

The messages are the code. `test_rubric_file_without_required_pass_rule_refused`, `test_rubric_file_refusals_carry_one_stable_code` (36 files), `test_rubric_file_pass_rule_shape_is_range_with_max_only`, `test_rubric_file_is_parsed_exactly_and_without_defaults`, `test_rubric_file_note_is_optional_text_and_nothing_else`, `test_no_code_path_supplies_a_review_budget`.

### 4.4 The shipped example

`docs/examples/rubric-mr.example.json` (staged) is the example of the base case. Its facts are pinned by `test_shipped_example_rubric_file_loads_builds_and_runs`: the `note` says PLACEHOLDER and "no limit"; `flag_min_confidence` is `0.5`; the share item is required with `max = 1.0` (a float), `inclusive = true`, so the rule cannot fail (a run in which every export token disagrees has share 1.0 and `passed = true`); the file parses, builds a configuration, a pipeline run over it passes `check_run_bundle`; the note is stored nowhere. The CLI and the report print the budget as `review budget: 1.0 (no limit)` for this value (P3-22 and P3-23, batch E; not pinned here). No code path supplies a budget: both the parser and the builder refuse a spec without it, and no module other than `rubricfile` names `RangeRule` or writes a pass rule (AST).

## 5. `flagsdoc` (P3-19, ADR 0023 rule 12)

```text
FLAGS_METADATA_KEY = "tracer.flags"
build_flags_document(*, normaliser, provider, model, threshold: Decimal, alignment, export, stt, flags) -> dict
validate_flags_document(document, *, normaliser, threshold: Decimal, model, export: TokenSequence | None = None) -> None
class FlagsDocumentError(InputRefusedError):  code
```

`build_flags_document` writes exactly the `tracer.flags/1` document of ADR 0023 from the parts of batch C: `stt.model_source` is `requested`, `flag_min_confidence` is `format_threshold(threshold)`, `tokens.flagged_export` is the sum of `export_tokens` over the **flagged** spans, `spans` holds every span (flagged or not) in span order with `confidence` as `SpanFlag.confidence`, and `audio_start_ms` / `audio_end_ms` only when the span has a range. `validate_flags_document` is the structural check of the ADR's JSON Schema (written by hand: `jsonschema` is not a runtime dependency; a non-mapping is a `TypeError`) and every cross-field rule of the ADR, one `FlagsDocumentError.code` each (the message is the code and never holds a value):

| code | rule |
|---|---|
| `flags_shape` | any structural fault of the schema (unknown or missing member, wrong type, a float or bool where an integer is required, a pattern, a minimum, an audio bound without its pair) |
| `flags_binding_mismatch` | `normaliser`, `flag_min_confidence` or `stt.model` differ from the binding values given |
| `flags_counts` | `hits + substitutions + deletions != tokens.export`; `hits + substitutions + insertions != tokens.stt`; the sums of the span op counts differ from the alignment's; `tokens.flagged_export` is not the sum of `export_tokens` over the flagged spans |
| `flags_span` | `export_tokens != substitutions + deletions`; `start > end`; zero width iff `export_tokens == 0` violated; `stt_token_end - stt_token_start != substitutions + insertions`; `edit_distance > key_length`; `confidence` is not the half-even 4-digit display of `edit_distance / key_length`; `flagged` is not the exact comparison `edit_distance >= threshold * key_length`; `audio_start_ms > audio_end_ms` |
| `flags_order` | spans not sorted by (`turn_seq`, `start`, `end`, `stt_token_start`); non-zero-width spans of one turn overlapping; STT token ranges overlapping or beyond `tokens.stt` |
| `flags_export_mismatch` | with `export` given: a `turn_seq` that names no export turn, an offset beyond the turn's text or not on a token edge |

The tests (`test_flags_document_*`, 77 collected) refuse 46 structural and 23 cross-field mutations of the hand-computed document of the base scenario and check, with `jsonschema` (a test dependency), that a structural fault is refused by the schema and the validator, and a cross-field fault by the validator only. A float where an integer is required (`9.0`) is accepted by JSON Schema and refused by the validator (`flags_shape`): the validator is stricter, by design (`test_flags_document_counts_are_python_integers_not_floats_or_booleans`).

## 6. The base scenario (hand-computed, used by every scoring test)

Export (VoicEra format, three lines; invented words of Marathi-like text; the speakers map to `agent`, `caller`, `agent`):

```text
turn 0 (assistant): NAMASKAR MALA PAANI GAAV
turn 1 (user):      KAAM GHAR BAAZAR
turn 2 (assistant): DON TEEN JEVAN VEL(LLA)
```

STT, two chunks (24,000 frames at 8 kHz cut with `max_chunk_ms=2000`, `search_ms=1000`), language `mr` for both:

```text
chunk 0: NAMASKAR MALA PANI GAAV KAAM GHAR        phrases (0-2000 ms) (2000-4000 ms)
chunk 1: BAAZAR DON JEVAN VEL(LLA) ABHI           phrases (4000-6000 ms) (6000-8000 ms)
```

Alignment: 11 export tokens, 11 STT tokens, H 9, S 1 (PAANI spelled without its long a), D 1 (TEEN), I 1 (ABHI); unique minimal alignment (2 edits cannot beat D + I at the end). MER = 3/12 = 0.25. Spans: (1) turn 0, the word PAANI, STT tokens [2, 3), edit distance 1 of key length 4, confidence `0.25`, audio (0, 2000), not at a chunk edge; (2) turn 2, the word TEEN, a deletion at STT gap 8, 3 of 3, confidence `1`, no audio; (3) turn 2, zero width at the end of the turn, STT tokens [10, 11), 3 of 3, confidence `1`, audio (6000, 8000), last token of chunk 1 so `near_chunk_boundary` true. With the test threshold 0.3: spans 2 and 3 are flags, count 2, flagged export tokens 1, share 1/11. Evidence of every scored result: the turn reference of export turn 2, then the artifact range (6000, 8000). The full document is `base_document()` in the support module.

## 7. `scoring.score_results` (P3-19, rules 11 to 15)

```text
score_results(*, configuration, run: Run, export: Transcript | None, export_turns: Sequence[Turn],
              stt: Transcript | None, stt_turns: Sequence[Turn], audio_artifact_id: ArtifactId | None,
              created_at: datetime, registry: ScorerRegistry = DEFAULT_REGISTRY) -> tuple[MetricResult, ...]
```

Pure (no clock, no I/O, no log): the three results in `SHIPPED_KEYS` order, ids fresh. Every result has `metric = {id, version, content_digest}` of its definition, `created_at` as given and `transcript_ids = [export.id, stt.id]` for those that exist (in that order; `None` when neither exists). The common applicability answer is `results.applicability(definition, run, turns=<agent turns of the held transcripts>, transcripts=<held>)`, i.e. exactly what `check_run_bundle` re-runs, so the stored result can never disagree with the re-run.

Order of decisions:

1. Applicability says `not_applicable`: three results with `status=not_applicable`, that `na_reason`, `missing_kinds` (order of `requires`), `language_source` and `declared_language_transcript_ids` as the answer (a declared language the metric does not cover gives `unsupported_language` with source `declared`; no declaration gives `language_not_observed` with no source). No `scorer`, no value, no metadata, no evidence.
2. Otherwise one computation, then three `scored` or three `error` results. An error result has `value=None`, no `passed`, `error={code, message}`, `scorer`, the same `language_source` (`declared`) and `declared_language_transcript_ids` (`[export.id]`) as the answer, no metadata and no evidence.
3. The checks in order, each an error code (messages hold counts and sizes only, never a tag or any text):

| code | when and message |
|---|---|
| `stt_language_not_observed` | some STT turn has no language or `und`: `"<n> of <m> stt turns carry no observed language"` (checked first) |
| `stt_unsupported_language` | else some STT turn's primary language is not covered by the definition (`mr-IN` is covered): `"<n> of <m> stt turns carry a language other than the covered one"` |
| `no_tokens` | the export has no token (message `"the export has no token"`), else the STT has none (`"the stt transcript has no token"`); a transcript with no turn counts; never a MER of 0 or 1 |
| `alignment_too_large` | `AlignmentTooLargeError`; the message is its text (sizes) |
| `levenshtein_too_large` | `LevenshteinTooLargeError`; sizes |
| `stt_provenance_missing` | the STT transcript has no `provider` or no `model` (never invented) |
| `flags_document_refused` | `validate_flags_document` refuses, or the document would give a non-empty `metadata_flags` (a secret-looking model name); message `"the flags document was refused: <code>"` |

   The gate looks at the turns of non-empty chunks only (an empty chunk has no turn, ADR 0004 10b).
4. Scored results (`status=scored`): values `MER = alignment.mer` (`edits / (hits + edits)`, a float), `count = number of flagged spans` (an int), `share = flagged export tokens / export tokens` (a float, a zero-width flag covers none). `passed` is the definition's pass rule applied to the value (the share only), else `None`. `scorer = {implementation_ref, version}` of the registry, `language_source=declared`, `declared_language_transcript_ids=[export.id]`, `metadata = {"tracer.flags": document}` (the same document in all three; `metadata_flags` empty), and `evidence_refs`: one `EvidenceRef{kind: turn}` per distinct export turn that holds a **flagged** span, in `seq` order, then one `EvidenceRef{kind: artifact_range, ref_id: <audio artifact>, start_ms, end_ms}` per distinct audio range of a flagged span, in span order. Unflagged spans leave no reference.

Tests: `test_results_pass_check_result_against_definition` (every result passes `check_result_against_definition`), `test_share_passed_follows_the_owners_budget`, `test_flags_metadata_validates_against_tracer_flags_1`, `test_flags_metadata_has_no_text_and_no_metadata_flags`, `test_evidence_refs_resolve_in_bundle`, `test_evidence_refs_list_distinct_turns_and_ranges_in_order`, `test_flagged_spans_in_one_phrase_share_one_audio_range`, `test_transcript_ids_required_with_two_transcripts`, `test_language_source_declared_from_export`, `test_empty_export_is_error_no_tokens_not_zero`, `test_empty_stt_is_error_no_tokens_not_zero`, `test_stt_language_gate_is_an_error_never_a_score` (7 cases), `test_stt_language_gate_ignores_empty_chunks_and_accepts_regional_tags`, `test_alignment_size_cap_is_an_error_with_its_own_code`, `test_levenshtein_size_cap_is_an_error_with_its_own_code`, `test_stt_without_provider_or_model_is_an_error_not_an_invented_value`, `test_a_secret_looking_model_name_is_refused_as_an_error`, `test_a_refused_document_becomes_an_error_whatever_the_reason`, `test_score_results_is_pure_and_repeats_the_pipeline`, `test_threshold_decides_flags_exactly_and_the_document_records_every_span`, `test_texts_that_agree_after_normalisation_have_no_span`, `test_the_lla_and_la_spellings_are_never_merged`, `test_scores_equal_an_independent_reference_on_random_calls` (hypothesis, 40 derandomised examples, against a plain DP written in the test).

`test_metrics_evaluate_agent_outputs_only` (BL-145, renamed; ADR 0016 I17) pins, on a variant in which the caller line of the export is also flagged: the export's `caller` turns are compared and can carry a flag; every span names an export turn; every turn evidence reference names a turn whose transcript is the export and whose `source` starts with `agent_` (never `our_stt`); the STT turns (role `unknown`) carry no span and no reference; the definitions require `agent_transcript`; no verdict exists.

## 8. `pipeline` (P3-20)

```text
EXIT_SCORED = 0;  EXIT_NOT_SCORED = 2;  EXIT_STT_FAILED = 3
@dataclass(frozen=True) class CostParts:  catalogs: tuple[PricingCatalog, ...]; records: tuple[CostRecord, ...]; summaries: tuple[CostSummary, ...]
CostBuilder = Callable[[Sequence[UsageRecord]], CostParts]
@dataclass(frozen=True) class PipelineResult:  bundle: RunBundle; outcome: str; stt_failure: SttError | None
    exit_code -> int                      # property

score_recorded_call(*, audio: WavAudio | None, export_text: str | None, engine: SttEngine | None,
    configuration: RecordedConfiguration, project: Project, agent_target: AgentTarget,
    configured_language: str, export_declared_language: str | None, caller_origin: str,
    consent: ConsentRecord, retention: RetentionPolicy, created_at: datetime,
    registry: ScorerRegistry = DEFAULT_REGISTRY,
    max_chunk_ms: int = 25_000, search_ms: int = 5_000, accept_provider_downmix: bool = False,
    accept_export_warnings: bool = False, cost_builder: CostBuilder | None = None) -> PipelineResult
```

All parameters are keyword-only. These have **no default**: `audio`, `export_text`, `engine`, `configuration`, `project`, `agent_target`, `configured_language`, `export_declared_language`, `caller_origin`, `consent`, `retention`, `created_at` (every privacy and language input is explicit; the CLI fills `configured_language` and `export_declared_language` from the same `--declared-language`, they are two inputs because an undeclared export is a T-DEG case). The chunking values are the documented technical defaults of batch A (not rubric values). There is no threshold, confidence or budget parameter: they live in the configuration.

### 8.1 What is injected, and the order of operations

Injected: the engine (any `SttEngine`; tests use `FakeEngine`), the one instant `created_at` (stamps every record the pipeline builds, the reader's records, the `SttContext`; the pipeline reads no clock, no environment, no file, prints and logs nothing: AST-checked), the registry and the cost builder. Not injected: ids (fresh UUIDv7 per call, ambiguity 4). The order, each step before the next:

1. `caller_origin` is `human` or `simulated`; `configured_language` equals the configuration's evaluator language; an engine is present when there is audio (`ValueError` otherwise).
2. `parameters_of(snapshot)` (`BindingParameterError`) and `check_runnable` (`UnknownScorerError`).
3. The export is read (`read_voicera_export`, privacy marker `human_subject=true` with the given consent and retention, the declaration `{language: export_declared_language, source: user_declared, declared_at: created_at}` or none, `accept_warnings=accept_export_warnings`): `VoicEraRefusedError` propagates before any send.
4. The recording is chunked (`chunk_wav`): `ChunkerError` (a stereo recording without `accept_provider_downmix`) propagates before any send.
5. `engine.transcribe(plan, SttContext(run_id, audio.artifact.id, created_at, privacy, configured_language))`, once, only when there is audio (also when the export is missing: the recording does exist, and T-DEG needs `transcript` available). An `SttConfigurationError` (key missing, language refused) or any other exception propagates; it is never recorded as an outcome.
6. The Run, the three results, the bundle, then `check_run_bundle(bundle)`.

### 8.2 The records

- `Run`: `mode=recorded`; `caller_origin` as given; `observed_tier=T0`; `started_at` absent (never the ingest time, never the stamp of the export lines); `available_inputs` exactly, in this order: `audio` (if audio), `transcript` (if the engine returned a transcript, even an empty one), `agent_transcript` (if the export has an agent turn, T-A4; an export with only `user` lines is read and compared but this kind is not available); `detected_capabilities=[]`, `channel_maps=[]`, no `caller_external_id`; `project_id`, `agent_target` (`{id, version, content_digest}`) and `config_snapshot_id` as given; **`external_refs=[]`** (finding F14: the call id stays on the export transcript's `provider_call_ref`).
- `ingest_warnings`: the export reader's warnings in reader order, then the engine's warnings in outcome order, then, for a failed STT, `IngestWarning{code: "stt_failed", path: "/stt", message: str(failure)}` with no `raw` (the error's text is built from machine facts only).
- `bundle`: `RunBundle` with the configuration's snapshot (the same object, shared by all runs of one configuration), `artifacts=[audio.artifact]` (the reader's, unchanged), `transcripts=[export, stt]` (those that exist, in that order), `turns` = export turns then STT turns each in `seq` order, `results`, `usage_records` = the engine's usage (kept for a failed STT too), and the cost parts when a `cost_builder` is given and there is audio (called once with the usage tuple, also for a failed STT; a bundle without a builder holds no cost record: cost is the caller's to add, never a zero). No verdict (`verdict is None`, BL-186).
- An STT failure is all or nothing: no STT transcript, no STT turn; every result is `not_applicable` `missing_input` with `missing_kinds=["transcript"]`; `outcome="stt_failed"`, `exit_code=3`, `stt_failure` the engine's error.
- Missing inputs: no audio gives `missing_kinds=["transcript"]` and no engine call; no export `["agent_transcript"]`; neither `["transcript", "agent_transcript"]`; an export with only caller lines `["agent_transcript"]`. A zero-frame recording gives an empty plan, an empty STT transcript and `error` `no_tokens` (stt).
- `outcome`: `scored` (all three scored), `stt_failed`, else `not_scored`; `exit_code` 0, 3, 2. The constants are the pipeline's; batch E documents them and may add codes for usage errors.

### 8.3 How `check_run_bundle` is asserted

(a) `pipeline.check_run_bundle` is a module-level name; the test replaces it with a spy and asserts one call, with the very bundle the result holds (`test_pipeline_bundle_passes_check_run_bundle`), so a pipeline that built the bundle with `model_construct` and skipped the check fails. (b) The test calls the public `check_run_bundle` on the bundle and on a bundle read back from its JSON (`model_validate_json`, which runs the rules on load). (c) The same is done for every outcome (missing inputs, errors, STT failure) in the other tests. (d) Mutation teeth: a damaged bundle (a repeated result, a raised tier) is refused by the rules (`[duplicate]`, `[tier]`), proving the check bites.

### 8.4 Determinism rules

Given the same configuration (one `RecordedConfiguration` value), project, agent target, audio, export text, `created_at`, parameters and the same engine answer, two calls give bundles that are identical **after every typed id is replaced by its order of first appearance** (`mask_ids`); the snapshot is identical byte for byte (it is the same value); result, turn and warning order is fixed. A byte-identical bundle is impossible with fresh UUIDv7 ids (ADR 0023 rule 18's wording, ambiguity 4); `test_pipeline_is_deterministic_given_same_stt` pins the masked equality, that another STT answer differs, and that the snapshot is shared.

## 9. Error codes in one table

`unknown_scorer` (`UnknownScorerError.code`); `binding_param_missing`, `binding_param_invalid`, `normaliser_unknown`, `binding_param_unequal` (`BindingParameterError.code`); the nine `rubric_*` codes (section 4.3); `flags_shape`, `flags_binding_mismatch`, `flags_counts`, `flags_span`, `flags_order`, `flags_export_mismatch` (`FlagsDocumentError.code`); the result error codes `stt_language_not_observed`, `stt_unsupported_language`, `no_tokens`, `alignment_too_large`, `levenshtein_too_large`, `stt_provenance_missing`, `flags_document_refused` (`MetricResult.error.code`, the first two replacing the `unsupported_language` not-applicable reason that 0.0.1 cannot store for an STT turn, ADR 0023 Ambiguity 1); the Run warning code `stt_failed`. Exit codes 0, 2, 3.

## 10. Exceptions raised, not recorded

`UnknownScorerError`, `BindingParameterError`, `RubricFileError`, `VoicEraRefusedError`, `ChunkerError` and its subclasses, `SttConfigurationError` and its subclasses, any other exception of the engine, `TypeError`/`ValueError` for a wrong argument. They stop the run before the next step; no bundle exists. A failed transcription is the one engine failure that is recorded (as the outcome of section 8.2), because it is a fact about the call and the run was paid for.

## 11. `tracer_cli.store` (P3-21)

```text
STORE_FILE_NAME = "tracer.sqlite"
open_sqlite_store(out_dir) -> Engine           # creates out_dir (parents), the file 0600 (create_store_engine), migrates to head, open_store
store_run(engine, *, project, agent_target, bundle) -> StoredRun
@dataclass(frozen=True) class StoredRun:  bundle_id, created: bool, inserted: dict[str, int]
load_run(engine, bundle_id) -> RunBundle
```

`store_run` writes in this order, each call idempotent for equal content: `store_project`, `store_definition(agent_target)`, `store_snapshot(bundle.snapshot)` (with the definitions it embeds), `store_run_bundle`. `created` and `inserted` come from the bundle's `StoreResult` (a second store of the same bundle: `created=False`, nothing inserted). `load_run` is `load_run_bundle(...).bundle`. The engine is untyped here (`tracer_cli` may not import `sqlalchemy`, batch A); the store takes a URL string. The tests:

- `test_run_round_trips_through_store_byte_identical`: stored and loaded `model_dump_json()` are equal as text and bytes, and equal as models; the project, agent target, snapshot, every definition and the snapshot found by digest come back.
- `test_store_file_is_0600`: the file is `0o600` under umask 0 and 022, also after storing, and nothing beside it in the directory is readable by group or others; opening again never loosens it (POSIX only).
- `test_pipeline_bundle_stores_on_both_backends` (`[sqlite]`, `[postgres]`): store, load, store again (no change).
- Other outcomes (missing inputs, errors, a failed STT with its warning) round-trip; two runs under one configuration share one snapshot; **three runs of one call (R1, R2, R3) are stored in one store** (`test_rescoring_the_same_call_stores_a_second_run`, finding F14); the directory is created and migrated; the store module is the only importer of `tracer_evaldb`.

`test_key_never_in_store_and_report_outputs` is in the P3-21 row since SCOPE Decisions D9 (store channel; the report channel belongs to P3-23, batch E). It is written here: with the canary key in the process environment, two runs (one scored, one with a failed STT) are scored and stored, and the canary and the name `SARVAM_API_KEY` appear in no byte of any file under `--out` (the SQLite file and anything beside it), in no stored or reloaded record and in no log record; the check is not vacuous (the call text is in the file).

## 12. Findings and ambiguities (the lead auto-approves; each is implemented as recommended in the oracle and pinned as stated)

### 12.1 Lead decision of 2026-10-25 (applied)

The review budget has no default in code and is an explicit field of the rubric file; a file without a required share item with a pass rule is refused (`test_rubric_file_without_required_pass_rule_refused`, `test_no_code_path_supplies_a_review_budget`, `test_missing_binding_param_refuses_before_any_send`); the example file sets `max = 1.0` as a labelled placeholder (section 4.4); `flag_min_confidence` 0.5 is a member of the same file (no code default). **Contract words changed because of it:** (1) section 4.1 adds the optional top-level member `note` (free text, never stored) so that the placeholder can be labelled inside the file; before, the format had no place for a label. (2) section 4.4 and the test `test_shipped_example_rubric_file_loads_builds_and_runs` (new). (3) `build_configuration` refuses a spec without the budget with `rubric_budget_missing` (new, section 3.3). No test was weakened. The consent value `obtained` is the CLI's input; the tests use `unknown` (they pin that it is passed through, never changed).

### 12.2 Findings

- **F13 (blocks rule 17 as written).** `ConfigSnapshot` of schema 0.0.1 requires "an enabled binding of an `llm_judge`, boolean, per_conversation, `equals_true` definition that uses the prompt variable `expected_outcome`", and every such check must be a required rubric item (`snapshot._check_expected_outcome`). A recorded run therefore cannot be built with only the share as its required item: ADR 0023 rule 17 ("exactly one required item") and SCOPE F3 miss this. Recommendation (pinned, section 3.3): an **inert expected-outcome check** that is never run (no result, no verdict, `judge_model_ref {none, not_run}`, a prompt that says so) as the first, required rubric item; the share is the second required item. The honest text lives in the Evaluator and the prompt. Alternative: wait for the 0.0.2 schema (BL-182 widened: a snapshot without the check). ADR 0023 rule 17 and its test names need the same edit.
- **F14.** The VoicEra call id as `Run.external_refs` makes a second run of the same call a `ReimportConflict` in the store (ADR 0022 Decision 11), which breaks R2 and R3 of the noise-floor measurement (P3-31) in one store. Recommendation (pinned): the Run holds no external reference; the call id stays on the export transcript (`provider_call_ref`). Trade-off: no store-level protection against scoring one call twice by mistake (it costs STT calls only; each run is a distinct measurement). Alternative: one `--out` per run.
- **F15.** `AgentTarget` requires an `Endpoint`; a recording has none. Pinned: `wss://recorded-call.invalid/` (RFC 6761 reserved, can never resolve), nothing declared.
- **F16.** Each `build_configuration` call makes fresh ids, so its snapshot digest is new; two CLI invocations never share a snapshot unless the CLI reuses the value (one process) or reloads it from the store. Cross-run comparison therefore reads the `tracer.flags/1` parameters, not snapshot digests. Backlog candidate: snapshot reuse by rubric-file digest.

### 12.3 Ambiguities

1. **Pipeline outcome and exit code names.** SCOPE puts the non-zero exit in P3-20 but the exit codes in P3-22. Pinned: `outcome` in {`scored`, `not_scored`, `stt_failed`}, `exit_code` 0, 2, 3 on `PipelineResult`; batch E may add codes.
2. **"Owner definition versions."** Pinned: version-1 definitions built from the templates and the file (no template is stored, so there is no earlier version to supersede). Alternative: store the three templates as version 1 and the owner's share as version 2 of the same id (more records, same facts).
3. **Scorer version on a definition.** A `MetricDefinition` has no scorer-version field, so "a definition whose version differs stops the run" (rule 3) cannot be checked; only the unknown `implementation_ref` refuses. `ScorerInfo.version` records the distribution version.
4. **Ids and "byte-identical" (rule 18).** UUIDv7 ids are fresh per record, so the byte-identical claim holds only for the stored round trip (`test_run_round_trips_through_store_byte_identical`) and, for a rerun, modulo ids. No id injection is pinned. Recommendation: reword rule 18 to "identical after ids are masked, the snapshot byte for byte".
5. **Persona text.** `Persona` has only `language` and `accent`; rule 17's persona text cannot exist; the Evaluator text carries it.
6. **Threshold stays a JSON number.** The file value `0.5` is stored as the float in the three bindings and read as `Decimal("0.5")`; a Decimal or text threshold in a hand-made spec is `binding_param_invalid`.
7. **Cost in the bundle.** The pipeline (pure core) cannot import `tracer_evalproviders.cost`, so cost is an injected `CostBuilder`; the CLI composes it from `compute_stt_cost` and the optional user USD catalog (cost `not_available` otherwise). Not tested end to end here (batch E).
8. **STT-language results are `error`, not `not_applicable`** (ADR 0023 rule 14, Ambiguity 1): unchanged; the CLI says "not scored: unsupported language detected".
9. **An export read with `accept_export_warnings=true` that yields no transcript** (an empty file) is treated as no export (`agent_transcript` missing). Not separately tested.
10. **`levenshtein_too_large` and `alignment_too_large`** are named here because ADR 0023 rule 15 says "its own code"; the caps are batch C's constants.
11. **The postgres case** could not be run in this session (no server); its teeth are the sqlite case plus the shared code path. The lead's Postgres CI job runs it.
12. **The batch B boundary** (`tracer_evalcore.stt`, `SttEngine`, `SttOutcome`, `SttContext`, the `Stt...` errors) was read from the tree as it stands (it exists there); the tests build records with the evalschema models and never import `tracer_evalproviders`. If batch B changes a name, `FakeEngine` is the one place to adapt.

## 13. Test inventory (every SCOPE name, exact)

| Task | Names | File |
|---|---|---|
| P3-18 | `test_shipped_definitions_have_no_pass_rule`, `test_agreement_unit_is_ratio_never_wer_or_cer`, `test_definitions_requires_transcript_and_agent_transcript`, `test_unknown_scorer_refuses_before_run`, `test_rubric_file_without_required_pass_rule_refused`, `test_missing_binding_param_refuses_before_any_send`, `test_metrics_evaluate_agent_outputs_only` | `test_recorded_definitions.py` |
| P3-18 extra | `test_scorer_registry_entries_carry_the_distribution_version`, `test_rubric_file_refusals_carry_one_stable_code` (36), `test_rubric_file_pass_rule_shape_is_range_with_max_only`, `test_rubric_file_is_parsed_exactly_and_without_defaults`, `test_rubric_spec_has_no_defaults`, `test_binding_parameters_equal_in_the_three_bindings`, `test_build_configuration_refuses_bad_values_and_a_language_without_a_default`, `test_configuration_snapshot_is_a_valid_recorded_scoring_snapshot`, `test_configuration_rubric_items_and_the_inert_expected_outcome_check`, `test_configuration_rubric_follows_the_file_for_optional_items`, `test_configuration_evaluator_and_persona_say_what_the_run_is`, `test_configuration_digests_verify_and_ids_are_fresh`, `test_recorded_project_and_agent_target_are_honest_records`, `test_shipped_example_rubric_file_loads_builds_and_runs`, `test_rubric_file_note_is_optional_text_and_nothing_else`, `test_no_code_path_supplies_a_review_budget`, `test_every_test_named_by_the_scope_rows_of_batch_d_exists` (4) | same |
| P3-19 | `test_results_pass_check_result_against_definition`, `test_flags_metadata_validates_against_tracer_flags_1`, `test_flags_metadata_has_no_text_and_no_metadata_flags`, `test_evidence_refs_resolve_in_bundle`, `test_transcript_ids_required_with_two_transcripts`, `test_language_source_declared_from_export`, `test_empty_export_is_error_no_tokens_not_zero`, `test_empty_stt_is_error_no_tokens_not_zero` | `test_recorded_scoring.py` |
| P3-19 extra | the list at the end of section 7, and the 10 functions of `test_flags_document.py` (section 5) | same, `test_flags_document.py` |
| P3-20 | `test_pipeline_bundle_passes_check_run_bundle`, `test_pipeline_observed_tier_is_t0`, `test_pipeline_started_at_absent_never_ingest_time`, `test_pipeline_is_deterministic_given_same_stt`, `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit` | `test_recorded_pipeline.py` |
| P3-20 extra | run record, project and agent target, privacy markers, instant and no clock, signature without defaults, one engine call with plan and context, chunking, stereo, export read before the engine, engine configuration errors propagate, warnings order, cost builder (2), argument checks, missing inputs (4 outcomes, named `test_pipeline_without_audio_is_missing_input_transcript` and its siblings), zero-frame recording, instruction-like text, text canary, a 3,000-token call, exit codes | same |
| P3-21 | `test_run_round_trips_through_store_byte_identical`, `test_store_file_is_0600`, `test_pipeline_bundle_stores_on_both_backends` (`[sqlite]`, `[postgres]`), `test_key_never_in_store_and_report_outputs` (store channel) | `test_recorded_store.py` |
| P3-21 extra | every outcome round trips (5), a failed STT and an error result, one snapshot for two runs, `test_rescoring_the_same_call_stores_a_second_run`, project and target stored by the call, directory created and migrated, store module is the only importer | same |

The SCOPE rows name no `test_tdeg_*` in P3-18 to P3-21; the T-DEG names stay in P3-27 (batch F) and reuse `score_recorded_call`. The outcomes they need are already pinned above under `test_pipeline_*` names.

## 14. Required changes to existing tests (named spec change; qa did not edit them; tightening only, nothing removed that a test still asserts)

Two existing tests assert that a pending name leaves `PENDING` in the commit that adds its test (`assert not PENDING & defined`), so they go red the moment the batch D files are moved. The edit, to be made in the same commit as the move (`oracle-spec-change.patch` is the reference):

- `packages/evalcore/tests/test_adr0023_recorded_scoring.py`: remove 21 names from `PENDING`: `test_agreement_unit_is_ratio_never_wer_or_cer`, `test_definitions_requires_transcript_and_agent_transcript`, `test_empty_export_is_error_no_tokens_not_zero`, `test_empty_stt_is_error_no_tokens_not_zero`, `test_evidence_refs_resolve_in_bundle`, `test_flags_metadata_has_no_text_and_no_metadata_flags`, `test_flags_metadata_validates_against_tracer_flags_1`, `test_language_source_declared_from_export`, `test_metrics_evaluate_agent_outputs_only`, `test_missing_binding_param_refuses_before_any_send`, `test_pipeline_bundle_passes_check_run_bundle`, `test_pipeline_is_deterministic_given_same_stt`, `test_pipeline_observed_tier_is_t0`, `test_pipeline_started_at_absent_never_ingest_time`, `test_results_pass_check_result_against_definition`, `test_rubric_file_without_required_pass_rule_refused`, `test_run_round_trips_through_store_byte_identical`, `test_shipped_definitions_have_no_pass_rule`, `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit`, `test_transcript_ids_required_with_two_transcripts`, `test_unknown_scorer_refuses_before_run`.
- `packages/evalproviders/tests/test_evalproviders_adr0024.py`: remove `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit` from `PENDING`.

Before: the set contains the names; after: it does not (the sets shrink, the guards that PENDING is a subset of the SCOPE names and that the ADRs still cite them keep holding). ADR text edits that this batch makes necessary and qa did not make: ADR 0023 rule 17 (two required items, F13; no Persona text, ambiguity 5), rule 18 (identical modulo ids, ambiguity 4) and the `Run` without external references (F14); the optional `note` of the rubric file.

## 15. Coverage gaps and risks against the gate

- The inert check (F13) and the placeholder endpoint (F15) are 0.0.1 workarounds that read oddly; they are pinned so that nobody removes them by accident, and need the lead's acceptance (or the 0.0.2 schema).
- The oracle's `uv`-built snapshot sealing is about 40 lines; a dev who seals differently is free to, provided the digests verify (the tests build nothing by hand).
- No test runs the Postgres case; no test uses a real WAV of a call, a real Sarvam body or the private fixture; all text is invented and not natively reviewed (BL-169): the tests prove the plumbing, not Marathi quality.
- Timing: the 3,000-token call and the 40-example property run in about two seconds each; no timing assertion.
- The cost path through a real user catalog is batch E (P3-22) and the batch B tests; here only the injection point is pinned.
- The scorer does not guard `LoneSurrogateError` of the normaliser: unreachable, because `Turn.text` (`VerbatimText`) and the readers refuse lone surrogates before the scorer runs.

## 16. Evidence (`docs/phase3/evidence/red/batch-d/`)

Counts (2026-10-26). Everything ran in a scratch copy of the repository under the session scratchpad, one environment per Python through `UV_PROJECT_ENVIRONMENT` (the shared `.venv` was not used and stays on its default Python); the real tree changed only under `docs/phase3/`: nothing was created or edited under `packages/`.

- red (`red.py3.11.log` to `red.py3.14.log`), the staged tests against the tree without the batch D modules (the oracle files moved away): 223 collected, `--tb=line`: **217 failed, 1 error, 4 passed, 1 deselected (`[postgres]`)** on 3.11, 3.12, 3.13 and 3.14. Reasons on 3.13: 217 `ModuleNotFoundError` for the module the test loads (`tracer_evalcore.definitions` 79, `.flagsdoc` 78, `.rubricfile` 42, `tracer_cli.store` 8, `.pipeline` 6, `.scoring` 2, `.registry` 2; one of the `store` ones is the error of the fixture that opens the store) and 1 assertion (the store module is the only importer of `tracer_evaldb`: the set is empty). The 4 that pass are the guards `test_every_test_named_by_the_scope_rows_of_batch_d_exists[P3-18..P3-21]`: they read SCOPE and the staged test files only (their teeth are the mutants of the file lists, not of the code).
- oracle (`oracle.py3.11.log` to `oracle.py3.14.log`), a hand-written minimal implementation (`oracle-src.patch`: the six evalcore modules, `tracer_cli/store.py` and the example file, about 1,000 lines; not lint-clean or strictly typed, the tests are; the dev owns the real code): `packages/evalcore/tests` and `packages/cli/tests` on 3.11, 3.12, 3.13 and 3.14: **571 passed, 1 deselected** each (the 223 new tests less the deselected one, plus the earlier batch A and C tests of those directories).
- whole workspace with the oracle and the PENDING tightening of section 14 (`oracle-spec-change.patch`), `--ignore=packages/evalproviders/tests` (batch B is being implemented in parallel; its staged tests are red in the tree): `oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`: **11,593 passed, 1,288 deselected** on each. Without the spec change the two PENDING tests fail as section 14 says; no other earlier test changed.
- mutants (`mutants.txt`): **130 mutants of the oracle, 125 caught, 5 equivalent (with reasons), 0 survived**: across the registry, the rubric file, the definitions, the flags document, the scoring, the pipeline, the store (including the key canary) and the lead's budget decision. The first run left 13 survivors; each led to a new test (the totals and zero-width rules of the document, a non-mapping document, one audio range shared by two flags, a spy on the document validator, bundle ordering, the configured language reaching the engine, the declaration instant) or to the mutant being corrected.
- the staged tests alone: `ruff check`, `ruff format --check` and `mypy` (repository configuration) pass on the seven files; no non-ASCII byte in a staged file or in this contract.

Not run: Python 3.15 (non-blocking); GitHub-hosted execution; Windows; the `[postgres]` case (no server); any real send (owner-gated). Logs are scrubbed of home and scratch paths and are under 5,000 lines each.
