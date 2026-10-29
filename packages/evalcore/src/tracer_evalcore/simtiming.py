# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Timing extraction: the ``tracer.timing/1`` document (ADR 0027 rules 1, 10 and 11).

Pure: every instant is an integer of nanoseconds of the caller's injected monotonic clock. Every
value is an integer number of milliseconds (floored) between two such instants, or a count; a
value with a missing edge is ``None`` and never 0. The structural check is written by hand over
the schema this module ships (``jsonschema`` is not a runtime dependency).
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.recorder import LogFrame, RecorderFormat, _check, decode_to_pcm16
from tracer_evalcore.simtypes import DIALECTS, FRAME_MS
from tracer_evalcore.turntaking import (
    ACTIVITY_MIN_PEAK_DEFAULT,
    TurnTakingError,
    activity_min_peak_of,
    agent_frame_is_active,
)

__all__ = [
    "TIMING_EDGE_MISSING",
    "TIMING_FORMAT",
    "TIMING_JSON_SCHEMA",
    "TIMING_METADATA_KEY",
    "TIMING_METRICS",
    "CallerTurn",
    "SelfTimes",
    "TimingDocumentError",
    "TimingValue",
    "activity_wording",
    "extract_timing",
    "nearest_rank_median",
    "timing_value",
    "validate_timing_document",
]

TIMING_FORMAT: Final = "tracer.timing/1"
TIMING_METADATA_KEY: Final = "tracer.timing"
TIMING_EDGE_MISSING: Final = "timing_edge_missing"
_MAX: Final = 9007199254740991
_MS: Final = 1_000_000
_SCHEMA_TEXT: Final = r"""{
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "title": "tracer.timing/1",
 "type": "object",
 "additionalProperties": false,
 "required": [
  "format",
  "dialect",
  "clock",
  "quantum_ms",
  "caller_frame_ms",
  "setup_ms",
  "first_agent_audio_ms",
  "overlap_ms",
  "answered_caller_turns",
  "gap_median_ms",
  "gap_n",
  "barge_in_stop_median_ms",
  "barge_in_n",
  "turns"
 ],
 "properties": {
  "format": {
   "type": "string",
   "enum": [
    "tracer.timing/1"
   ]
  },
  "dialect": {
   "type": "string",
   "enum": [
    "pipecat-protobuf/1",
    "twilio-media-stream/1",
    "smallwebrtc/1"
   ]
  },
  "clock": {
   "type": "string",
   "enum": [
    "simulator_monotonic"
   ]
  },
  "quantum_ms": {
   "type": "integer",
   "minimum": 1,
   "maximum": 9007199254740991
  },
  "caller_frame_ms": {
   "type": "integer",
   "enum": [
    20
   ]
  },
  "activity_min_peak": {
   "type": "integer",
   "minimum": 1,
   "maximum": 32767
  },
  "setup_ms": {
   "type": [
    "integer",
    "null"
   ],
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "first_agent_audio_ms": {
   "type": [
    "integer",
    "null"
   ],
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "overlap_ms": {
   "type": [
    "integer",
    "null"
   ],
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "answered_caller_turns": {
   "type": "integer",
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "gap_median_ms": {
   "type": [
    "integer",
    "null"
   ],
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "gap_n": {
   "type": "integer",
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "barge_in_stop_median_ms": {
   "type": [
    "integer",
    "null"
   ],
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "barge_in_n": {
   "type": "integer",
   "minimum": 0,
   "maximum": 9007199254740991
  },
  "turns": {
   "type": "array",
   "items": {
    "type": "object",
    "additionalProperties": false,
    "required": [
     "turn_seq",
     "gap_ms",
     "overlap_ms",
     "barge_in_after_ms",
     "barge_in_stop_ms",
     "self"
    ],
    "properties": {
     "turn_seq": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
     },
     "gap_ms": {
      "type": [
       "integer",
       "null"
      ],
      "minimum": 0,
      "maximum": 9007199254740991
     },
     "overlap_ms": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
     },
     "barge_in_after_ms": {
      "type": [
       "integer",
       "null"
      ],
      "minimum": 0,
      "maximum": 9007199254740991
     },
     "barge_in_stop_ms": {
      "type": [
       "integer",
       "null"
      ],
      "minimum": 0,
      "maximum": 9007199254740991
     },
     "self": {
      "type": "object",
      "additionalProperties": false,
      "required": [
       "reply_ms",
       "wait_ms",
       "stt_ms",
       "brain_ms",
       "tts_ms"
      ],
      "properties": {
       "reply_ms": {
        "type": [
         "integer",
         "null"
        ],
        "minimum": 0,
        "maximum": 9007199254740991
       },
       "wait_ms": {
        "type": [
         "integer",
         "null"
        ],
        "minimum": 0,
        "maximum": 9007199254740991
       },
       "stt_ms": {
        "type": [
         "integer",
         "null"
        ],
        "minimum": 0,
        "maximum": 9007199254740991
       },
       "brain_ms": {
        "type": [
         "integer",
         "null"
        ],
        "minimum": 0,
        "maximum": 9007199254740991
       },
       "tts_ms": {
        "type": [
         "integer",
         "null"
        ],
        "minimum": 0,
        "maximum": 9007199254740991
       }
      }
     }
    }
   }
  }
 }
}"""
TIMING_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_SCHEMA_TEXT)
#: metric key -> (document field, name of the missing edge, count field or None)
TIMING_METRICS: Final = {
    "sim_first_agent_audio_ms": ("first_agent_audio_ms", "first active agent frame", None),
    "sim_agent_response_gap_median_ms": (
        "gap_median_ms",
        "active agent frame after a caller turn",
        "gap_n",
    ),
    "sim_overlap_ms": ("overlap_ms", "established call", None),
    "sim_barge_in_stop_ms": (
        "barge_in_stop_median_ms",
        "end of the interrupted agent activity",
        "barge_in_n",
    ),
    "sim_answered_caller_turns": ("answered_caller_turns", "", None),
}
_SELF_KEYS: Final = ("reply_ms", "wait_ms", "stt_ms", "brain_ms", "tts_ms")


