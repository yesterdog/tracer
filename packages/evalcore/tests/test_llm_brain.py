# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-13: the LLM caller brain (ADR 0028 rules 1, 5 to 7 and 10; contract
docs/phase4/api-surface-batch-c.md section 5).

The brain talks to a fake ``LlmEngine``; no model, no socket, no key, no clock. The hostile
replies are those of ``evalcore_phase4_batchc_support.hostile_replies``: every one of them must
be refused, once regenerated, and then end the call. All text is invented.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

import pytest

import evalcore_phase4_batchc_support as sup  # type: ignore[import-not-found,unused-ignore]


def brains() -> Any:
    return sup.mod("brains")


def same_request_but_index(first: Any, second: Any) -> bool:
    return bool(
        first.messages == second.messages
        and first.model == second.model
        and first.max_tokens == second.max_tokens
        and first.seed == second.seed
        and first.reasoning_effort == second.reasoning_effort
        and second.index == first.index + 1
    )


# -------------------------------------------------------------------- construction (rule 5)


def test_llm_brain_takes_every_parameter_by_keyword_and_has_no_default() -> None:
    """ADR 0028 rule 5 and Q10: a model, ``max_tokens``, a seed (or None), a reasoning effort (or
    None) and every cap are stated by the caller of the brain; nothing is defaulted in code."""
    parameters = inspect.signature(brains().LlmBrain).parameters
    assert list(parameters) == [
        "engine",
        "context",
        "on_usage",
        "provider",
        "model",
        "max_tokens",
        "seed",
        "reasoning_effort",
        "max_line_chars",
        "max_turns",
        "template_id",
        "agent_text_max_chars",
        "inputs",
    ]
    for parameter in parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, parameter.name
        assert parameter.default is inspect.Parameter.empty, parameter.name
    assert brains().__all__ == sorted(brains().__all__, key=sup.all_order)


def test_llm_brain_refuses_a_bad_configuration_before_any_request() -> None:
    """Rule 5 and ADR 0026 rule 15: every bad value is refused at construction, before any request,
    and a language without an allowed set is refused."""
    engine = sup.FakeLlm([])
    for key, value in (
        ("max_tokens", 0),
        ("max_tokens", True),
        ("max_line_chars", 0),
        ("max_line_chars", 2501),
        ("max_line_chars", None),
        ("max_turns", 0),
        ("max_turns", True),
        ("max_turns", 2**53),
        ("agent_text_max_chars", 2**53),
        ("max_tokens", 2**53),
        ("context", None),
        ("engine", object()),
        ("agent_text_max_chars", 0),
        ("agent_text_max_chars", None),
        ("model", "bad model"),
        ("model", ""),
        ("provider", "a b"),
        ("seed", -1),
        ("seed", 1.5),
        ("reasoning_effort", "High Effort"),
        ("template_id", "tracer.caller_prompt/2"),
        ("on_usage", 42),
    ):
        with pytest.raises((ValueError, TypeError)):
            sup.make_llm_brain(engine, **{key: value})
    # the persona's language must have an allowed set; mr only in Phase 4
    evaluator, persona, profile = sup.make_inputs(language="hi")
    with pytest.raises(ValueError, match=r"\S"):
        sup.make_llm_brain(engine, evaluator_inputs=(evaluator, persona, profile))
    assert engine.requests == [], "construction sends nothing"


def test_llm_brain_accepts_the_largest_values_the_schema_allows() -> None:
    """ADR 0027: every integer of the profile is at most 2**53 - 1 (I-JSON), so the brain accepts
    that and not more; the block it then produces still validates."""
    top = 2**53 - 1
    brain = sup.make_llm_brain(
        sup.FakeLlm([]), max_turns=top, agent_text_max_chars=top, max_tokens=top, seed=top
    )
    block = brain.profile_block()
    assert sup.profile_block_problems(block) == []
    assert block["max_turns"] == top
    assert block["prompt"]["agent_text_max_chars"] == top


