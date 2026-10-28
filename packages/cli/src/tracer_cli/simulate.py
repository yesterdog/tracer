# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``tracer simulate``: one simulated call, from the checks to the report (P4-21, P4-23).

The 15 steps of ADR 0026 rule 14 run in this order and in no other (``STEPS``, pinned by a
call-trace test): (1) parse and validate every input; (2) the allowlist; (3) the authorisation;
(4) the spend caps; (5) the output probe; (6) the keys and the call log; (7) pre-synthesis; (8) the
store; (9) connect; (10) the call loop; (11) hang up; (12) the speech-to-text of the agent channel;
(13) scoring; (14) the store write; (15) the report. A refusal at steps 1 to 6 sends nothing, opens
no socket and is exit 4. A ``BaseException`` after step 6 keeps the usage of every answered request
in ``usage-recovery-<UTC>.json``. This module is imported only when a simulation runs, so
importing the command line loads neither the simulator nor its WebSocket stack.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import hashlib
import os
import signal
import warnings
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

from tracer_cli import cli as cli_module
from tracer_cli import judgeplan, simsummary, store
from tracer_cli.cli import (
    EXIT_UNEXPECTED,
    MAX_CONFIG_FILE_BYTES,
    MAX_TRANSCRIPT_FILE_BYTES,
    Environment,
    ProviderFailure,
    Refusal,
)
from tracer_cli.fakes import FakeTts, ScenarioStt
from tracer_cli.judgesummary import plan_lines
from tracer_cli.replay import ReplayStt
from tracer_cli.report import RelativeAudio, relative_audio_src
from tracer_cli.simargs import CAP_OPTIONS
from tracer_cli.simreport import render_sim_report
from tracer_evalcore.audio.activity import activity_chunk_plan, select_stt_channel
from tracer_evalcore.brain import CallerBrain
from tracer_evalcore.brains import LlmBrain, ReplayBrain, ReplayDirectoryError
from tracer_evalcore.callstate import effective_max_duration_ms
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.golden import bind_golden_set
from tracer_evalcore.importers.voicera import read_voicera_export
from tracer_evalcore.judge import JudgeContext
from tracer_evalcore.judgerecords import JudgeSetup, with_judge_results
from tracer_evalcore.judgerun import judge_bundle
from tracer_evalcore.llm import LlmContext, LlmOutcome, LlmRequest
from tracer_evalcore.pipeline import CostParts
from tracer_evalcore.prompt import prompt_inputs
from tracer_evalcore.recorder import LogFrame, RecorderFormat, build_recording
from tracer_evalcore.simbundle import (
    CallerLineFact,
    ExportRead,
    RecordingFacts,
    SimulatedCall,
    build_simulated_bundle,
    provisional_privacy,
    simulated_agent_target,
    simulated_project,
)
from tracer_evalcore.simoutcome import authorisation_reference_sha256, build_sim_outcome
from tracer_evalcore.simprofile import (
    SimProfileFile,
    build_sim_profile,
    parse_sim_profile_file,
    validate_sim_profile,
)
from tracer_evalcore.simscore import SimulatedConfiguration, build_simulated_configuration
from tracer_evalcore.simspec import (
    SimEvaluatorSpec,
    SimPersonaSpec,
    SimTargetSpec,
    SimTestProfileSpec,
    parse_sim_evaluator,
    parse_sim_persona,
    parse_sim_target,
    parse_sim_test_profile,
    resolve_profile_text,
)
from tracer_evalcore.simtiming import CallerTurn, extract_timing
from tracer_evalcore.stt import (
    SttContext,
    SttEngine,
    SttError,
    SttOutcome,
)
from tracer_evalcore.sttparts import CappedStt, assemble_stt_outcome, single_chunk_plan
from tracer_evalcore.tts import (
    TtsCapReachedError,
    TtsContext,
    TtsEngine,
    TtsError,
    TtsOutcome,
    TtsRequest,
    TtsVoice,
)
from tracer_evalcore.turntaking import TurnTakingPolicy
from tracer_evalproviders.cost import compute_usage_cost
from tracer_evalproviders.judgeexchange import (
    ExchangeStore,
    JudgeExchangeModeWarning,
    exchange_directory,
)
from tracer_evalproviders.judgehosts import BoundEndpoint, JudgeEndpoint, Resolver
from tracer_evalproviders.sarvam.chat import create_sarvam_llm
from tracer_evalproviders.sarvam.engine import create_sarvam_stt_for_run
from tracer_evalproviders.sarvam.tts import create_sarvam_tts
from tracer_evalproviders.secrets import load_role_key, provider_config_ref
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalproviders.ttscache import TTS_CACHE_DIR_NAME, TtsCache, tts_cache_key
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import ArtifactId, RunId
from tracer_evalschema.transcript import LanguageDeclaration
from tracer_evalschema.usage import UsageRecord
from tracer_evalsim.addresses import Resolver as TargetResolver
from tracer_evalsim.addresses import check_target_addresses, system_resolver
from tracer_evalsim.allowlist import AllowlistError, find_entry, read_allowlist
from tracer_evalsim.clock import Clock, SystemClock
from tracer_evalsim.dialects.base import Dialect, WireConnection
from tracer_evalsim.dialects.protobuf_dialect import PipecatProtobufDialect
from tracer_evalsim.dialects.twilio import TwilioMediaStreamDialect
from tracer_evalsim.errors import TargetRefusedError
from tracer_evalsim.fakeagent import (
    FakeAgentError,
    FakeAgentScenario,
    connect_fake_agent,
    parse_fake_agent,
    speech_texts,
)
from tracer_evalsim.guard import (
    CallLock,
    GuardError,
    check_call_interval,
    kill_switch_present,
)
from tracer_evalsim.limits import CLOSE_TIMEOUT_S, CONNECT_TIMEOUT_S
from tracer_evalsim.lines import LlmLines, PresynthesisedLines, SynthesisedLine, run_in_thread
from tracer_evalsim.runner import (
    CallerEnd,
    CallResult,
    RunnerSettings,
    StopFlag,
    run_call,
)
from tracer_evalsim.ws import (
    BearerToken,
    ConnectionLabels,
    WebSocketTarget,
    check_connection_labels,
    open_websocket,
    parse_target_url,
    read_bearer_token,
)

__all__ = [
    "RECORDING_NAME",
    "STEPS",
    "STEPS_WITH_JUDGE",
    "ConnectRequest",
    "EngineSeam",
    "SimWorld",
    "SystemSignals",
    "run_simulate",
]

STEPS: Final = (
    "parse_inputs",
    "allowlist",
    "authorisation",
    "caps",
    "output_probe",
    "keys_and_call_log",
    "presynthesis",
    "open_store",
    "connect",
    "call_loop",
    "hang_up",
    "post_call_stt",
    "scoring",
    "store_write",
    "report",
)
#: the steps of a judged run: the judge is step 13b, after scoring and before the store (Q-J6)
STEPS_WITH_JUDGE: Final = (
    *STEPS[: STEPS.index("scoring") + 1],
    "judge",
    *STEPS[STEPS.index("scoring") + 1 :],
)
RECORDING_NAME: Final = "recording-{stamp}.wav"
_MIN_SEARCH_MS: Final = 5_000
T = TypeVar("T")


