# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-10, the provider half: per-channel speech-to-text with the declared role (contract
docs/phase4/api-surface-batch-b.md section 8; ADR 0024 amendments A5 and A6, invariant IA3).

The STT input of a simulated run is the agent channel of the stereo recording, cut at the
agent-activity segments (``tracer.chunk_plan/2``); every Turn takes ``role`` from the declared
channel map (``SttContext.role``), never inferred; the caller channel is not transcribed by
default; recorded mono input keeps ``tracer.chunk_plan/1`` and role ``unknown`` exactly; a real
request on an 8 kHz chunk is refused before the send until an owner-gated check (BL-293).
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import math
import sys
import wave
from array import array
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_support import activity_mod  # type: ignore[import-not-found,unused-ignore]
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    Rig,
    context,
    json_response,
    multipart_parts,
    ok_body,
    pcm,
    small_plan,
    stt,
)
from tracer_evalcore.audio.chunker import chunk_pcm
from tracer_evalschema.artifact import ChannelMap
from tracer_evalschema.ids import ArtifactId

RATE = 16000
FRAME = RATE * 20 // 1000  # 320 samples
CALLER_VALUE = 12345  # a value the agent channel never holds


def _samples(pattern: str) -> list[int]:
    out: list[int] = []
    for char in pattern:
        out += [7 if char == "D" else 0] * FRAME
    return out


def _stereo(caller: list[int], agent: list[int]) -> bytes:
    inter: list[int] = []
    for a, b in zip(caller, agent, strict=True):
        inter += [a, b]
    data = array("h", inter)
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def _declared(*roles: str) -> Any:
    return ChannelMap.model_validate(
        {
            "artifact_id": ArtifactId.new(),
            "channels": [{"index": i, "role": r} for i, r in enumerate(roles)],
            "determination": "declared",
            "method": "tracer_evalsim_stereo/1",
        }
    )


AGENT = "D" * 25 + "." * 10 + "D" * 20  # two segments with a wait of 100 ms (5 frames)


def _recording(roles: tuple[str, str]) -> tuple[bytes, list[int], list[int]]:
    agent = _samples(AGENT)
    caller = [CALLER_VALUE] * len(agent)
    channels = {"agent": agent, "caller": caller}
    first, second = channels[roles[0]], channels[roles[1]]
    return _stereo(first, second), agent, caller


def _plan(roles: tuple[str, str] = ("caller", "agent")) -> tuple[Any, Any, list[int], list[int]]:
    act = activity_mod()
    cmap = _declared(*roles)
    chosen = act.select_stt_channel(cmap)
    pcm, agent, caller = _recording(roles)
    plan = act.activity_chunk_plan(
        pcm,
        sample_rate_hz=RATE,
        channels=2,
        channel=chosen.index,
        wait_for_agent_silence_ms=100,
    )
    return plan, chosen, agent, caller


def _ctx(role: str) -> Any:
    return dataclasses.replace(context("mr"), role=role)


def _wav_samples(wav: bytes) -> list[int]:
    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (1, 2, RATE)
        data = array("h")
        data.frombytes(handle.readframes(handle.getnframes()))
        if sys.byteorder == "big":
            data.byteswap()
        return list(data)


def _replies() -> list[Any]:
    return [
        json_response(ok_body("first reply", request_id="s-0", phrases=[("a", 0.1, 0.3)])),
        json_response(ok_body("second reply", request_id="s-1", phrases=[("b", 0.0, 0.2)])),
    ]


# ------------------------------------------------------------------------- the named tests


