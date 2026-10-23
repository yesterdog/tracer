# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Artifacts, audio formats and channel maps (ADR 0004 Decision 5, ADR 0007 Decisions 6 to 8,
ADR 0012 Decisions 4 and 5, ADR 0016 Decision 4; P1-10a).

- ``AudioFormat{container, codec, sample_rate_hz, channels, bits_per_sample?}``: an artifact is
  audio if and only if it has one. ``bits_per_sample`` is optional (N27): a lossy codec has no bit
  depth, so a producer omits it rather than inventing one.
- ``Artifact`` (``art``, a record of fact): ``uri`` (``url_rules.check_artifact_uri``), ``sha256``,
  ``size``, ``audio_format?``, ``turn_ref?`` and a required ``privacy`` marker. It hides its input
  in validation errors (ADR 0007 Decision 8 (a)).
- ``ChannelMap{artifact_id, channels, determination, method, confidence?}`` with
  ``ChannelAssignment{index, role}``: which channel of a recording carries whom. ``declared`` maps
  (the producer's documented layout) carry no confidence, ``inferred`` maps need one; at most one
  ``agent`` channel; channel indexes are unique; at least one channel.
- ``agent_audio_available(channel_maps)``: true iff some map identifies exactly one agent channel.
- ``check_channel_map(channel_map, artifact)``: the rules that need the artifact.
- ``is_loopback_uri(uri)``: the loopback definition of ADR 0007 Decision 6d (api-surface 22.12
  D1). Loopback hosts are valid on a record; P1-14 rejects them in shared and benchmark exports.

Checks that need several records raise ``ValueError`` naming the IDs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, Strict, model_validator

from tracer_evalschema._constraints import ClosedSet, FullMatch
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import EntityBase
from tracer_evalschema.ids import ArtifactId, TurnId
from tracer_evalschema.ijson import MAX_SAFE_INTEGER
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.text import MachineKey
from tracer_evalschema.url_rules import (
    ARTIFACT_URI_PATTERN,
    UrlRule,
    check_artifact_uri,
    is_loopback_host_uri,
)

__all__ = [
    "CHANNEL_ROLES",
    "DETERMINATIONS",
    "Artifact",
    "AudioFormat",
    "ChannelAssignment",
    "ChannelMap",
    "Confidence",
    "agent_audio_available",
    "check_channel_map",
    "is_loopback_uri",
]

CHANNEL_ROLES: Final = ("caller", "agent", "unknown", "mixed")
DETERMINATIONS: Final = ("declared", "inferred")

PositiveInt = Annotated[int, Strict(), Field(gt=0, le=MAX_SAFE_INTEGER)]
Index = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]

Confidence = Annotated[float, Strict(), Field(ge=0, le=1, allow_inf_nan=False)]
"""A probability reported by a producer or our analysis: a finite number in [0, 1]."""

Sha256Hex = Annotated[
    str, FullMatch("^[0-9a-f]{64}$", "sha256_hex", "{value} is not 64 lower-case hex digits")
]


class AudioFormat(BaseModel):
    """The format of an audio artifact (ADR 0012 Decision 4). ``container`` and ``codec`` are open
    values; the numbers are strict positive integers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    container: MachineKey
    codec: MachineKey
    sample_rate_hz: PositiveInt
    channels: PositiveInt
    bits_per_sample: PositiveInt | None = None


ArtifactUri = Annotated[str, UrlRule(check_artifact_uri, ARTIFACT_URI_PATTERN)]
"""``Artifact.uri``: ADR 0007 Decisions 6 to 6d (``url_rules.check_artifact_uri``)."""


class Artifact(HidingModel, EntityBase):
    """A stored file of a run, usually a recording (prefix ``art``; ADR 0012 Decision 4).

    A record of fact. ``privacy`` is required on every artifact (ADR 0016 I3). The model hides its
    input in validation errors, and no message quotes a rejected value (ADR 0007 Decision 8).
    """

    id_prefix: ClassVar[str] = "art"

    uri: ArtifactUri
    sha256: Sha256Hex
    size: Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]
    audio_format: AudioFormat | None = None
    turn_ref: TurnId | None = None
    privacy: PrivacyMarker


def is_loopback_uri(uri: str) -> bool:
    """True iff ``uri`` is an ``http``/``https`` URI with a loopback host (ADR 0007 Decision 6d).

    Loopback: ``localhost`` and names ending in ``.localhost``; every host the WHATWG URL Standard
    IPv4 parser maps into 127.0.0.0/8 (``127.1``, ``0x7f.0.0.1``, ``2130706433``, ...); ``::1``
    and the IPv4-mapped addresses in ``::ffff:127.0.0.0/104`` in any spelling. Compared after ASCII
    case-folding and the removal of one trailing dot, as written and percent-decoded once. Not
    loopback: ``0.0.0.0``, ``[::]``, private and link-local ranges, other names, and the
    authority of ``s3``, ``gs`` and ``file`` URIs.
    """
    return is_loopback_host_uri(uri)


# ------------------------------------------------------------------------------ channel maps


class ChannelAssignment(BaseModel):
    """One channel of a recording and whom it carries (P-31: ``mixed`` is a mono channel that
    carries the caller and the agent together)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: Index
    role: Annotated[str, ClosedSet(CHANNEL_ROLES)]


