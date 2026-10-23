# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A transport with no network or audio device: a scripted caller in, bot audio out.

Bot audio goes through Pipecat's normal output pacing (real time) and is then dropped, so
interruptions behave as they do on a real transport.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams


class FeedInputTransport(BaseInputTransport):
    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def feed(self, pcm16: bytes) -> None:
        await self.push_audio_frame(
            InputAudioRawFrame(audio=pcm16, sample_rate=self.sample_rate, num_channels=1)
        )


class NullOutputTransport(BaseOutputTransport):
    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        # A real transport blocks while the audio plays; do the same so the bot can be interrupted.
        await asyncio.sleep(len(frame.audio) / (2 * frame.num_channels * frame.sample_rate))
        return True


class ScriptedTransport(BaseTransport):
    def __init__(self, params: TransportParams) -> None:
        super().__init__()
        self._in = FeedInputTransport(params)
        self._out = NullOutputTransport(params)

    def input(self) -> FeedInputTransport:
        return self._in

    def output(self) -> NullOutputTransport:
        return self._out
