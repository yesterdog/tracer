# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-24: PricingCatalog, PriceEntry, BillingRules, PriceProvenance, AppliesWhenCondition
(ADR 0014 Decisions 10 and 11, ADR 0009 Decision 5, ADR 0003 Decision 1, P-24, P-37, P-38).

Assumed public API (docs/phase1/api-surface.md section 23), ``tracer_evalschema.pricing``:
``PricingCatalog`` (``pcat``, versioned: ``entries``, ``published_at``), ``PriceEntry`` (a value
object since revision 15, N47, with an author-assigned ``entry_key``, N61; the prefix ``pce`` is
withdrawn) and the value objects
``BillingRules{increment, minimum}``, ``PriceProvenance{kind, url?, retrieved_at, note?}`` and
``AppliesWhenCondition{kind, value}``. ``PriceEntry.billing`` is the literal ``"unknown"`` or a
``BillingRules``; ``price`` and ``minimum`` are USD ``Money``.

The SCOPE test ``test_unevaluable_applies_when_ambiguous`` needs ``cost.resolve_price`` (P1-25); it
is in ``test_cost_compute.py`` (api-surface 23.3 N46). Revision 16 and 17 (api-surface 23.6 groups
B, D, I, O, P, Q, W, X): ``PriceProvenance``, ``PriceEntry`` and ``PricingCatalog`` hide their
input (N62), ``provenance.url`` runs the URL rules, ``check_catalog_succession`` states P-24.

Names are SCOPE section 5 names unless the docstring says "Proposed name". Failures of these tests
are about the pricing module only.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api, data_json
from ._support_b2 import (
    assert_rejected,
    contains_key,
    error_texts,
    property_enum,
    registered_class,
    schema_accepts,
    schema_of,
    seal,
)
from ._support_b2 import (
    new_id as new_id_text,
)
from ._support_b3 import (
    AWS_KEY,
    PASTED_KEY,
    QUOTE_MARK,
    assert_input_hidden,
    marked_key,
    marked_set,
)
from ._support_b4 import (
    ALL_UNITS,
    billing,
    catalog_doc,
    entry_doc,
    make_catalog,
    make_entry,
    pricing_class,
    provenance_doc,
    schema_core,
    usd,
)

# the fixed words of the non-USD rule, which a tested code must not be a substring of (N62)
RULE_TEXT = "not supported only USD"
JAN = "2026-01-01T00:00:00.000000Z"
MAR = "2026-03-01T00:00:00.000000Z"
JUN = "2026-06-01T00:00:00.000000Z"
JUL = "2026-07-01T00:00:00.000000Z"
SEP = "2026-09-01T00:00:00.000000Z"


def _entry() -> Any:
    return pricing_class("PriceEntry")


def _catalog() -> Any:
    return pricing_class("PricingCatalog")


def _rules() -> Any:
    return registered_class("BillingRules")


def _iso_rows() -> dict[str, dict[str, Any]]:
    return {row["code"]: row for row in data_json("iso4217_active.json")["currencies"]}


# ------------------------------------------------------------------------------ catalog


def test_catalog_digest_immutable() -> None:
    """ADR 0009 I3: the digest of a catalog is fixed by its ``(id, version)``. A sealed catalog
    loads; changing any entry's price, its provenance (``retrieved_at``) or its ``entry_key`` (D4:
    the digest covers every entry field), the publication time or the version without a new
    digest is
    a ``DigestMismatch`` (typed, not wrapped); a new version of the same catalog has the same ID
    and another digest; and a bundle that holds one ``(id, version)`` with two content digests is a
    ``ConflictingVersionError`` while an identical duplicate is fine."""
    errors = api("errors")
    model = _catalog()
    catalog = make_catalog()
    doc = catalog.model_dump(mode="json")
    assert model.model_validate(doc).content_digest == catalog.content_digest
    price_changed = copy.deepcopy(doc)
    price_changed["entries"][0]["price"] = usd("0.16")
    # revision 15, N47 and D4: the digest covers every entry field, provenance and key included
    provenance_changed = copy.deepcopy(doc)
    provenance_changed["entries"][0]["provenance"]["retrieved_at"] = "2026-10-02T00:00:00.000000Z"
    key_changed = copy.deepcopy(doc)
    key_changed["entries"][0]["entry_key"] = "renamed-entry-key"
    for tampered in (
        price_changed,
        provenance_changed,
        key_changed,
        {**doc, "published_at": "2026-10-01T00:00:01.000000Z"},
        {**doc, "version": 2},
        {**doc, "metadata": {"added": 1}, "metadata_flags": []},
    ):
        with pytest.raises(errors.DigestMismatch):
            model.model_validate(tampered)
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    second = seal(model, {**body, "version": 2})
    assert second["id"] == doc["id"]
    assert second["content_digest"] != doc["content_digest"]
    assert model.model_validate(second).version == 2
    other_content = seal(model, {**body, "entries": [price_changed["entries"][0]]})
    assert other_content["version"] == doc["version"]
    assert other_content["id"] == doc["id"]
    assert other_content["content_digest"] != doc["content_digest"]
    api("bundle").check_bundle_consistency([doc, copy.deepcopy(doc)])
    api("bundle").check_bundle_consistency([doc, second])
    with pytest.raises(errors.ConflictingVersionError):
        api("bundle").check_bundle_consistency([doc, other_content])


def test_pricing_catalog_shape() -> None:
    """Proposed name (ADR 0014 Decision 10, ADR 0009 Decision 5): ``PricingCatalog`` is a versioned
    entity (``pcat``) with the entity and version fields plus ``entries`` and ``published_at``,
    both required (``published_at`` is a UTC instant). ``entries`` are ``PriceEntry`` value-object
    documents and non-empty (N47: ``minItems: 1`` in the JSON Schema); an invalid entry makes the
    catalog invalid at ``entries``. Two entries with one ``entry_key`` are rejected (N61). An
    entry that still carries entity fields (``id``) is an unknown field. An unknown field is an
    error."""
    model = _catalog()
    fields = set(model.model_fields)
    assert {"entries", "published_at", "version", "content_digest"} <= fields
    assert fields - {"id", "schema_version", "created_at", "metadata", "metadata_flags"} == {
        "version",
        "content_digest",
        "entries",
        "published_at",
    }
    assert {"entries", "published_at"} <= set(schema_of(model)["required"])
    loaded = make_catalog([entry_doc(), entry_doc(unit="output_tokens", price=usd("0.6"))])
    assert [e.unit for e in loaded.entries] == ["input_tokens", "output_tokens"]
    assert all(type(e) is _entry() for e in loaded.entries)
    for missing in ("entries", "published_at"):
        doc = catalog_doc()
        del doc[missing]
        assert_rejected(model, doc, field=missing)
    assert_rejected(model, catalog_doc(published_at="2026-10-01T00:00:00"), field="published_at")
    assert_rejected(model, catalog_doc(published_at=None), field="published_at")
    bad_entry = entry_doc(price=usd("-1"))
    caught = assert_rejected(model, catalog_doc([bad_entry]), field="entries")
    located = [tuple(str(p) for p in e["loc"]) for e in caught.errors()]
    assert any("entries" in loc and "price" in loc for loc in located), located
    shared = entry_doc()
    clash = entry_doc(entry_key=shared["entry_key"], unit="output_tokens", price=usd("0.6"))
    assert_rejected(model, catalog_doc([shared, clash]), field="entries")
    assert_rejected(model, catalog_doc([]), field="entries")
    assert schema_of(model)["properties"]["entries"]["minItems"] == 1
    assert not schema_accepts(model, catalog_doc([]))
    assert schema_accepts(model, make_catalog().model_dump(mode="json"))
    old_shape = entry_doc(id="pce_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b")
    assert_rejected(_entry(), old_shape)
    assert_rejected(model, catalog_doc([old_shape]), field="entries")
    assert_rejected(model, catalog_doc(unknown_field=1))


