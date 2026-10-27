# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Milestone M1 (SCOPE section A.5): ``tracer simulate`` runs end to end OFFLINE with fake engines.

Contract: docs/phase4/api-surface-batch-e.md section 10. The command

    tracer simulate --agent fake:agent.json --evaluator evaluator.json --persona persona.json
        --sim-profile sim_profile.json --tts fake --caller scripted --stt fake
        --authorisation-reference TEXT --out out

places one call to the in-process fake agent (no socket), synthesises the two scripted lines with
the
fake tone, hears the agent's two replies, writes the stereo recording, the store, the report and the
call log, and prints exactly the lines pinned below. The numbers are hand-computed on the virtual
clock: the first line is 14 frames (7 characters at 40 ms), sent at 0, 20 ... 260 ms (edge 280); the
agent speaks 10 frames from 400 (last at 580) and 5 frames from 1100; the second line starts at
580 + 300 = 880 and ends at 1140 (edge 1160). Gaps after the lines: 400 - 280 = 120 and
1160 - 1160 = 0 (the frame at 1160 is the first active one at or after the edge); the nearest-rank
median of [0, 120] is 0. The agent speaks at 1100, 1120 and 1140 while the second line is sent:
3 frames of 20 ms overlap.
"""

from __future__ import annotations

import hashlib
import re
import struct
import wave
from pathlib import Path

import pytest

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    HEARD_1,
    LINE_0,
    LINE_1,
    NOW,
    STAMP,
    base_args,
    mod,
    profile_doc,
    run_sim,
    scenario_doc,
    write_inputs,
)

STEPS = (
    "parse_inputs", "allowlist", "authorisation", "caps", "output_probe", "keys_and_call_log",
    "presynthesis", "open_store", "connect", "call_loop", "hang_up", "post_call_stt", "scoring",
    "store_write", "report",
)  # fmt: skip

NOTE = "observed at the simulator over pipecat-protobuf/1, quantum 20 ms"
EXPECTED = [
    "result: call established",
    "end reason: script_complete",
    "outcome: not evaluated",
    "dialect: pipecat-protobuf/1",
    "caller audio: synthetic (generated speech, not a person)",
    "caller turns: 2 started, 2 completed",
    "agent segments: 2",
    f"first agent audio: 400 ms ({NOTE})",
    f"gap after the caller's line (median): 0 ms ({NOTE}, N 2)",
    f"overlap: 60 ms ({NOTE})",
    f"answered caller turns: 2 ({NOTE})",
    "agreement: not scored (missing input agent_transcript)",
    "tts usage: none",
    "llm usage: none",
    "stt usage: none",
    "cost: not available (no_usage)",
    "ingest warnings: 0",
]


@pytest.fixture
def relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[str]]:
    """Inputs in ``in/`` and the output directory ``out`` relative to the working directory, so that
    the printed paths are the same on every machine."""
    monkeypatch.chdir(tmp_path)
    files = write_inputs(Path("in"))
    files.out = Path("out")
    args = [("out" if a == str(Path("in") / "out") else a) for a in base_args(files)]
    return tmp_path, [files, args]


def test_m1_offline_run_prints_exactly_these_lines(relative: tuple[Path, list[str]]) -> None:
    """The M1 contract: exit 0, nothing on stderr, and these stdout lines in this order, then the
    four lines naming the bundle and the three files the command wrote."""
    _, (files, args) = relative
    run = run_sim(files, args=args)
    assert run.code == 0, run.err
    assert run.err == ""
    assert run.lines[: len(EXPECTED)] == EXPECTED
    tail = run.lines[len(EXPECTED) :]
    assert len(tail) == 4
    assert re.fullmatch(r"bundle: rbnd_[0-9a-f-]{36}", tail[0])
    assert tail[1:] == [
        "store: out/tracer.sqlite",
        f"recording: out/recording-{STAMP}.wav",
        f"report: out/report-{STAMP}.html",
    ]
    assert run.out.isascii()


def test_m1_follows_the_fifteen_steps_in_order(relative: tuple[Path, list[str]]) -> None:
    _, (files, args) = relative
    run = run_sim(files, args=args)
    assert tuple(run.trace) == STEPS
    assert tuple(mod("simulate").STEPS) == STEPS


def test_m1_writes_exactly_these_files_private(relative: tuple[Path, list[str]]) -> None:
    root, (files, args) = relative
    run = run_sim(files, args=args)
    assert run.code == 0
    out = root / "out"
    assert run.names() == [
        f"recording-{STAMP}.wav",
        f"report-{STAMP}.html",
        "sim_call_log.jsonl",
        "tracer.sqlite",
    ]
    assert (out.stat().st_mode & 0o777) == 0o700
    for path in out.iterdir():
        assert (path.stat().st_mode & 0o777) == 0o600, path.name
    assert not (out / "tts-cache").exists()  # a fake engine has no cache
    assert not list(out.glob("usage-recovery-*"))


def test_m1_recording_is_stereo_caller_left_agent_right_exactly_as_sent(
    relative: tuple[Path, list[str]],
) -> None:
    """ADR 0027 rules 3 and 4 and invariant I2: channel 0 is what was sent, channel 1 what was
    received (placed at its arrival), at the dialect rate, 16-bit PCM."""
    root, (files, args) = relative
    run_sim(files, args=args)
    path = root / "out" / f"recording-{STAMP}.wav"
    with wave.open(str(path)) as reader:
        assert (reader.getnchannels(), reader.getframerate(), reader.getsampwidth()) == (
            2,
            16000,
            2,
        )
        frames = reader.readframes(reader.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    left, right = samples[0::2], samples[1::2]
    ms = 16  # samples per millisecond
    tone = 1000  # the fake tone and the fake agent's level
    assert set(left[: 280 * ms]) == {tone}  # line 0: 14 frames, 0 .. 280 ms
    assert set(left[280 * ms : 880 * ms]) == {0}
    assert set(left[880 * ms : 1160 * ms]) == {tone}  # line 1: 14 frames
    assert set(right[: 400 * ms]) == {0}
    assert set(right[400 * ms : 600 * ms]) == {tone}  # reply 1: 10 frames from 400
    assert set(right[600 * ms : 1100 * ms]) == {0}
    assert set(right[1100 * ms : 1200 * ms]) == {tone}  # reply 2: 5 frames from 1100
    assert len(left) == len(right) == 1200 * ms


def test_m1_store_report_and_recording_agree(relative: tuple[Path, list[str]]) -> None:
    root, (files, args) = relative
    run = run_sim(files, args=args)
    bundle = run.bundle()
    (artifact,) = bundle.artifacts
    data = (root / "out" / f"recording-{STAMP}.wav").read_bytes()
    assert artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert artifact.size == len(data)
    assert artifact.uri.startswith("file:///")
    assert artifact.uri.endswith(f"recording-{STAMP}.wav")
    texts = {
        t.source: [x.text for x in bundle.turns if x.transcript_id == t.id]
        for t in bundle.transcripts
    }
    assert texts == {"script": [LINE_0, LINE_1], "our_stt": [HEARD_0, HEARD_1]}
    assert bundle.run.started_at == NOW
    report = run.report()
    assert f'src="recording-{STAMP}.wav"' in report  # relative; no audio is embedded
    assert "data:audio" not in report


def test_m1_twice_gives_the_same_files_modulo_ids(tmp_path: Path) -> None:
    """The caller's audio and the timing are the same on a re-run (virtual clock, same inputs); the
    records differ only by their fresh identifiers."""
    first = write_inputs(tmp_path / "a")
    second = write_inputs(tmp_path / "b")
    one, two = run_sim(first), run_sim(second)
    assert one.code == two.code == 0
    wav_one = (first.out / f"recording-{STAMP}.wav").read_bytes()
    wav_two = (second.out / f"recording-{STAMP}.wav").read_bytes()
    assert wav_one == wav_two

    def keep(text: str) -> list[str]:
        skipped = ("bundle:", "store:", "recording:", "report:")
        return [x for x in text.splitlines() if not x.startswith(skipped)]

    assert keep(one.out) == keep(two.out)


def test_m1_on_the_twilio_dialect_runs_offline_too(tmp_path: Path) -> None:
    files = write_inputs(tmp_path, sim_profile=profile_doc("twilio-media-stream/1"))
    run = run_sim(files)
    assert run.code == 0, run.err
    assert run.value("dialect") == "twilio-media-stream/1"
    assert run.value("end reason") == "script_complete"
    with wave.open(str(files.out / f"recording-{STAMP}.wav")) as reader:
        assert (reader.getframerate(), reader.getnchannels()) == (8000, 2)
    assert "quantum 20 ms" in run.value("first agent audio")


def test_m1_a_silent_agent_is_exit_two_with_the_edge_named(tmp_path: Path) -> None:
    """An agent that never speaks: ``max_silence``, timing values not measured (never 0), exit 2."""
    files = write_inputs(tmp_path, scenario=scenario_doc([{"wait_ms": 10_000}]))
    run = run_sim(files)
    assert run.code == 2, (run.out, run.err)
    assert run.value("end reason") == "max_silence"
    assert run.value("first agent audio") == "not measured (missing edge: first active agent frame)"
    assert run.value("agent segments") == "0"
    assert run.value("overlap").startswith("0 ms")  # nothing was heard: a measured 0
    assert "our_stt" not in run.report()
