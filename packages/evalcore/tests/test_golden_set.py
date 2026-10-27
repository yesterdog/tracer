# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J04 and P4-J06: the synthetic mr golden fixture and the golden-set loader (ADR 0030 rules 1
to 3; contract docs/phase4/api-surface-batch-j1.md section 2).

The loader is strict JSON with closed keys, a version member, the caps of rule 2 and the
indic-linguistics character table (``mr-caller-charset/1``); every refusal is a stable code that
never quotes a value. Binding to the evaluator (rule 3) is a second, separate step that the
command takes at step 1, before any key is read.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

import pytest

from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    CODE_VERDICT,
    GOLDEN_FIXTURE,
    REASON_CODES,
    golden_fixture,
    golden_text,
    item_doc,
    mod,
    set_doc,
    sim_evaluator,
    sim_scenario,
)

PLANT_KINDS = (
    "exact_facts",
    "paraphrase",
    "number_words",
    "fact_missing",
    "fact_contradicted",
    "off_topic",
    "empty",
)


def refusal(text: str) -> str:
    golden = mod("golden")
    with pytest.raises(golden.GoldenSetError) as caught:
        golden.parse_golden_set(text)
    error = caught.value
    assert isinstance(error.code, str)
    assert str(error) == error.code, "the message is the stable code and nothing else"
    assert error.__cause__ is None
    assert CANARY not in str(error) + repr(error)
    return str(error.code)


# ----------------------------------------------------------------------------- P4-J04


def test_golden_fixture_mr_valid_and_marked_for_native_review() -> None:
    """The fixture is ASCII, valid under the loader, bound to the sim_mr scripted turns, marked
    for native review at every level, and its plants carry labels the validator accepts."""
    golden = mod("golden")
    output = mod("judgeoutput")
    raw = GOLDEN_FIXTURE.read_bytes()
    assert raw.isascii(), "Devanagari is written as \\u escapes, as sim_mr.json does"
    fixture = golden_fixture()
    assert fixture["fixture"] == "golden_mr"
    assert fixture["language"] == "mr"
    assert fixture["provenance"] == "synthetic_hand_written"
    assert fixture["needs_native_review"] is True
    assert fixture["bound_to"] == {"fixture": "sim_mr", "scenario": "sim-mr-s1"}

    # the golden set: 10 items that the loader and the binder accept, every one marked for review
    parsed = golden.parse_golden_set(golden_text())
    assert len(parsed.items) == 10
    assert [i.id for i in parsed.items] == [f"mr_q{k:02d}" for k in range(10)]
    assert all(i.needs_native_review is True for i in parsed.items)
    assert {i.match_mode for i in parsed.items} == {"all_facts", "equivalent_answer"}
    scenario = sim_scenario()
    for item in parsed.items:
        line = scenario["scripted_turns"][item.scripted_turn_index]["text"]
        assert item.question == line, "the question is the scripted line, byte for byte"
    golden.bind_golden_set(parsed, sim_evaluator(), caller_mode="scripted", barge_in_turns=())
    assert [i.scripted_turn_index for i in parsed.items] == list(range(10))

    # every string of the file is NFC and holds no control character
    def walk(node: Any) -> None:
        if isinstance(node, str):
            assert unicodedata.is_normalized("NFC", node)
            assert not any(unicodedata.category(c) in ("Cc", "Cf") for c in node), node
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)

    walk(fixture)

    # the plants: the seven kinds all occur; every item has the five common kinds
    plants = fixture["plants"]
    assert len({p["id"] for p in plants}) == len(plants)
    assert {p["kind"] for p in plants} == set(PLANT_KINDS)
    assert all(p["needs_native_review"] is True for p in plants)
    by_item: dict[str, set[str]] = {i.id: set() for i in parsed.items}
    for plant in plants:
        by_item[plant["item_id"]].add(plant["kind"])
    common = {"exact_facts", "paraphrase", "fact_contradicted", "off_topic", "empty"}
    for item_id, kinds in by_item.items():
        assert common <= kinds, item_id
    assert any("number_words" in k for k in by_item.values())
    assert any("fact_missing" in k for k in by_item.values())
    assert sum(1 for p in plants if p["kind"] == "fact_missing") >= 1

    # the intended labels are consistent with the output validator (cross-field rules included)
    items = {i.id: i for i in parsed.items}
    for plant in plants:
        item = items[plant["item_id"]]
        if plant["kind"] == "empty":
            assert plant["text"] == ""
            assert plant["intended_verdict"] is None
            assert plant["intended_error"] == "answer_window_empty"
            continue
        assert plant["text"].strip(), plant["id"]
        facts = [{"index": k, "status": s} for k, s in enumerate(plant["intended_facts"])]
        reply = json.dumps(
            {
                "verdict": plant["intended_verdict"],
                "reason_code": plant["intended_reason_code"],
                "facts": facts,
            }
        )
        verdict = output.parse_judge_output(
            reply, match_mode=item.match_mode, fact_count=len(item.expected_facts)
        )
        assert verdict.verdict == plant["intended_verdict"], plant["id"]
        assert CODE_VERDICT[item.match_mode][plant["intended_reason_code"]] == verdict.verdict

    # the wording of the reason codes and the labeller instructions, in mr and en
    wording = fixture["reason_code_wording"]
    assert tuple(sorted(wording)) == tuple(sorted(REASON_CODES))
    for code, entry in wording.items():
        assert set(entry) == {"mr", "en", "needs_native_review"}, code
        assert entry["mr"].strip()
        assert entry["en"].strip()
        assert entry["needs_native_review"] is True
        assert any("\u0900" <= c <= "\u097f" for c in entry["mr"]), code
    instructions = fixture["labeller_instructions"]
    assert set(instructions) == {"mr", "en", "needs_native_review"}
    assert any("\u0900" <= c <= "\u097f" for c in instructions["mr"])
    assert instructions["en"].isascii()
    assert len(instructions["en"]) > 50
    for word in ("agrees", "disagrees", "cannot_tell"):
        assert word in instructions["en"]
        assert word in instructions["mr"]


