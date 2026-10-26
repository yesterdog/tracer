# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Result mapping: the three MetricResult records of a recorded call (ADR 0023 rules 11 to 15).

Pure: no clock, no I/O, no log. The applicability of each result is the one
``tracer_evalschema.results.applicability`` gives for the transcripts the result names, which is
what ``check_run_bundle`` re-runs, so a stored result can never disagree with the re-run. The
alignment is computed once and shared by the three results. A failure is an ``error`` result
whose message holds counts and sizes only, never a tag or a text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from tracer_evalcore.align import AlignmentTooLargeError, align
from tracer_evalcore.definitions import (
    RecordedConfiguration,
    ScoringParameters,
    parameters_of,
)
from tracer_evalcore.flags import (
    STT_DISAGREEMENT,
    LevenshteinTooLargeError,
    SpanFlag,
    flag_spans,
)
from tracer_evalcore.flagsdoc import (
    FLAGS_METADATA_KEY,
    FlagsDocumentError,
    build_flags_document,
    validate_flags_document,
)
from tracer_evalcore.registry import (
    COUNT_KEY,
    DEFAULT_REGISTRY,
    MER_KEY,
    SHARE_KEY,
    SHIPPED_KEYS,
    ScorerRegistry,
    scorer_info,
)
from tracer_evalcore.spans import extract_spans
from tracer_evalcore.tokens import TextTurn, TimedSegment, tokenise_turns
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.base import compute_metadata_flags
from tracer_evalschema.events import EvidenceRef
from tracer_evalschema.ids import ArtifactId, MetricDefinitionId, MetricResultId, TurnId
from tracer_evalschema.language import primary_language
from tracer_evalschema.metrics import MetricDefinition, MetricDefinitionRef, rule_passes
from tracer_evalschema.results import (
    Applicability,
    MetricResult,
    ResultError,
    applicability,
)
from tracer_evalschema.run import Run
from tracer_evalschema.transcript import Transcript, Turn

__all__ = ["score_results"]


@dataclass(frozen=True)
class _Failure:
    code: str
    message: str


@dataclass(frozen=True)
class _Analysis:
    values: dict[str, int | float]
    evidence: tuple[EvidenceRef, ...]
    document: dict[str, Any]


def _text_turns(turns: Sequence[Turn], *, timed: bool) -> list[TextTurn]:
    result = []
    for turn in sorted(turns, key=lambda t: t.seq):
        segments: tuple[TimedSegment, ...] = ()
        if timed and turn.segments:
            segments = tuple(TimedSegment(s.text, s.start_ms, s.end_ms) for s in turn.segments)
        result.append(TextTurn(turn.seq, turn.text, segments))
    return result


def _language_gate(stt_turns: Sequence[Turn], definition: MetricDefinition) -> _Failure | None:
    """ADR 0023 rule 14: the STT language is the scorer's to check, over the turns that exist."""
    total = len(stt_turns)
    unobserved = sum(
        1 for t in stt_turns if t.language is None or primary_language(t.language) == "und"
    )
    if unobserved:
        return _Failure(
            "stt_language_not_observed",
            f"{unobserved} of {total} stt turns carry no observed language",
        )
    covered = {primary_language(tag) for tag in definition.languages}
    other = sum(1 for t in stt_turns if primary_language(t.language or "") not in covered)
    if other:
        return _Failure(
            "stt_unsupported_language",
            f"{other} of {total} stt turns carry a language other than the covered one",
        )
    return None


def _evidence(
    flagged: Sequence[SpanFlag], export_turns: Sequence[Turn], audio_artifact_id: ArtifactId | None
) -> tuple[EvidenceRef, ...]:
    """Flagged spans only: export turns in ``seq`` order, then audio ranges in span order."""
    by_seq = {turn.seq: turn for turn in export_turns}
    refs = [
        EvidenceRef(kind="turn", ref_id=cast(TurnId, by_seq[seq].id))
        for seq in sorted({flag.span.turn_seq for flag in flagged})
    ]
    if audio_artifact_id is not None:
        seen: set[tuple[int, int]] = set()
        for flag in flagged:
            start, end = flag.span.audio_start_ms, flag.span.audio_end_ms
            if start is None or end is None or (start, end) in seen:
                continue
            seen.add((start, end))
            refs.append(
                EvidenceRef(
                    kind="artifact_range", ref_id=audio_artifact_id, start_ms=start, end_ms=end
                )
            )
    return tuple(refs)


