# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-10: the cost of our STT through the evalschema pricing functions (contract
docs/phase3/api-surface-batch-b.md section 8; ADR 0024 rule 14; ADR 0014).

Without a user USD catalog the cost is ``not_available``; with one it is computed by
``compute_cost``; an INR price is never converted. The prices in these tests are made up for the
tests (a round number of USD per 60 seconds): they are not Sarvam's prices.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    NOTE,
    SRC,
    TWO,
    UTC_NOW,
    catalog,
    catalog_text,
    cost_mod,
    entry,
    usages,
)
from tracer_evalschema.cost import check_cost_summary
from tracer_evalschema.pricing import PricingCatalog

# -------------------------------------------------------------------------- no catalog


def test_cost_not_available_without_usd_catalog() -> None:
    use = usages(TWO)
    result = cost_mod().compute_stt_cost(use, catalog=None, created_at=UTC_NOW)
    assert len(result.records) == 2
    for record, usage in zip(result.records, use, strict=True):
        assert record.domain == "evaluation"
        assert record.status == "not_available"
        assert record.na_reason == "no_price_for_model"
        assert record.usage_record_ids == [usage.id], "its usage record is listed"
        assert record.amount is None
        assert record.basis is None
        assert record.pricing_snapshot is None
        assert record.rounding is None
        assert record.created_at == UTC_NOW
    summary = result.summary
    assert summary.domain == "evaluation"
    assert summary.total is None
    assert summary.included == []
    assert summary.completeness == "none"
    assert [(e.ref, e.reason) for e in summary.excluded] == [
        (r.id, "no_price_for_model") for r in result.records
    ]
    check_cost_summary(summary, list(result.records))


def test_cost_not_available_without_usd_catalog_and_no_usage_at_all() -> None:
    result = cost_mod().compute_stt_cost([], catalog=None, created_at=UTC_NOW)
    (record,) = result.records
    assert record.status == "not_available"
    assert record.na_reason == "no_usage"
    assert record.usage_record_ids == []
    assert result.summary.total is None
    with_catalog = cost_mod().compute_stt_cost([], catalog=catalog(), created_at=UTC_NOW)
    assert with_catalog.records[0].na_reason == "no_usage"
    assert with_catalog.summary.total is None


# ----------------------------------------------------------------------- user catalog


def test_cost_computed_with_user_usd_catalog() -> None:
    cat = catalog()
    use = usages(TWO)
    result = cost_mod().compute_stt_cost(use, catalog=cat, created_at=UTC_NOW)
    amounts = []
    for record, usage in zip(result.records, use, strict=True):
        assert record.status == "available"
        assert record.basis == "computed"
        assert record.amount is not None
        assert record.amount.currency == "USD"
        assert record.usage_record_ids == [usage.id]
        assert record.pricing_snapshot is not None
        assert record.pricing_snapshot.catalog_id == cat.id
        assert record.pricing_snapshot.content_digest == cat.content_digest
        assert record.pricing_snapshot.entry_keys == [
            "sarvam-audio_input_seconds",
            "sarvam-requests",
        ]
        assert record.rounding is None
        assert record.na_reason is None
        amounts.append(record.amount.amount)
    # 25 s and 12.5 s at 0.006 USD per 60 s: exactly 0.0025 and 0.00125
    assert amounts == ["0.0025", "0.00125"]
    summary = result.summary
    assert summary.total is not None
    assert summary.total.amount == "0.00375"
    assert summary.total.currency == "USD"
    assert summary.included == [r.id for r in result.records]
    assert summary.excluded == []
    assert summary.completeness == "complete"
    check_cost_summary(summary, list(result.records))


def test_cost_computed_with_user_usd_catalog_rounds_once_and_says_so() -> None:
    use = usages([(0, 1)])  # one frame: 1/24000 s, stated as a rounded 18-digit decimal
    seconds = use[0].quantities[0].count
    assert seconds == "0.000041666666666667"
    (record,) = cost_mod().compute_stt_cost(use, catalog=catalog(), created_at=UTC_NOW).records
    with localcontext() as ctx:
        ctx.prec = 100
        exact = Decimal(seconds) * Decimal("0.006") / Decimal(60)
        expected = exact.quantize(Decimal(1).scaleb(-18), rounding=ROUND_HALF_EVEN)
    text = format(expected, "f").rstrip("0").rstrip(".")
    assert record.amount.amount == text
    assert (record.rounding is not None) is (expected != exact)
    if record.rounding is not None:
        assert (record.rounding.scale, record.rounding.mode, record.rounding.exact) == (
            18,
            "half_even",
            False,
        )


