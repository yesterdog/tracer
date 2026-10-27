# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""A loopback fake OpenAI-compatible server for the judge tests (P4-J16; ADR 0030 rule 25).

Standard library only (``http.server``), bound to the literal loopback address ``127.0.0.1`` on an
ephemeral port, serving a scripted list of replies, one per request, and recording what it was
sent. The test that starts it carries the ``loopback`` marker, so only that test may reach a
loopback address (``conftest.py``, ``evalsim_loopback_guard.py``). No key, no external host, no
second process. Support module, not collected.
"""

from __future__ import annotations

import json
import socketserver
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

__all__ = ["FakeOpenAIServer", "Reply", "Seen", "chat_body"]


@dataclass(frozen=True)
class Reply:
    """One scripted reply. ``body`` is bytes, text (sent as is) or a JSON-able document."""

    body: Any
    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    content_type: str = "application/json"


@dataclass(frozen=True)
class Seen:
    """What the server was sent."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes

    def document(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.body.decode("utf-8"))
        return loaded


def chat_body(
    content: str,
    *,
    reply_id: str = "loop-1",
    model: str = "loopback-model-echo",
    prompt: int = 90,
    completion: int = 14,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """A chat-completions reply document in the OpenAI shape."""
    return {
        "id": reply_id,
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # not HTTPServer.server_bind: it looks up the host name of the address
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class FakeOpenAIServer:
    """``with FakeOpenAIServer([Reply(...)]) as server:``; ``server.url`` is the endpoint URL and
    ``server.seen`` the requests in order. A request beyond the script gets 500 and is recorded."""

    def __init__(self, replies: Sequence[Reply], *, path: str = "/v1/chat/completions") -> None:
        self._replies = list(replies)
        self._path = path
        self.seen: list[Seen] = []
        self.unscripted = 0
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # one request per connection

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                headers = {k.lower(): v for k, v in self.headers.items()}
                with outer._lock:
                    outer.seen.append(Seen("POST", self.path, headers, body))
                    position = len(outer.seen) - 1
                    scripted = outer._replies[position] if position < len(outer._replies) else None
                    if scripted is None:
                        outer.unscripted += 1
                if scripted is None:
                    self._send(500, b'{"error": {"code": "unscripted"}}', "application/json", {})
                    return
                raw = scripted.body
                if isinstance(raw, str):
                    payload = raw.encode("utf-8")
                elif isinstance(raw, bytes):
                    payload = raw
                else:
                    payload = json.dumps(raw).encode("utf-8")
                self._send(scripted.status, payload, scripted.content_type, scripted.headers)

            def do_GET(self) -> None:
                self._send(404, b"{}", "application/json", {})

            def _send(
                self, status: int, payload: bytes, content_type: str, headers: Mapping[str, str]
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                return None  # nothing to the console

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self._path}"

    def start(self) -> FakeOpenAIServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeOpenAIServer:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
