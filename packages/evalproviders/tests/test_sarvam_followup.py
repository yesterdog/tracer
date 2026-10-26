# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 3 batch B follow-up: tests that pin the behaviours the implementation added beyond the
batch B contract (decision log, "PHASE 3 BATCH B IMPLEMENTED", deviations 1 to 7).

Additive only: no earlier test is changed. The key is the canary ``CANARY_KEY``; no test reads a
real key, ``.env.local`` or the environment of the machine; no test opens a socket (the autouse
guard of ``conftest.py`` is on) or waits for real.
"""

from __future__ import annotations

import contextlib
import json
import signal
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MESSAGE_CANARY,
    TEXT_CANARY,
    Capture,
    Rig,
    ScriptedTransport,
    calllog_mod,
    chain,
    client_mod,
    context,
    engine_mod,
    hand_plan,
    json_response,
    mapping_mod,
    models,
    ok_body,
    secrets_mod,
    small_plan,
    stt,
)
from tracer_evalcore.normalise import NORMALISER_ID
from tracer_evalcore.tokens import TextTurn, TimedSegment, tokenise_turns

SECOND_KEY = "-".join(["OTHERCANARY", "".join(["k8", "w1", "p5"])])
DATE = "Fri, 23 Oct 2026 13:08:26 GMT"
DEADLINE_SECONDS = 20


@contextlib.contextmanager
def deadline(seconds: int = DEADLINE_SECONDS) -> Iterator[None]:
    """Fails the test instead of hanging it when the body takes longer than ``seconds``."""
    if not hasattr(signal, "SIGALRM"):  # pragma: no cover - POSIX only
        yield
        return

    def _expired(signum: int, frame: Any) -> None:
        raise AssertionError(f"the code under test did not finish within {seconds} s")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _forms(error: BaseException) -> str:
    return " | ".join(f"{item!s} {item!r} {item.args!r}" for item in chain(error))


def _first_chunk() -> Any:
    return small_plan().chunks[0]


# ------------------------------------------------------- deviation 1: two definitions in a file


@pytest.mark.parametrize(
    "lines",
    [
        [f"SARVAM_API_KEY={CANARY_KEY}", f"SARVAM_API_KEY={SECOND_KEY}"],
        [f"SARVAM_API_KEY={CANARY_KEY}", f"SARVAM_API_KEY={CANARY_KEY}"],
        [f"export SARVAM_API_KEY={CANARY_KEY}", f"SARVAM_API_KEY={SECOND_KEY}"],
        [f"SARVAM_API_KEY='{CANARY_KEY}'", "OTHER=1", f'  SARVAM_API_KEY = "{SECOND_KEY}"'],
        [f"SARVAM_API_KEY={CANARY_KEY}", "", "# a comment", "SARVAM_API_KEY="],
    ],
    ids=["two-keys", "same-key-twice", "export-and-plain", "quoted-and-spaced", "second-is-empty"],
)
def test_env_file_two_definitions_refused(tmp_path: Path, lines: list[str]) -> None:
    path = tmp_path / "keys.env"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    environ = {"SARVAM_API_KEY": SECOND_KEY}
    with pytest.raises(stt().SttKeyMissingError) as raised:
        secrets_mod().load_api_key(env_file=path, environ=environ)
    error = raised.value
    assert type(error) is stt().SttKeyMissingError
    assert error.reason == "key_ambiguous"
    assert error.__cause__ is None
    assert error.__context__ is None
    forms = _forms(error)
    for secret in (CANARY_KEY, SECOND_KEY, str(path), path.name):
        assert secret not in forms


def test_env_file_two_definitions_refused_before_the_out_dir_exists(tmp_path: Path) -> None:
    path = tmp_path / "keys.env"
    path.write_text(f"SARVAM_API_KEY={CANARY_KEY}\nSARVAM_API_KEY={SECOND_KEY}\n", "utf-8")
    out = tmp_path / "never-created"
    transport = ScriptedTransport([])
    with pytest.raises(stt().SttKeyMissingError) as raised:
        engine_mod().create_sarvam_stt(out_dir=out, env_file=path, transport=transport)
    assert raised.value.reason == "key_ambiguous"
    assert not out.exists()
    assert transport.requests == []


def test_env_file_one_definition_among_decoys_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "keys.env"
    path.write_text(
        f"# SARVAM_API_KEY={SECOND_KEY}\nOTHER_SARVAM_API_KEY={SECOND_KEY}\n"
        f"SARVAM_API_KEY={CANARY_KEY}\nSARVAM_API_KEY_2={SECOND_KEY}\n",
        "utf-8",
    )
    assert secrets_mod().load_api_key(env_file=path).reveal() == CANARY_KEY


# ------------------------------------------------------ deviation 2: number size bounds (parse)


def _body(probability: str | None = None, start: str = "0", end: str = "1") -> bytes:
    parts = ['"request_id": "r-1"', '"transcript": "t"', '"language_code": "mr-IN"']
    if probability is not None:
        parts.append(f'"language_probability": {probability}')
    parts.append(
        f'"timestamps": {{"words": ["w"], "start_time_seconds": [{start}], '
        f'"end_time_seconds": [{end}]}}'
    )
    return ("{" + ", ".join(parts) + "}").encode("ascii")


def _reason(body: bytes) -> str | None:
    """The refusal reason of a body, or ``None`` when it parses."""
    with deadline():
        try:
            models().parse_response(body)
        except models().SarvamResponseShapeError as shape:
            return str(shape.reason)
    return None


FRACTION_100 = "0." + "0" * 99 + "1"  # one hundred fractional digits
FRACTION_101 = "0." + "0" * 100 + "1"  # one hundred and one


def test_response_probability_with_100_fractional_digits_is_accepted() -> None:
    with deadline():
        parsed = models().parse_response(_body(probability=FRACTION_100))
    assert parsed.language_probability == Decimal(FRACTION_100)
    assert parsed.language_probability is not None
    assert parsed.language_probability.as_tuple().exponent == -100


@pytest.mark.parametrize(
    "number",
    [
        FRACTION_101,
        "0." + "1" * 5000,
        "0." + "0" * 100 + "0",  # zero with 101 fractional digits, written out
        "1E-101",
        "1E-999999999",
        "1E999999999",
        "0E999999999",
        "1E10",
        "1e+10",
    ],
    ids=[
        "101-digits",
        "5000-digits",
        "zero-101-digits",
        "1E-101",
        "1E-999999999",
        "1E999999999",
        "0E999999999",
        "1E10",
        "1e+10",
    ],
)
def test_response_probability_beyond_the_size_bounds_is_language_probability_invalid(
    number: str,
) -> None:
    assert _reason(_body(probability=number)) == "language_probability_invalid"


def test_response_probability_one_and_zero_still_parse_with_fraction_forms() -> None:
    for text in ("1", "1.0", "1E0", "0", "0.0", "0E-100", "0.5"):
        assert _reason(_body(probability=text)) is None, text


@pytest.mark.parametrize("field", ["start", "end"])
def test_response_time_with_100_fractional_digits_is_accepted(field: str) -> None:
    kwargs = {field: FRACTION_100}
    if field == "start":
        kwargs["end"] = "1"
    with deadline():
        parsed = models().parse_response(_body(**kwargs))
    assert parsed.phrases is not None
    assert len(parsed.phrases) == 1


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize(
    "number",
    [
        FRACTION_101,
        "0." + "1" * 5000,
        "1E-101",
        "1E-999999999",
        "1E999999999",
        "-1E999999999",
        "1E10",
        "1e+10",
        "10000000000",
        "10000000000.5",
        "1" + "0" * 5000,
    ],
    ids=[
        "101-digits",
        "5000-digits",
        "1E-101",
        "1E-999999999",
        "1E999999999",
        "minus-1E999999999",
        "1E10",
        "1e+10",
        "ten-billion",
        "ten-billion-and-a-half",
        "5001-digit-integer",
    ],
)
def test_response_time_beyond_the_size_bounds_is_refused_fast(field: str, number: str) -> None:
    kwargs = {field: number}
    if field == "start":
        kwargs["end"] = "1E999999999"
    reason = _reason(_body(**kwargs))
    assert reason in {"timestamps_invalid", "not_json"}
    if "5001" not in number and number != "1" + "0" * 5000:
        assert reason == "timestamps_invalid"


def test_response_time_just_below_ten_billion_seconds_is_accepted() -> None:
    assert _reason(_body(start="9999999999", end="9999999999.5")) is None
    assert _reason(_body(start="0", end="9999999999")) is None


def test_response_time_at_ten_billion_seconds_is_refused() -> None:
    assert _reason(_body(start="0", end="10000000000")) == "timestamps_invalid"
    assert _reason(_body(start="10000000000", end="10000000000")) == "timestamps_invalid"


@pytest.mark.parametrize("number", ["1E999999999", "0." + "1" * 5000, "1E-999999999"])
def test_client_maps_an_oversized_number_to_response_invalid_without_a_second_request(
    tmp_path: Path, number: str
) -> None:
    body = _body(probability=number)
    rig = Rig(tmp_path, [json_response(body)])
    with deadline(), pytest.raises(stt().SttResponseInvalid) as raised:
        rig.client.send_chunk(_first_chunk())
    assert raised.value.reason == "language_probability_invalid"
    assert raised.value.status == 200
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []


# ------------------------------------------------------- deviation 3: call log input validation


def _fresh_log(tmp_path: Path) -> Any:
    log = calllog_mod().CallLog.open(tmp_path / "log")
    return log


def _before(log: Any, **over: Any) -> None:
    facts: dict[str, Any] = {
        "chunk_index": 0,
        "attempt": 1,
        "bytes_sent": 100,
        "audio_seconds": "1.5",
    }
    facts.update(over)
    log.before(**facts)


def _after(log: Any, **over: Any) -> None:
    facts: dict[str, Any] = {
        "chunk_index": 0,
        "attempt": 1,
        "bytes_sent": 100,
        "audio_seconds": "1.5",
        "status": 200,
        "error_kind": None,
        "elapsed_ms": 10,
        "request_id": "req-1",
    }
    facts.update(over)
    log.after(**facts)


@pytest.mark.parametrize(
    "field", ["chunk_index", "attempt", "bytes_sent"], ids=["chunk", "attempt", "bytes"]
)
@pytest.mark.parametrize(
    "value", ["1", 1.0, True, False, b"1", [1], 1.5], ids=lambda v: type(v).__name__ + str(v)
)
def test_call_log_a_count_that_is_not_an_int_is_a_type_error_and_writes_nothing(
    tmp_path: Path, field: str, value: Any
) -> None:
    log = _fresh_log(tmp_path)
    try:
        with pytest.raises(TypeError):
            _before(log, **{field: value})
        with pytest.raises(TypeError):
            _after(log, **{field: value})
        assert log.path.read_bytes() == b""
    finally:
        log.close()


@pytest.mark.parametrize("field", ["chunk_index", "attempt", "bytes_sent"])
def test_call_log_a_required_count_that_is_none_is_a_type_error_and_writes_nothing(
    tmp_path: Path, field: str
) -> None:
    log = _fresh_log(tmp_path)
    try:
        with pytest.raises(TypeError):
            _before(log, **{field: None})
        assert log.path.read_bytes() == b""
    finally:
        log.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "200"),
        ("status", 200.0),
        ("status", True),
        ("elapsed_ms", "10"),
        ("elapsed_ms", 10.5),
        ("elapsed_ms", False),
    ],
)
def test_call_log_after_status_and_elapsed_that_are_not_ints_are_a_type_error(
    tmp_path: Path, field: str, value: Any
) -> None:
    log = _fresh_log(tmp_path)
    try:
        with pytest.raises(TypeError):
            _after(log, **{field: value})
        assert log.path.read_bytes() == b""
    finally:
        log.close()


def test_call_log_status_none_is_a_valid_transport_failure_line(tmp_path: Path) -> None:
    log = _fresh_log(tmp_path)
    try:
        _after(log, status=None, error_kind="read_timeout", request_id=None)
        line = json.loads(log.path.read_text("ascii"))
        assert line["status"] is None
        assert line["error_kind"] == "read_timeout"
    finally:
        log.close()


BAD_SECONDS = [
    "",
    " 1",
    "1 ",
    "1.",
    ".5",
    "-1",
    "+1",
    "1e3",
    "1E3",
    "0x10",
    "NaN",
    "inf",
    "1,5",
    "abc",
    "1.5\n",
    "\u0661",  # an Arabic-Indic digit
    "1" * 21,
    "1." + "1" * 19,
    "1.5; DROP",
    TEXT_CANARY,
    "1.5" + TEXT_CANARY,
]


@pytest.mark.parametrize("value", BAD_SECONDS, ids=[repr(v)[:24] for v in BAD_SECONDS])
def test_call_log_audio_seconds_that_is_not_a_decimal_string_is_a_value_error(
    tmp_path: Path, value: str
) -> None:
    log = _fresh_log(tmp_path)
    try:
        with pytest.raises(ValueError, match="decimal string") as raised_before:
            _before(log, audio_seconds=value)
        with pytest.raises(ValueError, match="decimal string") as raised_after:
            _after(log, audio_seconds=value)
        assert log.path.read_bytes() == b""
        for raised in (raised_before, raised_after):
            assert TEXT_CANARY not in _forms(raised.value)
            assert raised.value.__cause__ is None
    finally:
        log.close()


@pytest.mark.parametrize("value", [1.5, 1, None, b"1.5", ["1"]], ids=lambda v: type(v).__name__)
def test_call_log_audio_seconds_that_is_not_a_string_is_refused_and_writes_nothing(
    tmp_path: Path, value: Any
) -> None:
    log = _fresh_log(tmp_path)
    try:
        with pytest.raises((TypeError, ValueError)):
            _before(log, audio_seconds=value)
        assert log.path.read_bytes() == b""
    finally:
        log.close()


@pytest.mark.parametrize("value", ["0", "1", "12.5", "0.000001907348632812", "9" * 20])
def test_call_log_audio_seconds_decimal_strings_are_written_as_given(
    tmp_path: Path, value: str
) -> None:
    log = _fresh_log(tmp_path)
    try:
        _before(log, audio_seconds=value)
        assert json.loads(log.path.read_text("ascii"))["audio_seconds"] == value
    finally:
        log.close()


def test_call_log_longest_decimal_string_is_written(tmp_path: Path) -> None:
    value = "9" * 20 + "." + "9" * 18
    log = _fresh_log(tmp_path)
    try:
        _before(log, audio_seconds=value)
        assert json.loads(log.path.read_text("ascii"))["audio_seconds"] == value
    finally:
        log.close()


def test_call_log_text_can_never_reach_the_log(tmp_path: Path) -> None:
    """Whatever string is passed for a free-text slot, only a safe form is written."""
    log = _fresh_log(tmp_path)
    try:
        _after(log, error_kind=TEXT_CANARY, request_id=TEXT_CANARY + " spoken words")
        _after(log, error_kind="read timeout " + MESSAGE_CANARY, request_id="a b " + MESSAGE_CANARY)
        _after(log, error_kind="a" * 65, request_id="r" * 129)
        _after(log, error_kind="UPPER", request_id="\u0928\u092e")
        for bad in (TEXT_CANARY, MESSAGE_CANARY, CANARY_KEY):
            with pytest.raises(ValueError, match="decimal string"):
                _before(log, audio_seconds=bad)
            with pytest.raises(TypeError):
                _before(log, bytes_sent=bad)
        raw = log.path.read_bytes()
        text = raw.decode("ascii")
        assert TEXT_CANARY not in text
        assert MESSAGE_CANARY not in text
        assert CANARY_KEY not in text
        lines = [json.loads(line) for line in text.splitlines()]
        assert len(lines) == 4
        for line in lines:
            assert line["error_kind"] is None
            assert line["request_id"] is None
            assert set(line) == {
                "at",
                "phase",
                "chunk",
                "attempt",
                "bytes_sent",
                "audio_seconds",
                "status",
                "error_kind",
                "elapsed_ms",
                "request_id",
            }
    finally:
        log.close()


def test_call_log_a_rejected_line_does_not_read_the_clock_or_corrupt_the_next(
    tmp_path: Path,
) -> None:
    ticks: list[int] = []

    def clock() -> datetime:
        ticks.append(1)
        return datetime(2026, 10, 25, 9, 0, 0, 1, tzinfo=UTC)

    log = calllog_mod().CallLog.open(tmp_path / "log", clock=clock)
    try:
        with pytest.raises(TypeError):
            _before(log, attempt="1")
        with pytest.raises(ValueError, match="decimal string"):
            _before(log, audio_seconds="x")
        _before(log)
        assert len(ticks) == 1, "only the accepted line reads the clock"
        assert len(log.path.read_text("ascii").splitlines()) == 1
    finally:
        log.close()


# ----------------------------------------- deviation 4: SarvamStt call_log and context manager


class _Counting:
    """Counts the calls of ``close`` on a CallLog and on a SarvamClient."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.log_closes = 0
        self.client_closes = 0
        log_cls = calllog_mod().CallLog
        client_cls = client_mod().SarvamClient
        real_log_close = log_cls.close
        real_client_close = client_cls.close

        def log_close(this: Any) -> None:
            self.log_closes += 1
            real_log_close(this)

        def client_close(this: Any) -> None:
            self.client_closes += 1
            real_client_close(this)

        monkeypatch.setattr(log_cls, "close", log_close)
        monkeypatch.setattr(client_cls, "close", client_close)


