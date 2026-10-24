# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Reader of a VoicEra-style transcript export into one ``Transcript`` and its ``Turn`` records
(P3-04; the mapping of docs/phase1/api-surface-mappings-3.md, F8, promoted into the product).

The export is a header line ``call_id=<id>``, a ``---`` line and message lines
``[<timestamp>] <speaker>: <text>``. It says nothing about audio, timing offsets, language,
confidence, tools or how the call ended, so none of that is stated. Source pointers address
lines: ``f8:<n>`` and ``f8:<n>/call_id|timestamp|speaker|text``. The reader takes the decoded
text (never a file) and has no clock: ``created_at`` is an argument.

A warning that drops or alters content refuses the read unless the caller accepts it. A call id
with a control or bidirectional character is always refused, and its text is never repeated.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from tracer_evalcore.errors import InputRefusedError
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import ExternalRef, TranscriptId, TurnId
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.text import replace_lone_surrogates
from tracer_evalschema.transcript import LanguageDeclaration, Transcript, Turn

__all__ = [
    "NON_REFUSING_WARNING_CODES",
    "VoicEraRead",
    "VoicEraRefusedError",
    "read_voicera_export",
]

NON_REFUSING_WARNING_CODES: frozenset[str] = frozenset(
    {"call_id_not_uuid", "timestamp_not_parseable", "naive_timestamp", "time_precision_reduced"}
)
"""Warnings that lose nothing. Any other code, including one a later version adds, refuses."""

_MAX_LISTED = 20
_UNSAFE_CALL_ID = "call_id_unsafe_characters"
_CALL_ID_POINTER = "f8:1/call_id"
_BIDI = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏؜")

_HEADER = re.compile(r"call_id=(?P<value>.+)")
_UUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
_MESSAGE = re.compile(r"\[(?P<ts>[^\]]*)\] (?P<speaker>[^\s:]+):(?: (?P<text>.*))?")
_INSTANT = re.compile(
    r"(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})[T ](?P<h>[0-9]{2}):(?P<mi>[0-9]{2})"
    r":(?P<s>[0-9]{2})(?:\.(?P<frac>[0-9]+))?(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?"
)
_ROLE = {"assistant": "agent", "user": "caller"}
_BOM = "﻿"
_TIME_MESSAGES = {
    "timestamp_not_parseable": "the timestamp is not an ISO-8601 instant; no wall time is stored",
    "naive_timestamp": "the timestamp has no zone; no wall time is stored",
    "time_precision_reduced": "digits finer than a microsecond were cut",
}


@dataclass(frozen=True)
class VoicEraRead:
    transcript: Transcript | None
    turns: tuple[Turn, ...]
    warnings: tuple[IngestWarning, ...]
    external_refs: tuple[ExternalRef, ...]


class VoicEraRefusedError(InputRefusedError):
    """The export was refused. ``warnings`` holds every warning of the read (they quote the
    dropped lines in ``raw``); the message holds only codes and source pointers."""

    warnings: tuple[IngestWarning, ...]

    def __init__(self, message: str, warnings: tuple[IngestWarning, ...] = ()) -> None:
        super().__init__(message)
        self.warnings = warnings

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.args[0], self.warnings))


def _refusal_message(refusing: list[IngestWarning]) -> str:
    listed = [w.code if w.path is None else f"{w.code} at {w.path}" for w in refusing[:_MAX_LISTED]]
    text = "VoicEra export refused: " + "; ".join(listed)
    if len(refusing) > _MAX_LISTED:
        text += f"; and {len(refusing) - _MAX_LISTED} more"
    return text


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
        moment = datetime(
            int(found["y"]),
            int(found["mo"]),
            int(found["d"]),
            int(found["h"]),
            int(found["mi"]),
            int(found["s"]),
            int(fraction[:6].ljust(6, "0")),
            tzinfo=timezone(offset),
        )
        utc = moment.astimezone(UTC)
        text = (
            f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T"
            f"{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}.{utc.microsecond:06d}Z"
        )
    except (ValueError, OverflowError):
        return None, "timestamp_not_parseable"
    return text, ("time_precision_reduced" if len(fraction) > 6 else None)


