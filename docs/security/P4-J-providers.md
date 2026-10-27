<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# P4-J provider dossier: OpenAI-compatible judge providers

Task P4-J03 (security-licensing), Phase 4 batch J, ADR 0030 rule 13 and rule 24. One row per fact
that a provider profile depends on, each with its source URL and the date it was retrieved, or
marked `not verified`. Nothing in this file is a new web claim: every fact below is one that the
staff-reviewer read in a primary source on 2026-10-28 (ADR 0030 "Provider profiles" and SCOPE
sections J.1 and J.5), copied with its source; a fact that the reviewer did not read, or took from
a table without a source, is `not verified` and stays so. Nothing here is a statement of what a
provider does with data: the terms and data-use page of each provider is an owner gate (see the
last section), never an assumption.

No new runtime dependency: the judge client is Tracer's own thin client on `httpx`, which the
Sarvam adapters already use (ADR 0030 rule 12). The `openai` and `anthropic` Python SDKs and any
other provider SDK are not dependencies.

A profile is `verified` only when every fact row of that profile is `verified`. A profile that is
`unverified` is refused for a real run until its first owner-gated request is logged (ADR 0030
rule 13); the profile `local_loopback/1` reaches a literal loopback address only and is never a
real provider. The status table below is machine-read against the profiles shipped in
`tracer_evalproviders.judgeprofiles` (`test_judge_profile_status_matches_dossier`), and every fact
row is machine-read for its URL and date (`test_judge_provider_dossier_cites_url_and_date_per_fact`).

## Profile status

| Profile | Status | Basis |
|---|---|---|
| `anthropic_compat/1` | unverified | the authorisation scheme and the token field are the reviewer's table, with no primary source read; the page itself calls the layer not production-ready |
| `gemini_compat/1` | unverified | the token field, the honoured parameters and the usage fields are not verified; the page says "still in beta" |
| `xai/1` | unverified | the authorisation scheme, token field, `seed`, usage and errors are not verified |
| `openrouter/1` | unverified | the authorisation scheme, the token field, the optional headers and the error codes are not verified |
| `sarvam_v2/1` | unverified | the v2 page lists both authentication headers as required and the repository sends one (the requirement is not verified); the v1 and v2 pages disagree |
| `openai/1` | unverified | the API reference page returned 403; every fact is read from the openai-python source on `main`, not from the API reference and not from a tag |
| `local_loopback/1` | unverified | the behaviour of a user's own local server is not verified; the profile reaches a literal loopback address only |

## Facts

Columns: the profile, the fact, the source URL (one or more, `none` when not verified), the date it
was retrieved (`none` when not verified) and the verification.

