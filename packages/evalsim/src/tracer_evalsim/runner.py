# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The asyncio call runner (ADR 0026 rules 8 to 10, 15, 17; P4-21 steps 9 to 11).

``run_call`` places one call over an already-built transport: it connects, plays the turn-taking
policy against the agent's audio, sends the caller's lines through the paced sender, ends the call
with exactly one end reason and hangs up. Everything that varies is injected: the clock, the
connector, the line provider (scripted, replay or LLM), the in-call transcriber of an LLM caller and
the stop flag (signals, kill switch). The runner opens no socket itself, reads no environment and
no file; its result is plain data (two frame logs, the caller turns, counts, the end reason) from
which the command builds the recording, the timing document and the bundle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, NoReturn, Protocol

from tracer_evalcore.callstate import CallMachine
from tracer_evalcore.recorder import LogFrame, decode_to_pcm16
from tracer_evalcore.simtiming import SelfTimes
from tracer_evalcore.stt import SttCapReachedError, SttError
from tracer_evalcore.turntaking import (
    TurnDecision,
    TurnTaker,
    TurnTakingPolicy,
    agent_frame_is_active,
)
from tracer_evalsim.clock import Clock
from tracer_evalsim.dialects.base import (
    AgentEvent,
    Dialect,
    InboundReader,
    WireConnection,
    hang_up,
)
from tracer_evalsim.errors import SetupFailedError, TransportError
from tracer_evalsim.limits import FRAME_NS, MAX_AGENT_AUDIO_BYTES, agent_audio_cap_bytes
from tracer_evalsim.sender import PacedSender
from tracer_evalsim.watchdog import Watchdog

__all__ = [
    "CallResult",
    "CallTurnRecord",
    "CallerEnd",
    "LineProvider",
    "PreparedLine",
    "RunnerSettings",
    "SegmentReader",
    "StopFlag",
    "run_call",
]

_MS: Final = 1_000_000


class StopFlag:
    """A request to end the call (a signal, the kill switch): once set it stays set."""

    def __init__(self) -> None:
        self.requested = False
        self.signals = 0
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def request(self) -> None:
        self.requested = True
        self.signals += 1
        for listener in list(self._listeners):
            listener()


@dataclass(frozen=True)
class PreparedLine:
    """A caller line ready to send: its text and its 20 ms wire frames."""

    text: str
    frames: tuple[bytes, ...]
    brain_ms: int | None = None
    tts_ms: int | None = None
    invalid_outputs: int = 0
    regenerations: int = 0
    requests: int = 0


@dataclass(frozen=True)
class CallerEnd:
    """The caller has nothing more to say: the end reason of the brain."""

    end_reason: str
    invalid_outputs: int = 0
    regenerations: int = 0
    cap_reached: str | None = None
    requests: int = 0
    failure_code: str | None = None
    failure_status: int | None = None


class LineProvider(Protocol):
    """The caller brain as the runner sees it. ``needs_agent_text`` is true for the LLM caller."""

    mode: str
    needs_agent_text: bool

    async def next(self, turn_index: int, agent_text: str | None) -> PreparedLine | CallerEnd: ...


#: ``(caller_log, agent_log, origin_ns)`` -> the text of the agent segment that just ended
SegmentReader = Callable[[tuple[LogFrame, ...], tuple[LogFrame, ...], int], Awaitable[str | None]]


@dataclass(frozen=True)
class RunnerSettings:
    dialect: Dialect
    policy: TurnTakingPolicy
    max_duration_ms: int
    barge_ins: Mapping[int, int] = field(default_factory=dict)
    max_agent_audio_bytes: int = MAX_AGENT_AUDIO_BYTES


@dataclass(frozen=True)
class CallTurnRecord:
    """One caller line of which at least one frame was sent."""

    turn_seq: int
    text: str
    first_frame_ns: int
    last_frame_ns: int
    frames_sent: int
    frames_planned: int
    barge_in_after_ms: int | None
    cause: str
    self_times: SelfTimes

    @property
    def complete(self) -> bool:
        return self.frames_sent == self.frames_planned


