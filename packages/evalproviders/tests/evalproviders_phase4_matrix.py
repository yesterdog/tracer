# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The retry and error taxonomy of ADR 0024 rules 8 to 10, restated as scenarios that run against
the TTS adapter and the LLM adapter alike (ADR 0024 amendment A1: "the rules apply to them as they
apply to STT"). Each scenario mirrors a Phase 3 STT test of ``test_sarvam_client.py``; the facts of
an error are ``index`` (the request's position), ``status``, ``kind``, ``code``, ``request_id``,
``attempts`` and ``reason``.

A scenario is ``f(kit, tmp_path)``; the test modules pick them by name. The failures of a request
are the ``failure`` of its outcome (never raised); a ``BaseException`` of the transport is raised
unchanged.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    DATE,
    Kit,
    client_mod,
    models_mod,
)
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    MESSAGE_CANARY,
    chain,
    error_body,
    json_response,
)

Scenario = Callable[[Kit, Path], None]
SCENARIOS: dict[str, Scenario] = {}
INDEX = 5  # the position of the request in every scenario: it must come back in every error


def status(
    code: int,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    date: str | None = DATE,
) -> httpx.Response:
    response: httpx.Response = json_response(
        error_body() if body is None else body, status=code, headers=headers, date=date
    )
    return response


def _failed(kit: Kit, rig: Any, outcome: Any) -> Any:
    """The failure of an outcome that must hold nothing else (no result, no usage)."""
    assert outcome.failure is not None
    if kit.name == "tts":
        assert outcome.audio is None
    else:
        assert outcome.text is None
    assert outcome.usage == ()
    return outcome.failure


def _log_pairs(rig: Any) -> list[tuple[str, int]]:
    return [(x["event"], x["attempt"]) for x in rig.log_lines]


# ------------------------------------------------------------------ retried, then answered

RETRIES: dict[str, tuple[list[Callable[[], Any]], list[float]]] = {
    "429": ([lambda: status(429)], [1]),
    "503": ([lambda: status(503)], [1]),
    "429_then_503": ([lambda: status(429), lambda: status(503)], [1, 2]),
    "500_502_504": ([lambda: status(500), lambda: status(502), lambda: status(504)], [1, 2, 4]),
    "connect_error_then_read_timeout": (
        [lambda: httpx.ConnectError("x"), lambda: httpx.ReadTimeout("x")],
        [1, 2],
    ),
    "write_error": ([lambda: httpx.WriteError("x")], [1]),
    "pool_timeout": ([lambda: httpx.PoolTimeout("x")], [1]),
    "non_json_502": ([lambda: status(502, body=b"<html>bad gateway</html>")], [1]),
}


def _register_retry(label: str, make: list[Callable[[], Any]], waits: list[float]) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        steps = [m() for m in make] + [kit.ok(1)]
        rig = kit.rig(tmp_path, steps)
        outcome = kit.call(rig, INDEX)
        assert outcome.failure is None, label
        assert len(rig.transport.requests) == len(make) + 1
        assert rig.sleeps.waits == waits, "the waits are the fixed backoff, 1 then 2 then 4 s"
        assert len({r.content for r in rig.transport.requests}) == 1, "the same request again"
        assert len({r.headers["api-subscription-key"] for r in rig.transport.requests}) == 1
        assert _log_pairs(rig) == [
            (event, attempt) for attempt in range(1, len(make) + 2) for event in ("before", "after")
        ], "the call log keeps every attempt"
        assert len(outcome.usage) == 1, "one usage record for the answered request, not per attempt"

    SCENARIOS[f"retried_then_answered[{label}]"] = run


for _label, (_make, _waits) in RETRIES.items():
    _register_retry(_label, _make, _waits)


# ------------------------------------------------------------------ bounded attempts

GIVE_UP: dict[str, tuple[Callable[[], Any], str, int | None]] = {
    "429": (lambda: status(429), "RateLimited", 429),
    "500": (lambda: status(500), "ServerError", 500),
    "503": (lambda: status(503), "ServerError", 503),
    "599": (lambda: status(599), "ServerError", 599),
    "connect_error": (lambda: httpx.ConnectError("x"), "TransportError", None),
    "read_timeout": (lambda: httpx.ReadTimeout("x"), "TransportError", None),
}


