# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 20 (qa), part 1: ``EntityBase.metadata`` is I-JSON (RFC 7493).

Decision (owner, auto-approved on the adversarial findings): ``EntityBase.metadata`` is typed
``IJsonObject``. Before it, ``metadata: dict[str, Any]`` accepted ``NaN``, the infinities, lone
surrogates, integers beyond +-(2**53 - 1) and non-JSON Python values, and
``model_dump(mode="json")`` turned ``NaN`` into ``null`` without a word: data changed silently,
and the JCS digest of the dump could not be computed from what the user supplied.

The contract these tests pin, for every registered entity (a walk over the registry, no
hard-coded count):

- rejected with a ``ValidationError`` whose first error is at ``("metadata",)``: ``NaN``,
  ``Infinity``, ``-Infinity`` (also nested in lists and objects), integers outside
  +-(2**53 - 1), a lone surrogate in a string value or in a key, and every Python value that is
  not JSON (tuple, set, bytes, non-string key, other objects); also from JSON text
  (``model_validate_json``, ``loads_strict`` + ``model_validate`` for ``1e999``);
- the message names a JSON Pointer inside the metadata and never the rejected value;
- accepted and kept byte for byte, with the digest: nested objects and arrays, integers inside
  the range, finite floats (also beyond 2**53: a finite double is I-JSON), Indic text with ZWJ and
  ZWNJ, valid astral characters;
- ``model_dump(mode="json")`` never changes the metadata;
- the JSON Schema of ``metadata`` stays an unconstrained object (the architect lists the diff).

