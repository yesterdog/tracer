# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-04: the call state machine of tracer-evalcore (ADR 0026 rule 9 and invariant I3; contract
docs/phase4/api-surface-batch-a.md section 2).

The expected table is the grid in ``evalcore_batcha_support.GRID``, written by hand from the
contract and shared with no code of ``tracer_evalcore``. The module under test is loaded lazily,
so before it exists every test fails on its own with ``ModuleNotFoundError``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]
from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    ADR26,
    END_REASONS,
    EVENTS,
    GRID,
    PATHS,
    REQUESTABLE,
    STATES,
    callstate_mod,
    drive,
    grid_next,
    ref_reachable,
    simtypes_mod,
)

# Deterministic hypothesis profile (as Phase 1 to 3): derandomised, no deadline.
HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def _machine() -> Any:
    return callstate_mod().CallMachine()


def _legal_reason(state: str, event: str) -> str | None:
    """A reason that the contract accepts for ``event`` in ``state`` (kill_switch always is)."""
    return "kill_switch" if event == "end_requested" else None


# ----------------------------------------------------------------------------------------------
# The SCOPE tests of P4-04
# ----------------------------------------------------------------------------------------------


def test_state_machine_transition_table_exact() -> None:
    cs = callstate_mod()
    # the closed lists
    assert tuple(simtypes_mod().CALL_STATES) == STATES
    assert tuple(cs.CALL_EVENTS) == EVENTS
    assert tuple(simtypes_mod().END_REASONS) == END_REASONS
    assert tuple(cs.REQUESTABLE_END_REASONS) == REQUESTABLE
    assert len(REQUESTABLE) == 12
    assert "setup_failed" not in cs.REQUESTABLE_END_REASONS
    assert tuple(cs.PRECONNECT_END_REASONS) == ("kill_switch",)
    # the ADR names the eight states in this order in rule 9
    rule9 = next(
        body
        for label, body in sup.numbered(
            sup.heading_section(ADR26.read_text("utf-8"), "## Decision"), r"(\d+)\. \*\*"
        )
        if label == "9"
    )
    positions = [rule9.index(f"`{s}`") for s in STATES]
    assert positions == sorted(positions), "ADR 0026 rule 9 lists the states in another order"
    # the table: exactly 8 x 9 cells, each equal to the grid, immutable
    table = cs.TRANSITIONS
    assert set(table) == {(s, e) for s in STATES for e in EVENTS}
    assert len(table) == 72
    for state in STATES:
        for event in EVENTS:
            assert table[(state, event)] == grid_next(state, event), (state, event)
    with pytest.raises(TypeError):
        table[("preflight", "connected")] = "ended"
    with pytest.raises(TypeError):
        del table[("preflight", "connected")]
    # a fresh machine
    machine = cs.CallMachine()
    assert machine.state == "preflight"
    assert machine.end_reason is None
    assert machine.call_established is False
    # the grid itself reaches every state, and the machine follows the grid cell by cell
    assert ref_reachable() == set(STATES)
    for state in STATES:
        assert drive(cs.CallMachine(), PATHS[state]).state == state, state
    for state in STATES:
        for event in EVENTS:
            machine = drive(cs.CallMachine(), PATHS[state])
            before = (machine.state, machine.end_reason, machine.call_established)
            expected = grid_next(state, event)
            reason = _legal_reason(state, event)
            if state in ("preflight", "connecting") and event == "end_requested":
                reason = "kill_switch"
            if expected is None:
                with pytest.raises(cs.CallStateError) as error:
                    machine.apply(event, reason)
                assert error.value.code == "event_not_allowed_in_state"
                assert str(error.value) == "event_not_allowed_in_state"
                assert (machine.state, machine.end_reason, machine.call_established) == before
                continue
            step = machine.apply(event, reason)
            assert (step.source, step.event, step.target) == (state, event, expected)
            assert step.changed is (expected != state)
            assert machine.state == expected


