# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 12 (P1-16): the invalid golden fixtures. No tests here.

Each fixture is a valid document of ``_support_b9_valid`` with one deliberate defect, registered
with the location (pydantic ``loc``, empty for a model-level rule) and the error type of the first
error the model reports. A typed error of the package that pydantic does not wrap (a digest
mismatch, a version error) is registered with an empty path and its class name. A bundle rule is
registered with its bracketed tag.

``render_all()`` refuses to build when a model reports anything else, so a fixture can never claim
a rejection the model does not make.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from . import _support_b2 as b2
from ._support import api
from ._support_b9 import VALID, DocBuilder, dump, entity_models, invalid

Doc = dict[str, Any]
Path = tuple[str | int, ...]


class Set:
    """Replace (or add) the value at ``path`` (``to`` may be a function, called in the fixture's
    deterministic scope: use it for new IDs)."""

    def __init__(self, *path: str | int, to: Any) -> None:
        self.path, self.value = path, to


class Copy:
    """Set the member at ``path`` to a copy of the value found at ``source``."""

    def __init__(self, *path: str | int, source: Path) -> None:
        self.path, self.source = path, source


class Drop:
    """Remove the member at ``path``."""

    def __init__(self, *path: str | int) -> None:
        self.path = path


Edit = Set | Drop | Copy


def _walk(doc: Any, path: Path) -> Any:
    for step in path:
        doc = doc[step]
    return doc


def apply(doc: Doc, edits: Sequence[Edit]) -> Doc:
    out = copy.deepcopy(doc)
    for edit in edits:
        parent = _walk(out, edit.path[:-1])
        if isinstance(edit, Drop):
            del parent[edit.path[-1]]
        elif isinstance(edit, Copy):
            parent[edit.path[-1]] = copy.deepcopy(_walk(out, edit.source))
        else:
            value = edit.value() if callable(edit.value) else edit.value
            parent[edit.path[-1]] = copy.deepcopy(value)
    return out


def reseal(entity: str, doc: Doc) -> Doc:
    """``doc`` with a ``content_digest`` that matches its (invalid) content, so that the digest
    check does not hide the defect the fixture is about."""
    digest_api = api("digest")
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    if entity == "Evaluator":
        subject: Any = api("evaluator").evaluator_digest_domain(body)
    elif entity == "ConfigSnapshot":
        digest = api("snapshot").compute_snapshot_digest(body)
        return {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}
    else:
        subject = body
    digest = digest_api.compute_digest(subject)
    return {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}


def defect(
    entity: str,
    base: str,
    *edits: Edit,
    seal: bool = False,
) -> DocBuilder:
    """Builder of the valid document ``base`` of ``entity`` with ``edits`` applied (and a fresh
    digest when ``seal``)."""

    def build() -> Doc:
        model = entity_models()[entity]
        doc = dump(model, VALID[entity][base]())
        doc = apply(doc, edits)
        return reseal(entity, doc) if seal else doc

    return build


def bad(
    entity: str,
    name: str,
    base: str,
    edits: Sequence[Edit],
    path: Sequence[str | int],
    kind: str,
    why: str,
    *,
    tag: str | None = None,
    seal: bool = False,
) -> None:
    invalid(entity, name, defect(entity, base, *edits, seal=seal), path, kind, why, tag)


def bad_world(
    entity: str,
    name: str,
    build: DocBuilder,
    path: Sequence[str | int],
    kind: str,
    why: str,
    *,
    tag: str | None = None,
) -> None:
    invalid(entity, name, build, path, kind, why, tag)


MISSING = "missing"
EXTRA = "extra_forbidden"
PATTERN = "string_pattern_mismatch"

# ---------------------------------------------------------------------------------- helpers

VERSION_TOO_NEW = "SchemaVersionTooNew"
VERSION_OLDER = "SchemaVersionMismatch"
DIGEST_MISMATCH = "DigestMismatch"
GREATER_EQUAL = "greater_than_equal"
STRING_TYPE = "string_type"
BOOL_TYPE = "bool_type"
TOO_SHORT = "too_short"
VALUE_ERROR = "value_error"


def new_id(prefix: str) -> Any:
    return lambda: b2.new_id(prefix)


def flip_digest() -> Any:
    """A different, well-formed digest value."""
    return "0" * 64


def extra(entity: str, base: str = "minimal") -> None:
    bad(
        entity,
        "extra-member",
        base,
        [Set("unexpected_member", to=1)],
        ["unexpected_member"],
        EXTRA,
        "an unknown member is rejected, not ignored (extra=forbid)",
    )


def newer(entity: str, base: str = "minimal") -> None:
    bad(
        entity,
        "schema-version-newer",
        base,
        [Set("schema_version", to="0.0.2")],
        [],
        VERSION_TOO_NEW,
        "a document of a newer schema version is refused, not read with dropped members",
        seal=entity in _VERSIONED,
    )


