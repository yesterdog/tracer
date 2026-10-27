# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 4 batch E cli tests (``tracer simulate``, P4-21, P4-23 to P4-25).
Support module, not collected. Contract: docs/phase4/api-surface-batch-e.md.

The command is run in-process through ``tracer_cli.cli.run`` with an injected ``Environment`` whose
``sim`` member (``tracer_cli.simulate.SimWorld``) holds the seams: a virtual clock and its driver,
a trace of the steps, the fake or scripted engines and the connector. Nothing opens a socket,
nothing reads the machine's environment or a key, and the only key is the canary. Nothing of the
batch E modules is imported at module level (lazy loaders), so before they exist every test fails on
its own with ``ModuleNotFoundError`` and the directory still collects. All text is invented.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import heapq
import importlib
import io
import json
import re
import struct
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
STAMP = "20261027T120000Z"
REF_TEXT = "Approved by the test operator, ticket 17"
TOKEN_ENV = "AGENT_BEARER"  # noqa: S105 - the NAME of a variable, not a token
TOKEN_VALUE = "tokcanary" + "-".join(["a1", "b2", "c3"])
START_NS = 5_000_000_000
LINE_0 = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930"  # 7 characters
LINE_1 = "\u0927\u0928\u094d\u092f\u0935\u093e\u0926"  # 7 characters
HEARD_0 = "\u0939\u094b\u092f"
HEARD_1 = "\u0920\u0940\u0915"
DENY = re.compile(
    r"(?i)\b(?:accuracy|wer|cer|error rate|correct|recall|precision|latency|response time|quality"
    r"|task success|realism|realistic|fidelity|deterministic|determinism)\b"
)


def mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_cli.{name}")


def core(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalcore.{name}")


def sim_mod(name: str) -> ModuleType:
    return importlib.import_module(f"tracer_evalsim.{name}")


# ------------------------------------------------------------------------------ virtual time


class VirtualClock:
    """The ``Clock`` of tracer-evalsim with virtual time: it moves only when every task waits."""

    def __init__(self, start_ns: int = START_NS) -> None:
        self._now = start_ns
        self._seq = 0
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []

    def monotonic_ns(self) -> int:
        return self._now

    async def sleep_until_ns(self, deadline_ns: int) -> None:
        if deadline_ns <= self._now:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._waiters, (deadline_ns, self._seq, future))
        await future

    def advance(self) -> bool:
        while self._waiters and self._waiters[0][2].done():
            heapq.heappop(self._waiters)
        if not self._waiters:
            return False
        self._now = max(self._now, self._waiters[0][0])
        while self._waiters and self._waiters[0][0] <= self._now:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)
        return True

    def runner(self) -> Callable[[Callable[[], Awaitable[Any]]], Any]:
        async def drive(main: Callable[[], Awaitable[Any]]) -> Any:
            task = asyncio.ensure_future(main())
            try:
                while not task.done():
                    for _ in range(200):
                        await asyncio.sleep(0)
                    if task.done():
                        break
                    if not self.advance():
                        raise AssertionError("virtual run is stuck: nothing sleeps on the clock")
                return task.result()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        return lambda main: asyncio.run(drive(main))


# ---------------------------------------------------------------------------------- the files


def evaluator_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.sim_evaluator/1",
        "name": "synthetic helpline test",
        "language": "mr",
        "instructions": "Ask two short questions of the agent.",
        "expected_outcome": "OUTCOMECANARY the agent answers both questions.",
        "scripted_turns": [
            {"index": 0, "text": LINE_0, "language": "mr"},
            {"index": 1, "text": LINE_1, "language": "mr"},
        ],
        "max_duration_ms": 60_000,
    }
    doc.update(changes)
    return doc


def scenario_doc(steps: Sequence[Any] | None = None) -> dict[str, Any]:
    return {
        "format": "tracer.fake_agent/1",
        "steps": list(
            steps
            if steps is not None
            else [
                {"wait_ms": 400},
                {"speak": {"frames": 10, "level": 1000, "text": HEARD_0}},
                {"wait_ms": 500},
                {"speak": {"frames": 5, "level": 1000, "text": HEARD_1}},
            ]
        ),
    }


