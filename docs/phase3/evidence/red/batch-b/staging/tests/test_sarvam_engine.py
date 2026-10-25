# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-07 (all or nothing, sequential calls) and the SttEngine boundary (contract
docs/phase3/api-surface-batch-b.md sections 2 and 5.5; ADR 0024 rules 1, 7 and 11).

The engine is driven through an ``httpx.MockTransport``; nothing opens a socket.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    UTC_NOW,
    Rig,
    ScriptedTransport,
    context,
    engine_mod,
    error_body,
    json_response,
    multipart_parts,
    ok_body,
    privacy,
    small_plan,
    stt,
)
from tracer_evalcore.audio.chunker import chunk_pcm
from tracer_evalschema.transcript import check_transcript


def _ok(i: int, text: str = "t") -> httpx.Response:
    return json_response(ok_body(text, request_id=f"req-{i}"))  # type: ignore[no-any-return,unused-ignore]


def _bad(status: int) -> httpx.Response:
    return json_response(error_body(), status=status)  # type: ignore[no-any-return,unused-ignore]


def test_sarvam_engine_is_an_stt_engine(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [])
    engine = rig.engine()
    assert isinstance(engine, stt().SttEngine)
    assert engine.provider == "sarvam"
    assert engine.model == "saaras:v4"
    custom = Rig(tmp_path / "x", [], model="saaras:v3").engine()
    assert custom.model == "saaras:v3"


def test_sarvam_all_chunks_succeed_one_request_each_in_order(tmp_path: Path) -> None:
    plan = small_plan()
    rig = Rig(tmp_path, [_ok(i, f"text {i}") for i in range(3)])
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert [t.text for t in outcome.turns] == ["text 0", "text 1", "text 2"]
    assert len(outcome.usage) == 3
    assert outcome.transcript is not None
    check_transcript(outcome.transcript, outcome.turns)
    assert outcome.transcript.model == "saaras:v4"
    # sequential: one request in flight, in chunk order, each carrying its own chunk file
    assert rig.transport.max_active == 1
    sent = [
        next(p.data for p in multipart_parts(r) if p.name == "file") for r in rig.transport.requests
    ]
    assert sent == [c.wav for c in plan.chunks]
    assert rig.sleeps.waits == []


def test_sarvam_sends_the_audio_at_its_native_rate_without_resampling(tmp_path: Path) -> None:
    plan = small_plan(rate=24000)
    rig = Rig(tmp_path, [_ok(i) for i in range(3)])
    rig.engine().transcribe(plan, context("mr"))
    total = 0
    for request, chunk in zip(rig.transport.requests, plan.chunks, strict=True):
        data = next(p.data for p in multipart_parts(request) if p.name == "file")
        with wave.open(io.BytesIO(data), "rb") as reader:
            assert reader.getframerate() == 24000
            assert reader.getnchannels() == 1
            assert reader.getsampwidth() == 2
            assert reader.getnframes() == chunk.frames
            total += reader.getnframes()
    assert total == plan.total_frames


def test_sarvam_stereo_chunks_are_sent_as_they_are(tmp_path: Path) -> None:
    from evalproviders_support import pcm

    plan = chunk_pcm(
        pcm(48000),  # 24000 frames of 2 channels
        sample_rate_hz=24000,
        channels=2,
        max_chunk_ms=1000,
        search_ms=200,
        accept_provider_downmix=True,
    )
    rig = Rig(tmp_path, [_ok(i) for i in range(len(plan.chunks))])
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    for request, chunk in zip(rig.transport.requests, plan.chunks, strict=True):
        data = next(p.data for p in multipart_parts(request) if p.name == "file")
        assert data == chunk.wav, "Tracer computes no downmix; the provider merges the channels"


def test_sarvam_zero_chunks_send_nothing(tmp_path: Path) -> None:
    plan = chunk_pcm(b"", sample_rate_hz=24000, channels=1)
    assert plan.chunks == ()
    rig = Rig(tmp_path, [])
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert outcome.turns == ()
    assert outcome.usage == ()
    assert rig.transport.requests == []
    assert rig.log_text == ""


