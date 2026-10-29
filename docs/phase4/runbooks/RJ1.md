<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Run book RJ1: the first real judge run (P4-J28)

Status: prepared 2026-10-28, NOT executed. Nothing in this file has sent anything. Owner-gated: no real send before the confirmations in section 10 are answered. Audit: `docs/security/P4-J-audit.md` (GO with conditions, section 1 below). Provider facts: `docs/security/P4-J-providers.md` (the profile `openrouter/1` is `unverified`). Rules: ADR 0030. Owner settings: decision log entries "OWNER ANSWERS" and "OWNER (verbatim): 1. Go 2. Fine" (2026-10-28), model lookup entry "QWEN LOOKUP" (2026-10-28).

RJ1 checks the judge apparatus, not the agent and not the model. The call is offline: the fake agent of `docs/examples/simulated-mr/fake-agent.json` speaks the ten synthetic plants, fake text-to-speech and fake speech-to-text, no socket. Only step 13b (the judge) goes to OpenRouter, once per golden item, with synthetic text only: the golden set questions and expected facts, and the fake agent's words as the fake speech-to-text returned them (BL-266: no real-caller data). The text is invented and not reviewed by a native speaker (BL-169). The judge signal is uncalibrated; nothing is said about any model's Marathi or any agent. Output is counts only: no question, expected fact, answer, prompt, reply, key or path is quoted in a report or log of this run.

## 1. Conditions of the audit and where this book meets them

| Condition (P4-J26 verdict) | Met by |
|---|---|
| J-F1 fixed first | Fixed in 8f780a8 (BL-362); the template hashes are pinned by tests |
| `--judge-first-request-gate` for the first request; the exact Qwen model id from OpenRouter's own list shown to the owner (BL-368) | The flag is on every real command (section 4). The id `qwen/qwen3.7-plus` was read from OpenRouter's own pages on 2026-10-28 and the owner accepted it ("Fine", 2026-10-28). The lead shows it again at confirmation 1 (section 10) |
| `data_collection` stays `deny`; ask the owner before relaxing if a call is refused | The profile always sends `deny` (not configurable). Stop condition S3 (section 7) |
| The owner reviews the judge allowlist; the key only in a git-ignored env file; the caps set | Sections 3 and 5; confirmations 3 to 5 |
| The printed authorisation-reference hash is copied into the decision log (J-F2, BL-363) | Section 8, record R-1 |
| Synthetic golden set and offline M1 bundle only (BL-266) | Section 2; the golden set is the public `golden_mr.json` fixture |

## 2. Pre-registered values (owner, 2026-10-28), as flags and file keys

| Value | Where |
|---|---|
| Provider OpenRouter, profile `openrouter/1`, host `openrouter.ai`, path `/api/v1/chat/completions` | allowlist entry and `endpoint` of the configuration (section 3) |
| Model `qwen/qwen3.7-plus` | configuration `model` |
| Upstream provider `alibaba` only | configuration `provider_slug`; the profile sends `provider: {order: [alibaba], allow_fallbacks: false, require_parameters: true, data_collection: deny}` (the last three are fixed by the profile, not by the file) |
| `max_tokens` per request 2048 | configuration `max_tokens` |
| temperature 0, seed 1 | configuration `temperature`, `seed`; the record says `seed_guarantee: none` ("no determinism guarantee") |
| Reasoning settings | configuration `reasoning_effort` is `null`; the profile `openrouter/1` cannot send any (section 9, C-2) |
| Request cap 40, token cap 100000 | `--max-judge-requests 40`, `--max-judge-tokens 100000` |
| Repeats K | 5, as `tracer judge --repeats 5` |
| Key variable | `OPENROUTER_API_KEY`, in the git-ignored env file named by `--env-file` |

Scale: the golden set has 10 items (8 `all_facts`, 2 `equivalent_answer`), so a simulate run makes 10 requests and at most 20 with regenerations. Each repeat of `tracer judge` is bounded by its own caps (they belong to one repeat, ADR 0030 rule 21); five repeats are therefore bounded by 5 x 40 = 200 requests and 5 x 100000 tokens, and normally make 50 to 100 requests. The token cap is checked as reported usage plus the next request's `max_tokens`, so one request's prompt can cross it (BL-295).

## 3. Inputs (all synthetic, generated, outside the repository)

`make_inputs.py` copies five public files of `docs/examples/simulated-mr` (evaluator, persona, sim profile, fake agent, golden set), refuses unless the golden set equals the `golden_set` member of `packages/evalcore/tests/fixtures/golden_mr.json`, and writes two files. It refuses a destination inside the repository tree. Directory 0700, files 0600. It reads no key and opens no socket.

