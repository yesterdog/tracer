# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch J part 1 evalcore tests (P4-J01 to P4-J11). Support module,
not collected. Contract: docs/phase4/api-surface-batch-j1.md.

Nothing of the batch J modules is imported at module level (``mod`` imports lazily), so before the
modules exist each test fails on its own with ``ModuleNotFoundError`` and the directory still
collects. All text is invented; the Marathi text lives in the fixture ``golden_mr.json`` and in
``\\u`` escapes here, so that this file stays ASCII. The calls are synthetic: the speech-to-text
outcome is made by the Phase 3 response mapper from invented bodies, the bundle by the product's
pure builder. No test here opens a socket, reads a clock or holds a key.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    CREATED_AT,
    LANGUAGE,
    RATE,
    STARTED_AT,
    TTS_LANGUAGE,
    WAIT_MS,
    dumps,
    outcome_document,
    persona_doc,
    privacy,
    stored_profile,
    timing_document,
)

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
FIXTURES = TESTS / "fixtures"
GOLDEN_FIXTURE = FIXTURES / "golden_mr.json"
SIM_FIXTURE = FIXTURES / "sim_mr.json"

#: a canary that must never be quoted by a refusal, an error, a record or a log
CANARY = "".join(["CANARY", "-", "zq7fc3d1", "-", "9ae2b8"])
MODES = ("all_facts", "equivalent_answer")
REASON_CODES = (
    "all_facts_stated",
    "fact_contradicted",
    "fact_absent",
    "meaning_differs",
    "meaning_same",
    "answer_off_topic",
    "answer_unintelligible",
    "insufficient_information",
)
FACT_STATUSES = ("stated", "contradicted", "absent", "cannot_tell")
VERDICTS = ("agrees", "disagrees", "cannot_tell")
#: the code-to-verdict table of ADR 0030 Ambiguities 3 and 4, per match mode (contract section 6.3)
CODE_VERDICT = {
    "all_facts": {
        "all_facts_stated": "agrees",
        "fact_contradicted": "disagrees",
        "fact_absent": "disagrees",
        "answer_off_topic": "disagrees",
        "answer_unintelligible": "cannot_tell",
        "insufficient_information": "cannot_tell",
    },
    "equivalent_answer": {
        "meaning_same": "agrees",
        "meaning_differs": "disagrees",
        "answer_off_topic": "disagrees",
        "answer_unintelligible": "cannot_tell",
        "insufficient_information": "cannot_tell",
    },
}


def mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalcore.{name}")


def schema_mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalschema.{name}")


# ------------------------------------------------------------------------------ the fixtures


def golden_fixture() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(GOLDEN_FIXTURE.read_text("ascii"))
    return loaded


def sim_scenario(scenario_id: str = "sim-mr-s1") -> dict[str, Any]:
    document = json.loads(SIM_FIXTURE.read_text("ascii"))
    scenario: dict[str, Any] = next(s for s in document["scenarios"] if s["id"] == scenario_id)
    return scenario


def golden_text(document: Mapping[str, Any] | None = None) -> str:
    """The golden set of the fixture (or ``document``) as the file text a user would write."""
    chosen = golden_fixture()["golden_set"] if document is None else document
    return json.dumps(chosen, ensure_ascii=False)


def golden_set() -> Any:
    return mod("golden").parse_golden_set(golden_text())


def sim_evaluator_text(**changes: Any) -> str:
    """The sim_mr scenario s1 as a ``tracer.sim_evaluator/1`` file (ten scripted lines)."""
    scenario = sim_scenario()
    document: dict[str, Any] = {
        "format": "tracer.sim_evaluator/1",
        "name": "synthetic golden apparatus",
        "language": "mr",
        "instructions": "Ask the scripted questions of the agent.",
        "expected_outcome": "The agent answers each question.",
        "scripted_turns": scenario["scripted_turns"],
        "max_duration_ms": 600_000,
    }
    document.update(changes)
    return json.dumps(document, ensure_ascii=False)


def sim_evaluator() -> Any:
    return mod("simspec").parse_sim_evaluator(sim_evaluator_text())


def item_doc(index: int = 0, **changes: Any) -> dict[str, Any]:
    """A valid all_facts item of the fixture's golden set (a deep copy), with ``changes``."""
    base: dict[str, Any] = json.loads(json.dumps(golden_fixture()["golden_set"]["items"][index]))
    base.update(changes)
    return base


def set_doc(items: Sequence[Mapping[str, Any]] | None = None, **changes: Any) -> dict[str, Any]:
    document = json.loads(json.dumps(golden_fixture()["golden_set"]))
    if items is not None:
        document["items"] = [dict(i) if isinstance(i, Mapping) else i for i in items]
    document.update(changes)
    return document  # type: ignore[no-any-return]


# ----------------------------------------------------------------------- a fake judge engine


