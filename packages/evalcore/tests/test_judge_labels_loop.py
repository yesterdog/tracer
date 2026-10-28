# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-J21: the pure part of the human-label loop (ADR 0030 rule 22 and invariant I7; SCOPE Q-J17 to
Q-J19; contract docs/phase4/api-surface-batch-j2.md section 8): the strict loader of a labels file,
the view of a judged run, the blind packet and the agreement counts.

A label has a verdict (``null`` for an item not labelled: shown apart and never counted as a
disagreement) and the basis the human judged on. The packet shows what a person needs and never the
judge's verdict. The agreement report gives counts with denominators: no rate, no kappa, no
interval, no claim word. Pure functions over records: no clock, no file, no environment.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    FakeJudge,
    agrees_json,
    build_judged,
    judged_bundle,
    mod,
    run_items,
    verdict_json,
)

#: the format members, written in two pieces: the repository guard refuses a tracked file that holds
#: one of them as a single string (it is how it recognises a labels file or a packet)
LABELS_FORMAT = "tracer.judge_" + "labels/1"
PACKET_FORMAT = "tracer.judge_" + "packet/1"
BUNDLE = "rbnd_01a17f10-4888-7637-86a0-1af7ad452c4e"
OTHER = "rbnd_01a17f10-4888-7637-86a0-1af7ad452c4f"
VERDICTS = ("agrees", "disagrees", "cannot_tell")
ROWS = ("agrees", "disagrees", "cannot_tell", "no verdict", "not applicable")


def labels(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": LABELS_FORMAT,
        "bundle_id": BUNDLE,
        "labeller": "ana",
        "labels": [
            {"item_id": "mr_q00", "verdict": "agrees", "basis": "audio"},
            {"item_id": "mr_q01", "verdict": None, "basis": "stt_text"},
            {"item_id": "mr_q02", "verdict": "cannot_tell", "basis": "both"},
        ],
    }
    document.update(changes)
    return document


def parse(text: Any) -> Any:
    loaded = mod("judgelabels")
    return loaded.parse_judge_labels(text if isinstance(text, str) else json.dumps(text))


def refused(text: Any) -> Any:
    loaded = mod("judgelabels")
    with pytest.raises(loaded.JudgeLabelsError) as caught:
        parse(text)
    assert isinstance(caught.value, ValueError)
    assert str(caught.value) == caught.value.code  # type: ignore[attr-defined]
    return caught.value


# ------------------------------------------------------------------------------- the file


def test_labels_file_strict_closed_keys_and_version() -> None:
    """ADR 0030 rule 22: a labels file is strict JSON (a repeated key is refused), has the version
    member ``tracer.judge_labels/1`` and a closed set of keys at both levels; a verdict is one of
    three words or ``null``; a basis is one of three; an item appears once; the bundle id and the
    labeller are in their forms. A refusal is a code with a pointer to the place, never a value."""
    assert parse(labels()) == labels()
    for verdict in (*VERDICTS, None):
        for basis in ("audio", "stt_text", "both"):
            parse(labels(labels=[{"item_id": "mr_q00", "verdict": verdict, "basis": basis}]))
    cases: dict[str, Any] = {
        "extra key": labels(extra=1),
        "missing format": {k: v for k, v in labels().items() if k != "format"},
        "missing bundle": {k: v for k, v in labels().items() if k != "bundle_id"},
        "missing labeller": {k: v for k, v in labels().items() if k != "labeller"},
        "missing labels": {k: v for k, v in labels().items() if k != "labels"},
        "other version": labels(format="tracer.judge_labels/2"),
        "no version": labels(format=None),
        "bad bundle": labels(bundle_id="rbnd_x"),
        "a run id": labels(bundle_id="run_01a17f10-4888-7637-86a0-1af7ad452c4e"),
        "upper labeller": labels(labeller="Ana"),
        "empty labeller": labels(labeller=""),
        "long labeller": labels(labeller="a" * 65),
        "no labels": labels(labels=[]),
        "many labels": labels(
            labels=[{"item_id": f"q{n}", "verdict": None, "basis": "audio"} for n in range(101)]
        ),
        "label extra key": labels(
            labels=[{"item_id": "q", "verdict": None, "basis": "audio", "note": "x"}]
        ),
        "label no basis": labels(labels=[{"item_id": "q", "verdict": None}]),
        "label no verdict": labels(labels=[{"item_id": "q", "basis": "audio"}]),
        "label no item": labels(labels=[{"verdict": None, "basis": "audio"}]),
        "bad item id": labels(labels=[{"item_id": "Q 1", "verdict": None, "basis": "audio"}]),
        "long item id": labels(labels=[{"item_id": "q" * 41, "verdict": None, "basis": "audio"}]),
        "bad verdict": labels(labels=[{"item_id": "q", "verdict": "maybe", "basis": "audio"}]),
        "true verdict": labels(labels=[{"item_id": "q", "verdict": True, "basis": "audio"}]),
        "judge's word": labels(labels=[{"item_id": "q", "verdict": "stated", "basis": "audio"}]),
        "bad basis": labels(labels=[{"item_id": "q", "verdict": None, "basis": "video"}]),
        "null basis": labels(labels=[{"item_id": "q", "verdict": None, "basis": None}]),
        "item twice": labels(
            labels=[
                {"item_id": "q", "verdict": "agrees", "basis": "audio"},
                {"item_id": "q", "verdict": "disagrees", "basis": "audio"},
            ]
        ),
        "labels not a list": labels(labels={"item_id": "q"}),
    }
    for name, document in cases.items():
        error = refused(document)
        assert error.code == "judge_labels_invalid", name
        assert error.pointer.startswith("/"), name
    assert refused(labels(bundle_id="rbnd_SECRETVALUE")).pointer == "/bundle_id"
    assert "SECRETVALUE" not in repr(refused(labels(bundle_id="rbnd_SECRETVALUE")))
    assert (
        refused(labels(labels=[{"item_id": "q", "verdict": None, "basis": "video"}])).pointer
        == "/labels/0/basis"
    )
    assert refused("{").code == "judge_labels_not_json"
    assert refused("[1]").code == "judge_labels_invalid"
    assert (
        refused('{"format": "a", "format": "' + LABELS_FORMAT + '"}').code
        == "judge_labels_not_json"
    )
    assert refused('{"labels": NaN}').code == "judge_labels_not_json"
    assert refused(" " * 70_000).code == "judge_labels_too_large"


