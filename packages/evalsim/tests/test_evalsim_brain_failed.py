# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-338 (batch G): the caller's language model fails during a call, in ``tracer-evalsim``.

Contract: docs/phase4/api-surface-batch-g.md section 8. A failure of the caller brain's
provider (a 401, a 500, a transport error, a timeout) used to be a ``CallerEnd("transport_error")``,
so the call looked like a lost connection and the command exited 0 with no caller turn. It is now
``CallerEnd("brain_failed")`` carrying only typed facts: a stable code naming the error class and
the HTTP status when the error has one. The provider's message, a body, a header and a key are not
in any of it (``FactError`` never holds them). The line provider does the mapping; the runner ends
the call with that reason, keeps everything recorded so far and hangs up.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from evalsim_support_agents import Silence, Speak, Wait  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_g import inline, mod, play_g  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import audio_for, dialect_of  # type: ignore[import-not-found,unused-ignore]

CODES = (
    "llm_auth",
    "llm_error",
    "llm_rate_limited",
    "llm_rejected",
    "llm_response_invalid",
    "llm_server_error",
    "llm_transport",
)


def llm() -> Any:
    import importlib

    return importlib.import_module("tracer_evalcore.llm")


def failures() -> list[tuple[Any, str, int | None]]:
    """(the error the engine raises, the code, the status)."""
    m = llm()
    return [
        (m.LlmAuthError(index=0, status=401, attempts=1), "llm_auth", 401),
        (m.LlmAuthError(index=0, status=403, attempts=1), "llm_auth", 403),
        (m.LlmRequestRejected(index=0, status=400, attempts=1), "llm_rejected", 400),
        (m.LlmRateLimited(index=0, status=429, attempts=4), "llm_rate_limited", 429),
        (m.LlmServerError(index=0, status=500, attempts=4), "llm_server_error", 500),
        (m.LlmServerError(index=0, status=503, attempts=4), "llm_server_error", 503),
        (m.LlmTransportError(index=0, kind="timeout", attempts=4), "llm_transport", None),
        (m.LlmTransportError(index=0, kind="connect_error", attempts=4), "llm_transport", None),
        (
            m.LlmResponseInvalid(index=0, status=200, reason="not_json", attempts=1),
            "llm_response_invalid",
            200,
        ),
        (m.LlmError(index=0), "llm_error", None),
        (m.LlmKeyMissingError(reason="key_missing"), "llm_error", None),
    ]


class Brain:
    """A brain whose ``next_line`` raises ``error`` from the ``fail_at``-th request on."""

    def __init__(self, error: BaseException, *, fail_at: int = 0) -> None:
        self._error = error
        self._fail_at = fail_at
        self.requests = 0

    def next_line(self, turn_index: int, agent_text: str | None) -> Any:
        self.requests += 1
        if turn_index >= self._fail_at:
            raise self._error
        decision = importlib_brain().BrainDecision("ab", None, 0, 0)
        return decision


def importlib_brain() -> Any:
    import importlib

    return importlib.import_module("tracer_evalcore.brain")


def lines_of(brain: Brain, clock: Any) -> Any:
    from tracer_evalcore.tts import TtsOutcome

    def synthesise(text: str, index: int) -> Any:
        dialect = dialect_of("protobuf")
        return TtsOutcome(audio=audio_for(dialect, 5), usage=(), failure=None)

    return mod("lines").LlmLines(
        brain,
        synthesise,
        clock=clock,
        sample_rate_hz=16000,
        wire_codec="pcm_s16le",
        run_blocking=inline(),
        requests_of=lambda: brain.requests,
    )


def test_the_failure_codes_are_a_closed_sorted_list_of_machine_codes() -> None:
    codes = mod("lines").BRAIN_FAILURE_CODES
    assert tuple(codes) == CODES
    assert list(codes) == sorted(set(codes))


@pytest.mark.parametrize(("error", "code", "status"), failures())
def test_a_failed_brain_request_ends_the_caller_with_brain_failed_and_typed_facts(
    error: Any, code: str, status: int | None
) -> None:
    """Every subclass of ``LlmError`` except the cap is a failed brain. The fact kept is the code
    of the error class and its HTTP status, if it has one; the request is counted."""
    brain = Brain(error)

    async def ask() -> Any:
        return await lines_of(brain, mod("clock").SystemClock()).next(0, None)

    import asyncio

    end = asyncio.run(ask())
    assert isinstance(end, mod("runner").CallerEnd)
    assert end.end_reason == "brain_failed"
    assert (end.failure_code, end.failure_status) == (code, status)
    assert end.requests == 1
    assert end.cap_reached is None
    assert (end.invalid_outputs, end.regenerations) == (0, 0)
    assert mod("lines").brain_failure_code(error) == code
    assert all(
        isinstance(getattr(end, f.name), str | int | type(None)) for f in dataclasses.fields(end)
    )  # facts only: no exception object, no message


