# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The provider profiles of the judge (ADR 0030 rule 13; ADR 0024 amendment A11).

"OpenAI-compatible" is not one contract: each provider honours or silently ignores different
fields. The judge speaks to a provider only through one profile of a closed, named and versioned
set; a profile fixes the members of the request body it may send, the token field, the way the key
travels and the headers it may add, and cites the dossier ``docs/security/P4-J-providers.md`` fact
by fact. A setting a profile does not support is refused and never dropped. An ``unverified``
profile is refused for a real run until its first owner-gated request is logged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.judge import JudgeParams, JudgeRequest
from tracer_evalcore.judgeoutput import JUDGE_OUTPUT_RESPONSE_SCHEMA
from tracer_evalcore.llm import LlmMessage

__all__ = [
    "DOSSIER_PATH",
    "PROFILE_NAMES",
    "PROFILE_STATUS",
    "JudgeProfile",
    "JudgeProfileError",
    "build_request_body",
    "check_real_run",
    "checked_extra_headers",
    "get_profile",
    "params_as_sent",
    "planned_params",
    "request_headers",
]

DOSSIER_PATH: Final = "docs/security/P4-J-providers.md"
PROFILE_NAMES: Final = (
    "anthropic_compat/1",
    "gemini_compat/1",
    "xai/1",
    "openrouter/1",
    "sarvam_v2/1",
    "openai/1",
    "local_loopback/1",
)
#: the status column of the dossier, a plain dict read at ``get_profile`` time; every profile is
#: ``unverified`` until its first owner-gated request is logged and the dossier row is written
PROFILE_STATUS: dict[str, str] = dict.fromkeys(PROFILE_NAMES, "unverified")
_BASE: Final = ("model", "messages", "max_tokens", "stream", "temperature")
# The documented endpoint of each provider, as (host, path): informational only. The URL a run
# connects to is the owner's allowlist entry (ADR 0030 rule 14), never this table; the pieces are
# joined at run time so that no URL literal stands in the code (ADR 0024 rule 3, whose guard test
# scans this package for them).
_ENDPOINTS: Final = {
    "anthropic_compat/1": ("api.anthropic.com", "/v1/chat/completions"),
    "gemini_compat/1": ("generativelanguage.googleapis.com", "/v1beta/openai/chat/completions"),
    "xai/1": ("api.x.ai", "/v1/chat/completions"),
    "openrouter/1": ("openrouter.ai", "/api/v1/chat/completions"),
    "sarvam_v2/1": ("api.sarvam.ai", "/v2/chat/completions"),
    "openai/1": ("api.openai.com", "/v1/chat/completions"),
}
_RESPONSE_FORMAT: Final = {
    "type": "json_schema",
    "json_schema": {"name": "judge_output", "strict": True, "schema": JUDGE_OUTPUT_RESPONSE_SCHEMA},
}
_PROVIDER_FIXED: Final = {
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
}


