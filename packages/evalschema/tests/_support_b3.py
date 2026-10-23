# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 3 tests (P1-10a..c, P1-11, P1-12, P1-13, P1-22, P1-27). No tests.

The implementation modules are imported lazily through ``api()`` (see ``_support``), so a module
the architect has not written yet fails the test that needs it and never breaks collection. The
module, class and function names are listed in docs/phase1/api-surface.md, section 22.

Documents are JSON-mode dicts. ``doc3()`` returns a minimal valid document for one batch 3 entity;
``make3()`` validates it; ``rejected3()`` checks that a changed document is rejected. Versioned and
digested entities are sealed with ``_support_b2.seal`` and ``snapshot_doc`` (which uses the
``snapshot`` module's own digest function, as that function is part of the contract under test).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import CURRENT_SCHEMA_VERSION, api
from ._support_b2 import (
    EXCLUDED_LANGUAGE_TAGS,
    JUDGE_TEMPLATE,
    PLACEHOLDER_DIGEST,
    build,
    error_texts,
    judge_definition,
    latency_definition,
    make,
    meta,
    model_ref,
    new_id,
    ref,
    registered_class,
    seal,
)

__all__ = [
    "AWS_KEY",
    "DEVANAGARI",
    "ENTITY_SPECS3",
    "OTHER_LABEL_RAW",
    "OTHER_TAG",
    "PASTED_KEY",
    "QUOTE_MARK",
    "VERSIONED_NAMES3",
    "applicability_world",
    "artifact_doc",
    "assert_input_hidden",
    "assert_rejected3",
    "audio_format",
    "channel_map_doc",
    "cls3",
    "doc3",
    "entity_class3",
    "evidence_ref_doc",
    "exchange_doc",
    "ingest_warning_doc",
    "judge_info_doc",
    "language_declaration",
    "make3",
    "marked_id",
    "marked_key",
    "marked_set",
    "marked_tag",
    "meta",
    "new_id",
    "per_turn_summary_doc",
    "privacy_human",
    "privacy_synthetic",
    "result_doc",
    "run_doc",
    "segment_doc",
    "snapshot_doc",
    "snapshot_parts",
    "span_doc",
    "tool_call_doc",
    "transcript_doc",
    "turn_doc",
    "verdict_world",
]

# A credential-shaped value, assembled so that no test file contains a secret-looking literal.
PASTED_KEY = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz0123456789"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
# An observed tag and a raw label outside the nine configured languages, assembled from parts so
# that no test file spells an excluded language (the T-DRIFT matcher, ADR 0005 Decision 10).
OTHER_TAG = EXCLUDED_LANGUAGE_TAGS[0] + "-IN"
OTHER_LABEL_RAW = EXCLUDED_LANGUAGE_TAGS[0] + "_IN"
# Non-ASCII text for tests, spelled with code points so the files stay ASCII.
DEVANAGARI = "".join(chr(cp) for cp in (0x0928, 0x092E, 0x0938, 0x094D, 0x0924, 0x0947))

# A unique token for the no-echo tests: letters only, so it is valid text in every free-text field
# and invalid inside the identifier, key, closed-set, language-tag and decimal seeds below.
QUOTE_MARK = "Zq7xMark"


def marked_id(prefix: str) -> str:
    """A malformed typed ID of kind ``prefix`` that contains the marker."""
    return f"{prefix}_{QUOTE_MARK}"


def marked_key() -> str:
    """A value that fails every MachineKey, MetricKey and implementation_ref pattern."""
    return "bad key " + QUOTE_MARK


def marked_tag() -> str:
    """A malformed BCP-47 tag (an underscore is not a subtag separator) with the marker."""
    return "hi_" + QUOTE_MARK


def marked_set() -> str:
    """A string outside every closed set, with the marker."""
    return "no_such_" + QUOTE_MARK


# (module, class name, id prefix, versioned) of every entity of batch 3. ConfigSnapshot is a
# digested record of fact (ADR 0009 Decision 5): not versioned, but it carries a content_digest.
ENTITY_SPECS3: tuple[tuple[str, str, str, bool], ...] = (
    ("artifact", "Artifact", "art", False),
    ("transcript", "Transcript", "tsc", False),
    ("transcript", "Turn", "turn", False),
    ("transcript", "Exchange", "exc", False),
    ("events", "ToolCallEvent", "tcl", False),
    ("events", "StageSpan", "spn", False),
    ("results", "MetricResult", "res", False),
    ("verdict", "RunVerdict", "vrd", False),
    ("snapshot", "ConfigSnapshot", "snap", False),
    ("run", "Run", "run", False),
    ("dataset", "Dataset", "dts", True),
    ("dataset", "DatasetItem", "dti", False),
)
VERSIONED_NAMES3 = tuple(spec[1] for spec in ENTITY_SPECS3 if spec[3])


def cls3(module: str, name: str) -> Any:
    return getattr(api(module), name)


def entity_class3(name: str) -> Any:
    for module, class_name, _prefix, _versioned in ENTITY_SPECS3:
        if class_name == name:
            return cls3(module, class_name)
    raise KeyError(name)


# ----------------------------------------------------------------------------- privacy markers


def privacy_human(**over: Any) -> dict[str, Any]:
    """A human-subject marker with the consent and retention that P-46 requires."""
    return {
        "human_subject": True,
        "consent": {
            "status": "obtained",
            "reference": "consent-form-2026-10-01",
            "recorded_at": "2026-10-01T09:00:00.000000Z",
        },
        "retention": {
            "retain_until": "2027-10-01T00:00:00.000000Z",
            "policy_ref": "policy:retain-12-months",
            "delete_on_expiry": True,
        },
        **over,
    }


def privacy_synthetic(**over: Any) -> dict[str, Any]:
    """A marker for data with no human subject: no consent, no retention (P-46)."""
    return {"human_subject": False, **over}


# ----------------------------------------------------------------------- artifacts, channel maps


def audio_format(**over: Any) -> dict[str, Any]:
    return {
        "container": "wav",
        "codec": "pcm_s16le",
        "sample_rate_hz": 16000,
        "channels": 2,
        "bits_per_sample": 16,
        **over,
    }


def artifact_doc(**over: Any) -> dict[str, Any]:
    return {
        **meta("art"),
        "uri": "https://recordings.example.com/calls/2026/10/call-0001.wav",
        "sha256": "ab" * 32,
        "size": 1_234_567,
        "audio_format": audio_format(),
        "privacy": privacy_human(),
        **over,
    }


def channel_map_doc(artifact_id: str | None = None, **over: Any) -> dict[str, Any]:
    """A declared stereo map: channel 0 is the caller, channel 1 the agent (Pipecat layout)."""
    return {
        "artifact_id": artifact_id or new_id("art"),
        "channels": [{"index": 0, "role": "caller"}, {"index": 1, "role": "agent"}],
        "determination": "declared",
        "method": "pipecat_audio_buffer_processor_stereo",
        **over,
    }


def inferred_map_doc(artifact_id: str | None = None, **over: Any) -> dict[str, Any]:
    return {
        **channel_map_doc(artifact_id),
        "determination": "inferred",
        "method": "energy_correlation",
        "confidence": 0.9,
        **over,
    }


# ------------------------------------------------------------------------ transcripts and turns


def language_declaration(**over: Any) -> dict[str, Any]:
    """A user's declaration of the language of an untagged import (N14)."""
    return {
        "language": "mr-IN",
        "source": "user_declared",
        "declared_by": "cli",
        "declared_at": "2026-10-23T08:00:00.000000Z",
        **over,
    }


def transcript_doc(**over: Any) -> dict[str, Any]:
    return {
        **meta("tsc"),
        "source": "our_stt",
        "privacy": privacy_human(),
        **over,
    }


def segment_doc(**over: Any) -> dict[str, Any]:
    return {"granularity": "phrase", "text": "hello", **over}


def turn_doc(transcript_id: str | None = None, seq: int = 0, **over: Any) -> dict[str, Any]:
    """A turn without timing (``timing_source`` none), the shape transcript-only imports have."""
    return {
        **meta("turn"),
        "transcript_id": transcript_id or new_id("tsc"),
        "seq": seq,
        "role": "agent",
        "timing_source": "none",
        "source": "agent_log",
        "text": "How can I help you today?",
        **over,
    }


def exchange_doc(index: int = 0, **over: Any) -> dict[str, Any]:
    return {**meta("exc"), "index": index, "source": "telemetry", **over}


# ------------------------------------------------------------------ tool calls, spans, evidence


def tool_call_doc(**over: Any) -> dict[str, Any]:
    return {
        **meta("tcl"),
        "name": "lookup_account",
        "arguments_raw": '{"city": "Pune"}',
        "arguments": {"city": "Pune"},
        "arguments_complete": True,
        "result_raw": '{"ok": true}',
        "result_complete": True,
        "status": "completed",
        "result_error": False,
        "seq": 0,
        "source": "agent_telemetry",
        "external_call_id": "call_8f3a2c",
        **over,
    }


def span_doc(**over: Any) -> dict[str, Any]:
    return {
        **meta("spn"),
        "stage": "stt",
        "provider": "sarvam",
        "model": "saaras:v4",
        "start": "2026-10-22T10:00:01.000000Z",
        "duration_ms": 350,
        "ttfb_ms": 120,
        "span_ref": {"system": "otel", "kind": "span_id", "value": "00f067aa0ba902b7"},
        **over,
    }


def evidence_ref_doc(kind: str = "turn", ref_id: str | None = None, **over: Any) -> dict[str, Any]:
    prefix = {
        "turn": "turn",
        "exchange": "exc",
        "tool_call": "tcl",
        "span": "spn",
        "artifact_range": "art",
    }[kind]
    return {"kind": kind, "ref_id": ref_id or new_id(prefix), **over}


def ingest_warning_doc(**over: Any) -> dict[str, Any]:
    return {
        "code": "language_label_normalized",
        "path": "/detected_language",
        "raw": OTHER_LABEL_RAW,
        "message": "an underscore was replaced by a hyphen",
        **over,
    }


# ---------------------------------------------------------------------------- metric results


def judge_info_doc(**over: Any) -> dict[str, Any]:
    return {
        "model_ref": model_ref(),
        "prompt_sha256": "cd" * 32,
        "params": {"temperature": 0},
        "per_turn": [],
        **over,
    }


def per_turn_summary_doc(
    passed: int = 6, failed: int = 2, not_applicable: int = 1, undetermined: int = 1, **over: Any
) -> dict[str, Any]:
    """A consistent summary: the share, counted turns and total turns follow from the counts."""
    counted = passed + failed
    doc: dict[str, Any] = {
        "counts": {
            "passed": passed,
            "failed": failed,
            "not_applicable": not_applicable,
            "undetermined": undetermined,
        },
        "counted_turns": counted,
        "total_turns": counted + not_applicable + undetermined,
    }
    if counted:
        doc["share"] = api("metrics").per_turn_share(
            registered_class("PerTurnCounts").model_validate(doc["counts"])
        )
    doc.update(over)
    return doc


def result_doc(definition: Any = None, **over: Any) -> dict[str, Any]:
    """A scored boolean result of a judge definition (JudgeInfo, no scorer).

    ``definition`` is a validated ``MetricDefinition`` (or None for a fresh reference); the result
    refers to it by ``{id, version}``."""
    metric = ref("met") if definition is None else {"id": str(definition.id), "version": 1}
    if definition is not None:
        metric = {"id": str(definition.id), "version": definition.version}
    return {
        **meta("res"),
        "metric": metric,
        "status": "scored",
        "value": True,
        "passed": True,
        "judge": judge_info_doc(),
        "language_source": "observed",
        **over,
    }


def scorer_info(**over: Any) -> dict[str, Any]:
    return {"implementation_ref": "builtin:p95_latency", "version": "1.4.2", **over}


# ----------------------------------------------------------------------------------- runs


def run_doc(**over: Any) -> dict[str, Any]:
    return {
        **meta("run"),
        "project_id": new_id("prj"),
        "agent_target": ref("agt"),
        "config_snapshot_id": new_id("snap"),
        "mode": "recorded",
        "caller_origin": "human",
        "observed_tier": "T0",
        "available_inputs": ["audio", "transcript"],
        "channel_maps": [],
        "external_refs": [],
        "ingest_warnings": [],
        **over,
    }


# ------------------------------------------------------------------------- dataset documents


def _dataset_doc() -> dict[str, Any]:
    return {
        **meta("dts"),
        "version": 1,
        "name": "Refund calls",
        "item_ids": [new_id("dti"), new_id("dti")],
    }


def _dataset_item_doc() -> dict[str, Any]:
    return {
        **meta("dti"),
        "language": "hi-IN",
        "artifact_id": new_id("art"),
        "privacy": privacy_human(),
    }


# ---------------------------------------------------------------------------- the world builders


def snapshot_parts(
    *,
    with_profile: bool = False,
    profile_digest: Mapping[str, Any] | None = None,
    judge_over: Mapping[str, Any] | None = None,
    extra_definitions: Sequence[Mapping[str, Any]] = (),
    required: Sequence[bool] | None = None,
    enabled: Sequence[bool] | None = None,
    evaluator_over: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validated entities of a small, consistent run configuration.

    One persona and one evaluator; definition 0 is an expected-outcome check (a boolean judge
    that uses the reserved variable ``expected_outcome``, ``judge_over`` changes it), definition
    1 is a p95 latency check with weight 5, then ``extra_definitions``. Every definition has a
    binding of the evaluator (``enabled[i]`` switches one off: it stays in the evaluator's list
    but is not a rubric item) and the rubric has an item for each enabled binding
    (``required[i]``, default true)."""
    persona = make("Persona")
    evaluator_id = new_id("evl")
    definitions = [
        make("MetricDefinition", **judge_definition(**dict(judge_over or {}))),
        make("MetricDefinition", **latency_definition()),
        *(make("MetricDefinition", **dict(d)) for d in extra_definitions),
    ]
    enabled = enabled or [True] * len(definitions)
    required = required or [True] * len(definitions)
    bindings = [
        make(
            "MetricBinding",
            evaluator_id=evaluator_id,
            metric={"id": str(d.id), "version": d.version},
            enabled=enabled[index],
        )
        for index, d in enumerate(definitions)
    ]
    profile = None
    over: dict[str, Any] = {}
    if with_profile:
        profile = make("TestProfile")
        reference: dict[str, Any] = {"id": str(profile.id), "version": profile.version}
        if profile_digest is not None:
            reference["content_digest"] = dict(profile_digest)
        over["test_profile"] = reference
        over["instructions"] = "Greet {{profile.name}} and ask for a refund."
    over.update(evaluator_over or {})
    evaluator = make(
        "Evaluator",
        id=str(evaluator_id),
        persona={"id": str(persona.id), "version": persona.version},
        metric_bindings=[{"id": str(b.id), "version": b.version} for b in bindings],
        **over,
    )
    items = []
    for index, binding in enumerate(bindings):
        if not enabled[index]:
            continue
        item: dict[str, Any] = {
            "binding": {"id": str(binding.id), "version": binding.version},
            "required": required[index],
        }
        if index == 1:
            item["weight"] = "5"
        items.append(item)
    rubric = make("Rubric", items=items)
    return {
        "persona": persona,
        "evaluator": evaluator,
        "definitions": definitions,
        "bindings": bindings,
        "rubric": rubric,
        "profile": profile,
    }


def _dump(model: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = model.model_dump(mode="json", by_alias=True)
    return dumped


def snapshot_doc(parts: Mapping[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    """A ConfigSnapshot document with a correct digest from ``snapshot.compute_snapshot_digest``."""
    parts = parts or snapshot_parts()
    body: dict[str, Any] = {
        **meta("snap"),
        "evaluator": _dump(parts["evaluator"]),
        "persona": _dump(parts["persona"]),
        "metric_definitions": [_dump(d) for d in parts["definitions"]],
        "metric_bindings": [_dump(b) for b in parts["bindings"]],
        "rubric": _dump(parts["rubric"]),
        **over,
    }
    body.pop("content_digest", None)
    digest = api("snapshot").compute_snapshot_digest(body)
    return {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}


def verdict_world(
    *,
    weights: Sequence[str | None] = ("5", "3", "2"),
    required: Sequence[bool] = (True, True, True),
) -> dict[str, Any]:
    """Definitions, bindings and a rubric for the verdict tests: three boolean judge checks.

    Item i has weight ``weights[i]`` (None for unweighted) and ``required[i]``."""
    evaluator_id = new_id("evl")
    keys = ("goal_completion", "polite_tone", "no_pii_leak")
    definitions = [
        make(
            "MetricDefinition",
            **judge_definition(
                key=key, prompt_variables=["transcript"], prompt_template="Judge: {{transcript}}"
            ),
        )
        for key in keys[: len(weights)]
    ]
    bindings = [
        make(
            "MetricBinding",
            evaluator_id=evaluator_id,
            metric={"id": str(d.id), "version": d.version},
        )
        for d in definitions
    ]
    items = []
    for index, binding in enumerate(bindings):
        item: dict[str, Any] = {
            "binding": {"id": str(binding.id), "version": binding.version},
            "required": required[index],
        }
        if weights[index] is not None:
            item["weight"] = weights[index]
        items.append(item)
    rubric = make("Rubric", items=items)
    return {"definitions": definitions, "bindings": bindings, "rubric": rubric}


def applicability_world() -> dict[str, Any]:
    """Names for the language-step tests: a transcript with a producer tag, one with none."""
    return {}


# ------------------------------------------------------------------------------------ doc3


def doc3(name: str, /, **over: Any) -> dict[str, Any]:
    """The minimal valid document of a batch 3 entity, overrides applied (no digest)."""
    builders: dict[str, Callable[[], dict[str, Any]]] = {
        "Artifact": artifact_doc,
        "Transcript": transcript_doc,
        "Turn": turn_doc,
        "Exchange": exchange_doc,
        "ToolCallEvent": tool_call_doc,
        "StageSpan": span_doc,
        "MetricResult": lambda: result_doc(),
        "RunVerdict": lambda: _verdict_doc(),
        "Run": run_doc,
        "Dataset": _dataset_doc,
        "DatasetItem": _dataset_item_doc,
    }
    if name == "ConfigSnapshot":
        return snapshot_doc(None, **over)
    return {**builders[name](), **over}


def _verdict_doc() -> dict[str, Any]:
    binding = new_id("mbd")
    return {
        **meta("vrd"),
        "required_total": 1,
        "passed": 1,
        "failed": 0,
        "not_applicable": 0,
        "errored": 0,
        "status": "pass",
        "checks": [
            {
                "binding_id": binding,
                "metric_ref": ref("met"),
                "outcome": "passed",
                "result_id": new_id("res"),
            }
        ],
        "weighted_excluded": [],
    }


def make3(name: str, /, **over: Any) -> Any:
    """The validated batch 3 entity ``name`` (a versioned one is sealed first)."""
    model = entity_class3(name)
    doc = doc3(name, **over)
    return model.model_validate(seal(model, doc) if name in VERSIONED_NAMES3 else doc)


@contextlib.contextmanager
def _digest_off() -> Iterator[None]:
    base = api("base")
    original = base.verify_digest
    base.verify_digest = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        base.verify_digest = original


def assert_rejected3(
    model: Any, doc: Mapping[str, Any], *, field: str | None = None
) -> ValidationError:
    """``doc`` fails ``model`` with a ``ValidationError``; a versioned model gets a placeholder
    digest when the document has none (the digest check is off, so the rejection is about content).
    With ``field``, some error location must contain that name."""
    body = dict(doc)
    versioned = "version" in getattr(model, "model_fields", {})
    if versioned and "content_digest" not in body:
        body["content_digest"] = PLACEHOLDER_DIGEST
    with _digest_off(), pytest.raises(ValidationError) as caught:
        model.model_validate(body)
    if field is not None:
        located = [field in [str(part) for part in e["loc"]] for e in caught.value.errors()]
        assert any(located), f"no error located at {field!r}: {caught.value.errors()}"
    return caught.value


def assert_input_hidden(
    model: Any, doc: Mapping[str, Any], marker: str, *, field: str, label: str = ""
) -> list[ValidationError]:
    """``doc`` is rejected by ``model`` through ``model_validate`` and ``model_validate_json``, and
    ``marker`` appears in none of the renderings that code might log (``str``, ``repr``,
    ``errors(include_input=False)``, ``json(include_input=False)``). The error is still located at
    ``field`` (some error location contains that name) and every message is non-empty, so the
    rule that failed is named. Used for ADR 0007 Decision 8: the setting
    ``hide_input_in_errors`` (the input) and the rule that no shared validator quotes the
    rejected value in its message (finding N42, decided by the owner)."""
    body = dict(doc)
    if "content_digest" in getattr(model, "model_fields", {}) and "content_digest" not in body:
        body["content_digest"] = PLACEHOLDER_DIGEST
    with _digest_off():
        with pytest.raises(ValidationError) as via_python:
            model.model_validate(body)
        with pytest.raises(ValidationError) as via_json:
            model.model_validate_json(json.dumps(body))
    errors = [via_python.value, via_json.value]
    for error in errors:
        for text in error_texts(error):
            assert marker not in text, (
                f"{model.__name__} {label}: the rejected value is shown: {text}"
            )
        located = [[str(part) for part in e["loc"]] for e in error.errors()]
        assert any(field in loc for loc in located), (model.__name__, label, field, located)
        assert all(str(e["msg"]).strip() for e in error.errors()), (model.__name__, label)
    return errors


# the constants below keep imports used (mypy strict, ruff F401)
_UNUSED = (JUDGE_TEMPLATE, CURRENT_SCHEMA_VERSION, build)
