# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-16, P4-17: the WebSocket client against fake servers on the loopback interface (ADR 0026
rules 5, 6, 16, 22; contract ``docs/phase4/api-surface-batch-d.md`` section 5).

Every test here carries the ``loopback`` marker (named conftest change S-D1): it talks to a
``websockets`` server that the test starts itself on 127.0.0.1, in the same process, and to no
other host. Nothing here uses a key. The servers are in ``evalsim_support_servers``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    FakeAgent,
    HangUp,
    Interruption,
    ProtobufAgentCodec,
    Speak,
    TwilioAgentCodec,
    Wait,
)
from evalsim_support_clock import ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import leaks, mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_servers import (  # type: ignore[import-not-found,unused-ignore]
    ServerWire,
    closed_port,
    http_response,
    raw_server,
    tls_pair,
    wait_until,
    ws_server,
)
from evalsim_support_wire import InMemoryWire  # type: ignore[import-not-found,unused-ignore]

pytestmark = pytest.mark.loopback

SECRET = "tok-SECRET_abc.123~"  # noqa: S105 - a test value, not a secret
ORIGIN = "http://studio.example.test:3000"


def labels(**kwargs: Any) -> Any:
    base: dict[str, Any] = {
        "auth_token_env": None,
        "origin": None,
        "bot_holds_telephony_credentials": None,
    }
    return mod("ws").ConnectionLabels(**{**base, **kwargs})


def protobuf() -> Any:
    return mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000)


def twilio() -> Any:
    return mod("dialects.twilio").TwilioMediaStreamDialect(counter=1)


async def connect(
    url: str,
    *,
    dialect: Any = None,
    entry: Any = None,
    token: Any = None,
    open_timeout_s: float = 3.0,
    close_timeout_s: float = 1.0,
    ssl_context: ssl.SSLContext | None = None,
) -> Any:
    ws = mod("ws")
    target = ws.WebSocketTarget(url=url, labels=entry or labels())
    return await ws.open_websocket(
        target,
        dialect or protobuf(),
        token=token,
        open_timeout_s=open_timeout_s,
        close_timeout_s=close_timeout_s,
        ssl_context=ssl_context,
    )


async def until_closed(wire: ServerWire) -> None:
    while await wire.recv() is not None:
        pass


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_bearer_token_only_in_authorization_header_never_path_or_query() -> None:
    """ADR 0026 rule 16: the token is sent only as ``Authorization: Bearer``; never in the path,
    never in a query, never in another header (BL-300). The server records the real handshake."""
    ws = mod("ws")
    entry = labels(auth_token_env="TRACER_TEST_TOKEN")  # noqa: S106 - a name, not a secret
    token = ws.prepare_connection(
        "pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": SECRET}
    )

    async def main() -> Any:
        async with ws_server(until_closed) as server:
            conn = await connect(f"{server.url}/ws-client", entry=entry, token=token)
            await conn.close()
            await wait_until(lambda: server.handled == 1)
            return server.requests[0]

    request = run(main())
    assert request.path == "/ws-client"  # no query, no token segment
    assert request.header_values("authorization") == ["Bearer " + SECRET]
    assert request.text.count(SECRET) == 1  # in that header and nowhere else
    assert "token" not in request.path.lower()
    for name, value in request.headers:
        if name.lower() != "authorization":
            assert SECRET not in value, name


def test_no_authorization_header_without_a_token() -> None:
    async def main() -> Any:
        async with ws_server(until_closed) as server:
            conn = await connect(f"{server.url}/ws")
            await conn.close()
            await wait_until(lambda: server.handled == 1)
            return server.requests[0]

    request = run(main())
    assert request.header_values("authorization") == []
    assert request.header_values("cookie") == []


