# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-08: InputKind, pass rules, MetricDefinition, MetricBinding, PerTurnCounts (ADR 0003, 0010).

Assumed public API (docs/phase1/api-surface.md section 19), all in ``tracer_evalschema.metrics``:
``InputKind`` (closed set), ``PassRule`` (union discriminated by ``kind``: ``RangeRule``,
``EqualsTrueRule``, ``OneOfRule``), ``rule_passes(rule, value) -> bool``, ``PerTurnCounts``,
``per_turn_share(counts) -> str | None``, ``per_turn_status(counts) -> str``,
``MetricDefinition`` (versioned), ``MetricBinding`` (versioned), ``check_bindings_unique``.

Names from SCOPE section 5 are kept; "Proposed name" marks the others. Fields that the ADRs do
not spell out (``implementation_ref``, ``prompt_variables``, the shape of ``scale`` and
``aggregation``) are pinned in api-surface section 19 and listed there as ambiguities.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from ._support import api, data_json
from ._support_b2 import (
    EXCLUDED_LANGUAGE_TAGS,
    JUDGE_TEMPLATE,
    MIXED_ALIAS_TAG,
    assert_rejected,
    assert_rejected_name,
    cls,
    collect_enums,
    digest_check_off,
    entity_class,
    equals_true_rule,
    fields_of,
    jsonschema_module,
    judge_definition,
    latency_definition,
    make,
    model_ref,
    one_of_rule,
    parameter,
    property_enum,
    property_names,
    provider_ref,
    range_rule,
    raw_document,
    ref,
    registered_class,
    schema_accepts,
    schema_of,
    seal,
    walk_nodes,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    marked_id,
    marked_key,
    marked_set,
    marked_tag,
)

INPUT_KINDS = {
    "audio",
    "agent_audio",
    "transcript",
    "agent_transcript",
    "turn_timing",
    "tool_calls",
    "tool_results",
    "spans",
    "usage",
    "reference_text",
    "expected_tool_calls",
}


def _m() -> Any:
    return api("metrics")


def _rules() -> TypeAdapter[Any]:
    return TypeAdapter(_m().PassRule)


def _rule(doc: dict[str, Any]) -> Any:
    return _rules().validate_python(doc)


def _passes(rule: Any, value: Any) -> bool:
    return bool(_m().rule_passes(rule, value))


def _never_passes(rule: Any, value: Any) -> None:
    """No coercion: the call either returns False or raises a TypeError/ValueError."""
    try:
        result = _m().rule_passes(rule, value)
    except (TypeError, ValueError):
        return
    assert result is False, (rule, value)


def _definition(**over: Any) -> dict[str, Any]:
    return raw_document("MetricDefinition", **over)


def _accept(**over: Any) -> Any:
    return make("MetricDefinition", **over)


def _reject(field: str | None = None, **over: Any) -> ValidationError:
    return assert_rejected_name("MetricDefinition", field=field, **over)


def _rating(**over: Any) -> dict[str, Any]:
    base = judge_definition(
        key="politeness",
        value_type="rating",
        scale={"min": 1, "max": 5},
        pass_rule={"kind": "range", "min": 4, "inclusive": True},
    )
    return {**base, **over}


def _enum(**over: Any) -> dict[str, Any]:
    base = judge_definition(
        key="outcome_class",
        value_type="enum",
        scale={"options": ["resolved", "escalated", "abandoned"]},
        pass_rule=one_of_rule(["resolved"]),
    )
    return {**base, **over}


