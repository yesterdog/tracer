# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 1 gate decision A-C6: a run-level ``not_applicable`` result has no per-turn summary.

ADR 0004 Decisions 3 to 5 and ADR 0010 Decision 3: ``missing_input`` and ``agent_channel_unknown``
are decided from the run alone, before any turn is read ("run-level"; ``bundle._RUN_LEVEL_REASONS``,
the same pair the ``[applicability]`` rule calls a run-level answer). No turn was looked at, so
there is no per-turn outcome to summarise: a ``per_turn`` summary on such a result claims turns were
counted that were not. The other three reasons (``language_not_observed``, ``unsupported_language``,
``unsupported_script``) are derived from the turns, so a ``per_turn_share`` result in which every
turn was not applicable keeps its summary (counted 0, undetermined 0), exactly as before.

Contract (the request to the architect, mirrored in the report of this batch):

* ``MetricResult`` rejects ``status=not_applicable`` with ``na_reason`` in
  {``missing_input``, ``agent_channel_unknown``} and a non-null ``per_turn``.
* The error is located at the field ``("per_turn",)`` (the input is not echoed: ``MetricResult``
  hides input), has error type ``metric_result`` (like every rule of ``_status_rules``) and its
  message names the rule: it contains ``per_turn`` and ``na_reason=<the reason>`` (the same
  wording style as the ``language_source`` rule next to it).
* The generated JSON Schema of ``MetricResult`` says the same (``per_turn`` is ``null`` or absent
  when ``na_reason`` is one of the two), as it mirrors every presence rule of the model.

Fails today for the right reason: the model accepts the summary (``DID NOT RAISE``). The guards
(legal records) pass today and must keep passing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import equals_true_rule, judge_definition, make, schema_accepts
from ._support_b3 import (
    assert_input_hidden,
    assert_rejected3,
    entity_class3,
    judge_info_doc,
    per_turn_summary_doc,
    result_doc,
)

RUN_LEVEL = ("missing_input", "agent_channel_unknown")
TURN_DERIVED = ("language_not_observed", "unsupported_language", "unsupported_script")


def _model() -> Any:
    return entity_class3("MetricResult")


def _definition() -> Any:
    return make(
        "MetricDefinition",
        **judge_definition(
            aggregation={"kind": "per_turn_share", "turn_rule": equals_true_rule()},
            pass_rule={"kind": "range", "min": 0.75, "inclusive": True},
        ),
    )


def _na(reason: str, per_turn: dict[str, Any] | None, **over: Any) -> dict[str, Any]:
    """A not_applicable result of the per-turn definition with ``reason`` and ``per_turn``."""
    base: dict[str, Any] = {
        "status": "not_applicable",
        "value": None,
        "passed": None,
        "judge": None,
        "language_source": None,
        "na_reason": reason,
        "per_turn": per_turn,
    }
    if reason == "missing_input":
        base["missing_kinds"] = ["transcript"]
    return result_doc(_definition(), **{**base, **over})


def _all_not_applicable(total: int) -> dict[str, Any]:
    return per_turn_summary_doc(passed=0, failed=0, not_applicable=total, undetermined=0)


def _names_the_rule(error: Any, reason: str) -> bool:
    message = str(error["msg"])
    return "per_turn" in message and f"na_reason={reason}" in message


def _errors_at_per_turn(error: ValidationError) -> list[Any]:
    return [e for e in error.errors() if [str(p) for p in e["loc"]][:1] == ["per_turn"]]


def test_the_reason_sets_cover_every_not_applicable_reason() -> None:
    """The two groups below are the whole closed set (ADR 0004 Decision 4): a new reason must be
    classified as run-level or turn-derived here, not silently fall into one of them."""
    reasons = api("results").NA_REASONS
    assert sorted(reasons) == sorted((*RUN_LEVEL, *TURN_DERIVED))


@pytest.mark.parametrize("reason", RUN_LEVEL)
@pytest.mark.parametrize("total", [1, 4, 250])
def test_run_level_not_applicable_result_has_no_per_turn_summary(reason: str, total: int) -> None:
    """A-C6: a run-level not_applicable result that carries a ``per_turn`` summary is rejected, at
    the field ``per_turn``, with the rule ``metric_result`` and a message that says why."""
    error = assert_rejected3(_model(), _na(reason, _all_not_applicable(total)), field="per_turn")
    found = [e for e in _errors_at_per_turn(error) if _names_the_rule(e, reason)]
    assert found, error.errors(include_input=False)
    assert all(e["type"] == "metric_result" for e in found)
    # nothing else is wrong with the record: no other field is reported
    others = [e for e in error.errors() if [str(p) for p in e["loc"]][:1] != ["per_turn"]]
    assert others == [], others