def _engine_with_log(tmp_path: Path) -> tuple[Any, Any, Any]:
    transport = ScriptedTransport([json_response(ok_body("a"))])
    log = calllog_mod().CallLog.open(tmp_path / "out")
    client = client_mod().SarvamClient(
        secrets_mod().ApiKey(CANARY_KEY), call_log=log, transport=transport
    )
    return engine_mod().SarvamStt(client, call_log=log), client, log


def test_sarvam_stt_call_log_is_keyword_only(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [])
    with pytest.raises(TypeError):
        engine_mod().SarvamStt(rig.client, rig.log)
    # the contract signature still works and the engine then owns no log
    assert engine_mod().SarvamStt(rig.client).model == rig.client.model


def test_sarvam_stt_context_manager_closes_the_log_and_the_client_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, client, log = _engine_with_log(tmp_path)
    counts = _Counting(monkeypatch)
    with engine as entered:
        assert entered is engine
        assert counts.log_closes == 0
        assert counts.client_closes == 0
    assert counts.log_closes == 1
    assert counts.client_closes == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        _before(log)
    assert client._http.is_closed


def test_sarvam_stt_context_manager_closes_once_when_the_body_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _client, log = _engine_with_log(tmp_path)
    counts = _Counting(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"), engine:
        raise RuntimeError("boom")
    assert counts.log_closes == 1
    assert counts.client_closes == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        _before(log)


def test_sarvam_stt_context_manager_closes_once_when_transcribe_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _client, _log = _engine_with_log(tmp_path)
    counts = _Counting(monkeypatch)
    with pytest.raises(stt().SttLanguageNotSupportedError), engine:
        engine.transcribe(small_plan(), context("hi"))
    assert counts.log_closes == 1
    assert counts.client_closes == 1


def test_sarvam_stt_context_manager_does_not_swallow_the_exception(tmp_path: Path) -> None:
    engine, _client, _log = _engine_with_log(tmp_path)
    assert engine.__exit__(None, None, None) is None
    engine2, _c2, _l2 = _engine_with_log(tmp_path / "second")
    error = ValueError("kept")
    assert engine2.__exit__(ValueError, error, None) is None


def test_sarvam_stt_close_without_an_owned_log_leaves_the_caller_log_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, [])
    counts = _Counting(monkeypatch)
    with engine_mod().SarvamStt(rig.client):
        pass
    assert counts.client_closes == 1
    assert counts.log_closes == 0
    _before(rig.log)  # still writable: the engine did not own it
    assert len(rig.log_lines) == 1


