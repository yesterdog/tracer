# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 9: the mapping decisions that came after the converters were first written.

Auto-approved readings pinned here (numbers are the dev agent's; the pinned reading is in
docs/phase1/api-surface-mappings.md, section "Revision (batch 9)"):

1. F4 ``cancelled: true`` gives status ``cancelled`` through the cited derivation
   ``documented_cancelled_flag`` (never a bare ``constant:cancelled``).
2. The N64 merge (repeated usage attribute of one span, user-cited one-billable-event statement)
   also sums Decimal units (``metrics.audio_seconds``) exactly; without a complete citation the
   call is refused as N76: no UsageRecord, ``usage_quantities_not_merged``, a blocking gap row.
3. A placeholder model on an llm, stt or tts span keeps the usage with ``model`` None (M3).
5. A usage span with no provider gives ``malformed_entry`` AND a blocking gap row (data lost).
7. A repeated ``turn.number`` keeps the first turn span and warns ``duplicate_turn_number``; a
   turn span with no integer number gives ``malformed_entry``.
8. A '#' inside a JSON key of a source pointer is written ``%23`` (the '#' is the chain
   separator) and the independent resolver of the tests reads it back.

Everything here is hand-built input in the formats of the captures; nothing reads a private
fixture. Everything is ASCII.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from ._support_b6 import (
    check_no_fabrication,
    check_records_valid,
    check_warnings_quote_sources,
    mapping,
    otlp_doc,
    otlp_span,
    resolve_source,
    run_f1,
    run_f4,
)

TURN_ID = "a1a1a1a1a1a1a1a1"
RETRIEVED = re.compile(r"\d{4}-\d{2}-\d{2}")


def _turn(number: int | None = 1, *, span_id: str = TURN_ID, start_ns: int = 0, **kw: Any) -> Any:
    attrs: list[tuple[str, Any]] = [("turn.duration_seconds", kw.get("duration", 2.0))]
    if number is not None:
        attrs.insert(0, ("turn.number", number))
    return otlp_span(
        "turn",
        span_id,
        start_ns=start_ns or 1_792_757_168_000_000_000,
        end_ns=(start_ns or 1_792_757_168_000_000_000) + 2_000_000_000,
        attrs=attrs,
    )


def _stage(
    name: str,
    extra: Sequence[tuple[str, Any]],
    *,
    provider: str | None = "prov",
    model: str | None = "model-1",
    span_id: str = "c1c1c1c1c1c1c1c1",
    parent: str = TURN_ID,
) -> dict[str, Any]:
    base: list[tuple[str, Any]] = []
    if provider is not None:
        base.append(("gen_ai.provider.name", provider))
    if model is not None:
        base.append(("gen_ai.request.model", model))
    return otlp_span(
        name, span_id, parent=parent, start_ns=1_792_757_168_500_000_000, attrs=[*base, *extra]
    )


def _quantities(record: Any) -> dict[str, Any]:
    return {q.unit: q.count for q in record.quantities}


def _codes(result: Any) -> list[str]:
    return [w.code for w in result.warnings]


def _citation(**over: Any) -> Any:
    fields: dict[str, Any] = {
        "url": "https://example.com/contract/usage",
        "retrieved_at": "2026-10-23",
        "statement": "repeated usage attributes of one span are parts of one billable event",
    }
    fields.update(over)
    return mapping("common").Citation(**fields)


def _complete(citation: Any) -> bool:
    return bool(
        citation is not None
        and citation.url
        and citation.url.startswith("https://")
        and RETRIEVED.fullmatch(citation.retrieved_at or "")
        and citation.statement.strip()
    )


# ============================================================================ 1: F4 cancelled


def _cancel_doc() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "book it"},
        {"role": "tool_call", "name": "book", "arguments": {"n": 1}, "cancelled": True},
        {"role": "tool_call", "name": "book", "arguments": {"n": 2}, "cancelled": False},
        {"role": "assistant", "content": "done"},
    ]


