# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 7 (P1-14), part 3: the observed-tier check (R2) and the declaration of withheld records.

Contract: docs/phase1/api-surface-bundle.md section 6. ADR 0004 Decision 2a (O3, O3a, D3 of
api-surface 22.12): ``Run.observed_tier`` is the highest tier whose evidence the run holds, and a
bundle check requires the stored tier to equal the tier computed from the closed provenance fields
of the bundle's records (``check_observed_tier``, R2). A legitimate partial export would fail R2, so
a run bundle declares the records it deliberately withholds (``WithheldRecord{kind, record_id}``):

- the declaration is explicit, per run (a field of the ``RunBundle``, which is the unit of a run),
  and names record kinds and IDs, never values; without it R2 is strict;
- an under-claim (stored tier below the computed one) is always rejected;
- an over-claim is accepted only when the declaration covers a record kind that can carry the
  missing evidence (transcript, turn, exchange, tool call, stage span, usage record); a declared
  artifact (or result, or cost record) alone does not excuse it;
- a declaration never raises the computed tier;
- a sealed or redacted record keeps its provenance fields readable and counts as present; the
  turns that a sealed transcript text replaced are gone and count only through a declaration.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import new_id, registered_class
from ._support_b3 import QUOTE_MARK, make3, run_doc
from ._support_b4 import make_usage
from ._support_b7 import (
    assert_bundle_rejected,
    bundle_fn,
    export_class,
    export_doc,
    load_export,
    load_run,
    protect_by_redaction,
    redaction_entry,
    run_bundle_doc,
    run_class,
    sealed_doc,
    withheld_doc,
)