def _register_give_up(
    label: str, make: Callable[[], Any], class_suffix: str, code: int | None
) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [make() for _ in range(4)])
        outcome = kit.call(rig, INDEX)
        error = _failed(kit, rig, outcome)
        assert type(error) is kit.error(class_suffix)
        assert len(rig.transport.requests) == 4, "at most 4 attempts, and no fifth request"
        assert rig.sleeps.waits == [1, 2, 4], "no wait after the last attempt"
        assert error.attempts == 4
        assert error.index == INDEX
        assert error.status == code
        assert [x["attempt"] for x in rig.log_lines if x["event"] == "after"] == [1, 2, 3, 4]

    SCENARIOS[f"gives_up_after_four_attempts[{label}]"] = run


for _label, (_make1, _suffix, _code) in GIVE_UP.items():
    _register_give_up(_label, _make1, _suffix, _code)


def _class_follows_the_last_failure(kit: Kit, tmp_path: Path) -> None:
    steps: list[Any] = [status(429), status(503), httpx.ReadTimeout("x"), status(500)]
    rig = kit.rig(tmp_path / "a", steps)
    error = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(error) is kit.error("ServerError")
    assert (error.status, error.attempts) == (500, 4)
    steps = [status(500), status(500), status(500), httpx.ConnectError("x")]
    rig = kit.rig(tmp_path / "b", steps)
    error = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(error) is kit.error("TransportError")
    assert error.kind == "connect_error"
    assert error.status is None


SCENARIOS["class_follows_the_last_failure"] = _class_follows_the_last_failure

KINDS = [
    (httpx.ConnectTimeout("x"), "connect_timeout"),
    (httpx.ReadTimeout("x"), "read_timeout"),
    (httpx.WriteTimeout("x"), "write_timeout"),
    (httpx.PoolTimeout("x"), "pool_timeout"),
    (httpx.ConnectError("x"), "connect_error"),
    (httpx.ReadError("x"), "read_error"),
    (httpx.WriteError("x"), "write_error"),
    (httpx.RemoteProtocolError("x"), "transport_error"),
    (httpx.ProxyError("x"), "transport_error"),
]


def _register_kind(error: Exception, kind: str) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [error] * 4)
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert type(failure) is kit.error("TransportError")
        assert failure.kind == kind
        assert rig.sleeps.waits == [1, 2, 4]

    SCENARIOS[f"transport_kind[{type(error).__name__}->{kind}]"] = run


for _error, _kind in KINDS:
    _register_kind(_error, _kind)


def _any_other_client_error(kit: Kit, tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("bad", request=request)

    rig = kit.rig(tmp_path, [boom] * 4)
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("TransportError")
    assert (failure.kind, failure.attempts) == ("transport_error", 4)


SCENARIOS["any_other_http_client_error_is_a_transport_error"] = _any_other_client_error


# ------------------------------------------------------------------ never retried


def _register_no_retry(code: int) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [status(code)])
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        expected = kit.error("AuthError" if code in (401, 403) else "RequestRejected")
        assert type(failure) is expected
        assert len(rig.transport.requests) == 1
        assert rig.sleeps.waits == []
        assert (failure.attempts, failure.status, failure.index) == (1, code, INDEX)

    SCENARIOS[f"not_retried[{code}]"] = run


for _code in (400, 401, 403, 404, 408, 409, 413, 422, 451):
    _register_no_retry(_code)


def _no_retry_after_a_retry(kit: Kit, tmp_path: Path) -> None:
    rig = kit.rig(tmp_path, [status(429), status(400), kit.ok()])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("RequestRejected")
    assert failure.attempts == 2
    assert rig.sleeps.waits == [1]
    assert len(rig.transport.requests) == 2


SCENARIOS["not_retried_after_a_retry"] = _no_retry_after_a_retry


def _auth_code_on_any_4xx(kit: Kit, tmp_path: Path) -> None:
    rig = kit.rig(tmp_path, [status(400, body=error_body("invalid_api_key_error"))])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("AuthError")
    assert (failure.status, failure.code) == (400, "invalid_api_key_error")


