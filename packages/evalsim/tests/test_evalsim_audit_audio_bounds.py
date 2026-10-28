# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-353 / audit A-F4: the agent audio is bounded by the length of the call and by a byte rate.

Contract: docs/phase4/api-surface-batch-g2.md section 2. The total cap was 256 MiB for a call that
carries about 5.8 MB in 180 s, and the rate cap counted messages (1000 per second), not bytes: a
hostile agent could keep 64 KiB messages at that rate, reach the cap in about 4 s and make the
runner hold about 1 GiB (the frame, and a decoded copy). Now:

* 2.1 ``Dialect.realtime_bytes_per_second``: the wire audio of one second of speech (the measured
  rates of a real Pipecat 1.14.0 are 32000 B/s for protobuf at 16 kHz, 8000 B/s for twilio mu-law
  and 96000 B/s for smallwebrtc at 48 kHz, each exactly 1.0 times real time);
* 2.2 ``limits.agent_audio_cap_bytes``: that rate x (call length + the 1 s watchdog grace) x 4,
  never
  below 1 MiB and never above the 256 MiB constant or the ceiling the caller passes;
* 2.3 ``InboundReader``: a sliding one-second window of message BYTES, 1 MiB, error code
  ``inbound_bytes_exceeded``;
* 2.4 the runner uses both and ends a hostile call with a named code, holding a counted number of
  bytes (asserted on the frame log of the result, never on RSS).

No test opens a socket.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from evalsim_support_agents import (  # type: ignore[import-not-found,unused-ignore]
    Send,
    Wait,
    ref_encode,
)
from evalsim_support_clock import START_NS, ManualClock  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import (  # type: ignore[import-not-found,unused-ignore]
    dialect_of,
    lines,
    play,
)

NS = 1_000_000_000
MAX_BYTES_PER_SECOND = 1_048_576
MIB = 1_048_576
#: the rates measured against a real Pipecat 1.14.0 (batch F): bytes of audio per message, and the
#: interval of the messages in milliseconds
MEASURED = {
    "protobuf": (1280, 40),  # 16 kHz pcm_s16le
    "twilio": (320, 40),  # 8 kHz mulaw
    "webrtc": (1920, 20),  # 48 kHz pcm_s16le
}


def limits() -> Any:
    return mod("limits")


def webrtc_dialect() -> Any:
    return mod("dialects.smallwebrtc").SmallWebRtcDialect(sample_rate_hz=48000)


def dialect_for(name: str) -> Any:
    return webrtc_dialect() if name == "webrtc" else dialect_of(name)


# ------------------------------------------------------------------------------------ 2.1


@pytest.mark.parametrize("name", sorted(MEASURED))
def test_realtime_bytes_per_second_equals_the_rate_measured_against_pipecat(name: str) -> None:
    """BL-353 / A-F4 (2.1): the per-second wire rate of each dialect is the measured one."""
    size, interval_ms = MEASURED[name]
    dialect = dialect_for(name)
    assert dialect.realtime_bytes_per_second == size * 1000 // interval_ms
    assert dialect.realtime_bytes_per_second == dialect.frame_bytes * 50


def test_realtime_bytes_per_second_follows_the_sample_rate_and_the_codec() -> None:
    """BL-353 / A-F4 (2.1): 2 bytes per sample for pcm_s16le, 1 for mulaw."""
    protobuf = mod("dialects.protobuf_dialect").PipecatProtobufDialect
    assert protobuf(sample_rate_hz=8000).realtime_bytes_per_second == 16000
    assert protobuf(sample_rate_hz=24000).realtime_bytes_per_second == 48000
    assert protobuf(sample_rate_hz=48000).realtime_bytes_per_second == 96000
    assert dialect_of("twilio").realtime_bytes_per_second == 8000


# ------------------------------------------------------------------------------------ 2.2


