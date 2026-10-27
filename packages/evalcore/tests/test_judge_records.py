# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J10: the mapping of golden items and judge verdicts to schema 0.0.1 records (ADR 0030
rules 8 and 10; ADR 0027 rule 12a; contract docs/phase4/api-surface-batch-j1.md section 7).

One ``llm_judge`` enum definition per item without a pass rule, bindings that carry the item's
texts as parameters, a closed document in the snapshot and in each result, ``cannot_tell`` as an
``error`` ``judge_undetermined`` and never a value, a ``JudgeInfo`` with no confidence, and an
inert expected-outcome check that stays the only required rubric item. Every judged bundle passes
``check_run_bundle``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from evalcore_p4e_support import stored_profile  # type: ignore[import-not-found,unused-ignore]
from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CREATED_AT,
    FakeJudge,
    agrees_json,
    build_judged,
    configuration_with,
    default_params,
    golden_text,
    judge_setup,
    judged_bundle,
    mod,
    results_by_key,
    run_items,
    schema_mod,
    set_doc,
    sim_evaluator_text,
    verdict_json,
)


def all_agree(call: Any, **overrides: str) -> list[str]:
    """One reply per item, in order: agrees, with ``overrides`` (item id -> reply text)."""
    return [overrides.get(item.id, agrees_json(item)) for item in call.golden.items]


def judged(call: Any, replies: list[str], **engine: Any) -> tuple[Any, dict[str, Any], Any]:
    fake = FakeJudge(replies, run_id=call.run_id, **engine)
    judgements = run_items(call, fake)
    return judged_bundle(call, judgements), judgements, fake


