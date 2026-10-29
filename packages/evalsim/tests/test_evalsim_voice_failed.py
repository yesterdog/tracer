# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-369 (batch voice): the caller's speech synthesis fails during a call, in ``tracer-evalsim``.

Contract: docs/phase4/api-surface-batch-voice.md sections 3 and 4. A failure of the caller's
text-to-speech in the middle of a call (a 401, a 500, a transport error, a reply that is not audio)
used to be a ``CallerEnd("transport_error")``: the call looked like a lost connection and the
command exited 0. It is now ``CallerEnd("voice_failed")`` carrying only typed facts: a stable code
naming the error class and the HTTP status when the error has one. The provider's message, a body,
a header and a key are not in any of it (``FactError`` never holds them). A spend cap of the voice
is not a failure and stays ``spend_cap``. Both ways an engine can fail are covered: it may return
an outcome that holds the failure, and it may raise it. The line provider does the mapping; the
runner ends the call with that reason, keeps everything recorded so far and hangs up.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
from typing import Any

import pytest

from evalsim_support_agents import Silence, Speak, Wait  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_g import inline, mod, play_g  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import audio_for, dialect_of  # type: ignore[import-not-found,unused-ignore]

CODES = (
    "tts_auth",
    "tts_error",
    "tts_rate_limited",
    "tts_rejected",
    "tts_response_invalid",
    "tts_server_error",
    "tts_transport",
)


def tts() -> Any:
    return importlib.import_module("tracer_evalcore.tts")


def failures() -> list[tuple[Any, str, int | None]]:
    """(the error the engine reports, the code, the status): the exact class to key table of
    the contract, section 3.1."""
    m = tts()
    return [
        (m.TtsAuthError(index=0, status=401, attempts=1), "tts_auth", 401),
        (m.TtsAuthError(index=0, status=403, attempts=1), "tts_auth", 403),
        (m.TtsRequestRejected(index=0, status=400, attempts=1), "tts_rejected", 400),
        (m.TtsRequestRejected(index=0, status=422, attempts=1), "tts_rejected", 422),
        (m.TtsRateLimited(index=0, status=429, attempts=4), "tts_rate_limited", 429),
        (m.TtsServerError(index=0, status=500, attempts=4), "tts_server_error", 500),
        (m.TtsServerError(index=0, status=503, attempts=4), "tts_server_error", 503),
        (m.TtsTransportError(index=0, kind="timeout", attempts=4), "tts_transport", None),
        (m.TtsTransportError(index=0, kind="connect_error", attempts=4), "tts_transport", None),
        (
            m.TtsResponseInvalid(index=0, status=200, reason="audio_not_recognised", attempts=1),
            "tts_response_invalid",
            200,
        ),
        (
            m.TtsResponseInvalid(index=0, reason="cache_entry_invalid"),
            "tts_response_invalid",
            None,
        ),
        (m.TtsError(index=0), "tts_error", None),
        (m.TtsKeyMissingError(reason="key_missing"), "tts_error", None),
        (
            m.TtsLanguageNotSupportedError(index=0, reason="language_not_supported"),
            "tts_error",
            None,
        ),
        (m.TtsTextTooLongError(index=0, reason="text_too_long"), "tts_error", None),
    ]


def brain_mod() -> Any:
    return importlib.import_module("tracer_evalcore.brain")


class Brain:
    """A brain that answers every request with the line ``ab`` and counts its requests."""

    def __init__(
        self, *, invalid: int = 0, regenerations: int = 0, line: str | None = "ab"
    ) -> None:
        self.requests = 0
        self._invalid = invalid
        self._regenerations = regenerations
        self._line = line

    def next_line(self, turn_index: int, agent_text: str | None) -> Any:
        self.requests += 1
        if self._line is None:
            return brain_mod().BrainDecision(None, "script_complete", 0, 0)
        return brain_mod().BrainDecision(self._line, None, self._invalid, self._regenerations)


class Voice:
    """A synthesis function: it fails at the ``fail_at``-th line, by returning the failure in the
    outcome (``how="return"``) or by raising it (``how="raise"``), and says every other line."""

    def __init__(
        self, error: BaseException | None, *, how: str = "return", fail_at: int = 0
    ) -> None:
        self.error = error
        self.how = how
        self.fail_at = fail_at
        self.asked: list[tuple[str, int]] = []

    def __call__(self, text: str, index: int) -> Any:
        self.asked.append((text, index))
        if self.error is not None and index >= self.fail_at:
            if self.how == "raise":
                raise self.error
            return tts().TtsOutcome(audio=None, usage=(), failure=self.error)
        return tts().TtsOutcome(audio=audio_for(dialect_of("protobuf"), 5), usage=(), failure=None)


