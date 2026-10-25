# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-08: key handling and redaction (contract docs/phase3/api-surface-batch-b.md section 6; ADR
0024 rules 4 and 5, invariant I3).

The key in every test is the canary ``CANARY_KEY``, built from parts; it is not a key of any
account, and no test reads ``.env.local`` or the environment of the machine for a key. The canary
is searched for in every channel that must not hold it.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import pickle
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MESSAGE_CANARY,
    TEXT_CANARY,
    Rig,
    ScriptedTransport,
    chain,
    context,
    engine_mod,
    error_body,
    json_response,
    ok_body,
    secrets_mod,
    small_plan,
    stt,
)


def _forms(error: BaseException) -> list[str]:
    """Every textual form of an exception and of everything it is linked to."""
    forms = []
    for item in chain(error):
        forms += [str(item), repr(item), repr(item.args), repr(getattr(item, "__dict__", {}))]
        forms += list(getattr(item, "__notes__", []))
        forms.append("".join(traceback.format_exception(type(item), item, item.__traceback__)))
    return forms


# ------------------------------------------------------------------------- the wrapper


def test_key_wrapper_prints_a_fixed_placeholder_everywhere() -> None:
    key = secrets_mod().ApiKey(CANARY_KEY)
    other = secrets_mod().ApiKey("a-completely-different-key-value")
    placeholder = secrets_mod().PLACEHOLDER
    assert CANARY_KEY not in placeholder
    assert placeholder
    forms = [
        str(key),
        repr(key),
        format(key),
        format(key, ">40"),
        f"{key}",
        f"{key!r}",
        f"{key:>30}",
        "%s" % key,  # noqa: UP031
        "%r" % key,  # noqa: UP031
        f"{key}",
        "{!r:>50}".format(key),  # noqa: UP032
        repr([key]),
        repr({"k": key}),
        repr((key,)),
        str({key}),
    ]
    for form in forms:
        assert CANARY_KEY not in form
        assert CANARY_KEY[:6] not in form, "no part of the key"
        assert CANARY_KEY[-6:] not in form, "no part of the key"
    # the same text for any key: nothing about this key can be read from it
    assert str(other) == str(key)
    assert repr(other) == repr(key)
    assert format(other, ">40") == format(key, ">40")
    assert key.reveal() == CANARY_KEY, "the clear value is read through reveal() only"
    assert not hasattr(key, "__dict__")
    with pytest.raises(AttributeError):
        key.reveal = lambda: "x"
    with pytest.raises(AttributeError):
        key._value = "x"
    for operation in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            operation(key)
    with pytest.raises(TypeError):
        json.dumps({"key": key})
    assert json.dumps({"key": key}, default=str).count(CANARY_KEY) == 0


@pytest.mark.parametrize(
    "value",
    ["", " ", "\t\n", "a b", "a\nb", "a\r\nX-Evil: 1", "k\u00e9y", "k\x00y", "\u202ekey", "a\tb"],
)
def test_key_wrapper_refuses_an_unusable_value_without_quoting_it(value: str) -> None:
    with pytest.raises(stt().SttKeyMissingError) as raised:
        secrets_mod().ApiKey(value)
    assert type(raised.value) is stt().SttKeyMissingError
    for form in _forms(raised.value):
        assert value.strip() == "" or value not in form
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().ApiKey(None)
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().ApiKey(b"key")


def test_key_header_injection_is_refused_at_load() -> None:
    poisoned = CANARY_KEY + "\r\nX-Evil: 1"
    with pytest.raises(stt().SttKeyMissingError) as raised:
        secrets_mod().load_api_key(environ={"SARVAM_API_KEY": poisoned})
    for form in _forms(raised.value):
        assert CANARY_KEY not in form
        assert "X-Evil" not in form


# ---------------------------------------------------------------- exceptions and the chain


def _echoing(request: httpx.Request) -> str:
    return request.headers["api-subscription-key"]


