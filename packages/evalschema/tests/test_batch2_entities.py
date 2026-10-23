# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-06..P1-09 (cross-cutting): the eight entities of batch 2 behave like entities.

Every test here is parametrized over the entities Project, AgentTarget, Persona, TestProfile,
MetricDefinition, MetricBinding, Rubric and Evaluator (``_support_b2.ENTITY_SPECS``): registry
membership, prefix, versioning ``(id, version)`` with a verified ``content_digest`` (ADR 0009),
unknown fields, the schema version check, typed IDs, JSON Schema agreement, and the rule that a
closed set is an ``enum`` and never a ``const`` (ADR 0003 Decision 6).

Names are "Proposed name" unless SCOPE section 5 gives them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    BATCH2_MODELS,
    ENTITY_SPECS,
    REV8_VALUE_OBJECTS,
    VERSIONED_NAMES,
    assert_rejected,
    build,
    cls,
    contains_key,
    entity_class,
    fields_of,
    make,
    new_id,
    raw_document,
    registered_class,
    schema_accepts,
    schema_of,
    seal,
)

SPEC_IDS = [spec[1] for spec in ENTITY_SPECS]


@pytest.mark.parametrize(("module", "name", "prefix", "versioned"), ENTITY_SPECS, ids=SPEC_IDS)
def test_batch2_entity_is_registered_with_its_prefix(
    module: str, name: str, prefix: str, versioned: bool
) -> None:
    """Proposed name. The class is an ``EntityBase`` registered under its ADR 0001 prefix, and it
    is a ``VersionedEntity`` exactly when ADR 0009 Decision 5 says so."""
    model = cls(module, name)
    base = api("base")
    ids = api("ids")
    assert issubclass(model, base.EntityBase)
    assert model.id_prefix == prefix
    assert ids.ID_PREFIXES[prefix] == name
    assert api("registry").entity_classes()[prefix] is model
    assert issubclass(model, base.VersionedEntity) is versioned
    assert schema_of(model)["properties"]["id"]["pattern"].startswith(f"^{prefix}_")
    assert {"id", "schema_version", "created_at"} <= set(schema_of(model)["required"])
    if versioned:
        assert {"version", "content_digest"} <= set(schema_of(model)["required"])
        assert schema_of(model)["properties"]["version"]["minimum"] == 1


@pytest.mark.parametrize("name", SPEC_IDS)
def test_batch2_entity_roundtrip_and_schema_agree(name: str) -> None:
    """Proposed name. A valid entity survives model -> JSON -> model with byte-identical output,
    and its JSON Schema (validation and serialization mode) accepts its own dump."""
    model = entity_class(name)
    loaded = make(name)
    first = loaded.model_dump_json()
    again = model.model_validate_json(first)
    assert again == loaded
    assert again.model_dump_json() == first
    dumped = json.loads(first)
    assert dumped["id"].startswith(model.id_prefix + "_")
    assert dumped["schema_version"] == "0.0.1"
    assert dumped["created_at"].endswith("Z")
    assert dumped["metadata_flags"] == []
    for mode in ("validation", "serialization"):
        assert schema_accepts(model, dumped, mode), mode


@pytest.mark.parametrize("name", SPEC_IDS)
def test_batch2_entity_rejects_unknown_fields(name: str) -> None:
    """Proposed name (ADR 0007 I4): ``extra="forbid"``; a credential field is not ignored."""
    model = entity_class(name)
    for extra in ({"api_key": "x"}, {"unknown_field": 1}, {"password": "x"}):
        caught = assert_rejected(model, {**raw_document(name), **extra})
        assert any(e["type"] == "extra_forbidden" for e in caught.errors()), extra
    assert model.model_config.get("extra") == "forbid"
    assert schema_of(model).get("additionalProperties") is False


