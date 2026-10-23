# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Entity bases, the schema version check and versioned references (ADR 0001, 0007, 0008, 0009).

- ``EntityBase``: ``id`` (typed by the subclass's ``id_prefix``), ``schema_version``,
  ``created_at``, ``metadata`` and the load-verified ``metadata_flags``.
- ``VersionedEntity``: adds ``version`` and a ``content_digest`` that is verified on load.
- ``VersionedRef``: ``{id, version, content_digest?}``, a reference to a versioned entity.
- ``check_schema_version``: the reader rule of ADR 0008 Decision 2 (exact match while 0.0.x).

The version is checked before any field, and its errors are typed and not wrapped by pydantic.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, Strict, model_validator

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema.credentials import (
    HIGH_ENTROPY_TOKEN,
    KNOWN_CREDENTIAL_PREFIX,
    credential_reason,
)
from tracer_evalschema.digest import ContentDigest, verify_digest
from tracer_evalschema.errors import (
    SchemaVersionInvalid,
    SchemaVersionMismatch,
    SchemaVersionTooNew,
)
from tracer_evalschema.ids import TypedId, id_type
from tracer_evalschema.ijson import IJsonObject
from tracer_evalschema.primitives import UtcDatetime
from tracer_evalschema.text import VerbatimText

__all__ = [
    "SCHEMA_VERSION",
    "EntityBase",
    "MetadataFlag",
    "VersionedEntity",
    "VersionedRef",
    "check_schema_version",
    "compute_metadata_flags",
    "json_string_leaves",
    "verify_entity_digest",
]

# ----------------------------------------------------------------------------- schema version

_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)


def _version_tuple(text: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def check_schema_version(value: object) -> None:
    """Accept exactly ``SCHEMA_VERSION`` (ADR 0008 Decision 2, exact match while 0.0.x).

    Raises ``SchemaVersionInvalid`` unless ``value`` is a ``str`` of the form
    ``MAJOR.MINOR.PATCH`` (no leading zeros, no pre-release or build suffix, no white space),
    then compares the numbers: newer is ``SchemaVersionTooNew``, older ``SchemaVersionMismatch``.
    """
    if type(value) is not str or _SEMVER.fullmatch(value) is None:
        raise SchemaVersionInvalid(found=value, expected=SCHEMA_VERSION)
    found, expected = _version_tuple(value), _version_tuple(SCHEMA_VERSION)
    if found > expected:
        raise SchemaVersionTooNew(found=value, expected=SCHEMA_VERSION)
    if found < expected:
        raise SchemaVersionMismatch(found=value, expected=SCHEMA_VERSION)


# ------------------------------------------------------------------------------ metadata flags


class MetadataFlag(BaseModel):
    """A secret-looking string in ``metadata``: where it is and why (ADR 0007 Decision 5a)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: VerbatimText
    reason: Annotated[str, ClosedSet((KNOWN_CREDENTIAL_PREFIX, HIGH_ENTROPY_TOKEN))]


def _pointer_token(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")  # RFC 6901: "~" first


def json_string_leaves(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string leaf of a JSON value with its RFC 6901 pointer (``path`` is the prefix).

    Object member values and array elements are visited in document order; keys, numbers,
    booleans and nulls are not leaves.
    """
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, Mapping):
        return [
            leaf
            for key, item in value.items()
            for leaf in json_string_leaves(item, f"{path}/{_pointer_token(str(key))}")
        ]
    if isinstance(value, (list, tuple)):
        return [
            leaf
            for index, item in enumerate(value)
            for leaf in json_string_leaves(item, f"{path}/{index}")
        ]
    return []


def compute_metadata_flags(metadata: Mapping[str, Any]) -> list[MetadataFlag]:
    """The flags of ``metadata``: one per secret-looking string leaf, at any depth.

    Paths are RFC 6901 JSON Pointers from the entity document root (``/metadata/...``); keys,
    numbers, booleans and nulls are never flagged. The list is sorted by path, comparing UTF-16
    code units (the order JCS uses for keys), so it is canonical.
    """
    flags = []
    for path, text in json_string_leaves(metadata, "/metadata"):
        reason = credential_reason(text)
        if reason is not None:
            flags.append(MetadataFlag(path=path, reason=reason))
    return sorted(flags, key=lambda flag: flag.path.encode("utf-16-be"))


