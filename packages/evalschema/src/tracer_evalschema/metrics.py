# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Metric definitions, pass rules, bindings and per-turn counts (ADR 0003, 0004, 0010).

- ``InputKind``: the closed set of inputs a metric may require (ADR 0003, ADR 0004 Decision 1).
- Pass rules (P-25): ``RangeRule``, ``EqualsTrueRule`` and ``OneOfRule``, the union ``PassRule``
  discriminated by ``kind``, and ``rule_passes``: an exact ``Decimal`` comparison without
  coercion.
- ``PerTurnCounts``, ``per_turn_share`` and ``per_turn_status`` (P-26, P-56, P-58).
- ``MetricDefinition`` (``met``, versioned) with its value objects ``RatingScale``,
  ``EnumScale``, ``PerConversationAggregation``, ``PerTurnShareAggregation`` and
  ``MetricParameter``, and the field conditions of ADR 0010 Decision 1a.
- ``MetricBinding`` (``mbd``, versioned), the typed references ``MetricDefinitionRef`` and
  ``MetricBindingRef``, ``check_bindings_unique``, ``check_binding_params`` (E6) and
  ``render_metric_ref`` (display text only; a reference is never parsed from text).

Rules that need several fields are checked by a model validator and reported at the field they
are about. The kind, scale, unit and language conditions are also emitted in the JSON Schema as
``allOf`` of ``if``/``then``. The code-only rules (api-surface 19.6 N12) are: template variables
equal to ``prompt_variables``, ``one_of`` options within the scale, uniqueness by primary
language, range bounds within the value domain, and ``min < max`` of a rating scale.

``MetricDefinition`` and ``MetricBinding`` hide their input in validation errors (ADR 0007
Decision 8 (b), finding N41 decided by the owner), and no message inside them quotes a rejected
value (Decision 8 (c), finding N42): the union tags of ``PassRule`` and ``Aggregation`` report the
allowed tags, never the tag found.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Mapping, Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Annotated, Any, ClassVar, Final, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    Strict,
    Tag,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._constraints import ClosedSet, FullMatch
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import VersionedEntity, VersionedRef
from tracer_evalschema.ids import EvaluatorId, MetricBindingId, MetricDefinitionId
from tracer_evalschema.ijson import MAX_SAFE_INTEGER, IJsonValue
from tracer_evalschema.language import SupportedLanguage, primary_language
from tracer_evalschema.primitives import canonical_decimal_text
from tracer_evalschema.provider import ModelRef
from tracer_evalschema.text import METRIC_KEY_PATTERN, MetricKey, VerbatimText

__all__ = [
    "AGGREGATION_KINDS",
    "INPUT_KINDS",
    "LANGUAGE_INDEPENDENT",
    "METRIC_KINDS",
    "PARAMETER_VALUE_TYPES",
    "UNITS",
    "VALUE_TYPES",
    "Aggregation",
    "EnumScale",
    "EqualsTrueRule",
    "InputKind",
    "MetricBinding",
    "MetricBindingRef",
    "MetricDefinition",
    "MetricDefinitionRef",
    "MetricParameter",
    "OneOfRule",
    "PassRule",
    "PerConversationAggregation",
    "PerTurnCounts",
    "PerTurnShareAggregation",
    "RangeRule",
    "RatingScale",
    "check_binding_params",
    "check_bindings_unique",
    "per_turn_share",
    "per_turn_status",
    "render_metric_ref",
    "rule_passes",
    "template_variables",
]

T = TypeVar("T", bound=Hashable)


def _unique(what: str) -> Callable[[list[T]], list[T]]:
    """An after-validator that rejects a list holding one value twice."""

    def check(values: list[T]) -> list[T]:
        seen: set[T] = set()
        for position, value in enumerate(values):
            if value in seen:
                if values_hidden():
                    raise ValueError(f"{what} lists one value twice (again at position {position})")
                raise ValueError(f"{what} lists {value!r} twice")
            seen.add(value)
        return values

    return check


