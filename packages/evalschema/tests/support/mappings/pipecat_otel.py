# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F1: Pipecat OpenTelemetry spans (OTLP/JSON) to Exchanges, StageSpans, Transcripts, Turns,
UsageRecords and ToolCallEvents (docs/phase1/api-surface-mappings.md section 2).

Pipecat 1.13.0 writes the spans ``conversation``, ``turn`` (parent of the stage spans), ``stt``,
``llm`` and ``tts``. There is no tool-call span: a call is recoverable only from the ``input`` JSON
of a later ``llm`` span. ``NOT_GIVEN`` and ``unknown`` are Pipecat's placeholders for a missing
value and are never stored. Source pointers are RFC 6901 pointers into the OTLP document; a pointer
that continues inside the text of an attribute is chained with ``#``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tracer_evalschema.errors import TracerSchemaError
from tracer_evalschema.events import StageSpan, ToolCallEvent
from tracer_evalschema.loader import loads_strict
from tracer_evalschema.transcript import Exchange, Transcript, Turn
from tracer_evalschema.usage import UsageRecord

from .common import (
    Citation,
    Draft,
    GapRow,
    Leg,
    MappingResult,
    Recorder,
    TableRow,
    chain,
    floor_ms,
    pointer,
    raw_text,
    scalar_leaves,
    unix_nano_to_utc_us,
)

__all__ = ["PROVENANCE_TABLE", "map_f1"]

RETRIEVED = "2026-10-23"
_SRC = "https://github.com/pipecat-ai/pipecat/blob/v1.13.0/src/pipecat/"
METRICS_CITATION = Citation(
    url=_SRC + "metrics/metrics.py",
    retrieved_at=RETRIEVED,
    statement=(
        "LLMTokenUsage (pipecat-ai 1.13.0): Anthropic and Bedrock report prompt_tokens net of the "
        "cache counts, OpenAI-compatible services report it gross, with the cache counts already "
        "inside it."
    ),
)
PLACEHOLDER_CITATION = Citation(
    url=_SRC + "utils/tracing/service_decorators.py",
    retrieved_at=RETRIEVED,
    statement=(
        "'unknown' is the model name Pipecat writes when a service names none, and NOT_GIVEN is "
        "its sentinel for a setting that was not given (pipecat-ai 1.13.0); neither is a value."
    ),
)
TOOL_STATUS_CITATION = Citation(
    url=_SRC + "processors/aggregators/llm_response_universal.py",
    retrieved_at=RETRIEVED,
    statement=(
        "A tool message in the LLM context reads IN_PROGRESS while the call runs and CANCELLED "
        "when it was cancelled (pipecat-ai 1.13.0); those words are placeholders, not results."
    ),
)
PLACEHOLDER_MODELS = frozenset({"unknown", "NOT_GIVEN"})
GROSS_PROVIDERS = frozenset({"openai"})
NET_PROVIDERS = frozenset({"anthropic", "aws.bedrock"})
STAGES = ("stt", "llm", "tts")

_IN = "gen_ai.usage.input_tokens"
_OUT = "gen_ai.usage.output_tokens"
_READ = "gen_ai.usage.cache_read.input_tokens"
_WRITE = "gen_ai.usage.cache_creation.input_tokens"
_TOKEN_UNITS = (
    (_IN, "input_tokens"),
    (_READ, "cache_read_input_tokens"),
    (_WRITE, "cache_write_input_tokens"),
    (_OUT, "output_tokens"),
)


@dataclass
class _Attr:
    key: str
    ptr: str
    kind: str | None
    value: Any
    valued: bool = True

    @property
    def vptr(self) -> str:
        if self.kind:
            return f"{self.ptr}/value/{self.kind}"
        return f"{self.ptr}/value" if self.valued else self.ptr

    @property
    def shown(self) -> str | None:
        """The value as sent, as text (``None`` when the attribute holds no typed value)."""
        return raw_text(self.value) if self.kind else None

    def integer(self) -> int | None:
        """The value as an int when the attribute is an integer (OTLP writes int64 as text)."""
        if self.kind != "intValue":
            return None
        if type(self.value) is int:
            return self.value
        if isinstance(self.value, str) and self.value.isascii():
            try:
                return int(self.value)
            except ValueError:
                return None
        return None

    def number(self) -> float | None:
        if self.kind == "doubleValue" and type(self.value) in (int, float):
            return float(self.value)
        return None


@dataclass
class _Span:
    base: str
    order: int
    raw: Mapping[str, Any]
    name: str | None
    attrs: list[_Attr] = field(default_factory=list)
    start: int | None = None
    end: int | None = None

    def all(self, key: str) -> list[_Attr]:
        return [a for a in self.attrs if a.key == key]

    def first(self, key: str) -> _Attr | None:
        return next((a for a in self.attrs if a.key == key), None)

    def string(self, key: str) -> _Attr | None:
        found = self.first(key)
        if found is not None and found.kind == "stringValue" and isinstance(found.value, str):
            return found
        return None

    @property
    def span_id(self) -> str | None:
        value = self.raw.get("spanId")
        return value if isinstance(value, str) else None

    @property
    def parent_id(self) -> str | None:
        value = self.raw.get("parentSpanId")
        return value if isinstance(value, str) else None

    @property
    def trace_id(self) -> str | None:
        value = self.raw.get("traceId")
        return value if isinstance(value, str) else None

    @property
    def order_source(self) -> str:
        return f"{self.base}/startTimeUnixNano" if self.start is not None else f"{self.base}/name"


