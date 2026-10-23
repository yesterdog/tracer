# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 5 tests (P1-26 SealedPayload, KeyRef, RedactionRecord). No tests
here: the self-tests of the reference implementation are in test_sealed_payload.py.

The classes are found through the registry walk (``registered_class``), so a class the architect
has not written yet fails the test that needs it and never breaks collection. The pinned names,
fields and readings are in docs/phase1/api-surface-sealing.md.

This module is also the **test-only reference seal/unseal implementation** of ADR 0015
Decision 8 (``cryptography`` is a test-only dependency; nothing here is shipped):

- ``seal()`` builds a JSON-mode ``SealedPayload`` document: a fresh 256-bit DEK per payload,
  AES-256-GCM over the plaintext with ``AAD = JCS(aad_fields)`` (members whose value is null are
  absent, like everywhere in the digest domain), the DEK wrapped with AES Key Wrap (RFC 3394)
  under the KEK, binary fields as base64url without padding.
- ``unseal()`` validates the document with the model, unwraps the DEK, decrypts and
  authenticates, and only then checks ``plaintext_digest`` through ``digest.verify_digest()``.
  Any authentication failure is an ``UnsealError`` whose text never holds key or plaintext bytes.
  A digest mismatch is the package's ``DigestMismatch``. A keyed digest whose key the resolver
  does not have is not an error: the payload is authenticated by GCM and the result says
  ``digest_verified=False`` (ADR 0015 Consequences); it is never reported as verified.

The plaintext is ``bytes``. When ``plaintext_digest`` is present, the plaintext must be the UTF-8
text of a JSON object and the digest is over that object (api-surface-sealing.md, N77).
"""

from __future__ import annotations

import base64
import copy
import json
import os
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

import rfc8785
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import (
    InvalidUnwrap,
    aes_key_unwrap,
    aes_key_wrap,
)

from ._support import CURRENT_SCHEMA_VERSION, api
from ._support_b2 import new_id, registered_class

__all__ = [
    "DEK_BYTES",
    "DIGEST_KEY",
    "DIGEST_KEY_REF",
    "KEK",
    "KEK_REF",
    "KEYED",
    "NONCE_BYTES",
    "PAYLOAD_TYPES",
    "PLAIN",
    "SUBJECT_PREFIX",
    "TAG_BYTES",
    "VERSIONED_PAYLOAD_TYPES",
    "WRAPPED_DEK_BYTES",
    "UnsealError",
    "Unsealed",
    "aad_bytes_of",
    "aad_class",
    "aad_doc",
    "b64u",
    "dek_of",
    "dek_wrap_class",
    "dek_wrap_doc",
    "flip_b64u_byte",
    "payload_doc",
    "payload_for",
    "plain_digest_doc",
    "redaction_class",
    "redaction_doc",
    "replace_at",
    "resolver_for",
    "seal",
    "sealed_class",
    "subject_id_for",
    "unb64u",
    "unseal",
]

NONCE_BYTES = 12
DEK_BYTES = 32
WRAPPED_DEK_BYTES = 40  # RFC 3394: an n-byte key wraps to n + 8 bytes
TAG_BYTES = 16

# api-surface-sealing.md 1.1: the closed set of payload types and the kind of subject each seals.
PAYLOAD_TYPES: tuple[str, ...] = (
    "test_profile",
    "audio",
    "transcript_text",
    "tool_arguments",
    "tool_result",
)
SUBJECT_PREFIX: dict[str, str] = {
    "test_profile": "tpf",
    "audio": "art",
    "transcript_text": "tsc",
    "tool_arguments": "tcl",
    "tool_result": "tcl",
}
VERSIONED_PAYLOAD_TYPES = frozenset({"test_profile"})

# Fixed test keys. They are test data, not secrets, and appear in no fixture of the package.
KEK = bytes(range(32))
KEK_REF: dict[str, Any] = {"key_id": "kek-test-1", "key_version": "1"}
DIGEST_KEY = b"tracer-test-key-0000000000000000"
DIGEST_KEY_REF: dict[str, Any] = {"key_id": "hmac-test-1"}

KEYED = "hmac-sha256-jcs/v1"
PLAIN = "sha256-jcs/v1"


# ------------------------------------------------------------------------------ classes


def sealed_class() -> Any:
    return registered_class("SealedPayload")


def aad_class() -> Any:
    """The model behind ``SealedPayload.aad_fields`` (one nested model; its name is not pinned)."""
    annotation = sealed_class().model_fields["aad_fields"].annotation
    assert isinstance(annotation, type), annotation
    assert hasattr(annotation, "model_fields"), annotation
    return annotation


def dek_wrap_class() -> Any:
    annotation = sealed_class().model_fields["dek_wrap"].annotation
    assert isinstance(annotation, type), annotation
    assert hasattr(annotation, "model_fields"), annotation
    return annotation


def redaction_class() -> Any:
    return registered_class("RedactionRecord")


# ----------------------------------------------------------------------------- encoding


def b64u(data: bytes) -> str:
    """base64url without padding (the canonical form of ADR 0015 Decision 5)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    """Strict decoder for the canonical form: no padding, no white space, no other alphabet, and
    the unused trailing bits are zero (re-encoding gives the same text)."""
    if not text or any(ch not in _B64U_ALPHABET for ch in text):
        raise ValueError("not base64url")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64u(data) != text:
        raise ValueError("non-canonical base64url")
    return data


