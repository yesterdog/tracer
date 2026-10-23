# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Consistency rules for a set of documents exported or read together (ADR 0008, ADR 0009).

``check_bundle_consistency`` works on JSON-mode documents, so it can judge a bundle without
building its entities (``RunBundle`` and ``ExportBundle`` call it, P1-14):

- one ``schema_version`` for every document (``MixedSchemaVersionError``);
- a versioned document (it has ``version``): one ``(id, version)`` has one ``content_digest``
  (``ConflictingVersionError``); identical duplicates and other versions are fine;
- a record of fact (no ``version``): one ``id`` has one content (``ConflictingRecordError``);
  a correction is a new record with a new id (P-47);
- a reference ``{id, version, content_digest?}`` needs ``version``; when the bundle holds that
  ``(id, version)`` and both digests can be compared (same scheme, and for a keyed scheme the
  same ``key_ref``), they must be equal (``RefDigestMismatchError``). A digest that cannot be
  compared here is verified elsewhere with ``digest.verify_digest`` and is never reported as
  matching.

Content is compared on the digest domain (ADR 0006 Decision 5), so a ``null`` member and an
absent one are the same content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tracer_evalschema.digest import digest_domain
from tracer_evalschema.errors import (
    BundleError,
    ConflictingRecordError,
    ConflictingVersionError,
    MixedSchemaVersionError,
    RefDigestMismatchError,
)

__all__ = ["check_bundle_consistency"]


def _digest_fields(digest: Any) -> dict[str, Any] | None:
    if not isinstance(digest, Mapping):
        return None
    return {key: value for key, value in digest.items() if value is not None}


def _comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Two digests can be compared without a key: same scheme and the same key_ref (if any)."""
    return left.get("scheme") == right.get("scheme") and left.get("key_ref") == right.get("key_ref")


def _require_id(doc: Mapping[str, Any], what: str) -> str:
    identity = doc.get("id")
    if not isinstance(identity, str):
        raise BundleError(f"{what} without a string id: {identity!r}")
    return identity


def check_bundle_consistency(
    docs: Sequence[Mapping[str, Any]], refs: Sequence[Mapping[str, Any]] = ()
) -> None:
    """Raise a ``BundleError`` subclass if the documents cannot belong to one bundle."""
    versions = {repr(doc.get("schema_version")) for doc in docs}
    if len(versions) > 1:
        raise MixedSchemaVersionError(
            f"a bundle holds one schema_version, found {', '.join(sorted(versions))}"
        )

    versioned: dict[tuple[str, Any], dict[str, Any] | None] = {}
    records: dict[str, dict[str, Any]] = {}
    for doc in docs:
        identity = _require_id(doc, "document")
        if "version" in doc:
            key = (identity, doc["version"])
            digest = _digest_fields(doc.get("content_digest"))
            if key in versioned and versioned[key] != digest:
                raise ConflictingVersionError(
                    f"{identity} version {doc['version']!r} appears with two content digests"
                )
            versioned[key] = digest
        else:
            content = digest_domain(doc)
            if identity in records and records[identity] != content:
                raise ConflictingRecordError(
                    f"record {identity} appears with two different contents; a correction "
                    "is a new record with a new id"
                )
            records[identity] = content

    for ref in refs:
        identity = _require_id(ref, "reference")
        if not isinstance(ref.get("version"), int) or isinstance(ref.get("version"), bool):
            raise BundleError(f"reference to {identity} has no integer version")
        ref_digest = _digest_fields(ref.get("content_digest"))
        entity_digest = versioned.get((identity, ref["version"]))
        if ref_digest is None or entity_digest is None:
            continue
        if _comparable(ref_digest, entity_digest) and ref_digest != entity_digest:
            raise RefDigestMismatchError(
                f"reference to {identity} version {ref['version']} carries a digest that does "
                "not match the entity in the bundle"
            )
