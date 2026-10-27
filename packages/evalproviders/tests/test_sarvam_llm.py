# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-09: the Sarvam chat v2 adapter (contract docs/phase4/api-surface-batch-b.md section 3; ADR
0024 amendments A1, A3 and A7, invariant IA1; ADR 0028 rule 5; ADR 0026 rule 12).

``POST https://api.sarvam.ai/v2/chat/completions`` with an explicit model, a required
``max_tokens``, the messages, the seed and the reasoning effort when given, and nothing else (no
tools, no streaming). A 200 reply holds exactly one choice with a string content, a ``usage``
object with integer ``prompt_tokens`` and ``completion_tokens`` and a string ``id``.

UNVERIFIED (ADR 0024 sources section; marked where used): that the key header is
``api-subscription-key`` for chat v2 (assumed the same as STT and TTS), that the error body has the
STT shape, and where a cached-token count would be reported (assumed ``usage.prompt_tokens_details.
cached_tokens``, the OpenAI place). Each is one line of the adapter to change.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_matrix import SCENARIOS  # type: ignore[import-not-found,unused-ignore]
from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MR_LINE,
    SECOND_MR_LINE,
    LlmRig,
    chat_mod,
    chat_reply,
    core_llm,
    llm_context,
    llm_kit,
    llm_request,
    models_mod,
)
from evalproviders_support import json_response  # type: ignore[import-not-found,unused-ignore]

BODY_FIELDS = {"model", "messages", "max_tokens"}
MAX_SAFE = 2**53 - 1


def _body(request: Any) -> dict[str, Any]:
    document = json.loads(request.content.decode("utf-8"))
    assert isinstance(document, dict)
    return document


# --------------------------------------------------------------------- the request


@pytest.mark.parametrize("model", ["sarvam-105b", "glm5.3", "gemma4", "deepseekv4.1-flash"])
def test_sarvam_llm_model_and_max_tokens_required(tmp_path: Path, model: str) -> None:
    """ADR 0024 A3 / IA1, ADR 0028 rule 5: the request sends the model it was given and
    ``max_tokens``, the messages in order, and no other member (no ``tools``, no streaming, no
    sampling option): nothing the provider would default is asked for by omission or invented."""
    rig = LlmRig(tmp_path, [chat_reply()])
    request = llm_request(
        2,
        model=model,
        max_tokens=64,
        messages=[("system", "be brief"), ("user", MR_LINE), ("assistant", "ok"), ("user", "x\ny")],
    )
    rig.call(request)
    (sent,) = rig.transport.requests
    assert sent.method == "POST"
    assert str(sent.url) == "https://api.sarvam.ai/v2/chat/completions"
    body = _body(sent)
    assert set(body) == BODY_FIELDS
    assert body["model"] == model
    assert type(body["max_tokens"]) is int
    assert body["max_tokens"] == 64
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": MR_LINE},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "x\ny"},
    ]
    for member in ("tools", "tool_choice", "stream", "temperature", "top_p", "n", "stop"):
        assert member not in body


@pytest.mark.parametrize("max_tokens", [1, 64, 4096, MAX_SAFE])
def test_sarvam_llm_model_and_max_tokens_required_the_limit_goes_through_exactly(
    tmp_path: Path, max_tokens: int
) -> None:
    """ADR 0028 rule 5: ``max_tokens`` is sent exactly as given, up to 2**53 - 1, never clamped."""
    rig = LlmRig(tmp_path, [chat_reply()])
    rig.call(llm_request(0, max_tokens=max_tokens))
    assert _body(rig.transport.requests[0])["max_tokens"] == max_tokens


