# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The WebSocket client of the two WebSocket dialects (ADR 0026 rules 5, 6, 16, 22)."""

from __future__ import annotations

import logging
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
from websockets.typing import Origin

from tracer_evalsim.dialects.base import CloseInfo, Dialect
from tracer_evalsim.errors import SetupFailedError, TargetRefusedError, TransportError
from tracer_evalsim.limits import MAX_AUTH_TOKEN_CHARS, MAX_INBOUND_MESSAGE_BYTES

__all__ = [
    "BearerToken",
    "ConnectionLabels",
    "ParsedTarget",
    "WebSocketConnection",
    "WebSocketTarget",
    "check_connection_labels",
    "open_websocket",
    "parse_target_url",
    "prepare_connection",
    "read_bearer_token",
]

_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_ORIGIN = re.compile(r"(?:https?|wss?)://[a-z0-9.-]+(?::[0-9]{1,5})?")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

_SILENT = logging.getLogger("tracer_evalsim.ws.silent")
_SILENT.propagate = False
_SILENT.addHandler(logging.NullHandler())
_SILENT.setLevel(logging.CRITICAL + 1)


@dataclass(frozen=True)
class ConnectionLabels:
    auth_token_env: str | None
    origin: str | None
    bot_holds_telephony_credentials: bool | None

    def __post_init__(self) -> None:
        if self.auth_token_env is not None and not (
            isinstance(self.auth_token_env, str) and _ENV_NAME.fullmatch(self.auth_token_env)
        ):
            raise TargetRefusedError(code="auth_env_name_invalid")
        if self.origin is not None and not (
            isinstance(self.origin, str) and _ORIGIN.fullmatch(self.origin)
        ):
            raise TargetRefusedError(code="origin_invalid")
        if self.bot_holds_telephony_credentials is not None and not isinstance(
            self.bot_holds_telephony_credentials, bool
        ):
            raise TargetRefusedError(code="labels_inconsistent")

    def as_profile_connection(self) -> dict[str, Any]:
        return {
            "auth_token_env": self.auth_token_env,
            "origin": self.origin,
            "bot_holds_telephony_credentials": self.bot_holds_telephony_credentials,
        }


@dataclass(frozen=True)
class WebSocketTarget:
    url: str
    labels: ConnectionLabels


class BearerToken:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a bearer token is immutable")

    def __repr__(self) -> str:
        return "BearerToken(<redacted>)"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return "BearerToken(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("a bearer token is never copied or pickled")

    def __copy__(self) -> NoReturn:
        raise TypeError("a bearer token is never copied or pickled")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("a bearer token is never copied or pickled")

    def authorization_header_value(self) -> str:
        return "Bearer " + str(object.__getattribute__(self, "_value"))


def read_bearer_token(
    labels: ConnectionLabels, *, environ: Mapping[str, str]
) -> BearerToken | None:
    name = labels.auth_token_env
    if name is None:
        return None
    value = environ.get(name)
    if not value:
        raise TargetRefusedError(code="auth_token_missing")
    if len(value) > MAX_AUTH_TOKEN_CHARS or not _TOKEN.fullmatch(value):
        raise TargetRefusedError(code="auth_token_invalid")
    return BearerToken(value)


def check_connection_labels(dialect_id: str, labels: ConnectionLabels) -> None:
    if dialect_id == "twilio-media-stream/1":
        if labels.bot_holds_telephony_credentials is None:
            raise TargetRefusedError(code="bot_credentials_field_missing")
        if labels.bot_holds_telephony_credentials:
            raise TargetRefusedError(code="bot_holds_telephony_credentials")
    elif dialect_id in ("pipecat-protobuf/1", "smallwebrtc/1"):
        if labels.bot_holds_telephony_credentials is not None:
            raise TargetRefusedError(code="labels_inconsistent")
    else:
        raise TargetRefusedError(code="dialect_unknown")


def prepare_connection(
    dialect_id: str, labels: ConnectionLabels, *, environ: Mapping[str, str]
) -> BearerToken | None:
    """Step 2 refusals first (labels), then step 6 (the key read from the environment)."""
    check_connection_labels(dialect_id, labels)
    return read_bearer_token(labels, environ=environ)


@dataclass(frozen=True)
class ParsedTarget:
    scheme: str
    host: str
    port: int
    path: str
    loopback: bool


