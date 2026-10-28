<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch J, part 2: the judge in `tracer simulate`, `tracer judge`, the report, the store and the label loop (P4-J17 to P4-J25)

Status: red-first contract from `qa`, written against HEAD d5b78f6 (batch F is committed there). Part 1 of batch J (97d5aeb: the pure core and the providers,
P4-J01 to P4-J16, `api-surface-batch-j1.md`) and batch E (7699bd4: `tracer simulate`) are committed;
their names are reused as they are (`first_request_gate`, `ExchangeStore`, `JudgeSetup`,
`judge_results`, `with_judge_results`, `SimWorld`, `exit_code_of`, and the judge error codes
`judge_request_failed`, `judge_auth_failed`, `judge_requests_cap_reached`, `judge_tokens_cap_reached`).
Part 2 covers JC (P4-J17 to P4-J21: the commands, the report, the store, the labels), JD (P4-J22 the
injection suite, P4-J23 the tier-degradation matrix, P4-J24 the offline end to end, P4-J25 the key
canaries and the guards). The audit P4-J26, the measurement P4-J27 to P4-J29, the docs P4-J30 and the
sample bot P4-J32 are later. The schema package is frozen: nothing here changes it. Every test of
part 2 is written against this file. The oracle (the minimal implementation the tests were proved
against) is `oracle-src.patch` next to the evidence: a reference for the dev, not code to copy blindly.

