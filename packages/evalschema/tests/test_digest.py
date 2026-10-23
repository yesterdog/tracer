# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-05: ContentDigest, the scheme registry, verify_digest, the digest domain (ADR 0006).

Assumed public API (docs/phase1/api-surface.md) in ``tracer_evalschema.digest``:
``DIGEST_SCHEMES`` (read-only registry), ``ContentDigest``, ``KeyRef``, ``compute_digest()``,
``digest_domain()``, ``canonical_bytes()``, ``verify_digest()``; typed errors in
``tracer_evalschema.errors``. Typed errors are not wrapped by pydantic: they propagate out of
``model_validate`` as themselves.

The golden digests were computed outside the package, from hand-written canonical JSON strings
(RFC 8785) hashed with hashlib/hmac, and cross-checked against the rfc8785 library.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import PACKAGE_DIR, api, resolve_schema, versioned_doc, widget_classes

PLAIN = "sha256-jcs/v1"
KEYED = "hmac-sha256-jcs/v1"
HEX64 = "0" * 64

GOLDEN_OBJ: dict[str, Any] = {
    "id": "evl_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
    "name": "caf\u00e9 \u0939\u093f",
    "n": 1,
    "ratio": 0.5,
    "items": [3, 1, 2],
    "flag": True,
    "nested": {"b": 2, "a": 1},
    "empty": [],
}
GOLDEN_CANONICAL = (
    '{"empty":[],"flag":true,"id":"evl_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b","items":[3,1,2],'
    '"n":1,"name":"caf\u00e9 \u0939\u093f","nested":{"a":1,"b":2},"ratio":0.5}'
)
GOLDEN_SHA256 = "ef003a6ac8a7c09c6f7d96a6bb944099e6320f7b29513f76db4b28ab10ee71c0"
GOLDEN_HMAC_KEY = b"tracer-test-key-0000000000000000"
GOLDEN_HMAC = "3ab054d4aa2fd58d033bb8556ebf55da97183e15098455c08f8744d290d05593"
# keys are sorted by UTF-16 code unit: U+1F600 (D83D DE00) sorts before U+FF5E, unlike Python's
# code point order
ASTRAL_OBJ = {"\uff5e": 2, "\U0001f600": 1}
ASTRAL_CANONICAL = '{"\U0001f600":1,"\uff5e":2}'
ASTRAL_SHA256 = "5848fd4854a6ce2c6f23359368eef2f1a8d667176e91a9b74a92b7086fa7b207"
# RFC 8785 appendix B numbers: ES6 shortest round-trip formatting
NUMBERS_OBJ = {"numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001]}
NUMBERS_CANONICAL = '{"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27]}'
NUMBERS_SHA256 = "7c892d3452ad85ad65857a43e8dcac93b79475d2334fc3e85bac5c599142c158"


def _digest() -> Any:
    return api("digest")


def _errors() -> Any:
    return api("errors")


def _with_noise(obj: dict[str, Any]) -> dict[str, Any]:
    """Fields that are outside the digest domain must not change the result."""
    return {
        **obj,
        "schema_version": "0.0.1",
        "content_digest": {"scheme": PLAIN, "value": HEX64},
        "dropped": None,
    }


def test_content_digest_scheme_registered() -> None:
    digest = _digest()
    assert set(digest.DIGEST_SCHEMES) == {PLAIN, KEYED}
    assert len(digest.DIGEST_SCHEMES) == 2
    with pytest.raises(TypeError):
        digest.DIGEST_SCHEMES["none"] = object()
    with pytest.raises(TypeError):
        del digest.DIGEST_SCHEMES[PLAIN]
    # the field type is the registry: every registered scheme is accepted, nothing else
    ok_plain = digest.ContentDigest(scheme=PLAIN, value=HEX64)
    assert ok_plain.scheme == PLAIN
    assert ok_plain.key_ref is None
    ok_keyed = digest.ContentDigest(scheme=KEYED, value=HEX64, key_ref=digest.KeyRef(key_id="k1"))
    assert ok_keyed.scheme == KEYED
    schema = digest.ContentDigest.model_json_schema()
    assert "scheme" in schema["properties"]


def test_unknown_digest_scheme_rejected() -> None:
    digest = _digest()
    errors = _errors()
    for scheme in (
        "none",
        "sha256",
        "md5-jcs/v1",
        "sha256-jcs/v2",
        "SHA256-JCS/V1",
        "",
        "hmac-sha256-jcs/v1 ",
    ):
        with pytest.raises(errors.UnknownDigestScheme):
            digest.ContentDigest(scheme=scheme, value=HEX64)
        # ContentDigest hides its input, so the scheme name is asserted on the verify path only
        forged_scheme = digest.ContentDigest.model_construct(
            scheme=scheme, value=HEX64, key_ref=None
        )
        with pytest.raises(errors.UnknownDigestScheme) as verify_caught:
            digest.verify_digest({"a": 1}, forged_scheme)
        assert scheme in str(verify_caught.value) or scheme == ""
    # a record that bypasses validation still cannot be "verified": it raises, never succeeds
    forged = digest.ContentDigest.model_construct(scheme="none", value=HEX64, key_ref=None)
    with pytest.raises(errors.UnknownDigestScheme):
        digest.verify_digest({"a": 1}, forged)
    assert issubclass(errors.UnknownDigestScheme, errors.TracerSchemaError)


def test_digest_plain_sha256_jcs_golden() -> None:
    digest = _digest()
    # sanity of the golden data itself, independent of the package under test
    assert hashlib.sha256(GOLDEN_CANONICAL.encode("utf-8")).hexdigest() == GOLDEN_SHA256
    assert hashlib.sha256(ASTRAL_CANONICAL.encode("utf-8")).hexdigest() == ASTRAL_SHA256
    assert hashlib.sha256(NUMBERS_CANONICAL.encode("utf-8")).hexdigest() == NUMBERS_SHA256
    # the package produces exactly those bytes and digests
    assert digest.canonical_bytes(GOLDEN_OBJ) == GOLDEN_CANONICAL.encode("utf-8")
    assert digest.canonical_bytes(ASTRAL_OBJ) == ASTRAL_CANONICAL.encode("utf-8")
    assert digest.canonical_bytes(NUMBERS_OBJ) == NUMBERS_CANONICAL.encode("utf-8")
    for obj, expected in (
        (GOLDEN_OBJ, GOLDEN_SHA256),
        (ASTRAL_OBJ, ASTRAL_SHA256),
        (NUMBERS_OBJ, NUMBERS_SHA256),
    ):
        result = digest.compute_digest(obj)
        assert isinstance(result, digest.ContentDigest)
        assert result.scheme == PLAIN
        assert result.key_ref is None
        assert result.value == expected
        assert re.fullmatch(r"[0-9a-f]{64}", result.value)
        digest.verify_digest(obj, result)  # returns None, does not raise
        assert digest.verify_digest(obj, result) is None
    # fields outside the domain do not matter, key order does not matter
    assert digest.compute_digest(_with_noise(GOLDEN_OBJ)).value == GOLDEN_SHA256
    assert digest.compute_digest(dict(reversed(list(GOLDEN_OBJ.items())))).value == GOLDEN_SHA256


def test_hmac_digest_golden() -> None:
    digest = _digest()
    errors = _errors()
    assert hmac.new(
        GOLDEN_HMAC_KEY, GOLDEN_CANONICAL.encode("utf-8"), hashlib.sha256
    ).hexdigest() == (GOLDEN_HMAC)
    key_ref = digest.KeyRef(key_id="test-key-1")
    result = digest.compute_digest(GOLDEN_OBJ, scheme=KEYED, key=GOLDEN_HMAC_KEY, key_ref=key_ref)
    assert result.scheme == KEYED
    assert result.value == GOLDEN_HMAC
    assert result.key_ref == key_ref
    assert result.value != GOLDEN_SHA256, "keyed and plain digests of one value differ"
    seen: list[Any] = []

    def resolver(ref: Any) -> bytes | None:
        seen.append(ref)
        return GOLDEN_HMAC_KEY if ref.key_id == "test-key-1" else None

    assert digest.verify_digest(GOLDEN_OBJ, result, key_resolver=resolver) is None
    assert seen == [key_ref], "the resolver gets the KeyRef, never key material from the digest"
    # a different key is a mismatch, not a success
    with pytest.raises(errors.DigestMismatch):
        digest.verify_digest(
            GOLDEN_OBJ, result, key_resolver=lambda ref: b"another-key-00000000000000000000"
        )
    with pytest.raises(errors.DigestMismatch):
        digest.verify_digest({**GOLDEN_OBJ, "n": 2}, result, key_resolver=resolver)
    # keyed computation needs both a key and a key_ref
    for kwargs in ({"key": GOLDEN_HMAC_KEY}, {"key_ref": key_ref}, {}):
        with pytest.raises((ValueError, TypeError, errors.TracerSchemaError)):
            digest.compute_digest(GOLDEN_OBJ, scheme=KEYED, **kwargs)
    # the plain scheme takes no key
    with pytest.raises((ValueError, TypeError, errors.TracerSchemaError)):
        digest.compute_digest(GOLDEN_OBJ, scheme=PLAIN, key=GOLDEN_HMAC_KEY, key_ref=key_ref)


def test_digest_key_ref_must_be_absent_for_v1() -> None:
    digest = _digest()
    key_ref = digest.KeyRef(key_id="k1")
    with pytest.raises(ValidationError):
        digest.ContentDigest(scheme=PLAIN, value=HEX64, key_ref=key_ref)
    with pytest.raises(ValidationError):
        digest.ContentDigest.model_validate(
            {"scheme": PLAIN, "value": HEX64, "key_ref": {"key_id": "k1"}}
        )
    assert digest.ContentDigest(scheme=PLAIN, value=HEX64).key_ref is None
    assert digest.ContentDigest(scheme=PLAIN, value=HEX64, key_ref=None).key_ref is None


def test_content_digest_has_no_salt_ref_field() -> None:
    digest = _digest()
    assert "salt_ref" not in digest.ContentDigest.model_fields
    assert set(digest.ContentDigest.model_fields) == {"scheme", "value", "key_ref"}
    with pytest.raises(ValidationError):
        digest.ContentDigest.model_validate(
            {"scheme": PLAIN, "value": HEX64, "salt_ref": {"key_id": "k1"}}
        )
    assert "salt_ref" not in digest.ContentDigest.model_json_schema()["properties"]
    # value shape: 64 lowercase hex characters, nothing else
    for bad in (
        "",
        "0" * 63,
        "0" * 65,
        "A" * 64,
        "g" * 64,
        " " + "0" * 63,
        "0" * 64 + "\n",
        "\uff10" * 64,
    ):
        with pytest.raises(ValidationError):
            digest.ContentDigest(scheme=PLAIN, value=bad)
    with pytest.raises(ValidationError):
        digest.ContentDigest.model_validate({"scheme": PLAIN, "value": HEX64, "extra": 1})


def test_hmac_digest_requires_key_ref() -> None:
    digest = _digest()
    with pytest.raises(ValidationError):
        digest.ContentDigest(scheme=KEYED, value=HEX64)
    with pytest.raises(ValidationError):
        digest.ContentDigest(scheme=KEYED, value=HEX64, key_ref=None)
    with pytest.raises(ValidationError):
        digest.ContentDigest(
            scheme=KEYED, value=HEX64, key_ref="k1"
        )  # a bare string is not a KeyRef
    ok = digest.ContentDigest(scheme=KEYED, value=HEX64, key_ref=digest.KeyRef(key_id="k1"))
    assert ok.key_ref.key_id == "k1"
    # KeyRef names a key and carries none, and rejects unknown fields (no secret-bearing field)
    for forbidden in ("key", "secret", "key_material", "value", "password"):
        with pytest.raises(ValidationError):
            digest.KeyRef.model_validate({"key_id": "k1", forbidden: "x"})
    assert {"key_id"} <= set(digest.KeyRef.model_fields)
    assert set(digest.KeyRef.model_fields) <= {"key_id", "key_version", "provider_hint"}


def test_keyed_digest_unverifiable_without_key_raises() -> None:
    digest = _digest()
    errors = _errors()
    keyed = digest.compute_digest(
        GOLDEN_OBJ, scheme=KEYED, key=GOLDEN_HMAC_KEY, key_ref=digest.KeyRef(key_id="k1")
    )
    with pytest.raises(errors.DigestKeyUnavailable):
        digest.verify_digest(GOLDEN_OBJ, keyed)
    with pytest.raises(errors.DigestKeyUnavailable):
        digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=lambda ref: None)

    def failing(ref: Any) -> bytes:
        raise KeyError(ref.key_id)

    with pytest.raises(errors.DigestKeyUnavailable):
        digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=failing)
    # never success, even when the content is wrong: the key question comes first
    with pytest.raises(errors.DigestKeyUnavailable):
        digest.verify_digest({"other": 1}, keyed)
    assert issubclass(errors.DigestKeyUnavailable, errors.TracerSchemaError)
    # a plain digest needs no resolver and ignores one
    plain = digest.compute_digest(GOLDEN_OBJ)
    assert digest.verify_digest(GOLDEN_OBJ, plain, key_resolver=lambda ref: None) is None
    with pytest.raises(errors.DigestMismatch):
        digest.verify_digest({**GOLDEN_OBJ, "n": 2}, plain)