# ------------------------------------------------------------------------------ the counts


def item(item_id: str, **changes: Any) -> Any:
    loaded = mod("judgelabels")
    fields: dict[str, Any] = {
        "item_id": item_id,
        "question": "q",
        "match_mode": "all_facts",
        "expected_facts": ("f",),
        "expected_answer": None,
        "status": "scored",
        "verdict": "agrees",
        "reason_code": "all_facts_stated",
        "facts": ((0, "stated"),),
        "error_code": None,
        "na_reason": None,
        "missing_kinds": (),
        "attempts": 1,
        "window_turn_seqs": (0,),
    }
    fields.update(changes)
    return loaded.JudgedItem(**fields)


def five_items() -> list[Any]:
    return [
        item("a"),
        item("b", verdict="disagrees", reason_code="fact_absent", facts=((0, "absent"),)),
        item(
            "c",
            status="error",
            verdict="cannot_tell",
            reason_code="insufficient_information",
            facts=((0, "cannot_tell"),),
            error_code="judge_undetermined",
        ),
        item(
            "d",
            status="error",
            verdict=None,
            reason_code=None,
            facts=(),
            error_code="judge_output_invalid",
            attempts=2,
        ),
        item(
            "e",
            status="not_applicable",
            verdict=None,
            reason_code=None,
            facts=(),
            na_reason="missing_input",
            attempts=0,
            window_turn_seqs=(),
        ),
    ]


def test_the_row_of_the_table_is_the_verdict_or_why_there_is_none() -> None:
    loaded = mod("judgelabels")
    assert loaded.JUDGE_CATEGORIES == ROWS
    assert loaded.HUMAN_VERDICTS == VERDICTS
    assert [loaded.judge_category(i) for i in five_items()] == [
        "agrees", "disagrees", "cannot_tell", "no verdict", "not applicable",
    ]  # fmt: skip


def counts(items: list[Any], files: list[dict[str, Any]]) -> Any:
    return mod("judgelabels").agreement_counts(BUNDLE, items, files)


