# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-12: ConfigSnapshot, PricingCatalogRef and the profile lint (ADR 0006 Decisions 1, 2, 5, 6 and
9, ADR 0009 Decision 4, ADR 0013 Decision 2).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.snapshot``:
``ConfigSnapshot`` (``snap``; a digested record of fact: no ``version``, a required
``content_digest``) embedding ``evaluator``, ``persona``, ``metric_definitions``,
``metric_bindings`` and ``rubric`` and holding an optional ``pricing_catalog``
(``PricingCatalogRef{id, version, content_digest}``); ``snapshot_digest_domain(doc) -> dict`` and
``compute_snapshot_digest(doc) -> ContentDigest`` over the JSON-mode document (the digest domain
of ADR 0006 Decision 5, defined on JSON alone); ``check_internal_snapshot(snapshot)``;
``check_pricing_catalog_ref(ref, catalog_doc)`` and
``lint_profile_values_in_free_text(snapshot, profile) -> list[IngestWarning]``.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from typing import Any

import pytest
import rfc8785
from pydantic import ValidationError

from ._support import PACKAGE_DIR, ROOT, api
from ._support_b2 import (
    JUDGE_TEMPLATE,
    build,
    contains_key,
    embedded_models,
    entity_class,
    judge_definition,
    make,
    new_id,
    raw_document,
    registered_class,
    schema_accepts,
    schema_of,
    without_profile_digest,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    assert_rejected3,
    entity_class3,
    marked_id,
    marked_set,
    marked_tag,
    snapshot_doc,
    snapshot_parts,
)

PLAIN = {"scheme": "sha256-jcs/v1", "value": "a" * 64}
OTHER_PLAIN = {"scheme": "sha256-jcs/v1", "value": "b" * 64}
KEYED = {"scheme": "hmac-sha256-jcs/v1", "value": "c" * 64, "key_ref": {"key_id": "tracer-key-1"}}


def _snapshot(doc: dict[str, Any]) -> Any:
    return entity_class3("ConfigSnapshot").model_validate(doc)


def _doc(**parts: Any) -> dict[str, Any]:
    return snapshot_doc(snapshot_parts(**parts))


def _reject(doc: dict[str, Any], field: str | None = None) -> None:
    assert_rejected3(entity_class3("ConfigSnapshot"), doc, field=field)


def _independent_domain(doc: dict[str, Any]) -> dict[str, Any]:
    """ADR 0006 Decision 5 as a verifier in another language would write it: only JSON.

    Removes the top-level ``content_digest`` and ``schema_version``, the ``schema_version`` of
    each embedded entity, the ``content_digest`` of the evaluator's ``TestProfileRef``, then every
    object member that is ``null`` at any depth (array elements are kept)."""
    body = copy.deepcopy(doc)
    for key in ("content_digest", "schema_version"):
        body.pop(key, None)
    for key in ("evaluator", "persona", "rubric"):
        body[key].pop("schema_version", None)
    for key in ("metric_definitions", "metric_bindings"):
        for item in body[key]:
            item.pop("schema_version", None)
    profile = body["evaluator"].get("test_profile")
    if isinstance(profile, dict):
        profile.pop("content_digest", None)

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    stripped: dict[str, Any] = strip(body)
    return stripped


def _independent_hex(doc: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(_independent_domain(doc))).hexdigest()


# ----------------------------------------------------------------------------- the entity


def test_snapshot_shape() -> None:
    """Proposed name (ADR 0006 Decision 1, ADR 0009 Decision 5): a snapshot is an entity (``snap``)
    that is digested but not versioned: no ``version``, a required ``content_digest``. It embeds
    the evaluator, the persona, the definitions, the bindings and the rubric, and holds only
    references for the rest: its fields are exactly those, and it round-trips through JSON and its
    JSON Schema."""
    model = entity_class3("ConfigSnapshot")
    entity_fields = {"id", "schema_version", "created_at", "metadata", "metadata_flags"}
    assert set(model.model_fields) - entity_fields == {
        "content_digest",
        "evaluator",
        "persona",
        "metric_definitions",
        "metric_bindings",
        "rubric",
        "pricing_catalog",
    }
    assert "version" not in model.model_fields
    assert model.id_prefix == "snap"
    assert "content_digest" in schema_of(model)["required"]
    base = api("base")
    assert issubclass(model, base.EntityBase)
    assert not issubclass(model, base.VersionedEntity)
    assert api("registry").entity_classes()["snap"] is model
    doc = _doc()
    loaded = _snapshot(doc)
    assert str(loaded.id).startswith("snap_")
    assert loaded.pricing_catalog is None
    assert model.model_validate_json(loaded.model_dump_json()) == loaded
    assert schema_accepts(model, json.loads(loaded.model_dump_json()))
    for missing in ("content_digest", "evaluator", "persona", "metric_definitions", "rubric"):
        _reject({k: v for k, v in doc.items() if k != missing}, missing)
    for field in ("metric_definitions", "metric_bindings"):
        _reject({**doc, field: []})
    _reject({**doc, "version": 1})
    _reject({**doc, "test_profile": {"id": new_id("tpf"), "version": 1}})


