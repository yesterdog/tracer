# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Codecs between ``tracer_evalschema`` entities and the rows of ``tracer_evaldb.tables``.

``encode_<table>`` turns a validated model into an ``EncodedEntity``: the row of the entity table,
whose values are the Python values of the column types, and the rows of the side tables the entity
fills. ``decode_<table>`` is the inverse and trusts nothing in the row: the document is parsed by
the strict loader and validated by the model, and ``doc_sha256`` and every projected column are
recomputed from the validated model and compared with the row (ADR 0019 Decisions 2 and 6).

``doc`` is the exact ``model_dump_json()`` text. ``doc_sha256`` is the SHA-256 of the RFC 8785
bytes of the JSON-mode dump, hashed only through ``tracer_evalschema.digest``. A ``RunBundle`` row
holds the envelope only; its members are rows of ``run_bundle_member`` and ``decode_run_bundle``
takes them back through a ``resolve`` callback.

Errors never hold text of a document or a value of a column: a ``StoreIntegrityError`` names the
table, the column and the row's ID, and is raised ``from None``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from tracer_evalschema import digest, loader
from tracer_evalschema.agent import AgentTarget
from tracer_evalschema.artifact import Artifact
from tracer_evalschema.base import EntityBase, VersionedEntity
from tracer_evalschema.bundle import ExportBundle, RunBundle, WithheldRecord
from tracer_evalschema.cost import CostRecord, CostSummary
from tracer_evalschema.dataset import Dataset, DatasetItem
from tracer_evalschema.errors import DigestMismatch, NonIJsonValueError
from tracer_evalschema.evaluator import Evaluator
from tracer_evalschema.events import StageSpan, ToolCallEvent
from tracer_evalschema.ids import (
    ArtifactId,
    CostRecordId,
    ExchangeId,
    ExternalRef,
    MetricResultId,
    StageSpanId,
    ToolCallEventId,
    TranscriptId,
    TurnId,
    TypedId,
    UsageRecordId,
)
from tracer_evalschema.metrics import MetricBinding, MetricDefinition
from tracer_evalschema.persona import Persona
from tracer_evalschema.pricing import PricingCatalog
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.profile import TestProfile
from tracer_evalschema.project import Project
from tracer_evalschema.results import MetricResult
from tracer_evalschema.rubric import Rubric
from tracer_evalschema.run import Run
from tracer_evalschema.snapshot import ConfigSnapshot
from tracer_evalschema.transcript import Exchange, Transcript, Turn
from tracer_evalschema.usage import UsageRecord
from tracer_evalschema.verdict import RunVerdict

from .errors import StoreIntegrityError, StoreValueOutOfRange

__all__ = [
    "EncodedEntity",
    "decode",
    "decode_agent_target",
    "decode_artifact",
    "decode_config_snapshot",
    "decode_cost_record",
    "decode_cost_summary",
    "decode_dataset",
    "decode_dataset_item",
    "decode_evaluator",
    "decode_exchange",
    "decode_export_bundle",
    "decode_metric_binding",
    "decode_metric_definition",
    "decode_metric_result",
    "decode_persona",
    "decode_pricing_catalog",
    "decode_project",
    "decode_rubric",
    "decode_run",
    "decode_run_bundle",
    "decode_run_verdict",
    "decode_stage_span",
    "decode_test_profile",
    "decode_tool_call_event",
    "decode_transcript",
    "decode_turn",
    "decode_usage_record",
    "encode",
    "encode_agent_target",
    "encode_artifact",
    "encode_config_snapshot",
    "encode_cost_record",
    "encode_cost_summary",
    "encode_dataset",
    "encode_dataset_item",
    "encode_evaluator",
    "encode_exchange",
    "encode_export_bundle",
    "encode_metric_binding",
    "encode_metric_definition",
    "encode_metric_result",
    "encode_persona",
    "encode_pricing_catalog",
    "encode_project",
    "encode_rubric",
    "encode_run",
    "encode_run_bundle",
    "encode_run_verdict",
    "encode_stage_span",
    "encode_test_profile",
    "encode_tool_call_event",
    "encode_transcript",
    "encode_turn",
    "encode_usage_record",
]

# JCS (RFC 8785, I-JSON) refuses an integer outside this range, so no stored document can hold one
# (ADR 0019 Ambiguity 5, revision 35).
_MAX_STORED_INT = 2**53 - 1

Row = Mapping[str, object]
SideRows = dict[str, list[dict[str, object]]]