def test_agreement_counts_one_labeller_is_the_exact_contingency_table() -> None:
    loaded = mod("judgelabels")
    document = labels(
        labels=[
            {"item_id": "a", "verdict": "agrees", "basis": "audio"},
            {"item_id": "b", "verdict": "agrees", "basis": "audio"},
            {"item_id": "c", "verdict": "cannot_tell", "basis": "both"},
            {"item_id": "d", "verdict": "disagrees", "basis": "stt_text"},
            {"item_id": "e", "verdict": None, "basis": "audio"},
        ]
    )
    report = counts(five_items(), [document])
    assert report.labellers == ("ana",)
    assert report.item_count == 5
    assert report.bundle_id == BUNDLE
    table = report.judge_by_human["ana"]
    assert {k: v for k, v in table.items() if v} == {
        ("agrees", "agrees"): 1,
        ("disagrees", "agrees"): 1,
        ("cannot_tell", "cannot_tell"): 1,
        ("no verdict", "disagrees"): 1,
    }
    assert len(table) == 15
    assert report.unlabelled == {"ana": 1}
    assert report.bases["ana"] == {"audio": 2, "stt_text": 1, "both": 1}, "labelled items only"
    assert report.human_by_human is None
    assert (report.labelled_by_one_only, report.labelled_by_neither) == (0, 0)
    lines = loaded.agreement_lines(report)
    assert (
        lines[0] == "agreement report: counts of an uncalibrated judge signal against human labels"
    )
    assert lines[1:4] == [
        f"bundle: {BUNDLE}",
        "items: 5",
        "labeller: ana (basis audio 2, stt_text 1, both 1)",
    ]
    assert lines[-1] == "unlabelled by ana: 1 of 5 items (not counted as agreement or disagreement)"
    assert "judge disagrees, human agrees: 1 of 5 items" in lines
    assert len(lines) == 3 + 1 + 15 + 1
    for line in lines[4:-1]:
        assert re.fullmatch(r"judge [a-z_ ]+, human [a-z_]+: \d+ of 5 items", line), line


def test_unlabelled_items_are_counted_apart_never_as_a_disagreement() -> None:
    document = labels(
        labels=[
            {"item_id": "a", "verdict": None, "basis": "audio"},
            {"item_id": "b", "verdict": None, "basis": "audio"},
        ]
    )
    report = counts(five_items(), [document])  # c, d, e are not listed at all
    assert report.unlabelled == {"ana": 5}
    assert sum(report.judge_by_human["ana"].values()) == 0
    assert all(v == 0 for v in report.judge_by_human["ana"].values())


def test_agreement_counts_two_labellers_add_the_human_by_human_table() -> None:
    ana = labels(labels=[
        {"item_id": "a", "verdict": "agrees", "basis": "audio"},
        {"item_id": "b", "verdict": "agrees", "basis": "audio"},
        {"item_id": "c", "verdict": "cannot_tell", "basis": "audio"},
    ])  # fmt: skip
    bob = labels(labeller="bob", labels=[
        {"item_id": "a", "verdict": "agrees", "basis": "stt_text"},
        {"item_id": "b", "verdict": "disagrees", "basis": "stt_text"},
        {"item_id": "d", "verdict": "agrees", "basis": "stt_text"},
    ])  # fmt: skip
    report = counts(five_items(), [bob, ana])
    assert report.labellers == ("bob", "ana"), "in the order given"
    both = report.human_by_human
    assert both is not None
    assert len(both) == 9
    assert {k: v for k, v in both.items() if v} == {
        ("agrees", "agrees"): 1,
        ("disagrees", "agrees"): 1,
    }
    assert report.labelled_by_one_only == 2  # c (ana only) and d (bob only)
    assert report.labelled_by_neither == 1  # e
    assert report.unlabelled == {"bob": 2, "ana": 2}
    lines = mod("judgelabels").agreement_lines(report)
    assert "bob agrees, ana disagrees: 0 of 5 items" in lines
    assert "bob disagrees, ana agrees: 1 of 5 items" in lines
    assert lines[-2:] == ["labelled by one only: 2 of 5 items", "labelled by neither: 1 of 5 items"]


def test_agreement_counts_refuse_what_cannot_be_counted() -> None:
    loaded = mod("judgelabels")
    ana = labels(labels=[{"item_id": "a", "verdict": "agrees", "basis": "audio"}])

    def code(files: list[dict[str, Any]], items: list[Any] | None = None) -> str:
        with pytest.raises(loaded.JudgeLabelsError) as caught:
            counts(items if items is not None else five_items(), files)
        return str(caught.value.code)

    assert code([]) == "judge_labels_labeller_count"
    assert (
        code([ana, labels(labeller="bob"), labels(labeller="cy")]) == "judge_labels_labeller_count"
    )
    assert code([ana, ana]) == "judge_labels_labeller_repeated"
    assert code([labels(bundle_id=OTHER, labels=ana["labels"])]) == "judge_labels_bundle_differs"
    assert (
        code([labels(labels=[{"item_id": "zz", "verdict": None, "basis": "audio"}])])
        == "judge_labels_item_unknown"
    )


# ------------------------------------------------------------------ the view of a judged run