@dataclass(frozen=True, kw_only=True)
class EngineSeam:
    """What a factory of ``SimWorld`` is given to build one engine (tests only)."""

    role: str  # "tts", "llm" or "stt"
    call_log: SimCallLog
    out_dir: Path
    environ: Mapping[str, str] = field(repr=False)
    env_file: Path | None
    transport: Any
    sleep: Callable[[float], None]
    cap: int | None
    model: str | None
    cache: TtsCache | None = None


@dataclass(frozen=True, kw_only=True)
class ConnectRequest:
    """What a connector is given: the target as allowlisted, the dialect and the labels."""

    url: str
    dialect: Dialect
    labels: ConnectionLabels
    token: BearerToken | None = field(repr=False)


class SignalSource(Protocol):
    def install(self, on_signal: Callable[[], None]) -> contextlib.AbstractContextManager[None]: ...


class SystemSignals:
    """SIGINT and SIGTERM through the running loop (the main thread only)."""

    @contextlib.contextmanager
    def install(self, on_signal: Callable[[], None]) -> Iterator[None]:
        loop = asyncio.get_running_loop()
        added: list[signal.Signals] = []
        for number in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(number, on_signal)
                added.append(number)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        try:
            yield
        finally:
            for number in added:
                loop.remove_signal_handler(number)


@dataclass(frozen=True, kw_only=True)
class SimWorld:
    """The seams of one run: every one is optional and has a system default (tests only)."""

    clock: Clock | None = None
    run_async: Callable[[Callable[[], Awaitable[Any]]], Any] | None = None
    connect: Callable[[ConnectRequest], Awaitable[WireConnection]] | None = None
    tts_factory: Callable[[EngineSeam], TtsEngine] | None = None
    llm_factory: Callable[[EngineSeam], Any] | None = None
    stt_factory: Callable[[EngineSeam], SttEngine] | None = None
    signals: SignalSource | None = None
    trace: Callable[[str], None] | None = None
    run_blocking: Callable[[Callable[[], Any]], Awaitable[Any]] | None = None
    judge_resolver: Resolver | None = None  # the DNS lookup of a public judge entry
    target_resolver: TargetResolver | None = None  # the DNS lookup of a named target (rule 16)


class _Ledger:
    """The usage of every answered request, in the order they were answered."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)


class _Recovery:
    """Writes ``usage-recovery-<UTC>.json`` once, only when a request was answered."""

    def __init__(
        self, out_dir: Path, created_at: datetime, ledger: _Ledger, environment: Environment
    ) -> None:
        self._out_dir = out_dir
        self._created_at = created_at
        self._ledger = ledger
        self._environment = environment
        self.written = False
        self.path: Path | None = None

    def write_once(
        self, step: str, error: BaseException, bundle: RunBundle | None, *, announce: bool = True
    ) -> Path | None:
        if self.written or not self._ledger.records:
            return self.path
        self.written = True
        self.path = cli_module._write_recovery(
            self._out_dir,
            self._created_at,
            failed_step=step,
            error=error,
            usage=self._ledger.records,
            bundle=bundle,
        )
        if announce:
            self._environment.stdout.write(
                f"usage recovery: {self.path if self.path is not None else 'not written'}\n"
            )
        return self.path


class _CountingLlm:
    """Counts the requests a brain sends (the outcome document records them)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.requests = 0
        self.streams_usage = getattr(engine, "streams_usage", False)

    def complete(self, request: LlmRequest, context: LlmContext, **kwargs: Any) -> LlmOutcome:
        self.requests += 1
        outcome: LlmOutcome = self._engine.complete(request, context, **kwargs)
        return outcome


def _read_file(path: str, role: str, cap: int = MAX_CONFIG_FILE_BYTES) -> str:
    return cli_module._read_text(path, cap, role)


def _utc_stamp(moment: datetime) -> str:
    return f"{moment:%Y%m%dT%H%M%SZ}"


def _write_bytes_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


@dataclass
class _Inputs:
    created_at: datetime
    evaluator: SimEvaluatorSpec
    persona: SimPersonaSpec
    test_profile: SimTestProfileSpec | None
    profile_file: SimProfileFile
    target: SimTargetSpec | None
    scenario: FakeAgentScenario | None
    scripted_texts: tuple[str, ...]
    export: ExportRead | None
    replay: ReplayBrain | None
    wait_ms: int
    policy: TurnTakingPolicy
    judge: judgeplan.JudgeInputs | None = None


@dataclass(frozen=True)
class _JudgeRun:
    """The judge of one run, after the checks: what step 13b needs."""

    inputs: judgeplan.JudgeInputs
    setup: JudgeSetup
    bound: BoundEndpoint | None
    reference_sha256: str | None  # printed, never stored (api-surface-batch-j2 section 2, A-J2-5)


def _engine_kind(value: str) -> str:
    return "fake" if value == "fake" or value.startswith("fake:") else value


def _pre_call_errors(function: Callable[[], T]) -> T:
    """The refusals of the steps before the call: a code, never a value (exit 4)."""
    try:
        return function()
    except Refusal:
        raise
    except (AllowlistError, TargetRefusedError, GuardError) as error:
        raise Refusal(error.code) from None
    except (InputRefusedError, ReplayDirectoryError, FakeAgentError) as error:
        raise Refusal(str(error)) from None


