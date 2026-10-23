# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-10a..c, P1-11, P1-12, P1-13, P1-22 (cross-cutting): the twelve entities of batch 3 behave
like entities.

Every test here is parametrized over Artifact, Transcript, Turn, Exchange, ToolCallEvent,
StageSpan, MetricResult, RunVerdict, ConfigSnapshot, Run, Dataset and DatasetItem
(``_support_b3.ENTITY_SPECS3``): registry membership, prefix, versioning (ADR 0009: Dataset is
versioned, the others are records of fact, ConfigSnapshot is digested but not versioned), unknown
fields, the schema version check, typed IDs, JSON Schema agreement, and the rule that a closed
set is an ``enum`` and never a ``const`` (ADR 0003 Decision 6).

All names are "Proposed name".
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import contains_key, schema_accepts, schema_of, seal
from ._support_b3 import (
    ENTITY_SPECS3,
    VERSIONED_NAMES3,
    assert_rejected3,
    cls3,
    doc3,
    entity_class3,
    make3,
    new_id,
)

SPEC_IDS = [spec[1] for spec in ENTITY_SPECS3]

# Value objects the batch 3 entities embed (module free; found by name in the registry walk).
BATCH3_VALUE_OBJECTS = (
    "PrivacyMarker",
    "ConsentRecord",
    "RetentionPolicy",
    "CallerExternalId",
    "AudioFormat",
    "ChannelMap",
    "ChannelAssignment",
    "WallTime",
    "TimelineOrigin",
    "Segment",
    "LanguageDeclaration",
    "EvidenceRef",
    "IngestWarning",
    "ResultError",
    "ScorerInfo",
    "JudgeInfo",
    "JudgeTurnVerdict",
    "PerTurnSummary",
    "VerdictCheck",
    "WeightedExclusion",
    "PricingCatalogRef",
    "AgentTargetRef",
)


@pytest.mark.parametrize(("module", "name", "prefix", "versioned"), ENTITY_SPECS3, ids=SPEC_IDS)
def test_batch3_entity_is_registered_with_its_prefix(
    module: str, name: str, prefix: str, versioned: bool
) -> None:
    """The class is an ``EntityBase`` registered under its ADR 0001 prefix, a ``VersionedEntity``
    exactly when ADR 0009 Decision 5 says so (only Dataset), and its JSON Schema carries the
    prefix pattern for ``id`` and requires the entity fields."""
    model = cls3(module, name)
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
    else:
        assert "version" not in model.model_fields


@pytest.mark.parametrize("name", SPEC_IDS)
def test_batch3_entity_roundtrip_and_schema_agree(name: str) -> None:
    """A valid entity survives model -> JSON -> model with byte-identical output, the dump has the
    canonical id, schema version and UTC time forms, and its JSON Schema (validation and
    serialization mode) accepts its own dump."""
    model = entity_class3(name)
    loaded = make3(name)
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
def test_batch3_entity_rejects_unknown_fields(name: str) -> None:
    """ADR 0007 I4: ``extra="forbid"``; a credential field or an arbitrary one is not ignored, and
    the JSON Schema says ``additionalProperties: false``."""
    model = entity_class3(name)
    for extra in ({"api_key": "x"}, {"unknown_field": 1}, {"password": "x"}):
        caught = assert_rejected3(model, doc3(name, **extra))
        assert any(e["type"] == "extra_forbidden" for e in caught.errors()), extra
    assert model.model_config.get("extra") == "forbid"
    assert schema_of(model).get("additionalProperties") is False


@pytest.mark.parametrize("name", SPEC_IDS)
def test_batch3_entity_checks_schema_version_and_id_kind(name: str) -> None:
    """``schema_version`` is required and exact (typed errors, not wrapped by pydantic); the ``id``
    must be an ID of the entity's own kind: another kind, a bare UUID, an empty text and ``None``
    are rejected, and a missing ``id`` too."""
    model = entity_class3(name)
    errors = api("errors")
    doc = doc3(name)
    with pytest.raises(errors.SchemaVersionTooNew):
        model.model_validate({**doc, "schema_version": "0.0.2"})
    with pytest.raises(errors.SchemaVersionMismatch):
        model.model_validate({**doc, "schema_version": "0.0.0"})
    with pytest.raises((errors.SchemaVersionInvalid, ValidationError)):
        model.model_validate({k: v for k, v in doc.items() if k != "schema_version"})
    other = "run" if model.id_prefix != "run" else "evl"
    uuid_text = str(api("ids").RunId.new().uuid)
    for bad in (new_id(other), uuid_text, "", None, f"{model.id_prefix}_x"):
        assert_rejected3(model, {**doc, "id": bad}, field="id")
    with pytest.raises(ValidationError):
        model.model_validate({k: v for k, v in doc.items() if k != "id"})


