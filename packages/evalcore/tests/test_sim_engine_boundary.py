# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-03: the TTS, LLM and caller-brain boundaries of tracer-evalcore (contract
docs/phase4/api-surface-batch-0b.md section 5; ADR 0026 rule 4, ADR 0028 rule 1).

Nothing of the new modules is imported at module level: each test loads them lazily with
``importlib``, so before the modules exist every test fails on its own with
``ModuleNotFoundError`` and the directory still collects. All values are invented. The fake
engines build their own ``UsageRecord`` objects, open no socket and read no clock.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import json
import pickle
import re
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.ids import ExternalRef, RunId, UsageRecordId
from tracer_evalschema.usage import UsageQuantity, UsageRecord


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
ADR26 = ROOT / "docs" / "adr" / "0026-simulated-caller-architecture.md"
ADR27 = ROOT / "docs" / "adr" / "0027-simulated-run-records.md"

CREATED_AT = datetime(2026, 10, 27, 9, 0, 0, tzinfo=UTC)
#: A canary that must never be quoted by a refusal or appear in an error.
CANARY = "".join(["CANARY", "-", "zq7fc3d1", "-", "9ae2b8"])
FACTS: dict[str, Any] = {
    "index": 3,
    "status": 429,
    "kind": "read_timeout",
    "code": "rate_limited",
    "request_id": "req-0042.a:b_c",
    "attempts": 4,
    "reason": "retry_after_too_long",
}
BASES = {"tts": "Tts", "llm": "Llm"}
SUBCLASSES = {
    "tts": {
        "TtsAuthError": False,
        "TtsRequestRejected": False,
        "TtsRateLimited": False,
        "TtsServerError": False,
        "TtsTransportError": False,
        "TtsResponseInvalid": False,
        "TtsCapReachedError": False,
        "TtsConfigurationError": True,
        "TtsKeyMissingError": True,
        "TtsLanguageNotSupportedError": True,
        "TtsTextTooLongError": True,
    },
    "llm": {
        "LlmAuthError": False,
        "LlmRequestRejected": False,
        "LlmRateLimited": False,
        "LlmServerError": False,
        "LlmTransportError": False,
        "LlmResponseInvalid": False,
        "LlmCapReachedError": False,
        "LlmConfigurationError": True,
        "LlmKeyMissingError": True,
    },
}


def mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalcore.{name}")


def chain(exc: BaseException) -> list[BaseException]:
    """The exception and everything reachable through ``__cause__`` and ``__context__``."""
    seen: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo:
        item = todo.pop()
        if item is None or any(item is s for s in seen):
            continue
        seen.append(item)
        todo += [item.__cause__, item.__context__]
    return seen


def error_classes(module: str) -> dict[str, Any]:
    m = mod(module)
    base: Any = getattr(m, f"{BASES[module]}Error")
    out: dict[str, Any] = {f"{BASES[module]}Error": base}
    for name in SUBCLASSES[module]:
        out[name] = getattr(m, name)
    return out


def usage_record(stage: str, run_id: RunId, tag: str) -> UsageRecord:
    quantities = (
        [UsageQuantity(unit="input_characters", count=12), UsageQuantity(unit="requests", count=1)]
        if stage == "sim_caller_tts"
        else [
            UsageQuantity(unit="input_tokens", count=30),
            UsageQuantity(unit="output_tokens", count=7),
            UsageQuantity(unit="requests", count=1),
        ]
    )
    return UsageRecord(
        id=UsageRecordId.new(),
        schema_version=SCHEMA_VERSION,
        created_at=CREATED_AT,
        run_id=run_id,
        domain="evaluation",
        stage=stage,
        provider="fake",
        model="fake-model",
        provider_call_ref=ExternalRef(system="fake", kind="request_id", value=tag),
        quantities=quantities,
        source="measured" if stage == "sim_caller_tts" else "provider_reported",
    )


