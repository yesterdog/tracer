# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F5: Pipecat Evals scenario files (already parsed YAML) to Evaluators, MetricDefinitions,
MetricBindings and Rubrics (docs/phase1/api-surface-mappings-2.md section 3).

The source of the format is pipecat-ai 1.13.0 (``pipecat/evals/{scenario,script,simulation,
scenario_config}.py`` and the documentation pages named in the citations below). A scenario file
does not say everything an Evaluator needs (its language, the persona it refers to, the scripted
scenario's instructions, expected outcome and duration cap): those come from ``F5Declarations``,
the user's own statement, and are the only values from outside the file. Everything else is a
source value, a derivation from source values, or a documented default of Pipecat that is cited.

Source pointers address ``{"document": <the file>, "declared": <the declarations>}``. A scenario is
built all or nothing: a scenario that cannot be built gives no record at all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.digest import compute_digest
from tracer_evalschema.errors import TracerSchemaError
from tracer_evalschema.evaluator import Evaluator, evaluator_digest_domain
from tracer_evalschema.ids import EvaluatorId
from tracer_evalschema.ijson import check_ijson
from tracer_evalschema.metrics import MetricBinding, MetricDefinition
from tracer_evalschema.rubric import Rubric, validate_rubric
from tracer_evalschema.text import MACHINE_KEY_PATTERN, METRIC_KEY_PATTERN

from .common import (
    Citation,
    GapRow,
    Leg,
    MappingResult,
    Recorder,
    TableRow,
    TraceEntry,
    floor_ms,
    pointer,
    raw_text,
    scalar_leaves,
)

__all__ = ["PROVENANCE_TABLE", "F5Declarations", "map_f5"]

RETRIEVED = "2026-10-23"
_SRC = "https://github.com/pipecat-ai/pipecat/blob/v1.13.0/src/pipecat/evals/"

DEFAULT_JUDGE_CITATION = Citation(
    url=_SRC + "scenario_config.py",
    retrieved_at=RETRIEVED,
    statement=(
        "scenario_config._DEFAULT_JUDGE (pipecat-ai 1.13.0): a scenario whose judge block names "
        "no eval config is judged by service ollama with model gemma4:12b "
        "(services.DEFAULT_OLLAMA_JUDGE_MODEL)."
    ),
)
DEFAULT_DURATION_CITATION = Citation(
    url=_SRC + "simulation.py",
    retrieved_at=RETRIEVED,
    statement=(
        "simulation.DEFAULT_MAX_DURATION_S = 300.0 (pipecat-ai 1.13.0): a simulation that names "
        "no max_duration_s is capped at 300 seconds of wall clock."
    ),
)
JUDGE_INPUTS_CITATION = Citation(
    url=_SRC + "simulation.py",
    retrieved_at=RETRIEVED,
    statement=(
        "simulation.py module documentation (pipecat-ai 1.13.0): the judge reads the whole "
        "conversation and the bot's tool calls by name and arguments, not their results."
    ),
)
EXHAUSTIVE_CITATION = Citation(
    url=_SRC + "simulation.py",
    retrieved_at=RETRIEVED,
    statement=(
        'simulation.py module documentation (pipecat-ai 1.13.0): function_calls: "Every listed '
        "call must have happened and any call not listed fails it, so `calls: []` says the bot "
        'must call nothing", so an Evaluator for it states that a call not listed is a failure.'
    ),
)
MEASURE_CITATION = Citation(
    url=_SRC + "simulation.py",
    retrieved_at=RETRIEVED,
    statement=(
        "simulation.py module documentation (pipecat-ai 1.13.0): turns is the persona's turns, "
        "duration the seconds from the first line to the hang-up, words the longest bot reply "
        "in words, latency the slowest reply in seconds, function_calls the bot's calls against "
        "the listed ones."
    ),
)

# measure -> (kind, value type, unit, requires, factor from the file's bound to the unit)
MEASURES: dict[str, tuple[str, str, str | None, tuple[str, ...], int]] = {
    "turns": ("deterministic", "numeric", "count", ("transcript",), 1),
    "duration": ("deterministic", "numeric", "ms", ("turn_timing",), 1000),
    "words": ("deterministic", "numeric", "count", ("agent_transcript",), 1),
    "latency": ("latency", "numeric", "ms", ("turn_timing",), 1000),
    "function_calls": (
        "deterministic",
        "boolean",
        None,
        ("tool_calls", "expected_tool_calls"),
        1,
    ),
}
DEFAULT_JUDGE = ("ollama", "gemma4:12b")
DEFAULT_MAX_DURATION_MS = 300_000
MAX_DURATION_MS = 14_400_000

_MACHINE_KEY = re.compile(MACHINE_KEY_PATTERN)
_METRIC_KEY = re.compile(METRIC_KEY_PATTERN)

# ------------------------------------------------------------------------------ documented keys


@dataclass(frozen=True)
class _List:
    item: Any


_EVAL_BLOCK = {"service": None, "model": None, "endpoint": None, "extra": None, "factory": None}
_SPEECH = {
    "service": None,
    "voice": None,
    "model": None,
    "language": None,
    "speed": None,
    "sample_rate": None,
    "api_key": None,
    "factory": None,
}
_SCENARIO_KEYS: dict[str, Any] = {
    "name": None,
    "context": None,
    "stop_on_failure": None,
    "trigger_disconnect": None,
    "persona": None,
    "goal": None,
    "success": None,
    "simulator": _EVAL_BLOCK,
    "metrics": _List(
        {
            "name": None,
            "criterion": None,
            "min_score": None,
            "measure": None,
            "min_value": None,
            "max_value": None,
            "calls": _List({"name": None, "args": None}),
        }
    ),
    "max_turns": None,
    "max_duration_s": None,
    "max_silence_s": None,
    "runs": None,
    "user": {"modality": None, "speech": _SPEECH},
    "judge": {
        "modality": None,
        "eval": {
            **_EVAL_BLOCK,
            "explainer": _EVAL_BLOCK,
            "explain_below": None,
            "allow_continue": None,
        },
        "transcription": {
            "service": None,
            "model": None,
            "language": None,
            "padding_secs": None,
            "factory": None,
        },
    },
    "turns": _List(
        {
            "user": None,
            "audio": None,
            "dtmf": None,
            "image": None,
            "send_after": {"event": None, "delay_ms": None},
            "expect": _List(
                {
                    "event": None,
                    "within_ms": None,
                    "text_contains": None,
                    "text_excludes": None,
                    "eval": None,
                    "marker": None,
                    "marker_first": None,
                    "markers": None,
                    "text_after": None,
                    "check_from": None,
                    "absent": None,
                    "calls": _List({"name": None, "args": None}),
                    "name": None,
                    "args": None,
                }
            ),
        }
    ),
}
_FILE_KEYS: dict[str, Any] = {**_SCENARIO_KEYS, "scenarios": None}


def _unknown_keys(rec: Recorder, value: Any, schema: Any, ptr: str) -> None:
    """Warn ``unknown_key`` at every key the format does not document (Pipecat ignores them)."""
    if isinstance(schema, _List):
        if isinstance(value, list):
            for position, item in enumerate(value):
                _unknown_keys(rec, item, schema.item, f"{ptr}/{position}")
        return
    if not isinstance(schema, dict) or not isinstance(value, dict):
        return
    for key, child in value.items():
        where = ptr + pointer(str(key))
        if key not in schema:
            rec.warn(
                "unknown_key",
                "the key is not part of the Pipecat 1.13.0 scenario format and is ignored",
                path=where,
            )
        else:
            _unknown_keys(rec, child, schema[key], where)


# ------------------------------------------------------------------------------ declarations


@dataclass(frozen=True)
class F5Declarations:
    """What the user declares because a scenario file does not say it (table kind
    ``user_declared``): the language of every Evaluator and ScriptedTurn built, the persona every
    Evaluator references, the languages and script sensitivity of every definition built, and
    for a scripted scenario (the file has none of the three) the instructions, the expected
    outcome and the duration cap."""

    language: str
    persona: Mapping[str, Any]
    metric_languages: str | Sequence[str]
    script_sensitive: bool
    instructions: str | None = None
    expected_outcome: str | None = None
    max_duration_ms: int | None = None


# ------------------------------------------------------------------------------ the run


@dataclass
class _Built:
    record: Any
    legs: list[Leg]


@dataclass
class _Metric:
    built: _Built
    required: bool
    anchor: str


@dataclass
class _Call:
    name: str
    name_ptr: str
    args: dict[str, Any] | None
    args_ptr: str | None
    rank: int
    anchor: str


@dataclass
class _Judge:
    provider: str
    model: str
    legs: list[Leg]


@dataclass
class _Ctx:
    rec: Recorder
    decl: F5Declarations
    created: str
    late: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)

    def warn_late(self, code: str, message: str, *, path: str, raw: str | None = None) -> None:
        self.late.append((code, message, path, raw))