def lines_of(brain: Brain, voice: Voice, clock: Any) -> Any:
    return mod("lines").LlmLines(
        brain,
        voice,
        clock=clock,
        sample_rate_hz=16000,
        wire_codec="pcm_s16le",
        run_blocking=inline(),
        requests_of=lambda: brain.requests,
    )


def first(brain: Brain, voice: Voice, turn: int = 0) -> Any:
    clock = mod("clock").SystemClock()
    return asyncio.run(lines_of(brain, voice, clock).next(turn, None))


def test_the_voice_failure_codes_are_a_closed_sorted_list_of_machine_codes() -> None:
    codes = mod("lines").VOICE_FAILURE_CODES
    assert tuple(codes) == CODES
    assert list(codes) == sorted(set(codes))
    assert all(c.startswith("tts_") for c in codes)
    assert not set(codes) & set(mod("lines").BRAIN_FAILURE_CODES)
    assert "voice_failure_code" in mod("lines").__all__
    assert "VOICE_FAILURE_CODES" in mod("lines").__all__


@pytest.mark.parametrize("how", ["return", "raise"])
@pytest.mark.parametrize(("error", "code", "status"), failures())
def test_a_failed_synthesis_ends_the_caller_with_voice_failed_and_typed_facts(
    error: Any, code: str, status: int | None, how: str
) -> None:
    """Every ``TtsError`` except the cap is a failed voice, whether the engine returned it in the
    outcome or raised it. The fact kept is the code of the error class and its HTTP status, if it
    has one; the brain request that preceded is counted; what the brain reported is kept."""
    brain = Brain(invalid=2, regenerations=1)
    voice = Voice(error, how=how)
    end = first(brain, voice)
    assert isinstance(end, mod("runner").CallerEnd)
    assert end.end_reason == "voice_failed"
    assert (end.failure_code, end.failure_status) == (code, status)
    assert end.requests == 1
    assert end.cap_reached is None
    assert (end.invalid_outputs, end.regenerations) == (2, 1)
    assert mod("lines").voice_failure_code(error) == code
    assert code in mod("lines").VOICE_FAILURE_CODES
    assert voice.asked == [("ab", 0)]
    assert all(
        isinstance(getattr(end, f.name), str | int | type(None)) for f in dataclasses.fields(end)
    )  # facts only: no exception object, no message


@pytest.mark.parametrize("how", ["return", "raise"])
def test_the_cap_of_the_voice_is_still_a_spend_cap_and_not_a_failure(how: str) -> None:
    """``TtsCapReachedError`` is a ``TtsError`` but not a failure of the provider: the call ends
    ``spend_cap`` naming the cap, as before, whether the engine returned or raised it."""
    error = tts().TtsCapReachedError(index=0, reason="tts_chars")
    end = first(Brain(invalid=1, regenerations=1), Voice(error, how=how))
    assert end.end_reason == "spend_cap"
    assert end.cap_reached == "tts_chars"
    assert end.failure_code is None
    assert end.failure_status is None
    assert end.requests == 1
    assert (end.invalid_outputs, end.regenerations) == (1, 1)


@pytest.mark.parametrize("turn", [0, 1, 4])
def test_a_synthesis_that_fails_later_fails_the_same_way(turn: int) -> None:
    voice = Voice(tts().TtsServerError(index=turn, status=500, attempts=4), fail_at=turn)
    end = first(Brain(), voice, turn)
    assert (end.end_reason, end.failure_code, end.failure_status) == (
        "voice_failed",
        "tts_server_error",
        500,
    )
    assert voice.asked == [("ab", turn)]


@pytest.mark.parametrize("how", ["ok", "return", "raise"])
def test_the_brain_and_the_voice_both_run_through_the_blocking_runner(how: str) -> None:
    """The engines are synchronous: each call goes through run_blocking (a thread by default),
    never on the event loop, whether the synthesis succeeds, returns a failure or raises one."""
    calls: list[int] = []

    async def run(function: Any) -> Any:
        calls.append(len(calls))
        return function()

    error = None if how == "ok" else tts().TtsServerError(index=0, status=500, attempts=4)
    brain = Brain()
    lines = mod("lines").LlmLines(
        brain,
        Voice(error, how=how),
        clock=mod("clock").SystemClock(),
        sample_rate_hz=16000,
        wire_codec="pcm_s16le",
        run_blocking=run,
        requests_of=lambda: brain.requests,
    )
    end = asyncio.run(lines.next(0, None))
    assert calls == [0, 1]
    assert getattr(end, "end_reason", None) == (None if how == "ok" else "voice_failed")


