# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the P1-17 language-fixture tests (``test_language_fixtures.py``). No tests.

The fixtures live in ``tests/fixtures/languages``. This module loads them, looks at their text with
``unicodedata`` only (no network), builds the models the tests feed and holds the T-DRIFT matcher
used by the fixture-hygiene tests. The file is ASCII only. Anything the T-DRIFT matcher bans
(ADR 0005 Decision 10) is assembled from parts, so this file never spells it.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, cast

from ._support import FIXTURES, api, data_json
from ._support_b2 import latency_definition, make
from ._support_b3 import make3

LANGUAGES_DIR = FIXTURES / "languages"
NEGATIVES_DIR = LANGUAGES_DIR / "negatives"

NINE: tuple[str, ...] = ("en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu")
CROSS_CUTTING_FILE = "cross_cutting.json"
NEGATIVES_FILE = "negatives.json"
LANGUAGE_FILES: tuple[str, ...] = tuple(f"{code}.json" for code in NINE)

Case = dict[str, Any]

CASE_FIELDS: tuple[str, ...] = (
    "id",
    "language",
    "script",
    "kind",
    "category",
    "text",
    "purpose",
    "expected",
    "needs_native_review",
)
KINDS: tuple[str, ...] = ("text", "tag", "label")
OUTCOMES: tuple[str, ...] = ("preserved", "rejected", "flagged")

# Precomposed letters that NFC rewrites (composition exclusions, UAX #15): Devanagari QA..YYA and
# Gurmukhi LLA, SHA, KHHA, GHHA, ZA, FA.
COMPOSITION_EXCLUDED: frozenset[int] = frozenset(
    [*range(0x0958, 0x0960), 0x0A33, 0x0A36, 0x0A59, 0x0A5A, 0x0A5B, 0x0A5E]
)

ZWJ = chr(0x200D)
ZWNJ = chr(0x200C)
FSI = chr(0x2068)
PDI = chr(0x2069)

_SCRIPT_BY_NAME_PREFIX: dict[str, str] = {
    "DEVANAGARI": "Deva",
    "TELUGU": "Telu",
    "TAMIL": "Taml",
    "KANNADA": "Knda",
    "MALAYALAM": "Mlym",
    "GURMUKHI": "Guru",
    "GUJARATI": "Gujr",
    "LATIN": "Latn",
    "ARABIC": "Arab",
}


# ---------------------------------------------------------------------------------- loading


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(path: Path) -> list[Case]:
    """The ``cases`` list of one fixture file; an unreadable file gives ``[]`` (the file test
    reports it), so a broken fixture never breaks collection of the whole module."""
    try:
        cases: list[Case] = list(read_json(path)["cases"])
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return cases


def positive_cases() -> list[Case]:
    """Cases of the nine language files and the cross-cutting file, in file order."""
    found: list[Case] = []
    for name in (*LANGUAGE_FILES, CROSS_CUTTING_FILE):
        found.extend(load_cases(LANGUAGES_DIR / name))
    return found


def negative_cases() -> list[Case]:
    return load_cases(NEGATIVES_DIR / NEGATIVES_FILE)


def all_cases() -> list[Case]:
    return [*positive_cases(), *negative_cases()]


def fixture_files() -> list[Path]:
    """Every file under the languages directory, negatives directory included."""
    return sorted(path for path in LANGUAGES_DIR.rglob("*") if path.is_file())


# ---------------------------------------------------------------------------------- text facts


def code_point_label(char: str) -> str:
    return f"U+{ord(char):04X}"


def code_points(text: str) -> list[str]:
    return [code_point_label(char) for char in text]


NormalizationForm = Literal["NFC", "NFD", "NFKC", "NFKD"]


def nf(form: str, text: str) -> str:
    """Normalize ``text``; ``form`` is NFC, NFD, NFKC or NFKD (unicodedata raises otherwise)."""
    return unicodedata.normalize(cast(NormalizationForm, form), text)


def scripts_in(text: str) -> set[str]:
    """ISO 15924 codes of the letters and marks of ``text``. Digits, punctuation, spaces, joiners
    and combining accents (which inherit the script of their base) are ignored. A letter of a
    script this helper does not know shows up as ``?<NAME>``."""
    found: set[str] = set()
    for char in text:
        if unicodedata.category(char)[0] not in ("L", "M"):
            continue
        prefix = unicodedata.name(char, "UNKNOWN").split(" ", 1)[0]
        if prefix in ("COMBINING", "VARIATION"):
            # Combining accents and variation selectors inherit the script of their base.
            continue
        found.add(_SCRIPT_BY_NAME_PREFIX.get(prefix, "?" + prefix))
    return found


def declared_scripts(case: Case) -> set[str]:
    return set(str(case["script"]).split("+"))


def native_digit_range(language: str) -> tuple[int, int] | None:
    """The decimal digit run of a language from ``languages.json`` (``None`` for English)."""
    for row in data_json("languages.json")["languages"]:
        if row["language"] == language:
            digits = row["native_digits"]
            if not digits.get("exists"):
                return None
            low, high = str(digits["digit_range"]).split("..")
            return int(low.removeprefix("U+"), 16), int(high.removeprefix("U+"), 16)
    return None


