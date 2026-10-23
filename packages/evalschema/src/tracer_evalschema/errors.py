# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Typed errors of tracer-evalschema (ADR 0008 Decision 2).

Pydantic v2 turns only ``ValueError``, ``AssertionError`` and ``PydanticCustomError`` raised in a
validator into a ``ValidationError``. Every error below that derives from ``TracerSchemaError`` and
not from ``ValueError`` therefore propagates as itself out of ``model_validate``,
``model_validate_json``, ``TypeAdapter.validate_*`` and the strict loader, also from nested
models. Such an error carries no pydantic location, so its message names the offending value,
cut to 64 code points and ``...`` when longer (``_constraints.echoed``, P1-21 finding m4), except
where the value may be secret: ``DigestKeyUnavailable`` names the rule only, never a key id.

``ConfusablePrefixError``, ``DataFileError`` and ``WeakDigestKeyError`` are ``ValueError``s on
purpose: they are raised by ``ids.validate_new_prefix()``, the data loaders and
``digest.compute_digest``/``verify_digest`` with a caller's key, never inside model validation.
"""

from __future__ import annotations

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema._constraints import echoed


class TracerSchemaError(Exception):
    """Base class of every typed error of the package."""


# ------------------------------------------------------------------------------ schema version


class SchemaVersionError(TracerSchemaError):
    """A document's ``schema_version`` cannot be read by this version of the package."""

    found: object
    expected: str

    def __init__(self, found: object, expected: str = SCHEMA_VERSION) -> None:
        super().__init__(found, expected)
        self.found = found
        self.expected = expected

    def __str__(self) -> str:
        return f"{self._problem}: found {echoed(self.found)}, expected {self.expected!r}"

    _problem = "unreadable schema_version"


class SchemaVersionTooNew(SchemaVersionError):
    """The document was written by a newer schema version (fields are never dropped)."""

    _problem = "schema_version is newer than this reader"


class SchemaVersionMismatch(SchemaVersionError):
    """The document was written by an older schema version (exact match while 0.0.x)."""

    _problem = "schema_version is older than this reader and no upgrade step exists"


class SchemaVersionInvalid(SchemaVersionError):
    """The value is not a strict ``MAJOR.MINOR.PATCH`` string."""

    _problem = "schema_version is not a MAJOR.MINOR.PATCH string"


# ------------------------------------------------------------------------------------- digests


class UnknownDigestScheme(TracerSchemaError):
    """A ``ContentDigest.scheme`` that is not in the scheme registry (never "accept")."""


class DigestKeyUnavailable(TracerSchemaError):
    """A keyed digest cannot be checked: no key resolver, or the resolver gave no key.

    The message names the failure kind only, never the ``key_id``, ``key_version`` or
    ``provider_hint`` of the ``KeyRef`` (P1-21 finding C3a) and nothing of a resolver's exception,
    which stays reachable as ``__cause__``."""


class WeakDigestKeyError(DigestKeyUnavailable, ValueError):
    """A key for a keyed digest scheme (``hmac-sha256-jcs/v1``) is shorter than 32 bytes
    (P1-21 finding m1). A ``DigestKeyUnavailable`` (no usable key) and a ``ValueError`` (a bad
    argument). The message names the rule and the scheme, never key material or the key id."""


class DigestMismatch(TracerSchemaError):
    """A digest was checked and does not match the content."""


class NonIJsonValueError(TracerSchemaError):
    """A value outside I-JSON (RFC 7493) cannot be canonicalized (RFC 8785)."""


# -------------------------------------------------------------------------------------- loader


class DuplicateKeyError(TracerSchemaError):
    """A JSON object repeats a key (after unescaping)."""


class DepthLimitExceeded(TracerSchemaError):
    """A JSON document nests deeper than the loader allows (never a ``RecursionError``)."""


# ------------------------------------------------------------------------------------- bundles


class BundleError(TracerSchemaError):
    """A set of documents that cannot be exported or read together."""


class MixedSchemaVersionError(BundleError):
    """The documents of one bundle carry different ``schema_version`` values."""


class ConflictingVersionError(BundleError):
    """Two documents share ``(id, version)`` but not ``content_digest``."""


class ConflictingRecordError(BundleError):
    """Two records of fact share an ``id`` but not their content."""


class RefDigestMismatchError(BundleError):
    """A reference's digest disagrees with the entity it resolves to in the bundle."""


class UnresolvedReferenceError(BundleError):
    """A reference names an ``(id, version)`` that the bundle does not hold, where the seam is
    asked to require resolution (``check_bundle_consistency(..., require_resolved=True)``; a
    result's ``metric`` and a verdict check's ``metric_ref`` in a ``RunBundle``, B10)."""


class BundleSizeError(BundleError):
    """A serialized bundle is larger than the loader's limit (``loader.load_bundle``, S-N94). The
    message names the two sizes and no content."""


# ------------------------------------------------------------- raised outside model validation


class ConfusablePrefixError(TracerSchemaError, ValueError):
    """A proposed ID prefix is registered already or confusable with a registered one."""


class DataFileError(ValueError):
    """A vendored or caller-supplied data file is malformed."""
