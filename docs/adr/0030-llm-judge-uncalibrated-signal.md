# ADR 0030: LLM judge as an uncalibrated signal: golden sets, OpenAI-compatible provider profiles, human labels

- Status: Accepted (Phase 4 batch J, task P4-J01; schema-architect with security-licensing). Records the owner decision of 2026-10-28 (the LLM judge is built in Phase 4 as an uncalibrated signal, provider-agnostic through the OpenAI chat-completions specification) and the questions Q-J1 to Q-J20 of `docs/phase4/SCOPE.md` section J, auto-approved by the lead on the recommended option under the owner's standing rule of 2026-10-23 (decision log 2026-10-28 "OWNER DECISION ... LLM JUDGE" and "PHASE 4 BATCH J (LLM JUDGE, uncalibrated signal) SCOPE DELIVERED"). The rejected options and trade-offs are the staff-reviewer's (scope report of 2026-10-28) where the report gives them; where it gives none, the rule says "(architect)" and the lead may change them. Every provider fact below is what the staff-reviewer read in a primary source on 2026-10-28, with its source; a fact marked "not verified" was not read and is not assumed. The terms clauses are facts read from the terms, not legal advice. The points the scope leaves open are listed under "Ambiguities resolved" for the lead. Open for the owner: O-J1 to O-J6 (SCOPE section J.6), all before the first real judge request (P4-J28), none before the offline work. Accepted when it moved from the staging directory to `docs/adr/` (task P4-J01, 2026-10-28, lead auto-approval of the recommended options and of Ambiguities 1 to 6), in the same change as the amendments of ADRs 0023, 0024 and 0027 (task P4-J02) and ADR index revision 52; the owner gates O-J1 to O-J6 are not decisions of this ADR and bind only the first real judge request.
- Date: 2026-10-28
- Task: P4-J01 (amendments P4-J02; documents P4-J03 to P4-J05; implemented by P4-J06 to P4-J25, P4-J30 and P4-J32; audited by P4-J26; first real run P4-J28; measurement P4-J29; gate P4-JG)

## Context
The owner decided on 2026-10-28 to build the LLM judge in Phase 4, "build first, calibrate later", to measure the agent's generated answers against a golden set (about ten questions with expected answers or facts), through the OpenAI chat-completions specification so that Anthropic, Gemini, xAI (Grok), OpenRouter and others can be used, each provider's compatibility verified from its documentation and never assumed. Every verdict is labelled uncalibrated; human labels are collected through a label loop, and the tool reports judge-human agreement as data, never as accuracy; there is no pass or fail claim of quality.

The staff-reviewer's findings shape the design (SCOPE section J.1):
- FJ1: Anthropic's OpenAI-compatible layer ignores `response_format`, the tool `strict` flag, `seed`, `logprobs` and `reasoning_effort`; a `temperature` below 1 returns HTTP 400 on Claude 4.7 and later; `n` must be 1; `usage.prompt_tokens_details` is "Always empty"; forced `tool_choice` returns an error on Claude Opus and Sonnet 5.5; the page calls the layer "not considered a long-term or production-ready solution". So no rule may rely on provider-side schema enforcement: Tracer's own validator decides.
- FJ2: "OpenAI-compatible" is not one contract; each provider honours or silently ignores different fields, with different model ids, auth headers, error shapes and usage fields.
- FJ3: OpenRouter's defaults (optional `model`, fallbacks and load balancing on, `data_collection` `allow`, a response model that may differ, `usage.cost` in credits) are unsafe for an evaluation tool.
- FJ4: schema 0.0.1 holds the judge without a change, with one limitation (rule 11).
- FJ5: making the inert expected-outcome check real would give a pass or fail of the agent from an uncalibrated model (rule 10).
- FJ6: a judge cannot be added to a stored run unless its definitions were in the run's snapshot from the start, so the judge model is fixed at `tracer simulate` time (rules 8 and 21).
- FJ7: judge stability is measured on fixed answers (rule 21).
- FJ8: a Sarvam judge meets Sarvam terms 10.5(a) head-on (rule 24).
- FJ9: the Sarvam chat v1 and v2 documentation pages disagree (the profile table).
- FJ10: golden items need the scripted caller in Phase 4 (rules 1 and 3).

## Decision
Machine-read by `test_adr0030_rules_are_tested`, in the format of ADRs 0026 to 0029: every numbered rule of this section and every invariant cites at least one test name in backticks; a cited name that no test defines yet must be one that the task table of `docs/phase4/SCOPE.md` (section A.10) names, and that set of not-yet-defined names shrinks to empty by the gate (P4-G, gate item 15). The ADR is Accepted: its rules bind batch J, which implements them task by task.

