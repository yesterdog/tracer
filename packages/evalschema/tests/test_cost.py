# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-25, part 1: the cost record models (ADR 0014 Decisions 12, 12a, 12b, 14 and 16, P-35, P-38,
P-52, P-53, O6).

Assumed public API (docs/phase1/api-surface.md section 23), ``tracer_evalschema.cost``: the entity
``CostRecord`` (``cst``, a record of fact) and the value objects ``PricingSnapshot{catalog_id,
version, content_digest, entry_keys}`` (revision 16, N61: the entries are named by their
author-assigned ``entry_key``), ``CostRounding{scale, mode, exact}`` and
``ReportedCostRaw{reported_by, amount_raw, currency_raw?, currency_source}``; the function
``check_pricing_snapshot(snapshot, catalog_doc)``. ``CostRecord``, ``ReportedCostRaw`` and
``UsageRecord`` hide their input (N60).

The summary rules are in ``test_cost_summary.py``, the pure functions in ``test_cost_compute.py``,
the importer helper in ``test_cost_import.py``. Names are SCOPE section 5 names unless the
docstring says "Proposed name". A failure of these tests is about the cost module only.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import PACKAGE_DIR, ROOT, api
from ._support_b2 import (
    assert_rejected,
    new_id,
    property_enum,
    registered_class,
    schema_accepts,
    schema_of,
)
from ._support_b3 import QUOTE_MARK, assert_input_hidden, marked_id, marked_key, marked_set
from ._support_b4 import (
    DOMAINS,
    NA_REASONS,
    cost_class,
    cost_fn,
    ctx,
    entry_doc,
    make_catalog,
    qty,
    record_computed_doc,
    record_na_doc,
    record_reported_doc,
    reported_raw_doc,
    snapshot_doc,
    usage_doc,
    usd,
    when,
)

NONEMPTY = r"(?s).+"  # any message: the architect words the text, the test pins the type


def _record() -> Any:
    return cost_class("CostRecord")


def _make(doc: dict[str, Any]) -> Any:
    return _record().model_validate(doc)


# ------------------------------------------------------------------------------ shape


def test_cost_record_shape_and_enums() -> None:
    """Proposed name (ADR 0014 Decision 12, ADR 0003 Decision 1): a cost record is an entity
    (``cst``) with exactly the entity fields plus ``domain``, ``usage_record_ids``, ``status``,
    ``amount``, ``basis``, ``reported_by``, ``pricing_snapshot``, ``rounding``, ``na_reason``,
    ``unpriced_units`` and ``reported_raw``. ``domain``, ``usage_record_ids`` and ``status`` are
    required; ``unpriced_units`` defaults to an empty list. The enums are exact: domain (2),
    status (available, not_available), reported_by (provider, telemetry_platform) and na_reason
    (the ten reasons: revision 15 added ``amount_unparseable`` (N59), revision 16
    ``currency_conflict`` (N59 remainder)). ``usage_record_ids`` are ``use_`` IDs. A document of
    each valid shape (reported, computed, each not-available reason) is accepted."""
    model = _record()
    entity_fields = {"id", "schema_version", "created_at", "metadata", "metadata_flags"}
    assert set(model.model_fields) == entity_fields | {
        "domain",
        "usage_record_ids",
        "status",
        "amount",
        "basis",
        "reported_by",
        "pricing_snapshot",
        "rounding",
        "na_reason",
        "unpriced_units",
        "reported_raw",
    }
    assert {"domain", "usage_record_ids", "status"} <= set(schema_of(model)["required"])
    assert sorted(property_enum(model, "domain")) == sorted(DOMAINS)
    assert sorted(property_enum(model, "status")) == ["available", "not_available"]
    assert sorted(property_enum(model, "reported_by")) == ["provider", "telemetry_platform"]
    assert sorted(property_enum(model, "na_reason")) == sorted(NA_REASONS)
    assert len(property_enum(model, "na_reason")) == 10
    assert _make(record_reported_doc()).unpriced_units == []
    doc = record_reported_doc()
    del doc["unpriced_units"]
    assert _make(doc).unpriced_units == []
    assert _make(record_reported_doc(reported_by="telemetry_platform")).reported_by == (
        "telemetry_platform"
    )
    catalog = make_catalog()
    assert _make(record_computed_doc(catalog)).basis == "computed"
    for reason in NA_REASONS:
        loaded = _make(record_na_doc(reason))
        assert loaded.status == "not_available"
        assert loaded.na_reason == reason
        assert schema_accepts(model, loaded.model_dump(mode="json"))
    for field in ("domain", "usage_record_ids", "status"):
        doc = record_reported_doc()
        del doc[field]
        assert_rejected(model, doc, field=field)
    for bad_domain in ("agent", "Evaluation", "", None):
        assert_rejected(model, record_reported_doc(domain=bad_domain), field="domain")
    for bad_status in ("ok", "Available", "unavailable", "", None):
        assert_rejected(model, record_reported_doc(status=bad_status), field="status")
    for bad_reason in ("unknown", "no_price", "NO_USAGE", "", 3):
        assert_rejected(model, record_na_doc(na_reason=bad_reason), field="na_reason")
    ids = api("ids")
    for bad_id in (str(ids.RunId.new()), str(ids.UsageRecordId.new().uuid), "", None):
        assert_rejected(
            model, record_reported_doc(usage_record_ids=[bad_id]), field="usage_record_ids"
        )
    for bad_unit in ("tokens", "Output_Tokens", ""):
        assert_rejected(
            model,
            record_na_doc("unit_unpriced", unpriced_units=[bad_unit]),
            field="unpriced_units",
        )