Open owner decisions stay open: O-J1 to O-J6 come before the first real judge request (P4-J28), none
before this batch. No test sends a real request: everything uses the fake engines, an
`httpx.MockTransport` that the harness injects as `Environment.transport`, or the stored exchange
files. No test holds a key (canaries are built from parts). No test opens a socket and none carries
the `loopback` marker.

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-j2/staging/tests/<package>/`; no `__init__.py`; none of
them is edited by the dev. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). The support modules
   (`cli_p4j2_support.py`, `cli_p4j2_e2e.py`) are plain modules found through pytest's rootdir import
   mode, as the batch E and part 1 support modules are. None is edited by the dev.
2. Apply `spec-change.diff` (section 16) as exact-string edits against HEAD d5b78f6 (batch F is committed
   there, so its edits to `evalcore_phase4_adr_support.py` are in the base): it edits three existing test
   files and replaces none. `git apply --check` on d5b78f6 passes. `oracle-src.patch` is the reference
   implementation against the same HEAD, guard and `.gitignore` included.
3. Create nothing under `packages/` that has no `pyproject.toml`; all new modules go into the existing
   packages (section 1). No dependency is added (`uv.lock` is unchanged).
4. Apply the repository wiring of section 14 (`.gitignore` and the guard script).
5. Run `uv sync --locked --all-groups && uv run --locked pytest -q`; then, with a Postgres,
   `-m postgres` and `TRACER_TEST_POSTGRES_URL` (the Postgres case of
   `test_judged_and_rescoring_bundles_store_on_both_backends` is deselected by default).

Files (16 staged test files and 2 support modules): `cli` 11 test files and 2 support modules,
`evalcore` 4 test files, `evalproviders` 1 test file; `evalsim` none.

## 1. Package ownership

| Package | New or changed modules |
|---|---|
| `tracer-evalcore` (pure, no async, no network) | NEW `judgeconfig` (section 3), `judgerun` (section 4); CHANGED `judgelabels` (the strict loader, the view of a judged run, the packet, the agreement counts: section 8), `judgerecords` (`judge_applicable_items`) |
| `tracer-evalproviders` | CHANGED `judgeprofiles` (`planned_params`), `judgehosts` (`check_endpoint`, `bind_checked`; `bind_endpoint` is their composition), `judgeexchange` (`JudgeExchangeModeWarning`, the directory levels of BL-337, `planned_fake_params`) |
| `tracer-cli` | NEW `judgeplan` (the stages of the judge of a command), `judgecmd` (`tracer judge`), `labelscmd` (`tracer labels`), `judgesummary` (the text of a judged run); CHANGED `simargs` (the judge options), `cli` (the parser and the dispatch; both new commands are imported lazily), `simulate` (the integration, `STEPS_WITH_JUDGE`, `SimWorld.judge_resolver`), `simsummary` (the judge lines and the judge usage line), `simreport` (the judge section and the judge usage row), `store` (`store_rescoring`), `fakes` (`ScenarioStt` carries timing: section 9.5) |
| repository | `.gitignore` and `scripts/guard_private_fixtures.py` (section 14) |

No module of `tracer-evalcore` imports `httpx`, a socket, a clock or the process environment. The
command line stays free of the simulator and its stack until a call is placed: `judgecmd` and
`labelscmd` are imported from `cli.run` only when their command runs.

## 2. `tracer simulate`: the judge options

| Option | Rule |
|---|---|
| `--golden FILE` | the golden set (`tracer.golden_set/1`, part 1) |
| `--judge-config FILE` | the judge configuration (`tracer.judge_config/1`, section 3) |
| `--judge ENGINE` | `<profile>` (for example `openrouter/1`; syntax `[a-z][a-z0-9_]*/[0-9]{1,3}`), `fake:<scenario>` (one of `FAKE_SCENARIOS`) or `replay:<dir>` |
| `--judge-allowlist FILE` | the owner's judge allowlist (mode 0600); required for a profile engine (refusal `judge_allowlist_missing`, step 2) |
| `--max-judge-requests N`, `--max-judge-tokens N` | 1 to 2**53 - 1; required for a profile engine, in this order (refusals `cap_missing (--max-judge-requests)` and `cap_missing (--max-judge-tokens)`, step 4); no default |
| `--judge-authorisation-reference TEXT` | same shape as `--authorisation-reference` (1 to 200 characters); required for a profile engine whose profile is a real provider (`judge_authorisation_reference_missing`, step 3); never stored in a record; after the `bundle:` line the command prints `judge authorisation reference: sha256 <64 hex>` (Ambiguity A-J2-5) |
| `--judge-first-request-gate` | a flag (`store_true` with `default=None`, so that no option of the command has a default value, the rule of batch E's options test): the owner-gated first request of an `unverified` profile (the `first_request_gate` of part 1); without it `profile_unverified` (step 2) |
| `--env-file FILE` | also the one place the judge key is read, for the variable name of the matched entry only |

Usage errors (exit 64, parser level): `--golden`, `--judge-config` and `--judge` go together
(`--golden, --judge-config and --judge go together`); a judge option without them
(`<option> needs --golden, --judge-config and --judge`); an allowlist, a cap, a reference or the gate
with `fake:` or `replay:` (`<option> applies to a profile engine only`); a cap that is not a whole
number from 1 to 2**53 - 1. No option has a default for a value that decides a request or its spend.

Exit codes are those of batch E (`SIM_EXIT_CODES`): a judge result of status `error` is a result that
was not scored, so exit 2 (`exit_code_of` is unchanged); a `cannot_tell` is such a result
(`judge_undetermined`); a verdict, including `disagrees`, is not a failure (exit 0); a call that was not
established (5) and a failed speech-to-text (3) outrank 2, and then every judge result is
`not_applicable`. A refusal is exit 4 with one stderr line `error: <code>` (a stable code, never a
value).

`SimWorld` gains `judge_resolver: Callable[[str, int], Sequence[str]] | None` (the DNS lookup of a
public judge entry; tests only; `None` means `judgehosts.system_resolver`). The judge provider of a
test is `Environment.transport` (an `httpx.MockTransport`), as for the other engines.

The refusals of step 1, before any file but the inputs is read: `golden_*` (part 1 codes, among them
`golden_caller_not_scripted` for `--caller sarvam` AND `--caller fake:<dir>`, Ambiguity A1 of part 1,
`golden_question_mismatch`, `golden_turn_missing`, `golden_turn_barge_in`), `judge_config_*` (section 3),
`unknown_scenario` (`fake:<name>` outside `FAKE_SCENARIOS`), `exchange_missing` and
`exchange_directory_not_private` (the directory of `replay:<dir>` is not there, or is a link or a file: it is
checked at step 1 by building the replay engine, which reads nothing), `judge_config_profile_differs` (the
configuration's profile is not the engine's: for `fake:` it must be `fake/1`, for a profile engine it
must be that profile, for `replay:` any of the seven), `judge_config_endpoint` (a profile engine needs
an endpoint; `fake:` and `replay:` take none), `unknown_profile`, and the refusals of
`judgeprofiles.planned_params` (`seed_not_supported`, `reasoning_effort_not_supported`,
`reasoning_effort_required`, `provider_slug_not_supported`, `provider_slug_required`,
`header_not_supported`): a setting that the profile does not support is a refusal and is never dropped.

## 3. `tracer_evalcore.judgeconfig` (P4-J17 support; ADR 0030 rules 12, 14, 15, 17)

```python
JUDGE_CONFIG_FORMAT = "tracer.judge_config/1"
CONFIG_PROFILES: tuple[str, ...]            # the seven profiles of rule 13 and "fake/1"
MAX_JUDGE_CONFIG_BYTES = 65_536
class JudgeConfigError(InputRefusedError): code      # str(error) == code; never a value
@dataclass(frozen=True) class JudgeConfig:  profile: str, endpoint: str | None, params: JudgeParams
def parse_judge_config(text: str) -> JudgeConfig
```

Strict JSON (`loads_strict`; a repeated key is `judge_config_not_json`), one object with exactly the
keys `format`, `profile`, `endpoint`, `model`, `max_tokens`, `temperature`, `seed`, `reasoning_effort`,
`provider_slug`, `extra_headers`; every key is required and none has a default (`null` is "not sent"
for the optional settings). `extra_headers` is an object whose keys are in `EXTRA_HEADER_NAMES`.
The file never holds a key, a key variable name or a base URL of its own. Order of checks and codes:
`judge_config_too_large`, `judge_config_not_json`, `judge_config_shape` (not an object),
`judge_config_key_shaped` (any string, member names included, with the shape of a credential),
`judge_config_format`, `judge_config_missing_key`, `judge_config_unknown_key`, `judge_config_profile`,
`judge_config_endpoint` (`fake/1` with an endpoint, an endpoint that is not a string of 1 to 2,048
characters), `judge_config_extra_headers`, then the field of `JudgeParams` that is not accepted
(`judge_config_model`, `judge_config_max_tokens`, `judge_config_temperature`, `judge_config_seed`,
`judge_config_reasoning_effort`, `judge_config_provider_slug`, `judge_config_extra_headers`).

## 4. `tracer_evalcore.judgerun` (P4-J17, P4-J18, P4-J23; ADR 0030 rules 16, 17, 20, 21, 23)

```python
@dataclass(frozen=True) class JudgeRun: judgements: dict[str, JudgeItemResult], results: tuple[MetricResult, ...],
                                         usage: tuple[UsageRecord, ...], requests: int
def skipped_judgement(item_id, window: AnswerWindow, error_code) -> JudgeItemResult
def check_golden_against_bundle(golden: GoldenSet, bundle: RunBundle) -> None        # GoldenSetError
def judge_bundle(*, bundle, setup: JudgeSetup, engine, params: JudgeParams, context: JudgeContext,
                 call_end_ms: int, on_usage: UsageSink, created_at) -> JudgeRun