def older(entity: str, base: str = "minimal") -> None:
    bad(
        entity,
        "schema-version-older",
        base,
        [Set("schema_version", to="0.0.0")],
        [],
        VERSION_OLDER,
        "while the version is 0.0.x an older document needs an exact match",
        seal=entity in _VERSIONED,
    )


def tampered(entity: str, base: str = "maximal") -> None:
    bad(
        entity,
        "content-digest-mismatch",
        base,
        [Set("content_digest", "value", to=flip_digest())],
        [],
        DIGEST_MISMATCH,
        "the content_digest is checked on load against the content",
    )


_VERSIONED = frozenset(
    {
        "AgentTarget",
        "Persona",
        "TestProfile",
        "MetricDefinition",
        "MetricBinding",
        "Rubric",
        "Evaluator",
        "Dataset",
        "PricingCatalog",
    }
)

# ---------------------------------------------------------------------------------- Project

extra("Project")
bad("Project", "missing-name", "minimal", [Drop("name")], ["name"], MISSING, "name is required")
bad(
    "Project",
    "wrong-id-kind",
    "minimal",
    [Set("id", to=new_id("agt"))],
    ["id", "is-instance[ProjectId]"],
    "is_instance_of",
    "an id of another kind (agt_) is not a project id",
)
bad(
    "Project",
    "config-ref-without-scheme",
    "maximal",
    [Set("provider_configs", 0, "config_ref", to="no-scheme-here")],
    ["provider_configs", 0, "config_ref"],
    "config_ref",
    "config_ref is <scheme>:<name>",
)
newer("Project")
older("Project")

# ------------------------------------------------------------------------------- AgentTarget

extra("AgentTarget")
bad(
    "AgentTarget",
    "version-zero",
    "minimal",
    [Set("version", to=0)],
    ["version"],
    GREATER_EQUAL,
    "versions start at 1",
    seal=True,
)
bad(
    "AgentTarget",
    "unknown-endpoint-transport",
    "minimal",
    [Set("endpoint", to={"transport": "carrier-pigeon", "url": "wss://agent.example.com/ws"})],
    ["endpoint"],
    "union_tag",
    "the endpoint is a ws, webrtc or sip endpoint",
    seal=True,
)
bad(
    "AgentTarget",
    "unknown-capability",
    "maximal",
    [Set("declared_capabilities", 0, to="hearing")],
    ["declared_capabilities", 0],
    "closed_set",
    "a capability is one of the input kinds",
    seal=True,
)
tampered("AgentTarget")

# ---------------------------------------------------------------------------------- Persona

bad(
    "Persona",
    "language-not-supported",
    "minimal",
    [Set("language", to="fr-FR")],
    ["language"],
    "supported_language",
    "only the nine supported languages can be configured",
    seal=True,
)
bad(
    "Persona",
    "language-region-not-supported",
    "minimal",
    [Set("language", to="en-AU")],
    ["language"],
    "supported_language",
    "regions are limited to IN, US and GB",
    seal=True,
)
bad(
    "Persona",
    "accent-with-bidi-override",
    "maximal",
    [Set("accent", to="Delhi\u202e")],
    ["accent"],
    "human_label_character",
    "a label rejects bidi override characters",
    seal=True,
)
tampered("Persona")
older("Persona")

# ------------------------------------------------------------------------------- TestProfile

bad(
    "TestProfile",
    "empty-name",
    "minimal",
    [Set("name", to="")],
    ["name"],
    TOO_SHORT,
    "profile values are non-empty",
    seal=True,
)
bad(
    "TestProfile",
    "missing-account-number",
    "minimal",
    [Drop("account_number")],
    ["account_number"],
    MISSING,
    "every profile field is required",
    seal=True,
)
extra("TestProfile")
tampered("TestProfile")

# ------------------------------------------------------------------------- MetricDefinition

bad(
    "MetricDefinition",
    "judge-without-prompt",
    "maximal-judge-boolean",
    [Drop("prompt_template")],
    ["prompt_template"],
    "judge_field",
    "an llm_judge metric needs its prompt template",
    seal=True,
)
bad(
    "MetricDefinition",
    "numeric-without-unit",
    "maximal-latency-ms",
    [Set("unit", to=None)],
    ["unit"],
    "unit",
    "a numeric metric needs a unit",
    seal=True,
)
bad(
    "MetricDefinition",
    "range-rule-without-direction",
    "maximal-latency-ms",
    [Set("pass_rule", to={"kind": "range", "inclusive": True})],
    ["pass_rule", "range"],
    VALUE_ERROR,
    "a range rule needs min, max or both",
    seal=True,
)
bad(
    "MetricDefinition",
    "language-independent-but-script-sensitive",
    "minimal",
    [Set("script_sensitive", to=True)],
    ["languages"],
    "language_independent_script_sensitive",
    "a metric that is independent of language cannot depend on script",
    seal=True,
)
bad(
    "MetricDefinition",
    "prompt-variables-differ-from-template",
    "maximal-judge-boolean",
    [Set("prompt_variables", to=["transcript"])],
    ["prompt_variables"],
    "prompt_variables",
    "the declared variables equal the variables used in the template",
    seal=True,
)
tampered("MetricDefinition")