_B64U_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def flip_b64u_byte(text: str, index: int, mask: int = 0x01) -> str:
    """The base64url text of the same bytes with ``mask`` XORed into byte ``index``."""
    data = bytearray(unb64u(text))
    data[index] ^= mask
    return b64u(bytes(data))


# ------------------------------------------------------------------------------ documents


def subject_id_for(payload_type: str) -> str:
    return new_id(SUBJECT_PREFIX[payload_type])


def aad_doc(payload_type: str = "test_profile", /, **over: Any) -> dict[str, Any]:
    """A valid ``aad_fields`` document for ``payload_type`` (``subject_version`` only where the
    subject kind is versioned)."""
    doc: dict[str, Any] = {
        "payload_type": payload_type,
        "subject_id": subject_id_for(payload_type),
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    if payload_type in VERSIONED_PAYLOAD_TYPES:
        doc["subject_version"] = 1
    doc.update(over)
    return doc


def dek_wrap_doc(**over: Any) -> dict[str, Any]:
    """A structurally valid ``dek_wrap`` with random bytes (no cryptography behind it)."""
    doc: dict[str, Any] = {
        "alg": "A256KW",
        "kek_ref": dict(KEK_REF),
        "wrapped_dek": b64u(os.urandom(WRAPPED_DEK_BYTES)),
    }
    doc.update(over)
    return doc


def payload_doc(payload_type: str = "test_profile", /, **over: Any) -> dict[str, Any]:
    """A structurally valid ``SealedPayload`` document built from random bytes. It passes the
    schema and cannot be unsealed. Use ``seal()`` for a real one."""
    doc: dict[str, Any] = {
        "format": "tracer.sealed.v1",
        "alg": "A256GCM",
        "dek_wrap": dek_wrap_doc(),
        "nonce": b64u(os.urandom(NONCE_BYTES)),
        "ciphertext": b64u(os.urandom(64 + TAG_BYTES)),
        "aad_fields": aad_doc(payload_type),
    }
    doc.update(over)
    return doc


def plain_digest_doc(subject: Mapping[str, Any]) -> dict[str, Any]:
    """A plain ``sha256-jcs/v1`` digest document of ``subject`` (the form P-42 forbids)."""
    digest = api("digest").compute_digest(subject)
    dumped: dict[str, Any] = digest.model_dump(mode="json", exclude_none=True)
    return dumped


def redaction_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "method": "ner_mask",
        "tool_ref": "redactor 1.4.2",
        "performed_at": "2026-10-22T10:00:00.000000Z",
        "covers": ["audio", "text", "tool_arguments"],
    }
    doc.update(over)
    return doc


# --------------------------------------------------------------------- reference crypto


class UnsealError(Exception):
    """Authentication or unwrapping failed. The text never holds key, DEK or plaintext bytes."""


class Unsealed(NamedTuple):
    plaintext: bytes
    digest_verified: bool


def aad_bytes_of(aad_fields: Mapping[str, Any]) -> bytes:
    """``JCS(aad_fields)`` with null members removed (api-surface-sealing.md N78)."""
    present = {key: value for key, value in aad_fields.items() if value is not None}
    return rfc8785.dumps(present)