def test_snapshot_pii_walk_zero() -> None:
    """ADR 0006 I1, gate item 10: the ConfigSnapshot schema (embedded entities and references
    included, both modes) has no ``x-pii`` field, the snapshot embeds no ``TestProfile`` (only
    a ``TestProfileRef``), and the values of the referenced profile appear nowhere in the
    snapshot document. A positive control: the profile itself has ``x-pii`` fields."""
    model = entity_class3("ConfigSnapshot")
    for mode in ("validation", "serialization"):
        assert not contains_key(schema_of(model, mode), "x-pii"), mode
    embedded = {m.__name__ for m in embedded_models(model)}
    assert {"Evaluator", "Persona", "MetricDefinition", "MetricBinding", "Rubric"} <= embedded
    assert {"TestProfileRef"} <= embedded
    assert "TestProfile" not in embedded
    assert contains_key(schema_of(entity_class("TestProfile")), "x-pii"), "positive control"
    parts = snapshot_parts(with_profile=True, profile_digest=PLAIN)
    profile = parts["profile"]
    text = json.dumps(snapshot_doc(parts), ensure_ascii=False)
    for field in ("name", "dob", "account_number"):
        assert getattr(profile, field) not in text
    loaded = _snapshot(snapshot_doc(parts))
    assert loaded.evaluator.test_profile.id == profile.id
    assert getattr(loaded.evaluator.test_profile, "name", None) is None


# ----------------------------------------------------------------------------- the digest


def test_snapshot_jcs_digest_stable() -> None:
    """ADR 0006 I3, gate item 10: the snapshot digest is the plain SHA-256 over the RFC 8785 (JCS)
    bytes of the digest domain, computed here independently from the JSON document alone: it
    equals the stored ``content_digest`` and ``compute_snapshot_digest``, does not depend on key
    order or on the process (a fresh interpreter computes the same value), and changes when the
    content changes."""
    doc = _doc()
    module = api("snapshot")
    expected = _independent_hex(doc)
    assert doc["content_digest"] == {"scheme": "sha256-jcs/v1", "value": expected}
    computed = module.compute_snapshot_digest(doc)
    assert (computed.scheme, computed.value) == ("sha256-jcs/v1", expected)
    assert computed.key_ref is None
    reordered = {key: doc[key] for key in reversed(list(doc))}
    assert module.compute_snapshot_digest(reordered).value == expected
    assert module.compute_snapshot_digest(json.loads(json.dumps(doc))).value == expected
    assert _snapshot(doc).content_digest.value == expected
    script = (
        "import json, sys\n"
        "from tracer_evalschema.snapshot import compute_snapshot_digest\n"
        "print(compute_snapshot_digest(json.load(sys.stdin)).value)\n"
    )
    done = subprocess.run(  # noqa: S603 (the interpreter running the tests)
        [sys.executable, "-c", script],
        input=json.dumps(doc),
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
        env=_child_env(),
    )
    assert done.stdout.strip() == expected
    changed = {**doc, "metadata": {"note": "changed"}}
    assert module.compute_snapshot_digest(changed).value != expected
    assert module.snapshot_digest_domain(doc) == _independent_domain(doc)
    domain = module.snapshot_digest_domain(doc)
    assert "content_digest" not in domain
    assert "schema_version" not in domain


def _child_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return env


def test_snapshot_forged_digest_rejected() -> None:
    """A-09, ADR 0006 I4: a snapshot whose ``content_digest`` is not the digest of its content is
    rejected with the typed ``DigestMismatch``: a wrong hex value, a changed top-level field, an
    embedded record replaced by another valid record (the embedded digests are inside the
    domain, so each version used is pinned). A keyed digest cannot be verified without a key
    (``DigestKeyUnavailable``) and an unregistered scheme is ``UnknownDigestScheme``."""
    errors = api("errors")
    model = entity_class3("ConfigSnapshot")
    parts = snapshot_parts()
    doc = snapshot_doc(parts)
    assert _snapshot(doc).content_digest.value == doc["content_digest"]["value"]
    wrong = {"scheme": "sha256-jcs/v1", "value": "0" * 64}
    with pytest.raises(errors.DigestMismatch):
        model.model_validate({**doc, "content_digest": wrong})
    with pytest.raises(errors.DigestMismatch):
        model.model_validate({**doc, "metadata": {"added": 1}, "metadata_flags": []})
    with pytest.raises(errors.DigestMismatch):
        model.model_validate({**doc, "created_at": "2026-10-22T10:00:01.000000Z"})
    other = make(
        "Evaluator",
        id=str(parts["evaluator"].id),
        persona={"id": str(parts["persona"].id), "version": 1},
        metric_bindings=[{"id": str(b.id), "version": 1} for b in parts["bindings"]],
        instructions="A different instruction that is a perfectly valid evaluator.",
    )
    replaced = {**doc, "evaluator": other.model_dump(mode="json", by_alias=True)}
    with pytest.raises(errors.DigestMismatch):
        model.model_validate(replaced)
    with pytest.raises(errors.DigestKeyUnavailable):
        model.model_validate({**doc, "content_digest": KEYED})
    with pytest.raises(errors.UnknownDigestScheme):
        model.model_validate({**doc, "content_digest": {"scheme": "md5-jcs/v1", "value": "0" * 64}})
    # the forged document is fine again once the digest is recomputed for its content
    resealed = snapshot_doc(parts, metadata={"added": 1}, metadata_flags=[])
    assert _snapshot(resealed).metadata == {"added": 1}


