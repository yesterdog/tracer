# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The options of ``tracer simulate`` (P4-23). Imports nothing but the standard library, so the
command line stays free of the simulator and its WebSocket stack until a call is placed."""

from __future__ import annotations

import argparse
import re
import unicodedata
from typing import Any, Final

__all__ = [
    "AUTHORISATION_REFERENCE_MAX_CHARS",
    "CAP_OPTIONS",
    "SIM_EXIT_CODES",
    "add_judge_arguments",
    "add_simulate_arguments",
    "judge_usage_problem",
    "simulate_usage_problem",
]

AUTHORISATION_REFERENCE_MAX_CHARS: Final = 200
_REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_POSITIVE: Final = re.compile(r"[1-9][0-9]{0,15}")
_MAX_SAFE: Final = 9007199254740991
#: option -> (cap name of the sim profile, largest value)
CAP_OPTIONS: Final = {
    "max_call_seconds": ("call_seconds", 14_400),
    "max_tts_chars": ("tts_chars", _MAX_SAFE),
    "max_llm_tokens": ("llm_tokens", _MAX_SAFE),
    "max_stt_seconds": ("stt_seconds", _MAX_SAFE),
}
SIM_EXIT_CODES: Final = {
    0: "the call was established and every result is scored or not applicable",
    1: "unexpected failure",
    2: "a result was not scored, or the caller's language model failed",
    3: "speech-to-text failed",
    4: "input refused",
    5: "call not established",
    64: "usage",
}
_EXIT_CODE_HELP: Final = "exit codes:\n" + "".join(
    f"  {code:<2}  {meaning}\n" for code, meaning in sorted(SIM_EXIT_CODES.items())
)


def _cap(limit: int) -> Any:
    def parse(value: str) -> int:
        if _POSITIVE.fullmatch(value) is None or int(value) > limit:
            raise argparse.ArgumentTypeError(f"use a whole number from 1 to {limit}")
        return int(value)

    return parse


def _reference(value: str) -> str:
    if not 1 <= len(value) <= AUTHORISATION_REFERENCE_MAX_CHARS:
        raise argparse.ArgumentTypeError(f"use 1 to {AUTHORISATION_REFERENCE_MAX_CHARS} characters")
    if value != value.strip():
        raise argparse.ArgumentTypeError("no leading or trailing white space")
    if any(unicodedata.category(c) in _REFUSED_CATEGORIES for c in value):
        raise argparse.ArgumentTypeError("no control, format, separator or unassigned character")
    return value


def _tts(value: str) -> str:
    if value in ("sarvam", "fake"):
        return value
    raise argparse.ArgumentTypeError("use sarvam or fake")


def _stt(value: str) -> str:
    if value in ("sarvam", "fake") or (value.startswith("fake:") and len(value) > len("fake:")):
        return value
    raise argparse.ArgumentTypeError("use sarvam, fake or fake:DIRECTORY")


def _caller(value: str) -> str:
    if value in ("scripted", "sarvam") or (value.startswith("fake:") and len(value) > len("fake:")):
        return value
    raise argparse.ArgumentTypeError("use scripted, sarvam or fake:DIRECTORY")


def _agent(value: str) -> str:
    if value.startswith("fake:") and len(value) > len("fake:"):
        return value
    raise argparse.ArgumentTypeError("use fake:SCENARIO_FILE")


def _language(value: str) -> str:
    if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value) is None:
        raise argparse.ArgumentTypeError("not a language tag")
    return value


def _judge_engine(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]*/[0-9]{1,3}", value):
        return value
    for prefix in ("fake:", "replay:"):
        if value.startswith(prefix) and len(value) > len(prefix):
            return value
    raise argparse.ArgumentTypeError("use a profile name, fake:SCENARIO or replay:DIRECTORY")


def add_judge_arguments(parser: argparse.ArgumentParser, *, required: bool) -> None:
    """The options of the judge, shared by ``simulate`` (all optional, together) and ``judge``
    (the golden set, the configuration and the engine are required)."""
    parser.add_argument(
        "--golden", required=required, metavar="FILE", help="the golden set (tracer.golden_set/1)"
    )
    parser.add_argument(
        "--judge-config",
        required=required,
        metavar="FILE",
        help="the judge configuration (tracer.judge_config/1); every value is yours",
    )
    parser.add_argument(
        "--judge",
        required=required,
        type=_judge_engine,
        metavar="ENGINE",
        help="a profile name such as openrouter/1, fake:SCENARIO or replay:DIRECTORY",
    )
    parser.add_argument(
        "--judge-allowlist",
        metavar="FILE",
        help="the judge endpoints you allow (required for a profile engine; mode 0600)",
    )
    parser.add_argument(
        "--max-judge-requests",
        type=_cap(_MAX_SAFE),
        metavar="N",
        help="spend cap on judge requests, regenerations included (required for a profile engine)",
    )
    parser.add_argument(
        "--max-judge-tokens",
        type=_cap(_MAX_SAFE),
        metavar="N",
        help="spend cap on judge tokens (required for a profile engine)",
    )
    parser.add_argument(
        "--judge-authorisation-reference",
        type=_reference,
        metavar="TEXT",
        help="who authorised sending the call text to this provider (required for a real "
        "provider; only its SHA-256 is stored)",
    )
    parser.add_argument(
        "--judge-first-request-gate",
        action="store_true",
        default=None,
        help="the owner-gated first request of a profile that is not yet verified",
    )


def judge_usage_problem(args: argparse.Namespace) -> str | None:
    """The usage error that depends on more than one judge option, or ``None``."""
    given = [args.golden, args.judge_config, args.judge]
    extras = {
        "--judge-allowlist": args.judge_allowlist,
        "--max-judge-requests": args.max_judge_requests,
        "--max-judge-tokens": args.max_judge_tokens,
        "--judge-authorisation-reference": args.judge_authorisation_reference,
        "--judge-first-request-gate": args.judge_first_request_gate or None,
    }
    if any(x is None for x in given):
        if any(x is not None for x in given):
            return "--golden, --judge-config and --judge go together"
        used = [name for name, value in extras.items() if value is not None]
        return f"{used[0]} needs --golden, --judge-config and --judge" if used else None
    if args.judge.startswith(("fake:", "replay:")):
        used = [name for name, value in extras.items() if value is not None]
        if used:
            return f"{used[0]} applies to a profile engine only"
    return None


def add_simulate_arguments(parser: argparse.ArgumentParser) -> None:
    """The options of the command; every value that decides a call or its spend is stated."""
    parser.epilog = _EXIT_CODE_HELP
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("--target", metavar="FILE", help="the agent to call (tracer.sim_target/1)")
    parser.add_argument(
        "--agent",
        type=_agent,
        metavar="fake:SCENARIO",
        help="an offline fake agent from a scenario file, instead of --target; no socket is opened",
    )
    parser.add_argument(
        "--allowlist",
        metavar="FILE",
        help="the targets you allow (required with --target for a real run)",
    )
    parser.add_argument(
        "--evaluator", required=True, metavar="FILE", help="the test (tracer.sim_evaluator/1)"
    )
    parser.add_argument(
        "--persona", required=True, metavar="FILE", help="the caller (tracer.sim_persona/1)"
    )
    parser.add_argument(
        "--profile", metavar="FILE", help="synthetic identity values (tracer.sim_test_profile/1)"
    )
    parser.add_argument(
        "--sim-profile",
        required=True,
        metavar="FILE",
        help="the simulator configuration (tracer.sim_profile_file/1); every value is yours",
    )
    parser.add_argument(
        "--tts",
        required=True,
        type=_tts,
        metavar="ENGINE",
        help="sarvam, which sends the caller's lines to the provider, or fake (required)",
    )
    parser.add_argument(
        "--caller",
        required=True,
        type=_caller,
        metavar="MODE",
        help="scripted, sarvam (an LLM caller) or fake:DIRECTORY (replay lines) (required)",
    )
    parser.add_argument(
        "--stt",
        required=True,
        type=_stt,
        metavar="ENGINE",
        help="sarvam, fake (the words of a fake agent) or fake:DIRECTORY (recorded bodies)",
    )
    for option, (_, limit) in CAP_OPTIONS.items():
        parser.add_argument(
            "--" + option.replace("_", "-"),
            dest=option,
            type=_cap(limit),
            metavar="N",
            help="spend cap; required when the engine it bounds is not fake",
        )
    parser.add_argument(
        "--authorisation-reference",
        type=_reference,
        metavar="TEXT",
        help="who authorised this call (required for a real run; only its SHA-256 is stored)",
    )
    parser.add_argument("--out", required=True, metavar="DIR", help="the output directory")
    parser.add_argument(
        "--agent-transcript", metavar="FILE", help="the agent's own export of the call (optional)"
    )
    parser.add_argument(
        "--declared-language",
        type=_language,
        metavar="CODE",
        help="the language you declare for --agent-transcript (required with it)",
    )
    parser.add_argument(
        "--env-file", metavar="FILE", help="read the key SARVAM_API_KEY from this file only"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan and send nothing")
    add_judge_arguments(parser, required=False)


def simulate_usage_problem(args: argparse.Namespace) -> str | None:
    """The usage error that depends on more than one option, or ``None``."""
    if (args.target is None) == (args.agent is None):
        return "use exactly one of --target and --agent"
    if args.agent is not None and args.allowlist is not None:
        return "--allowlist applies to --target only"
    if (args.agent_transcript is None) != (args.declared_language is None):
        return "--agent-transcript and --declared-language go together"
    if args.stt == "fake" and args.agent is None:
        return "--stt fake needs --agent fake:SCENARIO"
    return judge_usage_problem(args)
