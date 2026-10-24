# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-05: the deterministic chunker (contract docs/phase3/api-surface-batch-a.md section 5).

The cut rule is ADR 0024 rule 7, restated in ``reference_ranges`` below in plain Python so that
the expected cut points of the hand-made signals and of the seeded random signals come from
code that shares nothing with ``tracer_evalcore``. All audio is synthetic and generated here.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import io
import itertools
import json
import random
import signal
import struct
import subprocess
import sys
import wave
from array import array
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import TranscriptId
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy
from tracer_evalschema.transcript import Transcript


@pytest.fixture(autouse=True)
def _chunker_must_finish() -> Iterator[None]:
    """A chunker that never advances must fail its test, not hang the CI job (and grow without
    bound): the alarm turns an endless loop into a TimeoutError after 20 s."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def alarm(signum: int, frame: Any) -> None:
        raise TimeoutError("the chunker did not finish in 20 s")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(20)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _chunker() -> Any:
    return importlib.import_module("tracer_evalcore.audio.chunker")


def _wav() -> Any:
    return importlib.import_module("tracer_evalcore.importers.wav")


# ------------------------------------------------------------------------ signals and the oracle


def to_pcm(samples: list[int]) -> bytes:
    data = array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def from_pcm(pcm: bytes) -> list[int]:
    data = array("h")
    data.frombytes(pcm)
    if sys.byteorder == "big":
        data.byteswap()
    return list(data)


def noise(frames: int, channels: int, seed: int) -> list[int]:
    rng = random.Random(seed)  # noqa: S311 - a seeded test signal, not a secret
    return [rng.randint(-3000, 3000) for _ in range(frames * channels)]


def reference_ranges(
    samples: list[int], *, channels: int, rate: int, max_ms: int, search_ms: int
) -> list[tuple[int, int]]:
    """ADR 0024 rule 7, written out: windows of W frames; a window that does not reach the end is
    cut at the middle sample of the lowest-energy whole 20 ms frame lying in its last S samples
    (frames counted from the window start; energy = exact sum of squares over all channels; ties
    to the latest frame)."""
    total = len(samples) // channels
    frame = rate * 20 // 1000
    window = rate * max_ms // 1000
    region = rate * search_ms // 1000
    ranges: list[tuple[int, int]] = []
    start = 0
    while total - start > window:
        best_k = -1
        best_energy = -1
        for k in range(window // frame):
            if k * frame < window - region or (k + 1) * frame > window:
                continue
            lo = (start + k * frame) * channels
            energy = sum(v * v for v in samples[lo : lo + frame * channels])
            if best_k < 0 or energy <= best_energy:  # <= : a tie goes to the latest frame
                best_k, best_energy = k, energy
        cut = start + best_k * frame + frame // 2
        ranges.append((start, cut))
        start = cut
    if total > start:
        ranges.append((start, total))
    return ranges


def ranges_of(plan: Any) -> list[tuple[int, int]]:
    return [(c.start_frame, c.end_frame) for c in plan.chunks]


def canonical_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    """The 44-byte canonical PCM header, written by hand."""
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def plan_of(samples: list[int], *, channels: int = 1, rate: int = 8000, **over: Any) -> Any:
    kwargs: dict[str, Any] = {"sample_rate_hz": rate, "channels": channels}
    kwargs.update(over)
    return _chunker().chunk_pcm(to_pcm(samples), **kwargs)


# --------------------------------------------------------------------------- the SCOPE tests


def test_chunker_is_deterministic() -> None:
    samples = noise(29600, 1, seed=7)
    pcm = to_pcm(samples)
    first = _chunker().chunk_pcm(
        pcm, sample_rate_hz=8000, channels=1, max_chunk_ms=1000, search_ms=200
    )
    second = _chunker().chunk_pcm(
        bytes(bytearray(pcm)), sample_rate_hz=8000, channels=1, max_chunk_ms=1000, search_ms=200
    )
    assert first == second
    assert [c.wav for c in first.chunks] == [c.wav for c in second.chunks]
    assert len(first.chunks) >= 4
    assert first.as_metadata() == second.as_metadata()
    # a fresh interpreter gives the same bytes (no dependence on hash seeds or the process)
    digest = hashlib.sha256(b"".join(c.wav for c in first.chunks)).hexdigest()
    code = (
        "import hashlib, random, sys\n"
        "from array import array\n"
        "from tracer_evalcore.audio.chunker import chunk_pcm\n"
        "rng = random.Random(7)\n"
        "samples = [rng.randint(-3000, 3000) for _ in range(29600)]\n"
        "a = array('h', samples)\n"
        "a.byteswap() if sys.byteorder == 'big' else None\n"
        "plan = chunk_pcm(a.tobytes(), sample_rate_hz=8000, channels=1, max_chunk_ms=1000,"
        " search_ms=200)\n"
        "print(hashlib.sha256(b''.join(c.wav for c in plan.chunks)).hexdigest())\n"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONHASHSEED": "12345"},
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == digest


@pytest.mark.parametrize(
    ("channels", "rate", "max_ms", "search_ms", "frames"),
    [
        (1, 8000, 1000, 200, 8000),  # exactly one window: no cut
        (1, 8000, 1000, 200, 8001),  # one frame more: a cut is needed
        (1, 8000, 1000, 200, 40007),
        (1, 8000, 1000, 1000, 24000),  # the search covers the whole window
        (1, 16000, 500, 100, 52345),
        (1, 22050, 750, 300, 77777),
        (1, 44100, 333, 100, 100001),  # the window is 14685 frames: a floor, not a round
        (1, 24000, 25000, 5000, 700_001),  # the defaults on a call just over 25 s
        (2, 8000, 1000, 200, 33000),
        (2, 16000, 400, 100, 20001),
        (1, 8000, 20, 20, 777),  # the smallest legal window: one 20 ms frame
        (1, 8000, 1000, 200, 1),
    ],
)
def test_chunker_covers_every_sample_once(
    channels: int, rate: int, max_ms: int, search_ms: int, frames: int
) -> None:
    samples = noise(frames, channels, seed=frames)
    pcm = to_pcm(samples)
    plan = _chunker().chunk_pcm(
        pcm,
        sample_rate_hz=rate,
        channels=channels,
        max_chunk_ms=max_ms,
        search_ms=search_ms,
        accept_provider_downmix=channels == 2,
    )
    chunks = plan.chunks
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].start_frame == 0
    assert chunks[-1].end_frame == frames == plan.total_frames
    for left, right in itertools.pairwise(chunks):
        assert left.end_frame == right.start_frame, "no gap and no overlap"
    assert all(c.end_frame > c.start_frame for c in chunks), "no empty chunk"
    # the samples of the chunks, joined, are the samples of the input
    joined: list[int] = []
    for c in chunks:
        with wave.open(io.BytesIO(c.wav), "rb") as reader:
            assert reader.getnchannels() == channels
            assert reader.getsampwidth() == 2
            assert reader.getframerate() == rate
            assert reader.getnframes() == c.end_frame - c.start_frame
            joined += from_pcm(reader.readframes(reader.getnframes()))
    assert joined == samples


@pytest.mark.parametrize(
    ("rate", "max_ms", "search_ms", "frames"),
    [
        (8000, 1000, 200, 100_000),
        (44100, 333, 100, 150_000),
        (16000, 777, 100, 200_000),
        (24000, 25000, 5000, 3_000_000),
        (22050, 750, 300, 16538),  # 16537.5 frames fit the window: a floor, not a round
    ],
)
def test_chunker_never_exceeds_max_chunk_ms(
    rate: int, max_ms: int, search_ms: int, frames: int
) -> None:
    samples = noise(frames, 1, seed=3)
    plan = _chunker().chunk_pcm(
        to_pcm(samples),
        sample_rate_hz=rate,
        channels=1,
        max_chunk_ms=max_ms,
        search_ms=search_ms,
    )
    window = rate * max_ms // 1000
    assert max(c.end_frame - c.start_frame for c in plan.chunks) <= window
    for c in plan.chunks:
        assert (c.end_frame - c.start_frame) * 1000 <= rate * max_ms
        assert c.frames == c.end_frame - c.start_frame
        exact = c.duration_seconds
        assert exact is None or exact * 1000 <= Decimal(max_ms)
    # the cut is never the window end: a chunk shorter than the window by the cut rule
    assert all(c.end_frame - c.start_frame < window for c in plan.chunks[:-1])


def test_chunker_cuts_at_lowest_energy_frame() -> None:
    f = 160  # a 20 ms frame at 8 kHz; the window is 8000 frames, the search region 1600
    rate, max_ms, search_ms = 8000, 1000, 200

    def signal(total: int, base: int = 1000) -> list[int]:
        return [base] * total

    def zero_block(samples: list[int], start: int, width: int = f) -> None:
        samples[start : start + width] = [0] * width

    def run(samples: list[int]) -> list[tuple[int, int]]:
        plan = plan_of(samples, rate=rate, max_chunk_ms=max_ms, search_ms=search_ms)
        assert ranges_of(plan) == reference_ranges(
            samples, channels=1, rate=rate, max_ms=max_ms, search_ms=search_ms
        )
        return ranges_of(plan)

    # a single quiet frame inside the region; quiet frames outside it are ignored. Frames 10 and
    # 39 lie before the region (frame 39 ends one frame before it starts), frames 50 and up lie
    # beyond the window.
    one = signal(12000)
    zero_block(one, 10 * f)
    zero_block(one, 39 * f)
    zero_block(one, 43 * f)
    zero_block(one, 50 * f)
    assert run(one) == [(0, 43 * f + f // 2), (43 * f + f // 2, 12000)]

    # the first frame of the region is a candidate, the frame before it is not
    low = signal(12000)
    zero_block(low, 39 * f)
    low[40 * f : 41 * f] = [1] * f
    assert run(low) == [(0, 40 * f + f // 2), (40 * f + f // 2, 12000)]

    # the last whole frame of the window is a candidate, the frame beyond it is not
    high = signal(12000)
    high[49 * f : 50 * f] = [1] * f
    zero_block(high, 50 * f)
    assert run(high) == [(0, 49 * f + f // 2), (49 * f + f // 2, 12000)]
    assert 49 * f + f // 2 < 8000, "the cut is inside the window"

    # energy is a sum of squares: a frame of +2000/-2000 (sum 0) is loud, not quiet
    loud = signal(12000, base=500)
    loud[45 * f : 46 * f] = [2000 if i % 2 == 0 else -2000 for i in range(f)]
    assert run(loud) == [(0, 49 * f + f // 2), (49 * f + f // 2, 12000)]

    # energy is the sum of squares, not of magnitudes: frame 47 holds a few loud impulses and is
    # quieter by magnitude (20 x 3000 = 60000 against 160 x 1000 = 160000) but louder by energy
    impulses = signal(12000)
    impulses[47 * f : 48 * f] = [3000] * 20 + [0] * (f - 20)
    assert run(impulses) == [(0, 49 * f + f // 2), (49 * f + f // 2, 12000)]

    # ties go to the latest frame: a flat signal is cut at the last candidate of every window
    flat = signal(20000)
    assert run(flat) == [(0, 7920), (7920, 15840), (15840, 20000)]

    # the 20 ms grid is counted from the start of each window, not from the start of the audio:
    # the second window starts at 6960, which is half a frame off the first grid
    second = signal(20000)
    zero_block(second, 43 * f)  # first window: quiet frame 43, cut at 6960
    zero_block(second, 6960 + 45 * f)  # second window: quiet frame 45 of its own grid
    assert run(second) == [(0, 6960), (6960, 6960 + 45 * f + f // 2), (14240, 20000)]

    # several channels: the energy is the sum over all channels. Frame 44 is silent on the left
    # only, frame 46 on the right only; neither is quiet, so the flat tie rule decides.
    stereo = [1000] * (12000 * 2)
    for frame in range(44 * f, 45 * f):
        stereo[2 * frame] = 0
        stereo[2 * frame + 1] = 2000
    for frame in range(46 * f, 47 * f):
        stereo[2 * frame] = 2000
        stereo[2 * frame + 1] = 0
    plan = plan_of(
        stereo,
        channels=2,
        rate=rate,
        max_chunk_ms=max_ms,
        search_ms=search_ms,
        accept_provider_downmix=True,
    )
    assert ranges_of(plan) == [(0, 49 * f + f // 2), (49 * f + f // 2, 12000)]
    assert ranges_of(plan) == reference_ranges(
        stereo, channels=2, rate=rate, max_ms=max_ms, search_ms=search_ms
    )

    # the smallest legal window: a window of one frame, searched whole, cut at its middle sample
    tiny = plan_of(signal(400), rate=rate, max_chunk_ms=20, search_ms=20)
    assert ranges_of(tiny) == [(0, 80), (80, 160), (160, 240), (240, 400)]


@pytest.mark.parametrize("seed", range(24))
def test_chunker_matches_the_reference_on_seeded_random_signals(seed: int) -> None:
    rng = random.Random(1000 + seed)  # noqa: S311 - a seeded test signal, not a secret
    rate = rng.choice([8000, 11025, 16000, 22050, 24000, 44100])
    channels = rng.choice([1, 2])
    max_ms = rng.choice([100, 250, 500, 1000, 1500])
    search_ms = rng.choice([40, 60, 100, max_ms // 2, max_ms])
    window = rate * max_ms // 1000
    frame = rate * 20 // 1000
    region = rate * search_ms // 1000
    frames = rng.randint(1, min(5 * window, 120_000))
    samples = noise(frames, channels, seed=seed)
    for block in rng.sample(range(frames // frame + 1), k=min(8, frames // frame + 1)):
        # plant quiet stretches at random places so that energy ties are rare
        lo, hi = block * frame * channels, (block + 1) * frame * channels
        samples[lo:hi] = [rng.randint(-3, 3) for _ in samples[lo:hi]]
    has_candidate = any(
        k * frame >= window - region and (k + 1) * frame <= window for k in range(window // frame)
    )
    kwargs: dict[str, Any] = {
        "channels": channels,
        "rate": rate,
        "max_chunk_ms": max_ms,
        "search_ms": search_ms,
        "accept_provider_downmix": channels == 2,
    }
    if frames > window and not has_candidate:
        # a cut is needed and no whole 20 ms frame lies in the search region
        with pytest.raises(_chunker().ChunkerParameterError):
            plan_of(samples, **kwargs)
        return
    plan = plan_of(samples, **kwargs)
    assert ranges_of(plan) == reference_ranges(
        samples, channels=channels, rate=rate, max_ms=max_ms, search_ms=search_ms
    )


def test_chunker_short_audio_is_one_chunk() -> None:
    chunker = _chunker()
    for frames in (1, 7999, 8000):  # the window is 8000 frames
        samples = noise(frames, 1, seed=frames)
        plan = plan_of(samples, rate=8000, max_chunk_ms=1000, search_ms=200)
        assert ranges_of(plan) == [(0, frames)]
        assert plan.chunks[0].wav == canonical_wav(to_pcm(samples), 8000, 1)
    over = plan_of(noise(8001, 1, seed=1), rate=8000, max_chunk_ms=1000, search_ms=200)
    assert len(over.chunks) == 2
    # with the defaults a 24 kHz call of 25 s or less is a single request
    default = plan_of([5] * (24000 * 25), rate=24000)
    assert ranges_of(default) == [(0, 600000)]
    assert chunker.DEFAULT_MAX_CHUNK_MS == 25_000
    assert chunker.DEFAULT_SEARCH_MS == 5_000
    # an empty recording has no chunk (an empty request would be refused by the provider)
    empty = plan_of([], rate=8000)
    assert empty.chunks == ()
    assert empty.total_frames == 0
    assert empty.as_metadata()["chunks"] == []


def test_chunker_stereo_is_not_downmixed_silently() -> None:
    chunker = _chunker()
    samples = noise(20000, 2, seed=5)
    pcm = to_pcm(samples)
    # refused by default, whatever the audio holds, and before anything else is looked at
    with pytest.raises(chunker.ChannelsRefusedError):
        chunker.chunk_pcm(pcm, sample_rate_hz=8000, channels=2)
    with pytest.raises(chunker.ChannelsRefusedError):
        chunker.chunk_pcm(b"", sample_rate_hz=8000, channels=2)
    with pytest.raises(chunker.ChannelsRefusedError):
        chunker.chunk_pcm(pcm, sample_rate_hz=8000, channels=2, accept_provider_downmix=False)
    assert issubclass(chunker.ChannelsRefusedError, chunker.ChunkerError)
    # with the explicit opt-in the chunks stay stereo, sample for sample: the provider merges
    # the channels (docs), Tracer computes no downmix
    plan = chunker.chunk_pcm(
        pcm,
        sample_rate_hz=8000,
        channels=2,
        max_chunk_ms=1000,
        search_ms=200,
        accept_provider_downmix=True,
    )
    assert len(plan.chunks) > 1
    assert all(c.channels == 2 for c in plan.chunks)
    for c in plan.chunks:
        with wave.open(io.BytesIO(c.wav), "rb") as reader:
            assert reader.getnchannels() == 2
    body = b"".join(c.wav[44:] for c in plan.chunks)
    assert body == pcm, "every sample of both channels is kept as it is"
    # three or more channels are refused even with the opt-in (the provider rule is unknown)
    with pytest.raises(chunker.ChannelsRefusedError):
        chunker.chunk_pcm(
            to_pcm(noise(100, 3, seed=1)),
            sample_rate_hz=8000,
            channels=3,
            accept_provider_downmix=True,
        )
    # the flag has no effect on mono
    mono = chunker.chunk_pcm(
        to_pcm(noise(100, 1, seed=1)),
        sample_rate_hz=8000,
        channels=1,
        accept_provider_downmix=True,
    )
    assert mono.chunks[0].channels == 1


# ------------------------------------------------------------- added by qa (contract section 5)


def test_chunk_wav_bytes_are_the_canonical_pcm_file() -> None:
    samples = noise(500, 1, seed=11)
    plan = plan_of(samples, rate=8000, max_chunk_ms=20, search_ms=20)
    assert len(plan.chunks) == 6  # 160-frame windows, each cut at its middle sample
    for c in plan.chunks:
        pcm = to_pcm(samples[c.start_frame : c.end_frame])
        assert c.wav == canonical_wav(pcm, 8000, 1)
        assert len(c.wav) == 44 + len(pcm)
    stereo = noise(300, 2, seed=12)
    st_plan = plan_of(
        stereo,
        channels=2,
        rate=16000,
        max_chunk_ms=1000,
        search_ms=200,
        accept_provider_downmix=True,
    )
    assert st_plan.chunks[0].wav == canonical_wav(to_pcm(stereo), 16000, 2)


def test_chunker_parameters_are_validated() -> None:
    chunker = _chunker()
    base = {"sample_rate_hz": 8000, "channels": 1}
    pcm = to_pcm(noise(20000, 1, seed=2))

    def run(**over: Any) -> Any:
        return chunker.chunk_pcm(pcm, **{**base, **over})

    for name, bad in (
        ("max_chunk_ms", 0),
        ("max_chunk_ms", -5),
        ("max_chunk_ms", True),
        ("max_chunk_ms", 1000.0),
        ("max_chunk_ms", "1000"),
        ("search_ms", -1),
        ("search_ms", False),
        ("search_ms", 20.0),
        ("sample_rate_hz", 0),
        ("sample_rate_hz", -8000),
        ("sample_rate_hz", True),
        ("sample_rate_hz", 8000.0),
        ("channels", 0),
        ("channels", -1),
        ("channels", True),
        ("channels", 1.0),
    ):
        with pytest.raises(chunker.ChunkerParameterError):
            run(**{name: bad})
    with pytest.raises(chunker.ChunkerParameterError):
        run(max_chunk_ms=1000, search_ms=1001)  # the search region is part of the window
    # a window without one whole 20 ms frame in its search region cannot be cut
    with pytest.raises(chunker.ChunkerParameterError):
        run(max_chunk_ms=1000, search_ms=0)
    with pytest.raises(chunker.ChunkerParameterError):
        run(max_chunk_ms=1000, search_ms=19)
    with pytest.raises(chunker.ChunkerParameterError):
        run(max_chunk_ms=10, search_ms=10)
    # ... but audio that needs no cut never looks at the search region
    assert (
        len(chunker.chunk_pcm(b"\x00\x00" * 100, **base, max_chunk_ms=1000, search_ms=0).chunks)
        == 1
    )
    # the audio itself
    with pytest.raises(chunker.ChunkerInputError):
        chunker.chunk_pcm(b"\x00\x00\x00", **base)  # not a whole number of samples
    with pytest.raises(chunker.ChunkerInputError):
        chunker.chunk_pcm(
            b"\x00\x00" * 3, sample_rate_hz=8000, channels=2, accept_provider_downmix=True
        )
    for wrong in ("text", [1, 2], None, 12, bytearray(b"\x00\x00"), memoryview(b"\x00\x00")):
        with pytest.raises(TypeError):
            chunker.chunk_pcm(wrong, **base)
    # the channels are judged before the audio: three bytes in stereo are refused as stereo
    with pytest.raises(chunker.ChannelsRefusedError):
        chunker.chunk_pcm(b"\x00\x00\x00", sample_rate_hz=8000, channels=2)
    for err in (
        chunker.ChunkerParameterError,
        chunker.ChunkerInputError,
        chunker.ChannelsRefusedError,
    ):
        assert issubclass(err, chunker.ChunkerError)
    assert issubclass(
        chunker.ChunkerError, importlib.import_module("tracer_evalcore.errors").InputRefusedError
    )
    assert issubclass(chunker.ChunkerError, ValueError)
    # the parameter spelling
    params = inspect.signature(chunker.chunk_pcm).parameters
    assert list(params) == [
        "pcm",
        "sample_rate_hz",
        "channels",
        "max_chunk_ms",
        "search_ms",
        "accept_provider_downmix",
    ]
    assert params["pcm"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for n, p in params.items() if n != "pcm")
    assert params["max_chunk_ms"].default == 25_000
    assert params["search_ms"].default == 5_000
    assert params["accept_provider_downmix"].default is False
    assert params["sample_rate_hz"].default is inspect.Parameter.empty
    assert params["channels"].default is inspect.Parameter.empty


def test_chunk_plan_metadata_is_integers_only_and_fits_a_transcript() -> None:
    plan = plan_of(noise(30000, 1, seed=9), rate=8000, max_chunk_ms=1000, search_ms=200)
    meta = plan.as_metadata()
    assert set(meta) == {
        "format",
        "max_chunk_ms",
        "search_ms",
        "sample_rate_hz",
        "channels",
        "total_frames",
        "chunks",
    }
    assert meta["format"] == "tracer.chunk_plan/1"
    for key in ("max_chunk_ms", "search_ms", "sample_rate_hz", "channels", "total_frames"):
        assert type(meta[key]) is int, key
    assert meta["max_chunk_ms"] == 1000
    assert meta["search_ms"] == 200
    assert meta["sample_rate_hz"] == 8000
    assert meta["channels"] == 1
    assert meta["total_frames"] == 30000
    assert meta["chunks"] == [
        {"index": c.index, "start_frame": c.start_frame, "end_frame": c.end_frame}
        for c in plan.chunks
    ]
    assert json.loads(json.dumps(meta)) == meta
    assert all(type(v) is int for entry in meta["chunks"] for v in entry.values())
    # the document is a valid Transcript.metadata value and trips no metadata flag
    transcript = Transcript(
        id=TranscriptId.new(),
        schema_version=SCHEMA_VERSION,
        created_at=datetime(2026, 10, 25, tzinfo=UTC),
        source="our_stt",
        privacy=PrivacyMarker(
            human_subject=True,
            consent=ConsentRecord(status="unknown"),
            retention=RetentionPolicy(delete_on_expiry=False),
        ),
        metadata={"tracer.chunk_plan": meta},
    )
    assert transcript.metadata_flags == []
    # as_metadata returns a fresh object: changing it does not change the plan
    meta["chunks"].clear()
    assert len(plan.as_metadata()["chunks"]) == len(plan.chunks)


def test_chunk_durations_are_exact_or_absent() -> None:
    plan = plan_of([7] * 36000, rate=24000, max_chunk_ms=25000)
    only = plan.chunks[0]
    assert only.duration_seconds == Decimal("1.5")
    assert only.start_seconds == Decimal(0)
    odd = plan_of([7] * 8001, rate=8000, max_chunk_ms=1000, search_ms=200)
    first, second = odd.chunks
    assert first.start_seconds == Decimal(0)
    assert second.start_seconds is not None
    assert second.start_seconds * 8000 == second.start_frame
    third_of_a_frame = plan_of([7] * 5, rate=24000)
    assert third_of_a_frame.chunks[0].duration_seconds is None  # 5/24000 has no finite decimal


def test_chunk_wav_reads_the_audio_of_the_reader(tmp_path: Path) -> None:
    wav = _wav()
    chunker = _chunker()
    samples = noise(25600, 1, seed=21)  # 3.2 s at 8 kHz
    path = tmp_path / "c.wav"
    path.write_bytes(canonical_wav(to_pcm(samples), 8000, 1))
    audio = wav.read_wav(
        path,
        created_at="2026-10-25T09:00:00.000000Z",
        consent=ConsentRecord(status="unknown"),
        retention=RetentionPolicy(delete_on_expiry=False),
    )
    plan = chunker.chunk_wav(audio, max_chunk_ms=1000, search_ms=200)
    assert ranges_of(plan) == reference_ranges(
        samples, channels=1, rate=8000, max_ms=1000, search_ms=200
    )
    assert len(plan.chunks) >= 4
    assert b"".join(c.wav[44:] for c in plan.chunks) == audio.pcm
    assert list(inspect.signature(chunker.chunk_wav).parameters) == [
        "audio",
        "max_chunk_ms",
        "search_ms",
        "accept_provider_downmix",
    ]
