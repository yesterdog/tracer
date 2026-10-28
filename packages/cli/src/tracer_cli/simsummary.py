# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The facts of a simulated run that the command prints and the report shows, read from the bundle.

Nothing here reads a file, the clock or the environment. A timing value is printed as stored and
always with what it is: "observed at the simulator over <dialect>", its quantum, and N for a median
(ADR 0023 amendment A1, ADR 0027 rule 10). A value that was not measured says so and names the
missing edge; it is never 0. The words of the claim deny-list never appear in this text.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from tracer_cli.judgesummary import judge_lines, judge_usage_present
from tracer_cli.summary import cost_text
from tracer_evalcore.registry import COUNT_KEY, MER_KEY, SHARE_KEY
from tracer_evalcore.simoutcome import SIM_OUTCOME_METADATA_KEY
from tracer_evalcore.simprofile import SIM_PROFILE_METADATA_KEY
from tracer_evalcore.simscore import SIM_AGREEMENT_KEYS, SIM_TIMING_KEYS
from tracer_evalcore.simtiming import TIMING_METADATA_KEY
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.primitives import canonical_decimal_text
from tracer_evalschema.results import MetricResult

__all__ = [
    "EXIT_NO_CALL",
    "SIM_TIMING_KEYS",
    "SimFacts",
    "exit_code_of",
    "sim_facts",
    "sim_summary_lines",
    "stage_usage_text",
    "timing_lines",
]

EXIT_NO_CALL: Final = 5
_PARTITION_TO_RECORDED: Final = dict(
    zip(SIM_AGREEMENT_KEYS, (MER_KEY, COUNT_KEY, SHARE_KEY), strict=True)
)


@dataclass(frozen=True)
class SimFacts:
    outcome: dict[str, Any]
    timing: dict[str, Any]
    profile: dict[str, Any]
    results: dict[str, MetricResult]


def sim_facts(bundle: RunBundle) -> SimFacts:
    keys = {d.id: d.key for d in bundle.snapshot.metric_definitions}
    return SimFacts(
        outcome=dict(bundle.run.metadata[SIM_OUTCOME_METADATA_KEY]),
        timing=dict(bundle.run.metadata[TIMING_METADATA_KEY]),
        profile=dict(bundle.snapshot.metadata[SIM_PROFILE_METADATA_KEY]),
        results={keys[r.metric.id]: r for r in bundle.results if r.metric.id in keys},
    )


def exit_code_of(bundle: RunBundle) -> int:
    """5 when the call was not established, 3 when the speech-to-text failed, 2 when a result is an
    error or the caller's language model failed (``brain_failed``), else 0 (precedence 1, 5, 3, 2,
    0; the 1 is the command's own, ADR 0026 rule 19)."""
    facts = sim_facts(bundle)
    if not facts.outcome["call_established"]:
        return EXIT_NO_CALL
    if any(w.code == "stt_failed" for w in bundle.run.ingest_warnings):
        return 3
    if facts.outcome["end_reason"] == "brain_failed":
        return 2
    if any(r.status == "error" for r in bundle.results):
        return 2
    return 0


def _edge(result: MetricResult) -> str:
    message = result.error.message if result.error is not None else ""
    for part in message.split(";"):
        if part.strip().startswith("missing edge:"):
            return part.strip()
    return "missing edge"


def _not_measured(result: MetricResult) -> str:
    if result.status == "error":
        return f"not measured ({_edge(result)})"
    kinds = ", ".join(result.missing_kinds or [])
    reason = result.na_reason or "not_applicable"
    return f"not measured (missing input {kinds})" if kinds else f"not measured ({reason})"


def timing_lines(facts: SimFacts) -> list[tuple[str, str]]:
    dialect = facts.timing["dialect"]
    quantum = facts.timing["quantum_ms"]
    note = f"observed at the simulator over {dialect}, quantum {quantum} ms"
    rows = (
        ("sim_first_agent_audio_ms", "first agent audio", " ms", None),
        (
            "sim_agent_response_gap_median_ms",
            "gap after the caller's line (median)",
            " ms",
            facts.timing["gap_n"],
        ),
        ("sim_overlap_ms", "overlap", " ms", None),
        (
            "sim_barge_in_stop_ms",
            "time for the agent's audio to stop (median)",
            " ms",
            facts.timing["barge_in_n"],
        ),
        ("sim_answered_caller_turns", "answered caller turns", "", None),
    )
    lines: list[tuple[str, str]] = []
    for key, label, unit, count in rows:
        result = facts.results.get(key)
        if result is None:
            continue
        if result.status == "scored":
            suffix = f", N {count}" if count is not None else ""
            lines.append((label, f"{result.value}{unit} ({note}{suffix})"))
        else:
            lines.append((label, _not_measured(result)))
    return lines


