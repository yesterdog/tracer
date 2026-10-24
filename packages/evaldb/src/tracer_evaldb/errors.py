# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Typed errors of the store. No message ever holds a database URL."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from tracer_evalschema.errors import ConflictingRecordError
from tracer_evalschema.ids import TypedId

if TYPE_CHECKING:
    from .repository import MissingMember

__all__ = [
    "DigestChangingUpgrade",
    "DowngradeDataLoss",
    "ExportReferenceNotStored",
    "IncompleteRunBundle",
    "InternalSnapshotRefused",
    "IrreversibleMigration",
    "MigrationIntegrityError",
    "MixedSchemaVersions",
    "ProjectNotStored",
    "RecordNotStored",
    "RecordOwnershipConflict",
    "ReimportConflict",
    "SnapshotInUse",
    "StoreConfigurationError",
    "StoreConnectionError",
    "StoreConstraintViolation",
    "StoreError",
    "StoreIntegrityError",
    "StoreNotMigrated",
    "StoreRevisionUnknown",
    "StoreSchemaVersionError",
    "StoreSchemaVersionMismatch",
    "StoreSchemaVersionTooNew",
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


class ReimportConflict(ConflictingRecordError):
    """An arriving run shares an external reference with a stored run that is a different run, or
    the same run with other content (ADR 0022 Decision 11).

    Not a ``StoreError``: it is the evalschema conflict class, so a caller that catches that class
    catches this. The message names the kind and the stored ID, never the reference.
    """

    def __init__(self, entity_kind: str, stored_id: TypedId) -> None:
        self.entity_kind = entity_kind
        self.stored_id = stored_id
        super().__init__(
            f"the {entity_kind} arriving shares an external reference with the stored "
            f"{entity_kind} {stored_id} and is not the same {entity_kind} with the same content"
        )


class RecordOwnershipConflict(StoreError):
    """A record of fact with this ID is stored and belongs to another run."""

    def __init__(self, kind: str, record_id: TypedId) -> None:
        self.kind = kind
        self.record_id = record_id
        super().__init__(f"{kind} {record_id} is stored and belongs to another run")


class ProjectNotStored(StoreError):
    """The project a run names is not stored; the store never creates one."""

    def __init__(self, project_id: TypedId) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} is not stored")


class InternalSnapshotRefused(StoreError, ValueError):
    """A snapshot whose profile reference has an absent or keyed digest is not internal data."""

    def __init__(self, snapshot_id: TypedId) -> None:
        self.snapshot_id = snapshot_id
        super().__init__(
            f"snapshot {snapshot_id} cannot be stored: an internal snapshot records the plain "
            "digest of its test profile, and it is absent or keyed here"
        )


class RecordNotStored(StoreError):
    """No row has this ID (and version)."""

    def __init__(self, kind: str, record_id: TypedId, version: int | None = None) -> None:
        self.kind = kind
        self.record_id = record_id
        self.version = version
        text = f"{kind} {record_id}"
        if version is not None:
            text += f" version {version}"
        super().__init__(f"{text} is not stored")


class IncompleteRunBundle(StoreError):
    """Members of a stored run bundle are missing (deleted rows). Names the bundle and the number
    missing, never a record's content."""

    def __init__(self, bundle_id: TypedId, missing: Sequence[MissingMember]) -> None:
        self.bundle_id = bundle_id
        self.missing = tuple(missing)
        super().__init__(
            f"run bundle {bundle_id} is incomplete: {len(self.missing)} member records are missing"
        )


class ExportReferenceNotStored(StoreError):
    """A run, dataset item or test profile that an export carries is not stored."""

    def __init__(self, kind: str, record_id: TypedId) -> None:
        self.kind = kind
        self.record_id = record_id
        super().__init__(f"the export carries {kind} {record_id}, which is not stored")


class StoreConstraintViolation(StoreError):
    """The database refused a write. Names the kind of constraint and, when the database says so,
    the table and the constraint; never a value."""

    def __init__(self, kind: str, table: str | None = None, constraint: str | None = None) -> None:
        self.kind = kind
        self.table = table
        self.constraint = constraint
        text = f"the database refused a write: {kind.replace('_', ' ')} constraint violated"
        if table is not None:
            text += f" (table {table!r})"
        if constraint is not None:
            text += f" (constraint {constraint!r})"
        super().__init__(text)


class StoreNotMigrated(StoreError):
    """The database has no revision, or is not at the head revision this evaldb ships."""

    def __init__(self, revision: str | None, head: str) -> None:
        self.revision = revision
        self.head = head
        found = "no revision" if revision is None else f"revision {revision}"
        super().__init__(
            f"the database is not migrated: it is at {found} and this evaldb ships {head}; "
            "run tracer_evaldb.migrate.upgrade(engine)"
        )


class StoreRevisionUnknown(StoreError):
    """The database is at a revision this evaldb does not ship (a newer evaldb migrated it)."""

    def __init__(self, revision: str) -> None:
        self.revision = revision
        super().__init__(f"the database is at revision {revision}, which this evaldb does not ship")


class StoreSchemaVersionError(StoreError):
    """The schema version recorded in ``tracer_meta`` is not the installed one."""

    def __init__(self, stored: str | None, installed: str, problem: str) -> None:
        self.stored = stored
        self.installed = installed
        found = "no version" if stored is None else repr(stored)
        super().__init__(f"{problem}: the store records {found}, installed is {installed!r}")


class StoreSchemaVersionTooNew(StoreSchemaVersionError):
    """The store holds documents of a newer schema version than the installed evalschema reads."""


class StoreSchemaVersionMismatch(StoreSchemaVersionError):
    """The store holds documents of an older, unreadable or unrecorded schema version."""


class IrreversibleMigration(StoreError):
    """A data migration cannot restore every stored byte on downgrade; raised before any change."""

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"the migration cannot be reversed byte for byte (table {table!r})")


class DigestChangingUpgrade(StoreError):
    """An upgrade would change the content digest of a versioned entity or a snapshot."""

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"the upgrade would change a content digest (table {table!r})")


class MixedSchemaVersions(StoreError):
    """A data migration left a document at a schema version other than its target."""

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"a document of table {table!r} is not at the target schema version")


class DowngradeDataLoss(StoreError):
    """A downgrade would destroy rows; pass ``allow_data_loss=True`` to accept."""

    def __init__(self, tables: Sequence[str]) -> None:
        self.tables = tuple(sorted(set(tables)))
        super().__init__(
            f"the downgrade would destroy rows of: {', '.join(self.tables)}; "
            "pass allow_data_loss=True to accept"
        )


class MigrationIntegrityError(StoreError):
    """``PRAGMA foreign_key_check`` reported rows after a migration step (rolled back)."""

    def __init__(self, tables: Sequence[str]) -> None:
        self.tables = tuple(sorted(set(tables)))
        super().__init__(f"the migration left foreign key violations in: {', '.join(self.tables)}")


class SnapshotInUse(StoreError):
    """A snapshot that a run references cannot be deleted."""

    def __init__(self, snapshot_id: TypedId) -> None:
        self.snapshot_id = snapshot_id
        super().__init__(f"snapshot {snapshot_id} is referenced by a run and cannot be deleted")
