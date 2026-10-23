# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the bundle applicability tests (batch 23, finding B15 / decision D1). No tests
here.

Contract: ``docs/phase1/api-surface-applicability.md``. The tests build their bundles from the T-DEG
worlds of ``_support_b13`` (13 metric definitions run at T0, T0m, T1 and T2 by the reference
evaluator, which decides each result with ``results.applicability``), so every stored result of a
world is the one the rule has to reproduce. A forged bundle is a world document with one edit.

Three kinds of helper:

* ``SCENARIOS`` and ``world_of``: the consistent worlds the rule must accept unchanged;
* ``transplant`` and ``first_differences``: put the result a different world stored for a metric
  into this world's document (a result that is well formed on its own and wrong for this run); the
  expected verdict is decided by comparing the two stored documents field by field, never by the
  library;
* ``multi_world``: a world that holds two transcripts (the case ``MetricResult.transcript_ids``
  exists for).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from ._support_b2 import new_id
from ._support_b3 import transcript_doc, turn_doc
from ._support_b13 import (
    AGENT_TEXTS,
    CALLER_TEXTS,
    TdegWorld,
    cached_world,
    rebuild_verdict,
)

__all__ = [
    "COMPARED_FIELDS",
    "NEEDS_TRANSCRIPT_IDS",
    "NO_TRANSCRIPT_IDS_NEEDED",
    "RUN_LEVEL_REASONS",
    "SCENARIOS",
    "Scenario",
    "TwoTranscripts",
    "agent_turn_ids",
    "check_failure",
    "declared_language_doc",
    "definition_of",
    "error_doc",
    "first_differences",
    "forge",
    "load_failure",
    "mentioned_fields",
    "multi_world",
    "reject_text",
    "result_of",
    "set_roles",
    "text_of_error",
    "transplant",
    "turn_texts",
    "with_results",
    "world_of",
]

# The five fields the rule compares, in the order of the contract (status class first).
COMPARED_FIELDS: tuple[str, ...] = (
    "status",
    "na_reason",
    "missing_kinds",
    "language_source",
    "declared_language_transcript_ids",
)

RUN_LEVEL_REASONS: tuple[str, ...] = ("missing_input", "agent_channel_unknown")

# Which of the 13 T-DEG metrics need MetricResult.transcript_ids once the bundle holds several
# transcripts (decision T-A3: requires has transcript or agent_transcript, or the metric is language
# dependent). Written by hand from the table in api-surface-tdeg.md section 1.
NEEDS_TRANSCRIPT_IDS: tuple[str, ...] = (
    "goal_completion",
    "agent_audio_clarity",
    "agent_politeness",
    "agent_wer",
    "turn_politeness_share",
    "turn_length_share",
    "agent_turn_count",
    "script_consistency",
)
NO_TRANSCRIPT_IDS_NEEDED: tuple[str, ...] = (
    "p95_latency",
    "tool_call_success",
    "stage_ttfb_p95",
    "silence_ratio",
    "agent_input_tokens",
)

TEXT_POOL: tuple[str, ...] = (*AGENT_TEXTS, *CALLER_TEXTS)


@dataclass(frozen=True)
class Scenario:
    """One consistent T-DEG bundle: a column of the matrix and the options of ``deg_world``."""

    name: str
    column: str
    options: tuple[tuple[str, Any], ...] = ()


def _s(name: str, column: str, **options: Any) -> Scenario:
    return Scenario(name, column, tuple(sorted(options.items())))


SCENARIOS: tuple[Scenario, ...] = (
    _s("T0", "T0"),
    _s("T0-empty-stt", "T0", stt=False),
    _s("T0-reported-cost", "T0", agent_cost="reported_inr"),
    _s("T0m", "T0m"),
    _s("T1", "T1"),
    _s("T1-no-spans", "T1", spans=False),
    _s("T1-human", "T1", origin="human"),
    _s("T2", "T2"),
    _s("T2-human", "T2", origin="human"),
    _s("T1-untagged", "T1", language="untagged"),
    _s("T1-declared", "T1", language="declared"),
    _s("T2-declared", "T2", language="declared"),
    _s("T1-unsupported-language", "T1", language="unsupported_language"),
    _s("T1-romanized", "T1", language="romanized"),
    _s("T1-mixed", "T1", language="mixed"),
    _s("T1-untagged-foreign", "T1", language="untagged_foreign"),
    _s("T0-untagged", "T0", language="untagged"),
    _s("T0m-romanized", "T0m", language="romanized"),
)


def world_of(scenario: Scenario) -> TdegWorld:
    return cached_world(scenario.column, **dict(scenario.options))