# ------------------------------------------------------------------------------ entries


def test_price_entry_shape_and_enums() -> None:
    """Proposed name (ADR 0014 Decision 11, ADR 0003 Decision 1): ``PriceEntry`` is a value object
    (N47: no entity fields, not an ``EntityBase``) with exactly ``entry_key`` (N61, a
    ``MachineKey`` assigned by the catalog author) plus ``provider``, ``model``, ``aliases``,
    ``unit``, ``unit_quantity``, ``price``, ``billing``, ``effective_from``, ``effective_to``,
    ``applies_when``, ``price_source`` and ``provenance``. Required: entry_key, provider, model,
    unit, unit_quantity, price, billing, effective_from, price_source, provenance. ``unit`` is the
    usage unit set (eight members, an ``enum``). ``unit_quantity`` is a positive canonical
    decimal. ``aliases`` defaults to no alias and holds distinct machine keys that are not the
    entry's own model (N72). ``effective_to`` is optional and, when present, later than
    ``effective_from`` (N48). ``provenance.kind`` is an open machine key (N50 remainder): a kind
    the test has not seen is accepted, a non-key is rejected, and its schema has no ``enum``."""
    model = _entry()
    assert not issubclass(model, api("base").EntityBase)
    assert set(model.model_fields) == {
        "entry_key",
        "provider",
        "model",
        "aliases",
        "unit",
        "unit_quantity",
        "price",
        "billing",
        "effective_from",
        "effective_to",
        "applies_when",
        "price_source",
        "provenance",
    }
    required = {
        "entry_key",
        "provider",
        "model",
        "unit",
        "unit_quantity",
        "price",
        "billing",
        "effective_from",
        "price_source",
        "provenance",
    }
    assert required <= set(schema_of(model)["required"])
    assert not ({"aliases", "effective_to", "applies_when"} & set(schema_of(model)["required"]))
    for entity_field in ("id", "schema_version", "created_at", "metadata", "metadata_flags"):
        assert entity_field not in model.model_fields
        assert_rejected(model, entry_doc(**{entity_field: "x"}))
    assert make_entry(entry_key="acme-stt-audio-2026-10").entry_key == "acme-stt-audio-2026-10"
    for bad_key in ("", "bad key", " k", "k ", None, 3, "k" * 257):
        assert_rejected(model, entry_doc(entry_key=bad_key), field="entry_key")
    assert sorted(property_enum(model, "unit")) == sorted(ALL_UNITS)
    assert sorted(property_enum(model, "price_source")) == ["catalog", "user_supplied"]
    for unit in ALL_UNITS:
        assert make_entry(unit=unit).unit == unit
    for bad_unit in ("tokens", "Input_Tokens", "", None):
        assert_rejected(model, entry_doc(unit=bad_unit), field="unit")
    doc = entry_doc()
    del doc["aliases"]
    assert model.model_validate(doc).aliases == []
    assert make_entry(aliases=["gpt-4.1-2025-04-14", "gpt-4.1-latest"]).aliases == [
        "gpt-4.1-2025-04-14",
        "gpt-4.1-latest",
    ]
    for bad_alias in ("", "bad key", 3):
        assert_rejected(model, entry_doc(aliases=[bad_alias]), field="aliases")
    assert_rejected(model, entry_doc(aliases=["a-1", "a-1"]), field="aliases")
    assert_rejected(model, entry_doc(aliases=["gpt-4.1"]), field="aliases")
    assert_rejected(model, entry_doc(model="m-1", aliases=["x-1", "m-1"]), field="aliases")
    for good in ("1", "60", "1000000", "0.5", "1000"):
        assert make_entry(unit_quantity=good).unit_quantity == good
    for bad in ("0", "-1", "1.50", "abc", "1e6", 1000000, 1.5, None, ""):
        assert_rejected(model, entry_doc(unit_quantity=bad), field="unit_quantity")
    for name in sorted(required):
        missing = entry_doc()
        del missing[name]
        assert_rejected(model, missing, field=name)
    for field in ("provider", "model"):
        for bad in ("", "bad key", None):
            assert_rejected(model, entry_doc(**{field: bad}), field=field)
    assert make_entry(effective_to=JUN).effective_to is not None
    assert make_entry().effective_to is None
    assert_rejected(model, entry_doc(effective_to=JAN), field="effective_to")
    assert_rejected(
        model, entry_doc(effective_to="2025-12-31T00:00:00.000000Z"), field="effective_to"
    )
    assert_rejected(model, entry_doc(effective_from="2026-01-01T00:00:00"), field="effective_from")
    assert_rejected(model, entry_doc(api_key="x"))
    provenance = registered_class("PriceProvenance")
    assert "enum" not in schema_of(provenance)["properties"]["kind"]
    for kind in ("provider_price_page", "never-seen-before:kind/v2", "user_supplied", "a"):
        assert make_entry(provenance=provenance_doc(kind=kind)).provenance.kind == kind
    for bad_kind in ("bad kind", "", None, 3):
        assert_rejected(model, entry_doc(provenance=provenance_doc(kind=bad_kind)), field="kind")


def test_applies_when_conditions_closed_kinds_and_value_types() -> None:
    """Proposed name (ADR 0014 Decision 11, P-37): ``applies_when`` is a list of typed conditions
    with a closed kind: context_tokens_gt, context_tokens_le, batch, region. The two token kinds
    take a strict integer >= 0, ``batch`` a strict boolean and ``region`` a machine key (P reading,
    section 23.3 N49). A free-text condition or an unknown kind is rejected, so a condition is
    always something the resolver can evaluate or knows it cannot. Revision 16 (N49 remainder):
    an empty ``applies_when`` list is rejected at ``applies_when`` (pydantic and the JSON Schema,
    ``minItems: 1``), while an omitted field is accepted (one spelling, one digest); a repeated
    kind (two ``region``, two ``context_tokens_gt``, two ``batch``) is rejected at
    ``applies_when`` by pydantic. "The kinds are distinct" cannot be written in JSON Schema
    2020-12, so it is a code-only rule (handling per staff-reviewer's ruling on N12/N38); a band
    is ``context_tokens_gt`` plus ``context_tokens_le``, which are two kinds."""
    model = registered_class("AppliesWhenCondition")
    assert sorted(property_enum(model, "kind")) == [
        "batch",
        "context_tokens_gt",
        "context_tokens_le",
        "region",
    ]
    cases: dict[str, tuple[tuple[Any, ...], tuple[Any, ...]]] = {
        "context_tokens_gt": ((0, 200000), (-1, "200000", 1.5, True, None)),
        "context_tokens_le": ((0, 200000), (-1, "200000", 1.5, False, None)),
        "batch": ((True, False), ("true", 1, 0, None, "")),
        "region": (("us-east-1", "eu"), ("", " eu", 5, True, None)),
    }
    for kind, (good, bad) in cases.items():
        for value in good:
            loaded = model.model_validate({"kind": kind, "value": value})
            assert loaded.kind == kind
            assert loaded.value == value
            entry = make_entry(applies_when=[{"kind": kind, "value": value}])
            assert entry.applies_when is not None
            assert entry.applies_when[0].kind == kind
            assert schema_accepts(_entry(), entry.model_dump(mode="json"))
        for value in bad:
            assert_rejected(model, {"kind": kind, "value": value}, field="value")
    for bad_kind in ("context_tokens_eq", "when", "Batch", "", None, "model == gpt-4.1"):
        assert_rejected(model, {"kind": bad_kind, "value": 1}, field="kind")
    assert_rejected(model, {"kind": "batch"}, field="value")
    assert_rejected(model, {"value": True}, field="kind")
    assert_rejected(model, {"kind": "batch", "value": True, "note": "x"})
    doc = entry_doc()
    assert make_entry().applies_when is None
    assert "applies_when" not in doc
    assert_rejected(_entry(), entry_doc(applies_when=[]), field="applies_when")
    assert not schema_accepts(_entry(), entry_doc(applies_when=[]))
    assert schema_accepts(_entry(), doc)
    for repeated in (
        [{"kind": "region", "value": "us"}, {"kind": "region", "value": "eu"}],
        [{"kind": "region", "value": "us"}, {"kind": "region", "value": "us"}],
        [
            {"kind": "context_tokens_gt", "value": 100},
            {"kind": "context_tokens_gt", "value": 200},
        ],
        [{"kind": "batch", "value": True}, {"kind": "batch", "value": False}],
        [
            {"kind": "batch", "value": True},
            {"kind": "region", "value": "us"},
            {"kind": "batch", "value": True},
        ],
    ):
        assert_rejected(_entry(), entry_doc(applies_when=repeated), field="applies_when")
    band = [
        {"kind": "context_tokens_gt", "value": 128000},
        {"kind": "context_tokens_le", "value": 1000000},
    ]
    assert len(make_entry(applies_when=band).applies_when or []) == 2


