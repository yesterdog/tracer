# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch G evalsim tests (target safety and BL-338). Support module,
not collected. Contract: docs/phase4/api-surface-batch-g.md.

``play_g`` is the ``play`` of ``evalsim_support_runner`` with the two seams of batch G added: the
``kill_switch`` poll (a callable that says whether the kill-switch file is present) and a line
provider that may be any object. Time is the virtual clock of ``evalsim_support_clock``; nothing
here opens a socket, reads a file or the wall clock. Product modules are imported lazily, so a
missing module fails one test and the directory still collects.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable, Sequence
from typing import Any

from evalsim_support_agents import FakeAgent  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import (  # type: ignore[import-not-found,unused-ignore]
    FRAME_NS,
    MS,
    ORIGIN,
    Played,
    codec_of,
    dialect_of,
    ms,
    settings,
)
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

__all__ = ["FRAME_NS", "MS", "ORIGIN", "START_NS", "Switch", "mod", "play_g"]


def mod(name: str) -> Any:
    return importlib.import_module(f"tracer_evalsim.{name}")


class Switch:
    """A stand-in for the kill-switch file: it answers True once the virtual clock has reached
    ``from_ms`` after the start of the run (the connection takes no time unless a test says so),
    and counts how often it was asked."""

    def __init__(self, clock: ManualClock, from_ms: int | None) -> None:
        self._clock = clock
        self._from = None if from_ms is None else ORIGIN + ms(from_ms)
        self.calls = 0
        self.times: list[int] = []

    def __call__(self) -> bool:
        self.calls += 1
        now = self._clock.monotonic_ns()
        self.times.append(now)
        return self._from is not None and now >= self._from


def play_g(
    kind: str,
    steps: Sequence[Any],
    line_provider: Any,
    *,
    switch_ms: int | None = None,
    use_switch: bool = True,
    connect_ms: int = 0,
    agent_runs: bool = True,
    **settings_kw: Any,
) -> tuple[Played, Switch]:
    """Place one virtual call of dialect ``kind`` ("protobuf" or "twilio") against the fake agent
    ``steps`` with a kill switch that turns on ``switch_ms`` after the start of the run (never if
    None).
    ``line_provider`` may be a callable that is given the clock."""
    runner = mod("runner")
    dialect = dialect_of(kind)
    clock = ManualClock()
    if callable(line_provider) and not hasattr(line_provider, "next"):
        line_provider = line_provider(clock)
    agent = FakeAgent(codec_of(kind), tuple(steps))
    held: list[Any] = []
    tasks: list[asyncio.Future[Any]] = []
    stop = runner.StopFlag()
    switch = Switch(clock, switch_ms)

    async def connect() -> Any:
        if connect_ms:
            await clock.sleep_until_ns(clock.monotonic_ns() + ms(connect_ms))
        client, remote = InMemoryWire.pair()
        held.append(client)
        if agent_runs:
            tasks.append(asyncio.ensure_future(agent.run(remote, clock)))
        return client

    async def main() -> Any:
        extra: dict[str, Any] = {"kill_switch": switch} if use_switch else {}
        try:
            return await runner.run_call(
                settings(dialect, **settings_kw),
                clock=clock,
                connect=connect,
                lines=line_provider,
                stop=stop,
                segments=None,
                **extra,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    result = run_virtual(clock, main)
    return Played(result, agent, clock, held[0] if held else None), switch


def inline() -> Callable[[Callable[[], Any]], Any]:
    """A ``run_blocking`` that runs the function at once, on the loop (tests only)."""

    async def run(function: Callable[[], Any]) -> Any:
        return function()

    return run