def _kind_of(value: Any) -> str | None:
    """The ``kind`` tag of a union member, from a document or a model."""
    kind = value.get("kind") if isinstance(value, Mapping) else getattr(value, "kind", None)
    return kind if isinstance(kind, str) else None


# ---------------------------------------------------------------------------------- InputKind

INPUT_KINDS: Final = (
    "audio",
    "agent_audio",
    "transcript",
    "agent_transcript",
    "turn_timing",
    "tool_calls",
    "tool_results",
    "spans",
    "usage",
    "reference_text",
    "expected_tool_calls",
)

InputKind = Annotated[str, ClosedSet(INPUT_KINDS)]
"""An input a metric can require and a run can have (closed, ADR 0003 Decision 1)."""


# ------------------------------------------------------------------------------- pass rules


def _finite_number(value: Any) -> int | float:
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise PydanticCustomError("finite_number", "a bound must be within +-(2**53 - 1)")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise PydanticCustomError(
        "finite_number", "a bound must be a finite JSON number (not a boolean, text or NaN)"
    )


class _FiniteNumberRule:
    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(_finite_number)

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "number"}


FiniteNumber = Annotated[int | float, _FiniteNumberRule()]
"""A finite JSON number, kept as given (``int`` or ``float``); ``bool`` and text are rejected."""


def as_decimal(value: int | float | Decimal) -> Decimal:
    """The exact decimal of a number; a float is read through its shortest round-trip text."""
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def _range_needs_a_bound(schema: dict[str, Any]) -> None:
    schema["anyOf"] = [
        {"required": [bound], "properties": {bound: {"type": "number"}}} for bound in ("min", "max")
    ]


class RangeRule(BaseModel):
    """``range{min?, max?, inclusive}``: ``max`` alone means lower is better, ``min`` alone higher
    is better, both a target band. ``inclusive`` applies to both bounds."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_range_needs_a_bound)

    kind: Annotated[str, ClosedSet(("range",))]
    min: FiniteNumber | None = None
    max: FiniteNumber | None = None
    inclusive: Annotated[bool, Strict()]

    @model_validator(mode="after")
    def _bounds(self) -> RangeRule:
        if self.min is None and self.max is None:
            raise ValueError("a range needs min, max or both (a rule without a direction)")
        if (
            self.min is not None
            and self.max is not None
            and as_decimal(self.min) > as_decimal(self.max)
        ):
            raise ValueError("a range needs min <= max")
        return self


class EqualsTrueRule(BaseModel):
    """``equals_true``: a boolean passes iff it is ``true``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(("equals_true",))]


