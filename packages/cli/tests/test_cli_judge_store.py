# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J20: the store of a judged run and of its rescoring bundles (ADR 0030 rules 8 and 19; ADR
0022 rule 5; ADR 0007 Decision 5; the batch E store tests as models).

Contract: docs/phase4/api-surface-batch-j2.md section 10. The judged bundle round-trips through the
SQLite store byte for byte; a rescoring bundle stores only its own rows (the judge results and the
judge usage); both store with the same rows on SQLite and Postgres (``postgres`` marker, loopback,
``TRACER_TEST_POSTGRES_URL``). The store holds the SHA-256 of an exchange, never a raw reply.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tracer_evaldb
from cli_p4e_support import STAMP, mod, run_sim, write_inputs  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    ENV_VAR,
    PUBLIC_IP,
    JudgeSim,
    chat_response,
    jmod,
    judge_argv,
    judge_doc,
    judge_results,
    load_bundle,
    loopback_judge_extra,
    public_add,
    public_files,
    resolver,
    run_cmd,
    run_judged,
    two_answers,
    user_document,
    verdict_for,
)

POSTGRES_URL_ENV = "TRACER_TEST_POSTGRES_URL"
RAW = "RAWREPLYCANARY not json at all"


class Captured:
    def __init__(self) -> None:
        self.project: Any = None
        self.target: Any = None
        self.bundle: Any = None


def capture(monkeypatch: pytest.MonkeyPatch) -> Captured:
    store = mod("store")
    original = store.store_run
    seen = Captured()

    def spy(engine: Any, *, project: Any, agent_target: Any, bundle: Any) -> Any:
        seen.project, seen.target, seen.bundle = project, agent_target, bundle
        return original(engine, project=project, agent_target=agent_target, bundle=bundle)

    monkeypatch.setattr(store, "store_run", spy)
    return seen


