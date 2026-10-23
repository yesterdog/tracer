# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04b: UtcDatetime, millisecond types, DecimalString, SupportedCurrency, Money (ADR 0002, 0014).

Assumed public API (docs/phase1/api-surface.md) in ``tracer_evalschema.primitives``: the type
aliases ``UtcDatetime``, ``Ms``, ``DecimalString``, ``SupportedCurrency``; the model ``Money``;
``load_supported_currencies(path=None)`` and ``make_money_model(codes)`` (the data-driven seam).
"""

from __future__ import annotations

import itertools
import json
import re
import typing
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from ._support import adapter, api, data_json, resolve_schema

SCHEMA_MODES: tuple[Literal["validation", "serialization"], ...] = ("validation", "serialization")


def _jsonschema() -> Any:
    return typing.cast(Any, __import__("jsonschema"))


# ------------------------------------------------------------------------------ UtcDatetime


def _utc() -> TypeAdapter[Any]:
    return adapter("primitives", "UtcDatetime")


def test_utc_rejects_naive() -> None:
    adapter = _utc()
    for naive in (
        datetime(2026, 10, 22, 10, 0, 0),
        "2026-10-22T10:00:00",
        "2026-10-22T10:00:00.123456",
        "2026-10-22",
        "2026-10-22 10:00:00",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(naive)
        if isinstance(naive, str):
            with pytest.raises(ValidationError):
                adapter.validate_json(json.dumps(naive))
    # aware input is accepted, so the rejection above is about the missing offset
    assert adapter.validate_python(datetime(2026, 10, 22, 10, tzinfo=UTC)) is not None


def test_utc_normalizes_offset_to_Z() -> None:
    adapter = _utc()
    ist = timezone(timedelta(hours=5, minutes=30))
    cases: list[Any] = [
        datetime(2026, 10, 22, 10, 0, 0, tzinfo=ist),
        "2026-10-22T10:00:00+05:30",
        "2026-10-22T04:30:00Z",
        "2026-10-22T04:30:00+00:00",
        "2026-10-21T20:30:00-08:00",
    ]
    for case in cases:
        value = adapter.validate_python(case)
        assert value.utcoffset() == timedelta(0), case
        assert value == datetime(2026, 10, 22, 4, 30, 0, tzinfo=UTC), case
        assert adapter.dump_python(value, mode="json") == "2026-10-22T04:30:00.000000Z", case
        assert adapter.dump_json(value) == b'"2026-10-22T04:30:00.000000Z"', case


def test_utc_serialization_canonical_six_digits() -> None:
    """P-04: one instant, one spelling (exactly six fractional digits)."""
    adapter = _utc()
    cases = {
        "2026-10-22T10:00:00Z": "2026-10-22T10:00:00.000000Z",
        "2026-10-22T10:00:00.5Z": "2026-10-22T10:00:00.500000Z",
        "2026-10-22T10:00:00.123Z": "2026-10-22T10:00:00.123000Z",
        "2026-10-22T10:00:00.000Z": "2026-10-22T10:00:00.000000Z",
        "2026-10-22T10:00:00.123456Z": "2026-10-22T10:00:00.123456Z",
        "2026-10-22T10:00:00.000001+00:00": "2026-10-22T10:00:00.000001Z",
    }
    for raw, canonical in cases.items():
        assert adapter.dump_python(adapter.validate_python(raw), mode="json") == canonical
    spellings = {adapter.dump_json(adapter.validate_python(raw)) for raw in list(cases)[:4]}
    assert len(spellings) == 3, "0, .000 and the Z-only form are the same instant: one spelling"
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
    assert all(pattern.match(c) for c in cases.values())
    # the canonical text is itself valid input and stable
    again = adapter.validate_python("2026-10-22T10:00:00.123456Z")
    assert adapter.dump_python(again, mode="json") == "2026-10-22T10:00:00.123456Z"


def test_utc_rejects_leap_second() -> None:
    adapter = _utc()
    for text in (
        "2016-12-31T23:59:60Z",
        "2016-12-31T23:59:60+00:00",
        "2016-12-31T23:59:60.5Z",
        "2016-12-31T18:59:60-05:00",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(text)
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(text))
    # the last valid second of that day is fine
    assert adapter.validate_python("2016-12-31T23:59:59Z") is not None


def test_utc_rejects_excess_precision_numbers_and_dates() -> None:
    """SCOPE name (revision 7; ADR 0002 Decision 1, api-surface section 4 and 15a item 7).

    Rejected in Python and JSON mode: a string with more than six fractional digits (never
    truncated or rounded; OTLP-style nanoseconds are the importer's business, escalated as E3),
    JSON numbers and Python int/float (no epoch values), bool, and date objects that are not
    datetime instances."""
    adapter = _utc()
    excess = [
        "2026-10-22T10:00:00.1234567Z",
        "2026-10-22T10:00:00.0000001Z",
        "2026-10-22T10:00:00.123456789Z",
        "2026-10-22T10:00:00.123456789+05:30",
        "2026-10-22T10:00:00.1234567-08:00",
        "2026-10-22T10:00:00.1234560Z",  # seven digits, the last one zero: still more than six
        "2026-10-22T10:00:00.1000000+00:00",
    ]
    for text in excess:
        with pytest.raises(ValidationError):
            adapter.validate_python(text)
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(text))
    # six digits or fewer are fine, so the rejection above is about the seventh digit
    for text in (
        "2026-10-22T10:00:00.123456Z",
        "2026-10-22T10:00:00.12345Z",
        "2026-10-22T10:00:00.1Z",
        "2026-10-22T10:00:00.123456+05:30",
    ):
        assert adapter.validate_python(text).utcoffset() == timedelta(0)

    # epoch values: numbers in Python, numbers in JSON, and numeric strings (no offset)
    for number in (1761127200, 1761127200.5, 1761127200123, 1761127200123456789, 0, -1, 1.0):
        with pytest.raises(ValidationError):
            adapter.validate_python(number)
    for lexeme in ("1761127200", "1761127200.5", "1761127200123", "0", "-1", "1.0", "1e9"):
        with pytest.raises(ValidationError):
            adapter.validate_json(lexeme)
    for text in ("1761127200", "1761127200.5", "0"):
        with pytest.raises(ValidationError):
            adapter.validate_python(text)
    for bad in (float("nan"), float("inf"), True, False):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
    for lexeme in ("true", "false", "null"):
        with pytest.raises(ValidationError):
            adapter.validate_json(lexeme)

    # a date is not an instant: choosing midnight would be an assumption
    for day in (date(2026, 10, 22), date.min, date.max):
        assert not isinstance(day, datetime)
        with pytest.raises(ValidationError):
            adapter.validate_python(day)
    with pytest.raises(ValidationError):
        adapter.validate_json('"2026-10-22"')
    # control: an aware datetime object is accepted unchanged
    aware = datetime(2026, 10, 22, 10, 0, 0, 123456, tzinfo=UTC)
    assert adapter.validate_python(aware) == aware


# --------------------------------------------------------------------------------- *_ms types


def _ms() -> TypeAdapter[Any]:
    return adapter("primitives", "Ms")


def test_ms_fields_reject_float_and_negative() -> None:
    adapter = _ms()
    for good in (0, 1, 1500, 4 * 3600 * 1000, 10**12):
        assert adapter.validate_python(good) == good
        assert adapter.validate_json(str(good)) == good
    for bad in (1.5, 1.0, 0.0, -1, -1000, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
    for lexeme in ("1.5", "1.0", "-1", "1e3", "null"):
        with pytest.raises(ValidationError):
            adapter.validate_json(lexeme)


def test_ms_rejects_bool_and_numeric_strings() -> None:
    """Proposed name. ``True`` must not become 1 ms and ``"5"`` must not become 5 ms."""
    adapter = _ms()
    for bad in (True, False, "5", "0", " 5", None):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
    for lexeme in ("true", '"12"', '"0"'):
        with pytest.raises(ValidationError):
            adapter.validate_json(lexeme)


def _is_nonneg_integer(schema: dict[str, Any], node: Any) -> bool:
    node = resolve_schema(schema, node)
    if not isinstance(node, dict):
        return False
    if node.get("type") == "integer":
        return bool(node.get("minimum") == 0)
    options = node.get("anyOf") or node.get("oneOf")
    if options:
        non_null = [o for o in options if resolve_schema(schema, o).get("type") != "null"]
        return len(non_null) == 1 and _is_nonneg_integer(schema, non_null[0])
    return False


def test_ms_field_names_are_integers_schema() -> None:
    schema = _ms().json_schema()
    assert schema["type"] == "integer"
    assert schema["minimum"] == 0
    # every property ending in _ms, in every model of the package, is a non-negative integer
    registry = api("registry")
    for model in registry.model_classes():
        for mode in ("validation", "serialization"):
            full = model.model_json_schema(mode=mode)
            nodes = [full, *full.get("$defs", {}).values()]
            for node in nodes:
                for name, prop in node.get("properties", {}).items():
                    if name.endswith("_ms"):
                        assert _is_nonneg_integer(full, prop), f"{model.__name__}.{name} ({mode})"


# -------------------------------------------------------------------------- DecimalString


def _dec() -> TypeAdapter[Any]:
    return adapter("primitives", "DecimalString")


def test_decimal_rejects_python_float() -> None:
    adapter = _dec()
    for bad in (1.5, 0.1, 0.0, -2.25, 1e-7, float("nan"), float("inf"), 1, 0, 10**30):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def test_decimal_rejects_json_number() -> None:
    adapter = _dec()
    for lexeme in ("1.5", "0.15", "1", "0", "-3", "1e3", "12345678901234567890", "1.0"):
        with pytest.raises(ValidationError):
            adapter.validate_json(lexeme)
    assert adapter.validate_json('"1.5"') == "1.5", "the same value as a JSON string is accepted"


@pytest.mark.parametrize("raw", ["1e3", "NaN", "Infinity", "-0", "+1", "1.", "-0.0"])
def test_decimal_rejects(raw: str) -> None:
    adapter = _dec()
    with pytest.raises(ValidationError):
        adapter.validate_python(raw)
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(raw))


def test_decimal_rejects_other_malformed_strings() -> None:
    """Proposed name. Adversarial A-13 neighbours: forms Python's Decimal() would accept."""
    adapter = _dec()
    for raw in (
        "",
        " ",
        " 1",
        "1 ",
        "1\n",
        "-Infinity",
        "inf",
        "nan",
        "sNaN",
        "1_000",
        "\u0661\u0662\u0663",  # Arabic-Indic digits
        "\uff11",  # fullwidth one
        "1,5",
        "--1",
        "1.2.3",
        "0x10",
        "1E+2",
        "1e-2",
        "0.1234567890123456789",  # scale 19 > 18
        "01",  # leading zeros
        "-01",
        "00",
        ".5",  # no integer part
        "-.5",
        "0.",  # trailing point
        "-1.",
        "+0.5",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(raw)


def test_decimal_scale_limit_is_18() -> None:
    """Proposed name. ADR 0014 Decision 1: scale <= 18."""
    adapter = _dec()
    assert adapter.validate_python("0.123456789012345678") == "0.123456789012345678"
    assert adapter.validate_python("-0.000000000000000001") == "-0.000000000000000001"
    assert adapter.validate_python("123456789.5") == "123456789.5"
    with pytest.raises(ValidationError):
        adapter.validate_python("0.1234567890123456789")


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("1.50", "1.5"),
        ("2.0", "2"),
        ("0.0", "0"),
        ("10.500", "10.5"),
        ("-1.250", "-1.25"),
        ("0.150", "0.15"),  # the example of E1 (user-accepted)
        ("3", "3"),
    ],
)
def test_decimal_canonical(raw: str, canonical: str) -> None:
    """Revision 7 (api-surface 15a item 5; ADR 0014 Decision 1; E1 accepted by the user).

    The type validates and never rewrites. Canonical input is returned unchanged ("3"); every
    non-canonical spelling of a valid number is rejected, in Python and JSON mode, with a message
    that names the input and its canonical spelling (for example
    ``decimal "1.50" is not in canonical form; write "1.5"``). Importers normalize source lexemes
    themselves, with a ``decimal_normalized`` warning (P1-29/P1-30)."""
    adapter = _dec()
    if raw == canonical:
        value = adapter.validate_python(raw)
        assert isinstance(value, str)
        assert value == canonical
        assert adapter.dump_python(value, mode="json") == canonical
        assert adapter.dump_json(value) == f'"{canonical}"'.encode()
        assert adapter.validate_json(json.dumps(raw)) == canonical
        return
    attempts: list[tuple[Callable[[Any], Any], str]] = [
        (adapter.validate_python, raw),
        (adapter.validate_json, json.dumps(raw)),
    ]
    for validate, payload in attempts:
        with pytest.raises(ValidationError) as caught:
            validate(payload)
        messages = [error["msg"] for error in caught.value.errors()]
        assert len(messages) == 1, messages
        message = messages[0]
        assert f'"{raw}"' in message, f"the message names the input {raw!r}: {message}"
        assert f'"{canonical}"' in message, f"the message names the canonical form: {message}"
        assert "canonical" in message.lower()
    assert adapter.validate_python(canonical) == canonical, "the canonical form is accepted"