def test_judge_definitions_are_llm_judge_enum_without_pass_rule() -> None:
    """Rule 8: one definition per item, key ``judge_golden_<id>``, enum agrees/disagrees, no pass
    rule, per conversation, requires transcript and turn timing, mr only, the fixed template of
    the item's mode, and the model reference ``{profile label, model}``."""
    records = mod("judgerecords")
    prompt = mod("judgeprompt")
    metrics = schema_mod("metrics")
    call = build_judged()
    snapshot = call.bundle.snapshot
    definitions = {d.key: d for d in snapshot.metric_definitions}
    judge_keys = [k for k in definitions if k.startswith(records.JUDGE_KEY_PREFIX)]
    assert records.JUDGE_KEY_PREFIX == "judge_golden_"
    assert judge_keys == [f"judge_golden_{i.id}" for i in call.golden.items]
    assert [records.judge_metric_key(i.id) for i in call.golden.items] == judge_keys
    for item in call.golden.items:
        definition = definitions[f"judge_golden_{item.id}"]
        assert definition.kind == "llm_judge"
        assert definition.value_type == "enum"
        assert list(definition.scale.options) == ["agrees", "disagrees"]
        assert definition.pass_rule is None
        assert isinstance(definition.aggregation, metrics.PerConversationAggregation)
        assert list(definition.requires) == ["transcript", "turn_timing"]
        assert list(definition.languages) == ["mr"]
        assert definition.script_sensitive is False
        assert definition.implementation_ref is None
        assert definition.unit is None
        assert definition.prompt_template == prompt.template_text(item.match_mode)
        assert list(definition.prompt_variables) == []
        assert (definition.judge_model_ref.provider, definition.judge_model_ref.model) == (
            "fake/1",
            "fake-model-1",
        )
        names = [p.name for p in definition.parameters]
        expected_name = "expected_facts" if item.match_mode == "all_facts" else "expected_answer"
        assert names == ["question", expected_name]
        assert all(p.default is None for p in definition.parameters), "no default"
        assert [p.value_type for p in definition.parameters] == [
            "string",
            "json" if item.match_mode == "all_facts" else "string",
        ]
    # the bindings carry the item's text as parameters; each is enabled and an optional rubric item
    by_id = {d.id: d for d in snapshot.metric_definitions}
    required_keys = []
    bound = {b.metric.id: b for b in snapshot.metric_bindings}
    for item_ref in snapshot.rubric.items:
        binding = next(b for b in snapshot.metric_bindings if b.id == item_ref.binding.id)
        if item_ref.required:
            required_keys.append(by_id[binding.metric.id].key)
    for item in call.golden.items:
        definition = definitions[f"judge_golden_{item.id}"]
        binding = bound[definition.id]
        assert binding.enabled is True
        expected_params: dict[str, Any] = {"question": item.question}
        if item.match_mode == "all_facts":
            expected_params["expected_facts"] = list(item.expected_facts)
        else:
            expected_params["expected_answer"] = item.expected_answer
        assert dict(binding.params) == expected_params
        metrics.check_binding_params(binding, definition)
        assert any(
            r.binding.id == binding.id and r.required is False for r in snapshot.rubric.items
        )
        assert binding.id in {r.id for r in snapshot.evaluator.metric_bindings}
    assert required_keys == [mod("simscore").SIM_PLACEHOLDER_KEY], (
        "only the inert check is required"
    )
    # the setup builds the same definitions on its own
    built = records.judge_definitions(call.setup, created_at=CREATED_AT)
    assert [d.key for d in built] == judge_keys
    assert all(d.pass_rule is None for d in built)
    # the snapshot holds the validated profile document (digest-covered metadata)
    document = snapshot.metadata[records.JUDGE_PROFILE_METADATA_KEY]
    assert records.JUDGE_PROFILE_METADATA_KEY == "tracer.judge_profile"
    assert document == call.setup.profile_document
    records.validate_judge_profile_document(document)
    assert document["calibration"] == "uncalibrated"
    assert document["golden_set"] == {"key": call.golden.key, "sha256": call.golden.sha256}
    assert document["template_sha256"] == mod("judgeprompt").template_shas()
    # the shipped sim metrics are untouched and the judge keys are not among them
    configuration = call.configuration
    assert not any(k.startswith("judge_golden_") for k in configuration.metric_keys())
    assert list(configuration.judge_keys()) == judge_keys
    plain = configuration_with(
        profile=stored_profile(), evaluator=json.loads(sim_evaluator_text()), judge=None
    )
    assert plain.judge_keys() == ()
    assert records.JUDGE_PROFILE_METADATA_KEY not in plain.snapshot.metadata
    assert [d.key for d in plain.snapshot.metric_definitions] == [
        k for k in definitions if k not in judge_keys
    ]
    # a golden set change is a new snapshot (the texts are digest-covered)
    changed = set_doc()
    changed["items"][0]["expected_facts"][0] = "\u0905"
    other = mod("golden").parse_golden_set(golden_text(changed))
    again = configuration_with(
        profile=stored_profile(),
        evaluator=json.loads(sim_evaluator_text()),
        judge=judge_setup(other),
    )
    texts = [dict(b.params).get("expected_facts") for b in again.snapshot.metric_bindings]
    assert ["\u0905"] in [t[:1] for t in texts if t], "the new text is in the new snapshot"
    assert records.JUDGE_PROFILE_METADATA_KEY in again.snapshot.metadata
    assert again.snapshot.metadata[records.JUDGE_PROFILE_METADATA_KEY]["golden_set"]["sha256"] == (
        other.sha256
    )
    assert other.sha256 != call.golden.sha256


def test_cannot_tell_is_error_judge_undetermined_never_a_value() -> None:
    """Rule 8: ``cannot_tell`` is the absence of a verdict: an ``error`` result with the code
    ``judge_undetermined``, no value, and the definition's enum has no third option."""
    call = build_judged()
    item = call.golden.items[3]
    cannot = verdict_json("cannot_tell", "insufficient_information", ["cannot_tell"])
    bundle, judgements, _ = judged(call, all_agree(call, **{item.id: cannot}))
    results = results_by_key(call, bundle)
    result = results[f"judge_golden_{item.id}"]
    assert result.status == "error"
    assert result.value is None
    assert result.passed is None
    assert result.error.code == "judge_undetermined"
    assert result.error.message.strip()
    assert result.judge is not None, "a request was made, so its provenance is recorded"
    assert result.metadata["tracer.judge"]["verdict"] == "cannot_tell"
    assert result.metadata["tracer.judge"]["reason_code"] == "insufficient_information"
    assert judgements[item.id].state == "cannot_tell"
    # every other item is scored with a value of the enum; nothing in the bundle has the value
    scored = [
        r for k, r in results.items() if k.startswith("judge_golden_") and r.status == "scored"
    ]
    assert len(scored) == 9
    assert all(r.value == "agrees" for r in scored)
    assert all(r.value != "cannot_tell" for r in bundle.results)
    definition = next(d for d in bundle.snapshot.metric_definitions if d.key == result_key(item))
    assert "cannot_tell" not in list(definition.scale.options)


def result_key(item: Any) -> str:
    return f"judge_golden_{item.id}"