def test_cost_computed_with_user_usd_catalog_prices_by_the_date_of_the_usage() -> None:
    old = entry(
        "audio_input_seconds",
        "60",
        "0.006",
        entry_key="old",
        effective_to="2026-06-01T00:00:00.000000Z",
    )
    new = entry(
        "audio_input_seconds",
        "60",
        "0.012",
        entry_key="new",
        effective_from="2026-06-01T00:00:00.000000Z",
    )
    cat = catalog([old, new, entry("requests", "1", "0")])
    (record,) = (
        cost_mod().compute_stt_cost(usages([(0, 600_000)]), catalog=cat, created_at=UTC_NOW).records
    )
    assert record.amount.amount == "0.005", "25 s at 0.012 USD per 60 s"
    assert record.pricing_snapshot.entry_keys == ["new", "sarvam-requests"]


def test_cost_with_a_price_only_for_audio_is_not_a_partial_sum() -> None:
    cat = catalog([entry("audio_input_seconds", "60", "0.006")])
    result = cost_mod().compute_stt_cost(usages(TWO), catalog=cat, created_at=UTC_NOW)
    for record in result.records:
        assert record.status == "not_available"
        assert record.na_reason == "unit_unpriced"
        assert record.unpriced_units == ["requests"]
        assert record.amount is None
    assert result.summary.total is None
    assert result.summary.completeness == "none"


def test_cost_with_unknown_billing_rules_is_billing_rules_missing() -> None:
    cat = catalog(
        [
            entry("audio_input_seconds", "60", "0.006", billing="unknown"),
            entry("requests", "1", "0"),
        ]
    )
    result = cost_mod().compute_stt_cost(usages(TWO), catalog=cat, created_at=UTC_NOW)
    assert {r.na_reason for r in result.records} == {"billing_rules_missing"}
    assert all(r.amount is None for r in result.records)
    assert result.summary.total is None


def test_cost_without_a_price_date_is_ambiguous_not_guessed() -> None:
    use = usages(TWO, at=None)
    assert all(u.occurred_at is None for u in use)
    result = cost_mod().compute_stt_cost(use, catalog=catalog(), created_at=UTC_NOW)
    assert {r.na_reason for r in result.records} == {"ambiguous_price"}
    assert result.summary.total is None
    dated = cost_mod().compute_stt_cost(
        use,
        catalog=catalog(),
        created_at=UTC_NOW,
        run_started_at=datetime(2026, 10, 24, tzinfo=UTC),
    )
    assert all(r.status == "available" for r in dated.records)
    assert dated.summary.total.amount == "0.00375"


@pytest.mark.parametrize("model", ["saaras:v3", "saaras:v4-extra", "other"])
def test_cost_for_another_model_is_no_price_for_model(model: str) -> None:
    result = cost_mod().compute_stt_cost(
        usages(TWO, model=model), catalog=catalog(), created_at=UTC_NOW
    )
    assert {r.na_reason for r in result.records} == {"no_price_for_model"}
    assert result.summary.total is None


def test_cost_never_zero_when_unpriced() -> None:
    scenarios: list[tuple[str, Any, Any]] = [
        ("no catalog", None, usages(TWO)),
        ("no usage", catalog(), []),
        (
            "billing unknown",
            catalog(
                [
                    entry("audio_input_seconds", "60", "0.006", billing="unknown"),
                    entry("requests", "1", "0"),
                ]
            ),
            usages(TWO),
        ),
        ("only audio priced", catalog([entry("audio_input_seconds", "60", "0.006")]), usages(TWO)),
        ("another model", catalog(), usages(TWO, model="saaras:v3")),
        ("no date", catalog(), usages(TWO, at=None)),
        (
            "price not yet effective",
            catalog(
                [
                    entry(
                        "audio_input_seconds",
                        "60",
                        "0.006",
                        effective_from="2027-01-01T00:00:00.000000Z",
                    ),
                    entry("requests", "1", "0"),
                ]
            ),
            usages(TWO),
        ),
    ]
    for label, cat, use in scenarios:
        result = cost_mod().compute_stt_cost(use, catalog=cat, created_at=UTC_NOW)
        assert result.records, label
        for record in result.records:
            assert record.status == "not_available", label
            assert record.amount is None, label
            assert record.basis is None, label
        assert result.summary.total is None, label
        assert result.summary.included == [], label
        assert result.summary.completeness == "none", label
        text = json.dumps(
            [r.model_dump(mode="json") for r in result.records]
            + [result.summary.model_dump(mode="json")]
        )
        assert '"amount": "0"' not in text, label
        assert '"total": {' not in text, label


