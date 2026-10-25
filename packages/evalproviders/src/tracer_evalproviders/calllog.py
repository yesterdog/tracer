# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The local call log (ADR 0024 rule 15): two JSON lines per attempt, no text, no key.

A ``before`` line is on disk before the request is sent, so a crash leaves a trace of the attempt.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

__all__ = ["CALL_LOG_NAME", "CallLog", "safe_request_id"]

CALL_LOG_NAME: Final = "sarvam_call_log.jsonl"
_SAFE_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SAFE_KIND: Final = re.compile(r"[a-z0-9_]{1,64}")
_SAFE_SECONDS: Final = re.compile(r"[0-9]{1,20}(?:\.[0-9]{1,18})?")
_FILE_MODE: Final = 0o600


def safe_request_id(value: object) -> str | None:
    """``value`` when it is a request id that is safe to write, else ``None``."""
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class CallLog:
    """Append-only, owner-only JSON lines in one file."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._clock = clock
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE)
        os.fchmod(descriptor, _FILE_MODE)
        self._descriptor = descriptor

    @classmethod
    def open(
        cls, out_dir: str | os.PathLike[str], *, clock: Callable[[], datetime] = _utc_now
    ) -> CallLog:
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory / CALL_LOG_NAME, clock=clock)

    @property
    def path(self) -> Path:
        return self._path

    def before(
        self, *, chunk_index: int, attempt: int, bytes_sent: int, audio_seconds: str
    ) -> None:
        self._write(
            "before",
            chunk_index,
            attempt,
            bytes_sent,
            audio_seconds,
            status=None,
            error_kind=None,
            elapsed_ms=None,
            request_id=None,
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
        self._write(
            "after",
            chunk_index,
            attempt,
            bytes_sent,
            audio_seconds,
            status=status,
            error_kind=error_kind,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
        )

    def close(self) -> None:
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1

    def _write(
        self,
        phase: str,
        chunk_index: int,
        attempt: int,
        bytes_sent: int,
        audio_seconds: str,
        *,
        status: int | None,
        error_kind: str | None,
        elapsed_ms: int | None,
        request_id: str | None,
    ) -> None:
        numbers = (chunk_index, attempt, bytes_sent, status, elapsed_ms)
        if not all(n is None or _is_int(n) for n in numbers):
            raise TypeError("a call log line holds integers only")
        if not _SAFE_SECONDS.fullmatch(audio_seconds):
            raise ValueError("audio_seconds must be a decimal string")
        moment = self._clock().astimezone(UTC)
        line = {
            "at": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "phase": phase,
            "chunk": chunk_index,
            "attempt": attempt,
            "bytes_sent": bytes_sent,
            "audio_seconds": audio_seconds,
            "status": status,
            "error_kind": error_kind if error_kind and _SAFE_KIND.fullmatch(error_kind) else None,
            "elapsed_ms": elapsed_ms,
            "request_id": safe_request_id(request_id),
        }
        data = (json.dumps(line, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        view = memoryview(data)
        while view:
            view = view[os.write(self._descriptor, view) :]
        os.fsync(self._descriptor)
