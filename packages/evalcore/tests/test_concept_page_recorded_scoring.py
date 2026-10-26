# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""SCOPE P3-32 and ADR 0023 rule 1 (invariant I1): the concept page of recorded-call scoring.

``docs/concepts/recorded-call-scoring.md`` must say what is measured and, as plainly, what is not
claimed. This module reads the page and checks that it cannot drift from the code and the ADRs:

* the deny-list of ADR 0023 rule 1 (accuracy, WER, CER, error rate, correct, recall, precision):
  the words appear only inside the one fenced block of the section "Not claimed", and there only
  in the one verbatim sentence of the ADR, which occurs exactly once on the page;
* the page has the fixed headings and makes each required statement (the table ``REQUIREMENTS``
  below; each statement fits one sentence, bullet or table row of its section);
* every BL id it cites exists in ``docs/BACKLOG.md`` and the required ones are cited; every test
  it names exists; every relative link resolves; the concept index links the page.

The checker ``page_problems`` has teeth: ``GOOD_PAGE`` is a small, hand-written page that passes,
and one mutation per rule must fail with the matching problem. ``GOOD_PAGE`` is a fixture of the
checker, not the page. The checks read files only; nothing is imported from the product.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from evalcore_batchh_support import (  # type: ignore[import-not-found,unused-ignore]
    BL_ID,
    CONCEPTS,
    FENCE,
    LINK,
    NOT_CLAIMED_SENTENCE,
    ROOT,
    TEST_NAME,
    Requirement,
    backlog_ids,
    defined_tests,
    deny_hits_outside_sentence,
    fold,
    h2_headings,
    sections,
    unmet,
)

PAGE = CONCEPTS / "recorded-call-scoring.md"
INDEX = CONCEPTS / "README.md"
TITLE = "# Recorded-call scoring"
HEADINGS = [
    "What is measured",
    "Not claimed",
    "Scope, roles and tier",
    "Cost",
    "Privacy and consent",
    "Run book",
    "Known limits",
    "Related",
]
REQUIRED_BL = {"BL-165", "BL-169", "BL-170", "BL-192", "BL-234"}
REQUIRED_LINKS = (
    "../adr/0023-recorded-call-agreement-scoring.md",
    "../adr/0024-provider-adapters-byok-sarvam-rest.md",
    "../adr/0025-marathi-comparison-normaliser.md",
    "../../packages/cli/README.md",
    "../examples/synthetic-mr/README.md",
)
REQUIRED_TESTS = ("test_concept_page_states_no_accuracy_claim",)

M, N, S, C, P, R, K = (
    "What is measured",
    "Not claimed",
    "Scope, roles and tier",
    "Cost",
    "Privacy and consent",
    "Run book",
    "Known limits",
)


def req(key: str, section: str, *alternatives: tuple[str, ...]) -> Requirement:
    return Requirement(key, section, alternatives)


