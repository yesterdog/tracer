# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Bundles: the bundle seam, ``RunBundle``, ``ExportBundle`` and the export rules (ADR 0004
Decision 2a, ADR 0006, ADR 0008, ADR 0009, ADR 0015 Decision 7, ADR 0016; P1-14). The contract is
api-surface.md section 25.

The seam
--------
``check_bundle_consistency(docs, refs=(), *, require_resolved=False)`` works on JSON-mode
documents, so it can judge a set of documents without building their entities:

- one ``schema_version`` for every document (``MixedSchemaVersionError``);
- a versioned document (it has ``version``): one ``(id, version)`` has one ``content_digest``
  (``ConflictingVersionError``); identical duplicates and other versions are fine;
- a record of fact (no ``version``): one ``id`` has one content (``ConflictingRecordError``);
  a correction is a new record with a new id (P-47);
- a reference ``{id, version, content_digest?}`` needs ``version``; when the documents hold that
  ``(id, version)`` and both digests can be compared (same scheme, and for a keyed scheme the
  same ``key_ref``), they must be equal (``RefDigestMismatchError``). A digest that cannot be
  compared here is verified elsewhere with ``digest.verify_digest`` and is never reported as
  matching. With ``require_resolved=True`` a reference whose ``(id, version)`` no document holds
  raises ``UnresolvedReferenceError`` (B10); by default it is not an error.

Content is compared on the digest domain (ADR 0006 Decision 5), so a ``null`` member and an
absent one are the same content.

The bundles
-----------
``RunBundle`` (``rbnd``) is the unit of a run (N22): the ``Run``, its ``ConfigSnapshot`` and the
records of the run, plus the declaration of the records it deliberately withholds
(``WithheldRecord``, ADR 0004 Decision 2a). It is purpose-neutral (B9). ``ExportBundle`` (``exp``)
embeds run bundles, datasets and their items, the sealed payloads (ADR 0015 Decision 7: the only
place for them) and the two kinds of redaction entry (``PayloadRedaction``,
``ProvenanceRedaction``). Both are records of fact, hide their input (B21) and run their rules on
load, after the fields (``check_run_bundle``, ``check_export_bundle``).

A rule raises a ``ValueError`` whose message starts with a bracketed tag (``[unresolved]``,
``[tier]``...) and names records by ID; it never quotes a value (a text, an argument, a URI, a
digest value, a caller's number, the run ID of an omitted ``source_run_id``). Typed errors (the
``BundleError`` subclasses of the seam, the schema version and digest errors of the embedded
models) are raised unwrapped and come before the tagged rules.

The observed tier
-----------------
``evidence_tier(records)`` is the highest tier the closed provenance fields of the records show
(T2 observer, T1 telemetry, else T0); ``check_observed_tier(run, records, *, withheld=())`` is R2:
an under-claim is always rejected, an over-claim only excused by a declared record of a kind that
carries provenance fields (D3, ``PROVENANCE_KINDS``); a declaration never raises the computed tier.

The applicability re-run
------------------------
The last rule of ``check_run_bundle`` (``[applicability]``, ADR 0004 Decision 9, B15) re-runs
``results.applicability`` for every stored result, with the definition the bundle's snapshot
embeds, over the transcripts the result read (``MetricResult.transcript_ids``; derived when the
bundle holds one transcript or none, required when it holds several and the metric reads
transcripts or is language dependent) and their turns limited by role (ADR 0004 Decision 6d). It
rejects any difference in the status class, ``na_reason``, ``missing_kinds``, ``language_source``
or ``declared_language_transcript_ids``, naming the result's ID, its position ``results[<i>]`` and
the first field that differs. A run that lists ``agent_transcript`` and holds turns has an agent
turn (T-A4). A withheld transcript or turn leaves only the run-level reasons to judge (B22).

The functions hold no module-level mutable state; the rules read their arguments only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._constraints import ClosedSet, echoed, echoed_text
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.artifact import Artifact, check_channel_map, is_loopback_uri
from tracer_evalschema.base import EntityBase
from tracer_evalschema.cost import CostRecord, CostSummary, check_cost_summary
from tracer_evalschema.dataset import Dataset, DatasetItem
from tracer_evalschema.digest import KEYED_SCHEME, digest_domain
from tracer_evalschema.errors import (
    BundleError,
    ConflictingRecordError,
    ConflictingVersionError,
    MixedSchemaVersionError,
    RefDigestMismatchError,
    UnresolvedReferenceError,
)
from tracer_evalschema.events import StageSpan, ToolCallEvent, check_tool_call_seqs
from tracer_evalschema.ids import (
    ArtifactId,
    CostRecordId,
    DatasetItemId,
    ExchangeId,
    MetricResultId,
    StageSpanId,
    ToolCallEventId,
    TranscriptId,
    TurnId,
    TypedId,
    UsageRecordId,
    wire_pattern,
)
from tracer_evalschema.metrics import LANGUAGE_INDEPENDENT, MetricDefinition
from tracer_evalschema.pricing import PricingCatalog
from tracer_evalschema.privacy import (
    REDACTION_COVERS,
    PrivacyMarker,
    RedactionRecord,
    check_human_subject_marker,
    derive_human_subject,
    export_human_subject,
)
from tracer_evalschema.profile import TestProfile
from tracer_evalschema.results import (
    Applicability,
    MetricResult,
    applicability,
    check_result_against_definition,
    strongest_na_reason,
)
from tracer_evalschema.run import Run
from tracer_evalschema.sealed import SealedPayload
from tracer_evalschema.snapshot import ConfigSnapshot, check_internal_snapshot
from tracer_evalschema.transcript import Exchange, Transcript, Turn, check_transcript
from tracer_evalschema.usage import UsageRecord
from tracer_evalschema.verdict import RunVerdict, check_verdict

__all__ = [
    "EXPORT_PURPOSES",
    "PAYLOAD_KIND_COVERS",
    "PROVENANCE_KINDS",
    "WITHHOLDABLE_KINDS",
    "ExportBundle",
    "PayloadRedaction",
    "ProvenanceRedaction",
    "RunBundle",
    "WithheldRecord",
    "check_bundle_consistency",
    "check_export_bundle",
    "check_observed_tier",
    "check_run_bundle",
    "evidence_tier",
]


# =========================================================================================== seam


def _digest_fields(digest: Any) -> dict[str, Any] | None:
    if not isinstance(digest, Mapping):
        return None
    return {key: value for key, value in digest.items() if value is not None}


