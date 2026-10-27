# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Cost of our speech-to-text through the evalschema pricing functions (ADR 0024 rule 14).

Tracer ships no price. A cost is computed only from a USD catalog the user supplied; without one
every record is ``not_available``. Nothing is converted from another currency and nothing is
ever reported as zero for want of a price.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Context
from typing import Any, Final

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.cost import CostRecord, CostSummary, compute_cost, summarize_costs
from tracer_evalschema.digest import compute_digest
from tracer_evalschema.ids import CostRecordId, CostSummaryId, PricingCatalogId
from tracer_evalschema.loader import loads_strict
from tracer_evalschema.pricing import PriceEntry, PriceProvenance, PricingCatalog
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "CatalogRefusedError",
    "SttCost",
    "compute_stt_cost",
    "compute_usage_cost",
    "load_user_catalog",
]

_DOMAIN: Final = "evaluation"
_PRECISION: Final = 200
_USER_FILE_MEMBERS: Final = frozenset({"published_at", "entries"})


class CatalogRefusedError(ValueError):
    """The user's price file was refused. The message is a short code, never a value."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SttCost:
    records: tuple[CostRecord, ...]
    summary: CostSummary


def _parse_catalog(text: str, created_at: datetime) -> PricingCatalog | str:
    """The sealed catalog, or the short code of the refusal (nothing is raised in here, so a
    refusal keeps no validation error as its context)."""
    try:
        document = loads_strict(text)
    except Exception:
        return "catalog_not_json"
    if (
        not isinstance(document, dict)
        or set(document) != _USER_FILE_MEMBERS
        or not isinstance(document["entries"], list)
        or not document["entries"]
    ):
        return "catalog_shape"
    try:
        entries = [PriceEntry.model_validate(entry) for entry in document["entries"]]
    except Exception:
        return "catalog_entry_invalid"
    if any(entry.price_source != "user_supplied" for entry in entries):
        return "catalog_entry_not_user_supplied"
    try:
        # The schema's own reading of an instant, through a model that holds one.
        published = PriceProvenance.model_validate(
            {"kind": "user_note", "retrieved_at": document["published_at"]}
        ).retrieved_at
        fields: dict[str, Any] = {
            "id": PricingCatalogId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "version": 1,
            "entries": entries,
            "published_at": published,
        }
        draft = PricingCatalog.model_construct(**fields, content_digest=None)
        sealed = draft.model_dump(mode="json", by_alias=True)
        sealed.pop("content_digest")
        sealed["metadata"] = {}
        sealed["metadata_flags"] = []
        digest = compute_digest(sealed).model_dump(mode="json", exclude_none=True)
        return PricingCatalog.model_validate({**sealed, "content_digest": digest})
    except Exception:
        return "catalog_invalid"


def load_user_catalog(text: str, *, created_at: datetime) -> PricingCatalog:
    """The user's USD price file as a sealed catalog.

    The file is ``{"published_at": <instant>, "entries": [<price entries>]}`` and nothing else:
    a person cannot compute a content digest, so Tracer assigns the id and version and seals it.
    """
    result = _parse_catalog(text, created_at)
    if isinstance(result, str):
        raise CatalogRefusedError(result)
    return result


def _not_available(created_at: datetime, reason: str, usage_ids: list[Any]) -> CostRecord:
    return CostRecord.model_validate(
        {
            "id": CostRecordId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "domain": _DOMAIN,
            "usage_record_ids": usage_ids,
            "status": "not_available",
            "na_reason": reason,
        }
    )


def compute_stt_cost(
    usages: Sequence[UsageRecord],
    *,
    catalog: PricingCatalog | None,
    created_at: datetime,
    run_started_at: datetime | None = None,
) -> SttCost:
    """One cost record for each usage record, and their summary."""
    context = Context(prec=_PRECISION)
    records: list[CostRecord] = []
    if not usages:
        records.append(_not_available(created_at, "no_usage", []))
    for usage in usages:
        if catalog is None:
            records.append(_not_available(created_at, "no_price_for_model", [usage.id]))
        else:
            records.append(
                compute_cost(
                    [usage],
                    catalog,
                    domain=_DOMAIN,
                    record_id=CostRecordId.new(),
                    created_at=created_at,
                    context=context,
                    run_started_at=run_started_at,
                )
            )
    summary = summarize_costs(
        records, domain=_DOMAIN, summary_id=CostSummaryId.new(), created_at=created_at
    )
    return SttCost(records=tuple(records), summary=summary)


compute_usage_cost = compute_stt_cost
