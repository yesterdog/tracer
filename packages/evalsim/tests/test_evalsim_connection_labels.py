# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-16, P4-17 (the rules apply to both WebSocket dialects): the connection labels of an
allowlist entry, the bearer token read from a named environment variable, the target URL rules
(ADR 0026 rule 16; ADR 0027 rule 13 ``connection``; contract ``docs/phase4/api-surface-batch-d.md``
section 5). Open no socket: every refusal here happens BEFORE any socket, and the guard of
``conftest.py`` turns any socket use into an ``AssertionError``, so a test passing proves none.

The handshake side of the same rules (the header really carries the token, nothing else does, the
``Origin`` header really is absent or exact) is in ``test_evalsim_websocket_loopback.py``.
"""

from __future__ import annotations

import asyncio
import copy
import pickle
from typing import Any

import pytest

from evalsim_support_imports import leaks, mod  # type: ignore[import-not-found,unused-ignore]

SECRET = "tok-SECRET_abc.123~x+y/z=="  # noqa: S105 - a test value, not a secret


def labels(**kwargs: Any) -> Any:
    base: dict[str, Any] = {
        "auth_token_env": None,
        "origin": None,
        "bot_holds_telephony_credentials": None,
    }
    return mod("ws").ConnectionLabels(**{**base, **kwargs})


def protobuf() -> Any:
    return mod("dialects.protobuf_dialect").PipecatProtobufDialect(sample_rate_hz=16000)


def test_bearer_token_from_env_never_stored() -> None:
    ws = mod("ws")
    errors = mod("errors")
    entry = labels(auth_token_env="TRACER_TEST_TOKEN")  # noqa: S106 - a name, not a secret
    token = ws.prepare_connection(
        "pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": SECRET}
    )
    assert token is not None
    assert labels().as_profile_connection() == {
        "auth_token_env": None,
        "origin": None,
        "bot_holds_telephony_credentials": None,
    }
    full = labels(
        auth_token_env="X_TOKEN",  # noqa: S106 - a variable name, not a secret
        origin="https://a.test",
        bot_holds_telephony_credentials=False,
    )
    assert full.as_profile_connection() == {
        "auth_token_env": "X_TOKEN",
        "origin": "https://a.test",
        "bot_holds_telephony_credentials": False,
    }
    # the only record of the token is the NAME of the variable: the labels, the target and the
    # profile document hold nothing else (ADR 0027 rule 13: "never its value")
    target = ws.WebSocketTarget(url="ws://127.0.0.1:7860/ws-client", labels=entry)
    assert entry.auth_token_env == "TRACER_TEST_TOKEN"  # noqa: S105 - a test value, not a secret
    assert entry.as_profile_connection() == {
        "auth_token_env": "TRACER_TEST_TOKEN",
        "origin": None,
        "bot_holds_telephony_credentials": None,
    }
    for holder in (entry, target, entry.as_profile_connection(), repr(entry), repr(target)):
        assert not leaks(holder, SECRET)
    # the token object never shows, copies or pickles its value
    for text in (
        repr(token),
        str(token),
        format(token, ""),
        f"{token}",
        f"{token!r}",
        "{}".format(token),  # noqa: UP032 - the old formatting paths are the point
    ):
        assert SECRET not in text
    assert not hasattr(token, "__dict__")
    with pytest.raises(TypeError):
        vars(token)
    for clone in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            clone(token)
    with pytest.raises(AttributeError):
        token.extra = 1
    # the one way out is the header value, used by the connection function and nothing else
    assert token.authorization_header_value() == "Bearer " + SECRET
    # a token the header cannot carry is refused, and the refusal never quotes it
    for bad in (
        "two words",
        "line\nbreak",
        "cr\rhere",
        'q"uote',
        "semi;colon",
        "caf\u00e9",
        "a=b",
        "x" * 4097,
    ):
        with pytest.raises(errors.TargetRefusedError) as raised:
            ws.prepare_connection("pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": bad})
        assert raised.value.code == "auth_token_invalid"
        assert not leaks(raised.value, bad)
    for good in ("a", "abc.DEF-123_~+/=", "a" * 4096, "abc=="):
        assert ws.prepare_connection(
            "pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": good}
        )
    # no variable named: no token, and the environment is not read at all
    assert (
        ws.prepare_connection("pipecat-protobuf/1", labels(), environ={"TRACER_TEST_TOKEN": SECRET})
        is None
    )


def test_missing_auth_token_env_refused_before_socket() -> None:
    ws = mod("ws")
    errors = mod("errors")
    entry = labels(auth_token_env="TRACER_TEST_TOKEN")  # noqa: S106 - a name, not a secret
    for environ in ({}, {"TRACER_TEST_TOKEN": ""}, {"OTHER": "x"}):
        with pytest.raises(errors.TargetRefusedError) as raised:
            ws.prepare_connection("pipecat-protobuf/1", entry, environ=environ)
        assert raised.value.code == "auth_token_missing"
        refusal: Any = raised.value
        assert refusal.END_REASON is None  # a refusal (exit 4), not a call outcome
        assert isinstance(refusal, ValueError)
    # the connection function refuses too (an entry that names a variable but no token passed),
    # and refuses the reverse (a token for an entry that names no variable)
    target = ws.WebSocketTarget(url="ws://127.0.0.1:9/ws-client", labels=entry)
    with pytest.raises(errors.TargetRefusedError) as raised2:
        asyncio.run(
            ws.open_websocket(
                target, protobuf(), token=None, open_timeout_s=1.0, close_timeout_s=1.0
            )
        )
    assert raised2.value.code == "auth_token_missing"
    token = ws.prepare_connection("pipecat-protobuf/1", entry, environ={"TRACER_TEST_TOKEN": "t"})
    plain = ws.WebSocketTarget(url="ws://127.0.0.1:9/ws-client", labels=labels())
    with pytest.raises(errors.TargetRefusedError) as raised3:
        asyncio.run(
            ws.open_websocket(
                plain, protobuf(), token=token, open_timeout_s=1.0, close_timeout_s=1.0
            )
        )
    assert raised3.value.code == "labels_inconsistent"


def test_open_websocket_has_no_default_timeouts() -> None:
    """The connect and close timeouts are the caller's numbers: no value is invented in code."""
    ws = mod("ws")
    target = ws.WebSocketTarget(url="ws://127.0.0.1:9/ws-client", labels=labels())
    with pytest.raises(TypeError):
        asyncio.run(ws.open_websocket(target, protobuf(), token=None, close_timeout_s=1.0))
    with pytest.raises(TypeError):
        asyncio.run(ws.open_websocket(target, protobuf(), token=None, open_timeout_s=1.0))
    with pytest.raises(TypeError):
        asyncio.run(ws.open_websocket(target, protobuf(), open_timeout_s=1.0, close_timeout_s=1.0))


