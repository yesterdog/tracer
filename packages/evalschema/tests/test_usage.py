# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-23: UsageRecord and UsageQuantity (ADR 0014 Decisions 8 and 9, ADR 0003 Decision 1, P-39).

Assumed public API (docs/phase1/api-surface.md section 23), ``tracer_evalschema.usage``: the entity
``UsageRecord`` (``use``, a record of fact) with ``run_id``, ``domain``, ``stage``, ``provider``,
``model``, ``provider_call_ref?``, ``occurred_at?`` (revision 15, N43), ``quantities`` (a list of
``UsageQuantity``), ``source`` and ``raw?``; the value object ``UsageQuantity{unit, count}``.
``count`` is a strict integer >= 0 for the token, character and request units and a canonical
decimal string for the two audio-seconds units.

Names are SCOPE section 5 names unless the docstring says "Proposed name". Failures of these tests
are about the usage module only; none needs a batch 3 class.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from ._support import api
from ._support_b2 import assert_rejected, property_enum, registered_class, schema_accepts, schema_of
from ._support_b4 import (
    ALL_UNITS,
    DOMAINS,
    INTEGER_UNITS,
    SECONDS_UNITS,
    SOURCES,
    STAGES,
    make_usage,
    qty,
    usage_class,
    usage_doc,
)

NON_ASCII = "".join(chr(cp) for cp in (0x0928, 0x092E, 0x0938, 0x094D, 0x0924, 0x0947))


def _quantity() -> Any:
    return registered_class("UsageQuantity")


def _record() -> Any:
    return usage_class("UsageRecord")


# ------------------------------------------------------------------------------ quantities


@pytest.mark.parametrize("unit", INTEGER_UNITS)
def test_token_counts_int_nonneg(unit: str) -> None:
    """ADR 0014 Decision 9, I8: token, character and request counts are strict integers >= 0.
    Zero is a known count and is accepted; a negative number, a float (also an integral one), a
    numeric text, a boolean and ``None`` are rejected, in Python and in JSON, and the value is
    stored and dumped as the integer it was."""
    model = _quantity()
    for good in (0, 1, 1_234_567):
        loaded = model.model_validate(qty(unit, good))
        assert loaded.count == good
        assert type(loaded.count) is int
        assert loaded.unit == unit
        assert json.loads(loaded.model_dump_json()) == {"unit": unit, "count": good}
        assert model.model_validate_json(json.dumps(qty(unit, good))) == loaded
    for bad in (-1, 1.5, 2.0, "5", "0", True, False, None, [1], {"n": 1}):
        assert_rejected(model, qty(unit, bad), field="count")
    for bad_json in ('{"unit": "%s", "count": "7"}', '{"unit": "%s", "count": 7.0}'):
        with pytest.raises(Exception, match="count"):
            model.model_validate_json(bad_json % unit)


@pytest.mark.parametrize("unit", SECONDS_UNITS)
def test_audio_seconds_decimal(unit: str) -> None:
    """ADR 0014 Decision 9, I8: audio seconds are a ``DecimalString`` (canonical text, scale <= 18,
    never a number). Zero and fractions are accepted and stored as written; a JSON number, a float,
    a boolean, a non-canonical spelling ("3.50", "3."), an exponent, NaN, a plus sign and a
    negative amount (decided by the owner, N45 (a): no quantity is negative in any unit) are
    rejected."""
    model = _quantity()
    for good in ("0", "3", "3.5", "0.25", "123456789.123456789", "0.000000000000000001"):
        loaded = model.model_validate(qty(unit, good))
        assert loaded.count == good
        assert type(loaded.count) is str
        assert json.loads(loaded.model_dump_json()) == {"unit": unit, "count": good}
    for bad in (
        3,
        3.5,
        True,
        "3.50",
        "3.",
        "-1",
        "-0",
        "1e3",
        "NaN",
        "Infinity",
        "+1",
        " 3",
        "",
        None,
    ):
        assert_rejected(model, qty(unit, bad), field="count")
    assert_rejected(model, qty(unit, "0.0000000000000000001"), field="count")  # scale 19


