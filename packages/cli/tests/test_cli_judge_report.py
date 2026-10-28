# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J19: the judge in the command output and in the report (ADR 0030 rules 9 and 10; ADR 0023
amendment A4, the claim words of the judge).

Contract: docs/phase4/api-surface-batch-j2.md section 9. Every judge line says that the signal is
uncalibrated; per item the verdict, the reason code, the fact statuses and "n of N" counts with
their denominators; no rate, no percent, no confidence; none of the claim words of ADR 0023 rule 1,
A1 and A4; ``outcome not evaluated`` is kept and no verdict of the run exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CLAIM_WORDS,
    JudgeSim,
    answer_with,
    judge_argv,
    judge_lines,
    load_bundle,
    run_cmd,
    run_judged,
    section_text,
)

COUNT = re.compile(
    r"^judge (agrees|disagrees|cannot_tell|error|not applicable): (\d+) of (\d+) items$"
)


def _mixed(tmp_path: Path) -> tuple[Any, Any, Any]:
    """Item q0 disagrees and item q1 agrees (two requests of the loopback engine)."""
    sim = JudgeSim([answer_with("disagrees"), answer_with("agrees")])
    run, files, jf = run_judged(tmp_path, engine="local_loopback/1", sim=sim)
    assert run.code == 0, (run.out, run.err)
    return run, files, jf


def test_judge_lines_labelled_uncalibrated_everywhere(tmp_path: Path) -> None:
    """ADR 0030 rule 9 and invariant I3: the output of ``tracer simulate``, the page, the dry run,
    the output of ``tracer judge``, the agreement report and every stored judge record say
    ``uncalibrated``; no record carries a confidence."""
    run, files, _jf = _mixed(tmp_path)
    block = judge_lines(run.out)
    assert block[0].startswith("judge: uncalibrated signal (golden set mr_p4j2_set, engine ")
    assert sum(1 for line in block if line.startswith("judge: ")) == 1
    # the page
    page = run.report()
    judge_section = section_text(page, "judge")
    assert judge_section.startswith("Uncalibrated judge signal")
    assert 'id="judge"' in page
    assert 'data-calibration="uncalibrated"' in page
    assert "uncalibrated judge signal" in judge_section.lower()
    # the dry run, the command that re-judges and the agreement report
    dry = run_judged(tmp_path / "dry", engine="fake:agrees", extra=["--dry-run"])[0]
    assert "judge: uncalibrated signal, engine fake:agrees, model judge-model-1" in dry.lines
    fake_run, fake_files, fake_jf = run_judged(tmp_path / "fake", engine="fake:agrees")
    again = run_cmd(judge_argv(fake_files.out, fake_run.value("bundle"), fake_jf, "fake:disagrees"))
    assert again.code == 0, (again.out, again.err)
    assert judge_lines(again.out)[0].startswith("judge: uncalibrated signal (golden set ")
    for command in (
        [
            "labels",
            "packet",
            "--out",
            str(fake_files.out),
            "--bundle",
            fake_run.value("bundle"),
            "--golden",
            str(fake_jf.golden),
        ],
    ):
        assert run_cmd(command).code == 0
    packet = next((fake_files.out / "labels").glob("packet-*.json"))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(json.loads(packet.read_text("utf-8"))["labels_template"]), "utf-8")
    assert (
        run_cmd(["labels", "check", "--out", str(fake_files.out), "--labels", str(labels)]).code
        == 0
    )
    agreement = run_cmd(
        ["labels", "agreement", "--out", str(fake_files.out), "--bundle", fake_run.value("bundle")]
    )
    assert agreement.code == 0, (agreement.out, agreement.err)
    assert "uncalibrated" in agreement.lines[0]
    # the stored records
    bundle = load_bundle(files.out, run.value("bundle"))
    profile = bundle.snapshot.metadata["tracer.judge_profile"]
    assert profile["calibration"] == "uncalibrated"
    for result in bundle.results:
        document = (result.metadata or {}).get("tracer.judge")
        if document is not None:
            assert document["calibration"] == "uncalibrated"
            assert document["confidence"] == "none_claimed"
        if result.judge is not None:
            assert result.judge.confidence is None
            assert result.passed is None


