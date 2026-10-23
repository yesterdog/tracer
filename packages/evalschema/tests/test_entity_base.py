# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-05: EntityBase, schema version errors, load-verified metadata flags (ADR 0001, 0007, 0008).

Assumed public API (docs/phase1/api-surface.md): ``tracer_evalschema.base.EntityBase`` (fields
``id``, ``schema_version``, ``created_at``, ``metadata``, ``metadata_flags``; a subclass sets the
class variable ``id_prefix``), ``MetadataFlag{path, reason}``, ``tracer_evalschema.errors`` and the
package attributes ``SCHEMA_VERSION`` / ``__version__``. Typed errors propagate out of
``model_validate`` unwrapped.
"""

from __future__ import annotations

import importlib.metadata
import json
import tomllib
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError, create_model

from ._support import (
    CURRENT_SCHEMA_VERSION,
    PACKAGE_DIR,
    api,
    entity_doc,
    versioned_doc,
    widget_classes,
)


def _widget() -> Any:
    return widget_classes().Widget


def test_entity_base_required_fields() -> None:
    widget = _widget()
    doc = entity_doc()
    value = widget.model_validate(doc)
    assert str(value.id) == doc["id"]
    assert value.id.uuid.version == 7
    assert value.schema_version == CURRENT_SCHEMA_VERSION
    assert value.created_at.utcoffset().total_seconds() == 0
    assert value.metadata == {}
    assert value.metadata_flags == []
    dumped = value.model_dump(mode="json")
    assert {"id", "schema_version", "created_at", "metadata", "metadata_flags", "name"} <= set(
        dumped
    )
    assert dumped["id"] == doc["id"]
    assert dumped["created_at"] == doc["created_at"]
    for field in ("id", "created_at"):
        with pytest.raises(ValidationError):
            widget.model_validate({k: v for k, v in doc.items() if k != field})
    errors = api("errors")
    with pytest.raises((ValidationError, errors.SchemaVersionInvalid)):
        widget.model_validate({k: v for k, v in doc.items() if k != "schema_version"})
    schema = widget.model_json_schema()
    assert {"id", "schema_version", "created_at"} <= set(schema["required"])
    assert schema["properties"]["id"]["pattern"].startswith("^evl_")
    # optional on input: metadata, and the computed flags
    minimal = {k: doc[k] for k in ("id", "schema_version", "created_at", "name")}
    assert widget.model_validate(minimal).metadata == {}


def test_extra_field_forbidden() -> None:
    widget = _widget()
    doc = entity_doc()
    for extra in ({"api_key": "x"}, {"unknown": 1}, {"content_digest": None}):
        with pytest.raises(ValidationError) as caught:
            widget.model_validate({**doc, **extra})
        assert any(e["type"] == "extra_forbidden" for e in caught.value.errors())
    with pytest.raises(ValidationError):
        widget.model_validate_json(json.dumps({**doc, "api_key": "x"}))
    assert widget.model_config.get("extra") == "forbid"


def test_entity_id_must_match_class_prefix() -> None:
    """Proposed name. A Widget (prefix evl) rejects other kinds' IDs and bare UUIDs."""
    ids = api("ids")
    widget = _widget()
    for bad in (str(ids.RunId.new()), str(ids.EvaluatorId.new().uuid), "evl_not-a-uuid", "", None):
        with pytest.raises(ValidationError):
            widget.model_validate(entity_doc(id=bad))


def test_entity_created_at_is_utc_datetime() -> None:
    """Proposed name. created_at uses UtcDatetime: naive is rejected, offsets become Z."""
    widget = _widget()
    with pytest.raises(ValidationError):
        widget.model_validate(entity_doc(created_at="2026-10-22T10:00:00"))
    value = widget.model_validate(entity_doc(created_at="2026-10-22T10:00:00+05:30"))
    assert value.model_dump(mode="json")["created_at"] == "2026-10-22T04:30:00.000000Z"


def test_schema_version_too_new() -> None:
    errors = api("errors")
    widget = _widget()
    doc = entity_doc()
    for newer in ("0.0.2", "0.0.10", "0.1.0", "1.0.0", "99.0.0"):
        with pytest.raises(errors.SchemaVersionTooNew) as caught:
            widget.model_validate({**doc, "schema_version": newer})
        assert caught.value.found == newer
        assert caught.value.expected == CURRENT_SCHEMA_VERSION
        with pytest.raises(errors.SchemaVersionTooNew):
            widget.model_validate_json(json.dumps({**doc, "schema_version": newer}))
        with pytest.raises(errors.SchemaVersionTooNew):
            api("base").check_schema_version(newer)
    assert api("base").check_schema_version(CURRENT_SCHEMA_VERSION) is None


