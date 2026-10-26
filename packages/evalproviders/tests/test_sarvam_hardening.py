# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch G, the adapter hardening of the P3-28 security audit (findings F3, F7, F9 and F10).

Contract: docs/phase3/api-surface-batch-g.md sections 6 to 9. Nothing here opens a socket: the
client is built over an injected transport, or built with no transport at all (httpx creates no
connection until a request is sent) and inspected. The only key is the canary of the batch B
support file. Files and directories are created under ``tmp_path``; nothing reads the machine's
environment or a private fixture.

- F3: the client never reads the proxy or certificate environment (``trust_env=False``).
- F10: every request asks for an uncompressed reply (``Accept-Encoding: identity``).
- F7: the call log directory is created private and the log is never opened through a link.
- F9: the env file is read only if it is a regular file of at most 64 KiB, and a file that other
  users can read earns a warning that names neither the file nor the key.
"""

from __future__ import annotations

import ast
import errno
import os
import ssl
import stat
import threading
import warnings
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    SRC,
    Rig,
    WallClock,
    calllog_mod,
    client_mod,
    json_response,
    ok_body,
    secrets_mod,
    small_plan,
    stt,
)

PROXY_AND_CA_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def _ok(request_id: str = "r-ok") -> httpx.Response:
    return json_response(ok_body(request_id=request_id))  # type: ignore[no-any-return,unused-ignore]


def _hostile_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every variable httpx would honour with ``trust_env=True``, pointing nowhere."""
    for name in PROXY_AND_CA_VARIABLES:
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "no-such-ca-file.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "no-such-ca-dir"))
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


def _client(tmp_path: Path, transport: httpx.BaseTransport | None) -> tuple[Any, Any]:
    log = calllog_mod().CallLog.open(tmp_path / "out", clock=WallClock())
    client = client_mod().SarvamClient(
        secrets_mod().ApiKey(CANARY_KEY), call_log=log, transport=transport
    )
    return client, log


# ---------------------------------------------------------------------------------------- F3


def test_audit_f3_client_never_reads_the_proxy_or_certificate_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hostile_environment(monkeypatch, tmp_path)
    # with trust_env left on, httpx would try to load SSL_CERT_FILE here and raise
    # FileNotFoundError, and it would mount a proxy transport for the proxy variables
    client, log = _client(tmp_path, None)
    try:
        http = client._http
        assert http.trust_env is False
        assert http._mounts == {}, "a proxy from the environment was mounted"
        transport = http._transport
        assert type(transport).__name__ == "HTTPTransport"
        assert "Proxy" not in type(transport._pool).__name__
    finally:
        client.close()
        log.close()


def test_audit_f3_client_with_an_injected_transport_does_not_trust_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hostile_environment(monkeypatch, tmp_path)
    rig = Rig(tmp_path, [_ok()])
    assert rig.client._http.trust_env is False
    assert rig.client._http._mounts == {}
    rig.client.send_chunk(small_plan().chunks[0])
    assert len(rig.transport.requests) == 1


def test_audit_f3_default_transport_still_verifies_tls_against_the_bundled_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hostile_environment(monkeypatch, tmp_path)
    client, log = _client(tmp_path, None)
    try:
        context = client._http._transport._pool._ssl_context
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
    finally:
        client.close()
        log.close()