def test_max_duration_is_min_of_evaluator_and_cap() -> None:
    cs = callstate_mod()
    f = cs.effective_max_duration_ms
    cases = [
        # (evaluator ms, cap seconds, expected)
        (60_000, 30, 30_000),
        (60_000, 90, 60_000),
        (60_000, 60, 60_000),
        (1, 1, 1),
        (999, 1, 999),
        (1_001, 1, 1_000),
        (14_400_000, 14_400, 14_400_000),
        (14_400_000, 1, 1_000),
        (1_000, 14_400, 1_000),
        (123_456, 123, 123_000),
        (123_456, 124, 123_456),
    ]
    for evaluator, cap, expected in cases:
        assert f(evaluator, cap) == expected, (evaluator, cap)
    # an absent cap (only legal with fake engines) leaves the evaluator's value
    assert f(77_000, None) == 77_000
    assert f(14_400_000, None) == 14_400_000
    # the bounds are the schema's and the sim profile's
    adr_cap = _profile_limit("max_call_seconds")
    assert (adr_cap["minimum"], adr_cap["maximum"]) == (1, 14_400)
    from tracer_evalschema.evaluator import MAX_DURATION_MS

    assert MAX_DURATION_MS == 14_400_000
    assert f(MAX_DURATION_MS, None) == MAX_DURATION_MS
    # refusals (a stable code, never the value)
    for bad_ms in (0, -1, MAX_DURATION_MS + 1, True, 1.0, "60000", None):
        with pytest.raises(cs.CallStateError) as error:
            f(bad_ms, 30)
        assert str(error.value) == "evaluator_max_duration_invalid"
    for bad_cap in (0, -5, 14_401, True, 5.0, "30"):
        with pytest.raises(cs.CallStateError) as error:
            f(60_000, bad_cap)
        assert str(error.value) == "max_call_seconds_invalid"
    assert issubclass(cs.CallStateError, ValueError)


@given(
    evaluator=st.integers(min_value=1, max_value=14_400_000),
    cap=st.one_of(st.none(), st.integers(min_value=1, max_value=14_400)),
)
@HSETTINGS
def test_max_duration_property_is_the_smaller_of_the_two(evaluator: int, cap: int | None) -> None:
    """The result never exceeds either limit and equals one of them (min, not sum or product)."""
    got = callstate_mod().effective_max_duration_ms(evaluator, cap)
    if cap is None:
        assert got == evaluator
    else:
        assert got == min(evaluator, cap * 1000)
        assert got <= cap * 1000
    assert got <= evaluator


def test_kill_switch_reaches_ended_from_every_state() -> None:
    cs = callstate_mod()
    for state in STATES:
        machine = drive(cs.CallMachine(), PATHS[state])
        assert machine.state == state
        first_reason = machine.end_reason
        machine.apply("end_requested", "kill_switch")
        if state in ("ending", "ended"):
            # a call already ending or ended keeps the reason it has (exactly one end reason)
            assert machine.end_reason == first_reason == "max_duration"
        else:
            assert machine.state == "ending", state
            assert machine.end_reason == "kill_switch"
        if machine.state == "ending":
            machine.apply("hangup_complete")
        assert machine.state == "ended", state
        assert machine.end_reason == first_reason or first_reason is None
        assert machine.end_reason is not None


@given(
    prefix=st.lists(st.tuples(st.sampled_from(EVENTS), st.sampled_from(REQUESTABLE)), max_size=25)
)
@HSETTINGS
def test_kill_switch_reaches_ended_after_any_legal_prefix(
    prefix: list[tuple[str, str]],
) -> None:
    """Whatever legal events came first, a kill switch then a hang-up ends the call in at most two
    events, with one end reason, and the established flag is untouched by them."""
    cs = callstate_mod()
    machine = cs.CallMachine()
    for event, reason in prefix:
        try:
            machine.apply(event, reason if event == "end_requested" else None)
        except cs.CallStateError:
            continue  # a refused event changes nothing; the property is about legal ones
    established = machine.call_established
    steps = 0
    while machine.state != "ended":
        if machine.state == "ending":
            machine.apply("hangup_complete")
        else:
            machine.apply("end_requested", "kill_switch")
        steps += 1
        assert steps <= 2
    assert machine.end_reason is not None
    assert machine.call_established is established


def test_no_event_after_ended() -> None:
    cs = callstate_mod()
    routes = {
        "max_duration": PATHS["ended"],
        "kill_switch": [("end_requested", "kill_switch"), ("hangup_complete", None)],
        "setup_failed": [
            ("connect_started", None),
            ("connect_failed", None),
            ("hangup_complete", None),
        ],
        "agent_hangup": [
            ("connect_started", None),
            ("connected", None),
            ("caller_line_started", None),
            ("end_requested", "agent_hangup"),
            ("hangup_complete", None),
        ],
    }
    for reason, route in routes.items():
        for event in EVENTS:
            for arg in [None] if event != "end_requested" else list(REQUESTABLE):
                machine = drive(cs.CallMachine(), route)
                assert machine.state == "ended"
                assert machine.end_reason == reason
                established = machine.call_established
                step = machine.apply(event, arg)
                assert step.source == "ended"
                assert step.target == "ended"
                assert step.changed is False
                assert machine.state == "ended"
                assert machine.end_reason == reason, "the end reason never changes"
                assert machine.call_established is established