def test_judged_bundle_round_trips_through_store_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0030 rule 19 and ADR 0027 invariant I9: the judged bundle read back from the store is
    the bundle that was stored, in value and in bytes; storing it again changes nothing; the file
    is private. The judge documents validate; the usage is stage ``judge``; the cost is not
    available (no USD catalog); a raw reply is never in the store, only the hash of the exchange
    file that holds it."""
    seen = capture(monkeypatch)
    sim = JudgeSim([chat_response(RAW, rid="r-0")])  # q0: prose first, then a valid reply
    run, files, _ = run_judged(tmp_path, engine="local_loopback/1", sim=sim)
    assert run.code == 0, (run.out, run.err)
    store = mod("store")
    path = files.out / "tracer.sqlite"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    engine = store.open_sqlite_store(files.out)
    try:
        loaded = store.load_run(engine, seen.bundle.id)
        assert loaded == seen.bundle
        assert loaded.model_dump_json() == seen.bundle.model_dump_json()
        assert loaded.model_dump_json().encode("utf-8") == seen.bundle.model_dump_json().encode(
            "utf-8"
        )
        again = store.store_run(
            engine, project=seen.project, agent_target=seen.target, bundle=seen.bundle
        )
        assert again.created is False
        assert sum(again.inserted.values()) == 0
    finally:
        engine.dispose()
    bundle = seen.bundle
    judge_usage = [u for u in bundle.usage_records if u.stage == "judge"]
    assert len(judge_usage) == 3, "q0 twice (a regeneration), q1 once"
    assert {u.domain for u in judge_usage} == {"evaluation"}
    assert {u.provider for u in judge_usage} == {"local_loopback/1"}
    summaries = [s for s in bundle.cost_summaries if s.domain == "evaluation"]
    assert len(summaries) == 1, "no USD catalog: no cost"
    assert summaries[0].total is None, "no USD catalog: no cost"
    assert summaries[0].completeness == "none"
    assert {r.na_reason for r in bundle.cost_records} == {"no_price_for_model"}
    assert len(bundle.cost_records) == 3, "one not-available record for each judge usage record"
    assert run.value("cost") == "not available (no_price_for_model)"
    # the documents are the ADR documents, free of text
    jrec = jmod("judgerecords")
    jrec.validate_judge_profile_document(bundle.snapshot.metadata[jrec.JUDGE_PROFILE_METADATA_KEY])
    results = judge_results(bundle)
    assert list(results) == ["q0", "q1"]
    for result in results.values():
        jrec.validate_judge_document(judge_doc(result))
        assert result.metadata_flags == []
    assert bundle.snapshot.metadata_flags == []
    assert bundle.run.metadata_flags == []
    assert judge_doc(results["q0"])["attempts"] == 2
    assert judge_doc(results["q0"])["regenerated"]
    # the store holds the hash of the file of the last attempt, never the reply
    exchange = files.out / "judge" / str(bundle.run.id) / "item-q0-2.json"
    assert (
        judge_doc(results["q0"])["exchange_sha256"]
        == hashlib.sha256(exchange.read_bytes()).hexdigest()
    )
    first = files.out / "judge" / str(bundle.run.id) / "item-q0-1.json"
    assert RAW in first.read_text("utf-8")
    assert RAW.encode() not in path.read_bytes()
    assert RAW not in bundle.model_dump_json()
    assert not any(RAW in line for line in run.out.splitlines())
    assert RAW not in run.report()
    assert RAW not in run.err


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "sqlite":
        engine = mod("store").open_sqlite_store(tmp_path / "backend")
        try:
            yield engine
        finally:
            engine.dispose()
        return
    url = os.environ.get(POSTGRES_URL_ENV)
    assert url, f"{POSTGRES_URL_ENV} is not set"
    engine = tracer_evaldb.create_store_engine(url)
    tracer_evaldb.metadata.drop_all(engine)
    tracer_evaldb.metadata.create_all(engine)
    try:
        yield engine
    finally:
        tracer_evaldb.metadata.drop_all(engine)
        engine.dispose()


def test_judged_and_rescoring_bundles_store_on_both_backends(
    backend: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0030 rule 19 and ADR 0022 rule 5: the judged bundle and its rescoring bundle store with
    the same rows on SQLite and Postgres and read back identical; the rescoring stores only its own
    rows (two judge results, two judge usage records, its cost rows and its envelope): every other
    record is shared and stored once. The SQLite parametrisation always runs; the Postgres one
    carries the ``postgres`` marker."""
    seen = capture(monkeypatch)
    run, files, jf = run_judged(tmp_path / "run", engine="local_loopback/1", sim=JudgeSim())
    assert run.code == 0
    first = seen.bundle
    rescore = run_cmd(
        judge_argv(
            files.out,
            run.value("bundle"),
            jf,
            "local_loopback/1",
            *loopback_judge_extra(jf),
        ),
        transport=JudgeSim(default="disagrees"),
    )
    assert rescore.code == 0, (rescore.out, rescore.err)
    second = load_bundle(files.out, rescore.value("repeat 1 of 1"))
    store = mod("store")
    stored = store.store_run(backend, project=seen.project, agent_target=seen.target, bundle=first)
    extra = store.store_rescoring(backend, second)
    reference = store.open_sqlite_store(tmp_path / "reference")
    try:
        expected = store.store_run(
            reference, project=seen.project, agent_target=seen.target, bundle=first
        )
        expected_extra = store.store_rescoring(reference, second)
    finally:
        reference.dispose()
    assert stored.created is True
    assert extra.created is True
    assert dict(stored.inserted) == dict(expected.inserted)
    assert dict(extra.inserted) == dict(expected_extra.inserted)
    inserted = dict(extra.inserted)
    assert inserted["metric_result"] == 2
    assert inserted["usage_record"] == 2
    assert inserted["run_bundle"] == 1
    for shared in ("transcript", "turn", "run", "artifact", "config_snapshot"):
        assert inserted.get(shared, 0) == 0, shared
    for bundle in (first, second):
        loaded = store.load_run(backend, bundle.id)
        assert loaded == bundle
        assert loaded.model_dump_json() == bundle.model_dump_json()
    # storing either again changes nothing
    assert sum(store.store_rescoring(backend, second).inserted.values()) == 0
    assert sum(store.store_rescoring(backend, first).inserted.values()) == 0


