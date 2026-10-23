# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Cost records, cost summaries and the pure cost functions (ADR 0014 Decisions 1a, 12 to 16b;
P1-25).

Models:

- ``CostRecord`` (``cst``, a record of fact): one cost of one domain, ``available`` or
  ``not_available``; its rules are stated in its class docstring, which is also its JSON Schema
  ``description`` (finding A29).
- ``PricingSnapshot{catalog_id, version, content_digest, entry_keys}``,
  ``CostRounding{scale: 18, mode: half_even, exact: false}``, ``ReportedCostRaw{reported_by,
  amount_raw, currency_raw?, currency_source}``.
- ``CostSummary`` (``csum``, a record of fact): one USD ``total`` (present iff something is
  included), ``included``, ``excluded`` (``CostExclusion{ref, reason, covered_by}``) and
  ``completeness``.

Functions (all pure: no clock, no I/O, no global state, inputs unchanged):

- ``resolve_price`` and ``compute_cost`` (Decisions 13, 13a, 13b, 15): ``Decimal`` only, with the
  caller's explicit ``decimal.Context`` and never the thread's current one. Every intermediate
  step is exact (the context's ``Inexact`` and ``InvalidOperation`` are trapped on a private
  copy, so a context too small for an exact result is a ``ValueError``, never a silently rounded
  amount); the record's total is rounded once, half-even at scale 18 (P-35, N56).
- ``check_pricing_snapshot`` (Decision 16b, N52), ``summarize_costs`` and ``check_cost_summary``
  (Decisions 16, 16a, 16b; P-54, N53).
- ``normalize_currency_label`` and ``import_reported_cost`` (Decisions 1a, 12a to 12c; P-52,
  P-53, N59, N68, N69): the importer of a reported cost, which never rejects the import.

``CostRecord`` and ``ReportedCostRaw`` hide their input in validation errors and no message
inside them quotes a rejected value (N60; ADR 0007 Decision 8); ``CostSummary`` does not. No
float is used anywhere in this module (ADR 0014 Decision 15a).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from typing import Annotated, Any, ClassVar, Final, NamedTuple

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    WithJsonSchema,
    model_validator,
)

from tracer_evalschema._constraints import ClosedSet, echoed
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import SCHEMA_VERSION, EntityBase, Version
from tracer_evalschema.digest import ContentDigest
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import (
    CostRecordId,
    CostSummaryId,
    PricingCatalogId,
    TypedId,
    UsageRecordId,
)
from tracer_evalschema.pricing import NONE, AppliesWhenCondition, PriceEntry, PricingCatalog
from tracer_evalschema.primitives import (
    DECIMAL_PATTERN,
    Money,
    canonical_decimal_text,
    iso4217_currency_name,
)
from tracer_evalschema.snapshot import PricingCatalogRef, check_pricing_catalog_ref
from tracer_evalschema.text import MachineKey, VerbatimText, replace_lone_surrogates
from tracer_evalschema.usage import DOMAINS, USAGE_UNITS, UsageRecord, UsageUnit

__all__ = [
    "BASES",
    "COMPLETENESS",
    "CURRENCY_SOURCES",
    "EXCLUSION_REASONS",
    "NA_REASONS",
    "RECORD_STATUSES",
    "REPORTERS",
    "ContractCurrency",
    "CostExclusion",
    "CostRecord",
    "CostRounding",
    "CostSummary",
    "ImportedCost",
    "PriceFacts",
    "PriceResolution",
    "PricingSnapshot",
    "ReportedCostRaw",
    "check_cost_summary",
    "check_pricing_snapshot",
    "compute_cost",
    "import_reported_cost",
    "normalize_currency_label",
    "resolve_price",
    "summarize_costs",
]

RECORD_STATUSES: Final = ("available", "not_available")
BASES: Final = ("reported", "computed")
REPORTERS: Final = ("provider", "telemetry_platform")
CURRENCY_SOURCES: Final = ("payload", "api_contract", "none")
NA_REASONS: Final = (
    "no_price_for_model",
    "unit_unpriced",
    "no_usage",
    "ambiguous_price",
    "billing_rules_missing",
    "usage_not_reported",
    "unsupported_currency",
    "currency_not_reported",
    "amount_unparseable",
    "currency_conflict",
)
EXCLUSION_REASONS: Final = (*NA_REASONS, "duplicate_of_reported", "overlaps_reported")
COMPLETENESS: Final = ("complete", "partial", "none")

_NO_USAGE_REASONS: Final = ("no_usage", "usage_not_reported")
_PRICED_USAGE_REASONS: Final = (
    "no_price_for_model",
    "unit_unpriced",
    "billing_rules_missing",
    "ambiguous_price",
)
_RAW_REASONS: Final = (
    "unsupported_currency",
    "currency_not_reported",
    "amount_unparseable",
    "currency_conflict",
)
_ALLOWED_SOURCES: Final = {
    "unsupported_currency": ("payload", "api_contract"),
    "currency_not_reported": ("none",),
    "amount_unparseable": ("payload", "api_contract"),
    "currency_conflict": ("payload",),
}
_NOT_AVAILABLE_ABSENT: Final = ("amount", "basis", "reported_by", "pricing_snapshot", "rounding")

ROUNDING_SCALE: Final = 18
_USD: Final = "USD"
_ASCII_SPACE: Final = " \t\n\r\x0b\x0c"  # the six ASCII white-space characters (D5)
_ASCII_LOWER: Final = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def normalize_currency_label(label: str | None) -> str | None:
    """``"USD"`` iff ``label`` equals ``USD`` after trimming the six ASCII white-space characters
    (U+0009 to U+000D, U+0020) and folding ASCII letters only (P-52, D5); ``None`` for everything
    else, including ``$``, ``US$``, ``dollars``, other codes, Unicode spaces and invisible
    characters. Pure."""
    if not isinstance(label, str):
        return None
    folded = label.strip(_ASCII_SPACE).translate(_ASCII_LOWER)
    return _USD if folded == "usd" else None


def _is_blank_label(label: str) -> bool:
    """Empty or ASCII white space only: no label at all (D6)."""
    return label.strip(_ASCII_SPACE) == ""


# ------------------------------------------------------------------------------- value objects


def _distinct(what: str) -> Any:
    def check(values: list[Any]) -> list[Any]:
        if len(set(values)) != len(values):
            raise ValueError(f"{what} lists one value twice")
        return values

    return check


EntryKeys = Annotated[
    list[MachineKey],
    Field(min_length=1, json_schema_extra={"uniqueItems": True}),
    AfterValidator(_distinct("entry_keys")),
]


class PricingSnapshot(HidingModel):
    """The catalog version and the entries a computed cost used (ADR 0014 Decision 12, O6, N61):
    ``(catalog_id, version, content_digest)`` names one catalog version and ``entry_keys`` the
    entries used, by their catalog-scoped ``entry_key`` (non-empty, distinct)."""

    # Embeds a ContentDigest (and so a KeyRef), so it hides its input (ADR 0007 Decision 8 (b),
    # P1-21 C3b closure rule).
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    catalog_id: PricingCatalogId
    version: Version
    content_digest: ContentDigest
    entry_keys: EntryKeys


