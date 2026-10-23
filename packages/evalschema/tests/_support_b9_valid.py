# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 12 (P1-16): the valid golden fixtures. No tests here.

Every fixture is a builder registered with ``@valid(entity, name)``. ``minimal`` has only the
required members; ``maximal`` fills every optional member that one document can hold; the
``maximal-<what>`` documents carry the members and the enum or union branches that ``maximal``
cannot (a second endpoint kind, another unit, another status). All texts are synthetic. Indic text
is synthetic too and awaits native review (``fixtures/README.md``).

The builders start from the batch 2 to 7 helpers, replace what the fixture needs, and let the
model check the result when ``render_all()`` dumps it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from . import _support_b2 as b2
from . import _support_b3 as b3
from . import _support_b7 as b7
from ._support import CURRENT_SCHEMA_VERSION
from ._support_b9 import entity_models, valid

CREATED = "2026-10-23T12:00:00.000000Z"
LATER = "2026-10-23T12:30:15.250000Z"

Doc = dict[str, Any]


def head(prefix: str, **over: Any) -> Doc:
    """The three entity members every minimal document needs."""
    doc: Doc = {
        "id": b2.new_id(prefix),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "created_at": CREATED,
    }
    doc.update(over)
    return doc


def full_head(prefix: str, metadata: dict[str, Any] | None = None, **over: Any) -> Doc:
    """The entity members of a maximal document: a non-empty ``metadata``."""
    doc = head(prefix, **over)
    doc["metadata"] = metadata or {
        "source": "golden fixture",
        "labels": ["synthetic", "regression"],
        "nested": {"rank": 3, "ratio": 0.5, "flag": True, "none": None},
    }
    return doc


def sealed(entity: str, doc: Doc) -> Doc:
    """``doc`` with a correct plain ``content_digest`` (a versioned entity)."""
    return b2.seal(entity_models()[entity], doc)


def snapshot_sealed(doc: Doc) -> Doc:
    return b7.resnap(doc)


def ref(prefix: str, version: int = 1, **over: Any) -> Doc:
    return {"id": b2.new_id(prefix), "version": version, **over}


def plain_digest(value: str) -> Doc:
    return {"scheme": "sha256-jcs/v1", "value": value}


def keyed_digest(value: str) -> Doc:
    return {
        "scheme": "hmac-sha256-jcs/v1",
        "value": value,
        "key_ref": {"key_id": "profile-hmac-1", "key_version": "2", "provider_hint": "local-kms"},
    }


# ------------------------------------------------------------------------------------ Project


@valid("Project", "minimal")
def _project_minimal() -> Doc:
    return head("prj", name="Minimal project")


@valid("Project", "maximal")
def _project_maximal() -> Doc:
    return full_head(
        "prj",
        name="Support voice agents",
        provider_configs=[
            {"provider": "sarvam", "model": "saaras:v4", "config_ref": "env:SARVAM_API_KEY"},
            {"provider": "openai", "model": "gpt-4.1", "config_ref": "secret:judge-key"},
        ],
    )


# ------------------------------------------------------------------------------- AgentTarget

ALL_INPUT_KINDS = [
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
]


@valid("AgentTarget", "minimal")
def _agent_minimal() -> Doc:
    return sealed(
        "AgentTarget",
        head("agt", version=1, name="Minimal agent", endpoint=b2.ws_endpoint()),
    )


def _agent_maximal(endpoint: Doc, name: str) -> Doc:
    return sealed(
        "AgentTarget",
        full_head(
            "agt",
            version=3,
            name=name,
            endpoint=endpoint,
            languages=["hi-IN", "en-IN", "mr-IN", "ta-IN", "pa-Guru-IN"],
            declared_capabilities=list(ALL_INPUT_KINDS),
            declared_stack=[
                {"stage": "stt", "provider": "sarvam", "model": "saaras:v4"},
                {"stage": "llm", "provider": "openai", "model": "gpt-4.1"},
                {"stage": "tts", "provider": "sarvam", "model": "bulbul:v2"},
                {"stage": "realtime", "provider": "openai", "model": "gpt-realtime"},
            ],
        ),
    )


@valid("AgentTarget", "maximal")
def _agent_maximal_ws() -> Doc:
    return _agent_maximal(b2.ws_endpoint(), "Websocket agent")


@valid("AgentTarget", "maximal-webrtc")
def _agent_maximal_webrtc() -> Doc:
    return _agent_maximal(b2.webrtc_endpoint(), "WebRTC agent")


@valid("AgentTarget", "maximal-sip")
def _agent_maximal_sip() -> Doc:
    return _agent_maximal(b2.sip_endpoint(), "SIP agent")


# ---------------------------------------------------------------------------------- Persona


@valid("Persona", "minimal")
def _persona_minimal() -> Doc:
    return sealed("Persona", head("per", version=1, language="hi-IN"))


@valid("Persona", "maximal")
def _persona_maximal() -> Doc:
    return sealed(
        "Persona",
        full_head(
            "per", version=2, language="mr-IN", accent="\u092a\u0941\u0923\u0947\u0930\u0940"
        ),
    )


# ------------------------------------------------------------------------------- TestProfile


@valid("TestProfile", "minimal")
def _profile_minimal() -> Doc:
    return sealed(
        "TestProfile",
        head(
            "tpf", version=1, name="Asha Verma", dob="1990-04-12", account_number="ACCT-4400-1234"
        ),
    )


@valid("TestProfile", "maximal")
def _profile_maximal() -> Doc:
    return sealed(
        "TestProfile",
        full_head(
            "tpf",
            version=4,
            name="\u0905\u0936\u094b\u0915 \u092a\u0935\u093e\u0930",
            dob="1985-11-02",
            account_number="ACCT-0099-5544",
        ),
    )


# ------------------------------------------------------------------------ metric definitions