def test_digest_verification_single_entry_point() -> None:
    """No module other than digest.py hashes or compares digests (ADR 0006 Decision 4)."""
    digest = _digest()
    assert callable(digest.verify_digest)
    root = PACKAGE_DIR / "src" / "tracer_evalschema"
    assert (root / "digest.py").is_file()
    pattern = re.compile(
        r"^\s*(?:import|from)\s+(?:hashlib|hmac|rfc8785)\b|\bcompare_digest\b|\bhashlib\.\w+\(|\bhmac\.new\(",
        re.MULTILINE,
    )
    offenders = [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*.py"))
        if path.name != "digest.py" or path.parent != root
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"digests computed or compared outside digest.py: {offenders}"
    assert pattern.search("import hashlib\n")
    assert pattern.search("hmac.compare_digest(a, b)")


def test_digest_domain_excludes_schema_version_and_nulls() -> None:
    digest = _digest()
    base = {"a": 1, "nested": {"x": 1}, "items": [1, 2], "empty": []}
    reference = digest.compute_digest(base)
    # (a) content_digest, (b) schema_version and (c) null fields are outside the domain
    noisy = {
        **base,
        "schema_version": "9.9.9",
        "content_digest": {"scheme": PLAIN, "value": HEX64},
        "gone": None,
        "nested": {"x": 1, "y": None},
    }
    assert digest.compute_digest(noisy) == reference
    assert digest.digest_domain(noisy) == base
    assert digest.digest_domain({"a": None, "b": 1}) == {"b": 1}
    # lists and empty lists are kept, so they are part of the domain
    assert digest.compute_digest({**base, "empty": [1]}) != reference
    without_empty = {k: v for k, v in base.items() if k != "empty"}
    assert digest.compute_digest(without_empty) != reference
    # any other change moves the digest
    for changed in ({**base, "a": 2}, {**base, "nested": {"x": 2}}, {**base, "items": [2, 1]}):
        assert digest.compute_digest(changed) != reference


def test_digest_domain_of_a_versioned_entity() -> None:
    """Proposed name. Same rule, observed through a VersionedEntity (verified on load)."""
    digest = _digest()
    widget = widget_classes().VersionedWidget
    doc = versioned_doc()
    loaded = widget.model_validate(doc)
    domain = digest.digest_domain(loaded)
    assert "content_digest" not in domain
    assert "schema_version" not in domain
    assert all(value is not None for value in domain.values())
    assert {"id", "created_at", "version", "name", "metadata", "metadata_flags"} <= set(domain)
    assert domain["id"] == doc["id"]
    assert domain["created_at"] == "2026-10-22T10:00:00.000000Z"
    assert digest.compute_digest(loaded) == digest.compute_digest(domain)
    assert loaded.content_digest.value == doc["content_digest"]["value"]
    # an explicit null for an optional field is outside the domain: the stored digest still verifies
    assert widget.model_validate({**doc, "note": None}).note is None
    # a real change is not
    with pytest.raises(_errors().DigestMismatch):
        widget.model_validate({**doc, "name": "other"})


def test_digest_rejects_non_ijson_values() -> None:
    digest = _digest()
    errors = _errors()
    for bad in (
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": float("-inf")},
        {"x": 2**53},
        {"x": 2**63},
        {"x": -(2**53)},
        {"x": "\ud800"},
        {"x": ["ok", {"y": 2**70}]},
    ):
        with pytest.raises(errors.NonIJsonValueError):
            digest.compute_digest(bad)
        with pytest.raises(errors.NonIJsonValueError):
            digest.canonical_bytes(bad)
    # the I-JSON boundary itself is fine
    for ok in (2**53 - 1, -(2**53 - 1), 0, -1):
        assert digest.compute_digest({"x": ok}).value
    assert issubclass(errors.NonIJsonValueError, errors.TracerSchemaError)
    # a non-I-JSON value is also reported by verify_digest, never treated as "no match"
    good = digest.compute_digest({"x": 1})
    with pytest.raises(errors.NonIJsonValueError):
        digest.verify_digest({"x": float("nan")}, good)


# ---------------------------------------------------------------------------- the digest domain
#
# ADR 0006 Decision 5 (revision 7), defined on the JSON document alone so that a verifier in any
# language needs no model: (a) and (b) remove `content_digest` and `schema_version` at the TOP
# LEVEL only; (c) removes every object member whose value is null at EVERY depth, including inside
# user `metadata`; null elements of arrays are kept (positions matter); lists, empty lists and
# empty objects are kept. Deeper exclusions are declared per entity (P-17; the `schema_version`
# of the entities embedded in a ConfigSnapshot, P1-12, which owns
# test_snapshot_digest_ignores_embedded_schema_version).


def test_digest_domain_removes_content_digest_and_schema_version_at_top_level_only() -> None:
    """Proposed name. A nested `schema_version` or `content_digest` is data: removing it by key
    name at every depth would also delete user metadata keys of that name. This is what makes the
    per-entity exclusion of embedded schema versions (ConfigSnapshot, P1-12) necessary."""
    digest = _digest()
    nested_digest = {"scheme": PLAIN, "value": HEX64}
    doc: dict[str, Any] = {
        "id": "snap_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
        "schema_version": "0.0.1",
        "content_digest": nested_digest,
        "evaluator": {"schema_version": "0.0.1", "content_digest": nested_digest, "name": "e"},
        "items": [{"schema_version": "0.0.1"}, {"content_digest": nested_digest}],
        "metadata": {"schema_version": "mine", "content_digest": "also mine", "k": 1},
    }
    domain = digest.digest_domain(doc)
    assert "schema_version" not in domain
    assert "content_digest" not in domain
    assert domain["evaluator"] == {
        "schema_version": "0.0.1",
        "content_digest": nested_digest,
        "name": "e",
    }
    assert domain["items"] == [{"schema_version": "0.0.1"}, {"content_digest": nested_digest}]
    assert domain["metadata"] == {"schema_version": "mine", "content_digest": "also mine", "k": 1}
    # so a change of a nested schema_version or content_digest moves the digest
    reference = digest.compute_digest(doc)
    changed = copy.deepcopy(doc)
    changed["evaluator"]["schema_version"] = "0.0.2"
    assert digest.compute_digest(changed) != reference
    changed = copy.deepcopy(doc)
    changed["metadata"]["schema_version"] = "yours"
    assert digest.compute_digest(changed) != reference
    # while the top-level pair never does
    top = {**doc, "schema_version": "9.9.9", "content_digest": {"scheme": PLAIN, "value": "f" * 64}}
    assert digest.compute_digest(top) == reference


def test_digest_domain_removes_nulls_at_every_object_depth_and_keeps_array_nulls() -> None:
    """Proposed name. pydantic's exclude_none is not a substitute: it keeps nulls inside dict
    values. Null object members go at every depth, including inside `metadata`; null array
    elements stay (positions matter); empty lists and empty objects stay."""
    digest = _digest()
    assert digest.digest_domain({"a": None, "b": 1}) == {"b": 1}
    assert digest.digest_domain({"a": {"b": None, "c": 1}}) == {"a": {"c": 1}}
    assert digest.digest_domain({"a": {"b": {"c": {"d": None}}}}) == {"a": {"b": {"c": {}}}}
    assert digest.digest_domain({"a": [{"b": None, "c": 1}, {"d": None}]}) == {"a": [{"c": 1}, {}]}
    assert digest.digest_domain({"a": [None, 1, None]}) == {"a": [None, 1, None]}
    assert digest.digest_domain({"a": [[None], [{"b": None}]]}) == {"a": [[None], [{}]]}
    assert digest.digest_domain({"a": [], "b": {}, "c": [[]], "d": {"e": {}}}) == {
        "a": [],
        "b": {},
        "c": [[]],
        "d": {"e": {}},
    }
    deep: dict[str, Any] = {"metadata": {"x": {"y": [{"z": None, "k": "v"}], "w": None}}}
    assert digest.digest_domain(deep) == {"metadata": {"x": {"y": [{"k": "v"}]}}}
    # the stated consequence: inside metadata a null member and an absent member are the same
    assert digest.compute_digest({"metadata": {"a": None}}) == digest.compute_digest(
        {"metadata": {}}
    )
    assert digest.compute_digest({"metadata": {"a": None, "b": 1}}) == digest.compute_digest(
        {"metadata": {"b": 1}}
    )
    # but a null array element is not an absent element
    assert digest.compute_digest({"a": [None]}) != digest.compute_digest({"a": []})
    assert digest.compute_digest({"a": [None, 1]}) != digest.compute_digest({"a": [1]})
    # falsy values that are not null are kept
    assert digest.digest_domain({"a": 0, "b": "", "c": False, "d": [], "e": {}}) == {
        "a": 0,
        "b": "",
        "c": False,
        "d": [],
        "e": {},
    }


def test_digest_domain_of_a_model_equals_that_of_its_json_dump() -> None:
    """Proposed name. A model is dumped with ``model_dump(mode="json", by_alias=True)`` and then
    treated as a mapping, so the domain can be computed by a verifier without the model, and the
    nulls inside `metadata` are removed too (exclude_none would keep them)."""
    digest = _digest()
    widget = widget_classes().VersionedWidget
    doc = versioned_doc(metadata={"a": None, "b": {"c": None, "d": 1}, "e": [None, {"f": None}]})
    loaded = widget.model_validate(doc)
    assert loaded.metadata == {"a": None, "b": {"c": None, "d": 1}, "e": [None, {"f": None}]}
    domain = digest.digest_domain(loaded)
    assert domain["metadata"] == {"b": {"d": 1}, "e": [None, {}]}
    assert domain == digest.digest_domain(loaded.model_dump(mode="json", by_alias=True))
    assert domain == digest.digest_domain(MappingProxyType(loaded.model_dump(mode="json")))
    # the stored digest was computed over the document with the nulls, and verifies on load
    assert loaded.content_digest == digest.compute_digest(domain)
    # the same document without the null members has the same digest
    plain = {k: v for k, v in doc.items() if k != "content_digest"}
    plain["metadata"] = {"b": {"d": 1}, "e": [None, {}]}
    assert digest.compute_digest(plain).value == doc["content_digest"]["value"]
    assert digest.digest_domain(loaded) is not loaded


def test_digest_domain_does_not_modify_its_input() -> None:
    """Proposed name. The domain is a new structure; the document it was computed from is left
    alone (the digest of a document must not change the document)."""
    digest = _digest()
    original: dict[str, Any] = {
        "schema_version": "0.0.1",
        "content_digest": {"scheme": PLAIN, "value": HEX64},
        "a": None,
        "b": {"c": None, "d": [None, {"e": None, "f": 1}]},
    }
    snapshot = copy.deepcopy(original)
    domain = digest.digest_domain(original)
    assert original == snapshot
    assert isinstance(domain, dict)
    digest.compute_digest(original)
    digest.canonical_bytes(original)
    assert original == snapshot


def test_canonical_bytes_are_the_jcs_of_the_domain() -> None:
    """Proposed name. ``canonical_bytes(obj)`` is RFC 8785 of ``digest_domain(obj)``, keys sorted by
    UTF-16 code unit; a digest is the hash of exactly those bytes."""
    digest = _digest()
    doc = {"schema_version": "0.0.1", "z": None, "b": [None, 1], "a": {"y": None, "x": "\u00e9"}}
    assert digest.canonical_bytes(doc) == '{"a":{"x":"\u00e9"},"b":[null,1]}'.encode()
    assert (
        digest.compute_digest(doc).value == hashlib.sha256(digest.canonical_bytes(doc)).hexdigest()
    )
    assert digest.canonical_bytes(ASTRAL_OBJ) == ASTRAL_CANONICAL.encode("utf-8")


# ------------------------------------------------------------------------- verify_digest contract


def test_verify_digest_returns_none_or_raises() -> None:
    """Proposed name. ADR 0006 Decision 4 (revision 7): ``verify_digest`` returns None on a match
    and raises otherwise (DigestMismatch, UnknownDigestScheme, DigestKeyUnavailable,
    NonIJsonValueError); it never returns a boolean, so a forgotten check cannot read as success.
    The key question is settled before any content is hashed."""
    digest = _digest()
    errors = _errors()
    key = GOLDEN_HMAC_KEY
    key_ref = digest.KeyRef(key_id="k1")
    plain = digest.compute_digest(GOLDEN_OBJ)
    keyed = digest.compute_digest(GOLDEN_OBJ, scheme=KEYED, key=key, key_ref=key_ref)

    def resolver(ref: Any) -> bytes | None:
        return key

    # matches: the value is exactly None (not True, not the digest)
    assert digest.verify_digest(GOLDEN_OBJ, plain) is None
    assert digest.verify_digest(GOLDEN_OBJ, plain, key_resolver=resolver) is None
    assert digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=resolver) is None
    assert digest.verify_digest(dict(reversed(list(GOLDEN_OBJ.items()))), plain) is None
    # every failure raises its own typed error and is a TracerSchemaError
    other = {**GOLDEN_OBJ, "n": 2}

    def wrong_key(ref: Any) -> bytes:
        return b"x" * 32

    forged = digest.ContentDigest.model_construct(scheme="x", value=HEX64, key_ref=None)
    failures: list[tuple[type[Exception], Any]] = [
        (errors.DigestMismatch, lambda: digest.verify_digest(other, plain)),
        (errors.DigestMismatch, lambda: digest.verify_digest(other, keyed, key_resolver=resolver)),
        (
            errors.DigestMismatch,
            lambda: digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=wrong_key),
        ),
        (errors.DigestKeyUnavailable, lambda: digest.verify_digest(GOLDEN_OBJ, keyed)),
        (
            errors.DigestKeyUnavailable,
            lambda: digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=lambda ref: None),
        ),
        (errors.NonIJsonValueError, lambda: digest.verify_digest({"x": float("nan")}, plain)),
        (
            errors.UnknownDigestScheme,
            lambda: digest.verify_digest(GOLDEN_OBJ, forged),
        ),
    ]
    for expected, call in failures:
        with pytest.raises(expected) as caught:
            call()
        assert type(caught.value) is expected
        assert isinstance(caught.value, errors.TracerSchemaError)
    # a resolver that raises ANY exception is DigestKeyUnavailable, never a pass-through and never
    # a success
    for exc in (KeyError("k1"), RuntimeError("vault down"), OSError("no route"), ValueError("bad")):

        def failing(ref: Any, exc: Exception = exc) -> bytes:
            raise exc

        with pytest.raises(errors.DigestKeyUnavailable):
            digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=failing)
    # the declared return type is None
    hints = digest.verify_digest.__annotations__
    assert hints.get("return") in (None, "None", type(None))