# --------------------------------------------------------------------------- MetricBinding

extra("MetricBinding")
bad(
    "MetricBinding",
    "missing-params",
    "minimal",
    [Drop("params")],
    ["params"],
    MISSING,
    "params is required, an empty object when nothing is set",
    seal=True,
)
bad(
    "MetricBinding",
    "enabled-is-not-a-boolean",
    "minimal",
    [Set("enabled", to="yes")],
    ["enabled"],
    BOOL_TYPE,
    "enabled is a JSON boolean, never text",
    seal=True,
)
bad(
    "MetricBinding",
    "metric-version-zero",
    "maximal",
    [Set("metric", "version", to=0)],
    ["metric", "version"],
    GREATER_EQUAL,
    "versions start at 1",
    seal=True,
)
tampered("MetricBinding")

# ------------------------------------------------------------------------------------ Rubric

bad(
    "Rubric",
    "no-items",
    "minimal",
    [Set("items", to=[])],
    ["items"],
    TOO_SHORT,
    "a rubric has at least one item",
    seal=True,
)
bad(
    "Rubric",
    "weight-zero",
    "maximal",
    [Set("items", 0, "weight", to="0")],
    ["items", 0, "weight"],
    "weight_not_positive",
    "a weight is strictly positive",
    seal=True,
)
bad(
    "Rubric",
    "weight-not-canonical",
    "maximal",
    [Set("items", 1, "weight", to="2.50")],
    ["items", 1, "weight"],
    "decimal_not_canonical",
    "a weight is a canonical decimal string",
    seal=True,
)
bad(
    "Rubric",
    "no-required-item",
    "minimal",
    [Set("items", 0, "required", to=False)],
    ["items"],
    VALUE_ERROR,
    "a rubric has at least one required item",
    seal=True,
)
tampered("Rubric")

# ---------------------------------------------------------------------------------- Evaluator

bad(
    "Evaluator",
    "unknown-profile-placeholder",
    "maximal",
    [Set("instructions", to="Say {{profile.ssn}} to the agent.")],
    ["instructions"],
    "value_error",
    "free text may name only fields of a test profile",
    seal=True,
)
bad(
    "Evaluator",
    "placeholder-without-test-profile",
    "minimal",
    [Set("instructions", to="Greet {{profile.name}}.")],
    ["test_profile"],
    "test_profile_required",
    "an evaluator with placeholders references the profile",
    seal=True,
)
bad(
    "Evaluator",
    "duration-over-four-hours",
    "minimal",
    [Set("max_duration_ms", to=14_400_001)],
    ["max_duration_ms"],
    "less_than_equal",
    "the longest test is four hours",
    seal=True,
)
bad(
    "Evaluator",
    "scripted-turn-index-gap",
    "maximal",
    [Set("scripted_turns", 1, "index", to=5)],
    ["scripted_turns"],
    VALUE_ERROR,
    "scripted turn indexes run 0, 1, 2, ...",
    seal=True,
)
bad(
    "Evaluator",
    "name-only-call-with-arguments",
    "maximal",
    [Set("expected_tool_calls", 0, "arguments", to={"a": 1})],
    ["expected_tool_calls", 0, "arguments"],
    "arguments",
    "match=name_only compares the tool name only",
    seal=True,
)
bad(
    "Evaluator",
    "exhaustive-flag-is-a-string",
    "maximal",
    [Set("expected_tool_calls_exhaustive", to="true")],
    ["expected_tool_calls_exhaustive"],
    "bool_type",
    "expected_tool_calls_exhaustive is a strict boolean or null, never a string",
    seal=True,
)
bad(
    "Evaluator",
    "no-metric-binding",
    "minimal",
    [Set("metric_bindings", to=[])],
    ["metric_bindings"],
    TOO_SHORT,
    "an evaluator has at least one binding",
    seal=True,
)
tampered("Evaluator")

# ---------------------------------------------------------------------------------- Artifact