def test_the_cap_is_still_a_spend_cap_and_not_a_failure() -> None:
    """``LlmCapReachedError`` is a subclass of ``LlmError`` but not a failure of the provider:
    the call ends ``spend_cap`` naming the cap, as before."""
    import asyncio

    brain = Brain(llm().LlmCapReachedError(index=0, reason="llm_tokens"))
    end = asyncio.run(lines_of(brain, mod("clock").SystemClock()).next(0, None))
    assert end.end_reason == "spend_cap"
    assert end.cap_reached == "llm_tokens"
    assert end.failure_code is None
    assert end.failure_status is None


def test_an_error_that_is_not_an_llm_error_is_not_swallowed() -> None:
    """A defect is not a provider failure: a ``RuntimeError`` from the brain reaches the caller of
    the line provider (the command reports it as an unexpected failure, exit 1)."""
    import asyncio

    brain = Brain(RuntimeError("defect"))
    with pytest.raises(RuntimeError):
        asyncio.run(lines_of(brain, mod("clock").SystemClock()).next(0, None))


def test_a_good_request_is_a_line_with_no_failure_facts() -> None:
    import asyncio

    brain = Brain(RuntimeError("never"), fail_at=99)
    line = asyncio.run(lines_of(brain, mod("clock").SystemClock()).next(0, None))
    assert isinstance(line, mod("runner").PreparedLine)
    assert line.text == "ab"


def test_the_end_record_has_the_new_fields_with_no_default_other_than_none() -> None:
    end = mod("runner").CallerEnd("script_complete")
    assert (end.failure_code, end.failure_status) == (None, None)
    names = [f.name for f in dataclasses.fields(mod("runner").CallerEnd)]
    assert names[-2:] == ["failure_code", "failure_status"]
    assert "failure_http_status" in [f.name for f in dataclasses.fields(mod("runner").CallResult)]


# ------------------------------------------------------------------------------- the runner


class FailsAfter:
    """A line provider (mode ``llm``) that says ``turns`` lines and then fails."""

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
            "brain_failed",
            requests=1,
            failure_code=self._failure[0],
            failure_status=self._failure[1],
        )


@pytest.mark.parametrize("kind", ["protobuf", "twilio"])
def test_the_runner_ends_the_call_brain_failed_after_a_line_and_keeps_what_was_recorded(
    kind: str,
) -> None:
    """One line of 5 frames is sent (0 to 80 ms), the agent answers (400 to 580 ms), the caller
    waits 300 ms of silence (to 880 ms), asks its brain for the second line and the brain fails:
    the end reason is ``brain_failed``, the call was established, one turn was sent in full, the
    agent's frames are kept and the call is hung up in the dialect. Failure facts are in the
    result."""
    dialect = dialect_of(kind)
    provider = FailsAfter(dialect, 1, ("llm_server_error", 500))
    played, _ = play_g(kind, [Wait(400), Speak(10)], provider, switch_ms=None)
    result = played.result
    assert result.end_reason == "brain_failed"
    assert result.call_established is True
    assert result.failure_code == "llm_server_error"
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


def test_a_brain_that_fails_at_the_very_first_request_leaves_a_call_without_a_turn() -> None:
    """The case of BL-338: the call is up, the caller speaks first and has nothing to say. The
    record is an established call of zero caller turns that ended ``brain_failed`` at once."""
    dialect = dialect_of("protobuf")
    provider = FailsAfter(dialect, 0, ("llm_auth", 401))
    played, _ = play_g("protobuf", [Silence(100)], provider, switch_ms=None)
    result = played.result
    assert (result.end_reason, result.call_established) == ("brain_failed", True)
    assert (result.failure_code, result.failure_http_status) == ("llm_auth", 401)
    assert result.turns == ()
    assert played.at(result.observed_until_ns) == 0
    assert result.caller_frames == ()
    assert result.counts["caller_turns_started"] == 0
    assert result.counts["llm_requests"] == 1


def test_a_failure_without_a_status_leaves_the_status_empty() -> None:
    dialect = dialect_of("protobuf")
    provider = FailsAfter(dialect, 0, ("llm_transport", None))
    played, _ = play_g("protobuf", [Silence(100)], provider, switch_ms=None)
    assert played.result.failure_code == "llm_transport"
    assert played.result.failure_http_status is None


def test_other_end_reasons_carry_no_failure_status() -> None:
    from evalsim_support_runner import lines

    dialect = dialect_of("protobuf")
    played, _ = play_g("protobuf", [Wait(400), Speak(10)], lines(dialect, 5), switch_ms=None)
    assert played.result.end_reason in ("script_complete", "max_silence")
    assert played.result.failure_http_status is None
    assert played.result.failure_code is None
