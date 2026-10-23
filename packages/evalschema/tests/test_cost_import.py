# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-25, part 4: the importer helper for reported costs (ADR 0014 Decisions 1a, 12, 12a, 12b;
P-52, P-53).

Assumed public API (docs/phase1/api-surface.md section 23.1), ``tracer_evalschema.cost``:

* ``normalize_currency_label(label) -> str | None``: ``"USD"`` if the label equals ``USD`` after
  trimming ASCII white space and ASCII case folding, else ``None``;
* ``ContractCurrency`` (``NamedTuple``: ``currency``, ``url``, ``retrieved_at``): the currency that
  the source's published API contract states for a cost field, with the citation of the field
  provenance table (P-51, ADR 0018);
* ``import_reported_cost(*, domain, usage_record_ids, reported_by, amount_raw, currency_raw,
  record_id, created_at, contract=None, currency_source_path=None, amount_source_path=None) ->
  ImportedCost`` (``NamedTuple``: ``record``, ``warnings``), where ``currency_raw`` is the label as
  sent or ``None`` when the payload carries none. Revision 17 (N69, D19): the two keywords locate
  the currency field and the amount field in the source; each warning carries the path of the
  field it is about, absent when not passed, never reconstructed; the single ``source_path`` of
  revision 16 is withdrawn (passing it is a ``TypeError``). The warnings are ``IngestWarning``
  objects (batch 3); the tests read only ``code``, ``raw``, ``path`` and ``message``, so only the
  implementation of the helper depends on batch 3.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

from typing import Any

import pytest

from ._support import api, data_json
from ._support_b2 import registered_class
from ._support_b4 import cost_class, cost_fn, when


def _import(**over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "domain": "evaluation",
        "usage_record_ids": [api("ids").UsageRecordId.new()],
        "reported_by": "provider",
        "amount_raw": "1000",
        "currency_raw": "USD",
        "record_id": api("ids").CostRecordId.new(),
        "created_at": when("2026-10-23T00:00:00Z"),
    }
    kwargs.update(over)
    return cost_fn("import_reported_cost")(**kwargs)


def _codes(imported: Any) -> list[str]:
    return [w.code for w in imported.warnings]


def _warning(imported: Any, code: str) -> Any:
    found = [w for w in imported.warnings if w.code == code]
    assert len(found) == 1, (code, _codes(imported))
    return found[0]


def _not_available(record: Any, reason: str) -> None:
    assert record.status == "not_available"
    assert record.na_reason == reason
    assert record.amount is None
    assert record.basis is None
    assert record.reported_by is None
    assert record.pricing_snapshot is None
    assert record.rounding is None


# ----------------------------------------------------------------------------- labels


@pytest.mark.parametrize(
    "label",
    ["USD", "usd", "Usd", "uSD", " USD ", "\tusd\n", "USD\r\n", " usd", "usd  ", "\n\nUSD"],
)
def test_normalize_currency_label_accepts_usd_variants(label: str) -> None:
    """Proposed name (ADR 0014 Decision 12a, P-52): a label equal to ``USD`` after trimming
    surrounding ASCII white space and ASCII case folding is USD."""
    assert cost_fn("normalize_currency_label")(label) == "USD"


@pytest.mark.parametrize(
    "label",
    [
        "$",
        "US$",
        "dollars",
        "Dollars",
        "USD$",
        "US dollar",
        "usd.",
        "USDT",
        "U S D",
        "US",
        "",
        " ",
        "EUR",
        "inr",
        "XXX",
        "ABC",
        "USD" + chr(0x200B),
        chr(0xFF35) + chr(0xFF33) + chr(0xFF24),
        "u" + chr(0x017F) + "d",
        "USD" + chr(0x0307),
        "US" + chr(0x0044) + chr(0x0000),
        chr(0x20B9),
    ],
)
def test_normalize_currency_label_rejects_everything_else(label: str) -> None:
    """Proposed name (ADR 0014 Decision 12a, P-52): symbols and other spellings are never mapped,
    because ``$`` is used by many currencies. Case folding is ASCII only: the full-width letters,
    the long s (which Unicode folds to s), a zero-width space and a combining mark do not make a
    label USD, and neither does an embedded NUL."""
    assert cost_fn("normalize_currency_label")(label) is None


