# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F6: a Pipecat ``AudioBufferProcessor`` recording (a WAV file) to an Artifact and a ChannelMap
(docs/phase1/api-surface-mappings.md section 6).

Only 16-bit integer PCM is mapped. The header says nothing about who is on a channel: the roles are
the producer's documented layout (stereo: user left, bot right; mono: mixed), recorded as a
``declared`` map with the citation, and never inferred from the samples.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from typing import Any

from tracer_evalschema.artifact import Artifact, ChannelMap

from .common import Citation, Draft, Leg, MappingResult, Recorder, TableRow, raw_text

__all__ = ["PROVENANCE_TABLE", "map_f6"]

RETRIEVED = "2026-10-23"
AUDIO_BUFFER_CITATION = Citation(
    url=(
        "https://github.com/pipecat-ai/pipecat/blob/v1.13.0/src/pipecat/processors/audio/"
        "audio_buffer_processor.py"
    ),
    retrieved_at=RETRIEVED,
    statement=(
        "AudioBufferProcessor (pipecat-ai 1.13.0): 'Mono output (num_channels=1): User and bot "
        "audio are mixed'; 'Stereo output (num_channels=2): User audio on left, bot audio on "
        "right'."
    ),
)

_FMT = "wav:fmt/"


def _read_header(data: bytes) -> dict[str, int] | None:
    """The fmt fields and the data length of a RIFF/WAVE file, or ``None`` if the file is not a
    complete one (no RIFF/WAVE header, no complete fmt chunk, no data chunk, or a data chunk that
    runs past the end of the file)."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    fields: dict[str, int] = {}
    position = 12
    while position + 8 <= len(data):
        chunk_id = data[position : position + 4]
        (size,) = struct.unpack_from("<I", data, position + 4)
        body = position + 8
        if chunk_id == b"fmt " and "format_tag" not in fields and size >= 16:
            if body + 16 > len(data):
                return None
            tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data, body)
            fields.update(
                format_tag=tag,
                channels=channels,
                sample_rate_hz=rate,
                byte_rate=byte_rate,
                block_align=align,
                bits_per_sample=bits,
            )
        if chunk_id == b"data":
            if body + size > len(data):
                return None
            fields["data_length"] = size
            break
        position = body + size + (size & 1)
    if "format_tag" not in fields or "data_length" not in fields:
        return None
    return fields


def map_f6(wav: bytes, *, uri: str, created_at: str, privacy: Mapping[str, Any]) -> MappingResult:
    rec = Recorder("F6", created_at, privacy)
    data = bytes(wav) if isinstance(wav, (bytes, bytearray, memoryview)) else b""
    header = _read_header(data)
    if header is None:
        rec.warn("wav_format_not_supported", "the file is not a complete RIFF/WAVE recording")
        return rec.result()
    rec.consume("wav:data/length")
    tag, bits, channels = header["format_tag"], header["bits_per_sample"], header["channels"]
    if tag != 1 or bits != 16:
        field_name = "format_tag" if tag != 1 else "bits_per_sample"
        rec.warn(
            "wav_format_not_supported",
            "only 16-bit integer PCM is mapped",
            path=_FMT + field_name,
            raw=raw_text(header[field_name]),
        )
        return rec.result()
    if channels < 1 or header["sample_rate_hz"] < 1:
        rec.warn(
            "wav_format_not_supported",
            "the header states no channel or no sample rate",
            path=_FMT + ("channels" if channels < 1 else "sample_rate_hz"),
            raw="0",
        )
        return rec.result()

    draft = Draft(Artifact, {"uri": uri, "privacy": privacy})
    draft.set(
        "sha256",
        hashlib.sha256(data).hexdigest(),
        Leg("/sha256", ("wav:bytes",), "sha256_of_bytes"),
    )
    draft.set("size", len(data), Leg("/size", ("wav:bytes",), "byte_length"))
    draft.set(
        "audio_format",
        {
            "container": "wav",
            "codec": "pcm_s16le",
            "sample_rate_hz": header["sample_rate_hz"],
            "channels": channels,
            "bits_per_sample": bits,
        },
        Leg("/audio_format/container", ("wav:riff",), "wav_container"),
        Leg(
            "/audio_format/codec", (_FMT + "format_tag", _FMT + "bits_per_sample"), "wav_pcm_codec"
        ),
        Leg("/audio_format/sample_rate_hz", (_FMT + "sample_rate_hz",)),
        Leg("/audio_format/channels", (_FMT + "channels",)),
        Leg("/audio_format/bits_per_sample", (_FMT + "bits_per_sample",)),
    )
    artifact = rec.commit(draft)
    if artifact is None:
        return rec.result()

    if channels > 2:
        rec.warn(
            "channel_layout_not_documented",
            "Pipecat documents the layout of one and two channel recordings only",
            path=_FMT + "channels",
            raw=raw_text(channels),
        )
        return rec.result()

    stereo = channels == 2
    layout = Draft(ChannelMap, {"artifact_id": artifact.id}, entity=False)
    cited = (_FMT + "channels",)
    if stereo:
        roles = [
            ("caller", "documented_stereo_left_user"),
            ("agent", "documented_stereo_right_bot"),
        ]
        method = "pipecat_audio_buffer_processor_stereo"
    else:
        roles = [("mixed", "documented_mono_mixed")]
        method = "pipecat_audio_buffer_processor_mono"
    legs: list[Leg] = []
    for index, (_role, derivation) in enumerate(roles):
        legs.append(Leg(f"/channels/{index}/index", (), f"constant:{index}"))
        legs.append(Leg(f"/channels/{index}/role", cited, derivation, AUDIO_BUFFER_CITATION))
    layout.set(
        "channels",
        [{"index": i, "role": role} for i, (role, _) in enumerate(roles)],
        *legs,
    )
    layout.set("determination", "declared", Leg("/determination", (), "constant:declared"))
    layout.set("method", method, Leg("/method", (), f"constant:{method}"))
    rec.commit(layout)
    return rec.result()


def _row(
    target: str,
    kind: str,
    source: str | None,
    derivation: str | None,
    note: str,
    citation: Citation | None = None,
) -> TableRow:
    return TableRow(target, kind, source, derivation, citation, note)


PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row("Artifact.sha256", "derivation", "wav:bytes", "sha256_of_bytes", "hash of the file bytes"),
    _row("Artifact.size", "derivation", "wav:bytes", "byte_length", "length of the file in bytes"),
    _row(
        "Artifact.audio_format.container",
        "derivation",
        "wav:riff",
        "wav_container",
        "RIFF/WAVE header",
    ),
    _row(
        "Artifact.audio_format.codec",
        "derivation",
        "wav:fmt/format_tag, wav:fmt/bits_per_sample",
        "wav_pcm_codec",
        "format tag 1 with 16 bits is little-endian signed 16-bit PCM; nothing else is mapped",
    ),
    _row(
        "Artifact.audio_format.sample_rate_hz",
        "source",
        "wav:fmt/sample_rate_hz",
        None,
        "read from the header; the Pipecat default is not assumed",
    ),
    _row("Artifact.audio_format.channels", "source", "wav:fmt/channels", None, "header"),
    _row(
        "Artifact.audio_format.bits_per_sample",
        "source",
        "wav:fmt/bits_per_sample",
        None,
        "header",
    ),
    _row(
        "Artifact.turn_ref",
        "absent",
        None,
        None,
        "the recording names no turn; the caller may link it",
    ),
    _row("ChannelMap.channels.*.index", "constant", None, "constant:0", "first channel (left)"),
    _row("ChannelMap.channels.*.index", "constant", None, "constant:1", "second channel (right)"),
    _row(
        "ChannelMap.channels.*.role",
        "derivation",
        "wav:fmt/channels",
        "documented_stereo_left_user",
        "stereo: user audio on the left",
        AUDIO_BUFFER_CITATION,
    ),
    _row(
        "ChannelMap.channels.*.role",
        "derivation",
        "wav:fmt/channels",
        "documented_stereo_right_bot",
        "stereo: bot audio on the right",
        AUDIO_BUFFER_CITATION,
    ),
    _row(
        "ChannelMap.channels.*.role",
        "derivation",
        "wav:fmt/channels",
        "documented_mono_mixed",
        "mono: user and bot audio are mixed",
        AUDIO_BUFFER_CITATION,
    ),
    _row(
        "ChannelMap.determination",
        "constant",
        None,
        "constant:declared",
        "the layout is the producer's documented one, not our analysis",
    ),
    _row(
        "ChannelMap.method",
        "constant",
        None,
        "constant:pipecat_audio_buffer_processor_stereo",
        "names the declaration (stereo)",
    ),
    _row(
        "ChannelMap.method",
        "constant",
        None,
        "constant:pipecat_audio_buffer_processor_mono",
        "names the declaration (mono)",
    ),
    _row(
        "ChannelMap.confidence",
        "absent",
        None,
        None,
        "a declaration is not a probability",
    ),
)