def _comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Two digests can be compared without a key: same scheme and the same key_ref (if any)."""
    return left.get("scheme") == right.get("scheme") and left.get("key_ref") == right.get("key_ref")


def _require_id(doc: Mapping[str, Any], what: str) -> str:
    identity = doc.get("id")
    if not isinstance(identity, str):
        raise BundleError(f"{what} without a string id: {echoed(identity)}")
    return identity


def check_bundle_consistency(
    docs: Sequence[Mapping[str, Any]],
    refs: Sequence[Mapping[str, Any]] = (),
    *,
    require_resolved: bool = False,
) -> None:
    """Raise a ``BundleError`` subclass if the documents cannot belong to one bundle (see the
    module docstring). ``require_resolved`` is keyword-only: with it, a reference whose ``(id,
    version)`` no document holds raises ``UnresolvedReferenceError`` naming the ID and version."""
    versions = {echoed(doc.get("schema_version")) for doc in docs}
    if len(versions) > 1:
        raise MixedSchemaVersionError(
            f"a bundle holds one schema_version, found {', '.join(sorted(versions))}"
        )

    versioned: dict[tuple[str, Any], dict[str, Any] | None] = {}
    records: dict[str, dict[str, Any]] = {}
    for doc in docs:
        identity = _require_id(doc, "document")
        if "version" in doc:
            key = (identity, doc["version"])
            digest = _digest_fields(doc.get("content_digest"))
            if key in versioned and versioned[key] != digest:
                raise ConflictingVersionError(
                    f"{echoed_text(identity)} version {echoed(doc['version'])} appears with two "
                    "content digests"
                )
            versioned[key] = digest
        else:
            content = digest_domain(doc)
            if identity in records and records[identity] != content:
                raise ConflictingRecordError(
                    f"record {echoed_text(identity)} appears with two different contents; a "
                    "correction is a new record with a new id"
                )
            records[identity] = content

    for ref in refs:
        identity = _require_id(ref, "reference")
        version = ref.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise BundleError(f"reference to {echoed_text(identity)} has no integer version")
        key = (identity, version)
        if key not in versioned:
            if require_resolved:
                raise UnresolvedReferenceError(
                    f"reference to {echoed_text(identity)} version {version} resolves to nothing "
                    "in the bundle"
                )
            continue
        ref_digest = _digest_fields(ref.get("content_digest"))
        entity_digest = versioned[key]
        if ref_digest is None or entity_digest is None:
            continue
        if _comparable(ref_digest, entity_digest) and ref_digest != entity_digest:
            raise RefDigestMismatchError(
                f"reference to {echoed_text(identity)} version {version} carries a digest that "
                "does not match the entity in the bundle"
            )


# ===================================================================================== constants

EXPORT_PURPOSES: Final = ("internal", "shared_export", "benchmark")
"""``ExportBundle.purpose`` (ADR 0016 Decision 7): the producer's own data, an ordinary shared
export, a benchmark (P-44)."""

WITHHOLDABLE_KINDS: Final = (
    "artifact",
    "transcript",
    "turn",
    "exchange",
    "tool_call",
    "span",
    "result",
    "usage_record",
    "cost_record",
)
"""The record kinds a ``WithheldRecord`` may name (B4)."""

PROVENANCE_KINDS: Final = ("transcript", "turn", "exchange", "tool_call", "span", "usage_record")
"""The withholdable kinds that carry provenance fields (ADR 0004 Decision 2a, D3): a declared
record of one of these kinds excuses an observed-tier over-claim."""

PAYLOAD_KIND_COVERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "audio": "audio",
        "transcript_text": "text",
        "tool_arguments": "tool_arguments",
        "tool_result": "tool_result",
    }
)
"""Sealed payload type -> the ``RedactionRecord.covers`` member that redacts the same payload
(S-N93). A test profile has no member: it is only ever sealed."""

_KIND_ID_TYPES: Final[Mapping[str, type[TypedId]]] = MappingProxyType(
    {
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
)

_TIERS: Final = ("T0", "T1", "T2")

_SHARED_PROFILE_SCHEME: Final = KEYED_SCHEME
"""The only digest scheme a ``TestProfileRef`` may carry in a shared or benchmark export (ADR 0006
Decision 6); the digest may also be absent."""

if set(PAYLOAD_KIND_COVERS.values()) != set(REDACTION_COVERS):  # pragma: no cover - import guard
    raise RuntimeError("PAYLOAD_KIND_COVERS must map onto exactly privacy.REDACTION_COVERS")


# ================================================================================ value objects


class _TypedIdUnion:
    """One validator for a field that holds a typed ID of one of several kinds (one error, not
    one per kind; the shape of ``EvidenceRef.ref_id``). Inside a model that hides its input the
    message names the rule only (ADR 0007 Decision 8 (c))."""

    def __init__(self, kinds: tuple[type[TypedId], ...], what: str) -> None:
        self.kinds = kinds
        self.what = what
        self.pattern = wire_pattern("(?:" + "|".join(kind.prefix for kind in kinds) + ")")

    def parse(self, value: Any) -> TypedId:
        if isinstance(value, TypedId) and type(value) in self.kinds:
            return value
        if isinstance(value, str):
            kind = next((k for k in self.kinds if value.startswith(k.prefix + "_")), None)
            if kind is not None:
                try:
                    return kind.parse(value)
                except ValueError:
                    pass
        if values_hidden() or not isinstance(value, str):
            raise PydanticCustomError(
                "typed_id",
                "not the ID of {what} ({pattern})",
                {"what": self.what, "pattern": self.pattern},
            )
        raise PydanticCustomError(
            "typed_id",
            "{value} is not the ID of {what} ({pattern})",
            {"value": echoed(value), "what": self.what, "pattern": self.pattern},
        )

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            self.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(str, when_used="json"),
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": self.pattern}


WithheldId = Annotated[
    ArtifactId
    | TranscriptId
    | TurnId
    | ExchangeId
    | ToolCallEventId
    | StageSpanId
    | MetricResultId
    | UsageRecordId
    | CostRecordId,
    _TypedIdUnion(tuple(_KIND_ID_TYPES.values()), "a withholdable record"),
]
"""The ID of a record of one of the ``WITHHOLDABLE_KINDS``."""

PayloadSubjectId = Annotated[
    ArtifactId | TranscriptId | ToolCallEventId,
    _TypedIdUnion(
        (ArtifactId, TranscriptId, ToolCallEventId), "an artifact, transcript or tool call"
    ),
]
"""The ID of a record whose payload a ``PayloadRedaction`` redacts."""


class WithheldRecord(BaseModel):
    """A record that a run bundle deliberately does not carry (ADR 0004 Decision 2a, D3, B4):
    the record's ``kind`` (``WITHHOLDABLE_KINDS``) and its ``record_id``, a typed ID of that kind.
    It names a kind and an ID, never a value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, ClosedSet(WITHHOLDABLE_KINDS)]
    record_id: WithheldId

    @model_validator(mode="after")
    def _id_of_the_kind(self) -> WithheldRecord:
        wanted = _KIND_ID_TYPES[self.kind]
        if type(self.record_id) is not wanted:
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("record_id",),
                        "withheld_kind",
                        f"kind={self.kind} needs an ID with the prefix {wanted.prefix}_",
                    )
                ],
            )
        return self


class PayloadRedaction(BaseModel):
    """The redaction of the payload of one record of an export (S-N89, B3): ``subject_id`` (an
    artifact, a transcript or a tool call) and the ``RedactionRecord``. Entries for one subject
    add up (the union of their ``covers``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: PayloadSubjectId
    redaction: RedactionRecord


class ProvenanceRedaction(BaseModel):
    """The recorded omission of a provenance field of an exported dataset item (ADR 0016
    Decision 9, O9, D2): the item, the field and the reason, never the run ID. Not a
    ``RedactionRecord``: it has no ``covers`` and never protects a payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: DatasetItemId
    field: Annotated[str, ClosedSet(("source_run_id",))]
    reason: Annotated[str, ClosedSet(("unresolved_reference",))]