def _step1_parse(args: argparse.Namespace, created_at: datetime) -> _Inputs:
    evaluator = parse_sim_evaluator(_read_file(args.evaluator, "evaluator"))
    persona = parse_sim_persona(_read_file(args.persona, "persona"))
    profile = (
        None
        if args.profile is None
        else parse_sim_test_profile(_read_file(args.profile, "profile"))
    )
    profile_file = parse_sim_profile_file(_read_file(args.sim_profile, "sim profile"))
    target = None if args.target is None else parse_sim_target(_read_file(args.target, "target"))
    scenario = (
        None
        if args.agent is None
        else parse_fake_agent(_read_file(args.agent[len("fake:") :], "fake agent scenario"))
    )
    if evaluator.language != "mr" or persona.language != "mr":
        raise Refusal("language_not_supported")
    if evaluator.language != persona.language:
        raise Refusal("persona_language_differs")
    if profile_file.dialect == "smallwebrtc/1":
        raise Refusal("dialect_not_runnable")
    if profile_file.voices.get(persona.key) is None:
        raise Refusal("voice_missing_for_persona")
    voice = profile_file.voices[persona.key]
    caller = profile_file.caller
    wanted_caller = {"scripted": "scripted", "sarvam": "llm"}.get(args.caller, "replay")
    if caller["mode"] != wanted_caller or (
        caller["mode"] == "llm" and caller["provider"] != "sarvam"
    ):
        raise Refusal("engine_mismatch (caller)")
    if voice["provider"] != args.tts:
        raise Refusal("engine_mismatch (tts)")
    if profile_file.stt["provider"] != _engine_kind(args.stt):
        raise Refusal("engine_mismatch (stt)")
    if args.stt == "sarvam" and profile_file.audio["sample_rate_hz"] == 8000:
        raise Refusal("stt_sample_rate_not_accepted")
    if args.stt == "fake":
        assert scenario is not None  # noqa: S101  (the usage check guarantees --agent)
        speech_texts(scenario)
    judge_inputs: judgeplan.JudgeInputs | None = None
    if args.judge is not None:
        judge_inputs = judgeplan.parse_inputs(args, _read_file)
        bind_golden_set(
            judge_inputs.golden,
            evaluator,
            caller_mode=caller["mode"],
            barge_in_turns={b["turn_index"] for b in profile_file.barge_ins},
        )
    scripted: tuple[str, ...] = ()
    replay: ReplayBrain | None = None
    if caller["mode"] == "scripted":
        if not evaluator.scripted_turns:
            raise Refusal("scripted_turns_missing")
        scripted = tuple(resolve_profile_text(t.text, profile) for t in evaluator.scripted_turns)
        if any(len(text) > 2500 for text in scripted):
            raise Refusal("scripted_line_too_long")
    elif caller["mode"] == "replay":
        replay = ReplayBrain(
            Path(args.caller[len("fake:") :]),
            language=evaluator.language,
            max_turns=caller["max_turns"],
            max_line_chars=caller["max_line_chars"],
        )
    export: ExportRead | None = None
    if args.agent_transcript is not None:
        if args.declared_language != "mr":
            raise Refusal("language_not_supported")
        read = read_voicera_export(
            _read_file(args.agent_transcript, "transcript", MAX_TRANSCRIPT_FILE_BYTES),
            created_at=created_at,
            privacy=provisional_privacy(),
            declared_language=LanguageDeclaration(
                language="mr", source="user_declared", declared_at=created_at
            ),
            accept_warnings=False,
        )
        if read.transcript is None:
            raise Refusal("export_without_transcript")
        export = ExportRead(read.transcript, read.turns, read.warnings)
    policy = TurnTakingPolicy.from_profile(profile_file.turn_taking)
    return _Inputs(
        created_at=created_at,
        evaluator=evaluator,
        persona=persona,
        test_profile=profile,
        profile_file=profile_file,
        target=target,
        scenario=scenario,
        scripted_texts=scripted,
        export=export,
        replay=replay,
        wait_ms=policy.wait_for_agent_silence_ms,
        policy=policy,
        judge=judge_inputs,
    )


def _caps(args: argparse.Namespace) -> dict[str, int | None]:
    return {name: getattr(args, option) for option, (name, _) in CAP_OPTIONS.items()}


def _step2_allowlist(
    args: argparse.Namespace,
    inputs: _Inputs,
    resolver: TargetResolver | None = None,
) -> tuple[ConnectionLabels, dict[str, Any]]:
    """The allowlist entry of the target and the stored profile document (no key is read).

    A target that is on the list is used, connected and stored as the entry wrote it (the
    normalised form), then the addresses of its name are checked (ADR 0026 rule 16).
    """
    dialect = inputs.profile_file.dialect
    if args.agent is not None:
        labels = ConnectionLabels(None, None, False if dialect == "twilio-media-stream/1" else None)
    else:
        assert inputs.target is not None  # noqa: S101
        if args.allowlist is None:
            raise Refusal("allowlist_missing")
        parsed = parse_target_url(inputs.target.url, schemes=("ws", "wss"))
        entry = find_entry(read_allowlist(args.allowlist), inputs.target.url)
        if entry is None:
            raise Refusal("target_not_on_allowlist")
        check_target_addresses(parsed.host, parsed.port, resolver=resolver or system_resolver)
        inputs.target = dataclasses.replace(inputs.target, url=entry.target)
        labels = ConnectionLabels(
            entry.auth_token_env, entry.origin, entry.bot_holds_telephony_credentials
        )
    check_connection_labels(dialect, labels)
    document = build_sim_profile(
        inputs.profile_file,
        persona_key=inputs.persona.key,
        connection=labels.as_profile_connection(),
        caps=_caps(args),
    )
    validate_sim_profile(
        document, scripted_turn_count=len(inputs.evaluator.scripted_turns), check_caps=False
    )
    return labels, document


def _step2_guards(
    args: argparse.Namespace, inputs: _Inputs, out_dir: Path, created_at: datetime
) -> None:
    """The minimum interval since the last call (a target run only) and the kill-switch file."""
    if args.agent is None:
        check_call_interval(
            out_dir, now_utc=created_at, min_interval_ms=inputs.profile_file.min_call_interval_ms
        )
    if kill_switch_present(out_dir):
        raise Refusal("kill_switch_file_present")


def _step4_caps(args: argparse.Namespace, document: Mapping[str, Any]) -> None:
    real = {
        "max_tts_chars": args.tts != "fake",
        "max_llm_tokens": args.caller == "sarvam",
        "max_stt_seconds": args.stt == "sarvam",
    }
    for option, needed in real.items():
        if needed and getattr(args, option) is None:
            raise Refusal(f"cap_missing (--{option.replace('_', '-')})")
    if any(real.values()) and args.max_call_seconds is None:
        raise Refusal("cap_missing (--max-call-seconds)")
    validate_sim_profile(document, scripted_turn_count=None)


# ------------------------------------------------------------------------------ configuration


def _build_configuration(
    inputs: _Inputs,
    document: Mapping[str, Any],
    created_at: datetime,
    setup: JudgeSetup | None = None,
) -> SimulatedConfiguration:
    try:
        return build_simulated_configuration(
            evaluator=inputs.evaluator,
            persona=inputs.persona,
            test_profile=inputs.test_profile,
            sim_profile=document,
            flag_min_confidence=inputs.profile_file.flag_min_confidence,
            normaliser=inputs.profile_file.normaliser,
            created_at=created_at,
            judge=setup,
        )
    except (ValueError, TypeError) as error:
        if isinstance(error, InputRefusedError):
            raise Refusal(str(error)) from None
        raise Refusal(f"input_record_invalid ({type(error).__name__})") from None


def _voice(document: Mapping[str, Any]) -> TtsVoice:
    v = document["voice"]
    return TtsVoice(
        provider=v["provider"],
        model=v["model"],
        speaker=v["speaker"],
        language_code=v["language_code"],
        pace=v["pace"],
    )


def _dialect(document: Mapping[str, Any]) -> Dialect:
    if document["dialect"] == "pipecat-protobuf/1":
        return PipecatProtobufDialect(sample_rate_hz=document["audio"]["sample_rate_hz"])
    return TwilioMediaStreamDialect(counter=1)