def _turn_share(rule: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    return judge_definition(
        key="polite_turn_share",
        aggregation={"kind": "per_turn_share", "turn_rule": equals_true_rule()},
        pass_rule=rule if rule is not None else {"kind": "range", "min": 0.8, "inclusive": True},
        **over,
    )


# ------------------------------------------------------------------------------- InputKind


def test_input_kind_members_exact() -> None:
    """ADR 0003 I1 / ADR 0004 I16: exactly the eleven members, a string ``enum`` in the schema."""
    adapter = TypeAdapter(_m().InputKind)
    for mode in ("validation", "serialization"):
        schema = adapter.json_schema(mode=mode)
        assert schema["type"] == "string"
        assert len(schema["enum"]) == len(set(schema["enum"])) == 11
        assert set(schema["enum"]) == INPUT_KINDS
        assert "const" not in schema
    for member in INPUT_KINDS:
        assert adapter.validate_python(member) == member
        assert adapter.validate_json(json.dumps(member)) == member
    for bad in ("Audio", "audio ", "video", "", "tool_call", "agent-audio", None, 1, ["audio"]):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def _closed_enum_table() -> list[tuple[str, tuple[str, ...], set[str]]]:
    """ADR 0003 Decision 1 as (owner class, field path, members). A literal copy, not generated."""
    kinds = INPUT_KINDS
    stages = {"stt", "llm", "tts", "judge", "sim_caller_llm", "sim_caller_tts", "realtime", "other"}
    usage_units = {
        "input_tokens",
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "audio_input_seconds",
        "audio_output_seconds",
        "input_characters",
        "requests",
    }
    return [
        ("MetricDefinition", ("requires",), kinds),
        ("MetricDefinition", ("kind",), {"deterministic", "llm_judge", "audio", "latency"}),
        ("MetricDefinition", ("value_type",), {"boolean", "rating", "enum", "numeric"}),
        ("MetricDefinition", ("aggregation", "kind"), {"per_conversation", "per_turn_share"}),
        ("MetricDefinition", ("pass_rule", "kind"), {"range", "equals_true", "one_of"}),
        # revision 8 (ADR 0003 I11: "qa adds those rows"); units have no currency member
        ("MetricDefinition", ("unit",), {"ms", "count", "ratio", "percent", "wer", "cer"}),
        ("MetricParameter", ("value_type",), {"boolean", "integer", "number", "string", "json"}),
        ("DeclaredStackEntry", ("stage",), {"stt", "llm", "tts", "realtime"}),
        (
            "ExpectedToolCall",
            ("match",),
            {"name_only", "arguments_exact", "arguments_subset"},
        ),
        ("AgentTarget", ("declared_capabilities",), kinds),
        ("AgentTarget", ("endpoint", "transport"), {"ws", "webrtc", "sip"}),
        ("ContentDigest", ("scheme",), {"sha256-jcs/v1", "hmac-sha256-jcs/v1"}),
        ("Money", ("currency",), {"USD"}),
        # revision 18 (api-surface-sealing R3, S-N80, S-N87): the closed sets of the sealed
        # payload and of the redaction record; owners are checked once they exist
        ("SealedPayload", ("format",), {"tracer.sealed.v1"}),
        ("SealedPayload", ("alg",), {"A256GCM"}),
        ("SealedPayload", ("dek_wrap", "alg"), {"A256KW"}),
        (
            "SealedPayload",
            ("aad_fields", "payload_type"),
            {"test_profile", "audio", "transcript_text", "tool_arguments", "tool_result"},
        ),
        ("RedactionRecord", ("covers",), {"audio", "text", "tool_arguments", "tool_result"}),
        (
            "Transcript",
            ("source",),
            {"our_stt", "agent_export", "agent_telemetry", "observer", "script", "human_reference"},
        ),
        (
            "Turn",
            ("source",),
            {
                "our_stt",
                "agent_stt",
                "agent_llm_output",
                "agent_tts_input",
                "agent_context",
                "agent_log",
                "script",
                "human_reference",
            },
        ),
        ("Turn", ("timing_source",), {"audio_alignment", "telemetry", "observer", "none"}),
        ("Turn", ("role",), {"caller", "agent", "unknown"}),
        ("WallTime", ("meaning",), {"start", "end", "logged", "unknown"}),
        ("TimelineOrigin", ("kind",), {"artifact", "telemetry", "observer"}),
        ("Segment", ("granularity",), {"word", "phrase", "utterance"}),
        (
            "Exchange",
            ("source",),
            {"audio_alignment", "telemetry", "observer", "inferred_from_turns"},
        ),
        ("StageSpan", ("stage",), stages),
        ("MetricResult", ("status",), {"scored", "not_applicable", "error"}),
        (
            "MetricResult",
            ("na_reason",),
            {
                "missing_input",
                "unsupported_language",
                "unsupported_script",
                "agent_channel_unknown",
                # P-59 (b), accepted by the owner in revision 8: a language-dependent metric on
                # input that carries no language tag. ADR 0003 Decision 1 still lists four.
                "language_not_observed",
            },
        ),
        ("RunVerdict", ("status",), {"pass", "fail", "inconclusive"}),
        ("Run", ("caller_origin",), {"simulated", "human"}),
        ("Run", ("mode",), {"simulated", "recorded"}),
        ("ChannelMap", ("determination",), {"declared", "inferred"}),
        ("ChannelMap", ("channels", "role"), {"caller", "agent", "unknown", "mixed"}),
        ("ToolCallEvent", ("status",), {"completed", "cancelled", "unknown"}),
        ("UsageRecord", ("domain",), {"evaluation", "agent_under_test"}),
        ("UsageRecord", ("stage",), stages),
        ("UsageRecord", ("source",), {"provider_reported", "telemetry", "measured"}),
        ("CostRecord", ("status",), {"available", "not_available"}),
        ("CostRecord", ("basis",), {"reported", "computed"}),
        ("CostRecord", ("reported_by",), {"provider", "telemetry_platform"}),
        ("CostSummary", ("completeness",), {"complete", "partial", "none"}),
        ("PriceEntry", ("price_source",), {"catalog", "user_supplied"}),
        # revision 15 and 16 (api-surface 23.6 group J): the batch 4 sets. No row for
        # PriceProvenance.kind: it is an open MachineKey (N50 remainder).
        (
            "CostRecord",
            ("na_reason",),
            {
                "no_price_for_model",
                "unit_unpriced",
                "no_usage",
                "ambiguous_price",
                "billing_rules_missing",
                "usage_not_reported",
                "unsupported_currency",
                "currency_not_reported",
                "amount_unparseable",
                "currency_conflict",
            },
        ),
        (
            "CostExclusion",
            ("reason",),
            {
                "no_price_for_model",
                "unit_unpriced",
                "no_usage",
                "ambiguous_price",
                "billing_rules_missing",
                "usage_not_reported",
                "unsupported_currency",
                "currency_not_reported",
                "amount_unparseable",
                "currency_conflict",
                "duplicate_of_reported",
                "overlaps_reported",
            },
        ),
        ("UsageQuantity", ("unit",), usage_units),
        ("PriceEntry", ("unit",), usage_units),
        ("CostRecord", ("unpriced_units",), usage_units),
        (
            "AppliesWhenCondition",
            ("kind",),
            {"context_tokens_gt", "context_tokens_le", "batch", "region"},
        ),
        ("ReportedCostRaw", ("reported_by",), {"provider", "telemetry_platform"}),
        (
            "PrivacyMarker",
            ("human_subject_reason",),
            {"marked_before_role_evidence", "real_person_likeness", "manual"},
        ),
        ("ConsentRecord", ("status",), {"obtained", "not_obtained", "unknown"}),
        ("ExportBundle", ("purpose",), {"internal", "shared_export", "benchmark"}),
        ("JudgeInfo", ("per_turn", "verdict"), {"yes", "no", "undetermined"}),
        ("EvidenceRef", ("kind",), {"turn", "exchange", "tool_call", "span", "artifact_range"}),
        ("ReportedCostRaw", ("currency_source",), {"payload", "api_contract", "none"}),
        ("CostRounding", ("mode",), {"half_even"}),
        # revision 10 (ADR 0003 I11; api-surface 21.4): the two N14 sets
        ("LanguageDeclaration", ("source",), {"user_declared"}),
        ("MetricResult", ("language_source",), {"observed", "declared"}),
        # batch 3 (api-surface section 22): the verdict sets
        ("VerdictCheck", ("outcome",), {"passed", "failed", "not_applicable", "errored"}),
        ("WeightedExclusion", ("outcome",), {"not_applicable", "errored"}),
        # revision 12 (api-surface 22.9 items 13 and 14): O2 and O3, both decided by the owner
        ("ToolCallEvent", ("source",), {"agent_export", "agent_telemetry", "observer"}),
        ("Run", ("observed_tier",), {"T0", "T1", "T2"}),
    ]


def _members_at(model: Any, path: tuple[str, ...]) -> list[Any]:
    """Enum members at ``path`` in the validation schema of ``model``."""
    schema = schema_of(model)
    options = [schema]
    for index, name in enumerate(path):
        found: list[Any] = []
        for node in options:
            for sub in _object_nodes(schema, node):
                if name in sub.get("properties", {}):
                    found.append(sub["properties"][name])
        assert found, f"{model.__name__}: no property {name!r} on the way to {path}"
        if index == len(path) - 1:
            return [m for node in found for m in collect_enums(schema, node)]
        options = found
    return []


def _object_nodes(root: dict[str, Any], node: Any) -> list[dict[str, Any]]:
    from ._support import resolve_schema

    node = resolve_schema(root, node)
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "properties" in node:
            out.append(node)
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                out.extend(_object_nodes(root, sub))
        if "items" in node:
            out.extend(_object_nodes(root, node["items"]))
    return out


def test_all_closed_enums_exact() -> None:
    """ADR 0003 I11 (C): every closed set equals the literal table. The batch 2 owners must exist
    (this test is red until they do); owners of later tasks are checked as soon as they exist, so
    the test becomes complete without being edited."""
    registry = api("registry")
    models = {m.__name__: m for m in registry.model_classes()}
    for name in (
        "MetricDefinition",
        "AgentTarget",
        "MetricParameter",
        "DeclaredStackEntry",
        "ExpectedToolCall",
        "SealedPayload",
        "RedactionRecord",
    ):
        assert name in models, f"{name} is not implemented yet"
    checked = 0
    for owner, path, expected in _closed_enum_table():
        if owner not in models:
            continue
        members = _members_at(models[owner], path)
        assert len(members) == len(set(members)), (owner, path, members)
        assert set(members) == expected, (owner, path, sorted(set(members) ^ expected))
        checked += 1
    assert checked >= 13


def test_open_value_fields_accept_unseen_provider() -> None:
    """ADR 0003 I12: open values name outside things and accept unseen ones, as ``MachineKey``:
    provider, model, tool name, and the judge model reference. (Revision 8: the judge model is a
    ``ModelRef{provider, model}`` without ``config_ref``, so it is built on a judge definition;
    on a latency definition the field is now rejected.)"""
    unseen = {"provider": "some-new-vendor", "model": "org/Model-Name:v2"}
    judge = _accept(**judge_definition(judge_model_ref=model_ref(**unseen)))
    assert (judge.judge_model_ref.provider, judge.judge_model_ref.model) == tuple(unseen.values())
    ref_model = cls("provider", "ProviderConfigRef")
    assert ref_model.model_validate(provider_ref(**unseen)).model == "org/Model-Name:v2"
    entry = cls("agent", "DeclaredStackEntry")
    assert entry.model_validate({"stage": "tts", **unseen}).provider == "some-new-vendor"
    call = cls("evaluator", "ExpectedToolCall")
    for name in ("book_flight_v2", "Org/Tool:v2", "tool.name@1"):
        loaded = call.model_validate({"name": name, "match": "name_only", "order_index": 0})
        assert loaded.name == name
    for bad in ("two words", "", "टूल", "x" * 257):
        with pytest.raises(ValidationError):
            call.model_validate({"name": bad, "match": "name_only", "order_index": 0})
        if bad:
            _reject(
                field="judge_model_ref", **judge_definition(judge_model_ref=model_ref(model=bad))
            )


# --------------------------------------------------------------------------- requires[]


def test_requires_nonempty_unique() -> None:
    """P-07: ``requires`` is a non-empty list of distinct ``InputKind``; pydantic and the JSON
    Schema (minItems, uniqueItems) agree."""
    model = entity_class("MetricDefinition")
    ok = _accept(requires=["transcript", "turn_timing", "usage"])
    assert ok.requires == ["transcript", "turn_timing", "usage"]
    assert _accept(requires=["audio"]).requires == ["audio"]
    for bad in ([], ["audio", "audio"], ["audio", "Audio"], ["telepathy"], "audio", None, [None]):
        _reject(field="requires", requires=bad)
    with pytest.raises(ValidationError):
        model.model_validate({k: v for k, v in _definition().items() if k != "requires"})
    prop = schema_of(model)["properties"]["requires"]
    assert prop["minItems"] >= 1
    assert prop["uniqueItems"] is True
    assert not schema_accepts(model, _definition(requires=[]))
    assert not schema_accepts(model, _definition(requires=["audio", "audio"]))
    assert schema_accepts(model, seal(model, _definition()))


# ------------------------------------------------------------------------------ pass rules


def test_pass_rule_range_inclusive_and_exclusive() -> None:
    """ADR 0010 I1: a range is inclusive or exclusive at both bounds; comparisons are exact in
    ``Decimal`` and a float bound is read through its shortest round-trip text."""
    band = _rule({"kind": "range", "min": 2, "max": 5, "inclusive": True})
    for value in (2, 3.5, 5, Decimal("2"), Decimal("5.0")):
        assert _passes(band, value) is True
    for value in (1.999, 5.001, 0, 6):
        assert _passes(band, value) is False
    exclusive = _rule({"kind": "range", "min": 2, "max": 5, "inclusive": False})
    for value in (2, 5, 1, 6):
        assert _passes(exclusive, value) is False
    for value in (2.001, 4.999, 3):
        assert _passes(exclusive, value) is True
    only_min = _rule({"kind": "range", "min": 0.8, "inclusive": True})
    assert _passes(only_min, 0.8)
    assert _passes(only_min, 1)
    assert not _passes(only_min, 0.79)
    only_max = _rule({"kind": "range", "max": 800, "inclusive": False})
    assert _passes(only_max, 799)
    assert not _passes(only_max, 800)
    # float bounds against decimal values: 0.1 means 0.1, not 0.1000000000000000055...
    tenth = _rule({"kind": "range", "min": 0.1, "inclusive": True})
    assert _passes(tenth, Decimal("0.1")) is True
    assert _passes(tenth, Decimal("0.0999999999999999999")) is False
    share = _rule({"kind": "range", "min": 0.75, "inclusive": True})
    assert _passes(share, Decimal("0.75")) is True
    assert _passes(share, Decimal("0.666666666666666667")) is False
    assert _passes(_rule({"kind": "range", "max": 0.3, "inclusive": True}), Decimal("0.3")) is True
    # a boolean is not a number
    for odd in (True, False, "3", None):
        _never_passes(band, odd)


def test_latency_lower_is_better_expressed_with_range_max() -> None:
    """P-25: lower-is-better is ``range{max}``: passes at or below the bound."""
    latency = _accept()
    assert latency.pass_rule.kind == "range"
    assert latency.pass_rule.max == 800
    assert latency.pass_rule.min is None
    for value, expected in ((0, True), (799, True), (800, True), (801, False), (5000, False)):
        assert _passes(latency.pass_rule, value) is expected, value
    wer = _accept(
        key="wer",
        unit="wer",
        kind="deterministic",
        requires=["transcript", "reference_text"],
        implementation_ref="builtin:wer",
        script_sensitive=True,
        pass_rule={"kind": "range", "max": 0.25, "inclusive": True},
    )
    assert _passes(wer.pass_rule, 0.25)
    assert not _passes(wer.pass_rule, 0.26)


def test_score_higher_is_better_expressed_with_range_min() -> None:
    """P-25: higher-is-better is ``range{min}``: passes at or above the bound."""
    politeness = _accept(**_rating())
    assert politeness.pass_rule.kind == "range"
    assert politeness.pass_rule.min == 4
    assert politeness.pass_rule.max is None
    for value, expected in ((5, True), (4, True), (3, False), (1, False)):
        assert _passes(politeness.pass_rule, value) is expected, value
    exclusive = _accept(**_rating(pass_rule={"kind": "range", "min": 4, "inclusive": False}))
    assert _passes(exclusive.pass_rule, 4) is False
    assert _passes(exclusive.pass_rule, 5) is True


_NO_DIRECTION: list[Any] = [
    {"kind": "threshold", "value": 0.8},
    {"kind": "threshold", "min": 0.8, "inclusive": True},
    {"kind": "min", "value": 1},
    {"kind": "gte", "value": 1},
    {"kind": "equals_false"},
    {"kind": "range", "inclusive": True},
    {"kind": "range"},
    {"kind": "RANGE", "min": 1, "inclusive": True},
    {"min": 1, "inclusive": True},
    {},
    0.8,
    1,
    "0.8",
    "range",
    True,
    None,
    [],
]


def test_pass_rule_without_direction_rejected() -> None:
    """P-25: an unknown kind (including a single-number rule), a ``range`` with neither bound,
    a bare number and a bare string are rejected, by pydantic and by the JSON Schema."""
    adapter = _rules()
    validator = jsonschema_module().Draft202012Validator(adapter.json_schema())
    assert validator.is_valid({"kind": "range", "min": 1, "inclusive": True}), "positive control"
    assert validator.is_valid({"kind": "equals_true"})
    assert validator.is_valid({"kind": "one_of", "options": ["a"]})
    for bad in _NO_DIRECTION:
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(bad))
        assert not validator.is_valid(bad), bad
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "range", "min": None, "max": None, "inclusive": True})
    # and inside a definition
    for bad in (b for b in _NO_DIRECTION if b is not None):
        _reject(field="pass_rule", pass_rule=bad)


def test_range_requires_at_least_one_bound() -> None:
    """ADR 0010 I2: min only, max only or both; neither is rejected; min above max, non-finite
    and non-numeric bounds are rejected; no coercion of bool or text."""
    for good in (
        {"kind": "range", "min": 1, "inclusive": True},
        {"kind": "range", "max": 1, "inclusive": False},
        {"kind": "range", "min": 1, "max": 1, "inclusive": True},
        {"kind": "range", "min": 0.5, "max": 2.5, "inclusive": True},
    ):
        rule = _rule(good)
        assert rule.kind == "range"
        assert json.loads(rule.model_dump_json(exclude_none=True))["kind"] == "range"
    for bad in (
        {"kind": "range", "inclusive": True},
        {"kind": "range", "min": 5, "max": 2, "inclusive": True},
        {"kind": "range", "min": float("nan"), "inclusive": True},
        {"kind": "range", "max": float("inf"), "inclusive": True},
        {"kind": "range", "min": True, "inclusive": True},
        {"kind": "range", "min": "1", "inclusive": True},
        {"kind": "range", "min": 1, "inclusive": "yes"},
        {"kind": "range", "min": 1, "inclusive": True, "threshold": 1},
    ):
        with pytest.raises(ValidationError):
            _rules().validate_python(bad)


