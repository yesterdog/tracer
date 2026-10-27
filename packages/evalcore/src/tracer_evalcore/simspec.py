# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The input files of ``tracer simulate`` that describe the test: evaluator, persona, test profile
and target (P4-23; docs/phase4/api-surface-batch-e.md section 4).

Each is a small JSON document with a ``format`` member, a closed set of keys and no default for
any value (SCOPE: every value the user states, Tracer supplies none). The command seals the real
schema records from them; Tracer does not ask the user to write a content digest. A refusal is a
``SimSpecError`` whose message is its stable code (``<kind>_<problem>``) and never a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "SIM_EVALUATOR_FORMAT",
    "SIM_PERSONA_FORMAT",
    "SIM_TARGET_FORMAT",
    "SIM_TEST_PROFILE_FORMAT",
    "ScriptedTurnSpec",
    "SimEvaluatorSpec",
    "SimPersonaSpec",
    "SimSpecError",
    "SimTargetSpec",
    "SimTestProfileSpec",
    "parse_sim_evaluator",
    "parse_sim_persona",
    "parse_sim_target",
    "parse_sim_test_profile",
    "resolve_profile_text",
]

SIM_EVALUATOR_FORMAT: Final = "tracer.sim_evaluator/1"
SIM_PERSONA_FORMAT: Final = "tracer.sim_persona/1"
SIM_TEST_PROFILE_FORMAT: Final = "tracer.sim_test_profile/1"
SIM_TARGET_FORMAT: Final = "tracer.sim_target/1"
_MAX_SAFE: Final = 9007199254740991


class SimSpecError(InputRefusedError):
    """A spec file was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ScriptedTurnSpec:
    index: int
    text: str
    language: str


@dataclass(frozen=True)
class SimEvaluatorSpec:
    name: str
    language: str
    instructions: str
    expected_outcome: str
    scripted_turns: tuple[ScriptedTurnSpec, ...]
    max_duration_ms: int


@dataclass(frozen=True)
class SimPersonaSpec:
    key: str
    language: str
    accent: str | None


@dataclass(frozen=True)
class SimTestProfileSpec:
    name: str
    dob: str
    account_number: str


@dataclass(frozen=True)
class SimTargetSpec:
    name: str
    url: str


def _read(kind: str, text: str, fmt: str, keys: frozenset[str]) -> Mapping[str, Any]:
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError):
        raise SimSpecError(f"{kind}_not_json") from None
    if not isinstance(document, Mapping):
        raise SimSpecError(f"{kind}_shape")
    if document.get("format") != fmt:
        raise SimSpecError(f"{kind}_format")
    for key in sorted(keys):
        if key not in document:
            raise SimSpecError(f"{kind}_missing_{key}")
    if set(document) - keys:
        raise SimSpecError(f"{kind}_unknown_key")
    return document


def _text(kind: str, document: Mapping[str, Any], key: str, *, limit: int = 100_000) -> str:
    value = document[key]
    if type(value) is not str or value == "" or len(value) > limit:
        raise SimSpecError(f"{kind}_invalid_{key}")
    return value


def parse_sim_evaluator(text: str) -> SimEvaluatorSpec:
    kind = "evaluator"
    keys = frozenset(
        {
            "format",
            "name",
            "language",
            "instructions",
            "expected_outcome",
            "scripted_turns",
            "max_duration_ms",
        }
    )
    document = _read(kind, text, SIM_EVALUATOR_FORMAT, keys)
    turns_raw = document["scripted_turns"]
    if type(turns_raw) is not list:
        raise SimSpecError(f"{kind}_invalid_scripted_turns")
    turns: list[ScriptedTurnSpec] = []
    for position, item in enumerate(turns_raw):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"index", "text", "language"}
            or type(item["index"]) is not int
            or item["index"] != position
            or type(item["text"]) is not str
            or item["text"] == ""
            or type(item["language"]) is not str
        ):
            raise SimSpecError(f"{kind}_invalid_scripted_turns")
        turns.append(ScriptedTurnSpec(item["index"], item["text"], item["language"]))
    duration = document["max_duration_ms"]
    if type(duration) is not int or not 1 <= duration <= 14_400_000:
        raise SimSpecError(f"{kind}_invalid_max_duration_ms")
    return SimEvaluatorSpec(
        name=_text(kind, document, "name", limit=200),
        language=_text(kind, document, "language", limit=35),
        instructions=_text(kind, document, "instructions"),
        expected_outcome=_text(kind, document, "expected_outcome"),
        scripted_turns=tuple(turns),
        max_duration_ms=duration,
    )


def parse_sim_persona(text: str) -> SimPersonaSpec:
    kind = "persona"
    document = _read(
        kind, text, SIM_PERSONA_FORMAT, frozenset({"format", "key", "language", "accent"})
    )
    key = document["key"]
    if type(key) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None:
        raise SimSpecError(f"{kind}_invalid_key")
    accent = document["accent"]
    if accent is not None and (type(accent) is not str or accent == "" or len(accent) > 200):
        raise SimSpecError(f"{kind}_invalid_accent")
    return SimPersonaSpec(key, _text(kind, document, "language", limit=35), accent)


def parse_sim_test_profile(text: str) -> SimTestProfileSpec:
    kind = "test_profile"
    document = _read(
        kind,
        text,
        SIM_TEST_PROFILE_FORMAT,
        frozenset({"format", "name", "dob", "account_number"}),
    )
    return SimTestProfileSpec(
        _text(kind, document, "name", limit=200),
        _text(kind, document, "dob", limit=200),
        _text(kind, document, "account_number", limit=200),
    )


def parse_sim_target(text: str) -> SimTargetSpec:
    kind = "target"
    document = _read(kind, text, SIM_TARGET_FORMAT, frozenset({"format", "name", "url"}))
    return SimTargetSpec(
        _text(kind, document, "name", limit=200), _text(kind, document, "url", limit=2048)
    )


_PLACEHOLDER: Final = re.compile(r"\{\{profile\.([a-z][a-z0-9_]*)\}\}")


def resolve_profile_text(text: str, profile: SimTestProfileSpec | None) -> str:
    """The text with every ``{{profile.<field>}}`` replaced, once and never recursively."""
    fields = (
        {}
        if profile is None
        else {
            "name": profile.name,
            "dob": profile.dob,
            "account_number": profile.account_number,
        }
    )

    def resolve(match: re.Match[str]) -> str:
        if profile is None:
            raise SimSpecError("placeholder_without_profile")
        try:
            return fields[match.group(1)]
        except KeyError:
            raise SimSpecError("placeholder_unknown") from None

    return _PLACEHOLDER.sub(resolve, text)
