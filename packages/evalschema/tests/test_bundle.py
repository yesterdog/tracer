# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 7 (P1-14), part 1: ``RunBundle``, its cross-record rules, the bundle seam and the loader.

Contract: docs/phase1/api-surface-bundle.md. ADRs 0004 (Decision 2a), 0006, 0008 (Decision 3),
0009 (Decisions 5 and 7), 0011 (Decisions 1 and 4), 0012, 0013, 0014 (Decisions 12 and 16), 0015
(Decision 7), 0016. The RunBundle is the unit of a run (N22): records of fact name no run, so every
"unique per run" and "of the same run" rule is checked here, over the records of one bundle.

Conventions of this batch (section 3 of the contract). A rule that is not about a typed error
raises a ``ValueError`` whose message starts with a bracketed tag (``[unresolved]``, ``[tier]``);
``model_validate`` turns it into a ``ValidationError`` that holds the tag. Typed errors (a
``BundleError`` subclass, ``SchemaVersionTooNew``, ``RefDigestMismatchError``) propagate
unwrapped. No message quotes a rejected value; identifiers are named.

The records are built by ``_support_b7.world()`` with the real models of batches 1 to 5.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import CURRENT_SCHEMA_VERSION, api
from ._support_b2 import (
    collect_enums,
    contains_key,
    embedded_models,
    new_id,
    property_names,
    registered_class,
    schema_accepts,
    schema_of,
    secret_like,
)
from ._support_b2 import seal as seal_entity
from ._support_b3 import QUOTE_MARK, privacy_human, privacy_synthetic
from ._support_b4 import record_reported_doc, summary_doc
from ._support_b7 import (
    KEYED,
    PLAIN,
    assert_bundle_rejected,
    assert_error_free_of,
    bundle_fn,
    export_class,
    export_doc,
    load_export,
    load_run,
    plain_profile_digest,
    resnap,
    run_bundle_doc,
    run_class,
    shared_ready,
    world,
)

TIERS = ("T0", "T1", "T2")
ORIGINS = ("human", "simulated")

RUN_BUNDLE_FIELDS = {
    "id",
    "schema_version",
    "created_at",
    "metadata",
    "metadata_flags",
    "run",
    "snapshot",
    "pricing_catalogs",
    "test_profiles",
    "artifacts",
    "transcripts",
    "turns",
    "exchanges",
    "tool_calls",
    "spans",
    "results",
    "verdict",
    "usage_records",
    "cost_records",
    "cost_summaries",
    "withheld",
}
EXPORT_BUNDLE_FIELDS = {
    "id",
    "schema_version",
    "created_at",
    "metadata",
    "metadata_flags",
    "purpose",
    "run_bundles",
    "datasets",
    "dataset_items",
    "sealed_payloads",
    "payload_redactions",
    "provenance_redactions",
}
WITHHOLDABLE_KINDS = {
    "artifact",
    "transcript",
    "turn",
    "exchange",
    "tool_call",
    "span",
    "result",
    "usage_record",
    "cost_record",
}


def _reject(doc: dict[str, Any], *tags: str) -> BaseException:
    return assert_bundle_rejected(run_class(), doc, *tags)


def _typed(doc: dict[str, Any], name: str) -> BaseException:
    return assert_bundle_rejected(run_class(), doc, typed=name)


# =========================================================================== shape and registry


def test_run_and_export_bundle_are_registered_entities() -> None:
    """ADR 0001 Decision 4 (``rbnd`` RunBundle, ``exp`` ExportBundle), ADR 0009 Decision 5 (records
    of fact, not versioned), ADR 0016 Decision 7. Both are ``EntityBase`` entities with their
    registered prefix, neither has ``version`` or ``content_digest``, and the registry finds them
    (the gate-bound ``test_every_entity_has_registered_prefix`` needs exactly these two)."""
    registry = api("registry")
    base = api("base")
    entities = registry.entity_classes()
    assert entities["rbnd"] is run_class()
    assert entities["exp"] is export_class()
    for model, prefix in ((run_class(), "rbnd"), (export_class(), "exp")):
        assert issubclass(model, base.EntityBase)
        assert not issubclass(model, base.VersionedEntity)
        assert model.id_prefix == prefix
        assert "version" not in model.model_fields
        assert "content_digest" not in model.model_fields
    assert set(run_class().model_fields) == RUN_BUNDLE_FIELDS
    assert set(export_class().model_fields) == EXPORT_BUNDLE_FIELDS
    run_required = {n for n, f in run_class().model_fields.items() if f.is_required()}
    export_required = {n for n, f in export_class().model_fields.items() if f.is_required()}
    assert run_required == {"id", "schema_version", "created_at", "run", "snapshot"}
    assert export_required == {"id", "schema_version", "created_at", "purpose"}
    assert api("ids").ID_PREFIXES["rbnd"] == "RunBundle"
    assert api("ids").ID_PREFIXES["exp"] == "ExportBundle"


def test_bundle_value_objects_shape() -> None:
    """The three value objects of P1-14 (contract section 2): ``WithheldRecord{kind, record_id}``,
    ``PayloadRedaction{subject_id, redaction}`` and ``ProvenanceRedaction{item_id, field, reason}``
    (ADR 0016 Decision 9, D2: item, field, reason, never the run id). Frozen, ``extra=forbid``,
    no entity fields, and none of them is a ``RedactionRecord`` (which keeps its four fields)."""
    base = api("base")
    registry = api("registry")
    value_objects = registry.value_object_classes()
    expected = {
        "WithheldRecord": {"kind", "record_id"},
        "PayloadRedaction": {"subject_id", "redaction"},
        "ProvenanceRedaction": {"item_id", "field", "reason"},
    }
    for name, fields in expected.items():
        model = registered_class(name)
        assert name in value_objects
        assert not issubclass(model, base.EntityBase)
        assert set(model.model_fields) == fields, name
        assert model.model_config.get("extra") == "forbid", name
        assert model.model_config.get("frozen") is True, name
        assert not ({"id", "schema_version", "created_at", "metadata"} & set(model.model_fields))
    redaction = registered_class("RedactionRecord")
    assert set(redaction.model_fields) == {"method", "tool_ref", "performed_at", "covers"}
    provenance = registered_class("ProvenanceRedaction")
    assert registered_class("ProvenanceRedaction") is not redaction
    assert not ({"covers", "method", "tool_ref", "performed_at"} & set(provenance.model_fields))


def test_bundle_closed_sets_are_exact_and_emit_enum() -> None:
    """ADR 0003 Decision 1 (closed sets), as the other batches: ``ExportBundle.purpose`` is
    internal, shared_export, benchmark; ``WithheldRecord.kind`` names the nine record kinds of
    contract section 2.3; ``ProvenanceRedaction.field`` is ``source_run_id`` and ``reason`` is
    ``unresolved_reference``. Each emits an ``enum`` (never ``const``), and any other string,
    also a case or white-space variant, is rejected."""
    bundle = api("bundle")
    assert tuple(bundle.EXPORT_PURPOSES) == ("internal", "shared_export", "benchmark")
    assert set(bundle.WITHHOLDABLE_KINDS) == WITHHOLDABLE_KINDS
    assert set(bundle.PROVENANCE_KINDS) == {
        "transcript",
        "turn",
        "exchange",
        "tool_call",
        "span",
        "usage_record",
    }
    assert set(bundle.PROVENANCE_KINDS) <= set(bundle.WITHHOLDABLE_KINDS)
    export_schema = schema_of(export_class())
    assert set(collect_enums(export_schema, export_schema["properties"]["purpose"])) == {
        "internal",
        "shared_export",
        "benchmark",
    }
    for model, field, members in (
        (registered_class("WithheldRecord"), "kind", WITHHOLDABLE_KINDS),
        (registered_class("ProvenanceRedaction"), "field", {"source_run_id"}),
        (registered_class("ProvenanceRedaction"), "reason", {"unresolved_reference"}),
    ):
        schema = schema_of(model)
        assert set(collect_enums(schema, schema["properties"][field])) == set(members)
        assert not contains_key(schema["properties"][field], "const")
    for bad in ("Internal", "internal ", "shared", "export", "public", "", " benchmark"):
        with pytest.raises(ValidationError):
            export_class().model_validate(export_doc(bad))
    withheld = registered_class("WithheldRecord")
    for bad in ("Turn", "turns", "record", "", "profile"):
        with pytest.raises(ValidationError):
            withheld.model_validate({"kind": bad, "record_id": new_id("turn")})


