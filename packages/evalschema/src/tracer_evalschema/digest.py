# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Content digests: the scheme registry, the digest domain and the one verification entry point.

ADR 0006 Decisions 3-5. This is the only module that hashes or compares digests (``hashlib``,
``hmac`` and ``rfc8785`` are imported nowhere else; a source scan checks it).

- ``sha256-jcs/v1``: SHA-256 of the RFC 8785 (JCS) canonical UTF-8 JSON of the digest domain;
  ``key_ref`` must be absent.
- ``hmac-sha256-jcs/v1``: HMAC-SHA-256 of the same bytes; ``key_ref`` names the key, which is
  resolved outside the schema and never stored.

The digest domain is defined on the JSON document alone, so a verifier in any language needs no
model: drop the top-level ``content_digest`` and ``schema_version``, then drop every object
member whose value is ``null`` at any depth (array elements are kept, ``null`` included).

``sha256_hex`` is the plain SHA-256 of a byte string, for callers that hash files rather than
documents (the ``schema.lock`` of ``schema_export``); it keeps ``hashlib`` in this module.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, NoReturn

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracer_evalschema._constraints import ClosedSet, FullMatch
from tracer_evalschema._hiding import values_hidden
from tracer_evalschema.errors import (
    DigestKeyUnavailable,
    DigestMismatch,
    NonIJsonValueError,
    UnknownDigestScheme,
)
from tracer_evalschema.text import MachineKey, VerbatimText

__all__ = [
    "DIGEST_SCHEMES",
    "KEYED_SCHEME",
    "PLAIN_SCHEME",
    "ContentDigest",
    "DigestScheme",
    "KeyRef",
    "canonical_bytes",
    "compute_digest",
    "digest_domain",
    "jcs_bytes",
    "sha256_hex",
    "verify_digest",
]

PLAIN_SCHEME = "sha256-jcs/v1"
KEYED_SCHEME = "hmac-sha256-jcs/v1"


@dataclass(frozen=True)
class DigestScheme:
    """A registered digest scheme."""

    name: str
    keyed: bool
    description: str


DIGEST_SCHEMES: Mapping[str, DigestScheme] = MappingProxyType(
    {
        PLAIN_SCHEME: DigestScheme(PLAIN_SCHEME, False, "SHA-256 over RFC 8785 (JCS) bytes"),
        KEYED_SCHEME: DigestScheme(KEYED_SCHEME, True, "HMAC-SHA-256 over RFC 8785 (JCS) bytes"),
    }
)
"""The scheme registry (read-only). Registering a scheme is an additive schema change."""


def _unknown_scheme(value: str, registered: tuple[str, ...]) -> NoReturn:
    if values_hidden():  # inside a model that hides its input: the rule, not the value
        raise UnknownDigestScheme(f"unknown digest scheme (registered: {', '.join(registered)})")
    raise UnknownDigestScheme(
        f"unknown digest scheme {value!r} (registered: {', '.join(registered)})"
    )


# --------------------------------------------------------------------------------- the models

HexDigest = Annotated[
    str, FullMatch("^[0-9a-f]{64}$", "digest_value", "{value} is not 64 lower-case hex digits")
]


class KeyRef(BaseModel):
    """Names a key held by a key manager; never carries key material (ADR 0007 Decision 4).

    ``key_id`` is the key manager's own name for the key, an opaque string stored as given
    (ADR 0001 I12), not one of our typed IDs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: Annotated[VerbatimText, Field(min_length=1)]
    key_version: Annotated[VerbatimText, Field(min_length=1)] | None = None
    provider_hint: MachineKey | None = None


class ContentDigest(BaseModel):
    """``{scheme, value, key_ref?}`` (ADR 0006 Decision 3). An unregistered scheme raises
    ``UnknownDigestScheme`` (not wrapped by pydantic): it is never "unverifiable, accept"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Annotated[str, ClosedSet(tuple(DIGEST_SCHEMES), _unknown_scheme)]
    value: HexDigest
    key_ref: KeyRef | None = None

    @model_validator(mode="after")
    def _key_ref_matches_scheme(self) -> ContentDigest:
        keyed = DIGEST_SCHEMES[self.scheme].keyed
        if keyed and self.key_ref is None:
            raise ValueError(f"scheme {self.scheme} requires key_ref (the KeyRef of the HMAC key)")
        if not keyed and self.key_ref is not None:
            raise ValueError(f"scheme {self.scheme} must not carry key_ref")
        return self


# ------------------------------------------------------------------------------ the domain