def test_schema_version_mismatch_older() -> None:
    errors = api("errors")
    widget = _widget()
    with pytest.raises(errors.SchemaVersionMismatch) as caught:
        widget.model_validate({**entity_doc(), "schema_version": "0.0.0"})
    assert caught.value.found == "0.0.0"
    assert caught.value.expected == CURRENT_SCHEMA_VERSION
    assert not issubclass(errors.SchemaVersionMismatch, errors.SchemaVersionTooNew)
    assert not issubclass(errors.SchemaVersionTooNew, errors.SchemaVersionMismatch)
    with pytest.raises(errors.SchemaVersionMismatch):
        api("base").check_schema_version("0.0.0")


@pytest.mark.parametrize("value", ["1", "v0.0.1"])
def test_schema_version_invalid(value: str) -> None:
    errors = api("errors")
    widget = _widget()
    with pytest.raises(errors.SchemaVersionInvalid) as caught:
        widget.model_validate({**entity_doc(), "schema_version": value})
    assert caught.value.found == value
    with pytest.raises(errors.SchemaVersionInvalid):
        api("base").check_schema_version(value)


def test_schema_version_invalid_other_malformed() -> None:
    """Proposed name. Neighbours of the SCOPE list; ``found`` carries the raw value."""
    errors = api("errors")
    for value in (
        "",
        "0.0",
        "0.0.1.2",
        "0.0.x",
        " 0.0.1",
        "0.0.1 ",
        "00.0.1",
        "0.0.01",
        "0,0,1",
        1,
        None,
        0.0,
    ):
        with pytest.raises(errors.SchemaVersionInvalid) as caught:
            api("base").check_schema_version(value)
        assert caught.value.found == value or caught.value.found is value


def test_version_errors_carry_found_and_expected() -> None:
    errors = api("errors")
    base = api("base")
    cases = [
        (errors.SchemaVersionTooNew, "0.0.2"),
        (errors.SchemaVersionMismatch, "0.0.0"),
        (errors.SchemaVersionInvalid, "v0.0.1"),
    ]
    for exc_type, found in cases:
        with pytest.raises(exc_type) as caught:
            base.check_schema_version(found)
        error = caught.value
        assert error.found == found
        assert error.expected == CURRENT_SCHEMA_VERSION
        assert found in str(error)
        assert CURRENT_SCHEMA_VERSION in str(error)
        assert isinstance(error, errors.SchemaVersionError)
        assert isinstance(error, errors.TracerSchemaError)
        assert isinstance(error, Exception)
    # constructible directly with the two attributes
    direct = errors.SchemaVersionTooNew(found="9.9.9", expected="0.0.1")
    assert (direct.found, direct.expected) == ("9.9.9", "0.0.1")


