# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch D tests (definitions, result mapping, pipeline).

Contract: docs/phase3/api-surface-batch-d.md. Nothing of the batch D modules is imported at
module level: each loader imports lazily, so that before the modules exist every test fails on
its own with ``ModuleNotFoundError`` and the directory still collects.

All text is invented: Marathi-like words written as escapes (the file is ASCII). The two numbers
``TEST_ONLY_FLAG_MIN_CONFIDENCE`` and ``TEST_ONLY_REVIEW_BUDGET`` are values for these tests
only. They are NOT defaults of the product and stand for nothing the owner has decided: the
owner sets the real ones (SCOPE Decisions D2, task P3-29).

``FakeEngine`` is the fake of the ``SttEngine`` protocol of ``tracer_evalcore.stt`` (batch B
boundary). It builds the records itself from scripted per-chunk text; it opens no socket and
reads no clock.
"""

from __future__ import annotations

import importlib
import json
import re
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.importers.wav import WavAudio, read_wav
from tracer_evalcore.stt import SttContext, SttError, SttOutcome
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import ExternalRef, TranscriptId, TurnId, UsageRecordId
from tracer_evalschema.primitives import canonical_decimal_text
from tracer_evalschema.privacy import ConsentRecord, RetentionPolicy
from tracer_evalschema.transcript import Segment, TimelineOrigin, Transcript, Turn
from tracer_evalschema.usage import UsageQuantity, UsageRecord

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
SRC = ROOT / "packages" / "evalcore" / "src" / "tracer_evalcore"
SCOPE = ROOT / "docs" / "phase3" / "SCOPE.md"
ADR23 = ROOT / "docs" / "adr" / "0023-recorded-call-agreement-scoring.md"

NORMALISER = "mr-Deva/1"
#: A value for these tests only; the owner's real threshold is not decided (P3-29).
TEST_ONLY_FLAG_MIN_CONFIDENCE = 0.3
#: A value for these tests only; the owner's real review budget is not decided (P3-29).
TEST_ONLY_REVIEW_BUDGET = 0.25
CREATED_AT = datetime(2026, 10, 25, 9, 0, 0, tzinfo=UTC)
CALL_ID = "5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11"
#: Canaries that must never reach a result message, a log or a file they do not belong to.
TEXT_CANARY = "".join(["TRANSCRIPT", "CANARY", "x9"])
KEY_CANARY = "-".join(["CANARYKEY", "".join(["zq", "7f", "c3", "d1"]), "".join(["9a", "e2", "b8"])])

SHARE = "stt_export_flagged_token_share"
MER = "stt_export_agreement_mer"
COUNT = "stt_export_flag_count"
KEYS = (MER, COUNT, SHARE)


def mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalcore.{name}")


# ------------------------------------------------------------------------ invented words

NAMASKAR = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930"
MALA = "\u092e\u0932\u093e"
PAANI = "\u092a\u093e\u0923\u0940"
PANI = "\u092a\u0923\u0940"  # paani without the long a
GAAV = "\u0917\u093e\u0935"
KAAM = "\u0915\u093e\u092e"
GHAR = "\u0918\u0930"
BAAZAR = "\u092c\u093e\u091c\u093e\u0930"
DON = "\u0926\u094b\u0928"
TEEN = "\u0924\u0940\u0928"
JEVAN = "\u091c\u0947\u0935\u0928"
VEL_LLA = "\u0935\u0947\u0933"  # ends in LLA
VEL_LA = "\u0935\u0947\u0932"  # ends in LA
ABHI = "\u0905\u092d\u0940"
DANDA = "\u0964"
DIGITS_123 = "\u0967\u0968\u0969"


def sentence(*words: str) -> str:
    return " ".join(words)


# The base scenario (hand-computed, see the contract section 11).
EXPORT_LINES = (
    ("assistant", sentence(NAMASKAR, MALA, PAANI, GAAV)),
    ("user", sentence(KAAM, GHAR, BAAZAR)),
    ("assistant", sentence(DON, TEEN, JEVAN, VEL_LLA)),
)
STT_TEXTS = (
    sentence(NAMASKAR, MALA, PANI, GAAV, KAAM, GHAR),
    sentence(BAAZAR, DON, JEVAN, VEL_LLA, ABHI),
)
#: Phrases of the two chunks: (text, start_ms, end_ms). Their words are those of STT_TEXTS.
STT_PHRASES: tuple[tuple[tuple[str, int, int], ...], ...] = (
    ((sentence(NAMASKAR, MALA, PANI), 0, 2000), (sentence(GAAV, KAAM, GHAR), 2000, 4000)),
    ((sentence(BAAZAR, DON), 4000, 6000), (sentence(JEVAN, VEL_LLA, ABHI), 6000, 8000)),
)


def export_text(lines: Sequence[tuple[str, str]], *, call_id: str = CALL_ID) -> str:
    """A VoicEra-format export: header, delimiter and one stamped line per message."""
    body = [
        f"[2026-10-25T06:00:{second:02d}.000+00:00] {speaker}: {text}"
        for second, (speaker, text) in enumerate(lines)
    ]
    return "\n".join([f"call_id={call_id}", "---", *body]) + "\n"


BASE_EXPORT = export_text(EXPORT_LINES)


def write_wav(path: Path, *, frames: int, rate: int = 8000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames * channels)
    return path


def consent() -> ConsentRecord:
    return ConsentRecord(status="unknown")


def retention() -> RetentionPolicy:
    return RetentionPolicy(delete_on_expiry=False)


def read_audio(tmp_path: Path, *, frames: int = 24000, rate: int = 8000) -> WavAudio:
    path = write_wav(tmp_path / "call.wav", frames=frames, rate=rate)
    return read_wav(path, created_at=CREATED_AT, consent=consent(), retention=retention())


# ------------------------------------------------------------------------ the rubric file


def rubric_doc(
    *,
    threshold: Any = TEST_ONLY_FLAG_MIN_CONFIDENCE,
    budget: Any = TEST_ONLY_REVIEW_BUDGET,
    optional: Sequence[str] = (MER, COUNT),
    normaliser: str = NORMALISER,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = [
        {
            "metric": SHARE,
            "required": True,
            "pass_rule": {"kind": "range", "max": budget, "inclusive": True},
        }
    ]
    items += [{"metric": key, "required": False} for key in optional]
    return {
        "format": "tracer.rubric/1",
        "parameters": {"flag_min_confidence": threshold, "normaliser": normaliser},
        "items": items,
    }


def rubric_text(**options: Any) -> str:
    return json.dumps(rubric_doc(**options))


def spec(**options: Any) -> Any:
    return mod("rubricfile").parse_rubric_file(rubric_text(**options))


def configuration(*, language: str = "mr", registry: Any = None, **options: Any) -> Any:
    definitions = mod("definitions")
    extra = {} if registry is None else {"registry": registry}
    return definitions.build_configuration(
        spec(**options), configured_language=language, created_at=CREATED_AT, **extra
    )


# ------------------------------------------------------------------------ the fake engine


@dataclass(frozen=True)
class ChunkScript:
    """What the fake engine answers for one chunk."""

    text: str
    language: str | None = "mr"
    phrases: tuple[tuple[str, int, int], ...] | None = None
    probability: str | None = None


def scripts(
    texts: Sequence[str] = STT_TEXTS,
    phrases: Sequence[tuple[tuple[str, int, int], ...] | None] | None = STT_PHRASES,
    languages: Sequence[str | None] | None = None,
) -> list[ChunkScript]:
    result = []
    for index, text in enumerate(texts):
        result.append(
            ChunkScript(
                text,
                "mr" if languages is None else languages[index],
                None if phrases is None else phrases[index],
            )
        )
    return result


def _seconds(frames: int, rate: int) -> str:
    value = (Decimal(frames) / Decimal(rate)).quantize(Decimal(1).scaleb(-18), ROUND_HALF_EVEN)
    return canonical_decimal_text(value)


@dataclass
class FakeEngine:
    """An ``SttEngine`` that answers from a script. ``calls`` records every ``transcribe``."""

    chunk_scripts: Sequence[ChunkScript] = field(default_factory=scripts)
    provider: str | None = "fake"
    model: str | None = "fake-model-1"
    fail_at: int | None = None
    failure: SttError | None = None
    raises: BaseException | None = None
    calls: list[tuple[ChunkPlan, SttContext]] = field(default_factory=list)
    warnings: tuple[Any, ...] = ()

    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome:
        self.calls.append((plan, context))
        if self.raises is not None:
            raise self.raises
        usage: list[UsageRecord] = []
        transcript_id = TranscriptId.new()
        turns: list[Turn] = []
        any_offsets = False
        failed = None
        for chunk in plan.chunks:
            if self.fail_at == chunk.index:
                failed = self.failure
                break
            usage.append(
                UsageRecord(
                    id=UsageRecordId.new(),
                    schema_version=SCHEMA_VERSION,
                    created_at=context.created_at,
                    run_id=context.run_id,
                    domain="evaluation",
                    stage="stt",
                    provider=self.provider or "fake",
                    model=self.model,
                    provider_call_ref=ExternalRef(
                        system="fake", kind="request_id", value=f"req-{chunk.index}"
                    ),
                    quantities=[
                        UsageQuantity(
                            unit="audio_input_seconds",
                            count=_seconds(chunk.frames, chunk.sample_rate_hz),
                        ),
                        UsageQuantity(unit="requests", count=1),
                    ],
                    source="measured",
                )
            )
            script = self.chunk_scripts[chunk.index]
            if not script.text.strip():
                continue
            segments = [
                Segment(granularity="phrase", start_ms=a, end_ms=b, text=t)
                for t, a, b in (script.phrases or ())
            ]
            any_offsets = any_offsets or bool(segments)
            turns.append(
                Turn(
                    id=TurnId.new(),
                    schema_version=SCHEMA_VERSION,
                    created_at=context.created_at,
                    transcript_id=transcript_id,
                    seq=len(turns),
                    role="unknown",
                    start_ms=min(s.start_ms for s in segments) if segments else None,  # type: ignore[type-var]
                    end_ms=max(s.end_ms for s in segments) if segments else None,  # type: ignore[type-var]
                    timing_source="audio_alignment" if segments else "none",
                    source="our_stt",
                    text=script.text,
                    segments=segments or None,
                    language=script.language,
                    metadata={"tracer.chunk_index": chunk.index},
                )
            )
        if failed is not None:
            return SttOutcome(None, (), tuple(usage), (), failed)
        tags = {t.language for t in turns}
        transcript = Transcript(
            id=transcript_id,
            schema_version=SCHEMA_VERSION,
            created_at=context.created_at,
            source="our_stt",
            provider=self.provider,
            model=self.model,
            detected_language=tags.pop() if len(tags) == 1 and None not in tags else None,
            timeline_origin=(
                TimelineOrigin(kind="artifact", ref=context.audio_artifact_id)
                if any_offsets
                else None
            ),
            privacy=context.privacy,
        )
        return SttOutcome(transcript, tuple(turns), tuple(usage), tuple(self.warnings), None)


# ------------------------------------------------------------------------ running the pipeline


@dataclass
class Run:
    result: Any
    engine: FakeEngine
    bundle: Any
    configuration: Any


def results_by_key(bundle: Any) -> dict[str, Any]:
    definitions = {d.id: d.key for d in bundle.snapshot.metric_definitions}
    return {definitions[r.metric.id]: r for r in bundle.results}


def run_pipeline(
    tmp_path: Path,
    *,
    export: str | None = BASE_EXPORT,
    engine: FakeEngine | None = None,
    audio: Any = "default",
    config: Any = None,
    language: str = "mr",
    export_language: str | None = "mr",
    caller_origin: str = "human",
    project: Any = None,
    agent_target: Any = None,
    **options: Any,
) -> Run:
    definitions = mod("definitions")
    pipeline = mod("pipeline")
    engine = engine if engine is not None else FakeEngine()
    wav = read_audio(tmp_path, frames=24000) if audio == "default" else audio
    config = config if config is not None else configuration(language=language)
    options.setdefault("max_chunk_ms", 2000)
    options.setdefault("search_ms", 1000)
    result = pipeline.score_recorded_call(
        audio=wav,
        export_text=export,
        engine=engine,
        configuration=config,
        project=project or definitions.recorded_project(name="test project", created_at=CREATED_AT),
        agent_target=agent_target
        or definitions.recorded_agent_target(name="test agent", created_at=CREATED_AT),
        configured_language=language,
        export_declared_language=export_language,
        caller_origin=caller_origin,
        consent=consent(),
        retention=retention(),
        created_at=CREATED_AT,
        **options,
    )
    return Run(result, engine, result.bundle, config)


_ID = re.compile(
    r"\b(?:[a-z]{2,6})_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def mask_ids(text: str) -> str:
    """The JSON text with every typed id replaced by <prefix>#<n> in order of first appearance."""
    seen: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        if value not in seen:
            seen[value] = f"{value.partition('_')[0]}#{len(seen)}"
        return seen[value]

    return _ID.sub(replace, text)


