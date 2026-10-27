<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch J, part 1: the LLM judge as an uncalibrated signal, documents, pure core and providers (P4-J01 to P4-J16)

Status: red-first contract from `qa`. Part 1 of batch J covers J0 (docs and ADR tests, P4-J01 to
P4-J05), JA (the pure core in `tracer-evalcore`, P4-J06 to P4-J11) and JB (the providers in
`tracer-evalproviders`, P4-J12 to P4-J16). Part 2 (P4-J17 to P4-J32: the `simulate` and `judge`
commands, report, store, labels, injection and T-DEG suites, end to end, audit, measurement, docs)
is not in this batch. Batch E is committed (7699bd4); its names are reused as they are. Every test
of part 1 is written against this file. The oracle (the minimal implementation the tests were
proved against) is `oracle-src.patch` next to the evidence: a reference for the dev, not code to
copy blindly. The schema package is frozen: nothing here changes it.

Open owner decisions stay open: O-J1 to O-J6 come before the first real judge request (P4-J28), none
before this batch. No test sends a real request: everything uses fakes, `httpx.MockTransport`, or the
loopback fake server of the tests. No test holds a key (canaries are built from parts).

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-j1/staging/` and not under `packages/`:
`tests/evalcore/` (11 test files, the support module `evalcore_p4j_support.py` and the fixture
`fixtures/golden_mr.json`), `tests/evalproviders/` (4 test files and `evalproviders_p4j_support.py`),
`tests/evalsim/` (1 test file and `evalsim_support_openai_server.py`), `docs/adr/0030-...md` (the
final text of the ADR) and `docs/security/P4-J-providers.md` (the dossier). The dev's first acts, in
order:

1. Copy each staged test file and support module to `packages/<package>/tests/` (same name; the
   fixture to `packages/evalcore/tests/fixtures/`). None is edited by the dev.
2. Apply `spec-change.diff` with `git apply` (exact-string edits; it moves ADR 0030 to `docs/adr/`,
   writes the index revision 52, amends ADRs 0023, 0024 and 0027, adds the dossier, adds the cost
   test to the SCOPE row P4-J12 and makes the named test edits of section 14).
3. Create nothing under `packages/` that has no `pyproject.toml`; all new modules go into the three
   existing packages (section 1). No dependency is added (`uv.lock` is unchanged).
4. Apply the repository wiring of section 12 (`.gitignore` and the guard script).
5. Run `uv sync --locked --all-groups && uv run --locked pytest -q`.

Marker use: `test_evalsim_judge_loopback.py` carries `pytest.mark.loopback` (its module is added to
`LOOPBACK_MODULES`, section 14). No other new test opens a socket.

## 1. Package ownership

| Package | New or changed modules |
|---|---|
| `tracer-evalcore` (pure, no async, no network) | NEW `golden`, `judgewindow`, `judgeprompt`, `judgeoutput`, `judge`, `judgerecords`, `judgelabels`; CHANGED `_schemalite` (also reads `minItems`, `maxItems`, `minLength`, `maxLength` and the type `number`; the bounds `minimum` and `maximum` also apply to a float; a bool is neither an integer nor a number; a `$` in a pattern is the end of the text, so a final line feed does not match) and `simscore` (`build_simulated_configuration(..., judge=None)`, `SimulatedConfiguration.judge` and `.judge_keys()`) |
| `tracer-evalproviders` | NEW `judgeprofiles`, `judgehosts`, `openai_compat`, `judgeexchange`; CHANGED `sarvam/_post.py` (`JsonPoster` takes `api_key=None` with `auth_scheme` `none`, and `auth_scheme`, `auth_header`; `post` takes `extra_headers`; `Reply` gains `status` and `request_id`, both with defaults; nothing else changes for the Sarvam adapters), `secrets.py` (`load_named_key`), `simcalllog.py` (the stage `judge`, a line that counts neither characters nor audio seconds) |
| `tracer-evalsim` | tests only (the loopback module and the fake server) |
| repository | `.gitignore` and `scripts/guard_private_fixtures.py` (section 12) |

No module of `tracer-evalcore` imports `httpx`, a socket, a clock or the process environment. The
judge client is Tracer's own code on `httpx` (ADR 0030 rule 12); the `openai` and provider SDKs are
not dependencies.

## 2. `tracer_evalcore.golden` (P4-J04, P4-J06; ADR 0030 rules 1 to 3)

```python
GOLDEN_SET_FORMAT = "tracer.golden_set/1"
GOLDEN_SET_JSON_SCHEMA: Mapping[str, Any]   # equal to the block of the ADR (section 15)
MATCH_MODES = ("all_facts", "equivalent_answer")
MAX_GOLDEN_ITEMS = 100; MAX_FACTS_PER_ITEM = 10; MAX_FACT_CHARS = 300; MAX_EXPECTED_ANSWER_CHARS = 1000
class GoldenSetError(InputRefusedError): code: str        # str(error) == code; never a value
@dataclass(frozen=True) class GoldenItem:  id, scripted_turn_index, question, match_mode,
    expected_facts: tuple[str, ...], expected_answer: str | None, needs_native_review: bool
@dataclass(frozen=True) class GoldenSet:   key, language, items: tuple[GoldenItem, ...], sha256: str
def parse_golden_set(text: str) -> GoldenSet
def bind_golden_set(golden: GoldenSet, evaluator: SimEvaluatorSpec, *, caller_mode: str,
                    barge_in_turns: Collection[int] = ()) -> None
