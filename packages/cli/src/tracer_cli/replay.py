# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The replay engine of ``--stt fake:<dir>``: recorded Sarvam bodies, no request, no usage."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.stt import (
    SttConfigurationError,
    SttContext,
    SttOutcome,
    SttResponseInvalid,
)
from tracer_evalproviders.sarvam.mapping import map_responses
from tracer_evalproviders.sarvam.models import (
    MAX_RESPONSE_BYTES,
    ChunkResponse,
    SarvamResponseShapeError,
    parse_response,
)

__all__ = ["REPLAY_FILE_FORMAT", "REPLAY_MODEL", "REPLAY_PROVIDER", "ReplayStt"]

REPLAY_FILE_FORMAT: Final = "chunk-{index:04d}.json"
REPLAY_PROVIDER: Final = "replay"
REPLAY_MODEL: Final = "recorded-responses"
_NAME: Final = re.compile(r"chunk-([0-9]{4})\.json")


class ReplayStt:
    """Answers each chunk from ``chunk-NNNN.json`` in a directory."""

    provider = REPLAY_PROVIDER
    model = REPLAY_MODEL

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        if not self._directory.is_dir():
            raise SttConfigurationError(reason="replay_directory_unreadable")

    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome:
        try:
            names = [p.name for p in self._directory.iterdir()]
        except OSError:
            raise SttConfigurationError(reason="replay_directory_unreadable") from None
        found = {int(m.group(1)) for n in names if (m := _NAME.fullmatch(n))}
        if any(index >= len(plan.chunks) for index in found):
            raise SttConfigurationError(reason="replay_extra_file")
        answered: list[ChunkResponse] = []
        for chunk in plan.chunks:
            path = self._directory / REPLAY_FILE_FORMAT.format(index=chunk.index)
            failure: SttResponseInvalid | None = None
            body = b""
            try:
                size = path.stat().st_size
                if size > MAX_RESPONSE_BYTES:
                    failure = SttResponseInvalid(chunk_index=chunk.index, reason="body_too_large")
                else:
                    body = path.read_bytes()
            except OSError:
                failure = SttResponseInvalid(chunk_index=chunk.index, reason="replay_file_missing")
            if failure is None:
                try:
                    response = parse_response(body)
                except SarvamResponseShapeError as error:
                    failure = SttResponseInvalid(chunk_index=chunk.index, reason=error.reason)
            if failure is not None:
                return SttOutcome(None, (), (), (), failure)
            answered.append(ChunkResponse(chunk.index, response, None, 1))
        outcome = map_responses(plan, answered, model=REPLAY_MODEL, context=context)
        if outcome.transcript is None:
            return outcome
        transcript = outcome.transcript.model_copy(update={"provider": REPLAY_PROVIDER})
        return SttOutcome(transcript, outcome.turns, (), outcome.warnings, None)
