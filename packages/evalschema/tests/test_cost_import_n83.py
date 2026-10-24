# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""N83 (decided, auto-approved, option A; api-surface 23.10, ADR README row N83): a lone surrogate
in a caller-supplied warning path of ``cost.import_reported_cost``.

Pinned behaviour, from the decision log and api-surface 23.10 (nothing else is pinned):

* ``currency_source_path`` and ``amount_source_path`` are locators, not data. A lone surrogate
  (U+D800..U+DFFF; two in a row are two, not an astral character) in a passed path is replaced by
  U+FFFD, one for one, exactly as ``text.replace_lone_surrogates`` does for the label and the
  amount, and records NOTHING extra: the import is identical to the one with the path already
  replaced. The importer never raises because of such a path.
* A path that is valid text (an astral character included) is kept as sent.
* A cited contract whose ``currency`` holds a lone surrogate stays a ``ValueError`` (mapping
  configuration, linted at P1-35), whatever the label, amount and paths. (``ValidationError`` is a
  ``ValueError``; the test pins only ``ValueError``.)

Ambiguities the ADR leaves open, deliberately NOT pinned here (see the report to the architect):

* an UNCITED contract (no url or no retrieved_at) with a surrogate currency: today it is ignored
  like a blank code (the importer never reads it); the ADR text says only "a cited contract's
  currency";
* the exact message of the contract ``ValueError`` and whether it is a bare ``ValueError`` or a
  pydantic ``ValidationError``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ._support import api
from ._support_b4 import cost_class, cost_fn, when

D800, DC00, DBFF = chr(0xD800), chr(0xDC00), chr(0xDBFF)
FFFD = chr(0xFFFD)
CITATION = "https://docs.example.com/api/generations#cost"
RETRIEVED = when("2026-10-01T00:00:00Z")
USAGE_IDS = [api("ids").UsageRecordId.new()]
RECORD_ID = api("ids").CostRecordId.new()
CREATED = when("2026-10-23T00:00:00Z")


def _import(**over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "domain": "evaluation",
        "usage_record_ids": USAGE_IDS,
        "reported_by": "provider",
        "amount_raw": "1000",
        "currency_raw": "USD",
        "record_id": RECORD_ID,
        "created_at": CREATED,
    }
    kwargs.update(over)
    return cost_fn("import_reported_cost")(**kwargs)


def _contract(code: str, *, cited: bool = True) -> Any:
    if cited:
        return cost_class("ContractCurrency")(code, CITATION, RETRIEVED)
    return cost_class("ContractCurrency")(code, None, None)


def _has_surrogate(text: str | None) -> bool:
    return text is not None and any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def _encodable(imported: Any) -> None:
    """Record and every warning encode as UTF-8 JSON (a lone surrogate cannot be written)."""
    imported.record.model_dump_json().encode("utf-8")
    for warning in imported.warnings:
        warning.model_dump_json().encode("utf-8")
        assert not _has_surrogate(warning.path), repr(warning.path)
        assert not _has_surrogate(warning.raw), repr(warning.raw)
        assert not _has_surrogate(warning.message), repr(warning.message)


# (sent path, stored path): every lone surrogate becomes one U+FFFD, nothing else changes.
PATHS = [
    ("/a/" + D800 + "/cost", "/a/" + FFFD + "/cost"),
    ("/a/" + DC00, "/a/" + FFFD),
    (D800, FFFD),
    (D800 + DC00, FFFD + FFFD),  # reversed pair is two lone code points
    (chr(0xD83D) + chr(0xDE00), FFFD + FFFD),  # a pair spelled as two code points is not astral
    (DBFF + "x" + D800, FFFD + "x" + FFFD),
    ("\u0c15" + D800 + "\u200d", "\u0c15" + FFFD + "\u200d"),  # Telugu ka + ZWJ around it
]

# imports whose warnings are about the CURRENCY field: (id, kwargs)
CURRENCY_CASES = [
    ("label_normalized", {"currency_raw": " usd "}),
    ("unsupported", {"currency_raw": "EUR"}),
    ("not_reported", {"currency_raw": None}),
    ("blank_not_reported", {"currency_raw": "  "}),
    ("contract_conflict_usd_label", {"currency_raw": "USD", "contract": "EUR"}),
    ("contract_conflict_other_label", {"currency_raw": "EUR", "contract": "USD"}),
    ("contract_unsupported", {"currency_raw": None, "contract": "EUR"}),
    ("damaged_label", {"currency_raw": "EU" + D800}),
]

# imports whose warnings are about the AMOUNT field: (id, kwargs)
AMOUNT_CASES = [
    ("decimal_normalized", {"amount_raw": "12.50"}),
    ("decimal_normalized_point", {"amount_raw": "7."}),
    ("unparseable", {"amount_raw": "1e3"}),
    ("unparseable_sign", {"amount_raw": "-1"}),
    ("damaged_amount", {"amount_raw": "1" + D800 + "2"}),
]