# ----------------------------------------------------------------------------- P4-J06


@pytest.mark.parametrize(
    ("label", "text", "code"),
    [
        ("empty text", "", "golden_not_json"),
        ("truncated", '{"format": ', "golden_not_json"),
        ("not an object", "[]", "golden_shape"),
        ("a string", '"x"', "golden_shape"),
        ("null", "null", "golden_shape"),
    ],
)
def test_golden_set_strict_json_closed_keys_and_version(label: str, text: str, code: str) -> None:
    """Strict JSON: not JSON and not an object are refused with their stable codes."""
    assert refusal(text) == code, label


def test_golden_set_strict_json_closed_keys_and_version_table() -> None:
    """Version, closed keys, duplicate keys, key shape and the item shape, one fault at a time."""
    golden = mod("golden")
    good = set_doc()
    assert golden.parse_golden_set(json.dumps(good)).key == good["key"]

    def broken(**changes: Any) -> str:
        return json.dumps(set_doc(**changes))

    # the version member: another format or another version is refused, a missing one too
    assert refusal(broken(format="tracer.golden_set/2")) == "golden_format"
    assert refusal(broken(format="tracer.golden_set")) == "golden_format"
    assert refusal(broken(format=1)) == "golden_format"
    without_format = {k: v for k, v in good.items() if k != "format"}
    assert refusal(json.dumps(without_format)) == "golden_format"
    # a missing key and an unknown key
    for key in ("key", "language", "items"):
        document = {k: v for k, v in good.items() if k != key}
        assert refusal(json.dumps(document)) == f"golden_missing_{key}"
    assert refusal(broken(extra="x")) == "golden_unknown_key"
    assert refusal(broken(version=1)) == "golden_unknown_key"
    # the set's own members
    assert refusal(broken(language="hi")) == "golden_language_not_supported"
    assert refusal(broken(language="en")) == "golden_language_not_supported"
    assert refusal(broken(language=None)) == "golden_language_not_supported"
    for bad_key in ("", "Set", "set-1", "1set", "a" * 65, None, 3):
        assert refusal(broken(key=bad_key)) == "golden_invalid_key", bad_key
    assert refusal(json.dumps({**good, "items": {}})) == "golden_items_count"
    assert refusal(json.dumps({**good, "items": "x"})) == "golden_items_count"

    # a repeated key anywhere is refused, whatever its value (strict JSON)
    duplicate_top = json.dumps(good)[:-1] + ', "key": "other_set"}'
    assert refusal(duplicate_top) == "golden_not_json"
    item = json.dumps(good["items"][0])
    duplicate_item = json.dumps(good).replace(item, item[:-1] + ', "id": "mr_q99"}', 1)
    assert refusal(duplicate_item) == "golden_not_json"

    # the items: closed keys per match mode
    assert refusal(json.dumps(set_doc(items=[item_doc(0, extra="x")]))) == "golden_item_shape"
    no_question = {k: v for k, v in item_doc(0).items() if k != "question"}
    assert refusal(json.dumps(set_doc(items=[no_question]))) == "golden_item_shape"
    no_review = {k: v for k, v in item_doc(0).items() if k != "needs_native_review"}
    assert refusal(json.dumps(set_doc(items=[no_review]))) == "golden_item_shape"
    both = item_doc(0, expected_answer="x")
    assert refusal(json.dumps(set_doc(items=[both]))) == "golden_item_shape"
    answer_item = item_doc(6)
    assert answer_item["match_mode"] == "equivalent_answer"
    with_facts = dict(answer_item, expected_facts=["x"])
    assert refusal(json.dumps(set_doc(items=[with_facts]))) == "golden_item_shape"
    assert refusal(json.dumps(set_doc(items=["x"]))) == "golden_item_shape"
    assert refusal(json.dumps(set_doc(items=[item_doc(0, needs_native_review="yes")]))) == (
        "golden_item_shape"
    )
    assert refusal(json.dumps(set_doc(items=[item_doc(0, match_mode="rating")]))) == (
        "golden_item_invalid_match_mode"
    )
    for bad_id in ("Q1", "q-1", "q 1", "1q", "", "a" * 41, None, 5):
        assert refusal(json.dumps(set_doc(items=[item_doc(0, id=bad_id)]))) == (
            "golden_item_invalid_id"
        ), bad_id
    clash = [item_doc(0), item_doc(1, id=item_doc(0)["id"])]
    assert refusal(json.dumps(set_doc(items=clash))) == "golden_item_repeated_id"
    for bad_index in (-1, True, 1.0, "0", None, 2**53):
        assert refusal(json.dumps(set_doc(items=[item_doc(0, scripted_turn_index=bad_index)]))) == (
            "golden_item_invalid_turn_index"
        ), bad_index
    # no refusal quotes the value it refused
    text = json.dumps(set_doc(items=[item_doc(0, id=CANARY)]))
    assert refusal(text) == "golden_item_invalid_id"