def test_f4_cancelled_status_is_a_cited_documented_derivation() -> None:
    """Decision 1: ``cancelled: true`` is the observer's documented statement (conversation
    observer, pipecat-ai 1.13.0), so the status ``cancelled`` is a derivation named
    ``documented_cancelled_flag``, not a constant the mapping chose. The trace entry of that
    status names the derivation, points at the ``cancelled`` field and carries a complete
    citation (https URL, retrieved_at date, statement); the provenance table has the row with the
    same derivation and a complete citation; no entry and no row uses ``constant:cancelled``.
    The generic no-fabrication check also passes (it re-computes the derivation)."""
    doc = _cancel_doc()
    result = run_f4(doc)
    module = mapping("pipecat_conversation")
    cancelled = next(c for c in result.tool_calls if c.status == "cancelled")
    entry = next(e for e in result.trace if e.record == str(cancelled.id) and e.field == "/status")
    assert entry.derivation == "documented_cancelled_flag"
    assert entry.sources == ("/1/cancelled",)
    assert resolve_source(doc, entry.sources[0]) is True
    assert _complete(entry.citation)
    rows = [
        r
        for r in module.PROVENANCE_TABLE
        if r.target == "ToolCallEvent.status" and r.derivation == "documented_cancelled_flag"
    ]
    assert len(rows) == 1
    assert rows[0].kind == "derivation"
    assert _complete(rows[0].citation)
    assert not [e for e in result.trace if (e.derivation or "") == "constant:cancelled"]
    assert not [r for r in module.PROVENANCE_TABLE if r.derivation == "constant:cancelled"]
    check_no_fabrication(result, doc, module)
    check_records_valid(result)


def test_f4_not_cancelled_flag_is_not_derived_as_cancelled() -> None:
    """The cited derivation is only for the boolean true: ``cancelled: false`` leaves the status
    ``unknown`` and its trace entry does not use ``documented_cancelled_flag``."""
    result = run_f4(_cancel_doc())
    other = next(c for c in result.tool_calls if c.status == "unknown")
    entry = next(e for e in result.trace if e.record == str(other.id) and e.field == "/status")
    assert entry.derivation != "documented_cancelled_flag"


# ============================================================================ 2: Decimal merge


def _repeated(name: str, key: str, values: Sequence[Any], extra: Sequence[Any] = ()) -> Any:
    return _stage(name, [*extra, *[(key, v) for v in values]])


DECIMAL_CASES = [
    ((0.1, 0.2), "0.3"),
    ((1.25, 0.5), "1.75"),
    ((0.1, 0.2, 0.3), "0.6"),
    ((2.5, 2.5), "5"),
    ((0.07, 0.01), "0.08"),
]


@pytest.mark.parametrize(("values", "total"), DECIMAL_CASES)
def test_f1_repeated_audio_seconds_merge_to_the_exact_decimal_sum(
    values: tuple[float, ...], total: str
) -> None:
    """Decision 2 (extends N64): with a complete user-cited statement that the repeated
    ``metrics.audio_seconds`` of one stt span are one billable event, the printed numbers are
    summed as Decimals (never as floats: 0.1 + 0.2 is exactly 0.3) into ONE quantity
    ``audio_input_seconds``, a canonical decimal string. The trace entry names the derivation
    ``merged_one_billable_event``, lists every occurrence as a source and carries the citation;
    ``raw`` keeps every occurrence in order; no float and no second quantity of that unit; the
    generic no-fabrication check passes."""
    span = _repeated("stt", "metrics.audio_seconds", values)
    doc = otlp_doc([span, _turn()])
    result = run_f1(doc, one_billable_event=_citation())
    assert len(result.usage_records) == 1
    record = result.usage_records[0]
    assert record.stage == "stt"
    assert [q.unit for q in record.quantities] == ["audio_input_seconds"]
    assert record.quantities[0].count == total
    assert isinstance(record.quantities[0].count, str)
    assert record.raw == {"metrics.audio_seconds": list(values)}
    assert json.loads(json.dumps(record.model_dump(mode="json")))["quantities"][0]["count"] == total
    entries = [
        e
        for e in result.trace
        if e.record == str(record.id) and e.derivation == "merged_one_billable_event"
    ]
    assert len(entries) == 1
    assert len(entries[0].sources) == len(values)
    assert [resolve_source(doc, p) for p in entries[0].sources] == list(values)
    assert _complete(entries[0].citation)
    assert result.gaps == ()
    assert "usage_quantities_not_merged" not in _codes(result)
    check_no_fabrication(result, doc, mapping("pipecat_otel"))
    check_records_valid(result)