```

`judge_bundle` is step 13b and the body of `tracer judge`. For each item of `setup.golden.items` in
golden order: an item whose definition does not apply to the call (`judgerecords.judge_applicable_items`,
the applicability answer of `judge_results`) is not judged and its result is `not_applicable`; for any other
the window is `answer_window(...)`; a window that is not `ok` is an error judgement with no request
(`judge_item`'s own rule), and this is decided before the state of the run; otherwise, if an earlier item
ended the run (`JudgeItemResult.stops_run`: an authentication failure or a cap), the item is
`skipped_judgement(item_id, window, <that error code>)`, with attempts 0 and no request; otherwise
`judge_item(...)`, whose requests are numbered from 0 across the items (`first_index`). `requests` is the
sum of the attempts, `usage` the records of the answered requests in the order answered (they also reach
`on_usage` before the next request), `results` is `judge_results(...)` over the judgements (one result per
judge definition of the snapshot, in its order). `skipped_judgement` refuses (`ValueError`) a code that is
not an error code of `JUDGE_ERROR_CODES` or is `judge_undetermined`. `check_golden_against_bundle` raises
`golden_set_differs` (the item ids are not the judge keys of the snapshot, in order) or
`golden_question_mismatch` (a question differs from the stored caller line with the same sequence number;
a line that was never sent is no mismatch). The closing edge of an answer window stays the earliest start
of any later caller line (a part 1 finding, kept and now tested).

## 5. Providers: what the commands need (P4-J17, BL-337)

* `judgeprofiles.planned_params(profile, params: JudgeParams) -> dict`: what `params_as_sent` will record
  for these settings, before any request exists (the `params` of `tracer.judge_profile/1`). It refuses as the
  first request would (`seed_not_supported`, ...) and checks the extra headers (`header_not_supported`).
* `judgeexchange.planned_fake_params(params) -> dict`: `{"max_tokens": n}` plus `temperature` when set; what
  `FakeJudge` records as sent.
* `judgehosts.check_endpoint(entries, *, url, profile, resolver=None, first_request_gate=False) ->
  JudgeEndpoint`: the first three steps of `bind_endpoint` (the entry, the profile and `check_real_run`, the
  DNS answers) and never the key; `judgehosts.bind_checked(entry, *, environ=None, env_file=None) ->
  BoundEndpoint`: the key by the entry's variable name only; `bind_endpoint` is exactly their composition.
* BL-337 (the decision, Ambiguity A-J2-14): `ExchangeStore(<out>/judge/<run>)` tightens an EXISTING
  `<out>/judge` that has a group or other bit to 0700, issues one `judgeexchange.JudgeExchangeModeWarning`
  (a `UserWarning`; the text names no path) and goes on; a link or a file in its place is refused
  (`exchange_directory_not_private`); the user's own `<out>` is never changed; an existing run directory
  that is open to others is still refused. The commands turn the warning into one stderr line
  `warning: <text>`.

## 6. The order of operations (P4-J17; ADR 0030 rule 20; SCOPE J.3 Q-J6)

`simulate.STEPS` is unchanged (a run without `--golden` traces exactly these 15 steps). A judged run
traces `simulate.STEPS_WITH_JUDGE`, which is `STEPS` with `"judge"` after `"scoring"` (step 13b):

| Step (trace name) | What the judge adds |
|---|---|
| 1 `parse_inputs` | `judgeplan.parse_inputs`: the golden set, the configuration, the engine and the profile settings (section 2); `bind_golden_set` against the evaluator, the caller mode and the barge-in turns. Nothing else is read. A dry run stops after this step and prints the plan (section 9.4) |
| 2 `allowlist` | `judgeplan.check_allowlist`: `read_judge_allowlist`, then `check_endpoint` (the entry, the profile, the gate, the DNS answers). The key variable is not read |
| 3 `authorisation` | `judgeplan.check_reference` |
| 4 `caps` | `judgeplan.check_caps` |
| 5 `output_probe` | `judgeplan.build_setup` (the `tracer.judge_profile/1` document and the `JudgeSetup`); the snapshot is built with `judge=setup` |
| 6 `keys_and_call_log` | after the role keys: `judgeplan.bind_key` (`bind_checked`, the variable of the matched entry; a refusal is the part 1 reason, for example `key_missing`); a missing key leaves no directory |
| 7 to 12 | as batch E |
| 13 `scoring` | as batch E |
| 13b `judge` | the exchange store is created here (only for a profile engine and only when some item can be judged), then `judgeplan.build_engine` and `judge_bundle(...)` with `on_usage` = the ledger; the cost is recomputed over all usage (judge usage is `not_available` without a USD catalog); `with_judge_results` |
| 14 `store_write`, 15 `report` | as batch E |

* A `BaseException` during 13b writes `usage-recovery-<UTC>.json` with `failed_step` `judge` (only the
  class name of the error, the records of every answered request), prints `usage recovery: <path>` and is
  raised again; the recording was written at step 11; no bundle is stored and no report is written.
* A store failure after the judge keeps the judge usage in the recovery file (`failed_step` `store`).
* The refusals of section 2 happen at their step and before any key read, any engine, any connection:
  exit 4, nothing created; the trace ends at the refusing step (the step is traced before its checks).
* The judge's request and reply never pass through the ledger of the other engines; the call log
  (`sim_call_log.jsonl`) gets the judge lines of part 1.
* The records: `tracer.judge_profile/1` in the snapshot metadata, the inert check still the only required
  rubric item, no `RunVerdict`; the usage records are stage `judge`, domain `evaluation`.

## 7. `tracer judge` (P4-J18; ADR 0030 rule 21; FJ6, FJ7)

`tracer judge --out DIR --bundle ID --golden FILE --judge-config FILE --judge ENGINE [--repeats K]
[--judge-allowlist FILE] [--max-judge-requests N] [--max-judge-tokens N]
[--judge-authorisation-reference TEXT] [--judge-first-request-gate] [--env-file FILE] [--dry-run]`.
`--golden` and `--judge-config` are required (Ambiguity A-J2-3): the golden set gives the scripted turn
index of each item, which the stored definitions do not hold, and the configuration gives the request
settings. `--repeats` is 1 to 100 and defaults to 1 (A-J2-8). The judge options follow the rules of
section 2.

Order: parse the golden set and the configuration (section 2) -> the store must exist
(`store_not_found`, nothing is created) -> load the bundle (`bundle_id_invalid`, `bundle_not_found`) ->
`run_has_no_judge_definitions` -> `judge_model_differs` (the configuration's profile and model are not the
`judge_model_ref` of every definition) -> `golden_set_differs` (the SHA-256 in the stored
`tracer.judge_profile/1`) and `check_golden_against_bundle` -> a dry run stops here (plan lines, then
`judge repeats: K` and `dry run: nothing was sent`; no allowlist is read) -> allowlist, reference, caps, key
as in section 6 -> the repeats. Exit 4 for a refusal, 64 for usage, 2 if any judge result of any repeat is
an `error`, else 0.

Each repeat is one rescoring bundle of the same run (ADR 0022 rule 5): a NEW bundle id (minted before the
requests; the exchange directory of a profile engine is `<out>/judge/<new bundle id>`), `created_at` from the
clock, the results of the original that are not judge results (the same records), plus the new judge results;
the usage records of the original that are not judge usage (the same records), plus the new judge usage; the
cost records and summary recomputed over that usage; every other record of the call shared. It is stored
with `store.store_rescoring`. The engine, its caps and its exchange store belong to one repeat. Output:
`rescoring of: <bundle>`, `repeats: K`, then per repeat `repeat r of K: <new bundle id>`, the judge lines of
section 9.1, `judge usage: <text>` and `judge changed from the stored run: n of N items` (an item changed when
its status, verdict, reason, fact statuses or error code differ from the stored run's); then
`store: <path>` and, with a reference, `judge authorisation reference: sha256 <hex>`. A `BaseException`
keeps the usage of every answered request of the command in `usage-recovery-<UTC>.json` (`failed_step`
`judge`), prints `usage recovery: <path>` and is raised again; the repeats stored before it stay.

## 8. The label loop (P4-J21; ADR 0030 rule 22 and invariant I7)

### 8.1 `tracer_evalcore.judgelabels`

`parse_judge_labels(text) -> dict`: at most `MAX_LABELS_BYTES` = 65,536 bytes (`judge_labels_too_large`),
strict JSON (`judge_labels_not_json`: a repeated key, NaN, a syntax error), then `validate_judge_labels`
(`judge_labels_invalid`, `pointer` names the place, for example `/labels/0/basis`; never a value).
`JudgedItem` (`item_id, question, match_mode, expected_facts, expected_answer, status, verdict, reason_code,
facts, error_code, na_reason, missing_kinds, attempts, window_turn_seqs`) and `judged_items(bundle)` read the
judge items of a stored bundle in the order of its snapshot from the bindings (the texts) and the results
(`tracer.judge/1`); `judge_category(item)` is its row of the table: the verdict (`agrees`, `disagrees`,
`cannot_tell`), `no verdict` (an error without a verdict) or `not applicable`. `JUDGE_CATEGORIES`,
`HUMAN_VERDICTS`.

`build_packet(bundle, golden, *, call_end_ms) -> dict` is the blind packet, `tracer.judge_packet/1`: `format`,
`bundle_id`, `language` `mr`, `golden_set` `{key, sha256}`, `recording` `{sha256, channel: "agent"}` (the SHA-256
of the stored recording, or null), `items` and `labels_template`. Each item has exactly `item_id`, `question`,
`match_mode`, `expected_facts` (a list, or null in `equivalent_answer`), `expected_answer` (a string, or null),
`audio_range_ms` (`{start_ms, end_ms}` = the answer window's edges, or null when the question was not sent) and
`stt_text` (the window text, `""` for an empty window, null when the question was not sent). It holds nothing
the judge said: no verdict, reason code, fact status, error code, model, hash or count of requests; two
judgements of one call give the same packet. `labels_template` is a valid `tracer.judge_labels/1` file with
labeller `labeller-1`, every verdict `null` and every basis `audio` (the recommendation of O-J5; Ambiguity A-J2-9).

`agreement_counts(bundle_id, items, labels) -> AgreementReport` and `agreement_lines(report) -> list[str]`:
one or two labels files (`judge_labels_labeller_count` otherwise), not the same labeller twice
(`judge_labels_labeller_repeated`), all for this bundle (`judge_labels_bundle_differs`), all items known
(`judge_labels_item_unknown`). For each labeller the table has the 5 judge rows by the 3 human verdicts; an item
whose verdict is `null`, or that the file does not list, is unlabelled: counted apart and never in a cell;
`bases` counts the labelled items by basis. With two labellers: the 3 by 3 human-by-human table over the items
both labelled, the items labelled by one only and by neither. The text (every count is `n of N items`; no other
number; no percent, rate, kappa or claim word):

```
agreement report: counts of an uncalibrated judge signal against human labels
bundle: <id>
items: N
labeller: <name> (basis audio a, stt_text s, both b)           # once per labeller, in the order given
judge <row>, human <verdict>: n of N items                      # 15 lines
unlabelled by <name>: n of N items (not counted as agreement or disagreement)
<first> <v>, <second> <w>: n of N items                         # two labellers: 9 lines
labelled by one only: n of N items                              # two labellers
labelled by neither: n of N items
```

### 8.2 `tracer labels`

* `packet --out DIR --bundle ID --golden FILE`: the golden set must be the run's (`golden_set_differs`,
  `golden_question_mismatch`); writes `<out>/labels/packet-<bundle id>.json` (UTF-8, indented, mode 0600 in a
  directory of mode 0700, never overwritten: `packet_exists`); prints `packet: <path>`, `items: N` and
  `blind: the judge's verdict is not in the packet`.