```

`parse_golden_set`: strict JSON (`loads_strict`; a repeated key is `golden_not_json`), closed keys,
the version member, then the caps; `sha256` is the SHA-256 of the UTF-8 bytes of `text`. Every text
(`question`, each fact, the expected answer) passes the character table `mr-caller-charset/1`
(`callerline.check_caller_reply`, `language="mr"`) and is a single line: a text with a line feed is
refused before the check. Order of checks: JSON, object, `format`, missing key (in sorted order),
unknown key, language, key, items (a list of 1 to 100), then each item in order (object; mode; key set of
the mode; id; turn index; question; caps; texts), then repeated ids.

Refusal codes (stable, listed here; none quotes a value): `golden_not_json`, `golden_shape`,
`golden_format`, `golden_missing_key`, `golden_missing_language`, `golden_missing_items`,
`golden_unknown_key`, `golden_language_not_supported`, `golden_invalid_key`, `golden_items_count`,
`golden_item_shape` (not an object, wrong key set for the mode, a field of the wrong type),
`golden_item_invalid_match_mode`, `golden_item_invalid_id`, `golden_item_repeated_id`,
`golden_item_invalid_turn_index` (an integer from 0 to 2**53 - 1, never a bool),
`golden_item_invalid_question` (1 to 2,500 characters), `golden_facts_count` (1 to 10, a list),
`golden_fact_too_long`, `golden_answer_too_long`, `golden_text_empty`, `golden_text_multiple_lines`,
`golden_text_not_nfc`, `golden_text_character_not_allowed`, `golden_text_no_script_letter`.
`bind_golden_set` (step 1 of the command, before any key): `golden_caller_not_scripted` (checked first;
anything but `"scripted"`, so the LLM and the replay brain are refused, Ambiguity 1), then per item in
order `golden_turn_missing`, `golden_question_mismatch` (byte for byte against
`evaluator.scripted_turns[index].text`), `golden_turn_barge_in`.

The fixture `golden_mr.json` (P4-J04): ASCII (Devanagari as `\u` escapes, as `sim_mr.json`), a
`tracer.golden_set/1` document under `golden_set` with 10 items bound to the turns 0 to 9 of the
scenario `sim-mr-s1` of `sim_mr.json` (8 `all_facts`, 2 `equivalent_answer`), every item and every
other record marked `needs_native_review: true`; `plants`: 54 agent replies (kinds `exact_facts`,
`paraphrase`, `number_words`, `fact_missing`, `fact_contradicted`, `off_topic`, `empty`), each with the
label the apparatus intends (`intended_verdict`, `intended_reason_code`, `intended_facts`, or
`intended_error: answer_window_empty` for an empty reply), which the output validator accepts;
`reason_code_wording` (the eight codes in mr and en) and `labeller_instructions` (mr and en). The
wording is the indic-linguistics agent's to review later; nothing here is a native-reviewed text.

## 3. `tracer_evalcore.judgewindow` (P4-J07; ADR 0030 rule 4)

```python
WINDOW_STATUSES = ("ok", "question_not_sent", "answer_window_empty")
@dataclass(frozen=True) class AnswerWindow:  status, open_ms: int | None, close_ms: int | None, text: str,
    turn_seqs: tuple[int, ...], turn_ids: tuple[TurnId, ...], excluded_turns: int, transcript_id: TranscriptId | None
def answer_window(*, scripted_turn_index: int, transcripts: Sequence[Transcript],
                  turns: Sequence[Turn], call_end_ms: int) -> AnswerWindow     # all keyword-only
