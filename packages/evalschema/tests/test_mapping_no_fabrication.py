# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-35 and gate item 13: the no-fabrication check across every F mapping, the gap register, the
per-mapping field-provenance tables (P-51) and the provenance files of the captures.

``test_no_fabricated_values_all_mappings`` runs every converter (F1 x 3, F3 x 38, F4 x 3, F7 x 3,
F6 x 3 and a set of hand-made inputs that reach the branches the captures do not) and checks, with
the independent resolver and the derivation registry of ``_support_b6``, that every non-null field
of every output record has a source or a declared, re-computable and (when it rests on a published
contract) cited derivation; that every warning quotes its source; and that every source key of the
fixture is mapped, consumed, quoted or in the gap register. The checker itself is tested at the
end with deliberately fabricated results (those self-tests need no converter and pass today).

Deferred (not here): F2 Langfuse (P1-30, ``test_f2_*`` and
``test_documented_currency_contract_requires_citation``), F5 Pipecat Evals YAML (P1-34), F8 VoicEra
(P1-36, private fixture and public twin).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ._support import TESTS_DIR
from ._support_b2 import new_id
from ._support_b3 import make3
from ._support_b6 import (
    CITATION_DERIVATIONS,
    DERIVATIONS,
    ENTRY_CITATION_ONLY,
    PIPECAT,
    SARVAM,
    SCENARIOS,
    WARNING_CODES,
    check_no_fabrication,
    check_warnings_quote_sources,
    coverage_gaps,
    f1_doc,
    f1_key,
    f1_universe,
    f3_universe,
    f4_doc,
    f4_key,
    f4_universe,
    f6_bytes,
    f6_key,
    f6_universe,
    f7_events,
    f7_key,
    f7_universe,
    generic_key,
    leaves,
    mapping,
    otlp_doc,
    otlp_span,
    resolve_source,
    run_f1,
    run_f3,
    run_f4,
    run_f6,
    run_f7,
    sarvam_case,
    sarvam_case_names,
    sarvam_root,
    wav_fields,
    wav_mono_16,
)