1. **Scope: simulated scripted runs only (owner decision 2026-10-28; Q-J9, Q-J10; FJ10).** The judge runs only inside `tracer simulate`, on a run whose caller is the scripted brain (ADR 0028 rule 1), on the golden items of one golden set (rule 2). It never runs on a Phase 3 recorded call (`tracer score`), never on real-caller data (BL-266), never on flagged spans (BL-174), and never on the whole call (the expected-outcome check stays inert, rule 10). A run without `--golden` is unchanged: same records, same key set, no judge request. Rejected: the judge on recorded or real calls (BACKLOG row, see SCOPE J.10); golden runs with the LLM caller (the question text is generated, so there is no fixed question to compare with, FJ10); the agent's export as the answer source (it needs a shared time base, BL-167, or turn ids). Trade-off: in Phase 4 the judge sees only simulated scripted calls. Tests: `test_golden_set_refused_with_llm_caller`, `test_tdeg_run_without_golden_set_unchanged`.
2. **The golden set document `tracer.golden_set/1` (P4-J04 to P4-J06).** A golden set is a strict JSON file read with `loads_strict` (a duplicate key is refused), with closed keys and a version member naming `tracer.golden_set/1`; at most 100 items, at most 10 expected facts per item, at most 300 characters per fact and at most 1,000 per expected answer; text in NFC and within the indic-linguistics character table for mr. Each item binds to one scripted turn (rule 3) and names its match mode (rule 7: `all_facts` with expected facts, or `equivalent_answer` with an expected answer). Every item of the project's synthetic fixture (`packages/evalcore/tests/fixtures/golden_mr.json`, P4-J04) carries `needs_native_review: true` and is for the apparatus only; a real golden set is the owner's (O-J6). Refusals happen at step 1 of rule 20, before any key is read. The key and SHA-256 of the golden set are recorded in `tracer.judge_profile/1` (rule 8). Its JSON Schema (draft 2020-12, closed keys) is machine-checked against the constants `tracer-evalcore` ships (P4-J05). Rejected (architect): a lenient loader (a repeated key would silently keep one value); no caps (a large set multiplies requests and spend). Trade-off: a set larger than the caps is split by the user into several runs. Tests: `test_golden_set_strict_json_closed_keys_and_version`, `test_golden_set_caps_items_facts_chars`, `test_golden_fixture_mr_valid_and_marked_for_native_review`, `test_adr0030_documents_match_json_schemas`.
3. **Items bind to scripted turns; the LLM caller is refused (Q-J9; FJ10).** Each item names `scripted_turn_index`, and its `question` must equal `Evaluator.scripted_turns[scripted_turn_index].text` byte for byte; a mismatch, an index that does not exist, a golden set with `--caller` set to the LLM caller, and an item on a turn that has a scheduled barge-in (`barge_in_after_ms`, ADR 0026 rule 10; the caller cuts the agent's answer by design) are refused at step 1, before any key is read. Rejected (architect): matching a question to a turn by similar text or by position alone (a reordered or edited script would silently bind an item to another line). Trade-off: editing a scripted line requires editing the golden item in the same change. Tests: `test_golden_item_question_must_equal_scripted_turn_text`, `test_golden_set_refused_with_llm_caller`, `test_golden_set_on_barge_in_turn_refused`.
4. **The answer window (P4-J07).** The window of an item opens at the end edge of the caller turn holding the item's line (the edge of ADR 0027 rule 10) and closes at the start of the next sent caller turn, or at the end of the call for the last line. It holds the `our_stt` agent-channel turns (role `agent` from the declared map, ADR 0027) whose `start_ms` falls inside it, in `seq` order, joined by one U+000A, which is the boundary between two turns and is kept by the cleaning of rule 6 (amended 2026-10-28, BL-362: the first text removed it, so the last word of a turn fused with the first word of the next). A turn that starts before the edge is excluded and counted (`excluded_turns` in `tracer.judge/1`). The window is never built from the agent's export and never from the script. It is a pure function of the recorded frame log and the STT turns, so a recorded log gives an exact window. Rejected: the agent's export as the answer (no shared time base, BL-167; FJ10); including turns that start before the question ends (they answer an earlier line or overlap the caller). Trade-off: the noise of our STT on the agent channel is part of the judged text; the label `basis` (rule 22) keeps it apart from judge error. Tests: `test_answer_window_from_end_of_question_to_next_caller_line`, `test_answer_window_excludes_turns_starting_before_question_end`, `test_answer_window_last_question_ends_at_call_end`, `test_answer_window_reads_our_stt_agent_turns_never_export_or_script`, `test_words_of_adjacent_turns_stay_separate`.
5. **What the judge sees and never sees (Q-J3).** A judge request holds the item's question (the exact scripted line), that item's expected facts or expected answer, and the window text of rule 4, and nothing else. It never holds: a key, a URL or an identifier; another item, its answer or its verdict; `Evaluator.instructions` or `expected_outcome`; persona or test-profile values; the caller brain's prompt; a metric definition other than its own template; the agent's export; any other result of the run. Request bodies are built offline in the tests and inspected for each forbidden item, with canaries. Rejected (architect): the evaluator instructions or the whole transcript "for context" (the judge would judge more than the item and could leak the outcome criterion). Trade-off: an answer that depends on an earlier line is judged without it and may come back `cannot_tell`. Tests: `test_judge_request_holds_only_question_expected_and_answer`, `test_judge_request_never_holds_key_instructions_profile_or_other_items`, `test_judge_never_receives_caller_brain_prompt_or_profile_values`, `test_judge_injection_cannot_leak_expected_facts_into_any_output`.
6. **The prompt: fixed templates, JSON-encoded untrusted data, fences, cleaning (P4-J08; BL-141).** The system message is a fixed template per match mode, shipped by `tracer-evalcore` under a versioned id; the SHA-256 of each template is recorded per mode in `tracer.judge_profile/1`. The user message is exactly one JSON document `{question, expected_facts or expected_answer, agent_answer}`, JSON-escaped, between fixed fence lines, with `agent_answer` labelled as untrusted data that holds no instruction. Before encoding, the agent's text is cleaned as ADR 0028 rule 4 cleans it, except that the window text is split at U+000A, each turn is cleaned alone (a turn with nothing left is dropped) and the turns are joined with one U+000A that the cleaning keeps (BL-362), and every occurrence of a fence sequence in any of the three texts is replaced by a fixed escape token. The cleaning is deterministic, so a replay builds byte-identical requests (rule 19). Rejected: a random fence per request (requests would not be reproducible, as ADR 0028 rule 4); the agent's text as a system message (it would read as an instruction). Trade-off: a fence lowers the chance that a model obeys injected text and cannot make it zero, so rule 7 trusts nothing in the reply. Tests: `test_agent_answer_is_json_encoded_inside_fenced_untrusted_block`, `test_delimiter_sequences_in_agent_text_are_neutralised`, `test_judge_prompt_template_sha_recorded_per_match_mode`, `test_judge_injection_forged_delimiter_stays_data`, `test_words_of_adjacent_turns_stay_separate`.
7. **The output contract and its validator: never repaired, one regeneration (Q-J5, Q-J11; FJ1).** The reply's `message.content` must be exactly one JSON object with the closed keys `{verdict, reason_code, facts}`: `verdict` one of `agrees`, `disagrees`, `cannot_tell`; `reason_code` one of the closed set `all_facts_stated`, `fact_contradicted`, `fact_absent`, `meaning_differs`, `meaning_same`, `answer_off_topic`, `answer_unintelligible`, `insufficient_information` (proposed; indic-linguistics reviews the wording, P4-J04); `facts` a list of `{index, status}` with `status` one of `stated`, `contradicted`, `absent`, `cannot_tell`, one entry per expected fact in mode `all_facts` and an empty list in mode `equivalent_answer`. The cross-field rules are enforced (in mode `all_facts`): `agrees` iff every fact is `stated`; `disagrees` iff any fact is `contradicted` or `absent`; `cannot_tell` iff no fact is `contradicted` or `absent` and at least one is `cannot_tell` (the partition reading of Ambiguity 2). At most 4,096 bytes. There is no free text and no "reason" prose. The validator refuses and never repairs: a code fence around the JSON, prose before or after it, an unknown key or value, or an inconsistent field is invalid. An invalid reply, or `finish_reason` `length`, gets one regeneration (the identical request); a second invalid reply makes the item's result `error` `judge_output_invalid`. Only counts of invalid replies are kept, never their text. Rejected: tool-call JSON (the `strict` flag is ignored on Anthropic and forced tool use returns an error on Claude 5.5, FJ1); repairing fenced JSON (a parser that guesses); storing the model's explanation (unvalidated text in records). Trade-off: a model that wraps JSON in a fence fails twice and the item is an error, not a verdict. Tests: `test_judge_output_closed_keys_and_values_only`, `test_judge_output_cross_field_consistency_enforced`, `test_judge_output_over_size_cap_refused`, `test_judge_output_never_repaired_one_regeneration_then_error`, `test_judge_reason_code_from_closed_set_only`, `test_judge_injection_cannot_change_output_format`, `test_judge_injection_cannot_flip_verdict_without_consistent_fields`, `test_judge_injection_cannot_store_free_text`.
8. **Mapping to records (Q-J7; P4-J10; no schema change).** Each item gives one `MetricDefinition` of kind `llm_judge`, key `judge_golden_<item_id>`, with `value_type=enum` and options `[agrees, disagrees]`, no pass rule, scope `per_conversation`, `requires=["transcript","turn_timing"]`, `languages=["mr"]`, `script_sensitive=false`, the fixed template of its mode as `prompt_template`, and `judge_model_ref={provider: <profile label>, model: <model>}`; its binding carries the item's texts as parameters with no default; each is an optional rubric item, and the inert check stays the only required item (rule 10). A verdict `cannot_tell` gives `error` with `error.code=judge_undetermined`, never a value. `JudgeInfo` holds `prompt_sha256` (the SHA-256 of the JCS of the messages sent), `params` as sent, `reported_model` verbatim, an empty `per_turn`, and no `confidence`; there is no `p_yes` anywhere. `MetricResult.metadata["tracer.judge"]` holds the closed document `tracer.judge/1`: verdict, reason code, fact statuses, the window's turn seqs, the excluded-turn count, attempts, the regeneration flag, the exchange SHA-256 (rule 19), `calibration: "uncalibrated"` and `confidence: "none_claimed"`. `ConfigSnapshot.metadata["tracer.judge_profile"]` holds `tracer.judge_profile/1`: profile, host, path, model, params as sent, caps, the key variable's name, the golden set's key and SHA-256, the template SHA-256 per mode and `calibration: "uncalibrated"`; the endpoint path is a stated exception to ADR 0027 rule 1's "no path" (it is not a file path; proposed amendment 12a). Each result's evidence is one `EvidenceRef` of kind `turn` per window turn, and `transcript_ids` names the `our_stt` transcript. Every judged bundle passes `check_run_bundle`. Rejected (architect): one definition for the whole golden set (one result could not say which item it judged); storing `cannot_tell` as a third enum value (it is the absence of a verdict, and an enum value would count as one). Trade-off: the snapshot carries one definition per item, so a golden set change is a new snapshot. Tests: `test_judge_definitions_are_llm_judge_enum_without_pass_rule`, `test_cannot_tell_is_error_judge_undetermined_never_a_value`, `test_judge_result_carries_judgeinfo_without_confidence_or_p_yes`, `test_judged_bundle_passes_check_run_bundle`, `test_judge_result_evidence_refs_name_answer_window_turns`.
9. **"Uncalibrated" everywhere: no confidence, no pass or fail (owner decision 2026-10-28).** Every judge line of the CLI, the report section (titled "uncalibrated judge signal"), the agreement report (rule 22) and the concept page `docs/concepts/llm-judge.md` says that the signal is uncalibrated, shows per item the verdict, the reason code, the fact statuses and "n of N" counts with their denominators, shows transcript text escaped only (ADR 0017), and holds none of the claim words of ADR 0023 rule 1, A1 and the proposed A4; the concept page holds the one fixed sentence of A4 in its "Not claimed" block. No number of the judge is a confidence, a probability, a rate or a score of the agent. Rejected (architect): a confidence from token log-probabilities or from the model's own statement (log-probabilities are ignored on the Anthropic layer, FJ1, and neither is calibrated against human labels); a summary percentage (small n; counts with denominators only, rule 22). Trade-off: a reader gets counts, not a headline number. Tests: `test_judge_lines_labelled_uncalibrated_everywhere`, `test_judge_report_never_claims_accuracy_or_quality`, `test_judge_report_shows_counts_with_denominators`, `test_judge_result_carries_judgeinfo_without_confidence_or_p_yes`, `test_judge_concept_page_states_no_claims`.
10. **The expected-outcome check stays inert; no RunVerdict (FJ5).** Every run, judged runs included, keeps the inert placeholder of ADR 0023 rule 17 and ADR 0027 rule 12 (`simulated_call_no_outcome_check`, judge model `{"provider":"none","model":"not_run"}`) as the only required rubric item; the judge definitions of rule 8 are optional items with no pass rule; no `RunVerdict` is produced, and every output keeps "outcome not evaluated". Rejected: making the check real for simulated runs (it would give `passed` true or false and a RunVerdict pass or fail of the agent from an uncalibrated model, against the owner rule of no pass or fail claim of quality; BL-270 stays open). Trade-off: the whole call's outcome is still not evaluated in Phase 4. Tests: `test_expected_outcome_check_stays_inert_with_judge`, `test_judge_report_states_outcome_not_evaluated`, `test_outcome_check_inert_and_no_verdict`.
11. **Language: mr only, and the 0.0.1 coupling (Q-J10; FJ4).** Golden sets, templates and judge definitions are mr only (`languages=["mr"]`). In schema 0.0.1 an `llm_judge` result whose `requires` names `transcript` is re-checked by the bundle over every turn of the transcripts the result names, not over the window of one item (`bundle._turns_read`), so in a simulated run any agent STT turn in another language anywhere in the call makes every judge result `not_applicable` `unsupported_language` (ADR 0023 amendment A3). This is accepted for Phase 4; there is no workaround; the fix is a per-result turn subset in schema 0.0.2 (BL-193, extended). Rejected: a schema change in Phase 4 (Q1 holds); other languages now (no native review; BACKLOG row). Trade-off: one stray non-mr agent turn removes every judge result of that run. Tests: `test_tdeg_judge_non_mr_turn_unsupported_language_all_items`, `test_golden_fixture_mr_valid_and_marked_for_native_review`.
12. **The wire subset (Q-J1).** Request body: `model` (always sent), `messages` (one `system` and one `user`, string content), `max_tokens` or `max_completion_tokens` as the profile says (always sent; required in the configuration), `temperature` only when the configuration sets it (otherwise not sent), `seed` only where the profile honours it (recorded with `seed_guarantee: none`), `response_format` (`json_schema` with `strict: true`) only on profiles whose documentation verifies it, `stream: false`; `n` is not sent; `tools`, `logprobs`, `user`, `store`, `metadata` and `functions` are never sent; a profile sends no field it does not list. Response: `id`, `model`, exactly one `choices[0].message.content` (a string), `finish_reason`, `usage.prompt_tokens` and `usage.completion_tokens`, optional `prompt_tokens_details.cached_tokens` (absent means absent, never 0), optional `completion_tokens_details.reasoning_tokens` and `system_fingerprint` (to metadata); a reply of the wrong shape or over the size cap is refused; an OpenRouter choice-level `error` is a typed failure. Errors are decided on the HTTP status, and on `error.code` only when it is a short machine key, never on message text. The client is Tracer's own thin client on httpx (P4-J12): `JsonPoster` generalised, and the existing `SarvamLlm` unchanged. Rejected: the `openai` Python SDK (its retries, proxy-environment handling and redirect behaviour are not ours to audit, and it adds dependencies); generalising `SarvamLlm` in place (it would break ADR 0024 rule 3 and `test_sarvam_base_url_not_overridable`). Trade-off: we maintain the profile table and re-verify it on drift. Tests: `test_openai_compat_request_body_subset_exact`, `test_openai_compat_response_parse_rejects_wrong_shape_and_oversize`, `test_openai_compat_choice_level_error_is_typed_failure`, `test_profile_never_sends_unlisted_field`, `test_profile_temperature_not_sent_unless_configured`.
13. **Provider profiles and their verification status (Q-J2; FJ1 to FJ3, FJ9).** The judge speaks to a provider only through one profile of a closed, named and versioned set: `anthropic_compat/1`, `gemini_compat/1`, `xai/1`, `openrouter/1`, `sarvam_v2/1`, `openai/1`, `local_loopback/1` (the table in "Provider profiles"). A profile fixes what is sent, omitted or required, the token field, the auth scheme and the response fields read, and cites the dossier `docs/security/P4-J-providers.md` (P4-J03), one row per fact with URL and retrieval date or "not verified". A profile's status is `verified` or `unverified` as the dossier says; an `unverified` profile is refused for a real run until its first owner-gated request is logged. `anthropic_compat/1` never sends `response_format` or `seed`; `openrouter/1` always sends `model` and `provider: {order: [<one slug>], allow_fallbacks: false, require_parameters: true, data_collection: "deny"}`; every profile records the returned `model` verbatim as `reported_model`; `sarvam_v2/1` sends the `api-subscription-key` header. An unknown profile name is refused. Rejected: a free-form "generic" profile with a user-supplied base URL (it sends unknown fields to unknown hosts). Trade-off: a new provider needs a profile row, a dossier row and tests. Tests: `test_profile_anthropic_compat_never_sends_response_format_or_seed`, `test_profile_openrouter_sends_pinned_provider_without_fallback_and_data_collection_deny`, `test_profile_reported_model_recorded_verbatim`, `test_profile_sarvam_v2_uses_subscription_key_header`, `test_unknown_profile_refused`, `test_unverified_profile_refused_for_real_run`, `test_judge_profile_status_matches_dossier`.
14. **Pinned hosts and key binding (Q-J4; P4-J14; BL-142).** The owner writes the judge allowlist file (git-ignored, mode 0600, no links, guarded; an unknown or repeated field refuses the file). Each entry is an exact `https://host[:port]/path` with `profile=` and `key_env=`; `http` only for a literal loopback address; no userinfo, query or fragment. The judge configuration may name only an entry. At step 2 of rule 20, before any key is read: the entry must match exactly; the client is built with `follow_redirects=False` and `trust_env=False`, with TLS verified; every DNS answer is checked, and a private, loopback or link-local answer is refused for a public entry. The key is read by its variable name only after the entry matches and is bound to that host: the key named for one host is never sent to another. Residual risk: a DNS rebinding race between our resolution check and httpx's own connect is not claimed closed (BACKLOG row). Rejected: a base URL in the configuration with no allowlist (server-side request forgery and key exfiltration through one edit); keys in the configuration file. Trade-off: every judge endpoint is added by hand by the owner. Tests: `test_judge_host_not_on_allowlist_refused_before_any_key_read`, `test_judge_key_bound_to_its_host_never_sent_elsewhere`, `test_judge_https_only_except_loopback_literal`, `test_judge_client_never_follows_redirects_or_reads_proxy_env`, `test_judge_dns_answer_private_refused_for_public_entry`, `test_judge_allowlist_mode_and_link_rules`.
15. **BYOK by variable name; canaries (ADR 0007; ADR 0024 amendment A4).** The judge key is read at run time from the environment variable the allowlist entry names (or from `--env-file`, read for that name only), held in a redacting wrapper, and never written to any output: exception, repr, log at any level (DEBUG included), exchange file, store, report, recovery file, labels file or agreement report. The configuration names variables, never values; a `key_env` value that does not match the variable-name pattern, or a configuration that holds a key-shaped value, is refused. Rejected: keys in the judge configuration or the allowlist (both are files a user may share). Trade-off: one more variable per judge provider. Tests: `test_judge_key_env_name_pattern_and_never_value_in_config`, `test_judge_key_bound_to_its_host_never_sent_elsewhere`, `test_judge_key_canaries_never_in_any_output_channel`.
16. **Retries, timeouts and the error taxonomy (ADR 0024 rules 9 and 10).** The judge client uses the timeouts, the bounded attempts and the bounded `Retry-After` of ADR 0024 rule 9 and decides as rule 10 does: on the HTTP status and on a short machine-key `error.code`, never on message text, which can echo input. `Judge*` errors hold facts only, are raised outside handlers and carry no cause and no context (the `_engine_errors` pattern). An authentication failure stops every further judge request of the run, and the remaining items are `error` with its code. Rejected (architect): retrying an authentication failure (the key will not become valid between attempts, and each attempt is a request against a key that is refused). Trade-off: one bad key ends the judging of the run. Tests: `test_openai_compat_retry_taxonomy_matches_adr0024`, `test_judge_errors_hold_facts_only_and_no_chain`, `test_tdeg_judge_auth_failure_stops_requests_and_errors_remaining`.
17. **Caps (Q-J13).** A real judge engine requires `--max-judge-requests` (every attempt and every regeneration counts), `--max-judge-tokens` and `max_tokens` per request in the judge configuration, each with no default in code; a missing cap is refused at step 4 of rule 20. The token cap is checked as reported usage plus the next request's `max_tokens`, so the prompt tokens of one request can cross it (no tokenizer; BL-295, extended). A reply over 4,096 bytes is refused (rule 7). When a cap is reached no further request is sent and the remaining items are `error` with the cap's code. Rejected (architect): defaults in code (Tracer supplies no value; O-J3). Trade-off: the user states every limit for every real run. Tests: `test_judge_caps_required_for_real_engine_no_defaults`, `test_tdeg_judge_cap_reached_no_request_beyond_cap`, `test_judge_output_over_size_cap_refused`.
18. **Usage and cost (Q-J12; ADR 0014).** Each answered judge request makes one `UsageRecord` with `stage=judge` and `domain=evaluation`, the model as requested, `prompt_tokens` and `completion_tokens` mapped exactly, cached tokens only when the reply reports them (absent stays absent, never 0); usage is handed out before the next request and survives an interruption (rule 20). Cost is `not_available` without a user USD price catalog; OpenRouter's `usage.cost` is "Cost in credits" and is never stored as a cost; no currency is converted. Rejected (architect): computing a cost from a provider price page (pricing is not verified, BL-151); storing credits as money. Trade-off: judged runs show no cost until a USD catalog exists. Tests: `test_openai_compat_usage_maps_prompt_completion_cached_exactly`, `test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost`, `test_judge_engine_streams_usage_before_next_request`, `test_judge_usage_survives_ctrl_c_mid_judging`.
19. **Exchange files, replay and reproducibility (Q-J14).** Each attempt writes `<out>/judge/<run>/item-<id>-<attempt>.json` (`tracer.judge_exchange/1`: the request messages, the raw response body, the request id and the status), mode 0600, opened with `O_NOFOLLOW`, git-ignored and guarded; the store holds the exchange SHA-256 (rule 8), never a raw reply. The `replay:<dir>` engine answers from those files and reproduces the stored verdicts byte for byte; the `fake:<scenario>` engine is deterministic and sends nothing. Judged and rescoring bundles round-trip through the store byte for byte, on SQLite and on Postgres. No determinism of any provider is claimed: a seed is recorded as sent, with `seed_guarantee: none`. Rejected (architect): raw replies in the store (a second copy of model output that may hold injected material, as ADR 0028 rule 12). Trade-off: replay needs the local exchange directory, which is never committed. Tests: `test_judge_exchange_files_private_no_follow_and_guarded`, `test_replay_judge_reproduces_stored_verdicts_byte_identical`, `test_fake_judge_is_deterministic_and_sends_nothing`, `test_judged_bundle_round_trips_through_store_byte_identical`, `test_judged_and_rescoring_bundles_store_on_both_backends`, `test_e2e_judge_synthetic_mr_exact_outputs`.
20. **Order of operations and recovery (Q-J6; extends ADR 0026 rule 14).** (1) parse and validate the target, evaluator, sim profile, golden set (against the evaluator) and judge configuration; (2) the target allowlist and the judge allowlist (host, profile, key name); (3) the terms gates, including `--judge-authorisation-reference`, stored as its SHA-256 only; (4) every cap present, the judge caps included; (5) the output probe; (6) the engine factories read the keys (the judge key by its bound name) and the call logs open; (7) to (12) as ADR 0026 rule 14 (the call, the hang-up, the STT); (13) scoring; (13b) the judge: items in golden-set order, sequential, one request in flight, usage streamed to the recovery file, an authentication failure stopping further requests, the cap checked before each request; (14) the store; (15) the report. A `BaseException` during 13b writes `usage-recovery` and is raised again (Phase 3 rule 16 (d)). A judge failure gives exit 2 (`exit_code_of`). The dry run prints the judge plan and sends nothing. Rejected: judging only later, after the run is stored (the judge definitions must be in the run's snapshot from the start, FJ6). Trade-off: the judge model is fixed when `tracer simulate` runs; re-judging with another model needs a new run. Tests: `test_simulate_with_golden_order_of_operations_pinned`, `test_judge_refusals_before_any_key_read_or_connect`, `test_judge_usage_survives_ctrl_c_mid_judging`, `test_simulate_dry_run_prints_judge_plan_and_sends_nothing`, `test_simulate_judge_errors_exit_2`.
21. **`tracer judge`: rescoring and repeats on fixed answers (Q-J16; FJ6, FJ7; ADR 0022 rule 5).** `tracer judge --out <dir> --bundle <id> --judge <engine> [--repeats K]` re-judges a stored judged run and stores one rescoring bundle per repeat, sharing the call's records and adding judge results. It is refused when the run's snapshot holds no judge definitions, and when the engine's model differs from the definitions' `judge_model_ref`. K repeats re-judge the same stored answers K times, so their counts of verdict changes measure the judge, not the agent. Rejected: measuring stability by repeating whole calls (the agent varies too, FJ7); a later judge with another model on a stored run (the snapshot fixes the definitions, FJ6). Trade-off: comparing judge models needs one new run per model. Tests: `test_tracer_judge_rescoring_bundle_shares_records_and_adds_judge_results`, `test_tracer_judge_refuses_run_without_judge_definitions`, `test_tracer_judge_model_must_equal_definition_model_ref`, `test_tracer_judge_repeats_store_one_bundle_each`.
22. **Labels, the blind packet and agreement counts (Q-J17 to Q-J19).** Human labels are files of the closed, versioned format `tracer.judge_labels/1`, not a store table and not `MetricResult`s. `tracer labels packet` writes a blind packet per item (the question, the expected facts, the agent-channel audio range, our STT text and a JSON template with `verdict: null`) and never the judge's verdict. Each label records its `basis` (`audio`, `stt_text` or `both`). `tracer labels check` validates a labels file and copies it content-addressed (mode 0600) under `<out>/labels/`. `tracer labels agreement` reports contingency counts (judge by human, and human by human when two labellers exist) with unlabelled items shown separately and never counted as disagreement; no percent, no kappa, no confidence interval and no claim word in Phase 4 (kappa and intervals wait for a pre-registered minimum n, BL-269). The measurement report follows its template (P4-J29); human-agreement numbers appear only from the owner's labels, as counts. Rejected: an evaldb side table (a store migration for an unversioned document); labels as `MetricResult`s (`deterministic` or `llm_judge` would misstate who judged). Trade-off: labels live beside the store, and joining them to runs is by content address. Tests: `test_labels_file_strict_closed_keys_and_version`, `test_label_packet_never_shows_judge_verdict`, `test_labels_import_content_addressed_and_private`, `test_agreement_report_counts_only_never_rates_or_accuracy`, `test_agreement_report_unlabelled_never_counted_as_disagreement`, `test_agreement_report_human_human_counts_when_two_labellers`, `test_p4j_measurement_report_follows_template`, `test_e2e_judge_report_store_and_agreement_consistent`.
23. **Tier degradation (Q-J8; P4-J23).** Every case of one judged configuration gives the same result keys and the same schema; only statuses, values and reasons differ. Setup failed or STT failed: `not_applicable` `missing_input`, and no judge request. The question not sent, or an empty answer window: `error` with `question_not_sent` or `answer_window_empty`, never `disagrees`. A non-mr agent turn: every item `not_applicable` `unsupported_language` (rule 11). An authentication failure, a cap reached, or an invalid output twice: `error` with its code. `cannot_tell`: `error` `judge_undetermined`. Nothing missing is ever 0 or false. A run without a golden set is unchanged. Rejected (architect): `disagrees` for a missing or empty answer (an agent that was never asked, or whose answer we did not capture, did not disagree). Trade-off: a call that ends early shows errors, not verdicts, for the items it did not reach. Tests: `test_tdeg_judge_setup_failed_all_missing_input_no_request`, `test_tdeg_judge_stt_failed_missing_input_no_request`, `test_tdeg_judge_question_not_sent_is_error_never_disagrees`, `test_tdeg_judge_empty_answer_window_is_error_never_disagrees`, `test_tdeg_judge_non_mr_turn_unsupported_language_all_items`, `test_tdeg_judge_auth_failure_stops_requests_and_errors_remaining`, `test_tdeg_judge_cap_reached_no_request_beyond_cap`, `test_tdeg_judge_invalid_output_twice_is_error`, `test_tdeg_judge_cannot_tell_is_judge_undetermined_error`, `test_tdeg_judge_schema_identical_across_cases`, `test_tdeg_run_without_golden_set_unchanged`.
24. **Data sharing and terms gates (O-J1, O-J2; FJ3, FJ8; BL-188, BL-266).** A real judge run sends the question, the expected facts and the agent's STT answer to the provider the owner chose (O-J1). Before it: the owner's data-sharing decision is logged (O-J2; the reviewer recommends simulated runs only); security-licensing records each provider's terms and data-use page with URL and retrieval date in the dossier as an owner gate, never as an assumption; the run carries `--judge-authorisation-reference`, stored as a SHA-256 only. Real-caller data never goes to a judge without a new owner decision (BL-266). A Sarvam judge needs its own owner decision on terms 10.5(a), which forbids using Sarvam output to "test ... any artificial intelligence system" (FJ8; BL-188). The `openrouter/1` profile always sends `data_collection: "deny"` (rule 13). The audit P4-J26 is GO, with no open P0 or P1, before the first real judge request. Rejected (architect): reading the owner's 2026-10-23 permission for frontier-lab APIs as a record of each provider's terms (the rule allows sending; the terms still have to be recorded per provider). Trade-off: the first real judge request waits for the owner's answers and the audit. Tests: `test_judge_provider_dossier_cites_url_and_date_per_fact`, `test_judge_profile_status_matches_dossier`, `test_unverified_profile_refused_for_real_run`, `test_profile_openrouter_sends_pinned_provider_without_fallback_and_data_collection_deny`, `test_p4j_audit_items_cite_tests`.
25. **No network in tests; the fake engine and the loopback server (ADR 0024 rule 17; ADR 0026 rule 23).** Unit tests open no socket and use `httpx.MockTransport`, the `fake:<scenario>` engine or the `replay:<dir>` engine. A loopback fake OpenAI-compatible server (`tests/support`, stdlib `http.server`, scripted profile behaviours and injection replies) drives the real httpx transport in tests that carry the `loopback` marker only. Real judge calls are owner-gated steps, never tests. The guard refuses staged exchange files, labels and judge allowlists. The CLI README's offline quick start uses `--judge fake:` and runs in the suite; the sample bot's golden scenario uses no model. Rejected (architect): a recorded real reply as a test fixture (it would put model output in the repository). Trade-off: provider behaviour is proven only by the owner-gated runs P4-J28 and P4-J29. Tests: `test_unit_tests_open_no_socket`, `test_fake_judge_is_deterministic_and_sends_nothing`, `test_loopback_fake_openai_server_round_trip`, `test_loopback_fake_server_injection_replies_are_refused`, `test_guard_rejects_staged_judge_exchanges_labels_and_allowlist`, `test_judge_readme_quick_start_runs_offline`, `test_samplebot_golden_scenario_uses_no_model`.

## Provider profiles
The facts of the closed set of rule 13, as the staff-reviewer read them on 2026-10-28 (SCOPE section J.3 Q-J2 and J.5). "Verified" means read in the named primary source on that date; "not verified" means not read, and nothing is assumed in its place. The dossier P4-J03 re-states each fact with its URL and retrieval date and sets the profile's status; until then a profile the reviewer marked unverified stays `unverified`, and the status of the others is the dossier's to set.

| Profile | Endpoint | Auth | Token field | Temperature, seed, `response_format` | Other | Status |
|---|---|---|---|---|---|---|
| `anthropic_compat/1` | `https://api.anthropic.com/v1/chat/completions` (base URL verified, platform.claude.com/docs/en/api/openai-sdk, redirected from docs.claude.com) | Bearer (reviewer's table) | `max_tokens` (reviewer's table) | verified: `response_format` "Ignored"; tool `strict` ignored; `seed`, `logprobs`, `reasoning_effort` ignored; `temperature` below 1 returns 400 on Claude 4.7 and later, the same for `top_p`, and both together return 400; so `response_format` and `seed` are never sent and `temperature` is not sent by default | verified: `n` must be 1; `usage.prompt_tokens_details` "Always empty"; forced `tool_choice` errors on Claude Opus and Sonnet 5.5; "not considered a long-term or production-ready solution"; non-workspace keys need an `anthropic-workspace-id` header (optional header of the profile); prompt-only JSON | dossier to set |
| `gemini_compat/1` | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` (verified, ai.google.dev/gemini-api/docs/openai) | Bearer (verified) | not verified | not verified whether `temperature`, `max_tokens` or `seed` is honoured (not listed on the page); the page states that unlisted parameters are "silently ignored" (in its image section); `json_schema` shown only through the SDK `parse` example; the page says "still in beta" and has internal inconsistencies (flagged by the fetch summary) | usage fields not verified; the token cap may be ignored | `unverified` until the first owner-gated request |
| `xai/1` | `https://api.x.ai/v1/chat/completions` (base `https://api.x.ai/v1` verified, docs.x.ai structured-outputs guide) | Bearer (reviewer's table; not in the verified list) | not verified | `json_schema` shown in a Chat Completions example (grok-4.7), verified; `seed` not verified | usage and error shapes not verified; xAI calls the Responses API "the preferred way"; the guide's text "SpaceXSI models" is quoted as fetched | `unverified` until the first owner-gated request |
| `openrouter/1` | `https://openrouter.ai/api/v1/chat/completions` (verified, openrouter.ai/docs api-reference overview) | Bearer | `max_tokens` | verified: `seed` integer; `json_schema` with `strict`, and "The request will fail" if the model does not support it (features/structured-outputs) | verified (features/provider-routing, api-reference overview): `model` optional and "If omitted, the user's or payer's default is used"; `allow_fallbacks` defaults to true; load balancing on by default; `data_collection` defaults to `allow`; the response `model` may differ from the request; `usage.cost` is "Cost in credits". The profile always sends `model` and the pinned `provider` object (rule 13). Optional `HTTP-Referer` and `X-OpenRouter-Title` with the owner's values. Error codes not verified | dossier to set |
| `sarvam_v2/1` | `https://api.sarvam.ai/v2/chat/completions` (verified, docs.sarvam.ai/api-reference/chat/chat-completions-v2.md) | `api-subscription-key` header; the v2 page lists both auth headers as "required" and the repository sends only `api-subscription-key` (marked UNVERIFIED in the source); the requirement is not verified | `max_tokens` (verified: it includes reasoning tokens) | verified: `json_schema` "guarantees the model generates output matching the supplied JSON Schema"; `reasoning_effort` defaults to `max` on glm5.3 and deepseekv4.1-flash and counts against `max_tokens`, so it is sent explicitly | verified: 413 above 10,485,760 bytes; 502 `model_call_failed`; 503 `model_overloaded`. The v1 page (docs.sarvam.ai/api-reference-docs/chat/chat-completions) shows `POST /v1/chat/completions`, models `sarvam-105b` and `sarvam-105b-conversations`, and `seed` "Beta. Determinism is not guaranteed": the two pages disagree (FJ9). Terms 10.5(a) gate (FJ8) | dossier to set |
| `openai/1` | `https://api.openai.com/v1/chat/completions` (reviewer's table, per the openai-python SDK source) | Bearer (per the SDK source) | `max_completion_tokens` (`max_tokens` "deprecated in favor of `max_completion_tokens`", verified in the SDK source) | `seed` Beta, "Determinism is not guaranteed" (verified in the SDK source) | verified only through raw.githubusercontent.com/openai/openai-python at `main` (not a tag): `completion_create_params.py` and `completion_usage.py`, including the usage detail fields; platform.openai.com returned 403 | dossier to set |
| `local_loopback/1` | a literal loopback address only (the loopback fake server of rule 25, or the user's own local server) | none | the minimal subset | the minimal subset | the behaviour of a user's own local server is not verified | n/a for real providers |

Not verified by anyone on 2026-10-28: xAI Chat Completions usage, errors, `seed` and token field; Gemini usage fields and whether `temperature`, `max_tokens` and `seed` are honoured; OpenRouter error codes; every provider's terms, data retention and training use; every provider's Marathi competence; the Sarvam v2 auth header requirement; pricing and currencies; the DNS rebinding race in httpx.

## The five documents (P4-J05)
Draft 2020-12, closed keys, every integer bounded by 2**53 - 1, machine-checked against the constants the code ships and against the code's enumerations and caps (`test_adr0030_documents_match_json_schemas`). Every member names the rule it comes from; no member exists that a rule does not need. The schemas are given below, one section each (the member tables list every member, nested ones included, with the rule of the ADR it comes from).

## The `tracer.golden_set/1` format
The golden set file `tracer.golden_set/1`, read with `loads_strict` by `tracer_evalcore.golden.parse_golden_set`. The cross-field rules a schema cannot state (an item's `question` equals the scripted line, the caller is the scripted brain, no barge-in turn, NFC and the character table `mr-caller-charset/1`, unique item ids) are code (rule 3).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.golden_set/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "key",
    "language",
    "items"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.golden_set/1"
      ]
    },
    "key": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
      "minLength": 1,
      "maxLength": 64
    },
    "language": {
      "type": "string",
      "enum": [
        "mr"
      ]
    },
    "items": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {
        "oneOf": [
          {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "id",
              "scripted_turn_index",
              "question",
              "needs_native_review",
              "match_mode",
              "expected_facts"
            ],
            "properties": {
              "id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
                "minLength": 1,
                "maxLength": 40
              },
              "scripted_turn_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9007199254740991
              },
              "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2500
              },
              "needs_native_review": {
                "type": "boolean"
              },
              "match_mode": {
                "type": "string",
                "enum": [
                  "all_facts"
                ]
              },
              "expected_facts": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 300
                }
              }
            }
          },
          {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "id",
              "scripted_turn_index",
              "question",
              "needs_native_review",
              "match_mode",
              "expected_answer"
            ],
            "properties": {
              "id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
                "minLength": 1,
                "maxLength": 40
              },
              "scripted_turn_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9007199254740991
              },
              "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2500
              },
              "needs_native_review": {
                "type": "boolean"
              },
              "match_mode": {
                "type": "string",
                "enum": [
                  "equivalent_answer"
                ]
              },
              "expected_answer": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000
              }
            }
          }
        ]
      }
    }
  }
}
```

| Member | Source | Meaning |
|---|---|---|
| `format` | rule 2 | the version member; the value is the format name |
| `key` | rules 2, 8 | the set's name, recorded in `tracer.judge_profile/1` with the SHA-256 of the file |
| `language` | rules 2, 11 | mr only |
| `items` | rules 2, 3 | 1 to 100 items, each bound to one scripted turn |
| `items[].id` | rule 8 | snake case; the metric key is `judge_golden_<id>` |
| `items[].scripted_turn_index` | rule 3 | the scripted turn the item is bound to |
| `items[].question` | rules 3, 5 | equal to `Evaluator.scripted_turns[scripted_turn_index].text`, at most 2,500 characters |
| `items[].match_mode` | rules 2, 7 | `all_facts` or `equivalent_answer`; picks the branch of `oneOf` |
| `items[].expected_facts` | rules 2, 7 | mode `all_facts`: 1 to 10 facts of at most 300 characters |
| `items[].expected_answer` | rules 2, 7 | mode `equivalent_answer`: at most 1,000 characters |
| `items[].needs_native_review` | rule 2, P4-J04 | true for every item of the synthetic fixture |

## The `tracer.judge_profile/1` format
The profile document in `ConfigSnapshot.metadata["tracer.judge_profile"]`, built by `tracer_evalcore.judgerecords.build_judge_profile_document` (ADR 0027 rule 12a). `profile` is one of the seven profiles of rule 13 or `fake/1`, the profile of the offline fake engine (Ambiguity 7); `host`, `path` and `key_env` are null for it.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_profile/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "profile",
    "host",
    "path",
    "model",
    "params",
    "caps",
    "key_env",
    "golden_set",
    "template_sha256",
    "calibration"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_profile/1"
      ]
    },
    "profile": {
      "type": "string",
      "enum": [
        "anthropic_compat/1",
        "gemini_compat/1",
        "xai/1",
        "openrouter/1",
        "sarvam_v2/1",
        "openai/1",
        "local_loopback/1",
        "fake/1"
      ]
    },
    "host": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[a-z0-9.-]{1,253}(:[0-9]{1,5})?$|^\\[[0-9a-f:]{2,45}\\](:[0-9]{1,5})?$"
    },
    "path": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^/[A-Za-z0-9._~/-]{0,255}$"
    },
    "model": {
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$"
    },
    "params": {
      "type": "object",
      "additionalProperties": false,
      "required": [],
      "properties": {
        "max_tokens": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_completion_tokens": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "temperature": {
          "type": "number",
          "minimum": 0,
          "maximum": 2
        },
        "seed": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "seed_guarantee": {
          "type": "string",
          "enum": [
            "none"
          ]
        },
        "reasoning_effort": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]{0,31}$"
        },
        "response_format": {
          "type": "string",
          "enum": [
            "json_schema"
          ]
        },
        "provider": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "order",
            "allow_fallbacks",
            "require_parameters",
            "data_collection"
          ],
          "properties": {
            "order": {
              "type": "array",
              "minItems": 1,
              "maxItems": 1,
              "items": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9._/-]{0,63}$"
              }
            },
            "allow_fallbacks": {
              "type": "boolean",
              "enum": [
                false
              ]
            },
            "require_parameters": {
              "type": "boolean",
              "enum": [
                true
              ]
            },
            "data_collection": {
              "type": "string",
              "enum": [
                "deny"
              ]
            }
          }
        }
      }
    },
    "caps": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "max_judge_requests",
        "max_judge_tokens"
      ],
      "properties": {
        "max_judge_requests": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_judge_tokens": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        }
      }
    },
    "key_env": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[A-Z][A-Z0-9_]{0,63}$"
    },
    "golden_set": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "key",
        "sha256"
      ],
      "properties": {
        "key": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
          "minLength": 1,
          "maxLength": 64
        },
        "sha256": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        }
      }
    },
    "template_sha256": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "all_facts",
        "equivalent_answer"
      ],
      "properties": {
        "all_facts": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        },
        "equivalent_answer": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        }
      }
    },
    "calibration": {
      "type": "string",
      "enum": [
        "uncalibrated"
      ]
    }
  }
}
```