REQUIREMENTS: tuple[Requirement, ...] = (
    req(
        "agreement_definition",
        M,
        (
            r"\bagreement\b",
            r"agent(?:'s)? (?:transcript )?export",
            r"speech-to-text|\bSTT\b",
            r"same (?:audio|recording|call)",
            r"\bindependent\b",
        ),
    ),
    req("mer_label", M, (r"agreement distance \(MER\)",)),
    req("mer_citation", M, (r"Morris, Maier and Green \(2004\)",)),
    req("counts_with_denominators", M, (r"\bdenominators?\b", r"\bcounts?\b")),
    req("threshold_has_no_default", M, (r"flag_min_confidence", r"no default")),
    req("confidence_uncalibrated", M, (r"\bconfidence\b", r"\buncalibrated\b|not calibrated")),
    req("confidence_not_a_probability", M, (r"\bconfidence\b", r"not a probability")),
    req(
        "a_flag_is_not_an_error",
        N,
        (r"\bflag\b", r"\bnot\b|\bnever\b|\bno\b", r"\berror\b|\bmistake|\bwrong"),
    ),
    req(
        "unflagged_is_not_vouched_for",
        N,
        (
            r"\bunflagged\b",
            r"\bnot\b|\bnever\b|\bno\b",
            r"\bright\b|\bverified\b|\bconfirmed\b|\bvouch",
        ),
    ),
    req("export_is_not_a_reference", N, (r"\bexport\b", r"\bnot\b", r"ground truth|\breference\b")),
    req(
        "two_clips_are_a_demonstration",
        N,
        (r"\btwo\b", r"\bclips\b|\bcalls\b", r"demonstration", r"BL-170"),
    ),
    req("marathi_only", S, (r"Marathi", r"\bmr\b", r"\bonly\b")),
    req("declared_language", S, (r"\bdeclared\b", r"--declared-language")),
    req("caller_never_evaluated", S, (r"\bcaller\b", r"\bnever\b", r"evaluated|judged|scored")),
    req(
        "caller_lines_are_agent_output",
        S,
        (r"\bcaller\b", r"\bexport\b", r"agent(?:'s)? own", r"recogni"),
    ),
    req("stt_turns_have_role_unknown", S, (r"\bunknown\b", r"speech-to-text|\bSTT\b", r"\brole\b")),
    req("flags_are_labelled_with_the_role", S, (r"\breport\b", r"\brole\b", r"\bflag")),
    req("tier_t0", S, (r"\bT0\b", r"\btier\b")),
    req("cost_not_available", C, (r"\bnot_available\b", r"\bUSD\b|\bprice\b")),
    req("cost_never_zero", C, (r"\bnever\b", r"\b0\b|\bzero\b", r"\bcost\b")),
    req(
        "audio_leaves_only_with_stt_sarvam",
        P,
        (r"\baudio\b", r"--stt sarvam", r"\bonly\b", r"Sarvam"),
    ),
    req("replay_sends_nothing", P, (r"fake:", r"sends? nothing|nothing is sent|no network")),
    req("report_and_store_hold_text", P, (r"\breport\b", r"\bstore\b", r"\btext\b", r"\bgit\b")),
    req(
        "call_log_holds_no_text",
        P,
        (r"call log", r"holds? no (?:call )?text|no (?:call )?text|counts, ids and timings"),
    ),
    req("out_is_outside_the_repository", P, (r"--out", r"\boutside\b", r"\brepository\b")),
    req("consent_record", P, (r"consent record", r"--consent-reference")),
    req(
        "retention_recorded_not_enforced",
        P,
        (r"\bretention\b", r"\brecorded\b", r"not enforced", r"BL-234"),
    ),
    req(
        "embedding_audio_is_opt_in",
        P,
        (r"--embed-audio", r"\baudio\b", r"opt-in|only with|only when"),
    ),
    req("run_book_pointer", R, (r"Run book for a real send", r"ADR 0024")),
    req("dry_run_first", R, (r"dry run", r"\bfirst\b")),
    req("max_chunks_ceiling", R, (r"--max-chunks", r"\bceiling\b|\brefuse")),
    req("offline_quick_start_pointer", R, (r"packages/cli/README\.md", r"offline|quick start")),
    req("number_words_against_digits", K, (r"number words", r"\bdigits\b", r"BL-192")),
    req("two_letter_threshold", K, (r"two-letter|2-letter", r"\bthreshold\b")),
    req("split_word_below_threshold", K, (r"\bsplit\b", r"\bword\b", r"\bthreshold\b")),
    req(
        "noise_floor",
        K,
        (r"noise floor", r"unmeasured|not (?:yet )?measured", r"P3-31"),
        (r"noise floor", r"docs/phase3/measurements/"),
    ),
    req("native_review_missing", K, (r"\bnative\b", r"\breview\b", r"BL-169")),
)


# ------------------------------------------------------------------------------ the checker