def judged() -> tuple[Any, Any]:
    call = build_judged()
    items = call.golden.items
    script = [
        agrees_json(items[0]),
        verdict_json("disagrees", "fact_absent", ["absent"]),
        verdict_json("cannot_tell", "insufficient_information", ["cannot_tell"]),
        "not json", "still not json",
        *[agrees_json(i) for i in items[4:]],
    ]  # fmt: skip
    engine = FakeJudge(script, run_id=call.run_id)
    judgements = run_items(call, engine)
    return call, judged_bundle(call, judgements)


def test_judged_items_are_read_from_the_stored_records_in_golden_order() -> None:
    loaded = mod("judgelabels")
    call, bundle = judged()
    items = loaded.judged_items(bundle)
    assert [i.item_id for i in items] == [g.id for g in call.golden.items]
    first = items[0]
    assert first.question == call.golden.items[0].question
    assert first.match_mode == "all_facts"
    assert first.expected_facts == call.golden.items[0].expected_facts
    assert first.expected_answer is None
    assert (first.status, first.verdict) == ("scored", "agrees")
    assert items[1].verdict == "disagrees"
    assert items[1].reason_code == "fact_absent"
    assert items[1].facts == ((0, "absent"),)
    assert (items[2].status, items[2].verdict, items[2].error_code) == (
        "error",
        "cannot_tell",
        "judge_undetermined",
    )
    assert (items[3].status, items[3].verdict, items[3].error_code, items[3].attempts) == (
        "error", None, "judge_output_invalid", 2,
    )  # fmt: skip
    assert items[6].match_mode == "equivalent_answer"
    assert items[6].expected_answer is not None
    assert items[6].expected_facts == ()
    assert [loaded.judge_category(i) for i in items[:4]] == [
        "agrees",
        "disagrees",
        "cannot_tell",
        "no verdict",
    ]
    unjudged = loaded.judged_items(call.bundle)
    assert {i.status for i in unjudged} == {"not_applicable"}


def test_the_packet_is_blind_by_construction() -> None:
    """ADR 0030 invariant I7: the packet is a function of the stored call and the golden set. Two
    judgements of the same call that disagree on every item give the same packet; nothing of the
    judge (verdict, reason, fact statuses, error codes, hashes, models) is in it."""
    loaded = mod("judgelabels")
    call, bundle = judged()
    packet = loaded.build_packet(bundle, call.golden, call_end_ms=call.call_end_ms)
    opposite_engine = FakeJudge(
        [
            verdict_json("disagrees", "answer_off_topic", ["absent"] * len(g.expected_facts))
            if g.match_mode == "all_facts"
            else verdict_json("disagrees", "meaning_differs")
            for g in call.golden.items
        ],
        run_id=call.run_id,
    )
    other_bundle = judged_bundle(call, run_items(call, opposite_engine))
    again = loaded.build_packet(other_bundle, call.golden, call_end_ms=call.call_end_ms)
    assert json.dumps(
        {
            **packet,
            "bundle_id": "",
            "labels_template": {**packet["labels_template"], "bundle_id": ""},
        },
        sort_keys=True,
    ) == json.dumps(
        {
            **again,
            "bundle_id": "",
            "labels_template": {**again["labels_template"], "bundle_id": ""},
        },
        sort_keys=True,
    )
    assert packet["format"] == PACKET_FORMAT
    assert packet["language"] == "mr"
    assert packet["bundle_id"] == str(bundle.id)
    assert again["bundle_id"] == str(other_bundle.id)
    text = json.dumps(packet, ensure_ascii=False).lower()
    for word in ("disagrees", "cannot_tell", "stated", "contradicted", "reason", "judge-", "fake-model",
                 "insufficient_information", "fact_absent", "meaning_same", "exchange", "calibrat"):  # fmt: skip
        assert word not in text.replace(PACKET_FORMAT, "").replace(LABELS_FORMAT, ""), word
    first = packet["items"][0]
    assert first["item_id"] == call.golden.items[0].id
    assert first["question"] == call.golden.items[0].question
    assert first["expected_facts"] == list(call.golden.items[0].expected_facts)
    assert first["audio_range_ms"] == {"start_ms": 200, "end_ms": 2000}
    assert first["stt_text"] == packet["items"][0]["stt_text"]
    assert first["stt_text"]
    template = packet["labels_template"]
    assert template["bundle_id"] == packet["bundle_id"]
    assert [x["item_id"] for x in template["labels"]] == [g.id for g in call.golden.items]
    assert {(x["verdict"], x["basis"]) for x in template["labels"]} == {(None, "audio")}
    parse(template)  # an untouched template is a valid labels file