| Member | Source | Meaning |
|---|---|---|
| `format` | rule 8 | the version member |
| `profile` | rule 13 | the profile name and version |
| `host` | rule 14 | host and non-default port of the allowlist entry, or null |
| `path` | rules 8, 14 | the endpoint path (a stated exception to ADR 0027 rule 1: a URL path, not a file path), or null |
| `model` | rule 8 | the model asked for |
| `params` | rules 8, 12 | the call parameters as sent; `seed_guarantee` is always `none` |
| `params.max_tokens` | rule 12 | the token field of the profile, one of the two |
| `params.max_completion_tokens` | rule 12 | the token field of the profile `openai/1` |
| `params.temperature` | rule 12 | present only when the configuration sets it |
| `params.seed` | rule 12 | present only where the profile honours it |
| `params.seed_guarantee` | rule 19 | recorded with a seed: none is guaranteed |
| `params.reasoning_effort` | rule 13 | sent explicitly by `sarvam_v2/1` only |
| `params.response_format` | rule 12 | `json_schema` on the profiles whose documentation verifies it |
| `params.provider` | rule 13 | the pinned provider object of `openrouter/1` |
| `caps` | rule 17 | the caps of the run, null when none applies |
| `caps.max_judge_requests` | rule 17 | every attempt and regeneration counts |
| `caps.max_judge_tokens` | rule 17 | reported usage plus the next `max_tokens` |
| `key_env` | rules 14, 15 | the NAME of the key variable, never a value; null when no key is used |
| `golden_set` | rules 2, 8 | the golden set's key and the SHA-256 of its file |
| `template_sha256` | rules 6, 8 | the SHA-256 of the fixed template of each match mode |
| `calibration` | rule 9 | always `uncalibrated` |