class OneOfRule(BaseModel):
    """``one_of{options}``: an enum value passes iff it is one of the distinct ``options``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(("one_of",))]
    options: Annotated[
        list[Annotated[str, Strict()]],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_unique("options")),
    ]


PASS_RULE_KINDS: Final = ("range", "equals_true", "one_of")


def _tag_error(what: str, tags: tuple[str, ...]) -> dict[str, Any]:
    """The custom error of a tagged union: the allowed tags, never the tag found (pydantic's own
    message quotes it; ADR 0007 Decision 8 (c), finding N42)."""
    return {
        "custom_error_type": "union_tag",
        "custom_error_message": f"{what} must be an object whose kind is one of {', '.join(tags)}",
        "custom_error_context": {"allowed": ", ".join(tags)},
    }


PassRule = Annotated[
    Annotated[RangeRule, Tag("range")]
    | Annotated[EqualsTrueRule, Tag("equals_true")]
    | Annotated[OneOfRule, Tag("one_of")],
    Discriminator(_kind_of, **_tag_error("a pass rule", PASS_RULE_KINDS)),
]
"""A pass rule: exactly one of the three kinds (P-25)."""


def rule_passes(rule: RangeRule | EqualsTrueRule | OneOfRule, value: Any) -> bool:
    """Whether ``value`` passes ``rule``, without coercion.

    ``range`` takes an ``int``, ``float`` or ``Decimal`` (never a ``bool``) and compares exactly in
    ``Decimal``; ``equals_true`` takes a ``bool``; ``one_of`` takes a ``str``. Any other value
    raises ``TypeError``. A non-finite number raises ``ValueError``.
    """
    if isinstance(rule, RangeRule):
        if type(value) not in (int, float, Decimal):
            raise TypeError(f"a range rule compares numbers, not {type(value).__name__}")
        number = as_decimal(value)
        if not number.is_finite():
            raise ValueError("a range rule compares finite numbers")
        if rule.min is not None:
            low = as_decimal(rule.min)
            if number < low or (number == low and not rule.inclusive):
                return False
        if rule.max is not None:
            high = as_decimal(rule.max)
            if number > high or (number == high and not rule.inclusive):
                return False
        return True
    if isinstance(rule, EqualsTrueRule):
        if type(value) is not bool:
            raise TypeError(f"equals_true compares booleans, not {type(value).__name__}")
        return value
    if isinstance(rule, OneOfRule):
        if type(value) is not str:
            raise TypeError(f"one_of compares strings, not {type(value).__name__}")
        return value in rule.options
    raise TypeError(f"not a pass rule: {type(rule).__name__}")


# ------------------------------------------------------------------------------ value domains


class _Domain:
    """The values a rule can be applied to: an interval, of integers only or of reals."""

    def __init__(
        self, low: Decimal | None, high: Decimal | None, integers: bool, label: str
    ) -> None:
        self.low, self.high, self.integers, self.label = low, high, integers, label

    def contains(self, bound: Decimal) -> bool:
        return (self.low is None or bound >= self.low) and (self.high is None or bound <= self.high)

    def admits(self, rule: RangeRule) -> bool:
        """Whether some value of the domain passes ``rule`` (its bounds lie in the domain)."""
        lower, lower_closed = self.low, True
        if rule.min is not None:
            lower, lower_closed = as_decimal(rule.min), rule.inclusive
        upper, upper_closed = self.high, True
        if rule.max is not None:
            upper, upper_closed = as_decimal(rule.max), rule.inclusive
        if self.integers:
            first = None
            if lower is not None:
                first = lower.to_integral_value(ROUND_CEILING)
                if not lower_closed and first == lower:
                    first += 1
            last = None
            if upper is not None:
                last = upper.to_integral_value(ROUND_FLOOR)
                if not upper_closed and last == upper:
                    last -= 1
            return first is None or last is None or first <= last
        if lower is None or upper is None:
            return True
        return lower < upper or (lower == upper and lower_closed and upper_closed)


def _interval(low: int | None, high: int | None, integers: bool, label: str) -> _Domain:
    return _Domain(
        None if low is None else Decimal(low),
        None if high is None else Decimal(high),
        integers,
        label,
    )


UNIT_DOMAINS: Final[Mapping[str, _Domain]] = {
    "ms": _interval(0, None, False, "ms (>= 0)"),
    "count": _interval(0, None, True, "count (integers >= 0)"),
    "ratio": _interval(0, 1, False, "ratio ([0, 1])"),
    "percent": _interval(0, 100, False, "percent ([0, 100])"),
    "wer": _interval(0, None, False, "wer (>= 0)"),
    "cer": _interval(0, None, False, "cer (>= 0)"),
}
SHARE_DOMAIN: Final = _interval(0, 1, False, "the share ([0, 1])")
UNITS: Final = tuple(UNIT_DOMAINS)


def _range_problem(rule: RangeRule, domain: _Domain) -> str | None:
    for name, bound in (("min", rule.min), ("max", rule.max)):
        if bound is not None and not domain.contains(as_decimal(bound)):
            shown = "" if values_hidden() else f" {bound!r}"
            return f"the range {name}{shown} lies outside the domain of {domain.label}"
    if not domain.admits(rule):
        return f"no value of {domain.label} passes this range"
    return None


# ------------------------------------------------------------------------------- per turn


Count = Annotated[int, Strict(), Field(ge=0)]


class PerTurnCounts(BaseModel):
    """The per-turn outcomes of a ``per_turn_share`` metric (P-56); all four are required."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: Count
    failed: Count
    not_applicable: Count
    undetermined: Count