def page_problems(text: str, backlog: set[str], tests: set[str], base: Path) -> list[str]:
    """Every way ``text`` breaks the rules of the module docstring; empty when it holds."""
    found: list[str] = []
    found += deny_problems(text)
    lines = text.splitlines()
    if not lines or lines[0] != TITLE:
        found.append(f"the first line is not {TITLE!r}")
    if h2_headings(text) != HEADINGS:
        found.append(f"the second-level headings are {h2_headings(text)}, not {HEADINGS}")
    body = sections(text)
    found += [f"missing statement: {r.key}" for r in unmet(body, REQUIREMENTS)]
    cited = set(BL_ID.findall(text))
    found += [f"cites a BACKLOG row that does not exist: {b}" for b in sorted(cited - backlog)]
    found += [f"does not cite {b}" for b in sorted(REQUIRED_BL - cited)]
    named = set(TEST_NAME.findall(text))
    found += [f"names a test that does not exist: {t}" for t in sorted(named - tests)]
    found += [f"does not name {t}" for t in REQUIRED_TESTS if t not in named]
    targets = [t for t in LINK.findall(text) if not re.match(r"[a-z]+:", t)]
    related = LINK.findall(body.get("Related", ""))
    for target in targets:
        if not (base / target.split("#", 1)[0]).resolve().exists():
            found.append(f"a link does not resolve: {target}")
    found += [f"does not link {t} in 'Related'" for t in REQUIRED_LINKS if t not in related]
    if any(unicode_is_hidden(c) for c in text):
        found.append("holds a format or control character")
    return found


def unicode_is_hidden(char: str) -> bool:
    return unicodedata.category(char) in ("Cf", "Cc") and char not in "\n\t"


def deny_problems(text: str) -> list[str]:
    """The deny-list rule alone (ADR 0023 rule 1): the words only in the fenced block of the
    section 'Not claimed', and there only in the verbatim sentence, which occurs exactly once."""
    found: list[str] = []
    outside, count = deny_hits_outside_sentence(text)
    if outside:
        found.append(f"deny-listed words outside the one verbatim sentence: {sorted(set(outside))}")
    if count != 1:
        found.append(f"the verbatim sentence occurs {count} times, not once")
    block = sections(text).get("Not claimed", "")
    fences = FENCE.findall(block)
    if len(fences) != 1:
        found.append(f"the section 'Not claimed' has {len(fences)} fenced blocks, not one")
    elif fold(NOT_CLAIMED_SENTENCE) not in fold(fences[0]):
        found.append("the verbatim sentence is not inside the fenced block of 'Not claimed'")
    elif fold(fences[0]).strip() != fold(NOT_CLAIMED_SENTENCE):
        found.append("the fenced block holds more than the verbatim sentence")
    if len(FENCE.findall(text)) != len(fences):
        found.append("a fenced block outside the section 'Not claimed'")
    return found


# ------------------------------------------------------------------------------ the real page


def _real() -> str:
    assert PAGE.is_file(), f"{PAGE.relative_to(ROOT)} does not exist"
    text: str = PAGE.read_text("utf-8")
    return text


def test_concept_page_states_no_accuracy_claim() -> None:
    """ADR 0023 rule 1 and I1: the deny-listed words only in the one verbatim sentence, inside
    the one fenced block of 'Not claimed'; the value is labelled 'agreement distance (MER)'."""
    text = _real()
    assert deny_problems(text) == []
    assert "agreement distance (MER)" in text
    assert "Match Error Rate" not in text


def test_concept_page_has_fixed_headings_and_required_statements() -> None:
    text = _real()
    lines = text.splitlines()
    assert lines[0] == TITLE
    assert h2_headings(text) == HEADINGS
    assert [r.key for r in unmet(sections(text), REQUIREMENTS)] == []


def test_concept_page_cites_only_existing_rows_tests_and_links() -> None:
    problems = page_problems(_real(), backlog_ids(), defined_tests(), CONCEPTS)
    assert problems == []