def test_equals_true_rule_boolean() -> None:
    """P-25 / ADR 0010 I3: ``equals_true`` passes exactly on ``True``; it takes no argument and
    there is no ``equals_false``; no coercion of 1 or "true"."""
    rule = _rule(equals_true_rule())
    assert _passes(rule, True) is True
    assert _passes(rule, False) is False
    for value in (1, 0, 1.0, "true", "yes", None, [True]):
        _never_passes(rule, value)
    for bad in (
        {"kind": "equals_true", "value": False},
        {"kind": "equals_true", "expected": True},
        {"kind": "equals_false"},
    ):
        with pytest.raises(ValidationError):
            _rules().validate_python(bad)
    assert _accept(**judge_definition()).pass_rule.kind == "equals_true"


def test_one_of_rule_options_subset() -> None:
    """ADR 0010 I3, I4: ``one_of{options}`` has distinct strings and passes exactly on a listed
    option; inside a definition the options must be a subset of the enum's options."""
    rule = _rule(one_of_rule(["resolved", "escalated"]))
    assert _passes(rule, "resolved")
    assert _passes(rule, "escalated")
    assert not _passes(rule, "abandoned")
    assert not _passes(rule, "Resolved")
    for value in (1, True, None, ["resolved"]):
        _never_passes(rule, value)
    for bad in (
        {"kind": "one_of", "options": []},
        {"kind": "one_of", "options": ["a", "a"]},
        {"kind": "one_of", "options": [1]},
        {"kind": "one_of"},
        {"kind": "one_of", "options": "resolved"},
        {"kind": "one_of", "options": ["a"], "x": 1},
    ):
        with pytest.raises(ValidationError):
            _rules().validate_python(bad)
    assert _accept(**_enum(pass_rule=one_of_rule(["resolved", "escalated"]))).pass_rule.options == [
        "resolved",
        "escalated",
    ]
    _reject(
        pass_rule=one_of_rule(["resolved", "unknown_option"]),
        **{k: v for k, v in _enum().items() if k != "pass_rule"},
    )


def test_pass_rule_kind_matches_value_type() -> None:
    """ADR 0010 I4: boolean takes ``equals_true``, numeric and rating take ``range``, enum takes
    ``one_of``; any other pairing is rejected; every type may have no pass rule (P-08)."""
    rules = {
        "range": {"kind": "range", "min": 1, "inclusive": True},
        "equals_true": equals_true_rule(),
        "one_of": one_of_rule(["resolved"]),
    }
    docs = {
        "boolean": judge_definition(),
        "numeric": latency_definition(),
        "rating": _rating(),
        "enum": _enum(),
    }
    fits = {"boolean": "equals_true", "numeric": "range", "rating": "range", "enum": "one_of"}
    for value_type, doc in docs.items():
        for kind, rule in rules.items():
            candidate = {**doc, "pass_rule": rule}
            if kind == fits[value_type] and kind == "one_of":
                candidate["pass_rule"] = one_of_rule(["resolved"])
            if kind == fits[value_type]:
                assert _accept(**candidate).pass_rule.kind == kind
            else:
                _reject(field="pass_rule", **candidate)
        assert _accept(**{**doc, "pass_rule": None}).pass_rule is None
        bare = {k: v for k, v in _definition(**doc).items() if k != "pass_rule"}
        model = entity_class("MetricDefinition")
        assert model.model_validate(seal(model, bare)).pass_rule is None


# --------------------------------------------------------------- definition fields


def test_script_sensitive_required_no_default() -> None:
    """ADR 0004 I13: ``script_sensitive`` is a required boolean with no default, in pydantic and
    in the JSON Schema."""
    model = entity_class("MetricDefinition")
    without = {k: v for k, v in _definition().items() if k != "script_sensitive"}
    with pytest.raises(ValidationError) as caught:
        model.model_validate(
            {**without, "content_digest": seal(model, _definition())["content_digest"]}
        )
    assert any("script_sensitive" in e["loc"] for e in caught.value.errors())
    schema = schema_of(model)
    assert "script_sensitive" in schema["required"]
    assert "default" not in schema["properties"]["script_sensitive"]
    assert not schema_accepts(model, without)
    for flag in (True, False):
        assert _accept(script_sensitive=flag).script_sensitive is flag
    for bad in ("yes", "true", 1, 0, None):
        _reject(field="script_sensitive", script_sensitive=bad)


def test_numeric_units_exclude_currency() -> None:
    """ADR 0010 I6: units are a closed non-currency set (ms, ratio, count, percent, wer, ...);
    no ISO 4217 code in any case, no symbol and no word for money is a unit; units belong to
    numeric metrics only."""
    model = entity_class("MetricDefinition")
    units = [u for u in property_enum(model, "unit") if isinstance(u, str)]
    assert {"ms", "ratio", "count", "percent", "wer"} <= set(units)
    codes = {row["code"] for row in data_json("iso4217_active.json")["currencies"]}
    assert not {u.upper() for u in units} & codes
    # revision 8: the default pass rule (max 800) lies outside the domain of ratio and percent, so
    # the positive control carries no pass rule; the assertion (the unit is accepted) is unchanged
    for unit in ("ms", "ratio", "count", "percent", "wer", "cer"):
        assert _accept(unit=unit, pass_rule=None).unit == unit
    money_words = ["usd", "$", "US$", "dollars", "rupees", "currency", "cents", "", "money"]
    for code in sorted(codes):
        _reject(field="unit", unit=code)
        _reject(field="unit", unit=code.lower())
    for bad in money_words:
        _reject(field="unit", unit=bad)
    _reject(field="unit", **judge_definition(unit="ms"))
    _reject(field="unit", **_rating(unit="count"))


def test_metric_key_is_a_metric_key() -> None:
    """Proposed name (ADR 0010 Decision 1 says MachineKey, SCOPE and ADR 0017 define ``MetricKey``
    for our own keys; ambiguity listed in api-surface): lowercase snake case."""
    for key in ("wer", "p95_latency", "tool_call_success", "goal_completion"):
        assert _accept(key=key).key == key
    for bad in ("WER", "p95-latency", "p95.latency", "", " wer", "wer\n", "1wer", "वर", "a b"):
        _reject(field="key", key=bad)


def test_scale_matches_value_type() -> None:
    """Proposed name (ADR 0010 Decision 1: "for rating: integer min/max; for enum: the option
    list"). A rating needs ``scale{min, max}`` with integers min < max, an enum needs
    ``scale{options}`` (non-empty, distinct strings); boolean and numeric take no scale."""
    assert _accept(**_rating()).scale.min == 1
    assert _accept(**_enum()).scale.options == ["resolved", "escalated", "abandoned"]
    for bad in (
        None,
        {"min": 5, "max": 1},
        {"min": 3, "max": 3},
        {"min": 1.5, "max": 5},
        {"min": 1},
        {"max": 5},
        {"options": ["a", "b"]},
        {"min": "1", "max": 5},
        {},
    ):
        _reject(**_rating(scale=bad))
    bad_enums: list[Any] = [
        None,
        {"options": []},
        {"options": ["a", "a"]},
        {"options": [1, 2]},
        {"min": 1, "max": 5},
        {},
    ]
    for bad_enum in bad_enums:
        _reject(**_enum(scale=bad_enum, pass_rule=None))
    _reject(field="scale", **judge_definition(scale={"min": 1, "max": 5}))
    _reject(field="scale", **latency_definition(scale={"min": 1, "max": 5}))
    _reject(field="scale", **judge_definition(scale={"options": ["a"]}))


def test_llm_judge_requires_prompt_template_and_judge_model_ref() -> None:
    """Proposed name (stated by the batch 2 brief; ADR 0010 Decision 1 lists both as optional):
    a ``llm_judge`` definition needs a ``prompt_template`` and a ``judge_model_ref``."""
    assert _accept(**judge_definition()).kind == "llm_judge"
    model = entity_class("MetricDefinition")
    for field in ("prompt_template", "judge_model_ref"):
        doc = {k: v for k, v in _definition(**judge_definition()).items() if k != field}
        assert_rejected(model, doc)
        assert_rejected(model, {**doc, field: None})
    _reject(field="prompt_template", **judge_definition(prompt_template=""))
    _reject(field="judge_model_ref", **judge_definition(judge_model_ref={"provider": "openai"}))


def test_deterministic_kinds_require_implementation_ref() -> None:
    """Proposed name (stated by the batch 2 brief): ``deterministic``, ``audio`` and ``latency``
    definitions name the scorer that computes them in ``implementation_ref`` (a MachineKey);
    a judge needs none."""
    for kind in ("deterministic", "audio", "latency"):
        assert _accept(kind=kind).implementation_ref == "builtin:p95_latency"
        doc = {k: v for k, v in _definition(kind=kind).items() if k != "implementation_ref"}
        assert_rejected(entity_class("MetricDefinition"), doc)
        _reject(field="implementation_ref", kind=kind, implementation_ref=None)
        for bad in ("", "two words", "स्कोरर"):
            _reject(field="implementation_ref", kind=kind, implementation_ref=bad)
    judge = {k: v for k, v in judge_definition().items() if k != "implementation_ref"}
    assert _accept(**judge).kind == "llm_judge"


def test_prompt_template_placeholders_declared_equals_used() -> None:
    """Proposed name (stated by the batch 2 brief): the placeholders ``{{name}}`` used in a
    ``prompt_template`` are exactly the names in ``prompt_variables`` (declared = used, no
    duplicates)."""
    template = "Goal: {{expected_outcome}}\nTranscript:\n{{transcript}}\nAgain {{transcript}}"
    ok = _accept(
        **judge_definition(
            prompt_template=template, prompt_variables=["expected_outcome", "transcript"]
        )
    )
    assert ok.prompt_variables == ["expected_outcome", "transcript"]
    assert _accept(**judge_definition(prompt_template="Plain question.", prompt_variables=[]))
    for template_text, declared in (
        ("Goal {{expected_outcome}} and {{transcript}}", ["expected_outcome"]),
        ("Goal {{expected_outcome}}", ["expected_outcome", "transcript"]),
        ("Goal {{expected_outcome}}", ["transcript"]),
        ("Goal {{expected_outcome}}", ["expected_outcome", "expected_outcome"]),
        ("No placeholders", ["transcript"]),
    ):
        _reject(**judge_definition(prompt_template=template_text, prompt_variables=declared))
    _reject(
        field="prompt_variables", **judge_definition(prompt_variables=["Bad Name", "transcript"])
    )


def test_metric_languages_supported_only() -> None:
    """Proposed name. ``languages`` lists configured languages (SupportedLanguage): native
    script, regions IN/US/GB, canonical case; excluded, romanized and mixed tags are rejected."""
    assert _accept(languages=["HI-in", "en", "pa-Guru"]).languages == ["hi-IN", "en", "pa-Guru"]
    for tag in ("hi-Latn", MIXED_ALIAS_TAG, *EXCLUDED_LANGUAGE_TAGS, "ta-LK", "hindi", "und", ""):
        _reject(field="languages", languages=["en", tag])