The judge configuration (`tracer.judge_config/1`; every key is required):

```json
{
  "format": "tracer.judge_config/1",
  "profile": "openrouter/1",
  "endpoint": "https://openrouter.ai/api/v1/chat/completions",
  "model": "qwen/qwen3.7-plus",
  "max_tokens": 2048,
  "temperature": 0,
  "seed": 1,
  "reasoning_effort": null,
  "provider_slug": "alibaba",
  "extra_headers": {}
}
```

The owner's judge allowlist (`judge-allowlist.txt`; the file name matches the guard, the directory is outside the repository; one entry per line, `#` starts a comment, fields `profile=` and `key_env=` only):

The file holds one line: the URL, one space, then the fields, written in two pieces here:

```
URL:    https://openrouter.ai/api/v1/chat/completions
FIELDS: profile=openrouter/1 key_env=OPENROUTER_API_KEY
```

The URL is the exact one of the profile table (`judgeprofiles._ENDPOINTS`, dossier row for `openrouter/1`, retrieved 2026-10-28); the entry must equal the configuration's `endpoint` or the run is refused with `host_not_allowed`. The file must be a regular file with no group or other bit (`allowlist_*` refusals otherwise).

## 4. The commands

Placeholders: `$IN` the inputs directory, `$OUT` the run output directory (outside the repository), `$ENVF` the git-ignored env file holding `OPENROUTER_API_KEY` (read by name only, at the real run), `$AUTH` the judge authorisation reference (1 to 200 characters; only its SHA-256 is printed).

```
tracer simulate --agent fake:$IN/fake-agent.json --evaluator $IN/evaluator.json \
  --persona $IN/persona.json --sim-profile $IN/sim-profile.json \
  --tts fake --caller scripted --stt fake --authorisation-reference rj1-offline-call \
  --golden $IN/golden-set.json --judge-config $IN/judge-config.json --judge openrouter/1 \
  --judge-allowlist $IN/judge-allowlist.txt --max-judge-requests 40 --max-judge-tokens 100000 \
  --judge-authorisation-reference "$AUTH" --judge-first-request-gate \
  --env-file $ENVF --out $OUT                                     # add --dry-run for the plan

tracer judge --out $OUT --bundle $BUNDLE --golden $IN/golden-set.json \
  --judge-config $IN/judge-config.json --judge openrouter/1 --repeats 5 \
  --judge-allowlist $IN/judge-allowlist.txt --max-judge-requests 40 --max-judge-tokens 100000 \
  --judge-authorisation-reference "$AUTH" --judge-first-request-gate --env-file $ENVF
```

`$BUNDLE` is the `bundle:` line of the simulate output. `tracer judge` needs the flag `--judge-first-request-gate` too, because the profile stays `unverified` in code until its dossier rows are verified (ADR 0030 rule 13). The judge model of a run is fixed when `tracer simulate` stores the definitions (FJ6); `tracer judge` refuses another model or profile (`judge_model_differs`).

The wrapper `rj1.sh` (kept by the lead outside the repository, with `make_inputs.py`, `check_judge.py` and `audit_dryrun.py`) builds exactly these commands. Its default is a plan. `--go` runs the simulate and `--repeat K` (K from 1 to 5) runs `tracer judge`; both refuse unless `RJ1_GO=yes-owner-approved-2026-10-28` is set and `RJ1_ENV_FILE` (an existing file) and `RJ1_AUTH_REF` are set. `--go` also refuses when `$OUT` exists (one simulate, one fresh directory), and re-runs the audited dry run first and refuses unless it shows `judge max requests: 40`. The wrapper writes stdout to files outside the repository and prints only the count lines.

## 5. Steps

1. **Plan (no key, no send).** `rj1.sh`. It writes the inputs, runs the local checks (`check_judge.py`: the allowlist parses; without the gate the profile is refused as `profile_unverified`; with the gate the entry matches; the planned parameters are printed; the DNS answer is a stub, nothing is resolved) and runs the dry run under an audit hook with `env -i` and an unreadable env file (mode 0000): expected 0 network events, 0 process events, 0 filesystem changes, 0 opens for write, 0 opens of the env file. A dry run reads neither the allowlist nor the key and does no DNS (it returns after step 1), which is why the allowlist is checked by the script.
2. **Read the plan.** Expected printed judge lines, in this order, then `dry run: nothing was sent`:
   `judge: uncalibrated signal, engine openrouter/1, model qwen/qwen3.7-plus`, `judge golden set: mr_apparatus_set, 10 items (8 all_facts, 2 equivalent_answer)`, `judge requests: up to 20 (one for each item, one more for each regeneration)`, `judge max tokens per request: 2048`, `judge max requests: 40`, `judge max tokens: 100000`. The plan also shows `caller: scripted (10 lines)`, `tts: fake, 272 characters to synthesise in 10 requests, 0 cache hits`, `llm: none`. The host, path and upstream are not in the dry-run plan; they are in the local check of step 1 and in section 3.