def test_basis_enum_exact() -> None:
    """ADR 0014 Decision 12, I14, ADR 0003 I4: ``basis`` is exactly reported or computed, an
    ``enum`` in the JSON Schema. There is no ``estimated`` (removed by the owner, OQ4), and another
    case or spelling is rejected, in Python and by the JSON Schema."""
    model = _record()
    assert sorted(property_enum(model, "basis")) == ["computed", "reported"]
    assert len(property_enum(model, "basis")) == 2
    catalog = make_catalog()
    for bad in ("estimated", "Reported", "computed ", "calculated", "", 1, True):
        assert_rejected(model, record_reported_doc(basis=bad), field="basis")
        assert_rejected(model, record_computed_doc(catalog, basis=bad), field="basis")
    good = _make(record_reported_doc()).model_dump(mode="json")
    assert schema_accepts(model, good)
    assert not schema_accepts(model, {**good, "basis": "estimated"})


def test_not_available_cost_has_no_basis() -> None:
    """ADR 0014 Decision 12, I14, A-14: ``basis`` is present if and only if ``status`` is
    available. A not-available record has no ``basis`` and also no ``amount`` (not even "0"), no
    ``reported_by``, no ``pricing_snapshot`` and no ``rounding``, each rejected at its own field;
    an available record without ``basis`` or without ``amount`` is rejected, and so is an
    available record with an ``na_reason``; a not-available record needs one."""
    model = _record()
    catalog = make_catalog()
    for reason in ("no_price_for_model", "billing_rules_missing", "ambiguous_price", "no_usage"):
        for field, value in (
            ("basis", "reported"),
            ("basis", "computed"),
            ("amount", usd("0")),
            ("amount", usd("12.5")),
            ("reported_by", "provider"),
            ("pricing_snapshot", snapshot_doc(catalog)),
            ("rounding", {"scale": 18, "mode": "half_even", "exact": False}),
        ):
            assert_rejected(model, record_na_doc(reason, **{field: value}), field=field)
    doc = record_reported_doc()
    del doc["basis"]
    assert_rejected(model, doc, field="basis")
    doc = record_reported_doc()
    del doc["amount"]
    assert_rejected(model, doc, field="amount")
    assert_rejected(model, record_reported_doc(amount=None), field="amount")
    assert_rejected(model, record_reported_doc(basis=None), field="basis")
    assert_rejected(model, record_reported_doc(na_reason="no_usage"), field="na_reason")
    doc = record_na_doc("no_price_for_model")
    del doc["na_reason"]
    assert_rejected(model, doc, field="na_reason")
    assert _make(record_na_doc("no_price_for_model")).basis is None
    assert _make(record_na_doc("no_price_for_model")).amount is None


def test_cost_amount_nonnegative() -> None:
    """ADR 0014 Decision 7, I24, P-38: ``Money`` may be negative in general (a refund is a ledger
    entry for later phases) but ``CostRecord.amount`` must be >= 0, because a negative computed
    cost is always a bug; the rejection is located at ``amount``. Zero is a known cost and is
    accepted, so a free call is not confused with a missing cost."""
    money = api("primitives").Money
    assert money(amount="-1", currency="USD").amount == "-1"
    catalog = make_catalog()
    for bad in ("-1", "-0.5", "-0.000000000000000001"):
        assert_rejected(_record(), record_reported_doc(amount=usd(bad)), field="amount")
        assert_rejected(_record(), record_computed_doc(catalog, amount=usd(bad)), field="amount")
    assert _make(record_reported_doc(amount=usd("0"))).amount.amount == "0"
    assert _make(record_computed_doc(catalog, amount=usd("0"))).amount.amount == "0"
    assert _make(record_reported_doc(amount=usd("0.000000000000000001"))).amount is not None
    for bad_currency in ("EUR", "INR", "usd", ""):
        assert_rejected(
            _record(),
            record_reported_doc(amount={"amount": "1", "currency": bad_currency}),
            field="amount",
        )
    for bad_amount in (1.5, 0.0, "1.50", "1e3", None):
        assert_rejected(
            _record(), record_reported_doc(amount={"amount": bad_amount, "currency": "USD"})
        )


def test_reported_basis_requires_reporter() -> None:
    """ADR 0014 Decision 12, I17: ``basis=reported`` requires ``reported_by``, provider or
    telemetry_platform, rejected at ``reported_by`` when absent, ``None`` or another value. A
    computed record names no reporter (``reported_by`` belongs to the reported basis; P reading,
    section 23.3 N51)."""
    model = _record()
    doc = record_reported_doc()
    del doc["reported_by"]
    assert_rejected(model, doc, field="reported_by")
    assert_rejected(model, record_reported_doc(reported_by=None), field="reported_by")
    for bad in ("vendor", "Provider", "telemetry", "", 1):
        assert_rejected(model, record_reported_doc(reported_by=bad), field="reported_by")
    for good in ("provider", "telemetry_platform"):
        assert _make(record_reported_doc(reported_by=good)).reported_by == good
    assert_rejected(
        model, record_computed_doc(make_catalog(), reported_by="provider"), field="reported_by"
    )


