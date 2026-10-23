# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Rubric: which bound metrics a verdict counts, which are required, and their weights.

ADR 0013 Decision 7 and ADR 0010 Decisions 4 and 4a.

- ``RubricItem{binding: MetricBindingRef, required, weight?}``: a weight is a canonical decimal
  strictly greater than 0; an item without a weight is not a weighted check.
- ``Rubric`` (``rub``, versioned): non-empty items, at least one required, no binding ``id``
  twice (at any version).
- ``validate_rubric(rubric, *, bindings, definitions)``: resolves item -> binding -> definition
  and checks what needs those records (unresolved or other-version references, a digest on a
  reference that disagrees, bindings of more than one evaluator, a disabled binding, one
  definition or one key reached twice, a required or weighted item without a pass rule, and the
  binding's parameters). Every ``ValueError`` names the binding or definition ID.

``Rubric`` hides its input in validation errors and no message inside it quotes a rejected value
(ADR 0007 Decision 8 (b) and (c); N41, N42).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, ClassVar

from pydantic import AfterValidator, ConfigDict, Field, Strict, WithJsonSchema
from pydantic_core import PydanticCustomError

from tracer_evalschema._constraints import echoed
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema.base import VersionedEntity
from tracer_evalschema.digest import ContentDigest
from tracer_evalschema.ids import EvaluatorId, MetricBindingId, TypedId
from tracer_evalschema.metrics import (
    MetricBinding,
    MetricBindingRef,
    MetricDefinition,
    check_binding_params,
)
from tracer_evalschema.primitives import DecimalString

__all__ = [
    "POSITIVE_DECIMAL_PATTERN",
    "PositiveDecimalString",
    "Rubric",
    "RubricItem",
    "validate_rubric",
]

POSITIVE_DECIMAL_PATTERN = r"^(?:[1-9][0-9]*(?:\.[0-9]{0,17}[1-9])?|0\.[0-9]{0,17}[1-9])$"
"""The canonical decimals strictly greater than 0 (``DecimalString`` without ``0`` and ``-``)."""


def _positive(text: str) -> str:
    if Decimal(text) <= 0:
        raise PydanticCustomError(
            "weight_not_positive",
            "a weight is strictly greater than 0 (omit the weight for an unweighted item)",
        )
    return text


PositiveDecimalString = Annotated[
    DecimalString,
    AfterValidator(_positive),
    WithJsonSchema({"type": "string", "pattern": POSITIVE_DECIMAL_PATTERN}),
]
"""A canonical decimal string strictly greater than 0."""


class RubricItem(HidingModel):
    """One check of a rubric: a binding, whether it is required, and an optional weight."""

    # Embeds a ContentDigest (and so a KeyRef), so it hides its input (ADR 0007 Decision 8 (b),
    # P1-21 C3b closure rule).
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    binding: MetricBindingRef
    required: Annotated[bool, Strict()]
    weight: PositiveDecimalString | None = None


def _check_items(items: list[RubricItem]) -> list[RubricItem]:
    if not any(item.required for item in items):
        raise ValueError("a rubric needs at least one required item")
    seen: set[MetricBindingId] = set()
    for position, item in enumerate(items):
        if item.binding.id in seen:
            if values_hidden():
                raise ValueError(
                    f"a binding appears twice in the rubric (again at item {position})"
                )
            raise ValueError(f"binding {item.binding.id} appears twice in the rubric")
        seen.add(item.binding.id)
    return items


class Rubric(HidingModel, VersionedEntity):
    """The checks of a verdict (prefix ``rub``; ADR 0013 Decision 7)."""

    id_prefix: ClassVar[str] = "rub"

    items: Annotated[
        list[RubricItem],
        Field(min_length=1),
        AfterValidator(_check_items),
    ]


# ------------------------------------------------------------------------------ resolution


def _digest_problem(
    reference: ContentDigest | None, actual: ContentDigest, what: str
) -> str | None:
    """A reference's digest is verified against the resolved record when present (ADR 0009 I7).

    Equal digests match. Two digests of one scheme (and one key) that differ do not. Any other
    pair cannot be compared without a key here and is never reported as matching.
    """
    if reference is None or reference == actual:
        return None
    if reference.scheme == actual.scheme and reference.key_ref == actual.key_ref:
        return f"the reference to {what} carries a content_digest that does not match it"
    return f"the content_digest on the reference to {what} cannot be verified here"


def _resolve(
    wanted: TypedId, version: int, records: Sequence[MetricBinding] | Sequence[MetricDefinition]
) -> MetricBinding | MetricDefinition | None:
    for record in records:
        if record.id == wanted and record.version == version:
            return record
    return None


def validate_rubric(
    rubric: Rubric,
    *,
    bindings: Sequence[MetricBinding],
    definitions: Sequence[MetricDefinition],
) -> None:
    """Check ``rubric`` against the bindings and definitions its items resolve to.

    Raises ``ValueError`` naming the binding (or definition) ID of the first item that breaks a
    rule (ADR 0013 Decision 7).
    """
    # Entity ids are typed by the subclass at run time; statically they are TypedId.
    evaluators: dict[EvaluatorId, TypedId] = {}
    reached: dict[TypedId, TypedId] = {}
    keys: dict[str, TypedId] = {}
    for item in rubric.items:
        ref = item.binding
        binding = _resolve(ref.id, ref.version, bindings)
        if not isinstance(binding, MetricBinding):
            raise ValueError(f"binding {ref.id} version {ref.version} is not supplied")
        problem = _digest_problem(ref.content_digest, binding.content_digest, f"binding {ref.id}")
        if problem is not None:
            raise ValueError(problem)
        if not binding.enabled:
            raise ValueError(
                f"binding {binding.id} is disabled: a disabled binding writes no result, so it "
                "cannot be a rubric item"
            )
        evaluators.setdefault(binding.evaluator_id, binding.id)
        if len(evaluators) > 1:
            first = next(iter(evaluators.values()))
            raise ValueError(
                f"bindings {first} and {binding.id} belong to different evaluators; a rubric "
                "belongs to one evaluator"
            )
        metric = binding.metric
        definition = _resolve(metric.id, metric.version, definitions)
        if not isinstance(definition, MetricDefinition):
            raise ValueError(
                f"definition {metric.id} version {metric.version} (bound by binding "
                f"{binding.id}) is not supplied"
            )
        problem = _digest_problem(
            metric.content_digest, definition.content_digest, f"definition {metric.id}"
        )
        if problem is not None:
            raise ValueError(f"binding {binding.id}: {problem}")
        if definition.id in reached:
            raise ValueError(
                f"bindings {reached[definition.id]} and {binding.id} both resolve to definition "
                f"{definition.id}"
            )
        reached[definition.id] = binding.id
        if definition.key in keys:
            key = "one key" if values_hidden() else f"the key {echoed(definition.key)}"
            raise ValueError(
                f"bindings {keys[definition.key]} and {binding.id} resolve to two definitions "
                f"with {key} (definition {definition.id}); keys are unique per evaluator"
            )
        keys[definition.key] = binding.id
        if definition.pass_rule is None and (item.required or item.weight is not None):
            role = "required" if item.required else "weighted"
            raise ValueError(
                f"binding {binding.id} is {role}, but definition {definition.id} has no pass rule"
            )
        check_binding_params(binding, definition)