def test_golden_set_strict_json_closed_keys_and_version_result() -> None:
    """What a parse returns: the key, the language, the typed items and the file's SHA-256."""
    import hashlib

    golden = mod("golden")
    text = golden_text()
    parsed = golden.parse_golden_set(text)
    assert golden.GOLDEN_SET_FORMAT == "tracer.golden_set/1"
    assert parsed.key == "mr_apparatus_set"
    assert parsed.language == "mr"
    assert parsed.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    first = parsed.items[0]
    assert first.match_mode == "all_facts"
    assert isinstance(first.expected_facts, tuple)
    assert first.expected_answer is None
    answer_item = parsed.items[6]
    assert answer_item.match_mode == "equivalent_answer"
    assert answer_item.expected_facts == ()
    assert answer_item.expected_answer
    with pytest.raises(Exception, match=r"."):
        first.id = "other"
    assert issubclass(golden.GoldenSetError, mod("errors").InputRefusedError)
    # the same bytes give the same hash; one changed byte gives another
    other = golden.parse_golden_set(text.replace("mr_apparatus_set", "mr_apparatus_seu"))
    assert other.sha256 != parsed.sha256
    # the hash is of the bytes of the file as written, white space around the document included
    padded = golden.parse_golden_set("\n" + text + "\n\n")
    assert padded.sha256 == hashlib.sha256(("\n" + text + "\n\n").encode("utf-8")).hexdigest()
    assert padded.sha256 != parsed.sha256