def seal(
    plaintext: bytes,
    *,
    payload_type: str = "test_profile",
    subject_id: str | None = None,
    subject_version: int | None = None,
    schema_version: str = CURRENT_SCHEMA_VERSION,
    kek: bytes = KEK,
    kek_ref: Mapping[str, Any] | None = None,
    digest_key: bytes | None = None,
    digest_key_ref: Mapping[str, Any] | None = None,
    digest_subject: Mapping[str, Any] | None = None,
    dek: bytes | None = None,
    nonce: bytes | None = None,
) -> dict[str, Any]:
    """The JSON-mode document of a ``SealedPayload``.

    ``dek`` and ``nonce`` are for golden vectors only; by default each call draws a fresh 256-bit
    DEK and a fresh 12-byte nonce. ``subject_version`` defaults to 1 for ``test_profile`` and to
    absent for the other types. With ``digest_key`` the keyed ``plaintext_digest`` is over
    ``digest_subject`` or, by default, over the JSON object that is the plaintext.
    """
    dek = os.urandom(DEK_BYTES) if dek is None else dek
    nonce = os.urandom(NONCE_BYTES) if nonce is None else nonce
    if subject_version is None and payload_type in VERSIONED_PAYLOAD_TYPES:
        subject_version = 1
    aad: dict[str, Any] = {
        "payload_type": payload_type,
        "subject_id": subject_id if subject_id is not None else subject_id_for(payload_type),
        "schema_version": schema_version,
    }
    if subject_version is not None:
        aad["subject_version"] = subject_version
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad_bytes_of(aad))
    doc: dict[str, Any] = {
        "format": "tracer.sealed.v1",
        "alg": "A256GCM",
        "dek_wrap": {
            "alg": "A256KW",
            "kek_ref": dict(KEK_REF if kek_ref is None else kek_ref),
            "wrapped_dek": b64u(aes_key_wrap(kek, dek)),
        },
        "nonce": b64u(nonce),
        "ciphertext": b64u(ciphertext),
        "aad_fields": aad,
    }
    if digest_key is not None:
        subject = digest_subject if digest_subject is not None else json.loads(plaintext)
        if not isinstance(subject, Mapping):
            raise ValueError("a plaintext digest needs a JSON object plaintext")
        ref = api("digest").KeyRef.model_validate(dict(digest_key_ref or DIGEST_KEY_REF))
        digest = api("digest").compute_digest(subject, scheme=KEYED, key=digest_key, key_ref=ref)
        doc["plaintext_digest"] = digest.model_dump(mode="json", exclude_none=True)
    return doc


def resolver_for(keys: Mapping[str, bytes]) -> Callable[[Any], bytes | None]:
    """A key resolver: ``KeyRef.key_id`` -> key bytes, or None when the key is not held."""

    def resolve(key_ref: Any) -> bytes | None:
        return keys.get(key_ref.key_id)

    return resolve


def _as_document(sealed: Any) -> dict[str, Any]:
    if isinstance(sealed, Mapping):
        return copy.deepcopy(dict(sealed))
    dumped: dict[str, Any] = sealed.model_dump(mode="json")
    return dumped


def dek_of(sealed: Any, kek: bytes = KEK) -> bytes:
    """The unwrapped DEK of ``sealed`` (document or model). For tests that compare DEKs."""
    wrapped = unb64u(_as_document(sealed)["dek_wrap"]["wrapped_dek"])
    try:
        return aes_key_unwrap(kek, wrapped)
    except InvalidUnwrap:
        raise UnsealError("the data key could not be unwrapped") from None


def unseal(
    sealed: Any,
    *,
    kek: bytes = KEK,
    key_resolver: Callable[[Any], bytes | None] | None = None,
) -> Unsealed:
    """Validate, unwrap, decrypt and authenticate, then check the digest (if any).

    A malformed document is the model's ``ValidationError``; a wrong KEK, a damaged wrapped DEK,
    nonce or ciphertext, or another AAD is an ``UnsealError``; a digest that does not match the
    decrypted plaintext is ``DigestMismatch``.
    """
    document = _as_document(sealed)
    model = sealed_class().model_validate(document)
    doc: dict[str, Any] = model.model_dump(mode="json")
    try:
        dek = aes_key_unwrap(kek, unb64u(doc["dek_wrap"]["wrapped_dek"]))
    except InvalidUnwrap:
        raise UnsealError("the data key could not be unwrapped") from None
    try:
        plaintext = AESGCM(dek).decrypt(
            unb64u(doc["nonce"]), unb64u(doc["ciphertext"]), aad_bytes_of(doc["aad_fields"])
        )
    except InvalidTag:
        raise UnsealError("authentication failed") from None
    digest = model.plaintext_digest
    if digest is None:
        return Unsealed(plaintext, False)
    subject = json.loads(plaintext)
    if not isinstance(subject, Mapping):
        raise UnsealError("a plaintext digest needs a JSON object plaintext")
    errors = api("errors")
    try:
        api("digest").verify_digest(subject, digest, key_resolver=key_resolver)
    except errors.DigestKeyUnavailable:
        return Unsealed(plaintext, False)
    return Unsealed(plaintext, True)


# --------------------------------------------------------------------------- doc editing


def replace_at(doc: Mapping[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """A deep copy of ``doc`` with the member at ``path`` set to ``value`` (a ``Ellipsis`` value
    removes the member)."""
    out = copy.deepcopy(dict(doc))
    node: Any = out
    for part in path[:-1]:
        node = node[part]
    if value is Ellipsis:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value
    return out


def payload_for(payload_type: str, plaintext: bytes | None = None, **kwargs: Any) -> dict[str, Any]:
    """A really sealed document for ``payload_type`` with a plaintext that suits the type."""
    if plaintext is None:
        plaintext = (
            json.dumps({"k": "v"}).encode("utf-8")
            if payload_type != "audio"
            else bytes(range(256)) * 4
        )
    return seal(plaintext, payload_type=payload_type, **kwargs)
