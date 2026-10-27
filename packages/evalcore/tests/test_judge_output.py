# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J09: the output validator of the judge (ADR 0030 rule 7; contract
docs/phase4/api-surface-batch-j1.md section 5).

A reply is accepted only as exactly one JSON object with the closed keys ``verdict``,
``reason_code`` and ``facts``, closed values, the cross-field rules and at most 4,096 bytes. It is
never repaired: a code fence around the JSON is invalid. One invalid reply (or a ``length`` finish)
gets one regeneration of the identical request, a second makes the item an error.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from typing import Any

import pytest

from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    CODE_VERDICT,
    FACT_STATUSES,
    REASON_CODES,
    VERDICTS,
    FakeJudge,
    agrees_json,
    build_judged,
    default_params,
    golden_set,
    mod,
    run_items,
    verdict_json,
)

GOOD = verdict_json("agrees", "all_facts_stated", ["stated", "stated"])


def parse(text: str, *, mode: str = "all_facts", count: int = 2) -> Any:
    return mod("judgeoutput").parse_judge_output(text, match_mode=mode, fact_count=count)


def refused(text: str, *, mode: str = "all_facts", count: int = 2) -> str:
    output = mod("judgeoutput")
    with pytest.raises(output.JudgeOutputInvalid) as caught:
        output.parse_judge_output(text, match_mode=mode, fact_count=count)
    code = str(caught.value.code)
    assert str(caught.value) == code, "the message is the stable code"
    assert code in output.OUTPUT_REFUSAL_CODES
    assert CANARY not in str(caught.value) + repr(caught.value)
    return code


