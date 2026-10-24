# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The shared MetaData of the store. Tables arrive with the table batch."""

from __future__ import annotations

import sqlalchemy as sa

__all__ = ["metadata"]

metadata = sa.MetaData()
