# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the tier-degradation tests (P1-19, T-DEG). No tests here.

The same evaluator and the same rubric (13 metric definitions, one per combination of ``requires``
and language dependence; see ``METRICS``) are run over RunBundles that hold the evidence of three
capability tiers, as synthetic documents shaped like the real captures of Pipecat (stereo recording,
OpenTelemetry spans, observer turns) and of a Sarvam-style STT pass:

- ``T0`` (black box): a mono recording that mixes both speakers, and our STT transcript of it
  (``our_stt``, audio-aligned timing, roles ``unknown`` because nobody separated the speakers). No
  channel map, no tool calls, no spans, no agent usage. Nothing is assumed about who spoke.
- ``T0m`` (not a tier; the T0 evidence plus a declared stereo channel map, so the agent channel and
  the agent turns are known). It shows that availability follows evidence, not the tier label.
- ``T1`` (telemetry): a stereo recording with a declared channel map, a telemetry transcript
  (``agent_telemetry``), tool calls and results, stage spans, agent usage (``telemetry``).
- ``T2`` (Pipecat observer): the same inputs from an observer: an observer transcript, observer
  turn timing and interruptions, observer tool calls, exchanges from the observer, spans, usage.

The evaluator here is a reference harness: it decides applicability with the library's own
``results.applicability`` (the code under test), and scores with canned values (``CANNED``), the way
a recorded judge response would. The expected statuses are NOT derived from the library: they are
the hand-written ``EXPECTED`` table, which is also the matrix of
``docs/phase1/api-surface-tdeg.md`` (``test_deg_matrix_doc_matches_expected`` keeps both equal).

