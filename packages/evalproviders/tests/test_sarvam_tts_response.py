# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-08, the reply side of the Sarvam TTS adapter that does not depend on the audio container
(contract docs/phase4/api-surface-batch-b.md sections 2.4 to 2.6; ADR 0024 amendments A1 and A2,
rules 8 to 10 as they apply to STT).

The reply is a JSON object with a string ``request_id`` and an ``audios`` array of exactly one
strict base64 string; the JSON body and the decoded audio are capped; every failure is a typed
``Tts*`` outcome with machine facts only, raised outside any handler. The retry and error
taxonomy are the scenarios of ``evalproviders_phase4_matrix`` run against this adapter.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_matrix import SCENARIOS  # type: ignore[import-not-found,unused-ignore]
from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    TtsRig,
    core_tts,
    models_mod,
    tts_kit,
    tts_request,
)
from evalproviders_support import json_response  # type: ignore[import-not-found,unused-ignore]

AUDIO = base64.b64encode(b"\x00\x01\x02").decode("ascii")  # "AAEC": valid base64, not a WAV


def _doc(**over: Any) -> bytes:
    document: dict[str, Any] = {"request_id": "tts-req", "audios": [AUDIO]}
    document.update(over)
    return json.dumps({k: v for k, v in document.items() if v is not ...}).encode()


# ------------------------------------------------------------- the retry taxonomy (ADR 0024 A1)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_sarvam_tts_retry_taxonomy_matches_stt_rules(tmp_path: Path, scenario: str) -> None:
    """ADR 0024 rules 8 to 10 for TTS: the same attempts (4), waits (1, 2, 4 s), ``Retry-After``
    bound (30 s), timeouts (10 s and 60 s), classes, facts and caps as the STT adapter, each
    scenario mirroring a test of ``test_sarvam_client.py``."""
    SCENARIOS[scenario](tts_kit, tmp_path)


# ------------------------------------------------------------- the shape of a 200 reply

BAD_SHAPES: list[tuple[str, bytes, str]] = [
    ("not json", b"<html>", "not_json"),
    ("empty body", b"", "not_json"),
    ("an array", b"[]", "not_object"),
    ("a string", b'"x"', "not_object"),
    ("null", b"null", "not_object"),
    (
        "duplicate member",
        b'{"request_id":"a","request_id":"b","audios":["AAEC"]}',
        "not_json",
    ),
    ("NaN", b'{"request_id":"a","audios":["AAEC"],"n":NaN}', "not_json"),
    ("request id missing", _doc(request_id=...), "request_id_invalid"),
    ("request id null", _doc(request_id=None), "request_id_invalid"),
    ("request id a number", _doc(request_id=5), "request_id_invalid"),
    ("request id empty", _doc(request_id=""), "request_id_invalid"),
    ("request id over 256 characters", _doc(request_id="x" * 257), "request_id_invalid"),
    ("audios missing", _doc(audios=...), "audios_invalid"),
    ("audios null", _doc(audios=None), "audios_invalid"),
    ("audios a string", _doc(audios=AUDIO), "audios_invalid"),
    ("audios an object", _doc(audios={"0": AUDIO}), "audios_invalid"),
    ("audios empty", _doc(audios=[]), "audios_invalid"),
    ("audios two items", _doc(audios=[AUDIO, AUDIO]), "audios_invalid"),
    ("audios item a number", _doc(audios=[5]), "audios_invalid"),
    ("audios item null", _doc(audios=[None]), "audios_invalid"),
    ("audios item a list", _doc(audios=[[AUDIO]]), "audios_invalid"),
    ("audio empty string", _doc(audios=[""]), "audio_empty"),
    ("audio not base64", _doc(audios=["not base64!"]), "audio_not_base64"),
    ("audio bad padding", _doc(audios=["AAE"]), "audio_not_base64"),
    ("audio extra padding", _doc(audios=["AAEC="]), "audio_not_base64"),
    ("audio newline inside", _doc(audios=["AA\nEC"]), "audio_not_base64"),
    ("audio trailing newline", _doc(audios=["AAEC\n"]), "audio_not_base64"),
    ("audio space inside", _doc(audios=["AA EC"]), "audio_not_base64"),
    ("audio url-safe alphabet", _doc(audios=["-_-_"]), "audio_not_base64"),
    ("audio data uri", _doc(audios=["data:audio/wav;base64,AAEC"]), "audio_not_base64"),
    ("audio non-ASCII", _doc(audios=["\u00e9AAE"]), "audio_not_base64"),
]


