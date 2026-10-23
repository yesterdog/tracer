# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 7 tests (P1-14 RunBundle, ExportBundle and the export rules, and
the export half of P1-27). No tests here.

The implementation is imported lazily through ``api()`` (see ``_support``), so a class or function
that the architect has not written yet fails the test that needs it and never breaks collection.
The pinned names, fields, message tags and readings are in ``docs/phase1/api-surface-bundle.md``.

Documents are JSON-mode dicts. ``run_bundle_doc()`` returns a complete, consistent ``RunBundle``
document of one small run (a snapshot with two metrics, an audio artifact with a stereo channel
map, a transcript with four turns, two exchanges, two tool calls, two stage spans, two results, a
verdict, usage, cost records and a cost summary); ``export_doc()`` wraps run bundle documents in an
``ExportBundle`` document. The helpers validate the pieces they build with the real batch 1 to 5
models, so a defect in a bundle test is never a defect of the fixture.

Tiers. ``tier="T1"`` (the default) shows telemetry evidence only (exchange source telemetry, tool
calls from ``agent_telemetry``, stage spans, agent usage from telemetry); ``"T0"`` shows none of
it; ``"T2"`` adds an observer transcript. ``origin="human"`` is a recorded run with a human caller
(every payload is human-subject data); ``origin="simulated"`` is a simulated run whose roles are
caller and agent only (derived false).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import error_texts, make, meta, new_id, registered_class
from ._support_b2 import seal as seal_entity
from ._support_b3 import (
    QUOTE_MARK,
    artifact_doc,
    channel_map_doc,
    exchange_doc,
    judge_info_doc,
    privacy_human,
    privacy_synthetic,
    run_doc,
    scorer_info,
    snapshot_doc,
    snapshot_parts,
    span_doc,
    tool_call_doc,
    transcript_doc,
    turn_doc,
)
from ._support_b4 import (
    make_catalog,
    record_computed_doc,
    record_reported_doc,
    summary_doc,
    usage_doc,
    usd,
)
from ._support_b5 import DIGEST_KEY, DIGEST_KEY_REF, KEYED, PLAIN, redaction_doc
from ._support_b5 import seal as seal_payload

__all__ = [
    "DIGEST_KEY",
    "DIGEST_KEY_REF",
    "EXPORT_PURPOSES",
    "KEYED",
    "PLAIN",
    "PROFILE_KEY",
    "PROFILE_KEY_REF",
    "QUOTE_MARK",
    "RunWorld",
    "assert_bundle_rejected",
    "assert_error_free_of",
    "bundle_fn",
    "by_kind",
    "dataset_doc",
    "dataset_item_doc",
    "drop_nulls",
    "export_class",
    "export_doc",
    "keyed_profile_digest",
    "load_export",
    "load_run",
    "plain_profile_digest",
    "protect_by_redaction",
    "redaction_entry",
    "replace_privacy",
    "resnap",
    "run_bundle_doc",
    "run_class",
    "sealed_doc",
    "shared_ready",
    "withheld_doc",
    "world",
]

EXPORT_PURPOSES = ("internal", "shared_export", "benchmark")
NON_INTERNAL = ("shared_export", "benchmark")

# A fixed HMAC key for the profile digests of shared exports. Test data, not a secret; it appears
# in no fixture of the package and never in an exported document.
PROFILE_KEY = b"tracer-test-profile-hmac-key-0000"
PROFILE_KEY_REF: dict[str, Any] = {"key_id": "profile-hmac-test-1"}


# ------------------------------------------------------------------------------------ lookups


def run_class() -> Any:
    return api("bundle").RunBundle


def export_class() -> Any:
    return api("bundle").ExportBundle


def bundle_fn(name: str) -> Any:
    return getattr(api("bundle"), name)


def load_run(doc: Mapping[str, Any]) -> Any:
    return run_class().model_validate(doc)


