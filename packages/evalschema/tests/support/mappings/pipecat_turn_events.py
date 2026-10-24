# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F7: Pipecat turn events (a list of event objects) to one observer Transcript with its Turns and
one Exchange per Pipecat turn (api-surface-mappings.md section 5).

The events carry three fields the capture harness adds (``seq``, ``source`` and
``harness_observed_at_utc``). The harness clock is not Pipecat's, so none of them is ever read as
data: they are registered gaps. The message timestamp is the start of the turn (Pipecat documents
it so) and is kept as a ``wall_time``, never as an offset.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tracer_evalschema.transcript import Exchange, Transcript, Turn

from .common import (
    Citation,
    Draft,
    Leg,
    MappingResult,
    Recorder,
    TableRow,
    floor_ms,
    format_utc_instant,
    pointer,
    raw_text,
)

__all__ = ["PROVENANCE_TABLE", "map_f7"]

RETRIEVED = "2026-10-23"
AGGREGATOR_URL = (
    "https://github.com/pipecat-ai/pipecat/blob/v1.13.0/src/pipecat/processors/aggregators/"
    "llm_response_universal.py"
)
STAGE_CITATION = Citation(
    url=AGGREGATOR_URL,
    retrieved_at=RETRIEVED,
    statement=(
        "The turn-stopped message holds the aggregated transcript 'that is then used in the "
        "context' (pipecat-ai 1.13.0), so its stage is the conversation context."
    ),
)
START_CITATION = Citation(
    url=AGGREGATOR_URL,
    retrieved_at=RETRIEVED,
    statement=(
        "UserTurnStoppedMessage.timestamp: 'When the user turn started'; "
        "AssistantTurnStoppedMessage.timestamp: 'When the assistant turn started' "
        "(pipecat-ai 1.13.0)."
    ),
)
_STOPPED = {
    "on_user_turn_stopped": ("caller", False),
    "on_assistant_turn_stopped": ("agent", True),
}
_KNOWN_UNMAPPED = frozenset(
    {
        "on_user_turn_started",
        "on_assistant_turn_started",
        "bot_started_speaking",
        "bot_stopped_speaking",
    }
)
_INSTANT = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.(?P<frac>\d+))?(?P<offset>Z|[+-]\d{2}:\d{2})?"
)


@dataclass
class _ExchangeState:
    number: int
    number_source: str
    duration_ms: int | None = None
    duration_source: str | None = None
    interrupted: bool | None = None
    interrupted_source: str | None = None
    started: bool = False
    ended: bool = False


def map_f7(events: Sequence[Any], *, created_at: str, privacy: Mapping[str, Any]) -> MappingResult:
    rec = Recorder("F7", created_at, privacy)
    transcript_id = rec.new_id(Transcript)
    exchanges: dict[int, _ExchangeState] = {}
    turns = 0
    for index, event in enumerate(events if isinstance(events, list) else []):
        where = pointer(index)
        if not isinstance(event, dict):
            rec.warn("malformed_entry", "the event is not a JSON object", path=where)
            continue
        name = event.get("event")
        if not isinstance(name, str):
            rec.warn("malformed_entry", "the event has no text name", path=where)
            continue
        rec.consume(where + "/event")
        payload = event.get("payload")
        if name in _STOPPED:
            turns += _turn_stopped(rec, name, payload, index, transcript_id, turns)
        elif name in ("on_turn_started", "on_turn_ended"):
            _turn_boundary(rec, name, payload, index, exchanges)
        elif name not in _KNOWN_UNMAPPED:
            rec.warn(
                "unknown_enum_value",
                "the event name is not one of the mapped Pipecat events",
                path=where + "/event",
                raw=name,
            )
    if turns:
        draft = Draft(Transcript, {"privacy": privacy})
        draft.set("source", "observer", Leg("/source", (), "constant:observer"))
        rec.commit(draft, entity_id=transcript_id)
    for state in exchanges.values():
        _exchange(rec, state)
    return rec.result()