@pytest.mark.parametrize("unit", INTEGER_UNITS)
def test_fractional_tokens_rejected(unit: str) -> None:
    """ADR 0014 Decision 9, A-28: a fractional count of tokens, characters or requests is never
    rounded or truncated; it is rejected, also inside a usage record (the error is located at the
    quantity's ``count``) and also as JSON text. The same fraction is a valid audio-seconds
    count, so the rule is about the unit, not about the digits."""
    model = _quantity()
    for bad in (1.5, "1.5", 0.5, "0.5", 0.1, "1e2", "1.0", 1e-3):
        assert_rejected(model, qty(unit, bad), field="count")
        error = assert_rejected(_record(), usage_doc(quantities=[qty(unit, bad)]), field="count")
        located = [tuple(str(p) for p in e["loc"]) for e in error.errors()]
        assert any("quantities" in loc and "count" in loc for loc in located), located
    with pytest.raises(Exception, match="count"):
        _record().model_validate_json(
            json.dumps({**usage_doc(), "quantities": [{"unit": unit, "count": 1.5}]})
        )
    for seconds_unit in SECONDS_UNITS:
        assert model.model_validate(qty(seconds_unit, "1.5")).count == "1.5"


def test_usage_quantity_shape_and_unit_set() -> None:
    """Proposed name (ADR 0014 Decision 9, ADR 0003 Decision 1): ``UsageQuantity`` is a value
    object with exactly ``unit`` and ``count``, both required, no entity fields, no unknown
    field; ``unit`` is the closed set of eight disjoint buckets, an ``enum`` in the JSON Schema;
    "tokens", "seconds", the singular and a different case are not units."""
    model = _quantity()
    assert set(model.model_fields) == {"unit", "count"}
    assert set(schema_of(model)["required"]) == {"unit", "count"}
    assert model.model_config.get("extra") == "forbid"
    assert sorted(property_enum(model, "unit")) == sorted(ALL_UNITS)
    assert len(property_enum(model, "unit")) == 8
    for unit in ALL_UNITS:
        count: Any = "1" if unit in SECONDS_UNITS else 1
        assert schema_accepts(model, qty(unit, count))
        assert model.model_validate(qty(unit, count)).unit == unit
    for bad_unit in ("tokens", "seconds", "input_token", "Input_Tokens", " requests", "", None, 3):
        assert_rejected(model, qty(bad_unit, 1), field="unit")
    assert_rejected(model, {"unit": "requests"}, field="count")
    assert_rejected(model, {"count": 1}, field="unit")
    assert_rejected(model, {**qty("requests", 1), "cost": "0"})
    for forbidden in ("id", "schema_version", "created_at", "metadata"):
        assert forbidden not in model.model_fields


# ---------------------------------------------------------------------------- the record


def test_usage_domain_stage_enum() -> None:
    """ADR 0003 Decision 1 and I5, ADR 0014 Decision 8: ``domain`` is exactly evaluation or
    agent_under_test; ``stage`` is exactly stt, llm, tts, judge, sim_caller_llm, sim_caller_tts,
    realtime, other; ``source`` is exactly provider_reported, telemetry, measured. All three are
    ``enum`` in the JSON Schema, each member is accepted, and a near miss, another case, an empty
    text and a non-string are rejected at the field."""
    model = _record()
    assert sorted(property_enum(model, "domain")) == sorted(DOMAINS)
    assert sorted(property_enum(model, "stage")) == sorted(STAGES)
    assert sorted(property_enum(model, "source")) == sorted(SOURCES)
    assert (len(DOMAINS), len(STAGES), len(SOURCES)) == (2, 8, 3)
    for stage in STAGES:
        assert make_usage(stage=stage).stage == stage
    for source in SOURCES:
        assert make_usage(source=source).source == source
    assert make_usage(domain="evaluation").domain == "evaluation"
    assert make_usage(domain="agent_under_test", source="telemetry").domain == "agent_under_test"
    bad_values = {
        "domain": ("agent", "Evaluation", " evaluation", "evaluation ", "", None, 1),
        "stage": ("STT", "stt ", "speech_to_text", "judge2", "", None, 1),
        "source": ("estimated", "measure", "Measured", "provider", "", None, 1),
    }
    for field, bads in bad_values.items():
        for bad in bads:
            assert_rejected(model, usage_doc(**{field: bad}), field=field)


