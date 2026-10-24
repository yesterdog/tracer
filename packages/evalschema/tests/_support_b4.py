# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 4 tests (P1-23 usage, P1-24 pricing, P1-25 cost). No tests here.

The implementation modules are imported lazily through ``api()`` (see ``_support``), so a module
the architect has not written yet fails the test that needs it and never breaks collection. The
module, class and function names are listed in docs/phase1/api-surface.md, section 23:
``tracer_evalschema.usage``, ``tracer_evalschema.pricing`` and ``tracer_evalschema.cost``.

Revision 16 (api-surface 23.6, N43 to N69): ``UsageRecord.occurred_at``, ``PriceEntry`` as a value
object with ``entry_key``, ``PricingSnapshot.entry_keys``, ten ``na_reason`` members.

Documents are JSON-mode dicts. ``usage_doc()``, ``entry_doc()``, ``catalog_doc()`` and the cost
document builders return complete valid documents; ``make_usage()``, ``make_entry()``,
``make_catalog()`` (sealed with ``_support_b2.seal``) validate them. ``compute()`` calls
``cost.compute_cost`` with the keyword arguments that every call needs.
"""

from __future__ import annotations

import decimal
import itertools
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ._support import api
from ._support_b2 import meta, new_id, registered_class, seal
from .support.mappings.common import format_utc_instant

__all__ = [
    "ALL_UNITS",
    "DOMAINS",
    "ENTITY_SPECS4",
    "EXCLUSION_REASONS",
    "INTEGER_UNITS",
    "NA_REASONS",
    "SECONDS_UNITS",
    "SOURCES",
    "STAGES",
    "VERSIONED_NAMES4",
    "billing",
    "catalog_doc",
    "catalog_of",
    "cls4",
    "compute",
    "cost_class",
    "cost_fn",
    "ctx",
    "doc4",
    "entity_class4",
    "entry_doc",
    "make4",
    "make_catalog",
    "make_entry",
    "make_usage",
    "pricing_class",
    "provenance_doc",
    "qty",
    "record_computed_doc",
    "record_na_doc",
    "record_reported_doc",
    "reported_raw_doc",
    "schema_core",
    "snapshot_doc",
    "summary_doc",
    "usage_class",
    "usage_doc",
    "usage_for",
    "usd",
    "utc_text",
    "when",
]

# ------------------------------------------------------------------------------ closed sets

ALL_UNITS: tuple[str, ...] = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "audio_input_seconds",
    "audio_output_seconds",
    "input_characters",
    "requests",
)
SECONDS_UNITS: tuple[str, ...] = ("audio_input_seconds", "audio_output_seconds")
INTEGER_UNITS: tuple[str, ...] = tuple(u for u in ALL_UNITS if u not in SECONDS_UNITS)
DOMAINS: tuple[str, ...] = ("evaluation", "agent_under_test")
STAGES: tuple[str, ...] = (
    "stt",
    "llm",
    "tts",
    "judge",
    "sim_caller_llm",
    "sim_caller_tts",
    "realtime",
    "other",
)
SOURCES: tuple[str, ...] = ("provider_reported", "telemetry", "measured")
NA_REASONS: tuple[str, ...] = (
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
EXCLUSION_REASONS: tuple[str, ...] = (*NA_REASONS, "duplicate_of_reported", "overlaps_reported")


# --------------------------------------------------------------------------------- lookups


def usage_class(name: str) -> Any:
    return getattr(api("usage"), name)


def pricing_class(name: str) -> Any:
    return getattr(api("pricing"), name)


def cost_class(name: str) -> Any:
    return getattr(api("cost"), name)


def cost_fn(name: str) -> Any:
    return getattr(api("cost"), name)


# ----------------------------------------------------------------------------- small values


def usd(amount: str) -> dict[str, str]:
    """A ``Money`` document in USD."""
    return {"amount": amount, "currency": "USD"}


def when(text: str) -> datetime:
    """An aware UTC ``datetime`` from an ISO text (``2026-06-01T00:00:00Z``)."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def ctx() -> decimal.Context:
    """The explicit decimal context the tests pass to ``compute_cost`` (large precision)."""
    return decimal.Context(prec=120, rounding=decimal.ROUND_HALF_EVEN)


def schema_core(node: Mapping[str, Any]) -> dict[str, Any]:
    """A schema node without its documentation keys (``title``, ``description``)."""
    return {k: v for k, v in node.items() if k not in ("title", "description")}


def qty(unit: Any, count: Any) -> dict[str, Any]:
    return {"unit": unit, "count": count}


# ---------------------------------------------------------------------------------- usage


def utc_text(moment: datetime) -> str:
    """The canonical wire text of an aware instant (``2026-06-01T00:00:00.000000Z``)."""
    return format_utc_instant(moment.astimezone(UTC))