def hexd(label: str) -> str:
    """A deterministic 64-digit lowercase hex string (a stand-in for a digest value)."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def vref(prefix: str, label: str, version: int = 1, scheme: str = "plain") -> Doc:
    """A ``{id, version, content_digest}`` reference with a stand-in digest of the given scheme."""
    digest = plain_digest(hexd(label)) if scheme == "plain" else keyed_digest(hexd(label))
    return {"id": b2.new_id(prefix), "version": version, "content_digest": digest}


ALL_LANGUAGES = [
    "en-IN",
    "hi-IN",
    "mr-IN",
    "te-IN",
    "ta-IN",
    "kn-IN",
    "ml-IN",
    "pa-Guru-IN",
    "gu-IN",
]

PARAMETERS = [
    {"name": "percentile", "value_type": "integer", "default": 95},
    {"name": "threshold", "value_type": "number", "default": 0.5},
    {"name": "locale_name", "value_type": "string", "default": "Pune"},
    {"name": "strict", "value_type": "boolean", "default": False},
    {"name": "options", "value_type": "json", "default": {"window": [1, 2, 3], "mode": "fast"}},
    {"name": "no_default", "value_type": "string"},
]

JUDGE_PROMPT = (
    "Expected: {{expected_outcome}}\nTranscript:\n{{transcript}}\nAnswer with a number from 1 to 5."
)


def definition(**over: Any) -> Doc:
    """A ``MetricDefinition`` document with the entity members; ``over`` replaces members."""
    doc = head("met", version=1)
    doc.update(over)
    return doc


@valid("MetricDefinition", "minimal")
def _definition_minimal() -> Doc:
    return sealed(
        "MetricDefinition",
        definition(
            key="has_reference",
            kind="deterministic",
            value_type="boolean",
            requires=["transcript"],
            languages="language_independent",
            script_sensitive=False,
            aggregation={"kind": "per_conversation"},
            implementation_ref="builtin:has_reference",
        ),
    )


@valid("MetricDefinition", "maximal")
def _definition_maximal() -> Doc:
    return sealed(
        "MetricDefinition",
        full_head(
            "met",
            version=2,
            key="politeness_share",
            kind="llm_judge",
            value_type="rating",
            scale={"min": 1, "max": 5},
            requires=list(ALL_INPUT_KINDS),
            languages=list(ALL_LANGUAGES),
            script_sensitive=True,
            aggregation={
                "kind": "per_turn_share",
                "turn_rule": {"kind": "range", "min": 4, "max": 5, "inclusive": True},
            },
            pass_rule={"kind": "range", "min": 0.8, "max": 1, "inclusive": True},
            parameters=list(PARAMETERS),
            prompt_template=JUDGE_PROMPT,
            prompt_variables=["expected_outcome", "transcript"],
            judge_model_ref={"provider": "openai", "model": "gpt-4.1"},
        ),
    )


def _numeric(name: str, kind: str, unit: str, **over: Any) -> Doc:
    base: Doc = {
        "key": name,
        "kind": kind,
        "value_type": "numeric",
        "unit": unit,
        "requires": ["turn_timing"],
        "languages": ["en-IN", "hi-IN"],
        "script_sensitive": False,
        "aggregation": {"kind": "per_conversation"},
        "implementation_ref": f"builtin:{name}",
    }
    base.update(over)
    return sealed("MetricDefinition", full_head("met", version=1, **base))


@valid("MetricDefinition", "maximal-latency-ms")
def _definition_latency() -> Doc:
    return _numeric(
        "p95_latency", "latency", "ms", pass_rule={"kind": "range", "max": 800, "inclusive": True}
    )


@valid("MetricDefinition", "maximal-wer")
def _definition_wer() -> Doc:
    return _numeric(
        "word_error_rate",
        "audio",
        "wer",
        requires=["transcript", "reference_text"],
        script_sensitive=True,
        pass_rule={"kind": "range", "min": 0, "max": 0.2, "inclusive": True},
    )


@valid("MetricDefinition", "maximal-cer")
def _definition_cer() -> Doc:
    return _numeric(
        "character_error_rate",
        "deterministic",
        "cer",
        requires=["transcript", "reference_text"],
        script_sensitive=True,
        pass_rule={"kind": "range", "max": 0.1, "inclusive": False},
    )


@valid("MetricDefinition", "maximal-count-share")
def _definition_count() -> Doc:
    return _numeric(
        "tool_calls_per_turn",
        "deterministic",
        "count",
        requires=["tool_calls"],
        aggregation={
            "kind": "per_turn_share",
            "turn_rule": {"kind": "range", "min": 1, "inclusive": True},
        },
        pass_rule={"kind": "range", "min": 0.5, "inclusive": True},
    )


@valid("MetricDefinition", "maximal-ratio-share")
def _definition_ratio() -> Doc:
    return _numeric(
        "silence_ratio",
        "deterministic",
        "ratio",
        requires=["audio"],
        aggregation={
            "kind": "per_turn_share",
            "turn_rule": {"kind": "range", "max": 0.3, "inclusive": True},
        },
        pass_rule={"kind": "range", "min": 0.9, "inclusive": True},
    )


@valid("MetricDefinition", "maximal-percent")
def _definition_percent() -> Doc:
    return _numeric(
        "talk_time_percent",
        "deterministic",
        "percent",
        requires=["turn_timing"],
        pass_rule={"kind": "range", "min": 20, "max": 60, "inclusive": True},
    )


@valid("MetricDefinition", "maximal-judge-enum-share")
def _definition_enum() -> Doc:
    return sealed(
        "MetricDefinition",
        full_head(
            "met",
            version=1,
            key="outcome_class",
            kind="llm_judge",
            value_type="enum",
            scale={"options": ["resolved", "escalated", "abandoned"]},
            requires=["transcript"],
            languages="language_independent",
            script_sensitive=False,
            aggregation={
                "kind": "per_turn_share",
                "turn_rule": {"kind": "one_of", "options": ["resolved", "escalated"]},
            },
            pass_rule={"kind": "range", "min": 0.7, "inclusive": True},
            prompt_template="Classify the call outcome.\nTranscript:\n{{transcript}}",
            prompt_variables=["transcript"],
            judge_model_ref={"provider": "anthropic", "model": "claude-sonnet-4-5"},
        ),
    )


@valid("MetricDefinition", "maximal-judge-enum-rule")
def _definition_enum_rule() -> Doc:
    return sealed(
        "MetricDefinition",
        full_head(
            "met",
            version=1,
            key="outcome_class_rule",
            kind="llm_judge",
            value_type="enum",
            scale={"options": ["resolved", "escalated", "abandoned"]},
            requires=["transcript"],
            languages=["hi-IN"],
            script_sensitive=False,
            aggregation={"kind": "per_conversation"},
            pass_rule={"kind": "one_of", "options": ["resolved"]},
            prompt_template="Classify the call outcome.\nTranscript:\n{{transcript}}",
            prompt_variables=["transcript"],
            judge_model_ref={"provider": "openai", "model": "gpt-4.1"},
        ),
    )


@valid("MetricDefinition", "maximal-judge-boolean")
def _definition_boolean() -> Doc:
    return sealed(
        "MetricDefinition",
        full_head(
            "met",
            version=1,
            key="goal_completion",
            kind="llm_judge",
            value_type="boolean",
            requires=["transcript", "agent_transcript"],
            languages=["en-IN", "hi-IN"],
            script_sensitive=False,
            aggregation={
                "kind": "per_turn_share",
                "turn_rule": {"kind": "equals_true"},
            },
            pass_rule={"kind": "range", "min": 1, "inclusive": True},
            prompt_template=JUDGE_PROMPT,
            prompt_variables=["expected_outcome", "transcript"],
            judge_model_ref={"provider": "openai", "model": "gpt-4.1"},
        ),
    )


@valid("MetricDefinition", "maximal-boolean-rule")
def _definition_boolean_rule() -> Doc:
    return sealed(
        "MetricDefinition",
        full_head(
            "met",
            version=1,
            key="used_refund_tool",
            kind="deterministic",
            value_type="boolean",
            requires=["tool_calls", "expected_tool_calls"],
            languages="language_independent",
            script_sensitive=False,
            aggregation={"kind": "per_conversation"},
            pass_rule={"kind": "equals_true"},
            implementation_ref="builtin:used_refund_tool",
        ),
    )


# --------------------------------------------------------------------- bindings and rubrics


@valid("MetricBinding", "minimal")
def _binding_minimal() -> Doc:
    return sealed(
        "MetricBinding",
        head(
            "mbd",
            version=1,
            evaluator_id=b2.new_id("evl"),
            metric=ref("met"),
            params={},
            enabled=True,
        ),
    )


@valid("MetricBinding", "maximal")
def _binding_maximal() -> Doc:
    return sealed(
        "MetricBinding",
        full_head(
            "mbd",
            version=2,
            evaluator_id=b2.new_id("evl"),
            metric=vref("met", "metric-definition", version=2),
            params={
                "percentile": 99,
                "strict": True,
                "locale_name": "Mumbai",
                "options": {"a": [1]},
            },
            enabled=True,
        ),
    )


@valid("MetricBinding", "maximal-disabled-keyed")
def _binding_disabled() -> Doc:
    return sealed(
        "MetricBinding",
        full_head(
            "mbd",
            version=1,
            evaluator_id=b2.new_id("evl"),
            metric=vref("met", "metric-definition-2", scheme="keyed"),
            params={"threshold": 0.25},
            enabled=False,
        ),
    )


@valid("Rubric", "minimal")
def _rubric_minimal() -> Doc:
    return sealed(
        "Rubric", head("rub", version=1, items=[{"binding": ref("mbd"), "required": True}])
    )


@valid("Rubric", "maximal")
def _rubric_maximal() -> Doc:
    return sealed(
        "Rubric",
        full_head(
            "rub",
            version=3,
            items=[
                {
                    "binding": vref("mbd", "binding-1"),
                    "required": True,
                    "weight": "5",
                },
                {
                    "binding": vref("mbd", "binding-2", scheme="keyed"),
                    "required": False,
                    "weight": "2.5",
                },
                {"binding": ref("mbd"), "required": False},
            ],
        ),
    )


# ------------------------------------------------------------------------------- Evaluator

HINDI_INSTRUCTIONS = (
    "\u0906\u092a \u0917\u094d\u0930\u093e\u0939\u0915 \u0939\u0948\u0902\u0964 "
    "{{profile.name}} \u0915\u0947 \u0928\u093e\u092e \u0938\u0947 \u092a\u0939\u091a\u093e\u0928 "
    "\u0926\u0947\u0902 \u0914\u0930 \u0916\u093e\u0924\u093e {{profile.account_number}} "
    "\u0915\u093e \u0930\u093f\u092b\u0902\u0921 \u092e\u093e\u0902\u0917\u0947\u0902\u0964"
)
HINDI_OUTCOME = (
    "\u090f\u091c\u0947\u0902\u091f \u0930\u093f\u092b\u0902\u0921 \u0936\u0941\u0930\u0942 "
    "\u0915\u0930\u0924\u093e \u0939\u0948 \u0914\u0930 \u0938\u0902\u0926\u0930\u094d\u092d "
    "\u0938\u0902\u0916\u094d\u092f\u093e \u0926\u0947\u0924\u093e \u0939\u0948\u0964"
)


def _evaluator_base(**over: Any) -> Doc:
    doc = head("evl", version=1)
    doc.update(over)
    return doc


@valid("Evaluator", "minimal")
def _evaluator_minimal() -> Doc:
    return sealed(
        "Evaluator",
        _evaluator_base(
            name="Refund request",
            language="en-IN",
            instructions="Ask for a refund of the last order.",
            expected_outcome="The agent starts a refund.",
            persona=ref("per"),
            metric_bindings=[ref("mbd")],
            max_duration_ms=300_000,
        ),
    )


def _evaluator_maximal(profile_scheme: str) -> Doc:
    return sealed(
        "Evaluator",
        full_head(
            "evl",
            version=2,
            name="\u0930\u093f\u092b\u0902\u0921 \u0905\u0928\u0941\u0930\u094b\u0927",
            language="hi-IN",
            instructions=HINDI_INSTRUCTIONS,
            expected_outcome=HINDI_OUTCOME,
            persona=vref("per", "persona", version=2, scheme=profile_scheme),
            test_profile=vref("tpf", "profile", version=3, scheme=profile_scheme),
            metric_bindings=[
                vref("mbd", "binding-1"),
                vref("mbd", "binding-2", scheme="keyed"),
                ref("mbd"),
            ],
            scripted_turns=[
                {"index": 0, "text": "\u0928\u092e\u0938\u094d\u0924\u0947", "language": "hi-IN"},
                {"index": 1, "text": "My name is {{profile.name}}", "language": "en-IN"},
            ],
            expected_tool_calls=[
                {
                    "name": "lookup_account",
                    "match": "name_only",
                    "arguments": None,
                    "order_index": 0,
                },
                {
                    "name": "issue_refund",
                    "match": "arguments_exact",
                    "arguments": {"account": "{{profile.account_number}}", "amount": 499},
                    "order_index": 1,
                },
                {
                    "name": "send_sms",
                    "match": "arguments_subset",
                    "arguments": {"template": "refund_started"},
                    "order_index": 2,
                },
            ],
            max_duration_ms=900_000,
        ),
    )


@valid("Evaluator", "maximal")
def _evaluator_maximal_plain() -> Doc:
    return _evaluator_maximal("plain")


@valid("Evaluator", "maximal-keyed-profile")
def _evaluator_maximal_keyed() -> Doc:
    return _evaluator_maximal("keyed")


# ------------------------------------------------------------------------------- privacy


def privacy_obtained() -> Doc:
    return {
        "human_subject": True,
        "human_subject_reason": "manual",
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
    }


def privacy_not_obtained() -> Doc:
    return {
        "human_subject": True,
        "human_subject_reason": "real_person_likeness",
        "consent": {
            "status": "not_obtained",
            "reference": "declined-2026-10-02",
            "recorded_at": "2026-10-02T09:30:00.000000Z",
        },
        "retention": {
            "retain_until": "2026-12-31T23:59:59.000000Z",
            "policy_ref": "policy:retain-90-days",
            "delete_on_expiry": False,
        },
    }


def privacy_unknown() -> Doc:
    return {
        "human_subject": True,
        "human_subject_reason": "marked_before_role_evidence",
        "consent": {"status": "unknown", "reference": "none-recorded", "recorded_at": CREATED},
        "retention": {
            "retain_until": "2027-01-01T00:00:00.000000Z",
            "policy_ref": "policy:default",
            "delete_on_expiry": True,
        },
    }


PRIVACY_NONE: Doc = {"human_subject": False}

# ------------------------------------------------------------------------------- Artifact


def art_format(**over: Any) -> Doc:
    doc: Doc = {
        "container": "wav",
        "codec": "pcm_s16le",
        "sample_rate_hz": 16000,
        "channels": 2,
        "bits_per_sample": 16,
    }
    doc.update(over)
    return doc


@valid("Artifact", "minimal")
def _artifact_minimal() -> Doc:
    return head(
        "art",
        uri="https://recordings.example.com/calls/0001.wav",
        sha256=hexd("artifact-minimal"),
        size=0,
        privacy=dict(PRIVACY_NONE),
    )


def _artifact(uri: str, privacy: Doc, fmt: Doc, label: str) -> Doc:
    return full_head(
        "art",
        uri=uri,
        sha256=hexd(label),
        size=1_234_567,
        audio_format=fmt,
        turn_ref=b2.new_id("turn"),
        privacy=privacy,
    )


@valid("Artifact", "maximal")
def _artifact_maximal() -> Doc:
    return _artifact(
        "https://recordings.example.com/calls/2026/10/call-0001.wav",
        privacy_obtained(),
        art_format(),
        "artifact-maximal",
    )


@valid("Artifact", "maximal-consent-not-obtained")
def _artifact_not_obtained() -> Doc:
    return _artifact(
        "s3://tracer-fixtures/calls/call-0002.flac",
        privacy_not_obtained(),
        art_format(
            container="flac", codec="flac", sample_rate_hz=48000, channels=1, bits_per_sample=24
        ),
        "artifact-not-obtained",
    )


@valid("Artifact", "maximal-consent-unknown")
def _artifact_unknown() -> Doc:
    return _artifact(
        "file:///var/tracer/calls/call-0003.opus",
        privacy_unknown(),
        art_format(
            container="ogg", codec="opus", sample_rate_hz=24000, channels=1, bits_per_sample=16
        ),
        "artifact-unknown",
    )


# ----------------------------------------------------------------------------- Transcript

EXTERNAL_TRACE: Doc = {
    "system": "otel",
    "kind": "trace_id",
    "value": "4bf92f3577b34da6a3ce929d0e0e4736",
}
EXTERNAL_SESSION: Doc = {"system": "pipecat", "kind": "session_id", "value": "session-0001"}


@valid("Transcript", "minimal")
def _transcript_minimal() -> Doc:
    return head("tsc", source="our_stt", privacy=dict(PRIVACY_NONE))


def _transcript(source: str, privacy: Doc, **over: Any) -> Doc:
    doc = full_head(
        "tsc",
        source=source,
        provider="sarvam",
        model="saaras:v4",
        provider_call_ref={"system": "sarvam", "kind": "request_id", "value": "req-8842-aa"},
        timeline_origin={
            "kind": "artifact",
            "ref": b2.new_id("art"),
            "started_at": "2026-10-23T11:59:58.000000Z",
        },
        privacy=privacy,
    )
    doc.update(over)
    return doc


@valid("Transcript", "maximal")
def _transcript_maximal() -> Doc:
    return _transcript(
        "our_stt",
        privacy_obtained(),
        detected_language="hi-IN",
        detected_language_raw="hi_IN",
        language_confidence=0.93,
    )


@valid("Transcript", "maximal-agent-export-declared")
def _transcript_agent_export() -> Doc:
    return _transcript(
        "agent_export",
        privacy_not_obtained(),
        timeline_origin={"kind": "telemetry", "ref": EXTERNAL_TRACE, "started_at": CREATED},
        declared_language={
            "language": "mr-IN",
            "source": "user_declared",
            "declared_by": "cli:import",
            "declared_at": "2026-10-23T08:00:00.000000Z",
        },
    )


@valid("Transcript", "maximal-agent-telemetry")
def _transcript_agent_telemetry() -> Doc:
    return _transcript(
        "agent_telemetry",
        privacy_unknown(),
        timeline_origin={"kind": "telemetry", "started_at": CREATED},
        detected_language="ta-IN",
        language_confidence=0.5,
    )


@valid("Transcript", "maximal-observer")
def _transcript_observer() -> Doc:
    return _transcript(
        "observer",
        dict(PRIVACY_NONE),
        timeline_origin={"kind": "observer", "ref": EXTERNAL_SESSION, "started_at": CREATED},
        detected_language="te-IN",
    )


@valid("Transcript", "maximal-script")
def _transcript_script() -> Doc:
    return _transcript("script", dict(PRIVACY_NONE), detected_language="en-IN")


@valid("Transcript", "maximal-human-reference")
def _transcript_human_reference() -> Doc:
    return _transcript(
        "human_reference",
        privacy_obtained(),
        timeline_origin={"kind": "observer", "ref": None, "started_at": None},
        declared_language={
            "language": "kn-IN",
            "source": "user_declared",
            "declared_by": "reviewer-7",
            "declared_at": "2026-10-23T09:00:00.000000Z",
        },
    )


# --------------------------------------------------------------------------------- Turn

MALAYALAM_CHILLU = "\u0d28\u0d7e"  # contains a chillu letter (synthetic)
MARATHI_SENTENCE = (
    "\u092e\u0932\u093e \u092a\u0948\u0938\u0947 \u092a\u0930\u0924 "
    "\u0939\u0935\u0947 \u0906\u0939\u0947\u0924"
)
ISOLATED = "account \u2066\u0967\u0968\u0969\u2069 ready"


def _segments() -> list[Doc]:
    return [
        {
            "granularity": "word",
            "start_ms": 1000,
            "end_ms": 1400,
            "text": "hello",
            "confidence": 0.98,
        },
        {
            "granularity": "phrase",
            "start_ms": 1000,
            "end_ms": 2200,
            "text": "hello there",
            "confidence": 0.9,
        },
        {
            "granularity": "utterance",
            "start_ms": 1000,
            "end_ms": 3000,
            "text": ISOLATED,
            "confidence": 0.8,
            "contains_bidi_controls": True,
        },
    ]


@valid("Turn", "minimal")
def _turn_minimal() -> Doc:
    return head(
        "turn",
        transcript_id=b2.new_id("tsc"),
        seq=0,
        role="caller",
        timing_source="none",
        source="our_stt",
        text="hello",
    )


def _turn(source: str, role: str, timing: str, meaning: str, **over: Any) -> Doc:
    doc = full_head(
        "turn",
        transcript_id=b2.new_id("tsc"),
        seq=3,
        role=role,
        start_ms=1000,
        end_ms=3500,
        wall_time={"at": LATER, "meaning": meaning},
        timing_source=timing,
        source=source,
        text=ISOLATED,
        segments=_segments(),
        language="en-IN",
        language_raw="en_IN",
        interrupted=True,
        exchange_index=1,
        contains_bidi_controls=True,
    )
    doc.update(over)
    return doc


@valid("Turn", "maximal")
def _turn_maximal() -> Doc:
    return _turn("our_stt", "caller", "audio_alignment", "start")


@valid("Turn", "maximal-agent-llm-output")
def _turn_agent_llm() -> Doc:
    return _turn(
        "agent_llm_output",
        "agent",
        "telemetry",
        "end",
        text="How can I help?",
        interrupted=False,
        contains_bidi_controls=False,
        segments=None,
        language="hi-IN",
        language_raw="hi",
    )


@valid("Turn", "maximal-agent-stt-unknown-role")
def _turn_agent_stt() -> Doc:
    return _turn(
        "agent_stt",
        "unknown",
        "observer",
        "logged",
        text=MARATHI_SENTENCE,
        contains_bidi_controls=False,
        segments=[
            {"granularity": "phrase", "start_ms": 1000, "end_ms": 2000, "text": MARATHI_SENTENCE}
        ],
        language="mr-IN",
        language_raw="mr",
    )


def _timed_none(source: str, meaning: str, text: str = "Namaste", **over: Any) -> Doc:
    return _turn(
        source,
        "agent",
        "none",
        meaning,
        start_ms=None,
        end_ms=None,
        text=text,
        contains_bidi_controls=False,
        segments=None,
        **over,
    )


@valid("Turn", "maximal-agent-tts-input")
def _turn_agent_tts() -> Doc:
    return _timed_none("agent_tts_input", "unknown", language="ta-IN", language_raw="ta")


@valid("Turn", "maximal-agent-context")
def _turn_agent_context() -> Doc:
    return _timed_none("agent_context", "logged", language="te-IN", language_raw="te")


@valid("Turn", "maximal-agent-log")
def _turn_agent_log() -> Doc:
    return _timed_none(
        "agent_log", "end", text=MALAYALAM_CHILLU, language="ml-IN", language_raw="ml_IN"
    )


@valid("Turn", "maximal-script")
def _turn_script() -> Doc:
    return _timed_none("script", "start", language="kn-IN", language_raw="kn")


@valid("Turn", "maximal-human-reference")
def _turn_human_reference() -> Doc:
    return _timed_none("human_reference", "unknown", language="pa-IN", language_raw="pa")


# ----------------------------------------------------------------------------- Exchange


@valid("Exchange", "minimal")
def _exchange_minimal() -> Doc:
    return head("exc", index=0, source="telemetry")


def _exchange(source: str, interrupted: bool, index: int) -> Doc:
    return full_head(
        "exc",
        index=index,
        source=source,
        started_at=LATER,
        duration_ms=4200,
        was_interrupted=interrupted,
    )


@valid("Exchange", "maximal")
def _exchange_maximal() -> Doc:
    return _exchange("telemetry", True, 2)


@valid("Exchange", "maximal-audio-alignment")
def _exchange_audio() -> Doc:
    return _exchange("audio_alignment", False, 3)


@valid("Exchange", "maximal-observer")
def _exchange_observer() -> Doc:
    return _exchange("observer", True, 4)


@valid("Exchange", "maximal-inferred-from-turns")
def _exchange_inferred() -> Doc:
    return _exchange("inferred_from_turns", False, 5)


# -------------------------------------------------------------------------- ToolCallEvent


@valid("ToolCallEvent", "minimal")
def _tool_minimal() -> Doc:
    return head(
        "tcl",
        name="lookup_account",
        arguments_complete=False,
        status="unknown",
        seq=0,
        source="agent_telemetry",
    )


def _tool(status: str, source: str, origin: Doc, **over: Any) -> Doc:
    doc = full_head(
        "tcl",
        name="issue_refund",
        arguments_raw='{"account": "ACCT-4400-1234", "amount": 499}',
        arguments={"account": "ACCT-4400-1234", "amount": 499, "items": [1, 2], "note": None},
        arguments_complete=True,
        result_raw='{"ok": true, "reference": "RF-1001"}',
        result_complete=True,
        status=status,
        result_error=False,
        seq=1,
        offset_ms=5400,
        timeline_origin=origin,
        source=source,
        external_call_id="call_8f3a2c",
    )
    doc.update(over)
    return doc


@valid("ToolCallEvent", "maximal")
def _tool_maximal() -> Doc:
    return _tool(
        "completed",
        "agent_telemetry",
        {"kind": "artifact", "ref": b2.new_id("art"), "started_at": CREATED},
    )


@valid("ToolCallEvent", "maximal-cancelled-truncated")
def _tool_cancelled() -> Doc:
    return _tool(
        "cancelled",
        "observer",
        {"kind": "observer", "ref": EXTERNAL_SESSION, "started_at": None},
        arguments_raw='{"account": "44001',
        arguments=None,
        arguments_complete=False,
        result_raw='{"ok": tr',
        result_complete=False,
        result_error=True,
        seq=2,
    )


@valid("ToolCallEvent", "maximal-unknown-export")
def _tool_unknown() -> Doc:
    return _tool(
        "unknown",
        "agent_export",
        {"kind": "telemetry", "ref": EXTERNAL_TRACE, "started_at": CREATED},
        seq=3,
    )


# ----------------------------------------------------------------------------- StageSpan

STAGES = ("stt", "llm", "tts", "realtime", "judge", "sim_caller_llm", "sim_caller_tts", "other")


@valid("StageSpan", "minimal")
def _span_minimal() -> Doc:
    return head(
        "spn",
        stage="stt",
        span_ref={"system": "otel", "kind": "span_id", "value": "00f067aa0ba902b7"},
    )


def _span(stage: str) -> Doc:
    return full_head(
        "spn",
        stage=stage,
        stage_raw="vad" if stage == "other" else None,
        provider="sarvam",
        model="saaras:v4",
        start="2026-10-23T12:00:01.000000Z",
        duration_ms=350,
        ttfb_ms=120,
        exchange_index=1,
        span_ref={"system": "otel", "kind": "span_id", "value": "00f067aa0ba902b7"},
    )


def _register_spans() -> None:
    for stage in STAGES:
        name = "maximal" if stage == "stt" else f"maximal-{stage.replace('_', '-')}"
        valid("StageSpan", name)(lambda stage=stage: _span(stage))  # type: ignore[misc]


_register_spans()


# ----------------------------------------------------------------------------- MetricResult


def mref(label: str = "metric", scheme: str = "plain") -> Doc:
    return vref("met", label, scheme=scheme)


def counts(passed: int, failed: int, not_applicable: int, undetermined: int) -> Doc:
    return {
        "passed": passed,
        "failed": failed,
        "not_applicable": not_applicable,
        "undetermined": undetermined,
    }


def per_turn(passed: int = 6, failed: int = 2, na: int = 1, undetermined: int = 1) -> Doc:
    return b3.per_turn_summary_doc(passed, failed, na, undetermined)


@valid("MetricResult", "minimal")
def _result_minimal() -> Doc:
    return head("res", metric=ref("met"), status="scored", value=True)


def _judge(**over: Any) -> Doc:
    doc: Doc = {
        "model_ref": {"provider": "openai", "model": "gpt-4.1"},
        "prompt_sha256": hexd("judge-prompt"),
        "params": {"temperature": 0, "seed": 7, "stop": ["END"]},
        "per_turn": [
            {
                "turn_seq": 0,
                "verdict": "yes",
                "p_yes": 0.97,
                "reported_model": "gpt-4.1-2026-04-14",
            },
            {"turn_seq": 1, "verdict": "no", "p_yes": 0.12, "reported_model": None},
            {"turn_seq": 2, "verdict": "undetermined", "p_yes": None, "reported_model": None},
        ],
        "confidence": 0.85,
        "reported_model": "gpt-4.1-2026-04-14",
    }
    doc.update(over)
    return doc


def _evidence() -> list[Doc]:
    return [
        {"kind": "turn", "ref_id": b2.new_id("turn"), "start_ms": None, "end_ms": None},
        {"kind": "exchange", "ref_id": b2.new_id("exc")},
        {"kind": "tool_call", "ref_id": b2.new_id("tcl")},
        {"kind": "span", "ref_id": b2.new_id("spn")},
        {"kind": "artifact_range", "ref_id": b2.new_id("art"), "start_ms": 100, "end_ms": 900},
    ]


def _scored(**over: Any) -> Doc:
    doc = full_head(
        "res",
        metric=mref(),
        status="scored",
        value=True,
        passed=True,
        judge=_judge(),
        language_source="observed",
        evidence_refs=_evidence(),
    )
    doc.update(over)
    return doc


@valid("MetricResult", "maximal")
def _result_maximal() -> Doc:
    return _scored()


@valid("MetricResult", "maximal-boolean-false")
def _result_false() -> Doc:
    return _scored(value=False, passed=False, metric=mref("metric-2", "keyed"))


@valid("MetricResult", "maximal-integer-scorer-declared")
def _result_integer() -> Doc:
    return _scored(
        value=420,
        passed=True,
        judge=None,
        scorer={"implementation_ref": "builtin:p95_latency", "version": "1.4.2"},
        language_source="declared",
        declared_language_transcript_ids=sorted([b2.new_id("tsc"), b2.new_id("tsc")]),
    )


@valid("MetricResult", "maximal-number")
def _result_number() -> Doc:
    return _scored(
        value=0.125,
        passed=False,
        judge=None,
        scorer={"implementation_ref": "builtin:wer", "version": "2.0.0"},
    )


@valid("MetricResult", "maximal-string")
def _result_string() -> Doc:
    return _scored(value="resolved", passed=True)


@valid("MetricResult", "maximal-per-turn-share")
def _result_share() -> Doc:
    return _scored(value=None, passed=True, per_turn=per_turn())


def _not_applicable(reason: str, **over: Any) -> Doc:
    doc = full_head(
        "res",
        metric=mref(),
        status="not_applicable",
        value=None,
        passed=None,
        na_reason=reason,
        language_source="observed",
    )
    doc.update(over)
    return doc


@valid("MetricResult", "maximal-na-missing-input")
def _result_na_missing() -> Doc:
    return _not_applicable(
        "missing_input",
        missing_kinds=list(ALL_INPUT_KINDS),
        language_source=None,
        per_turn=per_turn(0, 0, 4, 0),
        evidence_refs=_evidence()[:1],
    )


@valid("MetricResult", "maximal-na-agent-channel-unknown")
def _result_na_channel() -> Doc:
    return _not_applicable("agent_channel_unknown", language_source=None)


@valid("MetricResult", "maximal-na-language-not-observed")
def _result_na_not_observed() -> Doc:
    return _not_applicable("language_not_observed", language_source=None)


@valid("MetricResult", "maximal-na-unsupported-language")
def _result_na_unsupported_language() -> Doc:
    return _not_applicable("unsupported_language")


@valid("MetricResult", "maximal-na-unsupported-script")
def _result_na_unsupported_script() -> Doc:
    return _not_applicable("unsupported_script")


@valid("MetricResult", "maximal-error")
def _result_error() -> Doc:
    return _not_applicable(
        "x",
        status="error",
        na_reason=None,
        error={"code": "judge_call_failed", "message": "the judge provider returned HTTP 503"},
    )


@valid("MetricResult", "maximal-error-undetermined")
def _result_error_undetermined() -> Doc:
    return _not_applicable(
        "x",
        status="error",
        na_reason=None,
        per_turn=per_turn(0, 0, 2, 3),
        error={"code": "judge_undetermined", "message": "the judge was undetermined on every turn"},
    )


# ------------------------------------------------------------------------------ RunVerdict


def _check(outcome: str, label: str, scheme: str = "plain") -> Doc:
    return {
        "binding_id": b2.new_id("mbd"),
        "metric_ref": vref("met", label, scheme=scheme),
        "outcome": outcome,
        "result_id": b2.new_id("res"),
    }


@valid("RunVerdict", "minimal")
def _verdict_minimal() -> Doc:
    return head(
        "vrd",
        required_total=1,
        passed=1,
        failed=0,
        not_applicable=0,
        errored=0,
        status="pass",
        checks=[_check("passed", "a")],
    )


@valid("RunVerdict", "maximal")
def _verdict_maximal() -> Doc:
    return full_head(
        "vrd",
        required_total=5,
        passed=2,
        failed=0,
        not_applicable=2,
        errored=1,
        status="inconclusive",
        checks=[
            _check("passed", "a"),
            _check("passed", "b", "keyed"),
            _check("not_applicable", "c"),
            _check("not_applicable", "d"),
            _check("errored", "e"),
        ],
        weighted_score="0.7",
        weight_coverage="0.6",
        weighted_excluded=[
            {"binding_id": b2.new_id("mbd"), "outcome": "errored"},
            {"binding_id": b2.new_id("mbd"), "outcome": "not_applicable"},
        ],
    )


@valid("RunVerdict", "maximal-fail")
def _verdict_fail() -> Doc:
    return full_head(
        "vrd",
        required_total=4,
        passed=1,
        failed=2,
        not_applicable=0,
        errored=1,
        status="fail",
        checks=[
            _check("passed", "a"),
            _check("failed", "b"),
            _check("failed", "c"),
            _check("errored", "d"),
        ],
        weighted_score="0.25",
        weight_coverage="1",
        weighted_excluded=[],
    )


# ----------------------------------------------------------------------------------- Run


def _channel_maps(artifact_ids: list[str]) -> list[Doc]:
    return [
        {
            "artifact_id": artifact_ids[0],
            "channels": [{"index": 0, "role": "caller"}, {"index": 1, "role": "agent"}],
            "determination": "declared",
            "method": "pipecat_audio_buffer_processor_stereo",
            "confidence": None,
        },
        {
            "artifact_id": artifact_ids[1],
            "channels": [{"index": 0, "role": "mixed"}],
            "determination": "inferred",
            "method": "energy_correlation",
            "confidence": 0.9,
        },
        {
            "artifact_id": artifact_ids[2],
            "channels": [{"index": 0, "role": "unknown"}, {"index": 1, "role": "unknown"}],
            "determination": "inferred",
            "method": "voice_activity_overlap",
            "confidence": 0.4,
        },
    ]


def _warnings() -> list[Doc]:
    return [
        {
            "code": "language_label_normalized",
            "path": "/detected_language",
            "raw": "hi_IN",
            "message": "an underscore was replaced by a hyphen",
        },
        {"code": "timestamp_truncated", "path": None, "raw": None, "message": "kept 6 digits"},
    ]


@valid("Run", "minimal")
def _run_minimal() -> Doc:
    return head(
        "run",
        project_id=b2.new_id("prj"),
        agent_target=ref("agt"),
        config_snapshot_id=b2.new_id("snap"),
        mode="simulated",
        caller_origin="simulated",
        observed_tier="T0",
    )


def _run(tier: str, mode: str, origin: str, privacy: Doc | None, scheme: str, **over: Any) -> Doc:
    inputs = [k for k in ALL_INPUT_KINDS if tier != "T0" or k != "spans"]
    doc = full_head(
        "run",
        project_id=b2.new_id("prj"),
        agent_target=vref("agt", "agent-target", version=3, scheme=scheme),
        config_snapshot_id=b2.new_id("snap"),
        mode=mode,
        caller_origin=origin,
        started_at="2026-10-23T11:59:00.000000Z",
        caller_external_id=(
            None if privacy is None else {"value": "caller-0001", "privacy": privacy}
        ),
        detected_capabilities=list(ALL_INPUT_KINDS),
        channel_maps=_channel_maps([b2.new_id("art"), b2.new_id("art"), b2.new_id("art")]),
        available_inputs=inputs,
        observed_tier=tier,
        external_refs=[
            {
                "system": "voicera",
                "kind": "call_id",
                "value": "6a3e0a70-61cf-4a61-9f0e-0c2a1d3b9c11",
            },
            {"system": "twilio", "kind": "call_sid", "value": "CA0000000000000000000000000000000"},
        ],
        ingest_warnings=_warnings(),
    )
    doc.update(over)
    return doc


@valid("Run", "maximal")
def _run_maximal() -> Doc:
    return _run("T1", "recorded", "human", privacy_obtained(), "plain")


@valid("Run", "maximal-t2-not-obtained")
def _run_t2() -> Doc:
    return _run("T2", "recorded", "human", privacy_not_obtained(), "keyed")


@valid("Run", "maximal-t0-unknown-consent")
def _run_t0() -> Doc:
    return _run("T0", "recorded", "human", privacy_unknown(), "plain")


@valid("Run", "maximal-simulated")
def _run_simulated() -> Doc:
    return _run("T1", "simulated", "simulated", dict(PRIVACY_NONE), "keyed")


@valid("Run", "maximal-simulated-recorded")
def _run_recorded_simulated_caller() -> Doc:
    return _run("T2", "recorded", "simulated", None, "plain")


# ------------------------------------------------------------------ Dataset and DatasetItem


@valid("Dataset", "minimal")
def _dataset_minimal() -> Doc:
    return sealed("Dataset", head("dts", version=1, name="Empty set", item_ids=[]))


@valid("Dataset", "maximal")
def _dataset_maximal() -> Doc:
    return sealed(
        "Dataset",
        full_head(
            "dts",
            version=5,
            name="Refund calls",
            item_ids=[b2.new_id("dti"), b2.new_id("dti"), b2.new_id("dti")],
        ),
    )


@valid("DatasetItem", "minimal")
def _item_minimal() -> Doc:
    return head("dti", language="hi-IN")


def _item(privacy: Doc, **over: Any) -> Doc:
    doc = full_head(
        "dti",
        language="mr-IN",
        artifact_id=b2.new_id("art"),
        transcript_id=b2.new_id("tsc"),
        source_run_id=b2.new_id("run"),
        privacy=privacy,
    )
    doc.update(over)
    return doc


@valid("DatasetItem", "maximal")
def _item_maximal() -> Doc:
    return _item(privacy_obtained())


@valid("DatasetItem", "maximal-consent-not-obtained")
def _item_not_obtained() -> Doc:
    return _item(privacy_not_obtained(), source_run_id=None)


@valid("DatasetItem", "maximal-consent-unknown")
def _item_unknown() -> Doc:
    return _item(privacy_unknown(), transcript_id=None)


@valid("DatasetItem", "maximal-synthetic")
def _item_synthetic() -> Doc:
    return _item(dict(PRIVACY_NONE), artifact_id=None)


# ------------------------------------------------------------------------- ConfigSnapshot


def _snapshot(with_profile: bool, catalog_scheme: str | None, full: bool) -> Doc:
    parts = b3.snapshot_parts(with_profile=with_profile)
    over: Doc = {}
    if catalog_scheme is not None:
        digest = (
            plain_digest(hexd("catalog"))
            if catalog_scheme == "plain"
            else keyed_digest(hexd("catalog"))
        )
        over["pricing_catalog"] = {"id": b2.new_id("pcat"), "version": 2, "content_digest": digest}
    if full:
        over["metadata"] = {"source": "golden fixture", "labels": ["synthetic"], "n": 3}
    return b3.snapshot_doc(parts, **over)


@valid("ConfigSnapshot", "minimal")
def _snapshot_minimal() -> Doc:
    return _snapshot(False, None, False)


@valid("ConfigSnapshot", "maximal")
def _snapshot_maximal() -> Doc:
    return _snapshot(True, "plain", True)


@valid("ConfigSnapshot", "maximal-keyed-catalog")
def _snapshot_keyed() -> Doc:
    return _snapshot(True, "keyed", True)


# ------------------------------------------------------------------------------ UsageRecord

UNITS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "audio_input_seconds",
    "audio_output_seconds",
    "input_characters",
    "requests",
)


@valid("UsageRecord", "minimal")
def _usage_minimal() -> Doc:
    return head(
        "use",
        run_id=b2.new_id("run"),
        domain="evaluation",
        stage="judge",
        provider="openai",
        quantities=[{"unit": "input_tokens", "count": 1000}],
        source="provider_reported",
    )


def _usage(stage: str, domain: str, source: str, quantities: list[Doc], **over: Any) -> Doc:
    doc = full_head(
        "use",
        run_id=b2.new_id("run"),
        domain=domain,
        stage=stage,
        provider="sarvam",
        model="saaras:v4",
        provider_call_ref={"system": "sarvam", "kind": "request_id", "value": "req-8842-aa"},
        occurred_at="2026-10-23T12:00:05.500000Z",
        quantities=quantities,
        source=source,
        raw={"usage": {"prompt_tokens": 1000, "details": {"cached": 0}}, "model": "x"},
    )
    doc.update(over)
    return doc


@valid("UsageRecord", "maximal")
def _usage_maximal() -> Doc:
    return _usage(
        "judge",
        "evaluation",
        "provider_reported",
        [
            {"unit": "input_tokens", "count": 1000},
            {"unit": "cache_read_input_tokens", "count": 250},
            {"unit": "cache_write_input_tokens", "count": 0},
            {"unit": "output_tokens", "count": 200},
            {"unit": "audio_input_seconds", "count": "12.5"},
            {"unit": "audio_output_seconds", "count": "3.25"},
            {"unit": "input_characters", "count": 480},
            {"unit": "requests", "count": 1},
        ],
    )


@valid("UsageRecord", "maximal-stt-measured")
def _usage_stt() -> Doc:
    return _usage(
        "stt", "evaluation", "measured", [{"unit": "audio_input_seconds", "count": "61.04"}]
    )


@valid("UsageRecord", "maximal-llm-telemetry")
def _usage_llm() -> Doc:
    return _usage(
        "llm",
        "agent_under_test",
        "telemetry",
        [{"unit": "input_tokens", "count": 512}, {"unit": "output_tokens", "count": 64}],
    )


@valid("UsageRecord", "maximal-tts-agent")
def _usage_tts() -> Doc:
    return _usage(
        "tts", "agent_under_test", "provider_reported", [{"unit": "input_characters", "count": 220}]
    )


@valid("UsageRecord", "maximal-realtime")
def _usage_realtime() -> Doc:
    return _usage(
        "realtime",
        "agent_under_test",
        "telemetry",
        [
            {"unit": "audio_input_seconds", "count": "30"},
            {"unit": "audio_output_seconds", "count": "28"},
        ],
    )


@valid("UsageRecord", "maximal-sim-caller-llm")
def _usage_sim_llm() -> Doc:
    return _usage(
        "sim_caller_llm",
        "evaluation",
        "provider_reported",
        [{"unit": "output_tokens", "count": 90}],
    )


@valid("UsageRecord", "maximal-sim-caller-tts")
def _usage_sim_tts() -> Doc:
    return _usage(
        "sim_caller_tts", "evaluation", "measured", [{"unit": "input_characters", "count": 120}]
    )


@valid("UsageRecord", "maximal-other")
def _usage_other() -> Doc:
    return _usage("other", "evaluation", "provider_reported", [{"unit": "requests", "count": 3}])


# --------------------------------------------------------------------------- PricingCatalog


def _price(key: str, unit: str, **over: Any) -> Doc:
    doc: Doc = {
        "entry_key": key,
        "provider": "openai",
        "model": "gpt-4.1",
        "aliases": ["gpt-4.1-2026-04-14"],
        "unit": unit,
        "unit_quantity": "1000000",
        "price": {"amount": "0.15", "currency": "USD"},
        "billing": {"increment": "none", "minimum": "none"},
        "effective_from": "2026-01-01T00:00:00.000000Z",
        "effective_to": "2027-01-01T00:00:00.000000Z",
        "applies_when": [{"kind": "context_tokens_le", "value": 128000}],
        "price_source": "catalog",
        "provenance": {
            "kind": "provider_price_page",
            "url": "https://example.com/pricing",
            "retrieved_at": "2026-10-01T00:00:00.000000Z",
            "note": "price page captured by hand",
        },
    }
    doc.update(over)
    return doc


@valid("PricingCatalog", "minimal")
def _catalog_minimal() -> Doc:
    return sealed(
        "PricingCatalog",
        head(
            "pcat",
            version=1,
            entries=[
                {
                    "entry_key": "price-00001",
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "unit": "input_tokens",
                    "unit_quantity": "1000000",
                    "price": {"amount": "0.15", "currency": "USD"},
                    "billing": "unknown",
                    "effective_from": "2026-01-01T00:00:00.000000Z",
                    "price_source": "catalog",
                    "provenance": {
                        "kind": "provider_price_page",
                        "url": "https://example.com/pricing",
                        "retrieved_at": "2026-10-01T00:00:00.000000Z",
                    },
                }
            ],
            published_at="2026-10-01T00:00:00.000000Z",
        ),
    )


@valid("PricingCatalog", "maximal")
def _catalog_maximal() -> Doc:
    note = "converted at 83.10 INR per USD on 2026-10-01"
    entries = [
        _price("in-small", "input_tokens"),
        _price(
            "in-large",
            "input_tokens",
            applies_when=[
                {"kind": "context_tokens_gt", "value": 128000},
                {"kind": "region", "value": "us-east-1"},
            ],
            price={"amount": "0.3", "currency": "USD"},
        ),
        _price(
            "cache-read", "cache_read_input_tokens", applies_when=[{"kind": "batch", "value": True}]
        ),
        _price(
            "cache-write",
            "cache_write_input_tokens",
            applies_when=[{"kind": "batch", "value": False}],
        ),
        _price("out", "output_tokens", billing="unknown", applies_when=None, aliases=[]),
        _price(
            "audio-in",
            "audio_input_seconds",
            unit_quantity="60",
            billing={"increment": "15", "minimum": {"amount": "0.01", "currency": "USD"}},
            price_source="user_supplied",
            provider="sarvam",
            model="saaras:v4",
            provenance={
                "kind": "user_note",
                "url": None,
                "retrieved_at": "2026-10-01T00:00:00.000000Z",
                "note": note,
            },
        ),
        _price(
            "audio-out",
            "audio_output_seconds",
            unit_quantity="60",
            billing={"increment": "0.5", "minimum": "none"},
            provider="sarvam",
            model="bulbul:v2",
        ),
        _price(
            "chars",
            "input_characters",
            unit_quantity="10000",
            billing={"increment": "none", "minimum": {"amount": "0.001", "currency": "USD"}},
            provider="sarvam",
            model="bulbul:v2",
        ),
        _price("reqs", "requests", unit_quantity="1000", provider="sarvam", model="saaras:v4"),
    ]
    return sealed(
        "PricingCatalog",
        full_head("pcat", version=2, entries=entries, published_at="2026-10-02T08:30:00.000000Z"),
    )


# ------------------------------------------------------------------------------ CostRecord


def _snapshot_ref(scheme: str) -> Doc:
    digest = (
        plain_digest(hexd("catalog-2")) if scheme == "plain" else keyed_digest(hexd("catalog-2"))
    )
    return {
        "catalog_id": b2.new_id("pcat"),
        "version": 2,
        "content_digest": digest,
        "entry_keys": ["in-small", "out"],
    }


@valid("CostRecord", "minimal")
def _cost_minimal() -> Doc:
    return head(
        "cst",
        domain="evaluation",
        status="not_available",
        usage_record_ids=[],
        na_reason="no_usage",
    )


@valid("CostRecord", "maximal")
def _cost_maximal() -> Doc:
    return full_head(
        "cst",
        domain="evaluation",
        usage_record_ids=[b2.new_id("use"), b2.new_id("use")],
        status="available",
        amount={"amount": "0.18518505", "currency": "USD"},
        basis="computed",
        pricing_snapshot=_snapshot_ref("plain"),
        rounding={"scale": 18, "mode": "half_even", "exact": False},
        unpriced_units=[],
    )


@valid("CostRecord", "maximal-computed-keyed-agent")
def _cost_computed_keyed() -> Doc:
    return full_head(
        "cst",
        domain="agent_under_test",
        usage_record_ids=[b2.new_id("use")],
        status="available",
        amount={"amount": "12.5", "currency": "USD"},
        basis="computed",
        pricing_snapshot=_snapshot_ref("keyed"),
        unpriced_units=[],
    )


@valid("CostRecord", "maximal-reported-provider")
def _cost_reported_provider() -> Doc:
    return full_head(
        "cst",
        domain="evaluation",
        usage_record_ids=[b2.new_id("use")],
        status="available",
        amount={"amount": "1.5", "currency": "USD"},
        basis="reported",
        reported_by="provider",
        unpriced_units=[],
    )


@valid("CostRecord", "maximal-reported-platform")
def _cost_reported_platform() -> Doc:
    return full_head(
        "cst",
        domain="agent_under_test",
        usage_record_ids=[],
        status="available",
        amount={"amount": "0", "currency": "USD"},
        basis="reported",
        reported_by="telemetry_platform",
        unpriced_units=[],
    )


def _na(reason: str, **over: Any) -> Doc:
    doc = full_head(
        "cst",
        domain="evaluation",
        usage_record_ids=[b2.new_id("use")],
        status="not_available",
        na_reason=reason,
        unpriced_units=[],
    )
    doc.update(over)
    return doc


def _raw(by: str, amount: str, currency: str | None, source: str) -> Doc:
    return {
        "reported_by": by,
        "amount_raw": amount,
        "currency_raw": currency,
        "currency_source": source,
    }


@valid("CostRecord", "maximal-na-no-price-for-model")
def _cost_na_no_price() -> Doc:
    return _na("no_price_for_model")


@valid("CostRecord", "maximal-na-unit-unpriced")
def _cost_na_unit_unpriced() -> Doc:
    return _na("unit_unpriced", unpriced_units=list(UNITS))


@valid("CostRecord", "maximal-na-no-usage")
def _cost_na_no_usage() -> Doc:
    return _na("no_usage", usage_record_ids=[], domain="agent_under_test")


@valid("CostRecord", "maximal-na-ambiguous-price")
def _cost_na_ambiguous() -> Doc:
    return _na("ambiguous_price")


@valid("CostRecord", "maximal-na-billing-rules-missing")
def _cost_na_billing() -> Doc:
    return _na("billing_rules_missing")


@valid("CostRecord", "maximal-na-usage-not-reported")
def _cost_na_not_reported() -> Doc:
    return _na("usage_not_reported", usage_record_ids=[])


@valid("CostRecord", "maximal-na-unsupported-currency")
def _cost_na_unsupported() -> Doc:
    return _na(
        "unsupported_currency", reported_raw=_raw("provider", "12.50", "EUR", "api_contract")
    )


@valid("CostRecord", "maximal-na-currency-not-reported")
def _cost_na_currency_missing() -> Doc:
    return _na(
        "currency_not_reported", reported_raw=_raw("telemetry_platform", "12.50", None, "none")
    )


@valid("CostRecord", "maximal-na-amount-unparseable")
def _cost_na_unparseable() -> Doc:
    return _na("amount_unparseable", reported_raw=_raw("provider", "abc", "USD", "payload"))


@valid("CostRecord", "maximal-na-currency-conflict")
def _cost_na_conflict() -> Doc:
    return _na("currency_conflict", reported_raw=_raw("provider", "12.50", "USD", "payload"))


# ----------------------------------------------------------------------------- CostSummary


@valid("CostSummary", "minimal")
def _summary_minimal() -> Doc:
    return head("csum", domain="evaluation", included=[], excluded=[], completeness="none")


@valid("CostSummary", "maximal")
def _summary_maximal() -> Doc:
    included = [b2.new_id("cst"), b2.new_id("cst")]
    reasons = (
        "no_price_for_model",
        "unit_unpriced",
        "no_usage",
        "ambiguous_price",
        "billing_rules_missing",
        "usage_not_reported",
        "unsupported_currency",
        "currency_not_reported",
        "amount_unparseable",
        "currency_conflict",
        "duplicate_of_reported",
        "overlaps_reported",
    )
    excluded = [
        {
            "ref": b2.new_id("cst"),
            "reason": reason,
            "covered_by": [included[0]] if reason in ("duplicate_of_reported", "no_usage") else [],
        }
        for reason in reasons
    ]
    return full_head(
        "csum",
        domain="evaluation",
        total={"amount": "1.65", "currency": "USD"},
        included=included,
        excluded=excluded,
        completeness="partial",
    )


@valid("CostSummary", "maximal-complete-agent")
def _summary_complete() -> Doc:
    included = [b2.new_id("cst")]
    return full_head(
        "csum",
        domain="agent_under_test",
        total={"amount": "12.5", "currency": "USD"},
        included=included,
        excluded=[
            {"ref": b2.new_id("cst"), "reason": "duplicate_of_reported", "covered_by": included}
        ],
        completeness="complete",
    )


@valid("CostSummary", "maximal-none")
def _summary_none() -> Doc:
    return full_head(
        "csum",
        domain="agent_under_test",
        included=[],
        excluded=[{"ref": b2.new_id("cst"), "reason": "no_usage", "covered_by": []}],
        completeness="none",
    )


# ------------------------------------------------------------------------------ RunBundle


def _bundle_head(**over: Any) -> Doc:
    return head("rbnd", **over)


@valid("RunBundle", "minimal")
def _bundle_minimal() -> Doc:
    parts = b3.snapshot_parts()
    snapshot = b3.snapshot_doc(parts)
    run = head(
        "run",
        project_id=b2.new_id("prj"),
        agent_target=ref("agt"),
        config_snapshot_id=snapshot["id"],
        mode="simulated",
        caller_origin="simulated",
        observed_tier="T0",
    )
    return _bundle_head(run=run, snapshot=snapshot)


WITHHELD_KINDS = (
    ("artifact", "art"),
    ("transcript", "tsc"),
    ("turn", "turn"),
    ("exchange", "exc"),
    ("tool_call", "tcl"),
    ("span", "spn"),
    ("result", "res"),
    ("usage_record", "use"),
    ("cost_record", "cst"),
)


def _withheld() -> list[Doc]:
    return [{"kind": kind, "record_id": b2.new_id(prefix)} for kind, prefix in WITHHELD_KINDS]


@valid("RunBundle", "maximal")
def _bundle_maximal() -> Doc:
    world = b7.world(tier="T1", with_profile=True, profile_digest="plain")
    doc = world.copy()
    doc["metadata"] = {"source": "golden fixture", "labels": ["synthetic"]}
    doc["test_profiles"] = [world.parts["profile"].model_dump(mode="json", by_alias=True)]
    doc["withheld"] = _withheld()
    return doc


# ---------------------------------------------------------------------------- ExportBundle


@valid("ExportBundle", "minimal")
def _export_minimal() -> Doc:
    return head("exp", purpose="internal")


KEK_REF_FULL: Doc = {"key_id": "kek-golden-1", "key_version": "3", "provider_hint": "local-kms"}


def _sealed(payload_type: str, subject: str, keyed: bool, **kw: Any) -> Doc:
    extra: dict[str, Any] = {"kek_ref": dict(KEK_REF_FULL)}
    if keyed:
        extra.update(
            digest_key=b7.DIGEST_KEY,
            digest_key_ref={**b7.DIGEST_KEY_REF, "key_version": "2", "provider_hint": "local-kms"},
            digest_subject={"payload": payload_type},
        )
    plaintext = b'{"k": "v"}' if payload_type != "audio" else bytes(range(64)) * 2
    from . import _support_b5 as b5

    doc: Doc = b5.seal(plaintext, payload_type=payload_type, subject_id=subject, **extra, **kw)
    return doc


@valid("ExportBundle", "maximal")
def _export_maximal() -> Doc:
    world = b7.world(tier="T1", with_profile=True, profile_digest="plain")
    rb = world.copy()
    profile = world.parts["profile"]
    rb["test_profiles"] = [profile.model_dump(mode="json", by_alias=True)]
    artifact = rb["artifacts"][0]["id"]
    transcript = rb["transcripts"][0]["id"]
    call0, call1 = rb["tool_calls"][0]["id"], rb["tool_calls"][1]["id"]
    item_a = b7.dataset_item_doc(
        source_run_id=None, privacy=privacy_obtained(), artifact_id=b2.new_id("art")
    )
    item_b = b7.dataset_item_doc(language="mr-IN")
    dataset = b7.dataset_doc([item_a["id"], item_b["id"]])
    return full_head(
        "exp",
        purpose="internal",
        run_bundles=[rb],
        datasets=[dataset],
        dataset_items=[item_a, item_b],
        sealed_payloads=[
            _sealed("audio", artifact, True),
            _sealed("transcript_text", transcript, False),
            _sealed("tool_arguments", call0, True),
            _sealed("tool_result", call1, False),
            _sealed("test_profile", str(profile.id), True, subject_version=profile.version),
        ],
        payload_redactions=[
            b7.redaction_entry(artifact, ["audio", "text"]),
            b7.redaction_entry(call1, ["tool_arguments", "tool_result"]),
        ],
        provenance_redactions=[
            {"item_id": item_a["id"], "field": "source_run_id", "reason": "unresolved_reference"}
        ],
    )


@valid("ExportBundle", "maximal-shared-export")
def _export_shared() -> Doc:
    doc = b7.shared_ready("shared_export")
    doc["metadata"] = {"source": "golden fixture"}
    return doc


@valid("ExportBundle", "maximal-benchmark")
def _export_benchmark() -> Doc:
    return b7.shared_ready("benchmark")