def _read_spans(document: Any, rec: Recorder) -> list[_Span]:
    spans: list[_Span] = []
    resources = document.get("resourceSpans") if isinstance(document, dict) else None
    for i, resource in enumerate(resources if isinstance(resources, list) else []):
        scopes = resource.get("scopeSpans") if isinstance(resource, dict) else None
        for j, scope in enumerate(scopes if isinstance(scopes, list) else []):
            items = scope.get("spans") if isinstance(scope, dict) else None
            for k, raw in enumerate(items if isinstance(items, list) else []):
                if not isinstance(raw, dict):
                    continue
                base = f"/resourceSpans/{i}/scopeSpans/{j}/spans/{k}"
                name = raw.get("name")
                span = _Span(base, len(spans), raw, name if isinstance(name, str) else None)
                raw_attrs = raw.get("attributes")
                for n, attr in enumerate(raw_attrs if isinstance(raw_attrs, list) else []):
                    if not isinstance(attr, dict) or not isinstance(attr.get("key"), str):
                        continue
                    typed = attr.get("value")
                    kind, value = (None, None)
                    if isinstance(typed, dict) and len(typed) == 1:
                        kind, value = next(iter(typed.items()))
                    span.attrs.append(
                        _Attr(attr["key"], f"{base}/attributes/{n}", kind, value, "value" in attr)
                    )
                for slot, key in (("start", "startTimeUnixNano"), ("end", "endTimeUnixNano")):
                    parsed = unix_nano_to_utc_us(raw.get(key))
                    if parsed is None:
                        if key in raw:
                            rec.warn(
                                "timestamp_not_parseable",
                                "the instant is not a count of nanoseconds",
                                path=f"{base}/{key}",
                                raw=raw_text(raw[key]),
                            )
                    else:
                        setattr(span, slot, parsed[0])
                spans.append(span)
    return spans


def _time_key(span: _Span) -> tuple[int, int, int]:
    return (0, span.start, span.order) if span.start is not None else (1, 0, span.order)


def map_f1(
    document: Mapping[str, Any],
    *,
    run_id: Any,
    created_at: str,
    privacy: Mapping[str, Any],
    one_billable_event: Citation | None = None,
) -> MappingResult:
    rec = Recorder("F1", created_at, privacy)
    spans = _read_spans(document, rec)
    for span in spans:
        for key in ("name", "spanId", "parentSpanId", "traceId"):
            if key in span.raw:
                rec.consume(f"{span.base}/{key}")
        if "name" not in span.raw:
            rec.warn("malformed_entry", "the span has no name", path=span.base)
        elif span.name not in (*STAGES, "turn", "conversation"):
            rec.warn(
                "unknown_enum_value",
                "the span name is not one of conversation, turn, stt, llm or tts",
                path=f"{span.base}/name",
                raw=raw_text(span.raw.get("name")),
            )

    turn_numbers = _exchanges(rec, spans)
    stage = sorted((s for s in spans if s.name in STAGES), key=_time_key)
    for span in stage:
        _stage_span(rec, span, turn_numbers)
    _conversation_transcript(rec, stage, turn_numbers, created_at, privacy)
    _llm_transcript(rec, stage, turn_numbers, privacy)
    for span in stage:
        _usage(rec, span, run_id, one_billable_event)
    _tool_calls(rec, stage)
    _external_refs(rec, spans)
    return rec.result()


# ------------------------------------------------------------------------------ exchanges


def _exchanges(
    rec: Recorder, spans: Sequence[_Span]
) -> dict[tuple[str | None, str], tuple[int, str]]:
    """One Exchange per turn span with an integer ``turn.number``; the result maps (trace id, span
    id) of a turn span to (the number, the pointer of the number)."""
    numbers: dict[tuple[str | None, str], tuple[int, str]] = {}
    used: set[int] = set()
    for span in spans:
        if span.name != "turn":
            continue
        attr = span.first("turn.number")
        number = attr.integer() if attr is not None else None
        if attr is None or number is None or number < 0:
            rec.warn("malformed_entry", "the turn span has no usable turn.number", path=span.base)
            continue
        if number in used:
            rec.warn(
                "duplicate_turn_number",
                "the turn number is used by two turn spans; the first is kept",
                path=attr.vptr,
                raw=attr.shown,
            )
            continue
        used.add(number)
        if span.span_id is not None:
            numbers[(span.trace_id, span.span_id)] = (number, attr.vptr)
        draft = Draft(Exchange)
        draft.set("index", number, Leg("/index", (attr.vptr,), "otlp_int_to_int"))
        _instant(rec, draft, "started_at", "/started_at", span, span.start, "startTimeUnixNano")
        seconds = span.first("turn.duration_seconds")
        measured = floor_ms(seconds.number()) if seconds is not None else None
        if seconds is not None and measured is None:
            rec.warn(
                "malformed_entry",
                "turn.duration_seconds is not a non-negative number",
                path=seconds.vptr,
                raw=seconds.shown,
            )
        elif seconds is not None and measured is not None:
            draft.set(
                "duration_ms",
                measured[0],
                Leg("/duration_ms", (seconds.vptr,), "seconds_to_ms_floor"),
            )
            if measured[1]:
                _cut(rec, seconds.vptr, seconds.value)
        flag = span.first("turn.was_interrupted")
        if flag is not None and flag.kind == "boolValue" and type(flag.value) is bool:
            draft.set("was_interrupted", flag.value, Leg("/was_interrupted", (flag.vptr,)))
        draft.set("source", "telemetry", Leg("/source", (attr.vptr,), "constant:telemetry"))
        rec.commit(draft, where=span.base)
    return numbers


def _cut(rec: Recorder, path: str, value: Any) -> None:
    rec.warn(
        "time_precision_reduced",
        "digits finer than the stored precision were cut (truncated, never rounded)",
        path=path,
        raw=raw_text(value),
    )