def test_withheld_record_kind_matches_id_kind() -> None:
    """ADR 0004 Decision 2a (a declaration names record kinds and IDs): the ID is a typed ID of
    the kind the entry names, and no other string is accepted. ``record_id`` is a typed ID field,
    so the registry walks (``test_every_id_reference_field_has_registered_prefix``) apply."""
    withheld = registered_class("WithheldRecord")
    ids_by_kind = {
        "artifact": "art",
        "transcript": "tsc",
        "turn": "turn",
        "exchange": "exc",
        "tool_call": "tcl",
        "span": "spn",
        "result": "res",
        "usage_record": "use",
        "cost_record": "cst",
    }
    for kind, prefix in ids_by_kind.items():
        good = withheld.model_validate({"kind": kind, "record_id": new_id(prefix)})
        assert str(good.record_id).startswith(prefix + "_")
        for other_kind, other_prefix in ids_by_kind.items():
            if other_kind != kind:
                with pytest.raises(ValidationError):
                    withheld.model_validate({"kind": kind, "record_id": new_id(other_prefix)})
    for bad in ("", "turn_x", str(new_id("turn")).upper(), str(new_id("turn"))[5:], 7, None):
        with pytest.raises(ValidationError):
            withheld.model_validate({"kind": "turn", "record_id": bad})
    with pytest.raises(ValidationError):
        withheld.model_validate({"kind": "turn", "record_id": new_id("turn"), "value": "x"})
    # an unrelated kind of ID (a run, a bundle) is no record of a run
    for prefix in ("run", "rbnd", "exp", "snap", "dti"):
        with pytest.raises(ValidationError):
            withheld.model_validate({"kind": "turn", "record_id": new_id(prefix)})


# ======================================================================= acceptance and round trip


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("tier", TIERS)
def test_run_bundle_of_a_consistent_run_is_accepted(tier: str, origin: str) -> None:
    """The fixture world is a valid bundle at every tier and for both caller origins: a bundle of
    one run holds its snapshot, recording, transcript, turns, exchanges, tool calls, spans,
    results, verdict, usage and cost. It also keeps its content: dumping the model and loading
    the dump gives the same bundle."""
    doc = run_bundle_doc(tier=tier, origin=origin)
    bundle = load_run(doc)
    assert str(bundle.run.id) == doc["run"]["id"]
    assert [str(t.id) for t in bundle.turns] == [t["id"] for t in doc["turns"]]
    again = load_run(bundle.model_dump(mode="json", by_alias=True))
    assert again == bundle


def test_bundle_roundtrip_model_json_schema_model() -> None:
    """SCOPE P1-14 (binding name). Model -> JSON -> model is the identity for both bundles at every
    tier; the JSON of a model validates against its JSON Schema (draft 2020-12); the schema
    rejects a document with an unknown top-level member and one that lacks a required member; and
    ``model_validate_json`` of the dump equals the model. Cross-record rules are code-only and are
    listed with the other known gaps (contract section 8)."""
    for tier in TIERS:
        doc = run_bundle_doc(tier=tier)
        bundle = load_run(doc)
        dumped = bundle.model_dump(mode="json", by_alias=True)
        assert schema_accepts(run_class(), dumped)
        assert schema_accepts(run_class(), dumped, "serialization")
        assert run_class().model_validate_json(bundle.model_dump_json(by_alias=True)) == bundle
        assert not schema_accepts(run_class(), {**dumped, "unexpected": 1})
        assert not schema_accepts(run_class(), {k: v for k, v in dumped.items() if k != "run"})
    for purpose in ("internal", "shared_export", "benchmark"):
        export = load_export(
            shared_ready(purpose) if purpose != "internal" else export_doc(purpose)
        )
        dumped = export.model_dump(mode="json", by_alias=True)
        assert schema_accepts(export_class(), dumped)
        assert export_class().model_validate_json(export.model_dump_json(by_alias=True)) == export
        assert load_export(dumped) == export
        assert not schema_accepts(export_class(), {**dumped, "purpose": "public"})
        assert not schema_accepts(export_class(), {**dumped, "unexpected": 1})
        assert not schema_accepts(
            export_class(), {k: v for k, v in dumped.items() if k != "purpose"}
        )


def test_bundle_models_reject_unknown_and_secret_like_members() -> None:
    """ADR 0007 Decision 3 and ADR 0016 Decision 6: both bundles are ``extra=forbid``; a member
    named like a credential, a transport ``user_id`` or a caller identifier is an unknown member and
    is rejected; and no property of either schema (nested ones included) is named like a secret."""
    run_extras: list[dict[str, Any]] = [
        {"api_key": "x"},
        {"hmac_key": "x"},
        {"user_id": "u-1"},
        {"caller_external_id": {"value": "x"}},
        {"sealed_payloads": []},
        {"purpose": "internal"},
    ]
    for run_extra in run_extras:
        with pytest.raises(ValidationError):
            run_class().model_validate({**run_bundle_doc(tier="T0"), **run_extra})
    export_extras: list[dict[str, Any]] = [
        {"api_key": "x"},
        {"user_id": "u-1"},
        {"run": {}},
        {"withheld": []},
    ]
    for export_extra in export_extras:
        with pytest.raises(ValidationError):
            export_class().model_validate({**export_doc("internal"), **export_extra})
    for model in (run_class(), export_class()):
        for mode in ("validation", "serialization"):
            for name in property_names(model, mode):
                assert not secret_like(name), name


def test_bundle_models_hide_their_input() -> None:
    """ADR 0007 Decision 8 (b) and (e): both bundles embed models that hold personal data, so the
    set closed under embedding makes them hide their input. A rejected bundle shows no text of its
    records in ``str``, ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``,
    through ``model_validate`` and ``model_validate_json``, whichever rule rejected it (a rule of
    the bundle, a rule of an embedded record, an unknown member, a missing member)."""
    assert run_class().model_config.get("hide_input_in_errors") is True
    assert export_class().model_config.get("hide_input_in_errors") is True
    mark = QUOTE_MARK
    broken: dict[str, dict[str, Any]] = {}
    base = run_bundle_doc(tier="T0")
    base["turns"][0]["text"] = f"my account number is {mark}"
    base["tool_calls"][0]["arguments_raw"] = json.dumps({"account": mark})
    base["tool_calls"][0]["arguments"] = {"account": mark}
    base["metadata"] = {"note": mark}
    one = copy.deepcopy(base)
    one["usage_records"][0]["run_id"] = new_id("run")  # a bundle rule: membership
    broken["membership"] = one
    two = copy.deepcopy(base)
    two["turns"][1]["seq"] = two["turns"][0]["seq"]  # a bundle rule: transcript rules
    broken["seq"] = two
    three = copy.deepcopy(base)
    three["turns"][0]["role"] = "speaker"  # a rule of an embedded record
    broken["role"] = three
    four = copy.deepcopy(base)
    four["unknown_member"] = {"text": mark}
    broken["unknown"] = four
    five = copy.deepcopy(base)
    del five["run"]
    broken["missing"] = five
    for label, doc in broken.items():
        for model in (run_class(),):
            with pytest.raises(ValidationError) as via_python:
                model.model_validate(doc)
            with pytest.raises(ValidationError) as via_json:
                model.model_validate_json(json.dumps(doc))
            for error in (via_python.value, via_json.value):
                assert_error_free_of(error, mark)
                assert error.errors(), label
    exported = export_doc("shared_export", [base])  # unprotected human text: a rule of the export
    with pytest.raises(ValidationError) as exp_error:
        export_class().model_validate(exported)
    assert_error_free_of(exp_error.value, mark)
    with pytest.raises(ValidationError) as exp_json:
        export_class().model_validate_json(json.dumps(exported))
    assert_error_free_of(exp_json.value, mark)


