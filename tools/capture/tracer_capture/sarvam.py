# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Minimal Sarvam REST client for the F3 capture: call log, call cap, secret scrubbing.

The API key is read from a git-ignored dotenv file into memory only. It is never written to a
log, a fixture or stdout; every saved artifact is checked against it before it is written.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE_URL = "https://api.sarvam.ai"
AUTH_HEADER = "api-subscription-key"
CALL_CAP = 60
_DROP_RESPONSE_HEADERS = {"set-cookie"}


def load_key(env_file: Path, name: str = "SARVAM_API_KEY") -> str:
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                return value
    raise SystemExit(f"{name} not set in {env_file}")


@dataclass(frozen=True)
class CallResult:
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_s: float
    requested_at_utc: str


class SarvamClient:
    def __init__(self, key: str, call_log: Path, cap: int = CALL_CAP) -> None:
        self._key = key
        self._log = call_log
        self._cap = cap
        self._http = httpx.Client(base_url=BASE_URL, timeout=60.0)

    @property
    def calls_made(self) -> int:
        if not self._log.exists():
            return 0
        return sum(1 for ln in self._log.read_text(encoding="utf-8").splitlines() if ln.strip())

    def assert_clean(self, *blobs: bytes | str) -> None:
        needle = self._key.encode()
        for blob in blobs:
            data = blob if isinstance(blob, bytes) else blob.encode("utf-8")
            if needle in data:
                raise SystemExit("refusing to save: a secret value appears in the artifact")

    def call(
        self,
        *,
        endpoint: str,
        clip_id: str,
        language_requested: str | None,
        case: str,
        data: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        file: tuple[str, bytes, str] | None = None,
        key_override: str | None = None,
    ) -> CallResult:
        if self.calls_made >= self._cap:
            raise SystemExit(f"call cap {self._cap} reached")
        headers = {AUTH_HEADER: key_override if key_override is not None else self._key}
        requested_at = dt.datetime.now(dt.UTC).isoformat()
        t0 = time.monotonic()
        files = {"file": file} if file is not None else None
        resp = self._http.post(endpoint, headers=headers, data=data, files=files, json=json_body)
        elapsed = time.monotonic() - t0
        out_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
        }
        record = {
            "timestamp_utc": requested_at,
            "endpoint": endpoint,
            "case": case,
            "clip_id": clip_id,
            "language_requested": language_requested,
            "status": resp.status_code,
            "elapsed_s": round(elapsed, 3),
        }
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return CallResult(resp.status_code, out_headers, resp.content, elapsed, requested_at)
