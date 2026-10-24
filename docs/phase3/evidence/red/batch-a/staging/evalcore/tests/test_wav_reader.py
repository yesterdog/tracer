# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-03: the WAV reader (contract docs/phase3/api-surface-batch-a.md section 3).

Every WAV file is generated here, byte by byte, into ``tmp_path``: no audio file is read from the
repository and no real recording is touched. ``tracer_evalcore`` is loaded lazily so that, before
the package exists, each test fails on its own with ``ModuleNotFoundError``.
"""

from __future__ import annotations

import builtins
import contextlib
import dataclasses
import hashlib
import importlib
import inspect
import io
import os
import socket
import stat
import struct
import sys
import tracemalloc
from array import array
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from tracer_evalschema.artifact import Artifact, AudioFormat
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy

CREATED_AT = "2026-10-25T09:00:00.000000Z"
SENTINEL = b"PRIVATE-CONTENT-SENTINEL"


def _wav() -> Any:
    return importlib.import_module("tracer_evalcore.importers.wav")


def _seconds() -> Any:
    return importlib.import_module("tracer_evalcore.audio.seconds")


def _errors() -> Any:
    return importlib.import_module("tracer_evalcore.errors")


# ------------------------------------------------------------------------- byte-level builders


def pcm16(frames: int, channels: int = 1, seed: int = 1) -> bytes:
    """Deterministic little-endian 16-bit samples, interleaved, none of them zero by accident."""
    samples = array("h", [((i * 37 + seed * 101) % 2001) - 1000 for i in range(frames * channels)])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def chunk(chunk_id: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) % 2 else b""
    return chunk_id + struct.pack("<I", len(payload)) + payload + pad


def riff(
    pcm: bytes = b"",
    *,
    tag: int = 1,
    channels: int = 1,
    rate: int = 24000,
    bits: int = 16,
    data_size: int | None = None,
    before_data: bytes = b"",
    after_data: bytes = b"",
    fmt_payload: bytes | None = None,
) -> bytes:
    block = channels * bits // 8
    fmt = fmt_payload
    if fmt is None:
        fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    declared = len(pcm) if data_size is None else data_size
    data = b"data" + struct.pack("<I", declared) + pcm + (b"\x00" if len(pcm) % 2 else b"")
    body = b"WAVE" + chunk(b"fmt ", fmt) + before_data + data + after_data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def read(path: Path | str, **over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "created_at": CREATED_AT,
        "consent": ConsentRecord(status="obtained"),
        "retention": RetentionPolicy(delete_on_expiry=False),
    }
    kwargs.update(over)
    return _wav().read_wav(path, **kwargs)


# ------------------------------------------------------------------------------ the SCOPE tests


def test_wav_reader_reads_mono_24k_16bit_exact_duration(tmp_path: Path) -> None:
    pcm = pcm16(36000)  # 1.5 s at 24 kHz, the base-case format
    content = riff(pcm)
    path = write(tmp_path / "call.wav", content)
    audio = read(path)
    assert audio.frames == 36000
    assert audio.sample_rate_hz == 24000
    assert audio.channels == 1
    assert type(audio.duration_seconds) is Decimal
    assert audio.duration_seconds == Decimal("1.5")
    assert audio.pcm == pcm
    artifact = audio.artifact
    assert isinstance(artifact, Artifact)
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert artifact.size == len(content)
    assert artifact.audio_format == AudioFormat(
        container="wav", codec="pcm_s16le", sample_rate_hz=24000, channels=1, bits_per_sample=16
    )
    assert artifact.turn_ref is None
    assert artifact.created_at == datetime(2026, 10, 25, 9, 0, 0, tzinfo=UTC)

    # the duration is frames divided by the rate, as a decimal, and None when no finite decimal
    # with at most 18 fractional digits equals it (never rounded, never a float)
    assert read(write(tmp_path / "a.wav", riff(pcm16(24000)))).duration_seconds == Decimal(1)
    one_at_8k = read(write(tmp_path / "b.wav", riff(pcm16(1), rate=8000)))
    assert one_at_8k.duration_seconds == Decimal("0.000125")
    one_at_24k = read(write(tmp_path / "c.wav", riff(pcm16(1))))
    assert one_at_24k.frames == 1
    assert one_at_24k.duration_seconds is None
    zero = read(write(tmp_path / "d.wav", riff(b"")))
    assert zero.frames == 0
    assert zero.duration_seconds == Decimal(0)
    assert zero.pcm == b""

    # chunks the reader does not know are skipped, before and after the data; the hash and the
    # size are those of the whole file
    padded = riff(
        pcm,
        before_data=chunk(b"LIST", b"INFOodd" + SENTINEL),
        after_data=chunk(b"LIST", b"tail"),
    )
    again = read(write(tmp_path / "e.wav", padded))
    assert again.pcm == pcm
    assert again.artifact.sha256 == hashlib.sha256(padded).hexdigest()
    assert again.artifact.size == len(padded)

    # stereo and another rate are read as they are; str and Path both work
    stereo_pcm = pcm16(1600, channels=2)
    stereo = read(str(write(tmp_path / "f.wav", riff(stereo_pcm, channels=2, rate=16000))))
    assert (stereo.frames, stereo.channels, stereo.sample_rate_hz) == (1600, 2, 16000)
    assert stereo.pcm == stereo_pcm
    assert stereo.duration_seconds == Decimal("0.1")
    assert stereo.artifact.audio_format == AudioFormat(
        container="wav", codec="pcm_s16le", sample_rate_hz=16000, channels=2, bits_per_sample=16
    )


def test_wav_reader_rejects_non_pcm_and_8_or_24_bit(tmp_path: Path) -> None:
    wav = _wav()
    cases: dict[str, bytes] = {
        "ieee_float_32": riff(pcm16(10), tag=3, bits=32),
        "float_tag_16_bit": riff(pcm16(10), tag=3),
        "mp3_tag": riff(pcm16(10), tag=0x55),
        "a_law_8": riff(pcm16(10), tag=6, bits=8),
        "pcm_8_bit": riff(b"\x80" * 20, bits=8),
        "pcm_24_bit": riff(b"\x00" * 30, bits=24),
        "pcm_32_bit": riff(b"\x00" * 40, bits=32),
        "pcm_12_bit": riff(b"\x00" * 20, bits=12),
        "zero_channels": riff(b"", channels=0),
        "zero_rate": riff(pcm16(10), rate=0),
        # the extensible tag (0xFFFE) is refused on every Python: only the plain PCM tag 1 is read
        "extensible": riff(
            pcm16(10),
            fmt_payload=struct.pack("<HHIIHH", 0xFFFE, 1, 24000, 48000, 2, 16)
            + struct.pack("<HHI", 22, 16, 4)
            + b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71",
        ),
    }
    for name, content in cases.items():
        path = write(tmp_path / f"{name}.wav", content)
        with pytest.raises(wav.WavUnsupportedFormatError) as caught:
            read(path)
        assert type(caught.value) is wav.WavUnsupportedFormatError, name
    # the typed errors share one family
    err = _errors()
    for cls in (
        wav.WavUnsupportedFormatError,
        wav.WavMalformedError,
        wav.WavTruncatedError,
        wav.WavTooLargeError,
    ):
        assert issubclass(cls, wav.WavError)
    assert issubclass(wav.WavError, err.InputRefusedError)
    assert issubclass(err.InputRefusedError, err.EvalcoreError)
    assert issubclass(err.InputRefusedError, ValueError)
    assert issubclass(err.EvalcoreError, Exception)


def test_wav_reader_rejects_truncated_and_lying_header(tmp_path: Path) -> None:
    wav = _wav()
    good = pcm16(1000)
    truncated: dict[str, bytes] = {
        # the data chunk says 1000 frames, the file holds 400 bytes of them
        "data_cut": riff(good[:400], data_size=len(good)),
        # a streaming writer's placeholder, or any size beyond the file
        "placeholder_max": riff(good[:200], data_size=0xFFFFFFFF),
        "one_byte_beyond": riff(good, data_size=len(good) + 1),
        # the size is not a whole number of frames: the last frame is cut
        "partial_frame": riff(good[:301], data_size=301),
        "stereo_partial_frame": riff(pcm16(10, channels=2)[:-2], channels=2),
        # the file ends inside the fmt chunk
        "ends_in_fmt": riff(good)[:30],
    }
    for name, content in truncated.items():
        path = write(tmp_path / f"{name}.wav", content)
        with pytest.raises(wav.WavError) as caught:
            read(path)
        if name != "ends_in_fmt":
            assert type(caught.value) is wav.WavTruncatedError, name
        assert SENTINEL.decode()[:8] not in str(caught.value)

    # a header that claims 4 GiB in a tiny file must be refused without allocating for the claim
    path = write(tmp_path / "huge_claim.wav", riff(good[:100], data_size=0xFFFFFFF0))
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        with pytest.raises(wav.WavTruncatedError):
            read(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak - before < 8 * 1024 * 1024, "memory was allocated for the size the header claims"

    # not a WAV at all
    junk: dict[str, bytes] = {
        "empty": b"",
        "four_bytes": b"RIFF",
        "text": b"hello, this is not audio\n" * 10,
        "riff_not_wave": b"RIFF" + struct.pack("<I", 4) + b"AVI ",
        "no_data_chunk": b"RIFF"
        + struct.pack("<I", 28)
        + b"WAVE"
        + chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)),
        "no_fmt_chunk": b"RIFF" + struct.pack("<I", 12) + b"WAVE" + chunk(b"data", b"\x00\x00"),
    }
    for name, content in junk.items():
        with pytest.raises(wav.WavError):
            read(write(tmp_path / f"junk_{name}.wav", content))

    # the content of a rejected file is never put into the message
    leaky = write(
        tmp_path / "leaky.wav",
        riff(good[:50], data_size=5000, before_data=chunk(b"LIST", SENTINEL)),
    )
    with pytest.raises(wav.WavTruncatedError) as caught:
        read(leaky)
    assert SENTINEL.decode()[:8] not in str(caught.value)
    assert SENTINEL.decode()[:8] not in repr(caught.value)

    # an operating-system error is not rewrapped
    with pytest.raises(FileNotFoundError):
        read(tmp_path / "missing.wav")


class _CountingFile:
    """Wraps a real file object and counts the bytes handed out by any read method."""

    def __init__(self, real: Any, counter: list[int]) -> None:
        self._real = real
        self._counter = counter

    def _count(self, data: Any) -> Any:
        self._counter[0] += len(data)
        return data

    def read(self, *args: Any) -> Any:
        return self._count(self._real.read(*args))

    def read1(self, *args: Any) -> Any:
        return self._count(self._real.read1(*args))

    def readline(self, *args: Any) -> Any:
        return self._count(self._real.readline(*args))

    def readinto(self, buffer: Any) -> Any:
        got = self._real.readinto(buffer)
        self._counter[0] += got or 0
        return got

    def __iter__(self) -> Any:
        for line in self._real:
            self._counter[0] += len(line)
            yield line

    def __enter__(self) -> _CountingFile:
        self._real.__enter__()
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._real.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@contextlib.contextmanager
def counted_reads() -> Iterator[list[int]]:
    """Counts every byte that Python-level file reads hand out while the block runs."""
    counter = [0]
    real_open = builtins.open
    real_os_read = os.read

    def counting_open(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
        return _CountingFile(handle, counter) if "b" in str(mode) else handle

    def counting_os_read(fd: int, n: int) -> bytes:
        data = real_os_read(fd, n)
        counter[0] += len(data)
        return data

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "open", counting_open)
        patch.setattr(io, "open", counting_open)
        patch.setattr(os, "read", counting_os_read)
        yield counter


def test_wav_reader_rejects_oversize_before_reading(tmp_path: Path) -> None:
    wav = _wav()
    content = riff(pcm16(500))
    path = write(tmp_path / "exact.wav", content)

    # the boundary: a file of exactly max_bytes is read, one byte more is refused
    assert read(path, max_bytes=len(content)).frames == 500
    with counted_reads() as counter, pytest.raises(wav.WavTooLargeError) as caught:
        read(path, max_bytes=len(content) - 1)
    assert counter[0] == 0, "the size is read from the file system before any byte is read"
    assert type(caught.value) is wav.WavTooLargeError

    # the default cap is a named constant and is applied without reading a sparse file
    assert wav.DEFAULT_MAX_WAV_BYTES == 1_073_741_824
    assert inspect.signature(wav.read_wav).parameters["max_bytes"].default == 1_073_741_824
    big = tmp_path / "sparse.wav"
    with big.open("wb") as handle:
        handle.truncate(wav.DEFAULT_MAX_WAV_BYTES + 1)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        with pytest.raises(wav.WavTooLargeError):
            read(big)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak - before < 8 * 1024 * 1024
    big.unlink()

    # the cap itself is checked
    for bad in (0, -1):
        with pytest.raises(ValueError, match="max_bytes"):
            read(path, max_bytes=bad)
    for wrong in (True, 1.5, "100", None):
        with pytest.raises(TypeError):
            read(path, max_bytes=wrong)


def test_wav_reader_refuses_non_regular_files_without_reading(tmp_path: Path) -> None:
    """A device or a pipe has no size to compare with the cap, so it is never read."""
    wav = _wav()
    path = write(tmp_path / "device.wav", riff(pcm16(10)))
    real_fstat = os.fstat

    for mode in (stat.S_IFCHR, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFBLK):

        def fstat_of(fd: int, mode: int = mode) -> os.stat_result:
            real = real_fstat(fd)
            return os.stat_result((mode | 0o666, real.st_ino, real.st_dev, 1, 0, 0, 0, 0, 0, 0))

        with counted_reads() as counter, pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "fstat", fstat_of)
            with pytest.raises(wav.WavMalformedError) as caught:
                read(path)
        assert counter[0] == 0, f"mode {mode:o}: bytes were read from a non-regular file"
        assert type(caught.value) is wav.WavMalformedError


def test_artifact_uri_is_absolute_file_uri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = riff(pcm16(100))
    plain = write(tmp_path / "plain.wav", content)
    uri = read(plain).artifact.uri
    assert uri == plain.resolve().as_uri()
    assert uri.startswith("file:///")

    # a relative path is made absolute against the working directory at the time of the call
    (tmp_path / "rel").mkdir()
    write(tmp_path / "rel" / "x.wav", content)
    monkeypatch.chdir(tmp_path)
    relative = read(Path("rel") / "x.wav").artifact.uri
    assert relative == (tmp_path / "rel" / "x.wav").resolve().as_uri()
    assert "/rel/rel/" not in relative
    assert read("rel/../rel/x.wav").artifact.uri == relative, "no dot segment survives"

    # a symbolic link names the file whose bytes were read
    link = tmp_path / "link.wav"
    link.symlink_to(plain)
    assert read(link).artifact.uri == plain.resolve().as_uri()

    # characters that a URI cannot carry are percent-encoded: ASCII only, no query, no fragment
    awkward = write(tmp_path / ("caf\u00e9 \u092e\u0930\u093e\u0920\u0940 #1 ?q 100%.wav"), content)
    artifact = read(awkward).artifact
    parts = urlsplit(artifact.uri)
    assert artifact.uri.isascii()
    assert parts.scheme == "file"
    assert parts.netloc == ""
    assert parts.query == ""
    assert parts.fragment == ""
    assert unquote(parts.path) == str(awkward.resolve())
    # and the same bytes give the same hash whatever the name
    assert artifact.sha256 == read(plain).artifact.sha256

    # the model itself accepts the URI (the schema's own file-URI rules)
    assert Artifact.model_validate(artifact.model_dump(mode="json")) == artifact


def test_artifact_privacy_marker_requires_explicit_consent_input(tmp_path: Path) -> None:
    wav = _wav()
    path = write(tmp_path / "p.wav", riff(pcm16(100)))
    base = {"created_at": CREATED_AT}

    # consent and retention have no default: leaving either out is a TypeError, not a guess
    with pytest.raises(TypeError):
        wav.read_wav(path, **base, retention=RetentionPolicy(delete_on_expiry=True))
    with pytest.raises(TypeError):
        wav.read_wav(path, **base, consent=ConsentRecord(status="obtained"))
    with pytest.raises(TypeError):
        wav.read_wav(path, **base)
    # and nothing is coerced: a bare string or a dict is not a consent record
    retention = RetentionPolicy(delete_on_expiry=False)
    with pytest.raises(TypeError):
        wav.read_wav(path, **base, consent="obtained", retention=retention)
    with pytest.raises(TypeError):
        wav.read_wav(path, **base, consent={"status": "obtained"}, retention=retention)
    with pytest.raises(TypeError):
        wav.read_wav(
            path,
            **base,
            consent=ConsentRecord(status="obtained"),
            retention={"delete_on_expiry": False},
        )

    # each status is carried through as given; "unknown" stays "unknown"
    for status in ("obtained", "not_obtained", "unknown"):
        consent = ConsentRecord(status=status, reference="consent form 17")
        kept = RetentionPolicy(delete_on_expiry=True, policy_ref="ret-30d")
        marker = wav.read_wav(
            path, created_at=CREATED_AT, consent=consent, retention=kept
        ).artifact.privacy
        assert marker == PrivacyMarker(human_subject=True, consent=consent, retention=kept)
        assert marker.human_subject is True
        assert marker.human_subject_reason is None
        assert marker.consent is not None
        assert marker.consent.status == status
        assert marker.consent.reference == "consent form 17"
        assert marker.retention == kept


# ---------------------------------------------------------------- added by qa (contract section 3)


def test_wav_reader_signature_and_result_type() -> None:
    wav = _wav()
    params = inspect.signature(wav.read_wav).parameters
    assert list(params) == ["path", "created_at", "consent", "retention", "max_bytes"]
    assert params["path"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("created_at", "consent", "retention", "max_bytes"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    for name in ("created_at", "consent", "retention"):
        assert params[name].default is inspect.Parameter.empty, name
    result_type = wav.WavAudio
    assert dataclasses.is_dataclass(result_type)
    assert [f.name for f in dataclasses.fields(result_type)] == [
        "artifact",
        "frames",
        "sample_rate_hz",
        "channels",
        "duration_seconds",
        "pcm",
    ]


def test_wav_audio_is_frozen_and_does_not_print_samples(tmp_path: Path) -> None:
    path = write(tmp_path / "big.wav", riff(pcm16(48000)))
    audio = read(path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        audio.frames = 1
    assert len(audio.pcm) == 96000
    assert len(repr(audio)) < 2000, "the samples must not be part of the repr (logs, tracebacks)"
    assert SENTINEL.decode() not in repr(audio)


def test_wav_reader_ids_and_clock(tmp_path: Path) -> None:
    path = write(tmp_path / "t.wav", riff(pcm16(100)))
    first = read(path).artifact
    second = read(path).artifact
    assert first.id != second.id
    assert (first.sha256, first.size, first.uri) == (second.sha256, second.size, second.uri)
    # the creation time is the argument, never the clock
    other = read(path, created_at="2001-02-03T04:05:06.000000Z").artifact
    assert other.created_at.year == 2001
    assert first.created_at.year == 2026


def test_wav_reader_works_without_a_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the WAV reader opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert read(write(tmp_path / "n.wav", riff(pcm16(100)))).frames == 100


@pytest.mark.parametrize(
    ("rate", "frames", "expected"),
    [
        (24000, 36000, Decimal("1.5")),
        (24000, 24000, Decimal(1)),
        (24000, 0, Decimal(0)),
        (24000, 1, None),
        (24000, 3, Decimal("0.000125")),
        (8000, 1, Decimal("0.000125")),
        (44100, 44100, Decimal(1)),
        (44100, 1, None),
        (262144, 1, Decimal("0.000003814697265625")),  # 18 fractional digits: the limit
        (524288, 1, None),  # 19 fractional digits: more than a canonical decimal carries
        (3, 1, None),
        (1000, 254600, Decimal("254.6")),
    ],
)
def test_exact_seconds_is_exact_or_none(rate: int, frames: int, expected: Decimal | None) -> None:
    got = _seconds().exact_seconds(frames, rate)
    if expected is None:
        assert got is None
    else:
        assert type(got) is Decimal
        assert got == expected
        assert got * rate == frames, "exact: multiplying back gives the frame count"


def test_exact_seconds_validates_its_arguments() -> None:
    exact_seconds = _seconds().exact_seconds
    for frames, rate in ((-1, 24000), (1, 0), (1, -5)):
        with pytest.raises(ValueError, match=r"frames|rate"):
            exact_seconds(frames, rate)
    wrong_types: list[tuple[Any, Any]] = [
        (1.5, 24000),
        (1, 24000.0),
        ("1", 24000),
        (True, 24000),
        (1, True),
    ]
    for bad_frames, bad_rate in wrong_types:
        with pytest.raises(TypeError):
            exact_seconds(bad_frames, bad_rate)
