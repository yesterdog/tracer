# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Several roles, one BYOK variable, and the key canary through the new adapters (contract
docs/phase4/api-surface-batch-b.md section 10; ADR 0024 amendments A1 and A4, invariant IA2; ADR
0007 Decision 1).

Sarvam's STT, TTS and LLM adapters all read ``SARVAM_API_KEY`` (one key, three roles); each role
holds the key in its own redacting wrapper and fails with its own error class; ``--env-file`` is
read for exactly that name. The key never leaves the request header: it is in no exception (str,
repr, args, vars, chain), no log record at DEBUG, no call-log line, no usage record, no cache
file. ``test_key_canaries_never_in_any_output_channel`` (P4-30) extends this to the store and
the report; this file is the adapter level.
"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    LlmRig,
    SimRun,
    TtsRig,
    cache_mod,
    chat_mod,
    chat_reply,
    core_llm,
    core_tts,
    llm_context,
    llm_request,
    secrets_mod,
    simlog_mod,
    tts_context,
    tts_mod,
    tts_reply,
    tts_request,
)
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    MESSAGE_CANARY,
    TEXT_CANARY,
    ScriptedTransport,
    chain,
    error_body,
    json_response,
    small_plan,
    stt,
)

ROLES = ["stt", "tts", "llm"]


def _error_class(role: str) -> Any:
    return {
        "stt": stt().SttKeyMissingError,
        "tts": core_tts().TtsKeyMissingError,
        "llm": core_llm().LlmKeyMissingError,
    }[role]


def _echoing(request: httpx.Request) -> str:
    return request.headers["api-subscription-key"]


# ------------------------------------------------------------------- one variable, three roles


def test_every_sarvam_role_reads_the_one_variable() -> None:
    """A4: one key, three roles; the variable name is a constant of the adapter."""
    s = secrets_mod()
    assert dict(s.ENV_VAR_NAMES) == {
        "stt": "SARVAM_API_KEY",
        "tts": "SARVAM_API_KEY",
        "llm": "SARVAM_API_KEY",
    }
    assert s.ENV_VAR_NAME == "SARVAM_API_KEY"
    keys = []
    for role in ROLES:
        key = s.load_role_key(role, environ={"SARVAM_API_KEY": CANARY_KEY})
        assert isinstance(key, s.ApiKey)
        assert key.reveal() == CANARY_KEY
        assert str(key) == s.PLACEHOLDER
        assert CANARY_KEY not in repr(key)
        keys.append(key)
    assert len({id(k) for k in keys}) == 3, "each role holds the key in its own wrapper"


def test_every_sarvam_role_records_the_name_of_the_key_never_its_value() -> None:
    """ADR 0007 Decision 1: ``ProviderConfigRef`` holds ``env:SARVAM_API_KEY`` for every model."""
    s = secrets_mod()
    for model in ("saaras:v4", "bulbul:v3", "sarvam-105b", "deepseekv4.1-flash"):
        ref = s.provider_config_ref(model)
        assert (ref.provider, ref.model, ref.config_ref) == ("sarvam", model, "env:SARVAM_API_KEY")
        assert CANARY_KEY not in ref.model_dump_json()


@pytest.mark.parametrize("role", ROLES)
def test_a_missing_or_unusable_key_is_that_roles_own_error(tmp_path: Path, role: str) -> None:
    """ADR 0024 A4: each role refuses a missing, empty, ambiguous or unusable key with its own
    error class and a reason key, quoting nothing."""
    s = secrets_mod()
    cases: list[tuple[dict[str, str], Path | None, str]] = [
        ({}, None, "key_missing"),
        ({"SARVAM_API_KEY": ""}, None, "key_missing"),
        ({"SARVAM_API_KEY": " \t "}, None, "key_missing"),
        ({"SARVAM_API_KEY": "has a space"}, None, "key_invalid"),
        ({"OTHER_API_KEY": CANARY_KEY, "TTS_API_KEY": CANARY_KEY}, None, "key_missing"),
    ]
    both = tmp_path / "two.env"
    both.write_text(f"SARVAM_API_KEY={CANARY_KEY}\nSARVAM_API_KEY={CANARY_KEY}\n", "ascii")
    cases.append(({}, both, "key_ambiguous"))
    directory = tmp_path / "dir.env"
    directory.mkdir()
    cases.append(({}, directory, "env_file_not_regular"))
    cases.append(({}, tmp_path / "missing.env", "env_file_unreadable"))
    big = tmp_path / "big.env"
    big.write_bytes(b"#" * (s.ENV_FILE_MAX_BYTES + 1))
    cases.append(({}, big, "env_file_too_large"))
    for environ, env_file, reason in cases:
        with pytest.raises(_error_class(role)) as raised:
            s.load_role_key(role, environ=environ, env_file=env_file)
        error = raised.value
        assert type(error) is _error_class(role), (role, reason)
        assert error.reason == reason
        assert chain(error) == [error]
        for form in (str(error), repr(error), repr(vars(error))):
            assert CANARY_KEY not in form
            assert str(tmp_path) not in form
            assert "two.env" not in form


