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
import json
import os
import re
import stat
import sys
import time
import unicodedata
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO

from tracer_cli import report, store, summary
from tracer_cli.replay import ReplayStt
from tracer_cli.simargs import (
    add_judge_arguments,
    add_simulate_arguments,
    judge_usage_problem,
    simulate_usage_problem,
)
from tracer_evalcore import definitions, pipeline
from tracer_evalcore.align import ALIGN_MAX_TOKENS
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
from tracer_evalcore.scoring import export_token_count
from tracer_evalcore.stt import SttConfigurationError, SttContext, SttEngine, SttOutcome
from tracer_evalproviders.cost import CatalogRefusedError, compute_stt_cost, load_user_catalog
from tracer_evalproviders.dryrun import build_dry_run_plan
from tracer_evalproviders.sarvam.client import MAX_ATTEMPTS
from tracer_evalproviders.sarvam.engine import create_sarvam_stt
from tracer_evalproviders.sarvam.models import DEFAULT_MODEL
from tracer_evalproviders.secrets import EnvFileModeWarning, provider_config_ref
from tracer_evalschema.bundle import RunBundle
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
    "MAX_CONSENT_REFERENCE_CHARS",
    "MAX_SARVAM_CHUNKS",
    "MAX_SARVAM_CHUNK_MS",
    "MAX_TRANSCRIPT_FILE_BYTES",
    "RECOVERY_FORMAT",
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
MAX_SARVAM_CHUNKS: Final = 200
MAX_SARVAM_CHUNK_MS: Final = 30_000
MAX_CONSENT_REFERENCE_CHARS: Final = 200
RECOVERY_FORMAT: Final = "tracer.usage_recovery/1"

_SUPPORTED_LANGUAGE: Final = "mr"
_REPLAY_PREFIX: Final = "fake:"
_PROJECT_NAME: Final = "recorded-call scoring"
_TARGET_NAME: Final = "recorded call agent"
_MAX_MESSAGE_CHARS: Final = 500
_INSTANT: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
_LANGUAGE: Final = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_POSITIVE_INTEGER: Final = re.compile(r"[1-9][0-9]{0,8}")
_REFERENCE_REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
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


class ProviderFailure(Exception):
    """A provider failed before the call (exit 1); the message is a class name and closed keys."""


@dataclass(frozen=True, kw_only=True)
class EngineRequest:
    stt: str
    out_dir: Path
    model: str
    environ: Mapping[str, str] = field(repr=False)
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
    environ: Mapping[str, str] = field(repr=False)
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    transport: Any = None
    engine_factory: EngineFactory | None = None
    sim: Any = None  # a tracer_cli.simulate.SimWorld: the seams of `tracer simulate` (tests only)

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


def _consent_reference(value: str) -> str:
    if not 1 <= len(value) <= MAX_CONSENT_REFERENCE_CHARS:
        raise argparse.ArgumentTypeError(f"use 1 to {MAX_CONSENT_REFERENCE_CHARS} characters")
    if value != value.strip():
        raise argparse.ArgumentTypeError("no leading or trailing white space")
    if any(unicodedata.category(char) in _REFERENCE_REFUSED_CATEGORIES for char in value):
        raise argparse.ArgumentTypeError("no control, format, separator or unassigned character")
    return value