SCENARIOS["auth_code_on_any_4xx_is_an_auth_error"] = _auth_code_on_any_4xx


# ------------------------------------------------------------------ what an error holds


def _facts_from_the_error_body(kit: Kit, tmp_path: Path) -> None:
    body = error_body("invalid_request_error", message="echo of the input", rid="srv-req-9")
    rig = kit.rig(tmp_path, [status(400, body=body)])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("RequestRejected")
    assert (failure.status, failure.code, failure.request_id) == (
        400,
        "invalid_request_error",
        "srv-req-9",
    )
    assert (failure.index, failure.attempts) == (INDEX, 1)
    text = str(failure)
    for fact in ("400", "invalid_request_error", "srv-req-9", str(INDEX)):
        assert fact in text, fact
    for form in (text, repr(failure), repr(failure.args), repr(vars(failure))):
        assert "echo of the input" not in form


SCENARIOS["facts_come_from_the_error_body_and_its_message_from_nowhere"] = (
    _facts_from_the_error_body
)


def _register_code(raw: Any, kept: str) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        body = json.dumps({"error": {"message": "m", "code": raw, "request_id": "r-1"}}).encode()
        rig = kit.rig(tmp_path, [status(400, body=body)])
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert failure.code == kept

    SCENARIOS[f"error_code_kept_only_as_a_short_machine_key[{raw!r}]"] = run


for _raw, _kept in [
    ("rate_limit_exceeded_error", "rate_limit_exceeded_error"),
    ("Bad Code", "unrecognised"),
    ("UPPER", "unrecognised"),
    ("x" * 65, "unrecognised"),
    ("a\nb", "unrecognised"),
    (5, "unrecognised"),
    (None, "unrecognised"),
]:
    _register_code(_raw, _kept)


def _register_request_id(raw: Any, kept: str | None) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        body = json.dumps({"error": {"message": "m", "code": "c", "request_id": raw}}).encode()
        rig = kit.rig(tmp_path, [status(400, body=body)])
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert failure.request_id == kept

    SCENARIOS[f"error_request_id_kept_only_as_a_plain_identifier[{raw!r}]"] = run


for _raw2, _kept2 in [
    (
        "20261023_9e4bb02d-dcc0-4fbc-85ed-fca178c17115",
        "20261023_9e4bb02d-dcc0-4fbc-85ed-fca178c17115",
    ),
    ("a b", None),
    ("x\ny", None),
    ("", None),
    ("x" * 129, None),
    (7, None),
]:
    _register_request_id(_raw2, _kept2)


def _error_request_id_falls_back_to_the_header(kit: Kit, tmp_path: Path) -> None:
    no_id = json.dumps({"error": {"message": "m", "code": "c"}}).encode()
    rig = kit.rig(tmp_path / "a", [status(400, headers={"x-request-id": "hdr-1"}, body=no_id)])
    assert _failed(kit, rig, kit.call(rig, INDEX)).request_id == "hdr-1"
    both = error_body("c", message="m", rid="body-1")
    rig = kit.rig(tmp_path / "b", [status(400, headers={"x-request-id": "hdr-2"}, body=both)])
    assert _failed(kit, rig, kit.call(rig, INDEX)).request_id == "body-1", "the body wins"
    rig = kit.rig(tmp_path / "c", [status(400, headers={"x-request-id": "not plain"}, body=no_id)])
    assert _failed(kit, rig, kit.call(rig, INDEX)).request_id is None


SCENARIOS["error_request_id_falls_back_to_the_x_request_id_header"] = (
    _error_request_id_falls_back_to_the_header
)


def _provider_text_reaches_no_form(kit: Kit, tmp_path: Path) -> None:
    body = error_body("invalid_request_error", message=f"echo {MESSAGE_CANARY} of the input")
    rig = kit.rig(tmp_path, [status(400, body=body)])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    for form in (str(failure), repr(failure), repr(failure.args), repr(vars(failure))):
        assert MESSAGE_CANARY not in form
    assert MESSAGE_CANARY not in rig.log_text


SCENARIOS["provider_message_reaches_no_form_of_the_error_and_no_log"] = (
    _provider_text_reaches_no_form
)