def test_aggregation_discriminated_by_kind() -> None:
    """Proposed name (ADR 0010 Decision 3): ``aggregation`` is ``{kind: per_conversation}`` or
    ``{kind: per_turn_share, turn_rule}``; the tag is an enum in the schema, and unknown kinds
    and cross-variant fields are rejected."""
    assert _accept().aggregation.kind == "per_conversation"
    assert _accept(**_turn_share()).aggregation.kind == "per_turn_share"
    for bad in (
        {"kind": "per_call"},
        {"kind": "PER_CONVERSATION"},
        {},
        "per_conversation",
        None,
        {"kind": "per_conversation", "turn_rule": equals_true_rule()},
        {"kind": "per_conversation", "x": 1},
    ):
        _reject(field="aggregation", aggregation=bad)
    schema = schema_of(entity_class("MetricDefinition"))
    tags = sorted(
        tuple(node["properties"]["kind"].get("enum", ()))
        for node in walk_nodes(schema)
        if {"kind", "turn_rule"} <= set(node.get("properties", {}))
        or (set(node.get("properties", {})) == {"kind"} and "per_conversation" in json.dumps(node))
    )
    assert ("per_turn_share",) in tags


# ------------------------------------------------------------------------------ per turn


def test_per_turn_share_bounds_0_1() -> None:
    """P-58 (ADR 0010 I7): for a ``per_turn_share`` definition the pass rule is a range whose
    bounds lie in [0, 1] (the share is a fraction); other definitions are not bounded."""
    for good in (
        {"min": 0.8},
        {"min": 0},
        {"min": 1},
        {"max": 1},
        {"min": 0.25, "max": 0.75},
        {"min": 0, "max": 1},
    ):
        rule = {"kind": "range", "inclusive": True, **good}
        assert _accept(**_turn_share(rule)).pass_rule.kind == "range"
    for bad in (
        {"min": 1.0001},
        {"min": -0.01},
        {"max": 1.5},
        {"max": -0.1},
        {"min": 0.5, "max": 2},
        {"min": 80},
    ):
        _reject(field="pass_rule", **_turn_share({"kind": "range", "inclusive": True, **bad}))
    assert _accept(pass_rule=range_rule(max=5000)).pass_rule.max == 5000
    assert _accept(**{**_turn_share(), "pass_rule": None}).pass_rule is None


def test_per_turn_share_definition_requires_turn_rule() -> None:
    """P-58: ``per_turn_share`` needs a ``turn_rule`` (a pass rule that fits the value type) and
    has no ``min_share`` any more."""
    ok = _accept(**_turn_share())
    assert ok.aggregation.turn_rule.kind == "equals_true"
    for bad in (
        {"kind": "per_turn_share"},
        {"kind": "per_turn_share", "turn_rule": None},
        {"kind": "per_turn_share", "min_share": 0.8},
        {"kind": "per_turn_share", "min_share": 0.8, "turn_rule": equals_true_rule()},
        {"kind": "per_turn_share", "turn_rule": {"kind": "threshold", "value": 1}},
    ):
        doc = _turn_share()
        doc["aggregation"] = bad
        _reject(**doc)
    # the turn rule must fit the per-turn value type
    cases = [
        (judge_definition(), "equals_true", equals_true_rule()),
        (_rating(), "range", {"kind": "range", "min": 4, "inclusive": True}),
        (_enum(pass_rule=None), "one_of", one_of_rule(["resolved"])),
        (latency_definition(pass_rule=None), "range", range_rule()),
    ]
    all_rules = [
        equals_true_rule(),
        {"kind": "range", "min": 1, "inclusive": True},
        one_of_rule(["resolved"]),
    ]
    for doc, fitting_kind, fitting in cases:
        good = {
            **doc,
            "pass_rule": None,
            "aggregation": {"kind": "per_turn_share", "turn_rule": fitting},
        }
        assert _accept(**good).aggregation.turn_rule.kind == fitting_kind
        for other in all_rules:
            if other["kind"] != fitting_kind:
                _reject(**{**good, "aggregation": {"kind": "per_turn_share", "turn_rule": other}})


def test_per_turn_share_pass_rule_must_be_range() -> None:
    """P-58: whatever the value type, the pass rule of a ``per_turn_share`` definition is a
    ``range`` on the share; ``equals_true`` and ``one_of`` are rejected for it."""
    assert _accept(**_turn_share()).pass_rule.kind == "range"
    assert _accept(
        **_enum(
            pass_rule={"kind": "range", "min": 0.5, "inclusive": True},
            aggregation={"kind": "per_turn_share", "turn_rule": one_of_rule(["resolved"])},
        )
    )
    for rule in (equals_true_rule(), one_of_rule(["resolved"])):
        _reject(field="pass_rule", **_turn_share(rule))
    _reject(
        field="pass_rule",
        **_enum(
            pass_rule=one_of_rule(["resolved"]),
            aggregation={"kind": "per_turn_share", "turn_rule": one_of_rule(["resolved"])},
        ),
    )


def test_per_turn_counts_fields_exact() -> None:
    """P-56: ``PerTurnCounts`` is exactly ``{passed, failed, not_applicable, undetermined}``, all
    required integers >= 0."""
    counts = cls("metrics", "PerTurnCounts")
    names = {"passed", "failed", "not_applicable", "undetermined"}
    assert fields_of(counts) == names
    good = {"passed": 6, "failed": 2, "not_applicable": 1, "undetermined": 1}
    assert counts.model_validate(good).model_dump() == good
    assert counts.model_validate(dict.fromkeys(names, 0)).passed == 0
    schema = schema_of(counts)
    assert set(schema["required"]) == names
    assert schema.get("additionalProperties") is False
    for name in names:
        assert schema["properties"][name]["type"] == "integer"
        assert schema["properties"][name]["minimum"] == 0
        for bad in (-1, 1.5, 1.0, "1", True, None):
            with pytest.raises(ValidationError):
                counts.model_validate({**good, name: bad})
        with pytest.raises(ValidationError):
            counts.model_validate({k: v for k, v in good.items() if k != name})
    with pytest.raises(ValidationError):
        counts.model_validate({**good, "total": 10})


def _counts(passed: int, failed: int, not_applicable: int, undetermined: int) -> Any:
    return cls("metrics", "PerTurnCounts")(
        passed=passed, failed=failed, not_applicable=not_applicable, undetermined=undetermined
    )


def test_per_turn_share_excludes_na_turns_from_denominator() -> None:
    """P-26 (and P-56 for undetermined turns): ``share = passed / (passed + failed)`` as a canonical
    decimal string, half-even at scale 18; not_applicable and undetermined turns are not counted."""
    share = _m().per_turn_share
    assert share(_counts(3, 1, 6, 0)) == "0.75"
    assert share(_counts(6, 2, 1, 1)) == "0.75"
    assert share(_counts(2, 1, 0, 0)) == "0.666666666666666667"
    assert share(_counts(1, 2, 0, 0)) == "0.333333333333333333"
    assert share(_counts(1, 0, 9, 9)) == "1"
    assert share(_counts(0, 3, 2, 0)) == "0"
    # a terminating quotient longer than 18 digits is rounded half-even, not half-up
    assert share(_counts(1, 524_287, 0, 0)) == "0.000001907348632812"
    decimal: Any = api("primitives").DecimalString
    assert TypeAdapter(decimal).validate_python(share(_counts(2, 1, 0, 0)))


def test_per_turn_share_not_applicable_when_no_turn_applies() -> None:
    """P-26: no counted turn and every turn not_applicable: the metric is ``not_applicable`` and
    there is no share (never 0)."""
    status, share = _m().per_turn_status, _m().per_turn_share
    assert status(_counts(0, 0, 5, 0)) == "not_applicable"
    assert share(_counts(0, 0, 5, 0)) is None
    assert status(_counts(3, 1, 0, 0)) == "scored"
    assert status(_counts(0, 1, 0, 0)) == "scored"


def test_per_turn_status_no_counted_turn_cases() -> None:
    """Proposed name (P-56 detail in ADR 0010 Decision 3): with no counted turn and any
    undetermined turn the outcome is ``error`` (code judge_undetermined), else ``not_applicable``;
    an all-zero count has no turns and is a ValueError."""
    status = _m().per_turn_status
    assert status(_counts(0, 0, 0, 4)) == "error"
    assert status(_counts(0, 0, 3, 2)) == "error"
    assert status(_counts(0, 0, 3, 0)) == "not_applicable"
    with pytest.raises(ValueError, match="turn"):
        status(_counts(0, 0, 0, 0))
    assert _m().per_turn_share(_counts(0, 0, 0, 4)) is None


# ------------------------------------------------------------------------------ MetricBinding


def test_metric_binding_shape_and_params() -> None:
    """Proposed name (ADR 0010 Decision 4 and the batch 2 brief): a binding is a versioned entity
    ``{evaluator_id, metric: {id, version}, params, enabled}``; the metric reference is a
    ``MetricDefinition`` id with a version; ``params`` are JSON overrides stored unchanged."""
    binding = entity_class("MetricBinding")
    loaded = make("MetricBinding")
    assert loaded.enabled is True
    assert loaded.params == {}
    assert str(loaded.evaluator_id).startswith("evl_")
    assert str(loaded.metric.id).startswith("met_")
    assert loaded.metric.version == 1
    params = {
        "threshold_ms": 800,
        "ratio": 0.5,
        "labels": ["a", None, True],
        "nested": {"k": {"j": []}},
    }
    assert make("MetricBinding", params=params).params == params
    assert make("MetricBinding", enabled=False).enabled is False
    for bad in ("yes", 1, None):
        assert_rejected_name("MetricBinding", enabled=bad, field="enabled")
    bad_params: list[Any] = [{"x": {1, 2}}, {"x": b"bytes"}, {"x": object()}, "text", [1], None]
    for bad_param in bad_params:
        assert_rejected_name("MetricBinding", params=bad_param)
    for bad_ref in (
        ref("per"),
        ref("evl"),
        {"id": ref("met")["id"]},
        {"id": "met_x", "version": 1},
        {**ref("met"), "version": 0},
        None,
    ):
        assert_rejected_name("MetricBinding", metric=bad_ref, field="metric")
    for bad_eval in (ref("met")["id"], "", None, "evl_x"):
        assert_rejected_name("MetricBinding", evaluator_id=bad_eval, field="evaluator_id")
    errors = api("errors")
    with pytest.raises((ValidationError, errors.NonIJsonValueError)):
        binding.model_validate(
            seal(binding, raw_document("MetricBinding")) | {"params": {"x": float("nan")}}
        )
    assert schema_accepts(binding, make("MetricBinding", params=params).model_dump(mode="json"))


# ================================================================================ revision 8
# The tests below pin docs/phase1/api-surface.md sections 19.4 and 19.4.1 (revision 8) and the
# owner's answers to E5-E7, P-59 and P-60. Module locations that section 19 does not fix are
# marked "Proposed location": ``tracer_evalschema.metrics.render_metric_ref`` and
# ``tracer_evalschema.metrics.check_binding_params`` (next to ``check_bindings_unique``).


def _r(inclusive: bool = True, **bounds: Any) -> dict[str, Any]:
    """A ``range`` pass rule document."""
    return {"kind": "range", "inclusive": inclusive, **bounds}


