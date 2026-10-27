# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-12, usage and cost of a simulated call (contract docs/phase4/api-surface-batch-b.md
sections 4 and 5; ADR 0024 amendments A1 and A7, invariant IA4; ADR 0014).

Each answered request makes one UsageRecord with the stage of its engine (``sim_caller_tts``,
``sim_caller_llm``, ``stt``); a failed attempt, a refusal and a cache hit make none; each record is
handed out before the next request (the R1 lesson). Cost is ``not_available`` without a user USD
catalog, never zero, and no currency is converted. The prices below are made up for the tests.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_phase4_matrix import status  # type: ignore[import-not-found,unused-ignore]
from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MR_LINE,
    SECOND_MR_LINE,
    UTC_NOW,
    LlmRig,
    SimRun,
    TtsRig,
    chat_reply,
    core_llm,
    core_tts,
    cost_mod,
    llm_context,
    llm_request,
    tts_context,
    tts_reply,
    tts_request,
    tts_voice,
)
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    catalog_text,
    context,
    entry,
    json_response,
    ok_body,
    small_plan,
)
from tracer_evalschema.cost import check_cost_summary


class Sink:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def __call__(self, record: Any) -> None:
        self.records.append(record)


def _counts(record: Any) -> dict[str, Any]:
    return {q.unit: q.count for q in record.quantities}


# ------------------------------------------------------------------- the named usage test


def test_sim_usage_records_per_request_with_stages(tmp_path: Path) -> None:
    """A7: one record per answered request, with the stage of its engine and its own quantities;
    the retried attempt, the failed request and the refused request make none; the records are
    the ones the outcome holds and the ones handed to ``on_usage``."""
    sink = Sink()
    run = SimRun(
        tmp_path,
        tts=[
            status(503),
            tts_reply("tts-a"),
            tts_reply("tts-b"),
            status(403),
        ],
        llm=[chat_reply(reply_id="chat-a", prompt=40, completion=9), status(400)],
        stt=[json_response(ok_body(f"t{i}", request_id=f"stt-{i}")) for i in range(3)],
    )
    one = "\u0915\u094d\u0937\U0001f600e\u0301"  # 6 code points
    first = run.tts.synthesise(tts_request(0, one), tts_context(), on_usage=sink)
    other_voice = tts_voice(model="bulbul:v2")
    second = run.tts.synthesise(
        tts_request(1, MR_LINE + MR_LINE, voice=other_voice), tts_context(), on_usage=sink
    )
    failed_tts = run.tts.synthesise(tts_request(2, MR_LINE), tts_context(), on_usage=sink)
    answered_llm = run.llm.complete(
        llm_request(0, model="sarvam-105b"), llm_context(), on_usage=sink
    )
    failed_llm = run.llm.complete(llm_request(1), llm_context(), on_usage=sink)
    stt = run.stt.transcribe(small_plan(), context("mr"), on_usage=sink)
    assert failed_tts.failure is not None
    assert failed_llm.failure is not None
    assert stt.failure is None
    assert first.failure is None
    assert second.failure is None
    assert answered_llm.failure is None
    records = sink.records
    assert [r.stage for r in records] == [
        "sim_caller_tts",
        "sim_caller_tts",
        "sim_caller_llm",
        "stt",
        "stt",
        "stt",
    ]
    assert {r.domain for r in records} == {"evaluation"}
    assert {r.provider for r in records} == {"sarvam"}
    assert len({r.id for r in records}) == 6
    assert (
        list(first.usage) + list(second.usage) + list(answered_llm.usage) + list(stt.usage)
        == records
    )
    assert failed_tts.usage == ()
    assert failed_llm.usage == ()
    tts_a, tts_b, llm_a, *stt_records = records
    # TTS: characters are the code points of the text sent, a count we made; measured
    assert (tts_a.source, tts_b.source) == ("measured", "measured")
    assert _counts(tts_a) == {"input_characters": 6, "requests": 1}
    assert _counts(tts_b) == {"input_characters": 2 * len(MR_LINE), "requests": 1}
    assert (tts_a.model, tts_a.provider_call_ref.value) == ("bulbul:v3", "tts-a")
    assert tts_b.model == "bulbul:v2", "the model of the request, not a constant"
    assert (tts_b.provider_call_ref.system, tts_b.provider_call_ref.kind) == (
        "sarvam",
        "request_id",
    )
    assert tts_a.occurred_at == datetime(2026, 10, 23, 13, 8, 26, tzinfo=UTC)
    assert tts_a.raw is None
    assert tts_b.raw is None
    assert tts_a.metadata["tracer.request_index"] == 0
    assert tts_b.metadata["tracer.request_index"] == 1
    assert tts_a.metadata["tracer.model_basis"] == "requested_not_echoed"
    # LLM: provider reported
    assert (llm_a.source, llm_a.model) == ("provider_reported", "sarvam-105b")
    assert _counts(llm_a) == {"input_tokens": 40, "output_tokens": 9, "requests": 1}
    # STT: as in Phase 3
    assert {r.source for r in stt_records} == {"measured"}
    assert [r.provider_call_ref.value for r in stt_records] == ["stt-0", "stt-1", "stt-2"]
    for record in records:
        assert record.model_dump_json().count(CANARY_KEY) == 0
        assert MR_LINE not in record.model_dump_json(), "no text in a usage record"
        assert SECOND_MR_LINE not in record.model_dump_json()


