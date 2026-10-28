# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""The offline end-to-end run of batch J part 2 (P4-J24): a synthetic Marathi scripted call from the
shared fixtures (``sim_mr.json`` scenario ``sim-mr-s1`` and ``golden_mr.json``), a fake text-to-speech
(a tone), a fake agent that says one planted reply for each question, a scenario speech-to-text, a
fake judge that answers each planted reply with the label the apparatus intends for it, and fake
human labels. Support module, not collected. Contract: docs/phase4/api-surface-batch-j2.md section
15. Every text is invented and none has been reviewed by a native speaker (BL-169).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    NOW,
    Files,
    SimRun,
    evaluator_doc,
    run_sim,  # type: ignore[import-not-found,unused-ignore]
    scenario_doc,
    write_inputs,
)
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    LABELS_FORMAT,
    JudgeFiles,
    JudgeSim,
    chat_response,
    config_doc,
    loopback_add,
    run_cmd,
    scrub,
    user_document,
    write_judge_files,
)

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
FIXTURES = ROOT / "packages" / "evalcore" / "tests" / "fixtures"
#: the kind of planted agent reply used for each of the ten items (the last is no reply at all)
KINDS = (
    "exact_facts", "paraphrase", "fact_contradicted", "off_topic", "fact_missing",
    "number_words", "exact_facts", "fact_contradicted", "paraphrase", "empty",
)  # fmt: skip


@dataclass
class E2E:
    run: SimRun
    files: Files
    jf: JudgeFiles
    sim: JudgeSim
    plants: list[dict[str, Any]]
    golden: dict[str, Any]


def fixtures() -> tuple[dict[str, Any], dict[str, Any]]:
    golden = json.loads((FIXTURES / "golden_mr.json").read_text("utf-8"))
    sim = json.loads((FIXTURES / "sim_mr.json").read_text("utf-8"))
    return golden, sim


def planted() -> list[dict[str, Any]]:
    golden, _ = fixtures()
    chosen = []
    for index, kind in enumerate(KINDS):
        item = f"mr_q{index:02d}"
        (found,) = [p for p in golden["plants"] if p["item_id"] == item and p["kind"] == kind]
        chosen.append(found)
    return chosen


def agent_steps(lines: list[str], plants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The agent says each planted reply 200 ms after the caller's line ends and 100 ms long; the
    caller's next line follows about 300 ms after the agent is quiet. The last item has no reply."""
    steps: list[dict[str, Any]] = []
    for index, (line, plant) in enumerate(zip(lines, plants, strict=True)):
        if not plant["text"]:
            continue
        length = 40 * len(line)
        steps.append({"wait_ms": length + (200 if index == 0 else 480)})
        steps.append({"speak": {"frames": 5, "level": 1000, "text": plant["text"]}})
    return steps


def fake_model(plants: list[dict[str, Any]]) -> JudgeSim:
    """A judge that answers each planted reply with the label the apparatus intends for it."""
    by_text = {p["text"]: p for p in plants}

    def answer(request: httpx.Request, index: int) -> httpx.Response:
        document = user_document(request)
        plant = by_text[document["agent_answer"]]
        facts = [{"index": n, "status": s} for n, s in enumerate(plant["intended_facts"])]
        reply = {
            "verdict": plant["intended_verdict"],
            "reason_code": plant["intended_reason_code"],
            "facts": facts,
        }
        return chat_response(json.dumps(reply), rid=f"chatcmpl-{index}")  # type: ignore[no-any-return]

    return JudgeSim([answer] * 10)


def run_e2e(root: Path) -> E2E:
    golden_fixture, sim_fixture = fixtures()
    (scenario,) = [s for s in sim_fixture["scenarios"] if s["id"] == "sim-mr-s1"]
    lines = [t["text"] for t in scenario["scripted_turns"]]
    plants = planted()
    evaluator = evaluator_doc(
        name="synthetic helpline e2e",
        scripted_turns=scenario["scripted_turns"],
        max_duration_ms=120_000,
    )
    files = write_inputs(
        root,
        evaluator=evaluator,
        scenario=scenario_doc(agent_steps(lines, plants)),
    )
    golden = golden_fixture["golden_set"]
    jf = write_judge_files(files, golden=golden, config=config_doc("local_loopback/1"))
    sim = fake_model(plants)
    run = run_sim(files, add=loopback_add(jf, requests=20, tokens=100_000), transport=sim)
    return E2E(run, files, jf, sim, plants, golden)


def human_labels(bundle: str) -> dict[str, dict[str, Any]]:
    """Two fake labellers over the ten items: ana follows the apparatus except for q03 (cannot
    tell) and q08 (left out) and q09 (not listed); bob listens to the text only and sees q04 the
    other way round."""
    ana = {
        "q00": "agrees", "q01": "agrees", "q02": "disagrees", "q03": "cannot_tell",
        "q04": "disagrees", "q05": "agrees", "q06": "agrees", "q07": "disagrees", "q08": None,
    }  # fmt: skip
    bob = {
        "q00": "agrees", "q02": "disagrees", "q04": "agrees", "q05": None, "q06": "agrees",
        "q07": "disagrees", "q09": "disagrees",
    }  # fmt: skip
    docs = {}
    for name, verdicts, basis in (("ana", ana, "audio"), ("bob", bob, "stt_text")):
        docs[name] = {
            "format": LABELS_FORMAT,
            "bundle_id": bundle,
            "labeller": name,
            "labels": [
                {"item_id": f"mr_{k}", "verdict": v, "basis": basis} for k, v in verdicts.items()
            ],
        }
    return docs


def labelled(e2e: E2E) -> list[Any]:
    """Pack, check the two labels files and return the commands' results."""
    bundle = e2e.run.value("bundle")
    results = [
        run_cmd(
            [
                "labels",
                "packet",
                "--out",
                str(e2e.files.out),
                "--bundle",
                bundle,
                "--golden",
                str(e2e.jf.golden),
            ]
        )
    ]
    for name, document in human_labels(bundle).items():
        path = e2e.files.root / f"{name}.json"
        path.write_text(json.dumps(document), "utf-8")
        results.append(
            run_cmd(["labels", "check", "--out", str(e2e.files.out), "--labels", str(path)])
        )
    results.append(
        run_cmd(["labels", "agreement", "--out", str(e2e.files.out), "--bundle", bundle])
    )
    return results


_ = (NOW, scrub)