# ------------------------------------------------------------------------------- the errors


@pytest.mark.parametrize("module", ["tts", "llm"])
def test_tts_llm_errors_hold_facts_only_and_no_chain(module: str) -> None:
    m = mod(module)
    evalcore_errors = importlib.import_module("tracer_evalcore.errors")
    stt = mod("stt")
    classes = error_classes(module)
    other = error_classes("llm" if module == "tts" else "tts")
    base = classes[f"{BASES[module]}Error"]
    assert m.__all__ == sorted(m.__all__), "the list stays sorted"
    for name in classes:
        assert name in m.__all__, name
    assert issubclass(base, evalcore_errors.EvalcoreError)
    assert not issubclass(base, ValueError), "only the configuration errors are ValueErrors"
    assert not issubclass(base, stt.SttError)
    for name, cls in classes.items():
        if cls is not base:
            assert issubclass(cls, base), name
        assert not issubclass(cls, tuple(other.values())), f"{name} is in the other family"
        configuration = SUBCLASSES[module].get(name, False)
        assert issubclass(cls, ValueError) is configuration, name
    prefix = BASES[module]
    assert issubclass(
        getattr(m, f"{prefix}KeyMissingError"), getattr(m, f"{prefix}ConfigurationError")
    )

    for name, cls in classes.items():
        # every fact is a keyword-only attribute and nothing else is accepted
        error = cls(**FACTS)
        for key, value in FACTS.items():
            assert getattr(error, key) == value, (name, key)
        bare = cls()
        assert [getattr(bare, key) for key in FACTS] == [None] * len(FACTS), name
        with pytest.raises(TypeError):
            cls("a provider message")
        with pytest.raises(TypeError):
            cls(message=CANARY)
        with pytest.raises(TypeError):
            cls(body=CANARY)
        text = str(error)
        assert text.startswith(name), text
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
        assert error.args == (text,), "the message is the only argument"
        assert repr(error) == f"{name}({text!r})"
        assert set(vars(error)) == set(FACTS), f"{name} keeps something besides the facts"

        # a value that is not a machine fact is refused, without quoting it
        refused: list[dict[str, Any]] = [
            {"index": -1},
            {"index": True},
            {"index": "3"},
            {"status": 99},
            {"status": 600},
            {"status": True},
            {"status": "429"},
            {"attempts": 0},
            {"attempts": True},
            {"attempts": 1.0},
            {"kind": CANARY},
            {"code": CANARY},
            {"reason": CANARY},
            {"kind": "Upper"},
            {"code": "has space"},
            {"reason": "x" * 65},
            {"kind": ""},
            {"code": 1},
            {"reason": b"bytes"},
            {"code": "line\nbreak"},
            {"request_id": ""},
            {"request_id": "a b"},
            {"request_id": "x" * 129},
            {"request_id": "id\n"},
            {"request_id": CANARY + "!"},
        ]
        for bad in refused:
            with pytest.raises(ValueError, match=r".") as caught:
                cls(**bad)
            shown = str(caught.value) + repr(caught.value)
            assert CANARY not in shown, (name, bad)
            assert "line\nbreak" not in shown
            assert chain(caught.value) == [caught.value]
        edge = cls(kind="k" * 64, request_id="r" * 128, attempts=1, status=100, index=0)
        assert (edge.kind, edge.status, edge.index) == ("k" * 64, 100, 0)

        # raised outside the handler that saw the transport error: no cause, no context
        try:
            raise OSError(CANARY)
        except OSError:
            built = cls(kind="read_error", reason="transport_failed")
        assert built.__cause__ is None
        assert built.__context__ is None
        with pytest.raises(cls) as caught_again:
            raise built
        assert chain(caught_again.value) == [caught_again.value], name
        assert CANARY not in "".join(str(c) + repr(c) for c in chain(caught_again.value))

        # a copy and a pickle keep the type and the facts (the retry layers may rebuild them)
        revived = pickle.loads(pickle.dumps(error))  # noqa: S301
        for clone in (copy.copy(error), copy.deepcopy(error), revived):
            assert type(clone) is cls
            assert str(clone) == text
            assert {key: getattr(clone, key) for key in FACTS} == FACTS
            assert chain(clone) == [clone]