def test_golden_item_question_must_equal_scripted_turn_text() -> None:
    """Rule 3: the question equals ``Evaluator.scripted_turns[index].text`` byte for byte."""
    golden = mod("golden")
    evaluator = sim_evaluator()
    parsed = golden.parse_golden_set(golden_text())
    assert golden.bind_golden_set(parsed, evaluator, caller_mode="scripted") is None

    def bind(items: list[dict[str, Any]]) -> str:
        document = golden.parse_golden_set(json.dumps(set_doc(items=items), ensure_ascii=False))
        with pytest.raises(golden.GoldenSetError) as caught:
            golden.bind_golden_set(document, evaluator, caller_mode="scripted")
        assert str(caught.value) == caught.value.code
        return str(caught.value.code)

    line = item_doc(0)["question"]
    # one character more, one less, a changed character, a trailing space: all refused
    assert bind([item_doc(0, question=line + ".")]) == "golden_question_mismatch"
    assert bind([item_doc(0, question=line[:-1])]) == "golden_question_mismatch"
    assert bind([item_doc(0, question=line.replace(",", ";", 1))]) == "golden_question_mismatch"
    assert bind([item_doc(0, question=line + " ")]) == "golden_question_mismatch"
    # another line of the script is a mismatch, not a rebinding by similar text
    assert bind([item_doc(0, question=item_doc(1)["question"])]) == "golden_question_mismatch"
    # an index the script does not have
    assert bind([item_doc(0, scripted_turn_index=10)]) == "golden_turn_missing"
    assert bind([item_doc(0, scripted_turn_index=999)]) == "golden_turn_missing"
    # a reordered or edited script silently binding another line is exactly what is refused
    shifted = [item_doc(0, scripted_turn_index=1)]
    assert bind(shifted) == "golden_question_mismatch"
    # a second item is checked too (the first may be fine)
    assert bind([item_doc(0), item_doc(1, question="\u0905")]) == "golden_question_mismatch"


def test_golden_set_refused_with_llm_caller() -> None:
    """Rule 3 and Ambiguity 1: only the scripted brain; the LLM brain and the replay brain are
    refused at step 1 (``--caller fake:<dir>`` reads files, not ``scripted_turns``)."""
    golden = mod("golden")
    evaluator = sim_evaluator()
    parsed = golden.parse_golden_set(golden_text())
    for caller_mode in ("llm", "replay"):
        with pytest.raises(golden.GoldenSetError) as caught:
            golden.bind_golden_set(parsed, evaluator, caller_mode=caller_mode)
        assert caught.value.code == "golden_caller_not_scripted", caller_mode
    for odd in ("sarvam", "", "SCRIPTED", None):
        with pytest.raises(golden.GoldenSetError) as caught:
            golden.bind_golden_set(parsed, evaluator, caller_mode=odd)
        assert caught.value.code == "golden_caller_not_scripted", odd
    golden.bind_golden_set(parsed, evaluator, caller_mode="scripted")
    # the refusal comes first: a bad item under the LLM caller is still the caller refusal
    broken = golden.parse_golden_set(json.dumps(set_doc(items=[item_doc(0, question="\u0905")])))
    with pytest.raises(golden.GoldenSetError) as caught:
        golden.bind_golden_set(broken, evaluator, caller_mode="llm")
    assert caught.value.code == "golden_caller_not_scripted"


