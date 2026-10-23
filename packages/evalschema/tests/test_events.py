# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-10c: ToolCallEvent, StageSpan, EvidenceRef and IngestWarning (ADR 0002, 0003, 0012).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.events``:
the entities ``ToolCallEvent`` (``tcl``) and ``StageSpan`` (``spn``), the value objects
``EvidenceRef`` and ``IngestWarning``, and two pure checks that need several records:
``check_tool_call_seqs(events) -> None`` (``seq`` is unique among the events of one run) and
``check_evidence_resolves(refs, records) -> None`` (every reference names the ``id`` of a supplied
record). Both raise ``ValueError``.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import property_enum, registered_class, schema_accepts, schema_of
from ._support_b3 import (
    DEVANAGARI,
    OTHER_LABEL_RAW,
    QUOTE_MARK,
    assert_input_hidden,
    assert_rejected3,
    entity_class3,
    evidence_ref_doc,
    ingest_warning_doc,
    make3,
    marked_id,
    marked_key,
    marked_set,
    new_id,
    span_doc,
    tool_call_doc,
)

TRUNCATED = (
    '{"city": "Pu',
    '{"city": "Pune", "tags": ["a", "b"',
    '{"city": ',
    "{",
    '{"a": {"b": 1}',
    '{"city": "Pune",}',
)


def _call(**over: Any) -> Any:
    return make3("ToolCallEvent", **over)


def _reject_call(field: str | None = None, **over: Any) -> None:
    assert_rejected3(entity_class3("ToolCallEvent"), tool_call_doc(**over), field=field)


# -------------------------------------------------------------------------------- tool calls


def test_truncated_args_not_complete_not_parsed() -> None:
    """A-29, ADR 0012 I1, SCOPE P1-10c acceptance: a truncated payload never yields
    ``arguments_complete``. A call whose ``arguments_raw`` is cut off is stored with
    ``arguments_complete=False`` and ``arguments=None`` (the raw string kept exactly, never
    repaired); claiming ``arguments_complete=True`` for raw text that is not a whole JSON object,
    or attaching a parsed ``arguments`` to it, is rejected."""
    for raw in TRUNCATED:
        call = _call(arguments_raw=raw, arguments=None, arguments_complete=False)
        assert call.arguments is None
        assert call.arguments_complete is False
        assert call.arguments_raw == raw  # stored exactly, not closed or trimmed
        assert json.loads(call.model_dump_json())["arguments_raw"] == raw
        _reject_call(arguments_raw=raw, arguments=None, arguments_complete=True)
        _reject_call(arguments_raw=raw, arguments={"city": "Pu"}, arguments_complete=False)
        _reject_call(arguments_raw=raw, arguments={"city": "Pu"}, arguments_complete=True)
    for not_an_object in ("[1, 2]", '"text"', "42", "null"):
        _reject_call(arguments_raw=not_an_object, arguments=None, arguments_complete=True)
    whole = _call()
    assert whole.arguments == {"city": "Pune"}
    assert whole.arguments_complete is True
    nested = _call(
        arguments_raw='{"a": {"b": [1, 2, {"c": null}]}}',
        arguments={"a": {"b": [1, 2, {"c": None}]}},
    )
    assert nested.arguments_complete is True


def test_arguments_present_implies_complete() -> None:
    """ADR 0012 I1: ``arguments`` (the parsed object) is present only when ``arguments_complete`` is
    true. ``arguments_complete`` is required (no default, never inferred) and a strict boolean; a
    call may carry the raw string alone."""
    event = entity_class3("ToolCallEvent")
    _reject_call("arguments", arguments={"city": "Pune"}, arguments_complete=False)
    doc = tool_call_doc()
    assert_rejected3(
        event,
        {k: v for k, v in doc.items() if k != "arguments_complete"},
        field="arguments_complete",
    )
    for bad in (None, "true", 1, 0, "yes"):
        _reject_call("arguments_complete", arguments_complete=bad)
    only_raw = _call(arguments=None)
    assert only_raw.arguments is None
    assert only_raw.arguments_complete is True
    no_args = _call(arguments_raw=None, arguments=None)
    assert (no_args.arguments_raw, no_args.arguments) == (None, None)
    for bad_arguments in ([1], "x", 3, True):
        _reject_call("arguments", arguments=bad_arguments)
    assert schema_accepts(event, doc)
    assert "arguments_complete" in schema_of(event)["required"]


