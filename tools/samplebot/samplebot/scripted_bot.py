# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``scripted_bot``: a Pipecat 1.14.0 pipeline with no model, for the loopback interop tests.

    python -m samplebot.scripted_bot --dialect DIALECT
        --scenario FILE --sample-rate N --host 127.0.0.1 --port 0

A timer on the arrival of active caller frames decides that the caller has finished; the replies
are the sines of the scenario, started after the known delays. One dialect per process, calls one
after another. Stdout carries one JSON object per line (``ready``, ``twilio_start``,
``call_summary``); the logs go to stderr. Contract: docs/phase4/api-surface-batch-f.md section 1.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket
from loguru import logger
from pipecat.frames.frames import EndFrame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from samplebot.end_of_caller import EndOfCallerTimer
from samplebot.replies import tone_pcm_s16le
from samplebot.scenario import Reply, Scenario, ScenarioError, parse_scenario
from samplebot.summary import CallTally

PROTOBUF = "pipecat-protobuf/1"
TWILIO = "twilio-media-stream/1"
WEBRTC = "smallwebrtc/1"
DIALECTS = (PROTOBUF, TWILIO, WEBRTC)
ROUTES = {PROTOBUF: "/ws-client", TWILIO: "/ws", WEBRTC: "/api/offer"}
PIPECAT_VERSION = "1.14.0"
FRAME_MS = 20


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, sort_keys=True, ensure_ascii=True), flush=True)


class Tap(FrameProcessor):
    """Counts the active audio that arrives (frames of digital silence are not counted)."""

    def __init__(self, tally: CallTally) -> None:
        super().__init__()
        self._tally = tally

    async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and any(frame.audio):
            self._tally.on_audio(frame.audio, frame.sample_rate)
        await self.push_frame(frame, direction)


class Call:
    """One call: its tally, the replies still to say, and the pipeline that carries them."""

    def __init__(self, number: int, dialect: str, scenario: Scenario, rate: int) -> None:
        self.number = number
        self.dialect = dialect
        self.scenario = scenario
        self.rate = rate
        self.tally = CallTally()
        self.turns_ended = 0
        self.task: PipelineTask | None = None
        self.caller_gone = False
        self.bot_ended = False
        self._saying: set[asyncio.Task[None]] = set()

    def on_end_of_caller(self) -> None:
        self.tally.on_turn_end()
        k = self.turns_ended
        self.turns_ended += 1
        if k < len(self.scenario.replies) and self.task is not None:
            last = k == len(self.scenario.replies) - 1
            said = asyncio.ensure_future(self.say(self.scenario.replies[k], last))
            self._saying.add(said)
            said.add_done_callback(self._saying.discard)

    async def say(self, reply: Reply, last: bool) -> None:
        await asyncio.sleep(reply.delay_ms / 1000)
        task = self.task
        if task is None or self.caller_gone:
            return
        self.tally.on_reply_started()
        pcm = tone_pcm_s16le(hz=reply.hz, ms=reply.ms, level=reply.level, sample_rate_hz=self.rate)
        step = self.rate * FRAME_MS // 1000 * 2
        loop = asyncio.get_running_loop()
        start = loop.time()
        for index, offset in enumerate(range(0, len(pcm), step)):
            if self.caller_gone:
                return
            await task.queue_frame(
                OutputAudioRawFrame(
                    audio=pcm[offset : offset + step], sample_rate=self.rate, num_channels=1
                )
            )
            await asyncio.sleep(max(0.0, start + (index + 1) * FRAME_MS / 1000 - loop.time()))
        if last and self.scenario.after_replies == "hang_up" and not self.caller_gone:
            self.bot_ended = True
            await task.queue_frame(EndFrame())

    async def run(self, transport: Any) -> None:
        pipeline = Pipeline(
            [
                transport.input(),
                Tap(self.tally),
                EndOfCallerTimer(
                    end_of_caller_ms=self.scenario.end_of_caller_ms,
                    on_end_of_caller=self.on_end_of_caller,
                ),
                transport.output(),
            ]
        )
        task = PipelineTask(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=self.rate, audio_out_sample_rate=self.rate),
        )
        self.task = task

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport: Any, _client: Any) -> None:
            self.caller_gone = True
            await task.cancel()

        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(task)
        try:
            await runner.run()
        finally:
            for said in list(self._saying):
                said.cancel()
            self.task = None

    def finish(self) -> None:
        ended = "bot_hang_up" if self.bot_ended and not self.caller_gone else "caller_closed"
        emit(self.tally.summary(call=self.number, dialect=self.dialect, ended_by=ended))


