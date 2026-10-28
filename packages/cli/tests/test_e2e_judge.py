# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-J24: the offline end-to-end run of the judge (ADR 0030 rules 8, 19, 20 and 22 and invariants
I6 and I7; the batch E milestone M1 and the Phase 3 ``test_e2e_synthetic_mr`` as models).

Contract: docs/phase4/api-surface-batch-j2.md section 15. A synthetic Marathi scripted call (the
scenario ``sim-mr-s1`` of ``sim_mr.json`` and the ten items of ``golden_mr.json``), a fake
text-to-speech, a fake agent that says one planted reply for each question, a scenario
speech-to-text, a fake judge that answers each planted reply with the label the apparatus intends
for it, and fake human labels. The exact lines of the command, the exact documents of the judge,
the exact text of the judge section of the page, the exact packet ranges and the exact agreement
report are pinned, and the three views (output, page, store) and the agreement report are shown to
agree. Nothing here was reviewed by a native speaker (BL-169); nothing is a claim about any agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cli_p4e_support import STAMP  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_e2e import E2E, labelled, run_e2e  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CLAIM_WORDS,
    judge_doc,
    judge_results,
    scrub,
    section_text,
)

#: standard output of ``tracer simulate`` with ids and paths replaced
STDOUT = [
    "result: call established",
    "end reason: max_silence",
    "outcome: not evaluated",
    "dialect: pipecat-protobuf/1",
    "caller audio: synthetic (generated speech, not a person)",
    "caller turns: 10 started, 10 completed",
    "agent segments: 9",
    "first agent audio: 1800 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)",
    "gap after the caller's line (median): 200 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms, N 9)",
    "overlap: 0 ms (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)",
    "answered caller turns: 9 (observed at the simulator over pipecat-protobuf/1, quantum 20 ms)",
    "agreement: not scored (missing input agent_transcript)",
    "judge: uncalibrated signal (golden set mr_apparatus_set, engine local_loopback/1, model judge-model-1)",
    "judge agrees: 5 of 10 items",
    "judge disagrees: 4 of 10 items",
    "judge cannot_tell: 0 of 10 items",
    "judge error: 1 of 10 items",
    "judge not applicable: 0 of 10 items",
    "judge item mr_q00: agrees (reason all_facts_stated; facts 2 of 2 stated, 0 contradicted, 0 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q01: agrees (reason all_facts_stated; facts 1 of 1 stated, 0 contradicted, 0 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q02: disagrees (reason fact_contradicted; facts 0 of 1 stated, 1 contradicted, 0 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q03: disagrees (reason answer_off_topic; facts 0 of 1 stated, 0 contradicted, 1 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q04: disagrees (reason fact_absent; facts 1 of 2 stated, 0 contradicted, 1 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q05: agrees (reason all_facts_stated; facts 1 of 1 stated, 0 contradicted, 0 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q06: agrees (reason meaning_same; requests 1)",
    "judge item mr_q07: disagrees (reason fact_contradicted; facts 0 of 2 stated, 2 contradicted, 0 absent, 0 cannot_tell; requests 1)",
    "judge item mr_q08: agrees (reason meaning_same; requests 1)",
    "judge item mr_q09: error (answer_window_empty; requests 0)",
    "tts usage: none",
    "llm usage: none",
    "stt usage: none",
    "judge usage: 9 requests, 1080 input tokens, 135 output tokens",
    "cost: not available (no_price_for_model)",
    "ingest warnings: 0",
    "bundle: rbnd_ID",
    "store: ROOT/out/tracer.sqlite",
    "recording: ROOT/out/recording-20261027T120000Z.wav",
    "report: ROOT/out/report-20261027T120000Z.html",
]
#: the SHA-256 of the exchange file of the last attempt of each item (none for the empty window)
EXCHANGES = [
    "79545be7ccee1adab305262ccf6361ab8daff13353c0e2900b962d89f5ab776e",
    "9568fa09dc44786d422eebee86ec053646441013dae8bba28f10f17fa8f57ed5",
    "ea35e078fcdc1515455842a355ebb58527c023a2530d8e017c71ee597cab4935",
    "2bd7815ec649ac5898b9bc9a573b3681a383b4917f1fa3102401b27022d3b426",
    "500f57312c8f5ee3e29234c1a321de5f213e3e20bd15385f052e45a924933310",
    "9e2d3ea009d576b253e8e146037d94e0d008c01a88d8daf4b3ccef165fe43698",
    "b2b9dfeb628d8921b89603346e737c8738dab7ae06becf163429c6de87fd38b6",
    "30e965d6ad0f1a564cb928da573134bb3795e05c7fa08a874cac1146a5269916",
    "9868da406ecdc5debc547a05349befa33eaac9fb331ac9b968cdec3a0fd5b1e0",
    None,
]
JUDGE_SECTION = (
    "Uncalibrated judge signal This section is an uncalibrated judge signal: what a language model "
    "said about whether the agent's answer to each golden question agrees with the expected facts. It"
    " is a signal to look at, not a measurement of the agent. Each count is shown with the number of "
    "items it is out of, and nothing here estimates how far the signal can be relied on. judge "
    "uncalibrated signal (golden set mr_apparatus_set, engine local_loopback/1, model judge-model-1) "
    "judge agrees 5 of 10 items judge disagrees 4 of 10 items judge cannot_tell 0 of 10 items judge "
    "error 1 of 10 items judge not applicable 0 of 10 items mr_q00 agrees (reason all_facts_stated; "
    "facts 2 of 2 stated, 0 contradicted, 0 absent, 0 cannot_tell; requests 1) answer window: agent "
    "turns 0 mr_q01 agrees (reason all_facts_stated; facts 1 of 1 stated, 0 contradicted, 0 absent, 0"
    " cannot_tell; requests 1) answer window: agent turns 1 mr_q02 disagrees (reason "
    "fact_contradicted; facts 0 of 1 stated, 1 contradicted, 0 absent, 0 cannot_tell; requests 1) "
    "answer window: agent turns 2 mr_q03 disagrees (reason answer_off_topic; facts 0 of 1 stated, 0 "
    "contradicted, 1 absent, 0 cannot_tell; requests 1) answer window: agent turns 3 mr_q04 disagrees"
    " (reason fact_absent; facts 1 of 2 stated, 0 contradicted, 1 absent, 0 cannot_tell; requests 1) "
    "answer window: agent turns 4 mr_q05 agrees (reason all_facts_stated; facts 1 of 1 stated, 0 "
    "contradicted, 0 absent, 0 cannot_tell; requests 1) answer window: agent turns 5 mr_q06 agrees "
    "(reason meaning_same; requests 1) answer window: agent turns 6 mr_q07 disagrees (reason "
    "fact_contradicted; facts 0 of 2 stated, 2 contradicted, 0 absent, 0 cannot_tell; requests 1) "
    "answer window: agent turns 7 mr_q08 agrees (reason meaning_same; requests 1) answer window: "
    "agent turns 8 mr_q09 error (answer_window_empty; requests 0) answer window: no agent turn"
)
RANGES = [
    {"start_ms": 1600, "end_ms": 2180},
    {"start_ms": 4220, "end_ms": 4800},
    {"start_ms": 5960, "end_ms": 6540},
    {"start_ms": 7260, "end_ms": 7840},
    {"start_ms": 8520, "end_ms": 9100},
    {"start_ms": 10900, "end_ms": 11480},
    {"start_ms": 12080, "end_ms": 12660},
    {"start_ms": 13300, "end_ms": 13880},
    {"start_ms": 14840, "end_ms": 15420},
    {"start_ms": 16100, "end_ms": 19080},
]
RECORDING_SHA256 = "dffca8dde3affbe161b750f612ec746187393049f1ceabf5a60f5e46f79296d0"
CALL_END_MS = 19080

