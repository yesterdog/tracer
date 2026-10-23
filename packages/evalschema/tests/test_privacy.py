# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-27: PrivacyMarker, ConsentRecord, RetentionPolicy and the human-subject derivation
(ADR 0016 Decisions 3-6, SCOPE section 2 P1-27 and section 5).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.privacy``:
``PrivacyMarker{human_subject, human_subject_reason?, consent?, retention?}``,
``ConsentRecord{status, reference?, recorded_at?}``, ``RetentionPolicy{retain_until?, policy_ref?,
delete_on_expiry}``, ``derive_human_subject(run, turns) -> bool`` and
``check_human_subject_marker(marker, run, turns) -> None`` (a ``ValueError`` when the stored
marker is looser than the derivation, or stricter without a reason). ``turns`` has no default
(O1, revision 12: a call that forgets it is a ``TypeError``, never a silent "not human").

The export-time functions (``export_human_subject``, ``ExportBundle``) belong to P1-14 and are not
tested here. Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import inspect
import itertools
import json
from collections.abc import Iterable
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    contains_key,
    embedded_models,
    error_texts,
    property_enum,
    registered_class,
    schema_of,
)
from ._support_b3 import (
    assert_rejected3,
    channel_map_doc,
    doc3,
    entity_class3,
    make3,
    privacy_human,
    privacy_synthetic,
)

REASONS = ("marked_before_role_evidence", "real_person_likeness", "manual")


def _marker(doc: dict[str, Any]) -> Any:
    return api("privacy").PrivacyMarker.model_validate(doc)


def _run(**over: Any) -> Any:
    return make3("Run", **over)


def _simulated_run(channel_roles: Iterable[str] | None = None, **over: Any) -> Any:
    maps = []
    if channel_roles is not None:
        roles = list(channel_roles)
        maps = [
            channel_map_doc(
                channels=[{"index": index, "role": role} for index, role in enumerate(roles)]
            )
        ]
    return _run(mode="simulated", caller_origin="simulated", channel_maps=maps, **over)


def _turns(*roles: str) -> list[Any]:
    transcript = doc3("Transcript")
    return [
        make3("Turn", transcript_id=transcript["id"], seq=seq, role=role)
        for seq, role in enumerate(roles)
    ]


def _derive(run: Any, *roles: str) -> bool:
    return bool(api("privacy").derive_human_subject(run, _turns(*roles)))


def _check(marker: dict[str, Any], run: Any, *roles: str) -> None:
    api("privacy").check_human_subject_marker(_marker(marker), run, _turns(*roles))


# ------------------------------------------------------------------------------- the value objects


def test_consent_enum() -> None:
    """ADR 0016 I6 and ADR 0003: ``ConsentRecord.status`` is exactly obtained, not_obtained,
    unknown (pydantic and JSON Schema ``enum``, never ``const``); ``reference`` and ``recorded_at``
    are optional; ``recorded_at`` is a UTC instant; unknown fields are rejected."""
    consent = registered_class("ConsentRecord")
    for status in ("obtained", "not_obtained", "unknown"):
        loaded = consent.model_validate({"status": status})
        assert loaded.status == status
        assert loaded.reference is None
        assert loaded.recorded_at is None
        assert consent.model_validate_json(loaded.model_dump_json()) == loaded
    for bad in ("Obtained", "granted", "", None, True, 1, ["obtained"]):
        with pytest.raises(ValidationError):
            consent.model_validate({"status": bad})
    with pytest.raises(ValidationError):
        consent.model_validate({})
    with pytest.raises(ValidationError):
        consent.model_validate({"status": "obtained", "granted": True})
    full = consent.model_validate(
        {
            "status": "obtained",
            "reference": "consent-form-2026-10-01",
            "recorded_at": "2026-10-01T14:30:00+05:30",
        }
    )
    assert json.loads(full.model_dump_json())["recorded_at"] == "2026-10-01T09:00:00.000000Z"
    for naive in ("2026-10-01T09:00:00", 1_790_000_000, "yesterday"):
        with pytest.raises(ValidationError):
            consent.model_validate({"status": "obtained", "recorded_at": naive})
    schema = schema_of(consent)
    assert sorted(schema["properties"]["status"]["enum"]) == [
        "not_obtained",
        "obtained",
        "unknown",
    ]
    assert not contains_key(schema, "const")
    assert schema["required"] == ["status"]


