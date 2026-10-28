# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch A tests (call state machine, turn-taking, G.711, recorder,
timing). Contract: docs/phase4/api-surface-batch-a.md.

Nothing here imports a batch A module of ``tracer_evalcore`` at module level: each loader imports
lazily, so that before the modules exist every test fails on its own with ``ModuleNotFoundError``
and the directory still collects. The reference implementations below (a table of the call state
machine, the two G.711 derivations, a turn-taking decision, a WAV reader and a timing extraction)
share no code with ``tracer_evalcore``; they are written differently from the contract's prose
on purpose. All values are invented. Time is an integer number of nanoseconds, written with
``ms()`` where the contract speaks of milliseconds.
"""

from __future__ import annotations

import importlib
import struct
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from hypothesis import strategies as st

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
PACKAGES = ROOT / "packages"
EVALCORE_SRC = PACKAGES / "evalcore" / "src" / "tracer_evalcore"
ADR26 = ROOT / "docs" / "adr" / "0026-simulated-caller-architecture.md"
ADR27 = ROOT / "docs" / "adr" / "0027-simulated-run-records.md"
SCOPE4 = ROOT / "docs" / "phase4" / "SCOPE.md"

#: The batch A modules of tracer-evalcore (contract section 1), in file order.
BATCH_A_MODULES = ("callstate", "turntaking", "g711", "recorder", "simtiming")

NS_PER_MS = 1_000_000
#: An absolute instant far from zero: a test that forgets the origin shows it.
T0 = 7_000_000_000_000


def ms(value: int) -> int:
    """``value`` milliseconds in nanoseconds."""
    return value * NS_PER_MS


def callstate_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.callstate")


def turntaking_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.turntaking")


def g711_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.g711")


def recorder_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.recorder")


def simtiming_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.simtiming")


def simtypes_mod() -> ModuleType:
    return importlib.import_module("tracer_evalcore.simtypes")


# ------------------------------------------------------------------------ call state machine

STATES = (
    "preflight",
    "connecting",
    "connected",
    "caller_speaking",
    "awaiting_agent",
    "agent_speaking",
    "ending",
    "ended",
)
EVENTS = (
    "connect_started",
    "connected",
    "connect_failed",
    "caller_line_started",
    "caller_line_finished",
    "agent_active",
    "agent_silent",
    "end_requested",
    "hangup_complete",
)
END_REASONS = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
)
REQUESTABLE = tuple(r for r in END_REASONS if r != "setup_failed")

#: The pinned transition table, written as a grid: one row per state, one cell per event in the
#: order of ``EVENTS``. ``-`` is a refused cell (the event cannot happen in that state).
GRID = {
    "preflight": "connecting - - - - - - ending -",
    "connecting": "- connected ending - - - - ending -",
    "connected": "- - - caller_speaking - agent_speaking connected ending -",
    "caller_speaking": "- - - - awaiting_agent caller_speaking caller_speaking ending -",
    "awaiting_agent": "- - - caller_speaking - agent_speaking awaiting_agent ending -",
    "agent_speaking": "- - - caller_speaking - agent_speaking awaiting_agent ending -",
    "ending": "ending ending ending ending ending ending ending ending ended",
    "ended": "ended ended ended ended ended ended ended ended ended",
}


def grid_cell(state: str, event: str) -> str | None:
    cells = GRID[state].split()
    assert len(cells) == len(EVENTS), state
    cell = cells[EVENTS.index(event)]
    return None if cell == "-" else cell


def grid_next(state: str, event: str) -> str | None:
    return grid_cell(state, event)


#: A shortest event path from ``preflight`` to each state, written by hand from the grid. Each
#: item is ``(event, reason)``.
PATHS: dict[str, list[tuple[str, str | None]]] = {
    "preflight": [],
    "connecting": [("connect_started", None)],
    "connected": [("connect_started", None), ("connected", None)],
    "caller_speaking": [
        ("connect_started", None),
        ("connected", None),
        ("caller_line_started", None),
    ],
    "awaiting_agent": [
        ("connect_started", None),
        ("connected", None),
        ("caller_line_started", None),
        ("caller_line_finished", None),
    ],
    "agent_speaking": [
        ("connect_started", None),
        ("connected", None),
        ("agent_active", None),
    ],
    "ending": [
        ("connect_started", None),
        ("connected", None),
        ("end_requested", "max_duration"),
    ],
    "ended": [
        ("connect_started", None),
        ("connected", None),
        ("end_requested", "max_duration"),
        ("hangup_complete", None),
    ],
}


def drive(machine: Any, path: Sequence[tuple[str, str | None]]) -> Any:
    for event, reason in path:
        machine.apply(event, reason)
    return machine


def ref_reachable() -> set[str]:
    seen = {"preflight"}
    todo = ["preflight"]
    while todo:
        state = todo.pop()
        for event in EVENTS:
            nxt = grid_next(state, event)
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


# ------------------------------------------------------------------------ PCM frames


def pcm(samples: Sequence[int]) -> bytes:
    """16-bit little-endian PCM of ``samples``."""
    return struct.pack(f"<{len(samples)}h", *samples)


def unpcm(data: bytes) -> list[int]:
    assert len(data) % 2 == 0
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def silent(n: int = 160) -> bytes:
    return pcm([0] * n)


def active(n: int = 160, amplitude: int = 100) -> bytes:
    """A frame of ``n`` samples whose every sample is ``amplitude`` (non-zero)."""
    return pcm([amplitude] * n)


def log_frame(
    t_ns: int,
    payload: bytes,
    *,
    rate: int = 8000,
    channels: int = 1,
    codec: str = "pcm_s16le",
) -> Any:
    return recorder_mod().LogFrame(
        t_ns=t_ns, payload=payload, sample_rate_hz=rate, channels=channels, codec=codec
    )


def fmt_pcm(rate: int = 8000) -> Any:
    return recorder_mod().RecorderFormat(sample_rate_hz=rate, codec="pcm_s16le")


# ------------------------------------------------------------------------ G.711 derivations


def ref_mulaw(code: int) -> int:
    """Segment form of the ITU-T G.711 mu-law decoder (14-bit value times 4).

    Code bits after inversion: sign (1 = negative), 3 segment bits, 4 mantissa bits. The
    magnitude of segment ``s``, mantissa ``m`` is ``4 * (((2 * m + 33) << s) - 33)``.
    """
    inverted = (~code) & 0xFF
    sign = inverted >> 7
    segment = (inverted >> 4) & 7
    mantissa = inverted & 15
    magnitude = 4 * (((2 * mantissa + 33) << segment) - 33)
    return -magnitude if sign else magnitude


def ref_alaw(code: int) -> int:
    """Segment form of the ITU-T G.711 A-law decoder (13-bit value times 8).

    Even bits are inverted on the wire (``^ 0x55``); then sign (1 = positive), 3 segment bits and
    4 mantissa bits. Segment 0 has magnitude ``8 * (2 * m + 1)``, segment ``s >= 1`` has
    ``8 * ((2 * m + 33) << (s - 1))``.
    """
    coded = code ^ 0x55
    sign = coded >> 7
    segment = (coded >> 4) & 7
    mantissa = coded & 15
    magnitude = (
        8 * (2 * mantissa + 1) if segment == 0 else 8 * ((2 * mantissa + 33) << (segment - 1))
    )
    return magnitude if sign else -magnitude


# ------------------------------------------------------------------------ WAV reader


def read_wav(wav: bytes) -> dict[str, Any]:
    """Parse a canonical 44-byte-header PCM WAV; every field is asserted, nothing is trusted."""
    assert wav[:4] == b"RIFF", "not a RIFF/WAVE file"
    assert wav[8:12] == b"WAVE", "not a RIFF/WAVE file"
    (riff_size,) = struct.unpack("<I", wav[4:8])
    assert wav[12:16] == b"fmt "
    fmt_size, tag, channels, rate, byte_rate, align, bits = struct.unpack("<IHHIIHH", wav[16:36])
    assert wav[36:40] == b"data"
    (data_size,) = struct.unpack("<I", wav[40:44])
    assert len(wav) == 44 + data_size, "data size disagrees with the file size"
    assert riff_size == 36 + data_size
    data = wav[44:]
    frames = data_size // 4
    samples = struct.unpack(f"<{frames * 2}h", data) if frames else ()
    return {
        "fmt_size": fmt_size,
        "tag": tag,
        "channels": channels,
        "rate": rate,
        "byte_rate": byte_rate,
        "align": align,
        "bits": bits,
        "n": frames,
        "left": list(samples[0::2]),
        "right": list(samples[1::2]),
        "data": data,
    }


def sample_at(t_ns: int, origin_ns: int, rate: int) -> int:
    """The sample index of an instant: floor of the elapsed time times the rate (exact)."""
    return ((t_ns - origin_ns) * rate) // 1_000_000_000


# ------------------------------------------------------------------------ turn-taking reference


def ref_turn_decision(
    *,
    first: bool,
    speaks_first: bool,
    wait_ms: int,
    max_silence_ms: int,
    begin_ns: int,
    barge_ms: int | None,
    frames: Sequence[tuple[int, bool]],
) -> tuple[str, int | None, str | None]:
    """The final decision of one wait over a complete recorded frame sequence.

    ``frames`` are ``(arrival_ns, active)`` in arrival order. The rule, from the contract: a wait
    ends at ``begin_ns + max_silence_ms``; a frame arriving at or after that instant is not
    considered (the end is decided first); the caller speaks first at ``begin_ns`` when it is the
    first wait and ``speaks_first``; with a barge-in at ``onset + barge_ms``; otherwise at
    ``last_active + wait_ms`` once a gap of ``wait_ms`` follows an active frame; the speak instant
    wins a tie with the end.
    """
    end = begin_ns + ms(max_silence_ms)
    if first and speaks_first:
        return ("speak", begin_ns, "caller_first")
    act = [t for t, is_active in frames if is_active and begin_ns <= t < end]
    candidate: tuple[int, str] | None = None
    if barge_ms is not None:
        if act:
            candidate = (act[0] + ms(barge_ms), "barge_in")
    else:
        for i, t in enumerate(act):
            due = t + ms(wait_ms)
            following = act[i + 1] if i + 1 < len(act) else None
            if following is None or following >= due:
                candidate = (due, "agent_silent")
                break
    if candidate is not None and candidate[0] <= end:
        return ("speak", candidate[0], candidate[1])
    return ("end", end, "max_silence")


# ------------------------------------------------------------------------ strategies


#: Increasing instants in ms (gaps of at least 1) for frame sequences.
@st.composite
def frame_sequences(
    draw: st.DrawFn, *, max_frames: int = 14, max_gap_ms: int = 40, start_ms: int = 0
) -> list[tuple[int, bool]]:
    n = draw(st.integers(min_value=0, max_value=max_frames))
    t = start_ms
    out: list[tuple[int, bool]] = []
    for _ in range(n):
        t += draw(st.integers(min_value=0, max_value=max_gap_ms))
        out.append((ms(t), draw(st.booleans())))
    return out