def map_f5(
    document: Mapping[str, Any], *, declarations: F5Declarations, created_at: str
) -> MappingResult:
    rec = Recorder("F5", created_at, None)
    ctx = _Ctx(rec, declarations, _canonical_time(created_at))
    if not isinstance(document, dict):
        rec.warn("malformed_entry", "the scenario file is not a mapping", path="/document")
        return rec.result()
    name = document.get("name")
    if not isinstance(name, str) or not name:
        rec.warn(
            "malformed_entry",
            "the file has no usable name",
            path="/document/name" if "name" in document else "/document",
        )
        return rec.result()
    _unknown_keys(rec, document, _FILE_KEYS, "/document")
    entries = document.get("scenarios")
    defaults = {k: v for k, v in document.items() if k not in ("scenarios", "name")}
    if "scenarios" in document:
        rec.consume("/document/scenarios")
    if entries is None:
        rec.warn(
            "deprecated_scenario_shape",
            "a scenario's own keys at the top of the file are deprecated since pipecat-ai 1.11.0; "
            "the file is read as one scenario",
            path="/document",
        )
        _scenario(ctx, document, "/document", {}, name, None)
        return rec.result()
    if not isinstance(entries, list) or not entries:
        rec.warn(
            "malformed_entry", "scenarios must be a non-empty list", path="/document/scenarios"
        )
        return rec.result()
    for index, entry in enumerate(entries):
        ptr = f"/document/scenarios/{index}"
        if not isinstance(entry, dict):
            rec.warn("malformed_entry", "a scenario must be a mapping", path=ptr)
            continue
        _unknown_keys(rec, entry, _SCENARIO_KEYS, ptr)
        if "scenarios" in entry:
            rec.warn("malformed_entry", "a scenario cannot hold a scenarios list", path=ptr)
            continue
        scenario_name = entry.get("name")
        if not isinstance(scenario_name, str) or not scenario_name:
            rec.warn("malformed_entry", "a scenario needs a name", path=ptr)
            continue
        _scenario(ctx, entry, ptr, defaults, name, scenario_name)
    return rec.result()


def _canonical_time(created_at: str) -> str:
    try:
        moment = datetime.fromisoformat(created_at)
    except ValueError:
        return created_at
    if moment.tzinfo is None:
        return created_at
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class _View:
    """A scenario with the file's defaults: a key the scenario sets replaces the whole value."""

    def __init__(self, entry: Mapping[str, Any], ptr: str, defaults: Mapping[str, Any]) -> None:
        self.entry, self.ptr, self.defaults = entry, ptr, defaults

    def has(self, key: str) -> bool:
        return key in self.entry or key in self.defaults

    def get(self, key: str) -> tuple[Any, str]:
        if key in self.entry:
            return self.entry[key], self.ptr + pointer(key)
        if key in self.defaults:
            return self.defaults[key], "/document" + pointer(key)
        return None, self.ptr + pointer(key)


def _scenario(
    ctx: _Ctx,
    entry: Mapping[str, Any],
    ptr: str,
    defaults: Mapping[str, Any],
    file_name: str,
    scenario_name: str | None,
) -> None:
    rec = ctx.rec
    view = _View(entry, ptr, defaults)
    ctx.late = []
    if view.has("persona") and view.has("turns"):
        rec.warn(
            "malformed_entry",
            "a scenario is scripted (turns) or simulated (persona), not both",
            path=ptr,
        )
        return
    if view.has("persona"):
        simulated = True
    elif view.has("turns"):
        simulated = False
    else:
        rec.warn(
            "malformed_entry",
            "a scenario needs turns (scripted) or a persona (simulated)",
            path=ptr,
        )
        return
    if scenario_name is None:
        name_sources: tuple[str, ...] = ("/document/name",)
        name, name_derivation = file_name, None
    else:
        name_sources = ("/document/name", f"{ptr}/name")
        name, name_derivation = f"{file_name}/{scenario_name}", "scenario_qualified_name"
    plan = _Plan(ctx, view, ptr, name, Leg("/name", name_sources, name_derivation), simulated)
    if plan.build():
        for code, message, path, raw in ctx.late:
            rec.warn(code, message, path=path, raw=raw)


def _gap(key: str, reason: str) -> GapRow:
    return GapRow("F5", f"F5:{key}", "blocking", "lossy", reason, "dev")


def _failed_fields(exc: ValueError | TracerSchemaError) -> list[str]:
    errors = getattr(exc, "errors", None)
    if errors is None:
        return []
    try:
        located = errors(include_input=False, include_url=False)
    except TypeError:
        return []
    return [str(item["loc"][0]) for item in located if item.get("loc")]