def _kwargs(case: dict[str, Any]) -> dict[str, Any]:
    out = dict(case)
    if isinstance(out.get("contract"), str):
        out["contract"] = _contract(out["contract"])
    return out


@pytest.mark.parametrize(("sent", "stored"), PATHS)
@pytest.mark.parametrize(("name", "case"), CURRENCY_CASES, ids=[c[0] for c in CURRENCY_CASES])
def test_surrogate_in_currency_path_is_replaced_and_records_nothing_extra(
    name: str, case: dict[str, Any], sent: str, stored: str
) -> None:
    """The import with the surrogate path equals the import with the path already replaced (same
    record, same warnings in the same order, no extra warning), the warnings about the currency
    carry the replaced path, and nothing raises."""
    got = _import(**_kwargs(case), currency_source_path=sent)
    want = _import(**_kwargs(case), currency_source_path=stored)
    assert got.record == want.record, name
    assert got.warnings == want.warnings, name
    assert [w.code for w in got.warnings] == [w.code for w in want.warnings]
    assert any(w.path == stored for w in got.warnings), name
    assert not any(w.code == "text_not_valid_unicode" for w in got.warnings) or (
        case.get("currency_raw") == "EU" + D800
    ), name
    _encodable(got)


@pytest.mark.parametrize(("sent", "stored"), PATHS)
@pytest.mark.parametrize(("name", "case"), AMOUNT_CASES, ids=[c[0] for c in AMOUNT_CASES])
def test_surrogate_in_amount_path_is_replaced_and_records_nothing_extra(
    name: str, case: dict[str, Any], sent: str, stored: str
) -> None:
    got = _import(**_kwargs(case), amount_source_path=sent)
    want = _import(**_kwargs(case), amount_source_path=stored)
    assert got.record == want.record, name
    assert got.warnings == want.warnings, name
    assert any(w.path == stored for w in got.warnings), name
    assert not any(w.code == "text_not_valid_unicode" for w in got.warnings) or (
        case.get("amount_raw") == "1" + D800 + "2"
    ), name
    _encodable(got)


def test_both_paths_with_surrogates_and_both_fields_damaged() -> None:
    """Both paths damaged, both texts damaged: four-way mix; one warning per field as in N82, the
    paths told apart and both replaced; the codes equal those with clean paths."""
    cpath, amp = "/c" + D800, "/a" + DC00 + D800
    got = _import(
        currency_raw="EU" + D800,
        amount_raw="1" + DC00,
        currency_source_path=cpath,
        amount_source_path=amp,
    )
    want = _import(
        currency_raw="EU" + D800,
        amount_raw="1" + DC00,
        currency_source_path="/c" + FFFD,
        amount_source_path="/a" + FFFD + FFFD,
    )
    assert got.record == want.record
    assert got.warnings == want.warnings
    flagged = sorted(str(w.path) for w in got.warnings if w.code == "text_not_valid_unicode")
    assert flagged == sorted(["/c" + FFFD, "/a" + FFFD + FFFD])
    _encodable(got)


def test_both_paths_with_surrogates_with_warnings_on_each_field() -> None:
    got = _import(
        currency_raw="usd",
        amount_raw="1.0",
        currency_source_path="/c" + D800,
        amount_source_path="/a" + D800,
    )
    codes = [w.code for w in got.warnings]
    assert sorted(codes) == ["currency_label_normalized", "decimal_normalized"]
    by_code = {w.code: w.path for w in got.warnings}
    assert by_code == {
        "currency_label_normalized": "/c" + FFFD,
        "decimal_normalized": "/a" + FFFD,
    }
    _encodable(got)


def test_surrogate_in_the_path_of_the_other_field_is_not_a_crash_nor_a_warning() -> None:
    """Only the amount warns; the currency path (damaged) is never attached to a warning, so it
    cannot show anywhere; and the reverse."""
    only_amount = _import(
        amount_raw="1.0", currency_source_path="/c" + D800, amount_source_path="/a"
    )
    assert [(w.code, w.path) for w in only_amount.warnings] == [("decimal_normalized", "/a")]
    only_currency = _import(
        currency_raw="usd", currency_source_path="/c", amount_source_path="/a" + D800
    )
    assert [(w.code, w.path) for w in only_currency.warnings] == [
        ("currency_label_normalized", "/c")
    ]
    _encodable(only_amount)
    _encodable(only_currency)