@pytest.mark.parametrize("name", SPEC_IDS)
def test_batch2_entity_checks_schema_version_and_id_kind(name: str) -> None:
    """Proposed name. ``schema_version`` is required and exact (typed errors); the ``id`` must be
    an ID of the entity's own kind."""
    model = entity_class(name)
    errors = api("errors")
    doc = raw_document(name)
    with pytest.raises(errors.SchemaVersionTooNew):
        model.model_validate({**doc, "schema_version": "0.0.2"})
    with pytest.raises(errors.SchemaVersionMismatch):
        model.model_validate({**doc, "schema_version": "0.0.0"})
    with pytest.raises((errors.SchemaVersionInvalid, ValidationError)):
        model.model_validate({k: v for k, v in doc.items() if k != "schema_version"})
    other = "run" if model.id_prefix != "run" else "evl"
    for bad in (new_id(other), str(api("ids").RunId.new().uuid), "", None, f"{model.id_prefix}_x"):
        assert_rejected(model, {**doc, "id": bad}, field="id")
    with pytest.raises(ValidationError):
        model.model_validate({k: v for k, v in doc.items() if k != "id"})


@pytest.mark.parametrize("name", VERSIONED_NAMES)
def test_batch2_versioned_entity_digest_is_verified(name: str) -> None:
    """ADR 0009 I1, I2: ``version`` is an integer >= 1 and the plain ``content_digest`` is
    verified on load; any change to the content without a new digest raises ``DigestMismatch``
    and a keyed digest cannot be verified here (``DigestKeyUnavailable``)."""
    model = entity_class(name)
    errors = api("errors")
    doc = build(name)
    assert model.model_validate(doc).content_digest.value == doc["content_digest"]["value"]
    assert seal(model, doc) == doc, "the digest is a function of the content"
    for tampered in (
        {**doc, "version": 2},
        {**doc, "metadata": {"added": 1}, "metadata_flags": []},
        {**doc, "created_at": "2026-10-22T10:00:01.000000Z"},
    ):
        with pytest.raises(errors.DigestMismatch):
            model.model_validate(tampered)
    wrong = {**doc, "content_digest": {"scheme": "sha256-jcs/v1", "value": "0" * 64}}
    with pytest.raises(errors.DigestMismatch):
        model.model_validate(wrong)
    keyed = {
        "scheme": "hmac-sha256-jcs/v1",
        "value": "0" * 64,
        "key_ref": {"key_id": "tracer-test-key"},
    }
    with pytest.raises(errors.DigestKeyUnavailable):
        model.model_validate({**doc, "content_digest": keyed})
    for bad_version in (0, -1, 1.5, "1", True, None):
        assert_rejected(model, {**raw_document(name), "version": bad_version}, field="version")
    # a new version of the same logical object: same id, version + 1, its own digest
    second = seal(model, {**doc, "version": 2})
    assert second["id"] == doc["id"]
    assert second["content_digest"] != doc["content_digest"]
    assert model.model_validate(second).version == 2


def test_project_is_not_versioned() -> None:
    """ADR 0009 Decision 5: Project is a record of fact: no ``version`` and no ``content_digest``
    (an unknown field to it)."""
    project = entity_class("Project")
    doc = raw_document("Project")
    for extra in (
        {"version": 1},
        {"content_digest": {"scheme": "sha256-jcs/v1", "value": "0" * 64}},
    ):
        assert_rejected(project, {**doc, **extra})


def test_batch2_models_emit_enum_not_const() -> None:
    """Proposed name (the repository-wide ``test_no_const_in_generated_schemas`` comes with the
    schema export). ADR 0003 Decision 6: closed sets and union tags are ``{"type": "string",
    "enum": [...]}``, never ``const``, in validation and serialization mode."""
    models = [cls(module, name) for module, name in BATCH2_MODELS]
    models += [registered_class(name) for name in REV8_VALUE_OBJECTS]
    for model in models:
        for mode in ("validation", "serialization"):
            assert not contains_key(schema_of(model, mode), "const"), f"{model.__name__} ({mode})"