def _unsafe(value: str) -> bool:
    return any(ch in _BIDI or unicodedata.category(ch) == "Cc" for ch in value)


class _Reader:
    """What one read collects: warnings (once per code, path and raw) and the records."""

    def __init__(self, created_at: str | datetime) -> None:
        self.created_at = created_at
        self.warnings: list[IngestWarning] = []
        self.external_refs: list[ExternalRef] = []
        self._seen: set[tuple[str, str | None, str | None]] = set()

    def warn(
        self, code: str, message: str, *, path: str | None = None, raw: str | None = None
    ) -> None:
        shown_path = None if path is None else replace_lone_surrogates(path)
        shown_raw = None if raw is None else replace_lone_surrogates(raw)
        key = (code, shown_path, shown_raw)
        if key in self._seen:
            return
        self._seen.add(key)
        self.warnings.append(
            IngestWarning(code=code, path=shown_path, raw=shown_raw, message=message)
        )

    def text(self, value: str, source: str) -> str:
        """The text to store: a lone surrogate cannot be stored as UTF-8, so it becomes U+FFFD
        and ``text_not_valid_unicode`` says where."""
        replaced = replace_lone_surrogates(value)
        if replaced != value:
            self.warn(
                "text_not_valid_unicode",
                "the text held a lone surrogate, stored with U+FFFD in its place",
                path=source,
                raw=replaced,
            )
        return replaced

    def external_ref(self, value: str, source: str) -> bool:
        if replace_lone_surrogates(value) != value:
            self.warn(
                "text_not_valid_unicode",
                "an external identifier holds a lone surrogate and is not stored",
                path=source,
            )
            return False
        self.external_refs.append(ExternalRef(system="voicera", kind="call_id", value=value))
        return True

    def build(
        self,
        cls: type[Any],
        fixed: Mapping[str, Any],
        values: dict[str, Any],
        *,
        entity_id: Any,
        where: str | None,
    ) -> Any | None:
        """Build a record. An optional field the schema refuses is dropped with a warning; a
        record that cannot be built without a field is dropped with a warning. A refusal that
        names one of the caller's own arguments is raised: it is not content."""
        values = dict(values)
        optional = {name for name, info in cls.model_fields.items() if not info.is_required()}
        base = {
            **fixed,
            "id": entity_id,
            "schema_version": SCHEMA_VERSION,
            "created_at": self.created_at,
        }
        caller = set(fixed) | {"created_at"}
        for _ in range(len(values) + 1):
            try:
                return cls(**base, **values)
            except ValueError as exc:
                bad = _failing_fields(exc)
                if set(bad or []) & caller:
                    raise
                dropped = [n for n in (bad or []) if n in values and n in optional]
                if not dropped or len(dropped) != len(set(bad or [])):
                    self.warn(
                        "malformed_entry",
                        f"a {cls.__name__} could not be built from the source and was not stored",
                        path=where,
                    )
                    return None
                for name in dropped:
                    del values[name]
                    self.warn(
                        "malformed_entry",
                        f"{cls.__name__}.{name} is not valid for the schema and was not stored",
                        path=where,
                    )
        return None


def _failing_fields(exc: ValueError) -> list[str] | None:
    errors = getattr(exc, "errors", None)
    if errors is None:
        return None
    try:
        located = errors(include_input=False, include_url=False)
    except TypeError:
        return None
    names: list[str] = []
    for item in located:
        loc = item.get("loc") or ()
        if not loc:
            return None
        names.append(str(loc[0]))
    return names