def test_reported_currency_case_variant_normalized_with_warning() -> None:
    """ADR 0014 Decision 12a, I25, P-52: the importer (not the type) maps "usd" and " USD " to
    USD and records one ingest warning ``currency_label_normalized`` whose ``raw`` holds the label
    exactly as sent (white space included). The cost is then processed as USD: an available,
    reported record with the amount and the reporter. An exact "USD" gives no warning. The
    ``Money`` type itself still rejects "usd"."""
    for label in ("usd", " USD ", "Usd", "\tUSD\n"):
        imported = _import(currency_raw=label, amount_raw="1000")
        record = imported.record
        assert isinstance(record, cost_class("CostRecord"))
        assert record.status == "available"
        assert record.basis == "reported"
        assert record.reported_by == "provider"
        assert record.reported_raw is None
        assert record.amount.amount == "1000"
        assert record.amount.currency == "USD"
        warning = _warning(imported, "currency_label_normalized")
        assert warning.raw == label
        assert _codes(imported) == ["currency_label_normalized"]
    exact = _import(currency_raw="USD")
    assert exact.warnings == []
    assert exact.record.status == "available"
    with pytest.raises(Exception, match="currency"):
        api("primitives").Money(amount="1", currency="usd")


def test_reported_currency_symbol_not_normalized() -> None:
    """ADR 0014 Decision 12a, I25, P-52: "$", "US$" and other spellings ("dollars") are never
    mapped to USD. The cost is not_available with ``unsupported_currency``; ``reported_raw``
    keeps the amount lexeme and the label verbatim with ``currency_source=payload``; there is no
    ``currency_label_normalized`` warning, and an ``unsupported_currency`` warning is recorded."""
    for label in ("$", "US$", "dollars", "Dollars", "USD$", "usd.", "Rs", chr(0x20B9)):
        imported = _import(currency_raw=label, amount_raw="12.50")
        record = imported.record
        _not_available(record, "unsupported_currency")
        assert record.reported_raw.amount_raw == "12.50"
        assert record.reported_raw.currency_raw == label
        assert record.reported_raw.currency_source == "payload"
        assert record.reported_raw.reported_by == "provider"
        assert "currency_label_normalized" not in _codes(imported)
        assert "unsupported_currency" in _codes(imported)
        assert "decimal_normalized" not in _codes(imported)


def test_import_non_usd_not_converted_parsed_or_rejected() -> None:
    """Proposed name (ADR 0014 Decision 12, I7b): a reported cost in another currency is never
    rejected, converted or parsed: a record is returned for every ISO code and for text that is
    no ISO code, the amount lexeme is kept as sent (also "1,234.50", "1e3" and white space), the
    reporter is kept in ``reported_raw``, and the ``unsupported_currency`` warning names the code
    when the ISO 4217 data file knows it (and still exists when it does not)."""
    rows = {row["code"]: row for row in data_json("iso4217_active.json")["currencies"]}
    assert "INR" in rows
    assert "ABC" not in rows
    for by in ("provider", "telemetry_platform"):
        for amount in ("12.50", "1,234.50", "1e3", " 7 ", "0.10"):
            imported = _import(currency_raw="INR", amount_raw=amount, reported_by=by)
            _not_available(imported.record, "unsupported_currency")
            assert imported.record.reported_raw.amount_raw == amount
            assert imported.record.reported_raw.reported_by == by
            assert imported.record.reported_raw.currency_raw == "INR"
            assert "INR" in _warning(imported, "unsupported_currency").message
    for code in sorted(rows):
        if code == "USD":
            continue
        imported = _import(currency_raw=code, amount_raw="12.50")
        _not_available(imported.record, "unsupported_currency")
        assert imported.record.reported_raw.currency_raw == code
        assert code in _warning(imported, "unsupported_currency").message
    unknown = _import(currency_raw="ABC", amount_raw="3")
    _not_available(unknown.record, "unsupported_currency")
    assert unknown.record.reported_raw.currency_raw == "ABC"
    assert "unsupported_currency" in _codes(unknown)


def test_import_usd_amount_lexeme_normalized_with_warning() -> None:
    """Proposed name (ADR 0014 Decision 1a): a USD amount lexeme that is a valid decimal in
    another spelling is converted to the canonical spelling by the importer, with an ingest warning
    ``decimal_normalized`` whose ``raw`` holds the lexeme as sent: "12.50" becomes "12.5", "7."
    becomes "7", "0.0" becomes "0". A canonical lexeme gives no warning. Only the spelling
    changes, never the value."""
    for lexeme, canonical in (("12.50", "12.5"), ("7.", "7"), ("0.0", "0"), ("1000.000", "1000")):
        imported = _import(amount_raw=lexeme)
        assert imported.record.status == "available"
        assert imported.record.amount.amount == canonical
        assert _warning(imported, "decimal_normalized").raw == lexeme
    for lexeme in ("12.5", "0", "1000", "0.000000000000000001"):
        imported = _import(amount_raw=lexeme)
        assert imported.record.amount.amount == lexeme
        assert "decimal_normalized" not in _codes(imported)
    both = _import(currency_raw=" usd ", amount_raw="12.50")
    assert sorted(_codes(both)) == ["currency_label_normalized", "decimal_normalized"]
    assert both.record.amount.amount == "12.5"
    ids = [api("ids").UsageRecordId.new(), api("ids").UsageRecordId.new()]
    kept = _import(
        usage_record_ids=ids, reported_by="telemetry_platform", domain="agent_under_test"
    )
    assert kept.record.usage_record_ids == ids
    assert kept.record.reported_by == "telemetry_platform"
    assert kept.record.domain == "agent_under_test"