extra("Artifact")
bad(
    "Artifact",
    "sha256-uppercase",
    "minimal",
    [Set("sha256", to="AB" * 32)],
    ["sha256"],
    "sha256_hex",
    "a SHA-256 is 64 lower-case hex digits",
)
bad(
    "Artifact",
    "uri-scheme-not-allowed",
    "minimal",
    [Set("uri", to="ftp://example.com/a.wav")],
    ["uri"],
    "url_scheme",
    "artifact URIs use http, https, s3, gs or file",
)
bad(
    "Artifact",
    "human-subject-without-consent",
    "maximal",
    [Set("privacy", "consent", to=None)],
    ["privacy", "consent"],
    "privacy_marker",
    "a human-subject marker records its consent",
)
bad(
    "Artifact",
    "negative-size",
    "minimal",
    [Set("size", to=-1)],
    ["size"],
    GREATER_EQUAL,
    "a size is a count of bytes",
)
bad(
    "Artifact",
    "uri-with-userinfo",
    "minimal",
    [Set("uri", to="https://recordings.example.com@other.example.net/a.wav")],
    ["uri"],
    "url_userinfo",
    "a URI has no userinfo before the host (it can hide the real host)",
)

# -------------------------------------------------------------------------------- Transcript

extra("Transcript")
bad(
    "Transcript",
    "declared-language-on-own-stt",
    "maximal-agent-export-declared",
    [Set("source", to="our_stt")],
    ["declared_language"],
    "declared_language",
    "a declaration belongs to an imported transcript",
)
bad(
    "Transcript",
    "declared-language-with-detected-tag",
    "maximal-agent-export-declared",
    [Set("detected_language", to="mr-IN")],
    ["declared_language"],
    "declared_language",
    "a declaration is allowed only when the transcript has no language label",
)
bad(
    "Transcript",
    "timeline-artifact-without-art-id",
    "maximal",
    [Set("timeline_origin", "ref", to=None)],
    ["timeline_origin", "ref"],
    "timeline_origin",
    "kind=artifact needs the art_ id",
)
bad(
    "Transcript",
    "synthetic-with-consent",
    "maximal",
    [
        Set(
            "privacy",
            to={
                "human_subject": False,
                "consent": {"status": "obtained", "reference": None, "recorded_at": None},
                "retention": None,
                "human_subject_reason": None,
            },
        )
    ],
    ["privacy", "consent"],
    "privacy_marker",
    "a synthetic marker carries no consent",
)

# ------------------------------------------------------------------------------------- Turn

extra("Turn")
older("Turn")
bad(
    "Turn",
    "timing-none-with-offsets",
    "maximal",
    [Set("timing_source", to="none")],
    ["timing_source"],
    "timing_source",
    "timing_source=none means no offsets",
)
bad(
    "Turn",
    "end-before-start",
    "maximal",
    [Set("end_ms", to=500)],
    ["end_ms"],
    "time_order",
    "end_ms must not be before start_ms",
)
bad(
    "Turn",
    "bidi-flag-contradicts-text",
    "maximal",
    [Set("contains_bidi_controls", to=False)],
    ["contains_bidi_controls"],
    "bidi_flag",
    "the stored flag equals the computed one",
)
bad(
    "Turn",
    "role-not-known",
    "minimal",
    [Set("role", to="bot")],
    ["role"],
    "closed_set",
    "a role is caller, agent or unknown",
)
bad(
    "Turn",
    "segment-outside-turn",
    "maximal",
    [Set("segments", 0, "end_ms", to=9000)],
    ["segments"],
    "segment_bounds",
    "a segment lies inside its turn",
)

# ----------------------------------------------------------------------------------- Exchange

extra("Exchange")
bad(
    "Exchange",
    "negative-index",
    "minimal",
    [Set("index", to=-1)],
    ["index"],
    GREATER_EQUAL,
    "an exchange index is a count",
)
bad(
    "Exchange",
    "unknown-source",
    "minimal",
    [Set("source", to="guess")],
    ["source"],
    "closed_set",
    "the source is one of the four evidence kinds",
)
bad(
    "Exchange",
    "duration-as-text",
    "maximal",
    [Set("duration_ms", to="4200")],
    ["duration_ms"],
    "int_type",
    "durations are integers",
)

# ------------------------------------------------------------------------------ ToolCallEvent

extra("ToolCallEvent")
bad(
    "ToolCallEvent",
    "parsed-arguments-of-truncated-call",
    "maximal",
    [Set("arguments_complete", to=False)],
    ["arguments"],
    "tool_arguments",
    "parsed arguments exist only for complete arguments",
)
bad(
    "ToolCallEvent",
    "complete-with-truncated-raw",
    "maximal",
    [Set("arguments_raw", to='{"account": "44001')],
    ["arguments_complete"],
    "tool_arguments",
    "a truncated payload is never complete",
)
bad(
    "ToolCallEvent",
    "result-complete-without-raw",
    "maximal",
    [Set("result_raw", to=None)],
    ["result_complete"],
    "tool_result",
    "result_complete is stated only with result_raw",
)
bad(
    "ToolCallEvent",
    "offset-without-origin",
    "maximal",
    [Set("timeline_origin", to=None)],
    ["timeline_origin"],
    "timeline_origin",
    "an offset needs its timeline origin",
)