Documents: the minimal golden fixture of each entity (``fixtures/golden/<Entity>/valid``). All
non-ASCII characters are written as escapes in this file.
"""

from __future__ import annotations

import copy
import decimal
import json
import math
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import error_texts
from ._support_b9 import GOLDEN, entity_models, read_json

ENTITY_NAMES = sorted(entity_models())
DIGESTED = [
    name
    for name in ENTITY_NAMES
    if "content_digest" in read_json(GOLDEN / name / "valid" / "minimal.json")
]
BAD = "@@BAD@@"

# --------------------------------------------------------------------------------- the cases

NAN = float("nan")
INF = float("inf")

# (id, metadata) the model must refuse, as Python objects.
PYTHON_REJECTED: list[tuple[str, dict[str, Any]]] = [
    ("nan", {"v": NAN}),
    ("inf", {"v": INF}),
    ("minus-inf", {"v": -INF}),
    ("nan-in-list", {"a": [1, 2, NAN]}),
    ("inf-in-nested-object", {"a": {"b": [{"c": INF}]}}),
    ("int-2-53", {"v": 2**53}),
    ("int-minus-2-53", {"v": -(2**53)}),
    ("int-1e30", {"v": 10**30}),
    ("int-in-list", {"a": [0, [2**60]]}),
    ("lone-high-surrogate-value", {"v": "\ud800"}),
    ("lone-low-surrogate-value", {"v": "x\udc00y"}),
    ("reversed-pair-value", {"v": "\udc00\ud800"}),
    ("lone-surrogate-in-list", {"a": ["ok", "\ud83d"]}),
    ("lone-surrogate-key", {"\ud800": 1}),
    ("lone-surrogate-nested-key", {"a": {"b": {"\udfff": "x"}}}),
    ("tuple", {"v": (1, 2)}),
    ("set", {"v": {1, 2}}),
    ("bytes", {"v": b"abc"}),
    ("int-key", {1: "x"}),  # type: ignore[dict-item]
    ("none-key", {None: "x"}),  # type: ignore[dict-item]
    ("object", {"v": object()}),
    ("decimal", {"v": decimal.Decimal("1.5")}),
    ("complex", {"v": complex(1, 2)}),
]

# JSON text of one metadata member that must be refused (``@@BAD@@`` is replaced by the text).
JSON_TEXT_REJECTED: list[tuple[str, str]] = [
    ("NaN", "NaN"),
    ("Infinity", "Infinity"),
    ("-Infinity", "-Infinity"),
    ("1e999", "1e999"),
    ("-1e999", "-1e999"),
    ("int-2-53", "9007199254740992"),
    ("int-minus-2-53", "-9007199254740992"),
    ("int-beyond-i64", "123456789012345678901234567890"),
    ("array-with-nan", "[1, NaN]"),
    ("object-with-1e999", '{"a": [1e999]}'),
]


def minimal(entity: str) -> dict[str, Any]:
    doc: dict[str, Any] = read_json(GOLDEN / entity / "valid" / "minimal.json")
    return doc


def with_metadata(entity: str, metadata: Any) -> dict[str, Any]:
    """The minimal document of the entity with ``metadata`` replaced (digest and flags left as
    they are: a rejected metadata must fail at the field, before any digest or flag rule)."""
    doc = minimal(entity)
    doc["metadata"] = metadata
    return doc


def is_hiding(model: Any) -> bool:
    return bool(model.model_config.get("hide_input_in_errors"))


def assert_metadata_error(
    model: Any, error: ValidationError, *, pointer: str | None = None
) -> None:
    """The first error is at ``("metadata",)`` (not a digest, flag or other rule), has a message
    and no input, and the message holds the pointer when one is given."""
    first = error.errors(include_input=False, include_url=False)[0]
    assert tuple(first["loc"]) == ("metadata",), first
    assert str(first["msg"]).strip()
    if pointer is not None:
        assert pointer in str(first["msg"]), first["msg"]
    assert "input" not in first
    del model


def renderings(model: Any, error: ValidationError) -> list[str]:
    """Every form of the error that code might log. ``errors()`` and ``json()`` carry the input in
    pydantic whatever the config (ADR 0007 Decision 8 (d)); a model that does not hide its input
    echoes it in ``str`` too, so only the input-free forms are checked for it."""
    if is_hiding(model):
        return error_texts(error)
    return [repr(error.errors(include_input=False)), error.json(include_input=False)]


# ----------------------------------------------------------------------------- the registry walk


def test_every_registered_entity_has_a_minimal_golden_document() -> None:
    """The walk below covers each registered entity (none is skipped for want of a document)."""
    assert len(ENTITY_NAMES) == len(api("registry").entity_classes())
    assert len(ENTITY_NAMES) >= 26
    for entity in ENTITY_NAMES:
        assert (GOLDEN / entity / "valid" / "minimal.json").is_file(), entity


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_the_minimal_document_loads_before_any_change(entity: str) -> None:
    """Control: the unchanged document is valid, so a rejection below is caused by the metadata."""
    entity_models()[entity].model_validate(minimal(entity))


@pytest.mark.parametrize("entity", ENTITY_NAMES)
@pytest.mark.parametrize(("case", "metadata"), PYTHON_REJECTED, ids=[c for c, _ in PYTHON_REJECTED])
def test_metadata_that_is_not_ijson_is_rejected_at_the_metadata_path(
    entity: str, case: str, metadata: dict[str, Any]
) -> None:
    """EntityBase.metadata is an I-JSON object: ``model_validate`` raises ``ValidationError`` and
    the first error is at ``("metadata",)``. (Before: all of these were accepted and ``NaN`` was
    dumped as ``null``.)"""
    model = entity_models()[entity]
    with pytest.raises(ValidationError) as caught:
        model.model_validate(with_metadata(entity, metadata))
    assert_metadata_error(model, caught.value)
    del case


@pytest.mark.parametrize("entity", ENTITY_NAMES)
@pytest.mark.parametrize(
    ("case", "raw"), JSON_TEXT_REJECTED, ids=[c for c, _ in JSON_TEXT_REJECTED]
)
def test_metadata_json_text_that_is_not_ijson_is_rejected_by_model_validate_json(
    entity: str, case: str, raw: str
) -> None:
    """The same rule through ``model_validate_json``: pydantic's JSON reader decodes ``NaN``,
    ``Infinity`` and ``1e999`` to floats and large integers to ``int``; the field refuses them."""
    model = entity_models()[entity]
    text = json.dumps(with_metadata(entity, {"v": BAD})).replace(json.dumps(BAD), raw)
    assert raw in text
    with pytest.raises(ValidationError) as caught:
        model.model_validate_json(text)
    assert_metadata_error(model, caught.value)
    del case


@pytest.mark.parametrize("entity", ENTITY_NAMES)
@pytest.mark.parametrize("raw", ["1e999", "-1e999", "9007199254740993", "[1e999]"])
def test_metadata_overflow_through_loads_strict_is_rejected_by_the_model(
    entity: str, raw: str
) -> None:
    """X-01: ``loads_strict`` lets ``1e999`` through (it decodes to infinity, it is not the
    constant ``Infinity``) and a large integer through; the model is the second line."""
    loader = api("loader")
    model = entity_models()[entity]
    text = json.dumps(with_metadata(entity, {"v": BAD})).replace(json.dumps(BAD), raw)
    document = loader.loads_strict(text)
    with pytest.raises(ValidationError) as caught:
        model.model_validate(document)
    assert_metadata_error(model, caught.value)
    with pytest.raises(ValidationError) as via_model:
        loader.load_model(model, text)
    assert_metadata_error(model, via_model.value)


@pytest.mark.parametrize("entity", ENTITY_NAMES)
@pytest.mark.parametrize("raw", ['"\\ud800"', '"x\\udc00"'])
def test_metadata_lone_surrogate_escape_in_json_text_is_rejected(entity: str, raw: str) -> None:
    """A lone surrogate written as a JSON escape is refused whether the reader (pydantic's or the
    standard library's) decodes it to a lone surrogate or already refuses the text. Both are a
    ``ValidationError`` or, for the standard reader, a value the model refuses."""
    model = entity_models()[entity]
    text = json.dumps(with_metadata(entity, {"v": BAD})).replace(json.dumps(BAD), raw)
    with pytest.raises(ValidationError):
        model.model_validate_json(text)
    with pytest.raises(ValidationError) as caught:
        model.model_validate(api("loader").loads_strict(text))
    assert_metadata_error(model, caught.value)


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_metadata_lone_surrogate_key_in_json_text_is_rejected(entity: str) -> None:
    model = entity_models()[entity]
    text = json.dumps(with_metadata(entity, {BAD: 1})).replace(json.dumps(BAD), '"\\ud800"')
    with pytest.raises(ValidationError):
        model.model_validate_json(text)
    with pytest.raises(ValidationError) as caught:
        model.model_validate(api("loader").loads_strict(text))
    assert_metadata_error(model, caught.value)


def test_metadata_error_names_the_pointer_of_the_offending_member() -> None:
    """The message gives the JSON Pointer of the member inside the metadata (RFC 6901, ``~`` and
    ``/`` escaped), as the other I-JSON fields do."""
    model = entity_models()["Persona"]
    cases = [
        ({"a": [0, {"b": NAN}]}, "/a/1/b"),
        ({"x/y": {"z~": INF}}, "/x~1y/z~0"),
        ({"v": 2**53}, "/v"),
        ({"a": ["ok", "\ud800"]}, "/a/1"),
    ]
    for metadata, pointer in cases:
        with pytest.raises(ValidationError) as caught:
            model.model_validate(with_metadata("Persona", metadata))
        assert_metadata_error(model, caught.value, pointer=pointer)


# ------------------------------------------------------------------------------------ no echo

SENTINEL = "SENTINEL_q9x7"


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_metadata_errors_never_show_the_rejected_value(entity: str) -> None:
    """The rejected value is not in any input-free rendering of the error (and, for a model that
    hides its input, in none at all): a lone surrogate (it would also break a log writer), a
    large integer and the number text."""
    model = entity_models()[entity]
    big = 10**30 + 7
    cases: list[tuple[dict[str, Any], tuple[str, ...]]] = [
        ({"v": "\ud800" + SENTINEL}, ("\ud800", SENTINEL)),
        ({"v": big}, (str(big),)),
        ({SENTINEL + "\udc00": 1}, ("\udc00", SENTINEL)),
        ({"v": [NAN, SENTINEL]}, ()),
    ]
    for metadata, needles in cases:
        with pytest.raises(ValidationError) as caught:
            model.model_validate(with_metadata(entity, metadata))
        for text in renderings(model, caught.value):
            for needle in needles:
                assert needle not in text, f"a rejected value is shown: {needle!r}"
    # JSON text: the number text is not echoed either
    text_doc = json.dumps(with_metadata(entity, {"v": BAD})).replace(
        json.dumps(BAD), "123456789012345678901234567890123"
    )
    with pytest.raises(ValidationError) as caught_text:
        model.model_validate_json(text_doc)
    for text in renderings(model, caught_text.value):
        assert "123456789012345678901234567890123" not in text


# -------------------------------------------------------------- valid metadata is kept exactly

ZWJ = "\u200d"
ZWNJ = "\u200c"

RICH: dict[str, Any] = {
    "empty_object": {},
    "empty_list": [],
    "null": None,
    "true": True,
    "false": False,
    "ints": [0, -1, 1, 2**53 - 1, -(2**53 - 1), 1000000],
    "floats": [0.5, -2.25, 1e300, 5e-324, 2.0**60, 1.7976931348623157e308],
    "nested": {"a": {"b": [{"c": [1, [2, [3, {"d": "deep"}]]]}]}},
    "pointer/slash": {"tilde~": "x"},
    "text": 'plain ascii with "quotes", \\ backslash, tab\t, newline\n',
    # Hindi, Marathi, Telugu, Tamil, Kannada, Malayalam, Punjabi (Gurmukhi), Gujarati
    "hi": "\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e",
    "mr": "\u092e\u0930\u093e\u0920\u0940 \u092d\u093e\u0937\u093e",
    "te": "\u0c24\u0c46\u0c32\u0c41\u0c17\u0c41",
    "ta": "\u0ba4\u0bae\u0bbf\u0bb4\u0bcd",
    "kn": "\u0c95\u0ca8\u0ccd\u0ca8\u0ca1",
    "ml": "\u0d2e\u0d32\u0d2f\u0d3e\u0d33\u0d02",
    "pa": "\u0a2a\u0a70\u0a1c\u0a3e\u0a2c\u0a40",
    "gu": "\u0a97\u0ac1\u0a9c\u0ab0\u0abe\u0aa4\u0ac0",
    # conjuncts that need the joiners: KA + VIRAMA + ZWJ + SSA, and KA + VIRAMA + ZWNJ + SSA
    "zwj": "\u0915\u094d" + ZWJ + "\u0937",
    "zwnj": "\u0915\u094d" + ZWNJ + "\u0937",
    "malayalam_chillu": "\u0d28\u0d4d" + ZWJ,
    "bidi_mark": "abc\u200fdef",
    # a valid astral character (one scalar; the JSON escape form is a surrogate pair)
    "astral": "\U0001f600 \U0001d11e \U0010ffff",
    "astral_key_\U0001f600": 1,
    "\u0939\u093f\u0928\u094d\u0926\u0940": "key in Devanagari",
    "": "empty key",
    "nul_and_controls": "a\u0000b\u001fc",
    "not_a_secret": "short",
}


def _spy_digest_subjects(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Switch the digest check off and record what it was asked to verify (the digest domain)."""
    base = api("base")
    subjects: list[Any] = []

    def spy(subject: Any, digest: Any) -> None:
        subjects.append(subject)

    monkeypatch.setattr(base, "verify_entity_digest", spy)
    return subjects


def document_with_rich_metadata(
    entity: str, monkeypatch: pytest.MonkeyPatch, metadata: Any, *, drop_flags: bool = False
) -> dict[str, Any]:
    """The minimal document of the entity with ``metadata`` and a content digest computed over the
    digest domain the model itself hands to the verifier (the last call is the entity's own;
    embedded entities verify first). Entities without a digest are returned as they are."""
    doc = with_metadata(entity, copy.deepcopy(metadata))
    if drop_flags:
        del doc["metadata_flags"]
    if "content_digest" not in doc:
        return doc
    model = entity_models()[entity]
    with monkeypatch.context() as patch:
        subjects = _spy_digest_subjects(patch)
        model.model_validate(doc)
    assert subjects, f"{entity} verified no digest"
    digest = api("digest").compute_digest(subjects[-1])
    doc["content_digest"] = digest.model_dump(mode="json", exclude_none=True)
    return doc


def jcs(value: Any) -> bytes:
    result: bytes = api("digest").jcs_bytes(value)
    return result


def test_the_rich_metadata_is_ijson_and_its_expectations_are_real() -> None:
    """Self-check of the fixture: it holds what the module docstring says, JCS can digest it, and
    the standard library round-trips it (so a failure below is the model's)."""
    assert jcs(RICH)
    text = json.dumps(RICH, ensure_ascii=False)
    assert json.loads(text) == RICH
    assert json.loads(json.dumps(RICH)) == RICH  # the ASCII form (surrogate pairs)
    assert ZWJ in RICH["zwj"]
    assert ZWNJ in RICH["zwnj"]
    assert max(map(ord, RICH["astral"])) > 0xFFFF
    assert all(math.isfinite(number) for number in RICH["floats"])


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_ijson_metadata_loads_and_is_kept_byte_for_byte(
    entity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested objects and arrays, integers inside the range, finite floats (also beyond 2**53),
    Indic text with ZWJ and ZWNJ and valid astral characters are accepted and come back equal, in
    the same key order, in Python and in the JSON dump, with the digest unchanged."""
    model = entity_models()[entity]
    doc = document_with_rich_metadata(entity, monkeypatch, RICH)
    loaded = model.model_validate(doc)  # the digest of the document is verified here, unpatched
    assert loaded.metadata == RICH
    assert list(loaded.metadata) == list(RICH)
    assert loaded.metadata_flags == []
    dumped = loaded.model_dump(mode="json", by_alias=True)
    assert dumped["metadata"] == RICH
    assert jcs(dumped["metadata"]) == jcs(RICH)
    assert list(dumped["metadata"]) == list(RICH)
    # python mode: the same content, not a shared mutable object with the caller's input
    assert loaded.model_dump(by_alias=True)["metadata"] == RICH
    again = model.model_validate(dumped)
    assert again.model_dump(mode="json", by_alias=True) == dumped
    if "content_digest" in doc:
        assert dumped["content_digest"]["value"] == doc["content_digest"]["value"]


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_ijson_metadata_round_trips_as_json_text(
    entity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text in, text out: ``model_validate_json`` of the ASCII-escaped form (surrogate pairs for
    the astral characters) and of the UTF-8 form, then ``model_dump_json``, then the strict
    loader; the metadata is equal each time and its JCS bytes are those of the original."""
    model = entity_models()[entity]
    doc = document_with_rich_metadata(entity, monkeypatch, RICH)
    for ensure_ascii in (True, False):
        text = json.dumps(doc, ensure_ascii=ensure_ascii)
        raw: str | bytes = text if ensure_ascii else text.encode("utf-8")
        loaded = model.model_validate_json(raw)
        assert loaded.metadata == RICH
        out = loaded.model_dump_json(by_alias=True)
        parsed = api("loader").loads_strict(out)
        assert parsed["metadata"] == RICH
        assert jcs(parsed["metadata"]) == jcs(RICH)
        assert list(parsed["metadata"]) == list(RICH)
        assert model.model_validate_json(out).metadata == RICH
        assert api("loader").load_model(model, text).metadata == RICH


@pytest.mark.parametrize("entity", DIGESTED)
def test_valid_metadata_keeps_the_digest_and_a_change_breaks_it(
    entity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest covers the metadata exactly as supplied: a document whose digest was computed
    over it loads (verified on load), and the same document with one metadata member changed is a
    digest mismatch (not silently accepted, not a metadata error)."""
    doc = document_with_rich_metadata(entity, monkeypatch, RICH)
    assert "content_digest" in doc
    model = entity_models()[entity]
    model.model_validate(doc)
    changed = copy.deepcopy(doc)
    changed["metadata"]["ints"][0] = 1
    with pytest.raises(api("errors").DigestMismatch):
        model.model_validate(changed)


def test_the_digest_walk_is_not_vacuous() -> None:
    """At least the versioned entities and the snapshot carry a digest and are checked above."""
    assert {"Persona", "ConfigSnapshot", "Evaluator", "TestProfile"} <= set(DIGESTED)
    assert len(DIGESTED) >= 8
    assert len(DIGESTED) < len(ENTITY_NAMES)


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_model_dump_never_changes_the_metadata(entity: str) -> None:
    """For any metadata the model accepts, the dump equals the input; for metadata it cannot dump
    exactly it refuses to load. The old behaviour, dumping ``NaN`` as ``null`` and a tuple as a
    list, is therefore unreachable."""
    model = entity_models()[entity]
    for case, metadata in PYTHON_REJECTED:
        try:
            loaded = model.model_validate(with_metadata(entity, metadata))
        except ValidationError:
            continue
        dumped = loaded.model_dump(mode="json")["metadata"]
        assert dumped == metadata, f"{case}: the dump changed the metadata"
        pytest.fail(f"{case}: accepted although it is not I-JSON")  # pragma: no cover


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_omitted_metadata_is_empty_and_secret_looking_strings_are_still_flagged(
    entity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behaviour that must not change: an omitted ``metadata`` is ``{}``; a secret-looking string
    in valid metadata is flagged at its pointer (flags computed when omitted, digest over the
    computed document)."""
    model = entity_models()[entity]
    plain = minimal(entity)
    del plain["metadata"]
    assert model.model_validate(plain).metadata == {}
    secret = "sk-" + "a" * 24
    doc = document_with_rich_metadata(
        entity, monkeypatch, {"outer": {"k": secret}}, drop_flags=True
    )
    assert "metadata_flags" not in doc
    loaded = model.model_validate(doc)
    assert [flag.path for flag in loaded.metadata_flags] == ["/metadata/outer/k"]
    assert loaded.metadata == {"outer": {"k": secret}}


# ------------------------------------------------------------------ the type and the schema


def test_entity_base_metadata_field_is_typed_as_an_ijson_object() -> None:
    """The base class carries the type, so a new entity cannot forget it: the field carries the
    I-JSON object rule of ``tracer_evalschema.ijson`` (``IJsonObject``)."""
    field = api("base").EntityBase.model_fields["metadata"]
    rule = api("ijson").IJsonObject.__metadata__[0]
    assert rule in field.metadata, field.metadata


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_metadata_schema_stays_an_unconstrained_object(entity: str) -> None:
    """The JSON Schema of ``metadata`` is an object with no restricting keyword: no reference, no
    ``properties``, no ``anyOf``, no pattern. (The I-JSON limits cannot be written in JSON Schema:
    NaN is not JSON text, and integer range and surrogates are code-only, as for the other I-JSON
    fields.) The architect lists the exact diff of the exported schemas."""
    exported = json.loads(api("schema_export").render_schemas()[f"{entity}.schema.json"])
    prop = exported["properties"]["metadata"]
    assert prop["type"] == "object"
    allowed = {"type", "title", "default", "description", "additionalProperties"}
    assert set(prop) <= allowed, sorted(set(prop) - allowed)
    assert prop.get("additionalProperties", True) is True
    assert prop.get("default", {}) == {}
    assert "metadata" not in exported.get("required", [])
    model = entity_models()[entity]
    for mode in ("validation", "serialization"):
        generated = model.model_json_schema(mode=mode)
        assert _metadata_schema(generated)["type"] == "object"


def _metadata_schema(schema: dict[str, Any]) -> dict[str, Any]:
    node = schema["properties"]["metadata"]
    if "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
    result: dict[str, Any] = node
    return result
