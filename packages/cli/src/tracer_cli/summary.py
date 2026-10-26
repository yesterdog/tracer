# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The facts of a run that the command prints and the report shows, read from the bundle alone.

Nothing here reads a file, the clock or the environment. A number is printed as stored: the
``repr`` of a float, the integer as it is. A fact that the bundle does not hold is not guessed;
it is "none" or "not available" with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from tracer_evalcore.flagsdoc import FLAGS_METADATA_KEY
from tracer_evalcore.registry import COUNT_KEY, MER_KEY, SHARE_KEY
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.metrics import RangeRule
from tracer_evalschema.primitives import canonical_decimal_text
from tracer_evalschema.results import MetricResult
from tracer_evalschema.transcript import Transcript, Turn

__all__ = [
    "BudgetFacts",
    "Facts",
    "budget_facts",
    "cost_text",
    "export_transcript_of",
    "export_turns_of",
    "facts_of",
    "not_scored_reasons",
    "render_summary",
    "stt_failure_detail",
    "summary_lines",
    "usage_counts",
    "usage_text",
]

_ERROR_REASONS: Final = {
    "stt_unsupported_language": "unsupported language detected",
    "stt_language_not_observed": "language not observed in the stt transcript",
    "alignment_too_large": "input too large to compare",
    "levenshtein_too_large": "input too large to compare",
    "stt_provenance_missing": "speech-to-text provenance missing",
    "flags_document_refused": "flags document refused",
}
_NOT_APPLICABLE_REASONS: Final = {
    "unsupported_language": "unsupported language detected",
    "language_not_observed": "language not observed",
}
_NO_COST_RECORD: Final = "not available (no cost record)"


@dataclass(frozen=True)
class Facts:
    """The shipped results of a bundle by metric key, and the flags document of a scored one."""

    results: dict[str, MetricResult]
    document: dict[str, Any] | None


@dataclass(frozen=True)
class BudgetFacts:
    """The review budget: ``maximum`` as stored, whether it can never fail, and whether it held."""

    maximum: str
    no_limit: bool
    met: bool | None


def facts_of(bundle: RunBundle) -> Facts:
    keys = {definition.id: definition.key for definition in bundle.snapshot.metric_definitions}
    results = {keys[r.metric.id]: r for r in bundle.results if r.metric.id in keys}
    document: dict[str, Any] | None = None
    for result in bundle.results:
        if result.status == "scored":
            found = (result.metadata or {}).get(FLAGS_METADATA_KEY)
            if isinstance(found, dict):
                document = found
            break
    return Facts(results, document)


def export_transcript_of(bundle: RunBundle) -> Transcript:
    for transcript in bundle.transcripts:
        if transcript.source == "agent_export":
            return transcript
    raise ValueError("the bundle holds no export transcript")


def export_turns_of(bundle: RunBundle) -> list[Turn]:
    export = export_transcript_of(bundle)
    return sorted((t for t in bundle.turns if t.transcript_id == export.id), key=lambda t: t.seq)


def stt_failure_detail(bundle: RunBundle) -> str | None:
    for warning in bundle.run.ingest_warnings:
        if warning.code == "stt_failed":
            return str(warning.message)
    return None


def _result_reason(result: MetricResult) -> str:
    if result.status == "not_applicable":
        if result.na_reason == "missing_input":
            return "missing input " + ", ".join(result.missing_kinds or [])
        reason = result.na_reason or "not_applicable"
        return _NOT_APPLICABLE_REASONS.get(reason, reason.replace("_", " "))
    if result.error is None:
        return "error"
    code = result.error.code
    if code == "no_tokens":
        side = "export" if result.error.message.startswith("the export") else "stt transcript"
        return f"no tokens in the {side}"
    return _ERROR_REASONS.get(code, code)


def not_scored_reasons(bundle: RunBundle) -> tuple[str, ...]:
    if stt_failure_detail(bundle) is not None:
        return ("speech-to-text failed",)
    reasons: list[str] = []
    for result in bundle.results:
        if result.status == "scored":
            continue
        reason = _result_reason(result)
        if reason not in reasons:
            reasons.append(reason)
    return tuple(reasons)


def usage_counts(bundle: RunBundle) -> tuple[int, str] | None:
    """``(requests, audio seconds as canonical decimal text)``, or None when nothing was used."""
    if not bundle.usage_records:
        return None
    requests = 0
    seconds = Decimal(0)
    for record in bundle.usage_records:
        for quantity in record.quantities:
            if quantity.unit == "requests":
                requests += int(quantity.count)
            elif quantity.unit == "audio_input_seconds":
                seconds += Decimal(str(quantity.count))
    return requests, canonical_decimal_text(seconds)


def usage_text(bundle: RunBundle) -> str:
    counts = usage_counts(bundle)
    if counts is None:
        return "none"
    return f"{counts[0]} requests, {counts[1]} audio seconds"