* `check --out DIR --labels FILE`: `parse_judge_labels`; the bundle must be in the store at `<out>`
  (`store_not_found`, `bundle_not_found`); every item must be an item of the run (`judge_labels_item_unknown`);
  copies the file's bytes to `<out>/labels/labels-<SHA-256 of the bytes>.json` (0600 in a 0700 directory; an
  existing `<out>/labels` open to others is made private; a link or a file in its place is
  `labels_directory_not_private`); the same bytes again are not stored twice. Prints `labels: ok`,
  `bundle: <id>`, `labeller: <name>`, `labelled: n of N items`, `unlabelled: m of N items`, and
  `stored: <path>` (`stored: <path> (already stored)` the second time). A refusal copies nothing.
* `agreement --out DIR --bundle ID`: reads `<out>/labels/labels-*.json` (each a regular file with no group or
  other bit: `labels_file_not_private`; each parsed strictly), keeps those for the bundle, sorted by labeller
  (`labels_none_for_bundle` if none), prints `agreement_lines`.
* Without a subcommand or with an unknown one: usage, exit 64.

## 9. What is printed and what the page says (P4-J19; ADR 0030 rule 9; ADR 0023 amendment A4)

The judge text is written by `tracer_cli.judgesummary`; `sim_summary_lines` appends it for a bundle whose
snapshot holds `tracer.judge_profile`. A run without a golden set prints none of it.