# ================================================================== versions and record identity


def test_run_bundle_rejects_records_of_another_schema_version() -> None:
    """ADR 0008 Decision 3 (bundles are single-version) and Decision 2: a record of a newer schema
    version raises ``SchemaVersionTooNew`` and one of an older version ``SchemaVersionMismatch``,
    unwrapped, whichever record of the bundle carries it (an embedded record is checked by its own
    model; the bundle adds no wrapping)."""
    getters: list[Callable[[dict[str, Any]], dict[str, Any]]] = [
        lambda d: d["turns"][0],
        lambda d: d["artifacts"][0],
        lambda d: d["tool_calls"][0],
        lambda d: d["run"],
        lambda d: d["usage_records"][0],
        lambda d: d["snapshot"],
    ]
    for where in getters:
        newer = run_bundle_doc(tier="T1")
        where(newer)["schema_version"] = "0.0.2"
        assert_bundle_rejected(run_class(), newer, typed="SchemaVersionTooNew")
        older = run_bundle_doc(tier="T1")
        where(older)["schema_version"] = "0.0.0"
        assert_bundle_rejected(run_class(), older, typed="SchemaVersionMismatch")
    own = run_bundle_doc(tier="T0")
    own["schema_version"] = "0.0.2"
    assert_bundle_rejected(run_class(), own, typed="SchemaVersionTooNew")
    mixed = export_doc("internal", [run_bundle_doc(tier="T0")])
    mixed["run_bundles"][0]["turns"][0]["schema_version"] = "0.0.2"
    assert_bundle_rejected(export_class(), mixed, typed="SchemaVersionTooNew")


def test_run_bundle_rejects_same_record_id_different_content() -> None:
    """ADR 0009 Decision 5 and ADR 0016 Decision 5 (I5a): a bundle with two contents under one
    record-of-fact ID is rejected with ``ConflictingRecordError`` (a corrected record is a new
    record with a new ID). Any kind of record: a turn, a usage record, a tool call, a result."""
    twins: list[tuple[str, Callable[[dict[str, Any]], Any]]] = [
        ("turns", lambda r: r.update(text="changed")),
        ("usage_records", lambda r: r["quantities"][0].update(count=1)),
        ("tool_calls", lambda r: r.update(name="another_tool")),
        ("artifacts", lambda r: r.update(size=1)),
    ]
    for name, mutate in twins:
        doc = run_bundle_doc(tier="T1")
        twin = copy.deepcopy(doc[name][0])
        mutate(twin)
        doc[name].append(twin)
        assert_bundle_rejected(run_class(), doc, typed="ConflictingRecordError")
    export = export_doc("internal", [run_bundle_doc(tier="T0")])
    other = run_bundle_doc(tier="T0")
    other["turns"][0]["id"] = export["run_bundles"][0]["turns"][0]["id"]  # same ID, other content
    other["results"][0]["evidence_refs"][0]["ref_id"] = other["turns"][1]["id"]
    export["run_bundles"].append(other)
    assert_bundle_rejected(export_class(), export, typed="ConflictingRecordError")
    # the same record in two run bundles is rejected too, whatever its content
    twice = export_doc("internal", [run_bundle_doc(tier="T0")])
    clone = run_bundle_doc(tier="T0")
    clone["artifacts"][0] = copy.deepcopy(twice["run_bundles"][0]["artifacts"][0])
    clone["run"]["channel_maps"][0]["artifact_id"] = clone["artifacts"][0]["id"]
    clone["results"][0]["evidence_refs"][3]["ref_id"] = clone["artifacts"][0]["id"]
    twice["run_bundles"].append(clone)
    assert_bundle_rejected(export_class(), twice, "duplicate")


def test_run_bundle_rejects_a_record_listed_twice() -> None:
    """P (contract B2): a record that appears twice with identical content is rejected as
    ``[duplicate]``. The seam accepts identical duplicates because it merges documents of many
    sources, but a list of a bundle that repeats a usage record would count it twice in a cost
    total, so a bundle holds each record once. Same for a versioned entity (same ``(id,
    version)``); two versions of one catalog are fine."""
    for name in ("turns", "usage_records", "tool_calls", "artifacts", "results", "spans"):
        doc = run_bundle_doc(tier="T1")
        doc[name].append(copy.deepcopy(doc[name][0]))
        _reject(doc, "duplicate")
    doc = run_bundle_doc(tier="T1")
    doc["pricing_catalogs"].append(copy.deepcopy(doc["pricing_catalogs"][0]))
    _reject(doc, "duplicate")
    doc = run_bundle_doc(tier="T1")
    body = {k: v for k, v in doc["pricing_catalogs"][0].items() if k != "content_digest"}
    body["entries"] = copy.deepcopy(body["entries"])
    body["entries"][0]["price"]["amount"] = "9.99"
    rival = seal_entity(registered_class("PricingCatalog"), body)
    assert rival["content_digest"] != doc["pricing_catalogs"][0]["content_digest"]
    doc["pricing_catalogs"].append(rival)
    assert_bundle_rejected(run_class(), doc, typed="ConflictingVersionError")
    # an id may repeat across kinds only if the kinds differ, which typed IDs rule out
    doc = run_bundle_doc(tier="T1")
    doc["cost_summaries"].append(copy.deepcopy(doc["cost_summaries"][0]))
    _reject(doc, "duplicate")


# ======================================================================== membership and snapshot


def test_records_of_fact_name_no_run_except_usage_records() -> None:
    """N22 (decided by the owner): apart from ``UsageRecord.run_id``, no model of the package has a
    ``run_id`` member: a record of fact belongs to its run through the ``RunBundle``, which embeds
    the ``Run`` as ``run``. ``DatasetItem.source_run_id`` is provenance, not membership (O7), and
    the entries of P1-14 name records by ``record_id``, ``subject_id`` and ``item_id``."""
    registry = api("registry")
    owners = sorted(m.__name__ for m in registry.model_classes() if "run_id" in m.model_fields)
    assert owners == ["UsageRecord"]
    assert "run_id" not in run_class().model_fields
    assert run_class().model_fields["run"].annotation is registered_class("Run")
    assert "source_run_id" in registered_class("DatasetItem").model_fields
    assert "source_run_id" not in export_class().model_fields


