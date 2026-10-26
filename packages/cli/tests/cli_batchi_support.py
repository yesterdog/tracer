# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch I tests (the re-audit findings R1, R2, R3, R5 and R6).

Contract: docs/phase3/api-surface-batch-i.md. Built on the batch E and G helpers: the in-process
run, the scripted Sarvam transport, the input writers. ``run_capture`` is ``run_cli`` that also
hands back what the command wrote when it ends by an exception (a ``KeyboardInterrupt`` leaves
``run`` without a return value, and the streams are the evidence). Nothing of ``tracer_cli`` is
imported at module level. All text is invented; the key is the canary of the batch D support file.
No test opens a socket.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    SarvamScript,
    base_bodies,
    cli_mod,
    es,
    sarvam_body,
)
from cli_batchg_support import (  # type: ignore[import-not-found,unused-ignore]
    RECOVERY_AT,
    REPORT_AT,
    STORE,
    g_args,
    key_environ,
    new_inputs,
    read_recovery,
    runs_in_store,
)

CALL_LOG = "sarvam_call_log.jsonl"
MODE_WARNING = "warning: the output directory is accessible to group or other users (mode {mode})\n"
EMBED_WARNING = (
    "warning: --embed-audio puts the call audio into the report file; do not share the file\n"
)


def mode_warning(mode: int) -> str:
    """The one stderr line for an existing ``--out`` whose mode has group or other bits."""
    return MODE_WARNING.format(mode=f"{mode & 0o777:04o}")


@dataclass
class Captured:
    """What one in-process run did. ``error`` is the exception that left ``run`` (else ``None``)."""

    code: int | None
    error: BaseException | None
    out: str
    err: str
    engine: Any
    factory_calls: int
    requests: list[Any] = field(default_factory=list)

    def value(self, label: str) -> str:
        found = [line for line in self.out.splitlines() if line.startswith(f"{label}: ")]
        assert len(found) == 1, (label, self.out)
        return found[0][len(label) + 2 :]


def run_capture(
    args: Sequence[str],
    *,
    engine: Any = "default",
    environ: Mapping[str, str] | None = None,
    transport: Any = None,
    production: bool = False,
) -> Captured:
    """``tracer_cli.cli.run`` in-process; an exception that leaves ``run`` is returned."""
    support = es()
    cli = cli_mod()
    stdout, stderr = io.StringIO(), io.StringIO()
    chosen = support.FakeEngine() if engine == "default" else engine
    built: list[Any] = []

    def factory(request: Any) -> Any:
        built.append(request)
        return chosen

    environment = cli.Environment(
        stdout=stdout,
        stderr=stderr,
        environ=dict(environ or {}),
        clock=lambda: support.CREATED_AT,
        sleep=lambda seconds: None,
        transport=transport,
        engine_factory=None if production else factory,
    )
    code: int | None = None
    error: BaseException | None = None
    try:
        code = cli.run(list(args), environment)
    except BaseException as caught:
        error = caught
    return Captured(code, error, stdout.getvalue(), stderr.getvalue(), chosen, len(built))


def sarvam_capture(
    inputs: Any,
    *,
    script: SarvamScript | None = None,
    options: Mapping[str, str | None] | None = None,
    extra: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
) -> tuple[Captured, SarvamScript]:
    """The command over the real Sarvam engine on a scripted transport."""
    chosen = script if script is not None else SarvamScript()
    result = run_capture(
        g_args(inputs, options=options, extra=extra),
        production=True,
        transport=chosen.transport(),
        environ=key_environ() if environ is None else environ,
    )
    return result, chosen


class FailingScript(SarvamScript):  # type: ignore[misc,unused-ignore]
    """Answers the chunk requests in order and raises ``exc`` from the transport at request
    number ``fail_at`` (0 based). The request is recorded first, as a real one would be."""

    def __init__(
        self, fail_at: int, exc: BaseException, bodies: Sequence[Any] | None = None
    ) -> None:
        super().__init__(bodies)
        self.fail_at = fail_at
        self.exc = exc

    def __call__(self, request: Any) -> Any:
        if len(self.requests) == self.fail_at:
            self.requests.append(request)
            raise self.exc
        return super().__call__(request)


