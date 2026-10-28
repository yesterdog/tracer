# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J23: the tier-degradation (T-DEG) matrix of a judged run (ADR 0030 rule 23 and invariant I8;
SCOPE J.3 Q-J8; the batch E matrix ``test_tdeg_sim_*`` as the model).

Contract: docs/phase4/api-surface-batch-j2.md section 11. Every case of one judged configuration
gives the same judge result keys and the same schema; only the statuses, the values and the reasons
differ. Missing input is ``not_applicable`` and no request is sent; an unasked question or an empty
answer is an ``error`` and never ``disagrees``; a failure is an ``error`` with its code. Nothing
missing is 0 or false. The provider is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    HEARD_1,
    LINE_0,
    LINE_1,
    evaluator_doc,
    run_sim,
    scenario_doc,
    sim_mod,
    write_inputs,
)
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    JudgeSim,
    answer_with,
    chat_response,
    config_doc,
    golden_doc,
    judge_add,
    judge_doc,
    judge_results,
    loopback_add,
    run_judged,
    two_answers,
    write_judge_files,
)

THIRD = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930"  # a third caller line (7 characters)


def rows(bundle: Any) -> dict[str, tuple[Any, ...]]:
    """(status, value, error code, na_reason, missing kinds, attempts) of each judge item."""
    out = {}
    for item, result in judge_results(bundle).items():
        document = (result.metadata or {}).get("tracer.judge")
        out[item] = (
            result.status,
            result.value,
            None if result.error is None else result.error.code,
            result.na_reason,
            tuple(result.missing_kinds or ()),
            None if document is None else document["attempts"],
        )
    return out


def signature(bundle: Any) -> dict[str, Any]:
    """What must not change from case to case: the keys and the schema of the judge records."""
    results = list(judge_results(bundle).values())
    return {
        "definitions": [d.key for d in bundle.snapshot.metric_definitions],
        "result_keys": list(judge_results(bundle)),
        "result_fields": {tuple(sorted(r.model_dump(mode="json"))) for r in results},
        "profile": sorted(bundle.snapshot.metadata["tracer.judge_profile"]),
        "documents": {
            tuple(sorted(judge_doc(r))) for r in results if (r.metadata or {}).get("tracer.judge")
        },
        "required": [i.required for i in bundle.snapshot.rubric.items],
    }


def four(*extra: dict[str, Any]) -> dict[str, Any]:
    base = golden_doc()["items"]
    return golden_doc(  # type: ignore[no-any-return]
        [
            base[0],
            base[1],
            {**base[0], "id": "q2", "scripted_turn_index": 0},
            {**base[0], "id": "q3", "scripted_turn_index": 1, "question": LINE_1},
            *extra,
        ]
    )


def three_lines() -> dict[str, Any]:
    return evaluator_doc(  # type: ignore[no-any-return]
        scripted_turns=[
            {"index": 0, "text": LINE_0, "language": "mr"},
            {"index": 1, "text": LINE_1, "language": "mr"},
            {"index": 2, "text": THIRD, "language": "mr"},
        ]
    )


def three_item_golden() -> dict[str, Any]:
    base = golden_doc()["items"]
    return golden_doc(  # type: ignore[no-any-return]
        [base[0], base[1], {**base[0], "id": "q2", "scripted_turn_index": 2, "question": THIRD}]
    )


# --------------------------------------------------------------------- not applicable: no request


