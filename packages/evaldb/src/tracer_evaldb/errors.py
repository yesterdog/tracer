# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Typed errors of the store. No message ever holds a database URL."""

from __future__ import annotations

__all__ = [
    "StoreConfigurationError",
    "StoreConnectionError",
    "StoreError",
    "StoreIntegrityError",
    "StoreValueOutOfRange",
    "UnsupportedServerEncoding",
    "UnsupportedSqliteVersion",
]


class StoreError(Exception):
    """Base class of every error raised by tracer_evaldb."""


class StoreConfigurationError(StoreError):
    """The URL or an option is not supported."""


class StoreConnectionError(StoreError):
    """The database could not be reached or refused the connection."""


class UnsupportedSqliteVersion(StoreError):
    """The SQLite library is older than 3.37 (STRICT tables)."""


class UnsupportedServerEncoding(StoreError):
    """The Postgres database does not use UTF8."""


class StoreIntegrityError(StoreError):
    """A stored row disagrees with its own document.

    The message holds the table, the column and the row's ID, and nothing from the row.
    """

    def __init__(self, table: str, column: str | None = None, entity_id: str | None = None) -> None:
        self.table = table
        self.column = column
        self.entity_id = entity_id
        text = f"stored row of table {table!r} failed an integrity check"
        if column is not None:
            text += f" (column {column!r})"
        if entity_id is not None:
            text += f" (id {entity_id})"
        super().__init__(text)


class StoreValueOutOfRange(StoreError, ValueError):
    """An integer cannot be stored: the stored range is plus or minus ``2**53 - 1``.

    Names the table and the column, never the number.
    """

    def __init__(self, table: str, column: str) -> None:
        self.table = table
        self.column = column
        super().__init__(f"a value for column {column!r} of table {table!r} is out of range")