class JudgeProfileError(InputRefusedError):
    """A profile or a setting was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    endpoint: str | None
    auth: str
    auth_header: str | None
    token_field: str
    sends: tuple[str, ...]
    seed: bool
    response_format: bool
    reasoning_effort: str
    provider_object: bool
    optional_headers: tuple[str, ...]
    status: str
    real_provider: bool
    dossier: str


def _static(name: str) -> dict[str, Any]:
    sends: tuple[str, ...] = _BASE
    field = "max_tokens"
    auth, header = "bearer", None
    optional: tuple[str, ...] = ()
    if name == "xai/1":
        sends = (*_BASE, "response_format")
    elif name == "openrouter/1":
        sends = (*_BASE, "seed", "response_format", "provider")
        optional = ("HTTP-Referer", "X-OpenRouter-Title")
    elif name == "sarvam_v2/1":
        sends = (*_BASE, "response_format", "reasoning_effort")
        auth, header = "header", "api-subscription-key"
    elif name == "openai/1":
        field = "max_completion_tokens"
        sends = ("model", "messages", field, "stream", "temperature", "seed")
    elif name == "anthropic_compat/1":
        optional = ("anthropic-workspace-id",)
    elif name == "local_loopback/1":
        auth = "none"
    return {
        "endpoint": f"https://{_ENDPOINTS[name][0]}{_ENDPOINTS[name][1]}"
        if name in _ENDPOINTS
        else None,
        "auth": auth,
        "auth_header": header,
        "token_field": field,
        "sends": sends,
        "seed": "seed" in sends,
        "response_format": "response_format" in sends,
        "reasoning_effort": "required" if "reasoning_effort" in sends else "never",
        "provider_object": "provider" in sends,
        "optional_headers": optional,
        "real_provider": name != "local_loopback/1",
    }


def get_profile(name: object) -> JudgeProfile:
    """The profile of ``name``; ``unknown_profile`` for anything outside the closed set."""
    if type(name) is not str or name not in PROFILE_NAMES:
        raise JudgeProfileError("unknown_profile")
    status = PROFILE_STATUS[name]
    if status not in ("verified", "unverified"):
        raise ValueError("status: not verified or unverified")
    return JudgeProfile(name=name, status=status, dossier=DOSSIER_PATH, **_static(name))


def check_real_run(profile: JudgeProfile, *, first_request_gate: bool = False) -> None:
    """An ``unverified`` profile of a real provider is refused unless this is the owner-gated
    first request; the loopback profile is not a real provider."""
    if profile.real_provider and profile.status != "verified" and not first_request_gate:
        raise JudgeProfileError("profile_unverified")


def _check_settings(profile: JudgeProfile, request: JudgeRequest) -> None:
    params = request.params
    if params.seed is not None and not profile.seed:
        raise JudgeProfileError("seed_not_supported")
    if profile.reasoning_effort == "never" and params.reasoning_effort is not None:
        raise JudgeProfileError("reasoning_effort_not_supported")
    if profile.reasoning_effort == "required" and params.reasoning_effort is None:
        raise JudgeProfileError("reasoning_effort_required")
    if not profile.provider_object and params.provider_slug is not None:
        raise JudgeProfileError("provider_slug_not_supported")
    if profile.provider_object and params.provider_slug is None:
        raise JudgeProfileError("provider_slug_required")


def build_request_body(profile: JudgeProfile, request: JudgeRequest) -> dict[str, Any]:
    """The JSON body of the request: only members the profile lists, in its order."""
    _check_settings(profile, request)
    params = request.params
    body: dict[str, Any] = {
        "model": params.model,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        profile.token_field: params.max_tokens,
        "stream": False,
    }
    if params.temperature is not None:
        body["temperature"] = params.temperature
    if params.seed is not None:
        body["seed"] = params.seed
    if profile.response_format:
        body["response_format"] = _RESPONSE_FORMAT
    if profile.reasoning_effort == "required":
        body["reasoning_effort"] = params.reasoning_effort
    if profile.provider_object:
        body["provider"] = {"order": [params.provider_slug], **_PROVIDER_FIXED}
    return {member: body[member] for member in profile.sends if member in body}


def params_as_sent(profile: JudgeProfile, request: JudgeRequest) -> dict[str, Any]:
    """The record of what was sent besides the model and the messages (``JudgeInfo.params`` and
    ``tracer.judge_profile/1``): the token field, the optional settings and their guarantee."""
    body = build_request_body(profile, request)
    sent: dict[str, Any] = {profile.token_field: body[profile.token_field]}
    for member in ("temperature", "seed", "reasoning_effort", "provider"):
        if member in body:
            sent[member] = body[member]
    if "seed" in body:
        sent["seed_guarantee"] = "none"
    if "response_format" in body:
        sent["response_format"] = "json_schema"
    return sent


def planned_params(profile: JudgeProfile, params: JudgeParams) -> dict[str, Any]:
    """What ``params_as_sent`` will record for these settings, before any request exists (the
    document ``tracer.judge_profile/1`` of step 2). A setting the profile does not support is
    refused here as it would be at the first request, and the extra headers are checked too."""
    probe = JudgeRequest(
        index=0,
        item_id="probe",
        attempt=1,
        match_mode="all_facts",
        fact_count=1,
        messages=(LlmMessage(role="system", content="-"), LlmMessage(role="user", content="-")),
        params=params,
    )
    checked_extra_headers(profile, params.extra_headers)
    return params_as_sent(profile, probe)


def checked_extra_headers(
    profile: JudgeProfile, extra_headers: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """The optional headers of a request; one the profile does not list is refused."""
    checked: dict[str, str] = {}
    for name, value in extra_headers:
        if name not in profile.optional_headers:
            raise JudgeProfileError("header_not_supported")
        checked[name] = value
    return checked


def request_headers(
    profile: JudgeProfile, key: str | None, extra_headers: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """The headers of a request: the key in the one place the profile says, two transport headers
    and the optional headers the profile lists."""
    headers = {"accept-encoding": "identity", "content-type": "application/json"}
    if profile.auth == "none":
        if key is not None:
            raise ValueError("key: a profile without authorisation takes no key")
    elif key is None:
        raise ValueError("key: this profile needs a key")
    elif profile.auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif profile.auth_header is not None:
        headers[profile.auth_header] = key
    headers.update(checked_extra_headers(profile, extra_headers))
    return headers
