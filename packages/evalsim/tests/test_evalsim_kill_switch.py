# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (kill switch): the file that ends a call, in the runner (ADR 0026 rules 8 and 17).

Contract: docs/phase4/api-surface-batch-g.md sections 3 and 4. A file named by a constant of
``tracer-evalsim`` in ``--out`` is checked before the connection and before every frame: its
appearance hangs up within one 20 ms frame of the injected clock with the end reason
``kill_switch``. The runner never touches the file system (its docstring: no file); it is given a
poll, a callable, and the command builds that poll from ``guard.kill_switch_present``. The
cases run on the virtual clock against the fake agents: no socket, no wall clock, same numbers on
every run. Instants are in milliseconds after the origin of the call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_agents import Silence, Speak, Wait  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_g import FRAME_NS, mod, play_g  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import dialect_of, lines  # type: ignore[import-not-found,unused-ignore]

FRAME_MS = FRAME_NS // 1_000_000


# ------------------------------------------------------------------------------ the probe


def test_the_kill_switch_file_name_is_a_plain_constant_of_the_package() -> None:
    """The run book names the file: a constant, a plain relative name, not a path."""
    guard = mod("guard")
    assert guard.KILL_SWITCH_FILE_NAME == "kill-switch"
    assert guard.CALL_LOCK_FILE_NAME == "call.lock"
    for name in (guard.KILL_SWITCH_FILE_NAME, guard.CALL_LOCK_FILE_NAME):
        assert name.isascii()
        assert "/" not in name
        assert "\\" not in name
        assert name not in (".", "..")


def test_the_probe_sees_any_entry_of_that_name_and_follows_nothing(tmp_path: Path) -> None:
    """Existence is the signal: a file, an empty file, a directory or a dangling link all stop the
    call; the probe never opens or follows it, and an absent output directory is simply not a
    kill switch (nothing was written yet)."""
    guard = mod("guard")
    out = tmp_path / "out"
    assert guard.kill_switch_present(out) is False  # the directory does not exist yet
    out.mkdir(mode=0o700)
    assert guard.kill_switch_present(out) is False
    name = guard.KILL_SWITCH_FILE_NAME
    (out / name).write_bytes(b"")
    assert guard.kill_switch_present(out) is True
    (out / name).unlink()
    (out / name).mkdir()
    assert guard.kill_switch_present(out) is True
    (out / name).rmdir()
    os.symlink(tmp_path / "nowhere", out / name)  # dangling: nothing to follow to
    assert guard.kill_switch_present(out) is True
    assert guard.kill_switch_present(str(out)) is True  # a text path works too
    (out / name).unlink()
    (out / "KILL").write_bytes(b"")  # another name is not the switch
    (out / (name + ".txt")).write_bytes(b"")
    assert guard.kill_switch_present(out) is False