class _Plan:
    """One scenario on its way to an Evaluator, its definitions, bindings and Rubric."""

    def __init__(
        self,
        ctx: _Ctx,
        view: _View,
        ptr: str,
        name: str,
        name_leg: Leg,
        simulated: bool,
    ) -> None:
        self.ctx, self.rec, self.view, self.ptr = ctx, ctx.rec, view, ptr
        self.name, self.name_leg, self.simulated = name, name_leg, simulated
        self.evaluator_id: EvaluatorId = ctx.rec.new_id(Evaluator)
        self.calls: list[_Call] = []
        self.metrics: list[_Metric] = []
        self.keys: set[str] = set()
        self.ranks = 0
        self.exhaustive_ptr: str | None = None

    # -- failure ---------------------------------------------------------------------------

    def not_built(self, why: str, *, key: str | None = None, reason: str | None = None) -> bool:
        self.rec.warn(
            "evaluator_not_built", f"the scenario gives no Evaluator: {why}", path=self.ptr
        )
        if key is not None:
            self.rec.gap(_gap(key, reason or why))
        return False

    def refuse(
        self,
        code: str,
        message: str,
        path: str,
        gap_key: str,
        *,
        raw: str | None = None,
    ) -> None:
        self.rec.warn(code, message, path=path, raw=raw)
        self.rec.gap(_gap(gap_key, message))

    # -- the build -------------------------------------------------------------------------

    def build(self) -> bool:
        texts = self._simulated_texts() if self.simulated else self._scripted_texts()
        if texts is None:
            return False
        instructions, outcome, anchor = texts
        judge = self._judge()
        if judge is None:
            return False
        duration = self._duration()
        if duration is None:
            return False
        scripted: list[dict[str, Any]] = []
        scripted_legs: list[Leg] = []
        if not self.simulated and not self._script(scripted, scripted_legs):
            return False
        if not self._check(judge, anchor):
            return False
        if self.simulated:
            self._simulated_metrics(judge)
        return self._assemble(instructions, outcome, duration, scripted, scripted_legs)

    # -- texts -----------------------------------------------------------------------------

    def _text(self, key: str) -> tuple[str, str] | None:
        value, where = self.view.get(key)
        if not isinstance(value, str) or not value.strip():
            self.rec.warn(
                "malformed_entry",
                f"{key} must be a non-empty text",
                path=where if self.view.has(key) else self.ptr,
            )
            return None
        return value, where

    def _simulated_texts(self) -> tuple[tuple[str, list[Leg]], tuple[str, list[Leg]], str] | None:
        persona = self._text("persona")
        goal = self._text("goal")
        success = self._text("success")
        if persona is None or goal is None or success is None:
            return None
        trimmed = [self.rec.text(value, where)[0].strip() for value, where in (persona, goal)]
        stored, derivation = self.rec.text(success[0], success[1])
        instructions = (
            trimmed[0] + "\n\n" + trimmed[1],
            [Leg("/instructions", (persona[1], goal[1]), "join_persona_and_goal")],
        )
        outcome = (stored, [Leg("/expected_outcome", (success[1],), derivation)])
        return instructions, outcome, success[1]

    def _scripted_texts(self) -> tuple[tuple[str, list[Leg]], tuple[str, list[Leg]], str] | None:
        decl = self.ctx.decl
        missing = [
            name
            for name, value in (
                ("instructions", decl.instructions),
                ("expected_outcome", decl.expected_outcome),
                ("max_duration_ms", decl.max_duration_ms),
            )
            if value is None
        ]
        if missing:
            self.not_built(
                "a scripted scenario states no " + ", ".join(missing) + "; declare it in "
                "F5Declarations"
            )
            return None
        assert decl.instructions is not None
        assert decl.expected_outcome is not None
        texts: list[tuple[str, list[Leg]]] = []
        for name, value in (
            ("instructions", decl.instructions),
            ("expected_outcome", decl.expected_outcome),
        ):
            where = f"/declared/{name}"
            stored, derivation = self.rec.text(value, where)
            texts.append((stored, [Leg(f"/{name}", (where,), derivation or "user_declared")]))
        return texts[0], texts[1], "/declared/expected_outcome"

    # -- judge -----------------------------------------------------------------------------

    def _judge(self) -> _Judge | None:
        value, where = self.view.get("judge")
        block = where
        service: tuple[Any, str] | None = None
        model: tuple[Any, str] | None = None
        unknown = False
        if value is not None:
            config = value.get("eval") if isinstance(value, dict) else None
            if not isinstance(value, dict):
                unknown = True
            elif config:
                block = where + "/eval"
                if not isinstance(config, dict) or config.get("factory"):
                    unknown = True
                else:
                    service = (config.get("service"), block + "/service")
                    model = (config.get("model"), block + "/model")
                    unknown = any(
                        not isinstance(pair[0], str) or _MACHINE_KEY.fullmatch(pair[0]) is None
                        for pair in (service, model)
                    )
        if unknown:
            self.rec.warn(
                "judge_model_unknown",
                "the judge block names no service and model that can be stated (a factory "
                "builds it from code, or it is incomplete or of the wrong type)",
                path=block,
            )
            self.not_built(
                "its judge model cannot be stated",
                key="judge.eval",
                reason="the scenario's judge names no service and model",
            )
            return None
        if service is None or model is None:
            self.ctx.warn_late(
                "judge_default_applied",
                "the scenario names no judge model; Pipecat's documented default is used",
                path=self.ptr,
            )
            cited = "documented_default_judge"
            legs = [
                Leg("/judge_model_ref/provider", (self.ptr,), cited, DEFAULT_JUDGE_CITATION),
                Leg("/judge_model_ref/model", (self.ptr,), cited, DEFAULT_JUDGE_CITATION),
            ]
            return _Judge(DEFAULT_JUDGE[0], DEFAULT_JUDGE[1], legs)
        legs = [
            Leg("/judge_model_ref/provider", (service[1],)),
            Leg("/judge_model_ref/model", (model[1],)),
        ]
        return _Judge(str(service[0]), str(model[0]), legs)

    # -- duration --------------------------------------------------------------------------

    def _duration(self) -> tuple[int, Leg] | None:
        if not self.simulated:
            declared = self.ctx.decl.max_duration_ms
            if type(declared) is not int or not 0 < declared <= MAX_DURATION_MS:
                self.not_built("the declared max_duration_ms is not 1 ms to 4 hours")
                return None
            return declared, Leg(
                "/max_duration_ms", ("/declared/max_duration_ms",), "user_declared"
            )
        value, where = self.view.get("max_duration_s")
        if value is None:
            self.ctx.warn_late(
                "max_duration_default_applied",
                "the scenario names no max_duration_s; Pipecat's documented 300 s is used",
                path=self.ptr,
            )
            return DEFAULT_MAX_DURATION_MS, Leg(
                "/max_duration_ms",
                (self.ptr,),
                "documented_default_max_duration",
                DEFAULT_DURATION_CITATION,
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.rec.warn(
                "malformed_entry", "max_duration_s must be a number of seconds", path=where
            )
            self.not_built("max_duration_s is not a number", key="max_duration_s")
            return None
        measured = floor_ms(value) if value > 0 else None
        if measured is None or not 0 < measured[0] <= MAX_DURATION_MS:
            self.rec.warn(
                "max_duration_out_of_range",
                "max_duration_s is outside 1 ms to 4 hours",
                path=where,
                raw=raw_text(value),
            )
            self.not_built("max_duration_s is outside 1 ms to 4 hours", key="max_duration_s")
            return None
        if measured[1]:
            self.ctx.warn_late(
                "time_precision_reduced",
                "digits finer than a millisecond were cut (truncated, never rounded)",
                path=where,
                raw=raw_text(value),
            )
        return measured[0], Leg("/max_duration_ms", (where,), "seconds_to_ms_floor")

    # -- the script ------------------------------------------------------------------------

    def _script(self, scripted: list[dict[str, Any]], legs: list[Leg]) -> bool:
        rec = self.rec
        turns, tptr = self.view.get("turns")
        if not isinstance(turns, list):
            rec.warn("malformed_entry", "turns must be a list", path=tptr)
            return False
        language = self.ctx.decl.language
        for t, turn in enumerate(turns):
            base = f"{tptr}/{t}"
            if not isinstance(turn, dict):
                rec.warn("malformed_entry", "a turn must be a mapping", path=base)
                continue
            line = self._line(turn, base)
            if line is not None:
                text, sources, derivation = line
                i = len(scripted)
                scripted.append({"index": i, "text": text, "language": language})
                legs.append(Leg(f"/scripted_turns/{i}/index", (sources[0],), "order_index"))
                legs.append(Leg(f"/scripted_turns/{i}/text", sources, derivation))
                legs.append(
                    Leg(f"/scripted_turns/{i}/language", ("/declared/language",), "user_declared")
                )
            expect = turn.get("expect", [])
            if not isinstance(expect, list):
                rec.warn("malformed_entry", "expect must be a list", path=f"{base}/expect")
                continue
            for e, expectation in enumerate(expect):
                where = f"{base}/expect/{e}"
                if not isinstance(expectation, dict):
                    rec.warn("malformed_entry", "an expectation must be a mapping", path=where)
                    continue
                event = expectation.get("event")
                if not isinstance(event, str) or not event:
                    rec.warn("malformed_entry", "an expectation needs an event", path=where)
                    continue
                rec.consume(f"{where}/event")
                if event == "function_call":
                    self._function_call(expectation, where)
        if not scripted:
            return self.not_built(
                "the script has no caller line with text; an Evaluator without a script would "
                "run as a simulation",
                key="turns.user",
                reason="a scripted scenario with no caller line cannot be an Evaluator",
            )
        return True

    def _line(
        self, turn: Mapping[str, Any], base: str
    ) -> tuple[str, tuple[str, ...], str | None] | None:
        """The caller line of a turn: (text, source pointers, derivation), or ``None``."""
        rec = self.rec
        user = turn.get("user")
        where = f"{base}/user"
        if user is None:
            return None
        if isinstance(user, str):
            if not user.strip():
                rec.warn("malformed_entry", "a caller line has no text", path=where)
                return None
            stored, derivation = rec.text(user, where)
            return stored, (where,), derivation
        if not isinstance(user, list):
            rec.warn(
                "malformed_entry", "user must be a text or a list of texts and pauses", path=where
            )
            return None
        texts: list[str] = []
        sources: list[str] = []
        for n, part in enumerate(user):
            at = f"{where}/{n}"
            if isinstance(part, str) and part.strip():
                texts.append(rec.text(part, at)[0].strip())
                sources.append(at)
            elif not (
                isinstance(part, dict)
                and set(part) == {"pause_ms"}
                and type(part["pause_ms"]) is int
                and part["pause_ms"] > 0
            ):
                rec.warn("malformed_entry", "a part of user is neither text nor a pause", path=at)
                return None
        if not texts:
            rec.warn("malformed_entry", "user has pauses but no text", path=where)
            return None
        return " ".join(texts), tuple(sources), "join_user_text_parts"

    def _function_call(self, expectation: Mapping[str, Any], where: str) -> None:
        if expectation.get("absent") is True:
            return
        raw = expectation.get("calls")
        entries: list[tuple[Any, str, Any, str | None, str]] = []
        if raw is None:
            args_ptr = f"{where}/args" if "args" in expectation else None
            entries.append(
                (expectation.get("name"), f"{where}/name", expectation.get("args"), args_ptr, where)
            )
        elif isinstance(raw, list) and raw:
            for c, call in enumerate(raw):
                at = f"{where}/calls/{c}"
                if isinstance(call, str):
                    entries.append((call, at, None, None, at))
                elif isinstance(call, dict):
                    args_ptr = f"{at}/args" if "args" in call else None
                    entries.append((call.get("name"), f"{at}/name", call.get("args"), args_ptr, at))
                else:
                    self.rec.warn("malformed_entry", "a call must be a name or a mapping", path=at)
        else:
            self.rec.warn(
                "malformed_entry", "calls must be a non-empty list", path=f"{where}/calls"
            )
            return
        self._add_calls(entries, where)

    def _add_calls(
        self, entries: Sequence[tuple[Any, str, Any, str | None, str]], anchor: str
    ) -> None:
        """The calls of one expectation or metric share one rank (matched in any order)."""
        built: list[_Call] = []
        for name, name_ptr, args, args_ptr, where in entries:
            if not isinstance(name, str) or _MACHINE_KEY.fullmatch(name) is None:
                self.rec.warn(
                    "expectation_not_representable",
                    "a call with no name that is a machine key cannot be an expected tool call",
                    path=where,
                )
                continue
            stored: dict[str, Any] | None = None
            if args is not None:
                try:
                    copied = check_ijson(args)
                except ValueError:
                    copied = None
                if not isinstance(copied, dict):
                    self.rec.warn(
                        "non_json_value",
                        "the arguments are not one JSON object; the call is skipped",
                        path=args_ptr or where,
                    )
                    continue
                stored = copied or None
            built.append(_Call(name, name_ptr, stored, args_ptr, self.ranks, anchor))
        if built:
            self.calls.extend(built)
            self.ranks += 1

    # -- metrics ---------------------------------------------------------------------------

    def _lang(self) -> tuple[Any, list[Leg]]:
        languages = self.ctx.decl.metric_languages
        if isinstance(languages, str):
            return languages, [Leg("/languages", ("/declared/metric_languages",), "user_declared")]
        values = list(languages)
        return values, [
            Leg(f"/languages/{i}", (f"/declared/metric_languages/{i}",), "user_declared")
            for i in range(len(values))
        ]

    def _definition(self, fields: dict[str, Any], legs: list[Leg], judge: _Judge | None) -> _Built:
        languages, language_legs = self._lang()
        fields = {
            **fields,
            "languages": languages,
            "script_sensitive": self.ctx.decl.script_sensitive,
            "parameters": [],
        }
        legs = [
            *legs,
            *language_legs,
            Leg("/script_sensitive", ("/declared/script_sensitive",), "user_declared"),
        ]
        if judge is not None:
            fields["judge_model_ref"] = {"provider": judge.provider, "model": judge.model}
            legs.extend(judge.legs)
        record = _seal(self.ctx, MetricDefinition, self.rec.new_id(MetricDefinition), fields)
        return _Built(record, legs)

    def _check(self, judge: _Judge, anchor: str) -> bool:
        fields = {
            "key": "expected_outcome",
            "kind": "llm_judge",
            "value_type": "boolean",
            "requires": ["transcript", "tool_calls"],
            "aggregation": {"kind": "per_conversation"},
            "pass_rule": {"kind": "equals_true"},
            "prompt_template": "{{expected_outcome}}",
            "prompt_variables": ["expected_outcome"],
        }
        legs = [
            Leg("/key", (anchor,), "constant:expected_outcome"),
            Leg("/kind", (anchor,), "constant:llm_judge"),
            Leg("/value_type", (anchor,), "constant:boolean"),
            *_requires_legs(("transcript", "tool_calls"), "documented_judge_inputs", anchor),
            Leg("/aggregation/kind", (anchor,), "constant:per_conversation"),
            Leg("/pass_rule/kind", (anchor,), "constant:equals_true"),
            Leg("/prompt_template", (anchor,), "constant:{{expected_outcome}}"),
            Leg("/prompt_variables/0", (anchor,), "constant:expected_outcome"),
        ]
        try:
            built = self._definition(fields, legs, judge)
        except (ValueError, TracerSchemaError) as exc:
            failed = ", ".join(sorted(set(_failed_fields(exc)))) or "a field"
            return self.not_built(
                f"the expected-outcome check cannot be built ({failed}); check the declarations"
            )
        self.keys.add("expected_outcome")
        self.metrics.append(_Metric(built, True, anchor))
        return True

    def _simulated_metrics(self, judge: _Judge) -> None:
        value, where = self.view.get("metrics")
        if value is None:
            return
        if not isinstance(value, list):
            self.refuse("malformed_entry", "metrics must be a list", where, "metrics")
            return
        listing = [
            m
            for m, item in enumerate(value)
            if isinstance(item, dict) and item.get("measure") == "function_calls"
        ]
        many = len(listing) > 1
        if many:
            self.rec.gap(
                _gap(
                    "metrics.calls.@multiple",
                    "several function_calls metrics in one scenario cannot be held by one list "
                    "of expected calls",
                )
            )
        for m, item in enumerate(value):
            if many and m in listing:
                self.rec.warn(
                    "malformed_entry",
                    "a scenario with two or more function_calls metrics is not mapped: "
                    "each judges its own list, an Evaluator holds one",
                    path=f"{where}/{m}",
                )
                continue
            self._metric(item, f"{where}/{m}", judge)

    def _metric(self, item: Any, ptr: str, judge: _Judge) -> None:
        if not isinstance(item, dict):
            self.refuse("malformed_entry", "a metric must be a mapping", ptr, "metrics.name")
            return
        criterion, measure = item.get("criterion"), item.get("measure")
        if (criterion is None) == (measure is None):
            self.refuse(
                "malformed_entry",
                "a metric is judged (criterion) or measured (measure), one of the two",
                ptr,
                "metrics.criterion",
            )
            return
        name = item.get("name", measure)
        name_ptr = f"{ptr}/name" if "name" in item else f"{ptr}/measure"
        if not isinstance(name, str) or _METRIC_KEY.fullmatch(name) is None:
            self.refuse(
                "malformed_entry",
                "a metric needs a name that is a lowercase snake_case key",
                name_ptr if "name" in item or measure is not None else ptr,
                "metrics.name",
            )
            return
        if name in self.keys:
            self.refuse(
                "malformed_entry",
                "a metric name is used twice in one scenario; the first is kept",
                name_ptr,
                "metrics.name",
            )
            return
        try:
            if measure is None:
                outcome = self._judged(item, ptr, name, name_ptr, judge)
            else:
                outcome = self._measured(item, ptr, name, name_ptr, measure)
        except (ValueError, TracerSchemaError, OverflowError):
            self.refuse(
                "malformed_entry",
                "the metric cannot be held by a metric definition",
                ptr,
                "metrics.criterion" if measure is None else "metrics.measure",
            )
            return
        if outcome is None:
            return
        self.keys.add(name)
        self.metrics.append(outcome[0])
        if measure == "function_calls":
            self.exhaustive_ptr = f"{ptr}/calls"
        if outcome[1]:
            self._add_calls(outcome[1], ptr)

    def _judged(
        self, item: Mapping[str, Any], ptr: str, name: str, name_ptr: str, judge: _Judge
    ) -> tuple[_Metric, list[Any]] | None:
        criterion = item["criterion"]
        where = f"{ptr}/criterion"
        if not isinstance(criterion, str) or not criterion or "{{" in criterion:
            self.refuse(
                "malformed_entry",
                "a criterion is a non-empty text without {{ (it is the prompt template)",
                where,
                "metrics.criterion",
            )
            return None
        score = item.get("min_score")
        score_ptr = f"{ptr}/min_score"
        if score is not None and (
            isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1
        ):
            self.refuse(
                "malformed_entry",
                "min_score must be a number in 0..1",
                score_ptr,
                "metrics.min_score",
            )
            return None
        template, derivation = self.rec.text(criterion, where)
        fields: dict[str, Any] = {
            "key": name,
            "kind": "llm_judge",
            "value_type": "boolean",
            "requires": ["transcript", "tool_calls"],
            "aggregation": {"kind": "per_turn_share", "turn_rule": {"kind": "equals_true"}},
            "prompt_template": template,
            "prompt_variables": [],
        }
        legs = [
            Leg("/key", (name_ptr,)),
            Leg("/kind", (ptr,), "constant:llm_judge"),
            Leg("/value_type", (ptr,), "constant:boolean"),
            *_requires_legs(("transcript", "tool_calls"), "documented_judge_inputs", where),
            Leg("/aggregation/kind", (ptr,), "constant:per_turn_share"),
            Leg("/aggregation/turn_rule/kind", (ptr,), "constant:equals_true"),
            Leg("/prompt_template", (where,), derivation),
        ]
        if score is not None:
            fields["pass_rule"] = {"kind": "range", "min": score, "inclusive": True}
            legs += [
                Leg("/pass_rule/kind", (score_ptr,), "constant:range"),
                Leg("/pass_rule/min", (score_ptr,)),
                Leg("/pass_rule/inclusive", (score_ptr,), "constant:True"),
            ]
        built = self._definition(fields, legs, judge)
        return _Metric(built, score is not None, ptr), []

    def _measured(
        self, item: Mapping[str, Any], ptr: str, name: str, name_ptr: str, measure: Any
    ) -> tuple[_Metric, list[Any]] | None:
        where = f"{ptr}/measure"
        if not isinstance(measure, str) or measure not in MEASURES:
            self.rec.warn(
                "unknown_enum_value",
                "the measure is not one of turns, duration, words, latency, function_calls",
                path=where,
                raw=measure if isinstance(measure, str) else None,
            )
            self.rec.gap(_gap("metrics.measure", "the measure is not one Pipecat 1.13.0 defines"))
            return None
        if item.get("min_score") is not None or "min_score" in item:
            self.refuse(
                "malformed_entry",
                "a measured metric takes a range, not min_score",
                f"{ptr}/min_score",
                "metrics.min_score",
            )
            return None
        kind, value_type, unit, requires, factor = MEASURES[measure]
        fields: dict[str, Any] = {
            "key": name,
            "kind": kind,
            "value_type": value_type,
            "requires": list(requires),
            "aggregation": {"kind": "per_conversation"},
            "implementation_ref": f"pipecat_evals:{measure}",
        }
        legs = [
            Leg("/key", (name_ptr,)),
            Leg("/kind", (where,), f"constant:{kind}"),
            Leg("/value_type", (where,), f"constant:{value_type}"),
            *_requires_legs(requires, "documented_measure_table", where),
            Leg("/aggregation/kind", (where,), "constant:per_conversation"),
            Leg("/implementation_ref", (where,), f"constant:pipecat_evals:{measure}"),
        ]
        if unit is not None:
            fields["unit"] = unit
            legs.append(Leg("/unit", (where,), f"constant:{unit}"))
        calls: list[Any] = []
        if measure == "function_calls":
            listed = self._call_list(item, ptr)
            if listed is None:
                return None
            calls = listed
            fields["pass_rule"] = {"kind": "equals_true"}
            legs.append(Leg("/pass_rule/kind", (where,), "constant:equals_true"))
        else:
            rule = self._range(item, ptr, factor, legs)
            if rule is None:
                return None
            fields["pass_rule"] = rule
        built = self._definition(fields, legs, None)
        return _Metric(built, True, ptr), calls

    def _call_list(self, item: Mapping[str, Any], ptr: str) -> list[Any] | None:
        for key in ("min_value", "max_value"):
            if key in item:
                self.refuse(
                    "malformed_entry",
                    "a function_calls metric compares calls, not a range",
                    f"{ptr}/{key}",
                    f"metrics.{key}",
                )
                return None
        where = f"{ptr}/calls"
        raw = item.get("calls")
        if not isinstance(raw, list):
            self.refuse(
                "malformed_entry",
                "a function_calls metric needs a calls list",
                where if "calls" in item else ptr,
                "metrics.calls",
            )
            return None
        entries: list[tuple[Any, str, Any, str | None, str]] = []
        for c, call in enumerate(raw):
            at = f"{where}/{c}"
            if isinstance(call, str) and call:
                entries.append((call, at, None, None, at))
            elif isinstance(call, dict) and isinstance(call.get("name"), str) and call["name"]:
                args = call.get("args")
                if args is not None and not isinstance(args, dict):
                    self.refuse(
                        "malformed_entry", "args must be a mapping", f"{at}/args", "metrics.calls"
                    )
                    return None
                entries.append(
                    (call["name"], f"{at}/name", args, f"{at}/args" if "args" in call else None, at)
                )
            else:
                self.refuse(
                    "malformed_entry",
                    "a call is a name or a mapping with a name",
                    at,
                    "metrics.calls",
                )
                return None
        return entries

    def _range(
        self, item: Mapping[str, Any], ptr: str, factor: int, legs: list[Leg]
    ) -> dict[str, Any] | None:
        if "calls" in item:
            self.refuse(
                "malformed_entry",
                "calls belongs to a function_calls metric",
                f"{ptr}/calls",
                "metrics.calls",
            )
            return None
        bounds: dict[str, Any] = {}
        for key in ("min_value", "max_value"):
            value = item.get(key)
            if value is None:
                continue
            at = f"{ptr}/{key}"
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value != value
                or value in (float("inf"), float("-inf"))
                or value < 0
            ):
                self.refuse(
                    "malformed_entry",
                    f"{key} must be a number that is not negative",
                    at,
                    f"metrics.{key}",
                )
                return None
            bounds[key] = (value, at)
        if not bounds:
            self.refuse(
                "malformed_entry",
                "a measured metric needs min_value or max_value",
                ptr,
                "metrics.min_value",
            )
            return None
        if len(bounds) == 2 and bounds["min_value"][0] > bounds["max_value"][0]:
            self.refuse(
                "malformed_entry",
                "min_value is above max_value",
                bounds["min_value"][1],
                "metrics.min_value",
            )
            return None
        rule: dict[str, Any] = {"kind": "range", "inclusive": True}
        legs.append(Leg("/pass_rule/kind", (ptr,), "constant:range"))
        for key, target in (("min_value", "min"), ("max_value", "max")):
            if key not in bounds:
                continue
            value, at = bounds[key]
            if factor == 1:
                rule[target] = value
                legs.append(Leg(f"/pass_rule/{target}", (at,)))
            else:
                rule[target] = float(Decimal(repr(value)) * factor)
                legs.append(Leg(f"/pass_rule/{target}", (at,), "seconds_to_ms_float"))
        legs.append(Leg("/pass_rule/inclusive", (ptr,), "constant:True"))
        return rule

    # -- assembly --------------------------------------------------------------------------

    def _assemble(
        self,
        instructions: tuple[str, list[Leg]],
        outcome: tuple[str, list[Leg]],
        duration: tuple[int, Leg],
        scripted: list[dict[str, Any]],
        scripted_legs: list[Leg],
    ) -> bool:
        decl = self.ctx.decl
        try:
            persona = {"id": str(decl.persona["id"]), "version": decl.persona["version"]}
        except (KeyError, TypeError):
            return self.not_built("the declared persona has no id and version")
        try:
            return self._seal_all(instructions, outcome, duration, scripted, scripted_legs, persona)
        except (ValueError, TracerSchemaError) as exc:
            failed = ", ".join(sorted(set(_failed_fields(exc)))) or "a record"
            key = {
                "instructions": "persona",
                "expected_outcome": "success",
                "scripted_turns": "turns.user",
            }.get(failed)
            return self.not_built(
                f"the schema refuses {failed}", key=key, reason=f"the schema refuses {failed}"
            )

    def _seal_all(
        self,
        instructions: tuple[str, list[Leg]],
        outcome: tuple[str, list[Leg]],
        duration: tuple[int, Leg],
        scripted: list[dict[str, Any]],
        scripted_legs: list[Leg],
        persona: dict[str, Any],
    ) -> bool:
        ctx = self.ctx
        bindings: list[_Built] = []
        for metric in self.metrics:
            fields = {
                "evaluator_id": str(self.evaluator_id),
                "metric": _ref(metric.built.record),
                "params": {},
                "enabled": True,
            }
            record = _seal(ctx, MetricBinding, self.rec.new_id(MetricBinding), fields)
            bindings.append(_Built(record, [Leg("/enabled", (metric.anchor,), "constant:True")]))
        calls: list[dict[str, Any]] = []
        call_legs: list[Leg] = []
        for i, call in enumerate(self.calls):
            body: dict[str, Any] = {
                "name": call.name,
                "match": "name_only" if call.args is None else "arguments_subset",
                "order_index": call.rank,
            }
            sources = (
                (call.args_ptr,) if call.args_ptr is not None and call.args is not None else ()
            )
            call_legs.append(Leg(f"/expected_tool_calls/{i}/name", (call.name_ptr,)))
            call_legs.append(Leg(f"/expected_tool_calls/{i}/match", sources, "args_to_match_mode"))
            call_legs.append(
                Leg(f"/expected_tool_calls/{i}/order_index", (call.anchor,), "order_index")
            )
            if call.args is not None and call.args_ptr is not None:
                body["arguments"] = call.args
                for leaf, _ in scalar_leaves(call.args):
                    call_legs.append(
                        Leg(f"/expected_tool_calls/{i}/arguments{leaf}", (call.args_ptr + leaf,))
                    )
            calls.append(body)
        fields = {
            "name": self.name,
            "language": ctx.decl.language,
            "instructions": instructions[0],
            "expected_outcome": outcome[0],
            "persona": persona,
            "metric_bindings": [_ref(b.record) for b in bindings],
            "scripted_turns": scripted,
            "expected_tool_calls": calls,
            "max_duration_ms": duration[0],
        }
        if self.exhaustive_ptr is not None:
            fields["expected_tool_calls_exhaustive"] = True
        evaluator = _seal(ctx, Evaluator, self.evaluator_id, fields)
        evaluator_legs = [
            self.name_leg,
            Leg("/language", ("/declared/language",), "user_declared"),
            *instructions[1],
            *outcome[1],
            Leg("/persona/id", ("/declared/persona/id",), "user_declared"),
            Leg("/persona/version", ("/declared/persona/version",), "user_declared"),
            *scripted_legs,
            *call_legs,
            duration[1],
        ]
        if self.exhaustive_ptr is not None:
            evaluator_legs.append(
                Leg(
                    "/expected_tool_calls_exhaustive",
                    (self.exhaustive_ptr,),
                    "documented_measure_table",
                    EXHAUSTIVE_CITATION,
                )
            )
        items = [
            {"binding": _ref(binding.record), "required": metric.required}
            for binding, metric in zip(bindings, self.metrics, strict=True)
        ]
        rubric = _seal(ctx, Rubric, self.rec.new_id(Rubric), {"items": items})
        rubric_legs = [
            Leg(f"/items/{i}/required", (metric.anchor,), f"constant:{metric.required}")
            for i, metric in enumerate(self.metrics)
        ]
        validate_rubric(
            rubric,
            bindings=[b.record for b in bindings],
            definitions=[m.built.record for m in self.metrics],
        )
        pending = [
            *(m.built for m in self.metrics),
            *bindings,
            _Built(evaluator, evaluator_legs),
            _Built(rubric, rubric_legs),
        ]
        for built in pending:
            self.rec.records.append(built.record)
            key = str(built.record.id)
            for leg in built.legs:
                self.rec.trace.append(
                    TraceEntry(key, leg.pointer, leg.sources, leg.derivation, leg.citation)
                )
        return True


