# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F2: Langfuse-attributed OTLP spans (OTLP/JSON) to StageSpans, UsageRecords, CostRecords,
ToolCallEvents, Transcripts and Turns (docs/phase1/api-surface-mappings-2.md section 2).

A Langfuse observation is an OTLP span whose attributes name its type
(``langfuse.observation.type``) and carry its input, output, model, usage and cost. Only what
the Langfuse and OpenTelemetry documentation states is read; nothing is inferred from names.
Source pointers are RFC 6901 pointers into the OTLP document; one that continues inside the text
of an attribute is chained with ``#``. A number inside such a text is read as its lexeme, never
through a float.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tracer_evalschema.cost import ContractCurrency, import_reported_cost
from tracer_evalschema.errors import TracerSchemaError
from tracer_evalschema.events import StageSpan, ToolCallEvent
from tracer_evalschema.ids import CostRecordId, UsageRecordId
from tracer_evalschema.loader import loads_strict
from tracer_evalschema.text import MACHINE_KEY_PATTERN, replace_lone_surrogates
from tracer_evalschema.transcript import Transcript, Turn
from tracer_evalschema.usage import UsageRecord

from .common import (
    Citation,
    Draft,
    GapRow,
    Leg,
    MappingResult,
    Recorder,
    TableRow,
    TraceEntry,
    chain,
    pointer,
    raw_text,
    scalar_leaves,
    unix_nano_to_utc_us,
)
from .pipecat_otel import _Attr, _Span, _time_key

__all__ = ["LANGFUSE_COST_CONTRACT", "PROVENANCE_TABLE", "map_f2"]

RETRIEVED = "2026-10-23"
_OTEL_PAGE = "https://langfuse.com/integrations/native/opentelemetry"
_COST_PAGE = "https://langfuse.com/docs/observability/features/token-and-cost-tracking"

LANGFUSE_COST_CONTRACT = Citation(
    url=_OTEL_PAGE + "#observation-level-attributes",
    retrieved_at=RETRIEVED,
    statement=(
        'Langfuse OpenTelemetry attribute table, row "cost": "The calculated cost in USD. '
        'Generation only." It is read from langfuse.observation.cost_details (a JSON string) and '
        "gen_ai.usage.cost (set as the total key)."
    ),
)
OPERATION_CITATION = Citation(
    url="https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/",
    retrieved_at=RETRIEVED,
    statement=(
        "OpenTelemetry GenAI registry: the well-known values of gen_ai.operation.name chat, "
        "text_completion and generate_content name text-generation model calls."
    ),
)
TYPE_CITATION = Citation(
    url=_OTEL_PAGE,
    retrieved_at=RETRIEVED,
    statement=(
        "Langfuse OpenTelemetry integration, Ingestion transformations: an observation that "
        "carries a model attribute and no recognized langfuse.observation.type is a generation."
    ),
)
USAGE_CITATION = Citation(
    url=_COST_PAGE + "#usage-details-normalization",
    retrieved_at=RETRIEVED,
    statement=(
        "Langfuse usage normalization of OpenTelemetry gen_ai.usage.* attributes: cache reads and "
        "writes are subtracted from input; the OpenTelemetry registry says the input count "
        "includes the cached tokens."
    ),
)

OBSERVATION_TYPES = frozenset(
    {
        "span",
        "generation",
        "event",
        "embedding",
        "agent",
        "tool",
        "chain",
        "retriever",
        "guardrail",
        "evaluator",
    }
)
LLM_OPERATIONS = frozenset({"chat", "text_completion", "generate_content"})
MODEL_KEYS = (
    "langfuse.observation.model.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "llm.model_name",
    "model",
)

_TYPE = "langfuse.observation.type"
_DETAILS = "langfuse.observation.usage_details"
_COST_DETAILS = "langfuse.observation.cost_details"
_GEN_COST = "gen_ai.usage.cost"
_INPUT = "langfuse.observation.input"
_OUTPUT = "langfuse.observation.output"
_TOOL_ARGS = "gen_ai.tool.call.arguments"
_TOOL_RESULT = "gen_ai.tool.call.result"
_INPUT_SHADOWS = ("gen_ai.prompt", "input.value", "mlflow.spanInputs")
_OUTPUT_SHADOWS = ("gen_ai.completion", "output.value", "mlflow.spanOutputs")

_IN = "gen_ai.usage.input_tokens"
_OUT = "gen_ai.usage.output_tokens"
_READ = "gen_ai.usage.cache_read.input_tokens"
_WRITE = "gen_ai.usage.cache_creation.input_tokens"
_GENERIC_UNITS = (
    (_IN, "input_tokens"),
    (_READ, "cache_read_input_tokens"),
    (_WRITE, "cache_write_input_tokens"),
    (_OUT, "output_tokens"),
)
_DETAIL_UNITS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read_input_tokens": "cache_read_input_tokens",
    "input_cached_tokens": "cache_read_input_tokens",
}

_COUNT = re.compile(r"(?:0|[1-9][0-9]*)")
_MACHINE_KEY = re.compile(MACHINE_KEY_PATTERN)
_SURROGATES = re.compile("[\ud800-\udfff]")


class _Lexeme(str):
    """A number read from inside a JSON text: its lexeme as sent."""

    __slots__ = ()


