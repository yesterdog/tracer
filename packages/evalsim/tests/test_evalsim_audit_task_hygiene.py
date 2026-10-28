# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-339 / audit A-F7: an escaping exception leaves no task behind and no text in a log.

Contract: docs/phase4/api-surface-batch-g2.md section 5. ``run_call`` only lets a ``BaseException``
(or a programming error) escape. When one did, the reader task and the watchdog task of the call
were left running, and a reader task that had already failed with its own exception was never
retrieved: asyncio then logs ``Task exception was never retrieved`` with the repr of that exception
when the task is collected, which is text from a failure inside a call (it can carry a canary, a
URL or a header of the agent's connection). Now ``run_call`` cancels and retrieves the reader and
the
watchdog in a ``finally`` on every exit, normal or not (5.1, 5.2), and no asyncio log record at
DEBUG holds the text of a swallowed exception (5.3). The escaping exception itself is unchanged.

No test opens a socket and the clock is virtual.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import Any

import pytest

from evalsim_support_clock import ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import (  # type: ignore[import-not-found,unused-ignore]
    dialect_of,
    lines,
    mod,
    ms,
    settings,
)
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

#: a canary built from parts: no scanner pattern in the source
CANARY = "sim-" + "canary-" + "Zq7-" + "5513"


class CanaryWire:
    """A client end whose reader fails at once with an exception that carries the canary."""

    close_info = None

    async def send(self, message: bytes | str) -> None:
        return None

    async def recv(self) -> bytes | str | None:
        raise RuntimeError(CANARY)

    async def close(self, code: int = 1000) -> None:
        return None


class IdleWire:
    """A client end that never delivers a message until it is closed (a silent agent)."""

    def __init__(self) -> None:
        self._closed = asyncio.Event()
        self.close_info = None

    async def send(self, message: bytes | str) -> None:
        return None

    async def recv(self) -> bytes | str | None:
        await self._closed.wait()
        return None

    async def close(self, code: int = 1000) -> None:
        self._closed.set()


class FailingBrain:
    """A line provider that fails after 200 ms of call time (a bug in the brain, a Ctrl-C)."""

    mode = "scripted"
    needs_agent_text = False

    def __init__(self, clock: Any, make_error: Any) -> None:
        self._clock = clock
        self._make_error = make_error

    async def next(self, turn_index: int, agent_text: str | None) -> Any:
        await self._clock.sleep_until_ns(self._clock.monotonic_ns() + ms(200))
        raise self._make_error()


def _others() -> list[asyncio.Task[Any]]:
    """The tasks of the loop that are not done, apart from the test's own task and the driver of
    the virtual clock."""
    return [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and getattr(task.get_coro(), "__qualname__", "") != "_drive"
    ]


ERRORS = [
    pytest.param(lambda: RuntimeError("brain bug"), RuntimeError, id="runtime_error"),
    pytest.param(lambda: KeyboardInterrupt(), KeyboardInterrupt, id="keyboard_interrupt"),
    pytest.param(lambda: SystemExit(3), SystemExit, id="system_exit"),
]


def _escape(
    make_error: Any, wire: Any, kind: str = "protobuf", *, scripted_frames: int | None = None
) -> tuple[BaseException, list[str]]:
    """Run the call inside a coroutine of our own, let an exception escape it, and return that
    exception and the names of the tasks of the loop that are still not done once ``run_call``
    has left (the driver task excluded). The line provider raises ``make_error()`` after 200 ms,
    or, with ``scripted_frames``, is the scripted provider with one line of that many frames."""
    runner = mod("runner")
    dialect = dialect_of(kind)
    clock = ManualClock()
    provider = (
        FailingBrain(clock, make_error)
        if scripted_frames is None
        else lines(dialect, scripted_frames)
    )

    async def connect() -> Any:
        return wire

    async def main() -> tuple[BaseException, list[str]]:
        try:
            await runner.run_call(
                settings(dialect),
                clock=clock,
                connect=connect,
                lines=provider,
                stop=runner.StopFlag(),
            )
        except BaseException as error:
            escaped = error
        else:
            raise AssertionError("the call did not raise")
        others = _others()
        names = sorted(getattr(task.get_coro(), "__qualname__", "?") for task in others)
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        return escaped, names

    outcome: tuple[BaseException, list[str]] = run_virtual(clock, main)
    return outcome


class TrapWire:
    """A client end whose second send stalls until it is closed, whose receive never returns (a
    reader that is still running when the call ends) and whose close fails after it has released
    the stalled send (a bug in the connection): the hang-up of the watchdog fails."""

    close_info = None

    def __init__(self) -> None:
        self._closed = asyncio.Event()
        self._never = asyncio.Event()
        self.sends = 0

    async def send(self, message: bytes | str) -> None:
        self.sends += 1
        if self.sends > 1:
            await self._closed.wait()
            raise mod("errors").TransportError(code="connection_closed")

    async def recv(self) -> bytes | str | None:
        await self._never.wait()
        return None

    async def close(self, code: int = 1000) -> None:
        self._closed.set()
        raise RuntimeError(CANARY)


def test_a_failing_watchdog_hang_up_still_cleans_up_and_is_reported_once() -> None:
    """BL-339 / A-F7 (5.2): the connection stalls, the watchdog fires and its hang-up fails, so
    ``aclose()`` of the watchdog raises ``WatchdogError``. The reader, still running, is cancelled
    and retrieved all the same (no task is left), and the error that leaves ``run_call`` is that
    ``WatchdogError``, raised once (its context is the failure of the close, not a second
    ``WatchdogError``)."""
    caught, left = _escape(None, TrapWire(), scripted_frames=10)
    assert type(caught) is mod("errors").WatchdogError
    assert left == []
    assert isinstance(caught.__context__, RuntimeError), repr(caught.__context__)


@pytest.mark.parametrize("kind", ["protobuf", "twilio"])
@pytest.mark.parametrize(("make_error", "error_type"), ERRORS)
def test_the_exception_that_escapes_is_the_original_one_and_unchanged(
    kind: str, make_error: Any, error_type: type[BaseException]
) -> None:
    """BL-339 / A-F7 (5.1): the clean-up does not replace, wrap or swallow the exception."""
    caught, _ = _escape(make_error, IdleWire(), kind)
    assert type(caught) is error_type
    if error_type is RuntimeError:
        assert str(caught) == "brain bug"
    assert caught.__cause__ is None


@pytest.mark.parametrize("kind", ["protobuf", "twilio"])
@pytest.mark.parametrize(("make_error", "error_type"), ERRORS)
def test_an_escaping_exception_leaves_no_reader_and_no_watchdog_task(
    kind: str, make_error: Any, error_type: type[BaseException]
) -> None:
    """BL-339 / A-F7 (5.1): when the call raises, the reader task and the watchdog task are already
    done (cancelled and awaited) at the moment ``run_call`` has raised."""
    assert _escape(make_error, IdleWire(), kind)[1] == []


@pytest.mark.parametrize(("make_error", "error_type"), ERRORS)
def test_an_escaping_exception_leaves_no_task_when_the_reader_had_failed_first(
    make_error: Any, error_type: type[BaseException]
) -> None:
    """BL-339 / A-F7 (5.1): the reader failed with its own exception before the brain raised; the
    watchdog is still running at that moment."""
    assert _escape(make_error, CanaryWire())[1] == []


def _capture_asyncio_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="asyncio")


