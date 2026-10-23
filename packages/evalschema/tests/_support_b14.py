# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 22 tests (F8, the VoicEra-style transcript export, P1-36).

The converter ``tests/support/mappings/voicera_transcript.py`` (``map_f8``) does not exist yet;
dev writes it. The pinned API, readings (P) and ambiguities are in
docs/phase1/api-surface-mappings-3.md.

What is here:

- loaders for the synthetic public twin under ``tests/fixtures/synthetic/voicera-twin-*/`` and the
  location of the local-only real file (``tests/fixtures/private/voicera/``; nothing in this
  module reads the real file's text except through the functions the local-only tests call, and
  none of them puts that text into a message or a return value);
- an independent reference reading of the documented F8 line structure (``reference_parse``);
- the F8 source-pointer resolver (``f8:<line>[/call_id|/timestamp|/speaker|/text]``) and the
  source-key universe for the coverage check;
- ``check_no_fabrication_f8``: the P-51 check for F8 (every leaf has a source and a table row, the
  derivation re-computes, and only the documented fields may be set at all);
- the structural checks for the local-only run, written so that no message holds file text
  (``structural_failures``), and the structure signature of a file;
- the n-gram scanner behind ``test_no_private_text_in_committable_files``.

Everything is ASCII; non-ASCII characters are built with chr() or read from fixture files.
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import subprocess
import unicodedata
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._support import FIXTURES, ROOT
from ._support_b6 import CREATED_AT, check_records_valid, leaves, mapping, privacy_synthetic

__all__ = [
    "ALLOWED_CONSTANTS",
    "ALLOWED_LEAVES",
    "CREATED_AT",
    "F8_DERIVATIONS",
    "MIN_QUOTED_LINE",
    "PRIVATE_DIR",
    "REQUIRED_GAP_ROWS",
    "SHINGLE_WORDS",
    "TWIN_DIR",
    "TWIN_FILE",
    "WARNING_CODES_F8",
    "Parsed",
    "ParsedLine",
    "SilenceReport",
    "check_no_fabrication_f8",
    "check_warnings_quote_sources_f8",
    "committable_files",
    "compare_signatures",
    "expected_instant",
    "f8_key",
    "f8_universe",
    "find_shared_shingles",
    "good_result",
    "lines_of",
    "private_files",
    "private_shingles",
    "reference_parse",
    "resolve_f8",
    "run_f8",
    "silence_report",
    "stable",
    "structural_failures",
    "structure_signature",
    "twin_text",
    "utc_text",
    "word_tokens",
]

TWIN_DIR = FIXTURES / "synthetic" / "voicera-twin-2026-10-24"
TWIN_FILE = TWIN_DIR / "call_2026-10-24_11-01-07_c0ffee01.txt"
PRIVATE_DIR = FIXTURES / "private" / "voicera"

SHINGLE_WORDS = 8
# A fixed warning sentence may name the format (for example ``call_id=``); only a line at least this
# long counts as text of the file when checking that a message does not quote it.
MIN_QUOTED_LINE = 12

# The closed set of warning codes of the F8 mapping (api-surface-mappings-3.md section 4).
WARNING_CODES_F8: frozenset[str] = frozenset(
    {
        "empty_input",
        "header_not_recognized",
        "call_id_not_uuid",
        "delimiter_not_found",
        "unparsed_line",
        "unknown_enum_value",
        "timestamp_not_parseable",
        "naive_timestamp",
        "time_precision_reduced",
        "text_not_valid_unicode",
    }
)

# Derivation names F8 may use (the closed set of batch 6, narrowed) and the constants it may fix.
F8_DERIVATIONS: frozenset[str] = frozenset(
    {"order_index", "iso_offset_to_utc_us", "lone_surrogate_replaced"}
)
ALLOWED_CONSTANTS: dict[tuple[str, str], frozenset[str]] = {
    ("Transcript", "/source"): frozenset({"agent_export"}),
    ("Transcript", "/provider_call_ref/system"): frozenset({"voicera"}),
    ("Transcript", "/provider_call_ref/kind"): frozenset({"call_id"}),
    ("Turn", "/role"): frozenset({"agent", "caller"}),
    ("Turn", "/source"): frozenset({"agent_log"}),
    ("Turn", "/timing_source"): frozenset({"none"}),
    ("Turn", "/wall_time/meaning"): frozenset({"unknown"}),
}
# The only leaves an F8 record may carry (apart from the framework fields). A leaf outside this
# list is a fabricated value even if a trace entry claims a source for it.
ALLOWED_LEAVES: dict[str, frozenset[str]] = {
    "Transcript": frozenset(
        {
            "/source",
            "/provider_call_ref/system",
            "/provider_call_ref/kind",
            "/provider_call_ref/value",
        }
    ),
    "Turn": frozenset(
        {
            "/seq",
            "/role",
            "/source",
            "/timing_source",
            "/text",
            "/wall_time/at",
            "/wall_time/meaning",
        }
    ),
}
_FRAMEWORK = (
    "/id",
    "/schema_version",
    "/created_at",
    "/metadata",
    "/metadata_flags",
    "/transcript_id",
    "/privacy",
    "/contains_bidi_controls",
)
_ROLE_OF = {"assistant": "agent", "user": "caller"}

