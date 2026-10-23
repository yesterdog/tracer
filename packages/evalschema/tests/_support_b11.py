# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 14 tests: the F2 mapping (Langfuse-attributed OTLP, P1-30) and the
F5 mapping (Pipecat Evals scenario YAML, P1-34).

The converters do not exist yet. They are test-side code at
``packages/evalschema/tests/support/mappings/`` (``langfuse_otlp.py`` with ``map_f2`` and
``pipecat_evals.py`` with ``map_f5``) and are imported lazily through ``_support_b6.mapping()``,
so a
converter that is not written yet fails the test that needs it with ``ModuleNotFoundError``. The
pinned names, readings (P) and ambiguities (M) are in docs/phase1/api-surface-mappings-2.md.

What is here:

- loaders for the two synthetic fixture sets under ``tests/fixtures/synthetic/`` (hand-written from
  documentation, method ``synthetic_from_docs``);
- builders for hand-made Langfuse-attributed OTLP spans (arrays, doubles and JSON-text attributes);
- a scenario-YAML loader that follows Pipecat's rules (decimal-only integers, ``!include`` spliced
  relative to the including file) and refuses an include that leaves the fixture directory;
- the closed derivation registry of these two mappings (the one of ``_support_b6`` plus the
  derivations that only F2 and F5 use) and ``check_no_fabrication_b11()``, the P-51 check for them:
  every non-null leaf of every output record has a trace entry whose sources resolve in the input
  (numbers inside a JSON text are read as their lexeme), whose derivation is declared in the
  converter's provenance table and re-computes to the stored value, and cites its contract when it
  rests on one;
- the dispositions of every documented Langfuse attribute (F2) and every documented scenario key
  (F5), taken from the documentation pages and the Pipecat 1.13.0 source (retrieved 2026-10-23).

Everything is ASCII; non-ASCII characters are built with chr() or read from fixture files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from ._support import FIXTURES, api
from ._support_b6 import (
    CITATION_DERIVATIONS,
    CREATED_AT,
    DERIVATIONS,
    ENTRY_CITATION_ONLY,
    FRAMEWORK_FIELDS,
    leaves,
    mapping,
    new_run_id,
    privacy_synthetic,
)

__all__ = [
    "BASE_NS",
    "CITATION_DERIVATIONS_B11",
    "CREATED_AT",
    "DERIVATIONS_B11",
    "ENTRY_CITATION_ONLY_B11",
    "EVALS",
    "F2_ATTRIBUTES",
    "F2_DOCS",
    "F2_FRAMEWORK",
    "F5_DOCUMENTED_KEYS",
    "F5_FRAMEWORK",
    "LANGFUSE",
    "MEASURE_TABLE",
    "OWNERS",
    "SYNTH",
    "WARNING_CODES_F2",
    "WARNING_CODES_F5",
    "Num",
    "check_no_fabrication_b11",
    "check_warnings_quote_sources_b11",
    "declarations",
    "declared_root",
    "f2_doc",
    "f2_universe_key",
    "f5_key",
    "f5_universe",
    "lf_attr",
    "lf_doc",
    "lf_span",
    "load_scenario_yaml",
    "parse_scenario_yaml",
    "resolve_source_b11",
    "run_f2",
    "run_f5",
]

OWNERS: frozenset[str] = frozenset(
    {"schema-architect", "dev", "indic-linguistics", "staff-reviewer", "qa", "security-licensing"}
)
SYNTH = FIXTURES / "synthetic"
LANGFUSE = SYNTH / "langfuse-2026-10-23"
EVALS = SYNTH / "pipecat-evals-1.13.0"
F2_DOCS: tuple[str, ...] = ("voice_call", "generic_otel")

# ------------------------------------------------------------------------------------ F2 reading

