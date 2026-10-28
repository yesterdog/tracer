# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The scenario file of ``scripted_bot``: ``tracer.samplebot_scenario/1`` (strict JSON, closed keys,
no default for any value; a refusal is a stable code and never a value). Standard library only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

FORMAT = "tracer.samplebot_scenario/1"
AFTER_REPLIES = ("stay_silent", "hang_up")
__all__ = ["FORMAT", "Reply", "Scenario", "ScenarioError", "parse_scenario"]


class ScenarioError(ValueError):
    """``code`` is ``scenario_not_json``, ``scenario_shape`` or ``scenario_value``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Reply:
    delay_ms: int
    hz: int
    ms: int
    level: int


@dataclass(frozen=True)
class Scenario:
    end_of_caller_ms: int
    replies: tuple[Reply, ...]
    after_replies: str


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    raise ValueError(name)


def _int(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def parse_scenario(text: str) -> Scenario:
    try:
        document = json.loads(text, object_pairs_hook=_pairs, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        raise ScenarioError("scenario_not_json") from None
    keys = {"format", "end_of_caller_ms", "replies", "after_replies"}
    if (
        not isinstance(document, dict)
        or set(document) != keys
        or document["format"] != FORMAT
        or not isinstance(document["replies"], list)
    ):
        raise ScenarioError("scenario_shape")
    for item in document["replies"]:
        if not isinstance(item, dict) or set(item) != {"delay_ms", "tone"}:
            raise ScenarioError("scenario_shape")
        if not isinstance(item["tone"], dict) or set(item["tone"]) != {"hz", "ms", "level"}:
            raise ScenarioError("scenario_shape")
    replies = document["replies"]
    if not _int(document["end_of_caller_ms"], 20, 5000) or document["after_replies"] not in (
        AFTER_REPLIES
    ):
        raise ScenarioError("scenario_value")
    if not 1 <= len(replies) <= 64:
        raise ScenarioError("scenario_value")
    parsed = []
    for item in replies:
        tone = item["tone"]
        if not (
            _int(item["delay_ms"], 0, 10_000)
            and _int(tone["hz"], 50, 3400)
            and _int(tone["ms"], 20, 10_000)
            and tone["ms"] % 20 == 0
            and _int(tone["level"], 1, 32767)
        ):
            raise ScenarioError("scenario_value")
        parsed.append(Reply(item["delay_ms"], tone["hz"], tone["ms"], tone["level"]))
    return Scenario(document["end_of_caller_ms"], tuple(parsed), document["after_replies"])