3. **Simulate with the judge (OWNER-GATED SPEND, real OpenRouter requests).** `RJ1_GO=... RJ1_ENV_FILE=... RJ1_AUTH_REF=... rj1.sh --go`. About 20 s for the call, then up to 20 requests, one at a time. Step 2 of the command does a real DNS lookup of `openrouter.ai` (the only lookup; it must give public addresses). Read section 6 and 8 before step 4.
4. **K repeats.** After the simulate is clean (section 8 criteria A), `RJ1_GO=... rj1.sh --repeat 5`. Each repeat stores a new rescoring bundle of the same run.
5. **Label loop (offline, fake humans).** `tracer labels packet|check|agreement` with a labels file written by `fill_labels.py` or the apparatus labels of P4-J29. No request. The packet and the labels file hold call text: keep them outside git.
6. **Hand-off.** Counts only into the decision log and the P4-J29 measurement report. Do not paste text, paths, prompts or replies into chat.

## 6. What is logged, before and after (counts only)

Before the first request: the dry-run plan above; the owner's confirmations (section 10); the decision log entry "RJ1 start" with the date, the git HEAD, the model id, the caps and the confirmations. After step 3 (from stdout, the call log and the store): `result`, `end reason`, `bundle`, the hash line `judge authorisation reference: sha256 <64 hex>` (copied into the log: record R-1), `judge agrees|disagrees|cannot_tell|error|not applicable: n of 10 items`, `judge usage: <r> requests, <i> input tokens, <o> output tokens`, `cost:` line, exit code. The 10 `judge item <id>: ...` lines hold ids, verdicts, reason codes and counts, not text; they are kept in the local stdout file, not copied into a committed file. `sim_call_log.jsonl` gets one line before and one after each request (stage, index, attempt, bytes, status, elapsed, request id) and never text. No committed file holds an answer, a prompt, a reply, an expected fact or a question. The exchange files `$OUT/judge/<bundle>/item-<id>-<attempt>.json` (mode 0600) hold the messages and the raw reply: local only, never read into a report, never staged.

## 7. Stop conditions

Stop and report (no further command) on any of:

| Id | Condition | Action |
|---|---|---|
| S1 | A refusal exit 4 or 64, or a printed `error: <code>` | Report the code; the cause is an input, not a retry |
| S2 | `judge_auth_failed` on the first item (the tool stops further requests by itself) | Report; the owner checks the key outside the chat |
| S3 | OpenRouter refuses because of `data_collection: deny` (no endpoint matches the data policy), seen as every item ending in `judge_request_failed` with the same HTTP status in the call log | STOP. The owner decides before anything is relaxed; the profile has no setting to relax it, so a change is a code and ADR change |
| S4 | The model or upstream rejects a parameter (`require_parameters` true: seed, temperature, response_format, structured outputs) | Report the status and request id; do not drop a setting |
| S5 | A cap is reached (`judge_requests_cap_reached` or `judge_tokens_cap_reached`) | Report; do not raise a cap without the owner |
| S6 | Any key, prompt, reply, question, expected fact or path in stdout, stderr or a committed file | Treat as a leak: stop, tell the owner, rotate the key if it is a key |
| S7 | Ctrl-C | The tool hangs up gracefully, prints `usage recovery: <path|not written>`, and writes `usage-recovery-<UTC>.json` (class names and usage records only); a second signal aborts |

The kill-switch file of the call does not govern the judge step.

## 8. Exit codes, pass and fail of the RUN, and what to record

| Exit | Meaning |
|---|---|
| 0 | call established; every result scored or `not_applicable`; a `disagrees` verdict is a result, not a failure |
| 1 | unexpected failure (a recovery file may exist) |
| 2 | a result not scored: a judge `error` of any code, including a valid `cannot_tell` (`judge_undetermined`); `tracer judge` gives 2 if any item of any repeat is an `error` |
| 3 | speech-to-text failed |
| 4 | input refused (before any key read or connection) |
| 5 | call not established |
| 64 | usage |

