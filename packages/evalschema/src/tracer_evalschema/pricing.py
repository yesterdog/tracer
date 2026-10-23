# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Pricing catalogs and their entries (ADR 0014 Decisions 3, 10, 10a and 11, ADR 0007 Decision
6e, ADR 0009 Decision 5; P1-24).

- ``PricingCatalog`` (``pcat``, versioned and digested): ``entries`` (non-empty) and
  ``published_at``. Each ``entry_key`` appears once, and effective ranges under one key
  ``(provider, model, unit, applies_when)`` (``applies_when`` compared as a set) must not overlap;
  ranges are half-open ``[effective_from, effective_to)`` (N48, N49). Entries of different keys
  that can match one usage are accepted; the resolver answers ``ambiguous_price`` for them. The
  digest is the generic domain, so it covers every field of every entry (D4).
- ``PriceEntry``: a value object inside its catalog version (N47), named by the author-assigned
  ``entry_key`` (N61, a ``MachineKey`` scoped by the catalog reference). ``price`` and the
  billing ``minimum`` are USD ``Money`` >= 0 (P-38); ``unit_quantity`` and the billing
  ``increment`` are positive canonical decimals; ``billing`` is the literal ``"unknown"`` or a
  ``BillingRules`` whose two parts are each a value or the literal ``"none"``; ``aliases`` are
  distinct and never the entry's own ``model`` (N72); ``applies_when`` is absent or a non-empty
  list of conditions of distinct kinds that must all hold (N49); ``price_source`` says whether
  the price is ours or the user's, and a ``user_supplied`` price needs ``provenance.note``.
- ``PriceProvenance{kind, url?, retrieved_at, note?}``: ``kind`` is an open ``MachineKey``,
  ``url`` follows ``url_rules.check_price_source_url`` (credentials, fragment parameter names,
  loopback hosts; N50, N66), a present ``note`` is never empty or white space only
  (``str.isspace()``, N71).
- ``AppliesWhenCondition{kind, value}``: ``context_tokens_gt``/``context_tokens_le`` take an
  integer >= 0, ``batch`` a boolean and ``region`` a ``MachineKey`` (P-37, N49).
- ``check_catalog_succession(previous, next)``: P-24 (N47 remainder, N65, N72, D18).

``PriceProvenance``, ``PriceEntry`` and ``PricingCatalog`` hide their input in validation errors
and no message inside them quotes a rejected value; a non-USD price names the rule, not the code
(N62; ADR 0007 Decision 8). No float is used anywhere in this module (ADR 0014 Decision 15a).
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from datetime import datetime
from typing import Annotated, Any, ClassVar, Final

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    WithJsonSchema,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import VersionedEntity
from tracer_evalschema.ijson import MAX_SAFE_INTEGER
from tracer_evalschema.primitives import DecimalString, Money, UtcDatetime
from tracer_evalschema.text import MACHINE_KEY_PATTERN, MachineKey, VerbatimText
from tracer_evalschema.url_rules import PRICE_SOURCE_URL_PATTERN, UrlRule, check_price_source_url
from tracer_evalschema.usage import UsageUnit

__all__ = [
    "BILLING_UNKNOWN",
    "CONDITION_KINDS",
    "NONE",
    "POSITIVE_DECIMAL_PATTERN",
    "PRICE_SOURCES",
    "AppliesWhenCondition",
    "BillingRules",
    "PriceEntry",
    "PriceProvenance",
    "PricingCatalog",
    "check_catalog_succession",
]

NONE: Final = "none"
"""The billing part value "none": no increment (billed exactly) or no minimum charge."""
BILLING_UNKNOWN: Final = "unknown"
"""The ``billing`` value of an entry whose billing rules the author cannot confirm."""
PRICE_SOURCES: Final = ("catalog", "user_supplied")
CONDITION_KINDS: Final = ("context_tokens_gt", "context_tokens_le", "batch", "region")
_TOKEN_KINDS: Final = ("context_tokens_gt", "context_tokens_le")

POSITIVE_DECIMAL_PATTERN: Final = (
    r"^(?:[1-9][0-9]*|[1-9][0-9]*\.[0-9]{0,17}[1-9]|0\.[0-9]{0,17}[1-9])$"
)
"""The canonical decimals greater than 0 (a subset of ``primitives.DECIMAL_PATTERN``)."""


# ------------------------------------------------------------------------------ small types


def _positive(text: str) -> str:
    if text == "0" or text.startswith("-"):
        raise ValueError("the decimal must be greater than 0")
    return text