def _no_cause_no_context(kit: Kit, tmp_path: Path) -> None:
    for number, step in enumerate(
        [httpx.ConnectError("boom"), status(500), status(400), status(403)]
    ):
        code = getattr(step, "status_code", None)
        count = 4 if isinstance(step, httpx.ConnectError) or code == 500 else 1
        rig = kit.rig(tmp_path / f"e{number}", [step] * count)
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert chain(failure) == [failure], "no httpx error, request or header is kept"
        assert failure.__cause__ is None
        assert failure.__context__ is None


SCENARIOS["error_has_no_cause_and_no_context"] = _no_cause_no_context


def _class_tree(kit: Kit, tmp_path: Path) -> None:
    from tracer_evalcore.errors import EvalcoreError

    base = kit.error("Error")
    assert issubclass(base, EvalcoreError)
    for suffix in (
        "AuthError",
        "RequestRejected",
        "RateLimited",
        "ServerError",
        "TransportError",
        "ResponseInvalid",
        "CapReachedError",
    ):
        cls = kit.error(suffix)
        assert cls.__bases__ == (base,), suffix
        assert not issubclass(cls, ValueError), suffix
    assert issubclass(kit.error("ConfigurationError"), ValueError)
    assert issubclass(kit.error("KeyMissingError"), kit.error("ConfigurationError"))


SCENARIOS["class_tree"] = _class_tree


# ------------------------------------------------------------------ unexpected responses

UNEXPECTED = [
    ("100", httpx.Response(100)),
    ("204", httpx.Response(204)),
    ("206", httpx.Response(206, content=b"{}")),
    ("301", httpx.Response(301, headers={"location": "https://evil.example/x"})),
    ("302", httpx.Response(302, headers={"location": "https://api.sarvam.ai/other"})),
    ("303", httpx.Response(303, headers={"location": "/elsewhere"})),
    ("304", httpx.Response(304)),
    ("307", httpx.Response(307, headers={"location": "http://127.0.0.1:9/x"})),
    ("308", httpx.Response(308, headers={"location": "https://evil.example/x"})),
]


def _register_unexpected(label: str, response: httpx.Response) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [response])
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert type(failure) is kit.error("ResponseInvalid")
        assert failure.reason == "unexpected_status"
        assert failure.status == int(label)
        assert len(rig.transport.requests) == 1, "a redirect is not followed: one request only"
        assert rig.sleeps.waits == []
        assert {r.url.host for r in rig.transport.requests} == {"api.sarvam.ai"}

    SCENARIOS[f"unexpected_status_never_followed[{label}]"] = run


for _label3, _response in UNEXPECTED:
    _register_unexpected(_label3, _response)


def _wrong_content_type(kit: Kit, tmp_path: Path) -> None:
    for number, content_type in enumerate(["text/html", "text/plain", "application/xml", ""]):
        headers = {"content-type": content_type} if content_type else {}
        response = httpx.Response(200, headers=headers, content=b"{}")
        rig = kit.rig(tmp_path / f"t{number}", [response])
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert type(failure) is kit.error("ResponseInvalid")
        assert (failure.reason, failure.status) == ("content_type", 200), content_type
        assert len(rig.transport.requests) == 1


SCENARIOS["200_with_a_wrong_content_type"] = _wrong_content_type


def _json_content_type_forms(kit: Kit, tmp_path: Path) -> None:
    for number, content_type in enumerate(
        ["application/json", "application/json; charset=utf-8", "Application/JSON;charset=UTF-8"]
    ):
        good = kit.ok(number)
        response = httpx.Response(200, headers={"content-type": content_type}, content=good.content)
        rig = kit.rig(tmp_path / f"c{number}", [response])
        assert kit.call(rig, INDEX).failure is None, content_type


SCENARIOS["200_json_content_type_forms_are_accepted"] = _json_content_type_forms


# ------------------------------------------------------------------ size caps


class Endless(httpx.SyncByteStream):
    """A body that never ends; counts the bytes handed out."""

    def __init__(self) -> None:
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        while True:
            self.sent += 65536
            yield b" " * 65536


def _cap_for(kit: Kit) -> int:
    models = models_mod()
    if kit.name == "llm":
        return int(models.MAX_RESPONSE_BYTES)
    return int(models.tts_max_response_bytes(16000, "linear16"))