# The attributes the Langfuse OTLP documentation names (retrieved 2026-10-23, pages in
# fixtures/synthetic/langfuse-2026-10-23/provenance.json) plus the OpenTelemetry GenAI names it
# refers to. "mapped": the mapping reads it (a provenance-table row names it verbatim in its
# ``source``). "gap": the mapping does not read it, and the gap register has a row for it.
F2_ATTRIBUTES: dict[str, str] = {
    # trace level
    "langfuse.trace.name": "gap",
    "langfuse.user.id": "gap",
    "user.id": "gap",
    "langfuse.session.id": "mapped",
    "session.id": "mapped",
    "langfuse.release": "gap",
    "langfuse.trace.public": "gap",
    "langfuse.trace.tags": "gap",
    "langfuse.trace.metadata.*": "gap",
    "langfuse.trace.input": "gap",
    "langfuse.trace.output": "gap",
    "langfuse.version": "gap",
    "langfuse.environment": "gap",
    "deployment.environment": "gap",
    "deployment.environment.name": "gap",
    # observation level
    "langfuse.observation.type": "mapped",
    "langfuse.observation.level": "gap",
    "langfuse.observation.status_message": "gap",
    "langfuse.observation.metadata.*": "gap",
    "langfuse.observation.input": "mapped",
    "langfuse.observation.output": "mapped",
    "gen_ai.prompt": "gap",
    "gen_ai.completion": "gap",
    "input.value": "gap",
    "output.value": "gap",
    "mlflow.spanInputs": "gap",
    "mlflow.spanOutputs": "gap",
    "gen_ai.tool.call.arguments": "mapped",
    "gen_ai.tool.call.result": "mapped",
    "langfuse.observation.model.name": "mapped",
    "gen_ai.request.model": "mapped",
    "gen_ai.response.model": "mapped",
    "llm.model_name": "mapped",
    "model": "mapped",
    "gen_ai.provider.name": "mapped",
    "gen_ai.operation.name": "mapped",
    "langfuse.observation.model.parameters": "gap",
    "gen_ai.request.temperature": "gap",
    "llm.invocation_parameters.*": "gap",
    "langfuse.observation.usage_details": "mapped",
    "gen_ai.usage.input_tokens": "mapped",
    "gen_ai.usage.output_tokens": "mapped",
    "gen_ai.usage.cache_read.input_tokens": "mapped",
    "gen_ai.usage.cache_creation.input_tokens": "mapped",
    "gen_ai.usage.reasoning.output_tokens": "gap",
    "llm.token_count.*": "gap",
    "langfuse.observation.cost_details": "mapped",
    "gen_ai.usage.cost": "mapped",
    "langfuse.observation.prompt.name": "gap",
    "langfuse.observation.prompt.version": "gap",
    "langfuse.prompt.name": "gap",
    "langfuse.prompt.version": "gap",
    "langfuse.observation.completion_start_time": "gap",
    # OTLP structure
    "resource.*": "gap",
    "scope.name": "gap",
}

WARNING_CODES_F2: frozenset[str] = frozenset(
    {
        "unknown_enum_value",
        "usage_quantities_not_merged",
        "usage_inconsistent",
        "usage_key_unmapped",
        "cost_total_absent",
        "observation_json_unparseable",
        "tool_arguments_incomplete",
        "malformed_entry",
        "text_not_valid_unicode",
        "time_precision_reduced",
        # the warnings of cost.import_reported_cost are forwarded unchanged
        "currency_not_reported",
        "amount_unparseable",
        "decimal_normalized",
        "currency_label_normalized",
        "unsupported_currency",
        "currency_contract_conflict",
    }
)

# ------------------------------------------------------------------------------------ F5 reading