def _without_null_members(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_null_members(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_without_null_members(item) for item in value]
    return value


def digest_domain(obj: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    """The JSON object that is hashed (ADR 0006 Decision 5). The input is not changed.

    A model is dumped with ``model_dump(mode="json", by_alias=True)`` first. Then the top-level
    ``content_digest`` and ``schema_version`` are removed, and every object member whose value
    is ``null`` is removed at every depth; arrays keep all their elements. Exclusions deeper
    than the top level belong to the entity that declares them.
    """
    if isinstance(obj, BaseModel):
        document: Any = obj.model_dump(mode="json", by_alias=True)
    elif isinstance(obj, Mapping):
        document = obj
    else:
        raise TypeError(f"a digest needs a model or a mapping, got {type(obj).__name__}")
    top = {k: v for k, v in document.items() if k not in ("content_digest", "schema_version")}
    domain: dict[str, Any] = _without_null_members(top)
    return domain


def jcs_bytes(document: Any) -> bytes:
    """RFC 8785 (JCS) bytes of a JSON-mode value, exactly as given (no digest-domain rule is
    applied); ``NonIJsonValueError`` outside I-JSON. The sealed-payload AAD uses it
    (``sealed.aad_bytes``), so ``rfc8785`` stays imported in this module only."""
    try:
        return rfc8785.dumps(document)
    except rfc8785.CanonicalizationError as exc:
        raise NonIJsonValueError(f"value cannot be canonicalized (RFC 8785): {exc}") from exc


def canonical_bytes(obj: BaseModel | Mapping[str, Any]) -> bytes:
    """RFC 8785 (JCS) bytes of the digest domain; ``NonIJsonValueError`` outside I-JSON."""
    return jcs_bytes(digest_domain(obj))


def sha256_hex(data: bytes) -> str:
    """Lowercase hexadecimal SHA-256 of ``data`` (64 characters).

    Only ``bytes`` are accepted: a ``str`` (or any other type) raises ``TypeError`` instead of
    being encoded with a guessed encoding, so the hash of a file is always the hash of its bytes.
    """
    if not isinstance(data, bytes):
        raise TypeError(f"sha256_hex() takes bytes, not {type(data).__name__}")
    return hashlib.sha256(data).hexdigest()


def _hex_digest(scheme: str, payload: bytes, key: bytes | None) -> str:
    if DIGEST_SCHEMES[scheme].keyed:
        if key is None:  # pragma: no cover - callers settle the key first
            raise DigestKeyUnavailable(f"scheme {scheme} needs a key")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def compute_digest(
    obj: BaseModel | Mapping[str, Any],
    *,
    scheme: str = PLAIN_SCHEME,
    key: bytes | None = None,
    key_ref: KeyRef | None = None,
) -> ContentDigest:
    """Digest ``obj`` with a registered scheme.

    The plain scheme takes no key; the keyed scheme needs both the key bytes and the ``KeyRef``
    that names them (``ValueError`` otherwise). The key itself is never part of the result.
    """
    if scheme not in DIGEST_SCHEMES:
        _unknown_scheme(scheme, tuple(DIGEST_SCHEMES))
    if DIGEST_SCHEMES[scheme].keyed:
        if key is None or key_ref is None:
            raise ValueError(f"scheme {scheme} needs both key and key_ref")
        if not isinstance(key, bytes):
            raise TypeError("key must be bytes")
    elif key is not None or key_ref is not None:
        raise ValueError(f"scheme {scheme} takes no key and no key_ref")
    value = _hex_digest(scheme, canonical_bytes(obj), key)
    return ContentDigest(scheme=scheme, value=value, key_ref=key_ref)


def _resolve_key(
    digest: ContentDigest, key_resolver: Callable[[KeyRef], bytes | None] | None
) -> bytes:
    if key_resolver is None or digest.key_ref is None:
        raise DigestKeyUnavailable(
            f"keyed digest ({digest.scheme}) cannot be verified without a key resolver"
        )
    try:
        key = key_resolver(digest.key_ref)
    except Exception as exc:  # any resolver failure means "no key", never success
        # S-N91: the message names the rule and the key_id only; the resolver's error (text,
        # arguments, class) is never quoted and stays reachable as the chained cause
        raise DigestKeyUnavailable(
            f"no key available for key {digest.key_ref.key_id!r}: the key resolver failed (the "
            "cause is chained, never quoted in this message)"
        ) from exc
    if not isinstance(key, bytes):
        raise DigestKeyUnavailable(f"no key available for key {digest.key_ref.key_id!r}")
    return key


def verify_digest(
    obj: BaseModel | Mapping[str, Any],
    digest: ContentDigest,
    *,
    key_resolver: Callable[[KeyRef], bytes | None] | None = None,
) -> None:
    """Check ``digest`` against ``obj``: returns ``None`` on a match and raises otherwise.

    The scheme and, for a keyed scheme, the key are settled before any content is hashed:
    ``UnknownDigestScheme`` for an unregistered scheme (also on a model built with
    ``model_construct``), ``DigestKeyUnavailable`` when a keyed digest has no resolver or the
    resolver fails or returns no key (the resolver is asked once, only for keyed digests),
    then ``NonIJsonValueError`` for content outside I-JSON and ``DigestMismatch`` for a
    mismatch. The comparison is constant-time.
    """
    if digest.scheme not in DIGEST_SCHEMES:
        _unknown_scheme(digest.scheme, tuple(DIGEST_SCHEMES))
    key = _resolve_key(digest, key_resolver) if DIGEST_SCHEMES[digest.scheme].keyed else None
    actual = _hex_digest(digest.scheme, canonical_bytes(obj), key)
    if not hmac.compare_digest(actual.encode(), digest.value.encode()):
        identity = obj.get("id") if isinstance(obj, Mapping) else getattr(obj, "id", None)
        subject = f" of {identity}" if identity is not None else ""
        raise DigestMismatch(f"content digest{subject} does not match ({digest.scheme})")
