# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-37: the concept page ``docs/concepts/simulated-calls.md`` (ADR 0023 amendment A1, invariant IA1).

The page must say what a simulated call measures and, as plainly, what Tracer does not claim. This
module reads the page and checks that it cannot drift from the ADRs and the code:

* the one fixed sentence of ADR 0023 amendment A1 occurs exactly once, alone in the one fenced
  block of the section "Not claimed", and is verbatim the sentence of that ADR;
* none of the claim words (ADR 0023 rule 1, A1 and A4 together) appears anywhere else on the page;
* the page has the fixed headings and makes each required statement (``REQUIREMENTS``; each fits
  one sentence, bullet or table row of its section);
* it holds no private path, no secret-shaped string and no language code outside the nine
  supported ones; every BL id it cites exists, every test it names exists, every relative link
  resolves, and the concept index links the page.

The checker ``evalcore_p4docs_support.page_problems`` has teeth: each rule has a mutation of the real
page that must be refused with the matching problem. The checks read files only.
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
    SIM_SENTENCE,
    PageSpec,
    drop_requirement,
    index_problems,
    page_problems,
    replace_sentence,
)

PAGE = CONCEPTS / "simulated-calls.md"
INDEX = CONCEPTS / "README.md"
TITLE = "# Simulated calls"
HEADINGS = (
    "What a simulated call is",
    "What is measured",
    "Not claimed",
    "Roles, tier and outcome",
    "Safety and spend",
    "Privacy",
    "Offline quick start",
    "Known limits",
    "Related",
)
W, M, N, R, S, P, Q, K = HEADINGS[:8]


def req(key: str, section: str, *alternatives: tuple[str, ...]) -> Requirement:
    return Requirement(key, section, alternatives)


REQUIREMENTS: tuple[Requirement, ...] = (
    req("caller_is_synthetic", W, (r"\bcaller\b", r"\bsynthetic\b", r"\bscripted\b")),
    req("only_the_agent_is_evaluated", W, (r"\bonly\b", r"\bagent\b", r"evaluated|under test")),
    req("one_wire_dialect", W, (r"\bdialect\b", r"pipecat-protobuf/1")),
    req("one_call_per_invocation", W, (r"\bone call\b", r"invocation|command")),
    req("fake_engines_send_nothing", W, (r"fake:", r"sends? nothing|nothing is sent|no socket")),
    req("observed_at_the_simulator", M, (r"observed at the simulator over",)),
    req("first_agent_audio", M, (r"first agent audio",)),
    req("gap_median_with_n", M, (r"\bgap\b", r"\bmedian\b", r"\bN\b")),
    req("overlap", M, (r"\boverlap\b",)),
    req("answered_caller_turns", M, (r"answered caller turns",)),
    req("quantum", M, (r"\bquantum\b", r"\bframe\b")),
    req("simulator_own_clock", M, (r"\bmonotonic\b|own clock", r"\bsimulator\b")),
    req(
        "agreement_with_the_export",
        M,
        (r"\bagreement\b", r"\bexport\b", r"optional|when you supply|only when"),
    ),
    req(
        "timing_is_not_the_agents_own",
        N,
        (r"\btimings?\b", r"\bnot\b", r"\bagent's own\b|inside the agent"),
    ),
    req(
        "no_comparison_between_agents",
        N,
        (r"\bcompar", r"\bnot\b|\bnever\b|\bno\b", r"agents|dialects"),
    ),
    req("outcome_not_evaluated_in_claims", N, (r"outcome not evaluated",)),
    req("fixed_sentence_is_the_only_list", N, (r"\bsentence\b", r"\bonce\b|\bonly\b|\bone\b")),
    req("roles", R, (r"\bcaller\b", r"\bagent\b", r"\bunknown\b", r"\broles?\b")),
    req("tier_t0", R, (r"\bT0\b", r"\btier\b")),
    req("stereo_channels", R, (r"channel 0", r"channel 1")),
    req("outcome_not_evaluated", R, (r"outcome not evaluated", r"no verdict|not produced|inert")),
    req("allowlist_before_any_key", S, (r"\ballowlist\b", r"before any key|before a key")),
    req("caps_have_no_default", S, (r"\bcaps?\b", r"no default")),
    req("kill_switch", S, (r"kill-switch",)),
    req("minimum_interval", S, (r"call\.lock|minimum interval", r"--out")),
    req(
        "authorisation_reference_hash",
        S,
        (r"--authorisation-reference", r"SHA-256", r"never stored|text is never|not stored"),
    ),
    req("dry_run_sends_nothing", S, (r"--dry-run", r"sends? nothing|nothing is sent")),
    req("terms_are_the_owners", S, (r"\bterms\b", r"\bowner\b")),
    req(
        "synthetic_caller_audio_label",
        P,
        (r"synthetic caller audio|generated speech, not a person",),
    ),
    req("report_and_store_hold_text", P, (r"\breport\b", r"\bstore\b", r"\btext\b", r"\bgit\b")),
    req("out_outside_repository", P, (r"--out", r"\boutside\b", r"\brepository\b")),
    req(
        "agent_text_shown_escaped",
        P,
        (
            r"\bescaped\b",
            r"agent",
        ),
    ),
    req("fake_reads_no_key", P, (r"fake:", r"no key|reads no key|without a key")),
    req("quick_start_pointer", Q, (r"packages/cli/README\.md", r"quick start")),
    req("quick_start_is_plumbing", Q, (r"\bplumbing\b|nothing about any agent", r"fake")),
    req("instants_differ_per_run", Q, (r"differ|vary", r"run")),
    req("no_native_review", K, (r"\bnative\b", r"\breview\b", r"BL-169")),
    req("telephony_later", K, (r"telephony", r"BL-311")),
    req("token_cap_crossing", K, (r"\btoken cap\b|llm cap", r"BL-295")),
    req("timing_source_not_dedicated", K, (r"timing source|audio_alignment", r"BL-285")),
    req("no_run_verdict", K, (r"RunVerdict|verdict", r"BL-270")),
)