def test_sim_usage_records_per_request_with_stages_a_request_that_failed_after_four_attempts(
    tmp_path: Path,
) -> None:
    """The billing of a failed attempt is unverified, so it makes no record (ADR 0024 rule 9 and
    Ambiguity 5); the call log keeps every attempt."""
    sink = Sink()
    rig = TtsRig(tmp_path, [status(503)] * 4)
    outcome = rig.call(tts_request(0), on_usage=sink)
    assert type(outcome.failure) is core_tts().TtsServerError
    assert outcome.usage == ()
    assert sink.records == []
    assert len(rig.log_lines) == 8


def test_sim_usage_records_per_request_with_stages_are_stamped_with_the_context(
    tmp_path: Path,
) -> None:
    """The records carry the context's run id and creation time (the engine reads no clock for
    them)."""
    ctx = tts_context()
    rig = TtsRig(tmp_path, [tts_reply()])
    (record,) = rig.call(context=ctx).usage
    assert record.run_id == ctx.run_id
    assert record.created_at == ctx.created_at == UTC_NOW


def test_sim_usage_has_no_occurred_at_without_a_date_header(tmp_path: Path) -> None:
    """ADR 0014 Decision 8: ``occurred_at`` is the reply's Date or absent, never the local clock."""
    for number, date in enumerate([None, "yesterday"]):
        rig = TtsRig(tmp_path / f"d{number}", [tts_reply(date=date)])
        (record,) = rig.call().usage
        assert record.occurred_at is None


# ------------------------------------------------------ usage before the next request (R1)


def test_sarvam_tts_streams_usage_before_the_next_request(tmp_path: Path) -> None:
    """The R1 lesson (ADR 0024 rule 16 (d)) for TTS, through the core driver: when request k is
    sent, the records of the requests before it are already with the sink."""
    sink = Sink()
    seen: list[int] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(len(sink.records))
        return tts_reply(f"r{len(seen)}")  # type: ignore[no-any-return,unused-ignore]

    rig = TtsRig(tmp_path, [reply, reply, reply])
    outcomes = core_tts().synthesise_all(
        rig.engine,
        [tts_request(i, f"line {i}") for i in range(3)],
        tts_context(),
        on_usage=sink,
    )
    assert seen == [0, 1, 2]
    assert len(outcomes) == 3, "delivered once, by the engine"
    assert len(sink.records) == 3, "delivered once, by the engine"
    assert [o.usage[0].id for o in outcomes] == [r.id for r in sink.records]


def test_sarvam_tts_streams_usage_before_the_next_request_a_failure_ends_the_run(
    tmp_path: Path,
) -> None:
    """``synthesise_all`` stops after the first failed outcome; later requests are not sent."""
    sink = Sink()
    rig = TtsRig(tmp_path, [tts_reply("a"), status(403), tts_reply("never")])
    outcomes = core_tts().synthesise_all(
        rig.engine, [tts_request(i) for i in range(3)], tts_context(), on_usage=sink
    )
    assert len(outcomes) == 2
    assert outcomes[1].failure is not None
    assert len(rig.transport.requests) == 2, "the third request is not sent"
    assert len(sink.records) == 1


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt(), OSError(5, "io")])
def test_sarvam_tts_streams_usage_before_the_next_request_an_exception_keeps_the_usage(
    tmp_path: Path, exc: BaseException
) -> None:
    """A transport exception leaves the earlier usage with the sink and is raised unchanged."""
    sink = Sink()
    rig = TtsRig(tmp_path, [tts_reply("a"), exc])
    with pytest.raises(type(exc)) as raised:
        core_tts().synthesise_all(
            rig.engine, [tts_request(i) for i in range(3)], tts_context(), on_usage=sink
        )
    assert raised.value is exc, "the exception is not wrapped or replaced"
    assert [r.provider_call_ref.value for r in sink.records] == ["a"]
    assert len(rig.transport.requests) == 2, "no answered request is sent again"
    assert [x["event"] for x in rig.log_lines] == ["before", "after", "before"]