def test_the_new_constants_are_named_and_the_old_ceiling_is_unchanged() -> None:
    """BL-353 / A-F4 (2.2, 2.3): the names and values of the contract; the 256 MiB constant stays
    as the ceiling (its own tests pin it)."""
    lim = limits()
    assert lim.MAX_AGENT_AUDIO_BYTES == 268_435_456
    assert lim.AGENT_AUDIO_CAP_FACTOR == 4
    assert lim.MIN_AGENT_AUDIO_CAP_BYTES == MIB
    assert lim.MAX_INBOUND_BYTES_PER_SECOND == MAX_BYTES_PER_SECOND
    for name in (
        "AGENT_AUDIO_CAP_FACTOR",
        "MIN_AGENT_AUDIO_CAP_BYTES",
        "MAX_INBOUND_BYTES_PER_SECOND",
        "agent_audio_cap_bytes",
    ):
        assert name in lim.__all__, name


@pytest.mark.parametrize(
    ("rate", "ms", "expected"),
    [
        # (181 s) x rate x 4: the 180 s call of the audit, per dialect
        (32000, 180_000, 23_168_000),
        (8000, 180_000, 5_792_000),
        (96000, 180_000, 69_504_000),
        # the 60 s default of the runner tests
        (8000, 60_000, 1_952_000),
        (32000, 60_000, 7_808_000),
        # a short call is lifted to the floor of 1 MiB
        (8000, 1_000, MIB),
        (32000, 100, MIB),
        # a very long call is held at the 256 MiB constant
        (96000, 3_600_000, 268_435_456),
        (32000, 86_400_000, 268_435_456),
    ],
)
def test_agent_audio_cap_bytes_table(rate: int, ms: int, expected: int) -> None:
    """BL-353 / A-F4 (2.2): cap = clamp(rate x (ms / 1000 + 1 s) x 4, 1 MiB, 256 MiB), integer
    arithmetic (floor), the 1 s being the watchdog grace."""
    assert limits().agent_audio_cap_bytes(bytes_per_second=rate, max_duration_ms=ms) == expected


def test_agent_audio_cap_bytes_floors_a_fraction_of_a_byte() -> None:
    """BL-353 / A-F4 (2.2): integer arithmetic, floor: 33333 B/s x (61.001 s + 1 s) x 4 is
    8266717.332 bytes, so the cap is 8266717 (no float is involved)."""
    cap = limits().agent_audio_cap_bytes(bytes_per_second=33_333, max_duration_ms=61_001)
    assert cap == 8_266_717
    assert type(cap) is int


def test_agent_audio_cap_bytes_ceiling_only_lowers() -> None:
    """BL-353 / A-F4 (2.2): the ``ceiling`` argument (``RunnerSettings.max_agent_audio_bytes``) can
    lower the cap, also below the 1 MiB floor, and can never raise it above the constant."""
    cap = limits().agent_audio_cap_bytes
    assert cap(bytes_per_second=32000, max_duration_ms=180_000, ceiling=2000) == 2000
    assert cap(bytes_per_second=32000, max_duration_ms=180_000, ceiling=10_000_000) == 10_000_000
    assert cap(bytes_per_second=32000, max_duration_ms=180_000, ceiling=10**12) == 23_168_000
    assert cap(bytes_per_second=96000, max_duration_ms=3_600_000, ceiling=10**12) == 268_435_456


