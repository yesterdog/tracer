# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Column types of the store: the same value is stored the same way on SQLite and Postgres.

Each type accepts exactly one Python type and raises ``ValueError`` (surfacing from SQLAlchemy as
``StatementError``) for anything else; nothing is coerced. ``None`` is NULL. Messages are fixed
text and never hold the value. Types that need a CHECK constraint (UUID version, digest length,
boolean domain) attach it to the table when the column is added to one.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.visitors import InternalTraversal
from sqlalchemy.types import TypeDecorator, TypeEngine

__all__ = [
    "BigIntCol",
    "BoolCol",
    "DecimalCol",
    "DocCol",
    "InstantCol",
    "KeyCol",
    "Sha256Col",
    "UuidCol",
]

_INSTANT_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{6})Z", re.ASCII)


# --------------------------------------------------------------------------- CHECK expressions
#
# The same statement is written in each backend's SQL. They are custom elements because the
# expressions differ (SQLite reads a UUID as a 16-byte BLOB, Postgres as a uuid).


class _CheckElement(ColumnElement[bool]):
    inherit_cache = False
    type = sa.Boolean()
    # Lets SQLAlchemy find the column inside the CHECK (a type-bound constraint needs it).
    _traverse_internals = [("column", InternalTraversal.dp_clauseelement)]  # noqa: RUF012

    def __init__(self, column: ColumnElement[Any]) -> None:
        self.column = column


class _UuidV7(_CheckElement):
    """The version nibble of the UUID is 7 (SQLite: also exactly 16 bytes)."""


class _Sha256Length(_CheckElement):
    """The value is exactly 32 bytes."""


class _BoolDomain(_CheckElement):
    """The value is a boolean (SQLite stores 0 or 1)."""


class _InstantShape(_CheckElement):
    """SQLite only: the fixed-width ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` text."""


class _DecimalShape(_CheckElement):
    """SQLite only: plain decimal text (digits, one inner ``.``, an optional leading ``-``)."""


@compiles(_UuidV7, "sqlite")
def _uuid_v7_sqlite(element: _UuidV7, compiler: SQLCompiler, **kw: Any) -> str:
    col = compiler.process(element.column, **kw)
    # hex(NULL) is '' and not NULL, so NULL is allowed explicitly
    return f"({col} IS NULL OR (length({col}) = 16 AND substr(hex({col}), 13, 1) = '7'))"


@compiles(_UuidV7, "postgresql")
def _uuid_v7_postgresql(element: _UuidV7, compiler: SQLCompiler, **kw: Any) -> str:
    return f"substr({compiler.process(element.column, **kw)}::text, 15, 1) = '7'"


@compiles(_Sha256Length, "sqlite")
def _sha256_sqlite(element: _Sha256Length, compiler: SQLCompiler, **kw: Any) -> str:
    return f"length({compiler.process(element.column, **kw)}) = 32"


@compiles(_Sha256Length, "postgresql")
def _sha256_postgresql(element: _Sha256Length, compiler: SQLCompiler, **kw: Any) -> str:
    return f"octet_length({compiler.process(element.column, **kw)}) = 32"


@compiles(_BoolDomain, "sqlite")
def _bool_sqlite(element: _BoolDomain, compiler: SQLCompiler, **kw: Any) -> str:
    return f"{compiler.process(element.column, **kw)} IN (0, 1)"


@compiles(_BoolDomain, "postgresql")
def _bool_postgresql(element: _BoolDomain, compiler: SQLCompiler, **kw: Any) -> str:
    return f"{compiler.process(element.column, **kw)} IN (TRUE, FALSE)"


_DIGIT = "[0-9]"
_INSTANT_GLOB = (
    f"{_DIGIT * 4}-[0-1]{_DIGIT}-[0-3]{_DIGIT}T[0-2]{_DIGIT}:[0-5]{_DIGIT}:[0-5]{_DIGIT}."
    f"{_DIGIT * 6}Z"
)