def _loads(text: str) -> tuple[bool, Any]:
    """(whether the text is one JSON value, the value). Numbers are lexemes, a repeated key, NaN
    and Infinity are refused (they would make the reading ambiguous)."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError("repeated key")
            out[key] = value
        return out

    def refuse(_: str) -> Any:
        raise ValueError("not a JSON number")

    try:
        value = json.loads(
            text,
            parse_float=_Lexeme,
            parse_int=_Lexeme,
            parse_constant=refuse,
            object_pairs_hook=pairs,
        )
    except (ValueError, RecursionError):
        return False, None
    return True, value


def _is_text(value: Any) -> bool:
    """A JSON string (a number read as a lexeme is not one)."""
    return isinstance(value, str) and not isinstance(value, _Lexeme)


def _shown(value: Any) -> str | None:
    """A scalar of a JSON text as sent (a number: its lexeme); ``None`` for a container."""
    if isinstance(value, str):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return json.dumps(value)
    return None


def _complete(citation: Citation | None) -> bool:
    return (
        citation is not None
        and bool(citation.url)
        and citation.retrieved_at is not None
        and _date(citation.retrieved_at) is not None
    )


def _date(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        return None


def _anchor(span: _Span) -> str:
    """A pointer that resolves for the constants of a span's records (the name, else the span)."""
    return f"{span.base}/name" if "name" in span.raw else span.base


def _order_source(span: _Span) -> str:
    return f"{span.base}/startTimeUnixNano" if span.start is not None else _anchor(span)


def _gap(span: _Span, key: str, reason: str) -> GapRow:
    shown = span.name if span.name is not None else "<unnamed>"
    return GapRow("F2", f"F2:{shown}.{key}", "blocking", "lossy", reason, "dev")


@dataclass
class _Obs:
    span: _Span
    kind: str
    explicit: _Attr | None
    model: _Attr | None


def _cut(rec: Recorder, path: str, value: Any) -> None:
    rec.warn(
        "time_precision_reduced",
        "digits finer than the stored precision were cut (truncated, never rounded)",
        path=path,
        raw=raw_text(value),
    )


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
                                "malformed_entry",
                                "the instant is not a count of nanoseconds",
                                path=f"{base}/{key}",
                                raw=raw_text(raw[key]),
                            )
                    else:
                        setattr(span, slot, parsed[0])
                spans.append(span)
    return spans


def _text(span: _Span, key: str) -> _Attr | None:
    """The first attribute ``key`` that holds a text."""
    for attr in span.all(key):
        if attr.kind == "stringValue" and isinstance(attr.value, str):
            return attr
    return None


def _classify(rec: Recorder, span: _Span) -> _Obs:
    explicit: _Attr | None = None
    declared = _text(span, _TYPE)
    for attr in span.all(_TYPE):
        rec.consume(attr.vptr)
    if declared is not None:
        if declared.value in OBSERVATION_TYPES:
            explicit = declared
        else:
            rec.warn(
                "unknown_enum_value",
                "the observation type is not one Langfuse documents; it is not used",
                path=declared.vptr,
                raw=declared.value,
            )
    model: _Attr | None = None
    for key in MODEL_KEYS:
        for attr in span.all(key):
            if (
                model is None
                and attr.kind == "stringValue"
                and isinstance(attr.value, str)
                and attr.value
            ):
                model = attr
            else:
                rec.consume(attr.vptr)
    if explicit is not None:
        obs = _Obs(span, str(explicit.value), explicit, model)
    elif model is not None:
        obs = _Obs(span, "generation", None, model)
    else:
        obs = _Obs(span, "span", None, None)
    if model is not None and obs.kind not in ("generation", "embedding"):
        rec.consume(model.vptr)
    return obs


def map_f2(
    document: Mapping[str, Any],
    *,
    run_id: Any,
    created_at: str,
    privacy: Mapping[str, Any],
    cost_contract: Citation | None = LANGFUSE_COST_CONTRACT,
) -> MappingResult:
    rec = Recorder("F2", created_at, privacy)
    spans = _read_spans(document, rec)
    observations: list[_Obs] = []
    for span in spans:
        for key in ("name", "spanId", "traceId"):
            if key in span.raw:
                rec.consume(f"{span.base}/{key}")
        observations.append(_classify(rec, span))
    ordered = sorted(observations, key=lambda o: _time_key(o.span))
    calls = [o for o in ordered if o.kind in ("generation", "embedding")]
    for obs in calls:
        _stage_span(rec, obs)
        usage = _usage(rec, obs, run_id)
        _cost(rec, obs, usage, cost_contract, created_at)
    _tool_calls(rec, [o for o in ordered if o.kind == "tool"])
    _transcript(rec, [o for o in calls if o.kind == "generation"], privacy)
    _external_refs(rec, spans)
    return rec.result()


# ------------------------------------------------------------------------------ stage spans


def _instant(
    rec: Recorder, draft: Draft, name: str, target: str, span: _Span, nanoseconds: int | None
) -> None:
    if nanoseconds is None:
        return
    parsed = unix_nano_to_utc_us(nanoseconds)
    if parsed is None:
        return
    path = f"{span.base}/startTimeUnixNano"
    draft.set(name, parsed[1], Leg(target, (path,), "unix_nano_to_utc_us"))
    if parsed[2]:
        _cut(rec, path, span.raw["startTimeUnixNano"])