# ------------------------------------------------------------------------------- ranges


def _range_case(first: dict[str, Any], second: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry_doc(**first), entry_doc(**second)]


@pytest.mark.parametrize(
    ("first", "second", "overlaps"),
    [
        (
            {"effective_from": JAN, "effective_to": JUN},
            {"effective_from": MAR, "effective_to": SEP},
            True,
        ),
        ({"effective_from": JAN, "effective_to": JUN}, {"effective_from": MAR}, True),
        ({"effective_from": JAN}, {"effective_from": MAR}, True),
        ({"effective_from": JAN}, {"effective_from": JAN}, True),
        (
            {"effective_from": JAN, "effective_to": JUN},
            {"effective_from": MAR, "effective_to": "2026-04-01T00:00:00.000000Z"},
            True,
        ),
        ({"effective_from": JAN, "effective_to": JUN}, {"effective_from": JUN}, False),
        ({"effective_from": JAN, "effective_to": JUN}, {"effective_from": JUL}, False),
        (
            {"effective_from": JUN, "effective_to": JUL},
            {"effective_from": JAN, "effective_to": JUN},
            False,
        ),
    ],
    ids=[
        "partial",
        "open-later",
        "both-open",
        "same-start",
        "contained",
        "adjacent-half-open",
        "gap",
        "adjacent-reversed",
    ],
)
def test_overlapping_ranges_rejected(
    first: dict[str, Any], second: dict[str, Any], overlaps: bool
) -> None:
    """ADR 0014 Decision 11, A-25: effective ranges under one ``(provider, model, unit,
    applies_when)`` key must not overlap. Ranges are half-open, ``[effective_from,
    effective_to)``, so a price that ends when the next one starts is not an overlap (P reading,
    section 23.3 N48). An overlap makes the catalog invalid at ``entries`` and the message says
    "overlap"; a gap and adjacent ranges are accepted."""
    model = _catalog()
    docs = _range_case(first, second)
    if overlaps:
        caught = assert_rejected(model, catalog_doc(docs), field="entries")
        assert any("overlap" in str(e["msg"]).lower() for e in caught.errors()), caught.errors()
    else:
        assert len(make_catalog(docs).entries) == 2


def test_overlapping_ranges_only_within_one_key() -> None:
    """ADR 0014 Decision 11: the key is ``(provider, model, unit, applies_when)``. Overlapping
    ranges under another provider, another model, another unit or another ``applies_when`` are
    different prices, not a conflict; the same ``applies_when`` is the same key, also when the
    conditions are listed in another order (all conditions must hold, so the order carries no
    meaning; P reading, section 23.3 N49)."""
    model = _catalog()
    batch_on = [{"kind": "batch", "value": True}]
    batch_off = [{"kind": "batch", "value": False}]
    accepted: list[dict[str, Any]] = [
        {"provider": "anthropic"},
        {"model": "gpt-4.1-mini"},
        {"unit": "output_tokens"},
        {"applies_when": batch_on},
    ]
    for change in accepted:
        assert len(make_catalog([entry_doc(), entry_doc(**change)]).entries) == 2, change
    assert (
        len(
            make_catalog(
                [entry_doc(applies_when=batch_on), entry_doc(applies_when=batch_off)]
            ).entries
        )
        == 2
    )
    both = [{"kind": "context_tokens_gt", "value": 100000}, {"kind": "batch", "value": True}]
    flipped = list(reversed(both))
    caught = assert_rejected(
        model,
        catalog_doc([entry_doc(applies_when=both), entry_doc(applies_when=flipped)]),
        field="entries",
    )
    assert any("overlap" in str(e["msg"]).lower() for e in caught.errors())
    for same in ({"applies_when": batch_on}, {}):
        first = entry_doc(**same)
        caught = assert_rejected(model, catalog_doc([first, entry_doc(**same)]), field="entries")
        assert any("overlap" in str(e["msg"]).lower() for e in caught.errors())


def test_provenance_required() -> None:
    """ADR 0014 Decision 11, I9: provenance is required on every entry: ``kind`` and
    ``retrieved_at`` are required, ``url`` and ``note`` optional. Absent, ``None`` and ``{}`` are
    rejected at ``provenance``; a naive ``retrieved_at`` is rejected (UTC is never assumed); an
    unknown field is rejected. The kind is an open machine key in 0.0.1 (P reading, section 23.3
    N50)."""
    model = _entry()
    provenance = registered_class("PriceProvenance")
    doc = entry_doc()
    del doc["provenance"]
    assert_rejected(model, doc, field="provenance")
    assert_rejected(model, entry_doc(provenance=None), field="provenance")
    assert_rejected(model, entry_doc(provenance={}), field="provenance")
    assert set(provenance.model_fields) == {"kind", "url", "retrieved_at", "note"}
    assert {"kind", "retrieved_at"} <= set(schema_of(provenance)["required"])
    assert not ({"url", "note"} & set(schema_of(provenance)["required"]))
    for field in ("kind", "retrieved_at"):
        missing = provenance_doc()
        del missing[field]
        assert_rejected(provenance, missing, field=field)
    bad: Any
    for bad in ("", "bad kind", None):
        assert_rejected(provenance, provenance_doc(kind=bad), field="kind")
    for bad in ("2026-10-01T00:00:00", "yesterday", None, 1790000000):
        assert_rejected(provenance, provenance_doc(retrieved_at=bad), field="retrieved_at")
    minimal = {"kind": "provider_price_page", "retrieved_at": "2026-10-01T00:00:00.000000Z"}
    loaded = make_entry(provenance=minimal)
    assert loaded.provenance.url is None
    assert loaded.provenance.note is None
    full = provenance_doc(note="converted at 83.10 INR per USD on 2026-10-01")
    assert make_entry(provenance=full).provenance.note == full["note"]
    assert_rejected(provenance, provenance_doc(source="x"))
    again = _entry().model_validate_json(make_entry(provenance=full).model_dump_json())
    assert again.provenance.retrieved_at.year == 2026