def parse_target_url(url: str, *, schemes: tuple[str, ...]) -> ParsedTarget:
    if not isinstance(url, str) or not url.isascii() or not url.isprintable() or " " in url:
        raise TargetRefusedError(code="target_invalid")
    if "?" in url or "#" in url:
        raise TargetRefusedError(code="target_invalid")
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise TargetRefusedError(code="target_invalid") from None
    if parts.scheme not in schemes or not host or "@" in parts.netloc:
        raise TargetRefusedError(code="target_invalid")
    secure = parts.scheme in ("wss", "https")
    if port is None:
        port = 443 if secure else 80
    if not 1 <= port <= 65535:
        raise TargetRefusedError(code="target_invalid")
    loopback = host in _LOOPBACK_HOSTS
    if not secure and not loopback:
        raise TargetRefusedError(code="insecure_target")
    return ParsedTarget(parts.scheme, host, port, parts.path or "/", loopback)


class _NoRedirectConnect(connect):
    """websockets follows redirects by default (even to another host): a redirect is an error."""

    def process_redirect(self, exc: Exception) -> Exception | str:
        return exc


class WebSocketConnection:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._close_info: CloseInfo | None = None

    @property
    def close_info(self) -> CloseInfo | None:
        return self._close_info

    async def send(self, message: bytes | str) -> None:
        failed = False
        try:
            await self._ws.send(message)
        except ConnectionClosed:
            failed = True
        if failed:
            raise TransportError(code="connection_closed")

    async def recv(self) -> bytes | str | None:
        too_big = False
        try:
            message: bytes | str = await self._ws.recv()
            return message
        except ConnectionClosed as exc:
            sent, rcvd = exc.sent, exc.rcvd
            if sent is not None and sent.code == 1009:
                too_big = True
            else:
                self._close_info = CloseInfo(
                    code=rcvd.code if rcvd is not None else 1006, clean=rcvd is not None
                )
        if too_big:
            self._close_info = CloseInfo(code=1009, clean=True)
            raise TransportError(code="message_too_large", close_code=1009)
        return None

    async def close(self, code: int = 1000) -> None:
        await self._ws.close(code)
        if self._close_info is None:
            self._close_info = CloseInfo(code=code, clean=True)


async def open_websocket(
    target: WebSocketTarget,
    dialect: Dialect,
    *,
    token: BearerToken | None,
    open_timeout_s: float,
    close_timeout_s: float,
    ssl_context: ssl.SSLContext | None = None,
) -> WebSocketConnection:
    parsed = parse_target_url(target.url, schemes=("ws", "wss"))
    check_connection_labels(dialect.dialect_id, target.labels)
    if target.labels.auth_token_env is None and token is not None:
        raise TargetRefusedError(code="labels_inconsistent")
    if target.labels.auth_token_env is not None and token is None:
        raise TargetRefusedError(code="auth_token_missing")
    headers = {"Authorization": token.authorization_header_value()} if token is not None else None
    kwargs: dict[str, Any] = {}
    if parsed.scheme == "wss":
        kwargs["ssl"] = ssl_context or ssl.create_default_context()
    failure: tuple[type[Exception], int | None] | None = None
    ws: Any = None
    try:
        ws = await _NoRedirectConnect(
            target.url,
            additional_headers=headers,
            origin=Origin(target.labels.origin) if target.labels.origin is not None else None,
            compression=None,
            max_size=MAX_INBOUND_MESSAGE_BYTES,
            open_timeout=open_timeout_s,
            close_timeout=close_timeout_s,
            proxy=None,
            logger=_SILENT,
            **kwargs,
        )
    except ssl.SSLError:
        failure = (SetupFailedError, None)
        code = "tls_failed"
    except TimeoutError:
        failure = (SetupFailedError, None)
        code = "connect_timeout"
    except InvalidStatus as exc:
        status = exc.response.status_code
        failure = (SetupFailedError, status)
        code = "redirect_refused" if 300 <= status < 400 else "handshake_rejected"
    except ConnectionRefusedError:
        failure = (SetupFailedError, None)
        code = "connection_refused"
    except InvalidHandshake:
        failure = (SetupFailedError, None)
        code = "handshake_failed"
    except OSError:
        failure = (SetupFailedError, None)
        code = "connection_failed"
    if failure is not None:
        raise SetupFailedError(code=code, http_status=failure[1])
    return WebSocketConnection(ws)