@dataclass
class FakeJudge:
    """A scripted ``JudgeEngine``: one reply text per request, in order (a string, or a callable
    of the request, or an exception instance to return as a failure). It records every request,
    builds its own usage records, opens no socket and reads no clock."""

    replies: Sequence[Any]
    run_id: Any = None
    finish_reason: str = "stop"
    reported_model: str | None = "fake-model-1"
    requests: list[Any] = field(default_factory=list)
    events: list[tuple[str, int]] = field(default_factory=list)
    streams_usage: bool = False
    prompt_tokens: int = 40
    completion_tokens: int = 9

    def complete(self, request: Any, context: Any, *, on_usage: Any = None) -> Any:
        judge = mod("judge")
        self.requests.append(request)
        self.events.append(("request", request.index))
        position = len(self.requests) - 1
        if position >= len(self.replies):
            raise AssertionError(f"request {position + 1} was not scripted")
        step = self.replies[position]
        if callable(step):
            step = step(request)
        if isinstance(step, judge.JudgeError):
            return judge.JudgeOutcome(
                text=None,
                finish_reason=None,
                reported_model=None,
                request_id=None,
                exchange_sha256=None,
                params_sent=None,
                usage=(),
                failure=step,
            )
        record = usage_record(context.run_id, f"req-{request.index}")
        if self.streams_usage and on_usage is not None:
            on_usage(record)
        return judge.JudgeOutcome(
            text=step,
            finish_reason=self.finish_reason,
            reported_model=self.reported_model,
            request_id=f"req-{request.index}",
            exchange_sha256=("%064x" % (position + 1)),
            params_sent={"max_tokens": request.params.max_tokens},
            usage=(record,),
            failure=None,
        )


def usage_record(run_id: Any, tag: str, *, prompt: int = 40, completion: int = 9) -> Any:
    """A ``judge`` usage record of the evaluation domain (what the engines build)."""
    ids = schema_mod("ids")
    usage = schema_mod("usage")
    return usage.UsageRecord.model_validate(
        {
            "id": ids.UsageRecordId.new(),
            "schema_version": importlib.import_module("tracer_evalschema").SCHEMA_VERSION,
            "created_at": CREATED_AT,
            "run_id": run_id,
            "domain": "evaluation",
            "stage": "judge",
            "provider": "fake/1",
            "model": "fake-model",
            "provider_call_ref": {"system": "fake/1", "kind": "request_id", "value": tag},
            "quantities": [
                {"unit": "input_tokens", "count": prompt},
                {"unit": "output_tokens", "count": completion},
                {"unit": "requests", "count": 1},
            ],
            "source": "provider_reported",
        }
    )


def verdict_json(
    verdict: str, reason: str, facts: Sequence[str] = (), *, extra: Mapping[str, Any] | None = None
) -> str:
    """The reply text a well-behaved judge returns."""
    document: dict[str, Any] = {
        "verdict": verdict,
        "reason_code": reason,
        "facts": [{"index": i, "status": s} for i, s in enumerate(facts)],
    }
    document.update(extra or {})
    return json.dumps(document, separators=(",", ":"))


def agrees_json(item: Any) -> str:
    if item.match_mode == "all_facts":
        return verdict_json("agrees", "all_facts_stated", ["stated"] * len(item.expected_facts))
    return verdict_json("agrees", "meaning_same")


# --------------------------------------------------------------------- a judged synthetic call

LINE_GAP_MS = 2000
LINE_LEN_MS = 200
ANSWER_OFFSET_MS = 700


@dataclass
class JudgedCall:
    """A simulated bundle of the ten scripted lines of the fixture with a judge configuration."""

    bundle: Any
    configuration: Any
    setup: Any
    golden: Any
    stt: Any
    run_id: Any
    artifact_id: Any
    call_end_ms: int
    stt_turns: tuple[Any, ...]
    script_turns: tuple[Any, ...]


