# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The call state machine (ADR 0026 rule 9; contract docs/phase4/api-surface-batch-a.md section 2).

Pure and table-driven: eight states, nine events, eleven end reasons. No clock, no randomness and
no I/O. A call ends in ``ended`` with exactly one end reason; nothing changes after ``ended``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.simtypes import CALL_STATES, END_REASONS

__all__ = [
    "CALL_EVENTS",
    "PRECONNECT_END_REASONS",
    "REQUESTABLE_END_REASONS",
    "TRANSITIONS",
    "CallMachine",
    "CallStateError",
    "Transition",
    "effective_max_duration_ms",
]

CALL_EVENTS: Final = (
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
REQUESTABLE_END_REASONS: Final = tuple(r for r in END_REASONS if r != "setup_failed")
PRECONNECT_END_REASONS: Final = ("kill_switch",)

_N: Final = None  # a refused cell: the event cannot happen in that state (a runner defect)
_ROWS: Final[dict[str, tuple[str | None, ...]]] = {
    #                 cs            cn           cf        ls                lf                aa
    "preflight": ("connecting", _N, _N, _N, _N, _N, _N, "ending", _N),
    "connecting": (_N, "connected", "ending", _N, _N, _N, _N, "ending", _N),
    "connected": (
        _N,
        _N,
        _N,
        "caller_speaking",
        _N,
        "agent_speaking",
        "connected",
        "ending",
        _N,
    ),
    "caller_speaking": (
        _N,
        _N,
        _N,
        _N,
        "awaiting_agent",
        "caller_speaking",
        "caller_speaking",
        "ending",
        _N,
    ),
    "awaiting_agent": (
        _N,
        _N,
        _N,
        "caller_speaking",
        _N,
        "agent_speaking",
        "awaiting_agent",
        "ending",
        _N,
    ),
    "agent_speaking": (
        _N,
        _N,
        _N,
        "caller_speaking",
        _N,
        "agent_speaking",
        "awaiting_agent",
        "ending",
        _N,
    ),
    "ending": ("ending",) * 8 + ("ended",),
    "ended": ("ended",) * 9,
}
TRANSITIONS: Final[Mapping[tuple[str, str], str | None]] = MappingProxyType(
    {(state, event): row[i] for state, row in _ROWS.items() for i, event in enumerate(CALL_EVENTS)}
)


class CallStateError(InputRefusedError):
    """An event or a reason was refused. ``code`` is the stable reason; the message is the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Transition:
    """What one event did: the state before and after, and whether the state changed."""

    source: str
    event: str
    target: str
    changed: bool


class CallMachine:
    """One call's state. ``apply`` is the only way to move it."""

    def __init__(self) -> None:
        self._state = CALL_STATES[0]
        self._end_reason: str | None = None
        self._established = False

    @property
    def state(self) -> str:
        return self._state

    @property
    def end_reason(self) -> str | None:
        return self._end_reason

    @property
    def call_established(self) -> bool:
        return self._established

    def apply(self, event: str, reason: str | None = None) -> Transition:
        if event not in CALL_EVENTS:
            raise CallStateError("unknown_event")
        if event == "end_requested":
            if reason not in REQUESTABLE_END_REASONS:
                raise CallStateError("end_reason_invalid")
        elif reason is not None:
            raise CallStateError("reason_not_allowed")
        source = self._state
        target = TRANSITIONS[(source, event)]
        if target is None:
            raise CallStateError("event_not_allowed_in_state")
        if (
            event == "end_requested"
            and source in ("preflight", "connecting")
            and reason not in PRECONNECT_END_REASONS
        ):
            raise CallStateError("end_reason_not_allowed_before_connect")
        if target == "ending" and source not in ("ending", "ended"):
            self._end_reason = "setup_failed" if event == "connect_failed" else reason
        if event == "connected" and source == "connecting":
            self._established = True
        self._state = target
        return Transition(source, event, target, target != source)


def effective_max_duration_ms(evaluator_max_duration_ms: int, max_call_seconds: int | None) -> int:
    """The call's maximum duration: the smaller of the Evaluator's and the call cap (rule 9)."""
    if (
        type(evaluator_max_duration_ms) is not int
        or not 1 <= evaluator_max_duration_ms <= 14_400_000
    ):
        raise CallStateError("evaluator_max_duration_invalid")
    if max_call_seconds is None:
        return evaluator_max_duration_ms
    if type(max_call_seconds) is not int or not 1 <= max_call_seconds <= 14_400:
        raise CallStateError("max_call_seconds_invalid")
    return min(evaluator_max_duration_ms, max_call_seconds * 1000)