def _stage_legs(rec: Recorder, obs: _Obs) -> tuple[str, list[Leg], str | None, list[Leg]]:
    """(stage, the legs of ``/stage``, ``stage_raw``, the legs of ``/stage_raw``)."""
    span = obs.span
    operation = _text(span, "gen_ai.operation.name")
    if operation is not None:
        rec.consume(operation.vptr)
    if operation is not None and operation.value in LLM_OPERATIONS:
        return (
            "llm",
            [Leg("/stage", (operation.vptr,), "chat_operation_to_llm_stage", OPERATION_CITATION)],
            None,
            [],
        )
    anchor = obs.explicit if obs.explicit is not None else obs.model
    assert anchor is not None
    stage_leg = Leg("/stage", (anchor.vptr,), "constant:other")
    if obs.explicit is not None:
        raw_leg = Leg("/stage_raw", (anchor.vptr,), None)
    else:
        raw_leg = Leg(
            "/stage_raw", (anchor.vptr,), "observation_type_inferred_from_model", TYPE_CITATION
        )
    return "other", [stage_leg], obs.kind, [raw_leg]


def _stage_span(rec: Recorder, obs: _Obs) -> None:
    span = obs.span
    stage, stage_legs, stage_raw, raw_legs = _stage_legs(rec, obs)
    if span.span_id is None:
        rec.warn("malformed_entry", "the span has no spanId", path=span.base)
        return
    draft = Draft(StageSpan)
    draft.set("stage", stage, *stage_legs)
    if stage_raw is not None:
        draft.set("stage_raw", stage_raw, *raw_legs)
    provider = _text(span, "gen_ai.provider.name")
    if provider is not None:
        draft.set("provider", provider.value, Leg("/provider", (provider.vptr,)))
    if obs.model is not None:
        draft.set("model", obs.model.value, Leg("/model", (obs.model.vptr,)))
    _instant(rec, draft, "start", "/start", span, span.start)
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
    sid = f"{span.base}/spanId"
    draft.set(
        "span_ref",
        {"system": "otel", "kind": "span_id", "value": span.span_id},
        Leg("/span_ref/system", (sid,), "constant:otel"),
        Leg("/span_ref/kind", (sid,), "constant:span_id"),
        Leg("/span_ref/value", (sid,)),
    )
    rec.commit(draft, where=span.base)


# ------------------------------------------------------------------------------ usage


@dataclass
class _Counts:
    quantities: list[dict[str, Any]]
    legs: list[Leg]
    raw: dict[str, Any]
    raw_legs: list[Leg]
    sources: list[_Attr]


def _usage(rec: Recorder, obs: _Obs, run_id: Any) -> UsageRecord | None:
    span = obs.span
    details = span.all(_DETAILS)
    generic = {key: span.all(key) for key, _ in _GENERIC_UNITS}
    if not details and not any(generic.values()):
        return None
    if details:
        for attrs in generic.values():
            for attr in attrs:
                rec.consume(attr.vptr)
        counts = _details_counts(rec, span, details)
    else:
        counts = _generic_counts(rec, span, generic)
    if counts is None:
        return None
    provider = _text(span, "gen_ai.provider.name")
    if provider is None:
        rec.warn(
            "malformed_entry", "usage is not recorded: the span names no provider", path=span.base
        )
        for attr in counts.sources:
            rec.gap(_gap(span, attr.key, "the span states usage and names no provider"))
        return None
    stage, stage_legs, _, _ = _stage_legs(rec, obs)
    name_path = _anchor(span)
    draft = Draft(UsageRecord, {"run_id": run_id})
    draft.set(
        "domain", "agent_under_test", Leg("/domain", (name_path,), "constant:agent_under_test")
    )
    draft.set("stage", stage, *stage_legs)
    draft.set("provider", provider.value, Leg("/provider", (provider.vptr,)))
    if obs.model is not None:
        draft.set("model", obs.model.value, Leg("/model", (obs.model.vptr,)))
    draft.set("source", "telemetry", Leg("/source", (name_path,), "constant:telemetry"))
    _instant(rec, draft, "occurred_at", "/occurred_at", span, span.start)
    draft.set("quantities", counts.quantities, *counts.legs)
    draft.set("raw", counts.raw, *counts.raw_legs)
    built: UsageRecord | None = rec.commit(draft, where=span.base)
    if built is None:
        for attr in counts.sources:
            rec.gap(_gap(span, attr.key, "the usage record could not be built from the source"))
    return built


def _refuse(
    rec: Recorder,
    span: _Span,
    attr: _Attr,
    code: str,
    message: str,
    *,
    path: str | None = None,
    raw: str | None = None,
    reason: str,
) -> None:
    rec.warn(code, message, path=path if path is not None else attr.vptr, raw=raw)
    rec.gap(_gap(span, attr.key, reason))