def test_computed_requires_pricing_snapshot() -> None:
    """ADR 0014 Decision 12, I17: ``basis=computed`` requires ``pricing_snapshot``, rejected at
    ``pricing_snapshot`` when absent or ``None``. The snapshot names the catalog by ``catalog_id``
    (a ``pcat_`` ID), ``version`` (an integer >= 1), ``content_digest`` and ``entry_keys``: a
    non-empty list of distinct machine keys, the ``entry_key`` of the entries used (N61, option B;
    a key is scoped by the catalog reference). Revision 16 specification change (no assertion
    removed): the wrong-kind typed ID case of ``entry_ids`` has no meaning for an author key and
    is replaced by the key cases below; a snapshot that still carries ``entry_ids`` is an unknown
    field, like ``digest`` (O6). A reported record carries no snapshot (N51)."""
    model = _record()
    catalog = make_catalog()
    doc = record_computed_doc(catalog)
    del doc["pricing_snapshot"]
    assert_rejected(model, doc, field="pricing_snapshot")
    assert_rejected(
        model, record_computed_doc(catalog, pricing_snapshot=None), field="pricing_snapshot"
    )
    loaded = _make(record_computed_doc(catalog))
    assert str(loaded.pricing_snapshot.catalog_id) == str(catalog.id)
    assert loaded.pricing_snapshot.version == catalog.version
    assert loaded.pricing_snapshot.entry_keys == [catalog.entries[0].entry_key]
    ids = api("ids")
    for field, bad in (
        ("catalog_id", str(ids.RunId.new())),
        ("catalog_id", str(ids.PricingCatalogId.new().uuid)),
        ("version", 0),
        ("version", "1"),
        ("version", 1.5),
        ("entry_keys", [""]),
        ("entry_keys", ["bad key"]),
        ("entry_keys", []),
        ("entry_keys", ["k1", "k1"]),
        ("entry_keys", [7]),
    ):
        assert_rejected(
            model,
            record_computed_doc(catalog, pricing_snapshot=snapshot_doc(catalog, **{field: bad})),
            field="pricing_snapshot",
        )
    for field in ("catalog_id", "version", "content_digest", "entry_keys"):
        snap = snapshot_doc(catalog)
        del snap[field]
        assert_rejected(model, record_computed_doc(catalog, pricing_snapshot=snap), field=field)
    snapshot_model = registered_class("PricingSnapshot")
    keys_schema = schema_of(snapshot_model)["properties"]["entry_keys"]
    assert keys_schema["minItems"] == 1
    assert keys_schema["uniqueItems"] is True
    for bad_keys in ([], [""], ["bad key"], ["k1", "k1"]):
        assert not schema_accepts(snapshot_model, snapshot_doc(catalog, bad_keys))
    assert schema_accepts(snapshot_model, snapshot_doc(catalog, ["k1", "k2"]))
    old_name = snapshot_doc(catalog)
    old_name["entry_ids"] = [str(ids.UsageRecordId.new())]
    assert_rejected(
        model, record_computed_doc(catalog, pricing_snapshot=old_name), field="pricing_snapshot"
    )
    assert_rejected(
        model, record_reported_doc(pricing_snapshot=snapshot_doc(catalog)), field="pricing_snapshot"
    )


def test_cost_pricing_snapshot_content_digest_named_and_verified() -> None:
    """Revision 12, O6 (ADR 0014 Decision 12, I17a, ADR 0006 Decision 1): the pricing snapshot's
    fields are exactly ``catalog_id``, ``version``, ``content_digest`` and ``entry_keys``; the old
    name ``digest`` is an unknown field and is rejected; ``content_digest`` is required on a
    computed cost. It is verified against the catalog with the rules of
    ``snapshot.check_pricing_catalog_ref``, here ``cost.check_pricing_snapshot(snapshot,
    catalog_doc)``: another catalog or version, another scheme, or a keyed digest with another
    ``key_ref`` is a ``ValueError``; the same scheme and ``key_ref`` with another value is
    ``RefDigestMismatchError``; two keyed digests with the same ``key_ref`` are compared. The
    bundle seam sees the digest under the name ``content_digest``: a reference built from the
    snapshot's dump is checked by ``bundle.check_bundle_consistency``."""
    errors = api("errors")
    snapshot_model = registered_class("PricingSnapshot")
    assert set(snapshot_model.model_fields) == {
        "catalog_id",
        "version",
        "content_digest",
        "entry_keys",
    }
    assert set(schema_of(snapshot_model)["required"]) == set(snapshot_model.model_fields)
    catalog = make_catalog()
    catalog_doc = catalog.model_dump(mode="json")
    plain = snapshot_doc(catalog)
    renamed = {k: v for k, v in plain.items() if k != "content_digest"}
    renamed["digest"] = plain["content_digest"]
    with pytest.raises(ValidationError) as caught:
        snapshot_model.model_validate(renamed)
    kinds = {(e["type"], str(e["loc"][-1])) for e in caught.value.errors()}
    assert ("extra_forbidden", "digest") in kinds
    assert ("missing", "content_digest") in kinds
    with pytest.raises(ValidationError) as both:
        snapshot_model.model_validate({**plain, "digest": plain["content_digest"]})
    assert {e["type"] for e in both.value.errors()} == {"extra_forbidden"}
    record = _make(record_computed_doc(catalog))
    dumped = record.model_dump(mode="json")["pricing_snapshot"]
    assert "digest" not in dumped
    shown = {k: v for k, v in dumped["content_digest"].items() if v is not None}
    assert shown == plain["content_digest"]
    assert "digest" not in json.loads(record.model_dump_json())["pricing_snapshot"]
    snapshot = record.pricing_snapshot
    check = cost_fn("check_pricing_snapshot")
    check(snapshot, catalog_doc)
    other_version = copy.deepcopy(catalog_doc)
    other_version["version"] = 2
    other_id = {**catalog_doc, "id": new_id("pcat")}
    unknown_scheme = {**plain, "content_digest": {"scheme": "sha512-jcs/v9", "value": "0" * 64}}
    with pytest.raises(errors.UnknownDigestScheme):
        snapshot_model.model_validate(unknown_scheme)
    for other in (other_version, other_id):
        with pytest.raises(ValueError, match=NONEMPTY):
            check(snapshot, other)
    keyed_a = {
        "scheme": "hmac-sha256-jcs/v1",
        "value": "a" * 64,
        "key_ref": {"key_id": "tracer-key-a"},
    }
    keyed_b = {**keyed_a, "key_ref": {"key_id": "tracer-key-b"}}
    keyed_a_other = {**keyed_a, "value": "b" * 64}
    keyed_snapshot = snapshot_model.model_validate({**plain, "content_digest": keyed_a})
    check(keyed_snapshot, {**catalog_doc, "content_digest": keyed_a})
    with pytest.raises(ValueError, match=NONEMPTY):
        check(keyed_snapshot, {**catalog_doc, "content_digest": catalog_doc["content_digest"]})
    with pytest.raises(ValueError, match=NONEMPTY):
        check(keyed_snapshot, {**catalog_doc, "content_digest": keyed_b})
    with pytest.raises(errors.RefDigestMismatchError):
        check(keyed_snapshot, {**catalog_doc, "content_digest": keyed_a_other})
    wrong_value = {
        **catalog_doc,
        "content_digest": {**catalog_doc["content_digest"], "value": "0" * 64},
    }
    with pytest.raises(errors.RefDigestMismatchError):
        check(snapshot, wrong_value)
    reference = {
        "id": dumped["catalog_id"],
        "version": dumped["version"],
        "content_digest": dumped["content_digest"],
    }
    api("bundle").check_bundle_consistency([catalog_doc], [reference])
    with pytest.raises(errors.RefDigestMismatchError):
        api("bundle").check_bundle_consistency(
            [wrong_value], [{**reference, "content_digest": dumped["content_digest"]}]
        )


