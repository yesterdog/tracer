# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-12: Run (ADR 0004 Decision 2, ADR 0009 Decision 5, ADR 0012 Decision 6, ADR 0016 Decisions 2
and 6).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.run``: the entity
``Run`` (``run``, a record of fact) with ``project_id``, ``agent_target`` (an ``AgentTargetRef``),
``config_snapshot_id``, ``mode``, ``caller_origin``, ``started_at?``, ``caller_external_id?``,
``detected_capabilities``, ``channel_maps``, ``available_inputs``, ``observed_tier``,
``external_refs`` and ``ingest_warnings``; the value object ``CallerExternalId`` is tested in
``test_privacy.py``. The check that ``agent_audio`` needs a channel map is in ``test_artifact.py``.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    build,
    contains_key,
    entity_class,
    error_texts,
    property_enum,
    registered_class,
    schema_accepts,
    schema_of,
)
from ._support_b3 import (
    DEVANAGARI,
    QUOTE_MARK,
    assert_input_hidden,
    assert_rejected3,
    channel_map_doc,
    doc3,
    entity_class3,
    ingest_warning_doc,
    make3,
    marked_id,
    marked_key,
    marked_set,
    new_id,
    privacy_human,
    run_doc,
)

INPUT_KINDS = {
    "audio",
    "agent_audio",
    "transcript",
    "agent_transcript",
    "turn_timing",
    "tool_calls",
    "tool_results",
    "spans",
    "usage",
    "reference_text",
    "expected_tool_calls",
}


def _run(**over: Any) -> Any:
    return make3("Run", **over)


def _reject(field: str | None = None, **over: Any) -> None:
    assert_rejected3(entity_class3("Run"), run_doc(**over), field=field)


def test_run_shape_and_enums() -> None:
    """Proposed name (SCOPE section 2 P1-12, ADR 0003 Decision 1): a run is an entity (``run``, a
    record of fact: no ``version``, no digest) with exactly the fields of the contract;
    ``mode`` is simulated or recorded (an ``enum``); ``available_inputs`` and
    ``detected_capabilities`` are unique ``InputKind`` lists; ``agent_target`` is a typed
    reference (``AgentTargetRef{id, version, content_digest?}``); ``observed_tier`` takes T0, T1
    and T2; it round-trips through JSON and its JSON Schema."""
    model = entity_class3("Run")
    entity_fields = {"id", "schema_version", "created_at", "metadata", "metadata_flags"}
    assert set(model.model_fields) - entity_fields == {
        "project_id",
        "agent_target",
        "config_snapshot_id",
        "mode",
        "caller_origin",
        "started_at",
        "caller_external_id",
        "detected_capabilities",
        "channel_maps",
        "available_inputs",
        "observed_tier",
        "external_refs",
        "ingest_warnings",
    }
    assert api("registry").entity_classes()["run"] is model
    assert not {"version", "content_digest"} & set(model.model_fields)
    assert set(property_enum(model, "mode")) == {"simulated", "recorded"}
    assert set(property_enum(model, "caller_origin")) == {"simulated", "human"}
    assert set(property_enum(model, "available_inputs")) == INPUT_KINDS
    assert set(property_enum(model, "detected_capabilities")) == INPUT_KINDS
    assert not contains_key(schema_of(model), "const")
    for tier in ("T0", "T1", "T2"):
        assert _run(observed_tier=tier).observed_tier == tier
    loaded = _run(
        available_inputs=["audio", "transcript"],
        detected_capabilities=["tool_calls", "spans"],
        ingest_warnings=[ingest_warning_doc()],
        external_refs=[{"system": "voicera", "kind": "call_id", "value": str(uuid.uuid4())}],
    )
    assert model.model_validate_json(loaded.model_dump_json()) == loaded
    assert schema_accepts(model, json.loads(loaded.model_dump_json()))
    assert str(loaded.id).startswith("run_")
    ref_model = registered_class("AgentTargetRef")
    assert issubclass(ref_model, api("base").VersionedRef)
    assert set(ref_model.model_fields) == {"id", "version", "content_digest"}
    assert str(loaded.agent_target.id).startswith("agt_")
    for bad_ref in (
        {"id": new_id("per"), "version": 1},
        {"id": new_id("agt")},
        "agt_x",
        None,
        {"id": new_id("agt"), "version": 0},
    ):
        _reject("agent_target", agent_target=bad_ref)
    for field, prefix in (("project_id", "prj"), ("config_snapshot_id", "snap")):
        _reject(field, **{field: new_id("run")})
        _reject(field, **{field: str(uuid.uuid4())})
        _reject(field, **{field: None})
        assert str(getattr(_run(**{field: new_id(prefix)}), field)).startswith(prefix + "_")
    for missing in (
        "project_id",
        "agent_target",
        "config_snapshot_id",
        "mode",
        "caller_origin",
        "observed_tier",
    ):
        doc = {k: v for k, v in run_doc().items() if k != missing}
        assert_rejected3(model, doc, field=missing)
    for field in ("available_inputs", "detected_capabilities"):
        _reject(field, **{field: ["audio", "audio"]})
        _reject(field, **{field: ["telepathy"]})
        _reject(field, **{field: "audio"})
    for bad_tier in ("t0", "T3", "tier1", "", 0):
        _reject("observed_tier", observed_tier=bad_tier)
    for bad in ("simulated ", "live", "replay", "", None):
        _reject("mode", mode=bad)
    empty = _run(available_inputs=[])
    assert (empty.channel_maps, empty.external_refs, empty.ingest_warnings) == ([], [], [])
    _reject("ingest_warnings", ingest_warnings=[{"code": "x"}])
    _reject("channel_maps", channel_maps=[{"artifact_id": new_id("art")}])
    _reject(None, api_key="x")