def test_retain_until_utc() -> None:
    """ADR 0002 I8, ADR 0016 I6: ``RetentionPolicy.retain_until`` is a ``UtcDatetime``: an offset is
    converted and the value serializes with ``Z`` and six fractional digits; naive values, dates,
    numbers and text without an offset are rejected. ``delete_on_expiry`` is a required boolean."""
    policy = registered_class("RetentionPolicy")
    loaded = policy.model_validate(
        {"retain_until": "2027-03-31T23:30:00-05:00", "delete_on_expiry": True}
    )
    assert loaded.retain_until.utcoffset().total_seconds() == 0
    assert json.loads(loaded.model_dump_json())["retain_until"] == "2027-04-01T04:30:00.000000Z"
    assert policy.model_validate({"delete_on_expiry": False}).retain_until is None
    for bad in ("2027-03-31T23:30:00", "2027-03-31", 1_800_000_000, 1.5, True, "", "soon"):
        with pytest.raises(ValidationError):
            policy.model_validate({"retain_until": bad, "delete_on_expiry": True})
    for bad_flag in (None, "true", 1, 0, "yes"):
        with pytest.raises(ValidationError):
            policy.model_validate(
                {"retain_until": "2027-03-31T00:00:00Z", "delete_on_expiry": bad_flag}
            )
    with pytest.raises(ValidationError):
        policy.model_validate({"retain_until": "2027-03-31T00:00:00Z"})
    with pytest.raises(ValidationError):
        policy.model_validate({"delete_on_expiry": True, "keep_forever": True})
    assert "retain_until" in schema_of(policy)["properties"]


def test_privacy_marker_shape_and_reason_set() -> None:
    """Proposed name (ADR 0016 Decision 3, ADR 0003 Decision 1): the marker has exactly the four
    fields, the reason set is exactly marked_before_role_evidence, real_person_likeness, manual
    (an ``enum`` in the JSON Schema), ``human_subject`` is a required strict boolean, and unknown
    fields are rejected."""
    marker = registered_class("PrivacyMarker")
    assert set(marker.model_fields) == {
        "human_subject",
        "human_subject_reason",
        "consent",
        "retention",
    }
    assert set(property_enum(marker, "human_subject_reason")) == set(REASONS)
    required = set(schema_of(marker)["required"])
    assert "human_subject" in required
    assert not {"consent", "retention", "human_subject_reason"} & required
    assert not contains_key(schema_of(marker), "const")
    for reason in REASONS:
        loaded = marker.model_validate(privacy_human(human_subject_reason=reason))
        assert loaded.human_subject_reason == reason
    for bad in ("precaution", "Manual", "", True, 1):
        with pytest.raises(ValidationError):
            marker.model_validate(privacy_human(human_subject_reason=bad))
    bad_flags: list[Any] = [None, "true", 1, 0, "yes"]
    for bad_flag in bad_flags:
        with pytest.raises(ValidationError):
            marker.model_validate({"human_subject": bad_flag})
    with pytest.raises(ValidationError):
        marker.model_validate({})
    with pytest.raises(ValidationError):
        marker.model_validate({**privacy_synthetic(), "user_id": "x"})
    assert marker.model_validate(privacy_synthetic()).human_subject is False
    again = marker.model_validate_json(marker.model_validate(privacy_human()).model_dump_json())
    assert again == marker.model_validate(privacy_human())