@pytest.mark.parametrize(("make_error", "error_type"), ERRORS)
def test_a_failed_reader_task_is_never_reported_by_asyncio_with_its_text(
    caplog: pytest.LogCaptureFixture, make_error: Any, error_type: type[BaseException]
) -> None:
    """BL-339 / A-F7 (5.3): the reader failed with an exception whose text is a canary, and then
    another exception escaped the call (and was caught by the caller of ``run_call``, here the
    coroutine of the test: a ``BaseException`` that leaves a task step ends the loop, which is
    asyncio's own business and not the runner's). After the objects are collected no asyncio
    record, at any level, says ``never retrieved`` or holds the canary."""
    gc.collect()  # objects of earlier tests are collected before the capture starts
    _capture_asyncio_logs(caplog)
    _escape(make_error, CanaryWire())
    gc.collect()
    messages = [record.getMessage() for record in caplog.records]
    assert not any("never retrieved" in message for message in messages), messages
    assert CANARY not in caplog.text
    assert CANARY not in "\n".join(messages)


def test_a_reader_exception_that_is_raised_to_the_caller_is_not_logged_either(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """BL-339 / A-F7 (5.3): when the reader's exception is itself the one that escapes (the batch E
    behaviour: a programming error is raised, not turned into a hang-up), asyncio still logs
    nothing about it."""
    gc.collect()
    _capture_asyncio_logs(caplog)
    runner = mod("runner")
    dialect = dialect_of("protobuf")
    clock = ManualClock()

    async def connect() -> Any:
        return CanaryWire()

    async def main() -> None:
        await runner.run_call(
            settings(dialect),
            clock=clock,
            connect=connect,
            lines=lines(dialect, 10),
            stop=runner.StopFlag(),
        )

    with pytest.raises(RuntimeError, match=CANARY):
        run_virtual(clock, main)
    gc.collect()
    assert not any("never retrieved" in r.getMessage() for r in caplog.records)
    assert caplog.text.count(CANARY) == 0


def test_a_normal_call_still_ends_with_its_tasks_done_and_a_result() -> None:
    """BL-339 / A-F7: the clean-up changes nothing for a call that ends normally (the batch E base
    case): the same reason, no leftover task."""
    dialect = dialect_of("twilio")
    runner = mod("runner")
    clock = ManualClock()
    wire_client, _ = InMemoryWire.pair()

    async def connect() -> Any:
        return wire_client

    async def main() -> tuple[Any, list[Any]]:
        result = await runner.run_call(
            settings(dialect, max_silence_ms=500),
            clock=clock,
            connect=connect,
            lines=lines(dialect, 3),
            stop=runner.StopFlag(),
        )
        others = _others()
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        return result, others

    result, leftover = run_virtual(clock, main)
    assert result.end_reason == "max_silence"
    assert leftover == []