### 9.1 The lines (a label, a colon, a value; exact)

```
judge: uncalibrated signal (golden set <key>, engine <profile>, model <model>)
judge agrees: n of N items
judge disagrees: n of N items
judge cannot_tell: n of N items                  # the results with the verdict cannot_tell (errors judge_undetermined)
judge error: n of N items                        # the other errors
judge not applicable: n of N items
judge item <id>: <text>                          # one per item, in golden order
```

`<text>`: `agrees (reason <code>; facts <s> of <n> stated, <c> contradicted, <a> absent, <t> cannot_tell; requests
<r>)` (the facts part only in `all_facts` mode); `cannot_tell (error judge_undetermined; reason <code>; facts ...;
requests <r>)`; `error (<code>; requests <r>)`; `not applicable (missing input <kinds, separated by ", ">)` or
`not applicable (<reason with spaces>)`, for example `not applicable (unsupported language)`. The judge usage line
follows `stt usage`: `judge usage: <r> requests, <i> input tokens, <o> output tokens` (or `none`); `cost:` is
unchanged (`not available (no_price_for_model)` without a USD catalog; credits are never a cost). The expected
facts and the expected answer are NEVER printed or put on the page (Ambiguity A-J2-12).

### 9.2 The page (`simreport`)

A `<section id="judge" data-calibration="uncalibrated" data-headline="uncalibrated signal">` after the agreement
section: `<h2>Uncalibrated judge signal</h2>`, `<p id="judge-note">` (the fixed `JUDGE_NOTE`), a table
`#judge-summary` with the lines of 9.1 but the items, and an ordered list `#judge-items`: per item an `li`
`#judge-item-<id>` (`data-item`, `data-status`, `data-verdict`) with the id, the text of 9.1, `answer window: agent
turns <seqs>` (or `no agent turn`) and the question as an escaped untrusted-text element. The usage section gets
a row `judge model (uncalibrated signal)` with id `usage-judge`. `outcome-label` ("Outcome not evaluated: ...") is
kept. The note holds none of the claim words of rules 1, A1 and A4.

### 9.3 Claim words

The text Tracer writes for the judge (the lines of 9.1, the section of 9.2, the plan of 9.4, the agreement report)
holds none of: the words of ADR 0023 rule 1 and A1, and `correct`, `incorrect`, `correctness`, `factual`,
`factuality`, `truthful`, `hallucination`, `ground truth`, `calibrated` (whole word), `probability`, `confidence`,
`pass`, `fail`, `passed`, `failed`, `score of the agent`; no percent sign; no decimal number; none of `rate`, `score`,
`kappa`, `percent`, `likely`, `reliable`. Record keys and values (`confidence: "none_claimed"`) are exempt.

### 9.4 The dry run

After the lines of batch E and before `dry run: nothing was sent`:

```
judge: uncalibrated signal, engine <engine>, model <model>
judge golden set: <key>, <N> items (<n> all_facts, <m> equivalent_answer)      # the modes in sorted order
judge requests: up to <2N> (one for each item, one more for each regeneration)
judge max tokens per request: <max_tokens>
judge max requests: <N | not set>
judge max tokens: <N | not set>
```

It reads the golden set and the configuration only: no allowlist, no key variable, no DNS, no request, no directory.

### 9.5 The fake speech-to-text carries timing in a judged run

`tracer_cli.fakes.ScenarioStt(texts, *, timed=False)` (`--stt fake`): with `timed=True`, which `simulate` passes
when the run has a golden set, it answers each chunk with the words of the scenario and one `timestamps` phrase
`{words: [<text>], start_time_seconds: [0], end_time_seconds: [<chunk duration>]}`, so the turn starts where the
agent's segment starts (`test_scenario_stt_turns_carry_the_time_of_their_chunk_in_a_judged_run`). Without it
every offline item would be `answer_window_empty`. Without a golden set the engine is exactly what batch E
built (the turns have no time), because batch F pins the masked hashes of that run
(`test_scenario_stt_turns_have_no_time_without_a_golden_set`). It makes no request and claims no usage.

## 10. The store (P4-J20)

`store.store_rescoring(engine, bundle) -> StoredRun`: `tracer_evaldb.store_run_bundle` for a bundle of a run that
is already stored (the project, the target and the snapshot are in the store). A judged bundle round-trips through
SQLite byte for byte; the rescoring bundle inserts only its own rows (its judge results, its judge usage records,
its cost rows, its envelope and member rows); on Postgres the rows are the same. The store holds the SHA-256 of an
exchange file, never a raw reply (a reply that was refused is in the local file only).