def test_golden_set_caps_items_facts_chars() -> None:
    """Rule 2: at most 100 items, 10 facts, 300 characters per fact and 1,000 per expected
    answer; the characters are NFC and inside the mr table; every cap is a constant."""
    golden = mod("golden")
    assert (
        golden.MAX_GOLDEN_ITEMS,
        golden.MAX_FACTS_PER_ITEM,
        golden.MAX_FACT_CHARS,
        golden.MAX_EXPECTED_ANSWER_CHARS,
    ) == (100, 10, 300, 1000)
    base = item_doc(0)

    def many(count: int) -> list[dict[str, Any]]:
        return [dict(base, id=f"mr_x{k:03d}") for k in range(count)]

    assert len(golden.parse_golden_set(json.dumps(set_doc(items=many(100)))).items) == 100
    assert refusal(json.dumps(set_doc(items=many(101)))) == "golden_items_count"
    assert refusal(json.dumps(set_doc(items=[]))) == "golden_items_count"

    letter = "\u0905"
    ok_facts = [letter] * 10
    parsed = golden.parse_golden_set(
        json.dumps(set_doc(items=[dict(base, expected_facts=ok_facts)]))
    )
    assert len(parsed.items[0].expected_facts) == 10
    too_many = json.dumps(set_doc(items=[dict(base, expected_facts=[letter] * 11)]))
    assert refusal(too_many) == "golden_facts_count"
    no_facts = json.dumps(set_doc(items=[dict(base, expected_facts=[])]))
    assert refusal(no_facts) == "golden_facts_count"
    exact = json.dumps(set_doc(items=[dict(base, expected_facts=[letter * 300])]))
    assert golden.parse_golden_set(exact).items[0].expected_facts[0] == letter * 300
    assert (
        refusal(json.dumps(set_doc(items=[dict(base, expected_facts=[letter * 301])])))
        == "golden_fact_too_long"
    )

    answer = item_doc(6)
    exact_answer = json.dumps(set_doc(items=[dict(answer, expected_answer=letter * 1000)]))
    assert golden.parse_golden_set(exact_answer).items[0].expected_answer == letter * 1000
    assert (
        refusal(json.dumps(set_doc(items=[dict(answer, expected_answer=letter * 1001)])))
        == "golden_answer_too_long"
    )
    assert refusal(json.dumps(set_doc(items=[dict(base, question="")]))) == (
        "golden_item_invalid_question"
    )
    assert refusal(json.dumps(set_doc(items=[dict(base, question=letter * 2501)]))) == (
        "golden_item_invalid_question"
    )

    # characters: NFC, the table of mr-caller-charset/1, one line, not empty
    assert refusal(json.dumps(set_doc(items=[dict(base, expected_facts=["\u0958"])]))) == (
        "golden_text_not_nfc"
    )
    for bad in ("Latin words", "\u0905 abc", "\u0905\u200b", "\u0905\u202e", "\u0966\u0967"):
        assert refusal(json.dumps(set_doc(items=[dict(base, expected_facts=[bad])]))) == (
            "golden_text_character_not_allowed"
        ), bad
    assert refusal(json.dumps(set_doc(items=[dict(base, expected_facts=["\u0905\n\u0905"])]))) == (
        "golden_text_multiple_lines"
    )
    for blank in ("", "   "):
        assert refusal(json.dumps(set_doc(items=[dict(base, expected_facts=[blank])]))) == (
            "golden_text_empty"
        ), repr(blank)
    # digits and the allowed punctuation pass
    fine = json.dumps(set_doc(items=[dict(base, expected_facts=["\u0905 790, (1) - ?"])]))
    assert golden.parse_golden_set(fine).items[0].expected_facts == ("\u0905 790, (1) - ?",)
    # no refusal quotes the text it refused
    assert refusal(json.dumps(set_doc(items=[dict(base, expected_facts=[CANARY])]))) == (
        "golden_text_character_not_allowed"
    )


def test_golden_set_on_barge_in_turn_refused() -> None:
    """Rule 3: an item on a turn with a scheduled barge-in is refused (the caller cuts the
    agent's answer there by design); other turns are fine."""
    golden = mod("golden")
    evaluator = sim_evaluator()
    parsed = golden.parse_golden_set(golden_text())
    with pytest.raises(golden.GoldenSetError) as caught:
        golden.bind_golden_set(parsed, evaluator, caller_mode="scripted", barge_in_turns={3})
    assert caught.value.code == "golden_turn_barge_in"
    for barge in ({0}, {9}, {2, 5}, (4,), frozenset({7})):
        with pytest.raises(golden.GoldenSetError) as caught:
            golden.bind_golden_set(parsed, evaluator, caller_mode="scripted", barge_in_turns=barge)
        assert caught.value.code == "golden_turn_barge_in", barge
    # a barge-in on a turn that no item uses does not matter
    subset = golden.parse_golden_set(json.dumps(set_doc(items=[item_doc(0), item_doc(1)])))
    golden.bind_golden_set(subset, evaluator, caller_mode="scripted", barge_in_turns={5})
    golden.bind_golden_set(subset, evaluator, caller_mode="scripted", barge_in_turns=set())
    # the refusal is an input refusal (exit 4 at the command), raised before any key is read
    assert issubclass(golden.GoldenSetError, mod("errors").InputRefusedError)
