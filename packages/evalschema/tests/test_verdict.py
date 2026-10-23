# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-22: RunVerdict, the headline, the weighted score and its coverage (ADR 0013).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.verdict``:
the entity ``RunVerdict`` (``vrd``) with the value objects ``VerdictCheck{binding_id, metric_ref,
outcome, result_id}`` and ``WeightedExclusion{binding_id, outcome}``, and the pure functions
``render_headline(verdict) -> str``, ``render_weighted_score(verdict) -> str | None``,
``outcome_of_result(result) -> str``,
``compute_weighted_score(items) -> WeightedScore(score, coverage, excluded)`` (``items`` are
``(binding_id, weight, outcome)`` triples) and
``check_verdict(verdict, *, rubric, bindings, results) -> None`` (a ``ValueError``: the rules that
need the rubric, the bindings and the results).

Rubric validation (a required or weighted item needs a pass rule, weights are positive, a disabled
binding is refused) is tested in ``test_rubric.py`` under the same SCOPE names.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import importlib
import itertools
import pkgutil
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    contains_key,
    equals_true_rule,
    judge_definition,
    make,
    property_enum,
    ref,
    registered_class,
    schema_accepts,
    schema_of,
)
from ._support_b3 import (
    assert_rejected3,
    entity_class3,
    judge_info_doc,
    meta,
    new_id,
    per_turn_summary_doc,
    result_doc,
    verdict_world,
)

OUTCOMES = ("passed", "failed", "not_applicable", "errored")


def _verdict_module() -> Any:
    return api("verdict")


def _status(passed: int, failed: int, total: int) -> str:
    """ADR 0013 Decision 6, written out independently."""
    if failed > 0:
        return "fail"
    return "pass" if passed == total else "inconclusive"


def _check_doc(outcome: str, binding: str | None = None, **over: Any) -> dict[str, Any]:
    return {
        "binding_id": binding or new_id("mbd"),
        "metric_ref": ref("met"),
        "outcome": outcome,
        "result_id": new_id("res"),
        **over,
    }


def _verdict_doc(outcomes: list[str], **over: Any) -> dict[str, Any]:
    """A consistent verdict document for the given check outcomes."""
    counts = {o: outcomes.count(o) for o in OUTCOMES}
    total = len(outcomes)
    doc: dict[str, Any] = {
        **meta("vrd"),
        "required_total": total,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "not_applicable": counts["not_applicable"],
        "errored": counts["errored"],
        "status": _status(counts["passed"], counts["failed"], total),
        "checks": [_check_doc(o) for o in outcomes],
        "weighted_excluded": [],
    }
    doc.update(over)
    return doc


def _verdict(outcomes: list[str], **over: Any) -> Any:
    return entity_class3("RunVerdict").model_validate(_verdict_doc(outcomes, **over))


def _reject(doc: dict[str, Any], field: str | None = None) -> None:
    assert_rejected3(entity_class3("RunVerdict"), doc, field=field)


def test_weighted_exclusions_are_unique_per_binding() -> None:
    """Proposed name (ADR 0013 Decision 8): ``weighted_excluded`` lists a binding at most once."""
    binding = new_id("mbd")
    exclusion = {"binding_id": binding, "outcome": "not_applicable"}
    entity_class3("RunVerdict").model_validate(
        _verdict_doc(["passed", "not_applicable"], weighted_excluded=[exclusion])
    )
    _reject(
        _verdict_doc(["passed", "not_applicable"], weighted_excluded=[exclusion, exclusion]),
        "weighted_excluded",
    )
    _reject(
        _verdict_doc(
            ["passed", "not_applicable"],
            weighted_excluded=[exclusion, {"binding_id": binding, "outcome": "errored"}],
        ),
        "weighted_excluded",
    )


def test_outcome_of_result_needs_a_pass_decision() -> None:
    """Proposed name (ADR 0013 Decision 3; a reading listed in api-surface 22.5): a scored result
    with ``passed=None`` (a metric without a pass rule, P-08) has no verdict outcome: it cannot be a
    required check, and ``outcome_of_result`` raises a ``ValueError`` naming the result instead of
    reading it as passed or failed."""
    definition = make("MetricDefinition", **judge_definition(pass_rule=None))
    informational = entity_class3("MetricResult").model_validate(
        result_doc(definition, value=True, passed=None)
    )
    with pytest.raises(ValueError, match=str(informational.id)):
        _verdict_module().outcome_of_result(informational)


