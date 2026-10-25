# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Sarvam responses to Transcript, Turn, Segment and UsageRecord (ADR 0024 rules 12 and 13).

Pure: no clock, no network, no I/O. Every record is stamped with ``context.created_at``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Any, Final

from tracer_evalcore.audio.chunker import Chunk, ChunkPlan
from tracer_evalcore.stt import SttContext, SttOutcome
from tracer_evalproviders.sarvam.models import ChunkResponse, SarvamPhrase
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import TranscriptId, TurnId, UsageRecordId
from tracer_evalschema.language import normalize_language_label
from tracer_evalschema.primitives import canonical_decimal_text
from tracer_evalschema.transcript import Transcript, Turn
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "MODEL_BASIS",
    "ROUNDING_KEY",
    "map_responses",
    "map_usage_records",
    "usage_audio_seconds",
]

PROVIDER: Final = "sarvam"
MODEL_BASIS: Final = "requested_not_echoed"
ROUNDING_KEY: Final = "tracer.audio_seconds_rounding"
_SCALE: Final = 10**18


def usage_audio_seconds(frames: int, rate: int) -> tuple[str, str]:
    """``(canonical decimal text, "exact" | "half_even_18")`` of ``frames / rate`` seconds.

    Rounded half to even at 18 fractional digits, the schema's limit; never through a float.
    """
    exact = Fraction(frames, rate)
    scaled = round(exact * _SCALE)  # Fraction rounds half to even
    text = canonical_decimal_text(Decimal(scaled).scaleb(-18))
    return text, ("exact" if Fraction(scaled, _SCALE) == exact else "half_even_18")


def _ordered(plan: ChunkPlan, responses: Sequence[ChunkResponse]) -> list[ChunkResponse]:
    by_index: dict[int, ChunkResponse] = {}
    for item in responses:
        if item.chunk_index in by_index or not 0 <= item.chunk_index < len(plan.chunks):
            raise ValueError("a response names a chunk twice or a chunk the plan does not have")
        by_index[item.chunk_index] = item
    return [by_index[index] for index in sorted(by_index)]


def map_usage_records(
    plan: ChunkPlan, responses: Sequence[ChunkResponse], *, model: str, context: SttContext
) -> tuple[UsageRecord, ...]:
    """One measured usage record for each answered chunk (a subset of the plan is accepted)."""
    records: list[UsageRecord] = []
    for item in _ordered(plan, responses):
        chunk = plan.chunks[item.chunk_index]
        seconds, rounding = usage_audio_seconds(chunk.frames, chunk.sample_rate_hz)
        document: dict[str, Any] = {
            "id": UsageRecordId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": context.created_at,
            "metadata": {
                ROUNDING_KEY: rounding,
                "tracer.model_basis": MODEL_BASIS,
                "tracer.chunk_index": item.chunk_index,
            },
            "run_id": context.run_id,
            "domain": "evaluation",
            "stage": "stt",
            "provider": PROVIDER,
            "model": model,
            "provider_call_ref": {
                "system": PROVIDER,
                "kind": "request_id",
                "value": item.response.request_id,
            },
            "quantities": [
                {"unit": "audio_input_seconds", "count": seconds},
                {"unit": "requests", "count": 1},
            ],
            "source": "measured",
            "raw": None,
        }
        if item.responded_at is not None:
            document["occurred_at"] = item.responded_at
        records.append(UsageRecord.model_validate(document))
    return tuple(records)


def _floor_ms(chunk: Chunk, seconds: Decimal) -> int:
    """Milliseconds from sample 0 of the audio, floored, in exact rational arithmetic."""
    start = Fraction(chunk.start_frame, chunk.sample_rate_hz)
    return math.floor((start + Fraction(seconds)) * 1000)


def _language_warning(index: int, label: str) -> tuple[str | None, IngestWarning | None]:
    """The language tag of a provider label and the warning its reading calls for."""
    trimmed = label.strip()
    tag, notes = normalize_language_label(trimmed)
    if notes:
        code: str | None = notes[0].code
    else:
        code = "language_label_normalized" if trimmed != label else None
    if code is None:
        return tag, None
    message = (
        "the provider label is not a language tag; it is kept as received"
        if tag is None
        else "the provider label was read as a language tag"
    )
    warning = IngestWarning(
        code=code, path=f"/chunks/{index}/language_code", raw=label, message=message
    )
    return tag, warning


