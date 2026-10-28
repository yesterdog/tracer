# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J21: the human-label loop at the command line (ADR 0030 rule 22 and invariant I7; SCOPE
Q-J17 to Q-J19): ``tracer labels packet``, ``check`` and ``agreement``.

Contract: docs/phase4/api-surface-batch-j2.md section 8. The packet is blind (it never shows the
judge's verdict, reason or error); a labels file is validated and copied content-addressed (0600
in a 0700 directory) under ``<out>/labels/``; the agreement report gives counts with denominators
only, an unlabelled item is shown apart and never counted as a disagreement, and two labellers
give human-by-human counts. The provider is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from cli_p4e_support import HEARD_0, HEARD_1, LINE_0, LINE_1, NOW  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CLAIM_WORDS,
    LABELS_FORMAT,
    PACKET_FORMAT,
    JudgeSim,
    answer_with,
    golden_doc,
    load_bundle,
    run_cmd,
    run_judged,
    write_judge_files,
)

VERDICTS = ("agrees", "disagrees", "cannot_tell")
JUDGE_ROWS = ("agrees", "disagrees", "cannot_tell", "no verdict", "not applicable")


def four_items() -> dict[str, Any]:
    """Four items on the two caller lines: q0 and q2 on line 0, q1 and q3 on line 1."""
    base = golden_doc()["items"]
    facts = {**base[0], "expected_facts": [HEARD_0, HEARD_1]}
    return golden_doc(  # type: ignore[no-any-return]
        [
            base[0],
            base[1],
            {**facts, "id": "q2"},
            {**base[0], "id": "q3", "scripted_turn_index": 1, "question": LINE_1},
        ]
    )


def judged(root: Path, *, instant: Any = NOW, **kw: Any) -> tuple[Any, Any, Any]:
    """A judged run whose judge says: q0 agrees, q1 disagrees, q2 cannot_tell, q3 agrees."""
    sim = JudgeSim(
        [
            answer_with("agrees"),
            answer_with("disagrees"),
            answer_with("cannot_tell"),
            answer_with("agrees"),
        ]
    )
    run, files, jf = run_judged(
        root, engine="local_loopback/1", golden=four_items(), sim=sim, instant=instant, **kw
    )
    assert run.code == 2, (run.out, run.err)  # cannot_tell is an error result: exit 2
    return run, files, jf


def labels_doc(
    bundle: str, labeller: str, verdicts: dict[str, Any], basis: str = "audio"
) -> dict[str, Any]:
    return {
        "format": LABELS_FORMAT,
        "bundle_id": bundle,
        "labeller": labeller,
        "labels": [{"item_id": k, "verdict": v, "basis": basis} for k, v in verdicts.items()],
    }


def write_labels(root: Path, name: str, document: Any, mode: int = 0o644) -> Path:
    path = root / name
    path.write_text(document if isinstance(document, str) else json.dumps(document), "utf-8")
    path.chmod(mode)
    return path


def packet_of(run: Any, files: Any, jf: Any) -> tuple[Any, dict[str, Any], str]:
    result = run_cmd(
        [
            "labels",
            "packet",
            "--out",
            str(files.out),
            "--bundle",
            run.value("bundle"),
            "--golden",
            str(jf.golden),
        ]
    )
    assert result.code == 0, (result.out, result.err)
    path = Path(result.value("packet"))
    return result, json.loads(path.read_text("utf-8")), path.read_text("utf-8")


def check(files: Any, path: Path) -> Any:
    return run_cmd(["labels", "check", "--out", str(files.out), "--labels", str(path)])


def agreement(files: Any, bundle: str) -> Any:
    return run_cmd(["labels", "agreement", "--out", str(files.out), "--bundle", bundle])


def cell(table: dict[tuple[str, str], int], total: int) -> list[str]:
    return [
        f"judge {j}, human {h}: {table.get((j, h), 0)} of {total} items"
        for j in JUDGE_ROWS
        for h in VERDICTS
    ]


# ----------------------------------------------------------------------------- the packet