def test_read_bearer_token_needs_an_explicit_environment() -> None:
    """No ambient ``os.environ`` default: the caller passes the mapping (step 6 of the order of
    operations reads the keys, and a test cannot leak the developer's machine into a run)."""
    ws = mod("ws")
    with pytest.raises(TypeError):
        ws.read_bearer_token(labels(auth_token_env="TRACER_TEST_TOKEN"))  # noqa: S106 - a name, not a secret
    with pytest.raises(TypeError):
        ws.prepare_connection("pipecat-protobuf/1", labels())


@pytest.mark.parametrize(
    "name", ["lower", "1LEADING", "HAS-DASH", "HAS SPACE", "", "A" * 65, "\u00c4BC", "X.Y"]
)
def test_connection_labels_refuse_a_bad_variable_name(name: str) -> None:
    with pytest.raises(mod("errors").TargetRefusedError) as raised:
        labels(auth_token_env=name)
    assert raised.value.code == "auth_env_name_invalid"


@pytest.mark.parametrize("name", ["A", "_", "TRACER_TOKEN_2", "A" * 64, "_X1"])
def test_connection_labels_accept_a_variable_name(name: str) -> None:
    assert labels(auth_token_env=name).auth_token_env == name


@pytest.mark.parametrize(
    "origin",
    [
        "HTTP://example.test",  # upper case: the schema pattern is lower case
        "http://Example.test",
        "http://example.test/",
        "http://example.test/path",
        "http://example.test?x=1",
        "http://user@example.test",
        "ftp://example.test",
        "example.test",
        "null",
        "",
        "http://",
        "http://example.test:123456",
        "http://exa mple.test",
        "http://caf\u00e9.test",
    ],
)
def test_connection_labels_refuse_a_bad_origin(origin: str) -> None:
    with pytest.raises(mod("errors").TargetRefusedError) as raised:
        labels(origin=origin)
    assert raised.value.code == "origin_invalid"


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test",
        "https://a.b-c.d:8443",
        "ws://127.0.0.1:7860",
        "wss://x",
        "http://localhost",
    ],
)
def test_connection_labels_accept_an_origin(origin: str) -> None:
    assert labels(origin=origin).origin == origin


