# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""Shared helpers of the Phase 4 batch F cli tests (P4-28 to P4-30). Support module, not collected.
Contract: docs/phase4/api-surface-batch-f.md sections 4 to 6.

It builds on ``cli_p4e_support`` (the in-process run of ``tracer simulate`` with a virtual clock, the
fake agent, the scripted Sarvam and the canary key). Nothing here opens a socket or reads a key.
All text is invented; non-ASCII text is written as escapes or read from the committed synthetic
fixture ``packages/evalcore/tests/fixtures/sim_mr.json`` (indic-linguistics, needs native review,
BL-169).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli_batchf_support import (  # type: ignore[import-not-found,unused-ignore]
    KEY_CANARY,
    results_by_key,
)
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    LINE_0,
    LINE_1,
    REF_TEXT,
    TOKEN_VALUE,
    Files,
    SarvamSim,
    SimRun,
    VirtualClock,
    evaluator_doc,
    fake_connect,
    llm_caller,
    profile_doc,
    real_args,
    real_env,
    real_files,
    run_sim,
    scenario_doc,
    sim_mod,
    write_inputs,
)

__all__ = [
    "KEY_CANARY",
    "REF_TEXT",
    "TOKEN_VALUE",
    "CaseOutcome",
    "FakeSignals",
    "Files",
    "SarvamSim",
    "SimRun",
    "VirtualClock",
    "results_by_key",
    "run_case",
]

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
SIM_MR = ROOT / "packages" / "evalcore" / "tests" / "fixtures" / "sim_mr.json"

#: The result keys of a configuration without a barge-in (ADR 0027 rule 11), in the order stored.
TIMING_KEYS = (
    "sim_first_agent_audio_ms",
    "sim_agent_response_gap_median_ms",
    "sim_overlap_ms",
    "sim_answered_caller_turns",
)
AGREEMENT_KEYS = (
    "stt_export_agent_agreement_mer",
    "stt_export_agent_flag_count",
    "stt_export_agent_flagged_token_share",
)
RESULT_KEYS = (*TIMING_KEYS, *AGREEMENT_KEYS)

#: The ten cases of the T-DEG matrix of SCOPE P4-29, in the order the matrix test runs them.
CASES = (
    "setup_failed",
    "agent_silent",
    "comfort_noise",
    "no_export",
    "export_present",
    "stt_failure",
    "kill_switch",
    "max_duration",
    "max_silence",
    "spend_cap",
)


class FakeSignals:
    """The signal source of a test: it keeps the callback the command installs."""

    def __init__(self) -> None:
        self.callback: Any = None

    def install(self, on_signal: Any) -> Any:
        @contextlib.contextmanager
        def manager() -> Any:
            self.callback = on_signal
            yield

        return manager()


def fire_at(clock: VirtualClock, signals: FakeSignals, at_ms: int, files: Files) -> Any:
    """A connector that answers with the in-process fake agent and raises the first signal
    ``at_ms`` after the connection."""
    tasks: list[Any] = []

    async def connect(request: Any) -> Any:
        async def fire() -> None:
            await clock.sleep_until_ns(clock.monotonic_ns() + at_ms * 1_000_000)
            signals.callback()

        tasks.append(asyncio.ensure_future(fire()))
        return await fake_connect(files, clock)(request)

    return connect


@dataclass
class CaseOutcome:
    name: str
    run: SimRun
    files: Files
    sim: SarvamSim | None = None

    @property
    def bundle(self) -> Any:
        return self.run.bundle()

    @property
    def results(self) -> dict[str, Any]:
        found: dict[str, Any] = results_by_key(self.bundle)
        return found

    @property
    def timing(self) -> dict[str, Any]:
        timing = self.bundle.run.metadata["tracer.timing"]
        assert isinstance(timing, dict)
        return timing

    @property
    def outcome(self) -> dict[str, Any]:
        outcome = self.bundle.run.metadata["tracer.sim_outcome"]
        assert isinstance(outcome, dict)
        return outcome


def _replay_dir(root: Path, texts: list[str]) -> Path:
    """A speech-to-text replay directory with exactly one body per text (the replay refuses extra
    and missing files alike)."""
    folder = root / "stt_replay_exact"
    folder.mkdir(parents=True, exist_ok=True)
    for index, text in enumerate(texts):
        (folder / f"chunk-{index:04d}.json").write_text(
            json.dumps(
                {"request_id": f"replay-{index}", "transcript": text, "language_code": "mr-IN"},
                ensure_ascii=True,
            ),
            "utf-8",
        )
    return folder


def _swap_stt(args: list[str], folder: Path) -> list[str]:
    out = list(args)
    out[out.index("--stt") + 1] = f"fake:{folder}"
    return out