def _must_be_false(value: bool) -> bool:
    if value is not False:
        raise ValueError("exact is false: rounding is recorded only when it changed the amount")
    return value


class CostRounding(BaseModel):
    """``{scale: 18, mode: half_even, exact: false}`` (P-35): present on an available computed
    record iff the record's total was rounded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scale: Annotated[
        int,
        Strict(),
        Field(ge=ROUNDING_SCALE, le=ROUNDING_SCALE),
        WithJsonSchema({"type": "integer", "enum": [ROUNDING_SCALE]}),
    ]
    mode: Annotated[str, ClosedSet(("half_even",))]
    exact: Annotated[
        bool,
        Strict(),
        AfterValidator(_must_be_false),
        WithJsonSchema({"type": "boolean", "enum": [False]}),
    ]


_ROUNDED: Final = {"scale": ROUNDING_SCALE, "mode": "half_even", "exact": False}


def _raw_conditions(schema: dict[str, Any]) -> None:
    """``currency_raw`` present iff ``currency_source`` is not ``none`` (P-53), as JSON Schema
    ``if``/``then``."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"currency_source": {"enum": ["none"]}},
                "required": ["currency_source"],
            },
            "then": {"properties": {"currency_raw": {"type": "null"}}},
            "else": {
                "required": ["currency_raw"],
                "properties": {"currency_raw": {"type": "string", "minLength": 1}},
            },
        }
    ]