MODULES = {
    "F1": "pipecat_otel",
    "F3": "sarvam_stt",
    "F4": "pipecat_conversation",
    "F6": "pipecat_audio",
    "F7": "pipecat_turn_events",
}
OWNERS = {
    "schema-architect",
    "dev",
    "indic-linguistics",
    "staff-reviewer",
    "qa",
    "security-licensing",
}
TABLE_KINDS = {"source", "derivation", "absent", "user_declared", "constant"}
GAP_CLASSES = {"blocking", "non-blocking"}
GAP_KINDS = {"unmapped", "lossy", "absent_in_source"}
RUNS_DIR = TESTS_DIR / "support" / "mappings"
GAP_FIXTURES = {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"}

Run = tuple[Any, Any, str]  # (result, root document, converter module name)


# ============================================================================ runs


def _f1_runs() -> list[tuple[str, Callable[[], Run]]]:
    def make(scenario: str) -> Callable[[], Run]:
        def run() -> Run:
            doc = f1_doc(scenario)
            return run_f1(doc), doc, MODULES["F1"]

        return run

    return [(f"F1-{s}", make(s)) for s in SCENARIOS]


def _f3_runs() -> list[tuple[str, Callable[[], Run]]]:
    def make(name: str) -> Callable[[], Run]:
        def run() -> Run:
            case = sarvam_case(name)
            result = run_f3(case.request, case.body, case.meta, audio_artifact_id=FIXED_ARTIFACT)
            return result, sarvam_root(case.request, case.body, case.meta), MODULES["F3"]

        return run

    return [(f"F3-{n}", make(n)) for n in sarvam_case_names()]


def api_art() -> str:
    return new_id("art")


FIXED_ARTIFACT = api_art()


def _f4_runs() -> list[tuple[str, Callable[[], Run]]]:
    def make(scenario: str) -> Callable[[], Run]:
        def run() -> Run:
            doc = f4_doc(scenario)
            return run_f4(doc), doc, MODULES["F4"]

        return run

    return [(f"F4-{s}", make(s)) for s in SCENARIOS]


def _f7_runs() -> list[tuple[str, Callable[[], Run]]]:
    def make(scenario: str) -> Callable[[], Run]:
        def run() -> Run:
            events = f7_events(scenario)
            return run_f7(events), events, MODULES["F7"]

        return run

    return [(f"F7-{s}", make(s)) for s in SCENARIOS]


def _f6_runs() -> list[tuple[str, Callable[[], Run]]]:
    def make(scenario: str) -> Callable[[], Run]:
        def run() -> Run:
            data = f6_bytes(scenario)
            return run_f6(data), data, MODULES["F6"]

        return run

    return [(f"F6-{s}", make(s)) for s in SCENARIOS]


def _synthetic_runs() -> list[tuple[str, Callable[[], Run]]]:
    """Hand-made inputs for the branches the captures never reach (labelled synthetic: they are
    not fixtures and prove nothing about a real source, only about the mapping's own rules)."""
    attrs_llm = [("gen_ai.provider.name", "openai"), ("gen_ai.request.model", "gpt-4.1-mini")]
    messages = json.dumps(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c-1",
                        "type": "function",
                        "function": {"name": "a", "arguments": '{"x": 1}'},
                    },
                    {
                        "id": "c-2",
                        "type": "function",
                        "function": {"name": "b", "arguments": '{"y"'},
                    },
                ],
            },
            {"role": "tool", "content": '{"ok": true}', "tool_call_id": "c-1"},
        ]
    )

    def f1_tools() -> Run:
        turn = otlp_span(
            "turn",
            "a1a1a1a1a1a1a1a1",
            attrs=[
                ("turn.number", 1),
                ("turn.duration_seconds", 2.0),
                ("turn.was_interrupted", False),
            ],
        )
        llm = otlp_span(
            "llm",
            "b1b1b1b1b1b1b1b1",
            parent="a1a1a1a1a1a1a1a1",
            start_ns=1_792_757_168_123_456_789,
            attrs=[
                *attrs_llm,
                ("input", messages),
                ("output", "hi"),
                ("gen_ai.usage.input_tokens", 100),
                ("gen_ai.usage.output_tokens", 5),
                ("gen_ai.usage.cache_read.input_tokens", 30),
                ("metrics.ttfb", 0.123456),
            ],
        )
        tts = otlp_span(
            "tts",
            "c1c1c1c1c1c1c1c1",
            parent="a1a1a1a1a1a1a1a1",
            attrs=[
                ("gen_ai.provider.name", "kokoro"),
                ("gen_ai.request.model", "k-1"),
                ("text", "hi"),
                ("metrics.character_count", 2),
            ],
        )
        doc = otlp_doc([llm, tts, turn])
        return run_f1(doc), doc, MODULES["F1"]

    def f1_merged() -> Run:
        turn = otlp_span("turn", "a1a1a1a1a1a1a1a1", attrs=[("turn.number", 1)])
        llm = otlp_span(
            "llm",
            "b1b1b1b1b1b1b1b1",
            parent="a1a1a1a1a1a1a1a1",
            attrs=[
                *attrs_llm,
                ("gen_ai.usage.output_tokens", 5),
                ("gen_ai.usage.output_tokens", 7),
            ],
        )
        doc = otlp_doc([llm, turn])
        citation = mapping("common").Citation(
            url="https://example.com/c", retrieved_at="2026-10-23", statement="one billable event"
        )
        return run_f1(doc, one_billable_event=citation), doc, MODULES["F1"]

    def f1_net() -> Run:
        llm = otlp_span(
            "llm",
            "b1b1b1b1b1b1b1b1",
            attrs=[
                ("gen_ai.provider.name", "anthropic"),
                ("gen_ai.request.model", "claude-x"),
                ("gen_ai.usage.input_tokens", 10),
                ("gen_ai.usage.cache_read.input_tokens", 4),
                ("gen_ai.usage.output_tokens", 2),
            ],
        )
        doc = otlp_doc([llm])
        return run_f1(doc), doc, MODULES["F1"]

    def f3_timestamps() -> Run:
        request = {
            "endpoint": "POST https://api.sarvam.ai/speech-to-text",
            "form_fields": {
                "language_code": "unknown",
                "model": "saaras:v3",
                "with_timestamps": "true",
            },
        }
        body = json.dumps(
            {
                "request_id": "r-9",
                "transcript": "a. b.",
                "language_code": "hi_IN",
                "language_probability": 0.5,
                "timestamps": {
                    "words": ["a.", "b."],
                    "start_time_seconds": [0.0, 1.2345],
                    "end_time_seconds": [1.2345, 2.0],
                },
            }
        ).encode()
        meta = {"status": 200}
        result = run_f3(request, body, meta, audio_artifact_id=api_art())
        return result, sarvam_root(request, body, meta), MODULES["F3"]

    def f3_surrogate() -> Run:
        request = {"endpoint": "POST https://api.sarvam.ai/speech-to-text", "form_fields": {}}
        body = b'{"request_id": "r-1", "transcript": "a\\ud800b", "language_code": "hi\\udc00IN"}'
        meta = {"status": 200}
        return run_f3(request, body, meta), sarvam_root(request, body, meta), MODULES["F3"]

    def f4_cancelled() -> Run:
        doc: list[dict[str, Any]] = [
            {"role": "user", "content": "x"},
            {
                "role": "tool_call",
                "name": "n",
                "arguments": {"a": [1, {"b": None}]},
                "cancelled": True,
            },
            {"role": "assistant", "content": "y"},
        ]
        return run_f4(doc), doc, MODULES["F4"]

    def f7_stamps() -> Run:
        events = [
            {
                "seq": 0,
                "source": "a",
                "event": "on_user_turn_stopped",
                "payload": {"message": {"content": "n", "timestamp": "2026-10-23T12:05:36.507"}},
            },
            {
                "seq": 1,
                "source": "a",
                "event": "on_user_turn_stopped",
                "payload": {
                    "message": {"content": "z", "timestamp": "2026-10-23T12:05:36.5071239+05:30"}
                },
            },
            {"seq": 2, "source": "t", "event": "on_turn_started", "payload": {"turn_number": 4}},
        ]
        return run_f7(events), events, MODULES["F7"]

    def f6_mono() -> Run:
        data = wav_mono_16()
        return run_f6(data), data, MODULES["F6"]

    return [
        (f"synthetic-{fn.__name__}", fn)
        for fn in (
            f1_tools,
            f1_merged,
            f1_net,
            f3_timestamps,
            f3_surrogate,
            f4_cancelled,
            f7_stamps,
            f6_mono,
        )
    ]


