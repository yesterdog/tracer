# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Names shared by the simulated caller (ADR 0026 rules 5 and 9, ADR 0027, ADR 0028 rule 7).

Closed lists only: the wire dialects, the call states, the end reasons, the caller modes, the
caps and the end sentinel. Pure data; no behaviour.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CALLER_MODES",
    "CALL_STATES",
    "CAP_NAMES",
    "DIALECTS",
    "END_REASONS",
    "END_SENTINEL",
    "FRAME_MS",
    "MAX_LINE_CHARS",
]

DIALECTS: Final = ("pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1")
CALL_STATES: Final = (
    "preflight",
    "connecting",
    "connected",
    "caller_speaking",
    "awaiting_agent",
    "agent_speaking",
    "ending",
    "ended",
)
END_REASONS: Final = (
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
CALLER_MODES: Final = ("scripted", "llm", "replay")
CAP_NAMES: Final = ("call_seconds", "tts_chars", "llm_tokens", "stt_seconds")
END_SENTINEL: Final = "END_CALL"
FRAME_MS: Final = 20
MAX_LINE_CHARS: Final = 2500