def test_observed_tier_t0_rejected_with_spans() -> None:
    """O3a, rule R1 (owner, revision 13; ADR 0004 Decision 2a; api-surface 22.9 item 14): a Run
    that lists ``spans`` in ``available_inputs`` cannot have ``observed_tier=T0``, because
    StageSpans exist only where the agent emits telemetry or an observer records them. The error
    is located at ``observed_tier``, names the rule (the message mentions ``spans``) and does not
    show the input, through ``model_validate`` and ``model_validate_json``. The same document at
    T1 or T2 is valid; T0 without ``spans``, and T1 or T2 without ``spans``, are valid (an
    over-claim is not detectable on one record); ``detected_capabilities`` is not read, so T0
    with a detected ``spans`` and no ``spans`` in ``available_inputs`` is valid. The JSON Schema
    half waits for the answer to N38 (an ``if``/``then`` or a ``KNOWN_SCHEMA_GAPS`` entry) and is
    not pinned here."""
    model = entity_class3("Run")
    marker = "+91 98765-43210"
    holder = {"value": marker, "privacy": privacy_human()}
    evidence = ["audio", "transcript", "spans"]
    doc = run_doc(observed_tier="T0", available_inputs=evidence, caller_external_id=holder)
    error = assert_rejected3(model, doc, field="observed_tier")
    located = [e for e in error.errors() if "observed_tier" in [str(p) for p in e["loc"]]]
    assert located
    assert any("spans" in str(e["msg"]).lower() for e in located), located
    for text in error_texts(error):
        assert marker not in text
    with pytest.raises(ValidationError) as via_json:
        model.model_validate_json(json.dumps(doc))
    assert any(
        "observed_tier" in [str(p) for p in e["loc"]] and "spans" in str(e["msg"]).lower()
        for e in via_json.value.errors()
    )
    for text in error_texts(via_json.value):
        assert marker not in text
    for tier in ("T1", "T2"):
        valid = run_doc(observed_tier=tier, available_inputs=evidence)
        loaded = model.model_validate(valid)
        assert loaded.observed_tier == tier
        assert model.model_validate_json(loaded.model_dump_json()) == loaded
    for tier, inputs in (
        ("T0", ["audio", "transcript"]),
        ("T0", []),
        ("T1", ["audio", "transcript"]),
        ("T2", []),
    ):
        assert model.model_validate(run_doc(observed_tier=tier, available_inputs=inputs))
    detected_only = run_doc(
        observed_tier="T0",
        available_inputs=["audio", "transcript"],
        detected_capabilities=["spans"],
    )
    assert model.model_validate(detected_only).detected_capabilities == ["spans"]
    # spans anywhere in the list, in any position, is evidence
    for inputs in (["spans"], ["spans", "audio"], ["audio", "spans", "usage"]):
        bad = run_doc(observed_tier="T0", available_inputs=inputs)
        assert_rejected3(model, bad, field="observed_tier")


