# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J13: the provider profiles (ADR 0030 rule 13, ADR 0024 amendment A11; contract
docs/phase4/api-surface-batch-j1.md section 10).

"OpenAI-compatible" is not one contract: the judge speaks to a provider only through one profile
of a closed, named and versioned set, and a profile fixes what is sent, omitted or required. The
table below is the SCOPE Q-J2 and ADR 0030 profile table, restated; a profile sends no field it
does not list; an unverified profile is refused for a real run (``docs/security/P4-J-providers.md``
holds the status). Request bodies are built offline.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from evalproviders_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    PROFILE_URLS,
    JudgeRig,
    core,
    jmod,
    openai_reply,
    request,
)

BASE = ("model", "messages", "max_tokens", "stream", "temperature")
#: name -> (token field, auth, header, the members it may send, in order, optional headers)
TABLE: dict[str, tuple[str, str, str | None, tuple[str, ...], tuple[str, ...]]] = {
    "anthropic_compat/1": ("max_tokens", "bearer", None, BASE, ("anthropic-workspace-id",)),
    "gemini_compat/1": ("max_tokens", "bearer", None, BASE, ()),
    "xai/1": ("max_tokens", "bearer", None, (*BASE, "response_format"), ()),
    "openrouter/1": (
        "max_tokens",
        "bearer",
        None,
        (*BASE, "seed", "response_format", "provider"),
        ("HTTP-Referer", "X-OpenRouter-Title"),
    ),
    "sarvam_v2/1": (
        "max_tokens",
        "header",
        "api-subscription-key",
        (*BASE, "response_format", "reasoning_effort"),
        (),
    ),
    "openai/1": (
        "max_completion_tokens",
        "bearer",
        None,
        ("model", "messages", "max_completion_tokens", "stream", "temperature", "seed"),
        (),
    ),
    "local_loopback/1": ("max_tokens", "none", None, BASE, ()),
}
#: what a complete configuration of each profile looks like
FULL: dict[str, dict[str, Any]] = {
    "anthropic_compat/1": {"temperature": 1.0},
    "gemini_compat/1": {"temperature": 0.5},
    "xai/1": {"temperature": 0.5},
    "openrouter/1": {"temperature": 0.5, "seed": 7, "provider_slug": "provider-one"},
    "sarvam_v2/1": {"temperature": 0.5, "reasoning_effort": "low"},
    "openai/1": {"temperature": 0.5, "seed": 7},
    "local_loopback/1": {"temperature": 0.5},
}
NAMES = tuple(TABLE)
RESPONSE_FORMAT_PROFILES = ("xai/1", "openrouter/1", "sarvam_v2/1")


def refusal(call: Any) -> str:
    profiles = jmod("judgeprofiles")
    with pytest.raises(profiles.JudgeProfileError) as caught:
        call()
    error = caught.value
    assert str(error) == error.code, "the message is the stable code and nothing else"
    assert error.__cause__ is None
    return str(error.code)


def body_of(name: str, **changes: Any) -> dict[str, Any]:
    profiles = jmod("judgeprofiles")
    settings = {**FULL[name], **changes}
    settings = {k: v for k, v in settings.items() if v is not ...}
    built: dict[str, Any] = profiles.build_request_body(
        profiles.get_profile(name), request(**settings)
    )
    return built


def test_profile_table_is_the_adr_table() -> None:
    """The closed set, its order, the token field, the auth scheme and the members each profile may
    send (the table of ADR 0030 and SCOPE Q-J2); the shipped endpoints are the documented ones."""
    profiles = jmod("judgeprofiles")
    assert tuple(profiles.PROFILE_NAMES) == NAMES
    assert len(set(NAMES)) == 7
    for name, (token, auth, header, sends, optional) in TABLE.items():
        profile = profiles.get_profile(name)
        assert profile.name == name
        assert profile.token_field == token
        assert (profile.auth, profile.auth_header) == (auth, header)
        assert tuple(profile.sends) == sends
        assert tuple(profile.optional_headers) == optional
        assert profile.seed is ("seed" in sends)
        assert profile.response_format is ("response_format" in sends)
        assert profile.provider_object is (name == "openrouter/1")
        assert profile.reasoning_effort == ("required" if name == "sarvam_v2/1" else "never")
        assert profile.endpoint == PROFILE_URLS[name] or name == "local_loopback/1"
    assert profiles.get_profile("local_loopback/1").endpoint is None
    # a profile is a frozen value
    with pytest.raises(Exception, match=r"."):
        profiles.get_profile("openai/1").token_field = "max_tokens"  # noqa: S105