ALL_RUNS = _f1_runs() + _f3_runs() + _f4_runs() + _f7_runs() + _f6_runs() + _synthetic_runs()
RUN_IDS = [label for label, _ in ALL_RUNS]
RUN_BY_ID = dict(ALL_RUNS)


def _stable(result: Any) -> list[Any]:
    skip = {"id", "created_at", "transcript_id", "run_id"}
    return [
        (type(r).__name__, {k: v for k, v in r.model_dump(mode="json").items() if k not in skip})
        for r in result.records
    ]


# ============================================================================ the checks


@pytest.mark.parametrize("label", RUN_IDS)
def test_no_fabricated_values_all_mappings(label: str) -> None:
    """ADR 0018 Decision 2 and I1, gate item 13 (P-51): for every mapping run, each non-null field
    of each record is accounted for by a trace entry whose source resolves, whose derivation is
    declared in the converter's field-provenance table, re-computes to the stored value from
    the source values (independent registry) and, when it rests on a published contract, cites it
    (URL and retrieved_at). Warnings quote their sources (raw is the value as sent). External
    references equal the source value at their pointer. No ``declared_language`` appears (no
    declaration was supplied). The check is not skippable for a run that maps nothing."""
    result, root, module_name = RUN_BY_ID[label]()
    module = mapping(module_name)
    check_no_fabrication(result, root, module)
    check_warnings_quote_sources(result, root)
    assert len(result.external_refs) == len(result.external_ref_sources)
    for ref, pointer in zip(result.external_refs, result.external_ref_sources, strict=True):
        assert resolve_source(root, pointer) == ref.value
    for pointer in result.consumed:
        resolve_source(root, pointer)
    for transcript in result.transcripts:
        assert transcript.declared_language is None
    assert {w.code for w in result.warnings} <= WARNING_CODES, "an unpinned warning code"


@pytest.mark.parametrize("label", [i for i in RUN_IDS if not i.startswith("synthetic-")])
def test_every_source_field_mapped_or_gapped(label: str) -> None:
    """ADR 0018 Decision 4: the gap register lists every unmapped or lossy source field. For every
    captured fixture, each source key is mapped (a trace source), consumed as a decision input,
    quoted by a warning, an external reference, or has a row in the gap register. A new
    unmapped attribute fails here until it is mapped or registered with a classification."""
    result, root, _ = RUN_BY_ID[label]()
    fixture = label.split("-")[0]
    register = mapping("gap_register").GAP_REGISTER
    if fixture == "F1":
        universe = f1_universe(root)
        key_of: Callable[[str], str | None] = lambda p: f1_key(root, p)  # noqa: E731
    elif fixture == "F3":
        universe = f3_universe(root if isinstance(root, dict) else {})
        if "error" in root["response"]:
            # no transcript is produced, so the request that would qualify it is not read
            universe = {k for k in universe if not k.startswith("request.")}
        key_of = generic_key
    elif fixture == "F4":
        universe = f4_universe(root)
        key_of = lambda p: f4_key(root, p)  # noqa: E731
    elif fixture == "F7":
        universe = f7_universe(root)
        key_of = lambda p: f7_key(root, p)  # noqa: E731
    else:
        universe = f6_universe(root)
        key_of = lambda p: f6_key(root, p)  # noqa: E731
    assert coverage_gaps(result, universe, key_of, register, fixture) == []


@pytest.mark.parametrize("label", [i for i in RUN_IDS if not i.startswith("synthetic-")])
def test_mapping_is_deterministic_and_leaves_its_input_alone(label: str) -> None:
    """Two runs over the same input give the same records apart from ids and creation time, and the
    input document is unchanged (compared with a deep copy taken before)."""
    first_result, root, _ = RUN_BY_ID[label]()
    before = copy.deepcopy(root)
    second_result, root_again, _ = RUN_BY_ID[label]()
    assert root_again == before == root
    assert _stable(first_result) == _stable(second_result)
    assert [w.model_dump(mode="json") for w in first_result.warnings] == [
        w.model_dump(mode="json") for w in second_result.warnings
    ]


# ============================================================================ gap register


