# tracer-evaldb

Relational storage of `tracer-evalschema` 0.0.1 entities on SQLite and Postgres (SQLAlchemy Core and
Alembic). Status: 0.0.1, unreleased (Phase 2 in progress). The design is in ADRs 0019 to 0022.

Install the Postgres driver with the extra: `pip install "tracer-evaldb[postgres]"` (pg8000, a pure
Python driver; the URL is `postgresql+pg8000://...`). SQLite needs library version 3.37 or newer.

`create_store_engine(url)` returns an engine configured the way the store needs it (foreign keys on,
real transactions, UTC session time zone, UTF8 only) and never echoes the URL in an error. The
column types `UuidCol`, `InstantCol`, `DecimalCol`, `DocCol`, `Sha256Col`, `KeyCol`, `BigIntCol` and
`BoolCol` store the same values the same way on both backends.

`tracer_evaldb.metadata` holds the 37 tables (26 entity tables and 11 side tables, module
`tracer_evaldb.tables`). `tracer_evaldb.codecs` has `encode_<table>` and `decode_<table>` for each
entity table, the generic `encode` and `decode`, and `decode_run_bundle`: a model becomes the rows
to insert, and a row is checked against its own document on the way back
(`StoreIntegrityError`, `StoreValueOutOfRange`).