def test_cancelled_status() -> None:
    """ADR 0012 I2: ``status`` is completed, cancelled or unknown (an ``enum``); a cancelled call
    needs no result, and 'we do not know whether it completed' is ``unknown``, not a flag."""
    event = entity_class3("ToolCallEvent")
    assert set(property_enum(event, "status")) == {"completed", "cancelled", "unknown"}
    cancelled = _call(status="cancelled", result_raw=None, result_complete=None, result_error=None)
    assert cancelled.status == "cancelled"
    assert cancelled.result_raw is None
    assert _call(status="unknown", result_raw=None, result_complete=None).status == "unknown"
    for bad in ("canceled", "cancelled ", "running", "error", "errored", "ok", "", None):
        _reject_call("status", status=bad)
    assert "cancelled" not in event.model_fields  # not a boolean flag
    _reject_call(None, cancelled=True)


def test_result_optional() -> None:
    """ADR 0012 I3: a call with no result is valid (``result_raw=None``, ``result_complete=None``,
    ``result_error=None``); the three result fields survive a JSON round trip as given."""
    event = entity_class3("ToolCallEvent")
    bare = _call(result_raw=None, result_complete=None, result_error=None)
    assert (bare.result_raw, bare.result_complete, bare.result_error) == (None, None, None)
    assert event.model_validate_json(bare.model_dump_json()) == bare
    raw = '{"ok": true, "balance": "12.50"}'
    full = _call(result_raw=raw, result_complete=True)
    assert full.result_raw == raw
    cut = _call(result_raw='{"ok": tr', result_complete=False)
    assert cut.result_complete is False
    # the result is the exact source string and is not parsed into the model
    assert not {"result", "result_parsed"} & set(event.model_fields)


def test_result_complete_requires_result_raw() -> None:
    """ADR 0012 I3: ``result_complete`` is present only together with ``result_raw``. A value of
    ``false`` without a result is just as wrong as ``true``."""
    for flag in (True, False):
        _reject_call("result_complete", result_raw=None, result_complete=flag)
    for bad in ("true", 1, 0):
        _reject_call("result_complete", result_complete=bad)
    ok = _call(result_raw='{"ok": true}', result_complete=True)
    assert ok.result_complete is True
    assert _call(result_raw='{"ok": true}', result_complete=None).result_complete is None


def test_result_error_none_when_not_reported() -> None:
    """P-32 (ADR 0012 Decision 1, I18): ``result_error`` is true, false or ``None`` (the source
    did not report it); ``None`` is distinct from ``False`` and never defaults to false, and
    ``status`` keeps meaning only 'did the call finish': a completed call may have errored."""
    event = entity_class3("ToolCallEvent")
    base = {k: v for k, v in tool_call_doc().items() if k != "result_error"}
    omitted = event.model_validate(base)
    assert omitted.result_error is None
    for value in (None, False, True):
        loaded = _call(result_error=value)
        assert loaded.result_error is value
        assert event.model_validate_json(loaded.model_dump_json()).result_error is value
        assert json.loads(loaded.model_dump_json())["result_error"] is value
    errored = _call(status="completed", result_error=True)
    assert (errored.status, errored.result_error) == ("completed", True)
    bad_flags: list[Any] = [0, 1, "false", "error", []]
    for bad_flag in bad_flags:
        _reject_call("result_error", result_error=bad_flag)
    assert "errored" not in property_enum(event, "status")


