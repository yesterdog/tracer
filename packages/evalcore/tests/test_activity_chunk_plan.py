# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-10, the pure half: the agent-activity chunk plan ``tracer.chunk_plan/2`` and the choice of
the speech-to-text channel from the declared channel map (contract
docs/phase4/api-surface-batch-b.md section 8; ADR 0024 amendment A5, ADR 0026 rule 10).

The segment rule is restated in ``reference_segments`` in plain Python, so the expected bounds of
the hand-made and of the seeded random recordings come from code that shares nothing with
``tracer_evalcore``. All audio is synthetic and generated here. Nothing opens a socket.
"""

from __future__ import annotations

import importlib
import inspect
import io
import json
import random
import sys
import wave
from array import array
from typing import Any

import pytest

from tracer_evalschema.artifact import ChannelMap
from tracer_evalschema.ids import ArtifactId

FRAME_MS = 20


def _act() -> Any:
    return importlib.import_module("tracer_evalcore.audio.activity")


def _chunker() -> Any:
    return importlib.import_module("tracer_evalcore.audio.chunker")


# --------------------------------------------------------------------------- synthetic signals


def frame_len(rate: int) -> int:
    return rate * FRAME_MS // 1000


def to_pcm(samples: list[int]) -> bytes:
    data = array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def stereo(ch0: list[int], ch1: list[int]) -> bytes:
    assert len(ch0) == len(ch1)
    inter: list[int] = []
    for a, b in zip(ch0, ch1, strict=True):
        inter += [a, b]
    return to_pcm(inter)


def channel_from_pattern(
    pattern: str, rate: int, *, tail: int = 0, tail_active: bool = False
) -> list[int]:
    """One frame per character. ``.`` is a frame of zeros; the letters are the four ways to be
    active: ``A`` one sample of +1 at the first position, ``B`` one of -1 at the last, ``C`` one of
    -32768 in the middle, ``D`` every sample 7. ``tail`` extra samples form a partial last frame."""
    size = frame_len(rate)
    samples: list[int] = []
    for char in pattern:
        frame = [0] * size
        if char == "A":
            frame[0] = 1
        elif char == "B":
            frame[-1] = -1
        elif char == "C":
            frame[size // 2] = -32768
        elif char == "D":
            frame = [7] * size
        else:
            assert char == ".", char
        samples += frame
    if tail:
        assert tail < size
        samples += [5 if (tail_active and i == 0) else 0 for i in range(tail)]
    return samples


def noise(count: int, seed: int) -> list[int]:
    rng = random.Random(seed)  # noqa: S311 - a seeded test signal, not a secret
    return [rng.choice([-1000, -3, 1, 2, 999]) for _ in range(count)]


def reference_segments(samples: list[int], rate: int, wait_ms: int) -> list[tuple[int, int]]:
    """ADR 0024 amendment A5, written out: frames of 20 ms counted from sample 0 (the last one may
    be shorter); a frame is active when any of its samples is not zero; runs of active frames are
    joined when the silence between them is shorter than ``wait_ms``; a segment runs from the first
    sample of its first active frame to the last sample of its last active frame."""
    size = frame_len(rate)
    total = len(samples)
    count = -(-total // size)
    active = [any(samples[k * size : (k + 1) * size]) for k in range(count)]
    runs: list[list[int]] = []
    for k, flag in enumerate(active):
        if not flag:
            continue
        if runs and (k - runs[-1][1] - 1) * FRAME_MS < wait_ms:
            runs[-1][1] = k
        else:
            runs.append([k, k])
    return [(first * size, min((last + 1) * size, total)) for first, last in runs]


def wav_samples(wav: bytes) -> tuple[int, int, int, list[int]]:
    with wave.open(io.BytesIO(wav), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        data = array("h")
        data.frombytes(frames)
        if sys.byteorder == "big":
            data.byteswap()
        return handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), list(data)


def plan_of(
    ch0: list[int], ch1: list[int], rate: int, wait_ms: int, *, channel: int = 1, **over: Any
) -> Any:
    return _act().activity_chunk_plan(
        stereo(ch0, ch1),
        sample_rate_hz=rate,
        channels=2,
        channel=channel,
        wait_for_agent_silence_ms=wait_ms,
        **over,
    )


def bounds(plan: Any) -> list[tuple[int, int]]:
    return [(c.start_frame, c.end_frame) for c in plan.chunks]


# ----------------------------------------------------------------- the named test of the SCOPE

HAND_CASES = [
    # pattern, tail, tail_active, rate, wait_ms
    ("", 0, False, 16000, 100),
    ("....", 0, False, 16000, 100),
    ("A", 0, False, 16000, 100),
    ("..AA..", 0, False, 16000, 100),
    ("AA...AA", 0, False, 16000, 100),
    ("AA....AA", 0, False, 16000, 100),
    ("AA.....AA", 0, False, 16000, 100),
    ("A.B.C.D", 0, False, 24000, 60),
    ("A.B.C.D", 0, False, 24000, 40),
    ("A.B.C.D", 0, False, 24000, 41),
    ("A.B.C.D", 0, False, 24000, 20),
    ("A.B.C.D", 0, False, 24000, 1),
    ("DDDD.......DDDD...DD", 0, False, 22050, 130),
    ("DD...", 37, True, 16000, 100),
    ("DD...", 37, False, 16000, 100),
    ("DD.", 100, True, 8000, 60),
    ("......", 11, True, 16000, 1000),
    ("D" * 40, 0, False, 8000, 20),
]


def _check_case(
    pattern: str, tail: int, tail_active: bool, rate: int, wait_ms: int, seed: int
) -> None:
    ch1 = channel_from_pattern(pattern, rate, tail=tail, tail_active=tail_active)
    ch0 = noise(len(ch1), seed)
    plan = plan_of(ch0, ch1, rate, wait_ms)
    expected = reference_segments(ch1, rate, wait_ms)
    label = (pattern, tail, tail_active, rate, wait_ms)
    assert bounds(plan) == expected, label
    assert [c.index for c in plan.chunks] == list(range(len(expected))), label
    assert plan.total_frames == len(ch1), label
    assert plan.sample_rate_hz == rate, label
    assert plan.channels == 2, label
    assert plan.channel == 1, label
    assert plan.wait_for_agent_silence_ms == wait_ms, label
    covered = [0] * len(ch1)
    for chunk, (start, end) in zip(plan.chunks, expected, strict=True):
        assert chunk.sample_rate_hz == rate, label
        assert chunk.channels == 1, label
        assert chunk.frames == end - start, label
        channels, width, framerate, samples = wav_samples(chunk.wav)
        assert (channels, width, framerate) == (1, 2, rate), label
        assert samples == ch1[start:end], "the chunk is a copy of the channel, sample for sample"
        for i in range(start, end):
            covered[i] += 1
    assert max(covered, default=0) <= 1, "no sample is in two chunks"
    assert all(covered[i] == 1 for i, v in enumerate(ch1) if v != 0), (
        "every non-zero sample of the agent channel is in a chunk",
        label,
    )
    # determinism, and independence of the other channel
    again = plan_of(ch0, ch1, rate, wait_ms)
    assert again.as_metadata() == plan.as_metadata(), label
    assert [c.wav for c in again.chunks] == [c.wav for c in plan.chunks], label
    other = plan_of(noise(len(ch1), seed + 1), ch1, rate, wait_ms)
    assert bounds(other) == bounds(plan), label
    assert [c.wav for c in other.chunks] == [c.wav for c in plan.chunks], label


def test_activity_chunk_plan_is_deterministic_and_covers_agent_audio() -> None:
    """The plan is the A5 rule on the agent channel: the same input gives the same bounds and
    bytes; every non-zero sample of the channel lies in exactly one chunk; a chunk is a mono copy
    of the channel's samples; the other channel changes nothing. Checked on hand-made recordings
    and on 90 seeded random ones against an independent restatement of the rule."""
    for number, case in enumerate(HAND_CASES):
        _check_case(*case, seed=100 + number)
    rng = random.Random(20261027)  # noqa: S311 - a seeded test signal, not a secret
    for number in range(90):
        rate = rng.choice([8000, 16000, 22050, 24000])
        pattern = "".join(rng.choice("..A.BCDD") for _ in range(rng.randint(1, 60)))
        tail = rng.choice([0, 0, 1, 7, frame_len(rate) - 1])
        wait_ms = rng.choice([1, 20, 21, 40, 41, 60, 100, 130, 200, 400])
        _check_case(pattern, tail, rng.random() < 0.5, rate, wait_ms, seed=500 + number)


# ------------------------------------------------------------------------------- the rule


def test_activity_plan_gap_equal_to_the_wait_splits_and_a_shorter_gap_joins() -> None:
    """ADR 0026 rule 10: the agent is silent once no active frame arrived for the wait value, so a
    gap of exactly that length ends the segment and one frame less keeps it."""
    rate = 16000
    five = channel_from_pattern("DD.....DD", rate)  # a gap of 5 frames = 100 ms
    size = frame_len(rate)
    assert bounds(plan_of(noise(len(five), 1), five, rate, 100)) == [
        (0 * size, 2 * size),
        (7 * size, 9 * size),
    ]
    assert bounds(plan_of(noise(len(five), 1), five, rate, 101)) == [(0, 9 * size)]
    assert bounds(plan_of(noise(len(five), 1), five, rate, 99)) == [
        (0 * size, 2 * size),
        (7 * size, 9 * size),
    ]
    four = channel_from_pattern("DD....DD", rate)  # 80 ms
    assert bounds(plan_of(noise(len(four), 1), four, rate, 80)) == [
        (0, 2 * size),
        (6 * size, 8 * size),
    ]
    assert bounds(plan_of(noise(len(four), 1), four, rate, 81)) == [(0, 8 * size)]


@pytest.mark.parametrize("letter", ["A", "B", "C", "D"])
def test_activity_plan_any_nonzero_sample_makes_a_frame_active_no_threshold(letter: str) -> None:
    """ADR 0026 rule 10: a frame is active when it holds any non-zero sample; there is no energy
    threshold. +1, -1 and -32768 in any position count; an all-zero frame never does."""
    rate = 8000
    ch1 = channel_from_pattern(f"...{letter}...", rate)
    size = frame_len(rate)
    plan = plan_of(noise(len(ch1), 3), ch1, rate, 20)
    assert bounds(plan) == [(3 * size, 4 * size)]


def test_activity_plan_ignores_the_other_channel_and_follows_the_requested_one() -> None:
    """ADR 0024 A5: the samples of the requested channel only, never a downmix."""
    rate = 16000
    size = frame_len(rate)
    quiet = [0] * (8 * size)
    loud = channel_from_pattern("DDDDDDDD", rate)
    assert plan_of(loud, quiet, rate, 100, channel=1).chunks == ()
    only0 = plan_of(loud, quiet, rate, 100, channel=0)
    assert bounds(only0) == [(0, 8 * size)]
    assert only0.channel == 0
    _channels, _width, _rate, samples = wav_samples(only0.chunks[0].wav)
    assert samples == loud
    both = plan_of(loud, channel_from_pattern("..DD....", rate), rate, 100, channel=1)
    assert bounds(both) == [(2 * size, 4 * size)]


def test_activity_plan_last_partial_frame_is_a_frame() -> None:
    """The last frame may be shorter than 20 ms: active, it ends the segment at the last sample
    of the recording; silent, it is trimmed like any silent frame."""
    rate = 16000
    size = frame_len(rate)
    active_tail = channel_from_pattern("DD..", rate, tail=17, tail_active=True)
    plan = plan_of(noise(len(active_tail), 2), active_tail, rate, 20)
    assert bounds(plan) == [(0, 2 * size), (4 * size, 4 * size + 17)]
    silent_tail = channel_from_pattern("DD..", rate, tail=17, tail_active=False)
    plan = plan_of(noise(len(silent_tail), 2), silent_tail, rate, 20)
    assert bounds(plan) == [(0, 2 * size)]
    assert plan.total_frames == 4 * size + 17


def test_activity_plan_no_activity_has_no_chunk_and_still_states_the_recording() -> None:
    """A recording with no agent activity gives a plan with no chunk (nothing is sent), not an
    error."""
    rate = 16000
    ch1 = [0] * (frame_len(rate) * 5)
    plan = plan_of(noise(len(ch1), 5), ch1, rate, 100)
    assert plan.chunks == ()
    assert plan.total_frames == len(ch1)
    assert plan.as_metadata()["chunks"] == []
    empty = _act().activity_chunk_plan(
        b"", sample_rate_hz=rate, channels=2, channel=1, wait_for_agent_silence_ms=100
    )
    assert empty.chunks == ()
    assert empty.total_frames == 0


def test_activity_plan_chunk_indexes_run_on_across_segments_and_cuts() -> None:
    """Chunk indexes are consecutive over the whole plan: the usage records and the call log name
    them (``tracer.chunk_index``), so two segments never both start at 0."""
    rate = 8000
    size = frame_len(rate)
    # three segments, the middle one long enough to be cut by rule 7 (max 1000 ms)
    pattern = "DD" + "." * 12 + "D" * 130 + "." * 12 + "DD"
    ch1 = channel_from_pattern(pattern, rate)
    plan = plan_of(noise(len(ch1), 9), ch1, rate, 100, max_chunk_ms=1000, search_ms=200)
    assert [c.index for c in plan.chunks] == list(range(len(plan.chunks)))
    assert len(plan.chunks) > 3
    assert plan.chunks[0].end_frame == 2 * size
    assert plan.chunks[-1].start_frame == (2 + 12 + 130 + 12) * size


def test_activity_plan_a_long_segment_is_cut_inside_by_rule_7() -> None:
    """ADR 0024 A5: a segment longer than ``max_chunk_ms`` is cut inside by rule 7, at the quietest
    whole 20 ms frame in the last ``search_ms`` of each window, counted from the segment start;
    the cut bounds are on the recording timeline (offset by the segment start)."""
    rate = 8000
    size = frame_len(rate)
    lead = 7  # the segment starts at frame 7, not at 0, so an offset bug shows
    body = ["D"] * 130
    body[45] = "."  # a silent frame in the search region of the first window (frames 40 to 49)
    pattern = "." * lead + "".join(body) + "." * 4
    ch1 = channel_from_pattern(pattern, rate)
    plan = plan_of(noise(len(ch1), 11), ch1, rate, 100, max_chunk_ms=1000, search_ms=200)
    start = lead * size
    segment = ch1[start : start + 130 * size]
    reference = _chunker().chunk_pcm(
        to_pcm(segment), sample_rate_hz=rate, channels=1, max_chunk_ms=1000, search_ms=200
    )
    assert bounds(plan) == [(start + c.start_frame, start + c.end_frame) for c in reference.chunks]
    assert len(plan.chunks) >= 3
    assert plan.chunks[0].end_frame == start + 45 * size + size // 2, "cut at the silent frame"
    for chunk in plan.chunks:
        assert chunk.frames * 1000 <= 1000 * rate, "no chunk is longer than max_chunk_ms"
        _c, _w, _r, samples = wav_samples(chunk.wav)
        assert samples == ch1[chunk.start_frame : chunk.end_frame]


def test_activity_plan_metadata_v2_is_this_integer_document() -> None:
    """``tracer.chunk_plan/2``: the /1 document plus the channel index and the segment rule, both
    integers; nothing else, and it round-trips through JSON."""
    rate = 8000
    ch1 = channel_from_pattern("..AA...A", rate)
    plan = plan_of(noise(len(ch1), 4), ch1, rate, 100)
    document = plan.as_metadata()
    assert document == {
        "format": "tracer.chunk_plan/2",
        "max_chunk_ms": 25000,
        "search_ms": 5000,
        "sample_rate_hz": 8000,
        "channels": 2,
        "channel": 1,
        "wait_for_agent_silence_ms": 100,
        "frame_ms": 20,
        "total_frames": 1280,
        "chunks": [{"index": 0, "start_frame": 320, "end_frame": 1280}],
    }
    assert json.loads(json.dumps(document)) == document
    for key, value in document.items():
        if key not in {"format", "chunks"}:
            assert type(value) is int, key
    assert _act().ACTIVITY_PLAN_FORMAT == "tracer.chunk_plan/2"
    assert _act().ACTIVITY_FRAME_MS == 20


def test_activity_plan_parameters_are_checked_and_have_no_default_for_the_rule() -> None:
    """ADR 0026 rule 10: the silence value and the channel have no default in code; bad values
    are typed refusals."""
    act = _act()
    parameters = inspect.signature(act.activity_chunk_plan).parameters
    for name in ("sample_rate_hz", "channels", "channel", "wait_for_agent_silence_ms"):
        assert parameters[name].default is inspect.Parameter.empty, name
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert parameters["max_chunk_ms"].default == 25_000
    assert parameters["search_ms"].default == 5_000
    pcm = stereo([0, 1, 0, 1], [0, 0, 5, 0])
    good: dict[str, Any] = {
        "sample_rate_hz": 8000,
        "channels": 2,
        "channel": 1,
        "wait_for_agent_silence_ms": 100,
    }
    act.activity_chunk_plan(pcm, **good)
    chunker = _chunker()
    for name, bad in [
        ("wait_for_agent_silence_ms", 0),
        ("wait_for_agent_silence_ms", -20),
        ("wait_for_agent_silence_ms", True),
        ("wait_for_agent_silence_ms", 100.0),
        ("wait_for_agent_silence_ms", "100"),
        ("channel", -1),
        ("channel", 2),
        ("channel", True),
        ("channel", 1.0),
        ("sample_rate_hz", 0),
        ("sample_rate_hz", 8000.0),
        ("search_ms", 25_001),
    ]:
        with pytest.raises(chunker.ChunkerParameterError):
            act.activity_chunk_plan(pcm, **{**good, name: bad})
    with pytest.raises(chunker.ChannelsRefusedError):
        act.activity_chunk_plan(pcm, **{**good, "channels": 3})
    with pytest.raises(chunker.ChunkerInputError):
        act.activity_chunk_plan(pcm + b"\x00", **good)
    with pytest.raises(TypeError):
        act.activity_chunk_plan(list(pcm), **good)
    mono = act.activity_chunk_plan(
        to_pcm([0, 0, 3, 0]),
        sample_rate_hz=8000,
        channels=1,
        channel=0,
        wait_for_agent_silence_ms=1,
    )
    assert mono.channels == 1
    assert mono.channel == 0


# ----------------------------------------------------- the channel map decides who is who


def make_map(*roles: str, determination: str = "declared") -> Any:
    document: dict[str, Any] = {
        "artifact_id": ArtifactId.new(),
        "channels": [{"index": i, "role": r} for i, r in enumerate(roles)],
        "determination": determination,
        "method": "tracer_evalsim_stereo/1",
    }
    if determination == "inferred":
        document["confidence"] = 0.9
    return ChannelMap.model_validate(document)


def test_select_stt_channel_reads_the_declared_map_not_a_fixed_index() -> None:
    """ADR 0024 A5: the role of a Turn comes from the declared channel map, never inferred and
    never from a fixed position."""
    act = _act()
    first = act.select_stt_channel(make_map("caller", "agent"))
    assert (first.index, first.role) == (1, "agent")
    swapped = act.select_stt_channel(make_map("agent", "caller"))
    assert (swapped.index, swapped.role) == (0, "agent")
    only = act.select_stt_channel(make_map("agent"))
    assert (only.index, only.role) == (0, "agent")


def test_select_stt_channel_never_picks_the_caller_by_default() -> None:
    """ADR 0027 rule 5 / A5: our STT never runs on the caller channel by default; asking for it is
    an explicit, named argument."""
    act = _act()
    parameter = inspect.signature(act.select_stt_channel).parameters["role"]
    assert parameter.default == "agent"
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    for roles in [("caller", "agent"), ("agent", "caller")]:
        chosen = act.select_stt_channel(make_map(*roles))
        assert roles[chosen.index] == "agent"
    explicit = act.select_stt_channel(make_map("caller", "agent"), role="caller")
    assert (explicit.index, explicit.role) == (0, "caller")


@pytest.mark.parametrize(
    ("roles", "determination"),
    [
        (("caller", "agent"), "inferred"),
        (("caller", "unknown"), "declared"),
        (("caller", "caller"), "declared"),
        (("mixed",), "declared"),
        (("unknown", "unknown"), "declared"),
    ],
)
def test_select_stt_channel_refuses_what_the_map_does_not_declare(
    roles: tuple[str, ...], determination: str
) -> None:
    """A role is never inferred: an inferred map, a map without an agent channel and a mixed
    channel are refused, with a typed error that quotes nothing."""
    act = _act()
    with pytest.raises(act.ChannelMapRefusedError) as raised:
        act.select_stt_channel(make_map(*roles, determination=determination))
    from tracer_evalcore.errors import InputRefusedError

    assert isinstance(raised.value, InputRefusedError)


def test_select_stt_channel_argument_checks() -> None:
    """Wrong argument types, roles and maps are refused, never guessed."""
    act = _act()
    for bad in ("unknown", "mixed", "robot", ""):
        with pytest.raises(ValueError, match="role"):
            act.select_stt_channel(make_map("caller", "agent"), role=bad)
    with pytest.raises(TypeError):
        act.select_stt_channel({"channels": []})
    with pytest.raises(act.ChannelMapRefusedError):
        act.select_stt_channel(make_map("agent", "unknown"), role="caller")
    with pytest.raises(act.ChannelMapRefusedError):
        act.select_stt_channel(make_map("caller", "caller"), role="caller")
