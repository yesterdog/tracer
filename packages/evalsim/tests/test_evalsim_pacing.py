# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-20: real-time pacing, backpressure, a sender that never bursts (ADR 0026 rule 8; contract
``docs/phase4/api-surface-batch-d.md`` section 6). Open no socket; time is the virtual clock of
``evalsim_support_clock``, so every number below is exact and identical on every run.

The pacing rule the contract pins (the ADR gives the rule, the contract gives the math):

* a line is a sequence of 20 ms frames; the line's origin is the clock reading when ``send_line``
  starts, and frame ``k`` is *scheduled* at ``origin + k * 20 ms`` (``FrameRecord.scheduled_ns``);
* frame 0 leaves at once; frame ``k >= 1`` never leaves before its schedule;
* a write is awaited to completion before the next frame is scheduled (backpressure);
* if frame ``k`` is due and the clock is already ``>= 20 ms`` past its schedule, the schedule of
  this and every later frame of the line moves later by the same amount (a re-anchor): the sender
  never sends two frames back to back to catch up. A lateness below one frame is absorbed by the
  original schedule (the next frame is on time again);
* ``FrameRecord.scheduled_ns`` is always the ORIGINAL schedule, so lateness stays visible in the
  frame log; a frame is logged only after its write completed.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from evalsim_support_clock import START_NS, ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]

MS = 1_000_000
F = 20 * MS


def frames(count: int, size: int = 160) -> list[bytes]:
    return [bytes([i % 251]) * size for i in range(count)]


def run_line(
    count: int, *, delays_ms: dict[int, int] | None = None, should_stop: Any = None
) -> tuple[Any, Any, list[tuple[int, bytes]], ManualClock]:
    """Send one line of ``count`` frames; frame ``i``'s write takes ``delays_ms[i]`` ms."""
    clock = ManualClock()
    delays = delays_ms or {}
    writes: list[tuple[int, bytes]] = []

    async def send_frame(frame: bytes) -> None:
        index = len(writes)
        writes.append((clock.monotonic_ns(), frame))
        if index in delays:
            await clock.sleep_until_ns(clock.monotonic_ns() + delays[index] * MS)

    sender = mod("sender").PacedSender(clock, send_frame)
    payload = frames(count)

    async def main() -> Any:
        stop = should_stop(clock) if should_stop is not None else None
        return await sender.send_line(payload, should_stop=stop)

    result = run_virtual(clock, main)
    return sender, result, writes, clock


def test_sender_paces_real_time_with_injected_clock() -> None:
    sender, result, writes, clock = run_line(50)
    origin = START_NS
    assert (result.line_index, result.frames_sent, result.stopped) == (0, 50, False)
    # frame k left at exactly origin + k * 20 ms: not early, not late, not in a burst
    assert [at for at, _ in writes] == [origin + k * F for k in range(50)]
    log = sender.log
    assert [r.sent_ns for r in log] == [origin + k * F for k in range(50)]
    assert [r.scheduled_ns for r in log] == [origin + k * F for k in range(50)]
    assert [r.lateness_ns for r in log] == [0] * 50
    assert [(r.line_index, r.frame_index, r.size) for r in log] == [(0, k, 160) for k in range(50)]
    # the writes got the frames, in order, untouched
    assert [frame for _, frame in writes] == frames(50)
    # time only moved to frame deadlines: 49 sleeps (frame 0 needs none), none after the last
    # frame, no polling loop, no sleep of any other length
    assert clock.deadlines == [origin + k * F for k in range(1, 50)]
    assert clock.monotonic_ns() == origin + 49 * F


def test_sender_never_sends_a_frame_before_its_schedule() -> None:
    for delays in ({}, {2: 5}, {3: 19}, {3: 20}, {3: 21}, {1: 200, 9: 33}, {0: 100}):
        sender, _, _, _ = run_line(30, delays_ms=delays)
        for record in sender.log:
            assert record.sent_ns >= record.scheduled_ns, (delays, record)
        sent = [r.sent_ns for r in sender.log]
        assert sent == sorted(sent)


