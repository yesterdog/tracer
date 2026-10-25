# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-07: the Sarvam HTTP client (contract docs/phase3/api-surface-batch-b.md section 5; ADR 0024
rules 2, 3, 8, 9 and 10).

The client is driven through an ``httpx.MockTransport`` (``ScriptedTransport``); sleeping and
timing are injected, so no test waits and none reaches a network. The error cases replay the
captures of 2026-10-23 (``err_*``).
"""

from __future__ import annotations

import gzip
import inspect
import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    Capture,
    Rig,
    capture_names,
    chain,
    client_mod,
    engine_mod,
    error_body,
    json_response,
    models,
    ok_body,
    small_plan,
    stt,
)

DATE = "Fri, 23 Oct 2026 13:08:26 GMT"


def _plan_chunk(index: int = 0) -> Any:
    return small_plan().chunks[index]


def _status(
    status: int,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    date: str | None = DATE,
) -> httpx.Response:
    return json_response(  # type: ignore[no-any-return,unused-ignore]
        error_body() if body is None else body, status=status, headers=headers, date=date
    )


def _ok(request_id: str = "r-ok") -> httpx.Response:
    return json_response(ok_body(request_id=request_id))  # type: ignore[no-any-return,unused-ignore]


# -------------------------------------------------------------------------------- retries


@pytest.mark.parametrize(
    ("label", "failures", "waits"),
    [
        ("429", [lambda: _status(429)], [1]),
        ("503", [lambda: _status(503)], [1]),
        ("429 then 503", [lambda: _status(429), lambda: _status(503)], [1, 2]),
        (
            "500 502 504",
            [lambda: _status(500), lambda: _status(502), lambda: _status(504)],
            [1, 2, 4],
        ),
        (
            "connect error then read timeout",
            [lambda: httpx.ConnectError("x"), lambda: httpx.ReadTimeout("x")],
            [1, 2],
        ),
        ("write error", [lambda: httpx.WriteError("x")], [1]),
        ("pool timeout", [lambda: httpx.PoolTimeout("x")], [1]),
        ("non-JSON 502", [lambda: _status(502, body=b"<html>bad gateway</html>")], [1]),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_sarvam_retries_429_and_503_then_succeeds(
    tmp_path: Path, label: str, failures: list[Any], waits: list[int]
) -> None:
    chunk = _plan_chunk(1)
    steps = [f() for f in failures] + [_ok("r-after-retry")]
    rig = Rig(tmp_path, steps)
    result = rig.client.send_chunk(chunk)
    assert result.response.request_id == "r-after-retry"
    assert result.chunk_index == 1
    assert result.attempts == len(failures) + 1
    assert rig.sleeps.waits == waits, "the waits are the fixed backoff, 1 then 2 then 4 s"
    assert len(rig.transport.requests) == len(failures) + 1
    # a retried attempt is a new request with the same chunk: same file part, same key header
    files = [r.content.count(chunk.wav) for r in rig.transport.requests]
    assert files == [1] * len(files)
    assert len({r.headers["api-subscription-key"] for r in rig.transport.requests}) == 1
    lines = rig.log_lines
    assert [(x["phase"], x["attempt"]) for x in lines] == [
        (phase, attempt) for attempt in range(1, len(failures) + 2) for phase in ("before", "after")
    ], "the call log keeps every attempt"


def test_sarvam_retry_policy_constants_are_the_adr_values() -> None:
    c = client_mod()
    assert c.MAX_ATTEMPTS == 4
    assert tuple(c.BACKOFF_SECONDS) == (1, 2, 4)
    assert c.RETRY_AFTER_MAX_SECONDS == 30
    assert (c.CONNECT_TIMEOUT_SECONDS, c.POOL_TIMEOUT_SECONDS) == (10, 10)
    assert (c.READ_TIMEOUT_SECONDS, c.WRITE_TIMEOUT_SECONDS) == (60, 60)
    parameters = inspect.signature(c.SarvamClient.__init__).parameters
    assert parameters["sleep"].default is time.sleep, "production sleeps; tests inject a fake"
    assert parameters["monotonic"].default is time.monotonic
    assert parameters["transport"].default is None, "production uses the default transport"


def test_sarvam_timeouts_are_10s_to_connect_and_pool_60s_to_read_and_write(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [_ok()])
    rig.client.send_chunk(_plan_chunk())
    timeout = rig.transport.requests[0].extensions["timeout"]
    assert timeout == {"connect": 10.0, "read": 60.0, "write": 60.0, "pool": 10.0}


FAILURES: dict[str, tuple[Callable[[], Any], str, int | None]] = {
    "429": (lambda: _status(429), "SttRateLimited", 429),
    "500": (lambda: _status(500), "SttServerError", 500),
    "503": (lambda: _status(503), "SttServerError", 503),
    "599": (lambda: _status(599), "SttServerError", 599),
    "connect_error": (lambda: httpx.ConnectError("x"), "SttTransportError", None),
    "read_timeout": (lambda: httpx.ReadTimeout("x"), "SttTransportError", None),
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_sarvam_gives_up_after_bounded_retries(tmp_path: Path, name: str) -> None:
    make, class_name, status = FAILURES[name]
    rig = Rig(tmp_path, [make() for _ in range(4)])
    chunk = _plan_chunk(2)
    with pytest.raises(getattr(stt(), class_name)) as raised:
        rig.client.send_chunk(chunk)
    error = raised.value
    assert type(error) is getattr(stt(), class_name)
    assert len(rig.transport.requests) == 4, "at most 4 attempts, and no fifth request"
    assert rig.sleeps.waits == [1, 2, 4], "no wait after the last attempt"
    assert error.attempts == 4
    assert error.chunk_index == 2
    assert error.status == status
    assert [x["attempt"] for x in rig.log_lines if x["phase"] == "after"] == [1, 2, 3, 4]


def test_sarvam_gives_up_after_bounded_retries_class_follows_the_last_failure(
    tmp_path: Path,
) -> None:
    steps: list[Any] = [_status(429), _status(503), httpx.ReadTimeout("x"), _status(500)]
    rig = Rig(tmp_path, steps)
    with pytest.raises(stt().SttServerError) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.status == 500
    assert raised.value.attempts == 4
    steps = [_status(500), _status(500), _status(500), httpx.ConnectError("x")]
    rig = Rig(tmp_path / "second", steps)
    with pytest.raises(stt().SttTransportError) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.kind == "connect_error"
    assert raised.value.status is None


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (httpx.ConnectTimeout("x"), "connect_timeout"),
        (httpx.ReadTimeout("x"), "read_timeout"),
        (httpx.WriteTimeout("x"), "write_timeout"),
        (httpx.PoolTimeout("x"), "pool_timeout"),
        (httpx.ConnectError("x"), "connect_error"),
        (httpx.ReadError("x"), "read_error"),
        (httpx.WriteError("x"), "write_error"),
        (httpx.RemoteProtocolError("x"), "transport_error"),
        (httpx.ProxyError("x"), "transport_error"),
    ],
)
def test_sarvam_transport_error_kinds_are_short_machine_keys(
    tmp_path: Path, error: Exception, kind: str
) -> None:
    rig = Rig(tmp_path, [error] * 4)
    with pytest.raises(stt().SttTransportError) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.kind == kind
    assert rig.sleeps.waits == [1, 2, 4]


def test_sarvam_any_other_http_client_error_is_a_typed_transport_error(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("bad", request=request)

    rig = Rig(tmp_path, [boom] * 4)
    with pytest.raises(stt().SttTransportError) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.kind == "transport_error"
    assert raised.value.attempts == 4


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 409, 413, 422, 451])
def test_sarvam_does_not_retry_400_403(tmp_path: Path, status: int) -> None:
    rig = Rig(tmp_path, [_status(status)])
    expected = stt().SttAuthError if status in (401, 403) else stt().SttRequestRejected
    with pytest.raises(expected) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert type(raised.value) is expected
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []
    assert raised.value.attempts == 1
    assert raised.value.status == status


def test_sarvam_does_not_retry_400_403_after_a_retry(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [_status(429), _status(400), _ok()])
    with pytest.raises(stt().SttRequestRejected) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.attempts == 2
    assert rig.sleeps.waits == [1]
    assert len(rig.transport.requests) == 2


def test_sarvam_auth_code_on_any_4xx_is_an_auth_error(tmp_path: Path) -> None:
    body = error_body("invalid_api_key_error")
    rig = Rig(tmp_path, [_status(400, body=body)])
    with pytest.raises(stt().SttAuthError) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.status == 400
    assert raised.value.code == "invalid_api_key_error"


# -------------------------------------------------------------------------------- taxonomy

EXPECTED_BY_CAPTURE = {
    "err_bad_key": "SttAuthError",
    "err_invalid_language_code": "SttRequestRejected",
    "err_underscore_language_code": "SttRequestRejected",
    "err_no_file": "SttRequestRejected",
    "err_not_audio": "SttRequestRejected",
    "err_empty_file": "SttRequestRejected",
}


def test_sarvam_error_taxonomy_matches_captures(tmp_path: Path) -> None:
    names = [n for n in capture_names("err_") if n != "err_bn_in_unrelated_clip"]
    assert sorted(names) == sorted(EXPECTED_BY_CAPTURE)
    s = stt()
    for number, name in enumerate(names):
        capture = Capture(name)
        document = json.loads(capture.body)["error"]
        rig = Rig(tmp_path / f"c{number}", [capture.response()])
        with pytest.raises(getattr(s, EXPECTED_BY_CAPTURE[name])) as raised:
            rig.client.send_chunk(_plan_chunk(1))
        error = raised.value
        assert type(error) is getattr(s, EXPECTED_BY_CAPTURE[name]), name
        assert error.status == capture.status, name
        assert error.code == document["code"], name
        assert error.request_id == document["request_id"], name
        assert error.chunk_index == 1, name
        assert error.attempts == 1, name
        text = str(error)
        for fact in (str(capture.status), document["code"], document["request_id"], "1"):
            assert fact in text, (name, fact)
        # the message of the provider can echo input: it is in no form of the error
        for form in (text, repr(error), repr(error.args), repr(vars(error))):
            assert document["message"] not in form, name
        assert len(rig.transport.requests) == 1, name


def test_sarvam_error_taxonomy_classes_and_bases() -> None:
    s = stt()
    assert issubclass(s.SttError, Exception)
    from tracer_evalcore.errors import EvalcoreError

    assert issubclass(s.SttError, EvalcoreError)
    for name in (
        "SttAuthError",
        "SttRequestRejected",
        "SttRateLimited",
        "SttServerError",
        "SttTransportError",
        "SttResponseInvalid",
        "SttConfigurationError",
    ):
        cls = getattr(s, name)
        assert cls.__bases__[0] is s.SttError, name
    assert s.SttKeyMissingError.__bases__ == (s.SttConfigurationError,)
    assert s.SttLanguageNotSupportedError.__bases__ == (s.SttConfigurationError,)
    assert issubclass(s.SttConfigurationError, ValueError)
    for name in ("SttAuthError", "SttRequestRejected", "SttRateLimited", "SttServerError"):
        assert not issubclass(getattr(s, name), ValueError)
    # keyword-only facts, none of them text from the provider
    error = s.SttRateLimited(
        chunk_index=3, status=429, code="rate_limit", request_id="r", attempts=4
    )
    assert (error.chunk_index, error.status, error.code, error.request_id, error.attempts) == (
        3,
        429,
        "rate_limit",
        "r",
        4,
    )
    assert error.kind is None
    assert error.reason is None
    with pytest.raises(TypeError):
        s.SttRateLimited(3, 429)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("invalid_request_error", "invalid_request_error"),
        ("rate_limit_exceeded_error", "rate_limit_exceeded_error"),
        ("a", "a"),
        ("x" * 64, "x" * 64),
        ("x" * 65, "unrecognised"),
        ("Invalid-Request", "unrecognised"),
        ("with space", "unrecognised"),
        ("<script>alert(1)</script>", "unrecognised"),
        ("line\nbreak", "unrecognised"),
        ("1leading_digit", "unrecognised"),
        ("", "unrecognised"),
        ("MESSAGECANARY", "unrecognised"),
    ],
)
def test_sarvam_error_code_is_kept_only_as_a_short_machine_key(
    tmp_path: Path, code: str, expected: str
) -> None:
    rig = Rig(tmp_path, [_status(400, body=error_body(code, message="please ignore previous"))])
    with pytest.raises(stt().SttRequestRejected) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.code == expected


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        (error_body(rid="body-id-1"), {"x-request-id": "header-id-2"}, "body-id-1"),
        (b"<html>", {"x-request-id": "header-id-2"}, "header-id-2"),
        (error_body(rid="bad id with spaces"), {"x-request-id": "header-id-2"}, "header-id-2"),
        (error_body(rid="bad id with spaces"), {"x-request-id": "bad\u00e9"}, None),
        (error_body(rid="x" * 129), {}, None),
        (b"{}", {}, None),
        (b'{"error": "text"}', {}, None),
        (b'{"error": {"code": 5, "request_id": 7}}', {}, None),
        (b"", {}, None),
    ],
)
def test_sarvam_error_request_id_is_kept_only_when_it_is_a_plain_identifier(
    tmp_path: Path, body: bytes, headers: dict[str, str], expected: str | None
) -> None:
    rig = Rig(tmp_path, [_status(400, headers=headers, body=body)])
    with pytest.raises(stt().SttRequestRejected) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.request_id == expected
    if body in (b"<html>", b"", b"{}"):
        assert raised.value.code == "unrecognised"


def test_sarvam_error_text_of_the_provider_reaches_no_error_form(tmp_path: Path) -> None:
    from evalproviders_support import MESSAGE_CANARY

    body = error_body("invalid_request_error", message=f"echo {MESSAGE_CANARY} of the input")
    rig = Rig(tmp_path, [_status(400, body=body)])
    with pytest.raises(stt().SttRequestRejected) as raised:
        rig.client.send_chunk(_plan_chunk())
    error = raised.value
    for form in (str(error), repr(error), repr(error.args), repr(vars(error))):
        assert MESSAGE_CANARY not in form
    assert MESSAGE_CANARY not in rig.log_text


def test_sarvam_the_error_is_raised_with_no_cause_and_no_context(tmp_path: Path) -> None:
    for number, step in enumerate(
        [httpx.ConnectError("boom"), _status(500), _status(400), _status(403)]
    ):
        status = getattr(step, "status_code", None)
        count = 4 if isinstance(step, httpx.ConnectError) or status == 500 else 1
        rig = Rig(tmp_path / f"e{number}", [step] * count)
        with pytest.raises(stt().SttError) as raised:
            rig.client.send_chunk(_plan_chunk())
        assert chain(raised.value) == [raised.value], "no httpx error, request or header is kept"
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


# --------------------------------------------------------------------- unexpected responses


@pytest.mark.parametrize(
    ("label", "response"),
    [
        ("100", httpx.Response(100)),
        ("204", httpx.Response(204)),
        ("206", httpx.Response(206, content=b"{}")),
        ("301", httpx.Response(301, headers={"location": "https://evil.example/x"})),
        ("302", httpx.Response(302, headers={"location": "https://api.sarvam.ai/other"})),
        ("303", httpx.Response(303, headers={"location": "/elsewhere"})),
        ("304", httpx.Response(304)),
        ("307", httpx.Response(307, headers={"location": "http://127.0.0.1:9/x"})),
        ("308", httpx.Response(308, headers={"location": "https://evil.example/x"})),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_sarvam_unexpected_status_is_response_invalid_and_never_followed(
    tmp_path: Path, label: str, response: httpx.Response
) -> None:
    rig = Rig(tmp_path, [response])
    with pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert type(raised.value) is stt().SttResponseInvalid
    assert raised.value.reason == "unexpected_status"
    assert raised.value.status == int(label)
    assert len(rig.transport.requests) == 1, "a redirect is not followed: one request only"
    assert rig.sleeps.waits == []
    assert {r.url.host for r in rig.transport.requests} == {"api.sarvam.ai"}


def test_sarvam_200_with_a_wrong_content_type_is_response_invalid(tmp_path: Path) -> None:
    for number, content_type in enumerate(["text/html", "text/plain", "application/xml", ""]):
        headers = {"content-type": content_type} if content_type else {}
        response = httpx.Response(200, headers=headers, content=json.dumps(ok_body()).encode())
        rig = Rig(tmp_path / f"t{number}", [response])
        with pytest.raises(stt().SttResponseInvalid) as raised:
            rig.client.send_chunk(_plan_chunk())
        assert raised.value.reason == "content_type", content_type
        assert len(rig.transport.requests) == 1


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "Application/JSON;charset=UTF-8"],
)
def test_sarvam_200_json_content_type_forms_are_accepted(tmp_path: Path, content_type: str) -> None:
    response = httpx.Response(
        200, headers={"content-type": content_type}, content=json.dumps(ok_body()).encode()
    )
    rig = Rig(tmp_path, [response])
    assert rig.client.send_chunk(_plan_chunk()).response.transcript == "namaskar"


@pytest.mark.parametrize(
    ("label", "body", "reason"),
    [
        ("not json", b"<html>", "not_json"),
        ("an array", b"[]", "not_object"),
        (
            "transcript is a number",
            json.dumps({**ok_body(), "transcript": 5}).encode(),
            "transcript_invalid",
        ),
        ("request id missing", b'{"transcript":"x","language_code":null}', "request_id_invalid"),
    ],
)
def test_sarvam_invalid_body_is_response_invalid_and_not_retried(
    tmp_path: Path, label: str, body: bytes, reason: str
) -> None:
    rig = Rig(tmp_path, [json_response(body)])
    with pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_plan_chunk(1))
    error = raised.value
    assert error.reason == reason
    assert error.status == 200
    assert error.chunk_index == 1
    assert error.attempts == 1
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []
    assert str(error).count("{") == 0, "the body is not in the message"


def _padded_body(total: int) -> bytes:
    shell = b'{"request_id":"r","transcript":"","language_code":null}'
    pad = total - len(shell)
    assert pad >= 0
    return shell.replace(b'"transcript":""', b'"transcript":"' + b"x" * pad + b'"')


def test_sarvam_response_body_cap_is_exactly_one_mebibyte(tmp_path: Path) -> None:
    cap = models().MAX_RESPONSE_BYTES
    assert cap == 1_048_576
    exact = _padded_body(cap)
    assert len(exact) == cap
    rig = Rig(tmp_path / "exact", [json_response(exact)])
    assert len(rig.client.send_chunk(_plan_chunk()).response.transcript) > cap - 100
    over = _padded_body(cap + 1)
    rig = Rig(tmp_path / "over", [json_response(over)])
    with pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.reason == "body_too_large"
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []


class _Endless(httpx.SyncByteStream):
    """A body that never ends; counts the bytes handed out."""

    def __init__(self) -> None:
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        while True:
            self.sent += 65536
            yield b" " * 65536


def test_sarvam_response_reading_stops_at_the_cap(tmp_path: Path) -> None:
    stream = _Endless()
    headers = {"content-type": "application/json"}
    rig = Rig(tmp_path, [lambda request: httpx.Response(200, headers=headers, stream=stream)])
    with pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.reason == "body_too_large"
    assert stream.sent <= models().MAX_RESPONSE_BYTES + 2 * 65536, "reading stopped at the cap"


def test_sarvam_oversize_error_body_is_not_read_to_the_end(tmp_path: Path) -> None:
    stream = _Endless()
    headers = {"content-type": "application/json"}
    rig = Rig(tmp_path, [lambda request: httpx.Response(400, headers=headers, stream=stream)])
    with pytest.raises(stt().SttRequestRejected) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.code == "unrecognised"
    assert stream.sent <= models().MAX_RESPONSE_BYTES + 2 * 65536


def test_sarvam_a_compressed_body_is_capped_after_decoding(tmp_path: Path) -> None:
    bomb = gzip.compress(b" " * (models().MAX_RESPONSE_BYTES * 8))
    assert len(bomb) < 50_000
    headers = {"content-type": "application/json", "content-encoding": "gzip"}
    rig = Rig(
        tmp_path, [lambda r: httpx.Response(200, headers=headers, stream=httpx.ByteStream(bomb))]
    )
    with pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.reason == "body_too_large"


def test_sarvam_a_corrupt_compressed_body_is_response_invalid(tmp_path: Path) -> None:
    headers = {"content-type": "application/json", "content-encoding": "gzip"}
    garbage = httpx.ByteStream(b"this is not gzip data at all")
    rig = Rig(tmp_path, [lambda r: httpx.Response(200, headers=headers, stream=garbage)])
    with pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.reason == "body_undecodable"
    assert chain(raised.value) == [raised.value]


# ---------------------------------------------------------------------------- Retry-After


def _limited(status: int, retry_after: str | None, date: str | None = DATE) -> httpx.Response:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return _status(status, headers=headers, date=date)


@pytest.mark.parametrize(
    ("steps", "waits"),
    [
        ([("429", "7")], [7]),
        ([("429", "29")], [29]),
        ([("429", "30")], [30]),
        ([("429", "0")], [1]),
        ([("429", "1")], [1]),
        ([("503", "5")], [5]),
        ([("503", "30")], [30]),
        ([("429", None), ("429", "3")], [1, 3]),
        ([("429", None), ("429", "1")], [1, 2]),
        ([("429", None), ("429", None), ("429", "3")], [1, 2, 4]),
        ([("429", None), ("429", None), ("429", "9")], [1, 2, 9]),
        ([("429", "10"), ("429", "20"), ("503", "30")], [10, 20, 30]),
        ([("500", "5")], [1]),
        ([("502", "99999")], [1]),
        ([("504", "5")], [1]),
    ],
)
def test_sarvam_honours_bounded_retry_after(
    tmp_path: Path, steps: list[tuple[str, str | None]], waits: list[int]
) -> None:
    script = [_limited(int(status), value) for status, value in steps] + [_ok()]
    rig = Rig(tmp_path, script)
    assert rig.client.send_chunk(_plan_chunk()).attempts == len(steps) + 1
    assert rig.sleeps.waits == waits, "the larger of the backoff and the header; never less"


@pytest.mark.parametrize(
    ("retry_after", "date", "wait"),
    [
        ("Fri, 23 Oct 2026 13:08:36 GMT", DATE, 10),
        ("Fri, 23 Oct 2026 13:08:56 GMT", DATE, 30),
        ("Fri, 23 Oct 2026 13:08:26 GMT", DATE, 1),
        ("Fri, 23 Oct 2026 13:08:20 GMT", DATE, 1),
        ("Fri, 23 Oct 2026 13:08:27 GMT", DATE, 1),
        ("Fri, 23 Oct 2026 13:08:28 GMT", DATE, 2),
        ("Fri, 23 Oct 2026 13:08:36 GMT", None, 1),
        ("Fri, 23 Oct 2026 13:08:36 GMT", "yesterday", 1),
    ],
)
def test_sarvam_honours_bounded_retry_after_http_date(
    tmp_path: Path, retry_after: str, date: str | None, wait: int
) -> None:
    rig = Rig(tmp_path, [_limited(429, retry_after, date=date), _ok()])
    rig.client.send_chunk(_plan_chunk())
    assert rig.sleeps.waits == [wait], "measured against the response Date, never the local clock"


@pytest.mark.parametrize(
    "value",
    [
        "soon",
        "1.5",
        "-5",
        "",
        "+5",
        "5s",
        "\u0663",
        "0x10",
        "1e1",
        "5 6",
        "Tomorrow",
        "NaN",
        "\u0661\u0662",
    ],
)
def test_sarvam_honours_bounded_retry_after_ignores_an_unparseable_value(
    tmp_path: Path, value: str
) -> None:
    rig = Rig(tmp_path, [_limited(429, value), _ok()])
    rig.client.send_chunk(_plan_chunk())
    assert rig.sleeps.waits == [1]


@pytest.mark.parametrize(
    ("status", "value", "date"),
    [
        (429, "31", DATE),
        (429, "3600", DATE),
        (429, "99999999999999999999999", DATE),
        (503, "31", DATE),
        (429, "Fri, 23 Oct 2026 13:08:58 GMT", DATE),
        (503, "Sat, 24 Oct 2026 13:08:26 GMT", DATE),
    ],
)
def test_sarvam_honours_bounded_retry_after_above_the_bound_ends_the_attempts(
    tmp_path: Path, status: int, value: str, date: str
) -> None:
    rig = Rig(tmp_path, [_limited(status, value, date=date)])
    with pytest.raises(stt().SttRateLimited) as raised:
        rig.client.send_chunk(_plan_chunk(2))
    assert type(raised.value) is stt().SttRateLimited
    assert raised.value.status == status
    assert raised.value.attempts == 1
    assert raised.value.chunk_index == 2
    assert len(rig.transport.requests) == 1, "no wait longer than the bound, no second request"
    assert rig.sleeps.waits == []


def test_sarvam_honours_bounded_retry_after_above_the_bound_on_a_later_attempt(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [_limited(429, None), _limited(503, "31")])
    with pytest.raises(stt().SttRateLimited) as raised:
        rig.client.send_chunk(_plan_chunk())
    assert raised.value.attempts == 2
    assert raised.value.status == 503
    assert rig.sleeps.waits == [1]


# ----------------------------------------------------------------------------- endpoint


def test_sarvam_base_url_not_overridable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SARVAM_BASE_URL",
        "SARVAM_API_URL",
        "SARVAM_API_BASE",
        "SARVAM_ENDPOINT",
        "TRACER_SARVAM_URL",
        "API_BASE_URL",
        "BASE_URL",
    ):
        monkeypatch.setenv(name, "http://evil.example:8080")
    rig = Rig(tmp_path, [_ok()])
    rig.client.send_chunk(_plan_chunk())
    (request,) = rig.transport.requests
    assert str(request.url) == "https://api.sarvam.ai/speech-to-text"
    assert (request.url.scheme, request.url.host, request.url.port) == (
        "https",
        "api.sarvam.ai",
        None,
    )
    assert request.url.path == "/speech-to-text"
    assert request.url.query == b""
    # no argument of any public entry point names a URL, a host, a port or TLS verification
    forbidden = {
        "url",
        "base_url",
        "endpoint",
        "host",
        "port",
        "verify",
        "cert",
        "proxy",
        "proxies",
    }
    forbidden |= {
        "follow_redirects",
        "http2",
        "trust_env",
        "headers",
        "path",
        "scheme",
        "event_hooks",
    }
    for target in (
        client_mod().SarvamClient.__init__,
        client_mod().SarvamClient.send_chunk,
        engine_mod().SarvamStt.__init__,
        engine_mod().SarvamStt.transcribe,
        engine_mod().create_sarvam_stt,
        models().SarvamRequest,
    ):
        names = set(inspect.signature(target).parameters)
        assert not names & forbidden, (target, names & forbidden)
    assert models().API_BASE_URL == "https://api.sarvam.ai"
    assert models().STT_URL == "https://api.sarvam.ai/speech-to-text"
    assert "sarvam.ai" not in str(inspect.signature(client_mod().SarvamClient.__init__))


def test_sarvam_base_url_not_overridable_source_reads_no_environment_for_it() -> None:
    import ast
    import re

    from evalproviders_support import SRC

    offences = []
    for path in sorted(SRC.rglob("*.py")):
        name = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            reads = {"environ", "environb", "getenv", "getenvb", "putenv"}
            is_os_read = (
                isinstance(node, ast.Attribute)
                and node.attr in reads
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            )
            if is_os_read and name != "secrets.py":
                offences.append(f"{name}:{getattr(node, 'lineno', 0)}: os read")
            is_from_os = (
                isinstance(node, ast.ImportFrom)
                and node.module == "os"
                and bool({a.name for a in node.names} & reads)
            )
            if is_from_os and name != "secrets.py":
                offences.append(f"{name}:{getattr(node, 'lineno', 0)}: from os import")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.fullmatch(r"https?://\S+", node.value)
            ):
                allowed = {"https://api.sarvam.ai", "https://api.sarvam.ai/speech-to-text"}
                if name != "sarvam/models.py" or node.value not in allowed:
                    offences.append(f"{name}:{node.lineno}: url literal")
    assert not offences, offences


# ------------------------------------------------------------------------ Date header


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        (DATE, datetime(2026, 10, 23, 13, 8, 26, tzinfo=UTC)),
        ("Sat, 24 Oct 2026 00:00:00 GMT", datetime(2026, 10, 24, 0, 0, 0, tzinfo=UTC)),
        (None, None),
        ("", None),
        ("yesterday", None),
        ("Fri, 23 Oct 2026 25:61:61 GMT", None),
        ("Fri, 23 Oct 2026 13:08:26 -0000", None),
        ("Fri, 23 Oct 2026 18:38:26 +0530", datetime(2026, 10, 23, 13, 8, 26, tzinfo=UTC)),
        ("2026-10-23T13:08:26Z", None),
    ],
)
def test_sarvam_responded_at_is_the_http_date_header_or_nothing(
    tmp_path: Path, date: str | None, expected: datetime | None
) -> None:
    rig = Rig(tmp_path, [json_response(ok_body(), date=date)])
    result = rig.client.send_chunk(_plan_chunk())
    assert result.responded_at == expected
    if expected is not None:
        assert result.responded_at is not None
        assert result.responded_at.utcoffset().total_seconds() == 0
