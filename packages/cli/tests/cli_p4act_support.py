# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the batch activity cli tests (BL-313, ``turn_taking.activity_min_peak``).
Support module, not collected. Contract: docs/phase4/api-surface-batch-activity.md section 10.

It builds on ``cli_p4e_support`` and ``cli_p4f_support`` (the in-process run of ``tracer simulate``
with a virtual clock and the in-process fake agent, no socket). The noise-floor call of the field
case is played by the fake agent of ``tracer-evalsim``: its comfort noise is the quietest signal
(a PCM value of 1, the mu-law byte 0xFE which decodes to 8) and its speech is a level of 1000, so a
threshold of 2 (PCM) or 9 (mu-law) separates the two.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    HEARD_1,
    LINE_0,
    Files,
    SimRun,
    evaluator_doc,
    profile_doc,
    run_sim,
    scenario_doc,
    write_inputs,
)
from cli_p4f_support import (  # type: ignore[import-not-found,unused-ignore]
    base_args_target,
)

__all__ = [
    "ONE_LINE",
    "Files",
    "SimRun",
    "control_run",
    "field_files",
    "field_run",
    "field_steps",
    "gap_line",
    "turn_taking",
]

#: one scripted caller line (14 frames of the fake voice, 280 ms) so the script is complete after
#: the first reply of the agent that follows it
ONE_LINE = evaluator_doc(scripted_turns=[{"index": 0, "text": LINE_0, "language": "mr"}])


def turn_taking(
    peak: Any = "absent", *, first: bool = False, wait: int = 300, silence: int = 3000
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "caller_speaks_first": first,
        "wait_for_agent_silence_ms": wait,
        "max_silence_ms": silence,
    }
    if peak != "absent":
        section["activity_min_peak"] = peak
    return section


def field_steps(*, noise_between: int = 150) -> list[dict[str, Any]]:
    """Comfort noise (the quietest signal) in every pause, speech at 500 to 680 ms and again after
    ``noise_between`` noise frames (at 3700 to 3780 ms by default), then noise for good."""
    return [
        {"comfort": {"frames": 25}},
        {"speak": {"frames": 10, "level": 1000, "text": HEARD_0}},
        {"comfort": {"frames": noise_between}},
        {"speak": {"frames": 5, "level": 1000, "text": HEARD_1}},
        {"comfort": {"frames": 600}},
    ]


def field_files(
    root: Path, peak: Any = "absent", *, dialect: str = "pipecat-protobuf/1", **options: Any
) -> Files:
    return write_inputs(
        root,
        evaluator=ONE_LINE,
        sim_profile=profile_doc(dialect, turn_taking=turn_taking(peak, **options)),
        scenario=scenario_doc(field_steps()),
    )


def field_run(
    root: Path, peak: Any = "absent", *, add: tuple[str, ...] = (), **options: Any
) -> SimRun:
    """The field call against the fake agent with ``--stt fake`` (the scenario's own words)."""
    files = field_files(root, peak, **options)
    return run_sim(files, add=list(add))


def control_run(root: Path, peak: Any = "absent", **options: Any) -> SimRun:
    """The same call with the replay speech-to-text of one body (without a threshold the whole
    stream is one agent segment, so the replay holds exactly one body)."""
    files = field_files(root, peak, **options)
    folder = root / "stt_replay_one"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "chunk-0000.json").write_text(
        json.dumps({"request_id": "replay-0", "transcript": HEARD_0, "language_code": "mr-IN"}),
        "utf-8",
    )
    args = base_args_target(files)
    args[args.index("--stt") + 1] = f"fake:{folder}"
    return run_sim(files, args=args, target=True)


def gap_line(run: SimRun) -> re.Match[str]:
    line = run.value("gap after the caller's line (median)")
    found = re.fullmatch(r"(\d+) ms \((.*)\)", line)
    assert found is not None, line
    return found