@dataclass(frozen=True)
class EncodedEntity:
    """The rows to insert for one entity.

    ``row`` has a value for every column of ``table``; ``side_rows`` has a key (possibly an empty
    list) for every side table the entity kind can fill.
    """

    table: str
    row: Mapping[str, object]
    side_rows: Mapping[str, Sequence[Mapping[str, object]]]


# --------------------------------------------------------------------------------- projections


def _uuid(ref: TypedId | None) -> uuid.UUID | None:
    return None if ref is None else ref.uuid


_PRIVACY_COLUMNS = (
    "human_subject",
    "human_subject_reason",
    "consent_status",
    "retain_until",
    "delete_on_expiry",
    "policy_ref",
)


def _privacy(marker: PrivacyMarker | None, prefix: str) -> dict[str, object]:
    """The six privacy columns of a marker; all NULL when there is no marker (ADR 0019 6a)."""
    names = _PRIVACY_COLUMNS
    if marker is None:
        return {f"{prefix}{name}": None for name in names}
    consent, retention = marker.consent, marker.retention
    values = (
        marker.human_subject,
        marker.human_subject_reason,
        None if consent is None else consent.status,
        None if retention is None else retention.retain_until,
        None if retention is None else retention.delete_on_expiry,
        None if retention is None else retention.policy_ref,
    )
    return {f"{prefix}{name}": value for name, value in zip(names, values, strict=True)}


def _project_agent_target(m: AgentTarget, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"transport": m.endpoint.transport}


def _project_persona(m: Persona, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"language": m.language}


def _project_nothing(m: Any, run_id: uuid.UUID | None) -> dict[str, object]:
    return {}


def _project_evaluator(m: Evaluator, run_id: uuid.UUID | None) -> dict[str, object]:
    profile = m.test_profile
    return {
        "persona_id": m.persona.id.uuid,
        "persona_version": m.persona.version,
        "test_profile_id": None if profile is None else profile.id.uuid,
        "test_profile_version": None if profile is None else profile.version,
    }


def _project_metric_definition(m: MetricDefinition, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"key": m.key, "kind": m.kind, "value_type": m.value_type}


def _project_metric_binding(m: MetricBinding, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "evaluator_id": m.evaluator_id.uuid,
        "metric_id": m.metric.id.uuid,
        "metric_version": m.metric.version,
    }


def _project_project(m: Project, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"name": m.name}


def _project_config_snapshot(m: ConfigSnapshot, run_id: uuid.UUID | None) -> dict[str, object]:
    catalog = m.pricing_catalog
    return {
        "evaluator_id": m.evaluator.id.uuid,
        "evaluator_version": m.evaluator.version,
        "rubric_id": m.rubric.id.uuid,
        "rubric_version": m.rubric.version,
        "pricing_catalog_id": None if catalog is None else catalog.id.uuid,
        "pricing_catalog_version": None if catalog is None else catalog.version,
    }


def _project_run(m: Run, run_id: uuid.UUID | None) -> dict[str, object]:
    caller = m.caller_external_id
    return {
        "project_id": m.project_id.uuid,
        "config_snapshot_id": m.config_snapshot_id.uuid,
        "agent_target_id": m.agent_target.id.uuid,
        "agent_target_version": m.agent_target.version,
        "mode": m.mode,
        "caller_origin": m.caller_origin,
        "observed_tier": m.observed_tier,
        "started_at": m.started_at,
        # only the marker of the caller identifier is projected, never its value
        **_privacy(None if caller is None else caller.privacy, "caller_"),
    }


def _project_artifact(m: Artifact, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "run_id": run_id,
        "sha256": bytes.fromhex(m.sha256),
        "turn_ref": _uuid(m.turn_ref),
        **_privacy(m.privacy, ""),
    }


def _project_transcript(m: Transcript, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "run_id": run_id,
        "source": m.source,
        "detected_language": m.detected_language,
        **_privacy(m.privacy, ""),
    }


def _project_turn(m: Turn, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "run_id": run_id,
        "transcript_id": m.transcript_id.uuid,
        "seq": m.seq,
        "role": m.role,
        "start_ms": m.start_ms,
    }


def _project_exchange(m: Exchange, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"run_id": run_id, "index": m.index}


def _project_tool_call_event(m: ToolCallEvent, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"run_id": run_id, "source": m.source}


def _project_stage_span(m: StageSpan, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"run_id": run_id, "stage": m.stage}


def _project_metric_result(m: MetricResult, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "run_id": run_id,
        "metric_id": m.metric.id.uuid,
        "metric_version": m.metric.version,
        "status": m.status,
        "na_reason": m.na_reason,
        "passed": m.passed,  # None stays None, never False
    }


