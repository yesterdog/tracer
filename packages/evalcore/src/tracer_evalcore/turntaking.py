# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Turn-taking: when the simulated caller speaks (ADR 0026 rule 10, ADR 0028 rule 9).

Pure: instants are integers of nanoseconds read by the caller from its own injected monotonic
clock; nothing here reads a clock. Agent activity is a received frame whose decoded 16-bit
samples reach ``activity_min_peak`` in absolute value (the optional fourth value; absent it is 1,
which is any non-zero sample): no energy window, no model. The three policy values of the owner
have no default.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore.errors import InputRefusedError

__all__ = [
    "ACTIVITY_MIN_PEAK_DEFAULT",
    "ACTIVITY_MIN_PEAK_MAX",
    "ACTIVITY_MIN_PEAK_MIN",
    "SPEAK_CAUSES",
    "TURN_DECISION_KINDS",
    "TurnDecision",
    "TurnTaker",
    "TurnTakingError",
    "TurnTakingPolicy",
    "activity_min_peak_of",
    "agent_frame_is_active",
]

TURN_DECISION_KINDS: Final = ("wait", "speak", "end")
SPEAK_CAUSES: Final = ("caller_first", "agent_silent", "barge_in")
_MAX: Final = 9007199254740991
ACTIVITY_MIN_PEAK_MIN: Final = 1
ACTIVITY_MIN_PEAK_MAX: Final = 32767
ACTIVITY_MIN_PEAK_DEFAULT: Final = 1
_NS_PER_MS: Final = 1_000_000


