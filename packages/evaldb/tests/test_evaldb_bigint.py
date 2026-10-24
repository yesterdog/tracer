# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch B: the eighth column type, ``BigIntCol`` (ADR 0019 Table 3, Decision 6).

Contract: docs/phase2/api-surface-batch-b.md section 2. ``BigIntCol`` joins the seven types of
batch A (``tracer_evaldb.BigIntCol``): a Python ``int`` (exactly) in the signed 64-bit range,
Postgres ``bigint``, SQLite ``INTEGER``; anything else is refused at bind time with a
``ValueError`` (surfacing from SQLAlchemy as ``StatementError``), and the message never holds the
value.
"""

from __future__ import annotations

import decimal
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)


def _table(store: Any, evaldb: ModuleType) -> sa.Table:
    table: sa.Table = store.table(
        "bigint",
        sa.Column("n", sa.Integer, primary_key=True),
        sa.Column("v", evaldb.BigIntCol()),
    )
    return table


def test_bigint_column_round_trips_the_signed_64_bit_range(store: Any, evaldb: ModuleType) -> None:
    table = _table(store, evaldb)
    values = [0, 1, -1, 2**31, 2**53 + 1, -(2**53) - 1, INT64_MAX, INT64_MIN, None]
    with store.engine.begin() as conn:
        conn.execute(sa.insert(table), [{"n": i, "v": v} for i, v in enumerate(values)])
    with store.engine.connect() as conn:
        stored = [r[0] for r in conn.execute(sa.select(table.c.v).order_by(table.c.n))]
    assert stored == values
    assert all(type(v) is int for v in stored if v is not None)
    # the declared types of Table 3
    assert store.sql_type(table, "v") == ("bigint" if store.backend == "postgres" else "INTEGER")


@pytest.mark.parametrize(
    "bad",
    [
        INT64_MAX + 1,
        INT64_MIN - 1,
        2**64,
        -(2**64),
        10**30,
        True,
        False,
        1.0,
        "1",
        b"1",
        decimal.Decimal(1),
        [1],
    ],
    ids=lambda v: type(v).__name__ + (str(v)[:6] if isinstance(v, int) else ""),
)
def test_bigint_column_refuses_values_beyond_64_bits_and_other_types(
    store: Any, evaldb: ModuleType, bad: object
) -> None:
    table = _table(store, evaldb)
    with pytest.raises(sa.exc.StatementError) as caught, store.engine.begin() as conn:
        conn.execute(sa.insert(table), {"n": 1, "v": bad})
    assert isinstance(caught.value.orig, ValueError)
    message = str(caught.value.orig)
    if isinstance(bad, int) and not isinstance(bad, bool):
        assert str(abs(bad)) not in message  # the value never appears
    with store.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 0


def test_bigint_column_orders_numerically(store: Any, evaldb: ModuleType) -> None:
    """``version DESC`` must be a numeric order on both backends (text order would put 9 last)."""
    table = _table(store, evaldb)
    values = [9, 10, 100, 2**40, 2, 1000]
    with store.engine.begin() as conn:
        conn.execute(sa.insert(table), [{"n": i, "v": v} for i, v in enumerate(values)])
    with store.engine.connect() as conn:
        descending = [r[0] for r in conn.execute(sa.select(table.c.v).order_by(table.c.v.desc()))]
    assert descending == sorted(values, reverse=True)


def test_bigint_column_is_not_a_float_or_a_numeric(evaldb: ModuleType) -> None:
    column_type = evaldb.BigIntCol()
    assert not isinstance(column_type, (sa.Float, sa.Numeric, sa.DateTime))
    assert evaldb.BigIntCol is not None
    assert "BigIntCol" in evaldb.__all__


def test_bigint_column_has_no_check_of_its_own(evaldb: ModuleType) -> None:
    """A column that needs a bound (``>= 1``, ``>= 0``) gets it from the table, so a bare
    ``BigIntCol`` adds no constraint to a scratch table."""
    metadata = sa.MetaData()
    table = sa.Table("scratch_bigint", metadata, sa.Column("v", evaldb.BigIntCol()))
    assert not [c for c in table.constraints if isinstance(c, sa.CheckConstraint)]