def test_gap_register_zero_blocking() -> None:
    """Gate item 13 and ADR 0018 I12: no row of the register is blocking. A blocking gap (a
    source field needed by an MVP-1 metric, the verdict or the cost model that cannot be
    represented without loss or a guess) is resolved by a schema change, never by a guess, so
    the gate fails while one exists."""
    register = mapping("gap_register").GAP_REGISTER
    assert len(register) > 0
    assert [row for row in register if row.classification == "blocking"] == []


def test_gap_register_rows_well_formed() -> None:
    """Each row names its fixture (F1..F8) and a source key under that fixture, a classification
    (blocking / non-blocking), a kind (unmapped / lossy / absent_in_source), a reason with text
    and an owner from the project roles; keys are unique."""
    register = mapping("gap_register").GAP_REGISTER
    keys = [row.source_key for row in register]
    assert len(keys) == len(set(keys)), "a source key is registered twice"
    for row in register:
        assert row.fixture in GAP_FIXTURES
        assert row.source_key.startswith(row.fixture + ":")
        assert len(row.source_key) > 3
        assert row.classification in GAP_CLASSES
        assert row.kind in GAP_KINDS
        assert isinstance(row.reason, str)
        assert len(row.reason.strip()) >= 20
        assert row.owner in OWNERS
        assert "/Users/" not in row.reason
        assert "private" not in row.reason.lower()


def test_known_schema_gaps_have_owners_and_are_nonblocking() -> None:
    """Gate item 2 and ADR 0018 I12: ``KNOWN_SCHEMA_GAPS`` holds exactly the non-blocking rows of
    the register, each with an owner (api-surface-mappings.md, M16)."""
    module = mapping("gap_register")
    known = module.KNOWN_SCHEMA_GAPS
    assert tuple(known) == tuple(
        r for r in module.GAP_REGISTER if r.classification == "non-blocking"
    )
    assert known
    assert all(row.owner in OWNERS and row.classification == "non-blocking" for row in known)


REQUIRED_ROWS = {
    "F1:turn.turn.user_bot_latency_seconds": "the pre-computed user-bot latency has no target (M7)",
    "F1:stt.language": "detected-or-configured language is not an observation (M2)",
    "F1:llm.gen_ai.request.top_k": "NOT_GIVEN sentinel, nothing to map",
    "F4:assistant.content": "bot turns with no speech are not in the file (absent_in_source)",
    "F4:tool_call.@position": "the position of a tool call between turns has no target",
    "F7:*.@harness_observed_at_utc": "harness clock, not Pipecat time",
    "F3:response.language_code": "a forced language is an echo and is not stored",
}


@pytest.mark.parametrize("key", sorted(REQUIRED_ROWS))
def test_gap_register_names_the_known_real_data_findings(key: str) -> None:
    """The findings the captures produced are registered by name, not found by luck of the
    coverage test (REQUIRED_ROWS gives the reason of each key)."""
    register = mapping("gap_register").GAP_REGISTER
    rows = [r for r in register if r.source_key == key]
    assert len(rows) == 1, f"{key} is not registered ({REQUIRED_ROWS[key]})"
    assert rows[0].classification in GAP_CLASSES
    if key == "F4:assistant.content":
        assert rows[0].kind == "absent_in_source"


# Rows that were registered once and are gone because the gap they named no longer exists.
RETIRED_ROWS = {
    "F1:tts.metrics.character_count": (
        "M3: UsageRecord.model is optional, so the tts character count is kept as a UsageRecord "
        "with no model and nothing is lost"
    ),
}


@pytest.mark.parametrize("key", sorted(RETIRED_ROWS))
def test_gap_register_has_no_row_for_a_gap_that_no_longer_exists(key: str) -> None:
    """A register row names data the mapping loses. When the loss is gone the row must go too:
    a stale row hides nothing but says the schema still lacks something it now has (and
    ``KNOWN_SCHEMA_GAPS`` would carry it into P1-16). Both the full register and the
    non-blocking list are checked."""
    module = mapping("gap_register")
    assert [r for r in module.GAP_REGISTER if r.source_key == key] == [], RETIRED_ROWS[key]
    assert [r for r in module.KNOWN_SCHEMA_GAPS if r.source_key == key] == []


# ============================================================================ tables


@pytest.mark.parametrize("fixture", sorted(MODULES))
def test_provenance_tables_well_formed(fixture: str) -> None:
    """P-51: each mapping publishes ``PROVENANCE_TABLE``, rows of target path
    (``Class.field.path`` with ``*`` for list positions), kind (source / derivation / absent /
    user_declared / constant), source key, derivation name and citation. Every derivation named
    is in the pinned registry or is ``constant:<value>``; a derivation row names one; targets are
    unique per (target, derivation)."""
    rows = list(mapping(MODULES[fixture]).PROVENANCE_TABLE)
    assert rows
    seen = set()
    for row in rows:
        assert row.kind in TABLE_KINDS
        assert re.fullmatch(r"[A-Z][A-Za-z]+(\.[a-z_*0-9]+)*", row.target), row.target
        if row.kind in ("derivation", "constant"):
            assert row.derivation is not None
        if row.derivation is not None:
            assert row.derivation.startswith("constant:") or row.derivation in DERIVATIONS
        assert (row.target, row.derivation, row.source) not in seen
        seen.add((row.target, row.derivation, row.source))


