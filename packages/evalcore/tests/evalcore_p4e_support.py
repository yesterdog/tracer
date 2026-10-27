# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch E evalcore tests (P4-21, P4-22: the simulated bundle and its
scoring). Support module, not collected. Contract: docs/phase4/api-surface-batch-e.md.

Nothing of the batch E modules is imported at module level (``mod`` imports lazily), so before the
modules exist each test fails on its own with ``ModuleNotFoundError`` and the directory still
collects. All text is invented; Marathi words are ``\\u`` escapes. The calls are synthetic: the
timing documents are built by the batch A extractor from hand-written frame logs, the speech-to-text
outcomes by the Phase 3 response mapper from invented bodies, the export by the Phase 3 reader.
"""

from __future__ import annotations

import copy
import importlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    BAAZAR,
    DON,
    GAAV,
    GHAR,
    KAAM,
    MALA,
    NAMASKAR,
    PAANI,
    export_text,
    sentence,
)

CREATED_AT = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
STARTED_AT = datetime(2026, 10, 27, 12, 0, 1, tzinfo=UTC)
MS = 1_000_000
T0 = 5_000_000_000
RATE = 16_000
WAIT_MS = 300
MAX_SILENCE_MS = 3000
AUTH_TEXT = "Approved by the test operator, ticket 17"
LANGUAGE = "mr"
TTS_LANGUAGE = "mr-IN"

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())

LINE_0 = sentence(NAMASKAR, MALA)
LINE_1 = sentence(KAAM, GHAR)
AGENT_0 = sentence(NAMASKAR, MALA, PAANI, GAAV)
AGENT_1 = sentence(DON, BAAZAR)
_ = (DON, BAAZAR, GAAV, PAANI)


def mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalcore.{name}")


def ms(value: int) -> int:
    return value * MS


# ---------------------------------------------------------------------------------- spec files


def evaluator_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.sim_evaluator/1",
        "name": "synthetic helpline test",
        "language": LANGUAGE,
        "instructions": "Ask two short questions of the agent.",
        "expected_outcome": "The agent answers both questions.",
        "scripted_turns": [
            {"index": 0, "text": LINE_0, "language": LANGUAGE},
            {"index": 1, "text": LINE_1, "language": LANGUAGE},
        ],
        "max_duration_ms": 60_000,
    }
    doc.update(changes)
    return doc


def persona_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.sim_persona/1",
        "key": "caller-a",
        "language": LANGUAGE,
        "accent": None,
    }
    doc.update(changes)
    return doc


def profile_spec_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.sim_test_profile/1",
        "name": "Asha Testkar",
        "dob": "2001-02-03",
        "account_number": "TEST-0001",
    }
    doc.update(changes)
    return doc


def target_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.sim_target/1",
        "name": "sample agent",
        "url": "ws://127.0.0.1:8765/ws-client",
    }
    doc.update(changes)
    return doc


DIALECT_AUDIO = {
    "pipecat-protobuf/1": {"sample_rate_hz": RATE, "wire_codec": "pcm_s16le"},
    "twilio-media-stream/1": {"sample_rate_hz": 8000, "wire_codec": "mulaw"},
}


def profile_file_doc(
    dialect: str = "pipecat-protobuf/1",
    *,
    tts: str = "fake",
    stt: str = "fake",
    caller: dict[str, Any] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    """A complete, valid ``tracer.sim_profile_file/1`` for fake engines (every value written)."""
    audio = DIALECT_AUDIO[dialect]
    rate = audio["sample_rate_hz"]
    doc: dict[str, Any] = {
        "format": "tracer.sim_profile_file/1",
        "dialect": dialect,
        "audio": {
            "sample_rate_hz": rate,
            "channels": 1,
            "frame_ms": 20,
            "wire_codec": audio["wire_codec"],
        },
        "turn_taking": {
            "caller_speaks_first": True,
            "wait_for_agent_silence_ms": WAIT_MS,
            "max_silence_ms": MAX_SILENCE_MS,
        },
        "barge_ins": [],
        "caller": caller if caller is not None else {"mode": "scripted"},
        "voices": {
            "caller-a": {
                "provider": tts,
                "model": "tone" if tts == "fake" else "bulbul:v3",
                "speaker": "test-voice",
                "language_code": TTS_LANGUAGE,
                "pace": "1",
                "sample_rate_hz": rate,
                "codec": "mulaw" if audio["wire_codec"] == "mulaw" else "linear16",
            }
        },
        "stt": {
            "provider": stt,
            "model": "scenario-text" if stt == "fake" else "saaras:v4",
            "max_chunk_ms": 25_000,
        },
        "agreement": {"flag_min_confidence": 0.5, "normaliser": "mr-Deva/1"},
        "min_call_interval_ms": 0,
    }
    doc.update(changes)
    return doc


def llm_caller(**changes: Any) -> dict[str, Any]:
    prompt_mod = importlib.import_module("tracer_evalcore.prompt")
    block: dict[str, Any] = {
        "mode": "llm",
        "provider": "sarvam",
        "model": "sarvam-105b",
        "max_tokens": 200,
        "seed": 7,
        "reasoning_effort": None,
        "max_line_chars": 200,
        "max_turns": 4,
        "max_regenerations": 1,
        "end_sentinel": "END_CALL",
        "prompt": {
            "template_id": prompt_mod.TEMPLATE_ID,
            "template_sha256": prompt_mod.template_sha256(prompt_mod.TEMPLATE_ID),
            "agent_text_max_chars": 500,
        },
    }
    block.update(changes)
    return block


def replay_caller(**changes: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "mode": "replay",
        "max_line_chars": 200,
        "max_turns": 4,
        "end_sentinel": "END_CALL",
    }
    block.update(changes)
    return block


def dumps(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=True)


CONNECTION_NONE = {
    "auth_token_env": None,
    "origin": None,
    "bot_holds_telephony_credentials": None,
}
CONNECTION_TWILIO = {**CONNECTION_NONE, "bot_holds_telephony_credentials": False}
NO_CAPS = {"call_seconds": None, "tts_chars": None, "llm_tokens": None, "stt_seconds": None}


def stored_profile(
    file_doc: Mapping[str, Any] | None = None,
    *,
    caps: Mapping[str, int | None] | None = None,
    connection: Mapping[str, Any] | None = None,
    persona_key: str = "caller-a",
) -> dict[str, Any]:
    """The stored ``tracer.sim_profile/1`` of a fake-engine run (built by the product)."""
    simprofile = mod("simprofile")
    document = dict(file_doc if file_doc is not None else profile_file_doc())
    parsed = simprofile.parse_sim_profile_file(dumps(document))
    if connection is None:
        twilio = parsed.dialect == "twilio-media-stream/1"
        connection = CONNECTION_TWILIO if twilio else CONNECTION_NONE
    built: dict[str, Any] = simprofile.build_sim_profile(
        parsed,
        persona_key=persona_key,
        connection=dict(connection),
        caps=dict(caps if caps is not None else NO_CAPS),
    )
    simprofile.validate_sim_profile(built, check_caps=False)
    return built


def configuration(
    *,
    profile: Mapping[str, Any] | None = None,
    evaluator: Mapping[str, Any] | None = None,
    with_test_profile: bool = False,
    flag_min_confidence: float = 0.5,
) -> Any:
    """A sealed simulated-run configuration (the product's)."""
    spec = mod("simspec")
    stored = dict(profile if profile is not None else stored_profile())
    return mod("simscore").build_simulated_configuration(
        evaluator=spec.parse_sim_evaluator(dumps(evaluator if evaluator else evaluator_doc())),
        persona=spec.parse_sim_persona(dumps(persona_doc())),
        test_profile=spec.parse_sim_test_profile(dumps(profile_spec_doc()))
        if with_test_profile
        else None,
        sim_profile=stored,
        flag_min_confidence=flag_min_confidence,
        normaliser="mr-Deva/1",
        created_at=CREATED_AT,
    )


# ---------------------------------------------------------------------------- a synthetic call


def log_frame(t_ns: int, payload: bytes, *, rate: int = RATE, codec: str = "pcm_s16le") -> Any:
    return mod("recorder").LogFrame(t_ns, payload, rate, 1, codec)


def speech(samples: int = 320, level: int = 1000) -> bytes:
    return level.to_bytes(2, "little", signed=True) * samples


def quiet(samples: int = 320) -> bytes:
    return bytes(2 * samples)


def timing_document(
    *,
    established: bool = True,
    agent_starts_ms: Sequence[int] = (400, 1500),
    turns_ms: Sequence[int] = (0, 1000),
    barge: Mapping[int, int] | None = None,
    dialect: str = "pipecat-protobuf/1",
    agent_frames: int = 5,
    agent_runs: Sequence[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """The ``tracer.timing/1`` of a synthetic call: each caller line is 10 frames, each agent reply
    ``agent_frames`` active frames (or the explicit ``agent_runs`` of (start ms, frames));
    ``T0`` is the origin."""
    timing = mod("simtiming")
    recorder = mod("recorder")
    fmt = recorder.RecorderFormat(RATE, "pcm_s16le")
    if not established:
        document: dict[str, Any] = timing.extract_timing(
            dialect=dialect,
            fmt=fmt,
            wait_for_agent_silence_ms=WAIT_MS,
            connect_started_ns=T0 - ms(30),
            connect_ended_ns=T0 - ms(5),
            established=False,
            observed_until_ns=T0,
            caller_turns=[],
            agent_frames=[],
        )
        return document
    turns = [
        timing.CallerTurn(
            seq,
            T0 + ms(start),
            T0 + ms(start + 180),
            None if barge is None else barge.get(seq),
            timing.SelfTimes(
                None if seq == 0 else 320, None if seq == 0 else WAIT_MS, None, None, None
            ),
        )
        for seq, start in enumerate(turns_ms)
    ]
    runs = agent_runs if agent_runs is not None else [(a, agent_frames) for a in agent_starts_ms]
    frames = [
        log_frame(T0 + ms(start + 20 * k), speech()) for start, count in runs for k in range(count)
    ]
    document = timing.extract_timing(
        dialect=dialect,
        fmt=fmt,
        wait_for_agent_silence_ms=WAIT_MS,
        connect_started_ns=T0 - ms(30),
        connect_ended_ns=T0,
        established=True,
        observed_until_ns=T0 + ms(4000),
        caller_turns=turns,
        agent_frames=frames,
    )
    return document


def counts(**changes: int) -> dict[str, int]:
    base = dict.fromkeys(
        (
            "caller_turns_started",
            "caller_turns_completed",
            "agent_segments",
            "barge_ins_fired",
            "brain_requests",
            "brain_invalid_outputs",
            "brain_regenerations",
            "tts_requests",
            "tts_cache_hits",
            "llm_requests",
            "stt_requests",
            "frames_sent",
            "frames_received",
            "inbound_messages_ignored",
            "signals_received",
        ),
        0,
    )
    base.update(changes)
    return base


def outcome_document(
    *,
    established: bool = True,
    end_reason: str = "script_complete",
    cap_reached: str | None = None,
    mode: str = "scripted",
    dialect: str = "pipecat-protobuf/1",
    **count_changes: int,
) -> dict[str, Any]:
    built: dict[str, Any] = mod("simoutcome").build_sim_outcome(
        dialect=dialect,
        call_established=established,
        end_reason=end_reason if established else "setup_failed",
        cap_reached=cap_reached,
        connected_ms=4000 if established else None,
        authorisation_reference_sha256=mod("simoutcome").authorisation_reference_sha256(AUTH_TEXT),
        caller_text_origin=mode,
        counts=counts(
            caller_turns_started=2 if established else 0,
            caller_turns_completed=2 if established else 0,
            agent_segments=2 if established else 0,
            **count_changes,
        ),
        brain_mode=mode,
    )
    return built


# ------------------------------------------------------------------------ speech-to-text outcome


def privacy() -> Any:
    return mod("simbundle").provisional_privacy()


def stt_outcome(
    texts: Sequence[str],
    *,
    run_id: Any,
    artifact_id: Any,
    language_label: str | None = "mr-IN",
    role: str = "agent",
    usage_chunks: bool = True,
) -> Any:
    """An ``our_stt`` outcome of ``texts`` (one chunk each) made by the Phase 3 response mapper."""
    from tracer_evalcore.audio.chunker import Chunk, ChunkPlan
    from tracer_evalcore.stt import SttContext
    from tracer_evalproviders.sarvam.mapping import map_responses
    from tracer_evalproviders.sarvam.models import ChunkResponse, parse_response

    chunks = tuple(
        Chunk(
            index=k,
            start_frame=RATE * (k + 1),
            end_frame=RATE * (k + 1) + RATE // 2,
            sample_rate_hz=RATE,
            channels=1,
            wav=b"RIFF",
        )
        for k in range(len(texts))
    )
    plan = ChunkPlan(
        sample_rate_hz=RATE,
        channels=2,
        max_chunk_ms=25_000,
        search_ms=5_000,
        total_frames=RATE * (len(texts) + 2),
        chunks=chunks,
        channel=1,
        activity_wait_ms=WAIT_MS,
    )
    answered = []
    for k, text in enumerate(texts):
        body: dict[str, Any] = {"request_id": f"req-{k:04d}", "transcript": text}
        if language_label is not None:
            body["language_code"] = language_label
        answered.append(ChunkResponse(k, parse_response(json.dumps(body).encode()), None, 1))
    context = SttContext(
        run_id=run_id,
        audio_artifact_id=artifact_id,
        created_at=CREATED_AT,
        privacy=privacy(),
        configured_language=LANGUAGE,
        role=role,
    )
    outcome = map_responses(plan, answered, model="saaras:v4", context=context)
    if not usage_chunks:
        return outcome
    return outcome


def export_read(lines: Sequence[tuple[str, str]] | None = None, *, language: str = LANGUAGE) -> Any:
    """The agent's own export, read by the Phase 3 reader (provisional marker)."""
    from tracer_evalcore.importers.voicera import read_voicera_export
    from tracer_evalschema.transcript import LanguageDeclaration

    text = export_text(
        lines
        if lines is not None
        else [("assistant", AGENT_0), ("user", LINE_0), ("assistant", AGENT_1)]
    )
    read = read_voicera_export(
        text,
        created_at=CREATED_AT,
        privacy=privacy(),
        declared_language=LanguageDeclaration(
            language=language, source="user_declared", declared_at=CREATED_AT
        ),
        accept_warnings=True,
    )
    return mod("simbundle").ExportRead(read.transcript, read.turns, read.warnings)


@dataclass
class Built:
    bundle: Any
    configuration: Any
    call: Any
    stt: Any
    export: Any
    run_id: Any
    artifact_id: Any


def build_bundle(
    *,
    established: bool = True,
    stt_texts: Sequence[str] | None = (AGENT_0, AGENT_1),
    stt_language: str | None = "mr-IN",
    export: Any = None,
    stt_failed: bool = False,
    lines: int = 2,
    timing: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
    barge: Mapping[int, int] | None = None,
    complete_last_line: bool = True,
    end_reason: str = "script_complete",
    mode: str = "scripted",
    with_test_profile: bool = False,
    usage: Sequence[Any] = (),
    run_id: Any = None,
) -> Built:
    """A simulated bundle built by the product from a synthetic call (the pure builder)."""
    bundle_mod = mod("simbundle")
    ids = importlib.import_module("tracer_evalschema.ids")
    run_id = run_id if run_id is not None else ids.RunId.new()
    artifact_id = ids.ArtifactId.new()
    stored = dict(profile) if profile is not None else stored_profile()
    config = configuration(profile=stored, with_test_profile=with_test_profile)
    timing_doc = (
        dict(timing)
        if timing is not None
        else timing_document(established=established, barge=barge)
    )
    outcome = outcome_document(established=established, end_reason=end_reason, mode=mode)
    recording = (
        bundle_mod.RecordingFacts(
            artifact_id, "file:///tmp/call-recording.wav", "ab" * 32, 44 + 4 * RATE * 4, RATE
        )
        if established
        else None
    )
    caller_lines = tuple(
        bundle_mod.CallerLineFact(
            seq=k,
            text=(LINE_0, LINE_1)[k % 2],
            language=TTS_LANGUAGE,
            start_ms=1000 * k,
            end_ms=1000 * k + 200,
            complete=not (k == lines - 1 and not complete_last_line),
        )
        for k in range(lines if established else 0)
    )
    call = bundle_mod.SimulatedCall(
        run_id=run_id,
        established=established,
        started_at=STARTED_AT if established else None,
        recording=recording,
        caller_lines=caller_lines,
        caller_text_origin=mode,
        timing=timing_doc,
        outcome=outcome,
        usage=tuple(usage),
    )
    stt = None
    if established and stt_texts is not None and not stt_failed:
        stt = stt_outcome(
            stt_texts, run_id=run_id, artifact_id=artifact_id, language_label=stt_language
        )
    warnings: tuple[Any, ...] = ()
    if stt_failed:
        from tracer_evalschema.events import IngestWarning

        warnings = (
            IngestWarning(code="stt_failed", path="/stt", message="SttServerError: chunk 0"),
        )
    spec = importlib.import_module("tracer_evalcore.simspec")
    del spec
    project = bundle_mod.simulated_project(name="synthetic project", created_at=CREATED_AT)
    target = bundle_mod.simulated_agent_target(
        name="sample agent",
        transport="ws",
        url="ws://127.0.0.1:8765/ws-client",
        created_at=CREATED_AT,
    )
    built = bundle_mod.build_simulated_bundle(
        configuration=config,
        project=project,
        agent_target=target,
        call=call,
        stt=stt,
        export=export,
        stt_warnings=warnings,
        costs=None,
        created_at=CREATED_AT,
    )
    return Built(built, config, call, stt, export, run_id, artifact_id)


def usage_record(run_id: Any, stage: str, **quantities: int) -> Any:
    """A measured usage record of the run (ids are fresh)."""
    from tracer_evalschema import SCHEMA_VERSION
    from tracer_evalschema.ids import UsageRecordId
    from tracer_evalschema.usage import UsageRecord

    return UsageRecord.model_validate(
        {
            "id": UsageRecordId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": CREATED_AT,
            "run_id": run_id,
            "domain": "evaluation",
            "stage": stage,
            "provider": "sarvam",
            "model": "bulbul:v3",
            "quantities": [{"unit": unit, "count": count} for unit, count in quantities.items()],
            "source": "measured",
        }
    )


def results_by_key(built: Built) -> dict[str, Any]:
    keys = {d.id: d.key for d in built.bundle.snapshot.metric_definitions}
    return {keys[r.metric.id]: r for r in built.bundle.results}


def deep(doc: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(doc))