def test_consent_retention_required_iff_human_subject() -> None:
    """P-46 (ADR 0016 Decision 3, I6): consent and retention are required if and only if
    ``human_subject`` is true. A false marker with either is rejected (a consent record for a
    synthetic voice would read as a consent gap), and so is a true marker without either. A
    reason is meaningful only for a stricter (true) marker, so false with a reason is rejected."""
    marker = registered_class("PrivacyMarker")
    assert marker.model_validate(privacy_human()).consent.status == "obtained"
    assert marker.model_validate(privacy_synthetic()).consent is None
    human = privacy_human()
    for without in ("consent", "retention"):
        broken = {k: v for k, v in human.items() if k != without}
        with pytest.raises(ValidationError):
            marker.model_validate(broken)
    with pytest.raises(ValidationError):
        marker.model_validate({"human_subject": True})
    for extra in ("consent", "retention"):
        with pytest.raises(ValidationError):
            marker.model_validate({**privacy_synthetic(), extra: human[extra]})
    with pytest.raises(ValidationError):
        marker.model_validate(privacy_synthetic(human_subject_reason="manual"))
    # every consent status is acceptable on a human-subject marker (the benchmark rule is P1-14)
    for status in ("obtained", "not_obtained", "unknown"):
        ok = privacy_human(consent={"status": status})
        assert marker.model_validate(ok).consent.status == status


# --------------------------------------------------------------------------------- derivation


def test_human_subject_derived_from_caller_origin_human() -> None:
    """ADR 0016 Decision 5: ``caller_origin=human`` always derives true, whatever the role
    evidence: only caller and agent roles, a mixed channel, nothing at all."""
    human = _run(caller_origin="human", mode="recorded")
    assert _derive(human) is True
    assert _derive(human, "caller", "agent") is True
    with_map = _run(
        caller_origin="human",
        mode="recorded",
        channel_maps=[channel_map_doc(channels=[{"index": 0, "role": "mixed"}])],
    )
    assert _derive(with_map, "caller", "agent") is True
    assert api("privacy").derive_human_subject(human, ()) is True


def test_human_subject_turns_argument_required() -> None:
    """O1 (owner, revision 12; the ``None`` detail confirmed in revision 13; ADR 0016 Decision 5;
    api-surface 22.9 item 12): ``turns`` is a required argument of ``derive_human_subject`` and
    ``check_human_subject_marker``. Omitting it is a ``TypeError``, and so is ``turns=None``: only
    a real sequence is accepted, so a caller that forgot the turns of a transcript cannot get a
    silent "not human". An explicit empty sequence means "no turns" and, on a simulated run with
    no channel map, derives true (no role evidence). Proposed name (SCOPE section 5, P1-27)."""
    privacy = api("privacy")
    run = _simulated_run(channel_roles=["caller", "agent"])
    marker = _marker(privacy_synthetic())
    with pytest.raises(TypeError):
        privacy.derive_human_subject(run)
    with pytest.raises(TypeError):
        privacy.check_human_subject_marker(marker, run)
    with pytest.raises(TypeError):
        privacy.derive_human_subject(run, None)
    with pytest.raises(TypeError):
        privacy.derive_human_subject(run, turns=None)
    with pytest.raises(TypeError):
        privacy.check_human_subject_marker(marker, run, None)
    with pytest.raises(TypeError):
        privacy.check_human_subject_marker(marker, run, turns=None)
    for function in (privacy.derive_human_subject, privacy.check_human_subject_marker):
        parameter = inspect.signature(function).parameters["turns"]
        assert parameter.default is inspect.Parameter.empty, function.__name__
    # the turns passed by keyword or by position are the same evidence
    assert privacy.derive_human_subject(run, turns=_turns("caller", "agent")) is False
    assert privacy.derive_human_subject(run, turns=_turns("caller", "unknown")) is True
    # an explicit empty sequence is "no turns"; the channel evidence of the run still counts
    assert privacy.derive_human_subject(run, ()) is False
    bare = _simulated_run()
    assert bare.channel_maps == []
    assert privacy.derive_human_subject(bare, ()) is True
    assert privacy.derive_human_subject(bare, []) is True


def test_human_subject_derived_from_unknown_role() -> None:
    """Revision 5 (replaces the removed hand-over role): an ``unknown`` role anywhere derives
    true: in any Turn of any transcript, or in any ChannelMap channel, even on a simulated run
    whose other evidence is caller and agent only."""
    run = _simulated_run()
    assert _derive(run, "caller", "agent", "unknown") is True
    assert _derive(run, "unknown") is True
    unknown_channel = _simulated_run(channel_roles=["caller", "unknown"])
    assert _derive(unknown_channel) is True
    assert _derive(unknown_channel, "caller", "agent") is True
    assert _derive(_simulated_run(channel_roles=["mixed", "unknown"])) is True