def test_concept_page_is_linked_from_the_concepts_index() -> None:
    index = INDEX.read_text("utf-8")
    assert re.search(
        r"^\| \[recorded-call-scoring\.md\]\(recorded-call-scoring\.md\) \| \S", index, re.M
    )


def test_the_requirement_table_is_well_formed() -> None:
    keys = [r.key for r in REQUIREMENTS]
    assert len(keys) == len(set(keys))
    assert {r.section for r in REQUIREMENTS} <= set(HEADINGS)
    for r in REQUIREMENTS:
        for alt in r.alternatives:
            for pattern in alt:
                re.compile(pattern)
    assert {"BL-165", "BL-169", "BL-170", "BL-192", "BL-234"} == REQUIRED_BL


# ------------------------------------------------------------------------------ teeth

SENTENCES: dict[str, dict[str, str]] = {
    M: {
        "agreement_definition": (
            "Tracer measures the agreement between the agent's transcript export of a call and "
            "an independent speech-to-text transcript of the same audio."
        ),
        "mer_label": "The value is labelled agreement distance (MER).",
        "mer_citation": "It is the measure of Morris, Maier and Green (2004).",
        "counts_with_denominators": "Every number is printed with its counts and denominator.",
        "threshold_has_no_default": "The flag_min_confidence parameter has no default.",
        "confidence_uncalibrated": "The confidence of a flag is uncalibrated.",
        "confidence_not_a_probability": "The confidence is not a probability.",
    },
    N: {
        "a_flag_is_not_an_error": "A flag is not a claim that the export holds an error.",
        "unflagged_is_not_vouched_for": "An unflagged span is not shown to be right.",
        "export_is_not_a_reference": "The export is not a reference or ground truth.",
        "two_clips_are_a_demonstration": "Two clips are a demonstration (BL-170).",
    },
    S: {
        "marathi_only": "Only Marathi (mr, Devanagari) is in scope.",
        "declared_language": "The language is declared by you with --declared-language.",
        "caller_never_evaluated": "The caller is never evaluated.",
        "caller_lines_are_agent_output": (
            "The caller lines of the export are the agent's own recognition of the caller."
        ),
        "stt_turns_have_role_unknown": "The speech-to-text turns have role unknown.",
        "flags_are_labelled_with_the_role": "The report labels each flag with a role.",
        "tier_t0": "The run is at tier T0.",
    },
    C: {
        "cost_not_available": "Without a USD price the cost is not_available.",
        "cost_never_zero": "A missing cost is never 0.",
    },
    P: {
        "audio_leaves_only_with_stt_sarvam": ("The audio goes to Sarvam only with --stt sarvam."),
        "replay_sends_nothing": "With fake: the replay sends nothing.",
        "report_and_store_hold_text": (
            "The report and the store hold call text and stay out of git."
        ),
        "call_log_holds_no_text": "The call log holds no text.",
        "out_is_outside_the_repository": "Put --out outside the repository.",
        "consent_record": "The consent record is given with --consent-reference.",
        "retention_recorded_not_enforced": ("Retention is recorded and not enforced (BL-234)."),
        "embedding_audio_is_opt_in": "Embedding the audio with --embed-audio is opt-in.",
    },
    R: {
        "run_book_pointer": "Follow the Run book for a real send of ADR 0024.",
        "dry_run_first": "Do a dry run first.",
        "max_chunks_ceiling": "The --max-chunks ceiling refuses a larger plan.",
        "offline_quick_start_pointer": "See packages/cli/README.md for the offline quick start.",
    },
    K: {
        "number_words_against_digits": "Number words against digits are not folded (BL-192).",
        "two_letter_threshold": "A two-letter word flags exactly at the threshold.",
        "split_word_below_threshold": "A split word stays below the threshold.",
        "noise_floor": "The noise floor is unmeasured until P3-31.",
        "native_review_missing": "No native review has happened (BL-169).",
    },
}
FENCED = "```text\n" + NOT_CLAIMED_SENTENCE + "\n```"
RELATED = "\n".join(f"- [link]({t})" for t in REQUIRED_LINKS)
EXTRA: dict[str, str] = {
    M: "The scale follows BL-165.\n",
    N: "The tests are `test_concept_page_states_no_accuracy_claim`.\n\n" + FENCED + "\n",
    K: "A native review is open; the formats follow BL-192 and BL-234.\n",
    "Related": RELATED + "\n",
}