def test_sarvam_stt_close_called_directly_then_by_the_manager_never_raises(
    tmp_path: Path,
) -> None:
    engine, _client, log = _engine_with_log(tmp_path)
    with engine:
        engine.close()
    with pytest.raises(OSError, match="Bad file descriptor"):
        _before(log)


def test_create_sarvam_stt_closes_the_log_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _Counting(monkeypatch)
    transport = ScriptedTransport([json_response(ok_body("a"))])
    with engine_mod().create_sarvam_stt(
        out_dir=tmp_path / "made", environ={"SARVAM_API_KEY": CANARY_KEY}, transport=transport
    ) as engine:
        assert counts.log_closes == 0
        engine.transcribe(small_plan(ms=900), context())
        log_path = tmp_path / "made" / calllog_mod().CALL_LOG_NAME
        assert log_path.is_file()
    assert counts.log_closes == 1
    assert counts.client_closes == 1
    assert len(transport.requests) == 1


def test_create_sarvam_stt_closes_the_log_once_also_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _Counting(monkeypatch)
    with (
        pytest.raises(RuntimeError, match="boom"),
        engine_mod().create_sarvam_stt(
            out_dir=tmp_path / "made",
            environ={"SARVAM_API_KEY": CANARY_KEY},
            transport=ScriptedTransport([]),
        ),
    ):
        raise RuntimeError("boom")
    assert counts.log_closes == 1
    assert counts.client_closes == 1