def test_tdeg_judge_setup_failed_all_missing_input_no_request(tmp_path: Path) -> None:
    """ADR 0030 rule 23: when the call is not established there is nothing to judge: every judge
    result is ``not_applicable`` with ``missing_input`` (never ``disagrees``, never an error), no
    request reaches the provider, and the exit code is the call's (5)."""
    files = write_inputs(tmp_path, scenario=two_answers())
    jf = write_judge_files(files)
    sim = JudgeSim()

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    run = run_sim(
        files, target=True, add=loopback_add(jf), transport=sim, world_kw={"connect": refuse}
    )
    assert run.code == 5, (run.out, run.err)
    assert sim.requests == []
    bundle = run.bundle()
    table = rows(bundle)
    assert list(table) == ["q0", "q1"]
    for status, value, code, reason, kinds, attempts in table.values():
        assert (status, value, code, reason) == ("not_applicable", None, None, "missing_input")
        assert kinds
        assert attempts is None
    assert run.value("judge not applicable") == "2 of 2 items"
    assert run.value("judge item q0").startswith("not applicable (missing input ")
    assert [u for u in bundle.usage_records if u.stage == "judge"] == []
    assert not (files.out / "judge").exists()


def test_tdeg_judge_stt_failed_missing_input_no_request(tmp_path: Path) -> None:
    """When the speech-to-text of the agent failed there is no ``our_stt`` transcript: every judge
    result is ``not_applicable`` ``missing_input`` and no request is sent; exit 3."""
    files = write_inputs(tmp_path, scenario=two_answers())
    empty = tmp_path / "empty-stt"
    empty.mkdir()
    jf = write_judge_files(files)
    sim = JudgeSim()
    args = [
        "simulate", "--agent", f"fake:{files.scenario}", "--evaluator", str(files.evaluator),
        "--persona", str(files.persona), "--sim-profile", str(files.sim_profile),
        "--tts", "fake", "--caller", "scripted", "--stt", f"fake:{empty}",
        "--authorisation-reference", "Approved by the test operator, ticket 17",
        "--out", str(files.out), *loopback_add(jf),
    ]  # fmt: skip
    run = run_sim(files, args=args, transport=sim)
    assert run.code == 3, (run.out, run.err)
    assert sim.requests == []
    table = rows(run.bundle())
    assert list(table) == ["q0", "q1"]
    for status, value, code, reason, kinds, _ in table.values():
        assert (status, value, code, reason) == ("not_applicable", None, None, "missing_input")
        assert "transcript" in kinds
    assert run.value("judge not applicable") == "2 of 2 items"


def test_tdeg_judge_non_mr_turn_unsupported_language_all_items(tmp_path: Path) -> None:
    """ADR 0030 rule 11 (FJ4) and ADR 0023 amendment A3: one agent turn in another language makes
    EVERY judge result ``not_applicable`` ``unsupported_language`` in schema 0.0.1; no request is
    sent; not an error and not a verdict."""
    files = write_inputs(tmp_path, scenario=two_answers())
    replay = tmp_path / "stt_replay"
    for index, (text, language) in enumerate(((HEARD_0, "mr-IN"), (HEARD_1, "en-IN"))):
        (replay / f"chunk-{index:04d}.json").write_text(
            json.dumps(
                {
                    "request_id": f"r-{index}",
                    "transcript": text,
                    "language_code": language,
                    "timestamps": {
                        "words": [text],
                        "start_time_seconds": [0],
                        "end_time_seconds": [0.1],
                    },
                }
            ),
            "utf-8",
        )
    jf = write_judge_files(files)
    sim = JudgeSim()
    args = [
        "simulate", "--agent", f"fake:{files.scenario}", "--evaluator", str(files.evaluator),
        "--persona", str(files.persona), "--sim-profile", str(files.sim_profile),
        "--tts", "fake", "--caller", "scripted", "--stt", f"fake:{replay}",
        "--authorisation-reference", "Approved by the test operator, ticket 17",
        "--out", str(files.out), *loopback_add(jf),
    ]  # fmt: skip
    run = run_sim(files, args=args, transport=sim)
    assert sim.requests == []
    table = rows(run.bundle())
    assert list(table) == ["q0", "q1"]
    for status, value, code, reason, kinds, _ in table.values():
        assert (status, value, code, reason, kinds) == (
            "not_applicable", None, None, "unsupported_language", (),
        )  # fmt: skip
    assert run.value("judge not applicable") == "2 of 2 items"
    assert run.value("judge item q1") == "not applicable (unsupported language)"