# ---------------------------------------------------------------------- usage before the next


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    def sink(self, record: UsageRecord) -> None:
        ref = record.provider_call_ref
        assert ref is not None
        self.events.append(("usage", int(ref.value.split("-")[-1])))


def _tts_pieces(count: int) -> tuple[Any, Any, list[Any]]:
    tts = mod("tts")
    context = tts.TtsContext(run_id=RunId.new(), created_at=CREATED_AT)
    voice = tts.TtsVoice(
        provider="fake", model="fake-tts", speaker="fake-voice", language_code="mr-IN", pace="1"
    )
    requests = [
        tts.TtsRequest(index=i, text=f"line {i}", voice=voice, sample_rate_hz=8000, codec="mulaw")
        for i in range(count)
    ]
    return tts, context, requests


class _PlainTts:
    """Satisfies TtsEngine only: no flag, no keyword."""

    def __init__(self, tts: Any, log: _Log, fail_at: int | None = None, boom_at: int | None = None):
        self.tts, self.log, self.fail_at, self.boom_at = tts, log, fail_at, boom_at

    def synthesise(self, request: Any, context: Any) -> Any:
        self.log.events.append(("request", request.index))
        if self.boom_at == request.index:
            raise KeyboardInterrupt
        record = usage_record("sim_caller_tts", context.run_id, f"req-{request.index}")
        if self.fail_at == request.index:
            failure = self.tts.TtsResponseInvalid(index=request.index, code="bad_shape")
            return self.tts.TtsOutcome(audio=None, usage=(record,), failure=failure)
        return self.tts.TtsOutcome(audio=b"\x00\x01", usage=(record,), failure=None)