def _project_run_verdict(m: RunVerdict, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": m.status,
        "required_total": m.required_total,
        "passed": m.passed,
        "failed": m.failed,
        "not_applicable": m.not_applicable,
        "errored": m.errored,
    }


def _project_usage_record(m: UsageRecord, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "run_id": m.run_id.uuid,  # the native field
        "domain": m.domain,
        "source": m.source,
        "occurred_at": m.occurred_at,
    }


def _project_cost_record(m: CostRecord, run_id: uuid.UUID | None) -> dict[str, object]:
    money = m.amount
    return {
        "run_id": run_id,
        "status": m.status,
        "basis": m.basis,
        "amount": None if money is None else Decimal(money.amount),
        "currency": None if money is None else money.currency,
        "na_reason": m.na_reason,
    }


def _project_cost_summary(m: CostSummary, run_id: uuid.UUID | None) -> dict[str, object]:
    money = m.total
    return {
        "run_id": run_id,
        "completeness": m.completeness,
        "total": None if money is None else Decimal(money.amount),
        "currency": None if money is None else money.currency,
    }


def _project_dataset_item(m: DatasetItem, run_id: uuid.UUID | None) -> dict[str, object]:
    return {
        "artifact_id": _uuid(m.artifact_id),
        "transcript_id": _uuid(m.transcript_id),
        "source_run_id": _uuid(m.source_run_id),
        "language": m.language,
        **_privacy(m.privacy, ""),
    }


def _project_run_bundle(m: RunBundle, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"run_id": m.run.id.uuid}


def _project_export_bundle(m: ExportBundle, run_id: uuid.UUID | None) -> dict[str, object]:
    return {"purpose": m.purpose, "byte_length": len(m.model_dump_json().encode("utf-8"))}


# ------------------------------------------------------------------------------------ side rows


def _external_ref_rows(
    kind: str, entity_id: uuid.UUID, refs: Sequence[ExternalRef], run_id: uuid.UUID | None
) -> list[dict[str, object]]:
    """One row per distinct reference: the key is the SHA-256 of the JCS of [system, kind, value],
    so case, white space, normalization forms and U+0000 are all distinct, at any length."""
    rows: dict[bytes, dict[str, object]] = {}
    for ref in refs:
        key = bytes.fromhex(digest.sha256_hex(digest.jcs_bytes([ref.system, ref.kind, ref.value])))
        rows.setdefault(
            key,
            {"entity_kind": kind, "entity_id": entity_id, "key_sha256": key, "run_id": run_id},
        )
    return list(rows.values())


def _side_run(m: Run, run_id: uuid.UUID | None) -> SideRows:
    return {"external_ref_index": _external_ref_rows("run", m.id.uuid, m.external_refs, m.id.uuid)}


def _side_stage_span(m: StageSpan, run_id: uuid.UUID | None) -> SideRows:
    return {"external_ref_index": _external_ref_rows("span", m.id.uuid, [m.span_ref], run_id)}


def _side_transcript(m: Transcript, run_id: uuid.UUID | None) -> SideRows:
    refs = [] if m.provider_call_ref is None else [m.provider_call_ref]
    return {"external_ref_index": _external_ref_rows("transcript", m.id.uuid, refs, run_id)}


def _side_usage_record(m: UsageRecord, run_id: uuid.UUID | None) -> SideRows:
    refs = [] if m.provider_call_ref is None else [m.provider_call_ref]
    return {
        "external_ref_index": _external_ref_rows("usage_record", m.id.uuid, refs, m.run_id.uuid)
    }


def _side_config_snapshot(m: ConfigSnapshot, run_id: uuid.UUID | None) -> SideRows:
    members: list[tuple[str, int, VersionedEntity]] = [
        ("evaluator", 0, m.evaluator),
        ("persona", 0, m.persona),
        ("rubric", 0, m.rubric),
    ]
    members += [("metric_definition", i, d) for i, d in enumerate(m.metric_definitions)]
    members += [("metric_binding", i, b) for i, b in enumerate(m.metric_bindings)]
    profile = m.evaluator.test_profile
    return {
        "snapshot_member": [
            {
                "snapshot_id": m.id.uuid,
                "kind": kind,
                "ordinal": ordinal,
                "def_id": entity.id.uuid,
                "def_version": entity.version,
            }
            for kind, ordinal, entity in members
        ],
        "snapshot_profile_ref": (
            []
            if profile is None
            else [
                {
                    "snapshot_id": m.id.uuid,
                    "profile_id": profile.id.uuid,
                    "profile_version": profile.version,
                }
            ]
        ),
    }