def usage_doc(occurred_at: Any = None, **over: Any) -> dict[str, Any]:
    """A complete evaluation-domain judge usage record (provider-reported). ``occurred_at`` (N43)
    is absent unless given, as text or as an aware ``datetime``."""
    doc: dict[str, Any] = {
        **meta("use"),
        "run_id": new_id("run"),
        "domain": "evaluation",
        "stage": "judge",
        "provider": "openai",
        "model": "gpt-4.1",
        "quantities": [qty("input_tokens", 1000), qty("output_tokens", 200)],
        "source": "provider_reported",
    }
    if occurred_at is not None:
        doc["occurred_at"] = (
            utc_text(occurred_at) if isinstance(occurred_at, datetime) else occurred_at
        )
    doc.update(over)
    return doc


def make_usage(occurred_at: Any = None, **over: Any) -> Any:
    return usage_class("UsageRecord").model_validate(usage_doc(occurred_at, **over))


def usage_for(unit: str, count: Any, occurred_at: str | datetime | None = None, **over: Any) -> Any:
    """A validated usage record with one quantity."""
    return make_usage(occurred_at, quantities=[qty(unit, count)], **over)


# --------------------------------------------------------------------------------- pricing


def provenance_doc(**over: Any) -> dict[str, Any]:
    return {
        "kind": "provider_price_page",
        "url": "https://example.com/pricing",
        "retrieved_at": "2026-10-01T00:00:00.000000Z",
        **over,
    }


def billing(increment: Any = "none", minimum: Any = "none") -> dict[str, Any]:
    """A ``BillingRules`` document; each part is a value or the literal ``"none"``."""
    return {"increment": increment, "minimum": minimum}


_ENTRY_KEYS = itertools.count(1)
USER_NOTE = "converted at 83.10 INR per USD on 2026-10-01"


def entry_doc(**over: Any) -> dict[str, Any]:
    """A complete catalog entry: input tokens of gpt-4.1, 0.15 USD per 1M, no increment/minimum.

    A ``PriceEntry`` is a value object (N47): no ``id``, ``schema_version``, ``created_at`` or
    ``metadata``. A ``user_supplied`` entry gets a provenance note unless ``provenance=`` is given
    (N50 remainder (c)). It carries an author-assigned ``entry_key`` (N61); every call makes a new
    key so that a catalog of several default entries is valid, and a test that needs a given key
    passes ``entry_key=``."""
    if over.get("price_source") == "user_supplied" and "provenance" not in over:
        # N50 remainder (c): a user-supplied price needs a provenance note
        over = {**over, "provenance": provenance_doc(note=USER_NOTE)}
    return {
        "entry_key": f"price-{next(_ENTRY_KEYS):05d}",
        "provider": "openai",
        "model": "gpt-4.1",
        "aliases": [],
        "unit": "input_tokens",
        "unit_quantity": "1000000",
        "price": usd("0.15"),
        "billing": billing(),
        "effective_from": "2026-01-01T00:00:00.000000Z",
        "price_source": "catalog",
        "provenance": provenance_doc(),
        **over,
    }


def make_entry(**over: Any) -> Any:
    return pricing_class("PriceEntry").model_validate(entry_doc(**over))


def catalog_doc(entries: Sequence[Mapping[str, Any]] | None = None, **over: Any) -> dict[str, Any]:
    return {
        **meta("pcat"),
        "version": 1,
        "entries": [dict(e) for e in (entries if entries is not None else [entry_doc()])],
        "published_at": "2026-10-01T00:00:00.000000Z",
        **over,
    }


def make_catalog(entries: Sequence[Mapping[str, Any]] | None = None, **over: Any) -> Any:
    """A validated, correctly sealed ``PricingCatalog``."""
    model = pricing_class("PricingCatalog")
    return model.model_validate(seal(model, catalog_doc(entries, **over)))


def catalog_of(*entry_overrides: Mapping[str, Any], **over: Any) -> Any:
    """A sealed catalog with one entry per override mapping (each over ``entry_doc()``)."""
    return make_catalog([entry_doc(**dict(o)) for o in entry_overrides], **over)


# -------------------------------------------------------------------------------- cost docs


def snapshot_doc(catalog: Any, entry_keys: Any = None, **over: Any) -> dict[str, Any]:
    """A ``PricingSnapshot`` document naming ``catalog`` and some of its entries by key (N61)."""
    keys = entry_keys if entry_keys is not None else [e.entry_key for e in catalog.entries[:1]]
    return {
        "catalog_id": str(catalog.id),
        "version": catalog.version,
        "content_digest": catalog.content_digest.model_dump(mode="json", exclude_none=True),
        "entry_keys": list(keys),
        **over,
    }


def reported_raw_doc(**over: Any) -> dict[str, Any]:
    """A ``ReportedCostRaw`` for a payload currency label that is not USD."""
    return {
        "reported_by": "provider",
        "amount_raw": "12.50",
        "currency_raw": "EUR",
        "currency_source": "payload",
        **over,
    }


def record_reported_doc(**over: Any) -> dict[str, Any]:
    """An available, reported, 1.5 USD cost record of one usage record."""
    return {
        **meta("cst"),
        "domain": "evaluation",
        "usage_record_ids": [new_id("use")],
        "status": "available",
        "amount": usd("1.5"),
        "basis": "reported",
        "reported_by": "provider",
        "unpriced_units": [],
        **over,
    }