# Every key of the Pipecat 1.13.0 scenario file format, as ``f5_key`` writes it (path of keys
# without list positions and without the leading ``scenarios``; the free-form blocks ``extra``,
# ``args`` and ``context`` end the path). The value is the disposition the contract pins:
# "mapped" (a provenance-table row names the key in its ``source``), "consumed" (read as decision
# input: a provenance-table row of kind ``absent`` or the result's ``consumed`` names it), "gap"
# (the gap register has a row ``F5:<key>``), "loader" (handled by the YAML loader, outside the
# converter).
F5_DOCUMENTED_KEYS: dict[str, str] = {
    "name": "mapped",
    "scenarios": "consumed",
    "context": "gap",
    "stop_on_failure": "gap",
    "trigger_disconnect": "gap",
    "persona": "mapped",
    "goal": "mapped",
    "success": "mapped",
    "simulator.service": "gap",
    "simulator.model": "gap",
    "simulator.endpoint": "gap",
    "simulator.extra": "gap",
    "simulator.factory": "gap",
    "max_turns": "gap",
    "max_duration_s": "mapped",
    "max_silence_s": "gap",
    "runs": "gap",
    "metrics.name": "mapped",
    "metrics.criterion": "mapped",
    "metrics.min_score": "mapped",
    "metrics.measure": "mapped",
    "metrics.min_value": "mapped",
    "metrics.max_value": "mapped",
    "metrics.calls": "mapped",
    "metrics.calls.name": "mapped",
    "metrics.calls.args": "mapped",
    "user.modality": "gap",
    "user.speech.service": "gap",
    "user.speech.voice": "gap",
    "user.speech.model": "gap",
    "user.speech.language": "gap",
    "user.speech.speed": "gap",
    "user.speech.sample_rate": "gap",
    "user.speech.api_key": "gap",
    "user.speech.factory": "gap",
    "judge.modality": "gap",
    "judge.eval.service": "mapped",
    "judge.eval.model": "mapped",
    "judge.eval.endpoint": "gap",
    "judge.eval.extra": "gap",
    "judge.eval.factory": "gap",
    "judge.eval.explainer": "gap",
    "judge.eval.explainer.service": "gap",
    "judge.eval.explainer.model": "gap",
    "judge.eval.explainer.endpoint": "gap",
    "judge.eval.explainer.extra": "gap",
    "judge.eval.explainer.factory": "gap",
    "judge.eval.explain_below": "gap",
    "judge.eval.allow_continue": "gap",
    "judge.transcription.service": "gap",
    "judge.transcription.model": "gap",
    "judge.transcription.language": "gap",
    "judge.transcription.padding_secs": "gap",
    "judge.transcription.factory": "gap",
    "turns.user": "mapped",
    "turns.user.pause_ms": "gap",
    "turns.audio": "gap",
    "turns.dtmf": "gap",
    "turns.image": "gap",
    "turns.send_after.event": "gap",
    "turns.send_after.delay_ms": "gap",
    "turns.expect.event": "consumed",
    "turns.expect.within_ms": "gap",
    "turns.expect.text_contains": "gap",
    "turns.expect.text_excludes": "gap",
    "turns.expect.eval": "gap",
    "turns.expect.marker": "gap",
    "turns.expect.marker_first": "gap",
    "turns.expect.markers": "gap",
    "turns.expect.text_after": "gap",
    "turns.expect.check_from": "gap",
    "turns.expect.absent": "gap",
    "turns.expect.calls": "mapped",
    "turns.expect.calls.name": "mapped",
    "turns.expect.calls.args": "mapped",
    "turns.expect.name": "mapped",
    "turns.expect.args": "mapped",
    "!include": "loader",
}

F5_OPEN_KEYS: frozenset[str] = frozenset({"extra", "args", "context"})

WARNING_CODES_F5: frozenset[str] = frozenset(
    {
        "deprecated_scenario_shape",
        "evaluator_not_built",
        "judge_default_applied",
        "max_duration_default_applied",
        "expectation_not_representable",
        "text_not_valid_unicode",
        "judge_model_unknown",
        "unknown_key",
        "unknown_enum_value",
        "empty_call_list",
        "max_duration_out_of_range",
        "non_json_value",
        "malformed_entry",
        "time_precision_reduced",
    }
)

