# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Sarvam engine: the ``SttEngine`` of tracer-evalcore over the client (ADR 0024 rules 1, 6
and 11)."""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import ClassVar

import httpx

from tracer_evalcore.audio.chunker import ChunkPlan
from tracer_evalcore.stt import SttConfigurationError, SttContext, SttError, SttOutcome, UsageSink
from tracer_evalproviders.calllog import CallLog
from tracer_evalproviders.sarvam.client import SarvamClient
from tracer_evalproviders.sarvam.mapping import map_responses, map_usage_records
from tracer_evalproviders.sarvam.models import DEFAULT_MODEL, ChunkResponse, language_code_for
from tracer_evalproviders.secrets import load_api_key
from tracer_evalschema.usage import UsageRecord

__all__ = ["SarvamStt", "create_sarvam_stt"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SarvamStt:
    """Sends the chunks of a plan one after the other; all or nothing."""

    provider = "sarvam"
    streams_usage: ClassVar[bool] = True

    def __init__(self, client: SarvamClient, *, call_log: CallLog | None = None) -> None:
        """``call_log``, when given, belongs to the engine and is closed with it."""
        self._client = client
        self._owned_log = call_log

    @property
    def model(self) -> str:
        return self._client.model

    def transcribe(
        self, plan: ChunkPlan, context: SttContext, *, on_usage: UsageSink | None = None
    ) -> SttOutcome:
        """The outcome of the plan; the first chunk that fails ends the run and is returned as
        ``failure`` together with the usage of the chunks that were answered.

        ``on_usage`` is handed each answered chunk's record before the next request is built; the
        records are the ones of the outcome, with the same ids. Any other exception, including
        one of ``on_usage``, leaves unchanged and no further chunk is sent.
        """
        language_code_for(context.configured_language)  # refused before any request
        if plan.channel is not None and any(c.sample_rate_hz == 8000 for c in plan.chunks):
            raise SttConfigurationError(reason="sample_rate_not_accepted")
        answered: list[ChunkResponse] = []
        records: list[UsageRecord] = []
        failure: SttError | None = None
        for chunk in plan.chunks:
            try:
                response = self._client.send_chunk(chunk)
            except SttError as error:
                failure = error
                break
            answered.append(response)
            (record,) = map_usage_records(plan, [response], model=self.model, context=context)
            records.append(record)
            if on_usage is not None:
                on_usage(record)
        if failure is not None:
            return SttOutcome(
                transcript=None, turns=(), usage=tuple(records), warnings=(), failure=failure
            )
        outcome = map_responses(plan, answered, model=self.model, context=context)
        return dataclasses.replace(outcome, usage=tuple(records))

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