**A. The simulate step passes as a run** (not as a verdict on the model or the agent) when all hold: exit 0; `result: call established`; `judge not applicable: 0 of 10 items`; `judge error: 0 of 10 items`; `judge usage` shows between 10 and 20 requests; request count and tokens within the caps; `cost:` is `not available (no_price_for_model)` (no USD price file; OpenRouter's `usage.cost` is "Cost in credits" and is never stored as a cost, no currency is converted); the bundle is stored; the exchange files exist with mode 0600; the hash line was printed; no secret or text leak (S6). Exit 2 with errors that are `judge_undetermined` or `judge_output_invalid` is an apparatus finding (counts recorded), not a stop; `judge_request_failed`, `judge_auth_failed` or a cap error is a stop (S2 to S5). The `disagrees` and `agrees` counts are recorded and not interpreted.

**B. The repeats pass as a run** when `repeats: 5` is printed, five `repeat r of 5: <new bundle id>` lines appear, each with a `judge changed from the stored run: n of 10 items` line, the store is written, and no stop condition occurred.

**Record (counts only, in the decision log "RJ1 COMPLETE" and later P4-J29):** exit codes; for the simulate and for each repeat the five count lines and `judge usage`; per repeat `judge changed from the stored run: n of N items` (5 numbers); the K-repeat stability counts derived from the six runs' `judge item` lines: items with the same verdict in all 6 runs (a of 10) and items that changed in at least one (b of 10); regenerations per run (requests minus judged items) and `judge_output_invalid` count; total requests and tokens of the whole RJ1; R-1 the authorisation reference hash; the git HEAD and the wrapper's checksum. Unknown until the first real request: how `qwen/qwen3.7-plus` answers within 2048 tokens (see C-2), and what OpenRouter does with `deny` for this upstream.

## 9. Contradictions and limits between the approved settings and the code

* **C-1 (no contradiction): provider pinning.** The profile can express it: `provider_slug` `alibaba` becomes `provider.order: ["alibaba"]` with `allow_fallbacks` false, `require_parameters` true, `data_collection` `deny` (fixed members). `alibaba` matches the slug syntax. The code never checks the slug against OpenRouter's list; it was read as a tag of the model's endpoints on 2026-10-28. A wrong slug fails at the first request, not before.
* **C-2 (limit): no reasoning setting can be sent.** `openrouter/1` refuses a `reasoning_effort` (`reasoning_effort_not_supported`), so the configuration has `null`. The lookup says the model accepts a reasoning parameter and that it is not known whether it reasons by default. If it does, reasoning tokens may count against `max_tokens` 2048 and a reply may end with `finish_reason` `length`, which is refused and never repaired (one regeneration, then `judge_output_invalid`). The owner chose 2048 for room; if the first run shows many `judge_output_invalid`, that is a finding to report, and the remedy (a larger `max_tokens`, or a profile change) is the owner's decision.
* **C-3 (limit): `tracer judge` caps are per repeat.** See section 2: 5 repeats can use up to 200 requests; the registered 40 and 100000 apply to each command repeat, not to RJ1 in total.
* **C-4 (limit): the gate is on every command.** The profile stays `unverified` in code, so `--judge-first-request-gate` is needed on `tracer judge` as well; it is not a one-request switch.
* **C-5 (limit): no determinism.** Temperature 0 and seed 1 are requests; the profile records `seed_guarantee: none`, and the dossier marks the `openrouter/1` token field, optional headers and error codes as not verified.
* The dry-run plan prints no host, path or upstream; they are checked by `check_judge.py`.

## 10. The owner must confirm before `--go`

1. The model id `qwen/qwen3.7-plus` and the upstream `alibaba` as shown (BL-368), and the pricing/currency not being stored.
2. That the profile has no way to send a reasoning setting (C-2) and 2048 stays.
3. The judge allowlist file: the single line of section 3, reviewed by the owner (path outside the repository, mode 0600).
4. The env file path (git-ignored, mode 0600) holding `OPENROUTER_API_KEY`, and that the key is never opened by an agent; and the authorisation reference text (printed as a hash; the owner or lead copies the hash into the decision log).
5. The caps 40 and 100000, per command (C-3), and K = 5.
6. That a refusal caused by `data_collection: deny` stops the run and returns to the owner (S3).
7. That the request body carries the synthetic questions, expected facts and the fake agent's words to OpenRouter and Alibaba (O-J2 accepted 2026-10-28), and that the terms and data-use pages of OpenRouter and Alibaba were not read (dossier: owner gate).
8. The output directory (outside the repository, git-ignored), and that the store, exchange files, packet and labels stay local.
9. That `RJ1_GO` is set by the owner or on the owner's explicit message, not by an agent.

## 11. Known limits

* A dry run does not exercise the allowlist, the DNS check, the key or the engine; the local checks and the real run do.
* The DNS rebinding race of the judge allowlist (BL-326) is not closed; TLS bounds it.
* Injection by the agent's words is a residual (J-F5, BL-365): the fake agent's plants are benign, so RJ1 does not measure it.
* The result says nothing about Marathi competence of any model: the golden set is synthetic and not reviewed (BL-169).
