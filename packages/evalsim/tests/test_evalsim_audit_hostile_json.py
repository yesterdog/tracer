# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-352 / audit A-F3: a hostile agent cannot crash the run with deeply nested input.

Contract: docs/phase4/api-surface-batch-g2.md section 1. ``json.loads`` of 60000 ``[`` characters
(60 KB, inside the 64 KiB cap of ``MAX_INBOUND_MESSAGE_BYTES``) raises ``RecursionError``, which is
not a ``ValueError``. The twilio dialect used to catch only ``ValueError``, so one message from the
agent ended the whole run (exit 3) with the recording and the bundle lost. A message that cannot be
parsed is an IGNORED message, whatever the reason it cannot be parsed (section 1.1), and the call
goes on or ends by its normal reason (section 1.3). ``BaseException`` is never swallowed (1.2).

The other readers of agent-controlled bytes are covered too: the protobuf codec (flat, no
recursion), the webrtc data frame (no parser) and the signalling answer of the webrtc dialect
(a JSON body of up to 131072 bytes, section 1.4). No test opens a socket.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    HangUp,
    Send,
    Speak,
    Wait,
)
from evalsim_support_clock import ManualClock  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import (  # type: ignore[import-not-found,unused-ignore]
    COUNT_KEYS,
    dialect_of,
    lines,
    play,
)

#: strings that make ``json.loads`` raise ``RecursionError`` (checked below), all under 64 KiB
ARRAYS = "[" * 60000
OBJECTS = '{"a":' * 12000
MEDIA_THEN_ARRAYS = '{"event":"media","media":' + "[" * 60000
ARRAYS_AT_THE_CAP = "[" * 65536
HOSTILE_TEXT = {
    "arrays": ARRAYS,
    "objects": OBJECTS,
    "media_then_arrays": MEDIA_THEN_ARRAYS,
    "arrays_at_the_cap": ARRAYS_AT_THE_CAP,
}


def twilio() -> Any:
    return mod("dialects.twilio").TwilioMediaStreamDialect(counter=1)


def test_the_hostile_strings_are_inside_the_cap_and_the_premise_holds_on_this_interpreter() -> None:
    """BL-352 / A-F3: the premise of the audit, proved on this interpreter and not assumed. Every
    shape is inside the 64 KiB cap and none is parsed; the depth at which the C scanner gives up
    differs between interpreters (on 3.14 the object shape of 12000 levels is a plain
    ``JSONDecodeError``), so the premise is that the shapes at the cap, arrays among them, raise
    ``RecursionError`` here: a run where none does would prove nothing."""
    raised: dict[str, str] = {}
    for name, text in HOSTILE_TEXT.items():
        assert len(text.encode("utf-8")) <= mod("limits").MAX_INBOUND_MESSAGE_BYTES, name
        with pytest.raises((RecursionError, ValueError)) as caught:
            json.loads(text)
        raised[name] = type(caught.value).__name__
    assert raised["arrays_at_the_cap"] == "RecursionError", raised
    assert raised["arrays"] == "RecursionError", raised
    assert raised["media_then_arrays"] == "RecursionError", raised


@pytest.mark.parametrize("name", sorted(HOSTILE_TEXT))
def test_twilio_decode_ignores_a_message_that_json_cannot_parse_for_any_reason(name: str) -> None:
    """BL-352 / A-F3 (section 1.1): ``decode`` returns ``None`` (an ignored message) and does not
    raise, for a nesting that overflows the parser."""
    assert twilio().decode(HOSTILE_TEXT[name]) is None


@pytest.mark.parametrize("depth", [1, 100, 900, 1100, 5000, 60000])
def test_twilio_decode_ignores_nested_arrays_at_every_depth(depth: int) -> None:
    """BL-352 / A-F3: well-formed nesting (a list, not an object) is ignored at any depth, with or
    without a recursion error inside the parser."""
    message = "[" * depth + "]" * depth
    assert twilio().decode(message) is None


def test_twilio_decode_still_reads_a_good_message_after_a_hostile_one() -> None:
    """BL-352 / A-F3: the parser state is not poisoned by the overflow: the next media message of
    the same dialect object decodes."""
    dialect = twilio()
    assert dialect.decode(ARRAYS) is None
    good = '{"event":"media","media":{"payload":"VVVV"}}'
    decoded = dialect.decode(good)
    assert (decoded.kind, decoded.audio) == ("audio", b"UUU")
    assert dialect.decode('{"event":"clear"}').kind == "clear"