def test_decimal_json_schema_is_string_with_canonical_pattern() -> None:
    """Proposed name (gate item 15: every amount is a string with a pattern in the JSON Schema).
    The pattern describes the canonical form only."""
    schema = _dec().json_schema()
    assert schema["type"] == "string"
    pattern = re.compile(schema["pattern"])
    for canonical in ("0", "1", "-1", "0.5", "-0.5", "1.5", "10.25", "0.00000015", "123456789.5"):
        assert pattern.search(canonical), canonical
    for bad in ("1e3", "NaN", "Infinity", "-0", "+1", "1.", "1.50", "2.0", "", " 1", "1 ", "abc"):
        assert not pattern.search(bad), bad
    assert not pattern.search("0.1234567890123456789")
    assert _jsonschema().Draft202012Validator(schema).is_valid("0.5")


# The architect's pattern (ADR 0014 Decision 1, revision 7), spelled out here on purpose: the
# generated JSON Schema must carry exactly this text, and pydantic must accept exactly the strings
# it matches. The ``$`` is matched as "end of text" (ECMAScript, and a full match in Python), so a
# trailing newline is not accepted.
ADR_DECIMAL_PATTERN = r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]{0,17}[1-9])$"
_CANONICAL = re.compile(r"-?(0|[1-9][0-9]*)(\.([0-9]*[1-9]))?", re.ASCII)


