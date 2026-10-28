# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018
"""P4-30: the key canary across every output channel of ``tracer simulate``, for each of the three
roles that hold the Sarvam key (speech-to-text, text-to-speech, the caller model).

``test_cli_simulate_canaries.py`` (batch E, ``assert_sim_three_keys_never_in_outputs``) proves the
good path and an interrupted call. This file proves the HOSTILE path: one role's provider answers
with an error that echoes the key and a message of its own, or with a body that is not JSON, or
the transport fails with an exception whose text holds the key, while the other two roles work. The
key canary, the bearer-token canary, the authorisation-reference text and the message canary of the
failing role must be in none of: stdout, stderr, any exception the engines raised (``str``,
``repr``, ``args``, ``__dict__``, the chain and the formatted traceback), the ``repr`` of an
engine, of its seam and of the world, the DEBUG log records of every logger, the call log, the
store, the report, the recording, the recovery file or the names in the TTS cache directory.
The telephony key canary is deferred with the telephony stage (BL-311).

The check is not vacuous: each mode is served (the hostile transport counts its answers), the key
reached the provider in the header, and a text canary that is meant to be stored IS found in the
store and the report. Contract: docs/phase4/api-surface-batch-f.md section 7.
"""

from __future__ import annotations

import gc
import importlib
import json
import logging
import re
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    LINE_0,
    LINE_1,
    all_text,
    llm_caller,
    profile_doc,
    real_args,
    real_env,
    run_sim,
    sim_mod,
    write_inputs,
)
from cli_p4f_support import (  # type: ignore[import-not-found,unused-ignore]
    KEY_CANARY,
    REF_TEXT,
    TOKEN_VALUE,
    SarvamSim,
)

TOKEN_ENV = "AGENT_BEARER"  # noqa: S105 - the NAME of a variable, not a token
PATHS = {"tts": "/text-to-speech", "llm": "/v2/chat/completions", "stt": "/speech-to-text"}
MODES = ("echo_401", "echo_500", "echo_200_text", "raise_connect", "raise_timeout")
ROLES = tuple(PATHS)


def message_canary(role: str) -> str:
    return "-".join(["MSGCANARY", role.upper(), "".join(["a1", "b2", "c3"])])


class HostileSarvam(SarvamSim):  # type: ignore[misc,unused-ignore]
    """The scripted Sarvam with one role gone hostile. The other two answer as ``SarvamSim`` does."""

    def __init__(self, role: str, mode: str, **kwargs: Any) -> None:
        self.role = role
        self.mode = mode
        self.served = 0
        super().__init__(**kwargs)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != PATHS[self.role]:
            answered: httpx.Response = super()._handle(request)
            return answered
        self.requests.append(request)
        self.served += 1
        key = request.headers.get("api-subscription-key", "")
        text = f"invalid key {key} and {message_canary(self.role)}"
        if self.mode == "raise_connect":
            raise httpx.ConnectError(f"connect failed {text}", request=request)
        if self.mode == "raise_timeout":
            raise httpx.ReadTimeout(f"timed out {text}", request=request)
        headers = {"content-type": "application/json", "x-echo": key}
        if self.mode == "echo_200_text":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text=text)
        status = 401 if self.mode == "echo_401" else 500
        body = {"error": {"message": text, "code": text, "request_id": text}, "detail": text}
        return httpx.Response(status, headers=headers, json=body)


class Recorder:
    """Wraps an engine; keeps every exception its methods raise or hand back in an outcome's
    ``failure`` (the engines are never replaced)."""

    def __init__(self, inner: Any, sink: list[BaseException]) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_sink", sink)

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                result = attribute(*args, **kwargs)
            except BaseException as error:
                self._sink.append(error)
                raise
            failure = getattr(result, "failure", None)  # an outcome may carry the failure
            if isinstance(failure, BaseException):
                self._sink.append(failure)
            return result

        return call

    def __repr__(self) -> str:
        return repr(self._inner)


def text_of(error: BaseException) -> str:
    """Every textual view of an exception and of its chain."""
    parts: list[str] = []
    seen: set[int] = set()
    stack: list[BaseException | None] = [error]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        parts += [
            str(current),
            repr(current),
            repr(current.args),
            repr(getattr(current, "__dict__", {})),
            "".join(getattr(current, "__notes__", [])),
            "".join(traceback.format_exception(current)),
        ]
        stack += [current.__cause__, current.__context__]
    return "\n".join(parts)


