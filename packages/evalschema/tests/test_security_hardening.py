# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 25: the P1-21 security audit decisions (findings C3a, C3b, m1, m2, m4, m6), tests first.

Contract: ``docs/phase1/api-surface-hardening.md`` (readings P1 to P14, ambiguities A1 to A10).
Decision log: ``tracer_eval_framework_decisions.md``, "P1-21 SECURITY AUDIT DELIVERED" and
"AUTO-APPROVED audit decisions".

- C3a: ``DigestKeyUnavailable`` never quotes the ``key_id`` (it names the rule only). This reverses
  the S-N91 reading that the message names the key; the old test
  ``test_digest_key_unavailable_never_echoes_what_a_resolver_says`` asserts the opposite in one
  line and is a named spec change request (it is not edited here).
- C3b: ``KeyRef.key_id``, ``KeyRef.key_version`` (and so ``DekWrap.kek_ref``) reject
  credential-shaped values with an error that quotes no part of the value; ``KeyRef`` hides its
  input and so does every model that embeds ``KeyRef``, ``ContentDigest`` or ``DekWrap``.
- m1: a keyed digest (``hmac-sha256-jcs/v1``) needs a key of at least 32 bytes.
- m2: key separation compares ``key_id`` only.
- m4: in models that do not hide their input, closed-set and pattern messages echo at most 64
  characters of a value, then an ellipsis.
- m6: ``env:`` names of ``ProviderConfigRef.config_ref`` run the high-entropy credential rule.

Everything runs against fakes. Credential-shaped samples are built from parts (``_support_b16``).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError, field_validator

from ._support import adapter as type_adapter
from ._support import api
from ._support_b2 import (
    new_id,
    provider_ref,
    raw_document,
    registered_class,
)
from ._support_b5 import KEK_REF, KEYED, dek_wrap_class, dek_wrap_doc, payload_doc, sealed_class
from ._support_b16 import (
    GOOD_KEY,
    HEX_40_LOWER,
    HEX_40_UPPER,
    KEY_SAMPLES,
    MARKER_HINT,
    MARKER_KEY_ID,
    MARKER_KEY_VERSION,
    ORDINARY_KEY_IDS,
    ORDINARY_KEY_VERSIONS,
    SHAPE_OF,
    embedding_models,
    exception_texts,
    keyed,
    leaks,
    renderings,
    sealed_doc,
)

PLAIN = "sha256-jcs/v1"
HOSTILE_MARKER = "QA-FAKE-SECRET-5d02b7e4-not-a-real-key"
KEY_REF_CREDENTIAL = "key_ref_credential"
CONFIG_REF_CREDENTIAL = "config_ref_credential"


def _key_ref_class() -> Any:
    return api("digest").KeyRef


def _digest_class() -> Any:
    return api("digest").ContentDigest


# ====================================================================================== C3a


def _hostile_resolvers() -> dict[str, Any]:
    """Resolvers that fail or answer badly and put the key id and a secret in what they say."""

    class VaultDown(Exception):
        pass

    def raises_runtime(ref: Any) -> bytes:
        raise RuntimeError(f"vault token {HOSTILE_MARKER} rejected for {ref.key_id}")

    def raises_key_error(ref: Any) -> bytes:
        raise KeyError(ref.key_id, HOSTILE_MARKER)

    def raises_custom(ref: Any) -> bytes:
        raise VaultDown(ref.key_id, {"password": HOSTILE_MARKER})

    def raises_os(ref: Any) -> bytes:
        raise OSError(13, f"{HOSTILE_MARKER} {ref.key_id}")

    return {
        "runtime-error": raises_runtime,
        "key-error": raises_key_error,
        "custom-error": raises_custom,
        "os-error": raises_os,
    }


def _verify_message(resolver: Any, key_id: str, **ref_fields: Any) -> tuple[Any, Any, Any]:
    document, digest = keyed(key_id, **ref_fields)
    with pytest.raises(api("errors").DigestKeyUnavailable) as caught:
        api("digest").verify_digest(document, digest, key_resolver=resolver)
    return caught.value, document, digest


def test_digest_key_unavailable_never_quotes_the_key_id_when_the_resolver_fails() -> None:
    """C3a (auto-approved, reverses S-N91's "the message names the key_id"): the message of
    ``DigestKeyUnavailable`` names the rule only. A hostile resolver whose exception text, arguments
    or class hold the key id and secret text must not make either appear in ``str``, ``repr``,
    ``args``, the exception report or the ``errors()`` of a ``ValidationError`` that a caller
    builds from the typed error. Reading P1: the text is a function of the failure kind alone, so
    two different key ids (and versions, and hints) give the very same message."""
    errors = api("errors")
    for label, resolver in _hostile_resolvers().items():
        first, _doc, _digest = _verify_message(resolver, MARKER_KEY_ID)
        other, _doc2, _digest2 = _verify_message(
            resolver,
            "qa-other-key-id-91b0",
            key_version=MARKER_KEY_VERSION,
            provider_hint=MARKER_HINT,
        )
        for error in (first, other):
            assert isinstance(error, errors.DigestKeyUnavailable), label
            assert isinstance(error, errors.TracerSchemaError), label
            for text in exception_texts(error):
                for secret in (MARKER_KEY_ID, "qa-other-key-id-91b0", HOSTILE_MARKER):
                    assert secret not in text, (label, text)
                assert MARKER_KEY_VERSION not in text, (label, text)
                assert MARKER_HINT not in text, (label, text)
                assert type(error.__cause__).__name__ not in text, (label, text)
            assert "no key available" in str(error), "the S-N91 opening words are kept"
        assert str(first) == str(other), (label, "the message depends on the failure kind only")


