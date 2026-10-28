# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The judge configuration ``tracer.judge_config/1`` (ADR 0030 rules 12, 14, 15 and 17).

One strict JSON file with a version member and a closed set of keys. Every key is required and none
has a default (the user states every value; ``null`` says "not sent" for the optional request
settings). It names the profile and, for a real engine, the exact endpoint URL of an entry of the
owner's judge allowlist. It never holds a key, a key variable name or a base URL of its own: a key
is read by the variable name of the matched allowlist entry. A refusal is a ``JudgeConfigError``
whose message is its stable code and never a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.judge import EXTRA_HEADER_NAMES, JudgeParams
from tracer_evalschema.credentials import contains_credential_pattern
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "CONFIG_PROFILES",
    "JUDGE_CONFIG_FORMAT",
    "MAX_JUDGE_CONFIG_BYTES",
    "JudgeConfig",
    "JudgeConfigError",
    "parse_judge_config",
]

JUDGE_CONFIG_FORMAT: Final = "tracer.judge_config/1"
CONFIG_PROFILES: Final = (
    "anthropic_compat/1",
    "gemini_compat/1",
    "xai/1",
    "openrouter/1",
    "sarvam_v2/1",
    "openai/1",
    "local_loopback/1",
    "fake/1",
)
_KEYS: Final = (
    "format",
    "profile",
    "endpoint",
    "model",
    "max_tokens",
    "temperature",
    "seed",
    "reasoning_effort",
    "provider_slug",
    "extra_headers",
)
MAX_JUDGE_CONFIG_BYTES: Final = 65_536
_PARAM_FIELDS: Final = frozenset(
    {
        "model",
        "max_tokens",
        "temperature",
        "seed",
        "reasoning_effort",
        "provider_slug",
        "extra_headers",
    }
)


class JudgeConfigError(InputRefusedError):
    """A judge configuration was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JudgeConfig:
    profile: str
    endpoint: str | None
    params: JudgeParams


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, item in value.items():
            found.append(str(key))
            found.extend(_strings(item))
        return found
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def parse_judge_config(text: str) -> JudgeConfig:
    """The configuration of ``text``; ``JudgeConfigError`` on the first fault."""
    if len(text.encode("utf-8")) > MAX_JUDGE_CONFIG_BYTES:
        raise JudgeConfigError("judge_config_too_large")
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError, RecursionError):
        raise JudgeConfigError("judge_config_not_json") from None
    if not isinstance(document, Mapping):
        raise JudgeConfigError("judge_config_shape")
    if any(contains_credential_pattern(s) for s in _strings(document)):
        raise JudgeConfigError("judge_config_key_shaped")
    if document.get("format") != JUDGE_CONFIG_FORMAT:
        raise JudgeConfigError("judge_config_format")
    for key in _KEYS:
        if key not in document:
            raise JudgeConfigError("judge_config_missing_key")
    if set(document) - set(_KEYS):
        raise JudgeConfigError("judge_config_unknown_key")
    profile = document["profile"]
    if profile not in CONFIG_PROFILES:
        raise JudgeConfigError("judge_config_profile")
    endpoint = document["endpoint"]
    if profile == "fake/1" and endpoint is not None:
        raise JudgeConfigError("judge_config_endpoint")
    if endpoint is not None and (type(endpoint) is not str or not 1 <= len(endpoint) <= 2048):
        raise JudgeConfigError("judge_config_endpoint")
    headers = document["extra_headers"]
    if not isinstance(headers, Mapping) or any(name not in EXTRA_HEADER_NAMES for name in headers):
        raise JudgeConfigError("judge_config_extra_headers")
    try:
        params = JudgeParams(
            model=document["model"],
            max_tokens=document["max_tokens"],
            temperature=document["temperature"],
            seed=document["seed"],
            reasoning_effort=document["reasoning_effort"],
            provider_slug=document["provider_slug"],
            extra_headers=tuple((name, headers[name]) for name in sorted(headers)),
        )
    except ValueError as error:
        message = str(error)
        field = message.split(":", 1)[0]
        code = f"judge_config_{field}" if field in _PARAM_FIELDS else "judge_config_shape"
        raise JudgeConfigError(code) from None
    return JudgeConfig(profile=profile, endpoint=endpoint, params=params)
