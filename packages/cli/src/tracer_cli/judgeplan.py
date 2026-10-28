# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The judge of a command, from the options to an engine (``tracer simulate`` and ``tracer
judge``; ADR 0030 rules 12 to 17 and 20).

The stages are separate because the order of operations pins where each refusal happens: the
inputs and the profile settings at step 1, the owner's allowlist and the DNS answers at step 2,
the authorisation reference at step 3, the caps at step 4, the key at step 6. A refusal is a code
(``Refusal``), never a value. Nothing here is imported by the command line until a judge runs.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeVar

from tracer_cli.cli import Refusal
from tracer_evalcore.golden import GoldenSet, GoldenSetError, parse_golden_set
from tracer_evalcore.judgeconfig import JudgeConfig, JudgeConfigError, parse_judge_config
from tracer_evalcore.judgerecords import (
    JudgeDocumentError,
    JudgeSetup,
    build_judge_profile_document,
)
from tracer_evalproviders import judgeprofiles
from tracer_evalproviders.judgeexchange import (
    FAKE_SCENARIOS,
    create_fake_judge,
    create_replay_judge,
    planned_fake_params,
)
from tracer_evalproviders.judgehosts import (
    BoundEndpoint,
    JudgeAllowlistError,
    JudgeEndpoint,
    Resolver,
    bind_checked,
    check_endpoint,
    read_judge_allowlist,
)
from tracer_evalproviders.judgeprofiles import JudgeProfileError
from tracer_evalproviders.openai_compat import create_judge_engine

__all__ = [
    "JudgeInputs",
    "bind_key",
    "build_engine",
    "build_setup",
    "check_allowlist",
    "check_caps",
    "check_reference",
    "domain_errors",
    "engine_kind",
    "parse_inputs",
]

_FAKE: Final = "fake/1"
T = TypeVar("T")


@dataclass(frozen=True)
class JudgeInputs:
    engine: str
    kind: str  # "profile", "fake" or "replay"
    golden: GoldenSet
    config: JudgeConfig
    planned: Mapping[str, Any]


def engine_kind(engine: str) -> str:
    if engine.startswith("fake:"):
        return "fake"
    return "replay" if engine.startswith("replay:") else "profile"


def domain_errors(function: Callable[[], T]) -> T:
    """The refusals of the judge's own errors: a code, never a value (exit 4)."""
    try:
        return function()
    except Refusal:
        raise
    except (
        GoldenSetError,
        JudgeConfigError,
        JudgeAllowlistError,
        JudgeProfileError,
        JudgeDocumentError,
    ) as error:
        raise Refusal(str(error)) from None


def parse_inputs(args: argparse.Namespace, read: Callable[[str, str], str]) -> JudgeInputs:
    """Step 1: the golden set, the configuration, and what the profile would send. Nothing is
    read but the two files; no key, no allowlist, no network."""
    engine: str = args.judge
    kind = engine_kind(engine)
    golden = parse_golden_set(read(args.golden, "golden set"))
    config = parse_judge_config(read(args.judge_config, "judge configuration"))
    if kind == "fake":
        if engine[len("fake:") :] not in FAKE_SCENARIOS:
            raise Refusal("unknown_scenario")
        if config.profile != _FAKE:
            raise Refusal("judge_config_profile_differs")
        return JudgeInputs(engine, kind, golden, config, planned_fake_params(config.params))
    if config.profile == _FAKE:
        raise Refusal("judge_config_profile_differs")
    profile = judgeprofiles.get_profile(config.profile)
    if kind == "profile":
        if engine != config.profile:
            raise Refusal("judge_config_profile_differs")
        if config.endpoint is None:
            raise Refusal("judge_config_endpoint")
    elif config.endpoint is not None:
        raise Refusal("judge_config_endpoint")
    planned = judgeprofiles.planned_params(profile, config.params)
    if kind == "replay":
        # the directory must be there (a refusal at step 1, before anything else is read)
        create_replay_judge(os.fspath(engine[len("replay:") :]), profile=profile)
    return JudgeInputs(engine, kind, golden, config, planned)