def _planned_lines(args: argparse.Namespace, inputs: _Inputs) -> tuple[list[str], CallerEnd | None]:
    """The lines a scripted or replay caller will say, and what it says after them."""
    if inputs.profile_file.caller["mode"] == "scripted":
        return list(inputs.scripted_texts), CallerEnd("script_complete")
    if inputs.replay is None:
        return [], None
    lines: list[str] = []
    index = 0
    while True:
        decision = inputs.replay.next_line(index, None)
        if decision.line is not None:
            lines.append(decision.line)
            index += 1
            continue
        assert decision.end_reason is not None  # noqa: S101
        return lines, CallerEnd(
            decision.end_reason, decision.invalid_outputs, decision.regenerations
        )


# ---------------------------------------------------------------------------------- dry run


def _dry_run(args: argparse.Namespace, inputs: _Inputs, environment: Environment) -> int:
    profile = inputs.profile_file
    voice = profile.voices[inputs.persona.key]
    caller = profile.caller
    planned, _ = _planned_lines(args, inputs)
    out = [f"dialect: {profile.dialect}"]
    if caller["mode"] == "llm":
        requests = 2 * caller["max_turns"]
        out.append(
            f"caller: llm ({caller['provider']} {caller['model']}, up to {caller['max_turns']} "
            "turns, one regeneration at most each)"
        )
        out.append("tts: lines are synthesised during the call, one request each")
        out.append(f"llm: up to {requests} requests of at most {caller['max_tokens']} tokens each")
        out.append("stt: one request for each finished agent segment, during the call")
    else:
        out.append(f"caller: {caller['mode']} ({len(planned)} lines)")
        chars = sum(len(text) for text in planned)
        hits = 0
        if args.tts == "sarvam":
            cache = Path(args.out) / TTS_CACHE_DIR_NAME
            codec = "mulaw" if profile.audio["wire_codec"] == "mulaw" else "linear16"
            for text in planned:
                key = tts_cache_key(
                    provider=voice["provider"],
                    model=voice["model"],
                    speaker=voice["speaker"],
                    language_code=voice["language_code"],
                    sample_rate_hz=voice["sample_rate_hz"],
                    codec=codec,
                    pace=voice["pace"],
                    text=text,
                )
                hits += 1 if (cache / key).is_file() else 0
        out.append(
            f"tts: {args.tts}, {chars} characters to synthesise in {len(planned) - hits} "
            f"requests, {hits} cache hits"
        )
        out.append("llm: none")
        out.append(f"stt: {_engine_kind(args.stt)}, planned after the call from the agent audio")
    for label, value in (
        ("max call seconds", args.max_call_seconds),
        ("max tts characters", args.max_tts_chars),
        ("max llm tokens", args.max_llm_tokens),
        ("max stt seconds", args.max_stt_seconds),
    ):
        out.append(f"{label}: {'not set' if value is None else value}")
    if inputs.judge is not None:
        golden = inputs.judge.golden
        modes: dict[str, int] = {}
        for item in golden.items:
            modes[item.match_mode] = modes.get(item.match_mode, 0) + 1
        out.extend(
            plan_lines(
                engine=inputs.judge.engine,
                model=inputs.judge.config.params.model,
                golden_key=golden.key,
                modes=modes,
                max_tokens=inputs.judge.config.params.max_tokens,
                max_requests=args.max_judge_requests,
                max_tokens_total=args.max_judge_tokens,
            )
        )
    out.append("dry run: nothing was sent")
    environment.stdout.write("".join(f"{line}\n" for line in out))
    return 0


# ------------------------------------------------------------------------- transcription


class _Transcriber:
    """Speech-to-text of the agent channel, in the call (LLM caller) and after it."""

    def __init__(
        self,
        engine: SttEngine,
        ledger: _Ledger,
        *,
        fmt: RecorderFormat,
        wait_ms: int,
        max_chunk_ms: int,
        run_id: RunId,
        artifact_id: ArtifactId,
        created_at: datetime,
        run_blocking: Callable[[Callable[[], Any]], Awaitable[Any]],
    ) -> None:
        self.engine = engine
        self.ledger = ledger
        self.fmt = fmt
        self.wait_ms = wait_ms
        self.max_chunk_ms = max_chunk_ms
        self.run_id = run_id
        self.artifact_id = artifact_id
        self.created_at = created_at
        self.run_blocking = run_blocking
        self.done: dict[tuple[int, int], SttOutcome] = {}
        self.failure: SttError | None = None

    def context(self) -> SttContext:
        return SttContext(
            run_id=self.run_id,
            audio_artifact_id=self.artifact_id,
            created_at=self.created_at,
            privacy=provisional_privacy(),
            configured_language="mr",
            role="agent",
        )

    def plan_of(self, caller: Sequence[LogFrame], agent: Sequence[LogFrame], origin: int) -> Any:
        recording = build_recording(self.fmt, origin, caller, agent)
        channel = select_stt_channel(_channel_map(self.artifact_id)).index
        return activity_chunk_plan(
            recording.wav[44:],
            sample_rate_hz=self.fmt.sample_rate_hz,
            channels=2,
            channel=channel,
            wait_for_agent_silence_ms=self.wait_ms,
            max_chunk_ms=self.max_chunk_ms,
            search_ms=min(_MIN_SEARCH_MS, self.max_chunk_ms),
        )

    def _send(self, plan: Any) -> SttOutcome:
        """One request series; the usage of every answered chunk reaches the ledger once."""
        context = self.context()
        if getattr(self.engine, "streams_usage", False) is True:
            streamed: SttOutcome = self.engine.transcribe(  # type: ignore[call-arg]
                plan, context, on_usage=self.ledger.add
            )
            return streamed
        outcome = self.engine.transcribe(plan, context)
        for record in outcome.usage:
            self.ledger.add(record)
        return outcome

    def _one(self, plan: Any, index: int) -> SttOutcome:
        return self._send(single_chunk_plan(plan, index))

    async def in_call(
        self, caller: tuple[LogFrame, ...], agent: tuple[LogFrame, ...], origin: int
    ) -> str | None:
        """The text of the chunks the agent finished since the last call (LLM caller)."""
        plan = self.plan_of(caller, agent, origin)
        texts: list[str] = []
        for index, chunk in enumerate(plan.chunks):
            key = (chunk.start_frame, chunk.end_frame)
            if key in self.done:
                continue
            outcome = await self.run_blocking(partial(self._one, plan, index))
            if outcome.failure is not None:
                self.failure = outcome.failure
                raise outcome.failure
            self.done[key] = outcome
            texts.extend(turn.text for turn in outcome.turns)
        return " ".join(texts)

    def post_call(self, plan: Any) -> SttOutcome:
        """The transcript of the whole plan, sending only what was not already transcribed."""
        if not plan.chunks:
            raise ValueError("no agent audio to transcribe")
        if not self.done:
            return self._send(plan)
        parts: list[SttOutcome] = []
        for index, chunk in enumerate(plan.chunks):
            part = self.done.get((chunk.start_frame, chunk.end_frame))
            if part is None:
                part = self._one(plan, index)
                if part.failure is not None:
                    return part
            parts.append(part)
        return assemble_stt_outcome(parts, plan=plan, context=self.context())