def load_export(doc: Mapping[str, Any]) -> Any:
    return export_class().model_validate(doc)


# --------------------------------------------------------------------------------- digests


def plain_profile_digest(profile: Any) -> dict[str, Any]:
    """The plain ``sha256-jcs/v1`` digest document of a validated ``TestProfile``."""
    dumped: dict[str, Any] = profile.content_digest.model_dump(mode="json", exclude_none=True)
    return dumped


def keyed_profile_digest(profile: Any) -> dict[str, Any]:
    """The keyed (HMAC) digest document of a validated ``TestProfile``: the shared-export form."""
    digest_api = api("digest")
    key_ref = digest_api.KeyRef.model_validate(dict(PROFILE_KEY_REF))
    digest = digest_api.compute_digest(
        profile.model_dump(mode="json", by_alias=True),
        scheme=KEYED,
        key=PROFILE_KEY,
        key_ref=key_ref,
    )
    dumped: dict[str, Any] = digest.model_dump(mode="json", exclude_none=True)
    return dumped


# ---------------------------------------------------------------------------- the run world


class RunWorld:
    """A ``RunBundle`` document and the validated pieces it was built from."""

    def __init__(self, doc: dict[str, Any], parts: dict[str, Any], catalog: Any) -> None:
        self.doc = doc
        self.parts = parts
        self.catalog = catalog

    def copy(self) -> dict[str, Any]:
        return copy.deepcopy(self.doc)


def _marker(origin: str, marker: Mapping[str, Any] | None) -> dict[str, Any]:
    if marker is not None:
        return dict(marker)
    return privacy_human() if origin == "human" else privacy_synthetic()


