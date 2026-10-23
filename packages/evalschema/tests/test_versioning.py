# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-05: VersionedEntity, VersionedRef and bundle consistency (ADR 0006, 0008, 0009).

Assumed public API (docs/phase1/api-surface.md): ``tracer_evalschema.base.VersionedEntity``
(``version`` int >= 1, ``content_digest`` verified on load) and ``VersionedRef{id, version,
content_digest?}``; ``tracer_evalschema.bundle.check_bundle_consistency(docs, refs=())`` working on
JSON-mode documents, and the typed ``BundleError`` family in ``tracer_evalschema.errors``;
``tracer_evalschema.registry.entity_classes()`` for the entity-set check.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api, entity_doc, redigest, versioned_doc, widget_classes

ADR_VERSIONED = {
    "Persona",
    "TestProfile",
    "Evaluator",
    "MetricDefinition",
    "MetricBinding",
    "Rubric",
    "PricingCatalog",
    "AgentTarget",
    "Dataset",
}


def _versioned() -> Any:
    return widget_classes().VersionedWidget


def test_version_int_ge_1() -> None:
    widget = _versioned()
    for good in (1, 2, 17, 2**31):
        loaded = widget.model_validate(versioned_doc(version=good))
        assert loaded.version == good
        assert isinstance(loaded.version, int)
    for bad in (0, -1, 1.5, "1", True, None, 1.0):
        with pytest.raises(ValidationError):
            widget.model_validate(redigest(entity_doc("per", version=bad)))
    with pytest.raises(ValidationError):
        widget.model_validate({k: v for k, v in versioned_doc().items() if k != "version"})
    assert widget.model_json_schema()["properties"]["version"]["minimum"] == 1


def test_versioned_content_digest_mismatch_rejected() -> None:
    errors = api("errors")
    widget = _versioned()
    doc = versioned_doc()
    assert widget.model_validate(doc).content_digest.value == doc["content_digest"]["value"]
    # content changed, digest not recomputed
    for tampered in (
        {**doc, "name": "other"},
        {**doc, "version": 2},
        {**doc, "metadata": {"extra": 1}},
        {**doc, "created_at": "2026-10-22T10:00:01.000000Z"},
        {**doc, "note": "added"},
    ):
        with pytest.raises(errors.DigestMismatch):
            widget.model_validate(tampered)
    # digest replaced with a well-formed but wrong one
    wrong = {**doc, "content_digest": {"scheme": "sha256-jcs/v1", "value": "0" * 64}}
    with pytest.raises(errors.DigestMismatch):
        widget.model_validate(wrong)
    # a missing digest is not "unverified, accept"
    with pytest.raises((ValidationError, errors.TracerSchemaError)):
        widget.model_validate({k: v for k, v in doc.items() if k != "content_digest"})
    # a forged digest cannot be repaired by changing only the stored value's case
    upper = {
        **doc,
        "content_digest": {
            **doc["content_digest"],
            "value": doc["content_digest"]["value"].upper(),
        },
    }
    with pytest.raises((ValidationError, errors.TracerSchemaError)):
        widget.model_validate(upper)
    assert issubclass(errors.DigestMismatch, errors.TracerSchemaError)


def test_ref_requires_version() -> None:
    base = api("base")
    ids = api("ids")
    persona_id = str(ids.PersonaId.new())
    ref = base.VersionedRef(id=persona_id, version=1)
    assert str(ref.id) == persona_id
    assert ref.version == 1
    assert ref.content_digest is None
    with_digest = base.VersionedRef(
        id=persona_id,
        version=3,
        content_digest=api("digest").compute_digest({"a": 1}),
    )
    assert with_digest.content_digest.scheme == "sha256-jcs/v1"
    for bad in (
        {"id": persona_id},
        {"id": persona_id, "version": None},
        {"id": persona_id, "version": 0},
        {"id": persona_id, "version": -1},
        {"id": persona_id, "version": "1"},
        {"id": persona_id, "version": 1.5},
        {"version": 1},
        {"id": str(ids.PersonaId.new().uuid), "version": 1},  # bare UUID
        {"id": persona_id, "version": 1, "extra": True},
    ):
        with pytest.raises(ValidationError):
            base.VersionedRef.model_validate(bad)
    # a reference is a value object: no entity fields
    assert not {"schema_version", "created_at", "metadata"} & set(base.VersionedRef.model_fields)