def test_label_packet_never_shows_judge_verdict(tmp_path: Path) -> None:
    """ADR 0030 rule 22 and invariant I7: the packet holds, per item, the question, the expected
    facts or answer, the range of the agent channel to listen to (the answer window), our
    speech-to-text of it and an empty label; it holds nothing the judge said. Two runs of the same
    call that the judge judges in opposite ways give the same packet."""
    run, files, jf = judged(tmp_path / "mixed")
    result, packet, text = packet_of(run, files, jf)
    assert result.err == ""
    assert result.value("items") == "4"
    assert result.value("blind") == "the judge's verdict is not in the packet"
    path = Path(result.value("packet"))
    assert path == files.out / "labels" / f"packet-{run.value('bundle')}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert set(packet) == {"format", "bundle_id", "language", "golden_set", "recording", "items",
                           "labels_template"}  # fmt: skip
    assert packet["format"] == PACKET_FORMAT
    assert packet["bundle_id"] == run.value("bundle")
    assert packet["language"] == "mr"
    bundle = load_bundle(files.out, run.value("bundle"))
    assert packet["recording"] == {"sha256": bundle.artifacts[0].sha256, "channel": "agent"}
    assert packet["golden_set"]["key"] == "mr_p4j2_set"
    assert packet["golden_set"]["sha256"] == hashlib.sha256(jf.golden.read_bytes()).hexdigest()
    items = packet["items"]
    assert [i["item_id"] for i in items] == ["q0", "q1", "q2", "q3"]
    for item in items:
        assert set(item) == {"item_id", "question", "match_mode", "expected_facts",
                             "expected_answer", "audio_range_ms", "stt_text"}  # fmt: skip
    assert items[0]["question"] == LINE_0
    assert items[1]["question"] == LINE_1
    assert items[0]["expected_facts"] == [HEARD_0]
    assert items[0]["expected_answer"] is None
    assert items[1]["expected_facts"] is None
    assert items[1]["expected_answer"] == HEARD_1
    assert items[2]["expected_facts"] == [HEARD_0, HEARD_1]
    # the answer window: from the end of the question to the next caller line (or the call end)
    assert [i["audio_range_ms"] for i in items] == [
        {"start_ms": 280, "end_ms": 980},
        {"start_ms": 1260, "end_ms": 2580},
        {"start_ms": 280, "end_ms": 980},
        {"start_ms": 1260, "end_ms": 2580},
    ]
    assert [i["stt_text"] for i in items] == [HEARD_0, HEARD_1, HEARD_0, HEARD_1]
    # an empty label for each item, in a file that `tracer labels check` accepts as it is
    template = packet["labels_template"]
    assert template == {
        "format": LABELS_FORMAT,
        "bundle_id": run.value("bundle"),
        "labeller": "labeller-1",
        "labels": [{"item_id": f"q{n}", "verdict": None, "basis": "audio"} for n in range(4)],
    }
    # nothing the judge said: no verdict word, no reason, no fact status, no error code, no hash
    lowered = text.lower().replace(PACKET_FORMAT, "").replace(LABELS_FORMAT, "")
    for word in (
        "agrees", "cannot_tell", "stated", "contradicted", "absent", "judge", "reason",
        "exchange", "calibrat", "answer_window_empty", "question_not_sent",
        "all_facts_stated", "fact_absent", "meaning_same", "insufficient_information",
        "chatcmpl", "judge-model-1", "local_loopback",
    ):  # fmt: skip
        assert word not in lowered, word
    assert CLAIM_WORDS.search(result.out) is None
    # blind by construction: the same call judged the opposite way gives the same packet
    other_run, other_files, other_jf = run_judged(tmp_path / "other", engine="fake:disagrees")
    assert other_run.code == 0
    _, other, _ = packet_of(other_run, other_files, other_jf)
    assert _normal(other["items"]) == _normal(
        [i for i in packet["items"] if i["item_id"] in ("q0", "q1")]
    )
    # never overwritten
    again = run_cmd(["labels", "packet", "--out", str(files.out), "--bundle", run.value("bundle"),
                     "--golden", str(jf.golden)])  # fmt: skip
    assert (again.code, again.err) == (4, "error: packet_exists\n")
    assert path.read_text("utf-8") == text


def _normal(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, sort_keys=True, ensure_ascii=True)