# --------------------------------------------------------------------------------- StageSpan

extra("StageSpan")
bad(
    "StageSpan",
    "other-without-raw-label",
    "maximal-other",
    [Set("stage_raw", to=None)],
    ["stage_raw"],
    "stage_raw",
    "stage=other keeps the source label",
)
bad(
    "StageSpan",
    "raw-label-on-known-stage",
    "maximal",
    [Set("stage_raw", to="vad")],
    ["stage_raw"],
    "stage_raw",
    "stage_raw belongs to stage=other only",
)
bad(
    "StageSpan",
    "negative-duration",
    "maximal",
    [Set("duration_ms", to=-5)],
    ["duration_ms"],
    GREATER_EQUAL,
    "a duration is not negative",
)
bad(
    "StageSpan",
    "unknown-stage",
    "minimal",
    [Set("stage", to="vad")],
    ["stage"],
    "closed_set",
    "a stage is from the usage stage set",
)

# ------------------------------------------------------------------------------ MetricResult

MR = "metric_result"
extra("MetricResult")
bad(
    "MetricResult",
    "scored-with-na-reason",
    "maximal",
    [Set("na_reason", to="missing_input")],
    ["na_reason"],
    MR,
    "a scored result has no na_reason",
)
bad(
    "MetricResult",
    "not-applicable-with-value",
    "maximal-na-language-not-observed",
    [Set("value", to=False)],
    ["value"],
    MR,
    "a not_applicable result has no value, never false",
)
bad(
    "MetricResult",
    "not-applicable-without-reason",
    "maximal-na-language-not-observed",
    [Set("na_reason", to=None)],
    ["na_reason"],
    MR,
    "a not_applicable result says why",
)
bad(
    "MetricResult",
    "missing-input-without-kinds",
    "maximal-na-missing-input",
    [Set("missing_kinds", to=[])],
    ["missing_kinds"],
    MR,
    "missing_input lists the kinds that were missing",
)
bad(
    "MetricResult",
    "declared-ids-without-declared-source",
    "maximal-integer-scorer-declared",
    [Set("language_source", to="observed")],
    ["declared_language_transcript_ids"],
    MR,
    "declared transcripts are listed if and only if language_source is declared",
)
bad(
    "MetricResult",
    "share-contradicts-counts",
    "maximal-per-turn-share",
    [Set("per_turn", "share", to="0.5")],
    ["per_turn", "share"],
    "per_turn_share",
    "the share equals passed / (passed + failed)",
)
bad(
    "MetricResult",
    "error-without-error-object",
    "maximal-error",
    [Set("error", to=None)],
    ["error"],
    MR,
    "an error result carries its structured error",
)

# ------------------------------------------------------------------------------- RunVerdict

RV = "run_verdict"
extra("RunVerdict")
newer("RunVerdict")
bad(
    "RunVerdict",
    "counts-do-not-sum",
    "minimal",
    [Set("passed", to=3)],
    ["required_total"],
    RV,
    "required_total equals the four counts",
)
bad(
    "RunVerdict",
    "status-contradicts-counts",
    "minimal",
    [Set("status", to="fail")],
    ["status"],
    RV,
    "fail needs a failed check",
)
bad(
    "RunVerdict",
    "score-without-coverage",
    "maximal",
    [Set("weight_coverage", to=None)],
    ["weight_coverage"],
    RV,
    "a weighted score is stored with its coverage",
)
bad(
    "RunVerdict",
    "score-above-one",
    "maximal",
    [Set("weighted_score", to="1.5")],
    ["weighted_score"],
    RV,
    "a weighted score lies in [0, 1]",
)
bad(
    "RunVerdict",
    "negative-count",
    "minimal",
    [Set("failed", to=-1)],
    ["failed"],
    GREATER_EQUAL,
    "a count is not negative",
)

# ----------------------------------------------------------------------------------------- Run

extra("Run")
older("Run")
bad(
    "Run",
    "simulated-run-with-human-caller",
    "minimal",
    [Set("caller_origin", to="human")],
    ["caller_origin"],
    "caller_origin",
    "a simulated run is driven by our runner",
)
bad(
    "Run",
    "spans-in-a-tier-zero-run",
    "minimal",
    [Set("available_inputs", to=["spans"])],
    ["observed_tier"],
    "observed_tier",
    "spans come only from telemetry or an observer (rule R1)",
)
bad(
    "Run",
    "agent-audio-without-channel-map",
    "minimal",
    [Set("available_inputs", to=["agent_audio"])],
    ["available_inputs"],
    "agent_audio",
    "agent audio needs a channel map that identifies the agent",
)
bad(
    "Run",
    "available-input-listed-twice",
    "minimal",
    [Set("available_inputs", to=["transcript", "transcript"])],
    ["available_inputs"],
    VALUE_ERROR,
    "an input kind is listed once",
)
bad(
    "Run",
    "tier-not-known",
    "minimal",
    [Set("observed_tier", to="T3")],
    ["observed_tier"],
    "closed_set",
    "the tiers are T0, T1 and T2",
)

