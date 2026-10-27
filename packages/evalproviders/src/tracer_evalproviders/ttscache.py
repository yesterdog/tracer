# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The content-addressed TTS cache (ADR 0024 amendment A9)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Final

__all__ = ["TTS_CACHE_DIR_NAME", "TtsCache", "tts_cache_key"]

TTS_CACHE_DIR_NAME: Final = "tts-cache"
_KEY: Final = re.compile(r"[0-9a-f]{64}")
_MAX_ENTRY_BYTES: Final = 1 << 28


def tts_cache_key(
    *,
    provider: str,
    model: str,
    speaker: str,
    language_code: str,
    sample_rate_hz: int,
    codec: str,
    pace: str,
    text: str,
) -> str:
    for name, value in (
        ("provider", provider),
        ("model", model),
        ("speaker", speaker),
        ("language_code", language_code),
        ("codec", codec),
        ("pace", pace),
        ("text", text),
    ):
        if type(value) is not str:
            raise TypeError(f"{name} must be a string")
    if type(sample_rate_hz) is not int:
        raise TypeError("sample_rate_hz must be an int")
    document = {
        "provider": provider,
        "model": model,
        "speaker": speaker,
        "language_code": language_code,
        "sample_rate_hz": sample_rate_hz,
        "codec": codec,
        "pace": pace,
        "text": text,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError("key must be a string")
    if not _KEY.fullmatch(key):
        raise ValueError("key: not a lower-case hex sha-256")
    return key


class TtsCache:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @classmethod
    def open(cls, out_dir: str | os.PathLike[str]) -> TtsCache:
        out = Path(out_dir)
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = out / TTS_CACHE_DIR_NAME
        try:
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        except FileExistsError:
            pass
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        os.close(descriptor)
        return cls(directory)

    @property
    def path(self) -> Path:
        return self._dir

    def get(self, key: str) -> bytes | None:
        name = _check(key)
        try:
            descriptor = os.open(self._dir / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("a cache entry is not a regular file")
            if os.fstat(descriptor).st_size > _MAX_ENTRY_BYTES:
                raise OSError("a cache entry is larger than any audio the engine accepts")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read(_MAX_ENTRY_BYTES)
        finally:
            os.close(descriptor)

    def put(self, key: str, audio: bytes) -> None:
        name = _check(key)
        if not isinstance(audio, bytes):
            raise TypeError("audio must be bytes")
        if not audio:
            raise ValueError("audio: empty")
        descriptor = os.open(
            self._dir / name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("a cache entry is not a regular file")
            os.fchmod(descriptor, 0o600)
            os.ftruncate(descriptor, 0)
            view = memoryview(audio)
            while view:
                view = view[os.write(descriptor, view) :]
        finally:
            os.close(descriptor)