def base_document(
    threshold: str = "0.3", flagged: tuple[bool, bool, bool] = (False, True, True)
) -> dict[str, Any]:
    """The tracer.flags/1 document of the base scenario, computed by hand from the texts."""
    third = EXPORT_LINES[2][1]
    paani_at = EXPORT_LINES[0][1].index(PAANI)
    teen_at = third.index(TEEN)
    return {
        "format": "tracer.flags/1",
        "signal": "stt_disagreement",
        "normaliser": "mr-Deva/1",
        "stt": {"provider": "fake", "model": "fake-model-1", "model_source": "requested"},
        "flag_min_confidence": threshold,
        "alignment": {
            "unit": "word",
            "hits": 9,
            "substitutions": 1,
            "deletions": 1,
            "insertions": 1,
        },
        "tokens": {
            "export": 11,
            "stt": 11,
            "flagged_export": (1 if flagged[0] else 0) + (1 if flagged[1] else 0),
        },
        "spans": [
            {
                "turn_seq": 0,
                "start": paani_at,
                "end": paani_at + len(PAANI),
                "export_tokens": 1,
                "stt_token_start": 2,
                "stt_token_end": 3,
                "substitutions": 1,
                "deletions": 0,
                "insertions": 0,
                "edit_distance": 1,
                "key_length": 4,
                "confidence": "0.25",
                "flagged": flagged[0],
                "near_chunk_boundary": False,
                "audio_start_ms": 0,
                "audio_end_ms": 2000,
            },
            {
                "turn_seq": 2,
                "start": teen_at,
                "end": teen_at + len(TEEN),
                "export_tokens": 1,
                "stt_token_start": 8,
                "stt_token_end": 8,
                "substitutions": 0,
                "deletions": 1,
                "insertions": 0,
                "edit_distance": 3,
                "key_length": 3,
                "confidence": "1",
                "flagged": flagged[1],
                "near_chunk_boundary": False,
            },
            {
                "turn_seq": 2,
                "start": len(third),
                "end": len(third),
                "export_tokens": 0,
                "stt_token_start": 10,
                "stt_token_end": 11,
                "substitutions": 0,
                "deletions": 0,
                "insertions": 1,
                "edit_distance": 3,
                "key_length": 3,
                "confidence": "1",
                "flagged": flagged[2],
                "near_chunk_boundary": True,
                "audio_start_ms": 6000,
                "audio_end_ms": 8000,
            },
        ],
    }


def flags_of(result: Any) -> dict[str, Any]:
    document = result.metadata["tracer.flags"]
    assert isinstance(document, dict)
    return document


def callable_names(module: ModuleType) -> list[str]:
    return [n for n, v in vars(module).items() if callable(v) and not n.startswith("_")]


def is_decimal_text(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", value) is not None


def mapping_has(mapping: Mapping[str, Any], *names: str) -> bool:
    return all(name in mapping for name in names)