# --------------------------------------------------------------------- Dataset, DatasetItem

bad(
    "Dataset",
    "item-listed-twice",
    "maximal",
    [Copy("item_ids", 1, source=("item_ids", 0))],
    ["item_ids"],
    VALUE_ERROR,
    "a dataset lists an item once",
    seal=True,
)
bad(
    "Dataset",
    "item-id-malformed",
    "maximal",
    [Set("item_ids", 1, to="dti_0000")],
    ["item_ids", 1, "is-instance[DatasetItemId]"],
    "is_instance_of",
    "an item id is a well-formed dti_ id",
    seal=True,
)
bad(
    "Dataset",
    "version-zero",
    "minimal",
    [Set("version", to=0)],
    ["version"],
    GREATER_EQUAL,
    "versions start at 1",
    seal=True,
)
bad(
    "Dataset",
    "empty-name",
    "minimal",
    [Set("name", to="")],
    ["name"],
    "string_too_short",
    "a name is a non-empty label",
    seal=True,
)
tampered("Dataset")

extra("DatasetItem")
bad(
    "DatasetItem",
    "language-not-supported",
    "minimal",
    [Set("language", to="fr-FR")],
    ["language"],
    "supported_language",
    "only the nine supported languages",
)
bad(
    "DatasetItem",
    "artifact-without-privacy-marker",
    "minimal",
    [Set("artifact_id", to=new_id("art"))],
    ["privacy"],
    "privacy",
    "an item that references a recording carries a privacy marker",
)
bad(
    "DatasetItem",
    "item-without-source-run-marked-synthetic",
    "maximal-synthetic",
    [Set("source_run_id", to=None)],
    ["privacy"],
    "privacy",
    "without a source run the marker is human-subject",
)
bad(
    "DatasetItem",
    "transcript-id-of-another-kind",
    "minimal",
    [Set("transcript_id", to=new_id("art"))],
    ["transcript_id", "is-instance[TranscriptId]"],
    "is_instance_of",
    "a transcript_id is a tsc_ id",
)

# -------------------------------------------------------------------------- ConfigSnapshot


def _snapshot_with(edit: Any, over_snapshot: bool = False) -> DocBuilder:
    """The maximal snapshot after ``edit(snapshot_doc)``, with the digests of the edited
    embedded records and of the snapshot itself recomputed, so that only the defect is left."""

    def build() -> Doc:
        model = entity_models()["ConfigSnapshot"]
        doc = dump(model, VALID["ConfigSnapshot"]["maximal"]())
        edit(doc)
        return reseal("ConfigSnapshot", doc)

    return build


def _other_persona(doc: Doc) -> None:
    doc["evaluator"]["persona"]["id"] = b2.new_id("per")
    doc["evaluator"] = reseal("Evaluator", doc["evaluator"])


def _foreign_binding(doc: Doc) -> None:
    doc["metric_bindings"][0]["evaluator_id"] = b2.new_id("evl")
    doc["metric_bindings"][0] = reseal("MetricBinding", doc["metric_bindings"][0])
    reference = doc["evaluator"]["metric_bindings"][0]
    reference["content_digest"] = doc["metric_bindings"][0]["content_digest"]
    doc["evaluator"] = reseal("Evaluator", doc["evaluator"])


extra("ConfigSnapshot")
tampered("ConfigSnapshot")
bad_world(
    "ConfigSnapshot",
    "persona-reference-not-embedded",
    _snapshot_with(_other_persona),
    ["evaluator", "persona"],
    "snapshot_reference",
    "the evaluator's persona reference resolves to the embedded persona",
)
bad_world(
    "ConfigSnapshot",
    "binding-of-another-evaluator",
    _snapshot_with(_foreign_binding),
    ["metric_bindings", 0, "evaluator_id"],
    "snapshot_reference",
    "every embedded binding belongs to the snapshot's evaluator",
)
bad(
    "ConfigSnapshot",
    "no-metric-definitions",
    "minimal",
    [Set("metric_definitions", to=[])],
    ["metric_definitions"],
    TOO_SHORT,
    "a snapshot embeds at least one definition",
)

# ------------------------------------------------------------------------------- UsageRecord

