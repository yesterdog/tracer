# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The run verdict: the headline, the status and the weighted score (ADR 0013; P1-22).

- ``RunVerdict`` (``vrd``, a record of fact): ``required_total`` and the four counts of the
  required checks, the ``status``, one ``VerdictCheck`` per required rubric binding, and the
  optional ``weighted_score`` with its ``weight_coverage`` and the ``weighted_excluded`` list.
  Checked on load: the counts add up and equal the tally of the checks; the status follows from
  them (``fail`` if any check failed, else ``pass`` if all passed, else ``inconclusive``); score
  and coverage come together, the score in [0, 1] and the coverage in (0, 1].
- ``render_headline`` (``"x/y required checks passed"``; y includes the checks that did not run)
  and ``render_weighted_score`` (never the score without its coverage, P-33).
- ``outcome_of_result``, ``compute_weighted_score`` and ``check_verdict``: pure functions; the
  last checks a verdict against its rubric, bindings and results (``ValueError``).

Weights never change the headline (P-33). An excluded weighted check (not applicable or errored)
is in neither sum and is listed; it is never counted as 0.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Annotated, Any, ClassVar, Final, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, Strict, model_validator

from tracer_evalschema._constraints import ClosedSet, echoed
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import EntityBase
from tracer_evalschema.ids import MetricBindingId, MetricResultId
from tracer_evalschema.ijson import MAX_SAFE_INTEGER
from tracer_evalschema.metrics import MetricBinding, MetricDefinitionRef
from tracer_evalschema.primitives import DECIMAL_PATTERN, DecimalString, canonical_decimal_text
from tracer_evalschema.results import MetricResult
from tracer_evalschema.rubric import Rubric

__all__ = [
    "CHECK_OUTCOMES",
    "EXCLUDED_OUTCOMES",
    "VERDICT_STATUSES",
    "RunVerdict",
    "VerdictCheck",
    "WeightedExclusion",
    "WeightedScore",
    "check_verdict",
    "compute_weighted_score",
    "outcome_of_result",
    "render_headline",
    "render_weighted_score",
]

CHECK_OUTCOMES: Final = ("passed", "failed", "not_applicable", "errored")
EXCLUDED_OUTCOMES: Final = ("not_applicable", "errored")
VERDICT_STATUSES: Final = ("pass", "fail", "inconclusive")
SCALE: Final = 18

Count = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]


class VerdictCheck(HidingModel):
    """One required rubric binding and the outcome of its result (ADR 0013 Decisions 2 and 3).
    ``metric_ref`` is the binding's ``metric`` reference, never text."""

    # Embeds a ContentDigest (and so a KeyRef), so it hides its input (ADR 0007 Decision 8 (b),
    # P1-21 C3b closure rule).
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    binding_id: MetricBindingId
    metric_ref: MetricDefinitionRef
    outcome: Annotated[str, ClosedSet(CHECK_OUTCOMES)]
    result_id: MetricResultId