#: what the apparatus intends for each item (from the planted replies of the fixture)
INTENDED = [
    ("agrees", "all_facts_stated", [(0, "stated"), (1, "stated")]),
    ("agrees", "all_facts_stated", [(0, "stated")]),
    ("disagrees", "fact_contradicted", [(0, "contradicted")]),
    ("disagrees", "answer_off_topic", [(0, "absent")]),
    ("disagrees", "fact_absent", [(0, "stated"), (1, "absent")]),
    ("agrees", "all_facts_stated", [(0, "stated")]),
    ("agrees", "meaning_same", []),
    ("disagrees", "fact_contradicted", [(0, "contradicted"), (1, "contradicted")]),
    ("agrees", "meaning_same", []),
    (None, None, []),
]
VERDICTS = ("agrees", "disagrees", "cannot_tell")
ROWS = ("agrees", "disagrees", "cannot_tell", "no verdict", "not applicable")
#: the judge's row for each item, in order
JUDGE_ROW = ["agrees", "agrees", "disagrees", "disagrees", "disagrees", "agrees", "agrees",
             "disagrees", "agrees", "no verdict"]  # fmt: skip


def _cells(judge: list[str], human: dict[str, str | None]) -> dict[tuple[str, str], int]:
    table = {(j, h): 0 for j in ROWS for h in VERDICTS}
    for n, row in enumerate(judge):
        verdict = human.get(f"mr_q{n:02d}")
        if verdict is not None:
            table[(row, verdict)] += 1
    return table


