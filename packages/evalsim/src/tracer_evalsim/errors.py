# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Errors of the simulator package: facts only, never a payload, a header or a secret."""

from __future__ import annotations

import re
from typing import Any

from tracer_evalcore.errors import EvalcoreError

__all__ = [
    "REFUSAL_CODES",
    "SETUP_CODES",
    "TRANSPORT_CODES",
    "EvalsimError",
    "SetupFailedError",
    "TargetRefusedError",
    "TransportError",
    "TransportFactError",
    "WatchdogError",
]

REFUSAL_CODES = (
    "auth_env_name_invalid",
    "auth_token_invalid",
    "auth_token_missing",
    "bot_credentials_field_missing",
    "bot_holds_telephony_credentials",
    "dialect_unknown",
    "ice_server_invalid",
    "ice_server_required",
    "insecure_target",
    "labels_inconsistent",
    "origin_invalid",
    "target_invalid",
    "webrtc_extra_missing",
)
SETUP_CODES = (
    "answer_invalid",
    "connect_timeout",
    "connection_failed",
    "connection_refused",
    "handshake_failed",
    "handshake_rejected",
    "redirect_refused",
    "tls_failed",
)
TRANSPORT_CODES = (
    "audio_format_mismatch",
    "audio_frame_invalid",
    "connection_closed",
    "connection_lost",
    "inbound_rate_exceeded",
    "message_too_large",
    "send_failed",
)
#: fixed text per code (never derived from an input)
_HINTS = {"webrtc_extra_missing": "install the optional extra: tracer-evalsim[webrtc]"}
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class EvalsimError(EvalcoreError):
    """Base of every error raised by tracer-evalsim."""


class TransportFactError(EvalsimError):
    """An error that holds machine facts only (ADR 0026 rule 4 pattern)."""

    END_REASON: str | None = None

    def __init__(
        self, *, code: str, http_status: int | None = None, close_code: int | None = None
    ) -> None:
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise ValueError("code is not a machine code")
        if http_status is not None and (
            isinstance(http_status, bool)
            or not isinstance(http_status, int)
            or not 100 <= http_status <= 599
        ):
            raise ValueError("http_status is not an HTTP status")
        if close_code is not None and (
            isinstance(close_code, bool)
            or not isinstance(close_code, int)
            or not 0 <= close_code <= 65535
        ):
            raise ValueError("close_code is not a close code")
        parts = [f"code {code}"]
        if http_status is not None:
            parts.append(f"HTTP {http_status}")
        if close_code is not None:
            parts.append(f"close {close_code}")
        text = f"{type(self).__name__}: " + ", ".join(parts)
        if code in _HINTS:
            text += f" ({_HINTS[code]})"
        super().__init__(text)
        self.code = code
        self.http_status = http_status
        self.close_code = close_code

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild, (type(self), self.code, self.http_status, self.close_code))


def _rebuild(cls: type[TransportFactError], code: str, http: int | None, close: int | None) -> Any:
    return cls(code=code, http_status=http, close_code=close)


class TargetRefusedError(TransportFactError, ValueError):
    """A target or a connection label was refused before any socket was opened (exit 4)."""


class SetupFailedError(TransportFactError):
    """The call could not be established: the end reason ``setup_failed`` (ADR 0026 rule 9)."""

    END_REASON = "setup_failed"


class TransportError(TransportFactError):
    """The transport failed during the call: the end reason ``transport_error``."""

    END_REASON = "transport_error"


class WatchdogError(EvalsimError):
    """The watchdog's hang-up failed."""