def test_the_env_file_rules_of_phase_3_hold_for_every_role(tmp_path: Path) -> None:
    """ADR 0024 rule 4 holds for every role: the file is the only source, nothing is exported, a
    loose mode earns one warning."""
    s = secrets_mod()
    for role in ROLES:
        path = tmp_path / f"{role}.env"
        path.write_text(f"OTHER=OTHERCANARY\nexport SARVAM_API_KEY='{CANARY_KEY}'\nZ=1\n", "ascii")
        path.chmod(0o600)
        key = s.load_role_key(
            role, environ={"SARVAM_API_KEY": "from-the-environment"}, env_file=path
        )
        assert key.reveal() == CANARY_KEY, "with a file the file is the only source"
        assert "SARVAM_API_KEY" not in os.environ, "nothing is exported"
        loose = tmp_path / f"{role}.loose.env"
        loose.write_text(f"SARVAM_API_KEY={CANARY_KEY}\n", "ascii")
        loose.chmod(0o644)
        with pytest.warns(s.EnvFileModeWarning) as warned:
            s.load_role_key(role, environ={}, env_file=loose)
        assert len(warned) == 1
        assert CANARY_KEY not in str(warned[0].message)
        assert str(tmp_path) not in str(warned[0].message)


def test_the_role_must_be_one_of_the_three() -> None:
    """An unknown role is a programming error, not a key problem."""
    s = secrets_mod()
    for bad in ("", "judge", "STT", "telephony"):
        with pytest.raises(ValueError, match="role"):
            s.load_role_key(bad, environ={"SARVAM_API_KEY": CANARY_KEY})
    with pytest.raises(TypeError):
        s.load_role_key(None, environ={"SARVAM_API_KEY": CANARY_KEY})


def test_phase_3_load_api_key_is_unchanged() -> None:
    """The Phase 3 loader and its error class are unchanged."""
    key = secrets_mod().load_api_key(environ={"SARVAM_API_KEY": CANARY_KEY})
    assert key.reveal() == CANARY_KEY
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().load_api_key(environ={})


# ----------------------------------------------------------------------- the factories


def _log(tmp_path: Path) -> Any:
    return simlog_mod().SimCallLog.open(tmp_path / "out")


def test_the_tts_factory_reads_the_key_and_sends_it_in_the_header_only(tmp_path: Path) -> None:
    """ADR 0026 rule 14 step 6: the factory reads the key and the engine sends it in one header."""
    log = _log(tmp_path)
    transport = ScriptedTransport([tts_reply()])
    engine = tts_mod().create_sarvam_tts(
        call_log=log,
        cap_characters=None,
        environ={"SARVAM_API_KEY": CANARY_KEY},
        transport=transport,
    )
    try:
        assert engine.synthesise(tts_request(0), tts_context()).failure is None
        assert transport.requests[0].headers["api-subscription-key"] == CANARY_KEY
        assert CANARY_KEY not in repr(engine) + str(engine) + repr(vars(engine))
    finally:
        engine.close()
        log.close()


def test_the_llm_factory_reads_the_key_and_sends_it_in_the_header_only(tmp_path: Path) -> None:
    """ADR 0026 rule 14 step 6: the factory reads the key and the engine sends it in one header."""
    log = _log(tmp_path)
    transport = ScriptedTransport([chat_reply()])
    engine = chat_mod().create_sarvam_llm(
        call_log=log,
        cap_tokens=None,
        environ={"SARVAM_API_KEY": CANARY_KEY},
        transport=transport,
    )
    try:
        assert engine.complete(llm_request(0), llm_context()).failure is None
        assert transport.requests[0].headers["api-subscription-key"] == CANARY_KEY
        assert CANARY_KEY not in repr(engine) + str(engine) + repr(vars(engine))
    finally:
        engine.close()
        log.close()