def test_e2e_judge_synthetic_mr_exact_outputs(tmp_path: Path) -> None:
    """The exact bytes of the offline end-to-end run: the lines of the command, the judge documents
    (verdict, reason, fact statuses, the window, the exchange hash), the judge section of the page,
    the packet and the agreement report. The verdicts are the labels the apparatus intends for the
    planted replies, so this is also a check of the whole chain against known answers."""
    e = run_e2e(tmp_path)
    assert e.run.code == 2, (e.run.out, e.run.err)  # item 9 is an error: the agent was silent
    assert e.run.err == ""
    assert scrub(e.run.out, tmp_path).splitlines() == STDOUT
    assert len(e.sim.requests) == 9
    bundle = e.run.bundle()
    results = judge_results(bundle)
    assert list(results) == [f"mr_q{n:02d}" for n in range(10)]
    for n, ((item, result), (verdict, reason, facts)) in enumerate(
        zip(results.items(), INTENDED, strict=True)
    ):
        document = judge_doc(result)
        assert document == {
            "format": "tracer.judge/1",
            "verdict": verdict,
            "reason_code": reason,
            "facts": [{"index": i, "status": s} for i, s in facts],
            "window_turn_seqs": [n] if verdict is not None else [],
            "excluded_turns": n if n < 9 else 9,
            "attempts": 1 if verdict is not None else 0,
            "regenerated": False,
            "exchange_sha256": EXCHANGES[n],
            "calibration": "uncalibrated",
            "confidence": "none_claimed",
        }, item
        if verdict is None:
            assert (result.status, result.value, result.error.code) == (
                "error",
                None,
                "answer_window_empty",
            )
        else:
            assert (result.status, result.value, result.passed) == ("scored", verdict, None)
            assert result.judge is not None
            assert result.judge.confidence is None
            assert result.judge.reported_model == "judge-model-1"
            assert [r.kind for r in result.evidence_refs] == ["turn"]
    # the exchange file named by each hash is the file on disk
    directory = e.files.out / "judge" / str(bundle.run.id)
    for n in range(9):
        assert (
            hashlib.sha256((directory / f"item-mr_q{n:02d}-1.json").read_bytes()).hexdigest()
            == EXCHANGES[n]
        )
    # the page
    page = e.run.report()
    assert section_text(page, "judge") == JUDGE_SECTION
    assert section_text(page, "outcome-label").startswith("Outcome not evaluated")
    assert (
        re.sub(r"\s+", " ", section_text(page, "usage-judge"))
        == "9 requests, 1080 input tokens, 135 output tokens"
    )
    assert (e.files.out / f"report-{STAMP}.html").is_file()
    # the packet and the labels
    commands = labelled(e)
    assert [c.code for c in commands] == [0, 0, 0, 0]
    packet = json.loads(next((e.files.out / "labels").glob("packet-*.json")).read_text("utf-8"))
    assert [i["audio_range_ms"] for i in packet["items"]] == RANGES
    assert (
        packet["items"][0]["audio_range_ms"]["end_ms"]
        <= packet["items"][1]["audio_range_ms"]["start_ms"]
    )
    assert packet["items"][9]["audio_range_ms"]["end_ms"] == CALL_END_MS
    assert packet["recording"] == {"sha256": RECORDING_SHA256, "channel": "agent"}
    assert packet["golden_set"]["sha256"] == hashlib.sha256(e.jf.golden.read_bytes()).hexdigest()
    assert [i["stt_text"] for i in packet["items"]] == [p["text"] for p in e.plants]
    # the agreement report, built here from the labels and the judge rows alone
    ana = {"mr_q00": "agrees", "mr_q01": "agrees", "mr_q02": "disagrees", "mr_q03": "cannot_tell",
            "mr_q04": "disagrees", "mr_q05": "agrees", "mr_q06": "agrees", "mr_q07": "disagrees",
            "mr_q08": None}  # fmt: skip
    bob = {"mr_q00": "agrees", "mr_q02": "disagrees", "mr_q04": "agrees", "mr_q05": None,
            "mr_q06": "agrees", "mr_q07": "disagrees", "mr_q09": "disagrees"}  # fmt: skip
    expected = [
        "agreement report: counts of an uncalibrated judge signal against human labels",
        f"bundle: {commands[-1].lines[1].split(': ')[1]}",
        "items: 10",
    ]
    for name, labels, basis in (
        ("ana", ana, "audio 8, stt_text 0, both 0"),
        ("bob", bob, "audio 0, stt_text 6, both 0"),
    ):
        table = _cells(JUDGE_ROW, labels)
        expected.append(f"labeller: {name} (basis {basis})")
        expected += [
            f"judge {j}, human {h}: {table[(j, h)]} of 10 items" for j in ROWS for h in VERDICTS
        ]
        unlabelled = 10 - sum(v is not None for v in labels.values())
        expected.append(
            f"unlabelled by {name}: {unlabelled} of 10 items (not counted as agreement or disagreement)"
        )
    both = {(a, b): 0 for a in VERDICTS for b in VERDICTS}
    one = neither = 0
    for n in range(10):
        a, b = ana.get(f"mr_q{n:02d}"), bob.get(f"mr_q{n:02d}")
        if a is not None and b is not None:
            both[(a, b)] += 1
        elif a is None and b is None:
            neither += 1
        else:
            one += 1
    expected += [
        f"ana {a}, bob {b}: {both[(a, b)]} of 10 items" for a in VERDICTS for b in VERDICTS
    ]
    expected += [
        f"labelled by one only: {one} of 10 items",
        f"labelled by neither: {neither} of 10 items",
    ]
    assert (
        scrub(commands[-1].out, tmp_path).replace(commands[-1].lines[1].split(": ")[1], "rbnd_ID")
        == "\n".join(
            line.replace(commands[-1].lines[1].split(": ")[1], "rbnd_ID") for line in expected
        )
        + "\n"
    )
    assert CLAIM_WORDS.search(commands[-1].out) is None