def _binding_of(definition: Any, params: dict[str, Any]) -> Any:
    """A binding of ``definition`` (same id and version) with ``params``."""
    return make(
        "MetricBinding",
        metric={"id": str(definition.id), "version": definition.version},
        params=params,
    )


def _bound(
    parameters: list[dict[str, Any]], params: dict[str, Any], **definition_over: Any
) -> tuple[Any, Any]:
    """A latency definition declaring ``parameters`` and a binding of it with ``params``."""
    definition = make(
        "MetricDefinition", **latency_definition(parameters=parameters, **definition_over)
    )
    return definition, _binding_of(definition, params)


def _check_params(binding: Any, definition: Any) -> None:
    _m().check_binding_params(binding, definition)


def _rejects_naming(binding: Any, definition: Any, key: str) -> None:
    """``check_binding_params`` raises a ``ValueError`` that names the binding ID and the key."""
    with pytest.raises(ValueError, match=str(binding.id)) as caught:
        _check_params(binding, definition)
    assert key in str(caught.value), (key, str(caught.value))


# ------------------------------------------------------------------- references (resolution 1)


def test_metric_ref_text_form_is_display_only() -> None:
    """Resolution 1 / ADR 0009 Decision 4 (I9a): a metric reference is the object
    ``MetricDefinitionRef{id, version, content_digest?}`` and a string is rejected. ``key@version``
    (``p95_latency@3``) is display text made by ``render_metric_ref(definition)``; it is never
    stored and never parsed. Keys do not identify definitions: two definitions may share one.
    (The equality of ``VerdictCheck.metric_ref`` with the binding's ``metric`` is P1-22 and is not
    tested here.)"""
    metrics = _m()
    ref_model = registered_class("MetricDefinitionRef")
    assert issubclass(ref_model, api("base").VersionedRef)
    assert fields_of(ref_model) == {"id", "version", "content_digest"}
    first = make("MetricDefinition", version=3)
    other = make("MetricDefinition")
    assert first.id != other.id
    assert metrics.render_metric_ref(first) == "p95_latency@3"
    assert metrics.render_metric_ref(other) == "p95_latency@1"
    again = make("MetricDefinition", **latency_definition(key="p95_latency", unit="count"))
    assert again.id != other.id
    assert metrics.render_metric_ref(again) == metrics.render_metric_ref(other)
    as_object = {"id": str(first.id), "version": first.version}
    loaded = ref_model.model_validate(as_object)
    assert isinstance(loaded.id, api("ids").MetricDefinitionId)
    assert json.loads(loaded.model_dump_json(exclude_none=True)) == as_object
    text_forms = [
        "p95_latency@3",
        f"{first.id}@3",
        str(first.id),
        "3",
        "1.2.0",
        "p95_latency@1.2.0",
    ]
    for text in text_forms:
        with pytest.raises(ValidationError):
            ref_model.model_validate(text)
        with pytest.raises(ValidationError):
            ref_model.model_validate_json(json.dumps(text))
        assert_rejected_name("MetricBinding", metric=text, field="metric")
    for bad in (
        {"key": "p95_latency", "version": 3},
        {**as_object, "key": "p95_latency"},
        {"id": "p95_latency@3", "version": 3},
        {**as_object, "version": "3"},
        {**as_object, "version": "1.2.0"},
        {**as_object, "version": 1.5},
        {**as_object, "version": True},
    ):
        with pytest.raises(ValidationError):
            ref_model.model_validate(bad)
    assert make("MetricBinding", metric=as_object).metric.version == 3
    for name in dir(metrics):
        assert not re.search(
            r"(parse|from_text|from_str).*ref|ref.*(parse|from_text|from_str)", name
        )


# --------------------------------------------------------------- kind conditions (resolution 4)


def test_judge_model_ref_is_a_model_ref_without_config_ref() -> None:
    """Resolution 4: ``judge_model_ref`` is ``ModelRef{provider, model}`` (value object, open
    ``MachineKey`` values) and carries no ``config_ref``: a definition is portable and digested,
    while a ``config_ref`` names one deployment's variable (ADR 0010 Decision 1a)."""
    model_ref_class = registered_class("ModelRef")
    assert fields_of(model_ref_class) == {"provider", "model"}
    assert not {"id", "schema_version", "created_at", "metadata"} & fields_of(model_ref_class)
    schema = schema_of(model_ref_class)
    assert set(schema["required"]) == {"provider", "model"}
    assert schema.get("additionalProperties") is False
    assert model_ref_class.model_validate(model_ref()).provider == "openai"
    unseen = model_ref_class.model_validate(model_ref(provider="new-vendor", model="org/M:v2"))
    assert (unseen.provider, unseen.model) == ("new-vendor", "org/M:v2")
    for extra in (
        {"config_ref": "env:OPENAI_API_KEY"},
        {"api_key": "x"},
        {"base_url": "https://x"},
    ):
        with pytest.raises(ValidationError):
            model_ref_class.model_validate({**model_ref(), **extra})
    for field in ("provider", "model"):
        with pytest.raises(ValidationError):
            model_ref_class.model_validate({k: v for k, v in model_ref().items() if k != field})
        for bad in ("", "two words", "प्रदाता", "x" * 257, None, 7):
            with pytest.raises(ValidationError):
                model_ref_class.model_validate(model_ref(**{field: bad}))
    judge = _accept(**judge_definition())
    assert isinstance(judge.judge_model_ref, model_ref_class)
    assert judge.model_dump(mode="json")["judge_model_ref"] == model_ref()
    for with_key in (provider_ref(), model_ref(config_ref="env:OPENAI_API_KEY")):
        _reject(field="judge_model_ref", **judge_definition(judge_model_ref=with_key))
    model = entity_class("MetricDefinition")
    for mode in ("validation", "serialization"):
        assert "config_ref" not in property_names(model, mode)


def test_judge_only_fields_forbidden_on_other_kinds() -> None:
    """Resolution 4 (ADR 0010 Decision 1a): ``prompt_template``, ``prompt_variables`` and
    ``judge_model_ref`` are required when ``kind=llm_judge`` and forbidden for the other kinds;
    ``implementation_ref`` is the other way round. A judge without its prompt and model cannot be
    reproduced, and a scorer name on a judge would name two computations for one value."""
    judge_fields: dict[str, Any] = {
        "prompt_template": JUDGE_TEMPLATE,
        "prompt_variables": ["expected_outcome", "transcript"],
        "judge_model_ref": model_ref(),
    }
    for kind in ("deterministic", "audio", "latency"):
        assert _accept(kind=kind).kind == kind
        for field, value in judge_fields.items():
            _reject(field=field, **latency_definition(kind=kind, **{field: value}))
        _reject(**latency_definition(kind=kind, **judge_fields))
    _reject(field="implementation_ref", **judge_definition(implementation_ref="builtin:judge"))
    # a judge needs all three fields: the key itself may not be omitted
    model = entity_class("MetricDefinition")
    plain = judge_definition(prompt_template="Plain question.", prompt_variables=[])
    assert _accept(**plain).prompt_variables == []
    for field in ("prompt_template", "prompt_variables", "judge_model_ref"):
        assert_rejected(model, {k: v for k, v in _definition(**plain).items() if k != field})


def test_implementation_ref_is_namespaced() -> None:
    """Resolution 4 (ADR 0010 Decision 1a): ``implementation_ref`` is a ``MachineKey`` of the form
    ``<namespace>:<name>`` with the namespace ``[a-z][a-z0-9_]*`` (``builtin:wer``); the runtime's
    scorer registry resolves it (Phase 3). Proposed name."""
    for good in ("builtin:wer", "acme_corp:wer_v2", "x1:y", "builtin:p95.latency"):
        assert _accept(implementation_ref=good).implementation_ref == good
    for bad in (
        "wer",
        "Builtin:wer",
        "1ns:wer",
        "built-in:wer",
        "_ns:wer",
        "builtin:",
        ":wer",
        "builtin:two words",
        "builtin:वर",
        "builtin:" + "x" * 300,
    ):
        _reject(field="implementation_ref", implementation_ref=bad)


def test_prompt_template_rejects_other_double_braces() -> None:
    """Resolution 4 (ADR 0010 Decision 1a): any ``{{`` in a template that is not a well-formed
    ``{{name}}`` variable (a ``MetricKey``) is rejected, so a mistyped variable cannot reach the
    judge as literal text. Single braces and a lone ``}}`` are ordinary text. Proposed name."""
    for template, declared in (
        ("Q {{transcript}} and {single} and }} done", ["transcript"]),
        ("No variable, {just} braces", []),
    ):
        assert _accept(**judge_definition(prompt_template=template, prompt_variables=declared))
    for template in (
        "Q {{ transcript }}",
        "Q {{Transcript}}",
        "Q {{transcript",
        "Q {{transcript}",
        "Q {{profile.name}}",
        "Q {{}}",
        "Q {{ }}",
        "Q {{two words}}",
        "Q {{1st}}",
    ):
        # the declared variables are exactly the well-formed ones used, so only the stray braces
        # can be the reason
        stray = "Goal {{expected_outcome}} " + template + " then {{transcript}}"
        _reject(**judge_definition(prompt_template=stray))
        _reject(**judge_definition(prompt_template=template, prompt_variables=["transcript"]))


def test_expected_outcome_variable_definition_shapes() -> None:
    """Resolution 7 (ADR 0013 Decision 2): the evaluator's expected outcome reaches its check
    through the reserved prompt variable ``expected_outcome``. The rule that every evaluator has an
    expected-outcome check, and that it is a required rubric item, is the ConfigSnapshot's (P1-12).
    Here: the variable is an ordinary declared variable in every judge shape, an
    expected-outcome check (boolean, per conversation, ``equals_true``) is a valid definition, and
    other shapes that use the variable are valid ordinary metrics. Proposed name."""
    check = _accept(**judge_definition())
    assert check.pass_rule.kind == "equals_true"
    assert "expected_outcome" in check.prompt_variables
    rating = _accept(
        **_rating(
            prompt_template="Rate how well {{expected_outcome}} was met: {{transcript}}",
            prompt_variables=["expected_outcome", "transcript"],
        )
    )
    assert rating.value_type == "rating"
    per_turn = _accept(**_turn_share(prompt_variables=["expected_outcome", "transcript"]))
    assert per_turn.aggregation.kind == "per_turn_share"
    _reject(**judge_definition(prompt_variables=["transcript"]))
    _reject(**judge_definition(prompt_variables=["expected_outcome"]))
    # the variable is a judge-only field: a scorer-based definition cannot declare it
    _reject(**latency_definition(prompt_variables=["expected_outcome"]))