# ---------------------------------------------------------------------- errors, never disagrees


def test_tdeg_judge_question_not_sent_is_error_never_disagrees(tmp_path: Path) -> None:
    """ADR 0030 rule 23: a question the caller never sent (the agent hung up first) is an
    ``error`` ``question_not_sent`` and sends no request; an agent that was never asked did not
    disagree. The items that were asked are judged as usual."""
    scenario = scenario_doc(
        [
            {"wait_ms": 600},
            {"speak": {"frames": 5, "level": 1000, "text": HEARD_0}},
            {"hang_up": 1000},
        ]
    )
    sim = JudgeSim()
    run, _files, _ = run_judged(
        tmp_path,
        engine="local_loopback/1",
        scenario=scenario,
        evaluator=three_lines(),
        golden=three_item_golden(),
        sim=sim,
    )
    assert run.code == 2, (run.out, run.err)
    table = rows(run.bundle())
    assert list(table) == ["q0", "q1", "q2"]
    assert table["q0"][:3] == ("scored", "agrees", None)
    for item in ("q1", "q2"):
        status, value, code, reason, _, attempts = table[item]
        assert (status, value, code, reason, attempts) == (
            "error",
            None,
            "question_not_sent",
            None,
            0,
        )
    assert len(sim.requests) == 1
    assert run.value("judge error") == "2 of 3 items"
    assert run.value("judge disagrees") == "0 of 3 items"
    assert run.value("judge item q2") == "error (question_not_sent; requests 0)"


def test_tdeg_judge_empty_answer_window_is_error_never_disagrees(tmp_path: Path) -> None:
    """ADR 0030 rule 23: an answer window with no agent speech is an ``error``
    ``answer_window_empty`` and sends no request; silence is not disagreement."""
    # the speech-to-text of the first agent segment is empty: no turn, so nothing to judge
    files = write_inputs(tmp_path, scenario=two_answers())
    replay = tmp_path / "stt_replay"
    for index, text in enumerate(("", HEARD_1)):
        words = {"words": [text], "start_time_seconds": [0], "end_time_seconds": [0.1]}
        body = {"request_id": f"r-{index}", "transcript": text, "language_code": "mr-IN"}
        if text:
            body["timestamps"] = words
        (replay / f"chunk-{index:04d}.json").write_text(json.dumps(body), "utf-8")
    jf = write_judge_files(files)
    sim = JudgeSim()
    args = [
        "simulate", "--agent", f"fake:{files.scenario}", "--evaluator", str(files.evaluator),
        "--persona", str(files.persona), "--sim-profile", str(files.sim_profile),
        "--tts", "fake", "--caller", "scripted", "--stt", f"fake:{replay}",
        "--authorisation-reference", "Approved by the test operator, ticket 17",
        "--out", str(files.out), *loopback_add(jf),
    ]  # fmt: skip
    run = run_sim(files, args=args, transport=sim)
    table = rows(run.bundle())
    assert table["q0"][:3] == ("error", None, "answer_window_empty")
    assert table["q1"][:3] == ("scored", "agrees", None)
    assert table["q0"][5] == 0
    assert len(sim.requests) == 1
    assert run.code == 2
    document = judge_doc(judge_results(run.bundle())["q0"])
    assert document["verdict"] is None
    assert document["attempts"] == 0
    assert document["window_turn_seqs"] == []
    assert document["exchange_sha256"] is None
    assert document["excluded_turns"] == 0


