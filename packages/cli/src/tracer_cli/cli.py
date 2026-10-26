# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The ``tracer`` command line: ``tracer score``.

``run(argv, environment)`` is the command with its world injected (streams, process environment,
clock, sleep, transport, engine factory); ``main(argv)`` is the console script and wraps it with
``Environment.system()``, the one place that reads the real environment and the real clock.

Every privacy and language input is required and has no default. The key is never an option: it
is read by the Sarvam adapter from the environment or from ``--env-file``, and is never printed.
Refusals are one ASCII line made of codes, sizes and exception class names, never of a value from
a file.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO

from tracer_cli import report, store, summary
from tracer_cli.replay import ReplayStt
from tracer_evalcore import definitions, pipeline
from tracer_evalcore.audio.chunker import (
    DEFAULT_MAX_CHUNK_MS,
    DEFAULT_SEARCH_MS,
    ChunkPlan,
    chunk_wav,
)
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.importers.voicera import read_voicera_export
from tracer_evalcore.importers.wav import WavAudio, read_wav
from tracer_evalcore.rubricfile import parse_rubric_file
from tracer_evalcore.stt import SttConfigurationError, SttEngine
from tracer_evalproviders.cost import CatalogRefusedError, compute_stt_cost, load_user_catalog
from tracer_evalproviders.dryrun import build_dry_run_plan
from tracer_evalproviders.sarvam.client import MAX_ATTEMPTS
from tracer_evalproviders.sarvam.engine import create_sarvam_stt
from tracer_evalproviders.sarvam.models import DEFAULT_MODEL
from tracer_evalproviders.secrets import provider_config_ref
from tracer_evalschema.pricing import PricingCatalog
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy
from tracer_evalschema.transcript import LanguageDeclaration
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "EXIT_CODES",
    "EXIT_NOT_SCORED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_STT_FAILED",
    "EXIT_UNEXPECTED",
    "EXIT_USAGE",
    "MAX_CONFIG_FILE_BYTES",
    "MAX_TRANSCRIPT_FILE_BYTES",
    "EngineRequest",
    "Environment",
    "build_parser",
    "default_engine_factory",
    "main",
    "run",
]

EXIT_OK: Final = pipeline.EXIT_SCORED
EXIT_UNEXPECTED: Final = 1
EXIT_NOT_SCORED: Final = pipeline.EXIT_NOT_SCORED
EXIT_STT_FAILED: Final = pipeline.EXIT_STT_FAILED
EXIT_REFUSED: Final = 4
EXIT_USAGE: Final = 64
EXIT_CODES: Final = {
    EXIT_OK: "scored",
    EXIT_UNEXPECTED: "unexpected failure",
    EXIT_NOT_SCORED: "not scored",
    EXIT_STT_FAILED: "speech-to-text failed",
    EXIT_REFUSED: "input refused",
    EXIT_USAGE: "usage",
}
MAX_CONFIG_FILE_BYTES: int = 1_048_576
MAX_TRANSCRIPT_FILE_BYTES: int = 67_108_864

_SUPPORTED_LANGUAGE: Final = "mr"
_REPLAY_PREFIX: Final = "fake:"
_PROJECT_NAME: Final = "recorded-call scoring"
_TARGET_NAME: Final = "recorded call agent"
_MAX_MESSAGE_CHARS: Final = 500
_INSTANT: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_LANGUAGE: Final = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_EMBED_WARNING: Final = (
    "warning: --embed-audio puts the call audio into the report file; do not share the file\n"
)


class UsageError(Exception):
    """A command line the parser refuses; ``usage`` is the usage block of the parser that did."""

    def __init__(self, message: str, usage: str) -> None:
        super().__init__(message)
        self.usage = usage


class Refusal(Exception):
    """An input the command refuses (exit 4). The message is a code, a size or a class name."""


