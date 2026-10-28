# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: PT018
"""Harness of the interop tests (P4-27): start the sample bot of ``tools/samplebot`` as a child
process on this machine, place a call to it with the real runner, and read what both sides saw.
Support module, not collected. Contract: docs/phase4/api-surface-batch-f.md sections 1 to 3.

The bot is a separate uv project with its own lock (it is not a workspace member and Pipecat is
never installed in the tracer environment), so it is run with ``uv run --project tools/samplebot
--locked python -m samplebot.scripted_bot ...`` from the tool directory. The environment of the
child is built from an allow-list: no key, no proxy, no tracer variable. The bot prints one JSON
object per line on stdout: ``ready`` once, then one ``call_summary`` per finished call (contract
section 1.4); its logs go to stderr. Nothing here skips: a bot that cannot start, exits early or
stays silent past the bound raises ``SamplebotUnavailable`` (an ``AssertionError``) with the command
to run; the marker, not this module, decides whether the tests are selected.

The caller's tones, the mu-law encoder and the frequency estimate are written here independently of
the tool and of the product, so a wrong sample format, byte order or rate on either side cannot
cancel out.
"""

from __future__ import annotations

import collections
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
SAMPLEBOT = ROOT / "tools" / "samplebot"
BOT_CWD = SAMPLEBOT
COMMAND_ENV = "TRACER_SAMPLEBOT_COMMAND"
READY_TIMEOUT_S = 120.0
SUMMARY_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 15.0
HINT = (
    "build the environment of the tool with `uv sync --locked --project tools/samplebot` and run "
    "the suite with `uv run --locked --all-groups pytest -m integration packages/evalsim/tests` "
    "(the `interop` job of ci.yml does both)"
)
FRAME_MS = 20
MS = 1_000_000
#: the stated jitter of the apparatus (ms): what a loopback call with a timer-based bot may add to
#: the injected delay beyond one frame quantum. An engineering bound, not a measurement and not a
#: claim; a change is a named spec change with the numbers that justify it (BL-282, P4-36).
JITTER_MS = 50

KIND_ID = {
    "protobuf": "pipecat-protobuf/1",
    "twilio": "twilio-media-stream/1",
    "webrtc": "smallwebrtc/1",
}
RATE = {"protobuf": 16000, "twilio": 8000, "webrtc": 48000}
_ENV_KEEP = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "SSL_CERT_FILE",
)  # fmt: skip


class SamplebotUnavailable(AssertionError):
    """The sample bot did not start. A failure of the interop job, never a skip."""


def bot_command(
    dialect_id: str, scenario: Path, sample_rate_hz: int, *, port: int = 0
) -> list[str]:
    override = os.environ.get(COMMAND_ENV)
    if override:
        head = json.loads(override)
        assert isinstance(head, list) and all(isinstance(x, str) for x in head)
    else:
        head = [
            shutil.which("uv") or "uv", "run", "--project", str(SAMPLEBOT), "--locked", "python",
            "-m", "samplebot.scripted_bot",
        ]  # fmt: skip
    return [
        *head, "--dialect", dialect_id, "--scenario", str(scenario), "--sample-rate",
        str(sample_rate_hz), "--host", "127.0.0.1", "--port", str(port),
    ]  # fmt: skip


def child_environment() -> dict[str, str]:
    environment = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    for key, value in os.environ.items():
        if key.startswith("UV_") and key != "UV_PROJECT_ENVIRONMENT":
            environment[key] = value
    environment["PYTHONUNBUFFERED"] = "1"
    environment["NO_COLOR"] = "1"
    return environment


