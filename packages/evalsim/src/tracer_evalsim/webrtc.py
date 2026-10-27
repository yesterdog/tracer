# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``smallwebrtc/1``: signalling and configuration (ADR 0026 rule 7). aiortc is imported lazily."""

from __future__ import annotations

import asyncio
import http.client
import importlib
import json
import re
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from tracer_evalsim.errors import SetupFailedError, TargetRefusedError
from tracer_evalsim.limits import MAX_SDP_BYTES, MAX_SIGNALLING_RESPONSE_BYTES
from tracer_evalsim.ws import BearerToken, ConnectionLabels, parse_target_url

__all__ = [
    "DIALECT_ID",
    "SPEC_RETRIEVED",
    "SPEC_SOURCES",
    "Answer",
    "HttpSignalling",
    "WebRtcPlan",
    "build_rtc_configuration",
    "load_aiortc",
    "make_webrtc_plan",
    "offer_request_body",
    "parse_answer",
    "parse_ice_server",
    "patch_request_body",
]

DIALECT_ID = "smallwebrtc/1"
SPEC_RETRIEVED = "2026-10-27"
SPEC_SOURCES = (
    "https://raw.githubusercontent.com/pipecat-ai/pipecat/v1.14.0/src/pipecat/runner/run.py",
    "https://raw.githubusercontent.com/pipecat-ai/pipecat/v1.14.0/src/pipecat/transports/smallwebrtc/request_handler.py",
    "https://raw.githubusercontent.com/pipecat-ai/pipecat/v1.14.0/src/pipecat/transports/smallwebrtc/connection.py",
)
OFFER_SUFFIX = "/api/offer"
_ICE = re.compile(r"(?:stuns?|turns?):[a-z0-9.\-\[\]:]{1,200}(?:\?transport=(?:udp|tcp))?")
_PC_ID = re.compile(r"[A-Za-z0-9_.:-]{1,256}")


def parse_ice_server(text: str) -> str:
    if not isinstance(text, str) or not _ICE.fullmatch(text):
        raise TargetRefusedError(code="ice_server_invalid")
    return text


@dataclass(frozen=True)
class WebRtcPlan:
    offer_url: str
    ice_servers: tuple[str, ...]
    loopback: bool


def make_webrtc_plan(offer_url: str, ice_servers: Sequence[str]) -> WebRtcPlan:
    parsed = parse_target_url(offer_url, schemes=("http", "https"))
    if not parsed.path.endswith(OFFER_SUFFIX):
        raise TargetRefusedError(code="target_invalid")
    servers = tuple(parse_ice_server(s) for s in ice_servers)
    if not parsed.loopback and not servers:
        raise TargetRefusedError(code="ice_server_required")
    return WebRtcPlan(offer_url, servers, parsed.loopback)


def load_aiortc() -> ModuleType:
    failed = False
    module: ModuleType | None = None
    try:
        module = importlib.import_module("aiortc")
    except ImportError:
        failed = True
    if failed or module is None:
        raise TargetRefusedError(code="webrtc_extra_missing")
    return module


def build_rtc_configuration(plan: WebRtcPlan) -> Any:
    aiortc = load_aiortc()
    servers = [aiortc.RTCIceServer(urls=url) for url in plan.ice_servers]
    # an EMPTY list, never None: aiortc turns None into its default Google STUN server
    return aiortc.RTCConfiguration(iceServers=servers)


def offer_request_body(
    sdp: str,
    *,
    pc_id: str | None = None,
    restart_pc: bool | None = None,
    request_data: Any = None,
) -> dict[str, Any]:
    if not isinstance(sdp, str) or not sdp:
        raise ValueError("sdp must be a non-empty string")
    body: dict[str, Any] = {"sdp": sdp, "type": "offer"}
    if pc_id is not None:
        body["pc_id"] = pc_id
    if restart_pc is not None:
        body["restart_pc"] = restart_pc
    if request_data is not None:
        body["request_data"] = request_data
    return body


def patch_request_body(pc_id: str, candidates: Sequence[tuple[str, str, int]]) -> dict[str, Any]:
    if not isinstance(pc_id, str) or not _PC_ID.fullmatch(pc_id):
        raise ValueError("pc_id is not a safe identifier")
    return {
        "pc_id": pc_id,
        "candidates": [
            {"candidate": c, "sdp_mid": mid, "sdp_mline_index": index}
            for c, mid, index in candidates
        ],
    }


@dataclass(frozen=True)
class Answer:
    sdp: str
    type: str
    pc_id: str


def parse_answer(payload: object) -> Answer:
    ok = False
    if isinstance(payload, dict):
        sdp, kind, pc_id = payload.get("sdp"), payload.get("type"), payload.get("pc_id")
        ok = (
            isinstance(sdp, str)
            and 0 < len(sdp.encode("utf-8")) <= MAX_SDP_BYTES
            and kind == "answer"
            and isinstance(pc_id, str)
            and _PC_ID.fullmatch(pc_id) is not None
        )
    if not ok:
        raise SetupFailedError(code="answer_invalid")
    return Answer(sdp=payload["sdp"], type="answer", pc_id=payload["pc_id"])  # type: ignore[index]


def _request(
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_s: float,
    context: ssl.SSLContext | None,
) -> tuple[str | None, int | None, bytes]:
    parsed = parse_target_url(url, schemes=("http", "https"))
    conn: http.client.HTTPConnection
    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(
            parsed.host,
            parsed.port,
            timeout=timeout_s,
            context=context or ssl.create_default_context(),
        )
    else:
        conn = http.client.HTTPConnection(parsed.host, parsed.port, timeout=timeout_s)
    try:
        conn.request(method, parsed.path, body, headers)
        response = conn.getresponse()
        data = response.read(MAX_SIGNALLING_RESPONSE_BYTES + 1)
        return None, response.status, data
    except ssl.SSLError:
        return "tls_failed", None, b""
    except TimeoutError:
        return "connect_timeout", None, b""
    except ConnectionRefusedError:
        return "connection_refused", None, b""
    except http.client.HTTPException:
        return "handshake_failed", None, b""
    except OSError:
        return "connection_failed", None, b""
    finally:
        conn.close()


class HttpSignalling:
    def __init__(
        self,
        plan: WebRtcPlan,
        *,
        labels: ConnectionLabels,
        token: BearerToken | None,
        timeout_s: float,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._plan = plan
        self._labels = labels
        self._token = token
        self._timeout_s = timeout_s
        self._ssl = ssl_context

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = self._token.authorization_header_value()
        if self._labels.origin is not None:
            headers["Origin"] = self._labels.origin
        return headers

    async def _call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        code, status, payload = await asyncio.to_thread(
            _request,
            method,
            self._plan.offer_url,
            data,
            self._headers(),
            self._timeout_s,
            self._ssl,
        )
        if code is not None:
            raise SetupFailedError(code=code)
        if status is None:
            raise SetupFailedError(code="handshake_failed")
        if 300 <= status < 400:
            raise SetupFailedError(code="redirect_refused", http_status=status)
        if not 200 <= status < 300:
            raise SetupFailedError(code="handshake_rejected", http_status=status)
        if len(payload) > MAX_SIGNALLING_RESPONSE_BYTES:
            raise SetupFailedError(code="answer_invalid")
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            raise SetupFailedError(code="answer_invalid")
        return parsed

    async def post_offer(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", body)

    async def patch_candidates(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("PATCH", body)
