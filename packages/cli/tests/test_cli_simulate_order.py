# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21: the order of operations of ``tracer simulate`` (ADR 0026 rules 14 and 15; ADR 0024 rule 16
(d) and Ambiguity 8; the Phase 3 batch E, G and I order tests as models).

Contract: docs/phase4/api-surface-batch-e.md section 10.2. Pinned here: the 15 steps run in one
order (a call-trace), every refusal of steps 1 to 6 happens before any key is read, any engine is
built and any connection is made (nothing is sent), and the usage of every answered request survives
a Ctrl-C, a second signal and an exception anywhere after the keys were read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    REF_TEXT,
    TOKEN_ENV,
    TOKEN_VALUE,
    EnvRecorder,
    SarvamSim,
    VirtualClock,
    base_args,
    core,
    fake_connect,
    mod,
    profile_doc,
    real_args,
    real_env,
    real_files,
    run_sim,
    sim_mod,
    write_inputs,
)

#: The 15 steps of the command, in the pinned order (section 10.2 of the contract); the trace of a
#: run must contain them in exactly this order, and ``simulate.STEPS`` must equal this tuple.
STEPS = (
    "parse_inputs",
    "allowlist",
    "authorisation",
    "caps",
    "output_probe",
    "keys_and_call_log",
    "presynthesis",
    "open_store",
    "connect",
    "call_loop",
    "hang_up",
    "post_call_stt",
    "scoring",
    "store_write",
    "report",
)