def _instant(
    rec: Recorder,
    draft: Draft,
    name: str,
    target: str,
    span: _Span,
    nanoseconds: int | None,
    key: str,
) -> None:
    if nanoseconds is None:
        return
    parsed = unix_nano_to_utc_us(nanoseconds)
    if parsed is None:
        return
    path = f"{span.base}/{key}"
    draft.set(name, parsed[1], Leg(target, (path,), "unix_nano_to_utc_us"))
    if parsed[2]:
        _cut(rec, path, span.raw[key])


def _parent(
    span: _Span, numbers: Mapping[tuple[str | None, str], tuple[int, str]]
) -> tuple[int, str] | None:
    parent = span.parent_id
    return numbers.get((span.trace_id, parent)) if parent is not None else None


# ------------------------------------------------------------------------------ stage spans


def _stage_span(
    rec: Recorder, span: _Span, numbers: Mapping[tuple[str | None, str], tuple[int, str]]
) -> None:
    if span.span_id is None:
        rec.warn("malformed_entry", "the span has no spanId", path=span.base)
        return
    name_path = f"{span.base}/name"
    draft = Draft(StageSpan)
    draft.set("stage", span.name, Leg("/stage", (name_path,), "span_name_to_stage"))
    provider = span.string("gen_ai.provider.name")
    if provider is not None:
        draft.set("provider", provider.value, Leg("/provider", (provider.vptr,)))
    model = _model(rec, span)
    if model is not None:
        draft.set("model", model.value, Leg("/model", (model.vptr,)))
    _instant(rec, draft, "start", "/start", span, span.start, "startTimeUnixNano")
    if span.start is not None and span.end is not None:
        if span.end >= span.start:
            draft.set(
                "duration_ms",
                (span.end - span.start) // 1_000_000,
                Leg(
                    "/duration_ms",
                    (f"{span.base}/startTimeUnixNano", f"{span.base}/endTimeUnixNano"),
                    "ns_interval_to_ms_floor",
                ),
            )
            if (span.end - span.start) % 1_000_000:
                _cut(rec, f"{span.base}/endTimeUnixNano", span.raw["endTimeUnixNano"])
        else:
            rec.warn(
                "malformed_entry",
                "the span ends before it starts; no duration is stored",
                path=f"{span.base}/endTimeUnixNano",
            )
    ttfb = span.first("metrics.ttfb")
    if ttfb is not None:
        measured = floor_ms(ttfb.number())
        if measured is None:
            rec.warn(
                "malformed_entry",
                "metrics.ttfb is not a non-negative number",
                path=ttfb.vptr,
                raw=ttfb.shown,
            )
        else:
            draft.set("ttfb_ms", measured[0], Leg("/ttfb_ms", (ttfb.vptr,), "seconds_to_ms_floor"))
            if measured[1]:
                _cut(rec, ttfb.vptr, ttfb.value)
    parent = _parent(span, numbers)
    if parent is not None:
        draft.set(
            "exchange_index",
            parent[0],
            Leg("/exchange_index", (parent[1],), "parent_span_turn_number"),
        )
    sid = f"{span.base}/spanId"
    draft.set(
        "span_ref",
        {"system": "otel", "kind": "span_id", "value": span.span_id},
        Leg("/span_ref/system", (sid,), "constant:otel"),
        Leg("/span_ref/kind", (sid,), "constant:span_id"),
        Leg("/span_ref/value", (sid,)),
    )
    rec.commit(draft, where=span.base)


def _model(rec: Recorder, span: _Span) -> _Attr | None:
    """The span's model attribute, or ``None`` when it names none or a Pipecat placeholder (the
    placeholder is warned once per span)."""
    model = span.string("gen_ai.request.model")
    if model is None:
        return None
    if model.value in PLACEHOLDER_MODELS:
        rec.warn(
            "placeholder_value_ignored",
            "Pipecat's placeholder for a missing model name is not a model",
            path=model.vptr,
            raw=model.value,
        )
        return None
    return model


# ------------------------------------------------------------------------------ transcripts


def _turn_draft(
    rec: Recorder,
    span: _Span,
    text: _Attr,
    transcript_id: Any,
    seq: int,
    role: str,
    source: str,
    numbers: Mapping[tuple[str | None, str], tuple[int, str]],
) -> bool:
    stored, derivation = rec.text(str(text.value), text.vptr)
    draft = Draft(Turn, {"transcript_id": transcript_id})
    draft.set("seq", seq, Leg("/seq", (span.order_source,), "order_index"))
    draft.set("role", role, Leg("/role", (f"{span.base}/name",), f"constant:{role}"))
    draft.set("source", source, Leg("/source", (f"{span.base}/name",), f"constant:{source}"))
    draft.set(
        "timing_source", "none", Leg("/timing_source", (f"{span.base}/name",), "constant:none")
    )
    draft.set("text", stored, Leg("/text", (text.vptr,), derivation))
    parent = _parent(span, numbers)
    if parent is not None:
        draft.set(
            "exchange_index",
            parent[0],
            Leg("/exchange_index", (parent[1],), "parent_span_turn_number"),
        )
    return rec.commit(draft, where=span.base) is not None


def _agent_transcript(rec: Recorder, transcript_id: Any, privacy: Mapping[str, Any]) -> None:
    draft = Draft(Transcript, {"privacy": privacy})
    draft.set("source", "agent_telemetry", Leg("/source", (), "constant:agent_telemetry"))
    rec.commit(draft, entity_id=transcript_id)