@given(
    tail=st.lists(
        st.tuples(st.sampled_from(EVENTS), st.sampled_from(REQUESTABLE)), min_size=1, max_size=40
    )
)
@HSETTINGS
def test_no_state_is_entered_after_ended(tail: list[tuple[str, str]]) -> None:
    """After the first ``ended`` every later event leaves state, reason and flag as they were."""
    cs = callstate_mod()
    machine = drive(cs.CallMachine(), PATHS["ended"])
    snapshot = (machine.state, machine.end_reason, machine.call_established)
    for event, reason in tail:
        machine.apply(event, reason if event == "end_requested" else None)
        assert (machine.state, machine.end_reason, machine.call_established) == snapshot


def test_setup_failure_is_an_end_reason_not_an_exception() -> None:
    cs = callstate_mod()
    machine = drive(cs.CallMachine(), PATHS["connecting"])
    step = machine.apply("connect_failed")  # must not raise: a failed call is an outcome
    assert (step.source, step.target) == ("connecting", "ending")
    assert machine.end_reason == "setup_failed"
    assert machine.call_established is False
    machine.apply("hangup_complete")
    assert machine.state == "ended"
    assert machine.end_reason == "setup_failed"
    assert machine.call_established is False
    # setup_failed arrives only through connect_failed: never as a requested end reason
    for state in STATES:
        machine = drive(cs.CallMachine(), PATHS[state])
        with pytest.raises(cs.CallStateError) as error:
            machine.apply("end_requested", "setup_failed")
        assert str(error.value) == "end_reason_invalid"
        assert machine.state == state
    # and only while connecting: once the call was established a failure is transport_error
    for state in ("connected", "caller_speaking", "awaiting_agent", "agent_speaking"):
        machine = drive(cs.CallMachine(), PATHS[state])
        with pytest.raises(cs.CallStateError):
            machine.apply("connect_failed")
    # before the connection exists only the kill switch can end the call (ADR 0027: a call that
    # was not established has the end reason setup_failed or kill_switch)
    for state in ("preflight", "connecting"):
        for reason in REQUESTABLE:
            machine = drive(cs.CallMachine(), PATHS[state])
            if reason == "kill_switch":
                machine.apply("end_requested", reason)
                assert machine.end_reason == "kill_switch"
                assert not machine.call_established
            else:
                with pytest.raises(cs.CallStateError) as error:
                    machine.apply("end_requested", reason)
                assert str(error.value) == "end_reason_not_allowed_before_connect"
                assert machine.state == state
                assert machine.end_reason is None
    # a setup failure after the kill switch (the kill came first) keeps the first reason
    machine = drive(cs.CallMachine(), PATHS["connecting"])
    machine.apply("end_requested", "kill_switch")
    machine.apply("connect_failed")
    assert machine.state == "ending"
    assert machine.end_reason == "kill_switch"


# ----------------------------------------------------------------------------------------------
# Extra tests (each with its reason)
# ----------------------------------------------------------------------------------------------


def test_every_end_reason_is_reachable_with_its_own_value() -> None:
    """The thirteen end reasons of ADR 0026 rule 9 are all reachable, each recorded as itself (a
    table that merged two reasons, or lost one, would pass the grid test alone)."""
    cs = callstate_mod()
    seen = set()
    for reason in REQUESTABLE:
        machine = drive(cs.CallMachine(), PATHS["agent_speaking"])
        machine.apply("end_requested", reason)
        machine.apply("hangup_complete")
        assert (machine.state, machine.end_reason) == ("ended", reason)
        assert machine.call_established is True
        seen.add(reason)
    machine = drive(cs.CallMachine(), PATHS["connecting"])
    machine.apply("connect_failed")
    machine.apply("hangup_complete")
    seen.add(machine.end_reason)
    assert seen == set(END_REASONS)
    assert len(seen) == 13


def test_the_first_end_reason_wins() -> None:
    """Exactly one end reason: later requests (a watchdog after a signal) change nothing."""
    cs = callstate_mod()
    machine = drive(cs.CallMachine(), PATHS["agent_speaking"])
    machine.apply("end_requested", "agent_hangup")
    for reason in ("kill_switch", "max_duration", "transport_error"):
        step = machine.apply("end_requested", reason)
        assert step.changed is False
        assert machine.state == "ending"
        assert machine.end_reason == "agent_hangup"
    machine.apply("hangup_complete")
    assert machine.end_reason == "agent_hangup"


def test_late_events_while_ending_are_ignored_but_a_refused_event_changes_nothing() -> None:
    """Frames and line events still arrive while the call hangs up: ignored, not errors. An event
    that cannot happen (a runner defect) raises and leaves the machine exactly as it was."""
    cs = callstate_mod()
    machine = drive(cs.CallMachine(), PATHS["ending"])
    for event in EVENTS:
        if event in ("hangup_complete", "end_requested"):
            continue
        step = machine.apply(event)
        assert (step.source, step.target, step.changed) == ("ending", "ending", False)
    machine = drive(cs.CallMachine(), PATHS["awaiting_agent"])
    for bad in ("connected", "connect_started", "caller_line_finished", "hangup_complete"):
        with pytest.raises(cs.CallStateError):
            machine.apply(bad)
        assert machine.state == "awaiting_agent"
        assert machine.end_reason is None


