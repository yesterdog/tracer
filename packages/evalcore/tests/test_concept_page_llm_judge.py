# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J30: the concept page ``docs/concepts/llm-judge.md`` (ADR 0030 rule 9, ADR 0023 amendment A4).

The page must say what the judge reports and, as plainly, what Tracer does not claim. It also holds
the labeller guide and the reading of the judge options (golden sets, the answer window, provider
profiles, the owner allowlist, caps, replay, K repeats, exit codes). This module reads the page and
checks that it cannot drift from the ADRs and the code:

* the one fixed sentence of ADR 0023 amendment A4 (and ADR 0030 rule 9) occurs exactly once, alone
  in the one fenced block of the section "Not claimed", and is verbatim the sentence of both ADRs;
* none of the claim words (ADR 0023 rule 1, A1 and A4 together, whole words; "uncalibrated" stays
  allowed) appears anywhere else on the page;
* the page has the fixed headings and makes each required statement (``REQUIREMENTS``; each fits
  one sentence, bullet or table row of its section), among them the open points as they stand in
  the BACKLOG: the denominator of ``labels check`` (BL-350) and the authorisation reference that is
  printed as a SHA-256 and not stored (BL-363); a profile that is "not verified" stays so;
* it holds no private path, no secret-shaped string and no language code outside the nine
  supported ones; every BL id it cites exists, every test it names exists, every relative link
  resolves, and the concept index links the page.