def _channel_map_conditions(schema: dict[str, Any]) -> None:
    """The determination rule as JSON Schema ``if``/``then`` (ADR 0012 I8, I9)."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"determination": {"enum": ["inferred"]}},
                "required": ["determination"],
            },
            "then": {"required": ["confidence"], "properties": {"confidence": {"type": "number"}}},
            "else": {"properties": {"confidence": {"type": "null"}}},
        }
    ]


class ChannelMap(BaseModel):
    """Which channel of an artifact carries whom (ADR 0012 Decision 5).

    ``declared``: the producer's documented layout (for example Pipecat's stereo buffer, left the
    caller and right the agent); ``method`` names the declaration and there is no confidence (a
    declaration is not a probability). ``inferred``: our analysis; ``method`` names it and
    ``confidence`` is required. At most one channel is the agent (a second AI agent is a separate
    run), indexes are unique and the order is kept.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_extra=_channel_map_conditions
    )

    artifact_id: ArtifactId
    channels: Annotated[list[ChannelAssignment], Field(min_length=1)]
    determination: Annotated[str, ClosedSet(DETERMINATIONS)]
    method: MachineKey
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _rules(self) -> ChannelMap:
        problems: list[Problem] = []
        if self.determination == "declared" and self.confidence is not None:
            problems.append(
                Problem(
                    ("confidence",),
                    "channel_map_confidence",
                    "a declared channel map carries no confidence (a declaration is not a "
                    "probability)",
                )
            )
        if self.determination == "inferred" and self.confidence is None:
            problems.append(
                Problem(
                    ("confidence",),
                    "channel_map_confidence",
                    "an inferred channel map needs a confidence in [0, 1]",
                )
            )
        if sum(1 for channel in self.channels if channel.role == "agent") > 1:
            problems.append(
                Problem(
                    ("channels",),
                    "channel_map_agent",
                    "at most one channel carries the agent (a second AI agent is a separate run)",
                )
            )
        indexes = [channel.index for channel in self.channels]
        if len(set(indexes)) != len(indexes):
            problems.append(
                Problem(
                    ("channels",),
                    "channel_map_index",
                    "a channel index appears twice (one channel has one role)",
                )
            )
        raise_problems(type(self).__name__, problems)
        return self


def agent_audio_available(channel_maps: Sequence[ChannelMap]) -> bool:
    """True iff some map identifies exactly one ``agent`` channel (ADR 0004 Decision 5).

    No map, ``unknown`` channels only, or ``mixed`` channels only (the agent is on the channel but
    not separable) do not identify the agent.
    """
    return any(
        sum(1 for channel in channel_map.channels if channel.role == "agent") == 1
        for channel_map in channel_maps
    )


def check_channel_map(channel_map: ChannelMap, artifact: Artifact) -> None:
    """The rules of a channel map that need its artifact (ADR 0012 I11). ``ValueError`` when the
    map names another artifact, the artifact is not audio (no ``audio_format``), or a channel
    index is not below the artifact's channel count."""
    if channel_map.artifact_id != artifact.id:
        raise ValueError(
            f"the channel map names artifact {channel_map.artifact_id}, not artifact {artifact.id}"
        )
    if artifact.audio_format is None:
        raise ValueError(
            f"artifact {artifact.id} has no audio_format: a channel map belongs to an audio "
            "artifact"
        )
    count = artifact.audio_format.channels
    for channel in channel_map.channels:
        if channel.index >= count:
            raise ValueError(
                f"channel index {channel.index} is not below the {count} channel(s) of artifact "
                f"{artifact.id}"
            )