PROVENANCE_KINDS = ("transcript", "turn", "exchange", "tool_call", "span", "usage_record")
OTHER_KINDS = ("artifact", "result", "cost_record")
KIND_PREFIX = {
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


def _withheld(kind: str) -> dict[str, Any]:
    return withheld_doc(kind, new_id(KIND_PREFIX[kind]))


def _reject_run(doc: dict[str, Any], *tags: str) -> BaseException:
    return assert_bundle_rejected(run_class(), doc, *tags)


def _artifact_origin(art_id: str) -> dict[str, Any]:
    return {"kind": "artifact", "ref": art_id}


# =========================================================================== evidence_tier


def _turn(timing: str) -> Any:
    extra = {} if timing == "none" else {"start_ms": 0, "end_ms": 400}
    return make3("Turn", timing_source=timing, **extra)


def _usage(domain: str, source: str) -> Any:
    return make_usage(None, domain=domain, source=source)


def _call(**over: Any) -> Any:
    return make3("ToolCallEvent", **over)


def _transcript(**over: Any) -> Any:
    return make3("Transcript", **over)


@pytest.mark.parametrize(
    ("label", "build", "expected"),
    [
        ("transcript our_stt", lambda: _transcript(source="our_stt"), "T0"),
        ("transcript agent_export", lambda: _transcript(source="agent_export"), "T0"),
        ("transcript script", lambda: _transcript(source="script"), "T0"),
        ("transcript human_reference", lambda: _transcript(source="human_reference"), "T0"),
        ("transcript agent_telemetry", lambda: _transcript(source="agent_telemetry"), "T1"),
        ("transcript observer", lambda: _transcript(source="observer"), "T2"),
        ("turn none", lambda: _turn("none"), "T0"),
        ("turn audio_alignment", lambda: _turn("audio_alignment"), "T0"),
        ("turn telemetry", lambda: _turn("telemetry"), "T1"),
        ("turn observer", lambda: _turn("observer"), "T2"),
        ("turn source is a pipeline stage", lambda: make3("Turn", source="agent_log"), "T0"),
        ("exchange audio_alignment", lambda: make3("Exchange", source="audio_alignment"), "T0"),
        ("exchange inferred", lambda: make3("Exchange", source="inferred_from_turns"), "T0"),
        ("exchange telemetry", lambda: make3("Exchange", source="telemetry"), "T1"),
        ("exchange observer", lambda: make3("Exchange", source="observer"), "T2"),
        ("call agent_export", lambda: _call(source="agent_export"), "T0"),
        ("call agent_telemetry", lambda: _call(source="agent_telemetry"), "T1"),
        ("call observer", lambda: _call(source="observer"), "T2"),
        (
            "call origin telemetry",
            lambda: _call(
                source="agent_export",
                offset_ms=10,
                timeline_origin={
                    "kind": "telemetry",
                    "ref": {"system": "otel", "kind": "trace", "value": "t1"},
                },
            ),
            "T1",
        ),
        (
            "call origin observer",
            lambda: _call(
                source="agent_export",
                offset_ms=10,
                timeline_origin={
                    "kind": "observer",
                    "ref": {"system": "obs", "kind": "run", "value": "r1"},
                },
            ),
            "T2",
        ),
        (
            "transcript origin artifact",
            lambda: _transcript(source="our_stt", timeline_origin=_artifact_origin(new_id("art"))),
            "T0",
        ),
        (
            "transcript origin telemetry",
            lambda: _transcript(
                source="our_stt",
                timeline_origin={
                    "kind": "telemetry",
                    "ref": {"system": "otel", "kind": "trace", "value": "t1"},
                },
            ),
            "T1",
        ),
        (
            "transcript origin observer",
            lambda: _transcript(
                source="our_stt",
                timeline_origin={
                    "kind": "observer",
                    "ref": {"system": "obs", "kind": "run", "value": "r1"},
                },
            ),
            "T2",
        ),
        ("any stage span", lambda: make3("StageSpan"), "T1"),
        ("agent usage from telemetry", lambda: _usage("agent_under_test", "telemetry"), "T1"),
        ("agent usage reported", lambda: _usage("agent_under_test", "provider_reported"), "T0"),
        ("evaluation usage from telemetry", lambda: _usage("evaluation", "telemetry"), "T0"),
        ("evaluation usage measured", lambda: _usage("evaluation", "measured"), "T0"),
        ("artifact", lambda: make3("Artifact"), "T0"),
        ("metric result", lambda: make3("MetricResult"), "T0"),
    ],
)
def test_evidence_tier_reads_closed_provenance_fields(
    label: str, build: Any, expected: str
) -> None:
    """ADR 0004 Decision 2a (R2): the evidence tier is computed from the closed provenance fields
    only. Observer evidence is ``Transcript.source``, ``Turn.timing_source``, ``Exchange.source``,
    a ``TimelineOrigin.kind`` of a transcript or of a tool call, and ``ToolCallEvent.source`` equal
    to ``observer`` (T2). Telemetry evidence is the same fields at ``agent_telemetry`` or
    ``telemetry``, any ``StageSpan`` and an agent-under-test ``UsageRecord`` from telemetry (T1).
    Everything else is T0, in particular ``Turn.source`` (a pipeline stage, not a producer) and the
    evaluation domain's usage."""
    assert api("bundle").evidence_tier([build()]) == expected, label


@pytest.mark.parametrize(
    "stage",
    ["stt", "llm", "tts", "judge", "sim_caller_llm", "sim_caller_tts", "realtime", "other"],
)
def test_every_stage_span_is_telemetry_evidence(stage: str) -> None:
    """ADR 0004 Decision 2a: any ``StageSpan`` is telemetry evidence (T1), whatever its stage, its
    provider or its timing: a span carries a trace or span ID that only telemetry or an observer
    supplies (R1's premise)."""
    extra = {"stage_raw": "custom_stage"} if stage == "other" else {}
    span = make3("StageSpan", stage=stage, **extra)
    assert bundle_fn("evidence_tier")([span]) == "T1"
    bare = make3("StageSpan", stage=stage, provider=None, model=None, start=None, **extra)
    assert bundle_fn("evidence_tier")([bare]) == "T1"


def test_evidence_tier_of_many_records_is_the_highest() -> None:
    """The highest tier any record shows (O3: T2 if observer evidence, else T1 if telemetry
    evidence, else T0); the order of the records and their number do not matter; an empty
    collection and unrelated records are T0; any iterable is accepted and not consumed twice."""
    evidence_tier = bundle_fn("evidence_tier")
    t0, t1, t2 = _transcript(source="agent_export"), make3("StageSpan"), _call(source="observer")
    assert evidence_tier([]) == "T0"
    assert evidence_tier([make3("Artifact"), make3("Run", observed_tier="T0")]) == "T0"
    assert evidence_tier([t0, t1]) == "T1"
    assert evidence_tier([t1, t0, t2]) == "T2"
    assert evidence_tier([t2, t0]) == "T2"
    assert evidence_tier(iter([t0, t1, t0])) == "T1"
    assert evidence_tier(r for r in [t1, t1, t1]) == "T1"
    assert evidence_tier([t0] * 50 + [t1] + [t0] * 50) == "T1"


# ========================================================================== check_observed_tier


def _records_of(bundle: Any) -> list[Any]:
    return [
        bundle.run,
        *bundle.transcripts,
        *bundle.turns,
        *bundle.exchanges,
        *bundle.tool_calls,
        *bundle.spans,
        *bundle.usage_records,
    ]


def _run(tier: str) -> Any:
    return make3("Run", observed_tier=tier)


def test_check_observed_tier_function() -> None:
    """ADR 0004 Decision 2a (R2): ``check_observed_tier(run, records, *, withheld=())`` accepts a
    stored tier that equals the computed one and raises ``ValueError`` tagged ``[tier]`` for both an
    under-claim and an over-claim. The message names the stored and the computed tier and, when
    the computed tier comes from a record, the ID of one such record; it never quotes a value of a
    record. ``withheld`` is keyword-only."""
    check = bundle_fn("check_observed_tier")
    observer = _transcript(source="observer", id=new_id("tsc"))
    span = make3("StageSpan")
    quiet = _transcript(source="agent_export")
    secret_turn = make3(
        "Turn", text=f"my pin is {QUOTE_MARK}", timing_source="telemetry", start_ms=0, end_ms=5
    )
    for tier, records in (("T0", [quiet]), ("T1", [quiet, span]), ("T2", [observer, span, quiet])):
        check(_run(tier), records)
        check(_run(tier), iter(records))
    with pytest.raises(ValueError, match=r"\[tier\]") as under:
        check(_run("T0"), [quiet, span])
    assert "T0" in str(under.value)
    assert "T1" in str(under.value)
    assert str(span.id) in str(under.value)
    with pytest.raises(ValueError, match=r"\[tier\]") as under_two:
        check(_run("T1"), [quiet, span, observer])
    assert "T1" in str(under_two.value)
    assert "T2" in str(under_two.value)
    assert str(observer.id) in str(under_two.value)
    with pytest.raises(ValueError, match=r"\[tier\]") as under_zero:
        check(_run("T0"), [observer])
    assert "T0" in str(under_zero.value)
    assert "T2" in str(under_zero.value)
    for stored, records, computed in (
        ("T1", [quiet], "T0"),
        ("T2", [quiet], "T0"),
        ("T2", [quiet, span], "T1"),
        ("T1", [], "T0"),
    ):
        with pytest.raises(ValueError, match=r"\[tier\]") as over:
            check(_run(stored), records)
        assert stored in str(over.value)
        assert computed in str(over.value)
    with pytest.raises(ValueError, match=r"\[tier\]") as hidden:
        check(_run("T0"), [secret_turn])
    assert QUOTE_MARK not in str(hidden.value)
    with pytest.raises(TypeError):
        check(_run("T0"), [quiet], [])
    records = [quiet, span]
    before = [r.model_dump(mode="json") for r in records]
    check(_run("T1"), records)
    assert [r.model_dump(mode="json") for r in records] == before


@pytest.mark.parametrize("stored", ["T0", "T1", "T2"])
@pytest.mark.parametrize("evidence", ["T0", "T1", "T2"])
def test_bundle_observed_tier_equals_evidence_tier(stored: str, evidence: str) -> None:
    """SCOPE P1-14 (binding name), O3a, R2: a ``RunBundle`` accepts a stored ``observed_tier`` that
    equals the tier computed from its records, and rejects every other combination, an under-claim
    and an over-claim alike (``[tier]``), also inside an ``ExportBundle`` of any purpose. Records
    that are present and redacted or sealed still count."""
    doc = run_bundle_doc(tier=evidence)
    doc["run"]["observed_tier"] = stored
    if stored == "T0":
        doc["run"]["available_inputs"] = [k for k in doc["run"]["available_inputs"] if k != "spans"]
    if stored == evidence:
        load_run(doc)
        load_export(export_doc("internal", [doc]))
        return
    _reject_run(doc, "tier")
    assert_bundle_rejected(export_class(), export_doc("internal", [doc]), "tier")
    assert_bundle_rejected(
        export_class(),
        export_doc("shared_export", [doc], payload_redactions=protect_by_redaction(doc)),
        "tier",
    )


def _add_evidence(doc: dict[str, Any], source: str) -> str:
    """Turn the quiet T0 run bundle ``doc`` into one whose only provenance evidence is ``source``;
    returns the tier that evidence shows."""
    if source == "transcript_telemetry":
        doc["transcripts"][0]["source"] = "agent_telemetry"
        return "T1"
    if source == "transcript_observer":
        doc["transcripts"][0]["source"] = "observer"
        return "T2"
    if source in ("turn_telemetry", "turn_observer"):
        doc["transcripts"][0]["timeline_origin"] = _artifact_origin(doc["artifacts"][0]["id"])
        for seq, turn in enumerate(doc["turns"]):
            timing = "telemetry" if source == "turn_telemetry" else "observer"
            turn.update(timing_source=timing, start_ms=seq * 1000, end_ms=seq * 1000 + 800)
        return "T1" if source == "turn_telemetry" else "T2"
    if source in ("exchange_telemetry", "exchange_observer"):
        for exchange in doc["exchanges"]:
            exchange["source"] = source.removeprefix("exchange_")
        return "T1" if source == "exchange_telemetry" else "T2"
    if source in ("tool_call_telemetry", "tool_call_observer"):
        for call in doc["tool_calls"]:
            call["source"] = "agent_telemetry" if source.endswith("telemetry") else "observer"
        return "T1" if source == "tool_call_telemetry" else "T2"
    if source == "span":
        extra = make3("StageSpan").model_dump(mode="json", by_alias=True)
        doc["spans"] = [extra]
        doc["run"]["available_inputs"] = [*doc["run"]["available_inputs"], "spans"]
        return "T1"
    assert source == "agent_usage"
    template = copy.deepcopy(doc["usage_records"][0])
    template.update(id=new_id("use"), domain="agent_under_test", source="telemetry")
    doc["usage_records"] = [*doc["usage_records"], template]
    return "T1"


@pytest.mark.parametrize(
    "source",
    [
        "transcript_telemetry",
        "transcript_observer",
        "turn_telemetry",
        "turn_observer",
        "exchange_telemetry",
        "exchange_observer",
        "tool_call_telemetry",
        "tool_call_observer",
        "span",
        "agent_usage",
    ],
)
def test_each_kind_of_record_carries_the_tier_in_a_bundle(source: str) -> None:
    """ADR 0004 Decision 2a (R2): in a bundle, the evidence of every kind of record counts, one at
    a time: with the single provenance field of that kind set to telemetry or observer, the
    stored tier must be exactly the tier it shows. A stored tier one below is an under-claim and
    one above an over-claim (both ``[tier]``), and the record that carries the evidence is named
    for the under-claim."""
    doc = run_bundle_doc(tier="T0")
    shown = _add_evidence(doc, source)
    rank = {"T0": 0, "T1": 1, "T2": 2}
    for stored in ("T0", "T1", "T2"):
        candidate = copy.deepcopy(doc)
        candidate["run"]["observed_tier"] = stored
        if stored == "T0" and "spans" in candidate["run"]["available_inputs"]:
            candidate["run"]["available_inputs"] = [
                k for k in candidate["run"]["available_inputs"] if k != "spans"
            ]
        if stored == shown:
            load_run(candidate)
            continue
        error = _reject_run(candidate, "tier")
        if rank[stored] < rank[shown]:
            assert shown in str(error)
            assert stored in str(error)


def test_run_rule_r1_and_bundle_rule_r2_agree() -> None:
    """R1 stays on the Run record (a run whose ``available_inputs`` hold ``spans`` with
    ``observed_tier=T0`` is rejected at ``observed_tier``, a ``ValidationError`` of the Run itself)
    and R2 is the bundle's: a T1 run with its spans loads, and removing the spans from a T1 run
    whose other records are quiet is an over-claim of the bundle."""
    with pytest.raises(ValidationError) as caught:
        registered_class("Run").model_validate(
            run_doc(observed_tier="T0", available_inputs=["spans"])
        )
    assert any("observed_tier" in [str(p) for p in e["loc"]] for e in caught.value.errors())
    doc = run_bundle_doc(tier="T1")
    load_run(doc)
    quiet = copy.deepcopy(doc)
    quiet["spans"] = []
    quiet["exchanges"] = [dict(e, source="inferred_from_turns") for e in quiet["exchanges"]]
    quiet["tool_calls"] = [dict(c, source="agent_export") for c in quiet["tool_calls"]]
    quiet["usage_records"] = [
        u for u in quiet["usage_records"] if u["domain"] != "agent_under_test"
    ]
    quiet["results"][0]["evidence_refs"] = [
        e for e in quiet["results"][0]["evidence_refs"] if e["kind"] != "span"
    ]
    _reject_run(quiet, "tier")


# ======================================================================= withheld records (D3)


def _observer_in_turns() -> dict[str, Any]:
    """A T2 run whose only observer evidence is the timing of its turns (a sealed transcript text
    removes them), with a transcript that is otherwise quiet."""
    doc = run_bundle_doc(tier="T0")
    doc["run"]["observed_tier"] = "T2"
    art_id = doc["artifacts"][0]["id"]
    doc["transcripts"][0]["timeline_origin"] = _artifact_origin(art_id)
    for seq, turn in enumerate(doc["turns"]):
        turn.update(timing_source="observer", start_ms=seq * 1000, end_ms=seq * 1000 + 800)
    return doc


def _without_turns(doc: dict[str, Any], *, declare: bool) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    ids = [t["id"] for t in out["turns"]]
    out["turns"] = []
    out["results"][0]["evidence_refs"] = [
        e for e in out["results"][0]["evidence_refs"] if e["kind"] != "turn"
    ]
    for exchange in out["exchanges"]:
        exchange["source"] = "inferred_from_turns"
    out["withheld"] = [withheld_doc("turn", i) for i in ids] if declare else []
    return out


def test_export_declared_withheld_records_and_observed_tier() -> None:
    """SCOPE P1-14 (binding name), O3a and D3 of api-surface 22.12. Withholding the turns (a sealed
    transcript text) removes the only observer evidence of a T2 run. Without a declaration R2 is
    strict: ``[tier]``. With the turns declared withheld, in the run bundle of an export of any
    purpose, the over-claim is excused and the export is accepted; the declaration is serialized,
    names kinds and IDs only, and the bundle still holds none of the declared records."""
    base = _observer_in_turns()
    load_run(base)
    undeclared = _without_turns(base, declare=False)
    _reject_run(undeclared, "tier")
    declared = _without_turns(base, declare=True)
    bundle = load_run(declared)
    assert [w.kind for w in bundle.withheld] == ["turn"] * 4
    assert {str(w.record_id) for w in bundle.withheld} == {t["id"] for t in base["turns"]}
    assert bundle.turns == []
    tsc_id = base["transcripts"][0]["id"]
    for purpose in ("internal", "shared_export", "benchmark"):
        rest = [e for e in protect_by_redaction(declared) if e["subject_id"] != tsc_id]
        extra: dict[str, Any] = {}
        if purpose != "internal":
            extra = {
                "sealed_payloads": [sealed_doc("transcript_text", tsc_id)],
                "payload_redactions": rest,
            }
        export = load_export(export_doc(purpose, [declared], **extra))
        dumped = export.model_dump(mode="json", by_alias=True)["run_bundles"][0]["withheld"]
        assert all(set(entry) == {"kind", "record_id"} for entry in dumped)
    # the declaration is per run: another run bundle of the export gets no excuse from it
    other = _without_turns(_observer_in_turns(), declare=False)
    mixed = export_doc("internal", [declared, other])
    assert_bundle_rejected(export_class(), mixed, "tier")


@pytest.mark.parametrize("kind", PROVENANCE_KINDS)
def test_declaration_of_a_provenance_kind_excuses_an_over_claim(kind: str) -> None:
    """D3: an over-claim is accepted when the declaration covers a record kind that can carry the
    missing evidence (transcript, turn, exchange, tool call, stage span, usage record). Pinned for
    the over-claim of T1 where all six kinds can carry the evidence; the four kinds that can carry
    observer evidence also excuse a T2 over-claim (see the next test)."""
    doc = run_bundle_doc(tier="T0")
    doc["run"]["observed_tier"] = "T1"
    _reject_run(doc, "tier")
    doc["withheld"] = [_withheld(kind)]
    bundle = load_run(doc)
    assert bundle.withheld[0].kind == kind


@pytest.mark.parametrize("kind", ["transcript", "turn", "exchange", "tool_call"])
def test_declaration_of_an_observer_capable_kind_excuses_a_t2_over_claim(kind: str) -> None:
    """D3, T2: a T2 claim over quiet records is excused by a declared transcript, turn, exchange or
    tool call (each can carry observer provenance fields), and by none of the non-provenance
    kinds."""
    doc = run_bundle_doc(tier="T0")
    doc["run"]["observed_tier"] = "T2"
    _reject_run(doc, "tier")
    doc["withheld"] = [_withheld(kind)]
    load_run(doc)


@pytest.mark.parametrize("kind", OTHER_KINDS)
def test_declaration_of_another_kind_does_not_excuse_an_over_claim(kind: str) -> None:
    """D3: a declared artifact alone (and a declared result or cost record, which carry no
    provenance field) does not excuse an over-claim, whatever the stored tier."""
    for stored in ("T1", "T2"):
        doc = run_bundle_doc(tier="T0")
        doc["run"]["observed_tier"] = stored
        doc["withheld"] = [_withheld(kind)]
        _reject_run(doc, "tier")
    doc = run_bundle_doc(tier="T0")
    doc["run"]["observed_tier"] = "T1"
    doc["withheld"] = [_withheld(kind), _withheld("turn")]
    load_run(doc)  # one covering kind among them is enough


@pytest.mark.parametrize("kind", PROVENANCE_KINDS)
def test_an_under_claim_is_never_excused(kind: str) -> None:
    """D3: withholding cannot remove evidence that is present, so a stored tier below the computed
    one is rejected whatever is declared. And a declaration never raises the computed tier: the
    same bundle with and without the declaration computes the same tier, so an over-claim excused
    by it is not an under-claim of anything else."""
    t1 = run_bundle_doc(tier="T1")
    t1["run"]["observed_tier"] = "T0"
    t1["run"]["available_inputs"] = [k for k in t1["run"]["available_inputs"] if k != "spans"]
    t1["withheld"] = [_withheld(kind)]
    _reject_run(t1, "tier")
    t2 = run_bundle_doc(tier="T2")
    t2["run"]["observed_tier"] = "T1"
    t2["withheld"] = [_withheld(kind), _withheld("transcript")]
    _reject_run(t2, "tier")
    clean = run_bundle_doc(tier="T1")
    declared = copy.deepcopy(clean)
    declared["withheld"] = [_withheld(kind)]
    evidence_tier = bundle_fn("evidence_tier")
    first, second = load_run(clean), load_run(declared)
    assert evidence_tier(_records_of(first)) == evidence_tier(_records_of(second)) == "T1"


def test_check_observed_tier_withheld_argument() -> None:
    """The function form of the declaration: ``check_observed_tier(run, records, withheld=[...])``
    takes ``WithheldRecord`` objects. A covering kind excuses an over-claim, an artifact does not,
    an under-claim is rejected whatever is declared, and an empty declaration is the strict rule."""
    check = bundle_fn("check_observed_tier")
    withheld = registered_class("WithheldRecord")
    quiet = _transcript(source="agent_export")
    span = make3("StageSpan")

    def declaration(kind: str) -> Any:
        return withheld.model_validate({"kind": kind, "record_id": new_id(KIND_PREFIX[kind])})

    with pytest.raises(ValueError, match=r"\[tier\]"):
        check(_run("T2"), [quiet])
    with pytest.raises(ValueError, match=r"\[tier\]"):
        check(_run("T2"), [quiet], withheld=[])
    check(_run("T2"), [quiet], withheld=[declaration("turn")])
    check(_run("T1"), [quiet], withheld=(declaration("tool_call"),))
    with pytest.raises(ValueError, match=r"\[tier\]"):
        check(_run("T2"), [quiet], withheld=[declaration("artifact")])
    with pytest.raises(ValueError, match=r"\[tier\]"):
        check(_run("T0"), [quiet, span], withheld=[declaration("span"), declaration("turn")])


def test_withheld_declaration_must_be_true_and_unique() -> None:
    """D3 ("names record kinds and IDs", explicit): a record that is declared withheld must not be
    in the bundle (``[withheld]``, naming the ID), and a record is declared once. A declaration of
    a record that is not in the bundle is accepted whether or not any record needs it."""
    doc = run_bundle_doc(tier="T1")
    present = doc["turns"][0]["id"]
    bad = copy.deepcopy(doc)
    bad["withheld"] = [withheld_doc("turn", present)]
    error = _reject_run(bad, "withheld")
    assert present in str(error)
    twice = copy.deepcopy(doc)
    entry = _withheld("exchange")
    twice["withheld"] = [entry, copy.deepcopy(entry)]
    _reject_run(twice, "withheld")
    fine = copy.deepcopy(doc)
    fine["withheld"] = [_withheld("turn"), _withheld("artifact"), _withheld("cost_record")]
    assert len(load_run(fine).withheld) == 3
    artifact_present = copy.deepcopy(doc)
    artifact_present["withheld"] = [withheld_doc("artifact", doc["artifacts"][0]["id"])]
    _reject_run(artifact_present, "withheld")
    # the declaration can sit in an internal RunBundle, outside any export
    assert load_run(fine).withheld[0].kind == "turn"
    # an unrelated ID kind in the entry is a record error, not a bundle rule
    with pytest.raises(ValidationError):
        run_class().model_validate(
            {**doc, "withheld": [{"kind": "turn", "record_id": doc["run"]["id"]}]}
        )


@pytest.mark.parametrize(
    ("kind", "placement"),
    [
        ("turn", "evidence"),
        ("exchange", "evidence"),
        ("tool_call", "evidence"),
        ("span", "evidence"),
        ("artifact", "evidence"),
        ("transcript", "turn_transcript"),
        ("turn", "artifact_turn_ref"),
        ("artifact", "channel_map"),
        ("transcript", "declared_language"),
        ("usage_record", "cost_usage"),
    ],
)
def test_withheld_records_excuse_references_to_them(kind: str, placement: str) -> None:
    """D3 (a bundle that withholds a record cannot also resolve references to it): a reference by ID
    to a record that the declaration names is not ``[unresolved]``; the same reference without the
    declaration is. Evidence references, a turn's transcript, an artifact's ``turn_ref``, a channel
    map's artifact, a result's declared-language transcripts and a cost record's usage records."""
    prefix = KIND_PREFIX[kind]
    gone = new_id(prefix)
    doc = run_bundle_doc(tier="T1", declared=(placement == "declared_language"))
    if placement == "evidence":
        want = {"turn": 0, "exchange": 1, "tool_call": 2, "artifact": 3, "span": 4}[kind]
        doc["results"][0]["evidence_refs"][want]["ref_id"] = gone
    elif placement == "turn_transcript":
        doc["turns"][0]["transcript_id"] = gone
    elif placement == "artifact_turn_ref":
        doc["artifacts"][0]["turn_ref"] = gone
    elif placement == "channel_map":
        doc["run"]["channel_maps"][0]["artifact_id"] = gone
    elif placement == "declared_language":
        doc["results"][0]["declared_language_transcript_ids"] = [gone]
    else:
        doc["cost_records"][0]["usage_record_ids"] = [gone]
    _reject_run(doc, "unresolved")
    doc["withheld"] = [withheld_doc(kind, gone)]
    load_run(doc)


# ==================================================================== sealed and redacted records


def test_sealed_and_redacted_records_count_for_the_tier() -> None:
    """P (contract B3): a redacted record keeps its provenance fields readable (redaction covers
    payload content), so it counts like any record; a record that a ``SealedPayload`` seals keeps
    its metadata record in the export (the artifact, the transcript, the tool call) and counts too,
    and only its removed children (the turns of a sealed text) need a declaration. A T2 run whose
    observer evidence is the observer transcript is accepted in a shared export with that transcript
    redacted, and with its text sealed and its turns declared."""
    rb = run_bundle_doc(tier="T2")
    tsc_id = rb["transcripts"][0]["id"]
    assert rb["transcripts"][0]["source"] == "observer"
    for purpose in ("shared_export", "benchmark"):
        load_export(export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb)))
    stripped = copy.deepcopy(rb)
    ids = [t["id"] for t in stripped["turns"]]
    stripped["turns"] = []
    stripped["results"][0]["evidence_refs"] = [
        e for e in stripped["results"][0]["evidence_refs"] if e["kind"] != "turn"
    ]
    rest = [e for e in protect_by_redaction(stripped) if e["subject_id"] != tsc_id]
    sealed = [sealed_doc("transcript_text", tsc_id)]
    no_declaration = export_doc(
        "shared_export", [stripped], sealed_payloads=sealed, payload_redactions=rest
    )
    # T-B13: the observer transcript is still there (T2 holds) but its turns are gone and
    # undeclared, so the scored results read nothing: the turns must be declared withheld
    with pytest.raises(ValidationError, match=r"\[applicability\]"):
        load_export(no_declaration)
    stripped["withheld"] = [withheld_doc("turn", i) for i in ids]
    declared = export_doc(
        "shared_export", [stripped], sealed_payloads=sealed, payload_redactions=rest
    )
    assert len(load_export(declared).run_bundles[0].withheld) == 4
    only_redaction = export_doc(
        "shared_export", [rb], payload_redactions=[redaction_entry(tsc_id, ["text"])]
    )
    assert_bundle_rejected(export_class(), only_redaction, "unprotected")
