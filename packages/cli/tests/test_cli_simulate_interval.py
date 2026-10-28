# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (one call per invocation, minimum interval): the lock file of ``tracer simulate`` (ADR 0026
rules 14 and 18; ``limits.min_call_interval_ms`` of the sim profile, an owner value with no default
in code; the owner pre-registered 3 s for the first real runs).

Contract: docs/phase4/api-surface-batch-g.md sections 3 and 7. A target run claims
``call.lock`` in ``--out`` at the connect (the instants of the connect, mode 0600, never through a
link, exclusive for the whole invocation); an earlier call than the interval after the last one is
refused at step 2 (exit 4, ``min_call_interval_not_elapsed``) before the authorisation, any key and
any connection. Time is injected: the command's clock (the UTC instant) and the virtual clock of
the runner (the monotonic instant); nothing sleeps. The provider is a scripted ``MockTransport``,
the connector a recorder.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    NOW,
    START_NS,
    EnvRecorder,
    Files,
    SarvamSim,
    SimRun,
    VirtualClock,
    base_args,
    fake_connect,
    profile_doc,
    real_args,
    run_sim,
    sim_mod,
    tree,
    write_inputs,
)
from cli_p4g_support import LOCK_NAME  # type: ignore[import-not-found,unused-ignore]

LOCK_RECORD = (
    '{"format":"tracer.call_lock/1","monotonic_ns":5000000000,'
    '"utc":"2026-10-27T12:00:00.000000Z"}\n'
)


def files_for(root: Path, interval_ms: int, **changes: Any) -> Files:
    return write_inputs(root, sim_profile=profile_doc(min_call_interval_ms=interval_ms, **changes))


class Connector:
    """A connector that counts how often it was asked and answers with the in-process agent."""

    def __init__(self, files: Files, clock: VirtualClock) -> None:
        self.calls = 0
        self._inner = fake_connect(files, clock)

    async def __call__(self, request: Any) -> Any:
        self.calls += 1
        return await self._inner(request)


def go(files: Files, *, at: Any = NOW, **extra: Any) -> tuple[SimRun, Connector]:
    clock = VirtualClock()
    connector = Connector(files, clock)
    run = run_sim(
        files, target=True, instant=at, clock=clock, world_kw={"connect": connector}, **extra
    )
    return run, connector


def lock(files: Files) -> Path:
    return Path(files.out / LOCK_NAME)


def connects_in_log(files: Files) -> int:
    path = files.out / "sim_call_log.jsonl"
    events = [json.loads(line)["event"] for line in path.read_text("ascii").splitlines()]
    return events.count("connect")