def run_hostile(
    root: Path, role: str, mode: str, caplog: pytest.LogCaptureFixture
) -> dict[str, Any]:
    caplog.set_level(logging.DEBUG)  # restored by the fixture: no level leaks into another test
    for name in ("httpx", "httpcore", "tracer_cli", "tracer_evalproviders", "tracer_evalsim"):
        caplog.set_level(logging.DEBUG, logger=name)
    caller = llm_caller() if role == "llm" else None
    files = write_inputs(
        root,
        sim_profile=profile_doc(
            tts="sarvam", stt="sarvam", **({"caller": caller} if caller else {})
        ),
        url="ws://127.0.0.1:8765/ws-client",
        allowlist_line=f"ws://127.0.0.1:8765/ws-client auth_token_env={TOKEN_ENV}",
    )
    args = real_args(files, target=True)
    if role == "llm":
        args[args.index("--caller") + 1] = "sarvam"
        args += ["--max-llm-tokens", "500"]
    sim = HostileSarvam(role, mode, chat=[LINE_0, LINE_1, "END_CALL"])
    errors: list[BaseException] = []
    engines: list[Any] = []
    seams: list[Any] = []
    factories = importlib_simulate()

    def wrap(kind: str) -> Any:
        def factory(seam: Any) -> Any:
            seams.append(seam)
            engine = factories[kind](seam)
            engines.append(engine)
            return Recorder(engine, errors)

        return factory

    environ = {**real_env(**{TOKEN_ENV: TOKEN_VALUE})}
    caplog.clear()
    run = run_sim(
        files,
        args=args,
        target=True,
        environ=environ,
        transport=sim,
        world_kw={
            "tts_factory": wrap("tts"),
            "llm_factory": wrap("llm"),
            "stt_factory": wrap("stt"),
        },
    )
    channels = {
        "stdout": run.out,
        "stderr": run.err,
        "files": all_text(files.out),
        "names": "\n".join(str(p.relative_to(files.out)) for p in files.out.rglob("*")),
        "logs": caplog.text
        + "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records),
        "exceptions": "\n".join(text_of(e) for e in errors),
        "reprs": "\n".join(
            [*(repr(e) + str(e) for e in engines), *(repr(s) for s in seams), repr(run.world)]
        ),
    }
    return {"run": run, "sim": sim, "channels": channels, "errors": errors, "files": files}


def importlib_simulate() -> dict[str, Any]:
    """The default engine builders of the command, so that the wrapped engines are the real ones."""
    from tracer_evalproviders.sarvam.chat import create_sarvam_llm
    from tracer_evalproviders.sarvam.engine import create_sarvam_stt_for_run
    from tracer_evalproviders.sarvam.tts import create_sarvam_tts

    def tts(seam: Any) -> Any:
        return create_sarvam_tts(
            call_log=seam.call_log, cap_characters=seam.cap, environ=seam.environ,
            env_file=seam.env_file, cache=seam.cache, transport=seam.transport, sleep=seam.sleep,
        )  # fmt: skip

    def llm(seam: Any) -> Any:
        return create_sarvam_llm(
            call_log=seam.call_log, cap_tokens=seam.cap, environ=seam.environ,
            env_file=seam.env_file, transport=seam.transport, sleep=seam.sleep,
        )  # fmt: skip

    def stt(seam: Any) -> Any:
        return create_sarvam_stt_for_run(
            call_log=seam.call_log, model=seam.model, environ=seam.environ,
            env_file=seam.env_file, transport=seam.transport, sleep=seam.sleep,
        )  # fmt: skip

    return {"tts": tts, "llm": llm, "stt": stt}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("role", ROLES)
def test_key_canaries_never_in_any_output_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, role: str, mode: str
) -> None:
    """One role's provider is hostile in one way; no secret is in any channel. The three roles
    share the one key variable, so each role is also shown to have sent the key in the header only
    (the canary is in the transport's own record of the request and nowhere else)."""
    result = run_hostile(tmp_path, role, mode, caplog)
    sim: HostileSarvam = result["sim"]
    run = result["run"]
    # not vacuous: the hostile answer was served, and the key reached the provider in the header
    assert sim.served >= 1, (role, mode)
    assert {r.headers["api-subscription-key"] for r in sim.requests} == {KEY_CANARY}
    assert run.code in (0, 1, 2, 3, 4, 5), (role, mode, run.code, run.err)
    assert run.err.count("\n") <= 1, run.err  # one "error: ..." line or nothing
    for channel, text in result["channels"].items():
        for secret in (KEY_CANARY, TOKEN_VALUE, REF_TEXT, message_canary(role)):
            assert secret not in text, (role, mode, channel)
        if channel in ("stdout", "stderr"):
            assert "SARVAM_API_KEY" not in text, (role, mode, channel)
        # the provider's own words never reach a channel (the adapters hold facts only)
        assert "invalid key" not in text, (role, mode, channel)
    # the TTS cache holds hex names only
    cache = result["files"].out / "tts-cache"
    if cache.is_dir():
        for entry in cache.iterdir():
            assert re.fullmatch(r"[0-9a-f]{64}", entry.name), entry.name
    # every failure the engines raised is provider-neutral: no cause, no context, facts only
    for error in result["errors"]:
        assert error.__cause__ is None and error.__context__ is None, (role, mode, error)
        assert KEY_CANARY not in text_of(error)
    # a failure after a paid request keeps the usage: the recovery file, key-free
    for recovery in result["files"].out.glob("usage-recovery-*.json"):
        document = json.loads(recovery.read_text("utf-8"))
        assert document["format"] == "tracer.usage_recovery/1"
        assert KEY_CANARY not in recovery.read_text("utf-8")


