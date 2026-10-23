# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 7 (P1-14), part 2: ``ExportBundle`` and the export rules; the export half of P1-27.

Contract: docs/phase1/api-surface-bundle.md. ADR 0016 (Decisions 5, 7, 7a and 9), ADR 0006
(Decisions 5 to 7), ADR 0007 (Decisions 6c and 6d), ADR 0015 (Decision 7), ADR 0001 and ADR 0009.

``ExportBundle{purpose}`` embeds the ``RunBundle`` of each exported run, the datasets and their
items, the sealed payloads, the payload redactions and the provenance redactions. ``internal``
exports are the producer's own and keep everything; ``shared_export`` and ``benchmark`` exports
apply the human-subject rule (P-44, P-57), the drop-or-key digest rule (P-17), the URI rules (O5,
O8) and the ``source_run_id`` policy (O9). Rules fire as ``[tag]`` ValueErrors (contract section 3).

Fixtures: ``_support_b7.shared_ready()`` is an accepted export of a human-caller run whose payloads
are all covered by payload redactions; a test changes one thing and expects one tag.
"""

from __future__ import annotations

import base64
import copy
import inspect
import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import new_id, property_names, registered_class, secret_like
from ._support_b3 import artifact_doc, privacy_human, privacy_synthetic
from ._support_b5 import KEK, dek_of
from ._support_b7 import (
    DIGEST_KEY,
    KEYED,
    NON_INTERNAL,
    PROFILE_KEY,
    assert_bundle_rejected,
    assert_error_free_of,
    bundle_fn,
    dataset_doc,
    dataset_item_doc,
    drop_nulls,
    export_class,
    export_doc,
    keyed_profile_digest,
    load_export,
    load_run,
    protect_by_redaction,
    redaction_entry,
    run_bundle_doc,
    run_class,
    sealed_doc,
    shared_ready,
    withheld_doc,
    world,
)

PURPOSES = ("internal", "shared_export", "benchmark")


def _reject(doc: dict[str, Any], *tags: str) -> BaseException:
    return assert_bundle_rejected(export_class(), doc, *tags)


def _entry_covering(doc: dict[str, Any], subject_id: str, covers: list[str]) -> None:
    doc["payload_redactions"] = [
        e for e in doc["payload_redactions"] if e["subject_id"] != subject_id
    ]
    if covers:
        doc["payload_redactions"].append(redaction_entry(subject_id, covers))


def _precaution(status: str = "unknown", reason: str = "marked_before_role_evidence") -> Any:
    """A stored human-subject marker written before role evidence existed (P-47, P-57)."""
    return privacy_human(consent={"status": status}, human_subject_reason=reason)


def _with_consent(status: str) -> dict[str, Any]:
    return privacy_human(consent={"status": status})


# ======================================================================================= internal


@pytest.mark.parametrize("tier", ["T0", "T1", "T2"])
def test_internal_bundle_human_allowed(tier: str) -> None:
    """ADR 0016 I11: an internal export may hold human-subject data in the clear: a human caller's
    recording, text and tool payloads with no seal and no redaction, whatever the consent status.
    The rules of shared exports do not apply to it: a ``file:`` URI, a loopback host, a plain
    digest of the test profile and a source run that is not in the export are all kept."""
    rb = run_bundle_doc(tier=tier, marker=_with_consent("not_obtained"))
    assert rb["artifacts"][0]["privacy"]["human_subject"] is True
    load_export(export_doc("internal", [rb]))
    mixed = run_bundle_doc(tier=tier, with_profile=True, profile_digest="plain")
    mixed["artifacts"][0]["uri"] = "file:///var/recordings/call-0001.wav"
    second = run_bundle_doc(tier=tier)
    second["artifacts"][0]["uri"] = "http://localhost:9000/bucket/call-0002.wav"
    item = dataset_item_doc(source_run_id=new_id("run"))
    export = load_export(export_doc("internal", [mixed, second], dataset_items=[item]))
    assert str(export.dataset_items[0].source_run_id) == item["source_run_id"]
    assert export.purpose == "internal"
    assert export.sealed_payloads == []


def test_internal_export_keeps_a_plain_profile_digest() -> None:
    """ADR 0006 Decision 6 (internal: ``TestProfileRef.content_digest`` is required and plain) and
    ``snapshot.check_internal_snapshot``: an internal export accepts a plain digest on the profile
    reference and rejects a keyed digest or none, ``[profile_digest]``, because the plain digest is
    what ties the run to its profile in the producer's own data. Test profiles in the clear are
    allowed in an internal export."""
    plain = run_bundle_doc(tier="T0", with_profile=True, profile_digest="plain")
    load_export(export_doc("internal", [plain]))
    keyed = run_bundle_doc(tier="T0", with_profile=True, profile_digest="keyed")
    _reject(export_doc("internal", [keyed]), "profile_digest")
    absent = run_bundle_doc(tier="T0", with_profile=True)
    _reject(export_doc("internal", [absent]), "profile_digest")
    w = world(tier="T0", with_profile=True, profile_digest="plain")
    doc = w.copy()
    doc["test_profiles"] = [w.parts["profile"].model_dump(mode="json", by_alias=True)]
    assert len(load_export(export_doc("internal", [doc])).run_bundles[0].test_profiles) == 1


def test_export_bundle_accepts_nothing_to_export() -> None:
    """An empty call and an empty export: a run bundle with a run and a snapshot only (no turn, no
    artifact, no result) and an export without any run bundle are valid at every purpose, because
    there is no payload to protect."""
    empty = run_bundle_doc(tier="T0")
    for name in (
        "artifacts",
        "transcripts",
        "turns",
        "exchanges",
        "tool_calls",
        "spans",
        "results",
        "usage_records",
        "cost_records",
        "cost_summaries",
    ):
        empty[name] = []
    empty["verdict"] = None
    empty["run"]["channel_maps"] = []
    empty["run"]["available_inputs"] = []
    for purpose in PURPOSES:
        export = load_export(export_doc(purpose, [empty]))
        assert export.run_bundles[0].turns == []
        assert load_export(export_doc(purpose, [])).run_bundles == []


# ==================================================================== human-subject payloads (P-44)


def _requires_protection(purpose: str) -> None:
    """Shared by the two SCOPE-named tests: every kind of human-subject payload of a human caller's
    run needs a seal or a redaction."""
    good = shared_ready(purpose)
    load_export(good)
    rb = good["run_bundles"][0]
    artifact_id = rb["artifacts"][0]["id"]
    transcript_id = rb["transcripts"][0]["id"]
    call_ids = [c["id"] for c in rb["tool_calls"]]
    # nothing at all
    bare = copy.deepcopy(good)
    bare["payload_redactions"] = []
    error = _reject(bare, "unprotected")
    assert "[unprotected]" in str(error)
    # one kind missing at a time
    cases: dict[str, tuple[str, list[str]]] = {
        "audio": (artifact_id, []),
        "text": (transcript_id, []),
        "tool_arguments": (call_ids[0], ["tool_result"]),
        "tool_result": (call_ids[0], ["tool_arguments"]),
        "second call": (call_ids[1], []),
    }
    for label, (subject, covers) in cases.items():
        broken = copy.deepcopy(good)
        _entry_covering(broken, subject, covers)
        text = str(_reject(broken, "unprotected"))
        assert subject in text or label == "tool_result", label
        # the message names records and rules, never the text of a payload
        assert "turn 0 text" not in text
        assert rb["tool_calls"][0]["arguments_raw"] not in text


def test_shared_export_human_requires_seal_or_redaction() -> None:
    """SCOPE P1-14 (binding), ADR 0016 Decision 7, I8 (any human-subject payload): in a
    ``shared_export`` the recording, the text and the arguments and the result of each tool call
    of a human caller's run are each sealed or redacted. Missing any one is ``[unprotected]``; the
    message names the record. An ``internal`` export of the same run is fine (the first test)."""
    _requires_protection("shared_export")


def test_benchmark_purpose_human_requires_seal_or_redaction() -> None:
    """SCOPE P1-14 (binding), ADR 0016 Decision 7, I8: the same rule for a ``benchmark`` export."""
    _requires_protection("benchmark")


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_shared_export_simulated_run_with_unknown_role_requires_seal_or_redaction(
    purpose: str,
) -> None:
    """ADR 0016 I9 and A-20: a simulated run with an ``unknown``-role turn is human-subject data
    (the speaker may be a real person), so its payloads need a seal or a redaction in a shared or
    benchmark export, with the human-subject marker the derivation demands. The same run without
    the unknown turn needs nothing."""
    rb = run_bundle_doc(tier="T0", origin="simulated", unknown_turn=True, marker=privacy_human())
    _reject(export_doc(purpose, [rb]), "unprotected")
    protected = export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
    load_export(protected)
    clean = run_bundle_doc(tier="T0", origin="simulated")
    load_export(export_doc(purpose, [clean]))


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_redaction_covers_all_payload_kinds(purpose: str) -> None:
    """ADR 0016 I10, S-N87 and S-N93 (the bundle half of the test whose structural half is in
    ``test_sealed_payload.py``): a redaction covers each payload kind separately, and the map from
    a sealed payload type to the ``covers`` member is ``bundle.PAYLOAD_KIND_COVERS``
    (audio -> audio, transcript_text -> text, tool_arguments -> tool_arguments, tool_result ->
    tool_result; a profile has no member, it is only ever sealed). A redaction that covers every
    kind but the one a payload needs does not protect it. Two entries for one record add up."""
    bundle = api("bundle")
    privacy = api("privacy")
    assert dict(bundle.PAYLOAD_KIND_COVERS) == {
        "audio": "audio",
        "transcript_text": "text",
        "tool_arguments": "tool_arguments",
        "tool_result": "tool_result",
    }
    assert set(bundle.PAYLOAD_KIND_COVERS.values()) == set(privacy.REDACTION_COVERS)
    assert "test_profile" not in bundle.PAYLOAD_KIND_COVERS
    rb = run_bundle_doc(tier="T0")
    everything = ["audio", "text", "tool_arguments", "tool_result"]
    art, tsc = rb["artifacts"][0]["id"], rb["transcripts"][0]["id"]
    calls = [c["id"] for c in rb["tool_calls"]]
    full = export_doc(
        purpose,
        [rb],
        payload_redactions=[redaction_entry(i, everything) for i in (art, tsc, *calls)],
    )
    load_export(full)  # a redaction that covers more than the payload needs is fine
    for subject, missing in (
        (art, "audio"),
        (tsc, "text"),
        (calls[0], "tool_arguments"),
        (calls[1], "tool_result"),
    ):
        broken = copy.deepcopy(full)
        _entry_covering(broken, subject, [k for k in everything if k != missing])
        _reject(broken, "unprotected")
    split = copy.deepcopy(full)
    _entry_covering(split, calls[0], [])
    split["payload_redactions"] += [
        redaction_entry(calls[0], ["tool_arguments"]),
        redaction_entry(calls[0], ["tool_result"]),
    ]
    load_export(split)
    # a call without a result needs no tool_result cover; without arguments no tool_arguments cover
    lean = copy.deepcopy(rb)
    for call in lean["tool_calls"]:
        call.pop("result_raw")
        call.pop("result_complete")
    lean_export = export_doc(
        purpose,
        [lean],
        payload_redactions=[
            redaction_entry(art, ["audio"]),
            redaction_entry(tsc, ["text"]),
            *[redaction_entry(c["id"], ["tool_arguments"]) for c in lean["tool_calls"]],
        ],
    )
    load_export(lean_export)
    # a redaction of a record the export does not hold is an error of the export
    stray = copy.deepcopy(full)
    stray["payload_redactions"].append(redaction_entry(new_id("art"), ["audio"]))
    error = _reject(stray, "redaction")
    assert stray["payload_redactions"][-1]["subject_id"] in str(error)


def test_payload_redaction_shape() -> None:
    """S-N89 (a ``RedactionRecord`` has no id; an export attaches it by position):
    ``PayloadRedaction``
    pairs one ``RedactionRecord`` with the typed ID of an artifact, a transcript or a tool call, and
    nothing else. The ID is not a run, a turn or a result."""
    model = registered_class("PayloadRedaction")
    record = registered_class("RedactionRecord")
    assert set(model.model_fields) == {"subject_id", "redaction"}
    good = redaction_entry(new_id("art"), ["audio"])
    parsed = model.model_validate(good)
    assert isinstance(parsed.redaction, record)
    for prefix in ("art", "tsc", "tcl"):
        model.model_validate(redaction_entry(new_id(prefix), ["audio"]))
    for prefix in ("run", "turn", "res", "spn", "exc", "use", "dti", "tpf"):
        with pytest.raises(ValidationError):
            model.model_validate(redaction_entry(new_id(prefix), ["audio"]))
    for bad in (
        {**good, "extra": 1},
        {"subject_id": good["subject_id"]},
        {"redaction": good["redaction"]},
        {**good, "redaction": {**good["redaction"], "covers": []}},
        {**good, "redaction": {**good["redaction"], "covers": ["profile"]}},
    ):
        with pytest.raises(ValidationError):
            model.model_validate(bad)


# ============================================================ the P-57 precautionary markers


def _simulated_precaution(
    status: str, reason: str = "marked_before_role_evidence"
) -> dict[str, Any]:
    return run_bundle_doc(
        tier="T0", origin="simulated", marker=_precaution(status=status, reason=reason)
    )


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_precautionary_marker_uses_derivation_at_export(purpose: str) -> None:
    """P-57, ADR 0016 Decision 7a and I15: a simulated run whose artifact and transcript carry a
    ``marked_before_role_evidence`` marker, in a bundle that holds a transcript of caller and agent
    turns only (and a channel map of the same roles), is non-human at export time: a shared and a
    benchmark export accept it with no seal and no redaction."""
    rb = _simulated_precaution("unknown")
    bundle = load_run(rb)
    assert {t.role for t in bundle.turns} == {"caller", "agent"}
    assert bundle.artifacts[0].privacy.human_subject is True
    load_export(export_doc(purpose, [rb]))


@pytest.mark.parametrize("reason", ["manual", "real_person_likeness"])
@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_manual_marker_always_honoured_at_export(purpose: str, reason: str) -> None:
    """P-57: a ``manual`` or ``real_person_likeness`` marker is human-subject at export whatever the
    evidence: the same simulated bundle then needs a seal or a redaction (``[unprotected]``), and a
    benchmark also needs consent obtained (``[consent]`` for ``unknown``)."""
    rb = _simulated_precaution("unknown", reason)
    _reject(export_doc(purpose, [rb]), "unprotected")
    protected = export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
    if purpose == "benchmark":
        _reject(protected, "consent")
        obtained = _simulated_precaution("obtained", reason)
        load_export(
            export_doc(purpose, [obtained], payload_redactions=protect_by_redaction(obtained))
        )
    else:
        load_export(protected)
    # a stored true marker without any reason is human-subject too
    plain = run_bundle_doc(tier="T0", origin="simulated", unknown_turn=True, marker=privacy_human())
    _reject(export_doc(purpose, [plain]), "unprotected")


def test_simulated_precautionary_payload_with_unknown_consent_enters_benchmark() -> None:
    """P-57, ADR 0016 Decision 7a (consent for such payloads is not applicable): the stored
    consent ``unknown`` of a simulator's precautionary marker is not consulted when the export-time
    value is false, so the payload is not blocked from a benchmark. The same bundle with a
    ``manual`` reason is blocked."""
    rb = _simulated_precaution("unknown")
    assert rb["artifacts"][0]["privacy"]["consent"]["status"] == "unknown"
    load_export(export_doc("benchmark", [rb]))
    blocked = _simulated_precaution("unknown", "manual")
    _reject(
        export_doc("benchmark", [blocked], payload_redactions=protect_by_redaction(blocked)),
        "consent",
    )


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_precautionary_marker_human_when_bundle_lacks_role_evidence(purpose: str) -> None:
    """P-57 ("role evidence then available" means the evidence in the bundle being validated): a
    precautionary marker on a simulated run whose bundle holds no turn and no channel map derives
    ``true``, so the payload needs a seal or a redaction, and a benchmark rejects ``unknown``
    consent."""
    rb = _simulated_precaution("unknown")
    rb["turns"] = []
    rb["results"] = []
    rb["verdict"] = None
    rb["run"]["channel_maps"] = []
    rb["run"]["available_inputs"] = ["audio", "transcript", "tool_calls", "usage"]
    load_run(rb)
    _reject(export_doc(purpose, [rb]), "unprotected")
    protected = export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
    if purpose == "benchmark":
        _reject(protected, "consent")
    else:
        load_export(protected)


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_precautionary_marker_on_human_caller_run_stays_human(purpose: str) -> None:
    """P-57: ``caller_origin=human`` always derives ``true``, so a precautionary marker on a human
    caller's run stays human-subject: sealed or redacted payloads are required, and P-44 still
    blocks ``unknown`` consent in a benchmark."""
    rb = run_bundle_doc(tier="T0", origin="human", marker=_precaution("unknown"))
    _reject(export_doc(purpose, [rb]), "unprotected")
    protected = export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
    if purpose == "benchmark":
        _reject(protected, "consent")
    else:
        load_export(protected)


def test_export_rederivation_leaves_stored_marker_unchanged() -> None:
    """ADR 0016 Decision 7a ("the stored record is unchanged"; ADR 0009 write-once): validating an
    export re-derives the human-subject value in memory only. The embedded artifact and transcript
    keep their marker, reason and consent record, byte for byte in the dump, and the dump of the
    whole export loads back to the same export."""
    rb = _simulated_precaution("unknown")
    doc = export_doc("benchmark", [rb])
    export = load_export(doc)
    dumped = export.model_dump(mode="json", by_alias=True)
    embedded = dumped["run_bundles"][0]
    assert drop_nulls(embedded["artifacts"][0]["privacy"]) == rb["artifacts"][0]["privacy"]
    assert drop_nulls(embedded["transcripts"][0]["privacy"]) == rb["transcripts"][0]["privacy"]
    stored = embedded["artifacts"][0]["privacy"]
    assert stored["human_subject"] is True
    assert stored["human_subject_reason"] == "marked_before_role_evidence"
    assert stored["consent"]["status"] == "unknown"
    inner = export.run_bundles[0]
    artifact = inner.artifacts[0]
    before = artifact.privacy.model_dump(mode="json")
    api("privacy").export_human_subject(artifact.privacy, inner.run, inner.turns)
    assert artifact.privacy.model_dump(mode="json") == before
    assert load_export(dumped) == export
    with pytest.raises(ValidationError):
        artifact.privacy.human_subject = False  # frozen: the stored record is never edited


def _run_and_turns(
    origin: str, unknown: bool = False, with_map: bool = True
) -> tuple[Any, list[Any]]:
    doc = run_bundle_doc(tier="T0", origin=origin, unknown_turn=unknown, marker=privacy_human())
    if not with_map:
        doc["run"]["channel_maps"] = []
        doc["run"]["available_inputs"] = ["audio", "transcript", "tool_calls", "usage"]
    run = registered_class("Run").model_validate(doc["run"])
    turns = [registered_class("Turn").model_validate(t) for t in doc["turns"]]
    return run, turns


def test_export_human_subject_turns_argument_required() -> None:
    """O1 and ADR 0016 Decisions 5 and 7a (the export half of ``test_human_subject_turns_argument_
    required``): ``privacy.export_human_subject(marker, run, turns)`` has no default for ``turns``;
    omitting it or passing ``None`` is a ``TypeError``, and so is anything that is not a sequence
    of turns (a string, a generator's absence). An explicit empty sequence means no turn
    evidence."""
    privacy = api("privacy")
    marker = registered_class("PrivacyMarker").model_validate(privacy_synthetic())
    run, turns = _run_and_turns("simulated")
    with pytest.raises(TypeError):
        privacy.export_human_subject(marker, run)
    with pytest.raises(TypeError):
        privacy.export_human_subject(marker, run, None)
    with pytest.raises(TypeError):
        privacy.export_human_subject(marker, run, turns=None)
    with pytest.raises(TypeError):
        privacy.export_human_subject(marker, run, "turn")
    with pytest.raises(TypeError):
        privacy.export_human_subject(marker, run, b"")
    assert privacy.export_human_subject(marker, run, ()) is False
    assert privacy.export_human_subject(marker, run, []) is False
    assert privacy.export_human_subject(marker, run, turns) is False
    parameters = list(inspect.signature(privacy.export_human_subject).parameters.values())
    assert [p.name for p in parameters] == ["marker", "run", "turns"]
    assert all(p.default is p.empty for p in parameters)


def test_export_human_subject_truth_table() -> None:
    """ADR 0016 Decision 7a: stored false -> false (a stored false is valid only where the
    derivation is false); stored true with ``marked_before_role_evidence`` ->
    ``derive_human_subject`` over the evidence supplied; stored true with ``manual`` or
    ``real_person_likeness`` -> true always;
    stored true without a reason -> true. Evidence outside the arguments does not count."""
    privacy = api("privacy")
    marker_cls = registered_class("PrivacyMarker")

    def value(marker: Mapping[str, Any], run: Any, turns: list[Any]) -> bool:
        result = privacy.export_human_subject(marker_cls.model_validate(dict(marker)), run, turns)
        assert type(result) is bool
        return result

    sim_run, sim_turns = _run_and_turns("simulated")
    unk_run, unk_turns = _run_and_turns("simulated", unknown=True)
    hum_run, hum_turns = _run_and_turns("human")
    nomap_run, nomap_turns = _run_and_turns("simulated", with_map=False)
    false_marker = privacy_synthetic()
    pre = privacy_human(human_subject_reason="marked_before_role_evidence")
    manual = privacy_human(human_subject_reason="manual")
    likeness = privacy_human(human_subject_reason="real_person_likeness")
    plain_true = privacy_human()
    assert value(false_marker, sim_run, sim_turns) is False
    assert value(false_marker, hum_run, hum_turns) is False  # a stored false is taken at its word
    assert value(pre, sim_run, sim_turns) is False
    assert value(pre, sim_run, []) is False  # the run's own channel map shows caller and agent
    assert value(pre, nomap_run, []) is True
    assert value(pre, nomap_run, nomap_turns) is False
    assert value(pre, unk_run, unk_turns) is True
    assert value(pre, hum_run, hum_turns) is True
    for marker in (manual, likeness, plain_true):
        for run, turns in (
            (sim_run, sim_turns),
            (unk_run, unk_turns),
            (hum_run, hum_turns),
            (nomap_run, []),
        ):
            assert value(marker, run, turns) is True


# ======================================================================== digests of test profiles


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_shared_export_drops_or_keys_profile_digest(purpose: str) -> None:
    """ADR 0006 Decision 6 and ADR 0015: in a shared or benchmark export every
    ``TestProfileRef.content_digest`` is absent or keyed (``hmac-sha256-jcs/v1``); a plain
    ``sha256-jcs/v1`` digest is ``[profile_digest]`` (A-30), in any of the run bundles, and the
    message names the reference, not the digest. A test profile in the clear is ``[profile]``: a
    portable export carries a profile only as a ``SealedPayload`` (ADR 0006 Decision 7)."""
    for digest in ("keyed", None):
        rb = run_bundle_doc(tier="T0", with_profile=True, profile_digest=digest)
        load_export(export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb)))
    plain = run_bundle_doc(tier="T0", with_profile=True, profile_digest="plain")
    plain_value = plain["snapshot"]["evaluator"]["test_profile"]["content_digest"]["value"]
    doc = export_doc(purpose, [plain], payload_redactions=protect_by_redaction(plain))
    error = _reject(doc, "profile_digest")
    assert plain_value not in str(error)
    assert plain["snapshot"]["evaluator"]["test_profile"]["id"] in str(error)
    ok = run_bundle_doc(tier="T0", with_profile=True, profile_digest="keyed")
    two = export_doc(
        purpose,
        [ok, plain],
        payload_redactions=[*protect_by_redaction(ok), *protect_by_redaction(plain)],
    )
    _reject(two, "profile_digest")
    w = world(tier="T0", with_profile=True, profile_digest="keyed")
    in_clear = w.copy()
    in_clear["test_profiles"] = [w.parts["profile"].model_dump(mode="json", by_alias=True)]
    _reject(
        export_doc(purpose, [in_clear], payload_redactions=protect_by_redaction(in_clear)),
        "profile",
    )


def test_shared_export_snapshot_digest_verifies_after_profile_digest_dropped() -> None:
    """P-17 and N20 (SCOPE P1-14, binding name): the snapshot digest and the embedded evaluator's
    digest exclude ``test_profile.content_digest``, so a shared export can drop it, or replace it
    by a keyed digest, and the recipient still verifies both. After the drop the snapshot loads
    with its original ``content_digest``, ``compute_snapshot_digest`` of the dropped document
    equals it, the embedded evaluator verifies, and the export is accepted at every non-internal
    purpose. A snapshot whose digest was forged, or whose profile ``version`` changed, is still
    ``DigestMismatch``: the profile ``id`` and ``version`` stay in the digest."""
    w = world(tier="T0", with_profile=True, profile_digest="plain")
    original = w.copy()
    snapshot_digest = drop_nulls(original["snapshot"]["content_digest"])
    evaluator_digest = drop_nulls(original["snapshot"]["evaluator"]["content_digest"])
    assert "content_digest" in original["snapshot"]["evaluator"]["test_profile"]
    for variant in ("dropped", "keyed"):
        rb = copy.deepcopy(original)
        reference = rb["snapshot"]["evaluator"]["test_profile"]
        if variant == "dropped":
            del reference["content_digest"]
        else:
            reference["content_digest"] = keyed_profile_digest(w.parts["profile"])
        snapshot_model = registered_class("ConfigSnapshot").model_validate(rb["snapshot"])
        assert drop_nulls(snapshot_model.content_digest.model_dump(mode="json")) == snapshot_digest
        computed = api("snapshot").compute_snapshot_digest(rb["snapshot"])
        assert drop_nulls(computed.model_dump(mode="json")) == snapshot_digest
        evaluator_model = registered_class("Evaluator").model_validate(rb["snapshot"]["evaluator"])
        assert (
            drop_nulls(evaluator_model.content_digest.model_dump(mode="json")) == evaluator_digest
        )
        for purpose in NON_INTERNAL:
            export = load_export(
                export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
            )
            loaded = export.run_bundles[0].snapshot
            assert drop_nulls(loaded.content_digest.model_dump(mode="json")) == snapshot_digest
    forged = copy.deepcopy(original)
    del forged["snapshot"]["evaluator"]["test_profile"]["content_digest"]
    forged["snapshot"]["content_digest"]["value"] = "0" * 64
    assert_bundle_rejected(
        export_class(),
        export_doc("shared_export", [forged], payload_redactions=protect_by_redaction(forged)),
        typed="DigestMismatch",
    )
    other_version = copy.deepcopy(original)
    del other_version["snapshot"]["evaluator"]["test_profile"]["content_digest"]
    other_version["snapshot"]["evaluator"]["test_profile"]["version"] = 2
    assert_bundle_rejected(
        export_class(),
        export_doc(
            "shared_export", [other_version], payload_redactions=protect_by_redaction(other_version)
        ),
        typed="DigestMismatch",
    )


def test_export_has_no_digest_key_material() -> None:
    """ADR 0006 Decision 3 and ADR 0015 (a keyed digest names its key by ``key_ref`` and the key is
    never in the export): the serialized export holds no byte of the HMAC key of the profile digest,
    of the digest key of a sealed payload or of the key-encryption key, in raw, hex, base64 or
    base64url form, nor the data key of a sealed payload; every digest object in it has only
    ``scheme``, ``value`` and ``key_ref``; no property of the ExportBundle schema is named like
    key material; and a member that tries to carry a key is an unknown member."""
    w = world(tier="T0", with_profile=True, profile_digest="keyed")
    rb = w.copy()
    art_id = rb["artifacts"][0]["id"]
    sealed = sealed_doc("audio", art_id, digest_key=None)
    redactions = [e for e in protect_by_redaction(rb) if e["subject_id"] != art_id]
    export = load_export(
        export_doc("shared_export", [rb], sealed_payloads=[sealed], payload_redactions=redactions)
    )
    text = export.model_dump_json(by_alias=True)
    keys = {
        "profile": PROFILE_KEY,
        "digest": DIGEST_KEY,
        "kek": KEK,
        "dek": dek_of(sealed),
    }
    for label, key in keys.items():
        forms = {
            key.hex(),
            base64.b64encode(key).decode("ascii"),
            base64.urlsafe_b64encode(key).decode("ascii"),
            base64.urlsafe_b64encode(key).decode("ascii").rstrip("="),
        }
        if all(32 <= b < 127 for b in key):
            forms.add(key.decode("ascii"))
        for form in forms:
            assert form not in text, f"{label} key material in the export"

    def digests(node: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if "scheme" in node and "value" in node:
                found.append(node)
            for value in node.values():
                found.extend(digests(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(digests(value))
        return found

    seen = digests(json.loads(text))
    assert any(d["scheme"] == KEYED for d in seen)
    for digest in seen:
        assert set(digest) <= {"scheme", "value", "key_ref"}
        if digest["scheme"] == KEYED:
            assert set(digest["key_ref"]) <= {"key_id", "key_version", "provider_hint"}
    names = property_names(export_class()) | property_names(run_class())
    assert not names & {
        "hmac_key",
        "key_material",
        "dek",
        "kek",
        "secret",
        "password",
        "private_key",
    }
    assert not [n for n in names if secret_like(n)]
    for name in ("hmac_key", "key", "digest_key", "dek"):
        with pytest.raises(ValidationError):
            export_class().model_validate({**export_doc("internal", []), name: "x"})


# ========================================================================================= consent


@pytest.mark.parametrize("status", ["not_obtained", "unknown"])
def test_benchmark_rejects_not_obtained_consent(status: str) -> None:
    """P-44, ADR 0016 Decision 7 and I14: a ``benchmark`` export of human-subject data needs
    ``consent.status=obtained``. ``not_obtained`` is rejected (``[consent]``) even when every
    payload is redacted, and also when the recording is sealed instead of redacted."""
    rb = run_bundle_doc(tier="T0", marker=_with_consent(status))
    redacted = export_doc("benchmark", [rb], payload_redactions=protect_by_redaction(rb))
    _reject(redacted, "consent")
    art_id = rb["artifacts"][0]["id"]
    sealed = export_doc(
        "benchmark",
        [rb],
        sealed_payloads=[sealed_doc("audio", art_id)],
        payload_redactions=[e for e in protect_by_redaction(rb) if e["subject_id"] != art_id],
    )
    _reject(sealed, "consent")
    obtained = run_bundle_doc(tier="T0", marker=_with_consent("obtained"))
    load_export(
        export_doc("benchmark", [obtained], payload_redactions=protect_by_redaction(obtained))
    )


def test_benchmark_rejects_unknown_consent() -> None:
    """P-44: ``unknown`` is treated as not obtained for a benchmark until consent is recorded. One
    record with ``unknown`` consent among records with ``obtained`` consent is enough: checked for
    the transcript alone and for the artifact alone, and the message names the record."""
    for holder in ("transcripts", "artifacts"):
        rb = run_bundle_doc(tier="T0")
        rb[holder][0]["privacy"] = _with_consent("unknown")
        doc = export_doc("benchmark", [rb], payload_redactions=protect_by_redaction(rb))
        error = _reject(doc, "consent")
        assert rb[holder][0]["id"] in str(error)
    both = run_bundle_doc(tier="T0", marker=_with_consent("unknown"))
    _reject(
        export_doc("benchmark", [both], payload_redactions=protect_by_redaction(both)), "consent"
    )


@pytest.mark.parametrize("status", ["not_obtained", "unknown", "obtained"])
def test_shared_export_allows_not_obtained_consent_when_sealed_or_redacted(status: str) -> None:
    """P-44, ADR 0016 I14: for an ordinary shared export, sealing or redaction is enough whatever
    the consent status: ``not_obtained`` and ``unknown`` are accepted when every payload is sealed
    or redacted, and still rejected (``[unprotected]``) when it is not."""
    rb = run_bundle_doc(tier="T0", marker=_with_consent(status))
    load_export(export_doc("shared_export", [rb], payload_redactions=protect_by_redaction(rb)))
    art_id = rb["artifacts"][0]["id"]
    load_export(
        export_doc(
            "shared_export",
            [rb],
            sealed_payloads=[sealed_doc("audio", art_id)],
            payload_redactions=[e for e in protect_by_redaction(rb) if e["subject_id"] != art_id],
        )
    )
    _reject(export_doc("shared_export", [rb]), "unprotected")


def test_benchmark_consent_of_tool_calls_comes_from_the_run_markers() -> None:
    """P (contract B7): a tool call has no marker of its own (ADR 0016 Decision 4), so in a
    benchmark its human-subject arguments and result are covered by the consent of the run's
    artifacts and transcripts. A run with tool calls but no marked record that records
    ``obtained`` consent has no consent for them: ``[consent]``; the same run is fine in a
    ``shared_export`` when the calls are redacted."""
    rb = run_bundle_doc(tier="T0")
    rb["artifacts"], rb["transcripts"], rb["turns"], rb["results"] = [], [], [], []
    rb["verdict"] = None
    rb["run"]["channel_maps"] = []
    rb["run"]["available_inputs"] = ["tool_calls", "tool_results", "usage"]
    entries = [
        redaction_entry(c["id"], ["tool_arguments", "tool_result"]) for c in rb["tool_calls"]
    ]
    shared = export_doc("shared_export", [rb], payload_redactions=entries)
    load_export(shared)
    _reject(export_doc("benchmark", [rb], payload_redactions=entries), "consent")
    with_consent = run_bundle_doc(tier="T0")
    load_export(
        export_doc(
            "benchmark", [with_consent], payload_redactions=protect_by_redaction(with_consent)
        )
    )
    # a simulated run whose derivation is false has nothing to consent to
    sim = run_bundle_doc(tier="T0", origin="simulated")
    load_export(export_doc("benchmark", [sim]))
    # the consent may be recorded on the transcript alone or on the recording alone
    only_text = run_bundle_doc(tier="T0")
    only_text["artifacts"] = []
    only_text["run"]["channel_maps"] = []
    only_text["run"]["available_inputs"] = ["transcript", "tool_calls", "tool_results", "usage"]
    only_text["results"][0]["evidence_refs"] = [
        e for e in only_text["results"][0]["evidence_refs"] if e["kind"] != "artifact_range"
    ]
    load_run(only_text)
    load_export(
        export_doc("benchmark", [only_text], payload_redactions=protect_by_redaction(only_text))
    )
    only_audio = run_bundle_doc(tier="T0")
    only_audio["transcripts"], only_audio["turns"] = [], []
    only_audio["results"][0]["evidence_refs"] = [
        e for e in only_audio["results"][0]["evidence_refs"] if e["kind"] != "turn"
    ]
    for exchange_holder in only_audio["exchanges"]:
        exchange_holder["source"] = "inferred_from_turns"
    load_run(only_audio)
    load_export(
        export_doc("benchmark", [only_audio], payload_redactions=protect_by_redaction(only_audio))
    )
    # and a run whose only consent record is not obtained is blocked (the record rule fires first)
    refused = run_bundle_doc(tier="T0", marker=_with_consent("not_obtained"))
    _reject(
        export_doc("benchmark", [refused], payload_redactions=protect_by_redaction(refused)),
        "consent",
    )


# ================================================================================= URIs (O5, O8)

LOOPBACK_URIS = (
    "http://localhost:9000/bucket/call.wav",
    "https://localhost/bucket/call.wav",
    "http://127.0.0.1:9000/bucket/call.wav",
    "https://127.0.0.1/bucket/call.wav",
    "http://127.1/bucket/call.wav",
    "http://0x7f.0.0.1/bucket/call.wav",
    "http://2130706433/bucket/call.wav",
    "http://[::1]:9000/bucket/call.wav",
    "http://app.localhost/bucket/call.wav",
)
NOT_LOOPBACK_URIS = (
    "https://recordings.example.com/calls/call.wav",
    "http://10.0.0.5:9000/bucket/call.wav",
    "http://192.168.1.2/bucket/call.wav",
    "s3://recordings-bucket/calls/call.wav",
    "gs://recordings-bucket/calls/call.wav",
)


def _with_uri(uri: str, **world_kwargs: Any) -> dict[str, Any]:
    rb = run_bundle_doc(tier="T0", **world_kwargs)
    rb["artifacts"][0]["uri"] = uri
    return rb


def _uri_export(rb: dict[str, Any], purpose: str) -> dict[str, Any]:
    return export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_shared_and_benchmark_exports_reject_file_uris(purpose: str) -> None:
    """O5 and ADR 0007 Decision 6c (a local path is meaningless to a recipient and may hold a user
    name): a ``file:`` artifact URI is valid in a record and in an ``internal`` export and is
    ``[uri]`` in a shared or benchmark export, also in the second run bundle. The message names the
    artifact and the rule, never the path."""
    uri = "file:///home/alice/recordings/call-0001.wav"
    artifact = api("artifact").Artifact.model_validate(artifact_doc(uri=uri))
    assert artifact.uri == uri
    rb = _with_uri(uri)
    load_export(export_doc("internal", [rb]))
    error = _reject(_uri_export(rb, purpose), "uri")
    assert rb["artifacts"][0]["id"] in str(error)
    assert "alice" not in str(error)
    assert "recordings" not in str(error)
    second = run_bundle_doc(tier="T0")
    doc = export_doc(
        purpose,
        [second, rb],
        payload_redactions=[*protect_by_redaction(second), *protect_by_redaction(rb)],
    )
    _reject(doc, "uri")
    windows = _with_uri("file:///C:/rec/call-0001.wav")
    _reject(_uri_export(windows, purpose), "uri")


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_shared_and_benchmark_exports_reject_loopback_uris(purpose: str) -> None:
    """O8 and ADR 0007 Decision 6d (the export half of
    ``test_artifact_uri_loopback_host_rules``): an
    ``http`` or ``https`` artifact URI whose host is loopback (``localhost``, ``*.localhost``, any
    ``127.0.0.0/8`` spelling, ``::1``) is valid in a record and in an ``internal`` export and is
    ``[uri]`` in a shared or benchmark export. Private ranges, public names and ``s3``/``gs``
    URIs are not loopback and are accepted. The message names the artifact, not the URI."""
    artifact_cls = api("artifact")
    for uri in (*LOOPBACK_URIS, *NOT_LOOPBACK_URIS):
        artifact_cls.Artifact.model_validate(artifact_doc(uri=uri))  # valid on the record
    for uri in LOOPBACK_URIS:
        assert artifact_cls.is_loopback_uri(uri) is True, uri
        rb = _with_uri(uri)
        load_export(export_doc("internal", [rb]))
        error = _reject(_uri_export(rb, purpose), "uri")
        assert rb["artifacts"][0]["id"] in str(error)
        assert uri.split("//", 1)[1].split("/", 1)[0] not in str(error), uri
    for uri in NOT_LOOPBACK_URIS:
        assert artifact_cls.is_loopback_uri(uri) is False, uri
        load_export(_uri_export(_with_uri(uri), purpose))


# =========================================================================== source_run_id (O7, O9)


def _items(in_run: str, outside: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    inside = dataset_item_doc(source_run_id=in_run)
    out = dataset_item_doc(source_run_id=outside or new_id("run"))
    return inside, out


def test_export_source_run_id_kept_only_where_policy_allows() -> None:
    """O9, ADR 0016 Decision 9 and D2 (SCOPE P1-14, binding name). ``internal`` exports keep a
    ``DatasetItem.source_run_id``, resolved or not. ``shared_export`` and ``benchmark`` exports keep
    it only when the source run is in the same export; an item that still names a run the export
    does not hold is ``[source_run]`` (the message names the item, never the run ID). The dropped
    copy of the item plus a ``ProvenanceRedaction{item_id, field, reason}`` is accepted; the entry
    is
    serialized in the export and never holds the run ID."""
    rb = run_bundle_doc(tier="T0")
    inside, out = _items(rb["run"]["id"])
    omitted_run = out["source_run_id"]
    load_export(export_doc("internal", [rb], dataset_items=[inside, out]))
    for purpose in NON_INTERNAL:
        redactions = protect_by_redaction(rb)
        kept = export_doc(purpose, [rb], dataset_items=[inside, out], payload_redactions=redactions)
        error = _reject(kept, "source_run")
        assert out["id"] in str(error)
        assert omitted_run not in str(error)
        dropped = {k: v for k, v in out.items() if k != "source_run_id"}
        entry = {"item_id": out["id"], "field": "source_run_id", "reason": "unresolved_reference"}
        good = export_doc(
            purpose,
            [rb],
            dataset_items=[inside, dropped],
            payload_redactions=redactions,
            provenance_redactions=[entry],
        )
        export = load_export(good)
        assert export.provenance_redactions[0].model_dump(mode="json") == entry
        assert [i.source_run_id is None for i in export.dataset_items] == [False, True]
        dumped = export.model_dump_json(by_alias=True)
        assert omitted_run not in dumped
        assert json.loads(dumped)["provenance_redactions"] == [entry]
        # an item that names a run of the export keeps its field, and needs no entry
        assert str(export.dataset_items[0].source_run_id) == rb["run"]["id"]
        # a second run bundle makes its run resolvable
        other = run_bundle_doc(tier="T0")
        late = dataset_item_doc(source_run_id=other["run"]["id"])
        load_export(
            export_doc(
                purpose,
                [rb, other],
                dataset_items=[late],
                payload_redactions=[*redactions, *protect_by_redaction(other)],
            )
        )


@pytest.mark.parametrize("purpose", PURPOSES)
def test_provenance_redaction_entries_are_checked(purpose: str) -> None:
    """ADR 0016 Decision 9 (export validation "rejects an omission entry whose item is absent or
    still carries the field") and D2: an entry for an item that the export does not hold, an entry
    for an item that still has ``source_run_id`` and two entries for one item are
    ``[provenance_redaction]``, in every purpose. The ``field`` and ``reason`` are closed sets."""
    rb = run_bundle_doc(tier="T0")
    redactions = protect_by_redaction(rb) if purpose != "internal" else []

    def entry_of(item_id: str) -> dict[str, Any]:
        return {"item_id": item_id, "field": "source_run_id", "reason": "unresolved_reference"}

    bare = dataset_item_doc()
    still = dataset_item_doc(source_run_id=rb["run"]["id"])
    base = {"payload_redactions": redactions, "dataset_items": [bare, still]}
    load_export(export_doc(purpose, [rb], provenance_redactions=[entry_of(bare["id"])], **base))
    _reject(
        export_doc(purpose, [rb], provenance_redactions=[entry_of(new_id("dti"))], **base),
        "provenance_redaction",
    )
    _reject(
        export_doc(purpose, [rb], provenance_redactions=[entry_of(still["id"])], **base),
        "provenance_redaction",
    )
    _reject(
        export_doc(
            purpose,
            [rb],
            provenance_redactions=[entry_of(bare["id"]), entry_of(bare["id"])],
            **base,
        ),
        "provenance_redaction",
    )
    model = registered_class("ProvenanceRedaction")
    for bad in (
        {**entry_of(bare["id"]), "field": "artifact_id"},
        {**entry_of(bare["id"]), "reason": "other"},
        {**entry_of(bare["id"]), "run_id": rb["run"]["id"]},
        {**entry_of(bare["id"]), "value": rb["run"]["id"]},
        {"item_id": bare["id"], "field": "source_run_id"},
        entry_of(new_id("run")),
        entry_of("dti_x"),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(bad)


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_provenance_redaction_never_satisfies_seal_or_redaction(purpose: str) -> None:
    """ADR 0016 Decision 9 and D2: the recorded omission of a field is not a ``RedactionRecord`` and
    never protects a human-subject payload. The unprotected bundle stays ``[unprotected]`` with
    any number of valid entries, and an entry cannot stand in for a payload redaction (the
    ``payload_redactions`` list takes ``PayloadRedaction`` entries only)."""
    rb = run_bundle_doc(tier="T0")
    item = dataset_item_doc()
    entry = {"item_id": item["id"], "field": "source_run_id", "reason": "unresolved_reference"}
    doc = export_doc(purpose, [rb], dataset_items=[item], provenance_redactions=[entry])
    _reject(doc, "unprotected")
    doc["payload_redactions"] = [entry]
    with pytest.raises(ValidationError):
        export_class().model_validate(doc)
    assert "covers" not in registered_class("ProvenanceRedaction").model_fields


def test_export_dataset_item_source_run_id_is_unresolved_provenance() -> None:
    """O7, ADR 0016 Decision 5, 9 and I4a (SCOPE P1-14, binding name): an export never requires
    ``DatasetItem.source_run_id`` to resolve, never fills or rewrites it, and never takes role
    evidence from outside the bundle. An ``internal`` export keeps an item whose source run is
    absent, unedited. In a shared export the item whose source run is absent has no role evidence
    and its payload stays human-subject; the item that names a run of the export derives from that
    run's turns, so a precautionary marker on a simulated run is non-human, and a benchmark does
    not consult its stored ``unknown`` consent."""
    sim = _simulated_precaution("unknown")
    art_id = sim["artifacts"][0]["id"]
    marker = _precaution("unknown")
    stray_run = new_id("run")
    item = dataset_item_doc(artifact_id=art_id, privacy=marker, source_run_id=stray_run)
    internal = load_export(export_doc("internal", [sim], dataset_items=[item]))
    assert str(internal.dataset_items[0].source_run_id) == stray_run
    assert internal.dataset_items[0].privacy.human_subject_reason == "marked_before_role_evidence"
    # shared: the dropped copy has no source run; its payload (the artifact) stays human-subject
    dropped = {k: v for k, v in item.items() if k != "source_run_id"}
    entry = {"item_id": item["id"], "field": "source_run_id", "reason": "unresolved_reference"}
    bare = export_doc(
        "shared_export", [sim], dataset_items=[dropped], provenance_redactions=[entry]
    )
    _reject(bare, "unprotected")
    covered = copy.deepcopy(bare)
    covered["payload_redactions"] = [redaction_entry(art_id, ["audio"])]
    load_export(covered)
    # benchmark: the same item, redacted payload, consent unknown, is blocked by the item's consent
    bench = copy.deepcopy(covered)
    bench["purpose"] = "benchmark"
    _reject(bench, "consent")
    # an item that names the run of the export derives from that run's evidence
    resolved = dataset_item_doc(artifact_id=art_id, privacy=marker, source_run_id=sim["run"]["id"])
    for purpose in NON_INTERNAL:
        export = load_export(export_doc(purpose, [sim], dataset_items=[resolved]))
        assert str(export.dataset_items[0].source_run_id) == sim["run"]["id"]
        before = drop_nulls(export.dataset_items[0].model_dump(mode="json"))
        assert before == resolved
    # an item that names the run, whose marker is a manual one, is human-subject
    manual = dataset_item_doc(
        artifact_id=art_id,
        privacy=_precaution("unknown", "manual"),
        source_run_id=sim["run"]["id"],
    )
    _reject(export_doc("shared_export", [sim], dataset_items=[manual]), "unprotected")


def test_export_dataset_ids_resolve_inside_the_export() -> None:
    """ADR 0009 Decision 5 (a ``Dataset`` version lists its ``item_ids``): every listed item is a
    ``DatasetItem`` of the export, else ``[unresolved]``. An item that no dataset lists is fine, and
    so are two datasets that list the same item. Two items with one ID are ``[duplicate]``."""
    rb = run_bundle_doc(tier="T0")
    one, two, loose = dataset_item_doc(), dataset_item_doc(), dataset_item_doc()
    ds = dataset_doc([one["id"], two["id"]])
    ds_again = dataset_doc([one["id"]], name="Smaller")
    for purpose in PURPOSES:
        base = {"payload_redactions": protect_by_redaction(rb)} if purpose != "internal" else {}
        load_export(
            export_doc(
                purpose, [rb], datasets=[ds, ds_again], dataset_items=[one, two, loose], **base
            )
        )
        _reject(
            export_doc(purpose, [rb], datasets=[ds], dataset_items=[one, loose], **base),
            "unresolved",
        )
        _reject(
            export_doc(purpose, [rb], datasets=[], dataset_items=[one, copy.deepcopy(one)], **base),
            "duplicate",
        )


def test_dataset_item_payload_is_protected_when_the_item_is_human_subject() -> None:
    """P (contract B6, conservative): a payload of the export that a human-subject dataset item
    references (``artifact_id`` or ``transcript_id``) is protected like a payload whose own marker
    says so. A simulated artifact with a false marker, referenced by an item whose export-time value
    is true, is ``[unprotected]``; a benchmark also needs the item's consent obtained. An item
    without a payload reference is not a payload."""
    sim = run_bundle_doc(tier="T0", origin="simulated")
    art_id, tsc_id = sim["artifacts"][0]["id"], sim["transcripts"][0]["id"]
    by_artifact = dataset_item_doc(artifact_id=art_id, privacy=privacy_human())
    by_transcript = dataset_item_doc(transcript_id=tsc_id, privacy=privacy_human())
    for item in (by_artifact, by_transcript):
        _reject(export_doc("shared_export", [sim], dataset_items=[item]), "unprotected")
    both = export_doc("shared_export", [sim], dataset_items=[by_artifact, by_transcript])
    both["payload_redactions"] = [
        redaction_entry(art_id, ["audio"]),
        redaction_entry(tsc_id, ["text"]),
    ]
    load_export(both)
    bench = copy.deepcopy(both)
    bench["purpose"] = "benchmark"
    load_export(bench)  # the items carry consent obtained
    weak = dataset_item_doc(artifact_id=art_id, privacy=_with_consent("unknown"))
    bench["dataset_items"] = [weak, by_transcript]
    _reject(bench, "consent")
    shared = copy.deepcopy(bench)
    shared["purpose"] = "shared_export"
    load_export(shared)
    plain = dataset_item_doc()
    load_export(export_doc("benchmark", [sim], dataset_items=[plain]))
    no_payload = dataset_item_doc(privacy=_with_consent("unknown"))
    load_export(export_doc("benchmark", [sim], dataset_items=[no_payload]))


# ================================================================================ sealed payloads


def test_sealed_payload_accepted_in_export_bundle_and_refused_elsewhere() -> None:
    """ADR 0015 Decision 7, I12 (the behavioural half of ``test_sealed_only_in_export_bundle``): an
    ``ExportBundle`` accepts a sealed payload at every purpose; a ``RunBundle`` and a
    ``ConfigSnapshot`` refuse a document that carries one (an unknown member), and no member of
    their schemas can hold it."""
    rb = run_bundle_doc(tier="T0", origin="simulated")
    art_id = rb["artifacts"][0]["id"]
    sealed = sealed_doc("audio", art_id)
    for purpose in PURPOSES:
        export = load_export(export_doc(purpose, [rb], sealed_payloads=[sealed]))
        assert len(export.sealed_payloads) == 1
        assert export.sealed_payloads[0].aad_fields.payload_type == "audio"
    with pytest.raises(ValidationError):
        run_class().model_validate({**rb, "sealed_payloads": [sealed]})
    with pytest.raises(ValidationError):
        run_class().model_validate({**rb, "metadata": {"sealed": 1}, "sealed": sealed})
    snapshot_doc_ = copy.deepcopy(rb["snapshot"])
    with pytest.raises(ValidationError):
        registered_class("ConfigSnapshot").model_validate(
            {**snapshot_doc_, "sealed_payloads": [sealed]}
        )
    for name in ("artifacts", "turns", "tool_calls", "run"):
        record = copy.deepcopy(rb)
        target = record[name] if isinstance(record[name], dict) else record[name][0]
        target["sealed"] = sealed
        with pytest.raises(ValidationError):
            run_class().model_validate(record)


def test_sealed_payload_subject_must_be_in_the_export() -> None:
    """ADR 0015 Decision 4 (``subject_id`` is the sealed subject): the subject of a sealed payload
    is
    a record of the export - the artifact, transcript or tool call that its payload type names, or
    a test profile that a snapshot of the export references with that ``subject_version``. Otherwise
    ``[sealed]``; the message names the subject ID, never any sealed byte. Two payloads of one type
    for one subject are ``[duplicate]``."""
    w = world(tier="T0", origin="simulated", with_profile=True, profile_digest="keyed")
    rb = w.copy()
    art_id = rb["artifacts"][0]["id"]
    tsc_id = rb["transcripts"][0]["id"]
    call_id = rb["tool_calls"][0]["id"]
    profile = rb["snapshot"]["evaluator"]["test_profile"]
    good = [
        sealed_doc("audio", art_id),
        sealed_doc("transcript_text", tsc_id),
        sealed_doc("tool_arguments", call_id),
        sealed_doc("tool_result", call_id),
        sealed_doc("test_profile", profile["id"], subject_version=profile["version"]),
    ]
    load_export(export_doc("shared_export", [rb], sealed_payloads=good))
    for strange in (
        sealed_doc("audio", new_id("art")),
        sealed_doc("transcript_text", new_id("tsc")),
        sealed_doc("tool_arguments", new_id("tcl")),
        sealed_doc("tool_result", new_id("tcl")),
        sealed_doc("test_profile", new_id("tpf"), subject_version=1),
        sealed_doc("test_profile", profile["id"], subject_version=profile["version"] + 1),
    ):
        error = _reject(export_doc("shared_export", [rb], sealed_payloads=[strange]), "sealed")
        assert strange["aad_fields"]["subject_id"] in str(error)
        assert_error_free_of(
            error, strange["ciphertext"][:24], strange["dek_wrap"]["wrapped_dek"][:24]
        )
    twice = [sealed_doc("audio", art_id), sealed_doc("audio", art_id)]
    _reject(export_doc("shared_export", [rb], sealed_payloads=twice), "duplicate")
    both_kinds = [sealed_doc("tool_arguments", call_id), sealed_doc("tool_result", call_id)]
    load_export(export_doc("shared_export", [rb], sealed_payloads=both_kinds))


def test_sealed_audio_protects_the_recording() -> None:
    """ADR 0016 Decision 7: a ``SealedPayload`` of type ``audio`` for the artifact protects a
    human-subject recording in a shared or benchmark export, the same as a redaction. The sealed
    payload of another artifact, or of another payload type, does not."""
    rb = run_bundle_doc(tier="T0")
    art_id = rb["artifacts"][0]["id"]
    rest = [e for e in protect_by_redaction(rb) if e["subject_id"] != art_id]
    for purpose in NON_INTERNAL:
        load_export(
            export_doc(
                purpose,
                [rb],
                sealed_payloads=[sealed_doc("audio", art_id)],
                payload_redactions=rest,
            )
        )
        other = run_bundle_doc(tier="T0")
        wrong = [sealed_doc("audio", other["artifacts"][0]["id"])]
        two = export_doc(
            purpose,
            [rb, other],
            sealed_payloads=wrong,
            payload_redactions=[*rest, *protect_by_redaction(other)],
        )
        _reject(two, "unprotected")
        by_text = export_doc(
            purpose,
            [rb],
            sealed_payloads=[sealed_doc("transcript_text", rb["transcripts"][0]["id"])],
            payload_redactions=rest,
        )
        _reject(by_text, "unprotected")


def test_sealed_text_replaces_the_turns_of_its_transcript() -> None:
    """P (contract B3, the sealed record keeps its provenance fields; its children are withheld): a
    sealed ``transcript_text`` payload protects the text only when the transcript's turns are no
    longer in the bundle - turns beside their own ciphertext would be the text in the clear. With
    the turns present the transcript is ``[unprotected]`` unless a redaction covers ``text``. The
    run bundle that lost its turns declares them withheld, so evidence references to them resolve,
    and the export is accepted."""
    rb = run_bundle_doc(tier="T1")
    tsc_id = rb["transcripts"][0]["id"]
    rest = [e for e in protect_by_redaction(rb) if e["subject_id"] != tsc_id]
    sealed = [sealed_doc("transcript_text", tsc_id)]
    for purpose in NON_INTERNAL:
        _reject(
            export_doc(purpose, [rb], sealed_payloads=sealed, payload_redactions=rest),
            "unprotected",
        )
        stripped = copy.deepcopy(rb)
        turn_ids = [t["id"] for t in stripped["turns"]]
        stripped["turns"] = []
        stripped["withheld"] = [withheld_doc("turn", i) for i in turn_ids]
        ok = export_doc(purpose, [stripped], sealed_payloads=sealed, payload_redactions=rest)
        export = load_export(ok)
        assert export.run_bundles[0].turns == []
        assert [str(w.record_id) for w in export.run_bundles[0].withheld] == turn_ids
        undeclared = copy.deepcopy(stripped)
        undeclared["withheld"] = []
        _reject(
            export_doc(purpose, [undeclared], sealed_payloads=sealed, payload_redactions=rest),
            "unresolved",
        )
    # a transcript without any turn in the bundle holds no text to protect
    empty = copy.deepcopy(rb)
    empty["turns"] = []
    empty["withheld"] = [withheld_doc("turn", t["id"]) for t in rb["turns"]]
    load_export(export_doc("shared_export", [empty], payload_redactions=rest))


def test_sealed_tool_payloads_replace_the_clear_fields() -> None:
    """P (contract B3): a sealed ``tool_arguments`` payload protects the arguments only when the
    exported tool call no longer carries them (``arguments_raw`` and ``arguments`` absent), and a
    sealed ``tool_result`` only when ``result_raw`` is absent; otherwise the clear field needs a
    redaction. The arguments and the result are separate payloads: sealing one and leaving the
    other in the clear is ``[unprotected]`` whichever one is sealed. The event keeps its other
    fields (name, status, seq, source, completeness flags)."""
    rb = run_bundle_doc(tier="T1")
    first = rb["tool_calls"][0]
    rest = [e for e in protect_by_redaction(rb) if e["subject_id"] != first["id"]]
    sealed_args = sealed_doc("tool_arguments", first["id"])
    sealed_result = sealed_doc("tool_result", first["id"])
    args_fields = ("arguments_raw", "arguments")
    result_fields = ("result_raw", "result_complete")

    def stripped_of(*fields: str) -> dict[str, Any]:
        doc = copy.deepcopy(rb)
        for field in fields:
            doc["tool_calls"][0].pop(field)
        return doc

    for purpose in NON_INTERNAL:
        # sealed but still in the clear: protected by neither
        for sealed in ([sealed_args], [sealed_result], [sealed_args, sealed_result]):
            clear = export_doc(purpose, [rb], sealed_payloads=sealed, payload_redactions=rest)
            _reject(clear, "unprotected")
        # arguments sealed and removed, the result in the clear but redacted: accepted
        only_args = export_doc(
            purpose,
            [stripped_of(*args_fields)],
            sealed_payloads=[sealed_args],
            payload_redactions=[*rest, redaction_entry(first["id"], ["tool_result"])],
        )
        load_export(only_args)
        # the same with the result sealed and removed
        only_result = export_doc(
            purpose,
            [stripped_of(*result_fields)],
            sealed_payloads=[sealed_result],
            payload_redactions=[*rest, redaction_entry(first["id"], ["tool_arguments"])],
        )
        load_export(only_result)
        # arguments sealed and removed, result sealed but in the clear and not redacted
        half = export_doc(
            purpose,
            [stripped_of(*args_fields)],
            sealed_payloads=[sealed_args, sealed_result],
            payload_redactions=rest,
        )
        _reject(half, "unprotected")
        # arguments removed and result in the clear, only the result sealed: arguments are gone
        # (nothing to protect), the result is still in the clear
        mirror = export_doc(
            purpose,
            [stripped_of(*args_fields)],
            sealed_payloads=[sealed_result],
            payload_redactions=rest,
        )
        _reject(mirror, "unprotected")
        # both sealed and both removed: accepted
        both = export_doc(
            purpose,
            [stripped_of(*args_fields, *result_fields)],
            sealed_payloads=[sealed_args, sealed_result],
            payload_redactions=rest,
        )
        load_export(both)
        # result sealed and removed but the arguments sealed and still in the clear: rejected
        clear_args = export_doc(
            purpose,
            [stripped_of(*result_fields)],
            sealed_payloads=[sealed_args, sealed_result],
            payload_redactions=rest,
        )
        _reject(clear_args, "unprotected")


def _with_second_records(rb: dict[str, Any]) -> dict[str, Any]:
    """The run bundle plus a second artifact and a second transcript with a turn of its own and a
    third tool call, each a copy of the first with a new ID (and a result evidence reference)."""
    out = copy.deepcopy(rb)
    artifact = copy.deepcopy(out["artifacts"][0])
    artifact["id"] = new_id("art")
    out["artifacts"].append(artifact)
    transcript = copy.deepcopy(out["transcripts"][0])
    transcript["id"] = new_id("tsc")
    out["transcripts"].append(transcript)
    turn = copy.deepcopy(out["turns"][0])
    turn.update(id=new_id("turn"), transcript_id=transcript["id"], seq=0, exchange_index=None)
    out["turns"].append(turn)
    call = copy.deepcopy(out["tool_calls"][0])
    call.update(id=new_id("tcl"), seq=9)
    out["tool_calls"].append(call)
    return out


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_every_record_of_a_kind_is_checked_not_only_the_first(purpose: str) -> None:
    """The export rules run over every record of a kind: the second artifact, the second
    transcript, the third tool call, the second sealed payload, the second redaction entry and the
    second declaration are checked as the first is. A violation in the later record is the same
    error as in the first."""
    rb = _with_second_records(run_bundle_doc(tier="T0"))
    full = protect_by_redaction(rb)
    load_export(export_doc(purpose, [rb], payload_redactions=full))
    second_art = rb["artifacts"][1]["id"]
    second_tsc = rb["transcripts"][1]["id"]
    third_call = rb["tool_calls"][2]["id"]
    for subject in (second_art, second_tsc, third_call):
        missing = [e for e in full if e["subject_id"] != subject]
        _reject(export_doc(purpose, [rb], payload_redactions=missing), "unprotected")
    loopback = copy.deepcopy(rb)
    loopback["artifacts"][1]["uri"] = "http://localhost:9000/bucket/second.wav"
    _reject(export_doc(purpose, [loopback], payload_redactions=full), "uri")
    file_uri = copy.deepcopy(rb)
    file_uri["artifacts"][1]["uri"] = "file:///home/alice/second.wav"
    _reject(export_doc(purpose, [file_uri], payload_redactions=full), "uri")
    stray = [*full, redaction_entry(new_id("tsc"), ["text"])]
    _reject(export_doc(purpose, [rb], payload_redactions=stray), "redaction")
    sealed = [sealed_doc("audio", second_art), sealed_doc("audio", new_id("art"))]
    _reject(export_doc(purpose, [rb], payload_redactions=full, sealed_payloads=sealed), "sealed")
    items = [dataset_item_doc(), dataset_item_doc(source_run_id=new_id("run"))]
    _reject(export_doc(purpose, [rb], payload_redactions=full, dataset_items=items), "source_run")
    two_runs = [run_bundle_doc(tier="T0"), rb]
    _reject(
        export_doc(
            purpose, two_runs, payload_redactions=[*protect_by_redaction(two_runs[0]), *full][:-1]
        ),
        "unprotected",
    )
    if purpose == "benchmark":
        for holder in ("artifacts", "transcripts"):
            weak = copy.deepcopy(rb)
            weak[holder][1]["privacy"] = _with_consent("unknown")
            _reject(export_doc(purpose, [weak], payload_redactions=full), "consent")
    # a later record also fails the run bundle's own rules
    looser = copy.deepcopy(rb)
    looser["run"]["caller_origin"] = "simulated"
    looser["run"]["mode"] = "simulated"
    looser["turns"][1]["role"] = "unknown"
    looser["transcripts"][1]["privacy"] = privacy_synthetic()
    _reject(export_doc(purpose, [looser], payload_redactions=full), "marker")
    declared = copy.deepcopy(rb)
    declared["withheld"] = [
        withheld_doc("turn", new_id("turn")),
        withheld_doc("artifact", second_art),
    ]
    _reject(export_doc(purpose, [declared], payload_redactions=full), "withheld")


def test_later_items_run_bundles_and_entries_are_checked_too() -> None:
    """The rules run over every dataset item, dataset, run bundle and entry, not only the first:
    the second item's payload protection, the second dataset's items, the caller identifier or the
    profile digest of the second run bundle, a sealed profile that the second run bundle's
    snapshot references."""
    sim = run_bundle_doc(tier="T0", origin="simulated")
    second_art = copy.deepcopy(sim["artifacts"][0])
    second_art["id"] = new_id("art")
    sim["artifacts"].append(second_art)
    first = dataset_item_doc(
        artifact_id=sim["artifacts"][0]["id"],
        privacy=_precaution("unknown"),
        source_run_id=sim["run"]["id"],
    )
    later = dataset_item_doc(artifact_id=second_art["id"], privacy=privacy_human())
    load_export(export_doc("shared_export", [sim], dataset_items=[first]))
    _reject(export_doc("shared_export", [sim], dataset_items=[first, later]), "unprotected")
    redacted = [redaction_entry(second_art["id"], ["audio"])]
    load_export(
        export_doc(
            "shared_export", [sim], dataset_items=[first, later], payload_redactions=redacted
        )
    )
    ds_ok = dataset_doc([first["id"]])
    ds_bad = dataset_doc([later["id"], new_id("dti")], name="Later")
    _reject(
        export_doc("shared_export", [sim], datasets=[ds_ok, ds_bad], dataset_items=[first, later]),
        "unresolved",
    )
    # run bundles: the caller identifier, the internal digest, the sealed profile of the second
    plain = run_bundle_doc(tier="T0")
    carrying = run_bundle_doc(tier="T0")
    carrying["run"]["caller_external_id"] = {"value": "+91 98765 43210", "privacy": privacy_human()}
    _reject(export_doc("internal", [plain, carrying]), "caller_external_id")
    keyed = run_bundle_doc(tier="T0", with_profile=True, profile_digest="keyed")
    _reject(export_doc("internal", [run_bundle_doc(tier="T0"), keyed]), "profile_digest")
    with_profile = run_bundle_doc(tier="T0", with_profile=True, profile_digest="keyed")
    reference = with_profile["snapshot"]["evaluator"]["test_profile"]
    sealed_profile = sealed_doc(
        "test_profile", reference["id"], subject_version=reference["version"]
    )
    other = run_bundle_doc(tier="T0")
    load_export(
        export_doc(
            "shared_export",
            [other, with_profile],
            sealed_payloads=[sealed_profile],
            payload_redactions=[*protect_by_redaction(other), *protect_by_redaction(with_profile)],
        )
    )


# ========================================================================= identity and caller ids


def test_export_bundle_holds_no_caller_identifier() -> None:
    """ADR 0016 Decision 6 and I7, A-27 (the ExportBundle half of ``test_user_id_not_in_snapshot_
    or_export``): ``Run.caller_external_id`` (a transport ``user_id``, maybe a phone number) never
    appears in an ExportBundle. A run bundle that carries one is valid on its own (it is the
    producer's record) and an export of it is ``[caller_external_id]`` at every purpose, naming the
    run and never the number; the export builder strips the field. No ExportBundle member is called
    ``user_id``."""
    number = "+91 98765 43210"
    rb = run_bundle_doc(tier="T0")
    rb["run"]["caller_external_id"] = {"value": number, "privacy": privacy_human()}
    load_run(rb)
    for purpose in PURPOSES:
        doc = export_doc(
            purpose,
            [rb],
            payload_redactions=protect_by_redaction(rb) if purpose != "internal" else [],
        )
        error = _reject(doc, "caller_external_id")
        assert rb["run"]["id"] in str(error)
        assert_error_free_of(error, number)
    stripped = copy.deepcopy(rb)
    del stripped["run"]["caller_external_id"]
    load_export(export_doc("internal", [stripped]))
    for name in ("user_id", "caller_external_id", "caller_id", "phone"):
        assert name not in export_class().model_fields
        with pytest.raises(ValidationError):
            export_class().model_validate({**export_doc("internal", []), name: number})


def test_export_bundle_holds_run_bundles_once() -> None:
    """ADR 0001 (``rbnd`` is referenced by ``exp``) and N22: the run bundles of an export are
    distinct. The same run bundle twice, and two run bundles of one run, are ``[duplicate]``; a
    shared record in two bundles is too. Two different runs are fine, and each is checked on its
    own (an export of a good run and a bad run is rejected as a whole, naming the bad record)."""
    a, b = run_bundle_doc(tier="T0"), run_bundle_doc(tier="T0")
    load_export(export_doc("internal", [a, b]))
    _reject(export_doc("internal", [a, copy.deepcopy(a)]), "duplicate")
    c = run_bundle_doc(tier="T0")
    c["run"]["id"] = a["run"]["id"]
    for usage in c["usage_records"]:
        usage["run_id"] = a["run"]["id"]
    assert_bundle_rejected(
        export_class(), export_doc("internal", [a, c]), typed="ConflictingRecordError"
    )
    bad = run_bundle_doc(tier="T0")
    bad["usage_records"][0]["run_id"] = new_id("run")
    error = _reject(export_doc("internal", [a, bad]), "membership")
    assert bad["usage_records"][0]["id"] in str(error)


def test_export_bundle_is_checked_through_the_bundle_functions() -> None:
    """Contract section 4: ``bundle.check_run_bundle(bundle)`` and ``bundle.check_export_bundle(
    bundle)`` are the rules the models run on load; a valid model passes, and the functions raise
    ``ValueError`` with the tagged message for a model that was built without validation
    (``model_construct``). They are idempotent and do not change the bundle."""
    rb = run_bundle_doc(tier="T0")
    bundle = load_run(rb)
    before = bundle.model_dump(mode="json", by_alias=True)
    bundle_fn("check_run_bundle")(bundle)
    bundle_fn("check_run_bundle")(bundle)
    assert bundle.model_dump(mode="json", by_alias=True) == before
    export = load_export(shared_ready("shared_export", tier="T0"))
    bundle_fn("check_export_bundle")(export)
    broken_doc = copy.deepcopy(rb)
    broken_doc["usage_records"][0]["run_id"] = new_id("run")
    constructed_usage = registered_class("UsageRecord").model_validate(
        broken_doc["usage_records"][0]
    )
    broken = bundle.model_copy(update={"usage_records": [constructed_usage]})
    with pytest.raises(ValueError, match=r"\[membership\]"):
        bundle_fn("check_run_bundle")(broken)
    unprotected = export.model_copy(update={"payload_redactions": []})
    with pytest.raises(ValueError, match=r"\[unprotected\]"):
        bundle_fn("check_export_bundle")(unprotected)