def _is_canonical_decimal(text: str) -> bool:
    """The rule of api-surface section 4, written differently from the pattern: canonical
    -?(0|[1-9][0-9]*)(.[0-9]*[1-9])?, ASCII digits, not "-0", at most 18 fractional digits."""
    match = _CANONICAL.fullmatch(text)
    if match is None or text == "-0":
        return False
    fraction = match.group(3)
    return fraction is None or len(match.group(2)) - 1 <= 18


def _ecma_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a JSON Schema pattern with ECMAScript end-of-text semantics for ``$``."""
    assert pattern.startswith("^")
    assert pattern.endswith("$")
    assert pattern.count("$") == 1
    return re.compile(pattern[:-1] + r"\Z")


def test_decimal_pattern_is_the_architects_pattern() -> None:
    """Proposed name. Both schema modes carry exactly the pattern of ADR 0014 Decision 1."""
    adapter = _dec()
    schemas = {
        "validation": adapter.json_schema(mode="validation"),
        "serialization": adapter.json_schema(mode="serialization"),
    }
    for mode, schema in schemas.items():
        assert schema["type"] == "string", mode
        assert schema["pattern"] == ADR_DECIMAL_PATTERN, mode
        assert "const" not in schema
    # the definition used by this module agrees with the pattern on the examples of api-surface
    pattern = _ecma_pattern(ADR_DECIMAL_PATTERN)
    for good in ("0", "-1", "0.5", "0.00000015", "123456789.5", "0.123456789012345678"):
        assert pattern.search(good), good
        assert _is_canonical_decimal(good), good
    for bad in ("-0", "1.", "1.50", "2.0", "+1", "1e3", "NaN", "", " 1", "1 ", "1\n"):
        assert not pattern.search(bad), repr(bad)
        assert not _is_canonical_decimal(bad), repr(bad)


def test_decimal_pydantic_and_json_schema_accept_the_same_set() -> None:
    """Proposed name (api-surface 15a item 5: no KNOWN_SCHEMA_GAPS entry is needed). Over every
    string of length 1..6 on the alphabet 0 1 5 9 . - (55,986 strings) plus targeted spellings,
    pydantic accepts a string if and only if the JSON Schema pattern matches it, and both agree
    with the canonical rule written independently of the pattern."""
    adapter = _dec()
    schema = adapter.json_schema()
    pattern = _ecma_pattern(schema["pattern"])
    validator = _jsonschema().Draft202012Validator(schema)
    targeted = [
        "0.123456789012345678",  # scale 18
        "0.1234567890123456789",  # scale 19
        "-0.000000000000000001",
        "123456789012345678901234567890.5",
        "1" * 40,
        "0." + "0" * 17 + "1",
        "0." + "0" * 18 + "1",
        "1e3",
        "1E3",
        "NaN",
        "Infinity",
        "-Infinity",
        "+1",
        "1_0",
        "\u0661",
        "\uff11",
        "1,5",
        "0x1",
        " 1",
        "1 ",
        "",
    ]
    corpus = [
        "".join(chars)
        for length in range(1, 7)
        for chars in itertools.product("0159.-", repeat=length)
    ]
    corpus.extend(targeted)
    disagreements: list[tuple[str, bool, bool, bool]] = []
    for text in corpus:
        try:
            adapter.validate_python(text)
            by_pydantic = True
        except ValidationError:
            by_pydantic = False
        by_pattern = pattern.search(text) is not None
        by_rule = _is_canonical_decimal(text)
        if not by_pydantic == by_pattern == by_rule:
            disagreements.append((text, by_pydantic, by_pattern, by_rule))
    assert not disagreements, f"(text, pydantic, pattern, rule): {disagreements[:10]}"
    # the real jsonschema validator agrees on a spread of the corpus (it needs no trailing newline
    # cases: Python's re.search lets "$" match before one)
    for text in corpus[:: len(corpus) // 4000 or 1]:
        assert validator.is_valid(text) is (pattern.search(text) is not None), repr(text)
    for text in ("0", "-1", "0.5", "1.50", "2.0", "-0", "1.", "+1", "1e3"):
        assert validator.is_valid(text) is _is_canonical_decimal(text), text
        try:
            adapter.validate_python(text)
            accepted = True
        except ValidationError:
            accepted = False
        assert accepted is validator.is_valid(text), text
    # a trailing newline: not accepted by pydantic, not matched by the ECMAScript pattern
    for text in ("1\n", "0.5\n", "\n"):
        assert pattern.search(text) is None
        with pytest.raises(ValidationError):
            adapter.validate_python(text)


# ----------------------------------------------------------------------------- SupportedCurrency


def _iso_rows() -> dict[str, dict[str, Any]]:
    return {r["code"]: r for r in data_json("iso4217_active.json")["currencies"]}


def _money() -> Any:
    return api("primitives").Money


def _amount() -> str:
    return "1"


@pytest.mark.parametrize(
    "currency", ["usd", "EUR", "INR", "XAF", "XXX", "XAU", "BOV", "CLF", "ABC", ""]
)
def test_money_rejects_non_usd(currency: str) -> None:
    money = _money()
    with pytest.raises(ValidationError):
        money(amount=_amount(), currency=currency)
    with pytest.raises(ValidationError):
        money.model_validate_json(json.dumps({"amount": _amount(), "currency": currency}))
    # the same input with USD is valid, so the failure is about the currency
    assert money(amount=_amount(), currency="USD").currency == "USD"


def test_money_rejects_every_non_usd_iso_code() -> None:
    money = _money()
    rows = _iso_rows()
    assert len(rows) == 178
    assert "USD" in rows
    accepted = []
    for code in rows:
        if code == "USD":
            continue
        try:
            money(amount=_amount(), currency=code)
        except ValidationError:
            continue
        accepted.append(code)
    assert not accepted, f"non-USD ISO 4217 codes accepted by Money: {accepted}"
    assert money(amount=_amount(), currency="USD").currency == "USD"


def test_money_currency_required_and_strictly_typed() -> None:
    """Proposed name. ADR 0014 Decision 1: ``currency`` is kept, not implied, and is exact."""
    money = _money()
    with pytest.raises(ValidationError):
        money(amount="1")
    for bad in (None, 840, ["USD"], " USD", "USD ", "USD\n", "U SD", "$", "US$"):
        with pytest.raises(ValidationError):
            money(amount="1", currency=bad)
    with pytest.raises(ValidationError):
        money(amount="1", currency="USD", extra_field=1)


def test_supported_currency_set_is_data_driven(tmp_path: Path) -> None:
    primitives = api("primitives")
    shipped = data_json("supported_currencies.json")["currencies"]
    assert shipped == ["USD"]
    assert tuple(primitives.load_supported_currencies()) == ("USD",)

    # the shipped types: validator and JSON Schema enum
    money = primitives.Money
    schema = money.model_json_schema()
    currency = resolve_schema(schema, schema["properties"]["currency"])
    assert currency["enum"] == ["USD"]
    currency_adapter = adapter("primitives", "SupportedCurrency")
    assert currency_adapter.validate_python("USD") == "USD"
    with pytest.raises(ValidationError):
        currency_adapter.validate_python("EUR")
    enum_schema = currency_adapter.json_schema()
    assert resolve_schema(enum_schema, enum_schema)["enum"] == ["USD"]

    # a test-only file with an extra code changes both the validator and the enum
    path = tmp_path / "supported_currencies.json"
    path.write_text(
        json.dumps({"_meta": {"description": "test-only"}, "currencies": ["USD", "EUR"]}),
        encoding="utf-8",
    )
    codes = tuple(primitives.load_supported_currencies(path))
    assert codes == ("USD", "EUR")
    wider = primitives.make_money_model(codes)
    assert wider(amount="1", currency="EUR").currency == "EUR"
    assert wider(amount="1", currency="USD").currency == "USD"
    with pytest.raises(ValidationError):
        wider(amount="1", currency="INR")
    wide_schema = wider.model_json_schema()
    assert resolve_schema(wide_schema, wide_schema["properties"]["currency"])["enum"] == [
        "USD",
        "EUR",
    ]
    # ... and the shipped Money is unchanged by building another one
    with pytest.raises(ValidationError):
        money(amount="1", currency="EUR")
    assert resolve_schema(
        money.model_json_schema(), money.model_json_schema()["properties"]["currency"]
    )["enum"] == ["USD"]


def _schema_node(schema: dict[str, Any], model_or_adapter_schema: Any) -> Any:
    return resolve_schema(schema, model_or_adapter_schema)


def _find_const(node: Any, where: str = "") -> list[str]:
    """Paths of every ``const`` keyword in a JSON Schema (a property NAMED const is not one)."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "const":
                found.append(f"{where}/const")
            elif key in ("properties", "$defs", "patternProperties"):
                for name, child in (value or {}).items():
                    found.extend(_find_const(child, f"{where}/{key}/{name}"))
            else:
                found.extend(_find_const(value, f"{where}/{key}"))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found.extend(_find_const(child, f"{where}/{index}"))
    return found