_SHARE_SCALE = 18


def per_turn_share(counts: PerTurnCounts) -> str | None:
    """``passed / (passed + failed)`` as a canonical decimal string, or ``None`` if no turn was
    counted (never 0). Not-applicable and undetermined turns are not counted (P-26, P-56). A
    quotient that does not fit 18 fractional digits is rounded half-even at scale 18."""
    counted = counts.passed + counts.failed
    if counted == 0:
        return None
    quotient, remainder = divmod(counts.passed * 10**_SHARE_SCALE, counted)
    if remainder * 2 > counted or (remainder * 2 == counted and quotient % 2 == 1):
        quotient += 1
    value = Decimal(quotient).scaleb(-_SHARE_SCALE)
    return canonical_decimal_text(value)


def per_turn_status(counts: PerTurnCounts) -> Literal["scored", "not_applicable", "error"]:
    """The outcome of a ``per_turn_share`` metric from its counts (ADR 0010 Decision 3).

    ``scored`` if a turn was counted; otherwise ``error`` (code ``judge_undetermined``) if a turn
    is undetermined, else ``not_applicable``. Counts with no turn at all raise ``ValueError``.
    """
    total = counts.passed + counts.failed + counts.not_applicable + counts.undetermined
    if total == 0:
        raise ValueError("per-turn counts with no turn at all have no outcome")
    if counts.passed + counts.failed > 0:
        return "scored"
    return "error" if counts.undetermined > 0 else "not_applicable"


# -------------------------------------------------------------------- definition value objects

METRIC_KINDS: Final = ("deterministic", "llm_judge", "audio", "latency")
VALUE_TYPES: Final = ("boolean", "rating", "enum", "numeric")
AGGREGATION_KINDS: Final = ("per_conversation", "per_turn_share")
PARAMETER_VALUE_TYPES: Final = ("boolean", "integer", "number", "string", "json")
LANGUAGE_INDEPENDENT: Final = "language_independent"

FITTING_RULE: Final[Mapping[str, str]] = {
    "boolean": "equals_true",
    "numeric": "range",
    "rating": "range",
    "enum": "one_of",
}


class RatingScale(BaseModel):
    """The integer scale of a rating metric (``min < max``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min: Annotated[int, Strict()]
    max: Annotated[int, Strict()]

    @model_validator(mode="after")
    def _ordered(self) -> RatingScale:
        if self.min >= self.max:
            raise ValueError("a rating scale needs min < max")
        return self


class EnumScale(BaseModel):
    """The options of an enum metric (non-empty, distinct ``MetricKey`` values)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    options: Annotated[
        list[MetricKey],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_unique("options")),
    ]


class PerConversationAggregation(BaseModel):
    """One value per run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(("per_conversation",))]


class PerTurnShareAggregation(BaseModel):
    """The share of counted turns that pass ``turn_rule`` (P-58); its threshold is the
    definition's ``pass_rule``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(("per_turn_share",))]
    turn_rule: PassRule


Aggregation = Annotated[
    Annotated[PerConversationAggregation, Tag("per_conversation")]
    | Annotated[PerTurnShareAggregation, Tag("per_turn_share")],
    Discriminator(_kind_of, **_tag_error("an aggregation", AGGREGATION_KINDS)),
]


def value_fits(value_type: str, value: Any) -> bool:
    """Whether an I-JSON ``value`` has the parameter ``value_type`` (no bool/number coercion)."""
    if value_type == "boolean":
        return type(value) is bool
    if value_type == "integer":
        return type(value) is int
    if value_type == "number":
        return type(value) in (int, float)
    if value_type == "string":
        return type(value) is str
    return value_type == "json"


