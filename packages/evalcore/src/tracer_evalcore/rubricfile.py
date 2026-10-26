# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The rubric file ``tracer.rubric/1``: the owner's run parameters and review budget.

The file is the one place where the threshold, the normaliser and the review budget are written.
No parameter has a default here or anywhere else (ADR 0023 rules 4 and 17): a file that lacks
one is refused with a stable code before anything is sent. The optional ``note`` is for people
and is never stored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.flags import ThresholdError, parse_threshold
from tracer_evalcore.normalise import SUPPORTED_NORMALISERS
from tracer_evalcore.registry import SHARE_KEY, SHIPPED_KEYS
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict
from tracer_evalschema.metrics import RangeRule

__all__ = [
    "RUBRIC_FORMAT",
    "RubricFileError",
    "RubricItemSpec",
    "RubricSpec",
    "check_spec",
    "parse_rubric_file",
]

RUBRIC_FORMAT = "tracer.rubric/1"

_TOP = frozenset({"format", "parameters", "items"})
_PARAMETERS = frozenset({"flag_min_confidence", "normaliser"})
_ITEM = frozenset({"metric", "required"})
_RULE = frozenset({"kind", "max", "inclusive"})


class RubricFileError(InputRefusedError):
    """A rubric file was refused. ``code`` is the stable reason; the message is the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RubricItemSpec:
    metric: str
    required: bool
    pass_rule: RangeRule | None


@dataclass(frozen=True)
class RubricSpec:
    """The parsed file. ``flag_min_confidence`` stays the JSON number of the file."""

    flag_min_confidence: int | float
    normaliser: str
    items: tuple[RubricItemSpec, ...]


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def _read(text: str) -> Mapping[str, Any]:
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError):
        raise RubricFileError("rubric_not_json") from None
    if not isinstance(document, Mapping):
        raise RubricFileError("rubric_shape")
    return document


def _shape(document: Mapping[str, Any]) -> list[tuple[str, bool, Any, bool]]:
    """The structural checks: top level, parameters and items, without any value rule."""
    if set(document) - {"note"} != _TOP:
        raise RubricFileError("rubric_shape")
    if "note" in document and not isinstance(document["note"], str):
        raise RubricFileError("rubric_shape")
    parameters, raw_items = document["parameters"], document["items"]
    if (
        not isinstance(parameters, Mapping)
        or set(parameters) != _PARAMETERS
        or not _is_number(parameters["flag_min_confidence"])
        or not isinstance(parameters["normaliser"], str)
        or not isinstance(raw_items, list)
    ):
        raise RubricFileError("rubric_shape")
    items: list[tuple[str, bool, Any, bool]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise RubricFileError("rubric_shape")
        members = set(raw)
        if members not in (_ITEM, _ITEM | {"pass_rule"}):
            raise RubricFileError("rubric_shape")
        metric, required = raw["metric"], raw["required"]
        if not isinstance(metric, str) or type(required) is not bool:
            raise RubricFileError("rubric_shape")
        if "pass_rule" in raw and metric != SHARE_KEY:
            raise RubricFileError("rubric_shape")
        items.append((metric, required, raw.get("pass_rule"), "pass_rule" in raw))
    return items


def _rule(raw: Any) -> RangeRule:
    """The share's pass rule: exactly ``range`` with a ``max`` in [0, 1] and ``inclusive``."""
    if not isinstance(raw, Mapping) or set(raw) != _RULE:
        raise RubricFileError("rubric_pass_rule_invalid")
    limit = raw["max"]
    if raw["kind"] != "range" or not _is_number(limit) or type(raw["inclusive"]) is not bool:
        raise RubricFileError("rubric_pass_rule_invalid")
    if not 0 <= limit <= 1:
        raise RubricFileError("rubric_pass_rule_invalid")
    try:
        return RangeRule.model_validate(dict(raw))
    except ValueError:
        raise RubricFileError("rubric_pass_rule_invalid") from None


def _check_metrics(metrics: Sequence[str]) -> None:
    if any(metric not in SHIPPED_KEYS for metric in metrics):
        raise RubricFileError("rubric_metric_unknown")
    if len(set(metrics)) != len(metrics):
        raise RubricFileError("rubric_metric_repeated")


def _check_required(items: Sequence[RubricItemSpec]) -> None:
    if any(item.required and item.pass_rule is None for item in items):
        raise RubricFileError("rubric_required_without_pass_rule")


def _check_budget(items: Sequence[RubricItemSpec]) -> None:
    """The review budget is the share's required pass rule; nothing else stands in for it."""
    if not any(i.metric == SHARE_KEY and i.required and i.pass_rule is not None for i in items):
        raise RubricFileError("rubric_budget_missing")


def _check_rule_values(items: Sequence[RubricItemSpec]) -> None:
    for item in items:
        rule = item.pass_rule
        if rule is None:
            continue
        if item.metric != SHARE_KEY:
            raise RubricFileError("rubric_shape")
        limit = rule.max
        if rule.min is not None or not _is_number(limit) or not 0 <= limit <= 1:  # type: ignore[operator]
            raise RubricFileError("rubric_pass_rule_invalid")


def check_spec(spec: RubricSpec) -> None:
    """The file rules for a spec that was not parsed from a file (the budget is checked first)."""
    _check_budget(spec.items)
    _check_metrics([item.metric for item in spec.items])
    _check_rule_values(spec.items)
    _check_required(spec.items)


def parse_rubric_file(text: str) -> RubricSpec:
    """Parse the decoded text of a rubric file. Raises ``RubricFileError`` with one code."""
    if not isinstance(text, str):
        raise TypeError("the rubric file is decoded text")
    document = _read(text)
    shaped = _shape(document)
    if document["format"] != RUBRIC_FORMAT:
        raise RubricFileError("rubric_format_unsupported")
    threshold = document["parameters"]["flag_min_confidence"]
    normaliser = document["parameters"]["normaliser"]
    try:
        parse_threshold(threshold)
    except (ThresholdError, ValueError):
        raise RubricFileError("rubric_parameter_invalid") from None
    if normaliser not in SUPPORTED_NORMALISERS:
        raise RubricFileError("rubric_parameter_invalid")
    _check_metrics([metric for metric, _, _, _ in shaped])
    items = tuple(
        RubricItemSpec(metric, required, _rule(raw) if present else None)
        for metric, required, raw, present in shaped
    )
    _check_required(items)
    _check_budget(items)
    return RubricSpec(threshold, normaliser, items)