def _channel_map(artifact_id: ArtifactId) -> Any:
    from tracer_evalcore.simbundle import CHANNEL_MAP_METHOD
    from tracer_evalschema.artifact import ChannelAssignment, ChannelMap

    return ChannelMap(
        artifact_id=artifact_id,
        channels=[
            ChannelAssignment(index=0, role="caller"),
            ChannelAssignment(index=1, role="agent"),
        ],
        determination="declared",
        method=CHANNEL_MAP_METHOD,
    )


# ------------------------------------------------------------------------------ the command


def _usage_total(records: Sequence[UsageRecord], stage: str, unit: str) -> int:
    total = 0
    for record in records:
        if record.stage != stage:
            continue
        for quantity in record.quantities:
            if quantity.unit == unit:
                total += int(quantity.count)
    return total


def run_simulate(args: argparse.Namespace, environment: Environment) -> int:
    world: SimWorld = environment.sim if environment.sim is not None else SimWorld()
    trace = world.trace or (lambda _step: None)
    moment = environment.clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("the clock must return an aware datetime")
    created_at = moment.astimezone(UTC)
    out_dir = Path(args.out)

    trace("parse_inputs")
    inputs = _pre_call_errors(lambda: _step1_parse(args, created_at))
    if args.dry_run:
        return _dry_run(args, inputs, environment)
    trace("allowlist")
    labels, document = _pre_call_errors(
        lambda: _step2_allowlist(args, inputs, world.target_resolver)
    )
    _pre_call_errors(lambda: _step2_guards(args, inputs, out_dir, created_at))
    entry: JudgeEndpoint | None = None
    if inputs.judge is not None:
        entry = _pre_call_errors(
            lambda: judgeplan.check_allowlist(
                args,
                inputs.judge,  # type: ignore[arg-type]
                resolver=world.judge_resolver,
                auth_token_env=labels.auth_token_env,
            )
        )
    trace("authorisation")
    if args.authorisation_reference is None:
        raise Refusal("authorisation_reference_missing")
    reference_hash = authorisation_reference_sha256(args.authorisation_reference)
    judge_reference_hash: str | None = None
    if inputs.judge is not None:
        reference = _pre_call_errors(lambda: judgeplan.check_reference(args, inputs.judge))  # type: ignore[arg-type]
        judge_reference_hash = (
            None if reference is None else authorisation_reference_sha256(reference)
        )
    trace("caps")
    _pre_call_errors(lambda: _step4_caps(args, document))
    if inputs.judge is not None:
        _pre_call_errors(lambda: judgeplan.check_caps(args, inputs.judge))  # type: ignore[arg-type]
    trace("output_probe")
    if "?" in str(out_dir.resolve()):
        raise Refusal("output_path_invalid: the output path must not contain a question mark")
    if os.path.lexists(out_dir) and not out_dir.is_dir():
        raise Refusal("the output path exists and is not a directory")
    cli_module._check_output_location(out_dir)
    stamp = _utc_stamp(created_at)
    report_path = out_dir / f"report-{stamp}.html"
    recording_path = out_dir / RECORDING_NAME.format(stamp=stamp)
    for existing in (report_path, recording_path):
        if os.path.lexists(existing):
            raise Refusal("report_exists: a file with this name is already in the output directory")
    setup: JudgeSetup | None = None
    if inputs.judge is not None:
        setup = _pre_call_errors(lambda: judgeplan.build_setup(args, inputs.judge, entry))  # type: ignore[arg-type]
    configuration = _build_configuration(inputs, document, created_at, setup)

    trace("keys_and_call_log")
    token: BearerToken | None = None
    if args.agent is None:
        token = _pre_call_errors(lambda: read_bearer_token(labels, environ=environment.environ))
    for role, real in (
        ("tts", args.tts == "sarvam"),
        ("llm", args.caller == "sarvam"),
        ("stt", args.stt == "sarvam"),
    ):
        if real:
            try:
                load_role_key(
                    role,
                    environ=environment.environ,
                    env_file=None if args.env_file is None else Path(args.env_file),
                )
            except (ValueError, SttError) as error:
                raise Refusal(str(getattr(error, "reason", None) or type(error).__name__)) from None
    judge: _JudgeRun | None = None
    if inputs.judge is not None and setup is not None:
        bound = judgeplan.bind_key(
            entry,
            environ=environment.environ,
            env_file=None if args.env_file is None else Path(args.env_file),
        )
        judge = _JudgeRun(inputs.judge, setup, bound, judge_reference_hash)
    ledger = _Ledger()
    recovery = _Recovery(out_dir, created_at, ledger, environment)
    try:
        call_log = SimCallLog.open(out_dir, clock=environment.clock)
    except OSError as error:
        raise Refusal(f"output_unavailable ({type(error).__name__})") from None
    engines: list[Any] = []
    try:
        return _after_keys(
            args,
            environment,
            world,
            trace,
            inputs=inputs,
            labels=labels,
            token=token,
            document=document,
            configuration=configuration,
            reference_hash=reference_hash,
            created_at=created_at,
            report_path=report_path,
            recording_path=recording_path,
            ledger=ledger,
            call_log=call_log,
            engines=engines,
            recovery=recovery,
            judge=judge,
        )
    except BaseException as error:
        recovery.write_once("call", error, None)
        raise
    finally:
        for engine in engines:
            close = getattr(engine, "close", None)
            if callable(close):
                close()
        call_log.close()