def test_surrogate_paths_without_any_warning_are_harmless() -> None:
    clean = _import()
    for kwargs in (
        {"currency_source_path": D800},
        {"amount_source_path": D800},
        {"currency_source_path": D800, "amount_source_path": DC00},
    ):
        got = _import(**kwargs)
        assert got.warnings == []
        assert got.record == clean.record


def test_valid_paths_are_kept_as_sent_astral_included() -> None:
    astral = "/spans/" + chr(0x1F600) + "/cost\u200d\u0c15"
    got = _import(currency_raw="EUR", amount_raw="1.0", currency_source_path=astral)
    assert [(w.code, w.path) for w in got.warnings] == [("unsupported_currency", astral)]
    got = _import(amount_raw="1.0", amount_source_path=astral)
    assert [(w.code, w.path) for w in got.warnings] == [("decimal_normalized", astral)]
    # a mixed path: the astral character is kept, only the lone code point is replaced
    mixed = _import(
        currency_raw="EUR", currency_source_path="/" + chr(0x1F600) + D800 + chr(0x1F600)
    )
    assert mixed.warnings[0].path == "/" + chr(0x1F600) + FFFD + chr(0x1F600)


def test_unaffected_paths_are_not_altered_by_the_replacement() -> None:
    """The replacement touches code points in U+D800..U+DFFF only: U+FFFD and neighbours of the
    surrogate block (U+D7FF, U+E000) in a path stay as sent."""
    path = "/" + chr(0xD7FF) + chr(0xE000) + FFFD + "/x"
    got = _import(currency_raw="EUR", currency_source_path=path)
    assert got.warnings[0].path == path


@pytest.mark.parametrize("label", ["USD", "usd", "EUR", None])
@pytest.mark.parametrize("code", ["USD" + D800, "EUR" + DC00, D800, "U" + D800 + "D"])
def test_contract_currency_with_a_lone_surrogate_stays_a_value_error(
    code: str, label: str | None
) -> None:
    """Option A keeps the ``ValueError`` for a surrogate in a cited contract's currency (mapping
    configuration), also when the paths are clean and also when they hold surrogates themselves
    (the path replacement must not turn this into a silent success)."""
    contract = _contract(code)
    for paths in (
        {},
        {"currency_source_path": "/c", "amount_source_path": "/a"},
        {"currency_source_path": "/c" + D800, "amount_source_path": "/a" + D800},
    ):
        with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
            _import(currency_raw=label, contract=contract, **paths)
        with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
            _import(currency_raw=label, amount_raw="1.0", contract=contract, **paths)


CONTRACT_MESSAGE = (
    "a cited contract states its currency code as valid Unicode text "
    "(contract.currency holds a lone surrogate)"
)


@pytest.mark.parametrize("label", ["USD", "usd", "EUR", None, "EU" + D800])
@pytest.mark.parametrize("code", ["USD" + D800, "EUR" + DC00, D800, "U" + D800 + "D"])
def test_contract_currency_surrogate_message_names_the_rule_and_never_echoes_the_value(
    code: str, label: str | None
) -> None:
    """The explicit ``ValueError`` of a cited contract whose currency holds a lone surrogate says
    ``contract.currency`` and ``lone surrogate``, equals the pinned text, and holds neither a
    surrogate nor any part of the value (the ISO-like prefix of the code) nor its code point."""
    contract = _contract(code)
    for paths in (
        {},
        {"currency_source_path": "/c", "amount_source_path": "/a"},
        {"currency_source_path": "/c" + D800, "amount_source_path": "/a" + D800},
    ):
        for amount in ("1000", "1.0", "1e3", "1" + DC00):
            with pytest.raises(ValueError, match=re.escape(CONTRACT_MESSAGE)) as caught:
                _import(currency_raw=label, amount_raw=amount, contract=contract, **paths)
            text = str(caught.value)
            assert text == CONTRACT_MESSAGE
            assert "contract.currency" in text
            assert "lone surrogate" in text
            assert not _has_surrogate(text)
            text.encode("utf-8")
            for part in ("USD", "EUR", "D800", "DC00", "d800", "dc00"):
                assert part not in text


@pytest.mark.parametrize("code", ["USD" + D800, "EUR" + DC00, D800])
@pytest.mark.parametrize("label", ["USD", "EUR", None])
def test_uncited_contract_with_a_surrogate_currency_still_does_not_raise(
    code: str, label: str | None
) -> None:
    """An uncited contract (no url, no retrieved_at, or either alone) counts as no contract; its
    currency is never read, so it never raises, and the import equals the one without contract."""
    base = _import(currency_raw=label)
    for url, retrieved in ((None, None), ("", RETRIEVED), (CITATION, None), (None, RETRIEVED)):
        contract = cost_class("ContractCurrency")(code, url, retrieved)
        got = _import(currency_raw=label, contract=contract)
        assert got.record == base.record
        assert got.warnings == base.warnings