def test_run_verdict_shape_and_enums() -> None:
    """Proposed name (ADR 0013 Decisions 1-3): a verdict is an entity (``vrd``, a record of fact)
    with ``required_total``, the four counts, ``status`` (pass, fail, inconclusive), ``checks``,
    the optional ``weighted_score`` and ``weight_coverage`` and ``weighted_excluded``; a check is
    a value object ``{binding_id, metric_ref, outcome, result_id}`` with the outcome set passed,
    failed, not_applicable, errored; the typed IDs are ``mbd_`` and ``res_`` and ``metric_ref``
    is a ``MetricDefinitionRef`` object, never text."""
    model = entity_class3("RunVerdict")
    assert set(model.model_fields) - {
        "id",
        "schema_version",
        "created_at",
        "metadata",
        "metadata_flags",
    } == {
        "required_total",
        "passed",
        "failed",
        "not_applicable",
        "errored",
        "status",
        "checks",
        "weighted_score",
        "weight_coverage",
        "weighted_excluded",
    }
    assert set(property_enum(model, "status")) == {"pass", "fail", "inconclusive"}
    check = registered_class("VerdictCheck")
    assert set(check.model_fields) == {"binding_id", "metric_ref", "outcome", "result_id"}
    assert set(property_enum(check, "outcome")) == set(OUTCOMES)
    assert not contains_key(schema_of(model), "const")
    assert not {"id", "schema_version", "created_at", "metadata"} & set(check.model_fields)
    loaded = _verdict(["passed", "failed", "not_applicable"])
    assert model.model_validate_json(loaded.model_dump_json()) == loaded
    assert schema_accepts(model, loaded.model_dump(mode="json"))
    assert str(loaded.id).startswith("vrd_")
    for bad in ("passed ", "pass", "skipped", "Passed", "", None):
        with pytest.raises(ValidationError):
            check.model_validate({**_check_doc("passed"), "outcome": bad})
    for field, bad_value in (
        ("binding_id", new_id("met")),
        ("binding_id", "mbd_x"),
        ("result_id", new_id("mbd")),
        ("result_id", None),
        ("metric_ref", "p95_latency@3"),
        ("metric_ref", new_id("met")),
        ("metric_ref", {"id": new_id("mbd"), "version": 1}),
    ):
        with pytest.raises(ValidationError):
            check.model_validate({**_check_doc("passed"), field: bad_value})
    for bad_status in ("passed", "ok", "Pass", "", None):
        _reject(_verdict_doc(["passed"], status=bad_status), "status")


def test_empty_verdict_rejected() -> None:
    """Proposed name (ADR 0013 Decision 7: a rubric has at least one required metric, so a verdict
    has at least one check): ``required_total`` is at least 1. A verdict of 0/0 would be a
    vacuous ``pass``."""
    _reject(_verdict_doc([]), "required_total")
    _reject(
        {
            **_verdict_doc([]),
            "status": "pass",
            "required_total": 0,
            "checks": [],
        }
    )
    assert _verdict(["passed"]).required_total == 1


def test_counts_sum_to_required_total() -> None:
    """ADR 0013 I1: ``required_total`` equals ``passed + failed + not_applicable + errored``;
    the denominator includes the checks that did not run. A count that does not add up, a negative
    or non-integer count, or a missing count is rejected."""
    outcomes = ["passed", "passed", "failed", "not_applicable", "errored"]
    loaded = _verdict(outcomes)
    total = loaded.passed + loaded.failed + loaded.not_applicable + loaded.errored
    assert total == loaded.required_total == 5
    for delta in (1, -1, 3):
        _reject(_verdict_doc(outcomes, required_total=5 + delta))
    for field in ("passed", "failed", "not_applicable", "errored"):
        _reject({k: v for k, v in _verdict_doc(outcomes).items() if k != field}, field)
        for bad in (-1, 1.5, "1", True, None):
            _reject(_verdict_doc(outcomes, **{field: bad}), field)
    _reject(_verdict_doc(outcomes, required_total="5"), "required_total")
    _reject(_verdict_doc(outcomes, required_total=5.0), "required_total")


def test_status_pass_fail_inconclusive_table() -> None:
    """ADR 0013 Decision 6, I2: ``pass`` iff every required check passed; ``fail`` iff any failed;
    ``inconclusive`` iff none failed and some did not run. The table of the ADR:
    (5,5,0,0,0) pass, (5,3,1,1,0) fail, (5,4,0,1,0) inconclusive, (5,4,0,0,1) inconclusive,
    where the columns are required_total, passed, failed, not_applicable, errored."""
    table = [
        ((5, 5, 0, 0, 0), "pass"),
        ((5, 3, 1, 1, 0), "fail"),
        ((5, 4, 0, 1, 0), "inconclusive"),
        ((5, 4, 0, 0, 1), "inconclusive"),
    ]
    for (total, passed, failed, not_applicable, errored), status in table:
        outcomes = (
            ["passed"] * passed
            + ["failed"] * failed
            + ["not_applicable"] * not_applicable
            + ["errored"] * errored
        )
        assert len(outcomes) == total
        assert _verdict(outcomes).status == status
    # the same rule over every composition of up to five checks
    for total in range(1, 6):
        for composition in itertools.product(OUTCOMES, repeat=total):
            expected = _status(composition.count("passed"), composition.count("failed"), total)
            assert _verdict(list(composition)).status == expected


