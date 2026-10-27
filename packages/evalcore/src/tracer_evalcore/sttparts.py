# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Speech-to-text of a simulated call in parts: the seconds cap and the assembly of one transcript.

``CappedStt`` wraps an engine and refuses, before it sends, a plan whose audio would take the run
above ``--max-stt-seconds`` (ADR 0026 rule 15: the seconds are known exactly before the send).
``assemble_stt_outcome`` joins single-chunk outcomes (the in-call chunks of an LLM caller and the
chunks transcribed after the call) into the one ``our_stt`` transcript of the run (ADR 0028 rule 9:
the chunk is never sent twice). Pure: no clock, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction
from typing import Any, Final

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.stt import (
    SttCapReachedError,
    SttContext,
    SttEngine,
    SttOutcome,
    UsageSink,
)
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import TranscriptId
from tracer_evalschema.transcript import Turn
from tracer_evalschema.usage import UsageRecord

__all__ = ["CappedStt", "assemble_stt_outcome", "single_chunk_plan"]

_MODEL_BASIS: Final = "requested_not_echoed"


class CappedStt:
    """An engine that sends nothing once the seconds cap would be crossed.

    ``used_seconds`` counts the audio of every plan that was sent (a plan that failed halfway was
    sent, and its billing is unverified, so it counts). ``None`` means no cap (fake engines).
    """

    def __init__(self, inner: SttEngine, *, cap_seconds: int | None) -> None:
        if cap_seconds is not None and (type(cap_seconds) is not int or cap_seconds < 0):
            raise ValueError("cap_seconds: not a non-negative integer or None")
        self._inner = inner
        self._cap = cap_seconds
        self._used = Fraction(0)
        self.streams_usage: bool = getattr(inner, "streams_usage", False) is True

    @property
    def used_seconds(self) -> Fraction:
        return self._used

    def __getattr__(self, name: str) -> Any:
        if name in ("preflight", "close", "provider", "model"):
            return getattr(self._inner, name)
        raise AttributeError(name)

    def transcribe(
        self, plan: ChunkPlan, context: SttContext, *, on_usage: UsageSink | None = None
    ) -> SttOutcome:
        seconds = sum((Fraction(c.frames, c.sample_rate_hz) for c in plan.chunks), Fraction(0))
        if self._cap is not None and self._used + seconds > self._cap:
            return SttOutcome(
                None, (), (), (), SttCapReachedError(chunk_index=0, reason="stt_seconds")
            )
        self._used += seconds
        if on_usage is not None and self.streams_usage:
            return self._inner.transcribe(plan, context, on_usage=on_usage)  # type: ignore[call-arg]
        outcome = self._inner.transcribe(plan, context)
        if on_usage is not None:
            for record in outcome.usage:
                on_usage(record)
        return outcome


def single_chunk_plan(plan: ChunkPlan, index: int) -> ChunkPlan:
    """A plan of the one chunk ``index`` of ``plan``, numbered 0, on the same recording timeline."""
    chunk = plan.chunks[index]
    return replace(plan, chunks=(replace(chunk, index=0),))


def assemble_stt_outcome(
    parts: Sequence[SttOutcome], *, plan: ChunkPlan, context: SttContext
) -> SttOutcome:
    """One transcript from ``parts``: part ``k`` is the successful outcome of chunk ``k`` of
    ``plan`` (a single-chunk outcome). Turns are renumbered, usage keeps its record ids."""
    if len(parts) != len(plan.chunks) or not parts:
        raise ValueError("a transcript needs one part for every chunk of the plan")
    if any(part.transcript is None for part in parts):
        raise ValueError("a failed part cannot be assembled")
    first = parts[0].transcript
    assert first is not None  # noqa: S101
    transcript_id = TranscriptId.new()
    turns: list[Turn] = []
    usage: list[UsageRecord] = []
    warnings: list[IngestWarning] = []
    empty: list[int] = []
    tags: list[str | None] = []
    labels: list[str | None] = []
    origin = None
    for position, part in enumerate(parts):
        assert part.transcript is not None  # noqa: S101
        if part.transcript.timeline_origin is not None:
            origin = part.transcript.timeline_origin
        if not part.turns:
            empty.append(position)
        for turn in part.turns:
            tags.append(turn.language)
            labels.append(turn.language_raw)
            turns.append(
                turn.model_copy(
                    update={
                        "transcript_id": transcript_id,
                        "seq": len(turns),
                        "metadata": {**turn.metadata, "tracer.chunk_index": position},
                    }
                )
            )
        for record in part.usage:
            usage.append(
                record.model_copy(
                    update={"metadata": {**record.metadata, "tracer.chunk_index": position}}
                )
            )
        warnings.extend(part.warnings)
    update: dict[str, Any] = {
        "id": transcript_id,
        "metadata": {
            "tracer.chunk_plan": plan.as_metadata(),
            "tracer.empty_chunks": {"count": len(empty), "indexes": empty},
            "tracer.model_basis": _MODEL_BASIS,
        },
        "privacy": context.privacy,
        "detected_language": None,
        "detected_language_raw": None,
        "timeline_origin": origin,
    }
    if tags and None not in tags and len(set(tags)) == 1:
        update["detected_language"] = tags[0]
        if len(set(labels)) == 1 and labels[0] is not None:
            update["detected_language_raw"] = labels[0]
    transcript = first.model_copy(update=update)
    return SttOutcome(
        transcript=transcript,
        turns=tuple(turns),
        usage=tuple(usage),
        warnings=tuple(warnings),
        failure=None,
    )