def test_batch2_models_are_all_found_by_the_registry_walk() -> None:
    """Proposed name. ``registry.model_classes()`` reaches every model batch 2 adds, so the
    whole-model walks (secret names, typed IDs, ``_ms`` schemas) cover them."""
    found = {m.__name__ for m in api("registry").model_classes()}
    assert {name for _module, name in BATCH2_MODELS} <= found
    assert set(REV8_VALUE_OBJECTS) <= found, "the revision 8 value objects (api-surface 19.4.1)"
    values = api("registry").value_object_classes()
    for name in (
        "ProviderConfigRef",
        "DeclaredStackEntry",
        "TestProfileRef",
        "RangeRule",
        "EqualsTrueRule",
        "OneOfRule",
        "PerTurnCounts",
        "ScriptedTurn",
        "ExpectedToolCall",
        *REV8_VALUE_OBJECTS,
    ):
        assert name in values, name
    entities: Any = api("registry").entity_classes()
    for _module, name, prefix, _versioned in ENTITY_SPECS:
        assert entities[prefix].__name__ == name


# ================================================================================ revision 8
# Typed references (api-surface 19.4.1, N6) and the digest of a reference between definitions (E5).

TYPED_REFS = (("PersonaRef", "per"), ("MetricDefinitionRef", "met"), ("MetricBindingRef", "mbd"))
PLAIN = {"scheme": "sha256-jcs/v1", "value": "a" * 64}
KEYED = {"scheme": "hmac-sha256-jcs/v1", "value": "b" * 64, "key_ref": {"key_id": "tracer-key-1"}}


@pytest.mark.parametrize(("name", "prefix"), TYPED_REFS, ids=[t[0] for t in TYPED_REFS])
def test_typed_reference_narrows_the_id_kind(name: str, prefix: str) -> None:
    """Section 19.4.1 and N6 (ADR 0009 Decision 4, I9a): each typed reference is a subclass of the
    generic ``VersionedRef`` whose ``id`` is the typed ID of one kind, ``{id, version >= 1,
    content_digest?}`` and nothing else. It is always a JSON object: a string, another kind's ID, a
    bare UUID and a missing or non-positive version are rejected, by pydantic and by the JSON
    Schema (``id`` carries the ``^<prefix>_`` pattern). Under E5 (accepted by the owner) the digest
    is optional, and a plain or keyed digest is accepted when present. Proposed name."""
    model = registered_class(name)
    ids = api("ids")
    own = ids.id_type(prefix)
    assert issubclass(model, api("base").VersionedRef)
    assert fields_of(model) == {"id", "version", "content_digest"}
    assert not {"schema_version", "created_at", "metadata"} & fields_of(model)
    target = str(own.new())
    loaded = model.model_validate({"id": target, "version": 3})
    assert isinstance(loaded.id, own)
    assert (loaded.version, loaded.content_digest) == (3, None)
    assert model.model_validate_json(loaded.model_dump_json()) == loaded
    with_plain = model.model_validate({"id": target, "version": 1, "content_digest": PLAIN})
    assert with_plain.content_digest.scheme == "sha256-jcs/v1"
    with_keyed = model.model_validate({"id": target, "version": 1, "content_digest": KEYED})
    assert with_keyed.content_digest.key_ref.key_id == "tracer-key-1"
    others = [
        new_id(other) for other in ("per", "met", "mbd", "evl", "tpf", "run") if other != prefix
    ]
    for bad_id in (*others, str(own.new().uuid), "", f"{prefix}_x", None):
        with pytest.raises(ValidationError):
            model.model_validate({"id": bad_id, "version": 1})
    for text in (target, f"{target}@1", "p95_latency@1", "1"):
        with pytest.raises(ValidationError):
            model.model_validate(text)
        with pytest.raises(ValidationError):
            model.model_validate_json(f'"{text}"')
    for bad_version in (None, 0, -1, 1.5, "1", "1.0.0", True):
        with pytest.raises(ValidationError):
            model.model_validate({"id": target, "version": bad_version})
    for missing in ("id", "version"):
        with pytest.raises(ValidationError):
            model.model_validate(
                {k: v for k, v in {"id": target, "version": 1}.items() if k != missing}
            )
    for bad_digest in (
        {**PLAIN, "value": "A" * 64},
        {**PLAIN, "key_ref": {"key_id": "k"}},
        {"scheme": "hmac-sha256-jcs/v1", "value": "b" * 64},
        {"scheme": "sha256-jcs/v1"},
    ):
        with pytest.raises(ValidationError):
            model.model_validate({"id": target, "version": 1, "content_digest": bad_digest})
    with pytest.raises(ValidationError):
        model.model_validate({"id": target, "version": 1, "key": "p95_latency"})
    schema = schema_of(model)
    assert set(schema["required"]) == {"id", "version"}
    assert schema.get("additionalProperties") is False
    assert schema["properties"]["id"]["pattern"].startswith(f"^{prefix}_")
    assert schema_accepts(model, {"id": target, "version": 2})
    assert not schema_accepts(model, {"id": others[0], "version": 2})
    assert not schema_accepts(model, {"id": target})