class ReportedCostRaw(HidingModel):
    """A reported source's cost values, verbatim and never parsed (ADR 0014 Decisions 12, 12b):
    the amount lexeme, the currency label as sent (``payload``) or as the cited API contract
    writes it (``api_contract``), or no currency (``none``). A present ``currency_raw`` is never
    empty or ASCII white space only, which is no label (D6)."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, json_schema_extra=_raw_conditions
    )

    reported_by: Annotated[str, ClosedSet(REPORTERS)]
    amount_raw: VerbatimText
    currency_raw: VerbatimText | None = None
    currency_source: Annotated[str, ClosedSet(CURRENCY_SOURCES)]

    @model_validator(mode="after")
    def _currency_raw_matches_source(self) -> ReportedCostRaw:
        message = None
        if self.currency_source == "none" and self.currency_raw is not None:
            message = "currency_source=none carries no currency_raw"
        elif self.currency_source != "none" and self.currency_raw is None:
            message = "currency_source payload or api_contract needs currency_raw"
        elif self.currency_raw is not None and _is_blank_label(self.currency_raw):
            message = (
                "currency_raw is never empty or ASCII white space only: such a label is no label "
                "(currency_source=none, D6)"
            )
        if message is not None:
            raise_problems(
                type(self).__name__, [Problem(("currency_raw",), "currency_raw", message)]
            )
        return self


# ---------------------------------------------------------------------------------- records


def _not_negative(money: Money) -> Money:
    if money.amount.startswith("-"):  # canonical text: negative iff it starts with "-"
        raise ValueError("a cost is never negative (P-38)")
    return money


NonNegativeMoney = Annotated[Money, AfterValidator(_not_negative)]

UsageRecordIds = Annotated[
    list[UsageRecordId],
    Field(json_schema_extra={"uniqueItems": True}),
    AfterValidator(_distinct("usage_record_ids")),
]
UnpricedUnits = Annotated[
    list[UsageUnit],
    Field(json_schema_extra={"uniqueItems": True}),
    AfterValidator(_distinct("unpriced_units")),
]

_NULL: Final = {"type": "null"}
_PRESENT: Final = {"not": {"type": "null"}}


def _when(field: str, values: Sequence[str], then: dict[str, Any], **other: Any) -> dict[str, Any]:
    rule = {
        "if": {"properties": {field: {"enum": list(values)}}, "required": [field]},
        "then": then,
    }
    rule.update(other)
    return rule


def _record_conditions(schema: dict[str, Any]) -> None:
    """The presence rules of a cost record (Decisions 12, 12b; N51, N67) as JSON Schema
    ``if``/``then``. Arithmetic and label rules stay code-only."""
    schema["allOf"] = [
        _when(
            "status",
            ["available"],
            {
                "required": ["amount", "basis"],
                "properties": {
                    "amount": _PRESENT,
                    "basis": _PRESENT,
                    "na_reason": _NULL,
                    "reported_raw": _NULL,
                    "unpriced_units": {"maxItems": 0},
                },
            },
            **{
                "else": {
                    "required": ["na_reason"],
                    "properties": {
                        "na_reason": _PRESENT,
                        **dict.fromkeys(_NOT_AVAILABLE_ABSENT, _NULL),
                    },
                }
            },
        ),
        _when(
            "basis",
            ["reported"],
            {
                "required": ["reported_by"],
                "properties": {
                    "reported_by": _PRESENT,
                    "pricing_snapshot": _NULL,
                    "rounding": _NULL,
                },
            },
        ),
        _when(
            "basis",
            ["computed"],
            {
                "required": ["pricing_snapshot"],
                "properties": {
                    "pricing_snapshot": _PRESENT,
                    "reported_by": _NULL,
                    "usage_record_ids": {"minItems": 1},
                },
            },
        ),
        _when(
            "na_reason",
            list(_RAW_REASONS),
            {"required": ["reported_raw"], "properties": {"reported_raw": _PRESENT}},
            **{"else": {"properties": {"reported_raw": _NULL}}},
        ),
        *(
            _when(
                "na_reason",
                [reason],
                {
                    "properties": {
                        "reported_raw": {"properties": {"currency_source": {"enum": list(sources)}}}
                    }
                },
            )
            for reason, sources in _ALLOWED_SOURCES.items()
        ),
        _when(
            "na_reason",
            ["unit_unpriced"],
            {"required": ["unpriced_units"], "properties": {"unpriced_units": {"minItems": 1}}},
            **{"else": {"properties": {"unpriced_units": {"maxItems": 0}}}},
        ),
        _when(
            "na_reason",
            list(_NO_USAGE_REASONS),
            {"properties": {"usage_record_ids": {"maxItems": 0}}},
        ),
        _when(
            "na_reason",
            list(_PRICED_USAGE_REASONS),
            {"properties": {"usage_record_ids": {"minItems": 1}}},
        ),
    ]


class CostRecord(HidingModel, EntityBase):
    """A cost of one domain (prefix ``cst``; a record of fact; ADR 0014 Decisions 12 to 16).

    ``status=available``: an ``amount`` (USD, >= 0) and a ``basis``, with no ``na_reason``, no
    ``reported_raw`` and no ``unpriced_units``. ``basis=reported`` names ``reported_by`` and has no
    ``pricing_snapshot`` and no ``rounding``. ``basis=computed`` has the ``pricing_snapshot`` it
    used, names no reporter, lists the usage records it priced, and carries ``rounding`` only when
    the amount was rounded (P-35).

    ``status=not_available``: an ``na_reason`` and nothing that could read as a cost (no
    ``amount``, ``basis``, ``reported_by``, ``pricing_snapshot`` or ``rounding``; never an amount
    of 0). ``reported_raw`` keeps a reported source's values verbatim and is present exactly for
    the four reasons ``unsupported_currency``, ``currency_not_reported``, ``amount_unparseable``
    and ``currency_conflict`` (N51). ``unpriced_units`` is non-empty exactly when the reason is
    ``unit_unpriced``. ``usage_record_ids`` has no repeats and follows the reason (N67): empty for
    ``no_usage`` and ``usage_not_reported``, non-empty for ``no_price_for_model``,
    ``unit_unpriced``, ``billing_rules_missing`` and ``ambiguous_price``.
    """

    model_config = ConfigDict(json_schema_extra=_record_conditions)

    id_prefix: ClassVar[str] = "cst"

    domain: Annotated[str, ClosedSet(DOMAINS)]
    usage_record_ids: UsageRecordIds
    status: Annotated[str, ClosedSet(RECORD_STATUSES)]
    amount: NonNegativeMoney | None = None
    basis: Annotated[str, ClosedSet(BASES)] | None = None
    reported_by: Annotated[str, ClosedSet(REPORTERS)] | None = None
    pricing_snapshot: PricingSnapshot | None = None
    rounding: CostRounding | None = None
    na_reason: Annotated[str, ClosedSet(NA_REASONS)] | None = None
    unpriced_units: UnpricedUnits = Field(default_factory=list)
    reported_raw: ReportedCostRaw | None = None

    @model_validator(mode="after")
    def _rules(self) -> CostRecord:
        problems: list[Problem] = []

        def bad(field: str, message: str) -> None:
            problems.append(Problem((field,), f"cost_{field}", message))

        if self.status == "available":
            self._available_rules(bad)
        else:
            self._not_available_rules(bad)
        raise_problems(type(self).__name__, problems)
        return self

    def _available_rules(self, bad: Any) -> None:
        if self.amount is None:
            bad("amount", "an available cost has an amount (USD)")
        if self.basis is None:
            bad("basis", "an available cost has a basis, reported or computed")
        if self.na_reason is not None:
            bad("na_reason", "an available cost has no na_reason")
        if self.reported_raw is not None:
            bad("reported_raw", "reported_raw belongs to a not_available record of a raw reason")
        if self.unpriced_units:
            bad("unpriced_units", "unpriced_units are listed only with na_reason=unit_unpriced")
        if self.basis == "reported":
            if self.reported_by is None:
                bad("reported_by", "basis=reported names who reported it (reported_by)")
            if self.pricing_snapshot is not None:
                bad("pricing_snapshot", "a reported cost carries no pricing_snapshot")
            if self.rounding is not None:
                bad("rounding", "a reported cost carries no rounding (it is not computed)")
        elif self.basis == "computed":
            if self.pricing_snapshot is None:
                bad("pricing_snapshot", "basis=computed needs the pricing_snapshot it used")
            if self.reported_by is not None:
                bad("reported_by", "a computed cost names no reporter")
            if not self.usage_record_ids:
                bad("usage_record_ids", "a computed cost names the usage records it priced")

    def _not_available_rules(self, bad: Any) -> None:
        for name in _NOT_AVAILABLE_ABSENT:
            if getattr(self, name) is not None:
                bad(name, f"a not_available cost has no {name} (never an amount of 0)")
        reason = self.na_reason
        if reason is None:
            bad("na_reason", "a not_available cost states its na_reason")
            return
        raw = self.reported_raw
        if reason in _RAW_REASONS and raw is None:
            bad("reported_raw", f"na_reason={reason} keeps the reported values in reported_raw")
        if reason not in _RAW_REASONS and raw is not None:
            bad("reported_raw", f"na_reason={reason} carries no reported_raw")
        if reason in _RAW_REASONS and raw is not None:
            message = _raw_mismatch(reason, raw)
            if message is not None:
                bad("reported_raw", message)
        if (reason == "unit_unpriced") != bool(self.unpriced_units):
            bad("unpriced_units", "unpriced_units are non-empty iff na_reason=unit_unpriced")
        if reason in _NO_USAGE_REASONS and self.usage_record_ids:
            bad("usage_record_ids", f"na_reason={reason} names no usage record (N67)")
        if reason in _PRICED_USAGE_REASONS and not self.usage_record_ids:
            bad("usage_record_ids", f"na_reason={reason} names the usage records it priced (N67)")


def _raw_mismatch(reason: str, raw: ReportedCostRaw) -> str | None:
    """Why ``raw`` contradicts the import steps for ``reason`` (Decisions 12b, 12c; N51 (c))."""
    if raw.currency_source not in _ALLOWED_SOURCES[reason]:
        allowed = " or ".join(_ALLOWED_SOURCES[reason])
        return f"na_reason={reason} goes with currency_source {allowed}"
    if raw.currency_raw is None:
        return None
    usd = normalize_currency_label(raw.currency_raw) == _USD
    if reason == "unsupported_currency" and usd:
        return (
            "a stored currency label that normalizes to USD is not unsupported_currency "
            "(the importer processes it as USD, Decision 12c)"
        )
    if reason in ("amount_unparseable", "currency_conflict") and not usd:
        return (
            f"na_reason={reason} keeps a currency label that normalizes to USD (Decision 12c); "
            "another label is unsupported_currency"
        )
    return None


# ----------------------------------------------------------------------------------- summary


def _exclusion_conditions(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        _when("reason", ["duplicate_of_reported"], {"properties": {"covered_by": {"minItems": 1}}})
    ]


class CostExclusion(BaseModel):
    """A record a summary leaves out of its total, and why (Decision 16a): the record's
    ``na_reason``, ``duplicate_of_reported`` (an available computed record set aside for an
    available reported one; ``covered_by`` non-empty) or ``overlaps_reported`` (an available
    computed record that only partly overlaps included reported records, N53). ``covered_by``
    lists the included records that cover all of the record's usage, else it is empty; it names
    each record at most once (N78)."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_exclusion_conditions)

    ref: CostRecordId
    reason: Annotated[str, ClosedSet(EXCLUSION_REASONS)]
    covered_by: Annotated[
        list[CostRecordId],
        Field(json_schema_extra={"uniqueItems": True}),
        AfterValidator(_distinct("covered_by")),
    ]

    @model_validator(mode="after")
    def _duplicate_is_covered(self) -> CostExclusion:
        if self.reason == "duplicate_of_reported" and not self.covered_by:
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("covered_by",),
                        "covered_by",
                        "duplicate_of_reported names the included reported record in covered_by",
                    )
                ],
            )
        return self