def test_sarvam_tts_streams_usage_before_the_next_request_a_sink_that_raises_stops_the_run(
    tmp_path: Path,
) -> None:
    """An observer that cannot keep a record stops the run before the next request."""

    class Broken(Exception):
        pass

    def observer(record: Any) -> None:
        raise Broken

    rig = TtsRig(tmp_path, [tts_reply("a"), tts_reply("b")])
    with pytest.raises(Broken):
        core_tts().synthesise_all(
            rig.engine, [tts_request(i) for i in range(2)], tts_context(), on_usage=observer
        )
    assert len(rig.transport.requests) == 1


def test_sarvam_llm_streams_usage_before_the_next_request(tmp_path: Path) -> None:
    """The R1 lesson for chat: each record reaches the sink before the next request is built."""
    sink = Sink()
    seen: list[int] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(len(sink.records))
        return chat_reply(reply_id=f"c{len(seen)}")  # type: ignore[no-any-return,unused-ignore]

    rig = LlmRig(tmp_path, [reply, reply, reply])
    results = [
        core_llm().complete_once(rig.engine, llm_request(i), llm_context(), on_usage=sink)
        for i in range(3)
    ]
    assert seen == [0, 1, 2]
    assert len(sink.records) == 3
    assert [o.usage[0].id for o in results] == [r.id for r in sink.records]


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_sarvam_llm_streams_usage_before_the_next_request_an_exception_keeps_the_usage(
    tmp_path: Path, exc: BaseException
) -> None:
    """A transport exception leaves the earlier usage with the sink and is raised unchanged."""
    sink = Sink()
    rig = LlmRig(tmp_path, [chat_reply(reply_id="a"), exc])
    core_llm().complete_once(rig.engine, llm_request(0), llm_context(), on_usage=sink)
    with pytest.raises(type(exc)) as raised:
        core_llm().complete_once(rig.engine, llm_request(1), llm_context(), on_usage=sink)
    assert raised.value is exc
    assert [r.provider_call_ref.value for r in sink.records] == ["a"]


def test_sarvam_llm_streams_usage_before_the_next_request_a_sink_that_raises_is_not_hidden(
    tmp_path: Path,
) -> None:
    """An observer's exception is not swallowed."""

    class Broken(Exception):
        pass

    def observer(record: Any) -> None:
        raise Broken

    rig = LlmRig(tmp_path, [chat_reply()])
    with pytest.raises(Broken):
        rig.call(on_usage=observer)


def test_sarvam_engines_without_on_usage_return_the_same_usage(tmp_path: Path) -> None:
    """Without a sink the outcome is the same; the outcome always holds its records."""
    rig = TtsRig(tmp_path, [tts_reply("a"), tts_reply("b")])
    plain = rig.call(tts_request(0))
    explicit = rig.call(tts_request(1), on_usage=None)
    assert len(plain.usage) == len(explicit.usage) == 1


# ----------------------------------------------------------------------------- cost


def _usages(tmp_path: Path) -> list[Any]:
    run = SimRun(
        tmp_path,
        tts=[tts_reply("t")],
        llm=[chat_reply(reply_id="c", prompt=1000, completion=500, cached=200)],
        stt=[json_response(ok_body("x", request_id="s")) for _ in range(3)],
    )
    sink = Sink()
    run.tts.synthesise(tts_request(0, "a" * 100), tts_context(), on_usage=sink)
    run.llm.complete(llm_request(0), llm_context(), on_usage=sink)
    run.stt.transcribe(small_plan(), context("mr"), on_usage=sink)
    return sink.records


def test_sim_cost_not_available_without_usd_catalog(tmp_path: Path) -> None:
    """A7, ADR 0024 rule 14: without a user USD catalog every cost is ``not_available``
    (``no_price_for_model``), the summary has no total, and nothing is ever an amount of 0."""
    use = _usages(tmp_path)
    assert {r.stage for r in use} == {"sim_caller_tts", "sim_caller_llm", "stt"}
    result = cost_mod().compute_usage_cost(use, catalog=None, created_at=UTC_NOW)
    assert len(result.records) == len(use) == 5
    for record, usage in zip(result.records, use, strict=True):
        assert record.domain == "evaluation"
        assert record.status == "not_available"
        assert record.na_reason == "no_price_for_model"
        assert record.usage_record_ids == [usage.id]
        assert record.amount is None
        assert record.basis is None
        assert record.pricing_snapshot is None
        assert record.created_at == UTC_NOW
    summary = result.summary
    assert summary.total is None
    assert summary.included == []
    assert summary.completeness == "none"
    assert [(e.ref, e.reason) for e in summary.excluded] == [
        (r.id, "no_price_for_model") for r in result.records
    ]
    check_cost_summary(summary, list(result.records))
    empty = cost_mod().compute_usage_cost([], catalog=None, created_at=UTC_NOW)
    assert empty.records[0].na_reason == "no_usage"
    assert empty.summary.total is None