def test_tool_call_seq_unique_per_run() -> None:
    """ADR 0012 I4: ``seq`` (a non-negative integer, the order of calls in a run) is unique among
    the tool calls of one run (``check_tool_call_seqs``); gaps are fine."""
    check = api("events").check_tool_call_seqs
    calls = [_call(seq=seq, external_call_id=f"call_{seq}") for seq in (0, 1, 2, 7)]
    check(calls)
    check([])
    with pytest.raises(ValueError, match=r"\b1\b"):
        check([*calls, _call(seq=1)])
    check(calls[:2])
    check([_call(seq=1)])  # another run's events are checked in their own list
    for bad in (-1, 1.5, "0", True, None):
        _reject_call("seq", seq=bad)
    doc = tool_call_doc()
    assert_rejected3(
        entity_class3("ToolCallEvent"), {k: v for k, v in doc.items() if k != "seq"}, field="seq"
    )


def test_tool_call_event_shape() -> None:
    """Proposed name (ADR 0012 Decision 1, ADR 0001 I12): ``name`` is a MachineKey (open: unseen
    tool names are fine); ``external_call_id`` is the provider's identifier, opaque and stored
    verbatim as data (a v4 UUID or any text, never a typed ID of ours and never rewritten); the
    event carries no privacy marker (it takes its run's derived value) and no secret-like
    field."""
    event = entity_class3("ToolCallEvent")
    for name in ("book_flight_v2", "Org/Tool:v2", "tool.name@1"):
        assert _call(name=name).name == name
    for bad in ("two words", "", DEVANAGARI, "x" * 257, None):
        _reject_call("name", name=bad)
    foreign = str(uuid.UUID("3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b"))  # a v4 UUID, kept as data
    for external in (foreign, "call_8f3a2c", "  spaced id  ", "toolu_01A", "run_" + foreign):
        loaded = _call(external_call_id=external)
        assert loaded.external_call_id == external
        assert json.loads(loaded.model_dump_json())["external_call_id"] == external
    assert _call(external_call_id=None).external_call_id is None
    assert _call().id != _call().id
    assert str(_call().id).startswith("tcl_")
    _reject_call("source", source=None)  # required; the set itself: ..._source_closed_set
    assert "privacy" not in event.model_fields
    original = _call()
    assert event.model_validate_json(original.model_dump_json()) == original
    assert schema_accepts(event, json.loads(original.model_dump_json()))


def test_tool_call_event_source_closed_set() -> None:
    """O2 (owner, revision 12; ADR 0003 Decision 1, ADR 0012 Decision 1; api-surface 22.9 item
    13): ``ToolCallEvent.source`` is the closed set ``agent_export``, ``agent_telemetry``,
    ``observer`` (the spellings of ``Transcript.source`` minus the producers that cannot observe
    a tool call). Each member validates and round-trips through JSON and the JSON Schema. Any
    other spelling is rejected at ``source`` by pydantic and by the JSON Schema, and the property
    is an ``enum``, never a ``const``. Proposed name (SCOPE section 5, P1-10c)."""
    event = entity_class3("ToolCallEvent")
    members = ("agent_export", "agent_telemetry", "observer")
    for member in members:
        loaded = _call(source=member)
        assert loaded.source == member
        assert event.model_validate_json(loaded.model_dump_json()) == loaded
        assert json.loads(loaded.model_dump_json())["source"] == member
        assert schema_accepts(event, json.loads(loaded.model_dump_json()))
    for bad in (
        "telemetry",
        "otel",
        "webhook",
        "Agent_Telemetry",
        "agent-telemetry",
        " agent_telemetry",
        "our_stt",
        "script",
        "human_reference",
        "unknown",
        "",
    ):
        _reject_call("source", source=bad)
        assert not schema_accepts(event, tool_call_doc(source=bad)), bad
    missing = {k: v for k, v in tool_call_doc().items() if k != "source"}
    assert_rejected3(event, missing, field="source")  # required: no default, never inferred
    assert "source" in schema_of(event)["required"]
    node = schema_of(event)["properties"]["source"]
    assert node["type"] == "string"
    assert sorted(node["enum"]) == sorted(members)
    assert "const" not in node
    assert sorted(property_enum(event, "source")) == sorted(members)


# ----------------------------------------------------------------------------------- spans