def test_snapshot_digest_independent_of_profile_digest() -> None:
    """P-17 (ADR 0006 Decision 5, I20): every ``TestProfileRef`` inside a snapshot contributes only
    ``{id, version}`` to the snapshot digest; its ``content_digest`` is outside the domain, so a
    plain digest, another plain digest, a keyed digest and no digest at all give one snapshot
    digest, while the profile's ``id`` and ``version`` are inside it. Computed on the JSON
    document, so that a recipient of an export that dropped the profile digest can recompute it."""
    module = api("snapshot")
    doc = _doc(with_profile=True, profile_digest=PLAIN)
    reference = doc["evaluator"]["test_profile"]
    assert {k: v for k, v in reference["content_digest"].items() if v is not None} == PLAIN
    base = module.compute_snapshot_digest(doc).value
    assert base == _independent_hex(doc)
    for digest in (OTHER_PLAIN, KEYED, None):
        variant = copy.deepcopy(doc)
        if digest is None:
            variant["evaluator"]["test_profile"].pop("content_digest")
        else:
            variant["evaluator"]["test_profile"]["content_digest"] = digest
        assert module.compute_snapshot_digest(variant).value == base, digest
        nulled = copy.deepcopy(variant)
        nulled["evaluator"]["test_profile"]["content_digest"] = None
        assert module.compute_snapshot_digest(nulled).value == base
    for field, value in (("version", 2), ("id", new_id("tpf"))):
        variant = copy.deepcopy(doc)
        variant["evaluator"]["test_profile"][field] = value
        assert module.compute_snapshot_digest(variant).value != base, field
    # the digest of the embedded evaluator itself is a different matter (see the findings in
    # api-surface section 22.5, N20); other embedded digests stay in the domain
    changed = copy.deepcopy(doc)
    changed["persona"]["content_digest"]["value"] = "f" * 64
    assert module.compute_snapshot_digest(changed).value != base
    without = _doc(with_profile=True)
    assert without["evaluator"]["test_profile"].get("content_digest") is None
    assert module.compute_snapshot_digest(without).value == _independent_hex(without)


def test_evaluator_digest_excludes_test_profile_digest() -> None:
    """N20 (owner, revision 11; ADR 0006 Decision 5, ADR 0009; api-surface 22.9 item 3): the
    Evaluator's own ``content_digest`` is computed over the generic domain minus the one path
    ``test_profile.content_digest``, so an export that drops or keys the profile digest does not
    break the embedded Evaluator's digest. The digest is unchanged when only that member changes
    (another plain digest, a keyed one, absent, ``null``); it changes when ``test_profile.id`` or
    ``version`` changes and when any other field changes, a ``content_digest`` key inside user
    ``metadata`` included. ``evaluator.evaluator_digest_domain(doc)`` removes only that path. A
    ConfigSnapshot whose evaluator lost or keyed its profile digest still loads and has the same
    snapshot digest. Proposed name (SCOPE section 5, P1-09 and P1-12)."""
    errors = api("errors")
    digest = api("digest")
    module = api("evaluator")
    model = entity_class("Evaluator")
    profile = make("TestProfile")
    reference = {"id": str(profile.id), "version": profile.version}
    evaluator_model = make("Evaluator", test_profile={**reference, "content_digest": PLAIN})
    doc: dict[str, Any] = evaluator_model.model_dump(mode="json", by_alias=True)
    stored = doc["content_digest"]
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    # the stored digest is the digest of the domain without the profile digest, computed here
    no_profile_digest = copy.deepcopy(body)
    no_profile_digest["test_profile"].pop("content_digest")
    assert digest.compute_digest(no_profile_digest).value == stored["value"]
    assert digest.compute_digest(body).value != stored["value"], "the profile digest is excluded"

    def variant(change: Any) -> dict[str, Any]:
        copied = copy.deepcopy(doc)
        change(copied)
        return copied

    def set_profile_digest(value: Any) -> Any:
        return lambda d: d["test_profile"].__setitem__("content_digest", value)

    loaded = model.model_validate(doc)
    assert loaded.test_profile.content_digest.value == PLAIN["value"]
    for label, change in (
        ("other plain", set_profile_digest(OTHER_PLAIN)),
        ("keyed", set_profile_digest(KEYED)),
        ("null", set_profile_digest(None)),
        ("dropped", lambda d: d["test_profile"].pop("content_digest")),
    ):
        again = model.model_validate(variant(change))
        assert again.content_digest == loaded.content_digest, label
    keyed = model.model_validate(variant(set_profile_digest(KEYED)))
    assert keyed.test_profile.content_digest.key_ref is not None  # what was given is kept
    assert model.model_validate(variant(lambda d: d["test_profile"].pop("content_digest")))
    # everything else is inside the digest
    for _label, change in (
        ("profile version", lambda d: d["test_profile"].__setitem__("version", 2)),
        ("profile id", lambda d: d["test_profile"].__setitem__("id", new_id("tpf"))),
        ("instructions", lambda d: d.__setitem__("instructions", "Another valid instruction.")),
        ("metadata", lambda d: d.__setitem__("metadata", {"added": 1})),
        ("name", lambda d: d.__setitem__("name", "Another name")),
    ):
        with pytest.raises(errors.DigestMismatch):
            model.model_validate(variant(change))
    # a content_digest key in user metadata is user data: it stays in the domain
    with_metadata = make(
        "Evaluator",
        test_profile={**reference, "content_digest": PLAIN},
        metadata={"content_digest": "user-supplied"},
    ).model_dump(mode="json", by_alias=True)
    assert with_metadata["metadata"] == {"content_digest": "user-supplied"}
    domain = module.evaluator_digest_domain(with_metadata)
    assert domain["metadata"] == {"content_digest": "user-supplied"}
    assert "content_digest" not in domain["test_profile"]
    assert domain["test_profile"]["id"] == reference["id"]
    expected = digest.digest_domain(with_metadata)
    expected["test_profile"] = {
        k: v for k, v in expected["test_profile"].items() if k != "content_digest"
    }
    assert domain == expected
    changed_metadata = copy.deepcopy(with_metadata)
    changed_metadata["metadata"]["content_digest"] = "other"
    with pytest.raises(errors.DigestMismatch):
        model.model_validate(changed_metadata)
    plain = make("Evaluator")  # no profile at all: the domain is the generic one
    plain_doc = plain.model_dump(mode="json", by_alias=True)
    assert module.evaluator_digest_domain(plain_doc) == digest.digest_domain(plain_doc)
    # the snapshot half: dropping or keying the profile digest keeps the snapshot loadable and equal
    snapshot = _doc(with_profile=True, profile_digest=PLAIN)
    base_digest = api("snapshot").compute_snapshot_digest(snapshot).value
    assert _snapshot(snapshot).content_digest.value == base_digest
    for label, replacement in (("keyed", KEYED), ("dropped", None), ("other plain", OTHER_PLAIN)):
        edited = copy.deepcopy(snapshot)
        if replacement is None:
            edited["evaluator"]["test_profile"].pop("content_digest")
        else:
            edited["evaluator"]["test_profile"]["content_digest"] = replacement
        assert api("snapshot").compute_snapshot_digest(edited).value == base_digest, label
        assert _snapshot(edited).content_digest.value == base_digest, label


