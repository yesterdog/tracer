# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Typed errors of the store. No message ever holds a database URL."""

from __future__ import annotations

__all__ = [
    "StoreConfigurationError",
    "StoreConnectionError",
    "StoreError",
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