class Seams:
    """Recording factories and connector for the real-engine paths (the engines are the product's,
    built exactly as the default factories build them)."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def tts(self, seam: Any) -> Any:
        from tracer_evalproviders.sarvam.tts import create_sarvam_tts

        self.events.append("tts_factory")
        return create_sarvam_tts(
            call_log=seam.call_log,
            cap_characters=seam.cap,
            environ=seam.environ,
            env_file=seam.env_file,
            cache=seam.cache,
            transport=seam.transport,
            sleep=seam.sleep,
        )

    def stt(self, seam: Any) -> Any:
        from tracer_evalproviders.sarvam.engine import create_sarvam_stt_for_run

        self.events.append("stt_factory")
        return create_sarvam_stt_for_run(
            call_log=seam.call_log,
            model=seam.model,
            environ=seam.environ,
            env_file=seam.env_file,
            transport=seam.transport,
            sleep=seam.sleep,
        )

    def kwargs(self) -> dict[str, Any]:
        return {"tts_factory": self.tts, "stt_factory": self.stt}


class Closing:
    """A proxy that records ``close`` and forwards everything else to the real engine."""

    def __init__(self, engine: Any, name: str, closed: list[str]) -> None:
        self._engine = engine
        self._name = name
        self._closed = closed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    def close(self) -> None:
        self._closed.append(self._name)
        self._engine.close()


@pytest.mark.parametrize("refused", [False, True], ids=["call", "connection refused"])
def test_every_engine_and_the_call_log_are_closed_when_the_run_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refused: bool
) -> None:
    """The run owns its engines and the one call log: all are closed once, whatever the outcome."""
    from tracer_evalproviders.simcalllog import SimCallLog

    closed: list[str] = []
    original = SimCallLog.close

    def spy(self: Any) -> None:
        closed.append("call_log")
        original(self)

    monkeypatch.setattr(SimCallLog, "close", spy)
    seams = Seams([])
    files = real_files(tmp_path)
    clock = VirtualClock()

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    run = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ=real_env(),
        transport=SarvamSim(),
        clock=clock,
        world_kw={
            "connect": refuse if refused else fake_connect(files, clock),
            "tts_factory": lambda seam: Closing(seams.tts(seam), "tts", closed),
            "stt_factory": lambda seam: Closing(seams.stt(seam), "stt", closed),
        },
    )
    assert run.code == (5 if refused else 0), (run.out, run.err)
    assert sorted(closed) == ["call_log", "stt", "tts"]


def test_a_refusal_is_traced_at_the_step_that_refused(tmp_path: Path) -> None:
    """The trace names the step that was running when the run was refused (the step is traced
    before its checks), so a reader of the trace knows how far the run got."""
    expected = {
        "bad evaluator": (case_bad_evaluator, STEPS[:1]),
        "not on the allowlist": (case_not_on_allowlist, STEPS[:2]),
        "no reference": (case_no_reference, STEPS[:3]),
        "no cap": (case_cap("--max-tts-chars"), STEPS[:4]),
        "output is a file": (case_out_is_a_file, STEPS[:5]),
        "bearer missing": (case_missing_bearer, STEPS[:6]),
    }
    for label, (make, steps) in expected.items():
        files = real_files(tmp_path / label.replace(" ", "-"))
        run = run_sim(
            files,
            args=make(files),
            environ={"SARVAM_API_KEY": KEY_CANARY},
            transport=SarvamSim(),
        )
        assert run.code == 4, (label, run.out, run.err)
        assert tuple(run.trace) == steps, label


def test_order_of_operations_pinned_by_call_trace(tmp_path: Path) -> None:
    """ADR 0026 rule 14 and invariant I4: the steps run in this order; the key variable is first
    read at step 6, the output directory appears at step 6, the store at step 8 and the connection
    at step 9. The engines are built after the keys and before the pre-synthesis."""
    files = real_files(tmp_path)
    env = real_env()
    snapshots: list[tuple[str, int, bool, bool]] = []
    events: list[str] = []

    def trace(step: str) -> None:
        events.append(step)
        snapshots.append(
            (step, len(env.reads), files.out.exists(), (files.out / "tracer.sqlite").exists())
        )

    seams = Seams(events)
    run = run_sim(
        files,
        args=real_args(files),
        environ=env,
        transport=SarvamSim(),
        world_kw={"trace": trace, **seams.kwargs()},
    )
    assert run.code == 0, (run.out, run.err)
    steps = [e for e in events if e in STEPS]
    assert tuple(steps) == STEPS == tuple(mod("simulate").STEPS)
    assert events.index("tts_factory") > events.index("keys_and_call_log")
    assert events.index("stt_factory") > events.index("keys_and_call_log")
    assert events.index("tts_factory") < events.index("presynthesis")
    by_step = {step: (reads, out, store) for step, reads, out, store in snapshots}
    for step in STEPS[:5]:  # parse .. output probe: no key read, nothing created
        assert by_step[step][0] == 0, step
        assert by_step[step][1] is False, step
    assert by_step["presynthesis"][0] > 0
    assert by_step["presynthesis"][1] is True
    assert by_step["open_store"][2] is False
    assert by_step["connect"][2] is True
    assert all(name == "SARVAM_API_KEY" for name in env.reads)
    assert by_step["report"][2] is True


def test_the_trace_of_a_failed_connection_stops_after_the_connect_step(tmp_path: Path) -> None:
    """A refused connection is an outcome (``setup_failed``): there is no call loop, no hang-up
    and no speech-to-text, and the run is still scored, stored and reported (exit 5)."""
    files = write_inputs(tmp_path)

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    run = run_sim(files, target=True, world_kw={"connect": refuse})
    assert run.code == 5, (run.out, run.err)
    assert tuple(run.trace) == (
        "parse_inputs", "allowlist", "authorisation", "caps", "output_probe", "keys_and_call_log",
        "presynthesis", "open_store", "connect", "hang_up", "scoring", "store_write", "report",
    )  # fmt: skip
    assert run.value("result") == "call not established"
    assert run.value("end reason") == "setup_failed"
    assert not list(files.out.glob("recording-*"))  # nothing was recorded


# --------------------------------------------------------------------------------- refusals


def write(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text, "utf-8")
    path.chmod(mode)


def case_bad_evaluator(f: Any) -> list[str]:
    write(f.evaluator, "{not json")
    return list(real_args(f))


def case_language(f: Any) -> list[str]:
    write(f.evaluator, json.dumps({**json.loads(f.evaluator.read_text()), "language": "hi"}))
    return list(real_args(f))


def case_no_voice(f: Any) -> list[str]:
    doc = json.loads(f.sim_profile.read_text())
    doc["voices"] = {"someone-else": doc["voices"]["caller-a"]}
    write(f.sim_profile, json.dumps(doc))
    return list(real_args(f))


def case_engine_mismatch(f: Any) -> list[str]:
    args = real_args(f)
    args[args.index("--tts") + 1] = "fake"
    return list(args)


def case_webrtc(f: Any) -> list[str]:
    doc = json.loads(f.sim_profile.read_text())
    doc["dialect"] = "smallwebrtc/1"
    doc["audio"]["wire_codec"] = "webrtc_negotiated"
    write(f.sim_profile, json.dumps(doc))
    return list(real_args(f))


def case_stt_eight_khz(f: Any) -> list[str]:
    write(
        f.sim_profile, json.dumps(profile_doc("twilio-media-stream/1", tts="sarvam", stt="sarvam"))
    )
    return list(real_args(f))


def case_no_scripted_turns(f: Any) -> list[str]:
    write(f.evaluator, json.dumps({**json.loads(f.evaluator.read_text()), "scripted_turns": []}))
    return list(real_args(f))


def case_replay_missing(f: Any) -> list[str]:
    caller = {"mode": "replay", "max_line_chars": 200, "max_turns": 2, "end_sentinel": "END_CALL"}
    write(f.sim_profile, json.dumps(profile_doc(tts="sarvam", stt="sarvam", caller=caller)))
    args = real_args(f)
    args[args.index("--caller") + 1] = f"fake:{f.root / 'no-such-directory'}"
    return list(args)


def case_not_on_allowlist(f: Any) -> list[str]:
    write(f.allowlist, "ws://127.0.0.1:9999/other\n")
    return list(real_args(f, target=True))


def case_allowlist_open_mode(f: Any) -> list[str]:
    f.allowlist.chmod(0o644)
    return list(real_args(f, target=True))


def case_allowlist_missing(f: Any) -> list[str]:
    args = real_args(f, target=True)
    at = args.index("--allowlist")
    del args[at : at + 2]
    return list(args)


def case_twilio_credentials(f: Any) -> list[str]:
    write(f.sim_profile, json.dumps(profile_doc("twilio-media-stream/1")))
    write(f.allowlist, f"{f.url} bot_holds_telephony_credentials=true\n")
    return list(base_args(f, target=True))


def case_insecure(f: Any) -> list[str]:
    write(
        f.target,
        json.dumps({"format": "tracer.sim_target/1", "name": "x", "url": "ws://example.com/ws"}),
    )
    write(f.allowlist, "wss://example.com/ws\n")
    return list(real_args(f, target=True))


def case_no_reference(f: Any) -> list[str]:
    args = real_args(f)
    at = args.index("--authorisation-reference")
    del args[at : at + 2]
    return list(args)


def case_cap(option: str) -> Any:
    def make(f: Any) -> list[str]:
        args = real_args(f)
        at = args.index(option)
        del args[at : at + 2]
        return list(args)

    return make


def case_out_is_a_file(f: Any) -> list[str]:
    f.out.write_text("not a directory")
    return list(real_args(f))


def case_report_exists(f: Any) -> list[str]:
    f.out.mkdir(mode=0o700)
    (f.out / "report-20261027T120000Z.html").write_text("earlier")
    return list(real_args(f))


def case_missing_key(f: Any) -> list[str]:
    return list(real_args(f))


def case_missing_bearer(f: Any) -> list[str]:
    write(f.allowlist, f"{f.url} auth_token_env={TOKEN_ENV}\n")
    return list(real_args(f, target=True))


CASES = [
    ("bad evaluator file", case_bad_evaluator, 4, "evaluator_not_json", True),
    ("language", case_language, 4, "language_not_supported", True),
    ("persona without a voice", case_no_voice, 4, "voice_missing_for_persona", True),
    ("engine mismatch", case_engine_mismatch, 4, "engine_mismatch (tts)", True),
    ("webrtc dialect", case_webrtc, 4, "dialect_not_runnable", True),
    ("stt at 8 kHz", case_stt_eight_khz, 4, "stt_sample_rate_not_accepted", True),
    ("no scripted turns", case_no_scripted_turns, 4, "scripted_turns_missing", True),
    ("replay directory missing", case_replay_missing, 4, "directory_missing", True),
    ("target not on the allowlist", case_not_on_allowlist, 4, "target_not_on_allowlist", True),
    ("allowlist mode", case_allowlist_open_mode, 4, "allowlist_mode_too_open", True),
    ("allowlist missing", case_allowlist_missing, 4, "allowlist_missing", True),
    ("bot holds credentials", case_twilio_credentials, 4, "bot_holds_telephony_credentials", True),
    ("insecure target", case_insecure, 4, "insecure_target", True),
    (
        "authorisation reference missing",
        case_no_reference,
        4,
        "authorisation_reference_missing",
        True,
    ),
    ("tts cap missing", case_cap("--max-tts-chars"), 4, "cap_missing (--max-tts-chars)", True),
    ("stt cap missing", case_cap("--max-stt-seconds"), 4, "cap_missing (--max-stt-seconds)", True),
    (
        "call cap missing",
        case_cap("--max-call-seconds"),
        4,
        "cap_missing (--max-call-seconds)",
        True,
    ),
    (
        "output path is a file",
        case_out_is_a_file,
        4,
        "the output path exists and is not a directory",
        False,
    ),
    ("report name taken", case_report_exists, 4, "report_exists", False),
    ("bearer token variable missing", case_missing_bearer, 4, "auth_token_missing", True),
    ("key missing", case_missing_key, 4, "key_missing", True),
]


@pytest.mark.parametrize(
    ("label", "make", "code", "message", "no_dir"), CASES, ids=[c[0] for c in CASES]
)
def test_refusals_happen_before_any_key_read_or_connect(
    tmp_path: Path, label: str, make: Any, code: int, message: str, no_dir: bool
) -> None:
    """ADR 0026 rule 14 and invariant I4: a refusal at steps 1 to 6 sends nothing, builds no engine,
    opens no connection, reads no key (or, for a missing key, finds it missing and creates no
    directory), prints nothing on stdout and exits 4 with one ``error:`` line naming the code."""
    files = real_files(tmp_path)
    args = make(files)
    env = EnvRecorder({} if label == "key missing" else {"SARVAM_API_KEY": KEY_CANARY})
    sim = SarvamSim()
    calls: list[str] = []
    out_before = files.out.exists()

    async def connect(request: Any) -> Any:
        calls.append("connect")
        raise AssertionError("a connection was made")

    seams = Seams(calls)
    run = run_sim(
        files,
        args=args,
        environ=env,
        transport=sim,
        world_kw={"connect": connect, **seams.kwargs()},
    )
    assert run.code == code, (run.out, run.err)
    assert run.out == ""
    assert run.err.startswith("error: "), run.err
    assert message in run.err, run.err
    assert len(run.err.splitlines()) == 1
    assert run.err.isascii()
    assert calls == [], calls  # no factory call and no connection
    assert sim.requests == []
    if label != "key missing":
        assert [r for r in env.reads if r == "SARVAM_API_KEY"] == [] or label in (
            "bearer token variable missing",
        )
    assert KEY_CANARY not in run.err
    if no_dir:
        assert files.out.exists() is out_before is False, "a refusal left a directory behind"
    steps = [s for s in run.trace if s in STEPS]
    assert "presynthesis" not in steps
    assert "connect" not in steps
    assert not list(tmp_path.glob("**/usage-recovery-*"))


def test_a_usage_error_is_exit_64_with_nothing_on_stdout(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    for add, drop in (([], ["--evaluator"]), ([], ["--out"]), (["--bogus"], [])):
        run = run_sim(files, args=real_args(files), add=add, drop=drop)
        assert run.code == 64
        assert run.out == ""
        assert run.err.startswith("usage: ")
        assert "tracer: error:" in run.err
        assert run.trace == []


# -------------------------------------------------------------------------- usage recovery


def recovery_files(out: Path) -> list[Path]:
    return sorted(out.glob("usage-recovery-*.json"))


def read_recovery(out: Path) -> dict[str, Any]:
    (path,) = recovery_files(out)
    assert (path.stat().st_mode & 0o777) == 0o600
    document: dict[str, Any] = json.loads(path.read_text("ascii"))
    return document


def test_usage_survives_ctrl_c_mid_call(tmp_path: Path) -> None:
    """ADR 0026 rule 14 and invariant I4: a ``KeyboardInterrupt`` anywhere after the keys were read
    keeps the usage of every answered request in ``usage-recovery-<UTC>.json``, prints its path
    and is
    raised again. Here: after the two TTS requests were paid, while the call is being connected, and
    again in the middle of the call loop."""
    for where in ("connect", "send"):
        files = real_files(tmp_path / where)
        clock = VirtualClock()
        sim = SarvamSim()
        with pytest.raises(KeyboardInterrupt):
            run_sim(
                files,
                args=real_args(files, target=True),
                target=True,
                environ=real_env(),
                transport=sim,
                clock=clock,
                world_kw={"connect": interrupting_connect(where, files, clock)},
            )
        document = read_recovery(files.out)
        assert document["format"] == "tracer.usage_recovery/1" == mod("cli").RECOVERY_FORMAT
        assert document["failed_step"] == "call"
        assert document["error_class"] == "KeyboardInterrupt"
        stages = [u["stage"] for u in document["usage_records"]]
        assert stages == ["sim_caller_tts", "sim_caller_tts"], where
        assert [u["quantities"][0]["unit"] for u in document["usage_records"]] == [
            "input_characters"
        ] * 2
        text = json.dumps(document)
        assert KEY_CANARY not in text
        assert "OUTCOMECANARY" not in text
        assert len(sim.bodies("/text-to-speech")) == 2


def interrupting_connect(where: str, files: Any, clock: VirtualClock) -> Any:
    """A connector that raises ``KeyboardInterrupt`` while connecting, or on the fourth message
    the simulator sends (in the middle of the call loop)."""

    async def connect(request: Any) -> Any:
        if where == "connect":
            raise KeyboardInterrupt
        conn = await fake_connect(files, clock)(request)
        original = conn.send
        sent = [0]

        async def send(message: Any) -> None:
            if sent[0] >= 3:
                raise KeyboardInterrupt
            sent[0] += 1
            await original(message)

        conn.send = send
        return conn

    return connect


def test_an_exception_after_the_keys_keeps_the_usage_and_is_exit_1(tmp_path: Path) -> None:
    files = real_files(tmp_path)

    async def broken(request: Any) -> Any:
        raise RuntimeError("secret detail " + KEY_CANARY)

    run = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ=real_env(),
        transport=SarvamSim(),
        world_kw={"connect": broken},
    )
    assert run.code == 1
    assert run.err == "error: unexpected failure (RuntimeError)\n"
    (path,) = recovery_files(files.out)
    assert run.out == f"usage recovery: {path}\n"
    assert len(json.loads(path.read_text())["usage_records"]) == 2
    assert KEY_CANARY not in run.out + run.err + path.read_text()


def test_a_refusal_after_paid_requests_still_keeps_their_usage(tmp_path: Path) -> None:
    """The cap of the TTS characters is reached by the second line (7 + 7 > 10): the run is refused
    before the call (exit 4) and the one request that was paid is in the recovery file."""
    files = real_files(tmp_path)
    args = real_args(files)
    args[args.index("--max-tts-chars") + 1] = "10"
    sim = SarvamSim()
    run = run_sim(files, args=args, environ=real_env(), transport=sim)
    assert run.code == 4
    assert "spend_cap_reached_before_the_call (tts_chars)" in run.err
    assert len(sim.bodies("/text-to-speech")) == 1
    assert len(read_recovery(files.out)["usage_records"]) == 1
    assert "connect" not in run.trace
    assert not list(files.out.glob("report-*"))


# ----------------------------------------------------------------------------------- signals


class FakeSignals:
    """The signal source of a test: it keeps the callback the command installs."""

    def __init__(self) -> None:
        self.callback: Any = None
        self.installed = 0
        self.removed = 0

    def install(self, on_signal: Any) -> Any:
        import contextlib

        @contextlib.contextmanager
        def manager() -> Any:
            self.callback = on_signal
            self.installed += 1
            try:
                yield
            finally:
                self.removed += 1

        return manager()


def fire_at(clock: VirtualClock, signals: FakeSignals, at_ms: int, times: int, files: Any) -> Any:
    tasks: list[Any] = []

    async def connect(request: Any) -> Any:
        import asyncio

        async def fire() -> None:
            await clock.sleep_until_ns(clock.monotonic_ns() + at_ms * 1_000_000)
            for _ in range(times):
                signals.callback()

        tasks.append(asyncio.ensure_future(fire()))
        return await sim_mod("fakeagent").connect_fake_agent(
            sim_mod("fakeagent").parse_fake_agent(files.scenario.read_text()),
            request.dialect,
            clock,
        )()

    return connect


def test_the_first_signal_hangs_up_gracefully_and_the_run_is_stored(tmp_path: Path) -> None:
    """ADR 0026 rule 17 and SCOPE P4-23: the first SIGINT or SIGTERM ends the call with
    ``kill_switch``; what was recorded is kept, stored and reported."""
    files = write_inputs(tmp_path)
    (files.root / "stt_replay" / "chunk-0001.json").unlink()  # only the first reply is heard
    clock, signals = VirtualClock(), FakeSignals()
    run = run_sim(
        files,
        target=True,
        clock=clock,
        world_kw={"signals": signals, "connect": fire_at(clock, signals, 700, 1, files)},
    )
    assert run.code in (0, 2), (run.out, run.err)
    assert run.value("end reason") == "kill_switch"
    assert signals.installed == 1
    assert signals.removed == 1
    bundle = run.bundle()
    assert bundle.run.metadata["tracer.sim_outcome"]["end_reason"] == "kill_switch"
    assert bundle.run.metadata["tracer.sim_outcome"]["counts"]["signals_received"] == 1
    assert list(files.out.glob("recording-*.wav"))
    assert list(files.out.glob("report-*.html"))
    assert run.value("caller turns").startswith("1 started")


def test_a_second_signal_aborts_and_the_usage_is_kept(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    clock, signals = VirtualClock(), FakeSignals()
    with pytest.raises(KeyboardInterrupt):
        run_sim(
            files,
            args=real_args(files, target=True),
            target=True,
            environ=real_env(),
            transport=SarvamSim(),
            clock=clock,
            world_kw={"signals": signals, "connect": fire_at(clock, signals, 300, 2, files)},
        )
    assert len(read_recovery(files.out)["usage_records"]) == 2
    assert not list(files.out.glob("report-*"))


def test_a_stop_before_the_connection_is_a_kill_switch_that_never_connects(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    calls: list[str] = []

    async def connect(request: Any) -> Any:
        calls.append("connect")
        raise AssertionError

    class Early(FakeSignals):
        def install(self, on_signal: Any) -> Any:
            on_signal()  # the signal arrived before the loop was running the call
            return super().install(on_signal)

    run = run_sim(files, target=True, world_kw={"signals": Early(), "connect": connect})
    assert run.code == 5
    assert calls == []
    assert run.value("end reason") == "kill_switch"
    assert run.value("result") == "call not established"
    assert core("simtiming") is not None
    assert REF_TEXT not in run.out
    assert TOKEN_VALUE not in run.out