class _StreamingTts(_PlainTts):
    """Satisfies UsageStreamingTtsEngine: hands each record out before it returns."""

    streams_usage = True

    def synthesise(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
        outcome = super().synthesise(request, context)
        if on_usage is not None:
            for record in outcome.usage:
                on_usage(record)
        return outcome


class _FlagOffTts(_PlainTts):
    """Has the keyword but the flag is False: the driver must not pass ``on_usage``."""

    streams_usage = False

    def synthesise(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
        assert on_usage is None, "the driver passed a sink to an engine that does not stream"
        return super().synthesise(request, context)


def test_engines_stream_usage_before_next_request() -> None:
    stt = mod("stt")
    for name in ("tts", "llm"):
        m = mod(name)
        assert "UsageSink" in m.__all__
        assert m.UsageSink is stt.UsageSink, "one UsageSink type for the three engines"
    tts, context, requests = _tts_pieces(3)

    # protocols: the plain engine is not a streaming one; the flag and the keyword are both needed
    log = _Log()
    assert isinstance(_PlainTts(tts, log), tts.TtsEngine)
    assert not isinstance(_PlainTts(tts, log), tts.UsageStreamingTtsEngine)
    assert isinstance(_StreamingTts(tts, log), tts.TtsEngine)
    assert isinstance(_StreamingTts(tts, log), tts.UsageStreamingTtsEngine)
    assert list(inspect.signature(tts.TtsEngine.synthesise).parameters) == [
        "self",
        "request",
        "context",
    ]
    parameter = inspect.signature(tts.UsageStreamingTtsEngine.synthesise).parameters["on_usage"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
    llm = mod("llm")
    assert list(inspect.signature(llm.LlmEngine.complete).parameters) == [
        "self",
        "request",
        "context",
    ]
    parameter = inspect.signature(llm.UsageStreamingLlmEngine.complete).parameters["on_usage"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None

    # the order: the usage of request k is handed out before request k+1 is sent, for a plain
    # engine (the driver delivers it) and for a streaming engine (the engine does, exactly once)
    expected = [(kind, i) for i in range(3) for kind in ("request", "usage")]
    for engine_type in (_PlainTts, _StreamingTts, _FlagOffTts):
        run_log = _Log()
        outcomes = tts.synthesise_all(
            engine_type(tts, run_log), requests, context, on_usage=run_log.sink
        )
        assert run_log.events == expected, engine_type.__name__
        assert [o.audio for o in outcomes] == [b"\x00\x01"] * 3
        assert all(o.failure is None for o in outcomes)

    # a failure ends the run after its own usage was handed out; later requests are not sent
    for engine_type in (_PlainTts, _StreamingTts):
        run_log = _Log()
        outcomes = tts.synthesise_all(
            engine_type(tts, run_log, fail_at=1), requests, context, on_usage=run_log.sink
        )
        assert run_log.events == [("request", 0), ("usage", 0), ("request", 1), ("usage", 1)]
        assert len(outcomes) == 2
        assert (outcomes[1].failure is None, outcomes[1].audio) == (False, None)
        assert isinstance(outcomes[1].failure, tts.TtsResponseInvalid)

    # a sink that raises stops the run before the next request; a BaseException from the engine
    # propagates after the earlier usage was handed out
    run_log = _Log()

    def angry(record: UsageRecord) -> None:
        run_log.sink(record)
        raise RuntimeError("sink")

    with pytest.raises(RuntimeError):
        tts.synthesise_all(_PlainTts(tts, run_log), requests, context, on_usage=angry)
    assert run_log.events == [("request", 0), ("usage", 0)]
    run_log = _Log()
    with pytest.raises(KeyboardInterrupt):
        tts.synthesise_all(
            _PlainTts(tts, run_log, boom_at=2), requests, context, on_usage=run_log.sink
        )
    assert run_log.events == [
        ("request", 0),
        ("usage", 0),
        ("request", 1),
        ("usage", 1),
        ("request", 2),
    ]

    # no request: nothing is sent and nothing is returned
    run_log = _Log()
    assert tts.synthesise_all(_PlainTts(tts, run_log), [], context, on_usage=run_log.sink) == ()
    assert run_log.events == []

    # the outcome holds audio or a failure, never both and never neither; its usage is TTS usage
    record = usage_record("sim_caller_tts", context.run_id, "req-0")
    failure = tts.TtsTransportError(kind="read_error", attempts=4)
    with pytest.raises(ValueError, match=r"."):
        tts.TtsOutcome(audio=None, usage=(), failure=None)
    with pytest.raises(ValueError, match=r"."):
        tts.TtsOutcome(audio=b"\x00", usage=(), failure=failure)
    with pytest.raises(ValueError, match=r"."):
        tts.TtsOutcome(audio=b"", usage=(), failure=None)
    wrong = usage_record("stt", context.run_id, "req-0")
    with pytest.raises(ValueError, match=r"."):
        tts.TtsOutcome(audio=b"\x00", usage=(wrong,), failure=None)
    with pytest.raises(TypeError):
        tts.TtsOutcome(audio=b"\x00", usage=("not a record",), failure=None)
    assert tts.TtsOutcome(audio=b"\x00", usage=(), failure=None).usage == (), "a cache hit"
    assert tts.TtsOutcome(audio=None, usage=(record,), failure=failure).failure is failure

    # the language model: one request at a time, its usage handed out before the call returns
    llm_context = llm.LlmContext(run_id=RunId.new(), created_at=CREATED_AT)
    request = llm.LlmRequest(
        index=0,
        messages=(llm.LlmMessage(role="user", content="hello"),),
        model="fake-llm",
        max_tokens=64,
        seed=None,
        reasoning_effort=None,
    )

    class PlainLlm:
        def __init__(self, log: _Log) -> None:
            self.log = log

        def complete(self, request: Any, context: Any) -> Any:
            self.log.events.append(("request", request.index))
            record = usage_record("sim_caller_llm", context.run_id, f"req-{request.index}")
            return llm.LlmOutcome(text="reply", usage=(record,), failure=None)

    class StreamingLlm(PlainLlm):
        streams_usage = True

        def complete(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
            outcome = super().complete(request, context)
            if on_usage is not None:
                for record in outcome.usage:
                    on_usage(record)
            return outcome

    assert isinstance(PlainLlm(log), llm.LlmEngine)
    assert not isinstance(PlainLlm(log), llm.UsageStreamingLlmEngine)
    assert isinstance(StreamingLlm(log), llm.UsageStreamingLlmEngine)
    for llm_engine in (PlainLlm, StreamingLlm):
        run_log = _Log()
        outcome = llm.complete_once(
            llm_engine(run_log), request, llm_context, on_usage=run_log.sink
        )
        assert outcome.text == "reply"
        assert run_log.events == [("request", 0), ("usage", 0)], llm_engine.__name__
    with pytest.raises(ValueError, match=r"."):
        llm.LlmOutcome(text=None, usage=(), failure=None)
    with pytest.raises(ValueError, match=r"."):
        llm.LlmOutcome(text="x", usage=(), failure=llm.LlmTransportError(kind="read_error"))
    wrong_stage = usage_record("sim_caller_tts", context.run_id, "r-0")
    with pytest.raises(ValueError, match=r"."):
        llm.LlmOutcome(text="x", usage=(wrong_stage,), failure=None)
    assert llm.LlmOutcome(text="", usage=(), failure=None).text == "", "an empty reply is a text"


# ---------------------------------------------------------------------------- the brain


class _Brain:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def next_line(self, turn_index: int, agent_text: str | None) -> Any:
        brain = mod("brain")
        if turn_index >= 1:
            return brain.BrainDecision(
                line=None, end_reason="script_complete", invalid_outputs=0, regenerations=0
            )
        return brain.BrainDecision(line="line", end_reason=None, invalid_outputs=0, regenerations=0)


def test_brain_protocol_accepts_scripted_llm_replay() -> None:
    brain = mod("brain")
    types = mod("simtypes")
    assert tuple(types.CALLER_MODES) == ("scripted", "llm", "replay")
    assert types.END_SENTINEL == "END_CALL"
    assert set(brain.BRAIN_END_REASONS) <= set(types.END_REASONS)
    assert set(brain.BRAIN_END_REASONS) == {
        "script_complete",
        "caller_end_call",
        "max_turns",
        "brain_invalid_output",
        "spend_cap",
    }
    assert brain.__all__ == sorted(brain.__all__)
    assert list(inspect.signature(brain.CallerBrain.next_line).parameters) == [
        "self",
        "turn_index",
        "agent_text",
    ]
    for mode in ("scripted", "llm", "replay"):
        instance = _Brain(mode)
        assert isinstance(instance, brain.CallerBrain), mode
        assert brain.require_caller_brain(instance) is instance
        step = instance.next_line(0, None)
        assert (step.line, step.end_reason) == ("line", None)
        end = instance.next_line(1, "agent said something")
        assert (end.line, end.end_reason) == (None, "script_complete")
    # refusals: no mode, an unknown mode, no next_line, a class instead of an instance
    with pytest.raises(ValueError, match=r"."):
        brain.require_caller_brain(_Brain("random"))

    class NoMode:
        def next_line(self, turn_index: int, agent_text: str | None) -> Any: ...

    class NoMethod:
        mode = "scripted"

    for bad in (NoMode(), NoMethod(), object(), None, "scripted"):
        with pytest.raises(TypeError):
            brain.require_caller_brain(bad)

    # the decision: a line or an end reason, never both and never neither
    def make(**over: Any) -> Any:
        fields: dict[str, Any] = {
            "line": "line",
            "end_reason": None,
            "invalid_outputs": 0,
            "regenerations": 0,
        }
        fields.update(over)
        return brain.BrainDecision(**fields)

    assert make().line == "line"
    assert make(line=None, end_reason="caller_end_call").end_reason == "caller_end_call"
    assert make(invalid_outputs=2, regenerations=1).regenerations == 1
    assert make(line="x" * 2500).line == "x" * 2500
    for bad_fields in (
        {"end_reason": "script_complete"},
        {"line": None},
        {"line": ""},
        {"line": "x" * 2501},
        {"line": None, "end_reason": "agent_hangup"},
        {"line": None, "end_reason": "not_a_reason"},
        {"invalid_outputs": -1},
        {"invalid_outputs": True},
        {"regenerations": -1},
        {"regenerations": 1},
        {"line": 5},
    ):
        with pytest.raises((ValueError, TypeError)):
            make(**bad_fields)
    parameters = inspect.signature(brain.BrainDecision).parameters
    assert all(p.default is inspect.Parameter.empty for p in parameters.values()), "no defaults"


# ------------------------------------------------- added by qa (not in the SCOPE table)


def test_tts_llm_request_records_have_no_defaults_and_validate() -> None:
    tts = mod("tts")
    llm = mod("llm")
    for cls in (
        tts.TtsVoice,
        tts.TtsRequest,
        tts.TtsContext,
        llm.LlmMessage,
        llm.LlmRequest,
        llm.LlmContext,
    ):
        parameters = inspect.signature(cls).parameters
        assert all(p.default is inspect.Parameter.empty for p in parameters.values()), cls.__name__
    assert tuple(tts.TTS_CODECS) == ("linear16", "mulaw")
    assert tuple(llm.LLM_ROLES) == ("system", "user", "assistant")

    def voice(**over: Any) -> Any:
        fields: dict[str, Any] = {
            "provider": "fake",
            "model": "fake-tts",
            "speaker": "fake-voice",
            "language_code": "mr-IN",
            "pace": "1",
        }
        fields.update(over)
        return tts.TtsVoice(**fields)

    assert [voice(pace=p).pace for p in ("0.5", "1.25", "999", "0")] == ["0.5", "1.25", "999", "0"]
    for bad in (
        {"pace": 1.0},
        {"pace": "1.0"},
        {"pace": "01"},
        {"pace": "1000"},
        {"pace": "1."},
        {"pace": "-1"},
        {"pace": ""},
        {"provider": "bad name"},
        {"model": ""},
        {"speaker": "x" * 65},
        {"language_code": "mr"},
        {"language_code": "MR-in"},
        {"speaker": CANARY + " "},
    ):
        with pytest.raises(ValueError, match=r".") as caught:
            voice(**bad)
        assert CANARY not in str(caught.value) + repr(caught.value)
    # the language is the engine's to refuse (TtsLanguageNotSupportedError), not the record's
    assert voice(language_code="hi-IN").language_code == "hi-IN"

    def request(**over: Any) -> Any:
        fields: dict[str, Any] = {
            "index": 0,
            "text": "line",
            "voice": voice(),
            "sample_rate_hz": 8000,
            "codec": "mulaw",
        }
        fields.update(over)
        return tts.TtsRequest(**fields)

    assert request(sample_rate_hz=48000, codec="linear16").codec == "linear16"
    for bad_request in (
        {"index": -1},
        {"index": True},
        {"text": ""},
        {"text": b"bytes"},
        {"sample_rate_hz": 7999},
        {"sample_rate_hz": 48001},
        {"sample_rate_hz": 16000.0},
        {"codec": "mp3"},
        {"codec": "wav"},
    ):
        with pytest.raises(ValueError, match=r"."):
            request(**bad_request)
    with pytest.raises(TypeError):
        request(voice="fake-voice")

    def llm_request(**over: Any) -> Any:
        fields: dict[str, Any] = {
            "index": 0,
            "messages": (llm.LlmMessage(role="system", content="s"),),
            "model": "fake-llm",
            "max_tokens": 1,
            "seed": 0,
            "reasoning_effort": "low",
        }
        fields.update(over)
        return llm.LlmRequest(**fields)

    assert llm_request(seed=None, reasoning_effort=None).seed is None
    assert llm_request(max_tokens=2**53 - 1).max_tokens == 2**53 - 1
    for bad_llm in (
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_tokens": 2**53},
        {"max_tokens": 1.5},
        {"seed": -1},
        {"seed": True},
        {"seed": 2**53},
        {"reasoning_effort": "High"},
        {"reasoning_effort": ""},
        {"model": ""},
        {"model": "a b"},
        {"messages": ()},
        {"messages": [llm.LlmMessage(role="user", content="x")]},
        {"index": -1},
    ):
        with pytest.raises(ValueError, match=r"."):
            llm_request(**bad_llm)
    with pytest.raises(TypeError):
        llm_request(messages=("text",))
    for bad_role in ("tool", "System", ""):
        with pytest.raises(ValueError, match=r"."):
            llm.LlmMessage(role=bad_role, content="x")
    with pytest.raises(ValueError, match=r"."):
        llm.LlmMessage(role="user", content="")


def _first_json(text: str, heading: str) -> dict[str, Any]:
    start = text.index(heading)
    block = text.index("```json", start) + len("```json")
    parsed: dict[str, Any] = json.loads(text[block : text.index("```", block)])
    return parsed


def test_sim_constants_are_the_ones_the_adrs_state() -> None:
    types = mod("simtypes")
    assert types.__all__ == sorted(types.__all__)
    adr27 = ADR27.read_text(encoding="utf-8")
    outcome = _first_json(adr27, "## The `tracer.sim_outcome/1` format")
    timing = _first_json(adr27, "## The `tracer.timing/1` format")
    profile = _first_json(adr27, "## The `tracer.sim_profile/1` format")
    props = outcome["properties"]
    assert tuple(types.END_REASONS) == tuple(props["end_reason"]["enum"])
    assert len(types.END_REASONS) == 12
    assert tuple(types.CALLER_MODES) == tuple(props["caller_text_origin"]["enum"])
    assert tuple(f"{c}" for c in types.CAP_NAMES) == tuple(
        c for c in props["cap_reached"]["enum"] if c is not None
    )
    for document in (outcome, timing, profile):
        assert tuple(types.DIALECTS) == tuple(document["properties"]["dialect"]["enum"])
    assert profile["properties"]["caller"]["oneOf"][1]["properties"]["end_sentinel"]["enum"] == [
        types.END_SENTINEL
    ]
    assert profile["properties"]["audio"]["properties"]["frame_ms"]["enum"] == [types.FRAME_MS]
    assert timing["properties"]["caller_frame_ms"]["enum"] == [types.FRAME_MS]
    assert (
        profile["properties"]["caller"]["oneOf"][1]["properties"]["max_line_chars"]["maximum"]
        == types.MAX_LINE_CHARS
    )
    # the eight states, in the order ADR 0026 rule 9 lists them
    adr26 = ADR26.read_text(encoding="utf-8")
    rule9 = adr26[adr26.index("9. **The call state machine") :]
    rule9 = rule9[: rule9.index("\n10. ")]
    listed = rule9[rule9.index("exactly the states") : rule9.index("and exactly the end reasons")]
    assert tuple(re.findall(r"`([a-z_]+)`", listed)) == tuple(types.CALL_STATES)
    assert len(types.CALL_STATES) == 8
    reasons = rule9[rule9.index("exactly the end reasons") : rule9.index("Every call ends")]
    assert tuple(re.findall(r"`([a-z_]+)`", reasons)) == tuple(types.END_REASONS)