def test_established_only_after_connected_and_only_once_connecting() -> None:
    """``call_established`` is the fact the outcome document records: true from ``connected`` on,
    never for a call that failed to connect, never turned back to false."""
    cs = callstate_mod()
    machine = cs.CallMachine()
    machine.apply("connect_started")
    assert machine.call_established is False
    machine.apply("connected")
    assert machine.call_established is True
    machine.apply("end_requested", "script_complete")
    machine.apply("hangup_complete")
    assert machine.call_established is True


def test_arguments_are_validated_and_only_the_machine_error_is_raised() -> None:
    """Junk in, ``CallStateError`` out: never a ``KeyError`` or a ``TypeError`` from the table."""
    cs = callstate_mod()
    machine = drive(cs.CallMachine(), PATHS["connected"])
    junk_events = ("", "Connected", "connected ", None, 3, ("connected",), b"connected", "ended")
    for event in junk_events:
        with pytest.raises(cs.CallStateError) as error:
            machine.apply(event)
        assert str(error.value) == "unknown_event"
    for reason in (None, "", "bogus", "setup_failed", "Kill_Switch", 3, True):
        with pytest.raises(cs.CallStateError) as error:
            machine.apply("end_requested", reason)
        assert str(error.value) == "end_reason_invalid"
    for event in EVENTS:
        if event == "end_requested":
            continue
        with pytest.raises(cs.CallStateError) as error:
            machine.apply(event, "kill_switch")
        assert str(error.value) == "reason_not_allowed"
    assert machine.state == "connected"
    assert machine.end_reason is None
    # every message is a code: lower case words, no value quoted
    for code in ("unknown_event", "end_reason_invalid", "reason_not_allowed"):
        assert re.fullmatch(r"[a-z][a-z_]*", code)


@given(
    steps=st.lists(
        st.tuples(st.sampled_from(EVENTS), st.sampled_from([None, *REQUESTABLE])), max_size=40
    )
)
@HSETTINGS
def test_machine_follows_the_reference_model_on_random_event_sequences(
    steps: list[tuple[str, str | None]],
) -> None:
    """Model-based: the machine and a reference (the grid plus the stated reason rules) agree on
    state, end reason, established flag and refusals after every event; and at the end the
    invariants of ADR 0026 rule 9 and ADR 0027 (outcome cross-field rules) hold."""
    cs = callstate_mod()
    machine = cs.CallMachine()
    state, reason_now, established = "preflight", None, False
    for event, reason in steps:
        # the reference
        arg = reason if event == "end_requested" else None
        nxt = grid_next(state, event)
        refuse = (event == "end_requested" and arg is None) or nxt is None
        if not refuse and event == "end_requested" and state in ("preflight", "connecting"):
            refuse = arg != "kill_switch"
        try:
            machine.apply(event, arg)
        except cs.CallStateError:
            assert refuse, (state, event, arg)
            assert machine.state == state
            continue
        assert not refuse, (state, event, arg)
        assert nxt is not None
        if nxt == "ending" and state not in ("ending", "ended"):
            reason_now = "setup_failed" if event == "connect_failed" else arg
        if event == "connected" and state == "connecting":
            established = True
        state = nxt
        assert machine.state == state
        assert machine.end_reason == reason_now
        assert machine.call_established is established
        assert (machine.end_reason is None) == (state not in ("ending", "ended"))
    if machine.state == "ended":
        assert machine.end_reason is not None
        if not machine.call_established:
            assert machine.end_reason in ("setup_failed", "kill_switch")
        if machine.end_reason == "setup_failed":
            assert not machine.call_established


def test_the_table_text_in_the_grid_has_one_row_per_state_and_nine_cells() -> None:
    """Guards the test's own oracle: a typo in the grid would make two wrong things agree."""
    assert set(GRID) == set(STATES)
    for state, row in GRID.items():
        assert len(row.split()) == len(EVENTS), state
        for cell in row.split():
            assert cell == "-" or cell in STATES, (state, cell)


def _profile_limit(key: str) -> dict[str, Any]:
    import json

    section = sup.heading_section(
        sup.ADR27.read_text("utf-8"), "## The `tracer.sim_profile/1` format"
    )
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1
    limits: dict[str, Any] = json.loads(blocks[0])["properties"]["limits"]["properties"][key]
    return limits