```

Pure. The question's turn is the `script` transcript turn with `seq == scripted_turn_index`; none, or
one marked `tracer.caller_line_complete: false`, gives `question_not_sent` (open and close `None`).
The window is `[end_ms of that turn, start_ms of the script turn with the next larger seq)`, or up to
`call_end_ms` for the last sent line. It holds the turns of the `our_stt` transcript with role `agent`
and a `start_ms` in the window, in `seq` order, joined by one U+000A (no other transcript, no other role,
no turn without a time). `excluded_turns` counts every such `our_stt` agent turn that starts before the
opening edge (Ambiguity 8). No turn, or only blank texts, gives `answer_window_empty` (edges and the
count are still set). No `our_stt` transcript is a `ValueError` naming `our_stt` (the command decides
earlier, by the run's inputs). `transcript_id` is the `our_stt` transcript's id in every status.

## 4. `tracer_evalcore.judgeprompt` (P4-J08; ADR 0030 rules 5 and 6; BL-141)

```python
TEMPLATE_IDS = {"all_facts": "tracer.judge_prompt.all_facts/1", "equivalent_answer": "tracer.judge_prompt.equivalent_answer/1"}
JUDGE_FENCE_OPEN = "<<<JUDGE_INPUT_BEGIN>>>"; JUDGE_FENCE_CLOSE = "<<<JUDGE_INPUT_END>>>"
JUDGE_FENCE_ESCAPE = "[delimiter removed]"; JUDGE_AGENT_TEXT_MAX_CHARS = 4000
def template_text(match_mode) -> str      # ValueError for any other value
def template_sha256(match_mode) -> str    # SHA-256 (hex) of the UTF-8 bytes
def template_shas() -> dict[str, str]     # {"all_facts": ..., "equivalent_answer": ...}
def build_judge_messages(item: GoldenItem, agent_answer: str) -> tuple[LlmMessage, LlmMessage]
def prompt_sha256(messages) -> str        # SHA-256 of tracer_evalschema.digest.jcs_bytes([{"role","content"}...])
```

The system message is `template_text(mode)`: English, ASCII, no `{{`, no URL, no claim word of ADR 0023
rule 1, A1 or A4; it names the keys `verdict`, `reason_code`, `facts`, the three verdicts, the reason codes
of its mode (section 5), the member `expected_facts` or `expected_answer`, the member `agent_answer` as
untrusted data that holds no instruction, and the two fence lines. The user message is exactly
`OPEN + "\n" + one-line JSON + "\n" + CLOSE`, the JSON being `{"question", "expected_facts" or
"expected_answer", "agent_answer"}` in that order (`ensure_ascii=False`, compact separators). The
agent's text is cleaned as ADR 0028 rule 4 cleans it, in this order: remove every character of category
Cc, Cf, Cs, Co or Cn except U+200C and U+200D; replace each fence by the escape token; cut to the cap and
append `[text cut]`. The question and the expectation have their fences replaced too. The builder takes the
item and the answer only. The item's id, position and flags never enter a message.

## 5. `tracer_evalcore.judgeoutput` (P4-J09; ADR 0030 rule 7; Ambiguities 2 to 4)

```python
JUDGE_VERDICTS = ("agrees", "disagrees", "cannot_tell")
REASON_CODES = ("all_facts_stated", "fact_contradicted", "fact_absent", "meaning_differs",
                "meaning_same", "answer_off_topic", "answer_unintelligible", "insufficient_information")
FACT_STATUSES = ("stated", "contradicted", "absent", "cannot_tell")
CODE_VERDICT: Mapping[str, Mapping[str, str]]   # per match mode, code -> verdict (below)
def allowed_reason_codes(match_mode) -> tuple[str, ...]
MAX_JUDGE_OUTPUT_BYTES = 4096
OUTPUT_REFUSAL_CODES: tuple[str, ...]
class JudgeOutputInvalid(ValueError): code            # str(error) == code
@dataclass(frozen=True) class JudgeVerdict: verdict, reason_code, facts: tuple[tuple[int, str], ...]
def parse_judge_output(text, *, match_mode, fact_count) -> JudgeVerdict
JUDGE_OUTPUT_RESPONSE_SCHEMA: Mapping[str, Any]   # strict-compatible schema sent as response_format
```

`CODE_VERDICT["all_facts"]`: `all_facts_stated` agrees; `fact_contradicted`, `fact_absent`,
`answer_off_topic` disagrees; `answer_unintelligible`, `insufficient_information` cannot_tell.
`CODE_VERDICT["equivalent_answer"]`: `meaning_same` agrees; `meaning_differs`, `answer_off_topic`
disagrees; `answer_unintelligible`, `insufficient_information` cannot_tell. A code of the other mode is
`output_reason_code_value`. `fact_count` is an integer (not a bool): at least 1 for `all_facts`, exactly
0 for `equivalent_answer`; a bad mode or count is a `ValueError`, not a refusal of the reply. Never repaired:
only white space around the JSON object is tolerated (it is JSON); a code fence, prose, a byte order mark,
two objects, a repeated key, `NaN` are `output_not_json`.

Checks in order, with the code of the first that fails: size in UTF-8 bytes (`output_too_large`, before any
parsing), JSON by `loads_strict` (`output_not_json`), an object (`output_not_object`), exactly the keys
`verdict`, `reason_code`, `facts` (`output_keys`), the verdict in its closed set (`output_verdict_value`), the
reason code in the closed set of the mode (`output_reason_code_value`), `facts` a list of objects with exactly
`index` (an int, not a bool) and `status` (a str) (`output_facts_shape`), each status in its closed set
(`output_fact_status_value`), the indexes exactly `0..fact_count-1` in order (`output_facts_index`), or an empty
list in the other mode (`output_facts_not_empty`), the verdict against the facts (`agrees` iff every fact is
`stated`; `disagrees` iff a fact is `contradicted` or `absent`; `cannot_tell` iff none of those and one is
`cannot_tell`: `output_verdict_facts_inconsistent`), the reason against the verdict by `CODE_VERDICT`
(`output_reason_verdict_inconsistent`). No refusal quotes the text.

## 6. `tracer_evalcore.judge` (P4-J09 driver, P4-J11; ADR 0030 rules 7, 16, 17, 18)

`__all__` is sorted. Errors, all raised outside any handler, facts only, no cause and no context
(`_engine_errors.FactError`: keyword-only `index, status, kind, code, request_id, attempts, reason`):
`JudgeError(FactError)` and its direct subclasses `JudgeAuthError`, `JudgeRequestRejected`,
`JudgeRateLimited`, `JudgeServerError`, `JudgeTransportError`, `JudgeResponseInvalid`,
`JudgeCapReachedError` (`reason` is `judge_requests` or `judge_tokens`), and `JudgeConfigurationError(JudgeError,
ValueError)` with `JudgeKeyMissingError`. The classes `Judge*` are the names `JsonPoster` builds from its
`prefix="Judge"`.

```python
UsageSink = tracer_evalcore.stt.UsageSink
@dataclass(frozen=True) class JudgeParams:   # no defaults; the order of the fields is the contract
    model, max_tokens, temperature, seed, reasoning_effort, provider_slug, extra_headers
@dataclass(frozen=True) class JudgeContext:  run_id, created_at
@dataclass(frozen=True) class JudgeRequest:  index, item_id, attempt, match_mode, fact_count, messages, params
@dataclass(frozen=True) class JudgeOutcome:  text, finish_reason, reported_model, request_id,
                                              exchange_sha256, params_sent, usage, failure
class JudgeEngine(Protocol):            complete(self, request, context) -> JudgeOutcome
class UsageStreamingJudgeEngine(Protocol): streams_usage: bool; complete(self, request, context, *, on_usage=None)
def judge_once(engine, request, context, *, on_usage) -> JudgeOutcome
JUDGE_ERROR_CODES = ("judge_undetermined", "judge_output_invalid", "judge_request_failed", "judge_auth_failed",
                     "judge_requests_cap_reached", "judge_tokens_cap_reached", "question_not_sent", "answer_window_empty")
@dataclass(frozen=True) class JudgeItemResult: item_id, state, verdict, reason_code, facts, error_code, window,
    attempts, regenerated, prompt_sha256, exchange_sha256, reported_model, params, usage, failure; property stops_run
def judge_item(*, engine, item, window, params, context, first_index, on_usage) -> JudgeItemResult
```

Validation: `JudgeParams.model` as `LlmRequest.model`; `max_tokens` an int from 1 to 2**53 - 1; `temperature`
`None` or an int or float (not a bool) from 0 to 2; `seed` `None` or a non-negative int; `reasoning_effort` `None`
or `[a-z][a-z0-9_]{0,31}`; `provider_slug` `None` or `[a-z0-9][a-z0-9._/-]{0,63}`; `extra_headers` a tuple of
`(name, value)` pairs, the name one of `anthropic-workspace-id`, `HTTP-Referer`, `X-OpenRouter-Title`, the value
printable ASCII of 1 to 200 characters. `JudgeRequest`: `attempt` 1 or 2, `fact_count` at least 1 for
`all_facts` and 0 for `equivalent_answer`, `messages` a tuple of two `LlmMessage`, `params` a `JudgeParams`,
`item_id` a snake-case id. `JudgeOutcome`: a text or a failure, exactly one (an empty text is a text);
`usage` holds `UsageRecord`s of stage `judge` and domain `evaluation` (`ValueError` for another stage,
`TypeError` for another type). Every violation is a `ValueError` or `TypeError` that quotes no value.

`judge_item`: a window that is not `ok` gives an error judgement with no request (`question_not_sent`,
`answer_window_empty`, attempts 0). Otherwise the messages are built once, and the request is sent as attempt
1 (`index = first_index`); an invalid reply (the validator refuses it, or `finish_reason == "length"`) gets the
identical request as attempt 2 (`index = first_index + 1`), never a repair; a second invalid reply is an error
`judge_output_invalid` (attempts 2, `regenerated` True). A failure of the request is an error without a
regeneration: `JudgeAuthError` gives `judge_auth_failed`, `JudgeCapReachedError` gives
`judge_requests_cap_reached` or `judge_tokens_cap_reached` by its reason, any other `JudgeError` gives
`judge_request_failed`. `state` is `verdict` (agrees or disagrees), `cannot_tell` (the verdict is kept,
`error_code` is `judge_undetermined`) or `error`. The usage of each answered request reaches `on_usage`
before the next request (by the engine if it streams, else by `judge_once`); `usage` lists the records;
`prompt_sha256`, `exchange_sha256`, `reported_model` and `params` come from the last outcome. The invalid
replies are counted by `attempts`, `regenerated` and the state, never kept as text. `stops_run` is true for an
authentication failure and for a cap.

## 7. `tracer_evalcore.judgerecords` and `simscore` (P4-J10; ADR 0030 rule 8, ADR 0027 rule 12a)

```python
JUDGE_KEY_PREFIX = "judge_golden_"; CALIBRATION = "uncalibrated"; CONFIDENCE = "none_claimed"
JUDGE_PROFILE_FORMAT / JUDGE_PROFILE_METADATA_KEY = "tracer.judge_profile" / JUDGE_PROFILE_JSON_SCHEMA
JUDGE_FORMAT / JUDGE_METADATA_KEY = "tracer.judge" / JUDGE_JSON_SCHEMA
JUDGE_EXCHANGE_FORMAT / JUDGE_EXCHANGE_JSON_SCHEMA
JUDGE_ERROR_MESSAGES: Mapping[str, str]       # one fixed ASCII sentence per code, no claim word
class JudgeDocumentError(InputRefusedError): code, pointer   # names a place, never a value
def judge_metric_key(item_id) -> str
def build_judge_profile_document(*, profile, host, path, model, params, max_judge_requests, max_judge_tokens,
                                 key_env, golden) -> dict      # fills format, golden_set, template_sha256, calibration; validates
def validate_judge_profile_document(document) -> None
def build_judge_document(*, verdict, reason_code, facts, window_turn_seqs, excluded_turns, attempts,
                         regenerated, exchange_sha256) -> dict   # validates
def validate_judge_document(document) -> None
def judgement_document(judgement: JudgeItemResult) -> dict
def build_exchange_document(*, messages, response_body, request_id, status) -> dict
def validate_judge_exchange_document(document) -> None
@dataclass(frozen=True) class JudgeSetup:  golden: GoldenSet, model_ref: Mapping[str, str], profile_document: Mapping[str, Any]
def judge_definitions(setup, *, created_at) -> list[MetricDefinition]
def judge_results(*, snapshot, run, transcripts, turns, judgements: Mapping[str, JudgeItemResult], created_at) -> tuple[MetricResult, ...]
def with_judge_results(bundle, results, usage) -> RunBundle    # appends, then check_run_bundle
```

Each validator is `_schemalite.first_violation` over the shipped schema plus the cross-field rules of
section 15 (a `JudgeDocumentError` with the code `judge_profile_invalid`, `judge_invalid` or
`judge_exchange_invalid` and the pointer of the first fault). `judgement_document` is
`build_judge_document` over a judgement: `window_turn_seqs` from the window, `exchange_sha256` from the
judgement, `verdict`, `reason_code` and `facts` from it (null and empty for an error with no verdict).

`judge_definitions`: one `MetricDefinition` per item, in golden order: key `judge_golden_<id>`; kind
`llm_judge`; `value_type` `enum`, scale options `[agrees, disagrees]`; no pass rule; `per_conversation`;
`requires` `[transcript, turn_timing]`; `languages` `[mr]`; `script_sensitive` false; `prompt_template` the
template of the item's mode; `prompt_variables` `[]`; `judge_model_ref` the setup's `model_ref`; parameters
`question` (`string`) and `expected_facts` (`json`) or `expected_answer` (`string`), none with a default.
`simscore.build_simulated_configuration(..., judge=setup)` appends the definitions after the inert check, one
enabled binding each whose `params` hold the item's texts, rubric items that are NOT required (the inert check
stays the only required item), and `metadata["tracer.judge_profile"] = setup.profile_document` beside the sim
profile. `SimulatedConfiguration.metric_keys()` lists the shipped sim metrics only; `judge_keys()` lists the
judge keys in golden order (`()` without a judge). A run without a setup is byte-for-byte what batch E builds.

`judge_results`: one result per judge definition of the snapshot, in order, from the records of the call.
`applicability(definition, run, turns=<every turn of the our_stt transcript>, transcripts=[our_stt])` decides
first, as `simscore` does for the agreement metrics: a `not_applicable` answer (a missing input, another
language) gives that result and needs no judgement; otherwise the item's judgement is required (a missing one
is a `ValueError`). `verdict` states give `scored` with the verdict as the value, `passed` None; `cannot_tell`
and `error` states give `error` with `ResultError(code, JUDGE_ERROR_MESSAGES[code])`. A result that had a
request carries `JudgeInfo(model_ref=<the definition's>, prompt_sha256, params, per_turn=[], confidence None,
reported_model)`; one that had none (`question_not_sent`, `answer_window_empty`) has no `judge`. Evidence is
one `turn` reference per window turn with its `start_ms` and `end_ms`; `transcript_ids` is `[our_stt id]`
(for a call with no `our_stt` transcript: the ids of the other non-script transcripts, else None);
`language_source` and `declared_language_transcript_ids` come from the applicability answer;
`metadata["tracer.judge"]` is the closed document (`judgement_document`). No `RunVerdict`, no `p_yes`.

## 8. The dossier `docs/security/P4-J-providers.md` (P4-J03; ADR 0030 rules 13 and 24)

A table `## Profile status` (`| Profile | Status | Basis |`) and a table `## Facts` (`| Profile | Fact |
Source | Retrieved | Verification |`): a `verified` row has one or more `https://` URLs (separated by `; `)
and an ISO date from 2026-10-28; a `not verified` row has `none` and `none`. A profile is `verified` only if
all its rows are. A table `## Terms and data use (owner gate)` has one row per provider, each "not fetched"
and an owner gate (O-J2), no URL. All seven profiles are `unverified` today; the file states that httpx is
reused and that no provider SDK is a dependency. The facts are those of ADR 0030 "Provider profiles", copied
with their sources; nothing was fetched again. `tracer_evalproviders.judgeprofiles.PROFILE_STATUS` equals the
status table.

## 9. `tracer_evalproviders.openai_compat` (P4-J12; ADR 0030 rules 12, 16, 17, 18; ADR 0024 A10, A13)

```python
JUDGE_MAX_RESPONSE_BYTES = 65_536
MAX_ATTEMPTS, BACKOFF_SECONDS, RETRY_AFTER_MAX_SECONDS    # the objects of sarvam.client, re-exported
def build_judge_http_client(transport) -> httpx.Client    # sarvam.client.build_http_client: no redirect, no environment, TLS verified
@dataclass(frozen=True) class ChatReply: id, model, content, finish_reason, prompt_tokens, completion_tokens,
                                          cached_tokens, reasoning_tokens, system_fingerprint
def parse_chat_response(body: bytes, *, index: int, attempts: int) -> ChatReply | JudgeResponseInvalid
class OpenAICompatJudge:    # a UsageStreamingJudgeEngine; streams_usage = True
    def __init__(self, api_key: ApiKey | None, *, profile, url, call_log, max_requests, max_tokens,
                 exchanges=None, transport=None, sleep=time.sleep, monotonic=time.monotonic)
    complete(request, context, *, on_usage=None) -> JudgeOutcome;  close()
def create_judge_engine(bound: BoundEndpoint, *, call_log, max_requests, max_tokens, exchanges=None,
                        transport=None, sleep=time.sleep, monotonic=time.monotonic) -> OpenAICompatJudge
```

`complete`: the caps first (`max_requests`: the number of `complete` calls so far reaching it gives
`JudgeCapReachedError(reason="judge_requests")`; `max_tokens`: reported usage so far plus this request's
`max_tokens` above the cap gives `reason="judge_tokens"`; nothing is sent), then
`judgeprofiles.build_request_body`, then one `JsonPoster` post (stage `judge`, the retries, bounded
`Retry-After` and taxonomy of ADR 0024 rules 9 and 10 with `Judge*` errors), then, for a 200 reply, the
exchange file (if `exchanges` is given and the body is UTF-8) and the parse. `JsonPoster` is generalised:
`auth_scheme` is `"header"` (the default, `api-subscription-key`), `"bearer"` or `"none"`; `post` takes the
profile's optional `extra_headers`; `Reply` gains `status` and `request_id`. `parse_chat_response` reads `id` (1 to 256 characters), `model` (1 to 256, kept
verbatim), exactly one choice, a choice-level `error` object (reason `choice_error`; `code` kept only if it is a
short machine key), `message.content` a string (an empty one is a string), `finish_reason` a string,
`usage.prompt_tokens` and `completion_tokens` integers from 0 to 2**53 - 1, an optional
`prompt_tokens_details.cached_tokens` (absent, `None` or an empty details object mean not reported; never 0; at
most `prompt_tokens`), an optional `completion_tokens_details.reasoning_tokens` and `system_fingerprint`; other
members are ignored and never kept. Reasons: `not_json`, `not_object`, `id_invalid`, `model_invalid`,
`choices_invalid`, `choice_error`, `message_invalid`, `content_invalid`, `finish_reason_invalid`,
`usage_invalid`, `fingerprint_invalid`, plus the poster's `content_type`, `body_too_large`,
`body_undecodable`, `unexpected_status`. A content over 4,096 bytes is NOT a client failure (the validator
refuses it and the item is regenerated).

The usage record of an answered request: `stage` `judge`, `domain` `evaluation`, `source`
`provider_reported`, `provider` the profile name (for example `openrouter/1`), `model` as requested,
`provider_call_ref` `{system: profile, kind: request_id, value: reply id}` (omitted if the id is not a safe
identifier), quantities `input_tokens` (prompt minus cached), `cache_read_input_tokens` (only if reported),
`output_tokens`, `requests` 1; `occurred_at` from the `Date` header; metadata `tracer.request_index`, and
`tracer.seed` with `tracer.seed_guarantee: "none"` when a seed was sent, `tracer.reasoning_tokens`,
`tracer.system_fingerprint`. OpenRouter's `usage.cost` ("Cost in credits") is read nowhere and stored nowhere;
the cost of a judge usage is `not_available` without a USD catalog (`cost.compute_usage_cost`).
`JudgeOutcome.params_sent` is `judgeprofiles.params_as_sent`. A failure has no usage.

## 10. `tracer_evalproviders.judgeprofiles` (P4-J13; ADR 0030 rule 13, ADR 0024 A11)

```python
PROFILE_NAMES = ("anthropic_compat/1", "gemini_compat/1", "xai/1", "openrouter/1", "sarvam_v2/1", "openai/1", "local_loopback/1")
PROFILE_STATUS: dict[str, str]     # the dossier's status table; a plain dict, read at get_profile time
class JudgeProfileError(InputRefusedError): code
@dataclass(frozen=True) class JudgeProfile: name, endpoint, auth ("bearer"|"header"|"none"), auth_header,
    token_field, sends: tuple[str, ...], seed: bool, response_format: bool, reasoning_effort ("required"|"never"),
    provider_object: bool, optional_headers: tuple[str, ...], status, real_provider: bool, dossier
def get_profile(name) -> JudgeProfile                  # "unknown_profile" for anything else
def build_request_body(profile, request) -> dict
def request_headers(profile, key: str | None, extra_headers) -> dict[str, str]
def params_as_sent(profile, request) -> dict[str, Any]
def check_real_run(profile, *, first_request_gate=False) -> None      # "profile_unverified"
def checked_extra_headers(profile, extra_headers) -> dict[str, str]    # "header_not_supported"
```

The table (the members a profile may send, in this order; `temperature` only when set, `seed` only when set and
supported, the others always):

| Profile | Token field | Auth | `sends` | Optional headers |
|---|---|---|---|---|
| `anthropic_compat/1` | `max_tokens` | bearer | model, messages, max_tokens, stream, temperature | `anthropic-workspace-id` |
| `gemini_compat/1` | `max_tokens` | bearer | model, messages, max_tokens, stream, temperature | none |
| `xai/1` | `max_tokens` | bearer | + response_format | none |
| `openrouter/1` | `max_tokens` | bearer | + seed, response_format, provider | `HTTP-Referer`, `X-OpenRouter-Title` |
| `sarvam_v2/1` | `max_tokens` | header `api-subscription-key` | + response_format, reasoning_effort | none |
| `openai/1` | `max_completion_tokens` | bearer | model, messages, max_completion_tokens, stream, temperature, seed | none |
| `local_loopback/1` | `max_tokens` | none | model, messages, max_tokens, stream, temperature | none |

`stream` is always `false`; `n`, `tools`, `logprobs`, `user`, `store`, `metadata` and `functions` are never sent.
`response_format` is `{"type": "json_schema", "json_schema": {"name": "judge_output", "strict": true, "schema":
JUDGE_OUTPUT_RESPONSE_SCHEMA}}` on the three profiles whose documentation shows it (Ambiguity 9). `openrouter/1`
always sends `provider: {"order": [<slug>], "allow_fallbacks": false, "require_parameters": true,
"data_collection": "deny"}`. A setting the profile does not support is a refusal and never dropped:
`seed_not_supported`, `reasoning_effort_not_supported`, `provider_slug_not_supported`, `header_not_supported`;
a missing required one: `reasoning_effort_required` (`sarvam_v2/1`), `provider_slug_required`
(`openrouter/1`). `params_as_sent` is the record of `tracer.judge_profile/1.params` and `JudgeInfo.params`: the
token field, `temperature`, `seed` with `seed_guarantee: "none"`, `reasoning_effort`, `response_format:
"json_schema"` and `provider`, as sent. `request_headers` adds `accept-encoding: identity` and `content-type:
application/json`. `get_profile` raises a `ValueError` for a status outside `verified` and `unverified`.
`check_real_run` refuses an `unverified` profile of a real provider unless `first_request_gate` is true (the
owner-gated first request; the flag's name at the command is part 2's); `local_loopback/1` has
`real_provider` False and is never refused for that.

## 11. `tracer_evalproviders.judgehosts` (P4-J14; ADR 0030 rules 14 and 15, ADR 0024 A12, BL-142)

```python
MAX_JUDGE_ALLOWLIST_BYTES = 65_536; KEY_ENV_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
class JudgeAllowlistError(InputRefusedError): code
@dataclass(frozen=True) class JudgeEndpoint: url, scheme, host, port, path, profile, key_env
@dataclass(frozen=True) class BoundEndpoint: endpoint, profile: JudgeProfile, api_key: ApiKey | None
Resolver = Callable[[str, int], Sequence[str]]; def system_resolver(host, port) -> list[str]   # socket.getaddrinfo; catches OSError only
def parse_judge_allowlist(text) -> tuple[JudgeEndpoint, ...]
def read_judge_allowlist(path) -> tuple[JudgeEndpoint, ...]
def find_judge_endpoint(entries, url, profile) -> JudgeEndpoint
def bind_endpoint(entries, *, url, profile, environ=None, env_file=None, resolver=None, first_request_gate=False) -> BoundEndpoint
```

One entry per line: `<url> profile=<name> key_env=<NAME>`; `#` starts a comment only at the start of the line or after
white space; fields are parsed first (an unknown, repeated, empty or positional token is `allowlist_field_invalid`,
a missing `profile` or `key_env` is `allowlist_field_missing`), then the URL. The URL is exactly
`https://host[:port]/path`: scheme `http` only for a literal loopback address (`allowlist_scheme_not_allowed`
otherwise, a name such as `localhost` included); the scheme and host are lower-cased and the default port is
dropped; no userinfo, query, fragment, `%`, `.` or `..` segment or empty segment, a port from 1 to 65535, a path
of `[A-Za-z0-9._~/-]` (`allowlist_url_invalid`). `allowlist_profile_unknown` for a name outside the seven;
`allowlist_profile_host_mismatch` for `local_loopback/1` on a non-loopback host; `allowlist_key_env_forbidden` for
a key on `local_loopback/1`; `allowlist_key_env_invalid` for a `key_env` that does not match the pattern or that
has the shape of a credential (`tracer_evalschema.credentials.contains_credential_pattern`);
`allowlist_entry_repeated`; `allowlist_key_env_shared` (one variable name for two hosts). `read_judge_allowlist`:
`allowlist_unreadable`, `allowlist_not_a_regular_file` (a link, a hard link, a directory, a FIFO; judged by
`lstat` before any open, then opened with `O_NOFOLLOW`), `allowlist_mode_too_open` (any group or other bit),
`allowlist_too_large`.

`bind_endpoint`, in this order and stopping at the first refusal: `find_judge_endpoint` (a URL that does not
normalise, or is not listed: `host_not_allowed`; listed with another profile: `profile_mismatch`;
`JudgeAllowlistError`), `judgeprofiles.check_real_run` (`profile_unverified`, a `JudgeProfileError`), the
resolution of an entry whose host is not a literal loopback address (a literal address is its own answer; a
name is resolved with `resolver(host, port)`; no answer `dns_no_answer`, an `OSError` `dns_failed`, any answer
that is not a global unicast address, v4-mapped addresses unwrapped, zone ids stripped, an unparsable one
included, `dns_answer_not_public`), then the key by `secrets.load_named_key(entry.key_env)` (an `ApiKey`, or a
`JudgeKeyMissingError` whose `reason` is `key_missing`, `key_invalid`, `key_ambiguous` or an env file reason).
Before the last step the environment, the env file and the resolver are not touched by a refusal of an earlier
step. A rebinding race between this check and the connection is not claimed closed (BL-326).

`secrets.load_named_key(name, *, environ=None, env_file=None) -> ApiKey`: as `load_api_key` for the variable
`name` (with an env file, only that file is read and only that name; two definitions are `key_ambiguous`; the
environment is not consulted), raising `JudgeKeyMissingError(reason=...)` outside any handler.
`OpenAICompatJudge(url=...)` refuses (`ValueError`) a plain `http` URL whose host is not a literal loopback
address.

## 12. `tracer_evalproviders.judgeexchange` (P4-J15; ADR 0030 rule 19, rule 25, I6)

```python
def exchange_directory(out_dir, run_id) -> Path                  # <out>/judge/<run id>
def exchange_file_name(item_id, attempt) -> str                  # item-<id>-<attempt>.json
class JudgeExchangeError(InputRefusedError): code
class ExchangeStore:  directory; __init__(directory); write(item_id, attempt, document) -> str; read(item_id, attempt) -> dict
class ReplayJudge;  def create_replay_judge(directory, *, profile) -> ReplayJudge     # a JudgeEngine
FAKE_SCENARIOS = ("agrees", "disagrees", "cannot_tell", "invalid_once", "invalid", "fenced", "auth_failure")
class FakeJudge;    def create_fake_judge(scenario) -> FakeJudge                      # a JudgeEngine
```

`ExchangeStore`: creates `<out>/judge` and the run directory itself with mode 0700 (each level explicitly); an
existing directory must be a real directory (not a link) with no group or other bit
(`exchange_directory_not_private`). `write` validates the document, opens the file with
`O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW` and mode 0600 and writes `jcs_bytes(document)`; it returns the SHA-256 (hex) of
the bytes written; an existing file (a link included) is `exchange_exists` and is not changed. `read` refuses a
missing file (`exchange_missing`), a non-regular file or a link (`exchange_not_a_regular_file`), a mode with a group
or other bit (`exchange_mode_too_open`), a file over 262,144 bytes or a document that does not validate
(`exchange_invalid`). The item id must be snake case (at most 40 characters) and the attempt 1 or 2 (`ValueError`).
`OpenAICompatJudge` writes one file per answered (HTTP 200) request whose body is UTF-8 and puts the SHA-256 in
the outcome; nothing is written for any other reply.

`ReplayJudge` reads the stored exchange of `(request.item_id, request.attempt)`, requires its `messages` to equal
the request's (`JudgeResponseInvalid(reason="replay_request_differs")`; no file: `replay_missing`), reads the
body with `parse_chat_response` and returns the outcome with the file's SHA-256, `params_sent` from
`params_as_sent(profile, request)`, and NO usage (a replay costs nothing). It takes a directory and a profile only.
`FakeJudge` answers from the request alone (`match_mode`, `fact_count`, `attempt`): `agrees`, `disagrees` (every
fact absent / `meaning_differs`), `cannot_tell` (every fact `cannot_tell`), `invalid_once` (prose on attempt 1,
agrees on attempt 2), `invalid` (prose), `fenced` (valid JSON inside a code fence), `auth_failure`
(`JudgeAuthError(status=401)`); `reported_model` is `fake/<scenario>`, `finish_reason` `stop`, no usage, no
exchange hash; an unknown name is `JudgeConfigurationError(reason="unknown_scenario")`.

Repository wiring (dev): `.gitignore` gains `**/judge/*/item-*-[0-9].json` (the exchange files);
`scripts/guard_private_fixtures.py` gains `JUDGE_EXCHANGE = "tracer judge exchange file"`, found by name
(`(^|/)judge/[^/]+/item-[a-z0-9_]+-[12]\.json$`) in `kind_by_name` and by content (`"format"` with the value
`tracer.judge_exchange/1` in the head) in `kind_by_content`, with the message form of the other kinds. The guard for
labels and the allowlist, and the staged-file test, are P4-J25.

## 13. The loopback fake server (P4-J16; ADR 0030 rule 25)

`evalsim_support_openai_server.py`: `FakeOpenAIServer(replies)`, the standard library's `ThreadingHTTPServer` on
`127.0.0.1` port 0 in a thread, one scripted `Reply(body, status, headers, content_type)` per request, the
requests recorded (`Seen`), `chat_body(content, ...)` for a chat-completions document. The tests carry the
`loopback` marker and drive the real transport of `OpenAICompatJudge` against it with an allowlist entry
`http://127.0.0.1:<port>/v1/chat/completions profile=local_loopback/1` (or a real profile on the loopback address
with `first_request_gate=True`).

## 14. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only)

* S-J (`evalcore_phase4_adr_support.py`): `ADR30`; `SPECS["0030"]` (25 rules, 9 invariants, self test
  `test_adr0030_rules_are_tested`); `SPECS["0024A"]` 9 to 13 rules and `SPECS["0023A"]` 3 to 4 (invariant counts
  unchanged); `_P0030` (the names of P4-J17 and later that ADR 0030 cites), `_P0023A` gains the three names of
  rule A4 that a later task defines, `_P0024A` gains `test_judge_key_canaries_never_in_any_output_channel`,
  `_P0027` unchanged; `PENDING["0030"]`; the message suffix of `whole_adr_problems` is "(an ADR from 0031 has no
  pending set)"; the docstring and comment headers.