def profile_doc(
    dialect: str = "pipecat-protobuf/1",
    *,
    tts: str = "fake",
    stt: str = "fake",
    caller: Mapping[str, Any] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    rate = 8000 if dialect == "twilio-media-stream/1" else 16000
    wire = "mulaw" if rate == 8000 else "pcm_s16le"
    doc: dict[str, Any] = {
        "format": "tracer.sim_profile_file/1",
        "dialect": dialect,
        "audio": {"sample_rate_hz": rate, "channels": 1, "frame_ms": 20, "wire_codec": wire},
        "turn_taking": {
            "caller_speaks_first": True,
            "wait_for_agent_silence_ms": 300,
            "max_silence_ms": 3000,
        },
        "barge_ins": [],
        "caller": dict(caller) if caller is not None else {"mode": "scripted"},
        "voices": {
            "caller-a": {
                "provider": tts,
                "model": "tone" if tts == "fake" else "bulbul:v3",
                "speaker": "test-voice",
                "language_code": "mr-IN",
                "pace": "1",
                "sample_rate_hz": rate,
                "codec": "mulaw" if rate == 8000 else "linear16",
            }
        },
        "stt": {
            "provider": stt,
            "model": "scenario-text" if stt == "fake" else "saaras:v4",
            "max_chunk_ms": 25_000,
        },
        "agreement": {"flag_min_confidence": 0.5, "normaliser": "mr-Deva/1"},
        "min_call_interval_ms": 0,
    }
    doc.update(changes)
    return doc


def llm_caller(**changes: Any) -> dict[str, Any]:
    prompt = core("prompt")
    block: dict[str, Any] = {
        "mode": "llm",
        "provider": "sarvam",
        "model": "sarvam-105b",
        "max_tokens": 50,
        "seed": 7,
        "reasoning_effort": None,
        "max_line_chars": 200,
        "max_turns": 3,
        "max_regenerations": 1,
        "end_sentinel": "END_CALL",
        "prompt": {
            "template_id": prompt.TEMPLATE_ID,
            "template_sha256": prompt.template_sha256(prompt.TEMPLATE_ID),
            "agent_text_max_chars": 500,
        },
    }
    block.update(changes)
    return block


@dataclass
class Files:
    root: Path
    evaluator: Path
    persona: Path
    profile: Path
    sim_profile: Path
    scenario: Path
    target: Path
    allowlist: Path
    export: Path
    out: Path
    url: str = ""


def write_inputs(
    root: Path,
    *,
    evaluator: Mapping[str, Any] | None = None,
    sim_profile: Mapping[str, Any] | None = None,
    scenario: Mapping[str, Any] | None = None,
    allowlist_line: str | None = None,
    url: str = "ws://127.0.0.1:8765/ws-client",
) -> Files:
    root.mkdir(parents=True, exist_ok=True)

    def put(name: str, doc: Any, mode: int = 0o644) -> Path:
        path = root / name
        path.write_text(
            doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=True), "utf-8"
        )
        path.chmod(mode)
        return path

    files = Files(
        root=root,
        evaluator=put("evaluator.json", evaluator if evaluator is not None else evaluator_doc()),
        persona=put(
            "persona.json",
            {"format": "tracer.sim_persona/1", "key": "caller-a", "language": "mr", "accent": None},
        ),
        profile=put(
            "profile.json",
            {
                "format": "tracer.sim_test_profile/1",
                "name": "Asha Testkar",
                "dob": "2001-02-03",
                "account_number": "TEST-0001",
            },
        ),
        sim_profile=put(
            "sim_profile.json", sim_profile if sim_profile is not None else profile_doc()
        ),
        scenario=put("agent.json", scenario if scenario is not None else scenario_doc()),
        target=put(
            "target.json", {"format": "tracer.sim_target/1", "name": "sample agent", "url": url}
        ),
        allowlist=put(
            "allowlist.txt",
            (allowlist_line if allowlist_line is not None else url) + "\n",
            0o600,
        ),
        export=put(
            "export.txt",
            "call_id=5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11\n---\n"
            f"[2026-10-25T06:00:00.000+00:00] assistant: {HEARD_0}\n"
            f"[2026-10-25T06:00:01.000+00:00] user: {LINE_0}\n"
            f"[2026-10-25T06:00:02.000+00:00] assistant: {HEARD_1}\n",
        ),
        out=root / "out",
        url=url,
    )
    replay = root / "stt_replay"
    replay.mkdir(exist_ok=True)
    for index, text in enumerate((HEARD_0, HEARD_1)):
        (replay / f"chunk-{index:04d}.json").write_text(
            json.dumps(
                {"request_id": f"replay-{index}", "transcript": text, "language_code": "mr-IN"},
                ensure_ascii=True,
            ),
            "utf-8",
        )
    return files


