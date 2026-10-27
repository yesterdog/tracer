# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-20: the wall-clock watchdog, independent of the state machine and of the sender (ADR 0026
rule 8, rule 17; contract ``docs/phase4/api-surface-batch-d.md`` section 6). Open no socket; time
is the virtual clock, so "one second of grace" is checked to the nanosecond.

The watchdog knows only a clock, a maximum duration and a ``hang_up`` coroutine. It hangs up when
``max_duration + 1 s`` has elapsed since ``start()``, whatever any other task is doing. Rejected
in the ADR: a watchdog driven by the state machine (a stalled machine would never fire it).
"""

from __future__ import annotations

import ast
import asyncio
from typing import Any

import pytest

from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import SRC, imported_top_levels, mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

S = 1_000_000_000


def make(clock: Any, max_ns: int, hang_up: Any, **kwargs: Any) -> Any:
    return mod("watchdog").Watchdog(clock, max_duration_ns=max_ns, hang_up=hang_up, **kwargs)


def test_watchdog_hangs_up_even_if_state_machine_stalls() -> None:
    clock = ManualClock()
    wire_a, wire_b = InMemoryWire.pair()
    seen: dict[str, Any] = {}

    async def hang_up() -> None:
        seen["hang_up_at"] = clock.monotonic_ns()
        await wire_a.close(1001)

    async def stalled_machine() -> None:
        # stands for the call state machine in `awaiting_agent` with the agent gone quiet and the
        # silence limit never reached: it waits on something that never happens
        await asyncio.Event().wait()

    async def main() -> None:
        wd = make(clock, 5 * S, hang_up)
        wd.start()
        machine = asyncio.ensure_future(stalled_machine())
        deadline = wd.deadline_ns
        assert deadline == START_NS + 5 * S + S  # max duration plus one second of grace
        # a probe that looks one nanosecond before and one nanosecond after the deadline
        await clock.sleep_until_ns(deadline - 1)
        seen["before"] = wd.fired
        await clock.sleep_until_ns(deadline + 1)
        seen["after"] = wd.fired
        await wd.aclose()
        machine.cancel()
        await asyncio.gather(machine, return_exceptions=True)

    run_virtual(clock, main)
    assert seen["before"] is False  # not one nanosecond early
    assert seen["after"] is True
    assert seen["hang_up_at"] == START_NS + 6 * S  # exactly at max + 1 s
    assert wire_a.calls == [("close", 1001)]  # hung up once
    assert wire_a.close_info is not None
    assert wire_b.close_info is not None


def _fire_times(max_ns: int) -> list[int]:
    clock = ManualClock()
    fired: list[int] = []

    async def hang_up() -> None:
        fired.append(clock.monotonic_ns())

    async def main() -> None:
        wd = make(clock, max_ns, hang_up)
        wd.start()
        await clock.sleep_until_ns(START_NS + max_ns + 2 * S)
        assert wd.fired is True
        assert wd.fired_at_ns == fired[0]
        await wd.aclose()

    run_virtual(clock, main)
    return fired


def test_watchdog_grace_is_one_second_and_fixed() -> None:
    assert mod("limits").WATCHDOG_GRACE_NS == S
    for max_ns in (1, S, 90 * S, 14_400 * S):
        assert _fire_times(max_ns) == [START_NS + max_ns + S]


def test_watchdog_reports_fired_when_it_starts_to_hang_up_not_when_it_finishes() -> None:
    clock = ManualClock()
    seen: dict[str, Any] = {}

    async def slow_hang_up() -> None:
        await clock.sleep_until_ns(clock.monotonic_ns() + 10 * S)  # a transport that closes slowly

    async def main() -> None:
        wd = make(clock, S, slow_hang_up)
        wd.start()
        await clock.sleep_until_ns(START_NS + 2 * S + 1)  # 1 ns after the deadline, hang-up running
        seen["fired"] = wd.fired
        seen["at"] = wd.fired_at_ns
        await clock.sleep_until_ns(START_NS + 20 * S)
        await wd.aclose()

    run_virtual(clock, main)
    assert seen == {"fired": True, "at": START_NS + 2 * S}


def test_watchdog_hangs_up_even_if_the_sender_is_blocked_in_a_write() -> None:
    """The sender is stuck on a write (backpressure); the watchdog does not wait for it."""
    errors = mod("errors")
    clock = ManualClock()
    released = asyncio.Event
    box: dict[str, Any] = {}

    async def main() -> None:
        gate = released()

        async def send_frame(frame: bytes) -> None:
            await gate.wait()  # the write never completes by itself
            raise errors.TransportError(code="connection_closed")

        async def hang_up() -> None:
            box["at"] = clock.monotonic_ns()
            gate.set()  # closing the transport fails the pending write

        sender = mod("sender").PacedSender(clock, send_frame)
        wd = make(clock, 2 * S, hang_up)
        wd.start()
        with pytest.raises(errors.TransportError):
            await sender.send_line([bytes(160)] * 10)
        await wd.aclose()

    run_virtual(clock, main)
    assert box["at"] == START_NS + 3 * S


def test_watchdog_cancelled_before_its_deadline_never_fires() -> None:
    clock = ManualClock()
    calls: list[int] = []

    async def hang_up() -> None:
        calls.append(1)

    async def main() -> None:
        wd = make(clock, 10 * S, hang_up)
        wd.start()
        await clock.sleep_until_ns(START_NS + 4 * S)
        await wd.aclose()
        await clock.sleep_until_ns(START_NS + 30 * S)
        assert wd.fired is False
        assert wd.fired_at_ns is None

    run_virtual(clock, main)
    assert calls == []


def test_watchdog_hang_up_failure_is_not_swallowed() -> None:
    clock = ManualClock()

    async def hang_up() -> None:
        raise RuntimeError("SECRET-detail the transport said")

    async def main() -> None:
        wd = make(clock, S, hang_up)
        wd.start()
        await clock.sleep_until_ns(START_NS + 3 * S)
        assert wd.fired is True  # it did fire; the hang-up failed
        with pytest.raises(mod("errors").WatchdogError) as raised:
            await wd.aclose()
        assert "SECRET" not in str(raised.value)  # facts only
        assert raised.value.__cause__ is None
        assert issubclass(mod("errors").WatchdogError, mod("errors").EvalsimError)

    run_virtual(clock, main)


def test_watchdog_validates_its_arguments_and_starts_once() -> None:
    clock = ManualClock()

    async def hang_up() -> None:
        return None

    bad: Any
    for bad in (0, -1, True, 1.5, "5", None):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            make(clock, bad, hang_up)
    with pytest.raises(TypeError):
        mod("watchdog").Watchdog(clock, max_duration_ns=S)
    with pytest.raises(TypeError):
        mod("watchdog").Watchdog(clock, hang_up=hang_up)

    async def main() -> None:
        wd = make(clock, S, hang_up)
        assert wd.fired is False
        assert wd.deadline_ns is None
        wd.start()
        with pytest.raises(RuntimeError):
            wd.start()
        await wd.aclose()
        await wd.aclose()  # closing twice is harmless

    run_virtual(clock, main)


def test_watchdog_module_depends_on_nothing_that_could_stall_it() -> None:
    """Independence by construction: the module imports the standard library, the clock protocol,
    the error base and the limits, and no sender, runner, state machine, dialect or transport."""
    source = SRC / "watchdog.py"
    top = imported_top_levels(source)
    assert top <= {"__future__", "asyncio", "collections", "tracer_evalsim"} | set(
        __import__("sys").stdlib_module_names
    )
    assert not top & {"websockets", "aiortc", "tracer_evalcore"}
    tree = ast.parse(source.read_text(encoding="utf-8"))
    internal = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("tracer_evalsim")
    }
    assert internal <= {"tracer_evalsim.clock", "tracer_evalsim.errors", "tracer_evalsim.limits"}
