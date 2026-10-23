# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 6 tests (real-sample mappings P1-29, P1-31, P1-32, P1-33 and the
no-fabrication harness of P1-35). No test of a converter here; the self-tests of the checkers are
in test_mapping_no_fabrication.py.

The converters do not exist yet. They are test-side code (ADR 0018: adapters ship in later
phases) at ``packages/evalschema/tests/support/mappings/`` and are imported lazily through
``mapping()``, so a converter that is not written yet fails the test that needs it with
``ModuleNotFoundError`` and never breaks collection. The pinned names, signatures, readings (P)
and ambiguities (M) are in docs/phase1/api-surface-mappings.md.

What is here:

- fixture loaders for the captured data under ``tests/fixtures/real/`` (read as bytes or UTF-8,
  never edited);
- builders for hand-made OTLP spans (the N64 tests build their spans, ADR 0018 Decision 3a);
- an independent JSON-Pointer / WAV-header resolver and the registry of derivations that
  re-computes every derived value from its source values (the P-51 check);
- ``check_no_fabrication()``: every non-null leaf of every output record must have a trace entry
  whose sources resolve in the input, whose derivation is declared in the converter's provenance
  table and re-computes to the stored value; warnings must quote their sources; every source key
  must be mapped, consumed or listed in the gap register.

Everything is ASCII; non-ASCII characters are built with chr() or read from fixture files.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib
import json
import re
import struct
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from ._support import FIXTURES, api

__all__ = [
    "CITATION_DERIVATIONS",
    "CREATED_AT",
    "DERIVATIONS",
    "ENTRY_CITATION_ONLY",
    "FRAMEWORK_FIELDS",
    "PIPECAT",
    "REAL",
    "SARVAM",
    "SCENARIOS",
    "WARNING_CODES",
    "SarvamCase",
    "canonical_text",
    "check_no_fabrication",
    "check_records_valid",
    "check_warnings_quote_sources",
    "coverage_gaps",
    "f1_doc",
    "f1_key",
    "f1_universe",
    "f3_universe",
    "f4_doc",
    "f4_key",
    "f4_universe",
    "f6_bytes",
    "f6_key",
    "f6_universe",
    "f7_events",
    "f7_key",
    "f7_universe",
    "generic_key",
    "leaves",
    "mapping",
    "new_run_id",
    "otlp_attr",
    "otlp_doc",
    "otlp_span",
    "privacy_synthetic",
    "resolve_source",
    "run_f1",
    "run_f3",
    "run_f4",
    "run_f6",
    "run_f7",
    "sarvam_case",
    "sarvam_case_names",
    "sarvam_root",
    "wav_fields",
    "wav_mono_16",
    "wav_stereo_16",
]

REAL = FIXTURES / "real"
PIPECAT = REAL / "pipecat-1.13.0"
SARVAM = REAL / "sarvam-2026-10-23"
SCENARIOS: tuple[str, ...] = ("en", "en_tool", "hi")
CREATED_AT = "2026-10-23T12:00:00.000000Z"

# Fields the caller supplies or the framework computes; they are not mapped from a source.
FRAMEWORK_FIELDS: tuple[str, ...] = (
    "/id",
    "/schema_version",
    "/created_at",
    "/metadata",
    "/metadata_flags",
    "/run_id",
    "/transcript_id",
    "/privacy",
    "/uri",
    "/contains_bidi_controls",
    "/segments/*/contains_bidi_controls",
    "/timeline_origin/ref",
    "/artifact_id",
)

# Derivations that rest on a published contract: the table row and the trace entry must cite it
# (URL and retrieved_at), ADR 0018 Decision 2 and Decision 3a.
CITATION_DERIVATIONS: frozenset[str] = frozenset(
    {
        "input_tokens_gross_minus_cache",
        "input_tokens_net_as_reported",
        "documented_stereo_left_user",
        "documented_stereo_right_bot",
        "documented_mono_mixed",
        "documented_start_of_turn",
        "tool_status_from_context_message",
        "merged_one_billable_event",
        "placeholder_sentinel_ignored",
        "documented_conversation_stage",
    }
)