def test_digest_key_unavailable_for_a_resolver_answer_that_is_not_a_key_quotes_no_key_id() -> None:
    """C3a: the other ``DigestKeyUnavailable`` path of ``_resolve_key``, a resolver that returns
    ``None`` or something that is not ``bytes``, also names no key id, and the message is the same
    for every key id. Reading P2: the opening words ``no key available`` are kept."""
    errors = api("errors")
    answers: dict[str, Any] = {
        "none": None,
        "text": f"{HOSTILE_MARKER}",
        "bytearray": bytearray(GOOD_KEY),
        "int": 42,
        "list": [HOSTILE_MARKER],
    }
    for label, answer in answers.items():

        def resolver(ref: Any, answer: Any = answer) -> Any:
            return answer

        first, _d, _g = _verify_message(resolver, MARKER_KEY_ID)
        other, _d2, _g2 = _verify_message(resolver, "qa-other-key-id-91b0", key_version="9")
        assert isinstance(first, errors.DigestKeyUnavailable), label
        for error in (first, other):
            for text in exception_texts(error):
                assert MARKER_KEY_ID not in text, (label, text)
                assert "qa-other-key-id-91b0" not in text, (label, text)
                assert HOSTILE_MARKER not in text, (label, text)
            assert "no key available" in str(error), label
        assert str(first) == str(other), label


def test_digest_key_unavailable_without_a_resolver_quotes_no_key_id() -> None:
    """C3a, guard: a keyed digest verified without a resolver already names no key id; the new
    rule must keep it so (the message names the scheme, which is a registered name)."""
    errors = api("errors")
    document, digest = keyed(MARKER_KEY_ID, key_version=MARKER_KEY_VERSION)
    with pytest.raises(errors.DigestKeyUnavailable) as caught:
        api("digest").verify_digest(document, digest)
    for text in exception_texts(caught.value):
        assert MARKER_KEY_ID not in text
        assert MARKER_KEY_VERSION not in text
    assert KEYED in str(caught.value)


def test_digest_key_unavailable_keeps_the_cause_chain_and_none_of_its_text() -> None:
    """C3a: the original exception stays reachable as ``__cause__`` (an operator can find the
    reason there) but nothing of it, text, arguments or class name, is in the message. The cause
    holds the key id and the secret (the resolver put them there); the typed error does not."""
    for label, resolver in _hostile_resolvers().items():
        error, _doc, _digest = _verify_message(resolver, MARKER_KEY_ID)
        cause = error.__cause__
        assert isinstance(cause, BaseException), label
        assert HOSTILE_MARKER in " ".join(str(a) for a in cause.args) + str(cause), label
        assert str(cause) not in str(error), label
        assert type(cause).__name__ not in str(error), label
        for arg in error.args:
            assert not isinstance(arg, BaseException), label
        assert error.__suppress_context__ is True, "raised with 'from', not an implicit context"


def test_digest_key_unavailable_wrapped_in_a_validation_error_shows_no_key_id() -> None:
    """C3a: a caller that turns the typed error into a ``ValidationError`` (the usual wrapping)
    keeps the key id and the secret out of every rendering that can leave the input out, and
    ``errors()`` itself, which carries the input only when the caller passes one."""
    errors = api("errors")
    document, digest = keyed(MARKER_KEY_ID, key_version=MARKER_KEY_VERSION)
    for label, resolver in _hostile_resolvers().items():

        class Wrapper(BaseModel):
            value: int

            @field_validator("value")
            @classmethod
            def _check(cls, value: int, resolver: Any = resolver) -> int:
                try:
                    api("digest").verify_digest(document, digest, key_resolver=resolver)
                except errors.DigestKeyUnavailable as exc:
                    raise ValueError(str(exc)) from exc
                return value

        with pytest.raises(ValidationError) as wrapped:
            Wrapper.model_validate({"value": 1})
        for text in (*renderings(wrapped.value), str(wrapped.value.errors())):
            assert MARKER_KEY_ID not in text, (label, text)
            assert MARKER_KEY_VERSION not in text, (label, text)
            assert HOSTILE_MARKER not in text, (label, text)


# ====================================================================================== C3b


def _targets(field: str, value: str) -> list[tuple[str, Any, Any, tuple[str, ...]]]:
    """``(label, model class, document, expected error location)`` for every place a ``KeyRef``
    is validated: the model itself, a content digest, a key wrap, a sealed payload (both of its
    key references)."""
    key_ref: dict[str, Any] = {"key_id": "vault-key-1"}
    key_ref[field] = value
    ref_with_id = {"key_id": "vault-key-1", "key_version": "1"}
    ref_with_id[field] = value
    digest_ref: dict[str, Any] = {"key_id": "hmac-other-1"}
    digest_ref[field] = value
    return [
        ("KeyRef", _key_ref_class(), dict(key_ref), (field,)),
        (
            "ContentDigest",
            _digest_class(),
            {"scheme": KEYED, "value": "0" * 64, "key_ref": dict(key_ref)},
            ("key_ref", field),
        ),
        (
            "DekWrap",
            dek_wrap_class(),
            dek_wrap_doc(kek_ref=dict(ref_with_id)),
            ("kek_ref", field),
        ),
        (
            "SealedPayload.dek_wrap",
            sealed_class(),
            sealed_doc(kek_ref=dict(ref_with_id)),
            ("dek_wrap", "kek_ref", field),
        ),
        (
            "SealedPayload.plaintext_digest",
            sealed_class(),
            sealed_doc(digest_key_ref=digest_ref),
            ("plaintext_digest", "key_ref", field),
        ),
    ]