def test_stage_span_nonneg() -> None:
    """ADR 0002 I5, ADR 0012 I5: ``duration_ms`` and ``ttfb_ms`` are non-negative integers
    (zero is fine), ``start`` is a UTC instant, and a value the source does not provide stays
    absent (never derived)."""
    span = entity_class3("StageSpan")
    assert make3("StageSpan", duration_ms=0, ttfb_ms=0).duration_ms == 0
    bare = make3("StageSpan", duration_ms=None, ttfb_ms=None, start=None)
    assert (bare.duration_ms, bare.ttfb_ms, bare.start) == (None, None, None)
    only_ttfb = {k: v for k, v in span_doc().items() if k not in ("duration_ms", "start")}
    assert span.model_validate(only_ttfb).duration_ms is None
    for name in ("duration_ms", "ttfb_ms"):
        for bad in (-1, 1.5, "10", True):
            assert_rejected3(span, span_doc(**{name: bad}), field=name)
    for bad in ("2026-10-22T10:00:00", 1_790_000_000, "now"):
        assert_rejected3(span, span_doc(start=bad), field="start")
    loaded = make3("StageSpan", start="2026-10-22T15:30:00+05:30")
    assert json.loads(loaded.model_dump_json())["start"] == "2026-10-22T10:00:00.000000Z"
    properties = schema_of(span)["properties"]
    for name in ("duration_ms", "ttfb_ms"):
        assert '"minimum": 0' in json.dumps(properties[name])
    assert "latency_ms" not in span.model_fields  # latency is never an attribute of the span


def test_stage_span_other_keeps_stage_raw() -> None:
    """P-05 (ADR 0003 Decision 1, ADR 0012 I19): ``StageSpan.stage`` is the usage-stage set
    (stt, llm, tts, judge, sim_caller_llm, sim_caller_tts, realtime, other); when the stage is
    ``other`` the source label is kept in ``stage_raw`` (a MachineKey, verbatim) so latency and
    cost join on one key; ``stage_raw`` is absent for every other stage."""
    span = entity_class3("StageSpan")
    stages = {"stt", "llm", "tts", "judge", "sim_caller_llm", "sim_caller_tts", "realtime", "other"}
    assert set(property_enum(span, "stage")) == stages
    for stage in stages - {"other"}:
        loaded = make3("StageSpan", stage=stage)
        assert (loaded.stage, loaded.stage_raw) == (stage, None)
        assert_rejected3(span, span_doc(stage=stage, stage_raw="speech_to_text"), field="stage_raw")
    other = make3("StageSpan", stage="other", stage_raw="speech.to-text/v2")
    assert (other.stage, other.stage_raw) == ("other", "speech.to-text/v2")
    assert_rejected3(span, span_doc(stage="other"), field="stage_raw")
    assert_rejected3(span, span_doc(stage="other", stage_raw=None), field="stage_raw")
    for bad in ("two words", "", DEVANAGARI):
        assert_rejected3(span, span_doc(stage="other", stage_raw=bad), field="stage_raw")
    bad_stages: list[Any] = ["STT", "speech_to_text", "agent", "", None]
    for bad_stage in bad_stages:
        assert_rejected3(span, span_doc(stage=bad_stage), field="stage")


def test_stage_span_shape() -> None:
    """Proposed name (ADR 0012 Decision 2, Decision 6): ``span_ref`` is required and points to the
    raw span as an ``ExternalRef`` (trace and span IDs are foreign identifiers, never our IDs);
    ``provider`` and ``model`` are open MachineKeys; ``exchange_index`` is a non-negative
    integer; the span has no privacy marker."""
    span = entity_class3("StageSpan")
    doc = span_doc()
    assert_rejected3(span, {k: v for k, v in doc.items() if k != "span_ref"}, field="span_ref")
    for bad in (
        "00f067aa0ba902b7",
        None,
        {"system": "otel"},
        {"system": "otel", "kind": "span_id"},
    ):
        assert_rejected3(span, span_doc(span_ref=bad), field="span_ref")
    loaded = make3("StageSpan")
    assert loaded.span_ref.value == "00f067aa0ba902b7"
    assert json.loads(loaded.model_dump_json())["span_ref"]["system"] == "otel"
    assert (
        make3("StageSpan", provider="some-new-vendor", model="org/Model-Name:v2").model
        == "org/Model-Name:v2"
    )
    bad_indexes: list[Any] = [-1, 1.5, "0", True]
    for bad_index in bad_indexes:
        assert_rejected3(span, span_doc(exchange_index=bad_index), field="exchange_index")
    assert make3("StageSpan", exchange_index=3).exchange_index == 3
    assert "privacy" not in span.model_fields
    assert schema_accepts(span, json.loads(loaded.model_dump_json()))