def build_page(
    drop: str | None = None, extra: dict[str, str] | None = None, skip_fence: bool = False
) -> str:
    parts = [TITLE, ""]
    for heading in HEADINGS:
        parts += [f"## {heading}", ""]
        for key, sentence in SENTENCES.get(heading, {}).items():
            if key != drop:
                parts += [f"- {sentence}"]
        tail = (extra or {}).get(heading, EXTRA.get(heading, ""))
        if skip_fence and heading == N:
            tail = tail.replace(FENCED, "")
        parts += ["", tail]
    return "\n".join(parts) + "\n"


GOOD_BACKLOG = {"BL-165", "BL-169", "BL-170", "BL-192", "BL-234"}
GOOD_TESTS = {"test_concept_page_states_no_accuracy_claim"}


def check(text: str) -> list[str]:
    return page_problems(text, GOOD_BACKLOG, GOOD_TESTS, CONCEPTS)


def test_the_checker_accepts_a_good_page() -> None:
    assert check(build_page()) == []


@pytest.mark.parametrize("key", [r.key for r in REQUIREMENTS])
def test_the_checker_reports_each_missing_statement(key: str) -> None:
    # every key but the noise floor has exactly one sentence in the good page
    problems = check(build_page(drop=key))
    assert f"missing statement: {key}" in problems, problems


@pytest.mark.parametrize(
    "word",
    [
        "accuracy",
        "WER",
        "CER",
        "error rate",
        "Error  Rate",
        "error-rate",
        "correct",
        "Recall",
        "PRECISION",
        "\uff41ccuracy",  # a full-width letter
        "acc\u200buracy",  # a zero-width space
        "error\nrate",
    ],
)
def test_the_checker_refuses_a_deny_listed_word_outside_the_sentence(word: str) -> None:
    for heading in (M, K):
        text = build_page(extra={heading: f"Extra line about the {word} of it.\n"})
        assert any("deny-listed" in p for p in check(text)), (heading, word)


def test_the_checker_refuses_a_deny_listed_word_in_the_fence_beside_the_sentence() -> None:
    bad = FENCED.replace("\n```", "\nNo recall is claimed.\n```")
    text = build_page(extra={N: bad})
    problems = check(text)
    assert any("deny-listed" in p for p in problems), problems
    assert any("more than the verbatim sentence" in p for p in problems), problems


def test_the_checker_refuses_the_expansion_of_the_abbreviation() -> None:
    text = build_page(extra={M: "MER is the Match Error Rate.\n"})
    assert any("deny-listed" in p for p in check(text))


def test_the_checker_refuses_a_missing_or_repeated_or_misplaced_sentence() -> None:
    assert any("occurs 0 times" in p for p in check(build_page(skip_fence=True)))
    twice = build_page(extra={N: FENCED + "\n\n" + NOT_CLAIMED_SENTENCE + "\n"})
    assert any("occurs 2 times" in p for p in check(twice))
    outside = build_page(skip_fence=True, extra={M: NOT_CLAIMED_SENTENCE + "\n"})
    problems = check(outside)
    assert any("has 0 fenced blocks" in p for p in problems), problems
    paraphrase = FENCED.replace("agree;", "agree,")
    assert any(
        "deny-listed" in p or "occurs 0" in p for p in check(build_page(extra={N: paraphrase}))
    )


def test_the_checker_refuses_a_fence_outside_not_claimed() -> None:
    text = build_page(extra={K: "```text\nsome output\n```\n"})
    assert any("fenced block outside" in p for p in check(text))