def test_user_supplied_marked() -> None:
    """ADR 0014 Decision 11, I9: every entry says where its price came from, ``price_source``
    catalog or user_supplied, required and never defaulted; the mark survives a round trip and
    does not relax any other rule (a user-supplied price is still USD and still needs billing
    rules and provenance)."""
    model = _entry()
    doc = entry_doc()
    del doc["price_source"]
    assert_rejected(model, doc, field="price_source")
    for bad in ("user", "User_Supplied", "supplied", "", None, True):
        assert_rejected(model, entry_doc(price_source=bad), field="price_source")
    user = make_entry(price_source="user_supplied")
    assert user.price_source == "user_supplied"
    assert json.loads(user.model_dump_json())["price_source"] == "user_supplied"
    assert model.model_validate_json(user.model_dump_json()).price_source == "user_supplied"
    assert make_entry(price_source="catalog").price_source == "catalog"
    assert_rejected(
        model, entry_doc(price_source="user_supplied", price=usd("0.1") | {"currency": "EUR"})
    )
    broken = entry_doc(price_source="user_supplied")
    del broken["billing"]
    assert_rejected(model, broken, field="billing")
    broken = entry_doc(price_source="user_supplied")
    del broken["provenance"]
    assert_rejected(model, broken, field="provenance")


def test_explicit_zero_price_allowed() -> None:
    """ADR 0014 Decision 11, I9: an explicit price of 0 is a known price (a free tier) and is
    accepted for ``price``, and a minimum of 0 is accepted too; it is not the same as a missing
    price. The canonical spelling is "0"; "0.0" is rejected as non-canonical."""
    entry = make_entry(price=usd("0"))
    assert entry.price.amount == "0"
    assert entry.price.currency == "USD"
    assert json.loads(entry.model_dump_json())["price"] == usd("0")
    assert make_entry(billing=billing("none", usd("0"))).billing.minimum.amount == "0"
    assert_rejected(_entry(), entry_doc(price=usd("0.0")), field="price")
    doc = entry_doc()
    del doc["price"]
    assert_rejected(_entry(), doc, field="price")
    assert_rejected(_entry(), entry_doc(price=None), field="price")


def test_price_and_minimum_nonnegative() -> None:
    """Proposed name (ADR 0014 Decision 7, P-38): ``Money`` may be negative in general, but
    ``PriceEntry.price`` and ``BillingRules.minimum`` must be >= 0, because a negative computed
    cost is always a bug. The rejection is located at ``price`` and at ``minimum``."""
    money = api("primitives").Money
    assert money(amount="-1", currency="USD").amount == "-1"
    for bad in ("-1", "-0.000000000000000001", "-100"):
        assert_rejected(_entry(), entry_doc(price=usd(bad)), field="price")
        assert_rejected(_entry(), entry_doc(billing=billing("none", usd(bad))), field="minimum")
        assert_rejected(_rules(), billing("none", usd(bad)), field="minimum")
    assert make_entry(price=usd("0.000000000000000001")).price.amount == "0.000000000000000001"


# ------------------------------------------------------------------------------ billing


def test_price_entry_requires_billing_rules() -> None:
    """ADR 0014 Decision 11, I9: ``billing`` is required and is either the literal "unknown" or a
    ``BillingRules``. An absent ``billing``, ``None``, an empty object, a bare "none", another case
    of "unknown" and any other text are rejected at ``billing``. "unknown" is accepted and stays
    the literal after a round trip."""
    model = _entry()
    doc = entry_doc()
    del doc["billing"]
    assert_rejected(model, doc, field="billing")
    bad: Any
    for bad in (None, {}, "none", "UNKNOWN", "Unknown", "", "free", 0, True, []):
        assert_rejected(model, entry_doc(billing=bad), field="billing")
    unknown = make_entry(billing="unknown")
    assert unknown.billing == "unknown"
    assert json.loads(unknown.model_dump_json())["billing"] == "unknown"
    assert model.model_validate_json(unknown.model_dump_json()).billing == "unknown"
    assert schema_accepts(model, unknown.model_dump(mode="json"))
    ruled = make_entry(billing=billing("15", usd("0.005")))
    assert ruled.billing != "unknown"
    assert schema_accepts(model, ruled.model_dump(mode="json"))
    assert model.model_validate_json(ruled.model_dump_json()).billing == ruled.billing


def test_billing_parts_required_value_or_none() -> None:
    """ADR 0014 Decision 11, I10: in a ``BillingRules`` both ``increment`` and ``minimum`` are
    required, and each is a value or the literal "none"; "none" (no increment, no minimum) is a
    different statement from an absent part, and "unknown" is not a part value. ``increment`` is a
    positive canonical decimal; ``minimum`` is a USD ``Money``."""
    rules = _rules()
    assert set(rules.model_fields) == {"increment", "minimum"}
    assert set(schema_of(rules)["required"]) == {"increment", "minimum"}
    for part in ("increment", "minimum"):
        missing = billing()
        del missing[part]
        assert_rejected(rules, missing, field=part)
        assert_rejected(rules, billing(**{part: None}), field=part)
        assert_rejected(rules, billing(**{part: "unknown"}), field=part)
        assert_rejected(rules, billing(**{part: ""}), field=part)
    assert_rejected(rules, {})
    none_none = rules.model_validate(billing())
    assert none_none.increment == "none"
    assert none_none.minimum == "none"
    dumped = json.loads(none_none.model_dump_json())
    assert dumped == {"increment": "none", "minimum": "none"}
    both = rules.model_validate(billing("15", usd("0.005")))
    assert both.increment == "15"
    assert both.minimum.amount == "0.005"
    assert json.loads(both.model_dump_json()) == {"increment": "15", "minimum": usd("0.005")}
    assert rules.model_validate(billing("0.5", "none")).increment == "0.5"
    assert rules.model_validate(billing("none", usd("1"))).minimum.amount == "1"
    bad: Any
    for bad in ("0", "-1", "-15", "15.0", "1e1", "abc", 15, 0.5, True):
        assert_rejected(rules, billing(increment=bad), field="increment")
    for bad in ("0.005", 0.005, 5, {"amount": "1"}, {"currency": "USD"}):
        assert_rejected(rules, billing(minimum=bad), field="minimum")
    assert_rejected(rules, {**billing(), "rounding": "up"})


@pytest.mark.parametrize("price_source", ["catalog", "user_supplied"])
@pytest.mark.parametrize("part", ["price", "minimum"])
@pytest.mark.parametrize(
    "code", ["usd", "EUR", "INR", "XAF", "XXX", "XAU", "BOV", "CLF", "ABC", ""]
)
def test_price_entry_rejects_non_usd(price_source: str, part: str, code: str) -> None:
    """ADR 0014 Decision 11, I7a, A-12: a configured price is an input we control, so a non-USD
    ``price`` or ``billing.minimum`` is a validation error, for catalog and for user-supplied
    entries alike, located at the part. Revision 16 (N62, a specification change and not a
    weakening): ``PriceEntry`` hides its input, so the message states the rule ("not supported"
    and "only USD") and names neither the rejected code nor its ISO 4217 name, and does not say
    "not an ISO 4217 code" either (ADR 0014 Decision 3; ADR 0007 Decision 8 (c)). The location
    assertion is unchanged. The ISO naming stays covered by
    ``test_non_usd_error_names_iso_currency`` on a bare ``Money``."""
    money = usd("0.15") | {"currency": code}
    if part == "price":
        doc = entry_doc(price_source=price_source, price=money)
    else:
        doc = entry_doc(price_source=price_source, billing=billing("none", money))
    caught = assert_rejected(_entry(), doc, field=part)
    text = " ".join(error_texts(caught))
    rows = _iso_rows()
    assert "not supported" in text
    assert "only USD" in text
    assert "not an ISO 4217 code" not in text
    if code:  # the empty text is a substring of everything
        assert code not in RULE_TEXT, "a tested code must not be part of the fixed rule text"
        assert code not in text
    if code in rows:
        assert rows[code]["name"] not in text