def test_sarvam_llm_model_and_max_tokens_required_the_engine_defaults_nothing() -> None:
    """No default model, limit, seed or effort in code (ADR 0026 rule 12): none is a parameter of
    the engine or of its factory; the request record carries them."""
    names = {
        "model",
        "max_tokens",
        "seed",
        "reasoning_effort",
        "effort",
        "messages",
        "temperature",
        "tools",
        "stream",
    }
    chat = chat_mod()
    for target in (chat.SarvamLlm.__init__, chat.create_sarvam_llm):
        assert not set(inspect.signature(target).parameters) & names, target
    parameters = inspect.signature(chat.SarvamLlm.complete).parameters
    assert list(parameters) == ["self", "request", "context", "on_usage"]
    assert parameters["on_usage"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["on_usage"].default is None


def test_sarvam_llm_model_and_max_tokens_required_the_engine_is_an_llm_engine(
    tmp_path: Path,
) -> None:
    """ADR 0026 rule 4: the adapter satisfies both runtime protocols of the boundary."""
    rig = LlmRig(tmp_path, [])
    assert isinstance(rig.engine, core_llm().LlmEngine)
    assert isinstance(rig.engine, core_llm().UsageStreamingLlmEngine)
    assert rig.engine.streams_usage is True
    assert rig.engine.provider == "sarvam"


def test_sarvam_llm_model_and_max_tokens_required_a_request_is_a_request_record(
    tmp_path: Path,
) -> None:
    """The engine takes the typed records and refuses loose dictionaries before any request."""
    rig = LlmRig(tmp_path, [])
    with pytest.raises(TypeError):
        rig.engine.complete({"model": "m", "max_tokens": 5}, llm_context())
    with pytest.raises(TypeError):
        rig.engine.complete(llm_request(), None)
    assert rig.transport.requests == []


@pytest.mark.parametrize(
    ("seed", "effort"),
    [(None, None), (0, None), (7, None), (MAX_SAFE, None), (None, "low"), (123, "high")],
)
def test_sarvam_llm_seed_and_effort_are_sent_when_given_and_only_then(
    tmp_path: Path, seed: int | None, effort: str | None
) -> None:
    """ADR 0024 A3: the seed (0 included) and the reasoning effort are sent when given and absent
    otherwise."""
    rig = LlmRig(tmp_path, [chat_reply()])
    rig.call(llm_request(0, seed=seed, effort=effort))
    body = _body(rig.transport.requests[0])
    assert ("seed" in body) is (seed is not None), "seed 0 is sent: 0 is a seed"
    if seed is not None:
        assert type(body["seed"]) is int
        assert body["seed"] == seed
    assert ("reasoning_effort" in body) is (effort is not None)
    if effort is not None:
        assert body["reasoning_effort"] == effort
    assert set(body) - {"seed", "reasoning_effort"} == BODY_FIELDS


def test_sarvam_llm_the_key_is_in_one_header_and_nowhere_else(tmp_path: Path) -> None:
    """UNVERIFIED: the header name for chat v2 (assumed ``api-subscription-key``, like STT)."""
    rig = LlmRig(tmp_path, [chat_reply()])
    rig.call()
    (request,) = rig.transport.requests
    assert request.headers.get_list("api-subscription-key") == [CANARY_KEY]
    assert request.headers.get_list("accept-encoding") == ["identity"]
    assert "authorization" not in request.headers
    assert request.headers["content-type"].split(";")[0] == "application/json"
    assert CANARY_KEY not in str(request.url)
    assert CANARY_KEY.encode() not in request.content


def test_sarvam_llm_base_url_not_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0024 rule 3 for chat v2: the host and the path are constants no variable or argument
    moves."""
    for name in (
        "SARVAM_BASE_URL",
        "SARVAM_API_URL",
        "SARVAM_CHAT_URL",
        "SARVAM_ENDPOINT",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "BASE_URL",
    ):
        monkeypatch.setenv(name, "http://evil.example:8080")
    rig = LlmRig(tmp_path, [chat_reply()])
    rig.call()
    (request,) = rig.transport.requests
    assert str(request.url) == "https://api.sarvam.ai/v2/chat/completions"
    assert request.url.query == b""
    models = models_mod()
    assert models.CHAT_PATH == "/v2/chat/completions"
    assert models.CHAT_URL == models.API_BASE_URL + models.CHAT_PATH
    forbidden = {"url", "base_url", "endpoint", "host", "port", "verify", "proxy", "proxies"}
    forbidden |= {"follow_redirects", "http2", "trust_env", "headers", "path", "scheme"}
    chat = chat_mod()
    for target in (chat.SarvamLlm.__init__, chat.SarvamLlm.complete, chat.create_sarvam_llm):
        assert not set(inspect.signature(target).parameters) & forbidden, target


# -------------------------------------------------------------------------- the reply


def test_sarvam_llm_reply_text_is_returned_verbatim(tmp_path: Path) -> None:
    """The adapter does not judge the reply (the output validator is ADR 0028 rule 6): no strip,
    no normalisation, no truncation; an empty reply is a text."""
    for number, text in enumerate(
        [
            SECOND_MR_LINE,
            "  padded \n",
            "a\nb",
            "END_CALL",
            "",
            "\u0915\u094d\u200d\u0937",
            "e\u0301",
        ]
    ):
        rig = LlmRig(tmp_path / f"t{number}", [chat_reply(text)])
        outcome = rig.call()
        assert outcome.failure is None
        assert outcome.text == text


def test_sarvam_llm_reply_members_the_adapter_does_not_know_are_ignored(tmp_path: Path) -> None:
    """ADR 0024 rule 8: unknown members of a reply are kept out of every record."""
    extra: dict[str, Any] = {
        "system_fingerprint": "fp",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "logprobs": None,
                "message": {
                    "role": "assistant",
                    "content": MR_LINE,
                    "reasoning_content": "private thoughts",
                    "tool_calls": None,
                },
            }
        ],
    }
    rig = LlmRig(tmp_path, [chat_reply("ignored", extra=extra)])
    outcome = rig.call()
    assert outcome.failure is None
    assert outcome.text == MR_LINE
    dumped = repr(outcome) + "".join(u.model_dump_json() for u in outcome.usage)
    assert "private thoughts" not in dumped
    assert "system_fingerprint" not in dumped


# ----------------------------------------------------------------------------- usage


def test_sarvam_llm_usage_maps_tokens_exactly(tmp_path: Path) -> None:
    """ADR 0024 A7: one record per answered request: ``evaluation``, ``sim_caller_llm``, the model
    as requested (the reply may echo another name), ``provider_reported``, the reply id as
    ``provider_call_ref``, ``occurred_at`` from the reply's ``Date`` header, ``input_tokens`` the
    reported prompt tokens, ``output_tokens`` the reported completion tokens and ``requests`` 1;
    the record is stamped with the context's ``created_at`` and run id."""
    context = llm_context()
    rig = LlmRig(tmp_path, [chat_reply(reply_id="chat-77", prompt=123, completion=45)])
    outcome = rig.call(llm_request(9, model="gemma4"), context=context)
    assert outcome.failure is None
    (record,) = outcome.usage
    assert record.domain == "evaluation"
    assert record.stage == "sim_caller_llm"
    assert record.provider == "sarvam"
    assert record.model == "gemma4", "as requested; the reply says another name"
    assert record.source == "provider_reported"
    assert (
        record.provider_call_ref.system,
        record.provider_call_ref.kind,
        record.provider_call_ref.value,
    ) == ("sarvam", "request_id", "chat-77")
    assert record.occurred_at == datetime(2026, 10, 23, 13, 8, 26, tzinfo=UTC)
    assert record.created_at == context.created_at
    assert record.run_id == context.run_id
    assert record.raw is None
    counts = {q.unit: q.count for q in record.quantities}
    assert counts == {"input_tokens": 123, "output_tokens": 45, "requests": 1}
    assert all(type(c) is int for c in counts.values())
    assert record.metadata["tracer.request_index"] == 9


def test_sarvam_llm_usage_maps_tokens_exactly_cached_tokens_are_split_as_adr_0014(
    tmp_path: Path,
) -> None:
    """ADR 0014 Decision 9 / A7: ``input_tokens`` is the prompt tokens minus the reported cached
    tokens and ``cache_read_input_tokens`` is the cached count, only when the reply reports one.
    UNVERIFIED: where chat v2 would report it (assumed ``usage.prompt_tokens_details.
    cached_tokens``)."""
    for number, (prompt, cached) in enumerate([(100, 30), (100, 100), (7, 1)]):
        rig = LlmRig(
            tmp_path / f"c{number}", [chat_reply(prompt=prompt, completion=2, cached=cached)]
        )
        (record,) = rig.call().usage
        counts = {q.unit: q.count for q in record.quantities}
        assert counts == {
            "input_tokens": prompt - cached,
            "cache_read_input_tokens": cached,
            "output_tokens": 2,
            "requests": 1,
        }
    rig = LlmRig(tmp_path / "none", [chat_reply(prompt=100, completion=2)])
    (record,) = rig.call().usage
    assert "cache_read_input_tokens" not in {q.unit for q in record.quantities}


def test_sarvam_llm_usage_maps_tokens_exactly_zero_counts_and_big_counts(tmp_path: Path) -> None:
    """Zero and the largest safe integer are exact counts (ADR 0014 Decision 15a: no float)."""
    rig = LlmRig(
        tmp_path,
        [chat_reply(prompt=0, completion=0), chat_reply(prompt=MAX_SAFE, completion=MAX_SAFE)],
    )
    (zero,) = rig.call(llm_request(0)).usage
    assert {q.unit: q.count for q in zero.quantities} == {
        "input_tokens": 0,
        "output_tokens": 0,
        "requests": 1,
    }
    (big,) = rig.call(llm_request(1)).usage
    assert {q.unit: q.count for q in big.quantities}["input_tokens"] == MAX_SAFE


def test_sarvam_llm_usage_has_no_occurred_at_without_a_date_header(tmp_path: Path) -> None:
    """ADR 0014 Decision 8: never the local clock for ``occurred_at``."""
    for number, date in enumerate([None, "yesterday", "2026-10-23T13:08:26Z"]):
        rig = LlmRig(tmp_path / f"d{number}", [chat_reply(date=date)])
        (record,) = rig.call().usage
        assert record.occurred_at is None


def test_sarvam_llm_usage_is_one_record_per_answered_request_and_none_for_a_failure(
    tmp_path: Path,
) -> None:
    """ADR 0024 rule 9 / A7: a retried attempt and a failed request make no usage record."""
    from evalproviders_phase4_matrix import status  # type: ignore[import-not-found,unused-ignore]

    rig = LlmRig(tmp_path, [status(503), chat_reply(reply_id="a"), status(403)])
    first = rig.call(llm_request(0))
    assert [r.provider_call_ref.value for r in first.usage] == ["a"], (
        "the retried attempt is no record"
    )
    second = rig.call(llm_request(1))
    assert second.failure is not None
    assert second.usage == ()


# ------------------------------------------------------------------------------ seed


def test_sarvam_llm_seed_recorded_never_claimed_deterministic(tmp_path: Path) -> None:
    """ADR 0024 A3, ADR 0028 rule 5: the seed is sent and recorded (``tracer.seed``), the record
    says in a machine key that it guarantees nothing (``tracer.seed_guarantee`` = ``none``) and
    no record or constant of the adapter claims repeatability. A seed of 0 is recorded."""
    for number, seed in enumerate([0, 1, 424242, MAX_SAFE]):
        rig = LlmRig(tmp_path / f"s{number}", [chat_reply()])
        (record,) = rig.call(llm_request(0, seed=seed, effort="low")).usage
        assert record.metadata["tracer.seed"] == seed
        assert type(record.metadata["tracer.seed"]) is int
        assert record.metadata["tracer.seed_guarantee"] == "none"
        assert record.metadata["tracer.reasoning_effort"] == "low"
        text = record.model_dump_json().lower()
        for word in ("determin", "reproduc", "repeatab", "identical"):
            assert word not in text, word
    plain = LlmRig(tmp_path / "plain", [chat_reply()])
    (record,) = plain.call(llm_request(0)).usage
    for key in ("tracer.seed", "tracer.seed_guarantee", "tracer.reasoning_effort"):
        assert key not in record.metadata, "nothing is recorded that was not sent"


def test_sarvam_llm_seed_recorded_never_claimed_deterministic_the_statement() -> None:
    """Every output says that replies may differ between requests (ADR 0028 rule 5): the CLI
    prints this one sentence of the adapter, and it makes no claim of repeatability."""
    statement = chat_mod().SEED_STATEMENT
    assert statement == "replies may differ between requests, even with the same seed"
    assert statement.isascii()


def test_sarvam_llm_same_seed_twice_is_two_requests_and_two_records(tmp_path: Path) -> None:
    """A seed does not merge two requests: each is sent and recorded (no determinism claim)."""
    rig = LlmRig(
        tmp_path, [chat_reply(SECOND_MR_LINE, reply_id="a"), chat_reply("other", reply_id="b")]
    )
    first = rig.call(llm_request(0, seed=5))
    second = rig.call(llm_request(1, seed=5))
    assert (first.text, second.text) == (SECOND_MR_LINE, "other")
    assert len(rig.transport.requests) == 2


# ---------------------------------------------------------------------- wrong replies


def _good() -> dict[str, Any]:
    decoded = json.loads(chat_reply().content)
    assert isinstance(decoded, dict)
    return decoded


def _mutated(change: Any) -> bytes:
    document = _good()
    change(document)
    return json.dumps(document).encode()


def _set(path: tuple[Any, ...], value: Any) -> Any:
    def change(document: dict[str, Any]) -> None:
        node: Any = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return change


def _drop(path: tuple[Any, ...]) -> Any:
    def change(document: dict[str, Any]) -> None:
        node: Any = document
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return change


BAD_SHAPES: list[tuple[str, bytes, str]] = [
    ("not json", b"<html>", "not_json"),
    ("empty body", b"", "not_json"),
    ("an array", b"[]", "not_object"),
    ("a string", b'"x"', "not_object"),
    ("null", b"null", "not_object"),
    ("duplicate member", b'{"id":"a","id":"b","choices":[],"usage":{}}', "not_json"),
    ("NaN", b'{"id":"a","x":NaN}', "not_json"),
    ("id missing", _mutated(_drop(("id",))), "id_invalid"),
    ("id null", _mutated(_set(("id",), None)), "id_invalid"),
    ("id a number", _mutated(_set(("id",), 5)), "id_invalid"),
    ("id empty", _mutated(_set(("id",), "")), "id_invalid"),
    ("id over 256 characters", _mutated(_set(("id",), "x" * 257)), "id_invalid"),
    ("choices missing", _mutated(_drop(("choices",))), "choices_invalid"),
    ("choices null", _mutated(_set(("choices",), None)), "choices_invalid"),
    ("choices a string", _mutated(_set(("choices",), "x")), "choices_invalid"),
    ("choices an object", _mutated(_set(("choices",), {"0": {}})), "choices_invalid"),
    ("choices empty", _mutated(_set(("choices",), [])), "choices_invalid"),
    (
        "choices two",
        _mutated(lambda d: d["choices"].append(d["choices"][0])),
        "choices_invalid",
    ),
    ("choice a number", _mutated(_set(("choices",), [5])), "message_invalid"),
    ("choice without message", _mutated(_drop(("choices", 0, "message"))), "message_invalid"),
    ("message a string", _mutated(_set(("choices", 0, "message"), "x")), "message_invalid"),
    ("message null", _mutated(_set(("choices", 0, "message"), None)), "message_invalid"),
    (
        "content missing",
        _mutated(_drop(("choices", 0, "message", "content"))),
        "content_invalid",
    ),
    (
        "content null",
        _mutated(_set(("choices", 0, "message", "content"), None)),
        "content_invalid",
    ),
    (
        "content a number",
        _mutated(_set(("choices", 0, "message", "content"), 5)),
        "content_invalid",
    ),
    (
        "content a list of parts",
        _mutated(_set(("choices", 0, "message", "content"), [{"type": "text", "text": "x"}])),
        "content_invalid",
    ),
    ("usage missing", _mutated(_drop(("usage",))), "usage_invalid"),
    ("usage null", _mutated(_set(("usage",), None)), "usage_invalid"),
    ("usage a list", _mutated(_set(("usage",), [1, 2])), "usage_invalid"),
    ("prompt tokens missing", _mutated(_drop(("usage", "prompt_tokens"))), "usage_invalid"),
    (
        "completion tokens missing",
        _mutated(_drop(("usage", "completion_tokens"))),
        "usage_invalid",
    ),
    ("prompt tokens a float", _mutated(_set(("usage", "prompt_tokens"), 5.0)), "usage_invalid"),
    ("prompt tokens a string", _mutated(_set(("usage", "prompt_tokens"), "5")), "usage_invalid"),
    ("prompt tokens true", _mutated(_set(("usage", "prompt_tokens"), True)), "usage_invalid"),
    ("prompt tokens null", _mutated(_set(("usage", "prompt_tokens"), None)), "usage_invalid"),
    ("prompt tokens negative", _mutated(_set(("usage", "prompt_tokens"), -1)), "usage_invalid"),
    (
        "prompt tokens above 2**53-1",
        _mutated(_set(("usage", "prompt_tokens"), 2**53)),
        "usage_invalid",
    ),
    (
        "completion tokens a float",
        _mutated(_set(("usage", "completion_tokens"), 1.5)),
        "usage_invalid",
    ),
    (
        "completion tokens false",
        _mutated(_set(("usage", "completion_tokens"), False)),
        "usage_invalid",
    ),
    (
        "completion tokens negative",
        _mutated(_set(("usage", "completion_tokens"), -3)),
        "usage_invalid",
    ),
    (
        "cached tokens above the prompt",
        _mutated(lambda d: d["usage"].update(prompt_tokens_details={"cached_tokens": 12})),
        "usage_invalid",
    ),
    (
        "cached tokens negative",
        _mutated(lambda d: d["usage"].update(prompt_tokens_details={"cached_tokens": -1})),
        "usage_invalid",
    ),
    (
        "cached tokens a float",
        _mutated(lambda d: d["usage"].update(prompt_tokens_details={"cached_tokens": 1.0})),
        "usage_invalid",
    ),
    (
        "cached tokens a string",
        _mutated(lambda d: d["usage"].update(prompt_tokens_details={"cached_tokens": "1"})),
        "usage_invalid",
    ),
]


@pytest.mark.parametrize(("label", "body", "reason"), BAD_SHAPES, ids=[b[0] for b in BAD_SHAPES])
def test_sarvam_llm_response_rejects_wrong_shape_and_oversize(
    tmp_path: Path, label: str, body: bytes, reason: str
) -> None:
    """ADR 0024 A3: exactly one choice with a string content, a ``usage`` of integer
    ``prompt_tokens`` and ``completion_tokens``, and a string ``id``; anything else is
    ``LlmResponseInvalid`` with a short reason key, is not retried, makes no usage record, and
    keeps neither the body nor any text of it."""
    rig = LlmRig(tmp_path, [json_response(body)])
    outcome = rig.call(llm_request(3))
    failure = outcome.failure
    assert type(failure) is core_llm().LlmResponseInvalid, label
    assert outcome.text is None
    assert outcome.usage == ()
    assert failure.reason == reason
    assert (failure.status, failure.index, failure.attempts) == (200, 3, 1)
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []
    assert str(failure).count("{") == 0
    assert [x["event"] for x in rig.log_lines] == ["before", "after"]


def test_sarvam_llm_response_rejects_wrong_shape_and_oversize_the_reply_text_cap(
    tmp_path: Path,
) -> None:
    """The reply text is capped in UTF-8 bytes: exactly 65,536 are read, one more is
    ``reply_too_large`` (a line is at most 2,500 characters, ADR 0028 rule 10; the cap is a
    technical bound)."""
    assert models_mod().LLM_MAX_REPLY_BYTES == 65_536
    fits = "\u0905" * 21845 + "a"
    assert len(fits.encode("utf-8")) == 65_536
    ok = LlmRig(tmp_path / "ok", [chat_reply(fits)])
    assert ok.call().text == fits
    over = LlmRig(tmp_path / "over", [chat_reply(fits + "a")])
    failure = over.call().failure
    assert type(failure) is core_llm().LlmResponseInvalid
    assert failure.reason == "reply_too_large"
    assert len(over.transport.requests) == 1


def test_sarvam_llm_response_rejects_wrong_shape_and_oversize_the_body_cap(tmp_path: Path) -> None:
    """ADR 0024 rule 8: the JSON body cap of 1,048,576 bytes is exact for chat v2."""
    cap = models_mod().MAX_RESPONSE_BYTES
    shell = chat_reply().content

    def padded(total: int) -> bytes:
        return bytes(shell + b" " * (total - len(shell)))

    exact = LlmRig(tmp_path / "exact", [json_response(padded(cap))])
    assert exact.call().failure is None, "a body of exactly the cap is read"
    over = LlmRig(tmp_path / "over", [json_response(padded(cap + 1))])
    failure = over.call().failure
    assert type(failure) is core_llm().LlmResponseInvalid
    assert failure.reason == "body_too_large"


def test_sarvam_llm_the_largest_safe_integer_count_is_exact(tmp_path: Path) -> None:
    """ADR 0014 Decision 15a: no float on the cost path; 2**53 - 1 is read exactly."""
    body = chat_reply(prompt=1, completion=1).content.decode()
    big = body.replace('"prompt_tokens": 1,', '"prompt_tokens": 9007199254740991,')
    assert "9007199254740991" in big
    rig = LlmRig(tmp_path, [json_response(big.encode())])
    (record,) = rig.call().usage
    assert {q.unit: q.count for q in record.quantities}["input_tokens"] == MAX_SAFE


# ------------------------------------------------------------- the shared taxonomy


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_sarvam_llm_retry_taxonomy_matches_stt_rules(tmp_path: Path, scenario: str) -> None:
    """ADR 0024 A1: the LLM adapter follows rules 8 to 10 as the STT adapter does."""
    SCENARIOS[scenario](llm_kit, tmp_path)


def test_sarvam_llm_an_id_of_256_characters_is_accepted(tmp_path: Path) -> None:
    """A hostile reply cannot carry a huge string into a record: the id is at most 256 characters
    (contract 3.3)."""
    rig = LlmRig(tmp_path, [chat_reply(reply_id="x" * 256)])
    outcome = rig.call()
    assert outcome.failure is None
    assert outcome.usage[0].provider_call_ref.value == "x" * 256


def test_sarvam_llm_the_request_id_falls_back_to_the_header_for_the_log(tmp_path: Path) -> None:
    """The same rule as the TTS adapter and the STT adapter (ADR 0024 rule 15)."""
    rig = LlmRig(
        tmp_path,
        [
            chat_reply(reply_id="has space", headers={"x-request-id": "hdr-ok-1"}),
            chat_reply(reply_id="body-id-2", headers={"x-request-id": "hdr-ok-2"}),
            chat_reply(reply_id="has space", headers={"x-request-id": "not plain"}),
        ],
    )
    for index in range(3):
        assert rig.call(llm_request(index)).failure is None
    afters = [x["request_id"] for x in rig.log_lines if x["event"] == "after"]
    assert afters == ["hdr-ok-1", "body-id-2", None]