PositiveDecimal = Annotated[
    DecimalString,
    AfterValidator(_positive),
    WithJsonSchema({"type": "string", "pattern": POSITIVE_DECIMAL_PATTERN}),
]
"""A canonical decimal > 0 (``unit_quantity``, ``increment``)."""


def _not_negative(money: Money) -> Money:
    if money.amount.startswith("-"):  # canonical text: negative iff it starts with "-"
        raise ValueError("a price or a minimum charge is never negative (P-38)")
    return money


NonNegativeMoney = Annotated[Money, AfterValidator(_not_negative)]
"""A USD ``Money`` >= 0 (``PriceEntry.price``, ``BillingRules.minimum``)."""

NoneLiteral = Annotated[str, ClosedSet((NONE,))]
UnknownLiteral = Annotated[str, ClosedSet((BILLING_UNKNOWN,))]


def _is_blank(text: str) -> bool:
    return text == "" or text.isspace()


def _not_blank(text: str) -> str:
    if _is_blank(text):
        raise ValueError("a note is never empty or white space only (it states the price source)")
    return text


Note = Annotated[VerbatimText, Field(min_length=1), AfterValidator(_not_blank)]
"""A present ``provenance.note``: text that is not white space only (``str.isspace()``, N71);
kept exactly as given, surrounding white space included."""

PriceSourceUrl = Annotated[str, UrlRule(check_price_source_url, PRICE_SOURCE_URL_PATTERN)]


# ------------------------------------------------------------------------------ provenance


class PriceProvenance(HidingModel):
    """Where a price came from (ADR 0014 Decision 11). See the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kind: MachineKey
    url: PriceSourceUrl | None = None
    retrieved_at: UtcDatetime
    note: Note | None = None


# ----------------------------------------------------------------------------- billing rules


class BillingRules(BaseModel):
    """How a provider bills one event (ADR 0014 Decision 11, P-38): both parts are required and
    each is a value or the literal ``"none"``. ``increment``: a positive decimal quantity in the
    entry's unit (the quantity is rounded up to a multiple of it). ``minimum``: a USD ``Money``
    >= 0, the minimum charge per billable event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    increment: Annotated[NoneLiteral | PositiveDecimal, Field(union_mode="left_to_right")]
    minimum: Annotated[NoneLiteral | NonNegativeMoney, Field(union_mode="left_to_right")]


# ------------------------------------------------------------------------------- conditions


_KEY_REGEX = re.compile(MACHINE_KEY_PATTERN[1:-1])


def _condition_value(value: Any) -> Any:
    if type(value) is bool:
        return value
    if type(value) is int and 0 <= value <= MAX_SAFE_INTEGER:
        return value
    if type(value) is str and _KEY_REGEX.fullmatch(value) is not None:
        return value
    raise PydanticCustomError(
        "condition_value",
        "a condition value is an integer >= 0 (context_tokens_gt, context_tokens_le), a boolean "
        "(batch) or a machine key (region)",
    )


class _ConditionValueRule:
    """Validation and JSON Schema of ``AppliesWhenCondition.value`` (the type per kind is checked
    by the model and stated with ``if``/``then``)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(_condition_value)

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "anyOf": [
                {"type": "integer", "minimum": 0, "maximum": MAX_SAFE_INTEGER},
                {"type": "boolean"},
                {"type": "string", "pattern": MACHINE_KEY_PATTERN},
            ]
        }


def _condition_conditions(schema: dict[str, Any]) -> None:
    """The value type by kind (P-37, N49) as JSON Schema ``if``/``then``."""
    by_kind: list[tuple[list[str], dict[str, Any]]] = [
        (list(_TOKEN_KINDS), {"type": "integer"}),
        (["batch"], {"type": "boolean"}),
        (["region"], {"type": "string"}),
    ]
    schema["allOf"] = [
        {
            "if": {"properties": {"kind": {"enum": kinds}}, "required": ["kind"]},
            "then": {"properties": {"value": value}},
        }
        for kinds, value in by_kind
    ]


class AppliesWhenCondition(BaseModel):
    """One typed condition of a price entry (P-37). A condition is something the resolver can
    evaluate from the caller's ``PriceFacts`` or knows it cannot (``ambiguous_price``)."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_condition_conditions)

    kind: Annotated[str, ClosedSet(CONDITION_KINDS)]
    value: Annotated[int | bool | str, _ConditionValueRule()]

    @model_validator(mode="after")
    def _value_fits_kind(self) -> AppliesWhenCondition:
        if self.kind in _TOKEN_KINDS:
            fits, wanted = type(self.value) is int, "an integer >= 0 (a number of tokens)"
        elif self.kind == "batch":
            fits, wanted = type(self.value) is bool, "a boolean"
        else:
            fits, wanted = type(self.value) is str, "a machine key (a region name)"
        if not fits:
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("value",),
                        "condition_value",
                        f"the value of a {self.kind} condition is {wanted}",
                    )
                ],
            )
        return self

    def fact_key(self) -> tuple[str, Hashable]:
        """``(kind, value)``: the condition as a set member (the order of conditions carries no
        meaning, N49)."""
        return (self.kind, self.value)