def _details_counts(rec: Recorder, span: _Span, details: list[_Attr]) -> _Counts | None:
    attr = details[0]
    if len(details) > 1:
        _refuse(
            rec,
            span,
            attr,
            "usage_inconsistent",
            "the usage attribute is repeated in one span; no usage is recorded for the call",
            path=details[1].vptr,
            reason="langfuse.observation.usage_details repeats in one span",
        )
        return None
    if attr.kind != "stringValue" or not isinstance(attr.value, str):
        _refuse(
            rec,
            span,
            attr,
            "observation_json_unparseable",
            "usage_details is not a text; no usage is recorded for the call",
            raw=attr.shown,
            reason="usage_details is not a JSON text",
        )
        return None
    ok, parsed = _loads(attr.value)
    if not ok or not isinstance(parsed, dict):
        _refuse(
            rec,
            span,
            attr,
            "observation_json_unparseable",
            "usage_details is not a JSON object; no usage is recorded for the call",
            raw=attr.value,
            reason="usage_details is not a JSON object",
        )
        return None
    problems = 0
    seen: set[str] = set()
    units: list[tuple[str, str, str]] = []
    for key, value in parsed.items():
        where = chain(attr.vptr, pointer(key))
        if key == "total":
            continue
        unit = _DETAIL_UNITS.get(key)
        if unit is None:
            rec.warn(
                "usage_key_unmapped",
                "usage_details names a bucket that no usage unit holds; the whole call's usage "
                "is not recorded",
                path=where,
                raw=_shown(value),
            )
            problems += 1
        elif not (isinstance(value, _Lexeme) and _COUNT.fullmatch(value)):
            rec.warn(
                "usage_inconsistent",
                "a usage count is not a non-negative integer; no usage is recorded for the call",
                path=where,
                raw=_shown(value),
            )
            problems += 1
        elif unit in seen:
            rec.warn(
                "usage_quantities_not_merged",
                "two keys state one unit and nothing says they are one billable event; no usage "
                "is recorded for the call",
                path=where,
            )
            problems += 1
        else:
            seen.add(unit)
            units.append((key, unit, where))
    if problems:
        rec.gap(_gap(span, attr.key, "usage_details cannot be stored without a guess"))
        return None
    if not units:
        return None
    quantities: list[dict[str, Any]] = []
    legs: list[Leg] = []
    for position, (key, unit, where) in enumerate(units):
        count = int(str(parsed[key]))
        quantities.append({"unit": unit, "count": count})
        legs.append(Leg(f"/quantities/{position}/unit", (where,), f"constant:{unit}"))
        legs.append(Leg(f"/quantities/{position}/count", (where,), "json_int_lexeme_to_int"))
    stored, derivation = rec.text(attr.value, attr.vptr)
    raw_leg = Leg("/raw" + pointer(_DETAILS), (attr.vptr,), derivation)
    return _Counts(quantities, legs, {_DETAILS: stored}, [raw_leg], [attr])


def _generic_counts(
    rec: Recorder, span: _Span, groups: Mapping[str, list[_Attr]]
) -> _Counts | None:
    counted = [a for key, _ in _GENERIC_UNITS for a in groups[key]]
    for key, _ in _GENERIC_UNITS:
        if len(groups[key]) > 1:
            _refuse(
                rec,
                span,
                groups[key][1],
                "usage_inconsistent",
                "the usage attribute is repeated in one span; no usage is recorded for the call",
                reason="a usage attribute repeats in one span",
            )
            return None
    for attr in counted:
        value = attr.integer()
        if value is None or value < 0:
            _refuse(
                rec,
                span,
                attr,
                "usage_inconsistent",
                "a token count is not a non-negative integer; no usage is recorded for the call",
                raw=attr.shown,
                reason="a token count is not a non-negative integer",
            )
            return None
    cache = groups[_READ] + groups[_WRITE]
    if cache and not groups[_IN]:
        _refuse(
            rec,
            span,
            cache[0],
            "usage_inconsistent",
            "a cache count without an input count cannot be split; no usage is recorded",
            reason="a cache count has no input count to split",
        )
        return None
    quantities: list[dict[str, Any]] = []
    legs: list[Leg] = []
    for key, unit in _GENERIC_UNITS:
        if not groups[key]:
            continue
        attr = groups[key][0]
        count = int(attr.integer() or 0)
        sources: tuple[str, ...] = (attr.vptr,)
        derivation, citation = "otlp_int_to_int", None
        if key == _IN and cache:
            count -= sum(int(a.integer() or 0) for a in cache)
            if count < 0:
                _refuse(
                    rec,
                    span,
                    cache[0],
                    "usage_inconsistent",
                    "the cache counts exceed the input count; no usage is recorded",
                    reason="the cache counts exceed the input count",
                )
                return None
            sources = (attr.vptr, *(a.vptr for a in cache))
            derivation, citation = "input_tokens_gross_minus_cache", USAGE_CITATION
        position = len(quantities)
        quantities.append({"unit": unit, "count": count})
        legs.append(Leg(f"/quantities/{position}/unit", (attr.vptr,), f"constant:{unit}"))
        legs.append(Leg(f"/quantities/{position}/count", sources, derivation, citation))
    raw: dict[str, Any] = {}
    raw_legs: list[Leg] = []
    for key, _ in _GENERIC_UNITS:
        if groups[key]:
            attr = groups[key][0]
            raw[key] = attr.integer()
            raw_legs.append(Leg("/raw" + pointer(key), (attr.vptr,), "otlp_int_to_int"))
    return _Counts(quantities, legs, raw, raw_legs, counted)


# ------------------------------------------------------------------------------ cost


def _canonical_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass
class _Amount:
    text: str
    sources: tuple[str, ...]
    raw_derivation: str | None
    amount_path: str