@pytest.mark.parametrize("roles", [("caller", "agent"), ("agent", "caller")])
def test_stt_agent_channel_turns_have_role_agent_from_declared_map(
    tmp_path: Path, roles: tuple[str, str]
) -> None:
    """A5 / IA3: the Turns of the agent channel have ``role`` ``agent`` because the declared map
    says that channel is the agent's (in either position), ``source`` ``our_stt``, times on the
    recording timeline (the chunk's start plus the phrase time), and the transcript carries the
    ``tracer.chunk_plan/2`` document."""
    plan, chosen, _agent, _caller = _plan(roles)
    assert (chosen.index, chosen.role) == (roles.index("agent"), "agent")
    assert len(plan.chunks) == 2
    rig = Rig(tmp_path, _replies())
    outcome = rig.engine().transcribe(plan, _ctx(chosen.role))
    assert outcome.failure is None
    turns = outcome.turns
    assert [t.role for t in turns] == ["agent", "agent"]
    assert {t.source for t in turns} == {"our_stt"}
    assert [t.seq for t in turns] == [0, 1]
    assert [t.text for t in turns] == ["first reply", "second reply"]
    for turn, chunk, (start, end) in zip(turns, plan.chunks, [(0.1, 0.3), (0.0, 0.2)], strict=True):
        base = Fraction(chunk.start_frame, RATE)
        assert turn.start_ms == math.floor((base + Fraction(str(start))) * 1000)
        assert turn.end_ms == math.floor((base + Fraction(str(end))) * 1000)
        assert turn.timing_source == "audio_alignment"
    assert plan.chunks[1].start_frame == 35 * FRAME, (
        "the second segment is on the recording timeline"
    )
    transcript = outcome.transcript
    assert transcript is not None
    assert transcript.source == "our_stt"
    assert transcript.metadata["tracer.chunk_plan"] == plan.as_metadata()
    assert transcript.metadata["tracer.chunk_plan"]["format"] == "tracer.chunk_plan/2"
    assert transcript.metadata["tracer.chunk_plan"]["channel"] == roles.index("agent")
    # one record per chunk, indexes as in the plan
    assert [r.metadata["tracer.chunk_index"] for r in outcome.usage] == [0, 1]
    assert {r.stage for r in outcome.usage} == {"stt"}


def test_stt_agent_channel_turns_have_role_agent_from_declared_map_the_role_is_checked() -> None:
    """``SttContext.role`` is one of the three transcript roles and defaults to ``unknown`` (the
    Phase 3 value); a Turn never gets a role from anywhere else."""
    base = context("mr")
    assert base.role == "unknown"
    for role in ("caller", "agent", "unknown"):
        assert dataclasses.replace(base, role=role).role == role
    for bad in ("robot", "", "mixed", None, 5):
        with pytest.raises(ValueError, match="role"):
            dataclasses.replace(base, role=bad)
    fields = [f.name for f in dataclasses.fields(stt().SttContext)]
    assert fields[:5] == [
        "run_id",
        "audio_artifact_id",
        "created_at",
        "privacy",
        "configured_language",
    ]
    assert fields[5:] == ["role"]


def test_stt_never_runs_on_caller_channel_by_default(tmp_path: Path) -> None:
    """A5 / IA3: with the declared map and no opt-in, every request carries the agent channel's
    samples and none of the caller channel's; and the default choice is never the caller's."""
    for roles in [("caller", "agent"), ("agent", "caller")]:
        plan, chosen, agent, _caller = _plan(roles)
        assert roles[chosen.index] == "agent"
        rig = Rig(tmp_path / "".join(roles), _replies())
        rig.engine().transcribe(plan, _ctx(chosen.role))
        sent = [
            _wav_samples(next(p for p in multipart_parts(r) if p.name == "file").data)
            for r in rig.transport.requests
        ]
        assert len(sent) == 2
        for chunk, samples in zip(plan.chunks, sent, strict=True):
            assert samples == agent[chunk.start_frame : chunk.end_frame]
            assert CALLER_VALUE not in samples
        assert sum(len(s) for s in sent) == 45 * FRAME, "only the agent's audio was sent"
    caller_explicit = activity_mod().select_stt_channel(_declared("caller", "agent"), role="caller")
    assert caller_explicit.index == 0, "the caller channel needs the explicit argument"