# --------------------------------------------------------------------------------- the engines


def wav_bytes(samples: bytes, rate: int) -> bytes:
    return (
        struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + len(samples),
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            rate,
            rate * 2,
            2,
            16,
            b"data",
            len(samples),
        )
        + samples
    )


class SarvamSim(httpx.MockTransport):
    """A scripted Sarvam: TTS (a 100 ms tone per request), chat v2 (scripted replies) and STT
    (scripted transcripts). Every request is kept; the key header is checked, never echoed."""

    def __init__(
        self,
        *,
        chat: Sequence[str] = (),
        stt: Sequence[str] = (HEARD_0, HEARD_1),
        tts_status: int = 200,
        tokens: tuple[int, int] = (10, 5),
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.paths: list[str] = []
        self._chat = list(chat)
        self._stt = list(stt)
        self._tts_status = tts_status
        self._tokens = tokens
        super().__init__(self._handle)

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(r.content)
            for r in self.requests
            if r.url.path == path and r.content[:1] == b"{"
        ]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.paths.append(request.url.path)
        headers = {"content-type": "application/json", "date": "Tue, 27 Oct 2026 12:00:01 GMT"}
        if request.url.path == "/text-to-speech":
            if self._tts_status != 200:
                return httpx.Response(
                    self._tts_status, headers=headers, json={"error": {"code": "x"}}
                )
            body = json.loads(request.content)
            rate = body["speech_sample_rate"]
            samples = (1000).to_bytes(2, "little", signed=True) * (rate // 50 * 5)
            audio = (
                wav_bytes(samples, rate) if body["output_audio_codec"] == "linear16" else samples
            )
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "request_id": f"tts-{len(self.requests)}",
                    "audios": [base64.b64encode(audio).decode("ascii")],
                },
            )
        if request.url.path == "/v2/chat/completions":
            reply = self._chat.pop(0) if self._chat else "END_CALL"
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "id": f"chat-{len(self.requests)}",
                    "choices": [{"message": {"role": "assistant", "content": reply}}],
                    "usage": {
                        "prompt_tokens": self._tokens[0],
                        "completion_tokens": self._tokens[1],
                    },
                },
            )
        if request.url.path == "/speech-to-text":
            text = self._stt.pop(0) if self._stt else HEARD_1
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "request_id": f"stt-{len(self.requests)}",
                    "transcript": text,
                    "language_code": "mr-IN",
                },
            )
        return httpx.Response(404, headers=headers, json={})


# ---------------------------------------------------------------------------------- running


@dataclass
class SimRun:
    code: int
    out: str
    err: str
    trace: list[str]
    files: Files
    clock: VirtualClock
    world: Any
    environ: Any

    def value(self, label: str) -> str:
        found = [x for x in self.out.splitlines() if x.startswith(f"{label}: ")]
        assert len(found) == 1, (label, self.out)
        return found[0][len(label) + 2 :]

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()

    def names(self) -> list[str]:
        return sorted(p.name for p in self.files.out.iterdir()) if self.files.out.is_dir() else []

    def report(self) -> str:
        (path,) = list(self.files.out.glob("report-*.html"))
        return path.read_text("utf-8")

    def bundle(self) -> Any:
        from tracer_evalschema.ids import RunBundleId

        store = mod("store")
        engine = store.open_sqlite_store(self.files.out)
        try:
            return store.load_run(engine, RunBundleId.parse(self.value("bundle")))
        finally:
            engine.dispose()


