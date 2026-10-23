# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Time, duration, decimal and money types (ADR 0002, ADR 0014).

- ``UtcDatetime``: an aware instant, stored in UTC, serialized ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.
- ``Ms``: a non-negative integer number of milliseconds (offsets and durations).
- ``DecimalString``: an exact decimal in canonical text form; never a float, never rewritten.
- ``SupportedCurrency`` and ``Money``: USD only in 0.0.1, read from
  ``data/supported_currencies.json``; ``make_money_model()`` builds the same model for another
  code list (the data-driven seam).

Inside a model that hides its input (``_hiding.values_hidden()``) no message quotes the rejected
value (ADR 0007 Decision 8 (c), finding N42); on their own (a bare ``TypeAdapter``) the messages
keep naming the value, for example the canonical spelling of a decimal.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, Inexact
from pathlib import Path
from typing import Annotated, Any, NoReturn

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    Strict,
    create_model,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema._constraints import ClosedSet, echoed, echoed_text
from tracer_evalschema._hiding import values_hidden
from tracer_evalschema.data_loader import package_data, read_json_file
from tracer_evalschema.errors import DataFileError

__all__ = [
    "DECIMAL_PATTERN",
    "DecimalString",
    "Money",
    "Ms",
    "SupportedCurrency",
    "UtcDatetime",
    "canonical_decimal_text",
    "iso4217_currency_name",
    "load_supported_currencies",
    "make_money_model",
]

# ------------------------------------------------------------------------------- UtcDatetime

# RFC 3339 date-time with a mandatory offset, at most six fractional digits, and seconds 00-59
# (a leap second cannot be represented by datetime, so it is rejected, not clamped).
UTC_DATETIME_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"
)
_UTC_TEXT = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})T(?P<h>[01][0-9]|2[0-3]):(?P<m>[0-5][0-9])"
    r":(?P<s>[0-5][0-9])(?:\.(?P<frac>[0-9]{1,6}))?"
    r"(?P<offset>Z|(?P<sign>[+-])(?P<oh>[01][0-9]|2[0-3]):(?P<om>[0-5][0-9]))"
)


def _invalid_instant(value: object, why: str) -> PydanticCustomError:
    if values_hidden():
        return PydanticCustomError("utc_datetime", "not a UTC instant: {why}", {"why": why})
    return PydanticCustomError(
        "utc_datetime", "{value} is not a UTC instant: {why}", {"value": echoed(value), "why": why}
    )


def _parse_instant(text: str) -> datetime:
    match = _UTC_TEXT.fullmatch(text)
    if match is None:
        raise _invalid_instant(
            text,
            "write YYYY-MM-DDTHH:MM:SS[.ffffff] with Z or an offset, at most six fractional "
            "digits, seconds 00-59",
        )
    offset = timedelta(0)
    if match["sign"] is not None:
        offset = timedelta(hours=int(match["oh"]), minutes=int(match["om"]))
        if match["sign"] == "-":
            offset = -offset
    try:
        year, month, day = (int(part) for part in match["date"].split("-"))
        local = datetime(
            year,
            month,
            day,
            int(match["h"]),
            int(match["m"]),
            int(match["s"]),
            int((match["frac"] or "0").ljust(6, "0")),
            tzinfo=timezone(offset),
        )
        return local.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise _invalid_instant(text, str(exc)) from None


def _to_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise _invalid_instant(value, "a naive datetime has no offset; UTC is never assumed")
        return value.astimezone(UTC)
    if isinstance(value, str):
        return _parse_instant(value)
    raise _invalid_instant(
        value, "only an aware datetime or an RFC 3339 string is accepted (no epoch, no date)"
    )


def _format_instant(value: datetime) -> str:
    # Explicit zero padding: strftime("%Y") is platform dependent for years below 1000
    # (glibc writes "1", macOS writes "0001"), and the output must match UTC_DATETIME_PATTERN.
    utc = value.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}"
        f"T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}.{utc.microsecond:06d}Z"
    )