def test_inconsistent_status_rejected() -> None:
    """A-15, ADR 0013 I3: a stored status that disagrees with the counts is rejected, for every
    wrong status of every composition of up to four checks; a failed check with a ``pass`` status
    is the classic forgery."""
    statuses = ("pass", "fail", "inconclusive")
    for total in range(1, 5):
        for composition in itertools.product(OUTCOMES, repeat=total):
            right = _status(composition.count("passed"), composition.count("failed"), total)
            for status in statuses:
                doc = _verdict_doc(list(composition), status=status)
                if status == right:
                    entity_class3("RunVerdict").model_validate(doc)
                else:
                    _reject(doc)
    _reject(_verdict_doc(["passed", "failed"], status="pass"))
    _reject(_verdict_doc(["passed", "not_applicable"], status="pass"))
    _reject(_verdict_doc(["passed", "errored"], status="pass"))


def test_counts_equal_check_tally() -> None:
    """ADR 0013 I4, Decision 4: every count equals the tally of ``checks[].outcome``; a count
    moved from one outcome to another with the sum intact is rejected, and so is a binding that
    has two checks."""
    outcomes = ["passed", "passed", "failed", "not_applicable"]
    _verdict(outcomes)
    moved = _verdict_doc(outcomes, passed=1, failed=2)
    _reject(moved)
    _reject(_verdict_doc(outcomes, passed=3, not_applicable=0))
    _reject(_verdict_doc(outcomes, errored=1, not_applicable=0))
    extra = _verdict_doc(outcomes)
    extra["checks"] = [*extra["checks"], _check_doc("passed")]
    _reject(extra)
    short = _verdict_doc(outcomes)
    short["checks"] = short["checks"][:-1]
    _reject(short)
    twice = _verdict_doc(["passed", "passed"])
    twice["checks"][1]["binding_id"] = twice["checks"][0]["binding_id"]
    _reject(twice)


def test_headline() -> None:
    """ADR 0013 Decision 5, I5: ``render_headline`` is exactly ``"{passed}/{required_total} required
    checks passed"``; the denominator includes not_applicable and errored checks, so a run that
    could not check everything never reads as a clean pass."""
    render = _verdict_module().render_headline
    assert render(_verdict(["passed"] * 4 + ["failed"])) == "4/5 required checks passed"
    assert render(_verdict(["passed"] * 3 + ["not_applicable", "errored"])) == (
        "3/5 required checks passed"
    )
    assert render(_verdict(["passed"])) == "1/1 required checks passed"
    assert render(_verdict(["failed", "failed"])) == "0/2 required checks passed"
    assert render(_verdict(["not_applicable"] * 3)) == "0/3 required checks passed"
    twelve = _verdict(["passed"] * 10 + ["failed", "errored"])
    assert render(twelve) == "10/12 required checks passed"


def test_headline_unchanged_by_weights() -> None:
    """P-33 (ADR 0013 Decision 5, I5): weights never change the headline and the headline never
    contains the weighted score; the same checks with and without a weighted score and with
    different scores render the same text."""
    render = _verdict_module().render_headline
    outcomes = ["passed", "passed", "failed", "not_applicable"]
    plain = _verdict(outcomes)
    heavy = _verdict(outcomes, weighted_score="0.9", weight_coverage="1")
    light = _verdict(outcomes, weighted_score="0.1", weight_coverage="0.5")
    assert render(plain) == render(heavy) == render(light) == "2/4 required checks passed"
    for verdict in (heavy, light):
        text = render(verdict)
        assert "weight" not in text.lower()
        assert verdict.weighted_score not in text
    assert heavy.status == light.status == "fail"


# ---------------------------------------------------------------------- the weighted score


def _items(spec: list[tuple[str, str]]) -> list[tuple[Any, str, str]]:
    return [(api("ids").MetricBindingId.new(), weight, outcome) for weight, outcome in spec]


def test_weighted_score_5_3_2_with_na_is_1_coverage_0_8() -> None:
    """P-33 golden (ADR 0013 Decision 8, I8c): weights 5, 3 and 2; the weight-5 and weight-3 checks
    passed and the weight-2 check is not_applicable: score ``1``, coverage ``0.8``, rendered
    ``weighted score 1.00 (weight coverage 0.80)``, and the weight-2 check is listed as excluded."""
    verdict_module = _verdict_module()
    items = _items([("5", "passed"), ("3", "passed"), ("2", "not_applicable")])
    result = verdict_module.compute_weighted_score(items)
    assert (result.score, result.coverage) == ("1", "0.8")
    assert [(e.binding_id, e.outcome) for e in result.excluded] == [(items[2][0], "not_applicable")]
    verdict = _verdict(
        ["passed", "passed", "not_applicable"],
        weighted_score=result.score,
        weight_coverage=result.coverage,
        weighted_excluded=[{"binding_id": str(items[2][0]), "outcome": "not_applicable"}],
    )
    assert verdict_module.render_weighted_score(verdict) == (
        "weighted score 1.00 (weight coverage 0.80)"
    )
    assert verdict.weighted_excluded[0].outcome == "not_applicable"
    assert verdict.status == "inconclusive"