def _positive_integer(value: str) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("use a positive whole number")
    return int(value)


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
        "--consent-reference",
        type=_consent_reference,
        metavar="TEXT",
        help="where the consent is recorded (required with --consent obtained)",
    )
    score.add_argument(
        "--consent-recorded-at",
        type=_instant,
        metavar="INSTANT",
        help="when the consent was recorded (required with --consent obtained)",
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
        "--max-chunks",
        type=_positive_integer,
        metavar="N",
        help=(
            "refuse a plan with more chunks than N; required with --stt sarvam, "
            f"which accepts at most {MAX_SARVAM_CHUNKS}"
        ),
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
    simulate = commands.add_parser(
        "simulate",
        help="place one simulated call to an agent",
        description=(
            "Place one simulated call from a synthetic caller to an agent under test, record "
            "both sides and score the recording. Every value that decides the call or its spend "
            "is yours: none has a default. Nothing is sent before every check has passed."
        ),
        allow_abbrev=False,
        streams=streams,
    )
    add_simulate_arguments(simulate)
    judge = commands.add_parser(
        "judge",
        help="re-judge a stored judged run (an uncalibrated signal)",
        description=(
            "Re-judge the stored answers of a judged simulated run with the same model, once or "
            "K times, and store one rescoring bundle for each repeat. The answers do not change, "
            "so the repeats measure the judge, not the agent. Every value that decides a request "
            "or its spend is yours: none has a default."
        ),
        epilog=_EXIT_CODE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        streams=streams,
    )
    judge.add_argument("--out", required=True, metavar="DIR", help="the output directory (store)")
    judge.add_argument("--bundle", required=True, metavar="ID", help="the judged run bundle")
    judge.add_argument(
        "--repeats",
        type=_repeats,
        default=1,
        metavar="K",
        help="how many times to re-judge, 1 to 100 (default 1)",
    )
    judge.add_argument("--env-file", metavar="FILE", help="read the judge key from this file only")
    judge.add_argument("--dry-run", action="store_true", help="print the plan and send nothing")
    add_judge_arguments(judge, required=True)
    labels = commands.add_parser(
        "labels",
        help="the human-label loop of the judge",
        description="Blind packets, labels files and counts of the judge against human labels.",
        allow_abbrev=False,
        streams=streams,
    )
    steps = labels.add_subparsers(dest="labels_command", parser_class=_Parser)
    packet = steps.add_parser(
        "packet", help="write a blind packet", allow_abbrev=False, streams=streams
    )
    packet.add_argument("--out", required=True, metavar="DIR", help="the output directory (store)")
    packet.add_argument("--bundle", required=True, metavar="ID", help="the judged run bundle")
    packet.add_argument("--golden", required=True, metavar="FILE", help="the golden set of the run")
    check = steps.add_parser(
        "check", help="validate a labels file and keep a copy", allow_abbrev=False, streams=streams
    )
    check.add_argument("--out", required=True, metavar="DIR", help="the output directory (store)")
    check.add_argument(
        "--labels", required=True, metavar="FILE", help="the labels file (tracer.judge_labels/1)"
    )
    agreement = steps.add_parser(
        "agreement",
        help="counts of the judge against the labels",
        allow_abbrev=False,
        streams=streams,
    )
    agreement.add_argument(
        "--out", required=True, metavar="DIR", help="the output directory (store)"
    )
    agreement.add_argument("--bundle", required=True, metavar="ID", help="the judged run bundle")
    return parser


# ------------------------------------------------------------------------------------ the inputs


def _repeats(value: str) -> int:
    if re.fullmatch(r"[1-9][0-9]{0,2}", value) is None or int(value) > 100:
        raise argparse.ArgumentTypeError("use a whole number from 1 to 100")
    return int(value)


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
    consent = ConsentRecord(
        status=args.consent,
        reference=args.consent_reference,
        recorded_at=args.consent_recorded_at,
    )
    if args.consent_recorded_at is not None and args.consent_recorded_at > created_at:
        raise Refusal("consent_recorded_at_after_run: the consent cannot be recorded in the future")
    delete_on_expiry, retain_until = args.retention
    retention = RetentionPolicy(delete_on_expiry=delete_on_expiry, retain_until=retain_until)
    if args.declared_language != _SUPPORTED_LANGUAGE:
        raise Refusal("language_not_supported")
    if args.stt == "sarvam" and args.consent != "obtained":
        raise Refusal(
            "consent_not_obtained: --stt sarvam sends the audio to the provider "
            "and needs --consent obtained"
        )
    out_dir = Path(args.out)
    if "?" in str(out_dir.resolve()):
        raise Refusal("output_path_invalid: the output path must not contain a question mark")
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
    export = read_voicera_export(
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
    if args.max_chunks is not None and len(plan.chunks) > args.max_chunks:
        raise Refusal(
            f"max_chunks_exceeded: the plan has {len(plan.chunks)} chunks "
            f"and --max-chunks is {args.max_chunks}"
        )
    if args.stt == "sarvam":
        tokens = export_token_count(
            export.turns, definitions.parameters_of(configuration.snapshot).normaliser
        )
        if tokens > ALIGN_MAX_TOKENS:
            raise Refusal(
                f"export_tokens_above_alignment_cap: the export has {tokens} tokens "
                f"and the alignment cap is {ALIGN_MAX_TOKENS}"
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
    if args.max_chunks is None:
        lines.append("max chunks: not set")
    else:
        count = len(prepared.plan.chunks)
        lines.append(f"max chunks: {args.max_chunks} (the plan has {count}: within the cap)")
    lines.append("dry run: nothing was sent")
    environment.stdout.write("".join(f"{line}\n" for line in lines))
    return EXIT_OK


def _write_new_file(path: Path, text: str) -> None:
    """Create ``path`` exclusively with mode 0600; an existing file or link is never opened."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(text.encode("utf-8"))


class _Recorder:
    """Passes ``transcribe`` through and keeps what was paid, so usage outlives any end of the send.

    It keeps the outcome and, for an engine that streams usage, the records handed out as each
    chunk was answered. ``usage`` is the outcome's once ``transcribe`` returned, otherwise the
    handed-out records: the two are never added.
    """

    def __init__(self, engine: SttEngine) -> None:
        self._engine = engine
        self.outcome: SttOutcome | None = None
        self.streamed: list[UsageRecord] = []

    @property
    def usage(self) -> tuple[UsageRecord, ...]:
        return self.outcome.usage if self.outcome is not None else tuple(self.streamed)

    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome:
        if getattr(self._engine, "streams_usage", False) is True:
            self.outcome = self._engine.transcribe(  # type: ignore[call-arg]
                plan, context, on_usage=self.streamed.append
            )
        else:
            self.outcome = self._engine.transcribe(plan, context)
        return self.outcome


def _check_output_location(out_dir: Path) -> None:
    """Refuse an output location that cannot be written, without creating anything."""
    nearest = out_dir
    while not os.path.lexists(nearest):
        nearest = nearest.parent
    if not nearest.is_dir() or not os.access(nearest, os.W_OK | os.X_OK):
        raise Refusal("output_unavailable (directory_not_writable)")
    store_file = out_dir / store.STORE_FILE_NAME
    if os.path.islink(store_file):
        raise Refusal("output_unavailable (store_file_is_a_link)")
    if os.path.lexists(store_file) and (
        not store_file.is_file() or not os.access(store_file, os.W_OK)
    ):
        raise Refusal("output_unavailable (store_file_not_writable)")


def _build_engine(
    factory: EngineFactory, request: EngineRequest, environment: Environment
) -> SttEngine:
    """Call the factory; an env file that others can read becomes one warning line on stderr."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            return factory(request)
        except OSError as error:
            raise Refusal(f"output_unavailable ({type(error).__name__})") from None
        finally:
            for item in caught:
                if issubclass(item.category, EnvFileModeWarning):
                    environment.stderr.write(f"warning: {_printable(str(item.message))}\n")


def _write_recovery(
    out_dir: Path,
    created_at: datetime,
    *,
    failed_step: str,
    error: BaseException,
    usage: Sequence[UsageRecord],
    bundle: RunBundle | None,
) -> Path | None:
    """Keep the usage of a paid send; ``None`` when there is none or the file cannot be written.

    Any ``Exception`` of its own becomes ``None``, so the failure being reported is never replaced.
    The document is built and serialised before the file is created: no half-written file.
    Only the class name of the error is written: a message could hold a key or call text.
    """
    if not usage:
        return None
    try:
        return _write_recovery_file(out_dir, created_at, failed_step, error, usage, bundle)
    except Exception:
        return None


def _write_recovery_file(
    out_dir: Path,
    created_at: datetime,
    failed_step: str,
    error: BaseException,
    usage: Sequence[UsageRecord],
    bundle: RunBundle | None,
) -> Path:
    document = {
        "format": RECOVERY_FORMAT,
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "failed_step": failed_step,
        "error_class": type(error).__name__,
        "usage_records": [record.model_dump(mode="json") for record in usage],
        "cost_records": [] if bundle is None else _dump(bundle.cost_records),
        "cost_summaries": [] if bundle is None else _dump(bundle.cost_summaries),
        "pricing_catalogs": [] if bundle is None else _dump(bundle.pricing_catalogs),
    }
    path = out_dir / f"usage-recovery-{created_at:%Y%m%dT%H%M%SZ}.json"
    _write_new_file(path, json.dumps(document, ensure_ascii=True, indent=1) + "\n")
    return path


def _dump(records: Sequence[Any]) -> list[Any]:
    return [record.model_dump(mode="json") for record in records]


def _execute(args: argparse.Namespace, prepared: _Prepared, environment: Environment) -> int:
    out_dir = Path(args.out)
    _check_output_location(out_dir)
    factory = environment.engine_factory or default_engine_factory
    engine = _build_engine(
        factory,
        EngineRequest(
            stt=args.stt,
            out_dir=out_dir,
            model=DEFAULT_MODEL,
            environ=environment.environ,
            env_file=None if args.env_file is None else Path(args.env_file),
            clock=environment.clock,
            sleep=environment.sleep,
            transport=environment.transport,
        ),
        environment,
    )
    try:
        preflight = getattr(engine, "preflight", None)
        if callable(preflight):
            preflight(prepared.plan)
        # The store is open before the first request: a store that cannot be written is found
        # while nothing has been sent.
        try:
            database = store.open_sqlite_store(out_dir)
        except Exception as error:
            raise Refusal(f"output_unavailable ({type(error).__name__})") from None
        try:
            return _send_and_save(args, prepared, environment, engine, database)
        finally:
            database.dispose()
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


def _send_and_save(
    args: argparse.Namespace,
    prepared: _Prepared,
    environment: Environment,
    engine: SttEngine,
    database: Any,
) -> int:
    out_dir = Path(args.out)
    try:
        leaf_mode = stat.S_IMODE(out_dir.stat().st_mode) & 0o777
    except OSError:
        leaf_mode = 0
    if leaf_mode & 0o077:
        environment.stderr.write(
            "warning: the output directory is accessible to group or other users "
            f"(mode {leaf_mode:04o})\n"
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
    recorder = _Recorder(engine)
    try:
        result = pipeline.score_recorded_call(
            audio=prepared.audio,
            export_text=prepared.export_text,
            engine=recorder,
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
    except BaseException as error:
        # No run exists, but a chunk may have been paid: keep its usage, also on Ctrl-C.
        if recorder.usage:
            saved = _write_recovery(
                out_dir,
                created_at,
                failed_step="pipeline",
                error=error,
                usage=recorder.usage,
                bundle=None,
            )
            environment.stdout.write(
                f"usage recovery: {saved if saved is not None else 'not written'}\n"
            )
        raise

    # From here the answer is in memory and paid for: each output is tried on its own.
    failures: list[Exception] = []
    stored = None
    try:
        stored = store.store_run(
            database, project=project, agent_target=target, bundle=result.bundle
        )
    except Exception as error:
        failures.append(error)
    except BaseException as error:
        # The user asked to stop: keep the paid usage, write no report, and stop.
        saved = _write_recovery(
            out_dir,
            created_at,
            failed_step="store",
            error=error,
            usage=result.bundle.usage_records,
            bundle=result.bundle,
        )
        environment.stdout.write(
            f"usage recovery: {saved if saved is not None else 'not written'}\n"
        )
        raise
    recovery: Path | None = None
    if stored is None:
        recovery = _write_recovery(
            out_dir,
            created_at,
            failed_step="store",
            error=failures[0],
            usage=result.bundle.usage_records,
            bundle=result.bundle,
        )

    wrote_report = False
    try:
        source: report.RelativeAudio | report.EmbeddedAudio
        if prepared.audio_bytes is not None:
            source = report.EmbeddedAudio(prepared.audio_bytes)
        else:
            source = report.RelativeAudio(
                report.relative_audio_src(Path(args.audio).resolve(), out_dir.resolve())
            )
        _write_new_file(prepared.report_path, report.render_report(result.bundle, audio=source))
        wrote_report = True
    except Exception as error:
        failures.append(error)

    environment.stdout.write(
        summary.render_summary(
            result.bundle, outcome=result.outcome, stt_failure=result.stt_failure
        )
    )
    environment.stdout.write(f"bundle: {result.bundle.id}\n")
    store_text = out_dir / store.STORE_FILE_NAME if stored is not None else "not written"
    environment.stdout.write(f"store: {store_text}\n")
    report_text = prepared.report_path if wrote_report else "not written"
    environment.stdout.write(f"report: {report_text}\n")
    if stored is None:
        recovery_text = recovery if recovery is not None else "not written"
        environment.stdout.write(f"usage recovery: {recovery_text}\n")
    if failures:
        environment.stderr.write(f"error: unexpected failure ({type(failures[0]).__name__})\n")
        return EXIT_UNEXPECTED
    return result.exit_code


def _score(args: argparse.Namespace, environment: Environment) -> int:
    moment = environment.clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("the clock must return an aware datetime")
    prepared = _prepare(args, moment.astimezone(UTC))
    if args.dry_run:
        return _dry_run(args, prepared, environment)
    return _execute(args, prepared, environment)


def _check_options(args: argparse.Namespace, usage: str) -> None:
    """The usage errors that depend on more than one option."""
    if args.consent == "obtained" and (
        args.consent_reference is None or args.consent_recorded_at is None
    ):
        raise UsageError(
            "--consent obtained needs --consent-reference and --consent-recorded-at", usage
        )
    if args.stt != "sarvam":
        return
    if args.max_chunk_ms > MAX_SARVAM_CHUNK_MS:
        raise UsageError(
            f"--max-chunk-ms above {MAX_SARVAM_CHUNK_MS} is refused for --stt sarvam", usage
        )
    if args.max_chunks is None and not args.dry_run:
        raise UsageError("--stt sarvam needs --max-chunks", usage)
    if args.max_chunks is not None and args.max_chunks > MAX_SARVAM_CHUNKS:
        raise UsageError(
            f"--max-chunks above {MAX_SARVAM_CHUNKS} is refused for --stt sarvam", usage
        )


def run(argv: Sequence[str], environment: Environment) -> int:
    parser = build_parser(environment.stdout, environment.stderr)
    try:
        args = parser.parse_args(list(argv))
        if args.command == "score" and args.priced_at is not None and args.price_file is None:
            raise UsageError("--priced-at needs --price-file", parser.format_usage())
        if args.command == "score":
            _check_options(args, parser.format_usage())
        if args.command == "simulate":
            problem = simulate_usage_problem(args)
            if problem is not None:
                raise UsageError(problem, parser.format_usage())
        if args.command == "judge":
            problem = judge_usage_problem(args)
            if problem is not None:
                raise UsageError(problem, parser.format_usage())
        if args.command == "labels" and args.labels_command is None:
            raise UsageError("use packet, check or agreement", parser.format_usage())
    except UsageError as error:
        environment.stderr.write(error.usage)
        environment.stderr.write(f"tracer: error: {_printable(str(error))}\n")
        return EXIT_USAGE
    if args.command is None:
        parser.print_help(environment.stderr)
        return EXIT_USAGE
    try:
        if args.command == "simulate":
            from tracer_cli import simulate as simulate_module

            return simulate_module.run_simulate(args, environment)
        if args.command == "judge":
            from tracer_cli import judgecmd

            return judgecmd.run_judge(args, environment)
        if args.command == "labels":
            from tracer_cli import labelscmd

            return labelscmd.run_labels(args, environment)
        return _score(args, environment)
    except (Refusal, InputRefusedError, SttConfigurationError, CatalogRefusedError) as error:
        environment.stderr.write(f"error: {_printable(str(error))}\n")
        return EXIT_REFUSED
    except ProviderFailure as error:
        environment.stderr.write(f"error: {_printable(str(error))}\n")
        return EXIT_UNEXPECTED
    except Exception as error:
        environment.stderr.write(f"error: unexpected failure ({type(error).__name__})\n")
        return EXIT_UNEXPECTED


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else list(argv), Environment.system())