## 11. The tier-degradation matrix (P4-J23; ADR 0030 rule 23)

| Case | Judge results | Requests | Exit |
|---|---|---|---|
| setup failed (connection refused) | all `not_applicable` `missing_input` | 0 | 5 |
| speech-to-text failed | all `not_applicable` `missing_input` (kind `transcript`) | 0 | 3 |
| a non-`mr` agent turn anywhere | all `not_applicable` `unsupported_language` | 0 | (as the call) |
| the question was not sent | that item `error` `question_not_sent`, attempts 0 | none for it | 2 |
| the answer window is empty (including an empty transcript of the segment) | that item `error` `answer_window_empty`, attempts 0 | none for it | 2 |
| authentication failure | the item and the rest `error` `judge_auth_failed` (attempts 1, then 0) | stops | 2 |
| a cap | the rest `error` `judge_requests_cap_reached` or `judge_tokens_cap_reached` | stops | 2 |
| invalid output twice | `error` `judge_output_invalid`, attempts 2, regenerated | 2 per item | 2 |
| a valid `cannot_tell` | `error` `judge_undetermined`, `tracer.judge/1` verdict `cannot_tell` | 1 | 2 |

Every case gives the same definitions, the same judge result keys in the same order, the same fields of a
result, the same keys of `tracer.judge/1` where it is present and the same keys of the profile document. A run
without a golden set is what batch E builds.

## 12. The injection suite (P4-J22; BL-141)

All through `tracer simulate` with the loopback profile and a scripted model: an order and an embedded verdict
JSON in the agent's words; a model that obeys them in an inconsistent reply (the verdict says `agrees`, a fact
says `absent`; a bare `{"verdict":"agrees"}`; a reason code of the other mode) is invalid twice and the item is an
`error` with no value; only a consistent reply counts. A reply with an extra member, a free-text reason code or a
fact with an extra member is invalid and its text is in the local exchange file only. Valid JSON in a code fence,
prose around the object, a list, two objects, a byte order mark, NaN and `finish_reason` `length` are refused and
never repaired (one regeneration, the identical request). A forged fence in the agent's words becomes
`[delimiter removed]`; the user message has one opening and one closing fence line and one line of JSON; bidi
controls are removed; a text over 4,000 characters is cut and marked `[text cut]`. The expected facts and the
expected answer are in the store (binding parameters) and the local exchange file only. The request body holds
exactly the members of the profile and, in the two messages, only the question, that item's expectation and the
agent's words.

## 13. Key canaries and the no-socket guard (P4-J25; ADR 0030 rules 15 and 25)

The key (a canary, the variable named by the allowlist entry, from the environment or an env file) is in none
of: stdout, stderr, the DEBUG log, the page, the store, the call log, the exchange files, the recovery file, the
packet, the labels file, the agreement report, the `repr`/`str`/traceback of the bound endpoint, the engine and
the typed failures; neither is the authorisation reference text. Runs: success, a 401 that echoes the key, a
transport error whose message holds the key, an interrupt, each from the environment and from an env file.
`cli_p4j2_support.assert_judge_tests_open_no_socket()` scans the test modules of `packages/cli/tests` for a
client, a server or a socket built by a test (the modules that build sockets on purpose are listed); the cli's
`test_unit_tests_open_no_socket` calls it (spec change S-J2) and a default-resolver check shows that a public
judge entry with no resolver seam cannot resolve in a unit test.

## 14. The repository guard and `.gitignore` (P4-J25)

`scripts/guard_private_fixtures.py` gains three kinds, with the message form of the others
(`<kind> tracked, staged or in history: <path>`):

| Constant | Message kind | By name | By content (first 64 KiB of the staged blob) |
|---|---|---|---|
| `JUDGE_LABELS` | `tracer judge labels file` | `(^\|/)labels/labels-[A-Za-z0-9_.-]+\.json\Z` | the member `"format"` with the value of the labels format |
| `JUDGE_PACKET` | `tracer judge label packet` | `(^\|/)labels/packet-[A-Za-z0-9_.-]+\.json\Z` | the member `"format"` with the value of the packet format |
| `JUDGE_ALLOWLIST` | `tracer judge allowlist` | `(^\|/)judge[-_]allowlist(\.(txt\|local\|conf\|list))?\Z` | a line that starts with `http://` or `https://` and has `profile=<name>/<n>` and optionally `key_env=<NAME>` |

The content rule for a labels file and a packet applies to a file that is a JSON object (its first
character, after a byte order mark and white space, is `{`): text that only quotes the member, such as a
diff, a document, a test or a prose line, is not a finding, and the rule needs no change to an existing test.
(The exchange rule of part 1 stays what it is: any file; the tests build that member from two pieces.) Text
that only names a format, an enum that lists it, a description of the entry shape in prose or in code, and
names such as `docs/judge-allowlist-howto.md` are not findings. `.gitignore` gains `**/labels/labels-*.json`, `**/labels/packet-*.json`, `**/judge-allowlist`,
`**/judge-allowlist.*`, `**/judge_allowlist` and `**/judge_allowlist.*` (the exchange files of a repeat are
already matched by `**/judge/*/item-*-[0-9].json`).

## 15. The offline end to end (P4-J24)

`cli_p4j2_e2e.run_e2e`: the scenario `sim-mr-s1` of `sim_mr.json` (ten scripted lines) and the ten items of
`golden_mr.json`; a fake text-to-speech (a tone, 40 ms for each character); a fake agent that says one planted
reply for each question, 200 ms after the line ends (the last item has no reply and the call ends
`max_silence`); `--stt fake`; a judge (`httpx.MockTransport`, profile `local_loopback/1`) that answers each
planted reply with the label the apparatus intends for it; two fake labellers. The tests pin: the exact lines
of the command (ids and paths replaced), the ten `tracer.judge/1` documents including the SHA-256 of each
exchange file, the exact text of the judge section of the page, the packet ranges and the recording hash, and
the whole agreement report (built in the test from the labels and the judge rows alone); and that the output, the
page, the stored bundle and the agreement report give the same counts and verdicts, that the store returns the
bundle byte for byte and that the packet is blind. Nothing was reviewed by a native speaker (BL-169).