# Derivations whose citation is supplied with the call (the user's statement of a contract), so
# the static table row carries none but every trace entry must.
ENTRY_CITATION_ONLY: frozenset[str] = frozenset({"merged_one_billable_event"})

# Warning codes the contract pins (api-surface-mappings.md section 4).
WARNING_CODES: frozenset[str] = frozenset(
    {
        "time_precision_reduced",
        "placeholder_value_ignored",
        "usage_quantities_not_merged",
        "cache_semantics_unknown",
        "usage_inconsistent",
        "tool_arguments_incomplete",
        "tool_call_conflict",
        "tool_result_placeholder",
        "tool_result_without_call",
        "async_tool_payload",
        "llm_input_unparseable",
        "interim_transcript_skipped",
        "unknown_enum_value",
        "naive_timestamp",
        "timestamp_not_parseable",
        "turn_text_missing",
        "language_label_normalized",
        "language_label_unparseable",
        "language_label_is_request_echo",
        "label_not_transcript_language",
        "empty_transcript",
        "timestamps_arrays_mismatch",
        "timeline_origin_unknown",
        "provider_error_response",
        "text_not_valid_unicode",
        "wav_format_not_supported",
        "channel_layout_not_documented",
        "body_not_json",
        "malformed_entry",
        "duplicate_turn_number",
    }
)


def mapping(name: str) -> Any:
    """Import the converter module ``tests/support/mappings/<name>.py`` lazily."""
    assert __package__, "the tests directory is a package"
    return importlib.import_module(f"{__package__}.support.mappings.{name}")


# ------------------------------------------------------------------------------ fixtures