# --------------------------------------------------------------------------------- evidence


def test_evidence_ref_resolves_in_bundle() -> None:
    """ADR 0012 Decision 3, I6 (the bundle half is P1-14): an ``EvidenceRef{kind, ref_id,
    start_ms?, end_ms?}`` points from a result to evidence of the same bundle. ``kind`` is turn,
    exchange, tool_call, span or artifact_range, and ``ref_id`` is the typed ID of the matching
    kind (turn_, exc_, tcl_, spn_, art_); a mismatch is rejected. ``check_evidence_resolves``
    rejects a reference whose ID is not the ``id`` of a supplied record."""
    evidence = registered_class("EvidenceRef")
    check = api("events").check_evidence_resolves
    turn = make3("Turn")
    exchange = make3("Exchange")
    call = make3("ToolCallEvent")
    span = make3("StageSpan")
    artifact = make3("Artifact")
    records = [turn, exchange, call, span, artifact]
    refs = [
        evidence.model_validate(evidence_ref_doc("turn", str(turn.id))),
        evidence.model_validate(evidence_ref_doc("exchange", str(exchange.id))),
        evidence.model_validate(evidence_ref_doc("tool_call", str(call.id))),
        evidence.model_validate(evidence_ref_doc("span", str(span.id))),
        evidence.model_validate(
            evidence_ref_doc("artifact_range", str(artifact.id), start_ms=1000, end_ms=2500)
        ),
    ]
    check(refs, records)
    check([], [])
    for kind, wrong in (
        ("turn", "tcl"),
        ("exchange", "turn"),
        ("tool_call", "spn"),
        ("span", "exc"),
        ("artifact_range", "turn"),
        ("turn", "run"),
    ):
        with pytest.raises(ValidationError):
            evidence.model_validate({"kind": kind, "ref_id": new_id(wrong)})
    for kind in ("turn", "span"):
        with pytest.raises(ValidationError):
            evidence.model_validate({"kind": kind, "ref_id": "not an id"})
    dangling = evidence.model_validate(evidence_ref_doc("turn"))
    with pytest.raises(ValueError, match=str(dangling.ref_id)):
        check([*refs, dangling], records)
    with pytest.raises(ValueError, match=str(artifact.id)):
        check(refs, records[:-1])  # the artifact record is missing
    loaded = evidence.model_validate_json(refs[0].model_dump_json())
    assert loaded == refs[0]
    assert json.loads(refs[0].model_dump_json())["ref_id"] == str(turn.id)