def test_raw_preserved() -> None:
    """ADR 0014 Decision 8, I8: ``raw`` keeps the source payload verbatim: the same object content,
    the same key order at every depth, text with white space, non-ASCII characters and escapes
    unchanged, numbers as they were, and an independent copy (changing the input afterwards does
    not change the record). It is not interpreted: the quantities are exactly what was given,
    whatever the payload says. A record without ``raw`` holds None."""
    payload: dict[str, Any] = {
        "z_first": 1,
        "id": "chatcmpl-123",
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 5000},
        },
        "text": "  spaced \t out  ",
        "native": NON_ASCII,
        "nested": [1, 2.5, None, True, {"z": 1, "a": 2}],
        "a_second": 2,
    }
    pristine = copy.deepcopy(payload)
    record = make_usage(raw=payload)
    assert record.raw == pristine
    assert list(record.raw) == list(pristine)
    assert list(record.raw["usage"]) == list(pristine["usage"])
    assert list(record.raw["nested"][4]) == ["z", "a"]
    dumped = record.model_dump(mode="json")
    assert dumped["raw"] == pristine
    assert list(dumped["raw"]) == list(pristine)
    again = _record().model_validate_json(record.model_dump_json())
    assert again.raw == pristine
    assert list(again.raw) == list(pristine)
    payload["usage"]["prompt_tokens"] = 1
    payload["text"] = "changed"
    assert record.raw == pristine
    assert [(q.unit, q.count) for q in record.quantities] == [
        ("input_tokens", 1000),
        ("output_tokens", 200),
    ]
    assert make_usage().raw is None
    assert make_usage(raw=None).raw is None


def test_agent_domain_measured_usage_rejected() -> None:
    """ADR 0014 Decision 8, P-39: ``measured`` means directly observed by us in a call we made, and
    we never make the agent's billable calls, so ``agent_under_test`` with ``source=measured`` is
    rejected, at ``source``, for every stage and unit. The other five domain and source pairs are
    accepted (the evaluation domain may be measured)."""
    for stage in STAGES:
        assert_rejected(
            _record(),
            usage_doc(domain="agent_under_test", source="measured", stage=stage),
            field="source",
        )
    assert_rejected(
        _record(),
        usage_doc(
            domain="agent_under_test",
            source="measured",
            stage="stt",
            quantities=[qty("audio_input_seconds", "42.5")],
        ),
        field="source",
    )
    accepted = [
        ("evaluation", "provider_reported"),
        ("evaluation", "telemetry"),
        ("evaluation", "measured"),
        ("agent_under_test", "provider_reported"),
        ("agent_under_test", "telemetry"),
    ]
    for domain, source in accepted:
        loaded = make_usage(domain=domain, source=source)
        assert (loaded.domain, loaded.source) == (domain, source)
    with pytest.raises(Exception, match="source"):
        _record().model_validate_json(
            json.dumps({**usage_doc(), "domain": "agent_under_test", "source": "measured"})
        )