## The `tracer.judge/1` format
The result document in `MetricResult.metadata["tracer.judge"]`, built by `tracer_evalcore.judgerecords.build_judge_document`. Cross-field rules (code): `verdict` is null if and only if `reason_code` is null and `facts` is empty; `regenerated` is true if and only if `attempts` is 2; with `attempts` 0 the verdict and `exchange_sha256` are null.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "verdict",
    "reason_code",
    "facts",
    "window_turn_seqs",
    "excluded_turns",
    "attempts",
    "regenerated",
    "exchange_sha256",
    "calibration",
    "confidence"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge/1"
      ]
    },
    "verdict": {
      "type": [
        "string",
        "null"
      ],
      "enum": [
        "agrees",
        "disagrees",
        "cannot_tell",
        null
      ]
    },
    "reason_code": {
      "type": [
        "string",
        "null"
      ],
      "enum": [
        "all_facts_stated",
        "fact_contradicted",
        "fact_absent",
        "meaning_differs",
        "meaning_same",
        "answer_off_topic",
        "answer_unintelligible",
        "insufficient_information",
        null
      ]
    },
    "facts": {
      "type": "array",
      "maxItems": 10,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "index",
          "status"
        ],
        "properties": {
          "index": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9
          },
          "status": {
            "type": "string",
            "enum": [
              "stated",
              "contradicted",
              "absent",
              "cannot_tell"
            ]
          }
        }
      }
    },
    "window_turn_seqs": {
      "type": "array",
      "items": {
        "type": "integer",
        "minimum": 0,
        "maximum": 9007199254740991
      }
    },
    "excluded_turns": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "attempts": {
      "type": "integer",
      "minimum": 0,
      "maximum": 2
    },
    "regenerated": {
      "type": "boolean"
    },
    "exchange_sha256": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[0-9a-f]{64}$"
    },
    "calibration": {
      "type": "string",
      "enum": [
        "uncalibrated"
      ]
    },
    "confidence": {
      "type": "string",
      "enum": [
        "none_claimed"
      ]
    }
  }
}
```

| Member | Source | Meaning |
|---|---|---|
| `format` | rule 8 | the version member |
| `verdict` | rules 7, 8 | `agrees`, `disagrees` or `cannot_tell`; null when the judge gave no usable verdict |
| `reason_code` | rule 7 | one of the eight codes; null with the verdict |
| `facts` | rule 7 | the status of each expected fact, empty in mode `equivalent_answer` |
| `facts[].index` | rule 7 | the position in `expected_facts`, from 0 |
| `facts[].status` | rule 7 | `stated`, `contradicted`, `absent` or `cannot_tell` |
| `window_turn_seqs` | rules 4, 8 | the `seq` of each `our_stt` turn of the answer window |
| `excluded_turns` | rules 4, 8 | the number of `our_stt` agent turns that start before the window opens |
| `attempts` | rules 7, 8 | requests made for the item: 0 when none was sent, 2 after a regeneration |
| `regenerated` | rules 7, 8 | whether the identical request was sent a second time |
| `exchange_sha256` | rules 8, 19 | the SHA-256 of the exchange file of the last attempt, or null |
| `calibration` | rule 9 | always `uncalibrated` |
| `confidence` | rule 9 | always `none_claimed`: no judge number is a confidence |

## The `tracer.judge_exchange/1` format
The local exchange file `<out>/judge/<run>/item-<id>-<attempt>.json`, written by `tracer_evalproviders.judgeexchange.ExchangeStore` as the RFC 8785 form of this document (mode 0600). The store holds only its SHA-256.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_exchange/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "messages",
    "response_body",
    "request_id",
    "status"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_exchange/1"
      ]
    },
    "messages": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "role",
          "content"
        ],
        "properties": {
          "role": {
            "type": "string",
            "enum": [
              "system",
              "user"
            ]
          },
          "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100000
          }
        }
      }
    },
    "response_body": {
      "type": "string",
      "maxLength": 65536
    },
    "request_id": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[A-Za-z0-9_.:-]{1,128}$"
    },
    "status": {
      "type": "integer",
      "minimum": 100,
      "maximum": 599
    }
  }
}
```