def test_contract_derivations_carry_complete_citations() -> None:
    """ADR 0018 Decision 2 and I13: a derivation that relies on a published contract cites it with
    a URL (https) and the date it was retrieved; an uncited contract is not a derivation. The
    rows named here are the contract-based ones (documentation-derived channel layout, cache
    semantics, the turn-start timestamp, the tool-result placeholders, the placeholder
    sentinels); the user-supplied merge statement carries its citation per call instead."""
    for fixture, name in MODULES.items():
        for row in mapping(name).PROVENANCE_TABLE:
            if row.derivation in CITATION_DERIVATIONS - ENTRY_CITATION_ONLY:
                assert row.citation is not None, f"{fixture} {row.target}"
                assert row.citation.url.startswith("https://")
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.citation.retrieved_at)
                assert row.citation.statement.strip()


# ============================================================================ adapters (all)


@pytest.mark.parametrize("fixture", ["F1", "F4", "F7"])
def test_adapter_unknown_enum_value_becomes_warning(fixture: str) -> None:
    """SCOPE 'All': a value outside the mapped set (a span name, a message role, an event name)
    is an ``unknown_enum_value`` warning that quotes the value; the rest of the input is still
    mapped and nothing raises."""
    if fixture == "F1":
        doc = otlp_doc([otlp_span("vad", "e1e1e1e1e1e1e1e1")])
        result, value = run_f1(doc), "vad"
    elif fixture == "F4":
        result, value = (
            run_f4([{"role": "robot", "content": "x"}, {"role": "user", "content": "y"}]),
            "robot",
        )
    else:
        events = [{"seq": 0, "source": "s", "event": "on_robot", "payload": {}}]
        result, value = run_f7(events), "on_robot"
    assert [w.raw for w in result.warnings if w.code == "unknown_enum_value"] == [value]


@pytest.mark.parametrize("label", ["hindi", "", " ", "h", "hi-IN-", "hi--IN", "12-34", "hi_"])
def test_adapter_malformed_language_label_recorded_as_warning(label: str) -> None:
    """SCOPE 'All', ADR 0005 I14: a provider label that is not a tag is kept verbatim in
    ``detected_language_raw``, gives no tag, and is recorded as ``language_label_unparseable``;
    the transcript text is still mapped. Names are never mapped to tags by guessing."""
    request = {"endpoint": "POST https://api.sarvam.ai/speech-to-text", "form_fields": {}}
    body = json.dumps({"request_id": "r", "transcript": "text", "language_code": label}).encode()
    result = run_f3(request, body)
    transcript = result.transcripts[0]
    assert transcript.detected_language_raw == label
    assert transcript.detected_language is None
    assert [w.code for w in result.warnings if w.code.startswith("language_label_")] == [
        "language_label_unparseable"
    ]
    assert [t.text for t in result.turns] == ["text"]


# ============================================================================ fixture files


def _provenance_files() -> list[Path]:
    return [PIPECAT / s / "provenance.json" for s in SCENARIOS] + [SARVAM / "provenance.json"]


@pytest.mark.parametrize("path", _provenance_files(), ids=lambda p: p.parent.name)
def test_provenance_json_schema_all_fixtures(path: Path) -> None:
    """ADR 0018 I11, gate item 13: every real fixture folder has a provenance.json with source,
    retrieval date (ISO date), method (captured / synthetic_from_docs / private_real, and a
    committed real capture is ``captured``), license, PII status (none for these synthetic
    voices) and the software version it came from; every file it lists exists and has the
    recorded sha256; a Hindi capture says it needs native review and is never a WER reference
    (Decision 9)."""
    data = json.loads(path.read_bytes())
    for key in ("source", "retrieval_date", "method", "license", "pii_status"):
        assert isinstance(data.get(key), str), key
        assert data[key].strip(), key
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["retrieval_date"])
    assert data["method"] == "captured"
    assert data["pii_status"].lower().startswith(("none", "synthetic"))
    folder = path.parent
    if folder.parent.name == "pipecat-1.13.0":
        assert data["software"]["packages"]["pipecat-ai"] == "1.13.0"
        assert set(data["files"]) == {
            "f1_spans.otlp.json",
            "f4_conversation.json",
            "f6_stereo.wav",
            "f7_turn_events.jsonl",
        }
        assert data["scenario"]["id"] == folder.name
        if folder.name == "hi":
            assert data["indic_verified"] == "needs_native_review"
    else:
        assert data["api"]
        assert data["doc_urls"]
        assert data["software"]
        assert data["indic_verified"] == "needs_native_review"
    for name, entry in data["files"].items():
        actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        assert entry["sha256"] == actual, f"{name} changed after capture"