def _conversation_transcript(
    rec: Recorder,
    stage: Sequence[_Span],
    numbers: Mapping[tuple[str | None, str], tuple[int, str]],
    created_at: str,
    privacy: Mapping[str, Any],
) -> None:
    transcript_id = rec.new_id(Transcript)
    made = 0
    for span in stage:
        if span.name == "stt":
            text = span.string("transcript")
            final = span.first("is_final")
            if final is not None:
                rec.consume(final.vptr)
            if text is None:
                continue
            if final is not None and final.kind == "boolValue" and final.value is False:
                rec.warn(
                    "interim_transcript_skipped",
                    "an interim speech-to-text hypothesis is not a turn",
                    path=final.vptr,
                )
                continue
            role, source = "caller", "agent_stt"
        elif span.name == "tts":
            text = span.string("text")
            if text is None:
                continue
            role, source = "agent", "agent_tts_input"
        else:
            continue
        if _turn_draft(rec, span, text, transcript_id, made, role, source, numbers):
            made += 1
    if made:
        _agent_transcript(rec, transcript_id, privacy)


def _llm_transcript(
    rec: Recorder,
    stage: Sequence[_Span],
    numbers: Mapping[tuple[str | None, str], tuple[int, str]],
    privacy: Mapping[str, Any],
) -> None:
    transcript_id = rec.new_id(Transcript)
    made = 0
    for span in stage:
        if span.name != "llm":
            continue
        text = span.string("output")
        if text is None:
            continue
        if _turn_draft(rec, span, text, transcript_id, made, "agent", "agent_llm_output", numbers):
            made += 1
    if made:
        _agent_transcript(rec, transcript_id, privacy)


# ------------------------------------------------------------------------------ usage


def _gap(stage: str | None, key: str, reason: str) -> GapRow:
    """A blocking row for usage the mapping refuses to guess (cost information is lost)."""
    return GapRow("F1", f"F1:{stage}.{key}", "blocking", "lossy", reason, "schema-architect")


def _usage(rec: Recorder, span: _Span, run_id: Any, merge_citation: Citation | None) -> None:
    if span.name == "llm":
        _llm_usage(rec, span, run_id, merge_citation)
    elif span.name == "stt":
        _single_usage(
            rec, span, run_id, "metrics.audio_seconds", "audio_input_seconds", merge_citation
        )
    elif span.name == "tts":
        _single_usage(
            rec, span, run_id, "metrics.character_count", "input_characters", merge_citation
        )


_USAGE_KEYS = {
    "llm": tuple(key for key, _ in _TOKEN_UNITS),
    "stt": ("metrics.audio_seconds",),
    "tts": ("metrics.character_count",),
}


def _usage_header(rec: Recorder, span: _Span, run_id: Any) -> Draft | None:
    """The fields every usage record of a span shares, or ``None`` when the span names no
    provider (the usage it states is then lost: a warning and a blocking gap row per usage key).
    A missing or placeholder model is left unset (M3)."""
    provider = span.string("gen_ai.provider.name")
    model = _model(rec, span)
    if provider is None:
        stated = [key for key in _USAGE_KEYS.get(span.name or "", ()) if span.all(key)]
        if stated:
            rec.warn(
                "malformed_entry",
                "usage is not recorded: the span names no provider",
                path=span.base,
            )
            for key in stated:
                rec.gap(_gap(span.name, key, "the span states usage and names no provider"))
        return None
    name_path = f"{span.base}/name"
    draft = Draft(UsageRecord, {"run_id": run_id})
    draft.set(
        "domain", "agent_under_test", Leg("/domain", (name_path,), "constant:agent_under_test")
    )
    draft.set("stage", span.name, Leg("/stage", (name_path,), "span_name_to_stage"))
    draft.set("provider", provider.value, Leg("/provider", (provider.vptr,)))
    if model is not None:
        draft.set("model", model.value, Leg("/model", (model.vptr,)))
    draft.set("source", "telemetry", Leg("/source", (name_path,), "constant:telemetry"))
    _instant(rec, draft, "occurred_at", "/occurred_at", span, span.start, "startTimeUnixNano")
    return draft


def _raw_legs(attrs: Sequence[_Attr]) -> tuple[dict[str, Any], list[Leg]]:
    """The usage attributes as received (a repeated key keeps every value, in order)."""
    raw: dict[str, Any] = {}
    legs: list[Leg] = []
    groups: dict[str, list[_Attr]] = {}
    for attr in attrs:
        if attr.kind is not None:
            groups.setdefault(attr.key, []).append(attr)
    for key, group in groups.items():
        values = [a.integer() if a.kind == "intValue" else a.value for a in group]
        if len(group) == 1:
            raw[key] = values[0]
            targets = [("/raw" + pointer(key), group[0])]
        else:
            raw[key] = values
            targets = [("/raw" + pointer(key, n), a) for n, a in enumerate(group)]
        for target, attr in targets:
            legs.append(
                Leg(target, (attr.vptr,), "otlp_int_to_int" if attr.kind == "intValue" else None)
            )
    return raw, legs