def digit_forms(text: str, language: str) -> set[str]:
    """``ascii``, ``native`` (the language's own block) and ``other`` decimal digits in ``text``."""
    own = native_digit_range(language)
    forms: set[str] = set()
    for char in text:
        if unicodedata.category(char) != "Nd":
            continue
        point = ord(char)
        if 0x30 <= point <= 0x39:
            forms.add("ascii")
        elif own is not None and own[0] <= point <= own[1]:
            forms.add("native")
        else:
            forms.add("other")
    return forms


def invisible_counts(text: str, wanted: list[str]) -> dict[str, int]:
    counts = Counter(code_point_label(char) for char in text)
    return {label: counts.get(label, 0) for label in wanted}


def expected_form(claim: str, text: str) -> str:
    """A ``normalization`` claim: ``=`` means "equal to the case text"."""
    return text if claim == "=" else claim


def json_plain(text: str) -> bytes | None:
    """The UTF-8 bytes ``text`` has inside a JSON document written without ``\\u`` escapes, or
    ``None`` when JSON would escape part of it (control characters, quote, backslash)."""
    if any(ord(char) < 0x20 or char in ('"', "\\") for char in text):
        return None
    return text.encode("utf-8")


def facts_for(case: Case) -> dict[str, Any]:
    """Normalization facts of one case, computed with ``unicodedata`` only."""
    text = str(case["text"])
    facts: dict[str, Any] = {
        "id": case["id"],
        "code_points": len(text),
        "unidata_version": unicodedata.unidata_version,
    }
    for form in ("NFC", "NFD", "NFKC", "NFKD"):
        result = nf(form, text)
        facts[f"{form.lower()}_changes_text"] = result != text
        facts[f"{form.lower()}_length"] = len(result)
    facts["joiners"] = {"U+200D": text.count(ZWJ), "U+200C": text.count(ZWNJ)}
    return facts


# ----------------------------------------------------------------------------- the T-DRIFT matcher


def _join(*parts: str) -> str:
    return "".join(parts)


# Assembled from parts: this file must not spell what the matcher looks for (ADR 0005 Decision 10).
_EXCLUDED_SUBTAGS = _join("(?:b", "n|u", "r|o", "r|a", "s)")
_BANNED_NAMES = "|".join(
    (
        _join("ben", "gali"),
        _join("u", "rdu"),
        _join("od", "ia"),
        _join("ori", "ya"),
        _join("assam", "ese"),
        _join("shah", "mukhi"),
        _join("hing", "lish"),
    )
)
_MIXED_WORDS = _join("code", "[-_ ]?", "mix")
_MIXED_ALIAS = _join("hi", "[-_]", "en", r"\b")
_SUBTAG_IN_TAG_SYNTAX = _join(r"\b", _EXCLUDED_SUBTAGS, r"[-_][A-Za-z0-9]{2,8}\b")
_SUBTAG_QUOTED = _join("[\"']", _EXCLUDED_SUBTAGS, "[\"']")
_EXCLUDED_SCRIPT_TAG = _join(r"\bpa", "[-_]", "Ar", r"ab\b")

TDRIFT_PATTERN = re.compile(
    "|".join(
        (
            _BANNED_NAMES,
            _MIXED_WORDS,
            _MIXED_ALIAS,
            _SUBTAG_IN_TAG_SYNTAX,
            _SUBTAG_QUOTED,
            _EXCLUDED_SCRIPT_TAG,
        )
    ),
    re.IGNORECASE,
)
"""An approximation of the T-DRIFT matcher (ADR 0005 Decision 10): excluded language names, the
mixed-language words, the alias, and excluded subtags only in tag syntax (a quoted string or
``xx-YY``/``xx_YY``), because two of the subtags are ordinary English words."""


def tdrift_hits(text: str) -> list[str]:
    """Every text the matcher finds in ``text`` (empty when the text is clean)."""
    return [match.group(0) for match in TDRIFT_PATTERN.finditer(text)]


def iter_json_strings(node: Any) -> Iterator[str]:
    """Every string key and value of a decoded JSON document."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from iter_json_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_json_strings(item)


# ---------------------------------------------------------------------------------- model builders


@functools.cache
def script_sensitive_definition() -> Any:
    """A script-sensitive metric that covers all nine configured languages (as the existing
    ``test_wer_not_applicable_for_observed_romanized`` builds it)."""
    rows = data_json("languages.json")["languages"]
    return make(
        "MetricDefinition",
        **latency_definition(
            key="custom_error_rate",
            kind="deterministic",
            unit="cer",
            requires=["transcript"],
            languages=[row["language"] for row in rows],
            script_sensitive=True,
            pass_rule={"kind": "range", "max": 0.25, "inclusive": True},
            implementation_ref="builtin:cer",
        ),
    )


def metric_flag(tag: str) -> str | None:
    """The ``not_applicable`` reason of ``script_sensitive_definition()`` on one observed turn
    tagged ``tag`` (``None``: the metric applies)."""
    transcript = make3("Transcript", source="our_stt")
    turn = make3("Turn", transcript_id=str(transcript.id), seq=0, language=tag)
    outcome = api("results").check_language(script_sensitive_definition(), [turn], [transcript])
    reason: str | None = outcome.na_reason
    return reason
