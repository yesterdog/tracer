# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F4: a Pipecat ``conversation.json`` (messages with role user, assistant or tool_call) to one
observer Transcript with its Turns and the ToolCallEvents (api-surface-mappings.md section 4).

The file has no timing, no tool results and no bot turn in which the bot said nothing (Pipecat
documents all three), so the mapping states none of them. Both sides are context writes
(``agent_context``); the loss of the empty bot turn is a registered gap, not an invented Turn.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tracer_evalschema.events import ToolCallEvent
from tracer_evalschema.transcript import Transcript, Turn

from .common import (
    Citation,
    Draft,
    Leg,
    MappingResult,
    Recorder,
    TableRow,
    pointer,
    raw_text,
    scalar_leaves,
)

__all__ = ["PROVENANCE_TABLE", "map_f4"]

RETRIEVED = "2026-10-23"
OBSERVER_CITATION = Citation(
    url=(
        "https://github.com/pipecat-ai/pipecat/blob/v1.13.0/src/pipecat/evals/"
        "conversation_observer.py"
    ),
    retrieved_at=RETRIEVED,
    statement=(
        "ConversationObserver (pipecat-ai 1.13.0): each side's turn is what the context "
        "aggregators write to the LLM context, so the stage of both user and assistant turns is "
        "the conversation context."
    ),
)
_ROLES = {"user": "caller", "assistant": "agent"}


def map_f4(
    conversation: Sequence[Any], *, created_at: str, privacy: Mapping[str, Any]
) -> MappingResult:
    rec = Recorder("F4", created_at, privacy)
    transcript_id = rec.new_id(Transcript)
    turns = 0
    calls = 0
    items = conversation if isinstance(conversation, list) else []
    for index, message in enumerate(items):
        where = pointer(index)
        if not isinstance(message, dict):
            rec.warn("malformed_entry", "the entry is not a JSON object", path=where)
            continue
        role = message.get("role")
        role_path = where + "/role"
        if not isinstance(role, str):
            rec.warn("malformed_entry", "the entry has no text role", path=where)
            continue
        rec.consume(role_path)
        if role in _ROLES:
            turns += _turn(rec, message, index, transcript_id, turns)
        elif role == "tool_call":
            calls += _tool_call(rec, message, index, calls)
        else:
            rec.warn(
                "unknown_enum_value",
                "the role is not one of user, assistant or tool_call",
                path=role_path,
                raw=raw_text(role),
            )
    if turns:
        draft = Draft(Transcript, {"privacy": privacy})
        draft.set("source", "observer", Leg("/source", (), "constant:observer"))
        rec.commit(draft, entity_id=transcript_id)
    return rec.result()


def _turn(
    rec: Recorder, message: Mapping[str, Any], index: int, transcript_id: Any, seq: int
) -> int:
    where = pointer(index)
    content = message.get("content")
    if not isinstance(content, str):
        rec.warn("malformed_entry", "the message has no text content", path=where)
        return 0
    role = str(message["role"])
    role_path = where + "/role"
    text, text_derivation = rec.text(content, where + "/content")
    draft = Draft(Turn, {"transcript_id": transcript_id})
    draft.set("seq", seq, Leg("/seq", (role_path,), "order_index"))
    draft.set("role", _ROLES[role], Leg("/role", (role_path,), f"constant:{_ROLES[role]}"))
    draft.set(
        "source",
        "agent_context",
        Leg("/source", (role_path,), "documented_conversation_stage", OBSERVER_CITATION),
    )
    draft.set("timing_source", "none", Leg("/timing_source", (role_path,), "constant:none"))
    draft.set("text", text, Leg("/text", (where + "/content",), text_derivation))
    return 1 if rec.commit(draft, where=where) is not None else 0