@dataclass(frozen=True, kw_only=True)
class EngineRequest:
    stt: str
    out_dir: Path
    model: str
    environ: Mapping[str, str]
    env_file: Path | None
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    transport: Any


EngineFactory = Callable[[EngineRequest], SttEngine]


@dataclass(frozen=True, kw_only=True)
class Environment:
    """The world of one run. ``transport`` is a test seam, never a command line option."""

    stdout: TextIO
    stderr: TextIO
    environ: Mapping[str, str]
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    transport: Any = None
    engine_factory: EngineFactory | None = None

    @classmethod
    def system(cls) -> Environment:
        return cls(
            stdout=sys.stdout,
            stderr=sys.stderr,
            environ=os.environ,
            clock=lambda: datetime.now(UTC),
            sleep=time.sleep,
        )


def default_engine_factory(request: EngineRequest) -> SttEngine:
    if request.stt.startswith(_REPLAY_PREFIX):
        return ReplayStt(Path(request.stt[len(_REPLAY_PREFIX) :]))
    return create_sarvam_stt(
        out_dir=request.out_dir,
        model=request.model,
        environ=request.environ,
        env_file=request.env_file,
        transport=request.transport,
        sleep=request.sleep,
        clock=request.clock,
    )


# ------------------------------------------------------------------------------------ the parser


