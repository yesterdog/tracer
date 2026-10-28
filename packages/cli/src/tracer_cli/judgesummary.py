# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The text of a judged run: the judge lines of the command output and the judge section of the
report share this module (ADR 0030 rule 9; ADR 0023 amendment A4).

Every line says that the signal is uncalibrated, shows per item the verdict, the reason code, the
fact statuses and "n of N" counts with their denominators, and nothing else: no confidence, no
probability, no rate, no pass or fail. The text of the golden set (the expected facts) is never
printed. Nothing here reads a file, the clock or the environment.
"""

from __future__ import annotations

from typing import Any, Final

from tracer_evalcore.judgelabels import JudgedItem, judged_items
from tracer_evalcore.judgerecords import JUDGE_PROFILE_METADATA_KEY
from tracer_evalschema.bundle import RunBundle

__all__ = [
    "JUDGE_HEADLINE",
    "item_text",
    "judge_lines",
    "judge_profile_of",
    "judge_usage_present",
    "plan_lines",
]

JUDGE_HEADLINE: Final = "uncalibrated signal"


def judge_profile_of(bundle: RunBundle) -> dict[str, Any] | None:
    """The ``tracer.judge_profile/1`` document of the snapshot, or ``None`` for an unjudged run."""
    found = bundle.snapshot.metadata.get(JUDGE_PROFILE_METADATA_KEY)
    return dict(found) if isinstance(found, dict) else None


def judge_usage_present(bundle: RunBundle) -> bool:
    return judge_profile_of(bundle) is not None


def _facts_text(item: JudgedItem) -> str:
    counts = dict.fromkeys(("stated", "contradicted", "absent", "cannot_tell"), 0)
    for _, status in item.facts:
        counts[status] += 1
    return (
        f"{counts['stated']} of {len(item.facts)} stated, {counts['contradicted']} contradicted, "
        f"{counts['absent']} absent, {counts['cannot_tell']} cannot_tell"
    )


def item_text(item: JudgedItem) -> str:
    """One item: its verdict and the closed facts behind it, or why there is none."""
    if item.status == "not_applicable":
        reason = item.na_reason or "not_applicable"
        if reason == "missing_input" and item.missing_kinds:
            return f"not applicable (missing input {', '.join(item.missing_kinds)})"
        return f"not applicable ({reason.replace('_', ' ')})"
    if item.verdict is None:
        return f"error ({item.error_code}; requests {item.attempts})"
    parts = [f"reason {item.reason_code}"]
    if item.facts:
        parts.append(f"facts {_facts_text(item)}")
    parts.append(f"requests {item.attempts}")
    lead = item.verdict
    if item.status == "error":
        lead = f"{item.verdict} (error {item.error_code}; "
        return lead + "; ".join(parts) + ")"
    return f"{lead} (" + "; ".join(parts) + ")"


def judge_lines(bundle: RunBundle) -> list[tuple[str, str]]:
    """The judge lines of a judged run (empty for a run without a golden set)."""
    profile = judge_profile_of(bundle)
    if profile is None:
        return []
    items = judged_items(bundle)
    total = len(items)

    def count(test: Any) -> str:
        return f"{sum(1 for i in items if test(i))} of {total} items"

    lines = [
        (
            "judge",
            f"{JUDGE_HEADLINE} (golden set {profile['golden_set']['key']}, engine "
            f"{profile['profile']}, model {profile['model']})",
        ),
        ("judge agrees", count(lambda i: i.status == "scored" and i.verdict == "agrees")),
        ("judge disagrees", count(lambda i: i.status == "scored" and i.verdict == "disagrees")),
        ("judge cannot_tell", count(lambda i: i.verdict == "cannot_tell")),
        (
            "judge error",
            count(lambda i: i.status == "error" and i.verdict != "cannot_tell"),
        ),
        ("judge not applicable", count(lambda i: i.status == "not_applicable")),
    ]
    lines.extend((f"judge item {i.item_id}", item_text(i)) for i in items)
    return lines


def plan_lines(
    *,
    engine: str,
    model: str,
    golden_key: str,
    modes: dict[str, int],
    max_tokens: int,
    max_requests: int | None,
    max_tokens_total: int | None,
) -> list[str]:
    """What a dry run says about the judge; nothing is sent."""
    items = sum(modes.values())
    mode_text = ", ".join(f"{n} {name}" for name, n in sorted(modes.items()))
    return [
        f"judge: {JUDGE_HEADLINE}, engine {engine}, model {model}",
        f"judge golden set: {golden_key}, {items} items ({mode_text})",
        f"judge requests: up to {2 * items} (one for each item, one more for each regeneration)",
        f"judge max tokens per request: {max_tokens}",
        f"judge max requests: {'not set' if max_requests is None else max_requests}",
        f"judge max tokens: {'not set' if max_tokens_total is None else max_tokens_total}",
    ]