def _requires_legs(requires: Sequence[str], derivation: str, anchor: str) -> list[Leg]:
    citation = (
        JUDGE_INPUTS_CITATION if derivation == "documented_judge_inputs" else MEASURE_CITATION
    )
    return [Leg(f"/requires/{i}", (anchor,), derivation, citation) for i in range(len(requires))]


def _ref(record: Any) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "version": record.version,
        "content_digest": record.content_digest.model_dump(mode="json", exclude_none=True),
    }


def _seal(ctx: _Ctx, cls: type[Any], entity_id: Any, fields: Mapping[str, Any]) -> Any:
    """The record with its content digest (ADR 0006 Decision 5). ``ValueError`` or
    ``TracerSchemaError`` if the schema refuses the fields."""
    doc = {
        "id": str(entity_id),
        "schema_version": SCHEMA_VERSION,
        "created_at": ctx.created,
        "metadata": {},
        "metadata_flags": [],
        "version": 1,
        **fields,
    }
    subject = evaluator_digest_domain(doc) if cls is Evaluator else doc
    digest = compute_digest(subject).model_dump(mode="json", exclude_none=True)
    return cls.model_validate({**doc, "content_digest": digest})


# ------------------------------------------------------------------------------ the table


def _row(
    target: str,
    kind: str,
    source: str | None,
    derivation: str | None,
    note: str,
    citation: Citation | None = None,
) -> TableRow:
    return TableRow(target, kind, source, derivation, citation, note)


