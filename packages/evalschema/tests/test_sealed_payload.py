# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-26: SealedPayload, KeyRef, RedactionRecord, and the drop-or-key digest rule for every payload
type (ADR 0015, ADR 0006 Decision 3, ADR 0007 Decisions 4 and 8, ADR 0016 Decision 7, P-42, P-18).

Pinned API and readings: docs/phase1/api-surface-sealing.md (the lead merges it into
api-surface.md). Names are SCOPE section 5 names unless the docstring says "Proposed name".
The reference seal/unseal is test-only and lives in ``_support_b5`` (``cryptography`` is a
test-only dependency, ADR 0015 Decision 8). The bundle and export half (an ExportBundle that
carries or refuses sealed payloads, the human-subject export rule) belongs to P1-14.

The golden vectors (G1 profile with a keyed digest, G2 audio without one) were computed outside
the package with ``cryptography``, ``rfc8785``, ``hmac`` and ``hashlib`` from hand-written
canonical strings. The AAD strings below are hand-written JCS; the RFC 3394 vector is the
published one (section 4.6).
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap
from pydantic import ValidationError

from ._support import CURRENT_SCHEMA_VERSION, api
from ._support_b2 import (
    SECRET_DENYLIST,
    contains_key,
    embedded_models,
    new_id,
    property_enum,
    registered_class,
    schema_accepts,
    schema_of,
    secret_like,
    walk_nodes,
)
from ._support_b3 import QUOTE_MARK, assert_input_hidden, marked_id, marked_set
from ._support_b5 import (
    DIGEST_KEY,
    DIGEST_KEY_REF,
    KEK,
    KEK_REF,
    NONCE_BYTES,
    PAYLOAD_TYPES,
    SUBJECT_PREFIX,
    TAG_BYTES,
    VERSIONED_PAYLOAD_TYPES,
    WRAPPED_DEK_BYTES,
    UnsealError,
    aad_bytes_of,
    aad_class,
    aad_doc,
    b64u,
    dek_of,
    dek_wrap_class,
    dek_wrap_doc,
    flip_b64u_byte,
    payload_doc,
    payload_for,
    plain_digest_doc,
    redaction_class,
    redaction_doc,
    replace_at,
    resolver_for,
    seal,
    sealed_class,
    subject_id_for,
    unb64u,
    unseal,
)

HEX64 = "0" * 64

# ------------------------------------------------------------------------- golden vectors

# G1: a test profile, subject version 1, keyed plaintext_digest (key DIGEST_KEY, key_id
# "hmac-test-1"). KEK = bytes 0..31, DEK = bytes 0x40..0x5f, nonce = bytes 0..11.
G1_SUBJECT = "tpf_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b"
G1_PLAINTEXT = (
    '{"fields":{"account":"12345678","dob":"1990-01-02"},'
    '"id":"tpf_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b","name":"\u0939\u093f","note":null,'
    '"schema_version":"0.0.1","version":1}'
).encode()
G1_AAD = (
    b'{"payload_type":"test_profile","schema_version":"0.0.1",'
    b'"subject_id":"tpf_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b","subject_version":1}'
)
G1_DOC: dict[str, Any] = {
    "format": "tracer.sealed.v1",
    "alg": "A256GCM",
    "dek_wrap": {
        "alg": "A256KW",
        "kek_ref": {"key_id": "kek-test-1", "key_version": "1"},
        "wrapped_dek": "vSonaujHRkx-izlmdKxuDpVYyExgCbP6QTzwameiAII-TXIN8kGfqQ",
    },
    "nonce": "AAECAwQFBgcICQoL",
    "ciphertext": (
        "QScKERLgINPgKPvg2Y6v1PaqWStWbpxh3RTdoHaJS3JiFKtjmXgpTokhSLJxmvrAGz13EfQtfwYJofPFNzp-1_9E"
        "mJ6XmRr4sQC9nyh3uwDU6IjAIYA2VNxCyHI4Gg03t-DHwyGWGAN6joDv55v0pBk1eCiDhOP4Dz8L7AQNjGNXbTd7"
        "XeidqZZQoR2zbHwxA675JbtMTkZxVyV8oRvVk4CHp9fsL3sVqFVTyrwpLNq0-mZCQQ"
    ),
    "aad_fields": {
        "payload_type": "test_profile",
        "subject_id": G1_SUBJECT,
        "subject_version": 1,
        "schema_version": "0.0.1",
    },
    "plaintext_digest": {
        "scheme": "hmac-sha256-jcs/v1",
        "value": "a801f747d033e40866fcf4f3006142ac6a968a6047189c9a13cd2d09fae776be",
        "key_ref": {"key_id": "hmac-test-1"},
    },
}
G1_DOMAIN = (
    '{"fields":{"account":"12345678","dob":"1990-01-02"},'
    '"id":"tpf_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b","name":"\u0939\u093f","version":1}'
)
G1_PLAIN_SHA256 = "3f44820e9de26cf54e2e1777c85ad9f5abc73b2c016a23246394ad962ebe43dd"
G1_DEK = bytes(range(0x40, 0x60))
G1_NONCE = bytes(range(12))

# G2: audio bytes, no subject_version (Artifact is not versioned), no plaintext_digest.
G2_SUBJECT = "art_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8c"
G2_PLAINTEXT = bytes([0, 1, 2, 3, 250, 251, 252, 253, 254, 255]) * 3
G2_AAD = (
    b'{"payload_type":"audio","schema_version":"0.0.1","subject_id":"' + G2_SUBJECT.encode() + b'"}'
)
G2_DEK = bytes(range(200, 232))
G2_NONCE = bytes(range(100, 112))
G2_DOC: dict[str, Any] = {
    "format": "tracer.sealed.v1",
    "alg": "A256GCM",
    "dek_wrap": {
        "alg": "A256KW",
        "kek_ref": {"key_id": "kek-test-1", "key_version": "1"},
        "wrapped_dek": "rh9cSQOqaw7RJuGS04aFjmIjLfEeuma_l34NT1rPpHVB8Kb_PLhJ7Q",
    },
    "nonce": "ZGVmZ2hpamtsbW5v",
    "ciphertext": "WM-jJYb4CCwibU7OBL0HeZBacynjTxqHeCDRnK2VH-frIvuPz7hSECLUPTE2IA",
    "aad_fields": {
        "payload_type": "audio",
        "subject_id": G2_SUBJECT,
        "schema_version": "0.0.1",
    },
}

# RFC 3394 section 4.6: 256-bit KEK, 256-bit key data.
RFC3394_KEK = bytes(range(32))
RFC3394_DATA = bytes.fromhex("00112233445566778899AABBCCDDEEFF000102030405060708090A0B0C0D0E0F")
RFC3394_WRAPPED = bytes.fromhex(
    "28C9F404C4B810F4CBCCB35CFB87F8263F5786E2D80ED326CBC7F0E71A99F43BFB988B9B7A02DD21"
)


def _validates(doc: dict[str, Any]) -> Any:
    return sealed_class().model_validate(doc)


def _rejected(doc: dict[str, Any], *fields: str) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        sealed_class().model_validate(doc)
    if fields:
        located = {str(part) for e in caught.value.errors() for part in e["loc"]}
        assert set(fields) & located, f"no error located at {fields}: {caught.value.errors()}"
    return caught.value


# =============================================================================== I1: shape


