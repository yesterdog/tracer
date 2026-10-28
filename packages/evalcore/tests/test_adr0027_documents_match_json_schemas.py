# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``test_adr0027_documents_match_json_schemas`` (Phase 4 batch 0, task P4-00b).

ADR 0027 gives three JSON Schema blocks (draft 2020-12): ``tracer.sim_profile/1``,
``tracer.sim_outcome/1`` and ``tracer.timing/1``. This test reads them from the ADR and checks:

* each block is valid under ``Draft202012Validator.check_schema``;
* the structural promises the ADR makes about all of them (the title and ``$schema`` pinned, every
  object closed with ``additionalProperties: false``, every integer bounded by ``2**53 - 1``, no
  ``const`` because single-member sets are written as ``enum``, ``required`` inside
  ``properties``);
* the enumerations that other ADRs also state agree (the dialects of ADR 0026 rule 5, the end
  reasons of ADR 0026 rule 9, the caller modes and the sentinel of ADR 0028, the caps);
* documents that satisfy the prose cross-field rules of the ADR validate, and each of a long list
  of violations is refused;
* the checks have teeth: broken copies of the ADR text (a broken schema, a dropped
  ``additionalProperties``, a bound over ``2**53 - 1``, a missing block, a changed title) are
  refused by the same function that passes the real text.

The ADR gives no example documents, so the documents here are built from its cross-field rules with
invented values. The comparison with the schemas ``tracer-evalcore`` ships ("the two" in the ADR)
is added by the task that ships them (contract section 6); there is nothing to compare today.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import (  # type: ignore[import-untyped,unused-ignore]
    Draft202012Validator,
    SchemaError,
)

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

MAX_INT = 2**53 - 1
SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
HEADINGS = {
    "tracer.sim_profile/1": "## The `tracer.sim_profile/1` format",
    "tracer.sim_outcome/1": "## The `tracer.sim_outcome/1` format",
    "tracer.timing/1": "## The `tracer.timing/1` format",
}
DIALECTS = ["pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1"]
END_REASONS = [
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
]
STATES = [
    "preflight",
    "connecting",
    "connected",
    "caller_speaking",
    "awaiting_agent",
    "agent_speaking",
    "ending",
    "ended",
]


# ----------------------------------------------------------------------------------------------
# Reading the ADR
# ----------------------------------------------------------------------------------------------


def extract_schemas(text: str) -> dict[str, dict[str, Any]]:
    """The three schemas of the ADR text, by title; raises ``AssertionError`` on any defect."""
    schemas: dict[str, dict[str, Any]] = {}
    for title, heading in HEADINGS.items():
        section = sup.heading_section(text, heading)
        blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
        assert len(blocks) == 1, f"{title}: {len(blocks)} json blocks, expected 1"
        try:
            schema = json.loads(blocks[0])
        except json.JSONDecodeError as error:
            raise AssertionError(f"{title}: the block is not JSON: {error}") from error
        assert isinstance(schema, dict), title
        schemas[title] = schema
    return schemas