def test_bearer_token_never_in_any_log_record_or_output(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The websockets library writes every request header at DEBUG (``> Authorization: ...``):
    the client must keep its logging away from it (a silent logger), so that neither a log
    record at DEBUG on any logger nor the process output ever holds the token."""
    ws = mod("ws")
    entry = labels(auth_token_env="TRACER_TEST_TOKEN")  # noqa: S106 - a name, not a secret
    token = ws.prepare_connection(
        "pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": SECRET}
    )
    caplog.set_level(logging.DEBUG)
    logging.getLogger("websockets").setLevel(logging.DEBUG)

    def reject(request: Any) -> Any:
        return http_response(401, "Unauthorized", {"Content-Type": "text/plain"}, b"no")

    async def main() -> None:
        async with ws_server(until_closed) as ok:
            conn = await connect(f"{ok.url}/ws-client", entry=entry, token=token)
            await conn.send(b"\x00")
            await conn.close()
        async with ws_server(until_closed, respond=reject) as bad:
            with pytest.raises(mod("errors").SetupFailedError):
                await connect(f"{bad.url}/ws-client", entry=entry, token=token)

    run(main())
    assert SECRET not in caplog.text
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in repr(record.args)
        assert not record.exc_info or SECRET not in repr(record.exc_info[1])
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_bearer_token_not_in_errors_or_connection_state() -> None:
    ws = mod("ws")
    errors = mod("errors")
    entry = labels(auth_token_env="TRACER_TEST_TOKEN")  # noqa: S106 - a name, not a secret
    token = ws.prepare_connection(
        "pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": SECRET}
    )

    def reject(request: Any) -> Any:
        return http_response(401, "Unauthorized", {"WWW-Authenticate": "Bearer"}, SECRET.encode())

    async def main() -> tuple[Any, Any]:
        async with ws_server(until_closed, respond=reject) as bad:
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(f"{bad.url}/ws-client", entry=entry, token=token)
        async with ws_server(until_closed) as ok:
            conn = await connect(f"{ok.url}/ws-client", entry=entry, token=token)
            state = conn
            await conn.close()
        return raised.value, state

    failure, conn = run(main())
    assert (failure.code, failure.http_status) == ("handshake_rejected", 401)
    assert not leaks(failure, SECRET)  # not in str, repr, args or attributes
    assert failure.__cause__ is None and failure.__context__ is None  # raised outside any handler  # noqa: PT018 - one fact in two clauses
    assert not leaks(conn, SECRET)  # nothing of tracer_evalsim holds it after the connect
    assert failure.code in errors.SETUP_CODES


def test_origin_header_from_entry_or_absent() -> None:
    """ADR 0026 rule 16: Origin is the entry's ``origin`` field; without it none is sent."""

    async def main() -> tuple[Any, Any]:
        async with ws_server(until_closed) as server:
            plain = await connect(f"{server.url}/ws-client")
            await plain.close()
            marked = await connect(f"{server.url}/ws-client", entry=labels(origin=ORIGIN))
            await marked.close()
            await wait_until(lambda: server.handled == 2)
            return server.requests[0], server.requests[1]

    absent, present = run(main())
    assert absent.header_values("origin") == []
    assert present.header_values("origin") == [ORIGIN]  # exactly the entry's value, one header


def test_origin_the_server_refuses_is_a_rejected_handshake_not_a_retry() -> None:
    def refuse_unlisted(request: Any) -> Any:
        if request.headers.get("Origin") != ORIGIN:
            return http_response(403, "Forbidden", {})
        return None

    async def main() -> None:
        errors = mod("errors")
        async with ws_server(until_closed, respond=refuse_unlisted) as server:
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(f"{server.url}/ws")
            assert (raised.value.code, raised.value.http_status) == ("handshake_rejected", 403)
            assert len(server.requests) == 1  # no retry, no second attempt
            ok = await connect(f"{server.url}/ws", entry=labels(origin=ORIGIN))
            await ok.close()

    run(main())


def test_websocket_client_offers_no_extension_and_no_subprotocol() -> None:
    """Hardening (ADR 0026 rule 22): no permessage-deflate, so no decompression bomb; audio does
    not compress anyway."""

    async def main() -> Any:
        async with ws_server(until_closed) as server:
            conn = await connect(f"{server.url}/ws")
            await conn.close()
            await wait_until(lambda: server.handled == 1)
            return server.requests[0]

    request = run(main())
    assert request.header_values("sec-websocket-extensions") == []
    assert request.header_values("sec-websocket-protocol") == []


def test_the_three_written_loopback_names_all_connect() -> None:
    """127.0.0.1, localhost and ::1 are the loopback targets the ADR allows over plain ws://."""
    errors_hit: list[str] = []

    async def hello(wire: ServerWire) -> None:
        await wire.send(b"hello")
        await until_closed(wire)

    async def one(url: str) -> Any:
        conn = await connect(url)
        first = await conn.recv()
        await conn.close()
        return first

    async def main() -> list[Any]:
        got = []
        async with ws_server(hello) as v4:
            got.append(await one(f"ws://127.0.0.1:{v4.port}/ws"))
            got.append(await one(f"ws://localhost:{v4.port}/ws"))
        try:
            async with ws_server(hello, host="::1") as v6:
                got.append(await one(f"ws://[::1]:{v6.port}/ws"))
        except OSError:
            errors_hit.append("no IPv6 loopback on this machine")
        return got

    got = run(main())
    assert got[:2] == [b"hello", b"hello"]
    assert got[2:] in ([b"hello"], [])
    assert len(got) == 3 or errors_hit


def test_the_proxy_environment_is_ignored_the_connection_goes_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """websockets 15 and later read HTTP_PROXY, WS_PROXY, ALL_PROXY and the system proxy settings by
    default: a stale variable would send the call (and the bearer token) to a proxy. The client
    connects to the target directly. A raw server stands in for the proxy."""

    async def main() -> tuple[int, int]:
        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1)

        async with raw_server(proxy) as stand_in, ws_server(until_closed) as server:
            for name in (
                "WS_PROXY",
                "ws_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "ALL_PROXY",
                "all_proxy",
            ):
                monkeypatch.setenv(name, f"http://127.0.0.1:{stand_in.port}")
            monkeypatch.delenv("NO_PROXY", raising=False)
            monkeypatch.delenv("no_proxy", raising=False)
            conn = await connect(f"{server.url}/ws")
            await conn.close()
            await wait_until(lambda: server.handled == 1)
            return stand_in.connections, server.handled

    assert run(main()) == (0, 1)