def test_e2e_judge_report_store_and_agreement_consistent(tmp_path: Path) -> None:
    """The same facts in every place: the lines of the command, the judge section of the page, the
    judge results of the stored bundle and the agreement report give the same counts and the same
    verdict for every item; the stored bundle is the one the store returns, byte for byte; the
    counts of the agreement report add up to the number of items; nothing the judge produced is in
    the packet."""
    e: E2E = run_e2e(tmp_path)
    bundle = e.run.bundle()
    results = judge_results(bundle)
    by_status = {"agrees": 0, "disagrees": 0, "error": 0}
    for result in results.values():
        by_status[result.value if result.status == "scored" else "error"] += 1
    assert by_status == {"agrees": 5, "disagrees": 4, "error": 1}
    for label, key in (
        ("judge agrees", "agrees"),
        ("judge disagrees", "disagrees"),
        ("judge error", "error"),
    ):
        assert e.run.value(label) == f"{by_status[key]} of 10 items"
    section = section_text(e.run.report(), "judge")
    for label, key in (
        ("judge agrees", "agrees"),
        ("judge disagrees", "disagrees"),
        ("judge error", "error"),
    ):
        assert f"{label} {by_status[key]} of 10 items" in section
    for item, result in results.items():
        line = e.run.value(f"judge item {item}")
        assert line.startswith(result.value if result.status == "scored" else "error")
        assert f"{item} {line}" in section
    # the store returns the bundle that was built
    reread = e.run.bundle()
    assert reread.model_dump_json() == bundle.model_dump_json()
    commands = labelled(e)
    assert [c.code for c in commands] == [0, 0, 0, 0]
    report = commands[-1].lines
    counts = [int(m.group(1)) for line in report if (m := re.search(r": (\d+) of 10 items", line))]
    judge_cells = [line for line in report if line.startswith("judge ")]
    assert len(judge_cells) == 30  # 15 cells for each of the two labellers
    for name, labelled_items in (("ana", 8), ("bob", 6)):
        block = [
            line
            for line in report[
                report.index(next(x for x in report if x.startswith(f"labeller: {name}"))) :
            ]
            if line.startswith("judge ")
        ][:15]
        unlabelled = next(
            int(x.split(": ")[1].split(" ")[0])
            for x in report
            if x.startswith(f"unlabelled by {name}")
        )
        assert sum(int(x.split(": ")[1].split(" ")[0]) for x in block) + unlabelled == 10
        assert sum(int(x.split(": ")[1].split(" ")[0]) for x in block) == labelled_items
    assert all(0 <= n <= 10 for n in counts)
    # the judge's rows of the report are the verdicts of the store
    ana = [line for line in report if line.startswith("judge ")][:15]
    row_totals: dict[str, int] = {}
    for line in ana:
        row = line.split(", human ")[0].removeprefix("judge ")
        row_totals[row] = row_totals.get(row, 0) + int(line.split(": ")[1].split(" ")[0])
    assert row_totals["agrees"] <= by_status["agrees"]
    assert row_totals["disagrees"] <= by_status["disagrees"]
    assert row_totals["no verdict"] <= by_status["error"]
    # blind: the packet holds nothing of what the judge said
    packet_text = next((e.files.out / "labels").glob("packet-*.json")).read_text("utf-8")
    for result in results.values():
        document = judge_doc(result)
        if document["exchange_sha256"]:
            assert document["exchange_sha256"] not in packet_text
    for word in (
        "all_facts_stated",
        "fact_absent",
        "fact_contradicted",
        "meaning_same",
        "answer_off_topic",
        "answer_window_empty",
    ):
        assert word not in packet_text, word
    assert scrub(packet_text, tmp_path).count("rbnd_ID") == 2
