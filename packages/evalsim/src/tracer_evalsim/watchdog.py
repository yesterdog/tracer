# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The wall-clock watchdog (ADR 0026 rule 8), independent of the state machine and the sender."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tracer_evalsim.clock import Clock
from tracer_evalsim.errors import WatchdogError
from tracer_evalsim.limits import WATCHDOG_GRACE_NS

__all__ = ["Watchdog"]


class Watchdog:
    def __init__(
        self,
        clock: Clock,
        *,
        max_duration_ns: int,
        hang_up: Callable[[], Awaitable[None]],
        grace_ns: int = WATCHDOG_GRACE_NS,
    ) -> None:
        valid = isinstance(max_duration_ns, int) and not isinstance(max_duration_ns, bool)
        if not valid or max_duration_ns <= 0:
            raise ValueError("max_duration_ns must be a positive integer")
        self._clock = clock
        self._max = max_duration_ns
        self._grace = grace_ns
        self._hang_up = hang_up
        self._task: asyncio.Future[None] | None = None
        self.deadline_ns: int | None = None
        self.fired = False
        self.fired_at_ns: int | None = None
        self._failed = False

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("the watchdog is already started")
        self.deadline_ns = self._clock.monotonic_ns() + self._max + self._grace
        self._task = asyncio.ensure_future(self._run(self.deadline_ns))

    async def _run(self, deadline_ns: int) -> None:
        await self._clock.sleep_until_ns(deadline_ns)
        self.fired = True
        self.fired_at_ns = self._clock.monotonic_ns()
        try:
            await self._hang_up()
        except Exception:
            self._failed = True

    async def aclose(self) -> None:
        task = self._task
        if task is not None and not task.done() and not self.fired:
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if self._failed:
            raise WatchdogError("the hang-up of the watchdog failed")