def test_agent_audio_cap_bytes_grows_with_the_call_length_and_stays_above_real_time() -> None:
    """BL-353 / A-F4 (2.2): never below the real-time audio of the whole call (so a polite agent
    is never cut) and about four times that, not 256 MiB, for a call of 180 s."""
    cap = limits().agent_audio_cap_bytes
    previous = 0
    for ms in (1_000, 30_000, 60_000, 180_000, 600_000):
        for rate in (8000, 32000, 96000):
            value = cap(bytes_per_second=rate, max_duration_ms=ms)
            assert value >= min(rate * (ms // 1000 + 1), 268_435_456)
        value = cap(bytes_per_second=32000, max_duration_ms=ms)
        assert value >= previous
        previous = value
    realtime = 96000 * 180
    assert cap(bytes_per_second=96000, max_duration_ms=180_000) <= 4.1 * realtime
    assert cap(bytes_per_second=96000, max_duration_ms=180_000) < 268_435_456 // 3


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "100", None])
def test_agent_audio_cap_bytes_refuses_a_value_that_is_not_a_positive_integer(bad: Any) -> None:
    """BL-353 / A-F4 (2.2): ``ValueError`` for each argument; nothing is guessed."""
    cap = limits().agent_audio_cap_bytes
    with pytest.raises(ValueError, match="bytes_per_second"):
        cap(bytes_per_second=bad, max_duration_ms=1000)
    with pytest.raises(ValueError, match="max_duration_ms"):
        cap(bytes_per_second=1000, max_duration_ms=bad)
    with pytest.raises(ValueError, match="ceiling"):
        cap(bytes_per_second=1000, max_duration_ms=1000, ceiling=bad)


# ------------------------------------------------------------------------------------ 2.3


def reader(name: str, clock: Any, **caps: int) -> Any:
    return mod("dialects.base").InboundReader(dialect_for(name), clock, **caps)


def twilio_reader(clock: Any, **caps: int) -> Any:
    dialect = dialect_of("twilio")
    dialect.opening_messages()
    return mod("dialects.base").InboundReader(dialect, clock, **caps)


def test_inbound_reader_default_byte_rate_is_one_mebibyte_per_second() -> None:
    """BL-353 / A-F4 (2.3): 16 messages of 65536 bytes fit in one second (the sum is exactly the
    limit and the limit is inclusive); the 17th is refused with ``inbound_bytes_exceeded``,
    END_REASON ``transport_error``."""
    errors = mod("errors")
    r = twilio_reader(ManualClock())
    junk = "x" * 65536
    for _ in range(16):
        assert r.feed(junk) is None  # not JSON: ignored, but it counts as bytes read
    with pytest.raises(errors.TransportError) as raised:
        r.feed(junk)
    assert raised.value.code == "inbound_bytes_exceeded"
    assert raised.value.END_REASON == "transport_error"
    assert "inbound_bytes_exceeded" in errors.TRANSPORT_CODES
    assert list(errors.TRANSPORT_CODES) == sorted(set(errors.TRANSPORT_CODES))


def test_inbound_reader_byte_window_slides_by_exact_nanoseconds() -> None:
    """BL-353 / A-F4 (2.3): a message leaves the window exactly one second after it came (the same
    rule as the message-rate window of the batch D contract)."""
    errors = mod("errors")
    clock = ManualClock()
    r = twilio_reader(clock, max_bytes_per_second=1000)
    assert r.feed("x" * 600) is None
    clock.advance_to(START_NS + 400_000_000)
    assert r.feed("x" * 400) is None  # 1000 in the window: allowed (inclusive)
    clock.advance_to(START_NS + NS - 1)
    with pytest.raises(errors.TransportError) as raised:
        r.feed("x")  # the 600 bytes are still inside
    assert raised.value.code == "inbound_bytes_exceeded"
    clock.advance_to(START_NS + NS)  # the first message is exactly one second old: out
    assert r.feed("x" * 600) is None  # 400 + 600
    with pytest.raises(errors.TransportError):
        r.feed("x")
    clock.advance_to(START_NS + NS + 400_000_000)  # the second message leaves
    assert r.feed("x" * 400) is None