def test_weighted_score_excludes_na_and_lists_them() -> None:
    """ADR 0013 Decision 8, I8: not_applicable weighted checks are left out of both sums and
    listed in ``weighted_excluded`` with their binding ID and outcome, in input order; failed
    checks stay in with a contribution of 0."""
    compute = _verdict_module().compute_weighted_score
    items = _items(
        [("4", "passed"), ("2", "not_applicable"), ("3", "failed"), ("1", "not_applicable")]
    )
    result = compute(items)
    # scored weight 7, earned 4: 4/7; coverage 7/10
    assert result.score == "0.571428571428571429"
    assert result.coverage == "0.7"
    assert [(str(e.binding_id), e.outcome) for e in result.excluded] == [
        (str(items[1][0]), "not_applicable"),
        (str(items[3][0]), "not_applicable"),
    ]
    only_failed = compute(_items([("2", "failed"), ("2", "passed")]))
    assert (only_failed.score, only_failed.coverage) == ("0.5", "1")
    assert only_failed.excluded == []


def test_weighted_score_excludes_errored() -> None:
    """P-33 (ADR 0013 Decision 8, I8): an errored weighted check is excluded exactly like a
    not_applicable one (never counted as 0), and listed with outcome ``errored``."""
    compute = _verdict_module().compute_weighted_score
    items = _items([("5", "passed"), ("5", "errored"), ("2", "failed")])
    result = compute(items)
    assert result.score == "0.714285714285714286"  # 5 / 7
    assert result.coverage == "0.583333333333333333"  # 7 / 12
    assert [(e.binding_id, e.outcome) for e in result.excluded] == [(items[1][0], "errored")]
    # "never counted as 0": an errored check is not a failed one
    as_failed = compute(_items([("5", "passed"), ("5", "failed"), ("2", "failed")]))
    assert as_failed.score != result.score
    exclusion = registered_class("WeightedExclusion")
    assert set(property_enum(exclusion, "outcome")) == {"not_applicable", "errored"}
    for bad in ("passed", "failed", "skipped", None):
        with pytest.raises(ValidationError):
            exclusion.model_validate({"binding_id": new_id("mbd"), "outcome": bad})
    assert not {"id", "schema_version", "created_at", "metadata"} & set(exclusion.model_fields)


def test_weighted_score_absent_when_all_excluded() -> None:
    """P-33 (ADR 0013 Decision 8, I8a): when no weighted check was scored (all excluded, or the
    rubric has no weights) both the score and the coverage are absent: never a score of 0
    standing for 'nothing measured'."""
    compute = _verdict_module().compute_weighted_score
    result = compute(_items([("5", "not_applicable"), ("3", "errored")]))
    assert (result.score, result.coverage) == (None, None)
    assert [e.outcome for e in result.excluded] == ["not_applicable", "errored"]
    empty = compute([])
    assert (empty.score, empty.coverage, empty.excluded) == (None, None, [])
    verdict = _verdict(["not_applicable"])
    assert (verdict.weighted_score, verdict.weight_coverage) == (None, None)
    assert api("verdict").render_weighted_score(verdict) is None
    dumped = verdict.model_dump(mode="json")
    assert dumped["weighted_score"] is None
    assert dumped["weight_coverage"] is None


def test_weighted_score_range_0_1() -> None:
    """P-33 (ADR 0013 Decision 8, I8a; hypothesis): the computed score lies in [0,1] and the
    coverage in (0,1] for any weights and outcomes, the score is 0 only when every scored
    check failed, and a stored score or coverage outside its range is rejected."""
    compute = _verdict_module().compute_weighted_score

    @settings(derandomize=True, max_examples=120, deadline=None)
    @given(
        st.lists(
            st.tuples(st.integers(min_value=1, max_value=50), st.sampled_from(OUTCOMES)),
            min_size=1,
            max_size=8,
        )
    )
    def check(spec: list[tuple[int, str]]) -> None:
        result = compute(_items([(str(w), o) for w, o in spec]))
        scored = [(w, o) for w, o in spec if o in ("passed", "failed")]
        if not scored:
            assert (result.score, result.coverage) == (None, None)
            return
        assert result.score is not None
        assert result.coverage is not None
        assert Decimal(0) <= Decimal(result.score) <= Decimal(1)
        assert Decimal(0) < Decimal(result.coverage) <= Decimal(1)
        if all(o == "failed" for _w, o in scored):
            assert result.score == "0"
        if all(o == "passed" for _w, o in scored):
            assert result.score == "1"
        exact = Decimal(sum(w for w, _o in scored)) / Decimal(sum(w for w, _o in spec))
        assert abs(Decimal(result.coverage) - exact) <= Decimal("1e-18")

    check()
    for score in ("1.1", "-0.1", "2", "-1"):
        _reject(_verdict_doc(["passed"], weighted_score=score, weight_coverage="1"))
    for coverage in ("0", "1.1", "-0.5", "2"):
        _reject(_verdict_doc(["passed"], weighted_score="1", weight_coverage=coverage))
    _reject(_verdict_doc(["passed"], weighted_score=0.5, weight_coverage="1"))
    _reject(_verdict_doc(["passed"], weighted_score="0.50", weight_coverage="1"))
    assert _verdict(["passed"], weighted_score="0", weight_coverage="0.5").weighted_score == "0"
    assert _verdict(["passed"], weighted_score="1", weight_coverage="1").weight_coverage == "1"