## 16. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only)

* S-J2-1 (`evalcore_phase4_adr_support.py`): the 43 names of P4-J17 to P4-J25 leave the pending sets because the
  tests now exist (`_P0030` 43 names, all but the audit, the measurement, the concept page, the quick start and
  the sample bot; `_P0024A` 1 and `_P0023A` 2 of the same names; `_P0024A` becomes `frozenset()`), and the comment headers follow. Applied by the dev as exact-string
  edits; no replacement of the file is staged.
* S-J2-2 (`test_cli_no_socket.py::test_unit_tests_open_no_socket`): the named test also calls
  `assert_judge_tests_open_no_socket()` (P4-J25 "extended").
* S-J2-3 (`test_cli_score.py::test_cli_has_no_export_command`): the commands of the parser are
  `{"score", "simulate", "judge", "labels"}`; the rest of the test (no command exports, shares, redacts or
  uploads; no module of those names) is unchanged and holds.
* Docs (P4-J30, not in the diff): ADR 0030 rules 20 to 22 and the SCOPE rows P4-J17, P4-J18 and P4-J21 name the
  options `--golden` and `--judge-config` of `tracer judge`, the flag `--judge-first-request-gate`, the judge
  configuration and the packet; the concept page and the README follow.
* Tests that the new modules touch (a module list, an import allowlist, a header check, a language scan) are
  named in `spec-change.diff` when the whole workspace shows them. The run of the whole workspace with the
  oracle found S-J2-3 (the command set) and a default of the new flag (it has no default, like every option,
  so the product sets `default=None`).

## 17. Test inventory (exact SCOPE names, section A.10)

P4-J17: `test_simulate_with_golden_order_of_operations_pinned`, `test_judge_refusals_before_any_key_read_or_connect`,
`test_judge_caps_required_for_real_engine_no_defaults`, `test_judge_usage_survives_ctrl_c_mid_judging`,
`test_simulate_dry_run_prints_judge_plan_and_sends_nothing`, `test_simulate_judge_errors_exit_2`.
P4-J18: `test_tracer_judge_rescoring_bundle_shares_records_and_adds_judge_results`,
`test_tracer_judge_refuses_run_without_judge_definitions`, `test_tracer_judge_model_must_equal_definition_model_ref`,
`test_tracer_judge_repeats_store_one_bundle_each`.
P4-J19: `test_judge_lines_labelled_uncalibrated_everywhere`, `test_judge_report_never_claims_accuracy_or_quality`,
`test_judge_report_shows_counts_with_denominators`, `test_judge_report_states_outcome_not_evaluated`.
P4-J20: `test_judged_bundle_round_trips_through_store_byte_identical`,
`test_judged_and_rescoring_bundles_store_on_both_backends`.
P4-J21: `test_labels_file_strict_closed_keys_and_version`, `test_label_packet_never_shows_judge_verdict`,
`test_labels_import_content_addressed_and_private`, `test_agreement_report_counts_only_never_rates_or_accuracy`,
`test_agreement_report_unlabelled_never_counted_as_disagreement`,
`test_agreement_report_human_human_counts_when_two_labellers`.
P4-J22: `test_judge_injection_cannot_flip_verdict_without_consistent_fields`,
`test_judge_injection_cannot_store_free_text`, `test_judge_injection_cannot_change_output_format`,
`test_judge_injection_forged_delimiter_stays_data`, `test_judge_injection_cannot_leak_expected_facts_into_any_output`,
`test_judge_never_receives_caller_brain_prompt_or_profile_values`.
P4-J23: `test_tdeg_judge_setup_failed_all_missing_input_no_request`, `test_tdeg_judge_stt_failed_missing_input_no_request`,
`test_tdeg_judge_question_not_sent_is_error_never_disagrees`, `test_tdeg_judge_empty_answer_window_is_error_never_disagrees`,
`test_tdeg_judge_non_mr_turn_unsupported_language_all_items`, `test_tdeg_judge_auth_failure_stops_requests_and_errors_remaining`,
`test_tdeg_judge_cap_reached_no_request_beyond_cap`, `test_tdeg_judge_invalid_output_twice_is_error`,
`test_tdeg_judge_cannot_tell_is_judge_undetermined_error`, `test_tdeg_judge_schema_identical_across_cases`,
`test_tdeg_run_without_golden_set_unchanged`.
P4-J24: `test_e2e_judge_synthetic_mr_exact_outputs`, `test_e2e_judge_report_store_and_agreement_consistent`.
P4-J25: `test_judge_key_canaries_never_in_any_output_channel`, `test_unit_tests_open_no_socket` (extended by the
spec change), `test_guard_rejects_staged_judge_exchanges_labels_and_allowlist`.

`test_phase4_batch_j2_wiring.py` checks that these names exist exactly once (the extended one excepted), have
left the pending sets, are listed here and cite sections that exist. Every other test has a docstring giving its
reason. Extra tests (not SCOPE names): BL-337 (three tests of the exchange directory levels), the part 1
findings (the closing edge of the window, the usage of a paid reply before its exchange file, the short reads),
`planned_params`, `check_endpoint`/`bind_checked`, the judge configuration (34 cases), `judge_bundle`, the pure
label loop, `tracer judge` replay/golden/usage/dry-run/interrupt, the key-free representation test, the
`.gitignore` test, the `--stt fake` timing test, the usage errors and a burst of refusals (34 parametrised cases).