def test_judge_result_carries_judgeinfo_without_confidence_or_p_yes() -> None:
    """Rule 8 and invariant I3: ``JudgeInfo`` holds the hash of the messages sent, the parameters
    as sent and the reported model verbatim; no confidence, no ``p_yes``, an empty ``per_turn``;
    the closed document ``tracer.judge/1`` says ``uncalibrated`` and ``none_claimed``."""
    records = mod("judgerecords")
    prompt = mod("judgeprompt")
    call = build_judged()
    reported = "fake\u202emodel-2026-10-28"
    bundle, _judgements, fake = judged(call, all_agree(call), reported_model=reported)
    results = results_by_key(call, bundle)
    for position, item in enumerate(call.golden.items):
        result = results[result_key(item)]
        assert result.status == "scored"
        assert result.value == "agrees"
        assert result.passed is None
        info = result.judge
        assert (info.model_ref.provider, info.model_ref.model) == ("fake/1", "fake-model-1")
        sent = fake.requests[position].messages
        assert info.prompt_sha256 == prompt.prompt_sha256(sent)
        assert info.params == {"max_tokens": 512}
        assert info.reported_model == reported, "verbatim, bidi control kept"
        assert info.confidence is None
        assert list(info.per_turn) == []
        assert result.scorer is None
        assert result.per_turn is None
        dumped = json.dumps(result.model_dump(mode="json", by_alias=True))
        assert "p_yes" not in dumped
        assert '"confidence": null' in dumped or "confidence" not in dumped
        document = result.metadata[records.JUDGE_METADATA_KEY]
        assert records.JUDGE_METADATA_KEY == "tracer.judge"
        records.validate_judge_document(document)
        assert document["calibration"] == "uncalibrated"
        assert document["confidence"] == "none_claimed"
        assert document["verdict"] == "agrees"
        assert document["attempts"] == 1
        assert document["regenerated"] is False
        assert document["exchange_sha256"] == "%064x" % (position + 1)
        assert document["facts"] == [
            {"index": k, "status": "stated"} for k in range(len(item.expected_facts))
        ]
    # a regenerated item records two attempts and the flag, and the hash of the one request
    call2 = build_judged()
    first = call2.golden.items[0]
    bundle2, _, fake2 = judged(call2, ["not json", *all_agree(call2)])
    doc = results_by_key(call2, bundle2)[result_key(first)].metadata["tracer.judge"]
    assert (doc["attempts"], doc["regenerated"]) == (2, True)
    assert fake2.requests[0].messages == fake2.requests[1].messages
    # usage: one judge record per answered request, in the bundle, stage judge
    stages = [u.stage for u in bundle.usage_records]
    assert stages.count("judge") == 10
    assert set(stages) == {"judge"}
    assert all(u.domain == "evaluation" and u.run_id == call.run_id for u in bundle.usage_records)
    assert default_params().max_tokens == 512