def test_sarvam_requests_reference_the_hashed_clips() -> None:
    """Each request that names a clip records the clip's sha256, and it is the one in the
    provenance file (the data the call used is the data that is committed)."""
    provenance = json.loads((SARVAM / "provenance.json").read_bytes())["files"]
    checked = 0
    no_file = []
    no_clip = []
    for name in sarvam_case_names():
        request = sarvam_case(name).request
        file = request.get("file")
        if file is None:
            assert "file" in request, f"{name}: the request document has no 'file' member at all"
            no_file.append(name)  # a documented absence: the call was made without a file
            continue
        if "clip" not in file:
            no_clip.append(name)  # a clip built on the fly (bytes only): its hash is still named
            assert len(file["sha256"]) == 64
            continue
        clip = (SARVAM / name / file["clip"]).resolve().relative_to(SARVAM.resolve())
        assert provenance[clip.as_posix()]["sha256"] == file["sha256"]
        checked += 1
    assert no_file == ["err_no_file"], "only the missing-file error case sends no file"
    assert checked >= 30


def test_no_secret_in_the_captured_fixtures() -> None:
    """Gate item 14 for the data this batch relies on: no key-looking value in any captured
    request, response or provenance file (the keys are patterns, assembled from parts)."""
    patterns = [
        re.compile("sk-" + "[A-Za-z0-9_-]{20,}"),
        re.compile("AKIA" + "[0-9A-Z]{16}"),
        re.compile("Bearer" + r"\s+[A-Za-z0-9._-]{20,}"),
        re.compile("api-subscription-key" + r"\s*[:=]\s*[A-Za-z0-9_-]{16,}"),
    ]
    for path in (
        list(SARVAM.rglob("*.json"))
        + list(PIPECAT.rglob("*.json"))
        + list(PIPECAT.rglob("*.jsonl"))
    ):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert pattern.search(text) is None, path.name


# ============================================================================ converter hygiene

EXPECTED_MODULES = [
    "__init__.py",
    "common.py",
    "gap_register.py",
    "pipecat_audio.py",
    "pipecat_conversation.py",
    "pipecat_otel.py",
    "pipecat_turn_events.py",
    "sarvam_stt.py",
]
FORBIDDEN = {
    "network": re.compile(
        r"^\s*(import|from)\s+(socket|http|urllib|requests|httpx|aiohttp|ssl)\b", re.M
    ),
    "clock": re.compile(
        r"datetime\.(now|utcnow|today)\(|time\.time\(|time\.monotonic\(|uuid\.uuid4\("
    ),
    "environment": re.compile(r"os\.environ|os\.getenv|dotenv"),
    "private fixture": re.compile(r"fixtures/private|voicera|VoicEra|\.env|/keys\b"),
    "write": re.compile(r"\.write_(text|bytes)\(|open\([^)]*[\"'][wa]"),
}


def test_converter_modules_exist_and_are_hygienic() -> None:
    """The converters are test-side code (adapters ship in later phases) in the directory the
    contract names. They make no network call, read no clock (a created_at is an argument;
    nothing is 'now'), read no environment variable or key file, never touch the private
    fixture and write no file. Live-provider tests are opt-in and marked; none exists here."""
    missing = [n for n in EXPECTED_MODULES if not (RUNS_DIR / n).is_file()]
    assert missing == [], f"converter modules not written yet: {missing}"
    for name in EXPECTED_MODULES:
        text = (RUNS_DIR / name).read_text(encoding="utf-8")
        assert text.splitlines()[0].startswith("# SPDX-License-Identifier: Apache-2.0")
        for what, pattern in FORBIDDEN.items():
            assert pattern.search(text) is None, f"{name}: {what}"


# ============================================================================ checker self-tests
# These tests use a hand-made result and need no converter: they prove the checkers above can
# fail. Each builds a correct fake, then breaks exactly one thing.


def _entry(
    record: Any, field: str, sources: tuple[str, ...], derivation: str | None, citation: Any = None
) -> Any:
    return SimpleNamespace(
        record=str(record.id),
        field=field,
        sources=sources,
        derivation=derivation,
        citation=citation,
    )


def _row(
    target: str, derivation: str | None, kind: str = "derivation", citation: Any = None
) -> Any:
    return SimpleNamespace(
        target=target, kind=kind, derivation=derivation, citation=citation, source=None
    )