def test_human_subject_defaults_true_when_unknown() -> None:
    """ADR 0016 Decision 5: no role evidence at all (no turn of any transcript, no channel map)
    derives true, because a real person cannot be ruled out. Missing evidence never reads as a
    privacy claim of 'not human'."""
    run = _simulated_run()
    assert run.channel_maps == []
    assert _derive(run) is True
    assert api("privacy").derive_human_subject(run, []) is True
    assert api("privacy").derive_human_subject(run, ()) is True


def _expected(origin: str, turn_roles: set[str], channel_roles: set[str]) -> bool:
    """The ADR 0016 Decision 5 rule, written out independently of the implementation."""
    if origin == "human":
        return True
    evidence = turn_roles | channel_roles
    if not evidence:
        return True
    return "unknown" in evidence


def test_human_subject_false_only_when_simulated_and_roles_known() -> None:
    """ADR 0016 I4: the derivation is false only if ``caller_origin=simulated``, role evidence
    exists, and every role in it is caller, agent or a mixed channel. Exhaustive over the origin,
    every subset of turn roles and every subset of channel roles (a channel map holds at most one
    agent channel, which a role set guarantees)."""
    turn_subsets = [
        set(c) for n in range(4) for c in itertools.combinations(("caller", "agent", "unknown"), n)
    ]
    channel_subsets = [
        set(c)
        for n in range(5)
        for c in itertools.combinations(("caller", "agent", "unknown", "mixed"), n)
    ]
    seen_false = 0
    for origin in ("simulated", "human"):
        for turn_roles in turn_subsets:
            for channel_roles in channel_subsets:
                run = _run(
                    mode="simulated" if origin == "simulated" else "recorded",
                    caller_origin=origin,
                    channel_maps=(
                        [
                            channel_map_doc(
                                channels=[
                                    {"index": i, "role": r}
                                    for i, r in enumerate(sorted(channel_roles))
                                ]
                            )
                        ]
                        if channel_roles
                        else []
                    ),
                )
                got = _derive(run, *sorted(turn_roles))
                expected = _expected(origin, turn_roles, channel_roles)
                assert got is expected, (origin, turn_roles, channel_roles)
                seen_false += not got
    # simulated, evidence present, no unknown: turn roles from {caller, agent} (4 subsets) and
    # channel roles from {caller, agent, mixed} (8 subsets), minus the one without evidence
    assert seen_false == 4 * 8 - 1
    # the only false cases: simulated, evidence present, no unknown role
    assert _derive(_simulated_run(), "caller") is False
    assert _derive(_simulated_run(), "agent") is False
    assert _derive(_simulated_run(), "caller", "agent") is False
    assert _derive(_simulated_run(channel_roles=["caller", "agent"])) is False


def test_mixed_channel_counts_as_caller_and_agent_evidence() -> None:
    """P-31 (ADR 0012, ADR 0016 Decision 5): a ``mixed`` channel is evidence of exactly the
    caller and the agent. On a simulated run it rules a human out (false), on a human-origin run
    the origin already derives true, and a mixed channel beside an unknown one is still true."""
    mono = [channel_map_doc(channels=[{"index": 0, "role": "mixed"}])]
    simulated = _run(mode="simulated", caller_origin="simulated", channel_maps=mono)
    assert _derive(simulated) is False
    assert _derive(simulated, "caller", "agent") is False
    human = _run(mode="recorded", caller_origin="human", channel_maps=mono)
    assert _derive(human) is True
    with_unknown = _simulated_run(channel_roles=["mixed", "unknown"])
    assert _derive(with_unknown) is True


def test_simulated_run_with_unknown_role_turn_is_human_subject() -> None:
    """Revision 5 rename of the former transfer test; A-20, A-31: a simulated run whose transcript
    has a turn with role ``unknown`` is human-subject data, and a stored ``human_subject=false``
    marker on it is looser than the derivation and is rejected."""
    run = _simulated_run()
    assert _derive(run, "caller", "agent", "caller", "unknown") is True
    with pytest.raises(ValueError, match=r"looser|derivation|unknown"):
        _check(privacy_synthetic(), run, "caller", "agent", "unknown")
    _check(privacy_human(), run, "caller", "agent", "unknown")


