# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The injected clock of the simulator (ADR 0026 rule 8)."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

__all__ = ["Clock", "SystemClock"]


class Clock(Protocol):
    def monotonic_ns(self) -> int: ...

    async def sleep_until_ns(self, deadline_ns: int) -> None: ...


class SystemClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    async def sleep_until_ns(self, deadline_ns: int) -> None:
        while True:
            remaining = deadline_ns - time.monotonic_ns()
            if remaining <= 0:
                await asyncio.sleep(0)
                return
            await asyncio.sleep(remaining / 1e9)