# ---------------------------------------------- deviation 5: Retry-After of any digit length


def _limited(value: str, status: int = 429) -> Any:
    return json_response(b"{}", status=status, headers={"retry-after": value}, date=DATE)


def _lowered_int_limit() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def manager() -> Iterator[None]:
        previous = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(640)
        try:
            yield
        finally:
            sys.set_int_max_str_digits(previous)

    return manager()


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("digits", [10, 11, 100, 4300, 5000, 100_000])
def test_retry_after_digit_string_longer_than_nine_digits_is_above_the_bound(
    tmp_path: Path, status: int, digits: int
) -> None:
    rig = Rig(tmp_path, [_limited("7" * digits, status)])
    with deadline(), _lowered_int_limit(), pytest.raises(stt().SttRateLimited) as raised:
        rig.client.send_chunk(_first_chunk())
    assert type(raised.value) is stt().SttRateLimited
    assert not isinstance(raised.value, ValueError)
    assert raised.value.reason == "retry_after_above_bound"
    assert raised.value.status == status
    assert raised.value.attempts == 1
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []


@pytest.mark.parametrize("value", ["1" + "0" * 4999, "9" * 5000, "1" * 5000])
def test_retry_after_5000_digits_does_not_hit_pythons_int_limit(tmp_path: Path, value: str) -> None:
    assert len(value) == 5000
    rig = Rig(tmp_path, [_limited(value)])
    with deadline(), pytest.raises(stt().SttRateLimited) as raised:
        rig.client.send_chunk(_first_chunk())
    assert raised.value.reason == "retry_after_above_bound"
    assert rig.sleeps.waits == []