@dataclass(frozen=True)
class CallResult:
    end_reason: str
    call_established: bool
    connect_started_ns: int | None
    connect_ended_ns: int | None
    observed_until_ns: int
    caller_frames: tuple[LogFrame, ...]
    agent_frames: tuple[LogFrame, ...]
    turns: tuple[CallTurnRecord, ...]
    cap_reached: str | None
    close_code: int | None
    failure_code: str | None
    watchdog_fired: bool
    counts: Mapping[str, int]
    failure_http_status: int | None = None


def _ms(ns: int) -> int:
    return ns // _MS


class _Ended(Exception):
    """Internal: the call is over for ``reason``."""

    def __init__(
        self,
        reason: str,
        cap: str | None = None,
        code: str | None = None,
        close_code: int | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cap = cap
        self.code = code
        self.close_code = close_code
        self.status = status


async def run_call(
    settings: RunnerSettings,
    *,
    clock: Clock,
    connect: Callable[[], Awaitable[WireConnection]],
    lines: LineProvider,
    stop: StopFlag,
    segments: SegmentReader | None = None,
    kill_switch: Callable[[], bool] | None = None,
) -> CallResult:
    """Place one call. Every outcome of the call is a result; only a ``BaseException`` escapes.

    ``kill_switch`` answers whether the kill-switch file is present. It is asked before the
    connection and before every frame, and while the caller waits it is asked on a tick of one
    frame, so its appearance hangs up within one frame of the injected clock.
    """
    runner = _Run(settings, clock, connect, lines, stop, segments, kill_switch)
    return await runner.run()


class _Run:
    def __init__(
        self,
        settings: RunnerSettings,
        clock: Clock,
        connect: Callable[[], Awaitable[WireConnection]],
        lines: LineProvider,
        stop: StopFlag,
        segments: SegmentReader | None,
        kill_switch: Callable[[], bool] | None = None,
    ) -> None:
        self.kill_switch = kill_switch
        self.s = settings
        self.clock = clock
        self.connect = connect
        self.lines = lines
        self.stop = stop
        self.segments = segments
        self.machine = CallMachine()
        self.taker = TurnTaker(settings.policy)
        self.wake = asyncio.Event()
        self.conn: WireConnection | None = None
        self.origin = 0
        self.deadline_ns = 0
        self.caller_log: list[LogFrame] = []
        self.agent_log: list[LogFrame] = []
        self.turns: list[CallTurnRecord] = []
        self.reader: InboundReader | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.watchdog_fired = False
        self.agent_bytes = 0
        self.audio_cap = agent_audio_cap_bytes(
            bytes_per_second=settings.dialect.realtime_bytes_per_second,
            max_duration_ms=settings.max_duration_ms,
            ceiling=settings.max_agent_audio_bytes,
        )
        self.watchdog: Watchdog | None = None
        self.released = False
        self.empty_audio = 0
        self.barge_ins_fired = 0
        self.invalid = 0
        self.regenerations = 0
        self.brain_requests = 0
        self.llm_requests = 0
        self.last_active_ns: int | None = None
        self.first_active_ns: int | None = None
        self.wait_active = False
        self.close_code: int | None = None
        self.failure_code: str | None = None
        self.cap: str | None = None
        self.pcm_cache: dict[int, bytes] = {}

    # ------------------------------------------------------------------------------ inbound
    def _on_event(self, event: AgentEvent) -> None:
        if event.kind != "audio":
            return
        audio = event.audio or b""
        if not audio:
            self.empty_audio += 1
            return
        dialect = self.s.dialect
        self.agent_bytes += len(audio)
        self.agent_log.append(
            LogFrame(event.arrival_ns, audio, dialect.sample_rate_hz, 1, dialect.wire_codec)
        )
        pcm = decode_to_pcm16(dialect.wire_codec, audio)
        if self.taker.agent_frame(event.arrival_ns, pcm):
            self.last_active_ns = event.arrival_ns
            if self.first_active_ns is None:
                self.first_active_ns = event.arrival_ns
            self.wait_active = True
        if self.agent_bytes > self.audio_cap:
            raise TransportError(code="message_too_large")
        self.wake.set()

    async def _read(self) -> None:
        assert self.conn is not None  # noqa: S101
        assert self.reader is not None  # noqa: S101
        end = await self.reader.run(self.conn, self._on_event)
        self.wake.set()
        if not end.clean:
            raise _Ended("transport_error", code="connection_lost", close_code=end.close_code)
        raise _Ended("agent_hangup", close_code=end.close_code)

    def _reader_end(self) -> _Ended | None:
        task = self.reader_task
        if task is None or not task.done():
            return None
        if task.cancelled():
            return _Ended("transport_error", code="reader_cancelled")
        error = task.exception()
        if isinstance(error, _Ended):
            return error
        if isinstance(error, TransportError):
            return _Ended("transport_error", code=error.code, close_code=error.close_code)
        if error is not None:
            raise error
        return _Ended("agent_hangup")

    # ------------------------------------------------------------------------------ checks
    def _check(self) -> None:
        now = self.clock.monotonic_ns()
        ended = self._reader_end()
        if self.watchdog_fired:
            raise _Ended("max_duration")
        if self.stop.requested:
            raise _Ended("kill_switch")
        if self.kill_switch is not None and self.kill_switch():
            raise _Ended("kill_switch")
        if now >= self.deadline_ns:
            raise _Ended("max_duration")
        if ended is not None:
            raise ended

    def _should_stop(self) -> bool:
        try:
            self._check()
        except _Ended:
            return True
        return False

    async def _sleep_or_wake(self, until_ns: int) -> None:
        self.wake.clear()
        now = self.clock.monotonic_ns()
        if until_ns <= now:
            await asyncio.sleep(0)
            return
        sleeper = asyncio.ensure_future(self.clock.sleep_until_ns(until_ns))
        waker = asyncio.ensure_future(self.wake.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, waker):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, waker, return_exceptions=True)

    # ------------------------------------------------------------------------------ the call
    async def run(self) -> CallResult:
        clock = self.clock
        started = clock.monotonic_ns()
        self.machine.apply("connect_started")
        established = False
        ended: _Ended | None = None
        ended_at = started
        if self.stop.requested or (self.kill_switch is not None and self.kill_switch()):
            ended = _Ended("kill_switch")
            self.machine.apply("end_requested", "kill_switch")
        else:
            try:
                self.conn = await self.connect()
            except SetupFailedError as error:
                self.machine.apply("connect_failed")
                ended = _Ended("setup_failed", code=error.code)
                self.failure_code = error.code
            else:
                self.machine.apply("connected")
                established = True
        connect_ended = clock.monotonic_ns()
        if established:
            try:
                ended = await self._call(connect_ended)
            finally:
                await self._release()  # on every exit, a BaseException included (BL-339)
            ended_at = clock.monotonic_ns()
        else:
            ended_at = connect_ended
        assert ended is not None  # noqa: S101
        self.machine.apply("hangup_complete")
        reason = self.machine.end_reason or ended.reason
        counts = {
            "caller_turns_started": len(self.turns),
            "caller_turns_completed": sum(1 for t in self.turns if t.complete),
            "barge_ins_fired": self.barge_ins_fired,
            "brain_requests": self.brain_requests,
            "brain_invalid_outputs": self.invalid,
            "brain_regenerations": self.regenerations,
            "llm_requests": self.llm_requests,
            "frames_sent": len(self.caller_log),
            "frames_received": len(self.agent_log),
            "inbound_messages_ignored": (
                (self.reader.stats.ignored if self.reader is not None else 0) + self.empty_audio
            ),
            "signals_received": self.stop.signals,
        }
        return CallResult(
            end_reason=reason,
            call_established=established,
            connect_started_ns=started,
            connect_ended_ns=connect_ended,
            observed_until_ns=ended_at,
            caller_frames=tuple(self.caller_log),
            agent_frames=tuple(self.agent_log),
            turns=tuple(self.turns),
            cap_reached=self.cap,
            close_code=self.close_code,
            failure_code=self.failure_code or ended.code,
            watchdog_fired=self.watchdog_fired,
            counts=counts,
            failure_http_status=ended.status,
        )

    async def _hang_up(self) -> None:
        if self.conn is not None:
            await hang_up(self.conn, self.s.dialect)

    async def _watchdog_hang_up(self) -> None:
        self.watchdog_fired = True
        self.wake.set()
        await self._hang_up()

    async def _call(self, origin: int) -> _Ended:
        assert self.conn is not None  # noqa: S101
        dialect = self.s.dialect
        self.origin = origin
        self.deadline_ns = origin + self.s.max_duration_ms * _MS
        self.reader = InboundReader(dialect, self.clock)
        self.reader_task = asyncio.ensure_future(self._read())
        self.stop.subscribe(self.wake.set)
        watchdog = Watchdog(
            self.clock,
            max_duration_ns=self.s.max_duration_ms * _MS,
            hang_up=self._watchdog_hang_up,
        )
        watchdog.start()
        self.watchdog = watchdog
        try:
            for message in dialect.opening_messages():
                await self.conn.send(message)
            await self._turns()
        except _Ended as ended:
            result = ended
        except TransportError as error:
            result = _Ended("transport_error", code=error.code, close_code=error.close_code)
            if error.code == "connection_closed":
                # a write into a closed connection: if the agent sent a Close frame, that is the
                # reason (the reader needs a turn of the loop to have seen it)
                for _ in range(5):
                    await asyncio.sleep(0)
                seen = self._reader_end()
                if seen is not None and seen.reason == "agent_hangup":
                    result = seen
        if self.watchdog_fired and result.reason in ("transport_error", "agent_hangup"):
            result = _Ended("max_duration")  # the watchdog closed the connection itself
        self.failure_code = result.code
        self.close_code = result.close_code
        if result.cap is not None:
            self.cap = result.cap
        self.machine.apply("end_requested", result.reason)
        try:
            await self._hang_up()
        except TransportError:
            pass
        finally:
            await self._release()
        return result

    async def _release(self) -> None:
        """Close the watchdog, cancel the reader and retrieve its outcome; once (BL-339)."""
        if self.released:
            return
        self.released = True
        try:
            if self.watchdog is not None:
                await self.watchdog.aclose()
        finally:
            task = self.reader_task
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _turns(self) -> NoReturn:
        policy = self.s.policy
        sender = PacedSender(self.clock, self._send_frame)
        turn = 0
        first = True
        while True:
            barge = self.s.barge_ins.get(turn)
            now = self.clock.monotonic_ns()
            self.taker.begin_wait(
                now, barge_in_after_ms=None if (first and policy.caller_speaks_first) else barge
            )
            self.wait_active = False
            self.first_active_ns = None
            wait_started = now
            decision = await self._await_decision(wait_started, barge)
            decided = self.clock.monotonic_ns()
            agent_text: str | None = None
            stt_ms: int | None = None
            pending = self.lines.needs_agent_text
            if pending:
                self.taker.brain_started(decided)
            if (
                self.lines.needs_agent_text
                and self.segments is not None
                and not first
                and self.wait_active
            ):
                started = self.clock.monotonic_ns()
                try:
                    agent_text = await self.segments(
                        tuple(self.caller_log), tuple(self.agent_log), self.origin
                    )
                except SttCapReachedError:
                    raise _Ended("spend_cap", cap="stt_seconds") from None
                except SttError:
                    raise _Ended("transport_error", code="stt_failed") from None
                stt_ms = _ms(self.clock.monotonic_ns() - started)
            item = await self.lines.next(turn, agent_text)
            if pending:
                self.taker.brain_finished(self.clock.monotonic_ns())
            if isinstance(item, CallerEnd):
                self.invalid += item.invalid_outputs
                self.regenerations += item.regenerations
                self.llm_requests += item.requests
                self.brain_requests += 1 if item.requests else 0
                raise _Ended(
                    item.end_reason,
                    cap=item.cap_reached,
                    code=item.failure_code,
                    status=item.failure_status,
                )
            self.invalid += item.invalid_outputs
            self.regenerations += item.regenerations
            self.llm_requests += item.requests
            self.brain_requests += 1 if item.requests else 0
            self._check()
            self.taker.line_started(self.clock.monotonic_ns())
            if decision.cause == "barge_in":
                self.barge_ins_fired += 1
            await self._send_line(sender, turn, item, decision, barge, stt_ms)
            first = False
            turn += 1

    async def _send_frame(self, frame: bytes) -> None:
        assert self.conn is not None  # noqa: S101
        stream_ms = _ms(self.clock.monotonic_ns() - self.origin)
        await self.conn.send(self.s.dialect.encode_audio(frame, stream_ms=stream_ms))

    async def _send_line(
        self,
        sender: PacedSender,
        turn: int,
        item: PreparedLine,
        decision: TurnDecision,
        barge: int | None,
        stt_ms: int | None,
    ) -> None:
        dialect = self.s.dialect
        self.machine.apply("caller_line_started")
        before = len(sender.log)
        first_ns: int | None = None
        error: TransportError | None = None
        try:
            await sender.send_line(item.frames, should_stop=self._should_stop)
        except TransportError as failure:
            error = failure
        records = sender.log[before:]
        for record in records:
            frame = item.frames[record.frame_index]
            self.caller_log.append(
                LogFrame(record.sent_ns, frame, dialect.sample_rate_hz, 1, dialect.wire_codec)
            )
        if records:
            first_ns = records[0].sent_ns
            last_ns = records[-1].sent_ns
            last_active = self._last_active_before(first_ns)
            wait_ms: int | None = None
            if decision.cause == "agent_silent" and decision.at_ns is not None:
                at_decision = self._last_active_before(decision.at_ns)
                if at_decision is not None:
                    wait_ms = _ms(decision.at_ns - at_decision)
            elif decision.cause == "barge_in" and barge is not None:
                wait_ms = barge
            reply_ms = (
                None
                if decision.cause == "caller_first" or last_active is None
                else _ms(first_ns - last_active)
            )
            self.turns.append(
                CallTurnRecord(
                    turn_seq=turn,
                    text=item.text,
                    first_frame_ns=first_ns,
                    last_frame_ns=last_ns,
                    frames_sent=len(records),
                    frames_planned=len(item.frames),
                    barge_in_after_ms=barge if decision.cause == "barge_in" else None,
                    cause=decision.cause or "",
                    self_times=SelfTimes(
                        reply_ms=reply_ms,
                        wait_ms=wait_ms,
                        stt_ms=stt_ms,
                        brain_ms=item.brain_ms,
                        tts_ms=item.tts_ms,
                    ),
                )
            )
        if error is not None:
            raise error
        if len(records) < len(item.frames):
            self._check()  # raises the reason the line was cut short
            raise _Ended("kill_switch")  # pragma: no cover
        self.machine.apply("caller_line_finished")

    def _last_active_before(self, instant_ns: int) -> int | None:
        found: int | None = None
        for frame in self.agent_log:
            if frame.t_ns > instant_ns:
                break
            pcm = self.pcm_cache.get(id(frame))
            if pcm is None:
                pcm = decode_to_pcm16(frame.codec, frame.payload)
                self.pcm_cache[id(frame)] = pcm
            if agent_frame_is_active(pcm, min_peak=self.s.policy.activity_min_peak):
                found = frame.t_ns
        return found

    async def _await_decision(self, wait_started: int, barge: int | None) -> TurnDecision:
        policy = self.s.policy
        wait_ns = policy.wait_for_agent_silence_ms * _MS
        end_ns = wait_started + policy.max_silence_ms * _MS
        while True:
            self._check()
            now = self.clock.monotonic_ns()
            decision = self.taker.poll(now)
            if decision.kind == "speak":
                return decision
            if decision.kind == "end":
                raise _Ended("max_silence")
            wake_at = min(end_ns, self.deadline_ns)
            if self.kill_switch is not None:
                wake_at = min(wake_at, now + FRAME_NS)
            if self.wait_active and self.last_active_ns is not None:
                if barge is None:
                    wake_at = min(wake_at, self.last_active_ns + wait_ns)
                elif self.first_active_ns is not None:
                    wake_at = min(wake_at, self.first_active_ns + barge * _MS)
            if wake_at <= now:
                wake_at = now + FRAME_NS // 20
            await self._sleep_or_wake(wake_at)