extra("UsageRecord")
bad(
    "UsageRecord",
    "agent-measured-by-us",
    "minimal",
    [Set("domain", to="agent_under_test"), Set("source", to="measured")],
    ["source"],
    "usage_source",
    "usage of the agent under test is never measured by us (P-39)",
)
bad(
    "UsageRecord",
    "no-quantities",
    "minimal",
    [Set("quantities", to=[])],
    ["quantities"],
    TOO_SHORT,
    "a usage record has at least one quantity",
)
bad(
    "UsageRecord",
    "unit-listed-twice",
    "minimal",
    [
        Set(
            "quantities",
            to=[{"unit": "input_tokens", "count": 1}, {"unit": "input_tokens", "count": 2}],
        )
    ],
    ["quantities"],
    VALUE_ERROR,
    "one quantity per unit; repeats are never summed",
)
bad(
    "UsageRecord",
    "fractional-token-count",
    "minimal",
    [Set("quantities", 0, "count", to="1.5")],
    ["quantities", 0, "count"],
    "usage_count",
    "a token count is an integer, never rounded",
)
bad(
    "UsageRecord",
    "negative-token-count",
    "minimal",
    [Set("quantities", 0, "count", to=-1)],
    ["quantities", 0, "count", "constrained-int"],
    GREATER_EQUAL,
    "no quantity is negative",
)

# ---------------------------------------------------------------------------- PricingCatalog

extra("PricingCatalog")
bad(
    "PricingCatalog",
    "no-entries",
    "minimal",
    [Set("entries", to=[])],
    ["entries"],
    TOO_SHORT,
    "a catalog has at least one entry",
    seal=True,
)
bad(
    "PricingCatalog",
    "entry-key-used-twice",
    "maximal",
    [Copy("entries", 1, source=("entries", 0))],
    ["entries"],
    "entry_key",
    "entry keys are unique within a catalog",
    seal=True,
)
bad(
    "PricingCatalog",
    "price-not-in-usd",
    "maximal",
    [Set("entries", 0, "price", "currency", to="EUR")],
    ["entries", 0, "price", "currency"],
    "currency_not_supported",
    "prices are USD only in 0.0.1",
    seal=True,
)
bad(
    "PricingCatalog",
    "effective-range-reversed",
    "maximal",
    [Set("entries", 0, "effective_to", to="2025-01-01T00:00:00.000000Z")],
    ["entries", 0, "effective_to"],
    "effective_range",
    "effective_to is later than effective_from",
    seal=True,
)
bad(
    "PricingCatalog",
    "user-price-without-note",
    "maximal",
    [Set("entries", 5, "provenance", "note", to=None)],
    ["entries", 5, "provenance", "note"],
    "provenance_note",
    "a user-supplied price needs a provenance note",
    seal=True,
)
tampered("PricingCatalog")

# ------------------------------------------------------------------------------- CostRecord

CR = "cost_record"
extra("CostRecord")
bad(
    "CostRecord",
    "available-without-amount",
    "maximal-reported-provider",
    [Set("amount", to=None)],
    ["amount"],
    "cost_amount",
    "an available cost has an amount",
)
bad(
    "CostRecord",
    "unavailable-with-amount",
    "maximal-na-no-usage",
    [Set("amount", to={"amount": "0", "currency": "USD"})],
    ["amount"],
    "cost_amount",
    "a missing cost never carries an amount, not even 0",
)
bad(
    "CostRecord",
    "computed-without-pricing-snapshot",
    "maximal",
    [Set("pricing_snapshot", to=None)],
    ["pricing_snapshot"],
    "cost_pricing_snapshot",
    "a computed cost names the prices it used",
)
bad(
    "CostRecord",
    "amount-not-in-usd",
    "maximal-reported-provider",
    [Set("amount", "currency", to="EUR")],
    ["amount", "currency"],
    "currency_not_supported",
    "money is USD only in 0.0.1",
)
bad(
    "CostRecord",
    "negative-amount",
    "maximal-reported-provider",
    [Set("amount", "amount", to="-1")],
    ["amount"],
    VALUE_ERROR,
    "a cost is not negative",
)

# ------------------------------------------------------------------------------ CostSummary

CS = "cost_summary"
extra("CostSummary")
bad(
    "CostSummary",
    "total-without-included-record",
    "minimal",
    [Set("total", to={"amount": "1", "currency": "USD"})],
    ["total"],
    "total",
    "a total exists only when something is included",
)
bad(
    "CostSummary",
    "completeness-contradicts-exclusions",
    "maximal-complete-agent",
    [Set("completeness", to="partial")],
    ["completeness"],
    "completeness",
    "complete means every exclusion is covered by an included record",
)
bad(
    "CostSummary",
    "duplicate-without-covering-record",
    "maximal-complete-agent",
    [Set("excluded", 0, "covered_by", to=[]), Set("completeness", to="partial")],
    ["excluded", 0, "covered_by"],
    "covered_by",
    "duplicate_of_reported names the included record that covers it",
)
bad(
    "CostSummary",
    "total-not-in-usd",
    "maximal-complete-agent",
    [Set("total", "currency", to="EUR")],
    ["total", "currency"],
    "currency_not_supported",
    "money is USD only in 0.0.1",
)