class Bot:
    """The state of the process: the calls so far, one at a time."""

    def __init__(self, args: argparse.Namespace, scenario: Scenario) -> None:
        self.args = args
        self.scenario = scenario
        self.calls = 0
        self.lock = asyncio.Lock()

    def new_call(self) -> Call:
        self.calls += 1
        return Call(self.calls, self.args.dialect, self.scenario, self.args.sample_rate)


def websocket_params(serializer: Any) -> FastAPIWebsocketParams:
    return FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=serializer,
    )


def build_app(bot: Bot) -> FastAPI:
    app = FastAPI()
    handler = SmallWebRTCRequestHandler(ice_servers=None)
    running: set[asyncio.Task[None]] = set()

    @app.websocket("/ws-client")
    async def ws_client(websocket: WebSocket) -> None:
        if bot.args.dialect != PROTOBUF:
            await websocket.close()
            return
        async with bot.lock:
            await websocket.accept()
            call = bot.new_call()
            transport = FastAPIWebsocketTransport(
                websocket=websocket, params=websocket_params(ProtobufFrameSerializer())
            )
            await call.run(transport)
            call.finish()

    @app.websocket("/ws")
    async def ws_twilio(websocket: WebSocket) -> None:
        if bot.args.dialect != TWILIO:
            await websocket.close()
            return
        async with bot.lock:
            await websocket.accept()
            call = bot.new_call()
            # the identifiers are the ones the caller sent in `start` (never invented here)
            _kind, data = await parse_telephony_websocket(websocket)
            emit(
                {
                    "event": "twilio_start",
                    "call": call.number,
                    "stream_sid": data["stream_id"],
                    "call_sid": data["call_id"],
                    "custom_parameters": data.get("body", {}),
                }
            )
            serializer = TwilioFrameSerializer(
                stream_sid=data["stream_id"],
                call_sid=data["call_id"],
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
            )
            transport = FastAPIWebsocketTransport(
                websocket=websocket, params=websocket_params(serializer)
            )
            await call.run(transport)
            call.finish()

    async def serve_webrtc(connection: Any) -> None:
        async with bot.lock:
            call = bot.new_call()
            transport = SmallWebRTCTransport(
                webrtc_connection=connection,
                params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
            )
            await call.run(transport)
            call.finish()

    @app.post("/api/offer")
    async def offer(request: SmallWebRTCRequest) -> Any:
        if bot.args.dialect != WEBRTC:
            return {"detail": "not served"}

        async def on_connection(connection: Any) -> None:
            started = asyncio.ensure_future(serve_webrtc(connection))
            running.add(started)
            started.add_done_callback(running.discard)

        return await handler.handle_web_request(request, on_connection)

    @app.patch("/api/offer")
    async def candidates(request: SmallWebRTCPatchRequest) -> dict[str, str]:
        await handler.handle_patch_request(request)
        return {"status": "success"}

    return app


def url_for(dialect: str, host: str, port: int) -> str:
    shown = f"[{host}]" if ":" in host else host
    scheme = "http" if dialect == WEBRTC else "ws"
    return f"{scheme}://{shown}:{port}{ROUTES[dialect]}"


class QuietServer(uvicorn.Server):
    """uvicorn re-raises the signal that stopped it, so the process would die of SIGTERM (exit 143);
    this server leaves the signals to ``serve`` and a stop is a normal return (exit 0)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield

    def stop(self) -> None:
        self.should_exit = True


async def serve(args: argparse.Namespace, scenario: Scenario) -> None:
    config = uvicorn.Config(
        build_app(Bot(args, scenario)), host=args.host, port=args.port, log_level="warning"
    )
    server = QuietServer(config)
    loop = asyncio.get_running_loop()
    for stopping in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stopping, server.stop)
    serving = asyncio.ensure_future(server.serve())
    while not server.started and not serving.done():
        await asyncio.sleep(0.02)
    if serving.done():
        await serving
        return
    port = server.servers[0].sockets[0].getsockname()[1]
    ready: dict[str, Any] = {
        "event": "ready",
        "dialect": args.dialect,
        "url": url_for(args.dialect, args.host, port),
        "pipecat": PIPECAT_VERSION,
    }
    if args.dialect == TWILIO:
        ready["auto_hang_up"] = False
        ready["credentials"] = False
    emit(ready)
    await serving


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripted_bot")
    parser.add_argument("--dialect", required=True, choices=list(DIALECTS))
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--sample-rate", required=True, type=int)
    parser.add_argument("--host", required=True, choices=["127.0.0.1", "::1", "localhost"])
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    try:
        scenario = parse_scenario(args.scenario.read_text(encoding="utf-8"))
    except ScenarioError as error:
        print(f"scenario refused: {error.code}", file=sys.stderr)
        return 2
    asyncio.run(serve(args, scenario))
    return 0


if __name__ == "__main__":
    sys.exit(main())
