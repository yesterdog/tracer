# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Span export as OTLP/JSON (protobuf JSON mapping; ids as lowercase hex per the OTLP spec)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    raise TypeError(f"unsupported attribute type {type(v).__name__}")


def _attrs(attrs: Any) -> list[dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in (attrs or {}).items()]


def _span(s: ReadableSpan) -> dict[str, Any]:
    ctx = s.get_span_context()
    if ctx is None:
        raise ValueError("span without a span context")
    out: dict[str, Any] = {
        "traceId": format(ctx.trace_id, "032x"),
        "spanId": format(ctx.span_id, "016x"),
        "name": s.name,
        "kind": int(s.kind.value) + 1,
        "startTimeUnixNano": str(s.start_time),
        "endTimeUnixNano": str(s.end_time),
        "attributes": _attrs(s.attributes),
        "events": [
            {"timeUnixNano": str(e.timestamp), "name": e.name, "attributes": _attrs(e.attributes)}
            for e in s.events
        ],
        "status": {"code": int(s.status.status_code.value)},
    }
    if s.parent is not None:
        out["parentSpanId"] = format(s.parent.span_id, "016x")
    if s.status.description:
        out["status"]["message"] = s.status.description
    return out


def spans_to_otlp_json(spans: Sequence[ReadableSpan]) -> dict[str, Any]:
    groups: dict[tuple[str, str | None], list[ReadableSpan]] = {}
    for s in spans:
        scope = s.instrumentation_scope
        groups.setdefault(
            (scope.name if scope else "", scope.version if scope else None), []
        ).append(s)
    resource = spans[0].resource if spans else None
    scope_spans = []
    for (name, version), items in groups.items():
        scope_obj: dict[str, Any] = {"name": name}
        if version:
            scope_obj["version"] = version
        scope_spans.append({"scope": scope_obj, "spans": [_span(s) for s in items]})
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attrs(resource.attributes if resource else {})},
                "scopeSpans": scope_spans,
            }
        ]
    }