# The measures of a simulation (Pipecat 1.13.0 simulation.py SIMULATION_MEASURES) and what the
# mapping builds for each (contract section 3.3). ``scale`` is the factor that turns the bound the
# file writes into the unit of the definition (seconds -> ms).
MEASURE_TABLE: dict[str, dict[str, Any]] = {
    "turns": {
        "kind": "deterministic",
        "value_type": "numeric",
        "unit": "count",
        "requires": ["transcript"],
        "scale": 1,
    },
    "duration": {
        "kind": "deterministic",
        "value_type": "numeric",
        "unit": "ms",
        "requires": ["turn_timing"],
        "scale": 1000,
    },
    "words": {
        "kind": "deterministic",
        "value_type": "numeric",
        "unit": "count",
        "requires": ["agent_transcript"],
        "scale": 1,
    },
    "latency": {
        "kind": "latency",
        "value_type": "numeric",
        "unit": "ms",
        "requires": ["turn_timing"],
        "scale": 1000,
    },
    "function_calls": {
        "kind": "deterministic",
        "value_type": "boolean",
        "unit": None,
        "requires": ["tool_calls", "expected_tool_calls"],
        "scale": 1,
    },
}

# --------------------------------------------------------------------------- OTLP/JSON builders


class Num(str):
    """A number read from inside a JSON text: its lexeme as sent, as text (``0.000097``,
    ``9.7e-05``, ``86``). The checker resolves chained source pointers to this, so a converter that
    keeps the lexeme (``parse_float=str``) and one that parses and re-prints are both checked."""

    __slots__ = ()


def lf_attr(key: str, value: Any) -> dict[str, Any]:
    """One OTLP/JSON attribute: string, int64 (as text), double, bool or an array of strings."""
    if isinstance(value, bool):
        typed: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        typed = {"intValue": str(value)}
    elif isinstance(value, float):
        typed = {"doubleValue": value}
    elif isinstance(value, list):
        typed = {"arrayValue": {"values": [{"stringValue": item} for item in value]}}
    else:
        typed = {"stringValue": value}
    return {"key": key, "value": typed}


TRACE_ID = "5b8efff798038103d269b633813fc60c"
BASE_NS = 1_792_746_000_000_000_000  # 2026-10-23T09:00:00Z


def lf_span(
    name: str,
    span_id: str,
    attrs: Sequence[tuple[str, Any]] = (),
    *,
    parent: str | None = None,
    start_ms: int = 0,
    end_ms: int = 500,
    extra_ns: int = 0,
    trace_id: str = TRACE_ID,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(BASE_NS + start_ms * 1_000_000 + extra_ns),
        "endTimeUnixNano": str(BASE_NS + end_ms * 1_000_000 + extra_ns),
        "attributes": [lf_attr(k, v) for k, v in attrs],
        "events": [],
        "status": {"code": 0},
    }
    if parent is not None:
        span["parentSpanId"] = parent
    return span


def lf_doc(*spans: dict[str, Any], resource: Sequence[tuple[str, Any]] = ()) -> dict[str, Any]:
    attrs = [lf_attr(k, v) for k, v in (resource or [("service.name", "tracer-test")])]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": attrs},
                "scopeSpans": [{"scope": {"name": "tracer-test"}, "spans": list(spans)}],
            }
        ]
    }