def world(
    *,
    tier: str = "T1",
    origin: str = "human",
    with_profile: bool = False,
    profile_digest: str | None = None,
    with_cost: bool = True,
    declared: bool = False,
    unknown_turn: bool = False,
    marker: Mapping[str, Any] | None = None,
    **over: Any,
) -> RunWorld:
    """A consistent run. ``profile_digest`` is None (the reference has none), ``"plain"`` or
    ``"keyed"``. ``marker`` replaces the privacy marker of the artifact and the transcript.
    ``declared`` makes the transcript an untagged import with a user-declared language and the
    first result use it (N14). ``unknown_turn`` makes the second turn's role ``unknown``. ``over``
    replaces top-level members of the ``RunBundle`` document."""
    assert tier in ("T0", "T1", "T2"), tier
    parts = snapshot_parts(with_profile=with_profile)
    profile = parts["profile"]
    if with_profile and profile_digest is not None:
        digest = (
            plain_profile_digest(profile)
            if profile_digest == "plain"
            else keyed_profile_digest(profile)
        )
        # The evaluator's own digest ignores the profile reference's digest (N20), so adding it
        # leaves the embedded evaluator valid.
        evaluator = parts["evaluator"].model_dump(mode="json", by_alias=True)
        evaluator["test_profile"]["content_digest"] = digest
        parts["evaluator"] = registered_class("Evaluator").model_validate(evaluator)
    catalog = make_catalog() if with_cost else None
    snap_over: dict[str, Any] = {}
    if catalog is not None:
        snap_over["pricing_catalog"] = {
            "id": str(catalog.id),
            "version": catalog.version,
            "content_digest": catalog.content_digest.model_dump(mode="json", exclude_none=True),
        }
    snap = snapshot_doc(parts, **snap_over)

    mark = _marker(origin, marker)
    art_id, tsc_id = new_id("art"), new_id("tsc")
    spans_on = tier in ("T1", "T2")
    run = run_doc(
        mode="recorded" if origin == "human" else "simulated",
        caller_origin="human" if origin == "human" else "simulated",
        config_snapshot_id=snap["id"],
        observed_tier=tier,
        available_inputs=[
            "audio",
            "agent_audio",
            "transcript",
            "tool_calls",
            "tool_results",
            "usage",
            *(["spans"] if spans_on else []),
        ],
        channel_maps=[channel_map_doc(art_id)],
        started_at="2026-10-22T09:59:00.000000Z",
    )
    artifact = artifact_doc(id=art_id, privacy=mark)
    transcript_source = (
        "observer" if tier == "T2" else ("agent_export" if tier == "T0" or declared else "our_stt")
    )
    transcript = transcript_doc(id=tsc_id, source=transcript_source, privacy=mark)
    if declared:
        transcript["source"] = "agent_export"
        transcript["declared_language"] = {
            "language": "mr-IN",
            "source": "user_declared",
            "declared_by": "cli",
            "declared_at": "2026-10-23T08:00:00.000000Z",
        }
    exchange_source = {"T0": "inferred_from_turns", "T1": "telemetry", "T2": "telemetry"}[tier]
    exchanges = [exchange_doc(0, source=exchange_source), exchange_doc(1, source=exchange_source)]
    roles = ["caller", "agent", "caller", "agent"]
    if unknown_turn:
        roles[2] = "unknown"
    turns = [
        turn_doc(
            tsc_id,
            seq,
            role=role,
            source="agent_stt" if role != "agent" else "agent_llm_output",
            exchange_index=seq // 2,
            text=f"turn {seq} text",
        )
        for seq, role in enumerate(roles)
    ]
    call_source = "agent_export" if tier == "T0" else "agent_telemetry"
    calls = [
        tool_call_doc(seq=0, source=call_source),
        tool_call_doc(seq=1, source=call_source, name="issue_refund"),
    ]
    spans = [span_doc(stage="stt"), span_doc(stage="llm", model="gpt-4.1")] if spans_on else []

    definitions = parts["definitions"]
    bindings = parts["bindings"]
    evidence = [
        {"kind": "turn", "ref_id": turns[1]["id"]},
        {"kind": "exchange", "ref_id": exchanges[0]["id"]},
        {"kind": "tool_call", "ref_id": calls[0]["id"]},
        {"kind": "artifact_range", "ref_id": art_id, "start_ms": 100, "end_ms": 900},
    ]
    if spans:
        evidence.append({"kind": "span", "ref_id": spans[0]["id"]})
    res_judge = {
        **meta("res"),
        "metric": {
            "id": str(definitions[0].id),
            "version": definitions[0].version,
            "content_digest": definitions[0].content_digest.model_dump(
                mode="json", exclude_none=True
            ),
        },
        "status": "scored",
        "value": True,
        "passed": True,
        "judge": judge_info_doc(),
        "language_source": "declared" if declared else "observed",
        "declared_language_transcript_ids": [tsc_id] if declared else [],
        "evidence_refs": evidence,
    }
    res_latency = {
        **meta("res"),
        "metric": {"id": str(definitions[1].id), "version": definitions[1].version},
        "status": "scored",
        "value": 420,
        "passed": True,
        "scorer": scorer_info(),
        "language_source": "observed",
    }
    verdict_api = api("verdict")
    weighted = verdict_api.compute_weighted_score([(bindings[1].id, "5", "passed")])
    verdict = {
        **meta("vrd"),
        "required_total": 2,
        "passed": 2,
        "failed": 0,
        "not_applicable": 0,
        "errored": 0,
        "status": "pass",
        "checks": [
            {
                "binding_id": str(bindings[0].id),
                "metric_ref": {"id": str(definitions[0].id), "version": definitions[0].version},
                "outcome": "passed",
                "result_id": res_judge["id"],
            },
            {
                "binding_id": str(bindings[1].id),
                "metric_ref": {"id": str(definitions[1].id), "version": definitions[1].version},
                "outcome": "passed",
                "result_id": res_latency["id"],
            },
        ],
        "weighted_score": weighted.score,
        "weight_coverage": weighted.coverage,
        "weighted_excluded": [],
    }

    usage: list[dict[str, Any]] = []
    costs: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    run_id = run["id"]
    if with_cost:
        judge_usage = usage_doc(run_id=run_id)
        calc_usage = usage_doc(
            run_id=run_id,
            stage="sim_caller_llm",
            quantities=[{"unit": "input_tokens", "count": 1_000_000}],
        )
        usage = [judge_usage, calc_usage]
        reported = record_reported_doc(usage_record_ids=[judge_usage["id"]])
        computed = record_computed_doc(
            catalog, usage_record_ids=[calc_usage["id"]], amount=usd("0.15")
        )
        costs = [reported, computed]
        summaries = [
            summary_doc(
                total=usd("1.65"),
                included=[reported["id"], computed["id"]],
                excluded=[],
                completeness="complete",
            )
        ]
    if tier in ("T1", "T2"):
        usage.append(
            usage_doc(
                run_id=run_id,
                domain="agent_under_test",
                stage="llm",
                source="telemetry",
                quantities=[{"unit": "input_tokens", "count": 500}],
            )
        )

    doc: dict[str, Any] = {
        **meta("rbnd"),
        "run": run,
        "snapshot": snap,
        "pricing_catalogs": [catalog.model_dump(mode="json", by_alias=True)] if catalog else [],
        "test_profiles": [],
        "artifacts": [artifact],
        "transcripts": [transcript],
        "turns": turns,
        "exchanges": exchanges,
        "tool_calls": calls,
        "spans": spans,
        "results": [res_judge, res_latency],
        "verdict": verdict,
        "usage_records": usage,
        "cost_records": costs,
        "cost_summaries": summaries,
        "withheld": [],
    }
    doc.update(over)
    return RunWorld(doc, parts, catalog)