@compiles(_InstantShape, "sqlite")
def _instant_sqlite(element: _InstantShape, compiler: SQLCompiler, **kw: Any) -> str:
    col = compiler.process(element.column, **kw)
    return f"length({col}) = 27 AND {col} GLOB '{_INSTANT_GLOB}'"


@compiles(_DecimalShape, "sqlite")
def _decimal_sqlite(element: _DecimalShape, compiler: SQLCompiler, **kw: Any) -> str:
    col = compiler.process(element.column, **kw)
    return (
        f"({col} GLOB '[0-9]*' OR {col} GLOB '-[0-9]*') AND {col} GLOB '*[0-9]' "
        f"AND {col} NOT GLOB '*[^0-9.-]*' AND {col} NOT GLOB '?*-*' AND {col} NOT GLOB '*.*.*'"
    )


_SQLITE_ONLY = (_InstantShape, _DecimalShape)


@event.listens_for(sa.Column, "after_parent_attach")
def _attach_type_check(column: sa.Column[Any], parent: Any) -> None:
    """Add the CHECK of a column type to its table (once, whatever order a table is built in)."""
    check = getattr(column.type, "check", None)
    if check is None or not isinstance(parent, sa.Table):
        return
    element_type, suffix = check
    name = f"{column.name}_{suffix}"
    if any(c.name == name for c in parent.constraints):
        return
    # _type_bound: Table.to_metadata() copies the columns, which attach this check again, so the
    # copy of the constraint itself must be skipped (SQLAlchemy's Enum and Boolean do the same).
    constraint = sa.CheckConstraint(element_type(column), name=name, _type_bound=True)
    if element_type in _SQLITE_ONLY:
        constraint = constraint.ddl_if(dialect="sqlite")
    parent.append_constraint(constraint)


# ------------------------------------------------------------------------------------ helpers


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("a str is required")
    if "\x00" in value:
        raise ValueError("U+0000 cannot be stored")
    if not value.isascii():
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("a lone surrogate cannot be stored") from None
    return value


class _Strict(TypeDecorator[Any]):
    check: ClassVar[tuple[Callable[[ColumnElement[Any]], ColumnElement[bool]], str] | None] = None


# ------------------------------------------------------------------------------------- types


class UuidCol(_Strict):
    """A ``uuid.UUID`` of version 7. Postgres ``uuid``; SQLite ``BLOB`` of the 16 bytes (their
    byte order is the time order of a UUIDv7)."""

    impl = sa.LargeBinary
    cache_ok = True
    check = (_UuidV7, "uuid_v7")

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=True))
        return dialect.type_descriptor(sa.LargeBinary())

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        if value is None:
            return None
        if type(value) is not uuid.UUID:
            raise ValueError("a uuid.UUID is required")
        return value if dialect.name == "postgresql" else value.bytes

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(bytes=bytes(value))


class Sha256Col(_Strict):
    """A SHA-256 digest: exactly 32 ``bytes``. Postgres ``bytea``; SQLite ``BLOB``."""

    impl = sa.LargeBinary
    cache_ok = True
    check = (_Sha256Length, "sha256_len")

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        if value is None:
            return None
        if type(value) is not bytes or len(value) != 32:
            raise ValueError("32 bytes are required")
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else bytes(value)