def _walk(node: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        found.append((path, node))
        for key, value in node.items():
            found += _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _walk(value, f"{path}[{index}]")
    return found


def _types(node: dict[str, Any]) -> list[str]:
    raw = node.get("type")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


def schema_problems(title: str, schema: dict[str, Any]) -> list[str]:
    """Every defect of one schema against the promises of ADR 0027; empty means none."""
    problems: list[str] = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return [f"{title}: not a valid draft 2020-12 schema: {error.message}"]
    if schema.get("title") != title:
        problems.append(f"{title}: title is {schema.get('title')!r}")
    if schema.get("$schema") != SCHEMA_URI:
        problems.append(f"{title}: $schema is {schema.get('$schema')!r}")
    if "$id" in schema and not (isinstance(schema["$id"], str) and schema["$id"]):
        problems.append(f"{title}: $id is present and not a non-empty string")
    if schema.get("type") != "object":
        problems.append(f"{title}: the document is not an object")
    for path, node in _walk(schema):
        if "const" in node:
            problems.append(f"{title} {path}: const (single-member sets are enum, ADR 0003 D6)")
        types = _types(node)
        if "object" in types:
            if node.get("additionalProperties") is not False:
                problems.append(f"{title} {path}: object not closed (additionalProperties)")
            props = node.get("properties", {})
            missing = set(node.get("required", [])) - set(props)
            if missing:
                problems.append(
                    f"{title} {path}: required keys {sorted(missing)} not in properties"
                )
            if "required" not in node and props:
                problems.append(f"{title} {path}: properties without required")
        if "integer" in types and "enum" not in node:
            bound = node.get("maximum")
            if not isinstance(bound, int) or bound > MAX_INT:
                problems.append(f"{title} {path}: integer without maximum <= 2**53 - 1")
            if not isinstance(node.get("minimum"), int):
                problems.append(f"{title} {path}: integer without minimum")
        if "string" in types and "pattern" in node:
            try:
                re.compile(node["pattern"])
            except re.error:
                problems.append(f"{title} {path}: pattern does not compile")
    return problems


def _enum(schema: dict[str, Any], *path: str) -> list[Any]:
    node: Any = schema
    for key in path:
        node = node[key] if key.isdigit() is False else node[int(key)]
    return list(node["enum"])


def cross_document_problems(
    schemas: dict[str, dict[str, Any]], adr26: str, adr28: str
) -> list[str]:
    """The enumerations that two documents (or two ADRs) both state must agree."""
    problems: list[str] = []
    profile = schemas["tracer.sim_profile/1"]["properties"]
    outcome = schemas["tracer.sim_outcome/1"]["properties"]
    timing = schemas["tracer.timing/1"]["properties"]
    for title, props in (("sim_profile", profile), ("sim_outcome", outcome), ("timing", timing)):
        if props["dialect"]["enum"] != DIALECTS:
            problems.append(f"{title}: dialect enum is {props['dialect']['enum']}")
    rule5 = _rule(adr26, 5)
    for dialect in DIALECTS:
        if f"`{dialect}`" not in rule5:
            problems.append(f"ADR 0026 rule 5 does not name `{dialect}`")
    if outcome["end_reason"]["enum"] != END_REASONS:
        problems.append("sim_outcome: end_reason enum is not the twelve reasons in order")
    rule9 = _rule(adr26, 9)
    for reason in END_REASONS:
        if f"`{reason}`" not in rule9:
            problems.append(f"ADR 0026 rule 9 does not name the end reason `{reason}`")
    for state in STATES:
        if f"`{state}`" not in rule9:
            problems.append(f"ADR 0026 rule 9 does not name the state `{state}`")
    limits = profile["limits"]["properties"]
    caps = {k.removeprefix("max_") for k in limits if k.startswith("max_")}
    reached = {v for v in outcome["cap_reached"]["enum"] if v is not None}
    if caps != reached:
        problems.append(f"caps {sorted(caps)} and cap_reached {sorted(reached)} differ")
    if None not in outcome["cap_reached"]["enum"]:
        problems.append("sim_outcome: cap_reached cannot be null")
    modes = {branch["properties"]["mode"]["enum"][0] for branch in profile["caller"]["oneOf"]}
    if modes != {"scripted", "llm", "replay"}:
        problems.append(f"sim_profile: caller modes are {sorted(modes)}")
    if set(outcome["caller_text_origin"]["enum"]) != modes:
        problems.append("sim_outcome: caller_text_origin differs from the caller modes")
    sentinels = {
        branch["properties"]["end_sentinel"]["enum"][0]
        for branch in profile["caller"]["oneOf"]
        if "end_sentinel" in branch["properties"]
    }
    if sentinels != {"END_CALL"} or "`END_CALL`" not in _rule(adr28, 7):
        problems.append("the end sentinel is not END_CALL in both ADR 0027 and ADR 0028 rule 7")
    if profile["audio"]["properties"]["frame_ms"]["enum"] != [20]:
        problems.append("sim_profile: frame_ms is not 20")
    if timing["caller_frame_ms"]["enum"] != [20]:
        problems.append("timing: caller_frame_ms is not 20")
    line_cap = profile["caller"]["oneOf"][1]["properties"]["max_line_chars"]["maximum"]
    if line_cap != 2500 or "2,500" not in _rule(adr28, 10):
        problems.append("max_line_chars maximum is not 2,500 in both ADR 0027 and ADR 0028 rule 10")
    if profile["voice"]["properties"]["language_code"]["enum"] != ["mr-IN"]:
        problems.append("sim_profile: voice.language_code is not mr-IN only")
    return problems


def _rule(adr_text: str, number: int) -> str:
    rules = sup.numbered(sup.heading_section(adr_text, "## Decision"), r"(\d+)\. \*\*")
    for label, body in rules:
        if label == str(number):
            return str(body)
    raise AssertionError(f"rule {number} is missing")


def all_problems(text: str, adr26: str, adr28: str) -> list[str]:
    try:
        schemas = extract_schemas(text)
    except AssertionError as error:
        return [str(error)]
    problems: list[str] = []
    for title, schema in schemas.items():
        problems += schema_problems(title, schema)
    if problems:
        return problems
    try:
        return cross_document_problems(schemas, adr26, adr28)
    except (AssertionError, KeyError, IndexError) as error:
        return [f"cross-document check could not read the schemas: {error!r}"]


def _texts() -> tuple[str, str, str]:
    return (
        sup.ADR27.read_text("utf-8"),
        sup.ADR26.read_text("utf-8"),
        sup.ADR28.read_text("utf-8"),
    )


# ----------------------------------------------------------------------------------------------
# Documents built from the ADR's cross-field rules (invented values)
# ----------------------------------------------------------------------------------------------


_SCHEMAS: dict[str, dict[str, Any]] = {}


def _schema(title: str) -> dict[str, Any]:
    """The schema of ``title`` as the ADR gives it now (never hard-coded here)."""
    if not _SCHEMAS:
        _SCHEMAS.update(extract_schemas(sup.ADR27.read_text("utf-8")))
    return _SCHEMAS[title]


def _synth(node: dict[str, Any]) -> Any:
    """A minimal valid value for a subschema the example documents below do not know."""
    types = _types(node)
    if "enum" in node:
        return node["enum"][0]
    if "null" in types:
        return None
    if "object" in types:
        return {k: _synth(node["properties"][k]) for k in node.get("required", [])}
    if "array" in types:
        return []
    if "boolean" in types:
        return False
    if "integer" in types:
        return node.get("minimum", 0)
    if "string" in types:
        for candidate in ("a", "A_B", "a.b", "0" * 64, "https://example.org"):
            if re.search(node.get("pattern", ""), candidate):
                return candidate
    raise AssertionError(f"cannot synthesise a value for {node}: extend the examples")


def _complete(
    title: str, doc: dict[str, Any], node: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Add every key the ADR's schema requires and the example lacks (a new required key of the
    ADR then changes no example by hand, and the examples still validate)."""
    node = _schema(title) if node is None else node
    props = node.get("properties", {})
    for key in node.get("required", []):
        if key not in doc:
            doc[key] = _synth(props[key])
        elif isinstance(doc[key], dict) and "object" in _types(props[key]):
            _complete(title, doc[key], props[key])
    return doc


def profile_scripted() -> dict[str, Any]:
    return _complete("tracer.sim_profile/1", _profile_scripted())


def _profile_scripted() -> dict[str, Any]:
    return {
        "format": "tracer.sim_profile/1",
        "dialect": "pipecat-protobuf/1",
        "audio": {
            "sample_rate_hz": 16000,
            "channels": 1,
            "frame_ms": 20,
            "wire_codec": "pcm_s16le",
        },
        "turn_taking": {
            "caller_speaks_first": False,
            "wait_for_agent_silence_ms": 800,
            "max_silence_ms": 5000,
        },
        "barge_ins": [{"turn_index": 1, "barge_in_after_ms": 300}],
        "caller": {"mode": "scripted"},
        "voice": {
            "provider": "fake",
            "model": "fake-tts",
            "speaker": "fake-speaker",
            "language_code": "mr-IN",
            "pace": "1",
            "sample_rate_hz": 16000,
            "codec": "linear16",
        },
        "stt": {"provider": "fake", "model": "fake-stt", "max_chunk_ms": 30000},
        "limits": {
            "max_call_seconds": None,
            "max_tts_chars": None,
            "max_llm_tokens": None,
            "max_stt_seconds": None,
            "min_call_interval_ms": 0,
        },
    }


def profile_twilio_llm() -> dict[str, Any]:
    doc = profile_scripted()
    doc["dialect"] = "twilio-media-stream/1"
    doc["audio"].update(sample_rate_hz=8000, wire_codec="mulaw")
    doc["voice"].update(provider="sarvam", sample_rate_hz=8000, codec="mulaw", pace="0.85")
    doc["barge_ins"] = []
    doc["caller"] = {
        "mode": "llm",
        "provider": "sarvam",
        "model": "example-model",
        "max_tokens": 200,
        "seed": None,
        "reasoning_effort": "low",
        "max_line_chars": 400,
        "max_turns": 20,
        "max_regenerations": 1,
        "end_sentinel": "END_CALL",
        "prompt": {
            "template_id": "tracer.caller_prompt/1",
            "template_sha256": "0" * 64,
            "agent_text_max_chars": 1000,
        },
    }
    doc["limits"].update(
        max_call_seconds=300, max_tts_chars=2000, max_llm_tokens=4000, max_stt_seconds=600
    )
    return doc


def profile_webrtc_replay() -> dict[str, Any]:
    doc = profile_scripted()
    doc["dialect"] = "smallwebrtc/1"
    doc["audio"].update(sample_rate_hz=48000, wire_codec="webrtc_negotiated")
    doc["voice"].update(sample_rate_hz=48000, codec="linear16", pace="1.25")
    doc["barge_ins"] = []
    doc["caller"] = {
        "mode": "replay",
        "max_line_chars": 2500,
        "max_turns": 5,
        "end_sentinel": "END_CALL",
    }
    return doc


def outcome_ok() -> dict[str, Any]:
    return _complete("tracer.sim_outcome/1", _outcome_ok())


def _outcome_ok() -> dict[str, Any]:
    return {
        "format": "tracer.sim_outcome/1",
        "dialect": "pipecat-protobuf/1",
        "outcome": "not_evaluated",
        "call_established": True,
        "end_reason": "script_complete",
        "cap_reached": None,
        "started_at_clock": "simulator_wall_clock",
        "connected_ms": 12345,
        "authorisation_reference_sha256": "a" * 64,
        "caller_text_origin": "scripted",
        "counts": {
            "caller_turns_started": 3,
            "caller_turns_completed": 3,
            "agent_segments": 3,
            "barge_ins_fired": 0,
            "brain_requests": 0,
            "brain_invalid_outputs": 0,
            "brain_regenerations": 0,
            "tts_requests": 3,
            "tts_cache_hits": 0,
            "llm_requests": 0,
            "stt_requests": 3,
            "frames_sent": 600,
            "frames_received": 640,
            "inbound_messages_ignored": 2,
            "signals_received": 0,
        },
    }


def outcome_setup_failed() -> dict[str, Any]:
    doc = outcome_ok()
    doc.update(call_established=False, end_reason="setup_failed", connected_ms=None)
    doc["counts"].update(caller_turns_started=0, caller_turns_completed=0, agent_segments=0)
    return doc


def outcome_spend_cap() -> dict[str, Any]:
    doc = outcome_ok()
    doc.update(end_reason="spend_cap", cap_reached="tts_chars", caller_text_origin="llm")
    return doc


def timing_ok() -> dict[str, Any]:
    return _complete("tracer.timing/1", _timing_ok())


def _timing_ok() -> dict[str, Any]:
    turn = {
        "turn_seq": 0,
        "gap_ms": 900,
        "overlap_ms": 0,
        "barge_in_after_ms": None,
        "barge_in_stop_ms": None,
        "self": {
            "reply_ms": None,
            "wait_ms": None,
            "stt_ms": None,
            "brain_ms": None,
            "tts_ms": None,
        },
    }
    return {
        "format": "tracer.timing/1",
        "dialect": "twilio-media-stream/1",
        "clock": "simulator_monotonic",
        "quantum_ms": 20,
        "caller_frame_ms": 20,
        "setup_ms": 150,
        "first_agent_audio_ms": 1200,
        "overlap_ms": 0,
        "answered_caller_turns": 1,
        "gap_median_ms": 900,
        "gap_n": 1,
        "barge_in_stop_median_ms": None,
        "barge_in_n": 0,
        "turns": [turn],
    }


def timing_never_connected() -> dict[str, Any]:
    doc = timing_ok()
    doc.update(
        setup_ms=None,
        first_agent_audio_ms=None,
        overlap_ms=None,
        answered_caller_turns=0,
        gap_median_ms=None,
        gap_n=0,
        turns=[],
    )
    return doc


GOOD: dict[str, list[Callable[[], dict[str, Any]]]] = {
    "tracer.sim_profile/1": [profile_scripted, profile_twilio_llm, profile_webrtc_replay],
    "tracer.sim_outcome/1": [outcome_ok, outcome_setup_failed, outcome_spend_cap],
    "tracer.timing/1": [timing_ok, timing_never_connected],
}

Change = Callable[[dict[str, Any]], object]
BAD: dict[str, dict[str, tuple[Callable[[], dict[str, Any]], Change]]] = {
    "tracer.sim_profile/1": {
        "unknown top-level key": (profile_scripted, lambda d: d.update(extra=1)),
        "missing limits": (profile_scripted, lambda d: d.pop("limits")),
        "missing barge_ins": (profile_scripted, lambda d: d.pop("barge_ins")),
        "wrong format": (profile_scripted, lambda d: d.update(format="tracer.sim_profile/2")),
        "unknown dialect": (profile_scripted, lambda d: d.update(dialect="twilio-media-stream/2")),
        "stereo wire": (profile_scripted, lambda d: d["audio"].update(channels=2)),
        "frame 10 ms": (profile_scripted, lambda d: d["audio"].update(frame_ms=10)),
        "rate below 8000": (profile_scripted, lambda d: d["audio"].update(sample_rate_hz=7999)),
        "rate above 48000": (profile_scripted, lambda d: d["audio"].update(sample_rate_hz=48001)),
        "unknown wire codec": (profile_scripted, lambda d: d["audio"].update(wire_codec="alaw")),
        "silence zero": (profile_scripted, lambda d: d["turn_taking"].update(max_silence_ms=0)),
        "silence is a string": (
            profile_scripted,
            lambda d: d["turn_taking"].update(wait_for_agent_silence_ms="800"),
        ),
        "silence over 2**53-1": (
            profile_scripted,
            lambda d: d["turn_taking"].update(max_silence_ms=2**53),
        ),
        "speaks_first is a string": (
            profile_scripted,
            lambda d: d["turn_taking"].update(caller_speaks_first="false"),
        ),
        "barge-in extra key": (profile_scripted, lambda d: d["barge_ins"][0].update(text="x")),
        "barge-in negative": (profile_scripted, lambda d: d["barge_ins"][0].update(turn_index=-1)),
        "unknown caller mode": (profile_scripted, lambda d: d["caller"].update(mode="tools")),
        "scripted with a model": (profile_scripted, lambda d: d["caller"].update(model="m")),
        "llm without a seed key": (profile_twilio_llm, lambda d: d["caller"].pop("seed")),
        "llm without reasoning_effort key": (
            profile_twilio_llm,
            lambda d: d["caller"].pop("reasoning_effort"),
        ),
        "llm line cap 2501": (
            profile_twilio_llm,
            lambda d: d["caller"].update(max_line_chars=2501),
        ),
        "llm regenerations 2": (
            profile_twilio_llm,
            lambda d: d["caller"].update(max_regenerations=2),
        ),
        "llm sentinel other": (
            profile_twilio_llm,
            lambda d: d["caller"].update(end_sentinel="BYE"),
        ),
        "llm model with space": (profile_twilio_llm, lambda d: d["caller"].update(model="a b")),
        "llm template id pattern": (
            profile_twilio_llm,
            lambda d: d["caller"]["prompt"].update(template_id="tracer.caller_prompt/0"),
        ),
        "llm template hash upper case": (
            profile_twilio_llm,
            lambda d: d["caller"]["prompt"].update(template_sha256="A" * 64),
        ),
        "llm template hash short": (
            profile_twilio_llm,
            lambda d: d["caller"]["prompt"].update(template_sha256="0" * 63),
        ),
        "llm without prompt": (profile_twilio_llm, lambda d: d["caller"].pop("prompt")),
        "replay with a model": (profile_webrtc_replay, lambda d: d["caller"].update(model="m")),
        "replay without sentinel": (
            profile_webrtc_replay,
            lambda d: d["caller"].pop("end_sentinel"),
        ),
        "voice language other than mr-IN": (
            profile_scripted,
            lambda d: d["voice"].update(language_code="hi-IN"),
        ),
        "voice codec alaw": (profile_scripted, lambda d: d["voice"].update(codec="alaw")),
        "pace trailing zero": (profile_scripted, lambda d: d["voice"].update(pace="1.50")),
        "pace is a number": (profile_scripted, lambda d: d["voice"].update(pace=1.5)),
        "pace leading zero": (profile_scripted, lambda d: d["voice"].update(pace="01")),
        "speaker with slash space": (profile_scripted, lambda d: d["voice"].update(speaker="a b")),
        "stt without chunk": (profile_scripted, lambda d: d["stt"].pop("max_chunk_ms")),
        "limits unknown key": (profile_scripted, lambda d: d["limits"].update(max_llm_calls=1)),
        "limits missing interval": (
            profile_scripted,
            lambda d: d["limits"].pop("min_call_interval_ms"),
        ),
        "interval null": (
            profile_scripted,
            lambda d: d["limits"].update(min_call_interval_ms=None),
        ),
        "call seconds zero": (profile_scripted, lambda d: d["limits"].update(max_call_seconds=0)),
        "call seconds over four hours": (
            profile_scripted,
            lambda d: d["limits"].update(max_call_seconds=14401),
        ),
        "tts chars float": (profile_scripted, lambda d: d["limits"].update(max_tts_chars=1.5)),
    },
    "tracer.sim_outcome/1": {
        "unknown top-level key": (outcome_ok, lambda d: d.update(verdict="pass")),
        "outcome other than not_evaluated": (outcome_ok, lambda d: d.update(outcome="passed")),
        "missing end_reason": (outcome_ok, lambda d: d.pop("end_reason")),
        "unknown end_reason": (outcome_ok, lambda d: d.update(end_reason="agent_busy")),
        "end_reason is a list": (outcome_ok, lambda d: d.update(end_reason=["max_turns"])),
        "unknown cap": (outcome_spend_cap, lambda d: d.update(cap_reached="usd")),
        "cap missing": (outcome_spend_cap, lambda d: d.pop("cap_reached")),
        "clock other": (outcome_ok, lambda d: d.update(started_at_clock="utc")),
        "connected negative": (outcome_ok, lambda d: d.update(connected_ms=-1)),
        "connected float": (outcome_ok, lambda d: d.update(connected_ms=1.5)),
        "hash upper case": (
            outcome_ok,
            lambda d: d.update(authorisation_reference_sha256="A" * 64),
        ),
        "hash with text": (
            outcome_ok,
            lambda d: d.update(authorisation_reference_sha256="my reference"),
        ),
        "origin other": (outcome_ok, lambda d: d.update(caller_text_origin="human")),
        "established is a string": (outcome_ok, lambda d: d.update(call_established="yes")),
        "counts extra key": (outcome_ok, lambda d: d["counts"].update(pass_rate=1)),
        "counts missing key": (outcome_ok, lambda d: d["counts"].pop("signals_received")),
        "counts negative": (outcome_ok, lambda d: d["counts"].update(frames_sent=-1)),
        "counts over 2**53-1": (outcome_ok, lambda d: d["counts"].update(frames_sent=2**53)),
        "counts is a string": (outcome_ok, lambda d: d["counts"].update(frames_sent="600")),
    },
    "tracer.timing/1": {
        "unknown top-level key": (timing_ok, lambda d: d.update(latency_ms=1)),
        "missing quantum": (timing_ok, lambda d: d.pop("quantum_ms")),
        "quantum zero": (timing_ok, lambda d: d.update(quantum_ms=0)),
        "quantum is a float": (timing_ok, lambda d: d.update(quantum_ms=20.0 + 0.5)),
        "clock other": (timing_ok, lambda d: d.update(clock="wall")),
        "frame 10": (timing_ok, lambda d: d.update(caller_frame_ms=10)),
        "unknown dialect": (timing_ok, lambda d: d.update(dialect="sip/1")),
        "gap negative": (timing_ok, lambda d: d.update(gap_median_ms=-1)),
        "gap is a string": (timing_ok, lambda d: d.update(gap_median_ms="900")),
        "gap_n missing": (timing_ok, lambda d: d.pop("gap_n")),
        "turn extra key": (timing_ok, lambda d: d["turns"][0].update(text="x")),
        "turn missing self": (timing_ok, lambda d: d["turns"][0].pop("self")),
        "turn overlap null": (timing_ok, lambda d: d["turns"][0].update(overlap_ms=None)),
        "turn seq negative": (timing_ok, lambda d: d["turns"][0].update(turn_seq=-1)),
        "self extra key": (timing_ok, lambda d: d["turns"][0]["self"].update(total_ms=1)),
        "self missing key": (timing_ok, lambda d: d["turns"][0]["self"].pop("tts_ms")),
        "self value negative": (timing_ok, lambda d: d["turns"][0]["self"].update(stt_ms=-5)),
        "turns not a list": (timing_ok, lambda d: d.update(turns={})),
    },
}


# ----------------------------------------------------------------------------------------------
# The tests
# ----------------------------------------------------------------------------------------------


def test_adr0027_documents_match_json_schemas() -> None:
    text, adr26, adr28 = _texts()
    assert all_problems(text, adr26, adr28) == []
    schemas = extract_schemas(text)
    assert list(schemas) == list(HEADINGS)
    for title, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        assert schema["title"] == title
        assert schema["$schema"] == SCHEMA_URI
        assert schema["properties"]["format"]["enum"] == [title]
        assert "$id" not in schema or isinstance(schema["$id"], str)


def test_adr0027_names_the_validator_and_the_format_of_each_document() -> None:
    text = sup.ADR27.read_text("utf-8")
    decision = sup.heading_section(text, "## Decision")
    for title in HEADINGS:
        assert f"`{title}`" in decision, title
    assert "`test_adr0027_documents_match_json_schemas`" in decision
    for title, heading in HEADINGS.items():
        section = sup.heading_section(text, heading)
        assert "Cross-field rules" in section or "cross-field" in section.lower(), title


@pytest.mark.parametrize("title", list(GOOD))
def test_the_documented_documents_validate(title: str) -> None:
    schemas = extract_schemas(sup.ADR27.read_text("utf-8"))
    validator = Draft202012Validator(schemas[title])
    for make in GOOD[title]:
        document = make()
        assert [e.message for e in validator.iter_errors(document)] == [], make.__name__
        assert document["format"] == title


@pytest.mark.parametrize("title", list(BAD))
def test_each_violation_of_the_schemas_is_refused(title: str) -> None:
    schemas = extract_schemas(sup.ADR27.read_text("utf-8"))
    validator = Draft202012Validator(schemas[title])
    for name, (make, change) in BAD[title].items():
        document = make()
        change(document)
        assert list(validator.iter_errors(document)), f"{title}: {name} was accepted"


def _required_paths(
    node: dict[str, Any], doc: Any, prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    if not isinstance(doc, dict) or "object" not in _types(node):
        return paths
    for key in node.get("required", []):
        paths.append((*prefix, key))
    for key, sub in node.get("properties", {}).items():
        if key in doc:
            paths += _required_paths(sub, doc[key], (*prefix, key))
    return paths


@pytest.mark.parametrize("title", list(GOOD))
def test_dropping_any_required_key_the_adr_names_is_refused(title: str) -> None:
    """Schema-driven: whatever the ADR requires today (for example ``connection`` of the sim
    profile) is dropped from each example in turn and the document must be refused."""
    schema = _schema(title)
    validator = Draft202012Validator(schema)
    seen = 0
    for make in GOOD[title]:
        document = make()
        for path in _required_paths(schema, document):
            broken = copy.deepcopy(document)
            target = broken
            for key in path[:-1]:
                target = target[key]
            del target[path[-1]]
            assert list(validator.iter_errors(broken)), f"{title}: dropping {path} was accepted"
            seen += 1
    assert seen >= 10, title


def test_the_examples_carry_every_key_the_adr_requires_without_being_told() -> None:
    for title, makers in GOOD.items():
        for make in makers:
            assert set(_schema(title)["required"]) <= set(make()), (title, make.__name__)


def test_the_violation_lists_change_something_and_are_not_vacuous() -> None:
    for title, cases in BAD.items():
        assert len(cases) >= 17, title
        for name, (make, change) in cases.items():
            before = copy.deepcopy(make())
            after = copy.deepcopy(before)
            change(after)
            assert before != after, f"{title}: {name} changes nothing"


def test_the_oneof_branches_are_exclusive_for_the_good_documents() -> None:
    schemas = extract_schemas(sup.ADR27.read_text("utf-8"))
    branches = schemas["tracer.sim_profile/1"]["properties"]["caller"]["oneOf"]
    assert len(branches) == 3
    for make in GOOD["tracer.sim_profile/1"]:
        caller = make()["caller"]
        matching = [b for b in branches if not list(Draft202012Validator(b).iter_errors(caller))]
        assert len(matching) == 1, (caller["mode"], len(matching))


def test_an_optional_looking_key_is_not_optional() -> None:
    """Every key of every document is required (a fixed key set: absence is never a default)."""
    schemas = extract_schemas(sup.ADR27.read_text("utf-8"))
    for title, schema in schemas.items():
        for path, node in _walk(schema):
            if "object" in _types(node) and node.get("properties"):
                assert set(node["required"]) == set(node["properties"]), f"{title} {path}"


# ---------------------------------------------------------------------- mutants of the ADR text


def _break_json(text: str) -> str:
    return text.replace('"title": "tracer.timing/1",', '"title": "tracer.timing/1"', 1)


def _unclose_first_object(text: str) -> str:
    return text.replace('  "additionalProperties": false,\n', "", 1)


def _unclose_nested_object(text: str) -> str:
    return text.replace('      "additionalProperties": false,\n', "", 1)


def _bump_maximum(text: str) -> str:
    return text.replace('"maximum": 9007199254740991', '"maximum": 9007199254740992', 1)


def _drop_maximum(text: str) -> str:
    return text.replace(
        '"type": "integer", "minimum": 1, "maximum": 9007199254740991',
        '"type": "integer", "minimum": 1',
        1,
    )


def _bad_type(text: str) -> str:
    return text.replace(
        '"type": "integer", "minimum": 8000', '"type": "intger", "minimum": 8000', 1
    )


def _retitle(text: str) -> str:
    return text.replace('"title": "tracer.sim_outcome/1"', '"title": "tracer.sim_outcome/2"', 1)


def _wrong_draft(text: str) -> str:
    return text.replace(
        "https://json-schema.org/draft/2020-12/schema",
        "https://json-schema.org/draft-07/schema#",
        1,
    )


def _use_const(text: str) -> str:
    return text.replace('"enum": ["tracer.timing/1"]', '"const": "tracer.timing/1"', 1)


def _dialect_drift(text: str) -> str:
    return text.replace(
        '"enum": ["pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1"]',
        '"enum": ["pipecat-protobuf/1", "twilio-media-stream/1"]',
        1,
    )


def _drop_end_reason(text: str) -> str:
    return text.replace('"spend_cap", "brain_failed"]', '"spend_cap"]', 1)


def _drop_cap_name(text: str) -> str:
    return text.replace('"stt_seconds", null]', "null]", 1)


def _mode_drift(text: str) -> str:
    return text.replace('"enum": ["scripted", "llm", "replay"]', '"enum": ["scripted", "llm"]', 1)


def _required_not_in_properties(text: str) -> str:
    return text.replace('"required": ["mode"],', '"required": ["mode", "ghost"],', 1)


def _drop_block(text: str) -> str:
    start = text.index("## The `tracer.timing/1` format")
    fence = text.index("```json", start)
    return text[:fence] + text[fence + len("```json") :]


def _drop_section(text: str) -> str:
    start = text.index("## The `tracer.sim_outcome/1` format")
    end = text.index("## The `tracer.timing/1` format")
    return text[:start] + text[end:]


def _duplicate_block(text: str) -> str:
    start = text.index("## The `tracer.timing/1` format")
    fence = text.index("```json", start)
    end = text.index("```\n", fence + 8) + 4
    return text[:end] + text[fence:end] + text[end:]


def _lines_cap_drift(text: str) -> str:
    return text.replace('"maximum": 2500', '"maximum": 2501', 1)


def _lang_drift(text: str) -> str:
    return text.replace('"enum": ["mr-IN"]', '"enum": ["mr-IN", "hi-IN"]', 1)


MUTANTS: dict[str, Callable[[str], str]] = {
    "invalid JSON": _break_json,
    "first object left open": _unclose_first_object,
    "nested object left open": _unclose_nested_object,
    "a maximum over 2**53 - 1": _bump_maximum,
    "an integer without a maximum": _drop_maximum,
    "a misspelt type": _bad_type,
    "a changed title": _retitle,
    "another draft": _wrong_draft,
    "const instead of enum": _use_const,
    "a dialect missing from one document": _dialect_drift,
    "an end reason missing": _drop_end_reason,
    "a cap name missing from cap_reached": _drop_cap_name,
    "a caller mode missing from the outcome": _mode_drift,
    "required names a key not in properties": _required_not_in_properties,
    "a fence opener removed": _drop_block,
    "a whole section removed": _drop_section,
    "two blocks in one section": _duplicate_block,
    "line cap drift": _lines_cap_drift,
    "voice language drift": _lang_drift,
}


@pytest.mark.parametrize("name", list(MUTANTS))
def test_the_schema_check_has_teeth(name: str) -> None:
    text, adr26, adr28 = _texts()
    mutant = MUTANTS[name](text)
    assert mutant != text, f"the mutation {name!r} changed nothing"
    assert all_problems(mutant, adr26, adr28), f"the check accepted the mutant {name!r}"


def test_the_cross_adr_check_has_teeth() -> None:
    text, adr26, adr28 = _texts()
    assert all_problems(text, adr26.replace("`smallwebrtc/1`", "`smallwebrtc/9`", 1), adr28)
    assert all_problems(text, adr26.replace("`spend_cap`", "`spend`", 1), adr28)
    assert all_problems(text, adr26, adr28.replace("`END_CALL`", "`BYE`", 1))
    assert all_problems(text, adr26, adr28.replace("2,500", "3,000", 1))