def resnap(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The snapshot document with its ``content_digest`` recomputed after an edit (the digest of a
    ``ConfigSnapshot`` covers its pricing catalog reference, so editing one needs this)."""
    body = {k: v for k, v in snapshot.items() if k != "content_digest"}
    digest = api("snapshot").compute_snapshot_digest(body)
    return {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}


def run_bundle_doc(**kwargs: Any) -> dict[str, Any]:
    """The ``RunBundle`` document of ``world(**kwargs)``."""
    return world(**kwargs).doc


# ----------------------------------------------------------------------- export documents


def export_doc(
    purpose: str, run_bundles: Sequence[Mapping[str, Any]] | None = None, **over: Any
) -> dict[str, Any]:
    """An ``ExportBundle`` document. ``run_bundles`` defaults to one human-caller run."""
    bundles = [
        copy.deepcopy(dict(b))
        for b in (run_bundles if run_bundles is not None else [run_bundle_doc()])
    ]
    doc: dict[str, Any] = {
        **meta("exp"),
        "purpose": purpose,
        "run_bundles": bundles,
        "datasets": [],
        "dataset_items": [],
        "sealed_payloads": [],
        "payload_redactions": [],
        "provenance_redactions": [],
    }
    doc.update(over)
    return doc


def redaction_entry(subject_id: str, covers: Sequence[str], **over: Any) -> dict[str, Any]:
    """A ``PayloadRedaction`` document: the redaction of the payload of one record."""
    return {"subject_id": subject_id, "redaction": redaction_doc(covers=list(covers), **over)}


def protect_by_redaction(rb: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Redaction entries that cover every payload of a run bundle: the audio of each artifact, the
    text of each transcript, the arguments and the result of each tool call."""
    entries = [redaction_entry(a["id"], ["audio"]) for a in rb["artifacts"]]
    entries += [redaction_entry(t["id"], ["text"]) for t in rb["transcripts"]]
    entries += [
        redaction_entry(c["id"], ["tool_arguments", "tool_result"]) for c in rb["tool_calls"]
    ]
    return entries


def shared_ready(purpose: str = "shared_export", **world_kwargs: Any) -> dict[str, Any]:
    """An accepted export of a human-caller run whose payloads are all redacted. Used as the base
    of tests that change one thing."""
    rb = run_bundle_doc(**world_kwargs)
    return export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))


