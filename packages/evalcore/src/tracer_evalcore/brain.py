# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The caller brain boundary (ADR 0026 rule 4, ADR 0028 rule 1): scripted, LLM or replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from tracer_evalcore.simtypes import CALLER_MODES, MAX_LINE_CHARS

__all__ = [
    "BRAIN_END_REASONS",
    "BrainDecision",
    "CallerBrain",
    "require_caller_brain",
]

BRAIN_END_REASONS: Final = (
    "script_complete",
    "caller_end_call",
    "max_turns",
    "brain_invalid_output",
    "spend_cap",
)


@dataclass(frozen=True)
class BrainDecision:
    """The next caller line, or the end of the call with one of the brain's end reasons."""

    line: str | None
    end_reason: str | None
    invalid_outputs: int
    regenerations: int

    def __post_init__(self) -> None:
        if (self.line is None) == (self.end_reason is None):
            raise ValueError("a decision holds a line or an end reason, exactly one")
        if self.line is not None and (
            type(self.line) is not str or not 1 <= len(self.line) <= MAX_LINE_CHARS
        ):
            raise ValueError("line: empty or longer than the limit")
        if self.end_reason is not None and self.end_reason not in BRAIN_END_REASONS:
            raise ValueError("end_reason: not one of the brain's")
        for name, value in (("invalid_outputs", self.invalid_outputs),
                            ("regenerations", self.regenerations)):  # fmt: skip
            if type(value) is not int or value < 0:
                raise ValueError(f"{name}: not a non-negative integer")
        if self.regenerations > self.invalid_outputs:
            raise ValueError("regenerations: more than invalid outputs")


@runtime_checkable
class CallerBrain(Protocol):
    """What decides the next caller line; ``mode`` is one of ``CALLER_MODES``."""

    mode: str

    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision: ...


def require_caller_brain(candidate: object) -> CallerBrain:
    """Return the candidate when it is a brain of a known mode, else refuse it."""
    if not isinstance(candidate, CallerBrain):
        raise TypeError("not a caller brain")
    if candidate.mode not in CALLER_MODES:
        raise ValueError("mode: not one of the caller modes")
    return candidate