@pytest.mark.parametrize(("label", "body", "reason"), BAD_SHAPES, ids=[b[0] for b in BAD_SHAPES])
def test_sarvam_tts_response_rejects_wrong_shape_and_oversize(
    tmp_path: Path, label: str, body: bytes, reason: str
) -> None:
    """ADR 0024 A2: a 200 reply must be a JSON object with a string ``request_id`` and an
    ``audios`` array of exactly one base64 string; anything else is ``TtsResponseInvalid`` with a
    short reason key, is not retried, makes no usage record and quotes no body."""
    rig = TtsRig(tmp_path, [json_response(body)])
    outcome = rig.call(tts_request(2))
    failure = outcome.failure
    assert type(failure) is core_tts().TtsResponseInvalid, label
    assert outcome.audio is None
    assert outcome.usage == ()
    assert failure.reason == reason
    assert (failure.status, failure.index, failure.attempts) == (200, 2, 1)
    assert len(rig.transport.requests) == 1, "an invalid reply is not retried"
    assert rig.sleeps.waits == []
    assert str(failure).count("{") == 0, "the body is not in the message"
    assert [x["event"] for x in rig.log_lines] == ["before", "after"]


def _exact_rate_cap() -> tuple[int, int]:
    models = models_mod()
    return (
        int(models.tts_max_audio_bytes(8000, "mulaw")),
        int(models.tts_max_response_bytes(8000, "mulaw")),
    )


def test_sarvam_tts_response_rejects_wrong_shape_and_oversize_the_caps_are_these_numbers() -> None:
    """The caps are technical bounds, not measured values (ADR 0024 A2 leaves their value to
    P4-08; the contract fixes them): decoded audio at most 600 s of the requested rate, at 2 bytes
    a sample for ``linear16`` and 1 for ``mulaw``; the JSON body at most the base64 length of that
    plus 4,096 bytes."""
    models = models_mod()
    assert models.TTS_MAX_AUDIO_SECONDS == 600
    for rate in (8000, 16000, 22050, 24000, 32000, 44100, 48000):
        linear = rate * 2 * 600
        assert models.tts_max_audio_bytes(rate, "linear16") == linear
        assert models.tts_max_audio_bytes(rate, "mulaw") == rate * 600
        assert models.tts_max_response_bytes(rate, "linear16") == 4 * -(-linear // 3) + 4096
        assert models.tts_max_response_bytes(rate, "mulaw") == 4 * -(-(rate * 600) // 3) + 4096
    assert models.tts_max_audio_bytes(8000, "mulaw") == 4_800_000
    with pytest.raises(ValueError, match="codec"):
        models.tts_max_audio_bytes(8000, "wav")
    assert models.MAX_RESPONSE_BYTES == 1_048_576, "the STT and chat cap is unchanged"


def test_sarvam_tts_response_rejects_wrong_shape_and_oversize_decoded_audio_over_the_cap(
    tmp_path: Path,
) -> None:
    """One byte more audio than the cap is ``audio_too_large``, checked before the container is
    read; the body that carries it is still within the body cap."""
    audio_cap, body_cap = _exact_rate_cap()
    garbage = b"\x00" * (audio_cap + 1)
    body = _doc(audios=[base64.b64encode(garbage).decode("ascii")])
    assert len(body) <= body_cap
    rig = TtsRig(tmp_path, [json_response(body)])
    outcome = rig.call(tts_request(0, rate=8000, codec="mulaw"))
    assert type(outcome.failure) is core_tts().TtsResponseInvalid
    assert outcome.failure.reason == "audio_too_large"
    assert len(rig.transport.requests) == 1


def test_sarvam_tts_response_rejects_wrong_shape_and_oversize_body_over_the_cap(
    tmp_path: Path,
) -> None:
    """The JSON body is read up to the cap and no further: a body one byte over is
    ``body_too_large``; a body of exactly the cap is read (and then judged on its content)."""
    _audio_cap, body_cap = _exact_rate_cap()
    shell = _doc()

    def padded(total: int) -> bytes:
        return shell + b" " * (total - len(shell))

    over = TtsRig(tmp_path / "over", [json_response(padded(body_cap + 1))])
    failure = over.call(tts_request(0, rate=8000, codec="mulaw")).failure
    assert failure is not None
    assert failure.reason == "body_too_large"
    assert over.sleeps.waits == []
    exact = TtsRig(tmp_path / "exact", [json_response(padded(body_cap))])
    failure = exact.call(tts_request(0, rate=8000, codec="mulaw")).failure
    assert failure is not None
    assert failure.reason != "body_too_large", "a body of exactly the cap is read"


def test_sarvam_tts_response_rejects_wrong_shape_and_oversize_a_failed_reply_leaves_no_audio(
    tmp_path: Path,
) -> None:
    """A refused reply leaves no audio, no usage and no partial state."""
    rig = TtsRig(tmp_path, [json_response(_doc(audios=[]))])
    outcome = rig.call()
    assert outcome.audio is None
    assert outcome.usage == ()
    assert (rig.out).is_dir()