def _analyse(
    *,
    parameters: ScoringParameters,
    export_turns: Sequence[Turn],
    stt: Transcript,
    stt_turns: Sequence[Turn],
    audio_artifact_id: ArtifactId | None,
) -> _Analysis | _Failure:
    export_sequence = tokenise_turns(_text_turns(export_turns, timed=False), parameters.normaliser)
    if not export_sequence.tokens:
        return _Failure("no_tokens", "the export has no token")
    stt_sequence = tokenise_turns(_text_turns(stt_turns, timed=True), parameters.normaliser)
    if not stt_sequence.tokens:
        return _Failure("no_tokens", "the stt transcript has no token")
    try:
        alignment = align(export_sequence, stt_sequence)
        spans = extract_spans(alignment, export_sequence, stt_sequence)
        flags = flag_spans(
            spans,
            export_sequence,
            stt_sequence,
            signal=STT_DISAGREEMENT,
            flag_min_confidence=parameters.flag_min_confidence,
        )
    except AlignmentTooLargeError as exc:
        return _Failure("alignment_too_large", str(exc))
    except LevenshteinTooLargeError as exc:
        return _Failure("levenshtein_too_large", str(exc))
    if stt.provider is None or stt.model is None:
        return _Failure("stt_provenance_missing", "the stt transcript names no provider or model")
    document = build_flags_document(
        normaliser=parameters.normaliser,
        provider=stt.provider,
        model=stt.model,
        threshold=parameters.flag_min_confidence,
        alignment=alignment,
        export=export_sequence,
        stt=stt_sequence,
        flags=flags,
    )
    try:
        validate_flags_document(
            document,
            normaliser=parameters.normaliser,
            threshold=parameters.flag_min_confidence,
            model=stt.model,
            export=export_sequence,
        )
    except FlagsDocumentError as refusal:
        return _Failure("flags_document_refused", f"the flags document was refused: {refusal.code}")
    if compute_metadata_flags({FLAGS_METADATA_KEY: document}):
        return _Failure(
            "flags_document_refused", "the flags document was refused: metadata_flagged"
        )
    flagged = [flag for flag in flags if flag.flagged]
    values: dict[str, int | float] = {
        MER_KEY: alignment.mer,
        COUNT_KEY: len(flagged),
        SHARE_KEY: sum(flag.span.export_tokens for flag in flagged) / len(export_sequence.tokens),
    }
    return _Analysis(values, _evidence(flagged, export_turns, audio_artifact_id), document)


def score_results(
    *,
    configuration: RecordedConfiguration,
    run: Run,
    export: Transcript | None,
    export_turns: Sequence[Turn],
    stt: Transcript | None,
    stt_turns: Sequence[Turn],
    audio_artifact_id: ArtifactId | None,
    created_at: datetime,
    registry: ScorerRegistry = DEFAULT_REGISTRY,
) -> tuple[MetricResult, ...]:
    """The three results of one recorded call, in the order of ``SHIPPED_KEYS``."""
    parameters = parameters_of(configuration.snapshot)
    held = [t for t in (export, stt) if t is not None]
    held_ids = {t.id for t in held}
    transcript_ids = [t.id for t in held] or None
    read = [t for t in (*export_turns, *stt_turns) if t.transcript_id in held_ids]
    agent_turns = [t for t in read if t.role == "agent"]

    analysis: _Analysis | _Failure | None = None
    results: list[MetricResult] = []
    for key in SHIPPED_KEYS:
        definition = configuration.definition(key)
        answer = applicability(definition, run, turns=agent_turns, transcripts=held)
        common: dict[str, Any] = {
            "id": MetricResultId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "metric": MetricDefinitionRef(
                id=cast(MetricDefinitionId, definition.id),
                version=definition.version,
                content_digest=definition.content_digest,
            ),
            "transcript_ids": transcript_ids,
        }
        if answer.na_reason is not None:
            results.append(_not_applicable(common, answer))
            continue
        if export is None or stt is None:
            raise ValueError("the run lists inputs that were not supplied")
        provenance: dict[str, Any] = {
            "scorer": scorer_info(registry.get(definition.implementation_ref or "")),
            "language_source": answer.language_source,
            "declared_language_transcript_ids": list(answer.declared_language_transcript_ids),
        }
        stt_read = [t for t in stt_turns if t.transcript_id == stt.id]
        failure = _language_gate(stt_read, definition)
        if failure is None:
            if analysis is None:
                analysis = _analyse(
                    parameters=parameters,
                    export_turns=[t for t in export_turns if t.transcript_id == export.id],
                    stt=stt,
                    stt_turns=stt_read,
                    audio_artifact_id=audio_artifact_id,
                )
            if isinstance(analysis, _Failure):
                failure = analysis
        if failure is not None:
            results.append(
                MetricResult(
                    **common,
                    status="error",
                    value=None,
                    error=ResultError(code=failure.code, message=failure.message),
                    **provenance,
                )
            )
            continue
        assert isinstance(analysis, _Analysis)  # noqa: S101
        value = analysis.values[key]
        results.append(
            MetricResult(
                **common,
                status="scored",
                value=value,
                passed=None
                if definition.pass_rule is None
                else rule_passes(definition.pass_rule, value),
                evidence_refs=list(analysis.evidence),
                metadata={FLAGS_METADATA_KEY: analysis.document},
                **provenance,
            )
        )
    return tuple(results)


def _not_applicable(common: dict[str, Any], answer: Applicability) -> MetricResult:
    return MetricResult(
        **common,
        status="not_applicable",
        value=None,
        na_reason=answer.na_reason,
        missing_kinds=list(answer.missing_kinds),
        language_source=answer.language_source,
        declared_language_transcript_ids=list(answer.declared_language_transcript_ids),
    )
