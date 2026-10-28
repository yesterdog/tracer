# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared checker of the Phase 4 concept pages (P4-37 and P4-J30): ``simulated-calls.md`` and
``llm-judge.md``.

Both pages have the same shape as ``recorded-call-scoring.md`` (its checker is
``test_concept_page_recorded_scoring``): fixed title and second-level headings, one fenced block in
the section "Not claimed" that holds one fixed sentence exactly once, no claim word anywhere else,
required statements per section, existing cites and links. They add three rules of the batch:

* no private path and no secret-shaped string anywhere on the page;
* only the nine supported language codes (``en hi mr te ta kn ml pa gu``) appear as language codes;
* the claim words are the union of ADR 0023 rule 1, amendment A1 (simulated runs) and amendment A4
  (judge outputs), matched case-insensitively as whole words on a folded copy of the text.

Nothing here imports a Tracer package: the checks read Markdown files, ``docs/BACKLOG.md`` and the
AST of ``packages/*/tests`` only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from evalcore_batchh_support import (  # type: ignore[import-not-found,unused-ignore]
    BL_ID,
    CONCEPTS,
    FENCE,
    LINK,
    ROOT,
    TEST_NAME,
    Requirement,
    fold,
    h2_headings,
    sections,
    unmet,
)

__all__ = [
    "ALLOWED_LANGUAGES",
    "CLAIM_WORDS",
    "JUDGE_SENTENCE",
    "SIM_SENTENCE",
    "PageSpec",
    "drop_requirement",
    "page_problems",
    "replace_sentence",
]

#: ADR 0023 amendment A1, verbatim (the one sentence of the simulated-calls page).
SIM_SENTENCE = (
    "Tracer reports what it observed at the simulator over one wire dialect; it does not measure "
    "accuracy, the agent's latency or quality, task success, persona realism or determinism, and "
    "none of its numbers is a WER, a CER, an error rate, a recall or a precision."
)

#: ADR 0023 amendment A4 and ADR 0030 rule 9, verbatim (the one sentence of the judge page).
JUDGE_SENTENCE = (
    "Tracer's judge is an uncalibrated language-model signal: it reports whether a model said "
    "that an agent's answer agrees with the expected facts; it does not measure accuracy, "
    "correctness, factuality or quality, it is not a pass or fail of the agent, and none of its "
    "numbers is a confidence, a probability, an error rate, a recall or a precision."
)

_RULE_1 = ("accuracy", "wer", "cer", r"error[\s_-]+rate", "correct", "recall", "precision")
_A1 = (
    "latency",
    r"response\s+time",
    "quality",
    r"task\s+success",
    "realism",
    "realistic",
    "fidelity",
    "deterministic",
    "determinism",
)
_A4 = (
    "correct",
    "incorrect",
    "correctness",
    "factual",
    "factuality",
    "truthful",
    "hallucination",
    r"ground\s+truth",
    "calibrated",  # whole word only: "uncalibrated" is allowed and required
    "probability",
    "confidence",
    "pass",
    "fail",
    "passed",
    "failed",
    r"score\s+of\s+the\s+agent",
)

#: The union of ADR 0023 rule 1, A1 and A4, whole words, case-insensitive.
CLAIM_WORDS = re.compile(r"\b(?:" + "|".join(dict.fromkeys((*_RULE_1, *_A1, *_A4))) + r")\b", re.I)

ALLOWED_LANGUAGES = frozenset({"en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu"})

#: A language code on a page: a BCP-47 style tag (``xx-YY``, ``xx-Xxxx``) or a code span of two
#: lower-case letters. The primary subtag must be one of the nine.
_TAG = re.compile(r"\b([a-z]{2,3})-(?:[A-Z]{2}|[A-Z][a-z]{3}|\d{3})\b")
_CODE_SPAN = re.compile(r"`([a-z]{2})`")
#: Two-letter code spans that are names of packages, not language codes (the wheel ``av`` that
#: the optional extra ``webrtc`` installs). Every other two-letter span must be one of the nine.
NOT_LANGUAGE_SPANS = frozenset({"av"})

PRIVATE_PATH = re.compile(
    r"/Users/|/home/|/root/|/tmp/|/var/|/private/|(?<![\w.])~/|\\Users\\|[A-Z]:\\"
    r"|tracer-runs|github-purge|\.env\.local|fixtures/private|Desktop|pytest-of-|claude-\d{3}"
)

SECRET_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\b[0-9a-f]{40,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|subscription[_-]?key)\s*[:=]\s*['\"]?"
        r"[A-Za-z0-9._-]{12,}"
    ),
)


@dataclass(frozen=True)
class PageSpec:
    """Everything a page must satisfy."""

    path: Path
    title: str
    headings: tuple[str, ...]
    sentence: str
    requirements: tuple[Requirement, ...]
    required_links: tuple[str, ...]
    required_bl: frozenset[str]
    required_tests: tuple[str, ...]
    index_name: str


def _hidden(char: str) -> bool:
    return unicodedata.category(char) in ("Cf", "Cc") and char not in "\n\t"


def sentence_problems(text: str, sentence: str) -> list[str]:
    """The sentence rule alone: exactly once, alone in the one fenced block of 'Not claimed', no
    other fenced block on the page."""
    found: list[str] = []
    folded = fold(text)
    wanted = fold(sentence)
    count = folded.count(wanted)
    if count != 1:
        found.append(f"the fixed sentence occurs {count} times, not once")
    block = sections(text).get("Not claimed", "")
    fences = FENCE.findall(block)
    if len(fences) != 1:
        found.append(f"the section 'Not claimed' has {len(fences)} fenced blocks, not one")
    elif wanted not in fold(fences[0]):
        found.append("the fixed sentence is not inside the fenced block of 'Not claimed'")
    elif fold(fences[0]).strip() != wanted:
        found.append("the fenced block holds more than the fixed sentence")
    if len(FENCE.findall(text)) != len(fences):
        found.append("a fenced block outside the section 'Not claimed'")
    return found


def claim_word_problems(text: str, sentence: str) -> list[str]:
    folded = fold(text)
    rest = folded.replace(fold(sentence), " ")
    hits = sorted({m.group(0).lower() for m in CLAIM_WORDS.finditer(rest)})
    return [f"claim words outside the fixed sentence: {hits}"] if hits else []


def language_problems(text: str) -> list[str]:
    folded = fold(text)
    tags = [m.group(1) for m in _TAG.finditer(folded)]
    spans = [m.group(1) for m in _CODE_SPAN.finditer(text) if m.group(1) not in NOT_LANGUAGE_SPANS]
    bad = sorted({code for code in (*tags, *spans) if code not in ALLOWED_LANGUAGES})
    return [f"language codes outside the nine supported ones: {bad}"] if bad else []


def private_and_secret_problems(text: str) -> list[str]:
    found: list[str] = []
    folded = fold(text)
    if PRIVATE_PATH.search(folded):
        found.append("a private or machine-specific path")
    if any(shape.search(folded) for shape in SECRET_SHAPES):
        found.append("a secret-shaped string")
    return found


def page_problems(
    text: str, spec: PageSpec, backlog: set[str], tests: set[str], base: Path = CONCEPTS
) -> list[str]:
    """Every way ``text`` breaks the rules of ``spec``; empty when the page holds."""
    found: list[str] = []
    found += sentence_problems(text, spec.sentence)
    found += claim_word_problems(text, spec.sentence)
    lines = text.splitlines()
    if not lines or lines[0] != spec.title:
        found.append(f"the first line is not {spec.title!r}")
    if h2_headings(text) != list(spec.headings):
        found.append(
            f"the second-level headings are {h2_headings(text)}, not {list(spec.headings)}"
        )
    body = sections(text)
    found += [f"missing statement: {r.key}" for r in unmet(body, spec.requirements)]
    cited = set(BL_ID.findall(text))
    found += [f"cites a BACKLOG row that does not exist: {b}" for b in sorted(cited - backlog)]
    found += [f"does not cite {b}" for b in sorted(spec.required_bl - cited)]
    named = set(TEST_NAME.findall(text))
    found += [f"names a test that does not exist: {t}" for t in sorted(named - tests)]
    found += [f"does not name {t}" for t in spec.required_tests if t not in named]
    targets = [t for t in LINK.findall(text) if not re.match(r"[a-z]+:", t)]
    related = LINK.findall(body.get("Related", ""))
    for target in targets:
        if not (base / target.split("#", 1)[0]).resolve().exists():
            found.append(f"a link does not resolve: {target}")
    found += [f"does not link {t} in 'Related'" for t in spec.required_links if t not in related]
    found += language_problems(text)
    found += private_and_secret_problems(text)
    if any(_hidden(c) for c in text):
        found.append("holds a format or control character")
    return found


def index_problems(index_text: str, page_name: str) -> list[str]:
    pattern = rf"^\| \[{re.escape(page_name)}\]\({re.escape(page_name)}\) \| \S"
    if re.search(pattern, index_text, re.M):
        return []
    return [f"docs/concepts/README.md has no row for {page_name}"]


# ------------------------------------------------------------------------------ mutation helpers


def drop_requirement(text: str, requirement: Requirement) -> str:
    """``text`` without every line of the requirement's section that holds a sentence which
    satisfies it (the checker must then report the statement as missing)."""
    from evalcore_batchh_support import (  # type: ignore[import-not-found,unused-ignore]
        unit_satisfies,
        units,
    )

    body = sections(text).get(requirement.section, "")
    kept: list[str] = []
    for line in body.splitlines(keepends=True):
        if any(unit_satisfies(u, requirement.alternatives) for u in units(line)):
            continue
        kept.append(line)
    return text.replace(body, "".join(kept), 1)


def replace_sentence(text: str, sentence: str, new: str) -> str:
    """``text`` with the fixed sentence replaced by ``new`` (also when it is wrapped)."""
    words = [re.escape(w) for w in sentence.split(" ")]
    return re.sub(r"\s+".join(words), lambda _m: new, text, count=1)


_ = ROOT  # re-exported for the test modules