def test_audit_f3_source_passes_trust_env_false_to_the_http_client() -> None:
    tree = ast.parse((SRC / "sarvam" / "client.py").read_text("utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Client"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    ]
    assert len(calls) == 1, "exactly one httpx.Client is built"
    keywords = {k.arg: k.value for k in calls[0].keywords}
    assert "trust_env" in keywords
    value = keywords["trust_env"]
    assert isinstance(value, ast.Constant)
    assert value.value is False
    assert isinstance(keywords["follow_redirects"], ast.Constant)
    assert keywords["follow_redirects"].value is False


# --------------------------------------------------------------------------------------- F10


def test_audit_f10_every_attempt_asks_for_an_uncompressed_reply(tmp_path: Path) -> None:
    failing = json_response(b'{"error": {"code": "x"}}', status=503)
    rig = Rig(tmp_path, [failing, failing, _ok()])
    rig.client.send_chunk(small_plan().chunks[0])
    requests = rig.transport.requests
    assert len(requests) == 3, "two retries and the answer"
    for request in requests:
        assert request.headers.get_list("accept-encoding") == ["identity"]
        assert request.headers["api-subscription-key"] == CANARY_KEY
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert request.method == "POST"


# ---------------------------------------------------------------------------------------- F7


def test_audit_f7_call_log_directory_is_private_whatever_the_umask(tmp_path: Path) -> None:
    out = tmp_path / "parent" / "out"
    previous = os.umask(0o000)
    try:
        log = calllog_mod().CallLog.open(out)
    finally:
        os.umask(previous)
    log.close()
    assert stat.S_IMODE(out.stat().st_mode) == 0o700, oct(out.stat().st_mode)
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600


def test_audit_f7_an_existing_directory_keeps_its_mode(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o755)
    log = calllog_mod().CallLog.open(out)
    log.close()
    assert stat.S_IMODE(out.stat().st_mode) == 0o755, "the user's directory is not changed"
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600


def test_audit_f7_a_symbolic_link_in_place_of_the_log_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep me", "ascii")
    target.chmod(0o644)
    (out / "sarvam_call_log.jsonl").symlink_to(target)
    with pytest.raises(OSError) as raised:  # noqa: PT011 (the text is the platform's)
        calllog_mod().CallLog.open(out)
    assert raised.value.errno in (errno.ELOOP, errno.EMLINK, errno.EPERM)
    assert target.read_text("ascii") == "keep me", "nothing was written through the link"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644, "the target's mode was not changed"


def test_audit_f7_a_dangling_link_is_refused_and_creates_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "not-yet.txt"
    (out / "sarvam_call_log.jsonl").symlink_to(target)
    with pytest.raises(OSError):  # noqa: PT011 (the text is the platform's)
        calllog_mod().CallLog.open(out)
    assert not target.exists(), "the link was not followed to create its target"


def test_audit_f7_the_constructor_refuses_a_link_too(tmp_path: Path) -> None:
    target = tmp_path / "t.txt"
    target.write_text("x", "ascii")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(OSError):  # noqa: PT011 (the text is the platform's)
        calllog_mod().CallLog(link)
    assert target.read_text("ascii") == "x"


def test_audit_f7_source_opens_the_log_with_no_follow() -> None:
    text = (SRC / "calllog.py").read_text("utf-8")
    assert "O_NOFOLLOW" in text


# ---------------------------------------------------------------------------------------- F9


def _write_env(path: Path, body: bytes, mode: int = 0o600) -> Path:
    path.write_bytes(body)
    path.chmod(mode)
    return path


def _load(path: Path) -> Any:
    return secrets_mod().load_api_key(environ={}, env_file=path)


def _reason(path: Path) -> str:
    with pytest.raises(stt().SttKeyMissingError) as raised:
        _load(path)
    reason: str = raised.value.reason
    assert str(path) not in str(raised.value)
    assert path.name not in str(raised.value)
    return reason


def _within(seconds: float, call: Any) -> Any:
    """Run ``call`` on a thread; fail (and release it) if it does not return in time."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = call()
        except BaseException as error:
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail("the call did not return: it blocks on a file that is not a regular file")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def test_audit_f9_the_cap_is_64_kib() -> None:
    assert secrets_mod().ENV_FILE_MAX_BYTES == 65_536


def test_audit_f9_a_file_of_exactly_the_cap_is_read(tmp_path: Path) -> None:
    head = f"SARVAM_API_KEY={CANARY_KEY}\n".encode("ascii")
    body = head + b"#" * (65_536 - len(head) - 1) + b"\n"
    assert len(body) == 65_536
    assert _load(_write_env(tmp_path / "k.env", body)).reveal() == CANARY_KEY


def test_audit_f9_a_file_one_byte_over_the_cap_is_refused_even_if_the_key_comes_first(
    tmp_path: Path,
) -> None:
    head = f"SARVAM_API_KEY={CANARY_KEY}\n".encode("ascii")
    body = head + b"#" * (65_536 - len(head)) + b"\n"
    assert len(body) == 65_537
    assert _reason(_write_env(tmp_path / "k.env", body)) == "env_file_too_large"


def test_audit_f9_a_huge_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "huge.env"
    with path.open("wb") as handle:
        handle.write(f"SARVAM_API_KEY={CANARY_KEY}\n".encode("ascii"))
        handle.truncate(256 * 1024 * 1024)  # a sparse file
    path.chmod(0o600)
    assert _reason(path) == "env_file_too_large"


def test_audit_f9_a_directory_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "dir.env"
    folder.mkdir()
    assert _reason(folder) == "env_file_not_regular"


def test_audit_f9_a_device_is_refused() -> None:
    assert _reason(Path(os.devnull)) == "env_file_not_regular"


def test_audit_f9_a_fifo_is_refused_at_once_and_never_blocks(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.env"
    os.mkfifo(fifo, 0o600)
    try:
        reason = _within(10.0, lambda: _reason(fifo))
    finally:
        # release a reader that blocked on open(): a writer end makes the open return
        try:
            descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            pass
        else:
            os.close(descriptor)
    assert reason == "env_file_not_regular"


def test_audit_f9_a_link_to_a_regular_file_is_read(tmp_path: Path) -> None:
    real = _write_env(tmp_path / "real.env", f"SARVAM_API_KEY={CANARY_KEY}\n".encode("ascii"))
    link = tmp_path / "link.env"
    link.symlink_to(real)
    assert _load(link).reveal() == CANARY_KEY


def test_audit_f9_a_missing_file_keeps_its_reason(tmp_path: Path) -> None:
    assert _reason(tmp_path / "nothing.env") == "env_file_unreadable"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o664, 0o601, 0o770])
def test_audit_f9_a_file_other_users_can_read_earns_a_warning_that_holds_no_secret(
    tmp_path: Path, mode: int
) -> None:
    path = _write_env(tmp_path / "secret-name.env", f"SARVAM_API_KEY={CANARY_KEY}\n".encode(), mode)
    with pytest.warns(secrets_mod().EnvFileModeWarning) as caught:
        key = _load(path)
    assert key.reveal() == CANARY_KEY, "a warning is not a refusal"
    warned = [w for w in caught if issubclass(w.category, secrets_mod().EnvFileModeWarning)]
    assert len(warned) == 1
    message = str(warned[0].message)
    assert message == (
        f"the env file can be read by other users (mode {oct(stat.S_IMODE(mode))}); "
        "run chmod 600 on it"
    )
    assert issubclass(secrets_mod().EnvFileModeWarning, UserWarning)
    for secret in (CANARY_KEY, "secret-name", str(tmp_path), "SARVAM_API_KEY"):
        assert secret not in message


@pytest.mark.parametrize("mode", [0o600, 0o400, 0o500])
def test_audit_f9_an_owner_only_file_earns_no_warning(tmp_path: Path, mode: int) -> None:
    path = _write_env(tmp_path / "k.env", f"SARVAM_API_KEY={CANARY_KEY}\n".encode(), mode)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _load(path)
    assert [w for w in caught if issubclass(w.category, secrets_mod().EnvFileModeWarning)] == []


def test_audit_f9_the_environment_route_never_warns(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        key = secrets_mod().load_api_key(environ={"SARVAM_API_KEY": CANARY_KEY})
    assert key.reveal() == CANARY_KEY
    assert caught == []


def test_audit_f9_a_warned_file_with_no_key_is_still_a_missing_key(tmp_path: Path) -> None:
    path = _write_env(tmp_path / "k.env", b"OTHER=1\n", 0o644)
    with (
        pytest.warns(secrets_mod().EnvFileModeWarning),
        pytest.raises(stt().SttKeyMissingError) as raised,
    ):
        _load(path)
    assert raised.value.reason == "key_missing"