def test_refused_connection_is_setup_failed_with_no_chain() -> None:
    errors = mod("errors")

    async def main() -> Any:
        with pytest.raises(errors.SetupFailedError) as raised:
            await connect(f"ws://127.0.0.1:{closed_port()}/ws")
        return raised.value

    err = run(main())
    assert err.code == "connection_refused"
    assert err.END_REASON == "setup_failed"
    assert err.__cause__ is None and err.__context__ is None  # noqa: PT018 - one fact in two clauses
    assert not isinstance(
        err, OSError
    )  # a setup failure is an outcome, not an exception to the runner's caller


def test_a_handshake_that_never_completes_is_setup_failed_within_the_timeout() -> None:
    errors = mod("errors")

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()  # take the request, then say nothing

    async def main() -> float:
        async with raw_server(silent) as server:
            started = asyncio.get_running_loop().time()
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(server.url + "/ws", open_timeout_s=0.3)
            assert raised.value.code == "connect_timeout"
            return asyncio.get_running_loop().time() - started

    assert run(main()) < 3.0


def test_a_server_that_answers_with_junk_is_a_failed_handshake() -> None:
    errors = mod("errors")

    async def junk(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"garbage that is not http\r\n\r\n")
        await writer.drain()

    async def main() -> None:
        async with raw_server(junk) as server:
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(server.url + "/ws", open_timeout_s=2.0)
            assert raised.value.code == "handshake_failed"

    run(main())


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_in_the_handshake_is_never_followed(status: int) -> None:
    """ADR 0026 rule 16: a redirect ends the call with ``setup_failed``; the second server is never
    contacted (a followed redirect could reach a host that is not on the allowlist)."""
    errors = mod("errors")

    async def main() -> tuple[Any, int, int]:
        async with (
            ws_server(until_closed) as target,
            ws_server(until_closed, respond=None) as _unused,
        ):
            del _unused

            def redirect(request: Any) -> Any:
                return http_response(status, "Moved", {"Location": f"{target.url}/ws"})

            async with ws_server(until_closed, respond=redirect) as origin:
                with pytest.raises(errors.SetupFailedError) as raised:
                    await connect(f"{origin.url}/ws")
                return raised.value, len(target.requests), len(origin.requests)

    err, target_requests, origin_requests = run(main())
    assert (err.code, err.http_status) == ("redirect_refused", status)
    assert target_requests == 0
    assert origin_requests == 1


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 500, 503])
def test_a_handshake_answered_with_any_other_status_is_rejected_with_the_status(
    status: int,
) -> None:
    errors = mod("errors")

    def answer(request: Any) -> Any:
        return http_response(status, "X", {"Content-Type": "text/plain"}, b"SECRET-body")

    async def main() -> Any:
        async with ws_server(until_closed, respond=answer) as server:
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(f"{server.url}/ws")
            return raised.value

    err = run(main())
    assert (err.code, err.http_status) == ("handshake_rejected", status)
    assert "SECRET" not in str(err) and "SECRET" not in repr(err.args)  # the body is never quoted  # noqa: PT018 - one fact in two clauses