| Member | Source | Meaning |
|---|---|---|
| `format` | rule 19 | the version member |
| `messages` | rule 19 | the system and the user message that were sent |
| `messages[].role` | rule 5 | `system` or `user` |
| `messages[].content` | rule 5 | the text sent, at most 100,000 characters |
| `response_body` | rule 19 | the raw reply body of an answered request, at most 65,536 characters |
| `request_id` | rule 19 | the provider's request id when it has a plain form, else null |
| `status` | rule 19 | the HTTP status of the reply |

## The `tracer.judge_labels/1` format
The human labels file, one file per labeller, validated by `tracer_evalcore.judgelabels.validate_judge_labels` (the command `tracer labels check` is task P4-J21). A null `verdict` is an unlabelled item, shown separately and never counted as a disagreement.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_labels/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "bundle_id",
    "labeller",
    "labels"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_labels/1"
      ]
    },
    "bundle_id": {
      "type": "string",
      "pattern": "^rbnd_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "labeller": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9_-]{0,63}$"
    },
    "labels": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "item_id",
          "verdict",
          "basis"
        ],
        "properties": {
          "item_id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
            "minLength": 1,
            "maxLength": 40
          },
          "verdict": {
            "type": [
              "string",
              "null"
            ],
            "enum": [
              "agrees",
              "disagrees",
              "cannot_tell",
              null
            ]
          },
          "basis": {
            "type": "string",
            "enum": [
              "audio",
              "stt_text",
              "both"
            ]
          }
        }
      }
    }
  }
}
```

| Member | Source | Meaning |
|---|---|---|
| `format` | rule 22 | the version member |
| `bundle_id` | rule 22 | the judged run bundle the labels refer to |
| `labeller` | rule 22 | a pseudonym; two labellers give the human by human counts |
| `labels` | rule 22 | one label per item |
| `labels[].item_id` | rules 3, 22 | the golden item |
| `labels[].verdict` | rule 22 | the human's verdict, null when not labelled |
| `labels[].basis` | rule 22 | `audio`, `stt_text` or `both`: what the human judged on |

## Amendments to earlier ADRs (applied, task P4-J02)
Applied in the same change as the move of this ADR (task P4-J02), together with the named spec changes listed at the end of this section and ADR index revision 52. The text below is the text now in ADRs 0023, 0024 and 0027.

### ADR 0023, Phase 4 amendment, new rule A4 (after A3, before the amendment's invariants)
```text
A4. **The claim words for judge outputs (ADR 0030 rule 9; Phase 4 batch J, P4-J02).** Tracer-authored text of the judge lines of the `tracer simulate` and `tracer judge` output, of the judge section of the report ("uncalibrated judge signal"), of the agreement report of `tracer labels agreement` and of the concept page `docs/concepts/llm-judge.md` contains none of the words of rule 1 and A1 and none of these, matched case-insensitively as whole words or phrases: `correct`, `incorrect`, `correctness`, `factual`, `factuality`, `truthful`, `hallucination`, `ground truth`, `calibrated` (whole word only, so that "uncalibrated" stays allowed and required), `probability`, `confidence`, `pass`, `fail`, `passed`, `failed`, `score of the agent`. The concept page contains, in the fenced block of its section "Not claimed", exactly one sentence that names them, verbatim: "Tracer's judge is an uncalibrated language-model signal: it reports whether a model said that an agent's answer agrees with the expected facts; it does not measure accuracy, correctness, factuality or quality, it is not a pass or fail of the agent, and none of its numbers is a confidence, a probability, an error rate, a recall or a precision." and no deny-listed word outside that sentence. The deny-list applies to the text Tracer writes, not to record keys and values (for example `confidence: "none_claimed"` in `tracer.judge/1`), and not to transcript text, golden-set text or label text, which is shown escaped (ADR 0017). Rejected: reusing rule 1 and A1 alone (they do not cover correctness, factuality, hallucination, confidence or pass and fail, which a judge invites); a free-text disclaimer without a check. Trade-off: the word list can miss a paraphrase; the review gate P4-G is the rest. Tests: `test_judge_report_never_claims_accuracy_or_quality`, `test_agreement_report_counts_only_never_rates_or_accuracy`, `test_judge_concept_page_states_no_claims`.
```
The intro paragraph of the amendment then says "labelled A1 to A4" (and names task P4-J02 beside P4-00d). No new invariant: IA1 and IA2 keep their text.

### ADR 0024, Phase 4 amendment, new rules A10 to A13 (after A9, before the amendment's invariants)
```text
A10. **The generic chat-completions client (ADR 0030 rules 12 and 16; P4-J12).** `JsonPoster` is generalised for the judge: the auth scheme is a bearer token or one named header, the URL is the exact URL of one validated judge allowlist entry (ADR 0030 rule 14), and the usage stage is `judge`. Rule 3's fixed endpoint keeps holding for every Sarvam adapter, and the existing `SarvamLlm` is unchanged; the judge client's endpoint is fixed for a run by the owner's allowlist entry and never by an argument, an environment variable or the judge configuration. Timeouts, attempts, `Retry-After` and the error taxonomy are those of rules 9 and 10. Rejected: the `openai` Python SDK (retries, proxy-environment handling and redirect behaviour not ours to audit; more dependencies); a URL override on the Sarvam adapters. Trade-off: one more client shape to audit. Tests: `test_openai_compat_request_body_subset_exact`, `test_openai_compat_retry_taxonomy_matches_adr0024`, `test_sarvam_base_url_not_overridable`.
A11. **Provider profiles (ADR 0030 rule 13; P4-J13).** The judge client sends only what one profile of the closed, versioned set of ADR 0030 lists, and refuses an unknown profile; each profile cites the dossier `docs/security/P4-J-providers.md` fact by fact, and an `unverified` profile is refused for a real run until its first owner-gated request is logged. Rejected: a generic profile with a user base URL. Trade-off: a new provider needs a profile, a dossier row and tests. Tests: `test_unknown_profile_refused`, `test_profile_never_sends_unlisted_field`, `test_unverified_profile_refused_for_real_run`.
A12. **The judge allowlist and key binding (ADR 0030 rules 14 and 15; P4-J14).** The judge key is read by the variable name of the matched allowlist entry, only after the entry matches, and is sent only to that entry's host; `follow_redirects=False`, `trust_env=False`, TLS verified and every DNS answer checked as rule 3 and ADR 0026 rule 16 require; the key appears in no output channel (rule 5, A4). Rejected: keys or base URLs in the judge configuration. Trade-off: the owner adds each endpoint by hand. Tests: `test_judge_host_not_on_allowlist_refused_before_any_key_read`, `test_judge_key_bound_to_its_host_never_sent_elsewhere`, `test_judge_key_canaries_never_in_any_output_channel`.
A13. **Judge usage (ADR 0030 rule 18; ADR 0014).** Each answered judge request makes one UsageRecord with `stage=judge`, `domain=evaluation`, the model as requested and the prompt and completion tokens mapped exactly; cached tokens only when reported; cost `not_available` without a user USD catalog; OpenRouter credits are never stored as a cost; usage is handed out before the next request (rule 16 (d)). Rejected: credits as money; a cost from an unverified price page. Trade-off: no cost is shown for judged runs until a USD catalog exists. Tests: `test_openai_compat_usage_maps_prompt_completion_cached_exactly`, `test_judge_engine_streams_usage_before_next_request`.
```
The intro paragraph then says "labelled A1 to A13" (and names task P4-J02). No new invariant: IA1 to IA4 keep their text. Rules 1 to 17 and invariants I1 to I9 keep their numbers.

### ADR 0027, new rule 12a (one line directly after rule 12)
```text
12a. **The snapshot may embed judge definitions; the check stays inert (ADR 0030 rules 8 and 10; Phase 4 batch J, P4-J02).** A run of `tracer simulate --golden` embeds, beside the inert check of rule 12, one optional `llm_judge` definition and binding per golden item (ADR 0030 rule 8) and the document `tracer.judge_profile/1` in `ConfigSnapshot.metadata["tracer.judge_profile"]`; the endpoint path recorded there is a stated exception to rule 1's "no path" (it is a URL path, not a file path). The inert check stays the only required rubric item, `outcome` stays `not_evaluated`, and no RunVerdict is produced. A run without `--golden` embeds none of it. Rejected: making the check real (a pass or fail of the agent from an uncalibrated model; ADR 0030 rule 10). Trade-off: the snapshot of a judged run is larger by one definition per item. Tests: `test_expected_outcome_check_stays_inert_with_judge`, `test_judged_bundle_passes_check_run_bundle`.
```
The label `12a.` is deliberately not of the form `N. **`, so the rule count of ADR 0027 (17 rules, I1 to I10) is unchanged and rule 12a is read as part of rule 12's body by the rule reader.

### Tests that need a named spec change when the amendments and this ADR are applied
qa made each change (before and after, disclosed in `docs/phase4/evidence/red/batch-j1/spec-change.diff`).
- `packages/evalcore/tests/evalcore_phase4_adr_support.py`: `SPECS["0023A"]` from 3 rules to 4 and `SPECS["0024A"]` from 9 rules to 13 (invariant counts unchanged); `PENDING["0023A"]`, `PENDING["0024A"]` and `PENDING["0027"]` gain the batch J names the new text cites that no test defines yet; a new `SPECS["0030"]` (25 rules, 9 invariants, self test `test_adr0030_rules_are_tested`) with `PENDING["0030"]`; the message suffix "(an ADR from 0030 has no pending set)" of `whole_adr_problems` changes meaning; the module docstring.
- The ADR 0023 amendment module of `packages/evalcore/tests` (the module of `test_adr0023_phase4_amendment_is_tested`): `test_adr0023_phase4_amendment_has_the_pinned_labels` (A1 to A4) and `test_adr0023_phase4_amendment_says_where_it_is_machine_read` ("labelled A1 to A3" becomes "labelled A1 to A4"; "task P4-00d" stays).
- The ADR 0024 amendment module of `packages/evalcore/tests` (the module of `test_adr0024_phase4_amendment_is_tested`): `test_adr0024_phase4_amendment_has_the_pinned_labels` (A1 to A13) and `test_adr0024_phase4_amendment_says_where_it_is_machine_read` ("labelled A1 to A9" becomes "labelled A1 to A13").
- The Phase 4 ADR index and gate module of `packages/evalcore/tests` (the module of `test_phase4_adr_pending_sets_are_empty_at_the_gate`): `test_the_index_header_check_has_teeth` (its mutation replaces "Revision 5" by "Revision 49"; with the header at Revision 52 the mutant reads "Revision 492", which the header check accepts, so the test fails at any revision from 52 to 59); `STATUS` and the parametrised lists gain "0030"; the module docstring.
- The Phase 4 whole-ADR reader module of `packages/evalcore/tests` (the module of `test_no_adr_from_0026_cites_a_test_that_neither_exists_nor_is_pending`): `test_a_later_adr_is_read_too_and_has_no_pending_set` (its premise "an ADR from 0030 has no pending set" ends once `SPECS["0030"]` exists; the check it makes still holds for an invented name) and `test_the_reader_covers_every_adr_from_0026_and_the_three_amendments` (gains "ADR 0030").
- Unchanged by the amendments: `test_adr0023_rules_are_tested` and `test_adr0024_rules_are_tested` (they read only the Decision blocks, rules 1 to 19 and 1 to 17), `test_adr0027_has_17_rules_and_10_invariants_with_the_pinned_labels` (rule 12a is not a numbered label), and the revision 50 regexes of `test_the_index_announces_the_three_amendments_with_their_labels` (the revision 50 text is kept).

## Ambiguities resolved
For the lead (SCOPE section J covers Q-J1 to Q-J20; these are readings the scope leaves open).
- **Ambiguity 1. The replay brain.** The scope refuses golden sets with the LLM caller and requires the scripted caller (FJ10), and names no rule for the replay brain (`--caller fake:<dir>`), whose lines are files and not `Evaluator.scripted_turns`. **Recommendation:** refuse golden sets with the replay brain as with the LLM caller (rule 3 compares with `scripted_turns`, which the replay brain does not read). Rejected: allowing replay when the files happen to equal the script (a check on file content would be a second binding rule).
- **Ambiguity 2. The cross-field rules of the output.** The scope states: `agrees` iff every fact is `stated`; `disagrees` iff any fact is `contradicted` or `absent`; `cannot_tell` iff any fact is `cannot_tell` and none is `contradicted`. A reply with one fact `absent` and another `cannot_tell` satisfies both the `disagrees` and the `cannot_tell` condition, so the three are not a partition. **Recommendation:** `cannot_tell` iff no fact is `contradicted` or `absent` and at least one is `cannot_tell` (rule 7), which makes the three conditions a partition. Rejected: a precedence order applied after the fact (the validator would accept two verdicts for one fact list).
- **Ambiguity 3. Reason codes against verdicts.** The scope gives the closed set of reason codes and no table of which code may go with which verdict (for example `fact_absent` with `agrees`). **Recommendation:** P4-J09 adds a code-to-verdict table to the validator, reviewed by indic-linguistics with the wording (P4-J04), as a cross-field rule. Rejected: free pairing (a reason that contradicts the verdict would be stored).
- **Ambiguity 4. The `equivalent_answer` mode.** With an empty `facts` list the fact rules of rule 7 do not constrain the verdict. **Recommendation:** in that mode the verdict is constrained by the code-to-verdict table of Ambiguity 3 only (`meaning_same` with `agrees`, `meaning_differs` or `answer_off_topic` with `disagrees`, `insufficient_information` or `answer_unintelligible` with `cannot_tell`), to be confirmed by P4-J09.
- **Ambiguity 5. A self-test invariant.** The scope gives invariants I1 to I8, mirroring rules 5, 7, 9, 10, 14, 19, 22 and 23; the machine-read format of ADRs 0026 to 0029 requires that the last invariant cites the ADR's own test. **Recommendation:** I9 cites `test_adr0030_rules_are_tested`, as I7 of ADR 0028 and I4 of ADR 0029 do.
- **Ambiguity 6. Cost names no test.** Rule 18's "cost `not_available` without a USD catalog" and "credits never stored as cost" are gate item 20 but no named test of section A.10 states them; the usage tests cover the mapping only. **Recommendation:** qa adds a named test to P4-J12 or P4-J17 (a SCOPE row change logged by the lead), and rule 18 cites it.

- **Ambiguity 7. The profile of the offline engines.** The scope fixes seven profiles for real providers; a judged run with `fake:<scenario>` still needs a valid `tracer.judge_profile/1`. **Recommendation:** the document's `profile` may also be `fake/1` (host, path and `key_env` null); `fake/1` is not a profile of rule 13 and is refused as a real profile (`unknown_profile`). Rejected: no profile document for a fake run (the snapshot would differ in shape between a fake and a real run).

## Consequences
- A judged run costs one judge request per golden item plus at most one regeneration each; every number is capped by the user.
- No judge number is a claim: counts with denominators only; human agreement only from the owner's labels.
- The CLI and the report cannot use the words "pass", "fail", "passed" or "failed" in judge lines (A4); an error is called an error.
- A single non-mr agent turn removes every judge result of the run until schema 0.0.2 (rule 11, BL-193).
- Re-judging with another model needs a new run (FJ6); repeats with the same model re-judge stored answers (rule 21).
- A profile is a contract that can drift: the dossier is re-read when a provider changes its documentation, and the profile gets a new version.

## Invariants (testable)
- I1. A judge request holds only the item's question, its expected facts or answer, and the window text; never a key, the instructions, profile values or another item. Tests: `test_judge_request_holds_only_question_expected_and_answer`, `test_judge_request_never_holds_key_instructions_profile_or_other_items`.
- I2. A judge reply is accepted only as closed, consistent JSON; it is never repaired; one regeneration, then an error. Tests: `test_judge_output_never_repaired_one_regeneration_then_error`, `test_judge_output_cross_field_consistency_enforced`.
- I3. Every judge output is labelled uncalibrated and carries no confidence and no probability. Tests: `test_judge_lines_labelled_uncalibrated_everywhere`, `test_judge_result_carries_judgeinfo_without_confidence_or_p_yes`.
- I4. The inert expected-outcome check is in every snapshot, judged ones included, and no RunVerdict exists. Tests: `test_expected_outcome_check_stays_inert_with_judge`.
- I5. No judge request reaches a host outside the owner's allowlist, and a key is sent only to its own host. Tests: `test_judge_host_not_on_allowlist_refused_before_any_key_read`, `test_judge_key_bound_to_its_host_never_sent_elsewhere`.
- I6. Replay reproduces the stored verdicts byte for byte. Tests: `test_replay_judge_reproduces_stored_verdicts_byte_identical`.
- I7. A label packet never shows the judge's verdict; the agreement report gives counts only. Tests: `test_label_packet_never_shows_judge_verdict`, `test_agreement_report_counts_only_never_rates_or_accuracy`.
- I8. Every degradation case gives the same key set and schema, and a missing question or answer is never `disagrees`. Tests: `test_tdeg_judge_schema_identical_across_cases`, `test_tdeg_judge_question_not_sent_is_error_never_disagrees`.
- I9. Every numbered rule and every invariant cites a test. Test: `test_adr0030_rules_are_tested`.

## Index entry
Written into `docs/adr/README.md` as revision 52 (P4-J01, P4-J02). The mutation of `test_the_index_header_check_has_teeth` replaced "Revision 5" by "Revision 49", which reads "Revision 492" at revision 52 and was accepted by the header check; the test now replaces the number of the header line by 49 (disclosed in the spec change).
- Row: `| [0030](0030-llm-judge-uncalibrated-signal.md) | LLM judge as an uncalibrated signal (golden sets, OpenAI-compatible provider profiles, human labels) | Accepted | none (P4-J01, rev 52: Q-J1 to Q-J20 and Ambiguities 1 to 7 auto-approved by the lead; the owner gates O-J1 to O-J6 are before the first real judge request) |`
- Changes entry (as written in revision 52): "**New ADR 0030** (P4-J01): the LLM judge as an uncalibrated signal. 25 numbered rules and invariants I1 to I9 (`test_adr0030_rules_are_tested`) ... **ADR 0023 amended** (P4-J02): rule A4, the judge claim words and the fixed sentence of `docs/concepts/llm-judge.md` ... **ADR 0024 amended** (P4-J02): rules A10 to A13 ... **ADR 0027 rule 12a added** (P4-J02) ...", each with the named spec changes listed above.
