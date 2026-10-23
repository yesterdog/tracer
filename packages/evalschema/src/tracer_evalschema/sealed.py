# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Sealed payloads: encrypted personal data for explicit portable exports (ADR 0015; P1-26).

The schema only: this module holds no cryptography and no key material. Sealing and unsealing
(AES-256-GCM with a per-payload data key wrapped by AES Key Wrap, RFC 3394) belong to the key
manager integrations of a later phase; the reference implementation used by the tests is
test-only (ADR 0015 Decision 8).

- ``SealedPayload{format, alg, dek_wrap, nonce, ciphertext, aad_fields, plaintext_digest?}``, a
  value object that hides its input (finding S-N88). ``format`` is the closed set
  ``{"tracer.sealed.v1"}`` and ``alg`` ``{"A256GCM"}``; a reader rejects any other value.
- ``DekWrap{alg, kek_ref, wrapped_dek}``: the data key (DEK), wrapped under the key-encryption key
  (KEK) that ``kek_ref`` names. ``alg`` is ``{"A256KW"}``.
- ``SealedAadFields{payload_type, subject_id, subject_version?, schema_version}``: the additional
  authenticated data. ``payload_type`` is one of ``PAYLOAD_TYPES`` and ``subject_id`` is a typed ID
  of the kind that type seals (``PAYLOAD_SUBJECT_TYPES``, S-N80); ``subject_version`` is present
  if and only if the subject kind is versioned (a test profile, S-N84); ``schema_version`` is the
  schema version of the *plaintext*, checked for its form only (S-N79).
- ``aad_bytes(aad_fields)``: the AAD, ``JCS(aad_fields)`` with null members removed (S-N78), the
  one definition for every implementation.

Encoding (ADR 0015 Decision 5): every binary field is base64url without padding in canonical
form (alphabet ``A-Z a-z 0-9 - _``, no ``=``, no white space, a length that is not 1 modulo 4,
unused trailing bits zero). ``nonce`` is exactly 12 bytes, ``wrapped_dek`` exactly 40 bytes (a
wrapped 256-bit key, S-N81) and ``ciphertext`` at least 16 bytes: the 16-byte GCM tag is appended
to the encrypted bytes (S-N82).

The digest rule (ADR 0015 Decision 6, P-42, A-32): ``plaintext_digest`` is absent or a keyed
digest (``hmac-sha256-jcs/v1`` with a ``key_ref``) for every payload type; a plain digest is a
validation error at ``plaintext_digest``, and the digest key must not be the KEK (S-N85). A
present digest is over the plaintext read as a JSON object (S-N77), so audio is sealed without
one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Any, Final