class TurnTakingError(InputRefusedError):
    """A policy value or a call was refused. ``code`` is the stable reason; the message is it."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _is_int(value: object) -> bool:
    return type(value) is int


def _check_peak(value: object) -> int:
    if type(value) is not int or not ACTIVITY_MIN_PEAK_MIN <= value <= ACTIVITY_MIN_PEAK_MAX:
        raise TurnTakingError("activity_min_peak_invalid")
    return value


@dataclass(frozen=True)
class TurnTakingPolicy:
    """The ``turn_taking`` object of ``tracer.sim_profile/1``; every value is required."""

    caller_speaks_first: bool
    wait_for_agent_silence_ms: int
    max_silence_ms: int
    activity_min_peak: int = ACTIVITY_MIN_PEAK_DEFAULT

    def __post_init__(self) -> None:
        if type(self.caller_speaks_first) is not bool:
            raise TurnTakingError("caller_speaks_first_invalid")
        for name in ("wait_for_agent_silence_ms", "max_silence_ms"):
            value = getattr(self, name)
            if not _is_int(value) or not 1 <= value <= _MAX:
                raise TurnTakingError(f"{name}_invalid")
        if self.max_silence_ms < self.wait_for_agent_silence_ms:
            raise TurnTakingError("max_silence_below_wait")
        _check_peak(self.activity_min_peak)

    @classmethod
    def from_profile(cls, section: Mapping[str, Any]) -> TurnTakingPolicy:
        """Read the profile's ``turn_taking`` object: its three required keys with no default and
        the optional ``activity_min_peak`` (absent: 1)."""
        keys = {"caller_speaks_first", "wait_for_agent_silence_ms", "max_silence_ms"}
        if not isinstance(section, Mapping):
            raise TurnTakingError("turn_taking_not_an_object")
        missing = sorted(keys - set(section))
        if missing:
            raise TurnTakingError(f"{missing[0]}_missing")
        if set(section) - keys - {"activity_min_peak"}:
            raise TurnTakingError("turn_taking_unknown_key")
        return cls(
            caller_speaks_first=section["caller_speaks_first"],
            wait_for_agent_silence_ms=section["wait_for_agent_silence_ms"],
            max_silence_ms=section["max_silence_ms"],
            activity_min_peak=activity_min_peak_of(section),
        )


def activity_min_peak_of(section: Mapping[str, Any]) -> int:
    """The ``activity_min_peak`` of a ``turn_taking`` object or of a timing document: its value,
    or 1 when the member is missing (a document of an earlier batch)."""
    if not isinstance(section, Mapping):
        raise TurnTakingError("turn_taking_not_an_object")
    if "activity_min_peak" not in section:
        return ACTIVITY_MIN_PEAK_DEFAULT
    return _check_peak(section["activity_min_peak"])


def agent_frame_is_active(pcm: bytes, *, min_peak: int = ACTIVITY_MIN_PEAK_DEFAULT) -> bool:
    """True iff the largest absolute value of the frame's 16-bit little-endian samples is at least
    ``min_peak`` (1 to 32767; the default 1 is any non-zero sample)."""
    if type(pcm) is not bytes or not pcm or len(pcm) % 2:
        raise TurnTakingError("frame_invalid")
    _check_peak(min_peak)
    if min_peak == 1:
        return any(pcm)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return bool(max(max(samples), -min(samples)) >= min_peak)


@dataclass(frozen=True)
class TurnDecision:
    """``wait``; ``speak`` at ``at_ns`` for a cause of ``SPEAK_CAUSES``; or ``end``."""

    kind: str
    at_ns: int | None
    cause: str | None


_WAIT: Final = TurnDecision("wait", None, None)


class TurnTaker:
    """Decides, from the agent frames of one wait, when the caller speaks or the call ends.

    A call that raises changes nothing: no instant is taken, no decision is latched.
    """

    def __init__(self, policy: TurnTakingPolicy) -> None:
        if not isinstance(policy, TurnTakingPolicy):
            raise TurnTakingError("policy_invalid")
        self._policy = policy
        self._waits = 0
        self._open = False
        self._w = 0
        self._barge: int | None = None
        self._onset: int | None = None
        self._last_active: int | None = None
        self._decision: TurnDecision | None = None
        self._pending = False
        self._clock = 0

    def _check_time(self, now_ns: int) -> None:
        if not _is_int(now_ns) or now_ns < 0 or now_ns < self._clock:
            raise TurnTakingError("time_invalid")

    def begin_wait(self, now_ns: int, *, barge_in_after_ms: int | None = None) -> None:
        self._check_time(now_ns)
        if self._open:
            raise TurnTakingError("wait_open")
        if barge_in_after_ms is not None and (
            not _is_int(barge_in_after_ms) or not 0 <= barge_in_after_ms <= _MAX
        ):
            raise TurnTakingError("barge_in_after_ms_invalid")
        first = self._waits == 0 and self._policy.caller_speaks_first
        if first and barge_in_after_ms is not None:
            raise TurnTakingError("barge_in_on_first_line")
        self._clock = now_ns
        self._waits += 1
        self._open = True
        self._w = now_ns
        self._barge = barge_in_after_ms
        self._onset = None
        self._last_active = None
        self._pending = False
        self._decision = TurnDecision("speak", now_ns, "caller_first") if first else None

    def _speak_instant(self) -> tuple[int, str] | None:
        if self._barge is not None:
            if self._onset is None:
                return None
            return self._onset + self._barge * _NS_PER_MS, "barge_in"
        if self._last_active is None:
            return None
        due = self._last_active + self._policy.wait_for_agent_silence_ms * _NS_PER_MS
        return due, "agent_silent"

    def _decide(self, now_ns: int) -> TurnDecision | None:
        """The decision that holds at ``now_ns`` (``None``: still waiting); stores nothing."""
        if not self._open:
            return None
        if self._decision is not None:
            return self._decision
        end = self._w + self._policy.max_silence_ms * _NS_PER_MS
        speak = self._speak_instant()
        if speak is not None and speak[0] <= now_ns and speak[0] <= end:
            return TurnDecision("speak", speak[0], speak[1])
        if end <= now_ns:
            return TurnDecision("end", end, "max_silence")
        return None

    def agent_frame(self, now_ns: int, pcm: bytes) -> bool:
        self._check_time(now_ns)
        active = agent_frame_is_active(pcm, min_peak=self._policy.activity_min_peak)
        self._clock = now_ns
        before = self._decide(now_ns)
        if before is not None:
            self._decision = before
        elif self._open and active:
            if self._onset is None:
                self._onset = now_ns
            self._last_active = now_ns
            self._decision = self._decide(now_ns)
        return active

    def poll(self, now_ns: int) -> TurnDecision:
        self._check_time(now_ns)
        if not self._open:
            raise TurnTakingError("no_wait")
        self._clock = now_ns
        decision = self._decide(now_ns)
        if decision is not None:
            self._decision = decision
            return decision
        return _WAIT

    def brain_started(self, now_ns: int) -> None:
        self._check_time(now_ns)
        if not self._open:
            raise TurnTakingError("no_wait")
        if self._pending:
            raise TurnTakingError("brain_already_pending")
        self._clock = now_ns
        self._pending = True

    def brain_finished(self, now_ns: int) -> None:
        self._check_time(now_ns)
        if not self._pending:
            raise TurnTakingError("brain_not_pending")
        self._clock = now_ns
        self._pending = False

    def may_send(self, now_ns: int) -> bool:
        """Whether a line may start now; a query that changes nothing."""
        self._check_time(now_ns)
        if not self._open:
            raise TurnTakingError("no_wait")
        decision = self._decide(now_ns)
        return decision is not None and decision.kind == "speak" and not self._pending

    def line_started(self, now_ns: int) -> None:
        if not self.may_send(now_ns):
            if self._pending:
                raise TurnTakingError("brain_pending")
            raise TurnTakingError("no_speak_decision")
        self._clock = now_ns
        self._decision = self._decide(now_ns)
        self._open = False