def test_every_non_usd_iso_code_rejected_in_prices() -> None:
    """ADR 0014 I7a, A-12: every code of the vendored ISO 4217 file except USD (fund codes and
    X-codes included) is rejected as a ``price`` and as a ``billing.minimum``; USD is accepted."""
    rows = _iso_rows()
    assert "USD" in rows
    assert len(rows) > 150
    for code in sorted(rows):
        money = usd("1") | {"currency": code}
        if code == "USD":
            assert make_entry(price=money).price.currency == "USD"
            continue
        assert_rejected(_entry(), entry_doc(price=money), field="price")
        assert_rejected(_entry(), entry_doc(billing=billing("none", money)), field="minimum")


# --------------------------------------------------------------------------------- schema


def test_pricing_models_emit_enum_not_const_and_string_amounts() -> None:
    """Proposed name (ADR 0003 Decision 6, ADR 0014 I21): the catalog, entry, billing, provenance
    and condition schemas never contain ``const`` (the literals "unknown" and "none" and every
    closed set are ``enum``), contain no number type for an amount, and the entry's ``price`` and
    ``minimum`` are the ``Money`` definition whose ``amount`` is a string with the decimal
    pattern. Both validation and serialization mode."""
    models = [
        _catalog(),
        _entry(),
        _rules(),
        registered_class("PriceProvenance"),
        registered_class("AppliesWhenCondition"),
    ]
    for model in models:
        for mode in ("validation", "serialization"):
            schema = schema_of(model, mode)
            assert not contains_key(schema, "const"), f"{model.__name__} ({mode})"
    schema = schema_of(_entry())
    money = api("primitives").Money
    pattern = schema_of(money)["properties"]["amount"]["pattern"]
    assert pattern == api("primitives").DECIMAL_PATTERN
    defs = schema.get("$defs", {})
    assert "Money" in defs
    assert schema_core(defs["Money"]["properties"]["amount"]) == {
        "type": "string",
        "pattern": pattern,
    }
    assert defs["Money"]["properties"]["currency"]["enum"] == ["USD"]
    with pytest.raises(ValidationError):
        _entry().model_validate(entry_doc(price={"amount": 0.15, "currency": "USD"}))


# ------------------------------------------------------------------ provenance (N50, N62, N66)


def _provenance() -> Any:
    return registered_class("PriceProvenance")


def _carriers(provenance: dict[str, Any]) -> list[tuple[str, Any, dict[str, Any], tuple[str, ...]]]:
    """The three models that validate a provenance document: alone, inside a ``PriceEntry`` and
    inside a ``PricingCatalog``; with the location prefix of the provenance in each."""
    return [
        ("PriceProvenance", _provenance(), provenance, ()),
        ("PriceEntry", _entry(), entry_doc(provenance=provenance), ("provenance",)),
        (
            "PricingCatalog",
            _catalog(),
            catalog_doc([entry_doc(provenance=provenance)]),
            ("entries", "0", "provenance"),
        ),
    ]


def _located_at(error: ValidationError, *path: str) -> bool:
    """Some error location ends with ``path`` (compared as text)."""
    for e in error.errors():
        loc = tuple(str(p) for p in e["loc"])
        if loc[-len(path) :] == path:
            return True
    return False


def _reject_provenance(
    provenance: dict[str, Any],
    field: str,
    *,
    absent: tuple[str, ...] = (),
    present: tuple[str, ...] = (),
) -> None:
    """The provenance is rejected at ``field`` by all three carriers; none of the ``absent`` texts
    is in any logged form of the error and each of ``present`` is in the message text."""
    for label, model, doc, prefix in _carriers(provenance):
        error = assert_rejected(model, doc, field=field)
        assert _located_at(error, *prefix, field), (label, error.errors(include_input=False))
        texts = error_texts(error)
        ctx_text = repr([e.get("ctx") for e in error.errors(include_input=False)])
        for text in [*texts, ctx_text]:
            for secret in absent:
                assert secret not in text, f"{label}: {secret!r} is shown: {text}"
        messages = " ".join(str(e["msg"]).lower() for e in error.errors(include_input=False))
        for word in present:
            assert word in messages, (label, word, messages)


def _accept_provenance(provenance: dict[str, Any]) -> None:
    for label, model, doc, _prefix in _carriers(provenance):
        if label == "PricingCatalog":
            loaded = make_catalog([entry_doc(provenance=provenance)])
            assert loaded.entries[0].provenance.url == provenance.get("url"), label
        else:
            assert model.model_validate(doc) is not None, label


def test_price_provenance_url_rejects_credentials() -> None:
    """N50 option A, N62, N66 (ADR 0007 Decisions 6, 6a, 6b, 6e; api-surface 23.6 groups D and X):
    ``provenance.url`` runs the URL credential rules, rejected at ``provenance``/``url`` by
    ``PriceProvenance`` alone and inside ``PriceEntry`` and ``PricingCatalog``: userinfo; a
    credential name in the query (case, percent-encoding and ``-``/``_`` variants:
    ``X-Amz-Signature``, ``%74oken``, ``access-token``, ``x_api_key``); a Decision 5a pattern in
    the path and in a query value, as written and percent-encoded; and (N66 (a), decided) a
    credential name in a fragment piece, split at ``&`` and ``;``, with the same decoding and
    folding, including a piece without ``=`` (N70: ``#token`` and ``#a=1&key`` are names). The
    message names the rule and the URL part and no part of the URL appears in ``str(error)``,
    ``errors(include_input=False)`` or their ``ctx`` (N62): not the value, not the parameter
    name. Accepted: a plain ``https`` price page, ``?tokenizer=1``, a 32+ character opaque path
    segment (the high-entropy reason is not applied to URLs), and fragments such as
    ``#audio-models``, ``#tokens=1``, ``#token-pricing``, ``#access_token_ttl=5`` and
    ``#a=1&b=2``."""
    mark = QUOTE_MARK
    encoded_key = "%73" + PASTED_KEY[1:]  # "s" percent-encoded
    base = "https://example.com"
    rejected: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
        (f"https://bob:{mark}@example.com/pricing", ("bob", mark), ("userinfo",)),
        (f"{base}/p?X-Amz-Signature={mark}", ("X-Amz-Signature", mark), ("query",)),
        (f"{base}/p?%74oken={mark}", ("%74oken", mark), ("query",)),
        (f"{base}/p?access-token={mark}", ("access-token", mark), ("query",)),
        (f"{base}/p?x_api_key={mark}", ("x_api_key", mark), ("query",)),
        (f"{base}/p?a=1&TOKEN={mark}", ("TOKEN", mark), ("query",)),
        (f"{base}/{PASTED_KEY}/{mark}", (PASTED_KEY, mark), ("path",)),
        (f"{base}/{encoded_key}/{mark}", (encoded_key, PASTED_KEY, mark), ("path",)),
        (f"{base}/p?state={PASTED_KEY}&m={mark}", (PASTED_KEY, mark), ("query",)),
        (f"{base}/p?state={encoded_key}&m={mark}", (encoded_key, PASTED_KEY, mark), ("query",)),
        (f"{base}/{AWS_KEY}/{mark}", (AWS_KEY, mark), ("path",)),
        (f"{base}/p#access_token={mark}", ("access_token", mark), ("fragment",)),
        (f"{base}/p#state=1&token={mark}", ("token=", mark), ("fragment",)),
        (f"{base}/p#a=1;api-key={mark}", ("api-key", mark), ("fragment",)),
        (f"{base}/p#%74oken={mark}", ("%74oken", mark), ("fragment",)),
        (f"{base}/p#X-Amz-Signature={mark}", ("X-Amz-Signature", mark), ("fragment",)),
        (f"{base}/p#x_api_key=", ("x_api_key",), ("fragment",)),
        (f"{base}/{mark}#token", (mark,), ("fragment",)),
        (f"{base}/{mark}#a=1&key", ("&key", mark), ("fragment",)),
    ]
    for url, secrets, parts in rejected:
        _reject_provenance(provenance_doc(url=url), "url", absent=(url, *secrets), present=parts)
    for url in (
        f"{base}/pricing",
        f"{base}/pricing?tokenizer=1",
        f"{base}/p/a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0",
        f"{base}/p#audio-models",
        f"{base}/p#tokens=1",
        f"{base}/p#token-pricing",
        f"{base}/p#access_token_ttl=5",
        f"{base}/p#a=1&b=2",
        f"{base}/p?monkey=1#section",
    ):
        _accept_provenance(provenance_doc(url=url))