def test_one_call_per_invocation_and_min_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0026 rule 18, end to end with the interval the owner pre-registered (3,000 ms): one
    invocation connects exactly once and records the instants of the connect in a private lock
    file; the next invocation on the same output directory 2,999 ms later is refused (exit 4, one
    ``error:`` line with a code, nothing on stdout, no connection, nothing changed on disk, no
    waiting); exactly 3,000 ms later it connects again and the record moves on."""

    def no_sleep(seconds: float) -> None:
        raise AssertionError("the command slept")

    monkeypatch.setattr(time, "sleep", no_sleep)
    files = files_for(tmp_path, 3000)
    first, first_connector = go(files, at=NOW)
    assert first.code == 0, (first.out, first.err)
    assert first_connector.calls == 1  # one call per invocation
    assert connects_in_log(files) == 1
    info = os.lstat(lock(files))
    assert stat.S_ISREG(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert lock(files).read_text("ascii") == LOCK_RECORD  # UTC instant and the runner's clock
    assert START_NS == 5_000_000_000

    before = tree(files.out)
    soon, soon_connector = go(files, at=NOW + timedelta(milliseconds=2999))
    assert soon.code == 4, (soon.out, soon.err)
    assert soon.err == "error: min_call_interval_not_elapsed\n"
    assert soon.out == ""
    assert soon_connector.calls == 0
    assert tree(files.out) == before  # not a byte changed, not a file added
    assert soon.trace == ["parse_inputs", "allowlist"]
    for text in (soon.err, soon.out):
        assert LOCK_NAME not in text
        assert str(tmp_path) not in text

    later, later_connector = go(files, at=NOW + timedelta(milliseconds=3000))
    assert later.code == 0, (later.out, later.err)
    assert later_connector.calls == 1
    assert connects_in_log(files) == 2
    assert json.loads(lock(files).read_text("ascii")) == {
        "format": "tracer.call_lock/1",
        "monotonic_ns": 5_000_000_000,
        "utc": "2026-10-27T12:00:03.000000Z",
    }
    assert stat.S_IMODE(os.lstat(lock(files)).st_mode) == 0o600
    # the two accepted runs are two bundles of one store
    assert len(list(files.out.glob("report-*.html"))) == 2


def test_an_interval_of_zero_never_refuses_but_still_records(tmp_path: Path) -> None:
    files = files_for(tmp_path, 0)
    first, _ = go(files, at=NOW)
    second, connector = go(files, at=NOW + timedelta(seconds=1))
    assert (first.code, second.code) == (0, 0)
    assert connector.calls == 1
    assert lock(files).read_text("ascii").endswith('"utc":"2026-10-27T12:00:01.000000Z"}\n')


def test_a_failed_connect_counts_as_a_call(tmp_path: Path) -> None:
    """The record is written before the connect: a loop of refused connections is as much a dialler
    as a loop of calls, so the next attempt is held to the interval too."""
    files = files_for(tmp_path, 3000)

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    run = run_sim(files, target=True, instant=NOW, world_kw={"connect": refuse})
    assert run.code == 5
    assert lock(files).read_text("ascii") == LOCK_RECORD
    again, connector = go(files, at=NOW + timedelta(seconds=1))
    assert again.code == 4
    assert again.err == "error: min_call_interval_not_elapsed\n"
    assert connector.calls == 0


def test_the_interval_is_checked_at_step_2_before_the_authorisation_and_any_key(
    tmp_path: Path,
) -> None:
    """ADR 0026 rule 14: step 2 holds the allowlist, the minimum interval and the kill switch,
    before the authorisation (3) and the keys (6). A run that would also be refused for its missing
    authorisation reference is refused for the interval; no key is read."""
    files = files_for(tmp_path, 3000)
    go(files, at=NOW)
    args = base_args(files, target=True)
    at = args.index("--authorisation-reference")
    del args[at : at + 2]
    env = EnvRecorder({"SARVAM_API_KEY": KEY_CANARY})
    run = run_sim(files, args=args, target=True, instant=NOW + timedelta(seconds=1), environ=env)
    assert run.code == 4
    assert run.err == "error: min_call_interval_not_elapsed\n"
    assert run.trace == ["parse_inputs", "allowlist"]
    assert [r for r in env.reads if r == "SARVAM_API_KEY"] == []


def test_the_allowlist_is_checked_before_the_interval(tmp_path: Path) -> None:
    """The order inside step 2: a target that is not on the list is refused as such even when the
    interval has not elapsed (the list answers 'may I call this at all')."""
    files = files_for(tmp_path, 3000)
    go(files, at=NOW)
    files.allowlist.write_text("ws://127.0.0.1:9999/other\n", "utf-8")
    run, connector = go(files, at=NOW + timedelta(seconds=1))
    assert run.err == "error: target_not_on_allowlist\n"
    assert connector.calls == 0


def test_a_run_with_a_fake_agent_neither_reads_nor_writes_the_lock(tmp_path: Path) -> None:
    """The interval protects a target from being dialled in a loop. ``--agent fake:SCENARIO`` dials
    nothing: it is not held to the interval and leaves no lock (amended ADR 0026 rule 18)."""
    files = files_for(tmp_path, 3000)
    one = run_sim(files, instant=NOW)
    two = run_sim(files, instant=NOW + timedelta(milliseconds=1500))
    assert (one.code, two.code) == (0, 0), (two.out, two.err)
    assert not lock(files).exists()
    # and a lock that is there is not read by it either
    lock(files).write_text("garbage\n", "ascii")
    lock(files).chmod(0o644)
    three = run_sim(files, instant=NOW + timedelta(seconds=9))
    assert three.code == 0
    assert lock(files).read_text("ascii") == "garbage\n"


def test_a_dry_run_neither_reads_nor_writes_the_lock(tmp_path: Path) -> None:
    files = files_for(tmp_path, 3000)
    go(files, at=NOW)
    before = tree(files.out)
    args = [*base_args(files, target=True), "--dry-run"]
    del args[args.index("--authorisation-reference") : args.index("--authorisation-reference") + 2]
    run = run_sim(files, args=args, target=True, instant=NOW + timedelta(seconds=1))
    assert run.code == 0, (run.out, run.err)
    assert run.value("dry run") == "nothing was sent"
    assert tree(files.out) == before


@pytest.mark.parametrize(
    ("label", "prepare", "code"),
    [
        ("garbage", lambda p: p.write_text("garbage\n", "ascii"), "call_lock_invalid"),
        (
            "wrong format",
            lambda p: p.write_text(LOCK_RECORD.replace("call_lock/1", "call_lock/9"), "ascii"),
            "call_lock_invalid",
        ),
        (
            "recorded in the future",
            lambda p: p.write_text(LOCK_RECORD.replace("2026-10-27", "2026-10-28"), "ascii"),
            "call_lock_clock_inconsistent",
        ),
        (
            "too large",
            lambda p: p.write_text(LOCK_RECORD + " " * 9000, "ascii"),
            "call_lock_invalid",
        ),
    ],
)
def test_a_lock_that_cannot_be_trusted_refuses_the_run_and_is_left_as_it_is(
    tmp_path: Path, label: str, prepare: Any, code: str
) -> None:
    files = files_for(tmp_path, 3000)
    files.out.mkdir(mode=0o700)
    prepare(lock(files))
    lock(files).chmod(0o600)
    before = tree(files.out)
    instant = NOW if label == "recorded in the future" else NOW + timedelta(days=1)
    run, connector = go(files, at=instant)
    assert run.code == 4, (label, run.out, run.err)
    assert run.err == f"error: {code}\n"
    assert connector.calls == 0
    assert tree(files.out) == before


def test_a_lock_with_group_or_other_permission_or_a_link_refuses_the_run(tmp_path: Path) -> None:
    files = files_for(tmp_path / "mode", 3000)
    files.out.mkdir(mode=0o700)
    lock(files).write_text(LOCK_RECORD, "ascii")
    lock(files).chmod(0o644)
    run, connector = go(files, at=NOW + timedelta(days=1))
    assert (run.code, run.err) == (4, "error: call_lock_invalid\n")
    assert connector.calls == 0

    linked = files_for(tmp_path / "link", 3000)
    linked.out.mkdir(mode=0o700)
    real = tmp_path / "elsewhere.lock"
    real.write_text(LOCK_RECORD, "ascii")
    real.chmod(0o600)
    os.symlink(real, lock(linked))
    run, connector = go(linked, at=NOW + timedelta(days=1))
    assert (run.code, run.err) == (4, "error: call_lock_invalid\n")
    assert connector.calls == 0
    assert real.read_text("ascii") == LOCK_RECORD


def test_an_invocation_in_progress_on_the_same_output_directory_is_refused(tmp_path: Path) -> None:
    """Concurrency 1: while another invocation holds the claim, a second one is refused at the
    connect with ``call_in_progress`` (even with no interval), and makes no connection. The claim is
    an exclusive ``flock`` on the lock file, so it dies with the process that held it."""
    files = files_for(tmp_path, 0)
    files.out.mkdir(mode=0o700)
    descriptor = os.open(lock(files), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run, connector = go(files, at=NOW)
        assert (run.code, run.err) == (4, "error: call_in_progress\n"), (run.out, run.err)
        assert connector.calls == 0
        assert lock(files).read_bytes() == b""  # the holder's file was not touched
    finally:
        os.close(descriptor)  # closing releases the lock
    again, connector = go(files, at=NOW)
    assert again.code == 0, (again.out, again.err)
    assert connector.calls == 1


def test_the_claim_is_released_when_the_invocation_ends_even_after_a_failure(
    tmp_path: Path,
) -> None:
    files = files_for(tmp_path, 0)

    async def broken(request: Any) -> Any:
        raise RuntimeError("boom")

    crash = run_sim(files, target=True, instant=NOW, world_kw={"connect": broken})
    assert crash.code == 1
    again, connector = go(files, at=NOW + timedelta(seconds=1))
    assert again.code == 0, (again.out, again.err)
    assert connector.calls == 1


def test_nothing_about_the_lock_is_printed_or_stored_in_the_run(tmp_path: Path) -> None:
    files = files_for(tmp_path, 3000)
    run, _ = go(files, at=NOW)
    streams = run.out + run.err
    assert LOCK_NAME not in streams
    assert str(lock(files)) not in streams
    report = run.report()
    assert LOCK_NAME not in report
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]
    assert LOCK_NAME not in json.dumps(outcome)
    assert "call_lock" not in json.dumps(outcome)
    profile = run.bundle().snapshot.metadata["tracer.sim_profile"]
    assert profile["limits"]["min_call_interval_ms"] == 3000  # the value is recorded as used


def test_the_real_engines_are_not_built_before_the_interval_passes(tmp_path: Path) -> None:
    """A refusal at step 2 sends nothing to any provider (rule 14): with the real engines and the
    interval not elapsed, the provider is never called."""
    files = write_inputs(
        tmp_path, sim_profile=profile_doc(tts="sarvam", stt="sarvam", min_call_interval_ms=3000)
    )
    clock = VirtualClock()
    sim = SarvamSim()
    ok = run_sim(
        files, args=real_args(files, target=True), target=True, instant=NOW, clock=clock,
        environ={"SARVAM_API_KEY": KEY_CANARY}, transport=sim,
    )  # fmt: skip
    assert ok.code == 0, (ok.out, ok.err)
    sent = len(sim.requests)
    assert sent > 0
    soon = run_sim(
        files, args=real_args(files, target=True), target=True,
        instant=NOW + timedelta(seconds=1), environ={"SARVAM_API_KEY": KEY_CANARY}, transport=sim,
    )  # fmt: skip
    assert soon.code == 4
    assert len(sim.requests) == sent


def test_a_call_too_soon_after_step_2_is_refused_at_the_claim_and_the_usage_is_kept(
    tmp_path: Path,
) -> None:
    """The check of step 2 and the claim at the connect are two moments: another invocation may
    connect between them (here a record appears while the lines are being paid for). The claim
    decides: no connection, exit 4, the same code, and the usage of the two paid lines is kept in
    the recovery file that the last line of the output names (ADR 0026 rule 14, ADR 0024 rule
    16)."""
    files = write_inputs(
        tmp_path, sim_profile=profile_doc(tts="sarvam", stt="sarvam", min_call_interval_ms=3000)
    )
    connected: list[str] = []

    def watch(step: str) -> None:
        if step == "open_store":  # step 8: the output directory exists, the lines are paid for
            lock(files).write_text(LOCK_RECORD, "ascii")
            lock(files).chmod(0o600)

    async def connect(request: Any) -> Any:
        connected.append("connect")
        raise AssertionError("the connection was made")

    sim = SarvamSim()
    run = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ={"SARVAM_API_KEY": KEY_CANARY},
        transport=sim,
        instant=NOW + timedelta(seconds=1),
        world_kw={"trace": watch, "connect": connect},
    )
    assert connected == []
    assert run.code == 4, (run.out, run.err)
    assert run.err == "error: min_call_interval_not_elapsed\n"
    (recovery,) = list(files.out.glob("usage-recovery-*.json"))
    assert run.out == f"usage recovery: {recovery}\n"
    document = json.loads(recovery.read_text("ascii"))
    assert [u["stage"] for u in document["usage_records"]] == ["sim_caller_tts", "sim_caller_tts"]
    assert len(sim.bodies("/text-to-speech")) == 2
    assert lock(files).read_text("ascii") == LOCK_RECORD  # the refused claim recorded nothing