def test_profile_anthropic_compat_never_sends_response_format_or_seed() -> None:
    """FJ1: the Anthropic layer ignores ``response_format`` and ``seed`` (and ``reasoning_effort``);
    Tracer's own validator decides, so none is sent, and a configured one is refused, never dropped
    quietly; ``temperature`` is not sent by default (a value below 1 returns 400 on 4.7 and
    later)."""
    profiles = jmod("judgeprofiles")
    profile = profiles.get_profile("anthropic_compat/1")
    assert profile.response_format is False
    assert profile.seed is False
    assert profile.reasoning_effort == "never"
    assert profile.provider_object is False
    body = body_of("anthropic_compat/1", temperature=None)
    assert list(body) == ["model", "messages", "max_tokens", "stream"]
    for member in ("response_format", "seed", "reasoning_effort", "provider", "temperature"):
        assert member not in body
    assert body["stream"] is False
    # a configured seed or effort is a refusal with a code, not a silent drop
    assert refusal(lambda: body_of("anthropic_compat/1", seed=7)) == "seed_not_supported"
    assert refusal(lambda: body_of("anthropic_compat/1", reasoning_effort="low")) == (
        "reasoning_effort_not_supported"
    )
    assert refusal(lambda: body_of("anthropic_compat/1", provider_slug="s")) == (
        "provider_slug_not_supported"
    )
    # a configured temperature is passed as given (the owner's responsibility on 4.7 and later)
    assert body_of("anthropic_compat/1", temperature=1.0)["temperature"] == 1.0
    # prompt-only JSON: the schema is described in the system message, not in a parameter
    assert "verdict" in body["messages"][0]["content"]
    # the optional workspace header is allowed here and nowhere else
    headers = profiles.request_headers(
        profile, CANARY_KEY, (("anthropic-workspace-id", "ws_example"),)
    )
    assert headers["Authorization"] == f"Bearer {CANARY_KEY}"
    assert headers["anthropic-workspace-id"] == "ws_example"
    assert "api-subscription-key" not in headers
    assert refusal(
        lambda: profiles.request_headers(
            profiles.get_profile("openai/1"), CANARY_KEY, (("anthropic-workspace-id", "ws"),)
        )
    ) == ("header_not_supported")


@pytest.mark.parametrize("name", NAMES)
def test_profile_temperature_not_sent_unless_configured(name: str) -> None:
    """Rule 12: ``temperature`` only when the configuration sets it (zero included), as given."""
    profiles = jmod("judgeprofiles")
    profile = profiles.get_profile(name)
    unset = {k: v for k, v in FULL[name].items() if k != "temperature"}
    body = profiles.build_request_body(profile, request(**unset))
    assert "temperature" not in body, name
    assert "temperature" not in profiles.params_as_sent(profile, request(**unset))
    for value in (0, 0.0, 0.7, 1, 2):
        body = profiles.build_request_body(profile, request(**{**unset, "temperature": value}))
        assert body["temperature"] == value, (name, value)
        assert type(body["temperature"]) is type(value), (name, value)
        assert (
            profiles.params_as_sent(profile, request(**{**unset, "temperature": value}))[
                "temperature"
            ]
            == value
        )