def test_connection_labels_are_frozen_and_have_no_defaults() -> None:
    entry = labels()
    with pytest.raises(AttributeError):
        entry.origin = "http://x"
    with pytest.raises(TypeError):
        mod("ws").ConnectionLabels()
    with pytest.raises(TypeError):
        mod("ws").ConnectionLabels(auth_token_env=None, origin=None)


VALID_URLS = [
    ("ws://127.0.0.1:7860/ws-client", ("ws", "127.0.0.1", 7860, "/ws-client", True)),
    ("ws://localhost/ws", ("ws", "localhost", 80, "/ws", True)),
    ("ws://[::1]:8000/a/b", ("ws", "::1", 8000, "/a/b", True)),
    ("wss://agent.example.test/ws-client", ("wss", "agent.example.test", 443, "/ws-client", False)),
    ("wss://203.0.113.9:8443/x", ("wss", "203.0.113.9", 8443, "/x", False)),
    ("wss://127.0.0.1:1/x", ("wss", "127.0.0.1", 1, "/x", True)),
    ("ws://127.0.0.1:7860", ("ws", "127.0.0.1", 7860, "/", True)),  # an empty path is written /
    (
        "ws://127.0.0.1:7860/a%2Fb/%7e",
        ("ws", "127.0.0.1", 7860, "/a%2Fb/%7e", True),
    ),  # byte for byte
]


@pytest.mark.parametrize(("url", "expected"), VALID_URLS)
def test_target_url_is_parsed_and_the_path_is_kept_byte_for_byte(
    url: str, expected: tuple[Any, ...]
) -> None:
    parsed = mod("ws").parse_target_url(url, schemes=("ws", "wss"))
    assert (parsed.scheme, parsed.host, parsed.port, parsed.path, parsed.loopback) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ws://127.0.0.1:7860/ws?token=abc",  # a query (it often carries a token), BL-294
        "ws://127.0.0.1:7860/ws?",
        "ws://127.0.0.1:7860/ws#frag",
        "ws://127.0.0.1:7860/ws#",
        "ws://user:pw@127.0.0.1:7860/ws",  # user information
        "ws://user@127.0.0.1:7860/ws",
        "wss://caf\u00e9.example.test/ws",  # a host outside ASCII
        "ws://127.0.0.1:0/ws",
        "ws://127.0.0.1:65536/ws",
        "ws://127.0.0.1:abc/ws",
        "ws:///ws",
        "ws://",
        "http://127.0.0.1:7860/ws",  # not a WebSocket scheme
        "ftp://127.0.0.1/ws",
        "127.0.0.1:7860",
        "",
        "ws://127.0.0.1:7860/a b",
        "ws://127.0.0.1:7860/a\nb",
        "ws://127.0.0.1:7860/\u092e",
    ],
)
def test_target_url_with_query_userinfo_fragment_or_oddity_is_refused(url: str) -> None:
    with pytest.raises(mod("errors").TargetRefusedError) as raised:
        mod("ws").parse_target_url(url, schemes=("ws", "wss"))
    assert raised.value.code in ("target_invalid", "insecure_target")
    if raised.value.code == "insecure_target":
        pytest.fail(f"a malformed target must be target_invalid, not a TLS question: {url!r}")