# ----------------------------------------------------------------- stored marker versus derivation


def test_human_subject_looser_marker_rejected() -> None:
    """P-47, A-31: a stored ``human_subject=false`` where the derivation is true is an error, for
    each way the derivation can be true: a human caller, an ``unknown`` role, no role evidence.
    Where the derivation is false a stored false is accepted."""
    for run, roles in (
        (_run(caller_origin="human", mode="recorded"), ("caller", "agent")),
        (_simulated_run(), ("caller", "unknown")),
        (_simulated_run(), ()),
        (_simulated_run(channel_roles=["unknown"]), ()),
    ):
        assert _derive(run, *roles) is True
        with pytest.raises(ValueError, match=r"looser|derivation"):
            _check(privacy_synthetic(), run, *roles)
    _check(privacy_synthetic(), _simulated_run(), "caller", "agent")
    _check(privacy_synthetic(), _simulated_run(channel_roles=["mixed"]))


def test_human_subject_stricter_marker_requires_reason() -> None:
    """P-47 (reasons accepted in revision 6): a stored ``true`` where the derivation is false is
    valid only with a ``human_subject_reason``; without one it is rejected. Where the derivation
    is true no reason is needed."""
    run = _simulated_run()
    assert _derive(run, "caller", "agent") is False
    with pytest.raises(ValueError, match="reason"):
        _check(privacy_human(), run, "caller", "agent")
    for reason in REASONS:
        _check(privacy_human(human_subject_reason=reason), run, "caller", "agent")
    assert _derive(run, "unknown") is True
    _check(privacy_human(), run, "unknown")


def test_human_subject_manual_true_always_allowed() -> None:
    """P-47: a person may always set ``true`` manually, whatever the derivation says (true or
    false), and likewise for the real-person-likeness reason."""
    false_run = _simulated_run()
    true_run = _run(caller_origin="human", mode="recorded")
    for reason in ("manual", "real_person_likeness"):
        _check(privacy_human(human_subject_reason=reason), false_run, "caller", "agent")
        _check(privacy_human(human_subject_reason=reason), true_run, "caller", "agent")
        _check(privacy_human(human_subject_reason=reason), false_run)


def test_marker_before_role_evidence_stays_valid_with_reason() -> None:
    """P-47: a marker written while the run had no role evidence is ``true`` with reason
    ``marked_before_role_evidence``. When evidence later shows only caller and agent on a simulated
    run (derivation false) the stored record stays valid; the same marker without the reason does
    not. A too-loose record is never repaired in place (a new record is written instead)."""
    run = _simulated_run()
    precaution = privacy_human(human_subject_reason="marked_before_role_evidence")
    _check(precaution, run)  # no role evidence yet: derivation true
    _check(precaution, run, "caller", "agent")  # evidence arrived: derivation false, still valid
    with pytest.raises(ValueError, match="reason"):
        _check(privacy_human(), run, "caller", "agent")
    # the checker never edits the marker it is given
    marker = _marker(precaution)
    before = marker.model_dump_json()
    api("privacy").check_human_subject_marker(marker, run, _turns("caller", "agent"))
    assert marker.model_dump_json() == before


# ------------------------------------------------------------------------ payload entities


def test_privacy_marker_required_for_human_caller() -> None:
    """ADR 0016 I3: a PrivacyMarker is required on every Artifact, also on the artifacts of a
    human caller (the recordings that matter most). A missing marker is rejected; the marker of a
    human recording carries consent and retention."""
    artifact = entity_class3("Artifact")
    doc = doc3("Artifact")
    assert artifact.model_validate(doc).privacy.human_subject is True
    without = {k: v for k, v in doc.items() if k != "privacy"}
    assert_rejected3(artifact, without, field="privacy")
    assert_rejected3(artifact, {**doc, "privacy": None}, field="privacy")
    assert_rejected3(artifact, {**doc, "privacy": {"human_subject": True}}, field="privacy")