class TimingDocumentError(InputRefusedError):
    """A timing input or document was refused. ``code`` is the stable reason; the message is it."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _int(value: object) -> bool:
    return type(value) is int


def _opt_ms(value: object) -> bool:
    return value is None or (type(value) is int and 0 <= value <= _MAX)


@dataclass(frozen=True)
class SelfTimes:
    """The simulator's own times before one caller turn, each in ms or ``None`` (not run)."""

    reply_ms: int | None
    wait_ms: int | None
    stt_ms: int | None
    brain_ms: int | None
    tts_ms: int | None

    def __post_init__(self) -> None:
        for key in _SELF_KEYS:
            if not _opt_ms(getattr(self, key)):
                raise TimingDocumentError(f"self_{key}_invalid")


@dataclass(frozen=True)
class CallerTurn:
    """One caller line as sent: send instants of its first and last frame."""

    turn_seq: int
    first_frame_ns: int
    last_frame_ns: int
    barge_in_after_ms: int | None
    self_times: SelfTimes

    def __post_init__(self) -> None:
        if not _int(self.turn_seq) or self.turn_seq < 0:
            raise TimingDocumentError("turn_seq_invalid")
        for value in (self.first_frame_ns, self.last_frame_ns):
            if not _int(value) or value < 0:
                raise TimingDocumentError("turn_instant_invalid")
        if self.last_frame_ns < self.first_frame_ns:
            raise TimingDocumentError("turn_instants_reversed")
        if not _opt_ms(self.barge_in_after_ms):
            raise TimingDocumentError("barge_in_after_ms_invalid")
        if not isinstance(self.self_times, SelfTimes):
            raise TimingDocumentError("self_times_invalid")