def _side_dataset(m: Dataset, run_id: uuid.UUID | None) -> SideRows:
    return {
        "dataset_member": [
            {
                "dataset_id": m.id.uuid,
                "dataset_version": m.version,
                "ordinal": ordinal,
                "item_id": item_id.uuid,
            }
            for ordinal, item_id in enumerate(m.item_ids)
        ]
    }


# (member kind, RunBundle field, versioned); the kinds of ADR 0019 Table 2, in the order of the
# bundle's fields. `run`, `snapshot` and `verdict` are single records, the others lists.
_BUNDLE_MEMBERS = (
    ("run", "run", False),
    ("snapshot", "snapshot", False),
    ("pricing_catalog", "pricing_catalogs", True),
    ("test_profile", "test_profiles", True),
    ("artifact", "artifacts", False),
    ("transcript", "transcripts", False),
    ("turn", "turns", False),
    ("exchange", "exchanges", False),
    ("tool_call", "tool_calls", False),
    ("span", "spans", False),
    ("result", "results", False),
    ("verdict", "verdict", False),
    ("usage_record", "usage_records", False),
    ("cost_record", "cost_records", False),
    ("cost_summary", "cost_summaries", False),
)
_SINGLE_MEMBERS = frozenset({"run", "snapshot", "verdict"})
_WITHHELD_ID_TYPES: Mapping[str, type[TypedId]] = {
    "artifact": ArtifactId,
    "transcript": TranscriptId,
    "turn": TurnId,
    "exchange": ExchangeId,
    "tool_call": ToolCallEventId,
    "span": StageSpanId,
    "result": MetricResultId,
    "usage_record": UsageRecordId,
    "cost_record": CostRecordId,
}
_ENVELOPE_FIELDS = frozenset({"id", "schema_version", "created_at", "metadata", "metadata_flags"})


def _side_run_bundle(m: RunBundle, run_id: uuid.UUID | None) -> SideRows:
    members: list[dict[str, object]] = []
    for kind, field, versioned in _BUNDLE_MEMBERS:
        value = getattr(m, field)
        records = [] if value is None else value if isinstance(value, list) else [value]
        for ordinal, record in enumerate(records):
            members.append(
                {
                    "bundle_id": m.id.uuid,
                    "kind": kind,
                    "ordinal": ordinal,
                    "record_id": record.id.uuid,
                    "record_version": record.version if versioned else None,
                }
            )
    withheld: list[dict[str, object]] = [
        {
            "bundle_id": m.id.uuid,
            "ordinal": ordinal,
            "kind": entry.kind,
            "record_id": entry.record_id.uuid,
        }
        for ordinal, entry in enumerate(m.withheld)
    ]
    return {"run_bundle_member": members, "run_bundle_withheld": withheld}


def _side_export_bundle(m: ExportBundle, run_id: uuid.UUID | None) -> SideRows:
    """The link rows that let a deletion reach this stored copy (ADR 0020 Decision 4)."""
    runs = {bundle.run.id.uuid: None for bundle in m.run_bundles}
    items = {item.id.uuid: None for item in m.dataset_items}
    profiles = {
        profile.id.uuid: None for bundle in m.run_bundles for profile in bundle.test_profiles
    }
    for payload in m.sealed_payloads:
        if payload.aad_fields.payload_type == "test_profile":
            profiles[payload.aad_fields.subject_id.uuid] = None
    export_id = m.id.uuid
    return {
        "export_bundle_run": [{"export_id": export_id, "run_id": r} for r in runs],
        "export_bundle_item": [{"export_id": export_id, "item_id": i} for i in items],
        "export_bundle_profile": [{"export_id": export_id, "profile_id": p} for p in profiles],
    }


# ------------------------------------------------------------------------------------- specs

M = TypeVar("M", bound=EntityBase)

# The entities that carry, and whose row projects, their own content digest.
_DIGESTED = (VersionedEntity, ConfigSnapshot)


@dataclass(frozen=True)
class _Spec(Generic[M]):
    """What the codecs need to know about one entity table."""

    model: type[M]
    table: str
    project: Callable[[M, uuid.UUID | None], dict[str, object]]
    side: Callable[[M, uuid.UUID | None], SideRows] | None = None
    storage_run_id: bool = False  # run_id is not in the document: the caller supplies it