def test_price_provenance_url_syntax() -> None:
    """N50 remainder (a) (ADR 0007 Decision 6e; api-surface 23.6 group Q item 1): ``provenance.url``
    is an absolute URI with the lowercase scheme ``http`` or ``https`` and a host, ASCII only,
    without white space or control characters; a fragment is allowed (price pages use anchors).
    Accepted: ``https://example.com/pricing``, ``http://example.com/p?model=x`` and
    ``https://example.com/pricing#audio-models``. Rejected at ``provenance``/``url`` by pydantic,
    by ``PriceProvenance`` alone and inside ``PriceEntry`` and ``PricingCatalog``: a relative
    reference, the schemes ``ftp``, ``file``, ``ws`` and ``s3``, an upper-case scheme, no host,
    white space, a control character, a non-ASCII character, and a fragment holding a Decision 5a
    credential pattern as written and percent-encoded. No part of the URL appears in any error
    form (N62). The loopback and fragment-name cases are asserted by the two neighbouring
    tests."""
    mark = QUOTE_MARK
    for good in (
        "https://example.com/pricing",
        "http://example.com/p?model=x",
        "https://example.com/pricing#audio-models",
    ):
        _accept_provenance(provenance_doc(url=good))
    encoded_key = "%73" + PASTED_KEY[1:]
    for bad in (
        f"/{mark}/pricing",
        f"example.com/{mark}",
        f"ftp://example.com/{mark}",
        f"file:///etc/{mark}",
        f"ws://example.com/{mark}",
        f"s3://bucket/{mark}",
        f"HTTPS://example.com/{mark}",
        f"Https://example.com/{mark}",
        f"https:///{mark}",
        f"https://example.com/a b/{mark}",
        f"https://example.com/a\tb/{mark}",
        f"https://example.com/a\nb/{mark}",
        f"https://example.com/{chr(0xE9)}/{mark}",
        f"https://example.com/{mark}#{PASTED_KEY}",
        f"https://example.com/{mark}#{encoded_key}",
    ):
        _reject_provenance(provenance_doc(url=bad), "url", absent=(bad, mark))


LOOPBACK_URLS = (
    "http://localhost/pricing",
    "https://LOCALHOST./p",
    "https://api.localhost/p",
    "http://127.0.0.1:8080/p",
    "http://127.1/p",
    "http://0x7f.0.0.1/p",
    "http://2130706433/p",
    "http://%6Cocalhost/p",
    "http://[::1]/p",
    "http://[0:0:0:0:0:0:0:1]/p",
    "http://[::ffff:127.0.0.1]/p",
)
NOT_LOOPBACK_URLS = (
    "http://10.0.0.5/p",
    "http://172.16.0.1/p",
    "http://192.168.1.10/p",
    "http://169.254.1.1/p",
    "http://[fe80::1]/p",
    "http://0.0.0.0/p",
    "http://[::]/p",
    "http://host.docker.internal/p",
    "https://localhost.example/p",
    "https://mylocalhost/p",
    "https://example.com/localhost?h=127.0.0.1#localhost",
)


def test_price_provenance_url_loopback_host_rejected() -> None:
    """N66 (b), decided (ADR 0007 Decision 6e; api-surface 23.6 group X item 2): a price source is
    a public page, so a loopback host (the definition of ADR 0007 Decision 6d, one URL per
    spelling class) is rejected at ``provenance``/``url`` by pydantic, by ``PriceProvenance``
    alone and inside ``PriceEntry`` and ``PricingCatalog``, with a message that names the rule
    ("loopback host") and no part of the URL. Not loopback (accepted): the private and link-local
    ranges, ``0.0.0.0``, ``[::]``, ``host.docker.internal``, look-alike names and a loopback name
    outside the host. For every URL the verdict agrees with ``artifact.is_loopback_uri`` (one
    definition)."""
    is_loopback = api("artifact").is_loopback_uri
    for url in LOOPBACK_URLS:
        assert is_loopback(url) is True, url
        host = url.split("//", 1)[1].split("/", 1)[0]
        _reject_provenance(
            provenance_doc(url=url), "url", absent=(url, host), present=("loopback host",)
        )
    for url in NOT_LOOPBACK_URLS:
        assert is_loopback(url) is False, url
        _accept_provenance(provenance_doc(url=url))


def test_user_supplied_price_requires_provenance_note() -> None:
    """N50 remainder (c), N66 (c), D17 (ADR 0014 Decision 11; api-surface 23.6 groups Q item 2 and X
    item 3): an entry with ``price_source=user_supplied`` and no ``provenance.note`` is rejected at
    ``provenance``/``note`` by pydantic (the JSON Schema half of this presence rule follows
    staff-reviewer's ruling on N12/N38 and is not pinned here); with a note it is accepted; a
    ``catalog`` entry without a note is accepted. A present note that is empty or only ASCII
    white space (``""``, ``" "``, ``"\\t"``, ``"\\n \\r"``) is rejected at ``provenance``/``note``
    for both price sources (D17), validated as ``PriceProvenance``, ``PriceEntry`` and
    ``PricingCatalog``; the JSON Schema of ``note`` has ``minLength: 1``; a note with text and
    surrounding white space is accepted and kept exactly as given. A note of only "white space"
    in the sense of ``str.isspace()`` counts as white space (N71, decided): a no-break space
    and an ideographic space are rejected; invisible characters (U+200B, U+FEFF) are not
    asserted."""
    no_note = entry_doc(price_source="user_supplied", provenance=provenance_doc())
    error = assert_rejected(_entry(), no_note, field="note")
    assert _located_at(error, "provenance", "note")
    catalog_error = assert_rejected(_catalog(), catalog_doc([no_note]), field="note")
    assert _located_at(catalog_error, "entries", "0", "provenance", "note")
    text = "converted at 83.10 INR per USD on 2026-10-01"
    assert (
        make_entry(
            price_source="user_supplied", provenance=provenance_doc(note=text)
        ).provenance.note
        == text
    )
    assert make_entry(price_source="catalog").provenance.note is None
    assert (
        make_entry(price_source="catalog", provenance=provenance_doc(note=text)).provenance.note
        == text
    )
    padded = "  rate 83.2 INR per USD on 2026-10-01 "
    assert (
        make_entry(
            price_source="user_supplied", provenance=provenance_doc(note=padded)
        ).provenance.note
        == padded
    )
    assert _provenance().model_validate(provenance_doc(note=padded)).note == padded
    assert schema_of(_provenance())["properties"]["note"] is not None
    note_schema = schema_of(_provenance())["properties"]["note"]
    assert any(leaf.get("minLength") == 1 for leaf in note_schema.get("anyOf", [note_schema]))
    blanks = ("", " ", "\t", "\n \r", "   ", chr(0xA0), chr(0x3000), " " + chr(0xA0) + " ")
    for blank in blanks:
        for source in ("user_supplied", "catalog"):
            doc = provenance_doc(note=blank)
            for label, model, carried, prefix in _carriers(doc):
                if label != "PriceProvenance":
                    carried = (
                        entry_doc(price_source=source, provenance=doc)
                        if label == "PriceEntry"
                        else catalog_doc([entry_doc(price_source=source, provenance=doc)])
                    )
                error = assert_rejected(model, carried, field="note")
                assert _located_at(error, *prefix, "note"), (label, source, repr(blank))
    for good in ("0", ".", "-"):
        assert _provenance().model_validate(provenance_doc(note=good)).note == good


