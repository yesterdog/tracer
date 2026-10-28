# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J17 (support): the judge configuration ``tracer.judge_config/1`` (ADR 0030 rules 12, 14, 15
and 17; contract docs/phase4/api-surface-batch-j2.md section 3).

A strict JSON file with a version member and a closed set of keys. Every key is required (the
user states every value; ``null`` says "not sent" for the optional request settings). It never
holds a key, a key variable name or a base URL of its own: a key is read by the variable name of the
matched allowlist entry. A refusal is a code and never quotes a value.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest


def config() -> Any:
    return importlib.import_module("tracer_evalcore.judgeconfig")


def document(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": "tracer.judge_config/1",
        "profile": "openrouter/1",
        "endpoint": "https://openrouter.example.test/api/v1/chat/completions",
        "model": "vendor/model-1.5:free",
        "max_tokens": 400,
        "temperature": 0,
        "seed": 7,
        "reasoning_effort": None,
        "provider_slug": "vendor-a",
        "extra_headers": {
            "HTTP-Referer": "https://example.test/tracer",
            "X-OpenRouter-Title": "Tracer",
        },
    }
    base.update(changes)
    return base


def parse(doc: Any) -> Any:
    return config().parse_judge_config(doc if isinstance(doc, str) else json.dumps(doc))


def refused(doc: Any) -> str:
    with pytest.raises(config().JudgeConfigError) as caught:
        parse(doc)
    assert isinstance(caught.value, ValueError), "an input refusal"
    assert str(caught.value) == caught.value.code  # type: ignore[attr-defined]
    return str(caught.value.code)  # type: ignore[attr-defined]


def test_a_valid_configuration_is_read_into_the_judge_parameters() -> None:
    parsed = parse(document())
    assert parsed.profile == "openrouter/1"
    assert parsed.endpoint == "https://openrouter.example.test/api/v1/chat/completions"
    params = parsed.params
    assert (params.model, params.max_tokens, params.temperature, params.seed) == (
        "vendor/model-1.5:free", 400, 0, 7,
    )  # fmt: skip
    assert params.reasoning_effort is None
    assert params.provider_slug == "vendor-a"
    assert params.extra_headers == (
        ("HTTP-Referer", "https://example.test/tracer"),
        ("X-OpenRouter-Title", "Tracer"),
    )
    fake = parse(
        document(
            profile="fake/1",
            endpoint=None,
            seed=None,
            temperature=None,
            provider_slug=None,
            extra_headers={},
        )
    )
    assert fake.endpoint is None
    assert fake.params.extra_headers == ()
    assert fake.params.temperature is None
    assert fake.params.seed is None
    assert parse(document(temperature=0.5)).params.temperature == 0.5
    for name in (
        "anthropic_compat/1",
        "gemini_compat/1",
        "xai/1",
        "sarvam_v2/1",
        "openai/1",
        "local_loopback/1",
    ):
        assert (
            parse(document(profile=name, provider_slug=None, seed=None, extra_headers={})).profile
            == name
        )


def test_the_set_of_keys_is_closed_and_every_key_is_required() -> None:
    members = list(document())
    for key in members:
        broken = document()
        del broken[key]
        assert refused(broken) == (
            "judge_config_format" if key == "format" else "judge_config_missing_key"
        ), key
    for extra in ("api_key", "key_env", "base_url", "url", "user", "tools", "n", "top_p", "stream"):
        assert refused(document(**{extra: "x"})) == "judge_config_unknown_key", extra
    assert refused(document(format="tracer.judge_config/2")) == "judge_config_format"
    assert refused(document(format=None)) == "judge_config_format"
    assert refused("{") == "judge_config_not_json"
    assert (
        refused('{"format": "tracer.judge_config/1", "format": "tracer.judge_config/1"}')
        == "judge_config_not_json"
    )
    assert refused("[1]") == "judge_config_shape"
    assert refused("null") == "judge_config_shape"
    assert refused(" " * 70_000 + "{}") == "judge_config_too_large"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"profile": "nonesuch/1"}, "judge_config_profile"),
        ({"profile": None}, "judge_config_profile"),
        ({"profile": "openrouter"}, "judge_config_profile"),
        ({"profile": "fake/1", "endpoint": "https://x.example.test/"}, "judge_config_endpoint"),
        ({"endpoint": 5}, "judge_config_endpoint"),
        ({"endpoint": ""}, "judge_config_endpoint"),
        ({"model": ""}, "judge_config_model"),
        ({"model": "has space"}, "judge_config_model"),
        ({"model": 3}, "judge_config_model"),
        ({"max_tokens": 0}, "judge_config_max_tokens"),
        ({"max_tokens": True}, "judge_config_max_tokens"),
        ({"max_tokens": "400"}, "judge_config_max_tokens"),
        ({"max_tokens": 2**53}, "judge_config_max_tokens"),
        ({"max_tokens": 1.5}, "judge_config_max_tokens"),
        ({"temperature": -0.1}, "judge_config_temperature"),
        ({"temperature": 2.5}, "judge_config_temperature"),
        ({"temperature": True}, "judge_config_temperature"),
        ({"temperature": "0"}, "judge_config_temperature"),
        ({"seed": -1}, "judge_config_seed"),
        ({"seed": True}, "judge_config_seed"),
        ({"seed": 1.5}, "judge_config_seed"),
        ({"reasoning_effort": "HIGH"}, "judge_config_reasoning_effort"),
        ({"reasoning_effort": ""}, "judge_config_reasoning_effort"),
        ({"provider_slug": "Vendor A"}, "judge_config_provider_slug"),
        ({"provider_slug": ""}, "judge_config_provider_slug"),
        ({"extra_headers": []}, "judge_config_extra_headers"),
        ({"extra_headers": None}, "judge_config_extra_headers"),
        ({"extra_headers": {"Authorization": "x"}}, "judge_config_extra_headers"),
        ({"extra_headers": {"HTTP-Referer": ""}}, "judge_config_extra_headers"),
        ({"extra_headers": {"HTTP-Referer": "line\nbreak"}}, "judge_config_extra_headers"),
        ({"extra_headers": {"HTTP-Referer": 5}}, "judge_config_extra_headers"),
    ],
)
def test_each_setting_is_checked_and_a_refusal_is_a_code(
    changes: dict[str, Any], code: str
) -> None:
    assert refused(document(**changes)) == code


def test_a_value_shaped_like_a_key_is_refused_wherever_it_is_and_never_quoted() -> None:
    """ADR 0030 rule 15: the configuration names variables, never values; a string that has the
    shape of a credential (in the model, the endpoint, a header value or a member name) is refused,
    and the refusal does not repeat it."""
    shaped = "sk-" + "".join(["Ab1Cd2Ef3Gh4", "Ij5Kl6Mn7Op8", "Qr9St0Uv1Wx2"])
    for changes in (
        {"model": shaped},
        {"endpoint": f"https://x.example.test/{shaped}"},
        {"extra_headers": {"HTTP-Referer": shaped}},
        {"provider_slug": shaped.lower()},
        {"reasoning_effort": shaped},
    ):
        with pytest.raises(config().JudgeConfigError) as caught:
            parse(document(**changes))
        assert str(caught.value) == "judge_config_key_shaped", changes
        assert shaped not in repr(caught.value)
        assert shaped.lower() not in repr(caught.value)
    assert refused(document(**{shaped: 1})) == "judge_config_key_shaped"
