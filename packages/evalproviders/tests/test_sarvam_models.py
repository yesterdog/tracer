# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-06: Sarvam request and response models, the mr-only language map, the explicit model
(contract docs/phase3/api-surface-batch-b.md sections 3 and 4; ADR 0024 rules 6 and 8).

The response shapes come from the 47 captures of 2026-10-23 (public fixtures); the adversarial
bodies are written here. Nothing opens a socket.
"""

from __future__ import annotations

import dataclasses
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    Capture,
    Rig,
    capture_names,
    context,
    json_response,
    models,
    multipart_parts,
    ok_body,
    small_plan,
    stt,
)

FORM_FIELDS = {"file", "language_code", "model", "with_timestamps"}
OTHER_LANGUAGES = ["en", "hi", "te", "ta", "kn", "ml", "pa", "gu"]


# ------------------------------------------------------------------------------- request


@pytest.mark.parametrize("model", [None, "saaras:v3", "saaras:v4"])
def test_sarvam_request_sends_unknown_language_and_explicit_model(
    tmp_path: Path, model: str | None
) -> None:
    plan = small_plan()
    rig = Rig(tmp_path, [json_response(ok_body())], model=model)
    rig.client.send_chunk(plan.chunks[0])
    (request,) = rig.transport.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.sarvam.ai/speech-to-text"
    parts = multipart_parts(request)
    names = [p.name for p in parts]
    assert sorted(names) == sorted(FORM_FIELDS), "exactly the four form fields, each once"
    by_name = {p.name: p for p in parts}
    assert by_name["language_code"].data == b"unknown"
    assert by_name["with_timestamps"].data == b"true"
    expected_model = model if model is not None else "saaras:v4"
    assert by_name["model"].data == expected_model.encode(), "the model is always sent"
    assert models().DEFAULT_MODEL == "saaras:v4"
    file_part = by_name["file"]
    assert file_part.data == plan.chunks[0].wav, "the chunk file, byte for byte, at its own rate"
    assert file_part.content_type == "audio/wav"
    assert file_part.filename is not None
    assert re.fullmatch(r"[A-Za-z0-9._-]+\.wav", file_part.filename)
    for part in parts:
        if part.name != "file":
            assert part.filename is None
            assert part.content_type is None
    # the key is in one header only
    assert request.headers["api-subscription-key"] == CANARY_KEY
    for name, value in request.headers.items():
        if name.lower() != "api-subscription-key":
            assert CANARY_KEY not in value, name
    assert CANARY_KEY.encode() not in request.content
    assert CANARY_KEY not in str(request.url)
    assert "authorization" not in request.headers


def test_sarvam_request_never_sends_forced_code_in_base_case(tmp_path: Path) -> None:
    plan = small_plan()
    assert len(plan.chunks) == 3
    rig = Rig(tmp_path, [json_response(ok_body(request_id=f"r-{i}")) for i in range(3)])
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert len(rig.transport.requests) == 3
    for request in rig.transport.requests:
        fields = {p.name: p.data for p in multipart_parts(request)}
        assert fields["language_code"] == b"unknown"
        for forced in (b"mr-IN", b"mr_IN", b"hi-IN", b"en-IN"):
            assert forced not in request.content
    assert models().language_code_for("mr") == "unknown"
    assert models().SarvamRequest(model="saaras:v4").text_fields("mr") == {
        "language_code": "unknown",
        "model": "saaras:v4",
        "with_timestamps": "true",
    }


@pytest.mark.parametrize(
    "language", [*OTHER_LANGUAGES, "mr-IN", "MR", "mr ", "", "unknown", "xx", "fr", "hi-IN", "mar"]
)
def test_sarvam_configured_language_other_than_mr_refused(tmp_path: Path, language: str) -> None:
    plan = small_plan()
    rig = Rig(tmp_path, [json_response(ok_body())])
    with pytest.raises(stt().SttLanguageNotSupportedError) as raised:
        rig.engine().transcribe(plan, context(language))
    assert type(raised.value) is stt().SttLanguageNotSupportedError
    assert isinstance(raised.value, stt().SttConfigurationError)
    assert isinstance(raised.value, ValueError)
    assert isinstance(raised.value, stt().SttError)
    assert rig.transport.requests == [], "refused before any request (BL-162)"
    assert rig.log_text == "", "and before any attempt is logged"
    with pytest.raises(stt().SttLanguageNotSupportedError):
        models().language_code_for(language)


def test_sarvam_model_must_be_a_usable_name(tmp_path: Path) -> None:
    for number, bad in enumerate(("", " ", "saaras:v4 ", "\n", "a b")):
        with pytest.raises(stt().SttConfigurationError):
            Rig(tmp_path / f"m{number}", [], model=bad)


# ------------------------------------------------------------------------------ response

SUCCESS_CAPTURES = [*capture_names("stt_"), "err_bn_in_unrelated_clip"]
ERROR_CAPTURES = [n for n in capture_names("err_") if n != "err_bn_in_unrelated_clip"]


def test_sarvam_response_models_parse_all_captures() -> None:
    assert len(SUCCESS_CAPTURES) == 32
    assert len(ERROR_CAPTURES) == 6
    m = models()
    for name in SUCCESS_CAPTURES:
        capture = Capture(name)
        assert capture.status == 200, name
        raw: Any = json.loads(capture.body, parse_float=Decimal)
        parsed = m.parse_response(capture.body)
        assert parsed.request_id == raw["request_id"], name
        assert parsed.transcript == raw["transcript"], name
        assert parsed.language_code == raw["language_code"], name
        if "language_probability" in raw:
            assert isinstance(parsed.language_probability, Decimal), name
            assert parsed.language_probability == raw["language_probability"], name
            assert str(parsed.language_probability) == str(raw["language_probability"]), name
        else:
            assert parsed.language_probability is None, name
        if "timestamps" in raw:
            stamps = raw["timestamps"]
            assert parsed.phrases is not None, name
            assert len(parsed.phrases) == len(stamps["words"]), name
            for phrase, word, start, end in zip(
                parsed.phrases,
                stamps["words"],
                stamps["start_time_seconds"],
                stamps["end_time_seconds"],
                strict=True,
            ):
                assert phrase.text == word
                assert isinstance(phrase.start_seconds, Decimal)
                assert phrase.start_seconds == Decimal(str(start))
                assert phrase.end_seconds == Decimal(str(end))
        else:
            assert parsed.phrases is None, name
        # the transcript is personal data: it is not in the repr
        if raw["transcript"]:
            assert raw["transcript"] not in repr(parsed), name
            assert raw["transcript"] not in str(parsed), name
    # known values of two captures, written out
    hi = m.parse_response(Capture("stt_hi_timestamps").body)
    assert hi.language_code == "hi-IN"
    assert hi.language_probability is None
    assert [str(p.end_seconds) for p in hi.phrases] == ["3.33"]
    silence = m.parse_response(Capture("stt_silence_unknown").body)
    assert silence.transcript == ""
    assert silence.language_code == "en-IN"
    assert silence.language_probability is None
    mr = m.parse_response(Capture("stt_mr_unknown").body)
    assert mr.language_code == "mr-IN"
    assert mr.language_probability == Decimal("1.0")


def test_sarvam_response_models_parse_all_captures_error_bodies_are_not_success() -> None:
    for name in ERROR_CAPTURES:
        capture = Capture(name)
        assert capture.status in {400, 403}, name
        with pytest.raises(models().SarvamResponseShapeError):
            models().parse_response(capture.body)


def test_sarvam_response_models_have_exactly_the_documented_members() -> None:
    m = models()
    assert [f.name for f in dataclasses.fields(m.SarvamSttResponse)] == [
        "request_id",
        "transcript",
        "language_code",
        "language_probability",
        "phrases",
    ]
    assert [f.name for f in dataclasses.fields(m.SarvamPhrase)] == [
        "text",
        "start_seconds",
        "end_seconds",
    ]
    body = ok_body(phrases=[("a", 0.0, 1.0)])
    body["audio_duration"] = 12.5
    body["usage"] = {"x": 1}
    body["timestamps"]["confidence"] = [0.5]
    parsed = m.parse_response(json.dumps(body).encode())
    assert "audio_duration" not in repr(vars(parsed))
    assert "usage" not in repr(vars(parsed))
    assert len(parsed.phrases) == 1
    assert m.API_BASE_URL == "https://api.sarvam.ai"
    assert m.STT_PATH == "/speech-to-text"
    assert m.STT_URL == "https://api.sarvam.ai/speech-to-text"
    assert tuple(m.FORM_FIELD_NAMES) == ("file", "language_code", "model", "with_timestamps")
    assert m.MAX_RESPONSE_BYTES == 1_048_576


def test_sarvam_response_numbers_are_read_as_decimals_never_floats() -> None:
    body = (
        b'{"request_id":"r","transcript":"t","language_code":"mr-IN",'
        b'"language_probability":0.9950000000000000001,'
        b'"timestamps":{"words":["a"],"start_time_seconds":[0.1000000000000000001],'
        b'"end_time_seconds":[3.3300000000000000001]}}'
    )
    parsed = models().parse_response(body)
    assert str(parsed.language_probability) == "0.9950000000000000001"
    assert str(parsed.phrases[0].start_seconds) == "0.1000000000000000001"
    assert str(parsed.phrases[0].end_seconds) == "3.3300000000000000001"
    exponent = models().parse_response(
        b'{"request_id":"r","transcript":"t","language_code":null,"language_probability":1E0}'
    )
    assert exponent.language_probability == Decimal(1)
    ints = models().parse_response(
        b'{"request_id":"r","transcript":"t","language_code":null,'
        b'"timestamps":{"words":["a"],"start_time_seconds":[0],"end_time_seconds":[2]}}'
    )
    assert ints.phrases[0].end_seconds == Decimal(2)


def _base() -> dict[str, Any]:
    return {
        "request_id": "r-1",
        "transcript": "t",
        "language_code": "mr-IN",
        "language_probability": 0.5,
        "timestamps": {"words": ["a"], "start_time_seconds": [0.0], "end_time_seconds": [1.0]},
    }


def _with(**changes: Any) -> bytes:
    doc = _base()
    for key, value in changes.items():
        if value is ...:
            doc.pop(key, None)
        else:
            doc[key] = value
    return json.dumps(doc).encode()


def _ts(**changes: Any) -> bytes:
    stamps = dict(_base()["timestamps"])
    stamps.update(changes)
    return _with(timestamps=stamps)


BAD_BODIES: list[tuple[str, bytes, str]] = [
    ("empty", b"", "not_json"),
    ("html", b"<html>502</html>", "not_json"),
    ("invalid utf-8", b'{"request_id":"\xff"}', "not_json"),
    ("byte order mark", b"\xef\xbb\xbf" + _with(), "not_json"),
    ("truncated", _with()[:-5], "not_json"),
    ("trailing text", _with() + b" x", "not_json"),
    ("NaN", b'{"request_id":"r","transcript":"t","language_probability":NaN}', "not_json"),
    (
        "Infinity",
        b'{"request_id":"r","transcript":"t","language_probability":Infinity}',
        "not_json",
    ),
    (
        "duplicate member",
        b'{"request_id":"a","request_id":"b","transcript":"t","language_code":null}',
        "not_json",
    ),
    (
        "lone surrogate in the transcript",
        b'{"request_id":"r","transcript":"\\ud800","language_code":null}',
        "not_json",
    ),
    ("deeply nested", b"[" * 100_000 + b"]" * 100_000, "not_json"),
    ("array", b"[1, 2]", "not_object"),
    ("string", b'"transcript"', "not_object"),
    ("null", b"null", "not_object"),
    ("number", b"7", "not_object"),
    ("request_id missing", _with(request_id=...), "request_id_invalid"),
    ("request_id null", _with(request_id=None), "request_id_invalid"),
    ("request_id number", _with(request_id=7), "request_id_invalid"),
    ("request_id list", _with(request_id=["r"]), "request_id_invalid"),
    ("transcript missing", _with(transcript=...), "transcript_invalid"),
    ("transcript null", _with(transcript=None), "transcript_invalid"),
    ("transcript number", _with(transcript=7), "transcript_invalid"),
    ("transcript list", _with(transcript=["a"]), "transcript_invalid"),
    ("language_code number", _with(language_code=7), "language_code_invalid"),
    ("language_code bool", _with(language_code=True), "language_code_invalid"),
    ("language_code list", _with(language_code=["mr-IN"]), "language_code_invalid"),
    ("language_code object", _with(language_code={"a": 1}), "language_code_invalid"),
    ("probability string", _with(language_probability="0.9"), "language_probability_invalid"),
    ("probability null", _with(language_probability=None), "language_probability_invalid"),
    ("probability true", _with(language_probability=True), "language_probability_invalid"),
    ("probability list", _with(language_probability=[0.5]), "language_probability_invalid"),
    ("probability above 1", _with(language_probability=1.5), "language_probability_invalid"),
    ("probability integer 2", _with(language_probability=2), "language_probability_invalid"),
    ("probability negative", _with(language_probability=-0.1), "language_probability_invalid"),
    ("timestamps null", _with(timestamps=None), "timestamps_invalid"),
    ("timestamps string", _with(timestamps="0-1"), "timestamps_invalid"),
    ("timestamps list", _with(timestamps=[[0, 1]]), "timestamps_invalid"),
    ("timestamps empty object", _with(timestamps={}), "timestamps_invalid"),
    ("words missing", _ts(words=None), "timestamps_invalid"),
    ("starts not a list", _ts(start_time_seconds=0.0), "timestamps_invalid"),
    ("ends not a list", _ts(end_time_seconds="1"), "timestamps_invalid"),
    ("unequal lengths", _ts(end_time_seconds=[1.0, 2.0]), "timestamps_invalid"),
    ("word is a number", _ts(words=[7]), "timestamps_invalid"),
    ("word is null", _ts(words=[None]), "timestamps_invalid"),
    ("start is a string", _ts(start_time_seconds=["0"]), "timestamps_invalid"),
    ("start is null", _ts(start_time_seconds=[None]), "timestamps_invalid"),
    ("start is a bool", _ts(start_time_seconds=[False]), "timestamps_invalid"),
    ("end is a bool", _ts(end_time_seconds=[True]), "timestamps_invalid"),
    ("start is negative", _ts(start_time_seconds=[-0.5]), "timestamps_invalid"),
    (
        "end is negative",
        _ts(start_time_seconds=[-2.0], end_time_seconds=[-1.0]),
        "timestamps_invalid",
    ),
    (
        "end before start",
        _ts(start_time_seconds=[2.0], end_time_seconds=[1.0]),
        "timestamps_invalid",
    ),
]


@pytest.mark.parametrize(("label", "body", "reason"), BAD_BODIES, ids=[b[0] for b in BAD_BODIES])
def test_sarvam_response_rejects_wrong_types_and_oversize_body(
    label: str, body: bytes, reason: str
) -> None:
    with pytest.raises(models().SarvamResponseShapeError) as raised:
        models().parse_response(body)
    assert raised.value.reason == reason, label
    assert isinstance(raised.value, ValueError)
    # the message holds the machine key only: nothing of the body
    assert str(raised.value) == reason


GOOD_BODIES: list[tuple[str, bytes]] = [
    ("minimal", b'{"request_id":"r","transcript":"","language_code":null}'),
    ("language_code absent", b'{"request_id":"r","transcript":"t"}'),
    ("probability 0", _with(language_probability=0)),
    ("probability 1", _with(language_probability=1)),
    ("empty timestamps arrays", _ts(words=[], start_time_seconds=[], end_time_seconds=[])),
    ("zero-length phrase", _ts(start_time_seconds=[1.0], end_time_seconds=[1.0])),
    ("white space transcript", _with(transcript="  \n")),
    ("extra members", _with(extra={"a": [1]})),
]


@pytest.mark.parametrize(("label", "body"), GOOD_BODIES, ids=[b[0] for b in GOOD_BODIES])
def test_sarvam_response_accepts_the_documented_variants(label: str, body: bytes) -> None:
    parsed = models().parse_response(body)
    assert parsed.request_id == "r" or parsed.request_id == "r-1", label
    if label == "language_code absent":
        assert parsed.language_code is None
    if label == "empty timestamps arrays":
        assert parsed.phrases == ()