def nearest_rank_median(values: Sequence[int]) -> int | None:
    """The value at rank ``ceil(n / 2)`` of the sorted values; ``None`` for no value."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) + 1) // 2 - 1]


def _duration_ns(frame: LogFrame) -> int:
    samples = len(decode_to_pcm16(frame.codec, frame.payload)) // 2
    return samples * 1_000_000_000 // frame.sample_rate_hz


def extract_timing(
    *,
    dialect: str,
    fmt: RecorderFormat,
    wait_for_agent_silence_ms: int,
    connect_started_ns: int | None,
    connect_ended_ns: int | None,
    established: bool,
    observed_until_ns: int,
    caller_turns: Sequence[CallerTurn],
    agent_frames: Sequence[LogFrame],
    activity_min_peak: int | None = None,
) -> dict[str, Any]:
    """The timing document. ``activity_min_peak`` is the optional threshold of the sim profile: a
    frame is active iff its decoded samples reach it in absolute value. ``None`` means the profile
    did not give it (the rule is then 1, any non-zero sample, and the document has no member)."""
    if activity_min_peak is not None:
        activity_wording(activity_min_peak)
    peak = ACTIVITY_MIN_PEAK_DEFAULT if activity_min_peak is None else activity_min_peak
    if dialect not in DIALECTS:
        raise TimingDocumentError("dialect_invalid")
    if not isinstance(fmt, RecorderFormat):
        raise TimingDocumentError("format_invalid")
    if not _int(wait_for_agent_silence_ms) or not 1 <= wait_for_agent_silence_ms <= _MAX:
        raise TimingDocumentError("wait_for_agent_silence_ms_invalid")
    for value in (connect_started_ns, connect_ended_ns):
        if value is not None and (not _int(value) or value < 0):
            raise TimingDocumentError("connect_instant_invalid")
    if type(established) is not bool:
        raise TimingDocumentError("established_invalid")
    if connect_ended_ns is not None and connect_started_ns is None:
        raise TimingDocumentError("connect_started_missing")
    if (
        connect_started_ns is not None
        and connect_ended_ns is not None
        and connect_ended_ns < connect_started_ns
    ):
        raise TimingDocumentError("connect_instants_reversed")
    if established and connect_ended_ns is None:
        raise TimingDocumentError("established_without_instant")
    if not established and (caller_turns or agent_frames):
        raise TimingDocumentError("frames_without_call")
    if not _int(observed_until_ns) or observed_until_ns < 0:
        raise TimingDocumentError("observed_until_invalid")
    if not all(isinstance(t, CallerTurn) for t in caller_turns):
        raise TimingDocumentError("turn_invalid")
    if not all(isinstance(f, LogFrame) for f in agent_frames):
        raise TimingDocumentError("frame_invalid")
    setup_ms = (
        (connect_ended_ns - connect_started_ns) // _MS
        if connect_started_ns is not None and connect_ended_ns is not None
        else None
    )
    frame_ns = FRAME_MS * _MS
    if not established or connect_ended_ns is None:
        return _finish(dialect, FRAME_MS, setup_ms, None, None, [], activity_min_peak)
    origin = connect_ended_ns
    _check(fmt, agent_frames, origin, "agent")
    previous_end = origin
    for turn in caller_turns:
        if turn.first_frame_ns < origin or turn.first_frame_ns < previous_end:
            raise TimingDocumentError("turns_out_of_order")
        previous_end = turn.last_frame_ns + frame_ns
    seqs = [t.turn_seq for t in caller_turns]
    if seqs != sorted(set(seqs)):
        raise TimingDocumentError("turn_seq_not_increasing")
    longest = max((_duration_ns(f) for f in agent_frames), default=0)
    quantum = max(FRAME_MS, -(-longest // _MS))
    active = [
        (f.t_ns, _duration_ns(f))
        for f in agent_frames
        if agent_frame_is_active(decode_to_pcm16(f.codec, f.payload), min_peak=peak)
    ]
    last_seen = max(
        [observed_until_ns, *(t.last_frame_ns + frame_ns for t in caller_turns),
         *(f.t_ns for f in agent_frames)]
    )  # fmt: skip
    if observed_until_ns < last_seen:
        raise TimingDocumentError("observed_until_before_events")
    starts = [a for a, _ in active]
    prefix = [0]
    for _, duration in active:
        prefix.append(prefix[-1] + duration)
    silence_ns = wait_for_agent_silence_ms * _MS
    run_last = list(range(len(starts)))
    for i in range(len(starts) - 2, -1, -1):
        if starts[i + 1] - starts[i] < silence_ns:
            run_last[i] = run_last[i + 1]
    first_audio = (starts[0] - origin) // _MS if starts else None
    turns: list[dict[str, Any]] = []
    for turn in caller_turns:
        edge = turn.last_frame_ns + frame_ns
        after = bisect_left(starts, edge)
        gap = (starts[after] - edge) // _MS if after < len(starts) else None
        inside = prefix[after] - prefix[bisect_left(starts, turn.first_frame_ns)]
        stop = None
        if turn.barge_in_after_ms is not None:
            stop = _barge_stop(
                starts, active, run_last, turn.first_frame_ns, observed_until_ns, silence_ns
            )
        turns.append(
            {
                "turn_seq": turn.turn_seq,
                "gap_ms": gap,
                "overlap_ms": inside // _MS,
                "barge_in_after_ms": turn.barge_in_after_ms,
                "barge_in_stop_ms": stop,
                "self": {k: getattr(turn.self_times, k) for k in _SELF_KEYS},
            }
        )
    return _finish(
        dialect,
        quantum,
        setup_ms,
        first_audio,
        sum(t["overlap_ms"] for t in turns),
        turns,
        activity_min_peak,
    )


def _barge_stop(
    starts: list[int],
    active: list[tuple[int, int]],
    run_last: list[int],
    t0: int,
    observed_until: int,
    silence_ns: int,
) -> int | None:
    """The end of the agent activity a line interrupted, in ms, or ``None`` (no such edge)."""
    i = bisect_right(starts, t0) - 1
    if i < 0 or t0 - starts[i] >= silence_ns:
        return None
    last = run_last[i]
    if observed_until - starts[last] < silence_ns:
        return None
    stop = starts[last] + active[last][1] - t0
    return stop // _MS if stop > 0 else None


def _finish(
    dialect: str,
    quantum: int,
    setup_ms: int | None,
    first_audio: int | None,
    overlap: int | None,
    turns: list[dict[str, Any]],
    activity_min_peak: int | None = None,
) -> dict[str, Any]:
    gaps = [t["gap_ms"] for t in turns if t["gap_ms"] is not None]
    stops = [t["barge_in_stop_ms"] for t in turns if t["barge_in_stop_ms"] is not None]
    document = {
        "format": TIMING_FORMAT,
        "dialect": dialect,
        "clock": "simulator_monotonic",
        "quantum_ms": quantum,
        "caller_frame_ms": FRAME_MS,
        "setup_ms": setup_ms,
        "first_agent_audio_ms": first_audio,
        "overlap_ms": overlap,
        "answered_caller_turns": len(gaps),
        "gap_median_ms": nearest_rank_median(gaps),
        "gap_n": len(gaps),
        "barge_in_stop_median_ms": nearest_rank_median(stops),
        "barge_in_n": len(stops),
        "turns": turns,
    }
    if activity_min_peak is not None:
        document["activity_min_peak"] = activity_min_peak
    validate_timing_document(document)
    return document


def activity_wording(min_peak: int) -> str:
    """The closed wording of the activity rule in a label: ``any non-zero sample`` for 1, else
    ``peak >= N``. It states the rule applied, never what the audio is."""
    try:
        activity_min_peak_of({"activity_min_peak": min_peak})
    except TurnTakingError:
        raise TimingDocumentError("activity_min_peak_invalid") from None
    return "any non-zero sample" if min_peak == 1 else f"peak >= {min_peak}"


@dataclass(frozen=True)
class TimingValue:
    """A metric's value from a document: the integer, or the error code with its message."""

    value: int | None
    error_code: str | None
    message: str | None


