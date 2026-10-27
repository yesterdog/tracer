# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The answer window of a golden item (ADR 0030 rule 4).

Pure: a function of the records of a simulated call. The window opens at the end edge of the caller
turn that holds the item's line and closes at the start of the next sent caller turn, or at the end
of the call for the last line. It holds the ``our_stt`` agent-channel turns that start inside it, in
``seq`` order, joined by one U+000A. It is never built from the agent's export and never from the
script.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, cast

from tracer_evalcore.simbundle import TURN_COMPLETE_KEY
from tracer_evalschema.ids import TranscriptId, TurnId
from tracer_evalschema.transcript import Transcript, Turn

__all__ = ["WINDOW_STATUSES", "AnswerWindow", "answer_window"]

WINDOW_STATUSES: Final = ("ok", "question_not_sent", "answer_window_empty")


@dataclass(frozen=True)
class AnswerWindow:
    status: str
    open_ms: int | None
    close_ms: int | None
    text: str
    turn_seqs: tuple[int, ...]
    turn_ids: tuple[TurnId, ...]
    excluded_turns: int
    transcript_id: TranscriptId | None

    def __post_init__(self) -> None:
        if self.status not in WINDOW_STATUSES:
            raise ValueError("status: not an accepted value")
        if len(self.turn_seqs) != len(self.turn_ids):
            raise ValueError("turn_seqs and turn_ids differ in length")


def _empty(
    status: str,
    stt: Transcript,
    *,
    open_ms: int | None = None,
    close_ms: int | None = None,
    excluded: int = 0,
) -> AnswerWindow:
    return AnswerWindow(status, open_ms, close_ms, "", (), (), excluded, cast(TranscriptId, stt.id))


def answer_window(
    *,
    scripted_turn_index: int,
    transcripts: Sequence[Transcript],
    turns: Sequence[Turn],
    call_end_ms: int,
) -> AnswerWindow:
    """The window of the item bound to ``scripted_turn_index``; see the module text."""
    if type(scripted_turn_index) is not int or scripted_turn_index < 0:
        raise ValueError("scripted_turn_index: not a non-negative integer")
    if type(call_end_ms) is not int or call_end_ms < 0:
        raise ValueError("call_end_ms: not a non-negative integer")
    stt = [t for t in transcripts if t.source == "our_stt"]
    if len(stt) != 1:
        raise ValueError("answer_window needs exactly one our_stt transcript")
    transcript = stt[0]
    script_ids = {t.id for t in transcripts if t.source == "script"}
    script = sorted((t for t in turns if t.transcript_id in script_ids), key=lambda t: t.seq)
    line = next((t for t in script if t.seq == scripted_turn_index), None)
    if line is None or line.end_ms is None or line.metadata.get(TURN_COMPLETE_KEY) is False:
        return _empty("question_not_sent", transcript)
    opening = line.end_ms
    following = [
        t.start_ms for t in script if t.seq > scripted_turn_index and t.start_ms is not None
    ]
    closing = min(following) if following else call_end_ms
    agent = sorted(
        (
            t
            for t in turns
            if t.transcript_id == transcript.id and t.role == "agent" and t.start_ms is not None
        ),
        key=lambda t: t.seq,
    )
    excluded = sum(1 for t in agent if t.start_ms is not None and t.start_ms < opening)
    inside = [t for t in agent if t.start_ms is not None and opening <= t.start_ms < closing]
    if not inside or not any(t.text.strip() for t in inside):
        return _empty(
            "answer_window_empty", transcript, open_ms=opening, close_ms=closing, excluded=excluded
        )
    return AnswerWindow(
        status="ok",
        open_ms=opening,
        close_ms=closing,
        text="\n".join(t.text for t in inside),
        turn_seqs=tuple(t.seq for t in inside),
        turn_ids=tuple(cast(TurnId, t.id) for t in inside),
        excluded_turns=excluded,
        transcript_id=cast(TranscriptId, transcript.id),
    )