def _reading_stops_at_the_cap(kit: Kit, tmp_path: Path) -> None:
    stream = Endless()
    headers = {"content-type": "application/json"}
    rig = kit.rig(tmp_path, [lambda request: httpx.Response(200, headers=headers, stream=stream)])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("ResponseInvalid")
    assert failure.reason == "body_too_large"
    assert stream.sent <= _cap_for(kit) + 2 * 65536, "reading stopped at the cap"
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []


SCENARIOS["response_reading_stops_at_the_cap"] = _reading_stops_at_the_cap


def _oversize_error_body(kit: Kit, tmp_path: Path) -> None:
    stream = Endless()
    headers = {"content-type": "application/json"}
    rig = kit.rig(tmp_path, [lambda request: httpx.Response(400, headers=headers, stream=stream)])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("RequestRejected")
    assert failure.code == "unrecognised"
    assert stream.sent <= _cap_for(kit) + 2 * 65536


SCENARIOS["oversize_error_body_is_not_read_to_the_end"] = _oversize_error_body


def _compressed_body_capped_after_decoding(kit: Kit, tmp_path: Path) -> None:
    bomb = gzip.compress(b" " * (_cap_for(kit) * 3))
    assert len(bomb) < 200_000
    headers = {"content-type": "application/json", "content-encoding": "gzip"}
    rig = kit.rig(
        tmp_path, [lambda r: httpx.Response(200, headers=headers, stream=httpx.ByteStream(bomb))]
    )
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("ResponseInvalid")
    assert failure.reason == "body_too_large"


SCENARIOS["compressed_body_is_capped_after_decoding"] = _compressed_body_capped_after_decoding


def _corrupt_compressed_body(kit: Kit, tmp_path: Path) -> None:
    headers = {"content-type": "application/json", "content-encoding": "gzip"}
    garbage = httpx.ByteStream(b"this is not gzip data at all")
    rig = kit.rig(tmp_path, [lambda r: httpx.Response(200, headers=headers, stream=garbage)])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("ResponseInvalid")
    assert failure.reason == "body_undecodable"
    assert chain(failure) == [failure]


SCENARIOS["corrupt_compressed_body_is_response_invalid"] = _corrupt_compressed_body


# ------------------------------------------------------------------ Retry-After


def limited(code: int, retry_after: str | None, date: str | None = DATE) -> httpx.Response:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return status(code, headers=headers, date=date)


BOUNDED: list[tuple[list[tuple[str, str | None]], list[float]]] = [
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
]


def _register_bounded(number: int, steps: list[tuple[str, str | None]], waits: list[float]) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        script = [limited(int(code), value) for code, value in steps] + [kit.ok()]
        rig = kit.rig(tmp_path, script)
        assert kit.call(rig, INDEX).failure is None
        assert rig.sleeps.waits == waits, "the larger of the backoff and the header; never less"

    SCENARIOS[f"retry_after_is_honoured[{number}]"] = run


for _n, (_steps, _waits2) in enumerate(BOUNDED):
    _register_bounded(_n, _steps, _waits2)

HTTP_DATES = [
    ("Fri, 23 Oct 2026 13:08:36 GMT", DATE, 10),
    ("Fri, 23 Oct 2026 13:08:56 GMT", DATE, 30),
    ("Fri, 23 Oct 2026 13:08:26 GMT", DATE, 1),
    ("Fri, 23 Oct 2026 13:08:20 GMT", DATE, 1),
    ("Fri, 23 Oct 2026 13:08:28 GMT", DATE, 2),
    ("Fri, 23 Oct 2026 13:08:36 GMT", None, 1),
    ("Fri, 23 Oct 2026 13:08:36 GMT", "yesterday", 1),
]


def _register_date(number: int, retry_after: str, date: str | None, wait: float) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [limited(429, retry_after, date=date), kit.ok()])
        assert kit.call(rig, INDEX).failure is None
        assert rig.sleeps.waits == [wait], "measured against the response Date, not the clock"

    SCENARIOS[f"retry_after_http_date[{number}]"] = run


for _n2, (_ra, _dt, _wait) in enumerate(HTTP_DATES):
    _register_date(_n2, _ra, _dt, _wait)