def test_versioned_entity_set_matches_adr() -> None:
    registry = api("registry")
    base = api("base")
    versioned = {
        cls.__name__
        for cls in registry.entity_classes().values()
        if issubclass(cls, base.VersionedEntity)
    }
    assert versioned == ADR_VERSIONED
    records_of_fact = {
        cls.__name__
        for cls in registry.entity_classes().values()
        if not issubclass(cls, base.VersionedEntity)
    }
    assert records_of_fact.isdisjoint(ADR_VERSIONED)
    # ConfigSnapshot is a digested record of fact: not versioned, but it carries a digest field
    assert "ConfigSnapshot" in records_of_fact


# ------------------------------------------------------------------------------ bundles


def _check(docs: list[dict[str, Any]], refs: list[dict[str, Any]] | None = None) -> None:
    api("bundle").check_bundle_consistency(docs, refs or [])


def test_bundle_mixed_versions_rejected() -> None:
    errors = api("errors")
    first = entity_doc("evl")
    second = entity_doc("evl")
    _check([first, second])  # same version: fine
    for other in ("0.0.0", "0.0.2", "0.1.0"):
        with pytest.raises(errors.MixedSchemaVersionError) as caught:
            _check([first, {**second, "schema_version": other}])
        assert other in str(caught.value)
    # order does not matter
    with pytest.raises(errors.MixedSchemaVersionError):
        _check([{**second, "schema_version": "0.0.2"}, first, entity_doc("evl")])
    assert issubclass(errors.MixedSchemaVersionError, errors.BundleError)
    assert issubclass(errors.BundleError, errors.TracerSchemaError)
    _check([])  # an empty bundle is consistent


def test_bundle_rejects_same_id_version_different_digest() -> None:
    errors = api("errors")
    original = versioned_doc(name="v1")
    edited = redigest(
        {k: v for k, v in original.items() if k != "content_digest"} | {"name": "edited"}
    )
    assert edited["id"] == original["id"]
    assert edited["version"] == original["version"]
    assert edited["content_digest"] != original["content_digest"]
    with pytest.raises(errors.ConflictingVersionError) as caught:
        _check([original, edited])
    assert original["id"] in str(caught.value)
    # the same record twice is fine; a new version of the same logical object is fine
    _check([original, dict(original)])
    next_version = redigest(
        {k: v for k, v in original.items() if k != "content_digest"}
        | {"version": 2, "name": "edited"}
    )
    _check([original, next_version])
    # different objects with equal contents but different ids are fine
    _check([original, versioned_doc(name="v1")])
    assert issubclass(errors.ConflictingVersionError, errors.BundleError)


def test_bundle_rejects_same_record_id_different_content() -> None:
    """P-47 correction rule: a record of fact is corrected by a new record with a new id."""
    errors = api("errors")
    record = entity_doc("evl", name="original")
    changed = {**record, "name": "corrected"}
    with pytest.raises(errors.ConflictingRecordError) as caught:
        _check([record, changed])
    assert record["id"] in str(caught.value)
    with pytest.raises(errors.ConflictingRecordError):
        _check([record, {**record, "created_at": "2026-10-22T10:00:01.000000Z"}])
    with pytest.raises(errors.ConflictingRecordError):
        _check([record, {**record, "metadata": {"k": 1}}])
    # identical duplicates are fine, and so is a correction under a new id
    _check([record, dict(record)])
    _check([record, entity_doc("evl", name="corrected")])
    assert issubclass(errors.ConflictingRecordError, errors.BundleError)


def test_ref_digest_must_match_resolved_entity_in_bundle() -> None:
    errors = api("errors")
    entity = versioned_doc(name="profile")
    good_ref = {"id": entity["id"], "version": 1, "content_digest": entity["content_digest"]}
    _check([entity], [good_ref])
    # a digest that does not match the resolved entity is rejected
    other = redigest({k: v for k, v in entity.items() if k != "content_digest"} | {"name": "other"})
    bad_ref = {**good_ref, "content_digest": other["content_digest"]}
    with pytest.raises(errors.RefDigestMismatchError) as caught:
        _check([entity], [bad_ref])
    assert entity["id"] in str(caught.value)
    # an absent digest is allowed (shared exports drop it); it is not a mismatch
    _check([entity], [{"id": entity["id"], "version": 1}])
    # an unresolvable reference is reported by readers, not a bundle failure
    _check(
        [entity],
        [{"id": versioned_doc()["id"], "version": 1, "content_digest": other["content_digest"]}],
    )
    _check(
        [entity], [{"id": entity["id"], "version": 9, "content_digest": other["content_digest"]}]
    )
    _check([], [good_ref])
    # a version-less reference is malformed
    with pytest.raises((errors.BundleError, ValidationError, ValueError, KeyError)):
        _check([entity], [{"id": entity["id"], "content_digest": entity["content_digest"]}])
    assert issubclass(errors.RefDigestMismatchError, errors.BundleError)