def timing_value(document: Mapping[str, Any], metric: str) -> TimingValue:
    """The value of ``metric`` or ``timing_edge_missing`` with a message of counts; never 0."""
    if metric not in TIMING_METRICS:
        raise TimingDocumentError("metric_unknown")
    field, edge, count = TIMING_METRICS[metric]
    value = document[field]
    if value is not None:
        return TimingValue(value, None, None)
    detail = f"caller turns {len(document['turns'])}"
    if count is not None:
        detail += f", {count} {document[count]}"
    message = f"{TIMING_EDGE_MISSING}: {field} has no value; missing edge: {edge}; {detail}"
    return TimingValue(None, TIMING_EDGE_MISSING, message)


# ------------------------------------------------------------------------ the validator

_PROPS: Final = TIMING_JSON_SCHEMA["properties"]
_TOP: Final = frozenset(TIMING_JSON_SCHEMA["required"])
_TURN: Final = frozenset(_PROPS["turns"]["items"]["required"])
_SELF: Final = frozenset(_PROPS["turns"]["items"]["properties"]["self"]["required"])


def _bad(code: str) -> TimingDocumentError:
    return TimingDocumentError(code)


def _ms_or_none(value: object, code: str) -> None:
    if not _opt_ms(value):
        raise _bad(code)