def test_pricing_snapshot_entry_keys_resolve_in_catalog() -> None:
    """Proposed name (ADR 0009 Decision 5: a snapshot always names ``(catalog_id, version,
    entry_keys)``, so resolution is unambiguous; renamed in revision 16 from
    ``..._entry_ids_...`` because of N61, the old name must not exist): ``check_pricing_snapshot``
    also rejects an entry key that is not the ``entry_key`` of an entry of the catalog document,
    with a ``ValueError`` that says "entry" (N52). A key that names an entry passes, also with
    several entries."""
    catalog = make_catalog(
        [entry_doc(entry_key="k-a"), entry_doc(entry_key="k-b", unit="output_tokens")]
    )
    record = _make(record_computed_doc(catalog))
    check = cost_fn("check_pricing_snapshot")
    document = catalog.model_dump(mode="json")
    check(record.pricing_snapshot, document)
    both = _make(
        record_computed_doc(catalog, pricing_snapshot=snapshot_doc(catalog, ["k-a", "k-b"]))
    )
    check(both.pricing_snapshot, document)
    for stray_key in ("k-zzz", "K-A", "k-"):
        stray = record_computed_doc(catalog, pricing_snapshot=snapshot_doc(catalog, [stray_key]))
        with pytest.raises(ValueError, match="entry"):
            check(_make(stray).pricing_snapshot, document)
    mixed = record_computed_doc(catalog, pricing_snapshot=snapshot_doc(catalog, ["k-a", "k-zzz"]))
    with pytest.raises(ValueError, match="entry"):
        check(_make(mixed).pricing_snapshot, document)


# ------------------------------------------------------------------------------ rounding


def test_cost_rounding_fields_exact() -> None:
    """ADR 0014 Decision 15, P-35: ``CostRounding`` is the value object ``{scale: 18, mode:
    half_even, exact: false}``, all three required. Any other scale, another mode (``mode`` is an
    ``enum`` with one member, never ``const``) or ``exact: true`` is rejected. ``rounding`` is valid
    only on an available computed record (P reading, N51): a reported record with it is
    rejected."""
    model = registered_class("CostRounding")
    assert set(model.model_fields) == {"scale", "mode", "exact"}
    assert set(schema_of(model)["required"]) == {"scale", "mode", "exact"}
    assert property_enum(model, "mode") == ["half_even"]
    good = {"scale": 18, "mode": "half_even", "exact": False}
    loaded = model.model_validate(good)
    assert (loaded.scale, loaded.mode, loaded.exact) == (18, "half_even", False)
    assert json.loads(loaded.model_dump_json()) == good
    for field, bad_values in (
        ("scale", (17, 19, 0, "18", 18.0, True, None)),
        ("mode", ("half_up", "HALF_EVEN", "half-even", "down", "", None)),
        ("exact", (True, "false", 0, None)),
    ):
        for bad in bad_values:
            assert_rejected(model, {**good, field: bad}, field=field)
    for field in good:
        missing = dict(good)
        del missing[field]
        assert_rejected(model, missing, field=field)
    catalog = make_catalog()
    assert _make(record_computed_doc(catalog, rounding=good)).rounding.scale == 18
    assert _make(record_computed_doc(catalog)).rounding is None
    assert_rejected(_record(), record_reported_doc(rounding=good), field="rounding")


# ------------------------------------------------------------------------ reported raw