def stt_outcome_at(
    texts: Sequence[str],
    starts_ms: Sequence[int],
    *,
    run_id: Any,
    artifact_id: Any,
    language_label: str | None = "mr-IN",
    length_ms: int = 500,
) -> Any:
    """An ``our_stt`` agent-channel outcome of ``texts``, one chunk each, the chunk ``k`` starting
    at ``starts_ms[k]`` on the artifact timeline (the Phase 3 response mapper builds the turns)."""
    from tracer_evalcore.audio.chunker import Chunk, ChunkPlan
    from tracer_evalcore.stt import SttContext
    from tracer_evalproviders.sarvam.mapping import map_responses
    from tracer_evalproviders.sarvam.models import ChunkResponse, parse_response

    chunks = tuple(
        Chunk(
            index=k,
            start_frame=RATE * start // 1000,
            end_frame=RATE * (start + length_ms) // 1000,
            sample_rate_hz=RATE,
            channels=1,
            wav=b"RIFF",
        )
        for k, start in enumerate(starts_ms)
    )
    plan = ChunkPlan(
        sample_rate_hz=RATE,
        channels=2,
        max_chunk_ms=25_000,
        search_ms=5_000,
        total_frames=RATE * (max(starts_ms, default=0) // 1000 + 3),
        chunks=chunks,
        channel=1,
        activity_wait_ms=WAIT_MS,
    )
    answered = []
    for k, text in enumerate(texts):
        body: dict[str, Any] = {"request_id": f"req-{k:04d}", "transcript": text}
        # one phrase for the whole chunk, so that the turn has a start and an end on the timeline
        body["timestamps"] = {
            "words": [text],
            "start_time_seconds": [0.0],
            "end_time_seconds": [length_ms / 1000],
        }
        if language_label is not None:
            body["language_code"] = language_label
        answered.append(ChunkResponse(k, parse_response(json.dumps(body).encode()), None, 1))
    context = SttContext(
        run_id=run_id,
        audio_artifact_id=artifact_id,
        created_at=CREATED_AT,
        privacy=privacy(),
        configured_language=LANGUAGE,
        role="agent",
    )
    return map_responses(plan, answered, model="saaras:v4", context=context)


def configuration_with(
    *, profile: Mapping[str, Any], evaluator: Mapping[str, Any], judge: Any = None
) -> Any:
    """A sealed simulated-run configuration (the product's) with an optional judge setup."""
    spec = mod("simspec")
    return mod("simscore").build_simulated_configuration(
        evaluator=spec.parse_sim_evaluator(dumps(evaluator)),
        persona=spec.parse_sim_persona(dumps(persona_doc())),
        test_profile=None,
        sim_profile=dict(profile),
        flag_min_confidence=0.5,
        normaliser="mr-Deva/1",
        created_at=CREATED_AT,
        judge=judge,
    )


def judge_setup(golden: Any, *, model: str = "fake-model-1", profile: str = "fake/1") -> Any:
    """The ``JudgeSetup`` of a fake judge over ``golden`` (profile document built and validated)."""
    records = mod("judgerecords")
    document = records.build_judge_profile_document(
        profile=profile,
        host=None,
        path=None,
        model=model,
        params={"max_tokens": 512},
        max_judge_requests=None,
        max_judge_tokens=None,
        key_env=None,
        golden=golden,
    )
    return records.JudgeSetup(
        golden=golden, model_ref={"provider": profile, "model": model}, profile_document=document
    )


def build_judged(
    *,
    answers: Sequence[str] | None = None,
    answer_starts_ms: Sequence[int] | None = None,
    lines: int = 10,
    complete_last_line: bool = True,
    stt_language: str | None = "mr-IN",
    stt_failed: bool = False,
    established: bool = True,
    golden: Any = None,
    mode: str = "scripted",
    export: Any = None,
) -> JudgedCall:
    """A simulated bundle built by the product from ``lines`` of the sim_mr scenario s1, each
    sent at ``2000 * k`` ms and ending 200 ms later, with agent answers (default: the exact plant of
    each item) starting 700 ms after each line ends; the judge configuration is the fixture's."""
    ids = schema_mod("ids")
    bundle_mod = mod("simbundle")
    fixture = golden_fixture()
    golden = golden if golden is not None else golden_set()
    setup = judge_setup(golden)
    scenario = sim_scenario()
    exact = {p["item_id"]: p["text"] for p in fixture["plants"] if p["kind"] == "exact_facts"}
    texts = list(answers) if answers is not None else [exact[i.id] for i in golden.items][:lines]
    starts = (
        list(answer_starts_ms)
        if answer_starts_ms is not None
        else [LINE_GAP_MS * k + LINE_LEN_MS + ANSWER_OFFSET_MS for k in range(len(texts))]
    )
    run_id = ids.RunId.new()
    artifact_id = ids.ArtifactId.new()
    stored = stored_profile()
    configuration = configuration_with(
        profile=stored,
        evaluator=json.loads(sim_evaluator_text()),
        judge=setup,
    )
    sent = lines if established else 0
    timing = timing_document(established=established)
    outcome = outcome_document(established=established, end_reason="script_complete", mode=mode)
    recording = (
        bundle_mod.RecordingFacts(
            artifact_id, "file:///tmp/call-recording.wav", "ab" * 32, 44 + 4 * RATE * 25, RATE
        )
        if established
        else None
    )
    caller_lines = tuple(
        bundle_mod.CallerLineFact(
            seq=k,
            text=scenario["scripted_turns"][k]["text"],
            language=TTS_LANGUAGE,
            start_ms=LINE_GAP_MS * k,
            end_ms=LINE_GAP_MS * k + LINE_LEN_MS,
            complete=not (k == sent - 1 and not complete_last_line),
        )
        for k in range(sent)
    )
    call = bundle_mod.SimulatedCall(
        run_id=run_id,
        established=established,
        started_at=STARTED_AT if established else None,
        recording=recording,
        caller_lines=caller_lines,
        caller_text_origin=mode,
        timing=timing,
        outcome=outcome,
        usage=(),
    )
    stt = None
    warnings: tuple[Any, ...] = ()
    if established and not stt_failed:
        stt = stt_outcome_at(
            texts, starts, run_id=run_id, artifact_id=artifact_id, language_label=stt_language
        )
    elif stt_failed:
        from tracer_evalschema.events import IngestWarning

        warnings = (
            IngestWarning(code="stt_failed", path="/stt", message="SttServerError: chunk 0"),
        )
    project = bundle_mod.simulated_project(name="synthetic project", created_at=CREATED_AT)
    target = bundle_mod.simulated_agent_target(
        name="sample agent",
        transport="ws",
        url="ws://127.0.0.1:8765/ws-client",
        created_at=CREATED_AT,
    )
    built = bundle_mod.build_simulated_bundle(
        configuration=configuration,
        project=project,
        agent_target=target,
        call=call,
        stt=stt,
        export=export,
        stt_warnings=warnings,
        costs=None,
        created_at=CREATED_AT,
    )
    call_end_ms = LINE_GAP_MS * 10
    stt_turns = () if stt is None else tuple(stt.turns)
    script_turns = tuple(t for t in built.turns if t.role == "caller")
    return JudgedCall(
        built,
        configuration,
        setup,
        golden,
        stt,
        run_id,
        artifact_id,
        call_end_ms,
        stt_turns,
        script_turns,
    )


def run_items(
    call: JudgedCall,
    engine: Any,
    *,
    only: Sequence[str] | None = None,
    params: Any = None,
) -> dict[str, Any]:
    """Judge every item of the call's golden set (or ``only``) with ``engine`` and return the
    judgements by item id; the window of each item is read from the call's own records."""
    judge = mod("judge")
    window = mod("judgewindow")
    chosen = params or default_params()
    out: dict[str, Any] = {}
    index = 0
    stt_transcript = next(t for t in call.bundle.transcripts if t.source == "our_stt")
    for item in call.golden.items:
        if only is not None and item.id not in only:
            continue
        found = window.answer_window(
            scripted_turn_index=item.scripted_turn_index,
            transcripts=call.bundle.transcripts,
            turns=call.bundle.turns,
            call_end_ms=call.call_end_ms,
        )
        context = judge.JudgeContext(run_id=call.run_id, created_at=CREATED_AT)
        judged = judge.judge_item(
            engine=engine,
            item=item,
            window=found,
            params=chosen,
            context=context,
            first_index=index,
            on_usage=lambda record: None,
        )
        index += judged.attempts
        out[item.id] = judged
    del stt_transcript
    return out


def default_params(**changes: Any) -> Any:
    judge = mod("judge")
    fields: dict[str, Any] = {
        "model": "fake-model-1",
        "max_tokens": 512,
        "temperature": None,
        "seed": None,
        "reasoning_effort": None,
        "provider_slug": None,
        "extra_headers": (),
    }
    fields.update(changes)
    return judge.JudgeParams(**fields)


def judge_request(index: int = 0, **changes: Any) -> Any:
    """A ``JudgeRequest`` for the first fixture item (all_facts), with ``changes``."""
    judge = mod("judge")
    prompt = mod("judgeprompt")
    item = golden_set().items[0]
    messages = prompt.build_judge_messages(item, "x")
    fields: dict[str, Any] = {
        "index": index,
        "item_id": item.id,
        "attempt": 1,
        "match_mode": item.match_mode,
        "fact_count": len(item.expected_facts),
        "messages": messages,
        "params": default_params(),
    }
    fields.update(changes)
    return judge.JudgeRequest(**fields)


def results_by_key(call: JudgedCall, bundle: Any = None) -> dict[str, Any]:
    chosen = call.bundle if bundle is None else bundle
    keys = {d.id: d.key for d in chosen.snapshot.metric_definitions}
    return {keys[r.metric.id]: r for r in chosen.results}


def judged_bundle(call: JudgedCall, judgements: Mapping[str, Any]) -> Any:
    """The call's bundle with the judge results and judge usage appended (the product's)."""
    records = mod("judgerecords")
    results = records.judge_results(
        snapshot=call.bundle.snapshot,
        run=call.bundle.run,
        transcripts=call.bundle.transcripts,
        turns=call.bundle.turns,
        judgements=judgements,
        created_at=CREATED_AT,
    )
    usage = [u for j in judgements.values() for u in j.usage]
    return records.with_judge_results(call.bundle, results, usage)


Replier = Callable[[Any], str]
