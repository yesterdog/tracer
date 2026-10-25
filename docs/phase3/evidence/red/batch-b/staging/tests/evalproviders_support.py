# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch B tests (contract docs/phase3/api-surface-batch-b.md).

Nothing of the batch B packages is imported at module level: each module is loaded lazily by the
accessor functions below, so that before the code exists every test fails on its own with
``ModuleNotFoundError`` and the directory still collects.

No test reaches a network: requests go to ``ScriptedTransport`` (an ``httpx.MockTransport``) and
the key is the canary ``CANARY_KEY``, built from parts, never a real key.
"""

from __future__ import annotations

import importlib
import json
import re
import weakref
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from tracer_evalcore.audio.chunker import Chunk, ChunkPlan, chunk_pcm
from tracer_evalschema.ids import ArtifactId, RunId
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
CAPTURES = ROOT / "packages" / "evalschema" / "tests" / "fixtures" / "real" / "sarvam-2026-10-23"
SRC = ROOT / "packages" / "evalproviders" / "src" / "tracer_evalproviders"

#: The canary key. Built from parts so that the literal never appears in this file, and it is not
#: a key of any account. Every channel that must not hold a key is searched for it.
CANARY_KEY = "-".join(["CANARYKEY", "".join(["zq", "7f", "c3", "d1"]), "".join(["9a", "e2", "b8"])])
#: A second canary: text of a response that must never reach a log or a record that it
#: does not belong to.
TEXT_CANARY = "".join(["TRANSCRIPT", "CANARY", "x9"])
MESSAGE_CANARY = "".join(["MESSAGE", "CANARY", "q4"])

UTC_NOW = datetime(2026, 10, 25, 9, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------- lazy modules


def stt() -> ModuleType:
    return importlib.import_module("tracer_evalcore.stt")


def models() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.models")


def client_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.client")


def engine_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.engine")


def mapping_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.sarvam.mapping")


def secrets_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.secrets")


def calllog_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.calllog")


def cost_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.cost")


def dryrun_mod() -> ModuleType:
    return importlib.import_module("tracer_evalproviders.dryrun")


# ---------------------------------------------------------------------------- the captures


class Capture:
    """One recorded Sarvam call: the status, the headers and the body bytes as received."""

    def __init__(self, name: str) -> None:
        folder = CAPTURES / name
        self.name = name
        meta = json.loads((folder / "response_meta.json").read_text("utf-8"))
        self.status: int = meta["status"]
        self.headers: dict[str, str] = meta["headers"]
        self.body: bytes = (folder / "response_body.json").read_bytes()
        self.request: dict[str, Any] = json.loads((folder / "request.json").read_text("utf-8"))

    def response(self) -> httpx.Response:
        """The recorded response, with the headers that matter to the adapter."""
        keep = {
            k: v for k, v in self.headers.items() if k in {"date", "content-type", "x-request-id"}
        }
        return httpx.Response(self.status, headers=keep, content=self.body)


def capture_names(prefix: str) -> list[str]:
    return sorted(p.name for p in CAPTURES.iterdir() if p.is_dir() and p.name.startswith(prefix))


def json_response(
    document: Any,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    date: str | None = "Fri, 23 Oct 2026 13:08:26 GMT",
) -> httpx.Response:
    """A JSON response (``document`` is a JSON-able value or the body bytes themselves)."""
    body = document if isinstance(document, bytes) else json.dumps(document).encode("utf-8")
    merged = {"content-type": "application/json"}
    if date is not None:
        merged["date"] = date
    merged.update(headers or {})
    # header values are sent as UTF-8 bytes, so that a test can plant a non-ASCII value
    raw = [(k.encode("utf-8"), v.encode("utf-8")) for k, v in merged.items()]
    return httpx.Response(status, headers=raw, content=body)


def ok_body(
    text: str = "namaskar",
    *,
    request_id: str = "20261025_00000000-0000-4000-8000-000000000001",
    language: str | None = "mr-IN",
    probability: float | None = 1.0,
    phrases: Sequence[tuple[str, float, float]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"request_id": request_id, "transcript": text}
    if phrases is not None:
        body["timestamps"] = {
            "words": [p[0] for p in phrases],
            "start_time_seconds": [p[1] for p in phrases],
            "end_time_seconds": [p[2] for p in phrases],
        }
    body["language_code"] = language
    if probability is not None:
        body["language_probability"] = probability
    return body


def error_body(
    code: str = "invalid_request_error", message: str = "bad", rid: str = "r-1"
) -> bytes:
    return json.dumps({"error": {"message": message, "code": code, "request_id": rid}}).encode()


# -------------------------------------------------------------------------------- audio


def pcm(frames: int, *, seed: int = 7, quiet: slice | None = None) -> bytes:
    """Deterministic 16-bit mono noise of ``frames`` samples (an LCG), with an optional silent
    range of sample indexes."""
    import struct

    state = seed
    out = bytearray()
    for index in range(frames):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        value = ((state >> 8) % 2001) - 1000
        if quiet is not None and quiet.start <= index < quiet.stop:
            value = 0
        out += struct.pack("<h", value)
    return bytes(out)


def small_plan(*, rate: int = 24000, ms: int = 2500, max_chunk_ms: int = 1000) -> ChunkPlan:
    """A plan of real chunks (default: 2.5 s at 24 kHz in three chunks of at most 1 s)."""
    frames = rate * ms // 1000
    return chunk_pcm(
        pcm(frames),
        sample_rate_hz=rate,
        channels=1,
        max_chunk_ms=max_chunk_ms,
        search_ms=200,
    )


def hand_plan(
    spans: Sequence[tuple[int, int]], *, rate: int = 24000, channels: int = 1
) -> ChunkPlan:
    """A plan built by hand from ``(start_frame, end_frame)`` pairs; the chunk files are empty
    (the mapping reads frames and the rate only)."""
    chunks = tuple(
        Chunk(
            index=i,
            start_frame=a,
            end_frame=b,
            sample_rate_hz=rate,
            channels=channels,
            wav=b"",
        )
        for i, (a, b) in enumerate(spans)
    )
    return ChunkPlan(
        sample_rate_hz=rate,
        channels=channels,
        max_chunk_ms=25_000,
        search_ms=5_000,
        total_frames=spans[-1][1] if spans else 0,
        chunks=chunks,
    )


def privacy() -> PrivacyMarker:
    return PrivacyMarker(
        human_subject=True,
        consent=ConsentRecord(status="unknown"),
        retention=RetentionPolicy(delete_on_expiry=False),
    )


def context(language: str = "mr") -> Any:
    return stt().SttContext(
        run_id=RunId.new(),
        audio_artifact_id=ArtifactId.new(),
        created_at=UTC_NOW,
        privacy=privacy(),
        configured_language=language,
    )


# ------------------------------------------------------------------------- transport


Step = httpx.Response | BaseException | Callable[[httpx.Request], httpx.Response]


class ScriptedTransport(httpx.MockTransport):
    """Replays scripted steps in order (a response, an exception to raise, or a function of the
    request). More requests than steps is an ``AssertionError``: a test that sends more than it
    scripted fails. ``requests`` keeps every request seen, with its body read."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self.steps: list[Step] = list(steps)
        self.requests: list[httpx.Request] = []
        self.active = 0
        self.max_active = 0
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = len(self.requests) - 1
        if index >= len(self.steps):
            raise AssertionError(f"request {index + 1} was not scripted")
        step = self.steps[index]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if isinstance(step, BaseException):
                raise step
            if isinstance(step, httpx.Response):
                return httpx.Response(step.status_code, headers=step.headers, content=step.content)
            return step(request)
        finally:
            self.active -= 1