def test_f1_repeated_character_counts_merge_to_the_integer_sum() -> None:
    """The same rule for an integer unit of another stage: two tts ``metrics.character_count``
    with a complete citation sum to one ``input_characters`` count (5 + 7 = 12); an integer."""
    span = _repeated("tts", "metrics.character_count", (5, 7))
    doc = otlp_doc([span, _turn()])
    result = run_f1(doc, one_billable_event=_citation())
    assert [_quantities(r) for r in result.usage_records] == [{"input_characters": 12}]
    check_no_fabrication(result, doc, mapping("pipecat_otel"))


UNCITED: list[dict[str, Any] | None] = [
    None,
    {"url": None},
    {"retrieved_at": None},
    {"url": None, "retrieved_at": None},
    {"url": "", "retrieved_at": ""},
]


@pytest.mark.parametrize("citation", range(len(UNCITED)))
def test_f1_repeated_audio_seconds_without_a_complete_citation_is_refused_as_n76(
    citation: int,
) -> None:
    """Decision 2 (N64/N76): the same repeated ``metrics.audio_seconds`` with no statement, or an
    incomplete one, is refused for the whole call: no UsageRecord, exactly one warning
    ``usage_quantities_not_merged`` whose path locates the second occurrence (no raw), one
    BLOCKING gap row ``F1:stt.metrics.audio_seconds``, no quantity equal to a part or the sum,
    and nothing raises. Merging can change the bill (increment and minimum apply per record), so
    nothing is guessed."""
    span = _repeated("stt", "metrics.audio_seconds", (0.1, 0.2))
    doc = otlp_doc([span, _turn()])
    fields = UNCITED[citation]
    result = run_f1(doc, one_billable_event=None if fields is None else _citation(**fields))
    assert result.usage_records == ()
    flagged = [w for w in result.warnings if w.code == "usage_quantities_not_merged"]
    assert len(flagged) == 1
    assert flagged[0].raw is None
    located = resolve_source(doc, flagged[0].path or "")
    assert located["key"] == "metrics.audio_seconds"
    assert located["value"]["doubleValue"] == 0.2
    assert [(g.fixture, g.classification, g.source_key) for g in result.gaps] == [
        ("F1", "blocking", "F1:stt.metrics.audio_seconds")
    ]
    assert not [r for r in result.records if type(r).__name__ == "UsageRecord"]
    check_warnings_quote_sources(result, doc)
    check_records_valid(result)


# ============================================================================ 3: placeholders


@pytest.mark.parametrize(
    ("stage", "usage", "unit", "count"),
    [
        ("llm", [("gen_ai.usage.input_tokens", 40), ("gen_ai.usage.output_tokens", 5)], None, None),
        ("stt", [("metrics.audio_seconds", 1.5)], "audio_input_seconds", "1.5"),
        ("tts", [("metrics.character_count", 9)], "input_characters", 9),
    ],
)
@pytest.mark.parametrize("placeholder", ["unknown", "NOT_GIVEN"])
def test_f1_placeholder_model_keeps_the_usage_of_every_stage(
    stage: str, usage: list[tuple[str, Any]], unit: str | None, count: Any, placeholder: str
) -> None:
    """Decision 3 (M3): on an llm, stt or tts span the placeholder model is never stored and the
    usage is kept with ``model`` None. One UsageRecord, the stage's own quantities, one
    ``placeholder_value_ignored`` warning, no gap, the placeholder text in no record."""
    doc = otlp_doc([_stage(stage, usage, model=placeholder), _turn()])
    result = run_f1(doc)
    assert len(result.usage_records) == 1
    record = result.usage_records[0]
    assert record.stage == stage
    assert record.model is None
    if unit is None:
        assert _quantities(record) == {"input_tokens": 40, "output_tokens": 5}
    else:
        assert _quantities(record) == {unit: count}
    assert _codes(result).count("placeholder_value_ignored") == 1
    assert result.gaps == ()
    assert placeholder not in json.dumps([r.model_dump(mode="json") for r in result.records])
    check_no_fabrication(result, doc, mapping("pipecat_otel"))


# ============================================================================ 5: no provider

USAGE_BY_STAGE: dict[str, list[tuple[str, Any]]] = {
    "llm": [("gen_ai.usage.input_tokens", 40), ("gen_ai.usage.output_tokens", 5)],
    "stt": [("metrics.audio_seconds", 1.5)],
    "tts": [("metrics.character_count", 9)],
}