def test_the_factories_take_the_key_from_an_env_file_and_from_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both key sources work for both factories; ``environ`` is a parameter so tests never touch
    the machine."""
    log = _log(tmp_path)
    path = tmp_path / "k.env"
    path.write_text(f"SARVAM_API_KEY={CANARY_KEY}\n", "ascii")
    path.chmod(0o600)
    t1, t2 = ScriptedTransport([tts_reply()]), ScriptedTransport([chat_reply()])
    tts = tts_mod().create_sarvam_tts(
        call_log=log, cap_characters=None, env_file=path, transport=t1
    )
    llm = chat_mod().create_sarvam_llm(
        call_log=log, cap_tokens=None, env_file=path, environ={}, transport=t2
    )
    tts.synthesise(tts_request(0), tts_context())
    llm.complete(llm_request(0), llm_context())
    assert t1.requests[0].headers["api-subscription-key"] == CANARY_KEY
    assert t2.requests[0].headers["api-subscription-key"] == CANARY_KEY
    monkeypatch.setenv("SARVAM_API_KEY", CANARY_KEY)
    t3 = ScriptedTransport([tts_reply()])
    default = tts_mod().create_sarvam_tts(call_log=log, cap_characters=None, transport=t3)
    default.synthesise(tts_request(0), tts_context())
    assert t3.requests[0].headers["api-subscription-key"] == CANARY_KEY
    for engine in (tts, llm, default):
        engine.close()
    log.close()


def test_a_missing_key_refuses_before_any_request_and_writes_nothing(tmp_path: Path) -> None:
    """A4: a missing or empty variable for a selected real engine refuses the run before any
    request: the factory raises that role's error and the shared log holds nothing."""
    log = _log(tmp_path)
    transport = ScriptedTransport([])
    with pytest.raises(core_tts().TtsKeyMissingError) as one:
        tts_mod().create_sarvam_tts(
            call_log=log, cap_characters=None, environ={"SARVAM_API_KEY": ""}, transport=transport
        )
    with pytest.raises(core_llm().LlmKeyMissingError) as two:
        chat_mod().create_sarvam_llm(call_log=log, cap_tokens=None, environ={}, transport=transport)
    for error in (one.value, two.value):
        assert error.reason == "key_missing"
        assert chain(error) == [error]
    assert transport.requests == []
    assert log.path.read_text("ascii") == ""
    log.close()


# -------------------------------------------------------- the canary through every channel


def _failure_steps(echo_into: str) -> dict[str, list[Any]]:
    """Every failure path carries the key into whatever the library or the server builds."""

    def raising(error_type: type[httpx.RequestError]) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            raise error_type(f"failed for {_echoing(request)}", request=request)

        return handler

    def echo(code: int) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            key = _echoing(request)
            body = error_body("invalid_api_key_error", message=f"bad key {key} {MESSAGE_CANARY}")
            return json_response(body, status=code, headers={"x-echo": key})  # type: ignore[no-any-return,unused-ignore]

        return handler

    def echo_body(request: httpx.Request) -> httpx.Response:
        return json_response(  # type: ignore[no-any-return,unused-ignore]
            {echo_into: 5, "text": f"{_echoing(request)} {TEXT_CANARY}"}
        )

    return {
        "connect_error": [raising(httpx.ConnectError)] * 4,
        "read_timeout": [raising(httpx.ReadTimeout)] * 4,
        "other_request_error": [raising(httpx.RequestError)] * 4,
        "403": [echo(403)],
        "400": [echo(400)],
        "429": [echo(429)] * 4,
        "503": [echo(503)] * 4,
        "invalid_200": [echo_body],
        "redirect": [
            lambda r: httpx.Response(302, headers={"location": f"https://x/{_echoing(r)}"})
        ],
    }


def _forms(error: BaseException) -> list[str]:
    return [str(error), repr(error), repr(error.args), repr(vars(error))]