def test_run_caller_origin_required() -> None:
    """ADR 0016 Decision 2, I2: ``caller_origin`` is required with no default and is exactly
    ``simulated`` or ``human``: forgetting it must not silently turn a recording of a real person
    into a simulated call. The mode does not imply it for a recorded run."""
    model = entity_class3("Run")
    doc = run_doc()
    assert_rejected3(
        model, {k: v for k, v in doc.items() if k != "caller_origin"}, field="caller_origin"
    )
    assert_rejected3(model, {**doc, "caller_origin": None}, field="caller_origin")
    for bad in ("Human", "bot", "unknown", "real", "", True, 1):
        _reject("caller_origin", caller_origin=bad)
    assert _run(caller_origin="human", mode="recorded").caller_origin == "human"
    assert _run(caller_origin="simulated", mode="recorded").caller_origin == "simulated"
    assert "caller_origin" in schema_of(model)["required"]
    assert "default" not in schema_of(model)["properties"]["caller_origin"]
    assert not schema_accepts(model, {k: v for k, v in doc.items() if k != "caller_origin"})


def test_simulated_mode_implies_simulated_caller() -> None:
    """ADR 0016 Decision 2, I2: ``mode=simulated`` implies ``caller_origin=simulated``: a simulated
    run is driven by our runner, so its caller cannot be a human. A recorded run may have either
    origin (a replay of a simulated call, or a real call)."""
    for mode, origin, valid in (
        ("simulated", "simulated", True),
        ("simulated", "human", False),
        ("recorded", "simulated", True),
        ("recorded", "human", True),
    ):
        if valid:
            run = _run(mode=mode, caller_origin=origin)
            assert (run.mode, run.caller_origin) == (mode, origin)
        else:
            _reject("caller_origin", mode=mode, caller_origin=origin)
    # the JSON entry point applies the rule too
    with pytest.raises(ValueError, match=r"simulated|caller_origin"):
        entity_class3("Run").model_validate_json(
            json.dumps(run_doc(mode="simulated", caller_origin="human"))
        )


def test_run_started_at_optional_utc() -> None:
    """P-36 input (ADR 0014 Decision 15, SCOPE P1-12): ``Run.started_at`` is a ``UtcDatetime`` or
    absent: set by our runner for runs we drive, taken from the source for imported recordings,
    absent when unknown, and never the ingest time (a missing value stays ``None`` and is not
    filled with ``created_at`` or the clock). Naive times, numbers and text are rejected."""
    model = entity_class3("Run")
    unknown = _run()
    assert unknown.started_at is None
    assert json.loads(unknown.model_dump_json())["started_at"] is None
    omitted = model.model_validate({k: v for k, v in run_doc().items() if k != "started_at"})
    assert omitted.started_at is None
    assert omitted.created_at is not None
    again = model.model_validate(run_doc(created_at="2030-01-01T00:00:00.000000Z"))
    assert again.started_at is None  # not the ingest time
    stamped = _run(started_at="2026-10-22T15:30:00+05:30")
    assert json.loads(stamped.model_dump_json())["started_at"] == "2026-10-22T10:00:00.000000Z"
    assert stamped.started_at.utcoffset().total_seconds() == 0
    assert _run(started_at="2026-10-22T10:00:00Z").started_at is not None
    for bad in (
        "2026-10-22T10:00:00",
        "2026-10-22",
        1_790_000_000,
        1.5,
        True,
        "now",
        "",
        "2026-10-22T10:00:00.1234567Z",
        "2026-10-22T23:59:60Z",
    ):
        _reject("started_at", started_at=bad)
    assert model.model_validate_json(stamped.model_dump_json()) == stamped
    assert "started_at" not in schema_of(model).get("required", [])