def f1_doc(scenario: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((PIPECAT / scenario / "f1_spans.otlp.json").read_bytes())
    return doc


def f4_doc(scenario: str) -> list[dict[str, Any]]:
    doc: list[dict[str, Any]] = json.loads(
        (PIPECAT / scenario / "f4_conversation.json").read_bytes()
    )
    return doc


def f7_events(scenario: str) -> list[dict[str, Any]]:
    text = (PIPECAT / scenario / "f7_turn_events.jsonl").read_bytes().decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def f6_bytes(scenario: str) -> bytes:
    return (PIPECAT / scenario / "f6_stereo.wav").read_bytes()


@dataclass(frozen=True)
class SarvamCase:
    name: str
    request: dict[str, Any]
    body: bytes
    meta: dict[str, Any]


def sarvam_case_names() -> list[str]:
    return sorted(p.name for p in SARVAM.iterdir() if p.is_dir() and p.name != "clips")


def sarvam_case(name: str) -> SarvamCase:
    folder = SARVAM / name
    return SarvamCase(
        name=name,
        request=json.loads((folder / "request.json").read_bytes()),
        body=(folder / "response_body.json").read_bytes(),
        meta=json.loads((folder / "response_meta.json").read_bytes()),
    )


def sarvam_root(request: Mapping[str, Any], body: bytes, meta: Mapping[str, Any]) -> dict[str, Any]:
    """The document that the F3 source pointers address."""
    return {"request": dict(request), "response": json.loads(body), "meta": dict(meta)}


# ------------------------------------------------------------------------------ call helpers


def new_run_id() -> Any:
    return api("ids").id_type("run").new()


def privacy_synthetic() -> dict[str, Any]:
    return {"human_subject": False}


def run_f1(doc: Mapping[str, Any], **over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "run_id": new_run_id(),
        "created_at": CREATED_AT,
        "privacy": privacy_synthetic(),
    }
    kwargs.update(over)
    return mapping("pipecat_otel").map_f1(doc, **kwargs)


def run_f3(
    request: Mapping[str, Any], body: bytes, meta: Mapping[str, Any] | None = None, **over: Any
) -> Any:
    kwargs: dict[str, Any] = {"created_at": CREATED_AT, "privacy": privacy_synthetic()}
    kwargs.update(over)
    return mapping("sarvam_stt").map_f3(request, body, meta or {"status": 200}, **kwargs)


def run_f4(conversation: Sequence[Mapping[str, Any]], **over: Any) -> Any:
    kwargs: dict[str, Any] = {"created_at": CREATED_AT, "privacy": privacy_synthetic()}
    kwargs.update(over)
    return mapping("pipecat_conversation").map_f4(conversation, **kwargs)


def run_f7(events: Sequence[Mapping[str, Any]], **over: Any) -> Any:
    kwargs: dict[str, Any] = {"created_at": CREATED_AT, "privacy": privacy_synthetic()}
    kwargs.update(over)
    return mapping("pipecat_turn_events").map_f7(events, **kwargs)


def run_f6(wav: bytes, **over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "uri": "file:///tmp/tracer-test/f6_stereo.wav",
        "created_at": CREATED_AT,
        "privacy": privacy_synthetic(),
    }
    kwargs.update(over)
    return mapping("pipecat_audio").map_f6(wav, **kwargs)


# ------------------------------------------------------------------------------ OTLP builders

TRACE_ID = "e31c8352ead4fa04648d7a3e0b39e03d"


def otlp_attr(key: str, value: Any) -> dict[str, Any]:
    """One OTLP/JSON attribute (int64 as a string, as the captures write it)."""
    if isinstance(value, bool):
        typed: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        typed = {"intValue": str(value)}
    elif isinstance(value, float):
        typed = {"doubleValue": value}
    else:
        typed = {"stringValue": value}
    return {"key": key, "value": typed}


def otlp_span(
    name: str,
    span_id: str,
    *,
    parent: str | None = None,
    start_ns: int = 1_792_757_168_000_000_000,
    end_ns: int | None = None,
    attrs: Sequence[tuple[str, Any]] = (),
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": TRACE_ID,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + 500_000_000 if end_ns is None else end_ns),
        "attributes": [otlp_attr(k, v) for k, v in attrs],
        "events": [],
        "status": {"code": 0},
    }
    if parent is not None:
        span["parentSpanId"] = parent
    return span


def otlp_doc(spans: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """An OTLP/JSON document in the layout of the captures: stage spans under the scope
    ``pipecat``, turn and conversation spans under ``pipecat.turn``."""
    stage = [s for s in spans if s["name"] not in ("turn", "conversation")]
    turn = [s for s in spans if s["name"] in ("turn", "conversation")]
    scopes: list[dict[str, Any]] = []
    if stage:
        scopes.append({"scope": {"name": "pipecat"}, "spans": stage})
    if turn:
        scopes.append({"scope": {"name": "pipecat.turn"}, "spans": turn})
    resource = {"attributes": [otlp_attr("service.name", "tracer-test")]}
    return {"resourceSpans": [{"resource": resource, "scopeSpans": scopes}]}


# ------------------------------------------------------------------------------ WAV


def _wav(channels: int, rate: int, bits: int, tag: int, frames: int, fill: bytes) -> bytes:
    block = channels * bits // 8
    data = fill * (frames * block // len(fill))
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wav_stereo_16(left: bytes = b"\x01\x00", right: bytes = b"\x00\x00", frames: int = 8) -> bytes:
    """A 16 kHz 16-bit PCM stereo file; ``left`` and ``right`` are one 2-byte sample each."""
    data = (left + right) * frames
    fmt = struct.pack("<HHIIHH", 1, 2, 16000, 64000, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wav_mono_16(frames: int = 8) -> bytes:
    return _wav(1, 16000, 16, 1, frames, b"\x01\x00")


def wav_fields(data: bytes) -> dict[str, Any]:
    """The header fields a source pointer ``wav:<name>`` addresses, read independently of any
    converter. Missing fields are absent from the result."""
    out: dict[str, Any] = {"bytes": data}
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return out
    out["riff"] = "WAVE"
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if chunk_id == b"fmt " and pos + 8 + 16 <= len(data):
            tag, ch, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data, pos + 8)
            out.update(
                {
                    "fmt/format_tag": tag,
                    "fmt/channels": ch,
                    "fmt/sample_rate_hz": rate,
                    "fmt/byte_rate": byte_rate,
                    "fmt/block_align": align,
                    "fmt/bits_per_sample": bits,
                }
            )
        if chunk_id == b"data":
            out["data/length"] = size
        pos += 8 + size + (size & 1)
    return out


# ------------------------------------------------------------------------------ pointers


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


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


def resolve_source(root: Any, pointer: str) -> Any:
    """Resolve a source pointer. ``wav:<name>`` addresses a WAV header field of ``root`` (bytes);
    ``<ptr>#<ptr2>`` parses the string at ``<ptr>`` as JSON and continues inside it (chained)."""
    if pointer.startswith("wav:"):
        assert isinstance(root, bytes)
        return wav_fields(root)[pointer[4:]]
    parts = pointer.split("#")
    node = _walk(root, parts[0])
    for part in parts[1:]:
        assert isinstance(node, str), "'#' continues inside a JSON text"
        node = _walk(json.loads(node), part)
    return node


def leaves(value: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Scalar leaves of a JSON value with their pointers (empty containers have no leaf)."""
    if isinstance(value, dict):
        for key, item in value.items():
            token = str(key).replace("~", "~0").replace("/", "~1")
            yield from leaves(item, f"{prefix}/{token}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, f"{prefix}/{index}")
    else:
        yield prefix, value


def _pattern(pointer: str) -> str:
    return "/".join("*" if t.isdigit() else t for t in pointer.split("/"))


def _is_framework(pointer: str) -> bool:
    shape = _pattern(pointer)
    return any(shape == p or shape.startswith(p + "/") for p in FRAMEWORK_FIELDS)


# ------------------------------------------------------------------------------ derivations

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SURROGATES = re.compile("[\ud800-\udfff]")


def _decimal_of(value: Any) -> Decimal:
    return Decimal(repr(float(value)))


def _unix_nano_to_utc_us(args: tuple[Any, ...]) -> str:
    micro = int(args[0]) // 1000
    return (_EPOCH + timedelta(microseconds=micro)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ns_interval_to_ms_floor(args: tuple[Any, ...]) -> int:
    return (int(args[1]) - int(args[0])) // 1_000_000


def _seconds_to_ms_floor(args: tuple[Any, ...]) -> int:
    return int((_decimal_of(args[0]) * 1000).to_integral_value(rounding=ROUND_FLOOR))


def _float_to_decimal_string(args: tuple[Any, ...]) -> str:
    text = format(_decimal_of(args[0]), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _gross_minus_cache(args: tuple[Any, ...]) -> int:
    return int(args[0]) - sum(int(a) for a in args[1:])


def _language_label_to_tag(args: tuple[Any, ...]) -> str | None:
    tag, _ = api("language").normalize_language_label(str(args[0]).strip())
    return None if tag is None else str(tag)


def _channel_role(expected_channels: int, role: str) -> Callable[[tuple[Any, ...]], str]:
    def derive(args: tuple[Any, ...]) -> str:
        assert args == (expected_channels,), f"documented only for {expected_channels} channels"
        return role

    return derive


def _wav_codec(args: tuple[Any, ...]) -> str:
    assert args == (1, 16), "only 16-bit integer PCM is mapped"
    return "pcm_s16le"


def _provider_from_endpoint(args: tuple[Any, ...]) -> str:
    assert "api.sarvam.ai" in str(args[0])
    return "sarvam"


def _iso_to_utc_us(args: tuple[Any, ...]) -> str:
    moment = datetime.fromisoformat(str(args[0]))
    assert moment.tzinfo is not None, "a naive timestamp is never converted"
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _span_stage(args: tuple[Any, ...]) -> str:
    assert args[0] in ("stt", "llm", "tts")
    return str(args[0])


# A derivation maps the resolved source values to the value that must be stored. ``None`` means
# the value cannot be re-computed generically (an ordering or a flag); a dedicated test asserts it.
DERIVATIONS: dict[str, Callable[[tuple[Any, ...]], Any] | None] = {
    "otlp_int_to_int": lambda a: int(a[0]),
    "unix_nano_to_utc_us": _unix_nano_to_utc_us,
    "ns_interval_to_ms_floor": _ns_interval_to_ms_floor,
    "seconds_to_ms_floor": _seconds_to_ms_floor,
    "float_to_decimal_string": _float_to_decimal_string,
    "input_tokens_gross_minus_cache": _gross_minus_cache,
    "input_tokens_net_as_reported": lambda a: int(a[0]),
    "span_name_to_stage": _span_stage,
    "order_index": None,
    "parent_span_turn_number": lambda a: int(a[0]),
    "documented_stereo_left_user": _channel_role(2, "caller"),
    "documented_stereo_right_bot": _channel_role(2, "agent"),
    "documented_mono_mixed": _channel_role(1, "mixed"),
    "wav_pcm_codec": _wav_codec,
    "wav_container": lambda a: "wav" if a == ("WAVE",) else None,
    "sha256_of_bytes": lambda a: hashlib.sha256(a[0]).hexdigest(),
    "byte_length": lambda a: len(a[0]),
    "language_label_to_tag": _language_label_to_tag,
    "provider_from_endpoint_host": _provider_from_endpoint,
    "lone_surrogate_replaced": lambda a: _SURROGATES.sub("\ufffd", str(a[0])),
    "documented_start_of_turn": lambda a: "start",
    "documented_conversation_stage": lambda a: "agent_context",
    "iso_offset_to_utc_us": _iso_to_utc_us,
    "tool_status_from_context_message": None,
    "source_is_json_object": None,
    "merged_one_billable_event": lambda a: sum(int(x) for x in a),
    "placeholder_sentinel_ignored": None,
    "json_object_from_text": None,
}

# Constants fixed by the mapping itself ("constant:<value>" derivations) are compared literally.


def _derive(name: str, sources: tuple[Any, ...], target: Any) -> tuple[bool, Any]:
    """(whether the stored value is the re-computed one, the re-computed value)."""
    if name.startswith("constant:"):
        expected = name.removeprefix("constant:")
        return (str(target) == expected, expected)
    assert name in DERIVATIONS, f"derivation {name!r} is not in the pinned set"
    fn = DERIVATIONS[name]
    if fn is None:
        return (True, target)
    expected_value = fn(sources)
    return (expected_value == target and type(expected_value) is type(target), expected_value)


# ------------------------------------------------------------------------------ source keys


def generic_key(pointer: str) -> str:
    """A pointer without the array positions, dotted: /response/timestamps/words/0 ->
    response.timestamps.words."""
    base = pointer.split("#")[0]
    return ".".join(_unescape(t) for t in base[1:].split("/") if not t.isdigit())


def f1_key(doc: Mapping[str, Any], pointer: str) -> str | None:
    """The logical F1 source key of a pointer: ``<span name>.<attribute>``, ``<span>.@<field>``,
    ``resource.<attribute>`` or ``scope.name``."""
    tokens = pointer.split("#")[0][1:].split("/")
    try:
        if tokens[:1] != ["resourceSpans"]:
            return None
        res = doc["resourceSpans"][int(tokens[1])]
        if tokens[2] == "resource":
            return "resource." + str(res["resource"]["attributes"][int(tokens[4])]["key"])
        scope = res["scopeSpans"][int(tokens[3])]
        if tokens[4] == "scope":
            return "scope." + tokens[5]
        span = scope["spans"][int(tokens[5])]
        if tokens[6] == "attributes":
            return f"{span['name']}." + str(span["attributes"][int(tokens[7])]["key"])
        return f"{span['name']}.@{tokens[6]}"
    except (IndexError, KeyError, ValueError):
        return None


def f1_universe(doc: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for rs in doc["resourceSpans"]:
        for attr in rs.get("resource", {}).get("attributes", []):
            keys.add("resource." + str(attr["key"]))
        for scope in rs["scopeSpans"]:
            keys.add("scope.name")
            for span in scope["spans"]:
                for field in span:
                    if field == "attributes":
                        keys.update(f"{span['name']}.{a['key']}" for a in span["attributes"])
                    else:
                        keys.add(f"{span['name']}.@{field}")
    return keys


def f4_key(doc: Sequence[Mapping[str, Any]], pointer: str) -> str | None:
    tokens = pointer.split("#")[0][1:].split("/")
    try:
        message = doc[int(tokens[0])]
    except (IndexError, ValueError):
        return None
    return f"{message['role']}.{tokens[1]}"


def f4_universe(doc: Sequence[Mapping[str, Any]]) -> set[str]:
    return {f"{m['role']}.{field}" for m in doc for field in m}


def f7_key(events: Sequence[Mapping[str, Any]], pointer: str) -> str | None:
    tokens = pointer.split("#")[0][1:].split("/")
    try:
        event = events[int(tokens[0])]
    except (IndexError, ValueError):
        return None
    rest = [_unescape(t) for t in tokens[1:] if not t.isdigit()]
    if rest[:1] == ["payload"]:
        return f"{event['event']}.payload." + ".".join(rest[1:])
    return f"{event['event']}.@{rest[0]}"


def f7_universe(events: Sequence[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for event in events:
        name = event["event"]
        for field in event:
            if field != "payload":
                keys.add(f"{name}.@{field}")
        for pointer, _ in leaves(event["payload"]):
            keys.add(f"{name}.payload." + ".".join(_unescape(t) for t in pointer[1:].split("/")))
    return keys


def f6_key(data: bytes, pointer: str) -> str | None:
    return "wav." + pointer.removeprefix("wav:") if pointer.startswith("wav:") else None


def f6_universe(data: bytes) -> set[str]:
    return {"wav." + key for key in wav_fields(data)}


def f3_universe(root: Mapping[str, Any]) -> set[str]:
    keys = {generic_key(p) for p, _ in leaves(dict(root))}
    # request.file.* and meta.headers.* are enumerated as their own keys by generic_key
    return keys


# ------------------------------------------------------------------------------ the checks


def _records(result: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = [(str(r.id), r) for r in result.records]
    out.extend((f"channel_map/{m.artifact_id}", m) for m in result.channel_maps)
    return out


def check_records_valid(result: Any) -> None:
    """Every record validates again from its JSON-mode dump; every transcript passes
    ``check_transcript`` with its turns and the exchanges; the dumps are I-JSON/UTF-8 clean."""
    for _, record in _records(result):
        again = type(record).model_validate(record.model_dump(mode="json"))
        assert again == record, f"{type(record).__name__} does not round-trip"
        record.model_dump_json().encode("utf-8")
    transcripts = list(result.transcripts)
    turns = list(result.turns)
    for transcript in transcripts:
        own = [t for t in turns if t.transcript_id == transcript.id]
        api("transcript").check_transcript(transcript, own, list(result.exchanges))
    assert all(t.transcript_id in {x.id for x in transcripts} for t in turns)
    ids = [str(r.id) for r in result.records]
    assert len(ids) == len(set(ids)), "two records share an id"


def _table_rows(module: Any) -> list[Any]:
    return list(module.PROVENANCE_TABLE)


def _row_for(rows: Sequence[Any], target: str, derivation: str | None) -> Any | None:
    for row in rows:
        if row.target == target and row.kind != "absent" and row.derivation == derivation:
            return row
    return None


OPEN_OBJECT_FIELDS: frozenset[str] = frozenset({"raw", "arguments"})


def _target_pattern(record: Any, pointer: str) -> str:
    """``Class.field.path``: list positions are ``*``; everything below an open object field
    (``raw``, ``arguments``: keys are data, not schema) collapses to one ``*``."""
    out: list[str] = []
    for token in pointer[1:].split("/"):
        out.append("*" if token.isdigit() else token)
        if token in OPEN_OBJECT_FIELDS and len(out) == 1:
            out.append("*")
            break
    return type(record).__name__ + "." + ".".join(out)


def check_no_fabrication(
    result: Any, root: Any, module: Any, *, allow_declared_language: bool = False
) -> None:
    """The P-51 check (ADR 0018 Decision 2, gate item 13) for one mapping run.

    ``root`` is the document the source pointers address; ``module`` is the converter module with
    its ``PROVENANCE_TABLE``. Raises ``AssertionError`` naming the record, the field and why.
    """
    rows = _table_rows(module)
    by_key: dict[tuple[str, str], Any] = {}
    for entry in result.trace:
        by_key[(entry.record, entry.field)] = entry
    for key, record in _records(result):
        dump = record.model_dump(mode="json", exclude_none=True)
        for pointer, target in leaves(dump):
            if _is_framework(pointer):
                continue
            if pointer.startswith("/declared_language"):
                assert allow_declared_language, "declared_language needs a user declaration"
                continue
            entry = by_key.get((key, pointer))
            where = f"{type(record).__name__} {pointer}"
            assert entry is not None, f"{where} = {target!r} has no source (fabricated)"
            sources = tuple(resolve_source(root, p) for p in entry.sources)
            if entry.derivation is None:
                assert len(sources) == 1, f"{where}: a verbatim copy has exactly one source"
                same = sources[0] == target and type(sources[0]) is type(target)
                assert same, f"{where}: {target!r} is not the source value {sources[0]!r}"
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
            if entry.derivation in CITATION_DERIVATIONS:
                needed: tuple[Any, ...] = (entry.citation,)
                if entry.derivation not in ENTRY_CITATION_ONLY:
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
        if row.derivation in CITATION_DERIVATIONS - ENTRY_CITATION_ONLY:
            assert row.citation is not None, (
                f"table row {row.target}: derivation {row.derivation} needs a complete citation"
            )
            assert row.citation.url, (
                f"table row {row.target}: derivation {row.derivation} needs a complete citation"
            )
            assert row.citation.retrieved_at, (
                f"table row {row.target}: derivation {row.derivation} needs a complete citation"
            )


def canonical_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def check_warnings_quote_sources(result: Any, root: Any) -> None:
    """A warning's ``path`` resolves in the input and its ``raw`` is the source value as sent
    (with the lone-surrogate replacement as the one allowed exception, N82)."""
    for warning in result.warnings:
        if warning.path is None or not warning.path.startswith(("/", "wav:")):
            continue
        try:
            source = resolve_source(root, warning.path)
        except (KeyError, IndexError, AssertionError, ValueError):
            raise AssertionError(
                f"warning {warning.code}: path {warning.path!r} does not resolve"
            ) from None
        if warning.raw is None:
            continue
        text = _SURROGATES.sub("\ufffd", canonical_text(source))  # a lone surrogate is never stored
        assert warning.raw == text, (
            f"warning {warning.code} at {warning.path}: raw {warning.raw!r} is not the "
            f"source value {text!r}"
        )


def coverage_gaps(
    result: Any,
    universe: Iterable[str],
    key_of: Callable[[str], str | None],
    register: Iterable[Any],
    fixture: str,
) -> list[str]:
    """Source keys that are neither mapped (trace), consumed, quoted by a warning, an external
    reference, nor listed in the gap register. A list of ``<fixture>:<key>``."""
    seen: set[str] = set()
    pointers: list[str] = []
    for entry in result.trace:
        pointers.extend(entry.sources)
    pointers.extend(result.consumed)
    pointers.extend(result.external_ref_sources)
    pointers.extend(w.path for w in result.warnings if w.path is not None)
    for pointer in pointers:
        key = key_of(pointer)
        if key is not None:
            seen.add(key)
    patterns = [row.source_key for row in register if row.source_key.startswith(fixture + ":")]
    out = []
    for key in sorted(set(universe) - seen):
        label = f"{fixture}:{key}"
        if not any(fnmatch.fnmatchcase(label, p) for p in patterns):
            out.append(label)
    return out