def test_closed_value_sets_emit_enum_not_const_in_both_schema_modes() -> None:
    """Proposed name. ADR 0003 Decision 6 (revision 7): every closed value set, including a
    single-member one, is ``{"type": "string", "enum": [...]}`` in the validation AND the
    serialization schema, never pydantic's ``{"const": ...}`` for a one-member Literal. The
    validator and the enum come from the same tuple, so pydantic and the schema accept the same
    strings."""
    primitives = api("primitives")
    iso_codes = [*_iso_rows(), "usd", " USD", "USD ", "USD\n", "", "ABC", "US", "$"]
    cases = [
        ("shipped", ("USD",), primitives.Money),
        ("wider", ("USD", "EUR"), primitives.make_money_model(("USD", "EUR"))),
        ("single", ("INR",), primitives.make_money_model(("INR",))),
    ]
    for label, codes, model in cases:
        for mode in SCHEMA_MODES:
            schema = model.model_json_schema(mode=mode)
            node = _schema_node(schema, schema["properties"]["currency"])
            assert node["type"] == "string", (label, mode)
            assert node["enum"] == list(codes), (label, mode)
            assert "const" not in node, (label, mode)
            assert _find_const(schema) == [], (label, mode, _find_const(schema))
        # pydantic accepts a currency if and only if the enum lists it
        validator = _jsonschema().Draft202012Validator(
            _schema_node(
                model.model_json_schema(),
                model.model_json_schema()["properties"]["currency"],
            )
        )
        for code in iso_codes:
            try:
                model(amount="1", currency=code)
                accepted = True
            except ValidationError:
                accepted = False
            assert accepted is (code in codes), (label, code)
            assert validator.is_valid(code) is (code in codes), (label, code)
    # the standalone type, built from the shipped file
    currency = adapter("primitives", "SupportedCurrency")
    for mode, schema in (
        ("validation", currency.json_schema(mode="validation")),
        ("serialization", currency.json_schema(mode="serialization")),
    ):
        node = resolve_schema(schema, schema)
        assert (node["type"], node["enum"]) == ("string", ["USD"]), mode
        assert "const" not in node, mode