class _Parser(argparse.ArgumentParser):
    """Raises instead of exiting on a usage error, and writes to the injected streams."""

    def __init__(
        self,
        *args: Any,
        streams: tuple[TextIO | None, TextIO | None] = (None, None),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._streams = streams

    def error(self, message: str) -> Any:
        raise UsageError(message, self.format_usage())

    def _print_message(self, message: str, file: Any = None) -> None:
        if message:
            target = file if file is not None else (self._streams[0] or sys.stdout)
            target.write(message)

    def print_help(self, file: Any = None) -> None:
        self._print_message(self.format_help(), file)

    def exit(self, status: int = 0, message: str | None = None) -> Any:
        if message:
            self._print_message(message, self._streams[1] or sys.stderr)
        raise SystemExit(status)


def _language(value: str) -> str:
    if _LANGUAGE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("not a language tag")
    return value


def _instant(value: str) -> datetime:
    if _INSTANT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("not an RFC 3339 instant")
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError("not an RFC 3339 instant") from None


def _retention(value: str) -> tuple[bool, datetime | None]:
    if value == "keep":
        return False, None
    kind, _, rest = value.partition(":")
    if kind in ("keep-until", "delete-after") and rest:
        return kind == "delete-after", _instant(rest)
    raise argparse.ArgumentTypeError("use keep, keep-until:INSTANT or delete-after:INSTANT")


def _stt(value: str) -> str:
    if value == "sarvam" or (value.startswith(_REPLAY_PREFIX) and len(value) > len(_REPLAY_PREFIX)):
        return value
    raise argparse.ArgumentTypeError("use sarvam or fake:DIRECTORY")


_EXIT_CODE_HELP: Final = "exit codes:\n" + "".join(
    f"  {code:<2}  {meaning}\n" for code, meaning in sorted(EXIT_CODES.items())
)


def build_parser(
    stdout: TextIO | None = None, stderr: TextIO | None = None
) -> argparse.ArgumentParser:
    streams = (stdout, stderr)
    parser = _Parser(
        prog="tracer",
        description="Tracer: score a recorded call by the agreement of two transcripts.",
        allow_abbrev=False,
        streams=streams,
    )
    commands = parser.add_subparsers(dest="command", parser_class=_Parser)
    score = commands.add_parser(
        "score",
        help="score one recorded call",
        description=(
            "Compare the agent's transcript export of a recorded call with a speech-to-text "
            "transcript of its audio, and point at the spans where the two disagree. Every "
            "privacy and language input is required: none has a default."
        ),
        epilog=_EXIT_CODE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        streams=streams,
    )
    score.add_argument("audio", metavar="AUDIO", help="the recording: a 16-bit PCM WAV file")
    score.add_argument(
        "--transcript", required=True, metavar="FILE", help="the agent's export of the call"
    )
    score.add_argument(
        "--rubric", required=True, metavar="FILE", help="the rubric file (tracer.rubric/1)"
    )
    score.add_argument(
        "--declared-language",
        required=True,
        type=_language,
        metavar="CODE",
        help="the language you declare for the call (required; only mr in this release)",
    )
    score.add_argument(
        "--caller-origin",
        required=True,
        choices=("human", "simulated"),
        help="who called (required)",
    )
    score.add_argument(
        "--consent",
        required=True,
        choices=("obtained", "not_obtained", "unknown"),
        help="consent of the people recorded (required)",
    )
    score.add_argument(
        "--retention",
        required=True,
        type=_retention,
        metavar="POLICY",
        help="keep, keep-until:INSTANT or delete-after:INSTANT (required)",
    )
    score.add_argument(
        "--out", required=True, metavar="DIR", help="the output directory (required)"
    )
    score.add_argument(
        "--stt",
        required=True,
        type=_stt,
        metavar="ENGINE",
        help=(
            "sarvam, which sends the audio to the provider, or fake:DIR, which replays "
            "recorded responses and sends nothing (required)"
        ),
    )
    score.add_argument("--dry-run", action="store_true", help="print the plan and send nothing")
    score.add_argument(
        "--embed-audio",
        action="store_true",
        help="put the call audio into the report file (it then holds personal data)",
    )
    score.add_argument(
        "--env-file",
        metavar="FILE",
        help="read the key SARVAM_API_KEY from this file only; no other line is read",
    )
    score.add_argument(
        "--price-file",
        metavar="FILE",
        help=(
            "a USD price file you wrote; it must also price requests as well as audio_input_seconds"
        ),
    )
    score.add_argument(
        "--priced-at",
        type=_instant,
        metavar="INSTANT",
        help="the instant the price applies to (with --price-file)",
    )
    score.add_argument(
        "--max-chunk-ms",
        type=int,
        default=DEFAULT_MAX_CHUNK_MS,
        metavar="MS",
        help=f"technical default {DEFAULT_MAX_CHUNK_MS}",
    )
    score.add_argument(
        "--search-ms",
        type=int,
        default=DEFAULT_SEARCH_MS,
        metavar="MS",
        help=f"technical default {DEFAULT_SEARCH_MS}",
    )
    score.add_argument(
        "--accept-provider-downmix",
        action="store_true",
        help="send stereo chunks unchanged; the provider merges the channels",
    )
    score.add_argument(
        "--accept-export-warnings",
        action="store_true",
        help="continue past export warnings that drop a line",
    )
    return parser


# ------------------------------------------------------------------------------------ the inputs


def _printable(message: str) -> str:
    cleaned = "".join(c if " " <= c <= "~" else "?" for c in message)
    return cleaned[:_MAX_MESSAGE_CHARS]


def _read_text(path: str, cap: int, role: str) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read(cap + 1)
    except OSError as error:
        raise Refusal(f"cannot read the {role} file ({type(error).__name__})") from None
    if len(data) > cap:
        raise Refusal(f"the {role} file is too large (the cap is {cap} bytes)")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal(f"the {role} file is not valid UTF-8") from None


@dataclass(frozen=True)
class _Prepared:
    """Everything a run needs, read and checked before any engine exists."""

    created_at: datetime
    consent: ConsentRecord
    retention: RetentionPolicy
    audio: WavAudio
    audio_bytes: bytes | None
    export_text: str
    configuration: definitions.RecordedConfiguration
    catalog: PricingCatalog | None
    plan: ChunkPlan
    report_path: Path


def _prepare(args: argparse.Namespace, created_at: datetime) -> _Prepared:
    consent = ConsentRecord(status=args.consent)
    delete_on_expiry, retain_until = args.retention
    retention = RetentionPolicy(delete_on_expiry=delete_on_expiry, retain_until=retain_until)
    if args.declared_language != _SUPPORTED_LANGUAGE:
        raise Refusal("language_not_supported")
    out_dir = Path(args.out)
    if os.path.lexists(out_dir) and not out_dir.is_dir():
        raise Refusal("the output path exists and is not a directory")

    rubric = parse_rubric_file(_read_text(args.rubric, MAX_CONFIG_FILE_BYTES, "rubric"))
    catalog = None
    if args.price_file is not None:
        price_text = _read_text(args.price_file, MAX_CONFIG_FILE_BYTES, "price")
        catalog = load_user_catalog(price_text, created_at=created_at)
    export_text = _read_text(args.transcript, MAX_TRANSCRIPT_FILE_BYTES, "transcript")
    try:
        audio = read_wav(args.audio, created_at=created_at, consent=consent, retention=retention)
    except OSError as error:
        raise Refusal(f"cannot read the audio file ({type(error).__name__})") from None
    configuration = definitions.build_configuration(
        rubric, configured_language=_SUPPORTED_LANGUAGE, created_at=created_at
    )

    # The refusals of the reader and the chunker happen here, before any engine exists.
    read_voicera_export(
        export_text,
        created_at=created_at,
        privacy=PrivacyMarker(human_subject=True, consent=consent, retention=retention),
        declared_language=LanguageDeclaration(
            language=_SUPPORTED_LANGUAGE, source="user_declared", declared_at=created_at
        ),
        accept_warnings=args.accept_export_warnings,
    )
    plan = chunk_wav(
        audio,
        max_chunk_ms=args.max_chunk_ms,
        search_ms=args.search_ms,
        accept_provider_downmix=args.accept_provider_downmix,
    )

    report_path = out_dir / f"report-{created_at:%Y%m%dT%H%M%SZ}.html"
    if os.path.lexists(report_path):
        raise Refusal("report_exists: a report with this name is already in the output directory")
    audio_bytes: bytes | None = None
    if args.embed_audio:
        try:
            audio_bytes = Path(args.audio).read_bytes()
        except OSError as error:
            raise Refusal(f"cannot read the audio file ({type(error).__name__})") from None
        if hashlib.sha256(audio_bytes).hexdigest() != audio.artifact.sha256:
            raise Refusal("the audio file changed while it was read")
    return _Prepared(
        created_at=created_at,
        consent=consent,
        retention=retention,
        audio=audio,
        audio_bytes=audio_bytes,
        export_text=export_text,
        configuration=configuration,
        catalog=catalog,
        plan=plan,
        report_path=report_path,
    )


# ------------------------------------------------------------------------------------ the command


def _dry_run(args: argparse.Namespace, prepared: _Prepared, environment: Environment) -> int:
    plan_text = build_dry_run_plan(
        prepared.plan, model=DEFAULT_MODEL, catalog=prepared.catalog, priced_at=args.priced_at
    ).render()
    lines = [*plan_text.splitlines(), f"max attempts per chunk: {MAX_ATTEMPTS}"]
    lines.append("dry run: nothing was sent")
    environment.stdout.write("".join(f"{line}\n" for line in lines))
    return EXIT_OK


def _write_report(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(text.encode("utf-8"))


def _execute(args: argparse.Namespace, prepared: _Prepared, environment: Environment) -> int:
    out_dir = Path(args.out)
    factory = environment.engine_factory or default_engine_factory
    engine = factory(
        EngineRequest(
            stt=args.stt,
            out_dir=out_dir,
            model=DEFAULT_MODEL,
            environ=environment.environ,
            env_file=None if args.env_file is None else Path(args.env_file),
            clock=environment.clock,
            sleep=environment.sleep,
            transport=environment.transport,
        )
    )
    if args.embed_audio:
        environment.stderr.write(_EMBED_WARNING)

    created_at = prepared.created_at
    catalog = prepared.catalog

    def cost_builder(usages: Sequence[UsageRecord]) -> pipeline.CostParts:
        cost = compute_stt_cost(
            usages, catalog=catalog, created_at=created_at, run_started_at=args.priced_at
        )
        return pipeline.CostParts(
            catalogs=() if catalog is None else (catalog,),
            records=tuple(cost.records),
            summaries=(cost.summary,),
        )

    configs = (provider_config_ref(DEFAULT_MODEL),) if args.stt == "sarvam" else ()
    project = definitions.recorded_project(
        name=_PROJECT_NAME, created_at=created_at, provider_configs=configs
    )
    target = definitions.recorded_agent_target(name=_TARGET_NAME, created_at=created_at)
    try:
        result = pipeline.score_recorded_call(
            audio=prepared.audio,
            export_text=prepared.export_text,
            engine=engine,
            configuration=prepared.configuration,
            project=project,
            agent_target=target,
            configured_language=_SUPPORTED_LANGUAGE,
            export_declared_language=_SUPPORTED_LANGUAGE,
            caller_origin=args.caller_origin,
            consent=prepared.consent,
            retention=prepared.retention,
            created_at=created_at,
            max_chunk_ms=args.max_chunk_ms,
            search_ms=args.search_ms,
            accept_provider_downmix=args.accept_provider_downmix,
            accept_export_warnings=args.accept_export_warnings,
            cost_builder=cost_builder,
        )
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()

    database = store.open_sqlite_store(out_dir)
    try:
        stored = store.store_run(
            database, project=project, agent_target=target, bundle=result.bundle
        )
    finally:
        database.dispose()

    source: report.RelativeAudio | report.EmbeddedAudio
    if prepared.audio_bytes is not None:
        source = report.EmbeddedAudio(prepared.audio_bytes)
    else:
        source = report.RelativeAudio(
            report.relative_audio_src(Path(args.audio).resolve(), out_dir.resolve())
        )
    _write_report(prepared.report_path, report.render_report(result.bundle, audio=source))

    environment.stdout.write(
        summary.render_summary(
            result.bundle, outcome=result.outcome, stt_failure=result.stt_failure
        )
    )
    environment.stdout.write(f"bundle: {stored.bundle_id}\n")
    environment.stdout.write(f"store: {out_dir / store.STORE_FILE_NAME}\n")
    environment.stdout.write(f"report: {prepared.report_path}\n")
    return result.exit_code


def _score(args: argparse.Namespace, environment: Environment) -> int:
    moment = environment.clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("the clock must return an aware datetime")
    prepared = _prepare(args, moment.astimezone(UTC))
    if args.dry_run:
        return _dry_run(args, prepared, environment)
    return _execute(args, prepared, environment)


def run(argv: Sequence[str], environment: Environment) -> int:
    parser = build_parser(environment.stdout, environment.stderr)
    try:
        args = parser.parse_args(list(argv))
        if args.command == "score" and args.priced_at is not None and args.price_file is None:
            raise UsageError("--priced-at needs --price-file", parser.format_usage())
    except UsageError as error:
        environment.stderr.write(error.usage)
        environment.stderr.write(f"tracer: error: {_printable(str(error))}\n")
        return EXIT_USAGE
    if args.command is None:
        parser.print_help(environment.stderr)
        return EXIT_USAGE
    try:
        return _score(args, environment)
    except (Refusal, InputRefusedError, SttConfigurationError, CatalogRefusedError) as error:
        environment.stderr.write(f"error: {_printable(str(error))}\n")
        return EXIT_REFUSED
    except Exception as error:
        environment.stderr.write(f"error: unexpected failure ({type(error).__name__})\n")
        return EXIT_UNEXPECTED


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else list(argv), Environment.system())