def check_allowlist(
    args: argparse.Namespace,
    inputs: JudgeInputs,
    *,
    resolver: Resolver | None,
    auth_token_env: str | None = None,
) -> JudgeEndpoint | None:
    """Step 2: the entry of the owner's judge allowlist, its profile and its DNS answers. The key
    is not read."""
    if inputs.kind != "profile":
        return None
    if args.judge_allowlist is None:
        raise Refusal("judge_allowlist_missing")
    entries = read_judge_allowlist(args.judge_allowlist)
    if auth_token_env is not None and any(entry.key_env == auth_token_env for entry in entries):
        raise Refusal("auth_token_env_is_judge_key_env")  # one variable, two hosts (BL-354)
    assert inputs.config.endpoint is not None  # noqa: S101  (parse_inputs)
    return check_endpoint(
        entries,
        url=inputs.config.endpoint,
        profile=inputs.config.profile,
        resolver=resolver,
        first_request_gate=bool(args.judge_first_request_gate),
    )


def check_reference(args: argparse.Namespace, inputs: JudgeInputs) -> str | None:
    """Step 3: a real provider needs the authorisation reference; only its text is returned (the
    command stores its SHA-256)."""
    if inputs.kind != "profile":
        return None
    profile = judgeprofiles.get_profile(inputs.config.profile)
    if profile.real_provider and args.judge_authorisation_reference is None:
        raise Refusal("judge_authorisation_reference_missing")
    reference: str | None = args.judge_authorisation_reference
    return reference


def check_caps(args: argparse.Namespace, inputs: JudgeInputs) -> None:
    """Step 4: a profile engine has both caps; there is no default."""
    if inputs.kind != "profile":
        return
    if args.max_judge_requests is None:
        raise Refusal("cap_missing (--max-judge-requests)")
    if args.max_judge_tokens is None:
        raise Refusal("cap_missing (--max-judge-tokens)")


def build_setup(
    args: argparse.Namespace, inputs: JudgeInputs, entry: JudgeEndpoint | None
) -> JudgeSetup:
    """The judge of the snapshot: the golden set, the model reference and the document
    ``tracer.judge_profile/1``."""
    host = path = key_env = None
    if entry is not None:
        netloc = entry.url.split("://", 1)[1].split("/", 1)[0]
        host, path, key_env = netloc, entry.path, entry.key_env
    config = inputs.config
    document = build_judge_profile_document(
        profile=config.profile,
        host=host,
        path=path,
        model=config.params.model,
        params=inputs.planned,
        max_judge_requests=args.max_judge_requests,
        max_judge_tokens=args.max_judge_tokens,
        key_env=key_env,
        golden=inputs.golden,
    )
    return JudgeSetup(
        golden=inputs.golden,
        model_ref={"provider": config.profile, "model": config.params.model},
        profile_document=document,
    )


def bind_key(
    entry: JudgeEndpoint | None, *, environ: Mapping[str, str], env_file: Path | None
) -> BoundEndpoint | None:
    """Step 6: read the key of the matched entry by its variable name."""
    if entry is None:
        return None
    try:
        return bind_checked(entry, environ=environ, env_file=env_file)
    except ValueError as error:
        raise Refusal(str(getattr(error, "reason", None) or type(error).__name__)) from None


def build_engine(
    args: argparse.Namespace,
    inputs: JudgeInputs,
    bound: BoundEndpoint | None,
    *,
    call_log: Any,
    exchanges: Any,
    transport: Any,
    sleep: Callable[[float], None],
) -> Any:
    if inputs.kind == "fake":
        return create_fake_judge(inputs.engine[len("fake:") :])
    if inputs.kind == "replay":
        return create_replay_judge(
            os.fspath(inputs.engine[len("replay:") :]),
            profile=judgeprofiles.get_profile(inputs.config.profile),
        )
    assert bound is not None  # noqa: S101
    return create_judge_engine(
        bound,
        call_log=call_log,
        max_requests=args.max_judge_requests,
        max_tokens=args.max_judge_tokens,
        exchanges=exchanges,
        transport=transport,
        sleep=sleep,
    )
