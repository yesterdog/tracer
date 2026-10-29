# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The outcome of a simulated call: the document ``tracer.sim_outcome/1`` (ADR 0027 rules 1 and 12).

It records whether the call was established, its one end reason, the cap that ended it, the
SHA-256 of the authorisation reference (never its text), the brain mode and counts. The ``outcome``
member is always ``not_evaluated``: no judge runs (ADR 0027 rule 12). Machine keys, integers and
booleans only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final

from tracer_evalcore._schemalite import first_violation
from tracer_evalcore.errors import InputRefusedError

__all__ = [
    "SIM_OUTCOME_FORMAT",
    "SIM_OUTCOME_JSON_SCHEMA",
    "SIM_OUTCOME_METADATA_KEY",
    "SimOutcomeError",
    "authorisation_reference_sha256",
    "build_sim_outcome",
    "validate_sim_outcome",
]

SIM_OUTCOME_FORMAT: Final = "tracer.sim_outcome/1"
SIM_OUTCOME_METADATA_KEY: Final = "tracer.sim_outcome"
_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.sim_outcome/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "dialect",
    "outcome",
    "call_established",
    "end_reason",
    "cap_reached",
    "started_at_clock",
    "connected_ms",
    "authorisation_reference_sha256",
    "caller_text_origin",
    "counts"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.sim_outcome/1"
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
    "outcome": {
      "type": "string",
      "enum": [
        "not_evaluated"
      ]
    },
    "call_established": {
      "type": "boolean"
    },
    "end_reason": {
      "type": "string",
      "enum": [
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
        "voice_failed"
      ]
    },
    "cap_reached": {
      "type": [
        "string",
        "null"
      ],
      "enum": [
        "call_seconds",
        "tts_chars",
        "llm_tokens",
        "stt_seconds",
        null
      ]
    },
    "started_at_clock": {
      "type": "string",
      "enum": [
        "simulator_wall_clock"
      ]
    },
    "connected_ms": {
      "type": [
        "integer",
        "null"
      ],
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "authorisation_reference_sha256": {
      "type": "string",
      "pattern": "^[0-9a-f]{64}$"
    },
    "caller_text_origin": {
      "type": "string",
      "enum": [
        "scripted",
        "llm",
        "replay"
      ]
    },
    "counts": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "caller_turns_started",
        "caller_turns_completed",
        "agent_segments",
        "barge_ins_fired",
        "brain_requests",
        "brain_invalid_outputs",
        "brain_regenerations",
        "tts_requests",
        "tts_cache_hits",
        "llm_requests",
        "stt_requests",
        "frames_sent",
        "frames_received",
        "inbound_messages_ignored",
        "signals_received"
      ],
      "properties": {
        "caller_turns_started": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "caller_turns_completed": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "agent_segments": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "barge_ins_fired": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "brain_requests": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "brain_invalid_outputs": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "brain_regenerations": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "tts_requests": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "tts_cache_hits": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "llm_requests": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "stt_requests": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "frames_sent": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "frames_received": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "inbound_messages_ignored": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "signals_received": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        }
      }
    }
  }
}"""
SIM_OUTCOME_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_SCHEMA_TEXT)


class SimOutcomeError(InputRefusedError):
    """An outcome document was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def authorisation_reference_sha256(reference: str) -> str:
    """Lower-case hexadecimal SHA-256 of the UTF-8 bytes of the reference (ADR 0026 rule 21)."""
    if type(reference) is not str:
        raise TypeError("reference: not text")
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def validate_sim_outcome(document: Mapping[str, Any], *, brain_mode: str | None = None) -> None:
    """The schema of the ADR, then its cross-field rules; ``SimOutcomeError`` on the first fault.

    ``brain_mode`` (``scripted``, ``llm`` or ``replay``), when given, must equal the document's
    ``caller_text_origin`` (the mode of the snapshot's sim profile).
    """
    found = first_violation(SIM_OUTCOME_JSON_SCHEMA, document)
    if found is not None:
        raise SimOutcomeError(f"schema:{found}")
    end = document["end_reason"]
    established = document["call_established"]
    if end == "setup_failed" and established:
        raise SimOutcomeError("setup_failed_but_established")
    if not established and (
        end not in ("setup_failed", "kill_switch") or document["connected_ms"] is not None
    ):
        raise SimOutcomeError("not_established_inconsistent")
    if established and document["connected_ms"] is None:
        raise SimOutcomeError("established_without_connected_ms")
    if (document["cap_reached"] is not None) != (end == "spend_cap"):
        raise SimOutcomeError("cap_reached_inconsistent")
    counts = document["counts"]
    if counts["caller_turns_completed"] > counts["caller_turns_started"]:
        raise SimOutcomeError("turns_completed_above_started")
    if counts["barge_ins_fired"] > counts["caller_turns_started"]:
        raise SimOutcomeError("barge_ins_above_turns")
    if counts["brain_regenerations"] > counts["brain_invalid_outputs"]:
        raise SimOutcomeError("regenerations_above_invalid")
    if end == "brain_failed" and document["caller_text_origin"] != "llm":
        raise SimOutcomeError("brain_failed_without_llm_caller")
    if end == "voice_failed" and document["caller_text_origin"] != "llm":
        raise SimOutcomeError("voice_failed_without_llm_caller")
    if document["caller_text_origin"] == "scripted" and (
        counts["brain_requests"] or counts["llm_requests"]
    ):
        raise SimOutcomeError("scripted_with_brain_requests")
    if brain_mode is not None and document["caller_text_origin"] != brain_mode:
        raise SimOutcomeError("caller_text_origin_mismatch")


def build_sim_outcome(
    *,
    dialect: str,
    call_established: bool,
    end_reason: str,
    cap_reached: str | None,
    connected_ms: int | None,
    authorisation_reference_sha256: str,
    caller_text_origin: str,
    counts: Mapping[str, int],
    brain_mode: str | None = None,
) -> dict[str, Any]:
    """The validated document."""
    document: dict[str, Any] = {
        "format": SIM_OUTCOME_FORMAT,
        "dialect": dialect,
        "outcome": "not_evaluated",
        "call_established": call_established,
        "end_reason": end_reason,
        "cap_reached": cap_reached,
        "started_at_clock": "simulator_wall_clock",
        "connected_ms": connected_ms,
        "authorisation_reference_sha256": authorisation_reference_sha256,
        "caller_text_origin": caller_text_origin,
        "counts": dict(counts),
    }
    validate_sim_outcome(document, brain_mode=brain_mode)
    return document