def test_activity_plan_is_what_the_engine_sends_and_nothing_is_added(tmp_path: Path) -> None:
    """Each request carries exactly its chunk's WAV; the call log names the chunk indexes of the
    plan."""
    plan, chosen, _agent, _caller = _plan()
    rig = Rig(tmp_path, _replies())
    rig.engine().transcribe(plan, _ctx(chosen.role))
    for request, chunk in zip(rig.transport.requests, plan.chunks, strict=True):
        assert request.content.count(chunk.wav) == 1
    assert [x["chunk"] for x in rig.log_lines if x["phase"] == "before"] == [0, 1]


def test_phase3_mono_mapping_unchanged(tmp_path: Path) -> None:
    """IA3: recorded mono input keeps ADR 0024 rules 7 and 12 exactly: ``tracer.chunk_plan/1``,
    role ``unknown``, times offset by the chunk start; and the new fields are neutral for it
    (a plan has no channel and no wait value, a context has role ``unknown``, and a plan is still
    built from its six Phase 3 fields)."""
    plan = small_plan()
    ctx = context("mr")
    assert ctx.role == "unknown"
    assert plan.channel is None
    assert plan.wait_for_agent_silence_ms is None
    document = plan.as_metadata()
    assert document["format"] == "tracer.chunk_plan/1"
    assert set(document) == {
        "format",
        "max_chunk_ms",
        "search_ms",
        "sample_rate_hz",
        "channels",
        "total_frames",
        "chunks",
    }
    names = [f.name for f in dataclasses.fields(type(plan))]
    assert names[:6] == [
        "sample_rate_hz",
        "channels",
        "max_chunk_ms",
        "search_ms",
        "total_frames",
        "chunks",
    ]
    rebuilt = type(plan)(
        sample_rate_hz=plan.sample_rate_hz,
        channels=plan.channels,
        max_chunk_ms=plan.max_chunk_ms,
        search_ms=plan.search_ms,
        total_frames=plan.total_frames,
        chunks=plan.chunks,
    )
    assert rebuilt.as_metadata() == document
    rig = Rig(
        tmp_path,
        [
            json_response(ok_body(f"t{i}", request_id=f"r{i}", phrases=[("p", 0.0, 0.5)]))
            for i in range(3)
        ],
    )
    outcome = rig.engine().transcribe(plan, ctx)
    assert outcome.failure is None
    assert [t.role for t in outcome.turns] == ["unknown"] * 3
    assert {t.source for t in outcome.turns} == {"our_stt"}
    assert outcome.transcript is not None
    assert outcome.transcript.metadata["tracer.chunk_plan"] == document
    for turn, chunk in zip(outcome.turns, plan.chunks, strict=True):
        assert turn.start_ms == math.floor(Fraction(chunk.start_frame, chunk.sample_rate_hz) * 1000)
        assert turn.end_ms == math.floor(
            (Fraction(chunk.start_frame, chunk.sample_rate_hz) + Fraction(1, 2)) * 1000
        )


# ------------------------------------------------------------------------------ 8 kHz