@pytest.mark.parametrize(
    ("value", "wait"),
    [
        ("0" * 5000 + "7", 7.0),
        ("0" * 5000, 1.0),
        ("0" * 20 + "30", 30.0),
        ("000000030", 30.0),
        ("0000000000007", 7.0),
        ("30", 30.0),
    ],
    ids=[
        "5000-zeros-then-7",
        "5000-zeros",
        "20-zeros-30",
        "9-digits-30",
        "13-digits-7",
        "plain-30",
    ],
)
def test_retry_after_leading_zeros_do_not_count_as_significant_digits(
    tmp_path: Path, value: str, wait: float
) -> None:
    rig = Rig(tmp_path, [_limited(value), json_response(ok_body("a"))])
    with deadline(), _lowered_int_limit():
        rig.client.send_chunk(_first_chunk())
    assert rig.sleeps.waits == [wait]


@pytest.mark.parametrize("value", ["999999999", "100000000", "000000031", "0031", "31"])
def test_retry_after_nine_or_fewer_significant_digits_above_30_is_above_the_bound(
    tmp_path: Path, value: str
) -> None:
    rig = Rig(tmp_path, [_limited(value)])
    with pytest.raises(stt().SttRateLimited) as raised:
        rig.client.send_chunk(_first_chunk())
    assert raised.value.reason == "retry_after_above_bound"
    assert rig.sleeps.waits == []


