# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Technical bounds of the simulator (ADR 0026 rule 22); not measured values."""

from __future__ import annotations

from typing import Final

__all__ = [
    "FRAME_NS",
    "MAX_AUTH_TOKEN_CHARS",
    "MAX_INBOUND_MESSAGES_PER_SECOND",
    "MAX_INBOUND_MESSAGE_BYTES",
    "MAX_SDP_BYTES",
    "MAX_SIGNALLING_RESPONSE_BYTES",
    "NS_PER_S",
    "WATCHDOG_GRACE_NS",
]

NS_PER_S: Final = 1_000_000_000
FRAME_NS: Final = 20_000_000
MAX_INBOUND_MESSAGE_BYTES: Final = 65_536
MAX_INBOUND_MESSAGES_PER_SECOND: Final = 1_000
WATCHDOG_GRACE_NS: Final = NS_PER_S
MAX_AUTH_TOKEN_CHARS: Final = 4096
MAX_SDP_BYTES: Final = 65_536
MAX_SIGNALLING_RESPONSE_BYTES: Final = 131_072