def test_a_real_request_on_an_8khz_chunk_is_refused_before_the_send(tmp_path: Path) -> None:
    """A6: a ``twilio-media-stream/1`` call is 8,000 Hz; whether Sarvam STT accepts it is not
    verified (BL-293), so until the owner-gated check the engine refuses a simulated call's plan
    at 8,000 Hz before the first request: nothing sent, nothing logged, no usage; the chunk is the
    decoded 16-bit PCM, never resampled."""
    act = activity_mod()
    agent = [7] * (8 * 160) + [0] * (20 * 160)
    pcm = _stereo([0] * len(agent), agent)
    plan = act.activity_chunk_plan(
        pcm, sample_rate_hz=8000, channels=2, channel=1, wait_for_agent_silence_ms=100
    )
    assert plan.sample_rate_hz == 8000
    assert len(plan.chunks) == 1
    rig = Rig(tmp_path, [json_response(ok_body())])
    sink: list[Any] = []
    with pytest.raises(stt().SttConfigurationError) as raised:
        rig.engine().transcribe(plan, _ctx("agent"), on_usage=sink.append)
    assert raised.value.reason == "sample_rate_not_accepted"
    assert rig.transport.requests == []
    assert rig.log_lines == []
    assert sink == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_a_phase_3_mono_plan_at_8khz_is_still_sent_the_refusal_is_the_simulated_path_only(
    tmp_path: Path,
) -> None:
    """P4-10 acceptance: "Phase 3 mono behaviour unchanged". The existing engine tests send a
    mono plan at 8,000 Hz through a mock transport; the 8 kHz refusal (A6) is about a
    ``tracer.chunk_plan/2`` plan of a simulated call and leaves a recorded mono plan alone."""
    plan = chunk_pcm(pcm(8000), sample_rate_hz=8000, channels=1)
    assert plan.channel is None
    assert len(plan.chunks) == 1
    rig = Rig(tmp_path, [json_response(ok_body())])
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert len(rig.transport.requests) == 1


@pytest.mark.parametrize("rate", [16000, 22050, 24000, 44100, 48000])
def test_other_rates_are_still_sent_at_their_own_rate(tmp_path: Path, rate: int) -> None:
    """Only 8,000 Hz is refused; other rates are sent at their own rate, never resampled."""
    act = activity_mod()
    size = rate * 20 // 1000
    agent = [7] * (4 * size)
    plan = act.activity_chunk_plan(
        _stereo([0] * len(agent), agent),
        sample_rate_hz=rate,
        channels=2,
        channel=1,
        wait_for_agent_silence_ms=100,
    )
    rig = Rig(tmp_path, [json_response(ok_body())])
    outcome = rig.engine().transcribe(plan, _ctx("agent"))
    assert outcome.failure is None
    part = next(p for p in multipart_parts(rig.transport.requests[0]) if p.name == "file")
    with wave.open(io.BytesIO(part.data), "rb") as handle:
        assert handle.getframerate() == rate, "never resampled"


def test_a_plan_without_agent_activity_sends_nothing(tmp_path: Path) -> None:
    """No agent audio, no request, no usage."""
    act = activity_mod()
    quiet = [0] * (10 * FRAME)
    plan = act.activity_chunk_plan(
        _stereo([CALLER_VALUE] * len(quiet), quiet),
        sample_rate_hz=RATE,
        channels=2,
        channel=1,
        wait_for_agent_silence_ms=100,
    )
    assert plan.chunks == ()
    rig = Rig(tmp_path, [])
    sink: list[Any] = []
    outcome = rig.engine().transcribe(plan, _ctx("agent"), on_usage=sink.append)
    assert outcome.usage == ()
    assert sink == []
    assert rig.transport.requests == []


def test_the_context_role_changes_nothing_but_the_role_of_the_turns(tmp_path: Path) -> None:
    """The role is the only difference between a recorded and a simulated mapping."""
    plan = small_plan()
    base = Rig(
        tmp_path / "a", [json_response(ok_body(f"t{i}", request_id=f"r{i}")) for i in range(3)]
    )
    agent = Rig(
        tmp_path / "b", [json_response(ok_body(f"t{i}", request_id=f"r{i}")) for i in range(3)]
    )
    unknown = base.engine().transcribe(plan, _ctx("unknown"))
    as_agent = agent.engine().transcribe(plan, _ctx("agent"))
    assert [t.role for t in unknown.turns] == ["unknown"] * 3
    assert [t.role for t in as_agent.turns] == ["agent"] * 3
    for left, right in zip(unknown.turns, as_agent.turns, strict=True):
        assert (left.text, left.start_ms, left.end_ms, left.language, left.source) == (
            right.text,
            right.start_ms,
            right.end_ms,
            right.language,
            right.source,
        )
    assert "role" in inspect.signature(stt().SttContext).parameters