def _referenced(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """A complete entity document and a reference to it that carries the entity's digest."""
    entity = build(name)
    return entity, {
        "id": entity["id"],
        "version": entity["version"],
        "content_digest": entity["content_digest"],
    }


def _dumped_reference(holder: str, field: str, reference: dict[str, Any]) -> dict[str, Any]:
    """The reference as it appears in the JSON of an entity that holds it in ``field``."""
    value: Any = [reference] if field == "metric_bindings" else reference
    dumped = make(holder, **{field: value}).model_dump(mode="json", exclude_none=True)
    result: dict[str, Any] = dumped[field][0] if field == "metric_bindings" else dumped[field]
    return result


def _item_binding(rubric: Any) -> dict[str, Any]:
    dumped = rubric.model_dump(mode="json", exclude_none=True)
    reference: dict[str, Any] = dumped["items"][0]["binding"]
    return reference


def test_reference_digest_is_optional_and_verified_when_present() -> None:
    """E5 (accepted by the owner; ADR 0009 Decision 4, I7): a reference between definitions may omit
    ``content_digest``; when it is present it is verified against the resolved entity. The check is
    the bundle's (``check_bundle_consistency`` over the references in the documents of a bundle),
    shown here for the references an evaluator, a binding and a rubric make. Proposed name."""
    errors = api("errors")
    check = api("bundle").check_bundle_consistency
    wrong = {"scheme": "sha256-jcs/v1", "value": "0" * 64}
    cases = [
        ("Persona", "Evaluator", "persona"),
        ("MetricDefinition", "MetricBinding", "metric"),
        ("MetricBinding", "Evaluator", "metric_bindings"),
    ]
    for name, holder, field in cases:
        entity, reference = _referenced(name)
        without = {"id": reference["id"], "version": reference["version"]}
        shown = _dumped_reference(holder, field, without)
        assert shown["id"] == reference["id"]
        assert shown.get("content_digest") is None
        check([entity], [shown])
        carried = _dumped_reference(holder, field, reference)
        assert carried["content_digest"] == reference["content_digest"]
        check([entity], [carried])
        forged = _dumped_reference(holder, field, {**reference, "content_digest": wrong})
        with pytest.raises(errors.RefDigestMismatchError) as caught:
            check([entity], [forged])
        assert entity["id"] in str(caught.value)
    binding_doc, binding_ref = _referenced("MetricBinding")
    rubric = make("Rubric", items=[{"binding": binding_ref, "required": True}])
    check([binding_doc], [_item_binding(rubric)])
    forged_ref = {**binding_ref, "content_digest": wrong}
    forged = make("Rubric", items=[{"binding": forged_ref, "required": True}])
    with pytest.raises(errors.RefDigestMismatchError):
        check([binding_doc], [_item_binding(forged)])