def test_usage_record_shape_and_required_fields() -> None:
    """Proposed name (ADR 0014 Decision 8, ADR 0001 table, ADR 0012 Decision 6): a usage record is
    an entity (``use``) with exactly the entity fields plus ``run_id``, ``domain``, ``stage``,
    ``provider``, ``model``, ``provider_call_ref``, ``occurred_at``, ``quantities``, ``source`` and
    ``raw``; the first five, ``quantities`` and ``source`` are required (``occurred_at`` is
    optional, N43). ``run_id`` is a ``run_`` ID (the only
    record of fact that names its run, N22), ``provider`` and ``model`` are machine keys (a model
    name such as ``saaras:v4`` is fine), ``provider_call_ref`` is an ``ExternalRef`` object, and
    ``quantities`` is a non-empty list with one quantity per unit (the units are disjoint
    buckets; decided by the owner, N44)."""
    model = _record()
    entity_fields = {"id", "schema_version", "created_at", "metadata", "metadata_flags"}
    assert set(model.model_fields) == entity_fields | {
        "run_id",
        "domain",
        "stage",
        "provider",
        "model",
        "provider_call_ref",
        "occurred_at",
        "quantities",
        "source",
        "raw",
    }
    required = {"run_id", "domain", "stage", "provider", "model", "quantities", "source"}
    assert required <= set(schema_of(model)["required"])
    assert not ({"provider_call_ref", "occurred_at", "raw"} & set(schema_of(model)["required"]))
    for name in sorted(required):
        doc = usage_doc()
        del doc[name]
        assert_rejected(model, doc, field=name)
    ids = api("ids")
    for bad_run in (str(ids.EvaluatorId.new()), str(ids.RunId.new().uuid), "", None):
        assert_rejected(model, usage_doc(run_id=bad_run), field="run_id")
    assert make_usage(model="saaras:v4").model == "saaras:v4"
    for field in ("provider", "model"):
        for bad in ("", "bad key", " openai", None, 3):
            assert_rejected(model, usage_doc(**{field: bad}), field=field)
    ref = {"system": "openai", "kind": "request_id", "value": "req_8f3a2c"}
    assert make_usage(provider_call_ref=ref).provider_call_ref is not None
    assert make_usage().provider_call_ref is None
    for bad_ref in ("req_8f3a2c", {"system": "openai"}, 7):
        assert_rejected(model, usage_doc(provider_call_ref=bad_ref), field="provider_call_ref")
    assert_rejected(model, usage_doc(quantities=[]), field="quantities")
    assert_rejected(
        model,
        usage_doc(quantities=[qty("input_tokens", 1), qty("input_tokens", 2)]),
        field="quantities",
    )
    loaded = make_usage(
        quantities=[qty(unit, "1" if "seconds" in unit else 1) for unit in ALL_UNITS]
    )
    assert [q.unit for q in loaded.quantities] == list(ALL_UNITS)
    assert_rejected(model, usage_doc(cost=usd_zero()))
    assert_rejected(model, usage_doc(api_key="x"))


def test_usage_record_occurred_at_optional_utc() -> None:
    """N43 (owner, option A; ADR 0014 Decision 8; api-surface 23.6 group A item 3): ``occurred_at``
    is an optional ``UtcDatetime``, the instant of the billable event as the source reports it. It
    is absent by default (never filled with the ingest time, run start or now); absent and ``None``
    are accepted; an aware instant in any offset is stored in UTC and dumped in the canonical form
    with six fractional digits and ``Z``; a naive time, a number, a date only and more than six
    fractional digits are rejected at ``occurred_at`` by pydantic and by the JSON Schema."""
    model = _record()
    assert make_usage().occurred_at is None
    assert make_usage(occurred_at=None).occurred_at is None
    assert "occurred_at" not in schema_of(model)["required"]
    plain = make_usage()
    assert json.loads(plain.model_dump_json())["occurred_at"] is None
    loaded = make_usage(occurred_at="2026-10-01T12:00:00Z")
    assert json.loads(loaded.model_dump_json())["occurred_at"] == "2026-10-01T12:00:00.000000Z"
    assert loaded.occurred_at is not None
    assert loaded.occurred_at.utcoffset() is not None
    shifted = make_usage(occurred_at="2026-10-01T17:30:00.5+05:30")
    assert json.loads(shifted.model_dump_json())["occurred_at"] == "2026-10-01T12:00:00.500000Z"
    again = model.model_validate_json(loaded.model_dump_json())
    assert again == loaded
    assert again.occurred_at == loaded.occurred_at
    assert schema_accepts(model, loaded.model_dump(mode="json"))
    assert schema_accepts(model, usage_doc())
    bad: Any
    for bad in (
        "2026-10-01T12:00:00",
        "2026-10-01",
        "2026-10-01T12:00:00.1234567Z",
        1790000000,
        1790000000.5,
        True,
        "",
        "yesterday",
    ):
        assert_rejected(model, usage_doc(occurred_at=bad), field="occurred_at")
        assert not schema_accepts(model, usage_doc(occurred_at=bad)), bad
        with pytest.raises(Exception, match="occurred_at"):
            model.model_validate_json(json.dumps(usage_doc(occurred_at=bad)))
    assert_rejected(model, {**usage_doc(), "occurred_at": _naive()}, field="occurred_at")


def _naive() -> Any:
    import datetime

    return datetime.datetime(2026, 10, 1, 12, 0, 0)


def usd_zero() -> dict[str, str]:
    return {"amount": "0", "currency": "USD"}