class EnvRecorder(dict[str, str]):
    """An environment that remembers which variables were read."""

    def __init__(self, items: Mapping[str, str]) -> None:
        super().__init__(items)
        self.reads: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self.reads.append(str(key))
        return super().__contains__(key)


def base_args(files: Files, *, target: bool = False) -> list[str]:
    head = (
        ["--target", str(files.target), "--allowlist", str(files.allowlist)]
        if target
        else ["--agent", f"fake:{files.scenario}"]
    )
    return [
        "simulate",
        *head,
        "--evaluator", str(files.evaluator),
        "--persona", str(files.persona),
        "--sim-profile", str(files.sim_profile),
        "--tts", "fake",
        "--caller", "scripted",
        "--stt", "fake:" + str(files.root / "stt_replay") if target else "fake",
        "--authorisation-reference", REF_TEXT,
        "--out", str(files.out),
    ]  # fmt: skip


def run_sim(
    files: Files,
    *,
    args: Sequence[str] | None = None,
    add: Sequence[str] = (),
    drop: Sequence[str] = (),
    target: bool = False,
    environ: Mapping[str, str] | None = None,
    transport: Any = None,
    world_kw: Mapping[str, Any] | None = None,
    clock: VirtualClock | None = None,
    instant: datetime = NOW,
) -> SimRun:
    """Run ``tracer simulate`` in-process. ``drop`` removes options (with their values) from the
    base arguments; ``add`` appends; ``world_kw`` overrides or adds seams of ``SimWorld``."""
    cli = mod("cli")
    simulate = mod("simulate")
    virtual = clock or VirtualClock()
    trace: list[str] = []
    argv = list(args if args is not None else base_args(files, target=target))
    for option in drop:
        while option in argv:
            at = argv.index(option)
            del argv[at : at + (1 if option in ("--dry-run",) else 2)]
    argv += list(add)
    seams: dict[str, Any] = {
        "clock": virtual,
        "run_async": virtual.runner(),
        "trace": trace.append,
        "run_blocking": _inline,
    }
    if target and not (world_kw and "connect" in world_kw):
        seams["connect"] = fake_connect(files, virtual)
    seams.update(world_kw or {})
    world = simulate.SimWorld(**seams)
    stdout, stderr = io.StringIO(), io.StringIO()
    env: Any = environ if isinstance(environ, EnvRecorder) else dict(environ or {})
    environment = cli.Environment(
        stdout=stdout,
        stderr=stderr,
        environ=env,
        clock=lambda: instant,
        sleep=lambda seconds: None,
        transport=transport,
        sim=world,
    )
    code = cli.run(argv, environment)
    return SimRun(code, stdout.getvalue(), stderr.getvalue(), trace, files, virtual, world, env)


def fake_connect(files: Files, clock: VirtualClock) -> Callable[[Any], Awaitable[Any]]:
    """A connector for the ``--target`` path that answers with the in-process fake agent of the
    scenario file (no socket): the allowlist, the labels and the token still take their normal
    way."""

    async def connect(request: Any) -> Any:
        fakeagent = importlib.import_module("tracer_evalsim.fakeagent")
        scenario = fakeagent.parse_fake_agent(files.scenario.read_text("utf-8"))
        return await fakeagent.connect_fake_agent(scenario, request.dialect, clock)()

    return connect


async def _inline(function: Callable[[], Any]) -> Any:
    return function()


def tree(out: Path) -> dict[str, bytes]:
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def all_text(out: Path) -> str:
    """Every file under ``out`` as text (binary files as latin-1), for 'nowhere' searches."""
    return "\n".join(
        p.read_bytes().decode("latin-1") for p in sorted(out.rglob("*")) if p.is_file()
    )


def real_files(root: Path, **options: Any) -> Files:
    """Inputs for a run with the real (scripted-transport) Sarvam TTS and STT engines."""
    return write_inputs(root, sim_profile=profile_doc(tts="sarvam", stt="sarvam", **options))