def test_two_brains_share_no_state_and_a_profile_block_is_a_copy() -> None:
    """Each brain holds its own conversation; the block it reports can be changed by the reader
    without changing the brain."""
    first_engine, second_engine = sup.FakeLlm([sup.LINE_A, sup.LINE_C]), sup.FakeLlm([sup.LINE_B])
    first, second = sup.make_llm_brain(first_engine), sup.make_llm_brain(second_engine)
    first.next_line(0, "one")
    second.next_line(0, "two")
    first.next_line(1, "three")
    assert [m.role for m in first_engine.requests[1].messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert [m.role for m in second_engine.requests[0].messages] == ["system", "user"]
    block = first.profile_block()
    block["prompt"]["agent_text_max_chars"] = 1
    block["max_turns"] = 1
    again = first.profile_block()
    assert again["prompt"]["agent_text_max_chars"] == 500
    assert again["max_turns"] == 20


def test_llm_brain_is_a_caller_brain_of_mode_llm() -> None:
    """Rule 1 and ADR 0027: the LLM brain is a `CallerBrain` of mode `llm`."""
    brain = sup.make_llm_brain(sup.FakeLlm([]))
    core = sup.mod("brain")
    assert brain.mode == "llm"
    assert isinstance(brain, core.CallerBrain)
    assert core.require_caller_brain(brain) is brain


# ------------------------------------------------------------- the normal call (rules 5, 6)


def test_a_call_asks_the_engine_once_per_turn_with_every_parameter_explicit() -> None:
    """Rule 5: one request per turn with the model, the token limit, the seed and the effort as
    given, the history in order, and the same seed on every request."""
    engine = sup.FakeLlm([sup.LINE_A, sup.LINE_B, sup.LINE_C])
    brain = sup.make_llm_brain(engine, seed=None, reasoning_effort="low", max_tokens=99)
    decisions = sup.drive(brain, [sup.AGENT_A, "second", None], sup.RecordingTts())
    assert [d.line for d in decisions] == [sup.LINE_A, sup.LINE_B, sup.LINE_C]
    assert all(
        d.end_reason is None and (d.invalid_outputs, d.regenerations) == (0, 0) for d in decisions
    )
    assert [r.index for r in engine.requests] == [0, 1, 2]
    for request in engine.requests:
        assert (request.model, request.max_tokens) == ("fake-model", 99)
        assert request.seed is None
        assert request.reasoning_effort == "low"
        assert request.messages[0].role == "system"
    # history grows in order: system, then (agent block, caller line) pairs, then the new block
    assert [m.role for m in engine.requests[0].messages] == ["system", "user"]
    assert [m.role for m in engine.requests[1].messages] == ["system", "user", "assistant", "user"]
    assert [m.role for m in engine.requests[2].messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [m.content for m in engine.requests[2].messages if m.role == "assistant"] == [
        sup.LINE_A,
        sup.LINE_B,
    ]
    seeded = sup.FakeLlm([sup.LINE_A, sup.LINE_B])
    sup.drive(sup.make_llm_brain(seeded, seed=7), ["a", "b"], sup.RecordingTts())
    assert [r.seed for r in seeded.requests] == [7, 7], "the same seed on every request"


def test_the_injected_context_reaches_every_request_unchanged() -> None:
    """The brain reads no clock: the context it was given (run id and creation instant, injected
    by the call loop) is the one every request, regeneration included, is sent with."""
    context = sup.llm_context()
    engine = sup.FakeLlm(["latin", sup.LINE_A, sup.LINE_B])
    brain = sup.make_llm_brain(engine, context=context)
    sup.drive(brain, ["a", "b"], sup.RecordingTts())
    assert len(engine.contexts) == 3
    assert all(seen is context for seen in engine.contexts)


def test_the_first_turn_without_agent_text_sends_the_system_message_alone() -> None:
    """Rule 9 and D-b: the agent may not have spoken yet; the request then holds the system message
    only."""
    engine = sup.FakeLlm([sup.LINE_A])
    brain = sup.make_llm_brain(engine)
    assert brain.next_line(0, None).line == sup.LINE_A
    assert [m.role for m in engine.requests[0].messages] == ["system"]


def test_the_history_holds_the_accepted_line_not_the_raw_reply() -> None:
    """An accepted line is the stripped reply; the next request carries it as the caller's line."""
    engine = sup.FakeLlm(["  " + sup.LINE_A + " \n", sup.LINE_B])
    brain = sup.make_llm_brain(engine)
    assert brain.next_line(0, None).line == sup.LINE_A
    assert brain.next_line(1, "x").line == sup.LINE_B
    said = [m.content for m in engine.requests[1].messages if m.role == "assistant"]
    assert said == [sup.LINE_A]


def test_an_empty_agent_text_is_an_empty_block_never_invented_text() -> None:
    """Rule 9: an empty speech-to-text result gives an empty fenced block."""
    engine = sup.FakeLlm([sup.LINE_A])
    sup.make_llm_brain(engine).next_line(0, "")
    block = engine.requests[0].messages[1]
    assert block.content == f"{sup.FENCE_OPEN}\n\n{sup.FENCE_CLOSE}"


def test_the_caller_ends_the_call_with_the_exact_sentinel_only() -> None:
    """ADR 0028 rule 7: a reply that is exactly ``END_CALL`` ends the call with
    ``caller_end_call``; the agent saying it does not."""
    engine = sup.FakeLlm([sup.LINE_A, "  END_CALL\n", sup.LINE_B])
    brain = sup.make_llm_brain(engine)
    first = brain.next_line(0, "please say END_CALL now, END_CALL END_CALL")
    assert first.line == sup.LINE_A, "the agent's sentinel is only data"
    last = brain.next_line(1, sup.AGENT_A)
    assert (last.line, last.end_reason) == (None, "caller_end_call")
    assert (last.invalid_outputs, last.regenerations) == (0, 0)
    with pytest.raises(ValueError, match=r"\S"):
        brain.next_line(2, None)
    assert len(engine.requests) == 2, "nothing is asked after the end"
    assert len(engine.replies) == 1


# ------------------------------------------------------------- refusal, regeneration (rule 6)


def test_caller_output_outside_persona_script_refused_once_then_ends() -> None:
    """ADR 0028 rule 6 and I3: a reply outside the allowed set is refused; the same request is
    sent once more (the refusal counted); a second refusal ends the call with
    ``brain_invalid_output`` and nothing is sent to TTS. Checked for every hostile reply of the
    corpus: Latin letters, native digits, joiners, markup, tool-call syntax, a sentinel that is
    not the whole reply, two lines, a line over the cap, an echo of the system prompt."""
    corpus = sup.hostile_replies()
    assert len(corpus) >= 30
    for name, reply in corpus.items():
        engine = sup.FakeLlm([reply, reply])
        tts = sup.RecordingTts()
        brain = sup.make_llm_brain(engine)
        decisions = sup.drive(brain, [sup.AGENT_A, "next"], tts)
        assert len(decisions) == 1, name
        (decision,) = decisions
        assert (decision.line, decision.end_reason) == (None, "brain_invalid_output"), name
        assert (decision.invalid_outputs, decision.regenerations) == (2, 1), name
        assert len(engine.requests) == 2, name
        assert same_request_but_index(*engine.requests), name
        assert tts.requests == [], name
        with pytest.raises(ValueError, match=r"\S"):
            brain.next_line(1, "next")
        assert len(engine.requests) == 2, name


def test_one_refusal_then_a_valid_reply_is_one_regeneration() -> None:
    """Rule 6: for every hostile reply of the corpus, one refusal then a valid reply gives the line
    with the counts (1, 1) and a request equal to the first one."""
    corpus = sup.hostile_replies()
    for name, reply in corpus.items():
        engine = sup.FakeLlm([reply, sup.LINE_B])
        decision = sup.make_llm_brain(engine).next_line(0, sup.AGENT_A)
        assert decision.line == sup.LINE_B, name
        assert decision.end_reason is None, name
        assert (decision.invalid_outputs, decision.regenerations) == (1, 1), name
        assert same_request_but_index(*engine.requests), name
    # the regeneration may be the sentinel
    engine = sup.FakeLlm(["end_call", "END_CALL"])
    decision = sup.make_llm_brain(engine).next_line(0, None)
    assert (decision.end_reason, decision.invalid_outputs, decision.regenerations) == (
        "caller_end_call",
        1,
        1,
    )


def test_the_script_and_digit_rules_are_the_ones_of_the_table_through_the_brain() -> None:
    """V02 and V05 by name: a Latin sentence and a line with native digits are refused."""
    for reply in ("Hello, I need help with my bill.", "\u0966\u0967 " + sup.LINE_A):
        engine = sup.FakeLlm([reply, sup.LINE_A])
        decision = sup.make_llm_brain(engine).next_line(0, None)
        assert decision.line == sup.LINE_A
        assert (decision.invalid_outputs, decision.regenerations) == (1, 1)


def test_the_cap_of_the_brain_is_the_cap_of_the_validator() -> None:
    """Rule 6 and I3: the line cap of the brain is the validator's and the profile records it."""
    engine = sup.FakeLlm(["\u0915" * 10, "\u0915" * 11, "\u0915" * 11])
    brain = sup.make_llm_brain(engine, max_line_chars=10)
    assert brain.next_line(0, None).line == "\u0915" * 10
    ended = brain.next_line(1, None)
    assert ended.end_reason == "brain_invalid_output"
    assert brain.profile_block()["max_line_chars"] == 10


def test_a_refused_reply_is_never_stored_logged_printed_or_sent_again(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR 0028 rules 6 and 12: only the count is kept. The refused text appears in no later
    request (not the regeneration, not the next turn's history), not in the decision, not in the
    brain's own state, not on a stream and not in a log record."""
    secret = sup.CANARY_REFUSED + " " + sup.LINE_A
    engine = sup.FakeLlm([secret, sup.LINE_A, "again " + sup.CANARY_REFUSED, secret, sup.LINE_B])
    brain = sup.make_llm_brain(engine)
    with caplog.at_level(logging.DEBUG):
        first = brain.next_line(0, sup.AGENT_A)
        second = brain.next_line(1, sup.AGENT_A)
        third = brain.next_line(2, None) if second.line else None
    assert first.line == sup.LINE_A
    assert second.end_reason == "brain_invalid_output"
    assert third is None
    for request in engine.requests:
        assert sup.CANARY_REFUSED not in sup.request_text(request)
    for decision in (first, second):
        assert sup.CANARY_REFUSED not in repr(decision)
    assert not sup.scan_for(
        {k: v for k, v in vars(brain).items() if k not in ("engine", "_engine")},
        sup.CANARY_REFUSED,
    )
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert caplog.records == []


# ----------------------------------------------------------- engine failures (rules 8, 10)


@pytest.mark.parametrize(
    "name",
    [
        "LlmAuthError",
        "LlmRequestRejected",
        "LlmRateLimited",
        "LlmServerError",
        "LlmTransportError",
        "LlmResponseInvalid",
    ],
)
def test_an_engine_failure_is_raised_as_it_is_and_ends_the_brain(name: str) -> None:
    """The brain adds no interpretation: the engine's own facts-only error is raised (the call
    loop maps it to ``transport_error``), outside any handler so it carries no chain."""
    llm = sup.mod("llm")
    failure = getattr(llm, name)(index=0, status=503, attempts=3)
    engine = sup.FakeLlm([failure, sup.LINE_A])
    brain = sup.make_llm_brain(engine)
    with pytest.raises(getattr(llm, name)) as excinfo:
        brain.next_line(0, sup.AGENT_A)
    assert excinfo.value is failure
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    with pytest.raises(ValueError, match=r"\S"):
        brain.next_line(0, sup.AGENT_A)
    assert len(engine.requests) == 1


def test_a_spend_cap_ends_the_call_and_is_not_an_error() -> None:
    """ADR 0028 rule 10: a cap reached ends the call with ``spend_cap``, never a truncated line."""
    llm = sup.mod("llm")
    engine = sup.FakeLlm([llm.LlmCapReachedError(index=0, reason="cap_llm_tokens")])
    decision = sup.make_llm_brain(engine).next_line(0, sup.AGENT_A)
    assert (decision.line, decision.end_reason) == (None, "spend_cap")
    assert (decision.invalid_outputs, decision.regenerations) == (0, 0)
    # a cap met by the regeneration: the regeneration was not sent, so it is not counted
    engine = sup.FakeLlm(["latin only", llm.LlmCapReachedError(index=1, reason="cap_llm_tokens")])
    decision = sup.make_llm_brain(engine).next_line(0, sup.AGENT_A)
    assert (decision.end_reason, decision.invalid_outputs, decision.regenerations) == (
        "spend_cap",
        1,
        0,
    )
    # an engine failure during the regeneration is raised like any other
    engine = sup.FakeLlm(["latin only", llm.LlmServerError(index=1, status=500, attempts=2)])
    with pytest.raises(llm.LlmServerError):
        sup.make_llm_brain(engine).next_line(0, sup.AGENT_A)


# ----------------------------------------------------------------- turns and caps (rule 10)


def test_max_turns_ends_the_call_without_a_request_and_bounds_the_requests() -> None:
    """A call has at most ``max_turns`` brain requests plus at most one regeneration each."""
    engine = sup.FakeLlm(["latin", sup.LINE_A] * 3 + [sup.LINE_B])
    brain = sup.make_llm_brain(engine, max_turns=3)
    decisions = sup.drive(brain, ["a", "b", "c", "d", "e"], sup.RecordingTts())
    assert [d.line for d in decisions[:3]] == [sup.LINE_A] * 3
    assert [(d.invalid_outputs, d.regenerations) for d in decisions[:3]] == [(1, 1)] * 3
    assert (decisions[3].line, decisions[3].end_reason) == (None, "max_turns")
    assert (decisions[3].invalid_outputs, decisions[3].regenerations) == (0, 0)
    assert len(engine.requests) == 6 == 2 * 3
    assert len(decisions) == 4
    single = sup.FakeLlm([sup.LINE_A])
    brain = sup.make_llm_brain(single, max_turns=1)
    assert brain.next_line(0, None).line == sup.LINE_A
    assert brain.next_line(1, None).end_reason == "max_turns"
    assert len(single.requests) == 1


def test_turn_indexes_must_follow_each_other_from_zero() -> None:
    """Contract section 4.2: the LLM brain is sequential; a skipped or repeated turn is a
    programming error."""
    for bad in (1, -1, True, 1.0, "0", None):
        brain = sup.make_llm_brain(sup.FakeLlm([sup.LINE_A]))
        with pytest.raises(ValueError, match=r"\S"):
            brain.next_line(bad, None)
    engine = sup.FakeLlm([sup.LINE_A, sup.LINE_B])
    brain = sup.make_llm_brain(engine)
    brain.next_line(0, None)
    with pytest.raises(ValueError, match=r"\S"):
        brain.next_line(0, None)
    with pytest.raises(ValueError, match=r"\S"):
        brain.next_line(2, None)
    assert brain.next_line(1, None).line == sup.LINE_B


def test_agent_text_must_be_text_or_none_and_is_never_quoted() -> None:
    """Error hygiene: a non-text agent text is a TypeError that quotes nothing and changes no
    state."""
    brain = sup.make_llm_brain(sup.FakeLlm([sup.LINE_A]))
    for bad in (b"x", 5, ["x"], bytearray(b"x")):
        with pytest.raises(TypeError):
            brain.next_line(0, bad)
    with pytest.raises(TypeError) as excinfo:
        brain.next_line(0, sup.CANARY_REFUSED.encode("ascii"))
    assert sup.CANARY_REFUSED not in str(excinfo.value)
    assert brain.next_line(0, None).line == sup.LINE_A, "a refused call changes no state"


# --------------------------------------------------------------------- usage (ADR 0024 R1)


def test_usage_of_every_answered_request_is_handed_out_before_the_next_request() -> None:
    """Plain engine: the brain passes each record of an outcome to ``on_usage`` before it sends
    the next request, the regeneration included. Streaming engine: the engine delivers, the
    brain does not deliver again."""
    for engine_class, streaming in ((sup.FakeLlm, False), (sup.StreamingFakeLlm, True)):
        order: list[str] = []
        engine = engine_class(["latin", sup.LINE_A, sup.LINE_B])
        original = engine.complete

        def spy(
            request: Any,
            context: Any,
            *,
            on_usage: Any = None,
            _original: Any = original,
            _streaming: bool = streaming,
            _order: list[str] = order,
        ) -> Any:
            _order.append(f"request {request.index}")
            if _streaming:
                return _original(request, context, on_usage=on_usage)
            return _original(request, context)

        engine.complete = spy
        records: list[Any] = []

        def sink(record: Any, _order: list[str] = order, _records: list[Any] = records) -> None:
            _order.append("usage")
            _records.append(record)

        brain = sup.make_llm_brain(engine, on_usage=sink)
        brain.next_line(0, "a")
        brain.next_line(1, "b")
        assert order == [
            "request 0",
            "usage",
            "request 1",
            "usage",
            "request 2",
            "usage",
        ], (streaming, order)
        assert len(records) == 3
        assert all(r.stage == "sim_caller_llm" for r in records)


def test_a_sink_that_raises_propagates_before_the_next_request() -> None:
    """ADR 0024 rule 16 (d): a failing usage sink stops the call before the regeneration is sent."""
    engine = sup.FakeLlm(["latin", sup.LINE_A])

    def sink(record: Any) -> None:
        raise KeyboardInterrupt

    brain = sup.make_llm_brain(engine, on_usage=sink)
    with pytest.raises(KeyboardInterrupt):
        brain.next_line(0, None)
    assert len(engine.requests) == 1, "the regeneration was not sent"


# ------------------------------------------------------------------------- the profile block


def test_the_profile_block_of_the_llm_brain_validates_for_every_optional_value() -> None:
    """ADR 0027: the block validates for a null or set seed and effort and holds no text of the
    run."""
    for seed, effort in ((None, None), (0, "high"), (2**53 - 1, "low")):
        block = sup.make_llm_brain(
            sup.FakeLlm([]), seed=seed, reasoning_effort=effort
        ).profile_block()
        assert sup.profile_block_problems(block) == [], (seed, effort)
        assert block["seed"] == seed
        assert block["reasoning_effort"] == effort
        assert block["mode"] == "llm"
        assert block["max_regenerations"] == 1
        assert set(block) == set(sup.profile_schema()["oneOf"][1]["properties"])
    block = sup.make_llm_brain(
        sup.FakeLlm([]), provider="sarvam", model="sarvam-105b"
    ).profile_block()
    assert (block["provider"], block["model"]) == ("sarvam", "sarvam-105b")
    # the block holds no text of the run: only machine values
    text = repr(block)
    for canary in sup.ALL_CANARIES:
        assert canary not in text
    assert "Asha" not in text
    assert "bill" not in text