def test_the_canary_check_is_not_vacuous_a_stored_text_is_found(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Control: on a good run the expected-outcome canary and the agent's words ARE in the store
    and the report (they are meant to be), while the key is in neither. A search that could not
    find a stored text would prove nothing about the key."""
    files = write_inputs(
        tmp_path,
        sim_profile=profile_doc(tts="sarvam", stt="sarvam"),
        url="ws://127.0.0.1:8765/ws-client",
        allowlist_line=f"ws://127.0.0.1:8765/ws-client auth_token_env={TOKEN_ENV}",
    )
    sim = SarvamSim()
    run = run_sim(
        files,
        args=real_args(files, target=True),
        target=True,
        environ=real_env(**{TOKEN_ENV: TOKEN_VALUE}),
        transport=sim,
    )
    assert run.code == 0, (run.out, run.err)
    data = (files.out / "tracer.sqlite").read_bytes()
    assert b"OUTCOMECANARY" in data
    assert HEARD_0.encode("utf-8") in data
    assert HEARD_0 in run.report()
    everything = all_text(files.out) + run.out + run.err
    for secret in (KEY_CANARY, TOKEN_VALUE, REF_TEXT):
        assert secret not in everything
    assert sim_mod("errors") is not None


class SecretCarrier(Exception):
    """An exception that carries every secret in its message, its arguments and an attribute."""

    def __init__(self, *secrets: str) -> None:
        super().__init__("failed with " + " ".join(secrets), *secrets)
        self.secrets = secrets


@pytest.mark.parametrize(
    "failure",
    ["carrier", "runtime_error", "keyboard_interrupt", "store_carrier", "report_carrier"],
)
def test_key_canaries_never_in_a_crash_after_a_paid_request(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """The last channel: a failure of the command itself after the two TTS requests were paid (while
    connecting, while storing the run, or while writing the report). An
    exception whose message, arguments and attributes hold the key, the bearer token and the
    authorisation text must leave only its class name on stderr and in the recovery file (ADR 0024
    rule 16 (d), ADR 0026 rule 14); the usage of the paid requests is kept; no channel holds a
    secret. A ``KeyboardInterrupt`` (which carries no text) is raised again after the file is
    written."""
    caplog.set_level(logging.DEBUG)
    files = write_inputs(
        tmp_path,
        sim_profile=profile_doc(tts="sarvam", stt="sarvam"),
        url="ws://127.0.0.1:8765/ws-client",
        allowlist_line=f"ws://127.0.0.1:8765/ws-client auth_token_env={TOKEN_ENV}",
    )
    secrets = (KEY_CANARY, TOKEN_VALUE, REF_TEXT)
    connecting = ("carrier", "runtime_error", "keyboard_interrupt")

    async def crash(request: Any) -> Any:
        if failure == "keyboard_interrupt":
            raise KeyboardInterrupt  # as a Ctrl-C does: it carries no text
        if failure == "runtime_error":
            raise RuntimeError("boom " + " ".join(secrets))
        raise SecretCarrier(*secrets)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise SecretCarrier(*secrets)

    if failure == "store_carrier":
        monkeypatch.setattr(importlib.import_module("tracer_cli.store"), "store_run", broken)
    if failure == "report_carrier":
        monkeypatch.setattr(
            importlib.import_module("tracer_cli.simulate"), "render_sim_report", broken
        )
    sim = SarvamSim()
    kwargs: dict[str, Any] = {
        "args": real_args(files, target=True),
        "target": True,
        "environ": real_env(**{TOKEN_ENV: TOKEN_VALUE}),
        "transport": sim,
        "world_kw": {"connect": crash} if failure in connecting else {},
    }
    out = err = ""
    if failure == "keyboard_interrupt":
        with pytest.raises(KeyboardInterrupt):
            run_sim(files, **kwargs)
    else:
        run = run_sim(files, **kwargs)
        out, err = run.out, run.err
        assert run.code == 1
        expected = "RuntimeError" if failure == "runtime_error" else "SecretCarrier"
        assert err == f"error: unexpected failure ({expected})\n"
    recoveries = list(files.out.glob("usage-recovery-*.json"))
    if failure == "report_carrier":  # the run was stored: nothing is lost, so no recovery file
        assert recoveries == []
        assert run.value("store").endswith("tracer.sqlite")
        assert run.value("report") == "not written"
    else:
        (recovery,) = recoveries
        document = json.loads(recovery.read_text("utf-8"))
        assert document["failed_step"] == ("store" if failure == "store_carrier" else "call")
        assert document["error_class"] in ("RuntimeError", "SecretCarrier", "KeyboardInterrupt")
        stages = [u["stage"] for u in document["usage_records"]]
        assert stages[:2] == ["sim_caller_tts", "sim_caller_tts"]
        assert len(sim.bodies("/text-to-speech")) == 2
    gc.collect()  # an unretrieved task logs its exception when it is collected: now, not in a later test
    everything = out + err + all_text(files.out) + caplog.text
    for secret in secrets:
        assert secret not in everything, (failure, secret)
    if failure in ("carrier", "runtime_error"):
        assert out.startswith("usage recovery: ")