@pytest.mark.parametrize("stage", sorted(USAGE_BY_STAGE))
def test_f1_usage_span_without_a_provider_is_malformed_and_a_blocking_gap(stage: str) -> None:
    """Decision 5: a span that states usage but names no provider cannot be turned into a
    UsageRecord (provider is required) and the usage would be lost silently, so: no UsageRecord,
    exactly one ``malformed_entry`` warning whose path locates the span, and a BLOCKING gap row
    (ADR 0018 Decision 3, like N76) of fixture F1 under the stage whose key is one of the usage
    attributes of the span. Nothing raises; every other record is still produced."""
    doc = otlp_doc([_stage(stage, USAGE_BY_STAGE[stage], provider=None), _turn()])
    result = run_f1(doc)
    assert result.usage_records == ()
    flagged = [w for w in result.warnings if w.code == "malformed_entry"]
    assert len(flagged) == 1
    located = resolve_source(doc, flagged[0].path or "")
    assert located["name"] == stage
    assert result.gaps
    keys = {k for k, _ in USAGE_BY_STAGE[stage]}
    for gap in result.gaps:
        assert (gap.fixture, gap.classification) == ("F1", "blocking")
        assert gap.source_key.startswith(f"F1:{stage}.")
        assert gap.source_key.removeprefix(f"F1:{stage}.") in keys
        assert gap.owner == "schema-architect"
    assert len(result.stage_spans) == 1
    check_warnings_quote_sources(result, doc)
    check_records_valid(result)


@pytest.mark.parametrize("stage", sorted(USAGE_BY_STAGE))
def test_f1_span_without_provider_and_without_usage_loses_nothing(stage: str) -> None:
    """The counterpart: a provider-less span that states no usage loses no usage, so it is no
    malformed entry for the usage rule and no gap row."""
    doc = otlp_doc([_stage(stage, [], provider=None), _turn()])
    result = run_f1(doc)
    assert result.usage_records == ()
    assert result.gaps == ()
    assert "malformed_entry" not in _codes(result)


def test_f1_provider_less_span_does_not_hide_the_other_calls() -> None:
    """The refusal is per span: a second llm span with a provider is still recorded."""
    bad = _stage("llm", USAGE_BY_STAGE["llm"], provider=None)
    good = _stage("llm", USAGE_BY_STAGE["llm"], span_id="c2c2c2c2c2c2c2c2")
    result = run_f1(otlp_doc([bad, good, _turn()]))
    assert [_quantities(r) for r in result.usage_records] == [
        {"input_tokens": 40, "output_tokens": 5}
    ]
    assert _codes(result).count("malformed_entry") == 1
    assert result.gaps
    assert {g.classification for g in result.gaps} == {"blocking"}


# ============================================================================ 7: turn numbers


def test_f1_duplicate_turn_number_keeps_the_first_span_and_warns() -> None:
    """Decision 7: two turn spans with the same ``turn.number`` give ONE Exchange, the first span
    in the document (which is also the earlier one), with its own duration; exactly one
    ``duplicate_turn_number`` warning quoting the second span's number; the second span's child is
    not attributed to the kept exchange (the exchange of a stage span is stated by its own parent
    span only, so its exchange_index stays None). Nothing raises, and records validate."""
    first = _turn(1, span_id=TURN_ID, duration=2.0)
    second = _turn(1, span_id="a2a2a2a2a2a2a2a2", start_ns=1_792_757_178_000_000_000, duration=9.0)
    child_of_first = _stage("tts", [("text", "one")], span_id="c1c1c1c1c1c1c1c1")
    child_of_second = _stage(
        "tts", [("text", "two")], span_id="c2c2c2c2c2c2c2c2", parent="a2a2a2a2a2a2a2a2"
    )
    doc = otlp_doc([child_of_first, child_of_second, first, second])
    result = run_f1(doc)
    assert [(x.index, x.duration_ms) for x in result.exchanges] == [(1, 2000)]
    flagged = [w for w in result.warnings if w.code == "duplicate_turn_number"]
    assert len(flagged) == 1
    assert flagged[0].raw == "1"
    located = resolve_source(doc, flagged[0].path or "")
    assert located in ("1", 1)
    assert "malformed_entry" not in _codes(result)
    by_ref = {s.span_ref.value: s.exchange_index for s in result.stage_spans}
    assert by_ref == {"c1c1c1c1c1c1c1c1": 1, "c2c2c2c2c2c2c2c2": None}
    check_warnings_quote_sources(result, doc)
    check_records_valid(result)