def test_retry_after_just_below_the_cut_is_not_misread_as_above_the_bound(
    tmp_path: Path,
) -> None:
    for value in ("1", "29", "30"):
        rig = Rig(tmp_path / value, [_limited(value), json_response(ok_body("a"))])
        rig.client.send_chunk(_first_chunk())
        assert rig.sleeps.waits == [float(max(1, int(value)))]


def test_retry_after_is_ignored_on_a_500_whatever_its_length(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [_limited("9" * 5000, 500), json_response(ok_body("a"))])
    rig.client.send_chunk(_first_chunk())
    assert rig.sleeps.waits == [1.0]


# ------------------------------------------------------------- deviation 6: status ranges


def _one_response(tmp_path: Path, status: int) -> tuple[Rig, Any]:
    rig = Rig(tmp_path, [json_response(b"{}", status=status)] * 4)
    with pytest.raises(stt().SttError) as raised:
        rig.client.send_chunk(_first_chunk())
    return rig, raised.value


@pytest.mark.parametrize("status", [500, 501, 502, 503, 504, 550, 598, 599])
def test_status_500_to_599_is_a_retried_server_error(tmp_path: Path, status: int) -> None:
    rig, error = _one_response(tmp_path, status)
    assert type(error) is stt().SttServerError
    assert error.status == status
    assert error.attempts == 4
    assert len(rig.transport.requests) == 4
    assert rig.sleeps.waits == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    "status", [100, 101, 199, 201, 202, 204, 206, 299, 300, 301, 302, 304, 307, 308, 399]
)
def test_status_outside_200_and_400_to_599_is_unexpected_status(
    tmp_path: Path, status: int
) -> None:
    rig, error = _one_response(tmp_path, status)
    assert type(error) is stt().SttResponseInvalid
    assert error.reason == "unexpected_status"
    assert error.status == status
    assert error.attempts == 1
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []


@pytest.mark.parametrize("status", [600, 601, 650, 700, 799, 999])
def test_status_600_and_above_is_unexpected_status_not_a_server_error(
    tmp_path: Path, status: int
) -> None:
    rig, error = _one_response(tmp_path, status)
    assert type(error) is stt().SttResponseInvalid
    assert error.reason == "unexpected_status"
    assert error.status == status
    assert error.attempts == 1
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []
    line = rig.log_lines[-1]
    assert line["phase"] == "after"
    assert line["status"] == status


@pytest.mark.parametrize("status", [400, 404, 418, 451, 499])
def test_status_400_to_499_other_than_429_is_a_request_rejection_not_retried(
    tmp_path: Path, status: int
) -> None:
    rig, error = _one_response(tmp_path, status)
    assert type(error) is stt().SttRequestRejected
    assert error.status == status
    assert error.attempts == 1
    assert len(rig.transport.requests) == 1


def test_status_429_is_rate_limited_and_retried(tmp_path: Path) -> None:
    rig, error = _one_response(tmp_path, 429)
    assert type(error) is stt().SttRateLimited
    assert len(rig.transport.requests) == 4


# ----------------------------------------------- deviation 7: segments and the token rule


def _map_one(body: dict[str, Any] | bytes, frames: int = 24000 * 60) -> tuple[Any, Any]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    response = models().ChunkResponse(
        chunk_index=0, response=models().parse_response(raw), responded_at=None, attempts=1
    )
    plan = hand_plan([(0, frames)])
    outcome = mapping_mod().map_responses(plan, [response], model="saaras:v4", context=context())
    return outcome, outcome.turns[0]


def test_mapping_emits_one_segment_per_phrase() -> None:
    phrases = [("alpha", 0.0, 1.0), ("beta", 1.0, 2.0), ("alpha", 2.0, 3.5), ("gamma", 4.0, 4.0)]
    outcome, turn = _map_one(ok_body("alpha beta alpha gamma", phrases=phrases))
    assert turn.segments is not None
    assert len(turn.segments) == len(phrases)
    assert [s.text for s in turn.segments] == [p[0] for p in phrases]
    assert {s.granularity for s in turn.segments} == {"phrase"}
    assert outcome.warnings == ()


def test_mapping_segment_order_is_the_order_of_the_phrases_not_sorted() -> None:
    phrases = [("second", 5.0, 6.0), ("first", 1.0, 2.0)]
    _, turn = _map_one(ok_body("second first", phrases=phrases))
    assert [s.text for s in turn.segments] == ["second", "first"]
    assert [s.start_ms for s in turn.segments] == [5000, 1000]