# ------------------------------------------------------------------ no currency, contract


def test_reported_cost_without_currency_not_available() -> None:
    """ADR 0014 Decision 12b, I26, P-53: a reported cost that carries no currency is not_available
    with ``currency_not_reported`` (USD is never assumed): no amount, no basis; ``reported_raw``
    holds the amount lexeme verbatim, no ``currency_raw`` and ``currency_source=none``, and the
    reporter. Nothing is parsed or normalized. Revision 15, N59 (api-surface 23.6 group H item 9):
    the import always records one ``currency_not_reported`` warning, and no other warning (no
    currency, no parsing)."""
    for amount in ("42.50", "0", "1000", "1e3", " 7 ", "12.50"):
        for by in ("provider", "telemetry_platform"):
            imported = _import(currency_raw=None, amount_raw=amount, reported_by=by)
            record = imported.record
            _not_available(record, "currency_not_reported")
            assert record.reported_raw.amount_raw == amount
            assert record.reported_raw.currency_raw is None
            assert record.reported_raw.currency_source == "none"
            assert record.reported_raw.reported_by == by
            assert "decimal_normalized" not in _codes(imported)
            assert "currency_label_normalized" not in _codes(imported)
            assert _codes(imported) == ["currency_not_reported"]
    imported = _import(currency_raw=None, amount_raw="5")
    again = type(imported.record).model_validate_json(imported.record.model_dump_json())
    assert again == imported.record


def test_documented_currency_contract_requires_citation() -> None:
    """ADR 0014 Decision 12b, I26, P-53: when the source's published API contract states the
    currency of the field and the field provenance table cites it (URL and ``retrieved_at``), that
    currency is used. A USD contract gives an available reported record; any other currency gives
    ``unsupported_currency`` with ``currency_raw`` holding the code as written in the contract and
    ``currency_source=api_contract``. A contract without a URL, without ``retrieved_at`` or with an
    empty URL is not a citation and is treated as no currency: ``currency_not_reported`` with
    ``currency_source=none``, also when the contract says USD. Without any contract the result is
    the same."""
    contract = cost_class("ContractCurrency")
    url = "https://docs.example.com/api/generations#cost"
    retrieved = when("2026-10-01T00:00:00Z")
    cited_usd = _import(currency_raw=None, amount_raw="5", contract=contract("USD", url, retrieved))
    assert cited_usd.record.status == "available"
    assert cited_usd.record.basis == "reported"
    assert cited_usd.record.amount.amount == "5"
    assert cited_usd.record.reported_raw is None
    cited_inr = _import(currency_raw=None, amount_raw="5", contract=contract("INR", url, retrieved))
    _not_available(cited_inr.record, "unsupported_currency")
    assert cited_inr.record.reported_raw.currency_raw == "INR"
    assert cited_inr.record.reported_raw.currency_source == "api_contract"
    assert cited_inr.record.reported_raw.amount_raw == "5"
    for uncited in (
        contract("USD", None, retrieved),
        contract("USD", "", retrieved),
        contract("USD", url, None),
        contract("USD", None, None),
        contract("INR", None, retrieved),
        None,
    ):
        imported = _import(currency_raw=None, amount_raw="5", contract=uncited)
        _not_available(imported.record, "currency_not_reported")
        assert imported.record.reported_raw.currency_source == "none"
        assert imported.record.reported_raw.currency_raw is None
    assert tuple(contract._fields) == ("currency", "url", "retrieved_at")


# ---------------------------------------------------------------- N59 (revision 15 and 16)

CITATION = "https://docs.example.com/api/generations#cost"
RETRIEVED = when("2026-10-01T00:00:00Z")


def _contract(code: str) -> Any:
    return cost_class("ContractCurrency")(code, CITATION, RETRIEVED)


def _triples(imported: Any) -> list[tuple[str, str | None, str | None]]:
    """The warnings as a multiset of ``(code, raw, path)`` (N75: the order of the warnings of one
    import is not pinned yet)."""
    return sorted(((w.code, w.raw, w.path) for w in imported.warnings), key=repr)


def _revalidates(imported: Any) -> None:
    record = imported.record
    assert type(record).model_validate(record.model_dump(mode="json")) == record
    assert type(record).model_validate_json(record.model_dump_json()) == record


