# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``verify_database``: the read-only admin check of a store (Phase 2, P2-26).

Every ``doc`` is parsed by the strict loader and validated by its model; ``doc_sha256``, every
projected column and the rows of the side tables are recomputed from the validated model and
compared with what is stored. The result names the table, the wire ID and the version of what is
wrong and the schema identifier (column, side table, ``doc``) where; it never holds a value of a
document or of a column, and a damaged row is a finding, not an exception.

The check issues SELECT statements only and needs no migration. On Postgres it reads one
consistent snapshot (REPEATABLE READ), as ``load_run_bundle`` does.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.types import NullType

from tracer_evalschema import digest, loader
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.errors import DigestMismatch
from tracer_evalschema.ids import RunBundleId

from . import codecs
from .errors import StoreIntegrityError
from .repository import _MEMBERS, _PAIR_CHUNK
from .schema import metadata
from .tables import ENTITY_TABLES

__all__ = ["VERIFY_NOTES", "VERIFY_PROBLEMS", "VerifyFinding", "VerifyReport", "verify_database"]

VERIFY_PROBLEMS = (
    "doc_invalid",
    "digest_mismatch",
    "doc_sha256_mismatch",
    "projection_mismatch",
    "side_rows_mismatch",
)
VERIFY_NOTES = ("doc_text_not_canonical", "bundle_member_absent")


@dataclass(frozen=True)
class VerifyFinding:
    """One thing wrong (or, for a note, worth knowing) about one stored row.

    ``kind`` is the table, ``id`` the wire ID of the row, ``version`` the version of a versioned
    entity, ``problem`` one of ``VERIFY_PROBLEMS`` (``VERIFY_NOTES`` for a note) and ``location``
    a schema identifier: a column, a side table, ``doc`` or a bundle member kind. Never a value.
    """

    kind: str
    id: str
    version: int | None
    problem: str
    location: str | None


@dataclass(frozen=True)
class VerifyReport:
    """``findings`` are errors, ``informational`` are notes; both sorted. ``checked`` counts the
    rows examined in each of the 26 entity tables (zeros included)."""

    findings: tuple[VerifyFinding, ...]
    informational: tuple[VerifyFinding, ...]
    checked: dict[str, int]

    @property
    def ok(self) -> bool:
        return not self.findings


# --------------------------------------------------------------------------------- reading rows