# a made-up marker, not a key: it stands for secret text that a key store puts in its own errors
HOSTILE_MARKER = "QA-FAKE-SECRET-7f3a91c2-not-a-real-key"


def _every_text_of(error: BaseException) -> list[str]:
    """Each way an error shows its message: str, repr, args, the one-line exception report and the
    full report without the chained causes."""
    import traceback

    return [
        str(error),
        repr(error),
        " ".join(str(a) for a in error.args),
        "".join(traceback.format_exception_only(type(error), error)),
        "".join(traceback.format_exception(type(error), error, error.__traceback__, chain=False)),
    ]


def test_digest_key_unavailable_never_echoes_what_a_resolver_says() -> None:
    """S-N91 (owner, auto-approved): ``DigestKeyUnavailable`` names the rule and the ``key_id`` and
    nothing else. A hostile key resolver whose exception text, arguments or returned value hold
    secret text (a made-up marker) must not see that text in the typed error: not in ``str``, not
    in ``repr``, not in ``args``, not in the exception report, not in the ``errors()`` of a
    pydantic ``ValidationError`` that a caller builds from it (``include_input=False``), and not
    through the class name of the resolver's exception. The original exception is chained
    (``__cause__`` is the very exception raised, so an operator can still find the reason) and the
    chain, not the message, is where the text lives. A resolver that returns the wrong type, or
    text instead of bytes, is not echoed either."""
    digest = _digest()
    errors = _errors()
    key_ref = digest.KeyRef(key_id="vault-key-1")
    keyed = digest.compute_digest(GOLDEN_OBJ, scheme=KEYED, key=GOLDEN_HMAC_KEY, key_ref=key_ref)

    class VaultDown(Exception):
        pass

    hostile: list[Exception] = [
        RuntimeError(f"vault token {HOSTILE_MARKER} rejected"),
        KeyError(HOSTILE_MARKER),
        VaultDown(HOSTILE_MARKER, {"password": HOSTILE_MARKER}),
        OSError(13, HOSTILE_MARKER),
        ValueError(HOSTILE_MARKER.encode()),
    ]
    for original in hostile:

        def failing(ref: Any, original: Exception = original) -> bytes:
            raise original

        with pytest.raises(errors.DigestKeyUnavailable) as caught:
            digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=failing)
        error = caught.value
        for text in _every_text_of(error):
            assert HOSTILE_MARKER not in text, (type(original).__name__, text)
            assert type(original).__name__ not in text, (type(original).__name__, text)
        assert "vault-key-1" not in str(error), "the message names the rule only, never the key_id"
        assert "vault-key-1" not in repr(error)
        assert error.__cause__ is original, "the original exception is chained (raise ... from)"
        assert HOSTILE_MARKER in str(original) or HOSTILE_MARKER.encode() in original.args
        for arg in error.args:
            assert not isinstance(arg, BaseException)
        assert isinstance(error, errors.TracerSchemaError)

        # a caller that turns the typed error into a validation error (the usual wrapping) keeps
        # the message clean in errors(), with and without the input
        from pydantic import BaseModel, field_validator

        class Wrapper(BaseModel):
            value: int

            @field_validator("value")
            @classmethod
            def _check(cls, value: int) -> int:
                try:
                    digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=failing)
                except errors.DigestKeyUnavailable as exc:
                    raise ValueError(str(exc)) from exc
                return value

        with pytest.raises(ValidationError) as wrapped:
            Wrapper.model_validate({"value": 1})
        for report in (
            str(wrapped.value.errors(include_input=False, include_context=False)),
            str(wrapped.value.errors(include_input=False)),
            str(wrapped.value.errors()),
            str(wrapped.value),
            repr(wrapped.value),
            wrapped.value.json(include_input=False),
        ):
            assert HOSTILE_MARKER not in report, report
            assert "vault-key-1" not in report, "the report names the rule only"

    for bad_return in (HOSTILE_MARKER, HOSTILE_MARKER.encode().decode(), 0, [HOSTILE_MARKER]):
        with pytest.raises(errors.DigestKeyUnavailable) as caught:
            digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=lambda ref, v=bad_return: v)
        for text in _every_text_of(caught.value):
            assert HOSTILE_MARKER not in text, text
        assert "vault-key-1" not in str(caught.value), "the message names the rule only"
    # no resolver at all: still names the rule, never a key
    with pytest.raises(errors.DigestKeyUnavailable) as caught:
        digest.verify_digest(GOLDEN_OBJ, keyed)
    for text in _every_text_of(caught.value):
        assert HOSTILE_MARKER not in text
    # the real key bytes are not echoed by a wrong key either
    with pytest.raises(errors.DigestMismatch) as mismatch:
        digest.verify_digest(
            GOLDEN_OBJ, keyed, key_resolver=lambda ref: b"another-key-00000000000000000000"
        )
    for text in (str(mismatch.value), repr(mismatch.value)):
        assert "another-key" not in text
        assert GOLDEN_HMAC_KEY.decode() not in text