def test_reported_amount_unparseable_not_available_raw_kept() -> None:
    """N59 (owner, option A; ADR 0014 Decisions 1a and 12c step 4, detail D7; api-surface 23.6
    group H item 1): a reported amount that is not a valid decimal after lexical normalization
    (``"1e3"``, ``"1,000"``, ``"abc"``, ``""``, ``"-5"``, and by D7 ``"+1"``, ``"-0"``,
    ``" 12.5"``, ``"012.5"``) is not_available with ``na_reason=amount_unparseable``: no amount,
    no basis; ``reported_raw`` holds the amount and the currency label verbatim with
    ``currency_source`` payload (api_contract when the currency comes from a cited contract);
    exactly one ``amount_unparseable`` warning whose ``raw`` is the lexeme; nothing is raised and
    the record validates again from its JSON. The label ``USD`` gives no other warning. The
    amount is parsed only once the currency is USD (D7): ``"abc"`` with ``EUR`` is
    ``unsupported_currency`` and with no label ``currency_not_reported``."""
    amounts = ("1e3", "1,000", "abc", "", "-5", "+1", "-0", " 12.5", "012.5", "1_000", ".5")
    for amount in amounts:
        for label in ("USD", "usd"):
            imported = _import(currency_raw=label, amount_raw=amount)
            _not_available(imported.record, "amount_unparseable")
            raw = imported.record.reported_raw
            assert (raw.amount_raw, raw.currency_raw, raw.currency_source) == (
                amount,
                label,
                "payload",
            )
            assert raw.reported_by == "provider"
            assert _warning(imported, "amount_unparseable").raw == amount
            assert "decimal_normalized" not in _codes(imported)
            _revalidates(imported)
            if label == "USD":
                assert _codes(imported) == ["amount_unparseable"]
        via_contract = _import(currency_raw=None, amount_raw=amount, contract=_contract("USD"))
        _not_available(via_contract.record, "amount_unparseable")
        raw = via_contract.record.reported_raw
        assert (raw.amount_raw, raw.currency_raw, raw.currency_source) == (
            amount,
            "USD",
            "api_contract",
        )
        assert _codes(via_contract) == ["amount_unparseable"]
        assert _warning(via_contract, "amount_unparseable").raw == amount
        _revalidates(via_contract)
        foreign = _import(currency_raw="EUR", amount_raw=amount)
        _not_available(foreign.record, "unsupported_currency")
        assert foreign.record.reported_raw.amount_raw == amount
        assert "amount_unparseable" not in _codes(foreign)
        silent = _import(currency_raw=None, amount_raw=amount)
        _not_available(silent.record, "currency_not_reported")
        assert "amount_unparseable" not in _codes(silent)
    for good, canonical in (("12.5", "12.5"), ("0", "0"), ("7.", "7"), ("0.0", "0")):
        assert _import(amount_raw=good).record.amount.amount == canonical


def test_payload_currency_label_wins_over_cited_contract() -> None:
    """N59 edge case, N68 (b), N73 (ADR 0014 Decision 12c step 2; api-surface 23.6 group H item 5
    and Z item 5): a label sent in the payload wins over a cited API contract. A non-USD label
    gives ``unsupported_currency`` with ``currency_source=payload``, ``currency_raw`` the label and
    the raw amount kept (``"EUR"`` against a cited USD contract, ``"EUR"`` against a cited INR
    contract, ``"$"`` against a cited USD contract), and the warnings are exactly
    ``unsupported_currency`` and ``currency_contract_conflict`` (compared as a multiset, N75): the
    conflict message holds the payload label and the contract's code and its ``raw`` is the label as
    sent (D16). ``" usd "`` against a cited USD contract agrees (D15) and is an available reported
    record with only ``currency_label_normalized``. A non-USD label that equals the contract's
    code exactly agrees too (N73): ``"EUR"`` against a cited ``EUR`` contract is only
    ``unsupported_currency``; ``" eur "`` is not equal to ``EUR`` and conflicts."""
    for label, code in (("EUR", "USD"), ("EUR", "INR"), ("$", "USD")):
        imported = _import(currency_raw=label, amount_raw="12.50", contract=_contract(code))
        _not_available(imported.record, "unsupported_currency")
        raw = imported.record.reported_raw
        assert (raw.currency_source, raw.currency_raw, raw.amount_raw) == (
            "payload",
            label,
            "12.50",
        )
        assert sorted(_codes(imported)) == ["currency_contract_conflict", "unsupported_currency"]
        conflict = _warning(imported, "currency_contract_conflict")
        assert label in conflict.message
        assert code in conflict.message
        assert conflict.raw == label
        assert _warning(imported, "unsupported_currency").raw == label
        _revalidates(imported)
    agrees = _import(currency_raw=" usd ", amount_raw="5", contract=_contract("USD"))
    assert agrees.record.status == "available"
    assert agrees.record.basis == "reported"
    assert agrees.record.amount.amount == "5"
    assert _codes(agrees) == ["currency_label_normalized"]
    same = _import(currency_raw="EUR", amount_raw="5", contract=_contract("EUR"))
    _not_available(same.record, "unsupported_currency")
    assert same.record.reported_raw.currency_source == "payload"
    assert _codes(same) == ["unsupported_currency"]
    inexact = _import(currency_raw=" eur ", amount_raw="5", contract=_contract("EUR"))
    _not_available(inexact.record, "unsupported_currency")
    assert sorted(_codes(inexact)) == ["currency_contract_conflict", "unsupported_currency"]