class _Unreadable:
    """The stored value could not be turned into a Python value; it equals nothing."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<unreadable>"


_UNREADABLE = _Unreadable()
_FETCH = 500


def _rows(
    connection: Connection, table: sa.Table, where: Any = None, *, stream: bool = True
) -> Iterator[dict[str, Any]]:
    """The rows of a table with the column types applied one value at a time: a value the type
    refuses becomes ``_UNREADABLE`` instead of failing the whole read."""
    dialect = connection.dialect
    columns = [sa.type_coerce(column, NullType()).label(column.name) for column in table.c]
    statement = sa.select(*columns)
    if where is not None:
        statement = statement.where(where)
    if stream:
        connection = connection.execution_options(yield_per=_FETCH)
    for raw in connection.execute(statement).mappings():
        row: dict[str, Any] = {}
        for column in table.c:
            row[column.name] = _value(column, raw[column.name], dialect)
        yield row


def _value(column: sa.Column[Any], raw: object, dialect: Any) -> object:
    try:
        process: Any = column.type.process_result_value  # type: ignore[attr-defined]
        return process(raw, dialect)
    except NotImplementedError:  # a text type: the value is what the driver returned
        return raw
    except Exception:
        return _UNREADABLE


def _wire(prefix: str, value: object) -> str:
    return f"{prefix}_{value}" if isinstance(value, uuid.UUID) else f"{prefix}_unreadable"


def _int_or_none(value: object) -> int | None:
    return value if type(value) is int else None


def _freeze(row: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple((name, row[name]) for name in sorted(row))


# ------------------------------------------------------------------------------- side tables

# the columns that name the owner of a side row, per side table
_OWNER_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "snapshot_member": ("snapshot_id",),
    "snapshot_profile_ref": ("snapshot_id",),
    "external_ref_index": ("entity_kind", "entity_id"),
    "dataset_member": ("dataset_id", "dataset_version"),
    "export_bundle_run": ("export_id",),
    "export_bundle_item": ("export_id",),
    "export_bundle_profile": ("export_id",),
}
# entity table -> the entity_kind of the external references it owns
_EXTERNAL_KIND = {
    "run": "run",
    "stage_span": "span",
    "transcript": "transcript",
    "usage_record": "usage_record",
}


def _owner_key(side: str, table: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    if side == "dataset_member":
        return (row["id"], row["version"])
    if side == "external_ref_index":
        return (_EXTERNAL_KIND[table], row["id"])
    return (row["id"],)


# ---------------------------------------------------------------------------------- the check


class _Check:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.findings: set[VerifyFinding] = set()
        self.notes: set[VerifyFinding] = set()
        self.checked: dict[str, int] = {}
        # side table -> owner key -> (rows the document implies, the owner)
        self.expected: dict[str, dict[tuple[Any, ...], tuple[Counter[Any], VerifyFinding]]] = {}

    def problem(self, owner: VerifyFinding, problem: str, location: str | None) -> None:
        self.findings.add(VerifyFinding(owner.kind, owner.id, owner.version, problem, location))

    def note(self, owner: VerifyFinding, problem: str, location: str | None) -> None:
        self.notes.add(VerifyFinding(owner.kind, owner.id, owner.version, problem, location))

    def guarded(self, check: Callable[..., None], spec: Any, *args: Any) -> None:
        """Run one row's check; whatever a damaged row does to it is a finding, not an error."""
        try:
            check(spec, *args)
        except Exception:
            row = args[-1]
            version = _int_or_none(row.get("version"))
            owner = VerifyFinding(
                spec.table, _wire(spec.model.id_prefix, row.get("id")), version, "", None
            )
            self.problem(owner, "doc_invalid", "doc")

    def run(self) -> VerifyReport:
        for table in ENTITY_TABLES.values():
            spec = codecs._BY_TABLE[table.name]
            self.checked[table.name] = 0
            if spec.model is RunBundle:
                for row in list(_rows(self.connection, table, stream=False)):
                    self.checked[table.name] += 1
                    self.guarded(self.bundle, spec, row)
                continue
            for row in _rows(self.connection, table):
                self.checked[table.name] += 1
                self.guarded(self.entity, spec, table.name, row)
        self.compare_side_rows()
        order: Callable[[VerifyFinding], tuple[Any, ...]] = lambda f: (  # noqa: E731
            f.kind,
            f.id,
            -1 if f.version is None else f.version,
            f.problem,
            f.location or "",
        )
        return VerifyReport(
            tuple(sorted(self.findings, key=order)),
            tuple(sorted(self.notes, key=order)),
            dict(self.checked),
        )

    # ------------------------------------------------------------------------ one entity row

    def entity(self, spec: Any, table: str, row: dict[str, Any]) -> None:
        owner = VerifyFinding(
            table,
            _wire(spec.model.id_prefix, row["id"]),
            _int_or_none(row.get("version")),
            "",
            None,
        )
        text = row["doc"]
        if type(text) is not str:
            self.problem(owner, "doc_invalid", "doc")
            return
        try:
            model = codecs._load(spec, text)
        except DigestMismatch:
            # a digest the entity carries itself; any other model's digest failure is invalid input
            self.problem(
                owner,
                "digest_mismatch" if issubclass(spec.model, codecs._DIGESTED) else "doc_invalid",
                "doc",
            )
            return
        except Exception:
            self.problem(owner, "doc_invalid", "doc")
            return
        stored_run = row.get("run_id")
        run_id = stored_run if spec.storage_run_id and isinstance(stored_run, uuid.UUID) else None
        try:
            expected = codecs._row(spec, model, run_id)
        except Exception:
            self.problem(owner, "doc_invalid", "doc")
            return
        if not codecs._same(row["doc_sha256"], expected["doc_sha256"]):
            self.problem(owner, "doc_sha256_mismatch", "doc_sha256")
        for column, want in expected.items():
            if column not in ("doc", "doc_sha256") and not codecs._same(row.get(column), want):
                self.problem(owner, "projection_mismatch", column)
        canonical = str(expected["doc"])
        if text != canonical and _same_json(text, canonical):
            self.note(owner, "doc_text_not_canonical", "doc")
        if spec.side is not None:
            for side, rows in spec.side(model, run_id).items():
                key = _owner_key(side, table, row)
                self.expected.setdefault(side, {})[key] = (Counter(map(_freeze, rows)), owner)

    def compare_side_rows(self) -> None:
        for side, owners in self.expected.items():
            columns = _OWNER_COLUMNS[side]
            actual: dict[tuple[Any, ...], Counter[Any]] = {}
            for row in _rows(self.connection, metadata.tables[side]):
                key = tuple(row[c] for c in columns)
                if key in owners:
                    actual.setdefault(key, Counter())[_freeze(row)] += 1
            for key, (wanted, owner) in owners.items():
                if actual.get(key, Counter()) != wanted:
                    self.problem(owner, "side_rows_mismatch", side)

    # ----------------------------------------------------------------------------- run bundles

    def bundle(self, spec: Any, row: dict[str, Any]) -> None:
        owner = VerifyFinding("run_bundle", _wire("rbnd", row["id"]), None, "", None)
        text = row["doc"]
        try:
            if type(text) is not str:
                raise ValueError
            envelope = loader.loads_strict(text)
            if not isinstance(envelope, dict) or set(envelope) != codecs._ENVELOPE_FIELDS:
                raise ValueError
            sha = bytes.fromhex(digest.sha256_hex(digest.jcs_bytes(envelope)))
            bundle_uuid = RunBundleId.parse(envelope["id"]).uuid
            created = datetime.fromisoformat(envelope["created_at"]).astimezone(UTC)
            schema_version = envelope["schema_version"]
        except Exception:
            self.problem(owner, "doc_invalid", "doc")
            return
        # the envelope is checked by itself, so it is reported even when a member is damaged
        envelope_columns = {
            "doc_sha256": sha,
            "id": bundle_uuid,
            "schema_version": schema_version,
            "created_at": created,
        }
        for column, want in envelope_columns.items():
            if not codecs._same(row[column], want):
                location = column
                self.problem(
                    owner,
                    "doc_sha256_mismatch" if column == "doc_sha256" else "projection_mismatch",
                    location,
                )
        members = self.members(owner, row)
        if members is None:
            return
        records, absent = members
        for kind in sorted(absent):
            self.note(owner, "bundle_member_absent", kind)
        if absent or records is None:
            return
        # assemble the whole bundle; the columns already compared above are taken from the
        # envelope so that they are not reported twice and do not hide the ones that remain
        probe = {**row, **envelope_columns}
        member_rows, withheld_rows = self.listing(row["id"])
        try:
            built = codecs.decode_run_bundle(
                probe,
                members=member_rows,
                withheld=withheld_rows,
                resolve=lambda kind, record_id, version: records[kind, record_id, version],
            )
        except StoreIntegrityError as error:
            if error.column == "doc":
                self.problem(owner, "doc_invalid", "doc")
            else:
                self.problem(owner, "projection_mismatch", error.column)
            return
        except Exception:
            self.problem(owner, "doc_invalid", "doc")
            return
        canonical = codecs._document(spec, built)[0]
        if text != canonical and _same_json(text, canonical):
            self.note(owner, "doc_text_not_canonical", "doc")
        wanted = codecs._side_run_bundle(built, None)
        for side, rows in (
            ("run_bundle_member", member_rows),
            ("run_bundle_withheld", withheld_rows),
        ):
            if Counter(map(_freeze, rows)) != Counter(map(_freeze, wanted[side])):
                self.problem(owner, "side_rows_mismatch", side)

    def listing(self, bundle_id: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        member, withheld = (
            metadata.tables["run_bundle_member"],
            metadata.tables["run_bundle_withheld"],
        )
        return (
            list(_rows(self.connection, member, member.c.bundle_id == bundle_id, stream=False)),
            list(_rows(self.connection, withheld, withheld.c.bundle_id == bundle_id, stream=False)),
        )

    def members(
        self, owner: VerifyFinding, row: dict[str, Any]
    ) -> tuple[dict[tuple[str, uuid.UUID, int | None], Any] | None, set[str]] | None:
        """The decoded records of a bundle's members and the member kinds that are not stored.

        ``None`` for the records when a stored member does not verify (it is reported under its
        own table); ``None`` for the whole result when the member rows themselves are damaged.
        """
        member_rows, _withheld = self.listing(row["id"])
        try:
            wanted = [(m["kind"], m["record_id"], m["record_version"]) for m in member_rows]
            if any(not isinstance(rid, uuid.UUID) for _k, rid, _v in wanted):
                raise ValueError
        except Exception:
            self.problem(owner, "side_rows_mismatch", "run_bundle_member")
            return None
        records: dict[tuple[str, uuid.UUID, int | None], Any] = {}
        absent: set[str] = set()
        damaged = False
        for kind, table_name, _id_type, _field, versioned in _MEMBERS:
            keys = [(rid, v) for k, rid, v in wanted if k == kind]
            if not keys:
                continue
            table = metadata.tables[table_name]
            found: set[tuple[uuid.UUID, int | None]] = set()
            for start in range(0, len(keys), _PAIR_CHUNK):
                chunk = keys[start : start + _PAIR_CHUNK]
                if versioned:
                    where = sa.or_(
                        *(sa.and_(table.c.id == i, table.c.version == v) for i, v in chunk)
                    )
                else:
                    where = table.c.id.in_([i for i, _ in chunk])
                for stored in _rows(self.connection, table, where, stream=False):
                    key = (stored["id"], stored["version"] if versioned else None)
                    found.add(key)
                    try:
                        records[kind, key[0], key[1]] = codecs.decode(table_name, stored)
                    except Exception:
                        damaged = True
            if any(key not in found for key in keys):
                absent.add(kind)
        return (None if damaged else records), absent


def _same_json(left: str, right: str) -> bool:
    """The two texts hold the same JSON value (they differ only in white space or layout)."""
    try:
        return bool(json.loads(left) == json.loads(right))
    except ValueError:
        return False


def verify_database(engine: Engine) -> VerifyReport:
    """Check every row of the 26 entity tables and the side rows their documents imply.

    Read-only; works on any schema the package builds (migrated or created). A damaged row gives
    findings, never an exception, and no finding holds a value. ``engine`` is the engine of
    ``create_store_engine``.
    """
    if not isinstance(engine, Engine):
        raise TypeError("the first argument must be the Engine of create_store_engine")
    reader: Engine = engine
    if engine.dialect.name == "postgresql":
        reader = engine.execution_options(isolation_level="REPEATABLE READ")
    with reader.connect() as connection:
        try:
            return _Check(connection).run()
        finally:
            connection.rollback()