# Gap rows the static register must hold for F8 (api-surface-mappings-3.md section 5):
# key -> (kind, owner).
REQUIRED_GAP_ROWS: dict[str, tuple[str, str]] = {
    "F8:assistant.text": ("lossy", "schema-architect"),
    "F8:@turn_timing": ("absent_in_source", "schema-architect"),
    "F8:@language_tag": ("absent_in_source", "indic-linguistics"),
    "F8:@audio": ("absent_in_source", "schema-architect"),
    "F8:@confidence": ("absent_in_source", "schema-architect"),
    "F8:@tool_calls": ("absent_in_source", "schema-architect"),
    "F8:@end_of_call_reason": ("absent_in_source", "schema-architect"),
    "F8:@filename": ("unmapped", "dev"),
}


# ------------------------------------------------------------------------------ fixtures


def twin_text() -> str:
    """The public twin, decoded as UTF-8 with the newlines exactly as stored."""
    return TWIN_FILE.read_bytes().decode("utf-8")


def private_files() -> list[Path]:
    """The files placed under ``private/voicera/`` (names only are listed; nothing is read).
    Empty when the owner has not placed a file, which is the state of a CI checkout."""
    if not PRIVATE_DIR.is_dir():
        return []
    return sorted(p for p in PRIVATE_DIR.iterdir() if p.is_file() and not p.name.startswith("."))


def run_f8(text: str, **over: Any) -> Any:
    kwargs: dict[str, Any] = {"created_at": CREATED_AT, "privacy": privacy_synthetic()}
    kwargs.update(over)
    return mapping("voicera_transcript").map_f8(text, **kwargs)


def stable(result: Any) -> list[Any]:
    """The records without identity, creation time or link ids, for equality across two runs."""
    skip = {"id", "created_at", "transcript_id"}
    return [
        (type(r).__name__, {k: v for k, v in r.model_dump(mode="json").items() if k not in skip})
        for r in result.records
    ]


# ------------------------------------------------------------------------------ the format

_HEADER = re.compile(r"call_id=(?P<value>.+)")
_UUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
_MESSAGE = re.compile(r"\[(?P<ts>[^\]]*)\] (?P<speaker>[^\s:]+):(?: (?P<text>.*))?")
_INSTANT = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.(?P<frac>\d+))?(?P<offset>Z|[+-]\d{2}:\d{2})?"
)
_SURROGATES = re.compile("[\ud800-\udfff]")


def lines_of(text: str) -> list[str]:
    """The lines of an F8 file as the pointers number them: split at LF only (a U+2028 or U+0085
    inside a message is text, not a line end), one trailing CR removed from each line, the final
    empty piece after a closing LF dropped, one leading U+FEFF dropped from line 1."""
    pieces = text.split("\n")
    if pieces and pieces[-1] == "":
        pieces.pop()
    out = [piece[:-1] if piece.endswith("\r") else piece for piece in pieces]
    if out and out[0].startswith("﻿"):
        out[0] = out[0][1:]
    return out


@dataclass(frozen=True)
class ParsedLine:
    number: int  # 1-based, the number in the source pointer
    timestamp: str
    speaker: str
    text: str


@dataclass(frozen=True)
class Parsed:
    call_id: str | None  # the text after "call_id=" on line 1
    delimiter_ok: bool
    messages: tuple[ParsedLine, ...]
    unparsed: tuple[
        int, ...
    ]  # numbers of non-blank lines that are not a header, delimiter or message
    header_ok: bool