def _cat(*entries: dict[str, Any]) -> Any:
    return cost_mod().load_user_catalog(catalog_text(list(entries)), created_at=UTC_NOW)


TTS_PRICES = [
    entry("input_characters", "100", "0.03", entry_key="tts-chars", model="bulbul:v3"),
    entry("requests", "1", "0", entry_key="tts-req", model="bulbul:v3"),
]
LLM_PRICES = [
    entry("input_tokens", "1000", "0.01", entry_key="llm-in", model="sarvam-105b"),
    entry("output_tokens", "1000", "0.04", entry_key="llm-out", model="sarvam-105b"),
    entry("cache_read_input_tokens", "1000", "0.005", entry_key="llm-cache", model="sarvam-105b"),
    entry("requests", "1", "0", entry_key="llm-req", model="sarvam-105b"),
]


def test_sim_cost_is_computed_only_from_a_user_usd_catalog(tmp_path: Path) -> None:
    """ADR 0024 rule 14: with a user USD catalog cost is computed by the evalschema functions
    (made-up prices)."""
    use = [r for r in _usages(tmp_path) if r.stage != "stt"]
    result = cost_mod().compute_usage_cost(
        use, catalog=_cat(*TTS_PRICES, *LLM_PRICES), created_at=UTC_NOW
    )
    tts, llm = result.records
    assert (tts.status, tts.basis) == ("available", "computed")
    assert (tts.amount.amount, tts.amount.currency) == ("0.03", "USD"), (
        "100 characters at 0.03 per 100"
    )
    # 800 uncached input tokens, 500 output, 200 cached: 0.008 + 0.02 + 0.001
    assert (llm.status, llm.amount.amount, llm.amount.currency) == ("available", "0.029", "USD")
    assert result.summary.total is not None
    assert result.summary.total.amount == "0.059"
    assert result.summary.completeness == "complete"
    check_cost_summary(result.summary, list(result.records))


def test_sim_cost_a_unit_the_catalog_does_not_price_is_unpriced_never_zero(
    tmp_path: Path,
) -> None:
    """A unit without a price makes the record ``unit_unpriced``, never an amount of 0."""
    use = [r for r in _usages(tmp_path) if r.stage != "stt"]
    no_requests = [
        TTS_PRICES[0],
        *[e for e in LLM_PRICES if e["unit"] != "cache_read_input_tokens"],
    ]
    result = cost_mod().compute_usage_cost(use, catalog=_cat(*no_requests), created_at=UTC_NOW)
    tts, llm = result.records
    assert tts.status == "not_available"
    assert tts.na_reason == "unit_unpriced"
    assert tts.amount is None
    assert llm.status == "not_available", "cached tokens unpriced"
    assert llm.na_reason == "unit_unpriced", "cached tokens unpriced"
    assert result.summary.total is None


def test_sim_cost_a_model_the_catalog_does_not_name_has_no_price(tmp_path: Path) -> None:
    """A model without an entry has no price."""
    use = [r for r in _usages(tmp_path) if r.stage == "sim_caller_tts"]
    other = [entry("input_characters", "100", "0.03", entry_key="x", model="other-model")]
    (record,) = cost_mod().compute_usage_cost(use, catalog=_cat(*other), created_at=UTC_NOW).records
    assert record.status == "not_available"
    assert record.amount is None


def test_sim_cost_inr_is_never_converted() -> None:
    """ADR 0024 rule 14 / A7: no entry point takes an exchange rate or a currency, and the
    function is the same one for STT, TTS and LLM usage (a user catalog is USD only)."""
    parameters = set(inspect.signature(cost_mod().compute_usage_cost).parameters)
    assert parameters == {"usages", "catalog", "created_at", "run_started_at"}
    assert not parameters & {"fx", "rate", "inr", "currency", "usd_per_inr", "exchange_rate"}
    stt_parameters = set(inspect.signature(cost_mod().compute_stt_cost).parameters)
    assert stt_parameters == parameters, "the Phase 3 function is unchanged"
    assert cost_mod().compute_stt_cost.__module__ == cost_mod().compute_usage_cost.__module__