def test_verify_digest_settles_the_key_and_the_scheme_before_hashing_the_content() -> None:
    """Proposed name. Content that cannot be digested (NaN) is reported as NonIJsonValueError only
    when the digest could otherwise be checked: an unavailable key or an unknown scheme is
    reported first, and the resolver is asked for the key exactly once, only for keyed digests."""
    digest = _digest()
    errors = _errors()
    bad_content = {"x": float("nan")}
    key_ref = digest.KeyRef(key_id="k1")
    keyed = digest.compute_digest(GOLDEN_OBJ, scheme=KEYED, key=GOLDEN_HMAC_KEY, key_ref=key_ref)
    plain = digest.compute_digest(GOLDEN_OBJ)
    with pytest.raises(errors.DigestKeyUnavailable):
        digest.verify_digest(bad_content, keyed)
    with pytest.raises(errors.DigestKeyUnavailable):
        digest.verify_digest(bad_content, keyed, key_resolver=lambda ref: None)
    forged = digest.ContentDigest.model_construct(scheme="none", value=HEX64, key_ref=None)
    with pytest.raises(errors.UnknownDigestScheme):
        digest.verify_digest(bad_content, forged)
    with pytest.raises(errors.NonIJsonValueError):
        digest.verify_digest(bad_content, plain)
    with pytest.raises(errors.NonIJsonValueError):
        digest.verify_digest(bad_content, keyed, key_resolver=lambda ref: GOLDEN_HMAC_KEY)
    calls: list[Any] = []

    def counting(ref: Any) -> bytes:
        calls.append(ref)
        return GOLDEN_HMAC_KEY

    digest.verify_digest(GOLDEN_OBJ, plain, key_resolver=counting)
    assert calls == [], "a plain digest never asks for a key"
    digest.verify_digest(GOLDEN_OBJ, keyed, key_resolver=counting)
    assert calls == [key_ref], "a keyed digest asks once, with the KeyRef"


