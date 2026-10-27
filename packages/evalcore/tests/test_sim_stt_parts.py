# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 / P4-23: speech-to-text of a simulated call in parts (ADR 0026 rule 15, ADR 0028 rule 9,
ADR 0024 amendment A5).

Contract: docs/phase4/api-surface-batch-e.md section 8. ``CappedStt`` enforces ``--max-stt-seconds``
before a request is sent (the seconds of a plan are known exactly); ``assemble_stt_outcome`` joins
the
single-chunk outcomes of an LLM caller (transcribed during the call) and of the chunks transcribed
after it into the one ``our_stt`` transcript of the run, so no chunk is ever sent twice.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

import pytest

from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    CREATED_AT,
    RATE,
    WAIT_MS,
    mod,
    privacy,
)
from tracer_evalcore.audio.chunker import Chunk, ChunkPlan
from tracer_evalcore.stt import (
    SttContext,
    SttOutcome,
    SttServerError,
    UsageStreamingSttEngine,
)
from tracer_evalschema.ids import ArtifactId, RunId


def parts() -> Any:
    return mod("sttparts")


def plan_of(frames: list[int], *, starts: list[int] | None = None, rate: int = RATE) -> ChunkPlan:
    begin = starts or [rate * (k + 1) * 10 for k in range(len(frames))]
    chunks = tuple(
        Chunk(k, begin[k], begin[k] + frames[k], rate, 1, b"") for k in range(len(frames))
    )
    return ChunkPlan(
        sample_rate_hz=rate,
        channels=2,
        max_chunk_ms=25_000,
        search_ms=5_000,
        total_frames=begin[-1] + frames[-1] + rate,
        chunks=chunks,
        channel=1,
        activity_wait_ms=WAIT_MS,
    )


def context() -> SttContext:
    return SttContext(
        run_id=RunId.new(),
        audio_artifact_id=ArtifactId.new(),
        created_at=CREATED_AT,
        privacy=privacy(),
        configured_language="mr",
        role="agent",
    )


class Inner:
    """A fake engine: records every plan it is given and answers a scripted outcome."""

    def __init__(self, *, streams: bool = False, usage: tuple[Any, ...] = ()) -> None:
        self.plans: list[ChunkPlan] = []
        self.streams_usage = streams
        self.closed = False
        self.preflights = 0
        self._usage = usage

    def transcribe(self, plan: ChunkPlan, ctx: SttContext, *, on_usage: Any = None) -> SttOutcome:
        self.plans.append(plan)
        if self.streams_usage and on_usage is not None:
            for record in self._usage:
                on_usage(record)
        return SttOutcome(None, (), self._usage, (), SttServerError(chunk_index=0, status=503))

    def preflight(self, plan: ChunkPlan) -> None:
        self.preflights += 1

    def close(self) -> None:
        self.closed = True