def test_empty_currency_label_is_currency_not_reported() -> None:
    """N59 edge case, D6 (ADR 0014 Decision 12c steps 1 and 3; api-surface 23.6 group H item 6): an
    empty label and a label of ASCII white space only count as no label. Without a contract the
    cost is not_available ``currency_not_reported`` with ``currency_source=none``, ``currency_raw``
    absent and exactly one ``currency_not_reported`` warning whose ``raw`` is the label as sent;
    with a cited USD contract the contract applies (an available reported record, no such
    warning)."""
    for label in ("", "   ", "\t", " \n "):
        imported = _import(currency_raw=label, amount_raw="42")
        _not_available(imported.record, "currency_not_reported")
        raw = imported.record.reported_raw
        assert (raw.currency_raw, raw.currency_source, raw.amount_raw) == (None, "none", "42")
        assert _codes(imported) == ["currency_not_reported"]
        assert _warning(imported, "currency_not_reported").raw == label
        _revalidates(imported)
        cited = _import(currency_raw=label, amount_raw="42", contract=_contract("USD"))
        assert cited.record.status == "available"
        assert cited.record.amount.amount == "42"
        assert "currency_not_reported" not in _codes(cited)
        foreign = _import(currency_raw=label, amount_raw="42", contract=_contract("INR"))
        _not_available(foreign.record, "unsupported_currency")
        assert foreign.record.reported_raw.currency_source == "api_contract"


def test_unicode_space_in_currency_label_is_not_usd() -> None:
    """N59 edge case, D5 (ADR 0014 Decision 12c step 1; api-surface 23.6 group H item 7): only the
    six ASCII white-space characters are trimmed, so a label with a no-break space, an em space, an
    ideographic space, a zero-width space or a byte order mark around ``USD`` is not USD: the cost
    is not_available ``unsupported_currency`` with the label verbatim, ``currency_source=payload``
    and an ``unsupported_currency`` warning; no ``currency_label_normalized`` warning."""
    labels = (
        "USD" + chr(0xA0),
        chr(0xA0) + "USD",
        chr(0x2003) + "usd",
        chr(0x3000) + "USD",
        "USD" + chr(0x200B),
        chr(0xFEFF) + "USD",
    )
    for label in labels:
        imported = _import(currency_raw=label, amount_raw="5")
        _not_available(imported.record, "unsupported_currency")
        raw = imported.record.reported_raw
        assert (raw.currency_raw, raw.currency_source) == (label, "payload")
        assert "unsupported_currency" in _codes(imported)
        assert "currency_label_normalized" not in _codes(imported)
        _revalidates(imported)


def test_currency_not_reported_always_warns() -> None:
    """N59 edge case (ADR 0014 Decision 12c step 3; api-surface 23.6 group H item 8): a cost with
    no currency always records exactly one ``currency_not_reported`` warning (and no other):
    with no contract, and with each uncited contract form (no URL, an empty URL, no
    ``retrieved_at``, neither)."""
    contract = cost_class("ContractCurrency")
    forms = (
        None,
        contract("USD", None, RETRIEVED),
        contract("USD", "", RETRIEVED),
        contract("USD", CITATION, None),
        contract("USD", None, None),
        contract("INR", "", None),
    )
    for form in forms:
        imported = _import(currency_raw=None, amount_raw="5", contract=form)
        _not_available(imported.record, "currency_not_reported")
        assert _codes(imported) == ["currency_not_reported"]
        assert _warning(imported, "currency_not_reported").raw is None