from pydantic import (
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    Strict,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._constraints import ClosedSet, FullMatch
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.digest import DIGEST_SCHEMES, ContentDigest, KeyRef, jcs_bytes
from tracer_evalschema.errors import UnknownDigestScheme
from tracer_evalschema.ids import (
    ArtifactId,
    TestProfileId,
    ToolCallEventId,
    TranscriptId,
    TypedId,
    wire_pattern,
)

__all__ = [
    "AEAD_ALGORITHMS",
    "CIPHERTEXT_MIN_BYTES",
    "KEY_WRAP_ALGORITHMS",
    "NONCE_BYTES",
    "PAYLOAD_SUBJECT_TYPES",
    "PAYLOAD_TYPES",
    "SEALED_FORMATS",
    "SUBJECT_ID_PATTERN",
    "TAG_BYTES",
    "VERSIONED_PAYLOAD_TYPES",
    "WRAPPED_DEK_BYTES",
    "DekWrap",
    "SealedAadFields",
    "SealedPayload",
    "aad_bytes",
]

SEALED_FORMATS: Final = ("tracer.sealed.v1",)
AEAD_ALGORITHMS: Final = ("A256GCM",)
KEY_WRAP_ALGORITHMS: Final = ("A256KW",)

NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16
CIPHERTEXT_MIN_BYTES: Final = TAG_BYTES
WRAPPED_DEK_BYTES: Final = 40  # RFC 3394: a wrapped n-byte key is n + 8 bytes (n = 32)

PAYLOAD_SUBJECT_TYPES: Final[Mapping[str, type[TypedId]]] = MappingProxyType(
    {
        "test_profile": TestProfileId,
        "audio": ArtifactId,
        "transcript_text": TranscriptId,
        "tool_arguments": ToolCallEventId,
        "tool_result": ToolCallEventId,
    }
)
"""Payload type -> the ID class of the subject it seals (S-N80). Adding a type is an additive
change with an ADR 0015 amendment."""

PAYLOAD_TYPES: Final = tuple(PAYLOAD_SUBJECT_TYPES)
VERSIONED_PAYLOAD_TYPES: Final = ("test_profile",)
"""Payload types whose subject kind is a versioned entity: ``subject_version`` is required for
these and absent for the others (S-N84)."""

_SUBJECT_KINDS: Final = tuple(dict.fromkeys(PAYLOAD_SUBJECT_TYPES.values()))
SUBJECT_ID_PATTERN: Final = wire_pattern(
    "(?:" + "|".join(kind.prefix for kind in _SUBJECT_KINDS) + ")"
)


# ------------------------------------------------------------------------------- base64url

_ALPHABET: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_INDEX: Final = MappingProxyType({char: index for index, char in enumerate(_ALPHABET)})
_B64U_CHARS = re.compile("[A-Za-z0-9_-]*")
_SPARE_BITS: Final = MappingProxyType({2: 4, 3: 2})  # length mod 4 -> unused trailing bits
_ANY: Final = "[A-Za-z0-9_-]"
# last characters whose unused trailing bits are zero: 4 spare bits -> index % 16 == 0, 2 spare
# bits -> index % 4 == 0
_LAST_OF: Final = MappingProxyType(
    {
        spare: "".join(char for index, char in enumerate(_ALPHABET) if index % (1 << spare) == 0)
        for spare in (4, 2)
    }
)
_CANONICAL_PATTERN: Final = (
    f"^(?:{_ANY}{{4}})*(?:{_ANY}[{_LAST_OF[4]}]|{_ANY}{{2}}[{_LAST_OF[2]}])?$"
)


def _encoded_length(n_bytes: int) -> int:
    return (4 * n_bytes + 2) // 3


def _exact_pattern(n_bytes: int) -> str:
    length = _encoded_length(n_bytes)
    spare = _SPARE_BITS.get(length % 4)
    if spare is None:
        return f"^{_ANY}{{{length}}}$"
    return f"^{_ANY}{{{length - 1}}}[{_LAST_OF[spare]}]$"


def _is_canonical(text: str) -> bool:
    if _B64U_CHARS.fullmatch(text) is None or len(text) % 4 == 1:
        return False
    spare = _SPARE_BITS.get(len(text) % 4)
    return spare is None or _INDEX[text[-1]] % (1 << spare) == 0


@dataclass(frozen=True)
class Base64Url:
    """Canonical base64url without padding (ADR 0015 Decision 5), of exactly ``exact_bytes`` or
    at least ``min_bytes`` decoded bytes.

    The JSON Schema states the canonical form as a pattern (alphabet, no padding, no white space,
    possible lengths, zero trailing bits); a minimum byte count is a ``minLength``. Messages name
    the rule only and never the value (these fields live in models that hide their input).
    """

    exact_bytes: int | None = None
    min_bytes: int | None = None
    _pattern: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.exact_bytes is None) == (self.min_bytes is None):
            raise ValueError("Base64Url needs exactly one of exact_bytes and min_bytes")
        pattern = (
            _exact_pattern(self.exact_bytes) if self.exact_bytes is not None else _CANONICAL_PATTERN
        )
        object.__setattr__(self, "_pattern", pattern)

    def _check(self, value: str) -> str:
        if not _is_canonical(value):
            raise PydanticCustomError(
                "base64url",
                "not canonical base64url without padding (alphabet A-Z a-z 0-9 - _, no '=', no "
                "white space, unused trailing bits zero)",
            )
        n_bytes = len(value) * 3 // 4
        if self.exact_bytes is not None and n_bytes != self.exact_bytes:
            raise PydanticCustomError(
                "byte_length",
                "must encode exactly {expected} bytes",
                {"expected": self.exact_bytes},
            )
        if self.min_bytes is not None and n_bytes < self.min_bytes:
            raise PydanticCustomError(
                "byte_length",
                "must encode at least {expected} bytes",
                {"expected": self.min_bytes},
            )
        return value

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            self._check, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema: JsonSchemaValue = {"type": "string", "pattern": self._pattern}
        if self.min_bytes is not None:
            json_schema["minLength"] = _encoded_length(self.min_bytes)
        return json_schema