def test_wss_certificate_verification_is_on_by_default(tmp_path: Path) -> None:
    errors = mod("errors")
    server_ctx, trusting_ctx = tls_pair(tmp_path)

    async def hello(wire: ServerWire) -> None:
        await wire.send(b"hello")
        await until_closed(wire)

    async def main() -> tuple[Any, Any]:
        async with ws_server(hello, ssl_context=server_ctx, host="127.0.0.1") as server:
            # the default context trusts none of the throw-away certificates: the connect fails
            # at the TLS handshake and the server never sees an HTTP request
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(f"{server.url}/ws-client")
            assert server.requests == []
            assert server.handled == 0
            # a context that trusts exactly this certificate connects (the verified path works,
            # for a name and for the IP literal in the certificate)
            conn = await connect(f"{server.url}/ws-client", ssl_context=trusting_ctx)
            first = await conn.recv()
            await conn.close()
            by_name = await connect(
                f"wss://localhost:{server.port}/ws-client", ssl_context=trusting_ctx
            )
            await by_name.close()
            return raised.value, first

    err, first = run(main())
    assert err.code == "tls_failed"
    assert err.END_REASON == "setup_failed"
    assert first == b"hello"


def test_wss_hostname_must_match_the_certificate(tmp_path: Path) -> None:
    """A certificate that the client trusts but that names other hosts does not verify: the host
    name check stays on."""
    errors = mod("errors")
    server_ctx, trusting_ctx = tls_pair(tmp_path, dns=("other.example.test",), ips=())

    async def main() -> None:
        async with ws_server(until_closed, ssl_context=server_ctx) as server:
            with pytest.raises(errors.SetupFailedError) as raised:
                await connect(f"{server.url}/ws", ssl_context=trusting_ctx)
            assert raised.value.code == "tls_failed"
            assert server.requests == []

    run(main())


def test_twilio_call_ends_by_socket_close_after_stop() -> None:
    """ADR 0026 rule 6 (BL-299): the Pipecat serializer ignores an inbound ``stop``, so the call
    ends when Tracer CLOSES THE SOCKET. Tracer sends ``stop`` for Twilio-compatible servers, then
    closes, and never waits for the server to react to the ``stop``."""
    ws = mod("ws")
    base = mod("dialects.base")
    received: list[str] = []
    closed_by_client: list[bool] = []

    async def pipecat_like(wire: ServerWire) -> None:
        # reads and ignores everything, as Pipecat's Twilio serializer ignores start and stop
        while (message := await wire.recv()) is not None:
            received.append(message if isinstance(message, str) else message.hex())
        closed_by_client.append(True)

    async def main() -> Any:
        entry = labels(bot_holds_telephony_credentials=False)
        async with ws_server(pipecat_like) as server:
            d = twilio()
            conn = await ws.open_websocket(
                ws.WebSocketTarget(url=f"{server.url}/ws", labels=entry),
                d,
                token=None,
                open_timeout_s=3.0,
                close_timeout_s=1.0,
            )
            for message in d.opening_messages():
                await conn.send(message)
            await conn.send(d.encode_audio(bytes([0x55]) * 160, stream_ms=0))
            started = asyncio.get_running_loop().time()
            await base.hang_up(conn, d)
            elapsed = asyncio.get_running_loop().time() - started
            await wait_until(lambda: bool(closed_by_client))
            return conn.close_info, elapsed

    info, elapsed = run(main())
    events = [json.loads(m)["event"] for m in received]
    assert events == ["connected", "start", "media", "stop"]  # stop was sent, in order, last
    assert closed_by_client == [True]  # and then the socket was closed by the client
    assert info is not None and info.code == 1000  # noqa: PT018 - one fact in two clauses
    assert elapsed < 1.0  # no wait for the server to answer the stop


def test_twilio_target_with_credentials_false_connects_and_true_never_reaches_the_server() -> None:
    ws = mod("ws")
    errors = mod("errors")

    async def main() -> tuple[int, int]:
        async with ws_server(until_closed) as server:
            conn = await ws.open_websocket(
                ws.WebSocketTarget(
                    url=f"{server.url}/ws", labels=labels(bot_holds_telephony_credentials=False)
                ),
                twilio(),
                token=None,
                open_timeout_s=3.0,
                close_timeout_s=1.0,
            )
            await conn.close()
            with pytest.raises(errors.TargetRefusedError):
                await ws.open_websocket(
                    ws.WebSocketTarget(
                        url=f"{server.url}/ws", labels=labels(bot_holds_telephony_credentials=True)
                    ),
                    twilio(),
                    token=None,
                    open_timeout_s=3.0,
                    close_timeout_s=1.0,
                )
            with pytest.raises(errors.TargetRefusedError):
                await ws.open_websocket(
                    ws.WebSocketTarget(url=f"{server.url}/ws", labels=labels()),
                    twilio(),
                    token=None,
                    open_timeout_s=3.0,
                    close_timeout_s=1.0,
                )
            await wait_until(lambda: server.handled == 1)
            return len(server.requests), server.handled

    assert run(main()) == (1, 1)  # only the `false` entry opened a connection at all