class InstantCol(_Strict):
    """A timezone-aware ``datetime`` in, a UTC ``datetime`` out; years 1 to 9999 in UTC.

    Postgres ``timestamp with time zone``. SQLite ``TEXT`` ``YYYY-MM-DDTHH:MM:SS.ffffffZ``, written
    by hand (``strftime`` does not zero-pad years below 1000 everywhere), which sorts as time.
    """

    impl = sa.Text
    cache_ok = True
    check = (_InstantShape, "instant_shape")

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.TIMESTAMP(timezone=True))
        return dialect.type_descriptor(sa.Text())

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        if value is None:
            return None
        if type(value) is not datetime or value.utcoffset() is None:
            raise ValueError("a timezone-aware datetime is required")
        try:
            utc = value.astimezone(UTC)
        except OverflowError:
            raise ValueError("the instant is outside years 1 to 9999 in UTC") from None
        if dialect.name == "postgresql":
            return utc
        return (
            f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T{utc.hour:02d}:{utc.minute:02d}:"
            f"{utc.second:02d}.{utc.microsecond:06d}Z"
        )

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        match = _INSTANT_TEXT.fullmatch(value) if isinstance(value, str) else None
        if match is None:
            raise ValueError("the stored instant is malformed")
        year, month, day, hour, minute, second, micro = (int(g) for g in match.groups())
        try:
            return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
        except ValueError:
            raise ValueError("the stored instant is malformed") from None


class DecimalCol(_Strict):
    """A finite ``decimal.Decimal`` with its digits and scale kept; never a float.

    Postgres unconstrained ``numeric``. SQLite ``TEXT`` holding ``format(value, 'f')``. Negative
    zero is refused because Postgres ``numeric`` has none.
    """

    impl = sa.Text
    cache_ok = True
    check = (_DecimalShape, "decimal_shape")

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sa.Numeric(asdecimal=True))
        return dialect.type_descriptor(sa.Text())

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        if value is None:
            return None
        if type(value) is not Decimal:
            raise ValueError("a decimal.Decimal is required")
        if not value.is_finite():
            raise ValueError("NaN and infinity cannot be stored")
        if value.is_zero() and value.is_signed():
            raise ValueError("negative zero cannot be stored")
        return value if dialect.name == "postgresql" else format(value, "f")

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            result = Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("the stored decimal is malformed") from None
        if not result.is_finite():
            raise ValueError("the stored decimal is malformed")
        return result


class DocCol(_Strict):
    """Text kept exactly (Postgres ``text``, never json or jsonb). U+0000 and lone surrogates
    are refused: escaped NUL (the six characters) is fine, a raw NUL is not storable in Postgres."""

    impl = sa.Text
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        return None if value is None else _text(value)


class KeyCol(_Strict):
    """A key compared by its UTF-8 bytes on both backends (Postgres ``COLLATE "C"``, SQLite
    ``BINARY``): no case folding, no normalization, code point order."""

    impl = sa.Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        collation = "C" if dialect.name == "postgresql" else "BINARY"
        return dialect.type_descriptor(sa.Text(collation=collation))

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        return None if value is None else _text(value)


class BoolCol(_Strict):
    """A tri-state boolean: ``True``, ``False`` or NULL (absent or unknown, never false).

    The column is nullable with no default. Postgres ``boolean``; SQLite ``INTEGER`` 0 or 1.
    """

    impl = sa.Integer
    cache_ok = True
    check = (_BoolDomain, "bool")

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sa.Boolean())
        return dialect.type_descriptor(sa.Integer())

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        if value is None:
            return None
        if type(value) is not bool:
            raise ValueError("a bool or None is required")
        return value if dialect.name == "postgresql" else int(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else bool(value)


class BigIntCol(_Strict):
    """A Python ``int`` (exactly; ``bool`` is refused) in the signed 64-bit range.

    Postgres ``bigint``; SQLite ``INTEGER``. Any range rule (``>= 0``, ``>= 1``) belongs to the
    table, not to the type.
    """

    impl = sa.BigInteger
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sa.BigInteger())
        return dialect.type_descriptor(sa.Integer())

    def process_bind_param(self, value: object, dialect: Dialect) -> Any:
        if value is None:
            return None
        if type(value) is not int:
            raise ValueError("an int is required")
        if not -(2**63) <= value <= 2**63 - 1:
            raise ValueError("the integer is outside the signed 64-bit range")
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else int(value)