def test_external_refs_never_ids() -> None:
    """ADR 0001 I6, ADR 0012 I14: a foreign identifier (a Pipecat session, a VoicEra call ID, a
    trace ID) is recorded as an ``ExternalRef{system, kind, value}`` in ``Run.external_refs`` with
    the value verbatim, and never becomes an ID of ours: ``from_external`` mints a fresh v7 ID that
    differs from the external UUID, and a Run cannot take a bare or non-v7 UUID, or an ID of
    another kind, as its own ID or in an ID field."""
    ids = api("ids")
    external = str(uuid.uuid4())  # a v4 UUID, as VoicEra writes its call_id
    minted, ref = ids.from_external(ids.RunId, system="voicera", kind="call_id", value=external)
    assert str(minted.uuid) != external
    assert minted.uuid.version == 7
    run = _run(id=str(minted), external_refs=[ref.model_dump()])
    assert run.id == minted
    assert [(r.system, r.kind, r.value) for r in run.external_refs] == [
        ("voicera", "call_id", external)
    ]
    dumped = json.loads(run.model_dump_json())
    assert dumped["id"] == str(minted)
    assert dumped["external_refs"][0] == {"system": "voicera", "kind": "call_id", "value": external}
    assert external not in dumped["id"]
    odd = [" X-1 ", "run_" + external, "OTel/Trace:4bf92f35", DEVANAGARI]
    kept = _run(external_refs=[{"system": "s", "kind": "k", "value": value} for value in odd])
    assert [r.value for r in kept.external_refs] == odd  # verbatim, never parsed as an ID
    for bad_id in (external, "run_" + external, new_id("evl"), str(uuid.uuid4())):
        assert_rejected3(entity_class3("Run"), run_doc(id=bad_id), field="id")
    for field in ("project_id", "config_snapshot_id"):
        assert_rejected3(entity_class3("Run"), run_doc(**{field: external}), field=field)
    for bad_ref in ({"system": "s", "kind": "k"}, {"system": "s", "value": "v"}, "voicera:1", None):
        _reject("external_refs", external_refs=[bad_ref])
    _reject("external_refs", external_refs=[{"system": "s", "kind": "k", "value": "v", "id": "x"}])
    owners = [
        m.__name__ for m in api("registry").model_classes() if "external_refs" in m.model_fields
    ]
    assert owners == ["Run"]


def test_run_detected_capabilities_separate_from_declared() -> None:
    """Proposed name (P-23, ADR 0009 Decision 5; the Run half of
    ``test_declared_vs_detected_capabilities_separate``): what a run detects is recorded on the Run
    (``available_inputs`` as evidence and ``detected_capabilities``, the same ``InputKind``
    vocabulary as ``AgentTarget.declared_capabilities``) and never written back into the versioned
    AgentTarget: the Run references the target by ``{id, version}`` only, carries no declaration of
    its own, and the two lists on a run are independent."""
    target = entity_class("AgentTarget")
    declared = registered_class("AgentTargetRef")
    assert "detected_capabilities" not in target.model_fields
    assert "declared_capabilities" not in entity_class3("Run").model_fields
    assert set(declared.model_fields) == {"id", "version", "content_digest"}
    target_doc = build("AgentTarget", declared_capabilities=["tool_calls"])
    before = target.model_validate(target_doc).model_dump_json()
    run = _run(
        agent_target={"id": new_id("agt"), "version": 3},
        detected_capabilities=["spans", "tool_calls", "agent_transcript"],
        available_inputs=["audio", "transcript"],
    )
    assert run.agent_target.version == 3
    assert run.detected_capabilities == ["spans", "tool_calls", "agent_transcript"]
    assert run.available_inputs == ["audio", "transcript"]  # detection is not evidence
    only_available = _run(available_inputs=["audio"])
    assert only_available.detected_capabilities == []
    assert target.model_validate(target_doc).model_dump_json() == before  # the target is unchanged
    _reject(None, declared_capabilities=["tool_calls"])
    _reject(None, capabilities=["tool_calls"])
    _reject(
        "agent_target", agent_target={"id": new_id("agt"), "version": 1, "capabilities": ["spans"]}
    )
    assert {
        "audio",
        "agent_audio",
        "spans",
    } <= set(property_enum(entity_class3("Run"), "detected_capabilities"))