def read_voicera_export(
    text: str,
    *,
    created_at: str | datetime,
    privacy: PrivacyMarker | Mapping[str, Any],
    declared_language: LanguageDeclaration | Mapping[str, Any] | None = None,
    accept_warnings: bool = False,
) -> VoicEraRead:
    """Read decoded export text into a transcript and its turns.

    ``declared_language`` is the only source of ``Transcript.declared_language``. The caller's own
    arguments are validated first, whatever the export holds, with the schema's ``ValueError``.
    ``VoicEraRefusedError`` when a warning outside ``NON_REFUSING_WARNING_CODES`` was raised and
    ``accept_warnings`` is false, and always for a call id with a control or bidi character.
    """
    if not isinstance(text, str):
        raise TypeError("a VoicEra export is passed as decoded text")
    if type(accept_warnings) is not bool:
        raise TypeError("accept_warnings must be a bool")
    transcript_id = TranscriptId.new()
    # Validates created_at, privacy and declared_language before the export is looked at.
    arguments: dict[str, Any] = {
        "id": transcript_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "source": "agent_export",
        "privacy": privacy,
        "declared_language": declared_language,
    }
    Transcript(**arguments)
    reader = _Reader(created_at)
    lines = _lines(text)
    if all(line.strip() == "" for line in lines):
        reader.warn("empty_input", "the export has no line with content")
        return _finish(reader, None, [], accept_warnings)

    head: dict[str, Any] = {"source": "agent_export"}
    if declared_language is not None:
        head["declared_language"] = declared_language
    turns: list[Turn] = []
    header_ok = False
    for index, line in enumerate(lines):
        number = index + 1
        where = f"f8:{number}"
        if number == 1:
            found = _HEADER.fullmatch(line)
            if found is not None:
                header_ok = True
                _header(reader, head, found["value"])
                continue
            reader.warn(
                "header_not_recognized",
                "the first line is not a call_id header",
                path=where,
                raw=line,
            )
        elif number == 2:
            if line == "---":
                continue
            reader.warn(
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
                reader.warn("unparsed_line", "the line is not a message line", path=where, raw=line)
            continue
        turn = _turn(reader, transcript_id, where, message, len(turns))
        if turn is not None:
            turns.append(turn)

    transcript = None
    if turns or header_ok:
        transcript = reader.build(
            Transcript, {"privacy": privacy}, head, entity_id=transcript_id, where=None
        )
    return _finish(reader, transcript, turns, accept_warnings)


def _finish(
    reader: _Reader, transcript: Transcript | None, turns: list[Turn], accept_warnings: bool
) -> VoicEraRead:
    warnings = tuple(reader.warnings)
    refusing = [w for w in warnings if w.code not in NON_REFUSING_WARNING_CODES]
    if refusing and not accept_warnings:
        raise VoicEraRefusedError(_refusal_message(refusing), warnings)
    return VoicEraRead(
        transcript=transcript,
        turns=tuple(turns),
        warnings=warnings,
        external_refs=tuple(reader.external_refs),
    )


def _header(reader: _Reader, head: dict[str, Any], value: str) -> None:
    if _unsafe(value):
        # Never echoed: not the character, not any part of the identifier.
        warning = IngestWarning(
            code=_UNSAFE_CALL_ID,
            path=_CALL_ID_POINTER,
            message="the call id holds a control or bidirectional character and is refused",
        )
        raise VoicEraRefusedError(_refusal_message([warning]), (*reader.warnings, warning))
    if not _UUID.fullmatch(value):
        reader.warn(
            "call_id_not_uuid",
            "the call id is not shaped like a UUID and is kept as it is",
            path=_CALL_ID_POINTER,
            raw=value,
        )
    if reader.external_ref(value, _CALL_ID_POINTER):
        head["provider_call_ref"] = {"system": "voicera", "kind": "call_id", "value": value}


def _turn(
    reader: _Reader, transcript_id: Any, where: str, message: re.Match[str], seq: int
) -> Turn | None:
    speaker = message["speaker"]
    role = _ROLE.get(speaker)
    if role is None:
        reader.warn(
            "unknown_enum_value",
            "the speaker label is not assistant or user, so the line is not mapped",
            path=f"{where}/speaker",
            raw=speaker,
        )
        return None
    values: dict[str, Any] = {
        "seq": seq,
        "role": role,
        "source": "agent_log",
        "timing_source": "none",
        "text": reader.text(message["text"] or "", f"{where}/text"),
    }
    stamp = message["ts"]
    instant, code = _instant(stamp)
    if instant is not None:
        values["wall_time"] = {"at": instant, "meaning": "unknown"}
    if code is not None:
        reader.warn(code, _TIME_MESSAGES[code], path=f"{where}/timestamp", raw=stamp)
    built: Turn | None = reader.build(
        Turn,
        {"transcript_id": transcript_id},
        values,
        entity_id=TurnId.new(),
        where=where,
    )
    return built