def _reject(model: Any, doc: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(doc)
    return caught.value


@pytest.mark.parametrize("label", sorted(KEY_SAMPLES))
@pytest.mark.parametrize("field", ["key_id", "key_version"])
def test_keyref_rejects_credential_shaped_values_at_the_field_without_quoting_them(
    label: str, field: str
) -> None:
    """C3b: a credential-shaped ``key_id`` or ``key_version`` is a ``ValidationError`` at that
    field, wherever the ``KeyRef`` sits (itself, a content digest, a key wrap, a sealed payload's
    two references). Reading P3: exactly one error, of type ``key_ref_credential``, at the
    location of the field; no part of the value (no 8-character window of it) is in ``str``,
    ``repr``, ``errors(include_input=False)`` (with and without the context) or
    ``json(include_input=False)``; with ``hide_input_in_errors`` active the plain ``str`` is one
    of those renderings. The samples: known prefixes (sk-, sk_live_, ghp_, AKIA, AIza, JWT, PEM,
    ...), a mixed-case run of 32 characters or more in any one segment (segments split at ``/`` and
    ``:``, A5), and ``key=value``. 40 hex digits are NOT rejected (A1)."""
    value = KEY_SAMPLES[label]
    for where, model, doc, loc in _targets(field, value):
        error = _reject(model, doc)
        found = error.errors(include_input=False)
        assert len(found) == 1, (where, found)
        assert found[0]["loc"] == loc, (where, found[0]["loc"])
        assert found[0]["type"] == KEY_REF_CREDENTIAL, (where, found[0]["type"])
        assert "credential" in found[0]["msg"], (where, found[0]["msg"])
        quoted = leaks(value, renderings(error))
        assert not quoted, (where, label, quoted)
        assert "input_value" not in str(error), (where, "plain str must not carry the input")
        assert "input_value" not in repr(error), where


@pytest.mark.parametrize("field", ["key_id", "key_version"])
def test_keyref_credential_error_text_depends_on_the_rule_only(field: str) -> None:
    """C3b: two different values of the same shape give the very same error (type, location,
    message, context): the text is a function of the rule and the field, never of the value."""
    by_shape: dict[str, list[str]] = {}
    for label, shape in SHAPE_OF.items():
        by_shape.setdefault(shape, []).append(label)
    assert set(by_shape) == {"known_prefix", "high_entropy", "key_value"}
    for shape, labels in by_shape.items():
        reports = []
        for label in labels:
            error = _reject(_key_ref_class(), {"key_id": "vault-key-1", field: KEY_SAMPLES[label]})
            reports.append(error.errors(include_input=False))
        assert all(report == reports[0] for report in reports), shape


@pytest.mark.parametrize("field", ["key_id", "key_version"])
def test_keyref_credential_rule_through_the_json_entry_point(field: str) -> None:
    """C3b: ``model_validate_json`` and a ``TypeAdapter`` apply the same rule and show the same
    error as ``model_validate``."""
    adapter = TypeAdapter(_key_ref_class())
    for label, value in KEY_SAMPLES.items():
        document = {"key_id": "vault-key-1", field: value}
        text = json.dumps(document)
        calls: list[Callable[[], Any]] = [
            functools.partial(_key_ref_class().model_validate_json, text),
            functools.partial(adapter.validate_json, text),
            functools.partial(adapter.validate_python, document),
        ]
        for call in calls:
            with pytest.raises(ValidationError) as caught:
                call()
            found = caught.value.errors(include_input=False)
            assert [(e["loc"], e["type"]) for e in found] == [((field,), KEY_REF_CREDENTIAL)], label
            assert not leaks(value, renderings(caught.value)), label


def test_keyref_rejects_a_credential_in_either_field_and_reports_both() -> None:
    """C3b: when both ``key_id`` and ``key_version`` are credential-shaped, both fields are
    reported (pydantic collects the errors of a model), and neither value is quoted."""
    first, second = KEY_SAMPLES["openai"], KEY_SAMPLES["mixed-case-36"]
    error = _reject(_key_ref_class(), {"key_id": first, "key_version": second})
    found = error.errors(include_input=False)
    assert sorted(e["loc"] for e in found) == [("key_id",), ("key_version",)]
    assert not leaks(first, renderings(error))
    assert not leaks(second, renderings(error))


def test_keyref_accepts_ordinary_key_ids_and_versions_stored_as_given() -> None:
    """C3b: ordinary key names pass (vault names, KMS aliases and ARNs, UUIDs, 32 hex digits, a
    31-character mixed-case name, a name with no digit, non-ASCII text), and are stored verbatim
    (ADR 0001 I12: an opaque string; no trimming, no normalization)."""
    key_ref = _key_ref_class()
    for key_id in ORDINARY_KEY_IDS:
        model = key_ref(key_id=key_id)
        assert model.key_id == key_id
        assert model.key_version is None
        assert key_ref.model_validate_json(json.dumps({"key_id": key_id})).key_id == key_id
    for version in ORDINARY_KEY_VERSIONS:
        model = key_ref(key_id="vault-key-1", key_version=version)
        assert model.key_version == version
    full = key_ref(key_id="kms/prod/alias-1", key_version="1", provider_hint="aws-kms")
    assert full.model_dump(mode="json") == {
        "key_id": "kms/prod/alias-1",
        "key_version": "1",
        "provider_hint": "aws-kms",
    }


def test_keyref_accepts_a_40_hex_key_id_and_version() -> None:
    """A1 (decided): 40 hex digits (an OpenPGP v4 fingerprint, a SHA-1 thumbprint) are an ordinary
    key name and stay accepted, in either letter case, as ``key_id`` and as ``key_version``."""
    key_ref = _key_ref_class()
    for value in (HEX_40_LOWER, HEX_40_UPPER):
        assert key_ref(key_id=value).key_id == value
        assert key_ref(key_id="k", key_version=value).key_version == value


def test_key_id_entropy_rule_runs_per_segment_split_at_slash_and_colon() -> None:
    """A5 (decided): the high-entropy run rule is applied to each segment of a key id split at
    ``/`` and ``:``. A GCP KMS resource name and a URN-like id, whose whole text is long, mixed case
    and holds digits, are accepted, and so are segments of 31 characters joined by separators; a
    credential-shaped segment inside such a name is still rejected, and so is a known prefix in any
    segment. Both fields, nothing quoted."""
    key_ref = _key_ref_class()
    gcp = "projects/my-proj-123/locations/global/keyRings/ring1/cryptoKeys/key1"
    urn = "urn:tracer:kms:key:Prod-Ring-7:alias/Main-Key-2026"
    for value in (gcp, urn):
        assert key_ref(key_id=value).key_id == value
        assert key_ref(key_id="k", key_version=value).key_version == value
    for label in ("credential-segment-in-gcp-path", "credential-segment-after-colon"):
        value = KEY_SAMPLES[label]
        for field in ("key_id", "key_version"):
            error = _reject(key_ref, {"key_id": "vault-key-1", field: value})
            assert [(e["loc"], e["type"]) for e in error.errors(include_input=False)] == [
                ((field,), KEY_REF_CREDENTIAL)
            ]
            assert not leaks(value, renderings(error)), label


def test_keyref_still_requires_a_non_empty_key_id_and_version() -> None:
    """C3b, guard: the rule adds to ``min_length=1``; it does not replace it, and the other
    existing shape rules stay (strict ``str``, ``extra=forbid``, frozen)."""
    key_ref = _key_ref_class()
    for bad in (
        {"key_id": ""},
        {"key_id": "k", "key_version": ""},
        {"key_id": 7},
        {"key_id": None},
        {},
        {"key_id": "k", "api_key": "x"},
    ):
        with pytest.raises(ValidationError):
            key_ref.model_validate(bad)
    with pytest.raises(ValidationError):
        key_ref(key_id="k").key_id = "other"  # frozen


def test_keyref_hides_its_input_in_every_validation_error() -> None:
    """C3b: ``KeyRef`` sets ``hide_input_in_errors=True`` (ADR 0007 Decision 8 (a)) and is built on
    the hiding base, so the shared validators of its fields quote nothing. A distinctive rejected
    value of a wrong type shows in no rendering, plain ``str`` included."""
    from tracer_evalschema._hiding import HidingModel

    key_ref = _key_ref_class()
    assert key_ref.model_config.get("hide_input_in_errors") is True
    assert issubclass(key_ref, HidingModel)
    leak = "qa-leak-marker-8841"
    for doc in (
        {"key_id": {"leak": leak}},
        {"key_id": "k", "key_version": [leak]},
        {"key_id": "k", "provider_hint": f"{leak} with spaces"},
        {"key_id": "k", "extra_field": leak},
    ):
        error = _reject(key_ref, doc)
        for text in (*renderings(error), str(error)):
            assert leak not in text, (doc, text)
        assert "input_value" not in str(error)


def test_content_digest_hides_its_input_and_names_no_value() -> None:
    """C3b: ``ContentDigest`` embeds ``KeyRef`` and so hides its input too. A bad digest value or
    an unregistered scheme is rejected with a message that quotes neither (the shared validators
    read the hiding context), in every rendering."""
    digest_class = _digest_class()
    assert digest_class.model_config.get("hide_input_in_errors") is True
    leak = "qa-leak-marker-1f3c"
    error = _reject(digest_class, {"scheme": PLAIN, "value": leak})
    for text in (*renderings(error), str(error)):
        assert leak not in text
    assert "input_value" not in str(error)
    with pytest.raises(api("errors").UnknownDigestScheme) as unknown:
        digest_class.model_validate({"scheme": leak, "value": "0" * 64})
    for text in exception_texts(unknown.value):
        assert leak not in text
    assert "registered" in str(unknown.value), "the registered names are still listed"


def test_versioned_ref_hides_a_bad_digest_value() -> None:
    """C3b: a model that only embeds ``ContentDigest`` (here ``VersionedRef``) hides its input, so
    the digest's rejected text is not quoted through it either."""
    leak = "qa-leak-marker-77b2"
    version_ref = registered_class("VersionedRef")
    doc = {
        "id": new_id("run"),
        "version": 1,
        "content_digest": {"scheme": PLAIN, "value": leak},
    }
    error = _reject(version_ref, doc)
    for text in (*renderings(error), str(error)):
        assert leak not in text


HIDING_SEEDS = ("KeyRef", "ContentDigest", "DekWrap")


def test_every_model_that_embeds_a_keyref_a_content_digest_or_a_dek_wrap_hides_its_input() -> None:
    """C3b (the closure rule of ADR 0007 Decision 8 (a), (b), extended): the registry walk finds
    every model that is, or embeds at any depth, ``KeyRef``, ``ContentDigest`` or ``DekWrap``
    (fields inherited from a base count), and each sets ``hide_input_in_errors=True``, because the
    setting of the outermost model governs. The existing closure test seeds are a named change
    request (add ``KeyRef`` and ``ContentDigest``); this test walks on its own so the rule is
    pinned either way."""
    by_name, names = embedding_models(HIDING_SEEDS)
    assert {"KeyRef", "ContentDigest", "DekWrap", "SealedPayload"} <= set(names)
    assert len(names) >= 25, names  # the walk reaches the entities and references (31 today)
    unprotected = [
        name for name in names if by_name[name].model_config.get("hide_input_in_errors") is not True
    ]
    assert not unprotected, f"models that embed a key or digest model but show input: {unprotected}"


def test_models_that_embed_a_content_digest_use_the_hiding_validation_context() -> None:
    """C3b: setting the flag is not enough for the shared validators; a model must also set the
    hiding context (``HidingModel``), or its own fields quote values next to the hidden input.
    Every concrete model of the walk that is not an abstract base is a ``HidingModel`` subclass or
    inherits the context from one (reading P2b: ``VersionedEntity`` and ``EntityBase`` are
    abstract and exempt; every other model of the walk subclasses ``HidingModel``)."""
    from tracer_evalschema._hiding import HidingModel

    by_name, names = embedding_models(HIDING_SEEDS)
    # abstract bases have no registered ID or document of their own
    abstract = {"VersionedEntity", "EntityBase"}
    for name in names:
        if name in abstract:
            continue
        model = by_name[name]
        assert issubclass(model, HidingModel), f"{name} does not set the hiding context"


# ====================================================================================== m1


WEAK_MARKER = b"qa-weak-key-marker-"
WEAK_KEYS: dict[str, bytes] = {
    "empty": b"",
    "one-byte": b"k",
    "sixteen": (WEAK_MARKER + b"x" * 16)[:16],
    "thirty-one": (WEAK_MARKER + b"x" * 31)[:31],
}
SUBJECT = {"id": "subject-1", "text": "hello"}


def _assert_no_key_material(error: BaseException, key: bytes) -> None:
    for text in exception_texts(error):
        assert repr(key) not in text
        if len(key) >= 8:  # shorter keys are too short for a substring check to mean anything
            assert key.hex() not in text
            assert key.decode("ascii") not in text
        assert "qa-weak-key" not in text


@pytest.mark.parametrize("label", sorted(WEAK_KEYS))
def test_compute_digest_rejects_a_keyed_key_shorter_than_32_bytes(label: str) -> None:
    """m1 (auto-approved): ``compute_digest`` with ``hmac-sha256-jcs/v1`` needs a key of at least 32
    bytes. Empty, 1, 16 and 31 bytes raise ``ValueError``; reading P4: the message names the rule
    (``at least 32 bytes``) and the scheme, and holds no key material (not the bytes, not the hex,
    not the text)."""
    digest = api("digest")
    key = WEAK_KEYS[label]
    key_ref = digest.KeyRef(key_id="hmac-test-1")
    with pytest.raises(ValueError, match="at least 32 bytes") as caught:
        digest.compute_digest(SUBJECT, scheme=KEYED, key=key, key_ref=key_ref)
    assert not isinstance(caught.value, ValidationError), "a plain ValueError, not a model error"
    message = str(caught.value)
    assert "at least 32 bytes" in message
    assert KEYED in message
    assert isinstance(caught.value, api("errors").DigestKeyUnavailable), "A4: both classes"
    _assert_no_key_material(caught.value, key)


def test_compute_digest_accepts_keys_of_32_bytes_and_more() -> None:
    """m1: exactly 32 bytes is accepted (the boundary), and so are longer keys; the keyed value is
    deterministic, differs from the plain digest and between keys."""
    digest = api("digest")
    key_ref = digest.KeyRef(key_id="hmac-test-1")
    values = set()
    for length in (32, 33, 64, 4096):
        key = bytes(range(256)) * 16
        result = digest.compute_digest(SUBJECT, scheme=KEYED, key=key[:length], key_ref=key_ref)
        assert result.scheme == KEYED
        assert result.key_ref == key_ref
        again = digest.compute_digest(SUBJECT, scheme=KEYED, key=key[:length], key_ref=key_ref)
        assert result == again
        values.add(result.value)
        digest.verify_digest(SUBJECT, result, key_resolver=lambda ref, k=key[:length]: k)
    assert len(values) == 4
    assert digest.compute_digest(SUBJECT).value not in values


def test_compute_digest_key_rule_does_not_touch_the_plain_scheme_or_the_type_check() -> None:
    """m1, guard: the plain scheme takes no key and is unchanged; a key that is not ``bytes`` is
    still a ``TypeError`` (``bytearray`` of 32 bytes included), and a missing key or key ref is
    still the ``ValueError`` of the existing rule."""
    digest = api("digest")
    key_ref = digest.KeyRef(key_id="hmac-test-1")
    assert digest.compute_digest(SUBJECT).scheme == PLAIN
    with pytest.raises(TypeError):
        digest.compute_digest(SUBJECT, scheme=KEYED, key=bytearray(GOOD_KEY), key_ref=key_ref)
    with pytest.raises(TypeError):
        digest.compute_digest(SUBJECT, scheme=KEYED, key="x" * 40, key_ref=key_ref)
    with pytest.raises(ValueError, match="needs both key and key_ref"):
        digest.compute_digest(SUBJECT, scheme=KEYED, key=None, key_ref=key_ref)
    with pytest.raises(ValueError, match="needs both key and key_ref"):
        digest.compute_digest(SUBJECT, scheme=KEYED, key=GOOD_KEY, key_ref=None)
    with pytest.raises(ValueError, match="takes no key"):
        digest.compute_digest(SUBJECT, scheme=PLAIN, key=GOOD_KEY)


@pytest.mark.parametrize("label", sorted(WEAK_KEYS))
def test_verify_digest_rejects_a_resolved_key_shorter_than_32_bytes(label: str) -> None:
    """m1: ``verify_digest`` applies the same rule to the key its resolver returns. A weak key is a
    ``ValueError`` (reading P5: raised before any content is hashed, so it is never a
    ``DigestMismatch``), the resolver is asked once, and the message names the rule and holds
    neither key material nor the key id (C3a)."""
    digest = api("digest")
    document, good = keyed(MARKER_KEY_ID, subject=dict(SUBJECT))
    key = WEAK_KEYS[label]
    asked: list[Any] = []

    def resolver(ref: Any) -> bytes:
        asked.append(ref)
        return key

    with pytest.raises(ValueError, match="at least 32 bytes") as caught:
        digest.verify_digest(document, good, key_resolver=resolver)
    assert not isinstance(caught.value, api("errors").DigestMismatch)
    assert isinstance(caught.value, api("errors").DigestKeyUnavailable), "A4: both classes"
    assert len(asked) == 1
    message = str(caught.value)
    assert "at least 32 bytes" in message
    _assert_no_key_material(caught.value, key)
    for text in exception_texts(caught.value):
        assert MARKER_KEY_ID not in text


def test_verify_digest_accepts_a_resolved_key_of_exactly_32_bytes() -> None:
    """m1: the boundary on the verify side: a 32-byte key verifies; a wrong 32-byte key is a
    ``DigestMismatch`` (a key of acceptable length is never the cause of a ``ValueError``)."""
    digest = api("digest")
    document, good = keyed(MARKER_KEY_ID, subject=dict(SUBJECT))
    digest.verify_digest(document, good, key_resolver=lambda ref: GOOD_KEY)
    with pytest.raises(api("errors").DigestMismatch):
        digest.verify_digest(document, good, key_resolver=lambda ref: b"z" * 32)


# ====================================================================================== m2


def _sealed_with_keys(
    kek: dict[str, Any], digest_key: dict[str, Any]
) -> tuple[Any, ValidationError | None]:
    try:
        doc = sealed_doc(kek_ref=kek, digest_key_ref=digest_key)
        return sealed_class().model_validate(doc), None
    except ValidationError as error:
        return None, error


@pytest.mark.parametrize(
    ("kek_version", "digest_version"),
    [("1", None), (None, "1"), ("1", "2"), ("1", "1"), (None, None), ("7", "kek-ish")],
)
def test_key_separation_compares_key_id_only(
    kek_version: str | None, digest_version: str | None
) -> None:
    """m2 (auto-approved, stricter): the digest key must not be the KEK; the comparison is on
    ``key_id`` alone, so the same ``key_id`` is rejected whatever the versions are (a version of
    ``None`` against ``"1"`` included, which the old rule let through). Reading P6: one error at
    ``plaintext_digest.key_ref`` of type ``key_separation``, and no key id in it."""
    kek = {"key_id": "shared-key-1", "key_version": kek_version}
    digest_key = {"key_id": "shared-key-1", "key_version": digest_version}
    kek = {k: v for k, v in kek.items() if v is not None}
    digest_key = {k: v for k, v in digest_key.items() if v is not None}
    _model, error = _sealed_with_keys(kek, digest_key)
    assert error is not None, "same key_id, so the digest key is the KEK"
    found = error.errors(include_input=False)
    assert [(e["loc"], e["type"]) for e in found] == [
        (("plaintext_digest", "key_ref"), "key_separation")
    ]
    for text in renderings(error):
        assert "shared-key-1" not in text


@pytest.mark.parametrize(
    ("kek_id", "digest_id"),
    [
        ("kek-1", "hmac-1"),
        ("kek-1", "KEK-1"),  # compared verbatim: case matters
        ("kek-1", "kek-1 "),  # and so does a trailing space (not trimmed)
        ("caf" + chr(0xE9), "cafe" + chr(0x301)),  # NFC against NFD (S-10): different keys
    ],
)
@pytest.mark.parametrize("version", [None, "1"])
def test_key_separation_accepts_different_key_ids_with_any_versions(
    kek_id: str, digest_id: str, version: str | None
) -> None:
    """m2: different ``key_id`` strings are different keys (compared verbatim, S-N85 and S-10),
    whatever the versions, equal or not."""
    kek = {"key_id": kek_id} | ({"key_version": version} if version else {})
    digest_key = {"key_id": digest_id} | ({"key_version": version} if version else {})
    model, error = _sealed_with_keys(kek, digest_key)
    assert error is None, error
    assert model.plaintext_digest.key_ref.key_id == digest_id


def test_key_separation_message_names_the_rule_not_the_versions() -> None:
    """m2, reading P7: the message says the comparison is on ``key_id`` (so a reader does not
    think a version change separates the keys) and no longer says the versions are compared."""
    _model, error = _sealed_with_keys(
        {"key_id": "shared-key-1", "key_version": "1"}, {"key_id": "shared-key-1"}
    )
    assert error is not None
    message = error.errors(include_input=False)[0]["msg"]
    assert "key_id" in message
    assert "same key_id and key_version" not in message


# ====================================================================================== m4

LIMIT = 64
TAIL = "~tail-marker~"


def _forms(value: str) -> set[str]:
    """The two spellings reading P8 allows for a truncated echo: the marker outside or inside the
    quotes."""
    head = value[:LIMIT]
    return {repr(head) + "...", repr(head + "...")}


def _pattern_adapter() -> TypeAdapter[str]:
    from tracer_evalschema._constraints import FullMatch

    return TypeAdapter(
        Annotated[str, FullMatch("^[a-z]+$", "qa_pattern", "{value} does not match {pattern}")]
    )


def _closed_adapter() -> TypeAdapter[str]:
    from tracer_evalschema._constraints import ClosedSet

    return TypeAdapter(Annotated[str, ClosedSet(("alpha", "beta"))])


def _first_error(adapter: TypeAdapter[Any], value: Any) -> dict[str, Any]:
    with pytest.raises(ValidationError) as caught:
        adapter.validate_python(value)
    return dict(caught.value.errors(include_input=False)[0])


def test_short_values_are_echoed_unchanged() -> None:
    """m4: a value of up to 64 characters is quoted exactly as today (the closed-set and pattern
    messages, outside a model that hides its input). 64 characters is the last unchanged length."""
    for value in ("Q", "hello world", "A" * 63, "A" * 64):
        error = _first_error(_pattern_adapter(), value)
        assert error["msg"] == f"{value!r} does not match ^[a-z]+$"
        assert error["ctx"]["value"] == repr(value)
        closed = _first_error(_closed_adapter(), value)
        assert closed["msg"] == f"{value!r} is not one of ('alpha', 'beta')"
        assert closed["ctx"]["value"] == repr(value)
    with pytest.raises(ValidationError) as caught:
        registered_class("ModelRef").model_validate({"provider": "a b", "model": "m"})
    assert caught.value.errors(include_input=False)[0]["msg"] == (
        f"'a b' is not a machine key ({api('text').MACHINE_KEY_PATTERN})"
    )


@pytest.mark.parametrize("length", [65, 66, 100, 5000])
def test_longer_values_are_cut_at_64_characters_with_an_ellipsis(length: int) -> None:
    """m4 (auto-approved): from 65 characters on, the echoed value is its first 64 characters
    followed by an ellipsis (reading P8: three ASCII dots; outside or inside the quotes), in the
    message and in the context, for the pattern and the closed-set markers. Nothing after the 64th
    character is quoted."""
    value = ("ab" * 40)[:LIMIT] + (TAIL + "z" * length)[: length - LIMIT]
    assert len(value) == length
    for adapter in (_pattern_adapter(), _closed_adapter()):
        error = _first_error(adapter, value)
        assert any(form in error["msg"] for form in _forms(value)), error["msg"]
        assert error["ctx"]["value"] in _forms(value), error["ctx"]["value"]
        assert "tail-marker" not in error["msg"]
        assert "tail-marker" not in error["ctx"]["value"]
        assert len(error["msg"]) < LIMIT + 3 + 2 + 100, len(error["msg"])


def test_the_cut_counts_characters_not_bytes_or_graphemes() -> None:
    """m4, reading P9: the 64 is a count of code points, so 64 Devanagari letters are kept (192
    UTF-8 bytes) and the 65th is cut. The text is built with ``chr``."""
    value = "".join(chr(0x0915 + i) for i in range(100))
    assert len(value) == 100
    error = _first_error(_pattern_adapter(), value)
    assert any(form in error["msg"] for form in _forms(value))
    assert value[LIMIT] not in error["msg"]
    assert value[LIMIT - 1] in error["msg"]


def test_a_five_megabyte_invalid_value_gives_a_bounded_message_everywhere() -> None:
    """m4 (the audit's observation: a 5 MB invalid value gave a 5,000,212-character error): through
    a real model that does not hide its input (``ModelRef``), the Python and the JSON entry point,
    ``str``, ``repr``, ``errors(include_input=False)`` and ``json(include_input=False)`` are all
    bounded. The bound (reading P10): under 2,000 characters for one error."""
    model_ref = registered_class("ModelRef")
    assert model_ref.model_config.get("hide_input_in_errors") is not True
    huge = "q" * LIMIT + TAIL + "q" * 5_000_000
    calls: list[Callable[[], Any]] = [
        lambda: model_ref.model_validate({"provider": huge, "model": "m"}),
        lambda: model_ref.model_validate_json(json.dumps({"provider": huge, "model": "m"})),
    ]
    for call in calls:
        with pytest.raises(ValidationError) as caught:
            call()
        error = caught.value
        for text in (
            str(error),
            repr(error),
            repr(error.errors(include_input=False)),
            error.json(include_input=False),
        ):
            assert len(text) < 2000, len(text)
            assert "tail-marker" not in text
        message = error.errors(include_input=False)[0]["msg"]
        assert any(form in message for form in _forms(huge)), message[:200]


def test_five_megabyte_values_are_bounded_for_the_markers_directly() -> None:
    """m4: the same bound for the pattern marker and the closed-set marker used on their own."""
    huge = "q" * LIMIT + TAIL + "q" * 5_000_000
    for adapter in (_pattern_adapter(), _closed_adapter()):
        error = _first_error(adapter, huge)
        assert len(error["msg"]) < 2000
        assert len(repr(error["ctx"])) < 2000


@pytest.mark.parametrize(
    "site",
    [
        "typed_id",
        "language_tag",
        "utc_instant",
        "decimal_string",
    ],
)
def test_other_value_echoing_messages_are_cut_the_same_way(site: str) -> None:
    """m4, reading P11 (required, A3 decided): the other shared validators whose
    messages quote a rejected value outside a hiding model (typed IDs, language tags, UTC
    instants, decimal strings) cut the echo the same way, so a 5 MB value is bounded wherever it
    lands, not only in the two markers the audit named."""
    adapters: dict[str, TypeAdapter[Any]] = {
        "typed_id": type_adapter("ids", "TurnId"),
        "language_tag": type_adapter("language", "ObservedLanguageTag"),
        "utc_instant": type_adapter("primitives", "UtcDatetime"),
        "decimal_string": type_adapter("primitives", "DecimalString"),
    }
    huge = "q" * LIMIT + TAIL + "q" * 5_000_000
    with pytest.raises(ValidationError) as caught:
        adapters[site].validate_json(json.dumps(huge))  # the wire form: an ID is a str there
    message = caught.value.errors(include_input=False)[0]["msg"]
    assert len(message) < 2000, (site, len(message))
    assert "tail-marker" not in message
    assert any(form in message for form in _forms(huge)), (site, message[:200])


def test_unknown_digest_scheme_outside_a_hiding_model_is_cut_too() -> None:
    """m4, reading P11b: ``verify_digest`` on a record built without validation names the unknown
    scheme (it is outside any hiding model), and a 5 MB scheme is cut like every other echo, so
    the message is bounded and still names the registered schemes."""
    digest = api("digest")
    huge = "q" * LIMIT + TAIL + "q" * 5_000_000
    forged = digest.ContentDigest.model_construct(scheme=huge, value="0" * 64, key_ref=None)
    with pytest.raises(api("errors").UnknownDigestScheme) as caught:
        digest.verify_digest({"a": 1}, forged)
    message = str(caught.value)
    assert len(message) < 2000
    assert "tail-marker" not in message
    assert any(form in message for form in _forms(huge))
    assert "registered" in message
    short = digest.ContentDigest.model_construct(scheme="md5-jcs/v1", value="0" * 64, key_ref=None)
    with pytest.raises(api("errors").UnknownDigestScheme, match="'md5-jcs/v1'"):
        digest.verify_digest({"a": 1}, short)


def test_models_that_hide_their_input_still_quote_nothing_at_any_length() -> None:
    """m4, guard: the cut applies where values are quoted; where they are not (hiding models) the
    message still holds no part of the value, at any length. A long and a short value give the
    same message."""
    config = api("provider").ProviderConfigRef
    long_value = "q" * LIMIT + TAIL + "q" * 5000
    reports = []
    for value in ("x y", long_value):
        with pytest.raises(ValidationError) as caught:
            config.model_validate(provider_ref(provider=value))
        for text in renderings(caught.value):
            assert "tail-marker" not in text
            assert "x y" not in text
        reports.append(caught.value.errors(include_input=False))
    assert reports[0] == reports[1]


# ====================================================================================== m6

ENV_36 = "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"


def _config_error(config_ref: str) -> ValidationError:
    return _reject(api("provider").ProviderConfigRef, provider_ref(config_ref=config_ref))


def test_env_names_run_the_high_entropy_rule() -> None:
    """m6 (auto-approved): the name part after ``env:`` is checked with the high-entropy rule of
    ADR 0007 Decision 5a (a run of 32 or more characters with an upper-case letter, a lower-case
    letter and a digit, and 16 distinct characters), so a pasted token in an ``env:`` name is
    rejected. Reading P12: one error at ``config_ref`` of type ``config_ref_credential``; no part
    of the name is quoted."""
    for name in (ENV_36, ENV_36[:32], "MY_" + ENV_36, ENV_36 + "_TAIL"):
        error = _config_error("env:" + name)
        found = error.errors(include_input=False)
        assert [(e["loc"], e["type"]) for e in found] == [(("config_ref",), CONFIG_REF_CREDENTIAL)]
        assert not leaks(name, renderings(error)), name
        assert "input_value" not in str(error)


def test_env_name_credential_error_depends_on_the_rule_only() -> None:
    """m6: two different rejected env names give the same error text."""
    first = _config_error("env:" + ENV_36).errors(include_input=False)
    second = _config_error("env:Qw3rTy6uIo9pAs2dFg5hJk8lZx1cVb4nMm7e").errors(include_input=False)
    assert first == second


def test_env_high_entropy_rule_inside_a_project_and_through_json() -> None:
    """m6: the rule holds where a ``ProviderConfigRef`` is embedded (``Project``) and through the
    JSON entry point; the location names the nested field and nothing is quoted."""
    project = registered_class("Project")
    doc = raw_document("Project", provider_configs=[provider_ref(config_ref="env:" + ENV_36)])
    error = _reject(project, doc)
    found = error.errors(include_input=False)
    assert [e["type"] for e in found] == [CONFIG_REF_CREDENTIAL]
    assert found[0]["loc"][-1] == "config_ref"
    assert not leaks(ENV_36, renderings(error))
    config = api("provider").ProviderConfigRef
    with pytest.raises(ValidationError) as via_json:
        config.model_validate_json(json.dumps(provider_ref(config_ref="env:" + ENV_36)))
    assert not leaks(ENV_36, renderings(via_json.value))


@pytest.mark.parametrize("scheme", ["vault", "keyring", "env", "kms", "file"])
def test_the_entropy_rule_applies_to_the_name_part_of_every_config_ref_scheme(scheme: str) -> None:
    """m6 / A6 (decided): the high-entropy rule runs on the name part of every ``config_ref``
    scheme, not only ``env:``. A pasted token after ``vault:`` or ``keyring:`` (or any other
    lowercase scheme) is rejected like one after ``env:``: same error type, location and text,
    nothing quoted."""
    reference = _config_error("env:" + ENV_36).errors(include_input=False)
    for name in (ENV_36, "secret/" + ENV_36, ENV_36[:32]):
        error = _config_error(f"{scheme}:{name}")
        assert error.errors(include_input=False) == reference
        assert not leaks(name, renderings(error)), (scheme, name)


def test_env_names_that_are_not_high_entropy_are_accepted() -> None:
    """m6: ordinary environment variable names pass: upper-case with underscores and digits, a
    lower-case name, a long upper-case name (no lower-case letter), a mixed-case name one character
    below the threshold, and the other schemes (``keyring:``, ``vault:``) as before."""
    config = api("provider").ProviderConfigRef
    for ref in (
        "env:OPENAI_API_KEY",
        "env:SARVAM_API_KEY",
        "env:SARVAM_STT_KEY_2",
        "env:sarvam_api_key",
        "env:TRACER_PROVIDER_SARVAM_STT_API_KEY_PRODUCTION",
        "env:" + ENV_36[:31],
        "env:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # long, but one letter case and no digit
        "keyring:tracer/sarvam",
        "vault:secret/data/tracer/sarvam",
    ):
        assert config.model_validate(provider_ref(config_ref=ref)).config_ref == ref


def test_config_ref_known_prefix_is_checked_in_the_scheme_part_too() -> None:
    """m6, guard: the scheme part of a ``config_ref`` may hold letters, digits and ``+.-``, so a
    known prefix pasted there (a lower-case ``sk-`` run followed by a colon) is rejected, and
    nothing is quoted. The entropy rule is on the name part; the prefix rule is on the whole."""
    scheme = "sk" + "-" + "abcdefghijklmnopqrstuvwxyz"
    error = _config_error(scheme + ":NAME")
    assert [e["type"] for e in error.errors(include_input=False)] == [CONFIG_REF_CREDENTIAL]
    assert not leaks(scheme, renderings(error))


def test_config_ref_existing_rules_still_hold() -> None:
    """m6, guard: the known-prefix rule, the ``=`` rule and the length bound are unchanged, and
    they still quote nothing."""
    config = api("provider").ProviderConfigRef
    for bad in (
        "env:" + KEY_SAMPLES["openai"],
        "env:" + KEY_SAMPLES["aws-access-key"],
        "env:NAME=value",
        "env:" + "A" * 300,
        "no-scheme",
        "ENV:NAME",
    ):
        with pytest.raises(ValidationError) as caught:
            config.model_validate(provider_ref(config_ref=bad))
        assert not leaks(bad, renderings(caught.value)), bad


# ============================================================================== seed material


def test_the_dek_wrap_and_payload_helpers_build_valid_documents() -> None:
    """Self-check of the helpers (must stay green): the sealed documents built by ``_support_b16``
    are valid before any rule of this batch is applied, so a red test is red for its own rule, and
    the key reference of the fixture is an ordinary one."""
    model = sealed_class().model_validate(sealed_doc(digest_key_ref={"key_id": "hmac-other-1"}))
    assert model.plaintext_digest.key_ref.key_id == "hmac-other-1"
    assert model.dek_wrap.kek_ref.key_id == KEK_REF["key_id"]
    assert payload_doc()["dek_wrap"]["kek_ref"] == KEK_REF
    for label, value in KEY_SAMPLES.items():
        assert value, label
    reason = api("credentials").credential_reason
    assert reason(KEY_SAMPLES["mixed-case-36"]) == "high_entropy_token"
    assert reason(KEY_SAMPLES["mixed-case-32"]) == "high_entropy_token"
    assert api("credentials").credential_reason(ORDINARY_KEY_IDS[11]) is None
    assert api("credentials").credential_reason(HEX_40_LOWER) is None
    assert api("credentials").contains_credential_pattern(KEY_SAMPLES["openai"])