def test_run_channel_maps_and_warnings_are_value_objects() -> None:
    """Proposed name (ADR 0012 Decisions 5 and 7): ``channel_maps`` holds ``ChannelMap`` value
    objects (no IDs of their own; each names its artifact) and ``ingest_warnings`` holds
    ``IngestWarning`` value objects; both survive a round trip as given."""
    artifact = make3("Artifact")
    maps = [channel_map_doc(str(artifact.id))]
    warnings = [
        ingest_warning_doc(),
        ingest_warning_doc(code="time_precision_reduced", raw="1761127200123456789"),
    ]
    run = _run(
        channel_maps=maps, ingest_warnings=warnings, available_inputs=["audio", "agent_audio"]
    )
    assert str(run.channel_maps[0].artifact_id) == str(artifact.id)
    assert [w.code for w in run.ingest_warnings] == [
        "language_label_normalized",
        "time_precision_reduced",
    ]
    assert run.ingest_warnings[1].raw == "1761127200123456789"
    again = entity_class3("Run").model_validate_json(run.model_dump_json())
    assert again == run
    assert doc3("Run")["caller_origin"] == "human"


def test_run_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed
    ID (the run's own, a project, a snapshot, an agent target reference), closed-set member (mode,
    tier, input kinds) and MachineKey inside an embedded warning or channel map on ``Run`` do not
    appear in ``str``, ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``,
    through ``model_validate`` and ``model_validate_json``, and the error is still located at the
    field. ``CallerExternalId`` is checked for its marker. Proposed name (SCOPE section 5,
    P1-12)."""
    run = entity_class3("Run")
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("typed ID", run_doc(id=marked_id("run")), "id"),
        ("typed ID", run_doc(project_id=marked_id("prj")), "project_id"),
        ("typed ID", run_doc(config_snapshot_id=marked_id("snap")), "config_snapshot_id"),
        (
            "typed ID",
            run_doc(agent_target={"id": marked_id("agt"), "version": 1}),
            "agent_target",
        ),
        ("UtcDatetime", run_doc(started_at="not-a-time-" + QUOTE_MARK), "started_at"),
        ("ClosedSet", run_doc(mode=marked_set()), "mode"),
        ("ClosedSet", run_doc(caller_origin=marked_set()), "caller_origin"),
        ("ClosedSet", run_doc(observed_tier=marked_set()), "observed_tier"),
        ("ClosedSet", run_doc(available_inputs=[marked_set()]), "available_inputs"),
        ("ClosedSet", run_doc(detected_capabilities=[marked_set()]), "detected_capabilities"),
        ("MachineKey", run_doc(ingest_warnings=[ingest_warning_doc(code=marked_key())]), "code"),
        (
            "typed ID",
            run_doc(channel_maps=[channel_map_doc(marked_id("art"))]),
            "channel_maps",
        ),
        (
            "ClosedSet",
            run_doc(channel_maps=[channel_map_doc(determination=marked_set())]),
            "determination",
        ),
    ]
    for label, doc, field in cases:
        assert_input_hidden(run, doc, QUOTE_MARK, field=field, label=label)
    holder = registered_class("CallerExternalId")
    reason = {
        "value": "+91 98765-43210",
        "privacy": privacy_human(human_subject_reason=marked_set()),
    }
    assert_input_hidden(holder, reason, QUOTE_MARK, field="human_subject_reason")
