# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch I, finding R1 of the P3-28 re-audit, at the Sarvam engine: usage as the send goes on.

Contract: docs/phase3/api-surface-batch-i.md section 1.2. ``SarvamStt.transcribe`` takes an
optional keyword-only ``on_usage`` and the class says ``streams_usage = True``. Each chunk that is
answered hands its ``UsageRecord`` to ``on_usage`` at once, in chunk order, before the next chunk
is sent; the records handed out are the records of the outcome (same ids). An exception of the
transport, a ``KeyboardInterrupt`` or an ``OSError`` of the call log leaves the records of the
chunks answered so far with the caller. Nothing opens a socket: the client sits on a scripted
``httpx.MockTransport``.
"""

from __future__ import annotations

import errno
import inspect
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    Rig,
    calllog_mod,
    context,
    engine_mod,
    error_body,
    json_response,
    ok_body,
    small_plan,
    stt,
)
from tracer_evalcore.audio.chunker import chunk_pcm


def _ok(i: int) -> httpx.Response:
    return json_response(ok_body(f"t{i}", request_id=f"req-{i}"))  # type: ignore[no-any-return,unused-ignore]


def _bad(status: int) -> httpx.Response:
    return json_response(error_body(), status=status)  # type: ignore[no-any-return,unused-ignore]


class Sink:
    """What an observer would keep: the records, in the order they were handed out."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def __call__(self, record: Any) -> None:
        self.records.append(record)


# ============================================================ the shape