def _register_unparseable(value: str) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [limited(429, value), kit.ok()])
        assert kit.call(rig, INDEX).failure is None
        assert rig.sleeps.waits == [1]

    SCENARIOS[f"retry_after_unparseable_is_ignored[{value!r}]"] = run


for _value in ["soon", "1.5", "-5", "", "+5", "5s", "\u0663", "0x10", "1e1", "5 6", "NaN"]:
    _register_unparseable(_value)


def _register_above(code: int, value: str) -> None:
    def run(kit: Kit, tmp_path: Path) -> None:
        rig = kit.rig(tmp_path, [limited(code, value)])
        failure = _failed(kit, rig, kit.call(rig, INDEX))
        assert type(failure) is kit.error("RateLimited")
        assert (failure.status, failure.attempts, failure.index) == (code, 1, INDEX)
        assert len(rig.transport.requests) == 1, "no wait longer than the bound, no second request"
        assert rig.sleeps.waits == []

    SCENARIOS[f"retry_after_above_the_bound[{code},{value[:12]}]"] = run


for _code2, _value2 in [
    (429, "31"),
    (429, "3600"),
    (429, "99999999999999999999999"),
    (503, "31"),
    (429, "Fri, 23 Oct 2026 13:08:58 GMT"),
    (503, "Sat, 24 Oct 2026 13:08:26 GMT"),
]:
    _register_above(_code2, _value2)


def _above_the_bound_on_a_later_attempt(kit: Kit, tmp_path: Path) -> None:
    rig = kit.rig(tmp_path, [limited(429, None), limited(503, "31")])
    failure = _failed(kit, rig, kit.call(rig, INDEX))
    assert type(failure) is kit.error("RateLimited")
    assert (failure.attempts, failure.status) == (2, 503)
    assert rig.sleeps.waits == [1]


SCENARIOS["retry_after_above_the_bound_on_a_later_attempt"] = _above_the_bound_on_a_later_attempt


# ------------------------------------------------------------------ timeouts, headers, policy


def _timeouts_and_headers(kit: Kit, tmp_path: Path) -> None:
    rig = kit.rig(tmp_path, [status(503), status(503), kit.ok()])
    assert kit.call(rig, INDEX).failure is None
    assert len(rig.transport.requests) == 3
    for request in rig.transport.requests:
        assert request.extensions["timeout"] == {
            "connect": 10.0,
            "read": 60.0,
            "write": 60.0,
            "pool": 10.0,
        }
        assert request.headers.get_list("accept-encoding") == ["identity"]
        assert request.headers.get_list("api-subscription-key") == [CANARY_KEY]
        assert request.method == "POST"
        assert request.url.scheme == "https"
        assert request.url.host == "api.sarvam.ai"
        assert request.url.path == kit.path
        assert request.url.query == b""
        assert "authorization" not in request.headers
        assert request.headers["content-type"].split(";")[0] == "application/json"


SCENARIOS["timeouts_and_headers_of_every_attempt"] = _timeouts_and_headers


def _policy_constants_are_the_adr_values(kit: Kit, tmp_path: Path) -> None:
    import inspect
    import time

    c = client_mod()
    assert c.MAX_ATTEMPTS == 4
    assert tuple(c.BACKOFF_SECONDS) == (1, 2, 4)
    assert c.RETRY_AFTER_MAX_SECONDS == 30
    assert (c.CONNECT_TIMEOUT_SECONDS, c.POOL_TIMEOUT_SECONDS) == (10, 10)
    assert (c.READ_TIMEOUT_SECONDS, c.WRITE_TIMEOUT_SECONDS) == (60, 60)
    from evalproviders_phase4_support import chat_mod, tts_mod  # type: ignore[import-not-found,unused-ignore]

    cls = tts_mod().SarvamTts if kit.name == "tts" else chat_mod().SarvamLlm
    parameters = inspect.signature(cls.__init__).parameters
    assert parameters["sleep"].default is time.sleep, "production sleeps; tests inject a fake"
    assert parameters["monotonic"].default is time.monotonic
    assert parameters["transport"].default is None, "production uses the default transport"


SCENARIOS["policy_constants_are_the_adr_values"] = _policy_constants_are_the_adr_values