def reference_parse(text: str) -> Parsed:
    """The documented structure, read independently of the converter (api-surface-mappings-3.md
    section 2). A line 1 that is a message is a message (and the header is missing); same for
    line 2. Blank lines are ignored."""
    lines = lines_of(text)
    call_id: str | None = None
    header_ok = False
    delimiter_ok = False
    messages: list[ParsedLine] = []
    unparsed: list[int] = []
    for index, line in enumerate(lines):
        number = index + 1
        if number == 1:
            found = _HEADER.fullmatch(line)
            if found is not None:
                call_id, header_ok = found.group("value"), True
                continue
        if number == 2 and line == "---":
            delimiter_ok = True
            continue
        if line.strip() == "":
            continue
        message = _MESSAGE.fullmatch(line)
        if message is not None:
            messages.append(
                ParsedLine(
                    number,
                    message.group("ts"),
                    message.group("speaker"),
                    message.group("text") or "",
                )
            )
        elif number > 2:
            unparsed.append(number)
    return Parsed(call_id, delimiter_ok, tuple(messages), tuple(unparsed), header_ok)


def utc_text(moment: datetime) -> str:
    """A UTC datetime as the canonical text with six fractional digits and ``Z``."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def expected_instant(stamp: str) -> tuple[str | None, str | None]:
    """(the UTC text with six fractional digits, or ``None``; the warning code that belongs to
    this timestamp, or ``None``). ``timestamp_not_parseable`` and ``naive_timestamp`` give no
    instant; ``time_precision_reduced`` gives the truncated instant."""
    found = _INSTANT.fullmatch(stamp)
    if found is None:
        return None, "timestamp_not_parseable"
    if found.group("offset") is None:
        return None, "naive_timestamp"
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None, "timestamp_not_parseable"
    text = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    fraction = found.group("frac")
    return text, ("time_precision_reduced" if fraction and len(fraction) > 6 else None)


# ------------------------------------------------------------------------------ pointers


def resolve_f8(text: str, pointer: str) -> str:
    """Resolve an F8 source pointer: ``f8:<n>`` is the whole line n (1-based); ``f8:1/call_id``,
    ``f8:<n>/timestamp``, ``/speaker`` and ``/text`` are the parts of a header or message line.
    ``AssertionError`` when the pointer does not name something in the file."""
    assert pointer.startswith("f8:"), f"not an F8 pointer: {pointer!r}"
    number_text, _, part = pointer[3:].partition("/")
    assert number_text.isdigit(), f"not an F8 pointer: {pointer!r}"
    number = int(number_text)
    lines = lines_of(text)
    assert 1 <= number <= len(lines), f"line {number} does not exist"
    line = lines[number - 1]
    if part == "":
        return line
    if part == "call_id":
        found = _HEADER.fullmatch(line)
        assert number == 1
        assert found is not None, f"line {number} is not the header"
        return found.group("value")
    message = _MESSAGE.fullmatch(line)
    assert message is not None, f"line {number} is not a message"
    if part == "timestamp":
        return message.group("ts")
    if part == "speaker":
        return message.group("speaker")
    if part == "text":
        return message.group("text") or ""
    raise AssertionError(f"unknown F8 pointer part {part!r}")


def f8_key(pointer: str) -> str | None:
    """The logical source key of a pointer, for the coverage check. A pointer to a whole line
    that is not the delimiter has no key (it is a warning location)."""
    if not pointer.startswith("f8:"):
        return None
    number_text, _, part = pointer[3:].partition("/")
    if part == "call_id":
        return "header.call_id"
    if part in ("timestamp", "speaker", "text"):
        return f"message.{part}"
    if number_text == "2" and part == "":
        return "delimiter"
    return None


def f8_universe(text: str) -> set[str]:
    """The source keys that exist in this file: ``header.call_id`` if line 1 is a header,
    ``delimiter`` if line 2 is ``---``, ``message.timestamp|speaker|text`` if any message."""
    parsed = reference_parse(text)
    keys: set[str] = set()
    if parsed.header_ok:
        keys.add("header.call_id")
    if parsed.delimiter_ok:
        keys.add("delimiter")
    if parsed.messages:
        keys |= {"message.timestamp", "message.speaker", "message.text"}
    return keys


# ------------------------------------------------------------------------------ the checks


def _target_pattern(record: Any, pointer: str) -> str:
    tokens = ["*" if token.isdigit() else token for token in pointer[1:].split("/")]
    return type(record).__name__ + "." + ".".join(tokens)


def _is_framework(pointer: str) -> bool:
    return any(pointer == p or pointer.startswith(p + "/") for p in _FRAMEWORK)


def check_no_fabrication_f8(
    result: Any,
    text: str,
    module: Any,
    *,
    allow_declared_language: bool = False,
    show_values: bool = True,
) -> None:
    """The P-51 check (ADR 0018 Decision 2, gate item 13) for one F8 run. ``AssertionError``
    naming the record, the field and why. With ``show_values=False`` no message holds a value
    of the file (the local-only run uses it).

    For every non-null leaf of every record: it is a documented F8 leaf (``ALLOWED_LEAVES``); it
    has a trace entry whose sources resolve in the file; a verbatim copy equals its single
    source; a derivation re-computes (``iso_offset_to_utc_us``, ``lone_surrogate_replaced``,
    ``order_index`` = the 0-based count of earlier turns, ``constant:<v>`` with ``v`` allowed for
    that field and, for a role, the speaker label that the documentation maps); the converter's
    ``PROVENANCE_TABLE`` has a row for the target and the derivation. Leaves of
    ``declared_language`` come from the caller's declaration and are allowed only when the
    caller passed one.
    """
    rows = list(module.PROVENANCE_TABLE)
    by_key = {(e.record, e.field): e for e in result.trace}
    seq_expected = 0
    for record in result.records:
        key = str(record.id)
        kind = type(record).__name__
        dump = record.model_dump(mode="json", exclude_none=True)
        for pointer, target in leaves(dump):
            if _is_framework(pointer):
                continue
            where = f"{kind} {pointer}"
            shown = f" = {target!r}" if show_values else ""
            if pointer.startswith("/declared_language"):
                assert allow_declared_language, f"{where}: declared_language needs a declaration"
                assert (key, pointer) not in by_key, f"{where}: a declaration is not traced"
                continue
            assert pointer in ALLOWED_LEAVES.get(kind, frozenset()), (
                f"{where}{shown} is not a documented F8 field (fabricated)"
            )
            entry = by_key.get((key, pointer))
            assert entry is not None, f"{where}{shown} has no source (fabricated)"
            sources = tuple(resolve_f8(text, p) for p in entry.sources)
            derivation = entry.derivation
            if derivation is None:
                assert len(sources) == 1, f"{where}: a verbatim copy has exactly one source"
                same = sources[0] == target and type(sources[0]) is type(target)
                assert same, f"{where}: the value is not the source value"
            elif derivation == "iso_offset_to_utc_us":
                assert len(sources) == 1, f"{where}: one source timestamp"
                assert expected_instant(sources[0])[0] == target, (
                    f"{where}: the instant is not what {derivation} gives from the source"
                )
            elif derivation == "lone_surrogate_replaced":
                assert len(sources) == 1, f"{where}: one source text"
                assert _SURROGATES.sub("�", sources[0]) == target, (
                    f"{where}: not the source text with U+FFFD for lone surrogates"
                )
            elif derivation == "order_index":
                assert target == seq_expected, f"{where}: seq is not the 0-based turn count"
            elif derivation.startswith("constant:"):
                value = derivation.removeprefix("constant:")
                allowed = ALLOWED_CONSTANTS.get((kind, pointer), frozenset())
                assert value in allowed, f"{where}: constant {value!r} is not allowed here"
                assert str(target) == value, f"{where}: the value is not the constant"
                if (kind, pointer) == ("Turn", "/role"):
                    label = resolve_f8(text, entry.sources[0]) if entry.sources else ""
                    assert _ROLE_OF.get(label) == target, (
                        f"{where}: the role is not the one the documentation gives its label"
                    )
            else:
                raise AssertionError(f"{where}: derivation {derivation!r} is not an F8 derivation")
            row = next(
                (
                    r
                    for r in rows
                    if r.target == _target_pattern(record, pointer)
                    and r.kind != "absent"
                    and r.derivation == derivation
                ),
                None,
            )
            assert row is not None, (
                f"{where}: no provenance-table row for {_target_pattern(record, pointer)} "
                f"with derivation {derivation}"
            )
        if kind == "Turn":
            seq_expected += 1
    known = {str(r.id) for r in result.records}
    stray = [e for e in result.trace if e.record not in known]
    assert not stray, f"{len(stray)} trace entries for records that do not exist"


def check_warnings_quote_sources_f8(result: Any, text: str, *, show_values: bool = True) -> None:
    """A warning's ``path`` resolves in the file, its ``raw`` is the source value as sent (a lone
    surrogate shown as U+FFFD, N82), its code is in the closed set, and its ``message`` quotes no
    text of the file."""
    sources = [line for line in lines_of(text) if len(line.strip()) >= MIN_QUOTED_LINE]
    for warning in result.warnings:
        assert warning.code in WARNING_CODES_F8, f"warning code {warning.code!r} is not pinned"
        if warning.path is not None:
            try:
                source = resolve_f8(text, warning.path)
            except AssertionError:
                raise AssertionError(
                    f"warning {warning.code}: path does not resolve in the file"
                ) from None
            if warning.raw is not None:
                shown = _SURROGATES.sub("�", source)
                ok = warning.raw == shown
                detail = f": raw {warning.raw!r} is not {shown!r}" if show_values else ""
                assert ok, (
                    f"warning {warning.code} at {warning.path}: raw is not the source{detail}"
                )
        leaked = [line for line in sources if line in warning.message]
        assert not leaked, f"warning {warning.code}: the message quotes file text"


# ----------------------------------------------------------------------- a hand-built good result


def good_result(
    text: str, *, language: bool = False, extra_field: tuple[str, Any] | None = None
) -> Any:
    """A correct F8 ``MappingResult`` for ``text`` built by hand with the shared recorder (the
    first parsable messages only), independent of ``voicera_transcript.py``, so the checkers can
    be tested before the converter exists. ``language=True`` sets a language on every turn
    (fabricated: the checker must refuse it); ``extra_field`` sets one more field on the first
    turn (also to be refused)."""
    common = mapping("common")
    from tracer_evalschema.transcript import Transcript, Turn

    parsed = reference_parse(text)
    rec = common.Recorder("F8", CREATED_AT, privacy_synthetic())
    transcript_id = rec.new_id(Transcript)
    draft = common.Draft(Transcript, {"privacy": privacy_synthetic()})
    draft.set("source", "agent_export", common.Leg("/source", (), "constant:agent_export"))
    if parsed.call_id is not None:
        ref = {"system": "voicera", "kind": "call_id", "value": parsed.call_id}
        draft.set(
            "provider_call_ref",
            ref,
            common.Leg("/provider_call_ref/system", ("f8:1/call_id",), "constant:voicera"),
            common.Leg("/provider_call_ref/kind", ("f8:1/call_id",), "constant:call_id"),
            common.Leg("/provider_call_ref/value", ("f8:1/call_id",)),
        )
    rec.commit(draft, entity_id=transcript_id)
    seq = 0
    for message in parsed.messages:
        if message.speaker not in _ROLE_OF:
            continue
        where = f"f8:{message.number}"
        role = _ROLE_OF[message.speaker]
        turn = common.Draft(Turn, {"transcript_id": transcript_id})
        turn.set("seq", seq, common.Leg("/seq", (where + "/speaker",), "order_index"))
        turn.set("role", role, common.Leg("/role", (where + "/speaker",), f"constant:{role}"))
        turn.set(
            "source",
            "agent_log",
            common.Leg("/source", (where + "/speaker",), "constant:agent_log"),
        )
        turn.set(
            "timing_source",
            "none",
            common.Leg("/timing_source", (where + "/speaker",), "constant:none"),
        )
        turn.set("text", message.text, common.Leg("/text", (where + "/text",)))
        instant, _ = expected_instant(message.timestamp)
        if instant is not None:
            turn.set(
                "wall_time",
                {"at": instant, "meaning": "unknown"},
                common.Leg("/wall_time/at", (where + "/timestamp",), "iso_offset_to_utc_us"),
                common.Leg("/wall_time/meaning", (where + "/timestamp",), "constant:unknown"),
            )
        if language:
            turn.set("language", "mr-IN", common.Leg("/language", (where + "/text",)))
        if extra_field is not None and seq == 0:
            turn.set(extra_field[0], extra_field[1], common.Leg("/" + extra_field[0], (where,)))
        rec.commit(turn, where=where)
        seq += 1
    rec.external_ref("voicera", "call_id", parsed.call_id or "", "f8:1/call_id")
    rec.consume("f8:2")
    return rec.result()


def table_for_good_result() -> Any:
    """A stand-in converter module (the table that ``good_result`` conforms to)."""
    common = mapping("common")

    class _Module:
        PROVENANCE_TABLE = (
            common.TableRow(
                "Transcript.source", "constant", None, "constant:agent_export", None, ""
            ),
            common.TableRow(
                "Transcript.provider_call_ref.system",
                "constant",
                "header.call_id",
                "constant:voicera",
                None,
                "",
            ),
            common.TableRow(
                "Transcript.provider_call_ref.kind",
                "constant",
                "header.call_id",
                "constant:call_id",
                None,
                "",
            ),
            common.TableRow(
                "Transcript.provider_call_ref.value", "source", "header.call_id", None, None, ""
            ),
            common.TableRow("Turn.seq", "derivation", "message.speaker", "order_index", None, ""),
            common.TableRow("Turn.role", "constant", "message.speaker", "constant:agent", None, ""),
            common.TableRow(
                "Turn.role", "constant", "message.speaker", "constant:caller", None, ""
            ),
            common.TableRow(
                "Turn.source", "constant", "message.speaker", "constant:agent_log", None, ""
            ),
            common.TableRow(
                "Turn.timing_source", "constant", "message.speaker", "constant:none", None, ""
            ),
            common.TableRow("Turn.text", "source", "message.text", None, None, ""),
            common.TableRow(
                "Turn.wall_time.at",
                "derivation",
                "message.timestamp",
                "iso_offset_to_utc_us",
                None,
                "",
            ),
            common.TableRow(
                "Turn.wall_time.meaning",
                "constant",
                "message.timestamp",
                "constant:unknown",
                None,
                "",
            ),
        )

    return _Module


# ------------------------------------------------------------------------------ structure


def structural_failures(text: str, result: Any, module: Any) -> list[str]:
    """Structural checks of one mapping run over one file, for the local-only run on the real
    file. Returns failure keys: names, indices and counts only, never a value of the file. The
    same function runs in CI on the twin and on deliberately broken results, and
    ``test_f8_structural_checks_leak_no_file_text`` proves that no failure key holds file text."""
    failures: list[str] = []
    parsed = reference_parse(text)
    known = [m for m in parsed.messages if m.speaker in _ROLE_OF]
    unknown = [m for m in parsed.messages if m.speaker not in _ROLE_OF]
    turns = list(result.turns)
    kinds = sorted({type(r).__name__ for r in result.records})
    if kinds not in ([], ["Transcript"], ["Transcript", "Turn"]):
        failures.append(f"record_kinds_unexpected:{','.join(kinds)}")
    if len(result.transcripts) != 1 and (turns or parsed.header_ok):
        failures.append(f"transcript_count:{len(result.transcripts)}")
    if len(turns) != len(known):
        failures.append(f"turn_count:expected={len(known)},got={len(turns)}")
    codes = [w.code for w in result.warnings]
    for code, expected in (
        ("unparsed_line", len(parsed.unparsed)),
        ("unknown_enum_value", len(unknown)),
    ):
        if codes.count(code) > expected:
            failures.append(f"warning_count_too_high:{code}")
    outside = sorted(set(codes) - WARNING_CODES_F8)
    if outside:
        failures.append(f"warning_codes_outside_the_pinned_set:{len(outside)}")
    ordered = sorted(turns, key=lambda t: t.seq)
    if [t.seq for t in ordered] != list(range(len(ordered))):
        failures.append("seq_not_consecutive_from_zero")
    for index, (turn, message) in enumerate(zip(ordered, known, strict=False)):
        if turn.role != _ROLE_OF[message.speaker]:
            failures.append(f"role_differs_at_turn:{index}")
        if turn.text != _SURROGATES.sub("�", message.text):
            failures.append(f"text_differs_at_turn:{index}")
        if (turn.source, turn.timing_source) != ("agent_log", "none"):
            failures.append(f"source_or_timing_differs_at_turn:{index}")
        if any(
            getattr(turn, name) is not None
            for name in (
                "start_ms",
                "end_ms",
                "segments",
                "language",
                "language_raw",
                "interrupted",
                "exchange_index",
            )
        ):
            failures.append(f"field_that_the_file_cannot_hold_is_set_at_turn:{index}")
        instant, _ = expected_instant(message.timestamp)
        stamp = turn.wall_time
        if instant is None:
            if stamp is not None:
                failures.append(f"wall_time_without_instant_at_turn:{index}")
        elif stamp is None or utc_text(stamp.at) != instant:
            failures.append(f"wall_time_differs_at_turn:{index}")
        elif stamp.meaning != "unknown":
            failures.append(f"wall_time_meaning_differs_at_turn:{index}")
    if result.transcripts:
        transcript = result.transcripts[0]
        if transcript.source != "agent_export":
            failures.append("transcript_source")
        for name in ("detected_language", "detected_language_raw", "language_confidence"):
            if getattr(transcript, name) is not None:
                failures.append(f"transcript_field_set:{name}")
        if transcript.timeline_origin is not None or transcript.provider is not None:
            failures.append("transcript_field_set:origin_or_provider")
        ref = transcript.provider_call_ref
        if parsed.call_id is None:
            if ref is not None:
                failures.append("call_id_ref_without_header")
        elif ref is None or ref.value != parsed.call_id:
            failures.append("call_id_ref_differs")
    checks: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("records_valid", lambda: check_records_valid(result)),
        (
            "no_fabrication",
            lambda: check_no_fabrication_f8(result, text, module, show_values=False),
        ),
        (
            "warnings_quote_sources",
            lambda: check_warnings_quote_sources_f8(result, text, show_values=False),
        ),
    )
    for label, step in checks:
        try:
            step()
        except AssertionError as exc:
            # the messages of the F8 checks carry no value; the shared checker's might, so only
            # the kind is kept for it
            failures.append(
                f"{label}_failed:{exc}" if label != "records_valid" else f"{label}_failed"
            )
        except Exception as exc:  # report the class only, never the message
            failures.append(f"{label}_raised:{type(exc).__name__}")
    return failures


def _stamp_shape(stamp: str) -> str:
    """The digit-pattern shape of a timestamp: digits become 9, a trailing zone is kept as is."""
    zone = re.search(r"(Z|[+-]\d\d:\d\d)$", stamp)
    head = stamp[: zone.start()] if zone else stamp
    return re.sub(r"\d", "9", head) + (zone.group(0) if zone else "")


def structure_signature(text: str) -> dict[str, Any]:
    """Content-free features of an F8 file: line endings, BOM, final newline, the digit-pattern
    shape of the header id and of each timestamp, the separators around the speaker label, the
    speaker labels, whether every non-blank line after the delimiter is a message. Used to
    compare the twin with the real file (``test_f8_twin_matches_real_structure``)."""
    raw_lines = text.split("\n")
    crlf = sum(1 for line in raw_lines[:-1] if line.endswith("\r"))
    parsed = reference_parse(text)
    shapes = {_stamp_shape(m.timestamp) for m in parsed.messages}
    id_shape = None
    if parsed.call_id is not None:
        case = (
            "upper"
            if re.search("[A-F]", parsed.call_id) and not re.search("[a-f]", parsed.call_id)
            else "lower"
            if re.search("[a-f]", parsed.call_id) and not re.search("[A-F]", parsed.call_id)
            else "mixed-or-digits"
        )
        id_shape = re.sub(r"[0-9a-fA-F]", "x", parsed.call_id) + "|" + case
    lines = lines_of(text)
    separators = sorted(
        {
            ("] " if "] " in line else "]?") + (": " if ": " in line else ":?")
            for line in lines[2:]
            if _MESSAGE.fullmatch(line)
        }
    )
    return {
        "bom": text.startswith("﻿"),
        "line_ending": "none" if crlf == 0 else ("crlf" if crlf == len(raw_lines) - 1 else "mixed"),
        "final_newline": text.endswith("\n"),
        "header": parsed.header_ok,
        "header_id_shape": id_shape,
        "delimiter": parsed.delimiter_ok,
        "timestamp_shapes": sorted(shapes),
        "speaker_labels": sorted({m.speaker for m in parsed.messages}),
        "separators": separators,
        "unparsed_lines": len(parsed.unparsed),
        "blank_lines": sum(1 for line in lines if line.strip() == ""),
        "has_messages": bool(parsed.messages),
    }


def compare_signatures(twin: Mapping[str, Any], real: Mapping[str, Any]) -> list[str]:
    """The names of the features in which the real file's structure differs from the twin's.
    The speaker labels of the real file must be among the twin's (the twin has both documented
    labels); every other feature must be equal, except ``blank_lines`` and ``unparsed_lines``,
    which must be zero in the real file (the documentation says one line per message)."""
    differing: list[str] = []
    for name in (
        "bom",
        "line_ending",
        "final_newline",
        "header",
        "header_id_shape",
        "delimiter",
        "timestamp_shapes",
        "separators",
        "has_messages",
    ):
        if twin[name] != real[name]:
            differing.append(name)
    if not set(real["speaker_labels"]) <= set(twin["speaker_labels"]):
        differing.append("speaker_labels")
    for name in ("unparsed_lines", "blank_lines"):
        if real[name] != 0:
            differing.append(name)
    return differing


# ------------------------------------------------------------------------------ silence


@dataclass(frozen=True)
class SilenceReport:
    """What a call wrote to the places that logs live: counts only."""

    stdout_chars: int
    stderr_chars: int
    log_records: int
    python_warnings: int

    @property
    def silent(self) -> bool:
        return not (
            self.stdout_chars or self.stderr_chars or self.log_records or self.python_warnings
        )


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=0)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def silence_report(call: Callable[[], Any]) -> tuple[SilenceReport, Any]:
    """Run ``call`` and report whether it printed, logged (any logger, any level) or raised a
    Python warning. Returns the report and the call's result."""
    out, err = io.StringIO(), io.StringIO()
    handler = _Collect()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(0)
    try:
        with (
            warnings.catch_warnings(record=True) as caught,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            warnings.simplefilter("always")
            result = call()
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    return SilenceReport(
        len(out.getvalue()), len(err.getvalue()), handler.count, len(caught)
    ), result


# ------------------------------------------------------------------------------ n-gram scanner

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})(?:\\u([dD][c-fC-F][0-9a-fA-F]{2}))?")
MAX_SCANNED_BYTES = 20_000_000