def test_usd_label_against_non_usd_contract_is_currency_conflict() -> None:
    """N59 remainder (a), N68 (ADR 0014 Decision 12c step 2, detail D7, D16; api-surface 23.6 groups
    T item 1 and Z item 4): a payload label that is USD (``"USD"``, ``"usd"``, ``" USD "``)
    against a cited contract that states another currency (``EUR``, ``INR``) is not_available with
    ``na_reason=currency_conflict``: no amount, no basis, ``reported_raw`` with the amount and the
    label exactly as sent and ``currency_source=payload``, whatever the amount (``"12.5"``,
    ``"12.50"``, ``"abc"``: it is not parsed, so no ``amount_unparseable`` or ``decimal_normalized``
    warning). The warnings are exactly ``currency_contract_conflict`` for ``"USD"`` and, for the
    other two spellings, ``currency_label_normalized`` (its ``raw`` the label as sent) plus
    ``currency_contract_conflict`` (N68 (c)); the conflict warning's ``raw`` is the payload label
    as sent and its message holds the contract's code and the payload label. The record holds no
    contract currency (N68 (a)): ``reported_raw`` has exactly the fields ``reported_by``,
    ``amount_raw``, ``currency_raw`` and ``currency_source``, the contract's code is nowhere in the
    record's JSON, and a ``contract_currency_raw`` field is an unknown field. The same labels with
    an uncited contract (no URL, an empty URL, no ``retrieved_at``) give an available USD record:
    an uncited contract is no contract."""
    contract = cost_class("ContractCurrency")
    raw_model = registered_class("ReportedCostRaw")
    for code in ("EUR", "INR"):
        for label in ("USD", "usd", " USD "):
            for amount in ("12.5", "12.50", "abc"):
                imported = _import(currency_raw=label, amount_raw=amount, contract=_contract(code))
                _not_available(imported.record, "currency_conflict")
                raw = imported.record.reported_raw
                assert (raw.amount_raw, raw.currency_raw, raw.currency_source) == (
                    amount,
                    label,
                    "payload",
                )
                assert raw.reported_by == "provider"
                expected = ["currency_contract_conflict"]
                if label != "USD":
                    expected = ["currency_contract_conflict", "currency_label_normalized"]
                assert sorted(_codes(imported)) == expected, (label, amount, code)
                conflict = _warning(imported, "currency_contract_conflict")
                assert code in conflict.message
                assert label.strip() in conflict.message
                assert conflict.raw == label
                if label != "USD":
                    assert _warning(imported, "currency_label_normalized").raw == label
                assert set(raw_model.model_fields) == {
                    "reported_by",
                    "amount_raw",
                    "currency_raw",
                    "currency_source",
                }
                assert code not in imported.record.model_dump_json()
                _revalidates(imported)
    for code in ("EUR", "INR"):
        with pytest.raises(ValueError, match="contract_currency_raw"):
            raw_model.model_validate(
                {
                    "reported_by": "provider",
                    "amount_raw": "1",
                    "currency_raw": "USD",
                    "currency_source": "payload",
                    "contract_currency_raw": code,
                }
            )
    url = CITATION
    for uncited in (
        contract("EUR", None, RETRIEVED),
        contract("EUR", "", RETRIEVED),
        contract("EUR", url, None),
        contract("INR", None, None),
    ):
        for label in ("USD", "usd", " USD "):
            imported = _import(currency_raw=label, amount_raw="12.5", contract=uncited)
            assert imported.record.status == "available"
            assert imported.record.basis == "reported"
            assert imported.record.amount.amount == "12.5"
            assert "currency_contract_conflict" not in _codes(imported)


def test_unsupported_currency_warning_raw_and_currency_source_path() -> None:
    """N59 remainder (b), N69, N74, D19 (ADR 0014 Decision 12c step 5; api-surface 23.6 group Z
    item 2; renamed in revision 17 from ``..._and_source_path``, the old name must not exist):
    payload labels ``"EUR"``, ``" eur "``, ``"$"`` and "USD" followed by a no-break space give an
    ``unsupported_currency`` warning whose ``raw`` is the label exactly as sent. With
    ``currency_source_path="/resourceSpans/0/cost/currency"`` (any text, passed through
    verbatim) its ``path`` equals that text; with only ``amount_source_path``, or with neither,
    ``path`` is ``None``. With no label and a cited ``EUR`` contract the warning has ``raw``
    ``None`` (nothing was sent), a message that contains ``EUR`` and, because a currency path was
    passed, that path (N74). ``import_reported_cost(..., source_path="x")`` is a ``TypeError``:
    the single path is withdrawn, not aliased (D19)."""
    path = "/resourceSpans/0/cost/currency"
    other = "/resourceSpans/0/cost/amount"
    for label in ("EUR", " eur ", "$", "USD" + chr(0xA0)):
        with_path = _import(currency_raw=label, amount_raw="12.50", currency_source_path=path)
        warning = _warning(with_path, "unsupported_currency")
        assert warning.raw == label
        assert warning.path == path
        only_amount = _import(currency_raw=label, amount_raw="12.50", amount_source_path=other)
        assert _warning(only_amount, "unsupported_currency").path is None
        neither = _import(currency_raw=label, amount_raw="12.50")
        assert _warning(neither, "unsupported_currency").path is None
        assert _warning(neither, "unsupported_currency").raw == label
    for passed in ({"currency_source_path": "line 12"}, {}, {"amount_source_path": other}):
        from_contract = _import(
            currency_raw=None, amount_raw="12.50", contract=_contract("EUR"), **passed
        )
        _not_available(from_contract.record, "unsupported_currency")
        warning = _warning(from_contract, "unsupported_currency")
        assert warning.raw is None
        assert "EUR" in warning.message
        assert warning.path == passed.get("currency_source_path")
        assert from_contract.record.reported_raw.currency_raw == "EUR"
    with pytest.raises(TypeError):
        _import(currency_raw="EUR", amount_raw="1", source_path="/resourceSpans/0/cost")