def test_config_snapshot_and_evaluator_errors_do_not_echo_input() -> None:
    """O4 (owner, revision 12; ADR 0007 Decision 8 (b); api-surface 22.9 item 15), with finding
    N42 (decided by the owner): ``Evaluator`` and ``ConfigSnapshot`` hide their input in
    validation errors, because user-typed free text that the profile-value lint cannot see may
    hold personal data and a snapshot is copied into every bundle and export. A rejected Evaluator
    whose free text holds a marker string does not show it in ``str``, ``repr``,
    ``errors(include_input=False)`` or ``json(include_input=False)``, through ``model_validate``
    and ``model_validate_json``. Two seeds: a malformed placeholder with the marker in the
    surrounding text of the same field (``expected_outcome``, a scripted turn), and an unknown
    field that holds the marker (``extra_forbidden``, where the marker is the ``input_value``).
    The same Evaluator embedded in a ConfigSnapshot behaves the same. Each error is still located
    at the field and carries a message. Proposed name (SCOPE section 5, P1-12 and P1-09)."""
    evaluator = entity_class("Evaluator")
    snapshot = entity_class3("ConfigSnapshot")
    assert evaluator.model_config.get("hide_input_in_errors") is True
    assert snapshot.model_config.get("hide_input_in_errors") is True
    text = f"{QUOTE_MARK} said this {{{{profile.nam"
    turn = {"index": 0, "text": text, "language": "hi-IN"}
    direct = {
        "expected_outcome": raw_document("Evaluator", expected_outcome=text),
        "text": raw_document("Evaluator", scripted_turns=[turn]),
        "unexpected_note": {**raw_document("Evaluator"), "unexpected_note": QUOTE_MARK},
    }
    for field, doc in direct.items():
        errors = assert_input_hidden(evaluator, doc, QUOTE_MARK, field=field)
        if field == "unexpected_note":
            assert any(e["type"] == "extra_forbidden" for e in errors[0].errors())
    base = _doc()
    edits: list[tuple[str, Any]] = [
        ("expected_outcome", lambda d: d["evaluator"].__setitem__("expected_outcome", text)),
        ("text", lambda d: d["evaluator"].__setitem__("scripted_turns", [turn])),
        ("unexpected_note", lambda d: d["evaluator"].__setitem__("unexpected_note", QUOTE_MARK)),
        ("unexpected_note", lambda d: d.__setitem__("unexpected_note", QUOTE_MARK)),
    ]
    for field, edit in edits:
        copied = copy.deepcopy(base)
        edit(copied)
        assert_input_hidden(snapshot, copied, QUOTE_MARK, field=field)


def test_snapshot_digest_ignores_embedded_schema_version() -> None:
    """Revision 7 (ADR 0006 Decision 5, depth): the ``schema_version`` of each embedded entity
    (Evaluator, Persona, each MetricDefinition and MetricBinding, the Rubric) is outside the
    snapshot digest domain, at the paths the snapshot names, so the digest stays stable across
    schema upgrades; the top-level ``schema_version`` is outside it too. A user ``metadata`` key
    called ``schema_version`` is NOT removed (removal is by path, never by key name), and every
    other change moves the digest, including an embedded ``content_digest``."""
    module = api("snapshot")
    doc = _doc()
    base = module.compute_snapshot_digest(doc).value

    def digest_of(variant: dict[str, Any]) -> str:
        return str(module.compute_snapshot_digest(variant).value)

    paths: list[tuple[Any, ...]] = [
        ("schema_version",),
        ("evaluator", "schema_version"),
        ("persona", "schema_version"),
        ("rubric", "schema_version"),
        ("metric_definitions", 0, "schema_version"),
        ("metric_definitions", 1, "schema_version"),
        ("metric_bindings", 0, "schema_version"),
        ("metric_bindings", 1, "schema_version"),
    ]
    for path in paths:
        variant = copy.deepcopy(doc)
        node: Any = variant
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = "0.0.2"
        assert digest_of(variant) == base, path
        node.pop(path[-1])
        assert digest_of(variant) == base, path
    every = copy.deepcopy(doc)
    for path in paths:
        node = every
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = "9.9.9"
    assert digest_of(every) == base
    assert digest_of({**doc, "schema_version": "0.0.2"}) == base
    # not removed by name: user metadata keeps its member
    first = copy.deepcopy(doc)
    first["metadata"] = {"schema_version": "a"}
    second = copy.deepcopy(doc)
    second["metadata"] = {"schema_version": "b"}
    assert digest_of(first) != digest_of(second)
    nested = copy.deepcopy(doc)
    nested["evaluator"]["metadata"] = {"schema_version": "a"}
    other = copy.deepcopy(doc)
    other["evaluator"]["metadata"] = {"schema_version": "b"}
    assert digest_of(nested) != digest_of(other)
    # other changes move the digest
    for variant in (
        _with(doc, ("evaluator", "content_digest", "value"), "e" * 64),
        _with(doc, ("rubric", "items", 0, "required"), False),
        _with(doc, ("metric_definitions", 0, "key"), "renamed"),
        _with(doc, ("evaluator", "version"), 7),
    ):
        assert digest_of(variant) != base