def _cost(
    rec: Recorder,
    obs: _Obs,
    usage: UsageRecord | None,
    contract: Citation | None,
    created_at: str,
) -> None:
    span = obs.span
    details = span.all(_COST_DETAILS)
    generic = span.all(_GEN_COST)
    if not details and not generic:
        return
    if details:
        for attr in generic:
            rec.consume(attr.vptr)
        amount = _details_amount(rec, span, details[0])
        carrier = details[0]
    else:
        amount = _generic_amount(rec, span, generic[0])
        carrier = generic[0]
    if amount is None:
        return
    usable = contract if _complete(contract) else None
    moment = _date(usable.retrieved_at) if usable is not None and usable.retrieved_at else None
    imported = import_reported_cost(
        domain="agent_under_test",
        usage_record_ids=[] if usage is None else [UsageRecordId.parse(str(usage.id))],
        reported_by="telemetry_platform",
        amount_raw=amount.text,
        currency_raw=None,
        record_id=CostRecordId.new(),
        created_at=datetime.fromisoformat(created_at),
        contract=(
            ContractCurrency("USD", usable.url, moment)
            if usable is not None and moment is not None
            else None
        ),
        currency_source_path=carrier.vptr,
        amount_source_path=amount.amount_path,
    )
    for warning in imported.warnings:
        rec.warn(warning.code, warning.message, path=warning.path, raw=warning.raw)
    record = imported.record
    key = str(record.id)
    anchor = (carrier.vptr,)
    entries: list[tuple[str, tuple[str, ...], str | None, Citation | None]] = [
        ("/domain", anchor, "constant:agent_under_test", None),
        ("/status", anchor, f"constant:{record.status}", None),
    ]
    if record.amount is not None:
        entries.append(
            ("/amount/amount", amount.sources, _amount_derivation(amount), None),
        )
        entries.append(("/amount/currency", anchor, "contract_currency_usd", contract))
        entries.append(("/basis", anchor, f"constant:{record.basis}", None))
        entries.append(("/reported_by", anchor, f"constant:{record.reported_by}", None))
    if record.na_reason is not None:
        entries.append(("/na_reason", anchor, f"constant:{record.na_reason}", None))
    raw = record.reported_raw
    if raw is not None:
        entries.append(("/reported_raw/reported_by", anchor, f"constant:{raw.reported_by}", None))
        shown = amount.raw_derivation
        if replace_lone_surrogates(amount.text) != amount.text:
            shown = "lone_surrogate_replaced"
        entries.append(("/reported_raw/amount_raw", amount.sources, shown, None))
        if raw.currency_raw is not None:
            entries.append(
                ("/reported_raw/currency_raw", anchor, "contract_currency_usd", contract)
            )
        entries.append(
            ("/reported_raw/currency_source", anchor, f"constant:{raw.currency_source}", None)
        )
    rec.records.append(record)
    for field, sources, derivation, citation in entries:
        rec.trace.append(TraceEntry(key, field, sources, derivation, citation))


def _amount_derivation(amount: _Amount) -> str:
    return "float_amount_canonical" if amount.raw_derivation else "amount_lexeme_canonical"


def _details_amount(rec: Recorder, span: _Span, attr: _Attr) -> _Amount | None:
    if attr.kind != "stringValue" or not isinstance(attr.value, str):
        _refuse(
            rec,
            span,
            attr,
            "observation_json_unparseable",
            "cost_details is not a text; no cost is recorded for the call",
            raw=attr.shown,
            reason="cost_details is not a JSON text",
        )
        return None
    ok, parsed = _loads(attr.value)
    if not ok or not isinstance(parsed, dict):
        _refuse(
            rec,
            span,
            attr,
            "observation_json_unparseable",
            "cost_details is not a JSON object; no cost is recorded for the call",
            raw=attr.value,
            reason="cost_details is not a JSON object",
        )
        return None
    total = parsed.get("total")
    if total is None:
        _refuse(
            rec,
            span,
            attr,
            "cost_total_absent",
            "cost_details has no total; the total is not derived from the parts",
            raw=attr.value,
            reason="cost_details states no total for the call",
        )
        return None
    where = chain(attr.vptr, pointer("total"))
    if not isinstance(total, str):
        _refuse(
            rec,
            span,
            attr,
            "malformed_entry",
            "the cost total is neither a number nor a text; no cost is recorded for the call",
            path=where,
            raw=_shown(total),
            reason="the cost total is neither a number nor a text",
        )
        return None
    return _Amount(str(total), (where,), None, where)


def _generic_amount(rec: Recorder, span: _Span, attr: _Attr) -> _Amount | None:
    value = attr.value
    if attr.kind == "doubleValue" and type(value) in (int, float) and _finite(value):
        canonical = _canonical_decimal(Decimal(repr(float(value))))
        return _Amount(canonical, (attr.vptr,), "float_amount_canonical", attr.vptr)
    if attr.kind in ("stringValue", "intValue") and isinstance(value, str):
        return _Amount(value, (attr.vptr,), None, attr.vptr)
    _refuse(
        rec,
        span,
        attr,
        "malformed_entry",
        "gen_ai.usage.cost is not a number or a text; no cost is recorded for the call",
        raw=attr.shown,
        reason="gen_ai.usage.cost is not a number or a text",
    )
    return None


def _finite(value: Any) -> bool:
    return bool(value == value and value not in (float("inf"), float("-inf")))


# ------------------------------------------------------------------------------ tool calls


def _carrier(
    rec: Recorder, span: _Span, own: str, generic: str, shadows: Sequence[str]
) -> _Attr | None:
    """The text a tool observation's input or output is read from: the Langfuse attribute, else
    the generic one when no other carrier outranks it (documented)."""
    mine = _text(span, own)
    other = _text(span, generic)
    if mine is not None:
        if other is not None:
            rec.consume(other.vptr)
        return mine
    if other is None:
        return None
    if any(span.all(key) for key in shadows):
        rec.consume(other.vptr)
        return None
    return other