def test_an_error_that_is_not_a_tts_error_is_not_swallowed() -> None:
    """A defect is not a provider failure: a ``RuntimeError`` from the synthesis reaches the
    caller of the line provider (the command reports it as an unexpected failure, exit 1)."""
    with pytest.raises(RuntimeError):
        first(Brain(), Voice(RuntimeError("defect"), how="raise"))


def test_a_good_synthesis_is_a_line_with_no_failure_facts() -> None:
    line = first(Brain(), Voice(None))
    assert isinstance(line, mod("runner").PreparedLine)
    assert line.text == "ab"
    assert line.requests == 1
    assert len(line.frames) == 5


def test_a_brain_that_ends_the_call_never_asks_the_voice() -> None:
    voice = Voice(tts().TtsAuthError(index=0, status=401, attempts=1))
    end = first(Brain(line=None), voice)
    assert end.end_reason == "script_complete"
    assert end.failure_code is None
    assert voice.asked == []


def test_a_failed_brain_is_still_brain_failed_and_never_reaches_the_voice() -> None:
    """The order of the two failures: the brain is asked first, so its failure wins and the voice
    is not asked; the codes of the two never mix."""
    llm = importlib.import_module("tracer_evalcore.llm")

    class Broken(Brain):
        def next_line(self, turn_index: int, agent_text: str | None) -> Any:
            self.requests += 1
            raise llm.LlmAuthError(index=0, status=401, attempts=1)

    voice = Voice(tts().TtsAuthError(index=0, status=401, attempts=1))
    end = first(Broken(), voice)
    assert (end.end_reason, end.failure_code) == ("brain_failed", "llm_auth")
    assert voice.asked == []


def test_voice_failure_code_maps_by_class_in_the_order_of_the_contract() -> None:
    """A subclass of a mapped class takes the code of the mapped class; any other ``TtsError``,
    configuration errors included, is ``tts_error``."""
    from tracer_evalcore.tts import TtsAuthError, TtsError, TtsKeyMissingError

    class MyAuth(TtsAuthError):
        pass

    class MyOther(TtsError):
        pass

    code = mod("lines").voice_failure_code
    assert code(MyAuth(index=0, status=401, attempts=1)) == "tts_auth"
    assert code(MyOther(index=0)) == "tts_error"
    assert code(TtsKeyMissingError(reason="key_missing")) == "tts_error"


# ------------------------------------------------------------------------------- the runner


class SpeaksThenFails:
    """A line provider (mode ``llm``) that says ``turns`` lines and then loses its voice."""

    mode = "llm"
    needs_agent_text = True

    def __init__(self, dialect: Any, turns: int, failure: tuple[str, int | None]) -> None:
        self._dialect = dialect
        self._turns = turns
        self._failure = failure
        self.asked: list[int] = []

    async def next(self, turn_index: int, agent_text: str | None) -> Any:
        runner = mod("runner")
        self.asked.append(turn_index)
        if turn_index < self._turns:
            return runner.PreparedLine(
                text=f"line {turn_index}",
                frames=tuple(audio_for(self._dialect, 1) for _ in range(5)),
                requests=1,
            )
        return runner.CallerEnd(
            "voice_failed",
            requests=1,
            failure_code=self._failure[0],
            failure_status=self._failure[1],
        )


@pytest.mark.parametrize("kind", ["protobuf", "twilio"])
def test_the_runner_ends_the_call_voice_failed_after_a_line_and_keeps_what_was_recorded(
    kind: str,
) -> None:
    """One line of 5 frames is sent (0 to 80 ms), the agent answers (400 to 580 ms), the caller
    waits 300 ms of silence (to 880 ms), asks for the second line and its voice fails: the end
    reason is ``voice_failed``, the call was established, one turn was sent in full, the agent's
    frames are kept and the call is hung up in the dialect. Failure facts are in the result."""
    dialect = dialect_of(kind)
    provider = SpeaksThenFails(dialect, 1, ("tts_server_error", 500))
    played, _ = play_g(kind, [Wait(400), Speak(10)], provider, switch_ms=None)
    result = played.result
    assert result.end_reason == "voice_failed"
    assert result.call_established is True
    assert result.failure_code == "tts_server_error"
    assert result.failure_http_status == 500
    assert played.at(result.observed_until_ns) == 880
    (turn,) = result.turns
    assert (turn.frames_sent, turn.complete) == (5, True)
    assert len(result.agent_frames) == 10
    assert provider.asked == [0, 1]
    assert result.counts["caller_turns_started"] == 1
    assert result.counts["caller_turns_completed"] == 1
    assert result.counts["llm_requests"] == 2
    assert result.counts["brain_requests"] == 2
    assert result.cap_reached is None
    assert result.watchdog_fired is False
    if kind == "twilio":
        import json

        assert json.loads(played.client.sent[-1])["event"] == "stop"
    else:
        assert played.client.calls[-1] == ("close", 1000)