@pytest.mark.parametrize(
    "engine",
    ["fake:agrees", "fake:disagrees", "fake:cannot_tell", "fake:invalid", "fake:auth_failure"],
)
def test_judge_report_never_claims_accuracy_or_quality(tmp_path: Path, engine: str) -> None:
    """ADR 0023 amendment A4: the text Tracer writes for the judge (the lines of the command, the
    section of the page) holds none of the claim words of rule 1, A1 and A4, no percent sign and
    no decimal number; an error is called an error."""
    run, _files, _ = run_judged(tmp_path, engine=engine)
    texts = {
        "lines": "\n".join(judge_lines(run.out)),
        "section": section_text(run.report(), "judge"),
    }
    for name, text in texts.items():
        assert text, name
        assert CLAIM_WORDS.search(text) is None, (name, CLAIM_WORDS.search(text))
        assert "%" not in text, name
        assert re.search(r"\d\.\d", text) is None, name
        for word in ("rate", "rates", "score", "kappa", "percent", "likely", "reliable"):
            assert re.search(rf"\b{word}\b", text.lower()) is None, (name, word)


def test_judge_report_shows_counts_with_denominators(tmp_path: Path) -> None:
    """ADR 0030 rule 9: the counts are "n of N items" with the denominator, they add up to the
    number of items, the fact statuses of an item are counts out of its facts, and the same
    counts are in the page. No ratio and no percent."""
    run, _files, _ = _mixed(tmp_path)
    counts = {}
    for line in judge_lines(run.out):
        found = COUNT.match(line)
        if found:
            counts[found.group(1)] = (int(found.group(2)), int(found.group(3)))
    assert counts == {
        "agrees": (1, 2),
        "disagrees": (1, 2),
        "cannot_tell": (0, 2),
        "error": (0, 2),
        "not applicable": (0, 2),
    }
    assert sum(n for n, _ in counts.values()) == 2
    assert run.value("judge item q0") == (
        "disagrees (reason fact_absent; facts 0 of 1 stated, 0 contradicted, 1 absent, "
        "0 cannot_tell; requests 1)"
    )
    assert run.value("judge item q1") == "agrees (reason meaning_same; requests 1)"
    assert run.value("judge usage") == "2 requests, 240 input tokens, 30 output tokens"
    section = section_text(run.report(), "judge")
    for label, (n, total) in {
        "judge agrees": (1, 2),
        "judge disagrees": (1, 2),
        "judge cannot_tell": (0, 2),
        "judge error": (0, 2),
        "judge not applicable": (0, 2),
    }.items():
        assert f"{label} {n} of {total} items" in section, label
    assert "facts 0 of 1 stated, 0 contradicted, 1 absent, 0 cannot_tell" in section
    assert "answer window: agent turns 0" in section
    for line in judge_lines(run.out):
        assert "/" not in line.replace("fake/1", "").replace("local_loopback/1", "")
    page = run.report()
    assert "2 requests, 240 input tokens, 30 output tokens" in section_text(page, "usage-judge")


def test_judge_report_states_outcome_not_evaluated(tmp_path: Path) -> None:
    """ADR 0030 rule 10 and invariant I4: a judged run still says ``outcome: not evaluated`` in the
    output and on the page, keeps the inert placeholder as the only required rubric item, and
    produces no verdict of the run."""
    run, _files, _ = _mixed(tmp_path)
    assert run.value("outcome") == "not evaluated"
    page = run.report()
    assert section_text(page, "outcome-label").startswith("Outcome not evaluated")
    bundle = run.bundle()
    assert bundle.verdict is None
    required = [item for item in bundle.snapshot.rubric.items if item.required]
    assert len(required) == 1
    keys = {d.id: d.key for d in bundle.snapshot.metric_definitions}
    binding = {b.id: b for b in bundle.snapshot.metric_bindings}
    assert keys[binding[required[0].binding.id].metric.id] == "simulated_call_no_outcome_check"
    for item in bundle.snapshot.rubric.items:
        if item not in required:
            assert keys[binding[item.binding.id].metric.id] != "simulated_call_no_outcome_check"
    judge_defs = [
        d for d in bundle.snapshot.metric_definitions if d.key.startswith("judge_golden_")
    ]
    assert [d.pass_rule for d in judge_defs] == [None, None]
    assert [d.kind for d in judge_defs] == ["llm_judge", "llm_judge"]
    assert bundle.run.metadata["tracer.sim_outcome"]["outcome"] == "not_evaluated"


def test_a_run_without_a_golden_set_has_no_judge_section_and_no_judge_line(tmp_path: Path) -> None:
    from cli_p4e_support import run_sim, write_inputs  # type: ignore[import-not-found,unused-ignore]

    run = run_sim(write_inputs(tmp_path))
    assert run.code == 0
    assert judge_lines(run.out) == []
    page = run.report()
    assert 'id="judge"' not in page
    assert 'id="usage-judge"' not in page
    assert "judge" not in section_text(page, "usage").lower()