def run_case(name: str, root: Path) -> CaseOutcome:
    """Run one case of the T-DEG matrix. Every case is a two-line scripted mr call over
    ``pipecat-protobuf/1`` against the in-process fake agent, on the virtual clock, with no socket.
    Only what the case is about differs."""
    if name == "setup_failed":
        files = write_inputs(root)

        async def refuse(request: Any) -> Any:
            raise sim_mod("errors").SetupFailedError(code="connection_refused")

        run = run_sim(files, target=True, world_kw={"connect": refuse})
        return CaseOutcome(name, run, files)
    if name == "agent_silent":
        files = write_inputs(root, scenario=scenario_doc([{"wait_ms": 10_000}]))
        return CaseOutcome(name, run_sim(files), files)
    if name == "comfort_noise":
        # Comfort noise never goes quiet (ADR 0026 rule 10): the wait for the agent ends on
        # max_silence. One segment is heard, so the replay holds exactly one body.
        files = write_inputs(root, scenario=scenario_doc([{"comfort": {"frames": 600}}]))
        replay = _replay_dir(root, [HEARD_0])
        args = _swap_stt(base_args_target(files), replay)
        return CaseOutcome(name, run_sim(files, args=args, target=True), files)
    if name == "no_export":
        files = write_inputs(root)
        return CaseOutcome(name, run_sim(files), files)
    if name == "export_present":
        files = write_inputs(root)
        run = run_sim(
            files, add=["--agent-transcript", str(files.export), "--declared-language", "mr"]
        )
        return CaseOutcome(name, run, files)
    if name == "stt_failure":
        files = write_inputs(root)
        for body in (files.root / "stt_replay").glob("chunk-*.json"):
            body.unlink()
        run = run_sim(
            files,
            target=True,
            add=["--agent-transcript", str(files.export), "--declared-language", "mr"],
        )
        return CaseOutcome(name, run, files)
    if name == "kill_switch":
        files = real_files(root)
        clock, signals, sim = VirtualClock(), FakeSignals(), SarvamSim()
        run = run_sim(
            files,
            args=real_args(files, target=True),
            target=True,
            environ=real_env(),
            transport=sim,
            clock=clock,
            world_kw={"signals": signals, "connect": fire_at(clock, signals, 700, files)},
        )
        return CaseOutcome(name, run, files, sim)
    if name == "max_duration":
        steps = [{"wait_ms": 300}, {"speak": {"frames": 400, "level": 1000, "text": HEARD_0}}]
        files = write_inputs(
            root, evaluator=evaluator_doc(max_duration_ms=1500), scenario=scenario_doc(steps)
        )
        return CaseOutcome(name, run_sim(files), files)
    if name == "max_silence":
        steps = [
            {"wait_ms": 300},
            {"speak": {"frames": 10, "level": 1000, "text": HEARD_0}},
            {"wait_ms": 20_000},
        ]
        files = write_inputs(root, scenario=scenario_doc(steps))
        return CaseOutcome(name, run_sim(files), files)
    if name == "spend_cap":
        files = write_inputs(
            root, sim_profile=profile_doc(tts="sarvam", stt="sarvam", caller=llm_caller())
        )
        args = real_args(files)
        args[args.index("--caller") + 1] = "sarvam"
        args += ["--max-llm-tokens", "60"]
        sim = SarvamSim(chat=[LINE_0, LINE_1])
        run = run_sim(files, args=args, environ=real_env(), transport=sim)
        return CaseOutcome(name, run, files, sim)
    raise AssertionError(f"unknown case {name}")


def base_args_target(files: Files) -> list[str]:
    from cli_p4e_support import base_args  # type: ignore[import-not-found,unused-ignore]

    args: list[str] = base_args(files, target=True)
    return args


# ---------------------------------------------------------------- shared shape assertions

ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)")
TYPED_ID = re.compile(
    r"\b[a-z]{2,5}_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def mask(text: str) -> str:
    """Identifiers and instants replaced by fixed words (the bytes that remain are stable)."""
    return ISO_TIME.sub("<TIME>", TYPED_ID.sub("<ID>", text))


def describe(result: Any) -> tuple[str, Any]:
    """``(status, detail)`` of a stored result: the value, the missing kinds or the error code."""
    if result.status == "scored":
        return ("scored", result.value)
    if result.status == "not_applicable":
        return ("not_applicable", (result.na_reason, tuple(result.missing_kinds)))
    return (result.status, result.error.code)


def sim_mr_scenario(index: int = 0) -> Mapping[str, Any]:
    document = json.loads(SIM_MR.read_text("utf-8"))
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[index]
    assert isinstance(scenario, Mapping)
    return scenario