class MetricParameter(BaseModel):
    """A parameter a binding may set (ADR 0010 Decision 4a). ``default`` of ``null`` means no
    default; a present default has the declared ``value_type``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: MetricKey
    value_type: Annotated[str, ClosedSet(PARAMETER_VALUE_TYPES)]
    default: IJsonValue = None

    @model_validator(mode="after")
    def _default_fits(self) -> MetricParameter:
        if self.default is not None and not value_fits(self.value_type, self.default):
            named = "the parameter" if values_hidden() else f"parameter {self.name!r}"
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("default",),
                        "parameter_default",
                        f"the default of {named} is not a {self.value_type}",
                        self.default,
                    )
                ],
            )
        return self


def _unique_parameter_names(parameters: list[MetricParameter]) -> list[MetricParameter]:
    _unique("parameters")([parameter.name for parameter in parameters])
    return parameters


def _distinct_primary_languages(tags: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for tag in tags:
        primary = primary_language(tag)
        if primary in seen and values_hidden():
            raise ValueError(
                "languages lists two tags that share a primary language; coverage is decided by "
                "primary language"
            )
        if primary in seen:
            raise ValueError(
                f"languages lists {seen[primary]!r} and {tag!r}, which share the primary "
                f"language {primary!r}; coverage is decided by primary language"
            )
        seen[primary] = tag
    return tags


MetricLanguages = (
    Annotated[str, ClosedSet((LANGUAGE_INDEPENDENT,))]
    | Annotated[
        list[SupportedLanguage],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_distinct_primary_languages),
    ]
)
"""``"language_independent"`` or a non-empty list of distinct primary languages (P-59)."""

IMPLEMENTATION_REF_PATTERN = "^[a-z][a-z0-9_]*:[A-Za-z0-9._:/@+-]+$"

ImplementationRef = Annotated[
    str,
    FullMatch(
        IMPLEMENTATION_REF_PATTERN,
        "implementation_ref",
        "{value} is not <namespace>:<name> (namespace [a-z][a-z0-9_]*, for example builtin:wer)",
        max_length=256,
    ),
]

_TEMPLATE_VARIABLE = FullMatch(METRIC_KEY_PATTERN)


def template_variables(template: str) -> list[str] | None:
    """The ``{{name}}`` variables of a prompt template, in order, or ``None`` if any ``{{`` does
    not start a well-formed variable (``name`` a ``MetricKey``, no white space)."""
    names: list[str] = []
    position = 0
    while (start := template.find("{{", position)) >= 0:
        end = template.find("}}", start + 2)
        name = template[start + 2 : end] if end >= 0 else ""
        if end < 0 or "{" in name or not _TEMPLATE_VARIABLE.matches(name):
            return None
        names.append(name)
        position = end + 2
    return names


def _when(field: str, *values: str) -> dict[str, Any]:
    return {"properties": {field: {"enum": list(values)}}, "required": [field]}


_JUDGE_FIELDS: Final = ("prompt_template", "prompt_variables", "judge_model_ref")


def _definition_conditions(schema: dict[str, Any]) -> None:
    judge_types = {
        "prompt_template": "string",
        "prompt_variables": "array",
        "judge_model_ref": "object",
    }
    schema["allOf"] = [
        {
            "if": _when("kind", "llm_judge"),
            "then": {
                "required": list(_JUDGE_FIELDS),
                "properties": {
                    **{name: {"type": kind} for name, kind in judge_types.items()},
                    "implementation_ref": {"type": "null"},
                },
            },
            "else": {
                "required": ["implementation_ref"],
                "properties": {
                    "implementation_ref": {"type": "string"},
                    **{name: {"type": "null"} for name in _JUDGE_FIELDS},
                },
            },
        },
        {
            "if": _when("value_type", "rating"),
            "then": {
                "required": ["scale"],
                "properties": {"scale": {"type": "object", "required": ["min", "max"]}},
            },
        },
        {
            "if": _when("value_type", "enum"),
            "then": {
                "required": ["scale"],
                "properties": {"scale": {"type": "object", "required": ["options"]}},
            },
        },
        {
            "if": _when("value_type", "boolean", "numeric"),
            "then": {"properties": {"scale": {"type": "null"}}},
        },
        {
            "if": _when("value_type", "numeric"),
            "then": {"required": ["unit"], "properties": {"unit": {"type": "string"}}},
            "else": {"properties": {"unit": {"type": "null"}}},
        },
        {
            "if": {"properties": {"languages": {"type": "string"}}, "required": ["languages"]},
            "then": {"properties": {"script_sensitive": {"enum": [False]}}},
        },
    ]


# ------------------------------------------------------------------------------ the definition


class MetricDefinition(HidingModel, VersionedEntity):
    """What a metric measures and how it passes (prefix ``met``; ADR 0010 Decisions 1 and 1a).

    The judge fields (``prompt_template``, ``prompt_variables``, ``judge_model_ref``) are required
    for ``kind=llm_judge`` and forbidden otherwise; ``implementation_ref`` is the reverse. A
    ``scale`` belongs to rating and enum metrics, a ``unit`` to numeric ones. The pass rule fits
    ``value_type`` (for ``per_turn_share`` it is a range on the share and ``turn_rule`` fits the
    type), its bounds lie in the value domain, and some value passes.
    """

    model_config = ConfigDict(json_schema_extra=_definition_conditions, hide_input_in_errors=True)

    id_prefix: ClassVar[str] = "met"

    key: MetricKey
    kind: Annotated[str, ClosedSet(METRIC_KINDS)]
    value_type: Annotated[str, ClosedSet(VALUE_TYPES)]
    scale: RatingScale | EnumScale | None = None
    unit: Annotated[str, ClosedSet(UNITS)] | None = None
    requires: Annotated[
        list[InputKind],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_unique("requires")),
    ]
    languages: MetricLanguages
    script_sensitive: Annotated[bool, Strict()]
    aggregation: Aggregation
    pass_rule: PassRule | None = None
    parameters: Annotated[
        list[MetricParameter],
        AfterValidator(_unique_parameter_names),
    ] = Field(default_factory=list)
    implementation_ref: ImplementationRef | None = None
    prompt_template: Annotated[VerbatimText, Field(min_length=1)] | None = None
    prompt_variables: (
        Annotated[
            list[MetricKey],
            AfterValidator(_unique("prompt_variables")),
            Field(json_schema_extra={"uniqueItems": True}),
        ]
        | None
    ) = None
    judge_model_ref: ModelRef | None = None

    @model_validator(mode="after")
    def _field_conditions(self) -> MetricDefinition:
        problems = [
            *self._kind_problems(),
            *self._scale_and_unit_problems(),
            *self._rule_problems(),
        ]
        if self.languages == LANGUAGE_INDEPENDENT and self.script_sensitive:
            problems.append(
                Problem(
                    ("languages",),
                    "language_independent_script_sensitive",
                    'languages "language_independent" requires script_sensitive=false: a metric '
                    "that does not depend on language cannot depend on script",
                    self.languages,
                )
            )
        raise_problems(type(self).__name__, problems)
        return self

    def _kind_problems(self) -> list[Problem]:
        problems: list[Problem] = []
        judge = self.kind == "llm_judge"
        for name in _JUDGE_FIELDS:
            value = getattr(self, name)
            if judge and value is None:
                problems.append(
                    Problem((name,), "judge_field", f"{name} is required for kind=llm_judge")
                )
            elif not judge and value is not None:
                problems.append(
                    Problem(
                        (name,),
                        "judge_field",
                        f"{name} belongs to kind=llm_judge only (kind is {self.kind})",
                        value,
                    )
                )
        if judge and self.implementation_ref is not None:
            problems.append(
                Problem(
                    ("implementation_ref",),
                    "implementation_ref",
                    "an llm_judge metric names no scorer: implementation_ref is forbidden",
                    self.implementation_ref,
                )
            )
        if not judge and self.implementation_ref is None:
            problems.append(
                Problem(
                    ("implementation_ref",),
                    "implementation_ref",
                    f"kind={self.kind} needs implementation_ref (<namespace>:<name>)",
                )
            )
        if judge and self.prompt_template is not None and self.prompt_variables is not None:
            used = template_variables(self.prompt_template)
            if used is None:
                problems.append(
                    Problem(
                        ("prompt_template",),
                        "prompt_template",
                        "every {{ in a prompt template must start a variable written "
                        "{{name}} (a lowercase snake_case name, no spaces)",
                    )
                )
            elif set(used) != set(self.prompt_variables):
                # No variable names in the message: the model hides its input (ADR 0007 8 (c)).
                problems.append(
                    Problem(
                        ("prompt_variables",),
                        "prompt_variables",
                        "prompt_variables must equal the set of variables used in prompt_template",
                        self.prompt_variables,
                    )
                )
        return problems

    def _scale_and_unit_problems(self) -> list[Problem]:
        problems: list[Problem] = []
        wanted: dict[str, type[BaseModel]] = {"rating": RatingScale, "enum": EnumScale}
        scale_class = wanted.get(self.value_type)
        if scale_class is not None and not isinstance(self.scale, scale_class):
            shape = "{min, max}" if scale_class is RatingScale else "{options}"
            problems.append(
                Problem(
                    ("scale",),
                    "scale",
                    f"a {self.value_type} metric needs scale {shape}",
                    self.scale,
                )
            )
        if scale_class is None and self.scale is not None:
            problems.append(
                Problem(
                    ("scale",), "scale", "only rating and enum metrics have a scale", self.scale
                )
            )
        if self.value_type == "numeric" and self.unit is None:
            problems.append(Problem(("unit",), "unit", "a numeric metric needs a unit"))
        if self.value_type != "numeric" and self.unit is not None:
            problems.append(
                Problem(("unit",), "unit", "only numeric metrics have a unit", self.unit)
            )
        return problems

    def _value_domain(self) -> _Domain | None:
        if self.value_type == "rating" and isinstance(self.scale, RatingScale):
            return _interval(self.scale.min, self.scale.max, True, "the rating scale")
        if self.value_type == "numeric" and self.unit is not None:
            return UNIT_DOMAINS[self.unit]
        return None

    def _fit_problem(self, rule: RangeRule | EqualsTrueRule | OneOfRule) -> str | None:
        fitting = FITTING_RULE[self.value_type]
        if rule.kind != fitting:
            return f"a {rule.kind} rule does not fit value_type {self.value_type} (use {fitting})"
        if isinstance(rule, RangeRule):
            domain = self._value_domain()
            return None if domain is None else _range_problem(rule, domain)
        if isinstance(rule, OneOfRule) and isinstance(self.scale, EnumScale):
            outside = [option for option in rule.options if option not in self.scale.options]
            if outside:
                return "every one_of option must be an option of the scale"
        return None

    def _rule_problems(self) -> list[Problem]:
        problems: list[Problem] = []
        rule = self.pass_rule
        if isinstance(self.aggregation, PerTurnShareAggregation):
            turn_problem = self._fit_problem(self.aggregation.turn_rule)
            if turn_problem is not None:
                problems.append(Problem(("aggregation", "turn_rule"), "turn_rule", turn_problem))
            if rule is not None and not isinstance(rule, RangeRule):
                problems.append(
                    Problem(
                        ("pass_rule",),
                        "pass_rule",
                        "the pass rule of a per_turn_share metric is a range on the share "
                        f"(not {rule.kind})",
                    )
                )
            elif rule is not None:
                share_problem = _range_problem(rule, SHARE_DOMAIN)
                if share_problem is not None:
                    problems.append(Problem(("pass_rule",), "pass_rule", share_problem))
        elif rule is not None:
            fit_problem = self._fit_problem(rule)
            if fit_problem is not None:
                problems.append(Problem(("pass_rule",), "pass_rule", fit_problem))
        return problems


def render_metric_ref(definition: MetricDefinition) -> str:
    """``key@version`` (for example ``p95_latency@3``): display text only, never stored or read
    back. Keys do not identify definitions; the reference is ``MetricDefinitionRef``."""
    return f"{definition.key}@{definition.version}"


# ----------------------------------------------------------------------------- references


class MetricDefinitionRef(VersionedRef):
    """``{id, version, content_digest?}`` of a ``MetricDefinition`` (ADR 0009 Decision 4)."""

    id: MetricDefinitionId


class MetricBindingRef(VersionedRef):
    """``{id, version, content_digest?}`` of a ``MetricBinding`` (ADR 0009 Decision 4)."""

    id: MetricBindingId


# -------------------------------------------------------------------------------- the binding


class MetricBinding(HidingModel, VersionedEntity):
    """A definition bound to an evaluator (prefix ``mbd``; ADR 0010 Decisions 4 and 4a).

    ``params`` set parameters the definition declares, stored unchanged; they never change a
    definition field such as the pass rule (E6). ``check_binding_params`` checks them against the
    definition. A disabled binding is never run and may not be a rubric item.
    """

    id_prefix: ClassVar[str] = "mbd"

    evaluator_id: EvaluatorId
    metric: MetricDefinitionRef
    params: dict[MetricKey, IJsonValue]
    enabled: Annotated[bool, Strict()]


def check_bindings_unique(bindings: Sequence[MetricBinding]) -> None:
    """Reject two bindings of one evaluator that name one definition (any version, enabled or
    not, or one binding listed twice); the ``ValueError`` names the definition ID."""
    seen: dict[tuple[EvaluatorId, MetricDefinitionId], MetricBinding] = {}
    for binding in bindings:
        key = (binding.evaluator_id, binding.metric.id)
        if key in seen:
            raise ValueError(
                f"evaluator {binding.evaluator_id} binds definition {binding.metric.id} twice "
                f"(bindings {seen[key].id} and {binding.id})"
            )
        seen[key] = binding


def check_binding_params(binding: MetricBinding, definition: MetricDefinition) -> None:
    """Check a binding's ``params`` against the definition it binds (ADR 0010 Decision 4a).

    Every key is a declared parameter, every value has the declared type (no bool/number
    coercion), and every parameter without a default is present. The ``ValueError`` names the
    binding ID and the keys.
    """
    if (binding.metric.id, binding.metric.version) != (definition.id, definition.version):
        raise ValueError(
            f"binding {binding.id} binds {binding.metric.id} version {binding.metric.version}, "
            f"not definition {definition.id} version {definition.version}"
        )
    declared = {parameter.name: parameter for parameter in definition.parameters}
    hidden = values_hidden()  # inside a model that hides its input: no parameter names (N42)

    def named(name: str) -> str:
        return "a parameter" if hidden else f"parameter {name!r}"

    problems: list[str] = []
    for key, value in binding.params.items():
        parameter = declared.get(key)
        if parameter is None:
            problems.append(
                f"{named(key)} is not declared by the definition (a binding sets declared "
                "parameters only, never a definition field such as the pass rule)"
            )
        elif not value_fits(parameter.value_type, value):
            problems.append(f"{named(key)} must be a {parameter.value_type}")
    for name, parameter in declared.items():
        if parameter.default is None and name not in binding.params:
            problems.append(f"{named(name)} has no default and is missing")
    if problems:
        raise ValueError(f"binding {binding.id} of {definition.id}: " + "; ".join(problems))
