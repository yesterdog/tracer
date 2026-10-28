# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (kill switch): the file ``kill-switch`` in ``--out`` (ADR 0026 rules 14, 17 and 19; the
Q17 text of SCOPE: "a kill-switch file is checked every frame; a watchdog runs independently").

Contract: docs/phase4/api-surface-batch-g.md sections 3, 4 and 7. Present at step 2: the run
is refused (exit 4, ``kill_switch_file_present``) before any key. Appearing later: before the
connection it ends the run as a call that never happened (exit 5, like a signal then); during the
call it hangs up within one 20 ms frame of the injected clock, the end reason is ``kill_switch``,
the run is stored with what was recorded (the partial recording, the usage of every answered
request, a report) and the exit code is the one of a stored, scored call (0): the same as for the
first signal. The tool never removes the file; the operator does. Time is the virtual clock, the
provider a scripted ``MockTransport``, the file a real file in the temporary directory.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    NOW,
    STAMP,
    START_NS,
    EnvRecorder,
    SarvamSim,
    SimRun,
    VirtualClock,
    mod,
    real_args,
    real_env,
    real_files,
    run_sim,
    tree,
    write_inputs,
)
from cli_p4g_support import KILL_SWITCH_NAME, kill_at  # type: ignore[import-not-found,unused-ignore]

FRAME_MS = 20


def killed_call(root: Path, fire_ms: int) -> tuple[SimRun, SarvamSim, list[int], Any]:
    """The matrix run of the signal (two paid lines, one transcription), with the file created
    ``fire_ms`` after the connection instead of a signal."""
    files = real_files(root)
    clock, sim = VirtualClock(), SarvamSim()
    created: list[int] = []
    run = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ=real_env(),
        transport=sim,
        clock=clock,
        world_kw={"connect": kill_at(clock, files, fire_ms, created)},
    )
    return run, sim, created, files


def active_ms(path: Path) -> tuple[int, int]:
    """Milliseconds of non-zero samples on the left (caller) and right (agent) channel."""
    with wave.open(str(path)) as reader:
        assert (reader.getnchannels(), reader.getframerate()) == (2, 16000)
        data = reader.readframes(reader.getnframes())
    samples = [
        int.from_bytes(data[i : i + 2], "little", signed=True) for i in range(0, len(data), 2)
    ]
    left, right = samples[0::2], samples[1::2]
    return sum(1 for x in left if x) // 16, sum(1 for x in right if x) // 16


@pytest.mark.parametrize(
    ("fire_ms", "started", "completed", "left_ms"),
    [
        # in the wait for the agent's silence (the first reply ends at 580, the second line would
        # start at 880): seen on the tick of the wait
        (700, 1, 1, 100),
        # in the middle of the second line (frames at 880, 900, 920, 940, 960): the file appears at
        # 905, the poll before the frame at 920 sees it, so two of its five frames are sent
        (905, 2, 1, 140),
    ],
    ids=["while-waiting", "mid-line"],
)
def test_kill_switch_file_hangs_up_within_one_frame(
    tmp_path: Path, fire_ms: int, started: int, completed: int, left_ms: int
) -> None:
    """ADR 0026 rule 17: the file is checked before every frame; its appearance hangs up within
    one frame of the injected clock with the end reason ``kill_switch``. The partial recording and
    the usage of every answered request are kept in the stored run (two lines paid for, one
    transcription), the report is written, no recovery file is needed (nothing failed), the file is
    left where it is, and the exit code is the one of a stored call (0), as for a signal."""
    run, sim, created, files = killed_call(tmp_path, fire_ms)
    assert (created[0] - START_NS) // 1_000_000 == fire_ms
    assert run.code == 0, (run.out, run.err)
    assert run.err == ""
    assert run.value("end reason") == "kill_switch"
    assert run.value("result") == "call established"
    bundle = run.bundle()
    outcome = bundle.run.metadata["tracer.sim_outcome"]
    assert outcome["end_reason"] == "kill_switch"
    assert outcome["call_established"] is True
    assert fire_ms <= outcome["connected_ms"] < fire_ms + FRAME_MS  # within one frame
    assert outcome["counts"]["signals_received"] == 0  # a file is not a signal
    assert outcome["counts"]["caller_turns_started"] == started
    assert outcome["counts"]["caller_turns_completed"] == completed
    assert outcome["cap_reached"] is None
    # what existed is kept
    assert [u.stage for u in bundle.usage_records] == ["sim_caller_tts", "sim_caller_tts", "stt"]
    assert len(sim.bodies("/text-to-speech")) == 2
    assert sim.paths.count("/speech-to-text") == 1
    (wav,) = list(files.out.glob("recording-*.wav"))
    assert active_ms(wav) == (left_ms, 200)
    assert list(files.out.glob("report-*.html"))
    assert (files.out / "tracer.sqlite").is_file()
    assert run.value("tts usage") == "2 requests, 14 characters"
    assert run.value("stt usage") == "1 requests, 0.2 audio seconds"
    assert list(files.out.glob("usage-recovery-*.json")) == []
    assert (files.out / KILL_SWITCH_NAME).is_file()  # the operator removes it, the tool never does
    # the call log ends with the reason
    log = (files.out / "sim_call_log.jsonl").read_text("ascii").splitlines()
    assert '"end_reason":"kill_switch"' in log[-1]