Nonce = Annotated[str, Base64Url(exact_bytes=NONCE_BYTES)]
WrappedDek = Annotated[str, Base64Url(exact_bytes=WRAPPED_DEK_BYTES)]
Ciphertext = Annotated[str, Base64Url(min_bytes=CIPHERTEXT_MIN_BYTES)]


# ------------------------------------------------------------------------------ subject IDs


def _subject_id(value: Any) -> TypedId:
    if isinstance(value, TypedId) and type(value) in _SUBJECT_KINDS:
        return value
    if type(value) is str:
        kind = next((k for k in _SUBJECT_KINDS if value.startswith(k.prefix + "_")), None)
        if kind is not None:
            try:
                return kind.parse(value)
            except ValueError:
                pass
    # always value-free: this field lives in a model that hides its input (S-N88)
    raise PydanticCustomError(
        "subject_id",
        "not the ID of a sealable subject (a test profile, artifact, transcript or tool call; "
        "expected {pattern})",
        {"pattern": SUBJECT_ID_PATTERN},
    )


class _SubjectIdRule:
    """One validator for the union of the sealable subject ID kinds (one error, not four)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _subject_id,
            serialization=core_schema.plain_serializer_function_ser_schema(str, when_used="json"),
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": SUBJECT_ID_PATTERN}


SealedSubjectId = Annotated[
    TestProfileId | ArtifactId | TranscriptId | ToolCallEventId, _SubjectIdRule()
]
"""The typed ID of a sealed subject; which kind is allowed depends on ``payload_type``."""

_PLAINTEXT_SCHEMA_VERSION = FullMatch(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
    "schema_version_form",
    "{value} is not a schema version MAJOR.MINOR.PATCH (no leading zeros, no suffix)",
)


# ---------------------------------------------------------------------------------- models


def _aad_conditions(schema: dict[str, Any]) -> None:
    """Per payload type: the subject ID kind, and whether ``subject_version`` is required (an
    integer) or absent (``null``)."""
    rules: list[dict[str, Any]] = []
    for payload_type, kind in PAYLOAD_SUBJECT_TYPES.items():
        then: dict[str, Any] = {
            "properties": {"subject_id": {"pattern": wire_pattern(kind.prefix)}},
        }
        if payload_type in VERSIONED_PAYLOAD_TYPES:
            then["properties"]["subject_version"] = {"type": "integer"}
            then["required"] = ["subject_version"]
        else:
            then["properties"]["subject_version"] = {"type": "null"}
        rules.append(
            {
                "if": {
                    "properties": {"payload_type": {"enum": [payload_type]}},
                    "required": ["payload_type"],
                },
                "then": then,
            }
        )
    schema["allOf"] = rules


class SealedAadFields(HidingModel):
    """The additional authenticated data of a sealed payload (ADR 0015 Decision 4).

    Decryption under any other ``payload_type``, ``subject_id``, ``subject_version`` or
    ``schema_version`` fails authentication. ``schema_version`` is the plaintext's version and is
    not compared with the package's version (S-N79): a migrated bundle does not rewrite it.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, json_schema_extra=_aad_conditions
    )

    payload_type: Annotated[str, ClosedSet(PAYLOAD_TYPES)]
    subject_id: SealedSubjectId
    subject_version: Annotated[int, Strict(), Field(ge=1)] | None = None
    schema_version: Annotated[str, _PLAINTEXT_SCHEMA_VERSION]

    @model_validator(mode="after")
    def _subject_matches_payload_type(self) -> SealedAadFields:
        problems: list[Problem] = []
        kind = PAYLOAD_SUBJECT_TYPES[self.payload_type]
        entity = kind.__name__.removesuffix("Id")
        if type(self.subject_id) is not kind:
            problems.append(
                Problem(
                    ("subject_id",),
                    "subject_kind",
                    f"this payload_type seals a {entity}: subject_id needs the prefix "
                    f"{kind.prefix}_",
                )
            )
        if self.payload_type in VERSIONED_PAYLOAD_TYPES:
            if self.subject_version is None:
                problems.append(
                    Problem(
                        ("subject_version",),
                        "subject_version",
                        f"a {entity} is versioned: subject_version is required (an integer >= 1)",
                    )
                )
        elif self.subject_version is not None:
            problems.append(
                Problem(
                    ("subject_version",),
                    "subject_version",
                    f"a {entity} is not versioned: subject_version must be absent",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self


class DekWrap(HidingModel):
    """The payload's data key, wrapped with AES Key Wrap (RFC 3394) under the key-encryption key
    that ``kek_ref`` names (ADR 0015 Decision 2). ``kek_ref`` names a key; it never holds one."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    alg: Annotated[str, ClosedSet(KEY_WRAP_ALGORITHMS)]
    kek_ref: KeyRef
    wrapped_dek: WrappedDek


_KEYED_SCHEMES: Final = tuple(name for name, scheme in DIGEST_SCHEMES.items() if scheme.keyed)


def _payload_conditions(schema: dict[str, Any]) -> None:
    """``plaintext_digest`` is absent, ``null`` or of a keyed scheme (P-42). Key separation from
    ``dek_wrap.kek_ref`` compares two objects and is a code rule only."""
    schema["allOf"] = [
        {
            "properties": {
                "plaintext_digest": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {"scheme": {"enum": list(_KEYED_SCHEMES)}},
                            "required": ["scheme", "key_ref"],
                        },
                    ]
                }
            }
        }
    ]


class SealedPayload(HidingModel):
    """An encrypted payload bound to its subject (ADR 0015 Decision 1). Only an ``ExportBundle``
    may carry one (Decision 7, P1-14); snapshots, runs and internal bundles never do."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        json_schema_extra=_payload_conditions,
    )

    format: Annotated[str, ClosedSet(SEALED_FORMATS)]
    alg: Annotated[str, ClosedSet(AEAD_ALGORITHMS)]
    dek_wrap: DekWrap
    nonce: Nonce
    ciphertext: Ciphertext
    aad_fields: SealedAadFields
    plaintext_digest: ContentDigest | None = None

    @model_validator(mode="after")
    def _plaintext_digest_absent_or_keyed(self) -> SealedPayload:
        digest = self.plaintext_digest
        if digest is None:
            return self
        scheme = DIGEST_SCHEMES.get(digest.scheme)
        if scheme is None:  # only reachable through model_construct
            raise UnknownDigestScheme(
                f"unknown digest scheme (registered: {', '.join(DIGEST_SCHEMES)})"
            )
        problems: list[Problem] = []
        if not scheme.keyed:
            problems.append(
                Problem(
                    ("plaintext_digest",),
                    "plaintext_digest_keyed",
                    "a sealed payload's plaintext_digest is absent or keyed (with a key_ref): a "
                    "plain digest lets anyone holding the export confirm guesses of the sealed "
                    "content (ADR 0015 Decision 6, P-42)",
                )
            )
        elif digest.key_ref is not None and _same_key(digest.key_ref, self.dek_wrap.kek_ref):
            problems.append(
                Problem(
                    ("plaintext_digest", "key_ref"),
                    "key_separation",
                    "the digest key must not be the key-encryption key named by dek_wrap.kek_ref "
                    "(same key_id and key_version)",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self


def _same_key(left: KeyRef, right: KeyRef) -> bool:
    return left.key_id == right.key_id and left.key_version == right.key_version


# ---------------------------------------------------------------------------------- the AAD


def aad_bytes(aad_fields: SealedAadFields | Mapping[str, Any]) -> bytes:
    """The additional authenticated data of a sealed payload: the RFC 8785 (JCS) bytes of the
    JSON-mode ``aad_fields`` with every ``null`` member removed (ADR 0015 Decision 4, S-N78).

    An absent ``subject_version`` and an explicit ``null`` give the same bytes; member order in
    the source document does not matter. A mapping is validated as ``SealedAadFields`` first
    (``ValidationError`` if it is not valid). Pure: no key and no cryptography.
    """
    model = (
        aad_fields
        if isinstance(aad_fields, SealedAadFields)
        else SealedAadFields.model_validate(aad_fields)
    )
    return jcs_bytes(model.model_dump(mode="json", exclude_none=True))