def test_reported_non_usd_cost_not_available_raw_preserved() -> None:
    """ADR 0014 Decision 12, I7b, A-12: a reported cost in another currency is not rejected: it is
    a not_available record with ``na_reason=unsupported_currency``, no amount, no basis, and
    ``reported_raw`` holding the amount lexeme and the currency label verbatim, with
    ``currency_source=payload``. A reported "12.50" EUR stays "12.50" and "EUR"; nothing is
    parsed, canonicalized or converted, and every odd lexeme and label is kept as sent (a
    trailing zero, a separator, an exponent, white space, a non-ASCII digit string, a lower-case
    code, a symbol)."""
    native = "".join(chr(cp) for cp in (0x0967, 0x0968, 0x0969))
    lexemes = ("12.50", "1,234.50", "1e3", " 7 ", "0.10", "007", "NaN", native, "12.", "1_000")
    labels = ("EUR", "inr", "ABC", "$", "US$", "dollars", " eur ", "XAU")
    for amount_raw in lexemes:
        for label in labels:
            raw = reported_raw_doc(amount_raw=amount_raw, currency_raw=label)
            loaded = _make(record_na_doc("unsupported_currency", reported_raw=raw))
            assert loaded.status == "not_available"
            assert loaded.na_reason == "unsupported_currency"
            assert loaded.amount is None
            assert loaded.basis is None
            assert loaded.reported_raw.amount_raw == amount_raw
            assert loaded.reported_raw.currency_raw == label
            assert loaded.reported_raw.currency_source == "payload"
            wire = json.loads(loaded.model_dump_json())["reported_raw"]
            assert wire["amount_raw"] == amount_raw
            assert wire["currency_raw"] == label
            again = _record().model_validate_json(loaded.model_dump_json())
            assert again.reported_raw == loaded.reported_raw
    model = registered_class("ReportedCostRaw")
    assert set(model.model_fields) == {
        "reported_by",
        "amount_raw",
        "currency_raw",
        "currency_source",
    }
    assert {"reported_by", "amount_raw", "currency_source"} <= set(schema_of(model)["required"])
    assert "currency_raw" not in schema_of(model)["required"]
    assert sorted(property_enum(model, "reported_by")) == ["provider", "telemetry_platform"]
    bad: Any
    for bad in (12.5, 12, None, True):
        assert_rejected(model, reported_raw_doc(amount_raw=bad), field="amount_raw")
    for bad in ("vendor", "", None):
        assert_rejected(model, reported_raw_doc(reported_by=bad), field="reported_by")
    assert_rejected(model, reported_raw_doc(note="x"))


def test_reported_raw_present_iff_currency_na_reason() -> None:
    """ADR 0014 Decision 12, I7b, I26: ``reported_raw`` is present if and only if ``na_reason`` is
    ``unsupported_currency``, ``currency_not_reported``, (revision 15, N59) ``amount_unparseable``
    or (revision 16) ``currency_conflict``. For each of the ten reasons, a record that carries
    ``reported_raw`` when it should not (or lacks it when it should) is rejected at
    ``reported_raw``; an available record with ``reported_raw`` is rejected."""
    catalog = make_catalog()
    needs = {
        "unsupported_currency",
        "currency_not_reported",
        "amount_unparseable",
        "currency_conflict",
    }
    for reason in NA_REASONS:
        good = record_na_doc(reason)
        assert ("reported_raw" in good) == (reason in needs)
        _make(good)
        if reason in needs:
            without = {k: v for k, v in good.items() if k != "reported_raw"}
            assert_rejected(_record(), without, field="reported_raw")
            assert_rejected(_record(), {**good, "reported_raw": None}, field="reported_raw")
        else:
            assert_rejected(
                _record(), {**good, "reported_raw": reported_raw_doc()}, field="reported_raw"
            )
    assert_rejected(
        _record(), record_reported_doc(reported_raw=reported_raw_doc()), field="reported_raw"
    )
    assert_rejected(
        _record(),
        record_computed_doc(catalog, reported_raw=reported_raw_doc()),
        field="reported_raw",
    )


def test_reported_raw_currency_source_consistent() -> None:
    """ADR 0014 Decision 12b, I26, P-53: ``currency_source`` is payload, api_contract or none (an
    ``enum``). ``currency_raw`` is present if and only if the source is payload or api_contract; a
    source of none has no ``currency_raw``. ``currency_not_reported`` goes with
    ``currency_source=none`` and ``unsupported_currency`` with payload or api_contract, never
    none; revision 15: ``amount_unparseable`` goes with payload or api_contract, never none;
    revision 16: ``currency_conflict`` goes with payload only (the payload label is the one
    kept). The record is rejected at ``reported_raw`` otherwise."""
    model = registered_class("ReportedCostRaw")
    assert sorted(property_enum(model, "currency_source")) == ["api_contract", "none", "payload"]
    for source in ("payload", "api_contract"):
        loaded = model.model_validate(reported_raw_doc(currency_source=source))
        assert loaded.currency_raw == "EUR"
        without = reported_raw_doc(currency_source=source)
        del without["currency_raw"]
        assert_rejected(model, without, field="currency_raw")
        assert_rejected(
            model, reported_raw_doc(currency_source=source, currency_raw=None), field="currency_raw"
        )
    quiet = {"reported_by": "provider", "amount_raw": "7", "currency_source": "none"}
    loaded = model.model_validate(quiet)
    assert loaded.currency_raw is None
    assert loaded.currency_source == "none"
    assert_rejected(model, {**quiet, "currency_raw": "USD"}, field="currency_raw")
    assert_rejected(model, {**quiet, "currency_raw": ""}, field="currency_raw")
    for bad in ("header", "Payload", "", None, 0):
        assert_rejected(model, reported_raw_doc(currency_source=bad), field="currency_source")
    missing = reported_raw_doc()
    del missing["currency_source"]
    assert_rejected(model, missing, field="currency_source")
    # the record rules tie the reason to the source
    not_reported = record_na_doc("currency_not_reported")
    assert _make(not_reported).reported_raw.currency_source == "none"
    assert_rejected(
        _record(),
        record_na_doc("currency_not_reported", reported_raw=reported_raw_doc()),
        field="reported_raw",
    )
    for source in ("payload", "api_contract"):
        raw = reported_raw_doc(currency_source=source)
        assert (
            _make(
                record_na_doc("unsupported_currency", reported_raw=raw)
            ).reported_raw.currency_source
            == source
        )
    assert_rejected(
        _record(),
        record_na_doc("unsupported_currency", reported_raw=dict(quiet)),
        field="reported_raw",
    )
    unparseable = {"reported_by": "provider", "amount_raw": "abc", "currency_raw": "USD"}
    for source in ("payload", "api_contract"):
        raw = {**unparseable, "currency_source": source}
        loaded_record = _make(record_na_doc("amount_unparseable", reported_raw=raw))
        assert loaded_record.reported_raw.currency_source == source
    assert_rejected(
        _record(),
        record_na_doc("amount_unparseable", reported_raw={**quiet, "amount_raw": "abc"}),
        field="reported_raw",
    )
    conflict = {"reported_by": "provider", "amount_raw": "12.50", "currency_raw": "USD"}
    payload_conflict = _make(
        record_na_doc("currency_conflict", reported_raw={**conflict, "currency_source": "payload"})
    )
    assert payload_conflict.reported_raw.currency_source == "payload"
    assert_rejected(
        _record(),
        record_na_doc(
            "currency_conflict", reported_raw={**conflict, "currency_source": "api_contract"}
        ),
        field="reported_raw",
    )
    assert_rejected(
        _record(),
        record_na_doc("currency_conflict", reported_raw={**quiet, "amount_raw": "12.50"}),
        field="reported_raw",
    )