class Part:
    def __init__(self, name: str, filename: str | None, content_type: str | None, data: bytes):
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.data = data


def multipart_parts(request: httpx.Request) -> list[Part]:
    """The parts of a multipart/form-data request, in order."""
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data"), content_type
    match = re.search(r'boundary=("?)([^";]+)\1', content_type)
    assert match is not None, content_type
    delimiter = b"--" + match.group(2).encode("ascii")
    pieces = request.content.split(delimiter)
    assert pieces[0] == b"", "not a well-formed multipart body"
    assert pieces[-1].strip() == b"--", "not a well-formed multipart body"
    parts = []
    for piece in pieces[1:-1]:
        assert piece.startswith(b"\r\n"), "bad part framing"
        assert piece.endswith(b"\r\n"), "bad part framing"
        head, _, data = piece[2:-2].partition(b"\r\n\r\n")
        headers = BytesParser().parsebytes(head + b"\r\n\r\n", headersonly=True)
        disposition = headers["content-disposition"]
        name = re.search(r'name="([^"]*)"', disposition)
        filename = re.search(r'filename="([^"]*)"', disposition)
        assert name is not None, disposition
        parts.append(
            Part(
                name.group(1),
                filename.group(1) if filename else None,
                headers["content-type"],
                data,
            )
        )
    return parts


