# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P2-05: column types (contract docs/phase2/api-surface-batch-a.md section 5).

Every test that takes the ``store`` fixture runs on SQLite and, with ``-m postgres``, on Postgres.
Scratch tables are created through ``store.table`` (STRICT on SQLite). Values reach the database
through the column types, and ``sa.type_coerce`` reads the stored representation back raw.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import os
import random
import re
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

# --------------------------------------------------------------------------------- helpers


def make_v7(ms: int, rand_a: int = 0xABC, rand_b: int = 0x123456789ABCDEF) -> uuid.UUID:
    """A UUIDv7: 48-bit millisecond timestamp, version 7, RFC 4122 variant."""
    value = (ms & (2**48 - 1)) << 80
    value |= 0x7 << 76
    value |= (rand_a & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand_b & (2**62 - 1)
    return uuid.UUID(int=value)


def with_version(nibble: int) -> uuid.UUID:
    value = make_v7(1_700_000_000_000).int
    value = (value & ~(0xF << 76)) | (nibble << 76)
    return uuid.UUID(int=value)


def one_column_table(store: Any, sa: Any, column_type: Any, *, name: str = "col") -> Any:
    return store.table(
        name,
        sa.Column("pk", sa.Integer, primary_key=True),
        sa.Column("v", column_type),
    )


def put(store: Any, sa: Any, table: Any, values: list[Any]) -> None:
    with store.engine.begin() as conn:
        for value in values:
            conn.execute(sa.insert(table).values(v=value))


def get(store: Any, sa: Any, table: Any) -> list[Any]:
    with store.engine.connect() as conn:
        return [r[0] for r in conn.execute(sa.select(table.c.v).order_by(table.c.pk))]


def rejected_at_bind(store: Any, sa: Any, table: Any, value: Any) -> Any:
    with pytest.raises(sa.exc.StatementError) as info, store.engine.begin() as conn:
        conn.execute(sa.insert(table).values(v=value))
    assert isinstance(info.value.orig, ValueError), (value, info.value.orig)
    return info.value


def rejected_by_database(store: Any, sa: Any, table: Any, bindparam: Any) -> None:
    """Insert through a bind parameter of another type: only the database can refuse it."""
    with pytest.raises(sa.exc.DBAPIError), store.engine.begin() as conn:
        conn.execute(sa.insert(table).values(v=bindparam))


def db_typeof(store: Any, sa: Any, table: Any) -> list[str]:
    expr = (
        sa.func.typeof(table.c.v)
        if store.backend == "sqlite"
        else sa.cast(sa.func.pg_typeof(table.c.v), sa.Text)
    )
    with store.engine.connect() as conn:
        return [r[0] for r in conn.execute(sa.select(expr).order_by(table.c.pk))]


def raw(store: Any, sa: Any, table: Any, as_type: Any) -> list[Any]:
    with store.engine.connect() as conn:
        rows = conn.execute(sa.select(sa.type_coerce(table.c.v, as_type)).order_by(table.c.pk))
        return [r[0] for r in rows]


def ddl(sa: Any, table: Any, dialect_name: str) -> str:
    dialect = importlib.import_module(f"sqlalchemy.dialects.{dialect_name}").dialect()
    schema = importlib.import_module("sqlalchemy.schema")
    return str(schema.CreateTable(table).compile(dialect=dialect))


def instant_text(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T{utc.hour:02d}:{utc.minute:02d}:"
        f"{utc.second:02d}.{utc.microsecond:06d}Z"
    )


# ---------------------------------------------------------------------------------- UuidCol


def test_uuid_column_native_on_postgres_blob16_on_sqlite(
    store: Any, sa: Any, evaldb: ModuleType
) -> None:
    table = one_column_table(store, sa, evaldb.UuidCol())
    ids = [make_v7(1_700_000_000_000 + i, i, i * 7919) for i in range(5)]
    put(store, sa, table, [*ids, None])
    back = get(store, sa, table)
    assert back == [*ids, None]
    assert all(type(b) is uuid.UUID for b in back[:-1])
    assert "BLOB" in ddl(sa, table, "sqlite")
    assert re.search(r"\bv BLOB\b", ddl(sa, table, "sqlite"))
    assert not re.search(r"\bv (?:N?VAR)?CHAR", ddl(sa, table, "sqlite"))
    assert re.search(r"\bv UUID\b", ddl(sa, table, "postgresql"))
    if store.backend == "sqlite":
        assert store.sql_type(table, "v") == "BLOB"
        assert db_typeof(store, sa, table) == ["blob"] * 5 + ["null"]
        assert raw(store, sa, table, sa.LargeBinary())[:5] == [i.bytes for i in ids]
        with store.engine.connect() as conn:
            lengths = conn.execute(sa.select(sa.func.length(table.c.v)).order_by(table.c.pk))
            assert [r[0] for r in lengths] == [16] * 5 + [None]
    else:
        assert store.sql_type(table, "v") == "uuid"
        assert db_typeof(store, sa, table) == ["uuid"] * 5 + ["uuid"]
    # nothing is coerced: a string, bytes or an integer is not a UUID
    for bad in (str(ids[0]), ids[0].hex, ids[0].bytes, ids[0].int):
        rejected_at_bind(store, sa, table, bad)


def test_uuid_column_sorts_in_v7_time_order(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.UuidCol(), name="ord")
    in_time_order = [
        make_v7(ms, rand_a=(ms * 31) & 0xFFF, rand_b=ms * 104729)
        for ms in (
            0,
            1,
            255,
            256,
            65_535,
            65_536,
            1_000_000,
            1_700_000_000_000,
            1_700_000_000_001,
            2**47,
            2**48 - 1,
        )
    ]
    shuffled = list(in_time_order)
    random.Random(7).shuffle(shuffled)  # noqa: S311
    put(store, sa, table, shuffled)
    with store.engine.connect() as conn:
        rows = conn.execute(sa.select(table.c.v).order_by(table.c.v))
        assert [r[0] for r in rows] == in_time_order
        newest = conn.execute(sa.select(table.c.v).order_by(table.c.v.desc()).limit(1))
        assert newest.scalar_one() == in_time_order[-1]


def test_uuid_v7_check_rejects_other_versions(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.UuidCol(), name="v7")
    good = [make_v7(1_700_000_000_000), with_version(7)]
    put(store, sa, table, good)
    wrong = [
        *(with_version(n) for n in range(16) if n != 7),
        uuid.UUID(int=0),
        uuid.UUID(int=2**128 - 1),
        uuid.uuid4(),
        uuid.uuid1(),
        uuid.uuid5(uuid.NAMESPACE_DNS, "tracer"),
    ]
    assert len(wrong) == 20
    for value in wrong:
        with pytest.raises(sa.exc.DBAPIError), store.engine.begin() as conn:
            conn.execute(sa.insert(table).values(v=value))
    assert get(store, sa, table) == good
    if store.backend == "sqlite":  # a BLOB that is not 16 bytes long is not a UUID either
        for size in (0, 15, 17, 32):
            data = bytearray(make_v7(1_700_000_000_000).bytes)
            blob = bytes(data[:size]) + bytes(max(0, size - 16))
            rejected_by_database(store, sa, table, sa.bindparam("x", blob, type_=sa.LargeBinary()))
        assert get(store, sa, table) == good


# ------------------------------------------------------------------------------- InstantCol

UTC_EDGES = [
    datetime(1, 1, 1, 0, 0, 0, tzinfo=UTC),
    datetime(1, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
    datetime(1, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    datetime(999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    datetime(1000, 1, 1, tzinfo=UTC),
    datetime(1583, 1, 1, tzinfo=UTC),
    datetime(1900, 1, 1, tzinfo=UTC),
    datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    datetime(1970, 1, 1, tzinfo=UTC),
    datetime(2024, 2, 29, 12, 0, 0, 123456, tzinfo=UTC),
    datetime(2026, 10, 24, 6, 30, 15, 500000, tzinfo=UTC),
    datetime(2038, 1, 19, 3, 14, 8, tzinfo=UTC),
    datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
]
IST = timezone(timedelta(hours=5, minutes=30))
OFFSET_INPUTS = [
    datetime(2026, 10, 24, 12, 0, 0, 7, tzinfo=IST),
    datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=-8))),
    datetime(1850, 1, 1, tzinfo=ZoneInfo("Asia/Kolkata")),  # local mean time, odd offset
    datetime(1, 1, 2, 12, 0, tzinfo=timezone(timedelta(hours=14))),
    datetime(9999, 12, 31, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
]


def _check_instants(store: Any, sa: Any, evaldb: ModuleType, table: Any) -> None:
    inputs = [*UTC_EDGES, *OFFSET_INPUTS]
    put(store, sa, table, [*inputs, None])
    back = get(store, sa, table)
    assert back[-1] is None
    for sent, got in zip(inputs, back[:-1], strict=True):
        assert got == sent, (sent, got)
        assert got.tzinfo is UTC, "values come back as UTC-aware datetimes"
        assert got.utcoffset() == timedelta(0)
        assert got.astimezone(UTC).isoformat() == sent.astimezone(UTC).isoformat()
    if store.backend == "sqlite":
        texts = raw(store, sa, table, sa.Text())
        assert texts[:-1] == [instant_text(v) for v in inputs]
        assert all(len(t) == 27 for t in texts[:-1])
        assert db_typeof(store, sa, table) == ["text"] * len(inputs) + ["null"]


def test_instant_round_trip_edges(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.InstantCol())
    _check_instants(store, sa, evaldb, table)
    if store.backend == "sqlite":
        assert store.sql_type(table, "v") == "TEXT"
    else:
        assert store.sql_type(table, "v") == "timestamp with time zone"
    assert re.search(r"\bv TEXT\b", ddl(sa, table, "sqlite"))
    assert re.search(r"\bv TIMESTAMP WITH TIME ZONE\b", ddl(sa, table, "postgresql"))

    # only timezone-aware datetimes that stay inside years 1 to 9999 as UTC are accepted
    naive = datetime(2026, 1, 1)
    too_early = datetime(1, 1, 1, 0, 0, tzinfo=IST)
    too_late = datetime(9999, 12, 31, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
    for bad in (naive, too_early, too_late, "2026-01-01T00:00:00.000000Z", 0, 1.5):
        rejected_at_bind(store, sa, table, bad)
    with store.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 19


def test_instant_round_trip_under_non_utc_server_timezone(
    store: Any, sa: Any, evaldb: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = one_column_table(store, sa, evaldb.InstantCol(), name="tz")
    if store.backend == "postgres":
        # CI gives this database the default time zone Asia/Kolkata; the session must not use it
        plain = sa.create_engine(store.url)
        try:
            with plain.connect() as conn:
                assert conn.exec_driver_sql("SHOW TimeZone").scalar_one() == "Asia/Kolkata"
        finally:
            plain.dispose()
        _check_instants(store, sa, evaldb, table)
        return
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    try:
        assert time.timezone == -19800  # seconds west of UTC: +05:30
        _check_instants(store, sa, evaldb, table)
    finally:
        monkeypatch.undo()
        time.tzset()
    assert os.environ.get("TZ") != "Asia/Kolkata"


def test_sqlite_instant_text_sorts_chronologically(
    sqlite_store: Any, sa: Any, evaldb: ModuleType
) -> None:
    table = one_column_table(sqlite_store, sa, evaldb.InstantCol(), name="chrono")
    values = [*UTC_EDGES, *OFFSET_INPUTS]
    shuffled = list(values)
    random.Random(11).shuffle(shuffled)  # noqa: S311
    put(sqlite_store, sa, table, shuffled)
    expected = sorted(values, key=lambda v: v.astimezone(UTC))
    with sqlite_store.engine.connect() as conn:
        ordered = [r[0] for r in conn.execute(sa.select(table.c.v).order_by(table.c.v))]
        assert ordered == expected
        text_order = [
            r[0]
            for r in conn.execute(
                sa.select(sa.type_coerce(table.c.v, sa.Text())).order_by(table.c.v)
            )
        ]
        assert text_order == [instant_text(v) for v in expected]
        low = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
        high = datetime(2026, 10, 24, 6, 30, 15, 500000, tzinfo=UTC)
        between = [
            r[0]
            for r in conn.execute(
                sa.select(table.c.v).where(table.c.v >= low, table.c.v < high).order_by(table.c.v)
            )
        ]
        assert between == [v for v in expected if low <= v < high]
        assert conn.execute(sa.select(sa.func.min(table.c.v))).scalar_one() == expected[0]
        assert conn.execute(sa.select(sa.func.max(table.c.v))).scalar_one() == expected[-1]


# ------------------------------------------------------------------------------- DecimalCol

DECIMALS = [
    Decimal("0"),
    Decimal("0.000000000000000001"),
    Decimal("-0.000000000000000001"),
    Decimal("0.123456789012345678"),
    Decimal("123456789012345678901234567890"),
    Decimal("-123456789012345678901234567890"),
    Decimal("123456789012345678901234567890.123456789012345678"),
    Decimal("-123456789012345678901234567890.123456789012345678"),
    Decimal("1.50"),
    Decimal("100"),
    Decimal("-1"),
    Decimal("0.1"),
    Decimal("7" * 60 + "." + "3" * 40),
]


def test_decimal_round_trip_exact(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.DecimalCol(), name="dec")
    put(store, sa, table, [*DECIMALS, Decimal("1E+2"), None])
    back = get(store, sa, table)
    assert back[-1] is None
    for sent, got in zip(DECIMALS, back, strict=False):
        assert type(got) is Decimal
        assert got == sent
        assert str(got) == str(sent), "digits and scale are kept, not only the value"
    assert back[len(DECIMALS)] == Decimal("1E+2")
    if store.backend == "postgres":
        assert store.sql_type(table, "v") == "numeric", "unconstrained NUMERIC, no precision"
    assert re.search(r"\bv NUMERIC\b(?!\()", ddl(sa, table, "postgresql"))
    assert re.search(r"\bv TEXT\b", ddl(sa, table, "sqlite"))
    # no float, no int, no string, no non-finite value and no negative zero
    for bad in (
        0.1,
        1.0,
        1,
        "1.5",
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("-0"),
    ):
        rejected_at_bind(store, sa, table, bad)
    assert len(get(store, sa, table)) == len(DECIMALS) + 2


def test_sqlite_decimal_stored_as_text(sqlite_store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(sqlite_store, sa, evaldb.DecimalCol(), name="dectext")
    values = [Decimal("0"), Decimal("100"), Decimal("1.50"), Decimal("-1"), Decimal("1E+5")]
    put(sqlite_store, sa, table, values)
    assert sqlite_store.sql_type(table, "v") == "TEXT"
    assert db_typeof(sqlite_store, sa, table) == ["text"] * 5
    assert raw(sqlite_store, sa, table, sa.Text()) == ["0", "100", "1.50", "-1", "100000"]
    with sqlite_store.engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count()).select_from(table).where(sa.func.typeof(table.c.v) != "text")
        ).scalar_one()
        assert count == 0


# --------------------------------------------------------------------------------- BoolCol


def test_bool_tristate_null_never_false(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.BoolCol(), name="tri")
    assert table.c.v.nullable is True
    assert table.c.v.default is None
    assert table.c.v.server_default is None
    put(store, sa, table, [None, True, False])
    with store.engine.begin() as conn:  # the column omitted: absent stays NULL, never false
        conn.execute(sa.insert(table).values())
    back = get(store, sa, table)
    assert back == [None, True, False, None]
    assert back[0] is None
    assert back[1] is True
    assert back[2] is False
    assert back[3] is None
    with store.engine.connect() as conn:
        nulls = conn.execute(sa.select(sa.func.count()).where(table.c.v.is_(None))).scalar_one()
        assert nulls == 2
    if store.backend == "sqlite":
        assert store.sql_type(table, "v") == "INTEGER"
        assert db_typeof(store, sa, table) == ["null", "integer", "integer", "null"]
        assert raw(store, sa, table, sa.Integer()) == [None, 1, 0, None]
        rejected_by_database(store, sa, table, sa.bindparam("x", 2, type_=sa.Integer()))
        rejected_by_database(store, sa, table, sa.bindparam("x", -1, type_=sa.Integer()))
    else:
        assert store.sql_type(table, "v") == "boolean"
        rejected_by_database(store, sa, table, sa.bindparam("x", "maybe", type_=sa.String()))
    for bad in (0, 1, 2, 1.0, "true", "false", "f", b"\x00"):
        rejected_at_bind(store, sa, table, bad)
    assert get(store, sa, table) == [None, True, False, None]


# --------------------------------------------------------------------------------- KeyCol

ORDER_KEYS = [
    "a",
    "A",
    "b",
    "B",
    "z",
    "Z",
    "_",
    "-",
    "~",
    ".",
    "0",
    "9",
    "a b",
    "a-b",
    "a_b",
    "a.b",
    "ab",
    "aB",
    "\u00e9",
    "e\u0301",
    "\u00f1",
    "\u0915",
    "\u0958",
    "\u0915\u093c",
    "\u0d15",
    "\u0967",
    "\uff5e",
    "\U00010000",
    "\U0001f600",
    "\ud7ff",
    "\ue000",
    "\uffff",
]


def test_key_column_order_parity(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = store.table("key", sa.Column("k", evaldb.KeyCol(), primary_key=True))
    shuffled = list(ORDER_KEYS)
    random.Random(3).shuffle(shuffled)  # noqa: S311
    with store.engine.begin() as conn:
        for key in shuffled:
            conn.execute(sa.insert(table).values(k=key))
    expected = sorted(ORDER_KEYS, key=lambda s: s.encode("utf-8"))  # code point order
    utf16 = sorted(ORDER_KEYS, key=lambda s: s.encode("utf-16-be"))
    assert expected != utf16, "the sample must tell UTF-8 order from UTF-16 order"
    with store.engine.connect() as conn:
        assert [r[0] for r in conn.execute(sa.select(table.c.k).order_by(table.c.k))] == expected
        desc = [r[0] for r in conn.execute(sa.select(table.c.k).order_by(table.c.k.desc()))]
        assert desc == expected[::-1]
        low, high = "B", "aB"
        found = [
            r[0]
            for r in conn.execute(
                sa.select(table.c.k).where(table.c.k >= low, table.c.k < high).order_by(table.c.k)
            )
        ]
        assert found == [k for k in expected if low.encode() <= k.encode() < high.encode()]
        assert conn.execute(sa.select(sa.func.min(table.c.k))).scalar_one() == expected[0]
        assert conn.execute(sa.select(sa.func.max(table.c.k))).scalar_one() == expected[-1]
    if store.backend == "postgres":
        assert store.collation(table, "k") == "C"
        assert store.sql_type(table, "k") in {"text", "character varying"}
    else:
        assert store.sql_type(table, "k") == "TEXT"


def test_case_distinct_keys_distinct_on_both_backends(
    store: Any, sa: Any, evaldb: ModuleType
) -> None:
    keys = [
        "Abc",
        "abc",
        "ABC",
        "abc ",
        " abc",
        "abc\t",
        "I",
        "i",
        "\u0131",
        "\u0130",
        "\u00df",
        "SS",
        "ss",
        "\u03a3",
        "\u03c3",
        "\u03c2",
        "K",
        "\u212a",
        "k",
    ]
    table = store.table("case", sa.Column("k", evaldb.KeyCol(), primary_key=True))
    with store.engine.begin() as conn:
        for key in keys:
            conn.execute(sa.insert(table).values(k=key))
    with store.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == len(keys)
        for key in keys:
            rows = conn.execute(sa.select(table.c.k).where(table.c.k == key)).all()
            assert [r[0] for r in rows] == [key], key
    with pytest.raises(sa.exc.DBAPIError), store.engine.begin() as conn:
        conn.execute(sa.insert(table).values(k="Abc"))


def test_nfc_nfd_distinct_on_both_backends(store: Any, sa: Any, evaldb: ModuleType) -> None:
    pairs = [
        ("\u00e9", "e\u0301"),
        ("\u0958", "\u0915\u093c"),
        ("\uac00", "\u1100\u1161"),
        ("\u0a33", "\u0a32\u0a3c"),
        ("\u0d23\u0d4d\u200d", "\u0d7a"),  # Malayalam chillu: ZWJ sequence against atomic chillu
        ("\u212b", "\u00c5", "A\u030a"),
    ]
    flat = [s for group in pairs for s in group]
    key_table = store.table("nfk", sa.Column("k", evaldb.KeyCol(), primary_key=True))
    doc_table = store.table(
        "nfd", sa.Column("pk", sa.Integer, primary_key=True), sa.Column("v", evaldb.DocCol())
    )
    with store.engine.begin() as conn:
        for text in flat:
            conn.execute(sa.insert(key_table).values(k=text))
            conn.execute(sa.insert(doc_table).values(v=text))
    with store.engine.connect() as conn:
        for text in flat:
            found = conn.execute(sa.select(key_table.c.k).where(key_table.c.k == text)).all()
            assert [r[0].encode("utf-8") for r in found] == [text.encode("utf-8")], text
        assert conn.execute(sa.select(sa.func.count()).select_from(key_table)).scalar_one() == len(
            flat
        )
    assert [t.encode("utf-8") for t in get(store, sa, doc_table)] == [
        t.encode("utf-8") for t in flat
    ]


def test_key_column_rejects_nul_and_lone_surrogates(
    store: Any, sa: Any, evaldb: ModuleType
) -> None:
    table = store.table("keybad", sa.Column("k", evaldb.KeyCol(), primary_key=True))
    for bad in ("a\x00b", "\x00", "\ud800", "x\udfffy", b"bytes", 5, None):
        if bad is None:
            with pytest.raises(sa.exc.DBAPIError), store.engine.begin() as conn:
                conn.execute(sa.insert(table).values(k=bad))  # a key is never NULL
            continue
        with pytest.raises(sa.exc.StatementError) as info, store.engine.begin() as conn:
            conn.execute(sa.insert(table).values(k=bad))
        assert isinstance(info.value.orig, ValueError)
    with store.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 0


# ------------------------------------------------------------------------- DocCol, Sha256Col


def test_doc_column_round_trips_text_exactly(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.DocCol(), name="doc")
    texts = [
        '{"a":1}',
        '{"text":"\\u0000","n":1e300,"big":1152921504606846976}',
        "\u0928\u092e\u0938\u094d\u0924\u0947 \u200d\u200c \u202e\u2066",
        "line\r\nbreak\u2028\u2029\x0b\x0c\x85",
        "\U0001f600" * 3 + "\ufeff",
        "  leading and trailing  ",
        "",
        "x" * 5_000_000,
    ]
    put(store, sa, table, [*texts, None])
    back = get(store, sa, table)
    assert back == [*texts, None]
    assert [b.encode("utf-8") for b in back[:-1]] == [t.encode("utf-8") for t in texts]
    assert re.search(r"\bv TEXT\b", ddl(sa, table, "sqlite"))
    assert re.search(r"\bv TEXT\b", ddl(sa, table, "postgresql"))
    if store.backend == "sqlite":
        assert store.sql_type(table, "v") == "TEXT"
        assert set(db_typeof(store, sa, table)) == {"text", "null"}
    else:
        assert store.sql_type(table, "v") == "text", "text, not json or jsonb"
        assert set(db_typeof(store, sa, table)) == {"text"}
    for bad in ("raw\x00nul", "\ud800", b"bytes", {"a": 1}, 5):
        rejected_at_bind(store, sa, table, bad)


def test_sha256_column_is_32_byte_blob(store: Any, sa: Any, evaldb: ModuleType) -> None:
    table = one_column_table(store, sa, evaldb.Sha256Col(), name="sha")
    digests = [hashlib.sha256(b"x").digest(), bytes(32), b"\xff" * 32]
    put(store, sa, table, [*digests, None])
    back = get(store, sa, table)
    assert back == [*digests, None]
    assert all(type(b) is bytes for b in back[:-1])
    if store.backend == "sqlite":
        assert store.sql_type(table, "v") == "BLOB"
        assert db_typeof(store, sa, table) == ["blob"] * 3 + ["null"]
    else:
        assert store.sql_type(table, "v") == "bytea"
    assert re.search(r"\bv BLOB\b", ddl(sa, table, "sqlite"))
    assert re.search(r"\bv BYTEA\b", ddl(sa, table, "postgresql"))
    for bad in (
        b"",
        bytes(31),
        bytes(33),
        bytearray(32),
        memoryview(bytes(32)),
        digests[0].hex(),
        hashlib.sha256(b"x"),
        5,
    ):
        rejected_at_bind(store, sa, table, bad)
    for size in (0, 1, 31, 33):  # the database refuses a wrong length as well
        rejected_by_database(
            store, sa, table, sa.bindparam("x", bytes(size), type_=sa.LargeBinary())
        )
    assert get(store, sa, table) == [*digests, None]


# ------------------------------------------------------------------ no DateTime / Float columns


def test_no_sqlalchemy_datetime_columns(evaldb: ModuleType, sa: Any) -> None:
    forbidden_types = (sa.DateTime, sa.Date, sa.Time, sa.Interval, sa.Float)
    for name in ("UuidCol", "InstantCol", "DecimalCol", "DocCol", "Sha256Col", "KeyCol", "BoolCol"):
        cls = getattr(evaldb, name)
        assert not issubclass(cls, forbidden_types), name
    # every column of the shared metadata (empty until batch B adds the tables)
    assert isinstance(evaldb.metadata, sa.MetaData)
    for table in evaldb.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, forbidden_types), (table.name, column.name)
            impl = getattr(column.type, "impl", None)
            assert not isinstance(impl, forbidden_types), (table.name, column.name)
    # no module of the package names a DateTime, Date, Time, Interval or float type; TIMESTAMP
    # (the Postgres timestamptz of InstantCol) appears in columns.py only
    spec = importlib.util.find_spec("tracer_evaldb")
    assert spec is not None
    assert spec.submodule_search_locations
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    banned = {
        "DateTime",
        "Date",
        "Time",
        "Interval",
        "Float",
        "REAL",
        "FLOAT",
        "DOUBLE",
        "DOUBLE_PRECISION",
    }
    sources = sorted(p for p in package_dir.rglob("*.py") if "__pycache__" not in p.parts)
    assert sources
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, ast.Name):
                names = [node.id]
            for found in names:
                assert found not in banned, f"{path.name}:{getattr(node, 'lineno', 0)}: {found}"
                if found == "TIMESTAMP":
                    assert path.name == "columns.py", (
                        f"{path.name}:{getattr(node, 'lineno', 0)}: TIMESTAMP"
                    )