def test_label_packet_refuses_what_it_cannot_show(tmp_path: Path) -> None:
    run, files, jf = judged(tmp_path)
    other = write_judge_files(files, golden={**four_items(), "key": "mr_other_set"})
    for command, code in (
        (["--golden", str(other.golden)], "golden_set_differs"),
        (["--bundle", "rbnd_01a17f10-4888-7637-86a0-1af7ad452c4e"], "bundle_not_found"),
    ):
        argv = ["labels", "packet", "--out", str(files.out), "--bundle", run.value("bundle"),
                "--golden", str(jf.golden)]  # fmt: skip
        at = argv.index(command[0])
        argv[at + 1] = command[1]
        refused = run_cmd(argv)
        assert (refused.code, refused.err) == (4, f"error: {code}\n")
    assert not (files.out / "labels").exists() or not list((files.out / "labels").iterdir())


# ----------------------------------------------------------------------------- the import


def test_labels_import_content_addressed_and_private(tmp_path: Path) -> None:
    """ADR 0030 rule 22: ``labels check`` validates the file, then copies it to
    ``<out>/labels/labels-<SHA-256 of its bytes>.json`` with mode 0600 in a directory of mode 0700
    (a directory that was open to others is made private); the same file again is not stored
    twice; a changed file has another name; a refused file leaves nothing."""
    run, files, _jf = judged(tmp_path / "run")
    bundle = run.value("bundle")
    source = write_labels(
        tmp_path,
        "ana.json",
        labels_doc(bundle, "ana", {"q0": "agrees", "q1": "agrees", "q2": None}),
    )
    folder = files.out / "labels"
    folder.mkdir(mode=0o755)
    result = check(files, source)
    assert result.code == 0, (result.out, result.err)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    stored = folder / f"labels-{digest}.json"
    assert stored.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700, "the directory is made private"
    assert result.lines == [
        "labels: ok",
        f"bundle: {bundle}",
        "labeller: ana",
        "labelled: 2 of 3 items",
        "unlabelled: 1 of 3 items",
        f"stored: {stored}",
    ]
    assert stat.S_IMODE(source.stat().st_mode) == 0o644
    assert source.read_bytes() == stored.read_bytes()
    # the same file again: nothing new
    again = check(files, source)
    assert again.code == 0
    assert again.lines[-1] == f"stored: {stored} (already stored)"
    assert [p.name for p in folder.iterdir()] == [stored.name]
    # a changed file is another file
    changed = write_labels(
        tmp_path, "ana2.json", labels_doc(bundle, "ana", {"q0": "disagrees", "q1": "agrees"})
    )
    assert check(files, changed).code == 0
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 2
    assert stored.name in names
    # a file already at the content-addressed name with other bytes is never overwritten
    planted = write_labels(tmp_path, "planted.json", labels_doc(bundle, "ana", {"q0": "disagrees"}))
    squat = folder / f"labels-{hashlib.sha256(planted.read_bytes()).hexdigest()}.json"
    squat.write_text("not the labels", "utf-8")
    squat.chmod(0o600)
    names = sorted(p.name for p in folder.iterdir())
    clash = check(files, planted)
    assert (clash.code, clash.err) == (4, "error: labels_file_exists\n")
    assert squat.read_text("utf-8") == "not the labels"
    squat.unlink()
    names = sorted(p.name for p in folder.iterdir())
    # refused: nothing is copied
    refusals = {
        "not-json": ("{", "judge_labels_not_json"),
        "unknown-item": (labels_doc(bundle, "ana", {"zz": "agrees"}), "judge_labels_item_unknown"),
        "unknown-bundle": (
            labels_doc("rbnd_01a17f10-4888-7637-86a0-1af7ad452c4e", "ana", {"q0": "agrees"}),
            "bundle_not_found",
        ),
        "wrong-version": (
            {**labels_doc(bundle, "ana", {"q0": "agrees"}), "format": "tracer.judge_labels/2"},
            "judge_labels_invalid",
        ),
    }
    for name, (document, code) in refusals.items():
        path = write_labels(tmp_path, f"{name}.json", document)
        refused = check(files, path)
        assert (refused.code, refused.err) == (4, f"error: {code}\n"), name
        assert sorted(p.name for p in folder.iterdir()) == names, name
    assert check(files, tmp_path / "missing.json").code == 4
    # a link in place of the directory is refused
    other = tmp_path / "other"
    run2, files2, _ = judged(other)
    real = other / "real-labels"
    real.mkdir(mode=0o700)
    os.symlink(real, files2.out / "labels")
    path = write_labels(other, "x.json", labels_doc(run2.value("bundle"), "ana", {"q0": "agrees"}))
    refused = check(files2, path)
    assert (refused.code, refused.err) == (4, "error: labels_directory_not_private\n")
    assert list(real.iterdir()) == []