SPEC = PageSpec(
    path=PAGE,
    title=TITLE,
    headings=HEADINGS,
    sentence=SIM_SENTENCE,
    requirements=REQUIREMENTS,
    required_links=(
        "../adr/0023-recorded-call-agreement-scoring.md",
        "../adr/0026-simulated-caller-architecture.md",
        "../adr/0027-simulated-run-records.md",
        "../adr/0028-caller-brain-prompt-contract.md",
        "../../packages/cli/README.md",
        "../../packages/evalsim/README.md",
        "../examples/simulated-mr/README.md",
        "recorded-call-scoring.md",
        "llm-judge.md",
    ),
    required_bl=frozenset({"BL-169", "BL-270", "BL-285", "BL-295", "BL-311"}),
    required_tests=(
        "test_sim_concept_page_states_no_claims",
        "test_sim_readme_quick_start_runs_offline",
    ),
    index_name="simulated-calls.md",
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


def test_sim_concept_page_states_no_claims() -> None:
    """ADR 0023 amendment A1 and invariant IA1: the one fixed sentence once, alone in the one
    fenced block of 'Not claimed', verbatim the ADR's sentence; no claim word of rule 1, A1 or A4
    anywhere else; no private path, no secret, only the nine language codes; the required
    statements, cites and links hold."""
    text = _real()
    adr = (ROOT / "docs" / "adr" / "0023-recorded-call-agreement-scoring.md").read_text("utf-8")
    assert SIM_SENTENCE in adr, "the sentence of this test is not verbatim in ADR 0023 A1"
    assert _problems(text) == []


def test_sim_concept_page_has_fixed_headings_and_required_statements() -> None:
    text = _real()
    assert text.splitlines()[0] == TITLE
    assert h2_headings(text) == list(HEADINGS)
    assert [r.key for r in unmet(sections(text), REQUIREMENTS)] == []


def test_sim_concept_page_is_linked_from_the_concepts_index() -> None:
    assert index_problems(INDEX.read_text("utf-8"), "simulated-calls.md") == []


def test_the_sim_requirement_table_is_well_formed() -> None:
    keys = [r.key for r in REQUIREMENTS]
    assert len(keys) == len(set(keys))
    assert {r.section for r in REQUIREMENTS} <= set(HEADINGS)
    for r in REQUIREMENTS:
        for alt in r.alternatives:
            for pattern in alt:
                re.compile(pattern)


# ------------------------------------------------------------------------------ teeth (mutants)


@pytest.mark.parametrize("key", [r.key for r in REQUIREMENTS])
def test_the_sim_checker_reports_each_missing_statement(key: str) -> None:
    """Mutant: every line that makes the statement is removed from its section."""
    (requirement,) = [r for r in REQUIREMENTS if r.key == key]
    problems = _problems(drop_requirement(_real(), requirement))
    assert f"missing statement: {key}" in problems, problems


CLAIM_MUTANTS = [
    "accuracy", "WER", "CER", "error rate", "Error  Rate", "error-rate", "correct", "Recall",
    "PRECISION", "latency", "response time", "quality", "task success", "realism", "realistic",
    "fidelity", "deterministic", "determinism", "correctness", "factual", "factuality",
    "truthful", "hallucination", "ground truth", "calibrated", "probability", "confidence",
    "pass", "fail", "passed", "failed", "score of the agent",
    "\uff41ccuracy",  # a full-width letter
    "acc\u200buracy",  # a zero-width space
    "error\nrate",
]  # fmt: skip


@pytest.mark.parametrize("word", CLAIM_MUTANTS)
def test_the_sim_checker_refuses_a_claim_word_outside_the_sentence(word: str) -> None:
    text = _real()
    for heading in (M, K):
        body = sections(text)[heading]
        mutated = text.replace(
            body, body.rstrip("\n") + f"\n\nExtra line about the {word} of it.\n\n", 1
        )
        assert any("claim words" in p for p in _problems(mutated)), (heading, word)


def test_the_sim_checker_allows_the_word_uncalibrated() -> None:
    text = _real()
    body = sections(text)[M]
    mutated = text.replace(
        body, body.rstrip("\n") + "\n\nAn uncalibrated number is still a number.\n\n", 1
    )
    assert not any("claim words" in p for p in _problems(mutated))


def test_the_sim_checker_refuses_a_deny_word_in_the_fence_beside_the_sentence() -> None:
    text = _real()
    mutated = text.replace(SIM_SENTENCE + "\n```", SIM_SENTENCE + "\nNo recall is claimed.\n```", 1)
    assert mutated != text
    problems = _problems(mutated)
    assert any("claim words" in p for p in problems), problems
    assert any("more than the fixed sentence" in p for p in problems), problems


def test_the_sim_checker_refuses_a_missing_repeated_or_misplaced_sentence() -> None:
    text = _real()
    fenced = f"```text\n{SIM_SENTENCE}\n```"
    assert fenced in text or SIM_SENTENCE in text
    gone = replace_sentence(text, SIM_SENTENCE, "")
    assert any("occurs 0 times" in p for p in _problems(gone))
    twice = text.replace(
        "## Roles, tier and outcome", SIM_SENTENCE + "\n\n## Roles, tier and outcome", 1
    )
    assert any("occurs 2 times" in p for p in _problems(twice))
    changed = replace_sentence(text, SIM_SENTENCE, SIM_SENTENCE.replace("observed", "saw"))
    assert any("occurs 0 times" in p for p in _problems(changed))
    unfenced = re.sub(r"^```[^\n]*\n|^```[ \t]*$\n?", "", text, flags=re.M)
    problems = _problems(unfenced)
    assert any("has 0 fenced blocks" in p for p in problems), problems


def test_the_sim_checker_refuses_a_second_fenced_block() -> None:
    text = _real()
    mutated = text.replace("## Known limits", "```text\nsome output\n```\n\n## Known limits", 1)
    assert any("fenced block outside" in p for p in _problems(mutated))


def test_the_sim_checker_refuses_wrong_headings_and_title() -> None:
    text = _real()
    assert any(
        "second-level headings" in p
        for p in _problems(text.replace("## Privacy", "## Privacy notes", 1))
    )
    assert any("first line" in p for p in _problems(text.replace(TITLE, "# Simulated", 1)))
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
def test_the_sim_checker_refuses_a_private_path(snippet: str) -> None:
    text = _real()
    body = sections(text)[Q]
    mutated = text.replace(body, body.rstrip("\n") + f"\n\nSee {snippet}.\n\n", 1)
    assert "a private or machine-specific path" in _problems(mutated)


SECRET_SNIPPETS = [
    pytest.param("sk-" + "a1b2c3d4e5f6a7b8c9d0", id="sk-key"),
    pytest.param("AKIA" + "ABCDEFGHIJKLMNOP", id="aws-key"),
    pytest.param("AIza" + "SyA1B2C3D4E5F6G7H8I9J0K1", id="google-key"),
    pytest.param("Bearer " + "abcdefghijklmnop1234", id="bearer-token"),
    pytest.param("api_key=" + "abcdefghijkl1234", id="assigned-key"),
    pytest.param("0123456789abcdef" * 3, id="long-hex"),
    pytest.param("-----BEGIN " + "PRIVATE KEY-----", id="private-key-block"),
]


@pytest.mark.parametrize("snippet", SECRET_SNIPPETS)
def test_the_sim_checker_refuses_a_secret_shaped_string(snippet: str) -> None:
    text = _real()
    body = sections(text)[Q]
    mutated = text.replace(body, body.rstrip("\n") + f"\n\nExample {snippet} here.\n\n", 1)
    assert "a secret-shaped string" in _problems(mutated)


def _other_language_codes() -> list[str]:
    # built from parts: the repository scan for out-of-scope language tokens must not see them
    return ["b" + "n", "u" + "r", "o" + "r", "a" + "s", "f" + "r", "de", "zh"]


@pytest.mark.parametrize("which", range(7))
def test_the_sim_checker_refuses_a_language_code_outside_the_nine(which: int) -> None:
    code = _other_language_codes()[which]
    text = _real()
    body = sections(text)[K]
    for snippet in (f"The language `{code}` is added.", f"The tag {code}-IN is added."):
        mutated = text.replace(body, body.rstrip("\n") + f"\n\n{snippet}\n\n", 1)
        assert any("language codes outside" in p for p in _problems(mutated)), snippet


@pytest.mark.parametrize("code", ["en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu"])
def test_the_sim_checker_allows_each_of_the_nine_language_codes(code: str) -> None:
    text = _real()
    body = sections(text)[K]
    mutated = text.replace(
        body, body.rstrip("\n") + f"\n\nThe code `{code}` and the tag {code}-IN.\n\n", 1
    )
    assert not any("language codes outside" in p for p in _problems(mutated))


def test_the_sim_checker_refuses_unknown_rows_tests_and_links() -> None:
    text = _real()
    body = sections(text)[K]

    def add(snippet: str) -> str:
        return text.replace(body, body.rstrip("\n") + f"\n\n{snippet}\n\n", 1)

    assert any("BL-999" in p for p in _problems(add("See BL-999.")))
    assert any("test_nope" in p for p in _problems(add("Named `test_nope`.")))
    assert any("does not cite BL-295" in p for p in _problems(text.replace("BL-295", "BL-169")))
    assert any(
        "does not name test_sim_readme_quick_start_runs_offline" in p
        for p in _problems(text.replace("`test_sim_readme_quick_start_runs_offline`", ""))
    )
    broken = text.replace("../examples/simulated-mr/README.md", "../examples/none.md")
    problems = _problems(broken)
    assert any("does not resolve" in p for p in problems)
    assert any(
        "does not link ../examples/simulated-mr/README.md in 'Related'" in p for p in problems
    )


def test_the_sim_checker_refuses_invisible_characters() -> None:
    text = _real()
    body = sections(text)[M]
    mutated = text.replace(body, body.rstrip("\n") + "\n\nZero\u200bwidth in the open.\n\n", 1)
    assert any("format or control" in p for p in _problems(mutated))


def test_the_sim_checker_refuses_a_missing_index_row() -> None:
    index = INDEX.read_text("utf-8")
    assert index_problems(index.replace("simulated-calls.md", "other.md"), "simulated-calls.md")


def test_a_sim_statement_must_sit_in_its_own_section() -> None:
    """Mutant: the statement 'tier T0' moves from its section to another."""
    text = _real()
    (requirement,) = [r for r in REQUIREMENTS if r.key == "tier_t0"]
    moved = drop_requirement(text, requirement)
    body = sections(moved)[P]
    moved = moved.replace(body, body.rstrip("\n") + "\n\nThe run is at tier T0.\n\n", 1)
    assert "missing statement: tier_t0" in _problems(moved)


def test_a_sim_statement_inside_a_fence_does_not_count() -> None:
    text = _real()
    (requirement,) = [r for r in REQUIREMENTS if r.key == "tier_t0"]
    hidden = drop_requirement(text, requirement)
    body = sections(hidden)[R]
    hidden = hidden.replace(
        body, body.rstrip("\n") + "\n\n```text\nThe run is at tier T0.\n```\n\n", 1
    )
    problems = _problems(hidden)
    assert "missing statement: tier_t0" in problems
    assert any("fenced block outside" in p for p in problems)
