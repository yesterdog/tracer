# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Judging a stored call: step 13b of ``tracer simulate`` and the body of ``tracer judge`` (ADR
0030 rules 20 and 21).

Items are judged in golden order, one request in flight. An item whose window is not ``ok`` is an
error with no request (the fact about the call is decided before the state of the judge). After an
authentication failure or a cap, no further request is sent and every remaining item is an error
with that code. Pure orchestration over the engine boundary: no file, no clock, no environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tracer_evalcore.golden import GoldenSet, GoldenSetError
from tracer_evalcore.judge import (
    JudgeContext,
    JudgeEngine,
    JudgeItemResult,
    JudgeParams,
    UsageSink,
    UsageStreamingJudgeEngine,
    judge_item,
)
from tracer_evalcore.judgerecords import (
    JUDGE_KEY_PREFIX,
    JudgeSetup,
    judge_applicable_items,
    judge_results,
)
from tracer_evalcore.judgewindow import AnswerWindow, answer_window
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.results import MetricResult
from tracer_evalschema.usage import UsageRecord

__all__ = ["JudgeRun", "check_golden_against_bundle", "judge_bundle", "skipped_judgement"]


@dataclass(frozen=True)
class JudgeRun:
    """What step 13b decided: a judgement per judged item, one result per judge definition, the
    usage of every answered request, and the number of requests that were made."""

    judgements: dict[str, JudgeItemResult]
    results: tuple[MetricResult, ...]
    usage: tuple[UsageRecord, ...]
    requests: int


def skipped_judgement(item_id: str, window: AnswerWindow, error_code: str) -> JudgeItemResult:
    """An error judgement for an item that was not asked (a stop of the run)."""
    return JudgeItemResult(
        item_id=item_id,
        state="error",
        verdict=None,
        reason_code=None,
        facts=(),
        error_code=error_code,
        window=window,
        attempts=0,
        regenerated=False,
        prompt_sha256=None,
        exchange_sha256=None,
        reported_model=None,
        params={},
        usage=(),
        failure=None,
    )


def check_golden_against_bundle(golden: GoldenSet, bundle: RunBundle) -> None:
    """``tracer judge``: the golden set is the one of the run (same item keys in the same order),
    and each question equals the stored caller line it names."""
    keys = [
        d.key.removeprefix(JUDGE_KEY_PREFIX)
        for d in bundle.snapshot.metric_definitions
        if d.key.startswith(JUDGE_KEY_PREFIX)
    ]
    if keys != [item.id for item in golden.items]:
        raise GoldenSetError("golden_set_differs")
    script = {t.id for t in bundle.transcripts if t.source == "script"}
    lines = {t.seq: t.text for t in bundle.turns if t.transcript_id in script}
    for item in golden.items:
        if item.scripted_turn_index in lines and lines[item.scripted_turn_index] != item.question:
            raise GoldenSetError("golden_question_mismatch")


def judge_bundle(
    *,
    bundle: RunBundle,
    setup: JudgeSetup,
    engine: JudgeEngine | UsageStreamingJudgeEngine,
    params: JudgeParams,
    context: JudgeContext,
    call_end_ms: int,
    on_usage: UsageSink,
    created_at: datetime,
) -> JudgeRun:
    applicable = set(
        judge_applicable_items(
            snapshot=bundle.snapshot,
            run=bundle.run,
            transcripts=bundle.transcripts,
            turns=bundle.turns,
        )
    )
    judgements: dict[str, JudgeItemResult] = {}
    stop: str | None = None
    index = 0
    requests = 0
    used: list[UsageRecord] = []
    for item in setup.golden.items:
        if item.id not in applicable:
            continue
        window = answer_window(
            scripted_turn_index=item.scripted_turn_index,
            transcripts=bundle.transcripts,
            turns=bundle.turns,
            call_end_ms=call_end_ms,
        )
        if window.status == "ok" and stop is not None:
            judgements[item.id] = skipped_judgement(item.id, window, stop)
            continue
        judged = judge_item(
            engine=engine,
            item=item,
            window=window,
            params=params,
            context=context,
            first_index=index,
            on_usage=on_usage,
        )
        index += judged.attempts
        requests += judged.attempts
        used.extend(judged.usage)
        judgements[item.id] = judged
        if judged.stops_run and judged.error_code is not None:
            stop = judged.error_code
    results = judge_results(
        snapshot=bundle.snapshot,
        run=bundle.run,
        transcripts=bundle.transcripts,
        turns=bundle.turns,
        judgements=judgements,
        created_at=created_at,
    )
    return JudgeRun(judgements, results, tuple(used), requests)