def test_weight_coverage_required_with_score() -> None:
    """P-33 (ADR 0013 Decision 8, I8b): ``weight_coverage`` is present whenever ``weighted_score``
    is, and only then; the two are stored together. A score without a coverage, or a coverage
    without a score, is rejected."""
    _verdict(["passed"], weighted_score="1", weight_coverage="0.8")
    _verdict(["passed"])
    _reject(_verdict_doc(["passed"], weighted_score="1"), None)
    _reject(_verdict_doc(["passed"], weighted_score="1", weight_coverage=None))
    _reject(_verdict_doc(["passed"], weight_coverage="0.8"))
    _reject(_verdict_doc(["passed"], weighted_score=None, weight_coverage="0.8"))
    without = {k: v for k, v in _verdict_doc(["passed"]).items() if k != "weight_coverage"}
    _reject({**without, "weighted_score": "0.5"})
    schema = schema_of(entity_class3("RunVerdict"))
    assert "weight_coverage" not in schema.get("required", [])


def test_weight_coverage_is_scored_over_total_weight() -> None:
    """P-33 (ADR 0013 Decision 8, I8b): ``weight_coverage`` is the scored weight over the total
    weight of all weighted checks, exact in ``Decimal`` and rounded half-even at scale 18 when it
    does not terminate; the score is ``sum(w * passed) / sum(w)`` over the scored checks."""
    compute = _verdict_module().compute_weighted_score
    cases = [
        ([("5", "passed"), ("3", "passed"), ("2", "passed")], "1", "1"),
        ([("5", "passed"), ("3", "failed"), ("2", "not_applicable")], "0.625", "0.8"),
        ([("1", "passed"), ("1", "failed"), ("1", "failed")], "0.333333333333333333", "1"),
        ([("1", "passed"), ("1", "passed"), ("1", "failed")], "0.666666666666666667", "1"),
        ([("1", "passed"), ("2", "errored")], "1", "0.333333333333333333"),
        ([("0.5", "passed"), ("1.5", "failed"), ("2", "errored")], "0.25", "0.5"),
        ([("3", "passed"), ("7", "errored")], "1", "0.3"),
        ([("2", "failed"), ("3", "errored"), ("6", "errored")], "0", "0.181818181818181818"),
    ]
    for spec, score, coverage in cases:
        result = compute(_items(spec))
        assert (result.score, result.coverage) == (score, coverage), spec
    # exact ties at the 19th decimal digit round half to even
    tie_down = compute(_items([("1", "passed"), ("524287", "failed")]))  # 1 / 2**19
    assert tie_down.score == "0.000001907348632812"
    tie_up = compute(_items([("3", "passed"), ("524285", "failed")]))  # 3 / 2**19
    assert tie_up.score == "0.000005722045898438"
    big = compute(_items([("1000000000000", "passed"), ("1", "failed")]))
    assert big.score == "0.999999999999"
    assert big.coverage == "1"


def test_render_weighted_score_shows_coverage() -> None:
    """P-33 (ADR 0013 Decision 8, I8d): ``render_weighted_score`` returns ``"weighted score {score}
    (weight coverage {coverage})"`` with both values at two decimals (half-even, display only; the
    stored values stay exact) and ``None`` when there is no score."""
    render = _verdict_module().render_weighted_score
    verdict = _verdict(["passed"], weighted_score="0.571428571428571429", weight_coverage="0.7")
    assert render(verdict) == "weighted score 0.57 (weight coverage 0.70)"
    assert verdict.weighted_score == "0.571428571428571429"
    assert render(_verdict(["passed"], weighted_score="1", weight_coverage="1")) == (
        "weighted score 1.00 (weight coverage 1.00)"
    )
    assert render(_verdict(["passed"], weighted_score="0", weight_coverage="0.005")) == (
        "weighted score 0.00 (weight coverage 0.00)"
    )
    assert render(_verdict(["passed"], weighted_score="0.125", weight_coverage="0.135")) == (
        "weighted score 0.12 (weight coverage 0.14)"
    )  # half-even
    assert render(_verdict(["passed"])) is None