def _segments(chunk: Chunk, phrases: Sequence[SarvamPhrase]) -> list[dict[str, Any]] | None:
    """The phrases as segments, or ``None`` when one ends after the chunk (all or none)."""
    duration = Fraction(chunk.frames, chunk.sample_rate_hz)
    if any(Fraction(phrase.end_seconds) > duration for phrase in phrases):
        return None
    return [
        {
            "granularity": "phrase",
            "text": phrase.text,
            "start_ms": _floor_ms(chunk, phrase.start_seconds),
            "end_ms": _floor_ms(chunk, phrase.end_seconds),
        }
        for phrase in phrases
    ]


def map_responses(
    plan: ChunkPlan, responses: Sequence[ChunkResponse], *, model: str, context: SttContext
) -> SttOutcome:
    """The transcript, turns, usage and warnings of a plan answered in full."""
    ordered = _ordered(plan, responses)
    if [item.chunk_index for item in ordered] != list(range(len(plan.chunks))):
        raise ValueError("a transcript needs one response for every chunk of the plan")
    transcript_id = TranscriptId.new()
    turns: list[Turn] = []
    warnings: list[IngestWarning] = []
    empty: list[int] = []
    tags: list[str | None] = []
    labels: list[str | None] = []
    for item in ordered:
        chunk = plan.chunks[item.chunk_index]
        response = item.response
        if response.transcript.strip() == "":
            empty.append(item.chunk_index)
            continue
        label = response.language_code
        tag: str | None = None
        if label is not None:
            tag, warning = _language_warning(item.chunk_index, label)
            if warning is not None:
                warnings.append(warning)
        tags.append(tag)
        labels.append(label)
        segments: list[dict[str, Any]] | None = []
        if response.phrases:
            segments = _segments(chunk, response.phrases)
            if segments is None:
                warnings.append(
                    IngestWarning(
                        code="phrase_time_outside_chunk",
                        path=f"/chunks/{item.chunk_index}/timestamps",
                        message="a phrase ends after its chunk; no segment is made for the chunk",
                    )
                )
        metadata: dict[str, Any] = {"tracer.chunk_index": item.chunk_index}
        if response.language_probability is not None:
            metadata["tracer.language_probability"] = canonical_decimal_text(
                response.language_probability
            )
        turn: dict[str, Any] = {
            "id": TurnId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": context.created_at,
            "metadata": metadata,
            "transcript_id": transcript_id,
            "seq": len(turns),
            "role": "unknown",
            "source": "our_stt",
            "text": response.transcript,
            "timing_source": "none",
        }
        if label is not None:
            turn["language_raw"] = label
        if tag is not None:
            turn["language"] = tag
        if segments:
            turn["segments"] = segments
            turn["timing_source"] = "audio_alignment"
            turn["start_ms"] = min(s["start_ms"] for s in segments)
            turn["end_ms"] = max(s["end_ms"] for s in segments)
        turns.append(Turn.model_validate(turn))
    head: dict[str, Any] = {
        "id": transcript_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": context.created_at,
        "metadata": {
            "tracer.chunk_plan": plan.as_metadata(),
            "tracer.empty_chunks": {"count": len(empty), "indexes": empty},
            "tracer.model_basis": MODEL_BASIS,
        },
        "source": "our_stt",
        "provider": PROVIDER,
        "model": model,
        "privacy": context.privacy,
    }
    detected = tags[0] if tags and None not in tags and len(set(tags)) == 1 else None
    if detected is not None:
        head["detected_language"] = detected
        if len(set(labels)) == 1 and labels[0] is not None:
            head["detected_language_raw"] = labels[0]
    if any(turn.segments for turn in turns):
        head["timeline_origin"] = {"kind": "artifact", "ref": context.audio_artifact_id}
    return SttOutcome(
        transcript=Transcript.model_validate(head),
        turns=tuple(turns),
        usage=map_usage_records(plan, ordered, model=model, context=context),
        warnings=tuple(warnings),
        failure=None,
    )