def test_a_failed_store_write_keeps_the_judge_usage_and_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paid judge requests are not lost when the store cannot be written (the rule of the
    recording and the speech-to-text, applied to step 13b): exit 1, the report with its judge
    section, a recovery file with the judge usage, and the lines say what was not written."""
    store = mod("store")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "store_run", broken)
    run, files, _ = run_judged(tmp_path, engine="local_loopback/1", sim=JudgeSim())
    assert run.code == 1
    assert run.err == "error: unexpected failure (RuntimeError)\n"
    assert run.value("store") == "not written"
    (recovery,) = list(files.out.glob("usage-recovery-*.json"))
    assert run.out.splitlines()[-1] == f"usage recovery: {recovery}"
    document = json.loads(recovery.read_text("ascii"))
    assert document["failed_step"] == "store"
    assert [u["stage"] for u in document["usage_records"]] == ["judge", "judge"]
    assert "disk full" not in recovery.read_text("ascii") + run.out + run.err
    page = (files.out / f"report-{STAMP}.html").read_text("utf-8")
    assert 'id="judge"' in page


def test_credits_reported_by_the_provider_are_never_stored_as_cost(tmp_path: Path) -> None:
    """ADR 0030 rule 18: a provider that reports a cost in credits (``usage.cost``) does not make a
    cost: the member is read nowhere and stored nowhere, the usage holds the token counts, and the
    cost is not available without a USD catalog."""

    def with_credits(request: Any, index: int) -> Any:
        reply = chat_response(
            verdict_for(user_document(request), "agrees"),
            rid=f"c-{index}",
            extra={
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 15,
                    "cost": 0.8421,
                    "cost_details": {"upstream_inference_cost": 0.8421},
                }
            },
        )
        return reply

    run, files, _ = run_judged(
        tmp_path, engine="local_loopback/1", sim=JudgeSim([with_credits, with_credits])
    )
    assert run.code == 0, (run.out, run.err)
    bundle = run.bundle()
    assert run.value("cost") == "not available (no_price_for_model)"
    assert [s.total for s in bundle.cost_summaries] == [None]
    text = bundle.model_dump_json()
    assert "0.8421" not in text
    assert "0.8421" not in run.out + run.report()
    assert b"0.8421" not in (files.out / "tracer.sqlite").read_bytes()
    assert run.value("judge usage") == "2 requests, 240 input tokens, 30 output tokens"


def test_the_profile_document_says_what_was_sent_to_whom(tmp_path: Path) -> None:
    """ADR 0030 rule 8 and ADR 0027 rule 12a: the snapshot holds ``tracer.judge_profile/1``: the
    profile, the host and the path of the allowlist entry (a URL path is not a file path), the
    model, the parameters as they are sent, the caps, the NAME of the key variable, the golden set
    and the SHA-256 of each template. A fake or replayed run names no host, path or key variable.
    The model reference of every definition is the profile and the model."""
    jrec = jmod("judgerecords")
    key = jrec.JUDGE_PROFILE_METADATA_KEY
    loopback, _, jf = run_judged(tmp_path / "loopback", engine="local_loopback/1", sim=JudgeSim())
    document = loopback.bundle().snapshot.metadata[key]
    assert document["profile"] == "local_loopback/1"
    assert (document["host"], document["path"], document["key_env"]) == (
        "127.0.0.1:8123", "/v1/chat/completions", None,
    )  # fmt: skip
    assert document["model"] == "judge-model-1"
    assert document["params"] == {"max_tokens": 300}
    assert document["caps"] == {"max_judge_requests": 10, "max_judge_tokens": 100000}
    assert document["golden_set"] == {
        "key": "mr_p4j2_set",
        "sha256": hashlib.sha256(jf.golden.read_bytes()).hexdigest(),
    }
    assert document["template_sha256"] == jmod("judgeprompt").template_shas()
    assert document["calibration"] == "uncalibrated"
    for definition in loopback.bundle().snapshot.metric_definitions:
        if definition.key.startswith("judge_golden_"):
            assert definition.judge_model_ref.provider == "local_loopback/1"
            assert definition.judge_model_ref.model == "judge-model-1"
    # a real profile: the host without the default port, the variable name, the token field
    files = write_inputs(tmp_path / "public", scenario=two_answers())
    pjf = public_files(files, config={"temperature": 0.25})
    run = run_sim(
        files,
        add=public_add(pjf, requests=7),
        environ={ENV_VAR: CANARY},
        transport=JudgeSim(),
        world_kw={"judge_resolver": resolver(PUBLIC_IP)},
    )
    assert run.code == 0, (run.out, run.err)
    real = run.bundle().snapshot.metadata[key]
    assert (real["profile"], real["host"], real["path"], real["key_env"]) == (
        "openai/1", "judge.example.test", "/v1/chat/completions", ENV_VAR,
    )  # fmt: skip
    assert real["params"] == {"max_completion_tokens": 300, "temperature": 0.25}
    assert real["caps"] == {"max_judge_requests": 7, "max_judge_tokens": 100000}
    assert CANARY not in json.dumps(real)
    # fake: no host, no path, no variable, no caps
    fake, _, _ = run_judged(tmp_path / "fake", engine="fake:agrees")
    fake_doc = fake.bundle().snapshot.metadata[key]
    assert (fake_doc["profile"], fake_doc["host"], fake_doc["path"], fake_doc["key_env"]) == (
        "fake/1", None, None, None,
    )  # fmt: skip
    assert fake_doc["caps"] == {"max_judge_requests": None, "max_judge_tokens": None}
    assert fake_doc["params"] == {"max_tokens": 300}