* S-J1 (`test_evalsim_loopback_guard.py`): `LOOPBACK_MODULES` gains `test_evalsim_judge_loopback.py`.
* S-J2 (`test_adr0023_phase4_amendment_is_tested.py`, `test_adr0024_phase4_amendment_is_tested.py`): the labels
  are A1 to A4 and A1 to A13, and "labelled A1 to A4" and "labelled A1 to A13" (the task names P4-00d stay).
* S-J3 (`test_phase4_adr_index_and_gate.py`): `STATUS` and the two parametrised lists gain `0030`; `_bullet` reads
  revision 52 for ADR 0030; the mutant of `test_the_index_header_check_has_teeth` replaces the whole number of the
  header line by 49 (the old mutant, "Revision 5" to "Revision 49", reads "Revision 492" at revisions 52 to 59 and
  the header check accepts it, so the unmodified test fails at any of them; the new mutant fails the header check at
  every revision, and the other assertions of the test are unchanged).
* S-J4 (`test_phase4_adrs_cite_only_existing_or_pending_tests.py`): `test_a_later_adr_is_read_too_and_has_no_pending_set`
  uses an invented ADR 0031; `test_the_reader_covers_every_adr_from_0026_and_the_three_amendments` gains "ADR 0030" and 30.
* Docs in the same diff: ADR 0030 moves to `docs/adr/` (Accepted, the five schemas and member tables written, rule 18
  cites the cost test, Ambiguity 7 added); the staged copy is deleted; `docs/adr/README.md` revision 52 (header, the
  Changes section, the index row); ADR 0023 rule A4; ADR 0024 rules A10 to A13; ADR 0027 rule 12a (all as written in
  ADR 0030); `docs/security/P4-J-providers.md`; the SCOPE row P4-J12 names the cost test.

