# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-369 (batch voice): the thirteenth end reason, ``voice_failed``, in the pure records.

Contract: docs/phase4/api-surface-batch-voice.md section 2. A failure of the caller's speech
synthesis during a call (a 401, a 500, a transport error, a reply that is not audio) used to end the
call as ``transport_error`` with exit 0 and no caller turn, which reads as a successful test run,
the same defect class as BL-338. The new end reason says what happened. Pinned here: the closed list
(thirteen, ``voice_failed`` last), the call state machine (requestable only once the call was
established, the first reason wins), and ``tracer.sim_outcome/1`` (the schema enum and the
cross-field rules: it needs an established call, an LLM caller and no cap).
"""

from __future__ import annotations

from typing import Any

import pytest

from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    PATHS,
    callstate_mod,
    drive,
)
from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    AUTH_TEXT,
    counts,
    mod,
    outcome_document,
)

TWELVE = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
)
CONNECTED_STATES = ("connected", "caller_speaking", "awaiting_agent", "agent_speaking")


def so() -> Any:
    return mod("simoutcome")


def code_of(document: Any, **options: Any) -> str:
    with pytest.raises(so().SimOutcomeError) as caught:
        so().validate_sim_outcome(document, **options)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def test_the_end_reasons_are_the_twelve_of_batch_g_and_voice_failed_last() -> None:
    """ADR 0026 rule 9 (amended): exactly thirteen, in this order. ``voice_failed`` is appended, so
    the first twelve keep their place; ``BRAIN_END_REASONS`` (what a brain DECIDES) is unchanged
    because a failure of the voice is an exception of the engine, not a decision of the brain."""
    types = mod("simtypes")
    assert tuple(types.END_REASONS) == (*TWELVE, "voice_failed")
    assert len(types.END_REASONS) == len(set(types.END_REASONS)) == 13
    assert "voice_failed" not in mod("brain").BRAIN_END_REASONS
    assert set(mod("brain").BRAIN_END_REASONS) == {
        "script_complete",
        "caller_end_call",
        "max_turns",
        "brain_invalid_output",
        "spend_cap",
    }


def test_the_call_machine_takes_voice_failed_from_every_connected_state() -> None:
    """The reason is requestable in every state of an established call, ends in ``ended`` with
    that reason and ``call_established``; it is refused before the connection exists (a line is
    synthesised only once the call is up); and the first end reason wins over a later one, in
    both directions with ``brain_failed``."""
    cs = callstate_mod()
    assert "voice_failed" in cs.REQUESTABLE_END_REASONS
    assert "voice_failed" not in cs.PRECONNECT_END_REASONS
    for state in CONNECTED_STATES:
        machine = drive(cs.CallMachine(), PATHS[state])
        machine.apply("end_requested", "voice_failed")
        assert machine.state == "ending"
        machine.apply("hangup_complete")
        assert (machine.state, machine.end_reason) == ("ended", "voice_failed"), state
        assert machine.call_established is True
    for state in ("preflight", "connecting"):
        machine = drive(cs.CallMachine(), PATHS[state])
        with pytest.raises(cs.CallStateError) as error:
            machine.apply("end_requested", "voice_failed")
        assert str(error.value) == "end_reason_not_allowed_before_connect"
        assert machine.end_reason is None
    machine = drive(cs.CallMachine(), PATHS["agent_speaking"])
    machine.apply("end_requested", "agent_hangup")
    step = machine.apply("end_requested", "voice_failed")
    assert step.changed is False
    assert machine.end_reason == "agent_hangup"
    first = drive(cs.CallMachine(), PATHS["awaiting_agent"])
    first.apply("end_requested", "voice_failed")
    assert first.apply("end_requested", "brain_failed").changed is False
    assert first.apply("end_requested", "kill_switch").changed is False
    assert first.end_reason == "voice_failed"
    other = drive(cs.CallMachine(), PATHS["awaiting_agent"])
    other.apply("end_requested", "brain_failed")
    assert other.apply("end_requested", "voice_failed").changed is False
    assert other.end_reason == "brain_failed"


def test_the_outcome_schema_lists_thirteen_end_reasons_in_the_same_order() -> None:
    """ADR 0027 (amended) and ``simtypes`` agree, member by member (the ADR block is compared with
    the shipped schema by ``test_shipped_sim_outcome_schema_equals_adr0027_block``)."""
    schema = so().SIM_OUTCOME_JSON_SCHEMA
    assert schema["properties"]["end_reason"]["enum"] == [*TWELVE, "voice_failed"]
    assert list(mod("simtypes").END_REASONS) == schema["properties"]["end_reason"]["enum"]


def test_an_llm_call_that_lost_its_voice_is_a_valid_outcome() -> None:
    """The record of BL-369: established, one end reason ``voice_failed``, no cap, the text origin
    ``llm``, the brain request that preceded the failed synthesis counted in ``brain_requests``
    and ``llm_requests``."""
    document = outcome_document(
        end_reason="voice_failed", mode="llm", brain_requests=2, llm_requests=2
    )
    assert document["end_reason"] == "voice_failed"
    assert document["call_established"] is True
    assert document["cap_reached"] is None
    assert document["outcome"] == "not_evaluated"
    so().validate_sim_outcome(document, brain_mode="llm")
    # a call whose very first line could not be spoken has no caller turn at all
    first = so().build_sim_outcome(
        dialect="pipecat-protobuf/1",
        call_established=True,
        end_reason="voice_failed",
        cap_reached=None,
        connected_ms=0,
        authorisation_reference_sha256=so().authorisation_reference_sha256(AUTH_TEXT),
        caller_text_origin="llm",
        counts=counts(brain_requests=1, llm_requests=1),
        brain_mode="llm",
    )
    assert first["counts"]["caller_turns_started"] == 0
    assert first["end_reason"] == "voice_failed"


@pytest.mark.parametrize("mode", ["scripted", "replay"])
def test_voice_failed_needs_an_llm_caller(mode: str) -> None:
    """A scripted or replayed caller speaks audio that was synthesised BEFORE the call (ADR 0026
    rule 14 step 7), so its voice cannot fail during the call; a record of such a call that claims
    ``voice_failed`` is a defect in the record (never assume):
    ``voice_failed_without_llm_caller``."""
    document = outcome_document(end_reason="voice_failed", mode="llm", brain_requests=1)
    document["caller_text_origin"] = mode
    if mode == "scripted":
        document["counts"] = counts(caller_turns_started=0)
    assert code_of(document) == "voice_failed_without_llm_caller"


def test_voice_failed_needs_an_established_call_and_no_cap() -> None:
    """Existing cross-field rules apply unchanged: a call that was not established ends
    ``setup_failed`` or ``kill_switch`` only; a cap is named by ``spend_cap`` only."""
    document = outcome_document(end_reason="voice_failed", mode="llm", brain_requests=1)
    assert code_of({**document, "call_established": False, "connected_ms": None}) == (
        "not_established_inconsistent"
    )
    assert code_of({**document, "cap_reached": "tts_chars"}) == "cap_reached_inconsistent"


def test_the_two_failed_caller_reasons_are_two_reasons_not_one() -> None:
    """Rejected: generalising ``brain_failed`` to a caller-wide reason. Both are valid, both are
    kept apart in the record, and the message of the new cross-field rule names its own reason."""
    brain = outcome_document(end_reason="brain_failed", mode="llm", brain_requests=1)
    voice = outcome_document(end_reason="voice_failed", mode="llm", brain_requests=1)
    so().validate_sim_outcome(brain)
    so().validate_sim_outcome(voice)
    assert brain["end_reason"] != voice["end_reason"]
    scripted_brain = {**brain, "caller_text_origin": "replay"}
    scripted_voice = {**voice, "caller_text_origin": "replay"}
    assert code_of(scripted_brain) == "brain_failed_without_llm_caller"
    assert code_of(scripted_voice) == "voice_failed_without_llm_caller"


def test_the_closed_list_refuses_a_fourteenth_reason() -> None:
    """A thirteen-member list is closed: ``brain_failed`` and ``transport_error`` are still valid,
    a made-up next one is a schema fault."""
    for reason in ("transport_error", "brain_failed"):
        so().validate_sim_outcome(outcome_document(end_reason=reason, mode="llm"))
    document = outcome_document(end_reason="voice_failed", mode="llm")
    assert code_of({**document, "end_reason": "caller_failed"}).startswith("schema:")
    assert code_of({**document, "end_reason": "voice_broke"}).startswith("schema:")
