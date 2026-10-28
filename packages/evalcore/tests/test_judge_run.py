# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J17, P4-J18, P4-J23 (support): judging a stored call, the pure body of step 13b of ``tracer
simulate`` and of ``tracer judge`` (ADR 0030 rules 4, 16, 17, 20, 21 and 23; contract
docs/phase4/api-surface-batch-j2.md section 4).

Items are judged in golden order, one request in flight. An item whose window is not ``ok`` is an
error with no request, decided before the state of the judge. After an authentication failure or a
cap no further request is sent and each remaining item is an error with that code. An item whose
definition does not apply to the call needs no judgement. The engine is a scripted fake.
"""

from __future__ import annotations

from typing import Any

import pytest

from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CREATED_AT,
    FakeJudge,
    agrees_json,
    build_judged,
    default_params,
    mod,
)

pytestmark = pytest.mark.filterwarnings("ignore")


def run_all(call: Any, engine: Any, **changes: Any) -> Any:
    judge, runner = mod("judge"), mod("judgerun")
    usage_log: list[Any] = []

    def on_usage(record: Any) -> None:
        usage_log.append(record)
        engine.events.append(("usage", len(usage_log) - 1))

    fields: dict[str, Any] = {
        "bundle": call.bundle,
        "setup": call.setup,
        "engine": engine,
        "params": default_params(),
        "context": judge.JudgeContext(run_id=call.run_id, created_at=CREATED_AT),
        "call_end_ms": call.call_end_ms,
        "on_usage": on_usage,
        "created_at": CREATED_AT,
    }
    fields.update(changes)
    return runner.judge_bundle(**fields)


def replies(call: Any, count: int | None = None) -> list[str]:
    return [agrees_json(i) for i in call.golden.items][:count]


def test_judge_bundle_judges_every_item_in_golden_order_one_request_at_a_time() -> None:
    call = build_judged()
    engine = FakeJudge(replies(call), run_id=call.run_id, streams_usage=True)
    done = run_all(call, engine)
    ids = [i.id for i in call.golden.items]
    assert list(done.judgements) == ids
    assert [r.item_id for r in engine.requests] == ids
    assert [r.index for r in engine.requests] == list(range(10))
    assert [r.attempt for r in engine.requests] == [1] * 10
    assert done.requests == 10
    assert len(done.usage) == 10
    assert len(done.results) == 10
    assert [(r.status, r.value) for r in done.results] == [("scored", "agrees")] * 10
    assert all(r.passed is None and r.judge is not None for r in done.results)
    # one request in flight: the usage of each answered request is handed out before the next
    assert engine.events == [event for n in range(10) for event in (("request", n), ("usage", n))]


def test_judge_bundle_regenerates_once_and_counts_the_requests() -> None:
    call = build_judged()
    item0 = call.golden.items[0]
    script = ["not json", agrees_json(item0), *replies(call)[1:]]
    engine = FakeJudge(script, run_id=call.run_id)
    done = run_all(call, engine)
    first = done.judgements[item0.id]
    assert (first.attempts, first.regenerated, first.state) == (2, True, "verdict")
    assert done.requests == 11
    assert [r.index for r in engine.requests] == list(range(11))
    assert [r.attempt for r in engine.requests[:2]] == [1, 2]
    assert engine.requests[0].messages == engine.requests[1].messages


def test_judge_bundle_sends_nothing_when_the_call_gives_nothing_to_judge() -> None:
    for kwargs in ({"established": False}, {"stt_failed": True}):
        call = build_judged(**kwargs)
        engine = FakeJudge([], run_id=call.run_id)
        done = run_all(call, engine)
        assert engine.requests == []
        assert done.requests == 0
        assert done.judgements == {}
        assert [(r.status, r.na_reason) for r in done.results] == [
            ("not_applicable", "missing_input")
        ] * 10
        assert all(r.value is None and r.passed is None and r.error is None for r in done.results)
    other = build_judged(stt_language="en-IN")
    engine = FakeJudge([], run_id=other.run_id)
    done = run_all(other, engine)
    assert engine.requests == []
    assert [(r.status, r.na_reason) for r in done.results] == [
        ("not_applicable", "unsupported_language")
    ] * 10


def test_judge_bundle_decides_a_window_that_is_not_ok_before_it_asks_the_judge() -> None:
    """Three answered items, then no agent speech: the other items are ``answer_window_empty``
    (and the last one, whose line was cut short, ``question_not_sent``), with no request."""
    call = build_judged(answers=["x"] * 3)
    engine = FakeJudge(replies(call, 3), run_id=call.run_id)
    done = run_all(call, engine)
    assert len(engine.requests) == 3
    states = [(j.state, j.error_code, j.attempts) for j in done.judgements.values()]
    assert states[:3] == [("verdict", None, 1)] * 3
    assert states[3:] == [("error", "answer_window_empty", 0)] * 7
    cut = build_judged(complete_last_line=False)
    engine = FakeJudge(replies(cut, 9), run_id=cut.run_id)
    done = run_all(cut, engine)
    assert len(engine.requests) == 9
    last = done.judgements[cut.golden.items[9].id]
    assert (last.state, last.error_code, last.attempts) == ("error", "question_not_sent", 0)
    by_key = {r.metric.id: r for r in done.results}
    assert len(by_key) == 10
    assert all(r.error is not None for r in done.results[9:])


def test_judge_bundle_stops_after_an_authentication_failure_or_a_cap() -> None:
    judge = mod("judge")
    call = build_judged(answers=["x"] * 5)
    items = call.golden.items
    auth = judge.JudgeAuthError(index=1, status=401, attempts=1)
    engine = FakeJudge([agrees_json(items[0]), auth], run_id=call.run_id)
    done = run_all(call, engine)
    assert len(engine.requests) == 2, "no request after the failure"
    got = [(j.state, j.error_code, j.attempts) for j in done.judgements.values()]
    assert got[0] == ("verdict", None, 1)
    assert got[1] == ("error", "judge_auth_failed", 1)
    assert got[2:5] == [("error", "judge_auth_failed", 0)] * 3, "the items after it are not asked"
    assert got[5:] == [("error", "answer_window_empty", 0)] * 5, "a window error is not a stop"
    assert done.judgements[items[1].id].stops_run is True
    assert done.judgements[items[0].id].stops_run is False
    cap = judge.JudgeCapReachedError(index=2, reason="judge_tokens")
    capped = FakeJudge([agrees_json(items[0]), agrees_json(items[1]), cap], run_id=call.run_id)
    again = run_all(call, capped)
    codes = [(j.error_code) for j in again.judgements.values()][:5]
    assert codes == [
        None,
        None,
        "judge_tokens_cap_reached",
        "judge_tokens_cap_reached",
        "judge_tokens_cap_reached",
    ]
    assert len(capped.requests) == 3
    other = FakeJudge(
        [judge.JudgeCapReachedError(index=0, reason="judge_requests")], run_id=call.run_id
    )
    first = run_all(call, other)
    assert first.judgements[items[0].id].error_code == "judge_requests_cap_reached"
    # any other failure of a request is that item's error and stops nothing
    failed = FakeJudge(
        [judge.JudgeServerError(index=0, status=503, attempts=3), *replies(call)[1:5]],
        run_id=call.run_id,
    )
    mild = run_all(call, failed)
    codes = [(j.error_code) for j in mild.judgements.values()][:5]
    assert codes == ["judge_request_failed", None, None, None, None]
    assert len(failed.requests) == 5


def test_a_skipped_judgement_is_an_error_with_no_request_and_no_trace_of_one() -> None:
    runner = mod("judgerun")
    call = build_judged()
    window = mod("judgewindow").answer_window(
        scripted_turn_index=0,
        transcripts=call.bundle.transcripts,
        turns=call.bundle.turns,
        call_end_ms=call.call_end_ms,
    )
    skipped = runner.skipped_judgement("mr_q00", window, "judge_auth_failed")
    assert (skipped.state, skipped.error_code, skipped.attempts, skipped.regenerated) == (
        "error", "judge_auth_failed", 0, False,
    )  # fmt: skip
    assert skipped.verdict is None
    assert skipped.facts == ()
    assert skipped.usage == ()
    assert skipped.prompt_sha256 is None
    assert skipped.exchange_sha256 is None
    assert skipped.window is window
    document = mod("judgerecords").judgement_document(skipped)
    assert (document["attempts"], document["verdict"], document["exchange_sha256"]) == (
        0,
        None,
        None,
    )
    with pytest.raises(ValueError, match=r"."):
        runner.skipped_judgement("mr_q00", window, "judge_undetermined")


def test_the_golden_set_of_a_stored_run_is_checked_against_the_run() -> None:
    runner, golden = mod("judgerun"), mod("golden")
    call = build_judged()
    runner.check_golden_against_bundle(call.golden, call.bundle)
    items = list(call.golden.items)
    reordered = golden.GoldenSet(call.golden.key, "mr", tuple(reversed(items)), call.golden.sha256)
    with pytest.raises(golden.GoldenSetError) as caught:
        runner.check_golden_against_bundle(reordered, call.bundle)
    assert caught.value.code == "golden_set_differs"
    fewer = golden.GoldenSet(call.golden.key, "mr", tuple(items[:9]), call.golden.sha256)
    with pytest.raises(golden.GoldenSetError) as caught:
        runner.check_golden_against_bundle(fewer, call.bundle)
    assert caught.value.code == "golden_set_differs"
    changed = items[:]
    changed[3] = golden.GoldenItem(
        items[3].id, items[3].scripted_turn_index, items[0].question, items[3].match_mode,
        items[3].expected_facts, items[3].expected_answer, True,
    )  # fmt: skip
    with pytest.raises(golden.GoldenSetError) as caught:
        runner.check_golden_against_bundle(
            golden.GoldenSet(call.golden.key, "mr", tuple(changed), call.golden.sha256), call.bundle
        )
    assert caught.value.code == "golden_question_mismatch"
    # a line that was never sent is not a mismatch: its item is judged `question_not_sent`
    runner.check_golden_against_bundle(call.golden, build_judged(lines=4).bundle)


def test_answer_window_closes_at_the_earliest_start_of_any_later_caller_line() -> None:
    """Part 1 finding (decision log, J1 dev report): the closing edge is the minimum ``start_ms``
    of the caller lines after the question, which is the next line when the lines are in time
    order and the earliest of them when they are not."""
    window = mod("judgewindow")
    call = build_judged()
    turns = list(call.bundle.turns)
    script = {t.seq: t for t in turns if t.role == "caller"}
    assert script[2].start_ms == 4000
    assert script[3].start_ms == 6000
    early = script[3].model_copy(update={"start_ms": 3500})
    turns = [early if t.id == script[3].id else t for t in turns]
    shifted = window.answer_window(
        scripted_turn_index=1,
        transcripts=call.bundle.transcripts,
        turns=turns,
        call_end_ms=call.call_end_ms,
    )
    assert shifted.close_ms == 3500
    normal = window.answer_window(
        scripted_turn_index=1,
        transcripts=call.bundle.transcripts,
        turns=call.bundle.turns,
        call_end_ms=call.call_end_ms,
    )
    assert normal.close_ms == 4000