def _single_usage(
    rec: Recorder,
    span: _Span,
    run_id: Any,
    key: str,
    unit: str,
    merge_citation: Citation | None,
) -> None:
    """A stage whose usage is one attribute: stt audio seconds, tts characters."""
    attrs = span.all(key)
    if not attrs:
        return
    integer_unit = unit == "input_characters"
    for attr in attrs:
        number = attr.integer() if integer_unit else attr.number()
        if number is None or number < 0:
            rec.warn(
                "usage_inconsistent",
                "the usage value is not a non-negative number; no usage is recorded",
                path=attr.vptr,
                raw=attr.shown,
            )
            rec.gap(_gap(span.name, key, "the usage value is not a non-negative number"))
            return
    sources = tuple(a.vptr for a in attrs)
    citation: Citation | None = None
    if len(attrs) > 1:
        if not _complete(merge_citation):
            rec.warn(
                "usage_quantities_not_merged",
                "the usage attribute is repeated in one span and nothing allows summing it; no "
                "usage is recorded for the call",
                path=attrs[1].ptr,
            )
            rec.gap(
                _gap(
                    span.name,
                    key,
                    "a usage attribute repeats in one span and its counts are not summed",
                )
            )
            return
        count: Any
        if integer_unit:
            count = sum(int(a.integer() or 0) for a in attrs)
        else:
            count = _canonical_decimal(sum((Decimal(repr(a.number())) for a in attrs), Decimal(0)))
        derivation, citation = "merged_one_billable_event", merge_citation
    elif integer_unit:
        count, derivation = attrs[0].integer(), "otlp_int_to_int"
    else:
        count, derivation = (
            _decimal_string(float(attrs[0].number() or 0.0)),
            "float_to_decimal_string",
        )
    header = _usage_header(rec, span, run_id)
    if header is None:
        return
    header.set(
        "quantities",
        [{"unit": unit, "count": count}],
        Leg("/quantities/0/unit", (sources[0],), f"constant:{unit}"),
        Leg("/quantities/0/count", sources, derivation, citation),
    )
    raw, legs = _raw_legs(attrs)
    header.set("raw", raw, *legs)
    rec.commit(header, where=span.base)


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _complete(citation: Citation | None) -> bool:
    return (
        citation is not None
        and citation.url is not None
        and citation.url.startswith("https://")
        and _DATE.fullmatch(citation.retrieved_at or "") is not None
        and bool(citation.statement.strip())
    )


def _decimal_string(number: float) -> str:
    return _canonical_decimal(Decimal(repr(number)))


def _canonical_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _refuse(
    rec: Recorder, code: str, message: str, attr: _Attr, reason: str, *, key: str | None = None
) -> None:
    """No usage record for the call: a warning at the attribute and a blocking gap row (the
    cost information is lost, and a guess could change the bill)."""
    rec.warn(code, message, path=attr.ptr)
    rec.gap(_gap("llm", key or attr.key, reason))


def _llm_usage(rec: Recorder, span: _Span, run_id: Any, merge_citation: Citation | None) -> None:
    groups: dict[str, list[_Attr]] = {key: span.all(key) for key, _ in _TOKEN_UNITS}
    counted = [a for key, _ in _TOKEN_UNITS for a in groups[key]]
    if not counted:
        return
    for attr in counted:
        value = attr.integer()
        if value is None or value < 0:
            rec.warn(
                "usage_inconsistent",
                "a token count is not a non-negative integer; no usage is recorded",
                path=attr.vptr,
                raw=attr.shown,
            )
            rec.gap(_gap("llm", attr.key, "a token count is not a non-negative integer"))
            return

    repeated = [key for key, _ in _TOKEN_UNITS if len(groups[key]) > 1]
    if repeated and not _complete(merge_citation):
        for key in repeated:
            _refuse(
                rec,
                "usage_quantities_not_merged",
                "the usage attribute is repeated in one span and nothing states that the counts "
                "are parts of one billable event; no usage is recorded for the call",
                groups[key][1],
                "a usage attribute repeats in one span and the source does not say whether the "
                "counts are one billable event; no usage record is built",
            )
        return

    provider = span.string("gen_ai.provider.name")
    provider_name = provider.value if provider is not None else None
    cache_attrs = groups[_READ] + groups[_WRITE]
    gross = provider_name in GROSS_PROVIDERS
    net = provider_name in NET_PROVIDERS
    if cache_attrs and not (gross or net):
        _refuse(
            rec,
            "cache_semantics_unknown",
            "Pipecat does not state whether this service's input count includes its cache "
            "counts; no usage is recorded",
            cache_attrs[0],
            "the input count cannot be split from the cache counts for this provider",
        )
        return
    if cache_attrs and not groups[_IN]:
        _refuse(
            rec,
            "usage_inconsistent",
            "a cache count without an input count cannot be split; no usage is recorded",
            cache_attrs[0],
            "a cache count has no input count to split",
        )
        return
    if cache_attrs and gross and len(groups[_IN]) > 1:
        _refuse(
            rec,
            "usage_inconsistent",
            "a repeated input count cannot be split from the cache counts; no usage is recorded",
            groups[_IN][1],
            "a repeated input count cannot be split from the cache counts",
            key=_IN,
        )
        return

    quantities: list[dict[str, Any]] = []
    legs: list[Leg] = []
    for key, unit in _TOKEN_UNITS:
        occurrences = groups[key]
        if not occurrences:
            continue
        values = [int(a.integer() or 0) for a in occurrences]
        sources = tuple(a.vptr for a in occurrences)
        citation: Citation | None = None
        if len(occurrences) > 1:
            count, derivation, citation = sum(values), "merged_one_billable_event", merge_citation
        elif key == _IN and cache_attrs and gross:
            count = values[0] - sum(int(a.integer() or 0) for a in cache_attrs)
            if count < 0:
                _refuse(
                    rec,
                    "usage_inconsistent",
                    "the cache counts exceed the input count; no usage is recorded",
                    cache_attrs[0],
                    "the cache counts exceed the input count",
                )
                return
            sources = (occurrences[0].vptr, *(a.vptr for a in cache_attrs))
            derivation, citation = "input_tokens_gross_minus_cache", METRICS_CITATION
        elif key == _IN and cache_attrs and net:
            count, derivation, citation = (
                values[0],
                "input_tokens_net_as_reported",
                METRICS_CITATION,
            )
        else:
            count, derivation = values[0], "otlp_int_to_int"
        position = len(quantities)
        quantities.append({"unit": unit, "count": count})
        legs.append(Leg(f"/quantities/{position}/unit", (sources[0],), f"constant:{unit}"))
        legs.append(Leg(f"/quantities/{position}/count", sources, derivation, citation))
    header = _usage_header(rec, span, run_id)
    if header is None:
        return
    header.set("quantities", quantities, *legs)
    raw, raw_legs = _raw_legs([a for a in span.attrs if a.key.startswith("gen_ai.usage.")])
    header.set("raw", raw, *raw_legs)
    rec.commit(header, where=span.base)


