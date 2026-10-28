# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-J24 (support): the offline speech-to-text of a fake agent (``--stt fake``) gives its turns the
time of their chunk when the run is judged (contract docs/phase4/api-surface-batch-j2.md section 9.5).

A judged run cuts the agent channel into the answer window of each question, by the start time of
the agent's turns. The scenario speech-to-text of batch E returned the words of each segment and no
time, so every offline item would have been ``answer_window_empty``. In a run with a golden set it now
answers with one phrase that spans its chunk, as the provider does when it is asked for timestamps:
the turn starts where the agent's segment starts and ends where it ends. A run without a golden set
is byte for byte what batch E and batch F pin (the turns have no time). It makes no request and claims
no usage in either case.
"""

from __future__ import annotations

from pathlib import Path

from cli_p4e_support import HEARD_0, HEARD_1, run_sim, write_inputs  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    config_doc,
    judge_add,
    run_judged,
    two_answers,
    write_judge_files,
)


def agent_turns(run: object) -> list[tuple[str, int | None, int | None]]:
    bundle = run.bundle()  # type: ignore[attr-defined]
    (stt,) = [t for t in bundle.transcripts if t.source == "our_stt"]
    turns = sorted((t for t in bundle.turns if t.transcript_id == stt.id), key=lambda t: t.seq)
    return [(t.text, t.start_ms, t.end_ms) for t in turns]


def test_scenario_stt_turns_carry_the_time_of_their_chunk_in_a_judged_run(tmp_path: Path) -> None:
    run, _, _ = run_judged(tmp_path, engine="fake:agrees")
    assert run.code == 0, (run.out, run.err)
    assert agent_turns(run) == [(HEARD_0, 600, 700), (HEARD_1, 2200, 2300)]
    bundle = run.bundle()
    (stt,) = [t for t in bundle.transcripts if t.source == "our_stt"]
    plan = stt.metadata["tracer.chunk_plan"]
    rate = plan["sample_rate_hz"]
    spans = [
        (c["start_frame"] * 1000 // rate, c["end_frame"] * 1000 // rate) for c in plan["chunks"]
    ]
    turns = sorted((t for t in bundle.turns if t.transcript_id == stt.id), key=lambda t: t.seq)
    assert len(spans) == 2
    for turn, (start, end) in zip(turns, spans, strict=True):
        assert turn.start_ms == start, "the turn starts where the chunk starts"
        assert turn.end_ms is not None
        assert turn.end_ms <= end
    assert run.value("stt usage") == "none"


def test_scenario_stt_turns_have_no_time_without_a_golden_set(tmp_path: Path) -> None:
    """Byte-for-byte the batch E behaviour (batch F pins the masked report and bundle hashes of it)."""
    files = write_inputs(tmp_path / "plain", scenario=two_answers())
    run = run_sim(files)
    assert run.code == 0, (run.out, run.err)
    assert agent_turns(run) == [(HEARD_0, None, None), (HEARD_1, None, None)]
    assert run.value("stt usage") == "none"
    other = write_inputs(tmp_path / "judged", scenario=two_answers())
    jf = write_judge_files(other, config=config_doc("fake/1", None))
    judged = run_sim(other, add=judge_add(jf, "fake:agrees"))
    assert agent_turns(judged) == [(HEARD_0, 600, 700), (HEARD_1, 2200, 2300)]