def validate_timing_document(document: Mapping[str, Any]) -> None:
    if not isinstance(document, Mapping) or set(document) - {"activity_min_peak"} != _TOP:
        raise _bad("document_keys")
    if "activity_min_peak" in document:
        activity_wording(document["activity_min_peak"])
    if document["format"] != TIMING_FORMAT:
        raise _bad("format_invalid")
    if document["dialect"] not in DIALECTS:
        raise _bad("dialect_invalid")
    if document["clock"] != "simulator_monotonic":
        raise _bad("clock_invalid")
    if document["caller_frame_ms"] != FRAME_MS or type(document["caller_frame_ms"]) is not int:
        raise _bad("caller_frame_ms_invalid")
    quantum = document["quantum_ms"]
    if not _int(quantum) or not FRAME_MS <= quantum <= _MAX:
        raise _bad("quantum_ms_invalid")
    for key in ("setup_ms", "first_agent_audio_ms", "overlap_ms", "gap_median_ms",
                "barge_in_stop_median_ms"):  # fmt: skip
        _ms_or_none(document[key], f"{key}_invalid")
    for key in ("answered_caller_turns", "gap_n", "barge_in_n"):
        if not _int(document[key]) or not 0 <= document[key] <= _MAX:
            raise _bad(f"{key}_invalid")
    turns = document["turns"]
    if not isinstance(turns, list):
        raise _bad("turns_invalid")
    seqs: list[int] = []
    for turn in turns:
        if not isinstance(turn, Mapping) or set(turn) != _TURN:
            raise _bad("turn_keys")
        if not _int(turn["turn_seq"]) or not 0 <= turn["turn_seq"] <= _MAX:
            raise _bad("turn_seq_invalid")
        seqs.append(turn["turn_seq"])
        for key in ("gap_ms", "barge_in_after_ms", "barge_in_stop_ms"):
            _ms_or_none(turn[key], f"turn_{key}_invalid")
        if not _opt_ms(turn["overlap_ms"]) or turn["overlap_ms"] is None:
            raise _bad("turn_overlap_ms_invalid")
        own = turn["self"]
        if not isinstance(own, Mapping) or set(own) != _SELF:
            raise _bad("self_keys")
        for key in _SELF_KEYS:
            _ms_or_none(own[key], f"self_{key}_invalid")
        if turn["barge_in_after_ms"] is None and turn["barge_in_stop_ms"] is not None:
            raise _bad("barge_in_stop_without_barge_in")
    if seqs != sorted(set(seqs)):
        raise _bad("turns_not_sorted_unique")
    gaps = [t["gap_ms"] for t in turns if t["gap_ms"] is not None]
    stops = [t["barge_in_stop_ms"] for t in turns if t["barge_in_stop_ms"] is not None]
    if document["answered_caller_turns"] != len(gaps) or document["gap_n"] != len(gaps):
        raise _bad("answered_count_mismatch")
    if document["gap_median_ms"] != nearest_rank_median(gaps):
        raise _bad("gap_median_mismatch")
    if document["barge_in_n"] != len(stops):
        raise _bad("barge_in_count_mismatch")
    if document["barge_in_stop_median_ms"] != nearest_rank_median(stops):
        raise _bad("barge_in_median_mismatch")
    if document["overlap_ms"] is None:
        if turns:
            raise _bad("overlap_missing_with_turns")
    elif document["overlap_ms"] != sum(t["overlap_ms"] for t in turns):
        raise _bad("overlap_sum_mismatch")