def test_cost_a_price_of_zero_is_only_ever_the_users_statement() -> None:
    cat = catalog(
        [
            entry("audio_input_seconds", "60", "0", price_source="user_supplied"),
            entry("requests", "1", "0"),
        ]
    )
    (record,) = (
        cost_mod().compute_stt_cost(usages([(0, 600_000)]), catalog=cat, created_at=UTC_NOW).records
    )
    assert record.status == "available"
    assert record.amount.amount == "0"
    assert record.basis == "computed"


# ------------------------------------------------------------------------ INR, files


def test_inr_price_never_converted() -> None:
    inr = entry("audio_input_seconds", "60", "0.49", price={"amount": "0.49", "currency": "INR"})
    with pytest.raises(cost_mod().CatalogRefusedError) as raised:
        cost_mod().load_user_catalog(
            catalog_text([inr, entry("requests", "1", "0")]), created_at=UTC_NOW
        )
    assert raised.value.reason == "catalog_entry_invalid"
    assert "0.49" not in str(raised.value)
    assert "INR" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    # a user who converts by hand says so in the note; Tracer takes the USD number as written
    converted = entry(
        "audio_input_seconds",
        "60",
        "0.006",
        provenance={
            "kind": "user_note",
            "retrieved_at": "2026-10-25T00:00:00.000000Z",
            "note": "0.49 INR per minute converted by the owner at 81.7 INR per USD on 2026-10-25",
        },
    )
    cat = catalog([converted, entry("requests", "1", "0")])
    (record, *_) = (
        cost_mod().compute_stt_cost(usages([(0, 600_000)]), catalog=cat, created_at=UTC_NOW).records
    )
    assert record.amount.amount == "0.0025", "the owner's number, not a number Tracer derived"
    # no entry point takes an exchange rate or a currency, and no source file holds an INR price
    for target in (
        cost_mod().compute_stt_cost,
        cost_mod().load_user_catalog,
        __import__("tracer_evalproviders.dryrun", fromlist=["x"]).build_dry_run_plan,
    ):
        names = set(inspect.signature(target).parameters)
        assert not names & {
            "fx",
            "rate",
            "inr",
            "currency",
            "usd_per_inr",
            "exchange_rate",
            "fx_rate",
        }, names
    offences = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and id(node) not in docstrings:
                value = str(node.value)
                if "INR" in value.upper().split() or "0.49" in value or "rupee" in value.lower():
                    offences.append(f"{path.name}:{node.lineno}: {value!r}")
            if isinstance(node, ast.Name) and node.id.lower() in {"fx", "inr", "usd_per_inr"}:
                offences.append(f"{path.name}:{node.lineno}: {node.id}")
    assert not offences, offences