Other tests that the new files or modules touch (a module list, an import allowlist, a header check, a language
scan) are named in `spec-change.diff` when the whole workspace shows them.

## 15. The five documents (P4-J05)

The schemas are the blocks of ADR 0030 (sections "The `tracer.golden_set/1` format" and its four siblings), the
constants `GOLDEN_SET_JSON_SCHEMA`, `JUDGE_PROFILE_JSON_SCHEMA`, `JUDGE_JSON_SCHEMA`, `JUDGE_EXCHANGE_JSON_SCHEMA` and
`JUDGE_LABELS_JSON_SCHEMA` equal them (`test_adr0030_documents_match_json_schemas`), and each member of each table
of the ADR names the rule it comes from. The labels module `tracer_evalcore.judgelabels` ships only the format, the
schema, `LABEL_BASES = ("audio", "stt_text", "both")` and `validate_judge_labels(document)` in part 1 (a
`JudgeLabelsError(InputRefusedError)` with the code `judge_labels_invalid`); `tracer labels` is P4-J21.
Cross-field rules of the validators (the schema cannot state them): `tracer.judge/1`: `verdict` is null if and only
if `reason_code` is null and `facts` is empty; `regenerated` is true if and only if `attempts` is 2; with `attempts`
0 the verdict and `exchange_sha256` are null. `tracer.judge_profile/1`: `params` holds exactly one of `max_tokens`
and `max_completion_tokens`; `host`, `path` are both null or both set. `tracer.judge_labels/1`: no item id twice in a file.
The five members `format` of the documents are single-value enums (no `const`, ADR 0003 D6).

