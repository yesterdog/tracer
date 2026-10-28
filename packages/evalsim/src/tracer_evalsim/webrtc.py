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
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from types import ModuleType
from typing import Any

from tracer_evalsim.dialects.base import CloseInfo, Dialect
from tracer_evalsim.errors import SetupFailedError, TargetRefusedError, TransportError
from tracer_evalsim.limits import MAX_SDP_BYTES, MAX_SIGNALLING_RESPONSE_BYTES
from tracer_evalsim.ws import (
    BearerToken,
    ConnectionLabels,
    check_connection_labels,
    parse_target_url,
)

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
    "open_webrtc",
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
# Pipecat 1.14.0 writes "SmallWebRTCConnection#0-<32 hex>" (found by the interop run, batch F)
_PC_ID = re.compile(r"[A-Za-z0-9_.:#-]{1,256}")


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
        except Exception:  # a RecursionError of a hostile body is an invalid answer
            parsed = None
        if not isinstance(parsed, dict):
            raise SetupFailedError(code="answer_invalid")
        return parsed

    async def post_offer(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", body)

    async def patch_candidates(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("PATCH", body)


class WebRtcConnection:
    """The media of one ``smallwebrtc/1`` call as a ``WireConnection``: ``send`` hands one 20 ms PCM
    frame to the outgoing audio track, ``recv`` returns the next frame of the remote audio track
    (channel 0 of the decoded frame, 16-bit mono at the dialect rate) or ``None`` once the peer
    connection is gone."""

    def __init__(self, pc: Any, track: Any, incoming: asyncio.Queue[bytes | None]) -> None:
        self._pc = pc
        self._track = track
        self._incoming = incoming
        self._close_info: CloseInfo | None = None
        self._closed = False

    @property
    def close_info(self) -> CloseInfo | None:
        return self._close_info

    async def send(self, message: bytes | str) -> None:
        if self._closed or not isinstance(message, bytes):
            raise TransportError(code="connection_closed")
        self._track.push(message)

    async def recv(self) -> bytes | str | None:
        message = await self._incoming.get()
        if message is None:
            self._incoming.put_nowait(None)
            if self._close_info is None:
                self._close_info = CloseInfo(code=None, clean=False)
        return message

    async def close(self, code: int = 1000) -> None:
        if not self._closed:
            self._closed = True
            await self._pc.close()
        if self._close_info is None:
            self._close_info = CloseInfo(code=code, clean=True)
        self._incoming.put_nowait(None)


def _outgoing_track(aiortc: ModuleType, rate: int) -> Any:
    import av  # the optional extra: loaded only when a call is placed

    samples = rate * 20 // 1000
    track_base: Any = aiortc.mediastreams.MediaStreamTrack

    class PushedAudio(track_base):  # type: ignore[misc,valid-type,unused-ignore]
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self._queue: deque[bytes] = deque()
            self._start = 0.0
            self._pts = 0

        def push(self, frame: bytes) -> None:
            self._queue.append(frame)

        async def recv(self) -> Any:
            if self.readyState != "live":
                raise aiortc.mediastreams.MediaStreamError
            if self._pts == 0:
                self._start = time.monotonic()
            else:
                await asyncio.sleep(max(0.0, self._start + self._pts / rate - time.monotonic()))
            data = self._queue.popleft() if self._queue else bytes(samples * 2)
            frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
            frame.planes[0].update(data)
            frame.pts = self._pts
            frame.sample_rate = rate
            frame.time_base = Fraction(1, rate)
            self._pts += samples
            return frame

    return PushedAudio()


def _channel_zero(frame: Any) -> bytes:
    plane = bytes(frame.planes[0])
    channels = len(frame.layout.channels)
    width = frame.samples * 2
    if frame.format.is_planar or channels == 1:
        return plane[:width]
    view = memoryview(plane[: width * channels]).cast("h")
    return view[0::channels].tobytes()


async def open_webrtc(
    plan: WebRtcPlan,
    dialect: Dialect,
    *,
    labels: ConnectionLabels,
    token: BearerToken | None,
    open_timeout_s: float,
    close_timeout_s: float,
) -> WebRtcConnection:
    """Signal an offer to the target of ``plan`` and return the media connection once it is up.

    Refusals come first and send nothing: the dialect, the labels, the token, the extra. No ICE
    server is used unless the plan names one (host candidates only)."""
    if dialect.dialect_id != DIALECT_ID:
        raise TargetRefusedError(code="dialect_unknown")
    check_connection_labels(DIALECT_ID, labels)
    if labels.auth_token_env is None and token is not None:
        raise TargetRefusedError(code="labels_inconsistent")
    if labels.auth_token_env is not None and token is None:
        raise TargetRefusedError(code="auth_token_missing")
    aiortc = load_aiortc()
    pc = aiortc.RTCPeerConnection(configuration=build_rtc_configuration(plan))
    incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
    track = _outgoing_track(aiortc, dialect.sample_rate_hz)
    pc.addTrack(track)
    up = asyncio.Event()
    connection = WebRtcConnection(pc, track, incoming)

    async def pump(remote: Any) -> None:
        try:
            while True:
                frame = await remote.recv()
                if frame.sample_rate != dialect.sample_rate_hz:
                    incoming.put_nowait(None)
                    return
                incoming.put_nowait(_channel_zero(frame))
        except Exception:
            incoming.put_nowait(None)

    tasks: list[asyncio.Task[None]] = []

    @pc.on("track")  # type: ignore[untyped-decorator,misc,unused-ignore]
    def on_track(remote: Any) -> None:
        if remote.kind == "audio":
            tasks.append(asyncio.ensure_future(pump(remote)))

    @pc.on("connectionstatechange")  # type: ignore[untyped-decorator,misc,unused-ignore]
    async def on_state() -> None:
        if pc.connectionState == "connected":
            up.set()
        elif pc.connectionState in ("failed", "closed", "disconnected"):
            incoming.put_nowait(None)

    try:
        await pc.setLocalDescription(await pc.createOffer())
        signalling = HttpSignalling(plan, labels=labels, token=token, timeout_s=open_timeout_s)
        answer = parse_answer(
            await signalling.post_offer(offer_request_body(pc.localDescription.sdp))
        )
        await pc.setRemoteDescription(
            aiortc.RTCSessionDescription(sdp=answer.sdp, type=answer.type)
        )
        await asyncio.wait_for(up.wait(), open_timeout_s)
    except TimeoutError:
        await pc.close()
        raise SetupFailedError(code="connect_timeout") from None
    except (SetupFailedError, TargetRefusedError):
        await pc.close()
        raise
    except asyncio.CancelledError:
        await pc.close()
        raise
    except Exception:
        await pc.close()
        raise SetupFailedError(code="handshake_failed") from None
    return connection
