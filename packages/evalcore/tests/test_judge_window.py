# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J07: the answer window of a golden item (ADR 0030 rule 4; contract
docs/phase4/api-surface-batch-j1.md section 3).

A pure function of the records of a simulated call: the end edge of the caller turn that holds the
item's line opens the window, the start of the next sent caller turn (or the end of the call)
closes it, and it holds the ``our_stt`` agent-channel turns that start inside, in ``seq`` order,
joined by one line feed. The windows here are read from bundles that the product's own builder
makes from synthetic calls; the caller lines are sent at ``2000 * k`` ms and end 200 ms later.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from evalcore_p4e_support import export_read  # type: ignore[import-not-found,unused-ignore]
from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    build_judged,
    mod,
)

#: agent turn texts and start times (ms): item 0 owns ONE and TWO, item 1 owns THREE, item 2 owns
#: EDGE and FOUR; AT_CLOSE starts exactly where line 1 starts, before line 1 ended
TEXTS = ["ONE", "TWO", "AT_CLOSE", "THREE", "EDGE", "FOUR"]
STARTS = [500, 1500, 2000, 2500, 4200, 4300]


def window_of(call: Any, index: int, *, call_end_ms: int | None = None) -> Any:
    return mod("judgewindow").answer_window(
        scripted_turn_index=index,
        transcripts=call.bundle.transcripts,
        turns=call.bundle.turns,
        call_end_ms=call.call_end_ms if call_end_ms is None else call_end_ms,
    )


def test_answer_window_from_end_of_question_to_next_caller_line() -> None:
    """The window is [end edge of the question's turn, start of the next sent caller turn) and the
    text is the turns that start inside, in seq order, joined by one U+000A."""
    call = build_judged(lines=3, answers=TEXTS, answer_starts_ms=STARTS)
    window = window_of(call, 0)
    assert window.status == "ok"
    assert (window.open_ms, window.close_ms) == (200, 2000)
    assert window.text == "ONE\nTWO"
    assert window.turn_seqs == (0, 1)
    # the next window: AT_CLOSE (starts at 2000, before line 1 ended at 2200) is in no window
    window = window_of(call, 1)
    assert (window.open_ms, window.close_ms) == (2200, 4000)
    assert window.text == "THREE"
    assert window.turn_seqs == (3,)
    # a turn that starts exactly at the opening edge is inside; one at the closing edge is not
    last = window_of(call, 2)
    assert last.text == "EDGE\nFOUR"
    assert last.turn_seqs == (4, 5)
    assert (last.open_ms, last.close_ms) == (4200, call.call_end_ms)
    # the join character is a single line feed and nothing else
    assert "\r" not in window.text + last.text
    assert "\n\n" not in last.text
    # the window names its evidence: the turn ids in the same order, and the transcript
    stt = next(t for t in call.bundle.transcripts if t.source == "our_stt")
    assert last.transcript_id == stt.id
    by_seq = {t.seq: t.id for t in call.bundle.turns if t.transcript_id == stt.id}
    assert last.turn_ids == tuple(by_seq[s] for s in last.turn_seqs)
    # the same records give the same window, however the turns are ordered in the list
    again = mod("judgewindow").answer_window(
        scripted_turn_index=2,
        transcripts=list(reversed(call.bundle.transcripts)),
        turns=list(reversed(call.bundle.turns)),
        call_end_ms=call.call_end_ms,
    )
    assert again == last, "pure: a function of the records, in seq order, not list order"
    with pytest.raises(Exception, match=r"."):
        last.text = "other"


def test_answer_window_excludes_turns_starting_before_question_end() -> None:
    """A turn that starts before the opening edge is excluded and counted (ADR 0030 rule 4:
    ``excluded_turns``); the count is every ``our_stt`` agent turn that starts before the edge."""
    call = build_judged(lines=3, answers=TEXTS, answer_starts_ms=STARTS)
    assert window_of(call, 0).excluded_turns == 0
    # ONE, TWO and AT_CLOSE start before 2200
    assert window_of(call, 1).excluded_turns == 3
    assert "AT_CLOSE" not in window_of(call, 1).text
    # ONE, TWO, AT_CLOSE and THREE start before 4200
    assert window_of(call, 2).excluded_turns == 4
    # an agent turn that overlaps the caller's own line (starts at 100, before the edge at 200)
    early = build_judged(
        lines=2, answers=["EARLY", "LATE"], answer_starts_ms=[100, 900], golden=None
    )
    window = window_of(early, 0)
    assert window.text == "LATE"
    assert window.excluded_turns == 1
    assert window.turn_seqs == (1,)