def record_computed_doc(catalog: Any = None, **over: Any) -> dict[str, Any]:
    """An available, computed, 12.5 USD cost record that names a catalog snapshot."""
    catalog = catalog if catalog is not None else make_catalog()
    return {
        **meta("cst"),
        "domain": "evaluation",
        "usage_record_ids": [new_id("use")],
        "status": "available",
        "amount": usd("12.5"),
        "basis": "computed",
        "pricing_snapshot": snapshot_doc(catalog),
        "unpriced_units": [],
        **over,
    }


def record_na_doc(reason: str = "no_price_for_model", **over: Any) -> dict[str, Any]:
    """A not-available cost record. The reason decides which companion fields are valid:
    ``unit_unpriced`` lists a unit, the four currency reasons carry ``reported_raw``
    (``amount_unparseable`` and ``currency_conflict`` with a payload label that is USD, N51/N59).
    ``usage_record_ids`` follows N67: empty for ``no_usage`` and ``usage_not_reported``, one usage
    record for every other reason."""
    doc: dict[str, Any] = {
        **meta("cst"),
        "domain": "evaluation",
        "usage_record_ids": [new_id("use")],
        "status": "not_available",
        "na_reason": reason,
        "unpriced_units": [],
    }
    if reason == "unit_unpriced":
        doc["unpriced_units"] = ["output_tokens"]
    if reason == "unsupported_currency":
        doc["reported_raw"] = reported_raw_doc()
    if reason == "currency_not_reported":
        doc["reported_raw"] = {
            "reported_by": "provider",
            "amount_raw": "12.50",
            "currency_source": "none",
        }
    if reason == "amount_unparseable":
        doc["reported_raw"] = reported_raw_doc(amount_raw="abc", currency_raw="USD")
    if reason == "currency_conflict":
        doc["reported_raw"] = reported_raw_doc(amount_raw="12.50", currency_raw="USD")
    if reason in ("no_usage", "usage_not_reported"):
        doc["usage_record_ids"] = []
    doc.update(over)
    return doc


def summary_doc(**over: Any) -> dict[str, Any]:
    """A cost summary with one included record and no exclusion (total 12.5 USD)."""
    return {
        **meta("csum"),
        "domain": "evaluation",
        "total": usd("12.5"),
        "included": [new_id("cst")],
        "excluded": [],
        "completeness": "complete",
        **over,
    }


# --------------------------------------------------------------------------- compute_cost


def compute(usages: Sequence[Any], catalog: Any, **over: Any) -> Any:
    """``cost.compute_cost`` with the keyword arguments every call needs (api-surface 23.1)."""
    kwargs: dict[str, Any] = {
        "domain": "evaluation",
        "record_id": api("ids").CostRecordId.new(),
        "created_at": when("2026-10-23T00:00:00Z"),
        "run_started_at": when("2026-10-20T00:00:00Z"),
        "context": ctx(),
    }
    kwargs.update(over)
    return cost_fn("compute_cost")(usages, catalog, **kwargs)


# the registry helper is re-exported for the test modules of this batch
_UNUSED = registered_class


# ------------------------------------------------------------------- the five entities


# (module, class name, id prefix, versioned) of every entity of batch 4. PriceEntry is not an
# entity since revision 15 (N47): it is a value object inside its catalog version, identified by an
# author-assigned ``entry_key`` (N61), and the prefix ``pce`` is withdrawn. Only the catalog is
# versioned.
ENTITY_SPECS4: tuple[tuple[str, str, str, bool], ...] = (
    ("usage", "UsageRecord", "use", False),
    ("pricing", "PricingCatalog", "pcat", True),
    ("cost", "CostRecord", "cst", False),
    ("cost", "CostSummary", "csum", False),
)
VERSIONED_NAMES4 = tuple(spec[1] for spec in ENTITY_SPECS4 if spec[3])


def cls4(module: str, name: str) -> Any:
    return getattr(api(module), name)


def entity_class4(name: str) -> Any:
    for module, class_name, _prefix, _versioned in ENTITY_SPECS4:
        if class_name == name:
            return cls4(module, class_name)
    raise KeyError(name)


def doc4(name: str, /, **over: Any) -> dict[str, Any]:
    """The minimal valid document of a batch 4 entity, overrides applied (no digest)."""
    builders: dict[str, Any] = {
        "UsageRecord": usage_doc,
        "PricingCatalog": catalog_doc,
        "CostRecord": record_reported_doc,
        "CostSummary": summary_doc,
    }
    return {**builders[name](), **over}


def make4(name: str, /, **over: Any) -> Any:
    """The validated batch 4 entity ``name`` (the catalog is sealed first)."""
    model = entity_class4(name)
    doc = doc4(name, **over)
    return model.model_validate(seal(model, doc) if name in VERSIONED_NAMES4 else doc)