def _wall_time(rec: Recorder, message: Mapping[str, Any], where: str) -> tuple[str, str] | None:
    """(UTC text, source pointer) of the message timestamp, or ``None`` with a warning."""
    if "timestamp" not in message or message["timestamp"] is None:
        return None
    stamp = message["timestamp"]
    path = where + "/timestamp"
    if not isinstance(stamp, str):
        rec.warn(
            "timestamp_not_parseable",
            "the timestamp is not text; a number is never read as an instant",
            path=path,
            raw=raw_text(stamp),
        )
        return None
    found = _INSTANT.fullmatch(stamp)
    if found is None:
        rec.warn("timestamp_not_parseable", "the timestamp is not an instant", path=path, raw=stamp)
        return None
    if found["offset"] is None:
        rec.warn(
            "naive_timestamp",
            "the timestamp has no offset; UTC is never assumed",
            path=path,
            raw=stamp,
        )
        return None
    try:
        moment = datetime.fromisoformat(stamp).astimezone(UTC)
        text = format_utc_instant(moment)
    except (ValueError, OverflowError):
        rec.warn("timestamp_not_parseable", "the timestamp is not an instant", path=path, raw=stamp)
        return None
    if (found["frac"] or "")[6:].strip("0"):
        rec.warn(
            "time_precision_reduced",
            "digits finer than a microsecond were cut",
            path=path,
            raw=stamp,
        )
    return text, path


def _turn_stopped(
    rec: Recorder, name: str, payload: Any, index: int, transcript_id: Any, seq: int
) -> int:
    where = pointer(index)
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        located = where + "/payload" if isinstance(payload, dict) else where
        if isinstance(payload, dict) and "message" in payload:
            located += "/message"
        rec.warn("malformed_entry", "the event has no message object", path=located)
        return 0
    mpath = where + "/payload/message"
    content = message.get("content")
    if content is None:
        rec.warn(
            "turn_text_missing",
            "the message has no text content",
            path=mpath + "/content" if "content" in message else mpath,
        )
        return 0
    if not isinstance(content, str):
        rec.warn("malformed_entry", "the message content is not text", path=mpath + "/content")
        return 0
    role, assistant = _STOPPED[name]
    event_path = where + "/event"
    text, text_derivation = rec.text(content, mpath + "/content")
    draft = Draft(Turn, {"transcript_id": transcript_id})
    draft.set("seq", seq, Leg("/seq", (event_path,), "order_index"))
    draft.set("role", role, Leg("/role", (event_path,), f"constant:{role}"))
    draft.set(
        "source",
        "agent_context",
        Leg("/source", (event_path,), "documented_conversation_stage", STAGE_CITATION),
    )
    draft.set("timing_source", "none", Leg("/timing_source", (event_path,), "constant:none"))
    draft.set("text", text, Leg("/text", (mpath + "/content",), text_derivation))
    stamp = _wall_time(rec, message, mpath)
    if stamp is not None:
        draft.set(
            "wall_time",
            {"at": stamp[0], "meaning": "start"},
            Leg("/wall_time/at", (stamp[1],), "iso_offset_to_utc_us"),
            Leg("/wall_time/meaning", (stamp[1],), "documented_start_of_turn", START_CITATION),
        )
    if assistant and "interrupted" in message:
        if type(message["interrupted"]) is bool:
            draft.set(
                "interrupted",
                message["interrupted"],
                Leg("/interrupted", (mpath + "/interrupted",)),
            )
        else:
            rec.warn(
                "malformed_entry",
                "interrupted is not a boolean and stays unknown",
                path=mpath + "/interrupted",
                raw=raw_text(message["interrupted"]),
            )
    return 1 if rec.commit(draft, where=where) is not None else 0