def test_import_warning_paths_follow_their_field() -> None:
    """N69, N74, N75, D19 (ADR 0014 Decision 12c step 5; api-surface 23.6 group Z item 3): with two
    different paths passed, a warning about the currency field carries ``currency_source_path``
    and a warning about the amount carries ``amount_source_path``: ``"usd"`` with ``"12.50"``
    gives ``currency_label_normalized`` (currency path) and ``decimal_normalized`` (amount path);
    ``"USD"`` with ``"1e3"`` gives ``amount_unparseable`` (amount path); an empty label without a
    contract gives ``currency_not_reported`` (currency path); ``"USD"`` against a cited ``EUR``
    contract gives ``currency_contract_conflict`` (currency path); ``"EUR"`` gives
    ``unsupported_currency`` (currency path). Each case again with only the other path (that
    warning's path is ``None``) and with neither (``None``). Paths are passed through verbatim,
    also text that is no pointer; no warning carries the other field's path or a path the
    caller did not pass. Warnings are compared as a multiset of ``(code, raw, path)`` (N75). N74:
    ``currency_not_reported`` with ``currency_raw=None`` carries the currency path when it is
    passed."""
    paths = ("/spans/0/cost/currency", "line 12")
    amount_paths = ("/spans/0/cost/amount", "$.cost.amount")
    cases: list[tuple[dict[str, Any], list[tuple[str, str | None, str]]]] = [
        (
            {"currency_raw": "usd", "amount_raw": "12.50"},
            [("currency_label_normalized", "usd", "c"), ("decimal_normalized", "12.50", "a")],
        ),
        ({"currency_raw": "USD", "amount_raw": "1e3"}, [("amount_unparseable", "1e3", "a")]),
        ({"currency_raw": "", "amount_raw": "5"}, [("currency_not_reported", "", "c")]),
        ({"currency_raw": None, "amount_raw": "5"}, [("currency_not_reported", None, "c")]),
        (
            {"currency_raw": "USD", "amount_raw": "5", "contract": _contract("EUR")},
            [("currency_contract_conflict", "USD", "c")],
        ),
        ({"currency_raw": "EUR", "amount_raw": "5"}, [("unsupported_currency", "EUR", "c")]),
    ]
    for currency_path, amount_path in zip(paths, amount_paths, strict=True):
        for kwargs, expected in cases:
            for passed_currency, passed_amount in (
                (currency_path, amount_path),
                (currency_path, None),
                (None, amount_path),
                (None, None),
            ):
                imported = _import(
                    **kwargs,
                    currency_source_path=passed_currency,
                    amount_source_path=passed_amount,
                )
                wanted = sorted(
                    (
                        (code, raw, passed_currency if field == "c" else passed_amount)
                        for code, raw, field in expected
                    ),
                    key=repr,
                )
                assert _triples(imported) == wanted, (kwargs, passed_currency, passed_amount)


def _no_surrogates(*texts: str | None) -> None:
    for text in texts:
        if text is not None:
            assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in text), repr(text)


def _serialisable(imported: Any) -> None:
    """The record and every warning encode as UTF-8 JSON and the record validates again (a lone
    surrogate cannot be written as UTF-8)."""
    _revalidates(imported)
    imported.record.model_dump_json().encode("utf-8")
    for warning in imported.warnings:
        warning.model_dump_json().encode("utf-8")
        _no_surrogates(warning.raw, warning.path, warning.message)