class _UtcDatetimeRule:
    """Validation, serialization and JSON Schema of ``UtcDatetime`` (ADR 0002 Decision 1)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _to_utc,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _format_instant, when_used="json"
            ),
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "format": "date-time", "pattern": UTC_DATETIME_PATTERN}


UtcDatetime = Annotated[datetime, _UtcDatetimeRule()]
"""An aware instant: any offset is converted to UTC; naive values, epoch numbers, dates, leap
seconds and more than six fractional digits are rejected, never guessed or rounded."""


# --------------------------------------------------------------------------------------- Ms

Ms = Annotated[int, Strict(), Field(ge=0)]
"""Milliseconds as a JSON integer >= 0 (offsets and durations, never wall-clock values)."""


# ---------------------------------------------------------------------------- DecimalString

DECIMAL_PATTERN = r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]{0,17}[1-9])$"
"""The canonical decimal form (ADR 0014 Decision 1): no exponent, no trailing fractional zeros
or point, no ``-0``, no ``+``, at most 18 fractional digits, ASCII digits only."""

_DECIMAL_MAX_SCALE = 18
_CANONICAL_DECIMAL = re.compile(DECIMAL_PATTERN[1:-1])
# A plain decimal that differs from the canonical form only lexically (trailing fractional zeros,
# a trailing point, negative zero): these are reported with their canonical spelling.
_LEXICAL_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*)?")


def canonical_decimal_text(value: Decimal) -> str:
    """The canonical text of a finite ``Decimal`` (no exponent, trailing zeros or ``-0``)."""
    if not value.is_finite():
        raise ValueError(f"not a finite decimal: {value}")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "0") else text


def _lexical_canonical_form(text: str) -> str:
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _check_decimal(text: str) -> str:
    if _CANONICAL_DECIMAL.fullmatch(text) is not None:
        return text
    if values_hidden():
        _reject_decimal_quietly(text)
    if _LEXICAL_DECIMAL.fullmatch(text) is not None:
        canonical = _lexical_canonical_form(text)
        if _CANONICAL_DECIMAL.fullmatch(canonical) is not None:
            raise PydanticCustomError(
                "decimal_not_canonical",
                'decimal "{value}" is not in canonical form; write "{canonical}"',
                {"value": echoed_text(text), "canonical": echoed_text(canonical)},
            )
        raise PydanticCustomError(
            "decimal_scale",
            'decimal "{value}" has more than {max_scale} fractional digits',
            {"value": echoed_text(text), "max_scale": _DECIMAL_MAX_SCALE},
        )
    raise PydanticCustomError(
        "decimal_string",
        "{value} is not a decimal string (canonical form {pattern})",
        {"value": echoed(text), "pattern": DECIMAL_PATTERN},
    )


def _reject_decimal_quietly(text: str) -> NoReturn:
    """The rejection inside a model that hides its input: the rule and the form, no value and no
    canonical spelling (which would reveal the value)."""
    if _LEXICAL_DECIMAL.fullmatch(text) is not None:
        if _CANONICAL_DECIMAL.fullmatch(_lexical_canonical_form(text)) is not None:
            raise PydanticCustomError(
                "decimal_not_canonical",
                "the decimal is not in canonical form (no trailing fractional zeros, no trailing "
                "point, no -0); write the canonical spelling",
            )
        raise PydanticCustomError(
            "decimal_scale",
            "the decimal has more than {max_scale} fractional digits",
            {"max_scale": _DECIMAL_MAX_SCALE},
        )
    raise PydanticCustomError(
        "decimal_string",
        "not a decimal string (canonical form {pattern})",
        {"pattern": DECIMAL_PATTERN},
    )


class _DecimalRule:
    """Validation and JSON Schema of ``DecimalString``: exactly the strings of the pattern."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            _check_decimal, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": DECIMAL_PATTERN}


DecimalString = Annotated[str, _DecimalRule()]
"""An exact decimal as canonical text. Non-canonical spellings are rejected with the canonical
spelling in the message; normalizing a source lexeme is the importer's job (ADR 0014 1a)."""


def _exact_context(*texts: str) -> Context:
    """A decimal context precise enough for exact sums and roundings of these numbers."""
    return Context(prec=sum(len(text) for text in texts) + 2, rounding=ROUND_HALF_EVEN)


# ------------------------------------------------------------------------- SupportedCurrency


def _iso4217_names() -> dict[str, str]:
    rows = package_data("iso4217_active.json")["currencies"]
    return {row["code"]: row["name"] for row in rows}


