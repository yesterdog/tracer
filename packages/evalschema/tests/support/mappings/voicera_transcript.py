# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F8: a VoicEra-style transcript export (one text file) to one Transcript and one Turn per
message line (docs/phase1/api-surface-mappings-3.md).

The file is a header line ``call_id=<id>``, a ``---`` line and message lines
``[<timestamp>] <speaker>: <text>``. The export says nothing about audio, timing offsets,
language, confidence, tools or how the call ended, so none of that is stated. Source pointers
address lines: ``f8:<n>`` and ``f8:<n>/call_id|timestamp|speaker|text``. The converter takes the
decoded text, never a file, and reports only through the returned result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import TranscriptId
from tracer_evalschema.transcript import Transcript, Turn

from .common import Draft, Leg, MappingResult, Recorder, TableRow

__all__ = ["PROVENANCE_TABLE", "map_f8"]

_HEADER = re.compile(r"call_id=(?P<value>.+)")
_UUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
_MESSAGE = re.compile(r"\[(?P<ts>[^\]]*)\] (?P<speaker>[^\s:]+):(?: (?P<text>.*))?")
_INSTANT = re.compile(
    r"(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})[T ](?P<h>[0-9]{2}):(?P<mi>[0-9]{2})"
    r":(?P<s>[0-9]{2})(?:\.(?P<frac>[0-9]+))?(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?"
)
_ROLE = {"assistant": "agent", "user": "caller"}
_BOM = "﻿"


def _lines(text: str) -> list[str]:
    """Split at LF only, drop one trailing CR per line, the empty piece after a closing LF and
    one leading U+FEFF."""
    pieces = text.split("\n")
    if pieces and pieces[-1] == "":
        pieces.pop()
    out = [piece[:-1] if piece.endswith("\r") else piece for piece in pieces]
    if out and out[0].startswith(_BOM):
        out[0] = out[0][1:]
    return out


def _instant(stamp: str) -> tuple[str | None, str | None]:
    """(UTC text with six fractional digits or ``None``, the warning code or ``None``)."""
    found = _INSTANT.fullmatch(stamp)
    if found is None:
        return None, "timestamp_not_parseable"
    zone = found["zone"]
    if zone is None:
        return None, "naive_timestamp"
    try:
        if zone == "Z":
            offset = timedelta(0)
        else:
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            if hours > 23 or minutes > 59:
                return None, "timestamp_not_parseable"
            offset = timedelta(hours=hours, minutes=minutes)
            if zone[0] == "-":
                offset = -offset
        fraction = found["frac"] or ""
        micro = int(fraction[:6].ljust(6, "0"))
        moment = datetime(
            int(found["y"]),
            int(found["mo"]),
            int(found["d"]),
            int(found["h"]),
            int(found["mi"]),
            int(found["s"]),
            micro,
            tzinfo=timezone(offset),
        )
        text = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, OverflowError):
        return None, "timestamp_not_parseable"
    return text, ("time_precision_reduced" if len(fraction) > 6 else None)


def map_f8(
    text: str,
    *,
    created_at: str,
    privacy: Mapping[str, Any],
    declared_language: Mapping[str, Any] | None = None,
) -> MappingResult:
    if not isinstance(text, str):
        raise TypeError("an F8 export is passed as decoded text")
    # The caller's own arguments are checked first, whatever the content: the model raises.
    arguments: dict[str, Any] = {
        "id": TranscriptId.new(),
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "source": "agent_export",
        "privacy": privacy,
        "declared_language": declared_language,
    }
    Transcript(**arguments)
    rec = Recorder("F8", created_at, privacy)
    lines = _lines(text)
    if all(line.strip() == "" for line in lines):
        rec.warn("empty_input", "the export has no line with content")
        return rec.result()

    transcript_id = rec.new_id(Transcript)
    head = Draft(Transcript, {"privacy": privacy})
    head.set(
        "source",
        "agent_export",
        Leg("/source", (), "constant:agent_export"),
    )
    if declared_language is not None:
        head.set("declared_language", declared_language)
    header_ok = False
    turns = 0
    for index, line in enumerate(lines):
        number = index + 1
        where = f"f8:{number}"
        if number == 1:
            found = _HEADER.fullmatch(line)
            if found is not None:
                header_ok = True
                _header(rec, head, found["value"])
                continue
            rec.warn(
                "header_not_recognized",
                "the first line is not a call_id header",
                path=where,
                raw=line,
            )
        elif number == 2:
            if line == "---":
                rec.consume(where)
                continue
            rec.warn(
                "delimiter_not_found",
                "the second line is not the header delimiter",
                path=where,
                raw=line,
            )
            if _MESSAGE.fullmatch(line) is None:
                continue
        if line.strip() == "":
            continue
        message = _MESSAGE.fullmatch(line)
        if message is None:
            if number > 2:
                rec.warn("unparsed_line", "the line is not a message line", path=where, raw=line)
            continue
        if _message(rec, transcript_id, where, message, turns):
            turns += 1

    if turns or header_ok:
        rec.commit(head, entity_id=transcript_id)
    return rec.result()


def _header(rec: Recorder, head: Draft, value: str) -> None:
    source = "f8:1/call_id"
    if not _UUID.fullmatch(value):
        rec.warn(
            "call_id_not_uuid",
            "the call id is not shaped like a UUID and is kept as it is",
            path=source,
            raw=value,
        )
    if not rec.external_ref("voicera", "call_id", value, source):
        return
    head.set(
        "provider_call_ref",
        {"system": "voicera", "kind": "call_id", "value": value},
        Leg("/provider_call_ref/system", (source,), "constant:voicera"),
        Leg("/provider_call_ref/kind", (source,), "constant:call_id"),
        Leg("/provider_call_ref/value", (source,)),
    )


