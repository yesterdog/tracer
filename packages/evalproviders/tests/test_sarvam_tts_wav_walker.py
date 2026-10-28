# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The Sarvam TTS reply container after the first real call (2026-10-28).

Observed: ``output_audio_codec: "linear16"`` returned HEADERLESS 16-bit PCM (no RIFF header), while
the documentation says the field ``audios`` holds WAV files and the codec default is ``wav``. The
adapter therefore REQUESTS ``wav`` for the domain codec ``linear16`` and reads a RIFF/WAVE
container with a chunk walker; the domain codec (cache key, usage, the audio handed to callers)
stays ``linear16`` and the audio stays the bare PCM payload. Every WAV here is built from synthetic
bytes by ``chunk`` and ``riff``; no recorded audio is used.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    TtsRig,
    cache_mod,
    core_tts,
    tts_reply,
    tts_request,
    tts_samples,
)

RATE = 16000
STREAMED = 0xFFFFFFFF


def chunk(tag: bytes, body: bytes, *, size: int | None = None, pad: bool = True) -> bytes:
    declared = len(body) if size is None else size
    out = tag + struct.pack("<I", declared) + body
    if pad and len(body) % 2:
        out += b"\x00"
    return out


def fmt16(rate: int = RATE, *, channels: int = 1, bits: int = 16, tag: int = 1) -> bytes:
    block = channels * bits // 8
    return chunk(b"fmt ", struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits))


def fmt18(rate: int = RATE) -> bytes:
    body = struct.pack("<HHIIHHH", 1, 1, rate, rate * 2, 2, 16, 0)
    return chunk(b"fmt ", body)


def riff(*chunks: bytes, size: int | None = None) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) if size is None else size) + body


def synth(frames: int = 320) -> bytes:
    samples: bytes = tts_samples(frames)
    return samples


def call(tmp_path: Path, audio: bytes) -> object:
    rig = TtsRig(tmp_path, [tts_reply(audio=audio)])
    outcome = rig.call(tts_request(0, rate=RATE))
    assert len(rig.transport.requests) == 1
    return outcome


def assert_refused(outcome: object, reason: str) -> None:
    failure = outcome.failure  # type: ignore[attr-defined]
    assert type(failure) is core_tts().TtsResponseInvalid
    assert failure.reason == reason
    assert outcome.audio is None  # type: ignore[attr-defined]
    assert outcome.usage == ()  # type: ignore[attr-defined]


# ------------------------------------------------------------------------------ the request


def test_wav_walker_request_asks_for_wav_for_the_domain_codec_linear16(tmp_path: Path) -> None:
    """The wire codec for ``linear16`` is ``wav``; ``mulaw`` is sent as it is (UNVERIFIED)."""
    rig = TtsRig(tmp_path, [tts_reply()])
    rig.call(tts_request(0))
    assert json.loads(rig.transport.requests[0].content)["output_audio_codec"] == "wav"


def test_wav_walker_mulaw_request_is_unchanged(tmp_path: Path) -> None:
    rig = TtsRig(tmp_path, [tts_reply(rate=8000)])
    rig.call(tts_request(0, rate=8000, codec="mulaw"))
    assert json.loads(rig.transport.requests[0].content)["output_audio_codec"] == "mulaw"