def test_unpriced_units_listed_only_with_unit_unpriced() -> None:
    """Proposed name (ADR 0014 Decision 13: an unpriced unit makes the record not_available as a
    whole and lists ``unpriced_units``): ``unpriced_units`` is non-empty if and only if
    ``na_reason`` is ``unit_unpriced``, rejected at ``unpriced_units`` otherwise; units are
    usage units, listed once each (P reading, N51)."""
    model = _record()
    good = record_na_doc("unit_unpriced", unpriced_units=["output_tokens", "requests"])
    assert _make(good).unpriced_units == ["output_tokens", "requests"]
    assert_rejected(
        model, record_na_doc("unit_unpriced", unpriced_units=[]), field="unpriced_units"
    )
    assert_rejected(
        model,
        record_na_doc("unit_unpriced", unpriced_units=["requests", "requests"]),
        field="unpriced_units",
    )
    for reason in NA_REASONS:
        if reason == "unit_unpriced":
            continue
        assert_rejected(
            model, record_na_doc(reason, unpriced_units=["output_tokens"]), field="unpriced_units"
        )
    assert_rejected(
        model, record_reported_doc(unpriced_units=["output_tokens"]), field="unpriced_units"
    )


# ------------------------------------------------------- usage_record_ids (N51, N67) and labels


def _imported_raw(reason: str) -> dict[str, Any]:
    if reason == "currency_not_reported":
        return {"reported_by": "provider", "amount_raw": "5", "currency_source": "none"}
    label = {"unsupported_currency": "EUR"}.get(reason, "USD")
    return reported_raw_doc(amount_raw="abc", currency_raw=label)


def test_cost_record_usage_record_ids_rules() -> None:
    """N51 remainder (b), N67 (ADR 0014 Decision 12; api-surface 23.6 groups R item 1 and Y):
    ``usage_record_ids`` has no repeats (rejected at ``usage_record_ids`` by pydantic and by the
    JSON Schema, ``uniqueItems: true``), on available and on not_available records. An available
    ``reported`` record may have ``[]`` (a source that reports a cost without usage); an
    available ``computed`` record with ``[]`` is rejected. Not-available records by reason
    (N67, decided): ``no_usage`` and ``usage_not_reported`` have no usage record (one is
    rejected, ``[]`` accepted); ``no_price_for_model``, ``unit_unpriced``,
    ``billing_rules_missing`` and ``ambiguous_price`` need at least one (``[]`` rejected); the four
    imported reasons accept ``[]`` and one. These by-reason rules cannot be written as plain JSON
    Schema keywords; their ``if``/``then`` form follows staff-reviewer's ruling on N12/N38 and is
    not pinned here. Every record that ``compute_cost`` returns for no usage (both domains) and
    for each computed reason, and that ``import_reported_cost`` returns with
    ``usage_record_ids=[]`` for each imported reason, validates again from its JSON."""
    model = _record()
    catalog = make_catalog()
    use = new_id("use")
    assert _make(record_reported_doc(usage_record_ids=[])).usage_record_ids == []
    assert_rejected(
        model, record_computed_doc(catalog, usage_record_ids=[]), field="usage_record_ids"
    )
    assert _make(record_computed_doc(catalog, usage_record_ids=[use])).usage_record_ids
    repeated = [use, use]
    for doc in (
        record_reported_doc(usage_record_ids=repeated),
        record_computed_doc(catalog, usage_record_ids=repeated),
        record_na_doc("no_price_for_model", usage_record_ids=repeated),
        record_na_doc("unsupported_currency", usage_record_ids=repeated),
    ):
        assert_rejected(model, doc, field="usage_record_ids")
        assert not schema_accepts(model, doc)
    assert schema_of(model)["properties"]["usage_record_ids"]["uniqueItems"] is True
    for reason in NA_REASONS:
        none_allowed = reason in (
            "no_usage",
            "usage_not_reported",
            "unsupported_currency",
            "currency_not_reported",
            "amount_unparseable",
            "currency_conflict",
        )
        some_allowed = reason not in ("no_usage", "usage_not_reported")
        doc = record_na_doc(reason)
        empty = {**doc, "usage_record_ids": []}
        one = {**doc, "usage_record_ids": [use]}
        if none_allowed:
            assert _make(empty).usage_record_ids == [], reason
        else:
            assert_rejected(model, empty, field="usage_record_ids")
        if some_allowed:
            assert [str(i) for i in _make(one).usage_record_ids] == [use], reason
        else:
            assert_rejected(model, one, field="usage_record_ids")
    for domain in ("evaluation", "agent_under_test"):
        record = cost_fn("compute_cost")(
            [],
            catalog,
            domain=domain,
            record_id=api("ids").CostRecordId.new(),
            created_at=when("2026-10-23T00:00:00Z"),
            context=ctx(),
        )
        assert record.usage_record_ids == []
        assert model.model_validate(record.model_dump(mode="json")) == record
    from ._support_b4 import catalog_of, compute, make_usage

    priced = make_usage(quantities=[qty("input_tokens", 5)])
    both = make_usage(quantities=[qty("input_tokens", 5), qty("output_tokens", 5)])
    dated = {"run_started_at": when("2026-10-20T00:00:00Z")}
    computed: list[Any] = [
        compute([priced], catalog_of({"model": "other"}), **dated),
        compute([both], catalog_of({"unit": "input_tokens"}), **dated),
        compute([priced], catalog_of({"billing": "unknown"}), **dated),
        compute([priced], make_catalog(), run_started_at=None),
    ]
    assert [r.na_reason for r in computed] == [
        "no_price_for_model",
        "unit_unpriced",
        "billing_rules_missing",
        "ambiguous_price",
    ]
    contract = cost_class("ContractCurrency")
    cited = contract("EUR", "https://docs.example.com/api", when("2026-10-01T00:00:00Z"))
    importer = cost_fn("import_reported_cost")
    for label, amount, with_contract in (
        ("EUR", "5", None),
        (None, "5", None),
        ("USD", "abc", None),
        ("USD", "5", cited),
    ):
        imported = importer(
            domain="evaluation",
            usage_record_ids=[],
            reported_by="provider",
            amount_raw=amount,
            currency_raw=label,
            record_id=api("ids").CostRecordId.new(),
            created_at=when("2026-10-23T00:00:00Z"),
            contract=with_contract,
        )
        computed.append(imported.record)
    assert [r.na_reason for r in computed[4:]] == [
        "unsupported_currency",
        "currency_not_reported",
        "amount_unparseable",
        "currency_conflict",
    ]
    for record in computed:
        assert model.model_validate(record.model_dump(mode="json")) == record, record.na_reason
        assert model.model_validate_json(record.model_dump_json()) == record