def test_twilio_decode_treats_any_exception_of_the_parser_as_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BL-352 / A-F3 (section 1.1): any ``Exception`` of ``json.loads`` (a recursion error, a
    memory error, an error of a future parser) is an ignored message."""
    dialect = twilio()
    for error in (RecursionError("deep"), MemoryError("big"), RuntimeError("other"), OSError("x")):

        def boom(*args: Any, error: Exception = error, **kwargs: Any) -> Any:
            raise error

        monkeypatch.setattr("tracer_evalsim.dialects.twilio.json.loads", boom)
        assert dialect.decode('{"event":"clear"}') is None, type(error).__name__


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_twilio_decode_never_swallows_a_base_exception(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    """BL-352 / A-F3 (section 1.2): a Ctrl-C, an exit or a cancel is not an ignored message."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise error()

    monkeypatch.setattr("tracer_evalsim.dialects.twilio.json.loads", boom)
    with pytest.raises(error):
        twilio().decode('{"event":"clear"}')


def test_inbound_reader_counts_a_hostile_message_as_ignored_and_goes_on() -> None:
    """BL-352 / A-F3 (section 1.3): ``InboundReader.feed`` returns ``None``, counts one ignored
    message and one message in the rate window, and reads the next message normally."""
    reader = mod("dialects.base").InboundReader(twilio(), ManualClock())
    assert reader.feed(ARRAYS) is None
    assert reader.feed(OBJECTS) is None
    event = reader.feed('{"event":"clear"}')
    assert event is not None
    assert event.kind == "clear"
    stats = reader.stats
    assert (stats.messages, stats.ignored) == (3, 2)
    assert stats.by_kind == {"clear": 1}


def _steps(hostile: Any) -> list[Any]:
    return [Wait(400), Send(hostile), Wait(100), Speak(5)]


@pytest.mark.parametrize("name", sorted(HOSTILE_TEXT))
def test_a_hostile_nested_message_does_not_end_a_twilio_call(name: str) -> None:
    """BL-352 / A-F3 (section 1.3), end to end through the runner with a fake agent: the call ends
    by the same reason as without the message, the message is the one ignored message, the audio
    around it is received, and no exception leaves ``run_call``."""
    dialect = dialect_of("twilio")
    base = play("twilio", [Wait(400), Wait(100), Speak(5)], lines(dialect, 10))
    hostile = play("twilio", _steps(HOSTILE_TEXT[name]), lines(dialect, 10))
    assert hostile.result.end_reason == base.result.end_reason == "script_complete"
    assert hostile.result.failure_code is None
    assert hostile.result.counts["inbound_messages_ignored"] == 1
    assert base.result.counts["inbound_messages_ignored"] == 0
    assert hostile.result.counts["frames_received"] == base.result.counts["frames_received"] == 5
    assert set(hostile.result.counts) == set(COUNT_KEYS)
    assert hostile.agent_ms() == base.agent_ms()


def test_a_hostile_nested_message_followed_by_a_hang_up_is_an_agent_hangup() -> None:
    """BL-352 / A-F3: the normal reason of a call whose agent then hangs up is kept."""
    dialect = dialect_of("twilio")
    played = play(
        "twilio", [Wait(100), Send(ARRAYS), Send(OBJECTS), HangUp(1000)], lines(dialect, 10)
    )
    assert played.result.end_reason == "agent_hangup"
    assert played.result.counts["inbound_messages_ignored"] == 2
    assert played.result.call_established is True


def test_hostile_nested_input_before_the_first_caller_line_does_not_crash_the_runner() -> None:
    """BL-352 / A-F3: the message arrives while the runner waits, with nothing sent yet."""
    dialect = dialect_of("twilio")
    played = play(
        "twilio",
        [Send(ARRAYS), Send(MEDIA_THEN_ARRAYS), Wait(200)],
        lines(dialect, 3),
        caller_first=False,
    )
    assert played.result.call_established is True
    assert played.result.counts["inbound_messages_ignored"] == 2
    assert played.result.end_reason in ("max_silence", "script_complete", "agent_hangup")


# --------------------------------------------------------------------- the other readers