def _failure_rigs(tmp_path: Path) -> list[tuple[str, Rig]]:
    """Rigs whose every failure path carries the key into the error that the library or the
    server builds: the error text, the request, a header and the body all hold the canary."""

    def raising(error_type: type[httpx.RequestError]) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            raise error_type(f"failed for {_echoing(request)}", request=request)

        return handler

    def echo(status: int) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            key = _echoing(request)
            body = error_body("invalid_api_key_error", message=f"bad key {key} {MESSAGE_CANARY}")
            return json_response(  # type: ignore[no-any-return,unused-ignore]
                body, status=status, headers={"x-echo": key}
            )

        return handler

    def echo_body(request: httpx.Request) -> httpx.Response:
        return json_response(  # type: ignore[no-any-return,unused-ignore]
            ok_body(text=f"{_echoing(request)} {TEXT_CANARY}") | {"transcript": 5}
        )

    rigs = {
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
    return [(name, Rig(tmp_path / name, steps)) for name, steps in rigs.items()]


def test_key_never_in_exception_str_repr_or_chain(tmp_path: Path) -> None:
    chunk = small_plan().chunks[0]
    rigs = _failure_rigs(tmp_path)
    assert len(rigs) == 9
    for name, rig in rigs:
        with pytest.raises(stt().SttError) as raised:
            rig.client.send_chunk(chunk)
        error = raised.value
        assert chain(error) == [error], f"{name}: no cause, no context"
        for form in _forms(error):
            assert CANARY_KEY not in form, name
            assert MESSAGE_CANARY not in form, name
        assert CANARY_KEY not in rig.log_text, name
        # the client object itself shows no key
        for form in (repr(rig.client), str(rig.client), repr(rig.client.__dict__)):
            assert CANARY_KEY not in form, name


def test_key_never_in_exception_str_repr_or_chain_engine_failure_outcome(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        [
            json_response(ok_body()),
            lambda r: json_response(error_body(message=_echoing(r)), status=403),
        ],
    )
    outcome = rig.engine().transcribe(small_plan(), context("mr"))
    assert outcome.failure is not None
    assert outcome.transcript is None
    for form in [*_forms(outcome.failure), repr(outcome)]:
        assert CANARY_KEY not in form
    for usage in outcome.usage:
        assert CANARY_KEY not in usage.model_dump_json()


# ------------------------------------------------------------------------------ logging


def test_key_never_in_logs_at_debug(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.http11", "tracer_evalproviders", "tracer_evalcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    plan = small_plan()
    steps: list[Any] = [
        json_response(ok_body(request_id="log-1")),
        httpx.ConnectError("x"),
        lambda r: json_response(
            error_body(message=_echoing(r)), status=503, headers={"x-echo": _echoing(r)}
        ),
        json_response(ok_body(request_id="log-2")),
        lambda r: json_response(
            error_body("invalid_api_key_error", message=_echoing(r)), status=403
        ),
    ]
    rig = Rig(tmp_path, steps)
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is not None
    assert any(r.name == "httpx" for r in caplog.records), "the test saw the httpx request log"
    for record in caplog.records:
        haystack = [record.getMessage(), str(record.msg), repr(record.args), record.exc_text or ""]
        haystack += [repr(v) for v in vars(record).values()]
        if record.exc_info:
            haystack.append("".join(traceback.format_exception(*record.exc_info)))
        for text in haystack:
            assert CANARY_KEY not in text, record.name
    assert CANARY_KEY not in caplog.text


def test_key_never_in_logs_at_debug_loading_the_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    env_file = tmp_path / "keys.env"
    env_file.write_text(f"OTHER=OTHERCANARY\nSARVAM_API_KEY={CANARY_KEY}\n", "ascii")
    secrets_mod().load_api_key(environ={}, env_file=env_file)
    secrets_mod().load_api_key(environ={"SARVAM_API_KEY": CANARY_KEY})
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().load_api_key(environ={}, env_file=tmp_path / "missing.env")
    for text in (caplog.text, *[r.getMessage() for r in caplog.records]):
        assert CANARY_KEY not in text
        assert "OTHERCANARY" not in text


# ------------------------------------------------------------- records, call log, files


def test_key_never_in_call_log_store_or_report(tmp_path: Path) -> None:
    plan = small_plan()
    steps: list[Any] = [
        json_response(ok_body(request_id="a-1", text=f"hello {TEXT_CANARY}")),
        json_response(ok_body(request_id="a-2", phrases=[("p", 0.0, 0.5)])),
        json_response(ok_body(request_id="a-3")),
    ]
    rig = Rig(tmp_path, steps)
    outcome = rig.engine().transcribe(plan, context("mr"))
    assert outcome.failure is None
    assert outcome.transcript is not None
    documents = [outcome.transcript, *outcome.turns, *outcome.usage, *outcome.warnings]
    for record in documents:
        dumped = record.model_dump_json()
        assert CANARY_KEY not in dumped
        assert CANARY_KEY not in repr(record)
    assert [
        flag
        for record in documents
        if hasattr(record, "metadata_flags")
        for flag in record.metadata_flags
    ] == []
    # every file the run left behind, byte for byte
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file():
            assert CANARY_KEY.encode() not in path.read_bytes(), path.name
    assert CANARY_KEY not in rig.log_text
    # the key shown to the provider is the only place it was ever put
    for request in rig.transport.requests:
        assert request.headers["api-subscription-key"] == CANARY_KEY
        assert CANARY_KEY.encode() not in request.content


def test_key_never_in_call_log_store_or_report_cost_and_dry_run(tmp_path: Path) -> None:
    plan = small_plan()
    rig = Rig(tmp_path, [json_response(ok_body()) for _ in range(3)])
    outcome = rig.engine().transcribe(plan, context("mr"))
    from evalproviders_support import UTC_NOW, cost_mod, dryrun_mod

    cost = cost_mod().compute_stt_cost(outcome.usage, catalog=None, created_at=UTC_NOW)
    for record in (*cost.records, cost.summary):
        assert CANARY_KEY not in record.model_dump_json()
    dry = dryrun_mod().build_dry_run_plan(plan, model="saaras:v4")
    assert CANARY_KEY not in dry.render()
    assert CANARY_KEY not in repr(dry)


# ------------------------------------------------------------------------ the env file

ENV_FILE_FORMS = [
    ("plain", "SARVAM_API_KEY={k}\n"),
    ("export", "export SARVAM_API_KEY={k}\n"),
    ("double quotes", 'SARVAM_API_KEY="{k}"\n'),
    ("single quotes", "SARVAM_API_KEY='{k}'\n"),
    ("crlf", "SARVAM_API_KEY={k}\r\n"),
    ("no final newline", "SARVAM_API_KEY={k}"),
    ("trailing blanks", "SARVAM_API_KEY={k}   \t\n"),
    ("between other lines", "# a comment\n\nOTHER_SECRET=OTHERCANARY\nSARVAM_API_KEY={k}\nZ=1\n"),
    (
        "garbage around",
        "NOT A VALID LINE !!!\n=novar\n   \nSARVAM_API_KEY_BACKUP=wrong\nXSARVAM_API_KEY=wrong\n"
        "SARVAM_API_KEY={k}\nexport 2BAD=x\nXSARVAM_API_KEY=wrong\nSARVAM_API_KEY_BACKUP=wrong\n"
        "# SARVAM_API_KEY=commented\nxSARVAM_API_KEY=wrong\n",
    ),
]


@pytest.mark.parametrize(("label", "template"), ENV_FILE_FORMS, ids=[f[0] for f in ENV_FILE_FORMS])
def test_env_file_reads_only_named_variable(tmp_path: Path, label: str, template: str) -> None:
    env_file = tmp_path / "keys.env"
    env_file.write_bytes(template.format(k=CANARY_KEY).encode("ascii"))
    before = dict(os.environ)
    key = secrets_mod().load_api_key(environ={}, env_file=env_file)
    assert key.reveal() == CANARY_KEY, label
    assert dict(os.environ) == before, "nothing is exported into the process environment"
    assert "SARVAM_API_KEY" not in os.environ


def test_env_file_reads_only_named_variable_other_lines_are_never_parsed(tmp_path: Path) -> None:
    env_file = tmp_path / "keys.env"
    body = (
        b"OTHER_SECRET=OTHERCANARY\n\xff\xfe\x00 binary \x80 junk\n"
        b"SARVAM_API_KEY=" + CANARY_KEY.encode() + b"\n"
        b"\xc3\x28 broken utf-8 line\n"
    )
    env_file.write_bytes(body)
    assert secrets_mod().load_api_key(environ={}, env_file=env_file).reveal() == CANARY_KEY


def test_env_file_value_is_kept_whole(tmp_path: Path) -> None:
    env_file = tmp_path / "keys.env"
    value = "ab=cd#ef/gh+ij"
    env_file.write_text(f"SARVAM_API_KEY={value}\n", "ascii")
    assert secrets_mod().load_api_key(environ={}, env_file=env_file).reveal() == value


def test_env_file_wins_over_the_environment_and_does_not_fall_back(tmp_path: Path) -> None:
    env_file = tmp_path / "keys.env"
    env_file.write_text(f"SARVAM_API_KEY={CANARY_KEY}\n", "ascii")
    other = "ENVIRONMENT-KEY-must-not-be-used"
    key = secrets_mod().load_api_key(environ={"SARVAM_API_KEY": other}, env_file=env_file)
    assert key.reveal() == CANARY_KEY
    empty = tmp_path / "empty.env"
    empty.write_text("OTHER=1\n", "ascii")
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().load_api_key(environ={"SARVAM_API_KEY": other}, env_file=empty)


def test_env_file_errors_quote_no_line_and_no_path(tmp_path: Path) -> None:
    env_file = tmp_path / "secret-folder-name.env"
    env_file.write_text("OTHER_SECRET=OTHERCANARY\nGARBAGELINE here\n", "ascii")
    cases = [env_file, tmp_path / "nothing-here.env", tmp_path]
    for case in cases:
        with pytest.raises(stt().SttKeyMissingError) as raised:
            secrets_mod().load_api_key(environ={}, env_file=case)
        assert chain(raised.value) == [raised.value], "no OSError is kept"
        for form in _forms(raised.value):
            for text in (
                "OTHERCANARY",
                "GARBAGELINE",
                "secret-folder-name",
                "nothing-here",
                str(tmp_path),
            ):
                assert text not in form, (case.name, text)


@pytest.mark.parametrize(
    ("label", "environ", "env_text"),
    [
        ("no variable", {}, None),
        ("empty variable", {"SARVAM_API_KEY": ""}, None),
        ("blank variable", {"SARVAM_API_KEY": " \t "}, None),
        ("only another variable", {"OTHER": "x"}, None),
        ("empty in file", {}, "SARVAM_API_KEY=\n"),
        ("blank in file", {}, "SARVAM_API_KEY=   \n"),
        ("empty quotes in file", {}, 'SARVAM_API_KEY=""\n'),
        ("variable missing in file", {}, "OTHER=1\n"),
        ("empty file", {}, ""),
    ],
)
def test_missing_key_refuses_before_any_request(
    tmp_path: Path, label: str, environ: dict[str, str], env_text: str | None
) -> None:
    transport = ScriptedTransport([json_response(ok_body())])
    out = tmp_path / "out"
    kwargs: dict[str, Any] = {"out_dir": out, "environ": environ, "transport": transport}
    if env_text is not None:
        env_file = tmp_path / "k.env"
        env_file.write_text(env_text, "ascii")
        kwargs["env_file"] = env_file
    with pytest.raises(stt().SttKeyMissingError) as raised:
        engine_mod().create_sarvam_stt(**kwargs)
    assert type(raised.value) is stt().SttKeyMissingError, label
    assert isinstance(raised.value, stt().SttConfigurationError)
    assert transport.requests == [], "stopped before any request"
    assert not out.exists(), "and before any file is created"
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().load_api_key(environ=environ, env_file=kwargs.get("env_file"))


def test_key_is_read_from_the_environment_when_no_file_is_named() -> None:
    key = secrets_mod().load_api_key(environ={"SARVAM_API_KEY": f"  {CANARY_KEY}  \n"})
    assert key.reveal() == CANARY_KEY, "surrounding white space is not part of the key"
    assert secrets_mod().ENV_VAR_NAME == "SARVAM_API_KEY"


def test_key_default_environment_is_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", CANARY_KEY)
    assert secrets_mod().load_api_key().reveal() == CANARY_KEY
    monkeypatch.delenv("SARVAM_API_KEY")
    with pytest.raises(stt().SttKeyMissingError):
        secrets_mod().load_api_key()


def test_key_is_recorded_as_a_name_never_a_value(tmp_path: Path) -> None:
    ref = secrets_mod().provider_config_ref("saaras:v4")
    dumped = ref.model_dump(mode="json")
    assert dumped == {
        "provider": "sarvam",
        "model": "saaras:v4",
        "config_ref": "env:SARVAM_API_KEY",
    }
    assert secrets_mod().CONFIG_REF == "env:SARVAM_API_KEY"
    # the same, whichever file the key came from: the path is not an input
    import inspect

    assert list(inspect.signature(secrets_mod().provider_config_ref).parameters) == ["model"]
    assert CANARY_KEY not in json.dumps(dumped)