# ------------------------------------------------------------------------------ result lookups


def definition_of(world: TdegWorld, key: str) -> Any:
    return next(d for d in world.definitions if d.key == key)


def result_of(doc: Mapping[str, Any], world: TdegWorld, key: str) -> dict[str, Any]:
    """The result document of metric ``key`` inside ``doc`` (a copy of ``world.doc``)."""
    wanted = str(definition_of(world, key).id)
    found: dict[str, Any] = next(r for r in doc["results"] if r["metric"]["id"] == wanted)
    return found


def forge(world: TdegWorld, key: str, **changes: Any) -> dict[str, Any]:
    """A copy of the world's document whose result for ``key`` has ``changes`` applied and whose
    verdict is rebuilt, so the only fault is the result."""
    doc = world.copy()
    result_of(doc, world, key).update(changes)
    rebuild_verdict(world, doc)
    return doc


def with_results(
    world: TdegWorld, edits: Mapping[str, Mapping[str, Any]], doc: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Apply ``edits`` (metric key -> changes) to the results of ``doc`` (default: a copy of the
    world's) and rebuild the verdict."""
    body = world.copy() if doc is None else doc
    for key, changes in edits.items():
        result_of(body, world, key).update(changes)
    rebuild_verdict(world, body)
    return body


# ------------------------------------------------------------------------------- the transplant


def _stored_field(result: Mapping[str, Any], name: str) -> Any:
    if name == "status":
        return result["status"] == "not_applicable"  # the class: scored/error vs not applicable
    value = result.get(name)
    if name in ("missing_kinds", "declared_language_transcript_ids"):
        return list(value or [])
    return value


def first_differences(stored: Mapping[str, Any], other: Mapping[str, Any]) -> list[str]:
    """The compared fields (``COMPARED_FIELDS``) on which two result documents differ, status
    class first. ``error`` counts as scored for the status class (the class is not_applicable or
    not)."""
    return [
        name
        for name in COMPARED_FIELDS
        if _stored_field(stored, name) != _stored_field(other, name)
    ]


def transplant(
    target: TdegWorld, donor: TdegWorld, key: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The target's document with the result the donor stored for ``key`` in place of its own, under
    the target's result ID and metric reference, and the verdict rebuilt, together with the result
    document that was installed. ``None`` when the donor's result names declared-language
    transcripts and the target has no declaration to point at (the result would be rejected by an
    earlier rule, which is not what the caller is testing)."""
    doc = target.copy()
    mine = result_of(doc, target, key)
    theirs = copy.deepcopy(result_of(donor.copy(), donor, key))
    ids = theirs.get("declared_language_transcript_ids") or []
    if ids:
        transcripts = doc["transcripts"]
        if len(transcripts) != 1 or transcripts[0].get("declared_language") is None:
            return None
        theirs["declared_language_transcript_ids"] = [transcripts[0]["id"]]
    theirs["id"] = mine["id"]
    theirs["metric"] = mine["metric"]
    theirs["evidence_refs"] = mine.get("evidence_refs", [])
    mine.clear()
    mine.update(theirs)
    rebuild_verdict(target, doc)
    return doc, copy.deepcopy(theirs)


# --------------------------------------------------------------------------------- error results


def error_doc(
    stored: Mapping[str, Any], *, per_turn_undetermined: int | None = None
) -> dict[str, Any]:
    """The changes that turn a result into an ``error`` result of the same metric: the provenance
    of ``stored`` (``scorer``, ``judge``, ``language_source``, ``declared_language_transcript_ids``
    when it has them) is kept, value and passed go, the structured error is added. Never the
    identity (``id``, ``metric``). For a per-turn metric ``per_turn_undetermined`` is the number of
    turns (all undetermined, the judge record dropped)."""
    changes: dict[str, Any] = {
        k: copy.deepcopy(stored[k])
        for k in ("scorer", "judge", "language_source", "declared_language_transcript_ids")
        if k in stored
    }
    changes.update(
        {
            "status": "error",
            "value": None,
            "passed": None,
            "na_reason": None,
            "missing_kinds": [],
            "error": {"code": "scorer_failed", "message": "the scorer raised"},
            "per_turn": None,
        }
    )
    if per_turn_undetermined is not None:
        changes["error"] = {"code": "judge_undetermined", "message": "no turn was determined"}
        changes["judge"] = None
        changes["per_turn"] = {
            "share": None,
            "counts": {
                "passed": 0,
                "failed": 0,
                "not_applicable": 0,
                "undetermined": per_turn_undetermined,
            },
            "counted_turns": 0,
            "total_turns": per_turn_undetermined,
        }
    return changes


# -------------------------------------------------------------------------------- error text


def text_of_error(error: BaseException) -> str:
    """Every rendering of an error that code might log, joined (used to look for tags and leaks)."""
    parts = [str(error), repr(error)]
    if isinstance(error, ValidationError):
        parts.append(error.json(include_input=False))
    return "\n".join(parts)


def reject_text(model: Any, doc: Mapping[str, Any]) -> str:
    """Load ``doc`` and return the text of the error; fail if it loads."""
    with pytest.raises(ValidationError) as caught:
        model.model_validate(doc)
    return text_of_error(caught.value)


def load_failure(model: Any, doc: Mapping[str, Any]) -> str | None:
    """The text of the error ``model`` raises for ``doc``, or ``None`` when it loads."""
    try:
        model.model_validate(doc)
    except ValidationError as error:
        return text_of_error(error)
    return None


def check_failure(bundle: Any) -> str | None:
    """The text of the ``ValueError`` that ``check_run_bundle`` raises for a bundle, or ``None``."""
    from tracer_evalschema.bundle import check_run_bundle

    try:
        check_run_bundle(bundle)
    except ValueError as error:
        return str(error)
    return None


def mentioned_fields(text: str) -> set[str]:
    """The compared field names the message names, as whole words."""
    return {name for name in COMPARED_FIELDS if re.search(rf"\b{name}\b", text)}


def turn_texts() -> tuple[str, ...]:
    """The turn texts of the T-DEG worlds (the error must quote none of them)."""
    return TEXT_POOL


# ------------------------------------------------------------------------ roles and turn editing


def set_roles(
    doc: dict[str, Any], role: str, *, only: Callable[[dict[str, Any]], bool] | None = None
) -> None:
    for turn in doc["turns"]:
        if only is None or only(turn):
            turn["role"] = role


def agent_turn_ids(doc: Mapping[str, Any]) -> list[str]:
    return [t["id"] for t in doc["turns"] if t["role"] == "agent"]


def declared_language_doc(language: str = "hi-IN") -> dict[str, Any]:
    return {
        "language": language,
        "source": "user_declared",
        "declared_by": "cli",
        "declared_at": "2026-10-23T08:00:00.000000Z",
    }


# ------------------------------------------------------------------------- several transcripts


@dataclass
class TwoTranscripts:
    """A T1 bundle that holds two transcripts, ``first`` (the world's own, tagged ``hi-IN``) and
    ``second`` (added here, from our own STT of the same recording, 4 turns of unknown role).

    ``doc`` has ``transcript_ids = [first]`` on every result that needs the field, and the results
    are the ones the world stored (they read the first transcript only), so it is consistent.
    """

    world: TdegWorld
    doc: dict[str, Any]
    first: str
    second: str

    def copy(self) -> dict[str, Any]:
        return copy.deepcopy(self.doc)


def multi_world(
    *,
    second_tag: str | None = None,
    second_declared: str | None = None,
    column: str = "T1",
    origin: str = "human",
    language: str = "observed",
) -> TwoTranscripts:
    """Build the two-transcript bundle (see ``TwoTranscripts``). ``second_tag`` is the second
    transcript's ``detected_language`` (None: untagged); ``second_declared`` a user declaration for
    it (then it is an ``agent_export`` import: our own STT records a tag, it takes no declaration).
    """
    world = cached_world(column, origin=origin, language=language)
    doc = world.copy()
    first = doc["transcripts"][0]
    art_id = doc["artifacts"][0]["id"]
    second_id = new_id("tsc")
    second = transcript_doc(
        id=second_id,
        source="agent_export" if second_declared is not None else "our_stt",
        privacy=copy.deepcopy(first["privacy"]),
        timeline_origin={"kind": "artifact", "ref": art_id},
    )
    if second_tag is not None:
        second["detected_language"] = second_tag
    if second_declared is not None:
        second["declared_language"] = declared_language_doc(second_declared)
        second["timeline_origin"] = {"kind": "artifact", "ref": art_id}
    doc["transcripts"].append(second)
    for seq in range(4):
        doc["turns"].append(
            turn_doc(
                second_id,
                seq,
                role="unknown",
                source="our_stt",
                text=TEXT_POOL[seq],
                start_ms=seq * 3000,
                end_ms=seq * 3000 + 2400,
                timing_source="audio_alignment",
            )
        )
    needs = set(NEEDS_TRANSCRIPT_IDS)
    keys = {str(d.id): str(d.key) for d in world.definitions}
    for result in doc["results"]:
        if keys[result["metric"]["id"]] in needs:
            result["transcript_ids"] = [first["id"]]
    return TwoTranscripts(world, doc, first["id"], second_id)