# ------------------------------------------------------------------------------ tool calls


@dataclass
class _Call:
    call_id: str
    name: Any
    arguments: Any
    ptr: str
    seq: int
    name_ptr: str
    args_ptr: str
    id_ptr: str
    result: tuple[Any, str] | None = None


def _tool_calls(rec: Recorder, stage: Sequence[_Span]) -> None:
    calls: dict[str, _Call] = {}
    results: dict[str, tuple[Any, str]] = {}
    orphans: dict[str, str] = {}
    for span in stage:
        if span.name != "llm":
            continue
        source = span.string("input")
        if source is None:
            continue
        try:
            messages = json.loads(source.value)
        except (ValueError, RecursionError):
            messages = None
        if not isinstance(messages, list):
            rec.warn(
                "llm_input_unparseable",
                "the llm input is not a JSON list of messages; no tool call is read from it",
                path=source.vptr,
            )
            continue
        for m, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            where = chain(source.vptr, pointer(m))
            if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
                for c, call in enumerate(message["tool_calls"]):
                    _collect_call(rec, calls, call, chain(source.vptr, pointer(m, "tool_calls", c)))
            elif message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str):
                    rec.warn("tool_result_without_call", "a tool message names no call", path=where)
                elif call_id not in results:
                    results[call_id] = (message.get("content"), _content_ptr(message, where))
    for call_id, (content, where) in results.items():
        if call_id in calls:
            calls[call_id].result = (content, where)
        else:
            orphans[call_id] = where
    for where in orphans.values():
        rec.warn(
            "tool_result_without_call",
            "a tool message answers a call that no llm input contains",
            path=where,
        )
    for call in sorted(calls.values(), key=lambda c: c.seq):
        _tool_event(rec, call)


def _content_ptr(message: Mapping[str, Any], where: str) -> str:
    return where + "/content" if "content" in message else where


def _collect_call(rec: Recorder, calls: dict[str, _Call], call: Any, where: str) -> None:
    function = call.get("function") if isinstance(call, dict) else None
    call_id = call.get("id") if isinstance(call, dict) else None
    if not (isinstance(call_id, str) and isinstance(function, dict)):
        rec.warn("malformed_entry", "a tool call has no id or no function", path=where)
        return
    name, arguments = function.get("name"), function.get("arguments")
    known = calls.get(call_id)
    if known is not None:
        if (name, arguments) != (known.name, known.arguments):
            rec.warn(
                "tool_call_conflict",
                "the same call id appears with another name or arguments; the first is kept",
                path=where,
            )
        return
    calls[call_id] = _Call(
        call_id,
        name,
        arguments,
        where,
        len(calls),
        where + "/function/name",
        where + "/function/arguments",
        where + "/id",
    )


def _tool_event(rec: Recorder, call: _Call) -> None:
    if not isinstance(call.name, str):
        rec.warn("malformed_entry", "a tool call has no name", path=call.ptr)
        return
    name, name_derivation = rec.text(call.name, call.name_ptr)
    draft = Draft(ToolCallEvent)
    draft.set("name", name, Leg("/name", (call.name_ptr,), name_derivation))
    parsed: Any = None
    complete = False
    if isinstance(call.arguments, str):
        raw_args, raw_derivation = rec.text(call.arguments, call.args_ptr)
        draft.set(
            "arguments_raw", raw_args, Leg("/arguments_raw", (call.args_ptr,), raw_derivation)
        )
        try:
            parsed = loads_strict(call.arguments)
        except (ValueError, TracerSchemaError, RecursionError):
            parsed = None
        complete = isinstance(parsed, dict) and raw_derivation is None
        if not complete:
            rec.warn(
                "tool_arguments_incomplete",
                "the arguments are not one whole JSON object (cut off by the telemetry?)",
                path=call.args_ptr,
                raw=raw_text(call.arguments),
            )
    else:
        rec.warn(
            "tool_arguments_incomplete",
            "the call has no argument string",
            path=call.ptr,
        )
    if complete:
        draft.set(
            "arguments",
            parsed,
            *(
                Leg("/arguments" + leaf, (chain(call.args_ptr, leaf),), None)
                for leaf, _ in scalar_leaves(parsed)
            ),
        )
    draft.set(
        "arguments_complete",
        complete,
        Leg("/arguments_complete", (call.args_ptr,), "source_is_json_object"),
    )
    status, result_raw, status_leg = _status(rec, call)
    draft.set("status", status, status_leg)
    if result_raw is not None:
        text, derivation = rec.text(result_raw[0], result_raw[1])
        draft.set("result_raw", text, Leg("/result_raw", (result_raw[1],), derivation))
    draft.set("seq", call.seq, Leg("/seq", (call.ptr,), "order_index"))
    draft.set("source", "agent_telemetry", Leg("/source", (call.ptr,), "constant:agent_telemetry"))
    ident, id_derivation = rec.text(call.call_id, call.id_ptr)
    draft.set("external_call_id", ident, Leg("/external_call_id", (call.id_ptr,), id_derivation))
    rec.commit(draft, where=call.ptr)