# -------------------------------------------------------------------- no-echo (N62)


def _hidden_cases() -> list[tuple[str, str, dict[str, Any], str]]:
    """(label, which model, document, field) for every rejectable field of the pricing models;
    the marker is part of the rejected value except where noted."""
    mark = QUOTE_MARK
    url = f"https://example.com/p?token={mark}"
    entry_cases: list[tuple[str, dict[str, Any], str]] = [
        ("provider", entry_doc(provider=marked_key()), "provider"),
        ("model", entry_doc(model=marked_key()), "model"),
        ("alias", entry_doc(aliases=[marked_key()]), "aliases"),
        ("unit", entry_doc(unit=marked_set()), "unit"),
        ("entry_key", entry_doc(entry_key=marked_key()), "entry_key"),
        ("unit_quantity", entry_doc(unit_quantity=mark), "unit_quantity"),
        ("price amount", entry_doc(price={"amount": mark, "currency": "USD"}), "price"),
        (
            "minimum amount",
            entry_doc(billing=billing("none", usd("1") | {"amount": mark})),
            "minimum",
        ),
        ("increment", entry_doc(billing=billing(mark, "none")), "increment"),
        ("billing", entry_doc(billing=mark), "billing"),
        ("effective_from", entry_doc(effective_from=mark), "effective_from"),
        ("condition kind", entry_doc(applies_when=[{"kind": marked_set(), "value": 1}]), "kind"),
        (
            "condition region",
            entry_doc(applies_when=[{"kind": "region", "value": marked_key()}]),
            "value",
        ),
        (
            "condition tokens",
            entry_doc(applies_when=[{"kind": "context_tokens_gt", "value": mark}]),
            "value",
        ),
        ("price_source", entry_doc(price_source=marked_set()), "price_source"),
        ("provenance kind", entry_doc(provenance=provenance_doc(kind=marked_key())), "kind"),
        (
            "provenance retrieved_at",
            entry_doc(provenance=provenance_doc(retrieved_at=mark)),
            "retrieved_at",
        ),
        ("provenance url credential", entry_doc(provenance=provenance_doc(url=url)), "url"),
        (
            "provenance url userinfo",
            entry_doc(provenance=provenance_doc(url=f"https://bob:{mark}@example.com/p")),
            "url",
        ),
        (
            "provenance url pattern",
            entry_doc(provenance=provenance_doc(url=f"https://example.com/{PASTED_KEY}/{mark}")),
            "url",
        ),
        (
            "provenance url loopback",
            entry_doc(provenance=provenance_doc(url=f"http://{mark}.localhost/p")),
            "url",
        ),
        (
            "provenance url fragment",
            entry_doc(provenance=provenance_doc(url=f"https://example.com/p#access_token={mark}")),
            "url",
        ),
        (
            "provenance url syntax",
            entry_doc(provenance=provenance_doc(url=f"ftp://example.com/{mark}")),
            "url",
        ),
        ("unknown field", {**entry_doc(), "unknown_field": mark}, "unknown_field"),
    ]
    cases: list[tuple[str, str, dict[str, Any], str]] = []
    for label, doc, field in entry_cases:
        cases.append((label, "PriceEntry", doc, field))
        cases.append((label + " (in a catalog)", "PricingCatalog", catalog_doc([doc]), field))
    cases.append(
        ("catalog published_at", "PricingCatalog", catalog_doc(published_at=mark), "published_at")
    )
    cases.append(
        (
            "catalog unknown field",
            "PricingCatalog",
            {**catalog_doc(), "unknown_field": mark},
            "unknown_field",
        )
    )
    cases.append(
        ("catalog entries", "PricingCatalog", {**catalog_doc(), "entries": [mark]}, "entries")
    )
    for label, doc, field in entry_cases:
        provenance = doc.get("provenance")
        if isinstance(provenance, dict) and label.startswith("provenance"):
            cases.append((label + " (alone)", "PriceProvenance", provenance, field))
    cases.append(
        (
            "provenance unknown field",
            "PriceProvenance",
            {**provenance_doc(), "unknown_field": mark},
            "unknown_field",
        )
    )
    return cases


def test_pricing_errors_do_not_quote_rejected_values() -> None:
    """N62 (owner, option A; ADR 0007 Decision 8 (a) and (c), ADR 0014 Decision 3; api-surface
    23.6 groups I item 4 and X item 4): ``PriceProvenance``, ``PriceEntry`` and ``PricingCatalog``
    hide their input, and no shared validator quotes a rejected value inside them. A marker
    placed in each rejectable field (a bad provider, model or alias key, a bad unit, entry key,
    unit quantity, price or minimum amount, increment, applies_when kind and value, price source,
    provenance kind, retrieved_at, a credential URL in each form, a loopback URL whose host holds
    the marker, a fragment credential pair, a URL syntax error, an unknown field) never appears in
    ``str(error)``, ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``,
    through ``model_validate`` and ``model_validate_json``, validated alone (``PriceProvenance``,
    ``PriceEntry``) and embedded (``PricingCatalog``), and the error is still located at the
    field. A non-USD currency (``INR``) names neither the code nor ``Indian Rupee``."""
    models = {
        "PriceProvenance": _provenance(),
        "PriceEntry": _entry(),
        "PricingCatalog": _catalog(),
    }
    for model in models.values():
        assert model.model_config.get("hide_input_in_errors") is True, model.__name__
    for label, which, doc, field in _hidden_cases():
        assert_input_hidden(models[which], doc, QUOTE_MARK, field=field, label=label)
    for code, name in (("INR", "Indian Rupee"), ("EUR", "Euro")):
        inr = usd("1") | {"currency": code}
        for label, which, doc, field in (
            ("price", "PriceEntry", entry_doc(price=inr), "price"),
            ("minimum", "PriceEntry", entry_doc(billing=billing("none", inr)), "minimum"),
            (
                "price (in a catalog)",
                "PricingCatalog",
                catalog_doc([entry_doc(price=inr)]),
                "price",
            ),
            (
                "minimum (in a catalog)",
                "PricingCatalog",
                catalog_doc([entry_doc(billing=billing("none", inr))]),
                "minimum",
            ),
        ):
            for secret in (code, name):
                assert_input_hidden(models[which], doc, secret, field=field, label=label)