@pytest.mark.parametrize("name", [*VERSIONED_NAMES3, "ConfigSnapshot"])
def test_batch3_digested_entity_digest_is_verified(name: str) -> None:
    """ADR 0009 I2, ADR 0006 I4: a versioned entity (Dataset) and the digested snapshot verify their
    ``content_digest`` on load: changing the content without a new digest raises the typed
    ``DigestMismatch`` and a keyed digest cannot be verified here (``DigestKeyUnavailable``). The
    Dataset's version is an integer of at least 1."""
    model = entity_class3(name)
    errors = api("errors")
    tampers: tuple[dict[str, Any], ...]
    if name == "ConfigSnapshot":
        doc = doc3(name)
        tampers = (
            {**doc, "metadata": {"added": 1}, "metadata_flags": []},
            {**doc, "created_at": "2026-10-22T10:00:01.000000Z"},
        )
    else:
        doc = seal(model, doc3(name))
        tampers = (
            {**doc, "version": 2},
            {**doc, "metadata": {"added": 1}, "metadata_flags": []},
            {**doc, "created_at": "2026-10-22T10:00:01.000000Z"},
        )
    assert model.model_validate(doc).content_digest.value == doc["content_digest"]["value"]
    for tampered in tampers:
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
    if name != "ConfigSnapshot":
        for bad_version in (0, -1, 1.5, "1", True, None):
            assert_rejected3(model, {**doc3(name), "version": bad_version}, field="version")
        second = seal(model, {**doc, "version": 2})
        assert second["id"] == doc["id"]
        assert second["content_digest"] != doc["content_digest"]
        assert model.model_validate(second).version == 2


@pytest.mark.parametrize(
    "name", [n for n in SPEC_IDS if n not in (*VERSIONED_NAMES3, "ConfigSnapshot")]
)
def test_batch3_records_of_fact_are_not_versioned(name: str) -> None:
    """ADR 0009 Decision 5: a record of fact has no ``version`` and no ``content_digest`` (they are
    unknown fields to it); a correction is a new record with a new ID."""
    model = entity_class3(name)
    for extra in (
        {"version": 1},
        {"content_digest": {"scheme": "sha256-jcs/v1", "value": "0" * 64}},
    ):
        assert_rejected3(model, {**doc3(name), **extra})


def test_batch3_models_emit_enum_not_const() -> None:
    """ADR 0003 Decision 6 (the repository-wide ``test_no_const_in_generated_schemas`` comes with
    the schema export): closed sets and union tags of every batch 3 model are ``{"type": "string",
    "enum": [...]}`` and never ``const``, in validation and serialization mode."""
    registered = {m.__name__: m for m in api("registry").model_classes()}
    models: list[Any] = [entity_class3(name) for name in SPEC_IDS]
    for name in BATCH3_VALUE_OBJECTS:
        assert name in registered, f"{name} is not implemented yet"
        models.append(registered[name])
    for model in models:
        for mode in ("validation", "serialization"):
            assert not contains_key(schema_of(model, mode), "const"), f"{model.__name__} ({mode})"


def test_batch3_models_are_all_found_by_the_registry_walk() -> None:
    """``registry.model_classes()`` reaches every model batch 3 adds, so the whole-model walks
    (secret names, typed IDs, ``_ms`` schemas, the human-subject walk) cover them; entities are
    entities and value objects are value objects."""
    registry = api("registry")
    found = {m.__name__ for m in registry.model_classes()}
    assert set(SPEC_IDS) <= found
    values = registry.value_object_classes()
    for name in BATCH3_VALUE_OBJECTS:
        assert name in values, name
    entities = registry.entity_classes()
    for _module, name, prefix, _versioned in ENTITY_SPECS3:
        assert entities[prefix].__name__ == name
        assert name not in values