The checker has teeth: each rule has a mutation of the real page that must be refused.
"""

# ruff: noqa: E501

from __future__ import annotations

import functools
import re

import pytest

from evalcore_batchh_support import (  # type: ignore[import-not-found,unused-ignore]
    CONCEPTS,
    ROOT,
    Requirement,
    backlog_ids,
    defined_tests,
    h2_headings,
    sections,
    unmet,
)
from evalcore_p4docs_support import (  # type: ignore[import-not-found,unused-ignore]
    JUDGE_SENTENCE,
    PageSpec,
    drop_requirement,
    index_problems,
    page_problems,
    replace_sentence,
)

PAGE = CONCEPTS / "llm-judge.md"
INDEX = CONCEPTS / "README.md"
TITLE = "# LLM judge"
HEADINGS = (
    "What the judge reports",
    "Not claimed",
    "Golden sets",
    "The answer window",
    "Provider profiles",
    "Owner allowlist, key and caps",
    "Replay and repeats",
    "Labeller guide",
    "Reading the output",
    "Exit codes",
    "Offline quick start",
    "Known limits",
    "Related",
)
W, N, G, A, PR, OA, RR, L, RD, E, Q, K, REL = HEADINGS

#: the seven profile names of ADR 0030 rule 13
PROFILES = (
    "anthropic_compat/1",
    "gemini_compat/1",
    "xai/1",
    "openrouter/1",
    "sarvam_v2/1",
    "openai/1",
    "local_loopback/1",
)


def req(key: str, section: str, *alternatives: tuple[str, ...]) -> Requirement:
    return Requirement(key, section, alternatives)


REQUIREMENTS: tuple[Requirement, ...] = (
    req("uncalibrated_signal", W, (r"\buncalibrated\b", r"\bsignal\b")),
    req("verdicts_are_closed", W, (r"\bagrees\b", r"\bdisagrees\b", r"\bcannot_tell\b")),
    req("reason_code_and_fact_statuses", W, (r"reason code", r"fact status|statuses")),
    req("counts_with_denominators", W, (r"\bn of N\b|\bcounts?\b", r"denominators?")),
    req("no_number_is_a_measure", W, (r"\bno\b|\bnone\b|\bnot\b", r"\bnumber\b", r"signal")),
    req("simulated_scripted_calls_only", W, (r"\bsimulated\b", r"\bscripted\b", r"\bonly\b")),
    req("mr_only", W, (r"\bmr\b", r"\bonly\b")),
    req(
        "judge_sees_three_things",
        W,
        (r"\bquestion\b", r"expected", r"answer", r"\bnothing else\b|\bonly\b"),
    ),
    req("answer_is_untrusted_data", W, (r"\buntrusted\b|\bdata\b", r"instruction|fence|encoded")),
    req("reply_never_repaired", W, (r"\bnever repaired\b|\bnot repaired\b", r"regenerat")),
    req("outcome_not_evaluated", W, (r"outcome not evaluated", r"inert|no verdict|not produced")),
    req("fixed_sentence_is_the_only_list", N, (r"\bsentence\b", r"\bonce\b|\bonly\b|\bone\b")),
    req("not_a_verdict_of_the_agent", N, (r"\bnot\b", r"\bagent\b", r"verdict|judgement|judgment")),
    req("fake_judge_reads_nothing", N, (r"fake:", r"\bnothing\b|reads no|does not read")),
    req("golden_format", G, (r"tracer\.golden_set/1",)),
    req("golden_strict_json", G, (r"strict JSON", r"closed keys|duplicate")),
    req(
        "golden_caps",
        G,
        (r"\b100 items\b", r"\b10 (?:expected )?facts\b", r"\b300 characters\b", r"\b1,000\b"),
    ),
    req("golden_match_modes", G, (r"all_facts", r"equivalent_answer")),
    req(
        "golden_question_equals_script",
        G,
        (r"\bquestion\b", r"byte for byte|exactly equal|must equal", r"scripted"),
    ),
    req("golden_refused_with_llm_caller", G, (r"\brefused\b", r"LLM caller|--caller")),
    req("golden_refused_on_barge_in", G, (r"barge-in", r"\brefused\b")),
    req(
        "golden_refusals_before_any_key",
        G,
        (r"step 1|before any key", r"\brefused\b|\brefusals?\b"),
    ),
    req("synthetic_golden_fixture", G, (r"golden_mr\.json", r"apparatus|synthetic")),
    req("golden_not_natively_reviewed", G, (r"\bnative\b", r"\breview\b", r"BL-169")),
    req("window_opens_at_end_of_question", A, (r"\bopens\b", r"end", r"caller")),
    req("window_closes_at_next_caller_line", A, (r"\bcloses\b", r"next|call end|end of the call")),
    req(
        "window_reads_our_stt_agent_turns",
        A,
        (
            r"our_stt|our speech-to-text",
            r"agent",
        ),
    ),
    req("window_never_export_or_script", A, (r"\bnever\b", r"export", r"script")),
    req("window_excluded_turns_counted", A, (r"excluded", r"counted|count")),
    req(
        "empty_window_is_an_error",
        A,
        (r"answer_window_empty", r"\berror\b", r"never disagrees|not disagrees|never a verdict"),
    ),
    req("seven_profiles", PR, tuple(re.escape(p) for p in PROFILES)),
    req("profiles_are_unverified", PR, (r"\bunverified\b", r"dossier|P4-J-providers")),
    req("not_verified_stays", PR, (r"not verified",)),
    req("unverified_profile_refused", PR, (r"unverified", r"\brefused\b", r"real run", r"first")),
    req("first_request_gate_flag", PR, (r"--judge-first-request-gate",)),
    req("terms_not_fetched", PR, (r"\bterms\b", r"not fetched|owner gate")),
    req(
        "anthropic_no_response_format_or_seed",
        PR,
        (r"anthropic_compat/1", r"response_format", r"seed"),
    ),
    req("openrouter_pins_provider", PR, (r"openrouter/1", r"fallback", r"data_collection")),
    req("sarvam_judge_is_owner_decision", PR, (r"sarvam", r"10\.5\(a\)|BL-188", r"owner")),
    req("loopback_profile_literal_only", PR, (r"local_loopback/1", r"loopback")),
    req(
        "fake_and_replay_send_nothing",
        PR,
        (r"fake:", r"replay:", r"send nothing|sends nothing|nothing is sent"),
    ),
    req("allowlist_file_rules", OA, (r"allowlist", r"0600", r"git")),
    req("allowlist_entry_shape", OA, (r"https://host", r"profile=", r"key_env=")),
    req("http_only_loopback", OA, (r"\bhttp\b", r"loopback")),
    req("no_redirects_no_proxy_env", OA, (r"redirect", r"proxy")),
    req("private_dns_answer_refused", OA, (r"\bDNS\b", r"private|loopback|link-local", r"refused")),
    req("key_read_by_name_after_match", OA, (r"variable name|key_env", r"after", r"match")),
    req("key_never_in_output", OA, (r"\bkey\b", r"never", r"output|log|store|report")),
    req(
        "caps_required_no_default",
        OA,
        (r"--max-judge-requests", r"--max-judge-tokens", r"no default"),
    ),
    req("max_tokens_in_config", OA, (r"max_tokens", r"configuration|config")),
    req("regeneration_counts_against_cap", OA, (r"regenerat", r"cap|request")),
    req("token_cap_can_be_crossed", OA, (r"prompt tokens", r"BL-295")),
    req(
        "authorisation_reference_required",
        OA,
        (r"--judge-authorisation-reference", r"real provider|real run"),
    ),
    req(
        "authorisation_reference_hash_printed_not_stored",
        OA,
        (r"sha256", r"printed", r"not stored|never stored", r"BL-363"),
    ),
    req(
        "authorisation_reference_text_never_written",
        OA,
        (r"\btext\b", r"never", r"written|stored|printed"),
    ),
    req("dns_rebinding_residual", OA, (r"rebinding", r"BL-326")),
    req("exchange_files_local_only", RR, (r"item-", r"0600", r"git")),
    req("replay_reproduces_verdicts", RR, (r"replay:", r"byte")),
    req("fake_is_deterministic", RR, (r"fake:", r"deterministic|same request")),
    req("tracer_judge_command", RR, (r"tracer judge", r"--golden", r"--judge-config")),
    req("repeats_range", RR, (r"--repeats", r"\b1 to 100\b|\b100\b")),
    req("one_rescoring_bundle_per_repeat", RR, (r"rescoring bundle", r"repeat")),
    req("refused_without_judge_definitions", RR, (r"\brefused\b", r"judge definitions|no judge")),
    req("model_must_match", RR, (r"\bmodel\b", r"judge_model_ref|differs|must equal|must match")),
    req("changed_count_measures_the_judge", RR, (r"changed", r"judge", r"not the agent")),
    req(
        "no_provider_repeatability_claimed",
        RR,
        (r"\bseed\b", r"no guarantee", r"claimed|recorded as sent"),
    ),
    req("blind_packet", L, (r"blind packet",)),
    req("packet_contents", L, (r"question", r"expected facts", r"audio range", r"speech-to-text")),
    req(
        "packet_never_shows_judge_verdict",
        L,
        (r"packet", r"never", r"judge's verdict|verdict of the judge"),
    ),
    req("labels_file_format", L, (r"tracer\.judge_labels/1",)),
    req("label_verdicts_and_basis", L, (r"\bbasis\b", r"audio", r"stt_text")),
    req("null_verdict_is_unlabelled", L, (r"\bnull\b", r"unlabelled")),
    req(
        "labels_check_copies_content_addressed", L, (r"labels check", r"content-addressed|SHA-256")
    ),
    req(
        "labels_check_denominator",
        L,
        (r"labelled n of N|labelled: n of N", r"labels the file lists|file lists", r"BL-350"),
    ),
    req("agreement_counts_only", L, (r"labels agreement", r"counts only|only counts")),
    req(
        "unlabelled_never_disagreement",
        L,
        (r"unlabelled", r"never counted|not counted", r"disagree"),
    ),
    req("no_rate_in_agreement", L, (r"\bno\b", r"percent", r"kappa")),
    req("two_labellers_at_most", L, (r"two labellers|at most two",)),
    req("labels_stay_out_of_git", L, (r"labels", r"packets?", r"\bgit\b")),
    req(
        "lines_are_labelled_uncalibrated",
        RD,
        (r"\buncalibrated\b", r"every judge line|each judge line|judge line"),
    ),
    req("report_section_title", RD, (r"uncalibrated judge signal", r"report")),
    req("text_is_escaped", RD, (r"\bescaped\b",)),
    req(
        "expected_facts_never_printed",
        RD,
        (r"expected facts", r"never printed|not printed|never put"),
    ),
    req("cost_not_available", RD, (r"\bnot_available\b|not available", r"USD")),
    req("credits_never_cost", RD, (r"credits", r"never", r"cost")),
    req("exit_code_table", E, (r"\|\s*Code\s*\|",)),
    req("exit_2_is_a_result_not_scored", E, (r"\b2\b", r"\berror\b", r"cannot_tell")),
    req("disagrees_is_exit_0", E, (r"\bdisagrees\b", r"\b0\b")),
    req("exit_4_refusal_before_key", E, (r"\b4\b", r"refus", r"before")),
    req("quick_start_pointer", Q, (r"packages/cli/README\.md", r"--judge fake:")),
    req("quick_start_is_plumbing", Q, (r"\bplumbing\b", r"fake")),
    req("native_review_missing", K, (r"\bnative\b", r"\breview\b", r"BL-169")),
    req(
        "non_mr_turn_makes_all_items_not_applicable",
        K,
        (r"not_applicable|not applicable", r"BL-193"),
    ),
    req("no_calibration_measured", K, (r"\bno\b|\bnot\b", r"measured|measurement", r"human")),
    req("owner_gates_open", K, (r"O-J1", r"owner")),
    req("real_data_needs_new_decision", K, (r"real-caller|real caller", r"BL-266")),
    req("judge_only_on_simulated_runs", K, (r"BL-174|BL-269", r"recorded|real")),
)

SPEC = PageSpec(
    path=PAGE,
    title=TITLE,
    headings=HEADINGS,
    sentence=JUDGE_SENTENCE,
    requirements=REQUIREMENTS,
    required_links=(
        "../adr/0023-recorded-call-agreement-scoring.md",
        "../adr/0024-provider-adapters-byok-sarvam-rest.md",
        "../adr/0030-llm-judge-uncalibrated-signal.md",
        "../security/P4-J-providers.md",
        "../../packages/cli/README.md",
        "../examples/simulated-mr/README.md",
        "simulated-calls.md",
    ),
    required_bl=frozenset(
        {"BL-169", "BL-188", "BL-193", "BL-266", "BL-295", "BL-326", "BL-350", "BL-363"}
    ),
    required_tests=(
        "test_judge_concept_page_states_no_claims",
        "test_judge_readme_quick_start_runs_offline",
    ),
    index_name="llm-judge.md",
)


def _real() -> str:
    assert PAGE.is_file(), f"{PAGE.relative_to(ROOT)} does not exist"
    text: str = PAGE.read_text("utf-8")
    return text


@functools.cache
def _backlog() -> frozenset[str]:
    return frozenset(backlog_ids())


@functools.cache
def _tests() -> frozenset[str]:
    return frozenset(defined_tests())


def _problems(text: str) -> list[str]:
    found: list[str] = page_problems(text, SPEC, set(_backlog()), set(_tests()), CONCEPTS)
    return found


# ------------------------------------------------------------------------------ the real page


def test_judge_concept_page_states_no_claims() -> None:
    """ADR 0030 rule 9 and ADR 0023 amendment A4: the fixed sentence once, alone in the one fenced
    block of 'Not claimed', verbatim the ADRs' sentence; no claim word of rule 1, A1 or A4 anywhere
    else; no private path, no secret, only the nine language codes; the required statements, cites
    and links hold."""
    text = _real()
    adr23 = (ROOT / "docs" / "adr" / "0023-recorded-call-agreement-scoring.md").read_text("utf-8")
    adr30 = (ROOT / "docs" / "adr" / "0030-llm-judge-uncalibrated-signal.md").read_text("utf-8")
    assert JUDGE_SENTENCE in adr23, "the sentence of this test is not verbatim in ADR 0023 A4"
    assert JUDGE_SENTENCE in adr30, "the sentence of this test is not verbatim in ADR 0030"
    assert _problems(text) == []


def test_judge_concept_page_has_fixed_headings_and_required_statements() -> None:
    text = _real()
    assert text.splitlines()[0] == TITLE
    assert h2_headings(text) == list(HEADINGS)
    assert [r.key for r in unmet(sections(text), REQUIREMENTS)] == []


def test_judge_concept_page_is_linked_from_the_concepts_index() -> None:
    assert index_problems(INDEX.read_text("utf-8"), "llm-judge.md") == []


def test_judge_concept_page_names_every_profile_and_keeps_not_verified() -> None:
    """The seven profile names of ADR 0030 rule 13 are on the page, and the page does not call any
    profile verified: the dossier says ``unverified`` for all seven."""
    body = sections(_real())[PR]
    for profile in PROFILES:
        assert profile in body, profile
    assert not re.search(r"\bverified\b", body.replace("not verified", ""))


def test_the_judge_requirement_table_is_well_formed() -> None:
    keys = [r.key for r in REQUIREMENTS]
    assert len(keys) == len(set(keys))
    assert {r.section for r in REQUIREMENTS} <= set(HEADINGS)
    for r in REQUIREMENTS:
        for alt in r.alternatives:
            for pattern in alt:
                re.compile(pattern)


# ------------------------------------------------------------------------------ teeth (mutants)


@pytest.mark.parametrize("key", [r.key for r in REQUIREMENTS])
def test_the_judge_checker_reports_each_missing_statement(key: str) -> None:
    """Mutant: every line that makes the statement is removed from its section."""
    (requirement,) = [r for r in REQUIREMENTS if r.key == key]
    problems = _problems(drop_requirement(_real(), requirement))
    assert f"missing statement: {key}" in problems, problems


CLAIM_MUTANTS = [
    "accuracy", "WER", "CER", "error rate", "Error  Rate", "error-rate", "correct", "Recall",
    "PRECISION", "latency", "response time", "quality", "task success", "realism", "realistic",
    "fidelity", "deterministic", "determinism", "correctness", "incorrect", "factual",
    "factuality", "truthful", "hallucination", "ground truth", "calibrated", "probability",
    "confidence", "pass", "fail", "passed", "failed", "score of the agent",
    "\uff41ccuracy",  # a full-width letter
    "acc\u200buracy",  # a zero-width space
    "error\nrate",
]  # fmt: skip


@pytest.mark.parametrize("word", CLAIM_MUTANTS)
def test_the_judge_checker_refuses_a_claim_word_outside_the_sentence(word: str) -> None:
    text = _real()
    for heading in (W, RD, K):
        body = sections(text)[heading]
        mutated = text.replace(
            body, body.rstrip("\n") + f"\n\nExtra line about the {word} of it.\n\n", 1
        )
        assert any("claim words" in p for p in _problems(mutated)), (heading, word)


def test_the_judge_checker_allows_the_word_uncalibrated_and_requires_it() -> None:
    text = _real()
    assert "uncalibrated" in sections(text)[W]
    body = sections(text)[RD]
    mutated = text.replace(body, body.rstrip("\n") + "\n\nAn uncalibrated count is a count.\n\n", 1)
    assert not any("claim words" in p for p in _problems(mutated))


def test_the_judge_checker_refuses_a_deny_word_in_the_fence_beside_the_sentence() -> None:
    text = _real()
    mutated = text.replace(
        JUDGE_SENTENCE + "\n```", JUDGE_SENTENCE + "\nNo recall is claimed.\n```", 1
    )
    assert mutated != text
    problems = _problems(mutated)
    assert any("claim words" in p for p in problems), problems
    assert any("more than the fixed sentence" in p for p in problems), problems


def test_the_judge_checker_refuses_a_missing_repeated_changed_or_misplaced_sentence() -> None:
    text = _real()
    gone = replace_sentence(text, JUDGE_SENTENCE, "")
    assert any("occurs 0 times" in p for p in _problems(gone))
    twice = text.replace("## Golden sets", JUDGE_SENTENCE + "\n\n## Golden sets", 1)
    assert any("occurs 2 times" in p for p in _problems(twice))
    for old, new in (("uncalibrated", "calibrated"), ("whether a model said", "whether a model says"),
                     ("agent's answer", "agent answer"), ("none of its numbers", "none of the numbers")):  # fmt: skip
        changed = replace_sentence(text, JUDGE_SENTENCE, JUDGE_SENTENCE.replace(old, new))
        assert any("occurs 0 times" in p for p in _problems(changed)), old
    unfenced = re.sub(r"^```[^\n]*\n|^```[ \t]*$\n?", "", text, flags=re.M)
    assert any("has 0 fenced blocks" in p for p in _problems(unfenced))


def test_the_judge_checker_refuses_a_second_fenced_block() -> None:
    text = _real()
    mutated = text.replace("## Known limits", "```text\nsome output\n```\n\n## Known limits", 1)
    assert any("fenced block outside" in p for p in _problems(mutated))


def test_the_judge_checker_refuses_wrong_headings_and_title() -> None:
    text = _real()
    assert any(
        "second-level headings" in p
        for p in _problems(text.replace("## Exit codes", "## Exits", 1))
    )
    assert any("first line" in p for p in _problems(text.replace(TITLE, "# Judge", 1)))
    assert any("second-level headings" in p for p in _problems(text + "\n## Extra\n\ntext\n"))


PRIVATE_SNIPPETS = [
    pytest.param("/Users/someone/work", id="users-path"),
    pytest.param("/home/someone/work", id="home-path"),
    pytest.param("~/" + "tracer-runs/run1", id="runs-directory"),
    pytest.param("see ~/" + "github-purge-request.md", id="purge-request-file"),
    pytest.param("the file .env" + ".local holds it", id="local-env-file"),
    pytest.param("packages/evalschema/tests/fixtures/" + "private/x", id="private-fixture-path"),
    pytest.param("C:\\Users\\someone", id="windows-path"),
    pytest.param("/private" + "/tm" + "p/x", id="macos-temp"),
    pytest.param("pytest-of-" + "someone", id="pytest-temp"),
    pytest.param("/Desk" + "top/notes", id="desktop-path"),
]


@pytest.mark.parametrize("snippet", PRIVATE_SNIPPETS)
def test_the_judge_checker_refuses_a_private_path(snippet: str) -> None:
    text = _real()
    body = sections(text)[Q]
    mutated = text.replace(body, body.rstrip("\n") + f"\n\nSee {snippet}.\n\n", 1)
    assert "a private or machine-specific path" in _problems(mutated)


SECRET_SNIPPETS = [
    pytest.param("sk-" + "a1b2c3d4e5f6a7b8c9d0", id="sk-key"),
    pytest.param("AKIA" + "ABCDEFGHIJKLMNOP", id="aws-key"),
    pytest.param("AIza" + "SyA1B2C3D4E5F6G7H8I9J0K1", id="google-key"),
    pytest.param("xai-" + "a1b2c3d4e5f6a7b8c9d0e1", id="xai-key"),
    pytest.param("Bearer " + "abcdefghijklmnop1234", id="bearer-token"),
    pytest.param("api_key=" + "abcdefghijkl1234", id="assigned-key"),
    pytest.param("0123456789abcdef" * 3, id="long-hex"),
    pytest.param("-----BEGIN " + "PRIVATE KEY-----", id="private-key-block"),
]


@pytest.mark.parametrize("snippet", SECRET_SNIPPETS)
def test_the_judge_checker_refuses_a_secret_shaped_string(snippet: str) -> None:
    text = _real()
    body = sections(text)[OA]
    mutated = text.replace(body, body.rstrip("\n") + f"\n\nExample {snippet} here.\n\n", 1)
    assert "a secret-shaped string" in _problems(mutated)


def test_the_judge_checker_allows_a_variable_name_but_not_its_value() -> None:
    text = _real()
    body = sections(text)[OA]
    named = text.replace(
        body, body.rstrip("\n") + "\n\nThe variable JUDGE_KEY_VAR holds it.\n\n", 1
    )
    assert "a secret-shaped string" not in _problems(named)


def _other_language_codes() -> list[str]:
    # built from parts: the repository scan for out-of-scope language tokens must not see them
    return ["b" + "n", "u" + "r", "o" + "r", "a" + "s", "f" + "r", "de", "zh"]


@pytest.mark.parametrize("which", range(7))
def test_the_judge_checker_refuses_a_language_code_outside_the_nine(which: int) -> None:
    code = _other_language_codes()[which]
    text = _real()
    body = sections(text)[K]
    for snippet in (f"The language `{code}` is added.", f"The tag {code}-IN is added."):
        mutated = text.replace(body, body.rstrip("\n") + f"\n\n{snippet}\n\n", 1)
        assert any("language codes outside" in p for p in _problems(mutated)), snippet


@pytest.mark.parametrize("code", ["en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu"])
def test_the_judge_checker_allows_each_of_the_nine_language_codes(code: str) -> None:
    text = _real()
    body = sections(text)[K]
    mutated = text.replace(
        body, body.rstrip("\n") + f"\n\nThe code `{code}` and the tag {code}-IN.\n\n", 1
    )
    assert not any("language codes outside" in p for p in _problems(mutated))


def test_the_judge_checker_refuses_unknown_rows_tests_and_links() -> None:
    text = _real()
    body = sections(text)[K]

    def add(snippet: str) -> str:
        return text.replace(body, body.rstrip("\n") + f"\n\n{snippet}\n\n", 1)

    assert any("BL-999" in p for p in _problems(add("See BL-999.")))
    assert any("test_nope" in p for p in _problems(add("Named `test_nope`.")))
    assert any("does not cite BL-350" in p for p in _problems(text.replace("BL-350", "BL-169")))
    assert any(
        "does not name test_judge_readme_quick_start_runs_offline" in p
        for p in _problems(text.replace("`test_judge_readme_quick_start_runs_offline`", ""))
    )
    broken = text.replace("../security/P4-J-providers.md", "../security/none.md")
    problems = _problems(broken)
    assert any("does not resolve" in p for p in problems)
    assert any("does not link ../security/P4-J-providers.md in 'Related'" in p for p in problems)


def test_the_judge_checker_refuses_invisible_characters() -> None:
    text = _real()
    body = sections(text)[W]
    mutated = text.replace(body, body.rstrip("\n") + "\n\nZero\u200bwidth in the open.\n\n", 1)
    assert any("format or control" in p for p in _problems(mutated))


def test_the_judge_checker_refuses_a_missing_index_row() -> None:
    index = INDEX.read_text("utf-8")
    assert index_problems(index.replace("llm-judge.md", "other.md"), "llm-judge.md")


def test_a_judge_statement_must_sit_in_its_own_section() -> None:
    """Mutant: the statement about the denominator of ``labels check`` moves to another section."""
    text = _real()
    (requirement,) = [r for r in REQUIREMENTS if r.key == "labels_check_denominator"]
    moved = drop_requirement(text, requirement)
    body = sections(moved)[K]
    moved = moved.replace(
        body,
        body.rstrip("\n")
        + "\n\nThe line labelled n of N counts the labels the file lists (BL-350).\n\n",
        1,
    )
    assert "missing statement: labels_check_denominator" in _problems(moved)


def test_a_judge_statement_inside_a_fence_does_not_count() -> None:
    text = _real()
    (requirement,) = [
        r for r in REQUIREMENTS if r.key == "authorisation_reference_hash_printed_not_stored"
    ]
    hidden = drop_requirement(text, requirement)
    body = sections(hidden)[OA]
    hidden = hidden.replace(
        body,
        body.rstrip("\n") + "\n\n```text\nThe sha256 is printed and not stored (BL-363).\n```\n\n",
        1,
    )
    problems = _problems(hidden)
    assert "missing statement: authorisation_reference_hash_printed_not_stored" in problems
    assert any("fenced block outside" in p for p in problems)