def test_wav_walker_the_cache_key_keeps_the_domain_codec_and_the_entry_is_the_pcm(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    cache = cache_mod().TtsCache.open(out)
    rig = TtsRig(tmp_path, [tts_reply(frames=100, seed=3)], cache=cache)
    request = tts_request(0)
    voice = request.voice
    key = cache_mod().tts_cache_key(
        provider=voice.provider,
        model=voice.model,
        speaker=voice.speaker,
        language_code=voice.language_code,
        sample_rate_hz=request.sample_rate_hz,
        codec="linear16",
        pace=voice.pace,
        text=request.text,
    )
    outcome = rig.call(request)
    assert outcome.failure is None
    assert (out / "tts-cache" / key).read_bytes() == tts_samples(100, seed=3)
    assert rig.call(tts_request(1)).audio == tts_samples(100, seed=3)
    assert len(rig.transport.requests) == 1


# ------------------------------------------------------------------------------ accepted shapes


@pytest.mark.parametrize(
    ("label", "build"),
    [
        (
            "LIST chunk before data",
            lambda s: riff(fmt16(), chunk(b"LIST", b"INFOabc"), chunk(b"data", s)),
        ),
        (
            "LIST chunk of even size",
            lambda s: riff(chunk(b"LIST", b"INFOab"), fmt16(), chunk(b"data", s)),
        ),
        ("fmt of size 18", lambda s: riff(fmt18(), chunk(b"data", s))),
        ("data before fmt", lambda s: riff(chunk(b"data", s), fmt16())),
        (
            "chunk after data",
            lambda s: riff(fmt16(), chunk(b"data", s), chunk(b"LIST", b"INFOabc")),
        ),
        (
            "fact chunk",
            lambda s: riff(fmt16(), chunk(b"fact", b"\x00\x00\x00\x00"), chunk(b"data", s)),
        ),
        ("RIFF size field zero", lambda s: riff(fmt16(), chunk(b"data", s), size=0)),
        ("RIFF size field wrong", lambda s: riff(fmt16(), chunk(b"data", s), size=12345)),
    ],
)
def test_wav_walker_accepts_chunks_in_any_order_and_skips_unknown_ones(
    tmp_path: Path, label: str, build: object
) -> None:
    samples = synth()
    outcome = call(tmp_path, build(samples))  # type: ignore[operator]
    assert outcome.failure is None, label  # type: ignore[attr-defined]
    assert outcome.audio == samples  # type: ignore[attr-defined]
    assert type(outcome.audio) is bytes  # type: ignore[attr-defined]
    assert len(outcome.usage) == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize("marker", [0, STREAMED])
def test_wav_walker_accepts_a_streamed_size_when_the_rest_is_the_even_data(
    tmp_path: Path, marker: int
) -> None:
    samples = synth()
    audio = riff(fmt16(), chunk(b"data", samples, size=marker), size=marker)
    outcome = call(tmp_path, audio)
    assert outcome.failure is None  # type: ignore[attr-defined]
    assert outcome.audio == samples  # type: ignore[attr-defined]


@pytest.mark.parametrize("marker", [0, STREAMED])
def test_wav_walker_refuses_a_streamed_size_with_an_odd_rest(tmp_path: Path, marker: int) -> None:
    audio = riff(fmt16(), chunk(b"data", synth()[:-1], size=marker, pad=False), size=marker)
    assert_refused(call(tmp_path, audio), "audio_not_recognised")


def test_wav_walker_a_streamed_data_size_is_refused_when_a_chunk_follows_it_unreadable(
    tmp_path: Path,
) -> None:
    """With a streamed size the data is the rest of the buffer, so a chunk after it is not a
    chunk: the odd or the trailing bytes make the data wrong, never a silent guess."""
    audio = riff(fmt16(), chunk(b"data", synth() + b"xyz", size=STREAMED, pad=False))
    assert_refused(call(tmp_path, audio), "audio_not_recognised")


# ------------------------------------------------------------------------------ refused shapes


def test_wav_walker_refuses_headerless_bytes_as_not_recognised(tmp_path: Path) -> None:
    """The real shape seen on 2026-10-28 for ``linear16``: PCM that starts with zero samples."""
    headerless = b"\x00\x00\x00\x00" + synth()
    assert_refused(call(tmp_path, headerless), "audio_not_recognised")


@pytest.mark.parametrize(
    ("label", "build", "reason"),
    [
        ("wrong rate", lambda s: riff(fmt16(8000), chunk(b"data", s)), "audio_format_mismatch"),
        (
            "wrong rate, fmt 18",
            lambda s: riff(fmt18(24000), chunk(b"data", s)),
            "audio_format_mismatch",
        ),
        ("8 bit", lambda s: riff(fmt16(bits=8), chunk(b"data", s)), "audio_format_mismatch"),
        ("stereo", lambda s: riff(fmt16(channels=2), chunk(b"data", s)), "audio_format_mismatch"),
        (
            "float tag",
            lambda s: riff(fmt16(tag=3, bits=32), chunk(b"data", s)),
            "audio_format_mismatch",
        ),
        (
            "extensible tag",
            lambda s: riff(fmt16(tag=0xFFFE), chunk(b"data", s)),
            "audio_format_mismatch",
        ),
        (
            "no data chunk",
            lambda s: riff(fmt16(), chunk(b"LIST", b"INFOabc")),
            "audio_not_recognised",
        ),
        ("no fmt chunk", lambda s: riff(chunk(b"data", s)), "audio_not_recognised"),
        (
            "two fmt chunks",
            lambda s: riff(fmt16(), fmt16(), chunk(b"data", s)),
            "audio_not_recognised",
        ),
        (
            "two data chunks",
            lambda s: riff(fmt16(), chunk(b"data", s), chunk(b"data", s)),
            "audio_not_recognised",
        ),
        (
            "fmt of size 40",
            lambda s: riff(chunk(b"fmt ", b"\x01\x00\x01\x00" + b"\x00" * 36), chunk(b"data", s)),
            "audio_not_recognised",
        ),
        (
            "fmt of size 12",
            lambda s: riff(chunk(b"fmt ", b"\x01\x00\x01\x00" + b"\x00" * 8), chunk(b"data", s)),
            "audio_not_recognised",
        ),
        (
            "odd data length",
            lambda s: riff(fmt16(), chunk(b"data", s[:-1])),
            "audio_not_recognised",
        ),
        ("empty data", lambda s: riff(fmt16(), chunk(b"data", b"")), "audio_empty"),
        (
            "data longer than the bytes",
            lambda s: riff(fmt16(), chunk(b"data", s, size=len(s) + 100)),
            "audio_not_recognised",
        ),
        ("truncated data", lambda s: riff(fmt16(), chunk(b"data", s))[:-7], "audio_not_recognised"),
        (
            "truncated chunk header",
            lambda s: riff(fmt16(), chunk(b"data", s))[:20],
            "audio_not_recognised",
        ),
        ("only the RIFF header", lambda s: b"RIFF\x04\x00\x00\x00WAVE", "audio_not_recognised"),
        (
            "trailing garbage",
            lambda s: riff(fmt16(), chunk(b"data", s)) + b"garbage!",
            "audio_not_recognised",
        ),
        (
            "one stray byte after an even chunk",
            lambda s: riff(fmt16(), chunk(b"data", s)) + b"\x00",
            "audio_not_recognised",
        ),
        (
            "two trailing bytes",
            lambda s: riff(fmt16(), chunk(b"data", s)) + b"\x00\x00",
            "audio_not_recognised",
        ),
        (
            "not RIFF",
            lambda s: b"RIFX" + riff(fmt16(), chunk(b"data", s))[4:],
            "audio_not_recognised",
        ),
        (
            "not WAVE",
            lambda s: riff(fmt16(), chunk(b"data", s))[:8] + b"AVI " + b"x" * 40,
            "audio_not_recognised",
        ),
    ],
)
def test_wav_walker_refuses_what_is_not_the_requested_audio(
    tmp_path: Path, label: str, build: object, reason: str
) -> None:
    outcome = call(tmp_path, build(synth()))  # type: ignore[operator]
    assert_refused(outcome, reason)


def test_wav_walker_one_pad_byte_after_an_odd_final_chunk_is_accepted(tmp_path: Path) -> None:
    samples = synth()
    audio = riff(fmt16(), chunk(b"data", samples), chunk(b"LIST", b"INFOabc"))
    assert len(audio) % 2 == 0, "the pad byte is part of the final chunk"
    outcome = call(tmp_path, audio)
    assert outcome.failure is None  # type: ignore[attr-defined]
    assert outcome.audio == samples  # type: ignore[attr-defined]
    unpadded = call(tmp_path / "again", audio[:-1])
    assert unpadded.failure is None, "a missing final pad byte is tolerated"  # type: ignore[attr-defined]
    assert unpadded.audio == samples  # type: ignore[attr-defined]


def test_wav_walker_a_hostile_chunk_size_cannot_loop_or_overread(tmp_path: Path) -> None:
    audio = riff(fmt16(), chunk(b"LIST", b"x", size=STREAMED), chunk(b"data", synth()))
    assert_refused(call(tmp_path, audio), "audio_not_recognised")