def cost_text(bundle: RunBundle) -> str:
    summaries = [s for s in bundle.cost_summaries if s.domain == "evaluation"]
    if not summaries:
        return _NO_COST_RECORD
    summary = summaries[0]
    records = [r for r in bundle.cost_records if r.domain == "evaluation"]
    total = summary.total
    if summary.completeness == "complete" and total is not None:
        return f"{total.amount} {total.currency} (computed from the supplied USD price)"
    if summary.completeness == "partial" and total is not None:
        return (
            f"partial: {total.amount} {total.currency} "
            f"({len(summary.included)} of {len(records)} usage records priced)"
        )
    reasons = sorted({r.na_reason for r in records if r.na_reason})
    return f"not available ({', '.join(reasons) if reasons else 'no cost record'})"


def _number(value: object) -> str:
    return repr(value) if isinstance(value, float) else str(value)


def budget_facts(bundle: RunBundle, facts: Facts) -> BudgetFacts | None:
    """The range pass rule of the share definition in the snapshot, or None when it has none."""
    for definition in bundle.snapshot.metric_definitions:
        if definition.key != SHARE_KEY:
            continue
        rule = definition.pass_rule
        if not isinstance(rule, RangeRule) or rule.max is None:
            return None
        no_limit = Decimal(str(rule.max)) == 1 and rule.inclusive
        result = facts.results.get(SHARE_KEY)
        met = None if no_limit or result is None else result.passed
        return BudgetFacts(_number(rule.max), no_limit, met)
    return None


def _scored_lines(
    bundle: RunBundle, facts: Facts, document: dict[str, Any]
) -> list[tuple[str, str]]:
    alignment = document["alignment"]
    hits, subs = alignment["hits"], alignment["substitutions"]
    dels, ins = alignment["deletions"], alignment["insertions"]
    edits = subs + dels + ins
    tokens = document["tokens"]
    stt = document["stt"]
    flagged_turns = len({s["turn_seq"] for s in document["spans"] if s["flagged"]})
    lines = [
        ("stt provider", stt["provider"]),
        ("stt model", f"{stt['model']} (as requested)"),
        ("normaliser", document["normaliser"]),
        (
            "agreement distance (MER)",
            f"{_number(facts.results[MER_KEY].value)} "
            f"({edits} of {hits + edits}; H {hits}, S {subs}, D {dels}, I {ins})",
        ),
        ("tokens", f"{tokens['export']} in the export, {tokens['stt']} in the stt transcript"),
        (
            "flags",
            f"{facts.results[COUNT_KEY].value} "
            f"(confidence at or above {document['flag_min_confidence']})",
        ),
        (
            "flagged share of export tokens",
            f"{tokens['flagged_export']} of {tokens['export']} "
            f"({_number(facts.results[SHARE_KEY].value)})",
        ),
        ("export turns with a flag", f"{flagged_turns} of {len(export_turns_of(bundle))}"),
    ]
    budget = budget_facts(bundle, facts)
    if budget is not None:
        if budget.no_limit:
            lines.append(("review budget", f"{budget.maximum} (no limit)"))
        else:
            state = "within" if budget.met else "over"
            lines.append(("review budget", f"{budget.maximum} (flagged share is {state} it)"))
    return lines


def _printable(text: str) -> str:
    return "".join(c if " " <= c <= "~" else "?" for c in text)


def summary_lines(bundle: RunBundle, outcome: str) -> list[tuple[str, str]]:
    """The (label, value) pairs of a run, in the order the command prints them."""
    facts = facts_of(bundle)
    declared = export_transcript_of(bundle).declared_language
    scored = outcome == "scored" and facts.document is not None
    lines: list[tuple[str, str]] = [("result", "scored" if scored else "not scored")]
    if declared is not None:
        lines.append(("language declared by user", declared.language))
    if scored and facts.document is not None:
        lines.extend(_scored_lines(bundle, facts, facts.document))
    else:
        lines.extend(("not scored", reason) for reason in not_scored_reasons(bundle))
        detail = stt_failure_detail(bundle)
        if detail is not None:
            lines.append(("detail", _printable(detail)))
    lines.append(("stt usage", usage_text(bundle)))
    lines.append(("cost", cost_text(bundle)))
    lines.append(("ingest warnings", str(len(bundle.run.ingest_warnings))))
    return lines


def render_summary(bundle: RunBundle, *, outcome: str, stt_failure: object | None) -> str:
    """The lines the command prints for a run, before its bundle, store and report lines.

    ``stt_failure`` is the pipeline's own value; the text comes from the Run's warning, which the
    report reads too, so that the command and the report cannot differ.
    """
    del stt_failure
    return "".join(f"{label}: {value}\n" for label, value in summary_lines(bundle, outcome))