def _after_keys(
    args: argparse.Namespace,
    environment: Environment,
    world: SimWorld,
    trace: Callable[[str], None],
    *,
    inputs: _Inputs,
    labels: ConnectionLabels,
    token: BearerToken | None,
    document: Mapping[str, Any],
    configuration: SimulatedConfiguration,
    reference_hash: str,
    created_at: datetime,
    report_path: Path,
    recording_path: Path,
    ledger: _Ledger,
    call_log: SimCallLog,
    engines: list[Any],
    recovery: _Recovery,
    judge: _JudgeRun | None = None,
) -> int:
    out_dir = Path(args.out)
    env_file = None if args.env_file is None else Path(args.env_file)
    clock: Clock = world.clock or SystemClock()
    run_blocking = world.run_blocking or run_in_thread
    profile = inputs.profile_file
    dialect = _dialect(document)
    audio = document["audio"]
    fmt = RecorderFormat(
        audio["sample_rate_hz"], audio["wire_codec"].replace("webrtc_negotiated", "pcm_s16le")
    )
    run_id = RunId.new()
    artifact_id = ArtifactId.new()
    voice = _voice(document)
    voice_codec = document["voice"]["codec"]
    caps = _caps(args)

    def seam(
        role: str, cap: int | None, model: str | None, cache: TtsCache | None = None
    ) -> EngineSeam:
        return EngineSeam(
            role=role,
            call_log=call_log,
            out_dir=out_dir,
            environ=environment.environ,
            env_file=env_file,
            transport=environment.transport,
            sleep=environment.sleep,
            cap=cap,
            model=model,
            cache=cache,
        )

    # step 6: the engines (the keys were read and the call log opened by the caller)
    fake_tts: FakeTts | None = None
    if args.tts == "fake":
        fake_tts = FakeTts()
        tts_engine: TtsEngine = fake_tts
    else:
        cache = TtsCache.open(out_dir)
        tts_seam = seam("tts", caps["tts_chars"], document["voice"]["model"], cache)
        tts_engine = (
            world.tts_factory(tts_seam)
            if world.tts_factory is not None
            else create_sarvam_tts(
                call_log=call_log,
                cap_characters=caps["tts_chars"],
                environ=environment.environ,
                env_file=env_file,
                cache=cache,
                transport=environment.transport,
                sleep=environment.sleep,
            )
        )
    engines.append(tts_engine)
    if args.stt == "fake":
        assert inputs.scenario is not None  # noqa: S101
        base_stt: SttEngine = ScenarioStt(
            speech_texts(inputs.scenario), timed=inputs.judge is not None
        )
    elif args.stt.startswith("fake:"):
        base_stt = ReplayStt(Path(args.stt[len("fake:") :]))
    else:
        stt_seam = seam("stt", caps["stt_seconds"], profile.stt["model"])
        base_stt = (
            world.stt_factory(stt_seam)
            if world.stt_factory is not None
            else create_sarvam_stt_for_run(
                call_log=call_log,
                model=profile.stt["model"],
                environ=environment.environ,
                env_file=env_file,
                transport=environment.transport,
                sleep=environment.sleep,
            )
        )
    engines.append(base_stt)
    stt_engine = CappedStt(base_stt, cap_seconds=caps["stt_seconds"])
    counting: _CountingLlm | None = None
    brain: CallerBrain | None = None
    if args.caller == "sarvam":
        llm_seam = seam("llm", caps["llm_tokens"], profile.caller["model"])
        llm_engine = (
            world.llm_factory(llm_seam)
            if world.llm_factory is not None
            else create_sarvam_llm(
                call_log=call_log,
                cap_tokens=caps["llm_tokens"],
                environ=environment.environ,
                env_file=env_file,
                transport=environment.transport,
                sleep=environment.sleep,
            )
        )
        engines.append(llm_engine)
        counting = _CountingLlm(llm_engine)
        caller = profile.caller
        brain = LlmBrain(
            engine=counting,
            context=LlmContext(run_id=run_id, created_at=created_at),
            on_usage=ledger.add,
            provider=caller["provider"],
            model=caller["model"],
            max_tokens=caller["max_tokens"],
            seed=caller["seed"],
            reasoning_effort=caller["reasoning_effort"],
            max_line_chars=caller["max_line_chars"],
            max_turns=caller["max_turns"],
            template_id=caller["prompt"]["template_id"],
            agent_text_max_chars=caller["prompt"]["agent_text_max_chars"],
            inputs=prompt_inputs(
                configuration.snapshot.evaluator,
                configuration.snapshot.persona,
                configuration.test_profile,
            ),
        )

    def synthesise(text: str, index: int) -> TtsOutcome:
        request = TtsRequest(
            index=index,
            text=text,
            voice=voice,
            sample_rate_hz=document["voice"]["sample_rate_hz"],
            codec=voice_codec,
        )
        context = TtsContext(run_id=run_id, created_at=created_at)
        if getattr(tts_engine, "streams_usage", False) is True:
            outcome: TtsOutcome = tts_engine.synthesise(request, context, on_usage=ledger.add)  # type: ignore[call-arg]
            return outcome
        outcome = tts_engine.synthesise(request, context)
        for record in outcome.usage:
            ledger.add(record)
        return outcome

    # step 7: pre-synthesis of every line the scripted or replayed caller will say
    trace("presynthesis")
    cache_hits = 0
    if brain is not None:
        lines: Any = LlmLines(
            brain,
            synthesise,
            clock=clock,
            sample_rate_hz=audio["sample_rate_hz"],
            wire_codec=fmt.codec,
            run_blocking=run_blocking,
            requests_of=lambda: counting.requests if counting is not None else 0,
        )
    else:
        texts, final = _planned_lines(args, inputs)
        assert final is not None  # noqa: S101
        entries: list[SynthesisedLine] = []
        for index, text in enumerate(texts):
            try:
                outcome = synthesise(text, index)
                if outcome.failure is not None:
                    raise outcome.failure
            except TtsCapReachedError:
                raise Refusal("spend_cap_reached_before_the_call (tts_chars)") from None
            except TtsError as error:
                recovery.write_once("presynthesis", error, None)
                raise ProviderFailure(_tts_failure_line(error)) from None
            assert outcome.audio is not None  # noqa: S101
            if not outcome.usage and args.tts == "sarvam":
                cache_hits += 1
            entries.append(SynthesisedLine(text, outcome.audio))
        lines = PresynthesisedLines(
            inputs.profile_file.caller["mode"],
            entries,
            final,
            sample_rate_hz=audio["sample_rate_hz"],
            wire_codec=fmt.codec,
        )

    # step 8: the store
    trace("open_store")
    try:
        database = store.open_sqlite_store(out_dir)
    except Exception as error:
        raise Refusal(f"output_unavailable ({type(error).__name__})") from None
    try:
        return _call_and_save(
            args,
            environment,
            world,
            trace,
            inputs=inputs,
            labels=labels,
            token=token,
            document=document,
            configuration=configuration,
            reference_hash=reference_hash,
            created_at=created_at,
            report_path=report_path,
            recording_path=recording_path,
            ledger=ledger,
            call_log=call_log,
            database=database,
            clock=clock,
            run_blocking=run_blocking,
            dialect=dialect,
            fmt=fmt,
            run_id=run_id,
            artifact_id=artifact_id,
            lines=lines,
            stt_engine=stt_engine,
            counting=counting,
            cache_hits=cache_hits,
            recovery=recovery,
            judge=judge,
            engines=engines,
        )
    finally:
        database.dispose()


def _tts_failure_line(error: TtsError) -> str:
    """Class name, closed reason key and HTTP status only: never provider text, never a key."""
    status = None if error.status in (None, 200) else f"HTTP {error.status}"  # 200: shape refused
    facts = [x for x in (error.reason, status) if x]
    suffix = f": {', '.join(facts)}" if facts else ""
    return f"text-to-speech failed before the call ({type(error).__name__}{suffix})"


async def _default_connect(request: ConnectRequest) -> WireConnection:
    return await open_websocket(
        WebSocketTarget(request.url, request.labels),
        request.dialect,
        token=request.token,
        open_timeout_s=CONNECT_TIMEOUT_S,
        close_timeout_s=CLOSE_TIMEOUT_S,
    )


def _run_default(factory: Callable[[], Awaitable[Any]]) -> Any:
    async def drive() -> Any:
        return await factory()

    return asyncio.run(drive())


