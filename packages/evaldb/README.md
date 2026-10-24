# tracer-evaldb

Relational storage of `tracer-evalschema` 0.0.1 entities on SQLite and Postgres (SQLAlchemy Core and
Alembic). Status: 0.0.1, unreleased (Phase 2 in progress). The design is in ADRs 0019 to 0022.

Install the Postgres driver with the extra: `pip install "tracer-evaldb[postgres]"` (pg8000, a pure
Python driver; the URL is `postgresql+pg8000://...`). SQLite needs library version 3.37 or newer.

`create_store_engine(url)` returns an engine configured the way the store needs it (foreign keys on,
real transactions, UTC session time zone, UTF8 only) and never echoes the URL in an error. The
column types `UuidCol`, `InstantCol`, `DecimalCol`, `DocCol`, `Sha256Col`, `KeyCol` and `BoolCol`
store the same values the same way on both backends.