def test_stored_currency_label_consistent_with_reason() -> None:
    """N51 remainder (c) (ADR 0014 Decision 12 "stored currency labels", Decision 12c; api-surface
    23.6 group R item 2): a stored record agrees with the import steps after the P-52
    normalization. Each at ``reported_raw``, by pydantic: ``unsupported_currency`` with
    ``currency_source=payload`` and ``currency_raw`` ``"usd"``, ``" USD "`` or ``"USD"`` is
    rejected (the importer would have made it USD), and accepted with ``"EUR"``, ``"$"`` or
    "USD" followed by a no-break space (U+00A0 is not trimmed, so it is not USD);
    ``unsupported_currency`` with ``api_contract`` and ``"USD"`` is rejected;
    ``amount_unparseable`` with ``payload``: ``"EUR"`` rejected, ``"usd"`` accepted; with
    ``api_contract``: ``"EUR"`` rejected, ``"USD"`` accepted; ``currency_conflict`` with
    ``"EUR"`` rejected and ``" usd "`` accepted. JSON Schema cannot apply
    ``normalize_currency_label``: a code-only rule (handling per staff-reviewer's ruling on
    N12/N38)."""
    nbsp = chr(0xA0)

    def stored(reason: str, source: str, label: str) -> dict[str, Any]:
        raw = reported_raw_doc(amount_raw="12.50", currency_raw=label, currency_source=source)
        return record_na_doc(reason, reported_raw=raw)

    for label in ("usd", " USD ", "USD", "Usd", "\tUSD\n"):
        assert_rejected(
            _record(), stored("unsupported_currency", "payload", label), field="reported_raw"
        )
    for label in ("EUR", "$", "USD" + nbsp, nbsp + "USD", "US$", "dollars"):
        assert (
            _make(stored("unsupported_currency", "payload", label)).reported_raw.currency_raw
            == label
        )
    assert_rejected(
        _record(), stored("unsupported_currency", "api_contract", "USD"), field="reported_raw"
    )
    assert _make(stored("unsupported_currency", "api_contract", "EUR")).reported_raw is not None
    for source in ("payload", "api_contract"):
        assert_rejected(
            _record(), stored("amount_unparseable", source, "EUR"), field="reported_raw"
        )
        assert_rejected(_record(), stored("amount_unparseable", source, "$"), field="reported_raw")
        assert_rejected(
            _record(), stored("amount_unparseable", source, "USD" + nbsp), field="reported_raw"
        )
    assert _make(stored("amount_unparseable", "payload", "usd")).reported_raw.currency_raw == "usd"
    assert _make(stored("amount_unparseable", "payload", " USD ")).reported_raw is not None
    assert _make(stored("amount_unparseable", "api_contract", "USD")).reported_raw is not None
    assert_rejected(_record(), stored("currency_conflict", "payload", "EUR"), field="reported_raw")
    assert_rejected(_record(), stored("currency_conflict", "payload", "$"), field="reported_raw")
    assert_rejected(
        _record(), stored("currency_conflict", "payload", "USD" + nbsp), field="reported_raw"
    )
    for label in (" usd ", "USD", "usd"):
        assert (
            _make(stored("currency_conflict", "payload", label)).reported_raw.currency_raw == label
        )