FAILURES: list[tuple[str, list[Any], type[Exception] | str, int]] = [
    ("auth 403", [lambda: _bad(403)], "SttAuthError", 1),
    ("rejected 400", [lambda: _bad(400)], "SttRequestRejected", 1),
    ("429 four times", [lambda: _bad(429)] * 4, "SttRateLimited", 4),
    ("500 four times", [lambda: _bad(500)] * 4, "SttServerError", 4),
    ("timeout four times", [lambda: httpx.ReadTimeout("x")] * 4, "SttTransportError", 4),
    ("invalid body", [lambda: json_response(b"<html>")], "SttResponseInvalid", 1),
    (
        "redirect",
        [lambda: httpx.Response(302, headers={"location": "https://x.example"})],
        "SttResponseInvalid",
        1,
    ),
    (
        "retries then rejection",
        [lambda: _bad(503), lambda: _bad(503), lambda: _bad(400)],
        "SttRequestRejected",
        3,
    ),
]


@pytest.mark.parametrize("failing_chunk", [0, 1, 2])
@pytest.mark.parametrize(
    ("label", "makers", "class_name", "sent"), FAILURES, ids=[f[0] for f in FAILURES]
)
def test_sarvam_partial_chunk_failure_yields_no_transcript(
    tmp_path: Path,
    failing_chunk: int,
    label: str,
    makers: list[Any],
    class_name: str,
    sent: int,
) -> None:
    plan = small_plan()
    steps = [_ok(i, f"text {i}") for i in range(failing_chunk)] + [m() for m in makers]
    rig = Rig(tmp_path, steps)
    outcome = rig.engine().transcribe(plan, context("mr"))
    expected = getattr(stt(), class_name)
    assert type(outcome.failure) is expected, label
    assert outcome.failure.chunk_index == failing_chunk
    # all or nothing: no Transcript, no Turn, no Segment is built from the chunks that succeeded
    assert outcome.transcript is None
    assert outcome.turns == ()
    assert outcome.warnings == ()
    # the usage that happened is kept, one record per successful chunk, none for the failure
    assert len(outcome.usage) == failing_chunk
    assert [u.provider_call_ref.value for u in outcome.usage] == [
        f"req-{i}" for i in range(failing_chunk)
    ]
    assert [u.metadata["tracer.chunk_index"] for u in outcome.usage] == list(range(failing_chunk))
    # the run stops: nothing is sent for the chunks after the failed one
    assert len(rig.transport.requests) == failing_chunk + sent
    assert {x["chunk"] for x in rig.log_lines} == set(range(failing_chunk + 1))


def test_sarvam_partial_chunk_failure_a_retried_chunk_counts_once(tmp_path: Path) -> None:
    plan = small_plan()
    steps = [
        _ok(0),
        _bad(429),
        json_response(ok_body("again", request_id="req-1-retry")),
        _bad(403),
    ]
    rig = Rig(tmp_path, steps)
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert type(outcome.failure) is stt().SttAuthError
    assert outcome.failure.chunk_index == 2
    assert [u.provider_call_ref.value for u in outcome.usage] == ["req-0", "req-1-retry"], (
        "a failed attempt makes no UsageRecord (billing unverified)"
    )
    assert len(rig.transport.requests) == 4


def test_sarvam_engine_outcome_records_are_complete_when_nothing_fails(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [_ok(i) for i in range(3)])
    ctx = context("mr")
    outcome = rig.engine().transcribe(small_plan(), ctx)
    assert outcome.transcript.privacy == ctx.privacy
    assert all(u.run_id == ctx.run_id for u in outcome.usage)
    assert all(u.created_at == UTC_NOW for u in outcome.usage)


def test_stt_outcome_holds_a_transcript_or_a_failure_exactly_one() -> None:
    s = stt()
    failure = s.SttAuthError(chunk_index=0, status=403, code="x", request_id="r", attempts=1)
    ok = s.SttOutcome(transcript=None, turns=(), usage=(), warnings=(), failure=failure)
    assert ok.failure is failure
    with pytest.raises(ValueError, match=r"\S"):
        s.SttOutcome(transcript=None, turns=(), usage=(), warnings=(), failure=None)
    from evalproviders_support import hand_plan, mapping_mod, models

    plan = hand_plan([(0, 24000)])
    response = models().ChunkResponse(
        chunk_index=0,
        response=models().parse_response(json.dumps(ok_body()).encode()),
        responded_at=None,
        attempts=1,
    )
    good = mapping_mod().map_responses(plan, [response], model="saaras:v4", context=context())
    with pytest.raises(ValueError, match=r"\S"):
        s.SttOutcome(
            transcript=good.transcript,
            turns=good.turns,
            usage=good.usage,
            warnings=(),
            failure=failure,
        )
    with pytest.raises(ValueError, match=r"\S"):
        s.SttOutcome(transcript=None, turns=good.turns, usage=(), warnings=(), failure=failure)
    with pytest.raises(AttributeError):
        good.failure = failure