def test_judged_bundle_passes_check_run_bundle() -> None:
    """Every judged bundle passes the schema's own bundle check: scored, error, and
    not-applicable results alike, and the same bundle again after a JSON round trip."""
    bundles = schema_mod("bundle")
    call = build_judged()
    bundle, _, _ = judged(call, all_agree(call))
    bundles.check_run_bundle(bundle)
    assert len(bundle.results) == len(call.bundle.results) + 10
    again = bundles.RunBundle.model_validate_json(bundle.model_dump_json(by_alias=True))
    bundles.check_run_bundle(again)
    assert again.model_dump(mode="json", by_alias=True) == bundle.model_dump(
        mode="json", by_alias=True
    )

    # the call ended after four lines: the other six items are errors, never a disagreement
    short = build_judged(lines=4)
    bundle_short, _judgements, fake = judged(
        short, [agrees_json(i) for i in short.golden.items[:4]]
    )
    results = results_by_key(short, bundle_short)
    assert len(fake.requests) == 4
    for item in short.golden.items[4:]:
        result = results[result_key(item)]
        assert result.status == "error"
        assert result.error.code == "question_not_sent"
        assert result.value is None
        assert result.judge is None
    bundles.check_run_bundle(bundle_short)

    # a window with no answer in it is an error too
    quiet = build_judged(lines=2, answers=["\u0905"], answer_starts_ms=[900])
    bundle_quiet, _, _ = judged(quiet, [agrees_json(quiet.golden.items[0])])
    code = results_by_key(quiet, bundle_quiet)[result_key(quiet.golden.items[1])].error.code
    assert code == "answer_window_empty"

    # speech-to-text failed: every judge result is not applicable (missing input), no request
    failed = build_judged(stt_failed=True)
    records = mod("judgerecords")
    results_failed = records.judge_results(
        snapshot=failed.bundle.snapshot,
        run=failed.bundle.run,
        transcripts=failed.bundle.transcripts,
        turns=failed.bundle.turns,
        judgements={},
        created_at=CREATED_AT,
    )
    assert len(results_failed) == 10
    for result in results_failed:
        assert result.status == "not_applicable"
        assert result.na_reason == "missing_input"
        assert list(result.missing_kinds) == ["transcript"]
        assert result.judge is None
        assert result.value is None
    bundles.check_run_bundle(records.with_judge_results(failed.bundle, results_failed, []))

    # a call that was never established: the same, with both kinds missing
    never = build_judged(established=False)
    results_never = records.judge_results(
        snapshot=never.bundle.snapshot,
        run=never.bundle.run,
        transcripts=never.bundle.transcripts,
        turns=never.bundle.turns,
        judgements={},
        created_at=CREATED_AT,
    )
    assert all(r.status == "not_applicable" for r in results_never)
    assert all(list(r.missing_kinds) == ["transcript", "turn_timing"] for r in results_never)
    bundles.check_run_bundle(records.with_judge_results(never.bundle, results_never, []))

    # one agent turn in another language makes every judge result not applicable (rule 11)
    hindi = build_judged(stt_language="hi-IN")
    results_hindi = records.judge_results(
        snapshot=hindi.bundle.snapshot,
        run=hindi.bundle.run,
        transcripts=hindi.bundle.transcripts,
        turns=hindi.bundle.turns,
        judgements={},
        created_at=CREATED_AT,
    )
    assert all(
        r.status == "not_applicable" and r.na_reason == "unsupported_language"
        for r in results_hindi
    )
    bundles.check_run_bundle(records.with_judge_results(hindi.bundle, results_hindi, []))

    # a judgement missing for an item that needs one is a programming error, not a guess
    try:
        records.judge_results(
            snapshot=call.bundle.snapshot,
            run=call.bundle.run,
            transcripts=call.bundle.transcripts,
            turns=call.bundle.turns,
            judgements={},
            created_at=CREATED_AT,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("judge_results accepted a missing judgement")


def test_judge_result_evidence_refs_name_answer_window_turns() -> None:
    """Rule 8: the evidence of a result is one ``turn`` reference per window turn, in seq order,
    and ``transcript_ids`` names the ``our_stt`` transcript; a result with no window has none."""
    call = build_judged(
        lines=3,
        answers=["\u0905", "\u0906", "\u0907\u0908", "\u0909"],
        answer_starts_ms=[500, 1500, 2500, 4300],
    )
    bundle, judgements, _ = judged(call, [agrees_json(i) for i in call.golden.items[:3]])
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    turns = {t.id: t for t in bundle.turns}
    results = results_by_key(call, bundle)
    first = results[result_key(call.golden.items[0])]
    assert [ref.kind for ref in first.evidence_refs] == ["turn", "turn"]
    window = judgements[call.golden.items[0].id].window
    assert [ref.ref_id for ref in first.evidence_refs] == list(window.turn_ids)
    assert window.turn_seqs == (0, 1)
    for ref in first.evidence_refs:
        turn = turns[ref.ref_id]
        assert (ref.start_ms, ref.end_ms) == (turn.start_ms, turn.end_ms)
        assert turn.transcript_id == stt.id
        assert turn.role == "agent"
    assert list(first.transcript_ids) == [stt.id]
    assert first.metadata["tracer.judge"]["window_turn_seqs"] == [0, 1]
    second = results[result_key(call.golden.items[1])]
    assert [ref.ref_id for ref in second.evidence_refs] == [
        next(t.id for t in bundle.turns if t.transcript_id == stt.id and t.seq == 2)
    ]
    assert second.metadata["tracer.judge"]["window_turn_seqs"] == [2]
    assert first.metadata["tracer.judge"]["excluded_turns"] == 0
    assert second.metadata["tracer.judge"]["excluded_turns"] == 2
    # an item whose question was never sent names no turn, and its document says no request
    missing = results[result_key(call.golden.items[5])]
    assert missing.evidence_refs == []
    assert missing.status == "error"
    document = missing.metadata["tracer.judge"]
    assert (document["attempts"], document["verdict"], document["exchange_sha256"]) == (
        0,
        None,
        None,
    )
    assert document["window_turn_seqs"] == []
    mod("judgerecords").validate_judge_document(document)
    assert list(missing.transcript_ids) == [stt.id]


def test_expected_outcome_check_stays_inert_with_judge() -> None:
    """Rule 10, ADR 0027 rule 12a and invariant I4: the inert placeholder is the only required
    rubric item of a judged snapshot, no RunVerdict exists, and the outcome stays not evaluated."""
    simscore = mod("simscore")
    call = build_judged()
    bundle, _, _ = judged(call, all_agree(call))
    snapshot = bundle.snapshot
    by_id = {d.id: d for d in snapshot.metric_definitions}
    required = [
        by_id[next(b for b in snapshot.metric_bindings if b.id == item.binding.id).metric.id]
        for item in snapshot.rubric.items
        if item.required
    ]
    assert len(required) == 1
    inert = required[0]
    assert inert.key == simscore.SIM_PLACEHOLDER_KEY == "simulated_call_no_outcome_check"
    assert (inert.kind, inert.value_type) == ("llm_judge", "boolean")
    assert (inert.judge_model_ref.provider, inert.judge_model_ref.model) == ("none", "not_run")
    assert snapshot.evaluator.expected_outcome == "The agent answers each question."
    # the judge definitions are not expected-outcome checks and have no pass rule
    assert all(
        d.pass_rule is None
        for d in snapshot.metric_definitions
        if d.key.startswith("judge_golden_")
    )
    assert bundle.verdict is None, "no RunVerdict is produced"
    assert bundle.run.metadata["tracer.sim_outcome"]["outcome"] == "not_evaluated"
    assert not any(
        r.passed is not None
        for r in bundle.results
        if r.metric.id
        in {d.id for d in snapshot.metric_definitions if d.key.startswith("judge_golden_")}
    )
    # a run without a golden set embeds none of it and is otherwise the same
    plain = configuration_with(
        profile=stored_profile(), evaluator=json.loads(sim_evaluator_text()), judge=None
    )
    plain_required = [item for item in plain.snapshot.rubric.items if item.required]
    assert len(plain_required) == 1
    assert [d.key for d in plain.snapshot.metric_definitions if d.key == inert.key] == [inert.key]
    assert "judge_golden_" not in " ".join(d.key for d in plain.snapshot.metric_definitions)


#: the claim words of ADR 0023 rule 1, A1 and A4 (matched case-insensitively as whole words)
CLAIM_WORDS = (
    "accuracy",
    "WER",
    "CER",
    "error rate",
    "correct",
    "recall",
    "precision",
    "latency",
    "response time",
    "quality",
    "task success",
    "realism",
    "realistic",
    "fidelity",
    "deterministic",
    "determinism",
    "incorrect",
    "correctness",
    "factual",
    "factuality",
    "truthful",
    "hallucination",
    "ground truth",
    "calibrated",
    "probability",
    "confidence",
    "pass",
    "fail",
    "passed",
    "failed",
    "score of the agent",
)


def claim_words_in(text: str) -> list[str]:
    return [
        word
        for word in CLAIM_WORDS
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text, re.IGNORECASE)
    ]


def test_judge_messages_and_templates_hold_no_claim_word() -> None:
    """ADR 0023 A4: the text Tracer writes into a judge result and into the shipped templates
    uses none of the claim words (an error is called an error)."""
    records = mod("judgerecords")
    prompt = mod("judgeprompt")
    judge = mod("judge")
    assert set(records.JUDGE_ERROR_MESSAGES) == set(judge.JUDGE_ERROR_CODES)
    assert tuple(judge.JUDGE_ERROR_CODES) == (
        "judge_undetermined",
        "judge_output_invalid",
        "judge_request_failed",
        "judge_auth_failed",
        "judge_requests_cap_reached",
        "judge_tokens_cap_reached",
        "question_not_sent",
        "answer_window_empty",
    )
    for code, message in records.JUDGE_ERROR_MESSAGES.items():
        assert message.strip(), code
        assert message.isascii(), code
        assert claim_words_in(message) == [], (code, claim_words_in(message))
    for mode in ("all_facts", "equivalent_answer"):
        assert claim_words_in(prompt.template_text(mode)) == [], mode
    assert claim_words_in("a result that passed") == ["passed"], "the matcher has teeth"
