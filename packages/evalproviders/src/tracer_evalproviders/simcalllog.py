# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The call log of a simulated run (ADR 0024 amendment A8)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tracer_evalproviders.calllog import safe_request_id

__all__ = ["SIM_CALL_LOG_NAME", "SimCallLog", "SttCallLogView"]

SIM_CALL_LOG_NAME: Final = "sim_call_log.jsonl"
_SAFE_KIND: Final = re.compile(r"[a-z0-9_]{1,64}")
_SAFE_SECONDS: Final = re.compile(r"[0-9]{1,20}(?:\.[0-9]{1,18})?")
_COUNT_KEY: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STAGES: Final = ("tts", "llm", "stt", "judge")
_DIALECTS: Final = ("pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1")
_END_REASONS: Final = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_int(value: object) -> bool:
    return type(value) is int


class SimCallLog:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._clock = clock
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        self._descriptor = descriptor

    @classmethod
    def open(
        cls, out_dir: str | os.PathLike[str], *, clock: Callable[[], datetime] = _utc_now
    ) -> SimCallLog:
        directory = Path(out_dir)
        try:
            directory.mkdir(mode=0o700, parents=True)
            directory.chmod(0o700)
        except FileExistsError:
            pass
        return cls(directory / SIM_CALL_LOG_NAME, clock=clock)

    @property
    def path(self) -> Path:
        return self._path

    def for_stt(self) -> SttCallLogView:
        return SttCallLogView(self)

    def close(self) -> None:
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1

    def _stamp(self) -> str:
        return self._clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _emit(self, line: dict[str, object]) -> None:
        data = (json.dumps(line, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        view = memoryview(data)
        while view:
            view = view[os.write(self._descriptor, view) :]
        os.fsync(self._descriptor)

    def _request_line(
        self,
        event: str,
        stage: object,
        index: object,
        attempt: object,
        bytes_sent: object,
        characters: object,
        audio_seconds: object,
        status: int | None,
        error_kind: str | None,
        elapsed_ms: object,
        request_id: object,
    ) -> None:
        if stage not in _STAGES:
            raise ValueError("stage: not an accepted value")
        if not (_is_int(index) and _is_int(attempt) and _is_int(bytes_sent)):
            raise TypeError("a call log line holds integers only")
        if characters is not None and not _is_int(characters):
            raise TypeError("characters must be an integer")
        if audio_seconds is not None:
            if not isinstance(audio_seconds, str):
                raise TypeError("audio_seconds must be a decimal string")
            if not _SAFE_SECONDS.fullmatch(audio_seconds):
                raise ValueError("audio_seconds must be a decimal string")
        if stage == "tts" and (characters is None or audio_seconds is not None):
            raise ValueError("a tts line counts characters and nothing else")
        if stage == "stt" and (audio_seconds is None or characters is not None):
            raise ValueError("an stt line counts audio seconds and nothing else")
        if stage in ("llm", "judge") and (characters is not None or audio_seconds is not None):
            raise ValueError("an llm or judge line counts neither")
        for number in (status, elapsed_ms):
            if number is not None and not _is_int(number):
                raise TypeError("a call log line holds integers only")
        if event == "after" and not _is_int(elapsed_ms):
            raise TypeError("a call log line holds integers only")
        self._emit(
            {
                "at": self._stamp(),
                "event": event,
                "stage": stage,
                "index": index,
                "attempt": attempt,
                "bytes_sent": bytes_sent,
                "characters": characters,
                "audio_seconds": audio_seconds,
                "status": status,
                "error_kind": error_kind
                if error_kind and _SAFE_KIND.fullmatch(error_kind)
                else None,
                "elapsed_ms": elapsed_ms,
                "request_id": safe_request_id(request_id),
            }
        )

    def before(
        self,
        *,
        stage: str,
        index: int,
        attempt: int,
        bytes_sent: int,
        characters: int | None = None,
        audio_seconds: str | None = None,
    ) -> None:
        self._request_line(
            "before",
            stage,
            index,
            attempt,
            bytes_sent,
            characters,
            audio_seconds,
            None,
            None,
            None,
            None,
        )

    def after(
        self,
        *,
        stage: str,
        index: int,
        attempt: int,
        bytes_sent: int,
        status: int | None,
        error_kind: str | None,
        elapsed_ms: int,
        request_id: str | None,
        characters: int | None = None,
        audio_seconds: str | None = None,
    ) -> None:
        self._request_line(
            "after",
            stage,
            index,
            attempt,
            bytes_sent,
            characters,
            audio_seconds,
            status,
            error_kind,
            elapsed_ms,
            request_id,
        )

    def connect(self, *, dialect: str) -> None:
        if dialect not in _DIALECTS:
            raise ValueError("dialect: not an accepted value")
        self._emit({"at": self._stamp(), "event": "connect", "stage": "call", "dialect": dialect})

    def end(self, *, end_reason: str, counts: Mapping[str, int]) -> None:
        if end_reason not in _END_REASONS:
            raise ValueError("end_reason: not an accepted value")
        if not isinstance(counts, Mapping):
            raise TypeError("counts must be a mapping")
        for key, value in counts.items():
            if not (isinstance(key, str) and _COUNT_KEY.fullmatch(key)):
                raise ValueError("counts: not a machine key")
            if not _is_int(value) or value < 0:
                raise ValueError("counts: not a non-negative integer")
        self._emit(
            {
                "at": self._stamp(),
                "event": "end",
                "stage": "call",
                "end_reason": end_reason,
                "counts": dict(counts),
            }
        )


class SttCallLogView:
    """The shape of ``CallLog`` over the shared log, for the STT client."""

    def __init__(self, log: SimCallLog) -> None:
        self._log = log

    def before(
        self, *, chunk_index: int, attempt: int, bytes_sent: int, audio_seconds: str
    ) -> None:
        self._log.before(
            stage="stt",
            index=chunk_index,
            attempt=attempt,
            bytes_sent=bytes_sent,
            audio_seconds=audio_seconds,
        )

    def after(
        self,
        *,
        chunk_index: int,
        attempt: int,
        bytes_sent: int,
        audio_seconds: str,
        status: int | None,
        error_kind: str | None,
        elapsed_ms: int,
        request_id: str | None,
    ) -> None:
        self._log.after(
            stage="stt",
            index=chunk_index,
            attempt=attempt,
            bytes_sent=bytes_sent,
            audio_seconds=audio_seconds,
            status=status,
            error_kind=error_kind,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
        )

    def close(self) -> None:
        return None