def test_import_lone_surrogate_never_raises_and_is_flagged() -> None:
    """N82 (owner auto-approved, api-surface 23.10; closes the P1-31 lone-surrogate item):
    ``import_reported_cost`` never raises on the text of a reported label or amount. A string with
    a lone surrogate (U+D800..U+DFFF, also two in a row, which is not an astral character) is
    unparseable: a label is ``unsupported_currency`` (``currency_source=payload``), an amount with
    a USD label is ``amount_unparseable``; with a non-USD label the amount stays unparsed as in
    D7. The stored ``reported_raw`` holds the text with every lone surrogate replaced by U+FFFD,
    one for one, and nothing else changed. One ``text_not_valid_unicode`` warning per affected
    field records the replacement, with the path of that field when passed (D19, absent when not
    passed). The ``raw`` of the ``unsupported_currency`` and ``amount_unparseable`` warnings is the
    same replaced text as the stored one (the warning model cannot hold a surrogate); that of the
    ``text_not_valid_unicode`` warning is not pinned beyond holding no surrogate. No warning,
    message or record holds a surrogate and all serialise as UTF-8 JSON. A
    valid astral character is kept as sent and gives no such warning."""
    d800, dc00 = chr(0xD800), chr(0xDC00)
    fffd = chr(0xFFFD)
    cpath, apath = "/spans/0/cost/currency", "/spans/0/cost/amount"
    labels = (
        ("US" + d800 + "D", "US" + fffd + "D"),
        ("USD" + dc00, "USD" + fffd),
        (d800, fffd),
        ("EUR" + d800, "EUR" + fffd),
        (chr(0xD83D) + chr(0xDE00), fffd + fffd),
    )
    for sent, stored in labels:
        for paths in ({"currency_source_path": cpath, "amount_source_path": apath}, {}):
            imported = _import(currency_raw=sent, amount_raw="12.5", **paths)
            _not_available(imported.record, "unsupported_currency")
            raw = imported.record.reported_raw
            assert (raw.currency_raw, raw.currency_source, raw.amount_raw) == (
                stored,
                "payload",
                "12.5",
            )
            assert sorted(_codes(imported)) == ["text_not_valid_unicode", "unsupported_currency"]
            flagged = _warning(imported, "text_not_valid_unicode")
            assert flagged.path == paths.get("currency_source_path")
            assert _warning(imported, "unsupported_currency").path == flagged.path
            assert _warning(imported, "unsupported_currency").raw == stored
            _serialisable(imported)
    for sent, stored in (
        ("1" + d800 + "2", "1" + fffd + "2"),
        (d800, fffd),
        ("12.5" + dc00, "12.5" + fffd),
        (d800 + dc00, fffd + fffd),
    ):
        for label, contract in (("USD", None), ("usd", None), (None, _contract("USD"))):
            imported = _import(
                currency_raw=label,
                amount_raw=sent,
                contract=contract,
                currency_source_path=cpath,
                amount_source_path=apath,
            )
            _not_available(imported.record, "amount_unparseable")
            raw = imported.record.reported_raw
            assert raw.amount_raw == stored
            assert raw.currency_source == ("payload" if contract is None else "api_contract")
            assert "decimal_normalized" not in _codes(imported)
            assert _warning(imported, "amount_unparseable").path == apath
            assert _warning(imported, "amount_unparseable").raw == stored
            assert _warning(imported, "text_not_valid_unicode").path == apath
            _serialisable(imported)
        foreign = _import(
            currency_raw="EUR",
            amount_raw=sent,
            currency_source_path=cpath,
            amount_source_path=apath,
        )
        _not_available(foreign.record, "unsupported_currency")
        assert foreign.record.reported_raw.amount_raw == stored
        assert "amount_unparseable" not in _codes(foreign)
        assert _warning(foreign, "text_not_valid_unicode").path == apath
        _serialisable(foreign)
        silent = _import(currency_raw=None, amount_raw=sent)
        _not_available(silent.record, "currency_not_reported")
        assert silent.record.reported_raw.amount_raw == stored
        assert "text_not_valid_unicode" in _codes(silent)
        _serialisable(silent)
    # both fields damaged: one warning each, told apart by path
    both = _import(
        currency_raw="EU" + d800,
        amount_raw="1" + dc00,
        currency_source_path=cpath,
        amount_source_path=apath,
    )
    _not_available(both.record, "unsupported_currency")
    assert (both.record.reported_raw.currency_raw, both.record.reported_raw.amount_raw) == (
        "EU" + fffd,
        "1" + fffd,
    )
    flagged_paths = sorted(str(w.path) for w in both.warnings if w.code == "text_not_valid_unicode")
    assert flagged_paths == sorted([cpath, apath])
    _serialisable(both)
    # valid text is not flagged and kept as sent, astral characters included
    astral = "EUR" + chr(0x1F600)
    kept = _import(currency_raw=astral, amount_raw="12.5")
    assert kept.record.reported_raw.currency_raw == astral
    assert "text_not_valid_unicode" not in _codes(kept)
    assert _warning(kept, "unsupported_currency").raw == astral
    assert "text_not_valid_unicode" not in _codes(_import(currency_raw="USD", amount_raw="1e3"))
    assert "text_not_valid_unicode" not in _codes(_import())