def _unescape_u(match: re.Match[str]) -> str:
    first = int(match.group(1), 16)
    second = match.group(2)
    if second is not None and 0xD800 <= first <= 0xDBFF:
        return chr(0x10000 + ((first - 0xD800) << 10) + (int(second, 16) - 0xDC00))
    if 0xD800 <= first <= 0xDFFF:
        return match.group(0)
    text = chr(first)
    return text + (("\\u" + second) if second else "")


def word_tokens(text: str) -> list[str]:
    """Words for the n-gram comparison: JSON ``\\uXXXX`` escapes decoded, NFC, zero-width
    characters and edge punctuation removed, case-folded. Splitting is on white space."""
    if "\\u" in text:
        text = _ESCAPE.sub(_unescape_u, text)
    tokens: list[str] = []
    for word in unicodedata.normalize("NFC", text).split():
        word = word.translate(_ZERO_WIDTH)
        start, end = 0, len(word)
        while start < end and unicodedata.category(word[start]).startswith("P"):
            start += 1
        while end > start and unicodedata.category(word[end - 1]).startswith("P"):
            end -= 1
        word = word[start:end].casefold()
        if word:
            tokens.append(word)
    return tokens


def _windows(tokens: Sequence[str], size: int) -> Iterator[str]:
    for start in range(len(tokens) - size + 1):
        yield " ".join(tokens[start : start + size])