class Sleeps:
    """A fake ``sleep``: records the waits, waits for nothing."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class Ticker:
    """A fake monotonic clock: every call returns the previous value plus ``step`` seconds."""

    def __init__(self, step: float = 0.25, start: float = 100.0) -> None:
        self.step = step
        self.now = start - step
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        self.now += self.step
        return self.now


class WallClock:
    """A fake UTC clock for the call log: one second more at every call."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return datetime(2026, 10, 25, 9, 0, self.calls % 60, 123456, tzinfo=UTC)


class Rig:
    """A client over a scripted transport, with fake sleep, fake clocks and a call log."""

    def __init__(self, tmp_path: Path, steps: Sequence[Step], *, model: str | None = None) -> None:
        self.transport = ScriptedTransport(steps)
        self.sleeps = Sleeps()
        self.ticker = Ticker()
        self.out = tmp_path / "out"
        self.log = calllog_mod().CallLog.open(self.out, clock=WallClock())
        key = secrets_mod().ApiKey(CANARY_KEY)
        extra: dict[str, Any] = {} if model is None else {"model": model}
        self.client = client_mod().SarvamClient(
            key,
            call_log=self.log,
            transport=self.transport,
            sleep=self.sleeps,
            monotonic=self.ticker,
            **extra,
        )
        # no descriptor or connection pool outlives the rig, however many rigs a run builds
        weakref.finalize(self, self.log.close)
        weakref.finalize(self, self.client.close)

    @property
    def log_lines(self) -> list[dict[str, Any]]:
        text = self.log.path.read_text("ascii")
        return [json.loads(line) for line in text.splitlines()]

    @property
    def log_text(self) -> str:
        text: str = self.log.path.read_text("ascii")
        return text

    def engine(self) -> Any:
        return engine_mod().SarvamStt(self.client)


def chain(exc: BaseException) -> list[BaseException]:
    """The exception and everything reachable through ``__cause__`` and ``__context__``."""
    seen: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo:
        item = todo.pop()
        if item is None or any(item is s for s in seen):
            continue
        seen.append(item)
        todo += [item.__cause__, item.__context__]
    return seen


# ------------------------------------------------------------------ prices and usage (P3-10, P3-11)

DATE_AT = datetime(2026, 10, 23, 13, 8, 26, tzinfo=UTC)
NOTE = "price entered by the project owner for this test; per second billing, no minimum"


def entry(unit: str, quantity: str, amount: str, **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "entry_key": f"sarvam-{unit}",
        "provider": "sarvam",
        "model": "saaras:v4",
        "aliases": [],
        "unit": unit,
        "unit_quantity": quantity,
        "price": {"amount": amount, "currency": "USD"},
        "billing": {"increment": "none", "minimum": "none"},
        "effective_from": "2026-01-01T00:00:00.000000Z",
        "price_source": "user_supplied",
        "provenance": {
            "kind": "user_note",
            "retrieved_at": "2026-10-25T00:00:00.000000Z",
            "note": NOTE,
        },
    }
    doc.update(over)
    return doc


def catalog_text(entries: list[dict[str, Any]] | None = None, **top: Any) -> str:
    if entries is None:
        entries = [entry("audio_input_seconds", "60", "0.006"), entry("requests", "1", "0")]
    return json.dumps({"published_at": "2026-10-01T00:00:00.000000Z", "entries": entries, **top})


def catalog(entries: list[dict[str, Any]] | None = None) -> Any:
    return cost_mod().load_user_catalog(catalog_text(entries), created_at=UTC_NOW)


def usages(
    spans: list[tuple[int, int]], *, at: datetime | None = DATE_AT, model: str = "saaras:v4"
) -> list[Any]:
    plan = hand_plan(spans)
    responses = [
        models().ChunkResponse(
            chunk_index=i,
            response=models().parse_response(json.dumps(ok_body("t", request_id=f"r{i}")).encode()),
            responded_at=at,
            attempts=1,
        )
        for i in range(len(spans))
    ]
    return list(mapping_mod().map_usage_records(plan, responses, model=model, context=context()))


TWO = [(0, 600_000), (600_000, 900_000)]  # 25 s and 12.5 s at 24 kHz