## 16. Test inventory (exact SCOPE names, section A.10)

P4-J01: `test_adr0030_rules_are_tested`.
P4-J02: `test_adr0024_rules_are_tested`, `test_adr0023_rules_are_tested`, `test_adr0027_rules_are_tested`.
P4-J03: `test_judge_provider_dossier_cites_url_and_date_per_fact`, `test_judge_profile_status_matches_dossier`.
P4-J04: `test_golden_fixture_mr_valid_and_marked_for_native_review`.
P4-J05: `test_adr0030_documents_match_json_schemas`.
P4-J06: `test_golden_set_strict_json_closed_keys_and_version`, `test_golden_item_question_must_equal_scripted_turn_text`, `test_golden_set_refused_with_llm_caller`, `test_golden_set_caps_items_facts_chars`, `test_golden_set_on_barge_in_turn_refused`.
P4-J07: `test_answer_window_from_end_of_question_to_next_caller_line`, `test_answer_window_excludes_turns_starting_before_question_end`, `test_answer_window_last_question_ends_at_call_end`, `test_answer_window_reads_our_stt_agent_turns_never_export_or_script`.
P4-J08: `test_judge_request_holds_only_question_expected_and_answer`, `test_judge_request_never_holds_key_instructions_profile_or_other_items`, `test_agent_answer_is_json_encoded_inside_fenced_untrusted_block`, `test_delimiter_sequences_in_agent_text_are_neutralised`, `test_judge_prompt_template_sha_recorded_per_match_mode`.
P4-J09: `test_judge_output_closed_keys_and_values_only`, `test_judge_output_cross_field_consistency_enforced`, `test_judge_output_over_size_cap_refused`, `test_judge_output_never_repaired_one_regeneration_then_error`, `test_judge_reason_code_from_closed_set_only`.
P4-J10: `test_judge_definitions_are_llm_judge_enum_without_pass_rule`, `test_cannot_tell_is_error_judge_undetermined_never_a_value`, `test_judge_result_carries_judgeinfo_without_confidence_or_p_yes`, `test_judged_bundle_passes_check_run_bundle`, `test_judge_result_evidence_refs_name_answer_window_turns`, `test_expected_outcome_check_stays_inert_with_judge`.
P4-J11: `test_judge_errors_hold_facts_only_and_no_chain`, `test_judge_engine_streams_usage_before_next_request`.
P4-J12: `test_openai_compat_request_body_subset_exact`, `test_openai_compat_response_parse_rejects_wrong_shape_and_oversize`, `test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost`, `test_openai_compat_usage_maps_prompt_completion_cached_exactly`, `test_openai_compat_retry_taxonomy_matches_adr0024`, `test_openai_compat_choice_level_error_is_typed_failure`.
P4-J13: `test_profile_anthropic_compat_never_sends_response_format_or_seed`, `test_profile_temperature_not_sent_unless_configured`, `test_profile_openrouter_sends_pinned_provider_without_fallback_and_data_collection_deny`, `test_profile_reported_model_recorded_verbatim`, `test_profile_sarvam_v2_uses_subscription_key_header`, `test_unknown_profile_refused`, `test_profile_never_sends_unlisted_field`, `test_unverified_profile_refused_for_real_run`.
P4-J14: `test_judge_host_not_on_allowlist_refused_before_any_key_read`, `test_judge_key_bound_to_its_host_never_sent_elsewhere`, `test_judge_https_only_except_loopback_literal`, `test_judge_client_never_follows_redirects_or_reads_proxy_env`, `test_judge_dns_answer_private_refused_for_public_entry`, `test_judge_allowlist_mode_and_link_rules`, `test_judge_key_env_name_pattern_and_never_value_in_config`.
P4-J15: `test_judge_exchange_files_private_no_follow_and_guarded`, `test_replay_judge_reproduces_stored_verdicts_byte_identical`, `test_fake_judge_is_deterministic_and_sends_nothing`.
P4-J16: `test_loopback_fake_openai_server_round_trip`, `test_loopback_fake_server_injection_replies_are_refused`.