def test_no_const_in_generated_schemas() -> None:
    """SCOPE name (Schema and isolation, C; ADR 0003 Decision 6). No generated schema of any model
    or primitive contains ``const``. The walk over the registry becomes meaningful as models
    arrive; today it covers Money, ContentDigest and the primitives."""
    registry = api("registry")
    primitives = api("primitives")
    digest = api("digest")
    models = [*registry.model_classes(), primitives.Money, digest.ContentDigest, digest.KeyRef]
    offenders: dict[str, list[str]] = {}
    for model in models:
        for mode in SCHEMA_MODES:
            found = _find_const(model.model_json_schema(mode=mode))
            if found:
                offenders[f"{model.__name__}/{mode}"] = found
    for name in ("UtcDatetime", "Ms", "DecimalString", "SupportedCurrency"):
        primitive = adapter("primitives", name)
        for mode in SCHEMA_MODES:
            found = _find_const(primitive.json_schema(mode=mode))
            if found:
                offenders[f"{name}/{mode}"] = found
    assert not offenders, f"const found in generated schemas: {offenders}"


def test_load_supported_currencies_rejects_code_missing_from_iso4217(tmp_path: Path) -> None:
    """Proposed name. ADR 0014 Decision 2: every listed code must exist in List One."""
    primitives = api("primitives")
    errors = api("errors")
    for bad in (["USD", "ZZZ"], ["usd"], ["USD", "USD"], [], ["US"], "USD", [840]):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"_meta": {}, "currencies": bad}), encoding="utf-8")
        with pytest.raises((errors.DataFileError, ValueError)):
            primitives.load_supported_currencies(path)