def test_protobuf_decode_ignores_hostile_bytes_without_recursion() -> None:
    """BL-352 / A-F3 (section 1.4): the codec is flat (no nested message is parsed), so every
    shape that is hostile for a recursive parser is an ordinary refusal: ``None``, never an
    exception of another class."""
    dialect = dialect_of("protobuf")
    shapes = [
        b"[" * 60000,  # 0x5B is a start-group tag: refused
        b"\x0a" * 60000,  # a length-delimited field of length 10, repeated, runs past the end
        b"\x0a\x00" * 30000,  # the Text member (1) with an empty body, 30000 times
        b"\x12\x00" * 30000,  # the Audio member (2), empty, 30000 times
        b"\x0b" * 60000,  # start group
        b"\xff" * 65536,  # a varint that never ends
        bytes(range(256)) * 250,
    ]
    for shape in shapes:
        assert len(shape) <= mod("limits").MAX_INBOUND_MESSAGE_BYTES
        try:
            decoded = dialect.decode(shape)
        except mod("errors").TransportError:
            continue  # a stated transport refusal (a format mismatch) is an outcome, not a crash
        assert decoded is None or decoded.kind in ("audio", "text", "interruption", "message")


def test_a_hostile_protobuf_message_does_not_end_the_call_with_an_exception() -> None:
    """BL-352 / A-F3 (section 1.4), end to end on the binary dialect."""
    dialect = dialect_of("protobuf")
    base = play("protobuf", [Wait(400), Wait(100), Speak(5)], lines(dialect, 10))
    hostile = play(
        "protobuf",
        [Wait(400), Send(b"[" * 60000), Send(b"\x0a" * 60000), Wait(100), Speak(5)],
        lines(dialect, 10),
    )
    assert hostile.result.end_reason == base.result.end_reason
    assert hostile.result.failure_code is None
    assert hostile.result.counts["inbound_messages_ignored"] == 2
    assert hostile.result.counts["frames_received"] == 5


def test_the_webrtc_data_frame_has_no_parser_to_overflow() -> None:
    """BL-352 / A-F3 (section 1.4): a ``smallwebrtc/1`` message is raw PCM, decoded by length only:
    text, odd lengths and hostile-looking bytes are ignored or taken as audio, never parsed."""
    webrtc = mod("dialects.smallwebrtc").SmallWebRtcDialect(sample_rate_hz=48000)
    assert webrtc.decode("[" * 60000) is None
    assert webrtc.decode(b"[" * 60001) is None  # odd length
    assert webrtc.decode(b"") is None
    taken = webrtc.decode(b"[" * 60000)
    assert taken is not None
    assert taken.kind == "audio"
    assert len(taken.audio or b"") == 60000


def _signalling(monkeypatch: pytest.MonkeyPatch, body: bytes, status: int = 200) -> Any:
    webrtc = mod("webrtc")

    def fake_request(*args: Any, **kwargs: Any) -> tuple[str | None, int | None, bytes]:
        return None, status, body

    monkeypatch.setattr(webrtc, "_request", fake_request)
    plan = webrtc.make_webrtc_plan("http://127.0.0.1:7860/api/offer", ())
    labels = mod("ws").ConnectionLabels(
        auth_token_env=None, origin=None, bot_holds_telephony_credentials=None
    )
    return webrtc.HttpSignalling(plan, labels=labels, token=None, timeout_s=1.0)


@pytest.mark.parametrize(
    "body",
    [b"[" * 100000, b'{"a":' * 20000, b"[" * 131072],
    ids=["arrays", "objects", "arrays_at_the_cap"],
)
def test_a_deeply_nested_signalling_answer_is_an_invalid_answer(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    """BL-352 / A-F3 (section 1.4): the answer of the agent's signalling endpoint is hostile input
    too. A body that overflows the parser is ``SetupFailedError(code="answer_invalid")`` (the end
    reason ``setup_failed``), the same as any body that is not a JSON object."""
    assert len(body) <= mod("limits").MAX_SIGNALLING_RESPONSE_BYTES
    client = _signalling(monkeypatch, body)
    for call in (client.post_offer, client.patch_candidates):
        with pytest.raises(mod("errors").SetupFailedError) as raised:
            asyncio.run(call({"sdp": "v=0", "type": "offer"}))
        assert raised.value.code == "answer_invalid"
        assert raised.value.END_REASON == "setup_failed"
