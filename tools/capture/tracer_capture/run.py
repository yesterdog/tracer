# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Run one scripted scenario through a local Pipecat pipeline and write the capture.

Usage (from the repository root):
    uv run --project tools/capture python -m tracer_capture.run \
        --scenario tools/capture/scenarios/en.yaml --out <output dir>

Everything runs on this machine: Whisper (STT), a Qwen model behind a local OpenAI-compatible
server (LLM) and Kokoro (TTS). No network call is made once the models are cached, and no API key
is read.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import soxr
import yaml
from kokoro_onnx import Kokoro
from loguru import logger
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.evals.conversation_observer import (
    EvalConversationEndedMessage,
    EvalConversationObserver,
)
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.tracing.setup import setup_tracing
from pipecat.workers.runner import WorkerRunner

from tracer_capture.models import KOKORO_DIR, LLM_REPO, WHISPER_REPO, ensure_models
from tracer_capture.otlp import spans_to_otlp_json
from tracer_capture.provenance import write_provenance
from tracer_capture.transport import ScriptedTransport

IN_RATE = 16000
OUT_RATE = 24000
CHUNK_MS = 20
LLM_PORT = 8765


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return {"__type__": type(obj).__name__}


class EventLog:
    """Turn-event log: Pipecat's own event payloads, one JSON object per line.

    ``harness_observed_at_utc`` is added by the harness when the handler runs; it is not a
    Pipecat field.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, source: str, event: str, **payload: Any) -> None:
        self.rows.append(
            {
                "seq": len(self.rows),
                "source": source,
                "event": event,
                "payload": _plain(payload),
                "harness_observed_at_utc": _now(),
            }
        )


class BotActivity(BaseObserver):
    def __init__(self, log: EventLog) -> None:
        super().__init__(observe_every_push=False)
        self.log = log
        self.speaking = False
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.stopped.set()

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        # Broadcast frames arrive twice (downstream and upstream); count the downstream one.
        if frame.broadcast_sibling_id is not None and data.direction != FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            self.speaking = True
            self.started.set()
            self.stopped.clear()
            self.log.add("frame_observer", "bot_started_speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.speaking = False
            self.stopped.set()
            self.log.add("frame_observer", "bot_stopped_speaking")


def synth(kokoro: Kokoro, text: str, voice: str, lang: str) -> bytes:
    samples, rate = kokoro.create(text, voice=voice, speed=1.0, lang=lang)
    pcm = soxr.resample(samples.astype(np.float32), rate, IN_RATE)
    return bytes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())


async def feeder(
    transport: ScriptedTransport,
    turns: list[tuple[dict[str, Any], bytes]],
    activity: BotActivity,
    worker: PipelineWorker,
    ready: asyncio.Event,
) -> None:
    chunk = IN_RATE * CHUNK_MS // 1000 * 2
    silence = bytes(chunk)
    t0 = time.monotonic()
    sent = 0

    async def pump(pcm: bytes) -> None:
        nonlocal sent
        for i in range(0, len(pcm), chunk):
            part = pcm[i : i + chunk].ljust(chunk, b"\0")
            sent += 1
            delay = t0 + sent * CHUNK_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await transport.input().feed(part)

    async def quiet(secs: float) -> None:
        await pump(silence * int(secs * 1000 / CHUNK_MS))

    async def quiet_until(event: asyncio.Event, limit: float) -> bool:
        waited = 0.0
        while not event.is_set() and waited < limit:
            await pump(silence)
            waited += CHUNK_MS / 1000
        return event.is_set()

    async def bot_reply() -> None:
        # The previous caller turn is over; wait for the bot's reply to start, then to finish.
        activity.started.clear()
        if await quiet_until(activity.started, 20):
            await quiet_until(activity.stopped, 40)

    await ready.wait()
    t0 = time.monotonic()
    for spec, audio in turns:
        mode = spec["mode"]
        if mode == "first":
            await quiet(0.8)
        elif mode == "after_bot":
            await bot_reply()
            await quiet(1.0)
        elif mode == "interrupt":
            activity.started.clear()
            if not await quiet_until(activity.started, 30):
                raise RuntimeError("bot never started speaking; cannot interrupt")
            await quiet(1.0)
        else:
            raise ValueError(f"unknown turn mode {mode!r}")
        await pump(audio)
    await bot_reply()
    await quiet(2.0)
    await worker.queue_frame(EndFrame())


def start_llm(log_path: Path) -> subprocess.Popen[bytes]:
    exe = Path(sys.executable).parent / "mlx_lm.server"
    log = log_path.open("wb")
    proc = subprocess.Popen(  # noqa: S603
        [str(exe), "--model", LLM_REPO, "--host", "127.0.0.1", "--port", str(LLM_PORT)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "HF_HUB_OFFLINE": "1"},
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"LLM server exited early; see {log_path}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{LLM_PORT}/v1/models", timeout=2)
            return proc
        except OSError:
            time.sleep(1)
    proc.kill()
    raise RuntimeError("LLM server did not become ready")


def warm_up_llm() -> None:
    body = json.dumps(
        {"model": LLM_REPO, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{LLM_PORT}/v1/chat/completions",
        body,
        {"content-type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=120).read()  # noqa: S310


async def run(scenario_path: Path, out: Path, scratch: Path) -> dict[str, Any]:
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    sid = scenario["id"]
    out.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)

    os.environ.pop("HOSTNAME", None)
    os.environ.setdefault("OPENAI_API_KEY", "local-not-a-key")  # the OpenAI client needs a value
    _paths, models = ensure_models()
    import huggingface_hub.constants as hf_constants

    # Models are cached and pinned; the capture itself must not need the network.
    hf_constants.HF_HUB_OFFLINE = True
    llm_proc = start_llm(scratch / "llm_server.log")
    try:
        warm_up_llm()
        kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        caller = scenario["caller"]
        turns = [
            (t, synth(kokoro, t["text"], caller["tts_voice"], caller["tts_language"]))
            for t in caller["turns"]
        ]
        del kokoro

        exporter = InMemorySpanExporter()
        if not setup_tracing(service_name="tracer-capture", exporter=exporter):
            raise RuntimeError("tracing setup failed")
        from opentelemetry import trace

        provider = trace.get_tracer_provider()

        log = EventLog()
        bot = scenario["bot"]
        stt = WhisperSTTService(
            device="cpu",
            compute_type="int8",
            settings=WhisperSTTService.Settings(
                model=WHISPER_REPO, language=Language(bot["stt_language"])
            ),
        )
        llm = OpenAILLMService(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=f"http://127.0.0.1:{LLM_PORT}/v1",
            settings=OpenAILLMService.Settings(
                model=LLM_REPO,
                system_instruction=bot["system_instruction"],
                max_tokens=80,
                temperature=0.2,
            ),
        )
        tts = KokoroTTSService(
            model_path=str(KOKORO_DIR / "kokoro-v1.0.onnx"),
            voices_path=str(KOKORO_DIR / "voices-v1.0.bin"),
            settings=KokoroTTSService.Settings(
                voice=bot["tts_voice"], language=Language(bot["tts_language"].split("-")[0])
            ),
        )
        tool_schemas = []
        for spec in scenario.get("tools", []):
            result = spec["result"]

            async def handler(params: FunctionCallParams, result: Any = result) -> None:
                await params.result_callback(result)

            tool_schemas.append(
                FunctionSchema(
                    name=spec["name"],
                    description=spec["description"],
                    properties=spec["properties"],
                    required=spec["required"],
                    handler=handler,
                )
            )
        context = (
            LLMContext(tools=ToolsSchema(standard_tools=tool_schemas))
            if tool_schemas
            else LLMContext()
        )
        pair = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6))
            ),
        )
        transport = ScriptedTransport(
            TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=IN_RATE,
                audio_out_sample_rate=OUT_RATE,
            )
        )
        audio_buffer = AudioBufferProcessor(
            sample_rate=IN_RATE, num_channels=2, auto_start_recording=True
        )
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                pair.user(),
                llm,
                tts,
                transport.output(),
                audio_buffer,
                pair.assistant(),
            ]
        )

        activity = BotActivity(log)
        conv = EvalConversationObserver()
        conversation: list[dict[str, Any]] = []
        wav: dict[str, Any] = {}

        @conv.event_handler("on_conversation_ended")
        async def _ended(_o: Any, message: EvalConversationEndedMessage) -> None:
            conversation.extend(message.conversation)

        @audio_buffer.event_handler("on_audio_data")
        async def _audio(_b: Any, audio: bytes, sample_rate: int, channels: int) -> None:
            wav.setdefault("chunks", []).append(audio)
            wav["rate"], wav["channels"] = sample_rate, channels

        @pair.user().event_handler("on_user_turn_started")
        async def _uts(_a: Any, strategy: Any) -> None:
            log.add("user_aggregator", "on_user_turn_started", strategy=type(strategy).__name__)

        @pair.user().event_handler("on_user_turn_stopped")
        async def _utp(_a: Any, strategy: Any, message: Any) -> None:
            log.add(
                "user_aggregator",
                "on_user_turn_stopped",
                strategy=type(strategy).__name__,
                message=message,
            )

        @pair.assistant().event_handler("on_assistant_turn_started")
        async def _ats(_a: Any) -> None:
            log.add("assistant_aggregator", "on_assistant_turn_started")

        @pair.assistant().event_handler("on_assistant_turn_stopped")
        async def _atp(_a: Any, message: Any) -> None:
            log.add("assistant_aggregator", "on_assistant_turn_stopped", message=message)

        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=IN_RATE,
                audio_out_sample_rate=OUT_RATE,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[activity, conv],
            enable_tracing=True,
            enable_turn_tracking=True,
            conversation_id=f"capture-{sid}",
            additional_span_attributes={"capture.scenario": sid},
        )
        tto = worker.turn_tracking_observer
        if tto is None:
            raise RuntimeError("turn tracking observer is disabled")

        @tto.event_handler("on_turn_started")
        async def _ts(_o: Any, turn_number: int) -> None:
            log.add("turn_tracking_observer", "on_turn_started", turn_number=turn_number)

        @tto.event_handler("on_turn_ended")
        async def _te(_o: Any, turn_number: int, duration: float, was_interrupted: bool) -> None:
            log.add(
                "turn_tracking_observer",
                "on_turn_ended",
                turn_number=turn_number,
                duration=duration,
                was_interrupted=was_interrupted,
            )

        ready = asyncio.Event()

        @worker.event_handler("on_pipeline_started")
        async def _started(_w: Any, *_a: Any) -> None:
            ready.set()

        feed = asyncio.create_task(feeder(transport, turns, activity, worker, ready))
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
        await feed

        provider.force_flush()  # type: ignore[attr-defined]
        spans = list(exporter.get_finished_spans())
    finally:
        llm_proc.terminate()
        llm_proc.wait(timeout=30)

    files = {
        "f1_spans.otlp.json": json.dumps(spans_to_otlp_json(spans), indent=2, ensure_ascii=False),
        "f4_conversation.json": json.dumps(conversation, indent=2, ensure_ascii=False),
        "f7_turn_events.jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in log.rows),
    }
    for name, text in files.items():
        (out / name).write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    pcm = np.frombuffer(b"".join(wav["chunks"]), dtype=np.int16).reshape(-1, wav["channels"])
    sf.write(out / "f6_stereo.wav", pcm, wav["rate"], subtype="PCM_16")
    write_provenance(out, scenario_path, scenario, models, spans)
    return {"spans": len(spans), "events": len(log.rows), "conversation": conversation}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    result = asyncio.run(run(args.scenario, args.out, args.scratch))
    print(json.dumps({k: v for k, v in result.items() if k != "conversation"}))


if __name__ == "__main__":
    main()