def private_shingles(text: str, size: int = SHINGLE_WORDS) -> set[str]:
    """The word ``size``-grams of the message texts of an F8 file, taken over the continuous
    stream of the texts (so a window may span two lines). The header, the delimiter and the
    timestamp and speaker label of each line are not part of it; a line that is not a message
    contributes its whole text. Held in memory only; callers must never print it."""
    tokens: list[str] = []
    for index, line in enumerate(lines_of(text)):
        if index < 2 and (_HEADER.fullmatch(line) or line == "---"):
            continue
        message = _MESSAGE.fullmatch(line)
        tokens.extend(word_tokens(message.group("text") or "" if message else line))
    return set(_windows(tokens, size))


def committable_files(root: Path = ROOT) -> list[Path]:
    """Every file that git would commit: tracked or untracked and not ignored (``git ls-files
    -co --exclude-standard``), relative to ``root``, without anything under a ``fixtures/private/``
    folder (git ignores it already; this is a second guard). ``RuntimeError`` if git fails."""
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed with status {proc.returncode}")
    names = [n for n in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n]
    return sorted(Path(n) for n in names if "fixtures/private/" not in n and (root / n).is_file())


def find_shared_shingles(
    shingles: set[str], files: Iterable[Path], root: Path, size: int = SHINGLE_WORDS
) -> tuple[dict[str, int], int]:
    """(path -> number of windows of that file found in ``shingles``, number of files skipped
    because they are not UTF-8 text or are too large). Paths are relative strings; nothing of any
    file's text is returned."""
    hits: dict[str, int] = {}
    skipped = 0
    for relative in files:
        path = root / relative
        try:
            if path.stat().st_size > MAX_SCANNED_BYTES:
                skipped += 1
                continue
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue
        count = sum(1 for window in _windows(word_tokens(text), size) if window in shingles)
        if count:
            hits[relative.as_posix()] = count
    return hits, skipped