def test_inbound_reader_a_refused_message_is_not_counted() -> None:
    """BL-353 / A-F4 (2.3): a message refused by the byte window is not added to it, not decoded
    and not counted in ``stats`` (the same as a message refused by the rate window)."""
    errors = mod("errors")
    clock = ManualClock()
    r = twilio_reader(clock, max_bytes_per_second=1000)
    r.feed("x" * 900)
    before = r.stats
    for _ in range(3):
        with pytest.raises(errors.TransportError):
            r.feed("x" * 200)
    after = r.stats
    assert (after.messages, after.ignored, after.audio_bytes) == (
        before.messages,
        before.ignored,
        before.audio_bytes,
    )
    clock.advance_to(START_NS + NS)
    assert r.feed("x" * 1000) is None  # the window holds nothing of the refused ones


def test_inbound_reader_counts_message_bytes_of_text_in_utf8() -> None:
    """BL-353 / A-F4 (2.3): the size of a text message is its UTF-8 length, as for the size cap."""
    errors = mod("errors")
    r = twilio_reader(ManualClock(), max_bytes_per_second=1000)
    assert r.feed("\u092e" * 333) is None  # 999 bytes
    with pytest.raises(errors.TransportError):
        r.feed("xy")  # 1001


def test_inbound_reader_rate_code_wins_when_both_windows_are_full() -> None:
    """BL-353 / A-F4 (2.3): the order of the checks is size, message rate, byte rate. A message
    that is too big is ``message_too_large`` before anything else."""
    errors = mod("errors")
    r = twilio_reader(ManualClock(), max_messages_per_second=2, max_bytes_per_second=10)
    r.feed("ab")
    r.feed("ab")
    with pytest.raises(errors.TransportError) as both:
        r.feed("x" * 100)  # the rate window is full AND the bytes would exceed
    assert both.value.code == "inbound_rate_exceeded"
    big = twilio_reader(ManualClock(), max_message_bytes=5, max_bytes_per_second=1)
    with pytest.raises(errors.TransportError) as too_big:
        big.feed("x" * 6)
    assert too_big.value.code == "message_too_large"


def test_inbound_reader_byte_window_counts_binary_garbage_and_audio_alike() -> None:
    """BL-353 / A-F4 (2.3): bytes are counted before decoding, so ignored garbage and real audio
    spend the same budget."""
    errors = mod("errors")
    r = reader("protobuf", ManualClock(), max_bytes_per_second=3000)
    audio = ref_encode(
        "audio", {"audio": b"\x01\x00" * 640, "sample_rate": 16000, "num_channels": 1}
    )
    assert len(audio) > 1280
    assert r.feed(audio) is not None
    assert r.feed(b"\xff" * (3000 - len(audio))) is None
    with pytest.raises(errors.TransportError) as raised:
        r.feed(b"\xff")
    assert raised.value.code == "inbound_bytes_exceeded"


