# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-33: the F6 mapping (Pipecat ``AudioBufferProcessor`` recording, a WAV file) over the real
captures.

Fixtures: ``tests/fixtures/real/pipecat-1.13.0/{en,en_tool,hi}/f6_stereo.wav`` (16 kHz, 16-bit,
two channels, synthetic voices). Converter under test:
``tests/support/mappings/pipecat_audio.py::map_f6`` (docs/phase1/api-surface-mappings.md
section 6); it does not exist yet.

The WAV header says nothing about WHO is on a channel. The role of each channel is therefore not a
fact of the file: it is the documented layout of the producer (``AudioBufferProcessor``: 'Stereo
output (num_channels=2): User audio on left, bot audio on right'; 'Mono output: User and bot audio
are mixed', pipecat-ai 1.13.0), recorded as a ``declared`` channel map and marked in the field
provenance as documentation-derived (derivation ``documented_stereo_*`` with the citation). It is
never inferred from the samples (ADR 0012 Decision 5: 'Assume channel 0 = caller' is rejected unless
declared).
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b6 import (
    PIPECAT,
    SCENARIOS,
    check_records_valid,
    f6_bytes,
    run_f6,
    wav_fields,
    wav_mono_16,
    wav_stereo_16,
)

SCEN = pytest.mark.parametrize("scenario", SCENARIOS)


def _codes(result: Any) -> list[str]:
    return [w.code for w in result.warnings]


@SCEN
def test_f6_validates(scenario: str) -> None:
    """Every real recording maps to one Artifact and one ChannelMap that names it. The sha256 and
    the size are those of the file bytes (computed, nothing else is claimed: the file has no turn
    reference), the uri is the one the caller passes, the privacy marker the caller's."""
    path = PIPECAT / scenario / "f6_stereo.wav"
    data = path.read_bytes()
    uri = path.resolve().as_uri()
    result = run_f6(data, uri=uri)
    assert result.fixture == "F6"
    check_records_valid(result)
    assert len(result.artifacts) == 1
    assert len(result.channel_maps) == 1
    artifact = result.artifacts[0]
    assert artifact.uri == uri
    assert artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert artifact.size == len(data)
    assert artifact.privacy.human_subject is False
    assert artifact.turn_ref is None
    assert result.channel_maps[0].artifact_id == artifact.id
    for empty in (result.transcripts, result.turns, result.usage_records, result.stage_spans):
        assert empty == ()


@SCEN
def test_f6_audio_format_16bit(scenario: str) -> None:
    """The format fields are read from the header: container ``wav``, codec ``pcm_s16le`` (format
    tag 1, 16 bits, RIFF is little-endian), the file's own sample rate, channel count and bits per
    sample (the Pipecat default sample rate is not assumed)."""
    data = f6_bytes(scenario)
    header = wav_fields(data)
    fmt = run_f6(data).artifacts[0].audio_format
    assert (fmt.container, fmt.codec) == ("wav", "pcm_s16le")
    assert fmt.sample_rate_hz == header["fmt/sample_rate_hz"] == 16000
    assert fmt.channels == header["fmt/channels"] == 2
    assert fmt.bits_per_sample == header["fmt/bits_per_sample"] == 16


@SCEN
def test_f6_stereo_left_user_right_bot_declared(scenario: str) -> None:
    """Channel 0 (left) is the caller and channel 1 (right) the agent, ``determination`` is
    ``declared`` with no confidence (a declaration is not a probability) and the method names the
    declaration. The roles are documentation-derived: their trace entries use the derivations
    ``documented_stereo_left_user`` / ``documented_stereo_right_bot`` and cite the Pipecat source
    (URL and retrieved_at), P (M21)."""
    result = run_f6(f6_bytes(scenario))
    channel_map = result.channel_maps[0]
    assert [(c.index, c.role) for c in channel_map.channels] == [(0, "caller"), (1, "agent")]
    assert channel_map.determination == "declared"
    assert channel_map.confidence is None
    assert channel_map.method == "pipecat_audio_buffer_processor_stereo"
    key = f"channel_map/{channel_map.artifact_id}"
    roles = {e.field: e for e in result.trace if e.record == key and e.field.endswith("/role")}
    assert roles["/channels/0/role"].derivation == "documented_stereo_left_user"
    assert roles["/channels/1/role"].derivation == "documented_stereo_right_bot"
    for entry in roles.values():
        assert entry.sources == ("wav:fmt/channels",)
        assert entry.citation is not None
        assert "v1.13.0" in entry.citation.url
        assert "audio_buffer_processor" in entry.citation.url
        assert entry.citation.retrieved_at == "2026-10-23"
    # exactly one agent channel, so the agent audio input is available (ADR 0004)
    assert sum(1 for c in channel_map.channels if c.role == "agent") == 1


def test_f6_roles_are_declared_not_inferred_from_the_samples() -> None:
    """Swapping what is in the channels changes nothing in the map: a recording whose left
    channel is silent and whose right channel is loud, its mirror image and an all-silent file get
    the same declared layout. An implementation that measured the energy would fail here."""
    loud, quiet = b"\xff\x7f", b"\x00\x00"
    layouts = []
    for left, right in ((quiet, loud), (loud, quiet), (quiet, quiet)):
        result = run_f6(wav_stereo_16(left, right))
        layouts.append([(c.index, c.role) for c in result.channel_maps[0].channels])
        assert result.channel_maps[0].determination == "declared"
    assert layouts[0] == layouts[1] == layouts[2] == [(0, "caller"), (1, "agent")]


def test_f6_mono_is_one_mixed_channel() -> None:
    """Pipecat: 'Mono output: User and bot audio are mixed' (ADR 0012 P-31): one channel with role
    ``mixed``, declared, so no agent channel exists and agent audio stays unavailable."""
    result = run_f6(wav_mono_16())
    channel_map = result.channel_maps[0]
    assert [(c.index, c.role) for c in channel_map.channels] == [(0, "mixed")]
    assert channel_map.determination == "declared"
    assert channel_map.method == "pipecat_audio_buffer_processor_mono"
    entry = next(e for e in result.trace if e.field == "/channels/0/role")
    assert entry.derivation == "documented_mono_mixed"
    assert entry.citation is not None
    assert result.artifacts[0].audio_format.channels == 1


def test_f6_more_than_two_channels_have_no_documented_layout() -> None:
    """Pipecat documents one and two channels only: three channels give the Artifact (the format
    is a fact of the header) and no channel map, with ``channel_layout_not_documented``."""
    fmt = struct.pack("<HHIIHH", 1, 3, 16000, 96000, 6, 16)
    data = b"\x00\x00" * 3 * 4
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    result = run_f6(wav)
    assert len(result.artifacts) == 1
    assert result.artifacts[0].audio_format.channels == 3
    assert result.channel_maps == ()
    assert _codes(result) == ["channel_layout_not_documented"]


def test_f6_other_sample_rate_is_read_from_the_header() -> None:
    fmt = struct.pack("<HHIIHH", 1, 2, 8000, 32000, 4, 16)
    data = b"\x00\x00" * 2 * 4
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    assert run_f6(wav).artifacts[0].audio_format.sample_rate_hz == 8000


def _riff(tag: int, channels: int, rate: int, bits: int) -> bytes:
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    data = b"\x00" * block * 4
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize(
    "data",
    [
        _riff(3, 2, 16000, 32),
        _riff(1, 2, 16000, 8),
        _riff(1, 2, 16000, 24),
        _riff(0xFFFE, 2, 16000, 16),
        b"",
        b"RIFF",
        b"not a wav file at all, just text",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",
        wav_stereo_16()[:30],
    ],
)
def test_f6_unsupported_or_malformed_audio_is_a_warning(data: bytes) -> None:
    """Adversarial (malformed audio): only 16-bit integer PCM is mapped. Float, 8-bit, 24-bit,
    extensible, truncated and non-WAV input give no record and ``wav_format_not_supported``; no
    codec is guessed and nothing raises."""
    result = run_f6(data)
    assert result.records == ()
    assert result.channel_maps == ()
    assert _codes(result) == ["wav_format_not_supported"]


def test_f6_empty_recording_with_no_samples_is_still_an_artifact() -> None:
    """An empty call yields a valid header with no frames: the Artifact and the declared map exist
    (the file is real, its size is that of the bytes); no duration is invented."""
    result = run_f6(wav_stereo_16(frames=0))
    assert len(result.artifacts) == 1
    assert len(result.channel_maps) == 1
    assert result.artifacts[0].size == len(wav_stereo_16(frames=0))


def test_f6_large_recording() -> None:
    """Adversarial: a 20 MB recording is hashed and sized without error."""
    data = wav_stereo_16(frames=5_000_000)
    assert len(data) > 19_000_000
    result = run_f6(data)
    assert result.artifacts[0].size == len(data)
    assert result.artifacts[0].sha256 == hashlib.sha256(data).hexdigest()


def test_f6_uri_is_the_callers_and_a_bad_one_is_rejected_by_the_artifact_model(
    tmp_path: Path,
) -> None:
    """The mapping never builds a location: the uri is an argument. A uri the Artifact model
    rejects (relative path, ADR 0007 Decision 6) raises the model's own error; it is the caller's
    input, not content, so it is not softened into a warning."""
    with pytest.raises((api("errors").TracerSchemaError, ValidationError)):
        run_f6(f6_bytes("en"), uri="recordings/call.wav")
    ok = (tmp_path / "call.wav").as_uri()
    assert run_f6(f6_bytes("en"), uri=ok).artifacts[0].uri == ok