@pytest.mark.parametrize(
    "host",
    [
        "192.0.2.1",
        "10.0.0.5",
        "172.16.0.1",
        "169.254.169.254",
        "example.com",
        "agent.example.test",
        "[2001:db8::1]",
        "127.0.0.2",  # loopback in fact, but the rule names exactly localhost, 127.0.0.1 and ::1
        "0.0.0.0",  # noqa: S104 - a refusal test: the guard must refuse this address
        "localhost.example.test",
        "127.0.0.1.example.test",
        "[::ffff:127.0.0.1]",
    ],
)
def test_plain_ws_is_refused_for_every_target_that_is_not_loopback(host: str) -> None:
    """ADR 0026 rule 16: plain ws:// only for localhost, 127.0.0.1 and ::1 as written; every other
    target needs TLS (wss). Refused before a socket, before a DNS query."""
    ws = mod("ws")
    with pytest.raises(mod("errors").TargetRefusedError) as raised:
        ws.parse_target_url(f"ws://{host}:8080/ws", schemes=("ws", "wss"))
    assert raised.value.code == "insecure_target"
    # the same host over TLS passes the URL rule (certificate verification is the handshake's)
    assert ws.parse_target_url(f"wss://{host}:8080/ws", schemes=("ws", "wss")).scheme == "wss"
    target = ws.WebSocketTarget(url=f"ws://{host}:8080/ws", labels=labels())
    with pytest.raises(mod("errors").TargetRefusedError) as raised2:
        asyncio.run(
            ws.open_websocket(
                target, protobuf(), token=None, open_timeout_s=1.0, close_timeout_s=1.0
            )
        )
    assert raised2.value.code == "insecure_target"


def test_transport_errors_hold_facts_only() -> None:
    errors = mod("errors")
    err = errors.SetupFailedError(code="handshake_rejected", http_status=401, close_code=None)
    assert (err.code, err.http_status, err.close_code) == ("handshake_rejected", 401, None)
    assert str(err).startswith("SetupFailedError: ")
    assert err.args == (str(err),)
    assert vars(err) == {"code": "handshake_rejected", "http_status": 401, "close_code": None}
    assert err.END_REASON == "setup_failed"
    assert errors.TransportError.END_REASON == "transport_error"
    assert errors.TargetRefusedError.END_REASON is None
    for kwargs in (
        {"code": "Bad Code"},
        {"code": "CamelCase"},
        {"code": "UPPER"},
        {"code": "1leading"},
        {"code": "has-dash"},
        {"code": ""},
        {"code": "x" * 65},
        {"code": "ok", "http_status": 99},
        {"code": "ok", "http_status": 600},
        {"code": "ok", "http_status": True},
        {"code": "ok", "close_code": -1},
        {"code": "ok", "close_code": 65536},
    ):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            errors.TransportError(**kwargs)
    with pytest.raises(TypeError):
        errors.TransportError("positional")

    def through_pickle(error: Any) -> Any:
        return pickle.loads(pickle.dumps(error))  # noqa: S301 - our own error object

    for clone in (copy.copy, copy.deepcopy, through_pickle):
        again = clone(err)
        assert type(again) is type(err)
        assert (again.code, again.http_status) == ("handshake_rejected", 401)
    assert issubclass(errors.TransportFactError, errors.EvalsimError)
    assert issubclass(errors.TargetRefusedError, ValueError)
    assert not issubclass(errors.SetupFailedError, ValueError)
    assert not issubclass(errors.TransportError, ValueError)
    # the code sets are closed lists and every code is a machine code
    for codes in (errors.REFUSAL_CODES, errors.SETUP_CODES, errors.TRANSPORT_CODES):
        assert list(codes) == sorted(set(codes))
    assert not set(errors.REFUSAL_CODES) & set(errors.SETUP_CODES)
