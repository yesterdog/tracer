# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-08, the audio container of a TTS reply: the tests that depend on an assumption.

ADR 0024 A2 requires that the decoded bytes be "audio of the requested rate, one channel and the
requested codec" and says that WHICH CONTAINER the documented reply uses for each codec is not
verified (the reviewer read "base64 WAV"); it is fixed from the first owner-gated reply. These
tests therefore assume, and ONLY here, that a ``linear16`` reply is a canonical RIFF/WAVE file
(PCM, one channel, the requested rate, 16 bits) whose data chunk is the audio, and that anything
else is refused (fail closed). The ``mulaw`` reply container is not pinned at all. If the first
real reply shows another container, this file and ``wav_bytes`` in the support module are the only
places that change (contract section 2.5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    TtsRig,
    core_tts,
    models_mod,
    tts_reply,
    tts_request,
    tts_samples,
    wav_bytes,
)

RATES = [8000, 16000, 22050, 24000, 32000, 44100, 48000]


@pytest.mark.parametrize("rate", RATES)
def test_sarvam_tts_container_linear16_wav_is_unwrapped_to_its_samples(
    tmp_path: Path, rate: int
) -> None:
    """ASSUMPTION (container, ADR 0024 A2): a linear16 reply is a canonical WAV; the engine
    returns its data chunk."""
    rig = TtsRig(tmp_path, [tts_reply("wav-ok", rate=rate, frames=rate // 50, seed=3)])
    outcome = rig.call(tts_request(0, rate=rate))
    assert outcome.failure is None
    assert type(outcome.audio) is bytes
    assert outcome.audio == tts_samples(rate // 50, seed=3), "the data chunk, nothing else"
    assert len(outcome.usage) == 1


@pytest.mark.parametrize(
    ("label", "payload", "reason"),
    [
        ("other rate", wav_bytes(tts_samples(), 8000), "audio_format_mismatch"),
        ("other rate 24k", wav_bytes(tts_samples(), 24000), "audio_format_mismatch"),
        ("two channels", wav_bytes(tts_samples(), 16000, channels=2), "audio_format_mismatch"),
        ("8 bit", wav_bytes(tts_samples(), 16000, bits=8), "audio_format_mismatch"),
        ("mu-law format tag", wav_bytes(tts_samples(), 16000, fmt=7), "audio_format_mismatch"),
        (
            "float format tag",
            wav_bytes(tts_samples(), 16000, bits=32, fmt=3),
            "audio_format_mismatch",
        ),
        ("raw samples, no header", tts_samples(), "audio_not_recognised"),
        ("not RIFF", b"RIFX" + wav_bytes(tts_samples(), 16000)[4:], "audio_not_recognised"),
        (
            "not WAVE",
            wav_bytes(tts_samples(), 16000)[:8] + b"AVI " + b"x" * 40,
            "audio_not_recognised",
        ),
        ("truncated header", wav_bytes(tts_samples(), 16000)[:43], "audio_not_recognised"),
        (
            "data chunk shorter than its size",
            wav_bytes(tts_samples(), 16000)[:-10],
            "audio_not_recognised",
        ),
        ("odd data length", wav_bytes(tts_samples()[:-1], 16000), "audio_not_recognised"),
        ("no samples", wav_bytes(b"", 16000), "audio_empty"),
    ],
)
def test_sarvam_tts_container_refuses_audio_that_is_not_the_requested_one(
    tmp_path: Path, label: str, payload: bytes, reason: str
) -> None:
    """ASSUMPTION (container): audio of another rate, width or channel count, or not a WAV, is
    refused (fail closed)."""
    rig = TtsRig(tmp_path, [tts_reply(audio=payload)])
    outcome = rig.call(tts_request(1, rate=16000))
    assert type(outcome.failure) is core_tts().TtsResponseInvalid, label
    assert outcome.failure.reason == reason
    assert outcome.audio is None
    assert outcome.usage == ()
    assert len(rig.transport.requests) == 1, "a wrong container is not retried"


def test_sarvam_tts_container_decoded_audio_of_exactly_the_cap_is_accepted(
    tmp_path: Path,
) -> None:
    """The decoded-audio cap is exact: the cap is read, one byte more is ``audio_too_large``."""
    models = models_mod()
    cap = models.tts_max_audio_bytes(8000, "linear16")
    assert cap == 9_600_000
    samples = b"\x01\x00" * ((cap - 44) // 2)
    payload = wav_bytes(samples, 8000)
    assert len(payload) == cap
    rig = TtsRig(tmp_path, [tts_reply(audio=payload)])
    outcome = rig.call(tts_request(0, rate=8000))
    assert outcome.failure is None
    assert outcome.audio == samples
    assert len(rig.transport.requests[0].content) < 1000
    one_over = TtsRig(tmp_path / "over", [tts_reply(audio=payload + b"\x00")])
    failure = one_over.call(tts_request(0, rate=8000)).failure
    assert failure is not None
    assert failure.reason == "audio_too_large"


def test_sarvam_tts_container_members_the_adapter_does_not_know_are_ignored(
    tmp_path: Path,
) -> None:
    """ADR 0024 rule 8: unknown members are kept out of every record."""
    extra: dict[str, Any] = {
        "model": "named-by-the-server",
        "audio_duration": 1.5,
        "x": {"a": [1, 2, 3]},
    }
    rig = TtsRig(tmp_path, [tts_reply("extra-ok", extra=extra)])
    outcome = rig.call()
    assert outcome.failure is None
    dumped = repr(outcome) + "".join(u.model_dump_json() for u in outcome.usage)
    assert "named-by-the-server" not in dumped
    assert "audio_duration" not in dumped


def test_sarvam_tts_container_an_unusual_request_id_is_accepted_but_never_logged(
    tmp_path: Path,
) -> None:
    """An id that is not a plain identifier is kept in the record but never written to the log."""
    rig = TtsRig(tmp_path, [tts_reply("has space\nand newline")])
    outcome = rig.call()
    assert outcome.failure is None
    assert [x["request_id"] for x in rig.log_lines if x["event"] == "after"] == [None]
    assert "newline" not in rig.log_text


def test_sarvam_tts_container_the_json_body_may_be_pretty_printed_unicode(tmp_path: Path) -> None:
    """JSON whitespace and non-ASCII are legal; the reader is a JSON reader, not a byte matcher."""
    import base64
    import json

    from evalproviders_support import json_response  # type: ignore[import-not-found,unused-ignore]

    payload = base64.b64encode(wav_bytes(tts_samples(), 16000)).decode("ascii")
    body = json.dumps(
        {"request_id": "\u00e9-id", "audios": [payload]}, indent=2, ensure_ascii=False
    )
    rig = TtsRig(tmp_path, [json_response(body.encode("utf-8"))])
    assert rig.call().failure is None


def test_sarvam_tts_container_a_request_id_of_256_characters_is_accepted(tmp_path: Path) -> None:
    """The id is kept in a usage record: 256 characters is the longest one (a hostile reply cannot
    carry a huge string into a record); 257 is ``request_id_invalid`` (response test)."""
    rig = TtsRig(tmp_path, [tts_reply("x" * 256)])
    outcome = rig.call()
    assert outcome.failure is None
    assert outcome.usage[0].provider_call_ref.value == "x" * 256


def test_sarvam_tts_container_the_request_id_falls_back_to_the_header_for_the_log(
    tmp_path: Path,
) -> None:
    """ADR 0024 rule 15 (as the STT adapter): when the body's id is not a plain identifier the
    log takes the ``x-request-id`` header if that is one; a plain body id wins over the header."""
    rig = TtsRig(
        tmp_path,
        [
            tts_reply("has space", headers={"x-request-id": "hdr-ok-1"}),
            tts_reply("body-id-2", headers={"x-request-id": "hdr-ok-2"}),
            tts_reply("has space", headers={"x-request-id": "not plain"}),
        ],
    )
    for index in range(3):
        assert rig.call(tts_request(index)).failure is None
    afters = [x["request_id"] for x in rig.log_lines if x["event"] == "after"]
    assert afters == ["hdr-ok-1", "body-id-2", None]