def test_no_renderer_shows_weighted_score_without_coverage() -> None:
    """P-33 (ADR 0013 I8d; contract test): every public ``render_*`` function of the package that
    can render a verdict shows the weighted score together with its coverage: the walk calls each
    renderer with a verdict that has a score and checks its output, and requires
    ``render_weighted_score`` to be among the renderers that produced it."""
    package = api("")
    verdict = _verdict(["passed", "failed"], weighted_score="0.43", weight_coverage="0.87")
    produced: list[str] = []
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"tracer_evalschema.{info.name}")
        for name, function in vars(module).items():
            if not (name.startswith("render_") and callable(function)):
                continue
            if getattr(function, "__module__", None) != module.__name__:
                continue
            try:
                output = function(verdict)
            except (TypeError, AttributeError, ValueError, KeyError):
                continue
            if not isinstance(output, str):
                continue
            if "0.43" in output or "weighted" in output.lower():
                produced.append(f"{module.__name__}.{name}")
                if "0.43" in output:
                    assert "0.87" in output, f"{name} shows the score without its coverage"
                    assert "coverage" in output.lower()
    assert any(name.endswith(".render_weighted_score") for name in produced), produced
    headline = _verdict_module().render_headline(verdict)
    assert "0.43" not in headline


def test_weighted_score_uses_per_turn_passed_not_share() -> None:
    """P-58 (ADR 0013 Decision 8a): a weighted ``per_turn_share`` check is one check that
    contributes ``w * passed`` (1 or 0), never ``w * share``: a share of 0.75 against ``range{min:
    0.8}`` fails and contributes 0, so weights 4 (that check) and 1 (passed) give 1/5, not
    (4 * 0.75 + 1) / 5. A passing share contributes its whole weight."""
    verdict_module = _verdict_module()
    definition = make(
        "MetricDefinition",
        **_per_turn_definition_doc(min_share=0.8),
    )
    failing = entity_class3("MetricResult").model_validate(
        result_doc(
            definition,
            value=None,
            passed=False,
            per_turn=per_turn_summary_doc(3, 1, 0, 0),
            judge=judge_info_doc(),
        )
    )
    assert failing.per_turn.share == "0.75"
    assert verdict_module.outcome_of_result(failing) == "failed"
    passing = entity_class3("MetricResult").model_validate(
        result_doc(
            definition,
            value=None,
            passed=True,
            per_turn=per_turn_summary_doc(9, 1, 0, 0),
            judge=judge_info_doc(),
        )
    )
    assert passing.per_turn.share == "0.9"
    assert verdict_module.outcome_of_result(passing) == "passed"
    items = _items([("4", verdict_module.outcome_of_result(failing)), ("1", "passed")])
    result = verdict_module.compute_weighted_score(items)
    assert result.score == "0.2"
    assert result.score != "0.8"  # (4 * 0.75 + 1) / 5
    assert result.coverage == "1"
    items = _items([("4", verdict_module.outcome_of_result(passing)), ("1", "failed")])
    assert verdict_module.compute_weighted_score(items).score == "0.8"  # 4 * 1 / 5, not 4 * 0.9 / 5


def _per_turn_definition_doc(min_share: float) -> dict[str, Any]:
    return judge_definition(
        key="polite_turn_share",
        aggregation={"kind": "per_turn_share", "turn_rule": equals_true_rule()},
        pass_rule={"kind": "range", "min": min_share, "inclusive": True},
    )


# ------------------------------------------------------- results, bindings and the rubric


def _result_of(definition: Any, outcome: str) -> Any:
    model = entity_class3("MetricResult")
    if outcome == "passed":
        doc = result_doc(definition, value=True, passed=True)
    elif outcome == "failed":
        doc = result_doc(definition, value=False, passed=False)
    elif outcome == "not_applicable":
        doc = result_doc(
            definition,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source=None,
            na_reason="language_not_observed",
        )
    else:
        doc = result_doc(
            definition,
            status="error",
            value=None,
            passed=None,
            judge=None,
            error={"code": "judge_call_failed", "message": "HTTP 503"},
        )
    return model.model_validate(doc)