def _good() -> tuple[Any, Any, Any]:
    turn = make3(
        "Turn", seq=0, role="caller", source="agent_stt", timing_source="none", text="hello"
    )
    root = {"stt": {"text": "hello", "n": "3"}}
    trace = [
        _entry(turn, "/seq", (), "order_index"),
        _entry(turn, "/role", (), "constant:caller"),
        _entry(turn, "/source", (), "constant:agent_stt"),
        _entry(turn, "/timing_source", (), "constant:none"),
        _entry(turn, "/text", ("/stt/text",), None),
    ]
    rows = [
        _row("Turn.seq", "order_index"),
        _row("Turn.role", "constant:caller", "constant"),
        _row("Turn.source", "constant:agent_stt", "constant"),
        _row("Turn.timing_source", "constant:none", "constant"),
        _row("Turn.text", None, "source"),
    ]
    result = SimpleNamespace(
        records=(turn,),
        channel_maps=(),
        trace=tuple(trace),
        warnings=(),
        consumed=(),
        external_ref_sources=(),
    )
    return result, root, SimpleNamespace(PROVENANCE_TABLE=tuple(rows))


def test_checker_accepts_a_fully_sourced_record() -> None:
    result, root, module = _good()
    check_no_fabrication(result, root, module)


def test_checker_rejects_a_value_without_a_source() -> None:
    result, root, module = _good()
    turn = result.records[0]
    invented = make3(
        "Turn",
        seq=0,
        role="caller",
        source="agent_stt",
        timing_source="none",
        text="hello",
        wall_time={"at": "2026-10-23T12:00:00.000000Z", "meaning": "start"},
    )
    result.records = (invented,)
    result.trace = tuple(
        SimpleNamespace(**{**vars(e), "record": str(invented.id)}) for e in result.trace
    )
    assert turn.id != invented.id
    with pytest.raises(AssertionError, match="no source"):
        check_no_fabrication(result, root, module)


def test_checker_rejects_a_copy_that_differs_from_its_source() -> None:
    result, _, module = _good()
    with pytest.raises(AssertionError, match="not the source value"):
        check_no_fabrication(result, {"stt": {"text": "HELLO"}}, module)


def test_checker_rejects_a_wrong_derivation_result() -> None:
    result, _, module = _good()
    # claim the text is a decimal-string derivation of a float: it is not
    result.trace = tuple(
        _entry(result.records[0], "/text", ("/stt/n",), "float_to_decimal_string")
        if e.field == "/text"
        else e
        for e in result.trace
    )
    module.PROVENANCE_TABLE = (
        *module.PROVENANCE_TABLE,
        _row("Turn.text", "float_to_decimal_string"),
    )
    with pytest.raises((AssertionError, ValueError, TypeError)):
        check_no_fabrication(result, {"stt": {"text": "hello", "n": "3"}}, module)


def test_checker_rejects_a_missing_table_row_and_an_unknown_derivation() -> None:
    result, root, module = _good()
    module.PROVENANCE_TABLE = tuple(r for r in module.PROVENANCE_TABLE if r.target != "Turn.text")
    with pytest.raises(AssertionError, match="no provenance-table row"):
        check_no_fabrication(result, root, module)
    result, root, module = _good()
    result.trace = tuple(
        _entry(result.records[0], "/seq", (), "made_up") if e.field == "/seq" else e
        for e in result.trace
    )
    with pytest.raises(AssertionError, match="not in the pinned set"):
        check_no_fabrication(result, root, module)


def test_checker_requires_citations_for_contract_derivations() -> None:
    """A derivation that rests on a published contract needs a complete citation on the trace
    entry and on the table row; with both it passes (positive control)."""
    cited = SimpleNamespace(url="https://example.com/c", retrieved_at="2026-10-23", statement="s")
    result, root, module = _good()
    turn = result.records[0]
    bare = tuple(
        _entry(turn, "/text", ("/stt/text",), "documented_start_of_turn")
        if e.field == "/text"
        else e
        for e in result.trace
    )
    # documented_start_of_turn re-computes to the constant "start", so the text must be "start"
    root = {"stt": {"text": "start"}}
    turn = make3(
        "Turn", seq=0, role="caller", source="agent_stt", timing_source="none", text="start"
    )
    result.records = (turn,)

    def swap(e: Any, **kw: Any) -> Any:
        return SimpleNamespace(**{**vars(e), "record": str(turn.id), **kw})

    rows = [r for r in module.PROVENANCE_TABLE if r.target != "Turn.text"]
    for entry_cit, row_cit, ok in (
        (None, None, False),
        (cited, None, False),
        (None, cited, False),
        (cited, cited, True),
    ):
        result.trace = tuple(
            swap(e, derivation="documented_start_of_turn", citation=entry_cit)
            if e.field == "/text"
            else swap(e)
            for e in bare
        )
        module.PROVENANCE_TABLE = (
            *rows,
            _row("Turn.text", "documented_start_of_turn", citation=row_cit),
        )
        if ok:
            check_no_fabrication(result, root, module)
        else:
            with pytest.raises(AssertionError, match=r"must cite|needs a complete citation"):
                check_no_fabrication(result, root, module)


def test_checker_rejects_trace_entries_for_records_that_do_not_exist() -> None:
    result, root, module = _good()
    ghost = make3("Turn")
    result.trace = (*result.trace, _entry(ghost, "/text", ("/stt/text",), None))
    with pytest.raises(AssertionError, match="do not exist"):
        check_no_fabrication(result, root, module)