def test_conditional_field_rules_are_in_the_json_schema() -> None:
    """Section 19.4.1: rules that need several fields are checked by a model validator and, where
    JSON Schema can express them, emitted as ``allOf`` of ``if``/``then``. The kind conditions of
    the judge fields and ``implementation_ref``, and the scale and unit conditions of the value
    type, are expressible (they are not in the code-only list of N12), so the schema must refuse
    what pydantic refuses. Proposed name."""
    model = entity_class("MetricDefinition")
    latency, judge = latency_definition(), judge_definition()
    valid = [latency, judge, _rating(), _enum(), _turn_share()]
    for doc in valid:
        assert schema_accepts(model, seal(model, _definition(**doc))), doc["key"]

    def without(doc: dict[str, Any], *names: str) -> dict[str, Any]:
        return {k: v for k, v in doc.items() if k not in names}

    invalid: list[tuple[str, dict[str, Any]]] = [
        ("judge with implementation_ref", {**judge, "implementation_ref": "builtin:x"}),
        ("scorer with prompt_template", {**latency, "prompt_template": JUDGE_TEMPLATE}),
        ("scorer with judge_model_ref", {**latency, "judge_model_ref": model_ref()}),
        ("scorer with prompt_variables", {**latency, "prompt_variables": ["transcript"]}),
        ("scorer without implementation_ref", without(latency, "implementation_ref")),
        ("judge without prompt_template", without(judge, "prompt_template")),
        ("judge without judge_model_ref", without(judge, "judge_model_ref")),
        ("judge without prompt_variables", without(judge, "prompt_variables")),
        ("rating without scale", without(_rating(), "scale")),
        ("enum without scale", without(_enum(), "scale")),
        ("boolean with scale", {**judge, "scale": {"min": 1, "max": 5}}),
        ("numeric with scale", {**latency, "scale": {"options": ["a"]}}),
        ("numeric without unit", without(latency, "unit")),
        ("boolean with unit", {**judge, "unit": "ms"}),
        ("rating with unit", {**_rating(), "unit": "count"}),
    ]
    for label, doc in invalid:
        assert not schema_accepts(model, _definition(**doc)), label
        assert_rejected(model, _definition(**doc))


# ------------------------------------------------------------------- parameters (E6)


def test_metric_parameter_shape_and_defaults() -> None:
    """Section 19.4.1: ``MetricParameter{name: MetricKey, value_type, default?}`` is a value object
    and ``MetricDefinition.parameters`` (default ``[]``, names unique) declares what a binding's
    ``params`` may set. A ``default`` must match the ``value_type``: ``integer`` is a JSON integer,
    ``number`` a finite JSON number, ``boolean`` and ``integer`` never accept each other, and
    ``json`` is any I-JSON value. Proposed name."""
    param = registered_class("MetricParameter")
    assert fields_of(param) == {"name", "value_type", "default"}
    assert not {"id", "schema_version", "created_at", "metadata"} & fields_of(param)
    schema = schema_of(param)
    assert set(schema["required"]) == {"name", "value_type"}
    assert schema.get("additionalProperties") is False
    assert param.model_validate(parameter()).default is None
    for value_type in ("boolean", "integer", "number", "string", "json"):
        assert param.model_validate(parameter(value_type=value_type)).value_type == value_type
    for bad in ("float", "Integer", "int", "object", "", None, 3):
        with pytest.raises(ValidationError):
            param.model_validate(parameter(value_type=bad))
    for bad_name in ("Percentile", "a b", "", "1x", "a-b", None, 7):
        with pytest.raises(ValidationError):
            param.model_validate(parameter(name=bad_name))
    with pytest.raises(ValidationError):
        param.model_validate({**parameter(), "threshold": 1})
    errors = api("errors")
    nan = float("nan")
    defaults: dict[str, tuple[list[Any], list[Any]]] = {
        "boolean": ([True, False], [1, 0, "true", [True]]),
        "integer": ([0, 7, -1], [True, False, 1.5, "7", [1]]),
        "number": ([0, 1.5, -2.25], [True, "1.5", [1], nan, float("inf")]),
        "string": (["", "x", "नमस्ते"], [1, True, ["x"]]),
        "json": ([0, "x", True, 1.5, [1, None], {"a": {"b": []}}], [{1, 2}, b"x", nan]),
    }
    for value_type, (good, bad_defaults) in defaults.items():
        for value in good:
            loaded = param.model_validate(parameter(value_type=value_type, default=value))
            assert loaded.default == value
        for value in bad_defaults:
            with pytest.raises((ValidationError, errors.NonIJsonValueError)):
                param.model_validate(parameter(value_type=value_type, default=value))
    assert _accept().parameters == []
    declared = [parameter("percentile", "integer", default=95), parameter("lenient", "boolean")]
    assert [p.name for p in _accept(parameters=declared).parameters] == ["percentile", "lenient"]
    for bad_parameters in (
        [parameter("percentile"), parameter("percentile", "string")],
        [parameter("percentile"), parameter("percentile")],
        "percentile",
        [{"name": "percentile"}],
        None,
    ):
        _reject(field="parameters", parameters=bad_parameters)


_PARAMETER_VALUES: dict[str, tuple[list[Any], list[Any]]] = {
    "boolean": ([True, False], [1, 0, "true", None, [True]]),
    "integer": ([0, 95, -3], [True, False, 1.5, "95", None, [95]]),
    "number": ([0, 1.5, -2, 0.0], [True, False, "1", None, [1.5]]),
    "string": (["", "abc", "नमस्ते"], [1, True, None, ["a"]]),
    "json": ([None, 1, 1.5, True, "x", [1, {"a": None}], {"k": [True]}], []),
}


def test_binding_params_must_be_declared_and_typed() -> None:
    """Resolution 4 (ADR 0010 Decision 4a): when a binding meets its definition,
    ``check_binding_params(binding, definition)`` requires that every key is a declared parameter,
    every value matches the declared ``value_type`` strictly (no bool/number coercion), and every
    declared parameter without a default is present. A ``ValueError`` names the binding ID and the
    key. The effective values are the defaults overlaid by ``params`` (so a default makes a
    parameter optional)."""
    declared = [parameter("percentile", "integer"), parameter("window", "number", default=2.5)]
    definition = make("MetricDefinition", **latency_definition(parameters=declared))

    def check(params: dict[str, Any]) -> Any:
        return _binding_of(definition, params)

    _check_params(check({"percentile": 95}), definition)
    _check_params(check({"percentile": 99, "window": 3}), definition)
    # a required parameter that is missing
    _rejects_naming(check({"window": 1.5}), definition, "percentile")
    _rejects_naming(check({}), definition, "percentile")
    # an undeclared parameter, also next to valid ones
    _rejects_naming(check({"percentile": 95, "p99": 1}), definition, "p99")
    nothing_def, nothing = _bound([], {"anything": 1})
    _rejects_naming(nothing, nothing_def, "anything")
    none_def, none_declared = _bound([], {})
    _check_params(none_declared, none_def)
    # a default is used only when the parameter is not given; a given value is still typed
    _rejects_naming(check({"percentile": 95, "window": "wide"}), definition, "window")
    for value_type, (good, bad) in _PARAMETER_VALUES.items():
        one = [parameter("value", value_type)]
        for value in good:
            one_def, one_binding = _bound(one, {"value": value})
            _check_params(one_binding, one_def)
        for value in bad:
            bad_def, bad_binding = _bound(one, {"value": value})
            _rejects_naming(bad_binding, bad_def, "value")


def test_binding_cannot_change_the_pass_rule() -> None:
    """E6 (accepted by the owner): a binding cannot change a pass threshold. Two evaluators that
    want p95 limits of 800 ms and 1500 ms need two definitions (or two versions), not one definition
    with two binding params. ``params`` set declared parameters only, so a key that names a
    definition field (pass rule, bounds, requires, scale, unit, aggregation, languages, kind,
    prompt) is rejected as undeclared, and the binding model has no field that could carry a rule.
    Proposed name."""
    declared = [parameter("percentile", "integer", default=95)]
    definition = make("MetricDefinition", **latency_definition(parameters=declared))
    _check_params(_binding_of(definition, {"percentile": 99}), definition)
    attempts: dict[str, Any] = {
        "pass_rule": _r(max=1500),
        "max": 1500,
        "min": 100,
        "threshold": 1500,
        "threshold_ms": 1500,
        "inclusive": False,
        "requires": ["audio"],
        "languages": ["en"],
        "scale": {"min": 1, "max": 5},
        "unit": "count",
        "aggregation": {"kind": "per_conversation"},
        "script_sensitive": True,
        "kind": "audio",
        "prompt_template": "x",
        "judge_model_ref": model_ref(),
    }
    for key, value in attempts.items():
        _rejects_naming(_binding_of(definition, {key: value}), definition, key)
        _rejects_naming(_binding_of(definition, {"percentile": 99, key: value}), definition, key)
    binding_model = entity_class("MetricBinding")
    assert fields_of(binding_model) == {
        "id",
        "schema_version",
        "created_at",
        "metadata",
        "metadata_flags",
        "version",
        "content_digest",
        "evaluator_id",
        "metric",
        "params",
        "enabled",
    }
    for field in ("pass_rule", "threshold", "max", "min"):
        assert_rejected(
            entity_class("MetricBinding"), raw_document("MetricBinding", **{field: _r(max=1500)})
        )
    assert definition.pass_rule.max == 800, "the definition keeps the one threshold"


def test_metric_binding_params_keys_are_metric_keys() -> None:
    """Section 19.4.1: ``MetricBinding.params`` is ``dict[MetricKey, I-JSON]``: the names are
    lowercase snake case like the declared parameters, the dict is required (it may be empty), and
    the values are stored unchanged. Proposed name."""
    assert make("MetricBinding", params={"threshold_ms": 800, "a1": None}).params == {
        "threshold_ms": 800,
        "a1": None,
    }
    for bad in ("Threshold", "a b", "", "1x", "a-b", "a__b", "वर"):
        assert_rejected_name("MetricBinding", params={bad: 1}, field="params")
    model = entity_class("MetricBinding")
    without = {k: v for k, v in raw_document("MetricBinding").items() if k != "params"}
    assert_rejected(model, without)
    assert "params" in schema_of(model)["required"]


# ------------------------------------------------------------------------ domains (resolution 5)


def test_scale_and_unit_domains() -> None:
    """Resolution 5 (ADR 0010 Decision 1a): the units are exactly ``ms``, ``count``, ``ratio``,
    ``percent``, ``wer``, ``cer`` (a string ``enum``); the scale is flat: ``RatingScale{min, max}``
    for a rating and ``EnumScale{options: list[MetricKey]}`` for an enum; aggregation is a
    ``kind``-tagged object. The value domains of the units are exercised by
    ``test_range_bounds_within_value_domain``."""
    model = entity_class("MetricDefinition")
    units = [u for u in property_enum(model, "unit") if isinstance(u, str)]
    assert sorted(units) == sorted(["ms", "count", "ratio", "percent", "wer", "cer"])
    for unit in units:
        assert _accept(unit=unit, pass_rule=None).unit == unit
        assert schema_accepts(model, _definition(unit=unit, pass_rule=None))
    for bad in ("seconds", "MS", "ms ", "bytes", "tokens", "wpm", "fraction", "%", ""):
        _reject(field="unit", unit=bad)
    assert not schema_accepts(model, _definition(unit="usd"))
    rating, enum = registered_class("RatingScale"), registered_class("EnumScale")
    assert fields_of(rating) == {"min", "max"}
    assert fields_of(enum) == {"options"}
    assert isinstance(_accept(**_rating()).scale, rating)
    assert isinstance(_accept(**_enum()).scale, enum)
    assert _accept(**_rating(scale={"min": -2, "max": 2}, pass_rule=None)).scale.min == -2
    for bad_scale in ({"min": 3, "max": 3}, {"min": 5, "max": 1}, {"min": 1.5, "max": 5}):
        _reject(field="scale", **_rating(scale=bad_scale, pass_rule=None))
    for bad_options in (["Resolved"], ["not resolved"], [""], ["a-b"], ["1st"], ["resolved", "A"]):
        _reject(**_enum(scale={"options": bad_options}, pass_rule=None))
    assert _accept(**_enum(scale={"options": ["not_resolved", "ok2"]}, pass_rule=None))
    per_conversation = registered_class("PerConversationAggregation")
    per_turn = registered_class("PerTurnShareAggregation")
    assert fields_of(per_conversation) == {"kind"}
    assert fields_of(per_turn) == {"kind", "turn_rule"}
    assert isinstance(_accept().aggregation, per_conversation)
    assert isinstance(_accept(**_turn_share()).aggregation, per_turn)
    assert _accept().model_dump(mode="json")["aggregation"] == {"kind": "per_conversation"}