_S_AGENT_TARGET = _Spec(AgentTarget, "agent_target", _project_agent_target)
_S_PERSONA = _Spec(Persona, "persona", _project_persona)
_S_TEST_PROFILE = _Spec(TestProfile, "test_profile", _project_nothing)
_S_EVALUATOR = _Spec(Evaluator, "evaluator", _project_evaluator)
_S_METRIC_DEFINITION = _Spec(MetricDefinition, "metric_definition", _project_metric_definition)
_S_METRIC_BINDING = _Spec(MetricBinding, "metric_binding", _project_metric_binding)
_S_RUBRIC = _Spec(Rubric, "rubric", _project_nothing)
_S_PRICING_CATALOG = _Spec(PricingCatalog, "pricing_catalog", _project_nothing)
_S_DATASET = _Spec(Dataset, "dataset", _project_nothing, _side_dataset)
_S_PROJECT = _Spec(Project, "project", _project_project)
_S_CONFIG_SNAPSHOT = _Spec(
    ConfigSnapshot, "config_snapshot", _project_config_snapshot, _side_config_snapshot
)
_S_RUN = _Spec(Run, "run", _project_run, _side_run)
_S_ARTIFACT = _Spec(Artifact, "artifact", _project_artifact, storage_run_id=True)
_S_TRANSCRIPT = _Spec(
    Transcript, "transcript", _project_transcript, _side_transcript, storage_run_id=True
)
_S_TURN = _Spec(Turn, "turn", _project_turn, storage_run_id=True)
_S_EXCHANGE = _Spec(Exchange, "exchange", _project_exchange, storage_run_id=True)
_S_TOOL_CALL_EVENT = _Spec(
    ToolCallEvent, "tool_call_event", _project_tool_call_event, storage_run_id=True
)
_S_STAGE_SPAN = _Spec(
    StageSpan, "stage_span", _project_stage_span, _side_stage_span, storage_run_id=True
)
_S_METRIC_RESULT = _Spec(MetricResult, "metric_result", _project_metric_result, storage_run_id=True)
_S_RUN_VERDICT = _Spec(RunVerdict, "run_verdict", _project_run_verdict, storage_run_id=True)
_S_USAGE_RECORD = _Spec(UsageRecord, "usage_record", _project_usage_record, _side_usage_record)
_S_COST_RECORD = _Spec(CostRecord, "cost_record", _project_cost_record, storage_run_id=True)
_S_COST_SUMMARY = _Spec(CostSummary, "cost_summary", _project_cost_summary, storage_run_id=True)
_S_DATASET_ITEM = _Spec(DatasetItem, "dataset_item", _project_dataset_item)
_S_RUN_BUNDLE = _Spec(RunBundle, "run_bundle", _project_run_bundle, _side_run_bundle)
_S_EXPORT_BUNDLE = _Spec(ExportBundle, "export_bundle", _project_export_bundle, _side_export_bundle)

_SPECS: tuple[_Spec[Any], ...] = (
    _S_AGENT_TARGET,
    _S_PERSONA,
    _S_TEST_PROFILE,
    _S_EVALUATOR,
    _S_METRIC_DEFINITION,
    _S_METRIC_BINDING,
    _S_RUBRIC,
    _S_PRICING_CATALOG,
    _S_DATASET,
    _S_PROJECT,
    _S_CONFIG_SNAPSHOT,
    _S_RUN,
    _S_ARTIFACT,
    _S_TRANSCRIPT,
    _S_TURN,
    _S_EXCHANGE,
    _S_TOOL_CALL_EVENT,
    _S_STAGE_SPAN,
    _S_METRIC_RESULT,
    _S_RUN_VERDICT,
    _S_USAGE_RECORD,
    _S_COST_RECORD,
    _S_COST_SUMMARY,
    _S_DATASET_ITEM,
    _S_RUN_BUNDLE,
    _S_EXPORT_BUNDLE,
)
_BY_TABLE: Mapping[str, _Spec[Any]] = {spec.table: spec for spec in _SPECS}
_BY_MODEL: Mapping[type[EntityBase], _Spec[Any]] = {spec.model: spec for spec in _SPECS}

# ------------------------------------------------------------------------------------- encode


def _document(spec: _Spec[M], model: M) -> tuple[str, dict[str, object]]:
    """The ``doc`` text and the JSON-mode dump that ``doc_sha256`` covers."""
    if spec.model is RunBundle:
        include = set(_ENVELOPE_FIELDS)
        return model.model_dump_json(include=include), model.model_dump(
            mode="json", include=include
        )
    return model.model_dump_json(), model.model_dump(mode="json")