def test_sender_does_not_burst_after_a_stall_it_moves_the_schedule() -> None:
    # frame 3's write takes 200 ms: it leaves at 60 ms, completes at 260 ms
    sender, result, _, _ = run_line(10, delays_ms={3: 200})
    origin = START_NS
    sent = [r.sent_ns - origin for r in sender.log]
    # 0, 20, 40, 60, then the stall: frame 4 (due at 80) leaves when the write is done, at 260,
    # and the schedule moves with it: 280, 300, ... (not a catch-up at 260, 260, 260, ...)
    assert sent == [
        0,
        20 * MS,
        40 * MS,
        60 * MS,
        260 * MS,
        280 * MS,
        300 * MS,
        320 * MS,
        340 * MS,
        360 * MS,
    ]
    gaps = [b - a for a, b in itertools.pairwise(sent[3:])]
    assert all(gap >= F for gap in gaps[1:]), gaps
    # the lateness is visible in the log against the ORIGINAL schedule, and stays 180 ms
    assert [r.lateness_ns // MS for r in sender.log] == [0, 0, 0, 0, 180, 180, 180, 180, 180, 180]
    assert result.frames_sent == 10


def test_sender_absorbs_a_lateness_below_one_frame() -> None:
    # frame 2's write takes 30 ms (40 -> 70): frame 3 is due at 60 and leaves at 70 (10 ms late),
    # frame 4 is due at 80 and leaves at 80: the original schedule resumes
    sender, _, _, _ = run_line(8, delays_ms={2: 30})
    sent = [(r.sent_ns - START_NS) // MS for r in sender.log]
    assert sent == [0, 20, 40, 70, 80, 100, 120, 140]
    assert [r.lateness_ns // MS for r in sender.log] == [0, 0, 0, 10, 0, 0, 0, 0]


def test_sender_reanchors_exactly_at_one_frame_of_lateness() -> None:
    # 19 ms short of the boundary: frame 2's write 39 ms (40 -> 79); frame 3 (due 60) is 19 ms
    # late: no re-anchor, frame 4 is on its original schedule (80), one ms after frame 3
    sender, _, _, _ = run_line(7, delays_ms={2: 39})
    assert [(r.sent_ns - START_NS) // MS for r in sender.log] == [0, 20, 40, 79, 80, 100, 120]
    # exactly one frame late: write 40 ms (40 -> 80); frame 3 (due 60) leaves at 80 and the
    # schedule moves by 20: frame 4 at 100, not at 80
    sender, _, _, _ = run_line(7, delays_ms={2: 40})
    assert [(r.sent_ns - START_NS) // MS for r in sender.log] == [0, 20, 40, 80, 100, 120, 140]
    # the first frame never re-anchors: a long first write only delays the second
    sender, _, _, _ = run_line(4, delays_ms={0: 100})
    assert [(r.sent_ns - START_NS) // MS for r in sender.log] == [0, 100, 120, 140]


def test_sender_waits_for_each_write_before_scheduling_the_next() -> None:
    clock = ManualClock()
    state = {"active": 0, "max_active": 0, "writes": 0}
    done_at: list[int] = []
    started_at: list[int] = []

    async def send_frame(frame: bytes) -> None:
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        started_at.append(clock.monotonic_ns())
        await clock.sleep_until_ns(clock.monotonic_ns() + 7 * MS)
        done_at.append(clock.monotonic_ns())
        state["active"] -= 1

    sender = mod("sender").PacedSender(clock, send_frame)
    run_virtual(clock, lambda: sender.send_line(frames(12)))
    assert state["max_active"] == 1
    assert all(started_at[i + 1] >= done_at[i] for i in range(11))
    # 7 ms writes inside 20 ms slots: the pace is still exactly real time
    assert [s - START_NS for s in started_at] == [k * F for k in range(12)]


def test_sender_logs_a_frame_only_after_its_write_completed() -> None:
    errors = mod("errors")
    clock = ManualClock()
    count = {"n": 0}

    async def send_frame(frame: bytes) -> None:
        count["n"] += 1
        if count["n"] == 3:
            raise errors.TransportError(code="connection_closed")

    sender = mod("sender").PacedSender(clock, send_frame)

    async def main() -> None:
        with pytest.raises(errors.TransportError):
            await sender.send_line(frames(10))

    run_virtual(clock, main)
    assert [r.frame_index for r in sender.log] == [0, 1]  # the failed write was never sent
    assert clock.monotonic_ns() == START_NS + 2 * F


def test_sender_should_stop_is_consulted_before_every_frame_and_after_each_wait() -> None:
    # stop at 75 ms: frame 4 is due at 80, the check after the wait sees the stop and nothing is
    # written for it
    sender, result, writes, _ = run_line(
        10, should_stop=lambda clock: lambda: clock.monotonic_ns() >= START_NS + 75 * MS
    )
    assert (result.frames_sent, result.stopped) == (4, True)
    assert len(writes) == 4
    assert [r.frame_index for r in sender.log] == [0, 1, 2, 3]
    # stopped before the first frame: nothing is sent and nothing is waited for
    sender, result, writes, clock = run_line(10, should_stop=lambda clock: lambda: True)
    assert (result.frames_sent, result.stopped) == (0, True)
    assert writes == []
    assert clock.deadlines == []
    # the hook is called once per frame at least (the kill-switch file is checked every frame)
    calls = {"n": 0}

    def counting(clock: Any) -> Any:
        def check() -> bool:
            calls["n"] += 1
            return False

        return check

    run_line(10, should_stop=counting)
    assert calls["n"] >= 10


def test_sender_should_stop_is_checked_before_a_frame_that_does_not_wait() -> None:
    """A late frame (after a slow write) leaves at once: there is no wait after which to look, so
    the check before the frame is the only one. The stop becomes true during the slow write."""
    sender, result, writes, _ = run_line(
        10,
        delays_ms={1: 50},
        should_stop=lambda clock: lambda: clock.monotonic_ns() >= START_NS + 30 * MS,
    )
    # frame 0 at 0, frame 1 at 20 (its write runs to 70); frame 2 is due at 40 and is late: the
    # kill switch is already set at 70, so it is not written
    assert (result.frames_sent, result.stopped) == (2, True)
    assert len(writes) == 2
    assert [r.frame_index for r in sender.log] == [0, 1]


def test_sender_lines_have_their_own_origin_and_index() -> None:
    clock = ManualClock()
    writes: list[int] = []

    async def send_frame(frame: bytes) -> None:
        writes.append(clock.monotonic_ns())

    sender = mod("sender").PacedSender(clock, send_frame)

    async def main() -> tuple[Any, Any, Any]:
        first = await sender.send_line(frames(3))
        await clock.sleep_until_ns(clock.monotonic_ns() + 5_000 * MS)  # the agent speaks for 5 s
        second = await sender.send_line(frames(3))
        empty = await sender.send_line([])
        return first, second, empty

    first, second, empty = run_virtual(clock, main)
    assert (first.line_index, second.line_index, empty.line_index) == (0, 1, 2)
    assert (empty.frames_sent, empty.stopped) == (0, False)
    # the pause between lines is silence, not lateness
    assert [r.lateness_ns for r in sender.log] == [0] * 6
    assert [(r.line_index, r.frame_index) for r in sender.log] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
    ]
    assert writes[3] - writes[2] == 5_000 * MS  # the second line started when the pause ended
    assert sender.log[3].scheduled_ns == writes[3]


def test_sender_cancellation_stops_cleanly_and_the_sender_can_be_reused() -> None:
    clock = ManualClock()
    writes: list[int] = []

    async def send_frame(frame: bytes) -> None:
        writes.append(clock.monotonic_ns())

    sender = mod("sender").PacedSender(clock, send_frame)

    async def main() -> None:
        task = asyncio.ensure_future(sender.send_line(frames(100)))
        await clock.sleep_until_ns(clock.monotonic_ns() + 70 * MS)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        again = await sender.send_line(frames(2))
        assert again.frames_sent == 2

    run_virtual(clock, main)
    assert [r.line_index for r in sender.log] == [0, 0, 0, 0, 1, 1]
    assert [r.frame_index for r in sender.log[:4]] == [0, 1, 2, 3]


def test_sender_log_is_a_copy() -> None:
    sender, _, _, _ = run_line(3)
    log = sender.log
    assert isinstance(log, tuple)
    assert len(sender.log) == 3
    with pytest.raises(AttributeError):
        log[0].sent_ns = 0


def test_split_frames_cuts_whole_twenty_ms_frames_and_pads_the_last_with_silence() -> None:
    split = mod("sender").split_frames
    pcm = bytes(range(256)) * 10  # 2,560 bytes
    out = split(pcm, sample_rate_hz=16000, codec="pcm_s16le")
    assert [len(f) for f in out] == [640] * 4
    assert b"".join(out) == pcm  # 2,560 = exactly 4 frames: no padding
    longer = split(pcm + b"\x07\x07", sample_rate_hz=16000, codec="pcm_s16le")
    assert [len(f) for f in longer] == [640] * 5
    assert longer[4] == b"\x07\x07" + b"\x00" * 638  # digital silence in 16-bit PCM
    mulaw = split(b"\x01" * 161, sample_rate_hz=8000, codec="mulaw")
    assert [len(f) for f in mulaw] == [160, 160]
    assert mulaw[1] == b"\x01" + b"\xff" * 159  # 0xff is the mu-law code of a zero sample
    assert split(b"", sample_rate_hz=8000, codec="mulaw") == []
    assert [len(f) for f in split(bytes(1920), sample_rate_hz=48000, codec="pcm_s16le")] == [1920]
    for bad in (
        {"sample_rate_hz": 8000, "codec": "alaw"},
        {"sample_rate_hz": 8025, "codec": "mulaw"},
        {"sample_rate_hz": 0, "codec": "mulaw"},
    ):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            split(b"\x00" * 10, **bad)


def test_system_clock_never_wakes_early_and_a_past_deadline_returns_at_once() -> None:
    clock = mod("clock").SystemClock()
    first = clock.monotonic_ns()
    assert isinstance(first, int)
    assert clock.monotonic_ns() >= first

    async def main() -> tuple[int, int, int]:
        start = clock.monotonic_ns()
        deadline = start + 30 * MS
        await clock.sleep_until_ns(deadline)
        woke = clock.monotonic_ns()
        await clock.sleep_until_ns(start)  # in the past
        return deadline, woke, clock.monotonic_ns() - woke

    deadline, woke, past_cost = asyncio.run(main())
    assert woke >= deadline  # never early
    assert past_cost < 200 * MS  # a past deadline does not sleep


def test_the_clock_protocol_has_exactly_the_two_methods_the_fakes_implement() -> None:
    clock_module = mod("clock")
    assert {"Clock", "SystemClock"} <= set(clock_module.__all__)
    for name in ("monotonic_ns", "sleep_until_ns"):
        assert hasattr(clock_module.Clock, name)
        assert hasattr(clock_module.SystemClock, name)
        assert hasattr(ManualClock, name)