# ---------------------------------------------------------------------------------- entries


def _distinct_aliases(aliases: list[str]) -> list[str]:
    if len(set(aliases)) != len(aliases):
        raise ValueError("aliases list one model name twice (N72)")
    return aliases


def _distinct_kinds(conditions: list[AppliesWhenCondition]) -> list[AppliesWhenCondition]:
    kinds = [condition.kind for condition in conditions]
    if len(set(kinds)) != len(kinds):
        raise ValueError(
            "applies_when holds one condition kind twice; each kind appears at most once (a band "
            "is context_tokens_gt plus context_tokens_le, several regions are several entries)"
        )
    return conditions


Aliases = Annotated[
    list[MachineKey],
    Field(json_schema_extra={"uniqueItems": True}),
    AfterValidator(_distinct_aliases),
]
Conditions = Annotated[
    list[AppliesWhenCondition], Field(min_length=1), AfterValidator(_distinct_kinds)
]


def _entry_conditions(schema: dict[str, Any]) -> None:
    """A user-supplied price states its source in ``provenance.note`` (N50 remainder (c)), as
    JSON Schema ``if``/``then``."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"price_source": {"enum": ["user_supplied"]}},
                "required": ["price_source"],
            },
            "then": {
                "properties": {
                    "provenance": {"required": ["note"], "properties": {"note": {"type": "string"}}}
                }
            },
        }
    ]


class PriceEntry(HidingModel):
    """One price of a catalog version (ADR 0014 Decision 11; a value object since revision 15).
    See the module docstring."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        json_schema_extra=_entry_conditions,
    )

    entry_key: MachineKey
    provider: MachineKey
    model: MachineKey
    aliases: Aliases = Field(default_factory=list)
    unit: UsageUnit
    unit_quantity: PositiveDecimal
    price: NonNegativeMoney
    billing: Annotated[UnknownLiteral | BillingRules, Field(union_mode="left_to_right")]
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None = None
    applies_when: Conditions | None = None
    price_source: Annotated[str, ClosedSet(PRICE_SOURCES)]
    provenance: PriceProvenance

    @model_validator(mode="after")
    def _rules(self) -> PriceEntry:
        problems: list[Problem] = []
        if self.model in self.aliases:
            problems.append(
                Problem(
                    ("aliases",),
                    "price_aliases",
                    "an alias is a second name of the model; the entry's own model is not one "
                    "of its aliases (N72)",
                )
            )
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            problems.append(
                Problem(
                    ("effective_to",),
                    "effective_range",
                    "effective_to is later than effective_from (ranges are half-open, "
                    "[effective_from, effective_to))",
                )
            )
        if self.price_source == "user_supplied" and self.provenance.note is None:
            problems.append(
                Problem(
                    ("provenance", "note"),
                    "provenance_note",
                    "a user_supplied price needs provenance.note: the source of the price and, "
                    "for a converted price, the rate and date applied",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self

    def applies_when_set(self) -> frozenset[tuple[str, Hashable]]:
        """The conditions as a set (empty when the entry is unconditional)."""
        return frozenset(condition.fact_key() for condition in self.applies_when or ())

    def matches_model(self, model: str) -> bool:
        """``model`` is the entry's model or one of its aliases, compared exactly."""
        return model == self.model or model in self.aliases

    def is_effective_at(self, moment: datetime) -> bool:
        """``effective_from <= moment`` and (``effective_to`` absent or ``moment <
        effective_to``)."""
        return self.effective_from <= moment and (
            self.effective_to is None or moment < self.effective_to
        )


# --------------------------------------------------------------------------------- catalogs


def _ranges_overlap(first: PriceEntry, second: PriceEntry) -> bool:
    first_ends_after = second.effective_to is None or first.effective_from < second.effective_to
    second_ends_after = first.effective_to is None or second.effective_from < first.effective_to
    return first_ends_after and second_ends_after


def _overlap_key(entry: PriceEntry) -> tuple[Hashable, ...]:
    return (entry.provider, entry.model, entry.unit, entry.applies_when_set())


class PricingCatalog(HidingModel, VersionedEntity):
    """A version of a pricing catalog (prefix ``pcat``; versioned, ADR 0009). See the module
    docstring."""

    id_prefix: ClassVar[str] = "pcat"

    entries: Annotated[list[PriceEntry], Field(min_length=1)]
    published_at: UtcDatetime

    @model_validator(mode="after")
    def _entry_rules(self) -> PricingCatalog:
        problems: list[Problem] = []
        first_at: dict[str, int] = {}
        for index, entry in enumerate(self.entries):
            if entry.entry_key in first_at:
                problems.append(
                    Problem(
                        ("entries",),
                        "entry_key",
                        f"entries {first_at[entry.entry_key]} and {index} share one entry_key; "
                        "an entry_key appears once in a catalog version (N61)",
                    )
                )
            else:
                first_at[entry.entry_key] = index
        for later, entry in enumerate(self.entries):
            for earlier in range(later):
                other = self.entries[earlier]
                if _overlap_key(other) == _overlap_key(entry) and _ranges_overlap(other, entry):
                    problems.append(
                        Problem(
                            ("entries",),
                            "price_overlap",
                            f"entries {earlier} and {later} overlap: their effective ranges "
                            "(half-open, [effective_from, effective_to)) overlap under one "
                            "(provider, model, unit, applies_when) key (A-25)",
                        )
                    )
        raise_problems(type(self).__name__, problems)
        return self


# ------------------------------------------------------------------------------- succession


def _content(entry: PriceEntry) -> tuple[Hashable, ...]:
    """What an entry states about a price, for P-24 (N65, N72): every field except
    ``entry_key``, ``provenance`` and ``effective_to``; ``aliases`` and ``applies_when`` as
    sets."""
    billing: Hashable = (
        entry.billing
        if isinstance(entry.billing, str)
        else (
            entry.billing.increment,
            entry.billing.minimum
            if isinstance(entry.billing.minimum, str)
            else (entry.billing.minimum.amount, entry.billing.minimum.currency),
        )
    )
    return (
        entry.provider,
        entry.model,
        frozenset(entry.aliases),
        entry.unit,
        entry.unit_quantity,
        (entry.price.amount, entry.price.currency),
        billing,
        entry.effective_from,
        entry.applies_when_set(),
        entry.price_source,
    )


def check_catalog_succession(previous: PricingCatalog, next: PricingCatalog) -> None:
    """Check P-24 between two versions of one catalog (ADR 0014 Decision 10a): an unchanged entry
    keeps its ``entry_key`` and a changed one gets a new key.

    First the catalogs (D18): another ``id`` is a ``ValueError`` naming the catalog, a
    ``next.version`` that is not later than ``previous.version`` a ``ValueError`` naming the
    version. Then the entries, by content (every field except ``entry_key``, ``provenance`` and
    ``effective_to``, ``aliases`` and ``applies_when`` as sets; N65, N72): a key of ``previous``
    reused in ``next`` with other content, and an entry of ``next`` under a new key whose content
    equals an entry of ``previous``, are each a ``ValueError`` whose message says "entry" and names
    the key, never a provenance URL. A removed key and a new key with new content are accepted.
    Pure: no clock, no I/O, neither argument is changed.
    """
    if previous.id != next.id:
        raise ValueError(
            f"catalog succession compares two versions of one catalog, not catalog {previous.id} "
            f"and catalog {next.id}"
        )
    if next.version <= previous.version:
        raise ValueError(
            f"catalog {next.id}: the next version ({next.version}) is not later than the previous "
            f"version ({previous.version})"
        )
    before = {entry.entry_key: _content(entry) for entry in previous.entries}
    for entry in next.entries:
        known = before.get(entry.entry_key)
        if known is not None and known != _content(entry):
            raise ValueError(
                f"entry {entry.entry_key!r} of catalog {next.id} version {next.version} reuses a "
                f"key of version {previous.version} for different content; a changed price gets "
                "a new entry_key (P-24)"
            )
    for entry in next.entries:
        if entry.entry_key in before:
            continue
        content = _content(entry)
        for old_key, old_content in before.items():
            if old_content == content:
                raise ValueError(
                    f"entry {entry.entry_key!r} of catalog {next.id} version {next.version} has "
                    f"the content of entry {old_key!r} of version {previous.version} under a new "
                    "key; an unchanged price keeps its entry_key (P-24)"
                )
