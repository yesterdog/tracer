# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: PT011
"""P4-27 (the offline half of the WebRTC media path): the ``smallwebrtc/1`` dialect as the call
runner sees it, and the entry point of the media connection (contract
docs/phase4/api-surface-batch-f.md section 3.4).

``test_integration_webrtc_against_pipecat_loopback`` proves the media round trip against real
Pipecat; this file pins what can be pinned without a peer, in the default run, with no socket: the
dialect is a pass-through of 16-bit mono PCM (the WebRTC stack encodes it, so there is no message
format of our own and no opening or closing message), its frame is 20 ms at the dialect rate, a
timing document accepts the dialect and its rate, the modules import without the optional
``[webrtc]`` extra, and ``open_webrtc`` has the shape of ``open_websocket`` and refuses before any
I/O when the extra is absent or the labels are inconsistent. Nothing here pins what ADR 0026 marks
"not verified" (the codec on the wire, the data channel).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import subprocess
import sys
from typing import Any

import pytest

from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]


def dialect_class() -> Any:
    return mod("dialects.smallwebrtc").SmallWebRtcDialect


def frame(rate: int = 48000, fill: int = 7) -> bytes:
    return bytes([fill]) * (rate * 20 // 1000 * 2)


def test_the_dialect_is_a_dialect_of_the_runner_with_the_documented_identity() -> None:
    base = mod("dialects.base")
    webrtc = mod("webrtc")
    d = dialect_class()(sample_rate_hz=48000)
    assert isinstance(d, base.Dialect)
    assert (d.dialect_id, d.wire_codec) == ("smallwebrtc/1", "pcm_s16le")
    assert d.sample_rate_hz == 48000
    assert d.frame_bytes == 1920  # 960 samples of 2 bytes: 20 ms
    assert d.spec_sources == webrtc.SPEC_SOURCES
    assert d.spec_retrieved == webrtc.SPEC_RETRIEVED == "2026-10-27"
    assert d.dialect_id == webrtc.DIALECT_ID


@pytest.mark.parametrize("rate", [8000, 16000, 24000, 48000])
def test_the_dialect_passes_pcm_through_in_either_direction(rate: int) -> None:
    d = dialect_class()(sample_rate_hz=rate)
    pcm = frame(rate)
    assert d.frame_bytes == len(pcm)
    assert d.opening_messages() == ()
    assert d.closing_messages() == ()
    assert d.encode_audio(pcm, stream_ms=0) == pcm
    assert d.encode_audio(pcm, stream_ms=12345) == pcm
    decoded = d.decode(pcm)
    assert decoded == mod("dialects.base").Decoded(kind="audio", audio=pcm)
    # a received frame may be any whole number of samples (the transport decides its size)
    assert d.decode(pcm[:200]).audio == pcm[:200]
    assert d.decode(pcm * 3).audio == pcm * 3


def test_the_dialect_refuses_what_is_not_a_frame_or_not_audio() -> None:
    d = dialect_class()(sample_rate_hz=48000)
    for bad in (b"", b"\x00" * 1919, b"\x00" * 1921, b"\x00" * 3840, "text", None, bytearray(1920)):
        with pytest.raises(ValueError):
            d.encode_audio(bad, stream_ms=0)  # type: ignore[arg-type,unused-ignore]
    # inbound: a text message, an empty message and a half sample are not audio (ignored, counted)
    assert d.decode("hello") is None
    assert d.decode(b"") is None
    assert d.decode(b"\x01") is None
    assert d.decode(b"\x00" * 3) is None


@pytest.mark.parametrize(
    "rate", [0, 7999, 8001, 48050, 96000, -48000, True, 48000.0, "48000", None]
)
def test_the_rate_is_a_whole_number_of_hertz_from_8000_to_48000_in_steps_of_50(rate: Any) -> None:
    with pytest.raises(ValueError):
        dialect_class()(sample_rate_hz=rate)


def test_a_timing_document_accepts_the_dialect_and_its_frames() -> None:
    """The extractor reads the dialect id, the rate and 20 ms frames of 1920 bytes: a reply of ten
    frames from 400 ms after the connection has first audio 400 and a quantum of 20 ms."""
    core = importlib.import_module("tracer_evalcore.simtiming")
    recorder = importlib.import_module("tracer_evalcore.recorder")
    fmt = recorder.RecorderFormat(48000, "pcm_s16le")
    origin = 1_000_000_000
    frames = [
        recorder.LogFrame(
            origin + 400_000_000 + k * 20_000_000, frame(48000), 48000, 1, "pcm_s16le"
        )
        for k in range(10)
    ]
    document = core.extract_timing(
        dialect="smallwebrtc/1",
        fmt=fmt,
        wait_for_agent_silence_ms=300,
        connect_started_ns=origin - 5_000_000,
        connect_ended_ns=origin,
        established=True,
        observed_until_ns=origin + 700_000_000,
        caller_turns=[],
        agent_frames=frames,
    )
    assert document["dialect"] == "smallwebrtc/1"
    assert document["first_agent_audio_ms"] == 400
    assert document["quantum_ms"] == 20
    assert document["setup_ms"] == 5


def test_the_modules_import_without_the_extra_in_a_fresh_interpreter() -> None:
    code = (
        "import sys; sys.modules['aiortc'] = None; sys.modules['av'] = None\n"
        "import tracer_evalsim.webrtc as w\n"
        "import tracer_evalsim.dialects.smallwebrtc as d\n"
        "print(d.SmallWebRtcDialect(sample_rate_hz=48000).dialect_id, hasattr(w, 'open_webrtc'))\n"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["smallwebrtc/1", "True"]


def test_open_webrtc_has_the_shape_of_open_websocket() -> None:
    webrtc = mod("webrtc")
    ws = mod("ws")
    assert inspect.iscoroutinefunction(webrtc.open_webrtc)
    parameters = inspect.signature(webrtc.open_webrtc).parameters
    assert list(parameters) == [
        "plan", "dialect", "labels", "token", "open_timeout_s", "close_timeout_s",
    ]  # fmt: skip
    for name in ("labels", "token", "open_timeout_s", "close_timeout_s"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameters[name].default is inspect.Parameter.empty, (
            name
        )  # no default decides a call
    assert "open_webrtc" in webrtc.__all__
    assert list(inspect.signature(ws.open_websocket).parameters)[:2] == ["target", "dialect"]


def test_open_webrtc_refuses_before_any_io_without_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0026 rule 7: without the extra the connection is a refusal that names the extra, raised
    before any socket (the test guard would turn a connection attempt into an AssertionError)."""
    webrtc, errors, ws = mod("webrtc"), mod("errors"), mod("ws")
    plan = webrtc.make_webrtc_plan("http://127.0.0.1:7860/api/offer", ())
    d = dialect_class()(sample_rate_hz=48000)
    monkeypatch.setitem(sys.modules, "aiortc", None)

    async def attempt() -> None:
        await webrtc.open_webrtc(
            plan, d, labels=ws.ConnectionLabels(None, None, None), token=None,
            open_timeout_s=1.0, close_timeout_s=1.0,
        )  # fmt: skip

    with pytest.raises(errors.TargetRefusedError) as raised:
        asyncio.run(attempt())
    assert raised.value.code == "webrtc_extra_missing"
    assert "tracer-evalsim[webrtc]" in str(raised.value)