def _tool_calls(rec: Recorder, tools: Sequence[_Obs]) -> None:
    seq = 0
    for obs in tools:
        span = obs.span
        name = span.name
        if name is None or _MACHINE_KEY.fullmatch(name) is None:
            rec.warn("malformed_entry", "the tool observation has no usable name", path=span.base)
            continue
        name_path = _anchor(span)
        draft = Draft(ToolCallEvent)
        draft.set("name", name, Leg("/name", (name_path,)))
        source = _carrier(rec, span, _INPUT, _TOOL_ARGS, _INPUT_SHADOWS)
        if source is None:
            draft.set(
                "arguments_complete",
                False,
                Leg("/arguments_complete", (name_path,), "constant:False"),
            )
        else:
            stored, derivation = rec.text(str(source.value), source.vptr)
            draft.set("arguments_raw", stored, Leg("/arguments_raw", (source.vptr,), derivation))
            parsed: Any = None
            try:
                parsed = loads_strict(str(source.value))
            except (ValueError, TracerSchemaError, RecursionError):
                parsed = None
            complete = isinstance(parsed, dict) and derivation is None
            if complete:
                draft.set(
                    "arguments",
                    parsed,
                    *(
                        Leg("/arguments" + leaf, (chain(source.vptr, leaf),))
                        for leaf, _ in scalar_leaves(parsed)
                    ),
                )
            else:
                rec.warn(
                    "tool_arguments_incomplete",
                    "the arguments are not one whole JSON object",
                    path=source.vptr,
                    raw=raw_text(source.value),
                )
            draft.set(
                "arguments_complete",
                complete,
                Leg("/arguments_complete", (source.vptr,), "source_is_json_object"),
            )
        output = _carrier(rec, span, _OUTPUT, _TOOL_RESULT, _OUTPUT_SHADOWS)
        if output is not None:
            text, derivation = rec.text(str(output.value), output.vptr)
            draft.set("result_raw", text, Leg("/result_raw", (output.vptr,), derivation))
            draft.set("status", "completed", Leg("/status", (output.vptr,), "constant:completed"))
        else:
            draft.set("status", "unknown", Leg("/status", (name_path,), "constant:unknown"))
        draft.set("seq", seq, Leg("/seq", (_order_source(span),), "order_index"))
        draft.set(
            "source", "agent_telemetry", Leg("/source", (name_path,), "constant:agent_telemetry")
        )
        if rec.commit(draft, where=span.base) is not None:
            seq += 1


# ------------------------------------------------------------------------------ transcript


def _transcript(rec: Recorder, generations: Sequence[_Obs], privacy: Mapping[str, Any]) -> None:
    transcript_id = rec.new_id(Transcript)
    made = 0
    for obs in generations:
        span = obs.span
        output = _text(span, _OUTPUT)
        if output is None:
            continue
        ok, parsed = _loads(str(output.value))
        if not ok:
            rec.warn(
                "observation_json_unparseable",
                "the output is not JSON; it is not read as an agent line",
                path=output.vptr,
                raw=str(output.value),
            )
            continue
        if _is_text(parsed):
            text, where = str(parsed), chain(output.vptr, "")
        elif (
            isinstance(parsed, dict)
            and parsed.get("role") == "assistant"
            and _is_text(parsed.get("content"))
        ):
            text, where = str(parsed["content"]), chain(output.vptr, "/content")
        else:
            continue
        stored, derivation = rec.text(text, where)
        name_path = _anchor(span)
        draft = Draft(Turn, {"transcript_id": transcript_id})
        draft.set("seq", made, Leg("/seq", (_order_source(span),), "order_index"))
        draft.set("role", "agent", Leg("/role", (name_path,), "constant:agent"))
        draft.set(
            "source", "agent_llm_output", Leg("/source", (name_path,), "constant:agent_llm_output")
        )
        draft.set("timing_source", "none", Leg("/timing_source", (name_path,), "constant:none"))
        draft.set("text", stored, Leg("/text", (where,), derivation))
        if rec.commit(draft, where=span.base) is not None:
            made += 1
    if made:
        draft = Draft(Transcript, {"privacy": privacy})
        draft.set("source", "agent_telemetry", Leg("/source", (), "constant:agent_telemetry"))
        rec.commit(draft, entity_id=transcript_id)


# ------------------------------------------------------------------------------ references


def _external_refs(rec: Recorder, spans: Sequence[_Span]) -> None:
    seen_traces: set[str] = set()
    seen_sessions: set[str] = set()
    for span in spans:
        trace = span.trace_id
        if trace is not None and trace not in seen_traces:
            seen_traces.add(trace)
            rec.external_ref("otel", "trace_id", trace, f"{span.base}/traceId")
        sessions = [a for key in ("langfuse.session.id", "session.id") for a in span.all(key)]
        chosen = next((a for a in sessions if a.kind == "stringValue" and a.value), None)
        for attr in sessions:
            rec.consume(attr.vptr)
        if chosen is None:
            continue
        value = str(chosen.value)
        if value not in seen_sessions:
            seen_sessions.add(value)
            rec.external_ref("langfuse", "session_id", value, chosen.vptr)


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
_MODELS = ", ".join(MODEL_KEYS)
_COST_SOURCE = "langfuse.observation.cost_details, gen_ai.usage.cost"

PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row(
        "StageSpan.stage",
        "derivation",
        "gen_ai.operation.name",
        "chat_operation_to_llm_stage",
        "chat, text_completion and generate_content are the llm stage (F2-M3)",
        OPERATION_CITATION,
    ),
    _row(
        "StageSpan.stage",
        "constant",
        "langfuse.observation.type, " + _MODELS,
        "constant:other",
        "any other model call is stage other with the Langfuse type as stage_raw",
    ),
    _row(
        "StageSpan.stage_raw",
        "source",
        "langfuse.observation.type",
        None,
        "generation or embedding, verbatim",
    ),
    _row(
        "StageSpan.stage_raw",
        "derivation",
        _MODELS,
        "observation_type_inferred_from_model",
        "a model attribute with no recognized type makes the observation a generation (F2-M2)",
        TYPE_CITATION,
    ),
    _row("StageSpan.provider", "source", "gen_ai.provider.name", None, "verbatim"),
    _row("StageSpan.model", "source", _MODELS, None, "the first non-empty text (F2-M3)"),
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
    _row("StageSpan.ttfb_ms", "absent", "langfuse.observation.completion_start_time", None, "F2-3"),
    _row("StageSpan.exchange_index", "absent", None, None, "Langfuse has no turns (F2-M8)"),
    _row("StageSpan.span_ref.system", "constant", "@spanId", "constant:otel", "OpenTelemetry"),
    _row("StageSpan.span_ref.kind", "constant", "@spanId", "constant:span_id", "the span id"),
    _row("StageSpan.span_ref.value", "source", "@spanId", None, "verbatim"),
    _row(
        "UsageRecord.domain",
        "constant",
        "span name",
        "constant:agent_under_test",
        "the agent's own call",
    ),
    _row(
        "UsageRecord.stage",
        "derivation",
        "gen_ai.operation.name",
        "chat_operation_to_llm_stage",
        "as the StageSpan",
        OPERATION_CITATION,
    ),
    _row(
        "UsageRecord.stage",
        "constant",
        "langfuse.observation.type, " + _MODELS,
        "constant:other",
        "as the StageSpan",
    ),
    _row("UsageRecord.provider", "source", "gen_ai.provider.name", None, "verbatim"),
    _row("UsageRecord.model", "source", _MODELS, None, "as the StageSpan"),
    _row("UsageRecord.source", "constant", "span name", "constant:telemetry", "telemetry"),
    _row(
        "UsageRecord.occurred_at",
        "derivation",
        "@startTimeUnixNano",
        "unix_nano_to_utc_us",
        "truncated to the microsecond",
    ),
    *(
        _row(
            "UsageRecord.quantities.*.unit",
            "constant",
            source,
            f"constant:{unit}",
            f"the unit of {source}",
        )
        for source, unit in (
            ("gen_ai.usage.input_tokens, langfuse.observation.usage_details", "input_tokens"),
            (
                "gen_ai.usage.cache_read.input_tokens, langfuse.observation.usage_details",
                "cache_read_input_tokens",
            ),
            ("gen_ai.usage.cache_creation.input_tokens", "cache_write_input_tokens"),
            ("gen_ai.usage.output_tokens, langfuse.observation.usage_details", "output_tokens"),
        )
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, "
        "gen_ai.usage.cache_read.input_tokens, gen_ai.usage.cache_creation.input_tokens",
        "otlp_int_to_int",
        "the count as reported",
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "langfuse.observation.usage_details",
        "json_int_lexeme_to_int",
        "the exclusive bucket as stored by Langfuse (F2-M4)",
    ),
    _row(
        "UsageRecord.quantities.*.count",
        "derivation",
        "gen_ai.usage.input_tokens and the cache counts",
        "input_tokens_gross_minus_cache",
        "Langfuse subtracts cache reads and writes from the OpenTelemetry input count (F2-M4)",
        USAGE_CITATION,
    ),
    _row(
        "UsageRecord.raw.*",
        "derivation",
        "gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, "
        "gen_ai.usage.cache_read.input_tokens, gen_ai.usage.cache_creation.input_tokens",
        "otlp_int_to_int",
        "the usage attributes as received",
    ),
    _row(
        "UsageRecord.raw.*",
        "source",
        "langfuse.observation.usage_details",
        None,
        "the attribute as received",
    ),
    _row(
        "UsageRecord.raw.*",
        "derivation",
        "langfuse.observation.usage_details",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row("CostRecord.domain", "constant", _COST_SOURCE, "constant:agent_under_test", "the agent"),
    _row("CostRecord.status", "constant", _COST_SOURCE, "constant:available", "an imported amount"),
    _row(
        "CostRecord.status",
        "constant",
        _COST_SOURCE,
        "constant:not_available",
        "the importer refused the amount or the currency",
    ),
    _row(
        "CostRecord.amount.amount",
        "derivation",
        "langfuse.observation.cost_details, gen_ai.usage.cost",
        "amount_lexeme_canonical",
        "the lexeme of the total in its canonical spelling (N59)",
    ),
    _row(
        "CostRecord.amount.amount",
        "derivation",
        "gen_ai.usage.cost",
        "float_amount_canonical",
        "the shortest decimal text of the double",
    ),
    _row(
        "CostRecord.amount.currency",
        "derivation",
        _COST_SOURCE,
        "contract_currency_usd",
        "the documented currency of the Langfuse cost fields (P-53)",
        LANGFUSE_COST_CONTRACT,
    ),
    _row("CostRecord.basis", "constant", _COST_SOURCE, "constant:reported", "reported by Langfuse"),
    _row(
        "CostRecord.reported_by",
        "constant",
        _COST_SOURCE,
        "constant:telemetry_platform",
        "Langfuse is the telemetry platform",
    ),
    _row(
        "CostRecord.na_reason",
        "constant",
        _COST_SOURCE,
        "constant:currency_not_reported",
        "no cited contract",
    ),
    _row(
        "CostRecord.na_reason",
        "constant",
        _COST_SOURCE,
        "constant:amount_unparseable",
        "the amount is not a plain non-negative decimal (N59)",
    ),
    _row(
        "CostRecord.reported_raw.reported_by",
        "constant",
        _COST_SOURCE,
        "constant:telemetry_platform",
        "Langfuse is the telemetry platform",
    ),
    _row(
        "CostRecord.reported_raw.amount_raw",
        "source",
        _COST_SOURCE,
        None,
        "the lexeme or text as sent",
    ),
    _row(
        "CostRecord.reported_raw.amount_raw",
        "derivation",
        "gen_ai.usage.cost",
        "float_amount_canonical",
        "the shortest decimal text of the double",
    ),
    _row(
        "CostRecord.reported_raw.amount_raw",
        "derivation",
        _COST_SOURCE,
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "CostRecord.reported_raw.currency_raw",
        "derivation",
        _COST_SOURCE,
        "contract_currency_usd",
        "the documented currency of the Langfuse cost fields (P-53)",
        LANGFUSE_COST_CONTRACT,
    ),
    _row(
        "CostRecord.reported_raw.currency_source",
        "constant",
        _COST_SOURCE,
        "constant:api_contract",
        "the currency comes from the cited contract",
    ),
    _row(
        "CostRecord.reported_raw.currency_source",
        "constant",
        _COST_SOURCE,
        "constant:none",
        "no cited contract",
    ),
    _row("ToolCallEvent.name", "source", "span name of a tool observation", None, "verbatim"),
    _row(
        "ToolCallEvent.arguments_raw",
        "source",
        "langfuse.observation.input, gen_ai.tool.call.arguments",
        None,
        "verbatim, never repaired",
    ),
    _row(
        "ToolCallEvent.arguments_raw",
        "derivation",
        "langfuse.observation.input, gen_ai.tool.call.arguments",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "ToolCallEvent.arguments.*",
        "source",
        "langfuse.observation.input, gen_ai.tool.call.arguments",
        None,
        "only when the text is one whole JSON object",
    ),
    _row(
        "ToolCallEvent.arguments_complete",
        "derivation",
        "langfuse.observation.input, gen_ai.tool.call.arguments",
        "source_is_json_object",
        "true only for one whole JSON object",
    ),
    _row(
        "ToolCallEvent.arguments_complete",
        "constant",
        "span name",
        "constant:False",
        "never inferred true",
    ),
    _row(
        "ToolCallEvent.result_raw",
        "source",
        "langfuse.observation.output, gen_ai.tool.call.result",
        None,
        "verbatim",
    ),
    _row(
        "ToolCallEvent.result_raw",
        "derivation",
        "langfuse.observation.output, gen_ai.tool.call.result",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row("ToolCallEvent.result_complete", "absent", None, None, "not stated by the source"),
    _row("ToolCallEvent.result_error", "absent", None, None, "not stated by the source"),
    _row("ToolCallEvent.external_call_id", "absent", None, None, "not stated by the source"),
    _row(
        "ToolCallEvent.status",
        "constant",
        "langfuse.observation.output, gen_ai.tool.call.result",
        "constant:completed",
        "an output is stated",
    ),
    _row(
        "ToolCallEvent.status",
        "constant",
        "span name",
        "constant:unknown",
        "no output is stated, nothing is said about success",
    ),
    _row(
        "ToolCallEvent.seq",
        "derivation",
        "@startTimeUnixNano",
        "order_index",
        "order of span start, ties in document order",
    ),
    _row("ToolCallEvent.source", "constant", "span name", "constant:agent_telemetry", "telemetry"),
    _row(
        "ExternalRef.value",
        "source",
        "langfuse.session.id, session.id",
        None,
        "the session id, once per distinct value; langfuse.session.id wins (F2-M6, F2-M9)",
    ),
    _row("ExternalRef.value", "source", "@traceId", None, "the trace id, once per distinct value"),
    _row("Transcript.source", "constant", None, "constant:agent_telemetry", "Langfuse telemetry"),
    _row("Transcript.declared_language", "absent", None, None, "never filled from the source"),
    _row(
        "Turn.seq",
        "derivation",
        "@startTimeUnixNano",
        "order_index",
        "order of span start, ties in document order",
    ),
    _row("Turn.role", "constant", "span name", "constant:agent", "the output of a generation"),
    _row(
        "Turn.source",
        "constant",
        "span name",
        "constant:agent_llm_output",
        "output of a generation",
    ),
    _row(
        "Turn.timing_source",
        "constant",
        "span name",
        "constant:none",
        "a span marks when the service worked, not when someone spoke",
    ),
    _row(
        "Turn.text",
        "source",
        "langfuse.observation.output",
        None,
        "the JSON string or the content of an assistant message",
    ),
    _row(
        "Turn.text",
        "derivation",
        "langfuse.observation.output",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
)