def f2_doc(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((LANGFUSE / name / "f2_spans.otlp.json").read_bytes())
    return doc


def f2_universe_key(doc: Mapping[str, Any], pointer: str) -> str | None:
    """The logical F2 source key of a pointer (same key space as F1): ``<span name>.<attribute>``,
    ``<span name>.@<field>``, ``resource.<attribute>`` or ``scope.name``."""
    from ._support_b6 import f1_key

    return f1_key(doc, pointer)


def run_f2(doc: Mapping[str, Any], **over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "run_id": new_run_id(),
        "created_at": CREATED_AT,
        "privacy": privacy_synthetic(),
    }
    kwargs.update(over)
    return mapping("langfuse_otlp").map_f2(doc, **kwargs)


# ------------------------------------------------------------------------------------ YAML


def _yaml() -> Any:
    import importlib

    return importlib.import_module("yaml")


def _scenario_loader(base_dir: Path, root: Path) -> Any:
    """Pipecat's loader (1.13.0 scenario.py and utils/yaml.py): a SafeLoader that reads only
    plain-decimal integers (``012`` stays text), with ``!include <path>`` spliced relative to the
    including file. Unlike Pipecat, an include that leaves ``root`` is refused: the fixtures are
    trusted, an arbitrary document handed to a test helper is not."""
    yaml = _yaml()
    safe: Any = yaml.SafeLoader

    class Loader(safe):  # type: ignore[misc]
        pass

    Loader.yaml_implicit_resolvers = {
        ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:int"]
        for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    yaml.add_implicit_resolver(
        "tag:yaml.org,2002:int",
        re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$"),
        list("-+0123456789"),
        Loader=Loader,
    )

    def include(loader: Any, node: Any) -> Any:
        target = (base_dir / str(loader.construct_scalar(node))).resolve()
        if root.resolve() not in (target, *target.parents):
            raise ValueError("an !include may not leave the fixture directory")
        with target.open(encoding="utf-8") as handle:
            return yaml.load(handle, _scenario_loader(target.parent, root))

    Loader.add_constructor("!include", include)
    return Loader


def load_scenario_yaml(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """A scenario file as the top-level mapping Pipecat reads (``!include`` spliced)."""
    yaml = _yaml()
    confined = (root or path.parent).resolve()
    with path.open(encoding="utf-8") as handle:
        data = yaml.load(handle, _scenario_loader(path.parent, confined))
    assert isinstance(data, dict), "a scenario file's top level is a mapping"
    return data


def parse_scenario_yaml(text: str, *, base_dir: Path | None = None) -> dict[str, Any]:
    yaml = _yaml()
    where = base_dir or EVALS
    data = yaml.load(text, _scenario_loader(where, where))
    assert isinstance(data, dict)
    return data


# ------------------------------------------------------------------------------------ F5 calls


def declarations(**over: Any) -> Any:
    """``pipecat_evals.F5Declarations`` with test values (a fresh persona id each call)."""
    base: dict[str, Any] = {
        "language": "en",
        "persona": {"id": api("ids").PersonaId.new(), "version": 1},
        "metric_languages": ["en"],
        "script_sensitive": False,
    }
    base.update(over)
    return mapping("pipecat_evals").F5Declarations(**base)


def declared_root(decl: Any) -> dict[str, Any]:
    """The ``declared`` half of the F5 source root: what ``user_declared`` pointers address."""
    persona = decl.persona
    return {
        "language": decl.language,
        "persona": {"id": str(persona["id"]), "version": persona["version"]},
        "metric_languages": decl.metric_languages,
        "script_sensitive": decl.script_sensitive,
        "instructions": decl.instructions,
        "expected_outcome": decl.expected_outcome,
        "max_duration_ms": decl.max_duration_ms,
    }


def run_f5(document: Mapping[str, Any], decl: Any = None, **over: Any) -> Any:
    kwargs: dict[str, Any] = {"created_at": CREATED_AT, "declarations": decl or declarations()}
    kwargs.update(over)
    return mapping("pipecat_evals").map_f5(document, **kwargs)


def _unescape_key(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def f5_key(pointer: str) -> str | None:
    """The generic key of a pointer into the F5 source root ``{"document": ..., "declared": ...}``.
    Pointers into ``declared`` have no key. List positions and the leading ``scenarios/<n>`` are
    dropped; below ``extra``, ``args`` and ``context`` (free-form blocks) the path ends."""
    tokens = pointer.split("#")[0][1:].split("/")
    if tokens[:1] != ["document"]:
        return None
    tokens = tokens[1:]
    if tokens[:1] == ["scenarios"] and len(tokens) > 1 and tokens[1].isdigit():
        tokens = tokens[2:]
    elif tokens == ["scenarios"]:
        return "scenarios"
    out: list[str] = []
    for token in tokens:
        if token.isdigit():
            continue
        key = _unescape_key(token)
        out.append(key)
        if key in F5_OPEN_KEYS:
            break
    return ".".join(out) if out else None


def f5_universe(document: Mapping[str, Any]) -> set[str]:
    """Every leaf key of a scenario document, in ``f5_key`` form, plus the container keys that
    hold an empty list or mapping (an empty ``calls`` is still a key the file wrote)."""
    keys: set[str] = set()
    root = {"document": dict(document)}
    for pointer, value in leaves(root):
        key = f5_key(pointer)
        if key is not None:
            keys.add(key)
        del value
    _empty_containers(dict(document), "/document", keys)
    return keys


def _empty_containers(node: Any, pointer: str, keys: set[str]) -> None:
    if isinstance(node, dict):
        if not node and pointer != "/document":
            key = f5_key(pointer)
            if key is not None:
                keys.add(key)
        for name, child in node.items():
            token = str(name).replace("~", "~0").replace("/", "~1")
            _empty_containers(child, f"{pointer}/{token}", keys)
    elif isinstance(node, list):
        if not node:
            key = f5_key(pointer)
            if key is not None:
                keys.add(key)
        for index, child in enumerate(node):
            _empty_containers(child, f"{pointer}/{index}", keys)


# ------------------------------------------------------------------------------- the checker

_PERCENT = re.compile(r"%2[35]")


def _unescape(token: str) -> str:
    return (
        _PERCENT.sub(lambda m: "#" if m.group(0) == "%23" else "%", token)
        .replace("~1", "/")
        .replace("~0", "~")
    )


def _walk(node: Any, pointer: str) -> Any:
    if pointer == "":
        return node
    assert pointer.startswith("/"), f"not a JSON Pointer: {pointer!r}"
    for token in pointer[1:].split("/"):
        key = _unescape(token)
        if isinstance(node, list):
            node = node[int(key)]
        else:
            assert isinstance(node, dict), f"pointer {pointer!r} walks into a scalar"
            node = node[key]
    return node


def resolve_source_b11(root: Any, pointer: str) -> Any:
    """Resolve a source pointer (``ptr`` or ``ptr#ptr2#...``). A part after ``#`` parses the string
    found so far as JSON; numbers inside it are returned as ``Num`` lexemes. ``ptr#`` (an empty
    continuation) is the whole parsed JSON text."""
    parts = pointer.split("#")
    node = _walk(root, parts[0])
    for part in parts[1:]:
        assert isinstance(node, str), "'#' continues inside a JSON text"
        parsed = json.loads(node, parse_float=Num, parse_int=Num)
        node = _walk(parsed, part)
    return node


def _canonical_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _amount_canonical(args: tuple[Any, ...]) -> str:
    """The canonical decimal text of a plain decimal lexeme (no exponent, no sign)."""
    text = str(args[0])
    assert re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*)?", text), "only a plain decimal lexeme"
    return _canonical_decimal(Decimal(text))


def _float_amount(args: tuple[Any, ...]) -> str:
    return _canonical_decimal(Decimal(repr(float(args[0]))))


def _seconds_to_ms_float(args: tuple[Any, ...]) -> float:
    return float(Decimal(repr(args[0])) * 1000)


def _op_to_stage(args: tuple[Any, ...]) -> str:
    assert args[0] in ("chat", "text_completion", "generate_content")
    return "llm"


def _type_from_model(args: tuple[Any, ...]) -> str:
    assert isinstance(args[0], str), "a model attribute was present"
    assert args[0], "a model attribute was present"
    return "generation"


def _contract_usd(args: tuple[Any, ...]) -> str:
    del args
    return "USD"


def _scenario_name(args: tuple[Any, ...]) -> str:
    return f"{args[0]}/{args[1]}"


def _join_persona_goal(args: tuple[Any, ...]) -> str:
    return str(args[0]).strip() + "\n\n" + str(args[1]).strip()


def _join_user_parts(args: tuple[Any, ...]) -> str:
    return " ".join(str(part).strip() for part in args)


def _match_mode(args: tuple[Any, ...]) -> str:
    return "arguments_subset" if args and isinstance(args[0], dict) and args[0] else "name_only"


def _user_declared(args: tuple[Any, ...]) -> Any:
    assert len(args) == 1
    return args[0]


DERIVATIONS_B11: dict[str, Callable[[tuple[Any, ...]], Any] | None] = {
    **DERIVATIONS,
    "json_int_lexeme_to_int": lambda a: int(str(a[0])),
    "amount_lexeme_canonical": _amount_canonical,
    "float_amount_canonical": _float_amount,
    "contract_currency_usd": _contract_usd,
    "chat_operation_to_llm_stage": _op_to_stage,
    "observation_type_inferred_from_model": _type_from_model,
    "seconds_to_ms_float": _seconds_to_ms_float,
    "scenario_qualified_name": _scenario_name,
    "join_persona_and_goal": _join_persona_goal,
    "join_user_text_parts": _join_user_parts,
    "args_to_match_mode": _match_mode,
    "user_declared": _user_declared,
    "documented_judge_inputs": None,
    "documented_measure_table": None,
    "documented_default_judge": None,
    "documented_default_max_duration": None,
}
CITATION_DERIVATIONS_B11: frozenset[str] = CITATION_DERIVATIONS | {
    "contract_currency_usd",
    "chat_operation_to_llm_stage",
    "observation_type_inferred_from_model",
    "documented_judge_inputs",
    "documented_measure_table",
    "documented_default_judge",
    "documented_default_max_duration",
}
ENTRY_CITATION_ONLY_B11: frozenset[str] = ENTRY_CITATION_ONLY

F2_FRAMEWORK: tuple[str, ...] = (*FRAMEWORK_FIELDS, "/usage_record_ids")
F5_FRAMEWORK: tuple[str, ...] = (
    *FRAMEWORK_FIELDS,
    "/version",
    "/content_digest",
    "/evaluator_id",
    "/metric_bindings",
    "/metric",
    "/items/*/binding",
)


def _derive(name: str, sources: tuple[Any, ...], target: Any) -> tuple[bool, Any]:
    if name.startswith("constant:"):
        expected = name.removeprefix("constant:")
        return (str(target) == expected, expected)
    assert name in DERIVATIONS_B11, f"derivation {name!r} is not in the pinned set"
    fn = DERIVATIONS_B11[name]
    if fn is None:
        return (True, target)
    if name == "merged_one_billable_event":
        raise AssertionError("F2 and F5 never merge repeated quantities")
    expected_value = fn(sources)
    return (expected_value == target and type(expected_value) is type(target), expected_value)


def _same_as_source(source: Any, target: Any) -> bool:
    if isinstance(source, Num):
        if isinstance(target, str):
            return str(source) == target
        parsed = json.loads(str(source))
        return bool(parsed == target and type(parsed) is type(target))
    return bool(source == target and type(source) is type(target))


def _pattern(pointer: str) -> str:
    return "/".join("*" if t.isdigit() else t for t in pointer.split("/"))


def _is_framework(pointer: str, framework: Sequence[str]) -> bool:
    shape = _pattern(pointer)
    return any(shape == p or shape.startswith(p + "/") for p in framework)


_OPEN = frozenset({"raw", "arguments"})


def _target_pattern(record: Any, pointer: str) -> str:
    """``Class.field.path`` with ``*`` for list positions and one ``*`` for everything below an
    open object field (``raw``, ``arguments``: its keys are data, not schema) at any depth."""
    out: list[str] = []
    for token in pointer[1:].split("/"):
        out.append("*" if token.isdigit() else token)
        if token in _OPEN:
            out.append("*")
            break
    return type(record).__name__ + "." + ".".join(out)


def _records(result: Any) -> list[tuple[str, Any]]:
    return [(str(r.id), r) for r in result.records]


def _row_for(rows: Sequence[Any], target: str, derivation: str | None) -> Any | None:
    for row in rows:
        if row.target == target and row.kind != "absent" and row.derivation == derivation:
            return row
    return None


def check_no_fabrication_b11(
    result: Any, root: Any, module: Any, *, framework: Sequence[str]
) -> None:
    """The P-51 check (ADR 0018 Decision 2) for an F2 or F5 run. ``root`` is the document the
    source pointers address (F2: the OTLP document; F5: ``{"document": ..., "declared": ...}``).
    Raises ``AssertionError`` naming the record, the field and why."""
    rows = list(module.PROVENANCE_TABLE)
    by_key = {(e.record, e.field): e for e in result.trace}
    for key, record in _records(result):
        dump = record.model_dump(mode="json", exclude_none=True)
        for pointer, target in leaves(dump):
            if _is_framework(pointer, framework):
                continue
            entry = by_key.get((key, pointer))
            where = f"{type(record).__name__} {pointer}"
            assert entry is not None, f"{where} = {target!r} has no source (fabricated)"
            sources = tuple(resolve_source_b11(root, p) for p in entry.sources)
            if entry.derivation is None:
                assert len(sources) == 1, f"{where}: a verbatim copy has exactly one source"
                assert _same_as_source(sources[0], target), (
                    f"{where}: {target!r} is not the source value {sources[0]!r}"
                )
            else:
                ok, expected = _derive(entry.derivation, sources, target)
                assert ok, (
                    f"{where}: {target!r} is not what derivation {entry.derivation} gives "
                    f"({expected!r}) from {sources!r}"
                )
            row = _row_for(rows, _target_pattern(record, pointer), entry.derivation)
            assert row is not None, (
                f"{where}: no provenance-table row for {_target_pattern(record, pointer)} with "
                f"derivation {entry.derivation}"
            )
            if entry.derivation in CITATION_DERIVATIONS_B11:
                needed: tuple[Any, ...] = (entry.citation,)
                if entry.derivation not in ENTRY_CITATION_ONLY_B11:
                    needed = (entry.citation, row.citation)
                for cited in needed:
                    complete = cited is not None and bool(cited.url) and bool(cited.retrieved_at)
                    assert complete, (
                        f"{where}: derivation {entry.derivation} rests on a published contract "
                        "and must cite it (URL and retrieved_at)"
                    )
    known = {key for key, _ in _records(result)}
    stray = [e for e in result.trace if e.record not in known]
    assert not stray, f"trace entries for records that do not exist: {stray[:2]}"
    for row in rows:
        if row.derivation in CITATION_DERIVATIONS_B11 - ENTRY_CITATION_ONLY_B11:
            cited = row.citation
            assert cited is not None, f"table row {row.target} needs a citation"
            assert cited.url, f"table row {row.target} needs a citation URL"
            assert cited.retrieved_at, f"table row {row.target} needs a retrieval date"


_SURROGATES = re.compile("[\ud800-\udfff]")


def _text_of(value: Any) -> str:
    if isinstance(value, str):  # also Num: the lexeme as sent
        return value
    return json.dumps(value, ensure_ascii=False)


def check_warnings_quote_sources_b11(result: Any, root: Any) -> None:
    """A warning's ``path`` resolves in the input and its ``raw`` is the source value as sent (a
    number inside a JSON text is its lexeme; a lone surrogate is shown as U+FFFD, N82)."""
    for warning in result.warnings:
        if warning.path is None or not warning.path.startswith("/"):
            continue
        try:
            source = resolve_source_b11(root, warning.path)
        except (KeyError, IndexError, AssertionError, ValueError):
            raise AssertionError(
                f"warning {warning.code}: path {warning.path!r} does not resolve"
            ) from None
        if warning.raw is None:
            continue
        text = _SURROGATES.sub("\ufffd", _text_of(source))
        assert warning.raw == text, (
            f"warning {warning.code} at {warning.path}: raw {warning.raw!r} is not the "
            f"source value {text!r}"
        )