BAD_CATALOGS: list[tuple[str, str, str]] = [
    ("not json", "{", "catalog_not_json"),
    ("empty", "", "catalog_not_json"),
    ("nan", '{"published_at": "2026-10-01T00:00:00.000000Z", "entries": NaN}', "catalog_not_json"),
    ("a list", "[]", "catalog_shape"),
    ("no entries member", '{"published_at": "2026-10-01T00:00:00.000000Z"}', "catalog_shape"),
    ("empty entries", catalog_text([]), "catalog_shape"),
    (
        "entries not a list",
        catalog_text().replace('"entries": [', '"entries": {"a": [').replace("}]}", "}]}}"),
        "catalog_shape",
    ),
    ("extra top-level member", catalog_text(content_digest="x"), "catalog_shape"),
    (
        "an id of its own",
        catalog_text(id="pcat_00000000-0000-4000-8000-000000000000"),
        "catalog_shape",
    ),
    (
        "INR entry",
        catalog_text(
            [entry("audio_input_seconds", "60", "1", price={"amount": "1", "currency": "INR"})]
        ),
        "catalog_entry_invalid",
    ),
    (
        "EUR entry",
        catalog_text(
            [entry("audio_input_seconds", "60", "1", price={"amount": "1", "currency": "EUR"})]
        ),
        "catalog_entry_invalid",
    ),
    (
        "negative price",
        catalog_text([entry("audio_input_seconds", "60", "-1")]),
        "catalog_entry_invalid",
    ),
    (
        "no note",
        catalog_text(
            [
                entry(
                    "audio_input_seconds",
                    "60",
                    "1",
                    provenance={"kind": "user_note", "retrieved_at": "2026-10-25T00:00:00.000000Z"},
                )
            ]
        ),
        "catalog_entry_invalid",
    ),
    (
        "blank note",
        catalog_text(
            [
                entry(
                    "audio_input_seconds",
                    "60",
                    "1",
                    provenance={
                        "kind": "user_note",
                        "retrieved_at": "2026-10-25T00:00:00.000000Z",
                        "note": "  ",
                    },
                )
            ]
        ),
        "catalog_entry_invalid",
    ),
    ("unknown unit", catalog_text([entry("minutes", "60", "1")]), "catalog_entry_invalid"),
    (
        "a float price",
        catalog_text(
            [entry("audio_input_seconds", "60", "1", price={"amount": 1.5, "currency": "USD"})]
        ),
        "catalog_entry_invalid",
    ),
    ("an entry that is not an object", catalog_text(["x"]), "catalog_entry_invalid"),
    (
        "shipped price source",
        catalog_text([entry("audio_input_seconds", "60", "1", price_source="catalog")]),
        "catalog_entry_not_user_supplied",
    ),
    (
        "duplicate entry key",
        catalog_text(
            [
                entry("audio_input_seconds", "60", "1", entry_key="k"),
                entry("requests", "1", "0", entry_key="k"),
            ]
        ),
        "catalog_invalid",
    ),
    (
        "overlapping ranges",
        catalog_text(
            [
                entry("audio_input_seconds", "60", "1", entry_key="a"),
                entry("audio_input_seconds", "60", "2", entry_key="b"),
            ]
        ),
        "catalog_invalid",
    ),
    (
        "bad published_at",
        catalog_text().replace("2026-10-01T00:00:00.000000Z", "yesterday"),
        "catalog_invalid",
    ),
]


@pytest.mark.parametrize(
    ("label", "text", "reason"), BAD_CATALOGS, ids=[b[0] for b in BAD_CATALOGS]
)
def test_user_catalog_file_is_refused_with_a_short_code(label: str, text: str, reason: str) -> None:
    with pytest.raises(cost_mod().CatalogRefusedError) as raised:
        cost_mod().load_user_catalog(text, created_at=UTC_NOW)
    assert raised.value.reason == reason, label
    assert isinstance(raised.value, ValueError)
    assert str(raised.value) == reason, "the message is the code: nothing of the file"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_user_catalog_file_is_sealed_into_a_valid_catalog() -> None:
    cat = catalog()
    assert isinstance(cat, PricingCatalog)
    assert cat.version == 1
    assert cat.created_at == UTC_NOW
    assert [e.entry_key for e in cat.entries] == ["sarvam-audio_input_seconds", "sarvam-requests"]
    assert all(e.price_source == "user_supplied" and e.provenance.note == NOTE for e in cat.entries)
    again = PricingCatalog.model_validate(cat.model_dump(mode="json"))
    assert again == cat, "the content digest verifies on load"
    assert str(cat.id).startswith("pcat_")
    assert catalog().id != cat.id, "every load is a new catalog id"


@pytest.mark.parametrize(
    ("written", "instant"),
    [
        ("2026-10-01T00:00:00.000000Z", datetime(2026, 10, 1, tzinfo=UTC)),
        ("2026-10-01T00:00:00Z", datetime(2026, 10, 1, tzinfo=UTC)),
        ("2026-10-01T05:30:00+05:30", datetime(2026, 10, 1, tzinfo=UTC)),
    ],
)
def test_user_catalog_file_reads_published_at_as_the_schema_reads_an_instant(
    written: str, instant: datetime
) -> None:
    text = catalog_text().replace("2026-10-01T00:00:00.000000Z", written, 1)
    cat = cost_mod().load_user_catalog(text, created_at=UTC_NOW)
    assert cat.published_at == instant
    assert PricingCatalog.model_validate(cat.model_dump(mode="json")) == cat
