# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The output validator of the judge (ADR 0030 rule 7).

A reply is accepted only as exactly one JSON object with the closed keys ``verdict``,
``reason_code`` and ``facts``, closed values, the cross-field rules and at most 4,096 bytes. It is
never repaired. A refusal holds a stable code and never quotes the text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "CODE_VERDICT",
    "FACT_STATUSES",
    "JUDGE_OUTPUT_RESPONSE_SCHEMA",
    "JUDGE_VERDICTS",
    "MAX_JUDGE_OUTPUT_BYTES",
    "OUTPUT_REFUSAL_CODES",
    "REASON_CODES",
    "JudgeOutputInvalid",
    "JudgeVerdict",
    "allowed_reason_codes",
    "parse_judge_output",
]

JUDGE_VERDICTS: Final = ("agrees", "disagrees", "cannot_tell")
REASON_CODES: Final = (
    "all_facts_stated",
    "fact_contradicted",
    "fact_absent",
    "meaning_differs",
    "meaning_same",
    "answer_off_topic",
    "answer_unintelligible",
    "insufficient_information",
)
FACT_STATUSES: Final = ("stated", "contradicted", "absent", "cannot_tell")
MAX_JUDGE_OUTPUT_BYTES: Final = 4096
CODE_VERDICT: Final[Mapping[str, Mapping[str, str]]] = {
    "all_facts": {
        "all_facts_stated": "agrees",
        "fact_contradicted": "disagrees",
        "fact_absent": "disagrees",
        "answer_off_topic": "disagrees",
        "answer_unintelligible": "cannot_tell",
        "insufficient_information": "cannot_tell",
    },
    "equivalent_answer": {
        "meaning_same": "agrees",
        "meaning_differs": "disagrees",
        "answer_off_topic": "disagrees",
        "answer_unintelligible": "cannot_tell",
        "insufficient_information": "cannot_tell",
    },
}
OUTPUT_REFUSAL_CODES: Final = (
    "output_too_large",
    "output_not_json",
    "output_not_object",
    "output_keys",
    "output_verdict_value",
    "output_reason_code_value",
    "output_facts_shape",
    "output_fact_status_value",
    "output_facts_index",
    "output_facts_not_empty",
    "output_verdict_facts_inconsistent",
    "output_reason_verdict_inconsistent",
)
#: the schema sent as ``response_format`` where a profile's documentation shows it (strict mode
#: asks for every property required and no extra one; the bounds are the validator's)
JUDGE_OUTPUT_RESPONSE_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason_code", "facts"],
    "properties": {
        "verdict": {"type": "string", "enum": list(JUDGE_VERDICTS)},
        "reason_code": {"type": "string", "enum": list(REASON_CODES)},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "status"],
                "properties": {
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": list(FACT_STATUSES)},
                },
            },
        },
    },
}
_KEYS: Final = frozenset({"verdict", "reason_code", "facts"})


class JudgeOutputInvalid(ValueError):
    """A reply was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JudgeVerdict:
    verdict: str
    reason_code: str
    facts: tuple[tuple[int, str], ...]


def allowed_reason_codes(match_mode: object) -> tuple[str, ...]:
    if type(match_mode) is not str or match_mode not in CODE_VERDICT:
        raise ValueError("match_mode: not an accepted value")
    return tuple(CODE_VERDICT[match_mode])


def _expected_verdict(statuses: list[str]) -> str:
    if all(status == "stated" for status in statuses):
        return "agrees"
    if any(status in ("contradicted", "absent") for status in statuses):
        return "disagrees"
    return "cannot_tell"


def parse_judge_output(text: str, *, match_mode: object, fact_count: object) -> JudgeVerdict:
    """The verdict of ``text``; ``JudgeOutputInvalid`` on the first fault, never a repair."""
    codes = allowed_reason_codes(match_mode)
    if type(fact_count) is not int or fact_count < 0:
        raise ValueError("fact_count: not a non-negative integer")
    if (match_mode == "all_facts") != (fact_count >= 1):
        raise ValueError("fact_count: at least 1 for all_facts and 0 for equivalent_answer")
    if type(text) is not str:
        raise TypeError("text: not text")
    if len(text) > MAX_JUDGE_OUTPUT_BYTES or len(text.encode("utf-8")) > MAX_JUDGE_OUTPUT_BYTES:
        raise JudgeOutputInvalid("output_too_large")
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError, RecursionError):
        raise JudgeOutputInvalid("output_not_json") from None
    if not isinstance(document, Mapping):
        raise JudgeOutputInvalid("output_not_object")
    if set(document) != _KEYS:
        raise JudgeOutputInvalid("output_keys")
    verdict = document["verdict"]
    if type(verdict) is not str or verdict not in JUDGE_VERDICTS:
        raise JudgeOutputInvalid("output_verdict_value")
    code = document["reason_code"]
    if type(code) is not str or code not in codes:
        raise JudgeOutputInvalid("output_reason_code_value")
    listed = document["facts"]
    if not isinstance(listed, list):
        raise JudgeOutputInvalid("output_facts_shape")
    parsed: list[tuple[int, str]] = []
    for entry in listed:
        if (
            not isinstance(entry, Mapping)
            or set(entry) != {"index", "status"}
            or type(entry["index"]) is not int
            or type(entry["status"]) is not str
        ):
            raise JudgeOutputInvalid("output_facts_shape")
        parsed.append((entry["index"], entry["status"]))
    if any(status not in FACT_STATUSES for _, status in parsed):
        raise JudgeOutputInvalid("output_fact_status_value")
    if match_mode == "all_facts":
        if [index for index, _ in parsed] != list(range(fact_count)):
            raise JudgeOutputInvalid("output_facts_index")
        if verdict != _expected_verdict([status for _, status in parsed]):
            raise JudgeOutputInvalid("output_verdict_facts_inconsistent")
    elif parsed:
        raise JudgeOutputInvalid("output_facts_not_empty")
    if CODE_VERDICT[str(match_mode)][code] != verdict:
        raise JudgeOutputInvalid("output_reason_verdict_inconsistent")
    return JudgeVerdict(verdict, code, tuple(parsed))