def test_package_version_equals_schema_version() -> None:
    package = api("")
    assert package.SCHEMA_VERSION == package.__version__ == CURRENT_SCHEMA_VERSION
    assert importlib.metadata.version("tracer-evalschema") == CURRENT_SCHEMA_VERSION
    project = tomllib.loads((PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == CURRENT_SCHEMA_VERSION
    assert api("base").SCHEMA_VERSION == CURRENT_SCHEMA_VERSION


def test_metadata_flags_verified_on_load() -> None:
    """P-21: secret-looking metadata is preserved byte for byte and flagged in a serialized
    ``metadata_flags`` list that is recomputed and compared on load."""
    widget = _widget()
    base = api("base")
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2
    aws = "AKIAIOSFODNN7EXAMPLE"
    doc = entity_doc(
        metadata={"note": secret, "benign": "hello world", "nested": {"token": aws}, "count": 3}
    )
    doc.pop("metadata_flags")
    value = widget.model_validate(doc)
    assert value.metadata["note"] == secret, "the value is preserved, never redacted"
    assert value.metadata["nested"]["token"] == aws
    flags = value.metadata_flags
    assert all(isinstance(f, base.MetadataFlag) for f in flags)
    paths = [f.path for f in flags]
    assert len(flags) == 2, paths
    assert any("note" in p for p in paths)
    assert any("nested" in p and "token" in p for p in paths)
    assert not any("benign" in p or "count" in p for p in paths)
    assert all(isinstance(f.reason, str) and f.reason for f in flags)
    # serialized, and the round trip is stable
    dumped = value.model_dump(mode="json")
    assert [set(f) for f in dumped["metadata_flags"]] == [{"path", "reason"}] * 2
    assert widget.model_validate(dumped) == value
    assert widget.model_validate_json(value.model_dump_json()) == value
    # a flag list that disagrees with the computed one is rejected, whichever way
    with pytest.raises(ValidationError):
        widget.model_validate({**dumped, "metadata_flags": []})
    with pytest.raises(ValidationError):
        widget.model_validate({**dumped, "metadata_flags": dumped["metadata_flags"][:1]})
    forged = [*dumped["metadata_flags"], {"path": "/metadata/benign", "reason": "forged"}]
    with pytest.raises(ValidationError):
        widget.model_validate({**dumped, "metadata_flags": forged})
    renamed = [{**f, "path": f["path"] + "x"} for f in dumped["metadata_flags"]]
    with pytest.raises(ValidationError):
        widget.model_validate({**dumped, "metadata_flags": renamed})
    # removing the secret while keeping its flag is also a disagreement
    clean = {**dumped, "metadata": {"benign": "hello world"}}
    with pytest.raises(ValidationError):
        widget.model_validate(clean)
    # benign metadata has no flags and a forged flag on it is rejected
    plain = widget.model_validate(entity_doc(metadata={"benign": "hello"}))
    assert plain.metadata_flags == []
    with pytest.raises(ValidationError):
        widget.model_validate(
            entity_doc(
                metadata={"benign": "hello"},
                metadata_flags=[{"path": "/metadata/benign", "reason": "forged"}],
            )
        )
    # MetadataFlag is a value object: no entity fields
    assert set(base.MetadataFlag.model_fields) == {"path", "reason"}


# ------------------------------------------------------------------ metadata_flags: the exact rule
#
# ADR 0007 Decision 5a (revision 7). Scope: every JSON string at any depth of `metadata` (object
# member values and array elements); keys, numbers, booleans and nulls are never flagged.
# `known_credential_prefix`: a match of one of eleven patterns, starting at the beginning of the
# string or after a character outside [A-Za-z0-9]. `high_entropy_token`: a maximal run of
# [A-Za-z0-9+/=_-] of length >= 32 with an upper-case letter, a lower-case letter, a digit and
# >= 16 distinct characters. One flag per path (prefix wins). Paths are RFC 6901 JSON Pointers
# from the document root (`~` -> `~0`, `/` -> `~1`), sorted by UTF-16 code units.

KNOWN = "known_credential_prefix"
ENTROPY = "high_entropy_token"
# (label, a string that matches the pattern, a near miss that must not be flagged)
CREDENTIAL_SHAPES: list[tuple[str, str, str]] = [
    ("sk-", "sk-" + "a" * 20, "sk-" + "a" * 19),
    ("sk_live_", "sk_live_" + "a" * 16, "sk_live_" + "a" * 15),
    ("rk_test_", "rk_test_" + "b" * 16, "rk_test_" + "b" * 15),
    ("sk_test_", "sk_test_" + "c" * 20, "sk_prod_" + "c" * 20),
    ("AKIA", "AKIA" + "A" * 16, "AKIA" + "A" * 15),
    ("ASIA", "ASIA" + "0" * 16, "ASIA" + "a" * 16),  # tail must be [0-9A-Z]
    ("AIza", "AIza" + "a" * 35, "AIza" + "a" * 34),
    ("ghp_", "ghp_" + "a" * 36, "ghp_" + "a" * 35),
    ("ghs_", "ghs_" + "a" * 40, "ghx_" + "a" * 40),
    ("github_pat_", "github_pat_" + "a" * 22, "github_pat_" + "a" * 21),
    ("glpat-", "glpat-" + "a" * 20, "glpat-" + "a" * 19),
    ("xoxb-", "xoxb-" + "1" * 10, "xoxb-" + "1" * 9),
    ("xoxs-", "xoxs-" + "ab-" * 4, "xoxz-" + "1" * 10),
    ("hf_", "hf_" + "a" * 30, "hf_" + "a" * 29),
    ("jwt", "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + ".", "eyJ" + "a" * 9 + ".eyJ" + "b" * 10 + "."),
    ("pem", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PUBLIC KEY-----"),
    ("pem2", "-----BEGIN PRIVATE KEY-----", "-----BEGIN private key-----"),
]
_RUN_ALPHABET = "Aa0Bb1Cc2Dd3Ee4Ff5Gg6Hh7Ii8Jj9"  # first three characters: upper, lower, digit


def _entropy_run(length: int, distinct: int) -> str:
    """A run of `length` characters with exactly `distinct` distinct ones, including an upper-case
    letter, a lower-case letter and a digit, matching no credential prefix."""
    unit = _RUN_ALPHABET[:distinct]
    run = (unit * (length // distinct + 1))[:length]
    assert len(run) == length
    assert len(set(run)) == distinct
    return run


def _utf16_key(path: str) -> bytes:
    """Sort key comparing UTF-16 code units (big-endian bytes order = code unit order)."""
    return path.encode("utf-16-be")


def _only_flags(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    doc = entity_doc(metadata=metadata)
    doc.pop("metadata_flags")
    value = _widget().model_validate(doc)
    return [(f.path, f.reason) for f in value.metadata_flags]


def test_metadata_flag_rule_golden() -> None:
    """SCOPE name (revision 7; ADR 0007 Decision 5a, P-21): one document with both reasons, all
    string leaves of objects and arrays, the JSON Pointer escapes, the empty key, the order by
    UTF-16 code units, and values that must not be flagged. The expected list is literal."""
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2
    both = "sk-" + _entropy_run(40, 20)  # a credential prefix AND a long mixed run: one flag
    token = "Bearer " + _entropy_run(40, 24)  # high entropy inside a sentence
    metadata: dict[str, Any] = {
        "note": secret,
        "nested": {"token": "AKIA" + "IOSFODNN7EXAMPLE"},
        "items": ["plain", "hf_" + "a" * 30, ["x", "ghp_" + "a" * 36]],
        "a/b": "glpat-" + "a" * 20,
        "a~b": "xoxb-" + "1" * 10,
        "a~/b": "AIza" + "a" * 35,
        "": "sk_live_" + "a" * 16,
        "\U0001f600": "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + ".sig",
        "\uff5e": "rk_test_" + "b" * 16,
        "both": both,
        "entropy": token,
        "a": {"b": "ASIA" + "0" * 16},
        "a-b": "github_pat_" + "a" * 22,
        # never flagged: other types, keys, hex digests, our own IDs, ordinary text
        "digest": "0123456789abcdef" * 4,
        "id": "run_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
        "uuid": "0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
        "text": "hello world, this is a long sentence of ordinary words and Numbers 123 only",
        # out-of-range integers are rejected (I-JSON), covered by test_metadata_ijson.py
        "number": 9007199254740991,
        "flag": True,
        "nothing": None,
        "ratio": 0.5,
        secret: "the key is a key and a name is never flagged",
    }
    expected = [
        ("/metadata/", KNOWN),
        ("/metadata/a-b", KNOWN),
        ("/metadata/a/b", KNOWN),
        ("/metadata/a~0b", KNOWN),
        ("/metadata/a~0~1b", KNOWN),
        ("/metadata/a~1b", KNOWN),
        ("/metadata/both", KNOWN),
        ("/metadata/entropy", ENTROPY),
        ("/metadata/items/1", KNOWN),
        ("/metadata/items/2/1", KNOWN),
        ("/metadata/nested/token", KNOWN),
        ("/metadata/note", KNOWN),
        ("/metadata/\U0001f600", KNOWN),  # UTF-16 D83D DE00 sorts before U+FF5E
        ("/metadata/\uff5e", KNOWN),
    ]
    doc = entity_doc(metadata=metadata)
    doc.pop("metadata_flags")
    value = _widget().model_validate(doc)
    got = [(f.path, f.reason) for f in value.metadata_flags]
    assert got == expected
    # the order is the UTF-16 code unit order of the path (not Python's code point order)
    assert [p for p, _ in expected] == sorted(
        (p for p, _ in expected), key=lambda p: p.encode("utf-16-be")
    )
    assert [p for p, _ in expected] != sorted(p for p, _ in expected), "the two orders differ here"
    # values are preserved byte for byte; the flags are serialized as {path, reason} and reload
    assert value.metadata == metadata
    dumped = value.model_dump(mode="json")
    assert dumped["metadata_flags"] == [{"path": p, "reason": r} for p, r in expected]
    assert len({f["path"] for f in dumped["metadata_flags"]}) == len(expected), "one flag per path"
    assert _widget().model_validate(dumped) == value
    assert _widget().model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(("label", "matching", "near_miss"), CREDENTIAL_SHAPES)
def test_metadata_flag_known_credential_prefix_patterns(
    label: str, matching: str, near_miss: str
) -> None:
    """Proposed name. Each pattern of the fixed list (ADR 0007 Decision 5a) flags a matching
    string, does not flag a near miss, and only matches at the start of the string or after a
    character outside [A-Za-z0-9] (so ``_`` and ``=`` count as boundaries, a letter does not)."""
    assert _only_flags({"v": matching}) == [("/metadata/v", KNOWN)], label
    assert _only_flags({"v": near_miss}) == [], label
    assert _only_flags({"v": f"token={matching}"}) == [("/metadata/v", KNOWN)], label
    assert _only_flags({"v": f"Authorization: {matching} end"}) == [("/metadata/v", KNOWN)], label
    assert _only_flags({"v": f"_{matching}"}) == [("/metadata/v", KNOWN)], "'_' is a boundary"
    assert _only_flags({"v": f"x{matching}"}) == [], "preceded by a letter: not at a boundary"
    assert _only_flags({"v": f"7{matching}"}) == [], "preceded by a digit: not at a boundary"


@pytest.mark.parametrize(
    ("length", "distinct", "flagged"),
    [
        (32, 16, True),
        (32, 30, True),
        (40, 24, True),
        (64, 30, True),
        (31, 16, False),  # one character short
        (31, 30, False),
        (32, 15, False),  # one distinct character short
        (64, 15, False),
        (16, 16, False),
    ],
)
def test_metadata_flag_high_entropy_boundaries(length: int, distinct: int, flagged: bool) -> None:
    """Proposed name. ADR 0007 Decision 5a: length >= 32, upper + lower + digit, >= 16 distinct."""
    run = _entropy_run(length, distinct)
    assert _only_flags({"v": run}) == ([("/metadata/v", ENTROPY)] if flagged else [])
    # inside text the run is found as a maximal run of the character class
    assert _only_flags({"v": f"the token is {run}!"}) == (
        [("/metadata/v", ENTROPY)] if flagged else []
    )


def test_metadata_flag_high_entropy_needs_upper_lower_and_digit_in_one_run() -> None:
    """Proposed name. Each class is needed; the class [A-Za-z0-9+/=_-] holds the run together and
    anything else (space, dot, colon, non-ASCII) ends it; hex digests and our IDs are safe."""
    lower_digit = "abcdefghijklmnopqrstuvwxyz0123456789"
    upper_digit = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    upper_lower = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    full = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij0123456789"
    for run in (lower_digit, upper_digit, upper_lower):
        assert len(run) >= 32
        assert _only_flags({"v": run}) == [], run
    assert _only_flags({"v": full}) == [("/metadata/v", ENTROPY)]
    # + / = _ - belong to the run and count as distinct characters
    base64ish = "Aa0+Bb1/Cc2=Dd3_Ee4-Ff5Gg6Hh7Ii8"
    assert len(base64ish) >= 32
    assert _only_flags({"v": base64ish}) == [("/metadata/v", ENTROPY)]
    # a separator ends the run: two runs of 20 are not one run of 40
    twenty = _entropy_run(20, 20)
    for separator in (" ", ".", ":", ",", "\n", "\u00e9", "\u0939"):
        assert _only_flags({"v": twenty + separator + twenty}) == [], repr(separator)
    # lower-case hex digests and our own IDs are never flagged
    for harmless in (
        "0123456789abcdef" * 4,
        "ef003a6ac8a7c09c6f7d96a6bb944099e6320f7b29513f76db4b28ab10ee71c0",
        "run_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
        "evl_0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
        "0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b",
    ):
        assert _only_flags({"v": harmless}) == [], harmless


def test_metadata_flag_scope_is_string_leaves_of_metadata_only() -> None:
    """Proposed name. Keys, numbers, booleans and nulls are never flagged; strings are flagged at
    any depth, in objects and in arrays; only `metadata` is examined."""
    secret = "sk-" + "a" * 20
    # keys are not examined, even when they look like secrets
    assert _only_flags({secret: "ordinary", "k": {secret: 1}}) == []
    # non-string leaves
    # out-of-range integers are rejected (I-JSON), covered by test_metadata_ijson.py
    assert _only_flags({"n": 10**15, "f": 1e300, "b": True, "z": None, "e": [], "o": {}}) == []
    # depth: objects inside arrays inside objects
    deep: dict[str, Any] = {"l1": [{"l2": [{"l3": {"k": secret}}, secret]}]}
    assert _only_flags(deep) == [
        ("/metadata/l1/0/l2/0/l3/k", KNOWN),
        ("/metadata/l1/0/l2/1", KNOWN),
    ]
    # the secret in other fields of the entity is not metadata and is not flagged
    flagged_name = _widget().model_validate(entity_doc(name=secret, note=secret))
    assert flagged_name.metadata_flags == []
    assert flagged_name.name == secret
    # an empty metadata has no flags, and neither has benign text
    assert _only_flags({}) == []
    assert _only_flags({"a": "hello", "b": ["x", "y"], "c": {"d": "world"}}) == []


def test_metadata_flag_pointer_escaping_and_utf16_order() -> None:
    """Proposed name. RFC 6901: `~` is `~0` and `/` is `~1` (tilde first, so `~/` is `~0~1`); the
    list is sorted by path string comparing UTF-16 code units, so an astral character (a surrogate
    pair, D83D) sorts before U+FF5E although its code point is larger, and `/metadata/a-b` sorts
    before `/metadata/a/b` although the key `a` sorts before `a-b`."""
    secret = "sk-" + "a" * 20
    keys = [
        "a/b",
        "a~b",
        "a~/b",
        "~1",
        "~0",
        "/",
        "~",
        "a b",
        "\u00e9",
        "\U0001f600",
        "\uff5e",
        "\ue000",
    ]
    got = _only_flags(dict.fromkeys(keys, secret))
    paths = [path for path, _ in got]
    expected_paths = sorted(
        [
            "/metadata/a~1b",
            "/metadata/a~0b",
            "/metadata/a~0~1b",
            "/metadata/~01",
            "/metadata/~00",
            "/metadata/~1",
            "/metadata/~0",
            "/metadata/a b",
            "/metadata/\u00e9",
            "/metadata/\U0001f600",
            "/metadata/\uff5e",
            "/metadata/\ue000",
        ],
        key=_utf16_key,
    )
    assert paths == expected_paths
    assert all(reason == KNOWN for _, reason in got)
    # code point order would put the emoji last; UTF-16 order puts it before U+E000 and U+FF5E
    emoji, private, fullwidth = (f"/metadata/{ch}" for ch in ("\U0001f600", "\ue000", "\uff5e"))
    assert paths.index(emoji) < paths.index(private) < paths.index(fullwidth)
    # the whole path is compared as a string, not key by key
    assert [p for p, _ in _only_flags({"a": {"b": secret}, "a-b": secret})] == [
        "/metadata/a-b",
        "/metadata/a/b",
    ]
    # array indexes are decimal strings compared as text: 10 sorts before 2
    many = [secret if i in (2, 10) else "x" for i in range(12)]
    assert [p for p, _ in _only_flags({"i": many})] == ["/metadata/i/10", "/metadata/i/2"]


def test_metadata_flag_one_flag_per_path_prefix_reason_wins() -> None:
    """Proposed name. If both reasons match one string, the reason is known_credential_prefix."""
    mixed = "sk-" + _entropy_run(40, 20)
    assert _only_flags({"v": mixed}) == [("/metadata/v", KNOWN)]
    assert _only_flags({"v": f"{mixed} and {mixed}"}) == [("/metadata/v", KNOWN)]
    # a string that matches two different credential patterns is still one flag
    two = "sk-" + "a" * 20 + " AKIA" + "A" * 16
    assert _only_flags({"v": two}) == [("/metadata/v", KNOWN)]
    # high entropy and a credential prefix in different places of one string
    assert _only_flags({"v": _entropy_run(40, 24) + " hf_" + "a" * 30}) == [("/metadata/v", KNOWN)]


def test_metadata_flags_supplied_list_must_equal_the_computed_list() -> None:
    """Proposed name. ADR 0007 Decision 5a load rule: omitted -> computed; supplied -> the list
    must equal the computed one in entries, reasons and ORDER; anything else is a
    ValidationError (never a typed error: a forged list is a constraint violation)."""
    widget = _widget()
    first = "sk-" + "a" * 20
    second = "AKIA" + "A" * 16
    metadata = {"b": second, "a": first}
    computed = [
        {"path": "/metadata/a", "reason": KNOWN},
        {"path": "/metadata/b", "reason": KNOWN},
    ]
    assert widget.model_validate(entity_doc(metadata=metadata, metadata_flags=computed))
    for bad in (
        list(reversed(computed)),  # right content, wrong order
        [computed[0]],  # missing
        [*computed, computed[0]],  # duplicate
        [{**computed[0], "reason": ENTROPY}, computed[1]],  # wrong reason
        [{**computed[0], "reason": "forged"}, computed[1]],
        [{**computed[0], "path": "/metadata/c"}, computed[1]],
        [{"path": "/metadata/a"}, computed[1]],  # reason missing
        [{**computed[0], "extra": 1}, computed[1]],
        [],
    ):
        with pytest.raises(ValidationError):
            widget.model_validate(entity_doc(metadata=metadata, metadata_flags=bad))
    # a flag list for metadata that needs none
    with pytest.raises(ValidationError):
        widget.model_validate(entity_doc(metadata={"a": "hello"}, metadata_flags=computed[:1]))
    # JSON mode behaves the same
    good = entity_doc(metadata=metadata, metadata_flags=computed)
    assert widget.model_validate_json(json.dumps(good))
    with pytest.raises(ValidationError):
        widget.model_validate_json(json.dumps({**good, "metadata_flags": list(reversed(computed))}))
    # the reason strings are exactly these two
    assert _only_flags({"v": _entropy_run(40, 24)}) == [("/metadata/v", ENTROPY)]
    assert ENTROPY != KNOWN


# ------------------------------------------------------------ typed errors are not wrapped
#
# ADR 0008 Decision 6 (revision 7): pydantic v2 turns only ValueError, AssertionError and
# PydanticCustomError raised in validators into ValidationError, so every typed error that derives
# from TracerSchemaError and not from ValueError propagates as itself from model_validate,
# model_validate_json, TypeAdapter.validate_*, nested models and the loader.

NOT_VALUE_ERRORS = [
    "SchemaVersionError",
    "SchemaVersionTooNew",
    "SchemaVersionMismatch",
    "SchemaVersionInvalid",
    "UnknownDigestScheme",
    "DigestKeyUnavailable",
    "DigestMismatch",
    "NonIJsonValueError",
    "DuplicateKeyError",
    "DepthLimitExceeded",
    "BundleError",
    "MixedSchemaVersionError",
    "ConflictingVersionError",
    "ConflictingRecordError",
    "RefDigestMismatchError",
]


def test_typed_errors_do_not_derive_from_the_exceptions_pydantic_wraps() -> None:
    """Proposed name. The reason they propagate: none is a ValueError or AssertionError."""
    errors = api("errors")
    for name in NOT_VALUE_ERRORS:
        cls = getattr(errors, name)
        assert issubclass(cls, errors.TracerSchemaError), name
        assert not issubclass(cls, (ValueError, AssertionError)), name
    # the two that are ValueErrors are never raised inside model validation
    assert issubclass(errors.ConfusablePrefixError, ValueError)
    assert issubclass(errors.ConfusablePrefixError, errors.TracerSchemaError)
    assert issubclass(errors.DataFileError, ValueError)


LIST: Any = list  # `LIST[model]` for a model class that only exists at run time
DICT: Any = dict


def _holders() -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Every way a document reaches a model: direct, JSON, TypeAdapter, nested, loader."""
    widget = _widget()
    loader = api("loader")
    nested = create_model(
        "Holder",
        one=(widget, ...),
        many=(LIST[widget], ...),
        by_name=(DICT[str, widget], ...),
        maybe=(widget | None, None),
    )
    adapter = TypeAdapter(widget)
    list_adapter = TypeAdapter(LIST[widget])
    base = entity_doc()
    return {
        "model_validate": lambda doc: widget.model_validate(doc),
        "model_validate_json": lambda doc: widget.model_validate_json(json.dumps(doc)),
        "TypeAdapter.validate_python": lambda doc: adapter.validate_python(doc),
        "TypeAdapter.validate_json": lambda doc: adapter.validate_json(json.dumps(doc)),
        "list TypeAdapter": lambda doc: list_adapter.validate_python([doc]),
        "nested one": lambda doc: nested.model_validate({"one": doc, "many": [], "by_name": {}}),
        "nested list": lambda doc: nested.model_validate(
            {"one": base, "many": [doc], "by_name": {}}
        ),
        "nested dict": lambda doc: nested.model_validate(
            {"one": base, "many": [], "by_name": {"k": doc}}
        ),
        "nested json": lambda doc: nested.model_validate_json(
            json.dumps({"one": base, "many": [], "by_name": {}, "maybe": doc})
        ),
        "load_model": lambda doc: loader.load_model(widget, json.dumps(doc)),
    }


def test_schema_version_errors_propagate_unwrapped_through_every_entry_point() -> None:
    """Proposed name. SchemaVersionTooNew / Mismatch / Invalid come out as themselves, and the
    version is checked before any field is validated (so an invalid id next to a too-new version
    reports the version)."""
    errors = api("errors")
    cases = [
        ("99.0.0", errors.SchemaVersionTooNew),
        ("0.0.0", errors.SchemaVersionMismatch),
        ("v0.0.1", errors.SchemaVersionInvalid),
    ]
    for name, validate in _holders().items():
        for version, expected in cases:
            for extra in ({}, {"id": "not-an-id", "created_at": "never"}):
                doc = {**entity_doc(), "schema_version": version, **extra}
                with pytest.raises(expected) as caught:
                    validate(doc)
                assert type(caught.value) is expected, (name, version, extra)
                assert not isinstance(caught.value, ValidationError)
                assert version in str(caught.value), "the typed error names the offending value"
    # ordinary constraint violations are still ValidationErrors, never typed errors
    for name, validate in _holders().items():
        with pytest.raises(ValidationError) as caught:
            validate({**entity_doc(), "id": "not-an-id"})
        assert not isinstance(caught.value, errors.TracerSchemaError), name


def test_digest_errors_propagate_unwrapped_through_every_entry_point() -> None:
    """Proposed name. UnknownDigestScheme out of ContentDigest and DigestMismatch out of a
    VersionedEntity come out as themselves, also from nested positions; a ValidationError (a
    key_ref on a plain digest) stays a ValidationError."""
    errors = api("errors")
    digest = api("digest")
    versioned = widget_classes().VersionedWidget
    content_digest: Any = digest.ContentDigest
    holder = create_model(
        "DigestHolder", one=(content_digest, ...), many=(LIST[content_digest], ...)
    )
    plain = {"scheme": "sha256-jcs/v1", "value": "0" * 64}
    unknown = {"scheme": "md5-jcs/v1", "value": "0" * 64}
    adapter = TypeAdapter(digest.ContentDigest)
    attempts: dict[str, Callable[[dict[str, Any]], Any]] = {
        "model_validate": digest.ContentDigest.model_validate,
        "model_validate_json": lambda d: digest.ContentDigest.model_validate_json(json.dumps(d)),
        "TypeAdapter.validate_python": adapter.validate_python,
        "TypeAdapter.validate_json": lambda d: adapter.validate_json(json.dumps(d)),
        "nested": lambda d: holder.model_validate({"one": plain, "many": [d]}),
        "nested json": lambda d: holder.model_validate_json(json.dumps({"one": d, "many": []})),
    }
    for name, validate in attempts.items():
        with pytest.raises(errors.UnknownDigestScheme) as caught:
            validate(unknown)
        assert type(caught.value) is errors.UnknownDigestScheme, name
        with pytest.raises(ValidationError):
            validate({**plain, "key_ref": {"key_id": "k1"}})
    # ContentDigest hides its input, so the scheme name is asserted on the verify path only
    forged = digest.ContentDigest.model_construct(scheme="md5-jcs/v1", value="0" * 64, key_ref=None)
    with pytest.raises(errors.UnknownDigestScheme) as verify_caught:
        digest.verify_digest({"a": 1}, forged)
    assert "md5-jcs/v1" in str(verify_caught.value)
    # DigestMismatch from a versioned entity, directly and nested in a list
    doc = versioned_doc()
    tampered = {**doc, "name": "tampered"}
    list_adapter = TypeAdapter(LIST[versioned])
    for validate in (
        versioned.model_validate,
        lambda d: versioned.model_validate_json(json.dumps(d)),
        lambda d: list_adapter.validate_python([d]),
        lambda d: list_adapter.validate_json(json.dumps([d])),
    ):
        with pytest.raises(errors.DigestMismatch) as caught:
            validate(tampered)
        assert type(caught.value) is errors.DigestMismatch
        assert not isinstance(caught.value, ValidationError)
        assert doc["id"] in str(caught.value) or "digest" in str(caught.value).lower()


def test_loader_typed_errors_are_not_wrapped_either() -> None:
    """Proposed name. DuplicateKeyError and DepthLimitExceeded come out of load_model as
    themselves, and a typed error inside the document is not turned into a ValidationError."""
    errors = api("errors")
    loader = api("loader")
    widget = _widget()
    text = json.dumps(entity_doc())
    with pytest.raises(errors.DuplicateKeyError) as dup:
        loader.load_model(widget, text[:-1] + ', "name": "again"}')
    assert type(dup.value) is errors.DuplicateKeyError
    with pytest.raises(errors.DepthLimitExceeded) as deep:
        loader.load_model(widget, "[" * 200 + "]" * 200, max_depth=50)
    assert type(deep.value) is errors.DepthLimitExceeded
    with pytest.raises(errors.SchemaVersionMismatch) as old:
        loader.load_model(widget, json.dumps({**entity_doc(), "schema_version": "0.0.0"}))
    assert type(old.value) is errors.SchemaVersionMismatch