Evidence (under `docs/phase4/evidence/red/batch-j2/`): `red.py3.*.txt` (HEAD d5b78f6 plus the staged tests
and the spec change, per Python), `red-without-spec-change.py3.13.txt`, `red-reasons.py3.13.txt`,
`red-tests-that-pass-on-the-current-tree.txt`, `oracle.py3.*.txt`, `full.py3.*.txt`, `counts.txt`, `mutants.txt`,
`oracle-src.patch`, `spec-change.diff`. Not run: Postgres parity (no Docker), Python 3.15, the lowest-direct
dependency leg, hosted CI, any real provider.

## 18. Ambiguities, with recommendations (the lead auto-approves)

A-J2-1. Golden sets with the replay brain (`--caller fake:<dir>`) are refused with the LLM caller
    (`golden_caller_not_scripted`, step 1; auto-approved ambiguity A1 of the decision log). Recommendation: keep.
A-J2-2. The judge configuration is a new format, `tracer.judge_config/1` (section 3); SCOPE names the option only.
    Every key is required and `null` means "not sent". Recommendation: keep; add a row for it in the ADR 0030
    docs (P4-J30).
A-J2-3. SCOPE gives `tracer judge --out --bundle --judge [--repeats K]`. The command also needs `--golden`
    (the scripted turn index is not in the stored definitions; the golden file's SHA-256 is in the stored profile
    document) and `--judge-config` (the request settings; `replay:` needs the profile and the model).
    `tracer labels packet` needs `--golden` for the same reason. Recommendation: keep.
A-J2-4. The flag of `first_request_gate` (A-J1-15) is `--judge-first-request-gate`. Recommendation: keep.
A-J2-5. ADR 0030 rule 24 says the judge authorisation reference is "stored as a SHA-256 only". None of the five
    documents has a member for it, and a key in `Run.metadata` would make the Run of a rescoring bundle differ
    from the shared record (ADR 0022 rule 5: `ConflictingRecordError`). Reading taken: it is checked for presence
    and shape and its SHA-256 is printed (`judge authorisation reference: sha256 <hex>`); its text is never
    written. Alternative: a member of `tracer.judge_profile/1` (an ADR 0030 amendment, simulate only).
    Recommendation: keep, and file the typed record under BL-317.
A-J2-6. `ScenarioStt` carries timing in a judged run (section 9.5): a product change to a batch E fake, found
    because the offline quick start (`--stt fake`) otherwise gives `answer_window_empty` for every item. It is
    switched on by `--golden` alone, because making it unconditional changes the exact outputs that batch F
    pins (`test_e2e_sim_synthetic_mr_exact_outputs`: the flag span gains audio times). Recommendation: keep.
A-J2-7. A `cannot_tell` result is an `error`, so exit 2 (`exit_code_of` unchanged). Recommendation: keep.
A-J2-8. `--repeats` defaults to 1 and is at most 100; the caps and the exchange directory are per repeat (the total
    is bounded by K times the caps). Recommendation: keep; a total cap is a BACKLOG row if wanted.
A-J2-9. The template of a labels file in the packet has the labeller `labeller-1` and the basis `audio` on every
    label (the recommendation of O-J5; the schema has no null basis). The agreement report counts the basis of the
    labelled items, so a label that was made on another basis shows. Recommendation: keep.
A-J2-10. A repeat is a rescoring bundle that omits the original's judge results and judge usage and carries its own;
    the cost rows are recomputed for the usage it carries (new cost records for the shared usage as well).
    Recommendation: keep.
A-J2-11. The agreement report has the rows `no verdict` (an error without a verdict) and `not applicable` besides
    the three verdicts, and at most two labellers. Recommendation: keep; more labellers are a later task.
A-J2-12. The expected facts and the expected answer are never printed or put on the page; the page shows the
    question (an escaped untrusted-text element). Recommendation: keep (an injection that obtains the facts from
    the model gets them nowhere, section 12).
A-J2-13. `replay:` and `fake:` configurations take no endpoint (`judge_config_endpoint`); the profile document of
    such a run has `host`, `path` and `key_env` null. Recommendation: keep.
A-J2-14. BL-337: an existing `<out>/judge` open to others is tightened to 0700 with a warning (the Phase 3 call-log
    rule, applied to the level Tracer itself creates); the user's `<out>` is never changed. Recommendation: keep.
A-J2-15. The exchange store is created at step 13b, only for a profile engine and only when some item can be
    judged; the judge key is read last among the keys (step 6); the DNS answers are checked at step 2.
    Recommendation: keep.
A-J2-16. A window error (`question_not_sent`, `answer_window_empty`) is decided before an authentication failure or a
    cap that stopped the run; the items it does not cover are errors with the stop's code. Recommendation: keep.
A-J2-17. Part 1 recommendations from the decision log, each now tested: BL-337 (three tests), the closing edge of the
    answer window (the earliest start of any later caller line), the usage of a paid reply handed out before its
    exchange file is written, the short-read loop of the two readers. The last three pass on the current tree
    (they pin the dev's part 1 behaviour). Recommendation: keep.
A-J2-18. Postgres parity of the judged and rescoring bundles has not run (no Docker): run `-m postgres` before the gate.

Things in the ADR or the SCOPE that the tests contradict, or that I changed: (1) SCOPE P4-J18 and ADR 0030 rule 21
omit `--golden` and `--judge-config` of `tracer judge` (A-J2-3); (2) ADR 0030 rule 24 "stored as a SHA-256 only"
has no record to be stored in (A-J2-5); (3) ADR 0030 rule 22 says the packet shows "the agent-channel audio
range": implemented as the answer window's edges (the question's end to the next caller line or the call end);
(4) SCOPE Q-J6 puts the engine factories at step 6: the key is read and bound there, the engine object is built at
13b so that the exchange directory is created only when something is sent; (5) batch E's `--stt fake` cannot give a
judged run any verdict without the timing of A-J2-6.