def test_a_voice_that_fails_at_the_very_first_line_leaves_a_call_without_a_turn() -> None:
    """The case of BL-369: the call is up, the brain has a first line and the voice cannot say
    it. The record is an established call of zero caller turns that ended ``voice_failed`` at
    once; the brain request is counted."""
    dialect = dialect_of("protobuf")
    provider = SpeaksThenFails(dialect, 0, ("tts_auth", 401))
    played, _ = play_g("protobuf", [Silence(100)], provider, switch_ms=None)
    result = played.result
    assert (result.end_reason, result.call_established) == ("voice_failed", True)
    assert (result.failure_code, result.failure_http_status) == ("tts_auth", 401)
    assert result.turns == ()
    assert played.at(result.observed_until_ns) == 0
    assert result.caller_frames == ()
    assert result.counts["caller_turns_started"] == 0
    assert result.counts["llm_requests"] == 1
    assert result.counts["brain_requests"] == 1


def test_a_voice_failure_without_a_status_leaves_the_status_empty() -> None:
    dialect = dialect_of("protobuf")
    provider = SpeaksThenFails(dialect, 0, ("tts_transport", None))
    played, _ = play_g("protobuf", [Silence(100)], provider, switch_ms=None)
    assert played.result.failure_code == "tts_transport"
    assert played.result.failure_http_status is None


def test_voice_failed_and_brain_failed_are_two_reasons_with_the_same_fact_fields() -> None:
    dialect = dialect_of("protobuf")
    brain_end = importlib.import_module("tracer_evalsim.runner").CallerEnd(
        "brain_failed", requests=1, failure_code="llm_auth", failure_status=401
    )

    class BrainDown(SpeaksThenFails):
        async def next(self, turn_index: int, agent_text: str | None) -> Any:
            return brain_end

    played, _ = play_g("protobuf", [Silence(100)], BrainDown(dialect, 0, ("x", None)))
    assert played.result.end_reason == "brain_failed"
    assert played.result.failure_code == "llm_auth"


def test_only_the_llm_line_provider_can_lose_its_voice_during_a_call() -> None:
    """Reachability, stated by the code and not assumed: the scripted and the replayed caller are
    served by ``PresynthesisedLines``, which holds audio that exists before the call and takes no
    synthesis function, so it makes no request during the call and cannot end ``voice_failed``;
    ``LlmLines`` takes the synthesis function and is the only provider with a voice to lose."""
    import inspect

    lines = mod("lines")
    pre = list(inspect.signature(lines.PresynthesisedLines.__init__).parameters)
    assert pre == ["self", "mode", "entries", "final", "sample_rate_hz", "wire_codec"]
    assert lines.PresynthesisedLines.needs_agent_text is False
    assert "synthesise" in inspect.signature(lines.LlmLines.__init__).parameters
    assert lines.LlmLines.mode == "llm"
    final = importlib.import_module("tracer_evalsim.runner").CallerEnd("script_complete")
    provider = lines.PresynthesisedLines(
        "scripted",
        [lines.SynthesisedLine("ab", audio_for(dialect_of("protobuf"), 5))],
        final,
        sample_rate_hz=16000,
        wire_codec="pcm_s16le",
    )
    for turn in (0, 1, 2):
        item = asyncio.run(provider.next(turn, None))
        assert getattr(item, "end_reason", None) in (None, "script_complete")
        assert getattr(item, "failure_code", None) is None


def test_other_end_reasons_carry_no_voice_failure_status() -> None:
    from evalsim_support_runner import lines

    dialect = dialect_of("protobuf")
    played, _ = play_g("protobuf", [Wait(400), Speak(10)], lines(dialect, 5), switch_ms=None)
    assert played.result.end_reason in ("script_complete", "max_silence")
    assert played.result.failure_http_status is None
    assert played.result.failure_code is None