def test_open_webrtc_refuses_inconsistent_labels_and_other_dialects_before_any_io() -> None:
    webrtc, errors, ws = mod("webrtc"), mod("errors"), mod("ws")
    plan = webrtc.make_webrtc_plan("http://127.0.0.1:7860/api/offer", ())
    d = dialect_class()(sample_rate_hz=48000)

    async def attempt(dialect: Any, labels: Any, token: Any = None) -> None:
        await webrtc.open_webrtc(
            plan, dialect, labels=labels, token=token, open_timeout_s=1.0, close_timeout_s=1.0
        )

    with pytest.raises(errors.TargetRefusedError) as raised:  # a telephony label on WebRTC
        asyncio.run(attempt(d, ws.ConnectionLabels(None, None, False)))
    assert raised.value.code == "labels_inconsistent"
    with pytest.raises(errors.TargetRefusedError) as other:  # the wrong dialect for the transport
        asyncio.run(
            attempt(
                mod("dialects.twilio").TwilioMediaStreamDialect(counter=1),
                ws.ConnectionLabels(None, None, None),
            )
        )
    assert other.value.code == "dialect_unknown"
    with pytest.raises(errors.TargetRefusedError) as named:  # a token variable without a token
        asyncio.run(attempt(d, ws.ConnectionLabels("AGENT_BEARER", None, None)))
    assert named.value.code == "auth_token_missing"