def _row(spec: _Spec[M], model: M, run_id: uuid.UUID | None) -> dict[str, object]:
    """The full row of an entity table, computed from the model alone."""
    row: dict[str, object] = {
        "id": model.id.uuid,
        "schema_version": model.schema_version,
        "created_at": model.created_at,
    }
    if isinstance(model, VersionedEntity):
        row["version"] = model.version
    if isinstance(model, _DIGESTED):
        row["content_digest_scheme"] = model.content_digest.scheme
        row["content_digest_value"] = bytes.fromhex(model.content_digest.value)
    row.update(spec.project(model, run_id))
    for column, value in row.items():
        if type(value) is int and abs(value) > _MAX_STORED_INT:
            raise StoreValueOutOfRange(spec.table, column)
    text, dump = _document(spec, model)
    try:
        sha = bytes.fromhex(digest.sha256_hex(digest.jcs_bytes(dump)))
    except NonIJsonValueError:  # an integer elsewhere in the document
        raise StoreValueOutOfRange(spec.table, "doc") from None
    return {**row, "doc": text, "doc_sha256": sha}


def _encode(spec: _Spec[M], model: M, run_id: uuid.UUID | None) -> EncodedEntity:
    if not isinstance(model, spec.model):
        raise TypeError(f"encode_{spec.table} takes a {spec.model.__name__}")
    if spec.storage_run_id:
        if type(run_id) is not uuid.UUID:
            raise TypeError("run_id must be a uuid.UUID")
    elif run_id is not None:
        raise TypeError(f"encode_{spec.table} takes no run_id")
    side = {} if spec.side is None else spec.side(model, run_id)
    return EncodedEntity(spec.table, _row(spec, model, run_id), side)


def encode(model: EntityBase, *, run_id: uuid.UUID | None = None) -> EncodedEntity:
    """Encode any entity; ``run_id`` is required for the ten tables whose run is storage-only and
    refused for every other."""
    spec = _BY_MODEL.get(type(model))
    if spec is None:
        raise TypeError("encode takes one of the 26 entities of tracer_evalschema")
    return _encode(spec, model, run_id)


# ------------------------------------------------------------------------------------- decode


def _entity_id(row: Row) -> str | None:
    value = row.get("id")
    return str(value) if type(value) is uuid.UUID else None


def _integrity(table: str, column: str, row: Row) -> StoreIntegrityError:
    return StoreIntegrityError(table, column, _entity_id(row))


def _same(got: object, want: object) -> bool:
    """Equal value of the same type. Decimals by digits and scale, instants as instants."""
    if type(got) is not type(want):
        return False
    if isinstance(want, Decimal) and isinstance(got, Decimal):
        return format(got, "f") == format(want, "f")
    if isinstance(want, datetime):
        return got == want and want.tzinfo is not None
    return got == want


def _load(spec: _Spec[M], text: str) -> M:
    limit = loader.DEFAULT_MAX_BUNDLE_BYTES
    if len(text) * 4 > limit and len(text.encode("utf-8")) > limit:  # 4: UTF-8 bytes per character
        raise ValueError("the document is too large")
    if spec.model is ExportBundle:
        return loader.load_bundle(spec.model, text)
    return loader.load_model(spec.model, text)


def _decode(spec: _Spec[M], row: Row) -> M:
    text = row.get("doc")
    if type(text) is not str:
        raise _integrity(spec.table, "doc", row)
    try:
        model = _load(spec, text)
    except DigestMismatch:
        if issubclass(spec.model, _DIGESTED):
            raise
        raise _integrity(spec.table, "doc", row) from None
    except Exception:
        raise _integrity(spec.table, "doc", row) from None
    # the run of the ten storage-only tables is not in the document and cannot be re-checked
    stored_run = row.get("run_id")
    run_id = stored_run if isinstance(stored_run, uuid.UUID) and spec.storage_run_id else None
    if spec.storage_run_id and run_id is None:
        raise _integrity(spec.table, "run_id", row)
    try:
        expected = _row(spec, model, run_id)
    except StoreValueOutOfRange:
        raise _integrity(spec.table, "doc", row) from None
    # the document is the source; then the common, digest and projected columns, in that order
    for column in ("doc_sha256", *(c for c in expected if c not in ("doc", "doc_sha256"))):
        if not _same(row.get(column), expected[column]):
            raise _integrity(spec.table, column, row)
    return model


def decode(table: str, row: Row) -> Any:
    """Decode the row of any entity table except ``run_bundle`` (see ``decode_run_bundle``)."""
    spec = _BY_TABLE.get(table)
    if spec is None or spec.model is RunBundle:
        raise ValueError("decode takes the name of an entity table other than run_bundle")
    return _decode(spec, row)


def _bundle_failure(row: Row, column: str = "doc") -> StoreIntegrityError:
    return _integrity("run_bundle", column, row)


