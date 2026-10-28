# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Technical bounds of the simulator (ADR 0026 rule 22); not measured values."""

from __future__ import annotations

from typing import Final

__all__ = [
    "AGENT_AUDIO_CAP_FACTOR",
    "CLOSE_TIMEOUT_S",
    "CONNECT_TIMEOUT_S",
    "FRAME_NS",
    "MAX_AGENT_AUDIO_BYTES",
    "MAX_AUTH_TOKEN_CHARS",
    "MAX_INBOUND_BYTES_PER_SECOND",
    "MAX_INBOUND_MESSAGES_PER_SECOND",
    "MAX_INBOUND_MESSAGE_BYTES",
    "MAX_SDP_BYTES",
    "MAX_SIGNALLING_RESPONSE_BYTES",
    "MIN_AGENT_AUDIO_CAP_BYTES",
    "NS_PER_S",
    "WATCHDOG_GRACE_NS",
    "agent_audio_cap_bytes",
]

NS_PER_S: Final = 1_000_000_000
FRAME_NS: Final = 20_000_000
MAX_INBOUND_MESSAGE_BYTES: Final = 65_536
MAX_INBOUND_MESSAGES_PER_SECOND: Final = 1_000
WATCHDOG_GRACE_NS: Final = NS_PER_S
MAX_AUTH_TOKEN_CHARS: Final = 4096
MAX_SDP_BYTES: Final = 65_536
MAX_SIGNALLING_RESPONSE_BYTES: Final = 131_072
MAX_AGENT_AUDIO_BYTES: Final = 268_435_456
CONNECT_TIMEOUT_S: Final = 10.0
CLOSE_TIMEOUT_S: Final = 5.0

MAX_INBOUND_BYTES_PER_SECOND: Final = 1_048_576
AGENT_AUDIO_CAP_FACTOR: Final = 4
MIN_AGENT_AUDIO_CAP_BYTES: Final = 1_048_576


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def agent_audio_cap_bytes(
    *,
    bytes_per_second: int,
    max_duration_ms: int,
    ceiling: int = MAX_AGENT_AUDIO_BYTES,
) -> int:
    """The most agent audio one call may carry: the real-time wire rate of the dialect over the
    call length plus the watchdog grace, times ``AGENT_AUDIO_CAP_FACTOR``, never below
    ``MIN_AGENT_AUDIO_CAP_BYTES`` and never above ``ceiling`` or ``MAX_AGENT_AUDIO_BYTES``."""
    rate = _positive(bytes_per_second, "bytes_per_second")
    duration_ms = _positive(max_duration_ms, "max_duration_ms")
    limit = _positive(ceiling, "ceiling")
    span_ns = duration_ms * (NS_PER_S // 1000) + WATCHDOG_GRACE_NS
    derived = rate * span_ns * AGENT_AUDIO_CAP_FACTOR // NS_PER_S
    return min(limit, MAX_AGENT_AUDIO_BYTES, max(derived, MIN_AGENT_AUDIO_CAP_BYTES))