def _turn_boundary(
    rec: Recorder, name: str, payload: Any, index: int, exchanges: dict[int, _ExchangeState]
) -> None:
    where = pointer(index)
    body = payload if isinstance(payload, dict) else {}
    number = body.get("turn_number")
    number_path = where + "/payload/turn_number"
    if type(number) is not int or number < 0:
        rec.warn(
            "malformed_entry",
            "the event has no usable turn number",
            path=number_path if "turn_number" in body else where,
        )
        return
    rec.consume(number_path)
    ended = name == "on_turn_ended"
    state = exchanges.get(number)
    if state is None:
        state = exchanges[number] = _ExchangeState(number, number_path)
    if state.ended if ended else state.started:
        rec.warn(
            "duplicate_turn_number",
            "the turn number was reported twice; the first report is kept",
            path=number_path,
            raw=raw_text(number),
        )
        return
    if not ended:
        state.started = True
        return
    state.ended = True
    if "duration" in body:
        measured = floor_ms(body["duration"])
        if measured is None:
            rec.warn(
                "malformed_entry",
                "the duration is not a non-negative number of seconds",
                path=where + "/payload/duration",
                raw=raw_text(body["duration"]),
            )
        else:
            state.duration_ms = measured[0]
            state.duration_source = where + "/payload/duration"
            if measured[1]:
                rec.warn(
                    "time_precision_reduced",
                    "digits finer than a millisecond were cut",
                    path=state.duration_source,
                    raw=raw_text(body["duration"]),
                )
    if "was_interrupted" in body:
        if type(body["was_interrupted"]) is bool:
            state.interrupted = body["was_interrupted"]
            state.interrupted_source = where + "/payload/was_interrupted"
        else:
            rec.warn(
                "malformed_entry",
                "was_interrupted is not a boolean and stays unknown",
                path=where + "/payload/was_interrupted",
                raw=raw_text(body["was_interrupted"]),
            )


def _exchange(rec: Recorder, state: _ExchangeState) -> None:
    draft = Draft(Exchange)
    draft.set("index", state.number, Leg("/index", (state.number_source,)))
    if state.duration_ms is not None and state.duration_source is not None:
        draft.set(
            "duration_ms",
            state.duration_ms,
            Leg("/duration_ms", (state.duration_source,), "seconds_to_ms_floor"),
        )
    if state.interrupted is not None and state.interrupted_source is not None:
        draft.set(
            "was_interrupted",
            state.interrupted,
            Leg("/was_interrupted", (state.interrupted_source,)),
        )
    draft.set("source", "observer", Leg("/source", (state.number_source,), "constant:observer"))
    rec.commit(draft, where=state.number_source)


def _row(
    target: str,
    kind: str,
    source: str | None,
    derivation: str | None,
    note: str,
    citation: Citation | None = None,
) -> TableRow:
    return TableRow(target, kind, source, derivation, citation, note)


PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row("Transcript.source", "constant", None, "constant:observer", "Pipecat turn observers"),
    _row(
        "Transcript.declared_language",
        "absent",
        None,
        None,
        "no declaration was given to the importer; never filled from the source",
    ),
    _row("Transcript.detected_language", "absent", None, None, "the events have no language"),
    _row("Turn.seq", "derivation", "event", "order_index", "order of the turn-stopped events"),
    _row("Turn.role", "constant", "event", "constant:caller", "on_user_turn_stopped"),
    _row("Turn.role", "constant", "event", "constant:agent", "on_assistant_turn_stopped"),
    _row(
        "Turn.source",
        "derivation",
        "event",
        "documented_conversation_stage",
        "the message is the transcript used in the context (M8)",
        STAGE_CITATION,
    ),
    _row("Turn.timing_source", "constant", "event", "constant:none", "no offsets in the events"),
    _row("Turn.text", "source", "payload.message.content", None, "verbatim"),
    _row(
        "Turn.text",
        "derivation",
        "payload.message.content",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row(
        "Turn.wall_time.at",
        "derivation",
        "payload.message.timestamp",
        "iso_offset_to_utc_us",
        "an offset-aware instant converted to UTC, truncated to microseconds",
    ),
    _row(
        "Turn.wall_time.meaning",
        "derivation",
        "payload.message.timestamp",
        "documented_start_of_turn",
        "Pipecat documents the timestamp as the start of the turn",
        START_CITATION,
    ),
    _row("Turn.interrupted", "source", "payload.message.interrupted", None, "assistant turns only"),
    _row("Turn.exchange_index", "absent", None, None, "the events state no link (M11)"),
    _row("Exchange.index", "source", "payload.turn_number", None, "1-based as Pipecat counts"),
    _row(
        "Exchange.duration_ms",
        "derivation",
        "payload.duration",
        "seconds_to_ms_floor",
        "seconds truncated to whole milliseconds",
    ),
    _row("Exchange.was_interrupted", "source", "payload.was_interrupted", None, "verbatim"),
    _row("Exchange.source", "constant", "payload.turn_number", "constant:observer", "observer"),
    _row("Exchange.started_at", "absent", None, None, "the turn-start event carries no time"),
)