def _execute(
    world: SimWorld,
    settings: RunnerSettings,
    *,
    clock: Clock,
    connector: Callable[[], Awaitable[WireConnection]],
    lines: Any,
    segments: Any,
    kill_switch: Callable[[], bool] | None = None,
) -> CallResult:
    aborted = [False]

    async def main() -> CallResult:
        stop = StopFlag()
        task = asyncio.current_task()

        def on_signal() -> None:
            stop.request()
            if stop.signals >= 2 and task is not None:
                aborted[0] = True
                task.cancel()

        with (world.signals or SystemSignals()).install(on_signal):
            return await run_call(
                settings,
                clock=clock,
                connect=connector,
                lines=lines,
                stop=stop,
                segments=segments,
                kill_switch=kill_switch,
            )

    run_async = world.run_async or _run_default
    try:
        result: CallResult = run_async(main)
    except asyncio.CancelledError:
        if aborted[0]:
            raise KeyboardInterrupt from None
        raise
    return result


def _judge_step(
    args: argparse.Namespace,
    environment: Environment,
    judge: _JudgeRun,
    bundle: RunBundle,
    *,
    ledger: _Ledger,
    call_log: SimCallLog,
    recovery: _Recovery,
    engines: list[Any],
    call_end_ms: int,
    run_id: RunId,
    created_at: datetime,
) -> RunBundle:
    """Step 13b: the items in golden order, one request in flight. The usage of every answered
    request reaches the ledger before the next request, so an interrupt keeps it."""
    from tracer_evalcore.judgerecords import judge_applicable_items

    try:
        exchanges = None
        if judge.inputs.kind == "profile" and judge_applicable_items(
            snapshot=bundle.snapshot,
            run=bundle.run,
            transcripts=bundle.transcripts,
            turns=bundle.turns,
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                exchanges = ExchangeStore(exchange_directory(Path(args.out), run_id))
            for item in caught:
                if issubclass(item.category, JudgeExchangeModeWarning):
                    environment.stderr.write(f"warning: {item.message}\n")
        engine = judgeplan.build_engine(
            args,
            judge.inputs,
            judge.bound,
            call_log=call_log,
            exchanges=exchanges,
            transport=environment.transport,
            sleep=environment.sleep,
        )
        engines.append(engine)
        done = judge_bundle(
            bundle=bundle,
            setup=judge.setup,
            engine=engine,
            params=judge.inputs.config.params,
            context=JudgeContext(run_id=run_id, created_at=created_at),
            call_end_ms=call_end_ms,
            on_usage=ledger.add,
            created_at=created_at,
        )
    except BaseException as error:
        recovery.write_once("judge", error, None)
        raise
    cost = compute_usage_cost(ledger.records, catalog=None, created_at=created_at)
    updated = bundle.model_copy(
        update={"cost_records": list(cost.records), "cost_summaries": [cost.summary]}
    )
    return with_judge_results(updated, done.results, done.usage)


def _call_and_save(
    args: argparse.Namespace,
    environment: Environment,
    world: SimWorld,
    trace: Callable[[str], None],
    *,
    inputs: _Inputs,
    labels: ConnectionLabels,
    token: BearerToken | None,
    document: Mapping[str, Any],
    configuration: SimulatedConfiguration,
    reference_hash: str,
    created_at: datetime,
    report_path: Path,
    recording_path: Path,
    ledger: _Ledger,
    call_log: SimCallLog,
    database: Any,
    clock: Clock,
    run_blocking: Callable[[Callable[[], Any]], Awaitable[Any]],
    dialect: Dialect,
    fmt: RecorderFormat,
    run_id: RunId,
    artifact_id: ArtifactId,
    lines: Any,
    stt_engine: SttEngine,
    counting: _CountingLlm | None,
    cache_hits: int,
    recovery: _Recovery,
    judge: _JudgeRun | None = None,
    engines: list[Any] | None = None,
) -> int:
    out_dir = Path(args.out)
    profile = inputs.profile_file
    frame_ns = 20_000_000
    ms = 1_000_000
    transcriber = _Transcriber(
        stt_engine,
        ledger,
        fmt=fmt,
        wait_ms=inputs.wait_ms,
        max_chunk_ms=profile.stt["max_chunk_ms"],
        run_id=run_id,
        artifact_id=artifact_id,
        created_at=created_at,
        run_blocking=run_blocking,
    )
    settings = RunnerSettings(
        dialect=dialect,
        policy=inputs.policy,
        max_duration_ms=effective_max_duration_ms(
            inputs.evaluator.max_duration_ms, args.max_call_seconds
        ),
        barge_ins={b["turn_index"]: b["barge_in_after_ms"] for b in document["barge_ins"]},
    )
    connected_at: list[datetime] = []
    if args.agent is not None:
        assert inputs.scenario is not None  # noqa: S101
        base_connect = connect_fake_agent(inputs.scenario, dialect, clock)
    else:
        assert inputs.target is not None  # noqa: S101
        url = inputs.target.url

        async def base_connect() -> WireConnection:
            request = ConnectRequest(url=url, dialect=dialect, labels=labels, token=token)
            return await (world.connect or _default_connect)(request)

    async def connector() -> WireConnection:
        trace("connect")
        connection = await base_connect()
        connected_at.append(environment.clock())
        call_log.connect(dialect=dialect.dialect_id)
        trace("call_loop")
        return connection

    claim: CallLock | None = None
    if args.agent is None:
        # the instants of the connect, claimed for the call (concurrency 1, ADR 0026 rule 18)
        claim = _pre_call_errors(
            lambda: CallLock.claim(
                out_dir,
                now_utc=environment.clock().astimezone(UTC),
                monotonic_ns=clock.monotonic_ns(),
                min_interval_ms=profile.min_call_interval_ms,
            )
        )
    try:
        result = _execute(
            world,
            settings,
            clock=clock,
            connector=connector,
            lines=lines,
            segments=transcriber.in_call if lines.needs_agent_text else None,
            kill_switch=lambda: kill_switch_present(out_dir),
        )
    finally:
        if claim is not None:
            claim.close()
    trace("hang_up")
    established = result.call_established
    origin = result.connect_ended_ns or 0

    # the recording is written at once: a killed or failed later step never loses it
    recording_facts: RecordingFacts | None = None
    if established:
        recording = build_recording(fmt, origin, result.caller_frames, result.agent_frames)
        _write_bytes_new(recording_path, recording.wav)
        recording_facts = RecordingFacts(
            artifact_id,
            recording_path.resolve().as_uri(),
            hashlib.sha256(recording.wav).hexdigest(),
            len(recording.wav),
            fmt.sample_rate_hz,
        )
    kill_before_connect = not established and result.end_reason == "kill_switch"
    last_edges = [t.last_frame_ns + frame_ns for t in result.turns]
    last_edges += [f.t_ns for f in result.agent_frames]
    observed = max([result.observed_until_ns, *last_edges])
    timing = extract_timing(
        dialect=dialect.dialect_id,
        fmt=fmt,
        wait_for_agent_silence_ms=inputs.wait_ms,
        connect_started_ns=None if kill_before_connect else result.connect_started_ns,
        connect_ended_ns=None if kill_before_connect else result.connect_ended_ns,
        established=established,
        observed_until_ns=observed,
        caller_turns=[
            CallerTurn(
                t.turn_seq, t.first_frame_ns, t.last_frame_ns, t.barge_in_after_ms, t.self_times
            )
            for t in result.turns
        ],
        agent_frames=result.agent_frames if established else (),
    )

    # step 12: the speech-to-text of what the call loop did not already transcribe
    stt_outcome: SttOutcome | None = None
    warnings: list[IngestWarning] = []
    agent_segments = 0
    if established:
        trace("post_call_stt")
        plan = transcriber.plan_of(result.caller_frames, result.agent_frames, origin)
        agent_segments = len(plan.chunks)
        if plan.chunks:
            failure: SttError | None = transcriber.failure
            if failure is None:
                try:
                    outcome = transcriber.post_call(plan)
                except SttError as error:
                    failure = error
                else:
                    failure = outcome.failure
                    stt_outcome = outcome if failure is None else None
            if failure is not None:
                warnings.append(
                    IngestWarning(
                        code="stt_failed",
                        path="/stt",
                        message=str(failure) or type(failure).__name__,
                    )
                )
    counts = {
        "caller_turns_started": result.counts["caller_turns_started"],
        "caller_turns_completed": result.counts["caller_turns_completed"],
        "agent_segments": agent_segments,
        "barge_ins_fired": result.counts["barge_ins_fired"],
        "brain_requests": result.counts["brain_requests"],
        "brain_invalid_outputs": result.counts["brain_invalid_outputs"],
        "brain_regenerations": result.counts["brain_regenerations"],
        "tts_requests": _usage_total(ledger.records, "sim_caller_tts", "requests"),
        "tts_cache_hits": cache_hits,
        "llm_requests": 0 if counting is None else counting.requests,
        "stt_requests": _usage_total(ledger.records, "stt", "requests"),
        "frames_sent": result.counts["frames_sent"],
        "frames_received": result.counts["frames_received"],
        "inbound_messages_ignored": result.counts["inbound_messages_ignored"],
        "signals_received": result.counts["signals_received"],
    }
    call_log.end(end_reason=result.end_reason, counts=counts)
    mode = profile.caller["mode"]
    outcome_document = build_sim_outcome(
        dialect=dialect.dialect_id,
        call_established=established,
        end_reason=result.end_reason,
        cap_reached=result.cap_reached,
        connected_ms=(observed - origin) // ms if established else None,
        authorisation_reference_sha256=reference_hash,
        caller_text_origin=mode,
        counts=counts,
        brain_mode=mode,
    )
    language = document["voice"]["language_code"]
    caller_lines = tuple(
        CallerLineFact(
            seq=t.turn_seq,
            text=t.text,
            language=language,
            start_ms=(t.first_frame_ns - origin) // ms,
            end_ms=(t.last_frame_ns + frame_ns - origin) // ms,
            complete=t.complete,
        )
        for t in result.turns
    )
    cost = compute_usage_cost(ledger.records, catalog=None, created_at=created_at)
    costs = CostParts(catalogs=(), records=tuple(cost.records), summaries=(cost.summary,))
    models = {document["voice"]["model"]} if args.tts == "sarvam" else set()
    if args.caller == "sarvam":
        models.add(profile.caller["model"])
    if args.stt == "sarvam":
        models.add(profile.stt["model"])
    project = simulated_project(
        name="simulated-call evaluation",
        created_at=created_at,
        provider_configs=[provider_config_ref(model) for model in sorted(models)],
    )
    if inputs.target is not None:
        target = simulated_agent_target(
            name=inputs.target.name, transport="ws", url=inputs.target.url, created_at=created_at
        )
    else:
        target = simulated_agent_target(
            name="fake agent",
            transport="ws",
            url="wss://fake-agent.invalid/",
            created_at=created_at,
        )
    trace("scoring")
    bundle = build_simulated_bundle(
        configuration=configuration,
        project=project,
        agent_target=target,
        call=SimulatedCall(
            run_id=run_id,
            established=established,
            started_at=connected_at[0] if (established and connected_at) else None,
            recording=recording_facts,
            caller_lines=caller_lines,
            caller_text_origin=mode,
            timing=timing,
            outcome=outcome_document,
            usage=tuple(ledger.records),
            warnings=(),
        ),
        stt=stt_outcome,
        export=inputs.export,
        stt_warnings=warnings,
        costs=costs,
        created_at=created_at,
    )
    if judge is not None:
        trace("judge")
        bundle = _judge_step(
            args,
            environment,
            judge,
            bundle,
            ledger=ledger,
            call_log=call_log,
            recovery=recovery,
            engines=engines if engines is not None else [],
            call_end_ms=outcome_document["connected_ms"] or 0,
            run_id=run_id,
            created_at=created_at,
        )

    failures: list[Exception] = []
    stored = None
    trace("store_write")
    try:
        stored = store.store_run(database, project=project, agent_target=target, bundle=bundle)
    except Exception as error:
        failures.append(error)
    except BaseException as error:
        recovery.write_once("store", error, bundle)
        raise
    saved: Path | None = None
    if stored is None:
        saved = recovery.write_once("store", failures[0], bundle, announce=False)

    trace("report")
    wrote_report = False
    try:
        source = (
            RelativeAudio(relative_audio_src(recording_path.resolve(), out_dir.resolve()))
            if established
            else None
        )
        cli_module._write_new_file(report_path, render_sim_report(bundle, audio=source))
        wrote_report = True
    except Exception as error:
        failures.append(error)

    environment.stdout.write(
        "".join(f"{label}: {value}\n" for label, value in simsummary.sim_summary_lines(bundle))
    )
    if result.end_reason == "brain_failed":
        status = result.failure_http_status
        environment.stdout.write(
            f"brain failure: {result.failure_code}"
            + ("" if status is None else f" (HTTP {status})")
            + "\n"
        )
    environment.stdout.write(f"bundle: {bundle.id}\n")
    if judge is not None and judge.reference_sha256 is not None:
        environment.stdout.write(
            f"judge authorisation reference: sha256 {judge.reference_sha256}\n"
        )
    environment.stdout.write(
        f"store: {out_dir / store.STORE_FILE_NAME if stored is not None else 'not written'}\n"
    )
    environment.stdout.write(
        f"recording: {recording_path if established else 'none (the call was not established)'}\n"
    )
    environment.stdout.write(f"report: {report_path if wrote_report else 'not written'}\n")
    if stored is None:
        environment.stdout.write(
            f"usage recovery: {saved if saved is not None else 'not written'}\n"
        )
    if failures:
        environment.stderr.write(f"error: unexpected failure ({type(failures[0]).__name__})\n")
        return EXIT_UNEXPECTED
    return simsummary.exit_code_of(bundle)
