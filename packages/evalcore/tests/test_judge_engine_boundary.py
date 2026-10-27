# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J11: the ``JudgeEngine`` protocol and the ``Judge*`` errors (ADR 0030 rules 16 and 18;
ADR 0026 rule 4; contract docs/phase4/api-surface-batch-j1.md section 6).

The same shape as the language-model boundary of batch 0b: facts only, raised outside any handler,
no cause and no context; usage handed out before the next request. Nothing here opens a socket.
"""

from __future__ import annotations

import copy
import inspect
import pickle
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    FakeJudge,
    agrees_json,
    build_judged,
    default_params,
    golden_set,
    judge_request,
    mod,
    usage_record,
)
from tracer_evalschema.ids import RunId

FACTS: dict[str, Any] = {
    "index": 3,
    "status": 429,
    "kind": "read_timeout",
    "code": "rate_limited",
    "request_id": "req-0042.a:b_c",
    "attempts": 4,
    "reason": "retry_after_too_long",
}
CONFIGURATION = {"JudgeConfigurationError": True, "JudgeKeyMissingError": True}
NAMES = (
    "JudgeAuthError",
    "JudgeCapReachedError",
    "JudgeConfigurationError",
    "JudgeKeyMissingError",
    "JudgeRateLimited",
    "JudgeRequestRejected",
    "JudgeResponseInvalid",
    "JudgeServerError",
    "JudgeTransportError",
)


def chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo:
        item = todo.pop()
        if item is None or any(item is s for s in seen):
            continue
        seen.append(item)
        todo += [item.__cause__, item.__context__]
    return seen


def test_judge_errors_hold_facts_only_and_no_chain() -> None:
    """The family of ADR 0026 rule 4 for the judge: every fact is a validated keyword-only
    attribute, a provider message cannot be passed, a refusal never quotes its value, and the
    error carries neither a cause nor a context (a library error can quote the request)."""
    judge = mod("judge")
    evalcore_errors = mod("errors")
    stt = mod("stt")
    llm = mod("llm")
    assert judge.__all__ == sorted(judge.__all__), "the list stays sorted"
    base = judge.JudgeError
    assert issubclass(base, evalcore_errors.EvalcoreError)
    assert not issubclass(base, ValueError), "only the configuration errors are ValueErrors"
    assert not issubclass(base, stt.SttError)
    assert not issubclass(base, llm.LlmError)
    classes = {name: getattr(judge, name) for name in NAMES}
    for name in (*NAMES, "JudgeError"):
        assert name in judge.__all__, name
    assert issubclass(judge.JudgeKeyMissingError, judge.JudgeConfigurationError)
    for name, cls in classes.items():
        assert issubclass(cls, base), name
        assert not issubclass(cls, (stt.SttError, llm.LlmError)), f"{name} is in another family"
        assert issubclass(cls, ValueError) is CONFIGURATION.get(name, False), name

        error = cls(**FACTS)
        for key, value in FACTS.items():
            assert getattr(error, key) == value, (name, key)
        bare = cls()
        assert [getattr(bare, key) for key in FACTS] == [None] * len(FACTS), name
        for attempt in ({"message": CANARY}, {"body": CANARY}, {"cause": CANARY}):
            with pytest.raises(TypeError):
                cls(**attempt)
        with pytest.raises(TypeError):
            cls("a provider message")
        text = str(error)
        assert text.startswith(name)
        assert text.isascii()
        assert len(text) < 300
        for fact in (
            "429",
            "read_timeout",
            "rate_limited",
            "req-0042.a:b_c",
            "retry_after_too_long",
        ):
            assert fact in text, (name, fact)
        assert error.args == (text,)
        assert repr(error) == f"{name}({text!r})"
        assert set(vars(error)) == set(FACTS), f"{name} keeps something besides the facts"

        refused: list[dict[str, Any]] = [
            {"index": -1},
            {"index": True},
            {"status": 99},
            {"status": 600},
            {"attempts": 0},
            {"kind": CANARY},
            {"code": CANARY},
            {"reason": CANARY},
            {"code": "has space"},
            {"code": "line\nbreak"},
            {"request_id": "a b"},
            {"request_id": "x" * 129},
            {"request_id": CANARY + "!"},
        ]
        for bad in refused:
            with pytest.raises(ValueError, match=r".") as caught:
                cls(**bad)
            shown = str(caught.value) + repr(caught.value)
            assert CANARY not in shown, (name, bad)
            assert "line\nbreak" not in shown, (name, bad)
            assert chain(caught.value) == [caught.value]

        # raised outside the handler that saw the transport error: no cause, no context
        try:
            raise OSError(CANARY)
        except OSError:
            built = cls(kind="read_error", reason="transport_failed")
        assert built.__cause__ is None
        assert built.__context__ is None
        with pytest.raises(cls) as again:
            raise built
        assert chain(again.value) == [again.value]
        assert CANARY not in "".join(str(c) + repr(c) for c in chain(again.value))

        revived = pickle.loads(pickle.dumps(error))  # noqa: S301
        for clone in (copy.copy(error), copy.deepcopy(error), revived):
            assert type(clone) is cls
            assert str(clone) == text
            assert {key: getattr(clone, key) for key in FACTS} == FACTS

    # the cap error names the cap by a machine reason (ADR 0030 rule 17)
    for reason in ("judge_requests", "judge_tokens"):
        assert judge.JudgeCapReachedError(index=0, reason=reason).reason == reason

    # the records have no defaults: the request, the parameters and the context are explicit
    for cls in (judge.JudgeRequest, judge.JudgeParams, judge.JudgeContext):
        parameters = inspect.signature(cls).parameters
        assert all(p.default is inspect.Parameter.empty for p in parameters.values()), cls
    assert list(inspect.signature(judge.JudgeParams).parameters) == [
        "model",
        "max_tokens",
        "temperature",
        "seed",
        "reasoning_effort",
        "provider_slug",
        "extra_headers",
    ]
    params = default_params()
    with pytest.raises(FrozenInstanceError):
        params.model = "other"
    for bad_params in (
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_tokens": 2**53},
        {"model": ""},
        {"model": "a b"},
        {"temperature": True},
        {"temperature": -0.1},
        {"temperature": 2.5},
        {"temperature": "0"},
        {"seed": -1},
        {"seed": True},
        {"reasoning_effort": "High"},
        {"provider_slug": "Bad Slug"},
        {"provider_slug": ""},
        {"extra_headers": (("x", "y"),)},
        {"extra_headers": (("anthropic-workspace-id", "a\nb"),)},
        {"extra_headers": "x"},
    ):
        with pytest.raises(ValueError, match=r"."):
            default_params(**bad_params)
    assert default_params(temperature=0).temperature == 0
    assert default_params(temperature=1.0).temperature == 1.0
    assert default_params(seed=0, reasoning_effort="low", provider_slug="slug-1.a").seed == 0
    request = judge_request()
    with pytest.raises(FrozenInstanceError):
        request.index = 9
    for bad_request in (
        {"index": -1},
        {"index": True},
        {"attempt": 0},
        {"attempt": 3},
        {"match_mode": "rating"},
        {"fact_count": -1},
        {"item_id": ""},
        {"messages": ()},
        {"messages": ("text",)},
        {"params": {"model": "x"}},
    ):
        with pytest.raises((ValueError, TypeError)):
            judge_request(**bad_request)
    # a mode without facts has a fact count of zero and the other way round
    with pytest.raises(ValueError, match=r"."):
        judge_request(match_mode="equivalent_answer", fact_count=2)
    with pytest.raises(ValueError, match=r"."):
        judge_request(match_mode="all_facts", fact_count=0)


def _outcome(judge: Any, text: str | None, usage: tuple[Any, ...], failure: Any = None) -> Any:
    return judge.JudgeOutcome(
        text=text,
        finish_reason=None if text is None else "stop",
        reported_model=None,
        request_id=None,
        exchange_sha256=None,
        params_sent=None,
        usage=usage,
        failure=failure,
    )


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    def sink(self, record: Any) -> None:
        ref = record.provider_call_ref
        assert ref is not None
        self.events.append(("usage", int(ref.value.split("-")[-1])))


class _Plain:
    """Satisfies JudgeEngine only: no flag, no keyword."""

    def __init__(self, log: _Log, boom_at: int | None = None) -> None:
        self.log, self.boom_at = log, boom_at

    def complete(self, request: Any, context: Any) -> Any:
        self.log.events.append(("request", request.index))
        if self.boom_at == request.index:
            raise KeyboardInterrupt
        record = usage_record(context.run_id, f"req-{request.index}")
        return _outcome(mod("judge"), agrees_json(golden_set().items[0]), (record,))


class _Streaming(_Plain):
    """Satisfies UsageStreamingJudgeEngine: hands each record out before it returns."""

    streams_usage = True

    def complete(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
        outcome = super().complete(request, context)
        if on_usage is not None:
            for record in outcome.usage:
                on_usage(record)
        return outcome


class _FlagOff(_Plain):
    """Has the keyword but the flag is False: the driver must not pass ``on_usage``."""

    streams_usage = False

    def complete(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
        assert on_usage is None, "the driver passed a sink to an engine that does not stream"
        return super().complete(request, context)


def test_judge_engine_streams_usage_before_next_request() -> None:
    """Rule 18: the usage of request k reaches the sink before request k+1 is sent, for a plain
    engine (the driver hands it out) and for a streaming engine (the engine does, exactly once);
    a failure or an interrupt keeps what was already handed out."""
    judge = mod("judge")
    stt = mod("stt")
    assert judge.UsageSink is stt.UsageSink, "one UsageSink type for the four engines"
    context = judge.JudgeContext(run_id=RunId.new(), created_at=_now())
    parameter = inspect.signature(judge.UsageStreamingJudgeEngine.complete).parameters["on_usage"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
    assert list(inspect.signature(judge.JudgeEngine.complete).parameters) == [
        "self",
        "request",
        "context",
    ]
    log = _Log()
    assert isinstance(_Plain(log), judge.JudgeEngine)
    assert not isinstance(_Plain(log), judge.UsageStreamingJudgeEngine)
    assert isinstance(_Streaming(log), judge.JudgeEngine)
    assert isinstance(_Streaming(log), judge.UsageStreamingJudgeEngine)

    expected = [(kind, i) for i in range(3) for kind in ("request", "usage")]
    for engine_type in (_Plain, _Streaming, _FlagOff):
        run_log = _Log()
        engine = engine_type(run_log)
        for index in range(3):
            outcome = judge.judge_once(engine, judge_request(index), context, on_usage=run_log.sink)
            assert outcome.failure is None
            assert outcome.text
        assert run_log.events == expected, engine_type.__name__

    # a sink that raises stops the run before the next request
    run_log = _Log()

    def angry(record: Any) -> None:
        run_log.sink(record)
        raise RuntimeError("sink")

    with pytest.raises(RuntimeError):
        judge.judge_once(_Plain(run_log), judge_request(0), context, on_usage=angry)
    assert run_log.events == [("request", 0), ("usage", 0)]
    # a BaseException from the engine propagates after the earlier usage was handed out
    run_log = _Log()
    engine = _Plain(run_log, boom_at=2)

    def three_requests() -> None:
        for index in range(3):
            judge.judge_once(engine, judge_request(index), context, on_usage=run_log.sink)

    with pytest.raises(KeyboardInterrupt):
        three_requests()
    assert run_log.events == [
        ("request", 0),
        ("usage", 0),
        ("request", 1),
        ("usage", 1),
        ("request", 2),
    ]

    # the outcome holds a text or a failure, never both and never neither
    failure = judge.JudgeTransportError(kind="read_error", attempts=4)
    with pytest.raises(ValueError, match=r"."):
        _outcome(judge, None, ())
    with pytest.raises(ValueError, match=r"."):
        _outcome(judge, "text", (), failure)
    wrong = usage_record(context.run_id, "req-0")
    llm_like = wrong.model_copy(update={"stage": "sim_caller_llm"})
    with pytest.raises(ValueError, match=r"."):
        _outcome(judge, "text", (llm_like,))
    with pytest.raises(TypeError):
        _outcome(judge, "text", ("not a record",))
    assert _outcome(judge, None, (), failure).failure is failure
    assert _outcome(judge, "", ()).text == "", "an empty reply is a reply: the validator refuses it"

    # across the driver: the first attempt's usage is out before the regeneration is sent
    call = build_judged(lines=1)
    item = call.golden.items[0]
    log = _Log()
    events: list[tuple[str, int]] = log.events

    class Watching(FakeJudge):  # type: ignore[misc]
        def complete(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
            events.append(("request", request.index))
            return super().complete(request, context, on_usage=on_usage)

    engine2 = Watching(["prose", agrees_json(item)], run_id=call.run_id, streams_usage=True)
    judged = judge.judge_item(
        engine=engine2,
        item=item,
        window=mod("judgewindow").answer_window(
            scripted_turn_index=0,
            transcripts=call.bundle.transcripts,
            turns=call.bundle.turns,
            call_end_ms=call.call_end_ms,
        ),
        params=default_params(),
        context=judge.JudgeContext(run_id=call.run_id, created_at=context.created_at),
        first_index=0,
        on_usage=log.sink,
    )
    assert judged.state == "verdict"
    assert judged.attempts == 2
    assert events[:4] == [("request", 0), ("usage", 0), ("request", 1), ("usage", 1)]
    assert len(judged.usage) == 2


def _now() -> Any:
    from evalcore_p4e_support import CREATED_AT  # type: ignore[import-not-found,unused-ignore]

    return CREATED_AT
