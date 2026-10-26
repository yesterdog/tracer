# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch G tests (the security audit fixes F2, F4, F5, F8, F9).

Contract: docs/phase3/api-surface-batch-g.md. Everything is built on the batch E helpers
(``cli_batche_support``): the in-process run, the scripted Sarvam transport, the input writers.
Nothing of ``tracer_cli`` is imported at module level. All text is invented; the key is the canary
of the batch D support file. No test opens a socket.

``g_args`` builds the command line the way the batch G contract says a real send is asked for: the
consent flags, the reference, the instant and the explicit ceiling are always present, whatever
``score_args`` of the batch E support file adds by itself (a spec change of this batch gives it the
same defaults, so both agree).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    ROOT,
    CliRun,
    Inputs,
    SarvamScript,
    cli_mod,
    es,
    fresh,
    parse,
    report_files,
    run_cli,
    score_args,
    write_inputs,
)

REFERENCE = "owner statement 2026-10-25"
REFERENCE_CANARY = "".join(["REFERENCE", "CANARY", "k3"])
RECORDED_AT = "2026-10-20T10:30:00+05:30"
RECORDED_AT_UTC = datetime(2026, 10, 20, 5, 0, 0, tzinfo=UTC)
REPORT_AT = "report-20261025T090000Z.html"
RECOVERY_AT = "usage-recovery-20261025T090000Z.json"
STORE = "tracer.sqlite"

GOOD: dict[str, str | None] = {
    "--stt": "sarvam",
    "--consent": "obtained",
    "--consent-reference": REFERENCE,
    "--consent-recorded-at": RECORDED_AT,
    "--max-chunks": "2",
}


def g_args(
    inputs: Inputs,
    *,
    options: Mapping[str, str | None] | None = None,
    extra: Sequence[str] = (),
    chunking: bool = True,
) -> list[str]:
    """``score_args`` with the sarvam preconditions of the batch G contract made explicit."""
    built = score_args(inputs, options={**GOOD, **(options or {})}, extra=extra, chunking=chunking)
    return [str(item) for item in built]


def key_environ() -> dict[str, str]:
    return {"SARVAM_API_KEY": es().KEY_CANARY}


def sarvam_run(
    inputs: Inputs,
    *,
    script: SarvamScript | None = None,
    options: Mapping[str, str | None] | None = None,
    extra: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
    chunking: bool = True,
) -> tuple[CliRun, SarvamScript]:
    """The command with no injected engine: the real Sarvam engine over a scripted transport."""
    chosen = script if script is not None else SarvamScript()
    result = run_cli(
        g_args(inputs, options=options, extra=extra, chunking=chunking),
        production=True,
        transport=chosen.transport(),
        environ=key_environ() if environ is None else environ,
    )
    return result, chosen


def new_inputs(tmp_path: Path, name: str, **kwargs: Any) -> Inputs:
    return write_inputs(fresh(tmp_path, name), **kwargs)


def words(count: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


def call_log_lines(out: Path) -> list[dict[str, Any]]:
    path = out / "sarvam_call_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("ascii").splitlines()]


def stored_privacy_markers(out: Path, bundle_id: str) -> list[Any]:
    """The privacy marker of the artifact and of both transcripts of the stored run."""
    from cli_batche_support import load_stored_run  # type: ignore[import-not-found,unused-ignore]

    stored = load_stored_run(out, bundle_id)
    return [a.privacy for a in stored.artifacts] + [t.privacy for t in stored.transcripts]


def runs_in_store(out: Path) -> int:
    """How many run bundles the SQLite store holds (0 if it has no table yet)."""
    path = out / STORE
    if not path.is_file():
        return 0
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in connection.execute("select name from sqlite_master")}
        if "run_bundle" not in names:
            return 0
        return int(connection.execute("select count(*) from run_bundle").fetchone()[0])
    finally:
        connection.close()


def read_recovery(out: Path) -> dict[str, Any]:
    path = out / RECOVERY_AT
    assert path.is_file(), sorted(p.name for p in out.iterdir()) if out.is_dir() else out
    data = path.read_bytes()
    data.decode("ascii")
    document = json.loads(data)
    assert isinstance(document, dict)
    return document


def usage_summary(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """``(unit, count)`` of every quantity of the usage records, sorted."""
    return sorted((q["unit"], str(q["count"])) for record in records for q in record["quantities"])


def usage_of_stored(out: Path, bundle_id: str) -> list[tuple[str, str]]:
    from cli_batche_support import load_stored_run  # type: ignore[import-not-found,unused-ignore]

    stored = load_stored_run(out, bundle_id)
    return sorted(
        (q.unit, str(q.count)) for record in stored.usage_records for q in record.quantities
    )


def report_doc(out: Path) -> Any:
    files = report_files(out)
    assert len(files) == 1, sorted(p.name for p in out.iterdir())
    return parse(files[0].read_text("utf-8"))


def within(seconds: float, call: Any) -> Any:
    """Run ``call`` on a thread; fail the test (and let the thread go) if it does not return."""
    import pytest

    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = call()
        except BaseException as error:
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail("the command did not return in time")
    if "error" in box:
        raise box["error"]
    return box.get("value")


@dataclass
class ProbeEngine:
    """An engine that records what exists on disk when the command calls it."""

    out: Path
    events: list[tuple[Any, ...]] = field(default_factory=list)
    closed: int = 0
    refuse_in_preflight: bool = False
    inner: Any = None

    def _state(self) -> tuple[bool, bool]:
        return self.out.is_dir(), (self.out / STORE).is_file()

    def preflight(self, plan: Any) -> None:
        self.events.append(("preflight", len(plan.chunks), *self._state()))
        if self.refuse_in_preflight:
            from tracer_evalcore.stt import SttConfigurationError

            raise SttConfigurationError(reason="probe_refused")

    def transcribe(self, plan: Any, context: Any) -> Any:
        mode = self.out.stat().st_mode & 0o777 if self.out.is_dir() else None
        self.events.append(("transcribe", len(plan.chunks), *self._state(), mode))
        if self.inner is None:
            self.inner = es().FakeEngine()
        return self.inner.transcribe(plan, context)

    def close(self) -> None:
        self.closed += 1

    def named(self, name: str) -> list[tuple[Any, ...]]:
        return [e for e in self.events if e[0] == name]


class PlantingScript(SarvamScript):  # type: ignore[misc,unused-ignore]
    """A scripted transport that creates a file when the nth request arrives (a race)."""

    def __init__(self, plant: Path, *, at_request: int = 2, text: str = "planted") -> None:
        super().__init__()
        self.plant = plant
        self.at_request = at_request
        self.text = text

    def __call__(self, request: Any) -> Any:
        response = super().__call__(request)
        if len(self.requests) == self.at_request:
            self.plant.write_text(self.text, "ascii")
        return response


__all__ = [
    "GOOD",
    "RECORDED_AT",
    "RECORDED_AT_UTC",
    "RECOVERY_AT",
    "REFERENCE",
    "REFERENCE_CANARY",
    "REPORT_AT",
    "ROOT",
    "STORE",
    "PlantingScript",
    "ProbeEngine",
    "call_log_lines",
    "cli_mod",
    "g_args",
    "key_environ",
    "new_inputs",
    "read_recovery",
    "report_doc",
    "runs_in_store",
    "sarvam_run",
    "stored_privacy_markers",
    "usage_of_stored",
    "usage_summary",
    "within",
    "words",
]