@pytest.mark.parametrize("kind", ["tts", "llm"])
def test_the_key_never_appears_in_a_failure_its_chain_the_engine_or_the_call_log(
    tmp_path: Path, kind: str
) -> None:
    """ADR 0024 rule 5 / IA2 at the adapter: nine hostile failure paths echo the key and none of
    its forms keeps it."""
    for name, steps in _failure_steps("request_id" if kind == "tts" else "id").items():
        rig: Any = (
            TtsRig(tmp_path / name, steps) if kind == "tts" else LlmRig(tmp_path / name, steps)
        )
        outcome = rig.call()
        failure = outcome.failure
        assert failure is not None, name
        assert chain(failure) == [failure], f"{name}: no cause, no context"
        for form in _forms(failure):
            assert CANARY_KEY not in form, name
            assert MESSAGE_CANARY not in form, name
        for form in (repr(outcome), repr(rig.engine), str(rig.engine), repr(vars(rig.engine))):
            assert CANARY_KEY not in form, name
        assert CANARY_KEY not in rig.log_text, name


@pytest.mark.parametrize("kind", ["tts", "llm"])
def test_the_key_never_appears_in_records_logs_or_files_of_a_good_run(
    tmp_path: Path, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0024 IA2: no usage record, DEBUG log record, call-log line or cache file holds the
    key."""
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.http11", "tracer_evalproviders", "tracer_evalcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    out = tmp_path / "out"
    if kind == "tts":
        cache = cache_mod().TtsCache.open(out)
        steps: list[Any] = [
            tts_reply("ok-1"),
            httpx.ConnectError("x"),
            lambda r: json_response(  # type: ignore[no-any-return,unused-ignore]
                error_body(message=_echoing(r)), status=503, headers={"x-echo": _echoing(r)}
            ),
            tts_reply("ok-2"),
        ]
        rig: Any = TtsRig(tmp_path, steps, cache=cache)
        outcomes = [rig.call(tts_request(0, "one")), rig.call(tts_request(1, "two"))]
    else:
        steps = [
            chat_reply(reply_id="ok-1"),
            httpx.ConnectError("x"),
            lambda r: json_response(  # type: ignore[no-any-return,unused-ignore]
                error_body(message=_echoing(r)), status=503, headers={"x-echo": _echoing(r)}
            ),
            chat_reply(reply_id="ok-2"),
        ]
        rig = LlmRig(tmp_path, steps)
        outcomes = [rig.call(llm_request(0)), rig.call(llm_request(1))]
    assert all(o.failure is None for o in outcomes)
    assert any(r.name == "httpx" for r in caplog.records), "the test saw the httpx request log"
    for record in caplog.records:
        haystack = [record.getMessage(), str(record.msg), repr(record.args), record.exc_text or ""]
        haystack += [repr(v) for v in vars(record).values()]
        if record.exc_info:
            haystack.append("".join(traceback.format_exception(*record.exc_info)))
        for text in haystack:
            assert CANARY_KEY not in text, record.name
    assert CANARY_KEY not in caplog.text
    for outcome in outcomes:
        for usage in outcome.usage:
            assert CANARY_KEY not in usage.model_dump_json()
            assert CANARY_KEY not in repr(usage)
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file():
            assert CANARY_KEY.encode() not in path.read_bytes(), path.name
    for request in rig.transport.requests:
        assert request.headers["api-subscription-key"] == CANARY_KEY
        assert CANARY_KEY.encode() not in request.content


def test_the_key_never_appears_in_a_shared_log_of_the_three_engines(tmp_path: Path) -> None:
    """One log, three engines, three auth failures that echo the key: the log holds none."""
    run = SimRun(
        tmp_path,
        tts=[lambda r: json_response(error_body(message=_echoing(r)), status=403)],  # type: ignore[no-any-return,unused-ignore]
        llm=[lambda r: json_response(error_body(message=_echoing(r)), status=403)],  # type: ignore[no-any-return,unused-ignore]
        stt=[lambda r: json_response(error_body(message=_echoing(r)), status=403)],  # type: ignore[no-any-return,unused-ignore]
    )
    assert run.tts.synthesise(tts_request(0), tts_context()).failure is not None
    assert run.llm.complete(llm_request(0), llm_context()).failure is not None
    with pytest.raises(stt().SttAuthError):
        run.stt_client.send_chunk(small_plan().chunks[0])
    assert CANARY_KEY not in run.log_text