def _with(doc: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    variant = copy.deepcopy(doc)
    node: Any = variant
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return variant


def test_internal_snapshot_profile_digest_plain_required() -> None:
    """ADR 0006 Decision 6, I18: an internal snapshot records the plain (``sha256-jcs/v1``) digest
    of
    its test profile; ``check_internal_snapshot`` rejects an absent digest and a keyed one, and
    accepts a plain one. A snapshot without a test profile has nothing to record. The snapshot
    model itself loads all of these (a shared export drops or keys the digest, P1-14)."""
    check = api("snapshot").check_internal_snapshot
    plain = _snapshot(_doc(with_profile=True, profile_digest=PLAIN))
    check(plain)
    for digest in (None, KEYED):
        shared = _snapshot(_doc(with_profile=True, profile_digest=digest))
        with pytest.raises(ValueError, match=r"plain|digest"):
            check(shared)
    check(_snapshot(_doc()))


def test_pricing_catalog_ref_digest_verified() -> None:
    """ADR 0006 I16, gate item 10 (the bundle half is P1-14, the catalog entity P1-24):
    ``PricingCatalogRef{id, version, content_digest}`` (N26, revision 11: the field is named
    ``content_digest`` like every other reference; the old name ``digest`` is an unknown field and
    is rejected) names a ``pcat_`` ID, a version of at least 1 and a
    required digest; ``check_pricing_catalog_ref`` verifies the reference against the catalog
    document of the bundle: another ID or version, a different digest of the same scheme, and a
    digest that cannot be compared without a key are rejected, never reported as matching."""
    ref_model = registered_class("PricingCatalogRef")
    catalog_id = new_id("pcat")
    base = {"id": catalog_id, "version": 3, "content_digest": PLAIN}
    reference = ref_model.model_validate(base)
    assert (str(reference.id), reference.version, reference.content_digest.value) == (
        catalog_id,
        3,
        "a" * 64,
    )
    assert set(ref_model.model_fields) == {"id", "version", "content_digest"}
    assert ref_model.model_validate_json(reference.model_dump_json()) == reference
    for bad in (
        {**base, "content_digest": None},
        {k: v for k, v in base.items() if k != "content_digest"},
        {**base, "id": new_id("tpf")},
        {**base, "id": "pcat_x"},
        {**base, "version": 0},
        {**base, "version": "3"},
        {k: v for k, v in base.items() if k != "version"},
        {**base, "content_digest": {"scheme": "sha256-jcs/v1", "value": "A" * 64}},
        {**base, "digest": PLAIN},
    ):
        with pytest.raises(ValidationError):
            ref_model.model_validate(bad)
    check = api("snapshot").check_pricing_catalog_ref
    catalog = {"id": catalog_id, "version": 3, "content_digest": PLAIN}
    check(reference, catalog)
    with pytest.raises(api("errors").RefDigestMismatchError):
        check(reference, {**catalog, "content_digest": OTHER_PLAIN})
    with pytest.raises(ValueError, match=r"\S"):
        check(reference, {**catalog, "id": new_id("pcat")})
    with pytest.raises(ValueError, match=r"\S"):
        check(reference, {**catalog, "version": 4})
    with pytest.raises(ValueError, match=r"\S"):
        check(reference, {**catalog, "content_digest": KEYED})
    keyed_ref = ref_model.model_validate({**base, "content_digest": KEYED})
    with pytest.raises(ValueError, match=r"\S"):
        check(keyed_ref, catalog)
    # two keyed digests of the same key can be compared without the key (ADR 0009 Decision 7)
    keyed_catalog = {**catalog, "content_digest": KEYED}
    check(keyed_ref, keyed_catalog)
    other_value = {**KEYED, "value": "d" * 64}
    with pytest.raises(api("errors").RefDigestMismatchError):
        check(keyed_ref, {**catalog, "content_digest": other_value})
    other_key = {**KEYED, "key_ref": {"key_id": "tracer-key-2"}}
    with pytest.raises(ValueError, match=r"\S"):
        check(keyed_ref, {**catalog, "content_digest": other_key})
    held = _snapshot(snapshot_doc(snapshot_parts(), pricing_catalog=base))
    assert held.pricing_catalog == reference
    assert schema_of(entity_class3("ConfigSnapshot"))["properties"]["pricing_catalog"]


# ----------------------------------------------------------------------------------- the lint


def test_snapshot_free_text_profile_value_lint_warns() -> None:
    """P-16 (ADR 0006 Decision 9, I21): ``lint_profile_values_in_free_text(snapshot, profile)``
    returns an ``IngestWarning`` with code ``profile_value_in_free_text`` and the RFC 6901 path
    of each free-text string of the snapshot that holds a literal value of the profile, sorted by
    path, and never the value (not in the code, path, raw or message). Placeholders are not
    values; a snapshot without literals gives no warning; the profile need not be in the
    snapshot."""
    lint = api("snapshot").lint_profile_values_in_free_text
    clean_parts = snapshot_parts(with_profile=True, profile_digest=PLAIN)
    profile = clean_parts["profile"]
    clean = _snapshot(snapshot_doc(clean_parts))
    assert lint(clean, profile) == []  # the instructions use {{profile.name}}
    leaky_parts = snapshot_parts(
        with_profile=True,
        profile_digest=PLAIN,
        evaluator_over={
            "instructions": f"Greet {profile.name} and ask for a refund.",
            "expected_outcome": f"The agent verifies account {profile.account_number}.",
            "scripted_turns": [
                {"index": 0, "text": f"My date of birth is {profile.dob}.", "language": "en-IN"}
            ],
        },
    )
    leaky = _snapshot(snapshot_doc(leaky_parts))
    warnings = lint(leaky, profile)
    assert [w.path for w in warnings] == [
        "/evaluator/expected_outcome",
        "/evaluator/instructions",
        "/evaluator/scripted_turns/0/text",
    ]
    assert {w.code for w in warnings} == {"profile_value_in_free_text"}
    values = [profile.name, profile.dob, profile.account_number]
    for warning in warnings:
        dumped = json.dumps(warning.model_dump(mode="json"), ensure_ascii=False)
        for value in values:
            assert value not in dumped
        assert warning.raw is None
        assert warning.message
    assert isinstance(warnings[0], registered_class("IngestWarning"))
    again = lint(leaky, profile)
    assert [w.path for w in again] == [w.path for w in warnings]  # deterministic
    in_metadata = _snapshot(
        snapshot_doc(clean_parts, metadata={"note": f"call for {profile.name}"}, metadata_flags=[])
    )
    assert [w.path for w in lint(in_metadata, profile)] == ["/metadata/note"]
    other_profile = make(
        "TestProfile", name="Ravi Kumar", dob="1985-01-02", account_number="5500998877"
    )
    assert lint(leaky, other_profile) == []


def _policy_text() -> str:
    candidates = [
        PACKAGE_DIR / "SCHEMA_POLICY.md",
        ROOT / "SCHEMA_POLICY.md",
        ROOT / "docs" / "SCHEMA_POLICY.md",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    pytest.fail(
        "SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of "
        + ", ".join(str(p.relative_to(ROOT)) for p in candidates)
    )


def test_policy_documents_free_text_lint_limitation() -> None:
    """P-16 (ADR 0006 Decision 9, I21): SCHEMA_POLICY.md states that the free-text lint can only
    find
    values of the profile it is given, that personal data typed into free text that is in no
    profile cannot be detected and stays the author's responsibility, and (revision 8) that
    another format of the same value or a part of a value is not found. Gate-bound: green when
    P1-20a writes the file."""
    flat = re.sub(r"\s+", " ", _policy_text().lower())
    assert "lint" in flat
    assert re.search(r"(only|just) (find|detect)", flat) or "can only" in flat
    assert re.search(r"(in no profile|not in any profile|no profile)", flat)
    assert re.search(r"(responsibility|author)", flat)
    assert re.search(r"(another format|different format|other format)", flat)
    assert re.search(r"(part of a value|partial|first name alone|part of a name)", flat)
    assert "placeholder" in flat


# ------------------------------------------------------------------------- references inside


def test_snapshot_expected_outcome_is_a_required_equals_true_check() -> None:
    """Revision 8 (ADR 0013 Decision 2, api-surface 19.4 item 7): the evaluator's expected outcome
    reaches its check through the reserved prompt variable ``expected_outcome``. An
    expected-outcome check is an enabled binding whose definition is ``llm_judge``, boolean,
    per_conversation, ``equals_true`` and lists ``expected_outcome`` in ``prompt_variables``. The
    snapshot needs at least one, and every one must be a required rubric item. A definition that
    uses the variable in another shape is an ordinary metric."""
    ok = _snapshot(_doc())
    assert "expected_outcome" in ok.metric_definitions[0].prompt_variables
    assert ok.rubric.items[0].required is True
    # not required: rejected
    _reject(_doc(required=[False, True]))
    # no definition uses the variable
    _reject(
        _doc(judge_over={"prompt_variables": ["transcript"], "prompt_template": "{{transcript}}"})
    )
    # a disabled binding is no check (and writes no result)
    _reject(_doc(enabled=[False, True]))
    # another shape: a rating that uses the variable is an ordinary metric, not the check
    rating = {
        "value_type": "rating",
        "scale": {"min": 1, "max": 5},
        "pass_rule": {"kind": "range", "min": 4, "inclusive": True},
    }
    _reject(_doc(judge_over=rating))
    per_turn = {
        "aggregation": {"kind": "per_turn_share", "turn_rule": {"kind": "equals_true"}},
        "pass_rule": {"kind": "range", "min": 0.5, "inclusive": True},
    }
    _reject(_doc(judge_over=per_turn))
    # an ordinary metric that uses the variable may be optional while a real check is required
    ordinary = {
        **judge_definition(key="outcome_rating", **rating),
        "prompt_template": JUDGE_TEMPLATE,
    }
    both = _snapshot(_doc(extra_definitions=[ordinary], required=[True, True, False]))
    assert len(both.metric_definitions) == 3
    # two real checks: both must be required
    second = judge_definition(key="outcome_confirmed")
    _snapshot(_doc(extra_definitions=[second], required=[True, True, True]))
    _reject(_doc(extra_definitions=[second], required=[True, True, False]))
    _reject(_doc(extra_definitions=[second], required=[False, True, True]))


def test_definition_refs_digest_optional_and_verified_when_present() -> None:
    """E5 (accepted by the owner; ADR 0009 Decision 4, I11), the snapshot half: the references
    between the embedded records (the evaluator's persona and bindings, a binding's definition)
    may omit ``content_digest``; when one is present it is verified against the embedded record,
    and a mismatch is rejected with the typed ``RefDigestMismatchError``. An unresolved reference
    (no embedded record of that ``(id, version)``) is rejected."""
    errors = api("errors")
    parts = snapshot_parts()
    doc = snapshot_doc(parts)
    _snapshot(doc)  # references without digests: fine
    wrong = {"scheme": "sha256-jcs/v1", "value": "0" * 64}

    def reseal(mutate: Any) -> dict[str, Any]:
        body = copy.deepcopy({k: v for k, v in doc.items() if k != "content_digest"})
        mutate(body)
        return {
            **body,
            "content_digest": api("snapshot")
            .compute_snapshot_digest(body)
            .model_dump(mode="json", exclude_none=True),
        }

    persona = parts["persona"]
    right = persona.content_digest.model_dump(mode="json", exclude_none=True)

    def pin_persona(digest: dict[str, Any]) -> Any:
        def mutate(body: dict[str, Any]) -> None:
            body["evaluator"]["persona"]["content_digest"] = digest
            body["evaluator"]["content_digest"] = _reseal_evaluator(body["evaluator"])

        return mutate

    assert (
        _snapshot(reseal(pin_persona(right))).evaluator.persona.content_digest.value
        == right["value"]
    )
    with pytest.raises(errors.RefDigestMismatchError):
        _snapshot(reseal(pin_persona(wrong)))

    # the evaluator's binding references and a binding's definition reference follow the same rule
    def pin_binding(digest: dict[str, Any]) -> Any:
        def mutate(body: dict[str, Any]) -> None:
            body["evaluator"]["metric_bindings"][0]["content_digest"] = digest
            body["evaluator"]["content_digest"] = _reseal_evaluator(body["evaluator"])

        return mutate

    binding_digest = parts["bindings"][0].content_digest.model_dump(mode="json", exclude_none=True)
    assert _snapshot(reseal(pin_binding(binding_digest)))
    with pytest.raises(errors.RefDigestMismatchError):
        _snapshot(reseal(pin_binding(wrong)))

    def pin_definition(digest: dict[str, Any]) -> Any:
        def mutate(body: dict[str, Any]) -> None:
            body["metric_bindings"][0]["metric"]["content_digest"] = digest
            body["metric_bindings"][0]["content_digest"] = _reseal_binding(
                body["metric_bindings"][0]
            )

        return mutate

    definition_digest = parts["definitions"][0].content_digest.model_dump(
        mode="json", exclude_none=True
    )
    assert _snapshot(reseal(pin_definition(definition_digest)))
    with pytest.raises(errors.RefDigestMismatchError):
        _snapshot(reseal(pin_definition(wrong)))

    # an unresolved reference to a binding
    def unknown_binding(body: dict[str, Any]) -> None:
        body["evaluator"]["metric_bindings"][0] = {"id": new_id("mbd"), "version": 1}
        body["evaluator"]["content_digest"] = _reseal_evaluator(body["evaluator"])

    with pytest.raises(ValueError, match=r"\S"):
        _snapshot(reseal(unknown_binding))

    # a binding that belongs to another evaluator
    def other_evaluator(body: dict[str, Any]) -> None:
        body["metric_bindings"][0]["evaluator_id"] = new_id("evl")
        body["metric_bindings"][0]["content_digest"] = _reseal_binding(body["metric_bindings"][0])

    with pytest.raises(ValueError, match=r"\S"):
        _snapshot(reseal(other_evaluator))

    # an unresolved reference
    def dangling(body: dict[str, Any]) -> None:
        body["evaluator"]["persona"] = {"id": new_id("per"), "version": 1}
        body["evaluator"]["content_digest"] = _reseal_evaluator(body["evaluator"])

    with pytest.raises(ValueError, match=r"\S"):
        _snapshot(reseal(dangling))

    # a binding that points at a definition version that is not embedded
    def no_definition(body: dict[str, Any]) -> None:
        body["metric_bindings"][0]["metric"] = {"id": new_id("met"), "version": 1}
        body["metric_bindings"][0]["content_digest"] = _reseal_binding(body["metric_bindings"][0])

    with pytest.raises(ValueError, match=r"\S"):
        _snapshot(reseal(no_definition))

    # The half outside a snapshot (N34, api-surface 22.9 item 9; the same rule as above, shown for
    # the references an evaluator, a binding and a rubric hold, checked by the bundle): a reference
    # validates without a digest and with one; a present digest that disagrees with the resolved
    # record is a RefDigestMismatchError.
    bundle_check = api("bundle").check_bundle_consistency
    holders = (
        ("Persona", "Evaluator", "persona"),
        ("MetricDefinition", "MetricBinding", "metric"),
        ("MetricBinding", "Evaluator", "metric_bindings"),
        ("MetricBinding", "Rubric", "items"),
    )
    for name, holder, field in holders:
        entity = build(name)
        plain = {"id": entity["id"], "version": entity["version"]}
        carried = {**plain, "content_digest": entity["content_digest"]}
        forged = {**plain, "content_digest": wrong}

        def shown(reference: dict[str, Any], holder: str = holder, field: str = field) -> Any:
            if field == "metric_bindings":
                value: Any = [reference]
            elif field == "items":
                value = [{"binding": reference, "required": True}]
            else:
                value = reference
            dumped = make(holder, **{field: value}).model_dump(mode="json", exclude_none=True)
            held = dumped[field]
            if field == "metric_bindings":
                return held[0]
            return held[0]["binding"] if field == "items" else held

        bundle_check([entity], [shown(plain)])
        assert shown(plain).get("content_digest") is None
        bundle_check([entity], [shown(carried)])
        assert shown(carried)["content_digest"] == entity["content_digest"]
        with pytest.raises(errors.RefDigestMismatchError):
            bundle_check([entity], [shown(forged)])


def _reseal_evaluator(doc: dict[str, Any]) -> dict[str, Any]:
    """The Evaluator digest (N20): the generic domain minus ``test_profile.content_digest``,
    written out here and not taken from ``evaluator_digest_domain``."""
    body = without_profile_digest({k: v for k, v in doc.items() if k != "content_digest"})
    digest = api("digest").compute_digest(body)
    dumped: dict[str, Any] = digest.model_dump(mode="json", exclude_none=True)
    return dumped


def _reseal_binding(doc: dict[str, Any]) -> dict[str, Any]:
    """A MetricBinding has no ``test_profile``: the generic domain."""
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    digest = api("digest").compute_digest(body)
    dumped: dict[str, Any] = digest.model_dump(mode="json", exclude_none=True)
    return dumped


def test_snapshot_validates_the_rubric_against_bindings() -> None:
    """Proposed name (api-surface 19.4 items 1, 4 and 8, ADR 0013 Decision 7: ``validate_rubric`` is
    called by the snapshot): the embedded bindings, definitions and rubric must pass the rubric
    validation, so a binding that belongs to another evaluator, two definitions with one key, a
    required item whose definition has no pass rule and a disabled binding as a rubric item are
    rejected, each at snapshot level."""
    _snapshot(_doc())
    no_rule_parts = snapshot_parts(
        extra_definitions=[judge_definition(key="informational", pass_rule=None)],
        required=[True, True, True],
    )
    with pytest.raises(ValueError, match=r"\S"):
        _snapshot(snapshot_doc(no_rule_parts))
    renamed = snapshot_parts(extra_definitions=[judge_definition()])  # same key as definition 0
    with pytest.raises(ValueError, match=r"\S"):
        _snapshot(snapshot_doc(renamed))


def test_snapshot_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed
    ID, language tag, closed-set member, metric key, decimal weight and discriminator tag inside a
    ``ConfigSnapshot`` (its own ID and the embedded evaluator, persona, definitions, bindings and
    rubric) do not appear in ``str``, ``repr``, ``errors(include_input=False)`` or
    ``json(include_input=False)``, through ``model_validate`` and ``model_validate_json``, and the
    error is still located at the field. Proposed name (SCOPE section 5, P1-12)."""
    snapshot = entity_class3("ConfigSnapshot")
    base = _doc()
    bad_digest = {"scheme": "sha256-jcs/v1", "value": "zz" + QUOTE_MARK}

    def edited(edit: Any) -> dict[str, Any]:
        copied = copy.deepcopy(base)
        edit(copied)
        return copied

    cases: list[tuple[str, dict[str, Any], str]] = [
        ("typed ID", edited(lambda d: d.__setitem__("id", marked_id("snap"))), "id"),
        (
            "BCP-47",
            edited(lambda d: d["evaluator"].__setitem__("language", marked_tag())),
            "language",
        ),
        (
            "BCP-47",
            edited(lambda d: d["persona"].__setitem__("language", marked_tag())),
            "language",
        ),
        (
            "typed ID",
            edited(lambda d: d["evaluator"]["persona"].__setitem__("id", marked_id("per"))),
            "persona",
        ),
        (
            "ClosedSet",
            edited(lambda d: d["metric_definitions"][0].__setitem__("kind", marked_set())),
            "kind",
        ),
        (
            "MetricKey",
            edited(
                lambda d: d["metric_definitions"][0].__setitem__("key", "Bad Key " + QUOTE_MARK)
            ),
            "key",
        ),
        (
            "discriminator",
            edited(
                lambda d: d["metric_definitions"][0].__setitem__(
                    "pass_rule", {"kind": marked_set()}
                )
            ),
            "pass_rule",
        ),
        (
            "typed ID",
            edited(lambda d: d["metric_bindings"][0].__setitem__("evaluator_id", marked_id("evl"))),
            "evaluator_id",
        ),
        (
            "DecimalString",
            edited(lambda d: d["rubric"]["items"][0].__setitem__("weight", "x" + QUOTE_MARK)),
            "weight",
        ),
        (
            "digest value",
            edited(lambda d: d.__setitem__("content_digest", bad_digest)),
            "content_digest",
        ),
        (
            "digest value",
            edited(lambda d: d["persona"].__setitem__("content_digest", bad_digest)),
            "content_digest",
        ),
        (
            "digest value",
            edited(lambda d: d["evaluator"]["persona"].__setitem__("content_digest", bad_digest)),
            "content_digest",
        ),
        (
            "typed ID",
            edited(
                lambda d: d.__setitem__(
                    "pricing_catalog",
                    {"id": marked_id("pcat"), "version": 1, "content_digest": PLAIN},
                )
            ),
            "pricing_catalog",
        ),
    ]
    for label, doc, field in cases:
        assert_input_hidden(snapshot, doc, QUOTE_MARK, field=field, label=label)