@pytest.mark.parametrize("code", ["INR", "XAF", "ABC"])
def test_non_usd_error_names_iso_currency(code: str) -> None:
    money = _money()
    rows = _iso_rows()
    with pytest.raises(ValidationError) as caught:
        money(amount="1", currency=code)
    message = str(caught.value)
    assert code in message
    if code in rows:
        assert rows[code]["name"] in message, "the ISO 4217 name is taken from the vendored file"
        assert "USD" in message
        assert "not supported" in message
        assert "not an ISO 4217 code" not in message
    else:
        assert "not an ISO 4217 code" in message
        assert "USD" in message


# ------------------------------------------------------------------------------------ Money


def test_money_roundtrip_small() -> None:
    money = _money()
    value = money(amount="0.00000015", currency="USD")
    assert value.amount == "0.00000015"
    wire = value.model_dump_json()
    assert json.loads(wire) == {"amount": "0.00000015", "currency": "USD"}
    again = money.model_validate_json(wire)
    assert again == value
    assert again.amount == "0.00000015"
    assert money.model_validate(value.model_dump(mode="json")) == value
    # floats and JSON numbers for the amount are rejected
    with pytest.raises(ValidationError):
        money.model_validate_json('{"amount": 0.00000015, "currency": "USD"}')
    with pytest.raises(ValidationError):
        money(amount=1.5, currency="USD")