def _tool_call(rec: Recorder, message: Mapping[str, Any], index: int, seq: int) -> int:
    where = pointer(index)
    name = message.get("name")
    arguments = message.get("arguments")
    if not isinstance(name, str):
        rec.warn("malformed_entry", "the tool call has no name", path=where)
        return 0
    if not isinstance(arguments, dict):
        rec.warn("malformed_entry", "the tool call arguments are not a JSON object", path=where)
        return 0
    name_text, name_derivation = rec.text(name, where + "/name")
    cancelled = message.get("cancelled")
    status = "unknown"
    status_sources: tuple[str, ...] = (where + "/role",)
    if cancelled is True:
        status = "cancelled"
        status_sources = (where + "/cancelled",)
    elif "cancelled" in message:
        status_sources = (where + "/cancelled",)
        if cancelled is not False:
            rec.warn(
                "unknown_enum_value",
                "cancelled is not a boolean; the status stays unknown",
                path=where + "/cancelled",
                raw=raw_text(cancelled),
            )
    draft = Draft(ToolCallEvent)
    draft.set("name", name_text, Leg("/name", (where + "/name",), name_derivation))
    draft.set(
        "arguments",
        arguments,
        *(
            Leg("/arguments" + leaf, (where + "/arguments" + leaf,), None)
            for leaf, _ in scalar_leaves(arguments)
        ),
    )
    draft.set(
        "arguments_complete",
        True,
        Leg("/arguments_complete", (where + "/arguments",), "source_is_json_object"),
    )
    draft.set("status", status, Leg("/status", status_sources, f"constant:{status}"))
    draft.set("seq", seq, Leg("/seq", (where + "/role",), "order_index"))
    draft.set("source", "observer", Leg("/source", (where + "/role",), "constant:observer"))
    return 1 if rec.commit(draft, where=where) is not None else 0


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
    _row(
        "Transcript.source", "constant", None, "constant:observer", "Pipecat conversation observer"
    ),
    _row(
        "Transcript.declared_language",
        "absent",
        None,
        None,
        "no declaration was given to the importer; never filled from the source",
    ),
    _row("Transcript.detected_language", "absent", None, None, "the file has no language label"),
    _row(
        "Turn.seq",
        "derivation",
        "role",
        "order_index",
        "position among the user and assistant messages",
    ),
    _row("Turn.role", "constant", "role", "constant:caller", "user is the caller"),
    _row("Turn.role", "constant", "role", "constant:agent", "assistant is the agent"),
    _row(
        "Turn.source",
        "derivation",
        "role",
        "documented_conversation_stage",
        "both sides are context writes (M8)",
        OBSERVER_CITATION,
    ),
    _row("Turn.timing_source", "constant", "role", "constant:none", "the file has no timing"),
    _row("Turn.text", "source", "content", None, "verbatim"),
    _row(
        "Turn.text",
        "derivation",
        "content",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row("Turn.wall_time", "absent", None, None, "the file has no timestamps"),
    _row("ToolCallEvent.name", "source", "name", None, "verbatim"),
    _row(
        "ToolCallEvent.name",
        "derivation",
        "name",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row("ToolCallEvent.arguments.*", "source", "arguments", None, "the object as given"),
    _row(
        "ToolCallEvent.arguments_complete",
        "derivation",
        "arguments",
        "source_is_json_object",
        "the source gives a whole JSON object, not a string that could be cut",
    ),
    _row("ToolCallEvent.arguments_raw", "absent", None, None, "the source has no argument string"),
    _row("ToolCallEvent.result_raw", "absent", None, None, "the file holds no tool results"),
    _row(
        "ToolCallEvent.status",
        "constant",
        "cancelled",
        "constant:cancelled",
        "cancelled is true",
    ),
    _row(
        "ToolCallEvent.status",
        "constant",
        "cancelled",
        "constant:unknown",
        "cancelled false, absent or not a boolean says nothing about completion",
    ),
    _row("ToolCallEvent.seq", "derivation", "role", "order_index", "position among the tool calls"),
    _row("ToolCallEvent.source", "constant", "role", "constant:observer", "Pipecat observer"),
)