def test_warning_checker_requires_raw_to_be_the_source_value() -> None:
    warning = SimpleNamespace(code="x", path="/a/b", raw="v")
    result = SimpleNamespace(warnings=(warning,))
    check_warnings_quote_sources(result, {"a": {"b": "v"}})
    with pytest.raises(AssertionError, match="is not the source value"):
        check_warnings_quote_sources(result, {"a": {"b": "other"}})
    with pytest.raises(AssertionError, match="does not resolve"):
        check_warnings_quote_sources(result, {"a": {}})


def test_coverage_checker_reports_an_unmapped_key() -> None:
    entry = SimpleNamespace(sources=("/a/x",))
    result = SimpleNamespace(trace=(entry,), consumed=(), external_ref_sources=(), warnings=())
    row = SimpleNamespace(source_key="F9:a.z")
    assert coverage_gaps(result, {"a.x", "a.y", "a.z"}, generic_key, [row], "F9") == ["F9:a.y"]
    wildcard = SimpleNamespace(source_key="F9:a.*")
    assert coverage_gaps(result, {"a.x", "a.y"}, generic_key, [wildcard], "F9") == []


# ============================================================================ resolver and registry


def test_pointer_resolver_handles_escapes_chains_and_wav_fields() -> None:
    root = {"a/b": {"c~d": [10, {"e": json.dumps({"f": [1, 2, {"g": "deep"}]})}]}}
    assert resolve_source(root, "/a~1b/c~0d/0") == 10
    assert resolve_source(root, "/a~1b/c~0d/1/e#/f/2/g") == "deep"
    data = f6_bytes("en")
    assert resolve_source(data, "wav:fmt/channels") == 2
    assert resolve_source(data, "wav:fmt/bits_per_sample") == 16
    assert resolve_source(data, "wav:fmt/format_tag") == 1
    assert wav_fields(b"nope") == {"bytes": b"nope"}
    assert dict(leaves({"a": [1, {"b": None}], "c": {}})) == {"/a/0": 1, "/a/1/b": None}


def test_derivation_registry_known_values() -> None:
    unix = DERIVATIONS["unix_nano_to_utc_us"]
    ms = DERIVATIONS["seconds_to_ms_floor"]
    dec = DERIVATIONS["float_to_decimal_string"]
    gross = DERIVATIONS["input_tokens_gross_minus_cache"]
    assert unix is not None
    assert ms is not None
    assert dec is not None
    assert gross is not None
    assert unix(("1792757169527180032",)) == "2026-10-23T12:06:09.527180Z"
    assert unix(("1792757173361252999",)) == "2026-10-23T12:06:13.361252Z"
    assert ms((0.5665280818939209,)) == 566
    assert ms((1.9999,)) == 1999
    assert ms((12.523798291,)) == 12523
    assert ms((3,)) == 3000
    assert dec((4.1,)) == "4.1"
    assert dec((100.0,)) == "100"
    assert dec((0.00001,)) == "0.00001"
    assert gross((305, 273)) == 32
    assert gross((100, 30, 20)) == 50
    assert DERIVATIONS["span_name_to_stage"] is not None
    assert DERIVATIONS["documented_stereo_left_user"]((2,)) == "caller"  # type: ignore[misc]
    with pytest.raises(AssertionError):
        DERIVATIONS["documented_stereo_left_user"]((1,))  # type: ignore[misc]
    assert set(CITATION_DERIVATIONS) <= set(DERIVATIONS)
    assert ENTRY_CITATION_ONLY <= CITATION_DERIVATIONS


def test_source_key_functions() -> None:
    doc = f1_doc("en_tool")
    assert (
        f1_key(doc, "/resourceSpans/0/scopeSpans/0/spans/0/attributes/0/value/stringValue")
        == "stt.gen_ai.provider.name"
    )
    assert (
        f1_key(doc, "/resourceSpans/0/scopeSpans/0/spans/0/startTimeUnixNano")
        == "stt.@startTimeUnixNano"
    )
    assert (
        f1_key(doc, "/resourceSpans/0/resource/attributes/4/value/stringValue")
        == "resource.service.name"
    )
    assert f1_key(doc, "/resourceSpans/0/scopeSpans/0/scope/name") == "scope.name"
    assert "llm.gen_ai.request.top_k" in f1_universe(doc)
    assert generic_key("/response/timestamps/words/0") == "response.timestamps.words"
    assert f4_key(f4_doc("en_tool"), "/1/arguments") == "tool_call.arguments"
    events = f7_events("en")
    assert (
        f7_key(events, "/2/payload/message/content")
        == "on_user_turn_stopped.payload.message.content"
    )
    assert (
        f7_key(events, "/2/harness_observed_at_utc")
        == "on_user_turn_stopped.@harness_observed_at_utc"
    )
    assert f6_key(b"", "wav:fmt/channels") == "wav.fmt/channels"
    assert "wav.bytes" in f6_universe(f6_bytes("en"))