Documents are JSON-mode dicts; every piece is validated with the real models, so a defect in a
T-DEG test is never a defect of the fixture.
"""

from __future__ import annotations

import copy
import decimal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ._support import api
from ._support_b2 import judge_definition, make, meta, new_id, registered_class, seal
from ._support_b3 import artifact_doc as _artifact_doc
from ._support_b3 import audio_format as _audio_format
from ._support_b3 import (
    channel_map_doc,
    exchange_doc,
    judge_info_doc,
    privacy_human,
    privacy_synthetic,
    run_doc,
    scorer_info,
    snapshot_doc,
    span_doc,
    tool_call_doc,
    transcript_doc,
    turn_doc,
)
from ._support_b4 import entry_doc, make_catalog, usage_doc, usd
from ._support_b7 import export_doc
from .support.mappings.common import format_utc_instant

__all__ = [
    "AGENT_USAGE",
    "CANNED",
    "COLUMNS",
    "EXPECTED",
    "METRICS",
    "REQUIRED_KEYS",
    "TIERS",
    "WEIGHTS",
    "Case",
    "TdegWorld",
    "available_inputs_of",
    "bundle_of",
    "cached_world",
    "deg_world",
    "hindi",
    "load_bundle",
    "metric_keys",
    "rebuild_verdict",
    "result_by_key",
    "tier_columns",
]

TIERS = ("T0", "T1", "T2")
COLUMNS = ("T0", "T0m", "T1", "T2")  # the matrix columns; T0m is a variant of T0, not a tier

RUN_STARTED = "2026-10-22T09:59:00.000000Z"


def hindi(*code_points: int) -> str:
    """Devanagari text from code points (test files stay ASCII)."""
    return "".join(chr(cp) for cp in code_points)


AGENT_TEXTS = (
    hindi(0x0928, 0x092E, 0x0938, 0x094D, 0x0924, 0x0947),  # namaste
    hindi(0x0915, 0x0943, 0x092A, 0x092F, 0x093E),  # kripya
    hindi(0x0930, 0x093F, 0x092B, 0x0902, 0x0921),  # refund
    hindi(0x0927, 0x0928, 0x094D, 0x092F, 0x0935, 0x093E, 0x0926),  # dhanyavaad
)
CALLER_TEXTS = (
    hindi(0x0939, 0x093E, 0x0901),  # haan
    hindi(0x0920, 0x0940, 0x0915, 0x0020, 0x0939, 0x0948),  # theek hai
    hindi(0x0930, 0x093F, 0x092B, 0x0902, 0x0921),  # refund
    hindi(0x0936, 0x0941, 0x0915, 0x094D, 0x0930, 0x093F, 0x092F, 0x093E),  # shukriya
)


# ---------------------------------------------------------------------------- the metric set


@dataclass(frozen=True)
class Metric:
    """One metric definition of the T-DEG rubric and its place in the rubric."""

    key: str
    requires: tuple[str, ...]
    languages: str | tuple[str, ...]  # "language_independent" or the covered tags
    script_sensitive: bool
    aggregation: str  # per_conversation | per_turn_share
    kind: str
    required: bool
    weight: str | None
    note: str
    doc: dict[str, Any] = field(default_factory=dict)


COVERED = ("en", "hi", "mr")
INDEPENDENT = "language_independent"
_RANGE = {"kind": "range", "inclusive": True}


def _definition(
    key: str,
    kind: str,
    value_type: str,
    requires: Sequence[str],
    languages: Any,
    *,
    script_sensitive: bool = False,
    unit: str | None = None,
    pass_rule: Mapping[str, Any] | None,
    aggregation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "key": key,
        "kind": kind,
        "value_type": value_type,
        "requires": list(requires),
        "languages": languages if isinstance(languages, str) else list(languages),
        "script_sensitive": script_sensitive,
        "aggregation": dict(aggregation or {"kind": "per_conversation"}),
        "pass_rule": None if pass_rule is None else dict(pass_rule),
    }
    if unit is not None:
        doc["unit"] = unit
    if kind == "llm_judge":
        base = judge_definition()
        doc["prompt_template"] = base["prompt_template"]
        doc["prompt_variables"] = base["prompt_variables"]
        doc["judge_model_ref"] = base["judge_model_ref"]
    else:
        doc["implementation_ref"] = f"builtin:{key}"
    return doc


def _metrics() -> tuple[Metric, ...]:
    equals_true = {"kind": "equals_true"}
    spec: list[tuple[Metric, dict[str, Any]]] = []

    def add(
        key: str,
        kind: str,
        value_type: str,
        requires: Sequence[str],
        languages: Any,
        *,
        required: bool,
        weight: str | None,
        note: str,
        script_sensitive: bool = False,
        unit: str | None = None,
        pass_rule: Mapping[str, Any] | None,
        aggregation: Mapping[str, Any] | None = None,
    ) -> None:
        doc = _definition(
            key,
            kind,
            value_type,
            requires,
            languages,
            script_sensitive=script_sensitive,
            unit=unit,
            pass_rule=pass_rule,
            aggregation=aggregation,
        )
        metric = Metric(
            key=key,
            requires=tuple(requires),
            languages=languages if isinstance(languages, str) else tuple(languages),
            script_sensitive=script_sensitive,
            aggregation=(aggregation or {"kind": "per_conversation"})["kind"],
            kind=kind,
            required=required,
            weight=weight,
            note=note,
        )
        spec.append((metric, doc))

    add(
        "goal_completion",
        "llm_judge",
        "boolean",
        ["transcript"],
        COVERED,
        required=True,
        weight="4",
        note="expected-outcome judge over the transcript",
        pass_rule=equals_true,
    )
    add(
        "p95_latency",
        "latency",
        "numeric",
        ["turn_timing"],
        INDEPENDENT,
        required=True,
        weight="5",
        note="language independent",
        unit="ms",
        pass_rule={**_RANGE, "max": 800},
    )
    add(
        "tool_call_success",
        "deterministic",
        "boolean",
        ["tool_calls", "tool_results"],
        INDEPENDENT,
        required=True,
        weight="3",
        note="needs telemetry or an observer",
        pass_rule=equals_true,
    )
    add(
        "stage_ttfb_p95",
        "latency",
        "numeric",
        ["spans"],
        INDEPENDENT,
        required=False,
        weight="2",
        note="needs stage spans",
        unit="ms",
        pass_rule={**_RANGE, "max": 500},
    )
    add(
        "silence_ratio",
        "audio",
        "numeric",
        ["audio"],
        INDEPENDENT,
        required=False,
        weight=None,
        note="audio only",
        unit="ratio",
        pass_rule={**_RANGE, "max": 0.3},
    )
    add(
        "agent_audio_clarity",
        "audio",
        "numeric",
        ["agent_audio"],
        COVERED,
        required=False,
        weight="1",
        note="agent channel; language from the transcript tag",
        unit="ratio",
        pass_rule={**_RANGE, "min": 0.8},
    )
    add(
        "agent_politeness",
        "llm_judge",
        "boolean",
        ["agent_transcript"],
        COVERED,
        required=True,
        weight="2",
        note="agent turns only",
        pass_rule=equals_true,
    )
    add(
        "agent_wer",
        "audio",
        "numeric",
        ["agent_audio", "reference_text"],
        COVERED,
        required=False,
        weight=None,
        note="script sensitive; no reference text at any tier",
        script_sensitive=True,
        unit="wer",
        pass_rule={**_RANGE, "max": 0.2},
    )
    add(
        "turn_politeness_share",
        "llm_judge",
        "boolean",
        ["agent_transcript"],
        COVERED,
        required=False,
        weight="1",
        note="per turn, language dependent",
        pass_rule={**_RANGE, "min": 0.8},
        aggregation={"kind": "per_turn_share", "turn_rule": equals_true},
    )
    add(
        "turn_length_share",
        "deterministic",
        "boolean",
        ["agent_transcript"],
        INDEPENDENT,
        required=False,
        weight=None,
        note="per turn, language independent",
        pass_rule={**_RANGE, "min": 0.9},
        aggregation={"kind": "per_turn_share", "turn_rule": equals_true},
    )
    add(
        "agent_input_tokens",
        "deterministic",
        "numeric",
        ["usage"],
        INDEPENDENT,
        required=False,
        weight=None,
        note="cost dependent; informational",
        unit="count",
        pass_rule=None,
    )
    add(
        "agent_turn_count",
        "deterministic",
        "numeric",
        ["agent_transcript"],
        INDEPENDENT,
        required=False,
        weight=None,
        note="informational",
        unit="count",
        pass_rule=None,
    )
    add(
        "script_consistency",
        "deterministic",
        "boolean",
        ["transcript"],
        COVERED,
        required=False,
        weight=None,
        note="script sensitive, transcript only",
        script_sensitive=True,
        pass_rule=equals_true,
    )
    out = []
    for metric, doc in spec:
        out.append(Metric(**{**metric.__dict__, "doc": doc}))
    return tuple(out)


METRICS: tuple[Metric, ...] = _metrics()
REQUIRED_KEYS = tuple(m.key for m in METRICS if m.required)
WEIGHTS = {m.key: m.weight for m in METRICS if m.weight is not None}


def metric_keys() -> list[str]:
    return [m.key for m in METRICS]


# --------------------------------------------------------------------- canned scoring values

# key -> (value, passed) of a scored per-conversation result. The judge answers and the scorer
# values are recorded constants, the same at every tier (the evaluator is the same); only
# ``agent_input_tokens`` follows the agent usage of the bundle.
CANNED: dict[str, tuple[Any, bool | None]] = {
    "goal_completion": (True, True),
    "p95_latency": (420, True),
    "tool_call_success": (True, True),
    "stage_ttfb_p95": (480, True),
    "silence_ratio": (0.12, True),
    "agent_audio_clarity": (0.74, False),
    "agent_politeness": (True, True),
    "agent_wer": (0.08, True),  # never reached: no reference text at any tier
    "agent_turn_count": (4, None),
    "script_consistency": (True, True),
}
# per-turn outcomes of the agent turns, in order: yes/no for the judge, True/False for the scorer
CANNED_TURNS: dict[str, tuple[bool, ...]] = {
    "turn_politeness_share": (True, True, True, False),
    "turn_length_share": (True, True, True, True),
}

# Agent usage of the bundle at each tier (input tokens, output tokens), None where there is none.
AGENT_USAGE: dict[str, tuple[int, int] | None] = {
    "T0": None,
    "T0m": None,
    "T1": (500, 120),
    "T2": (640, 150),
}


# ----------------------------------------------------------------------- the expected matrix


@dataclass(frozen=True)
class Case:
    """The expected outcome of one metric at one column of the matrix."""

    status: str  # scored | not_applicable
    reason: str | None = None  # the na_reason
    kinds: tuple[str, ...] = ()  # missing_kinds
    value: Any = None  # the scored value (per-conversation metrics)
    passed: bool | None = None
    share: str | None = None  # the scored share (per-turn metrics)
    source: str | None = None  # language_source

    def cell(self) -> str:
        """The text of the cell in docs/phase1/api-surface-tdeg.md."""
        if self.status == "scored":
            if self.share is not None:
                return f"scored share {self.share} passed {_flag(self.passed)}"
            shown = "-" if self.value is None else _show(self.value)
            return f"scored {shown} passed {_flag(self.passed)}"
        if self.reason == "missing_input":
            return f"NA missing_input [{', '.join(self.kinds)}]"
        return f"NA {self.reason}"


def _show(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _flag(passed: bool | None) -> str:
    return "none" if passed is None else str(passed).lower()


def _s(value: Any, passed: bool | None, source: str | None = None) -> Case:
    return Case("scored", value=value, passed=passed, source=source)


def _share(share: str, passed: bool, source: str | None) -> Case:
    return Case("scored", share=share, passed=passed, source=source)


def _miss(*kinds: str) -> Case:
    return Case("not_applicable", "missing_input", tuple(kinds))


_ACU = Case("not_applicable", "agent_channel_unknown")

# Written by hand from ADR 0004 (Decisions 3 to 6, 5a), ADR 0010 (3, 3a) and the tier definitions
# above. A column is (T0, T0m, T1, T2).
EXPECTED: dict[str, dict[str, Case]] = {
    "goal_completion": dict.fromkeys(COLUMNS, _s(True, True, "observed")),
    "p95_latency": dict.fromkeys(COLUMNS, _s(420, True)),
    "tool_call_success": {
        "T0": _miss("tool_calls", "tool_results"),
        "T0m": _miss("tool_calls", "tool_results"),
        "T1": _s(True, True),
        "T2": _s(True, True),
    },
    "stage_ttfb_p95": {
        "T0": _miss("spans"),
        "T0m": _miss("spans"),
        "T1": _s(480, True),
        "T2": _s(480, True),
    },
    "silence_ratio": dict.fromkeys(COLUMNS, _s(0.12, True)),
    "agent_audio_clarity": {
        "T0": _ACU,
        "T0m": _s(0.74, False, "observed"),
        "T1": _s(0.74, False, "observed"),
        "T2": _s(0.74, False, "observed"),
    },
    "agent_politeness": {
        "T0": _miss("agent_transcript"),
        "T0m": _s(True, True, "observed"),
        "T1": _s(True, True, "observed"),
        "T2": _s(True, True, "observed"),
    },
    "agent_wer": {
        "T0": _miss("agent_audio", "reference_text"),
        "T0m": _miss("reference_text"),
        "T1": _miss("reference_text"),
        "T2": _miss("reference_text"),
    },
    "turn_politeness_share": {
        "T0": _miss("agent_transcript"),
        "T0m": _share("0.75", False, "observed"),
        "T1": _share("0.75", False, "observed"),
        "T2": _share("0.75", False, "observed"),
    },
    "turn_length_share": {
        "T0": _miss("agent_transcript"),
        "T0m": _share("1", True, None),
        "T1": _share("1", True, None),
        "T2": _share("1", True, None),
    },
    "agent_input_tokens": {
        "T0": _miss("usage"),
        "T0m": _miss("usage"),
        "T1": _s(500, None),
        "T2": _s(640, None),
    },
    "agent_turn_count": {
        "T0": _miss("agent_transcript"),
        "T0m": _s(4, None),
        "T1": _s(4, None),
        "T2": _s(4, None),
    },
    "script_consistency": dict.fromkeys(COLUMNS, _s(True, True, "observed")),
}


def tier_columns() -> tuple[str, ...]:
    return COLUMNS


def available_inputs_of(column: str) -> list[str]:
    """What the evidence of the column holds, written out (not derived from the tier label)."""
    base = ["audio", "transcript", "turn_timing"]
    if column == "T0":
        return base
    inputs = [*base[:1], "agent_audio", "transcript", "agent_transcript", "turn_timing"]
    if column == "T0m":
        return inputs
    return [*inputs, "tool_calls", "tool_results", "spans", "usage"]


# ----------------------------------------------------------------------------------- the world


@dataclass
class TdegWorld:
    """One RunBundle document and the validated pieces the evaluator and the tests read."""

    column: str
    doc: dict[str, Any]
    run: Any
    definitions: list[Any]
    transcripts: list[Any]
    turns: list[Any]
    catalog: Any
    parts: dict[str, Any]
    agent_usage: list[Any] = field(default_factory=list)

    def copy(self) -> dict[str, Any]:
        return copy.deepcopy(self.doc)

    @property
    def results(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = self.doc["results"]
        return found


def load_bundle(doc: Mapping[str, Any]) -> Any:
    return api("bundle").RunBundle.model_validate(doc)


def result_by_key(world: TdegWorld) -> dict[str, dict[str, Any]]:
    """The result documents of a world by metric key (the snapshot's definitions name the keys)."""
    by_metric = {r["metric"]["id"]: r for r in world.results}
    return {str(d.key): by_metric[str(d.id)] for d in world.definitions}


def _utc(moment: datetime) -> str:
    return format_utc_instant(moment.astimezone(UTC))


def _dump(model: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = model.model_dump(mode="json", by_alias=True)
    return dumped


def _parts(required: Sequence[str]) -> dict[str, Any]:
    """Persona, definitions, bindings, evaluator and rubric of the T-DEG configuration."""
    persona = make("Persona")
    evaluator_id = new_id("evl")
    definitions = [make("MetricDefinition", **copy.deepcopy(m.doc)) for m in METRICS]
    bindings = [
        make(
            "MetricBinding",
            evaluator_id=evaluator_id,
            metric={"id": str(d.id), "version": d.version},
        )
        for d in definitions
    ]
    evaluator = make(
        "Evaluator",
        id=str(evaluator_id),
        persona={"id": str(persona.id), "version": persona.version},
        metric_bindings=[{"id": str(b.id), "version": b.version} for b in bindings],
    )
    items = []
    for metric, binding in zip(METRICS, bindings, strict=True):
        item: dict[str, Any] = {
            "binding": {"id": str(binding.id), "version": binding.version},
            "required": metric.key in required,
        }
        if metric.weight is not None:
            item["weight"] = metric.weight
        items.append(item)
    rubric = make("Rubric", items=items)
    return {
        "persona": persona,
        "evaluator": evaluator,
        "definitions": definitions,
        "bindings": bindings,
        "rubric": rubric,
        "profile": None,
    }


def _catalog() -> Any:
    return make_catalog(
        [
            entry_doc(entry_key="gpt41-in", unit="input_tokens", price=usd("0.15")),
            entry_doc(entry_key="gpt41-out", unit="output_tokens", price=usd("0.6")),
        ]
    )


def _tag_for(language: str) -> str | None:
    return {
        "observed": "hi-IN",
        "untagged": None,
        "declared": None,
        "unsupported_language": "ta-IN",
        "romanized": "hi-Latn",
        "mixed": "hi-IN",
        "untagged_foreign": None,
    }[language]


def deg_world(
    column: str,
    *,
    origin: str = "simulated",
    language: str = "observed",
    required: Sequence[str] = REQUIRED_KEYS,
    spans: bool = True,
    agent_cost: str = "default",
    with_duplicate_eval_cost: bool = True,
    observed_tier: str | None = None,
    stt: bool = True,
) -> TdegWorld:
    """The RunBundle document of one column of the matrix, evaluated by the reference harness.

    ``origin`` is ``simulated`` or ``human`` (the caller). ``language`` is ``observed`` (the
    transcript carries ``hi-IN``), ``untagged``, ``declared`` (untagged import with a user
    declaration; an imported transcript only, so not at T0), ``unsupported_language`` (``ta-IN``,
    outside the metrics' lists), ``romanized`` (``hi-Latn``), ``mixed`` (``hi-IN`` with one agent
    turn tagged ``ta-IN``) or ``untagged_foreign`` (no transcript tag, one agent turn tagged
    ``ta-IN``, so one turn is foreign and the others have no language).

    ``required`` names the rubric's required metrics. ``spans`` False leaves the spans out of a T1
    or T2 bundle (the evidence decides, not the label). ``agent_cost`` is ``default`` or, for T0
    only, ``reported_inr`` (a black box whose API reports usage and a cost in a currency we do not
    support). ``observed_tier`` overrides the stored tier (default: the column's tier). ``stt``
    False (T0 only) is a recording whose STT pass produced nothing: no transcript, turn or
    exchange, and ``audio`` is the only input.
    """
    assert column in COLUMNS, column
    tier = "T0" if column in ("T0", "T0m") else column
    mono = column == "T0"
    telemetry = tier in ("T1", "T2")
    parts = _parts(required)
    catalog = _catalog()
    snap = snapshot_doc(
        parts,
        pricing_catalog={
            "id": str(catalog.id),
            "version": catalog.version,
            "content_digest": catalog.content_digest.model_dump(mode="json", exclude_none=True),
        },
    )

    # ------------------------------------------------------------------ roles and the marker
    roles_known = not mono
    caller_origin = "human" if origin == "human" else "simulated"
    human_subject = origin == "human" or not roles_known
    mark = privacy_human() if human_subject else privacy_synthetic()
    art_id, tsc_id = new_id("art"), new_id("tsc")

    agent_usage_docs: list[dict[str, Any]] = []
    run_id = new_id("run")
    amounts = AGENT_USAGE[column]
    if amounts is not None:
        agent_usage_docs.append(
            usage_doc(
                run_id=run_id,
                domain="agent_under_test",
                stage="llm",
                source="telemetry",
                quantities=[
                    {"unit": "input_tokens", "count": amounts[0]},
                    {"unit": "output_tokens", "count": amounts[1]},
                ],
            )
        )
    if column == "T2":
        agent_usage_docs.append(
            usage_doc(
                run_id=run_id,
                domain="agent_under_test",
                stage="tts",
                source="telemetry",
                quantities=[{"unit": "input_characters", "count": 420}],
            )
        )
    if agent_cost == "reported_inr":
        assert column == "T0", "the reported-cost black box is a T0 case"
        agent_usage_docs.append(
            usage_doc(
                run_id=run_id,
                domain="agent_under_test",
                stage="realtime",
                provider="agentco",
                model=None,
                source="provider_reported",
                quantities=[{"unit": "input_tokens", "count": 300}],
            )
        )

    assert stt or column == "T0", "a missing STT pass is a T0 case"
    inputs = available_inputs_of(column) if stt else ["audio"]
    if not spans and "spans" in inputs:
        inputs.remove("spans")
    if agent_usage_docs and "usage" not in inputs:
        inputs.append("usage")
    channel_maps = [] if mono else [channel_map_doc(art_id)]
    run = run_doc(
        id=run_id,
        mode="recorded" if origin == "human" else "simulated",
        caller_origin=caller_origin,
        config_snapshot_id=snap["id"],
        observed_tier=observed_tier or tier,
        available_inputs=inputs,
        channel_maps=channel_maps,
        started_at=RUN_STARTED,
    )

    artifact = _artifact_doc(
        id=art_id,
        privacy=mark,
        audio_format=_audio_format(channels=1 if mono else 2),
    )

    source = {"T0": "our_stt", "T1": "agent_telemetry", "T2": "observer"}[tier]
    origin_kind = {"T0": "artifact", "T1": "telemetry", "T2": "observer"}[tier]
    timeline: dict[str, Any] = {"kind": origin_kind}
    if origin_kind == "artifact":
        timeline["ref"] = art_id
    timing_source = {"T0": "audio_alignment", "T1": "telemetry", "T2": "observer"}[tier]
    tag = _tag_for(language)
    transcript = transcript_doc(
        id=tsc_id,
        source=source,
        privacy=mark,
        timeline_origin=timeline,
    )
    if tag is not None:
        transcript["detected_language"] = tag
    if language == "declared":
        assert source != "our_stt", "our own STT records its tag; a declaration is for imports"
        transcript["declared_language"] = {
            "language": "mr-IN",
            "source": "user_declared",
            "declared_by": "cli",
            "declared_at": "2026-10-23T08:00:00.000000Z",
        }

    exchange_source = {"T0": "inferred_from_turns", "T1": "telemetry", "T2": "observer"}[tier]
    exchanges = [exchange_doc(i, source=exchange_source) for i in range(4)]
    turns: list[dict[str, Any]] = []
    for seq in range(8):
        is_agent = seq % 2 == 1
        role = ("agent" if is_agent else "caller") if roles_known else "unknown"
        stage_source = (
            "our_stt" if tier == "T0" else ("agent_llm_output" if is_agent else "agent_stt")
        )
        turn = turn_doc(
            tsc_id,
            seq,
            role=role,
            source=stage_source,
            text=(AGENT_TEXTS if is_agent else CALLER_TEXTS)[seq // 2],
            start_ms=seq * 3000,
            end_ms=seq * 3000 + 2400,
            timing_source=timing_source,
            exchange_index=seq // 2,
        )
        if telemetry:
            turn["interrupted"] = seq == 5
        if language in ("mixed", "untagged_foreign") and seq == 3:
            turn["language"] = "ta-IN"
        turns.append(turn)

    calls: list[dict[str, Any]] = []
    span_docs: list[dict[str, Any]] = []
    if telemetry:
        call_source = "agent_telemetry" if tier == "T1" else "observer"
        calls = [
            tool_call_doc(seq=0, source=call_source, external_call_id="call_a1"),
            tool_call_doc(
                seq=1, source=call_source, name="issue_refund", external_call_id="call_a2"
            ),
        ]
        if spans:
            span_docs = [
                span_doc(
                    stage=stage,
                    span_ref={"system": "otel", "kind": "span_id", "value": f"00f067aa0ba902b{i}"},
                )
                for i, stage in enumerate(("stt", "llm", "tts"))
            ]

    # ------------------------------------------------------------------ evaluation (ours)
    judge_usage = usage_doc(run_id=run_id)
    eval_usage = [judge_usage]
    if origin != "human":
        eval_usage.append(
            usage_doc(
                run_id=run_id,
                stage="sim_caller_llm",
                quantities=[{"unit": "input_tokens", "count": 1_000_000}],
            )
        )
    usage_records = [*eval_usage, *agent_usage_docs]

    # --------------------------------------------------------------------- validated pieces
    if not stt:
        transcript, turns, exchanges = {}, [], []
    run_model = registered_class("Run").model_validate(run)
    transcript_models = [registered_class("Transcript").model_validate(transcript)] if stt else []
    turn_model = registered_class("Turn")
    turn_models = [turn_model.model_validate(t) for t in turns]
    usage_model = registered_class("UsageRecord")
    usage_models = [usage_model.model_validate(u) for u in usage_records]
    agent_models = [u for u in usage_models if u.domain == "agent_under_test"]
    agent_input_tokens = sum(
        int(q.count) for u in agent_models for q in u.quantities if q.unit == "input_tokens"
    )

    results = _evaluate(
        parts,
        run_model,
        transcript_models,
        turn_models,
        agent_input_tokens=agent_input_tokens,
    )
    verdict = _verdict(parts, results)

    cost_records, summaries = _costs(
        catalog, usage_models, run_model, with_duplicate_eval_cost, agent_cost
    )

    doc: dict[str, Any] = {
        **meta("rbnd"),
        "run": run,
        "snapshot": snap,
        "pricing_catalogs": [_dump(catalog)],
        "test_profiles": [],
        "artifacts": [artifact],
        "transcripts": [transcript] if stt else [],
        "turns": turns,
        "exchanges": exchanges,
        "tool_calls": calls,
        "spans": span_docs,
        "results": results,
        "verdict": verdict,
        "usage_records": usage_records,
        "cost_records": cost_records,
        "cost_summaries": summaries,
        "withheld": [],
    }
    return TdegWorld(
        column=column,
        doc=doc,
        run=run_model,
        definitions=parts["definitions"],
        transcripts=transcript_models,
        turns=turn_models,
        catalog=catalog,
        parts=parts,
        agent_usage=agent_models,
    )


_CACHE: dict[tuple[Any, ...], TdegWorld] = {}


def cached_world(column: str, **kwargs: Any) -> TdegWorld:
    """``deg_world`` built once per distinct arguments. The result is shared: a test that edits a
    document copies it first (``TdegWorld.copy``)."""
    key = (column, *sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in kwargs.items()))
    if key not in _CACHE:
        _CACHE[key] = deg_world(column, **kwargs)
    return _CACHE[key]


_BUNDLES: dict[int, Any] = {}


def bundle_of(world: TdegWorld) -> Any:
    """The validated ``RunBundle`` of a cached world (validated once)."""
    key = id(world)
    if key not in _BUNDLES:
        _BUNDLES[key] = load_bundle(world.doc)
    return _BUNDLES[key]


def rebuild_verdict(world: TdegWorld, doc: dict[str, Any]) -> None:
    """Replace ``doc["verdict"]`` by the verdict of ``doc["results"]`` (same ID), so that a test
    that edits a result stays consistent with the verdict and is rejected, if at all, for the rule
    it is about."""
    old_id = doc["verdict"]["id"]
    doc["verdict"] = {**_verdict(world.parts, doc["results"]), "id": old_id}


# --------------------------------------------------------------------------- the evaluator


def _read_turns(definition: Any, turns: Sequence[Any]) -> list[Any]:
    """The turns a metric reads (ADR 0004 Decision 6d): the role the input kind names."""
    requires = list(definition.requires)
    if "agent_transcript" in requires:
        return [t for t in turns if t.role == "agent"]
    if "transcript" in requires:
        return list(turns)
    return []


def _metric_ref(definition: Any) -> dict[str, Any]:
    return {
        "id": str(definition.id),
        "version": definition.version,
        "content_digest": definition.content_digest.model_dump(mode="json", exclude_none=True),
    }


def _provenance(definition: Any, status: str) -> dict[str, Any]:
    """The scorer or judge record of a result that ran."""
    if status == "not_applicable":
        return {}
    if definition.kind == "llm_judge":
        return {"judge": judge_info_doc()}
    return {"scorer": scorer_info(implementation_ref=definition.implementation_ref)}


def _evaluate(
    parts: Mapping[str, Any],
    run: Any,
    transcripts: Sequence[Any],
    turns: Sequence[Any],
    *,
    agent_input_tokens: int,
) -> list[dict[str, Any]]:
    """The reference evaluator: the same code path at every tier. Applicability comes from
    ``results.applicability`` (the code under test); scored values are recorded constants."""
    results_api = api("results")
    metrics_api = api("metrics")
    out: list[dict[str, Any]] = []
    for definition in parts["definitions"]:
        key = str(definition.key)
        read = _read_turns(definition, turns)
        base: dict[str, Any] = {
            **meta("res"),
            "metric": _metric_ref(definition),
            "value": None,
            "passed": None,
        }
        per_turn = definition.aggregation.kind == "per_turn_share"
        if per_turn:
            doc = _evaluate_per_turn(definition, run, transcripts, read, results_api, metrics_api)
        else:
            app = results_api.applicability(definition, run, turns=read, transcripts=transcripts)
            doc = _outcome(
                definition,
                app,
                value=_value_of(key, agent_input_tokens),
                passed_rule=definition.pass_rule,
                metrics_api=metrics_api,
            )
        out.append({**base, **doc})
    return out


def _value_of(key: str, agent_input_tokens: int) -> Any:
    if key == "agent_input_tokens":
        return agent_input_tokens
    return CANNED[key][0]


def _outcome(
    definition: Any, app: Any, *, value: Any, passed_rule: Any, metrics_api: Any
) -> dict[str, Any]:
    if app.na_reason is not None:
        return {
            "status": "not_applicable",
            "na_reason": app.na_reason,
            "missing_kinds": list(app.missing_kinds),
            "language_source": app.language_source,
            "declared_language_transcript_ids": [
                str(i) for i in app.declared_language_transcript_ids
            ],
        }
    passed = None if passed_rule is None else metrics_api.rule_passes(passed_rule, value)
    return {
        "status": "scored",
        "value": value,
        "passed": passed,
        "language_source": app.language_source,
        "declared_language_transcript_ids": [str(i) for i in app.declared_language_transcript_ids],
        **_provenance(definition, "scored"),
    }


def _evaluate_per_turn(
    definition: Any,
    run: Any,
    transcripts: Sequence[Any],
    read: Sequence[Any],
    results_api: Any,
    metrics_api: Any,
) -> dict[str, Any]:
    """A ``per_turn_share`` metric: the language steps run on each turn on its own (Decision 6e)."""
    key = str(definition.key)
    apps = [
        results_api.applicability(definition, run, turns=[turn], transcripts=transcripts)
        for turn in read
    ] or [results_api.applicability(definition, run, turns=[], transcripts=transcripts)]
    run_level = [a for a in apps if a.na_reason in ("missing_input", "agent_channel_unknown")]
    if run_level:
        first = run_level[0]
        return {
            "status": "not_applicable",
            "na_reason": first.na_reason,
            "missing_kinds": list(first.missing_kinds),
            "language_source": None,
            "declared_language_transcript_ids": [],
        }
    outcomes = CANNED_TURNS[key]
    passed = failed = not_applicable = 0
    verdicts: list[dict[str, Any]] = []
    reasons: list[str] = []
    for app, turn, ok in zip(apps, read, outcomes, strict=True):
        if app.na_reason is not None:
            not_applicable += 1
            reasons.append(app.na_reason)
            continue
        if ok:
            passed += 1
        else:
            failed += 1
        verdicts.append({"turn_seq": turn.seq, "verdict": "yes" if ok else "no"})
    counts = {
        "passed": passed,
        "failed": failed,
        "not_applicable": not_applicable,
        "undetermined": 0,
    }
    summary: dict[str, Any] = {
        "counts": counts,
        "counted_turns": passed + failed,
        "total_turns": passed + failed + not_applicable,
    }
    sources = [a.language_source for a in apps if a.language_source is not None]
    declared_ids = sorted({str(i) for a in apps for i in a.declared_language_transcript_ids})
    language_source = "declared" if declared_ids else ("observed" if sources else None)
    shared = {"language_source": language_source, "declared_language_transcript_ids": declared_ids}
    if passed + failed == 0:
        summary["share"] = None
        return {
            "status": "not_applicable",
            "na_reason": results_api.strongest_na_reason(reasons),
            "missing_kinds": [],
            "per_turn": summary,
            **shared,
        }
    share = metrics_api.per_turn_share(registered_class("PerTurnCounts").model_validate(counts))
    summary["share"] = share
    rule = definition.pass_rule
    passes = metrics_api.rule_passes(rule, decimal.Decimal(share))
    provenance = (
        {"judge": judge_info_doc(per_turn=verdicts)}
        if definition.kind == "llm_judge"
        else {"scorer": scorer_info(implementation_ref=definition.implementation_ref)}
    )
    return {"status": "scored", "per_turn": summary, "passed": passes, **shared, **provenance}


# ------------------------------------------------------------------------ verdict and costs


def _verdict(parts: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    verdict_api = api("verdict")
    result_model = registered_class("MetricResult")
    by_metric = {r["metric"]["id"]: result_model.model_validate(r) for r in results}
    bindings = {str(b.id): b for b in parts["bindings"]}
    checks: list[dict[str, Any]] = []
    weighted: list[tuple[Any, str, str]] = []
    for item in parts["rubric"].items:
        binding = bindings[str(item.binding.id)]
        result = by_metric[str(binding.metric.id)]
        if item.required:
            checks.append(
                {
                    "binding_id": str(binding.id),
                    "metric_ref": {"id": str(binding.metric.id), "version": binding.metric.version},
                    "outcome": verdict_api.outcome_of_result(result),
                    "result_id": str(result.id),
                }
            )
        if item.weight is not None:
            weighted.append((binding.id, item.weight, verdict_api.outcome_of_result(result)))
    tally = {
        name: sum(1 for c in checks if c["outcome"] == outcome)
        for name, outcome in (
            ("passed", "passed"),
            ("failed", "failed"),
            ("not_applicable", "not_applicable"),
            ("errored", "errored"),
        )
    }
    status = (
        "fail"
        if tally["failed"]
        else ("pass" if tally["passed"] == len(checks) else "inconclusive")
    )
    score = verdict_api.compute_weighted_score(weighted)
    return {
        **meta("vrd"),
        "required_total": len(checks),
        **tally,
        "status": status,
        "checks": checks,
        "weighted_score": score.score,
        "weight_coverage": score.coverage,
        "weighted_excluded": [
            {"binding_id": str(e.binding_id), "outcome": e.outcome} for e in score.excluded
        ],
    }


def _costs(
    catalog: Any,
    usages: Sequence[Any],
    run: Any,
    with_duplicate: bool,
    agent_cost: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cost_api = api("cost")
    ids_api = api("ids")
    started = run.started_at
    created = datetime(2026, 10, 23, tzinfo=UTC)

    def compute(items: Sequence[Any], domain: str) -> Any:
        return cost_api.compute_cost(
            items,
            catalog,
            domain=domain,
            record_id=ids_api.CostRecordId.new(),
            created_at=created,
            run_started_at=started,
            context=decimal.Context(prec=120, rounding=decimal.ROUND_HALF_EVEN),
        )

    evaluation = [u for u in usages if u.domain == "evaluation"]
    agent = [u for u in usages if u.domain == "agent_under_test"]
    records: dict[str, list[Any]] = {"evaluation": [], "agent_under_test": []}
    judge = evaluation[0]
    reported = registered_class("CostRecord").model_validate(
        {
            **meta("cst"),
            "domain": "evaluation",
            "usage_record_ids": [str(judge.id)],
            "status": "available",
            "amount": usd("1.5"),
            "basis": "reported",
            "reported_by": "provider",
            "unpriced_units": [],
        }
    )
    records["evaluation"].append(reported)
    if with_duplicate:
        records["evaluation"].append(compute([judge], "evaluation"))  # an estimate of the same call
    for other in evaluation[1:]:
        records["evaluation"].append(compute([other], "evaluation"))

    if not agent:
        records["agent_under_test"].append(compute([], "agent_under_test"))
    for usage in agent:
        records["agent_under_test"].append(compute([usage], "agent_under_test"))
    if agent_cost == "reported_inr":
        imported = cost_api.import_reported_cost(
            domain="agent_under_test",
            usage_record_ids=[agent[0].id],
            reported_by="provider",
            amount_raw="12.50",
            currency_raw="INR",
            record_id=ids_api.CostRecordId.new(),
            created_at=created,
            currency_source_path="/cost/currency",
            amount_source_path="/cost/amount",
        )
        records["agent_under_test"].append(imported.record)

    summaries = []
    for domain, items in records.items():
        summary = cost_api.summarize_costs(
            items,
            domain=domain,
            summary_id=ids_api.CostSummaryId.new(),
            created_at=created,
        )
        summaries.append(_dump(summary))
    flat = [_dump(r) for items in records.values() for r in items]
    return flat, summaries


# keep imports used by type checkers
_UNUSED = (seal, export_doc, _utc)
