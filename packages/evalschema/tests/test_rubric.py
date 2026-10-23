# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Rubric and its validation against bindings and definitions (ADR 0010, 0013 Decisions 2 and 7).

The brief for batch 2 puts Rubric with P1-08; SCOPE section 5 lists the rubric tests under P1-22
and this file keeps those names: ``test_rubric_requires_one_required_metric``,
``test_required_metric_needs_pass_rule``, ``test_required_boolean_check_with_equals_true_ok``,
``test_rubric_weight_requires_pass_rule`` (P-55).

Assumed public API (docs/phase1/api-surface.md section 19), ``tracer_evalschema.rubric``:
``Rubric`` (versioned; ``items: list[RubricItem{binding: {id, version}, required: bool,
weight: DecimalString | None}]``) and ``validate_rubric(rubric, *, bindings, definitions) -> None``
which resolves each item through its binding to the metric definition and raises ``ValueError``
(a pydantic ``ValidationError`` qualifies) naming the offending binding ID.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    assert_rejected_name,
    entity_class,
    equals_true_rule,
    fields_of,
    judge_definition,
    latency_definition,
    make,
    new_id,
    parameter,
    raw_document,
    registered_class,
    schema_of,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    marked_id,
)


def _world(*definitions: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """Definitions and one binding each (on one evaluator), validated entities."""
    evaluator_id = new_id("evl")
    built_definitions = [make("MetricDefinition", **d) for d in definitions]
    bindings = [
        make(
            "MetricBinding",
            evaluator_id=evaluator_id,
            metric={"id": str(d.id), "version": d.version},
        )
        for d in built_definitions
    ]
    return built_definitions, bindings


def _item(binding: Any, *, required: bool = True, weight: Any = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "binding": {"id": str(binding.id), "version": binding.version},
        "required": required,
    }
    if weight is not None:
        item["weight"] = weight
    return item


def _rubric(items: list[dict[str, Any]]) -> Any:
    return make("Rubric", items=items)


def _validate(rubric: Any, definitions: list[Any], bindings: list[Any]) -> None:
    api("rubric").validate_rubric(rubric, bindings=bindings, definitions=definitions)


INFORMATIONAL = {"key": "talk_ratio", "unit": "ratio", "pass_rule": None}


def test_rubric_requires_one_required_metric() -> None:
    """ADR 0013 I6: a rubric has at least one required item (checked by the model itself)."""
    _definitions, bindings = _world(latency_definition(), latency_definition(key="p50_latency"))
    first, second = bindings
    assert _rubric([_item(first)]).items[0].required is True
    assert len(_rubric([_item(first, required=False), _item(second)]).items) == 2
    for items in (
        [],
        [_item(first, required=False)],
        [_item(first, required=False), _item(second, required=False)],
    ):
        assert_rejected_name("Rubric", items=items, field="items")
    assert_rejected_name("Rubric", items=None)
    assert_rejected_name("Rubric", items="x")


def test_required_metric_needs_pass_rule() -> None:
    """ADR 0013 I7: a required item whose definition has no pass rule is rejected, naming the
    binding; with a pass rule it is accepted; an informational metric may stay in the rubric as
    not required."""
    definitions, bindings = _world(latency_definition(), latency_definition(**INFORMATIONAL))
    gated, informational = bindings
    _validate(_rubric([_item(gated), _item(informational, required=False)]), definitions, bindings)
    with pytest.raises(ValueError, match=str(informational.id)):
        _validate(_rubric([_item(gated), _item(informational)]), definitions, bindings)


def test_required_boolean_check_with_equals_true_ok() -> None:
    """ADR 0010 I5: a required boolean check (goal completion, the evaluator's expected outcome)
    with ``equals_true`` satisfies the rule."""
    definitions, bindings = _world(
        judge_definition(),
        judge_definition(key="expected_outcome_met", pass_rule=equals_true_rule()),
    )
    _validate(_rubric([_item(b) for b in bindings]), definitions, bindings)
    bare_definitions, bare_bindings = _world(judge_definition(pass_rule=None))
    with pytest.raises(ValueError, match=str(bare_bindings[0].id)):
        _validate(_rubric([_item(bare_bindings[0])]), bare_definitions, bare_bindings)


def test_rubric_weight_requires_pass_rule() -> None:
    """P-55: a weight on an item whose metric has no pass rule is rejected (it cannot contribute
    to the weighted score); weights are allowed on non-required items with a pass rule (P-34);
    an informational metric stays in the rubric unweighted."""
    definitions, bindings = _world(
        latency_definition(),
        latency_definition(key="p50_latency"),
        latency_definition(**INFORMATIONAL),
    )
    gated, optional, informational = bindings
    ok = _rubric(
        [
            _item(gated, weight="5"),
            _item(optional, required=False, weight="3"),
            _item(informational, required=False),
        ]
    )
    _validate(ok, definitions, bindings)
    for required in (True, False):
        bad = _rubric([_item(gated), _item(informational, required=required, weight="2")])
        with pytest.raises(ValueError, match=str(informational.id)):
            _validate(bad, definitions, bindings)


def test_rubric_weight_values() -> None:
    """Proposed name. Weights are canonical decimal strings strictly greater than 0 (ADR 0013
    Decision 7, revision 8): zero is rejected, because omitting the weight already says "contributes
    nothing" and all-zero weights would make the sum of weights 0; negative and non-canonical
    values are rejected too. Weights are relative: there is no upper bound and no required total.
    The error is located at ``weight``."""
    _definitions, (binding, other) = _world(
        latency_definition(), latency_definition(key="p50_latency")
    )
    for good in ("1", "5", "0.5", "2.25", "100", "0.000001", "100000000000"):
        assert (
            _rubric([_item(binding, weight=good), _item(other, required=False)]).items[0].weight
            == good
        )
    assert _rubric([_item(binding)]).items[0].weight is None
    for bad in (
        "0",
        "0.0",
        "0.00",
        "-0",
        "-1",
        "-0.5",
        "abc",
        "",
        "1e2",
        "5.0",
        "05",
        5,
        5.5,
        0,
        0.0,
        True,
        [1],
    ):
        assert_rejected_name("Rubric", items=[_item(binding, weight=bad)], field="weight")
        assert_rejected_name(
            "Rubric",
            items=[_item(binding), _item(other, required=False, weight=bad)],
            field="weight",
        )


def test_rubric_rejects_duplicate_binding_refs() -> None:
    """ADR 0009 / the batch 2 brief: a binding appears once in a rubric."""
    _definitions, (binding, other) = _world(
        latency_definition(), latency_definition(key="p50_latency")
    )
    assert len(_rubric([_item(binding), _item(other)]).items) == 2
    assert_rejected_name("Rubric", items=[_item(binding), _item(binding)])
    assert_rejected_name(
        "Rubric", items=[_item(binding, weight="1"), _item(binding, required=False)]
    )


def test_rubric_unresolved_references_rejected() -> None:
    """Proposed name. Validation resolves each item through its binding to its definition; a
    binding or definition that is not supplied (or has another version) is rejected, naming it."""
    definitions, bindings = _world(latency_definition(), latency_definition(key="p50_latency"))
    rubric = _rubric([_item(b) for b in bindings])
    _validate(rubric, definitions, bindings)
    with pytest.raises(ValueError, match=str(bindings[1].id)):
        _validate(rubric, definitions, bindings[:1])
    with pytest.raises(ValueError, match=str(definitions[1].id)):
        _validate(rubric, definitions[:1], bindings)
    stale = _rubric([{**_item(bindings[0]), "binding": {"id": str(bindings[0].id), "version": 9}}])
    with pytest.raises(ValueError, match=str(bindings[0].id)):
        _validate(stale, definitions, bindings)


def test_rubric_per_turn_share_metric_can_be_required_and_weighted() -> None:
    """ADR 0013 Decision 8a: a ``per_turn_share`` metric is one check; its pass rule is the range
    on the share, so it may be required and weighted; without that range it may not."""
    share = judge_definition(
        key="polite_turn_share",
        aggregation={"kind": "per_turn_share", "turn_rule": equals_true_rule()},
        pass_rule={"kind": "range", "min": 0.8, "inclusive": True},
    )
    unruled = {**share, "key": "unruled_turn_share", "pass_rule": None}
    definitions, bindings = _world(share, unruled)
    _validate(
        _rubric([_item(bindings[0], weight="4"), _item(bindings[1], required=False)]),
        definitions,
        bindings,
    )
    with pytest.raises(ValueError, match=str(bindings[1].id)):
        _validate(_rubric([_item(bindings[0]), _item(bindings[1])]), definitions, bindings)


def test_rubric_is_versioned_entity() -> None:
    """Proposed name. Rubric (prefix ``rub``) is versioned (ADR 0009 Decision 5)."""
    rubric = entity_class("Rubric")
    assert issubclass(rubric, api("base").VersionedEntity)
    assert rubric.id_prefix == "rub"
    assert raw_document("Rubric")["items"][0]["required"] is True


# ================================================================================ revision 8
# Pins api-surface 19.4 / 19.4.1 (revision 8): ADR 0013 Decision 7 and ADR 0010 Decisions 4 and 4a.


def _binding_for(definition: Any, evaluator_id: str, **over: Any) -> Any:
    return make(
        "MetricBinding",
        evaluator_id=evaluator_id,
        metric={"id": str(definition.id), "version": definition.version},
        **over,
    )


def _names_any(*identifiers: Any) -> str:
    """A ``match`` pattern that accepts a message naming any of the identifiers."""
    return "|".join(re.escape(str(identifier)) for identifier in identifiers)


def test_rubric_item_shape() -> None:
    """Section 19.4.1: ``RubricItem{binding: MetricBindingRef, required: bool, weight:
    DecimalString?}`` is a value object; ``required`` is a strict required boolean; the binding is a
    reference object of the binding kind (a typed ``MetricBindingRef``, never a string). Proposed
    name."""
    item = registered_class("RubricItem")
    assert fields_of(item) == {"binding", "required", "weight"}
    assert not {"id", "schema_version", "created_at", "metadata"} & fields_of(item)
    schema = schema_of(item)
    assert set(schema["required"]) == {"binding", "required"}
    assert schema.get("additionalProperties") is False
    _definitions, (binding,) = _world(latency_definition())
    good = _item(binding)
    loaded = item.model_validate(good)
    assert isinstance(loaded.binding, registered_class("MetricBindingRef"))
    assert loaded.weight is None
    for bad in ({**good, "required": "yes"}, {**good, "required": 1}, {**good, "extra": 1}):
        with pytest.raises(ValidationError):
            item.model_validate(bad)
    for missing in ("binding", "required"):
        with pytest.raises(ValidationError):
            item.model_validate({k: v for k, v in good.items() if k != missing})
    for bad_binding in (
        str(binding.id),
        f"{binding.id}@1",
        {"id": new_id("met"), "version": 1},
        {"id": new_id("evl"), "version": 1},
        {"id": str(binding.id)},
        None,
    ):
        assert_rejected_name("Rubric", items=[{**good, "binding": bad_binding}], field="binding")


def test_rubric_rejects_disabled_binding() -> None:
    """ADR 0013 Decision 7 (revision 8), resolution 8: ``validate_rubric`` rejects an item whose
    binding has ``enabled=false``, whether the item is required or not and weighted or not, and the
    error names the binding ID. A disabled binding writes no result, so as a required check it would
    make every run inconclusive, and as a weighted check it would keep the coverage below 1, both
    for a reason fixed when the rubric was written. A disabled binding that no item references is
    fine, and the evaluator may still list it."""
    evaluator_id = new_id("evl")
    gated = make("MetricDefinition", **latency_definition())
    optional = make("MetricDefinition", **latency_definition(key="p50_latency"))
    definitions = [gated, optional]
    on = _binding_for(gated, evaluator_id)
    off = _binding_for(optional, evaluator_id, enabled=False)
    also_off = _binding_for(gated, evaluator_id, enabled=False)
    assert off.enabled is False
    _validate(_rubric([_item(on)]), definitions, [on, off])
    for required in (True, False):
        for weight in (None, "3"):
            rubric = _rubric([_item(on), _item(off, required=required, weight=weight)])
            with pytest.raises(ValueError, match=str(off.id)):
                _validate(rubric, definitions, [on, off])
    # the only required item is the disabled one
    with pytest.raises(ValueError, match=str(also_off.id)):
        _validate(_rubric([_item(also_off)]), definitions, [also_off])
    # switching the same binding on makes the same rubric valid
    enabled_again = _binding_for(gated, evaluator_id, enabled=True)
    _validate(_rubric([_item(enabled_again)]), definitions, [enabled_again])
    # the evaluator may list a disabled binding; uniqueness counts it
    assert api("metrics").check_bindings_unique([on, off]) is None
    assert len(make("Evaluator", metric_bindings=[_ref_of(on), _ref_of(off)]).metric_bindings) == 2


def _ref_of(binding: Any) -> dict[str, Any]:
    return {"id": str(binding.id), "version": binding.version}


def test_bound_definition_keys_unique_per_evaluator() -> None:
    """Resolution 1 (ADR 0009 Decision 4, ADR 0013 Decision 7): keys do not identify definitions,
    but the definitions bound to one evaluator must have distinct keys, because the key is what a
    report shows (``p95_latency@3``). ``validate_rubric`` rejects two items that resolve to the
    same definition ``id`` (also at two versions) or to two definitions with the same ``key``, and
    names the binding or definition. Only definitions that an item resolves to count."""
    evaluator_id = new_id("evl")
    first = make("MetricDefinition", **latency_definition())
    same_key = make("MetricDefinition", **latency_definition(unit="count"))
    other_key = make("MetricDefinition", **latency_definition(key="p50_latency"))
    assert first.key == same_key.key != other_key.key
    a, b, c = (_binding_for(d, evaluator_id) for d in (first, same_key, other_key))
    _validate(_rubric([_item(a), _item(c, required=False)]), [first, other_key], [a, c])
    # two definitions with one key
    rubric = _rubric([_item(a), _item(b, required=False)])
    with pytest.raises(ValueError, match=_names_any(a.id, b.id, first.id, same_key.id)):
        _validate(rubric, [first, same_key], [a, b])
    # an unreferenced definition with the same key does not count
    _validate(_rubric([_item(a)]), [first, same_key, other_key], [a, b, c])
    # two bindings of one definition, and of two versions of one definition
    twice = _binding_for(first, evaluator_id)
    with pytest.raises(ValueError, match=_names_any(a.id, twice.id, first.id)):
        _validate(_rubric([_item(a), _item(twice, required=False)]), [first], [a, twice])
    renamed = latency_definition(key="p99_latency")
    newer = make("MetricDefinition", **renamed, id=str(first.id), version=2)
    assert newer.key != first.key, "only the definition id can be the reason"
    on_newer = _binding_for(newer, evaluator_id)
    with pytest.raises(ValueError, match=_names_any(a.id, on_newer.id, first.id)):
        _validate(
            _rubric([_item(a), _item(on_newer, required=False)]), [first, newer], [a, on_newer]
        )


def test_validate_rubric_rejects_bindings_of_more_than_one_evaluator() -> None:
    """Section 19.4.1: a rubric belongs to one evaluator, so ``validate_rubric`` rejects items whose
    bindings belong to more than one evaluator, naming a binding. Proposed name."""
    first = make("MetricDefinition", **latency_definition())
    second = make("MetricDefinition", **latency_definition(key="p50_latency"))
    here = _binding_for(first, new_id("evl"))
    there = _binding_for(second, new_id("evl"))
    rubric = _rubric([_item(here), _item(there, required=False)])
    with pytest.raises(ValueError, match=_names_any(here.id, there.id)):
        _validate(rubric, [first, second], [here, there])
    same = _binding_for(second, here.evaluator_id)
    _validate(_rubric([_item(here), _item(same, required=False)]), [first, second], [here, same])


def test_validate_rubric_checks_binding_params_against_the_definition() -> None:
    """Resolution 4 and E6 (ADR 0010 Decision 4a): ``validate_rubric`` calls
    ``check_binding_params`` for every item, so an undeclared parameter, a wrongly typed value or a
    missing required parameter is rejected with the binding ID and the key, and a binding cannot
    carry a pass threshold (``pass_rule``, ``max``, ...) because it is not a declared parameter."""
    evaluator_id = new_id("evl")
    declared = [parameter("percentile", "integer"), parameter("lenient", "boolean", default=False)]
    definition = make("MetricDefinition", **latency_definition(parameters=declared))

    def check(params: dict[str, Any]) -> None:
        binding = _binding_for(definition, evaluator_id, params=params)
        _validate(_rubric([_item(binding)]), [definition], [binding])

    check({"percentile": 95})
    check({"percentile": 99, "lenient": True})
    for params, key in (
        ({}, "percentile"),
        ({"percentile": "95"}, "percentile"),
        ({"percentile": True}, "percentile"),
        ({"percentile": 95, "lenient": 1}, "lenient"),
        ({"percentile": 95, "p99": 1}, "p99"),
        (
            {"percentile": 95, "pass_rule": {"kind": "range", "max": 1500, "inclusive": True}},
            "pass_rule",
        ),
        ({"percentile": 95, "max": 1500}, "max"),
    ):
        binding = _binding_for(definition, evaluator_id, params=params)
        with pytest.raises(ValueError, match=str(binding.id)) as caught:
            _validate(_rubric([_item(binding)]), [definition], [binding])
        assert key in str(caught.value)


def test_rubric_unweighted_items_and_no_required_total() -> None:
    """ADR 0013 Decision 7 (revision 8), rubric-level part: an item without a weight is valid,
    required or not, and is simply not a weighted check (it is in neither sum of the weighted score
    or of the coverage and never listed as excluded, which is the verdict's rule, P1-22). Weights
    are relative: no total is required and a rubric may have no weight at all. Proposed name."""
    definitions, (a, b, c) = _world(
        latency_definition(),
        latency_definition(key="p50_latency"),
        latency_definition(key="talk_ratio", unit="ratio", pass_rule=None),
    )
    unweighted = _rubric([_item(a), _item(b, required=False), _item(c, required=False)])
    assert [item.weight for item in unweighted.items] == [None, None, None]
    _validate(unweighted, definitions, [a, b, c])
    for weights in (("0.3", "0.9"), ("7", "1"), ("1000", "0.001"), ("1", "1")):
        rubric = _rubric(
            [
                _item(a, weight=weights[0]),
                _item(b, required=False, weight=weights[1]),
                _item(c, required=False),
            ]
        )
        assert [i.weight for i in rubric.items] == [*weights, None]
        _validate(rubric, definitions, [a, b, c])
    mixed = _rubric([_item(a), _item(b, required=False, weight="2")])
    assert [i.weight for i in mixed.items] == [None, "2"]
    assert "weight" not in schema_of(registered_class("RubricItem"))["required"]


def test_rubric_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected weight
    (the ``DecimalString`` message) and a rejected typed ID, as the rubric's own ID or inside an
    item's binding reference, do not appear in ``str``, ``repr``, ``errors(include_input=False)``
    or ``json(include_input=False)``, through ``model_validate`` and ``model_validate_json``, and
    the error is still located at the field. Proposed name (SCOPE section 5, P1-08 and P1-22)."""
    rubric = entity_class("Rubric")
    item = {"binding": {"id": new_id("mbd"), "version": 1}, "required": True}
    bad_binding = {"id": marked_id("mbd"), "version": 1}
    cases: list[tuple[str, dict[str, Any], str]] = [
        (
            "DecimalString",
            raw_document("Rubric", items=[{**item, "weight": "x" + QUOTE_MARK}]),
            "weight",
        ),
        (
            "DecimalString",
            raw_document("Rubric", items=[{**item, "weight": "1.5" + QUOTE_MARK}]),
            "weight",
        ),
        ("typed ID", raw_document("Rubric", id=marked_id("rub")), "id"),
        ("typed ID", raw_document("Rubric", items=[{**item, "binding": bad_binding}]), "binding"),
    ]
    for label, doc, field in cases:
        assert_input_hidden(rubric, doc, QUOTE_MARK, field=field, label=label)
    # A decimal that is valid but not canonical: outside a model that hides its input the message
    # names the input and the canonical spelling (test_decimal_canonical), inside Rubric it names
    # the rule only. A digit string cannot hold the marker, so the weight itself is the marker.
    non_canonical = raw_document("Rubric", items=[{**item, "weight": "7.250"}])
    assert_input_hidden(rubric, non_canonical, "7.250", field="weight", label="not canonical")
    too_precise = "0.1234567890123456781"  # 19 fractional digits, one more than the limit of 18
    over_scale = raw_document("Rubric", items=[{**item, "weight": too_precise}])
    assert_input_hidden(rubric, over_scale, too_precise, field="weight", label="scale")