def test_tdeg_judge_auth_failure_stops_requests_and_errors_remaining(tmp_path: Path) -> None:
    """ADR 0030 rule 16: an authentication failure is never retried and stops every further
    request of the run; the item that met it and the remaining items are ``error``
    ``judge_auth_failed``; the text the provider sent back with the 401 is nowhere."""
    sim = JudgeSim(
        [
            answer_with("agrees"),
            chat_response(
                None,
                status=401,
                extra={"error": {"code": "invalid_api_key", "message": "AUTHBODYCANARY"}},
            ),
        ]
    )
    run, files, _ = run_judged(tmp_path, engine="local_loopback/1", golden=four(), sim=sim)
    assert run.code == 2
    assert len(sim.requests) == 2, "the 401 is not retried and nothing is sent after it"
    table = rows(run.bundle())
    assert table["q0"][:3] == ("scored", "agrees", None)
    for item in ("q1", "q2", "q3"):
        assert table[item][:3] == ("error", None, "judge_auth_failed"), item
    assert [table[i][5] for i in ("q1", "q2", "q3")] == [1, 0, 0]
    everything = run.out + run.err + (files.out / "tracer.sqlite").read_bytes().decode("latin-1")
    assert "AUTHBODYCANARY" not in everything + run.report()


def test_tdeg_judge_cap_reached_no_request_beyond_cap(tmp_path: Path) -> None:
    """ADR 0030 rule 17: when a cap is reached no further request is sent and the remaining items
    are ``error`` with the cap's code: ``--max-judge-requests`` counts every request, and
    ``--max-judge-tokens`` is checked as the tokens reported so far plus the next ``max_tokens``."""
    sim = JudgeSim()
    files = write_inputs(tmp_path / "requests", scenario=two_answers())
    jf = write_judge_files(files, golden=four())
    run = run_sim(files, add=loopback_add(jf, requests=2), transport=sim)
    assert run.code == 2
    assert len(sim.requests) == 2
    table = rows(run.bundle())
    assert [table[i][:3] for i in ("q0", "q1")] == [("scored", "agrees", None)] * 2
    assert [table[i][:3] for i in ("q2", "q3")] == [
        ("error", None, "judge_requests_cap_reached")
    ] * 2
    # tokens: 135 reported by the first request, so 135 + 300 (max_tokens) crosses a cap of 400
    sim2 = JudgeSim()
    files2 = write_inputs(tmp_path / "tokens", scenario=two_answers())
    jf2 = write_judge_files(files2, golden=four())
    run2 = run_sim(files2, add=loopback_add(jf2, requests=10, tokens=400), transport=sim2)
    assert run2.code == 2
    assert len(sim2.requests) == 1
    table2 = rows(run2.bundle())
    assert table2["q0"][:3] == ("scored", "agrees", None)
    assert [table2[i][:3] for i in ("q1", "q2", "q3")] == [
        ("error", None, "judge_tokens_cap_reached")
    ] * 3
    assert run2.value("judge usage") == "1 requests, 120 input tokens, 15 output tokens"


def test_tdeg_judge_invalid_output_twice_is_error(tmp_path: Path) -> None:
    """ADR 0030 rule 7: a reply that is not the closed JSON gets one regeneration (the identical
    request); a second invalid reply is an ``error`` ``judge_output_invalid``; the text of the
    replies is kept nowhere but the local exchange files."""
    prose = "RAWPROSECANARY I think the agent answered well."
    sim = JudgeSim([prose] * 4)
    run, files, _ = run_judged(tmp_path, engine="local_loopback/1", sim=sim)
    assert run.code == 2
    assert len(sim.requests) == 4
    assert sim.requests[0].content == sim.requests[1].content, (
        "the regeneration is the same request"
    )
    table = rows(run.bundle())
    for item in ("q0", "q1"):
        assert table[item][:3] == ("error", None, "judge_output_invalid")
        assert table[item][5] == 2
        assert judge_doc(judge_results(run.bundle())[item])["regenerated"] is True
    assert prose not in run.out + run.err + run.report()
    assert prose.encode() not in (files.out / "tracer.sqlite").read_bytes()
    assert run.value("judge item q0") == "error (judge_output_invalid; requests 2)"