_SURROGATE_NOTE = "a lone surrogate is replaced by U+FFFD (N82)"
_CALL_NAMES = (
    "metrics.calls, metrics.calls.name, turns.expect.calls, turns.expect.calls.name, "
    "turns.expect.name"
)
_CALL_ARGS = "metrics.calls.args, turns.expect.calls.args, turns.expect.args"
_DECLARED = "the user's declaration (F5Declarations)"

PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row("Evaluator.name", "source", "name", None, "the file name alone in the deprecated shape"),
    _row(
        "Evaluator.name",
        "derivation",
        "name (file), name (scenario)",
        "scenario_qualified_name",
        "Pipecat's own name: <file name>/<scenario name>",
    ),
    _row("Evaluator.language", "user_declared", "declared language", "user_declared", _DECLARED),
    _row(
        "Evaluator.instructions",
        "derivation",
        "persona, goal",
        "join_persona_and_goal",
        "persona and goal, each trimmed, one blank line between (F5-M3)",
    ),
    _row(
        "Evaluator.instructions",
        "user_declared",
        "declared instructions",
        "user_declared",
        "a scripted scenario has none; the declaration supplies it",
    ),
    _row(
        "Evaluator.instructions",
        "derivation",
        "declared instructions",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row("Evaluator.expected_outcome", "source", "success", None, "verbatim, edges kept"),
    _row(
        "Evaluator.expected_outcome",
        "derivation",
        "success",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "Evaluator.expected_outcome",
        "user_declared",
        "declared expected_outcome",
        "user_declared",
        "a scripted scenario has none; the declaration supplies it",
    ),
    _row(
        "Evaluator.expected_outcome",
        "derivation",
        "declared expected_outcome",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row("Evaluator.persona.id", "user_declared", "declared persona", "user_declared", _DECLARED),
    _row(
        "Evaluator.persona.version",
        "user_declared",
        "declared persona",
        "user_declared",
        _DECLARED,
    ),
    _row("Evaluator.test_profile", "absent", None, None, "never filled from a scenario file"),
    _row(
        "Evaluator.scripted_turns.*.index",
        "derivation",
        "turns.user",
        "order_index",
        "list position over the lines that have text",
    ),
    _row("Evaluator.scripted_turns.*.text", "source", "turns.user", None, "the string verbatim"),
    _row(
        "Evaluator.scripted_turns.*.text",
        "derivation",
        "turns.user",
        "join_user_text_parts",
        "a line that pauses is its texts, trimmed, joined by one space",
    ),
    _row(
        "Evaluator.scripted_turns.*.text",
        "derivation",
        "turns.user",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "Evaluator.scripted_turns.*.language",
        "user_declared",
        "declared language",
        "user_declared",
        _DECLARED,
    ),
    _row("Evaluator.expected_tool_calls.*.name", "source", _CALL_NAMES, None, "verbatim"),
    _row(
        "Evaluator.expected_tool_calls.*.match",
        "derivation",
        _CALL_ARGS,
        "args_to_match_mode",
        "arguments_subset for non-empty args, name_only otherwise (F5-M6)",
    ),
    _row(
        "Evaluator.expected_tool_calls.*.order_index",
        "derivation",
        "metrics.calls, turns.expect.calls",
        "order_index",
        "one rank per expectation or metric, in file order (F5-M6)",
    ),
    _row(
        "Evaluator.expected_tool_calls_exhaustive",
        "derivation",
        "metrics.calls",
        "documented_measure_table",
        "a function_calls metric fails on any call not listed, so True (F5-2)",
        EXHAUSTIVE_CITATION,
    ),
    _row(
        "Evaluator.expected_tool_calls.*.arguments.*",
        "source",
        _CALL_ARGS,
        None,
        "verbatim; every listed key must be present, extras ignored",
    ),
    _row(
        "Evaluator.max_duration_ms",
        "derivation",
        "max_duration_s",
        "seconds_to_ms_floor",
        "whole milliseconds, truncated",
    ),
    _row(
        "Evaluator.max_duration_ms",
        "derivation",
        "max_duration_s",
        "documented_default_max_duration",
        "the 300 s Pipecat documents for a simulation that names none (F5-M4)",
        DEFAULT_DURATION_CITATION,
    ),
    _row(
        "Evaluator.max_duration_ms",
        "user_declared",
        "declared max_duration_ms",
        "user_declared",
        "a scripted scenario has none; the declaration supplies it",
    ),
    _row("MetricDefinition.key", "source", "metrics.name", None, "verbatim"),
    _row(
        "MetricDefinition.key",
        "source",
        "metrics.measure",
        None,
        "the name defaults to the measure",
    ),
    _row(
        "MetricDefinition.key",
        "constant",
        "success",
        "constant:expected_outcome",
        "the expected-outcome check every Evaluator has",
    ),
    _row(
        "MetricDefinition.kind",
        "constant",
        "metrics.criterion, success",
        "constant:llm_judge",
        "a judged metric and the expected-outcome check",
    ),
    _row(
        "MetricDefinition.kind",
        "constant",
        "metrics.measure",
        "constant:deterministic",
        "turns, duration, words and function_calls",
    ),
    _row("MetricDefinition.kind", "constant", "metrics.measure", "constant:latency", "latency"),
    _row(
        "MetricDefinition.value_type",
        "constant",
        "metrics.criterion, success",
        "constant:boolean",
        "yes or no per turn (Pipecat)",
    ),
    _row(
        "MetricDefinition.value_type",
        "constant",
        "metrics.measure",
        "constant:numeric",
        "a measured value",
    ),
    _row(
        "MetricDefinition.value_type",
        "constant",
        "metrics.measure",
        "constant:boolean",
        "function_calls",
    ),
    _row("MetricDefinition.unit", "constant", "metrics.measure", "constant:count", "turns, words"),
    _row(
        "MetricDefinition.unit", "constant", "metrics.measure", "constant:ms", "duration, latency"
    ),
    _row(
        "MetricDefinition.requires.*",
        "derivation",
        "metrics.criterion, success",
        "documented_judge_inputs",
        "the judge reads the conversation and the bot's tool calls (F5-M5)",
        JUDGE_INPUTS_CITATION,
    ),
    _row(
        "MetricDefinition.requires.*",
        "derivation",
        "metrics.measure",
        "documented_measure_table",
        "the inputs each measure needs (F5-M5)",
        MEASURE_CITATION,
    ),
    _row(
        "MetricDefinition.languages",
        "user_declared",
        "declared metric_languages",
        "user_declared",
        _DECLARED,
    ),
    _row(
        "MetricDefinition.languages.*",
        "user_declared",
        "declared metric_languages",
        "user_declared",
        _DECLARED,
    ),
    _row(
        "MetricDefinition.script_sensitive",
        "user_declared",
        "declared script_sensitive",
        "user_declared",
        _DECLARED,
    ),
    _row(
        "MetricDefinition.aggregation.kind",
        "constant",
        "metrics.criterion",
        "constant:per_turn_share",
        "the judge answers for every bot turn (Pipecat)",
    ),
    _row(
        "MetricDefinition.aggregation.kind",
        "constant",
        "metrics.measure, success",
        "constant:per_conversation",
        "one value per run",
    ),
    _row(
        "MetricDefinition.aggregation.turn_rule.kind",
        "constant",
        "metrics.criterion",
        "constant:equals_true",
        "a turn counts when the judge says yes",
    ),
    _row(
        "MetricDefinition.pass_rule.kind",
        "constant",
        "metrics.min_score, metrics.min_value, metrics.max_value",
        "constant:range",
        "bounds are inclusive (Pipecat)",
    ),
    _row(
        "MetricDefinition.pass_rule.kind",
        "constant",
        "metrics.measure, success",
        "constant:equals_true",
        "function_calls and the expected-outcome check",
    ),
    _row("MetricDefinition.pass_rule.min", "source", "metrics.min_score", None, "kept as written"),
    _row("MetricDefinition.pass_rule.min", "source", "metrics.min_value", None, "kept as written"),
    _row(
        "MetricDefinition.pass_rule.min",
        "derivation",
        "metrics.min_value",
        "seconds_to_ms_float",
        "a bound in seconds is milliseconds as a float",
    ),
    _row("MetricDefinition.pass_rule.max", "source", "metrics.max_value", None, "kept as written"),
    _row(
        "MetricDefinition.pass_rule.max",
        "derivation",
        "metrics.max_value",
        "seconds_to_ms_float",
        "a bound in seconds is milliseconds as a float",
    ),
    _row(
        "MetricDefinition.pass_rule.inclusive",
        "constant",
        "metrics.min_score, metrics.min_value, metrics.max_value",
        "constant:True",
        "Pipecat's bounds are inclusive",
    ),
    *(
        _row(
            "MetricDefinition.implementation_ref",
            "constant",
            "metrics.measure",
            f"constant:pipecat_evals:{measure}",
            "Pipecat's own computation of the measure (F5-5)",
        )
        for measure in MEASURES
    ),
    _row("MetricDefinition.scale", "absent", None, None, "a boolean or numeric metric has none"),
    _row("MetricDefinition.parameters", "absent", None, None, "no parameter is declared"),
    _row("MetricDefinition.prompt_template", "source", "metrics.criterion", None, "verbatim"),
    _row(
        "MetricDefinition.prompt_template",
        "derivation",
        "metrics.criterion",
        "lone_surrogate_replaced",
        _SURROGATE_NOTE,
    ),
    _row(
        "MetricDefinition.prompt_template",
        "constant",
        "success",
        "constant:{{expected_outcome}}",
        "the variable alone: the runner fills in the evaluator's expected outcome",
    ),
    _row(
        "MetricDefinition.prompt_variables.*",
        "constant",
        "success",
        "constant:expected_outcome",
        "the reserved variable",
    ),
    _row(
        "MetricDefinition.judge_model_ref.provider",
        "source",
        "judge.eval.service",
        None,
        "verbatim",
    ),
    _row("MetricDefinition.judge_model_ref.model", "source", "judge.eval.model", None, "verbatim"),
    _row(
        "MetricDefinition.judge_model_ref.provider",
        "derivation",
        "judge",
        "documented_default_judge",
        "Pipecat's default judge for a scenario that names none (F5-M4)",
        DEFAULT_JUDGE_CITATION,
    ),
    _row(
        "MetricDefinition.judge_model_ref.model",
        "derivation",
        "judge",
        "documented_default_judge",
        "Pipecat's default judge for a scenario that names none (F5-M4)",
        DEFAULT_JUDGE_CITATION,
    ),
    _row(
        "MetricBinding.enabled",
        "constant",
        "metrics.criterion, metrics.measure, success",
        "constant:True",
        "every built metric is bound and enabled",
    ),
    _row(
        "Rubric.items.*.required",
        "constant",
        "metrics.min_score, metrics.measure, success",
        "constant:True",
        "a metric with min_score, a measure and the expected-outcome check fail the run",
    ),
    _row(
        "Rubric.items.*.required",
        "constant",
        "metrics.criterion",
        "constant:False",
        "a judged metric without min_score is reported and never fails the run",
    ),
)
