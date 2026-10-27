# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Virtual time for the tracer-evalsim tests (P4-19, P4-20). Support module, not collected.

``ManualClock`` implements the ``Clock`` protocol of ``docs/phase4/api-surface-batch-d.md`` section
2 (``monotonic_ns`` and ``async sleep_until_ns``) with no wall clock at all: time moves only when
every task of the loop is blocked on the clock, and then it jumps to the earliest deadline. A test
that runs 60 seconds of call time therefore takes milliseconds and gives the same numbers on every
run and every Python version. ``run_virtual`` is the driver. A hang (a coroutine blocked on
something that is neither the clock nor finished) is reported as an ``AssertionError`` at once
instead of as a timeout.
"""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

#: A virtual call starts at a non-zero instant so that "instant 0" bugs show.
START_NS = 5_000_000_000
#: Event-loop turns that count as "everything that can run has run".
SETTLE_TURNS = 100


class ManualClock:
    """Deterministic ``Clock``: ``sleep_until_ns`` blocks until the driver advances time."""

    def __init__(self, start_ns: int = START_NS) -> None:
        self._now = start_ns
        self._seq = 0
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        #: every deadline passed to ``sleep_until_ns`` (also those already in the past)
        self.deadlines: list[int] = []

    def monotonic_ns(self) -> int:
        return self._now

    async def sleep_until_ns(self, deadline_ns: int) -> None:
        self.deadlines.append(deadline_ns)
        if deadline_ns <= self._now:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._waiters, (deadline_ns, self._seq, future))
        await future

    def advance_to(self, target_ns: int) -> None:
        """Move time forward to ``target_ns`` and release every sleeper due by then."""
        assert target_ns >= self._now, "virtual time never goes back"
        self._now = target_ns
        while self._waiters and self._waiters[0][0] <= self._now:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)

    def advance_to_next_deadline(self) -> bool:
        """Jump to the earliest live deadline; False if nobody sleeps."""
        while self._waiters and self._waiters[0][2].done():
            heapq.heappop(self._waiters)
        if not self._waiters:
            return False
        self.advance_to(max(self._now, self._waiters[0][0]))
        return True


async def settle() -> None:
    for _ in range(SETTLE_TURNS):
        await asyncio.sleep(0)


async def _drive(clock: ManualClock, main: Callable[[], Awaitable[T]]) -> T:
    task: asyncio.Future[T] = asyncio.ensure_future(main())
    try:
        while not task.done():
            await settle()
            if task.done():
                break
            if not clock.advance_to_next_deadline():
                raise AssertionError("virtual run is stuck: nothing sleeps on the clock")
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def run_virtual(clock: ManualClock, main: Callable[[], Awaitable[T]]) -> T:
    """Run ``main()`` to completion on a fresh loop with virtual time."""
    return asyncio.run(_drive(clock, main))


def run_for(clock: ManualClock, main: Callable[[], Awaitable[Any]], *, until_ns: int) -> None:
    """Run ``main()`` but stop the world at ``until_ns`` (the coroutine is cancelled there)."""

    async def bounded() -> None:
        task = asyncio.ensure_future(main())
        try:
            while not task.done():
                await settle()
                if task.done():
                    break
                nxt = clock._waiters[0][0] if clock._waiters else None
                if nxt is None or nxt > until_ns:
                    clock.advance_to(max(clock.monotonic_ns(), until_ns))
                    await settle()
                    break
                clock.advance_to_next_deadline()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(bounded())
