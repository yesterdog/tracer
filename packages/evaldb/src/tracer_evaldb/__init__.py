# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Relational storage of tracer-evalschema entities (SQLite and Postgres)."""

from __future__ import annotations

from .columns import BoolCol, DecimalCol, DocCol, InstantCol, KeyCol, Sha256Col, UuidCol
from .engine import create_store_engine
from .errors import (
    StoreConfigurationError,
    StoreConnectionError,
    StoreError,
    UnsupportedServerEncoding,
    UnsupportedSqliteVersion,
)
from .schema import metadata

__all__ = [
    "BoolCol",
    "DecimalCol",
    "DocCol",
    "InstantCol",
    "KeyCol",
    "Sha256Col",
    "StoreConfigurationError",
    "StoreConnectionError",
    "StoreError",
    "UnsupportedServerEncoding",
    "UnsupportedSqliteVersion",
    "UuidCol",
    "create_store_engine",
    "metadata",
]