# ===================================================================================== entities


# The lists of a bundle never hold one element twice: the bundle rules reject a repeated record ID
# (``[duplicate]``), entity version, withheld declaration, sealed subject or omission entry. The
# schema does not say so with ``uniqueItems`` (A22, ADR 0008 Decision 8): a validator compares
# arrays of objects pairwise, so the keyword costs quadratic time on a list whose length the
# document chooses (a 4,000-turn bundle). Long-list uniqueness is a code-only rule
# (CO-LONG-LIST-UNIQUENESS); a consumer that validates with the schema alone must check it.


class RunBundle(HidingModel, EntityBase):
    """The records of one run (prefix ``rbnd``; N22, B1). A record of fact.

    ``run`` and ``snapshot`` are required; every list defaults to empty and ``verdict`` to none.
    ``withheld`` declares the records the bundle deliberately does not carry. No sealed payload,
    no redaction and no purpose: those belong to the ``ExportBundle`` (ADR 0015 Decision 7, B9).
    ``check_run_bundle`` runs on load, after the fields.
    """

    id_prefix: ClassVar[str] = "rbnd"

    run: Run
    snapshot: ConfigSnapshot
    pricing_catalogs: list[PricingCatalog] = Field(default_factory=list)
    test_profiles: list[TestProfile] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    transcripts: list[Transcript] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)
    exchanges: list[Exchange] = Field(default_factory=list)
    tool_calls: list[ToolCallEvent] = Field(default_factory=list)
    spans: list[StageSpan] = Field(default_factory=list)
    results: list[MetricResult] = Field(default_factory=list)
    verdict: RunVerdict | None = None
    usage_records: list[UsageRecord] = Field(default_factory=list)
    cost_records: list[CostRecord] = Field(default_factory=list)
    cost_summaries: list[CostSummary] = Field(default_factory=list)
    withheld: list[WithheldRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bundle_rules(self) -> RunBundle:
        check_run_bundle(self)
        return self


class ExportBundle(HidingModel, EntityBase):
    """An export of run bundles and datasets (prefix ``exp``; ADR 0016 Decision 7, B1). A record
    of fact. ``purpose`` is ``internal``, ``shared_export`` or ``benchmark``. The only entity that
    embeds sealed payloads (ADR 0015 Decision 7). ``check_export_bundle`` runs on load, after the
    fields (and after each embedded run bundle has passed ``check_run_bundle``)."""

    id_prefix: ClassVar[str] = "exp"

    purpose: Annotated[str, ClosedSet(EXPORT_PURPOSES)]
    run_bundles: list[RunBundle] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    dataset_items: list[DatasetItem] = Field(default_factory=list)
    sealed_payloads: list[SealedPayload] = Field(default_factory=list)
    payload_redactions: list[PayloadRedaction] = Field(default_factory=list)
    provenance_redactions: list[ProvenanceRedaction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _export_rules(self) -> ExportBundle:
        check_export_bundle(self)
        return self


# ================================================================================ observed tier

# Per record class: the provenance fields read, and the values that are telemetry (T1) or observer
# (T2) evidence. Every other value, and every other field (``Turn.source`` is a pipeline stage, not
# a producer), is T0. ADR 0004 Decision 2a.
_T1_TRANSCRIPT_SOURCES: Final = frozenset({"agent_telemetry"})
_T1_TOOL_CALL_SOURCES: Final = frozenset({"agent_telemetry"})
_T1_TIMING: Final = frozenset({"telemetry"})  # Turn.timing_source, Exchange.source, origin kind
_T2_VALUE: Final = "observer"


def _rank_of(value: str, telemetry: frozenset[str]) -> int:
    if value == _T2_VALUE:
        return 2
    return 1 if value in telemetry else 0


def _record_rank(record: Any) -> int:
    """The tier (0, 1, 2) that one record's closed provenance fields show."""
    if isinstance(record, StageSpan):
        return 1
    if isinstance(record, UsageRecord):
        return 1 if record.domain == "agent_under_test" and record.source == "telemetry" else 0
    if isinstance(record, Turn):
        return _rank_of(record.timing_source, _T1_TIMING)
    if isinstance(record, Exchange):
        return _rank_of(record.source, _T1_TIMING)
    if isinstance(record, Transcript):
        rank = _rank_of(record.source, _T1_TRANSCRIPT_SOURCES)
    elif isinstance(record, ToolCallEvent):
        rank = _rank_of(record.source, _T1_TOOL_CALL_SOURCES)
    else:
        return 0
    origin = record.timeline_origin
    if origin is not None:
        rank = max(rank, _rank_of(origin.kind, _T1_TIMING))
    return rank


def _strongest_evidence(records: Iterable[Any]) -> tuple[int, Any]:
    """The highest rank among ``records`` and the first record that shows it (None for T0)."""
    best, witness = 0, None
    for record in records:
        rank = _record_rank(record)
        if rank > best:
            best, witness = rank, record
            if best == 2:
                break
    return best, witness


def evidence_tier(records: Iterable[Any]) -> str:
    """``"T0"``, ``"T1"`` or ``"T2"``: the highest tier the records' provenance fields show (O3,
    ADR 0004 Decision 2a). Any iterable, read once; records of other kinds count as T0."""
    return _TIERS[_strongest_evidence(records)[0]]


def check_observed_tier(
    run: Run, records: Iterable[Any], *, withheld: Iterable[WithheldRecord] = ()
) -> None:
    """R2: ``run.observed_tier`` equals the tier computed from ``records`` (ADR 0004 Decision 2a).

    ``ValueError`` tagged ``[tier]`` for an under-claim (always; it names the stored and the
    computed tier and one evidence record) and for an over-claim, unless ``withheld`` declares a
    record of a kind in ``PROVENANCE_KINDS`` (D3). A declaration never raises the computed tier.
    """
    computed, witness = _strongest_evidence(records)
    stored = _TIERS.index(run.observed_tier)
    if stored < computed:
        raise ValueError(
            f"[tier] run {run.id} stores observed_tier {_TIERS[stored]}, below the evidence tier "
            f"{_TIERS[computed]} that its records show (record {witness.id}); an under-claim is "
            "never excused"
        )
    if stored > computed and not any(entry.kind in PROVENANCE_KINDS for entry in withheld):
        raise ValueError(
            f"[tier] run {run.id} stores observed_tier {_TIERS[stored]}, above the evidence tier "
            f"{_TIERS[computed]} that its records show, and no withheld record of a kind that "
            "carries provenance is declared"
        )


# =================================================================================== run bundle

_RECORD_LISTS: Final = (
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
)
"""The lists of records of fact of a ``RunBundle`` (with ``run`` and ``verdict``)."""


def _records(bundle: RunBundle) -> list[Any]:
    """The records of fact of a run bundle: the run, every listed record and the verdict."""
    found: list[Any] = [bundle.run]
    for name in _RECORD_LISTS:
        found.extend(getattr(bundle, name))
    if bundle.verdict is not None:
        found.append(bundle.verdict)
    return found


def _record_documents(doc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The JSON documents of the records of fact of a dumped run bundle."""
    found: list[Mapping[str, Any]] = [doc["run"]]
    for name in _RECORD_LISTS:
        found.extend(doc[name])
    if doc.get("verdict") is not None:
        found.append(doc["verdict"])
    return found


def _entity_documents(doc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The documents of the entities a dumped run bundle embeds besides its records: the snapshot
    and the entities embedded in it, the pricing catalogs and the test profiles."""
    snapshot = doc["snapshot"]
    return [
        snapshot,
        snapshot["evaluator"],
        snapshot["persona"],
        snapshot["rubric"],
        *snapshot["metric_definitions"],
        *snapshot["metric_bindings"],
        *doc["pricing_catalogs"],
        *doc["test_profiles"],
    ]


def _optional_references(doc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """References verified when their target is embedded (N26, N52): the evaluator's
    ``TestProfileRef``, the snapshot's ``PricingCatalogRef`` and each computed cost record's
    ``pricing_snapshot`` read as ``{id, version, content_digest}``."""
    snapshot = doc["snapshot"]
    refs: list[Mapping[str, Any]] = []
    if snapshot["evaluator"].get("test_profile") is not None:
        refs.append(snapshot["evaluator"]["test_profile"])
    if snapshot.get("pricing_catalog") is not None:
        refs.append(snapshot["pricing_catalog"])
    for record in doc["cost_records"]:
        pricing = record.get("pricing_snapshot")
        if pricing is not None:
            refs.append(
                {
                    "id": pricing["catalog_id"],
                    "version": pricing["version"],
                    "content_digest": pricing["content_digest"],
                }
            )
    return refs


def _tagged(tag: str, exc: ValueError, prefix: str = "") -> ValueError:
    return ValueError(f"[{tag}] {prefix}{exc}")


def _run_bundle_seam(bundle: RunBundle) -> None:
    doc = bundle.model_dump(mode="json", by_alias=True)
    entities = _entity_documents(doc)
    check_bundle_consistency([*_record_documents(doc), *entities], _optional_references(doc))
    # B10: a result's metric and a verdict check's metric_ref must resolve to a definition of the
    # snapshot (``UnresolvedReferenceError``), and a comparable digest must match.
    required = [r["metric"] for r in doc["results"]]
    if doc.get("verdict") is not None:
        required.extend(check["metric_ref"] for check in doc["verdict"]["checks"])
    check_bundle_consistency(doc["snapshot"]["metric_definitions"], required, require_resolved=True)


def _check_unique_records(bundle: RunBundle) -> None:
    seen: set[TypedId] = set()
    for record in _records(bundle):
        if record.id in seen:
            raise ValueError(
                f"[duplicate] record {record.id} appears twice in run bundle {bundle.id}"
            )
        seen.add(record.id)
    versions: set[tuple[TypedId, int]] = set()
    for entity in (*bundle.pricing_catalogs, *bundle.test_profiles):
        key = (entity.id, entity.version)
        if key in versions:
            raise ValueError(
                f"[duplicate] {entity.id} version {entity.version} appears twice in run bundle "
                f"{bundle.id}"
            )
        versions.add(key)


def _check_references(bundle: RunBundle, present: set[TypedId], withheld: set[TypedId]) -> None:
    """``[unresolved]``, ``[channel_map]`` and ``[declared_language]`` (step 3)."""

    def need(target: TypedId, holder: TypedId, what: str) -> None:
        if target not in present and target not in withheld:
            raise ValueError(
                f"[unresolved] {what} of {holder} names {target}, which the bundle neither holds "
                "nor declares withheld"
            )

    for turn in bundle.turns:
        need(turn.transcript_id, turn.id, "transcript_id")
    for artifact in bundle.artifacts:
        if artifact.turn_ref is not None:
            need(artifact.turn_ref, artifact.id, "turn_ref")
    for channel_map in bundle.run.channel_maps:
        need(channel_map.artifact_id, bundle.run.id, "a channel map's artifact_id")
    for result in bundle.results:
        for evidence in result.evidence_refs:
            need(evidence.ref_id, result.id, "an evidence reference")
        for transcript_id in result.declared_language_transcript_ids:
            need(transcript_id, result.id, "declared_language_transcript_ids")
        for transcript_id in result.transcript_ids or ():
            need(transcript_id, result.id, "transcript_ids")
    if bundle.verdict is not None:
        for check in bundle.verdict.checks:
            need(check.result_id, bundle.verdict.id, "a check's result_id")
    for cost in bundle.cost_records:
        for usage_id in cost.usage_record_ids:
            need(usage_id, cost.id, "usage_record_ids")

    artifacts = {artifact.id: artifact for artifact in bundle.artifacts}
    for channel_map in bundle.run.channel_maps:
        mapped = artifacts.get(channel_map.artifact_id)
        if mapped is not None:
            try:
                check_channel_map(channel_map, mapped)
            except ValueError as exc:
                raise _tagged("channel_map", exc) from None

    transcripts = {transcript.id: transcript for transcript in bundle.transcripts}
    for result in bundle.results:
        for transcript_id in result.declared_language_transcript_ids:
            transcript = transcripts.get(transcript_id)
            if transcript is not None and transcript.declared_language is None:
                raise ValueError(
                    f"[declared_language] transcript {transcript_id}, listed by result "
                    f"{result.id} in declared_language_transcript_ids, has no declared_language"
                )


def _check_conversation(bundle: RunBundle, withheld_kinds: set[str]) -> None:
    """``[exchange]``, ``[transcript]`` and ``[tool_call_seq]`` (step 4)."""
    indexes: dict[int, TypedId] = {}
    for exchange in bundle.exchanges:
        if exchange.index in indexes:
            raise ValueError(
                f"[exchange] exchange index {exchange.index} is used by {indexes[exchange.index]} "
                f"and {exchange.id}"
            )
        indexes[exchange.index] = exchange.id
    turns_of: dict[TypedId, list[Turn]] = defaultdict(list)
    for turn in bundle.turns:
        turns_of[turn.transcript_id].append(turn)
    # A withheld exchange's index is unknown, so the turn -> exchange index rule cannot be judged.
    exchanges = None if "exchange" in withheld_kinds else bundle.exchanges
    for transcript in bundle.transcripts:
        turns = turns_of.get(transcript.id)
        if not turns:  # no text in the bundle (an empty call, or turns withheld)
            continue
        try:
            check_transcript(transcript, turns, exchanges)
        except ValueError as exc:
            raise _tagged("transcript", exc) from None
    try:
        check_tool_call_seqs(bundle.tool_calls)
    except ValueError as exc:
        raise _tagged("tool_call_seq", exc) from None


def _check_results(bundle: RunBundle, withheld: set[TypedId]) -> None:
    """``[rescoring]``, ``[result]`` and ``[verdict]`` (step 5)."""
    definitions = {(d.id, d.version): d for d in bundle.snapshot.metric_definitions}
    seen: dict[tuple[TypedId, int], TypedId] = {}
    for result in bundle.results:
        key = (result.metric.id, result.metric.version)
        if key in seen:
            raise ValueError(
                f"[rescoring] results {seen[key]} and {result.id} are two results of metric "
                f"{key[0]} version {key[1]} in one run bundle; a rescoring is a new run bundle"
            )
        seen[key] = result.id
        definition = definitions.get(key)
        if definition is None:  # pragma: no cover - the seam resolved every metric reference
            raise UnresolvedReferenceError(
                f"result {result.id} names metric {key[0]} version {key[1]}, which the snapshot "
                "does not embed"
            )
        try:
            check_result_against_definition(result, definition)
        except ValueError as exc:
            raise _tagged("result", exc) from None
    verdict = bundle.verdict
    if verdict is None:
        return
    if any(check.result_id in withheld for check in verdict.checks):
        # B17 (unpinned): a result the verdict needs is declared withheld, so the verdict cannot be
        # recomputed here; the references to it resolved through the declaration.
        return
    try:
        check_verdict(
            verdict,
            rubric=bundle.snapshot.rubric,
            bindings=bundle.snapshot.metric_bindings,
            results=bundle.results,
        )
    except ValueError as exc:
        raise _tagged("verdict", exc) from None


def _check_costs(bundle: RunBundle, withheld: set[TypedId]) -> None:
    """``[cost]`` and ``[summary]`` (step 6)."""
    catalogs = {(c.id, c.version): c for c in bundle.pricing_catalogs}
    for cost in bundle.cost_records:
        pricing = cost.pricing_snapshot
        if pricing is None:
            continue
        catalog = catalogs.get((pricing.catalog_id, pricing.version))
        if catalog is None:  # not embedded: the keys cannot be checked here
            continue
        known = {entry.entry_key for entry in catalog.entries}
        if any(key not in known for key in pricing.entry_keys):
            raise ValueError(
                f"[cost] cost record {cost.id} names an entry key that no entry of catalog "
                f"{pricing.catalog_id} version {pricing.version} has"
            )
    by_domain: dict[str, list[CostRecord]] = defaultdict(list)
    for cost in bundle.cost_records:
        by_domain[cost.domain].append(cost)
    domains: dict[str, TypedId] = {}
    for summary in bundle.cost_summaries:
        if summary.domain in domains:
            raise ValueError(
                f"[summary] cost summaries {domains[summary.domain]} and {summary.id} are two "
                f"summaries of the domain {summary.domain}"
            )
        domains[summary.domain] = summary.id
        named = [*summary.included, *(e.ref for e in summary.excluded)]
        named += [cover for e in summary.excluded for cover in e.covered_by]
        if any(record_id in withheld for record_id in named):
            # B17 (unpinned): a record the summary names is declared withheld; the summary cannot
            # be recomputed here.
            continue
        try:
            check_cost_summary(summary, by_domain.get(summary.domain, []))
        except ValueError as exc:
            raise _tagged("summary", exc, f"cost summary {summary.id}: ") from None


def _check_markers(bundle: RunBundle) -> None:
    """``[marker]`` (step 7): every artifact's and transcript's stored marker against the
    derivation over the bundle's turns (P-47, A-31)."""
    marked: list[Artifact | Transcript] = [*bundle.artifacts, *bundle.transcripts]
    for record in marked:
        try:
            check_human_subject_marker(record.privacy, bundle.run, bundle.turns)
        except ValueError as exc:
            raise _tagged("marker", exc, f"record {record.id}: ") from None


def _check_declaration(bundle: RunBundle, present: set[TypedId]) -> None:
    """``[withheld]`` (step 8): a declared record is absent and declared once."""
    declared: set[TypedId] = set()
    for entry in bundle.withheld:
        if entry.record_id in declared:
            raise ValueError(f"[withheld] {entry.record_id} is declared withheld twice")
        declared.add(entry.record_id)
        if entry.record_id in present:
            raise ValueError(
                f"[withheld] {entry.record_id} is declared withheld, but the bundle holds it"
            )


_RUN_LEVEL_REASONS: Final = ("missing_input", "agent_channel_unknown")
"""The not-applicable reasons decided from the run alone, before any language step (ADR 0004
Decisions 3 to 5)."""
_TRANSCRIPT_KINDS: Final = ("transcript", "agent_transcript")
_LANGUAGE_RECORD_KINDS: Final = frozenset({"transcript", "turn"})
"""Withheld kinds that take away what the language steps read, so those steps cannot be re-run."""


def _applicability_error(position: int, result: MetricResult, field: str) -> ValueError:
    """The ``[applicability]`` error: the result's ID and position and the one field that differs,
    never a value."""
    return ValueError(
        f"[applicability] result {result.id} (results[{position}]): {field} differs from the "
        "re-run of applicability over the run and the transcripts and turns the result read"
    )


def _needs_transcript_ids(definition: MetricDefinition) -> bool:
    """T-A3: the metric reads transcripts (``requires`` names a transcript kind) or depends on a
    language (its language may come from a transcript, ADR 0004 Decision 6d). Decided on the
    definition, never on the stored outcome."""
    return (
        any(kind in _TRANSCRIPT_KINDS for kind in definition.requires)
        or definition.languages != LANGUAGE_INDEPENDENT
    )


def _transcripts_read(
    position: int,
    result: MetricResult,
    definition: MetricDefinition,
    held: Mapping[TypedId, Transcript],
) -> list[Transcript]:
    """The transcripts ``result`` read (api-surface-applicability.md 3.2): the held transcripts
    its ``transcript_ids`` name, in that order (a withheld one is not held and so not read);
    without ids, the only transcript, or none. With several and no ids the ids are required for
    a metric that needs transcripts."""
    if result.transcript_ids is not None:
        return [held[i] for i in result.transcript_ids if i in held]
    if len(held) <= 1:
        return list(held.values())
    if _needs_transcript_ids(definition):
        raise ValueError(
            f"[applicability] result {result.id} (results[{position}]): transcript_ids required "
            "when the bundle holds several transcripts"
        )
    return []


def _turns_read(
    definition: MetricDefinition, turns: Sequence[Turn], transcripts: Sequence[Transcript]
) -> list[Turn]:
    """The turns of ``transcripts`` limited to the role the input kind names (ADR 0004 Decision
    6d): the agent turns for ``agent_transcript``, every turn for ``transcript``, none for a
    metric that reads no transcript input. Bundle order."""
    names = {transcript.id for transcript in transcripts}
    if "agent_transcript" in definition.requires:
        return [t for t in turns if t.transcript_id in names and t.role == "agent"]
    if "transcript" in definition.requires:
        return [t for t in turns if t.transcript_id in names]
    return []


def _run_level_answer(definition: MetricDefinition, run: Run) -> Applicability | None:
    """The answer of ``applicability`` when it is decided from the run alone (``missing_input``
    or ``agent_channel_unknown``), else ``None``."""
    answer = applicability(definition, run)
    return answer if answer.na_reason in _RUN_LEVEL_REASONS else None


def _per_turn_answer(
    definition: MetricDefinition, run: Run, turns: Sequence[Turn], transcripts: Sequence[Transcript]
) -> Applicability:
    """A ``per_turn_share`` metric with no run-level reason and at least one read turn: the
    language steps on each turn on its own (ADR 0004 Decision 6e, ADR 0010 Decision 3). Scored
    when any turn applies, else the strongest of the turns' reasons. ``declared`` if any turn used
    a declaration, else ``observed`` if any turn had an effective language (also a turn that was
    then not applicable); the declared transcripts are the sorted union."""
    answers = [
        applicability(definition, run, turns=[turn], transcripts=transcripts) for turn in turns
    ]
    reasons = [answer.na_reason for answer in answers]
    reason = None
    if None not in reasons:
        reason = strongest_na_reason(r for r in reasons if r is not None)
    declared = sorted({i for a in answers for i in a.declared_language_transcript_ids}, key=str)
    source = None
    if declared:
        source = "declared"
    elif any(answer.language_source is not None for answer in answers):
        source = "observed"
    return Applicability(reason, [], source, declared)


def _same_ids(left: Iterable[TypedId], right: Iterable[TypedId]) -> bool:
    return [str(i) for i in left] == [str(i) for i in right]


def _compare_applicability(position: int, result: MetricResult, expected: Applicability) -> None:
    """The stored result against the re-run, the first difference reported, in the order status
    class, ``na_reason``, ``missing_kinds`` (order of ``requires``), ``language_source``,
    ``declared_language_transcript_ids``. An ``error`` result is exempt from the status class and
    has no reason or kinds, except that a run-level reason forbids it (ADR 0004 I3: an error is
    for a metric whose inputs existed); its language provenance is compared."""
    if result.status == "error":
        if expected.na_reason in _RUN_LEVEL_REASONS:
            raise _applicability_error(position, result, "status")
    else:
        if (result.status == "not_applicable") != (expected.na_reason is not None):
            raise _applicability_error(position, result, "status")
        if result.na_reason != expected.na_reason:
            raise _applicability_error(position, result, "na_reason")
        if list(result.missing_kinds) != list(expected.missing_kinds):
            raise _applicability_error(position, result, "missing_kinds")
    if result.language_source != expected.language_source:
        raise _applicability_error(position, result, "language_source")
    if not _same_ids(
        result.declared_language_transcript_ids, expected.declared_language_transcript_ids
    ):
        raise _applicability_error(position, result, "declared_language_transcript_ids")


def _check_agent_turn(bundle: RunBundle, withheld_kinds: set[str]) -> None:
    """T-A4: a run that lists ``agent_transcript`` has an agent turn, judged only when the bundle
    holds turns and declares no turn withheld (a withheld turn may be the agent turn)."""
    run = bundle.run
    if (
        "agent_transcript" not in run.available_inputs
        or not bundle.turns
        or "turn" in withheld_kinds
        or any(turn.role == "agent" for turn in bundle.turns)
    ):
        return
    raise ValueError(
        f"[applicability] run {run.id} lists agent_transcript in available_inputs, but no turn of "
        "the bundle has the role agent"
    )


def _check_applicability(bundle: RunBundle, withheld_kinds: set[str]) -> None:
    """``[applicability]`` (step 9, the last rule; ADR 0004 Decision 9, B15): every stored result
    against ``results.applicability`` re-run over the bundle, with the definition the snapshot
    embeds, the run, and the transcripts and turns the result read.

    What cannot be re-run is skipped and nothing else (B22): with a ``transcript`` or ``turn``
    declared withheld, and for a per-turn metric that reads no turn (T-B4), only the run-level
    reasons are judged (a run-level answer must be stored exactly; any other answer must not
    carry a run-level reason) and the language outcome is accepted as stored. A withheld result
    is not there to check. The per-turn counts are not compared (T-B16).
    """
    _check_agent_turn(bundle, withheld_kinds)
    run = bundle.run
    definitions = {(d.id, d.version): d for d in bundle.snapshot.metric_definitions}
    held = {transcript.id: transcript for transcript in bundle.transcripts}
    language_known = not (withheld_kinds & _LANGUAGE_RECORD_KINDS)
    for position, result in enumerate(bundle.results):
        definition = definitions.get((result.metric.id, result.metric.version))
        if definition is None:  # pragma: no cover - the seam resolved every metric reference
            raise UnresolvedReferenceError(
                f"result {result.id} names metric {result.metric.id} version "
                f"{result.metric.version}, which the snapshot does not embed"
            )
        transcripts = _transcripts_read(position, result, definition, held)
        expected = _run_level_answer(definition, run)
        if expected is None and language_known:
            turns = _turns_read(definition, bundle.turns, transcripts)
            if definition.aggregation.kind != "per_turn_share":
                expected = applicability(definition, run, turns=turns, transcripts=transcripts)
            elif turns:
                expected = _per_turn_answer(definition, run, turns, transcripts)
        if expected is not None:
            _compare_applicability(position, result, expected)
        elif result.na_reason in _RUN_LEVEL_REASONS:
            raise _applicability_error(position, result, "na_reason")


def check_run_bundle(bundle: RunBundle) -> None:
    """The rules of a ``RunBundle`` (api-surface.md 25.4.1), in this order: the seam (typed
    errors), ``[duplicate]``, ``[snapshot]``, ``[membership]``, references by ID
    (``[unresolved]``, ``[channel_map]``, ``[declared_language]``), ``[exchange]``,
    ``[transcript]``, ``[tool_call_seq]``, ``[rescoring]``, ``[result]``, ``[verdict]``,
    ``[cost]``, ``[summary]``, ``[marker]``, ``[withheld]``, R2 (``[tier]``) and last
    ``[applicability]`` (the re-run of ``results.applicability`` per result). Pure and
    idempotent; the models run it on load."""
    _run_bundle_seam(bundle)
    _check_unique_records(bundle)
    run = bundle.run
    if run.config_snapshot_id != bundle.snapshot.id:
        raise ValueError(
            f"[snapshot] run {run.id} names snapshot {run.config_snapshot_id}, but the bundle "
            f"embeds snapshot {bundle.snapshot.id}"
        )
    for usage in bundle.usage_records:
        if usage.run_id != run.id:
            raise ValueError(
                f"[membership] usage record {usage.id} names run {usage.run_id}, not the bundle's "
                f"run {run.id}"
            )
    records = _records(bundle)
    present: set[TypedId] = {record.id for record in records}
    withheld: set[TypedId] = {entry.record_id for entry in bundle.withheld}
    withheld_kinds = {entry.kind for entry in bundle.withheld}
    _check_references(bundle, present, withheld)
    _check_conversation(bundle, withheld_kinds)
    _check_results(bundle, withheld)
    _check_costs(bundle, withheld)
    _check_markers(bundle)
    _check_declaration(bundle, present)
    check_observed_tier(run, records, withheld=bundle.withheld)
    _check_applicability(bundle, withheld_kinds)


# ================================================================================ export bundle


def _export_seam(bundle: ExportBundle) -> None:
    """The seam over every run bundle, its records and its embedded entities, the datasets and the
    dataset items: one schema version, one content per record ID (two run bundles of one run with
    different content are ``ConflictingRecordError``), one digest per ``(id, version)`` across the
    run bundles, and the optional references of every run bundle."""
    docs: list[Mapping[str, Any]] = []
    refs: list[Mapping[str, Any]] = []
    for run_bundle in bundle.run_bundles:
        doc = run_bundle.model_dump(mode="json", by_alias=True)
        docs.append(doc)
        docs.extend(_record_documents(doc))
        docs.extend(_entity_documents(doc))
        refs.extend(_optional_references(doc))
    docs.extend(d.model_dump(mode="json", by_alias=True) for d in bundle.datasets)
    docs.extend(i.model_dump(mode="json", by_alias=True) for i in bundle.dataset_items)
    check_bundle_consistency(docs, refs)


def _check_export_identity(bundle: ExportBundle) -> None:
    seen: set[TypedId] = set()
    for run_bundle in bundle.run_bundles:
        for identity in (run_bundle.id, *(record.id for record in _records(run_bundle))):
            if identity in seen:
                raise ValueError(f"[duplicate] {identity} appears twice in export {bundle.id}")
            seen.add(identity)
    items: set[TypedId] = set()
    for item in bundle.dataset_items:
        if item.id in items:
            raise ValueError(f"[duplicate] dataset item {item.id} appears twice in the export")
        items.add(item.id)
    datasets: set[tuple[TypedId, int]] = set()
    for dataset in bundle.datasets:
        key = (dataset.id, dataset.version)
        if key in datasets:
            raise ValueError(
                f"[duplicate] dataset {dataset.id} version {dataset.version} appears twice in the "
                "export"
            )
        datasets.add(key)


class _ExportIndex:
    """The records of an export by ID, built once per check (no state outlives the call)."""

    def __init__(self, bundle: ExportBundle) -> None:
        self.artifacts: dict[TypedId, RunBundle] = {}
        self.transcripts: dict[TypedId, RunBundle] = {}
        self.tool_calls: dict[TypedId, RunBundle] = {}
        self.runs: dict[TypedId, RunBundle] = {}
        self.profile_refs: set[tuple[TypedId, int]] = set()
        for run_bundle in bundle.run_bundles:
            self.runs[run_bundle.run.id] = run_bundle
            for artifact in run_bundle.artifacts:
                self.artifacts[artifact.id] = run_bundle
            for transcript in run_bundle.transcripts:
                self.transcripts[transcript.id] = run_bundle
            for call in run_bundle.tool_calls:
                self.tool_calls[call.id] = run_bundle
            reference = run_bundle.snapshot.evaluator.test_profile
            if reference is not None:
                self.profile_refs.add((reference.id, reference.version))
        self.sealed: set[tuple[str, TypedId]] = set()
        self.covers: dict[TypedId, set[str]] = defaultdict(set)

    def holds_payload_subject(self, subject: TypedId) -> bool:
        return any(subject in pool for pool in (self.artifacts, self.transcripts, self.tool_calls))


def _check_sealed_payloads(bundle: ExportBundle, index: _ExportIndex) -> None:
    """``[duplicate]`` and ``[sealed]``: one sealed payload per ``(payload_type, subject_id)``,
    and its subject is a record of the export (ADR 0015 Decisions 4 and 7)."""
    pools = {
        "audio": index.artifacts,
        "transcript_text": index.transcripts,
        "tool_arguments": index.tool_calls,
        "tool_result": index.tool_calls,
    }
    for payload in bundle.sealed_payloads:
        aad = payload.aad_fields
        key = (aad.payload_type, aad.subject_id)
        if key in index.sealed:
            raise ValueError(
                f"[duplicate] two sealed payloads of type {aad.payload_type} for {aad.subject_id}"
            )
        index.sealed.add(key)
        pool = pools.get(aad.payload_type)
        if pool is not None:
            found = aad.subject_id in pool
        else:  # test_profile: a reference of a snapshot of the export, same id and version
            found = (aad.subject_id, aad.subject_version) in index.profile_refs
        if not found:
            raise ValueError(
                f"[sealed] the subject {aad.subject_id} of a sealed {aad.payload_type} payload is "
                "not in the export"
            )


def _check_payload_redactions(bundle: ExportBundle, index: _ExportIndex) -> None:
    """``[redaction]``: each entry names an artifact, transcript or tool call of the export;
    entries for one subject add up."""
    for entry in bundle.payload_redactions:
        if not index.holds_payload_subject(entry.subject_id):
            raise ValueError(
                f"[redaction] the subject {entry.subject_id} of a payload redaction is not in the "
                "export"
            )
        index.covers[entry.subject_id].update(entry.redaction.covers)


def _check_datasets(bundle: ExportBundle) -> dict[TypedId, DatasetItem]:
    """``[unresolved]`` for a dataset member that is not an item of the export, and
    ``[provenance_redaction]`` (ADR 0016 Decision 9, D2)."""
    items = {item.id: item for item in bundle.dataset_items}
    for dataset in bundle.datasets:
        for item_id in dataset.item_ids:
            if item_id not in items:
                raise ValueError(
                    f"[unresolved] dataset {dataset.id} lists item {item_id}, which the export "
                    "does not hold"
                )
    entries: set[TypedId] = set()
    for entry in bundle.provenance_redactions:
        if entry.item_id in entries:
            raise ValueError(
                f"[provenance_redaction] two omission entries for dataset item {entry.item_id}"
            )
        entries.add(entry.item_id)
        item = items.get(entry.item_id)
        if item is None:
            raise ValueError(
                f"[provenance_redaction] the dataset item {entry.item_id} of an omission entry is "
                "not in the export"
            )
        if getattr(item, entry.field) is not None:
            raise ValueError(
                f"[provenance_redaction] the dataset item {entry.item_id} still carries the "
                f"{entry.field} its omission entry records as dropped"
            )
    return items


def _check_portable_run_bundle(run_bundle: RunBundle) -> None:
    """``[profile]``, ``[profile_digest]`` and ``[uri]`` for one run bundle of a shared or
    benchmark export (ADR 0006 Decisions 6 and 7, ADR 0007 Decisions 6c and 6d, P-17, O5, O8)."""
    if run_bundle.test_profiles:
        raise ValueError(
            f"[profile] run bundle {run_bundle.id} carries a test profile in the clear; a shared "
            "or benchmark export carries a profile only as a sealed test_profile payload"
        )
    reference = run_bundle.snapshot.evaluator.test_profile
    if (
        reference is not None
        and reference.content_digest is not None
        and reference.content_digest.scheme != _SHARED_PROFILE_SCHEME
    ):
        raise ValueError(
            f"[profile_digest] the reference to test profile {reference.id} carries a digest "
            f"that is not keyed; a shared or benchmark export drops it or keys it "
            f"({_SHARED_PROFILE_SCHEME})"
        )
    for artifact in run_bundle.artifacts:
        scheme = artifact.uri.partition(":")[0].lower()
        if scheme == "file":
            raise ValueError(
                f"[uri] artifact {artifact.id} has a file: URI, which a shared or benchmark export "
                "does not hold"
            )
        if is_loopback_uri(artifact.uri):
            raise ValueError(
                f"[uri] artifact {artifact.id} has a URI with a loopback host, which a shared or "
                "benchmark export does not hold"
            )


def _consent_obtained(marker: PrivacyMarker) -> bool:
    return marker.consent is not None and marker.consent.status == "obtained"


def _items_marking_payloads(
    bundle: ExportBundle, items: Mapping[TypedId, DatasetItem], index: _ExportIndex
) -> set[TypedId]:
    """``[source_run]`` (O9), the benchmark consent of items (P-44), and the payloads that a
    human-subject item references (B6)."""
    marked: set[TypedId] = set()
    for item in items.values():
        if item.source_run_id is not None and item.source_run_id not in index.runs:
            raise ValueError(
                f"[source_run] dataset item {item.id} keeps a source_run_id that names no run of "
                "the export; the exported copy omits it and records a provenance redaction"
            )
    for item in items.values():
        if item.privacy is None or (item.artifact_id is None and item.transcript_id is None):
            continue  # not a payload
        source = index.runs.get(item.source_run_id) if item.source_run_id is not None else None
        # ADR 0016 Decision 9: an item without its source run has no role evidence: true.
        human = (
            True if source is None else export_human_subject(item.privacy, source.run, source.turns)
        )
        if not human:
            continue
        if bundle.purpose == "benchmark" and not _consent_obtained(item.privacy):
            raise ValueError(
                f"[consent] dataset item {item.id} references human-subject data without "
                "consent.status=obtained, which a benchmark requires"
            )
        marked.update(t for t in (item.artifact_id, item.transcript_id) if t is not None)
    return marked


def _check_human_subject_payloads(
    bundle: ExportBundle, index: _ExportIndex, marked: set[TypedId]
) -> None:
    """``[unprotected]`` and ``[consent]`` (ADR 0016 Decision 7 and 7a, P-44, P-57, B3, B6, B7)."""
    benchmark = bundle.purpose == "benchmark"
    for run_bundle in bundle.run_bundles:
        run, turns = run_bundle.run, run_bundle.turns
        with_text = {turn.transcript_id for turn in turns}
        consents: list[bool] = []
        for artifact in run_bundle.artifacts:
            human = export_human_subject(artifact.privacy, run, turns)
            if human:
                consents.append(_consent_obtained(artifact.privacy))
            if (human or artifact.id in marked) and not (
                ("audio", artifact.id) in index.sealed
                or PAYLOAD_KIND_COVERS["audio"] in index.covers.get(artifact.id, ())
            ):
                raise ValueError(
                    f"[unprotected] artifact {artifact.id} holds human-subject audio that is "
                    "neither sealed nor redacted"
                )
            if human and benchmark and not _consent_obtained(artifact.privacy):
                raise ValueError(
                    f"[consent] artifact {artifact.id} is human-subject data without "
                    "consent.status=obtained, which a benchmark requires"
                )
        for transcript in run_bundle.transcripts:
            human = export_human_subject(transcript.privacy, run, turns)
            if human:
                consents.append(_consent_obtained(transcript.privacy))
            # Text is in the clear only through turns; a sealed transcript_text replaced them (B3).
            if (
                (human or transcript.id in marked)
                and transcript.id in with_text
                and PAYLOAD_KIND_COVERS["transcript_text"]
                not in index.covers.get(transcript.id, ())
            ):
                raise ValueError(
                    f"[unprotected] transcript {transcript.id} has human-subject turn text in the "
                    "clear that is not redacted (a sealed text replaces the turns)"
                )
            if human and benchmark and not _consent_obtained(transcript.privacy):
                raise ValueError(
                    f"[consent] transcript {transcript.id} is human-subject data without "
                    "consent.status=obtained, which a benchmark requires"
                )
        if not run_bundle.tool_calls or not derive_human_subject(run, turns):
            continue
        for call in run_bundle.tool_calls:
            covered = index.covers.get(call.id, set())
            if (call.arguments_raw is not None or call.arguments is not None) and (
                PAYLOAD_KIND_COVERS["tool_arguments"] not in covered
            ):
                raise ValueError(
                    f"[unprotected] tool call {call.id} has human-subject arguments in the clear "
                    "that are not redacted (a sealed tool_arguments payload replaces them)"
                )
            if call.result_raw is not None and PAYLOAD_KIND_COVERS["tool_result"] not in covered:
                raise ValueError(
                    f"[unprotected] tool call {call.id} has a human-subject result in the clear "
                    "that is not redacted (a sealed tool_result payload replaces it)"
                )
        if benchmark and not (consents and all(consents)):
            raise ValueError(
                f"[consent] the tool calls of run {run.id} are human-subject data, and the run's "
                "human-subject artifacts and transcripts do not all record consent.status="
                "obtained (B7)"
            )


def _check_expected_purpose(expected: object) -> None:
    """Check the ``expected_purpose`` argument itself (a call error, never tagged): ``None``
    passes; a value that is not a ``str`` (a ``bool``, ``bytes``, a list) is a ``TypeError``; a
    ``str`` outside ``EXPORT_PURPOSES`` (case and white space matter) is a ``ValueError``."""
    if expected is None:
        return
    if type(expected) is not str:
        raise TypeError(f"expected_purpose must be a str or None, got {type(expected).__name__}")
    if expected not in EXPORT_PURPOSES:
        raise ValueError(f"expected_purpose must be one of {', '.join(EXPORT_PURPOSES)}")


def check_export_bundle(bundle: ExportBundle, *, expected_purpose: str | None = None) -> None:
    """The rules of an ``ExportBundle`` (api-surface.md 25.4.2).

    ``expected_purpose`` (keyword-only): ``None`` (the default) accepts any purpose. The bundle's
    own ``purpose`` label is self-declared by its producer, so a consumer that needs a purpose
    passes it here: the argument is checked first (``TypeError`` if it is not a ``str``,
    ``ValueError`` if it is not one of ``internal``, ``shared_export``, ``benchmark``), then a
    bundle whose label differs is refused with ``[purpose]`` (naming both purposes, nothing of the
    bundle's content) before any other rule. Every purpose: the seam (typed errors),
    ``[caller_external_id]``, ``[duplicate]``, ``[sealed]``, ``[redaction]``, ``[unresolved]`` for
    dataset members and ``[provenance_redaction]``. ``internal``: the plain profile digest
    (``[profile_digest]``). ``shared_export`` and ``benchmark``: ``[profile]``,
    ``[profile_digest]``, ``[uri]``, ``[source_run]``, ``[unprotected]`` and, for a benchmark,
    ``[consent]``. Each embedded run bundle has passed ``check_run_bundle`` already. Pure and
    idempotent; the model runs it on load (with ``expected_purpose=None``)."""
    _check_expected_purpose(expected_purpose)
    if expected_purpose is not None and bundle.purpose != expected_purpose:
        raise ValueError(f"[purpose] expected {expected_purpose}, the bundle says {bundle.purpose}")
    _export_seam(bundle)
    for run_bundle in bundle.run_bundles:
        if run_bundle.run.caller_external_id is not None:
            raise ValueError(
                f"[caller_external_id] run {run_bundle.run.id} carries a caller_external_id; an "
                "export never holds one (the export builder strips it)"
            )
    _check_export_identity(bundle)
    index = _ExportIndex(bundle)
    _check_sealed_payloads(bundle, index)
    _check_payload_redactions(bundle, index)
    items = _check_datasets(bundle)
    if bundle.purpose == "internal":
        for run_bundle in bundle.run_bundles:
            try:
                check_internal_snapshot(run_bundle.snapshot)
            except ValueError as exc:
                raise _tagged("profile_digest", exc) from None
        return
    for run_bundle in bundle.run_bundles:
        _check_portable_run_bundle(run_bundle)
    marked = _items_marking_payloads(bundle, items, index)
    _check_human_subject_payloads(bundle, index, marked)