def test_a_killed_call_whose_store_write_fails_keeps_the_usage_in_a_recovery_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill switch and the usage rule together (ADR 0024 rule 16): the killed call was paid for;
    if the store then fails, the usage of the answered requests is in ``usage-recovery-<UTC>.json``
    and the last line of the output names it."""
    import json

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(mod("store"), "store_run", broken)
    run, _, _, files = killed_call(tmp_path, 700)
    assert run.code == 1
    (recovery,) = list(files.out.glob("usage-recovery-*.json"))
    assert run.out.splitlines()[-1] == f"usage recovery: {recovery}"
    document = json.loads(recovery.read_text("ascii"))
    assert [u["stage"] for u in document["usage_records"]] == [
        "sim_caller_tts",
        "sim_caller_tts",
        "stt",
    ]
    assert "disk full" not in recovery.read_text("ascii") + run.out + run.err
    assert list(files.out.glob("recording-*.wav"))


# --------------------------------------------------------------------- before the connection


def put_kill_switch(root: Path) -> Path:
    out = root / "out"
    out.mkdir(mode=0o700, exist_ok=True)
    path = out / KILL_SWITCH_NAME
    path.write_bytes(b"")
    return Path(path)


@pytest.mark.parametrize("target", [True, False], ids=["target", "fake-agent"])
def test_a_kill_switch_file_present_at_the_start_is_refused_before_any_key(
    tmp_path: Path, target: bool
) -> None:
    """ADR 0026 rule 17: 'checked before connect (present: refused, exit 4)', at step 2 with the
    allowlist and the interval: no key is read, nothing is sent, nothing is added to the output
    directory and the refusal is a code."""
    files = real_files(tmp_path)
    put_kill_switch(tmp_path)
    before = tree(files.out)
    env = EnvRecorder({"SARVAM_API_KEY": KEY_CANARY})
    sim = SarvamSim()
    run = run_sim(
        files, args=real_args(files, target=target), target=target, environ=env, transport=sim,
    )  # fmt: skip
    assert run.code == 4, (run.out, run.err)
    assert run.err == "error: kill_switch_file_present\n"
    assert run.out == ""
    assert run.trace == ["parse_inputs", "allowlist"]
    assert [r for r in env.reads if r == "SARVAM_API_KEY"] == []
    assert sim.requests == []
    assert tree(files.out) == before
    assert str(tmp_path) not in run.err


def test_a_dry_run_ignores_the_kill_switch_file(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    put_kill_switch(tmp_path)
    run = run_sim(files, add=["--dry-run"], drop=["--authorisation-reference"])
    assert run.code == 0, (run.out, run.err)
    assert run.value("dry run") == "nothing was sent"


def test_a_file_that_appears_after_step_2_and_before_the_connection_ends_a_call_never_made(
    tmp_path: Path,
) -> None:
    """A race the step-2 check cannot close: the operator creates the file while the lines are
    being paid for. The runner checks once more before it connects: the connector is not called,
    the end reason is ``kill_switch``, the call was not established (exit 5, as for a signal at
    that moment), the run is stored with the usage already paid, every result is
    ``not_applicable``."""
    files = real_files(tmp_path)
    connected: list[str] = []

    def watch(step: str) -> None:
        if step == "open_store":
            put_kill_switch(tmp_path)

    async def connect(request: Any) -> Any:
        connected.append("connect")
        raise AssertionError("the connection was made")

    sim = SarvamSim()
    run = run_sim(
        files, args=real_args(files, target=True), target=True, environ=real_env(), transport=sim,
        world_kw={"trace": watch, "connect": connect},
    )  # fmt: skip
    assert connected == []
    assert run.code == 5, (run.out, run.err)
    assert run.value("end reason") == "kill_switch"
    assert run.value("result") == "call not established"
    bundle = run.bundle()
    assert bundle.run.metadata["tracer.sim_outcome"]["call_established"] is False
    assert all(r.status == "not_applicable" for r in bundle.results)
    assert [u.stage for u in bundle.usage_records] == ["sim_caller_tts", "sim_caller_tts"]
    assert len(sim.bodies("/text-to-speech")) == 2


def test_the_file_stays_and_stops_the_next_run_until_the_operator_removes_it(
    tmp_path: Path,
) -> None:
    run, _, _, files = killed_call(tmp_path / "first", 700)
    assert run.code == 0
    kill = files.out / KILL_SWITCH_NAME
    assert kill.is_file()
    again = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ=real_env(),
        transport=SarvamSim(),
        instant=NOW.replace(second=30),
    )
    assert (again.code, again.err) == (4, "error: kill_switch_file_present\n")
    kill.unlink()
    after = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ=real_env(),
        transport=SarvamSim(),
        instant=NOW.replace(second=40),
    )
    assert after.code == 0, (after.out, after.err)


def test_a_link_with_the_name_of_the_switch_is_a_kill_switch_and_is_not_followed(
    tmp_path: Path,
) -> None:
    """Existence is the signal: a dangling link stops the run too; nothing is read through it."""
    import os

    files = real_files(tmp_path)
    files.out.mkdir(mode=0o700)
    os.symlink(tmp_path / "nowhere", files.out / KILL_SWITCH_NAME)
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert (run.code, run.err) == (4, "error: kill_switch_file_present\n")
    assert not (tmp_path / "nowhere").exists()


def test_the_report_and_the_names_of_a_killed_call_are_the_usual_ones(tmp_path: Path) -> None:
    run, _, _, files = killed_call(tmp_path, 700)
    names = sorted(p.name for p in files.out.iterdir())
    assert names == sorted(
        [
            KILL_SWITCH_NAME,
            "call.lock",
            f"recording-{STAMP}.wav",
            f"report-{STAMP}.html",
            "sim_call_log.jsonl",
            "tracer.sqlite",
            "tts-cache",
        ]
    )
    assert "kill-switch" not in run.out + run.err