def test_a_probe_that_cannot_tell_says_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never assume: when the file system cannot answer (an error other than 'no such entry'), the
    call must stop, so the answer is True. A missing entry or a missing directory is False."""
    guard = mod("guard")
    out = tmp_path / "out"
    out.mkdir()
    real = os.lstat

    def denied(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path).endswith(guard.KILL_SWITCH_FILE_NAME):
            raise PermissionError(13, "denied")
        return real(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "lstat", denied)
        assert guard.kill_switch_present(out) is True


# ------------------------------------------------------------------------- in the call loop


@pytest.mark.parametrize("kind", ["protobuf", "twilio"])
@pytest.mark.parametrize("switch_ms", [1, 19, 20, 21, 39, 90, 100, 101])
def test_a_switch_seen_mid_line_stops_before_the_next_frame_within_one_frame(
    kind: str, switch_ms: int
) -> None:
    """The line is 30 frames (0 to 580 ms). The switch turns on ``switch_ms`` after the connection:
    the poll before each frame sees it, so the first frame whose slot is at or after the instant is
    never sent and the hang-up is at that slot, less than one frame after the switch. Frame k is
    sent iff k*20 < switch_ms, so ``ceil(switch_ms / 20)`` frames are sent."""
    played, switch = play_g(
        kind,
        [Silence(100)],
        lines(dialect_of(kind), 30),
        switch_ms=switch_ms,
        max_silence_ms=60_000,
    )
    result = played.result
    sent = -(-switch_ms // FRAME_MS)
    assert played.caller_ms() == [20 * k for k in range(sent)]
    assert result.end_reason == "kill_switch"
    assert result.call_established is True
    end_ms = played.at(result.observed_until_ns)
    assert switch_ms <= end_ms < switch_ms + FRAME_MS
    (turn,) = result.turns
    assert turn.complete is False
    assert turn.frames_sent == sent
    assert result.counts["signals_received"] == 0  # a file is not a signal
    assert result.watchdog_fired is False
    assert result.failure_code is None
    assert switch.calls >= sent  # asked at least once per frame sent


def test_a_switch_seen_while_the_caller_waits_for_a_silent_agent_is_seen_within_one_frame() -> None:
    """Nothing wakes the runner while a silent agent is awaited (no frame arrives), so the poll
    must run on its own tick: the line ends at 100 ms, the agent says nothing for 10 s, the switch
    turns on at 2,010 ms and the call is over within one frame, not at ``max_silence``."""
    played, switch = play_g(
        "protobuf", [Wait(10_000)], lines(dialect_of("protobuf"), 5), switch_ms=2010,
        max_silence_ms=60_000,
    )  # fmt: skip
    result = played.result
    assert result.end_reason == "kill_switch"
    end_ms = played.at(result.observed_until_ns)
    assert 2010 <= end_ms < 2010 + FRAME_MS
    assert played.caller_ms() == [0, 20, 40, 60, 80]
    (turn,) = result.turns
    assert turn.complete is True
    # asked about once per frame period while waiting, not once per millisecond and not never
    assert 80 <= switch.calls <= 400


def test_a_switch_seen_while_the_agent_is_talking_ends_the_call_at_its_next_frame() -> None:
    """The agent speaks 10 frames from 400 ms (arrivals 400 to 580): the switch at 450 ms ends the
    call at the next wake, which is an agent frame or a tick, within one frame of 450."""
    played, _ = play_g(
        "protobuf", [Wait(400), Speak(10)], lines(dialect_of("protobuf"), 10, 10), switch_ms=450
    )
    result = played.result
    assert result.end_reason == "kill_switch"
    end_ms = played.at(result.observed_until_ns)
    assert 450 <= end_ms < 450 + FRAME_MS
    assert len(result.turns) == 1


def test_a_switch_present_before_the_connection_never_connects() -> None:
    """ADR 0026 rule 17: checked before the connection. Present at the start, the connector is
    never called, the call is not established and ends ``kill_switch`` (the same record as a
    signal before the connection)."""
    played, switch = play_g(
        "protobuf", [], lines(dialect_of("protobuf"), 10), switch_ms=0, agent_runs=False
    )
    result = played.result
    assert result.end_reason == "kill_switch"
    assert result.call_established is False
    assert played.client is None
    assert result.caller_frames == ()
    assert result.turns == ()
    assert switch.calls >= 1


def test_a_switch_that_turns_on_while_connecting_is_seen_as_soon_as_the_call_is_connected() -> None:
    played, _ = play_g(
        "protobuf", [Silence(100)], lines(dialect_of("protobuf"), 10), switch_ms=10, connect_ms=50,
    )  # fmt: skip
    result = played.result
    assert result.end_reason == "kill_switch"
    assert result.call_established is True
    assert result.turns == ()
    assert played.at(result.observed_until_ns) == 50  # the origin is the connection; at once


def test_without_a_switch_the_calls_are_unchanged() -> None:
    """The seam is optional: with no poll (None) and with a poll that never turns on, the base call
    of the runner tests is the same call (the arithmetic is in ``test_evalsim_runner``)."""
    steps = [Wait(400), Speak(10), Wait(500), Speak(5)]
    dialect = dialect_of("protobuf")
    without, _ = play_g("protobuf", steps, lines(dialect, 10, 10), use_switch=False)
    never, switch = play_g("protobuf", steps, lines(dialect, 10, 10), switch_ms=None)
    for played in (without, never):
        assert played.result.end_reason == "script_complete"
        assert played.at(played.result.observed_until_ns) == 1480
        assert played.caller_ms()[:10] == [20 * k for k in range(10)]
        assert played.caller_ms()[10:] == [880 + 20 * k for k in range(10)]
    assert switch.calls > 0
    assert without.agent_ms() == never.agent_ms()


def test_the_hang_up_is_sent_in_the_dialect_after_a_switch() -> None:
    """ADR 0026 rule 17 'hang up': Twilio sends ``stop`` last, the protobuf dialect closes the
    connection with code 1000 and nothing more is sent after the decision."""
    twilio, _ = play_g("twilio", [Silence(100)], lines(dialect_of("twilio"), 30), switch_ms=90)
    events = [json.loads(m)["event"] for m in twilio.client.sent]
    assert events[-1] == "stop"
    assert events.count("media") == 5
    proto, _ = play_g("protobuf", [Silence(100)], lines(dialect_of("protobuf"), 30), switch_ms=90)
    assert proto.client.calls[-1] == ("close", 1000)
    assert len([c for c in proto.client.calls if c[0] == "close"]) == 1
