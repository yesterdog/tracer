# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The ``tracer.flags/1`` document: built from the parts of a scoring pass and validated.

ADR 0023 rule 12 and the section "The tracer.flags/1 format". The document holds integers,
booleans, canonical decimal strings and short machine keys, never a text or an identifier. The
structural check is written by hand over the schema that ``tracer_evalcore.flags`` ships
(``jsonschema`` is not a runtime dependency; the patterns are read from that schema so the two
cannot drift). It is stricter than JSON Schema in one way: an integer is a Python ``int``, so
``9.0`` and ``True`` are refused. Every cross-field rule is a refusal with one stable code; the
message is the code and never holds a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from tracer_evalcore.align import Alignment
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.flags import (
    FLAGS_FORMAT,
    FLAGS_JSON_SCHEMA,
    SIGNAL_NAME,
    Confidence,
    SpanFlag,
    format_threshold,
)
from tracer_evalcore.tokens import TokenSequence

__all__ = [
    "FLAGS_METADATA_KEY",
    "FlagsDocumentError",
    "build_flags_document",
    "validate_flags_document",
]

FLAGS_METADATA_KEY = "tracer.flags"

_MAX = 9007199254740991
_PROPERTIES: Mapping[str, Any] = FLAGS_JSON_SCHEMA["properties"]
_SPAN_SCHEMA: Mapping[str, Any] = _PROPERTIES["spans"]["items"]
_NORMALISER = re.compile(_PROPERTIES["normaliser"]["pattern"])
_STT_NAME = re.compile(_PROPERTIES["stt"]["properties"]["provider"]["pattern"])
_THRESHOLD = re.compile(_PROPERTIES["flag_min_confidence"]["pattern"])
_CONFIDENCE = re.compile(_SPAN_SCHEMA["properties"]["confidence"]["pattern"])
_TOP = frozenset(FLAGS_JSON_SCHEMA["required"])
_STT = frozenset(_PROPERTIES["stt"]["required"])
_ALIGNMENT = frozenset(_PROPERTIES["alignment"]["required"])
_TOKENS = frozenset(_PROPERTIES["tokens"]["required"])
_SPAN = frozenset(_SPAN_SCHEMA["required"])
_AUDIO = frozenset({"audio_start_ms", "audio_end_ms"})
_SPAN_INTEGERS = _SPAN - {
    "confidence",
    "flagged",
    "near_chunk_boundary",
    "edit_distance",
    "key_length",
}