@pytest.mark.parametrize(
    "bad",
    [None, "three", ""],
    ids=["missing", "text", "empty-text"],
)
def test_f1_turn_span_without_an_integer_number_is_malformed(bad: Any) -> None:
    """Decision 7: a turn span whose ``turn.number`` is absent or not an integer gives exactly one
    ``malformed_entry`` warning whose path locates the turn span, and no Exchange (an index is
    never guessed or counted). A valid turn span next to it is still mapped."""
    broken = _turn(None, span_id="a2a2a2a2a2a2a2a2")
    if bad is not None:
        broken["attributes"].append({"key": "turn.number", "value": {"stringValue": bad}})
    good = _turn(2, span_id=TURN_ID)
    doc = otlp_doc([broken, good])
    result = run_f1(doc)
    assert [x.index for x in result.exchanges] == [2]
    flagged = [w for w in result.warnings if w.code == "malformed_entry"]
    assert len(flagged) == 1
    assert resolve_source(doc, flagged[0].path or "")["spanId"] == "a2a2a2a2a2a2a2a2"
    check_records_valid(result)


# ============================================================================ 8: '#' in a key


def test_resolver_reads_a_percent_encoded_hash_in_a_key() -> None:
    """The independent resolver of the tests (``_support_b6.resolve_source``, the checker that
    P-51 relies on): '#' separates the parts of a chained pointer, so a '#' that belongs to a
    JSON key is written ``%23`` and read back to the key; a part may continue inside a JSON text
    twice."""
    inner = json.dumps({"k#1": {"x": 5}, "a/b": 6, "t~": 7})
    outer = {"a": json.dumps({"args": inner})}
    assert resolve_source(outer, "/a#/args#/k%231/x") == 5
    assert resolve_source(outer, "/a#/args#/a~1b") == 6
    assert resolve_source(outer, "/a#/args#/t~0") == 7
    assert resolve_source({"p#q": 1}, "/p%23q") == 1
    with pytest.raises((AssertionError, KeyError, ValueError, IndexError)):
        resolve_source(outer, "/a#/args#/k#1/x")


def test_f1_hash_in_a_tool_argument_key_is_percent_encoded_in_chained_sources() -> None:
    """Decision 8: a tool call whose arguments object has a key that contains '#' (and one
    nested): every source pointer of the argument leaves is chained
    (``input`` attribute # tool_calls # arguments key path) and spells the '#' of the key as
    ``%23``, so the pointer has exactly the chain's number of '#' separators and the independent
    resolver reads the leaf back. The record keeps the key as written (``a#b``). The generic
    no-fabrication check passes, which resolves every pointer of the result."""
    arguments = json.dumps({"a#b": 1, "c": {"d#": "x"}, "e": [1, 2]})
    messages = json.dumps(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "act", "arguments": arguments},
                    }
                ],
            },
        ]
    )
    llm = _stage("llm", [("input", messages)], provider="openai", model="m-1")
    doc = otlp_doc([llm, _turn()])
    result = run_f1(doc)
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.arguments == {"a#b": 1, "c": {"d#": "x"}, "e": [1, 2]}
    leaf_entries = {
        e.field: e
        for e in result.trace
        if e.record == str(call.id) and e.field.startswith("/arguments/")
    }
    assert set(leaf_entries) == {
        "/arguments/a#b",
        "/arguments/c/d#",
        "/arguments/e/0",
        "/arguments/e/1",
    }
    expected_tail = {
        "/arguments/a#b": "/a%23b",
        "/arguments/c/d#": "/c/d%23",
        "/arguments/e/0": "/e/0",
        "/arguments/e/1": "/e/1",
    }
    for field, entry in leaf_entries.items():
        assert len(entry.sources) == 1
        parts = entry.sources[0].split("#")
        assert len(parts) == 3, entry.sources[0]
        assert parts[2] == expected_tail[field]
        assert resolve_source(doc, entry.sources[0]) == _leaf(call.arguments, field)
    check_no_fabrication(result, doc, mapping("pipecat_otel"))
    check_warnings_quote_sources(result, doc)