def real_args(files: Files, *, target: bool = False) -> list[str]:
    """The arguments of a run with Sarvam TTS and STT and every cap the engines need."""
    args = base_args(files, target=target)
    args[args.index("--tts") + 1] = "sarvam"
    args[args.index("--stt") + 1] = "sarvam"
    return [*args, "--max-call-seconds", "60", "--max-tts-chars", "100", "--max-stt-seconds", "60"]


def real_env(**extra: str) -> EnvRecorder:
    return EnvRecorder({"SARVAM_API_KEY": KEY_CANARY, **extra})


def assert_sim_three_keys_never_in_outputs(root: Path, caplog: Any) -> None:
    """The simulated-run extension of ``test_key_never_in_store_and_report_outputs`` (SCOPE P4-24:
    "extended to three keys"). One provider key serves the three roles (TTS, chat v2, STT); it goes
    to the provider through the scripted transport, from the environment and from an env file, in
    an ordinary call and in a call that is interrupted after it was paid. A second secret, a bearer
    token for the agent, rides along. No byte of any output holds any of them: stdout, stderr, the
    report, the recording, the store, the call log, the TTS cache, the recovery file, and the DEBUG
    log records. The check is not vacuous: a text canary placed in the expected outcome and in the
    agent's words IS found in the store and the report."""
    import logging

    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ("httpx", "httpcore", "tracer_cli", "tracer_evalproviders", "tracer_evalsim"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    env_file = root / "keys.env"
    root.mkdir(parents=True, exist_ok=True)
    env_file.write_text(f"DECOY=1\nSARVAM_API_KEY={KEY_CANARY}\n", "utf-8")
    env_file.chmod(0o600)
    secrets = (KEY_CANARY, TOKEN_VALUE, REF_TEXT)
    for index, (via_file, interrupted) in enumerate(((False, False), (True, False), (False, True))):
        folder = root / f"run{index}"
        files = write_inputs(
            folder,
            sim_profile=profile_doc(
                tts="sarvam", stt="sarvam", caller=None if interrupted else llm_caller()
            ),
            url="ws://127.0.0.1:8765/ws-client",
            allowlist_line=f"ws://127.0.0.1:8765/ws-client auth_token_env={TOKEN_ENV}",
        )
        args = real_args(files, target=True)
        if not interrupted:  # the interrupted call is a scripted one: its TTS was paid before it
            args[args.index("--caller") + 1] = "sarvam"
            args += ["--max-llm-tokens", "500"]
        if via_file:
            args += ["--env-file", str(env_file)]
        sim = SarvamSim(chat=[LINE_0, LINE_1, "END_CALL"])
        environ: dict[str, str] = {TOKEN_ENV: TOKEN_VALUE}
        if not via_file:
            environ["SARVAM_API_KEY"] = KEY_CANARY
        streams = ""
        if interrupted:

            async def interrupt(request: Any) -> Any:
                raise KeyboardInterrupt

            with contextlib.suppress(KeyboardInterrupt):
                run_sim(
                    files,
                    args=args,
                    target=True,
                    environ=environ,
                    transport=sim,
                    world_kw={"connect": interrupt},
                )
        else:
            run = run_sim(files, args=args, target=True, environ=environ, transport=sim)
            assert run.code == 0, (run.out, run.err)
            streams = run.out + run.err
            report = (next(files.out.glob("report-*.html"))).read_text("utf-8")
            data = (files.out / "tracer.sqlite").read_bytes()
            assert HEARD_0 in report  # the check is not vacuous
            assert b"OUTCOMECANARY" in data
            assert HEARD_0.encode("utf-8") in data
            assert {r.headers["api-subscription-key"] for r in sim.requests} == {KEY_CANARY}
        everything = streams + all_text(files.out)
        for secret in secrets:
            assert secret not in everything, (index, secret)
        assert "SARVAM_API_KEY" not in streams
        assert str(env_file) not in everything
        for path in (files.out / "tts-cache").iterdir():
            assert re.fullmatch(r"[0-9a-f]{64}", path.name)
        if interrupted:
            assert list(files.out.glob("usage-recovery-*.json"))
    for secret in secrets:
        assert secret not in caplog.text