def test_sealed_shape() -> None:
    """ADR 0015 I1 / Decision 1: ``{format, alg, dek_wrap{alg, kek_ref, wrapped_dek}, nonce,
    ciphertext, aad_fields{payload_type, subject_id, subject_version, schema_version},
    plaintext_digest?}``. A value object (no entity fields, frozen, ``extra="forbid"`` at every
    level), ``kek_ref`` is a ``KeyRef``, ``plaintext_digest`` a ``ContentDigest``."""
    sealed, wrap, aad = sealed_class(), dek_wrap_class(), aad_class()
    digest = api("digest")
    base = api("base")
    for model in (sealed, wrap, aad):
        assert not issubclass(model, base.EntityBase), model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert model.model_config.get("frozen") is True, model.__name__
    assert set(sealed.model_fields) == {
        "format",
        "alg",
        "dek_wrap",
        "nonce",
        "ciphertext",
        "aad_fields",
        "plaintext_digest",
    }
    required = {n for n, f in sealed.model_fields.items() if f.is_required()}
    assert required == set(sealed.model_fields) - {"plaintext_digest"}
    assert set(wrap.model_fields) == {"alg", "kek_ref", "wrapped_dek"}
    assert all(f.is_required() for f in wrap.model_fields.values())
    assert set(aad.model_fields) == {
        "payload_type",
        "subject_id",
        "subject_version",
        "schema_version",
    }
    assert {n for n, f in aad.model_fields.items() if f.is_required()} == {
        "payload_type",
        "subject_id",
        "schema_version",
    }
    assert wrap.model_fields["kek_ref"].annotation is digest.KeyRef
    assert embedded_models(sealed) >= {wrap, aad, digest.KeyRef, digest.ContentDigest}
    # none of the three carries an entity field or a field of its own named like one
    for model in (sealed, wrap):
        assert not {"id", "created_at", "metadata", "version", "content_digest"} & set(
            model.model_fields
        )

    good = payload_doc()
    model = _validates(good)
    assert model.plaintext_digest is None
    with pytest.raises(ValidationError):
        model.nonce = "x"  # frozen
    _rejected({**good, "api_key": "x"}, "api_key")
    _rejected({**good, "dek_wrap": {**good["dek_wrap"], "key": "x"}}, "key")
    _rejected({**good, "aad_fields": {**good["aad_fields"], "owner": "x"}}, "owner")


def test_sealed_required_fields() -> None:
    """Every field but ``plaintext_digest`` is required, at its own location; ``null`` and an
    absent ``plaintext_digest`` are the same."""
    good = payload_doc()
    for name in ("format", "alg", "dek_wrap", "nonce", "ciphertext", "aad_fields"):
        _rejected({k: v for k, v in good.items() if k != name}, name)
    for name in ("alg", "kek_ref", "wrapped_dek"):
        _rejected(replace_at(good, ("dek_wrap", name), ...), name)
    for name in ("payload_type", "subject_id", "schema_version"):
        _rejected(replace_at(good, ("aad_fields", name), ...), name)
    absent = _validates(good)
    explicit_null = _validates({**good, "plaintext_digest": None})
    assert absent == explicit_null
    assert absent.plaintext_digest is None


# ========================================================================== I2: closed sets


FORMAT_VARIANTS = (
    "tracer.sealed.v2",
    "tracer.sealed.v0",
    "tracer.sealed",
    "Tracer.sealed.v1",
    "TRACER.SEALED.V1",
    " tracer.sealed.v1",
    "tracer.sealed.v1 ",
    "tracer.sealed.v1\n",
    "tracer.sealed.v1\u200b",
    "jwe",
    "",
    "none",
)
ALG_VARIANTS = (
    "A128GCM",
    "A192GCM",
    "a256gcm",
    "A256gcm",
    "A256GCM ",
    " A256GCM",
    "A256GCM\n",
    "A256CBC-HS512",
    "C20P",
    "XC20P",
    "AES-256-GCM",
    "dir",
    "none",
    "",
)
KW_VARIANTS = (
    "A128KW",
    "A192KW",
    "a256kw",
    "A256KW ",
    "A256GCMKW",
    "RSA-OAEP",
    "RSA-OAEP-256",
    "ECDH-ES+A256KW",
    "dir",
    "none",
    "",
)


@pytest.mark.parametrize("bad", FORMAT_VARIANTS)
def test_unknown_format_rejected(bad: str) -> None:
    """ADR 0015 I2 / Consequences: readers reject any other ``format`` string (a new algorithm
    set is a new format). Case and white-space variants are other strings."""
    _rejected(payload_doc(format=bad), "format")


@pytest.mark.parametrize("bad", ALG_VARIANTS)
def test_alg_closed(bad: str) -> None:
    """ADR 0015 I2: ``alg`` is exactly ``A256GCM``."""
    _rejected(payload_doc(alg=bad), "alg")


@pytest.mark.parametrize("bad", KW_VARIANTS)
def test_dek_wrap_alg_closed(bad: str) -> None:
    """Proposed name. ``dek_wrap.alg`` is exactly ``A256KW`` (RFC 3394)."""
    _rejected(payload_doc(dek_wrap=dek_wrap_doc(alg=bad)), "alg")


def test_non_string_closed_values_rejected() -> None:
    """A number, a list or null in a closed field is a validation error, not a coercion."""
    for field in ("format", "alg"):
        for bad in (1, 1.5, True, None, ["A256GCM"], {"v": 1}):
            _rejected(payload_doc(**{field: bad}), field)
    for bad in (1, None, ["A256KW"]):
        _rejected(payload_doc(dek_wrap=dek_wrap_doc(alg=bad)), "alg")


def test_sealed_closed_sets_emit_enum_not_const() -> None:
    """Closed sets of one member (``format``, ``alg``, ``dek_wrap.alg``) are an ``enum`` with the
    one member in the JSON Schema, never ``const`` (ADR 0003, api-surface section 19 conventions);
    ``payload_type`` is an ``enum`` of exactly the five types."""
    sealed = sealed_class()
    schema = schema_of(sealed)
    assert not contains_key(schema, "const")
    assert not contains_key(schema_of(sealed, "serialization"), "const")
    assert property_enum(sealed, "format") == ["tracer.sealed.v1"]
    assert property_enum(sealed, "alg") == ["A256GCM"]
    assert property_enum(dek_wrap_class(), "alg") == ["A256KW"]
    assert sorted(property_enum(aad_class(), "payload_type")) == sorted(PAYLOAD_TYPES)
    assert len(property_enum(aad_class(), "payload_type")) == len(PAYLOAD_TYPES)


def test_payload_types_exact() -> None:
    """Proposed name (N80). The closed payload-type set is exactly the five types of ADR 0015
    Decision 6 ("test profiles, audio, transcript text, tool arguments and results")."""
    aad = aad_class()
    for payload_type in PAYLOAD_TYPES:
        aad.model_validate(aad_doc(payload_type))
    for bad in (
        "profile",
        "Audio",
        "audio ",
        "text",
        "transcript",
        "tool_call",
        "tool_arguments_raw",
        "usage_raw",
        "",
        None,
        1,
    ):
        with pytest.raises(ValidationError):
            aad.model_validate({**aad_doc("audio"), "payload_type": bad})


# ================================================================= I3: nonce, wrapped DEK, tag


def _text_of_len(n_bytes: int) -> str:
    """Canonical base64url of ``n_bytes`` deterministic bytes."""
    return b64u(bytes((7 * i + 3) % 256 for i in range(n_bytes)))


@pytest.mark.parametrize("n_bytes", [0, 1, 8, 11, 13, 16, 24, 32])
def test_nonce_len_matches_alg(n_bytes: int) -> None:
    """ADR 0015 I3 / Decision 3: the nonce is exactly 12 bytes (16 base64url characters); the JSON
    Schema states the length too."""
    doc = payload_doc(nonce=_text_of_len(n_bytes))
    _rejected(doc, "nonce")
    assert not schema_accepts(sealed_class(), doc)
    good = payload_doc(nonce=_text_of_len(NONCE_BYTES))
    _validates(good)
    assert schema_accepts(sealed_class(), good)


@pytest.mark.parametrize("n_bytes", [0, 16, 24, 32, 39, 41, 48])
def test_wrapped_dek_len_matches_kw_alg(n_bytes: int) -> None:
    """Proposed name (N81). AES Key Wrap of a 256-bit key is 40 bytes (RFC 3394: n + 8)."""
    doc = payload_doc(dek_wrap=dek_wrap_doc(wrapped_dek=_text_of_len(n_bytes)))
    _rejected(doc, "wrapped_dek")
    assert not schema_accepts(sealed_class(), doc)
    good = payload_doc(dek_wrap=dek_wrap_doc(wrapped_dek=_text_of_len(WRAPPED_DEK_BYTES)))
    _validates(good)
    assert schema_accepts(sealed_class(), good)


@pytest.mark.parametrize("n_bytes", [0, 1, 8, 15])
def test_ciphertext_shorter_than_tag_rejected(n_bytes: int) -> None:
    """Proposed name (N82). The ciphertext carries the 16-byte GCM tag (appended), so fewer than
    16 bytes cannot be a ciphertext. Exactly 16 bytes is an empty plaintext and is valid."""
    _rejected(payload_doc(ciphertext=_text_of_len(n_bytes)), "ciphertext")
    for ok in (TAG_BYTES, TAG_BYTES + 1, TAG_BYTES + 2, TAG_BYTES + 3, 1000):
        _validates(payload_doc(ciphertext=_text_of_len(ok)))