def _status(rec: Recorder, call: _Call) -> tuple[str, tuple[str, str] | None, Leg]:
    """(status, the result text and its pointer if the message is a real result, the trace leg)."""
    if call.result is None:
        return "unknown", None, Leg("/status", (call.ptr,), "constant:unknown")
    content, where = call.result
    leg = Leg("/status", (where,), "tool_status_from_context_message", TOOL_STATUS_CITATION)
    if not isinstance(content, str):
        rec.warn("malformed_entry", "a tool message has no text content", path=where)
        return "unknown", None, leg
    if content == "IN_PROGRESS":
        rec.warn(
            "tool_result_placeholder",
            "IN_PROGRESS is Pipecat's placeholder while a call runs, not a result",
            path=where,
            raw=content,
        )
        return "unknown", None, leg
    if content == "CANCELLED":
        return "cancelled", None, leg
    try:
        payload = json.loads(content)
    except (ValueError, RecursionError):
        payload = None
    if isinstance(payload, dict) and payload.get("type") == "async_tool":
        rec.warn(
            "async_tool_payload",
            "an async-tool payload says the call is still running; it is not a result",
            path=where,
        )
        return "unknown", None, leg
    return "completed", (content, where), leg


# ------------------------------------------------------------------------------ references


def _external_refs(rec: Recorder, spans: Sequence[_Span]) -> None:
    seen_traces: set[str] = set()
    seen_conversations: set[str] = set()
    for span in spans:
        trace = span.trace_id
        if trace is not None and trace not in seen_traces:
            seen_traces.add(trace)
            rec.external_ref("otel", "trace_id", trace, f"{span.base}/traceId")
        attr = span.string("conversation.id")
        if attr is None:
            continue
        rec.consume(attr.vptr)
        if attr.value not in seen_conversations:
            seen_conversations.add(attr.value)
            rec.external_ref("pipecat", "conversation_id", attr.value, attr.vptr)


# ------------------------------------------------------------------------------ the table


def _row(
    target: str,
    kind: str,
    source: str | None,
    derivation: str | None,
    note: str,
    citation: Citation | None = None,
) -> TableRow:
    return TableRow(target, kind, source, derivation, citation, note)


_SURROGATE_NOTE = "a lone surrogate is replaced by U+FFFD (N82)"

PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row("Transcript.source", "constant", None, "constant:agent_telemetry", "Pipecat telemetry"),
    _row("Transcript.declared_language", "absent", None, None, "never filled from the source"),
    _row(
        "Transcript.detected_language",
        "absent",
        "stt.language",
        None,
        "'Detected or configured language' cannot be told from the configured one (M2)",
    ),
    _row(
        "Exchange.index",
        "derivation",
        "turn.turn.number",
        "otlp_int_to_int",
        "1-based as Pipecat counts",
    ),
    _row(
        "Exchange.started_at",
        "derivation",
        "turn.@startTimeUnixNano",
        "unix_nano_to_utc_us",
        "truncated to the microsecond",
    ),
    _row(
        "Exchange.duration_ms",
        "derivation",
        "turn.turn.duration_seconds",
        "seconds_to_ms_floor",
        "seconds truncated to whole ms (M6)",
    ),
    _row("Exchange.was_interrupted", "source", "turn.turn.was_interrupted", None, "verbatim"),
    _row("Exchange.source", "constant", "turn.turn.number", "constant:telemetry", "telemetry"),
    _row("StageSpan.stage", "derivation", "span name", "span_name_to_stage", "stt, llm or tts"),
    _row("StageSpan.provider", "source", "gen_ai.provider.name", None, "verbatim"),
    _row(
        "StageSpan.model", "source", "gen_ai.request.model", None, "verbatim unless a placeholder"
    ),
    _row(
        "StageSpan.model",
        "absent",
        "gen_ai.request.model",
        "placeholder_sentinel_ignored",
        "unknown and NOT_GIVEN are Pipecat placeholders (M3)",
        PLACEHOLDER_CITATION,
    ),
    _row(
        "StageSpan.start",
        "derivation",
        "@startTimeUnixNano",
        "unix_nano_to_utc_us",
        "truncated to the microsecond",
    ),
    _row(
        "StageSpan.duration_ms",
        "derivation",
        "@startTimeUnixNano, @endTimeUnixNano",
        "ns_interval_to_ms_floor",
        "span interval, whole ms",
    ),
    _row(
        "StageSpan.ttfb_ms",
        "derivation",
        "metrics.ttfb",
        "seconds_to_ms_floor",
        "only from metrics.ttfb",
    ),
    _row(
        "StageSpan.exchange_index",
        "derivation",
        "parent turn span turn.number",
        "parent_span_turn_number",
        "stated by the span hierarchy only",
    ),
    _row("StageSpan.span_ref.system", "constant", "@spanId", "constant:otel", "OpenTelemetry"),
    _row("StageSpan.span_ref.kind", "constant", "@spanId", "constant:span_id", "the span id"),
    _row("StageSpan.span_ref.value", "source", "@spanId", None, "verbatim"),
    _row(
        "Turn.seq",
        "derivation",
        "@startTimeUnixNano",
        "order_index",
        "order of span start, ties in document order (M25)",
    ),
    _row("Turn.role", "constant", "span name", "constant:caller", "stt text is the caller"),
    _row("Turn.role", "constant", "span name", "constant:agent", "tts and llm text are the agent"),
    _row("Turn.source", "constant", "span name", "constant:agent_stt", "text of an stt span"),
    _row("Turn.source", "constant", "span name", "constant:agent_tts_input", "text sent to TTS"),
    _row(
        "Turn.source", "constant", "span name", "constant:agent_llm_output", "output of an llm span"
    ),
    _row(
        "Turn.timing_source",
        "constant",
        "span name",
        "constant:none",
        "a span marks when the service worked, not when someone spoke (M1)",
    ),
    _row("Turn.text", "source", "stt.transcript, tts.text, llm.output", None, "verbatim"),
    _row(
        "Turn.text",
        "derivation",
        "stt.transcript, tts.text, llm.output",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "Turn.exchange_index",
        "derivation",
        "parent turn span turn.number",
        "parent_span_turn_number",
        "stated by the span hierarchy only",
    ),
    _row(
        "UsageRecord.domain",
        "constant",
        "span name",
        "constant:agent_under_test",
        "the agent's own call",
    ),
    _row("UsageRecord.stage", "derivation", "span name", "span_name_to_stage", "stt, llm or tts"),
    _row("UsageRecord.provider", "source", "gen_ai.provider.name", None, "verbatim"),
    _row(
        "UsageRecord.model",
        "source",
        "gen_ai.request.model",
        None,
        "verbatim; no record without a named model (M3)",
    ),
    _row("UsageRecord.source", "constant", "span name", "constant:telemetry", "telemetry"),
    _row(
        "UsageRecord.occurred_at",
        "derivation",
        "@startTimeUnixNano",
        "unix_nano_to_utc_us",
        "truncated to the microsecond",
    ),
    _row(
        "UsageRecord.quantities.*.unit",
        "constant",
        "usage attribute",
        "constant:input_tokens",
        "gen_ai.usage.input_tokens",
    ),
    _row(
        "UsageRecord.quantities.*.unit",
        "constant",
        "usage attribute",
        "constant:cache_read_input_tokens",
        "gen_ai.usage.cache_read.input_tokens",
    ),
    _row(
        "UsageRecord.quantities.*.unit",
        "constant",
        "usage attribute",
        "constant:cache_write_input_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
    ),
    _row(
        "UsageRecord.quantities.*.unit",
        "constant",
        "usage attribute",
        "constant:output_tokens",
        "gen_ai.usage.output_tokens",
    ),
    _row(
        "UsageRecord.quantities.*.unit",
        "constant",
        "usage attribute",
        "constant:audio_input_seconds",
        "metrics.audio_seconds (M5)",
    ),
    _row(
        "UsageRecord.quantities.*.unit",
        "constant",
        "usage attribute",
        "constant:input_characters",
        "metrics.character_count (M5)",
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "usage attribute",
        "otlp_int_to_int",
        "the count as reported",
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "gen_ai.usage.input_tokens and cache counts",
        "input_tokens_gross_minus_cache",
        "OpenAI-compatible input is gross of the cache counts (M4)",
        METRICS_CITATION,
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "gen_ai.usage.input_tokens",
        "input_tokens_net_as_reported",
        "Anthropic and Bedrock input is net of the cache counts (M4)",
        METRICS_CITATION,
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "repeated usage attribute",
        "merged_one_billable_event",
        "sum, only with a cited statement that the counts are one billable event (N64)",
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "metrics.audio_seconds",
        "float_to_decimal_string",
        "canonical decimal text of the printed number",
    ),
    _row(
        "UsageRecord.raw.*",
        "derivation",
        "usage attributes",
        "otlp_int_to_int",
        "the usage attributes as received",
    ),
    _row(
        "UsageRecord.raw.*", "source", "usage attributes", None, "the usage attributes as received"
    ),
    _row("ToolCallEvent.name", "source", "llm.input tool_calls function.name", None, "verbatim"),
    _row(
        "ToolCallEvent.name",
        "derivation",
        "llm.input tool_calls function.name",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "ToolCallEvent.arguments_raw",
        "source",
        "llm.input tool_calls function.arguments",
        None,
        "verbatim, never repaired",
    ),
    _row(
        "ToolCallEvent.arguments_raw",
        "derivation",
        "llm.input tool_calls function.arguments",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "ToolCallEvent.arguments.*",
        "source",
        "llm.input tool_calls function.arguments",
        None,
        "only when the string is one whole JSON object",
    ),
    _row(
        "ToolCallEvent.arguments_complete",
        "derivation",
        "llm.input tool_calls function.arguments",
        "source_is_json_object",
        "true only for one whole JSON object",
    ),
    _row(
        "ToolCallEvent.result_raw",
        "source",
        "llm.input tool message content",
        None,
        "only a real result, not a placeholder",
    ),
    _row(
        "ToolCallEvent.result_raw",
        "derivation",
        "llm.input tool message content",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row("ToolCallEvent.result_complete", "absent", None, None, "not stated by the source"),
    _row("ToolCallEvent.result_error", "absent", None, None, "not stated by the source"),
    _row(
        "ToolCallEvent.status",
        "derivation",
        "llm.input tool message content",
        "tool_status_from_context_message",
        "IN_PROGRESS and the async payload are not completion (M20)",
        TOOL_STATUS_CITATION,
    ),
    _row(
        "ToolCallEvent.status",
        "constant",
        "llm.input tool_calls",
        "constant:unknown",
        "no tool message states an outcome",
    ),
    _row(
        "ToolCallEvent.seq",
        "derivation",
        "llm.input tool_calls",
        "order_index",
        "order of first appearance",
    ),
    _row(
        "ToolCallEvent.source",
        "constant",
        "llm.input tool_calls",
        "constant:agent_telemetry",
        "telemetry",
    ),
    _row("ToolCallEvent.external_call_id", "source", "llm.input tool_calls id", None, "verbatim"),
    _row(
        "ToolCallEvent.external_call_id",
        "derivation",
        "llm.input tool_calls id",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
)