def decode_run_bundle(
    row: Row,
    *,
    members: Sequence[Row],
    withheld: Sequence[Row],
    resolve: Callable[[str, uuid.UUID, int | None], object],
) -> RunBundle:
    """Rebuild a run bundle from its envelope row and the rows of its two side tables.

    ``members`` and ``withheld`` may come in any order; ``resolve(kind, record_id,
    record_version)`` returns the decoded record of a member. The result is validated as a
    ``RunBundle`` (every bundle rule) and its ID, schema version, creation time, ``doc_sha256``
    and run are compared with the row.
    """
    text = row.get("doc")
    if type(text) is not str:
        raise _bundle_failure(row)
    try:
        envelope = loader.loads_strict(text)
        if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
            raise ValueError("not an envelope")
        refs = {
            field: [_member_ref(m) for m in sorted(members, key=_ordinal) if m["kind"] == kind]
            for kind, field, _ in _BUNDLE_MEMBERS
        }
        entries = [_withheld_record(w) for w in sorted(withheld, key=_ordinal)]
    except Exception:
        raise _bundle_failure(row) from None
    # resolve is the caller's: its own errors are not ours to translate
    fields: dict[str, Any] = dict(envelope)
    for kind, field, _ in _BUNDLE_MEMBERS:
        records = [resolve(kind, record_id, version) for record_id, version in refs[field]]
        if kind in _SINGLE_MEMBERS:
            fields[field] = records[0] if records else None
        else:
            fields[field] = records
    fields["withheld"] = entries
    try:
        bundle = RunBundle.model_validate(fields)
        expected = _row(_S_RUN_BUNDLE, bundle, None)
    except Exception:
        raise _bundle_failure(row) from None
    for column in ("id", "schema_version", "created_at", "doc_sha256", "run_id"):
        if not _same(row.get(column), expected[column]):
            raise _bundle_failure(row, column)
    return bundle


def _ordinal(row: Row) -> int:
    ordinal = row["ordinal"]
    if type(ordinal) is not int:
        raise ValueError("ordinal is not an integer")
    return ordinal


def _member_ref(row: Row) -> tuple[uuid.UUID, int | None]:
    record_id, version = row["record_id"], row["record_version"]
    if type(record_id) is not uuid.UUID or not (version is None or type(version) is int):
        raise ValueError("malformed member row")
    return record_id, version


def _withheld_record(row: Row) -> WithheldRecord:
    kind, record_id = row["kind"], row["record_id"]
    if not isinstance(kind, str) or type(record_id) is not uuid.UUID:
        raise ValueError("malformed withheld row")
    return WithheldRecord.model_validate(
        {"kind": kind, "record_id": _WITHHELD_ID_TYPES[kind](record_id)}
    )


# ----------------------------------------------------------------------------- per-table API


def encode_agent_target(model: AgentTarget) -> EncodedEntity:
    return _encode(_S_AGENT_TARGET, model, None)


def decode_agent_target(row: Mapping[str, object]) -> AgentTarget:
    return _decode(_S_AGENT_TARGET, row)


def encode_persona(model: Persona) -> EncodedEntity:
    return _encode(_S_PERSONA, model, None)


def decode_persona(row: Mapping[str, object]) -> Persona:
    return _decode(_S_PERSONA, row)


def encode_test_profile(model: TestProfile) -> EncodedEntity:
    return _encode(_S_TEST_PROFILE, model, None)


def decode_test_profile(row: Mapping[str, object]) -> TestProfile:
    return _decode(_S_TEST_PROFILE, row)


def encode_evaluator(model: Evaluator) -> EncodedEntity:
    return _encode(_S_EVALUATOR, model, None)


def decode_evaluator(row: Mapping[str, object]) -> Evaluator:
    return _decode(_S_EVALUATOR, row)


def encode_metric_definition(model: MetricDefinition) -> EncodedEntity:
    return _encode(_S_METRIC_DEFINITION, model, None)


def decode_metric_definition(row: Mapping[str, object]) -> MetricDefinition:
    return _decode(_S_METRIC_DEFINITION, row)


def encode_metric_binding(model: MetricBinding) -> EncodedEntity:
    return _encode(_S_METRIC_BINDING, model, None)


def decode_metric_binding(row: Mapping[str, object]) -> MetricBinding:
    return _decode(_S_METRIC_BINDING, row)


def encode_rubric(model: Rubric) -> EncodedEntity:
    return _encode(_S_RUBRIC, model, None)


def decode_rubric(row: Mapping[str, object]) -> Rubric:
    return _decode(_S_RUBRIC, row)