_NUMERIC_BOUNDS: list[tuple[str, dict[str, Any], bool]] = [
    # (unit, range rule, accepted). ms >= 0, count integers >= 0, ratio [0,1], percent [0,100],
    # wer and cer >= 0 and may exceed 1. A bound outside the domain, or a range that no value of
    # the domain passes, is rejected; count counts integers only.
    ("ms", _r(max=0), True),
    ("ms", _r(min=0), True),
    ("ms", _r(min=0, inclusive=False), True),
    ("ms", _r(max=1e12), True),
    ("ms", _r(max=0, inclusive=False), False),
    ("ms", _r(min=-1), False),
    ("ms", _r(min=-1, max=5), False),
    ("ms", _r(max=-0.5), False),
    ("count", _r(max=0), True),
    ("count", _r(max=0.5), True),
    ("count", _r(max=0.5, inclusive=False), True),
    ("count", _r(min=0.5, max=1.5), True),
    ("count", _r(min=2, max=2), True),
    ("count", _r(min=1, max=3, inclusive=False), True),
    ("count", _r(max=0, inclusive=False), False),
    ("count", _r(min=0.5, max=0.9), False),
    ("count", _r(min=2.5, max=2.9999), False),
    ("count", _r(min=2, max=2, inclusive=False), False),
    ("count", _r(min=2, max=3, inclusive=False), False),
    ("count", _r(min=-1), False),
    ("ratio", _r(max=1), True),
    ("ratio", _r(min=0), True),
    ("ratio", _r(min=0.2, max=0.8), True),
    ("ratio", _r(min=0.5, max=0.5), True),
    ("ratio", _r(min=1, max=1), True),
    ("ratio", _r(min=0, max=0), True),
    ("ratio", _r(max=1.5), False),
    ("ratio", _r(min=-0.1), False),
    ("ratio", _r(min=1.1), False),
    ("ratio", _r(max=800), False),
    ("ratio", _r(max=1.0000000000000002), False),
    ("ratio", _r(min=1, inclusive=False), False),
    ("ratio", _r(max=0, inclusive=False), False),
    ("ratio", _r(min=0.5, max=0.5, inclusive=False), False),
    ("percent", _r(min=95), True),
    ("percent", _r(max=100), True),
    ("percent", _r(min=0, max=100), True),
    ("percent", _r(max=100.5), False),
    ("percent", _r(max=800), False),
    ("percent", _r(min=-1), False),
    ("percent", _r(min=100, inclusive=False), False),
    ("wer", _r(max=0.25), True),
    ("wer", _r(max=1.5), True),
    ("wer", _r(max=25), True),
    ("wer", _r(max=0), True),
    ("wer", _r(min=-0.1), False),
    ("wer", _r(max=0, inclusive=False), False),
    ("cer", _r(max=0.1), True),
    ("cer", _r(max=2), True),
    ("cer", _r(min=-1), False),
    ("cer", _r(max=0, inclusive=False), False),
]

_RATING_BOUNDS: list[tuple[dict[str, Any], dict[str, Any], bool]] = [
    # (scale, range rule, accepted). Bounds lie within the scale; a rating takes integers only.
    ({"min": 1, "max": 5}, _r(min=4), True),
    ({"min": 1, "max": 5}, _r(min=1), True),
    ({"min": 1, "max": 5}, _r(max=5), True),
    ({"min": 1, "max": 5}, _r(min=5), True),
    ({"min": 1, "max": 5}, _r(max=1), True),
    ({"min": 1, "max": 5}, _r(min=3, max=4), True),
    ({"min": 1, "max": 5}, _r(min=4.5), True),
    ({"min": 1, "max": 5}, _r(max=1.5), True),
    ({"min": 1, "max": 5}, _r(min=2, max=4, inclusive=False), True),
    ({"min": 1, "max": 5}, _r(min=0), False),
    ({"min": 1, "max": 5}, _r(max=0), False),
    ({"min": 1, "max": 5}, _r(max=6), False),
    ({"min": 1, "max": 5}, _r(min=6), False),
    ({"min": 1, "max": 5}, _r(min=-3, max=2), False),
    ({"min": 1, "max": 5}, _r(min=4.2, max=4.8), False),
    ({"min": 1, "max": 5}, _r(min=5, inclusive=False), False),
    ({"min": 1, "max": 5}, _r(max=1, inclusive=False), False),
    ({"min": 1, "max": 5}, _r(min=2, max=3, inclusive=False), False),
    ({"min": 0, "max": 10}, _r(min=0), True),
    ({"min": 0, "max": 10}, _r(max=10.0001), False),
    ({"min": -2, "max": 2}, _r(min=-2), True),
    ({"min": -2, "max": 2}, _r(min=-3), False),
]


def test_range_bounds_within_value_domain() -> None:
    """Resolution 5 (ADR 0010 Decision 1a, section 19.4.1): range bounds lie within the value
    domain (the scale for a rating, the unit's domain for a numeric metric, [0, 1] for the share)
    and the set of passing values is non-empty, counting integers only for a rating and for
    ``count``. Bounds are compared in ``Decimal``, a float read through its shortest text."""
    for unit, rule, accepted in _NUMERIC_BOUNDS:
        doc = latency_definition(unit=unit, pass_rule=rule)
        if accepted:
            assert _accept(**doc).pass_rule.kind == "range", (unit, rule)
        else:
            _reject(**doc)
            assert _accept(**{**doc, "pass_rule": None}).pass_rule is None, "control"
    for scale, rule, accepted in _RATING_BOUNDS:
        doc = _rating(scale=scale, pass_rule=rule)
        if accepted:
            assert _accept(**doc).pass_rule.kind == "range", (scale, rule)
        else:
            _reject(**doc)
            assert _accept(**{**doc, "pass_rule": None}).pass_rule is None, "control"
    # the share of a per-turn metric lies in [0, 1] and some share must pass
    for rule in (_r(min=1), _r(max=0), _r(min=1, max=1), _r(min=0, max=0), _r(min=0.5, max=0.5)):
        assert _accept(**_turn_share(rule)).pass_rule.kind == "range", rule
    for rule in (
        _r(min=1, inclusive=False),
        _r(max=0, inclusive=False),
        _r(min=0.5, max=0.5, inclusive=False),
        _r(min=1.0000000000000002),
        _r(max=-0.0000001),
    ):
        _reject(**_turn_share(rule))
    # the turn rule of a per-turn metric obeys the domain of its per-turn value
    rating_turn = _rating(pass_rule=None)
    for rule, accepted in ((_r(min=4), True), (_r(min=9), False), (_r(max=0), False)):
        doc = {**rating_turn, "aggregation": {"kind": "per_turn_share", "turn_rule": rule}}
        if accepted:
            assert _accept(**doc).aggregation.turn_rule.kind == "range"
        else:
            _reject(**doc)
    ratio_turn = latency_definition(unit="ratio", pass_rule=None)
    for rule, accepted in ((_r(max=0.5), True), (_r(max=800), False), (_r(min=-1), False)):
        doc = {**ratio_turn, "aggregation": {"kind": "per_turn_share", "turn_rule": rule}}
        if accepted:
            assert _accept(**doc).aggregation.turn_rule.kind == "range"
        else:
            _reject(**doc)


# ------------------------------------------------------------------------ languages (item 12)


def test_metric_languages_nonempty_distinct_primary() -> None:
    """Resolution 12 (ADR 0005 Decision 6, ADR 0004 Decision 6): ``MetricDefinition.languages`` is
    non-empty (schema ``minItems: 1``) and no two entries share a primary language, because
    coverage is decided by primary language. An empty list is rejected, never read as "none" or
    "all"; ``hi`` next to ``hi-IN`` would suggest a region distinction that the matching never
    makes."""
    nine = ["en-US", "hi-IN", "mr", "te", "ta-IN", "kn", "ml", "pa-Guru-IN", "gu"]
    for good in (nine, ["hi-IN", "en"], ["en-US"], ["HI-in", "en-GB"]):
        assert len(_accept(languages=good).languages) == len(good)
    for bad in (
        [],
        ["hi", "hi-IN"],
        ["hi-IN", "hi"],
        ["en-US", "en-GB"],
        ["pa-Guru", "pa-Guru-IN"],
        ["hi", "hi"],
        ["HI", "hi"],
        ["hi-IN", "HI-in"],
        ["en", "hi-IN", "en-GB"],
    ):
        _reject(field="languages", languages=bad)
    model = entity_class("MetricDefinition")
    assert not schema_accepts(model, _definition(languages=[]))
    nodes = list(walk_nodes(schema_of(model)["properties"]["languages"]))
    assert any(node.get("minItems", 0) >= 1 for node in nodes), "the schema states minItems"


def test_metric_languages_accepts_language_independent_value() -> None:
    """P-59 (a), accepted by the owner: ``MetricDefinition.languages`` may be the single value
    ``"language_independent"`` instead of a list (emitted as a string ``enum``), for a metric that
    does not depend on language (latency, interruption counts, talk ratio). Pinned reading: the
    scalar string, not a one-element list. That such a metric skips the language check, and that a
    language-dependent metric on untagged input is ``not_applicable`` with ``language_not_observed``
    (P-59 (b)), belong to MetricResult (P1-11). Proposed name."""
    model = entity_class("MetricDefinition")
    loaded = _accept(languages="language_independent")
    assert loaded.languages == "language_independent"
    assert loaded.model_dump(mode="json")["languages"] == "language_independent"
    again = model.model_validate_json(loaded.model_dump_json())
    assert again.languages == "language_independent"
    assert again.content_digest == loaded.content_digest
    members = [m for m in property_enum(model, "languages") if isinstance(m, str)]
    assert members == ["language_independent"]
    assert schema_accepts(model, seal(model, _definition(languages="language_independent")))
    assert schema_accepts(model, seal(model, _definition(languages=["en", "hi"])))
    for bad in (
        "LANGUAGE_INDEPENDENT",
        "language-independent",
        "language_independent ",
        "all",
        "any",
        "*",
        "en",
        "",
        ["language_independent"],
        ["en", "language_independent"],
        None,
        5,
    ):
        _reject(field="languages", languages=bad)
    assert not schema_accepts(model, _definition(languages="all"))
    assert not schema_accepts(model, _definition(languages=["language_independent"]))
    # a language-independent metric is an ordinary definition otherwise
    assert _accept(languages="language_independent").kind == "latency"