def test_labels_check_does_not_need_a_store_that_is_not_there(tmp_path: Path) -> None:
    path = write_labels(
        tmp_path,
        "l.json",
        labels_doc("rbnd_01a17f10-4888-7637-86a0-1af7ad452c4e", "ana", {"q0": None}),
    )
    result = run_cmd(["labels", "check", "--out", str(tmp_path / "nowhere"), "--labels", str(path)])
    assert (result.code, result.err) == (4, "error: store_not_found\n")
    assert not (tmp_path / "nowhere").exists()


# --------------------------------------------------------------------------- the agreement


def _setup(tmp_path: Path, labellers: dict[str, dict[str, Any]]) -> tuple[Any, Any, Any]:
    run, files, jf = judged(tmp_path / "run")
    for name, verdicts in labellers.items():
        document = labels_doc(run.value("bundle"), name, verdicts)
        assert check(files, write_labels(tmp_path, f"{name}.json", document)).code == 0
    return run, files, jf


def test_agreement_report_counts_only_never_rates_or_accuracy(tmp_path: Path) -> None:
    """ADR 0030 rule 22 and invariant I7; ADR 0023 amendment A4: the report is the contingency
    table of the judge by the human with every count out of the number of items; no percent, no
    decimal, no rate, no kappa, no claim word; it says the signal is uncalibrated."""
    labels = {"q0": "agrees", "q1": "agrees", "q2": "cannot_tell", "q3": "disagrees"}
    run, files, _ = _setup(tmp_path, {"ana": labels})
    result = agreement(files, run.value("bundle"))
    assert result.code == 0, (result.out, result.err)
    expected = [
        "agreement report: counts of an uncalibrated judge signal against human labels",
        f"bundle: {run.value('bundle')}",
        "items: 4",
        "labeller: ana (basis audio 4, stt_text 0, both 0)",
        *cell(
            {
                ("agrees", "agrees"): 1,  # q0
                ("disagrees", "agrees"): 1,  # q1
                ("cannot_tell", "cannot_tell"): 1,  # q2
                ("agrees", "disagrees"): 1,  # q3
            },
            4,
        ),
        "unlabelled by ana: 0 of 4 items (not counted as agreement or disagreement)",
    ]
    assert result.lines == expected
    text = result.out
    assert CLAIM_WORDS.search(text) is None
    assert "%" not in text
    assert re.search(r"\d\.\d", text) is None
    for word in ("rate", "kappa", "accuracy", "score", "percent", "interval"):
        assert re.search(rf"\b{word}\b", text.lower()) is None, word
    assert "uncalibrated" in result.lines[0]
    for line in result.lines[3:]:
        assert re.search(r"\d+ of 4 items", line) or line.startswith("labeller:")
    assert result.err == ""


def test_agreement_report_unlabelled_never_counted_as_disagreement(tmp_path: Path) -> None:
    """ADR 0030 rule 22: an item left ``null``, and an item the file does not list, are unlabelled:
    shown apart, counted in no cell, in particular in no disagreement cell."""
    run, files, _ = _setup(tmp_path, {"ana": {"q0": "agrees", "q1": None}})
    result = agreement(files, run.value("bundle"))
    assert result.code == 0
    assert result.lines[3:] == [
        "labeller: ana (basis audio 1, stt_text 0, both 0)",
        *cell({("agrees", "agrees"): 1}, 4),
        "unlabelled by ana: 3 of 4 items (not counted as agreement or disagreement)",
    ]
    # the judge disagreed on q1 and agreed on q3; neither gives a count against the human
    cells = {
        line.split(": ")[0]: int(line.split(": ")[1].split(" ")[0])
        for line in result.lines
        if line.startswith("judge ")
    }
    assert sum(cells.values()) == 1
    assert cells["judge disagrees, human agrees"] == 0
    assert cells["judge agrees, human disagrees"] == 0
    assert cells["judge disagrees, human disagrees"] == 0