def _summary_conditions(schema: dict[str, Any]) -> None:
    """``total`` present iff something is included; ``none`` iff nothing is (Decision 16)."""
    schema["allOf"] = [
        {
            "if": {"properties": {"included": {"minItems": 1}}, "required": ["included"]},
            "then": {
                "required": ["total"],
                "properties": {
                    "total": _PRESENT,
                    "completeness": {"enum": ["complete", "partial"]},
                },
            },
            "else": {"properties": {"total": _NULL, "completeness": {"enum": ["none"]}}},
        }
    ]


def _expected_completeness(included: Sequence[Any], excluded: Sequence[CostExclusion]) -> str:
    if not included:
        return "none"
    if any(not exclusion.covered_by for exclusion in excluded):
        return "partial"
    return "complete"


class CostSummary(EntityBase):
    """The cost of one domain of a run (prefix ``csum``; a record of fact; Decision 16). The
    rules on the summary alone are checked here; the rules against its records are
    ``check_cost_summary``."""

    model_config = ConfigDict(json_schema_extra=_summary_conditions)

    id_prefix: ClassVar[str] = "csum"

    domain: Annotated[str, ClosedSet(DOMAINS)]
    total: NonNegativeMoney | None = None
    included: Annotated[list[CostRecordId], Field(json_schema_extra={"uniqueItems": True})]
    excluded: list[CostExclusion]
    completeness: Annotated[str, ClosedSet(COMPLETENESS)]

    @model_validator(mode="after")
    def _rules(self) -> CostSummary:
        problems: list[Problem] = []
        if len(set(self.included)) != len(self.included):
            problems.append(
                Problem(("included",), "included", "included names one cost record twice")
            )
        refs = [exclusion.ref for exclusion in self.excluded]
        if len(set(refs)) != len(refs):
            problems.append(
                Problem(("excluded",), "excluded", "excluded names one cost record twice")
            )
        included = set(self.included)
        if included & set(refs):
            problems.append(
                Problem(("excluded",), "excluded", "a cost record is either included or excluded")
            )
        if any(cover not in included for e in self.excluded for cover in e.covered_by):
            problems.append(
                Problem(
                    ("excluded",),
                    "covered_by",
                    "covered_by names only records that the summary includes",
                )
            )
        if bool(self.included) != (self.total is not None):
            problems.append(
                Problem(
                    ("total",),
                    "total",
                    "total is present iff at least one record is included (never a total of 0 "
                    "for nothing)",
                )
            )
        if self.completeness != _expected_completeness(self.included, self.excluded):
            problems.append(
                Problem(
                    ("completeness",),
                    "completeness",
                    "completeness is none iff nothing is included, partial if some exclusion is "
                    "uncovered (empty covered_by), else complete",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self


# ------------------------------------------------------------------------- price resolution


class PriceFacts(NamedTuple):
    """The facts an ``applies_when`` condition reads, passed by the caller (N46); ``None`` is
    unknown. The cost path never derives them from usage quantities."""

    context_tokens: int | None = None
    batch: bool | None = None
    region: str | None = None


class PriceResolution(NamedTuple):
    """The entry that prices one quantity, or why none does (exactly one is set):
    ``no_price_for_model`` or ``ambiguous_price``."""

    entry: PriceEntry | None
    na_reason: str | None


def _condition_holds(condition: AppliesWhenCondition, facts: PriceFacts) -> bool | None:
    """True, False, or ``None`` when the fact the condition reads is unknown (N46)."""
    value = condition.value
    if condition.kind in ("context_tokens_gt", "context_tokens_le"):
        if facts.context_tokens is None or type(value) is not int:
            return None
        if condition.kind == "context_tokens_gt":
            return facts.context_tokens > value
        return facts.context_tokens <= value
    if condition.kind == "batch":
        return None if facts.batch is None else facts.batch is value
    return None if facts.region is None else facts.region == value


def _applies(entry: PriceEntry, facts: PriceFacts) -> bool | None:
    """All conditions must hold (N49 (c)): a known false removes the entry even when another
    condition is undecidable; otherwise an unknown fact makes it undecidable (``None``)."""
    verdicts = [_condition_holds(condition, facts) for condition in entry.applies_when or ()]
    if any(verdict is False for verdict in verdicts):
        return False
    if any(verdict is None for verdict in verdicts):
        return None
    return True


def _check_facts(facts: PriceFacts) -> None:
    """``PriceFacts`` is a plain ``NamedTuple``; its values are checked here, so a fact of the
    wrong type is an error and never a silent mismatch."""
    if not isinstance(facts, PriceFacts):
        raise TypeError("facts must be a PriceFacts")
    tokens, batch, region = facts
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        raise TypeError("PriceFacts.context_tokens is an int >= 0 or None (unknown)")
    if batch is not None and type(batch) is not bool:
        raise TypeError("PriceFacts.batch is a bool or None (unknown)")
    if region is not None and type(region) is not str:
        raise TypeError("PriceFacts.region is a str or None (unknown)")


def _check_instant(name: str, moment: datetime | None) -> None:
    if moment is None:
        return
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime (UTC is never assumed)")


def resolve_price(
    catalog: PricingCatalog,
    *,
    provider: str,
    model: str | None,
    unit: str,
    occurred_at: datetime | None = None,
    run_started_at: datetime | None = None,
    facts: PriceFacts = PriceFacts(),  # noqa: B008 (an immutable NamedTuple)
) -> PriceResolution:
    """The entry of ``catalog`` that prices one quantity (ADR 0014 Decision 15).

    ``model`` ``None`` (the source reported no model, M3) matches no entry: the answer is
    ``no_price_for_model`` whatever the catalog holds, never a price of 0 and never a guess.
    Candidates: same ``provider``, ``model`` equal to the entry's model or one of its aliases,
    same ``unit``, all compared exactly. No candidate: ``no_price_for_model`` (before any date,
    N48). The price date is ``occurred_at``, else ``run_started_at``; neither: ``ambiguous_price``
    (P-36, never now). No candidate effective at the date: ``no_price_for_model``. Conditions
    (``facts``): a known false removes an entry; an undecidable entry, or two or more remaining
    entries, give ``ambiguous_price``; none remaining gives ``no_price_for_model``.
    """
    _check_facts(facts)
    _check_instant("occurred_at", occurred_at)
    _check_instant("run_started_at", run_started_at)
    if model is None:  # not reported: nothing to match (M3)
        return PriceResolution(None, "no_price_for_model")
    candidates = [
        entry
        for entry in catalog.entries
        if entry.provider == provider and entry.unit == unit and entry.matches_model(model)
    ]
    if not candidates:
        return PriceResolution(None, "no_price_for_model")
    moment = occurred_at if occurred_at is not None else run_started_at
    if moment is None:
        return PriceResolution(None, "ambiguous_price")
    effective = [entry for entry in candidates if entry.is_effective_at(moment)]
    verdicts = [(entry, _applies(entry, facts)) for entry in effective]
    if any(verdict is None for _entry, verdict in verdicts):
        return PriceResolution(None, "ambiguous_price")
    matching = [entry for entry, verdict in verdicts if verdict]
    if not matching:
        return PriceResolution(None, "no_price_for_model")
    if len(matching) > 1:
        return PriceResolution(None, "ambiguous_price")
    return PriceResolution(matching[0], None)


# ------------------------------------------------------------------------- exact arithmetic

_ONE: Final = Decimal(1)
_TWO: Final = Decimal(2)


def _exact_context(context: Context) -> Context:
    """A private copy of the caller's context on which every loss of exactness raises."""
    work = context.copy()
    work.clear_flags()
    for signal in (Inexact, InvalidOperation, DivisionByZero, Overflow):
        work.traps[signal] = True
    return work


def _event_cost(
    work: Context, quantity: Decimal, entry: PriceEntry
) -> tuple[Decimal, Decimal] | None:
    """One billable event (one usage record and one unit, Decision 13b) as an exact fraction
    ``(numerator, unit_quantity)``: the quantity rounded up to the increment, times the price,
    then lifted to the minimum charge. A zero quantity costs exactly 0 (N45 (b)): ``None``."""
    if quantity.is_zero():
        return None
    billing = entry.billing
    if isinstance(billing, str):  # "unknown" never reaches the arithmetic (Decision 13a)
        raise ValueError("an entry with billing 'unknown' cannot give a computed cost")
    billed = quantity
    if billing.increment != NONE:
        increment = Decimal(billing.increment)
        steps = work.divide_int(quantity, increment)
        if work.multiply(steps, increment) != quantity:
            steps = work.add(steps, _ONE)
        billed = work.multiply(steps, increment)
    denominator = Decimal(entry.unit_quantity)
    numerator = work.multiply(billed, Decimal(entry.price.amount))
    if not isinstance(billing.minimum, str):
        floor = work.multiply(Decimal(billing.minimum.amount), denominator)
        if numerator < floor:
            numerator = floor
    return numerator, denominator


def _round_half_even(
    work: Context, numerator: Decimal, denominator: Decimal
) -> tuple[Decimal, bool]:
    """``numerator / denominator`` rounded half-even at scale 18 by exact integer division, and
    whether the rounding changed the value."""
    scaled = work.scaleb(numerator, Decimal(ROUNDING_SCALE))
    quotient, remainder = work.divmod(scaled, denominator)
    twice = work.multiply(remainder, _TWO)
    if twice > denominator or (
        twice == denominator and not work.remainder(quotient, _TWO).is_zero()
    ):
        quotient = work.add(quotient, _ONE)
    return work.scaleb(quotient, Decimal(-ROUNDING_SCALE)), not remainder.is_zero()


def _exact_total(
    events: Sequence[tuple[Decimal, PriceEntry]], context: Context
) -> tuple[str, bool]:
    """The canonical amount of the events, rounded once (P-35, N56), and whether it was rounded.
    Events with one ``unit_quantity`` are summed over that denominator before the fractions are
    combined, so the arithmetic stays exact and small."""
    work = _exact_context(context)
    try:
        sums: dict[str, Decimal] = {}
        for quantity, entry in events:
            term = _event_cost(work, quantity, entry)
            if term is not None:
                numerator, _denominator = term
                key = entry.unit_quantity
                sums[key] = work.add(sums.get(key, Decimal(0)), numerator)
        numerator, denominator = Decimal(0), _ONE
        for key, partial in sums.items():
            unit_quantity = Decimal(key)
            numerator = work.add(
                work.multiply(numerator, unit_quantity), work.multiply(partial, denominator)
            )
            denominator = work.multiply(denominator, unit_quantity)
        amount, rounded = _round_half_even(work, numerator, denominator)
    except DecimalException as exc:
        raise ValueError(
            f"the decimal context (prec={context.prec}) is too small for an exact cost; pass a "
            "context with a larger precision (no amount is ever rounded silently)"
        ) from exc
    return canonical_decimal_text(amount), rounded


# ------------------------------------------------------------------------------ compute_cost


def _record(**fields: Any) -> CostRecord:
    return CostRecord.model_validate({"schema_version": SCHEMA_VERSION, **fields})


def compute_cost(
    usages: Sequence[UsageRecord],
    catalog: PricingCatalog,
    *,
    domain: str,
    record_id: CostRecordId,
    created_at: datetime,
    context: Context,
    run_started_at: datetime | None = None,
    facts: PriceFacts = PriceFacts(),  # noqa: B008 (an immutable NamedTuple)
) -> CostRecord:
    """The computed cost of ``usages`` in ``domain`` (ADR 0014 Decisions 13 to 15).

    ``context`` is a required ``decimal.Context`` (the thread's context is never used). A usage
    record of another domain, or one given twice, is a ``ValueError`` ("domain", "usage"). Each
    quantity of each usage record is priced on its own with ``resolve_price`` (price date: the
    usage's ``occurred_at``, else ``run_started_at``; N43). The record is available and
    ``computed`` only if every quantity has an entry with ``BillingRules``; otherwise it is
    not_available with the first reason of Decision 13a: no usage (``usage_not_reported`` for the
    agent, ``no_usage`` for the evaluation), ``no_price_for_model`` (no quantity priced),
    ``billing_rules_missing``, ``unit_unpriced`` (with ``unpriced_units``), ``ambiguous_price``.
    A usage without a model (M3) is never priced: alone it gives ``no_price_for_model``; mixed
    with priced usage it is an unpriced part (``unit_unpriced``). A partial sum is never
    reported. The amount is exact, rounded once half-even at scale 18
    with ``rounding`` recorded iff that changed it; ``pricing_snapshot`` names the catalog and the
    distinct entries used, zero quantities included (N63), in first-use order.
    """
    if not isinstance(context, Context):
        raise TypeError("context must be a decimal.Context (the thread's context is never used)")
    if domain not in DOMAINS:
        raise ValueError(f"domain must be one of {', '.join(DOMAINS)}")
    _check_facts(facts)
    _check_instant("run_started_at", run_started_at)
    usage_list = list(usages)
    for usage in usage_list:
        if usage.domain != domain:
            raise ValueError(
                f"usage record {usage.id} is of domain {usage.domain}, not {domain}: a cost record "
                "has one domain (N57)"
            )
    ids = [usage.id for usage in usage_list]
    if len(set(ids)) != len(ids):
        raise ValueError("a usage record is given twice; each usage counts once")
    common = {"id": record_id, "created_at": created_at, "domain": domain, "usage_record_ids": ids}
    if not usage_list:
        nothing = "usage_not_reported" if domain == "agent_under_test" else "no_usage"
        return _record(**common, status="not_available", na_reason=nothing)
    resolutions: list[tuple[str, Decimal, PriceResolution]] = []
    for usage in usage_list:
        for quantity in usage.quantities:
            resolution = resolve_price(
                catalog,
                provider=usage.provider,
                model=usage.model,
                unit=quantity.unit,
                occurred_at=usage.occurred_at,
                run_started_at=run_started_at,
                facts=facts,
            )
            resolutions.append((quantity.unit, Decimal(quantity.count), resolution))
    reason = _reason(resolutions)
    if reason is not None:
        unpriced: list[str] = []
        if reason == "unit_unpriced":
            missing = {unit for unit, _q, r in resolutions if r.na_reason == "no_price_for_model"}
            unpriced = [unit for unit in USAGE_UNITS if unit in missing]
        return _record(**common, status="not_available", na_reason=reason, unpriced_units=unpriced)
    events = [(count, r.entry) for _unit, count, r in resolutions if r.entry is not None]
    amount, rounded = _exact_total(events, context)
    keys: list[str] = []
    for _count, entry in events:
        if entry.entry_key not in keys:
            keys.append(entry.entry_key)
    snapshot = {
        "catalog_id": catalog.id,
        "version": catalog.version,
        "content_digest": catalog.content_digest,
        "entry_keys": keys,
    }
    return _record(
        **common,
        status="available",
        amount={"amount": amount, "currency": _USD},
        basis="computed",
        pricing_snapshot=snapshot,
        rounding=_ROUNDED if rounded else None,
    )


def _reason(resolutions: Sequence[tuple[str, Decimal, PriceResolution]]) -> str | None:
    """The first reason of Decision 13a that applies to resolved quantities, or ``None``."""
    unpriced = [r for _u, _q, r in resolutions if r.na_reason == "no_price_for_model"]
    if len(unpriced) == len(resolutions):
        return "no_price_for_model"
    if any(r.entry is not None and isinstance(r.entry.billing, str) for _u, _q, r in resolutions):
        return "billing_rules_missing"
    if unpriced:
        return "unit_unpriced"
    if any(r.na_reason == "ambiguous_price" for _u, _q, r in resolutions):
        return "ambiguous_price"
    return None


# --------------------------------------------------------------------------- snapshot check


def check_pricing_snapshot(snapshot: PricingSnapshot, catalog_doc: Any) -> None:
    """Verify a pricing snapshot against the catalog's JSON document (Decision 16b, N52): the
    rules of ``snapshot.check_pricing_catalog_ref`` for ``(catalog_id, version, content_digest)``
    (``ValueError``, or ``RefDigestMismatchError`` for comparable digests that differ), and, when
    the document carries ``entries``, every ``entry_keys`` member names an ``entry_key`` of the
    catalog (``ValueError`` saying "entry")."""
    reference = PricingCatalogRef(
        id=snapshot.catalog_id, version=snapshot.version, content_digest=snapshot.content_digest
    )
    check_pricing_catalog_ref(reference, catalog_doc)
    entries = catalog_doc.get("entries")
    if entries is None:
        return
    known = {entry.get("entry_key") for entry in entries if isinstance(entry, dict)}
    unknown = [key for key in snapshot.entry_keys if key not in known]
    if unknown:
        raise ValueError(
            f"the pricing snapshot names entry key(s) {', '.join(map(repr, unknown))} that no "
            f"entry of catalog {snapshot.catalog_id} version {snapshot.version} has"
        )


# ---------------------------------------------------------------------------------- summaries


def _usage(record: CostRecord) -> set[UsageRecordId]:
    return set(record.usage_record_ids)


def _covering(record: CostRecord, included: Sequence[CostRecord]) -> list[TypedId]:
    """The included records (in their order) that share usage with ``record``, if together they
    cover all of its usage; a record without usage is never covered (N53 remainder)."""
    wanted = _usage(record)
    if not wanted:
        return []
    sharing = [other for other in included if _usage(other) & wanted]
    covered: set[UsageRecordId] = set()
    for other in sharing:
        covered |= _usage(other)
    return [other.id for other in sharing] if wanted <= covered else []


def _check_domain(records: Iterable[CostRecord], domain: str) -> None:
    for record in records:
        if record.domain != domain:
            raise ValueError(
                f"cost record {record.id} is of domain {record.domain}, not {domain}: a summary "
                "has one domain"
            )


def _check_no_shared_usage(records: Sequence[CostRecord], what: str) -> None:
    owner: dict[UsageRecordId, TypedId] = {}
    for record in records:
        for usage in record.usage_record_ids:
            if usage in owner:
                raise ValueError(
                    f"usage record {usage} is in {owner[usage]} and {record.id}, {what} (P-54: "
                    "each usage record in at most one included cost)"
                )
            owner[usage] = record.id


def _sum(records: Iterable[CostRecord]) -> Money | None:
    total: Money | None = None
    for record in records:
        if record.amount is not None:
            total = record.amount if total is None else total + record.amount
    return total


def summarize_costs(
    records: Sequence[CostRecord],
    *,
    domain: str,
    summary_id: CostSummaryId,
    created_at: datetime,
) -> CostSummary:
    """The summary of the cost records of one domain (Decisions 16, 16a, 16b; P-54, N53).

    Available reported records are included. An available computed record is excluded as
    ``duplicate_of_reported`` when its usage lies entirely inside the usage of available reported
    records (``covered_by`` names them), as ``overlaps_reported`` when it shares only part of it
    (``covered_by`` empty; never split), and included otherwise. Not-available records are
    excluded with their ``na_reason`` and ``covered_by`` naming the included records that cover
    all of their usage (else empty). Input order is kept. ``ValueError`` for a record of another
    domain ("domain"), a record given twice ("record"), and two available records of one basis
    sharing a usage record ("usage"). ``total`` is the exact sum, absent when nothing is included.
    """
    record_list = list(records)
    _check_domain(record_list, domain)
    if len({record.id for record in record_list}) != len(record_list):
        raise ValueError("a cost record is given twice")
    available = [record for record in record_list if record.status == "available"]
    reported = [record for record in available if record.basis == "reported"]
    computed = [record for record in available if record.basis == "computed"]
    _check_no_shared_usage(reported, "two available reported records for one usage")
    _check_no_shared_usage(computed, "two available computed records for one usage")
    reported_usage: set[UsageRecordId] = set()
    for record in reported:
        reported_usage |= _usage(record)
    set_aside: dict[TypedId, tuple[str, list[TypedId]]] = {}
    for record in computed:
        shared = _usage(record) & reported_usage
        if not shared:
            continue
        if shared == _usage(record):
            covers = [other.id for other in reported if _usage(other) & shared]
            set_aside[record.id] = ("duplicate_of_reported", covers)
        else:
            set_aside[record.id] = ("overlaps_reported", [])
    included = [
        record
        for record in record_list
        if record.status == "available" and record.id not in set_aside
    ]
    excluded: list[dict[str, Any]] = []
    for record in record_list:
        if record.id in set_aside:
            reason, covers = set_aside[record.id]
        elif record.status == "not_available":
            reason, covers = str(record.na_reason), _covering(record, included)
        else:
            continue
        excluded.append({"ref": record.id, "reason": reason, "covered_by": covers})
    exclusions = [CostExclusion.model_validate(item) for item in excluded]
    return CostSummary.model_validate(
        {
            "id": summary_id,
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "domain": domain,
            "total": _sum(included),
            "included": [record.id for record in included],
            "excluded": exclusions,
            "completeness": _expected_completeness(included, exclusions),
        }
    )


def check_cost_summary(summary: CostSummary, records: Sequence[CostRecord]) -> None:
    """Check a stored summary against its records (Decisions 16 to 16b; N22: a bundle-level
    check). ``ValueError`` whose message holds the bracketed word: a record named by the summary
    and not supplied, or supplied twice [record]; a supplied record of another domain [domain]; an
    included record that is not available [available]; two included records sharing a usage
    record [usage]; a total that is not the exact sum of the included amounts [total]; an
    exclusion whose reason is not the record's ``na_reason``, or an available record excluded
    with another reason than ``duplicate_of_reported`` or ``overlaps_reported`` [reason,
    available]; ``duplicate_of_reported`` or ``overlaps_reported`` on a record that does not
    qualify [duplicate, reported, overlap]; a ``covered_by`` other than the included records that
    cover the record's usage [covered_by]; a supplied record neither included nor excluded
    [excluded]. The order of ``included``, ``excluded`` and ``covered_by`` is not checked."""
    record_list = list(records)
    by_id: dict[TypedId, CostRecord] = {}
    for record in record_list:
        if record.id in by_id:
            raise ValueError(f"cost record {record.id} is supplied twice")
        by_id[record.id] = record
    named = [*summary.included, *(e.ref for e in summary.excluded)]
    named += [cover for e in summary.excluded for cover in e.covered_by]
    for record_id in named:
        if record_id not in by_id:
            raise ValueError(f"cost record {record_id} named by the summary is not supplied")
    _check_domain(record_list, summary.domain)
    included = [by_id[record_id] for record_id in summary.included]
    for record in included:
        if record.status != "available":
            raise ValueError(f"included cost record {record.id} is not available")
    _check_no_shared_usage(included, "both included")
    expected_total = _sum(included)
    if expected_total != summary.total:
        raise ValueError("the summary total is not the exact sum of the included amounts")
    reported_usage: set[UsageRecordId] = set()
    for record in included:
        if record.basis == "reported":
            reported_usage |= _usage(record)
    for exclusion in summary.excluded:
        _check_exclusion(exclusion, by_id[exclusion.ref], included, reported_usage)
    accounted = set(named)
    for record in record_list:
        if record.id not in accounted:
            raise ValueError(f"cost record {record.id} is neither included nor excluded")


def _check_exclusion(
    exclusion: CostExclusion,
    record: CostRecord,
    included: Sequence[CostRecord],
    reported_usage: set[UsageRecordId],
) -> None:
    reason = exclusion.reason
    if record.status == "not_available":
        if reason != record.na_reason:
            raise ValueError(
                f"the exclusion of cost record {record.id} gives the reason {reason}, not the "
                f"record's na_reason {record.na_reason}"
            )
    elif reason not in ("duplicate_of_reported", "overlaps_reported"):
        raise ValueError(
            f"cost record {record.id} is available: an available record is excluded only as "
            f"duplicate_of_reported or overlaps_reported, not with the reason {reason}"
        )
    else:
        usage = _usage(record)
        if record.basis != "computed":
            raise ValueError(
                f"the reason {reason} applies only to an available computed record; cost record "
                f"{record.id} is reported (reported records are preferred, P-54)"
            )
        if reason == "duplicate_of_reported" and not (usage and usage <= reported_usage):
            raise ValueError(
                f"duplicate_of_reported needs included reported records that cover all usage of "
                f"cost record {record.id}"
            )
        if reason == "duplicate_of_reported" and not any(
            other.basis == "reported" and other.id in exclusion.covered_by for other in included
        ):
            raise ValueError(
                f"the duplicate_of_reported exclusion of cost record {record.id} names no included "
                "reported record in covered_by"
            )
        if reason == "overlaps_reported" and not usage & reported_usage:
            raise ValueError(
                f"overlaps_reported: cost record {record.id} shares no usage with an included "
                "reported record (no overlap)"
            )
        if reason == "overlaps_reported" and usage <= reported_usage:
            raise ValueError(
                f"cost record {record.id} is fully covered by included reported records: that is "
                "duplicate_of_reported, not an overlap"
            )
    if set(exclusion.covered_by) != set(_covering(record, included)):
        raise ValueError(
            f"covered_by of the {reason} exclusion of cost record {record.id} is not the set of "
            "included records that cover all of its usage (empty when they do not)"
        )


# ---------------------------------------------------------------------------------- importer


class ContractCurrency(NamedTuple):
    """The currency a source's published API contract states for a cost field, with the citation
    of the mapping's field-provenance table (P-51, P-53). A citation needs a non-empty ``url`` and
    a ``retrieved_at``; otherwise it counts as no contract."""

    currency: str
    url: str | None
    retrieved_at: datetime | None


class ImportedCost(NamedTuple):
    """The record of an imported reported cost and the ingest warnings of the import."""

    record: CostRecord
    warnings: list[IngestWarning]


_LEXICAL_AMOUNT: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*)?")
_CANONICAL_AMOUNT: Final = re.compile(DECIMAL_PATTERN[1:-1])


def _normalized_amount(text: str) -> str | None:
    """The canonical spelling of a non-negative decimal lexeme that differs from it only by
    trailing fractional zeros or a trailing point (Decision 1a), else ``None`` (no sign, exponent,
    separator, white space, leading zero or ``.5`` form; nothing beyond scale 18)."""
    if _LEXICAL_AMOUNT.fullmatch(text) is None:
        return None
    canonical = text.rstrip("0").rstrip(".") if "." in text else text
    return canonical if _CANONICAL_AMOUNT.fullmatch(canonical) is not None else None


def _is_cited(contract: ContractCurrency | None) -> bool:
    return (
        contract is not None
        and isinstance(contract.url, str)
        and contract.url != ""
        and contract.retrieved_at is not None
    )


def _code_text(label: str) -> str:
    """How a warning names a currency label: with its ISO 4217 name when the vendored file knows
    the label exactly as written."""
    name = iso4217_currency_name(label)
    return f"{label} ({name})" if name is not None else repr(label)


def _lone_surrogates(text: str) -> str:
    """The lone surrogates of ``text`` as ``U+XXXX at index i`` (code point indexes), for a
    message that names what was replaced without holding it."""
    return ", ".join(
        f"U+{ord(ch):04X} at index {index}"
        for index, ch in enumerate(text)
        if 0xD800 <= ord(ch) <= 0xDFFF
    )


def import_reported_cost(
    *,
    domain: str,
    usage_record_ids: Sequence[UsageRecordId],
    reported_by: str,
    amount_raw: str,
    currency_raw: str | None,
    record_id: CostRecordId,
    created_at: datetime,
    contract: ContractCurrency | None = None,
    currency_source_path: str | None = None,
    amount_source_path: str | None = None,
) -> ImportedCost:
    """Turn a reported cost into a ``CostRecord`` and its ingest warnings (ADR 0014 Decision 12c);
    the import is never rejected for what the source sent.

    Steps: (0) text that is not valid Unicode (a lone surrogate, which cannot be written as UTF-8)
    is stored with each lone surrogate replaced by U+FFFD and records ``text_not_valid_unicode``,
    label first, then amount; such a label is unparseable (``unsupported_currency``) and such an
    amount too (``amount_unparseable`` once the currency is USD) (N82). (1) The label is trimmed
    of ASCII white space and ASCII-folded only (P-52); an empty or blank label is no label (D6).
    (2) A payload label wins over a cited contract; every
    disagreement records ``currency_contract_conflict`` (N68); a USD label against a non-USD
    contract is ``currency_conflict``. A label and a contract agree when both are USD after P-52
    or the label equals the contract's code exactly (N73). (3) No label: a cited contract's
    currency (``api_contract``), else ``currency_not_reported``. (4) Only a USD amount is parsed:
    canonical, lexically normalized (``decimal_normalized``) or ``amount_unparseable``. (5) Each
    warning carries ``currency_source_path`` or ``amount_source_path`` for its field, as passed
    (N69, N74). Warnings follow the order of these steps (N75).
    """
    warnings: list[IngestWarning] = []

    def warn(code: str, raw: str | None, path: str | None, message: str) -> None:
        warnings.append(IngestWarning(code=code, raw=raw, path=path, message=message))

    def raw_record(reason: str, source: str, currency: str | None) -> ImportedCost:
        raw = {
            "reported_by": reported_by,
            "amount_raw": amount_raw,
            "currency_raw": currency,
            "currency_source": source,
        }
        record = _record(
            id=record_id,
            created_at=created_at,
            domain=domain,
            usage_record_ids=list(usage_record_ids),
            status="not_available",
            na_reason=reason,
            reported_raw=raw,
        )
        return ImportedCost(record, warnings)

    if not isinstance(amount_raw, str):
        raise TypeError("amount_raw is the source lexeme as text (never a parsed number)")
    if currency_raw is not None and not isinstance(currency_raw, str):
        raise TypeError("currency_raw is the currency label as text, or None when none was sent")

    # step 0 (N82): never raise on the text of the source; replace lone surrogates and flag them
    label_damaged = currency_raw is not None and (
        replace_lone_surrogates(currency_raw) != currency_raw
    )
    amount_damaged = replace_lone_surrogates(amount_raw) != amount_raw
    if currency_raw is not None and label_damaged:
        where = _lone_surrogates(currency_raw)
        currency_raw = replace_lone_surrogates(currency_raw)
        warn(
            "text_not_valid_unicode",
            currency_raw,
            currency_source_path,
            f"the reported currency label is not valid Unicode text (lone surrogate {where}); "
            "each lone surrogate is stored as U+FFFD and the label is treated as unparseable",
        )
    if amount_damaged:
        where = _lone_surrogates(amount_raw)
        amount_raw = replace_lone_surrogates(amount_raw)
        warn(
            "text_not_valid_unicode",
            amount_raw,
            amount_source_path,
            f"the reported amount is not valid Unicode text (lone surrogate {where}); each lone "
            "surrogate is stored as U+FFFD and the amount is treated as unparseable",
        )

    cited = contract if _is_cited(contract) else None
    if cited is not None and _is_blank_label(cited.currency):
        raise ValueError("a cited contract states a currency code (contract.currency is blank)")
    contract_usd = cited is not None and normalize_currency_label(cited.currency) == _USD
    has_label = currency_raw is not None and not _is_blank_label(currency_raw)

    if currency_raw is not None and has_label:
        label = currency_raw
        # a damaged label never normalizes to USD (U+FFFD is no ASCII white space); stated anyway
        label_usd = not label_damaged and normalize_currency_label(label) == _USD
        if label_usd and label != _USD:
            warn(
                "currency_label_normalized",
                label,
                currency_source_path,
                "the reported currency label was normalized to USD (ASCII case and white space "
                "only; P-52)",
            )
        if cited is not None and not ((label_usd and contract_usd) or label == cited.currency):
            warn(
                "currency_contract_conflict",
                label,
                currency_source_path,
                f"the payload currency label {echoed(label)} disagrees with the cited API "
                f"contract, which states {cited.currency}; the payload label is kept",
            )
            if label_usd:
                return raw_record("currency_conflict", "payload", label)
        if not label_usd:
            warn(
                "unsupported_currency",
                label,
                currency_source_path,
                f"the reported currency {_code_text(label)} is not supported in {SCHEMA_VERSION} "
                "(only USD); the cost is not_available and the reported values are kept "
                "unconverted",
            )
            return raw_record("unsupported_currency", "payload", label)
        source, currency = "payload", label
    elif cited is not None:
        if not contract_usd:
            warn(
                "unsupported_currency",
                currency_raw,  # None when the payload sent no label; a blank label as sent (D6)
                currency_source_path,
                f"the cited API contract states the currency {_code_text(cited.currency)}, which "
                f"is not supported in {SCHEMA_VERSION} (only USD); the reported values are kept "
                "unconverted",
            )
            return raw_record("unsupported_currency", "api_contract", cited.currency)
        source, currency = "api_contract", cited.currency
    else:
        warn(
            "currency_not_reported",
            currency_raw,
            currency_source_path,
            "the reported cost carries no currency and no cited API contract states one; USD is "
            "never assumed",
        )
        return raw_record("currency_not_reported", "none", None)

    canonical = None if amount_damaged else _normalized_amount(amount_raw)
    if canonical is None:
        warn(
            "amount_unparseable",
            amount_raw,
            amount_source_path,
            "the reported amount is not a non-negative decimal (no exponent, sign, separator or "
            "white space is read); the cost is not_available and the lexeme is kept",
        )
        return raw_record("amount_unparseable", source, currency)
    if canonical != amount_raw:
        warn(
            "decimal_normalized",
            amount_raw,
            amount_source_path,
            f"the reported amount is stored in its canonical spelling {canonical} (trailing zeros "
            "or point removed; the value is unchanged)",
        )
    record = _record(
        id=record_id,
        created_at=created_at,
        domain=domain,
        usage_record_ids=list(usage_record_ids),
        status="available",
        amount={"amount": canonical, "currency": _USD},
        basis="reported",
        reported_by=reported_by,
    )
    return ImportedCost(record, warnings)