def test_stt_context_is_a_frozen_value() -> None:
    ctx = context("mr")
    assert ctx.configured_language == "mr"
    assert ctx.created_at == UTC_NOW
    assert ctx.privacy == privacy()
    with pytest.raises(AttributeError):
        ctx.configured_language = "hi"


def test_stt_error_facts_are_keyword_only_and_survive_pickling() -> None:
    import pickle

    s = stt()
    error = s.SttServerError(
        chunk_index=2, status=503, code="server_error", request_id="r", attempts=4
    )
    again = pickle.loads(pickle.dumps(error))  # noqa: S301 - our own object, in a test
    assert type(again) is s.SttServerError
    assert (again.chunk_index, again.status, again.code, again.request_id, again.attempts) == (
        2,
        503,
        "server_error",
        "r",
        4,
    )
    assert str(again) == str(error)


def test_create_sarvam_stt_builds_an_engine_that_uses_the_injected_pieces(tmp_path: Path) -> None:
    transport = ScriptedTransport([_ok(i) for i in range(3)])
    waits: list[float] = []
    from evalproviders_support import CANARY_KEY, WallClock

    engine = engine_mod().create_sarvam_stt(
        out_dir=tmp_path / "run1",
        environ={"SARVAM_API_KEY": CANARY_KEY},
        transport=transport,
        sleep=waits.append,
        clock=WallClock(),
    )
    outcome = engine.transcribe(small_plan(), context("mr"))
    assert outcome.failure is None
    assert {r.headers["api-subscription-key"] for r in transport.requests} == {CANARY_KEY}
    assert (tmp_path / "run1" / "sarvam_call_log.jsonl").is_file()
    import os

    assert "SARVAM_API_KEY" not in os.environ, "the key is not exported into the environment"
    assert isinstance(engine, stt().SttEngine)


def test_sarvam_a_long_call_is_sent_chunk_by_chunk_with_one_request_in_flight(
    tmp_path: Path,
) -> None:
    from evalproviders_support import pcm  # type: ignore[import-not-found,unused-ignore]

    # 40 s at 8 kHz in windows of 100 ms: 400 chunks, a call far longer than the base case
    plan = chunk_pcm(
        pcm(8000 * 40, seed=11),
        sample_rate_hz=8000,
        channels=1,
        max_chunk_ms=100,
        search_ms=40,
    )
    assert len(plan.chunks) >= 400
    rig = Rig(tmp_path, [_ok(i, f"t{i}") for i in range(len(plan.chunks))])
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert outcome.transcript is not None
    assert len(outcome.turns) == len(outcome.usage) == len(plan.chunks)
    assert rig.transport.max_active == 1
    assert len(rig.transport.requests) == len(plan.chunks)
    assert len(rig.log_lines) == 2 * len(plan.chunks)
    assert [t.seq for t in outcome.turns] == list(range(len(plan.chunks)))
    check_transcript(outcome.transcript, outcome.turns)


def test_sarvam_the_model_as_requested_is_what_is_sent_and_recorded(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [_ok(i) for i in range(3)], model="saaras:v3")
    outcome = rig.engine().transcribe(small_plan(), context("mr"))
    assert outcome.failure is None
    assert outcome.transcript is not None
    assert outcome.transcript.model == "saaras:v3"
    assert {u.model for u in outcome.usage} == {"saaras:v3"}
    for request in rig.transport.requests:
        fields = {p.name: p.data for p in multipart_parts(request)}
        assert fields["model"] == b"saaras:v3"
    failed = Rig(tmp_path / "f", [_bad(403)], model="saaras:v3")
    again = failed.engine().transcribe(small_plan(), context("mr"))
    assert again.usage == ()
    assert again.failure is not None


def test_sarvam_client_is_a_context_manager_that_closes_its_http_client(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [_ok(0)])
    with rig.client as client:
        assert client is rig.client
        client.send_chunk(small_plan().chunks[0])
    with pytest.raises(RuntimeError):
        rig.client.send_chunk(small_plan().chunks[0])