def test_judge_output_closed_keys_and_values_only() -> None:
    """Exactly the keys verdict, reason_code and facts; closed values; no free text anywhere."""
    output = mod("judgeoutput")
    assert tuple(output.JUDGE_VERDICTS) == VERDICTS
    assert tuple(output.FACT_STATUSES) == FACT_STATUSES
    verdict = parse(GOOD)
    assert (verdict.verdict, verdict.reason_code) == ("agrees", "all_facts_stated")
    assert verdict.facts == ((0, "stated"), (1, "stated"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.verdict = "disagrees"
    assert parse(f" \n{GOOD}\n ").verdict == "agrees", "white space around JSON is JSON"

    # not JSON at all, or not one object
    assert refused("") == "output_not_json"
    assert refused("agrees") == "output_not_json"
    assert refused("{") == "output_not_json"
    assert refused("\ufeff" + GOOD) == "output_not_json"
    assert refused(GOOD + GOOD) == "output_not_json"
    assert refused(GOOD + " thanks") == "output_not_json"
    assert refused("Here is my answer: " + GOOD) == "output_not_json"
    assert refused('{"verdict": NaN}') == "output_not_json"
    assert refused("[]") == "output_not_object"
    assert refused("null") == "output_not_object"
    assert refused('"agrees"') == "output_not_object"
    assert refused("3") == "output_not_object"
    duplicate = GOOD.replace('"verdict":"agrees"', '"verdict":"agrees","verdict":"disagrees"')
    assert duplicate != GOOD
    assert refused(duplicate) == "output_not_json"

    # the keys: a missing key, an unknown key (a "reason" in prose is the one to refuse)
    document = json.loads(GOOD)
    for key in document:
        assert refused(json.dumps({k: v for k, v in document.items() if k != key})) == (
            "output_keys"
        ), key
    for extra in ("reason", "confidence", "p_yes", "explanation", "Verdict"):
        assert refused(json.dumps({**document, extra: "x"})) == "output_keys", extra

    # the verdict and the reason code: closed sets, exact spelling, strings only
    for bad in ("Agrees", "AGREES", "maybe", "yes", "", None, True, 1, ["agrees"], "agrees "):
        assert refused(json.dumps({**document, "verdict": bad})) == "output_verdict_value", bad
    for bad in (
        "",
        "ALL_FACTS_STATED",
        "all-facts-stated",
        None,
        1,
        "because",
        "all_facts_stated ",
    ):
        assert refused(json.dumps({**document, "reason_code": bad})) == (
            "output_reason_code_value"
        ), bad

    # the facts: a list of {index, status} and nothing else
    not_lists: list[Any] = ["x", {}, None, 3, "stated"]
    for bad_facts in not_lists:
        assert refused(json.dumps({**document, "facts": bad_facts})) == "output_facts_shape"
    entries: list[Any] = [
        ["stated", "stated"],
        [{"index": 0, "status": "stated"}, "x"],
        [{"index": 0, "status": "stated", "note": "free text"}, {"index": 1, "status": "stated"}],
        [{"index": 0}, {"index": 1, "status": "stated"}],
        [{"status": "stated"}, {"index": 1, "status": "stated"}],
        [{"index": True, "status": "stated"}, {"index": 1, "status": "stated"}],
        [{"index": "0", "status": "stated"}, {"index": 1, "status": "stated"}],
        [{"index": 0.0, "status": "stated"}, {"index": 1, "status": "stated"}],
        [{"index": 0, "status": None}, {"index": 1, "status": "stated"}],
        [{"index": 0, "status": ["stated"]}, {"index": 1, "status": "stated"}],
    ]
    for bad_entries in entries:
        assert refused(json.dumps({**document, "facts": bad_entries})) == "output_facts_shape", (
            bad_entries
        )
    for bad_status in ("Stated", "true", "", "unknown", "stated "):
        shaped = [{"index": 0, "status": bad_status}, {"index": 1, "status": "stated"}]
        assert refused(json.dumps({**document, "facts": shaped})) == "output_fact_status_value"
    # one entry per expected fact, in order, from 0
    wrong_counts: list[Any] = [
        [],
        [{"index": 0, "status": "stated"}],
        [{"index": k, "status": "stated"} for k in range(3)],
        [{"index": 1, "status": "stated"}, {"index": 0, "status": "stated"}],
        [{"index": 1, "status": "stated"}, {"index": 2, "status": "stated"}],
        [{"index": 0, "status": "stated"}, {"index": 0, "status": "stated"}],
        [{"index": -1, "status": "stated"}, {"index": 0, "status": "stated"}],
    ]
    for facts in wrong_counts:
        assert refused(json.dumps({**document, "facts": facts})) == "output_facts_index", facts
    # the equivalent_answer mode has no facts
    equivalent = verdict_json("agrees", "meaning_same")
    assert parse(equivalent, mode="equivalent_answer", count=0).facts == ()
    assert refused(
        verdict_json("agrees", "meaning_same", ["stated"]), mode="equivalent_answer", count=0
    ) == ("output_facts_not_empty")
    # a free-text value anywhere is refused, whatever its place
    sneaky = json.dumps({**document, "reason_code": "all_facts_stated: " + CANARY})
    assert refused(sneaky) == "output_reason_code_value"


def test_judge_output_cross_field_consistency_enforced() -> None:
    """Rule 7 and Ambiguity 2: agrees iff every fact is stated; disagrees iff a fact is
    contradicted or absent; cannot_tell iff none of those and one is cannot_tell (a partition).
    The reason code must go with the verdict (the table of Ambiguity 3), per match mode."""
    output = mod("judgeoutput")
    representative = {
        "agrees": "all_facts_stated",
        "disagrees": "fact_absent",
        "cannot_tell": "insufficient_information",
    }

    def expected(statuses: tuple[str, ...]) -> str:
        if all(s == "stated" for s in statuses):
            return "agrees"
        if any(s in ("contradicted", "absent") for s in statuses):
            return "disagrees"
        return "cannot_tell"

    for count in (1, 2, 3):
        for statuses in itertools.product(FACT_STATUSES, repeat=count):
            for verdict in VERDICTS:
                text = verdict_json(verdict, representative[verdict], statuses)
                if verdict == expected(statuses):
                    assert parse(text, count=count).verdict == verdict, (statuses, verdict)
                else:
                    assert refused(text, count=count) == "output_verdict_facts_inconsistent", (
                        statuses,
                        verdict,
                    )
    # the partition: one fact absent and another cannot_tell is a disagreement, never a cannot_tell
    assert refused(
        verdict_json("cannot_tell", "insufficient_information", ["absent", "cannot_tell"])
    ) == ("output_verdict_facts_inconsistent")
    assert parse(verdict_json("disagrees", "fact_absent", ["absent", "cannot_tell"])).verdict == (
        "disagrees"
    )

    # the code-to-verdict table, per mode: every code with every verdict
    assert {m: dict(t) for m, t in output.CODE_VERDICT.items()} == CODE_VERDICT
    assert tuple(output.allowed_reason_codes("all_facts")) == tuple(CODE_VERDICT["all_facts"])
    assert tuple(output.allowed_reason_codes("equivalent_answer")) == tuple(
        CODE_VERDICT["equivalent_answer"]
    )
    facts_for = {
        "agrees": ["stated", "stated"],
        "disagrees": ["absent", "contradicted"],
        "cannot_tell": ["cannot_tell", "stated"],
    }
    for mode in ("all_facts", "equivalent_answer"):
        for code in REASON_CODES:
            for verdict in VERDICTS:
                facts = facts_for[verdict] if mode == "all_facts" else []
                text = verdict_json(verdict, code, facts)
                count = 2 if mode == "all_facts" else 0
                if CODE_VERDICT[mode].get(code) == verdict:
                    assert parse(text, mode=mode, count=count).reason_code == code
                elif code in CODE_VERDICT[mode]:
                    assert refused(text, mode=mode, count=count) == (
                        "output_reason_verdict_inconsistent"
                    ), (mode, code, verdict)
                else:
                    assert refused(text, mode=mode, count=count) == ("output_reason_code_value"), (
                        mode,
                        code,
                        verdict,
                    )
    # an unknown mode or a bad count is a programming error, not a refusal of the reply
    for bad_mode in ("rating", "", None):
        with pytest.raises(ValueError, match=r"."):
            output.parse_judge_output(GOOD, match_mode=bad_mode, fact_count=2)
    for bad_count in (-1, True, 1.5, "2"):
        with pytest.raises(ValueError, match=r"."):
            output.parse_judge_output(GOOD, match_mode="all_facts", fact_count=bad_count)


def test_judge_output_over_size_cap_refused() -> None:
    """Rule 7 and rule 17: at most 4,096 bytes of UTF-8; one byte more is refused before any
    reading of the content."""
    output = mod("judgeoutput")
    assert output.MAX_JUDGE_OUTPUT_BYTES == 4096
    base = verdict_json("agrees", "all_facts_stated", ["stated", "stated"])
    padded = base + " " * (4096 - len(base.encode("utf-8")))
    assert len(padded.encode("utf-8")) == 4096
    assert parse(padded).verdict == "agrees"
    assert refused(padded + " ") == "output_too_large"
    assert refused(" " * 5000 + base) == "output_too_large"
    # the cap counts bytes, not characters: 2,049 two-byte characters are over
    wide = '{"verdict":"agrees","reason_code":"all_facts_stated","facts":[],"x":"' + "\u00e9" * 2049
    assert len(wide) < 4096 < len(wide.encode("utf-8"))
    assert refused(wide) == "output_too_large"
    # a huge reply is refused without being parsed (the cost does not grow with the size)
    assert refused("[" * 2_000_000) == "output_too_large"


def test_judge_output_never_repaired_one_regeneration_then_error() -> None:
    """Rule 7 and invariant I2: a code fence, prose around the JSON, a wrong key or an
    inconsistent field is invalid and is never repaired; the identical request is sent once more,
    and a second invalid reply makes the item an ``error`` ``judge_output_invalid``."""
    call = build_judged(lines=1)
    item = call.golden.items[0]
    good = agrees_json(item)
    fenced = "```json\n" + good + "\n```"
    inconsistent = verdict_json("agrees", "all_facts_stated", ["stated", "absent"])
    cases = [
        ("valid at once", [good], "verdict", 1, False),
        ("prose then valid", ["I think it agrees.", good], "verdict", 2, True),
        ("fence then valid", [fenced, good], "verdict", 2, True),
        ("inconsistent then valid", [inconsistent, good], "verdict", 2, True),
        ("fence twice", [fenced, fenced], "error", 2, True),
        ("prose twice", ["agrees", "agrees " + CANARY], "error", 2, True),
        ("trailing text twice", [good + " ok", good + " ok"], "error", 2, True),
        ("extra key twice", [good[:-1] + ',"reason":"x"}'] * 2, "error", 2, True),
        ("inconsistent twice", [inconsistent, inconsistent], "error", 2, True),
    ]
    for label, replies, state, attempts, regenerated in cases:
        engine = FakeJudge(replies)
        judged = run_items(call, engine, only=[item.id])[item.id]
        assert judged.state == state, label
        assert (judged.attempts, judged.regenerated) == (attempts, regenerated), label
        assert len(engine.requests) == attempts, label
        # the identical request, sent again: same messages and parameters, another attempt
        first = engine.requests[0]
        for number, request in enumerate(engine.requests, start=1):
            assert request.messages == first.messages
            assert request.params == first.params
            assert request.attempt == number
            assert request.item_id == item.id
        if state == "error":
            assert judged.error_code == "judge_output_invalid", label
            assert judged.verdict is None
            assert judged.reason_code is None
            assert judged.facts == ()
        else:
            assert judged.verdict == "agrees"
            assert judged.error_code is None
        # only counts are kept: no text of any invalid reply is in the judgement
        shown = repr(dataclasses.asdict(judged))
        assert CANARY not in shown, label
        assert "```" not in shown, label
        assert "I think" not in shown, label
    # a finish_reason of length is invalid even when the text is valid JSON
    engine = FakeJudge([good, good], finish_reason="length")
    judged = run_items(call, engine, only=[item.id])[item.id]
    assert (judged.state, judged.error_code, judged.attempts) == (
        "error",
        "judge_output_invalid",
        2,
    )
    # a request that failed is not a reply: no regeneration, no judge_output_invalid
    mod_judge = mod("judge")
    engine = FakeJudge([mod_judge.JudgeServerError(status=503, attempts=4)])
    judged = run_items(call, engine, only=[item.id])[item.id]
    assert (judged.state, judged.error_code, judged.attempts) == (
        "error",
        "judge_request_failed",
        1,
    )
    assert len(engine.requests) == 1
    assert judged.regenerated is False
    engine = FakeJudge([mod_judge.JudgeAuthError(status=401, attempts=1)])
    judged = run_items(call, engine, only=[item.id])[item.id]
    assert judged.error_code == "judge_auth_failed"
    assert judged.stops_run is True
    assert len(engine.requests) == 1
    # a cannot_tell reply is a valid answer: it is kept as the verdict, not regenerated
    cannot = verdict_json("cannot_tell", "insufficient_information", ["cannot_tell"] * 2)
    engine = FakeJudge([cannot])
    first_item = golden_set().items[0]
    assert len(first_item.expected_facts) == 2
    judged = run_items(call, engine, only=[item.id])[item.id]
    assert (judged.state, judged.verdict, judged.attempts) == ("cannot_tell", "cannot_tell", 1)
    assert judged.error_code == "judge_undetermined"
    # no request when there is nothing to ask: the question was never sent, or the window is empty
    unsent = build_judged(lines=1)
    engine = FakeJudge([])
    result = run_items(unsent, engine, only=[unsent.golden.items[5].id])
    judged = result[unsent.golden.items[5].id]
    assert (judged.state, judged.error_code, judged.attempts) == (
        "error",
        "question_not_sent",
        0,
    )
    assert engine.requests == []
    assert default_params().max_tokens == 512


def test_judge_reason_code_from_closed_set_only() -> None:
    """Rule 7: the reason code is one of the eight, spelled exactly, and goes with its verdict."""
    output = mod("judgeoutput")
    assert tuple(output.REASON_CODES) == REASON_CODES
    assert len(set(REASON_CODES)) == 8
    all_facts = {
        "all_facts_stated": ("agrees", ["stated", "stated"]),
        "fact_contradicted": ("disagrees", ["contradicted", "stated"]),
        "fact_absent": ("disagrees", ["absent", "stated"]),
        "answer_off_topic": ("disagrees", ["absent", "absent"]),
        "answer_unintelligible": ("cannot_tell", ["cannot_tell", "cannot_tell"]),
        "insufficient_information": ("cannot_tell", ["cannot_tell", "stated"]),
    }
    for code, (verdict, facts) in all_facts.items():
        assert parse(verdict_json(verdict, code, facts)).reason_code == code
    equivalent = {
        "meaning_same": "agrees",
        "meaning_differs": "disagrees",
        "answer_off_topic": "disagrees",
        "answer_unintelligible": "cannot_tell",
        "insufficient_information": "cannot_tell",
    }
    for code, verdict in equivalent.items():
        assert parse(verdict_json(verdict, code), mode="equivalent_answer", count=0).verdict == (
            verdict
        )
    # a code of the other mode is not a code of this one
    assert refused(verdict_json("agrees", "meaning_same", ["stated", "stated"])) == (
        "output_reason_code_value"
    )
    assert (
        refused(verdict_json("agrees", "all_facts_stated"), mode="equivalent_answer", count=0)
        == "output_reason_code_value"
    )
    # near misses, other cases, other types, a sentence
    for bad in (
        "fact_absent_",
        "Fact_Absent",
        "fact absent",
        "fact-absent",
        "absent",
        "other",
        "",
        " fact_absent",
        "fact_absent\n",
        None,
        0,
        False,
        ["fact_absent"],
        {"code": "fact_absent"},
        "the agent did not say it",
    ):
        text = json.dumps(
            {
                "verdict": "disagrees",
                "reason_code": bad,
                "facts": [{"index": 0, "status": "absent"}, {"index": 1, "status": "absent"}],
            }
        )
        assert refused(text) == "output_reason_code_value", bad
    # the set the template names is the set the validator accepts
    assert set(output.allowed_reason_codes("all_facts")) | set(
        output.allowed_reason_codes("equivalent_answer")
    ) == set(REASON_CODES)


def test_judge_item_maps_failures_and_caps_to_their_codes() -> None:
    """A request that failed is an error with a code of its own: a cap says which cap, an
    authentication failure and a cap end the judging of the run, any other failure does not."""
    judge = mod("judge")
    call = build_judged(lines=1)
    item = call.golden.items[0]
    cases = [
        (
            judge.JudgeCapReachedError(index=0, reason="judge_requests"),
            "judge_requests_cap_reached",
            True,
        ),
        (
            judge.JudgeCapReachedError(index=0, reason="judge_tokens"),
            "judge_tokens_cap_reached",
            True,
        ),
        (judge.JudgeAuthError(index=0, status=403, attempts=1), "judge_auth_failed", True),
        (judge.JudgeRateLimited(index=0, status=429, attempts=4), "judge_request_failed", False),
        (judge.JudgeServerError(index=0, status=500, attempts=4), "judge_request_failed", False),
        (
            judge.JudgeTransportError(index=0, kind="read_timeout", attempts=4),
            "judge_request_failed",
            False,
        ),
        (
            judge.JudgeRequestRejected(index=0, status=400, attempts=1),
            "judge_request_failed",
            False,
        ),
        (
            judge.JudgeResponseInvalid(index=0, reason="not_json", attempts=1),
            "judge_request_failed",
            False,
        ),
        (judge.JudgeCapReachedError(index=0), "judge_request_failed", True),
    ]
    for failure, code, stops in cases:
        engine = FakeJudge([failure])
        judged = run_items(call, engine, only=[item.id])[item.id]
        assert judged.state == "error", (type(failure), code)
        assert judged.error_code == code, (type(failure), code)
        assert judged.stops_run is stops, (type(failure), code)
        assert judged.failure is failure
        assert len(engine.requests) == 1
        assert judged.attempts == 1
        assert judged.regenerated is False
        assert judged.verdict is None
        assert judged.usage == ()
