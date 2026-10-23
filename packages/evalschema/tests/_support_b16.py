# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 25 security-hardening tests (P1-21 audit findings C3, m1, m2, m4,
m6). No tests here.

Contract: ``docs/phase1/api-surface-hardening.md``. Everything is ASCII. Every credential-shaped
sample is assembled from parts at run time, so no complete key shape appears in a source line
(a secret scanner has nothing to find) and none of them is a real key.

What is here:

- ``KEY_SAMPLES``: key-id-shaped strings that the new ``KeyRef`` rule must reject, grouped by the
  shape they have (known prefix, high entropy, ``key=value``);
- ``ORDINARY_KEY_IDS`` and ``ORDINARY_KEY_VERSIONS``: values it must keep accepting;
- ``renderings``: every way a ``ValidationError`` can be shown without its input;
- ``leaks``: the check for "no part of the value is quoted";
- ``keyed``: a keyed digest plus its key, for the resolver tests;
- ``sealed_doc``: a structurally valid ``SealedPayload`` document with a keyed plaintext digest,
  for the key-separation and nested-location tests;
- ``embedding_models``: the registry walk behind the closure test.
"""

from __future__ import annotations

import traceback
from typing import Any

from pydantic import ValidationError

from ._support import api
from ._support_b2 import embedded_models, error_texts
from ._support_b5 import KEK_REF, KEYED, payload_doc

__all__ = [
    "GOOD_KEY",
    "HEX_40_LOWER",
    "HEX_40_UPPER",
    "KEY_SAMPLES",
    "MARKER_HINT",
    "MARKER_KEY_ID",
    "MARKER_KEY_VERSION",
    "ORDINARY_KEY_IDS",
    "ORDINARY_KEY_VERSIONS",
    "SHAPE_OF",
    "WINDOW",
    "embedding_models",
    "exception_texts",
    "keyed",
    "leaks",
    "renderings",
    "sealed_doc",
]

# Made-up markers. They are valid key ids under the new rule (lower-case words and a short hex
# tail), so a test can name a real KeyRef with them and then look for them in messages.
MARKER_KEY_ID = "qa-marker-key-id-3c9d51ae"
MARKER_KEY_VERSION = "qa-marker-version-77e1"
MARKER_HINT = "qa-marker-hint"

GOOD_KEY = b"tracer-test-key-0000000000000000"  # 32 bytes, the smallest accepted key

WINDOW = 8
"""A rendering leaks a value when any run of this many consecutive characters of it (or the whole
value, when it is shorter) appears in the rendering."""


def _kp(*parts: str) -> str:
    return "".join(parts)


_MIXED_36 = _kp("aB3dE6gH9jK2mN5pQ8sT1vW4yZ7b", "C0eF3hJ6")
_MIXED_32 = _MIXED_36[:32]
_MIXED_31 = _MIXED_36[:31]

# label -> (shape, value). The shape names the rule that must reject the value; two values of one
# shape give one and the same error text (the text depends on the rule and the location only).
_SAMPLES: dict[str, tuple[str, str]] = {
    "openai": ("known_prefix", _kp("sk", "-", "A1b2C3d4E5" * 2)),
    "openai-project": ("known_prefix", _kp("sk", "-proj-", "a1B2c3D4e5" * 3)),
    "stripe-live": ("known_prefix", _kp("sk", "_live_", "a1b2c3d4e5f6g7h8")),
    "stripe-restricted-test": ("known_prefix", _kp("rk", "_test_", "q1w2e3r4t5y6u7i8")),
    "github-pat-classic": ("known_prefix", _kp("gh", "p_", "A1" * 18)),
    "github-oauth": ("known_prefix", _kp("gh", "o_", "Z9" * 18)),
    "github-fine-grained": ("known_prefix", _kp("github", "_pat_", "a1B2c3D4e5F6g7H8i9J0k1")),
    "aws-access-key": ("known_prefix", _kp("AK", "IA", "ABCDEFGH12345678")),
    "aws-session-key": ("known_prefix", _kp("A", "SIA", "ZYXWVUTS98765432")),
    "google-api-key": ("known_prefix", _kp("AI", "za", "B2" * 17, "c")),
    "gitlab": ("known_prefix", _kp("gl", "pat-", "abcdefghij" * 2)),
    "slack": ("known_prefix", _kp("xox", "b-", "1234567890-abcdefghij")),
    "hugging-face": ("known_prefix", _kp("hf", "_", "aBcDeFgHiJ" * 3)),
    "jwt": (
        "known_prefix",
        _kp("eyJ", "hbGciOiJIUzI1NiJ9", ".eyJ", "zdWIiOiIxMjM0NTY3ODkwIn0", ".sigsigsig"),
    ),
    "pem-header": ("known_prefix", _kp("-----BEGIN ", "RSA PRIVATE KEY", "-----")),
    "prefix-after-separator": ("known_prefix", _kp("prod/", "sk", "-", "A1b2C3d4E5" * 2)),
    "prefix-after-underscore": ("known_prefix", _kp("kek_", "AK", "IA", "ABCDEFGH12345678")),
    "mixed-case-36": ("high_entropy", _MIXED_36),
    "mixed-case-32": ("high_entropy", _MIXED_32),
    "mixed-case-with-separators": ("high_entropy", _kp("kek/", _MIXED_36, "/main")),
    "credential-segment-in-gcp-path": (
        "high_entropy",
        _kp("projects/my-proj-123/locations/global/keyRings/ring1/cryptoKeys/", _MIXED_36),
    ),
    "credential-segment-after-colon": (
        "known_prefix",
        _kp("urn:tracer:", "sk", "-", "A1b2C3d4E5" * 2),
    ),
    "api-key-pair": ("key_value", "api_key=abcd1234"),
    "password-pair": ("key_value", "password=hunter2x"),
    "token-pair": ("key_value", "token=x1"),
    "bare-equals": ("key_value", "kek=1"),
}
KEY_SAMPLES: dict[str, str] = {label: value for label, (_shape, value) in _SAMPLES.items()}
SHAPE_OF: dict[str, str] = {label: shape for label, (shape, _value) in _SAMPLES.items()}

HEX_40_LOWER = "0123456789abcdef0123456789abcdef01234567"
HEX_40_UPPER = "0123456789ABCDEF0123456789ABCDEF01234567"

ORDINARY_KEY_IDS: tuple[str, ...] = (
    "vault-key-1",
    "kms/prod/alias-1",
    "3f2b8c1e-4d5a-4e6f-8a7b-9c0d1e2f3a4b",
    "alias/tracer-prod",
    "arn:aws:kms:ap-south-1:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab",
    "hsm:slot-3:label-7",
    "KEK-2026",
    "kek-test-1",
    "hmac-test-1",
    "k",
    "a" * 200,
    _MIXED_31,  # one character below the high-entropy threshold
    "0123456789abcdef0123456789abcdef",  # 32 hex: an Azure key version, a UUID without dashes
    "CamelCaseKeyNameWithoutAnyDigitsAtAll",  # long, mixed case, no digit
    "kunji-" + chr(0x0915) + chr(0x0916),  # a key name may hold non-ASCII text (opaque string)
    MARKER_KEY_ID,
    # A1: a 40-digit hex string is also an OpenPGP v4 fingerprint, so it stays accepted
    HEX_40_LOWER,
    HEX_40_UPPER,
    # A5: the high-entropy run rule applies per segment split at "/" and ":"
    "projects/my-proj-123/locations/global/keyRings/ring1/cryptoKeys/key1",
    "urn:tracer:kms:key:Prod-Ring-7:alias/Main-Key-2026",
    _MIXED_31 + "/" + _MIXED_31 + ":" + _MIXED_31,
)
ORDINARY_KEY_VERSIONS: tuple[str, ...] = (
    "1",
    "2026-10-24",
    "v7",
    "0123456789abcdef0123456789abcdef",
    "3f2b8c1e-4d5a-4e6f-8a7b-9c0d1e2f3a4b",
    HEX_40_LOWER,
    MARKER_KEY_VERSION,
)


# ------------------------------------------------------------------------------- renderings


def renderings(error: ValidationError) -> list[str]:
    """Every rendering of a ``ValidationError`` that can leave the input out (str, repr,
    ``errors(include_input=False)`` with and without context, ``json(include_input=False)``)."""
    return [
        *error_texts(error),
        repr(error.errors(include_input=False, include_context=False)),
        repr(error.errors(include_input=False, include_url=False)),
        error.json(include_input=False, include_context=False),
    ]


def exception_texts(error: BaseException) -> list[str]:
    """Each way a plain exception shows its message: str, repr, args, the one-line report and the
    full report without the chained causes."""
    return [
        str(error),
        repr(error),
        " ".join(str(a) for a in error.args),
        "".join(traceback.format_exception_only(type(error), error)),
        "".join(traceback.format_exception(type(error), error, error.__traceback__, chain=False)),
    ]


def leaks(value: str, texts: list[str]) -> list[str]:
    """The parts of ``value`` that appear in any of ``texts`` (empty list: nothing is quoted)."""
    found: list[str] = []
    width = min(WINDOW, len(value))
    for start in range(len(value) - width + 1):
        window = value[start : start + width]
        if any(window in text for text in texts):
            found.append(window)
    return found


# ----------------------------------------------------------------------------- digest helpers


def keyed(
    key_id: str = MARKER_KEY_ID,
    *,
    key_version: str | None = None,
    provider_hint: str | None = None,
    key: bytes = GOOD_KEY,
    subject: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Any]:
    """``(subject, digest)``: a keyed digest of ``subject`` made with ``key`` and named by a
    ``KeyRef`` with the given fields."""
    digest = api("digest")
    ref = digest.KeyRef(key_id=key_id, key_version=key_version, provider_hint=provider_hint)
    document = subject if subject is not None else {"id": "subject-1", "text": "hello"}
    return document, digest.compute_digest(document, scheme=KEYED, key=key, key_ref=ref)


def sealed_doc(
    *,
    kek_ref: dict[str, Any] | None = None,
    digest_key_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A structurally valid ``SealedPayload`` document (random bytes, no cryptography) whose
    ``plaintext_digest`` is a keyed digest value naming ``digest_key_ref`` (when given)."""
    doc = payload_doc("test_profile")
    if kek_ref is not None:
        doc["dek_wrap"]["kek_ref"] = dict(kek_ref)
    else:
        doc["dek_wrap"]["kek_ref"] = dict(KEK_REF)
    if digest_key_ref is not None:
        doc["plaintext_digest"] = {
            "scheme": KEYED,
            "value": "0" * 64,
            "key_ref": dict(digest_key_ref),
        }
    return doc


# ---------------------------------------------------------------------------- registry walk


def embedding_models(seeds: tuple[str, ...]) -> tuple[dict[str, Any], list[str]]:
    """``(by_name, names)``: every registered model, and the sorted names of the models that are
    a seed or embed one at any depth (fields inherited from a base count)."""
    by_name = {m.__name__: m for m in api("registry").model_classes()}
    seed_models = {by_name[name] for name in seeds}
    names = sorted(
        name
        for name, model in by_name.items()
        if model in seed_models or embedded_models(model) & seed_models
    )
    return by_name, names