def test_privacy_marker_required_on_payload_entities() -> None:
    """ADR 0016 Decision 4 and I3: ``privacy`` is required on every Artifact and every Transcript,
    and on a DatasetItem that holds a recording or a transcript. ToolCallEvent, Turn, Exchange,
    StageSpan and MetricResult carry no marker of their own: a tool call takes the value derived
    for its run."""
    for name in ("Artifact", "Transcript"):
        model = entity_class3(name)
        doc = doc3(name)
        assert model.model_validate(doc).privacy.human_subject is True
        assert_rejected3(model, {k: v for k, v in doc.items() if k != "privacy"}, field="privacy")
        assert "privacy" in schema_of(model)["required"], name
    item = entity_class3("DatasetItem")
    base = doc3("DatasetItem")
    assert_rejected3(item, {k: v for k, v in base.items() if k != "privacy"}, field="privacy")
    for name in ("ToolCallEvent", "Turn", "Exchange", "StageSpan", "MetricResult"):
        model = entity_class3(name)
        assert "privacy" not in model.model_fields, name
        assert "privacy" not in schema_of(model)["properties"], name


# ------------------------------------------------------------------------- caller identifier


def test_caller_external_id_requires_privacy_marker() -> None:
    """P-43 (ADR 0016 Decision 6, I7): ``Run.caller_external_id`` is the only place a transport
    ``user_id`` (possibly a phone number) may live, as ``CallerExternalId{value, privacy}``: the
    value is verbatim text, the privacy marker is required, and nothing else is accepted (a
    ``user_id`` field is rejected). A Run hides its input in validation errors, so the number is
    not echoed when the record is rejected."""
    holder = registered_class("CallerExternalId")
    number = "+91 98765-43210"
    loaded = holder.model_validate({"value": number, "privacy": privacy_human()})
    assert loaded.value == number
    assert holder.model_validate_json(loaded.model_dump_json()) == loaded
    assert set(holder.model_fields) == {"value", "privacy"}
    for bad in (
        {"value": number},
        {"privacy": privacy_human()},
        {"value": number, "privacy": None},
        {"value": "", "privacy": privacy_human()},
        {"value": number, "privacy": privacy_human(), "user_id": number},
    ):
        with pytest.raises(ValidationError):
            holder.model_validate(bad)
    run = _run(caller_external_id={"value": number, "privacy": privacy_human()})
    assert run.caller_external_id.value == number
    assert _run().caller_external_id is None
    for text in error_texts(_holder_error(holder, {"value": number})):
        assert number not in text
    assert holder.model_config.get("hide_input_in_errors") is True
    broken = doc3("Run", caller_external_id={"value": number})
    error = assert_rejected3(entity_class3("Run"), broken, field="privacy")
    for text in error_texts(error):
        assert number not in text
    assert entity_class3("Run").model_config.get("hide_input_in_errors") is True
    with pytest.raises(ValidationError):
        entity_class3("Run").model_validate({**doc3("Run"), "user_id": number})


def test_user_id_not_in_snapshot_or_export() -> None:
    """ADR 0006 Decision 8, ADR 0016 I7, A-27: a transport ``user_id`` never enters a snapshot.
    No schema property named ``user_id`` or ``caller_external_id`` exists anywhere in the
    ConfigSnapshot schema (embedded entities included), the snapshot rejects such a member, and
    the Run is the one model that holds ``caller_external_id``. (The ExportBundle half of this
    test needs P1-14.)"""
    snapshot = entity_class3("ConfigSnapshot")
    names = {
        name
        for mode in ("validation", "serialization")
        for node in _walk(schema_of(snapshot, mode))
        for name in node.get("properties", {})
    }
    assert not names & {"user_id", "caller_external_id", "caller_id", "phone"}
    assert registered_class("CallerExternalId") not in embedded_models(snapshot)
    assert registered_class("PrivacyMarker") not in embedded_models(snapshot)
    owners = [
        m.__name__
        for m in api("registry").model_classes()
        if "caller_external_id" in m.model_fields
    ]
    assert owners == ["Run"]
    doc = doc3("ConfigSnapshot")
    for extra in ({"user_id": "u-1"}, {"caller_external_id": {"value": "x"}}):
        with pytest.raises(ValidationError):
            snapshot.model_validate({**doc, **extra})


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _holder_error(holder: Any, doc: dict[str, Any]) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        holder.model_validate(doc)
    return caught.value