@pytest.mark.parametrize("name", sorted(MEASURED))
def test_a_real_time_agent_is_never_refused_by_the_byte_window(name: str) -> None:
    """BL-353 / A-F4 (2.3): the measured Pipecat rate (a message every interval, 60 s of call) stays
    far below the limit, so the byte window never refuses an agent that speaks in real time,
    whatever the framing overhead (a twilio message is about 1.7 times its audio)."""
    size, interval_ms = MEASURED[name]
    clock = ManualClock()
    r = twilio_reader(clock) if name == "twilio" else reader(name, clock)
    if name == "protobuf":
        message: Any = ref_encode(
            "audio", {"audio": b"\x01\x00" * (size // 2), "sample_rate": 16000, "num_channels": 1}
        )
    elif name == "twilio":
        payload = base64.b64encode(b"\x55" * size).decode("ascii")
        message = json.dumps(
            {"event": "media", "streamSid": "tracer-sim-stream-1", "media": {"payload": payload}},
            separators=(",", ":"),
        )
    else:
        message = b"\x01\x00" * (size // 2)
    for step in range(60 * 1000 // interval_ms):
        clock.advance_to(START_NS + step * interval_ms * 1_000_000)
        assert r.feed(message) is not None
    per_second = (len(message) if isinstance(message, bytes) else len(message.encode())) * (
        1000 // interval_ms
    )
    assert per_second * 10 < MAX_BYTES_PER_SECOND  # at least ten times the headroom


# ------------------------------------------------------------------------------------ 2.4


def _protobuf_audio_message(audio_bytes: int) -> bytes:
    message: bytes = ref_encode(
        "audio",
        {"audio": b"\x01\x00" * (audio_bytes // 2), "sample_rate": 16000, "num_channels": 1},
    )
    return message


def _retained(played: Any) -> int:
    return sum(len(frame.payload) for frame in played.result.agent_frames)


def test_a_hostile_protobuf_agent_at_the_message_rate_cap_ends_the_call_within_one_mebibyte() -> (
    None
):
    """BL-353 / A-F4 (2.4): a fake agent sends 64 KiB messages 1 ms apart (the message-rate cap of
    1000 per second). The call ends ``transport_error`` with ``inbound_bytes_exceeded`` once the
    byte window is full, holding no more audio than the window allows (counted on the frame log,
    not RSS). Without the byte window the call would hold 1000 x 64 KiB = 64 MB at the rate cap."""
    dialect = dialect_of("protobuf")
    message = _protobuf_audio_message(65_000)
    assert 65_000 < len(message) <= mod("limits").MAX_INBOUND_MESSAGE_BYTES
    steps: list[Any] = [Wait(50)]
    for _ in range(300):
        steps += [Send(message), Wait(1)]
    played = play("protobuf", steps, lines(dialect, 10))
    result = played.result
    assert result.end_reason == "transport_error"
    assert result.failure_code == "inbound_bytes_exceeded"
    assert result.counts["frames_received"] == MAX_BYTES_PER_SECOND // len(message) == 16
    retained = _retained(played)
    assert retained == 16 * 65_000
    assert retained <= MAX_BYTES_PER_SECOND  # bounded: the window, not the call length
    assert played.at(result.observed_until_ns) < 1000  # ended within the first second of the flood


def test_a_hostile_twilio_agent_at_the_message_rate_cap_ends_the_call_within_one_mebibyte() -> None:
    """BL-353 / A-F4 (2.4): the same on the JSON dialect (the base64 payload is 4/3 of the audio,
    so the window fills with fewer bytes of audio)."""
    dialect = dialect_of("twilio")
    payload = base64.b64encode(b"\x55" * 48_800).decode("ascii")
    message = json.dumps(
        {"event": "media", "streamSid": "tracer-sim-stream-1", "media": {"payload": payload}},
        separators=(",", ":"),
    )
    size = len(message.encode("utf-8"))
    assert size <= mod("limits").MAX_INBOUND_MESSAGE_BYTES
    steps: list[Any] = [Wait(50)]
    for _ in range(300):
        steps += [Send(message), Wait(1)]
    played = play("twilio", steps, lines(dialect, 10))
    assert played.result.end_reason == "transport_error"
    assert played.result.failure_code == "inbound_bytes_exceeded"
    accepted = MAX_BYTES_PER_SECOND // size
    assert played.result.counts["frames_received"] == accepted
    assert _retained(played) == accepted * 48_800 <= MAX_BYTES_PER_SECOND


def _twilio_media(audio_bytes: int) -> str:
    payload = base64.b64encode(b"\x55" * audio_bytes).decode("ascii")
    return json.dumps(
        {"event": "media", "streamSid": "tracer-sim-stream-1", "media": {"payload": payload}},
        separators=(",", ":"),
    )


def test_the_total_agent_audio_cap_follows_the_call_length_exactly() -> None:
    """BL-353 / A-F4 (2.4): for a twilio call of 60 s (the default of these tests) the cap is
    ``agent_audio_cap_bytes(8000, 60000)`` = 1_952_000 = 40 x 48_800. Forty such messages, one every
    100 ms (about 650 KB per second: under the byte window) are allowed and the call goes on; one
    byte more ends it with ``message_too_large``, the frame that crossed the cap being received."""
    cap = limits().agent_audio_cap_bytes(bytes_per_second=8000, max_duration_ms=60_000)
    assert cap == 1_952_000 == 40 * 48_800
    dialect = dialect_of("twilio")
    big = _twilio_media(48_800)
    assert len(big.encode("utf-8")) <= mod("limits").MAX_INBOUND_MESSAGE_BYTES
    forty: list[Any] = [Wait(50)]
    for _ in range(40):
        forty += [Send(big), Wait(100)]
    allowed = play("twilio", forty, lines(dialect, 10), max_silence_ms=60_000)
    assert allowed.result.failure_code is None
    assert allowed.result.counts["frames_received"] == 40
    assert _retained(allowed) == cap
    refused = play(
        "twilio",
        [*forty, Send(_twilio_media(1)), Wait(100)],
        lines(dialect, 10),
        max_silence_ms=60_000,
    )
    assert refused.result.end_reason == "transport_error"
    assert refused.result.failure_code == "message_too_large"
    assert refused.result.counts["frames_received"] == 41
    assert _retained(refused) == cap + 1


def test_a_hostile_agent_below_the_byte_window_is_stopped_by_the_total_cap() -> None:
    """BL-353 / A-F4 (2.4): 64 KiB messages every 100 ms (under the byte window and far under the
    message rate) never hit the window, and used to be held until 256 MiB. Now the call ends when
    the total passes the cap derived from the 60 s call, with the audio held bounded by the cap
    plus one message."""
    cap = limits().agent_audio_cap_bytes(bytes_per_second=8000, max_duration_ms=60_000)
    big = _twilio_media(48_800)
    steps: list[Any] = [Wait(50)]
    for _ in range(200):
        steps += [Send(big), Wait(100)]
    played = play("twilio", steps, lines(dialect_of("twilio"), 10), max_silence_ms=60_000)
    assert played.result.end_reason == "transport_error"
    assert played.result.failure_code == "message_too_large"
    assert cap < _retained(played) <= cap + 48_800


def test_a_shorter_call_has_a_proportionally_smaller_cap() -> None:
    """BL-353 / A-F4 (2.4): the cap is derived from ``max_duration_ms`` of the settings: for a 10 s
    call the cap is the 1 MiB floor (11 s x 8000 x 4 = 352000 is below it), and a flood that the
    60 s call would still accept ends the call earlier."""
    limit = limits().agent_audio_cap_bytes(bytes_per_second=8000, max_duration_ms=10_000)
    assert limit == MIB
    big = _twilio_media(48_800)
    steps: list[Any] = [Wait(50)]
    for _ in range(60):
        steps += [Send(big), Wait(100)]
    played = play(
        "twilio",
        steps,
        lines(dialect_of("twilio"), 10),
        max_duration_ms=10_000,
        max_silence_ms=60_000,
    )
    assert played.result.failure_code == "message_too_large"
    assert limit < _retained(played) <= limit + 48_800


def test_the_ceiling_of_the_settings_still_lowers_the_cap() -> None:
    """BL-353 / A-F4 (2.4): ``RunnerSettings.max_agent_audio_bytes`` stays the explicit ceiling
    (default: the 256 MiB constant, pinned by the batch E tests) and only lowers the derived cap."""
    dialect = dialect_of("protobuf")
    exact = 3 * 640
    allowed = play(
        "protobuf",
        [Wait(50), Send(_protobuf_audio_message(640)), Send(_protobuf_audio_message(640))],
        lines(dialect, 10),
        max_agent_audio_bytes=exact,
    )
    assert allowed.result.failure_code is None
    refused = play(
        "protobuf",
        [Wait(50)] + [Send(_protobuf_audio_message(640)) for _ in range(4)],
        lines(dialect, 10),
        max_agent_audio_bytes=exact,
    )
    assert refused.result.failure_code == "message_too_large"
    assert refused.result.counts["frames_received"] == 4  # the frame that crossed the cap