`test_phase4_batch_j1_wiring.py` checks that these names exist exactly once, have left the pending sets, and are
listed here. Every other test has a docstring giving its reason. The cost test of Ambiguity 6 is
`test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost` (P4-J12 row, ADR 0030 rule 18).
Extra tests (not SCOPE names): see the staged files; among them the Sarvam taxonomy matrix run against the judge
client, the caps of the engine, the DNS default resolver and the loopback key binding.

Evidence (under `docs/phase4/evidence/red/batch-j1/`): `red.py3.*.txt` (the current tree plus the staged tests, per
Python), `red-with-spec-change.py3.13.txt`, `red-reasons.py3.13.txt`, `red-tests-that-pass-on-the-current-tree.txt`,
`oracle.py3.*.txt`, `full.py3.*.txt`, `counts.txt`, `mutants.txt`, `oracle-src.patch`, `spec-change.diff`.
Not run: Postgres parity (no Docker; part 1 stores nothing), Python 3.15, the lowest-direct dependency leg, hosted CI,
any real provider.

## 17. Ambiguities, with recommendations (the lead auto-approves)

A-J1-1. ADR status. The staged ADR is Proposed; P4-J01 says Accepted. Recommendation: Accepted on the move to
    `docs/adr/` (the owner gates bind only the first real judge request). One line to flip: `EXPECTED_STATUS`.