def test_money_may_be_negative_but_stays_canonical() -> None:
    """Proposed name. ADR 0014 Decision 7 (P-38): Money may be negative in general. Revision 7:
    the amount must be written canonically ("2.50" is rejected, naming "2.5"); it is never
    rewritten."""
    money = _money()
    assert money(amount="-1.5", currency="USD").amount == "-1.5"
    assert money(amount="2.5", currency="USD").amount == "2.5"
    assert money(amount="-0.000000000000000001", currency="USD").amount == "-0.000000000000000001"
    for non_canonical, canonical in (("2.50", "2.5"), ("-2.50", "-2.5"), ("0.0", "0")):
        with pytest.raises(ValidationError) as caught:
            money(amount=non_canonical, currency="USD")
        assert any(f'"{canonical}"' in e["msg"] for e in caught.value.errors()), non_canonical
        with pytest.raises(ValidationError):
            money.model_validate_json(json.dumps({"amount": non_canonical, "currency": "USD"}))
    with pytest.raises(ValidationError):
        money(amount="-0", currency="USD")


def test_money_add_exact() -> None:
    money = _money()
    total = money(amount="0.1", currency="USD") + money(amount="0.2", currency="USD")
    assert isinstance(total, money)
    assert total.amount == "0.3", "0.1 + 0.2 must be exactly 0.3, never 0.30000000000000004"
    assert total.currency == "USD"
    tiny = money(amount="0.00000015", currency="USD") + money(amount="0.00000015", currency="USD")
    assert tiny.amount == "0.0000003"
    zero = money(amount="0.1", currency="USD") + money(amount="-0.1", currency="USD")
    assert zero.amount == "0", "a zero sum is '0', never '-0' or '0.0'"
    big = money(amount="123456789012345678.123456789012345678", currency="USD") + money(
        amount="0.000000000000000002", currency="USD"
    )
    assert big.amount == "123456789012345678.12345678901234568"
    with pytest.raises(TypeError):
        money(amount="1", currency="USD") + 1


def test_display_rounding_does_not_mutate_amount() -> None:
    money = _money()
    value = money(amount="0.00000015", currency="USD")
    shown = value.display(2)
    assert isinstance(shown, str)
    assert shown == "0.00"
    assert value.display(8) == "0.00000015"
    assert value.amount == "0.00000015", "display rounding is presentation only"
    assert value.model_dump() == {"amount": "0.00000015", "currency": "USD"}
    other = money(amount="12.3456", currency="USD")
    assert other.display(2) == "12.35"
    assert other.amount == "12.3456"