def _leaf(arguments: Mapping[str, Any], field: str) -> Any:
    node: Any = arguments
    for token in field.removeprefix("/arguments/").split("/"):
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def test_f4_hash_in_a_tool_argument_key_is_percent_encoded() -> None:
    """Decision 8, applied to every pointer ('#' is the chain separator everywhere, so a raw '#'
    in a key would be read as a separator even in an un-chained pointer): the F4 source pointer
    of an argument leaf whose key contains '#' spells it ``%23`` and resolves."""
    doc: list[dict[str, Any]] = [
        {"role": "user", "content": "go"},
        {"role": "tool_call", "name": "act", "arguments": {"a#b": 1}, "cancelled": False},
    ]
    result = run_f4(doc)
    call = result.tool_calls[0]
    entry = next(
        e for e in result.trace if e.record == str(call.id) and e.field == "/arguments/a#b"
    )
    assert entry.sources == ("/1/arguments/a%23b",)
    assert resolve_source(doc, entry.sources[0]) == 1
    check_no_fabrication(result, doc, mapping("pipecat_conversation"))


# ============================================================================ 8b: '%' in a key


def test_resolver_decodes_percent_escapes_in_one_pass() -> None:
    """``%25`` is a literal '%' and ``%23`` a '#', decoded in ONE left-to-right pass: the pointer
    token ``%2523`` is the key text ``%23`` (not ``#``), and ``%25`` alone is ``%``."""
    doc = {"100%": 1, "%23": 2, "#": 3, "a%b#c": 4}
    assert resolve_source(doc, "/100%25") == 1
    assert resolve_source(doc, "/%2523") == 2
    assert resolve_source(doc, "/%23") == 3
    assert resolve_source(doc, "/a%25b%23c") == 4
    inner = json.dumps({"%23": 5, "p%": 6})
    assert resolve_source({"a": inner}, "/a#/%2523") == 5
    assert resolve_source({"a": inner}, "/a#/p%25") == 6


_PERCENT_KEYS = {"50%": 1, "%23": 2, "c": {"x%y#z": 3, "%2523": 4}}
_PERCENT_TAILS = {
    "/arguments/50%": "/50%25",
    "/arguments/%23": "/%2523",
    "/arguments/c/x%y#z": "/c/x%25y%23z",
    "/arguments/c/%2523": "/c/%252523",
}


def test_f1_percent_and_percent_23_in_argument_keys_round_trip_in_chained_sources() -> None:
    """A key with a literal '%' and a key whose text is ``%23`` (and nested ones): the converter
    escapes '%' as ``%25`` before '#' as ``%23``, so every chained source pointer resolves, with
    the independent resolver, to the leaf it cites."""
    arguments = json.dumps(_PERCENT_KEYS)
    messages = json.dumps(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "act", "arguments": arguments},
                    }
                ],
            },
        ]
    )
    llm = _stage("llm", [("input", messages)], provider="openai", model="m-1")
    doc = otlp_doc([llm, _turn()])
    result = run_f1(doc)
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.arguments == _PERCENT_KEYS
    entries = {
        e.field: e
        for e in result.trace
        if e.record == str(call.id) and e.field.startswith("/arguments/")
    }
    assert set(entries) == set(_PERCENT_TAILS)
    for field, entry in entries.items():
        assert len(entry.sources) == 1
        parts = entry.sources[0].split("#")
        assert len(parts) == 3, entry.sources[0]
        assert parts[2] == _PERCENT_TAILS[field]
        assert resolve_source(doc, entry.sources[0]) == _leaf(call.arguments, field)
    check_no_fabrication(result, doc, mapping("pipecat_otel"))


def test_f4_percent_and_percent_23_in_argument_keys_round_trip() -> None:
    """The same for the un-chained F4 pointer: '%' is ``%25``, the text ``%23`` is ``%2523``."""
    doc: list[dict[str, Any]] = [
        {"role": "user", "content": "go"},
        {"role": "tool_call", "name": "act", "arguments": _PERCENT_KEYS, "cancelled": False},
    ]
    result = run_f4(doc)
    call = result.tool_calls[0]
    entries = {
        e.field: e
        for e in result.trace
        if e.record == str(call.id) and e.field.startswith("/arguments/")
    }
    assert set(entries) == set(_PERCENT_TAILS)
    for field, entry in entries.items():
        assert entry.sources == ("/1/arguments" + _PERCENT_TAILS[field],)
        assert resolve_source(doc, entry.sources[0]) == _leaf(call.arguments, field)
    check_no_fabrication(result, doc, mapping("pipecat_conversation"))