def test_the_checker_refuses_wrong_headings_and_title() -> None:
    text = build_page().replace("## Cost", "## Costs")
    assert any("second-level headings" in p for p in check(text))
    swapped = build_page().replace(TITLE, "# Recorded scoring")
    assert any("first line" in p for p in check(swapped))
    extra = build_page() + "\n## Extra\n\ntext\n"
    assert any("second-level headings" in p for p in check(extra))


def test_the_checker_refuses_unknown_backlog_rows_tests_and_links() -> None:
    assert any("BL-999" in p for p in check(build_page(extra={M: "See BL-999.\n"})))
    assert any("does not cite BL-234" in p for p in check(build_page().replace("BL-234", "BL-165")))
    assert any("test_nope" in p for p in check(build_page(extra={M: "Named `test_nope`.\n"})))
    assert any(
        "does not name" in p
        for p in check(build_page().replace("`test_concept_page_states_no_accuracy_claim`", ""))
    )
    broken = build_page().replace("../examples/synthetic-mr/README.md", "../examples/none.md")
    problems = check(broken)
    assert any("does not resolve" in p for p in problems)
    assert any(
        "does not link ../examples/synthetic-mr/README.md in 'Related'" in p for p in problems
    )


def test_the_checker_refuses_invisible_characters() -> None:
    assert any(
        "format or control" in p
        for p in check(build_page(extra={M: "Zero\u200bwidth in the open.\n"}))
    )


def test_the_noise_floor_may_point_at_a_measurement_report_instead() -> None:
    swapped = build_page().replace(
        "The noise floor is unmeasured until P3-31.",
        "The noise floor is in docs/phase3/measurements/ for the two clips.",
    )
    assert check(swapped) == []
    vague = build_page().replace(
        "The noise floor is unmeasured until P3-31.", "The noise floor is small."
    )
    assert "missing statement: noise_floor" in check(vague)


def test_a_statement_must_sit_in_its_own_section() -> None:
    moved = build_page(drop="tier_t0", extra={C: "The run is at tier T0.\n"})
    assert "missing statement: tier_t0" in check(moved)


def test_a_wrapped_verbatim_sentence_is_still_the_sentence() -> None:
    words = NOT_CLAIMED_SENTENCE.split(" ")
    wrapped = "```text\n" + "\n".join(" ".join(words[i : i + 6]) for i in range(0, len(words), 6))
    prose = "The tests are `test_concept_page_states_no_accuracy_claim`.\n\n"
    text = build_page(extra={N: prose + wrapped + "\n```\n"})
    assert check(text) == []


def test_a_statement_inside_a_fence_does_not_count() -> None:
    hidden = build_page(drop="tier_t0", extra={S: "```text\nThe run is at tier T0.\n```\n"})
    problems = check(hidden)
    assert "missing statement: tier_t0" in problems
    assert any("fenced block outside" in p for p in problems)


def test_a_statement_must_fit_one_sentence() -> None:
    split = build_page(
        drop="confidence_uncalibrated", extra={M: "The confidence is shown. It is uncalibrated.\n"}
    )
    assert "missing statement: confidence_uncalibrated" in check(split)
    joined = build_page(
        drop="confidence_uncalibrated",
        extra={M: "The confidence is shown, and it is uncalibrated.\n"},
    )
    assert "missing statement: confidence_uncalibrated" not in check(joined)


def test_a_statement_must_fit_one_bullet() -> None:
    bullets = "- The caller turns are counted\n- A flag is never scored\n"
    problems = check(build_page(drop="caller_never_evaluated", extra={S: bullets}))
    assert "missing statement: caller_never_evaluated" in problems


def test_a_heading_inside_a_fence_is_not_a_heading() -> None:
    text = "## A\n\n```text\n## B\n```\n\n## C\n"
    assert h2_headings(text) == ["A", "C"]
    assert list(sections(text)) == ["A", "C"]