| Profile | Fact | Source | Retrieved | Verification |
|---|---|---|---|---|
| `anthropic_compat/1` | Base URL `https://api.anthropic.com/v1/chat/completions` (the page was redirected from docs.claude.com) | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | `response_format` is "Ignored"; the `strict` flag on tools is ignored: "the tool use JSON is not guaranteed to follow the supplied schema" | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | `seed`, `logprobs` and `reasoning_effort` are ignored | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | `temperature` below 1 returns a 400 error on Claude 4.7 and later models ("omit it"); the same for `top_p`; both together return 400 | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | `n` must be 1; `usage.prompt_tokens_details` is "Always empty", so cached tokens are never reported | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | Forced `tool_choice` returns an error on Claude Opus and Sonnet 5.5; the layer is "primarily intended to test and compare model capabilities, and is not considered a long-term or production-ready solution" | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | Non-workspace keys need an `anthropic-workspace-id` header (optional header of the profile) | https://platform.claude.com/docs/en/api/openai-sdk | 2026-10-28 | verified |
| `anthropic_compat/1` | The authorisation scheme is Bearer (the reviewer's table; no primary source read) | none | none | not verified |
| `anthropic_compat/1` | The token field is `max_tokens` (the reviewer's table; no primary source read) | none | none | not verified |
| `gemini_compat/1` | Base URL `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`; Bearer authorisation | https://ai.google.dev/gemini-api/docs/openai | 2026-10-28 | verified |
| `gemini_compat/1` | The page says "still in beta"; parameters it does not list are "silently ignored" (in its image section); the page has internal inconsistencies flagged by the fetch summary | https://ai.google.dev/gemini-api/docs/openai | 2026-10-28 | verified |
| `gemini_compat/1` | `json_schema` structured output is shown only through the SDK `parse` example | https://ai.google.dev/gemini-api/docs/openai | 2026-10-28 | verified |
| `gemini_compat/1` | The token field | none | none | not verified |
| `gemini_compat/1` | Whether `temperature`, `max_tokens` or `seed` is honoured (the page does not list them) | none | none | not verified |
| `gemini_compat/1` | The usage fields | none | none | not verified |
| `xai/1` | Base `https://api.x.ai/v1`; a `chat.completions.create` example with `json_schema` (grok-4.7); xAI calls the Responses API "the preferred way"; the guide's text "SpaceXSI models" is quoted as fetched (the exact page path was not recorded by the reviewer: the structured-outputs guide of the docs site) | https://docs.x.ai | 2026-10-28 | verified |
| `xai/1` | The authorisation scheme is Bearer (the reviewer's table; not in the verified list) | none | none | not verified |
| `xai/1` | The token field | none | none | not verified |
| `xai/1` | Whether `seed` is honoured | none | none | not verified |
| `xai/1` | The usage and error shapes of Chat Completions | none | none | not verified |
| `openrouter/1` | Endpoint `https://openrouter.ai/api/v1/chat/completions`; `model` is optional ("If omitted, the user's or payer's default is used"); the response `model` may differ from the request; `usage.cost` is "Cost in credits" | https://openrouter.ai/docs/api-reference/overview; https://openrouter.ai/docs/features/provider-routing | 2026-10-28 | verified |
| `openrouter/1` | `allow_fallbacks` defaults to true; load balancing is on by default; `data_collection` defaults to `allow` ("providers which store user data non-transiently and may train on it") | https://openrouter.ai/docs/features/provider-routing | 2026-10-28 | verified |
| `openrouter/1` | `seed` is an integer; `json_schema` with `strict` is supported and "The request will fail" if the model does not support it | https://openrouter.ai/docs/features/structured-outputs | 2026-10-28 | verified |
| `openrouter/1` | The authorisation scheme is Bearer and the token field is `max_tokens` (not marked verified in the reviewer's table) | none | none | not verified |
| `openrouter/1` | The optional headers `HTTP-Referer` and `X-OpenRouter-Title` | none | none | not verified |
| `openrouter/1` | The error codes | none | none | not verified |
| `sarvam_v2/1` | Endpoint `https://api.sarvam.ai/v2/chat/completions`; `max_tokens` includes reasoning tokens; `response_format` `json_schema` "guarantees the model generates output matching the supplied JSON Schema" | https://docs.sarvam.ai/api-reference/chat/chat-completions-v2.md | 2026-10-28 | verified |
| `sarvam_v2/1` | `reasoning_effort` defaults to `max` on glm5.3 and deepseekv4.1-flash and counts against `max_tokens`, so the profile sends it explicitly | https://docs.sarvam.ai/api-reference/chat/chat-completions-v2.md | 2026-10-28 | verified |
| `sarvam_v2/1` | 413 above 10,485,760 bytes; 502 `model_call_failed`; 503 `model_overloaded` | https://docs.sarvam.ai/api-reference/chat/chat-completions-v2.md | 2026-10-28 | verified |
| `sarvam_v2/1` | The v1 page shows `POST /v1/chat/completions`, the models `sarvam-105b` and `sarvam-105b-conversations`, and `seed` as "Beta. Determinism is not guaranteed": the v1 and v2 pages disagree (FJ9) | https://docs.sarvam.ai/api-reference-docs/chat/chat-completions | 2026-10-28 | verified |
| `sarvam_v2/1` | The v2 page lists both authentication headers as "required"; the repository sends only `api-subscription-key` (marked UNVERIFIED in the source); the requirement is not verified | none | none | not verified |
| `openai/1` | Endpoint `https://api.openai.com/v1/chat/completions`; Bearer authorisation (per the openai-python SDK source on `main`, files `completion_create_params.py` and `completion_usage.py`; the exact URL path beyond the repository prefix was not recorded by the reviewer) | https://raw.githubusercontent.com/openai/openai-python/main/ | 2026-10-28 | verified |
| `openai/1` | `max_tokens` is "deprecated in favor of `max_completion_tokens`"; `seed` is Beta and "Determinism is not guaranteed"; the usage detail fields (same SDK source) | https://raw.githubusercontent.com/openai/openai-python/main/ | 2026-10-28 | verified |
| `openai/1` | The API reference page on platform.openai.com returned 403: it was not read | none | none | not verified |
| `local_loopback/1` | The behaviour of a user's own local server | none | none | not verified |

## Terms and data use (owner gate)

The terms and the data-use page of each provider were NOT fetched. They are recorded here as an
owner gate (O-J2, ADR 0030 rule 24), never as an assumption: before the first real judge request
security-licensing records each chosen provider's terms and data-use page with its URL and the
retrieval date, and the owner logs the data-sharing decision. Real-caller data never goes to a
judge without a new owner decision (BL-266).

| Provider | Terms and data use | Gate |
|---|---|---|
| anthropic | not fetched; no statement about retention or training is made here | owner gate O-J2 |
| gemini | not fetched; no statement about retention or training is made here | owner gate O-J2 |
| xai | not fetched; no statement about retention or training is made here | owner gate O-J2 |
| openrouter | not fetched; the profile always sends `data_collection: "deny"` and that is a request, not a record of what any upstream does | owner gate O-J2 |
| sarvam | not fetched in this batch; terms 10.5(a) forbid using Sarvam output to "test ... any artificial intelligence system" (read on 2026-10-27, BL-188), so a Sarvam judge needs its own owner decision | owner gate O-J2 and BL-188 |
| openai | not fetched; no statement about retention or training is made here | owner gate O-J2 |

Also not verified by anyone on 2026-10-28: every provider's Marathi competence, pricing and
currencies, and the DNS rebinding race in `httpx` (BL-326).
