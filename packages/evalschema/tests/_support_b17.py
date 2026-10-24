# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the Phase 1 gate tests of batch 28 (T-B13, T-B16, N19). No tests here.

Contract: ``docs/phase1/api-surface-gate-fixes.md``. The bundles are the consistent T-DEG worlds of
``_support_b13`` / ``_support_b15`` with one edit, so a rejection is always for the rule under test.

Three groups of helper:

* per-turn summaries: ``counts_changes`` and ``error_counts_changes`` build a stored per-turn result
  whose share, coverage and ``passed`` agree with each other (the model checks that), so the only
  fault left is the one the test plants (the counts against the turns read);
* ``add_transcript``: a second transcript with a chosen number of agent and caller turns, and the
  ``transcript_ids`` that say which transcripts each result read;
* ``declared_by`` documents for N19.
"""

from __future__ import annotations

import copy
import decimal
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ._support import api
from ._support_b2 import new_id
from ._support_b7 import shared_ready
from ._support_b13 import TdegWorld
from ._support_b15 import (
    NEEDS_TRANSCRIPT_IDS,
    definition_of,
    result_of,
)

__all__ = [
    "COUNT_FIELDS",
    "NA_FIELD",
    "PER_TURN_KEYS",
    "TOTAL_FIELD",
    "add_transcript",
    "count_differences",
    "counts_changes",
    "counts_of",
    "declaration_of",
    "declared_export",
    "error_counts_changes",
    "fields_named",
    "na_counts_changes",
    "position_of",
    "read_ids",
    "set_declared_by",
]

PER_TURN_KEYS: tuple[str, ...] = ("turn_politeness_share", "turn_length_share")

#: The two per-turn counts a bundle can recompute from the turns it holds, spelled as the message
#: names them (contract section P-C3). The other two counts (``passed``, ``failed``,
#: ``undetermined``) come from the scorer or judge and are not recomputable; their sum is fixed by
#: these two (``total_turns`` equals the four counts, a model rule).
TOTAL_FIELD = "per_turn.total_turns"
NA_FIELD = "per_turn.counts.not_applicable"
COUNT_FIELDS: tuple[str, ...] = (TOTAL_FIELD, NA_FIELD)


def fields_named(text: str) -> set[str]:
    """The count fields (``COUNT_FIELDS``) a message names, matched as whole spellings."""
    return {
        name for name in COUNT_FIELDS if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", text)
    }


def position_of(doc: Mapping[str, Any], world: TdegWorld, key: str) -> int:
    """The 0-based position of the result of metric ``key`` in ``doc["results"]``."""
    wanted = str(definition_of(world, key).id)
    return [r["metric"]["id"] for r in doc["results"]].index(wanted)


def count_differences(mine: Mapping[str, Any], other: Mapping[str, Any]) -> list[str]:
    """The count fields (``COUNT_FIELDS``, total first) on which the per-turn summaries of two
    stored result documents differ; empty unless both hold a summary. Used by the transplant
    matrix: ``mine`` is the target's own (right) result, ``other`` the donor's."""
    if mine.get("per_turn") is None or other.get("per_turn") is None:
        return []
    a, b = counts_of(mine), counts_of(other)
    found = []
    if a[4] != b[4]:
        found.append(TOTAL_FIELD)
    if a[2] != b[2]:
        found.append(NA_FIELD)
    return found


def counts_of(result: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """``(passed, failed, not_applicable, undetermined, total_turns)`` of a stored per-turn
    result."""
    summary = result["per_turn"]
    counts = summary["counts"]
    return (
        counts["passed"],
        counts["failed"],
        counts["not_applicable"],
        counts["undetermined"],
        summary["total_turns"],
    )


def _summary(passed: int, failed: int, not_applicable: int, undetermined: int) -> dict[str, Any]:
    counts = {
        "passed": passed,
        "failed": failed,
        "not_applicable": not_applicable,
        "undetermined": undetermined,
    }
    share = api("metrics").per_turn_share(api("metrics").PerTurnCounts.model_validate(counts))
    return {
        "share": share,
        "counts": counts,
        "counted_turns": passed + failed,
        "total_turns": passed + failed + not_applicable + undetermined,
    }


def counts_changes(
    world: TdegWorld,
    key: str,
    *,
    passed: int,
    failed: int = 0,
    not_applicable: int = 0,
    undetermined: int = 0,
) -> dict[str, Any]:
    """The changes that give the stored (scored) per-turn result of ``key`` these counts, with the
    share, the coverage and ``passed`` (the pass rule of the definition applied to the share) kept
    consistent. ``passed + failed`` must be at least 1 (a scored result counts a turn)."""
    assert passed + failed >= 1
    summary = _summary(passed, failed, not_applicable, undetermined)
    rule = definition_of(world, key).pass_rule
    metrics = api("metrics")
    ok = metrics.rule_passes(rule, decimal.Decimal(str(summary["share"])))
    return {"status": "scored", "per_turn": summary, "passed": ok}


def na_counts_changes(stored: Mapping[str, Any], total: int) -> dict[str, Any]:
    """A stored not-applicable per-turn result (every turn not applicable) over ``total`` turns:
    everything of ``stored`` is kept except the counts, which become ``total`` not applicable."""
    assert stored["status"] == "not_applicable"
    return {"per_turn": _summary(0, 0, total, 0)}


def error_counts_changes(
    stored: Mapping[str, Any], *, not_applicable: int, undetermined: int
) -> dict[str, Any]:
    """The changes that turn ``stored`` into the ``judge_undetermined`` error of a per-turn metric
    with these counts (no counted turn, at least one undetermined turn; P-56)."""
    assert undetermined >= 1
    changes: dict[str, Any] = {
        k: copy.deepcopy(stored[k])
        for k in ("scorer", "language_source", "declared_language_transcript_ids")
        if k in stored
    }
    changes.update(
        {
            "status": "error",
            "value": None,
            "passed": None,
            "na_reason": None,
            "missing_kinds": [],
            "judge": None,
            "error": {"code": "judge_undetermined", "message": "no turn was determined"},
            "per_turn": _summary(0, 0, not_applicable, undetermined),
        }
    )
    return changes


def read_ids(doc: Mapping[str, Any], world: TdegWorld, *ids: str) -> None:
    """Set ``transcript_ids = ids`` on every result of ``doc`` whose metric needs the field (the
    eight of ``NEEDS_TRANSCRIPT_IDS``), in place."""
    needs = set(NEEDS_TRANSCRIPT_IDS)
    keys = {str(d.id): str(d.key) for d in world.definitions}
    for result in doc["results"]:
        if keys[result["metric"]["id"]] in needs:
            result["transcript_ids"] = list(ids)


def add_transcript(
    doc: dict[str, Any],
    world: TdegWorld,
    *,
    agent: int,
    caller: int = 0,
    tag: str | None = "keep",
    read: Iterable[str] | None = None,
) -> str:
    """Add a second transcript to ``doc`` (in place) and return its ID.

    It is a copy of the world's first transcript (so it is as valid as that one: same source,
    privacy and, unless ``tag`` is ``None`` or another tag, the same language tag), with ``agent``
    turns of role ``agent`` and then ``caller`` turns of role ``caller``, copied from the first
    transcript's turns of those roles (new IDs, ``seq`` from 0, new times, no exchange).
    ``read`` are the transcript IDs the results that need the field are set to read (default: the
    first transcript only, as ``multi_world`` does), set with ``read_ids``.
    """
    first = doc["transcripts"][0]
    second = copy.deepcopy(first)
    second["id"] = new_id("tsc")
    if tag is None:
        second.pop("detected_language", None)
    elif tag != "keep":
        second["detected_language"] = tag
    doc["transcripts"].append(second)
    donors = {
        role: [t for t in doc["turns"] if t["transcript_id"] == first["id"] and t["role"] == role]
        for role in ("agent", "caller")
    }
    seq = 0
    for role, count in (("agent", agent), ("caller", caller)):
        assert donors[role], f"the first transcript has no {role} turn to copy"
        for i in range(count):
            turn = copy.deepcopy(donors[role][i % len(donors[role])])
            start = seq * 3000
            turn.update(
                id=new_id("turn"),
                transcript_id=second["id"],
                seq=seq,
                start_ms=start,
                end_ms=start + 2400,
                exchange_index=None,
            )
            doc["turns"].append(turn)
            seq += 1
    read_ids(doc, world, *(read if read is not None else [first["id"]]))
    return str(second["id"])


def declared_export(
    purpose: str, *, declared_by: str | None, transcript_index: int = 0, run_index: int = 0
) -> dict[str, Any]:
    """An ``ExportBundle`` document of ``purpose`` around the ``declared=True`` run of
    ``_support_b7.world`` (an imported, untagged transcript with a user declaration, N14; every
    payload redacted so a shared or benchmark export is otherwise accepted), whose declaration is
    by ``declared_by``. ``None`` leaves the member out (the shape N19 asks for in a shared or
    benchmark export)."""
    doc = shared_ready(purpose, declared=True)
    if purpose == "benchmark":
        # a benchmark needs consent that is not unknown or not obtained: the world's own marker
        # is accepted by the base export rules (shared_ready builds the same for both purposes).
        pass
    set_declared_by(doc, declared_by, transcript_index=transcript_index, run_index=run_index)
    return doc


def declaration_of(doc: Mapping[str, Any], *, transcript_index: int = 0, run_index: int = 0) -> Any:
    """The ``declared_language`` document of a transcript of an export document."""
    return doc["run_bundles"][run_index]["transcripts"][transcript_index]["declared_language"]


def set_declared_by(
    doc: dict[str, Any], declared_by: str | None, *, transcript_index: int = 0, run_index: int = 0
) -> None:
    """Set (or, for ``None``, remove) ``declared_by`` of a transcript's declaration, in place."""
    declaration = declaration_of(doc, transcript_index=transcript_index, run_index=run_index)
    if declared_by is None:
        declaration.pop("declared_by", None)
    else:
        declaration["declared_by"] = declared_by


_UNUSED = (result_of,)