# ------------------------------------------------------------------- closed sets in JSON Schema


def test_content_digest_scheme_is_an_enum_in_both_schema_modes() -> None:
    """Proposed name. ADR 0003 Decision 6 (revision 7): ``ContentDigest.scheme`` is a closed,
    registry-backed value set, emitted as ``{"type": "string", "enum": [...]}`` in the validation
    and the serialization schema, never as ``const``; the enum lists exactly the registered
    schemes."""
    digest = _digest()
    for mode in ("validation", "serialization"):
        schema = digest.ContentDigest.model_json_schema(mode=mode)
        node = resolve_schema(schema, schema["properties"]["scheme"])
        assert node["type"] == "string", mode
        assert sorted(node["enum"]) == sorted([PLAIN, KEYED]), mode
        assert len(node["enum"]) == len(set(node["enum"])) == 2
        assert "const" not in node, mode
        assert '"const"' not in json.dumps(schema), mode
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {"scheme", "value", "key_ref"}


def test_key_ref_key_id_is_an_opaque_string_stored_verbatim() -> None:
    """Proposed name. ADR 0001 I12 (revision 7): ``KeyRef.key_id`` is one of the three opaque
    ``*_id`` fields, a key manager's own name for the key, stored as given (it is not one of our
    typed IDs and has no ID pattern)."""
    digest = _digest()
    names = [
        "test-key-1",
        "arn:aws:kms:ap-south-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
        "projects/p/locations/l/keyRings/r/cryptoKeys/k",
        "run_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",  # looks like our ID; it is just a name here
    ]
    for name in names:
        assert digest.KeyRef(key_id=name).key_id == name
        dumped = digest.KeyRef(key_id=name).model_dump(mode="json")
        assert dumped["key_id"] == name
        assert digest.KeyRef.model_validate_json(json.dumps({"key_id": name})).key_id == name
    schema = digest.KeyRef.model_json_schema()
    node = resolve_schema(schema, schema["properties"]["key_id"])
    assert node["type"] == "string"
    assert not str(node.get("pattern", "")).startswith(("^run_", "^evl_")), "no typed-ID pattern"
    assert "key_id" in schema["required"]
    assert isinstance(digest.KeyRef(key_id="k1").key_id, str)