def test_profile_openrouter_sends_pinned_provider_without_fallback_and_data_collection_deny() -> (
    None
):
    """FJ3: ``model`` is always sent and ``provider`` is exactly one slug with fallbacks off,
    ``require_parameters`` on and ``data_collection`` ``deny``; a missing slug is a refusal."""
    profiles = jmod("judgeprofiles")
    body = body_of("openrouter/1")
    assert body["model"] == "judge-model-1"
    assert body["provider"] == {
        "order": ["provider-one"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }
    assert list(body["provider"]) == [
        "order",
        "allow_fallbacks",
        "require_parameters",
        "data_collection",
    ]
    assert body["provider"]["allow_fallbacks"] is False
    for member in ("models", "route", "transforms", "plugins", "reasoning", "usage", "include"):
        assert member not in body
    assert body["seed"] == 7
    assert body["max_tokens"] == 256
    assert refusal(lambda: body_of("openrouter/1", provider_slug=None)) == "provider_slug_required"
    # the record of what was sent: the same object, the seed with no guarantee
    sent = profiles.params_as_sent(
        profiles.get_profile("openrouter/1"), request(**FULL["openrouter/1"])
    )
    assert sent["provider"] == body["provider"]
    assert (sent["seed"], sent["seed_guarantee"]) == (7, "none")
    assert sent["response_format"] == "json_schema"
    assert sent["max_tokens"] == 256
    # the headers: bearer, and the owner's optional attribution headers only
    headers = profiles.request_headers(
        profiles.get_profile("openrouter/1"),
        CANARY_KEY,
        (("HTTP-Referer", "https://example.org/app"), ("X-OpenRouter-Title", "Tracer test")),
    )
    assert headers["Authorization"] == f"Bearer {CANARY_KEY}"
    assert headers["HTTP-Referer"] == "https://example.org/app"
    assert headers["X-OpenRouter-Title"] == "Tracer test"
    # a slug for another profile is a refusal, and a slug is a slug, not a list or a path
    core_judge = core("judge")
    for bad in ("", "Bad Slug", "a,b", ["a"], "x" * 65):
        with pytest.raises(ValueError, match=r"."):
            core_judge.JudgeParams(
                model="m",
                max_tokens=1,
                temperature=None,
                seed=None,
                reasoning_effort=None,
                provider_slug=bad,
                extra_headers=(),
            )


@pytest.mark.parametrize("name", NAMES)
def test_profile_reported_model_recorded_verbatim(tmp_path: Path, name: str) -> None:
    """FJ3: the model the provider returns is recorded as it came (``reported_model``), never
    filled from the request and never normalised; the usage record keeps the model asked for."""
    reported = "Vendor/Substitute-Model:2026-10-28\u202e"
    rig = JudgeRig(tmp_path, [openai_reply(model=reported)], profile=name)
    outcome = rig.complete(request(**FULL[name]))
    assert outcome.failure is None
    assert outcome.reported_model == reported
    assert reported != "judge-model-1"
    assert outcome.usage[0].model == "judge-model-1", "the model as requested"
    assert (
        outcome.params_sent["max_tokens" if name != "openai/1" else "max_completion_tokens"] == 256
    )
    # the same reply under another requested model: still the provider's string
    rig = JudgeRig(tmp_path / "b", [openai_reply(model="judge-model-1")], profile=name)
    outcome = rig.complete(request(**FULL[name]))
    assert outcome.reported_model == "judge-model-1"


def test_profile_sarvam_v2_uses_subscription_key_header(tmp_path: Path) -> None:
    """The v2 profile sends the key in the header ``api-subscription-key`` (the requirement of the
    second header is not verified, FJ9), ``max_tokens``, the reasoning effort explicitly because
    it counts against ``max_tokens``, and the structured-output request; never a bearer."""
    profiles = jmod("judgeprofiles")
    profile = profiles.get_profile("sarvam_v2/1")
    headers = profiles.request_headers(profile, CANARY_KEY, ())
    assert headers["api-subscription-key"] == CANARY_KEY
    assert not {k.lower() for k in headers} & {"authorization", "x-api-key"}
    rig = JudgeRig(tmp_path, [openai_reply()], profile="sarvam_v2/1")
    assert rig.complete(request(reasoning_effort="low")).failure is None
    sent = rig.transport.requests[0]
    assert sent.headers.get_list("api-subscription-key") == [CANARY_KEY]
    assert "authorization" not in sent.headers
    assert str(sent.url) == "https://api.sarvam.ai/v2/chat/completions"
    body = rig.body()
    assert body["reasoning_effort"] == "low"
    assert "max_tokens" in body
    assert body["response_format"]["type"] == "json_schema"
    assert (
        refusal(lambda: body_of("sarvam_v2/1", reasoning_effort=None))
        == "reasoning_effort_required"
    )
    assert refusal(lambda: body_of("sarvam_v2/1", seed=7)) == "seed_not_supported"
    for effort in ("low", "medium", "high", "max", "minimal"):
        assert body_of("sarvam_v2/1", reasoning_effort=effort)["reasoning_effort"] == effort
    # every other profile refuses an effort rather than sending it
    for name in NAMES:
        if name != "sarvam_v2/1":
            assert refusal(lambda n=name: body_of(n, reasoning_effort="low")) == (
                "reasoning_effort_not_supported"
            ), name


def test_unknown_profile_refused() -> None:
    """Rule 13: a name outside the closed set is refused (no generic profile, no base URL)."""
    profiles = jmod("judgeprofiles")
    for bad in (
        "generic/1",
        "openai",
        "OpenAI/1",
        "openai/2",
        "openai/1 ",
        "anthropic_compat/2",
        "anthropic",
        "fake/1",
        "https://api.example.org/v1",
        "http://127.0.0.1:8000/v1",
        "",
        "openrouter/1/extra",
        None,
        7,
    ):
        assert refusal(lambda b=bad: profiles.get_profile(b)) == "unknown_profile", bad
    assert tuple(profiles.PROFILE_NAMES) == NAMES
    assert not hasattr(profiles, "GenericProfile")


def test_profile_never_sends_unlisted_field() -> None:
    """Rule 12: whatever the configuration, the body holds only members the profile lists, always
    ``model``, ``messages``, the token field and ``stream``; the headers hold only the key, two
    transport headers and the profile's own optional headers."""
    profiles = jmod("judgeprofiles")
    output = core("judgeoutput")
    for name, (token, _auth, _header, sends, optional) in TABLE.items():
        profile = profiles.get_profile(name)
        accepted = 0
        for temperature, seed, effort, slug in itertools.product(
            (None, 0.3), (None, 5), (None, "low"), (None, "slug-a")
        ):
            settings = {
                "temperature": temperature,
                "seed": seed,
                "reasoning_effort": effort,
                "provider_slug": slug,
            }
            try:
                body = profiles.build_request_body(profile, request(**settings))
            except profiles.JudgeProfileError:
                continue
            accepted += 1
            assert set(body) <= set(sends), (name, settings, set(body) - set(sends))
            assert {"model", "messages", token, "stream"} <= set(body)
            assert list(body) == [m for m in sends if m in body], "the order is the profile's"
            assert body["stream"] is False
            sent = profiles.params_as_sent(profile, request(**settings))
            assert set(sent) <= {
                token,
                "temperature",
                "seed",
                "seed_guarantee",
                "reasoning_effort",
                "response_format",
                "provider",
            }
        assert accepted >= 1, name
        full = body_of(name)
        assert set(full) <= set(sends)
        # headers
        extra = tuple((h, "value-1") for h in optional)
        headers = profiles.request_headers(
            profile, None if name == "local_loopback/1" else CANARY_KEY, extra
        )
        allowed = {"accept-encoding", "content-type", *(h.lower() for h in optional)}
        if profile.auth == "bearer":
            allowed.add("authorization")
        if profile.auth == "header":
            allowed.add(profile.auth_header.lower())
        assert {h.lower() for h in headers} <= allowed, name
        # the key is in no body
        assert CANARY_KEY not in str(full)
    # response_format: the same strict JSON schema on the three profiles whose docs verify it
    for name in NAMES:
        has = "response_format" in body_of(name)
        assert has is (name in RESPONSE_FORMAT_PROFILES), name
    fmt = body_of("openrouter/1")["response_format"]
    assert fmt == {
        "type": "json_schema",
        "json_schema": {
            "name": "judge_output",
            "strict": True,
            "schema": output.JUDGE_OUTPUT_RESPONSE_SCHEMA,
        },
    }
    schema = output.JUDGE_OUTPUT_RESPONSE_SCHEMA
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert (
        set(schema["required"]) == set(schema["properties"]) == {"verdict", "reason_code", "facts"}
    )
    assert schema["properties"]["verdict"]["enum"] == list(output.JUDGE_VERDICTS)
    assert schema["properties"]["reason_code"]["enum"] == list(output.REASON_CODES)
    # nothing is sent that Q-J1 forbids, under any profile
    for name in NAMES:
        assert not set(body_of(name)) & {
            "n",
            "tools",
            "logprobs",
            "user",
            "store",
            "metadata",
            "functions",
        }


def test_unverified_profile_refused_for_real_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 13: an ``unverified`` profile is refused for a real run until its first owner-gated
    request is logged; the dossier sets the status; the loopback profile is not a real provider."""
    profiles = jmod("judgeprofiles")
    assert profiles.PROFILE_STATUS["gemini_compat/1"] == "unverified"
    assert profiles.PROFILE_STATUS["xai/1"] == "unverified"
    for name in NAMES:
        profile = profiles.get_profile(name)
        if name == "local_loopback/1":
            profiles.check_real_run(profile)
            continue
        assert profile.status == "unverified", name
        assert refusal(lambda p=profile: profiles.check_real_run(p)) == "profile_unverified", name
        assert refusal(lambda p=profile: profiles.check_real_run(p, first_request_gate=False)) == (
            "profile_unverified"
        )
        # the owner-gated first request is the one way through, and it is explicit
        profiles.check_real_run(profile, first_request_gate=True)
    # once the dossier says verified, the profile is no longer refused
    monkeypatch.setitem(profiles.PROFILE_STATUS, "openai/1", "verified")
    profile = profiles.get_profile("openai/1")
    assert profile.status == "verified"
    profiles.check_real_run(profile)
    # a status outside the two words is a programming error
    monkeypatch.setitem(profiles.PROFILE_STATUS, "xai/1", "probably")
    with pytest.raises(ValueError, match=r"."):
        profiles.get_profile("xai/1")