def test_every_record_of_a_kind_is_checked_in_the_run_bundle() -> None:
    """The rules of a run bundle run over every record of a kind, not only the first one: the
    third exchange, the second transcript, the second channel map, the second artifact, the second
    usage record of a cost record, a duplicated test profile."""
    doc = run_bundle_doc(tier="T1")
    third = copy.deepcopy(doc["exchanges"][0])
    third["id"] = new_id("exc")
    third["index"] = 2
    doc["exchanges"].append(third)
    load_run(doc)
    again = copy.deepcopy(doc)
    again["exchanges"][2]["index"] = 0
    _reject(again, "exchange")
    # a second transcript with two turns of one seq
    second = copy.deepcopy(doc)
    transcript = copy.deepcopy(second["transcripts"][0])
    transcript["id"] = new_id("tsc")
    second["transcripts"].append(transcript)
    turns = []
    for seq in (0, 0):
        turn = copy.deepcopy(second["turns"][0])
        turn.update(id=new_id("turn"), transcript_id=transcript["id"], seq=seq, exchange_index=None)
        turns.append(turn)
    second["turns"].extend(turns)
    _reject(second, "transcript")
    # a second channel map and a second artifact
    maps = copy.deepcopy(doc)
    extra_art = copy.deepcopy(maps["artifacts"][0])
    extra_art["id"] = new_id("art")
    maps["artifacts"].append(extra_art)
    extra_map = copy.deepcopy(maps["run"]["channel_maps"][0])
    extra_map["artifact_id"] = extra_art["id"]
    maps["run"]["channel_maps"].append(extra_map)
    load_run(maps)
    bad_map = copy.deepcopy(maps)
    bad_map["run"]["channel_maps"][1]["artifact_id"] = new_id("art")
    _reject(bad_map, "unresolved")
    short = copy.deepcopy(maps)
    short["artifacts"][1]["audio_format"]["channels"] = 1
    short["run"]["channel_maps"][1]["channels"] = [
        {"index": 0, "role": "caller"},
        {"index": 1, "role": "agent"},
    ]
    _reject(short, "channel_map")
    dangling = copy.deepcopy(maps)
    dangling["artifacts"][1]["turn_ref"] = new_id("turn")
    _reject(dangling, "unresolved")
    # a cost record that lists two usage records, the second unknown
    costs = copy.deepcopy(doc)
    costs["cost_records"][0]["usage_record_ids"] = [
        costs["cost_records"][0]["usage_record_ids"][0],
        new_id("use"),
    ]
    _reject(costs, "unresolved")
    # a test profile listed twice
    w = world(tier="T0", with_profile=True, profile_digest="plain")
    profile_doc = w.parts["profile"].model_dump(mode="json", by_alias=True)
    twice = w.copy()
    twice["test_profiles"] = [profile_doc, copy.deepcopy(profile_doc)]
    _reject(twice, "duplicate")


def test_run_bundle_names_its_snapshot() -> None:
    """N35 and ADR 0006 Decision 1: a run names its configuration only through
    ``config_snapshot_id``, so the bundle's snapshot is the one the run names."""
    doc = run_bundle_doc(tier="T0")
    doc["run"]["config_snapshot_id"] = new_id("snap")
    error = _reject(doc, "snapshot")
    assert doc["run"]["config_snapshot_id"] in str(error)


def test_usage_record_run_membership() -> None:
    """ADR 0001 (``UsageRecord.run_id`` is the one record of fact that names its run) and N22: every
    usage record of a bundle belongs to the bundle's run; one that names another run is rejected
    and the message names the usage record and both runs, not any value."""
    doc = run_bundle_doc(tier="T1")
    other = new_id("run")
    doc["usage_records"][1]["run_id"] = other
    error = _reject(doc, "membership")
    assert doc["usage_records"][1]["id"] in str(error)
    assert doc["run"]["id"] in str(error)
    # a usage record of the right run in another domain is fine
    good = run_bundle_doc(tier="T1")
    assert {u["domain"] for u in good["usage_records"]} == {"evaluation", "agent_under_test"}
    load_run(good)
    # an export of two runs checks each run bundle against its own run
    first, second = run_bundle_doc(tier="T0"), run_bundle_doc(tier="T0")
    second["usage_records"][0]["run_id"] = first["run"]["id"]
    assert_bundle_rejected(export_class(), export_doc("internal", [first, second]), "membership")