def encode_pricing_catalog(model: PricingCatalog) -> EncodedEntity:
    return _encode(_S_PRICING_CATALOG, model, None)


def decode_pricing_catalog(row: Mapping[str, object]) -> PricingCatalog:
    return _decode(_S_PRICING_CATALOG, row)


def encode_dataset(model: Dataset) -> EncodedEntity:
    return _encode(_S_DATASET, model, None)


def decode_dataset(row: Mapping[str, object]) -> Dataset:
    return _decode(_S_DATASET, row)


def encode_project(model: Project) -> EncodedEntity:
    return _encode(_S_PROJECT, model, None)


def decode_project(row: Mapping[str, object]) -> Project:
    return _decode(_S_PROJECT, row)


def encode_config_snapshot(model: ConfigSnapshot) -> EncodedEntity:
    return _encode(_S_CONFIG_SNAPSHOT, model, None)


def decode_config_snapshot(row: Mapping[str, object]) -> ConfigSnapshot:
    return _decode(_S_CONFIG_SNAPSHOT, row)


def encode_run(model: Run) -> EncodedEntity:
    return _encode(_S_RUN, model, None)


def decode_run(row: Mapping[str, object]) -> Run:
    return _decode(_S_RUN, row)


def encode_artifact(model: Artifact, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_ARTIFACT, model, run_id)


def decode_artifact(row: Mapping[str, object]) -> Artifact:
    return _decode(_S_ARTIFACT, row)


def encode_transcript(model: Transcript, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_TRANSCRIPT, model, run_id)


def decode_transcript(row: Mapping[str, object]) -> Transcript:
    return _decode(_S_TRANSCRIPT, row)


def encode_turn(model: Turn, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_TURN, model, run_id)


def decode_turn(row: Mapping[str, object]) -> Turn:
    return _decode(_S_TURN, row)


def encode_exchange(model: Exchange, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_EXCHANGE, model, run_id)


def decode_exchange(row: Mapping[str, object]) -> Exchange:
    return _decode(_S_EXCHANGE, row)


def encode_tool_call_event(model: ToolCallEvent, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_TOOL_CALL_EVENT, model, run_id)


def decode_tool_call_event(row: Mapping[str, object]) -> ToolCallEvent:
    return _decode(_S_TOOL_CALL_EVENT, row)


def encode_stage_span(model: StageSpan, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_STAGE_SPAN, model, run_id)


def decode_stage_span(row: Mapping[str, object]) -> StageSpan:
    return _decode(_S_STAGE_SPAN, row)


def encode_metric_result(model: MetricResult, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_METRIC_RESULT, model, run_id)


def decode_metric_result(row: Mapping[str, object]) -> MetricResult:
    return _decode(_S_METRIC_RESULT, row)


def encode_run_verdict(model: RunVerdict, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_RUN_VERDICT, model, run_id)


def decode_run_verdict(row: Mapping[str, object]) -> RunVerdict:
    return _decode(_S_RUN_VERDICT, row)


def encode_usage_record(model: UsageRecord) -> EncodedEntity:
    return _encode(_S_USAGE_RECORD, model, None)


def decode_usage_record(row: Mapping[str, object]) -> UsageRecord:
    return _decode(_S_USAGE_RECORD, row)


def encode_cost_record(model: CostRecord, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_COST_RECORD, model, run_id)


def decode_cost_record(row: Mapping[str, object]) -> CostRecord:
    return _decode(_S_COST_RECORD, row)


def encode_cost_summary(model: CostSummary, *, run_id: uuid.UUID) -> EncodedEntity:
    return _encode(_S_COST_SUMMARY, model, run_id)


def decode_cost_summary(row: Mapping[str, object]) -> CostSummary:
    return _decode(_S_COST_SUMMARY, row)


def encode_dataset_item(model: DatasetItem) -> EncodedEntity:
    return _encode(_S_DATASET_ITEM, model, None)


def decode_dataset_item(row: Mapping[str, object]) -> DatasetItem:
    return _decode(_S_DATASET_ITEM, row)


def encode_run_bundle(model: RunBundle) -> EncodedEntity:
    """Encode a run bundle: the envelope as ``doc``, the members as side rows."""
    return _encode(_S_RUN_BUNDLE, model, None)


def encode_export_bundle(model: ExportBundle) -> EncodedEntity:
    return _encode(_S_EXPORT_BUNDLE, model, None)


def decode_export_bundle(row: Mapping[str, object]) -> ExportBundle:
    return _decode(_S_EXPORT_BUNDLE, row)