# ------------------------------------------------------------------------- succession (P-24)

PRICE_PAGE = "https://prices.example.net/" + QUOTE_MARK


def _succession_entry(key: str = "k-input", **over: Any) -> dict[str, Any]:
    """An entry with every content field set to something that can be changed on its own."""
    return entry_doc(
        entry_key=key,
        aliases=["gpt-4.1-2025", "gpt-4.1-latest"],
        applies_when=[{"kind": "batch", "value": False}, {"kind": "region", "value": "us"}],
        billing=billing("15", usd("0.005")),
        provenance=provenance_doc(url=PRICE_PAGE, note="checked"),
        **over,
    )


def _pair(
    before: list[dict[str, Any]], after: list[dict[str, Any]], **next_over: Any
) -> tuple[Any, Any]:
    first = make_catalog(before)
    later = {"published_at": "2026-11-01T00:00:00.000000Z", **next_over}
    second = make_catalog(after, id=str(first.id), version=first.version + 1, **later)
    return first, second


def _succession() -> Any:
    return api("pricing").check_catalog_succession


def test_entry_identifier_stable_across_catalog_versions() -> None:
    """P-24, N47 remainder (option A), N65, N72 (ADR 0014 Decision 10a; api-surface 23.6 groups O
    and W): ``pricing.check_catalog_succession(previous, next)`` states that an unchanged entry
    keeps its ``entry_key`` across versions of one catalog and a changed one gets a new key.
    "Content" is every field except ``entry_key``, ``provenance`` and ``effective_to`` (N65);
    ``applies_when`` and ``aliases`` are compared as sets (N72). Returns ``None`` for: every entry
    unchanged; an entry removed; a new key with new content; a changed price under a new key
    while the old key is gone; a key reused with only ``provenance`` changed (``kind``, ``url``,
    ``retrieved_at`` and ``note`` each alone, and all four together); only ``effective_to``
    changed (closing an open range, moving it later, removing it); the same ``aliases`` or
    ``applies_when`` in another order; ``next.version == previous.version + 2``. A ``ValueError``
    whose message contains "entry" and the key (never a provenance URL) for: a key reused with a
    different ``price``, ``unit_quantity``, ``billing``, ``applies_when``, ``effective_from``,
    ``unit``, ``model``, ``provider``, ``price_source`` or ``aliases`` (one added, one removed);
    an entry of ``next`` with the content of an entry of ``previous`` under a new key, also with
    another ``provenance`` or ``effective_to``. Before any entry is compared (D18): other catalog
    ``id``s ("catalog") and a ``next.version`` equal to or lower than ``previous.version``
    ("version"). The function is pure: both catalogs dump equal before and after."""
    check = _succession()
    base = _succession_entry()
    fresh = _succession_entry("k-output", unit="output_tokens", price=usd("0.6"))

    def accepted(before: list[dict[str, Any]], after: list[dict[str, Any]], **over: Any) -> None:
        first, second = _pair(before, after, **over)
        snapshot = (first.model_dump(mode="json"), second.model_dump(mode="json"))
        assert check(first, second) is None
        assert (first.model_dump(mode="json"), second.model_dump(mode="json")) == snapshot

    def rejected(
        before: list[dict[str, Any]], after: list[dict[str, Any]], *words: str, **over: Any
    ) -> None:
        first, second = _pair(before, after, **over)
        snapshot = (first.model_dump(mode="json"), second.model_dump(mode="json"))
        with pytest.raises(ValueError, match=r"(?s).+") as caught:
            check(first, second)
        message = str(caught.value)
        for word in words:
            assert word in message, (word, message)
        assert QUOTE_MARK not in message
        assert "prices.example.net" not in message
        assert (first.model_dump(mode="json"), second.model_dump(mode="json")) == snapshot

    accepted([base], [dict(base)])
    accepted([base, fresh], [base])
    accepted([base], [base, fresh])
    accepted([base], [_succession_entry("k-input-2", price=usd("0.16"))])
    accepted([base, fresh], [base, fresh])
    first, _ = _pair([base], [base])
    assert check(first, make_catalog([base], id=str(first.id), version=first.version + 2)) is None
    for change in (
        {"kind": "other_page"},
        {"url": "https://prices.example.net/archive/2026"},
        {"retrieved_at": "2026-10-20T00:00:00.000000Z"},
        {"note": "checked again"},
        {
            "kind": "other_page",
            "url": "https://prices.example.net/archive/2026",
            "retrieved_at": "2026-10-20T00:00:00.000000Z",
            "note": "checked again",
        },
    ):
        reused = {**base, "provenance": {**base["provenance"], **change}}
        accepted([base], [reused])
    for before_to, after_to in ((None, JUN), (MAR, JUN), (JUN, None)):
        old = dict(base) if before_to is None else {**base, "effective_to": before_to}
        new = dict(base) if after_to is None else {**base, "effective_to": after_to}
        accepted([old], [new])
    accepted(
        [base],
        [
            {
                **base,
                "aliases": ["gpt-4.1-latest", "gpt-4.1-2025"],
                "applies_when": [
                    {"kind": "region", "value": "us"},
                    {"kind": "batch", "value": False},
                ],
            }
        ],
    )
    changes: dict[str, Any] = {
        "price": usd("0.16"),
        "unit_quantity": "1000",
        "billing": billing("30", "none"),
        "applies_when": [{"kind": "batch", "value": True}, {"kind": "region", "value": "us"}],
        "effective_from": MAR,
        "unit": "output_tokens",
        "model": "gpt-4.1-mini",
        "provider": "anthropic",
        "price_source": "user_supplied",
        "aliases": ["gpt-4.1-2025", "gpt-4.1-latest", "gpt-4.1-extra"],
    }
    for field, value in changes.items():
        extra = {"provenance": provenance_doc(url=PRICE_PAGE, note="checked")}
        rejected([base], [{**base, field: value, **extra}], "entry", "k-input")
    rejected([base], [{**base, "aliases": ["gpt-4.1-2025"]}], "entry", "k-input")
    rejected(
        [base], [{**base, "applies_when": [{"kind": "batch", "value": False}]}], "entry", "k-input"
    )
    twin = _succession_entry("k-input-twin")
    rejected([base], [twin], "entry", "k-input-twin")
    rejected(
        [base],
        [{**twin, "provenance": provenance_doc(url=PRICE_PAGE + "/other", note="other")}],
        "entry",
        "k-input-twin",
    )
    rejected([base], [{**twin, "effective_to": JUN}], "entry", "k-input-twin")
    one = make_catalog([base])
    other_id = make_catalog([base], id=new_id_text("pcat"), version=2)
    with pytest.raises(ValueError, match="catalog"):
        check(one, other_id)
    same = make_catalog([base], id=str(one.id), version=1)
    with pytest.raises(ValueError, match="version"):
        check(one, same)
    higher = make_catalog([base], id=str(one.id), version=5)
    lower = make_catalog([base], id=str(one.id), version=3)
    with pytest.raises(ValueError, match="version"):
        check(higher, lower)
    with pytest.raises(ValueError, match="version"):
        check(higher, higher)
    # the checks of the catalogs come first: a reused key with other content under another id
    with pytest.raises(ValueError, match="catalog"):
        check(
            one, make_catalog([{**base, "price": usd("0.99")}], id=new_id_text("pcat"), version=2)
        )