A-J1-2. `excluded_turns` (rule 4 says a turn that starts before the edge "is excluded and counted"). Reading taken:
    every `our_stt` agent turn that starts before the edge, so the count of a later item includes the earlier
    answers. Alternative: only a turn that straddles the edge. Recommendation: keep the literal reading.
A-J1-3. Reason codes by mode (Ambiguities 3 and 4 of the ADR give the table and the equivalent-answer subset).
    Reading taken: the codes of the other mode are invalid in a mode (`all_facts` has no `meaning_*`,
    `equivalent_answer` has no `fact_*` and no `all_facts_stated`). A code is not tied to a particular fact
    status beyond the verdict table (`fact_absent` with a contradicted fact is accepted). Recommendation: keep.
A-J1-4. Error codes. The ADR names `judge_undetermined`, `judge_output_invalid`, `question_not_sent` and
    `answer_window_empty` only; the others are mine: `judge_request_failed` (any other failure of the request),
    `judge_auth_failed`, `judge_requests_cap_reached`, `judge_tokens_cap_reached`. Recommendation: keep; part 2's
    T-DEG tests pin them.
A-J1-5. Unsupported settings are refused, not dropped: a seed on `anthropic_compat/1`, an effort anywhere but
    `sarvam_v2/1`, a slug anywhere but `openrouter/1`. Recommendation: keep (a configuration that silently differs
    from what was sent contradicts "params as sent").
A-J1-6. Which requests count against `max_judge_requests`: each `complete` call (a judge request and a
    regeneration), not each HTTP attempt of the retry policy. Recommendation: keep; a retry is the transport's.
A-J1-7. `exchange_sha256` in `tracer.judge/1` is the hash of the LAST attempt's file (the verdict's, or the last
    failed one); the first attempt's file is still on disk. Recommendation: keep.
A-J1-8. An answer-window cap on the agent text: `JUDGE_AGENT_TEXT_MAX_CHARS = 4000` is mine (ADR 0028 rule 4 has
    its own cap constant; the ADR for the judge states none). Recommendation: keep, record it in
    `tracer.judge_profile/1` only if a cap becomes configurable.
A-J1-9. `response_format` is sent on `xai/1`, `openrouter/1` and `sarvam_v2/1`, the profiles whose documentation
    the ADR marks as showing or guaranteeing `json_schema`; the schema is `JUDGE_OUTPUT_RESPONSE_SCHEMA`
    (closed, no bounds: the validator enforces counts). Recommendation: keep; `xai/1` stays `unverified`.
A-J1-10. The profile `fake/1` appears in `tracer.judge_profile/1` (ADR Ambiguity 7); the replay engine takes the
    profile of the configuration to say what would have been sent. Recommendation: keep.
A-J1-11. `local_loopback/1` is accepted only on a literal loopback address, and any real profile may also be
    pointed at one (a user's own server, the scripted fake). Recommendation: keep.
A-J1-12. Hard links: an allowlist or exchange file with more than one link is refused (the ADR says "no links").
    Recommendation: keep.
A-J1-13. The golden question is checked against the whole character table, not only NFC, so a scripted line with
    a Latin letter or a native digit cannot be judged. Recommendation: keep; a wider table is a BACKLOG row.
A-J1-14. The statuses of the dossier: all seven profiles are `unverified`, because every profile has at least one
    fact the reviewer did not read in a primary source (the authorisation scheme or the token field of most).
    Recommendation: keep; the first owner-gated request per profile flips it, with a dossier row.
A-J1-16. The Sarvam guard `test_sarvam_base_url_not_overridable_source_reads_no_environment_for_it` refuses a URL
    literal anywhere in `tracer_evalproviders` except `sarvam/models.py`. The documented endpoint of each judge
    profile is informational (a run connects to the owner's allowlist entry, never to the table), so
    `judgeprofiles.py` keeps `(host, path)` pairs and joins them at run time instead of editing the guard.
    Recommendation: keep; if the lead prefers literals, name `judgeprofiles.py` in that guard (a one-line spec change).
A-J1-17. `exchange_sha256` is the SHA-256 of the bytes of the file as written; the repository guard refuses any
    tracked file that holds the format member of the exchange document, so no test or doc may spell that member
    next to its value (the tests build it from two pieces). Recommendation: keep.
A-J1-15. Part 2 inherits: the name of the command flag for `first_request_gate`; the guard for labels and the
    allowlist; the CLI parsing of `--judge`; `judge_item` in the simulate order (step 13b). Recommendation: JC's.

Things in the ADR or the SCOPE that the tests contradict, or that I changed: (1) SCOPE P4-J02 says "existing rule
counts unchanged"; the amendments change the pinned counts of ADRs 0023 and 0024 (the architect already logged
this); (2) SCOPE gate item 15 "index revision bumped" needs the header-test mutant fix of S-J3; (3) rule 18 of the
ADR gets the cost test (SCOPE row P4-J12 and ADR text edited in the diff); (4) the staged ADR said Proposed and that
the file moves at P4-J01; the diff makes it Accepted and deletes the staged copy; (5) rule 2 says the loader refuses
"at step 1 (exit 4 or 64)": part 1 raises `GoldenSetError`, an `InputRefusedError`, and the exit code is the
command's (part 2).