def test_tdeg_judge_cannot_tell_is_judge_undetermined_error(tmp_path: Path) -> None:
    """ADR 0030 rule 8: a valid ``cannot_tell`` is an ``error`` with ``judge_undetermined`` and no
    value (the absence of a verdict, never a third enum value); the closed document keeps what the
    judge said."""
    run, _, _ = run_judged(tmp_path, engine="fake:cannot_tell")
    assert run.code == 2
    bundle = run.bundle()
    for item, result in judge_results(bundle).items():
        assert (result.status, result.value) == ("error", None), item
        assert result.error is not None
        assert result.error.code == "judge_undetermined"
        assert judge_doc(result)["verdict"] == "cannot_tell"
        assert result.passed is None
    assert run.value("judge cannot_tell") == "2 of 2 items"
    assert run.value("judge error") == "0 of 2 items"
    assert run.value("judge item q1").startswith("cannot_tell (error judge_undetermined; reason ")


# --------------------------------------------------------------------------- the same schema


def test_tdeg_judge_schema_identical_across_cases(tmp_path: Path) -> None:
    """ADR 0030 invariant I8: whatever happens, a judged configuration gives the same definitions,
    the same judge result keys in the same order, the same fields of a result, the same keys of the
    closed documents and the same profile document keys; the run needs no case-specific code."""
    seen: dict[str, dict[str, Any]] = {}

    def keep(name: str, run: Any) -> None:
        seen[name] = signature(run.bundle())

    for scenario in ("agrees", "disagrees", "cannot_tell", "invalid", "auth_failure"):
        run, _, _ = run_judged(tmp_path / scenario, engine=f"fake:{scenario}")
        keep(scenario, run)
    files = write_inputs(tmp_path / "setup", scenario=two_answers())
    jf = write_judge_files(files, config=config_doc("fake/1", None))

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    keep(
        "setup_failed",
        run_sim(files, target=True, add=judge_add(jf, "fake:agrees"), world_kw={"connect": refuse}),
    )
    hung = scenario_doc(
        [
            {"wait_ms": 600},
            {"speak": {"frames": 5, "level": 1000, "text": HEARD_0}},
            {"hang_up": 1000},
        ]
    )
    run, _, _ = run_judged(tmp_path / "hangup", engine="fake:agrees", scenario=hung)
    keep("question_not_sent", run)
    assert len(seen) == 7
    first = next(iter(seen.values()))
    assert first["definitions"][-2:] == ["judge_golden_q0", "judge_golden_q1"]
    assert first["required"].count(True) == 1
    standard = first["documents"]
    assert len(standard) == 1
    for name, signature_ in seen.items():
        assert {k: v for k, v in signature_.items() if k != "documents"} == {
            k: v for k, v in first.items() if k != "documents"
        }, name
        assert signature_["documents"] <= standard, name


def test_tdeg_run_without_golden_set_unchanged(tmp_path: Path) -> None:
    """ADR 0030 rule 1: a run without ``--golden`` is what batch E builds: the same definitions
    and bindings (the five shipped metrics and the inert check), no ``tracer.judge_profile``, no
    judge usage, no judge line, no judge request, the same exit code."""
    files = write_inputs(tmp_path / "plain")
    run = run_sim(files)
    assert run.code == 0, (run.out, run.err)
    bundle = run.bundle()
    keys = [d.key for d in bundle.snapshot.metric_definitions]
    assert not any(k.startswith("judge_") for k in keys)
    assert keys[-1] == "simulated_call_no_outcome_check"
    assert len(keys) == 8
    assert set(bundle.snapshot.metadata) == {"tracer.sim_profile"}
    assert [u for u in bundle.usage_records if u.stage == "judge"] == []
    assert [x for x in run.lines if x.startswith("judge")] == []
    assert not (files.out / "judge").exists()
    # the same call with a judge adds the judge definitions and nothing else to the snapshot
    judged, _, _ = run_judged(tmp_path / "judged", engine="fake:agrees")
    more = [d.key for d in judged.bundle().snapshot.metric_definitions]
    assert more == [*keys, "judge_golden_q0", "judge_golden_q1"]