def iso4217_currency_name(code: str) -> str | None:
    """The ISO 4217 List One name of ``code`` (exact match, ``INR`` -> ``Indian Rupee``), or
    ``None`` for text that is no code of the vendored file. Used only to name a code in messages
    and warnings (ADR 0014 Decision 3); it never widens what ``Money`` accepts."""
    return _iso4217_names().get(code)


def load_supported_currencies(path: Path | None = None) -> tuple[str, ...]:
    """Read the closed currency list (``currencies`` key) in file order.

    ``path`` defaults to the shipped ``supported_currencies.json``. Raises ``DataFileError`` for
    an empty list, a duplicate, a code that is not three upper-case ASCII letters, or a code
    missing from the shipped ``iso4217_active.json`` (ADR 0014 Decision 2).
    """
    document = package_data("supported_currencies.json") if path is None else read_json_file(path)
    source = "supported_currencies.json" if path is None else str(path)
    codes = document.get("currencies") if isinstance(document, dict) else None
    if not isinstance(codes, list) or not codes:
        raise DataFileError(f"{source}: 'currencies' must be a non-empty list")
    iso = _iso4217_names()
    for code in codes:
        if not isinstance(code, str) or re.fullmatch("[A-Z]{3}", code) is None:
            raise DataFileError(f"{source}: {code!r} is not an ISO 4217 alphabetic code")
        if code not in iso:
            raise DataFileError(f"{source}: {code} is not in iso4217_active.json")
    if len(set(codes)) != len(codes):
        raise DataFileError(f"{source}: duplicate currency codes in {codes}")
    return tuple(codes)


def _currency_set(codes: Sequence[str]) -> ClosedSet:
    names = _iso4217_names()

    def reject(value: str, allowed: tuple[str, ...]) -> NoReturn:
        only = ", ".join(allowed)
        if values_hidden():
            raise PydanticCustomError(
                "currency_not_supported",
                "the currency is not supported in {version}; only {only}",
                {"version": SCHEMA_VERSION, "only": only},
            )
        if value in names:
            raise PydanticCustomError(
                "currency_not_supported",
                "currency {code} ({name}) is not supported in {version}; only {only}",
                {"code": value, "name": names[value], "version": SCHEMA_VERSION, "only": only},
            )
        raise PydanticCustomError(
            "currency_not_iso4217",
            "currency {code} is not an ISO 4217 code; only {only}",
            {"code": echoed(value), "only": only},
        )

    return ClosedSet(tuple(codes), reject)


_SUPPORTED_CURRENCIES = load_supported_currencies()

SupportedCurrency = Annotated[str, _currency_set(_SUPPORTED_CURRENCIES)]
"""A currency code of the shipped closed list (``USD`` in 0.0.1)."""


# ------------------------------------------------------------------------------------- Money


class Money(BaseModel):
    """An exact amount of money: ``{amount, currency}``, both required (ADR 0014 Decision 1).

    Equal by value. ``+`` adds exactly and returns the canonical amount; ``display()`` rounds for
    presentation only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: DecimalString
    currency: SupportedCurrency

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money) or type(other) is not type(self):
            return NotImplemented
        if other.currency != self.currency:
            raise ValueError(f"cannot add {other.currency} to {self.currency} (no conversion)")
        context = _exact_context(self.amount, other.amount)
        context.traps[Inexact] = True
        total = context.add(Decimal(self.amount), Decimal(other.amount))
        return type(self)(amount=canonical_decimal_text(total), currency=self.currency)

    def display(self, places: int = 2) -> str:
        """The amount rounded half-even to exactly ``places`` fractional digits.

        Presentation only: ``amount`` is not changed. A result that rounds to zero is shown
        without a minus sign.
        """
        if type(places) is not int:
            raise TypeError(f"places must be an int, got {type(places).__name__}")
        if places < 0:
            raise ValueError(f"places must be >= 0, got {places}")
        context = _exact_context(self.amount, "0" * places)
        shown = Decimal(self.amount).quantize(Decimal(f"1e-{places}"), context=context)
        if shown.is_zero():
            shown = shown.copy_abs()
        return format(shown, "f")


def make_money_model(codes: Sequence[str], name: str = "Money") -> type[Money]:
    """A ``Money`` model whose ``currency`` accepts exactly ``codes`` (validator and enum)."""
    model: type[Money] = create_model(
        name,
        __base__=Money,
        __module__=__name__,
        currency=(Annotated[str, _currency_set(codes)], ...),
    )
    return model