def test_agreement_report_human_human_counts_when_two_labellers(tmp_path: Path) -> None:
    """ADR 0030 rule 22: with two labellers the report adds the human-by-human table over the
    items both labelled, and shows the items only one labelled and the items neither labelled;
    three labellers, one labeller twice and labels of another bundle are not mixed in."""
    ana = {"q0": "agrees", "q1": "agrees", "q2": "cannot_tell", "q3": None}
    bob = {"q0": "agrees", "q1": "disagrees", "q2": None, "q3": None}
    run, files, _ = _setup(tmp_path, {"ana": ana, "bob": bob})
    result = agreement(files, run.value("bundle"))
    assert result.code == 0, (result.out, result.err)
    lines = result.lines
    assert lines.count("items: 4") == 1
    assert [x for x in lines if x.startswith("labeller: ")] == [
        "labeller: ana (basis audio 3, stt_text 0, both 0)",
        "labeller: bob (basis audio 2, stt_text 0, both 0)",
    ]
    human = [x for x in lines if x.startswith(("ana ", "bob "))]
    assert human == [
        f"ana {a}, bob {b}: {n} of 4 items"
        for a in VERDICTS
        for b in VERDICTS
        for n in [{("agrees", "agrees"): 1, ("agrees", "disagrees"): 1}.get((a, b), 0)]
    ]
    assert "labelled by one only: 1 of 4 items" in lines  # q2
    assert "labelled by neither: 1 of 4 items" in lines  # q3
    assert "unlabelled by ana: 1 of 4 items (not counted as agreement or disagreement)" in lines
    assert "unlabelled by bob: 2 of 4 items (not counted as agreement or disagreement)" in lines
    assert CLAIM_WORDS.search(result.out) is None
    assert "%" not in result.out
    # the labels of another run in the same store are not mixed in
    other_run, _, _ = judged(tmp_path / "run", instant=NOW + timedelta(seconds=5))
    assert other_run.value("bundle") != run.value("bundle")
    elsewhere = labels_doc(other_run.value("bundle"), "dee", {"q0": "disagrees"})
    assert check(files, write_labels(tmp_path, "dee.json", elsewhere)).code == 0
    assert agreement(files, run.value("bundle")).lines == lines
    # a third labeller is not accepted, nor is one labeller twice
    third = write_labels(
        tmp_path, "cy.json", labels_doc(run.value("bundle"), "cy", {"q0": "agrees"})
    )
    assert check(files, third).code == 0
    refused = agreement(files, run.value("bundle"))
    assert (refused.code, refused.err) == (4, "error: judge_labels_labeller_count\n")
    for path in (files.out / "labels").glob("labels-*.json"):
        if json.loads(path.read_text("utf-8"))["labeller"] in ("cy", "bob"):
            path.unlink()
    twice = write_labels(
        tmp_path, "ana-again.json", labels_doc(run.value("bundle"), "ana", {"q0": "disagrees"})
    )
    assert check(files, twice).code == 0
    both = agreement(files, run.value("bundle"))
    assert (both.code, both.err) == (4, "error: judge_labels_labeller_repeated\n")


def test_agreement_refuses_without_labels_or_with_a_file_open_to_others(tmp_path: Path) -> None:
    run, files, _ = judged(tmp_path)
    none = agreement(files, run.value("bundle"))
    assert (none.code, none.err) == (4, "error: labels_none_for_bundle\n")
    document = labels_doc(run.value("bundle"), "ana", {"q0": "agrees"})
    assert check(files, write_labels(tmp_path, "ana.json", document)).code == 0
    (stored,) = list((files.out / "labels").glob("labels-*.json"))
    stored.chmod(0o644)
    loose = agreement(files, run.value("bundle"))
    assert (loose.code, loose.err) == (4, "error: labels_file_not_private\n")
    stored.chmod(0o600)
    assert agreement(files, run.value("bundle")).code == 0
    missing = run_cmd(["labels", "agreement", "--out", str(files.out), "--bundle", "x"])
    assert missing.code == 4


@pytest.mark.parametrize(
    ("command", "said"),
    [
        (["labels"], "use packet, check or agreement"),
        (["labels", "nonesuch"], "packet"),
        (["labels", "check"], "--labels"),
        (["labels", "packet", "--out", "x"], "--bundle"),
        (["labels", "agreement", "--out", "x"], "--bundle"),
    ],
)
def test_labels_usage_errors(command: list[str], said: str) -> None:
    """A usage error of the label commands is exit 64 and names what is wanted."""
    result = run_cmd(command)
    assert result.code == 64
    assert said in result.err
    assert result.out == ""