def test_the_engine_declares_that_it_streams_usage_and_takes_on_usage_by_keyword() -> None:
    cls = engine_mod().SarvamStt
    assert cls.streams_usage is True
    parameter = inspect.signature(cls.transcribe).parameters["on_usage"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
    assert list(inspect.signature(cls.transcribe).parameters)[:3] == ["self", "plan", "context"]


def test_the_engine_is_still_an_stt_engine(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [])
    assert isinstance(rig.engine(), stt().SttEngine)


def test_the_streaming_protocol_of_evalcore_accepts_the_engine(tmp_path: Path) -> None:
    protocol = stt().UsageStreamingSttEngine
    rig = Rig(tmp_path, [])
    assert isinstance(rig.engine(), protocol)


# ============================================================ the order


def test_each_record_is_handed_out_before_the_next_chunk_is_sent(tmp_path: Path) -> None:
    sink = Sink()
    seen: list[tuple[int, int]] = []

    def step(i: int) -> Any:
        def respond(request: httpx.Request) -> httpx.Response:
            seen.append((i, len(sink.records)))
            return _ok(i)

        return respond

    rig = Rig(tmp_path, [step(0), step(1), step(2)])
    rig.engine().transcribe(small_plan(), context("mr"), on_usage=sink)
    assert seen == [(0, 0), (1, 1), (2, 2)]
    assert len(sink.records) == 3


def test_the_records_are_one_per_chunk_in_chunk_order_and_are_the_outcome_records(
    tmp_path: Path,
) -> None:
    sink = Sink()
    ctx = context("mr")
    plan = small_plan()
    rig = Rig(tmp_path, [_ok(0), _ok(1), _ok(2)])
    outcome = rig.engine().transcribe(plan, ctx, on_usage=sink)
    assert outcome.failure is None
    assert [r.metadata["tracer.chunk_index"] for r in sink.records] == [0, 1, 2]
    assert [r.provider_call_ref.value for r in sink.records] == ["req-0", "req-1", "req-2"]
    assert {r.run_id for r in sink.records} == {ctx.run_id}
    assert {(r.provider, r.model, r.stage, r.source) for r in sink.records} == {
        ("sarvam", "saaras:v4", "stt", "measured")
    }
    for record, chunk in zip(sink.records, plan.chunks, strict=True):
        counts = {q.unit: q.count for q in record.quantities}
        assert counts["requests"] == 1
        assert Decimal(str(counts["audio_input_seconds"])) == Decimal(chunk.frames) / Decimal(
            chunk.sample_rate_hz
        )
    # one source of truth: the outcome holds the very records that were handed out
    assert list(outcome.usage) == sink.records
    assert [r.id for r in outcome.usage] == [r.id for r in sink.records]


def test_without_on_usage_the_outcome_is_what_it_was(tmp_path: Path) -> None:
    rig_a = Rig(tmp_path / "a", [_ok(0), _ok(1), _ok(2)])
    plain = rig_a.engine().transcribe(small_plan(), context("mr"))
    rig_b = Rig(tmp_path / "b", [_ok(0), _ok(1), _ok(2)])
    explicit = rig_b.engine().transcribe(small_plan(), context("mr"), on_usage=None)
    assert plain.failure is None
    assert explicit.failure is None
    assert len(plain.usage) == len(explicit.usage) == 3


def test_a_retried_chunk_is_handed_out_once(tmp_path: Path) -> None:
    sink = Sink()
    rig = Rig(tmp_path, [_ok(0), _bad(503), _ok(1), _ok(2)])
    outcome = rig.engine().transcribe(small_plan(), context("mr"), on_usage=sink)
    assert outcome.failure is None
    assert [r.metadata["tracer.chunk_index"] for r in sink.records] == [0, 1, 2]
    assert len(rig.transport.requests) == 4


def test_a_plan_without_chunks_hands_nothing_out(tmp_path: Path) -> None:
    sink = Sink()
    plan = chunk_pcm(b"", sample_rate_hz=24000, channels=1)
    rig = Rig(tmp_path, [])
    outcome = rig.engine().transcribe(plan, context("mr"), on_usage=sink)
    assert outcome.usage == ()
    assert sink.records == []
    assert rig.transport.requests == []


def test_a_language_the_engine_refuses_hands_nothing_out_and_sends_nothing(tmp_path: Path) -> None:
    sink = Sink()
    rig = Rig(tmp_path, [_ok(0)])
    with pytest.raises(stt().SttLanguageNotSupportedError):
        rig.engine().transcribe(small_plan(), context("hi"), on_usage=sink)
    assert sink.records == []
    assert rig.transport.requests == []


# ============================================================ the failures


def test_a_chunk_that_fails_with_an_stt_error_leaves_the_records_before_it(tmp_path: Path) -> None:
    sink = Sink()
    rig = Rig(tmp_path, [_ok(0), _bad(403)])
    outcome = rig.engine().transcribe(small_plan(), context("mr"), on_usage=sink)
    assert type(outcome.failure) is stt().SttAuthError
    assert [r.metadata["tracer.chunk_index"] for r in sink.records] == [0]
    assert list(outcome.usage) == sink.records
    assert [r.id for r in outcome.usage] == [r.id for r in sink.records]


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), ValueError("boom"), KeyboardInterrupt(), OSError(errno.EIO, "boom")],
    ids=["RuntimeError", "ValueError", "KeyboardInterrupt", "OSError"],
)
def test_an_exception_of_the_transport_on_chunk_2_leaves_the_record_of_chunk_1(
    tmp_path: Path, exc: BaseException
) -> None:
    sink = Sink()
    rig = Rig(tmp_path, [_ok(0), exc])
    with pytest.raises(type(exc)) as raised:
        rig.engine().transcribe(small_plan(), context("mr"), on_usage=sink)
    assert raised.value is exc, "the exception is not wrapped or replaced"
    assert [r.provider_call_ref.value for r in sink.records] == ["req-0"]
    assert len(rig.transport.requests) == 2, "no answered chunk is sent again"
    assert [x["phase"] for x in rig.log_lines] == ["before", "after", "before"]


def test_an_oserror_of_the_call_log_before_chunk_2_leaves_the_record_of_chunk_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calllog = calllog_mod()
    real = calllog.CallLog._write
    seen = {"n": 0}

    def write(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen["n"] += 1
        if seen["n"] == 3:
            raise OSError(errno.ENOSPC, "no space")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(calllog.CallLog, "_write", write)
    sink = Sink()
    rig = Rig(tmp_path, [_ok(0), _ok(1)])
    with pytest.raises(OSError, match="no space"):
        rig.engine().transcribe(small_plan(), context("mr"), on_usage=sink)
    assert [r.provider_call_ref.value for r in sink.records] == ["req-0"]
    assert len(rig.transport.requests) == 1, "the 'before' line is written before the send"


def test_an_observer_that_raises_stops_the_send_and_is_not_hidden(tmp_path: Path) -> None:
    # An observer that cannot keep a record must not let the engine go on paying for chunks
    # nobody accounts for: its exception leaves transcribe unchanged and no further chunk is sent.
    class Broken(Exception):
        pass

    def observer(record: Any) -> None:
        raise Broken

    rig = Rig(tmp_path, [_ok(0), _ok(1), _ok(2)])
    with pytest.raises(Broken):
        rig.engine().transcribe(small_plan(), context("mr"), on_usage=observer)
    assert len(rig.transport.requests) == 1