# ================================================================== I4: base64url canonical


def _bad_encodings(good: str) -> list[tuple[str, str]]:
    """Variants of canonical ``good`` that are not canonical base64url without padding."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    mid = len(good) // 2
    out = [
        ("plus", "+" + good[1:]),
        ("slash", "/" + good[1:]),
        ("equals inside", good[:mid] + "=" + good[mid + 1 :]),
        ("padding appended", good + "="),
        ("double padding", good + "=="),
        ("newline", good + "\n"),
        ("leading space", " " + good),
        ("trailing space", good + " "),
        ("inner space", good[:mid] + " " + good[mid:]),
        ("inner newline", good[:mid] + "\n" + good[mid:]),
        ("tab", good[:mid] + "\t" + good[mid:]),
        ("nul", good[:mid] + "\x00" + good[mid + 1 :]),
        ("fullwidth letter", "\uff21" + good[1:]),
        ("combining mark", good[:mid] + "\u0301" + good[mid:]),
        ("dot", good[:mid] + "." + good[mid + 1 :]),
        ("bang", good[:mid] + "!" + good[mid + 1 :]),
        ("empty", ""),
    ]
    # 4k+1 characters cannot be the encoding of any byte string
    out.append(("impossible length", good + "A" * ((1 - len(good)) % 4)))
    spare = {2: 4, 3: 2}.get(len(good) % 4)
    if spare is not None:
        last = alphabet.index(good[-1])
        out.append(("trailing bits set", good[:-1] + alphabet[last + 1]))
        out.append(("trailing bits all ones", good[:-1] + alphabet[last | ((1 << spare) - 1)]))
    return out


def _encoding_cases() -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    for field, length in (("nonce", NONCE_BYTES), ("wrapped_dek", WRAPPED_DEK_BYTES)):
        for label, text in _bad_encodings(_text_of_len(length)):
            cases.append((field, label, text))
    for length in (TAG_BYTES, TAG_BYTES + 1, TAG_BYTES + 2, 100, 101):
        for label, text in _bad_encodings(_text_of_len(length)):
            cases.append(("ciphertext", f"{label} ({length} bytes)", text))
    return cases


@pytest.mark.parametrize(
    ("field", "label", "text"),
    [pytest.param(*c, id=f"{c[0]}-{c[1]}") for c in _encoding_cases()],
)
def test_base64url_canonical_no_padding(field: str, label: str, text: str) -> None:
    """ADR 0015 I4 / Decision 5: binary fields are base64url without padding in canonical form: no
    white space, no padding, no standard-alphabet characters, no other characters, and unused
    trailing bits are zero. Every variant is rejected at its field."""
    if field == "wrapped_dek":
        doc = payload_doc(dek_wrap=dek_wrap_doc(wrapped_dek=text))
    else:
        doc = payload_doc(**{field: text})
    _rejected(doc, field)


def test_base64url_alphabet_and_padding_rejected_by_json_schema() -> None:
    """The alphabet, the padding and the white-space rules are expressible in JSON Schema, so the
    schema states them. Not asserted here: the trailing-bit rule and the impossible length of a
    ciphertext (code-only), and a trailing newline (Python's ``re.search`` lets ``$`` match
    before it, which ECMA 262 does not; the code rule rejects it, see the test above)."""
    sealed = sealed_class()
    code_only = {"trailing bits set", "trailing bits all ones", "newline"}
    for field, length in (("nonce", NONCE_BYTES), ("ciphertext", 40)):
        good = _text_of_len(length)
        assert schema_accepts(sealed, payload_doc(**{field: good}))
        for label, text in _bad_encodings(good):
            if label in code_only or (field == "ciphertext" and label == "impossible length"):
                continue
            assert not schema_accepts(sealed, payload_doc(**{field: text})), (field, label)
    good = _text_of_len(WRAPPED_DEK_BYTES)
    for label, text in _bad_encodings(good):
        if label in code_only:
            continue
        doc = payload_doc(dek_wrap=dek_wrap_doc(wrapped_dek=text))
        assert not schema_accepts(sealed, doc), label


def test_canonical_encodings_of_every_length_residue_accepted() -> None:
    """Ciphertext lengths with residue 0, 1 and 2 modulo 3 (no, 2 and 3 spare-bit characters) are
    accepted in their canonical form, so the trailing-bit rule does not reject canonical text."""
    for n_bytes in range(TAG_BYTES, TAG_BYTES + 9):
        text = _text_of_len(n_bytes)
        assert "=" not in text
        model = _validates(payload_doc(ciphertext=text))
        assert model.ciphertext == text
        assert model.model_dump(mode="json")["ciphertext"] == text


# ====================================================================== I5: KeyRef, no material


def test_keyref_no_key_material() -> None:
    """ADR 0015 I5 / ADR 0007 Decision 4: ``KeyRef`` names a key and carries none. Its fields are
    a subset of ``{key_id, key_version, provider_hint}``; every credential-like or key-material
    field name is rejected as an unknown field, on a bare ``KeyRef`` and as ``kek_ref`` and a
    digest ``key_ref`` inside a sealed payload; no JSON Schema property of ``KeyRef`` or of
    ``SealedPayload`` has a credential name (ADR 0007 Decision 3)."""
    digest = api("digest")
    key_ref = digest.KeyRef
    assert {"key_id"} <= set(key_ref.model_fields) <= {"key_id", "key_version", "provider_hint"}
    forbidden = [
        "key",
        "key_material",
        "key_bytes",
        "secret_key",
        "kek",
        "dek",
        "material",
        "jwk",
        "pem",
        "raw_key",
        "value",
        "bytes",
        *("_".join(entry) for entry in SECRET_DENYLIST),
    ]
    for name in forbidden:
        with pytest.raises(ValidationError):
            key_ref.model_validate({"key_id": "k1", name: "x"})
        with pytest.raises(ValidationError):
            _validates(payload_doc(dek_wrap=dek_wrap_doc(kek_ref={"key_id": "k1", name: "x"})))
        keyed = {
            "scheme": "hmac-sha256-jcs/v1",
            "value": HEX64,
            "key_ref": {"key_id": "k1", name: "x"},
        }
        with pytest.raises(ValidationError):
            _validates(payload_doc(plaintext_digest=keyed))
    for model in (key_ref, sealed_class(), dek_wrap_class(), aad_class()):
        for node in walk_nodes(schema_of(model)):
            for prop in node.get("properties", {}):
                assert not secret_like(prop), (model.__name__, prop)
    # the stored document holds the reference and nothing else
    stored = _validates(seal(b'{"a":1}', payload_type="tool_arguments")).model_dump(mode="json")
    assert set(stored["dek_wrap"]["kek_ref"]) <= {"key_id", "key_version", "provider_hint"}


def test_keyref_names_keys_of_every_provider() -> None:
    """Proposed name. ``key_id`` is the key manager's own name for the key and is stored as given
    (ADR 0001 I12): cloud resource names, vault paths and plain names are all valid. (Whether a
    pasted credential in ``key_id`` is rejected is N83 and is not pinned.)"""
    key_ref = api("digest").KeyRef
    for ok in (
        "arn:aws:kms:ap-south-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
        "projects/p/locations/global/keyRings/r/cryptoKeys/k",
        "vault:transit/keys/tracer",
        "kek-test-1",
    ):
        assert key_ref.model_validate({"key_id": ok}).key_id == ok
        _validates(payload_doc(dek_wrap=dek_wrap_doc(kek_ref={"key_id": ok, "key_version": "3"})))


# ============================================================ the AAD fields (ADR 0015 Dec. 4)


@pytest.mark.parametrize("payload_type", PAYLOAD_TYPES)
def test_aad_subject_kind_matches_payload_type(payload_type: str) -> None:
    """ADR 0015 Decision 4 (``subject_id`` is a typed ID of the sealed subject's kind, matched
    against ``payload_type``; the union is fixed here, N80): test_profile -> TestProfile (tpf),
    audio -> Artifact (art), transcript_text -> Transcript (tsc), tool_arguments and tool_result
    -> ToolCallEvent (tcl). Another kind is rejected at ``subject_id``, also a kind that is a
    sealable subject for another type. The error does not name the id."""
    aad = aad_class()
    good = aad_doc(payload_type)
    assert str(aad.model_validate(good).subject_id) == good["subject_id"]
    for prefix in (
        "prj", "agt", "per", "tpf", "evl", "met", "mbd", "rub", "snap", "run", "art", "tsc",
        "turn", "exc", "tcl", "spn", "res", "vrd", "dts", "dti", "pcat", "use", "cst", "csum",
        "rbnd", "exp",
    ):  # fmt: skip
        doc = {**good, "subject_id": new_id(prefix)}
        if prefix == SUBJECT_PREFIX[payload_type]:
            aad.model_validate(doc)
        else:
            with pytest.raises(ValidationError) as caught:
                aad.model_validate(doc)
            assert any("subject_id" in [str(p) for p in e["loc"]] for e in caught.value.errors())
            assert doc["subject_id"] not in str(caught.value)


def test_aad_subject_id_must_be_a_typed_id() -> None:
    """A bare UUID, a v4 UUID, an upper-case id, a foreign string, an empty string and a number
    are not a ``subject_id`` (A-16, A-17, ADR 0001 I12: the field is a typed ID, not opaque)."""
    aad = aad_class()
    good = aad_doc("test_profile")
    typed = good["subject_id"]
    suffix = typed.split("_", 1)[1]
    for bad in (
        suffix,
        "tpf_" + suffix.upper(),
        "tpf_" + "12345678-1234-4234-8234-123456789abc",
        "TPF_" + suffix,
        "tpf-" + suffix,
        " " + typed,
        typed + " ",
        typed + "\n",
        "profile-1",
        "",
        None,
        1,
    ):
        with pytest.raises(ValidationError):
            aad.model_validate({**good, "subject_id": bad})


@pytest.mark.parametrize("payload_type", PAYLOAD_TYPES)
def test_aad_subject_version_present_iff_versioned(payload_type: str) -> None:
    """ADR 0015 Decision 4 and api-surface section 18 N3 (recommendation, N84): ``subject_version``
    is required (an integer >= 1) when the subject kind is versioned (a TestProfile) and absent
    for the others (Artifact, Transcript, ToolCallEvent are not versioned). A ``null`` is the same
    as absent."""
    aad = aad_class()
    good = aad_doc(payload_type)
    without = {k: v for k, v in good.items() if k != "subject_version"}
    with_version = {**without, "subject_version": 1}
    if payload_type in VERSIONED_PAYLOAD_TYPES:
        aad.model_validate(with_version)
        aad.model_validate({**with_version, "subject_version": 7})
        for bad_doc in (without, {**without, "subject_version": None}):
            with pytest.raises(ValidationError) as caught:
                aad.model_validate(bad_doc)
            assert any(
                "subject_version" in [str(p) for p in e["loc"]] for e in caught.value.errors()
            )
        for bad in (0, -1, 1.5, "1", True, [1]):
            with pytest.raises(ValidationError):
                aad.model_validate({**with_version, "subject_version": bad})
    else:
        aad.model_validate(without)
        assert aad.model_validate({**without, "subject_version": None}) == aad.model_validate(
            without
        )
        with pytest.raises(ValidationError) as caught:
            aad.model_validate(with_version)
        assert any("subject_version" in [str(p) for p in e["loc"]] for e in caught.value.errors())


def test_aad_schema_version_is_plaintext_version() -> None:
    """ADR 0015 I9 / Decision 4: ``aad_fields.schema_version`` is the schema version of the
    *plaintext*. Validation requires the form ``MAJOR.MINOR.PATCH`` (no leading zeros, no suffix,
    no white space), located at ``schema_version``. The golden vectors seal a plaintext whose own
    ``schema_version`` equals the AAD one, and changing the AAD value fails authentication (the
    plaintext keeps its own version after a bundle is migrated, ADR 0015 Consequences). Reading
    N79: a well-formed version other than the current one is accepted by validation (the
    plaintext is opaque, so the model cannot know its schema); the attacker's best case in
    these tests relies on it."""
    aad = aad_class()
    good = aad_doc("audio")
    assert aad.model_validate(good).schema_version == CURRENT_SCHEMA_VERSION
    for bad in (
        "1", "1.0", "01.0.0", "0.0.01", "v0.0.1", "0.0.1-rc1", "0.0.1+b", " 0.0.1", "0.0.1\n",
        "0.0.1.0", "", "latest", None, 1, 0.1,
    ):  # fmt: skip
        with pytest.raises(ValidationError) as caught:
            aad.model_validate({**good, "schema_version": bad})
        assert any("schema_version" in [str(p) for p in e["loc"]] for e in caught.value.errors())
    assert json.loads(G1_PLAINTEXT)["schema_version"] == G1_DOC["aad_fields"]["schema_version"]
    tampered = replace_at(G1_DOC, ("aad_fields", "schema_version"), "0.0.2")
    _validates(tampered)  # a well-formed other version is accepted (N79)
    with pytest.raises(UnsealError):
        unseal(tampered, kek=KEK)


# ================================================================ I6: round trip, golden vectors


@pytest.mark.parametrize("payload_type", PAYLOAD_TYPES)
def test_seal_unseal_roundtrip(payload_type: str) -> None:
    """ADR 0015 I6: seal then unseal returns the plaintext, for every payload type, through the
    model (``model_validate`` -> ``model_dump(mode="json")`` -> ``model_validate_json``)."""
    if payload_type == "audio":
        plaintext = bytes(range(256)) * 10
    else:
        plaintext = json.dumps({"text": "namaste", "n": 1}).encode()
    doc = seal(plaintext, payload_type=payload_type)
    model = _validates(doc)
    again = sealed_class().model_validate_json(json.dumps(model.model_dump(mode="json")))
    assert again == model
    assert unseal(doc).plaintext == plaintext
    assert unseal(model).plaintext == plaintext
    assert unseal(again).plaintext == plaintext
    assert unseal(doc).digest_verified is False  # no digest, nothing was verified
    assert schema_accepts(sealed_class(), model.model_dump(mode="json", exclude_none=True))
    assert str(model.aad_fields.subject_id) == doc["aad_fields"]["subject_id"]


def test_seal_unseal_roundtrip_non_ascii_and_edge_plaintexts() -> None:
    """Empty, one-byte, NUL, non-UTF-8 and Indic (Devanagari, Tamil, Malayalam with ZWJ/ZWNJ)
    plaintexts come back byte for byte."""
    indic = (
        "\u0928\u092e\u0938\u094d\u0924\u0947 \u0ba4\u0bae\u0bbf\u0bb4\u0bcd "
        "\u0d15\u0d4d\u200d \u0d15\u0d4d\u200c\u0d37"
    ).encode()
    for plaintext in (b"", b"\x00", b"a", bytes(range(256)), indic, b"\xff\xfe\xfd"):
        doc = seal(plaintext, payload_type="audio")
        assert unseal(doc).plaintext == plaintext
    empty = _validates(seal(b"", payload_type="audio"))
    assert len(unb64u(empty.ciphertext)) == TAG_BYTES


def test_golden_vector_profile_unseals_independently() -> None:
    """G1 was computed outside the package. It validates, round-trips, decrypts with plain
    ``cryptography`` (not through the helper), and its AAD is the hand-written JCS string."""
    model = _validates(G1_DOC)
    assert model.model_dump(mode="json", exclude_none=True) == G1_DOC
    assert aad_bytes_of(G1_DOC["aad_fields"]) == G1_AAD
    dek = aes_key_unwrap(KEK, unb64u(G1_DOC["dek_wrap"]["wrapped_dek"]))
    assert dek == G1_DEK
    plaintext = AESGCM(dek).decrypt(unb64u(G1_DOC["nonce"]), unb64u(G1_DOC["ciphertext"]), G1_AAD)
    assert plaintext == G1_PLAINTEXT
    result = unseal(G1_DOC, kek=KEK, key_resolver=resolver_for({"hmac-test-1": DIGEST_KEY}))
    assert result.plaintext == G1_PLAINTEXT
    assert result.digest_verified is True


def test_golden_vector_audio_unseals_independently() -> None:
    """G2: audio bytes, no ``subject_version``, no digest. The AAD has no ``subject_version``
    member at all (a null member is absent, N78)."""
    model = _validates(G2_DOC)
    assert model.plaintext_digest is None
    assert aad_bytes_of(G2_DOC["aad_fields"]) == G2_AAD
    assert aad_bytes_of({**G2_DOC["aad_fields"], "subject_version": None}) == G2_AAD
    dek = aes_key_unwrap(KEK, unb64u(G2_DOC["dek_wrap"]["wrapped_dek"]))
    assert dek == G2_DEK
    assert (
        AESGCM(dek).decrypt(unb64u(G2_DOC["nonce"]), unb64u(G2_DOC["ciphertext"]), G2_AAD)
        == G2_PLAINTEXT
    )
    assert unseal(G2_DOC).plaintext == G2_PLAINTEXT
    # the same document with an explicit null subject_version unseals identically
    with_null = {**G2_DOC, "aad_fields": {**G2_DOC["aad_fields"], "subject_version": None}}
    assert unseal(with_null).plaintext == G2_PLAINTEXT


def test_golden_vectors_are_reproduced_by_the_reference_seal() -> None:
    """With the fixed DEK and nonce, the reference ``seal`` reproduces both golden documents
    exactly: the helper and the outside computation agree."""
    g1 = seal(
        G1_PLAINTEXT,
        payload_type="test_profile",
        subject_id=G1_SUBJECT,
        subject_version=1,
        digest_key=DIGEST_KEY,
        digest_key_ref=DIGEST_KEY_REF,
        dek=G1_DEK,
        nonce=G1_NONCE,
    )
    assert g1 == G1_DOC
    g2 = seal(G2_PLAINTEXT, payload_type="audio", subject_id=G2_SUBJECT, dek=G2_DEK, nonce=G2_NONCE)
    assert g2 == G2_DOC


def test_reference_keywrap_matches_rfc3394_vector() -> None:
    """The reference helper wraps with AES Key Wrap exactly as RFC 3394 section 4.6 (published
    256-bit KEK and key data vector)."""
    assert aes_key_wrap(RFC3394_KEK, RFC3394_DATA) == RFC3394_WRAPPED
    assert aes_key_unwrap(RFC3394_KEK, RFC3394_WRAPPED) == RFC3394_DATA
    sealed = seal(b"{}", payload_type="tool_arguments", dek=RFC3394_DATA, kek=RFC3394_KEK)
    assert unb64u(sealed["dek_wrap"]["wrapped_dek"]) == RFC3394_WRAPPED


def test_one_fresh_dek_and_nonce_per_payload() -> None:
    """ADR 0015 Decisions 2 and 3: a fresh 256-bit DEK and a fresh 12-byte nonce for every
    payload. 200 seals of the same plaintext give 200 distinct DEKs, nonces, wrapped DEKs and
    ciphertexts, and every DEK is 32 bytes."""
    docs = [seal(b'{"same":"plaintext"}', payload_type="tool_result") for _ in range(200)]
    deks = [dek_of(d) for d in docs]
    assert {len(d) for d in deks} == {32}
    assert len(set(deks)) == 200
    assert len({d["nonce"] for d in docs}) == 200
    assert len({d["dek_wrap"]["wrapped_dek"] for d in docs}) == 200
    assert len({d["ciphertext"] for d in docs}) == 200
    assert {len(unb64u(d["nonce"])) for d in docs} == {NONCE_BYTES}


# ==================================================================== I7: tamper, wrong KEK


def _real(payload_type: str = "tool_arguments") -> dict[str, Any]:
    return seal(b'{"account":"12345678"}', payload_type=payload_type)


@pytest.mark.parametrize("where", ["first", "middle", "last-tag-byte"])
def test_tamper_ciphertext_fails(where: str) -> None:
    """ADR 0015 I7: one flipped bit in the ciphertext (first byte, middle, last byte of the tag)
    fails authentication. The tampered document is still schema-valid."""
    doc = _real()
    size = len(unb64u(doc["ciphertext"]))
    index = {"first": 0, "middle": size // 2, "last-tag-byte": size - 1}[where]
    bad = {**doc, "ciphertext": flip_b64u_byte(doc["ciphertext"], index)}
    _validates(bad)
    with pytest.raises(UnsealError):
        unseal(bad)
    assert unseal(doc).plaintext == b'{"account":"12345678"}'


def test_tamper_ciphertext_length_fails() -> None:
    """A truncated ciphertext (a byte, the whole tag), an appended byte, and a ciphertext replaced
    by the empty-plaintext tag all fail."""
    doc = _real()
    raw = unb64u(doc["ciphertext"])
    for label, changed in (
        ("drop last byte", raw[:-1]),
        ("drop tag", raw[:-TAG_BYTES]),
        ("append byte", raw + b"\x00"),
        ("prepend byte", b"\x00" + raw),
        ("tag only", raw[-TAG_BYTES:]),
    ):
        bad = {**doc, "ciphertext": b64u(changed)}
        if len(changed) < TAG_BYTES:
            _rejected(bad, "ciphertext")  # shorter than a tag never validates (N82)
            continue
        with pytest.raises(UnsealError):
            unseal(bad)
        assert label


def test_tamper_nonce_fails() -> None:
    """ADR 0015 I7: a changed nonce fails authentication, for every byte position."""
    doc = _real()
    for index in range(NONCE_BYTES):
        bad = {**doc, "nonce": flip_b64u_byte(doc["nonce"], index, 0x80)}
        _validates(bad)
        with pytest.raises(UnsealError):
            unseal(bad)


def test_tamper_wrapped_dek_fails() -> None:
    """ADR 0015 I7: a changed wrapped DEK fails (the AES Key Wrap integrity check, RFC 3394), for
    every byte position; swapping in the wrapped DEK of another payload fails as well."""
    doc = _real()
    for index in range(WRAPPED_DEK_BYTES):
        bad = replace_at(
            doc, ("dek_wrap", "wrapped_dek"), flip_b64u_byte(doc["dek_wrap"]["wrapped_dek"], index)
        )
        _validates(bad)
        with pytest.raises(UnsealError):
            unseal(bad)
    other = _real()
    spliced = replace_at(doc, ("dek_wrap", "wrapped_dek"), other["dek_wrap"]["wrapped_dek"])
    with pytest.raises(UnsealError):
        unseal(spliced)


def test_unseal_rejects_wrong_kek() -> None:
    """ADR 0015 I7: another KEK, a KEK with one flipped bit and the DEK used as a KEK all fail;
    the error text holds no key bytes. Naming another ``kek_ref`` in the document does not help:
    the reference names a key, it does not choose it."""
    doc = _real()
    wrong = bytes(b ^ 0xFF for b in KEK)
    one_bit = bytes([KEK[0] ^ 1]) + KEK[1:]
    for kek in (wrong, one_bit, dek_of(doc), bytes(32)):
        with pytest.raises(UnsealError) as caught:
            unseal(doc, kek=kek)
        text = f"{caught.value!s} {caught.value!r}"
        assert kek.hex() not in text
        assert b64u(kek) not in text
    renamed = replace_at(doc, ("dek_wrap", "kek_ref"), {"key_id": "another-kek"})
    with pytest.raises(UnsealError):
        unseal(renamed, kek=wrong)
    assert unseal(renamed, kek=KEK).plaintext == b'{"account":"12345678"}'


# ============================================================ I8: AAD swap (A-19, core cases)


def test_aad_swap_to_other_profile_fails() -> None:
    """ADR 0015 I8 / A-19: a sealed profile moved onto another profile (another ``subject_id``)
    or another version of the same profile fails authentication, although the document stays
    schema-valid. Only the original AAD opens it."""
    plaintext = json.dumps({"name": "x"}).encode()
    doc = seal(plaintext, payload_type="test_profile", subject_version=1)
    other_profile = subject_id_for("test_profile")
    swapped_id = replace_at(doc, ("aad_fields", "subject_id"), other_profile)
    swapped_version = replace_at(doc, ("aad_fields", "subject_version"), 2)
    for bad in (swapped_id, swapped_version):
        _validates(bad)
        with pytest.raises(UnsealError):
            unseal(bad)
    assert unseal(doc).plaintext == plaintext


# ================================================================== I10: digest after decryption


def test_digest_mismatch_after_decrypt_raises() -> None:
    """ADR 0015 I10 / Decision 6: a present ``plaintext_digest`` is checked after decryption
    through ``verify_digest`` (with the key resolver) and a mismatch raises ``DigestMismatch``. The
    ciphertext is authentic here; the digest names other content."""
    errors = api("errors")
    plaintext = json.dumps({"name": "asha", "dob": "1990-01-02"}).encode()
    other = {"name": "asha", "dob": "1990-01-03"}
    doc = seal(
        plaintext,
        payload_type="test_profile",
        digest_key=DIGEST_KEY,
        digest_subject=other,
    )
    _validates(doc)
    resolver = resolver_for({"hmac-test-1": DIGEST_KEY})
    with pytest.raises(errors.DigestMismatch):
        unseal(doc, key_resolver=resolver)
    ok = seal(plaintext, payload_type="test_profile", digest_key=DIGEST_KEY)
    result = unseal(ok, key_resolver=resolver)
    assert result.plaintext == plaintext
    assert result.digest_verified is True


def test_digest_checked_with_wrong_key_raises_and_missing_key_is_unverified() -> None:
    """The keyed digest is a keyed check: the right key verifies; another key is a mismatch
    (``DigestMismatch``); no key (no resolver, or the resolver does not know the ``key_ref``) is
    not an error and is never reported as verified (ADR 0015 Consequences: the recipient can
    unseal and authenticate but cannot check the link)."""
    errors = api("errors")
    plaintext = json.dumps({"name": "asha"}).encode()
    doc = seal(plaintext, payload_type="test_profile", digest_key=DIGEST_KEY)
    right = resolver_for({"hmac-test-1": DIGEST_KEY})
    wrong = resolver_for({"hmac-test-1": b"another-key-another-key-another!!"})
    unknown = resolver_for({"some-other-key": DIGEST_KEY})
    assert unseal(doc, key_resolver=right).digest_verified is True
    with pytest.raises(errors.DigestMismatch):
        unseal(doc, key_resolver=wrong)
    for resolver in (None, unknown):
        result = unseal(doc, key_resolver=resolver)
        assert result.plaintext == plaintext
        assert result.digest_verified is False


def test_keyed_plaintext_digest_links_to_the_profile() -> None:
    """ADR 0015 Rejected alternatives / Decision 6: the keyed digest preserves the link to the
    referenced profile. For a sealed ``test_profile`` it is the keyed digest of the profile's
    digest domain (top-level ``schema_version`` and null members removed, ADR 0006 Decision 5): G1
    carries exactly the HMAC of the hand-written domain string, which differs from the plain
    SHA-256 of the same domain."""
    import hashlib
    import hmac

    expected = hmac.new(DIGEST_KEY, G1_DOMAIN.encode(), hashlib.sha256).hexdigest()
    assert G1_DOC["plaintext_digest"]["value"] == expected
    assert hashlib.sha256(G1_DOMAIN.encode()).hexdigest() == G1_PLAIN_SHA256
    assert expected != G1_PLAIN_SHA256
    digest = api("digest")
    profile = json.loads(G1_PLAINTEXT)
    computed = digest.compute_digest(
        profile,
        scheme="hmac-sha256-jcs/v1",
        key=DIGEST_KEY,
        key_ref=digest.KeyRef(key_id="hmac-test-1"),
    )
    assert computed.value == expected
    assert _validates(G1_DOC).plaintext_digest == computed


# ============================== I11, I11a, P-42, A-32: absent or keyed for every payload type


def _doc_with_digest(payload_type: str, digest: Any) -> dict[str, Any]:
    return payload_doc(payload_type, plaintext_digest=digest)


def _keyed_digest_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "scheme": "hmac-sha256-jcs/v1",
        "value": HEX64,
        "key_ref": dict(DIGEST_KEY_REF),
    }
    doc.update(over)
    return doc


def _plain_digest_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"scheme": "sha256-jcs/v1", "value": HEX64}
    doc.update(over)
    return doc


def test_sealed_profile_plaintext_digest_absent_or_keyed() -> None:
    """ADR 0015 I11 / Decision 6 (user decision 2): a sealed profile's ``plaintext_digest`` is
    absent or ``hmac-sha256-jcs/v1`` with a ``key_ref``."""
    _validates(payload_doc("test_profile"))
    _validates(payload_doc("test_profile", plaintext_digest=None))
    model = _validates(_doc_with_digest("test_profile", _keyed_digest_doc()))
    assert model.plaintext_digest.scheme == "hmac-sha256-jcs/v1"
    assert model.plaintext_digest.key_ref.key_id == "hmac-test-1"


def test_sealed_profile_plain_digest_rejected() -> None:
    """ADR 0015 I11 / A-32: a sealed profile with a plain ``sha256-jcs/v1`` ``plaintext_digest``
    is a validation error located at ``plaintext_digest``. The message names the rule and neither
    the digest value nor the scheme string of the offending digest."""
    plain = _plain_digest_doc(value="ab" * 32)
    error = _rejected(_doc_with_digest("test_profile", plain), "plaintext_digest")
    for text in (str(error), repr(error), error.json(include_input=False)):
        assert "ab" * 32 not in text
    assert any("plaintext_digest" in [str(p) for p in e["loc"]] for e in error.errors())
    assert all(str(e["msg"]).strip() for e in error.errors())


@pytest.mark.parametrize("payload_type", PAYLOAD_TYPES)
def test_sealed_payload_plaintext_digest_absent_or_keyed_all_types(payload_type: str) -> None:
    """ADR 0015 I11a / P-42 (binding): the drop-or-key rule holds for every payload type (test
    profile, audio, transcript text, tool arguments, tool results), because short sealed text is
    as guessable as a profile. Absent (omitted or null) and keyed are accepted; a plain digest is
    rejected at ``plaintext_digest``, whether given as a document or as a ``ContentDigest``
    instance (a validated instance is not a way around the rule)."""
    digest = api("digest")
    _validates(payload_doc(payload_type))
    _validates(payload_doc(payload_type, plaintext_digest=None))
    keyed = _validates(_doc_with_digest(payload_type, _keyed_digest_doc()))
    assert keyed.plaintext_digest.scheme == "hmac-sha256-jcs/v1"
    keyed_instance = digest.ContentDigest(
        scheme="hmac-sha256-jcs/v1", value=HEX64, key_ref=digest.KeyRef(key_id="hmac-test-1")
    )
    assert _validates(_doc_with_digest(payload_type, keyed_instance)).plaintext_digest is not None

    _rejected(_doc_with_digest(payload_type, _plain_digest_doc()), "plaintext_digest")
    plain_instance = digest.ContentDigest(scheme="sha256-jcs/v1", value=HEX64)
    _rejected(_doc_with_digest(payload_type, plain_instance), "plaintext_digest")
    # a real plain digest of real content, not just zeros
    real = plain_digest_doc({"answer": "yes"})
    assert real["scheme"] == "sha256-jcs/v1"
    _rejected(_doc_with_digest(payload_type, real), "plaintext_digest")


def test_plaintext_digest_other_shapes_rejected() -> None:
    """The only digest shapes are the documented ones: a keyed digest without ``key_ref`` and a
    plain one with a ``key_ref`` are the ``ContentDigest`` rules; a bare string, a number and an
    empty object are not digests."""
    shapes: list[Any] = [
        _keyed_digest_doc(key_ref=None),
        _plain_digest_doc(key_ref=dict(DIGEST_KEY_REF)),
        _keyed_digest_doc(value="A" * 64),
        _keyed_digest_doc(value="0" * 63),
        _keyed_digest_doc(extra="x"),
        "a" * 64,
        1,
        {},
        [],
    ]
    for bad in shapes:
        _rejected(payload_doc(plaintext_digest=bad), "plaintext_digest")


def test_unregistered_digest_scheme_is_a_typed_error_not_unverifiable() -> None:
    """An unregistered scheme in ``plaintext_digest`` raises ``UnknownDigestScheme`` unwrapped (ADR
    0006 Decision 3), including case and white-space variants of the two registered names. It is
    never accepted as "unverifiable"."""
    errors = api("errors")
    for scheme in (
        "sha512-jcs/v1",
        "HMAC-SHA256-JCS/V1",
        "hmac-sha256-jcs/v1 ",
        " sha256-jcs/v1",
        "hmac-sha256-jcs/v2",
        "",
    ):
        with pytest.raises(errors.UnknownDigestScheme):
            _validates(payload_doc(plaintext_digest=_keyed_digest_doc(scheme=scheme)))


def test_digest_key_ref_differs_from_kek_ref() -> None:
    """Proposed name (N85). Key separation: the key that computes ``plaintext_digest`` must not be
    the key-encryption key, because a recipient entrusted with the digest key (to check the link)
    would then be able to unwrap every DEK. A ``key_ref`` equal to ``dek_wrap.kek_ref`` (same
    ``key_id`` and ``key_version``) is rejected at ``plaintext_digest``; another ``key_version`` or
    another ``key_id`` is accepted."""
    same = _keyed_digest_doc(key_ref=dict(KEK_REF))
    _rejected(payload_doc(plaintext_digest=same), "plaintext_digest")
    only_id = _keyed_digest_doc(key_ref={"key_id": KEK_REF["key_id"]})
    _validates(payload_doc(plaintext_digest=only_id))
    other_version = _keyed_digest_doc(key_ref={"key_id": KEK_REF["key_id"], "key_version": "2"})
    _validates(payload_doc(plaintext_digest=other_version))
    _validates(payload_doc(plaintext_digest=_keyed_digest_doc()))


# ===================================================================== I12: only in ExportBundle


def test_sealed_only_in_export_bundle() -> None:
    """ADR 0015 I12 / Decision 7 (structural half; the behavioural half, an ExportBundle that
    carries a sealed payload and a RunBundle or snapshot that refuses one, is P1-14): no entity
    other than ``ExportBundle`` embeds ``SealedPayload`` at any depth, and the JSON Schema of every
    other entity does not mention it."""
    registry = api("registry")
    sealed = sealed_class()
    entities = registry.entity_classes()
    holders = {
        name for name, model in ((m.__name__, m) for m in entities.values())
        if sealed in embedded_models(model)
    }  # fmt: skip
    assert holders <= {"ExportBundle"}, f"sealed payloads outside an ExportBundle: {holders}"
    for model in entities.values():
        if model.__name__ == "ExportBundle":
            continue
        assert "SealedPayload" not in json.dumps(schema_of(model)), model.__name__
    # value objects that embed it are only reachable from ExportBundle (checked above for entities)
    value_holders = [
        m.__name__
        for m in registry.model_classes()
        if m is not sealed and sealed in embedded_models(m) and m not in entities.values()
    ]
    for name in value_holders:
        users = [
            e.__name__
            for e in entities.values()
            if registered_class(name) in embedded_models(e) and e.__name__ != "ExportBundle"
        ]
        assert not users, f"{name} embeds SealedPayload and is used by {users}"


# =========================================================================== RedactionRecord


def test_redaction_record_fields() -> None:
    """SCOPE entity specs / ADR 0016 Decision 7 and I10 (structural half; the bundle rule that
    demands a seal or a redaction is P1-14): ``RedactionRecord{method, tool_ref, performed_at,
    covers[]}``, all required, a frozen value object with ``extra="forbid"`` and no entity
    fields. ``method`` and ``tool_ref`` are non-empty text (N86), ``performed_at`` a UTC instant."""
    record = redaction_class()
    base = api("base")
    assert not issubclass(record, base.EntityBase)
    assert set(record.model_fields) == {"method", "tool_ref", "performed_at", "covers"}
    assert all(f.is_required() for f in record.model_fields.values())
    assert record.model_config.get("extra") == "forbid"
    assert record.model_config.get("frozen") is True
    good = record.model_validate(redaction_doc())
    assert good.method == "ner_mask"
    assert good.tool_ref == "redactor 1.4.2"
    assert list(good.covers) == ["audio", "text", "tool_arguments"]
    assert record.model_validate_json(json.dumps(good.model_dump(mode="json"))) == good
    for name in ("method", "tool_ref", "performed_at", "covers"):
        with pytest.raises(ValidationError) as caught:
            record.model_validate({k: v for k, v in redaction_doc().items() if k != name})
        assert any(name in [str(p) for p in e["loc"]] for e in caught.value.errors())
    for extra in ("api_key", "confidence", "id", "created_at", "reason", "payload_ref"):
        with pytest.raises(ValidationError):
            record.model_validate(redaction_doc(**{extra: "x"}))
    with pytest.raises(ValidationError):
        good.method = "other"  # frozen
    for field in ("method", "tool_ref"):
        for bad in ("", None, 1, ["x"], {"a": 1}):
            with pytest.raises(ValidationError):
                record.model_validate(redaction_doc(**{field: bad}))
    # non-ASCII method and tool names are text, not identifiers
    assert record.model_validate(
        redaction_doc(method="\u0915\u0935\u0930", tool_ref="\u0909")
    ).method


def test_redaction_performed_at_is_a_utc_instant() -> None:
    """``performed_at`` is the package's ``UtcDatetime``: an aware ISO 8601 instant, stored in UTC
    and written with a ``Z`` suffix. A naive time, a date, a number and a free-text date are
    rejected at the field; an instant with an offset is the same instant in UTC."""
    record = redaction_class()
    for ok in ("2026-10-22T10:00:00Z", "2026-10-22T10:00:00.000000Z", "2026-10-22T10:00:00.5Z"):
        record.model_validate(redaction_doc(performed_at=ok))
    offset = record.model_validate(redaction_doc(performed_at="2026-10-22T15:30:00+05:30"))
    assert offset.model_dump(mode="json")["performed_at"] == "2026-10-22T10:00:00.000000Z"
    for bad in (
        "2026-10-22T10:00:00",
        "2026-10-22",
        "22/10/2026",
        "yesterday",
        1761127200,
        "",
        None,
    ):
        with pytest.raises(ValidationError) as caught:
            record.model_validate(redaction_doc(performed_at=bad))
        assert any("performed_at" in [str(p) for p in e["loc"]] for e in caught.value.errors())


def test_redaction_covers_each_payload_kind_separately() -> None:
    """ADR 0016 I10 (structural half): ``covers`` is a non-empty list of distinct members of a
    closed set that names the payload kinds separately: ``audio``, ``text``, ``tool_arguments``
    and (S-N87, owner-approved) ``tool_result``; no other member is allowed.
    Each kind alone is a valid record, an empty or repeating list and an unknown kind are not;
    the JSON Schema states the set as an ``enum``."""
    record = redaction_class()
    kinds = {"audio", "text", "tool_arguments"}
    for kind in kinds:
        assert list(record.model_validate(redaction_doc(covers=[kind])).covers) == [kind]
    for pair in (["audio", "text"], ["text", "tool_arguments"], ["tool_arguments", "audio"]):
        assert list(record.model_validate(redaction_doc(covers=pair)).covers) == pair
    for bad in (
        [],
        ["audio", "audio"],
        ["video"],
        ["Audio"],
        ["audio "],
        ["profile"],
        ["all"],
        ["*"],
        "audio",
        None,
        [None],
        [1],
        ["audio", None],
    ):
        with pytest.raises(ValidationError) as caught:
            record.model_validate(redaction_doc(covers=bad))
        assert any("covers" in [str(p) for p in e["loc"]] for e in caught.value.errors())
    enum = set(property_enum(record, "covers"))
    assert kinds <= enum <= kinds | {"tool_result"}
    # S-N87: tool_result is a known member; the set is exactly the four kinds
    assert list(record.model_validate(redaction_doc(covers=["tool_result"])).covers) == [
        "tool_result"
    ]
    four = ["audio", "text", "tool_arguments", "tool_result"]
    assert list(record.model_validate(redaction_doc(covers=four)).covers) == four
    with pytest.raises(ValidationError):
        record.model_validate(redaction_doc(covers=["tool_result", "tool_result"]))
    assert enum == kinds | {"tool_result"}
    assert not contains_key(schema_of(record), "const")


# ========================================================================= no-echo (ADR 0007)


def test_sealed_models_hide_their_input() -> None:
    """ADR 0007 Decision 8 (a), (b) (N88): ``SealedPayload`` and the two models nested in it hide
    their input, because a rejected value can be a damaged ciphertext or wrapped key, or the id of
    a subject whose data is sealed. The set stays closed under embedding: every model of the
    package that embeds one of them hides too (``ExportBundle`` at P1-14 falls under this)."""
    registry = api("registry")
    seeds = {sealed_class(), dek_wrap_class(), aad_class()}
    for model in seeds:
        assert model.model_config.get("hide_input_in_errors") is True, model.__name__
    unprotected = [
        m.__name__
        for m in registry.model_classes()
        if embedded_models(m) & seeds and m.model_config.get("hide_input_in_errors") is not True
    ]
    assert not unprotected, f"models that embed a sealed model but show their input: {unprotected}"


def test_sealed_errors_do_not_echo_values() -> None:
    """ADR 0007 Decision 8 (c) as extended by N42: a rejected value never appears in ``str``,
    ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``, through
    ``model_validate`` and ``model_validate_json``, on ``SealedPayload`` and on the nested models
    alone. A marker sits in every rejectable field; the message names the field and the rule."""
    mark = QUOTE_MARK
    sealed, wrap, aad = sealed_class(), dek_wrap_class(), aad_class()
    bad_b64 = mark + "!"
    bad_ref = {"key_id": "k", "unknown_field": mark}
    good = payload_doc("test_profile")
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("format", sealed, {**good, "format": marked_set()}, "format"),
        ("alg", sealed, {**good, "alg": marked_set()}, "alg"),
        ("nonce chars", sealed, {**good, "nonce": bad_b64 * 2}, "nonce"),
        ("nonce length", sealed, {**good, "nonce": mark}, "nonce"),
        ("ciphertext chars", sealed, {**good, "ciphertext": bad_b64 * 4}, "ciphertext"),
        ("ciphertext length", sealed, {**good, "ciphertext": mark}, "ciphertext"),
        ("unknown member", sealed, {**good, "unknown_field": mark}, "unknown_field"),
        ("wrapped chars", wrap, dek_wrap_doc(wrapped_dek=bad_b64 * 6), "wrapped_dek"),
        ("wrapped length", wrap, dek_wrap_doc(wrapped_dek=mark), "wrapped_dek"),
        ("wrap alg", wrap, dek_wrap_doc(alg=marked_set()), "alg"),
        ("wrap unknown", wrap, {**dek_wrap_doc(), "unknown_field": mark}, "unknown_field"),
        ("kek_ref unknown", wrap, dek_wrap_doc(kek_ref=bad_ref), "unknown_field"),
        ("payload_type", aad, {**aad_doc("audio"), "payload_type": marked_set()}, "payload_type"),
        ("subject_id", aad, aad_doc("test_profile", subject_id=marked_id("tpf")), "subject_id"),
        ("wrong kind", aad, aad_doc("audio", subject_id=marked_id("art")), "subject_id"),
        ("schema_version", aad, aad_doc("audio", schema_version="0.0." + mark), "schema_version"),
        ("subject_version", aad, aad_doc("test_profile", subject_version=mark), "subject_version"),
        ("aad unknown", aad, {**aad_doc("audio"), "unknown_field": mark}, "unknown_field"),
    ]  # fmt: skip
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, mark, field=field, label=label)
    # embedded: the same markers inside a full payload
    embedded_cases = (
        ("subject", replace_at(good, ("aad_fields", "subject_id"), marked_id("tpf")), "subject_id"),
        ("wrapped", replace_at(good, ("dek_wrap", "wrapped_dek"), bad_b64 * 6), "wrapped_dek"),
        ("sver", replace_at(good, ("aad_fields", "schema_version"), "9." + mark), "schema_version"),
    )  # fmt: skip
    for label, doc, field in embedded_cases:
        assert_input_hidden(sealed, doc, mark, field=field, label=label)
    # the digest rule: the plain digest value and an unknown scheme are not echoed
    plain = _doc_with_digest("audio", _plain_digest_doc(value="cd" * 32))
    error = _rejected(plain, "plaintext_digest")
    for rendering in (str(error), repr(error), error.json(include_input=False)):
        assert "cd" * 32 not in rendering
    errors = api("errors")
    with pytest.raises(errors.UnknownDigestScheme) as scheme_error:
        _validates(payload_doc(plaintext_digest=_keyed_digest_doc(scheme="no_such_" + mark)))
    assert mark not in str(scheme_error.value)
    assert mark not in repr(scheme_error.value)


# ============================================================================ contract walks


def test_sealed_models_roundtrip_and_schema_agree() -> None:
    """``model_validate`` -> ``model_dump(mode="json")`` -> JSON -> ``model_validate_json`` is the
    identity, and the JSON Schema (validation and serialization mode) accepts the dump and
    rejects a document with an unknown member, for the golden vectors and every payload type."""
    sealed = sealed_class()
    docs = [G1_DOC, G2_DOC] + [payload_for(t) for t in PAYLOAD_TYPES]
    for doc in docs:
        model = _validates(doc)
        dumped = model.model_dump(mode="json")
        assert sealed.model_validate_json(json.dumps(dumped)) == model
        present = model.model_dump(mode="json", exclude_none=True)
        for mode in ("validation", "serialization"):
            assert schema_accepts(sealed, present, mode), (mode, present["aad_fields"])
        assert not schema_accepts(sealed, {**present, "key": "x"})
    jsonschema = __import__("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema_of(sealed))


def test_sealed_schema_has_no_secret_like_property_and_no_example_keys() -> None:
    """ADR 0007 Decision 3 over the sealed models (the walk of test_project_agent covers every
    registered model; this one names the sealed ones so a rename cannot hide). The schema holds
    no ``default`` that is key-like and no ``examples`` with literal ciphertext."""
    for model in (sealed_class(), dek_wrap_class(), aad_class(), redaction_class()):
        schema = schema_of(model)
        for node in walk_nodes(schema):
            assert not any(secret_like(name) for name in node.get("properties", {}))
            assert "examples" not in node
            assert "example" not in node


def test_sealed_classes_are_value_objects_found_by_the_registry() -> None:
    """The registry walk finds ``SealedPayload`` and ``RedactionRecord`` (gate-bound: test_ids
    lists both in VALUE_OBJECT_NAMES), and the model behind ``aad_fields`` is the only nested
    model besides ``dek_wrap``, ``KeyRef`` and ``ContentDigest``."""
    registry = api("registry")
    value_objects = registry.value_object_classes()
    assert "SealedPayload" in value_objects
    assert "RedactionRecord" in value_objects
    nested = embedded_models(sealed_class())
    assert {m.__name__ for m in nested} >= {"KeyRef", "ContentDigest"}
    assert aad_class() in nested
    assert dek_wrap_class() in nested
    assert len(nested) == 4, sorted(m.__name__ for m in nested)


# ============================================================== the reference helper itself


def test_reference_aad_is_jcs_with_null_members_removed() -> None:
    """The reference AAD is the RFC 8785 text of ``aad_fields`` with null members removed: keys in
    sorted order, no white space, non-ASCII not escaped, ``subject_version`` absent when null."""
    assert aad_bytes_of(G1_DOC["aad_fields"]) == G1_AAD
    assert aad_bytes_of({"subject_version": None, **G2_DOC["aad_fields"]}) == G2_AAD
    assert re.fullmatch(rb'\{"payload_type":"audio",.*"subject_id":"art_[0-9a-f-]+"\}', G2_AAD)


def test_reference_unseal_rejects_malformed_documents_before_decrypting() -> None:
    """The reference ``unseal`` validates with the model first: an unknown ``format`` or ``alg``
    is the model's ``ValidationError``, never a downgrade to some other cipher."""
    doc = _real()
    for field, bad in (("format", "tracer.sealed.v2"), ("alg", "none")):
        with pytest.raises(ValidationError):
            unseal({**doc, field: bad})


def test_reference_seal_without_digest_key_has_no_digest() -> None:
    """``seal`` writes ``plaintext_digest`` only when a digest key is given; with a key it writes
    the keyed scheme and names the key in ``key_ref``."""
    plaintext = json.dumps({"a": 1}).encode()
    assert "plaintext_digest" not in seal(plaintext, payload_type="tool_arguments")
    keyed = seal(plaintext, payload_type="tool_arguments", digest_key=DIGEST_KEY)
    assert keyed["plaintext_digest"]["scheme"] == "hmac-sha256-jcs/v1"
    assert keyed["plaintext_digest"]["key_ref"] == DIGEST_KEY_REF
    assert DIGEST_KEY.hex() not in json.dumps(keyed)