# --------------------------------------------------------------------------------- RunBundle

VE = VALUE_ERROR
T1_INPUTS = ["audio", "agent_audio", "transcript", "tool_calls", "tool_results", "usage"]

extra("RunBundle", "minimal")
bad(
    "RunBundle",
    "tier-below-the-evidence",
    "maximal",
    [Set("run", "observed_tier", to="T0"), Set("run", "available_inputs", to=T1_INPUTS)],
    [],
    VE,
    "a stored tier below what the records show is never excused",
    tag="tier",
)
bad(
    "RunBundle",
    "record-twice",
    "maximal",
    [Copy("turns", 1, source=("turns", 0))],
    [],
    VE,
    "one record id appears once in a run bundle",
    tag="duplicate",
)
bad(
    "RunBundle",
    "evidence-points-to-a-missing-turn",
    "maximal",
    [Set("results", 0, "evidence_refs", 0, "ref_id", to=new_id("turn"))],
    [],
    VE,
    "a result's evidence is a record of the bundle",
    tag="unresolved",
)
bad(
    "RunBundle",
    "snapshot-is-another-one",
    "maximal",
    [Set("run", "config_snapshot_id", to=new_id("snap"))],
    [],
    VE,
    "the run names the snapshot the bundle holds",
    tag="snapshot",
)
bad(
    "RunBundle",
    "withheld-record-is-held",
    "maximal",
    [
        Set("withheld", 1, "kind", to="turn"),
        Copy("withheld", 1, "record_id", source=("turns", 0, "id")),
    ],
    [],
    VE,
    "a record cannot be both held and declared withheld",
    tag="withheld",
)
bad(
    "RunBundle",
    "same-record-id-with-another-content",
    "maximal",
    [Copy("turns", 1, "id", source=("turns", 0, "id"))],
    [],
    "ConflictingRecordError",
    "two records with one id must be equal; a correction is a new record",
)
bad(
    "RunBundle",
    "usage-of-another-run",
    "maximal",
    [Set("usage_records", 0, "run_id", to=new_id("run"))],
    [],
    VE,
    "every usage record of a bundle names the bundle's run",
    tag="membership",
)

# ------------------------------------------------------------------------------ ExportBundle

extra("ExportBundle", "minimal")
bad(
    "ExportBundle",
    "purpose-not-known",
    "minimal",
    [Set("purpose", to="publish")],
    ["purpose"],
    "closed_set",
    "a purpose is internal, shared_export or benchmark",
)
bad(
    "ExportBundle",
    "shared-export-with-clear-payloads",
    "maximal-shared-export",
    [Set("payload_redactions", to=[])],
    [],
    VE,
    "human-subject payloads of a shared export are sealed or redacted",
    tag="unprotected",
)
bad(
    "ExportBundle",
    "benchmark-without-consent",
    "maximal-benchmark",
    [
        Set("run_bundles", 0, "artifacts", 0, "privacy", "consent", "status", to="not_obtained"),
        Set("run_bundles", 0, "transcripts", 0, "privacy", "consent", "status", to="not_obtained"),
    ],
    [],
    VE,
    "a benchmark export needs consent obtained, even when redacted",
    tag="consent",
)
bad(
    "ExportBundle",
    "sealed-payload-of-a-record-not-exported",
    "maximal",
    [Set("sealed_payloads", 0, "aad_fields", "subject_id", to=new_id("art"))],
    [],
    VE,
    "a sealed payload belongs to a record of the export",
    tag="sealed",
)
bad(
    "ExportBundle",
    "dataset-item-twice",
    "maximal",
    [Copy("dataset_items", 1, source=("dataset_items", 0))],
    [],
    VE,
    "a dataset item appears once in an export",
    tag="duplicate",
)
bad(
    "ExportBundle",
    "omission-entry-for-an-item-that-keeps-its-run",
    "maximal",
    [Set("dataset_items", 0, "source_run_id", to=new_id("run"))],
    [],
    VE,
    "an omission entry names an item whose source_run_id was dropped",
    tag="provenance_redaction",
)
bad(
    "ExportBundle",
    "file-uri-in-a-shared-export",
    "maximal-shared-export",
    [Set("run_bundles", 0, "artifacts", 0, "uri", to="file:///var/tracer/call-0001.wav")],
    [],
    VE,
    "a shared export carries no file: URI",
    tag="uri",
)