def write_scenario(
    path: Path,
    *,
    end_of_caller_ms: int,
    replies: Sequence[tuple[int, int, int, int]],
    after_replies: str = "stay_silent",
) -> Path:
    """``replies`` are ``(delay_ms, hz, ms, level)`` of ``tracer.samplebot_scenario/1``."""
    document = {
        "format": "tracer.samplebot_scenario/1",
        "end_of_caller_ms": end_of_caller_ms,
        "replies": [
            {"delay_ms": d, "tone": {"hz": hz, "ms": ms, "level": level}}
            for d, hz, ms, level in replies
        ],
        "after_replies": after_replies,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class SampleBot:
    """One bot process serving sequential calls."""

    def __init__(self, process: subprocess.Popen[str], ready: dict[str, Any]) -> None:
        self.process = process
        self.ready = ready
        self.summaries: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        self._threads: list[threading.Thread] = []

    # ---------------------------------------------------------------- start and stop

    @classmethod
    def start(
        cls, dialect_id: str, scenario: Path, *, sample_rate_hz: int, port: int = 0
    ) -> SampleBot:
        command = bot_command(dialect_id, scenario, sample_rate_hz, port=port)
        try:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=BOT_CWD if BOT_CWD.is_dir() else None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_environment(),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as error:
            raise SamplebotUnavailable(
                f"cannot start the sample bot ({type(error).__name__}): {HINT}"
            ) from None
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        placeholder: dict[str, Any] = {}
        bot = cls(process, placeholder)
        bot._events = events
        bot._threads = [
            threading.Thread(target=bot._pump_stdout, daemon=True),
            threading.Thread(target=bot._pump_stderr, daemon=True),
        ]
        for thread in bot._threads:
            thread.start()
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                bot.stop()
                raise SamplebotUnavailable(
                    f"the sample bot was not ready within {READY_TIMEOUT_S:g} s: {HINT}\n"
                    + bot.stderr_tail()
                )
            try:
                event = events.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                code = process.poll()
                if code is not None:
                    time.sleep(0.2)  # let the pumps drain
                    raise SamplebotUnavailable(
                        f"the sample bot exited before it was ready (exit code {code}): {HINT}\n"
                        + bot.stderr_tail()
                    ) from None
                continue
            if event.get("event") == "ready":
                bot.ready = event
                return bot
            bot._record(event)

    def _pump_stdout(self) -> None:
        stream = self.process.stdout
        assert stream is not None
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                self._stderr.append("stdout: " + line.rstrip()[:200])
                continue
            if isinstance(value, dict):
                self._events.put(value)

    def _pump_stderr(self) -> None:
        stream = self.process.stderr
        assert stream is not None
        for line in stream:
            self._stderr.append(line.rstrip()[:300])

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def _record(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if event.get("event") == "call_summary" and isinstance(event.get("call"), int):
            self.summaries[event["call"]] = event

    def stop(self) -> int | None:
        process = self.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=STOP_TIMEOUT_S)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        return process.returncode

    def __enter__(self) -> SampleBot:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ---------------------------------------------------------------- what it says

    @property
    def url(self) -> str:
        url = self.ready["url"]
        assert isinstance(url, str)
        return url

    def wait_summary(self, call: int) -> dict[str, Any]:
        """The ``call_summary`` of call number ``call`` (1 for the first call of the process)."""
        deadline = time.monotonic() + SUMMARY_TIMEOUT_S
        while call not in self.summaries:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no call_summary for call {call}\n{self.stderr_tail()}"
            try:
                self._record(self._events.get(timeout=min(remaining, 0.25)))
            except queue.Empty:
                assert self.process.poll() is None, f"the bot exited\n{self.stderr_tail()}"
        return self.summaries[call]

    def events_of(self, name: str, call: int) -> list[dict[str, Any]]:
        """The events called ``name`` of call number ``call`` seen so far (call ``wait_summary``
        first: the summary is the last thing a call prints)."""
        return [e for e in self.events if e.get("event") == name and e.get("call") == call]


# ------------------------------------------------------------------------------ audio, independent


def sine_pcm(hz: int, ms: int, level: int, rate: int) -> bytes:
    n = rate * ms // 1000
    return b"".join(
        round(level * math.sin(2 * math.pi * hz * i / rate)).to_bytes(2, "little", signed=True)
        for i in range(n)
    )


def mulaw_byte(sample: int) -> int:
    """G.711 mu-law encoding of one 16-bit sample (the classic bias and clip algorithm)."""
    bias, clip = 0x84, 32635
    sign = 0x80 if sample < 0 else 0
    magnitude = min(-sample if sample < 0 else sample, clip) + bias
    exponent, mask = 7, 0x4000
    while exponent > 0 and not magnitude & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def wire_audio(kind: str, pcm: bytes) -> bytes:
    if kind != "twilio":
        return pcm
    samples = [int.from_bytes(pcm[i : i + 2], "little", signed=True) for i in range(0, len(pcm), 2)]
    return bytes(mulaw_byte(s) for s in samples)


def estimate_hz(pcm: bytes, rate: int) -> float | None:
    """The frequency of ``pcm`` from its zero crossings: the sign changes (zeros skipped), counted
    between the first and the last, over the time between them. Padding before or after the tone
    (a codec's pre-roll, a tail) does not move it."""
    samples = [int.from_bytes(pcm[i : i + 2], "little", signed=True) for i in range(0, len(pcm), 2)]
    at: list[int] = []
    previous = 0
    for index, sample in enumerate(samples):
        if sample == 0:
            continue
        sign = 1 if sample > 0 else -1
        if previous and sign != previous:
            at.append(index)
        previous = sign
    if len(at) < 2:
        return None
    return (len(at) - 1) / 2 / ((at[-1] - at[0]) / rate)


# ------------------------------------------------------------------------------ a call


@dataclass
class Placed:
    kind: str
    result: Any
    timing: dict[str, Any]
    sent_ms: list[int]

    def replies(self) -> list[list[Any]]:
        """The active agent frames after each caller line's edge and before the next line."""
        from tracer_evalcore.recorder import decode_to_pcm16
        from tracer_evalcore.turntaking import agent_frame_is_active

        turns = self.result.turns
        windows = []
        for k, turn in enumerate(turns):
            start = turn.last_frame_ns + FRAME_MS * MS
            end = turns[k + 1].first_frame_ns if k + 1 < len(turns) else 1 << 62
            windows.append((start, end))
        out: list[list[Any]] = [[] for _ in windows]
        for frame in self.result.agent_frames:
            if not agent_frame_is_active(decode_to_pcm16(frame.codec, frame.payload)):
                continue
            for k, (start, end) in enumerate(windows):
                if start <= frame.t_ns < end:
                    out[k].append(frame)
        return out

    def reply_pcm(self, k: int) -> bytes:
        from tracer_evalcore.recorder import decode_to_pcm16

        return b"".join(decode_to_pcm16(f.codec, f.payload) for f in self.replies()[k])

    def reply_ms(self, k: int) -> int:
        from tracer_evalcore.recorder import decode_to_pcm16

        return sum(
            len(decode_to_pcm16(f.codec, f.payload)) // 2 * 1000 // f.sample_rate_hz
            for f in self.replies()[k]
        )


def make_dialect(kind: str, *, counter: int = 1) -> Any:
    if kind == "protobuf":
        from tracer_evalsim.dialects.protobuf_dialect import PipecatProtobufDialect

        return PipecatProtobufDialect(sample_rate_hz=RATE[kind])
    if kind == "twilio":
        from tracer_evalsim.dialects.twilio import TwilioMediaStreamDialect

        return TwilioMediaStreamDialect(counter=counter)
    from tracer_evalsim.dialects.smallwebrtc import SmallWebRtcDialect

    return SmallWebRtcDialect(sample_rate_hz=RATE[kind])


async def open_connection(kind: str, url: str, dialect: Any) -> Any:
    from tracer_evalsim.errors import SetupFailedError
    from tracer_evalsim.ws import ConnectionLabels

    if kind == "webrtc":
        from tracer_evalsim.webrtc import make_webrtc_plan, open_webrtc

        plan = make_webrtc_plan(url, ())
        labels = ConnectionLabels(None, None, None)
        return await open_webrtc(
            plan, dialect, labels=labels, token=None, open_timeout_s=30.0, close_timeout_s=5.0
        )
    from tracer_evalsim.ws import WebSocketTarget, open_websocket

    labels = ConnectionLabels(None, None, False if kind == "twilio" else None)
    try:
        return await open_websocket(
            WebSocketTarget(url, labels), dialect, token=None, open_timeout_s=30.0,
            close_timeout_s=5.0,
        )  # fmt: skip
    except SetupFailedError as error:
        raise AssertionError(f"the sample bot refused the connection: {error}") from None


async def place_call(
    bot: SampleBot,
    kind: str,
    lines: Sequence[bytes],
    *,
    counter: int = 1,
    wait_ms: int = 300,
    max_silence_ms: int = 5000,
    max_duration_ms: int = 120_000,
) -> Placed:
    """Place one call with the real runner, the real clock and the real connection; ``lines`` are
    16-bit PCM at the rate of the dialect (they are encoded to the wire codec here)."""
    from tracer_evalcore.recorder import RecorderFormat
    from tracer_evalcore.simtiming import CallerTurn, extract_timing
    from tracer_evalcore.turntaking import TurnTakingPolicy
    from tracer_evalsim.clock import SystemClock
    from tracer_evalsim.lines import PresynthesisedLines, SynthesisedLine
    from tracer_evalsim.runner import CallerEnd, RunnerSettings, StopFlag, run_call

    dialect = make_dialect(kind, counter=counter)
    entries = [SynthesisedLine(f"line {k}", wire_audio(kind, pcm)) for k, pcm in enumerate(lines)]
    provider = PresynthesisedLines(
        "scripted", entries, CallerEnd("script_complete"),
        sample_rate_hz=dialect.sample_rate_hz, wire_codec=dialect.wire_codec,
    )  # fmt: skip
    settings = RunnerSettings(
        dialect=dialect,
        policy=TurnTakingPolicy(
            caller_speaks_first=True,
            wait_for_agent_silence_ms=wait_ms,
            max_silence_ms=max_silence_ms,
        ),
        max_duration_ms=max_duration_ms,
    )

    async def connect() -> Any:
        return await open_connection(kind, bot.url, dialect)

    result = await run_call(
        settings, clock=SystemClock(), connect=connect, lines=provider, stop=StopFlag()
    )
    assert result.call_established, (result.end_reason, result.failure_code)
    frame_ns = FRAME_MS * MS
    last_edges = [t.last_frame_ns + frame_ns for t in result.turns]
    last_edges += [f.t_ns for f in result.agent_frames]
    timing = extract_timing(
        dialect=dialect.dialect_id,
        fmt=RecorderFormat(dialect.sample_rate_hz, dialect.wire_codec),
        wait_for_agent_silence_ms=wait_ms,
        connect_started_ns=result.connect_started_ns,
        connect_ended_ns=result.connect_ended_ns,
        established=True,
        observed_until_ns=max([result.observed_until_ns, *last_edges]),
        caller_turns=[
            CallerTurn(
                t.turn_seq, t.first_frame_ns, t.last_frame_ns, t.barge_in_after_ms, t.self_times
            )
            for t in result.turns
        ],
        agent_frames=result.agent_frames,
    )
    sent = [len(pcm) // 2 * 1000 // dialect.sample_rate_hz for pcm in lines]
    return Placed(kind, result, timing, sent)


# ------------------------------------------------------------------------------ shared checks


def expected_gap_ms(end_of_caller_ms: int, delay_ms: int) -> int:
    """The gap the apparatus must read: the bot fires its timer ``end_of_caller_ms`` after the
    arrival of the last caller frame and starts the reply ``delay_ms`` later; the edge of a line is
    the send instant of its last frame plus one frame."""
    return end_of_caller_ms + delay_ms - FRAME_MS


def check_round_trip(
    placed: Placed,
    summary: dict[str, Any],
    *,
    kind: str,
    caller_hz: Sequence[int],
    replies: Sequence[tuple[int, int, int]],
    end_of_caller_ms: int,
    hz_rel: float,
    ms_abs: int,
    gap_upper_ms: int,
    audio_ms_abs: int,
) -> None:
    """What every dialect must show after a two-line call against ``scripted_bot``: ``replies`` are
    ``(delay_ms, hz, ms)``. Both sides' numbers are compared with what was sent, by independent
    readers (the harness's own zero-crossing estimate and sums of frame durations)."""
    result, timing = placed.result, placed.timing
    assert result.end_reason == "script_complete", (result.end_reason, result.failure_code)
    assert len(result.turns) == len(caller_hz) == len(replies)
    assert all(t.complete for t in result.turns)
    rate = RATE[kind]
    assert result.agent_frames
    assert {f.sample_rate_hz for f in result.agent_frames} == {rate}
    assert {f.channels for f in result.agent_frames} == {1}
    quantum = timing["quantum_ms"]
    for k, (delay, hz, ms) in enumerate(replies):
        # what the agent said, as the simulator heard it
        assert placed.reply_ms(k) == pytest.approx(ms, abs=ms_abs), (kind, k, placed.reply_ms(k))
        estimate = estimate_hz(placed.reply_pcm(k), rate)
        assert estimate is not None
        assert abs(estimate - hz) <= hz * hz_rel, (kind, k, estimate, hz)
        # when it said it: never before the bot could know the line had ended, not much later
        gap = timing["turns"][k]["gap_ms"]
        assert gap is not None, (kind, k)
        expected = expected_gap_ms(end_of_caller_ms, delay)
        assert gap >= expected - quantum, (kind, k, gap, expected, quantum)
        assert gap <= expected + gap_upper_ms, (kind, k, gap, expected)
    assert timing["answered_caller_turns"] == len(replies)
    assert timing["overlap_ms"] == 0
    # what the bot heard of the caller
    assert summary["event"] == "call_summary"
    assert summary["dialect"] == KIND_ID[kind]
    assert summary["ended_by"] == "caller_closed"
    assert summary["replies_started"] == len(replies)
    turns = summary["caller_turns"]
    assert len(turns) == len(caller_hz), turns
    for k, turn in enumerate(turns):
        assert abs(turn["audio_ms"] - placed.sent_ms[k]) <= audio_ms_abs, (kind, k, turn)
        assert turn["hz"] is not None
        assert abs(turn["hz"] - caller_hz[k]) <= caller_hz[k] * hz_rel, (kind, k, turn)
    assert summary["inbound_frames"] >= 1
