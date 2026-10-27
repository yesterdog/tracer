# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Loopback fake servers of the tracer-evalsim tests. Support module, not collected.

Every server binds ``127.0.0.1`` (never an empty host, never ``0.0.0.0``) on port 0 and lives
inside the test's own event loop; the tests that use them carry the ``loopback`` marker (see
``evalsim_loopback_guard``). Nothing here reaches a host other than the loopback interface.

* ``ws_server``: a ``websockets`` server API server whose handshake requests are recorded
  (``RecordedRequest``: path and the raw headers) and which may answer the handshake itself
  (``respond``: a redirect, a 401, a plain 200), or accept it and hand a ``ServerWire`` to a
  handler (a fake agent, a hostile server).
* ``raw_server``: a bare TCP server (no HTTP), for a handshake that never completes.
* ``closed_port``: a loopback port nothing listens on (a refused connection).
* ``tls_pair``: a throw-away self-signed certificate for ``localhost`` / ``127.0.0.1`` made with
  ``cryptography`` (a pinned test dependency) and the two contexts: the server's, and a client
  context that trusts exactly that certificate.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import logging
import socket
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

#: the fake server's own websockets logging goes nowhere: at DEBUG the library writes every request
#: header it receives, which would put a bearer token into the captured logs of the TEST SERVER and
#: make "the token is in no log" unprovable
_SERVER_LOG = logging.getLogger("evalsim.test.server")
_SERVER_LOG.propagate = False
_SERVER_LOG.addHandler(logging.NullHandler())
_SERVER_LOG.setLevel(logging.CRITICAL + 1)


@dataclass(frozen=True)
class RecordedRequest:
    path: str
    headers: tuple[tuple[str, str], ...]

    def header_values(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    @property
    def text(self) -> str:
        """The request as one string, for "the secret appears nowhere" assertions."""
        return self.path + "\n" + "\n".join(f"{k}: {v}" for k, v in self.headers)


class ServerWire:
    """A server-side ``WireConnection`` over a websockets ``ServerConnection``."""

    def __init__(self, ws: ServerConnection) -> None:
        self.ws = ws

    async def send(self, message: bytes | str) -> None:
        await self.ws.send(message)

    async def recv(self) -> bytes | str | None:
        try:
            return await self.ws.recv()
        except ConnectionClosed:
            return None

    async def close(self, code: int = 1000) -> None:
        await self.ws.close(code)

    def abort(self) -> None:
        """Drop the TCP connection with no close frame."""
        self.ws.transport.abort()

    async def send_fragmented(self, chunks: list[bytes], gap_s: float) -> None:
        """One message in many frames, ``gap_s`` apart (a slow drip)."""

        async def parts() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk
                await asyncio.sleep(gap_s)

        await self.ws.send(parts())


@dataclass
class LoopbackWs:
    host: str
    port: int
    scheme: str = "ws"
    requests: list[RecordedRequest] = field(default_factory=list)
    handled: int = 0

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


Handler = Callable[[ServerWire], Awaitable[None]]
Responder = Callable[[Request], Response | None]


@asynccontextmanager
async def ws_server(
    handler: Handler,
    *,
    respond: Responder | None = None,
    ssl_context: ssl.SSLContext | None = None,
    host: str = "127.0.0.1",
    max_size: int | None = None,
) -> AsyncIterator[LoopbackWs]:
    box = LoopbackWs(host=host, port=0, scheme="wss" if ssl_context else "ws")

    def process_request(connection: ServerConnection, request: Request) -> Response | None:
        box.requests.append(
            RecordedRequest(path=request.path, headers=tuple(request.headers.raw_items()))
        )
        return respond(request) if respond is not None else None

    async def wrapped(ws: ServerConnection) -> None:
        box.handled += 1
        await handler(ServerWire(ws))

    async with serve(
        wrapped,
        host,
        0,
        process_request=process_request,
        ssl=ssl_context,
        max_size=max_size,
        compression=None,
        close_timeout=0.5,
        logger=_SERVER_LOG,
    ) as server:
        box.port = server.sockets[0].getsockname()[1]
        yield box


def http_response(status: int, reason: str, headers: dict[str, str], body: bytes = b"") -> Response:
    return Response(status, reason, Headers(headers), body)


@dataclass
class RawServer:
    port: int
    connections: int = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@asynccontextmanager
async def raw_server(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
) -> AsyncIterator[RawServer]:
    box = RawServer(port=0)

    async def counted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        box.connections += 1
        try:
            await handler(reader, writer)
        finally:
            writer.close()

    server = await asyncio.start_server(counted, "127.0.0.1", 0)
    try:
        box.port = server.sockets[0].getsockname()[1]
        yield box
    finally:
        server.close()
        await server.wait_closed()


def closed_port() -> int:
    """A loopback port with no listener: bind to get a free one, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def tls_pair(
    directory: Path,
    dns: tuple[str, ...] = ("localhost",),
    ips: tuple[str, ...] = ("127.0.0.1",),
) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """(server context, client context trusting only this certificate); the certificate names
    ``dns`` and ``ips`` as subject alternative names."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in dns]
                + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "loopback-cert.pem"
    key_path = directory / "loopback-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return server, client


def untrusting_client_context() -> ssl.SSLContext:
    """The context a verifying client has by default (it trusts none of the test certificates)."""
    return ssl.create_default_context()


async def wait_until(predicate: Callable[[], Any], timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


async def read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes, str]:
    """(method, target, lower-cased headers, body, the raw head as text) of one HTTP/1.1 request."""
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, target, _version = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    body = await reader.readexactly(int(headers.get("content-length", "0")))
    return method, target, headers, body, head.decode("latin-1")


def http_reply(status: int, body: bytes = b"", *, headers: dict[str, str] | None = None) -> bytes:
    lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}", "Connection: close"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