class FlagsDocumentError(InputRefusedError):
    """A flags document was refused. ``code`` is the stable reason; the message is the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def build_flags_document(
    *,
    normaliser: str,
    provider: str,
    model: str,
    threshold: Decimal,
    alignment: Alignment,
    export: TokenSequence,
    stt: TokenSequence,
    flags: Sequence[SpanFlag],
) -> dict[str, Any]:
    """The document of one scoring pass. Every span is recorded, flagged or not, in span order."""
    spans: list[dict[str, Any]] = []
    for flag in flags:
        span = flag.span
        record: dict[str, Any] = {
            "turn_seq": span.turn_seq,
            "start": span.start,
            "end": span.end,
            "export_tokens": span.export_tokens,
            "stt_token_start": span.stt_token_start,
            "stt_token_end": span.stt_token_end,
            "substitutions": span.substitutions,
            "deletions": span.deletions,
            "insertions": span.insertions,
            "edit_distance": flag.edit_distance,
            "key_length": flag.key_length,
            "confidence": flag.confidence,
            "flagged": flag.flagged,
            "near_chunk_boundary": flag.near_chunk_boundary,
        }
        if span.audio_start_ms is not None and span.audio_end_ms is not None:
            record["audio_start_ms"] = span.audio_start_ms
            record["audio_end_ms"] = span.audio_end_ms
        spans.append(record)
    return {
        "format": FLAGS_FORMAT,
        "signal": SIGNAL_NAME,
        "normaliser": normaliser,
        "stt": {"provider": provider, "model": model, "model_source": "requested"},
        "flag_min_confidence": format_threshold(threshold),
        "alignment": {
            "unit": "word",
            "hits": alignment.hits,
            "substitutions": alignment.substitutions,
            "deletions": alignment.deletions,
            "insertions": alignment.insertions,
        },
        "tokens": {
            "export": len(export.tokens),
            "stt": len(stt.tokens),
            "flagged_export": sum(f.span.export_tokens for f in flags if f.flagged),
        },
        "spans": spans,
    }


# ------------------------------------------------------------------------------ structure


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= _MAX


def _text(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _mapping(value: object, members: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != members:
        raise FlagsDocumentError("flags_shape")
    return value


def _check_span_shape(span: object) -> None:
    if not isinstance(span, Mapping):
        raise FlagsDocumentError("flags_shape")
    members = set(span)
    if not _SPAN <= members <= _SPAN | _AUDIO or len(members & _AUDIO) == 1:
        raise FlagsDocumentError("flags_shape")
    if not all(_integer(span[name]) for name in _SPAN_INTEGERS | (members & _AUDIO)):
        raise FlagsDocumentError("flags_shape")
    if not _integer(span["edit_distance"], 1) or not _integer(span["key_length"], 1):
        raise FlagsDocumentError("flags_shape")
    if not _text(span["confidence"], _CONFIDENCE):
        raise FlagsDocumentError("flags_shape")
    if type(span["flagged"]) is not bool or type(span["near_chunk_boundary"]) is not bool:
        raise FlagsDocumentError("flags_shape")


def _check_shape(document: Mapping[str, Any]) -> None:
    _mapping(document, _TOP)
    if document["format"] != FLAGS_FORMAT or document["signal"] != SIGNAL_NAME:
        raise FlagsDocumentError("flags_shape")
    if not _text(document["normaliser"], _NORMALISER):
        raise FlagsDocumentError("flags_shape")
    stt = _mapping(document["stt"], _STT)
    if stt["model_source"] != "requested" or not (
        _text(stt["provider"], _STT_NAME) and _text(stt["model"], _STT_NAME)
    ):
        raise FlagsDocumentError("flags_shape")
    if not _text(document["flag_min_confidence"], _THRESHOLD):
        raise FlagsDocumentError("flags_shape")
    alignment = _mapping(document["alignment"], _ALIGNMENT)
    if alignment["unit"] != "word" or not all(
        _integer(alignment[name]) for name in _ALIGNMENT - {"unit"}
    ):
        raise FlagsDocumentError("flags_shape")
    tokens = _mapping(document["tokens"], _TOKENS)
    if not (
        _integer(tokens["export"], 1)
        and _integer(tokens["stt"], 1)
        and _integer(tokens["flagged_export"])
    ):
        raise FlagsDocumentError("flags_shape")
    if not isinstance(document["spans"], list):
        raise FlagsDocumentError("flags_shape")
    for span in document["spans"]:
        _check_span_shape(span)


# ----------------------------------------------------------------------------- cross-field


def _check_span(span: Mapping[str, Any], threshold: Decimal) -> None:
    substitutions, deletions, insertions = (
        span["substitutions"],
        span["deletions"],
        span["insertions"],
    )
    width_is_zero = span["start"] == span["end"]
    if (
        span["start"] > span["end"]
        or span["export_tokens"] != substitutions + deletions
        or width_is_zero != (span["export_tokens"] == 0)
        or span["stt_token_end"] - span["stt_token_start"] != substitutions + insertions
        or span["edit_distance"] > span["key_length"]
    ):
        raise FlagsDocumentError("flags_span")
    confidence = Confidence(span["edit_distance"], span["key_length"])
    if span["confidence"] != confidence.display or span["flagged"] is not confidence.meets(
        threshold
    ):
        raise FlagsDocumentError("flags_span")
    if "audio_start_ms" in span and span["audio_start_ms"] > span["audio_end_ms"]:
        raise FlagsDocumentError("flags_span")


def _check_order(spans: Sequence[Mapping[str, Any]], stt_tokens: int) -> None:
    previous: tuple[int, int, int, int] | None = None
    last_end: dict[int, int] = {}
    stt_cursor = 0
    for span in spans:
        key = (span["turn_seq"], span["start"], span["end"], span["stt_token_start"])
        if previous is not None and key < previous:
            raise FlagsDocumentError("flags_order")
        previous = key
        if span["start"] != span["end"]:
            if span["start"] < last_end.get(span["turn_seq"], 0):
                raise FlagsDocumentError("flags_order")
            last_end[span["turn_seq"]] = span["end"]
        if span["stt_token_start"] < stt_cursor or span["stt_token_end"] > stt_tokens:
            raise FlagsDocumentError("flags_order")
        stt_cursor = span["stt_token_end"]


def _check_export(spans: Sequence[Mapping[str, Any]], export: TokenSequence) -> None:
    """Each span names an export turn, lies inside its text and sits on token edges."""
    texts = {turn.seq: turn.text for turn in export.turns}
    first = export.tokens[0] if export.tokens else None
    for span in spans:
        seq = span["turn_seq"]
        if seq not in texts or span["end"] > len(texts[seq]):
            raise FlagsDocumentError("flags_export_mismatch")
        in_turn = [t for t in export.tokens if t.turn_seq == seq]
        starts = {t.start for t in in_turn}
        ends = {t.end for t in in_turn}
        if span["start"] == span["end"]:
            anchors = set(ends)
            if first is not None and first.turn_seq == seq:
                anchors.add(first.start)
            ok = span["start"] in anchors
        else:
            ok = span["start"] in starts and span["end"] in ends
        if not ok:
            raise FlagsDocumentError("flags_export_mismatch")


def validate_flags_document(
    document: Mapping[str, Any],
    *,
    normaliser: str,
    threshold: Decimal,
    model: str,
    export: TokenSequence | None = None,
) -> None:
    """Refuse a document that breaks the schema or a cross-field rule of ADR 0023.

    ``normaliser``, ``threshold`` and ``model`` are the binding values the document must carry.
    With ``export`` the spans are also placed on the export they were made from. A value that is
    not a mapping is a ``TypeError``; the document is never changed.
    """
    if not isinstance(document, Mapping):
        raise TypeError("a flags document is a mapping")
    _check_shape(document)
    if (
        document["normaliser"] != normaliser
        or document["flag_min_confidence"] != format_threshold(threshold)
        or document["stt"]["model"] != model
    ):
        raise FlagsDocumentError("flags_binding_mismatch")
    alignment, tokens, spans = document["alignment"], document["tokens"], document["spans"]
    hits, substitutions = alignment["hits"], alignment["substitutions"]
    deletions, insertions = alignment["deletions"], alignment["insertions"]
    if (
        hits + substitutions + deletions != tokens["export"]
        or hits + substitutions + insertions != tokens["stt"]
    ):
        raise FlagsDocumentError("flags_counts")
    for span in spans:
        _check_span(span, threshold)
    _check_order(spans, tokens["stt"])
    totals = (
        sum(s["substitutions"] for s in spans),
        sum(s["deletions"] for s in spans),
        sum(s["insertions"] for s in spans),
    )
    flagged_tokens = sum(s["export_tokens"] for s in spans if s["flagged"])
    if (
        totals != (substitutions, deletions, insertions)
        or tokens["flagged_export"] != flagged_tokens
    ):
        raise FlagsDocumentError("flags_counts")
    if export is not None:
        _check_export(spans, export)