def test_close_info_reports_who_closed_and_how() -> None:
    async def server_closes(wire: ServerWire) -> None:
        await wire.close(1001)

    async def server_aborts(wire: ServerWire) -> None:
        wire.abort()

    async def main() -> list[Any]:
        out = []
        base = mod("dialects.base")
        clock = mod("clock").SystemClock()
        for handler in (server_closes, server_aborts):
            async with ws_server(handler) as server:
                conn = await connect(f"{server.url}/ws")
                end = await base.InboundReader(protobuf(), clock).run(conn, lambda event: None)
                out.append((end.clean, end.close_code, conn.close_info))
                with pytest.raises(mod("errors").TransportError) as raised:
                    await conn.send(b"late")
                assert raised.value.code == "connection_closed"
                assert await conn.recv() is None  # and again: a closed connection stays closed
        async with ws_server(until_closed) as server:
            conn = await connect(f"{server.url}/ws")
            await conn.close(1000)
            out.append((None, None, conn.close_info))
        return out

    (a_clean, a_code, a_info), (b_clean, b_code, b_info), (_, _, c_info) = run(main())
    assert (a_clean, a_code) == (True, 1001)
    assert (a_info.code, a_info.clean) == (1001, True)
    assert (b_clean, b_code) == (False, 1006)
    assert (b_info.code, b_info.clean) == (1006, False)
    assert (c_info.code, c_info.clean) == (1000, True)


def test_pipecat_runner_close_code_4003_is_reported_with_its_code() -> None:
    """The runner closes with 4003 when a token or an Origin check fails (ADR 0026, protobuf
    dialect). Tracer reports the code; how a call that ends so is classified is the runner's."""

    async def reject(wire: ServerWire) -> None:
        await wire.close(4003)

    async def main() -> Any:
        base = mod("dialects.base")
        async with ws_server(reject) as server:
            conn = await connect(f"{server.url}/ws-client")
            return await base.InboundReader(protobuf(), mod("clock").SystemClock()).run(
                conn, lambda e: None
            )

    end = run(main())
    assert (end.clean, end.close_code) == (True, 4003)


@pytest.mark.parametrize(
    ("dialect_factory", "codec_factory", "entry_kwargs", "interruption_kind"),
    [
        (protobuf, lambda: ProtobufAgentCodec(16000), {}, "interruption"),
        (
            twilio,
            lambda: TwilioAgentCodec("tracer-sim-stream-1"),
            {"bot_holds_telephony_credentials": False},
            "clear",
        ),
    ],
    ids=["protobuf", "twilio"],
)
def test_fake_agent_over_loopback_matches_the_in_memory_run(
    dialect_factory: Callable[[], Any],
    codec_factory: Callable[[], Any],
    entry_kwargs: dict[str, Any],
    interruption_kind: str,
) -> None:
    """P4-19: the same fake agent, served over a real WebSocket, sends the same messages in the
    same order as in memory, and the client decodes the same events."""
    steps = (Wait(20), Speak(3), Interruption(), Speak(1, burst=True), HangUp(1000))
    entry = labels(**entry_kwargs)

    clock = ManualClock()
    a, b = InMemoryWire.pair()
    memory_agent = FakeAgent(codec_factory(), steps)

    async def drain() -> None:
        while await a.recv() is not None:
            pass

    async def memory() -> None:
        task = asyncio.ensure_future(memory_agent.run(b, clock))
        await drain()
        await task

    run_virtual(clock, memory)
    expected = [m for _, m in memory_agent.sent_log]

    async def main() -> tuple[list[Any], list[Any]]:
        agent = FakeAgent(codec_factory(), steps)

        async def serve(wire: ServerWire) -> None:
            await agent.run(wire, mod("clock").SystemClock())

        events: list[Any] = []
        async with ws_server(serve) as server:
            d = dialect_factory()
            conn = await connect(f"{server.url}/ws", dialect=d, entry=entry)
            reader = mod("dialects.base").InboundReader(d, mod("clock").SystemClock())
            end = await reader.run(conn, events.append)
            assert end.close_code == 1000
        return [m for _, m in agent.sent_log], events

    sent, events = run(main())
    assert sent == expected
    assert [e.kind for e in events] == ["audio", "audio", "audio", interruption_kind, "audio"]