def sealed_doc(payload_type: str, subject_id: str, **kwargs: Any) -> dict[str, Any]:
    """A really sealed ``SealedPayload`` document for the subject (see ``_support_b5.seal``)."""
    if payload_type == "audio":
        plaintext = bytes(range(256)) * 2
    else:
        plaintext = json.dumps({"k": "v"}).encode("utf-8")
    return seal_payload(plaintext, payload_type=payload_type, subject_id=subject_id, **kwargs)


def withheld_doc(kind: str, record_id: str) -> dict[str, Any]:
    return {"kind": kind, "record_id": record_id}


def replace_privacy(rb: dict[str, Any], marker: Mapping[str, Any]) -> dict[str, Any]:
    """The run bundle document with ``marker`` on its artifact and its transcript."""
    out = copy.deepcopy(rb)
    for record in (*out["artifacts"], *out["transcripts"]):
        record["privacy"] = dict(marker)
    return out


def by_kind(rb: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    """The list of records of a run bundle document by field name (``turns``, ``tool_calls``...)."""
    found: list[dict[str, Any]] = rb[name]
    return found


def dataset_item_doc(**over: Any) -> dict[str, Any]:
    """A ``DatasetItem`` document without a payload reference and without a marker (a marker is
    required once ``artifact_id`` or ``transcript_id`` is given: pass ``privacy=``)."""
    doc: dict[str, Any] = {**meta("dti"), "language": "hi-IN"}
    doc.update(over)
    return doc


def dataset_doc(item_ids: Sequence[str], **over: Any) -> dict[str, Any]:
    """A sealed ``Dataset`` (version 1) listing ``item_ids``."""
    model = registered_class("Dataset")
    body = {**meta("dts"), "version": 1, "name": "Refund calls", "item_ids": list(item_ids), **over}
    return seal_entity(model, body)


# ---------------------------------------------------------------------------- assertions


def drop_nulls(value: Any) -> Any:
    """``value`` without the object members whose value is None, at any depth (the null rule of the
    digest domain, ADR 0006 Decision 5). A dump with ``exclude_none=False`` and the document it
    came from are equal after this."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def _error_text(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        return "\n".join(error_texts(error))
    return str(error)


def assert_bundle_rejected(
    model: Any, doc: Mapping[str, Any], *tags: str, typed: str | None = None
) -> BaseException:
    """``doc`` is rejected by ``model``. Without ``typed`` the error is a ``ValidationError`` whose
    text holds every bracketed tag in ``tags`` (api-surface-bundle.md section 3). With ``typed``
    the error is the typed error of that name from ``tracer_evalschema.errors`` (or a subclass),
    raised unwrapped. The text is returned for further assertions."""
    if typed is not None:
        error_type = getattr(api("errors"), typed)
        with pytest.raises(error_type) as caught_typed:
            model.model_validate(doc)
        assert not isinstance(caught_typed.value, ValidationError), typed
        raised: BaseException = caught_typed.value
        return raised
    with pytest.raises(ValidationError) as caught:
        model.model_validate(doc)
    text = _error_text(caught.value)
    for tag in tags:
        assert f"[{tag}]" in text, f"missing tag [{tag}] in: {text[:600]}"
    return caught.value


def assert_error_free_of(error: BaseException, *needles: str) -> None:
    """None of ``needles`` appears in any rendering of the error that code might log."""
    texts = [str(error), repr(error)]
    if isinstance(error, ValidationError):
        texts += error_texts(error)
        texts.append(error.json(include_input=False))
    for needle in needles:
        for text in texts:
            assert needle not in text, f"a rejected value is shown: {needle!r}"


_UNUSED: tuple[Callable[..., Any], ...] = (make,)