# ------------------------------------------------- languages: list or sentinel (P-59, revision 9)

SENTINEL = "language_independent"
NINE_LANGUAGES = ["en-US", "hi-IN", "mr", "te", "ta-IN", "kn", "ml", "pa-Guru-IN", "gu"]


def _wer(**over: Any) -> dict[str, Any]:
    return latency_definition(
        key="wer",
        unit="wer",
        kind="deterministic",
        requires=["transcript", "reference_text"],
        implementation_ref="builtin:wer",
        pass_rule={"kind": "range", "max": 0.25, "inclusive": True},
        **over,
    )


def _language_branches(model: Any) -> list[dict[str, Any]]:
    node = schema_of(model)["properties"]["languages"]
    branches = node.get("anyOf")
    assert isinstance(branches, list), f"languages is not an anyOf: {node}"
    return branches


def test_metric_languages_list_or_language_independent() -> None:
    """P-59 (accepted by the owner, api-surface 20.2): ``MetricDefinition.languages`` is required
    (no default) and is either the string ``"language_independent"`` or a non-empty list of
    distinct primary ``SupportedLanguage`` values. The one-element list
    ``["language_independent"]`` and the empty list are rejected, as is the sentinel next to a
    language, in another case or spelling, or as any other type. The JSON Schema is an ``anyOf`` of
    ``{"type": "string", "enum": ["language_independent"]}`` and the array schema with
    ``minItems: 1``. The text is the same for every metric kind. Proposed name, binding since
    revision 9."""
    model = entity_class("MetricDefinition")
    # the sentinel: every kind, in python and in JSON, with a stable digest
    for doc in (latency_definition(), judge_definition(), _wer()):
        loaded = _accept(**{**doc, "languages": SENTINEL})
        assert loaded.languages == SENTINEL
        assert isinstance(loaded.languages, str)
        assert loaded.model_dump(mode="json")["languages"] == SENTINEL
        again = model.model_validate_json(loaded.model_dump_json())
        assert again.languages == SENTINEL
        assert again.content_digest == loaded.content_digest
    # the list: one entry, all nine languages, canonical case, order kept
    nine = NINE_LANGUAGES
    for good in (nine, ["hi-IN"], ["HI-in", "en-GB"], ["pa-Guru"], list(reversed(nine))):
        loaded = _accept(languages=good)
        assert isinstance(loaded.languages, list)
        assert len(loaded.languages) == len(good)
        again = model.model_validate_json(loaded.model_dump_json())
        assert again.languages == loaded.languages
    assert _accept(languages=["HI-in", "en-GB"]).languages == ["hi-IN", "en-GB"]
    assert _accept(languages=nine).languages == nine
    assert _accept(languages=SENTINEL).content_digest != _accept(languages=["en"]).content_digest
    # a list needs distinct primary languages; the sentinel is not a language
    for bad in (["hi", "hi-IN"], ["en-US", "en-GB"], ["hi", "hi"], ["en", "hi-IN", "en-GB"]):
        _reject(field="languages", languages=bad)
    # the sentinel is exactly one string, never a list entry
    bad_lists: list[Any] = [
        [SENTINEL],
        [SENTINEL, SENTINEL],
        ["en", SENTINEL],
        [SENTINEL, "en"],
        [[SENTINEL]],
        [],
        [[]],
        [None],
        [1],
        [True],
        [["en"]],
        [{"en": 1}],
    ]
    for bad_list in bad_lists:
        _reject(field="languages", languages=bad_list)
    for bad_other in (
        "LANGUAGE_INDEPENDENT",
        "Language_Independent",
        "language-independent",
        "language independent",
        "languageindependent",
        "language_independent ",
        " language_independent",
        "language_independent\n",
        "language_independent\x00",
        "language_independent,en",
        "language_independen",
        "language_independents",
        "all",
        "any",
        "none",
        "*",
        "en",
        "hi-IN",
        "",
        None,
        5,
        1.5,
        True,
        False,
        {},
        {"en": True},
        {SENTINEL: True},
    ):
        _reject(field="languages", languages=bad_other)
    # JSON entry point: the same reading, no coercion of a JSON array of one sentinel
    for bad_json in ('["language_independent"]', "[]", '"all"', "null", "5", "{}", '[["en"]]'):
        body = json.dumps({**seal(model, _definition()), "languages": json.loads(bad_json)})
        with digest_check_off(), pytest.raises(ValidationError) as via_json:
            model.model_validate_json(body)
        assert any("languages" in [str(p) for p in e["loc"]] for e in via_json.value.errors())
    # required, no default
    without = {k: v for k, v in _definition().items() if k != "languages"}
    with digest_check_off(), pytest.raises(ValidationError) as caught:
        model.model_validate(
            {**without, "content_digest": seal(model, _definition())["content_digest"]}
        )
    assert any("languages" in [str(p) for p in e["loc"]] for e in caught.value.errors())
    schema = schema_of(model)
    assert "languages" in schema["required"]
    assert "default" not in schema["properties"]["languages"]
    # the JSON Schema: anyOf of the string enum and the non-empty array, nothing else
    branches = _language_branches(model)
    assert len(branches) == 2, branches
    strings = [b for b in branches if b.get("type") == "string"]
    arrays = [b for b in branches if b.get("type") == "array"]
    assert len(strings) == 1
    assert len(arrays) == 1
    assert strings[0].get("enum") == [SENTINEL]
    assert arrays[0].get("minItems") == 1
    assert "items" in arrays[0]
    for good_schema in (SENTINEL, ["en"], NINE_LANGUAGES):
        assert schema_accepts(model, seal(model, _definition(languages=good_schema)))
    for bad_schema in ([], [SENTINEL], "all", "en", None, 5, [None], ["hindi"]):
        assert not schema_accepts(model, _definition(languages=bad_schema)), bad_schema


def test_language_independent_metric_not_script_sensitive() -> None:
    """P-59 (accepted by the owner, api-surface 20.2; ADR 0004 Decision 6): a metric whose
    ``languages`` is ``"language_independent"`` has no language to depend on, so
    ``script_sensitive=True`` with it is rejected, at ``languages`` or ``script_sensitive`` or at
    the model, with a message that names the rule. ``script_sensitive=False`` is accepted with it,
    and a list of languages is accepted with either value, so the rule binds the sentinel only.
    The rule is the same for every kind and in both entry points. It spans two fields, so
    the JSON Schema may or may not express it (api-surface 19.6 N12 keeps it code-only); that is
    not pinned. Proposed name, binding since revision 9."""
    model = entity_class("MetricDefinition")
    docs = {
        "latency": latency_definition(),
        "llm_judge": judge_definition(),
        "deterministic": _wer(),
        "per_turn_share": _turn_share(),
    }
    for kind, doc in docs.items():
        accepted = _accept(**{**doc, "languages": SENTINEL, "script_sensitive": False})
        assert accepted.languages == SENTINEL, kind
        assert accepted.script_sensitive is False, kind
        for sensitive in (True, False):
            listed = _accept(**{**doc, "languages": ["en", "hi"], "script_sensitive": sensitive})
            assert listed.script_sensitive is sensitive, (kind, sensitive)
            single = _accept(**{**doc, "languages": ["hi-IN"], "script_sensitive": sensitive})
            assert single.script_sensitive is sensitive, (kind, sensitive)
        error = _reject(**{**doc, "languages": SENTINEL, "script_sensitive": True})
        entries = error.errors(include_input=False)
        placed = [
            e
            for e in entries
            if not e["loc"] or {"languages", "script_sensitive"} & {str(p) for p in e["loc"]}
        ]
        assert placed, (kind, entries)
        messages = [str(e["msg"]).lower() for e in placed]
        assert any("script_sensitive" in m or SENTINEL in m for m in messages), (kind, messages)
        sealed = {
            **seal(model, _definition(**doc)),
            "languages": SENTINEL,
            "script_sensitive": True,
        }
        with digest_check_off(), pytest.raises(ValidationError) as via_json:
            model.model_validate_json(json.dumps(sealed))
        assert any(
            SENTINEL in str(e["msg"])
            or "script_sensitive" in str(e["msg"])
            or not e["loc"]
            or "languages" in e["loc"]
            or "script_sensitive" in e["loc"]
            for e in via_json.value.errors(include_input=False)
        )
    # the wording of the rule does not leak a value, and no other field is blamed
    error = _reject(**{**latency_definition(), "languages": SENTINEL, "script_sensitive": True})
    assert not any(
        {"key", "kind", "unit", "requires", "pass_rule"} & {str(p) for p in e["loc"]}
        for e in error.errors(include_input=False)
    )
    # script_sensitive stays a required boolean with the sentinel (no coercion into False)
    for bad in ("false", 0, None):
        _reject(
            field="script_sensitive",
            **{**latency_definition(), "languages": SENTINEL, "script_sensitive": bad},
        )
    # the rule reads the value, not the position: the order of keys in the document is irrelevant
    swapped = {"script_sensitive": True, "languages": SENTINEL}
    rest = {k: v for k, v in latency_definition().items() if k not in swapped}
    _reject(**{**swapped, **rest})


def test_definition_and_binding_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected metric
    key, closed-set member, unit, language tag, input kind, pass-rule and aggregation tag
    (pydantic's discriminator message), implementation reference and typed ID on
    ``MetricDefinition`` and ``MetricBinding`` does not appear in ``str``, ``repr``,
    ``errors(include_input=False)`` or ``json(include_input=False)``, through ``model_validate``
    and ``model_validate_json``, and the error is still located at the field. Proposed name
    (SCOPE section 5, P1-08)."""
    definition = entity_class("MetricDefinition")
    binding = entity_class("MetricBinding")
    bad_metric = {"id": marked_id("met"), "version": 1}
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("MetricKey", definition, _definition(key="Bad Key " + QUOTE_MARK), "key"),
        ("ClosedSet", definition, _definition(kind=marked_set()), "kind"),
        ("ClosedSet", definition, _definition(value_type=marked_set()), "value_type"),
        ("ClosedSet", definition, _definition(unit=marked_set()), "unit"),
        ("ClosedSet", definition, _definition(requires=[marked_set()]), "requires"),
        ("BCP-47", definition, _definition(languages=[marked_tag()]), "languages"),
        ("discriminator", definition, _definition(pass_rule={"kind": marked_set()}), "pass_rule"),
        (
            "discriminator",
            definition,
            _definition(aggregation={"kind": marked_set()}),
            "aggregation",
        ),
        ("pattern", definition, _definition(implementation_ref=marked_key()), "implementation_ref"),
        (
            "MetricKey",
            definition,
            _definition(**judge_definition(prompt_variables=["Bad Var " + QUOTE_MARK])),
            "prompt_variables",
        ),
        (
            "MachineKey",
            definition,
            _definition(**judge_definition(judge_model_ref=model_ref(model=marked_key()))),
            "model",
        ),
        ("typed ID", definition, _definition(id=marked_id("met")), "id"),
        ("typed ID", binding, raw_document("MetricBinding", id=marked_id("mbd")), "id"),
        (
            "typed ID",
            binding,
            raw_document("MetricBinding", evaluator_id=marked_id("evl")),
            "evaluator_id",
        ),
        ("typed ID", binding, raw_document("MetricBinding", metric=bad_metric), "metric"),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, QUOTE_MARK, field=field, label=label)