def _message(
    rec: Recorder, transcript_id: Any, where: str, message: re.Match[str], seq: int
) -> bool:
    speaker = message["speaker"]
    role = _ROLE.get(speaker)
    if role is None:
        rec.warn(
            "unknown_enum_value",
            "the speaker label is not assistant or user, so the line is not mapped",
            path=f"{where}/speaker",
            raw=speaker,
        )
        return False
    by_speaker = (f"{where}/speaker",)
    by_text = (f"{where}/text",)
    stamp_at = (f"{where}/timestamp",)
    turn = Draft(Turn, {"transcript_id": transcript_id})
    turn.set("seq", seq, Leg("/seq", by_speaker, "order_index"))
    turn.set("role", role, Leg("/role", by_speaker, f"constant:{role}"))
    turn.set("source", "agent_log", Leg("/source", by_speaker, "constant:agent_log"))
    turn.set("timing_source", "none", Leg("/timing_source", by_speaker, "constant:none"))
    body, derivation = rec.text(message["text"] or "", f"{where}/text")
    turn.set("text", body, Leg("/text", by_text, derivation))
    stamp = message["ts"]
    instant, code = _instant(stamp)
    if instant is not None:
        turn.set(
            "wall_time",
            {"at": instant, "meaning": "unknown"},
            Leg("/wall_time/at", stamp_at, "iso_offset_to_utc_us"),
            Leg("/wall_time/meaning", stamp_at, "constant:unknown"),
        )
    if code is not None:
        rec.warn(
            code,
            _TIME_MESSAGES[code],
            path=f"{where}/timestamp",
            raw=stamp,
        )
    return rec.commit(turn, where=where) is not None


_TIME_MESSAGES = {
    "timestamp_not_parseable": "the timestamp is not an ISO-8601 instant; no wall time is stored",
    "naive_timestamp": "the timestamp has no zone; no wall time is stored",
    "time_precision_reduced": "digits finer than a microsecond were cut",
}


def _row(target: str, kind: str, source: str | None, derivation: str | None, note: str) -> TableRow:
    return TableRow(target, kind, source, derivation, None, note)


def _absent(target: str, note: str) -> TableRow:
    return _row(target, "absent", None, None, note)


_NO_LABEL = "the export names no such value, so none is stored"

PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row("Transcript.source", "constant", None, "constant:agent_export", "an exported agent log"),
    _row(
        "Transcript.provider_call_ref.system",
        "constant",
        "header.call_id",
        "constant:voicera",
        "the platform name is the user's statement (ADR 0018 Decision 6, unverified)",
    ),
    _row(
        "Transcript.provider_call_ref.kind",
        "constant",
        "header.call_id",
        "constant:call_id",
        "the header names a call id",
    ),
    _row(
        "Transcript.provider_call_ref.value",
        "source",
        "header.call_id",
        None,
        "verbatim, case kept, not parsed",
    ),
    _row(
        "Transcript.declared_language",
        "user_declared",
        None,
        None,
        "the caller's declaration (N14), stored as supplied and never read as observed",
    ),
    _absent("Transcript.provider", _NO_LABEL),
    _absent("Transcript.model", _NO_LABEL),
    _absent("Transcript.detected_language", "the export carries no language label"),
    _absent("Transcript.detected_language_raw", "the export carries no language label"),
    _absent("Transcript.language_confidence", "the export carries no confidence"),
    _absent("Transcript.timeline_origin", "no turn has an offset, so there is no origin"),
    _row("Turn.seq", "derivation", "message.speaker", "order_index", "0-based count of turns made"),
    _row(
        "Turn.role",
        "constant",
        "message.speaker",
        "constant:agent",
        "the label assistant is the agent",
    ),
    _row(
        "Turn.role",
        "constant",
        "message.speaker",
        "constant:caller",
        "the label user is the caller",
    ),
    _row(
        "Turn.source",
        "constant",
        "message.speaker",
        "constant:agent_log",
        "the export does not say which pipeline stage produced a line",
    ),
    _row(
        "Turn.timing_source",
        "constant",
        "message.speaker",
        "constant:none",
        "the line has no offset, only a wall-clock timestamp",
    ),
    _row("Turn.text", "source", "message.text", None, "verbatim, never normalised or stripped"),
    _row(
        "Turn.text",
        "derivation",
        "message.text",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row(
        "Turn.wall_time.at",
        "derivation",
        "message.timestamp",
        "iso_offset_to_utc_us",
        "the instant converted to UTC with six fractional digits",
    ),
    _row(
        "Turn.wall_time.meaning",
        "constant",
        "message.timestamp",
        "constant:unknown",
        "the export does not say whether the stamp is a start, an end or a log time",
    ),
    _absent("Turn.start_ms", "the export has no offset"),
    _absent("Turn.end_ms", "the export has no offset"),
    _absent("Turn.segments", "the export has no sub-line structure"),
    _absent("Turn.language", "the export carries no language label"),
    _absent("Turn.language_raw", "the export carries no language label"),
    _absent("Turn.interrupted", "the export does not say; never false"),
    _absent("Turn.exchange_index", "no Exchange is built from per-line stamps"),
)