# ================================================================================ references by ID


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("turn.transcript_id", lambda d: d["turns"][0].update(transcript_id=new_id("tsc"))),
        (
            "last turn.transcript_id",
            lambda d: d["turns"][-1].update(transcript_id=new_id("tsc")),
        ),
        ("artifact.turn_ref", lambda d: d["artifacts"][0].update(turn_ref=new_id("turn"))),
        (
            "channel_map.artifact_id",
            lambda d: d["run"]["channel_maps"][0].update(artifact_id=new_id("art")),
        ),
        (
            "evidence.turn",
            lambda d: d["results"][0]["evidence_refs"][0].update(ref_id=new_id("turn")),
        ),
        (
            "evidence.exchange",
            lambda d: d["results"][0]["evidence_refs"][1].update(ref_id=new_id("exc")),
        ),
        (
            "evidence.tool_call",
            lambda d: d["results"][0]["evidence_refs"][2].update(ref_id=new_id("tcl")),
        ),
        (
            "evidence.artifact_range",
            lambda d: d["results"][0]["evidence_refs"][3].update(ref_id=new_id("art")),
        ),
        (
            "evidence.span",
            lambda d: d["results"][0]["evidence_refs"][4].update(ref_id=new_id("spn")),
        ),
        ("verdict.result_id", lambda d: d["verdict"]["checks"][0].update(result_id=new_id("res"))),
        (
            "cost.usage_record_ids",
            lambda d: d["cost_records"][0].update(usage_record_ids=[new_id("use")]),
        ),
        (
            "last cost.usage_record_ids",
            lambda d: d["cost_records"][-1].update(usage_record_ids=[new_id("use")]),
        ),
        (
            "last verdict.result_id",
            lambda d: d["verdict"]["checks"][-1].update(result_id=new_id("res")),
        ),
        (
            "last evidence ref",
            lambda d: d["results"][0]["evidence_refs"][-1].update(ref_id=new_id("spn")),
        ),
    ],
)
def test_run_bundle_reference_by_id_must_resolve(
    label: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    """N22 (the bundle is the unit of a run, so a reference between its records resolves inside it):
    a turn names a transcript of the bundle, an artifact's ``turn_ref`` a turn, a channel map an
    artifact, an evidence reference one of the five evidence kinds, a verdict check a result, a
    cost record its usage records. A dangling reference is ``[unresolved]`` and the message names
    the dangling ID (never a value). ADR 0012 I6 is the bundle half of
    ``test_evidence_ref_resolves_in_bundle``."""
    doc = run_bundle_doc(tier="T1")
    mutate(doc)
    error = _reject(doc, "unresolved")
    assert str(error).count("[unresolved]") >= 1, label


def test_evidence_ref_resolves_in_run_bundle() -> None:
    """The bundle half of ``test_evidence_ref_resolves_in_bundle`` (ADR 0012 Decision 3, I6): every
    kind of ``EvidenceRef`` of the fixture resolves inside the bundle, and the error of a dangling
    one names its ``ref_id``. An ID of another kind in ``ref_id`` is rejected by ``EvidenceRef``
    itself (a ValidationError, not a bundle rule)."""
    doc = run_bundle_doc(tier="T1")
    kinds = [e["kind"] for e in doc["results"][0]["evidence_refs"]]
    assert set(kinds) == {"turn", "exchange", "tool_call", "span", "artifact_range"}
    load_run(doc)
    dangling = new_id("turn")
    doc["results"][0]["evidence_refs"][0]["ref_id"] = dangling
    error = _reject(doc, "unresolved")
    assert dangling in str(error)
    # evidence of a result may point at a record of another kind that exists: kind mismatches
    wrong = run_bundle_doc(tier="T1")
    wrong["results"][0]["evidence_refs"][0]["ref_id"] = wrong["tool_calls"][0]["id"]
    with pytest.raises(ValidationError):
        run_class().model_validate(wrong)


def test_bundle_declared_language_transcript_ids_resolve() -> None:
    """SCOPE P1-14 (binding name; revision 10, N14; ADR 0004 Decision 6g): every transcript listed
    in a result's ``declared_language_transcript_ids`` exists in the bundle and has a
    ``declared_language``. An unknown ID is ``[unresolved]``; a transcript without a declaration
    is ``[declared_language]`` (the declaration is the evidence for
    ``language_source=declared``)."""
    doc = run_bundle_doc(tier="T0", declared=True)
    bundle = load_run(doc)
    assert bundle.results[0].language_source == "declared"
    assert bundle.transcripts[0].declared_language is not None
    missing = copy.deepcopy(doc)
    missing["results"][0]["declared_language_transcript_ids"] = [new_id("tsc")]
    _reject(missing, "unresolved")
    undeclared = copy.deepcopy(doc)
    del undeclared["transcripts"][0]["declared_language"]
    undeclared["transcripts"][0]["detected_language"] = "mr-IN"
    undeclared["transcripts"][0]["detected_language_raw"] = "mr_IN"
    _reject(undeclared, "declared_language")
    # a second transcript without a declaration, listed next to the right one
    second = copy.deepcopy(doc)
    extra = copy.deepcopy(second["transcripts"][0])
    extra["id"] = new_id("tsc")
    del extra["declared_language"]
    extra["detected_language"] = "mr-IN"
    extra["detected_language_raw"] = "mr_IN"
    second["transcripts"].append(extra)
    second["results"][0]["declared_language_transcript_ids"] = sorted(
        [doc["transcripts"][0]["id"], extra["id"]]
    )
    _reject(second, "declared_language")
    # an observed result lists none (listing one without language_source=declared is a record
    # rule, not a bundle rule)
    ok = run_bundle_doc(tier="T0")
    assert ok["results"][0]["declared_language_transcript_ids"] == []
    load_run(ok)


# ==================================================================================== the results


def test_bundle_scorer_implementation_ref_equals_definition() -> None:
    """SCOPE P1-14 (binding: "P-61's ``implementation_ref`` equality is also checked in the
    bundle"; ADR 0010 Decision 5a): a result that ran a deterministic scorer carries the
    definition's ``implementation_ref``, compared by equality and case-sensitively. The ``version``
    is verbatim and never compared. A scorer on a judge result, or none on a scorer result, is
    ``[result]`` too."""
    doc = run_bundle_doc(tier="T1")
    assert doc["results"][1]["scorer"]["implementation_ref"] == "builtin:p95_latency"
    for ref in ("builtin:p99_latency", "BUILTIN:P95_LATENCY", "builtin:p95_latency "):
        bad = copy.deepcopy(doc)
        bad["results"][1]["scorer"]["implementation_ref"] = ref
        if ref.endswith(" "):
            with pytest.raises(ValidationError):  # not a MachineKey: the record model rejects it
                run_class().model_validate(bad)
            continue
        error = _reject(bad, "result")
        assert "implementation_ref" in str(error)
        assert doc["results"][1]["id"] in str(error)
    for version in ("1.4.2", "9.9.9-rc1", "2026-10-23", "v0", "a"):
        ok = copy.deepcopy(doc)
        ok["results"][1]["scorer"]["version"] = version
        load_run(ok)
    no_scorer = copy.deepcopy(doc)
    del no_scorer["results"][1]["scorer"]
    with pytest.raises(ValidationError):
        run_class().model_validate(no_scorer)  # scored deterministic results record their scorer
    extra = copy.deepcopy(doc)
    extra["results"][0]["scorer"] = {"implementation_ref": "builtin:p95_latency", "version": "1"}
    _reject(extra, "result")
    swapped = copy.deepcopy(doc)  # the scorer of one metric on the result of the other
    swapped["results"][1]["scorer"]["implementation_ref"] = "builtin:goal_completion"
    _reject(swapped, "result")


def test_run_bundle_result_is_checked_against_its_definition() -> None:
    """ADR 0010 Decision 4a and ADR 0004 Decision 6g: the rules that need the definition run in the
    bundle (``check_result_against_definition`` over the snapshot's definitions): a scored value
    that does not fit ``value_type``, ``passed`` that is not the pass rule applied to the value, a
    missing ``language_source`` for a language-bound metric, a judge result without
    ``JudgeInfo``."""
    doc = run_bundle_doc(tier="T1")
    load_run(doc)
    bad_value = copy.deepcopy(doc)
    bad_value["results"][0]["value"] = "yes"
    _reject(bad_value, "result")
    bad_passed = copy.deepcopy(doc)
    bad_passed["results"][1]["passed"] = False
    _reject(bad_passed, "result")
    bad_lang = copy.deepcopy(doc)
    del bad_lang["results"][1]["language_source"]
    _reject(bad_lang, "result")
    no_judge = copy.deepcopy(doc)
    del no_judge["results"][0]["judge"]
    _reject(no_judge, "result")


def test_run_bundle_result_metric_must_resolve_to_a_snapshot_definition() -> None:
    """ADR 0009 Decision 4 and 7, E5: a result names its definition by ``{id, version,
    content_digest?}``. The reference resolves inside the bundle (the snapshot embeds the
    definitions) and the digest is verified when present. An unknown definition or version raises
    ``UnresolvedReferenceError`` (a ``BundleError``) and a digest that differs
    ``RefDigestMismatchError``, both unwrapped. The same for the ``metric_ref`` of a verdict
    check."""
    doc = run_bundle_doc(tier="T1")
    assert "content_digest" in doc["results"][0]["metric"]
    load_run(doc)
    breakers: list[Callable[[dict[str, Any]], Any]] = [
        lambda d: d["results"][0]["metric"].update(id=new_id("met")),
        lambda d: d["results"][0]["metric"].update(version=7),
        lambda d: d["results"][1]["metric"].update(version=2),
    ]
    for mutate in breakers:
        bad = copy.deepcopy(doc)
        mutate(bad)
        error = _typed(bad, "UnresolvedReferenceError")
        assert isinstance(error, api("errors").BundleError)
    wrong_digest = copy.deepcopy(doc)
    wrong_digest["results"][0]["metric"]["content_digest"]["value"] = "0" * 64
    _typed(wrong_digest, "RefDigestMismatchError")
    in_verdict = copy.deepcopy(doc)
    in_verdict["verdict"]["checks"][0]["metric_ref"]["content_digest"] = {
        "scheme": PLAIN,
        "value": "0" * 64,
    }
    _typed(in_verdict, "RefDigestMismatchError")
    dangling_check = copy.deepcopy(doc)
    dangling_check["verdict"]["checks"][1]["metric_ref"]["id"] = new_id("met")
    _typed(dangling_check, "UnresolvedReferenceError")
    # a keyed digest on the reference cannot be compared without the key: not a mismatch, and never
    # reported as a match
    keyed = copy.deepcopy(doc)
    keyed["results"][0]["metric"]["content_digest"] = {
        "scheme": KEYED,
        "value": "0" * 64,
        "key_ref": {"key_id": "k-1"},
    }
    load_run(keyed)
    # no digest at all is allowed (E5: optional)
    plain = copy.deepcopy(doc)
    del plain["results"][0]["metric"]["content_digest"]
    load_run(plain)


def test_run_bundle_has_one_result_per_metric() -> None:
    """N33 (open part, decided at P1-14 as contract B5): a bundle carries no rescoring beside the
    original result; a second result of one metric ``(id, version)`` is ``[rescoring]``. A
    rescoring is a new RunBundle."""
    doc = run_bundle_doc(tier="T1")
    twin = copy.deepcopy(doc["results"][0])
    twin["id"] = new_id("res")
    twin["value"] = False
    twin["passed"] = False
    doc["results"].append(twin)
    _reject(doc, "rescoring")
    other = run_bundle_doc(tier="T1")
    bundle = load_run(other)
    assert len({(r.metric.id, r.metric.version) for r in bundle.results}) == len(bundle.results)


def test_run_bundle_verdict_is_checked_against_rubric_and_results() -> None:
    """ADR 0013 (verdict) as a bundle rule: ``check_verdict(verdict, rubric=, bindings=, results=)``
    over the snapshot's rubric and bindings and the bundle's results. A check whose outcome
    differs from its result, a missing required check and a stale weighted score are ``[verdict]``.
    A bundle without a verdict is valid (a run that was not scored)."""
    doc = run_bundle_doc(tier="T1")
    load_run(doc)
    stale = copy.deepcopy(doc)
    stale["results"][1].update(value=1000, passed=False)  # the verdict still says: passed
    _reject(stale, "verdict")
    missing = copy.deepcopy(doc)
    missing["verdict"].update(
        required_total=1,
        passed=1,
        checks=[doc["verdict"]["checks"][0]],
        weighted_score=None,
        weight_coverage=None,
    )
    for key in ("weighted_score", "weight_coverage"):
        missing["verdict"].pop(key)
    _reject(missing, "verdict")
    no_verdict = copy.deepcopy(doc)
    no_verdict["verdict"] = None
    assert load_run(no_verdict).verdict is None
    absent = copy.deepcopy(doc)
    del absent["verdict"]
    assert load_run(absent).verdict is None
    no_results = copy.deepcopy(doc)
    no_results["results"] = []
    no_results["verdict"] = None
    load_run(no_results)
    only_verdict = copy.deepcopy(doc)
    only_verdict["results"] = []
    _reject(only_verdict, "unresolved")


# ============================================================ transcripts, exchanges and tool calls


def test_run_bundle_transcript_rules_run_over_the_bundles_turns() -> None:
    """ADR 0011 I1 and I11, N21 (turns are separate records): ``check_transcript`` runs for each
    transcript over the turns of the bundle that name it, with the bundle's exchanges. A repeated
    ``seq``, offsets without a ``timeline_origin`` and a turn naming an exchange index that no
    exchange has are ``[transcript]``; a repeated exchange index is ``[exchange]``. A transcript
    with no turn in the bundle is fine (an empty call, or a withheld text)."""
    doc = run_bundle_doc(tier="T1")
    repeated = copy.deepcopy(doc)
    repeated["turns"][1]["seq"] = repeated["turns"][0]["seq"]
    error = _reject(repeated, "transcript")
    assert "seq" in str(error)
    offsets = copy.deepcopy(doc)
    offsets["turns"][0].update(start_ms=0, end_ms=500, timing_source="audio_alignment")
    error = _reject(offsets, "transcript")
    assert "timeline_origin" in str(error)
    dangling = copy.deepcopy(doc)
    dangling["turns"][0]["exchange_index"] = 7
    error = _reject(dangling, "transcript")
    assert "7" in str(error)
    twice = copy.deepcopy(doc)
    twice["exchanges"][1]["index"] = 0
    for turn in twice["turns"]:
        turn["exchange_index"] = 0
    error = _reject(twice, "exchange")
    assert "0" in str(error)
    empty = copy.deepcopy(doc)
    empty["turns"] = []
    empty["results"][0]["evidence_refs"] = [
        e for e in empty["results"][0]["evidence_refs"] if e["kind"] != "turn"
    ]
    # T-B13: a scored result of a metric that reads a transcript has read a turn, so an empty call
    # holds no such result (nothing was scored)
    empty["results"] = []
    empty["verdict"] = None
    assert load_run(empty).turns == []
    # a timeline origin on a transcript that has no turn left is not an error of the bundle
    with_origin = copy.deepcopy(empty)
    with_origin["transcripts"][0]["timeline_origin"] = {
        "kind": "artifact",
        "ref": with_origin["artifacts"][0]["id"],
    }
    load_run(with_origin)


def test_run_bundle_tool_call_seq_is_unique_in_the_bundle() -> None:
    """ADR 0012 I4 and N22: ``seq`` is unique among the tool calls of one run, so among the tool
    calls of the bundle (``check_tool_call_seqs``). ``[tool_call_seq]``, naming the seq."""
    doc = run_bundle_doc(tier="T1")
    doc["tool_calls"][1]["seq"] = doc["tool_calls"][0]["seq"]
    error = _reject(doc, "tool_call_seq")
    assert str(doc["tool_calls"][0]["seq"]) in str(error)
    ok = run_bundle_doc(tier="T1")
    ok["tool_calls"][1]["seq"] = 41
    load_run(ok)


def test_run_bundle_channel_map_is_checked_against_its_artifact() -> None:
    """ADR 0012 Decision 5 (``check_channel_map``): a channel index beyond the artifact's channel
    count is ``[channel_map]``, checked in the bundle where the artifact is known. A map for an
    artifact that is not in the bundle is ``[unresolved]`` (see the reference test)."""
    doc = run_bundle_doc(tier="T1")
    assert doc["artifacts"][0]["audio_format"]["channels"] == 2
    doc["run"]["channel_maps"][0]["channels"] = [
        {"index": 0, "role": "caller"},
        {"index": 2, "role": "agent"},
    ]
    _reject(doc, "channel_map")
    mono = run_bundle_doc(tier="T1")
    mono["artifacts"][0]["audio_format"]["channels"] = 1
    _reject(mono, "channel_map")


# ===================================================================== pricing, usage and cost


def test_pricing_catalog_ref_digest_in_the_bundle_seam() -> None:
    """N26 and N52 (the bundle seam reads every catalog digest as ``content_digest``): the
    snapshot's ``PricingCatalogRef`` and each ``CostRecord.pricing_snapshot`` (mapped to
    ``{id: catalog_id, version, content_digest}``) are verified against the embedded catalog. A
    different digest of the same scheme raises ``RefDigestMismatchError`` unwrapped. A catalog that
    is not embedded, or only another version of it, leaves the reference unverified, not rejected.
    A keyed digest on the reference cannot be compared and is neither a match nor a mismatch."""
    doc = run_bundle_doc(tier="T1")
    load_run(doc)
    flipped = {"scheme": PLAIN, "value": "0" * 64}
    snap_bad = copy.deepcopy(doc)
    snap_bad["snapshot"]["pricing_catalog"]["content_digest"] = flipped
    snap_bad["snapshot"] = resnap(snap_bad["snapshot"])
    _typed(snap_bad, "RefDigestMismatchError")
    cost_bad = copy.deepcopy(doc)
    computed = next(c for c in cost_bad["cost_records"] if c["basis"] == "computed")
    computed["pricing_snapshot"]["content_digest"] = flipped
    _typed(cost_bad, "RefDigestMismatchError")
    not_embedded = copy.deepcopy(doc)
    not_embedded["pricing_catalogs"] = []
    load_run(not_embedded)
    other_version = copy.deepcopy(doc)
    other_version["snapshot"]["pricing_catalog"]["version"] = 2
    other_version["snapshot"]["pricing_catalog"]["content_digest"] = flipped
    other_version["snapshot"] = resnap(other_version["snapshot"])
    for c in other_version["cost_records"]:
        if c["basis"] == "computed":
            c["pricing_snapshot"]["version"] = 2
            c["pricing_snapshot"]["content_digest"] = flipped
    load_run(other_version)
    keyed = copy.deepcopy(doc)
    keyed["snapshot"]["pricing_catalog"]["content_digest"] = {
        "scheme": KEYED,
        "value": "0" * 64,
        "key_ref": {"key_id": "k-1"},
    }
    keyed["snapshot"] = resnap(keyed["snapshot"])
    load_run(keyed)


def test_run_bundle_cost_record_names_entries_of_its_catalog() -> None:
    """ADR 0014 Decision 16b (N52, ``cost.check_pricing_snapshot``) in the bundle: when the catalog
    of a computed cost record is embedded, every ``entry_keys`` member is an entry of it. A key
    that no entry has is ``[cost]``. Without the catalog the keys cannot be checked and are not
    rejected."""
    doc = run_bundle_doc(tier="T1")
    computed = next(c for c in doc["cost_records"] if c["basis"] == "computed")
    computed["pricing_snapshot"]["entry_keys"] = ["no-such-entry"]
    _reject(doc, "cost")
    without = copy.deepcopy(doc)
    without["pricing_catalogs"] = []
    load_run(without)


def test_run_bundle_cost_summary_is_checked_against_the_bundles_records() -> None:
    """ADR 0014 Decision 16a and 16b, N22 (``cost.check_cost_summary`` is a bundle-level check): a
    total that is not the exact sum of the included amounts, a record the summary names that the
    bundle does not hold, and a cost record that is neither included nor excluded are
    ``[summary]``. One summary per domain."""
    doc = run_bundle_doc(tier="T1")
    load_run(doc)
    wrong_total = copy.deepcopy(doc)
    wrong_total["cost_summaries"][0]["total"] = {"amount": "1.66", "currency": "USD"}
    _reject(wrong_total, "summary")
    unknown = copy.deepcopy(doc)
    unknown["cost_summaries"][0]["included"][0] = new_id("cst")
    _reject(unknown, "summary")
    unaccounted = copy.deepcopy(doc)
    extra_usage = copy.deepcopy(unaccounted["usage_records"][0])
    extra_usage["id"] = new_id("use")
    unaccounted["usage_records"].append(extra_usage)
    extra_cost = copy.deepcopy(unaccounted["cost_records"][0])
    extra_cost["id"] = new_id("cst")
    extra_cost["usage_record_ids"] = [extra_usage["id"]]
    unaccounted["cost_records"].append(extra_cost)
    _reject(unaccounted, "summary")
    twice = copy.deepcopy(doc)
    second = copy.deepcopy(twice["cost_summaries"][0])
    second["id"] = new_id("csum")
    twice["cost_summaries"].append(second)
    _reject(twice, "summary")
    none = copy.deepcopy(doc)
    none["cost_summaries"] = []
    load_run(none)  # a summary is a convenience record; a run may be exported before it is built


def test_run_bundle_cost_summaries_are_checked_per_domain() -> None:
    """ADR 0014 Decision 16 (``CostSummary`` is the cost of one domain of a run): a bundle may hold
    one summary for the evaluation domain and one for the agent-under-test domain, each checked
    against the cost records of its own domain. A summary that names a cost record of another
    domain is ``[summary]``."""
    doc = run_bundle_doc(tier="T1")
    agent_usage = next(u for u in doc["usage_records"] if u["domain"] == "agent_under_test")
    cost = record_reported_doc(domain="agent_under_test", usage_record_ids=[agent_usage["id"]])
    summary = summary_doc(
        domain="agent_under_test",
        total={"amount": "1.5", "currency": "USD"},
        included=[cost["id"]],
    )
    doc["cost_records"].append(cost)
    doc["cost_summaries"].append(summary)
    bundle = load_run(doc)
    assert {s.domain for s in bundle.cost_summaries} == {"evaluation", "agent_under_test"}
    crossed = copy.deepcopy(doc)
    crossed["cost_summaries"][1]["included"] = [doc["cost_records"][0]["id"]]
    crossed["cost_summaries"][1]["total"] = {"amount": "1.5", "currency": "USD"}
    _reject(crossed, "summary")


# ================================================================================= digests of refs


def test_run_bundle_verifies_the_digest_of_a_test_profile_reference() -> None:
    """ADR 0009 Decision 7 and ADR 0006 Decision 5 (``test_ref_digest_must_match_resolved_entity_
    in_bundle`` at the bundle level): when the bundle embeds the ``TestProfile`` that the
    evaluator's ``TestProfileRef`` names (same ``(id, version)``), a digest of the same scheme on
    the
    reference must equal the profile's: ``RefDigestMismatchError``. A keyed reference digest cannot
    be compared without the key and is accepted unverified; no digest at all is accepted in a
    RunBundle (the drop-or-key rule is an export rule); another version of the profile is
    ignored."""
    w = world(tier="T0", with_profile=True, profile_digest="plain")
    profile_doc = w.parts["profile"].model_dump(mode="json", by_alias=True)
    doc = w.copy()
    doc["test_profiles"] = [profile_doc]
    load_run(doc)
    bad = copy.deepcopy(doc)
    bad["snapshot"]["evaluator"]["test_profile"]["content_digest"]["value"] = "0" * 64
    _typed(bad, "RefDigestMismatchError")
    keyed = world(tier="T0", with_profile=True, profile_digest="keyed")
    keyed_doc = keyed.copy()
    keyed_doc["test_profiles"] = [keyed.parts["profile"].model_dump(mode="json", by_alias=True)]
    load_run(keyed_doc)
    none = world(tier="T0", with_profile=True)
    none_doc = none.copy()
    none_doc["test_profiles"] = [none.parts["profile"].model_dump(mode="json", by_alias=True)]
    load_run(none_doc)
    assert none.parts["profile"].version == 1
    plain_doc = plain_profile_digest(w.parts["profile"])
    assert plain_doc["scheme"] == PLAIN


# ================================================================== privacy markers (A-31, P-47)


def test_run_bundle_rejects_a_marker_looser_than_the_derivation() -> None:
    """A-31 and ADR 0016 Decision 5, I5 (P-47): the stored marker of an artifact or a transcript
    may be stricter than ``derive_human_subject(run, turns)`` over the bundle, never looser.
    ``human_subject=false`` on a run with an ``unknown``-role turn, on a human caller's run, or on
    a simulated run without role evidence is ``[marker]``; a stricter marker needs a reason, and
    any of the three reasons is accepted."""
    unknown = run_bundle_doc(tier="T0", origin="simulated", unknown_turn=True)
    _reject(unknown, "marker")
    human_false = run_bundle_doc(tier="T0", origin="human", marker=privacy_synthetic())
    _reject(human_false, "marker")
    no_evidence = run_bundle_doc(tier="T0", origin="simulated")
    no_evidence["turns"] = []
    no_evidence["results"] = []
    no_evidence["verdict"] = None
    no_evidence["run"]["channel_maps"] = []
    no_evidence["run"]["available_inputs"] = ["audio", "transcript", "tool_calls", "usage"]
    _reject(no_evidence, "marker")
    # the same documents with a human-subject marker are valid
    ok_unknown = run_bundle_doc(
        tier="T0", origin="simulated", unknown_turn=True, marker=privacy_human()
    )
    load_run(ok_unknown)
    # stricter without a reason
    strict = run_bundle_doc(tier="T0", origin="simulated", marker=privacy_human())
    _reject(strict, "marker")
    for reason in ("manual", "real_person_likeness", "marked_before_role_evidence"):
        load_run(
            run_bundle_doc(
                tier="T0",
                origin="simulated",
                marker=privacy_human(human_subject_reason=reason),
            )
        )
    # the transcript's marker alone is enough to reject
    only_transcript = run_bundle_doc(
        tier="T0", origin="simulated", unknown_turn=True, marker=privacy_human()
    )
    only_transcript["transcripts"][0]["privacy"] = privacy_synthetic()
    error = _reject(only_transcript, "marker")
    assert only_transcript["transcripts"][0]["id"] in str(error)
    # the same document inside an export is rejected for the same reason, at any purpose
    for purpose in ("internal", "shared_export", "benchmark"):
        assert_bundle_rejected(export_class(), export_doc(purpose, [unknown]), "marker")


# ======================================================================== the seam and the loader


def test_bundle_seam_reference_resolution_is_opt_in() -> None:
    """ADR 0009 Decision 7 (revision 7 seam) extended by P1-14 (contract section 5):
    ``check_bundle_consistency(docs, refs=(), *, require_resolved=False)``. By default a
    reference to an ``(id, version)`` that no document holds is not an error (the batch 1 rule,
    unchanged). With ``require_resolved=True`` it raises ``errors.UnresolvedReferenceError``, a
    ``BundleError`` whose message names the id and version. A resolved reference still has its
    digest compared, and a missing ``version`` is still a ``BundleError`` (or a rule of the
    reference)."""
    check = bundle_fn("check_bundle_consistency")
    errors = api("errors")
    assert issubclass(errors.UnresolvedReferenceError, errors.BundleError)
    persona: dict[str, Any] = {
        "id": str(new_id("per")),
        "version": 1,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "content_digest": {"scheme": PLAIN, "value": "a" * 64},
    }
    other = {**persona, "id": str(new_id("per"))}
    ref = {"id": persona["id"], "version": 1}
    check([persona], [ref])
    check([persona], [ref], require_resolved=True)
    check([persona], [{"id": other["id"], "version": 1}])
    with pytest.raises(errors.UnresolvedReferenceError) as caught:
        check([persona], [{"id": other["id"], "version": 1}], require_resolved=True)
    assert other["id"] in str(caught.value)
    with pytest.raises(errors.UnresolvedReferenceError):
        check([persona], [{"id": persona["id"], "version": 2}], require_resolved=True)
    check([persona], [{"id": persona["id"], "version": 2}])  # default: another version is fine
    with pytest.raises(errors.RefDigestMismatchError):
        check(
            [persona],
            [{**ref, "content_digest": {"scheme": PLAIN, "value": "b" * 64}}],
            require_resolved=True,
        )
    check(
        [persona],
        [{**ref, "content_digest": {"scheme": PLAIN, "value": "a" * 64}}],
        require_resolved=True,
    )
    check([], [], require_resolved=True)
    with pytest.raises(TypeError):
        check([persona], [ref], True)


def _size_of(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc).encode("utf-8")


def test_bundle_loader_size_limit() -> None:
    """S-N94 (ADR 0015 Known gaps; decided: no schema maximum, a per-bundle limit at the loader):
    ``loader.load_bundle(model, data, *, max_bytes=DEFAULT_MAX_BUNDLE_BYTES, max_depth=...)`` loads
    a ``RunBundle`` or an ``ExportBundle`` (``str`` or ``bytes``). The size is the length of the
    UTF-8 bytes and is checked before any parsing; a bundle over the limit raises
    ``errors.BundleSizeError`` (a ``BundleError``), a bundle exactly at the limit loads. The
    default is a positive int between 64 MiB and 1 GiB (long recordings are sealed into a bundle).
    The message names the two sizes and no content. ``load_model`` keeps no limit."""
    loader = api("loader")
    errors = api("errors")
    assert issubclass(errors.BundleSizeError, errors.BundleError)
    default = loader.DEFAULT_MAX_BUNDLE_BYTES
    assert type(default) is int
    assert 64 * 2**20 <= default <= 2**30
    doc = run_bundle_doc(tier="T0")
    data = _size_of(doc)
    text = data.decode("utf-8")
    assert loader.load_bundle(run_class(), data) == load_run(doc)
    assert loader.load_bundle(run_class(), text) == load_run(doc)
    assert loader.load_bundle(run_class(), data, max_bytes=len(data)) == load_run(doc)
    with pytest.raises(errors.BundleSizeError) as caught:
        loader.load_bundle(run_class(), data, max_bytes=len(data) - 1)
    assert str(len(data)) in str(caught.value)
    assert str(len(data) - 1) in str(caught.value)
    assert doc["turns"][0]["text"] not in str(caught.value)
    # the limit is checked first: input that is not even JSON is still a size error when too large
    with pytest.raises(errors.BundleSizeError):
        loader.load_bundle(run_class(), "{" * 1000, max_bytes=999)
    with pytest.raises(ValueError, match=r"."):
        loader.load_bundle(run_class(), "{" * 10)  # under the limit: a syntax error as ever
    export = export_doc("internal", [doc])
    exp_data = _size_of(export)
    assert loader.load_bundle(export_class(), exp_data, max_bytes=len(exp_data)) == load_export(
        export
    )
    with pytest.raises(errors.BundleSizeError):
        loader.load_bundle(export_class(), exp_data, max_bytes=len(exp_data) - 1)
    # the unit is bytes, not characters
    wide = copy.deepcopy(doc)
    wide["turns"][0]["text"] = "".join(chr(cp) for cp in (0x0928, 0x092E, 0x0938, 0x094D)) * 40
    wide_text = json.dumps(wide, ensure_ascii=False)
    wide_bytes = wide_text.encode("utf-8")
    assert len(wide_bytes) > len(wide_text)
    with pytest.raises(errors.BundleSizeError):
        loader.load_bundle(run_class(), wide_text, max_bytes=len(wide_text))
    assert (
        loader.load_bundle(run_class(), wide_text, max_bytes=len(wide_bytes)).turns[0].text
        == wide["turns"][0]["text"]
    )
    assert (
        loader.load_bundle(run_class(), wide_bytes, max_bytes=len(wide_bytes)).turns[0].text
        == wide["turns"][0]["text"]
    )
    for bad in (-1, 1.5, True, None, "10"):
        with pytest.raises((ValueError, TypeError)):
            loader.load_bundle(run_class(), data, max_bytes=bad)
    assert loader.load_model(run_class(), data) == load_run(doc)


def test_bundle_loader_keeps_the_strict_json_rules() -> None:
    """ADR 0008 Decision 6 and A-36 at the bundle level: ``load_bundle`` parses strictly like
    ``load_model``: a repeated object key (also nested inside a record) is ``DuplicateKeyError``,
    nesting beyond ``max_depth`` is ``DepthLimitExceeded``, ``NaN`` is a ``ValueError``, a record
    of a newer schema version is ``SchemaVersionTooNew`` and any other rule of the bundle is its
    own error. Only a ``RunBundle`` or an ``ExportBundle`` can be loaded: any other model is a
    ``TypeError`` before the data is read."""
    loader = api("loader")
    errors = api("errors")
    doc = run_bundle_doc(tier="T0")
    text = json.dumps(doc)
    duplicate = text.replace('"seq": 0,', '"seq": 0, "seq": 1,', 1)
    assert duplicate != text
    with pytest.raises(errors.DuplicateKeyError):
        loader.load_bundle(run_class(), duplicate)
    with pytest.raises(errors.DepthLimitExceeded):
        loader.load_bundle(run_class(), text, max_depth=3)
    with pytest.raises(errors.DepthLimitExceeded):
        loader.load_bundle(run_class(), text.encode("utf-8"), max_depth=3)
    with pytest.raises(errors.DuplicateKeyError):
        loader.load_bundle(run_class(), duplicate.encode("utf-8"))
    with pytest.raises(ValueError, match="NaN"):
        loader.load_bundle(run_class(), text.replace('"size": 1234567', '"size": NaN', 1))
    newer = copy.deepcopy(doc)
    newer["turns"][0]["schema_version"] = "0.0.2"
    with pytest.raises(errors.SchemaVersionTooNew):
        loader.load_bundle(run_class(), json.dumps(newer))
    broken = copy.deepcopy(doc)
    broken["usage_records"][0]["run_id"] = new_id("run")
    with pytest.raises(ValidationError):
        loader.load_bundle(run_class(), json.dumps(broken))
    with pytest.raises(TypeError):
        loader.load_bundle(registered_class("Run"), json.dumps(doc["run"]))
    with pytest.raises(TypeError):
        loader.load_bundle(dict, json.dumps(doc))


def test_run_bundle_embeds_only_what_the_schema_lists() -> None:
    """ADR 0015 Decision 7 and ADR 0016 I12 (the structural half is in ``test_sealed_payload.py``):
    a ``RunBundle`` embeds no ``SealedPayload`` at any depth and its JSON Schema never mentions
    one; an ``ExportBundle`` is the entity that embeds it, next to the redactions."""
    sealed = registered_class("SealedPayload")
    assert sealed not in embedded_models(run_class())
    assert sealed in embedded_models(export_class())
    assert "SealedPayload" not in json.dumps(schema_of(run_class()))
    assert "SealedPayload" in json.dumps(schema_of(export_class()))
    assert sealed not in embedded_models(registered_class("ConfigSnapshot"))
    for forbidden in ("sealed_payloads", "payload_redactions", "provenance_redactions"):
        assert forbidden not in run_class().model_fields
        assert forbidden in export_class().model_fields