def stage_usage_text(bundle: RunBundle, stage: str) -> str:
    records = [r for r in bundle.usage_records if r.stage == stage]
    if not records:
        return "none"
    totals: dict[str, Decimal] = {}
    for record in records:
        for quantity in record.quantities:
            totals[quantity.unit] = totals.get(quantity.unit, Decimal(0)) + Decimal(
                str(quantity.count)
            )

    def count(unit: str) -> str:
        return canonical_decimal_text(totals.get(unit, Decimal(0)))

    if stage == "sim_caller_tts":
        return f"{count('requests')} requests, {count('input_characters')} characters"
    if stage in ("sim_caller_llm", "judge"):
        return (
            f"{count('requests')} requests, {count('input_tokens')} input tokens, "
            f"{count('output_tokens')} output tokens"
        )
    return f"{count('requests')} requests, {count('audio_input_seconds')} audio seconds"


def _agreement_lines(bundle: RunBundle, facts: SimFacts) -> list[tuple[str, str]]:
    mer = facts.results.get(SIM_AGREEMENT_KEYS[0])
    if mer is None:
        return []
    if mer.status == "scored":
        document = (mer.metadata or {}).get("tracer.flags", {})
        alignment = document.get("alignment", {})
        hits, subs = alignment.get("hits", 0), alignment.get("substitutions", 0)
        dels, ins = alignment.get("deletions", 0), alignment.get("insertions", 0)
        edits = subs + dels + ins
        count = facts.results[SIM_AGREEMENT_KEYS[1]].value
        return [
            (
                "agreement distance (MER)",
                f"{mer.value!r} ({edits} of {hits + edits}; H {hits}, S {subs}, D {dels}, "
                f"I {ins}) between the agent's own export and our speech-to-text of the agent",
            ),
            ("flags", str(count)),
        ]
    return [("agreement", _not_scored_text(mer))]


def _not_scored_text(result: MetricResult) -> str:
    if result.status == "not_applicable":
        if result.na_reason == "missing_input":
            return "not scored (missing input " + ", ".join(result.missing_kinds or []) + ")"
        return "not scored (" + (result.na_reason or "not applicable").replace("_", " ") + ")"
    return "not scored (" + (result.error.code if result.error else "error") + ")"


def sim_summary_lines(bundle: RunBundle) -> list[tuple[str, str]]:
    facts = sim_facts(bundle)
    outcome, counts = facts.outcome, facts.outcome["counts"]
    established = outcome["call_established"]
    lines: list[tuple[str, str]] = [
        ("result", "call established" if established else "call not established"),
        ("end reason", str(outcome["end_reason"])),
        ("outcome", "not evaluated"),
        ("dialect", str(outcome["dialect"])),
        ("caller audio", "synthetic (generated speech, not a person)"),
        (
            "caller turns",
            f"{counts['caller_turns_started']} started, "
            f"{counts['caller_turns_completed']} completed",
        ),
        ("agent segments", str(counts["agent_segments"])),
    ]
    cap = outcome["cap_reached"]
    if cap is not None:
        lines.append(("cap reached", str(cap)))
    lines.extend(timing_lines(facts))
    lines.extend(_agreement_lines(bundle, facts))
    lines.extend(judge_lines(bundle))
    lines.append(("tts usage", stage_usage_text(bundle, "sim_caller_tts")))
    lines.append(("llm usage", stage_usage_text(bundle, "sim_caller_llm")))
    lines.append(("stt usage", stage_usage_text(bundle, "stt")))
    if judge_usage_present(bundle):
        lines.append(("judge usage", stage_usage_text(bundle, "judge")))
    lines.append(("cost", cost_text(bundle)))
    lines.append(("ingest warnings", str(len(bundle.run.ingest_warnings))))
    return lines