def four_bodies() -> list[dict[str, Any]]:
    """Four answers, for ``--max-chunk-ms 1000 --search-ms 500`` (the 3 s recording is 4 chunks)."""
    extra = [
        sarvam_body(f"w{i}", request_id=f"20261025_replay-{i:04d}", language="mr-IN")
        for i in (2, 3)
    ]
    return [*base_bodies(), *extra]


FOUR_CHUNKS = {"--max-chunk-ms": "1000", "--search-ms": "500", "--max-chunks": "4"}


def call_log(out: Path) -> list[dict[str, Any]]:
    path = out / CALL_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("ascii").splitlines()]


def answered_in_call_log(out: Path) -> list[tuple[int, str | None]]:
    """``(chunk, request_id)`` of every ``after`` line with status 200, in order."""
    return [
        (line["chunk"], line["request_id"])
        for line in call_log(out)
        if line["phase"] == "after" and line["status"] == 200
    ]


def recovery_chunks(document: Mapping[str, Any]) -> list[int]:
    return [int(r["metadata"]["tracer.chunk_index"]) for r in document["usage_records"]]


def recovery_request_ids(document: Mapping[str, Any]) -> list[str]:
    return [r["provider_call_ref"]["value"] for r in document["usage_records"]]


def names(out: Path) -> list[str]:
    return sorted(p.name for p in out.iterdir()) if out.is_dir() else []


class StreamingFake:
    """An injected engine that follows the batch I streaming protocol: ``streams_usage`` is true
    and ``transcribe`` takes ``on_usage``. It builds the base scenario answer with ``FakeEngine``,
    hands the usage records out one by one and, if asked, raises after ``die_after`` of them."""

    streams_usage = True
    provider = "fake"
    model = "fake-model-1"

    def __init__(self, *, die_after: int | None = None, exc: BaseException | None = None) -> None:
        self.die_after = die_after
        self.exc = exc if exc is not None else RuntimeError("STREAMDIED")
        self.calls: list[Any] = []
        self.handed: list[Any] = []
        self.closed = 0

    def transcribe(self, plan: Any, context: Any, *, on_usage: Any = None) -> Any:
        self.calls.append((plan, context, on_usage))
        outcome = es().FakeEngine().transcribe(plan, context)
        for index, record in enumerate(outcome.usage):
            if self.die_after == index:
                raise self.exc
            self.handed.append(record)
            if on_usage is not None:
                on_usage(record)
        if self.die_after is not None and self.die_after >= len(outcome.usage):
            raise self.exc
        return outcome

    def close(self) -> None:
        self.closed += 1


class PlainEngine:
    """An injected engine of the old protocol: no ``streams_usage``, ``transcribe(plan, context)``
    only. It must never be handed ``on_usage``."""

    provider = "fake"
    model = "fake-model-1"

    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    def transcribe(self, plan: Any, context: Any) -> Any:
        self.calls += 1
        return es().FakeEngine().transcribe(plan, context)

    def close(self) -> None:
        self.closed += 1


class FlaggedOffEngine(PlainEngine):
    """``streams_usage`` is present and false: the command must not pass ``on_usage`` either."""

    streams_usage = False

    def __init__(self) -> None:
        super().__init__()
        self.kwargs: list[dict[str, Any]] = []

    def transcribe(self, plan: Any, context: Any, **kwargs: Any) -> Any:
        self.kwargs.append(dict(kwargs))
        return super().transcribe(plan, context)


def at(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


__all__ = [
    "CALL_LOG",
    "EMBED_WARNING",
    "FOUR_CHUNKS",
    "RECOVERY_AT",
    "REPORT_AT",
    "STORE",
    "Captured",
    "FailingScript",
    "FlaggedOffEngine",
    "PlainEngine",
    "StreamingFake",
    "answered_in_call_log",
    "at",
    "call_log",
    "four_bodies",
    "mode_warning",
    "names",
    "new_inputs",
    "read_recovery",
    "recovery_chunks",
    "recovery_request_ids",
    "run_capture",
    "runs_in_store",
    "sarvam_capture",
]
