# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The plan printed before anything is sent (ADR 0024 rule 16).

Reads no key, opens no log, opens no connection and writes no file: it holds a chunk plan, a
model name and, optionally, a USD catalog the user supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalproviders.cost import compute_stt_cost
from tracer_evalproviders.sarvam.mapping import usage_audio_seconds
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import RunId, UsageRecordId
from tracer_evalschema.pricing import PricingCatalog
from tracer_evalschema.primitives import canonical_decimal_text
from tracer_evalschema.usage import UsageRecord

__all__ = ["DryRunCost", "DryRunPlan", "build_dry_run_plan"]


@dataclass(frozen=True)
class DryRunCost:
    status: str  # "estimated" or "not_available"
    amount: str | None
    na_reason: str | None


@dataclass(frozen=True)
class DryRunPlan:
    chunks: int
    calls: int
    total_frames: int
    sample_rate_hz: int
    audio_seconds: Decimal
    audio_seconds_rounding: str  # "exact" or "half_even_18"
    max_chunk_ms: int
    search_ms: int
    cost: DryRunCost | None

    def render(self) -> str:
        seconds = canonical_decimal_text(self.audio_seconds)
        if self.audio_seconds_rounding != "exact":
            seconds += " (rounded half-even to 18 digits)"
        if self.cost is None:
            cost = "cost: not available (no USD price supplied)"
        elif self.cost.status == "estimated":
            cost = f"cost: {self.cost.amount} USD (computed from the supplied USD price)"
        else:
            cost = f"cost: not available ({self.cost.na_reason})"
        return "\n".join(
            [
                f"chunks: {self.chunks}",
                f"audio seconds: {seconds}",
                f"calls: {self.calls}",
                cost,
            ]
        )


def _estimate(
    plan: ChunkPlan, model: str, catalog: PricingCatalog, priced_at: datetime | None
) -> DryRunCost:
    """The cost of one hypothetical usage per chunk, priced at ``priced_at``."""
    usages = [
        UsageRecord.model_validate(
            {
                "id": UsageRecordId.new(),
                "schema_version": SCHEMA_VERSION,
                "created_at": catalog.created_at,
                "run_id": RunId.new(),
                "domain": "evaluation",
                "stage": "stt",
                "provider": "sarvam",
                "model": model,
                "quantities": [
                    {
                        "unit": "audio_input_seconds",
                        "count": usage_audio_seconds(chunk.frames, chunk.sample_rate_hz)[0],
                    },
                    {"unit": "requests", "count": 1},
                ],
                "source": "measured",
            }
        )
        for chunk in plan.chunks
    ]
    result = compute_stt_cost(
        usages, catalog=catalog, created_at=catalog.created_at, run_started_at=priced_at
    )
    total = result.summary.total
    if result.summary.completeness == "complete" and total is not None:
        return DryRunCost("estimated", total.amount, None)
    reasons = [record.na_reason for record in result.records if record.na_reason is not None]
    return DryRunCost("not_available", None, reasons[0] if reasons else "no_usage")


def build_dry_run_plan(
    plan: ChunkPlan,
    *,
    model: str,
    catalog: PricingCatalog | None = None,
    priced_at: datetime | None = None,
) -> DryRunPlan:
    """The plan of a run: counts, audio seconds and, with a user catalog, a cost estimate."""
    text, rounding = usage_audio_seconds(plan.total_frames, plan.sample_rate_hz)
    return DryRunPlan(
        chunks=len(plan.chunks),
        calls=len(plan.chunks),
        total_frames=plan.total_frames,
        sample_rate_hz=plan.sample_rate_hz,
        audio_seconds=Decimal(text),
        audio_seconds_rounding=rounding,
        max_chunk_ms=plan.max_chunk_ms,
        search_ms=plan.search_ms,
        cost=None if catalog is None else _estimate(plan, model, catalog, priced_at),
    )