def test_policy_documents_reported_cost_without_usage_limitation() -> None:
    """P1-20a, N51 remainder (a) (api-surface 23.6 group R item 3; red until SCHEMA_POLICY.md
    exists, like the two existing policy-document tests): SCHEMA_POLICY.md states that a reported
    cost without usage records shares no usage in summaries, so a double count with a computed
    record for the same call is not detected, and that importers must link usage whenever the
    source identifies it. Gate-bound."""
    candidates = [
        PACKAGE_DIR / "SCHEMA_POLICY.md",
        ROOT / "SCHEMA_POLICY.md",
        ROOT / "docs" / "SCHEMA_POLICY.md",
    ]
    text = ""
    for path in candidates:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            break
    else:
        pytest.fail(
            "SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of "
            + ", ".join(str(c) for c in candidates)
        )
    flat = re.sub(r"\s+", " ", text.lower())
    assert re.search(r"reported cost[^.]*without usage", flat)
    assert re.search(r"(shares no usage|no usage record in common|shares nothing)", flat)
    assert re.search(r"(double count|counted twice|counted two times)", flat)
    assert re.search(r"(not detected|cannot be detected|is not found|undetected)", flat)
    assert "computed" in flat
    assert re.search(r"importers? (must|should) link", flat)
    assert re.search(r"(whenever|when) the source (identifies|names|gives)", flat)


# ------------------------------------------------------------------------- no-echo (N60)


def test_usage_and_cost_errors_do_not_quote_rejected_values() -> None:
    """N60 (owner, option A; ADR 0007 Decision 8 (a), (b), (c); api-surface 23.6 group I item 2):
    ``UsageRecord``, ``ReportedCostRaw`` and ``CostRecord`` hide their input and name the rule and
    the location only. A marker placed in each rejectable field (a wrong-kind ``run_id`` or
    usage id, a bad ``provider`` key, a fractional token count, a bad closed-set member, an
    ``occurred_at`` that is no instant, a bad ``na_reason``, a non-USD ``amount.currency``, a bad
    ``currency_source``, an unknown field) never appears in ``str(error)``, ``repr``,
    ``errors(include_input=False)`` or ``json(include_input=False)``, through ``model_validate``
    and ``model_validate_json``, validated alone and embedded (``ReportedCostRaw`` inside
    ``CostRecord``, ``UsageQuantity`` inside ``UsageRecord``). The currency message inside
    ``CostRecord`` names the rule, not ``INR`` or ``Indian Rupee``."""
    registered = {m.__name__: m for m in api("registry").model_classes()}
    usage, raw, record = (
        registered["UsageRecord"],
        registered["ReportedCostRaw"],
        registered["CostRecord"],
    )
    for model in (usage, raw, record):
        assert model.model_config.get("hide_input_in_errors") is True, model.__name__
    mark = QUOTE_MARK
    catalog = make_catalog()
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("run_id", usage, usage_doc(run_id=marked_id("run")), "run_id"),
        ("provider", usage, usage_doc(provider=marked_key()), "provider"),
        ("model", usage, usage_doc(model=marked_key()), "model"),
        ("domain", usage, usage_doc(domain=marked_set()), "domain"),
        ("stage", usage, usage_doc(stage=marked_set()), "stage"),
        ("source", usage, usage_doc(source=marked_set()), "source"),
        ("unit", usage, usage_doc(quantities=[qty(marked_set(), 1)]), "unit"),
        (
            "fractional count",
            usage,
            usage_doc(quantities=[qty("input_tokens", "1.5" + mark)]),
            "count",
        ),
        ("seconds count", usage, usage_doc(quantities=[qty("audio_input_seconds", mark)]), "count"),
        ("occurred_at", usage, usage_doc(occurred_at=mark), "occurred_at"),
        ("call ref", usage, usage_doc(provider_call_ref=mark), "provider_call_ref"),
        ("unknown field", usage, {**usage_doc(), "unknown_field": mark}, "unknown_field"),
        ("reported_by", raw, reported_raw_doc(reported_by=marked_set()), "reported_by"),
        ("currency_source", raw, reported_raw_doc(currency_source=marked_set()), "currency_source"),
        ("amount_raw", raw, reported_raw_doc(amount_raw=7), "amount_raw"),
        ("unknown raw field", raw, {**reported_raw_doc(), "unknown_field": mark}, "unknown_field"),
        (
            "usage id",
            record,
            record_reported_doc(usage_record_ids=[marked_id("use")]),
            "usage_record_ids",
        ),
        ("record domain", record, record_reported_doc(domain=marked_set()), "domain"),
        ("status", record, record_reported_doc(status=marked_set()), "status"),
        ("basis", record, record_reported_doc(basis=marked_set()), "basis"),
        (
            "reported_by of record",
            record,
            record_reported_doc(reported_by=marked_set()),
            "reported_by",
        ),
        ("na_reason", record, record_na_doc(na_reason=marked_set()), "na_reason"),
        (
            "unpriced unit",
            record,
            record_na_doc("unit_unpriced", unpriced_units=[marked_set()]),
            "unpriced_units",
        ),
        (
            "amount",
            record,
            record_reported_doc(amount={"amount": mark, "currency": "USD"}),
            "amount",
        ),
        (
            "embedded currency_source",
            record,
            record_na_doc(
                "unsupported_currency", reported_raw=reported_raw_doc(currency_source=marked_set())
            ),
            "currency_source",
        ),
        (
            "snapshot key",
            record,
            record_computed_doc(catalog, pricing_snapshot=snapshot_doc(catalog, [marked_key()])),
            "entry_keys",
        ),
        (
            "snapshot catalog id",
            record,
            record_computed_doc(
                catalog, pricing_snapshot=snapshot_doc(catalog, catalog_id=marked_id("pcat"))
            ),
            "catalog_id",
        ),
        (
            "unknown record field",
            record,
            {**record_reported_doc(), "unknown_field": mark},
            "unknown_field",
        ),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, mark, field=field, label=label)
    inr = {"amount": "1", "currency": "INR"}
    for secret in ("INR", "Indian Rupee"):
        assert_input_hidden(
            record, record_reported_doc(amount=inr), secret, field="amount", label="currency"
        )