def test_capped_stt_sends_a_plan_only_while_its_seconds_fit_the_cap() -> None:
    """ADR 0026 rule 15: the seconds are exact before the send; a plan that would cross the cap is
    not sent and the failure is a typed cap error, not a provider error."""
    inner = Inner()
    capped = parts().CappedStt(inner, cap_seconds=2)
    ctx = context()
    first = capped.transcribe(plan_of([RATE, RATE // 2]), ctx)  # 1.5 s: within the cap
    assert len(inner.plans) == 1
    assert first.failure is not None
    assert not isinstance(first.failure, mod("stt").SttCapReachedError)
    assert capped.used_seconds == Fraction(3, 2)
    second = capped.transcribe(plan_of([RATE * 6 // 10]), ctx)  # 0.6 s: 2.1 s would cross
    assert len(inner.plans) == 1  # nothing was sent
    assert isinstance(second.failure, mod("stt").SttCapReachedError)
    assert second.failure.reason == "stt_seconds"
    assert second.transcript is None
    assert second.usage == ()
    assert capped.used_seconds == Fraction(3, 2)  # the refused plan used nothing
    third = capped.transcribe(plan_of([RATE // 2]), ctx)  # 0.5 s: exactly the cap, inclusive
    assert len(inner.plans) == 2
    assert not isinstance(third.failure, mod("stt").SttCapReachedError)
    assert capped.used_seconds == 2


def test_capped_stt_counts_seconds_exactly_and_zero_refuses_everything() -> None:
    inner = Inner()
    capped = parts().CappedStt(inner, cap_seconds=1)
    # 16000 Hz, 1/3 s three times is exactly 1 s (a float sum would drift)
    third = RATE // 3
    assert third * 3 != RATE
    ok = capped.transcribe(plan_of([RATE]), context())
    assert len(inner.plans) == 1
    assert ok.failure is not None
    zero = parts().CappedStt(Inner(), cap_seconds=0)
    assert isinstance(
        zero.transcribe(plan_of([1]), context()).failure, mod("stt").SttCapReachedError
    )


def test_capped_stt_sums_in_exact_fractions_not_floats() -> None:
    """Eleven chunks of one frame at 11 Hz are exactly one second; summed as floats the total is
    1.0000000000000002 and a cap of one second would wrongly refuse the plan."""
    inner = Inner()
    capped = parts().CappedStt(inner, cap_seconds=1)
    plan = plan_of([1] * 11, starts=[100 * (k + 1) for k in range(11)], rate=11)
    drifted = 0.0
    for chunk in plan.chunks:  # added one after the other, as a running total is
        drifted += chunk.frames / chunk.sample_rate_hz
    assert drifted > 1  # the float trap is real
    capped.transcribe(plan, context())
    assert len(inner.plans) == 1
    assert capped.used_seconds == 1


def test_capped_stt_without_a_cap_sends_everything() -> None:
    inner = Inner()
    capped = parts().CappedStt(inner, cap_seconds=None)
    for _ in range(3):
        capped.transcribe(plan_of([RATE * 100]), context())
    assert len(inner.plans) == 3
    assert capped.used_seconds == 300


@pytest.mark.parametrize("cap", [-1, True, 1.5, "3", -(10**30)])
def test_capped_stt_refuses_a_bad_cap(cap: Any) -> None:
    with pytest.raises(ValueError, match="cap_seconds"):
        parts().CappedStt(Inner(), cap_seconds=cap)


def test_capped_stt_hands_usage_to_the_sink_once_for_both_kinds_of_engine() -> None:
    """A streaming engine delivers each record itself; any other engine's records are delivered
    from the outcome; the sink never sees a record twice."""
    from evalcore_p4e_support import usage_record  # type: ignore[import-not-found,unused-ignore]

    run_id = RunId.new()
    records = (usage_record(run_id, "stt", requests=1),)
    for streams in (True, False):
        seen: list[Any] = []
        capped = parts().CappedStt(Inner(streams=streams, usage=records), cap_seconds=10)
        assert capped.streams_usage is streams
        capped.transcribe(plan_of([RATE]), context(), on_usage=seen.append)
        assert [r.id for r in seen] == [records[0].id], streams


def test_capped_stt_is_a_streaming_engine_only_when_the_inner_one_is() -> None:
    assert isinstance(
        parts().CappedStt(Inner(streams=True), cap_seconds=1), UsageStreamingSttEngine
    )
    assert parts().CappedStt(Inner(streams=False), cap_seconds=1).streams_usage is False


def test_capped_stt_passes_preflight_and_close_through() -> None:
    inner = Inner()
    capped = parts().CappedStt(inner, cap_seconds=1)
    capped.preflight(plan_of([RATE]))
    capped.close()
    assert inner.preflights == 1
    assert inner.closed
    with pytest.raises(AttributeError):
        capped.something_else  # noqa: B018


def test_single_chunk_plan_is_the_chunk_numbered_zero_on_the_same_timeline() -> None:
    plan = plan_of([RATE, RATE // 2, RATE // 4], starts=[1000, 50_000, 90_000])
    single = parts().single_chunk_plan(plan, 1)
    assert len(single.chunks) == 1
    chunk = single.chunks[0]
    assert (chunk.index, chunk.start_frame, chunk.end_frame) == (0, 50_000, 50_000 + RATE // 2)
    assert (single.sample_rate_hz, single.channels, single.channel, single.total_frames) == (
        plan.sample_rate_hz,
        plan.channels,
        plan.channel,
        plan.total_frames,
    )
    assert plan.chunks[1].index == 1  # the original is untouched


def single_outcome(
    plan: ChunkPlan, k: int, text: str, ctx: SttContext, label: str | None = "mr-IN"
) -> SttOutcome:
    from tracer_evalproviders.sarvam.mapping import map_responses
    from tracer_evalproviders.sarvam.models import ChunkResponse, parse_response

    body: dict[str, Any] = {"request_id": f"req-{k}", "transcript": text}
    if label is not None:
        body["language_code"] = label
    sub = parts().single_chunk_plan(plan, k)
    return map_responses(
        sub,
        [ChunkResponse(0, parse_response(json.dumps(body).encode()), None, 1)],
        model="saaras:v4",
        context=ctx,
    )


def test_assemble_joins_single_chunk_outcomes_into_one_transcript() -> None:
    plan = plan_of([RATE, RATE, RATE])
    ctx = context()
    pieces = [
        single_outcome(plan, 0, "\u0928\u092e\u0938\u094d\u0915\u093e\u0930", ctx),
        single_outcome(plan, 1, "   ", ctx),  # an empty chunk: no turn, counted
        single_outcome(plan, 2, "\u0927\u0928\u094d\u092f\u0935\u093e\u0926", ctx),
    ]
    joined = parts().assemble_stt_outcome(pieces, plan=plan, context=ctx)
    assert joined.failure is None
    assert joined.transcript is not None
    transcript = joined.transcript
    assert transcript.id not in {p.transcript.id for p in pieces if p.transcript}
    assert [t.seq for t in joined.turns] == [0, 1]
    assert [t.text for t in joined.turns] == [
        "\u0928\u092e\u0938\u094d\u0915\u093e\u0930",
        "\u0927\u0928\u094d\u092f\u0935\u093e\u0926",
    ]
    assert all(t.transcript_id == transcript.id and t.role == "agent" for t in joined.turns)
    assert [t.metadata["tracer.chunk_index"] for t in joined.turns] == [0, 2]
    assert transcript.metadata["tracer.chunk_plan"] == plan.as_metadata()
    assert transcript.metadata["tracer.empty_chunks"] == {"count": 1, "indexes": [1]}
    assert transcript.source == "our_stt"
    assert transcript.provider == "sarvam"
    assert transcript.model == "saaras:v4"
    assert transcript.detected_language == "mr-IN"
    assert transcript.privacy == ctx.privacy
    assert len(joined.usage) == 3
    assert [u.metadata["tracer.chunk_index"] for u in joined.usage] == [0, 1, 2]
    assert [u.id for u in joined.usage] == [u.id for p in pieces for u in p.usage]
    assert joined.warnings == ()


def test_assemble_leaves_the_language_open_when_the_chunks_disagree() -> None:
    plan = plan_of([RATE, RATE])
    ctx = context()
    pieces = [
        single_outcome(plan, 0, "\u0928", ctx, "mr-IN"),
        single_outcome(plan, 1, "ab", ctx, "en-IN"),
    ]
    joined = parts().assemble_stt_outcome(pieces, plan=plan, context=ctx)
    assert joined.transcript.detected_language is None
    assert [t.language for t in joined.turns] == ["mr-IN", "en-IN"]


def test_assemble_refuses_what_is_not_one_successful_part_per_chunk() -> None:
    plan = plan_of([RATE, RATE])
    ctx = context()
    good = single_outcome(plan, 0, "\u0928", ctx)
    failed = SttOutcome(None, (), (), (), SttServerError(chunk_index=0, status=503))
    with pytest.raises(ValueError, match="one part for every chunk"):
        parts().assemble_stt_outcome([good], plan=plan, context=ctx)
    with pytest.raises(ValueError, match="failed part"):
        parts().assemble_stt_outcome([good, failed], plan=plan, context=ctx)
    with pytest.raises(ValueError, match="one part for every chunk"):
        parts().assemble_stt_outcome([], plan=plan_of([RATE]), context=ctx)