def _world_and_results(
    outcomes: list[str],
    *,
    weights: tuple[str | None, ...] | None = None,
    required: tuple[bool, ...] | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    count = len(outcomes)
    world = verdict_world(
        weights=weights or ("5", "3", "2")[:count],
        required=required or (True,) * count,
    )
    results = [_result_of(d, o) for d, o in zip(world["definitions"], outcomes, strict=True)]
    return world, results


def _verdict_for(world: dict[str, Any], results: list[Any], **over: Any) -> Any:
    """The verdict a runner would write for ``results`` (checks for required items only, the
    weighted score through ``compute_weighted_score``)."""
    verdict_module = _verdict_module()
    by_metric = {r.metric.id: r for r in results}
    bindings = {b.id: b for b in world["bindings"]}
    checks = []
    weighted = []
    for item in world["rubric"].items:
        binding = bindings[item.binding.id]
        result = by_metric[binding.metric.id]
        outcome = verdict_module.outcome_of_result(result)
        if item.required:
            checks.append(
                {
                    "binding_id": str(binding.id),
                    "metric_ref": {"id": str(binding.metric.id), "version": binding.metric.version},
                    "outcome": outcome,
                    "result_id": str(result.id),
                }
            )
        if item.weight is not None:
            weighted.append((binding.id, item.weight, outcome))
    score = verdict_module.compute_weighted_score(weighted)
    outcomes = [c["outcome"] for c in checks]
    doc = _verdict_doc(
        outcomes,
        checks=checks,
        weighted_score=score.score,
        weight_coverage=score.coverage,
        weighted_excluded=[
            {"binding_id": str(e.binding_id), "outcome": e.outcome} for e in score.excluded
        ],
    )
    doc.update(over)
    return doc


def _validate(verdict_doc: dict[str, Any], world: dict[str, Any], results: list[Any]) -> None:
    verdict = entity_class3("RunVerdict").model_validate(verdict_doc)
    _verdict_module().check_verdict(
        verdict, rubric=world["rubric"], bindings=world["bindings"], results=results
    )


def test_checks_reference_required_bindings_only() -> None:
    """ADR 0013 I10, Decision 2: ``checks`` has one entry for each required binding of the rubric
    and none for a binding that is not required; a missing check, an extra check for an optional
    binding, a check for a binding outside the rubric, and a binding checked twice are rejected.
    The headline counts required checks only, while an optional weighted check can still enter
    the weighted score (P-34)."""
    world, results = _world_and_results(
        ["passed", "failed", "passed"], required=(True, True, False)
    )
    doc = _verdict_for(world, results)
    assert doc["required_total"] == 2
    _validate(doc, world, results)
    bindings = world["bindings"]
    extra = {
        "binding_id": str(bindings[2].id),
        "metric_ref": {"id": str(bindings[2].metric.id), "version": 1},
        "outcome": "passed",
        "result_id": str(results[2].id),
    }
    with_extra = _verdict_for(world, results)
    with_extra["checks"] = [*with_extra["checks"], extra]
    with_extra.update(required_total=3, passed=2, status="fail")
    with pytest.raises(ValueError, match="required"):
        _validate(with_extra, world, results)
    missing = _verdict_for(world, results)
    missing["checks"] = missing["checks"][:1]
    missing.update(required_total=1, failed=0, status="pass")
    with pytest.raises(ValueError, match="required"):
        _validate(missing, world, results)
    foreign = _verdict_for(world, results)
    foreign["checks"] = [
        {**foreign["checks"][0], "binding_id": new_id("mbd")},
        foreign["checks"][1],
    ]
    with pytest.raises(ValueError, match="required"):
        _validate(foreign, world, results)
    twice = _verdict_for(world, results)
    twice["checks"] = [twice["checks"][0], twice["checks"][0]]
    twice.update(required_total=2, passed=2, failed=0, status="pass")
    with pytest.raises((ValueError, ValidationError)):
        _validate(twice, world, results)


def test_check_outcome_matches_metric_result_status() -> None:
    """ADR 0013 Decision 3, I11: scored with ``passed`` true is ``passed``; scored with ``passed``
    false is ``failed``; not_applicable is ``not_applicable``; error is ``errored``
    (``outcome_of_result``). A check whose outcome disagrees with its result is rejected, and so
    is one whose result is not supplied or belongs to another metric."""
    verdict_module = _verdict_module()
    world, results = _world_and_results(
        ["passed", "failed", "not_applicable"], weights=(None, None, None)
    )
    assert [verdict_module.outcome_of_result(r) for r in results] == [
        "passed",
        "failed",
        "not_applicable",
    ]
    errored = _result_of(world["definitions"][0], "errored")
    assert verdict_module.outcome_of_result(errored) == "errored"
    doc = _verdict_for(world, results)
    _validate(doc, world, results)
    for index, wrong in ((0, "failed"), (1, "passed"), (2, "errored"), (0, "not_applicable")):
        broken = _verdict_for(world, results)
        broken["checks"][index] = {**broken["checks"][index], "outcome": wrong}
        counts = {o: [c["outcome"] for c in broken["checks"]].count(o) for o in OUTCOMES}
        broken.update(
            passed=counts["passed"],
            failed=counts["failed"],
            not_applicable=counts["not_applicable"],
            errored=counts["errored"],
            status=_status(counts["passed"], counts["failed"], len(broken["checks"])),
        )
        with pytest.raises(ValueError, match="outcome"):
            _validate(broken, world, results)
    with pytest.raises(ValueError, match="result"):
        _validate(doc, world, results[:2])
    other_metric = _verdict_for(world, results)
    other_metric["checks"][0] = {**other_metric["checks"][0], "result_id": str(results[1].id)}
    with pytest.raises(ValueError, match="result"):
        _validate(other_metric, world, results)
    # also when both results have the same outcome: the result must be a result of the metric
    twin_world, twin_results = _world_and_results(["passed", "passed"], weights=(None, None))
    twin = _verdict_for(twin_world, twin_results)
    _validate(twin, twin_world, twin_results)
    twin["checks"][0] = {**twin["checks"][0], "result_id": str(twin_results[1].id)}
    with pytest.raises(ValueError, match="result"):
        _validate(twin, twin_world, twin_results)


def test_verdict_check_metric_ref_is_typed_and_equals_binding_metric() -> None:
    """Proposed name (ADR 0013 Decision 2, api-surface 19.4 item 1): ``metric_ref`` of a check is a
    ``MetricDefinitionRef`` object and must equal the ``metric`` of the binding named by
    ``binding_id``; another definition, another version or a text such as ``key@version`` is
    rejected. Digests on the reference are verified when present."""
    world, results = _world_and_results(["passed", "failed"], weights=(None, None))
    doc = _verdict_for(world, results)
    _validate(doc, world, results)
    check = doc["checks"][0]
    binding = world["bindings"][0]
    assert check["metric_ref"] == {"id": str(binding.metric.id), "version": binding.metric.version}
    for wrong in (
        {"id": new_id("met"), "version": 1},
        {"id": str(binding.metric.id), "version": 2},
        {"id": str(world["bindings"][1].metric.id), "version": 1},
    ):
        broken = _verdict_for(world, results)
        broken["checks"][0] = {**check, "metric_ref": wrong}
        with pytest.raises(ValueError, match=r"metric_ref|metric"):
            _validate(broken, world, results)
    for text in ("goal_completion@1", f"{binding.metric.id}@1", str(binding.metric.id)):
        broken = _verdict_for(world, results)
        broken["checks"][0] = {**check, "metric_ref": text}
        _reject(broken, "checks")


def test_weighted_score_checked_against_rubric_and_results() -> None:
    """Proposed name (ADR 0013 Decision 8; ``check_verdict``): the stored ``weighted_score``,
    ``weight_coverage`` and ``weighted_excluded`` equal what the rubric's weights and the results
    give (a forged score, a wrong coverage and a wrong exclusion list are rejected), a weighted
    check that is not required still counts (P-34) and an unweighted check is in neither sum."""
    world, results = _world_and_results(["passed", "failed", "not_applicable"])
    doc = _verdict_for(world, results)
    assert (doc["weighted_score"], doc["weight_coverage"]) == ("0.625", "0.8")
    assert [e["outcome"] for e in doc["weighted_excluded"]] == ["not_applicable"]
    _validate(doc, world, results)
    for field, wrong in (
        ("weighted_score", "1"),
        ("weighted_score", "0.5"),
        ("weight_coverage", "1"),
        ("weight_coverage", "0.7"),
    ):
        with pytest.raises(ValueError, match="weight"):
            _validate({**doc, field: wrong}, world, results)
    with pytest.raises(ValueError, match="weight"):
        _validate({**doc, "weighted_excluded": []}, world, results)
    with pytest.raises(ValueError, match="weight"):
        _validate(
            {
                **doc,
                "weighted_excluded": [
                    {"binding_id": str(world["bindings"][1].id), "outcome": "errored"}
                ],
            },
            world,
            results,
        )
    # P-34: a weighted check that is not required is in the score but not in the headline
    optional_world, optional_results = _world_and_results(
        ["passed", "failed", "passed"], required=(True, False, False)
    )
    optional = _verdict_for(optional_world, optional_results)
    assert (optional["required_total"], optional["passed"], optional["failed"]) == (1, 1, 0)
    assert optional["status"] == "pass"
    assert (optional["weighted_score"], optional["weight_coverage"]) == ("0.7", "1")  # (5 + 2) / 10
    _validate(optional, optional_world, optional_results)
    # an unweighted check is in neither sum and never listed as excluded
    unweighted_world, unweighted_results = _world_and_results(
        ["passed", "not_applicable", "failed"], weights=("5", None, "3")
    )
    unweighted = _verdict_for(unweighted_world, unweighted_results)
    assert (unweighted["weighted_score"], unweighted["weight_coverage"]) == ("0.625", "1")
    assert unweighted["weighted_excluded"] == []
    _validate(unweighted, unweighted_world, unweighted_results)
    no_weights_world, no_weights_results = _world_and_results(
        ["passed", "failed"], weights=(None, None)
    )
    no_weights = _verdict_for(no_weights_world, no_weights_results)
    assert (no_weights["weighted_score"], no_weights["weight_coverage"]) == (None, None)
    _validate(no_weights, no_weights_world, no_weights_results)