@pytest.mark.parametrize("reason", RUN_LEVEL)
@pytest.mark.parametrize(
    "counts",
    [
        (0, 0, 3, 0),  # every turn not applicable (the shape a per-turn reason carries)
        (0, 0, 2, 3),  # undetermined turns (an error shape)
        (3, 1, 0, 0),  # scored counts
    ],
)
def test_run_level_summary_is_rejected_whatever_the_counts(
    reason: str, counts: tuple[int, int, int, int]
) -> None:
    """The rule does not depend on the counts: no turn was read, so no summary fits."""
    summary = per_turn_summary_doc(*counts)
    error = assert_rejected3(_model(), _na(reason, summary), field="per_turn")
    assert any(_names_the_rule(e, reason) for e in _errors_at_per_turn(error)), error.errors(
        include_input=False
    )


@pytest.mark.parametrize("reason", RUN_LEVEL)
def test_run_level_rejection_does_not_echo_the_summary(reason: str) -> None:
    """The rejection names the rule, never the counts (the model hides input)."""
    for marker in ("8675309", "9182736", "4455667"):
        summary = _all_not_applicable(int(marker))
        doc = _na(reason, summary)
        rest = json.dumps({k: v for k, v in doc.items() if k != "per_turn"})
        if marker in rest:
            continue
        errors = assert_input_hidden(_model(), doc, marker, field="per_turn", label=reason)
        for error in errors:
            assert any(_names_the_rule(e, reason) for e in error.errors()), reason
        return
    raise AssertionError("every marker collided with the random IDs of the record")


@pytest.mark.parametrize("reason", RUN_LEVEL)
def test_run_level_result_without_summary_loads(reason: str) -> None:
    """Guard: the legal run-level record (no ``per_turn``, absent or null) still loads."""
    model = _model()
    with_null = model.model_validate(_na(reason, None))
    assert with_null.per_turn is None
    absent = {k: v for k, v in _na(reason, None).items() if k != "per_turn"}
    assert model.model_validate(absent).per_turn is None


@pytest.mark.parametrize("reason", TURN_DERIVED)
@pytest.mark.parametrize("total", [1, 4, 250])
def test_turn_derived_not_applicable_result_keeps_its_per_turn_summary(
    reason: str, total: int
) -> None:
    """Guard (ADR 0010 Decision 3): when every turn was not applicable for a language reason the
    result is not_applicable and still carries the summary (counted 0, undetermined 0)."""
    loaded = _model().model_validate(_na(reason, _all_not_applicable(total)))
    assert loaded.status == "not_applicable"
    assert loaded.per_turn is not None
    assert loaded.per_turn.total_turns == total
    assert loaded.per_turn.counts.not_applicable == total


def test_other_statuses_keep_their_per_turn_summary() -> None:
    """Guard: the rule is about not_applicable results only: a scored per-turn result, and an
    error ``judge_undetermined`` result with its summary, are untouched."""
    model = _model()
    scored = result_doc(
        _definition(),
        value=None,
        passed=True,
        per_turn=per_turn_summary_doc(6, 2, 1, 1),
        judge=judge_info_doc(),
    )
    assert model.model_validate(scored).per_turn is not None
    undetermined = result_doc(
        _definition(),
        status="error",
        value=None,
        passed=None,
        judge=None,
        per_turn=per_turn_summary_doc(0, 0, 2, 3),
        error={"code": "judge_undetermined", "message": "undetermined"},
    )
    assert model.model_validate(undetermined).per_turn is not None


@pytest.mark.parametrize("reason", RUN_LEVEL)
def test_run_level_summary_is_rejected_by_the_json_schema_too(reason: str) -> None:
    """The generated schema mirrors every presence rule of the model (``_result_conditions``): it
    rejects the run-level record with a summary and accepts the same record without one."""
    model = _model()
    assert not schema_accepts(model, _na(reason, _all_not_applicable(4)))
    assert schema_accepts(model, _na(reason, None))


@pytest.mark.parametrize("reason", TURN_DERIVED)
def test_turn_derived_summary_is_accepted_by_the_json_schema(reason: str) -> None:
    """Guard: the schema keeps accepting the legal per-turn not_applicable records."""
    assert schema_accepts(_model(), _na(reason, _all_not_applicable(4)))