def test_evidence_ref_shape() -> None:
    """Proposed name (ADR 0012 Decision 3, ADR 0003): the kind set is exactly turn, exchange,
    tool_call, span, artifact_range; offsets are ``Ms`` values measured from sample 0 of the
    artifact, with ``end_ms >= start_ms``; the reference has no identity fields; unknown fields
    are rejected."""
    evidence = registered_class("EvidenceRef")
    assert set(property_enum(evidence, "kind")) == {
        "turn",
        "exchange",
        "tool_call",
        "span",
        "artifact_range",
    }
    ranged = evidence.model_validate(evidence_ref_doc("artifact_range", start_ms=0, end_ms=0))
    assert (ranged.start_ms, ranged.end_ms) == (0, 0)
    assert evidence.model_validate(evidence_ref_doc("turn")).start_ms is None
    with pytest.raises(ValidationError):
        evidence.model_validate(evidence_ref_doc("artifact_range", start_ms=5, end_ms=4))
    for bad in (-1, 1.5, "1", True):
        with pytest.raises(ValidationError):
            evidence.model_validate(evidence_ref_doc("artifact_range", start_ms=bad))
    for bad_kind in ("Turn", "message", "", None):
        with pytest.raises(ValidationError):
            evidence.model_validate({"kind": bad_kind, "ref_id": new_id("turn")})
    with pytest.raises(ValidationError):
        evidence.model_validate({**evidence_ref_doc("turn"), "note": "x"})
    assert not {"id", "schema_version", "created_at", "metadata"} & set(evidence.model_fields)
    assert schema_accepts(evidence, evidence_ref_doc("span"))


# ------------------------------------------------------------------------------- warnings


def test_ingest_warning_shape() -> None:
    """ADR 0012 I16, Decision 7: ``IngestWarning{code, path?, raw?, message}``: ``code`` is a
    MachineKey, ``message`` is required and non-empty, ``path`` and ``raw`` are optional verbatim
    text (the raw value of what could not be mapped faithfully, kept code point for code point),
    unknown fields are rejected, and it is a value object. It can carry a
    ``LanguageLabelWarning`` 1:1 (same code, same raw)."""
    warning = registered_class("IngestWarning")
    loaded = warning.model_validate(ingest_warning_doc())
    assert (loaded.code, loaded.path, loaded.raw) == (
        "language_label_normalized",
        "/detected_language",
        OTHER_LABEL_RAW,
    )
    minimal = warning.model_validate({"code": "decimal_normalized", "message": "x"})
    assert (minimal.path, minimal.raw) == (None, None)
    assert warning.model_validate_json(loaded.model_dump_json()) == loaded
    for raw in (" hi_IN\n", "", "2026-10-22 10:00:00", DEVANAGARI, "1.50"):
        assert warning.model_validate(ingest_warning_doc(raw=raw)).raw == raw
    for missing in ("code", "message"):
        with pytest.raises(ValidationError):
            warning.model_validate({k: v for k, v in ingest_warning_doc().items() if k != missing})
    for bad_code in ("two words", "", DEVANAGARI, None):
        with pytest.raises(ValidationError):
            warning.model_validate(ingest_warning_doc(code=bad_code))
    for bad_message in ("", None, 3):
        with pytest.raises(ValidationError):
            warning.model_validate(ingest_warning_doc(message=bad_message))
    with pytest.raises(ValidationError):
        warning.model_validate(ingest_warning_doc(severity="high"))
    assert not {"id", "schema_version", "created_at", "metadata"} & set(warning.model_fields)
    label = api("language").observe_language_label(OTHER_LABEL_RAW).warnings[0]
    carried = warning.model_validate(
        {"code": label.code, "raw": label.raw, "message": "label normalized"}
    )
    assert (carried.code, carried.raw) == (label.code, label.raw)
    assert schema_accepts(warning, ingest_warning_doc())


def test_event_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed
    ID, MachineKey and closed-set member on ``ToolCallEvent`` and a rejected MachineKey on
    ``IngestWarning`` do not appear in ``str``, ``repr``, ``errors(include_input=False)`` or
    ``json(include_input=False)``, through ``model_validate`` and ``model_validate_json``, and the
    error is still located at the field. Proposed name (SCOPE section 5, P1-10c)."""
    event = entity_class3("ToolCallEvent")
    warning = registered_class("IngestWarning")
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("typed ID", event, tool_call_doc(id=marked_id("tcl")), "id"),
        ("MachineKey", event, tool_call_doc(name=marked_key()), "name"),
        ("ClosedSet", event, tool_call_doc(status=marked_set()), "status"),
        ("ClosedSet", event, tool_call_doc(source=marked_set()), "source"),
        ("MachineKey", warning, ingest_warning_doc(code=marked_key()), "code"),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, QUOTE_MARK, field=field, label=label)