def test_answer_window_last_question_ends_at_call_end() -> None:
    """For the last sent line the window closes at the end of the call, a turn that starts at or
    after that instant is outside, and a line that was never sent has no window."""
    call = build_judged(lines=3, answers=TEXTS, answer_starts_ms=STARTS)
    assert window_of(call, 2, call_end_ms=4301).text == "EDGE\nFOUR"
    assert window_of(call, 2, call_end_ms=4300).text == "EDGE"
    assert window_of(call, 2, call_end_ms=4200).status == "answer_window_empty"
    # the question was not sent: the call ended before the line, so it is not a disagreement
    unsent = window_of(call, 5)
    assert unsent.status == "question_not_sent"
    assert unsent.text == ""
    assert unsent.turn_seqs == ()
    assert unsent.open_ms is None
    # a line that was cut short (not complete) was not asked either
    cut = build_judged(lines=3, answers=TEXTS, answer_starts_ms=STARTS, complete_last_line=False)
    assert window_of(cut, 2).status == "question_not_sent"
    assert window_of(cut, 1).status == "ok"
    # no turn inside the window (the answer starts after the next line began)
    late = build_judged(lines=2, answers=["LATE"], answer_starts_ms=[2500])
    empty = window_of(late, 0)
    assert empty.status == "answer_window_empty"
    assert empty.text == ""
    assert empty.turn_seqs == ()
    assert empty.turn_ids == ()
    assert (empty.open_ms, empty.close_ms) == (200, 2000)
    assert empty.excluded_turns == 0
    # the statuses are a closed set
    assert tuple(mod("judgewindow").WINDOW_STATUSES) == (
        "ok",
        "question_not_sent",
        "answer_window_empty",
    )


def test_answer_window_reads_our_stt_agent_turns_never_export_or_script() -> None:
    """Only the ``our_stt`` transcript's agent-role turns count: not the agent's export (no shared
    time base, BL-167), not the script, and not the caller-role turn of another source."""
    export = export_read([("assistant", CANARY + " export one"), ("assistant", "export two")])
    call = build_judged(lines=3, answers=TEXTS, answer_starts_ms=STARTS, export=export)
    assert any(t.source == "agent_export" for t in call.bundle.transcripts)
    for index in (0, 1, 2):
        window = window_of(call, index)
        assert CANARY not in window.text
        assert "export" not in window.text
        assert all(text in TEXTS for text in window.text.split("\n"))
    # the script's own lines are never in a window
    scenario_line = call.golden.items[0].question
    assert all(scenario_line not in window_of(call, i).text for i in (0, 1, 2))
    # a turn whose role is not agent (or whose transcript is not our_stt) is ignored
    stt = next(t for t in call.bundle.transcripts if t.source == "our_stt")
    turns = list(call.bundle.turns)
    flipped = [
        t.model_copy(update={"role": "caller"}) if t.transcript_id == stt.id and t.seq == 1 else t
        for t in turns
    ]
    window = mod("judgewindow").answer_window(
        scripted_turn_index=0,
        transcripts=call.bundle.transcripts,
        turns=flipped,
        call_end_ms=call.call_end_ms,
    )
    assert window.text == "ONE"
    # a turn whose text is only white space says nothing: the window is empty
    blanked = [
        t.model_copy(update={"text": "  "}) if t.transcript_id == stt.id and t.seq in (0, 1) else t
        for t in turns
    ]
    silent = mod("judgewindow").answer_window(
        scripted_turn_index=0,
        transcripts=call.bundle.transcripts,
        turns=blanked,
        call_end_ms=call.call_end_ms,
    )
    assert silent.status == "answer_window_empty"
    assert silent.text == ""
    assert silent.turn_seqs == ()
    # without an our_stt transcript there is nothing to window (the command decides earlier)
    failed = build_judged(lines=3, stt_failed=True)
    with pytest.raises(ValueError, match=r"our_stt"):
        window_of(failed, 0)
    # the function takes records and times only: no clock, no engine, no file
    parameters = inspect.signature(mod("judgewindow").answer_window).parameters
    assert list(parameters) == ["scripted_turn_index", "transcripts", "turns", "call_end_ms"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
