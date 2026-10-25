# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Sarvam engine: the ``SttEngine`` of tracer-evalcore over the client (ADR 0024 rules 1, 6
and 11)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import TracebackType

import httpx

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.stt import SttContext, SttError, SttOutcome
from tracer_evalproviders.calllog import CallLog
from tracer_evalproviders.sarvam.client import SarvamClient
from tracer_evalproviders.sarvam.mapping import map_responses, map_usage_records
from tracer_evalproviders.sarvam.models import DEFAULT_MODEL, ChunkResponse, language_code_for
from tracer_evalproviders.secrets import load_api_key

__all__ = ["SarvamStt", "create_sarvam_stt"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SarvamStt:
    """Sends the chunks of a plan one after the other; all or nothing."""

    provider = "sarvam"

    def __init__(self, client: SarvamClient, *, call_log: CallLog | None = None) -> None:
        """``call_log``, when given, belongs to the engine and is closed with it."""
        self._client = client
        self._owned_log = call_log

    @property
    def model(self) -> str:
        return self._client.model

    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome:
        """The outcome of the plan; the first chunk that fails ends the run and is returned as
        ``failure`` together with the usage of the chunks that were answered."""
        language_code_for(context.configured_language)  # refused before any request
        answered: list[ChunkResponse] = []
        failure: SttError | None = None
        for chunk in plan.chunks:
            try:
                answered.append(self._client.send_chunk(chunk))
            except SttError as error:
                failure = error
                break
        if failure is not None:
            usage = map_usage_records(plan, answered, model=self.model, context=context)
            return SttOutcome(transcript=None, turns=(), usage=usage, warnings=(), failure=failure)
        return map_responses(plan, answered, model=self.model, context=context)

    def close(self) -> None:
        self._client.close()
        if self._owned_log is not None:
            self._owned_log.close()

    def __enter__(self) -> SarvamStt:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def create_sarvam_stt(
    *,
    out_dir: str | os.PathLike[str],
    model: str = DEFAULT_MODEL,
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    clock: Callable[[], datetime] = _utc_now,
) -> SarvamStt:
    """An engine with the key and the call log in ``out_dir``.

    The key is read first: a missing key stops here, before ``out_dir`` exists and before any
    request.
    """
    key = load_api_key(environ=environ, env_file=env_file)
    log = CallLog.open(out_dir, clock=clock)
    client = SarvamClient(
        key, call_log=log, model=model, transport=transport, sleep=sleep, monotonic=monotonic
    )
    return SarvamStt(client, call_log=log)