# ----------------------------------------------------------------------------------- entities


class EntityBase(BaseModel):
    """Base of every entity (ADR 0001, 0007, 0008).

    A subclass sets ``id_prefix`` (a registered prefix); its ``id`` field then accepts only IDs of
    that kind. ``schema_version`` is required and checked before any field. ``metadata`` is a
    user-owned I-JSON object (RFC 7493, ``ijson.IJsonObject``), preserved byte for byte: NaN,
    infinities, integers outside +-(2**53 - 1), lone surrogates and Python values that are not JSON
    (tuples, sets, bytes, non-string keys) are rejected with the JSON Pointer of the member, never
    its value. ``metadata_flags`` is computed from it when omitted, must equal the computed list
    when supplied, and is always serialized.
    """

    model_config = ConfigDict(extra="forbid")

    id_prefix: ClassVar[str]

    id: TypedId
    schema_version: Annotated[str, ClosedSet((SCHEMA_VERSION,))]
    created_at: UtcDatetime
    metadata: IJsonObject = Field(default_factory=dict)
    metadata_flags: list[MetadataFlag] = Field(default_factory=list)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Runs while the class is created and before pydantic collects the fields, so the
        # subclass's own `id` annotation is the ID class of its prefix.
        super().__init_subclass__(**kwargs)
        prefix = cls.__dict__.get("id_prefix")
        if prefix is not None:
            cls.__annotations__ = {**cls.__annotations__, "id": id_type(prefix)}

    @model_validator(mode="before")
    @classmethod
    def _version_first_then_flags(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        if "schema_version" in data:
            check_schema_version(data["schema_version"])  # typed errors, not wrapped
        metadata = data.get("metadata", {})
        if "metadata_flags" not in data and isinstance(metadata, Mapping):
            flags = compute_metadata_flags(metadata)
            return {**data, "metadata_flags": [flag.model_dump() for flag in flags]}
        return data

    @model_validator(mode="after")
    def _verify_metadata_flags(self) -> EntityBase:
        if self.metadata_flags != compute_metadata_flags(self.metadata):
            raise ValueError(
                "metadata_flags do not equal the flags computed from metadata (entries, reasons "
                "and order must match; omit the field to have it computed)"
            )
        return self


Version = Annotated[int, Strict(), Field(ge=1)]


def verify_entity_digest(subject: BaseModel | Mapping[str, Any], digest: ContentDigest) -> None:
    """The one place where an entity's own ``content_digest`` is verified on load (a versioned
    entity, and the digested ConfigSnapshot). It calls ``digest.verify_digest`` through this
    module's attribute, so a test can switch the check off while it builds a document."""
    verify_digest(subject, digest)


class VersionedEntity(EntityBase):
    """An entity with versions (ADR 0009): ``(id, version)`` names one immutable content.

    ``content_digest`` covers the JSON-mode document (ADR 0006 Decision 5) and is verified on
    load; a mismatch raises ``DigestMismatch`` and a keyed digest ``DigestKeyUnavailable``
    (there is no key resolver at this layer). Neither is wrapped by pydantic. An entity with an
    entity-specific exclusion (ADR 0006 Decision 5; the Evaluator, N20) overrides
    ``digest_subject``.
    """

    version: Version
    content_digest: ContentDigest

    def digest_subject(self) -> BaseModel | Mapping[str, Any]:
        """What ``content_digest`` is computed over: the model itself (the generic domain)."""
        return self

    @model_validator(mode="after")
    def _verify_content_digest(self) -> VersionedEntity:
        verify_entity_digest(self.digest_subject(), self.content_digest)
        return self


class VersionedRef(BaseModel):
    """A reference to one version of a versioned entity (ADR 0009 Decision 4).

    ``id`` is the typed ID of the referenced entity (any registered kind) and ``version`` is
    required; ``content_digest`` is optional (shared exports may drop it).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: TypedId
    version: Version
    content_digest: ContentDigest | None = None