def test_mapping_segment_text_is_verbatim_for_every_phrase() -> None:
    phrases = [
        ("  lead", 0.0, 1.0),
        ("trail  ", 1.0, 2.0),
        ("\t tab \n", 2.0, 3.0),
        ("\u0915\u094d\u200d\u0937 \u200c x", 3.0, 4.0),
        ("a  b", 4.0, 5.0),
    ]
    _, turn = _map_one(ok_body("t", phrases=phrases))
    assert [s.text for s in turn.segments] == [p[0] for p in phrases]


def test_mapping_segment_texts_are_not_merged_split_or_deduplicated() -> None:
    phrases = [("same", 0.0, 1.0), ("same", 0.0, 1.0), ("two words", 1.0, 2.0)]
    _, turn = _map_one(ok_body("same same two words", phrases=phrases))
    assert [s.text for s in turn.segments] == ["same", "same", "two words"]


@pytest.mark.parametrize(
    "name", ["stt_en_timestamps", "stt_hi_timestamps", "stt_ta_timestamps_unknown"]
)
def test_mapping_capture_segments_concatenate_to_the_transcript_for_the_token_rule(
    name: str,
) -> None:
    capture = Capture(name)
    document = json.loads(capture.body)
    _, turn = _map_one(capture.body)
    assert turn.text == document["transcript"]
    assert turn.segments is not None
    assert [s.text for s in turn.segments] == document["timestamps"]["words"]
    segments = tuple(TimedSegment(s.text, s.start_ms, s.end_ms) for s in turn.segments)
    sequence = tokenise_turns(
        [TextTurn(seq=turn.seq, text=turn.text, segments=segments)], NORMALISER_ID
    )
    assert len(sequence.tokens) > 0
    assert all(token.segment_index is not None for token in sequence.tokens), (
        "tokens.py maps a turn's tokens to segments only when the segment keys concatenate to the "
        "turn keys"
    )
    segment_keys: list[str] = []
    for segment in segments:
        segment_keys += [
            t.key for t in tokenise_turns([TextTurn(0, segment.text)], NORMALISER_ID).tokens
        ]
    assert segment_keys == list(sequence.keys)


@pytest.mark.parametrize(
    "name", ["stt_en_timestamps", "stt_hi_timestamps", "stt_ta_timestamps_unknown"]
)
def test_mapping_capture_segments_cover_the_turn_times(name: str) -> None:
    _, turn = _map_one(Capture(name).body)
    assert turn.timing_source == "audio_alignment"
    assert turn.start_ms == min(s.start_ms for s in turn.segments)
    assert turn.end_ms == max(s.end_ms for s in turn.segments)


def test_token_rule_is_not_met_when_phrases_do_not_concatenate_to_the_transcript() -> None:
    """The contrast case: segments that differ from the turn text map no token (tokens.py
    keeps the turn text and leaves segment_index None; it never guesses)."""
    _, turn = _map_one(ok_body("one two three", phrases=[("one", 0.0, 1.0), ("three", 1.0, 2.0)]))
    segments = tuple(TimedSegment(s.text, s.start_ms, s.end_ms) for s in turn.segments)
    sequence = tokenise_turns([TextTurn(seq=0, text=turn.text, segments=segments)], NORMALISER_ID)
    assert [t.segment_index for t in sequence.tokens] == [None, None, None]


def test_token_rule_is_met_for_a_multi_phrase_response_that_concatenates() -> None:
    _, turn = _map_one(
        ok_body(
            "one two three", phrases=[("one", 0.0, 1.0), ("two", 1.0, 2.0), ("three", 2.0, 3.0)]
        )
    )
    segments = tuple(TimedSegment(s.text, s.start_ms, s.end_ms) for s in turn.segments)
    sequence = tokenise_turns([TextTurn(seq=0, text=turn.text, segments=segments)], NORMALISER_ID)
    assert [t.segment_index for t in sequence.tokens] == [0, 1, 2]


def test_mapping_a_response_with_no_timestamps_has_no_segments() -> None:
    _, turn = _map_one(ok_body("plain text"))
    assert turn.segments is None