@pytest.mark.parametrize(
    ("amount", "places", "shown"),
    [
        # ties go to the even digit (ROUND_HALF_EVEN), up and down
        ("0.125", 2, "0.12"),
        ("0.135", 2, "0.14"),
        ("0.145", 2, "0.14"),
        ("0.155", 2, "0.16"),
        ("1.005", 2, "1.00"),
        ("1.015", 2, "1.02"),
        ("2.675", 2, "2.68"),
        ("-0.125", 2, "-0.12"),
        ("-0.135", 2, "-0.14"),
        ("0.5", 0, "0"),
        ("1.5", 0, "2"),
        ("2.5", 0, "2"),
        ("3.5", 0, "4"),
        ("12.5", 0, "12"),
        ("13.5", 0, "14"),
        ("-1.5", 0, "-2"),
        ("-2.5", 0, "-2"),
        ("0.00000015", 7, "0.0000002"),
        ("0.00000025", 7, "0.0000002"),
        ("-0.00000015", 7, "-0.0000002"),
        # not ties: the ordinary direction
        ("0.126", 2, "0.13"),
        ("0.124", 2, "0.12"),
        ("12.3456", 2, "12.35"),
        # exactly `places` fractional digits, zero-padded, never an exponent
        ("1", 2, "1.00"),
        ("0", 2, "0.00"),
        ("0", 0, "0"),
        ("1.5", 4, "1.5000"),
        ("7", 0, "7"),
        ("0.000000000000000001", 18, "0.000000000000000001"),
        ("0.000000000000000001", 2, "0.00"),
        ("1000000", 2, "1000000.00"),
        ("-3", 1, "-3.0"),
        # exact arithmetic, not limited to the default 28-digit context
        ("123456789012345678.123456789012345678", 2, "123456789012345678.12"),
        ("123456789012345678.123456789012345678", 18, "123456789012345678.123456789012345678"),
        ("123456789012345678.123456789012345678", 20, "123456789012345678.12345678901234567800"),
        ("123456789012345678.125", 2, "123456789012345678.12"),
    ],
)
def test_money_display_rounds_half_even_with_exact_digits(
    amount: str, places: int, shown: str
) -> None:
    """Revision 7 (api-surface 15a item 8; ADR 0014 Decision 6): ``Money.display(places)`` rounds
    half-even, shows exactly ``places`` fractional digits (none for 0) and never an exponent, and
    leaves the stored amount alone."""
    money = _money()
    value = money(amount=amount, currency="USD")
    assert value.display(places) == shown
    assert value.amount == amount, "display rounding is presentation only"
    assert value.display(places) == shown, "deterministic"


def test_money_display_default_places_and_argument_checks() -> None:
    """Proposed name. ``display(places: int = 2)``; ``places`` must be an int >= 0."""
    money = _money()
    value = money(amount="12.3456", currency="USD")
    assert value.display() == "12.35"
    assert value.display() == value.display(2)
    assert isinstance(value.display(0), str)
    for bad in (-1, -10, 2.0, 1.5, "2", None, [2]):
        with pytest.raises((TypeError, ValueError)):
            value.display(bad)
    assert value.amount == "12.3456"


def test_money_add_returns_canonical_amounts() -> None:
    """Proposed name. ADR 0014 Decision 1: results of our own arithmetic are produced in canonical
    form (revision 7), so a sum never needs a non-canonical spelling."""
    money = _money()
    for left, right, total in (
        ("1.5", "1.5", "3"),
        ("0.1", "0.9", "1"),
        ("1.25", "-1.25", "0"),
        ("-0.5", "-0.25", "-0.75"),
        ("0.000000000000000001", "0.000000000000000009", "0.00000000000000001"),
        ("100", "200", "300"),
        ("-0.5", "0.5", "0"),
    ):
        result = money(amount=left, currency="USD") + money(amount=right, currency="USD")
        assert result.amount == total, (left, right)
        assert adapter("primitives", "DecimalString").validate_python(result.amount) == total
        assert result == money(amount=total, currency="USD"), "equal by value"
    # the operands are value objects and are not changed by addition
    first = money(amount="0.1", currency="USD")
    second = money(amount="0.2", currency="USD")
    first + second
    assert (first.amount, second.amount) == ("0.1", "0.2")