class WeightedExclusion(BaseModel):
    """A weighted check left out of the weighted score, and why (ADR 0013 Decision 8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: MetricBindingId
    outcome: Annotated[str, ClosedSet(EXCLUDED_OUTCOMES)]


def _status_of(passed: int, failed: int, total: int) -> str:
    if failed > 0:
        return "fail"
    return "pass" if passed == total else "inconclusive"


class RunVerdict(HidingModel, EntityBase):
    """The verdict of a run (prefix ``vrd``; ADR 0013). A record of fact without ``run_id``: the
    RunBundle is the unit of a run (N22)."""

    id_prefix: ClassVar[str] = "vrd"

    required_total: Annotated[int, Strict(), Field(ge=1, le=MAX_SAFE_INTEGER)]
    passed: Count
    failed: Count
    not_applicable: Count
    errored: Count
    status: Annotated[str, ClosedSet(VERDICT_STATUSES)]
    checks: list[VerdictCheck]
    weighted_score: DecimalString | None = None
    weight_coverage: DecimalString | None = None
    weighted_excluded: list[WeightedExclusion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> RunVerdict:
        problems: list[Problem] = []

        def problem(field: str, message: str) -> None:
            problems.append(Problem((field,), "run_verdict", message))

        counts = (self.passed, self.failed, self.not_applicable, self.errored)
        if sum(counts) != self.required_total:
            problem(
                "required_total",
                "required_total must equal passed + failed + not_applicable + errored",
            )
        tally = tuple(
            sum(1 for check in self.checks if check.outcome == outcome)
            for outcome in CHECK_OUTCOMES
        )
        if tally != counts:
            problem("checks", "the counts must equal the tally of checks[].outcome")
        bindings = [check.binding_id for check in self.checks]
        if len(set(bindings)) != len(bindings):
            problem("checks", "a binding has one check at most")
        if self.status != _status_of(self.passed, self.failed, self.required_total):
            problem(
                "status",
                "status is fail if any check failed, else pass if every required check passed, "
                "else inconclusive",
            )
        if (self.weighted_score is None) != (self.weight_coverage is None):
            field = "weight_coverage" if self.weight_coverage is None else "weighted_score"
            problem(field, "weighted_score and weight_coverage are stored together (P-33)")
        if self.weighted_score is not None and not 0 <= Decimal(self.weighted_score) <= 1:
            problem("weighted_score", "the weighted score lies in [0, 1]")
        if self.weight_coverage is not None and not 0 < Decimal(self.weight_coverage) <= 1:
            problem("weight_coverage", "the weight coverage lies in (0, 1]")
        excluded = [exclusion.binding_id for exclusion in self.weighted_excluded]
        if len(set(excluded)) != len(excluded):
            problem("weighted_excluded", "weighted_excluded lists a binding once")
        raise_problems(type(self).__name__, problems)
        return self


# ---------------------------------------------------------------------------------- rendering


def _two_decimals(text: str) -> str:
    return str(Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def render_headline(verdict: RunVerdict) -> str:
    """``"{passed}/{required_total} required checks passed"`` (ADR 0013 Decision 5): the
    denominator includes not_applicable and errored checks, and weights never enter it."""
    return f"{verdict.passed}/{verdict.required_total} required checks passed"


def render_weighted_score(verdict: RunVerdict) -> str | None:
    """``"weighted score {score} (weight coverage {coverage})"``, both at two decimals half-even
    (display only), or ``None`` without a score. The score is never shown alone (P-33)."""
    score, coverage = verdict.weighted_score, verdict.weight_coverage
    if score is None or coverage is None:
        return None
    return f"weighted score {_two_decimals(score)} (weight coverage {_two_decimals(coverage)})"


# ------------------------------------------------------------------------------ the outcome


def outcome_of_result(result: MetricResult) -> str:
    """The verdict outcome of a result (ADR 0013 Decision 3): scored and passed is ``passed``,
    scored and not passed ``failed``, ``not_applicable`` and ``errored`` follow the status. A
    scored result without a pass decision (``passed=None``) has no outcome: ``ValueError``."""
    if result.status == "not_applicable":
        return "not_applicable"
    if result.status == "error":
        return "errored"
    if result.passed is None:
        raise ValueError(
            f"result {result.id} is scored without a pass decision (passed=None): a metric without "
            "a pass rule cannot be a verdict check"
        )
    return "passed" if result.passed else "failed"


# --------------------------------------------------------------------------- weighted score


class WeightedScore(NamedTuple):
    """The weighted score, its coverage (canonical decimal strings, or ``None`` when nothing was
    scored) and the excluded checks in input order."""

    score: str | None
    coverage: str | None
    excluded: list[WeightedExclusion]


def _rounded(value: Fraction) -> str:
    """``value`` (>= 0) rounded half-even at scale 18, as canonical decimal text."""
    scaled = value * 10**SCALE
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    twice = 2 * remainder
    if twice > scaled.denominator or (twice == scaled.denominator and quotient % 2 == 1):
        quotient += 1
    return canonical_decimal_text(Decimal(quotient).scaleb(-SCALE))


_CANONICAL_DECIMAL: Final = re.compile(DECIMAL_PATTERN[1:-1])


def _weight(text: object) -> Fraction:
    if not isinstance(text, str) or _CANONICAL_DECIMAL.fullmatch(text) is None:
        raise ValueError("a weight is a canonical decimal string")
    weight = Fraction(Decimal(text))
    if weight <= 0:
        raise ValueError("a weight is strictly greater than 0")
    return weight


def compute_weighted_score(items: Iterable[tuple[Any, str, str]]) -> WeightedScore:
    """The weighted score of ``(binding_id, weight, outcome)`` items (ADR 0013 Decision 8).

    Scored items are passed or failed: ``score = sum(w * passed) / sum(w)`` over them and
    ``coverage = sum(w scored) / sum(w all)``, exact and rounded half-even at scale 18. Not
    applicable and errored items are excluded and listed, in input order. With nothing scored
    the score and coverage are ``None`` (never 0).
    """
    total = Fraction(0)
    scored = Fraction(0)
    earned = Fraction(0)
    excluded: list[WeightedExclusion] = []
    for binding_id, weight_text, outcome in items:
        weight = _weight(weight_text)
        if outcome not in CHECK_OUTCOMES:
            raise ValueError(f"unknown check outcome {echoed(outcome)}")
        total += weight
        if outcome in EXCLUDED_OUTCOMES:
            excluded.append(WeightedExclusion(binding_id=binding_id, outcome=outcome))
            continue
        scored += weight
        if outcome == "passed":
            earned += weight
    if scored == 0:
        return WeightedScore(None, None, excluded)
    return WeightedScore(_rounded(earned / scored), _rounded(scored / total), excluded)


# ----------------------------------------------------------------------------- check_verdict


def _digests_differ(left: Any, right: Any) -> bool:
    """Two digests that can be compared without a key (same scheme and ``key_ref``) and differ.
    A digest that is absent, or that cannot be compared here, is never reported as differing."""
    if left is None or right is None:
        return False
    return bool(left.scheme == right.scheme and left.key_ref == right.key_ref and left != right)


def _find_binding(bindings: Sequence[MetricBinding], wanted: Any, version: int) -> MetricBinding:
    for binding in bindings:
        if binding.id == wanted and binding.version == version:
            return binding
    raise ValueError(f"binding {wanted} version {version} of the rubric is not supplied")


def _results_of(results: Sequence[MetricResult], binding: MetricBinding) -> list[MetricResult]:
    metric = binding.metric
    return [r for r in results if (r.metric.id, r.metric.version) == (metric.id, metric.version)]


def check_verdict(
    verdict: RunVerdict,
    *,
    rubric: Rubric,
    bindings: Sequence[MetricBinding],
    results: Sequence[MetricResult],
) -> None:
    """Check a verdict against its rubric, bindings and results (ADR 0013). ``ValueError`` when:

    - the checks are not exactly the required rubric bindings (message names ``required``);
    - a check's ``metric_ref`` differs from its binding's ``metric`` (``metric``);
    - a check's result is not supplied or is not a result of the binding's metric (``result``);
    - a check's outcome differs from ``outcome_of_result`` (``outcome``);
    - a weighted rubric binding (required or not) has no single result of its metric, or the stored
      score, coverage or exclusions differ from ``compute_weighted_score`` (``weight``).
    """
    resolved = {
        item.binding.id: _find_binding(bindings, item.binding.id, item.binding.version)
        for item in rubric.items
    }
    required = {item.binding.id for item in rubric.items if item.required}
    checked = [check.binding_id for check in verdict.checks]
    if set(checked) != required or len(checked) != len(required):
        raise ValueError(
            "the verdict's checks are not exactly the required bindings of the rubric (one check "
            "per required binding, none for another binding)"
        )
    by_id = {result.id: result for result in results}
    for check in verdict.checks:
        binding = resolved[check.binding_id]
        reference, metric = check.metric_ref, binding.metric
        if (reference.id, reference.version) != (metric.id, metric.version) or _digests_differ(
            reference.content_digest, metric.content_digest
        ):
            raise ValueError(
                f"the check of binding {binding.id} names metric_ref {reference.id} version "
                f"{reference.version}, not the binding's metric {metric.id} version "
                f"{metric.version}"
            )
        result = by_id.get(check.result_id)
        if result is None:
            raise ValueError(
                f"the result {check.result_id} of binding {binding.id} is not supplied"
            )
        if (result.metric.id, result.metric.version) != (metric.id, metric.version):
            raise ValueError(
                f"result {result.id} is not a result of the metric of binding {binding.id}"
            )
        if check.outcome != outcome_of_result(result):
            raise ValueError(
                f"the check of binding {binding.id} has outcome {check.outcome}, but its result "
                f"{result.id} gives {outcome_of_result(result)}"
            )
    weighted: list[tuple[Any, str, str]] = []
    for item in rubric.items:
        if item.weight is None:
            continue
        binding = resolved[item.binding.id]
        found = _results_of(results, binding)
        if len(found) != 1:
            raise ValueError(
                f"weighted binding {binding.id} needs exactly one result of its metric among the "
                f"supplied results, found {len(found)}"
            )
        weighted.append((binding.id, item.weight, outcome_of_result(found[0])))
    expected = compute_weighted_score(weighted)
    if (verdict.weighted_score, verdict.weight_coverage) != (expected.score, expected.coverage):
        raise ValueError(
            "the stored weighted_score and weight_coverage differ from the rubric's weights "
            "applied to the results"
        )
    stored = [(e.binding_id, e.outcome) for e in verdict.weighted_excluded]
    if stored != [(e.binding_id, e.outcome) for e in expected.excluded]:
        raise ValueError(
            "the stored weighted_excluded list differs from the excluded weighted checks of the "
            "rubric"
        )
