# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J12: the generic chat-completions client of the judge (ADR 0030 rules 12, 16 and 18; ADR
0024 amendments A10 and A13; contract docs/phase4/api-surface-batch-j1.md section 9).

Tracer's own thin client on ``httpx`` (``JsonPoster`` generalised: a bearer token or one named
header, the URL of one validated entry, the stage ``judge``). The request is the subset of Q-J1;
the reply is read as one exact shape; the retries and the taxonomy are those of ADR 0024 rules 9
and 10, proved by running the very scenarios of the Sarvam adapters against this client. Every
test uses ``httpx.MockTransport``; no socket is opened and no key is real.
"""

from __future__ import annotations

import importlib
import inspect
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MESSAGE_CANARY,
    PROFILE_URLS,
    TEXT_CANARY,
    JudgeRig,
    context,
    core,
    jmod,
    judge_kit,
    openai_reply,
    request,
    verdict_text,
)
from evalproviders_phase4_matrix import SCENARIOS  # type: ignore[import-not-found,unused-ignore]
from evalproviders_support import chain, error_body, json_response  # type: ignore[import-not-found,unused-ignore]

FORBIDDEN = (
    "n",
    "tools",
    "tool_choice",
    "logprobs",
    "top_logprobs",
    "user",
    "store",
    "metadata",
    "functions",
    "function_call",
    "top_p",
    "stop",
    "frequency_penalty",
    "presence_penalty",
    "parallel_tool_calls",
    "service_tier",
)


def test_openai_compat_request_body_subset_exact(tmp_path: Path) -> None:
    """Q-J1 and rule 12: ``model``, the messages (one system and one user, string content), the
    token field, ``stream: false`` and nothing else; ``temperature`` only when configured; a
    member of the forbidden list is never sent by any profile."""
    base = tmp_path
    rig = JudgeRig(base / "a", [openai_reply()])
    assert rig.complete().failure is None
    sent = rig.transport.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == PROFILE_URLS["local_loopback/1"]
    body = json.loads(sent.content.decode("utf-8"))
    assert list(body) == ["model", "messages", "max_tokens", "stream"]
    assert body["model"] == "judge-model-1"
    assert body["max_tokens"] == 256
    assert body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert all(set(m) == {"role", "content"} for m in body["messages"])
    assert all(isinstance(m["content"], str) and m["content"] for m in body["messages"])
    asked = request()
    assert [m["content"] for m in body["messages"]] == [m.content for m in asked.messages]
    assert sent.headers["content-type"].split(";")[0] == "application/json"
    assert sent.headers.get_list("accept-encoding") == ["identity"]
    assert "authorization" not in sent.headers, "the loopback profile sends no key"
    assert sent.url.query == b""
    assert sent.extensions["timeout"] == {
        "connect": 10.0,
        "read": 60.0,
        "write": 60.0,
        "pool": 10.0,
    }

    # a configured temperature is sent, zero included; nothing else appears with it
    for number, value in enumerate((0, 0.0, 0.2, 1, 2)):
        rig = JudgeRig(base / f"t{number}", [openai_reply()])
        assert rig.complete(request(temperature=value)).failure is None
        sent_body = rig.body()
        assert list(sent_body) == ["model", "messages", "max_tokens", "stream", "temperature"]
        assert sent_body["temperature"] == value

    # every profile, every optional setting on: still no member of the forbidden list
    full: dict[str, dict[str, Any]] = {
        "anthropic_compat/1": {"temperature": 1.0},
        "gemini_compat/1": {"temperature": 0.5},
        "xai/1": {"temperature": 0.5},
        "openrouter/1": {"temperature": 0.5, "seed": 7, "provider_slug": "provider-one"},
        "sarvam_v2/1": {"temperature": 0.5, "reasoning_effort": "low"},
        "openai/1": {"temperature": 0.5, "seed": 7},
        "local_loopback/1": {"temperature": 0.5},
    }
    for name, settings in full.items():
        rig = JudgeRig(base / name.replace("/", "_"), [openai_reply()], profile=name)
        assert rig.complete(request(**settings)).failure is None, name
        sent_body = rig.body()
        assert not set(sent_body) & set(FORBIDDEN), name
        assert sent_body["stream"] is False
        assert "model" in sent_body
        assert isinstance(sent_body["messages"], list)
        assert len(sent_body["messages"]) == 2
        # the call log of the request holds integers only: no text, no key
        assert {x["stage"] for x in rig.log_lines} == {"judge"}, name
        assert CANARY_KEY not in rig.log_text
        assert "judge-model-1" not in rig.log_text
        for message in sent_body["messages"]:
            assert message["content"][:30] not in rig.log_text

    # the engine defaults nothing: sleep, clock and transport are injected, the stage is fixed
    parameters = inspect.signature(jmod("openai_compat").OpenAICompatJudge.__init__).parameters
    assert parameters["sleep"].default is time.sleep
    assert parameters["monotonic"].default is time.monotonic
    assert parameters["transport"].default is None
    assert parameters["api_key"].default is inspect.Parameter.empty
    assert parameters["profile"].default is inspect.Parameter.empty
    assert parameters["url"].default is inspect.Parameter.empty
    assert core("judge").UsageStreamingJudgeEngine is not None


def _valid_body() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        openai_reply(cached=10, reasoning=5, fingerprint="fp_1").content
    )
    return loaded


def _with(path: str, value: Any) -> bytes:
    """The valid body with the member at ``path`` (dotted) replaced; ``...`` removes it."""
    document = _valid_body()
    node: Any = document
    steps = path.split(".")
    for step in steps[:-1]:
        node = node[int(step)] if isinstance(node, list) else node[step]
    last = steps[-1]
    if value is ...:
        if isinstance(node, list):
            del node[int(last)]
        else:
            del node[last]
    elif isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return json.dumps(document).encode("utf-8")


BAD_SHAPES: list[tuple[str, bytes, str]] = [
    ("not json", b"not json at all", "not_json"),
    ("empty", b"", "not_json"),
    ("array", b"[]", "not_object"),
    ("string", b'"x"', "not_object"),
    ("lone surrogate", b'{"id": "\\ud800"}', "not_json"),
    ("not utf-8", b"\xff\xfe{}", "not_json"),
    ("repeated key", b'{"id": "a", "id": "b"}', "not_json"),
    ("id missing", _with("id", ...), "id_invalid"),
    ("id a number", _with("id", 5), "id_invalid"),
    ("id empty", _with("id", ""), "id_invalid"),
    ("id 257 long", _with("id", "x" * 257), "id_invalid"),
    ("model missing", _with("model", ...), "model_invalid"),
    ("model a number", _with("model", 5), "model_invalid"),
    ("model empty", _with("model", ""), "model_invalid"),
    ("model 257 long", _with("model", "m" * 257), "model_invalid"),
    ("choices missing", _with("choices", ...), "choices_invalid"),
    ("choices not a list", _with("choices", {}), "choices_invalid"),
    ("no choice", _with("choices", []), "choices_invalid"),
    ("choice not an object", _with("choices.0", "x"), "choices_invalid"),
    (
        "two choices",
        _with("choices", [_valid_body()["choices"][0], _valid_body()["choices"][0]]),
        "choices_invalid",
    ),
    ("message missing", _with("choices.0.message", ...), "message_invalid"),
    ("message a string", _with("choices.0.message", "x"), "message_invalid"),
    ("content null", _with("choices.0.message.content", None), "content_invalid"),
    ("content a number", _with("choices.0.message.content", 5), "content_invalid"),
    (
        "content in parts",
        _with("choices.0.message.content", [{"type": "text", "text": "x"}]),
        "content_invalid",
    ),
    ("finish_reason missing", _with("choices.0.finish_reason", ...), "finish_reason_invalid"),
    ("finish_reason null", _with("choices.0.finish_reason", None), "finish_reason_invalid"),
    ("finish_reason a number", _with("choices.0.finish_reason", 1), "finish_reason_invalid"),
    ("usage missing", _with("usage", ...), "usage_invalid"),
    ("usage a list", _with("usage", []), "usage_invalid"),
    ("prompt missing", _with("usage.prompt_tokens", ...), "usage_invalid"),
    ("prompt negative", _with("usage.prompt_tokens", -1), "usage_invalid"),
    ("prompt a float", _with("usage.prompt_tokens", 1.5), "usage_invalid"),
    ("prompt a bool", _with("usage.prompt_tokens", True), "usage_invalid"),
    ("prompt a string", _with("usage.prompt_tokens", "5"), "usage_invalid"),
    ("prompt beyond 2**53-1", _with("usage.prompt_tokens", 2**53), "usage_invalid"),
    ("completion missing", _with("usage.completion_tokens", ...), "usage_invalid"),
    ("completion negative", _with("usage.completion_tokens", -1), "usage_invalid"),
    ("completion a float", _with("usage.completion_tokens", 2.0), "usage_invalid"),
    ("completion a bool", _with("usage.completion_tokens", True), "usage_invalid"),
    ("cached a bool", _with("usage.prompt_tokens_details.cached_tokens", True), "usage_invalid"),
    (
        "reasoning a bool",
        _with("usage.completion_tokens_details.reasoning_tokens", True),
        "usage_invalid",
    ),
    (
        "cached above prompt",
        _with("usage.prompt_tokens_details.cached_tokens", 121),
        "usage_invalid",
    ),
    ("cached negative", _with("usage.prompt_tokens_details.cached_tokens", -1), "usage_invalid"),
    ("cached a float", _with("usage.prompt_tokens_details.cached_tokens", 1.5), "usage_invalid"),
    ("details a list", _with("usage.prompt_tokens_details", [1]), "usage_invalid"),
    (
        "reasoning negative",
        _with("usage.completion_tokens_details.reasoning_tokens", -1),
        "usage_invalid",
    ),
    (
        "reasoning a string",
        _with("usage.completion_tokens_details.reasoning_tokens", "5"),
        "usage_invalid",
    ),
    ("completion details a string", _with("usage.completion_tokens_details", "x"), "usage_invalid"),
    ("fingerprint a number", _with("system_fingerprint", 5), "fingerprint_invalid"),
]


def test_openai_compat_response_parse_rejects_wrong_shape_and_oversize(tmp_path: Path) -> None:
    """Rule 12: ``id``, ``model``, exactly one choice with a string content and a ``finish_reason``,
    integer ``usage`` counts; anything else is ``JudgeResponseInvalid`` with a machine reason that
    quotes nothing; a body over the cap is refused while it is read."""
    compat = jmod("openai_compat")
    judge = core("judge")
    assert compat.JUDGE_MAX_RESPONSE_BYTES == 65_536
    good = compat.parse_chat_response(
        openai_reply(cached=10, reasoning=5, fingerprint="fp_1").content, index=3, attempts=1
    )
    assert isinstance(good, compat.ChatReply)
    assert (good.id, good.model, good.finish_reason) == (
        "chatcmpl-req-1",
        "judge-model-1-echo",
        "stop",
    )
    assert (good.prompt_tokens, good.completion_tokens) == (120, 20)
    assert (good.cached_tokens, good.reasoning_tokens, good.system_fingerprint) == (10, 5, "fp_1")
    assert good.content == verdict_text()
    for label, body, reason in BAD_SHAPES:
        found = compat.parse_chat_response(body, index=3, attempts=2)
        assert isinstance(found, judge.JudgeResponseInvalid), label
        assert found.reason == reason, (label, found.reason)
        assert (found.index, found.attempts, found.status) == (3, 2, 200), label
        assert found.__cause__ is None
        assert found.__context__ is None
    # members the client does not know are ignored, and never kept
    extra = _valid_body()
    extra["provider"] = "upstream-1"
    extra["usage"]["cost"] = 0.0123457
    extra["choices"][0]["logprobs"] = None
    extra["service_tier"] = "default"
    parsed = compat.parse_chat_response(json.dumps(extra).encode(), index=0, attempts=1)
    assert isinstance(parsed, compat.ChatReply)
    assert "0.0123457" not in repr(parsed)
    # anthropic: details present and empty (documented "Always empty"), or null, mean not reported
    empties: list[Any] = [{}, None]
    for details in empties:
        doc = _valid_body()
        doc["usage"]["prompt_tokens_details"] = details
        parsed = compat.parse_chat_response(json.dumps(doc).encode(), index=0, attempts=1)
        assert isinstance(parsed, compat.ChatReply)
        assert parsed.cached_tokens is None
    bare = _valid_body()
    del bare["usage"]["prompt_tokens_details"], bare["usage"]["completion_tokens_details"]
    del bare["system_fingerprint"]
    parsed = compat.parse_chat_response(json.dumps(bare).encode(), index=0, attempts=1)
    assert (parsed.cached_tokens, parsed.reasoning_tokens, parsed.system_fingerprint) == (
        None,
        None,
        None,
    )

    # end to end: the failure comes back as the outcome's failure, nothing is retried, nothing is
    # recorded as usage, and the text of the reply reaches no form of the error and no log
    base = tmp_path
    tainted = _valid_body()
    tainted["choices"][0]["message"]["content"] = None
    tainted["extra"] = TEXT_CANARY
    tainted["model"] = TEXT_CANARY
    rig = JudgeRig(base / "a", [json_response(tainted)])
    outcome = rig.complete()
    assert outcome.text is None
    assert outcome.usage == ()
    assert isinstance(outcome.failure, judge.JudgeResponseInvalid)
    assert outcome.failure.reason == "content_invalid"
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []
    for form in (str(outcome.failure), repr(outcome.failure), repr(vars(outcome.failure))):
        assert TEXT_CANARY not in form
    assert TEXT_CANARY not in rig.log_text
    assert chain(outcome.failure) == [outcome.failure]

    # a body over the cap is refused while it is read, whatever it holds
    huge = openai_reply(content="x" * (compat.JUDGE_MAX_RESPONSE_BYTES + 10))
    rig = JudgeRig(base / "b", [huge])
    outcome = rig.complete()
    assert isinstance(outcome.failure, judge.JudgeResponseInvalid)
    assert (outcome.failure.reason, outcome.failure.status) == ("body_too_large", 200)
    assert outcome.usage == ()
    assert len(rig.transport.requests) == 1

    # a content over 4,096 bytes is the validator's to refuse (rule 7), not the client's
    long_text = "y" * 5000
    rig = JudgeRig(base / "c", [openai_reply(content=long_text)])
    outcome = rig.complete()
    assert outcome.failure is None
    assert outcome.text == long_text
    assert len(outcome.usage) == 1

    # a reply that is not JSON content-typed is refused before it is parsed
    rig = JudgeRig(
        base / "d", [httpx.Response(200, headers={"content-type": "text/html"}, content=b"{}")]
    )
    outcome = rig.complete()
    assert isinstance(outcome.failure, judge.JudgeResponseInvalid)
    assert outcome.failure.reason == "content_type"


def test_openai_compat_usage_maps_prompt_completion_cached_exactly(tmp_path: Path) -> None:
    """Rule 18 and ADR 0014 Decision 9: input tokens uncached, cached read tokens only when the
    reply reports them (absent stays absent, never 0), output tokens, one request; stage
    ``judge``, domain ``evaluation``, the model as requested; handed out before ``complete``
    returns."""
    base = tmp_path
    rig = JudgeRig(
        base / "a",
        [openai_reply(prompt=100, completion=20, cached=30, reasoning=7, fingerprint="fp_9")],
        profile="openrouter/1",
    )
    handed: list[Any] = []
    ctx = context()
    outcome = rig.complete(
        request(7, seed=11, provider_slug="provider-one"), on_usage=handed.append, ctx=ctx
    )
    assert outcome.failure is None
    assert len(outcome.usage) == 1
    assert handed == list(outcome.usage), "the sink got the record before the call returned"
    (record,) = outcome.usage
    quantities = {q.unit: q.count for q in record.quantities}
    assert quantities == {
        "input_tokens": 70,
        "cache_read_input_tokens": 30,
        "output_tokens": 20,
        "requests": 1,
    }, "the reasoning tokens are inside the completion tokens and are not counted twice"
    assert (record.stage, record.domain, record.source) == (
        "judge",
        "evaluation",
        "provider_reported",
    )
    assert (record.provider, record.model) == ("openrouter/1", "judge-model-1")
    assert record.run_id == ctx.run_id
    assert record.provider_call_ref.value == "chatcmpl-req-1"
    assert record.provider_call_ref.kind == "request_id"
    assert record.occurred_at is not None
    assert record.occurred_at.isoformat().startswith("2026-10-23T13:08:26")
    assert record.metadata["tracer.request_index"] == 7
    assert record.metadata["tracer.seed"] == 11
    assert record.metadata["tracer.seed_guarantee"] == "none"
    assert record.metadata["tracer.reasoning_tokens"] == 7
    assert record.metadata["tracer.system_fingerprint"] == "fp_9"

    # no cached count in the reply: no cache bucket at all (absent stays absent, never 0)
    for number, details in enumerate((None, {}, {"cached_tokens": None})):
        usage_extra: dict[str, Any] = {}
        if details is not None:
            usage_extra["prompt_tokens_details"] = details
        rig = JudgeRig(
            base / f"n{number}", [openai_reply(prompt=100, completion=20, usage_extra=usage_extra)]
        )
        (record,) = rig.complete().usage
        quantities = {q.unit: q.count for q in record.quantities}
        assert quantities == {"input_tokens": 100, "output_tokens": 20, "requests": 1}
        assert "tracer.reasoning_tokens" not in record.metadata
        assert "tracer.seed" not in record.metadata
    # a reported zero is a reported zero
    rig = JudgeRig(base / "z", [openai_reply(prompt=10, completion=0, cached=0)])
    (record,) = rig.complete().usage
    assert {q.unit: q.count for q in record.quantities} == {
        "input_tokens": 10,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "requests": 1,
    }
    # the largest safe integer is exact; one more is refused
    top = 2**53 - 1
    rig = JudgeRig(base / "top", [openai_reply(prompt=top, completion=top)])
    (record,) = rig.complete().usage
    assert {q.unit: q.count for q in record.quantities}["input_tokens"] == top
    # the engine is a usage-streaming engine: it hands each record out itself, once
    engine = rig.engine
    assert engine.streams_usage is True
    assert isinstance(engine, core("judge").UsageStreamingJudgeEngine)
    # a failed request has no usage record
    rig = JudgeRig(base / "f", [json_response(error_body(), status=400)])
    outcome = rig.complete()
    assert outcome.usage == ()
    assert outcome.failure is not None
    # one record per answered request, none for a retried attempt
    rig = JudgeRig(base / "r", [json_response(error_body(), status=503), openai_reply()])
    assert len(rig.complete().usage) == 1


def test_judge_cost_not_available_without_usd_catalog_and_credits_never_stored_as_cost(
    tmp_path: Path,
) -> None:
    """Rule 18 and Ambiguity 6: OpenRouter's ``usage.cost`` is "Cost in credits" and is never
    stored as a cost (it is stored nowhere); without a USD price catalog every cost record of a
    judge usage is ``not_available``, and nothing is converted or reported as zero."""
    from datetime import UTC, datetime

    cost = jmod("cost")
    credits = 0.0123457
    base = tmp_path
    rig = JudgeRig(
        base / "a",
        [
            openai_reply(
                usage_extra={
                    "cost": credits,
                    "cost_details": {"upstream_inference_cost": credits},
                    "is_byok": False,
                }
            )
        ],
        profile="openrouter/1",
    )
    outcome = rig.complete(request(provider_slug="provider-one"))
    assert outcome.failure is None
    (record,) = outcome.usage
    dumped = json.dumps(record.model_dump(mode="json", by_alias=True))
    assert "0.0123457" not in dumped
    assert "credits" not in dumped.lower()
    assert "cost" not in dumped.lower().replace("cost_", "")
    assert "0.0123457" not in rig.log_text
    assert "0.0123457" not in repr(outcome)
    created = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    computed = cost.compute_usage_cost([record], catalog=None, created_at=created)
    assert [r.status for r in computed.records] == ["not_available"]
    assert [r.na_reason for r in computed.records] == ["no_price_for_model"]
    assert computed.summary.model_dump(mode="json")  # a summary exists and holds no money
    shown = json.dumps(computed.summary.model_dump(mode="json", by_alias=True))
    assert "0.0123457" not in shown
    # no usage at all is also "not available", never zero
    none = cost.compute_usage_cost([], catalog=None, created_at=created)
    assert [r.status for r in none.records] == ["not_available"]
    assert [r.na_reason for r in none.records] == ["no_usage"]


def test_openai_compat_uses_the_one_http_policy_of_adr0024(tmp_path: Path) -> None:
    """The judge client uses the one policy of ADR 0024 rules 9 and 10 (the constants are the
    Sarvam client's, not copies) and the one kind of HTTP client: no redirect, no environment."""
    sarvam = importlib.import_module("tracer_evalproviders.sarvam.client")
    compat = jmod("openai_compat")
    assert compat.MAX_ATTEMPTS == sarvam.MAX_ATTEMPTS == 4
    assert tuple(compat.BACKOFF_SECONDS) == tuple(sarvam.BACKOFF_SECONDS) == (1, 2, 4)
    assert compat.RETRY_AFTER_MAX_SECONDS == sarvam.RETRY_AFTER_MAX_SECONDS == 30
    client = compat.build_judge_http_client(None)
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
    finally:
        client.close()
    rig = JudgeRig(tmp_path / "a", [openai_reply()])
    assert rig.complete().failure is None


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_openai_compat_retry_taxonomy_matches_adr0024(tmp_path: Path, scenario: str) -> None:
    """ADR 0024 rules 9 and 10 for the judge: the scenarios that the Sarvam TTS and chat adapters
    pass (retries, bounded attempts, Retry-After, the error classes, redirects never followed,
    size caps, what an error holds) are run against the judge client on the profile
    ``sarvam_v2/1`` (one named header, the Sarvam v2 URL) with the ``Judge*`` error family."""
    SCENARIOS[scenario](judge_kit, tmp_path)


def test_openai_compat_choice_level_error_is_typed_failure(tmp_path: Path) -> None:
    """OpenRouter can answer 200 with an ``error`` inside the choice: a typed failure
    (``JudgeResponseInvalid`` ``choice_error``), not retried, and its message reaches no form."""
    judge = core("judge")
    for number, (code, kept) in enumerate(
        [(502, None), ("server_error", "server_error"), ("Bad Code!", None), (None, None)]
    ):
        body = _valid_body()
        body["choices"][0]["error"] = {"code": code, "message": f"upstream said {MESSAGE_CANARY}"}
        body["choices"][0]["message"]["content"] = ""
        body["choices"][0]["finish_reason"] = "error"
        rig = JudgeRig(tmp_path / f"e{number}", [json_response(body)], profile="openrouter/1")
        outcome = rig.complete(request(provider_slug="provider-one"))
        assert outcome.text is None
        assert outcome.usage == ()
        failure = outcome.failure
        assert type(failure) is judge.JudgeResponseInvalid
        assert (failure.reason, failure.status, failure.attempts) == ("choice_error", 200, 1)
        assert failure.code == kept
        assert len(rig.transport.requests) == 1, "not retried"
        assert rig.sleeps.waits == [], "not retried"
        for form in (str(failure), repr(failure), repr(vars(failure))):
            assert MESSAGE_CANARY not in form
        assert MESSAGE_CANARY not in rig.log_text
        assert failure.__cause__ is None
        assert failure.__context__ is None
    # an error with a null message slot and no content is the same failure, not a content error
    body = _valid_body()
    body["choices"][0] = {
        "error": {"code": 500, "message": "m"},
        "message": None,
        "finish_reason": "error",
    }
    rig = JudgeRig(tmp_path / "null", [json_response(body)], profile="openrouter/1")
    failure = rig.complete(request(provider_slug="provider-one")).failure
    assert failure is not None
    assert failure.reason == "choice_error"
    # the same body from a profile that is not OpenRouter is read by the same shape rules
    rig = JudgeRig(tmp_path / "other", [json_response(body)], profile="openai/1")
    failure = rig.complete().failure
    assert failure is not None
    assert failure.reason == "choice_error"


def test_openai_compat_caps_count_requests_and_reported_tokens(tmp_path: Path) -> None:
    """Rule 17: a request beyond ``max_requests`` is not sent; the token cap is the reported usage
    plus the next ``max_tokens`` (so one prompt can cross it); neither cap has a default."""
    judge = core("judge")
    rig = JudgeRig(tmp_path / "a", [openai_reply(), openai_reply(), openai_reply()], max_requests=2)
    assert rig.complete(request(0)).failure is None
    assert rig.complete(request(1)).failure is None
    third = rig.complete(request(2))
    assert isinstance(third.failure, judge.JudgeCapReachedError)
    assert third.failure.reason == "judge_requests"
    assert third.usage == ()
    assert third.text is None
    assert len(rig.transport.requests) == 2, "nothing is sent beyond the cap"
    # tokens: 140 reported by the first answer; the next max_tokens (256) must fit under the cap
    rig = JudgeRig(
        tmp_path / "b", [openai_reply(prompt=120, completion=20)] * 3, max_tokens=140 + 256
    )
    assert rig.complete(request(0)).failure is None
    assert rig.complete(request(1)).failure is None, "140 + 256 is exactly the cap: allowed"
    over = rig.complete(request(2))
    assert isinstance(over.failure, judge.JudgeCapReachedError)
    assert over.failure.reason == "judge_tokens"
    assert len(rig.transport.requests) == 2
    # a first request whose own max_tokens exceeds the cap is refused before it is sent
    rig = JudgeRig(tmp_path / "c", [openai_reply()], max_tokens=255)
    refused = rig.complete(request(0))
    assert refused.failure is not None
    assert refused.failure.reason == "judge_tokens"
    assert rig.transport.requests == []
    # a retried attempt is not a second request; an unlimited engine counts without limit
    rig = JudgeRig(
        tmp_path / "d", [json_response(error_body(), status=503), openai_reply()], max_requests=1
    )
    assert rig.complete(request(0)).failure is None
    # bad caps are a programming error, not a quiet "unlimited"
    for bad in (-1, 0, True, 1.5, "3"):
        with pytest.raises(ValueError, match=r"."):
            JudgeRig(tmp_path / f"bad{bad!r}".replace("/", "_"), [], max_requests=bad)
        with pytest.raises(ValueError, match=r"."):
            JudgeRig(tmp_path / f"badt{bad!r}".replace("/", "_"), [], max_tokens=bad)


def test_openai_compat_optional_headers_go_to_the_profile_that_lists_them(tmp_path: Path) -> None:
    """The owner's attribution headers reach the request of the profile that lists them and are
    refused for another; they replace nothing the profile sets itself."""
    cases = {
        "anthropic_compat/1": (("anthropic-workspace-id", "ws_example"),),
        "openrouter/1": (
            ("HTTP-Referer", "https://example.org/app"),
            ("X-OpenRouter-Title", "Tracer"),
        ),
    }
    for number, (name, headers) in enumerate(cases.items()):
        rig = JudgeRig(tmp_path / f"h{number}", [openai_reply()], profile=name)
        settings: dict[str, Any] = (
            {"provider_slug": "provider-one"} if name == "openrouter/1" else {}
        )
        outcome = rig.complete(request(extra_headers=headers, **settings))
        assert outcome.failure is None, name
        sent = rig.transport.requests[0]
        for header, value in headers:
            assert sent.headers[header] == value, (name, header)
        assert sent.headers["authorization"] == f"Bearer {CANARY_KEY}"
        assert sent.headers["content-type"].split(";")[0] == "application/json"
        assert sent.headers.get_list("accept-encoding") == ["identity"]
    # a header the profile does not list is a refusal before anything is sent
    profiles = jmod("judgeprofiles")
    rig = JudgeRig(tmp_path / "refused", [openai_reply()], profile="openai/1")
    with pytest.raises(profiles.JudgeProfileError) as caught:
        rig.complete(request(extra_headers=(("anthropic-workspace-id", "ws"),)))
    assert caught.value.code == "header_not_supported"
    assert rig.transport.requests == []
